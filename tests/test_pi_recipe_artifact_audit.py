from __future__ import annotations

import json
from pathlib import Path

from tools.audit_pi_recipe_artifacts import audit_path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _run_dir(
    tmp_path: Path,
    *,
    name: str = 'run',
    source_path: str = 'merged.mkv',
    duration_seconds: float = 3600.0,
    chapter_count: int = 0,
    disposition: str = 'map_to_bangumi',
    target_span_episode_ids: list[int] | None = None,
    media_kind: str = 'ova',
    episode_type: str = 'regular',
    excluded_count: int = 0,
) -> Path:
    run_dir = tmp_path / name
    target_span_episode_ids = list(target_span_episode_ids or [])
    assignments = [
        {
            'source_path': source_path,
            'disposition': disposition,
            'rule_name': 'main',
            'target': {
                'bangumi_subject_id': 100,
                'media_kind': media_kind,
                'episode_id': 1001 if disposition == 'map_to_bangumi' and not target_span_episode_ids else 0,
                'episode_type': episode_type,
                'sort': 1,
                'ep': 1,
                'title': 'Episode 1',
            },
            'target_span': {
                'bangumi_subject_id': 100 if target_span_episode_ids else 0,
                'media_kind': media_kind if target_span_episode_ids else '',
                'episode_ids': target_span_episode_ids,
                'sort_start': 1 if target_span_episode_ids else None,
                'sort_end': len(target_span_episode_ids) if target_span_episode_ids else None,
                'episode_type': episode_type if target_span_episode_ids else '',
            },
            'reason': 'test',
        }
    ]
    for index in range(excluded_count):
        assignments.append({
            'source_path': f'extra-{index}.mkv',
            'disposition': 'non_bangumi_or_supplemental',
            'rule_name': 'extras',
            'target': {},
            'target_span': {},
            'reason': 'package extra',
        })
    local_files = [
        {
            'source_path': source_path,
            'container_facts': {'probe_status': 'available', 'duration_seconds': duration_seconds, 'chapter_count': chapter_count},
            'fact_summary': {'duration_seconds': duration_seconds, 'chapter_count': chapter_count},
        },
        *[
            {
                'source_path': f'extra-{index}.mkv',
                'container_facts': {'probe_status': 'available', 'duration_seconds': 120.0, 'chapter_count': 0},
                'fact_summary': {'duration_seconds': 120.0, 'chapter_count': 0},
            }
            for index in range(excluded_count)
        ],
    ]
    _write_json(run_dir / 'case_input.json', {'context': {'local_files': local_files}})
    _write_json(
        run_dir / 'final_result.json',
        {
            'status': 'accepted',
            'summary': 'accepted',
            'accounting': {
                'mapped_file_count': 1 if disposition == 'map_to_bangumi' else 0,
                'mapped_path_count': 1 if disposition == 'map_to_bangumi' else 0,
                'mapped_target_episode_count': len(target_span_episode_ids) or (1 if disposition == 'map_to_bangumi' else 0),
                'single_file_multi_episode_count': 1 if len(target_span_episode_ids) >= 2 else 0,
                'excluded_path_count': excluded_count + (1 if disposition == 'non_bangumi_or_supplemental' else 0),
            },
            'compiled_plan': {'assignments': assignments},
            'organize_recipe': {'rules': [{'name': 'main'}]},
        },
    )
    return run_dir


def test_audit_flags_long_single_episode_mapping(tmp_path):
    run_dir = _run_dir(tmp_path, duration_seconds=3600.0, chapter_count=4)

    report = audit_path(run_dir)

    codes = {issue['code'] for issue in report['runs'][0]['issues']}
    assert 'long_single_episode_mapping' in codes
    assert 'chaptered_file_single_episode_mapping' in codes
    assert report['runs_with_review_count'] == 1


def test_audit_treats_single_file_multi_episode_as_info(tmp_path):
    run_dir = _run_dir(tmp_path, duration_seconds=3600.0, chapter_count=4, target_span_episode_ids=[1001, 1002, 1003])

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    codes = {issue['code'] for issue in issues}
    assert 'single_file_multi_episode_mapping' in codes
    assert 'multi_episode_target_accounting' in codes
    assert 'long_single_episode_mapping' not in codes
    assert report['runs_with_review_count'] == 0


