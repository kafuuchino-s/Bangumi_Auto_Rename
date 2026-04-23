from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .baseline import load_baseline_record
from .lanes.rename import run_rename_lane
from .manifest import DEFAULT_MANIFEST_PATH, build_snapshot, filter_manifest_entries, load_manifest
from .models import CANONICAL_MODE_CHOICES, RunContext, RunReport
from .report import write_report_json, write_report_markdown
from ..config.config_manager import cm


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGRESSION_ROOT = PROJECT_ROOT / 'data' / 'regression'
DEFAULT_BASELINE_ROOT = DEFAULT_REGRESSION_ROOT / 'baselines'
DEFAULT_ARTIFACTS_ROOT = DEFAULT_REGRESSION_ROOT / 'runs'


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_runtime_signature() -> tuple[dict[str, Any], dict[str, Any]]:
    ai_model_info = {
        'model': cm.get_config('ai_model'),
        'base_url': cm.get_config('ai_base_url'),
        'output_format': cm.get_config('openai_output_format'),
        'api_interface': cm.get_config('openai_api_interface'),
    }
    provider_version_info = {
        'subtitle_auto_fetch_provider': cm.get_config('subtitle_auto_fetch_provider'),
        'subtitle_sync_mode': cm.get_config('subtitle_sync_mode'),
    }
    return ai_model_info, provider_version_info


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write('\n')


def run_rename_regression(
    *,
    mode: str,
    manifest: Path | None = None,
    baseline_root: Path | None = None,
    artifacts_root: Path | None = None,
    sample_id: str | None = None,
    max_samples: int | None = None,
) -> dict[str, Any]:
    if mode not in CANONICAL_MODE_CHOICES:
        raise ValueError(f'Unsupported mode: {mode}')
    canonical_mode = mode

    resolved_baseline_root = baseline_root or DEFAULT_BASELINE_ROOT
    resolved_artifacts_root = artifacts_root or DEFAULT_ARTIFACTS_ROOT
    manifest_path = manifest or DEFAULT_MANIFEST_PATH

    run_id = f'{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}-{uuid4().hex[:8]}'
    run_dir = resolved_artifacts_root / run_id
    sample_results_dir = run_dir / 'sample_results'
    sandbox_root = run_dir / 'sandbox'
    for path in [run_dir, sample_results_dir, sandbox_root]:
        path.mkdir(parents=True, exist_ok=True)

    manifest_version, manifest_entries = load_manifest(manifest_path)
    selected_entries, selection_notes = filter_manifest_entries(
        manifest_entries,
        mode=canonical_mode,
        sample_id=sample_id,
        max_samples=max_samples,
    )
    snapshot = build_snapshot(
        manifest_version=manifest_version,
        mode=canonical_mode,
        entries=selected_entries,
        selection_notes=selection_notes,
    )
    snapshot_path = run_dir / 'manifest_snapshot.json'
    _write_json(snapshot_path, snapshot.to_dict())

    ai_model_info, provider_version_info = _resolve_runtime_signature()
    run_context = RunContext(
        run_id=run_id,
        mode=canonical_mode,
        started_at=_utc_now(),
        manifest_version=manifest_version,
        manifest_snapshot_path=str(snapshot_path),
        baseline_root=str(resolved_baseline_root),
        artifacts_root=str(resolved_artifacts_root),
        ai_model_info=ai_model_info,
        provider_version_info=provider_version_info,
        selected_sample_ids=[entry.sample_id for entry in selected_entries],
    )
    run_context_path = run_dir / 'run_context.json'
    _write_json(run_context_path, run_context.to_dict())

    flaky_samples: list[str] = []
    infra_failures: list[str] = []
    observation_failures: list[str] = []
    quarantine_candidates: list[str] = []

    rename_summary, lane_flaky, lane_infra, lane_observation, lane_quarantine = run_rename_lane(
        entries=selected_entries,
        baseline_root=resolved_baseline_root,
        sample_results_dir=sample_results_dir,
        sandbox_root=sandbox_root,
        mode=canonical_mode,
    )
    summary = rename_summary.to_dict()
    flaky_samples.extend(lane_flaky)
    infra_failures.extend(lane_infra)
    observation_failures.extend(lane_observation)
    quarantine_candidates.extend(lane_quarantine)

    product_failure_count = summary.get('product_failure_count', 0)
    infra_failure_count = summary.get('infra_failure_count', 0)
    gate_failed = canonical_mode == 'check' and product_failure_count > 0
    selected_count = summary.get('selected_count', 0)

    report = RunReport(
        run_context=run_context.to_dict(),
        summary=summary,
        gate_result={
            'gate_failed': gate_failed,
            'product_failure_count': product_failure_count,
            'infra_failure_count': infra_failure_count,
            'flaky_count': len(flaky_samples),
        },
        flaky_samples=sorted(set(flaky_samples)),
        infra_failures=sorted(set(infra_failures)),
        observation_failures=sorted(set(observation_failures)),
        quarantine_candidates=sorted(set(quarantine_candidates)),
    )

    report_json_path = run_dir / 'report.json'
    report_md_path = run_dir / 'report.md'
    write_report_json(report_json_path, report)
    write_report_markdown(report_md_path, report)

    return {
        'run_id': run_id,
        'selected_count': selected_count,
        'selected_sample_ids': run_context.selected_sample_ids,
        'manifest_snapshot_path': str(snapshot_path),
        'run_context_path': str(run_context_path),
        'report_json_path': str(report_json_path),
        'report_md_path': str(report_md_path),
        'gate_failed': gate_failed,
        'exit_code': 2 if gate_failed else 0,
        'mode': canonical_mode,
    }
