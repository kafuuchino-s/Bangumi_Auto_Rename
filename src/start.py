import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parents[2]))

import uvicorn  # noqa: E402

from .logger import logger  # noqa: E402
from .web import app  # noqa: E402

if __name__ == '__main__' or __name__ == '__mp_main__':
    logger.info('程序启动中...')

# 纯 FastAPI + uvicorn 启动（已移除 NiceGUI）
if __name__ == '__main__' or __name__ == '__mp_main__':
    uvicorn.run(
        app,
        host='0.0.0.0',
        port=5999,
        log_config=None,
    )
