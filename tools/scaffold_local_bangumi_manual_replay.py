from __future__ import annotations

import argparse
import json
from pathlib import Path

from summarize_local_bangumi_human_trace import summarize_trace


def _load_json(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace")
    payload = json.JSONDecoder(strict=False).decode(text)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _raw_sample_summary(raw_sample: Path | None) -> dict[str, object]:
    if raw_sample is None:
        return {}
    payload = _load_json(raw_sample)
    files = payload.get("files")
    video_count = 0
    if isinstance(files, list):
        for item in files:
            path = str(item.get("path") if isinstance(item, dict) else item)
            if path.lower().endswith((".mkv", ".mp4", ".avi", ".m2ts", ".ts")):
                video_count += 1
    return {
        "raw_sample": raw_sample.as_posix(),
        "root_name": payload.get("root_name"),
        "file_count": len(files) if isinstance(files, list) else None,
        "video_file_count": video_count,
    }


def build_markdown(sample_result: Path, raw_sample: Path | None = None) -> str:
    trace = summarize_trace(sample_result)
    raw_summary = _raw_sample_summary(raw_sample)
    latest = trace.get("latest_submit_repair") if isinstance(trace.get("latest_submit_repair"), dict) else {}
    readiness = trace.get("resolution_readiness") if isinstance(trace.get("resolution_readiness"), dict) else {}
    submit_results = trace.get("submit_results") if isinstance(trace.get("submit_results"), list) else []
    tool_sequence = trace.get("tool_sequence") if isinstance(trace.get("tool_sequence"), list) else []
    runtime_review = trace.get("runtime_review") if isinstance(trace.get("runtime_review"), dict) else {}

    return "\n".join(
        [
            f"# Local→Bangumi Manual Replay: {sample_result.stem}",
            "",
            "## Inputs",
            "",
            f"- sample_result: `{sample_result.as_posix()}`",
            f"- raw_sample: `{raw_summary.get('raw_sample', '')}`",
            f"- root_name: `{raw_summary.get('root_name', '')}`",
            f"- file_count/video_file_count: `{raw_summary.get('file_count', '')}` / `{raw_summary.get('video_file_count', '')}`",
            "",
            "## Runner Result",
            "",
            f"- status: `{trace.get('status')}`",
            f"- summary: `{trace.get('summary')}`",
            f"- accepted_contract_ok: `{trace.get('accepted_contract_ok')}`",
            f"- final_verifier_passed: `{trace.get('final_verifier_passed')}`",
            f"- turns: `{trace.get('turn_count')}`",
            f"- tool_sequence: `{' > '.join(str(item) for item in tool_sequence)}`",
            "",
            "## Manual Human Path",
            "",
            "- TODO: Describe the evidence a human can see from the same raw sample, Bangumi search/inspect surfaces, and legal locators.",
            "- TODO: Name the work units and the semantic decision for each, without relying on evidence unavailable to the agent.",
            "",
            "## Agent Actual Trace",
            "",
            "```json",
            json.dumps(
                {
                    "submit_results": submit_results,
                    "latest_issue_counts": latest.get("issue_counts") if isinstance(latest, dict) else {},
                    "blocking_units": latest.get("blocking_units") if isinstance(latest, dict) else [],
                    "diagnostic_units": latest.get("diagnostic_units") if isinstance(latest, dict) else [],
                    "visible_target_surface_missing_units": latest.get("visible_target_surface_missing_units") if isinstance(latest, dict) else [],
                    "resolution_readiness": readiness,
                    "runtime_review": runtime_review,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            "## Divergence Point",
            "",
            "- TODO: First turn/work-unit where the agent diverged from the manual path.",
            "",
            "## Gap Category",
            "",
            "- TODO: Choose one: state_structure, tool_boundary, evidence_surface, prompt_overconstraint, verifier_feedback, provider_or_context_health, model_variance, safe_fail.",
            "",
            "## Generic Architecture Gap",
            "",
            "- is_generic_architecture_gap: TODO",
            "- proposed_fix_layer: TODO",
            "- proposed_generic_fix: TODO",
            "",
            "## Fixed Layer Boundary Check",
            "",
            "- TODO: Confirm the fix only changes locator/schema/support/coverage/overlap/duplicate/accounting/budget/loop/provider mechanics or evidence presentation.",
            "- TODO: Confirm it does not encode sample title, Bangumi id, or file-to-target rules.",
            "",
            "## Rerun Gate",
            "",
            "- command: TODO",
            "- output_dir: TODO",
            "- expected_check: accepted_contract_ok or concrete fail_closed blocker, plus unsafe-accepted spot check.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a manual replay markdown scaffold from a HumanCaseAgent trace.")
    parser.add_argument("sample_result_json", help="Generated sample_*.json from a focused gate run.")
    parser.add_argument("--raw-sample", help="Optional raw sample JSON used by the gate.")
    parser.add_argument("--output", required=True, help="Markdown file to write.")
    args = parser.parse_args()

    sample_result = Path(args.sample_result_json)
    raw_sample = Path(args.raw_sample) if args.raw_sample else None
    output = Path(args.output)
    output.write_text(build_markdown(sample_result, raw_sample), encoding="utf-8")
    print(output.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
