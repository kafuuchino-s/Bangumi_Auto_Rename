"""任务 API 路由。

薄封装现有能力，不碰业务逻辑：
- 列表/详情：读 task/record JSON + queue 活跃态（serializers）
- 入队：queue.enqueue + 路径修复（复用 web.py）
- 重试/编辑/删除：文件系统 + queue 操作
- 重跑字幕：SubtitleAutoFetcher
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from ..queue.task_queue import get_queue_manager
from ..queue.task_status import TaskStatus
from ..subtitle.auto_fetch import SubtitleAutoFetcher
from ..utils.path import RECORD_PATH, TASK_PATH
from ..utils.utils import get_task, write_task
from .serializers import build_task_detail, list_task_rows

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _transform_path(path: str) -> str:
    """宿主机→Docker 路径转换 + URL 编码修复（延迟 import 避免循环依赖）。"""
    from ..web import convert_host_path_to_docker, fix_url_encoded_path

    return fix_url_encoded_path(convert_host_path_to_docker(path))


# ----------------------- 请求模型 ----------------------- #
class TaskCreateRequest(BaseModel):
    path: str
    is_anime: bool | None = None
    is_movie: bool | None = None


class TaskEditRequest(BaseModel):
    is_anime: bool | None = None
    name: str | None = None
    season_id: int | None = None
    is_movie: bool | None = None


def _text_to_bool(text: Any) -> bool | None:
    if text is True:
        return True
    if text is False:
        return False
    if text == "是":
        return True
    if text == "否":
        return False
    return None


# ----------------------- 路由 ----------------------- #
@router.get("")
def get_tasks() -> dict[str, Any]:
    """任务列表（合并落盘 + 队列活跃态）。"""
    return {"tasks": list_task_rows()}


@router.get("/stream")
async def tasks_stream() -> StreamingResponse:
    """SSE：每 2 秒推送任务列表快照（供前端实时刷新）。"""

    async def event_generator():
        while True:
            payload = {"tasks": list_task_rows()}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(
        event_generator(), media_type="text/event-stream"
    )


@router.get("/{uuid}")
def get_task_detail(uuid: str) -> dict[str, Any]:
    """单个任务详情（task + record 合并）。"""
    detail = build_task_detail(uuid)
    if not detail.get("found"):
        raise HTTPException(status_code=404, detail="任务不存在")
    return detail


@router.post("")
async def create_task(req: TaskCreateRequest) -> dict[str, Any]:
    """入队新任务（含路径修复/Docker 转换，复用 web.py）。

    async def：enqueue() 内部 asyncio.create_task 懒启动 worker 需要运行中
    的事件循环（对齐 web.py 的 /sendTask，后者本就是 async def）。
    """
    path = _transform_path(req.path)
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {path}")

    queue_mgr = get_queue_manager()
    if queue_mgr.is_path_in_queue(str(p)):
        return {"code": 200, "data": "任务已在队列中", "task_id": None}

    task_id = queue_mgr.enqueue(
        path=str(p),
        is_anime=req.is_anime,
        is_movie=req.is_movie,
    )
    return {"code": 200, "data": "任务已加入队列", "task_id": task_id}


@router.post("/{uuid}/retry")
async def retry_task(uuid: str) -> dict[str, Any]:
    """重试任务：按原参数重新入队，入队成功后再删旧记录。

    必须用 async def：enqueue() 内部用 asyncio.create_task 懒启动 worker，
    需要运行中的事件循环。同步路由跑在 anyio threadpool 线程里无事件循环，
    会抛 RuntimeError('no running event loop') 导致 500——且旧实现先删记录
    再入队，入队失败时旧记录已被删，前端再查就 404「任务不存在」。
    """
    task_data = get_task(uuid)
    if not task_data:
        raise HTTPException(status_code=404, detail="任务数据不存在")

    path = task_data.get("path")
    if not isinstance(path, str) or not path:
        raise HTTPException(status_code=400, detail="任务缺少路径")

    queue_mgr = get_queue_manager()
    if queue_mgr.is_path_in_queue(path):
        raise HTTPException(status_code=409, detail="该任务已在队列中")

    # 先入队，成功后再删旧记录——避免入队失败时丢失任务记录
    task_id = queue_mgr.enqueue(
        path=path,
        is_anime=task_data.get("is_anime"),
        is_movie=task_data.get("is_movie"),
        original_uuid=uuid,
    )

    for p in (TASK_PATH / f"{uuid}.json", RECORD_PATH / f"{uuid}.json"):
        if p.exists():
            p.unlink()

    return {"code": 200, "data": "任务已重新入队", "task_id": task_id}


@router.post("/{uuid}/edit")
async def edit_task(uuid: str, req: TaskEditRequest) -> dict[str, Any]:
    """编辑任务信息后重新入队（对齐 edit_page 逻辑）。

    async def：enqueue() 内部 asyncio.create_task 懒启动 worker 需要运行中
    的事件循环（对齐 /sendTask）。
    """
    task_data = get_task(uuid)
    if not task_data:
        raise HTTPException(status_code=404, detail="任务数据不存在")

    # 合并编辑字段
    updated = dict(task_data)
    if req.is_anime is not None:
        updated["is_anime"] = req.is_anime
    if req.is_movie is not None:
        updated["is_movie"] = req.is_movie
    if req.name is not None:
        updated["name"] = req.name
    if req.season_id is not None:
        updated["season_id"] = req.season_id
    write_task(uuid, updated)

    path = updated.get("path")
    if not isinstance(path, str) or not path:
        raise HTTPException(status_code=400, detail="任务缺少路径")

    queue_mgr = get_queue_manager()
    if queue_mgr.is_path_in_queue(path):
        raise HTTPException(status_code=409, detail="该任务已在队列中")

    task_id = queue_mgr.enqueue(
        path=path,
        is_anime=_text_to_bool(updated.get("is_anime")),
        is_movie=_text_to_bool(updated.get("is_movie")),
        original_uuid=uuid,
        cus_name=updated.get("name"),
        cus_season_id=updated.get("season_id"),
    )
    return {"code": 200, "data": "修改成功，任务已加入队列", "task_id": task_id}


@router.delete("/{uuid}")
def delete_task(uuid: str) -> dict[str, Any]:
    """删除任务记录（task + record）。"""
    removed = 0
    for p in (TASK_PATH / f"{uuid}.json", RECORD_PATH / f"{uuid}.json"):
        if p.exists():
            p.unlink()
            removed += 1
    if removed == 0:
        raise HTTPException(status_code=404, detail="任务记录不存在")
    return {"code": 200, "data": f"已删除 {removed} 个记录"}


@router.post("/{uuid}/refetch-subtitle")
def refetch_subtitle(uuid: str) -> dict[str, Any]:
    """重跑主任务的字幕自动抓取。"""
    task_data = get_task(uuid)
    if not task_data:
        raise HTTPException(status_code=404, detail="任务数据不存在")
    if task_data.get("type") == "subtitle":
        raise HTTPException(status_code=400, detail="字幕导入任务不支持重跑字幕抓取")

    record_path = RECORD_PATH / f"{uuid}.json"
    if not record_path.exists():
        raise HTTPException(status_code=400, detail="主任务缺少入库记录")

    queue_mgr = get_queue_manager()
    path = task_data.get("path")
    if isinstance(path, str) and queue_mgr.is_path_in_queue(path):
        raise HTTPException(status_code=409, detail="任务仍在队列中，暂不能重跑字幕抓取")

    result = SubtitleAutoFetcher().process_task(uuid)
    return {
        "code": 200,
        "status": result.get("status"),
        "reason": result.get("reason"),
        "result": result,
    }
