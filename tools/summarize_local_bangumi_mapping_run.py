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


def _latest_submit_rejection(audits: list[dict[str, Any]]) -> dict[str, Any]:
    for audit in reversed(audits):
        if audit.get("note") != "pi_case_agent_tool_call" or audit.get("tool_name") != "submit_mapping_draft":
            continue
        summary = audit.get("result_summary") if isinstance(audit.get("result_summary"), dict) else {}
        if summary.get("accepted") is False:
            return audit
    return {}


def _rejection_counter(audits: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for audit in audits:
        if audit.get("note") != "pi_case_agent_tool_call" or bool(audit.get("accepted")):
            continue
        tool_name = str(audit.get("tool_name") or "unknown")
        summary = audit.get("result_summary") if isinstance(audit.get("result_summary"), dict) else {}
        reason = str(summary.get("status") or summary.get("error") or "rejected")
        counter[f"{tool_name}:{reason}"] += 1
    return counter


def _sample_row(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    snapshot = _snapshot(payload)
    audits = _audits(snapshot)
    submit_rejection = _latest_submit_rejection(audits)
    submit_summary = submit_rejection.get("result_summary") if isinstance(submit_rejection.get("result_summary"), dict) else {}
    return {
        "sample": path.stem,
        "status": str(snapshot.get("status") or payload.get("status") or "unknown"),
        "summary": str(snapshot.get("summary") or payload.get("summary") or ""),
        "main_file_count": snapshot.get("main_file_count") or snapshot.get("contract_main_file_count"),
        "mapped_file_count": snapshot.get("mapped_file_count"),
        "mapped_target_episode_count": snapshot.get("mapped_target_episode_count"),
        "single_file_multi_episode_count": snapshot.get("single_file_multi_episode_count"),
        "excluded_file_count": snapshot.get("excluded_file_count"),
        "resolved_unmapped_file_count": snapshot.get("resolved_unmapped_file_count") or snapshot.get("excluded_file_count"),
        "unresolved_count": snapshot.get("unresolved_count"),
        "accepted_contract_ok": snapshot.get("accepted_contract_ok"),
        "final_verifier_passed": snapshot.get("final_verifier_passed"),
        "case_agent_mode": snapshot.get("case_agent_mode"),
        "pi_run_dir": snapshot.get("pi_run_dir"),
        "pi_provider": snapshot.get("pi_provider"),
        "pi_model": snapshot.get("pi_model"),
        "turn_count": (snapshot.get("pi_runtime_result") or {}).get("runner_result", {}).get("turn_count") if isinstance(snapshot.get("pi_runtime_result"), dict) else None,
        "tool_rejection_count": snapshot.get("tool_rejection_count"),
        "tool_counts": snapshot.get("pi_tool_call_counts") if isinstance(snapshot.get("pi_tool_call_counts"), dict) else {},
        "tool_sequence_tail": list(snapshot.get("pi_tool_sequence") or [])[-10:],
        "ai_call_counts_by_stage": snapshot.get("ai_call_counts_by_stage") if isinstance(snapshot.get("ai_call_counts_by_stage"), dict) else {},
        "bangumi_span_count": snapshot.get("bangumi_span_count"),
        "detail_equivalent_target_span_count": snapshot.get("detail_equivalent_target_span_count"),
        "latest_submit_rejection_status": submit_summary.get("status"),
        "latest_submit_rejection_summary": submit_summary.get("summary"),
        "latest_submit_verifier_passed": submit_summary.get("verifier_passed"),
        "latest_submit_verifier_issue_count": submit_summary.get("verifier_issue_count"),
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
            f"tools={runner.get('pi_tool_call_counts')}"
        )
    print(f"derived_counts: {summary['derived_counts']}")
    print(f"summary_counts: {summary['summary_counts']}")
    print(f"tool_counts: {summary['tool_counts']}")
    print(f"rejection_counts: {summary['rejection_counts']}")
    print()

    for row in rows:
        print(
            f"- {row['sample']}: {row['status']} / {row['summary']} "
            f"mapped={row['mapped_file_count']} resolved_unmapped={row['resolved_unmapped_file_count']} "
            f"excluded_compat={row['excluded_file_count']} unresolved={row['unresolved_count']} "
            f"mode={row['case_agent_mode']} turns={row['turn_count']} rejected={row['tool_rejection_count']}"
            f" pi_model={row.get('pi_provider')}/{row.get('pi_model')}"
        )
        if row.get("latest_submit_rejection_status"):
            print(
                "  submit_rejection: "
                f"status={row['latest_submit_rejection_status']} summary={row['latest_submit_rejection_summary']} "
                f"verifier_passed={row['latest_submit_verifier_passed']} "
                f"issues={row['latest_submit_verifier_issue_count']}"
            )
        print(f"  tool_tail: {' > '.join(row['tool_sequence_tail'])}")


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
