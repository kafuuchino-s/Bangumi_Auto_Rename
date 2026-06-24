"""文件浏览 API 路由（目录列表，供前端文件选择器用）。"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..config.config_manager import cm

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/browse")
def browse(
    path: str = Query(...),
    show_hidden: bool = Query(False),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """列出指定目录下的子项（目录优先 + 名字序），支持搜索与分页。

    - path 为空时返回默认起始目录（Windows 盘符 / Docker 挂载根）。
    - search：对子项 name 做大小写不敏感包含过滤；``..`` 父项不受影响。
    - page/limit：分页（``..`` 父项不计入 total/分页，始终置顶返回）。
    """
    if not path:
        path = _default_root()
    p = Path(path).expanduser()
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {path}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {path}")

    try:
        items = list(p.glob("*"))
    except (OSError, PermissionError):
        raise HTTPException(status_code=403, detail=f"无权限访问: {path}")

    if not show_hidden:
        items = [i for i in items if not i.name.startswith(".")]
    # 搜索过滤（大小写不敏感包含）
    if search:
        s = search.lower()
        items = [i for i in items if s in i.name.lower()]
    # 排序：目录优先 + 名字序
    items.sort(key=lambda x: x.name.lower())
    items.sort(key=lambda x: not x.is_dir())

    total = len(items)
    total_pages = max(1, (total + limit - 1) // limit)
    page = min(page, total_pages)
    start = (page - 1) * limit
    page_items = items[start : start + limit]

    result = [
        {"name": i.name, "path": str(i), "is_dir": i.is_dir()}
        for i in page_items
    ]
    # 上级目录（不计入 total/分页，始终置顶）
    parent_path = str(p.parent) if p != p.parent else None
    if parent_path is not None:
        result.insert(0, {"name": "..", "path": parent_path, "is_dir": True})
    return {
        "current": str(p),
        "parent": parent_path,
        "items": result,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


@router.get("/drives")
def list_drives() -> dict[str, Any]:
    """列出可选盘符 / 挂载点（供前端切换）。"""
    drives: list[str] = []
    system = platform.system()
    if system == "Windows":
        try:
            import win32api

            drives = win32api.GetLogicalDriveStrings().split("\000")[:-1]
        except Exception:
            drives = []
    elif system == "Darwin":
        import os

        drives = [
            d for d in os.listdir("/Volumes")
            if os.path.isdir(os.path.join("/Volumes", d))
        ]
        drives = [f"/Volumes/{d}" for d in drives]
    elif system == "Linux":
        docker_path = cm.get_config("docker_mnt") or "/media"
        import os

        try:
            drives = [
                os.path.join(docker_path, d)
                for d in os.listdir(docker_path)
                if os.path.isdir(os.path.join(docker_path, d))
            ]
        except OSError:
            drives = []
    return {"drives": drives, "system": system}


def _default_root() -> str:
    """默认起始目录。"""
    system = platform.system()
    if system == "Windows":
        try:
            import win32api

            ds = win32api.GetLogicalDriveStrings().split("\000")[:-1]
            return ds[0] if ds else "C:\\"
        except Exception:
            return "C:\\"
    return str(Path("~").expanduser())
