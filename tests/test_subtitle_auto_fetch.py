from pathlib import Path
from types import SimpleNamespace

import pytest

from src.subtitle.auto_fetch import SubtitleAutoFetcher
from src.subtitle.providers import SubtitleCandidate, SubtitleThreadPackage


@pytest.fixture(autouse=True)
def _force_single_shot_backend(monkeypatch):
    """auto_fetch Case Agent 默认后端已切 pi；本套件测 single_shot 行为，
    强制钉死 single_shot 避免真起 node sidecar。"""
    import src.subtitle.auto_fetch as af_mod

    orig = af_mod.cm_get

    def patched(key, default=None):
        if key == "subtitle_auto_fetch_case_agent_backend":
            return "single_shot"
        return orig(key, default)

    monkeypatch.setattr(af_mod, "cm_get", patched)


def make_package(package_id, flags, *, has_direct_download=True, page_number=1):
    return SubtitleThreadPackage(
        package_id=package_id,
        page_number=page_number,
        floor_label=f"{package_id}-floor",
        post_author="author",
        post_time="2023-03-13 06:43:45",
        post_text="package text",
        context_text="package context",
        links=[
            SimpleNamespace(
                url=f"https://example.com/{package_id}.zip",
                kind="attachment",
                label=f"{package_id}.zip",
                filename_hint=f"{package_id}.zip",
                is_direct_download=has_direct_download,
            )
        ],
        has_direct_download=has_direct_download,
        package_flags=flags,
    )


def build_fetcher(monkeypatch, tmp_path):
    fetcher = SubtitleAutoFetcher()
    task_uuid = "task-1"
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task",
        lambda uuid: {
            "uuid": uuid,
            "name": "Seitokai no Ichizon",
            "tmdb_name": "Seitokai no Ichizon",
            "season_id": 1,
            "is_movie": False,
            "target_root": str(tmp_path / "Series"),
        },
    )
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_record",
        lambda uuid: {"a": str(tmp_path / "Series" / "Season 1" / "ep1.mkv")},
    )
    season_dir = tmp_path / "Series" / "Season 1"
    season_dir.mkdir(parents=True, exist_ok=True)
    (season_dir / "ep1.mkv").write_text("video", encoding="utf-8")
    return fetcher, task_uuid


def build_season0_fetcher(monkeypatch, tmp_path):
    fetcher = SubtitleAutoFetcher()
    task_uuid = "task-season0"
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task",
        lambda uuid: {
            "uuid": uuid,
            "name": "鬼灭之刃",
            "tmdb_name": "鬼灭之刃",
            "path": str(
                tmp_path
                / "[BeanSub&FZSD&VCB-Studio] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen [Ma10p_1080p]"
            ),
            "season_id": 0,
            "is_movie": False,
            "target_root": str(tmp_path / "SeriesRoot"),
        },
    )
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_record",
        lambda uuid: {
            str(
                tmp_path
                / "Source"
                / "[BeanSub&FZSD&VCB-Studio] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen [Ma10p_1080p][x265_flac].mkv"
            ): str(
                tmp_path
                / "SeriesRoot"
                / "Season 00"
                / "鬼灭之刃 - S00E14 - 1080p x265 FLAC - BeanSub&FZSD&VCB-Studio.mkv"
            )
        },
    )
    season_dir = tmp_path / "SeriesRoot" / "Season 00"
    season_dir.mkdir(parents=True, exist_ok=True)
    (
        season_dir
        / "鬼灭之刃 - S00E14 - 1080p x265 FLAC - BeanSub&FZSD&VCB-Studio.mkv"
    ).write_text("video", encoding="utf-8")
    return fetcher, task_uuid


def test_process_uses_ai_selected_thread_package(monkeypatch, tmp_path):
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="thread-1",
        detail_url="https://bbs.acgrip.com/thread-1",
        source="acgrip",
    )
    packages = [
        make_package("patch", ["patch"]),
        make_package("batch", ["batch", "simplified"]),
    ]
    candidate.thread_packages = packages
    candidate.pages_scanned = 2

    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_candidate",
        lambda task_data, ranked_candidates: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="thread ok",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "thread ok",
                "warnings": [],
            },
        ),
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda task_data, candidate_data, package_summaries: SimpleNamespace(
            selected_index=1,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="pick batch",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 1,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "pick batch",
                "warnings": [],
            },
        ),
    )

    downloaded = tmp_path / "picked.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    download_calls = {}

    def fake_download(candidate, destination_dir, package=None):
        download_calls["package"] = package
        return SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/picked.zip",
            selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    assert result["selected_package"]["package_id"] == "batch"
    assert download_calls["package"].package_id == "batch"
    assert result["package_ai_result"]["selected_index"] == 1
    assert result["pages_scanned"] == 2


