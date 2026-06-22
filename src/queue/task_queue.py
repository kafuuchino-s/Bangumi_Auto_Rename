"""任务队列管理器 - 单例模式"""
import asyncio
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from nicegui import run

from ..config.config_manager import cm
from ..logger import logger
from ..notification.emby_notify import get_emby_notifier
from ..notification.telegram_notify import get_telegram_notifier
from ..rename.cleaner import extract_video_format
from ..subtitle.auto_fetch import SubtitleAutoFetcher
from .task_status import QueuedTask, TaskStatus

RefreshCallback = Callable[[], None]
TaskData = dict[str, object]
FailedTaskInfo = dict[str, str]


class TaskQueueManager:
    """任务队列管理器（单例）"""

    _instance: "TaskQueueManager | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._queue: asyncio.Queue[QueuedTask] = asyncio.Queue()
        self._tasks: dict[str, QueuedTask] = {}  # task_id -> QueuedTask
        self._running_tasks: dict[str, QueuedTask] = {}  # 正在运行的任务
        self._workers_running: bool = False
        self._worker_count: int = 0
        self._refresh_callbacks: list[RefreshCallback] = []
        self._batch_total: int = 0
        self._batch_success: int = 0
        self._batch_failed: int = 0
        self._batch_success_task_ids: list[str] = []
        self._batch_failed_tasks: list[FailedTaskInfo] = []
        self._initialized: bool = True

        logger.info("[队列] 任务队列管理器已初始化")

    def add_refresh_callback(self, callback: RefreshCallback) -> None:
        """注册UI刷新回调"""
        if callback not in self._refresh_callbacks:
            self._refresh_callbacks.append(callback)

    def _notify_refresh(self) -> None:
        """通知所有回调刷新UI"""
        for callback in self._refresh_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"[队列] 刷新回调失败: {e}")

    def enqueue(
        self,
        path: str,
        is_anime: bool | None = None,
        is_movie: bool | None = None,
        original_uuid: str | None = None,
        cus_name: str | None = None,
        cus_season_id: int | None = None,
        use_ai: bool | None = None,
        _is_sub_task: bool = False,
    ) -> str:
        """
        添加任务到队列

        Args:
            path: 文件/文件夹路径
            is_anime: 是否为动漫
            is_movie: 是否为电影
            original_uuid: 重试时的原始UUID
            cus_name: 自定义名称（编辑页面）
            cus_season_id: 自定义季度ID（编辑页面）
            use_ai: 是否使用AI（None表示使用全局配置）
            _is_sub_task: 是否为子任务（由父任务拆分出来的）

        Returns:
            任务ID
        """
        task_id = str(uuid.uuid4())
        task = QueuedTask(
            task_id=task_id,
            path=path,
            is_anime=is_anime,
            is_movie=is_movie,
            original_uuid=original_uuid,
            cus_name=cus_name,
            cus_season_id=cus_season_id,
            use_ai=use_ai,
            is_sub_task=_is_sub_task,
            status=TaskStatus.PENDING,
            created_at=datetime.now(),
        )

        self._tasks[task_id] = task
        self._queue.put_nowait(task)

        logger.info(f"[队列] 任务已加入队列: {task_id}, 路径: {path}")

        # 确保worker正在运行
        if not self._workers_running:
            _ = asyncio.create_task(self._start_workers())

        # 通知UI刷新
        self._notify_refresh()

        return task_id

    def get_task_status(self, task_id: str) -> QueuedTask | None:
        """根据任务ID获取任务状态"""
        return self._tasks.get(task_id)

    def get_path_status(self, path: str) -> TaskStatus | None:
        """
        根据路径获取队列状态

        Returns:
            TaskStatus 或 None（不在队列中）
        """
        # 检查正在运行的任务
        for task in self._running_tasks.values():
            if task.path == path:
                return TaskStatus.RUNNING

        # 检查队列中的任务
        for task in self._tasks.values():
            if task.path == path and task.status == TaskStatus.PENDING:
                return TaskStatus.PENDING

        return None

    def get_queue_position(self, path: str) -> int:
        """
        获取路径在队列中的位置

        Returns:
            0 = 正在执行
            1+ = 队列位置
            -1 = 不在队列中
        """
        # 检查是否正在运行
        for task in self._running_tasks.values():
            if task.path == path:
                return 0

        # 获取所有等待中的任务，按创建时间排序
        pending_tasks = [
            t for t in self._tasks.values() if t.status == TaskStatus.PENDING
        ]
        pending_tasks.sort(key=lambda t: t.created_at)

        for i, task in enumerate(pending_tasks):
            if task.path == path:
                return i + 1

        return -1

    def list_active_tasks(self) -> list[QueuedTask]:
        """获取当前队列中待处理/执行中的任务快照。"""
        active_tasks = [
            task
            for task in self._tasks.values()
            if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ]
        active_tasks.sort(
            key=lambda task: (
                task.status != TaskStatus.RUNNING,
                task.created_at,
            )
        )
        return active_tasks

    def is_path_in_queue(self, path: str) -> bool:
        """检查路径是否已在队列中"""
        return self.get_path_status(path) is not None

    async def _start_workers(self) -> None:
        """启动后台worker"""
        if self._workers_running:
            return

        self._workers_running = True
        self._batch_total = 0
        self._batch_success = 0
        self._batch_failed = 0
        self._batch_success_task_ids = []
        self._batch_failed_tasks = []
        max_workers = cm.get_config('queue_max_workers') or 1
        max_workers = max(1, int(max_workers))  # 最少1个

        logger.info(f"[队列] 启动 {max_workers} 个后台处理器")

        # 启动多个worker
        workers = [
            asyncio.create_task(self._worker_loop(i))
            for i in range(max_workers)
        ]
        self._worker_count = max_workers

        # 等待所有worker完成
        _ = await asyncio.gather(*workers)

        # 所有 worker 完成后，触发 Emby 通知
        self._trigger_emby_notification()
        self._trigger_telegram_notification()

        self._workers_running = False
        self._worker_count = 0
        logger.info("[队列] 所有后台处理器已停止")

    async def _worker_loop(self, worker_id: int) -> None:
        """单个worker的循环"""
        logger.info(f"[队列] Worker-{worker_id} 已启动")

        while True:
            try:
                # 等待任务，超时后检查是否还有任务
                task = await asyncio.wait_for(
                    self._queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                # 检查是否还有待处理任务
                if self._queue.empty() and not self._running_tasks:
                    break
                continue

            await self._process_task(task, worker_id)
            self._queue.task_done()

        logger.info(f"[队列] Worker-{worker_id} 已停止")

    async def _process_task(
        self, task: QueuedTask, worker_id: int
    ) -> None:
        """处理单个任务"""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        self._running_tasks[task.task_id] = task

        logger.info(
            f"[队列] Worker-{worker_id} 开始处理任务: {task.task_id}, "
            f"路径: {task.path}"
        )

        # 通知UI刷新（任务开始执行）
        self._notify_refresh()

        try:
            # 在线程池中执行实际处理
            result = await run.io_bound(self._execute_rename, task)

            if isinstance(result, str):
                # 返回字符串表示错误
                task.status = TaskStatus.FAILED
                task.error = result
                logger.warning(
                    f"[队列] 任务失败: {task.task_id}, 错误: {result}"
                )
            else:
                task.status = TaskStatus.COMPLETED
                logger.info(f"[队列] 任务完成: {task.task_id}")

                persisted_task_id = (
                    getattr(task, "original_uuid", None) or task.task_id
                )
                await run.io_bound(self._execute_subtitle_auto_fetch, persisted_task_id)

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"[队列] 任务异常: {task.task_id}, 错误: {e}")

        finally:
            self._record_batch_result(task)
            task.finished_at = datetime.now()
            _ = self._running_tasks.pop(task.task_id, None)

            # 通知UI刷新（任务完成）
            self._notify_refresh()

            # 清理已完成的任务（保留一段时间用于状态显示）
            _ = asyncio.create_task(self._cleanup_task(task.task_id))

    def _execute_rename(self, task: QueuedTask) -> str | bool:
        """执行重命名处理（在线程池中运行）"""
        from ..rename.process import Rename

        return Rename().process(
            Path(task.path),
            task.is_anime,
            task.is_movie,
            task.original_uuid or task.task_id,
            task.cus_name,
            task.cus_season_id,
            _is_sub_task=task.is_sub_task,  # 传递子任务标记
            _enqueue_task=self.enqueue,
        )

    def _execute_subtitle_auto_fetch(self, task_uuid: str) -> None:
        if not bool(cm.get_config("subtitle_auto_fetch_enabled") or False):
            return

        try:
            SubtitleAutoFetcher().process_task(task_uuid)
        except Exception as e:
            logger.error(f"[队列] 字幕自动抓取异常: {task_uuid}, 错误: {e}")

    async def _cleanup_task(self, task_id: str, delay: float = 3.0) -> None:
        """延迟清理已完成的任务"""
        await asyncio.sleep(delay)
        _ = self._tasks.pop(task_id, None)

    def _record_batch_result(self, task: QueuedTask) -> None:
        """记录批次汇总统计"""
        self._batch_total += 1

        if task.status == TaskStatus.COMPLETED:
            self._batch_success += 1
            persisted_task_id = (
                getattr(task, "original_uuid", None) or task.task_id
            )
            self._batch_success_task_ids.append(persisted_task_id)
            return

        if task.status == TaskStatus.FAILED:
            self._batch_failed += 1
            self._batch_failed_tasks.append(
                {
                    "path": task.path,
                    "error": (task.error or "未知错误").strip() or "未知错误",
                }
            )

    def _trigger_emby_notification(self) -> None:
        """触发 Emby 媒体库刷新通知"""
        if self._batch_success <= 0:
            logger.info("[队列] 批次无成功任务，跳过 Emby 刷新")
            return

        try:
            emby = get_emby_notifier()
            if emby.is_available():
                success, message = emby.refresh_library()
                if success:
                    logger.info(f"[队列] Emby 通知成功: {message}")
                else:
                    logger.warning(f"[队列] Emby 通知失败: {message}")
        except Exception as e:
            logger.error(f"[队列] Emby 通知异常: {e}")

    def _trigger_telegram_notification(self) -> None:
        """触发 Telegram 批次汇总通知"""
        try:
            notifier = get_telegram_notifier()
            if not notifier.is_available():
                return

            has_success = self._batch_success > 0
            has_failure = self._batch_failed > 0

            notify_on_success = bool(cm.get_config("telegram_notify_on_success") or False)
            notify_on_failure = bool(cm.get_config("telegram_notify_on_failure") or False)

            should_notify = (
                (has_success and notify_on_success)
                or (has_failure and notify_on_failure)
            )
            if not should_notify:
                logger.info("[队列] Telegram 通知条件未满足，跳过发送")
                return

            task_details = self._collect_task_details()
            record_targets = self._collect_record_targets()
            message = self._build_telegram_message(task_details, record_targets)
            photo_url = self._build_tmdb_poster_url(task_details)

            if photo_url:
                success, reason = notifier.send_photo(photo_url, message)
            else:
                success, reason = notifier.send_message(message)

            if success:
                logger.info(f"[队列] Telegram 通知成功: {reason}")
            else:
                logger.warning(f"[队列] Telegram 通知失败: {reason}")

        except Exception as e:
            logger.error(f"[队列] Telegram 通知异常: {e}")

    def _build_telegram_message(
        self,
        task_details: list[TaskData],
        record_targets: list[Path],
    ) -> str:
        """构建 Telegram caption 文本（入库模板风格）。

        首行入库计数如实区分「实际落地」与「跳过已存在」：
        - landed>0, skipped=0 → 「已入库 X 个文件」
        - landed>0, skipped>0 → 「已入库 X 个文件（跳过 Y 个已存在）」
        - landed=0, skipped>0 → 「跳过入库 Y 个文件」（全跳过，不伪装成已入库）
        - landed=0, skipped=0 → 「已入库 0 个文件」（无操作）
        landed 来自 record 实际落地目标数；skipped 来自各任务 task_data 的
        skipped_file_count 汇总。完全无 record 文件（异常/非 rename 流程）时
        landed 回退 _batch_success 兜底。
        """
        had_record = getattr(self, "_had_any_record_file", False)
        landed = len(record_targets) if had_record else self._batch_success
        skipped = sum(
            int(d.get("skipped_file_count") or 0) for d in task_details
        )
        if landed > 0 and skipped > 0:
            file_line = f"📂 已入库{landed}个文件（跳过{skipped}个已存在）"
        elif landed == 0 and skipped > 0:
            file_line = f"📂 跳过入库{skipped}个文件"
        else:
            file_line = f"📂 已入库{landed}个文件"
        title_year = self._build_title_year(task_details)
        season_episode = self._build_season_episode(
            task_details,
            record_targets,
        )
        category = self._build_category(task_details)
        release_group = self._build_release_group(task_details)
        genre_tag = self._build_genre_tag(task_details)
        resource_term = self._build_resource_term(
            task_details,
            record_targets,
        )
        total_size = self._build_total_size(record_targets)
        err_msg = self._build_error_message()

        lines = [file_line, title_year]

        detail_lines: list[str] = []
        if season_episode:
            detail_lines.append(f"📺 集数： {season_episode}")
        if category:
            detail_lines.append(f"🎭 类别： {category}")
        if release_group:
            detail_lines.append(f"👥 小组： {release_group}")
        if genre_tag:
            detail_lines.append(f"🏷️ 标签： {genre_tag}")
        if resource_term:
            detail_lines.append(f"🌟 质量： {resource_term}")
        detail_lines.append(f"💾 大小： {total_size}")

        subtitle_line = self._build_subtitle_summary(task_details)
        if subtitle_line:
            detail_lines.append(f"📝 字幕： {subtitle_line}")

        message = "\n".join(lines + detail_lines)
        if err_msg:
            message += f"，以下文件处理失败：{err_msg}"
        return message

    def _collect_task_details(self) -> list[TaskData]:
        """收集批次任务详情，用于通知模板渲染。"""
        details: list[TaskData] = []
        for task_id in self._batch_success_task_ids:
            task_data = self._read_task_data(task_id)
            if task_data:
                details.append(task_data)
        return details

    def _collect_record_targets(self) -> list[Path]:
        """收集成功任务的目标文件路径。

        同时记录 self._had_any_record_file：本批成功任务中是否存在 record 文件。
        用于区分两种 record_targets 为空的情形：
          - 有 record 文件但内容为空（全跳过，本次 0 入库）→ 真实 0，不回退
          - 完全无 record 文件（异常/非 rename 流程）→ 回退 _batch_success 兜底
        """
        from ..utils.path import RECORD_PATH

        targets: list[Path] = []
        had_any_record_file = False
        for task_id in self._batch_success_task_ids:
            record_path = RECORD_PATH / f"{task_id}.json"
            if not record_path.exists():
                continue
            had_any_record_file = True
            try:
                with open(record_path, "r", encoding="utf-8") as f:
                    record_data = json.load(f)
                if not isinstance(record_data, dict):
                    continue
                for target in record_data.values():
                    if isinstance(target, str):
                        targets.append(Path(target))
            except Exception as e:
                logger.warning(f"[队列] 读取记录失败: {task_id}, {e}")

        self._had_any_record_file = had_any_record_file
        return targets

    def _read_task_data(self, task_id: str) -> TaskData | None:
        """从 data/task 读取任务详情。"""
        from ..utils.path import TASK_PATH

        file_path = TASK_PATH / f"{task_id}.json"
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning(f"[队列] 读取任务详情失败: {task_id}, {e}")
            return None

    def _build_title_year(
        self,
        task_details: list[TaskData],
    ) -> str:
        if not task_details:
            return "未知标题"

        first = task_details[0]
        name = str(
            first.get("tmdb_name")
            or first.get("name")
            or "未知标题"
        )
        year_value = first.get("tmdb_year")
        if year_value is None:
            year_value = first.get("year")
        if year_value is None:
            year_value = first.get("release_year")
        year = str(year_value or "")
        if year:
            return f"{name} ({year})"
        return name

    def _build_season_episode(
        self,
        task_details: list[TaskData],
        record_targets: list[Path],
    ) -> str:
        """构建集数行，按 season 分组（正片 + 特典分开显示）。

        旧实现把所有 record_targets 的 episode 号混在一起取 min/max，且 season
        只取 min(season_ids)，导致含特典（S00）的样本显示「已入库18个文件」
        但「集数 S01E01-E12」（只正片范围），对不上——18 = 12 正片 + 6 特典。
        现按 (season, episode) 分组：正片季（season>0）在前，特典（season=0）
        在后，每组显示 S{ss}E{min}-E{max}，拼接如「S01E01-E12 + S00E01-E07」。
        """
        season_ids: list[int] = []
        for item in task_details:
            season_id = item.get("season_id")
            if isinstance(season_id, int):
                season_ids.append(season_id)

        if not season_ids:
            return ""

        # 从 record_targets 抽 (season, episode) 对，按 season 分组
        season_episodes: dict[int, list[int]] = {}
        for target in record_targets:
            se = self._extract_season_episode_from_name(target.name)
            if se is not None:
                season, episode = se
                season_episodes.setdefault(season, []).append(episode)

        if season_episodes:
            # 正片季（season>0）升序在前，特典（season=0）在后
            ordered_seasons = sorted(season_episodes.keys(), key=lambda s: (s == 0, s))
            parts: list[str] = []
            for season in ordered_seasons:
                eps = sorted(set(season_episodes[season]))
                min_ep, max_ep = eps[0], eps[-1]
                if min_ep == max_ep:
                    parts.append(f"S{season:02d}E{min_ep:02d}")
                else:
                    parts.append(f"S{season:02d}E{min_ep:02d}-E{max_ep:02d}")
            return " + ".join(parts)

        # fallback：抽不出 (season, episode) 对，退回旧 season_ids 逻辑
        season = min(season_ids)
        if season == 0:
            return "S00"
        if self._batch_success <= 1:
            return f"S{season:02d}E01"
        return f"S{season:02d}E01-E{self._batch_success:02d}"

    def _build_category(
        self,
        task_details: list[TaskData],
    ) -> str:
        if not task_details:
            return ""

        first = task_details[0]
        media_type = str(first.get("tmdb_media_type") or "").lower()
        if media_type == "movie":
            return "电影"
        if media_type in ("tv", "series"):
            return "动漫" if bool(first.get("is_anime")) else "剧集"

        is_movie = bool(first.get("is_movie"))
        is_anime = bool(first.get("is_anime"))

        if is_movie:
            return "电影"
        if is_anime:
            return "动漫"
        return "剧集"

    def _build_release_group(
        self,
        task_details: list[TaskData],
    ) -> str:
        if not task_details:
            return ""

        first = task_details[0]
        saved = str(first.get("release_group") or "").strip()
        if saved:
            return saved

        source_path = str(first.get("path") or "")
        if source_path:
            source_name = Path(source_path).name
            if source_name.startswith("[") and "]" in source_name:
                return source_name.split("]", 1)[0].lstrip("[").strip()

        return ""

    def _build_genre_tag(
        self,
        task_details: list[TaskData],
    ) -> str:
        if not task_details:
            return ""

        first = task_details[0]
        tmdb_genres = first.get("tmdb_genres")
        if not isinstance(tmdb_genres, list):
            return ""

        names: list[str] = []
        for genre in tmdb_genres:
            if not isinstance(genre, dict):
                continue
            genre_name = str(genre.get("name") or "").strip()
            if genre_name:
                names.append(genre_name)

        if not names:
            return ""

        return " / ".join(names[:2])

    def _build_resource_term(
        self,
        task_details: list[TaskData],
        record_targets: list[Path],
    ) -> str:
        if task_details:
            saved = str(task_details[0].get("resource_term") or "").strip()
            if saved:
                return saved

        if record_targets:
            quality_hit: dict[str, int] = {}
            for target in record_targets:
                quality = extract_video_format(target.name)
                if quality:
                    quality_hit[quality] = quality_hit.get(quality, 0) + 1
            if quality_hit:
                return sorted(
                    quality_hit.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )[0][0]

        if not task_details:
            return ""

        source_path = str(task_details[0].get("path") or "")
        if not source_path:
            return ""

        quality = extract_video_format(Path(source_path).name) or ""
        return quality

    def _build_total_size(self, record_targets: list[Path]) -> str:
        total_bytes = 0
        for target in record_targets:
            try:
                if target.is_file():
                    total_bytes += target.stat().st_size
            except Exception:
                continue

        if total_bytes <= 0:
            return "未知"

        gb = total_bytes / (1024 ** 3)
        return f"{gb:.2f} GB"

    def _build_subtitle_summary(
        self,
        task_details: list[TaskData],
    ) -> str:
        if not task_details:
            return ""

        attempted_items = [
            item for item in task_details if item.get("subtitle_fetch_attempted")
        ]
        statuses = [
            str(item.get("subtitle_fetch_status") or "").strip()
            for item in attempted_items
        ]
        if not statuses:
            return "未尝试"

        first_attempted = attempted_items[0]
        if all(status == "success" for status in statuses):
            language = str(first_attempted.get("subtitle_fetch_language") or "").strip()
            return f"自动补字幕成功{f'（{language}）' if language else ''}"

        if all(status == "skipped" for status in statuses):
            reason = str(
                first_attempted.get("subtitle_fetch_error") or "已存在字幕"
            ).strip()
            return f"已跳过（{reason}）"

        if any(status == "success" for status in statuses):
            return "部分任务自动补字幕成功"

        reason = str(first_attempted.get("subtitle_fetch_error") or "未知原因").strip()
        return f"自动补字幕失败（{reason}）"

    def _build_error_message(self) -> str:
        if not self._batch_failed_tasks:
            return ""

        items: list[str] = []
        for failed in self._batch_failed_tasks[:5]:
            name = Path(failed["path"]).name
            error_summary = failed["error"].replace("\n", " ")
            if len(error_summary) > 50:
                error_summary = error_summary[:47] + "..."
            items.append(f"{name}({error_summary})")
        return "；".join(items)

    def _extract_episode_from_name(self, filename: str) -> int | None:
        """从目标文件名中提取 EXX 集数。"""
        patterns = [
            r"\bS\d{1,2}E(\d{1,3})\b",
            r"\bE(\d{1,3})\b",
            r"\bEP(\d{1,3})\b",
            r"[\[\(](\d{1,3})[\]\)]",
            r"第\s*(\d{1,3})\s*[话話集]",
        ]

        for pattern in patterns:
            match = re.search(pattern, filename, re.IGNORECASE)
            if not match:
                continue
            try:
                return int(match.group(1))
            except ValueError:
                continue

        return None

    def _extract_season_episode_from_name(
        self, filename: str
    ) -> tuple[int, int] | None:
        """从目标文件名提取 (season, episode) 对，供按 season 分组显示集数。

        Emby 风格落地文件名形如「xxx - S01E01.mkv」「xxx - S00E07.mkv」，
        优先匹配 S(\\d{1,2})E(\\d{1,3})。匹配不到返回 None（调用方走 fallback）。
        """
        match = re.search(r"\bS(\d{1,2})E(\d{1,3})\b", filename, re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1)), int(match.group(2))
        except ValueError:
            return None

    def _build_tmdb_poster_url(
        self,
        task_details: list[TaskData],
    ) -> str:
        if not task_details:
            return ""

        poster_path = task_details[0].get("poster_path")
        if not poster_path:
            return ""

        poster = str(poster_path).strip()
        if not poster:
            return ""

        if poster.startswith("http://") or poster.startswith("https://"):
            return poster

        if not poster.startswith("/"):
            poster = "/" + poster
        return f"https://image.tmdb.org/t/p/w500{poster}"


def get_queue_manager() -> TaskQueueManager:
    """获取队列管理器单例"""
    return TaskQueueManager()
