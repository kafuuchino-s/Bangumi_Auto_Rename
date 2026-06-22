"""字幕 Case Agent 本地入口单测（Phase 2）。

覆盖 ``run_subtitle_case_agent_mapping`` 的四态：
- accepted（含 unmatched 子项）
- fail_closed（coverage / 解析失败 / duplicate / needs_more_evidence）
- need_confirm（AI 空映射 / 无目标视频）
- invalid（AI 调用异常）

对齐 tests/test_subtitle_case_agent_verifier.py 风格：直接构造事实 + mock AI，
断言 status / compiled_plan 形状。AI mock 返回同 ``SubtitleMappingResult`` 形状
的 SimpleNamespace（mappings / unmatched_files / confidence / reason）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.subtitle.case_agent.local_subtitle_entry import (
    build_subtitle_file_cards,
    run_subtitle_case_agent_mapping,
)


def _ss(**kwargs):
    """单轮后端快捷入口：强制 backend=single_shot，绕过 Pi sidecar（单元测试用）。"""
    return run_subtitle_case_agent_mapping(backend="single_shot", **kwargs)


def _pi(**kwargs):
    """Pi 后端快捷入口：强制 backend=pi（需 runtime_invoker/fake env，集成测试用）。"""
    return run_subtitle_case_agent_mapping(backend="pi", **kwargs)


def test_subtitle_case_agent_primary_enabled_default_is_true():
    """对齐 rename：Case Agent 主路径默认启用。"""
    from src.config.config_manager import CONFIG_DEFAULT

    assert CONFIG_DEFAULT["subtitle_case_agent_primary_enabled"] is True


# ---------------------------------------------------------------------------
# source_video：local 原始文件名作为 AI 证据
# ---------------------------------------------------------------------------

def test_target_card_carries_source_video_from_processed_task():
    """record 的 key（local 源文件名）经 ProcessedTask.source_videos ->
    SubtitleTargetVideoCard.source_video -> readable_target_cards 暴露给 AI。"""
    from src.subtitle.case_agent.evidence_broker import build_target_video_cards
    from src.subtitle.case_agent.workspace import build_subtitle_case_workspace

    # 目标名（重命名后）与 local 原始名不同——典型"字幕包与 local 命名一致"场景
    tasks = [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": ["Foo - S01E01 - Pilot.mkv"],
            "target_dir": "/lib/Foo (2020)/Season 01",
            "video_targets": {},
            "source_videos": {"Foo - S01E01 - Pilot.mkv": "[SubGroup] Foo 01.mkv"},
        }
    ]
    cards = build_target_video_cards(tasks)
    assert len(cards) == 1
    assert cards[0].video == "Foo - S01E01 - Pilot.mkv"
    assert cards[0].source_video == "[SubGroup] Foo 01.mkv"

    ws = build_subtitle_case_workspace(
        archive_name="foo.zip",
        subtitle_files=[],
        target_videos=cards,
    )
    readable = ws.readable_target_cards()
    assert readable[0]["source_video"] == "[SubGroup] Foo 01.mkv"
    assert readable[0]["video"] == "Foo - S01E01 - Pilot.mkv"


def test_target_card_source_video_empty_when_absent():
    """旧 record 无 source / 直传字幕场景：source_video 为空，不报错。"""
    from src.subtitle.case_agent.evidence_broker import build_target_video_cards

    tasks = [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": ["Foo - S01E01 - Pilot.mkv"],
            "target_dir": "/lib/Foo (2020)/Season 01",
            "video_targets": {},
            # 无 source_videos
        }
    ]
    cards = build_target_video_cards(tasks)
    assert cards[0].source_video == ""


def test_target_card_carries_arc_name_from_video_arc_names():
    """ProcessedTask.video_arc_names -> SubtitleTargetVideoCard.arc_name/arc_name_cn
    -> readable_target_cards 暴露给 AI。多季同 episode 配对的关键证据（S02E01
    無限列車編 vs S03E01 遊郭編，都从 E01 开始，靠 arc 名区分）。"""
    from src.subtitle.case_agent.evidence_broker import build_target_video_cards
    from src.subtitle.case_agent.workspace import build_subtitle_case_workspace

    tasks = [
        {
            "uuid": "t-s2",
            "title": "Demon Slayer",
            "season": 2,
            "is_movie": False,
            "videos": ["Demon Slayer - S02E01.mkv"],
            "target_dir": "/lib/Demon Slayer (2019)/Season 02",
            "video_targets": {},
            "source_videos": {},
            "video_arc_names": {
                "Demon Slayer - S02E01.mkv": ("鬼滅の刃 無限列車編", "鬼灭之刃 无限列车篇"),
            },
        },
        {
            "uuid": "t-s3",
            "title": "Demon Slayer",
            "season": 3,
            "is_movie": False,
            "videos": ["Demon Slayer - S03E01.mkv"],
            "target_dir": "/lib/Demon Slayer (2019)/Season 03",
            "video_targets": {},
            "source_videos": {},
            "video_arc_names": {
                "Demon Slayer - S03E01.mkv": ("鬼滅の刃 遊郭編", "鬼灭之刃 游郭篇"),
            },
        },
    ]
    cards = build_target_video_cards(tasks)
    by_video = {c.video: c for c in cards}
    # S02E01 的 arc 是無限列車編，S03E01 是遊郭編——区分明确
    assert by_video["Demon Slayer - S02E01.mkv"].arc_name == "鬼滅の刃 無限列車編"
    assert by_video["Demon Slayer - S02E01.mkv"].arc_name_cn == "鬼灭之刃 无限列车篇"
    assert by_video["Demon Slayer - S03E01.mkv"].arc_name == "鬼滅の刃 遊郭編"
    assert by_video["Demon Slayer - S03E01.mkv"].arc_name_cn == "鬼灭之刃 游郭篇"

    ws = build_subtitle_case_workspace(
        archive_name="mugen.zip", subtitle_files=[], target_videos=cards,
    )
    readable = {r["video"]: r for r in ws.readable_target_cards()}
    assert readable["Demon Slayer - S02E01.mkv"]["arc_name"] == "鬼滅の刃 無限列車編"
    assert readable["Demon Slayer - S03E01.mkv"]["arc_name_cn"] == "鬼灭之刃 游郭篇"


def test_target_card_arc_name_empty_when_absent():
    """旧 task 无 video_arc_names：arc_name/arc_name_cn 为空，不报错。"""
    from src.subtitle.case_agent.evidence_broker import build_target_video_cards

    tasks = [
        {
            "uuid": "t1", "title": "Foo", "season": 1, "is_movie": False,
            "videos": ["Foo - S01E01.mkv"], "target_dir": "/lib/Foo/Season 01",
            "video_targets": {}, "source_videos": {},
        }
    ]
    cards = build_target_video_cards(tasks)
    assert cards[0].arc_name == ""
    assert cards[0].arc_name_cn == ""


def test_build_processed_task_captures_video_arc_names_from_task_data(monkeypatch, tmp_path):
    """_build_processed_task_from_file 从 task_data.bgm_video_subject_map +
    bgm_subjects 抽 video_arc_names，供 target card 填 arc_name。"""
    from src.subtitle.processor import SubtitleProcessor
    from src.utils.path import TASK_PATH, RECORD_PATH
    from src.utils.utils import write_task

    task_uuid = "arc-task-1"
    task_data = {
        "uuid": task_uuid, "type": "rename", "name": "Demon Slayer",
        "is_movie": False, "season_id": 2,
        "target_root": str(tmp_path / "Demon Slayer (2019)"),
        "bgm_video_subject_map": {"Demon Slayer - S02E01.mkv": 350764, "Demon Slayer - S02E02.mkv": 350764},
        "bgm_subjects": [
            {"id": 350764, "name": "鬼滅の刃 無限列車編", "name_cn": "鬼灭之刃 无限列车篇", "media_kind": "tv"},
            {"id": 328195, "name": "鬼滅の刃 遊郭編", "name_cn": "鬼灭之刃 游郭篇", "media_kind": "tv"},
        ],
    }
    write_task(task_uuid, task_data)
    record = {
        "[VCB] Kimetsu 27.mkv": str(tmp_path / "Demon Slayer (2019)" / "Season 02" / "Demon Slayer - S02E01.mkv"),
        "[VCB] Kimetsu 28.mkv": str(tmp_path / "Demon Slayer (2019)" / "Season 02" / "Demon Slayer - S02E02.mkv"),
    }
    (RECORD_PATH / f"{task_uuid}.json").write_text(
        __import__("json").dumps(record, ensure_ascii=False), encoding="utf-8"
    )

    proc = SubtitleProcessor()
    task = proc._load_single_processed_task(task_uuid)
    assert task is not None
    arc_names = task.get("video_arc_names") or {}
    assert arc_names.get("Demon Slayer - S02E01.mkv") == ("鬼滅の刃 無限列車編", "鬼灭之刃 无限列车篇")
    assert arc_names.get("Demon Slayer - S02E02.mkv") == ("鬼滅の刃 無限列車編", "鬼灭之刃 无限列车篇")

    # cleanup
    for p in (TASK_PATH / f"{task_uuid}.json", RECORD_PATH / f"{task_uuid}.json"):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def test_build_processed_task_captures_source_videos_from_record(monkeypatch, tmp_path):
    """_build_processed_task_from_file 从 record 的 key（local 源路径）抽 source_videos。"""
    import json

    from src.subtitle.processor import SubtitleProcessor
    from src.utils.path import TASK_PATH, RECORD_PATH

    task_dir = tmp_path / "task"
    record_dir = tmp_path / "record"
    task_dir.mkdir(parents=True, exist_ok=True)
    record_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.subtitle.processor.TASK_PATH", task_dir)
    monkeypatch.setattr("src.subtitle.processor.RECORD_PATH", record_dir)

    task_uuid = "t-uuid"
    (task_dir / f"{task_uuid}.json").write_text(
        json.dumps(
            {
                "type": "tv",
                "uuid": task_uuid,
                "name": "Foo",
                "is_movie": False,
                "season_id": 1,
                "target_root": str(tmp_path / "lib"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # record key = local 源路径, value = 目标路径
    (record_dir / f"{task_uuid}.json").write_text(
        json.dumps(
            {
                "/downloads/[SubGroup] Foo 01.mkv": "/lib/Foo (2020)/Season 01/Foo - S01E01 - Pilot.mkv",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    processor = SubtitleProcessor()
    task = processor._build_processed_task_from_file(task_dir / f"{task_uuid}.json")
    assert task is not None
    assert task["source_videos"] == {"Foo - S01E01 - Pilot.mkv": "[SubGroup] Foo 01.mkv"}
    assert task["videos"] == ["Foo - S01E01 - Pilot.mkv"]


# ---------------------------------------------------------------------------
# processor.process() 端到端（Case Agent 主路径）
# ---------------------------------------------------------------------------

def _processor_with_case_agent(monkeypatch, tmp_path):
    """构造 SubtitleProcessor，强制走 Case Agent 主路径，TASK_PATH 隔离。"""
    from src.subtitle.processor import SubtitleProcessor

    task_dir = tmp_path / "task_data"
    task_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.subtitle.processor.TASK_PATH", task_dir)

    config = {
        "subtitle_case_agent_primary_enabled": True,
        # 单测走 Phase 2 单轮后端，避免起 Pi sidecar（Phase 3 有独立 pi_runner 单测）
        "subtitle_case_agent_backend": "single_shot",
    }
    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: config.get(key),
    )
    processor = SubtitleProcessor()
    return processor


def test_process_case_agent_accepted_lands_and_records_unmatched_todo(
    monkeypatch, tmp_path
):
    """accepted + unmatched：落盘已匹配部分，unmatched 写进任务 JSON 待人工，整体 success。"""
    from src.subtitle.extractor import ExtractedSubtitle

    processor = _processor_with_case_agent(monkeypatch, tmp_path)

    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    video_name = "Foo - S01E01 - A.mkv"
    video_path = target_dir / video_name
    video_path.write_text("v", encoding="utf-8")
    tasks = [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": [video_name],
            "target_dir": str(target_dir),
            "video_targets": {video_name: str(video_path)},
        }
    ]
    monkeypatch.setattr(
        processor, "_load_processed_tasks", lambda max_tasks=10: tasks
    )

    s1 = tmp_path / "01.ass"
    s1.write_text("a", encoding="utf-8")
    s2 = tmp_path / "02.ass"
    s2.write_text("b", encoding="utf-8")
    subs = [
        ExtractedSubtitle(temp_path=s1, archive_path="S1/01.ass", filename="01.ass"),
        ExtractedSubtitle(temp_path=s2, archive_path="S1/02.ass", filename="02.ass"),
    ]
    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: subs)
    monkeypatch.setattr(
        processor.extractor, "get_archive_structure", lambda sf: {"S1": ["01.ass", "02.ass"]}
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)

    processor.ai_client.analyze_subtitle_mapping = lambda **kwargs: SimpleNamespace(
        mappings=[_mapping("S1/01.ass", "t1", video_name, "chs")],
        unmatched_files=["S1/02.ass"],
        confidence="High",
        reason="ep02 no video",
    )

    trans_call = {}

    class FakeTrans:
        def __init__(self, R, uuid, force_mode=None, force_overwrite=None):
            trans_call["R"] = dict(R)

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "success"
    assert result["matched_count"] == 1
    assert result["total_subtitles"] == 2
    assert result["pipeline_mode"] == "subtitle_case_agent_primary"
    # unmatched item 含 reason_kind/reason（结构化分类）；single_shot 旧 AI 路径
    # 无 reason_kind 信息，默认 'unknown' → 归 unmatched（待人工，不过滤）。
    assert result["unmatched"] == [
        {"ref": "SF2", "archive_path": "S1/02.ass",
         "reason_kind": "unknown", "reason": "ai_unmatched"}
    ]
    # no_target_videos 应为空（旧路径无 reason_kind=no_target_video）
    assert result.get("no_target_videos") == []
    assert s1 in trans_call["R"]
    # 任务 JSON 写盘
    import json
    task_files = list((tmp_path / "task_data").glob("*.json"))
    assert len(task_files) == 1
    data = json.loads(task_files[0].read_text(encoding="utf-8"))
    assert data["type"] == "subtitle"
    assert data["status"] == "success"
    assert data["unmatched"] == [
        {"ref": "SF2", "archive_path": "S1/02.ass",
         "reason_kind": "unknown", "reason": "ai_unmatched"}
    ]
    assert data.get("no_target_videos") == []


def test_process_case_agent_fail_closed_maps_to_need_confirm(monkeypatch, tmp_path):
    """fail_closed：合同不通过 -> 对外 need_confirm，带 case_agent_status=fail_closed，不落盘。"""
    from src.subtitle.extractor import ExtractedSubtitle

    processor = _processor_with_case_agent(monkeypatch, tmp_path)

    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    video_name = "Foo - S01E01 - A.mkv"
    (target_dir / video_name).write_text("v", encoding="utf-8")
    tasks = [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": [video_name],
            "target_dir": str(target_dir),
            "video_targets": {video_name: str(target_dir / video_name)},
        }
    ]
    monkeypatch.setattr(
        processor, "_load_processed_tasks", lambda max_tasks=10: tasks
    )

    s1 = tmp_path / "01.ass"
    s1.write_text("a", encoding="utf-8")
    s2 = tmp_path / "02.ass"
    s2.write_text("b", encoding="utf-8")
    subs = [
        ExtractedSubtitle(temp_path=s1, archive_path="S1/01.ass", filename="01.ass"),
        ExtractedSubtitle(temp_path=s2, archive_path="S1/02.ass", filename="02.ass"),
    ]
    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: subs)
    monkeypatch.setattr(
        processor.extractor, "get_archive_structure", lambda sf: {"S1": ["01.ass", "02.ass"]}
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)

    # AI 只映射 SF1，漏掉 SF2 且不 unmatched -> coverage error -> fail_closed
    processor.ai_client.analyze_subtitle_mapping = lambda **kwargs: SimpleNamespace(
        mappings=[_mapping("S1/01.ass", "t1", video_name, "chs")],
        unmatched_files=[],
        confidence="Medium",
        reason="",
    )

    trans_called = {"value": False}

    class FakeTrans:
        def __init__(self, *a, **k):
            trans_called["value"] = True

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "need_confirm"
    assert result["case_agent_status"] == "fail_closed"
    assert result["pipeline_mode"] == "subtitle_case_agent_primary"
    assert trans_called["value"] is False  # 不落盘部分匹配


def test_process_case_agent_need_confirm_when_ai_empty(monkeypatch, tmp_path):
    """need_confirm：AI 空映射 -> need_confirm，available_tasks 暴露供人工选择。"""
    from src.subtitle.extractor import ExtractedSubtitle

    processor = _processor_with_case_agent(monkeypatch, tmp_path)

    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": ["Foo - S01E01 - A.mkv"],
            "target_dir": str(target_dir),
            "video_targets": {},
        }
    ]
    monkeypatch.setattr(
        processor, "_load_processed_tasks", lambda max_tasks=10: tasks
    )

    s1 = tmp_path / "01.ass"
    s1.write_text("a", encoding="utf-8")
    subs = [ExtractedSubtitle(temp_path=s1, archive_path="S1/01.ass", filename="01.ass")]
    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: subs)
    monkeypatch.setattr(
        processor.extractor, "get_archive_structure", lambda sf: {"S1": ["01.ass"]}
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)

    processor.ai_client.analyze_subtitle_mapping = lambda **kwargs: SimpleNamespace(
        mappings=[], unmatched_files=[], confidence="Low", reason=""
    )

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "need_confirm"
    assert result["available_tasks"] == [{"uuid": "t1", "title": "Foo", "season": 1}]
    assert "case_agent_status" not in result  # need_confirm 不带 fail_closed 标记


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _subs(tmp_path) -> list[Any]:
    """构造两个 ExtractedSubtitle-shaped 事实（archive_path 精确）。"""
    s1 = tmp_path / "01.ass"
    s1.write_text("a", encoding="utf-8")
    s2 = tmp_path / "02.ass"
    s2.write_text("b", encoding="utf-8")
    return [
        SimpleNamespace(temp_path=s1, archive_path="S1/01.ass", filename="01.ass"),
        SimpleNamespace(temp_path=s2, archive_path="S1/02.ass", filename="02.ass"),
    ]


def _tasks(tmp_path) -> list[dict[str, object]]:
    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    v1 = "Foo - S01E01 - A.mkv"
    v2 = "Foo - S01E02 - B.mkv"
    (target_dir / v1).write_text("v", encoding="utf-8")
    (target_dir / v2).write_text("v", encoding="utf-8")
    return [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": [v1, v2],
            "target_dir": str(target_dir),
            "video_targets": {
                v1: str(target_dir / v1),
                v2: str(target_dir / v2),
            },
        }
    ]


def _lang_resolver(lang: str) -> tuple[str, bool]:
    table = {
        "chs": ("zh-CN", True),
        "cht": ("zh-TW", False),
        "jpn": ("ja", False),
    }
    return table.get((lang or "").lower().strip(), ("zh-CN", True))


class _FakeAIClient:
    """模拟 AIClient：按构造时给定的返回值/异常响应 analyze_subtitle_mapping。"""

    def __init__(self, *, result: Any = None, raise_exc: Exception | None = None):
        self._result = result
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    def analyze_subtitle_mapping(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return self._result


def _mapping(subtitle_path: str, task_uuid: str, video: str, language: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        subtitle_path=subtitle_path,
        task_uuid=task_uuid,
        video=video,
        language=language,
    )


# ---------------------------------------------------------------------------
# build_subtitle_file_cards
# ---------------------------------------------------------------------------

def test_build_subtitle_file_cards_extracts_language_hint(tmp_path):
    subs = [
        SimpleNamespace(temp_path=tmp_path / "a.ass", archive_path="a.ass", filename="01.chs.ass"),
        SimpleNamespace(temp_path=tmp_path / "b.ass", archive_path="b.ass", filename="02.jpn.ass"),
    ]
    cards = build_subtitle_file_cards(subs)
    assert cards[0].archive_path == "a.ass"
    assert cards[0].language_hint == "chs"
    assert cards[1].language_hint == "jpn"


# ---------------------------------------------------------------------------
# accepted
# ---------------------------------------------------------------------------

def test_entry_accepted_full_mapping(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    v2 = tasks[0]["videos"][1]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                _mapping("S1/01.ass", "t1", v1, "chs"),
                _mapping("S1/02.ass", "t1", v2, "cht"),
            ],
            unmatched_files=[],
            confidence="High",
            reason="ok",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
        archive_name="foo.zip",
    )
    assert res["status"] == "accepted"
    assert res["ok"] is True
    plan = res["compiled_plan"]
    assert plan is not None
    assert len(plan.mappings) == 2
    assert plan.mappings[0].emby_lang == "zh-CN"
    assert plan.mappings[0].is_simplified is True
    assert plan.mappings[1].emby_lang == "zh-TW"
    assert plan.mappings[1].is_simplified is False
    assert plan.unmatched_refs == []


def test_entry_accepted_with_unmatched(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[_mapping("S1/01.ass", "t1", v1, "chs")],
            unmatched_files=["S1/02.ass"],
            confidence="Medium",
            reason="ep02 no matching video",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "accepted"
    plan = res["compiled_plan"]
    assert plan is not None
    assert len(plan.mappings) == 1
    assert plan.unmatched_refs == ["SF2"]


# ---------------------------------------------------------------------------
# fail_closed
# ---------------------------------------------------------------------------

def test_entry_fail_closed_coverage_missing(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[_mapping("S1/01.ass", "t1", v1, "chs")],  # SF2 漏掉
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    assert res["compiled_plan"] is None
    assert res["ok"] is True  # 合格业务结果
    issues = res["snapshot"]["verifier"]["issues"]
    assert any(i["issue_code"] == "coverage_error" for i in issues)


def test_entry_fail_closed_unresolvable_subtitle_path(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                # subtitle_path 在固定层找不到 -> needs_more_evidence
                _mapping("NOT_EXIST/01.ass", "t1", v1, "chs"),
                _mapping("S1/02.ass", "t1", tasks[0]["videos"][1], "chs"),
            ],
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    issues = res["snapshot"]["verifier"]["issues"]
    # 解析失败落 needs_more_evidence -> not_ready
    assert any(i["issue_code"] == "not_ready" for i in issues)


def test_entry_fail_closed_unresolvable_target(tmp_path):
    tasks = _tasks(tmp_path)
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                # video 不在任务视频清单 -> target 解析失败 -> needs_more_evidence
                _mapping("S1/01.ass", "t1", "WRONG.mkv", "chs"),
                _mapping("S1/02.ass", "t1", tasks[0]["videos"][1], "chs"),
            ],
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    issues = res["snapshot"]["verifier"]["issues"]
    assert any(i["issue_code"] == "not_ready" for i in issues)


def test_entry_fail_closed_duplicate_target_language(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                _mapping("S1/01.ass", "t1", v1, "chs"),
                _mapping("S1/02.ass", "t1", v1, "chs"),  # 同 target 同语言冲突
            ],
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    issues = res["snapshot"]["verifier"]["issues"]
    assert any(i["issue_code"] == "duplicate_target_language" for i in issues)


def test_entry_fail_closed_missing_language(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                _mapping("S1/01.ass", "t1", v1, None),  # 语言缺 -> missing_language
                _mapping("S1/02.ass", "t1", tasks[0]["videos"][1], "chs"),
            ],
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    issues = res["snapshot"]["verifier"]["issues"]
    assert any(i["issue_code"] == "missing_language" for i in issues)


# ---------------------------------------------------------------------------
# need_confirm
# ---------------------------------------------------------------------------

def test_entry_need_confirm_ai_empty_mappings(tmp_path):
    tasks = _tasks(tmp_path)
    ai = _FakeAIClient(
        result=SimpleNamespace(mappings=[], unmatched_files=[], confidence="Low", reason="")
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "need_confirm"
    assert res["compiled_plan"] is None


def test_entry_need_confirm_no_target_videos(tmp_path):
    ai = _FakeAIClient(result=None)
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=[],  # 无已处理任务 -> 无目标视频
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "need_confirm"
    assert ai.calls == []  # 无目标视频时不应调 AI


def test_entry_need_confirm_ai_returns_none(tmp_path):
    tasks = _tasks(tmp_path)
    ai = _FakeAIClient(result=None)
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "need_confirm"


# ---------------------------------------------------------------------------
# invalid
# ---------------------------------------------------------------------------

def test_entry_invalid_ai_call_raises(tmp_path):
    tasks = _tasks(tmp_path)
    ai = _FakeAIClient(raise_exc=RuntimeError("ai boom"))
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "invalid"
    assert res["ok"] is False
    assert "ai_call_error" in res["summary"]


# ---------------------------------------------------------------------------
# 同 target 不同语言：accepted（双语合法）
# ---------------------------------------------------------------------------

def test_entry_accepted_same_target_different_language(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                _mapping("S1/01.ass", "t1", v1, "chs"),
                _mapping("S1/02.ass", "t1", v1, "cht"),  # 同视频挂简繁双语
            ],
            unmatched_files=[],
            confidence="High",
            reason="bilingual",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "accepted"
    plan = res["compiled_plan"]
    assert plan is not None
    assert len(plan.mappings) == 2
    langs = {m.emby_lang for m in plan.mappings}
    assert langs == {"zh-CN", "zh-TW"}


# ---------------------------------------------------------------------------
# unmatched 按 reason_kind 分类（processor._build_unmatched_details）
# ---------------------------------------------------------------------------

def test_build_unmatched_details_splits_no_target_from_unmatched():
    """processor 按 unmatched_reason_kind 分类：
    no_target_video -> no_target_details（过滤出 unmatched）；
    duplicate_language / no_confident_match / unknown -> unmatched（待人工）。
    """
    from src.subtitle.case_agent.models import (
        CompiledSubtitlePlan,
        CompiledUnmatchedEntry,
    )
    from src.subtitle.extractor import ExtractedSubtitle
    from src.subtitle.processor import SubtitleProcessor

    # 绕过 __init__（避免起 AIClient/FFsubsyncRunner）
    proc = SubtitleProcessor.__new__(SubtitleProcessor)
    subs = [
        ExtractedSubtitle(temp_path=tmp_path_dummy(1), archive_path="PV.ass", filename="PV.ass"),
        ExtractedSubtitle(temp_path=tmp_path_dummy(2), archive_path="dup.ass", filename="dup.ass"),
        ExtractedSubtitle(temp_path=tmp_path_dummy(3), archive_path="unsure.ass", filename="unsure.ass"),
        ExtractedSubtitle(temp_path=tmp_path_dummy(4), archive_path="unk.ass", filename="unk.ass"),
    ]
    plan = CompiledSubtitlePlan(unmatched=[
        CompiledUnmatchedEntry(ref="SF1", reason_kind="no_target_video", reason="PV no target"),
        CompiledUnmatchedEntry(ref="SF2", reason_kind="duplicate_language", reason="dup tc"),
        CompiledUnmatchedEntry(ref="SF3", reason_kind="no_confident_match", reason="unsure ep"),
        CompiledUnmatchedEntry(ref="SF4", reason_kind="unknown", reason="ai_unmatched"),
    ])
    unmatched_details, no_target_details = proc._build_unmatched_details(
        compiled_plan=plan,
        subtitle_files=subs,
        sub_by_archive={},
    )
    # no_target 只含 SF1
    assert [d["ref"] for d in no_target_details] == ["SF1"]
    assert no_target_details[0]["reason_kind"] == "no_target_video"
    # unmatched 含其余 3 个（待人工）
    assert [d["ref"] for d in unmatched_details] == ["SF2", "SF3", "SF4"]
    assert {d["reason_kind"] for d in unmatched_details} == {
        "duplicate_language", "no_confident_match", "unknown"
    }
    # 每条都带 archive_path + reason
    for d in unmatched_details + no_target_details:
        assert d["archive_path"]
        assert d["reason"]


def tmp_path_dummy(i: int):
    """占位 temp_path（_build_unmatched_details 不读它，只用 archive_path 顺序对齐）。"""
    from pathlib import Path
    return Path(f"/tmp/dummy{i}.ass")
