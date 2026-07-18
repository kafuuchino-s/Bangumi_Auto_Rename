"""字幕 API 路由。

- 列表：字幕任务行（serializers）
- 导入：multipart 上传字幕压缩包 → SubtitleProcessor.process
- 删除/重试/清压缩包：文件系统 + processor
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File

from ..subtitle.processor import SubtitleProcessor
from ..utils.path import SUBTITLE_UPLOAD_PATH, TASK_PATH
from ..utils.utils import get_task
from .serializers import list_subtitle_rows
from .contract import canonical_subtitle_rows, ok

router = APIRouter(prefix="/subtitle", tags=["subtitle"])

SUPPORTED_EXTENSIONS = {".zip", ".rar", ".ass", ".ssa", ".srt", ".sub", ".vtt"}


@router.get("/tasks")
def get_subtitle_tasks() -> dict[str, Any]:
    """字幕任务列表。"""
    return ok({"tasks": canonical_subtitle_rows(list_subtitle_rows())})


@router.post("/import")
async def import_subtitle(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传并处理字幕压缩包。"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="不支持的文件格式（ZIP/RAR/ASS/SRT 等）",
        )

    SUBTITLE_UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    upload_path = SUBTITLE_UPLOAD_PATH / filename
    content = await file.read()
    with open(upload_path, "wb") as f:
        f.write(content)

    processor = SubtitleProcessor()
    result = await asyncio.get_event_loop().run_in_executor(
        None, processor.process, upload_path
    )
    return ok(result, result="subtitle_imported")


@router.delete("/{uuid}")
def delete_subtitle(uuid: str) -> dict[str, Any]:
    """删除字幕任务记录。"""
    p = TASK_PATH / f"{uuid}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="字幕任务记录不存在")
    p.unlink()
    return ok({}, result="subtitle_deleted")


@router.delete("/{uuid}/archive")
def clean_subtitle_archive(uuid: str) -> dict[str, Any]:
    """清除字幕压缩包文件。"""
    task_data = get_task(uuid)
    if not isinstance(task_data, dict):
        raise HTTPException(status_code=404, detail="任务数据不存在")
    archive_path = Path(str(task_data.get("archive_path", "")))
    if not archive_path.exists():
        raise HTTPException(status_code=404, detail="压缩包已不存在")
    archive_path.unlink()
    return ok({}, result="subtitle_archive_deleted")


@router.post("/{uuid}/retry")
async def retry_subtitle(uuid: str) -> dict[str, Any]:
    """重试字幕任务（重新处理压缩包）。"""
    task_data = get_task(uuid)
    if not isinstance(task_data, dict):
        raise HTTPException(status_code=404, detail="任务数据不存在")
    archive_path = Path(str(task_data.get("archive_path", "")))
    if not archive_path.exists():
        raise HTTPException(status_code=404, detail=f"压缩包不存在: {archive_path.name}")

    # 删旧记录
    old = TASK_PATH / f"{uuid}.json"
    if old.exists():
        old.unlink()

    processor = SubtitleProcessor()
    result = await asyncio.get_event_loop().run_in_executor(
        None, processor.process, archive_path
    )
    return ok(result, result="subtitle_retried")
