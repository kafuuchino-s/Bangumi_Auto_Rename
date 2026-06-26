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

SUBTITLE_FETCH_STATUS_LABELS: dict[str, str] = {
    "success": "成功",
    "failed": "失败",
    "need_confirm": "需人工确认",
    "skipped": "未抓取",
}

# TMDB 媒体类型人话化（详情页展示用，is_anime/is_movie 仅内部落地路由用，不再展示）。
TMDB_MEDIA_TYPE_LABELS: dict[str, str] = {
    "tv": "剧集 (TV)",
    "movie": "电影",
}


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _int_or_zero(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _nested_status(case_agent_result: object) -> str:
    """从 case_agent_result.{status,snapshot.status} 取状态（新链路口径）。"""
    mapping = _as_mapping(case_agent_result)
    return _str(mapping.get("status") or _as_mapping(mapping.get("snapshot")).get("status"))


def _nested_snapshot_kind(case_agent_result: object) -> str:
    """从 case_agent_result.snapshot.product_result_kind 取产品结果类型。"""
    mapping = _as_mapping(case_agent_result)
    return _str(_as_mapping(mapping.get("snapshot")).get("product_result_kind"))


def _load_subtitle_fetch_child(uuid: str) -> Mapping[str, object]:
    """读 {uuid}.subtitle_fetch.json 子任务文件（auto_fetch 附属结果）。

    auto_fetch 是重命名任务批次收尾时触发的附属流程，结果挂在主任务下，
    文件名带 .subtitle_fetch 后缀。配对统计（matched/missing/unmatched/
    no_target）只在这个子任务文件里，主 task JSON 顶层没有。
    """
    path = TASK_PATH / f"{uuid}.subtitle_fetch.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _as_mapping(data)
    except (OSError, json.JSONDecodeError):
        return {}


def _build_subtitle_fetch_section(
    task_data: Mapping[str, object], child: Mapping[str, object]
) -> dict[str, Any] | None:
    """构造字幕自动抓取区块。仅在任务实际触发过 auto_fetch 时返回非 None。"""
    attempted = bool(task_data.get("subtitle_fetch_attempted"))
    # 没触发过 auto_fetch，且子任务文件也不存在 → 不展示该区块。
    if not attempted and not child:
        return None
    status = _str(task_data.get("subtitle_fetch_status"))
    case_agent_status = _str(
        task_data.get("subtitle_fetch_case_agent_status")
        or child.get("case_agent_status")
    )
    failure_reason = _str(task_data.get("subtitle_fetch_failure_reason"))
    provider = _str(task_data.get("subtitle_fetch_provider"))
    # 配对统计优先取子任务文件（video 维度），回退主 task 顶层（多为 None）。
    missing = _int_or_zero(
        child.get("missing_video_count")
        if child
        else task_data.get("subtitle_fetch_missing_video_count")
    )
    matched = _int_or_zero(child.get("matched_count") if child else 0)
    unmatched = len(child.get("unmatched") or []) if child else 0
    no_target = len(child.get("no_target_videos") or []) if child else 0
    selections = _int_or_zero(child.get("selections_count") if child else 0)
    return {
        "status": status,
        "status_label": SUBTITLE_FETCH_STATUS_LABELS.get(status, status),
        "case_agent_status": case_agent_status,
        "case_agent_status_label": CASE_AGENT_STATUS_LABELS.get(
            case_agent_status, case_agent_status
        ),
        "provider": provider,
        "failure_reason": failure_reason,
        "missing_video_count": missing,
        "matched_count": matched,
        "unmatched_count": unmatched,
        "no_target_count": no_target,
        "selections_count": selections,
    }


def _format_bool_text(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "自动"


# ---- 任务详情：BGM/TMDB 条目 + 映射明细 ----

def _compact_range(nums: list[int]) -> str:
    """把离散集号压成可读范围，如 [1,2,3,10,11] → '1-3, 10-11'。"""
    nums = sorted(set(n for n in nums if n is not None))
    if not nums:
        return ""
    parts: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(parts)


def _stat_total_size_bytes(items: list[Mapping[str, object]]) -> int:
    """对所有 rename_plan item 的 source_abs_path 实时 stat 求总字节。

    task JSON 不持久化文件大小，故详情页渲染时实时取。源文件被移走/删除
    时 stat 失败，静默跳过该条（不计入总量）。
    """
    total = 0
    for it in items:
        p = _str(it.get("source_abs_path") or it.get("source_path") or "")
        if not p:
            continue
        try:
            total += Path(p).stat().st_size
        except OSError:
            continue
    return total


def _rename_plan_items(task_data: Mapping[str, object]) -> list[Mapping[str, object]]:
    rp = task_data.get("bgm_to_tmdb_rename_plan") or {}
    items = rp.get("items") or []
    return [it for it in items if isinstance(it, Mapping)]


def _build_bangumi_subjects(task_data: Mapping[str, object]) -> list[dict[str, Any]]:
    """BGM 条目列表（支持多 subject），每个含集数范围（regular/special 分开）。

    数据源：task 顶层 bgm_subjects（条目元信息）+ bgm_to_tmdb_rename_plan.items[]
    的 bangumi_assignment.target（逐条集号，按 episode_type 聚合）。
    """
    subjects = task_data.get("bgm_subjects") or []
    if not isinstance(subjects, list):
        return []
    items = _rename_plan_items(task_data)
    by_sid: dict[int, dict[str, list[int]]] = {}
    for it in items:
        tgt = (it.get("bangumi_assignment") or {}).get("target") or {}
        sid = tgt.get("bangumi_subject_id")
        et = _str(tgt.get("episode_type") or "regular") or "regular"
        sort = tgt.get("sort")
        if not sid or sort is None:
            continue
        by_sid.setdefault(int(sid), {"regular": [], "special": []})
        bucket = "special" if et == "special" else "regular"
        by_sid[int(sid)][bucket].append(int(sort))
    out: list[dict[str, Any]] = []
    for s in subjects:
        if not isinstance(s, Mapping):
            continue
        sid = s.get("id")
        agg = by_sid.get(int(sid), {}) if sid is not None else {}
        reg = _compact_range(agg.get("regular", []))
        spc_count = len(agg.get("special", []))
        ranges: list[str] = []
        if reg:
            ranges.append(f"第{reg}话")
        if spc_count:
            # special 的 sort 含 0 且不连续，用计数比范围更清晰。
            ranges.append(f"special × {spc_count}")
        out.append({
            "id": sid,
            "name": _str(s.get("name")),
            "name_cn": _str(s.get("name_cn")),
            "media_kind": _str(s.get("media_kind")),
            "assignment_count": s.get("assignment_count", "-"),
            "episode_ranges": " + ".join(ranges),
        })
    return out


def _build_tmdb_subjects(task_data: Mapping[str, object]) -> list[dict[str, Any]]:
    """TMDB 条目列表，每个含集数范围（按 season 聚合）。

    数据源：bgm_to_tmdb_rename_plan.items[].destination。单 subject 场景用
    task 顶层 tmdb_name/tmdb_year 补全元信息。
    """
    items = _rename_plan_items(task_data)
    by_season: dict[int, list[int]] = {}
    tmdb_refs: dict[str, dict[str, object]] = {}
    for it in items:
        dest = it.get("destination") or {}
        ref = _str(dest.get("tmdb_ref"))
        sn = dest.get("season_number")
        en = dest.get("episode_number")
        if ref and sn is not None and en is not None:
            tmdb_refs.setdefault(ref, {"tmdb_id": dest.get("tmdb_id"), "media_type": dest.get("media_type")})
            by_season.setdefault(int(sn), [])
            by_season[int(sn)].append(int(en))
    if not tmdb_refs:
        return []
    ranges: list[str] = []
    # 正片 season（>0）在前，S00 special 在后，符合阅读习惯。
    seasons = sorted(by_season, key=lambda s: (s == 0, s))
    for sn in seasons:
        eps = sorted(set(by_season[sn]))
        if not eps:
            continue
        tok = f"S{sn:02d}E{eps[0]:02d}-E{eps[-1]:02d}" if eps[0] != eps[-1] else f"S{sn:02d}E{eps[0]:02d}"
        ranges.append(tok)
    ref0 = next(iter(tmdb_refs))
    info = tmdb_refs[ref0]
    return [{
        "tmdb_ref": ref0,
        "tmdb_id": info.get("tmdb_id") or task_data.get("tmdb_id"),
        "media_type": _str(info.get("media_type") or task_data.get("tmdb_media_type")),
        "name": _str(task_data.get("tmdb_name")),
        "year": task_data.get("tmdb_year", "-"),
        "episode_ranges": " + ".join(ranges),
    }]


def _build_mapping_details(task_data: Mapping[str, object]) -> list[dict[str, Any]]:
    """映射明细表：源文件 → BGM 编号 → TMDB 落点 → 置信度（三方纯编号对称）。

    多 subject 时 BGM 编号带 subject 前缀（如 '26449 #1'）。supplemental/
    未映射行 BGM/TMDB 显示 '-'。置信度取自 bgm_to_tmdb_verified_plan.mappings
    （rename_plan.items 不带 confidence，两个 list 按 source_path 对齐合并）。
    """
    items = _rename_plan_items(task_data)
    # source_path → confidence（verified_plan 才有 confidence）。
    conf_by_src: dict[str, str] = {}
    vp = task_data.get("bgm_to_tmdb_verified_plan") or {}
    for m in (vp.get("mappings") or []):
        if isinstance(m, Mapping):
            conf_by_src[_str(m.get("source_path"))] = _str(m.get("confidence"))
    sids = set()
    for it in items:
        tgt = (it.get("bangumi_assignment") or {}).get("target") or {}
        sid = tgt.get("bangumi_subject_id")
        if sid:  # 过滤 None / 0（supplemental 的 target 可能 sid=0）
            sids.add(int(sid))
    multi = len(sids) > 1
    rows: list[dict[str, Any]] = []
    for it in items:
        src = _str(it.get("source_path") or "")
        name = src.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        disposition = _str(it.get("disposition"))
        tgt = (it.get("bangumi_assignment") or {}).get("target") or {}
        sid = tgt.get("bangumi_subject_id")
        et = _str(tgt.get("episode_type") or "")
        sort = tgt.get("sort")
        bgm_label = "-"
        if sort is not None and disposition != "unmapped_supplemental":
            base = f"#{sort}" if et != "special" else (f"special #{sort}" if sort else "special")
            bgm_label = f"{sid} {base}" if multi else base
        elif et == "special":
            bgm_label = "special"
        dest = it.get("destination") or {}
        tok = _str(dest.get("episode_token"))
        tmdb_label = tok or "-"
        rows.append({
            "source_name": name,
            "source_path": src,
            "bgm": bgm_label,
            "tmdb": tmdb_label,
            "confidence": conf_by_src.get(src) or "-",
            "disposition": disposition,
        })
    return rows


def _format_bytes(n: int) -> str:
    """字节 → 人话大小（GB/MB/KB）。"""
    if n <= 0:
        return "-"
    for unit, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= factor:
            return f"{n / factor:.1f} {unit}"
    return f"{n} B"


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
    """任务列表行：合并落盘 task JSON + queue 活跃态。与 create_table 对齐。

    每条任务（按 uuid）独立一行——同路径的多次任务（失败→重试→成功）是
    不同业务事件，不能按 path 合并去重，否则旧失败记录会覆盖新成功记录。
    """
    queue_mgr = get_queue_manager()
    rows_by_uuid: dict[str, dict[str, Any]] = {}

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
        # 状态归一成短词，供 StatusBadge 精确匹配（成功/失败/处理中/等待处理）。
        # 完整 error 原文不进列表行——详情页 build_task_detail 直接读 task JSON
        # 取 error/failure_reason 完整展示，列表只看成败 + 人话短句。
        error_value = task_data.get("error")
        failure_reason = _str(task_data.get("failure_reason"))
        has_error = (isinstance(error_value, str) and error_value) or failure_reason
        status = "失败" if has_error else "成功"
        failure_reason_label = (
            FAILURE_REASON_LABELS.get(failure_reason, failure_reason)
            if failure_reason
            else ""
        )
        ai_used = task_data.get("ai_used", task_data.get("use_ai", False))
        _, queue_status_text = _queue_status_text(path)
        uuid_value = _str(task_data.get("uuid")) or f.stem
        rows_by_uuid[uuid_value] = {
            "path": path,
            "name": task_data.get("name", "未知"),
            "uuid": uuid_value,
            "season": task_data.get("season_id", "-"),
            "status": status,
            "failure_reason_label": failure_reason_label,
            "queue_status": queue_status_text,
            "is_anime": task_data.get("is_anime", False),
            "is_movie": task_data.get("is_movie", False),
            "ai_used": "是" if ai_used else "否",
        }

    # 活跃但未落盘的任务（处理中/队列中）
    for task in queue_mgr.list_active_tasks():
        task_uuid = task.original_uuid or task.task_id
        if task_uuid in rows_by_uuid:
            continue
        if task.status == TaskStatus.RUNNING:
            queue_status_text = "执行中..."
            status = "处理中"
        else:
            position = queue_mgr.get_queue_position(task.path)
            queue_status_text = f"队列中 #{position}"
            status = "等待处理"
        rows_by_uuid[task_uuid] = {
            "path": task.path,
            "name": task.cus_name or Path(task.path).name,
            "uuid": task_uuid,
            "season": task.cus_season_id or "-",
            "status": status,
            "failure_reason_label": "",
            "queue_status": queue_status_text,
            "is_anime": _format_bool_text(task.is_anime),
            "is_movie": _format_bool_text(task.is_movie),
            "ai_used": "待处理",
        }

    rows = list(rows_by_uuid.values())
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
    # Case Agent 状态回退链：新 local_bangumi_to_tmdb_product 链路不写顶层
    # case_agent_status，成功信号分散在 case_agent_result.status /
    # bgm_to_tmdb_bridge_status / subtitle_fetch_case_agent_status 里。
    case_agent_status = _str(
        task_data.get("case_agent_status")
        or record_data.get("case_agent_status")
        or _nested_status(task_data.get("case_agent_result"))
        or task_data.get("bgm_to_tmdb_bridge_status")
        or task_data.get("subtitle_fetch_case_agent_status")
    )
    # 产品结果类型回退链：record 无结构化 product_result_kind 时，取 task 顶层
    # local_bangumi_product_result_kind，再取 case_agent_result.snapshot.product_result_kind。
    product_result_kind = _str(
        record_data.get("product_result_kind")
        or task_data.get("local_bangumi_product_result_kind")
        or _nested_snapshot_kind(task_data.get("case_agent_result"))
        or task_data.get("bgm_to_tmdb_bridge_status")
    )
    # 落地映射：旧链路 record 是 {"mappings": [...], "target_dir": ...}；
    # 新 local_bangumi_to_tmdb_product 链路 record 是扁平 {源路径: 目标路径} dict，
    # 没有 mappings/target_dir key，需要回退到 task 顶层 target_root /
    # transferred_file_count，并按扁平 dict 计数。
    mappings = record_data.get("mappings")
    if isinstance(mappings, list):
        mapping_count = len(mappings)
    elif isinstance(record_data, Mapping) and record_data:
        # 扁平 源->目标 dict：条目数即映射数。
        mapping_count = len(record_data)
    else:
        mapping_count = _int_or_zero(task_data.get("transferred_file_count"))
    target_dir = _str(
        record_data.get("target_dir") or task_data.get("target_root")
    )
    # 字幕自动抓取区块：读子任务文件拿配对统计，仅在触发过 auto_fetch 时展示。
    sf_child = _load_subtitle_fetch_child(uuid)
    subtitle_fetch = _build_subtitle_fetch_section(task_data, sf_child)
    # BGM/TMDB 条目（对齐展示集数范围）+ 映射明细 + 总大小。
    rename_items = _rename_plan_items(task_data)
    bangumi_subjects = _build_bangumi_subjects(task_data)
    tmdb_subjects = _build_tmdb_subjects(task_data)
    mapping_details = _build_mapping_details(task_data)
    total_size = _format_bytes(_stat_total_size_bytes(rename_items))
    return {
        "found": True,
        "uuid": uuid,
        "basic": {
            "path": _str(task_data.get("path")),
            "name": _str(task_data.get("name")) or "未知",
            "season_id": task_data.get("season_id", "-"),
            # is_anime/is_movie 仅内部落地路由用，不再展示；
            # 详情页改用 TMDB 权威媒体类型（tmdb_media_type 由 BGM→TMDB 桥接回写）。
            "tmdb_media_type": _str(task_data.get("tmdb_media_type")),
            "tmdb_media_type_label": TMDB_MEDIA_TYPE_LABELS.get(
                _str(task_data.get("tmdb_media_type")),
                _str(task_data.get("tmdb_media_type")),
            ),
            "tmdb_name": _str(task_data.get("tmdb_name")),
            "tmdb_year": task_data.get("tmdb_year", "-"),
            "tmdb_id": task_data.get("tmdb_id", "-"),
        },
        "failure": {
            "reason": failure_reason,
            "reason_label": FAILURE_REASON_LABELS.get(failure_reason, failure_reason),
            "error": _str(task_data.get("error")),
        },
        "ai": {
            "ai_used": bool(task_data.get("ai_used")),
            "ai_attempted": bool(task_data.get("ai_attempted")),
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
            "product_result_kind": product_result_kind,
        },
        "landing": {
            "target_dir": target_dir,
            "mapping_count": mapping_count,
            "mappings": mappings if isinstance(mappings, list) else [],
        },
        # 仅在触发过 auto_fetch 时存在；前端据此条件渲染字幕区块。
        "subtitle_fetch": subtitle_fetch,
        # BGM/TMDB 条目（对齐集数范围）+ 映射明细 + 总大小。
        # 仅在 rename_plan 存在时非空；前端据此条件渲染。
        "bangumi_subjects": bangumi_subjects,
        "tmdb_subjects": tmdb_subjects,
        "mapping_details": mapping_details,
        "total_size": total_size,
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
