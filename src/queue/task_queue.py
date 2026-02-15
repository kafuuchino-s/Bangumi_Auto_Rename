"""任务队列管理器 - 单例模式"""
import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from nicegui import run

from ..config.config_manager import cm
from ..logger import logger
from ..notification.emby_notify import get_emby_notifier
from ..rename.process import Rename
from .task_status import QueuedTask, TaskStatus


class TaskQueueManager:
    """任务队列管理器（单例）"""

    _instance: Optional['TaskQueueManager'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._queue: asyncio.Queue[QueuedTask] = asyncio.Queue()
        self._tasks: Dict[str, QueuedTask] = {}  # task_id -> QueuedTask
        self._running_tasks: Dict[str, QueuedTask] = {}  # 正在运行的任务
        self._workers_running = False
        self._worker_count = 0
        self._refresh_callbacks: List[Callable] = []
        self._initialized = True

        logger.info("[队列] 任务队列管理器已初始化")

    def add_refresh_callback(self, callback: Callable) -> None:
        """注册UI刷新回调"""
        if callback not in self._refresh_callbacks:
            self._refresh_callbacks.append(callback)

    def remove_refresh_callback(self, callback: Callable) -> None:
        """移除UI刷新回调"""
        if callback in self._refresh_callbacks:
            self._refresh_callbacks.remove(callback)

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
        is_anime: Optional[bool] = None,
        is_movie: Optional[bool] = None,
        original_uuid: Optional[str] = None,
        cus_name: Optional[str] = None,
        cus_season_id: Optional[int] = None,
        use_ai: Optional[bool] = None,
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
            asyncio.create_task(self._start_workers())

        # 通知UI刷新
        self._notify_refresh()

        return task_id

    def get_task_status(self, task_id: str) -> Optional[QueuedTask]:
        """根据任务ID获取任务状态"""
        return self._tasks.get(task_id)

    def get_path_status(self, path: str) -> Optional[TaskStatus]:
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

    def get_queue_size(self) -> int:
        """获取队列中等待的任务数量"""
        return sum(
            1 for t in self._tasks.values() if t.status == TaskStatus.PENDING
        )

    def get_running_count(self) -> int:
        """获取正在运行的任务数量"""
        return len(self._running_tasks)

    def is_path_in_queue(self, path: str) -> bool:
        """检查路径是否已在队列中"""
        return self.get_path_status(path) is not None

    async def _start_workers(self) -> None:
        """启动后台worker"""
        if self._workers_running:
            return

        self._workers_running = True
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
        await asyncio.gather(*workers)

        # 所有 worker 完成后，触发 Emby 通知
        self._trigger_emby_notification()

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
            f"[队列] Worker-{worker_id} 开始处理任务: "
            f"{task.task_id}, 路径: {task.path}"
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

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"[队列] 任务异常: {task.task_id}, 错误: {e}")

        finally:
            task.finished_at = datetime.now()
            self._running_tasks.pop(task.task_id, None)

            # 通知UI刷新（任务完成）
            self._notify_refresh()

            # 清理已完成的任务（保留一段时间用于状态显示）
            asyncio.create_task(self._cleanup_task(task.task_id))

    def _execute_rename(self, task: QueuedTask):
        """执行重命名处理（在线程池中运行）"""
        # 处理use_ai设置
        original_ai_enabled = None
        if task.use_ai is False:
            # 临时禁用AI
            original_ai_enabled = cm.get_config('ai_enabled')
            cm.set_config('ai_enabled', False)

        try:
            return Rename().process(
                Path(task.path),
                task.is_anime,
                task.is_movie,
                task.original_uuid,
                task.cus_name,
                task.cus_season_id,
                _is_sub_task=task.is_sub_task,  # 传递子任务标记
            )
        finally:
            if original_ai_enabled is not None:
                # 恢复AI设置
                cm.set_config('ai_enabled', original_ai_enabled)

    async def _cleanup_task(self, task_id: str, delay: float = 3.0) -> None:
        """延迟清理已完成的任务"""
        await asyncio.sleep(delay)
        self._tasks.pop(task_id, None)

    def clear_completed(self) -> None:
        """清理所有已完成和失败的任务"""
        to_remove = [
            task_id
            for task_id, task in self._tasks.items()
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
        ]
        for task_id in to_remove:
            del self._tasks[task_id]

    def _trigger_emby_notification(self) -> None:
        """触发 Emby 媒体库刷新通知"""
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


def get_queue_manager() -> TaskQueueManager:
    """获取队列管理器单例"""
    return TaskQueueManager()