def test_process_falls_back_to_rule_package_when_package_ai_fails(monkeypatch, tmp_path):
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="thread-1",
        detail_url="https://bbs.acgrip.com/thread-1",
        source="acgrip",
    )
    candidate.thread_packages = [
        make_package("font", ["font"]),
        make_package("batch", ["batch", "simplified"]),
    ]

    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_candidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_thread_package", lambda *args, **kwargs: None)

    downloaded = tmp_path / "fallback.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    selected = {}

    def fake_download(candidate, destination_dir, package=None):
        selected["package"] = package
        return SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/fallback.zip",
            selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    assert selected["package"].package_id == "batch"
    assert result["selected_package"]["package_id"] == "batch"


def test_process_skips_when_package_ai_explicitly_rejects(monkeypatch, tmp_path):
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="thread-1",
        detail_url="https://bbs.acgrip.com/thread-1",
        source="acgrip",
    )
    candidate.thread_packages = [
        make_package("special", ["special"]),
        make_package("batch", ["batch", "simplified"]),
    ]
    candidate.pages_scanned = 1

    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_candidate",
        lambda task_data, ranked_candidates: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="thread ok",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "thread ok",
                "warnings": [],
            },
        ),
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda task_data, candidate_data, package_summaries: SimpleNamespace(
            selected_index=0,
            should_use=False,
            confidence="Medium",
            language_assessment="简体中文",
            reason="special-only package",
            warnings=["ova only"],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": False,
                "confidence": "Medium",
                "language_assessment": "简体中文",
                "reason": "special-only package",
                "warnings": ["ova only"],
            },
        ),
    )

    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda *args, **kwargs: pytest.fail("download should not be called"),
    )
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda *args, **kwargs: pytest.fail("processor should not be called"),
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "skipped"
    assert result["reason"] == "package_ai_rejected"
    assert result["selected_package"] is None
    assert result["package_ai_result"]["should_use"] is False
    assert result["package_ai_result"]["reason"] == "special-only package"




def test_process_retries_with_path_title_when_primary_search_has_no_candidates(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task",
        lambda uuid: {
            "uuid": uuid,
            "name": "东京暗鸦",
            "tmdb_name": "东京暗鸦",
            "path": str(tmp_path / "[MMZY-Sub&VCB-Studio] Tokyo Ravens [Hi10p_1080p]"),
            "season_id": 1,
            "is_movie": False,
            "target_root": str(tmp_path / "Series"),
        },
    )
    candidate = SubtitleCandidate(
        title="东京暗鸦 / Tokyo Ravens 字幕",
        detail_url="https://bbs.acgrip.com/thread-1",
        source="acgrip",
    )
    queries = []

    def fake_search(keyword, limit=10):
        queries.append(keyword)
        if keyword == "东京暗鸦":
            return []
        if keyword == "Tokyo Ravens":
            return [candidate]
        return []

    monkeypatch.setattr(fetcher.provider, "search", fake_search)
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_candidate", lambda *args, **kwargs: None)

    downloaded = tmp_path / "fallback-search.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda candidate, destination_dir, package=None: SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/fallback-search.zip",
            selected_package=package,
        ),
    )
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    assert queries == ["东京暗鸦", "Tokyo Ravens"]


def test_process_passes_loaded_candidate_packages_to_ai(monkeypatch, tmp_path):
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    external_only = SubtitleCandidate(
        title="外链候选",
        detail_url="https://bbs.acgrip.com/thread-external",
        source="acgrip",
        external_urls=["https://pan.acgrip.com/"],
    )
    direct_candidate = SubtitleCandidate(
        title="直链候选",
        detail_url="https://bbs.acgrip.com/thread-direct",
        source="acgrip",
    )
    direct_candidate.thread_packages = [
        make_package("batch", ["batch", "simplified"]),
    ]

    monkeypatch.setattr(
        fetcher.provider,
        "search",
        lambda keyword, limit=10: [external_only, direct_candidate],
    )
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)

    ai_candidate_inputs = {}

    def fake_choose_candidate(task_data, ranked_candidates):
        ai_candidate_inputs["ranked_candidates"] = ranked_candidates
        return SimpleNamespace(
            selected_index=1,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="thread with downloadable package",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 1,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "thread with downloadable package",
                "warnings": [],
            },
        )

    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_candidate", fake_choose_candidate)
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda task_data, candidate_data, package_summaries: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="pick loaded batch",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "pick loaded batch",
                "warnings": [],
            },
        ),
    )

    downloaded = tmp_path / "direct.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    download_calls = []

    def fake_download(candidate, destination_dir, package=None):
        download_calls.append(candidate.title)
        return SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/direct.zip",
            selected_package=package,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"


