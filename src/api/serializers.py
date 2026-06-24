"""API 数据序列化层（纯函数）。

把原本混在 UI（data_table_page 等）里的「数据构造逻辑」抽成纯函数，
UI 与 API 共用，**不碰业务逻辑**。

函数清单：
- ``list_task_rows``：任务列表行（合并 task JSON + queue 活跃态）
- ``list_subtitle_rows``：字幕任务列表行
- ``build_task_detail``：单个任务详情（task + record 合并，人话化）
- ``build_dashboard_stats``：仪表盘统计
- ``mask_secrets``：配置密钥脱敏
- ``read_log_tail``：日志末尾 N 行 + 格式化
"""

from __future__ import annotations

import json
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from ..config.config_manager import cm
from ..queue.task_queue import get_queue_manager
from ..queue.task_status import TaskStatus
from ..utils.path import RECORD_PATH, TASK_PATH, log_path
from ..utils.utils import get_record, get_task

# failure_reason → 人话映射（与 data_table_page 对齐）
FAILURE_REASON_LABELS: dict[str, str] = {
    "invalid_path": "路径无效或不存在",
    "ai_empty_mapping": "AI 未识别出任何可落地的映射",
    "ai_unavailable": "AI 服务不可用（未配置密钥/超时/连接失败）",
    "local_bangumi_case_agent_primary": "Case Agent 映射未通过合同校验",
    "bgm_to_tmdb_product_pipeline_error": "BGM→TMDB 产品链路执行异常",
    "bgm_to_tmdb_bridge_failed": "BGM→TMDB 桥接失败（未能桥接到 TMDB 季集）",
    "bgm_to_tmdb_rename_plan_invalid": "BGM→TMDB 重命名计划非法",
    "bgm_to_tmdb_rename_plan_dry_run": "BGM→TMDB 重命名计划预演未通过",
    "bgm_to_tmdb_no_targetable_files": "无可落地的目标文件",
    "bgm_to_tmdb_transfer_failed": "文件迁移落盘失败（硬链/复制/移动）",
}

CASE_AGENT_STATUS_LABELS: dict[str, str] = {
    "accepted": "已接受（通过合同校验）",
    "fail_closed": "合同不通过（合格失败）",
    "need_confirm": "需人工确认",
    "invalid": "实现/合同错误",
}

