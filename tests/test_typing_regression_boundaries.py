import json
from pathlib import Path

from src.rename.process import Rename
from src.utils import utils


def test_process_enqueues_subtasks_with_structural_directory_name_inheritance(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("src.rename.process.Search", lambda: object())
    monkeypatch.setattr("src.rename.process.AIProcessor", lambda: object())

    def fake_get_config(key):
        if key in {
            "bangumi_path",
            "movie_path",
            "anime_path",
            "anime_movie_path",
        }:
            return str(tmp_path / key)
        return None

    monkeypatch.setattr("src.rename.process.cm.get_config", fake_get_config)

    parent = tmp_path / "Space Battleship Yamato 2199"
    film_dir = parent / "Film"
    extra_dir = parent / "OVA Collection"
    film_dir.mkdir(parents=True)
    extra_dir.mkdir(parents=True)

    rename = Rename()
    calls: list[dict[str, object]] = []

    def fake_enqueue(**kwargs):
        calls.append(kwargs)
        return "queued"

    result = rename.process(parent, _is_anime=True, _enqueue_task=fake_enqueue)

    assert result is True
    assert len(calls) == 2

    queued_by_name = {Path(str(call["path"])).name: call for call in calls}
    assert queued_by_name["Film"]["cus_name"] == "Space Battleship Yamato 2199"
    assert queued_by_name["Film"]["_is_sub_task"] is True
    assert queued_by_name["Film"]["is_anime"] is True
    assert queued_by_name["OVA Collection"]["cus_name"] is None
    assert queued_by_name["OVA Collection"]["_is_sub_task"] is True


def test_get_task_adds_missing_is_movie_and_sorts_priority_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "TASK_PATH", tmp_path)

    task_path = tmp_path / "task-1.json"
    task_path.write_text(
        json.dumps(
            {
                "name": "Frieren",
                "path": "/library/Frieren",
                "is_anime": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    task = utils.get_task("task-1")

    assert task["is_anime"] is True
    assert "is_movie" in task
    assert task["is_movie"] is None
    assert list(task.keys()) == ["is_anime", "is_movie", "name", "path"]
