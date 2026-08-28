from __future__ import annotations

import json
from types import SimpleNamespace

from src.config.config_manager import cm
from src.rename.bgm_to_tmdb import (
    BgmToTmdbMapping,
    BgmToTmdbMappingDraft,
    TmdbLegalNode,
    build_tmdb_legal_graph,
    tv_legal_node_id,
    verify_and_compile_bgm_to_tmdb_plan,
)
from src.rename.bgm_to_tmdb.compiler import compile_bgm_to_tmdb_input
from src.rename.case_agent.recipe import CompiledOrganizeAssignment, CompiledOrganizePlan, CompiledTarget
from src.rename.process import Rename, _run_local_bangumi_case_agent_primary, _temporary_debug_task_record_paths


class _File:
    def __init__(self, file_id: str, name: str, relative_path: str, is_main_video_candidate: bool = True):
        self.file_id = file_id
        self.name = name
        self.relative_path = relative_path
        self.is_main_video_candidate = is_main_video_candidate


def test_local_bangumi_primary_writes_only_case_agent_stage(monkeypatch):
    stages: list[str] = []

    monkeypatch.setattr('src.rename.process.write_decision_snapshot', lambda stage, *args, **kwargs: stages.append(stage))
    monkeypatch.setattr('src.rename.process.run_local_bangumi_case_agent_mapping', lambda **kwargs: {'ok': True, 'status': 'accepted', 'summary': 'ok'})

    result = _run_local_bangumi_case_agent_primary(
        local_evidence=type('LocalEvidence', (), {'source_path': 'tests/sample', 'files': [_File('f1', 'ep1.mkv', 'ep1.mkv')]})(),
        bangumi_contexts=[],
        source_path='tests/sample',
    )

    assert result['status'] == 'accepted'
    assert stages == ['rename_local_bangumi_case_agent_result']


def test_case_agent_primary_receives_parent_directory_without_fixed_split(tmp_path, monkeypatch):
    parent = tmp_path / 'Series Pack'
    season_1 = parent / 'Season 1'
    season_2 = parent / 'Season 2'
    season_1.mkdir(parents=True)
    season_2.mkdir(parents=True)
    (season_1 / '01.mkv').write_bytes(b'')
    (season_2 / '01.mkv').write_bytes(b'')

    captured: dict[str, object] = {}


    def fake_case_agent_primary(**kwargs):
        local_evidence = kwargs['local_evidence']
        captured['paths'] = [file.relative_path for file in local_evidence.files]
        captured['bangumi_contexts'] = kwargs['bangumi_contexts']
        return {'ok': True, 'status': 'fail_closed', 'summary': 'agent handled parent'}

    def fail_enqueue(**kwargs):
        raise AssertionError('Case Agent primary should decide splitting, not Rename.process')

    def fake_error_reply(self, task_uuid, message, path, *args, extra_task_data=None, **kwargs):
        return {'task_uuid': task_uuid, 'message': message, 'extra_task_data': extra_task_data or {}}
    monkeypatch.setattr('src.rename.process._run_local_bangumi_case_agent_primary', fake_case_agent_primary)
    monkeypatch.setattr(Rename, 'error_reply', fake_error_reply)

    rename = Rename()
    assert not hasattr(rename, 'search')

    with cm.temporary_config({'rename_local_bangumi_case_agent_primary_enabled': True}):
        result = rename.process(parent, _tuuid='case-agent-task', _enqueue_task=fail_enqueue)

    assert captured['paths'] == ['Season 1/01.mkv', 'Season 2/01.mkv']
    assert captured['bangumi_contexts'] == []
    assert result['extra_task_data']['case_agent_result']['summary'] == 'agent handled parent'


