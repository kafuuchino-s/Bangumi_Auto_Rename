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


SAMPLE_WORKER_COUNT = 10
ALLOWED_FAIL_CLOSED_SUMMARIES = {
    "budget_exhausted",
    "no_new_evidence",
    "semantic_target_conflict",
    "child_case_unresolved",
    "provider_retry_exhausted",
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


def _sample_row(sample_path: Path, result: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else result
    status = str(snapshot.get("status") or result.get("status") or "unknown")
    accepted_contract_ok = _accepted_contract_ok(snapshot) if isinstance(snapshot, dict) else False
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
        "case_planning_action": snapshot.get("case_planning_action") if isinstance(snapshot, dict) else None,
        "summary": snapshot.get("summary") or result.get("summary") if isinstance(snapshot, dict) else result.get("summary"),
    }


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
    try:
        evidence = local_evidence_from_raw_sample(sample)
        result = run_local_bangumi_case_agent_mapping(
            local_evidence=evidence,
            bangumi_contexts=[],
            ai_client=AIClient(),
            source_path=sample,
            bangumi_client=BangumiClient(),
        )
        elapsed_ms = int((time.time() - started) * 1000)
        sample_id = sample.stem
        (output_dir / f"{sample_id}.json").write_text(
            json.dumps(_json_safe(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return _sample_row(sample, result, elapsed_ms)
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
    parser.add_argument("--sample", action="append", default=[], help="Substring filter; can be repeated.")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--offset", type=int, default=0, help="Skip this many selected samples before applying --limit.")
    parser.add_argument("--cache-mode", choices=["read-write", "cache-only", "refresh", "off"], default=None)
    parser.add_argument("--dry-build", action="store_true", help="Only build LocalEvidence from raw samples; do not call AI/Bangumi.")
    args = parser.parse_args()

    if args.cache_mode:
        os.environ["BAR_AI_RESPONSE_CACHE_MODE"] = args.cache_mode

    raw_root = args.raw_root
    samples = _select_samples(raw_root, list(args.sample or []), args.limit, max(0, int(args.offset or 0)))
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
    summary = {
        "ok": not strict_failures,
        "raw_root": raw_root.as_posix(),
        "output_dir": output_dir.as_posix(),
        "sample_count": len(rows),
        "worker_count": SAMPLE_WORKER_COUNT,
        "counts": counts,
        "summary_counts": summary_counts,
        "allowed_fail_closed_summaries": sorted(ALLOWED_FAIL_CLOSED_SUMMARIES),
        "accepted_contract_ok_count": sum(1 for row in rows if row.get("accepted_contract_ok")),
        "strict_failure_count": len(strict_failures),
        "strict_failures": strict_failures,
        "rows": rows,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
