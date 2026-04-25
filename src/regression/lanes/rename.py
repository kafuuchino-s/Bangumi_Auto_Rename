from __future__ import annotations

import json
import shutil
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..baseline import load_baseline_record, save_baseline_record
from ..compare.rename import compare_rename_result
from ..models import BaselineRecord, RenameSample, RunSummary, SampleRunResult
from ...config.config_manager import cm
from ...rename.process import Rename, _temporary_debug_task_record_paths


LIBRARY_KINDS = ('anime', 'bangumi', 'movie', 'anime_movie')
RENAME_LANE_CONTRACT_VERSION = 1
RENAME_LANE_RUNNER_KIND = 'rename_lane_main_flow'
RENAME_LANE_RUNTIME_ENTRYPOINT = 'src.regression.lanes.rename._execute_sample -> src.rename.process.Rename.process'
VIDEO_SUFFIXES = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts'}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_rename_lane_contract() -> dict[str, Any]:
    return {
        'schema_version': RENAME_LANE_CONTRACT_VERSION,
        'runner_kind': RENAME_LANE_RUNNER_KIND,
        'runtime_entrypoint': RENAME_LANE_RUNTIME_ENTRYPOINT,
        'raw_sample_materialization': 'tests/sample_pool/raw JSON -> sandbox source tree',
        'uses_runtime_rename_process': True,
        'uses_shadow_candidate_logic': False,
        'filesystem_sandboxed': True,
        'authoritative_for_sample_pool': True,
        'baseline_required_for_gate': True,
    }


def summarize_rename_payload(payload: dict[str, Any]) -> dict[str, Any]:
    routes = [item for item in payload.get('routes') or [] if isinstance(item, dict)]
    mapping = [item for item in payload.get('mapping') or [] if isinstance(item, dict)]
    task_artifacts = [item for item in payload.get('task_artifacts') or [] if isinstance(item, dict)]
    return {
        'final_type': payload.get('final_type'),
        'route_count': len(routes),
        'routes': routes,
        'mapping_count': len(mapping),
        'target_count': len({str(item.get('target_rel') or '') for item in mapping}),
        'task_artifact_count': len(task_artifacts),
        'failure_reasons': sorted(
            {
                str(item.get('failure_reason') or '')
                for item in task_artifacts
                if str(item.get('failure_reason') or '')
            }
        ),
        'pipeline_modes': sorted(
            {
                str(item.get('pipeline_mode') or '')
                for item in task_artifacts
                if str(item.get('pipeline_mode') or '')
            }
        ),
    }


def _find_duplicate_mapping_targets(mapping: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, str]] = []
    for item in mapping:
        target_rel = str(item.get('target_rel') or '').strip()
        if not target_rel:
            continue
        key = target_rel.casefold()
        previous = seen.get(key)
        if previous is None:
            seen[key] = item
            continue
        duplicates.append(
            {
                'target_rel': target_rel,
                'first_source_rel': str(previous.get('source_rel') or ''),
                'second_source_rel': str(item.get('source_rel') or ''),
            }
        )
    return duplicates


def _clear_directory(path: Path) -> None:
    if not path.exists():
        return
    for item in path.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def _clear_sample_side_effects(
    *,
    library_root: Path,
    record_output_path: Path,
) -> None:
    _clear_directory(record_output_path)
    _clear_directory(library_root)


