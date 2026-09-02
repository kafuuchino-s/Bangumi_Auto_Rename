import json
from pathlib import Path
from types import SimpleNamespace

from src.subtitle.extractor import ExtractedSubtitle
from src.subtitle.processor import SubtitleProcessor
from src.subtitle.syncer import SyncResult


def _build_process_fixture(
    monkeypatch,
    tmp_path,
    *,
    sync_enabled=True,
    sync_mode="best_effort",
    overwrite_policy="follow_global",
    sync_result=None,
    create_video=True,
):
    task_dir = tmp_path / "task_data"
    task_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.subtitle.processor.TASK_PATH", task_dir)

    config = {
        "subtitle_sync_enabled": sync_enabled,
        "subtitle_sync_mode": sync_mode,
        "subtitle_sync_overwrite_policy": overwrite_policy,
    }
    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: config.get(key),
    )

    processor = SubtitleProcessor()
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    source_path = extract_dir / "a.ass"
    source_path.write_text("subtitle", encoding="utf-8")

    subtitle = ExtractedSubtitle(
        temp_path=source_path,
        archive_path="subs/a.ass",
        filename="a.ass",
    )

    target_dir = tmp_path / "target" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    video_name = "Show - S01E01 - Pilot.mkv"
    video_path = target_dir / video_name
    if create_video:
        video_path.write_text("video", encoding="utf-8")

    task = {
        "uuid": "task-1",
        "title": "Show",
        "season": 1,
        "target_dir": str(target_dir),
        "videos": [video_name],
        "video_targets": {video_name: str(video_path)},
        "is_movie": False,
    }

    monkeypatch.setattr(
        processor,
        "_load_processed_tasks",
        lambda max_tasks=10: [task],
    )
    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: [subtitle])
    monkeypatch.setattr(
        processor.extractor,
        "get_archive_structure",
        lambda subtitle_files: {"subs": ["a.ass"]},
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)
    monkeypatch.setattr(
        processor.extractor,
        "get_extract_dir",
        lambda archive_path: extract_dir,
    )

    def _fake_case_agent(
        *,
        _uuid,
        archive_path,
        subtitle_files,
        processed_tasks,
        mapping_only=False,
        allowed_target_keys=None,
    ):
        from src.subtitle.case_agent import (
            build_subtitle_case_workspace,
            build_subtitle_file_cards,
            build_target_video_cards,
        )
        from src.subtitle.case_agent.models import (
            CompiledSubtitleMapping,
            CompiledSubtitlePlan,
        )

        workspace = build_subtitle_case_workspace(
            archive_name=archive_path.name,
            subtitle_files=build_subtitle_file_cards(subtitle_files),
            target_videos=build_target_video_cards(processed_tasks),
        )
        subtitle_card = workspace.subtitle_files[0]
        target_card = workspace.target_videos[0]
        plan = CompiledSubtitlePlan(
            mappings=[
                CompiledSubtitleMapping(
                    subtitle_ref=subtitle_card.ref,
                    subtitle_archive_path=subtitle_card.archive_path,
                    target_ref=target_card.ref,
                    task_uuid=target_card.task_uuid,
                    video=target_card.video,
                    target_dir=target_card.target_dir,
                    emby_lang="zh-CN",
                    is_simplified=True,
                    is_movie=target_card.is_movie,
                )
            ],
            unmatched=[],
            summary="test plan",
        )
        return processor._land_compiled_plan(
            _uuid=_uuid,
            archive_path=archive_path,
            subtitle_files=subtitle_files,
            processed_tasks=processed_tasks,
            compiled_plan=plan,
            snapshot=None,
            confidence="High",
            pipeline_mode="subtitle_case_agent",
            mapping_only=mapping_only,
            allowed_target_keys=allowed_target_keys,
        )


    monkeypatch.setattr(processor, "_process_case_agent", _fake_case_agent)

    if sync_result is None:
        synced_path = extract_dir / ".ffsubsync" / "a.ass"
        synced_path.parent.mkdir(parents=True, exist_ok=True)
        synced_path.write_text("synced", encoding="utf-8")
        sync_result = SyncResult(
            success=True,
            used_fallback=False,
            reason="",
            output_path=synced_path,
            duration=0.01,
        )

    monkeypatch.setattr(
        processor.syncer,
        "sync_subtitle",
        lambda **kwargs: sync_result,
    )

    return processor, {
        "task_dir": task_dir,
        "source_path": source_path,
        "target_dir": target_dir,
        "video_name": video_name,
    }


def test_sync_overwrite_policy_follow_global(monkeypatch):
    processor = SubtitleProcessor()

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: "follow_global" if key == "subtitle_sync_overwrite_policy" else None,
    )

    assert processor._resolve_sync_overwrite_policy() is None


def test_sync_overwrite_policy_overwrite(monkeypatch):
    processor = SubtitleProcessor()

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: "overwrite" if key == "subtitle_sync_overwrite_policy" else None,
    )

    assert processor._resolve_sync_overwrite_policy() is True


def test_sync_overwrite_policy_skip(monkeypatch):
    processor = SubtitleProcessor()

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: "skip" if key == "subtitle_sync_overwrite_policy" else None,
    )

    assert processor._resolve_sync_overwrite_policy() is False


