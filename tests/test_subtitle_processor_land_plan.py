"""_land_compiled_plan 0-mappings 语义测试。

回归 0045 sel#1 bug：Case Agent accepted + 0 mappings + 全 unmatched（no_target_video）
时，processor 旧代码 `if not file_mapping: error` 把合格结果误报成 error。
修复后：0 mappings + 有 unmatched → accepted（success + no_target_videos），
只有 0 mappings + 0 unmatched 才是 error。
"""
from pathlib import Path
from unittest.mock import patch

from src.subtitle.case_agent.models import (
    CompiledSubtitleMapping,
    CompiledSubtitlePlan,
    CompiledUnmatchedEntry,
)
from src.subtitle.processor import SubtitleProcessor


def _make_subtitle_files(tmp_path: Path):
    """造两个字幕文件事实（ExtractedSubtitle 等价 dict）。"""
    from src.subtitle.extractor import ExtractedSubtitle

    f1 = tmp_path / "sub1.ass"
    f1.write_text("sub", encoding="utf-8")
    f2 = tmp_path / "sub2.ass"
    f2.write_text("sub", encoding="utf-8")
    return [
        ExtractedSubtitle(temp_path=f1, archive_path="sub1.ass", filename="sub1.ass"),
        ExtractedSubtitle(temp_path=f2, archive_path="sub2.ass", filename="sub2.ass"),
    ]


def _make_processed_tasks(tmp_path: Path):
    """造一个已处理任务（含一个目标视频）。"""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    return [
        {
            "uuid": "task-1",
            "title": "Test Anime",
            "target_dir": str(target_dir),
            "is_movie": False,
            "videos": ["Test Anime - S01E01.mkv"],
            "video_targets": {},
        }
    ]


def test_zero_mappings_with_unmatched_returns_success_not_error(
    monkeypatch, tmp_path
):
    """Case Agent accepted + 0 mappings + 全 unmatched → success + no_target_videos，
    不是 error。回归 0045 sel#1（字幕包无目标视频字幕，Case Agent 正确判 accepted）。"""
    processor = SubtitleProcessor()
    archive = tmp_path / "subs.zip"
    archive.write_bytes(b"fake")
    subs = _make_subtitle_files(tmp_path)
    tasks = _make_processed_tasks(tmp_path)

    # mock 解压直接返回 subs
    monkeypatch.setattr(processor.extractor, "extract", lambda p: subs)
    monkeypatch.setattr(processor.extractor, "cleanup", lambda p: None)
    monkeypatch.setattr(
        processor, "_resolve_processed_tasks", lambda target_task_uuid=None: (tasks, "")
    )
    monkeypatch.setattr(
        processor.extractor,
        "get_archive_structure",
        lambda s: {"": ["sub1.ass", "sub2.ass"]},
    )
    # case_agent_enabled=True 走 Case Agent
    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda k, default=None: True if k == "subtitle_case_agent_primary_enabled" else default,
    )

    # mock Case Agent 入口返回 accepted + 0 mappings + 2 unmatched (no_target_video)
    compiled_plan = CompiledSubtitlePlan(
        mappings=[],
        unmatched=[
            CompiledUnmatchedEntry(ref="SF1", reason_kind="no_target_video", reason="no target"),
            CompiledUnmatchedEntry(ref="SF2", reason_kind="no_target_video", reason="no target"),
        ],
        summary="all subtitles lack matching target",
    )
    entry_result = {
        "ok": True,
        "status": "accepted",
        "summary": "all subtitles lack matching target",
        "snapshot": {"draft": {"status": "accepted"}},
        "compiled_plan": compiled_plan,
    }
    monkeypatch.setattr(
        "src.subtitle.case_agent.run_subtitle_case_agent_mapping",
        lambda **kw: entry_result,
    )

    result = processor.process_mapping(archive, target_task_uuid="task-1")

    # 关键：不再报 error，是 success（accepted + 0 matched + unmatched 详情）
    assert result["status"] == "success", f"expected success, got {result.get('status')}: {result.get('error')}"
    assert result.get("error") is None
    assert result.get("pipeline_mode") == "subtitle_case_agent_primary"
    # 0 mappings，unmatched 走 no_target_videos 分类
    assert len(result.get("mappings") or []) == 0
    # no_target_videos 应有 2 条（no_target_video 分类）
    assert len(result.get("no_target_videos") or []) == 2


