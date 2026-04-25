from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.regression.cli import build_parser
from src.regression.compare.rename import compare_rename_result
from src.regression.lanes.rename import (
    _classify_process_failure,
    _execute_sample,
    _find_duplicate_mapping_targets,
    _normalize_task_artifact,
    build_rename_lane_contract,
    build_rename_lane_observation,
    run_rename_lane,
)
from src.regression.report import build_report_markdown
from src.regression.models import BaselineRecord, RunSummary
from src.regression.manifest import (
    expand_protected_samples,
    filter_manifest_entries,
    infer_risk_tags_from_changed_paths,
)
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


def test_cli_parser_supports_repeatable_sample_id():
    args = build_parser().parse_args(['--sample-id', 'sample_001', '--sample-id', 'sample_002'])

    assert args.sample_id == ['sample_001', 'sample_002']


def test_filter_manifest_entries_keeps_manifest_order_for_multiple_sample_ids():
    entries = [_make_entry('sample_002'), _make_entry('sample_001'), _make_entry('sample_003')]

    filtered, notes = filter_manifest_entries(
        entries,
        mode='full',
        sample_id=['sample_003', 'sample_001'],
    )

    assert [entry.sample_id for entry in filtered] == ['sample_001', 'sample_003']
    assert notes == ['sample_id filter applied: sample_003, sample_001', 'full selection applied']


def test_expand_protected_samples_adds_matching_protector_and_preserves_order():
    sample_0117 = RenameSample(
        sample_id='sample_0117',
        sample_json='tests/sample_pool/raw/sample_0117.json',
        tags=['tv_strict_mapping'],
    )
    sample_0013 = RenameSample(
        sample_id='sample_0013',
        sample_json='tests/sample_pool/raw/sample_0013.json',
        tags=['movie_resolution'],
    )
    sample_0091 = RenameSample(
        sample_id='sample_0091',
        sample_json='tests/sample_pool/raw/sample_0091.json',
        protects=['tv_strict_mapping', 'movie_resolution'],
    )

    expanded, scope_expansion, auto_added_ids = expand_protected_samples(
        [sample_0117, sample_0013, sample_0091],
        [sample_0117],
    )

    assert [entry.sample_id for entry in expanded] == ['sample_0117', 'sample_0091']
    assert auto_added_ids == ['sample_0091']
    assert scope_expansion == [
        {
            'requested_sample_id': 'sample_0117',
            'added_sample_id': 'sample_0091',
            'reason': 'protects',
            'matched_tags': ['tv_strict_mapping'],
        }
    ]


def test_expand_protected_samples_supports_always_with():
    root = RenameSample(
        sample_id='sample_root',
        sample_json='tests/sample_pool/raw/sample_root.json',
        always_with=['sample_guard'],
    )
    guard = RenameSample(
        sample_id='sample_guard',
        sample_json='tests/sample_pool/raw/sample_guard.json',
    )

    expanded, scope_expansion, auto_added_ids = expand_protected_samples(
        [root, guard],
        [root],
    )

    assert [entry.sample_id for entry in expanded] == ['sample_root', 'sample_guard']
    assert auto_added_ids == ['sample_guard']
    assert scope_expansion == [
        {
            'requested_sample_id': 'sample_root',
            'added_sample_id': 'sample_guard',
            'reason': 'always_with',
        }
    ]


def test_infer_risk_tags_from_changed_paths_matches_rules_in_order():
    tags, inference = infer_risk_tags_from_changed_paths(
        [
            '.\\src\\rename\\ai_processor.py',
            'src/regression/compare/rename.py',
            'docs/README.md',
        ]
    )

    assert tags == [
        'tv_strict_mapping',
        'episode_dedupe',
        'season_numbering',
        'compare_normalization',
    ]
    assert inference == [
        {
            'path': 'src/rename/ai_processor.py',
            'matched_tags': ['tv_strict_mapping', 'episode_dedupe', 'season_numbering'],
            'matched_rules': ['src/rename/ai_processor.py'],
        },
        {
            'path': 'src/regression/compare/rename.py',
            'matched_tags': ['compare_normalization'],
            'matched_rules': ['src/regression/compare/rename.py', 'src/regression/*.py'],
        },
    ]


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