def test_audit_demotes_long_boundary_episode_in_sequence_to_info(tmp_path):
    run_dir = tmp_path / 'sequence'
    assignments = []
    local_files = []
    for episode_number, duration in [(1, 2936.0), (2, 1440.0), (3, 1440.0)]:
        source_path = f'episode-{episode_number:02}.mkv'
        assignments.append({
            'source_path': source_path,
            'disposition': 'map_to_bangumi',
            'rule_name': 'main-sequence',
            'extracted_episode_number': episode_number,
            'target': {
                'bangumi_subject_id': 100,
                'media_kind': 'tv',
                'episode_id': 1000 + episode_number,
                'episode_type': 'regular',
                'sort': episode_number,
                'ep': episode_number,
                'title': f'Episode {episode_number}',
            },
            'target_span': {},
            'reason': 'test sequence',
        })
        local_files.append({
            'source_path': source_path,
            'container_facts': {'probe_status': 'available', 'duration_seconds': duration, 'chapter_count': 0},
            'fact_summary': {'duration_seconds': duration, 'chapter_count': 0},
        })
    _write_json(run_dir / 'case_input.json', {'context': {'local_files': local_files}})
    _write_json(
        run_dir / 'final_result.json',
        {
            'status': 'accepted',
            'summary': 'accepted',
            'accounting': {'mapped_file_count': 3, 'mapped_target_episode_count': 3, 'excluded_path_count': 0},
            'compiled_plan': {'assignments': assignments},
            'organize_recipe': {'rules': [{'name': 'main-sequence'}]},
        },
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_sequence_boundary_mapping' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_single_episode_mapping' for issue in issues)
    assert report['runs_with_review_count'] == 0


def test_audit_demotes_long_boundary_episode_across_exact_rules_to_info(tmp_path):
    run_dir = tmp_path / 'exact-rule-sequence'
    assignments = []
    local_files = []
    for episode_number, duration in [(1, 2936.0), (2, 1440.0), (3, 1440.0), (4, 1440.0)]:
        source_path = f'episode #{episode_number}.mkv'
        assignments.append({
            'source_path': source_path,
            'disposition': 'map_to_bangumi',
            'rule_name': f'exact-{episode_number}',
            'target': {
                'bangumi_subject_id': 100,
                'media_kind': 'tv',
                'episode_id': 1000 + episode_number,
                'episode_type': 'regular',
                'sort': episode_number,
                'ep': episode_number,
                'title': f'Episode {episode_number}',
            },
            'target_span': {},
            'reason': 'exact file rule',
        })
        local_files.append({
            'source_path': source_path,
            'container_facts': {'probe_status': 'available', 'duration_seconds': duration, 'chapter_count': 0},
            'fact_summary': {'duration_seconds': duration, 'chapter_count': 0},
        })
    _write_json(run_dir / 'case_input.json', {'context': {'local_files': local_files}})
    _write_json(
        run_dir / 'final_result.json',
        {
            'status': 'accepted',
            'summary': 'accepted',
            'accounting': {'mapped_file_count': 4, 'mapped_target_episode_count': 4, 'excluded_path_count': 0},
            'compiled_plan': {'assignments': assignments},
            'organize_recipe': {'rules': [{'name': f'exact-{index}'} for index in range(1, 5)]},
        },
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_sequence_boundary_mapping' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_single_episode_mapping' for issue in issues)
    assert report['runs_with_review_count'] == 0


def test_audit_demotes_long_extended_sequence_to_info(tmp_path):
    run_dir = tmp_path / 'extended-sequence'
    assignments = []
    local_files = []
    for episode_number in range(1, 4):
        source_path = f'Example Extended Edition - {episode_number:02}.mkv'
        assignments.append({
            'source_path': source_path,
            'disposition': 'map_to_bangumi',
            'rule_name': 'extended-sequence',
            'extracted_episode_number': episode_number,
            'target': {
                'bangumi_subject_id': 100,
                'media_kind': 'tv',
                'episode_id': 1000 + episode_number,
                'episode_type': 'regular',
                'sort': episode_number,
                'ep': episode_number,
                'title': f'Episode {episode_number}',
            },
            'target_span': {},
            'reason': 'complete extended edition sequence',
        })
        local_files.append({
            'source_path': source_path,
            'container_facts': {'probe_status': 'available', 'duration_seconds': 2752.0, 'chapter_count': 0},
            'fact_summary': {'duration_seconds': 2752.0, 'chapter_count': 0},
        })
    _write_json(run_dir / 'case_input.json', {'context': {'local_files': local_files}})
    _write_json(
        run_dir / 'final_result.json',
        {
            'status': 'accepted',
            'summary': 'accepted',
            'accounting': {'mapped_file_count': 3, 'mapped_target_episode_count': 3, 'excluded_path_count': 0},
            'compiled_plan': {'assignments': assignments},
            'organize_recipe': {'rules': [{'name': 'extended-sequence'}]},
        },
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_format_sequence_mapping' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_single_episode_mapping' for issue in issues)
    assert report['runs_with_review_count'] == 0