def test_zero_mappings_zero_unmatched_still_error(monkeypatch, tmp_path):
    """Case Agent accepted + 0 mappings + 0 unmatched → 真实现错误 → error。
    确保修复没破坏"啥都没产出"的 error 语义。"""
    processor = SubtitleProcessor()
    archive = tmp_path / "subs.zip"
    archive.write_bytes(b"fake")
    subs = _make_subtitle_files(tmp_path)
    tasks = _make_processed_tasks(tmp_path)

    monkeypatch.setattr(processor.extractor, "extract", lambda p: subs)
    monkeypatch.setattr(processor.extractor, "cleanup", lambda p: None)
    monkeypatch.setattr(
        processor, "_resolve_processed_tasks", lambda target_task_uuid=None: (tasks, "")
    )
    monkeypatch.setattr(
        processor.extractor,
        "get_archive_structure",
        lambda s: {"": ["sub1.ass", "sub2.ass"]},
    )
    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda k, default=None: True if k == "subtitle_case_agent_primary_enabled" else default,
    )

    # mock Case Agent 返回 accepted + 0 mappings + 0 unmatched（异常：accepted 却啥都没）
    compiled_plan = CompiledSubtitlePlan(
        mappings=[],
        unmatched=[],
        summary="",
    )
    entry_result = {
        "ok": True,
        "status": "accepted",
        "summary": "",
        "snapshot": {},
        "compiled_plan": compiled_plan,
    }
    monkeypatch.setattr(
        "src.subtitle.case_agent.run_subtitle_case_agent_mapping",
        lambda **kw: entry_result,
    )

    result = processor.process_mapping(archive, target_task_uuid="task-1")

    # 0 mappings + 0 unmatched → error（实现错误）
    assert result["status"] == "error"
    assert "无法建立字幕映射" in (result.get("error") or "")


def test_land_plan_only_writes_allowed_target_videos(monkeypatch, tmp_path):
    processor = SubtitleProcessor()
    archive = tmp_path / "subs.zip"
    archive.write_bytes(b"fake")
    subs = _make_subtitle_files(tmp_path)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    videos = [target_dir / "Show - S01E01.mkv", target_dir / "Show - S01E02.mkv"]
    tasks = [
        {
            "uuid": "task-1",
            "title": "Show",
            "target_dir": str(target_dir),
            "is_movie": False,
            "videos": [video.name for video in videos],
            "video_targets": {},
        }
    ]
    plan = CompiledSubtitlePlan(
        mappings=[
            CompiledSubtitleMapping(
                subtitle_ref=f"SF{index}",
                subtitle_archive_path=f"sub{index}.ass",
                target_ref=f"TV{index}",
                task_uuid="task-1",
                video=video.name,
                emby_lang="zh-CN",
                is_simplified=True,
            )
            for index, video in enumerate(videos, start=1)
        ]
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda path: None)

    result = processor._land_compiled_plan(
        _uuid="scope-test",
        archive_path=archive,
        subtitle_files=subs,
        processed_tasks=tasks,
        compiled_plan=plan,
        snapshot={},
        confidence="High",
        mapping_only=True,
        allowed_target_keys={processor._normalize_card_path(str(videos[0]))},
    )

    assert result["status"] == "success"
    assert result["matched_count"] == 1


def test_land_plan_filters_nonpreferred_language_without_writing(
    monkeypatch, tmp_path
):
    processor = SubtitleProcessor()
    archive = tmp_path / "subs.zip"
    archive.write_bytes(b"fake")
    subs = _make_subtitle_files(tmp_path)
    tasks = _make_processed_tasks(tmp_path)
    plan = CompiledSubtitlePlan(
        mappings=[
            CompiledSubtitleMapping(
                subtitle_ref="SF1",
                subtitle_archive_path="sub1.ass",
                target_ref="TV1",
                task_uuid="task-1",
                video="Test Anime - S01E01.mkv",
                emby_lang="zh-TW",
                is_simplified=False,
            )
        ]
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda path: None)

    result = processor._land_compiled_plan(
        _uuid="language-filter-test",
        archive_path=archive,
        subtitle_files=subs,
        processed_tasks=tasks,
        compiled_plan=plan,
        snapshot={},
        confidence="High",
        allowed_emby_languages={"zh-CN"},
    )

    assert result["status"] == "success"
    assert result["matched_count"] == 0
    assert result["mappings"] == []
    assert result["language_mismatches"][0]["language"] == "zh-TW"
    assert result["language_mismatches"][0]["write_status"] == (
        "filtered_nonpreferred_language"
    )
    assert not list((tmp_path / "target").glob("*.ass"))
