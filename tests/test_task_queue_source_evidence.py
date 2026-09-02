from __future__ import annotations

import json
from pathlib import Path

import src.utils.utils as utils
from src.queue.task_queue import TaskQueueManager
from src.queue.task_status import QueuedTask


def test_source_evidence_is_persisted_after_rename(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_path = tmp_path / "tasks"
    task_path.mkdir()
    monkeypatch.setattr(utils, "TASK_PATH", task_path)
    (task_path / "task-1.json").write_text(
        '{"path": "/media/Anime/Title", "name": "Title"}',
        encoding="utf-8",
    )
    task = QueuedTask(
        task_id="task-1",
        path="/media/Anime/Title",
        source_evidence={
            "provider": "moviepilot",
            "download_hash": "abc123",
        },
    )

    TaskQueueManager._persist_source_evidence(task)

    saved = json.loads((task_path / "task-1.json").read_text(encoding="utf-8"))
    assert saved["source_evidence"] == {
        "provider": "moviepilot",
        "download_hash": "abc123",
    }


def test_source_evidence_write_failure_does_not_escape(monkeypatch) -> None:
    def fail_get_task(_uuid: str):
        raise OSError("disk unavailable")

    monkeypatch.setattr("src.utils.utils.get_task", fail_get_task)
    task = QueuedTask(
        task_id="task-id",
        path="/media/source",
        source_evidence={"provider": "moviepilot"},
    )

    TaskQueueManager._persist_source_evidence(task)


def test_split_children_inherit_source_evidence(monkeypatch) -> None:
    manager = TaskQueueManager()
    captured: list[dict[str, object]] = []

    def fake_enqueue(self, **kwargs):
        captured.append(kwargs)
        return "child-task"

    def fake_process(self, *_args, **kwargs):
        kwargs["_enqueue_task"](
            path="/media/Anime/Title/Disc 1",
            is_anime=None,
            is_movie=None,
            _is_sub_task=True,
        )
        return True

    monkeypatch.setattr(TaskQueueManager, "enqueue", fake_enqueue)
    monkeypatch.setattr("src.rename.process.Rename.process", fake_process)
    task = QueuedTask(
        task_id="parent-task",
        path="/media/Anime/Title",
        source_evidence={
            "provider": "moviepilot",
            "download_hash": "abc123",
        },
    )

    assert manager._execute_rename(task) is True
    assert captured[0]["source_evidence"] == task.source_evidence