def test_tmdb_not_found_is_product_failure_not_infra_failure():
    status, infra_failure = _classify_process_failure('[TMDB] 未搜索到匹配结果')

    assert status == 'product_failed'
    assert infra_failure is False


def test_tmdb_no_result_detail_is_product_failure_not_infra_failure():
    status, infra_failure = _classify_process_failure(
        "未能完成全部电影映射: TMDB无结果:Wake Up, Girls! 青春之影"
    )

    assert status == 'product_failed'
    assert infra_failure is False


def test_rename_lane_contract_is_authoritative_main_flow_contract():
    contract = build_rename_lane_contract()

    assert contract['runner_kind'] == 'rename_lane_main_flow'
    assert contract['uses_runtime_rename_process'] is True
    assert contract['uses_shadow_candidate_logic'] is False
    assert contract['authoritative_for_sample_pool'] is True
    assert 'Rename.process' in contract['runtime_entrypoint']


def test_rename_lane_observation_uses_contract_and_payload_summary():
    observation = build_rename_lane_observation(
        entry=RenameSample(
            sample_id='sample_contract',
            sample_json='tests/sample_pool/raw/tv/sample_contract.json',
            check=False,
            anchor=True,
            tags=['tv_strict_mapping'],
        ),
        execution_result={
            'status': 'executed',
            'infra_failure': False,
            'message': '',
            'payload': {
                'final_type': 'tv',
                'routes': [{'route_type': 'tv', 'tmdb_id': 1}],
                'mapping': [{'source_rel': 'a.mkv', 'target_rel': 'Show/S01E01.mkv'}],
                'task_artifacts': [{'pipeline_mode': 'ai_strict', 'failure_reason': None}],
                'record_artifacts': [],
                'library_files': [],
            },
            'artifacts': {'sample_root': 'sandbox/sample_contract'},
        },
        manifest_version='test-manifest',
    )

    assert observation['artifact_type'] == 'rename_lane_main_flow_observation'
    assert observation['runner_kind'] == 'rename_lane_main_flow'
    assert observation['lane_contract']['authoritative_for_sample_pool'] is True
    assert observation['summary']['final_type'] == 'tv'
    assert observation['summary']['route_count'] == 1
    assert observation['summary']['mapping_count'] == 1
    assert observation['summary']['pipeline_modes'] == ['ai_strict']


def test_duplicate_mapping_targets_are_detected():
    duplicates = _find_duplicate_mapping_targets(
        [
            {'source_rel': 'A/part1.mkv', 'target_rel': 'Movie/Movie.mkv'},
            {'source_rel': 'B/part2.mkv', 'target_rel': 'movie/movie.mkv'},
        ]
    )

    assert duplicates == [
        {
            'target_rel': 'movie/movie.mkv',
            'first_source_rel': 'A/part1.mkv',
            'second_source_rel': 'B/part2.mkv',
        }
    ]


def test_normalize_task_artifact_preserves_video_discovery(tmp_path: Path):
    source_root = tmp_path / 'source'
    sample_root = tmp_path / 'sample'
    source_root.mkdir()
    sample_root.mkdir()
    task_path = tmp_path / 'task.json'
    task_data = {
        'path': str(source_root),
        'uuid': 'task-video-debug',
        'failure_reason': 'ai_empty_mapping',
        'video_discovery': {
            'raw_video_count': 3,
            'promo_video_count': 3,
            'processable_video_count': 0,
        },
        'unmapped_potential_main_files': ['Episode 13.mkv'],
        'ignored_supplemental_relative_paths': ['NCOP.mkv'],
    }

    artifact = _normalize_task_artifact(
        task_path=task_path,
        task_data=task_data,
        source_root=source_root,
        sample_root=sample_root,
    )

    assert artifact['video_discovery']['raw_video_count'] == 3
    assert artifact['video_discovery']['processable_video_count'] == 0
    assert artifact['unmapped_potential_main_files'] == ['Episode 13.mkv']
    assert artifact['ignored_supplemental_relative_paths'] == ['NCOP.mkv']


