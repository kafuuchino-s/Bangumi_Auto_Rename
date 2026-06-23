from pathlib import Path

from src.queue.task_queue import TaskQueueManager
from src.rename.process import Rename


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
            Path("/tmp/葬送的芙莉莲 - S01E01 - 1080p.mkv"),
            Path("/tmp/葬送的芙莉莲 - S01E02 - 1080p.mkv"),
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


def test_trigger_telegram_notification_all_skipped_reports_skipped(monkeypatch, tmp_path):
    """全跳过场景：record 文件存在但内容为空（本次 0 实际落地）且 task_data
    记 skipped_file_count>0 时，TG 通知应如实显示「跳过入库 N 个文件」，
    而非「已入库0个文件」伪装无操作、也不回退 _batch_success 虚报。

    区分两种 record_targets 为空的情形：
      - 有 record 文件但空（全跳过）→ had_any_record_file=True → landed=0
      - 完全无 record 文件（异常）→ had_any_record_file=False → 回退 _batch_success
    """
    notifier = _FakeTelegramNotifier(available=True)
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])
    manager._batch_success_task_ids = ["task-all-skip"]

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
            "skipped_file_count": 18,
        }

    monkeypatch.setattr(manager, "_read_task_data", _read_task_data)

    # 真实 record 文件存在但内容为空 dict（全跳过落地 0 个）
    record_dir = tmp_path / "record"
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "task-all-skip.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("src.utils.path.RECORD_PATH", record_dir)

    # 不 mock _collect_record_targets，走真实路径读空 record
    manager._trigger_telegram_notification()

    assert manager._had_any_record_file is True
    assert len(notifier.sent_messages) == 1
    # 全跳过如实显示「跳过入库18个文件」，不伪装「已入库0」也不回退「已入库1」
    assert "📂 跳过入库18个文件" in notifier.sent_messages[0]
    assert "已入库1个文件" not in notifier.sent_messages[0]


def test_trigger_telegram_notification_mixed_landed_and_skipped(monkeypatch, tmp_path):
    """混合场景：本次实际落地 X 个 + 跳过 Y 个已存在，首行显示
    「已入库 X 个文件（跳过 Y 个已存在）」。"""
    import json as _json

    notifier = _FakeTelegramNotifier(available=True)
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])
    manager._batch_success_task_ids = ["task-mix"]

    monkeypatch.setattr("src.queue.task_queue.get_telegram_notifier", lambda: notifier)

    def _get_config(key):
        if key == "telegram_notify_on_success":
            return True
        if key == "telegram_notify_on_failure":
            return True
        return None

    monkeypatch.setattr("src.queue.task_queue.cm.get_config", _get_config)

    def _read_task_data(task_id):
        return {
            "tmdb_name": "测试番剧",
            "tmdb_year": "2024",
            "tmdb_media_type": "tv",
            "tmdb_genres": [{"name": "动画"}],
            "name": "测试番剧",
            "year": "2024",
            "season_id": 1,
            "is_anime": True,
            "is_movie": False,
            "path": "/tmp/test.mkv",
            "release_group": "",
            "resource_term": "1080p x265 AAC",
            "poster_path": None,
            "skipped_file_count": 6,
        }

    monkeypatch.setattr(manager, "_read_task_data", _read_task_data)

    # record 有 12 个实际落地目标
    record_dir = tmp_path / "record"
    record_dir.mkdir(parents=True, exist_ok=True)
    record = {f"/tmp/src{i:02d}.mkv": f"/tmp/测试番剧 - S01E{i:02d}.mkv" for i in range(1, 13)}
    (record_dir / "task-mix.json").write_text(_json.dumps(record, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("src.utils.path.RECORD_PATH", record_dir)

    manager._trigger_telegram_notification()

    assert len(notifier.sent_messages) == 1
    assert "📂 已入库12个文件（跳过6个已存在）" in notifier.sent_messages[0]


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


def test_extract_episode_from_name_supports_sxxexx_format():
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])

    assert manager._extract_episode_from_name("战勇。 - S02E13 - x264 FLAC - Final8.mkv") == 13


