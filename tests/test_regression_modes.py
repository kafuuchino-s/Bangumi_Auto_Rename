from __future__ import annotations

from pathlib import Path

import pytest

from src.regression.cli import build_parser
from src.regression.compare.rename import compare_rename_result
from src.regression.lanes.rename import run_rename_lane
from src.regression.models import BaselineRecord, RunSummary
from src.regression.manifest import filter_manifest_entries
from src.regression.models import CANONICAL_MODE_CHOICES, MODE_CHOICES, RenameSample
from src.regression.runner import run_rename_regression


def _make_entry(sample_id: str = 'sample_001', check: bool = True) -> RenameSample:
    return RenameSample(
        sample_id=sample_id,
        sample_json=f'tests/sample_pool/raw/{sample_id}.json',
        check=check,
    )


def test_mode_choices_are_canonical_only():
    assert MODE_CHOICES == CANONICAL_MODE_CHOICES
    assert MODE_CHOICES == ('check', 'update-baseline', 'full')


def test_filter_manifest_entries_check_mode_uses_core_filter():
    entries = [_make_entry(), _make_entry('sample_002', check=False)]

    check_entries, check_notes = filter_manifest_entries(entries, mode='check')

    assert [entry.sample_id for entry in check_entries] == ['sample_001']
    assert check_notes == ['check filter applied']


def test_run_rename_regression_rejects_unknown_modes(tmp_path: Path, monkeypatch):
    entry = _make_entry()
    monkeypatch.setattr('src.regression.runner.load_manifest', lambda path: ('42', [entry]))

    with pytest.raises(ValueError, match='Unsupported mode: invalid-mode'):
        run_rename_regression(
            mode='invalid-mode',
            manifest=tmp_path / 'manifest.json',
            baseline_root=tmp_path / 'baseline',
            artifacts_root=tmp_path / 'artifacts',
        )


def test_run_rename_regression_check_mode_sets_gate_failed_on_product_failure(
    tmp_path: Path, monkeypatch
):
    entry = _make_entry()

    monkeypatch.setattr('src.regression.runner.load_manifest', lambda path: ('42', [entry]))
    monkeypatch.setattr('src.regression.runner._resolve_runtime_signature', lambda: ({}, {}))
    monkeypatch.setattr(
        'src.regression.runner.run_rename_lane',
        lambda **kwargs: (
            RunSummary(
                selected_count=1,
                completed_count=1,
                passed_count=0,
                product_failure_count=1,
                infra_failure_count=0,
                flaky_count=0,
                baseline_missing_count=0,
                manual_review_count=0,
                sample_results=[{'sample_id': entry.sample_id, 'status': 'product_failed'}],
            ),
            [],
            [],
            [],
            [entry.sample_id],
        ),
    )

    result = run_rename_regression(
        mode='check',
        manifest=tmp_path / 'manifest.json',
        baseline_root=tmp_path / 'baseline',
        artifacts_root=tmp_path / 'artifacts',
    )

    assert result['mode'] == 'check'
    assert result['selected_count'] == 1
    assert result['gate_failed'] is True
    assert result['exit_code'] == 2


def test_check_mode_mismatch_is_blocking_regardless_of_anchor(tmp_path: Path, monkeypatch):
    entry = RenameSample(sample_id='sample_001', sample_json='tests/sample_pool/raw/sample_001.json', check=True, anchor=False)

    def fake_execute_with_retry(_entry, sample_root):
        del sample_root
        return {
            'result': {
                'status': 'executed',
                'infra_failure': False,
                'message': '',
                'payload': {
                    'final_type': 'tv',
                    'routes': [],
                    'mapping': [{'route_type': 'tv', 'source_rel': 'a', 'target_rel': 'b'}],
                    'library_files': [],
                    'task_artifacts': [],
                    'record_artifacts': [],
                },
                'artifacts': {},
            },
            'retry_count': 0,
            'is_flaky': False,
        }

    baseline = BaselineRecord(
        sample_id='sample_001',
        schema_version=1,
        anchor=False,
        captured_at='2026-04-23T00:00:00+00:00',
        runtime_signature={},
        expected={
            'final_type': 'tv',
            'routes': [],
            'mapping': [],
            'library_files': [],
            'task_artifacts': [],
            'record_artifacts': [],
        },
    )

    monkeypatch.setattr('src.regression.lanes.rename._execute_with_retry', fake_execute_with_retry)
    monkeypatch.setattr('src.regression.lanes.rename.load_baseline_record', lambda baseline_root, sample_id: baseline)

    (tmp_path / 'baseline').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'results').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'sandbox').mkdir(parents=True, exist_ok=True)

    summary, *_ = run_rename_lane(
        entries=[entry],
        baseline_root=tmp_path / 'baseline',
        sample_results_dir=tmp_path / 'results',
        sandbox_root=tmp_path / 'sandbox',
        mode='check',
    )

    assert summary.sample_results[0]['status'] == 'product_failed'
    assert summary.product_failure_count == 1
    assert summary.manual_review_count == 0


