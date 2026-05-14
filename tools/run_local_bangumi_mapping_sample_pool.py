from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.ai.client import AIClient
from src.bangumi.client import BangumiClient
from src.rename.case_agent.local_bangumi_entry import run_local_bangumi_case_agent_mapping
from src.rename.local_evidence import LocalEvidence, LocalFileEvidence
from src.rename.local_supplemental_filter import classify_local_video_supplemental
from src.rename.utils import VIDEO_SUFFIX


SAMPLE_WORKER_COUNT = 20
SAMPLE_PROVIDER_NO_RESPONSE_RETRIES = 1
ALLOWED_FAIL_CLOSED_SUMMARIES = {
    "budget_exhausted",
    "no_new_evidence",
    "semantic_target_conflict",
    "child_case_unresolved",
    "provider_retry_exhausted",
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
    call_count = 0
    retry_count = 0
    legacy_subagent_call_count = 0
    for audit in audits:
        if not isinstance(audit, dict):
            continue
        if audit.get("note") == "orchestrator_agent_called":
            stage = "orchestrator_agent"
            call_count += 1
            _add_count(call_counts_by_stage, stage)
            _add_count(attempt_counts_by_stage, stage)
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
    }


def _sample_row(sample_path: Path, result: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else result
    status = str(snapshot.get("status") or result.get("status") or "unknown")
    accepted_contract_ok = _accepted_contract_ok(snapshot) if isinstance(snapshot, dict) else False
    ai_stats = _case_agent_ai_call_stats(snapshot) if isinstance(snapshot, dict) else {}
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
        "briefing_memory_lost": snapshot.get("briefing_memory_lost") if isinstance(snapshot, dict) else None,
        "case_planning_action": snapshot.get("case_planning_action") if isinstance(snapshot, dict) else None,
        "summary": snapshot.get("summary") or result.get("summary") if isinstance(snapshot, dict) else result.get("summary"),
        "orchestrator_turn_count": snapshot.get("orchestrator_turn_count") if isinstance(snapshot, dict) else None,
        "orchestrator_tool_call_counts": snapshot.get("orchestrator_tool_call_counts") if isinstance(snapshot, dict) else None,
        "orchestrator_tool_sequence": snapshot.get("orchestrator_tool_sequence") if isinstance(snapshot, dict) else None,
        "tool_rejection_count": snapshot.get("tool_rejection_count") if isinstance(snapshot, dict) else None,
        "compact_count": snapshot.get("compact_count") if isinstance(snapshot, dict) else None,
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


def _run_mapping_sample(sample: Path, output_dir: Path) -> dict[str, Any]:
    started = time.time()
    retry_reasons: list[str] = []
    try:
        evidence = local_evidence_from_raw_sample(sample)
        result: dict[str, Any] = {}
        for attempt in range(SAMPLE_PROVIDER_NO_RESPONSE_RETRIES + 1):
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
        elapsed_ms = int((time.time() - started) * 1000)
        if retry_reasons:
            result = {
                **result,
                "sample_runner_retry_count": len(retry_reasons),
                "sample_runner_retry_reasons": retry_reasons,
            }
        _write_sample_result(sample, output_dir, result)
        row = _sample_row(sample, result, elapsed_ms)
        if retry_reasons:
            row["sample_runner_retry_count"] = len(retry_reasons)
            row["sample_runner_retry_reasons"] = retry_reasons
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


def _run_in_parallel(samples: list[Path], worker, *args: Any) -> list[dict[str, Any]]:
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
    parser.add_argument("--cache-mode", choices=["read-write", "cache-only", "refresh", "off"], default=None)
    parser.add_argument("--dry-build", action="store_true", help="Only build LocalEvidence from raw samples; do not call AI/Bangumi.")
    args = parser.parse_args()

    if args.cache_mode:
        os.environ["BAR_AI_RESPONSE_CACHE_MODE"] = args.cache_mode

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
        rows = _run_in_parallel(samples, _run_mapping_sample, output_dir)
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
    for row in rows:
        for key, value in (row.get("ai_call_counts_by_stage") if isinstance(row.get("ai_call_counts_by_stage"), dict) else {}).items():
            _add_count(ai_call_counts_by_stage, str(key), int(value or 0))
        for key, value in (row.get("ai_attempt_counts_by_stage") if isinstance(row.get("ai_attempt_counts_by_stage"), dict) else {}).items():
            _add_count(ai_attempt_counts_by_stage, str(key), int(value or 0))
        for key, value in (row.get("ai_provider_retry_counts_by_stage") if isinstance(row.get("ai_provider_retry_counts_by_stage"), dict) else {}).items():
            _add_count(ai_provider_retry_counts_by_stage, str(key), int(value or 0))
        for key, value in (row.get("orchestrator_tool_call_counts") if isinstance(row.get("orchestrator_tool_call_counts"), dict) else {}).items():
            _add_count(orchestrator_tool_call_counts, str(key), int(value or 0))
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
        "ai_call_counts_by_stage": ai_call_counts_by_stage,
        "ai_attempt_counts_by_stage": ai_attempt_counts_by_stage,
        "ai_provider_retry_counts_by_stage": ai_provider_retry_counts_by_stage,
        "orchestrator_turn_count_total": sum(int(row.get("orchestrator_turn_count") or 0) for row in rows),
        "orchestrator_tool_call_counts": orchestrator_tool_call_counts,
        "tool_rejection_count_total": sum(int(row.get("tool_rejection_count") or 0) for row in rows),
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