def test_build_season_episode_uses_episode_number_for_season0():
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])

    season_episode = manager._build_season_episode(
        [{"season_id": 0}],
        [Path("/tmp/超次元游戏：海王星 - S00E03 - HEVC FLAC.mkv")],
    )

    assert season_episode == "S00E03"


def test_build_season_episode_groups_main_episodes_and_specials_separately():
    """正片 + 特典分开显示：12 正片 + 6 特典应显示
    「S01E01-E12 + S00E02-E07」，而非旧的「S01E01-E12」（漏特典）。"""
    manager = _build_manager_with_stats(total=1, success=18, failed=0, failed_tasks=[])

    targets = [
        Path(f"/tmp/Jinrui - S01E{i:02d} - 1080p.mkv") for i in range(1, 13)
    ] + [
        Path(f"/tmp/Jinrui - S00E{i:02d} - Special.mkv") for i in range(2, 8)
    ]

    season_episode = manager._build_season_episode([{"season_id": 1}], targets)

    assert season_episode == "S01E01-E12 + S00E02-E07"


def test_build_season_episode_orders_main_seasons_before_specials():
    """正片季升序在前，特典（S00）在后；单集不显示范围。"""
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])

    targets = [
        Path("/tmp/Show - S00E05 - Special.mkv"),
        Path("/tmp/Show - S02E01 - 1080p.mkv"),
        Path("/tmp/Show - S01E01 - 1080p.mkv"),
    ]

    season_episode = manager._build_season_episode(
        [{"season_id": 1}, {"season_id": 2}, {"season_id": 0}], targets
    )

    assert season_episode == "S01E01 + S02E01 + S00E05"


def test_build_season_episode_non_contiguous_lists_each_episode():
    """同季集数不连续时逐个列出，不用 min-max 区间虚报缺失集数。

    S00 只有 E02/E03/E07（缺 E04-E06）→ ``S00E02-E03,E07``，不显示 ``S00E02-E07``。
    """
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])

    targets = [
        Path("/tmp/Show - S00E07 - Special.mkv"),
        Path("/tmp/Show - S00E02 - Special.mkv"),
        Path("/tmp/Show - S00E03 - Special.mkv"),
    ]

    season_episode = manager._build_season_episode([{"season_id": 0}], targets)

    assert season_episode == "S00E02-E03,E07"


def test_build_season_episode_single_gap_lists_both_sides():
    """S01 有 E01-E05 + E07-E12（缺 E06）→ 两段分别连续，逗号分隔。"""
    manager = _build_manager_with_stats(total=1, success=1, failed=0, failed_tasks=[])

    targets = [Path(f"/tmp/Show - S01E{i:02d} - 1080p.mkv") for i in range(1, 6)]
    targets += [Path(f"/tmp/Show - S01E{i:02d} - 1080p.mkv") for i in range(7, 13)]

    season_episode = manager._build_season_episode([{"season_id": 1}], targets)

    assert season_episode == "S01E01-E05,E07-E12"


def test_resolve_task_poster_path_tv_prefers_season_poster():
    renamer = Rename()
    info = {
        "poster_path": "/series.jpg",
        "seasons": [
            {"season_number": 0, "poster_path": "/s00.jpg"},
            {"season_number": 1, "poster_path": "/s01.jpg"},
        ],
    }

    assert renamer._resolve_task_poster_path(
        info=info,
        is_movie=False,
        season_id=1,
    ) == "/s01.jpg"


def test_resolve_task_poster_path_tv_fallback_to_series_poster():
    renamer = Rename()
    info = {
        "poster_path": "/series.jpg",
        "seasons": [
            {"season_number": 1, "poster_path": ""},
            {"season_number": 2},
        ],
    }

    assert renamer._resolve_task_poster_path(
        info=info,
        is_movie=False,
        season_id=1,
    ) == "/series.jpg"


def test_resolve_task_poster_path_movie_keeps_current_poster():
    renamer = Rename()
    info = {
        "poster_path": "/movie.jpg",
        "seasons": [
            {"season_number": 1, "poster_path": "/s01.jpg"},
        ],
    }

    assert renamer._resolve_task_poster_path(
        info=info,
        is_movie=True,
        season_id=1,
    ) == "/movie.jpg"