def test_process_retries_next_keyword_when_first_candidate_is_wrong_arc(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_season0_fetcher(monkeypatch, tmp_path)
    wrong_candidate = SubtitleCandidate(
        title="鬼灭之刃 游郭篇",
        detail_url="https://bbs.acgrip.com/thread-wrong",
        source="acgrip",
    )
    correct_candidate = SubtitleCandidate(
        title="剧场版 鬼灭之刃 无限列车篇",
        detail_url="https://bbs.acgrip.com/thread-correct",
        source="acgrip",
    )
    correct_candidate.thread_packages = [
        make_package("mugen", ["batch", "simplified"])
    ]
    queries = []

    def fake_search(keyword, limit=10):
        queries.append(keyword)
        if keyword == "鬼灭之刃":
            return [wrong_candidate]
        if keyword == "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen":
            return [correct_candidate]
        return []

    monkeypatch.setattr(fetcher.provider, "search", fake_search)
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)

    def fake_choose_candidate(task_data, ranked_candidates):
        selected_title = ranked_candidates[0]["title"]
        if "游郭篇" in selected_title:
            return SimpleNamespace(
                selected_index=0,
                should_use=False,
                confidence="High",
                language_assessment="简体中文",
                reason="wrong arc",
                warnings=["yuukaku mismatch"],
                model_dump=lambda: {
                    "selected_index": 0,
                    "should_use": False,
                    "confidence": "High",
                    "language_assessment": "简体中文",
                    "reason": "wrong arc",
                    "warnings": ["yuukaku mismatch"],
                },
            )
        return SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="correct arc",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "correct arc",
                "warnings": [],
            },
        )

    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_candidate", fake_choose_candidate)
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda *args, **kwargs: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="pick correct package",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "pick correct package",
                "warnings": [],
            },
        ),
    )

    downloaded = tmp_path / "mugen.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    download_calls = []
    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda candidate, destination_dir, package=None: (
            download_calls.append(candidate.title)
            or SimpleNamespace(
                status="success",
                downloaded_path=downloaded,
                download_url="https://example.com/mugen.zip",
                selected_package=package,
            )
        ),
    )
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    assert queries == ["鬼灭之刃", "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen"]
    assert download_calls == ["剧场版 鬼灭之刃 无限列车篇"]
    assert result["search_keyword"] == "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen"


def test_process_retries_next_keyword_when_processor_need_confirm(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_season0_fetcher(monkeypatch, tmp_path)
    wrong_candidate = SubtitleCandidate(
        title="鬼灭之刃 游郭篇",
        detail_url="https://bbs.acgrip.com/thread-wrong",
        source="acgrip",
    )
    wrong_candidate.thread_packages = [make_package("wrong", ["batch", "simplified"])]
    correct_candidate = SubtitleCandidate(
        title="剧场版 鬼灭之刃 无限列车篇",
        detail_url="https://bbs.acgrip.com/thread-correct",
        source="acgrip",
    )
    correct_candidate.thread_packages = [
        make_package("mugen", ["batch", "simplified"])
    ]
    queries = []
    processor_calls = []

    def fake_search(keyword, limit=10):
        queries.append(keyword)
        if keyword == "鬼灭之刃":
            return [wrong_candidate]
        if keyword == "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen":
            return [correct_candidate]
        return []

    monkeypatch.setattr(fetcher.provider, "search", fake_search)
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_candidate",
        lambda *args, **kwargs: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="pick current candidate",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "pick current candidate",
                "warnings": [],
            },
        ),
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda *args, **kwargs: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="pick package",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "pick package",
                "warnings": [],
            },
        ),
    )

    downloaded_wrong = tmp_path / "wrong.zip"
    downloaded_wrong.write_text("subtitle", encoding="utf-8")
    downloaded_correct = tmp_path / "correct.zip"
    downloaded_correct.write_text("subtitle", encoding="utf-8")

    def fake_download(candidate, destination_dir, package=None):
        path = downloaded_wrong if "游郭篇" in candidate.title else downloaded_correct
        return SimpleNamespace(
            status="success",
            downloaded_path=path,
            download_url=f"https://example.com/{path.name}",
            selected_package=package,
        )

    def fake_process(path, target_task_uuid=None):
        processor_calls.append(path.name)
        if path == downloaded_wrong:
            return {"status": "need_confirm", "error": "AI 无法确定匹配的动漫，请手动选择"}
        return {"status": "success"}

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(fetcher.processor, "process", fake_process)

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    assert queries == ["鬼灭之刃", "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen"]
    assert processor_calls == ["wrong.zip", "correct.zip"]
    assert result["search_keyword"] == "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen"


