"""日志 API 路由（末尾 tail + SSE 流）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from ..utils.path import log_path
from .serializers import read_log_tail
from .contract import ok

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/tail")
def get_log_tail(n: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """日志末尾 n 行（已格式化）。"""
    lines = read_log_tail(n)
    return ok({"lines": lines, "count": len(lines), "file": log_path.name})


@router.get("/stream")
async def log_stream() -> StreamingResponse:
    """SSE：每 2 秒推送日志末尾增量行。"""

    async def event_generator():
        last_count = 0
        while True:
            lines = read_log_tail(200)
            if lines:
                # 增量：只推新行；若行数变少（轮转）则全量
                if last_count > len(lines):
                    new_lines = lines
                else:
                    new_lines = lines[last_count:]
                last_count = len(lines)
                for line in new_lines:
                    yield f"data: {json.dumps(line, ensure_ascii=False)}\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
