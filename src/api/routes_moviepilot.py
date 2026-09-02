from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..moviepilot import MoviePilotAPIError, MoviePilotClient
from ..moviepilot.recovery import scan_recovery_candidates, source_evidence
from ..queue.task_queue import get_queue_manager
from ..utils.path import RECORD_PATH, TASK_PATH
from .contract import ok

router = APIRouter(prefix="/moviepilot", tags=["moviepilot"])


def _transform_path(path: str) -> str:
    from ..web import convert_host_path_to_docker, fix_url_encoded_path

    return fix_url_encoded_path(convert_host_path_to_docker(path))


def _scan(limit: int) -> dict[str, Any]:
    queue = get_queue_manager()
    return scan_recovery_candidates(
        MoviePilotClient.configured(),
        path_converter=_transform_path,
        task_path=TASK_PATH,
        record_path=RECORD_PATH,
        is_queued=queue.is_path_in_queue,
        limit=limit,
    )


@router.get("/recovery")
async def get_recovery_candidates(
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    try:
        report = await asyncio.to_thread(_scan, limit)
    except MoviePilotAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ok(report)


@router.post("/recovery/{history_id}/enqueue")
async def enqueue_recovery(history_id: int) -> dict[str, Any]:
    try:
        report = await asyncio.to_thread(_scan, 200)
    except MoviePilotAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    item = next(
        (
            row
            for row in report.get("items", [])
            if row.get("history_id") == history_id
        ),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="MoviePilot 下载历史不存在")
    status = str(item.get("status") or "")
    if status == "downloading":
        raise HTTPException(status_code=409, detail="下载任务尚未完成")
    if status == "processed":
        raise HTTPException(status_code=409, detail="该下载已由 BAR 处理")
    if status == "status_unavailable":
        raise HTTPException(status_code=409, detail="无法确认下载器完成状态")
    if status == "queued":
        raise HTTPException(status_code=409, detail="该路径已在队列中")
    if status != "recoverable":
        raise HTTPException(
            status_code=409,
            detail="下载路径不存在或不含视频",
        )

    local_path = Path(str(item["local_path"]))
    queue = get_queue_manager()
    if queue.is_path_in_queue(str(local_path)):
        raise HTTPException(status_code=409, detail="该路径已在队列中")

    task_id = queue.enqueue(
        path=str(local_path),
        is_anime=None,
        is_movie=None,
        source_evidence=source_evidence(item),
    )
    return ok(
        {"task_id": task_id, "history_id": history_id},
        result="moviepilot_recovery_enqueued",
    )
