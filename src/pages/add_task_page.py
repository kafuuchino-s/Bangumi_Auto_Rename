from pathlib import Path
from typing import Sequence

from nicegui import ui, run

from ..logger import logger
from ..rename.process import Rename
from ..component.local_file_picker import local_file_picker


async def pick_file() -> None:
    result = await local_file_picker('~', multiple=True)
    if isinstance(result, Sequence):
        result = result[0]

    logger.info(f'[开始任务] 选择了 {result}')
    data = await run.io_bound(Rename().process, Path(result))
    if isinstance(data, str):
        ui.notify(data)
    else:
        ui.notify(f'开始任务 {result}！')