def test_case_agent_primary_does_not_require_tmdb_key(tmp_path, monkeypatch):
    parent = tmp_path / 'Series Pack'
    parent.mkdir()
    (parent / '01.mkv').write_bytes(b'')

    captured: dict[str, object] = {}

    def fake_case_agent_primary(**kwargs):
        captured['called'] = True
        return {'ok': True, 'status': 'accepted', 'summary': 'mapped to bangumi only'}

    def fake_error_reply(self, task_uuid, message, path, *args, extra_task_data=None, **kwargs):
        return {'task_uuid': task_uuid, 'message': message, 'extra_task_data': extra_task_data or {}}
    monkeypatch.setattr('src.rename.process._run_local_bangumi_case_agent_primary', fake_case_agent_primary)
    monkeypatch.setattr(Rename, 'error_reply', fake_error_reply)

    rename = Rename()
    assert not hasattr(rename, 'search')

    with cm.temporary_config({'rename_local_bangumi_case_agent_primary_enabled': True}):
        result = rename.process(parent, _tuuid='case-agent-no-tmdb')

    assert captured['called'] is True
    assert result['extra_task_data']['case_agent_result']['status'] == 'accepted'


def test_rename_roots_default_to_anime_when_entry_has_no_media_hint(tmp_path):
    config = {
        'tv_path': str(tmp_path / 'TV'),
        'movie_path': str(tmp_path / 'Movies'),
        'anime_path': str(tmp_path / 'Anime'),
        'anime_movie_path': str(tmp_path / 'Anime Movies'),
    }

    with cm.temporary_config(config):
        default_roots = Rename()._bgm_to_tmdb_rename_roots(is_anime=None)
        non_anime_roots = Rename()._bgm_to_tmdb_rename_roots(is_anime=False)

    assert default_roots.tv_root == config['anime_path']
    assert default_roots.movie_root == config['anime_movie_path']
    assert non_anime_roots.tv_root == config['tv_path']
    assert non_anime_roots.movie_root == config['movie_path']


def test_product_pipeline_dry_run_compiles_final_plan_without_transfer(tmp_path, monkeypatch):
    parent = tmp_path / 'Series Pack'
    parent.mkdir()
    source = parent / 'E01.mkv'
    source.write_bytes(b'episode')
    task_path = tmp_path / 'task'
    record_path = tmp_path / 'record'
    task_path.mkdir()
    record_path.mkdir()
    run_dir = tmp_path / 'pi' / 'run'
    run_dir.mkdir(parents=True)

    compiled_plan = _compiled_plan('E01.mkv')
    graph, verified_plan = _verified_tmdb_plan(compiled_plan)

    def fake_case_agent_primary(**kwargs):
        return _accepted_case_agent_result(compiled_plan)

    def fake_bridge_agent(**kwargs):
        return SimpleNamespace(
            ok=True,
            status='accepted',
            summary='bridge accepted',
            errors=[],
            run_dir=run_dir,
            tool_call_counts={'submit_bgm_to_tmdb_bridge_recipe_params': 1},
            verified_plan=verified_plan,
            tmdb_legal_graph=graph,
        )

    def fail_transfer(*args, **kwargs):
        raise AssertionError('dry-run product pipeline must not call Trans')
    monkeypatch.setattr('src.rename.process._run_local_bangumi_case_agent_primary', fake_case_agent_primary)
    monkeypatch.setattr('src.rename.process.run_bgm_to_tmdb_bridge_agent', fake_bridge_agent)
    monkeypatch.setattr('src.rename.process.Trans', fail_transfer)

    with _temporary_debug_task_record_paths(task_path, record_path):
        with cm.temporary_config({
            'rename_bgm_to_tmdb_product_pipeline_enabled': True,
            'rename_bgm_to_tmdb_execute_enabled': False,
            'anime_path': str(tmp_path / 'Anime'),
            'anime_movie_path': str(tmp_path / 'Anime Movies'),
        }):
            result = Rename().process(parent, _is_anime=True, _tuuid='dry-run-task')

    task_data = json.loads((task_path / 'dry-run-task.json').read_text(encoding='utf-8'))
    assert isinstance(result, str)
    assert task_data['failure_reason'] == 'bgm_to_tmdb_rename_plan_dry_run'
    assert task_data['pipeline_mode'] == 'local_bangumi_to_tmdb_product_dry_run'
    assert task_data['bgm_to_tmdb_rename_verifier_result']['passed'] is True
    assert task_data['bgm_to_tmdb_rename_plan']['items'][0]['destination']['target_path']
    assert not (record_path / 'dry-run-task.json').exists()


