from __future__ import annotations

import argparse
import json
from collections import Counter
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
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _sample_files(run_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in run_dir.glob("*.json")
        if path.name != "summary.json"
    )


def _counter_add_dict(counter: Counter[str], values: dict[str, Any] | None) -> None:
    if not isinstance(values, dict):
        return
    for key, value in values.items():
        try:
            counter[str(key)] += int(value)
        except (TypeError, ValueError):
            continue


def _latest_mapping_intent_audit(audits: list[dict[str, Any]]) -> dict[str, Any]:
    for audit in reversed(audits):
        if audit.get("note") == "orchestrator_mapping_intents_result":
            return audit
    return {}


def _latest_finish_rejection(audits: list[dict[str, Any]]) -> dict[str, Any]:
    for audit in reversed(audits):
        if audit.get("note") == "orchestrator_tool_output_rejected" and audit.get("tool_name") == "finish_case":
            return audit
    return {}


def _rejection_counter(audits: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for audit in audits:
        if audit.get("note") != "orchestrator_tool_output_rejected":
            continue
        tool_name = str(audit.get("tool_name") or "unknown")
        reason = str(audit.get("reason") or "unknown")
        counter[f"{tool_name}:{reason}"] += 1
    return counter


def _sample_row(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    snapshot = _snapshot(payload)
    audits = _audits(snapshot)
    intent = _latest_mapping_intent_audit(audits)
    finish_rejection = _latest_finish_rejection(audits)
    finish_gate = finish_rejection.get("finish_gate") if isinstance(finish_rejection.get("finish_gate"), dict) else {}
    open_rows = finish_rejection.get("open_rows") if isinstance(finish_rejection.get("open_rows"), list) else intent.get("open_rows")
    if not isinstance(open_rows, list):
        open_rows = []
    return {
        "sample": path.stem,
        "status": str(snapshot.get("status") or payload.get("status") or "unknown"),
        "summary": str(snapshot.get("summary") or payload.get("summary") or ""),
        "main_file_count": snapshot.get("main_file_count") or snapshot.get("contract_main_file_count"),
        "mapped_file_count": snapshot.get("mapped_file_count"),
        "excluded_file_count": snapshot.get("excluded_file_count"),
        "unresolved_count": snapshot.get("unresolved_count"),
        "accepted_contract_ok": snapshot.get("accepted_contract_ok"),
        "final_verifier_passed": snapshot.get("final_verifier_passed"),
        "turn_count": snapshot.get("orchestrator_turn_count"),
        "tool_rejection_count": snapshot.get("tool_rejection_count"),
        "tool_counts": snapshot.get("orchestrator_tool_call_counts") if isinstance(snapshot.get("orchestrator_tool_call_counts"), dict) else {},
        "tool_sequence_tail": list(snapshot.get("orchestrator_tool_sequence") or [])[-10:],
        "ai_call_counts_by_stage": snapshot.get("ai_call_counts_by_stage") if isinstance(snapshot.get("ai_call_counts_by_stage"), dict) else {},
        "legacy_subagent_call_count": snapshot.get("legacy_subagent_call_count"),
        "bangumi_span_count": snapshot.get("bangumi_span_count"),
        "detail_equivalent_target_span_count": snapshot.get("detail_equivalent_target_span_count"),
        "latest_intent_status": intent.get("status"),
        "latest_intent_compiled_patch_count": intent.get("compiled_patch_count"),
        "latest_intent_blocked_count": intent.get("blocked_intent_count"),
        "latest_intent_blocked_issue_codes": intent.get("blocked_intent_issue_codes") or [],
        "latest_intent_patch_issue_codes": intent.get("patch_issue_codes") or [],
        "latest_intent_requested_evidence": intent.get("requested_evidence") or [],
        "latest_finish_rejection_reason": finish_rejection.get("reason"),
        "remaining_target_side_request_count": finish_gate.get("remaining_target_side_executable_request_count"),
        "remaining_target_side_request_ids": finish_gate.get("remaining_target_side_executable_request_ids") or [],
        "durable_draft_evidence_intent_count": finish_gate.get("durable_draft_evidence_intent_count"),
        "no_new_evidence_preconditions_ok": finish_gate.get("no_new_evidence_preconditions_ok"),
        "open_rows": open_rows[:4],
        "rejection_counts": dict(_rejection_counter(audits)),
    }


def _format_list(values: list[Any], *, limit: int = 8) -> str:
    items = [str(value) for value in values[:limit]]
    suffix = "" if len(values) <= limit else f" ... +{len(values) - limit}"
    return ", ".join(items) + suffix


def _print_text(summary: dict[str, Any], rows: list[dict[str, Any]], *, show_open_rows: bool) -> None:
    print(f"run_dir: {summary['run_dir']}")
    if summary.get("runner_summary"):
        runner = summary["runner_summary"]
        print(
            "runner: "
            f"ok={runner.get('ok')} sample_count={runner.get('sample_count')} worker_count={runner.get('worker_count')} "
            f"counts={runner.get('counts')} strict_failure_count={runner.get('strict_failure_count')}"
        )
        print(
            "ai: "
            f"total={runner.get('ai_call_count_total')} attempts={runner.get('ai_attempt_count_estimate_total')} "
            f"legacy={runner.get('legacy_subagent_call_count_total')} tools={runner.get('orchestrator_tool_call_counts')}"
        )
    print(f"derived_counts: {summary['derived_counts']}")
    print(f"summary_counts: {summary['summary_counts']}")
    print(f"tool_counts: {summary['tool_counts']}")
    print(f"rejection_counts: {summary['rejection_counts']}")
    print()

    for row in rows:
        print(
            f"- {row['sample']}: {row['status']} / {row['summary']} "
            f"mapped={row['mapped_file_count']} excluded={row['excluded_file_count']} unresolved={row['unresolved_count']} "
            f"turns={row['turn_count']} rejected={row['tool_rejection_count']}"
        )
        print(
            "  latest_intent: "
            f"status={row['latest_intent_status']} compiled={row['latest_intent_compiled_patch_count']} "
            f"blocked={row['latest_intent_blocked_count']} blocked_issues={row['latest_intent_blocked_issue_codes']} "
            f"patch_issues={row['latest_intent_patch_issue_codes']} requested={row['latest_intent_requested_evidence']}"
        )
        if row.get("latest_finish_rejection_reason"):
            print(
                "  finish_rejection: "
                f"{row['latest_finish_rejection_reason']} target_requests={row['remaining_target_side_request_count']} "
                f"ids=[{_format_list(list(row['remaining_target_side_request_ids']))}] "
                f"durable_intents={row['durable_draft_evidence_intent_count']} "
                f"no_new_ok={row['no_new_evidence_preconditions_ok']}"
            )
        print(f"  tool_tail: {' > '.join(row['tool_sequence_tail'])}")
        if show_open_rows and row.get("open_rows"):
            for open_row in row["open_rows"]:
                print(
                    "  open_row: "
                    f"{open_row.get('row_ref')} {open_row.get('local_ref')} "
                    f"status={open_row.get('status')} disposition={open_row.get('disposition')} "
                    f"candidates={open_row.get('candidate_target_refs')} requested={open_row.get('requested_request_types')} "
                    f"next={open_row.get('recommended_next')}"
                )


def summarize_run(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise NotADirectoryError(run_dir)
    rows = [_sample_row(path) for path in _sample_files(run_dir)]
    status_counts = Counter(row["status"] for row in rows)
    summary_counts = Counter(row["summary"] for row in rows)
    tool_counts: Counter[str] = Counter()
    rejection_counts: Counter[str] = Counter()
    for row in rows:
        _counter_add_dict(tool_counts, row.get("tool_counts"))
        _counter_add_dict(rejection_counts, row.get("rejection_counts"))
    runner_summary_path = run_dir / "summary.json"
    runner_summary = _load_json(runner_summary_path) if runner_summary_path.exists() else {}
    return {
        "run_dir": run_dir.as_posix(),
        "runner_summary": runner_summary,
        "derived_counts": dict(status_counts),
        "summary_counts": dict(summary_counts),
        "tool_counts": dict(tool_counts),
        "rejection_counts": dict(rejection_counts),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a Local to Bangumi sample-pool mapping run.")
    parser.add_argument("run_dir", help="Generated run directory containing summary.json and sample result JSON files.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    parser.add_argument("--show-open-rows", action="store_true", help="Include a short open-row action summary per sample.")
    args = parser.parse_args()

    summary = summarize_run(Path(args.run_dir))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_text(summary, list(summary["rows"]), show_open_rows=bool(args.show_open_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
