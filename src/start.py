import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parents[2]))

import uvicorn  # noqa: E402

from .logger import logger  # noqa: E402
from .utils.metadata_cache import gc_expired, migrate_legacy_if_needed  # noqa: E402
from .web import app  # noqa: E402

if __name__ == '__main__' or __name__ == '__mp_main__':
    logger.info('程序启动中...')
    # 首启迁移旧碎 JSON 缓存树 → diskcache；每次启动顺带 gc 过期条目。
    # off 模式内部跳过；失败不阻断启动。
    migrate_legacy_if_needed()
    try:
        gc_expired()
    except Exception:
        logger.exception('metadata cache 启动 gc 失败')

# 纯 FastAPI + uvicorn 启动（已移除 NiceGUI）
if __name__ == '__main__' or __name__ == '__mp_main__':
    uvicorn.run(
        app,
        host='0.0.0.0',
        port=5999,
        log_config=None,
    )
