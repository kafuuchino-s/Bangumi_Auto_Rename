"""API 总路由聚合。

由 web.py 挂载：``app.include_router(api_router, prefix='/api')``。
"""

from fastapi import APIRouter

from .routes_config import router as config_router
from .routes_dashboard import router as dashboard_router
from .routes_files import router as files_router
from .routes_logs import router as logs_router
from .routes_moviepilot import router as moviepilot_router
from .routes_subtitle import router as subtitle_router
from .routes_tasks import router as tasks_router

api_router = APIRouter()
api_router.include_router(tasks_router)
api_router.include_router(subtitle_router)
api_router.include_router(config_router)
api_router.include_router(dashboard_router)
api_router.include_router(logs_router)
api_router.include_router(files_router)
api_router.include_router(moviepilot_router)

__all__ = ["api_router"]
