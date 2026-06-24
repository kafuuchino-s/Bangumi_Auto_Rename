"""仪表盘 API 路由（统计 + SSE 队列实时流）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from .serializers import build_dashboard_stats, list_task_rows

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard() -> dict[str, Any]:
    """仪表盘统计快照。"""
    return build_dashboard_stats()


@router.get("/stream")
async def dashboard_stream() -> StreamingResponse:
    """SSE：每 2 秒推送仪表盘统计 + 任务列表摘要。"""

    async def event_generator():
        while True:
            payload = {
                "stats": build_dashboard_stats(),
                "task_count": len(list_task_rows()),
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