def test_rename_lane_missing_task_record_artifacts_is_product_failure(tmp_path: Path, monkeypatch):
    sample_json = tmp_path / 'sample_no_artifact.json'
    sample_json.write_text(
        '{"root_name":"No Artifact","files":[{"path":"No.Artifact.S01E01.mkv"}]}',
        encoding='utf-8',
    )
    entry = RenameSample(sample_id='sample_no_artifact', sample_json=str(sample_json), check=True)

    def fake_process(self, *args, **kwargs):  # noqa: ANN001, ARG001
        return True

    monkeypatch.setattr('src.regression.lanes.rename.Rename.process', fake_process)

    sample_results_dir = tmp_path / 'results'
    sample_results_dir.mkdir()

    summary, _flaky_samples, infra_failures, observation_failures, quarantine_candidates = run_rename_lane(
        entries=[entry],
        baseline_root=tmp_path / 'baseline',
        sample_results_dir=sample_results_dir,
        sandbox_root=tmp_path / 'sandbox',
        mode='check',
    )

    assert summary.product_failure_count == 1
    assert summary.infra_failure_count == 0
    assert infra_failures == []
    assert observation_failures == []
    assert quarantine_candidates == ['sample_no_artifact']


def test_execute_sample_missing_artifacts_has_synthetic_failure_reason(tmp_path: Path, monkeypatch):
    sample_json = tmp_path / 'sample_no_artifact.json'
    sample_json.write_text(
        '{"root_name":"No Artifact","files":[{"path":"No.Artifact.S01E01.mkv"}]}',
        encoding='utf-8',
    )
    entry = RenameSample(sample_id='sample_no_artifact', sample_json=str(sample_json), check=True)

    def fake_process(self, *args, **kwargs):  # noqa: ANN001, ARG001
        return True

    monkeypatch.setattr('src.regression.lanes.rename.Rename.process', fake_process)

    result = _execute_sample(entry, tmp_path / 'sandbox')

    assert result['status'] == 'product_failed'
    artifact = result['payload']['task_artifacts'][0]
    assert artifact['artifact_name'] == 'synthetic_missing_runtime_artifacts'
    assert artifact['failure_reason'] == 'missing_runtime_artifacts'
    assert artifact['process_message'] == 'rename process completed without task/record artifacts'
    assert artifact['video_discovery']['source_video_count'] == 1


def test_rename_lane_child_failure_artifact_is_product_failure(tmp_path: Path, monkeypatch):
    sample_json = tmp_path / 'sample_child_failure.json'
    sample_json.write_text(
        '{"root_name":"Child Failure","files":[{"relative_path":"Child Failure/Child.Failure.S01E01.mkv"}]}',
        encoding='utf-8',
    )
    entry = RenameSample(sample_id='sample_child_failure', sample_json=str(sample_json), check=True)
    sample_root = tmp_path / 'sandbox'
    task_dir = sample_root / 'data' / 'task'
    record_dir = sample_root / 'data' / 'record'
    library_file = sample_root / 'library' / 'anime' / 'Show (2024)' / 'Season 01' / 'Show - S01E01.mkv'
    task_dir.mkdir(parents=True)
    record_dir.mkdir(parents=True)
    library_file.parent.mkdir(parents=True)

    task_payload = {
        'source_path': str(sample_root / 'source' / 'Child Failure'),
        'is_anime': True,
        'is_movie': False,
        'tmdb_id': None,
        'tmdb_name': None,
        'season_id': None,
        'target_root': '',
        'failure_reason': 'ai_timeout',
    }

    def fake_process(self, *args, **kwargs):  # noqa: ANN001, ARG001
        (task_dir / 'child.json').write_text(json.dumps(task_payload), encoding='utf-8')
        (record_dir / 'child.json').write_text(
            json.dumps(
                {
                    'mapping': [
                        {
                            'source_rel': 'Child.Failure.S01E01.mkv',
                            'target_rel': 'Show (2024)/Season 01/Show - S01E01.mkv',
                        }
                    ]
                }
            ),
            encoding='utf-8',
        )
        library_file.write_bytes(b'')
        return True

    monkeypatch.setattr('src.regression.lanes.rename.Rename.process', fake_process)
    result = _execute_sample(entry, sample_root)

    assert result['status'] == 'product_failed'
    assert result['infra_failure'] is False
    assert 'ai_timeout' in result['message']
    assert result['payload']['record_artifacts'] == []
    assert result['payload']['library_files'] == []


