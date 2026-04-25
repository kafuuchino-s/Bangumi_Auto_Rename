from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import traceback
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ = sys.path.insert(0, str(PROJECT_ROOT))

from src.regression.lanes.rename import _execute_sample, build_rename_lane_observation
from src.regression.manifest import load_manifest
from src.regression.models import RenameSample

DEFAULT_MANIFEST = PROJECT_ROOT / "tests" / "sample_pool" / "manifest" / "manifest_main_flow_full.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tests" / "sample_pool" / "generated" / "main_flow_preview"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: str | Path | None) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(PROJECT_ROOT))
    except Exception:
        return str(p)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return payload if isinstance(payload, dict) else {}


def select_entries(
    entries: list[RenameSample],
    sample_ids: list[str] | None,
    max_samples: int | None,
) -> list[RenameSample]:
    selected = list(entries)
    if sample_ids:
        requested = set(sample_ids)
        selected = [entry for entry in selected if entry.sample_id in requested]
    if max_samples is not None:
        selected = selected[:max_samples]
    return selected


def build_observation(
    *,
    entry: RenameSample,
    execution_result: dict[str, Any],
    manifest_version: str,
) -> dict[str, Any]:
    observation = build_rename_lane_observation(
        entry=entry,
        execution_result=execution_result,
        manifest_version=manifest_version,
    )
    observation["artifact_type"] = "sample_pool_main_flow_observation"
    observation["preview_tool"] = "tools/run_sample_pool_main_flow_preview.py"
    observation["artifacts"] = {
        key: rel(value)
        for key, value in (observation.get("artifacts") or {}).items()
        if isinstance(value, str)
    }
    return observation


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _execute_entry_preview(
    *,
    entry: RenameSample,
    sample_root: Path,
    manifest_version: str,
) -> dict[str, Any]:
    try:
        execution_result = _execute_sample(entry, sample_root)
    except Exception as exc:
        execution_result = {
            "status": "runner_error",
            "infra_failure": True,
            "message": str(exc),
            "payload": {},
            "artifacts": {
                "sample_root": str(sample_root),
                "traceback": traceback.format_exc(),
            },
        }
    return build_observation(
        entry=entry,
        execution_result=execution_result,
        manifest_version=manifest_version,
    )


def _run_entry_worker(payload: tuple[dict[str, Any], str, str]) -> dict[str, Any]:
    entry_payload, sample_root_raw, manifest_version = payload
    return _execute_entry_preview(
        entry=RenameSample(**entry_payload),
        sample_root=Path(sample_root_raw),
        manifest_version=manifest_version,
    )


def run_preview(
    *,
    manifest: Path,
    output_dir: Path,
    sample_ids: list[str] | None,
    max_samples: int | None,
    workers: int = 1,
) -> dict[str, Any]:
    manifest_version, entries = load_manifest(manifest)
    selected_entries = select_entries(entries, sample_ids, max_samples)
    observations_dir = output_dir / "observations"
    sandbox_root = output_dir / "sandbox"
    observations_dir.mkdir(parents=True, exist_ok=True)
    sandbox_root.mkdir(parents=True, exist_ok=True)

    observations: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    final_type_counts: Counter[str] = Counter()

    resolved_workers = max(1, int(workers or 1))
    if resolved_workers == 1 or len(selected_entries) <= 1:
        observations = [
            _execute_entry_preview(
                entry=entry,
                sample_root=sandbox_root / entry.sample_id,
                manifest_version=manifest_version,
            )
            for entry in selected_entries
        ]
    else:
        indexed_observations: dict[int, dict[str, Any]] = {}
        worker_payloads = [
            (
                index,
                (
                    entry.to_dict(),
                    str(sandbox_root / entry.sample_id),
                    manifest_version,
                ),
            )
            for index, entry in enumerate(selected_entries)
        ]
        with ProcessPoolExecutor(max_workers=min(resolved_workers, len(selected_entries))) as executor:
            future_to_index = {
                executor.submit(_run_entry_worker, payload): index
                for index, payload in worker_payloads
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    indexed_observations[index] = future.result()
                except Exception as exc:
                    entry = selected_entries[index]
                    indexed_observations[index] = build_observation(
                        entry=entry,
                        execution_result={
                            "status": "runner_error",
                            "infra_failure": True,
                            "message": str(exc),
                            "payload": {},
                            "artifacts": {"sample_root": str(sandbox_root / entry.sample_id)},
                        },
                        manifest_version=manifest_version,
                    )
        observations = [indexed_observations[index] for index in range(len(selected_entries))]

    for observation in observations:
        sample_id = str(observation.get("sample_id") or "unknown")
        write_json(observations_dir / f"{sample_id}.main_flow_observation.json", observation)
        status_counts[str(observation.get("process_status") or "unknown")] += 1
        final_type = str((observation.get("summary") or {}).get("final_type") or "unknown")
        final_type_counts[final_type] += 1

    summary = {
        "artifact_type": "sample_pool_main_flow_preview_summary",
        "schema_version": 1,
        "runner_kind": "main_flow_preview",
        "main_flow_verified": True,
        "main_flow_observer": True,
        "generated_at": utc_now(),
        "manifest": rel(manifest),
        "manifest_version": manifest_version,
        "selected_count": len(selected_entries),
        "completed_count": len(observations),
        "workers": min(resolved_workers, max(1, len(selected_entries))),
        "status_counts": dict(status_counts),
        "final_type_counts": dict(final_type_counts),
        "observations_dir": rel(observations_dir),
        "sample_ids": [entry.sample_id for entry in selected_entries],
    }
    write_json(output_dir / "sample_pool_main_flow_preview_summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run sample-pool samples through the main Rename flow in sandbox preview mode.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--sample-id", action="append", default=None, help="Run one or more sample IDs. Repeatable.")
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1, help="Run samples in separate worker processes. Use 10 for full sample-pool reruns.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run_preview(
        manifest=args.manifest,
        output_dir=args.output_dir,
        sample_ids=args.sample_id,
        max_samples=args.max_samples,
        workers=args.workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