def test_product_pipeline_execute_transfers_and_writes_success_task(tmp_path, monkeypatch):
    parent = tmp_path / 'Series Pack'
    parent.mkdir()
    source = parent / 'E01.mkv'
    source.write_bytes(b'episode')
    (parent / 'E01.chs.ass').write_bytes(b'ass subtitle')
    (parent / 'E01.srt').write_bytes(b'srt subtitle')
    task_path = tmp_path / 'task'
    record_path = tmp_path / 'record'
    task_path.mkdir()
    record_path.mkdir()
    run_dir = tmp_path / 'pi' / 'run'
    run_dir.mkdir(parents=True)

    compiled_plan = _compiled_plan('E01.mkv')
    graph, verified_plan = _verified_tmdb_plan(compiled_plan)

    def fake_case_agent_primary(**kwargs):
        return _accepted_case_agent_result(compiled_plan)

    def fake_bridge_agent(**kwargs):
        return SimpleNamespace(
            ok=True,
            status='accepted',
            summary='bridge accepted',
            errors=[],
            run_dir=run_dir,
            tool_call_counts={'submit_bgm_to_tmdb_bridge_recipe_params': 1},
            verified_plan=verified_plan,
            tmdb_legal_graph=graph,
        )
    monkeypatch.setattr('src.rename.process._run_local_bangumi_case_agent_primary', fake_case_agent_primary)
    monkeypatch.setattr('src.rename.process.run_bgm_to_tmdb_bridge_agent', fake_bridge_agent)

    with _temporary_debug_task_record_paths(task_path, record_path):
        with cm.temporary_config({
            'rename_bgm_to_tmdb_product_pipeline_enabled': True,
            'rename_bgm_to_tmdb_execute_enabled': True,
            'anime_path': str(tmp_path / 'Anime'),
            'anime_movie_path': str(tmp_path / 'Anime Movies'),
            'mode': '复制',
        }):
            result = Rename().process(parent, _tuuid='execute-task')

    assert result is True
    task_data = json.loads((task_path / 'execute-task.json').read_text(encoding='utf-8'))
    record_data = json.loads((record_path / 'execute-task.json').read_text(encoding='utf-8'))
    target_path = tmp_path / 'Anime' / 'Example Show (2024)' / 'Season 1' / 'Example Show - S01E01.mkv'
    assert target_path.read_bytes() == b'episode'
    assert task_data['error'] == ''
    assert task_data['is_anime'] is True
    assert task_data['is_movie'] is False
    assert task_data['pipeline_mode'] == 'local_bangumi_to_tmdb_product'
    assert task_data['tmdb_id'] == 42
    assert task_data['tmdb_name'] == 'Example Show'
    assert task_data['season_id'] == 1
    assert task_data['target_root'] == str(tmp_path / 'Anime' / 'Example Show (2024)')
    assert task_data['transferred_file_count'] == 3
    assert record_data == {str(source.resolve()): str(target_path)}

    target_sub_chs = tmp_path / 'Anime' / 'Example Show (2024)' / 'Season 1' / 'Example Show - S01E01.zh-CN.default.ass'
    target_sub_srt = tmp_path / 'Anime' / 'Example Show (2024)' / 'Season 1' / 'Example Show - S01E01.zh.srt'
    assert target_sub_chs.read_bytes() == b'ass subtitle'
    assert target_sub_srt.read_bytes() == b'srt subtitle'
    assert task_data['subtitle_mapping'] == {
        str((parent / 'E01.chs.ass').resolve()): str(target_sub_chs),
        str((parent / 'E01.srt').resolve()): str(target_sub_srt),
    }
    assert task_data['subtitle_transfer_failed'] is False


