from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config.config_manager import cm
from src.rename.bgm_to_tmdb import (
    compile_bgm_to_tmdb_input,
    iter_accepted_compiled_plan_artifacts,
    load_accepted_compiled_plan_artifact,
    run_bgm_to_tmdb_bridge_agent,
)


DEFAULT_ACCEPTED_ROOT = Path(
    'tests/sample_pool/generated/pi_full146_gpt54mini_workers10_runner_repair_20260531_112000'
)
DEFAULT_WORKERS = 3


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _default_output_dir() -> Path:
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    millis = int((time.time() % 1) * 1000)
    return Path('tests/sample_pool/generated') / f'bgm_to_tmdb_bridge_gate_{timestamp}_{millis:03d}'


def _safe_id(value: str) -> str:
    text = ''.join(ch if ch.isalnum() or ch in '-_.' else '-' for ch in str(value or 'sample'))
    text = '-'.join(part for part in text.split('-') if part)
    return (text or 'sample')[:120]


def _artifact_key(path: Path) -> str:
    return path.resolve().as_posix().casefold()


def _resolve_artifact(path: Path) -> Path:
    if path.is_absolute():
        return path
    repo_relative = REPO_ROOT / path
    if repo_relative.exists():
        return repo_relative
    return path


def _select_artifacts(
    *,
    accepted_root: Path,
    artifacts: list[Path],
    sample_filters: list[str],
    limit: int | None,
    offset: int,
) -> list[Path]:
    if artifacts:
        candidates = [_resolve_artifact(path) for path in artifacts]
    else:
        candidates = iter_accepted_compiled_plan_artifacts(accepted_root)
    for path in candidates:
        if not path.exists():
            raise FileNotFoundError(f'accepted artifact does not exist: {path}')
        if not path.is_file():
            raise ValueError(f'accepted artifact path is not a file: {path}')

    if sample_filters:
        lowered = [token.casefold() for token in sample_filters if token]
        candidates = [
            path
            for path in candidates
            if any(token in path.stem.casefold() or token in path.as_posix().casefold() for token in lowered)
        ]
    if offset > 0:
        candidates = candidates[offset:]
    if limit is not None:
        candidates = candidates[: max(0, limit)]
    return candidates


def _selected_entries(artifacts: list[Path]) -> list[dict[str, Any]]:
    stem_counts = Counter(path.stem for path in artifacts)
    seen: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    for path in artifacts:
        sample_id = _safe_id(path.stem)
        output_id = sample_id
        if stem_counts[path.stem] > 1:
            seen[path.stem] = int(seen.get(path.stem) or 0) + 1
            output_id = f'{sample_id}-{seen[path.stem]:02d}'
        entries.append({'artifact': path, 'sample_id': sample_id, 'output_id': output_id})
    return entries


def _progress_path(output_dir: Path, output_id: str) -> Path:
    return output_dir / f'{output_id}.progress.json'


def _result_path(output_dir: Path, output_id: str) -> Path:
    return output_dir / f'{output_id}.json'


def _write_progress(
    *,
    artifact: Path,
    output_dir: Path,
    output_id: str,
    phase: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        'kind': 'bgm_to_tmdb_bridge_sample_runner_progress',
        'updated_at_ms': int(time.time() * 1000),
        'phase': phase,
        'artifact': artifact.as_posix(),
        'output_dir': output_dir.as_posix(),
        **dict(extra or {}),
    }
    path = _progress_path(output_dir, output_id)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp_path.replace(path)