def test_process_passes_precise_source_and_target_hints_to_ai(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_season0_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="剧场版 鬼灭之刃 无限列车篇",
        detail_url="https://bbs.acgrip.com/thread-correct",
        source="acgrip",
    )
    candidate.thread_packages = [make_package("mugen", ["batch", "simplified"])]
    ai_inputs = {}

    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)

    def fake_choose_candidate(task_data, ranked_candidates):
        ai_inputs["candidate_task_data"] = task_data
        return SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="correct arc",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "correct arc",
                "warnings": [],
            },
        )

    def fake_choose_package(task_data, candidate_data, package_summaries):
        ai_inputs["package_task_data"] = task_data
        return SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="correct package",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "correct package",
                "warnings": [],
            },
        )

    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_candidate", fake_choose_candidate)
    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_thread_package", fake_choose_package)

    downloaded = tmp_path / "correct.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda candidate, destination_dir, package=None: SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/correct.zip",
            selected_package=package,
        ),
    )
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    candidate_task_data = ai_inputs["candidate_task_data"]
    package_task_data = ai_inputs["package_task_data"]
    assert candidate_task_data["subtitle_auto_fetch_source_title_hint"] == "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen"
    assert candidate_task_data["subtitle_auto_fetch_is_season_zero_tv"] is True
    assert candidate_task_data["subtitle_auto_fetch_source_video_names"] == [
        "[BeanSub&FZSD&VCB-Studio] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen [Ma10p_1080p][x265_flac].mkv"
    ]
    assert candidate_task_data["subtitle_auto_fetch_missing_target_video_names"] == [
        "鬼灭之刃 - S00E14 - 1080p x265 FLAC - BeanSub&FZSD&VCB-Studio.mkv"
    ]


def test_process_uses_ai_query_expansion_after_deterministic_keywords_exhausted(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_season0_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="剧场版 鬼灭之刃 无限列车篇",
        detail_url="https://bbs.acgrip.com/thread-mugen",
        source="acgrip",
    )
    candidate.thread_packages = [make_package("mugen", ["batch", "simplified"])]
    queries = []
    ai_calls = []

    def fake_search(keyword, limit=10):
        queries.append(keyword)
        if keyword == "鬼灭之刃 无限列车篇":
            return [candidate]
        return []

    monkeypatch.setattr(fetcher.provider, "search", fake_search)
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(
        fetcher.ai_client,
        "generate_subtitle_search_queries",
        lambda task_data: (
            ai_calls.append(list(task_data.get("subtitle_auto_fetch_existing_keywords") or []))
            or ["鬼灭之刃", "鬼灭之刃 无限列车篇"]
        ),
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_candidate",
        lambda *args, **kwargs: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="correct arc",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "correct arc",
                "warnings": [],
            },
        ),
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda *args, **kwargs: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="pick package",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "pick package",
                "warnings": [],
            },
        ),
    )

    downloaded = tmp_path / "mugen-ai.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda candidate, destination_dir, package=None: SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/mugen-ai.zip",
            selected_package=package,
        ),
    )
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    assert queries == [
        "鬼灭之刃",
        "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen",
        "鬼灭之刃 无限列车篇",
    ]
    assert ai_calls == [["鬼灭之刃", "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen"]]
    assert result["search_keyword"] == "鬼灭之刃 无限列车篇"



