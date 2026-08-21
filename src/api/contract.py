"""Stable, language-independent HTTP API contract helpers.

The UI owns wording and formatting.  This module deliberately keeps the
wire format small: successful responses carry ``data`` and optional stable
``result`` codes; failures carry one ``error`` object.
"""

from __future__ import annotations

import re
import hashlib
from collections.abc import Mapping
from typing import Any


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        *,
        params: Mapping[str, Any] | None = None,
        message: str = "",
        status_code: int = 400,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.params = dict(params or {})
        self.message = message or code
        self.status_code = status_code


def ok(data: Any = None, *, result: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"data": data}
    if result is not None:
        payload["result"] = result
    return payload


def failure(
    code: str,
    *,
    params: Mapping[str, Any] | None = None,
    message: str = "",
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "params": dict(params or {}),
            "message": message or code,
        }
    }


_STATUS_ALIASES = {
    "pending": "pending",
    "running": "running",
    "completed": "completed",
    "success": "completed",
    "failed": "failed",
    "error": "failed",
    "等待处理": "pending",
    "处理中": "running",
    "执行中...": "running",
    "成功": "completed",
    "失败": "failed",
}


def canonical_status(value: object, *, has_error: bool = False) -> str:
    if has_error:
        return "failed"
    text = str(value or "").strip()
    mapped = _STATUS_ALIASES.get(text.lower(), _STATUS_ALIASES.get(text, text or "completed"))
    return mapped if mapped in {"pending", "running", "completed", "failed"} else "pending"


def _bool_or_none(value: object) -> bool | None:
    if value is True or value is False:
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on", "是", "鏄"}:
            return True
        if text in {"false", "0", "no", "off", "否", "鍚"}:
            return False
    return None


def _queue_position(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    match = re.search(r"#\s*(\d+)", text)
    return int(match.group(1)) if match else None


def canonical_task_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        error = row.get("failure_reason") or row.get("failure_reason_code")
        out.append(
            {
                "id": row.get("id"),
                "path": row.get("path", ""),
                "name": row.get("name"),
                "uuid": row.get("uuid", ""),
                "season": row.get("season"),
                "status": canonical_status(
                    row.get("status"), has_error=bool(error)
                ),
                "failure_reason": error or None,
                "queue_position": _queue_position(row.get("queue_position", row.get("queue_status"))),
                "is_anime": _bool_or_none(row.get("is_anime")),
                "is_movie": _bool_or_none(row.get("is_movie")),
                "ai_used": _bool_or_none(row.get("ai_used")),
            }
        )
    return out


def canonical_subtitle_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        matched = row.get("matched_count", 0)
        total = row.get("total_count", row.get("total_subtitles", 0))
        if isinstance(matched, str) and "/" in matched:
            left, right = matched.split("/", 1)
            try:
                matched, total = int(left), int(right)
            except ValueError:
                matched, total = 0, 0
        sync = row.get("sync")
        if not isinstance(sync, Mapping):
            sync = {"enabled": False, "success": 0, "attempted": 0, "fallback": 0}
        out.append(
            {
                "id": row.get("id"),
                "archive": row.get("archive", ""),
                "archive_path": row.get("archive_path", ""),
                "matched_task": row.get("matched_task") or None,
                "matched_count": int(matched or 0),
                "total_count": int(total or 0),
                "sync": {
                    "enabled": bool(sync.get("enabled")),
                    "success": int(sync.get("success", 0) or 0),
                    "attempted": int(sync.get("attempted", 0) or 0),
                    "fallback": int(sync.get("fallback", 0) or 0),
                },
                "status": canonical_status(row.get("status")),
                "uuid": row.get("uuid", ""),
            }
        )
    return out


def canonical_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Drop presentation labels recursively while preserving raw evidence."""

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: clean(item)
                for key, item in value.items()
                if not key.endswith("_label") and key not in {"total_size", "bgm", "tmdb"}
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    result = clean(detail)
    if not isinstance(result, dict):
        return {}
    if "status" in result:
        result["status"] = canonical_status(result["status"])
    for key in ("failure", "case_agent", "subtitle_fetch"):
        section = result.get(key)
        if isinstance(section, dict) and "status" in section:
            section["status"] = canonical_status(section["status"])
    return result


def canonical_field_spec(entries: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Expose only structural IDs; labels/help/options belong to the locale."""

    def stable_id(value: object) -> str:
        original = str(value or "").strip()
        text = original.lower()
        aliases = {
            "链接": "link", "复制": "copy", "剪切": "move", "移动": "move",
            "覆盖": "overwrite", "跳过": "skip", "拒绝": "skip",
            "是": "true", "否": "false", "启用": "enabled", "禁用": "disabled", "自动": "auto",
            "ai 识别": "ai_recognition", "ai 高级路由": "ai_routing",
            "bgm→tmdb 产品链路": "bgm_tmdb_pipeline", "webhook 过滤与分类": "webhook_filters",
            "传输与覆盖": "transfer", "元数据缓存": "metadata_cache", "媒体库路径": "media_paths",
            "重命名标题": "rename_titles", "字幕对齐（ffsubsync）": "subtitle_sync", "字幕自动抓取": "subtitle_fetch",
            "抓取高级": "subtitle_fetch_advanced", "运行时": "runtime",
            "通知：emby": "notify_emby", "通知：telegram": "notify_telegram", "输出路径": "output_paths",
        }
        if text in aliases:
            return aliases[text]
        if original.isascii() and re.fullmatch(r"[A-Za-z0-9_.-]+", original):
            return original
        text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
        if text:
            return text
        raw = str(value or "default").encode("utf-8")
        return "id_" + hashlib.sha1(raw).hexdigest()[:10]

    out: list[dict[str, Any]] = []
    for entry in entries:
        item = {
            key: entry[key]
            for key in (
                "key",
                "control",
                "level",
                "tab",
                "select_mode",
                "min",
                "max",
                "step",
                "bool_toggle",
            )
            if key in entry
        }
        item["group"] = stable_id(entry.get("group"))
        if entry.get("subgroup"):
            item["subgroup"] = stable_id(entry.get("subgroup"))
        if isinstance(entry.get("options"), list):
            item["options"] = [stable_id(option) for option in entry["options"]]
        out.append(item)
    return out
