"""REST API 层（保留 Python 后端 + 供新前端调用的薄封装）。

设计原则：
- 只薄封装现有能力（queue / config / get_task / get_record / 通知测试 / 日志 / 文件浏览），
  **不碰业务逻辑**（rename / subtitle / case_agent / bgm_to_tmdb 内部零改动）。
- 数据构造逻辑抽到 ``serializers.py`` 纯函数，UI 与 API 共用。
- 路由按域分组（routes_tasks / routes_config / ...），由 ``router.py`` 聚合后挂到 web.py 的 app。

挂载：``app.include_router(api_router, prefix='/api')``。
"""

from .router import api_router

__all__ = ["api_router"]