def _write_sample_result(output_dir: Path, output_id: str, payload: dict[str, Any]) -> None:
    _result_path(output_dir, output_id).write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def _dry_build_entry(entry: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    artifact = Path(entry['artifact'])
    output_id = str(entry['output_id'])
    sample_id = str(entry['sample_id'])
    started = time.time()
    try:
        _write_progress(
            artifact=artifact,
            output_dir=output_dir,
            output_id=output_id,
            phase='artifact_load_started',
        )
        plan = load_accepted_compiled_plan_artifact(artifact)
        bridge_input = compile_bgm_to_tmdb_input(plan, source_path=artifact)
        elapsed_ms = int((time.time() - started) * 1000)
        row = {
            'artifact': artifact.as_posix(),
            'sample_id': sample_id,
            'sample': sample_id,
            'status': 'dry_build',
            'ok': True,
            'elapsed_ms': elapsed_ms,
            'assignment_count': len(bridge_input.assignments),
            'mapped_bangumi_assignment_count': sum(1 for item in bridge_input.assignments if item.is_mapped_bangumi),
            'supplemental_assignment_count': sum(1 for item in bridge_input.assignments if not item.is_mapped_bangumi),
            'source_path': bridge_input.source_path,
        }
        _write_progress(
            artifact=artifact,
            output_dir=output_dir,
            output_id=output_id,
            phase='dry_build_finished',
            extra=row,
        )
        _write_sample_result(output_dir, output_id, {'ok': True, 'status': 'dry_build', 'sample_runner': row})
        return row
    except Exception as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        row = {
            'artifact': artifact.as_posix(),
            'sample_id': sample_id,
            'sample': sample_id,
            'status': 'error',
            'ok': False,
            'elapsed_ms': elapsed_ms,
            'summary': 'dry_build_error',
            'error': f'{type(exc).__name__}: {exc}',
        }
        _write_progress(
            artifact=artifact,
            output_dir=output_dir,
            output_id=output_id,
            phase='dry_build_error',
            extra=row,
        )
        _write_sample_result(output_dir, output_id, {'ok': False, 'status': 'error', 'sample_runner': row})
        return row


def _run_bridge_entry(entry: dict[str, Any], output_dir: Path, sample_timeout_seconds: int) -> dict[str, Any]:
    artifact = Path(entry['artifact'])
    output_id = str(entry['output_id'])
    sample_id = str(entry['sample_id'])
    started = time.time()
    try:
        _write_progress(
            artifact=artifact,
            output_dir=output_dir,
            output_id=output_id,
            phase='artifact_load_started',
        )
        plan = load_accepted_compiled_plan_artifact(artifact)
        bridge_input = compile_bgm_to_tmdb_input(plan, source_path=artifact)
        _write_progress(
            artifact=artifact,
            output_dir=output_dir,
            output_id=output_id,
            phase='artifact_loaded',
            extra={
                'sample_id': sample_id,
                'assignment_count': len(bridge_input.assignments),
                'mapped_bangumi_assignment_count': sum(1 for item in bridge_input.assignments if item.is_mapped_bangumi),
                'supplemental_assignment_count': sum(1 for item in bridge_input.assignments if not item.is_mapped_bangumi),
            },
        )
        overrides: dict[str, Any] = {}
        if int(sample_timeout_seconds or 0) > 0:
            overrides['rename_local_bangumi_pi_timeout_seconds'] = max(1, int(sample_timeout_seconds))
        _write_progress(
            artifact=artifact,
            output_dir=output_dir,
            output_id=output_id,
            phase='bridge_agent_started',
            extra={'sample_timeout_seconds': int(sample_timeout_seconds or 0)},
        )
        if overrides:
            with cm.temporary_config(overrides):
                result = run_bgm_to_tmdb_bridge_agent(
                    compiled_plan=plan,
                    artifact_path=artifact,
                    source_path=artifact,
                    sample_id=sample_id,
                )
        else:
            result = run_bgm_to_tmdb_bridge_agent(
                compiled_plan=plan,
                artifact_path=artifact,
                source_path=artifact,
                sample_id=sample_id,
            )
        elapsed_ms = int((time.time() - started) * 1000)
        row = _run_result_row(
            artifact=artifact,
            sample_id=sample_id,
            result=result,
            elapsed_ms=elapsed_ms,
            assignment_count=len(bridge_input.assignments),
        )
        _write_progress(
            artifact=artifact,
            output_dir=output_dir,
            output_id=output_id,
            phase='bridge_agent_finished',
            extra={
                'status': row.get('status'),
                'summary': row.get('summary'),
                'tool_call_counts': row.get('tool_call_counts'),
                'run_dir': row.get('run_dir'),
            },
        )
        _write_sample_result(
            output_dir,
            output_id,
            {
                'ok': row.get('ok'),
                'status': row.get('status'),
                'summary': row.get('summary'),
                'sample_runner': row,
                'bridge_run_result': result,
            },
        )
        return row
    except Exception as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        row = {
            'artifact': artifact.as_posix(),
            'sample_id': sample_id,
            'sample': sample_id,
            'status': 'error',
            'ok': False,
            'elapsed_ms': elapsed_ms,
            'summary': 'sample_runner_error',
            'error': f'{type(exc).__name__}: {exc}',
        }
        _write_progress(
            artifact=artifact,
            output_dir=output_dir,
            output_id=output_id,
            phase='bridge_agent_error',
            extra=row,
        )
        _write_sample_result(output_dir, output_id, {'ok': False, 'status': 'error', 'sample_runner': row})
        return row


def _run_result_row(
    *,
    artifact: Path,
    sample_id: str,
    result: Any,
    elapsed_ms: int,
    assignment_count: int,
) -> dict[str, Any]:
    verifier = result.final_verifier_result
    verified_plan = result.verified_plan
    recipe_params = getattr(result, 'recipe_params', None)
    return {
        'artifact': artifact.as_posix(),
        'sample_id': sample_id,
        'sample': sample_id,
        'status': result.status,
        'ok': bool(result.ok),
        'elapsed_ms': elapsed_ms,
        'summary': result.summary,
        'final_action': result.final_action,
        'errors': list(result.errors),
        'assignment_count': assignment_count,
        'bridge_mapping_count': len(verified_plan.mappings) if verified_plan is not None else 0,
        'recipe_rule_count': len(recipe_params.rules) if recipe_params is not None else 0,
        'recipe_params_present': recipe_params is not None,
        'verified_plan_target_count': verified_plan.tmdb_target_count if verified_plan is not None else 0,
        'verified_plan_present': verified_plan is not None,
        'final_verifier_passed': verifier.passed if verifier is not None else None,
        'final_verifier_issue_count': len(verifier.issues) if verifier is not None else None,
        'run_dir': result.run_dir.as_posix(),
        'pi_provider': result.pi_provider,
        'pi_model': result.pi_model,
        'pi_base_url': result.pi_base_url,
        'runtime_returncode': result.runtime_returncode,
        'runtime_command': result.runtime_command,
        'tool_trace_count': len(result.tool_trace),
        'tool_call_counts': dict(result.tool_call_counts),
        'tool_sequence': list(result.tool_sequence),
        'submit_rejection_count': int(result.submit_rejection_count or 0),
    }


def _run_in_parallel(
    entries: list[dict[str, Any]],
    worker,
    *args: Any,
    worker_count: int,
) -> list[dict[str, Any]]:
    if len(entries) == 1:
        return [worker(entries[0], *args)]
    indexed_rows: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(worker_count or 1))) as executor:
        futures = {
            executor.submit(worker, entry, *args): index
            for index, entry in enumerate(entries)
        }
        for future in as_completed(futures):
            indexed_rows[futures[future]] = future.result()
    return [indexed_rows[index] for index in range(len(entries))]


