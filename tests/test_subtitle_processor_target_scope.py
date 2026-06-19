from pathlib import Path
from types import SimpleNamespace

import pytest

from src.subtitle.processor import SubtitleProcessor


def _force_single_shot_backend(monkeypatch):
    """单测走 Phase 2 单轮后端，避免起 Pi sidecar。"""
    from src.subtitle.case_agent import local_subtitle_entry

    real_get_config = local_subtitle_entry._get_config

    def fake_get_config(key, default=None):
        if key == "subtitle_case_agent_backend":
            return "single_shot"
        return real_get_config(key, default)

    monkeypatch.setattr(local_subtitle_entry, "_get_config", fake_get_config)


def test_process_loads_target_tasks_via_precise_helper(monkeypatch, tmp_path):
    processor = SubtitleProcessor()
    _force_single_shot_backend(monkeypatch)
    archive_path = tmp_path / "archive.rar"
    archive_path.write_bytes(b"data")

    subtitle = SimpleNamespace(
        temp_path=tmp_path / "a.ass",
        archive_path="a.ass",
        filename="a.ass",
    )
    subtitle.temp_path.write_text("subtitle", encoding="utf-8")

    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: [subtitle])
    monkeypatch.setattr(
        processor.extractor,
        "get_archive_structure",
        lambda subtitle_files: {"/": ["a.ass"]},
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)

    precise_calls = []
    general_calls = []

    def fake_precise_loader(task_uuid):
        precise_calls.append(task_uuid)
        return [{"uuid": "task-1", "is_movie": True, "title": "Show", "season": 1}]

    def fake_general_loader(max_tasks=10, target_root=None):
        general_calls.append((max_tasks, target_root))
        return []

    monkeypatch.setattr(
        processor,
        "_load_processed_tasks_for_target_uuid",
        fake_precise_loader,
    )
    monkeypatch.setattr(processor, "_load_processed_tasks", fake_general_loader)
    monkeypatch.setattr(
        processor.ai_client,
        "analyze_subtitle_mapping",
        lambda **kwargs: SimpleNamespace(mappings=[], confidence="Low"),
    )

    result = processor.process(archive_path, target_task_uuid="task-1")

    assert precise_calls == ["task-1"]
    assert general_calls == []
    assert result["status"] == "need_confirm"


def test_process_keeps_precise_target_scope_when_tv_task_has_no_target_root(
    monkeypatch,
    tmp_path,
):
    processor = SubtitleProcessor()
    _force_single_shot_backend(monkeypatch)
    archive_path = tmp_path / "archive.rar"
    archive_path.write_bytes(b"data")

    subtitle = SimpleNamespace(
        temp_path=tmp_path / "a.ass",
        archive_path="a.ass",
        filename="a.ass",
    )
    subtitle.temp_path.write_text("subtitle", encoding="utf-8")

    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: [subtitle])
    monkeypatch.setattr(
        processor.extractor,
        "get_archive_structure",
        lambda subtitle_files: {"/": ["a.ass"]},
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)

    precise_task = {
        "uuid": "task-1",
        "is_movie": False,
        "title": "血意少年",
        "season": 1,
        "target_root": "",
        "target_dir": str(tmp_path / "Series" / "Season 1"),
        "videos": ["ep1.mkv"],
        "video_targets": {"ep1.mkv": str(tmp_path / "Series" / "Season 1" / "ep1.mkv")},
    }
    general_calls = []

    monkeypatch.setattr(
        processor,
        "_load_processed_tasks_for_target_uuid",
        lambda task_uuid: [precise_task],
    )

    def fake_general_loader(max_tasks=10, target_root=None):
        general_calls.append((max_tasks, target_root))
        return [
            {
                "uuid": "other-task",
                "is_movie": False,
                "title": "无关作品",
                "season": 1,
                "target_root": "",
                "target_dir": str(tmp_path / "Other" / "Season 1"),
                "videos": ["other.mkv"],
                "video_targets": {"other.mkv": str(tmp_path / "Other" / "Season 1" / "other.mkv")},
            }
        ]

    monkeypatch.setattr(processor, "_load_processed_tasks", fake_general_loader)
    monkeypatch.setattr(
        processor.ai_client,
        "analyze_subtitle_mapping",
        lambda **kwargs: SimpleNamespace(mappings=[], confidence="Low"),
    )

    result = processor.process(archive_path, target_task_uuid="task-1")

    assert general_calls == []
    assert result["available_tasks"] == [
        {
            "uuid": "task-1",
            "title": "血意少年",
            "season": 1,
        }
    ]
