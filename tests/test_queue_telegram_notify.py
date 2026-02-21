from pathlib import Path

from src.queue.task_queue import TaskQueueManager


class _FakeTelegramNotifier:
    def __init__(self, available=True, raise_on_send=False):
        self.available = available
        self.raise_on_send = raise_on_send
        self.sent_messages = []
        self.sent_photos = []

    def is_available(self):
        return self.available

    def send_message(self, text):
        if self.raise_on_send:
            raise RuntimeError("mock send failure")
        self.sent_messages.append(text)
        return True, "ok"

    def send_photo(self, photo_url, caption):
        if self.raise_on_send:
            raise RuntimeError("mock send failure")
        self.sent_photos.append((photo_url, caption))
        return True, "ok"


class _FakeTask:
    def __init__(self, task_id, path, error):
        self.task_id = task_id
        self.path = path
        self.error = error


def _build_manager_with_stats(
    total=3,
    success=2,
    failed=1,
    failed_tasks=None,
):
    manager = TaskQueueManager()
    manager._batch_total = total
    manager._batch_success = success
    manager._batch_failed = failed
    manager._batch_success_task_ids = ["task-1", "task-2"] if success else []
    manager._batch_failed_tasks = failed_tasks or [
        {"path": "/tmp/fail.mkv", "error": "mock error"}
    ]
    return manager


def test_trigger_telegram_notification_send_photo_with_caption(monkeypatch):
    notifier = _FakeTelegramNotifier(available=True)
    manager = _build_manager_with_stats()
    manager._batch_success_task_ids = ["task-1", "task-2"]

    monkeypatch.setattr(
        "src.queue.task_queue.get_telegram_notifier",
        lambda: notifier,
    )

    def _get_config(key):
        if key == "telegram_notify_on_success":
            return True
        if key == "telegram_notify_on_failure":
            return True
        return None

    monkeypatch.setattr("src.queue.task_queue.cm.get_config", _get_config)

    def _read_task_data(task_id):
        if task_id == "task-1":
            return {
                "tmdb_name": "葬送的芙莉莲",
                "tmdb_year": "2023",
                "tmdb_media_type": "tv",
                "tmdb_genres": [
                    {"name": "冒险"},
                    {"name": "奇幻"},
                ],
                "name": "葬送的芙莉莲",
                "year": "2023",
                "season_id": 1,
                "is_anime": True,
                "is_movie": False,
                "path": "/tmp/[LoliHouse] title - 01.mkv",
                "release_group": "LoliHouse",
                "resource_term": "1080p HEVC AAC",
                "poster_path": "/abc123.jpg",
            }
        return {
            "tmdb_name": "葬送的芙莉莲",
            "tmdb_year": "2023",
            "tmdb_media_type": "tv",
            "tmdb_genres": [
                {"name": "冒险"},
                {"name": "奇幻"},
            ],
            "name": "葬送的芙莉莲",
            "year": "2023",
            "season_id": 1,
            "is_anime": True,
            "is_movie": False,
            "path": "/tmp/[LoliHouse] title - 02.mkv",
            "release_group": "LoliHouse",
            "resource_term": "1080p HEVC AAC",
            "poster_path": "/abc123.jpg",
        }

    monkeypatch.setattr(manager, "_read_task_data", _read_task_data)

    monkeypatch.setattr(
        manager,
        "_collect_record_targets",
        lambda: [
            Path("/tmp/葬送的芙莉莲 - S01E01 - 第1话.mkv"),
            Path("/tmp/葬送的芙莉莲 - S01E02 - 第2话.mkv"),
        ],
    )

    manager._trigger_telegram_notification()

    assert len(notifier.sent_photos) == 1
    photo_url, caption = notifier.sent_photos[0]
    assert photo_url == "https://image.tmdb.org/t/p/w500/abc123.jpg"
    assert "📂 已入库2个文件" in caption
    assert "葬送的芙莉莲 (2023)" in caption
    assert "📺 集数： S01E01-E02" in caption
    assert "🎭 类别： 动漫" in caption
    assert "👥 小组： LoliHouse" in caption
    assert "🏷️ 标签： 冒险 / 奇幻" in caption
    assert "🌟 质量： 1080p HEVC AAC" in caption