def build_rename_lane_observation(
    *,
    entry: RenameSample,
    execution_result: dict[str, Any],
    manifest_version: str,
) -> dict[str, Any]:
    payload = execution_result.get('payload') if isinstance(execution_result.get('payload'), dict) else {}
    artifacts = execution_result.get('artifacts') if isinstance(execution_result.get('artifacts'), dict) else {}
    contract = build_rename_lane_contract()
    return {
        'artifact_type': 'rename_lane_main_flow_observation',
        'schema_version': RENAME_LANE_CONTRACT_VERSION,
        'runner_kind': RENAME_LANE_RUNNER_KIND,
        'lane_contract': contract,
        'main_flow_verified': True,
        'main_flow_observer': True,
        'observation_is_truth': False,
        'generated_at': _utc_now(),
        'manifest_version': manifest_version,
        'sample_id': entry.sample_id,
        'sample_json': entry.sample_json,
        'anchor': entry.anchor,
        'check': entry.check,
        'tags': entry.tags,
        'protects': entry.protects,
        'runtime_entrypoint': contract['runtime_entrypoint'],
        'uses_runtime_rename_process': contract['uses_runtime_rename_process'],
        'uses_shadow_candidate_logic': contract['uses_shadow_candidate_logic'],
        'bangumi_context_expected': True,
        'strict_validation_expected': True,
        'filesystem_sandboxed': contract['filesystem_sandboxed'],
        'process_status': str(execution_result.get('status') or 'unknown'),
        'infra_failure': bool(execution_result.get('infra_failure')),
        'message': execution_result.get('message') or '',
        'summary': summarize_rename_payload(payload),
        'payload': payload,
        'artifacts': dict(artifacts),
    }


def _normalize_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _read_json_file(path: Path) -> dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as file:
        payload = json.load(file)
    return payload if isinstance(payload, dict) else {}


def _normalize_output_rel(path: Path, sample_root: Path) -> str:
    resolved_path = path.resolve()
    library_root = (sample_root / 'library').resolve()
    for kind in LIBRARY_KINDS:
        try:
            return resolved_path.relative_to((library_root / kind).resolve()).as_posix()
        except Exception:
            continue
    return _normalize_rel(path, sample_root)


def _collect_library_files(library_root: Path, sample_root: Path) -> list[dict[str, Any]]:
    if not library_root.exists():
        return []
    results: list[dict[str, Any]] = []
    for file_path in sorted(
        [item for item in library_root.rglob('*') if item.is_file()],
        key=lambda item: item.as_posix().casefold(),
    ):
        results.append(
            {
                'path': _normalize_output_rel(file_path, sample_root),
                'size': file_path.stat().st_size,
            }
        )
    return results


def _collect_source_video_discovery(source_root: Path) -> dict[str, Any]:
    video_files = sorted(
        [
            item
            for item in source_root.rglob('*')
            if item.is_file() and item.suffix.casefold() in VIDEO_SUFFIXES
        ],
        key=lambda item: _normalize_rel(item, source_root).casefold(),
    )
    return {
        'source_video_count': len(video_files),
        'source_video_examples': [_normalize_rel(item, source_root) for item in video_files[:20]],
    }


def _normalize_mixed_execution_task(
    mixed_execution: dict[str, Any],
    sample_root: Path,
) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for child in mixed_execution.get('children') or []:
        if not isinstance(child, dict):
            continue
        children.append(
            {
                'route_type': child.get('route_type'),
                'is_movie': bool(child.get('is_movie')),
                'name': child.get('name'),
                'season_id': child.get('season_id'),
                'tmdb_id': child.get('tmdb_id'),
                'tmdb_media_type': child.get('tmdb_media_type'),
                'target_root_rel': _normalize_output_rel(Path(str(child.get('target_root') or '')), sample_root)
                if child.get('target_root')
                else '',
                'target_paths': sorted(
                    _normalize_output_rel(Path(str(item)), sample_root)
                    for item in (child.get('target_paths') or [])
                    if str(item).strip()
                ),
                'target_count': child.get('target_count'),
            }
        )

    return {
        'plan_kind': mixed_execution.get('plan_kind'),
        'planning_mode': mixed_execution.get('planning_mode'),
        'candidate_route_types': sorted(str(item) for item in (mixed_execution.get('candidate_route_types') or [])),
        'selected_route_type': mixed_execution.get('selected_route_type'),
        'child_route_types': sorted(str(item) for item in (mixed_execution.get('child_route_types') or [])),
        'child_count': mixed_execution.get('child_count'),
        'child_target_roots': sorted(
            _normalize_output_rel(Path(str(item)), sample_root)
            for item in (mixed_execution.get('child_target_roots') or [])
            if str(item).strip()
        ),
        'children': sorted(
            children,
            key=lambda item: (
                str(item.get('route_type') or ''),
                str(item.get('target_root_rel') or ''),
                str(item.get('name') or ''),
            ),
        ),
    }