def _strict_row_ok(row: dict[str, Any]) -> bool:
    status = str(row.get('status') or '')
    if status == 'dry_build':
        return bool(row.get('ok'))
    if status == 'accepted':
        return (
            bool(row.get('ok'))
            and bool(row.get('verified_plan_present'))
            and bool(row.get('final_verifier_passed'))
            and not list(row.get('errors') or [])
        )
    if status == 'fail_closed':
        return bool(row.get('ok')) and str(row.get('final_action') or '') == 'fail_closed'
    return False


def _count_nested(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, dict):
            continue
        for nested_key, nested_value in value.items():
            counts[str(nested_key)] = int(counts.get(str(nested_key)) or 0) + int(nested_value or 0)
    return dict(sorted(counts.items()))


def _summary(
    *,
    accepted_root: Path,
    output_dir: Path,
    entries: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    worker_count: int,
    dry_build: bool,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    summary_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get('status') or 'unknown')
        counts[status] = int(counts.get(status) or 0) + 1
        summary_text = str(row.get('summary') or '')
        if summary_text:
            summary_counts[summary_text] = int(summary_counts.get(summary_text) or 0) + 1
    strict_failures = [
        {
            'artifact': row.get('artifact'),
            'sample_id': row.get('sample_id'),
            'status': row.get('status'),
            'summary': row.get('summary'),
            'error': row.get('error'),
            'errors': row.get('errors'),
        }
        for row in rows
        if not _strict_row_ok(row)
    ]
    return {
        'ok': not strict_failures,
        'dry_build': bool(dry_build),
        'accepted_root': accepted_root.as_posix(),
        'output_dir': output_dir.as_posix(),
        'sample_count': len(rows),
        'selected_artifact_count': len(entries),
        'worker_count': max(1, int(worker_count or 1)),
        'counts': dict(sorted(counts.items())),
        'summary_counts': dict(sorted(summary_counts.items())),
        'accepted_count': int(counts.get('accepted') or 0),
        'fail_closed_count': int(counts.get('fail_closed') or 0),
        'error_count': int(counts.get('error') or 0),
        'dry_build_count': int(counts.get('dry_build') or 0),
        'assignment_count_total': sum(int(row.get('assignment_count') or 0) for row in rows),
        'verified_plan_target_count_total': sum(int(row.get('verified_plan_target_count') or 0) for row in rows),
        'recipe_rule_count_total': sum(int(row.get('recipe_rule_count') or 0) for row in rows),
        'tool_call_counts': _count_nested(rows, 'tool_call_counts'),
        'submit_rejection_count_total': sum(int(row.get('submit_rejection_count') or 0) for row in rows),
        'strict_failure_count': len(strict_failures),
        'strict_failures': strict_failures,
        'rows': rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Run BGM-to-TMDB Pi bridge dry-run from accepted Local-to-Bangumi artifacts.'
    )
    parser.add_argument('--accepted-root', type=Path, default=DEFAULT_ACCEPTED_ROOT)
    parser.add_argument('--artifact', action='append', type=Path, default=[], help='Accepted artifact JSON path; can be repeated.')
    parser.add_argument('--sample', action='append', default=[], help='Substring filter over artifact path/stem; can be repeated.')
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--offset', type=int, default=0, help='Skip this many selected artifacts before applying --limit.')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS)
    parser.add_argument('--sample-timeout-seconds', type=int, default=0)
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--dry-build', action='store_true', help='Parse accepted artifacts and compile bridge input; do not call Pi or TMDB.')
    parser.add_argument('--all', action='store_true', help='Run every selected accepted artifact. Without this and without --limit, limit defaults to 3.')
    args = parser.parse_args(argv)

    accepted_root = args.accepted_root
    if not accepted_root.is_absolute():
        accepted_root = REPO_ROOT / accepted_root
    output_dir = args.output_dir or _default_output_dir()
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    limit = args.limit
    if limit is None and not args.all:
        limit = 3
    artifacts = _select_artifacts(
        accepted_root=accepted_root,
        artifacts=list(args.artifact or []),
        sample_filters=list(args.sample or []),
        limit=limit,
        offset=max(0, int(args.offset or 0)),
    )
    entries = _selected_entries(artifacts)
    if args.dry_build:
        rows = _run_in_parallel(entries, _dry_build_entry, output_dir, worker_count=args.workers)
    else:
        rows = _run_in_parallel(
            entries,
            _run_bridge_entry,
            output_dir,
            int(args.sample_timeout_seconds or 0),
            worker_count=args.workers,
        )
    summary = _summary(
        accepted_root=accepted_root,
        output_dir=output_dir,
        entries=entries,
        rows=rows,
        worker_count=args.workers,
        dry_build=args.dry_build,
    )
    (output_dir / 'summary.json').write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(_json_safe(summary), ensure_ascii=False, indent=2))
    return 0 if summary['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