def test_full_mode_non_check_mismatch_stays_observation_failed(tmp_path: Path, monkeypatch):
    entry = RenameSample(sample_id='sample_002', sample_json='tests/sample_pool/raw/sample_002.json', check=False, anchor=False)

    def fake_execute_with_retry(_entry, sample_root):
        del sample_root
        return {
            'result': {
                'status': 'executed',
                'infra_failure': False,
                'message': '',
                'payload': {
                    'final_type': 'tv',
                    'routes': [],
                    'mapping': [{'route_type': 'tv', 'source_rel': 'a', 'target_rel': 'b'}],
                    'library_files': [],
                    'task_artifacts': [],
                    'record_artifacts': [],
                },
                'artifacts': {},
            },
            'retry_count': 0,
            'is_flaky': False,
        }

    baseline = BaselineRecord(
        sample_id='sample_002',
        schema_version=1,
        anchor=False,
        captured_at='2026-04-23T00:00:00+00:00',
        runtime_signature={},
        expected={
            'final_type': 'tv',
            'routes': [],
            'mapping': [],
            'library_files': [],
            'task_artifacts': [],
            'record_artifacts': [],
        },
    )

    monkeypatch.setattr('src.regression.lanes.rename._execute_with_retry', fake_execute_with_retry)
    monkeypatch.setattr('src.regression.lanes.rename.load_baseline_record', lambda baseline_root, sample_id: baseline)

    (tmp_path / 'baseline').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'results').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'sandbox').mkdir(parents=True, exist_ok=True)

    summary, *_ = run_rename_lane(
        entries=[entry],
        baseline_root=tmp_path / 'baseline',
        sample_results_dir=tmp_path / 'results',
        sandbox_root=tmp_path / 'sandbox',
        mode='full',
    )

    assert summary.sample_results[0]['status'] == 'observation_failed'
    assert summary.product_failure_count == 0
    assert summary.manual_review_count == 1


def test_anchor_still_only_expands_compare_scope(tmp_path: Path):
    baseline = BaselineRecord(
        sample_id='sample_003',
        schema_version=1,
        anchor=False,
        captured_at='2026-04-23T00:00:00+00:00',
        runtime_signature={},
        expected={
            'final_type': 'tv',
            'routes': [],
            'mapping': [],
            'library_files': [{'path': 'extra.mkv', 'size': 1}],
            'task_artifacts': [],
            'record_artifacts': [],
        },
    )
    actual = {
        'final_type': 'tv',
        'routes': [],
        'mapping': [],
        'library_files': [],
        'task_artifacts': [],
        'record_artifacts': [],
    }

    non_anchor_summary = compare_rename_result(actual, baseline, is_anchor=False)
    anchor_summary = compare_rename_result(actual, baseline, is_anchor=True)

    assert non_anchor_summary['matched'] is True
    assert non_anchor_summary['mismatch_fields'] == []
    assert anchor_summary['matched'] is False
    assert anchor_summary['mismatch_fields'] == ['library_files']