def test_process_skips_ai_query_expansion_when_deterministic_keyword_succeeds(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    candidate = SubtitleCandidate(
        title="Seitokai no Ichizon 字幕",
        detail_url="https://bbs.acgrip.com/thread-1",
        source="acgrip",
    )
    candidate.thread_packages = [make_package("batch", ["batch", "simplified"])]

    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: [candidate])
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(
        fetcher.ai_client,
        "generate_subtitle_search_queries",
        lambda *args, **kwargs: pytest.fail("AI query expansion should not be called"),
    )
    monkeypatch.setattr(fetcher.ai_client, "choose_subtitle_candidate", lambda *args, **kwargs: None)

    downloaded = tmp_path / "success.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda candidate, destination_dir, package=None: SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/success.zip",
            selected_package=package,
        ),
    )
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"



def test_process_appends_deduped_ai_queries_after_existing_keywords(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_season0_fetcher(monkeypatch, tmp_path)
    queries = []

    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: queries.append(keyword) or [])
    monkeypatch.setattr(
        fetcher.ai_client,
        "generate_subtitle_search_queries",
        lambda task_data: [
            "鬼灭之刃",
            "鬼灭之刃 无限列车篇",
            "鬼灭之刃 无限列车篇",
            "Mugen Train",
        ],
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "failed"


def test_process_filters_overbroad_ai_queries_for_specific_season0_target(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_season0_fetcher(monkeypatch, tmp_path)
    queries = []

    monkeypatch.setattr(fetcher.provider, "search", lambda keyword, limit=10: queries.append(keyword) or [])
    monkeypatch.setattr(
        fetcher.ai_client,
        "generate_subtitle_search_queries",
        lambda task_data: ["鬼灭之刃", "鬼灭之刃 无限列车篇"],
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "failed"
    assert queries == [
        "鬼灭之刃",
        "Gekijouban Kimetsu no Yaiba Mugen Ressha Hen",
        "鬼灭之刃 无限列车篇",
    ]





def test_process_allows_broader_ai_query_for_regular_tv_title(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid = build_fetcher(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_task",
        lambda uuid: {
            "uuid": uuid,
            "name": "夜樱四重奏：花之歌",
            "tmdb_name": "夜樱四重奏：花之歌",
            "path": str(tmp_path / "[VCB-Studio] Yozakura Quartet Hana no Uta [1080p]"),
            "season_id": 1,
            "is_movie": False,
            "target_root": str(tmp_path / "Series"),
        },
    )
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.get_record",
        lambda uuid: {
            "a": str(tmp_path / "Series" / "Season 1" / "ep1.mkv")
        },
    )

    candidate = SubtitleCandidate(
        title="夜樱四重奏 字幕",
        detail_url="https://bbs.acgrip.com/thread-yozakura",
        source="acgrip",
    )
    candidate.thread_packages = [make_package("batch", ["batch", "simplified"])]
    queries = []

    def fake_search(keyword, limit=10):
        queries.append(keyword)
        if keyword == "夜樱四重奏":
            return [candidate]
        return []

    monkeypatch.setattr(fetcher.provider, "search", fake_search)
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda candidate: candidate)
    monkeypatch.setattr(fetcher.provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(
        fetcher.ai_client,
        "generate_subtitle_search_queries",
        lambda task_data: ["夜樱四重奏"],
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_candidate",
        lambda *args, **kwargs: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="broad tv query is acceptable",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "broad tv query is acceptable",
                "warnings": [],
            },
        ),
    )
    monkeypatch.setattr(
        fetcher.ai_client,
        "choose_subtitle_thread_package",
        lambda *args, **kwargs: SimpleNamespace(
            selected_index=0,
            should_use=True,
            confidence="High",
            language_assessment="简体中文",
            reason="pick package",
            warnings=[],
            model_dump=lambda: {
                "selected_index": 0,
                "should_use": True,
                "confidence": "High",
                "language_assessment": "简体中文",
                "reason": "pick package",
                "warnings": [],
            },
        ),
    )

    downloaded = tmp_path / "yozakura.zip"
    downloaded.write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr(
        fetcher.provider,
        "download",
        lambda candidate, destination_dir, package=None: SimpleNamespace(
            status="success",
            downloaded_path=downloaded,
            download_url="https://example.com/yozakura.zip",
            selected_package=package,
        ),
    )
    monkeypatch.setattr(
        fetcher.processor,
        "process",
        lambda path, target_task_uuid=None: {"status": "success"},
    )

    result = fetcher.process_task(task_uuid)

    assert result["status"] == "success"
    assert queries == [
        "夜樱四重奏：花之歌",
        "夜樱四重奏 花之歌",
        "Yozakura Quartet Hana no Uta",
        "夜樱四重奏",
    ]
    assert result["search_keyword"] == "夜樱四重奏"