def test_trigger_telegram_notification_fallback_to_text(monkeypatch):
    notifier = _FakeTelegramNotifier(available=True)
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])
    manager._batch_success_task_ids = ["task-1"]

    monkeypatch.setattr(
        "src.queue.task_queue.get_telegram_notifier",
        lambda: notifier,
    )

    def _get_config(key):
        if key == "telegram_notify_on_success":
            return True
        if key == "telegram_notify_on_failure":
            return True
        return None

    monkeypatch.setattr("src.queue.task_queue.cm.get_config", _get_config)

    def _read_task_data(task_id):
        return {
            "tmdb_name": "测试电影",
            "tmdb_year": "2024",
            "tmdb_media_type": "movie",
            "tmdb_genres": [{"name": "动画"}],
            "name": "测试电影",
            "year": "2024",
            "season_id": 0,
            "is_anime": False,
            "is_movie": True,
            "path": "/tmp/test.mkv",
            "release_group": "",
            "resource_term": "1080p x265 AAC",
            "poster_path": None,
        }

    monkeypatch.setattr(manager, "_read_task_data", _read_task_data)
    monkeypatch.setattr(manager, "_collect_record_targets", lambda: [])

    manager._trigger_telegram_notification()

    assert notifier.sent_photos == []
    assert len(notifier.sent_messages) == 1
    assert "📂 已入库1个文件" in notifier.sent_messages[0]
    assert "测试电影 (2024)" in notifier.sent_messages[0]
    assert "🎭 类别： 电影" in notifier.sent_messages[0]
    assert "🏷️ 标签： 动画" in notifier.sent_messages[0]
    assert "🌟 质量： 1080p x265 AAC" in notifier.sent_messages[0]


def test_trigger_telegram_notification_respects_switch(monkeypatch):
    notifier = _FakeTelegramNotifier(available=True)
    manager = _build_manager_with_stats(total=2, success=2, failed=0, failed_tasks=[])

    monkeypatch.setattr(
        "src.queue.task_queue.get_telegram_notifier",
        lambda: notifier,
    )

    def _get_config(key):
        if key == "telegram_notify_on_success":
            return False
        if key == "telegram_notify_on_failure":
            return True
        return None

    monkeypatch.setattr("src.queue.task_queue.cm.get_config", _get_config)

    manager._trigger_telegram_notification()

    assert notifier.sent_messages == []
    assert notifier.sent_photos == []


def test_trigger_telegram_notification_failure_does_not_raise(monkeypatch):
    notifier = _FakeTelegramNotifier(available=True, raise_on_send=True)
    manager = _build_manager_with_stats()

    monkeypatch.setattr(
        "src.queue.task_queue.get_telegram_notifier",
        lambda: notifier,
    )

    def _get_config(key):
        if key == "telegram_notify_on_success":
            return True
        if key == "telegram_notify_on_failure":
            return True
        return None

    monkeypatch.setattr("src.queue.task_queue.cm.get_config", _get_config)

    def _read_task_data(task_id):
        return {
            "tmdb_name": "测试",
            "tmdb_year": "2024",
            "tmdb_media_type": "tv",
            "tmdb_genres": [{"name": "悬疑"}],
            "name": "测试",
            "year": "2024",
            "season_id": 1,
            "is_anime": True,
            "is_movie": False,
            "path": "/tmp/test.mkv",
            "release_group": "TestGroup",
            "resource_term": "1080p",
            "poster_path": "/abc.jpg",
        }

    monkeypatch.setattr(manager, "_read_task_data", _read_task_data)

    manager._trigger_telegram_notification()


def test_build_release_group_prefers_saved_value():
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])
    group = manager._build_release_group(
        [
            {
                "release_group": "SavedGroup",
                "path": "/tmp/[PathGroup] test.mkv",
            }
        ]
    )
    assert group == "SavedGroup"


def test_build_resource_term_prefers_saved_value():
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])
    term = manager._build_resource_term(
        [{"resource_term": "1080p HEVC AAC", "path": "/tmp/test.mkv"}],
        [],
    )
    assert term == "1080p HEVC AAC"


def test_build_resource_term_fallback_from_path_filename():
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])
    term = manager._build_resource_term(
        [{"resource_term": "", "path": "/tmp/[Team] demo 1080p.mkv"}],
        [],
    )
    assert term == "1080p"


def test_record_batch_result_updates_stats():
    manager = _build_manager_with_stats(total=0, success=0, failed=0, failed_tasks=[])

    from src.queue.task_status import TaskStatus

    ok_task = _FakeTask(task_id="ok-id", path="/tmp/ok.mkv", error=None)
    ok_task.status = TaskStatus.COMPLETED
    manager._record_batch_result(ok_task)

    fail_task = _FakeTask(
        task_id="fail-id", path="/tmp/fail.mkv", error="failure reason"
    )
    fail_task.status = TaskStatus.FAILED
    manager._record_batch_result(fail_task)

    assert manager._batch_total == 2
    assert manager._batch_success == 1
    assert manager._batch_failed == 1
    assert manager._batch_success_task_ids == ["ok-id"]
    assert manager._batch_failed_tasks[0]["path"] == "/tmp/fail.mkv"