def _normalize_mixed_execution_record(
    mixed_execution: dict[str, Any],
    sample_root: Path,
) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for child in mixed_execution.get('children') or []:
        if not isinstance(child, dict):
            continue
        children.append(
            {
                'route_type': child.get('route_type'),
                'target_root_rel': _normalize_output_rel(Path(str(child.get('target_root') or '')), sample_root)
                if child.get('target_root')
                else '',
                'target_count': child.get('target_count'),
            }
        )
    return {
        'plan_kind': mixed_execution.get('plan_kind'),
        'planning_mode': mixed_execution.get('planning_mode'),
        'child_route_types': sorted(str(item) for item in (mixed_execution.get('child_route_types') or [])),
        'children': sorted(
            children,
            key=lambda item: (
                str(item.get('route_type') or ''),
                str(item.get('target_root_rel') or ''),
            ),
        ),
    }


def _normalize_task_artifact(
    *,
    task_path: Path,
    task_data: dict[str, Any],
    source_root: Path,
    sample_root: Path,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        'artifact_name': task_path.name,
        'source_rel': _normalize_rel(Path(str(task_data.get('path') or '')), source_root)
        if task_data.get('path')
        else '',
        'route_type': task_data.get('tmdb_media_type') or ('movie' if task_data.get('is_movie') else 'tv'),
        'pipeline_mode': task_data.get('pipeline_mode'),
        'tmdb_id': task_data.get('tmdb_id'),
        'tmdb_name': task_data.get('tmdb_name') or task_data.get('name'),
        'season_id': task_data.get('season_id'),
        'target_root_rel': _normalize_output_rel(Path(str(task_data.get('target_root') or '')), sample_root)
        if task_data.get('target_root')
        else '',
        'is_mixed_parent': bool(task_data.get('is_mixed_parent')),
        'failure_reason': task_data.get('failure_reason'),
    }
    mixed_execution = task_data.get('mixed_execution')
    if isinstance(mixed_execution, dict):
        normalized['mixed_execution'] = _normalize_mixed_execution_task(mixed_execution, sample_root)
    video_discovery = task_data.get('video_discovery')
    if isinstance(video_discovery, dict):
        normalized['video_discovery'] = video_discovery
    for key in ('unmapped_potential_main_files', 'ignored_supplemental_relative_paths'):
        value = task_data.get(key)
        if isinstance(value, list):
            normalized[key] = value
    return normalized


def _normalize_record_artifact(
    *,
    record_path: Path,
    record_data: dict[str, Any],
    source_root: Path,
    sample_root: Path,
) -> dict[str, Any]:
    mapping: list[dict[str, Any]] = []
    mixed_execution: dict[str, Any] | None = None
    for source_raw, target_raw in sorted(record_data.items(), key=lambda item: str(item[0]).casefold()):
        if str(source_raw) == '_mixed_execution':
            if isinstance(target_raw, dict):
                mixed_execution = _normalize_mixed_execution_record(target_raw, sample_root)
            continue
        source_path = Path(str(source_raw))
        target_path = Path(str(target_raw))
        mapping.append(
            {
                'source_rel': _normalize_rel(source_path, source_root),
                'target_rel': _normalize_output_rel(target_path, sample_root),
            }
        )

    normalized = {
        'artifact_name': record_path.name,
        'mapping': sorted(
            mapping,
            key=lambda item: (str(item.get('target_rel') or ''), str(item.get('source_rel') or '')),
        ),
    }
    if mixed_execution is not None:
        normalized['mixed_execution'] = mixed_execution
    return normalized