def test_run_rename_regression_auto_adds_protected_samples(tmp_path: Path, monkeypatch):
    requested = RenameSample(
        sample_id='sample_0117',
        sample_json='tests/sample_pool/raw/sample_0117.json',
        tags=['tv_strict_mapping'],
    )
    protector = RenameSample(
        sample_id='sample_0091',
        sample_json='tests/sample_pool/raw/sample_0091.json',
        protects=['tv_strict_mapping'],
    )

    monkeypatch.setattr(
        'src.regression.runner.load_manifest',
        lambda path: ('42', [requested, protector]),
    )
    monkeypatch.setattr('src.regression.runner._resolve_runtime_signature', lambda: ({}, {}))

    captured_entries = []

    def fake_run_rename_lane(**kwargs):
        captured_entries.extend(entry.sample_id for entry in kwargs['entries'])
        return (
            RunSummary(
                selected_count=2,
                completed_count=2,
                passed_count=2,
                product_failure_count=0,
                infra_failure_count=0,
                flaky_count=0,
                baseline_missing_count=0,
                manual_review_count=0,
                sample_results=[
                    {'sample_id': 'sample_0117', 'status': 'passed'},
                    {'sample_id': 'sample_0091', 'status': 'passed'},
                ],
            ),
            [],
            [],
            [],
            [],
        )

    monkeypatch.setattr('src.regression.runner.run_rename_lane', fake_run_rename_lane)

    result = run_rename_regression(
        mode='full',
        manifest=tmp_path / 'manifest.json',
        baseline_root=tmp_path / 'baseline',
        artifacts_root=tmp_path / 'artifacts',
        sample_id='sample_0117',
    )

    assert captured_entries == ['sample_0117', 'sample_0091']
    assert result['requested_sample_ids'] == ['sample_0117']
    assert result['auto_added_sample_ids'] == ['sample_0091']
    assert result['selected_sample_ids'] == ['sample_0117', 'sample_0091']


def test_run_rename_regression_supports_multiple_requested_sample_ids(tmp_path: Path, monkeypatch):
    first = RenameSample(sample_id='sample_001', sample_json='tests/sample_pool/raw/sample_001.json')
    second = RenameSample(sample_id='sample_002', sample_json='tests/sample_pool/raw/sample_002.json')

    monkeypatch.setattr('src.regression.runner.load_manifest', lambda path: ('42', [first, second]))
    monkeypatch.setattr('src.regression.runner._resolve_runtime_signature', lambda: ({}, {}))

    captured_entries = []

    def fake_run_rename_lane(**kwargs):
        captured_entries.extend(entry.sample_id for entry in kwargs['entries'])
        return (
            RunSummary(
                selected_count=2,
                completed_count=2,
                passed_count=2,
                product_failure_count=0,
                infra_failure_count=0,
                flaky_count=0,
                baseline_missing_count=0,
                manual_review_count=0,
                sample_results=[
                    {'sample_id': 'sample_001', 'status': 'passed'},
                    {'sample_id': 'sample_002', 'status': 'passed'},
                ],
            ),
            [],
            [],
            [],
            [],
        )

    monkeypatch.setattr('src.regression.runner.run_rename_lane', fake_run_rename_lane)

    result = run_rename_regression(
        mode='full',
        manifest=tmp_path / 'manifest.json',
        baseline_root=tmp_path / 'baseline',
        artifacts_root=tmp_path / 'artifacts',
        sample_id=['sample_002', 'sample_001'],
    )

    assert captured_entries == ['sample_001', 'sample_002']
    assert result['requested_sample_ids'] == ['sample_002', 'sample_001']
    assert result['selected_sample_ids'] == ['sample_001', 'sample_002']
    assert result['auto_added_sample_ids'] == []


