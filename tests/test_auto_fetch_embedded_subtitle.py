"""auto_fetch 内嵌字幕识别测试。

验证扫描阶段对"内嵌已含优先语言字幕轨"的视频跳过抓取：
- 内嵌 zh-CN（chi）命中 preferred=zh-CN → 跳过
- 内嵌 ja（jpn）不命中 preferred=zh-CN → 仍进 missing
- 开关关 → 内嵌 zh-CN 也不跳过
- ffprobe 失败/无轨（[]）→ 回退外挂判定
- 外挂命中优先于内嵌探轨（有外挂时不探轨）

不依赖真 ffprobe：monkeypatch ``_probe_embedded_subtitle_languages`` 注入返回值。
"""

from pathlib import Path

import pytest

from src.subtitle.auto_fetch import SubtitleAutoFetcher


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_fetcher(monkeypatch, tmp_path, *, record_targets, target_root=None,
                  is_movie=False):
    """造 fetcher + task/record，落盘空 mkv 候选（非真视频，ffprobe 必失败，
    测试靠 monkeypatch 探轨函数注入语言返回值）。"""
    fetcher = SubtitleAutoFetcher()
    task_uuid = "task-embedded"
    task = {
        "uuid": task_uuid,
        "name": "Test Series",
        "tmdb_name": "Test Series",
        "is_movie": is_movie,
    }
    if target_root is not None:
        task["target_root"] = str(target_root)
    monkeypatch.setattr("src.subtitle.auto_fetch.get_task", lambda uuid: task)
    monkeypatch.setattr("src.subtitle.auto_fetch.get_record",
                        lambda uuid: record_targets)
    # 落盘候选视频（空文件，_is_candidate_video 只看 exists + suffix）
    for target in record_targets.values():
        if isinstance(target, str):
            p = Path(target)
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("video", encoding="utf-8")
    return fetcher, task_uuid


def _patch_probe(monkeypatch, lang_map):
    """按 video 文件名 -> 语言列表注入探轨返回值。未在 map 中的文件返 []。"""
    def fake_probe(video_path: Path) -> list:
        return lang_map.get(video_path.name, [])
    monkeypatch.setattr(
        "src.subtitle.auto_fetch._probe_embedded_subtitle_languages", fake_probe
    )


def _set_skip_flag(monkeypatch, value):
    """覆盖 subtitle_auto_fetch_skip_if_embedded_language 配置。"""
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.cm_get",
        lambda key, default=None: value if key == "subtitle_auto_fetch_skip_if_embedded_language" else (default if key != "subtitle_auto_fetch_preferred_language" else "zh-CN"),
    )


# ---------------------------------------------------------------------------
# task scope（串行 _has_usable_subtitle）
# ---------------------------------------------------------------------------

def test_embedded_zhcn_skips_video(monkeypatch, tmp_path):
    """内嵌 chi 轨命中 preferred=zh-CN → 视频不进 missing。"""
    target = tmp_path / "Series" / "Season 1" / "ep1.mkv"
    fetcher, _ = _make_fetcher(
        monkeypatch, tmp_path, record_targets={"a": str(target)}
    )
    _patch_probe(monkeypatch, {"ep1.mkv": ["chi"]})

    scan_scope = {"type": "task", "root": None}
    missing = fetcher._collect_videos_missing_subtitles(scan_scope, {"a": str(target)})

    assert missing == [], f"内嵌 zh-CN 应跳过，实际 missing={missing}"


def test_embedded_ja_does_not_skip(monkeypatch, tmp_path):
    """内嵌 jpn 轨不命中 preferred=zh-CN → 视频仍进 missing。"""
    target = tmp_path / "Series" / "Season 1" / "ep1.mkv"
    fetcher, _ = _make_fetcher(
        monkeypatch, tmp_path, record_targets={"a": str(target)}
    )
    _patch_probe(monkeypatch, {"ep1.mkv": ["jpn"]})

    scan_scope = {"type": "task", "root": None}
    missing = fetcher._collect_videos_missing_subtitles(scan_scope, {"a": str(target)})

    assert [p.name for p in missing] == ["ep1.mkv"]


def test_skip_flag_off_keeps_video(monkeypatch, tmp_path):
    """开关关 → 内嵌 zh-CN 也不跳过，仍进 missing。"""
    target = tmp_path / "Series" / "Season 1" / "ep1.mkv"
    fetcher, _ = _make_fetcher(
        monkeypatch, tmp_path, record_targets={"a": str(target)}
    )
    _patch_probe(monkeypatch, {"ep1.mkv": ["chi"]})
    _set_skip_flag(monkeypatch, False)

    scan_scope = {"type": "task", "root": None}
    missing = fetcher._collect_videos_missing_subtitles(scan_scope, {"a": str(target)})

    assert [p.name for p in missing] == ["ep1.mkv"]


