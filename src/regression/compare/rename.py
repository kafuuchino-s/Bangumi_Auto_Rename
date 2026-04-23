from __future__ import annotations

import json
from typing import Any

from ..models import BaselineRecord


def _sorted_mapping(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            str(item.get('route_type') or ''),
            str(item.get('target_rel') or ''),
            str(item.get('source_rel') or ''),
        ),
    )


def _sorted_routes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            str(item.get('route_type') or ''),
            int(item.get('tmdb_id') or 0),
            int(item.get('season_id') or 0),
            int(item.get('mapping_count') or 0),
            str(item.get('target_root_rel') or ''),
            str(item.get('tmdb_name') or ''),
        ),
    )


def _sorted_output_files(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: str(item.get('path') or ''),
    )


def _normalize_task_artifact(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized.pop('artifact_name', None)
    return normalized


def _sorted_task_artifacts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_items = [_normalize_task_artifact(item) for item in items]
    return sorted(
        normalized_items,
        key=lambda item: (
            str(item.get('route_type') or ''),
            str(item.get('target_root_rel') or ''),
            str(item.get('source_rel') or ''),
            int(item.get('tmdb_id') or 0),
            int(item.get('season_id') or 0),
            str(item.get('pipeline_mode') or ''),
            str(item.get('failure_reason') or ''),
            json.dumps(item.get('mixed_execution') or {}, sort_keys=True, ensure_ascii=False),
        ),
    )


def _normalize_record_artifact(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized.pop('artifact_name', None)
    normalized['mapping'] = _sorted_mapping(list(normalized.get('mapping') or []))
    return normalized


def _sorted_record_artifacts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_items = [_normalize_record_artifact(item) for item in items]
    return sorted(
        normalized_items,
        key=lambda item: (
            json.dumps(item.get('mapping') or [], sort_keys=True, ensure_ascii=False),
            json.dumps(item.get('mixed_execution') or {}, sort_keys=True, ensure_ascii=False),
        ),
    )


def compare_rename_result(
    actual: dict[str, Any],
    baseline: BaselineRecord | None,
    *,
    is_anchor: bool,
) -> dict[str, Any]:
    if baseline is None:
        return {
            'matched': False,
            'baseline_missing': True,
            'mismatch_fields': ['baseline'],
            'details': {'message': 'baseline file not found'},
        }

    expected = baseline.expected
    mismatch_fields: list[str] = []
    details: dict[str, Any] = {}

    final_type = actual.get('final_type')
    if final_type != expected.get('final_type'):
        mismatch_fields.append('final_type')
        details['final_type'] = {
            'expected': expected.get('final_type'),
            'actual': final_type,
        }

    actual_routes = _sorted_routes(list(actual.get('routes') or []))
    expected_routes = _sorted_routes(list(expected.get('routes') or []))
    if actual_routes != expected_routes:
        mismatch_fields.append('routes')
        details['routes'] = {'expected': expected_routes, 'actual': actual_routes}

    actual_mapping = _sorted_mapping(list(actual.get('mapping') or []))
    expected_mapping = _sorted_mapping(list(expected.get('mapping') or []))
    if actual_mapping != expected_mapping:
        mismatch_fields.append('mapping')
        details['mapping'] = {'expected': expected_mapping, 'actual': actual_mapping}

    actual_output_files = _sorted_output_files(list(actual.get('library_files') or []))
    expected_output_files = _sorted_output_files(list(expected.get('library_files') or []))
    if is_anchor and actual_output_files != expected_output_files:
        mismatch_fields.append('library_files')
        details['library_files'] = {
            'expected': expected_output_files,
            'actual': actual_output_files,
        }

    actual_task_artifacts = _sorted_task_artifacts(list(actual.get('task_artifacts') or []))
    expected_task_artifacts = _sorted_task_artifacts(list(expected.get('task_artifacts') or []))
    if is_anchor and actual_task_artifacts != expected_task_artifacts:
        mismatch_fields.append('task_artifacts')
        details['task_artifacts'] = {
            'expected': expected_task_artifacts,
            'actual': actual_task_artifacts,
        }

    actual_record_artifacts = _sorted_record_artifacts(list(actual.get('record_artifacts') or []))
    expected_record_artifacts = _sorted_record_artifacts(list(expected.get('record_artifacts') or []))
    if is_anchor and actual_record_artifacts != expected_record_artifacts:
        mismatch_fields.append('record_artifacts')
        details['record_artifacts'] = {
            'expected': expected_record_artifacts,
            'actual': actual_record_artifacts,
        }

    return {
        'matched': not mismatch_fields,
        'baseline_missing': False,
        'mismatch_fields': mismatch_fields,
        'details': details,
    }