def test_run_rename_regression_auto_detected_changed_paths_filters_noise(tmp_path: Path, monkeypatch):
    requested = RenameSample(
        sample_id='sample_0006',
        sample_json='tests/sample_pool/raw/sample_0006.json',
    )
    protector = RenameSample(
        sample_id='sample_0091',
        sample_json='tests/sample_pool/raw/sample_0091.json',
        protects=['tv_strict_mapping', 'compare_normalization'],
    )

    monkeypatch.setattr(
        'src.regression.runner.load_manifest',
        lambda path: ('42', [requested, protector]),
    )
    monkeypatch.setattr('src.regression.runner._resolve_runtime_signature', lambda: ({}, {}))
    monkeypatch.setattr(
        'src.regression.runner._discover_changed_paths',
        lambda: [
            '.vscode/settings.json',
            'src/rename/ai_processor.py',
            'src/regression/runner.py',
            '.sisyphus/tmp/file.txt',
            'tmp_prompt.txt',
        ],
    )

    captured_entries = []

    def fake_run_rename_lane(**kwargs):
        captured_entries.extend(entry.sample_id for entry in kwargs['entries'])
        return (
            RunSummary(
                selected_count=2,
                completed_count=2,
                passed_count=2,
                product_failure_count=0,
                infra_failure_count=0,
                flaky_count=0,
                baseline_missing_count=0,
                manual_review_count=0,
                sample_results=[
                    {'sample_id': 'sample_0006', 'status': 'passed'},
                    {'sample_id': 'sample_0091', 'status': 'passed'},
                ],
            ),
            [],
            [],
            [],
            [],
        )

    monkeypatch.setattr('src.regression.runner.run_rename_lane', fake_run_rename_lane)

    result = run_rename_regression(
        mode='full',
        manifest=tmp_path / 'manifest.json',
        baseline_root=tmp_path / 'baseline',
        artifacts_root=tmp_path / 'artifacts',
        sample_id='sample_0006',
    )

    assert captured_entries == ['sample_0006', 'sample_0091']
    assert result['changed_paths'] == ['src/rename/ai_processor.py', 'src/regression/runner.py']
    assert result['inferred_risk_tags'] == [
        'tv_strict_mapping',
        'episode_dedupe',
        'season_numbering',
        'compare_normalization',
    ]
    assert result['auto_added_sample_ids'] == ['sample_0091']


