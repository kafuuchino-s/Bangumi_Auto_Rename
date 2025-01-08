from pathlib import Path
from typing import Optional, Sequence

from nicegui import run

from ..logger import logger
from ..element.red import notify
from ..rename.process import Rename
from ..pages.data_table_page import create_table
from ..component.local_file_picker import local_file_picker


async def pick_file() -> None:
    result: Optional[Sequence[str]] = await local_file_picker(
        '~',
        multiple=True,
    )
    if result is None:
        return notify('取消添加任务！')
    for p in result:
        logger.info(f'[开始任务] 选择了 {result}')
        data = await run.io_bound(Rename().process, Path(p))
        create_table.refresh()
        if isinstance(data, str):
            notify(data)
        else:
            notify(f'开始任务 {result}！')
