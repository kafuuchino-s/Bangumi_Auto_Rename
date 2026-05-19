from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Empty
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.ai.client import AIClient
from src.bangumi.client import BangumiClient
from src.config.config_manager import cm
from src.rename.case_agent.local_bangumi_entry import run_local_bangumi_case_agent_mapping
from src.rename.local_evidence import LocalEvidence, LocalFileEvidence
from src.rename.local_supplemental_filter import classify_local_video_supplemental
from src.rename.utils import VIDEO_SUFFIX


SAMPLE_WORKER_COUNT = 20
SAMPLE_PROVIDER_NO_RESPONSE_RETRIES = 1
CASE_AGENT_PROGRESS_ENV_VAR = "LOCAL_BANGUMI_CASE_AGENT_PROGRESS_PATH"
ALLOWED_FAIL_CLOSED_SUMMARIES = {
    "budget_exhausted",
    "agent_fail_closed_from_submit",
    "no_new_evidence",
    "semantic_target_conflict",
    "child_case_unresolved",
    "provider_retry_exhausted",
    "semantic_ambiguity",
    "retrieval_exhausted",
    "agent_recovery_failed",
    "obvious_terminal_fail_closed",
    "provider_failure",
}
AI_CALL_STAGE_BY_NAME = {
    "call_local_structure_agent": "local_structure",
    "call_case_briefing_agent": "case_briefing",
    "call_case_planner": "case_planner",
    "call_query_composer": "query_composer",
    "call_mapping_draft_editor": "mapping_draft_editor",
    "call_case_judge": "case_judge",
}


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _load_raw_sample(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"raw sample must be a JSON object: {path}")
    return payload


def local_evidence_from_raw_sample(path: Path) -> LocalEvidence:
    payload = _load_raw_sample(path)
    root_name = str(payload.get("root_name") or path.stem)
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise ValueError(f"raw sample files must be a list: {path}")

    files: list[LocalFileEvidence] = []
    directories: set[str] = set()
    video_suffixes = {suffix.casefold() for suffix in VIDEO_SUFFIX}
    for index, item in enumerate(raw_files, start=1):
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get("path") or item.get("relative_path") or "").strip()
        if not relative_path:
            continue
        path_obj = Path(relative_path)
        suffix = path_obj.suffix.casefold()
        is_video = suffix in video_suffixes
        supplemental = classify_local_video_supplemental(relative_path, is_video=is_video)
        directories.update(part for part in path_obj.parts[:-1] if part)
        size = item.get("size", item.get("size_bytes"))
        files.append(
            LocalFileEvidence(
                file_id=f"file_{index:03d}",
                relative_path=relative_path.replace("\\", "/"),
                name=path_obj.name,
                suffix=suffix,
                is_video=is_video,
                is_supplemental_candidate=bool(supplemental.is_supplemental),
                is_main_video_candidate=is_video and not supplemental.is_supplemental,
                size_bytes=int(size) if isinstance(size, int) else None,
            )
        )

    return LocalEvidence(
        root_name=root_name,
        root_path=str(path),
        files=files,
        video_count=sum(1 for file in files if file.is_video),
        main_video_count=sum(1 for file in files if file.is_main_video_candidate),
        supplemental_candidate_count=sum(1 for file in files if file.is_supplemental_candidate),
        directory_structure=sorted(directories),
    )


def _select_samples(raw_root: Path, filters: list[str], limit: int | None, offset: int = 0) -> list[Path]:
    candidates = sorted(raw_root.rglob("*.json"), key=lambda item: item.as_posix().casefold())
    return _filter_samples(candidates, filters, limit, offset)


def _filter_samples(candidates: list[Path], filters: list[str], limit: int | None, offset: int = 0) -> list[Path]:
    if filters:
        lowered = [item.casefold() for item in filters]
        candidates = [
            path
            for path in candidates
            if any(token in path.as_posix().casefold() or token in path.stem.casefold() for token in lowered)
        ]
    if offset > 0:
        candidates = candidates[offset:]
    if limit is not None:
        candidates = candidates[: max(0, limit)]
    return candidates


def _manifest_items(payload: Any, manifest_path: Path) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("samples"), list):
        return list(payload["samples"])
    raise ValueError(f"sample list manifest must be a JSON array or an object with samples[]: {manifest_path}")


def _sample_key(path: Path) -> str:
    return path.resolve().as_posix().casefold()


def _resolve_manifest_sample_path(raw_root: Path, manifest_path: Path, sample_value: str) -> Path:
    raw = str(sample_value or "").strip()
    if not raw:
        raise ValueError(f"sample list contains an empty sample_path: {manifest_path}")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    repo_relative = REPO_ROOT / candidate
    if repo_relative.exists():
        return repo_relative
    return raw_root / candidate