def test_run_rename_regression_can_disable_protected_sample_expansion(tmp_path: Path, monkeypatch):
    requested = RenameSample(
        sample_id='sample_0117',
        sample_json='tests/sample_pool/raw/sample_0117.json',
        tags=['tv_strict_mapping'],
    )
    protector = RenameSample(
        sample_id='sample_0091',
        sample_json='tests/sample_pool/raw/sample_0091.json',
        protects=['tv_strict_mapping'],
    )

    monkeypatch.setattr(
        'src.regression.runner.load_manifest',
        lambda path: ('42', [requested, protector]),
    )
    monkeypatch.setattr('src.regression.runner._resolve_runtime_signature', lambda: ({}, {}))

    captured_entries = []

    def fake_run_rename_lane(**kwargs):
        captured_entries.extend(entry.sample_id for entry in kwargs['entries'])
        return (
            RunSummary(
                selected_count=1,
                completed_count=1,
                passed_count=1,
                product_failure_count=0,
                infra_failure_count=0,
                flaky_count=0,
                baseline_missing_count=0,
                manual_review_count=0,
                sample_results=[{'sample_id': 'sample_0117', 'status': 'passed'}],
            ),
            [],
            [],
            [],
            [],
        )

    monkeypatch.setattr('src.regression.runner.run_rename_lane', fake_run_rename_lane)

    result = run_rename_regression(
        mode='full',
        manifest=tmp_path / 'manifest.json',
        baseline_root=tmp_path / 'baseline',
        artifacts_root=tmp_path / 'artifacts',
        sample_id='sample_0117',
        expand_protected_samples_enabled=False,
    )

    assert captured_entries == ['sample_0117']
    assert result['requested_sample_ids'] == ['sample_0117']
    assert result['auto_added_sample_ids'] == []
    assert result['selected_sample_ids'] == ['sample_0117']


def test_run_rename_regression_auto_adds_protected_samples_from_changed_paths(tmp_path: Path, monkeypatch):
    requested = RenameSample(
        sample_id='sample_0006',
        sample_json='tests/sample_pool/raw/sample_0006.json',
    )
    protector = RenameSample(
        sample_id='sample_0091',
        sample_json='tests/sample_pool/raw/sample_0091.json',
        protects=['tv_strict_mapping', 'movie_resolution'],
    )

    monkeypatch.setattr(
        'src.regression.runner.load_manifest',
        lambda path: ('42', [requested, protector]),
    )
    monkeypatch.setattr('src.regression.runner._resolve_runtime_signature', lambda: ({}, {}))

    captured_entries = []

    def fake_run_rename_lane(**kwargs):
        captured_entries.extend(entry.sample_id for entry in kwargs['entries'])
        return (
            RunSummary(
                selected_count=2,
                completed_count=2,
                passed_count=2,
                product_failure_count=0,
                infra_failure_count=0,
                flaky_count=0,
                baseline_missing_count=0,
                manual_review_count=0,
                sample_results=[
                    {'sample_id': 'sample_0006', 'status': 'passed'},
                    {'sample_id': 'sample_0091', 'status': 'passed'},
                ],
            ),
            [],
            [],
            [],
            [],
        )

    monkeypatch.setattr('src.regression.runner.run_rename_lane', fake_run_rename_lane)

    result = run_rename_regression(
        mode='full',
        manifest=tmp_path / 'manifest.json',
        baseline_root=tmp_path / 'baseline',
        artifacts_root=tmp_path / 'artifacts',
        sample_id='sample_0006',
        changed_paths=['src/rename/ai_processor.py'],
    )

    assert captured_entries == ['sample_0006', 'sample_0091']
    assert result['requested_sample_ids'] == ['sample_0006']
    assert result['auto_added_sample_ids'] == ['sample_0091']
    assert result['selected_sample_ids'] == ['sample_0006', 'sample_0091']
    assert result['changed_paths'] == ['src/rename/ai_processor.py']
    assert result['inferred_risk_tags'] == [
        'tv_strict_mapping',
        'episode_dedupe',
        'season_numbering',
    ]