def test_audit_demotes_single_long_special_target_to_info(tmp_path):
    run_dir = _run_dir(
        tmp_path,
        source_path='Example Special Episode.mkv',
        duration_seconds=2841.0,
        chapter_count=0,
        media_kind='special',
        episode_type='special',
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_single_special_mapping' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_single_episode_mapping' for issue in issues)
    assert report['runs_with_review_count'] == 0


def test_audit_flags_long_excluded_file(tmp_path):
    run_dir = _run_dir(tmp_path, disposition='non_bangumi_or_supplemental', duration_seconds=1800.0)

    report = audit_path(run_dir)

    codes = {issue['code'] for issue in report['runs'][0]['issues']}
    assert 'long_excluded_file' in codes


def test_audit_demotes_long_excluded_file_after_targeted_lookup_to_info(tmp_path):
    run_dir = _run_dir(tmp_path, source_path='Standalone Bonus.mkv', disposition='non_bangumi_or_supplemental', duration_seconds=1800.0)
    (run_dir / 'tool_trace.jsonl').write_text(
        json.dumps({
            'tool': 'find_bangumi_targets_for_local_file',
            'arguments': {'source_path': 'Standalone Bonus.mkv'},
            'ok': True,
        }, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_excluded_after_targeted_lookup' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_excluded_file' for issue in issues)


def test_audit_demotes_obvious_long_extra_exclusion_to_info(tmp_path):
    run_dir = _run_dir(
        tmp_path,
        source_path='SPs/Show Stage Greeting.mkv',
        disposition='non_bangumi_or_supplemental',
        duration_seconds=1800.0,
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_obvious_extra_excluded' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_excluded_file' for issue in issues)


def test_audit_demotes_interview_video_token_exclusion_to_info(tmp_path):
    run_dir = _run_dir(
        tmp_path,
        source_path='SPs/Show [IV02].mkv',
        disposition='non_bangumi_or_supplemental',
        duration_seconds=1800.0,
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_obvious_extra_excluded' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_excluded_file' for issue in issues)


def test_audit_demotes_travel_feature_exclusion_to_info(tmp_path):
    run_dir = _run_dir(
        tmp_path,
        source_path='Show おのせんせい／はらすずこの五島ばらか旅#01.mkv',
        disposition='non_bangumi_or_supplemental',
        duration_seconds=1800.0,
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_obvious_extra_excluded' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_excluded_file' for issue in issues)


def test_audit_demotes_pre_release_special_program_exclusion_to_info(tmp_path):
    run_dir = _run_dir(
        tmp_path,
        source_path='Movie 第七章公開直前特別番組 - 全七章の軌跡 -.mkv',
        disposition='non_bangumi_or_supplemental',
        duration_seconds=2952.116,
    )

    report = audit_path(run_dir)

    issues = report['runs'][0]['issues']
    assert any(issue['code'] == 'long_obvious_extra_excluded' and issue['severity'] == 'info' for issue in issues)
    assert not any(issue['code'] == 'long_excluded_file' for issue in issues)


def test_audit_discovers_batch_summary_rows(tmp_path):
    run_dir = _run_dir(tmp_path, name='run-a', duration_seconds=3600.0)
    batch = tmp_path / 'batch'
    _write_json(batch / 'summary.json', {'rows': [{'sample': 'sample-a.json', 'pi_run_dir': str(run_dir), 'pi_turn_count': 15}]})

    report = audit_path(batch)

    assert report['run_count'] == 1
    assert report['runs'][0]['sample'] == 'sample-a.json'
    assert any(issue['code'] == 'expensive_accepted_case' for issue in report['runs'][0]['issues'])
