from __future__ import annotations

import json
from pathlib import Path

from src.config.config_manager import cm
from src.rename.case_agent.recipe import (
    CompiledOrganizeAssignment,
    CompiledOrganizePlan,
    CompiledTarget,
)
from tools import run_bgm_to_tmdb_bridge_sample_pool as sample_runner


def test_sample_runner_dry_builds_accepted_artifacts(tmp_path) -> None:
    accepted_root = tmp_path / 'accepted'
    output_dir = tmp_path / 'out'
    _write_artifact(accepted_root / 'sample_alpha.json', 'alpha.mkv')
    _write_artifact(accepted_root / 'sample_beta.json', 'beta.mkv')

    code = sample_runner.main([
        '--accepted-root',
        str(accepted_root),
        '--output-dir',
        str(output_dir),
        '--dry-build',
        '--all',
        '--workers',
        '2',
    ])

    summary = _read_summary(output_dir)
    assert code == 0
    assert summary['dry_build'] is True
    assert summary['sample_count'] == 2
    assert summary['counts'] == {'dry_build': 2}
    assert summary['assignment_count_total'] == 2
    assert (output_dir / 'sample_alpha.json').exists()
    assert (output_dir / 'sample_alpha.progress.json').exists()


def test_sample_runner_defaults_to_three_without_all(tmp_path) -> None:
    accepted_root = tmp_path / 'accepted'
    output_dir = tmp_path / 'out'
    for index in range(4):
        _write_artifact(accepted_root / f'sample_{index:04d}.json', f'{index}.mkv')

    code = sample_runner.main([
        '--accepted-root',
        str(accepted_root),
        '--output-dir',
        str(output_dir),
        '--dry-build',
    ])

    summary = _read_summary(output_dir)
    assert code == 0
    assert summary['sample_count'] == 3
    assert [row['sample_id'] for row in summary['rows']] == ['sample_0000', 'sample_0001', 'sample_0002']


def test_sample_runner_applies_sample_offset_and_limit(tmp_path) -> None:
    accepted_root = tmp_path / 'accepted'
    output_dir = tmp_path / 'out'
    _write_artifact(accepted_root / 'sample_alpha.json', 'alpha.mkv')
    _write_artifact(accepted_root / 'sample_beta_a.json', 'beta-a.mkv')
    _write_artifact(accepted_root / 'sample_beta_b.json', 'beta-b.mkv')

    code = sample_runner.main([
        '--accepted-root',
        str(accepted_root),
        '--output-dir',
        str(output_dir),
        '--dry-build',
        '--sample',
        'beta',
        '--offset',
        '1',
        '--limit',
        '1',
    ])

    summary = _read_summary(output_dir)
    assert code == 0
    assert summary['sample_count'] == 1
    assert summary['rows'][0]['sample_id'] == 'sample_beta_b'


def test_sample_runner_live_path_uses_fake_runtime_and_writes_summary(tmp_path, monkeypatch) -> None:
    accepted_root = tmp_path / 'accepted'
    output_dir = tmp_path / 'out'
    _write_artifact(accepted_root / 'sample_fail_closed.json', 'fail-closed.mkv')
    monkeypatch.setenv(
        'BAR_PI_BGM_TO_TMDB_FAKE_RESULT_JSON',
        json.dumps({
            'tool_calls': [
                {
                    'tool': 'fail_closed',
                    'arguments': {
                        'reason': 'TMDB candidates are insufficient for a safe bridge',
                        'reason_kind': 'insufficient_evidence',
                    },
                }
            ]
        }),
    )

    with cm.temporary_config({
        'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi'),
        'rename_local_bangumi_pi_timeout_seconds': 30,
    }):
        code = sample_runner.main([
            '--accepted-root',
            str(accepted_root),
            '--output-dir',
            str(output_dir),
            '--limit',
            '1',
            '--workers',
            '1',
            '--sample-timeout-seconds',
            '30',
        ])

    summary = _read_summary(output_dir)
    assert code == 0
    assert summary['counts'] == {'fail_closed': 1}
    assert summary['fail_closed_count'] == 1
    assert summary['strict_failure_count'] == 0
    assert summary['tool_call_counts'] == {'fail_closed': 1}
    assert (tmp_path / 'pi' / 'bgm_to_tmdb' / 'runs').exists()


def _write_artifact(path: Path, source_path: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'ok': True,
        'status': 'accepted',
        'snapshot': {
            'status': 'accepted',
            'accepted_contract_ok': True,
            'compiled_plan': CompiledOrganizePlan(
                assignments=[
                    CompiledOrganizeAssignment(
                        source_path=source_path,
                        disposition='map_to_bangumi',
                        target=CompiledTarget(
                            bangumi_subject_id=100,
                            media_kind='tv',
                            episode_id=101,
                            episode_type='regular',
                            sort=1,
                            ep=1,
                            title='Bangumi episode',
                        ),
                    )
                ]
            ).model_dump(mode='json'),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def _read_summary(output_dir: Path) -> dict:
    return json.loads((output_dir / 'summary.json').read_text(encoding='utf-8'))