def test_build_report_markdown_includes_scope_expansion_metadata():
    report = RunSummary(
        selected_count=2,
        completed_count=2,
        passed_count=2,
        product_failure_count=0,
        infra_failure_count=0,
        flaky_count=0,
        baseline_missing_count=0,
        manual_review_count=0,
        sample_results=[
            {'sample_id': 'sample_0117', 'status': 'passed'},
            {'sample_id': 'sample_0091', 'status': 'passed'},
        ],
    )
    from src.regression.models import RunContext, RunReport

    markdown = build_report_markdown(
        RunReport(
            run_context=RunContext(
                run_id='run-1',
                mode='full',
                started_at='2026-04-23T00:00:00+00:00',
                manifest_version='42',
                manifest_snapshot_path='snapshot.json',
                baseline_root='baseline',
                artifacts_root='artifacts',
                ai_model_info={},
                provider_version_info={},
                selected_sample_ids=['sample_0117', 'sample_0091'],
                requested_sample_ids=['sample_0117'],
                auto_added_sample_ids=['sample_0091'],
                scope_expansion=[
                    {
                        'requested_sample_id': 'sample_0117',
                        'added_sample_id': 'sample_0091',
                        'reason': 'protects',
                        'matched_tags': ['tv_strict_mapping'],
                    }
                ],
            ),
            summary=report,
            gate_result={
                'gate_failed': False,
                'product_failure_count': 0,
                'infra_failure_count': 0,
                'flaky_count': 0,
            },
            flaky_samples=[],
            infra_failures=[],
            observation_failures=[],
            quarantine_candidates=[],
        )
    )

    assert '#### Requested Sample IDs' in markdown
    assert '`sample_0117`' in markdown
    assert '#### Auto-added Protected Sample IDs' in markdown
    assert '`sample_0091`' in markdown
    assert '#### Scope Expansion' in markdown
    assert '`sample_0117` -> `sample_0091` via `protects`' in markdown


def test_build_report_markdown_includes_changed_path_inference_metadata():
    report = RunSummary(
        selected_count=2,
        completed_count=2,
        passed_count=2,
        product_failure_count=0,
        infra_failure_count=0,
        flaky_count=0,
        baseline_missing_count=0,
        manual_review_count=0,
        sample_results=[
            {'sample_id': 'sample_0006', 'status': 'passed'},
            {'sample_id': 'sample_0091', 'status': 'passed'},
        ],
    )
    from src.regression.models import RunContext, RunReport

    markdown = build_report_markdown(
        RunReport(
            run_context=RunContext(
                run_id='run-2',
                mode='full',
                started_at='2026-04-23T00:00:00+00:00',
                manifest_version='42',
                manifest_snapshot_path='snapshot.json',
                baseline_root='baseline',
                artifacts_root='artifacts',
                ai_model_info={},
                provider_version_info={},
                selected_sample_ids=['sample_0006', 'sample_0091'],
                requested_sample_ids=['sample_0006'],
                auto_added_sample_ids=['sample_0091'],
                changed_paths=['src/rename/ai_processor.py'],
                inferred_risk_tags=['tv_strict_mapping', 'episode_dedupe'],
                changed_path_inference=[
                    {
                        'path': 'src/rename/ai_processor.py',
                        'matched_tags': ['tv_strict_mapping', 'episode_dedupe'],
                        'matched_rules': ['src/rename/ai_processor.py'],
                    }
                ],
                scope_expansion=[
                    {
                        'added_sample_id': 'sample_0091',
                        'reason': 'changed_paths',
                        'matched_tags': ['tv_strict_mapping'],
                        'changed_paths': ['src/rename/ai_processor.py'],
                    }
                ],
            ),
            summary=report,
            gate_result={
                'gate_failed': False,
                'product_failure_count': 0,
                'infra_failure_count': 0,
                'flaky_count': 0,
            },
            flaky_samples=[],
            infra_failures=[],
            observation_failures=[],
            quarantine_candidates=[],
        )
    )

    assert '#### Changed Paths' in markdown
    assert '`src/rename/ai_processor.py`' in markdown
    assert '#### Inferred Risk Tags' in markdown
    assert '`tv_strict_mapping`' in markdown
    assert '#### Changed-path Inference' in markdown
    assert '`src/rename/ai_processor.py` -> tags=[' in markdown
    assert '`sample_0091` auto-added via `changed_paths`' in markdown


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
    assert '--changed-path CHANGED_PATH' in help_text
    assert '--no-expand-protected-samples' in help_text