def test_probe_failure_falls_back_to_sidecar(monkeypatch, tmp_path):
    """探轨返 []（ffprobe 失败/无轨）→ 回退外挂判定：
    无外挂 → 进 missing；有外挂 → 跳过。"""
    target_no_sidecar = tmp_path / "Series" / "Season 1" / "ep1.mkv"
    target_with_sidecar = tmp_path / "Series" / "Season 1" / "ep2.mkv"
    fetcher, _ = _make_fetcher(
        monkeypatch, tmp_path,
        record_targets={"a": str(target_no_sidecar), "b": str(target_with_sidecar)},
    )
    # 给 ep2 造一个外挂字幕
    (target_with_sidecar.parent / "ep2.zh-CN.ass").write_text("sub", encoding="utf-8")
    _patch_probe(monkeypatch, {})  # 全部返 []

    scan_scope = {"type": "task", "root": None}
    missing = fetcher._collect_videos_missing_subtitles(
        scan_scope,
        {"a": str(target_no_sidecar), "b": str(target_with_sidecar)},
    )

    assert [p.name for p in missing] == ["ep1.mkv"]


def test_sidecar_short_circuits_probe(monkeypatch, tmp_path):
    """有外挂字幕时不应调用探轨（外挂优先，零成本短路）。"""
    target = tmp_path / "Series" / "Season 1" / "ep1.mkv"
    fetcher, _ = _make_fetcher(
        monkeypatch, tmp_path, record_targets={"a": str(target)}
    )
    (target.parent / "ep1.zh-CN.ass").write_text("sub", encoding="utf-8")

    probe_calls = []

    def spy_probe(video_path: Path) -> list:
        probe_calls.append(video_path.name)
        return ["chi"]

    monkeypatch.setattr(
        "src.subtitle.auto_fetch._probe_embedded_subtitle_languages", spy_probe
    )

    scan_scope = {"type": "task", "root": None}
    missing = fetcher._collect_videos_missing_subtitles(scan_scope, {"a": str(target)})

    assert missing == []
    assert probe_calls == [], "有外挂字幕不应触发探轨"


# ---------------------------------------------------------------------------
# series scope（并发探轨 _filter_by_embedded_language）
# ---------------------------------------------------------------------------

def test_series_scope_embedded_filter(monkeypatch, tmp_path):
    """series scope：2 集内嵌 zh-CN 跳过，1 集内嵌 ja 保留 + 1 集无轨保留。"""
    root = tmp_path / "SeriesRoot"
    season = root / "Season 1"
    season.mkdir(parents=True, exist_ok=True)
    for name in ("ep01.mkv", "ep02.mkv", "ep03.mkv", "ep04.mkv"):
        (season / name).write_text("video", encoding="utf-8")
    fetcher, _ = _make_fetcher(
        monkeypatch, tmp_path,
        record_targets={},
        target_root=root,
    )
    _patch_probe(monkeypatch, {
        "ep01.mkv": ["chi"],
        "ep02.mkv": ["zh-Hans"],
        "ep03.mkv": ["jpn"],
        "ep04.mkv": [],
    })

    scan_scope = {"type": "series", "root": str(root)}
    missing = fetcher._collect_videos_missing_subtitles(scan_scope, {})

    names = {p.name for p in missing}
    assert names == {"ep03.mkv", "ep04.mkv"}, f"实际 missing={names}"


def test_series_scope_order_preserved(monkeypatch, tmp_path):
    """series scope 剔除内嵌命中后保持原目录顺序。"""
    root = tmp_path / "SeriesRoot"
    season = root / "Season 1"
    season.mkdir(parents=True, exist_ok=True)
    for name in ("ep01.mkv", "ep02.mkv", "ep03.mkv", "ep04.mkv"):
        (season / name).write_text("video", encoding="utf-8")
    fetcher, _ = _make_fetcher(
        monkeypatch, tmp_path, record_targets={}, target_root=root,
    )
    _patch_probe(monkeypatch, {
        "ep02.mkv": ["chi"],  # 中间一集命中，跳过
    })

    scan_scope = {"type": "series", "root": str(root)}
    missing = fetcher._collect_videos_missing_subtitles(scan_scope, {})

    assert [p.name for p in missing] == ["ep01.mkv", "ep03.mkv", "ep04.mkv"]


# ---------------------------------------------------------------------------
# 归一表单元测试
# ---------------------------------------------------------------------------

def test_normalize_embedded_language():
    from src.subtitle.auto_fetch import _normalize_embedded_language

    assert _normalize_embedded_language("chi") == "zh-CN"
    assert _normalize_embedded_language("CHI") == "zh-CN"
    assert _normalize_embedded_language("zho") == "zh-CN"
    assert _normalize_embedded_language("zh") == "zh-CN"
    assert _normalize_embedded_language("zh-Hans") == "zh-CN"
    assert _normalize_embedded_language("zh-TW") == "zh-TW"
    assert _normalize_embedded_language("zh-Hant") == "zh-TW"
    assert _normalize_embedded_language("jpn") == "ja"
    assert _normalize_embedded_language("eng") == "en"
    assert _normalize_embedded_language("kor") == "ko"
    assert _normalize_embedded_language("fra") is None  # 未覆盖语言
    assert _normalize_embedded_language(None) is None
    assert _normalize_embedded_language("") is None