def _normalize_route(
    *,
    task_data: dict[str, Any],
    record_data: dict[str, Any],
    source_root: Path,
    sample_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    route_type = str(task_data.get('tmdb_media_type') or ('movie' if task_data.get('is_movie') else 'tv'))
    normalized_mapping: list[dict[str, Any]] = []
    for source_raw, target_raw in sorted(record_data.items(), key=lambda item: str(item[1]).casefold()):
        source_path = Path(str(source_raw))
        target_path = Path(str(target_raw))
        normalized_mapping.append(
            {
                'route_type': route_type,
                'source_rel': _normalize_rel(source_path, source_root),
                'target_rel': _normalize_output_rel(target_path, sample_root),
            }
        )

    route_summary = {
        'route_type': route_type,
        'tmdb_id': task_data.get('tmdb_id'),
        'season_id': task_data.get('season_id'),
        'tmdb_name': task_data.get('tmdb_name') or task_data.get('name'),
        'target_root_rel': _normalize_output_rel(Path(str(task_data.get('target_root') or '')), sample_root)
        if task_data.get('target_root')
        else '',
        'mapping_count': len(normalized_mapping),
    }
    return route_summary, normalized_mapping


def _collect_task_record_artifacts(
    *,
    task_output_path: Path,
    record_output_path: Path,
    source_root: Path,
    sample_root: Path,
) -> tuple[list[Path], list[Path], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    task_files = sorted(task_output_path.glob('*.json'), key=lambda item: item.name.casefold())
    record_files = sorted(record_output_path.glob('*.json'), key=lambda item: item.name.casefold())

    routes: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    task_artifacts: list[dict[str, Any]] = []
    record_artifacts: list[dict[str, Any]] = []

    record_lookup = {item.name: item for item in record_files}

    for task_path in task_files:
        task_data = _read_json_file(task_path)
        record_path = record_lookup.get(task_path.name)
        record_data = _read_json_file(record_path) if record_path and record_path.exists() else {}
        route_summary, route_mapping = _normalize_route(
            task_data=task_data,
            record_data={
                key: value
                for key, value in record_data.items()
                if str(key) != '_mixed_execution'
            },
            source_root=source_root,
            sample_root=sample_root,
        )
        routes.append(route_summary)
        mapping.extend(route_mapping)
        task_artifacts.append(
            _normalize_task_artifact(
                task_path=task_path,
                task_data=task_data,
                source_root=source_root,
                sample_root=sample_root,
            )
        )
        if record_path and record_path.exists():
            record_artifacts.append(
                _normalize_record_artifact(
                    record_path=record_path,
                    record_data=record_data,
                    source_root=source_root,
                    sample_root=sample_root,
                )
            )

    return task_files, record_files, routes, mapping, task_artifacts, record_artifacts


def _classify_process_failure(message: str) -> tuple[str, bool]:
    lowered = message.casefold()
    product_failure_markers = (
        '未搜索到匹配结果',
        '无结果',
        'tmdb无结果',
        'tmdb no result',
        '未能完成全部电影映射',
        'no matching result',
        'not found',
        'no match',
    )
    if any(marker in lowered for marker in product_failure_markers):
        return 'product_failed', False

    infra_keywords = (
        'timeout',
        'network',
        'connection',
        'api',
        'tmdb',
        'openai',
        'http',
        '503',
        '502',
    )
    if any(keyword in lowered for keyword in infra_keywords):
        return 'infra_failed', True
    return 'product_failed', False


def _build_runtime_signature() -> dict[str, Any]:
    return {
        'ai_model': cm.get_config('ai_model'),
        'ai_base_url': cm.get_config('ai_base_url'),
        'openai_output_format': cm.get_config('openai_output_format'),
        'openai_api_interface': cm.get_config('openai_api_interface'),
    }


@contextmanager
def _materialized_sample(sample_json_path: Path, sample_root: Path) -> Iterator[tuple[dict[str, Any], Path]]:
    with open(sample_json_path, 'r', encoding='utf-8') as file:
        raw_sample = json.load(file)
    if not isinstance(raw_sample, dict):
        raise ValueError(f'Invalid sample json: {sample_json_path}')

    root_name = str(raw_sample.get('root_name') or sample_json_path.stem)
    source_root = sample_root / 'source' / root_name
    source_root.mkdir(parents=True, exist_ok=True)
    for item in raw_sample.get('files') or []:
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get('path') or '').strip()
        if not relative_path:
            continue
        target_path = source_root / Path(relative_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.touch(exist_ok=True)

    yield raw_sample, source_root


def _execute_sample(entry: RenameSample, sample_root: Path) -> dict[str, Any]:
    sample_json_path = Path(str(entry.sample_json or ''))
    if not sample_json_path.is_absolute():
        sample_json_path = Path(__file__).resolve().parents[3] / sample_json_path

    task_output_path = sample_root / 'data' / 'task'
    record_output_path = sample_root / 'data' / 'record'
    library_root = sample_root / 'library'
    task_output_path.mkdir(parents=True, exist_ok=True)
    record_output_path.mkdir(parents=True, exist_ok=True)
    library_root.mkdir(parents=True, exist_ok=True)

    with _materialized_sample(sample_json_path, sample_root) as (_, source_root):
        with cm.temporary_config(
            {
                'anime_path': str(library_root / 'anime'),
                'bangumi_path': str(library_root / 'bangumi'),
                'movie_path': str(library_root / 'movie'),
                'anime_movie_path': str(library_root / 'anime_movie'),
                'mode': '复制',
                'overwrite_existing': True,
                'subtitle_auto_fetch_enabled': False,
                'subtitle_auto_fetch_use_ai_rerank': False,
                'subtitle_auto_fetch_search_mode': 'auto',
            }
        ):
            rename = Rename()

            def _enqueue_inline_subtask(
                *,
                path: str,
                is_anime: bool | None = None,
                is_movie: bool | None = None,
                original_uuid: str | None = None,
                cus_name: str | None = None,
                cus_season_id: int | None = None,
                use_ai: bool | None = None,
                _is_sub_task: bool = False,
            ) -> str:
                del original_uuid, use_ai
                task_id = str(uuid.uuid4())
                _ = rename.process(
                    Path(path),
                    is_anime,
                    is_movie,
                    _tuuid=task_id,
                    cus_name=cus_name,
                    cus_season_id=cus_season_id,
                    _is_sub_task=_is_sub_task,
                    _enqueue_task=_enqueue_inline_subtask,
                )
                return task_id

            with _temporary_debug_task_record_paths(task_output_path, record_output_path):
                process_result = rename.process(
                    source_root,
                    _is_anime=True,
                    _enqueue_task=_enqueue_inline_subtask,
                )

        if isinstance(process_result, str):
            status, infra_failure = _classify_process_failure(process_result)
            task_files, record_files, routes, mapping, task_artifacts, record_artifacts = _collect_task_record_artifacts(
                task_output_path=task_output_path,
                record_output_path=record_output_path,
                source_root=source_root,
                sample_root=sample_root,
            )
            return {
                'status': status,
                'infra_failure': infra_failure,
                'message': process_result,
                'payload': {
                    'final_type': 'unknown',
                    'routes': routes,
                    'mapping': mapping,
                    'library_files': _collect_library_files(library_root, sample_root),
                    'task_artifacts': task_artifacts,
                    'record_artifacts': record_artifacts,
                },
                'artifacts': {
                    'sample_root': str(sample_root),
                    'source_root': str(source_root),
                    'task_dir': str(task_output_path),
                    'record_dir': str(record_output_path),
                    'task_files': [str(item) for item in task_files],
                    'record_files': [str(item) for item in record_files],
                },
            }

        task_files, record_files, routes, mapping, task_artifacts, record_artifacts = _collect_task_record_artifacts(
            task_output_path=task_output_path,
            record_output_path=record_output_path,
            source_root=source_root,
            sample_root=sample_root,
        )
        if not task_files or not record_files:
            payload = {
                'final_type': 'unknown',
                'routes': [],
                'mapping': [],
                'library_files': _collect_library_files(library_root, sample_root),
                'task_artifacts': [
                    {
                        'artifact_name': 'synthetic_missing_runtime_artifacts',
                        'source_rel': '.',
                        'route_type': 'unknown',
                        'pipeline_mode': 'rename_lane',
                        'tmdb_id': None,
                        'tmdb_name': None,
                        'season_id': None,
                        'target_root_rel': '',
                        'is_mixed_parent': False,
                        'failure_reason': 'missing_runtime_artifacts',
                        'process_message': 'rename process completed without task/record artifacts',
                        'video_discovery': _collect_source_video_discovery(source_root),
                    }
                ],
                'record_artifacts': [],
            }
            return {
                'status': 'product_failed',
                'infra_failure': False,
                'message': 'rename process completed without task/record artifacts',
                'payload': payload,
                'artifacts': {
                    'sample_root': str(sample_root),
                    'source_root': str(source_root),
                    'task_dir': str(task_output_path),
                    'record_dir': str(record_output_path),
                },
            }

        route_types = {str(item.get('route_type') or '') for item in routes}
        if len(route_types) > 1:
            final_type = 'mixed'
        elif routes:
            final_type = str(routes[0].get('route_type') or 'unknown')
        else:
            final_type = 'unknown'

        payload = {
            'final_type': final_type,
            'routes': routes,
            'mapping': mapping,
            'library_files': _collect_library_files(library_root, sample_root),
            'task_artifacts': task_artifacts,
            'record_artifacts': record_artifacts,
        }
        child_failure_reasons = sorted(
            {
                str(item.get('failure_reason') or '')
                for item in task_artifacts
                if str(item.get('failure_reason') or '')
            }
        )
        if child_failure_reasons:
            _clear_sample_side_effects(
                library_root=library_root,
                record_output_path=record_output_path,
            )
            task_files, record_files, routes, mapping, task_artifacts, record_artifacts = _collect_task_record_artifacts(
                task_output_path=task_output_path,
                record_output_path=record_output_path,
                source_root=source_root,
                sample_root=sample_root,
            )
            payload.update(
                {
                    'routes': routes,
                    'mapping': mapping,
                    'library_files': _collect_library_files(library_root, sample_root),
                    'task_artifacts': task_artifacts,
                    'record_artifacts': record_artifacts,
                }
            )
            return {
                'status': 'product_failed',
                'infra_failure': False,
                'message': (
                    '[子任务] 部分子任务失败: '
                    f"{', '.join(child_failure_reasons)}"
                ),
                'payload': payload,
                'artifacts': {
                    'sample_root': str(sample_root),
                    'source_root': str(source_root),
                    'task_dir': str(task_output_path),
                    'record_dir': str(record_output_path),
                    'task_files': [str(item) for item in task_files],
                    'record_files': [str(item) for item in record_files],
                },
            }
        zero_mapping_routes = [
            route
            for route in routes
            if int(route.get('mapping_count') or 0) == 0
        ]
        if zero_mapping_routes:
            _clear_sample_side_effects(
                library_root=library_root,
                record_output_path=record_output_path,
            )
            task_files, record_files, routes, mapping, task_artifacts, record_artifacts = _collect_task_record_artifacts(
                task_output_path=task_output_path,
                record_output_path=record_output_path,
                source_root=source_root,
                sample_root=sample_root,
            )
            payload.update(
                {
                    'routes': routes,
                    'mapping': mapping,
                    'library_files': _collect_library_files(library_root, sample_root),
                    'task_artifacts': task_artifacts,
                    'record_artifacts': record_artifacts,
                }
            )
            return {
                'status': 'product_failed',
                'infra_failure': False,
                'message': '[子任务] 存在未产生映射的子任务',
                'payload': payload,
                'artifacts': {
                    'sample_root': str(sample_root),
                    'source_root': str(source_root),
                    'task_dir': str(task_output_path),
                    'record_dir': str(record_output_path),
                    'task_files': [str(item) for item in task_files],
                    'record_files': [str(item) for item in record_files],
                },
            }
        duplicate_targets = _find_duplicate_mapping_targets(mapping)
        if duplicate_targets:
            payload['duplicate_targets'] = duplicate_targets
            _clear_sample_side_effects(
                library_root=library_root,
                record_output_path=record_output_path,
            )
            task_files, record_files, routes, mapping, task_artifacts, record_artifacts = _collect_task_record_artifacts(
                task_output_path=task_output_path,
                record_output_path=record_output_path,
                source_root=source_root,
                sample_root=sample_root,
            )
            payload.update(
                {
                    'routes': routes,
                    'mapping': mapping,
                    'library_files': _collect_library_files(library_root, sample_root),
                    'task_artifacts': task_artifacts,
                    'record_artifacts': record_artifacts,
                }
            )
            payload['duplicate_targets'] = duplicate_targets
            return {
                'status': 'product_failed',
                'infra_failure': False,
                'message': (
                    '[映射] 多个源文件映射到同一目标: '
                    f"{duplicate_targets[0]['target_rel']}"
                ),
                'payload': payload,
                'artifacts': {
                    'sample_root': str(sample_root),
                    'source_root': str(source_root),
                    'task_dir': str(task_output_path),
                    'record_dir': str(record_output_path),
                    'task_files': [str(item) for item in task_files],
                    'record_files': [str(item) for item in record_files],
                },
            }
        return {
            'status': 'executed',
            'infra_failure': False,
            'message': '',
            'payload': payload,
            'artifacts': {
                'sample_root': str(sample_root),
                'source_root': str(source_root),
                'task_dir': str(task_output_path),
                'record_dir': str(record_output_path),
                'task_files': [str(item) for item in task_files],
                'record_files': [str(item) for item in record_files],
            },
        }


def _execute_with_retry(entry: RenameSample, sample_root: Path) -> dict[str, Any]:
    first_result = _execute_sample(entry, sample_root / 'attempt_1')
    retryable = first_result['status'] in {'infra_failed', 'product_failed'}
    if not retryable:
        return {'result': first_result, 'retry_count': 0, 'is_flaky': False}

    second_result = _execute_sample(entry, sample_root / 'attempt_2')
    if second_result['status'] == 'executed':
        return {'result': second_result, 'retry_count': 1, 'is_flaky': True}
    return {'result': second_result, 'retry_count': 1, 'is_flaky': False}


def run_rename_lane(
    *,
    entries: list[RenameSample],
    baseline_root: Path,
    sample_results_dir: Path,
    sandbox_root: Path,
    mode: str,
) -> tuple[RunSummary, list[str], list[str], list[str], list[str]]:
    sample_results: list[dict[str, Any]] = []
    flaky_samples: list[str] = []
    infra_failures: list[str] = []
    observation_failures: list[str] = []
    quarantine_candidates: list[str] = []

    passed_count = 0
    product_failure_count = 0
    infra_failure_count = 0
    baseline_missing_count = 0
    manual_review_count = 0

    runtime_signature = _build_runtime_signature()

    for entry in entries:
        started_at = _utc_now()
        sample_root = sandbox_root / entry.sample_id
        sample_root.mkdir(parents=True, exist_ok=True)
        artifact_path = sample_results_dir / f'{entry.sample_id}.json'

        try:
            execution = _execute_with_retry(entry, sample_root)
            execution_result = execution['result']
            retry_count = int(execution['retry_count'])
            is_flaky = bool(execution['is_flaky'])

            if mode == 'update-baseline' and execution_result['status'] == 'executed':
                baseline_record = BaselineRecord(
                    sample_id=entry.sample_id,
                    schema_version=1,
                    anchor=entry.anchor,
                    captured_at=_utc_now(),
                    runtime_signature=runtime_signature,
                    expected=dict(execution_result['payload']),
                    notes=['Generated by regression update-baseline mode.'],
                )
                baseline_path = save_baseline_record(baseline_root, baseline_record)
                comparison_summary = {
                    'matched': True,
                    'baseline_updated': True,
                    'baseline_path': str(baseline_path),
                }
                status = 'baseline_updated'
                infra_failure = False
            elif execution_result['status'] != 'executed':
                comparison_summary = {
                    'matched': False,
                    'message': execution_result['message'],
                }
                status = str(execution_result['status'])
                infra_failure = bool(execution_result['infra_failure'])
            else:
                baseline = load_baseline_record(baseline_root, sample_id=entry.sample_id)
                comparison_summary = compare_rename_result(
                    dict(execution_result['payload']),
                    baseline,
                    is_anchor=entry.anchor,
                )
                if comparison_summary.get('baseline_missing'):
                    status = 'baseline_missing'
                    infra_failure = False
                elif comparison_summary.get('matched'):
                    status = 'passed'
                    infra_failure = False
                elif mode == 'check':
                    status = 'product_failed'
                    infra_failure = False
                elif not entry.anchor:
                    status = 'observation_failed'
                    infra_failure = False
                else:
                    status = 'product_failed'
                    infra_failure = False

            if is_flaky and status in {'passed', 'baseline_updated'}:
                flaky_samples.append(entry.sample_id)

            if status == 'passed':
                passed_count += 1
            elif status == 'product_failed':
                product_failure_count += 1
                quarantine_candidates.append(entry.sample_id)
            elif status == 'infra_failed':
                infra_failure_count += 1
                infra_failures.append(entry.sample_id)
            elif status == 'baseline_missing':
                baseline_missing_count += 1
                manual_review_count += 1
                observation_failures.append(entry.sample_id)
            elif status == 'observation_failed':
                manual_review_count += 1
                observation_failures.append(entry.sample_id)
                quarantine_candidates.append(entry.sample_id)
            elif status == 'baseline_updated':
                manual_review_count += 1

            sample_result = SampleRunResult(
                sample_id=entry.sample_id,
                status=status,
                anchor=entry.anchor,
                is_flaky=is_flaky,
                infra_failure=infra_failure,
                retry_count=retry_count,
                comparison_summary=comparison_summary,
                artifacts=dict(execution_result.get('artifacts') or {}),
                started_at=started_at,
                finished_at=_utc_now(),
            )
        except Exception as exc:
            infra_failure_count += 1
            infra_failures.append(entry.sample_id)
            sample_result = SampleRunResult(
                sample_id=entry.sample_id,
                status='infra_failed',
                anchor=entry.anchor,
                is_flaky=False,
                infra_failure=True,
                retry_count=0,
                comparison_summary={
                    'matched': False,
                    'message': str(exc),
                    'traceback': traceback.format_exc(),
                },
                artifacts={'sample_root': str(sample_root)},
                started_at=started_at,
                finished_at=_utc_now(),
            )

        with open(artifact_path, 'w', encoding='utf-8') as file:
            json.dump(sample_result.to_dict(), file, indent=2, ensure_ascii=False)
            file.write('\n')
        sample_results.append(sample_result.to_dict())

    summary = RunSummary(
        selected_count=len(entries),
        completed_count=len(sample_results),
        passed_count=passed_count,
        product_failure_count=product_failure_count,
        infra_failure_count=infra_failure_count,
        flaky_count=len(flaky_samples),
        baseline_missing_count=baseline_missing_count,
        manual_review_count=manual_review_count,
        sample_results=sample_results,
    )
    return summary, flaky_samples, infra_failures, observation_failures, quarantine_candidates
