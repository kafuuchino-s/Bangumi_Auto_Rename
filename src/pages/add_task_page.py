from typing import Optional, Sequence

from nicegui import ui

from ..element.red import RedButton, RedToogle, notify
from ..logger import logger
from ..queue.task_queue import get_queue_manager
from ..component.local_file_picker import local_file_picker


async def pick_file() -> None:
    result: Optional[Sequence[str]] = await local_file_picker(
        '~',
        multiple=True,
    )
    if result is None:
        return notify('取消添加任务！')

    queue_mgr = get_queue_manager()
    added_count = 0

    for p in result:
        logger.info(f'[开始任务] 选择了 {p}')

        # 检查是否已在队列中
        if queue_mgr.is_path_in_queue(p):
            notify(f'路径已在队列中: {p}')
            continue

        # 加入队列，is_anime=None 表示自动判断
        queue_mgr.enqueue(
            path=p,
            is_anime=None,
        )
        added_count += 1

    # 队列会自动通知UI刷新
    if added_count > 0:
        notify(f'已将 {added_count} 个任务加入队列！')
    else:
        notify('没有新任务加入队列')