PIPELINE_MODE_LABELS: dict[str, str] = {
    "local_bangumi_case_agent_primary": "Local→Bangumi Case Agent",
    "local_bangumi_to_tmdb_product": "Local→Bangumi→TMDB 产品链路",
    "local_bangumi_to_tmdb_product_dry_run": "Local→Bangumi→TMDB 预演",
}


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _format_bool_text(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "自动"


def _queue_status_text(path: str) -> tuple[str, str]:
    """返回 (status, queue_status_text)。"""
    queue_mgr = get_queue_manager()
    qs = queue_mgr.get_path_status(path)
    if qs == TaskStatus.RUNNING:
        return ("处理中", "执行中...")
    if qs == TaskStatus.PENDING:
        position = queue_mgr.get_queue_position(path)
        return ("等待处理", f"队列中 #{position}")
    return ("-", "-")


def list_task_rows() -> list[dict[str, Any]]:
    """任务列表行：合并落盘 task JSON + queue 活跃态。与 create_table 对齐。"""
    queue_mgr = get_queue_manager()
    rows_by_path: dict[str, dict[str, Any]] = {}

    if TASK_PATH.exists():
        sorted_files = sorted(
            TASK_PATH.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
        )
    else:
        sorted_files = []

    for f in sorted_files:
        task_data = _as_mapping(get_task(f.stem))
        if task_data.get("type") == "subtitle":
            continue
        path = _str(task_data.get("path"))
        if not path:
            continue
        error_value = task_data.get("error")
        status = error_value if (isinstance(error_value, str) and error_value) else "成功"
        ai_used = task_data.get("ai_used", task_data.get("use_ai", False))
        _, queue_status_text = _queue_status_text(path)
        rows_by_path[path] = {
            "path": path,
            "name": task_data.get("name", "未知"),
            "uuid": task_data.get("uuid", ""),
            "season": task_data.get("season_id", "-"),
            "status": status,
            "queue_status": queue_status_text,
            "is_anime": task_data.get("is_anime", False),
            "is_movie": task_data.get("is_movie", False),
            "ai_used": "是" if ai_used else "否",
        }

    # 活跃但未落盘的任务（处理中/队列中）
    for task in queue_mgr.list_active_tasks():
        if task.path in rows_by_path:
            continue
        if task.status == TaskStatus.RUNNING:
            queue_status_text = "执行中..."
            status = "处理中"
        else:
            position = queue_mgr.get_queue_position(task.path)
            queue_status_text = f"队列中 #{position}"
            status = "等待处理"
        rows_by_path[task.path] = {
            "path": task.path,
            "name": task.cus_name or Path(task.path).name,
            "uuid": task.original_uuid or task.task_id,
            "season": task.cus_season_id or "-",
            "status": status,
            "queue_status": queue_status_text,
            "is_anime": _format_bool_text(task.is_anime),
            "is_movie": _format_bool_text(task.is_movie),
            "ai_used": "待处理",
        }

    rows = list(rows_by_path.values())
    for index, row in enumerate(rows):
        row["id"] = index
    return rows


def list_subtitle_rows() -> list[dict[str, Any]]:
    """字幕任务列表行。与 create_subtitle_table 对齐。"""
    rows: list[dict[str, Any]] = []
    if not TASK_PATH.exists():
        return rows
    sorted_files = sorted(
        TASK_PATH.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
    )
    for index, f in enumerate(sorted_files):
        task_data = _as_mapping(get_task(f.stem))
        if task_data.get("type") != "subtitle":
            continue
        status = "成功" if task_data.get("status") == "success" else "失败"
        archive_name = Path(_str(task_data.get("archive_path"))).name
        sync_summary = _as_mapping(task_data.get("sync_summary"))
        if sync_summary.get("enabled"):
            sync_text = (
                f"{sync_summary.get('success', 0)}/"
                f"{sync_summary.get('attempted', 0)}, "
                f"回退{sync_summary.get('fallback', 0)}"
            )
        else:
            sync_text = "-"
        rows.append({
            "id": index,
            "archive": archive_name,
            "archive_path": _str(task_data.get("archive_path")),
            "matched_task": _str(task_data.get("matched_task")) or "-",
            "matched_count": f"{task_data.get('matched_count', 0)}/{task_data.get('total_subtitles', 0)}",
            "sync": sync_text,
            "status": status,
            "uuid": _str(task_data.get("uuid")),
        })
    return rows


def build_task_detail(uuid: str) -> dict[str, Any]:
    """单个任务详情：task + record 合并，人话化字段。"""
    task_data = get_task(uuid)
    # get_task 对不存在的 uuid 返回 {}（空 dict），视为未找到
    if not task_data:
        return {"found": False, "uuid": uuid}
    task_data = _as_mapping(task_data)
    record_data = _as_mapping(get_record(uuid) or {})

    failure_reason = _str(task_data.get("failure_reason"))
    case_agent_status = _str(
        task_data.get("case_agent_status") or record_data.get("case_agent_status")
    )
    mappings = record_data.get("mappings")
    return {
        "found": True,
        "uuid": uuid,
        "basic": {
            "path": _str(task_data.get("path")),
            "name": _str(task_data.get("name")) or "未知",
            "season_id": task_data.get("season_id", "-"),
            "is_anime": _format_bool_text(task_data.get("is_anime")),
            "is_movie": _format_bool_text(task_data.get("is_movie")),
        },
        "failure": {
            "reason": failure_reason,
            "reason_label": FAILURE_REASON_LABELS.get(failure_reason, failure_reason),
            "error": _str(task_data.get("error")),
        },
        "ai": {
            "ai_used": bool(task_data.get("ai_used")),
            "ai_attempted": bool(task_data.get("ai_attempted")),
            "ai_confidence": _str(task_data.get("ai_confidence")) or "-",
            "pipeline_mode": _str(task_data.get("pipeline_mode")) or "-",
            "pipeline_mode_label": PIPELINE_MODE_LABELS.get(
                _str(task_data.get("pipeline_mode")), _str(task_data.get("pipeline_mode"))
            ),
        },
        "case_agent": {
            "status": case_agent_status,
            "status_label": CASE_AGENT_STATUS_LABELS.get(
                case_agent_status, case_agent_status
            ),
            "product_result_kind": _str(record_data.get("product_result_kind")),
        },
        "landing": {
            "target_dir": _str(record_data.get("target_dir")),
            "mapping_count": len(mappings) if isinstance(mappings, list) else 0,
            "mappings": mappings if isinstance(mappings, list) else [],
        },
    }


def build_dashboard_stats() -> dict[str, Any]:
    """仪表盘统计：队列状态 + 今日统计。与 create_dashboard 对齐。"""
    queue_mgr = get_queue_manager()
    active_tasks = queue_mgr.list_active_tasks()
    running = sum(1 for t in active_tasks if t.status == TaskStatus.RUNNING)
    pending = sum(1 for t in active_tasks if t.status != TaskStatus.RUNNING)

    today = date.today()
    today_total = 0
    today_success = 0
    today_failed = 0
    if TASK_PATH.exists():
        for f in TASK_PATH.iterdir():
            try:
                mtime = date.fromtimestamp(f.stat().st_mtime)
            except OSError:
                continue
            if mtime != today:
                continue
            td = get_task(f.stem)
            if not isinstance(td, Mapping) or td.get("type") == "subtitle":
                continue
            today_total += 1
            err = td.get("error")
            if isinstance(err, str) and err:
                today_failed += 1
            else:
                today_success += 1

    success_rate = (
        round(today_success / today_total * 100) if today_total > 0 else None
    )
    return {
        "running": running,
        "pending": pending,
        "today_success": today_success,
        "today_failed": today_failed,
        "today_total": today_total,
        "success_rate": success_rate,
    }


# --------------------------------------------------------------------------- #
# 配置脱敏
# --------------------------------------------------------------------------- #
_SECRET_KEY_HINTS = ("api_key", "bot_token", "password", "secret", "token")


def _is_secret_key(key: str) -> bool:
    return any(h in key for h in _SECRET_KEY_HINTS)


def mask_secrets(config: Mapping[str, Any]) -> dict[str, Any]:
    """返回配置副本，密钥类字段用星号脱敏。"""
    out: dict[str, Any] = {}
    for k, v in config.items():
        if _is_secret_key(k) and isinstance(v, str) and v:
            out[k] = "*" * len(v)
        else:
            out[k] = v
    return out


def get_all_config() -> dict[str, Any]:
    """读取全部配置（路径转换生效）。"""
    return {key: cm.get_config(key) for key in cm.config}


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
def format_log_line(line: str) -> str:
    """JSON 结构化日志行 → 可读文本 [时间] [级别] 事件。"""
    line = line.rstrip("\n")
    if not line or not line.lstrip().startswith("{"):
        return line
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return line
    if not isinstance(obj, dict):
        return line
    ts = str(obj.get("timestamp") or "")[11:19]
    level = str(obj.get("level") or "").upper()
    event = str(obj.get("event") or "")
    parts = []
    if ts:
        parts.append(f"[{ts}]")
    if level:
        parts.append(f"[{level}]")
    parts.append(event)
    return " ".join(parts)


def read_log_tail(n: int = 200) -> list[str]:
    """读取日志末尾 n 行并格式化。"""
    path = log_path
    if not path.exists() or not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [format_log_line(line) for line in deque(f, maxlen=n)]
    except OSError:
        return []
