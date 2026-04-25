from __future__ import annotations

import json
from pathlib import Path

from tools.audit_sample_pool_main_flow_observations import audit_observation


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def _raw_sample(tmp_path: Path) -> Path:
    sample = tmp_path / 'sample.json'
    _write_json(
        sample,
        {
            'root_name': 'Show.S01',
            'files': [
                {'path': 'Show.S01E01.mkv', 'size': 1},
                {'path': 'Show.S01E02.mkv', 'size': 1},
            ],
        },
    )
    return sample


def _raw_special_sample(tmp_path: Path, source_name: str) -> Path:
    sample = tmp_path / 'sample_special.json'
    _write_json(
        sample,
        {
            'root_name': 'Show.Special',
            'files': [{'path': source_name, 'size': 1}],
        },
    )
    return sample


def _observation(tmp_path: Path, *, sample_json: Path, task_artifacts: list[dict]) -> Path:
    observation = tmp_path / 'observation.json'
    mapping = [
        {
            'route_type': 'tv',
            'source_rel': 'Show.S01E01.mkv',
            'target_rel': 'Show (2024)/Season 01/Show - S01E01 - 1080p WEB-DL.mkv',
        },
        {
            'route_type': 'tv',
            'source_rel': 'Show.S01E02.mkv',
            'target_rel': 'Show (2024)/Season 01/Show - S01E02 - 1080p WEB-DL.mkv',
        },
    ]
    _write_json(
        observation,
        {
            'sample_id': 'sample_show',
            'sample_json': str(sample_json),
            'uses_runtime_rename_process': True,
            'uses_shadow_candidate_logic': False,
            'process_status': 'executed',
            'infra_failure': False,
            'message': '',
            'summary': {'final_type': 'tv', 'mapping_count': 2, 'target_count': 2},
            'payload': {
                'final_type': 'tv',
                'routes': [
                    {
                        'route_type': 'tv',
                        'tmdb_id': 1,
                        'season_id': 1,
                        'tmdb_name': 'Show',
                        'target_root_rel': 'Show (2024)',
                        'mapping_count': 2,
                    }
                ],
                'mapping': mapping,
                'library_files': [{'path': item['target_rel'], 'size': 0} for item in mapping],
                'task_artifacts': task_artifacts,
                'record_artifacts': [{'artifact_name': 'task.json', 'mapping': mapping}],
            },
        },
    )
    return observation


def test_audit_marks_clean_executed_as_auto_structural_pass(tmp_path: Path):
    sample_json = _raw_sample(tmp_path)
    observation = _observation(
        tmp_path,
        sample_json=sample_json,
        task_artifacts=[
            {
                'artifact_name': 'task.json',
                'route_type': 'tv',
                'tmdb_id': 1,
                'season_id': 1,
                'target_root_rel': 'Show (2024)',
                'failure_reason': None,
            }
        ],
    )

    result = audit_observation(observation)

    assert result['audited_status'] == 'auto_structural_pass'
    assert result['hard_violations'] == []
    assert result['warnings'] == []


def test_audit_rejects_executed_with_child_failure(tmp_path: Path):
    sample_json = _raw_sample(tmp_path)
    observation = _observation(
        tmp_path,
        sample_json=sample_json,
        task_artifacts=[
            {
                'artifact_name': 'task.json',
                'route_type': 'tv',
                'tmdb_id': 1,
                'season_id': 1,
                'target_root_rel': 'Show (2024)',
                'failure_reason': 'ai_timeout',
            }
        ],
    )

    result = audit_observation(observation)

    assert result['audited_status'] == 'unsafe_executed'
    assert 'executed:failure_artifact_present' in result['hard_violations']


def test_audit_marks_failed_with_outputs_as_side_effect_failure(tmp_path: Path):
    sample_json = _raw_sample(tmp_path)
    observation = _observation(
        tmp_path,
        sample_json=sample_json,
        task_artifacts=[
            {
                'artifact_name': 'task.json',
                'route_type': 'tv',
                'tmdb_id': 1,
                'season_id': 1,
                'target_root_rel': 'Show (2024)',
                'failure_reason': None,
            }
        ],
    )
    payload = json.loads(observation.read_text(encoding='utf-8'))
    payload['process_status'] = 'product_failed'
    payload['message'] = '[映射] 多个源文件映射到同一目标'
    _write_json(observation, payload)

    result = audit_observation(observation)

    assert result['audited_status'] == 'partial_side_effect_failure'
    assert 'failure:side_effects_present' in result['hard_violations']


def test_audit_allows_season_zero_when_source_has_special_cue(tmp_path: Path):
    sample_json = _raw_special_sample(tmp_path, 'Show OVA 01.mkv')
    observation = _observation(
        tmp_path,
        sample_json=sample_json,
        task_artifacts=[
            {
                'artifact_name': 'task.json',
                'route_type': 'tv',
                'tmdb_id': 1,
                'season_id': 0,
                'target_root_rel': 'Show (2024)',
                'failure_reason': None,
            }
        ],
    )
    payload = json.loads(observation.read_text(encoding='utf-8'))
    mapping = [
        {
            'route_type': 'tv',
            'source_rel': 'Show OVA 01.mkv',
            'target_rel': 'Show (2024)/Season 00/Show - S00E01 - 1080p WEB-DL.mkv',
        }
    ]
    payload['summary'] = {'final_type': 'tv', 'mapping_count': 1, 'target_count': 1}
    payload['payload']['routes'][0]['season_id'] = 0
    payload['payload']['routes'][0]['mapping_count'] = 1
    payload['payload']['task_artifacts'][0]['season_id'] = 0
    payload['payload']['mapping'] = mapping
    payload['payload']['library_files'] = [{'path': mapping[0]['target_rel'], 'size': 0}]
    payload['payload']['record_artifacts'] = [{'artifact_name': 'task.json', 'mapping': mapping}]
    _write_json(observation, payload)

    result = audit_observation(observation)

    assert result['audited_status'] == 'auto_structural_pass'


def test_audit_reviews_season_zero_without_special_cue(tmp_path: Path):
    sample_json = _raw_special_sample(tmp_path, 'Show 01.mkv')
    observation = _observation(
        tmp_path,
        sample_json=sample_json,
        task_artifacts=[
            {
                'artifact_name': 'task.json',
                'route_type': 'tv',
                'tmdb_id': 1,
                'season_id': 0,
                'target_root_rel': 'Show (2024)',
                'failure_reason': None,
            }
        ],
    )
    payload = json.loads(observation.read_text(encoding='utf-8'))
    mapping = [
        {
            'route_type': 'tv',
            'source_rel': 'Show 01.mkv',
            'target_rel': 'Show (2024)/Season 00/Show - S00E01 - 1080p WEB-DL.mkv',
        }
    ]
    payload['summary'] = {'final_type': 'tv', 'mapping_count': 1, 'target_count': 1}
    payload['payload']['routes'][0]['season_id'] = 0
    payload['payload']['routes'][0]['mapping_count'] = 1
    payload['payload']['task_artifacts'][0]['season_id'] = 0
    payload['payload']['mapping'] = mapping
    payload['payload']['library_files'] = [{'path': mapping[0]['target_rel'], 'size': 0}]
    payload['payload']['record_artifacts'] = [{'artifact_name': 'task.json', 'mapping': mapping}]
    _write_json(observation, payload)

    result = audit_observation(observation)

    assert result['audited_status'] == 'manual_review'
    assert 'route:season_zero_manual_review' in result['warnings']
