from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    payload = json.JSONDecoder(strict=False).decode(text)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else payload


def _audits(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    raw = snapshot.get("case_judge_request_audits")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _compact_units(units: object, *, limit: int = 8) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(units, list):
        return rows
    for unit in units:
        if not isinstance(unit, dict):
            continue
        rows.append(
            {
                key: unit.get(key)
                for key in (
                    "unit",
                    "local",
                    "target",
                    "issue",
                    "issue_codes",
                    "available_target_episode_numbers",
                    "search_queries_to_try",
                    "local_slice_mapping_options",
                )
                if key in unit
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _int_value(payload: dict[str, Any], key: str) -> int:
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(payload: dict[str, Any], key: str) -> float:
    try:
        return float(payload.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _explicit_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _accepted_contract_ok(snapshot: dict[str, Any], payload: dict[str, Any]) -> bool:
    explicit = _explicit_value(snapshot.get("accepted_contract_ok"), payload.get("accepted_contract_ok"))
    if explicit is not None:
        return bool(explicit)
    if str(snapshot.get("status") or payload.get("status") or "") != "accepted":
        return False
    main_count = _int_value(snapshot, "main_file_count") or _int_value(snapshot, "contract_main_file_count")
    accounted = _int_value(snapshot, "accounted_for_count")
    mapped = _int_value(snapshot, "mapped_file_count")
    excluded = _int_value(snapshot, "excluded_file_count")
    unresolved = _int_value(snapshot, "unresolved_count")
    open_count = _int_value(snapshot, "open_file_count")
    needs_more = _int_value(snapshot, "needs_more_evidence_file_count")
    unaligned = _int_value(snapshot, "unaligned_file_count")
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


def summarize_trace(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    snapshot = _snapshot(payload)
    audits = _audits(snapshot)
    tool_turns: list[dict[str, Any]] = []
    submit_results: list[dict[str, Any]] = []
    tool_rejections: list[dict[str, Any]] = []
    latest_submit_repair: dict[str, Any] = {}
    latest_session_summary: dict[str, Any] = {}

    for audit in audits:
        note = audit.get("note")
        if note == "orchestrator_agent_called":
            tool_turns.append(
                {
                    "turn": audit.get("turn_count"),
                    "tool": audit.get("tool_name"),
                    "consecutive_same_tool_count": audit.get("consecutive_same_tool_count"),
                    "single_tool_loop_suspected_count": audit.get("single_tool_loop_suspected_count"),
                }
            )
        elif note == "human_case_agent_submit_result":
            submit_results.append(
                {
                    "index": len(submit_results) + 1,
                    "accepted": audit.get("accepted"),
                    "issue_counts": audit.get("issue_counts") or {},
                    "repeated_submit_rejection": audit.get("repeated_submit_rejection"),
                }
            )
        elif note == "human_case_agent_tool_rejected":
            tool_rejections.append(
                {
                    "tool": audit.get("tool_name") or audit.get("rejected_tool"),
                    "reason": audit.get("reason") or audit.get("issue"),
                }
            )
        elif note == "orchestrator_agent_session_summary":
            latest_session_summary = audit
        if isinstance(audit.get("latest_submit_repair"), dict):
            latest_submit_repair = audit["latest_submit_repair"]
    if not latest_submit_repair and isinstance(latest_session_summary.get("latest_submit_repair"), dict):
        latest_submit_repair = latest_session_summary["latest_submit_repair"]

    readiness = snapshot.get("resolution_readiness_summary")
    if not isinstance(readiness, dict):
        readiness = {}
    submit_rejection_count = _int_value(snapshot, "submit_rejection_count")
    if not submit_rejection_count:
        submit_rejection_count = sum(1 for row in submit_results if not row.get("accepted"))
    accepted_contract_ok = _accepted_contract_ok(snapshot, payload)
    final_verifier_passed = bool(
        _explicit_value(snapshot.get("final_verifier_passed"), payload.get("final_verifier_passed"))
    )

    loop_health = {
        "tool_rejection_count": _int_value(snapshot, "tool_rejection_count"),
        "stall_warning_count": _int_value(snapshot, "stall_warning_count"),
        "stall_suspected_count": _int_value(snapshot, "stall_suspected_count"),
        "near_turn_limit_unhealthy_count": _int_value(snapshot, "near_turn_limit_unhealthy_count"),
        "no_progress_escape_count": _int_value(snapshot, "no_progress_escape_count") or _int_value(latest_session_summary, "no_progress_escape_count"),
        "recovery_frontier_switch_count": _int_value(snapshot, "recovery_frontier_switch_count") or _int_value(latest_session_summary, "recovery_frontier_switch_count"),
        "exact_fail_closed_after_frontier_exhausted_count": _int_value(
            snapshot,
            "exact_fail_closed_after_frontier_exhausted_count",
        )
        or _int_value(latest_session_summary, "exact_fail_closed_after_frontier_exhausted_count"),
        "weak_related_blocking_action_count": _int_value(snapshot, "weak_related_blocking_action_count") or _int_value(latest_session_summary, "weak_related_blocking_action_count"),
        "human_next_action_blocked_no_new_evidence_count": _int_value(
            snapshot,
            "human_next_action_blocked_no_new_evidence_count",
        ),
        "context_soft_limit_hit_count": _int_value(snapshot, "context_soft_limit_hit_count"),
        "context_hard_limit_hit_count": _int_value(snapshot, "context_hard_limit_hit_count"),
        "compact_count": _int_value(snapshot, "compact_count"),
        "repeated_submit_rejection_count": sum(
            1 for row in submit_results if row.get("repeated_submit_rejection")
        ),
    }
    runtime_review = {
        "status": snapshot.get("status") or payload.get("status"),
        "accepted_contract_ok": accepted_contract_ok,
        "final_verifier_passed": final_verifier_passed,
        "tool_sequence": snapshot.get("orchestrator_tool_sequence") or [],
        "turn_count": snapshot.get("orchestrator_turn_count"),
        "submit_rejection_count": submit_rejection_count,
        "loop_health": loop_health,
        "legacy_subagent_call_count": _int_value(snapshot, "legacy_subagent_call_count"),
        "legacy_orchestrator_main_path_used": bool(snapshot.get("legacy_orchestrator_main_path_used")),
        "provider_cached_input_ratio": _float_value(snapshot, "orchestrator_provider_cached_input_ratio"),
        "local_response_cache_file_count": _int_value(snapshot, "local_response_cache_file_count"),
        "manual_vs_agent_divergence_point": snapshot.get("manual_vs_agent_divergence_point") or "",
        "resolution_readiness_summary": readiness,
    }

    return {
        "sample": path.as_posix(),
        "status": snapshot.get("status") or payload.get("status"),
        "summary": snapshot.get("summary") or payload.get("summary"),
        "accepted_contract_ok": accepted_contract_ok,
        "final_verifier_passed": final_verifier_passed,
        "turn_count": snapshot.get("orchestrator_turn_count"),
        "tool_sequence": snapshot.get("orchestrator_tool_sequence") or [],
        "submit_rejection_count": submit_rejection_count,
        "runtime_review": runtime_review,
        "attention_focus_change_count": _int_value(snapshot, "attention_focus_change_count"),
        "agenda_open_count": _int_value(snapshot, "agenda_open_count"),
        "agenda_closed_count": _int_value(snapshot, "agenda_closed_count"),
        "noise_candidate_count": _int_value(snapshot, "noise_candidate_count"),
        "high_quality_candidate_count_by_turn": snapshot.get("high_quality_candidate_count_by_turn") or latest_session_summary.get("high_quality_candidate_count_by_turn") or [],
        "diagnostic_candidate_count_by_turn": snapshot.get("diagnostic_candidate_count_by_turn") or latest_session_summary.get("diagnostic_candidate_count_by_turn") or [],
        "noisy_candidate_count_by_turn": snapshot.get("noisy_candidate_count_by_turn") or latest_session_summary.get("noisy_candidate_count_by_turn") or [],
        "blocking_action_count_by_turn": snapshot.get("blocking_action_count_by_turn") or latest_session_summary.get("blocking_action_count_by_turn") or [],
        "no_progress_escape_count": _int_value(snapshot, "no_progress_escape_count") or _int_value(latest_session_summary, "no_progress_escape_count"),
        "recovery_frontier_switch_count": _int_value(snapshot, "recovery_frontier_switch_count") or _int_value(latest_session_summary, "recovery_frontier_switch_count"),
        "exact_fail_closed_after_frontier_exhausted_count": _int_value(
            snapshot,
            "exact_fail_closed_after_frontier_exhausted_count",
        )
        or _int_value(latest_session_summary, "exact_fail_closed_after_frontier_exhausted_count"),
        "weak_related_blocking_action_count": _int_value(snapshot, "weak_related_blocking_action_count") or _int_value(latest_session_summary, "weak_related_blocking_action_count"),
        "legacy_subagent_call_count": _int_value(snapshot, "legacy_subagent_call_count"),
        "legacy_orchestrator_main_path_used": bool(snapshot.get("legacy_orchestrator_main_path_used")),
        "provider_cached_input_ratio": _float_value(snapshot, "orchestrator_provider_cached_input_ratio"),
        "local_response_cache_file_count": _int_value(snapshot, "local_response_cache_file_count"),
        "manual_vs_agent_divergence_point": snapshot.get("manual_vs_agent_divergence_point") or "",
        "loop_health": loop_health,
        "tool_turns": tool_turns,
        "submit_results": submit_results,
        "tool_rejections": tool_rejections,
        "stall_warning_count": snapshot.get("stall_warning_count"),
        "near_turn_limit_unhealthy_count": snapshot.get("near_turn_limit_unhealthy_count"),
        "resolution_readiness": readiness,
        "latest_submit_repair": {
            "issue_counts": latest_submit_repair.get("issue_counts") or {},
            "required_missing_work_units": _compact_units(latest_submit_repair.get("required_missing_work_units")),
            "blocking_units": _compact_units(latest_submit_repair.get("blocking_units")),
            "diagnostic_units": _compact_units(latest_submit_repair.get("diagnostic_units")),
            "visible_target_surface_missing_units": _compact_units(
                latest_submit_repair.get("visible_target_surface_missing_units")
            ),
            "target_surface_actions": latest_submit_repair.get("target_surface_actions") or [],
            "blocking_target_surface_actions": latest_submit_repair.get("blocking_target_surface_actions") or [],
            "diagnostic_target_surface_actions": latest_submit_repair.get("diagnostic_target_surface_actions") or [],
            "rejected_or_noisy_actions": latest_submit_repair.get("rejected_or_noisy_actions") or [],
            "repair_frontier": latest_submit_repair.get("repair_frontier") or [],
            "search_queries_to_try": latest_submit_repair.get("search_queries_to_try") or [],
            "repeat_rejection_warning": latest_submit_repair.get("repeat_rejection_warning"),
        },
    }


def _print_text(summary: dict[str, Any]) -> None:
    print(f"sample: {summary['sample']}")
    print(
        "status: "
        f"{summary.get('status')} / {summary.get('summary')} "
        f"accepted_contract_ok={summary.get('accepted_contract_ok')} verifier={summary.get('final_verifier_passed')}"
    )
    print(f"turns: {summary.get('turn_count')} sequence={' > '.join(summary.get('tool_sequence') or [])}")
    print(
        "runtime: "
        f"submit_rejections={summary.get('submit_rejection_count')} "
        f"legacy_subagents={summary.get('legacy_subagent_call_count')} "
        f"provider_cache_ratio={summary.get('provider_cached_input_ratio'):.3f} "
        f"local_response_cache_files={summary.get('local_response_cache_file_count')}"
    )
    loop_health = summary.get("loop_health") if isinstance(summary.get("loop_health"), dict) else {}
    if loop_health:
        print(f"loop_health: {json.dumps(loop_health, ensure_ascii=False)}")
    print(
        "workspace_health: "
        f"focus_changes={summary.get('attention_focus_change_count')} "
        f"agenda_open={summary.get('agenda_open_count')} "
        f"agenda_closed={summary.get('agenda_closed_count')} "
        f"noise_candidates={summary.get('noise_candidate_count')}"
    )
    print(
        "recovery_health: "
        f"high_quality_by_turn={summary.get('high_quality_candidate_count_by_turn')} "
        f"diagnostic_by_turn={summary.get('diagnostic_candidate_count_by_turn')} "
        f"noisy_by_turn={summary.get('noisy_candidate_count_by_turn')} "
        f"blocking_actions_by_turn={summary.get('blocking_action_count_by_turn')} "
        f"no_progress_escapes={summary.get('no_progress_escape_count')} "
        f"weak_related_blocking={summary.get('weak_related_blocking_action_count')}"
    )
    if summary.get("tool_rejections"):
        print(f"tool_rejections: {summary['tool_rejections']}")
    print("submit_results:")
    for row in summary.get("submit_results") or []:
        print(
            f"  #{row['index']} accepted={row.get('accepted')} "
            f"repeated={row.get('repeated_submit_rejection')} issues={row.get('issue_counts')}"
        )
    latest = summary.get("latest_submit_repair") if isinstance(summary.get("latest_submit_repair"), dict) else {}
    print(f"latest_issue_counts: {latest.get('issue_counts')}")
    for key in (
        "required_missing_work_units",
        "blocking_units",
        "diagnostic_units",
        "visible_target_surface_missing_units",
        "target_surface_actions",
        "search_queries_to_try",
    ):
        value = latest.get(key)
        if value:
            print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    readiness = summary.get("resolution_readiness") if isinstance(summary.get("resolution_readiness"), dict) else {}
    if readiness:
        print(f"resolution_readiness.status: {readiness.get('status')}")
        print(f"resolution_readiness.blocking_work_units: {readiness.get('blocking_work_units')}")
        print(f"resolution_readiness.mechanical_gaps: {readiness.get('mechanical_gaps')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize one HumanCaseAgent sample trace mechanically.")
    parser.add_argument("sample_result_json", help="Generated sample_*.json from run_local_bangumi_mapping_sample_pool.py.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    summary = summarize_trace(Path(args.sample_result_json))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_text(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