def test_product_pipeline_fail_closed_retry_recovers_to_accepted(tmp_path, monkeypatch):
    """段2 桥接首次 fail_closed（假阴性）→ 单次重试转 accepted → 正常落地。

    验证 rename_bgm_to_tmdb_retry_on_fail_closed=True 时，fail_closed 会被重试
    一次，重试 accepted 则继续落地（不任务失败）。
    """
    parent = tmp_path / 'Series Pack'
    parent.mkdir()
    source = parent / 'E01.mkv'
    source.write_bytes(b'episode')
    task_path = tmp_path / 'task'
    record_path = tmp_path / 'record'
    task_path.mkdir()
    record_path.mkdir()
    run_dir = tmp_path / 'pi' / 'run'
    run_dir.mkdir(parents=True)

    compiled_plan = _compiled_plan('E01.mkv')
    graph, verified_plan = _verified_tmdb_plan(compiled_plan)

    def fake_case_agent_primary(**kwargs):
        return _accepted_case_agent_result(compiled_plan)

    call_count = {'n': 0}

    def fake_bridge_agent(**kwargs):
        call_count['n'] += 1
        if call_count['n'] == 1:
            # 首次假阴性 fail_closed
            return SimpleNamespace(
                ok=False,
                status='fail_closed',
                summary='Pi fail_closed (false negative)',
                errors=[],
                run_dir=run_dir,
                tool_call_counts={},
                verified_plan=None,
                tmdb_legal_graph=None,
            )
        # 重试转 accepted
        return SimpleNamespace(
            ok=True,
            status='accepted',
            summary='bridge accepted on retry',
            errors=[],
            run_dir=run_dir,
            tool_call_counts={'submit_bgm_to_tmdb_bridge_recipe_params': 1},
            verified_plan=verified_plan,
            tmdb_legal_graph=graph,
        )
    monkeypatch.setattr('src.rename.process._run_local_bangumi_case_agent_primary', fake_case_agent_primary)
    monkeypatch.setattr('src.rename.process.run_bgm_to_tmdb_bridge_agent', fake_bridge_agent)

    with _temporary_debug_task_record_paths(task_path, record_path):
        with cm.temporary_config({
            'rename_bgm_to_tmdb_product_pipeline_enabled': True,
            'rename_bgm_to_tmdb_execute_enabled': True,
            'rename_bgm_to_tmdb_retry_on_fail_closed': True,
            'anime_path': str(tmp_path / 'Anime'),
            'anime_movie_path': str(tmp_path / 'Anime Movies'),
            'mode': '复制',
        }):
            result = Rename().process(parent, _is_anime=True, _tuuid='retry-task')

    assert result is True
    assert call_count['n'] == 2  # 首次 fail_closed + 重试 accepted
    task_data = json.loads((task_path / 'retry-task.json').read_text(encoding='utf-8'))
    assert task_data['error'] == ''
    assert task_data['bgm_to_tmdb_bridge_retried_after_fail_closed'] is True
    target_path = tmp_path / 'Anime' / 'Example Show (2024)' / 'Season 1' / 'Example Show - S01E01.mkv'
    assert target_path.read_bytes() == b'episode'


