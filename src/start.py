import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parents[2]))
__package__ = 'Bangumi_Auto_Rename.src'

from Bangumi_Auto_Rename.src.web import ui  # noqa: E402 # type: ignore

from .logger import logger  # noqa: E402

if __name__ == '__mp_main__':
    logger.info('程序启动中...')

ui.run(
    port=5888,
    storage_secret='KEY233WuyiDay',
    title='番剧自动重命名',
    favicon='🔥',
    reload=True,
)