def _load_sample_list_entries(manifest_path: Path, raw_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    with manifest_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    entries: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for index, item in enumerate(_manifest_items(payload, manifest_path), start=1):
        if isinstance(item, str):
            sample_value = item
            metadata: dict[str, Any] = {"sample_path": item}
        elif isinstance(item, dict):
            sample_value = str(item.get("sample_path") or item.get("sample") or "")
            metadata = dict(item)
        else:
            raise ValueError(f"sample list item #{index} must be a string or object: {manifest_path}")
        path = _resolve_manifest_sample_path(raw_root, manifest_path, sample_value)
        if not path.exists():
            raise FileNotFoundError(f"sample list item #{index} does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"sample list item #{index} is not a file: {path}")
        key = _sample_key(path)
        if key in seen:
            raise ValueError(f"sample list contains a duplicate sample_path: {path}")
        seen.add(key)
        entries.append((path, metadata))
    return entries


def _load_sample_list(manifest_path: Path, raw_root: Path) -> list[Path]:
    return [path for path, _metadata in _load_sample_list_entries(manifest_path, raw_root)]


def _count_rows_by_metadata(rows: list[dict[str, Any]], metadata_by_key: dict[str, dict[str, Any]], metadata_field: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        sample = str(row.get("sample") or "")
        metadata = metadata_by_key.get(_sample_key(Path(sample))) if sample else None
        value = str((metadata or {}).get(metadata_field) or "")
        if not value:
            continue
        status = str(row.get("status") or "unknown")
        bucket = counts.setdefault(value, {})
        bucket[status] = bucket.get(status, 0) + 1
    return counts


def _default_output_dir() -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    millis = int((time.time() % 1) * 1000)
    return Path("tests/sample_pool/generated") / f"local_bangumi_mapping_gate_{timestamp}_{millis:03d}"


def _accepted_contract_ok(snapshot: dict[str, Any]) -> bool:
    if str(snapshot.get("status") or "") != "accepted":
        return False
    main_count = int(snapshot.get("main_file_count") or snapshot.get("contract_main_file_count") or 0)
    accounted = int(snapshot.get("accounted_for_count") or 0)
    mapped = int(snapshot.get("mapped_file_count") or 0)
    excluded = int(snapshot.get("excluded_file_count") or 0)
    unresolved = int(snapshot.get("unresolved_count") or 0)
    open_count = int(snapshot.get("open_file_count") or 0)
    needs_more = int(snapshot.get("needs_more_evidence_file_count") or 0)
    unaligned = int(snapshot.get("unaligned_file_count") or 0)
    return (
        main_count > 0
        and accounted == main_count
        and mapped + excluded == main_count
        and unresolved == 0
        and open_count == 0
        and needs_more == 0
        and unaligned == 0
        and bool(snapshot.get("accepted_accounting_ready"))
        and bool(snapshot.get("final_verifier_passed"))
    )


def _strict_row_ok(row: dict[str, Any]) -> bool:
    if bool(row.get("briefing_memory_lost")):
        return False
    status = str(row.get("status") or "")
    if status == "dry_build":
        return True
    if status == "accepted":
        return bool(row.get("ok")) and bool(row.get("accepted_contract_ok"))
    if status == "fail_closed":
        return (
            bool(row.get("ok"))
            and bool(row.get("final_verifier_passed"))
            and str(row.get("summary") or "") in ALLOWED_FAIL_CLOSED_SUMMARIES
        )
    return False


def _add_count(target: dict[str, int], key: str, value: int = 1) -> None:
    target[key] = int(target.get(key) or 0) + int(value)


def _case_agent_ai_call_stats(snapshot: dict[str, Any]) -> dict[str, Any]:
    audits = snapshot.get("case_judge_request_audits") if isinstance(snapshot.get("case_judge_request_audits"), list) else []
    call_counts_by_stage: dict[str, int] = {}
    attempt_counts_by_stage: dict[str, int] = {}
    retry_counts_by_stage: dict[str, int] = {}
    orchestrator_usage_total_tokens = 0
    orchestrator_usage_input_tokens = 0
    orchestrator_usage_output_tokens = 0
    orchestrator_provider_cached_input_tokens = 0
    orchestrator_max_turn_input_tokens = 0
    orchestrator_min_cached_input_ratio_after_first_turn = 0.0
    orchestrator_low_cached_turn_count_after_first_turn = 0
    cached_ratios_after_first_turn: list[float] = []
    cached_ratio_samples: list[dict[str, Any]] = []
    call_count = 0
    retry_count = 0
    legacy_subagent_call_count = 0
    for audit in audits:
        if not isinstance(audit, dict):
            continue
        if audit.get("note") == "orchestrator_agent_called":
            stage = "orchestrator_agent"
            retries = int(audit.get("provider_retry_count") or 0)
            call_count += 1
            retry_count += retries
            _add_count(call_counts_by_stage, stage)
            _add_count(attempt_counts_by_stage, stage, 1 + retries)
            if retries:
                _add_count(retry_counts_by_stage, stage, retries)
            usage = audit.get("usage") if isinstance(audit.get("usage"), dict) else {}
            input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
            details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else usage.get("prompt_tokens_details")
            cached_tokens = int((details or {}).get("cached_tokens") or (details or {}).get("cache_read_input_tokens") or 0) if isinstance(details, dict) else 0
            orchestrator_usage_input_tokens += input_tokens
            orchestrator_usage_output_tokens += output_tokens
            orchestrator_usage_total_tokens += total_tokens
            orchestrator_provider_cached_input_tokens += cached_tokens
            orchestrator_max_turn_input_tokens = max(orchestrator_max_turn_input_tokens, input_tokens)
            if int(audit.get("turn_count") or 0) >= 2 and input_tokens:
                ratio = cached_tokens / input_tokens
                cached_ratios_after_first_turn.append(ratio)
                if ratio < 0.25:
                    orchestrator_low_cached_turn_count_after_first_turn += 1
                if len(cached_ratio_samples) < 24:
                    cached_ratio_samples.append({
                        "turn": int(audit.get("turn_count") or 0),
                        "tool_name": str(audit.get("tool_name") or ""),
                        "input_tokens": input_tokens,
                        "cached_tokens": cached_tokens,
                        "cached_ratio": ratio,
                        "tail_lcp_with_previous_bytes": int(audit.get("tail_lcp_with_previous_bytes") or 0),
                        "tail_lcp_with_previous_estimated_tokens": int(audit.get("tail_lcp_with_previous_estimated_tokens") or 0),
                        "instructions_sha256": str(audit.get("instructions_sha256") or ""),
                        "tools_sha256": str(audit.get("tools_sha256") or ""),
                        "case_desk_sha256": str(audit.get("case_desk_sha256") or ""),
                        "tail_sha256": str(audit.get("tail_sha256") or ""),
                        "tool_choice": audit.get("tool_choice"),
                    })
            continue
        call_name = str(audit.get("call_name") or "").strip()
        if not call_name or call_name == "LocalPackageAnalysis":
            continue
        if call_name in {"call_query_composer", "call_mapping_draft_editor", "call_case_judge"}:
            legacy_subagent_call_count += 1
        stage = AI_CALL_STAGE_BY_NAME.get(call_name, call_name.removeprefix("call_") or "unknown")
        retries = int(audit.get("provider_retry_count") or 0)
        call_count += 1
        retry_count += retries
        _add_count(call_counts_by_stage, stage)
        _add_count(attempt_counts_by_stage, stage, 1 + retries)
        if retries:
            _add_count(retry_counts_by_stage, stage, retries)
    return {
        "ai_call_count": call_count,
        "ai_attempt_count_estimate": call_count + retry_count,
        "ai_provider_retry_count": retry_count,
        "ai_call_counts_by_stage": call_counts_by_stage,
        "ai_attempt_counts_by_stage": attempt_counts_by_stage,
        "ai_provider_retry_counts_by_stage": retry_counts_by_stage,
        "legacy_subagent_call_count": legacy_subagent_call_count,
        "orchestrator_usage_total_tokens": orchestrator_usage_total_tokens,
        "orchestrator_usage_input_tokens": orchestrator_usage_input_tokens,
        "orchestrator_usage_output_tokens": orchestrator_usage_output_tokens,
        "orchestrator_provider_cached_input_tokens": orchestrator_provider_cached_input_tokens,
        "orchestrator_provider_cached_input_ratio": (
            orchestrator_provider_cached_input_tokens / orchestrator_usage_input_tokens
            if orchestrator_usage_input_tokens
            else 0.0
        ),
        "orchestrator_min_cached_input_ratio_after_first_turn": min(cached_ratios_after_first_turn) if cached_ratios_after_first_turn else 0.0,
        "orchestrator_low_cached_turn_count_after_first_turn": orchestrator_low_cached_turn_count_after_first_turn,
        "orchestrator_cached_input_ratio_samples": cached_ratio_samples,
        "orchestrator_max_turn_input_tokens": orchestrator_max_turn_input_tokens,
    }


def _tool_rejection_reason_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    audits = snapshot.get("case_judge_request_audits") if isinstance(snapshot.get("case_judge_request_audits"), list) else []
    counts: dict[str, int] = {}
    for audit in audits:
        if not isinstance(audit, dict):
            continue
        note = str(audit.get("note") or "")
        if note == "human_case_agent_tool_rejected":
            reason = str(audit.get("reason") or audit.get("issue") or "unknown")
            counts[reason] = int(counts.get(reason) or 0) + 1
            continue
        if note != "orchestrator_tool_selected" or bool(audit.get("accepted")):
            continue
        reason = str(audit.get("reason") or "unknown")
        counts[reason] = int(counts.get(reason) or 0) + 1
    session_summary = next(
        (
            audit
            for audit in reversed(audits)
            if isinstance(audit, dict) and audit.get("note") == "orchestrator_agent_session_summary"
        ),
        {},
    )
    if isinstance(session_summary, dict):
        total = int(session_summary.get("tool_rejection_count") or 0)
        missing = total - sum(counts.values())
        if missing > 0:
            counts["tool_output_rejected"] = int(counts.get("tool_output_rejected") or 0) + missing
    return counts


def _sample_row(sample_path: Path, result: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else result
    status = str(snapshot.get("status") or result.get("status") or "unknown")
    accepted_contract_ok = _accepted_contract_ok(snapshot) if isinstance(snapshot, dict) else False
    ai_stats = _case_agent_ai_call_stats(snapshot) if isinstance(snapshot, dict) else {}
    rejection_reason_counts = _tool_rejection_reason_counts(snapshot) if isinstance(snapshot, dict) else {}
    audits = snapshot.get("case_judge_request_audits") if isinstance(snapshot, dict) and isinstance(snapshot.get("case_judge_request_audits"), list) else []
    session_summary = next(
        (
            audit
            for audit in reversed(audits)
            if isinstance(audit, dict) and audit.get("note") == "orchestrator_agent_session_summary"
        ),
        {},
    )
    session_summary = session_summary if isinstance(session_summary, dict) else {}

    def sample_value(key: str) -> Any:
        if isinstance(snapshot, dict) and snapshot.get(key) is not None:
            return snapshot.get(key)
        return session_summary.get(key)

    summary_value = snapshot.get("summary") or result.get("summary") if isinstance(snapshot, dict) else result.get("summary")
    return {
        "sample": sample_path.as_posix(),
        "status": status,
        "ok": bool(result.get("ok")),
        "accepted_contract_ok": accepted_contract_ok,
        "elapsed_ms": elapsed_ms,
        "main_file_count": snapshot.get("main_file_count") or snapshot.get("contract_main_file_count") if isinstance(snapshot, dict) else None,
        "assignment_intent_count": snapshot.get("assignment_intent_count") if isinstance(snapshot, dict) else None,
        "mapped_file_count": snapshot.get("mapped_file_count") if isinstance(snapshot, dict) else None,
        "excluded_file_count": snapshot.get("excluded_file_count") if isinstance(snapshot, dict) else None,
        "unresolved_count": snapshot.get("unresolved_count") if isinstance(snapshot, dict) else None,
        "final_verifier_passed": snapshot.get("final_verifier_passed") if isinstance(snapshot, dict) else None,
        "case_agent_mode": snapshot.get("case_agent_mode") if isinstance(snapshot, dict) else None,
        "legacy_orchestrator_main_path_used": snapshot.get("legacy_orchestrator_main_path_used") if isinstance(snapshot, dict) else None,
        "semantic_subagent_call_count": snapshot.get("semantic_subagent_call_count") if isinstance(snapshot, dict) else None,
        "first_turn_estimated_tokens": snapshot.get("first_turn_estimated_tokens") if isinstance(snapshot, dict) else None,
        "agent_facing_locator_count": snapshot.get("agent_facing_locator_count") if isinstance(snapshot, dict) else None,
        "submit_rejection_count": snapshot.get("submit_rejection_count") if isinstance(snapshot, dict) else None,
        "submit_rejection_issue_counts": snapshot.get("submit_rejection_issue_counts") if isinstance(snapshot, dict) else None,
        "human_case_saved_work_unit_count": snapshot.get("human_case_saved_work_unit_count") if isinstance(snapshot, dict) else None,
        "human_case_draft_revision_count": snapshot.get("human_case_draft_revision_count") if isinstance(snapshot, dict) else None,
        "attention_focus_change_count": snapshot.get("attention_focus_change_count") if isinstance(snapshot, dict) else None,
        "agenda_open_count": snapshot.get("agenda_open_count") if isinstance(snapshot, dict) else None,
        "agenda_closed_count": snapshot.get("agenda_closed_count") if isinstance(snapshot, dict) else None,
        "noise_candidate_count": snapshot.get("noise_candidate_count") if isinstance(snapshot, dict) else None,
        "stall_warning_count": snapshot.get("stall_warning_count") if isinstance(snapshot, dict) else None,
        "no_progress_escape_count": snapshot.get("no_progress_escape_count") if isinstance(snapshot, dict) else None,
        "recovery_frontier_switch_count": snapshot.get("recovery_frontier_switch_count") if isinstance(snapshot, dict) else None,
        "exact_fail_closed_after_frontier_exhausted_count": snapshot.get("exact_fail_closed_after_frontier_exhausted_count") if isinstance(snapshot, dict) else None,
        "weak_related_blocking_action_count": snapshot.get("weak_related_blocking_action_count") if isinstance(snapshot, dict) else None,
        "case_resolution_goal_status": sample_value("case_resolution_goal_status"),
        "case_resolution_goal_strategy_counts": sample_value("case_resolution_goal_strategy_counts"),
        "case_resolution_goal_progress_count": sample_value("case_resolution_goal_progress_count"),
        "same_blocker_strategy_change_required_count": sample_value("same_blocker_strategy_change_required_count"),
        "case_resolution_goal_terminal_rejection_count": sample_value("case_resolution_goal_terminal_rejection_count"),
        "obvious_terminal_fail_closed_count": sample_value("obvious_terminal_fail_closed_count")
        or (1 if summary_value == "obvious_terminal_fail_closed" else 0),
        "repair_strategy_missing_count": sample_value("repair_strategy_missing_count"),
        "high_quality_candidate_count_by_turn": snapshot.get("high_quality_candidate_count_by_turn") if isinstance(snapshot, dict) else None,
        "diagnostic_candidate_count_by_turn": snapshot.get("diagnostic_candidate_count_by_turn") if isinstance(snapshot, dict) else None,
        "noisy_candidate_count_by_turn": snapshot.get("noisy_candidate_count_by_turn") if isinstance(snapshot, dict) else None,
        "blocking_action_count_by_turn": snapshot.get("blocking_action_count_by_turn") if isinstance(snapshot, dict) else None,
        "manual_vs_agent_divergence_point": snapshot.get("manual_vs_agent_divergence_point") if isinstance(snapshot, dict) else None,
        "resolution_readiness_summary": snapshot.get("resolution_readiness_summary") if isinstance(snapshot, dict) else None,
        "case_understanding_applied": snapshot.get("case_understanding_applied") if isinstance(snapshot, dict) else None,
        "briefing_memory_lost": snapshot.get("briefing_memory_lost") if isinstance(snapshot, dict) else None,
        "case_planning_action": snapshot.get("case_planning_action") if isinstance(snapshot, dict) else None,
        "split_child_case_count": snapshot.get("split_child_case_count") if isinstance(snapshot, dict) else None,
        "split_child_statuses": snapshot.get("split_child_statuses") if isinstance(snapshot, dict) else None,
        "summary": summary_value,
        "orchestrator_turn_count": snapshot.get("orchestrator_turn_count") if isinstance(snapshot, dict) else None,
        "orchestrator_tool_call_counts": snapshot.get("orchestrator_tool_call_counts") if isinstance(snapshot, dict) else None,
        "orchestrator_tool_sequence": snapshot.get("orchestrator_tool_sequence") if isinstance(snapshot, dict) else None,
        "case_resolution_ledger_row_count": snapshot.get("case_resolution_ledger_row_count") if isinstance(snapshot, dict) else None,
        "ledger_outcome_counts": snapshot.get("ledger_outcome_counts") if isinstance(snapshot, dict) else None,
        "ledger_blocked_row_count": snapshot.get("ledger_blocked_row_count") if isinstance(snapshot, dict) else None,
        "ledger_compiled_patch_count": snapshot.get("ledger_compiled_patch_count") if isinstance(snapshot, dict) else None,
        "ledger_requested_evidence_count": snapshot.get("ledger_requested_evidence_count") if isinstance(snapshot, dict) else None,
        "root_orchestrator_turn_count": snapshot.get("root_orchestrator_turn_count") if isinstance(snapshot, dict) else None,
        "root_orchestrator_tool_call_counts": snapshot.get("root_orchestrator_tool_call_counts") if isinstance(snapshot, dict) else None,
        "root_orchestrator_tool_sequence": snapshot.get("root_orchestrator_tool_sequence") if isinstance(snapshot, dict) else None,
        "tool_rejection_count": snapshot.get("tool_rejection_count") if isinstance(snapshot, dict) else None,
        "tool_rejection_reason_counts": rejection_reason_counts,
        "near_turn_limit_unhealthy_count": snapshot.get("near_turn_limit_unhealthy_count") if isinstance(snapshot, dict) else None,
        "stall_suspected_count": snapshot.get("stall_suspected_count") if isinstance(snapshot, dict) else None,
        "compact_count": snapshot.get("compact_count") if isinstance(snapshot, dict) else None,
        "orchestrator_session_mode": snapshot.get("orchestrator_session_mode") if isinstance(snapshot, dict) else None,
        "orchestrator_provider_session_enabled": snapshot.get("orchestrator_provider_session_enabled") if isinstance(snapshot, dict) else None,
        "orchestrator_http_session_id": snapshot.get("orchestrator_http_session_id") if isinstance(snapshot, dict) else None,
        "orchestrator_prompt_cache_key": snapshot.get("orchestrator_prompt_cache_key") if isinstance(snapshot, dict) else None,
        "orchestrator_prompt_cache_retention": snapshot.get("orchestrator_prompt_cache_retention") if isinstance(snapshot, dict) else None,
        "orchestrator_stable_prefix_estimated_tokens": snapshot.get("orchestrator_stable_prefix_estimated_tokens") if isinstance(snapshot, dict) else None,
        "orchestrator_turn_tail_estimated_tokens": snapshot.get("orchestrator_turn_tail_estimated_tokens") if isinstance(snapshot, dict) else None,
        **ai_stats,
    }


def _is_provider_no_response_result(result: dict[str, Any]) -> bool:
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else result
    if not isinstance(snapshot, dict):
        return False
    error_kind = str(snapshot.get("case_agent_error_kind") or snapshot.get("error_kind") or result.get("error_kind") or "")
    if error_kind == "provider_no_response":
        return True
    errors = snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else result.get("errors")
    return any("provider_no_response" in str(item) or "no response" in str(item).casefold() for item in list(errors or []))


def _write_sample_result(sample: Path, output_dir: Path, result: dict[str, Any]) -> None:
    sample_id = sample.stem
    (output_dir / f"{sample_id}.json").write_text(
        json.dumps(_json_safe(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _progress_path_for_sample(sample: Path, output_dir: Path) -> Path:
    return output_dir / f"{sample.stem}.progress.json"


def _read_sample_progress(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"progress_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"progress_read_error": "progress payload is not an object"}


def _write_runner_progress(sample: Path, output_dir: Path, *, phase: str, extra: dict[str, Any] | None = None) -> None:
    progress_path_text = os.environ.get(CASE_AGENT_PROGRESS_ENV_VAR, "").strip()
    if not progress_path_text:
        return
    progress_path = Path(progress_path_text)
    try:
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "kind": "local_bangumi_sample_runner_progress",
            "updated_at_ms": int(time.time() * 1000),
            "phase": phase,
            "sample": sample.as_posix(),
            "output_dir": output_dir.as_posix(),
            **dict(extra or {}),
        }
        tmp_path = progress_path.with_suffix(progress_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(progress_path)
    except Exception:
        return


def _row_with_partial_progress(row: dict[str, Any], progress_path: Path) -> dict[str, Any]:
    progress = _read_sample_progress(progress_path)
    if not progress:
        return row
    session = progress.get("session") if isinstance(progress.get("session"), dict) else {}
    row = {
        **row,
        "partial_progress_path": progress_path.as_posix(),
        "partial_progress_phase": progress.get("phase"),
        "partial_orchestrator_turn_count": session.get("orchestrator_turn_count"),
        "partial_orchestrator_tool_sequence": session.get("orchestrator_tool_sequence"),
        "partial_tool_rejection_count": session.get("tool_rejection_count"),
        "partial_progress_case_id": progress.get("case_id"),
    }
    return row


def _dry_build_row(sample: Path) -> dict[str, Any]:
    evidence = local_evidence_from_raw_sample(sample)
    return {
        "sample": sample.as_posix(),
        "status": "dry_build",
        "file_count": len(evidence.files),
        "video_count": evidence.video_count,
        "main_video_count": evidence.main_video_count,
        "supplemental_candidate_count": evidence.supplemental_candidate_count,
        "root_name": evidence.root_name,
    }


def _run_mapping_sample_uncapped(
    sample: Path,
    output_dir: Path,
    max_rounds: int | None = None,
) -> dict[str, Any]:
    started = time.time()
    retry_reasons: list[str] = []
    try:
        _write_runner_progress(sample, output_dir, phase="sample_load_started")
        evidence = local_evidence_from_raw_sample(sample)
        _write_runner_progress(
            sample,
            output_dir,
            phase="sample_loaded",
            extra={
                "root_name": evidence.root_name,
                "file_count": len(evidence.files),
                "main_video_count": evidence.main_video_count,
                "supplemental_candidate_count": evidence.supplemental_candidate_count,
            },
        )
        result: dict[str, Any] = {}
        for attempt in range(SAMPLE_PROVIDER_NO_RESPONSE_RETRIES + 1):
            _write_runner_progress(
                sample,
                output_dir,
                phase="case_agent_mapping_started",
                extra={"attempt": attempt + 1, "max_rounds": max_rounds},
            )
            overrides: dict[str, Any] = {}
            if max_rounds is not None:
                overrides['rename_local_bangumi_case_agent_audit_max_rounds'] = max(1, int(max_rounds))
            if overrides:
                with cm.temporary_config(overrides):
                    result = run_local_bangumi_case_agent_mapping(
                        local_evidence=evidence,
                        bangumi_contexts=[],
                        ai_client=AIClient(),
                        source_path=sample,
                        bangumi_client=BangumiClient(),
                    )
            else:
                result = run_local_bangumi_case_agent_mapping(
                    local_evidence=evidence,
                    bangumi_contexts=[],
                    ai_client=AIClient(),
                    source_path=sample,
                    bangumi_client=BangumiClient(),
                )
            if not _is_provider_no_response_result(result):
                break
            if attempt >= SAMPLE_PROVIDER_NO_RESPONSE_RETRIES:
                break
            retry_reasons.append("provider_no_response")
            time.sleep(min(1.0, 0.25 * (attempt + 1)))
        _write_runner_progress(
            sample,
            output_dir,
            phase="case_agent_mapping_finished",
            extra={
                "status": str(result.get("status") or ""),
                "summary": str(result.get("summary") or ""),
                "retry_count": len(retry_reasons),
            },
        )
        elapsed_ms = int((time.time() - started) * 1000)
        if retry_reasons:
            result = {
                **result,
                "sample_runner_retry_count": len(retry_reasons),
                "sample_runner_retry_reasons": retry_reasons,
            }
        row = _sample_row(sample, result, elapsed_ms)
        if retry_reasons:
            row["sample_runner_retry_count"] = len(retry_reasons)
            row["sample_runner_retry_reasons"] = retry_reasons
        _write_sample_result(
            sample,
            output_dir,
            {
                **result,
                "sample_runner": row,
            },
        )
        return row
    except Exception as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "sample": sample.as_posix(),
            "status": "error",
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
        }


def _run_mapping_sample_process_entry(
    queue: mp.Queue,
    sample: str,
    output_dir: str,
    max_rounds: int | None,
) -> None:
    progress_path = _progress_path_for_sample(Path(sample), Path(output_dir))
    previous_progress_path = os.environ.get(CASE_AGENT_PROGRESS_ENV_VAR)
    os.environ[CASE_AGENT_PROGRESS_ENV_VAR] = progress_path.as_posix()
    try:
        row = _run_mapping_sample_uncapped(Path(sample), Path(output_dir), max_rounds)
        queue.put({"ok": True, "row": row})
    except BaseException as exc:
        queue.put({"ok": False, "error": str(exc)})
    finally:
        if previous_progress_path is None:
            os.environ.pop(CASE_AGENT_PROGRESS_ENV_VAR, None)
        else:
            os.environ[CASE_AGENT_PROGRESS_ENV_VAR] = previous_progress_path


def _run_mapping_sample(
    sample: Path,
    output_dir: Path,
    max_rounds: int | None = None,
    sample_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    timeout = int(sample_timeout_seconds or 0)
    if timeout <= 0:
        return _run_mapping_sample_uncapped(sample, output_dir, max_rounds)

    started = time.time()
    queue: mp.Queue = mp.Queue(maxsize=1)
    progress_path = _progress_path_for_sample(sample, output_dir)
    process = mp.Process(
        target=_run_mapping_sample_process_entry,
        args=(queue, sample.as_posix(), output_dir.as_posix(), max_rounds),
    )
    process.start()
    deadline = started + timeout
    message: dict[str, Any] | None = None
    while time.time() < deadline:
        try:
            item = queue.get(timeout=0.5)
            message = item if isinstance(item, dict) else {"ok": False, "error": "sample process returned non-object message"}
            break
        except Empty:
            if not process.is_alive():
                break
    if message is not None:
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
    elif process.is_alive():
        process.terminate()
        process.join(5)
        elapsed_ms = int((time.time() - started) * 1000)
        row = {
            "sample": sample.as_posix(),
            "status": "error",
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "summary": f"sample_timeout_{timeout}s",
            "error": f"sample exceeded wall-clock timeout of {timeout} seconds",
            "sample_timeout_seconds": timeout,
            "sample_timed_out": True,
        }
        row = _row_with_partial_progress(row, progress_path)
        progress_payload = _read_sample_progress(progress_path)
        _write_sample_result(
            sample,
            output_dir,
            {
                "ok": False,
                "status": "error",
                "summary": row["summary"],
                "error": row["error"],
                "sample_runner": row,
                "case_agent_progress": progress_payload,
            },
        )
        return row
    if message is None:
        try:
            item = queue.get_nowait()
            message = item if isinstance(item, dict) else {"ok": False, "error": "sample process returned non-object message"}
        except Empty:
            message = None
    if message is None:
        elapsed_ms = int((time.time() - started) * 1000)
        row = {
            "sample": sample.as_posix(),
            "status": "error",
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "summary": "sample_process_no_result",
            "error": f"sample process exited with code {process.exitcode} without returning a result",
        }
        row = _row_with_partial_progress(row, progress_path)
        _write_sample_result(
            sample,
            output_dir,
            {
                "ok": False,
                "status": "error",
                "summary": row["summary"],
                "error": row["error"],
                "sample_runner": row,
                "case_agent_progress": _read_sample_progress(progress_path),
            },
        )
        return row
    if not bool(message.get("ok")):
        elapsed_ms = int((time.time() - started) * 1000)
        row = {
            "sample": sample.as_posix(),
            "status": "error",
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "summary": "sample_process_error",
            "error": str(message.get("error") or ""),
        }
        row = _row_with_partial_progress(row, progress_path)
        _write_sample_result(
            sample,
            output_dir,
            {
                "ok": False,
                "status": "error",
                "summary": row["summary"],
                "error": row["error"],
                "sample_runner": row,
                "case_agent_progress": _read_sample_progress(progress_path),
            },
        )
        return row
    row = message.get("row")
    return row if isinstance(row, dict) else {
        "sample": sample.as_posix(),
        "status": "error",
        "ok": False,
        "elapsed_ms": int((time.time() - started) * 1000),
        "summary": "sample_process_invalid_result",
        "error": "sample process returned a non-object row",
    }


def _run_in_parallel(samples: list[Path], worker, *args: Any) -> list[dict[str, Any]]:
    if len(samples) == 1:
        return [worker(samples[0], *args)]
    indexed_rows: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=SAMPLE_WORKER_COUNT) as executor:
        futures = {
            executor.submit(worker, sample, *args): index
            for index, sample in enumerate(samples)
        }
        for future in as_completed(futures):
            indexed_rows[futures[future]] = future.result()
    return [indexed_rows[index] for index in range(len(samples))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Local to Bangumi Case Agent mapping-only gate on raw sample-pool JSON.")
    parser.add_argument("--raw-root", type=Path, default=Path("tests/sample_pool/raw"))
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--sample-list", type=Path, default=None, help="JSON manifest with sample_path entries to run in exact manifest order.")
    parser.add_argument("--sample", action="append", default=[], help="Substring filter; can be repeated.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0, help="Skip this many selected samples before applying --limit.")
    parser.add_argument("--max-rounds", type=int, default=None, help="Temporarily override Case Agent max rounds for audit runs.")
    parser.add_argument("--sample-timeout-seconds", type=int, default=0, help="Terminate a sample run that exceeds this wall-clock limit. 0 disables the timeout.")
    parser.add_argument("--dry-build", action="store_true", help="Only build LocalEvidence from raw samples; do not call AI/Bangumi.")
    args = parser.parse_args()

    raw_root = args.raw_root
    limit = args.limit
    if limit is None and args.sample_list is None:
        limit = 3
    sample_metadata_by_key: dict[str, dict[str, Any]] = {}
    if args.sample_list is not None:
        sample_entries = _load_sample_list_entries(args.sample_list, raw_root)
        sample_metadata_by_key = {
            _sample_key(path): metadata
            for path, metadata in sample_entries
        }
        samples = _filter_samples(
            [path for path, _metadata in sample_entries],
            list(args.sample or []),
            limit,
            max(0, int(args.offset or 0)),
        )
    else:
        samples = _select_samples(raw_root, list(args.sample or []), limit, max(0, int(args.offset or 0)))
    output_dir = args.output_dir or _default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    if args.dry_build:
        rows = _run_in_parallel(samples, _dry_build_row)
    else:
        ai_client = AIClient()
        if not ai_client.is_available():
            summary = {"ok": False, "error": "AI client is not available", "samples": [path.as_posix() for path in samples]}
            (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2
        rows = _run_in_parallel(
            samples,
            _run_mapping_sample,
            output_dir,
            args.max_rounds,
            args.sample_timeout_seconds,
        )
    if sample_metadata_by_key:
        for row in rows:
            sample = str(row.get("sample") or "")
            metadata = sample_metadata_by_key.get(_sample_key(Path(sample))) if sample else None
            if not metadata:
                continue
            row["bucket"] = metadata.get("bucket", "")
            row["expected_policy"] = metadata.get("expected_policy", "")

    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row.get("status") or "unknown")] = counts.get(str(row.get("status") or "unknown"), 0) + 1
    summary_counts: dict[str, int] = {}
    for row in rows:
        summary_name = str(row.get("summary") or "")
        if summary_name:
            summary_counts[summary_name] = summary_counts.get(summary_name, 0) + 1
    strict_failures = [
        {
            "sample": row.get("sample"),
            "status": row.get("status"),
            "summary": row.get("summary"),
            "accepted_contract_ok": row.get("accepted_contract_ok"),
            "final_verifier_passed": row.get("final_verifier_passed"),
            "error": row.get("error"),
        }
        for row in rows
        if not _strict_row_ok(row)
    ]
    ai_call_counts_by_stage: dict[str, int] = {}
    ai_attempt_counts_by_stage: dict[str, int] = {}
    ai_provider_retry_counts_by_stage: dict[str, int] = {}
    orchestrator_tool_call_counts: dict[str, int] = {}
    orchestrator_session_mode_counts: dict[str, int] = {}
    tool_rejection_reason_counts: dict[str, int] = {}
    for row in rows:
        for key, value in (row.get("ai_call_counts_by_stage") if isinstance(row.get("ai_call_counts_by_stage"), dict) else {}).items():
            _add_count(ai_call_counts_by_stage, str(key), int(value or 0))
        for key, value in (row.get("ai_attempt_counts_by_stage") if isinstance(row.get("ai_attempt_counts_by_stage"), dict) else {}).items():
            _add_count(ai_attempt_counts_by_stage, str(key), int(value or 0))
        for key, value in (row.get("ai_provider_retry_counts_by_stage") if isinstance(row.get("ai_provider_retry_counts_by_stage"), dict) else {}).items():
            _add_count(ai_provider_retry_counts_by_stage, str(key), int(value or 0))
        for key, value in (row.get("orchestrator_tool_call_counts") if isinstance(row.get("orchestrator_tool_call_counts"), dict) else {}).items():
            _add_count(orchestrator_tool_call_counts, str(key), int(value or 0))
        for key, value in (row.get("tool_rejection_reason_counts") if isinstance(row.get("tool_rejection_reason_counts"), dict) else {}).items():
            _add_count(tool_rejection_reason_counts, str(key), int(value or 0))
        _add_count(orchestrator_session_mode_counts, str(row.get("orchestrator_session_mode") or "unknown"))
    total_input_tokens = sum(int(row.get("orchestrator_usage_input_tokens") or 0) for row in rows)
    total_cached_input_tokens = sum(int(row.get("orchestrator_provider_cached_input_tokens") or 0) for row in rows)
    summary = {
        "ok": not strict_failures,
        "raw_root": raw_root.as_posix(),
        "sample_list": args.sample_list.as_posix() if args.sample_list is not None else "",
        "output_dir": output_dir.as_posix(),
        "sample_count": len(rows),
        "worker_count": SAMPLE_WORKER_COUNT,
        "counts": counts,
        "summary_counts": summary_counts,
        "sample_list_bucket_counts": _count_rows_by_metadata(rows, sample_metadata_by_key, "bucket") if sample_metadata_by_key else {},
        "sample_list_expected_policy_counts": _count_rows_by_metadata(rows, sample_metadata_by_key, "expected_policy") if sample_metadata_by_key else {},
        "allowed_fail_closed_summaries": sorted(ALLOWED_FAIL_CLOSED_SUMMARIES),
        "accepted_contract_ok_count": sum(1 for row in rows if row.get("accepted_contract_ok")),
        "ai_call_count_total": sum(int(row.get("ai_call_count") or 0) for row in rows),
        "ai_attempt_count_estimate_total": sum(int(row.get("ai_attempt_count_estimate") or 0) for row in rows),
        "ai_provider_retry_count_total": sum(int(row.get("ai_provider_retry_count") or 0) for row in rows),
        "legacy_subagent_call_count_total": sum(int(row.get("legacy_subagent_call_count") or 0) for row in rows),
        "case_understanding_applied_count": sum(1 for row in rows if bool(row.get("case_understanding_applied"))),
        "ai_call_counts_by_stage": ai_call_counts_by_stage,
        "ai_attempt_counts_by_stage": ai_attempt_counts_by_stage,
        "ai_provider_retry_counts_by_stage": ai_provider_retry_counts_by_stage,
        "orchestrator_turn_count_total": sum(int(row.get("orchestrator_turn_count") or 0) for row in rows),
        "orchestrator_tool_call_counts": orchestrator_tool_call_counts,
        "orchestrator_session_mode_counts": orchestrator_session_mode_counts,
        "orchestrator_usage_total_tokens": sum(int(row.get("orchestrator_usage_total_tokens") or 0) for row in rows),
        "orchestrator_usage_input_tokens": total_input_tokens,
        "orchestrator_usage_output_tokens": sum(int(row.get("orchestrator_usage_output_tokens") or 0) for row in rows),
        "orchestrator_provider_cached_input_tokens": total_cached_input_tokens,
        "orchestrator_provider_cached_input_ratio": (total_cached_input_tokens / total_input_tokens) if total_input_tokens else 0.0,
        "orchestrator_min_cached_input_ratio_after_first_turn": min([float(row.get("orchestrator_min_cached_input_ratio_after_first_turn") or 0.0) for row in rows] or [0.0]),
        "orchestrator_low_cached_turn_count_after_first_turn": sum(int(row.get("orchestrator_low_cached_turn_count_after_first_turn") or 0) for row in rows),
        "orchestrator_stable_prefix_estimated_tokens": max([int(row.get("orchestrator_stable_prefix_estimated_tokens") or 0) for row in rows] or [0]),
        "orchestrator_turn_tail_estimated_tokens": max([int(row.get("orchestrator_turn_tail_estimated_tokens") or 0) for row in rows] or [0]),
        "orchestrator_max_turn_input_tokens": max([int(row.get("orchestrator_max_turn_input_tokens") or 0) for row in rows] or [0]),
        "orchestrator_provider_session_enabled_count": sum(1 for row in rows if bool(row.get("orchestrator_provider_session_enabled"))),
        "orchestrator_http_history_replay_count": sum(1 for row in rows if str(row.get("orchestrator_session_mode") or "") == "http_history_replay"),
        "orchestrator_prompt_cache_key_count": sum(1 for row in rows if bool(row.get("orchestrator_prompt_cache_key"))),
        "tool_rejection_count_total": sum(int(row.get("tool_rejection_count") or 0) for row in rows),
        "tool_rejection_reason_counts": tool_rejection_reason_counts,
        "attention_focus_change_count_total": sum(int(row.get("attention_focus_change_count") or 0) for row in rows),
        "agenda_open_count_total": sum(int(row.get("agenda_open_count") or 0) for row in rows),
        "agenda_closed_count_total": sum(int(row.get("agenda_closed_count") or 0) for row in rows),
        "noise_candidate_count_total": sum(int(row.get("noise_candidate_count") or 0) for row in rows),
        "stall_warning_count_total": sum(int(row.get("stall_warning_count") or 0) for row in rows),
        "no_progress_escape_count_total": sum(int(row.get("no_progress_escape_count") or 0) for row in rows),
        "weak_related_blocking_action_count_total": sum(int(row.get("weak_related_blocking_action_count") or 0) for row in rows),
        "case_resolution_goal_progress_count_total": sum(int(row.get("case_resolution_goal_progress_count") or 0) for row in rows),
        "same_blocker_strategy_change_required_count_total": sum(int(row.get("same_blocker_strategy_change_required_count") or 0) for row in rows),
        "case_resolution_goal_terminal_rejection_count_total": sum(int(row.get("case_resolution_goal_terminal_rejection_count") or 0) for row in rows),
        "obvious_terminal_fail_closed_count_total": sum(int(row.get("obvious_terminal_fail_closed_count") or 0) for row in rows),
        "repair_strategy_missing_count_total": sum(int(row.get("repair_strategy_missing_count") or 0) for row in rows),
        "compact_count_total": sum(int(row.get("compact_count") or 0) for row in rows),
        "strict_failure_count": len(strict_failures),
        "strict_failures": strict_failures,
        "rows": rows,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