def test_compare_rename_result_ignores_route_order_for_same_content():
    baseline = BaselineRecord(
        sample_id='sample_routes',
        schema_version=1,
        anchor=True,
        captured_at='2026-04-23T00:00:00+00:00',
        runtime_signature={},
        expected={
            'final_type': 'mixed',
            'routes': [
                {
                    'route_type': 'movie',
                    'tmdb_id': 635302,
                    'season_id': 0,
                    'tmdb_name': '鬼灭之刃剧场版：无限列车篇',
                    'target_root_rel': '鬼灭之刃剧场版：无限列车篇 (2020)',
                    'mapping_count': 1,
                },
                {
                    'route_type': 'tv',
                    'tmdb_id': 85937,
                    'season_id': 3,
                    'tmdb_name': '鬼灭之刃',
                    'target_root_rel': '鬼灭之刃 (2019)',
                    'mapping_count': 11,
                },
                {
                    'route_type': 'tv',
                    'tmdb_id': 85937,
                    'season_id': 2,
                    'tmdb_name': '鬼灭之刃',
                    'target_root_rel': '鬼灭之刃 (2019)',
                    'mapping_count': 7,
                },
            ],
            'mapping': [],
            'library_files': [],
            'task_artifacts': [],
            'record_artifacts': [],
        },
    )
    actual = {
        'final_type': 'mixed',
        'routes': [
            {
                'route_type': 'tv',
                'tmdb_id': 85937,
                'season_id': 2,
                'tmdb_name': '鬼灭之刃',
                'target_root_rel': '鬼灭之刃 (2019)',
                'mapping_count': 7,
            },
            {
                'route_type': 'movie',
                'tmdb_id': 635302,
                'season_id': 0,
                'tmdb_name': '鬼灭之刃剧场版：无限列车篇',
                'target_root_rel': '鬼灭之刃剧场版：无限列车篇 (2020)',
                'mapping_count': 1,
            },
            {
                'route_type': 'tv',
                'tmdb_id': 85937,
                'season_id': 3,
                'tmdb_name': '鬼灭之刃',
                'target_root_rel': '鬼灭之刃 (2019)',
                'mapping_count': 11,
            },
        ],
        'mapping': [],
        'library_files': [],
        'task_artifacts': [],
        'record_artifacts': [],
    }

    summary = compare_rename_result(actual, baseline, is_anchor=True)

    assert summary['matched'] is True
    assert summary['mismatch_fields'] == []


def test_compare_rename_result_ignores_uuid_only_artifact_name_differences():
    baseline = BaselineRecord(
        sample_id='sample_artifacts',
        schema_version=1,
        anchor=True,
        captured_at='2026-04-23T00:00:00+00:00',
        runtime_signature={},
        expected={
            'final_type': 'mixed',
            'routes': [],
            'mapping': [],
            'library_files': [],
            'task_artifacts': [
                {
                    'artifact_name': '11111111-1111-1111-1111-111111111111.json',
                    'source_rel': 'Movie/main.mkv',
                    'route_type': 'movie',
                    'pipeline_mode': 'ai_strict',
                    'tmdb_id': 635302,
                    'tmdb_name': '鬼灭之刃剧场版：无限列车篇',
                    'season_id': 0,
                    'target_root_rel': '鬼灭之刃剧场版：无限列车篇 (2020)',
                    'is_mixed_parent': False,
                    'failure_reason': None,
                }
            ],
            'record_artifacts': [
                {
                    'artifact_name': '22222222-2222-2222-2222-222222222222.json',
                    'mapping': [
                        {
                            'route_type': 'movie',
                            'source_rel': 'Movie/main.mkv',
                            'target_rel': 'anime_movie/鬼灭之刃剧场版：无限列车篇 (2020)/main.mkv',
                        }
                    ],
                }
            ],
        },
    )
    actual = {
        'final_type': 'mixed',
        'routes': [],
        'mapping': [],
        'library_files': [],
        'task_artifacts': [
            {
                'artifact_name': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json',
                'source_rel': 'Movie/main.mkv',
                'route_type': 'movie',
                'pipeline_mode': 'ai_strict',
                'tmdb_id': 635302,
                'tmdb_name': '鬼灭之刃剧场版：无限列车篇',
                'season_id': 0,
                'target_root_rel': '鬼灭之刃剧场版：无限列车篇 (2020)',
                'is_mixed_parent': False,
                'failure_reason': None,
            }
        ],
        'record_artifacts': [
            {
                'artifact_name': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.json',
                'mapping': [
                    {
                        'route_type': 'movie',
                        'source_rel': 'Movie/main.mkv',
                        'target_rel': 'anime_movie/鬼灭之刃剧场版：无限列车篇 (2020)/main.mkv',
                    }
                ],
            }
        ],
    }

    summary = compare_rename_result(actual, baseline, is_anchor=True)

    assert summary['matched'] is True
    assert summary['mismatch_fields'] == []


def test_cli_help_lists_only_public_modes():
    parser = build_parser()
    help_text = parser.format_help()

    assert 'Public modes: check, update-baseline, full.' in help_text
    assert '--mode {check,update-baseline,full}' in help_text
    assert '--manifest MANIFEST' in help_text
    assert '--baseline-root BASELINE_ROOT' in help_text
    assert '--artifacts-root ARTIFACTS_ROOT' in help_text
    assert '--sample-id SAMPLE_ID' in help_text
    assert '--max-samples MAX_SAMPLES' in help_text