def test_product_pipeline_fail_closed_no_retry_when_disabled(tmp_path, monkeypatch):
    """rename_bgm_to_tmdb_retry_on_fail_closed=False 时 fail_closed 不重试，直接任务失败。"""
    parent = tmp_path / 'Series Pack'
    parent.mkdir()
    source = parent / 'E01.mkv'
    source.write_bytes(b'episode')
    task_path = tmp_path / 'task'
    record_path = tmp_path / 'record'
    task_path.mkdir()
    record_path.mkdir()
    run_dir = tmp_path / 'pi' / 'run'
    run_dir.mkdir(parents=True)

    compiled_plan = _compiled_plan('E01.mkv')

    def fake_case_agent_primary(**kwargs):
        return _accepted_case_agent_result(compiled_plan)

    call_count = {'n': 0}

    def fake_bridge_agent(**kwargs):
        call_count['n'] += 1
        return SimpleNamespace(
            ok=False,
            status='fail_closed',
            summary='Pi fail_closed',
            errors=[],
            run_dir=run_dir,
            tool_call_counts={},
            verified_plan=None,
            tmdb_legal_graph=None,
        )
    monkeypatch.setattr('src.rename.process._run_local_bangumi_case_agent_primary', fake_case_agent_primary)
    monkeypatch.setattr('src.rename.process.run_bgm_to_tmdb_bridge_agent', fake_bridge_agent)

    with _temporary_debug_task_record_paths(task_path, record_path):
        with cm.temporary_config({
            'rename_bgm_to_tmdb_product_pipeline_enabled': True,
            'rename_bgm_to_tmdb_execute_enabled': True,
            'rename_bgm_to_tmdb_retry_on_fail_closed': False,
            'anime_path': str(tmp_path / 'Anime'),
            'anime_movie_path': str(tmp_path / 'Anime Movies'),
            'mode': '复制',
        }):
            result = Rename().process(parent, _is_anime=True, _tuuid='no-retry-task')

    assert isinstance(result, str)  # 任务失败
    assert call_count['n'] == 1  # 没重试
    task_data = json.loads((task_path / 'no-retry-task.json').read_text(encoding='utf-8'))
    assert task_data['failure_reason'] == 'bgm_to_tmdb_bridge_failed'


def _compiled_plan(source_path: str) -> CompiledOrganizePlan:
    return CompiledOrganizePlan(
        assignments=[
            CompiledOrganizeAssignment(
                source_path=source_path,
                disposition='map_to_bangumi',
                target=CompiledTarget(
                    bangumi_subject_id=100,
                    media_kind='tv',
                    episode_id=1001,
                    episode_type='regular',
                    sort=1,
                    ep=1,
                    title='Bangumi 1',
                ),
                reason='accepted BGM mapping',
            )
        ]
    )


def _accepted_case_agent_result(compiled_plan: CompiledOrganizePlan) -> dict[str, object]:
    return {
        'ok': True,
        'status': 'accepted',
        'summary': 'accepted',
        'snapshot': {
            'status': 'accepted',
            'case_agent_status': 'accepted',
            'accepted_contract_ok': True,
            'compiled_plan': compiled_plan.model_dump(mode='json'),
        },
        'result': {'status': 'accepted', 'ok': True},
    }


def _verified_tmdb_plan(compiled_plan: CompiledOrganizePlan):
    bridge_input = compile_bgm_to_tmdb_input(compiled_plan, source_path='Series Pack')
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Example Show',
            'year': 2024,
            'legal_nodes': [
                TmdbLegalNode(
                    legal_node_id=tv_legal_node_id(42, 1, 1),
                    media_type='tv',
                    tmdb_id=42,
                    season_number=1,
                    episode_number=1,
                    title='Start',
                )
            ],
        }
    ])
    verified_plan, verifier_result = verify_and_compile_bgm_to_tmdb_plan(
        bridge_input,
        graph,
        BgmToTmdbMappingDraft(
            mappings=[
                BgmToTmdbMapping(
                    source_path='E01.mkv',
                    tmdb_legal_node_ids=['tv:42:S01E01'],
                    confidence='High',
                    reason='title/order match',
                )
            ]
        ),
    )
    assert verifier_result.passed is True
    assert verified_plan is not None
    return graph, verified_plan