def test_apply_subtitle_sync_best_effort_fallback(monkeypatch, tmp_path):
    processor = SubtitleProcessor()

    source_path = tmp_path / "src.ass"
    source_path.write_text("dummy", encoding="utf-8")
    target_path = tmp_path / "dst.ass"
    video_path = tmp_path / "missing_video.mkv"  # 不存在 -> skipped

    detail = {"subtitle": "A/src.ass", "sync_status": "disabled"}
    sync_items = [
        {
            "source_path": source_path,
            "target_path": target_path,
            "video_path": video_path,
            "detail": detail,
        }
    ]

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: ("best_effort" if key == "subtitle_sync_mode" else None),
    )

    summary, final_mapping = processor._apply_subtitle_sync(
        archive_path=tmp_path / "archive.zip",
        sync_items=sync_items,
        original_mapping={source_path: target_path},
    )

    assert summary["enabled"] is True
    assert summary["mode"] == "best_effort"
    assert summary["attempted"] == 1
    assert summary["skipped"] == 1
    assert summary["strict_failed"] is False
    assert detail["sync_status"] == "skipped"
    assert final_mapping == {source_path: target_path}


def test_apply_subtitle_sync_strict_failed(monkeypatch, tmp_path):
    processor = SubtitleProcessor()

    source_path = tmp_path / "src.ass"
    source_path.write_text("dummy", encoding="utf-8")
    target_path = tmp_path / "dst.ass"
    video_path = tmp_path / "missing_video.mkv"  # 不存在 -> strict failed

    detail = {"subtitle": "A/src.ass", "sync_status": "disabled"}
    sync_items = [
        {
            "source_path": source_path,
            "target_path": target_path,
            "video_path": video_path,
            "detail": detail,
        }
    ]

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: ("strict" if key == "subtitle_sync_mode" else None),
    )

    summary, final_mapping = processor._apply_subtitle_sync(
        archive_path=tmp_path / "archive.zip",
        sync_items=sync_items,
        original_mapping={source_path: target_path},
    )

    assert summary["enabled"] is True
    assert summary["mode"] == "strict"
    assert summary["attempted"] == 1
    assert summary["failed"] == 1
    assert summary["strict_failed"] is True
    assert summary["strict_error"]
    assert detail["sync_status"] == "skipped"
    assert final_mapping == {source_path: target_path}


def test_process_sync_success_uses_synced_mapping(monkeypatch, tmp_path):
    processor, fixture = _build_process_fixture(
        monkeypatch,
        tmp_path,
        sync_enabled=True,
        sync_mode="best_effort",
        overwrite_policy="overwrite",
    )

    trans_call = {}

    class FakeTrans:
        def __init__(self, R, uuid, force_mode=None, force_overwrite=None):
            trans_call["R"] = dict(R)
            trans_call["uuid"] = uuid
            trans_call["force_mode"] = force_mode
            trans_call["force_overwrite"] = force_overwrite

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    expected_target = (
        fixture["target_dir"] / "Show - S01E01 - Pilot.zh-CN.default.ass"
    )

    assert result["status"] == "success"
    assert result["sync_summary"]["enabled"] is True
    assert result["sync_summary"]["success"] == 1
    assert result["sync_summary"]["fallback"] == 0
    assert result["mappings"][0]["sync_status"] == "synced"

    assert trans_call["force_mode"] == "复制"
    assert trans_call["force_overwrite"] is True
    assert fixture["source_path"] not in trans_call["R"]
    assert expected_target == trans_call["R"].get(
        next(iter(trans_call["R"].keys()))
    )


def test_process_sync_best_effort_fallback_still_success(monkeypatch, tmp_path):
    sync_result = SyncResult(
        success=False,
        used_fallback=True,
        reason="mock fail",
        output_path=None,
        duration=0.02,
    )
    processor, fixture = _build_process_fixture(
        monkeypatch,
        tmp_path,
        sync_enabled=True,
        sync_mode="best_effort",
        overwrite_policy="follow_global",
        sync_result=sync_result,
    )

    trans_call = {}

    class FakeTrans:
        def __init__(self, R, uuid, force_mode=None, force_overwrite=None):
            trans_call["R"] = dict(R)
            trans_call["force_overwrite"] = force_overwrite

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "success"
    assert result["sync_summary"]["attempted"] == 1
    assert result["sync_summary"]["success"] == 0
    assert result["sync_summary"]["fallback"] == 1
    assert result["sync_summary"]["strict_failed"] is False
    assert result["mappings"][0]["sync_status"] == "fallback"

    assert fixture["source_path"] in trans_call["R"]
    assert trans_call["force_overwrite"] is None


def test_process_sync_strict_failure_writes_error_task(monkeypatch, tmp_path):
    sync_result = SyncResult(
        success=False,
        used_fallback=True,
        reason="mock fail",
        output_path=None,
        duration=0.02,
    )
    processor, fixture = _build_process_fixture(
        monkeypatch,
        tmp_path,
        sync_enabled=True,
        sync_mode="strict",
        overwrite_policy="follow_global",
        sync_result=sync_result,
    )

    trans_called = {"value": False}

    class FakeTrans:
        def __init__(self, *args, **kwargs):
            trans_called["value"] = True

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "error"
    assert "字幕对齐失败" in result["error"]
    assert result["sync_summary"]["strict_failed"] is True
    assert result["mappings"][0]["sync_status"] == "fallback"
    assert trans_called["value"] is False

    task_files = list(fixture["task_dir"].glob("*.json"))
    assert len(task_files) == 1

    task_data = json.loads(task_files[0].read_text(encoding="utf-8"))
    assert task_data["status"] == "error"
    assert task_data["sync_summary"]["strict_failed"] is True
    assert task_data["mappings"][0]["sync_status"] == "fallback"
