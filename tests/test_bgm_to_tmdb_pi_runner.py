from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.config.config_manager import cm
from src.rename.bgm_to_tmdb import (
    BgmToTmdbBridgeToolState,
    BgmToTmdbRecipeParams,
    build_tmdb_legal_graph,
    run_bgm_to_tmdb_bridge_agent,
    tv_legal_node_id,
)
from src.rename.case_agent.recipe import (
    CompiledOrganizeAssignment,
    CompiledOrganizePlan,
    CompiledTarget,
)


def test_pi_runner_fake_runtime_submit_accepts_bridge(tmp_path) -> None:
    graph = _graph()

    def fake_runtime(state: BgmToTmdbBridgeToolState) -> dict[str, Any]:
        result = state.handle_tool('submit_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': _recipe_params()})
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'tool_results': [result]}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi')}):
        result = run_bgm_to_tmdb_bridge_agent(
            compiled_plan=_plan(),
            artifact_path='accepted.json',
            sample_id='sample_accepted',
            initial_legal_graph=graph,
            runtime_invoker=fake_runtime,
        )

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.recipe_params is not None
    assert len(result.recipe_params.rules) == 1
    assert result.verified_plan is not None
    assert result.verified_plan.tmdb_target_count == 1
    assert result.tool_call_counts == {'submit_bgm_to_tmdb_bridge_recipe_params': 1}


def test_pi_runner_auto_finalizes_accepted_validation(tmp_path) -> None:
    def fake_runtime(state: BgmToTmdbBridgeToolState) -> dict[str, Any]:
        result = state.handle_tool('validate_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': _recipe_params()})
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'tool_results': [result]}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi')}):
        result = run_bgm_to_tmdb_bridge_agent(
            compiled_plan=_plan(),
            artifact_path='accepted.json',
            sample_id='sample_validate_only',
            initial_legal_graph=_graph(),
            runtime_invoker=fake_runtime,
        )

    assert result.status == 'accepted'
    assert result.raw_runtime_result['post_runtime_auto_finalization']['accepted'] is True
    assert result.tool_call_counts['validate_bgm_to_tmdb_bridge_recipe_params'] == 1
    assert result.tool_call_counts['submit_bgm_to_tmdb_bridge_recipe_params'] == 1


def test_pi_runner_accepts_partial_tmdb_absent_plan(tmp_path) -> None:
    def fake_runtime(state: BgmToTmdbBridgeToolState) -> dict[str, Any]:
        result = state.handle_tool(
            'submit_bgm_to_tmdb_bridge_recipe_params',
            {
                'recipe_params': {
                    'summary': 'map main episode and record missing TMDB special',
                    'rules': [
                        {
                            'name': 'main',
                            'rule_type': 'episode_sequence',
                            'select_bgm': {'source_paths': ['E01.mkv']},
                            'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1'},
                            'confidence': 'High',
                            'reason': 'TMDB title and episode title match the BGM regular episode.',
                        },
                        {
                            'name': 'missing_special',
                            'rule_type': 'tmdb_absent_group',
                            'select_bgm': {'source_paths': ['SP01.mkv']},
                            'confidence': 'High',
                            'reason': 'Hydrated TMDB season 0 and episode-title checks expose no legal node for this BGM special.',
                        },
                    ],
                }
            },
        )
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'tool_results': [result]}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi')}):
        result = run_bgm_to_tmdb_bridge_agent(
            compiled_plan=_plan_with_special(),
            artifact_path='accepted.json',
            sample_id='sample_partial_absent',
            initial_legal_graph=_graph(),
            runtime_invoker=fake_runtime,
        )

    assert result.status == 'accepted'
    assert result.verified_plan is not None
    assert result.verified_plan.tmdb_target_count == 1
    assert result.verified_plan.tmdb_absent_count == 1


def test_pi_runner_auto_finalized_acceptance_does_not_report_runtime_error(tmp_path) -> None:
    def fake_runtime(state: BgmToTmdbBridgeToolState) -> dict[str, Any]:
        result = state.handle_tool('validate_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': _recipe_params()})
        return {'ok': False, 'returncode': 1, 'argv': ['fake'], 'tool_results': [result]}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi')}):
        result = run_bgm_to_tmdb_bridge_agent(
            compiled_plan=_plan(),
            artifact_path='accepted.json',
            sample_id='sample_validate_only_runtime_error',
            initial_legal_graph=_graph(),
            runtime_invoker=fake_runtime,
        )

    assert result.status == 'accepted'
    assert result.raw_runtime_result['post_runtime_auto_finalization']['accepted'] is True
    assert result.errors == []


def test_pi_runner_no_final_result_fails_closed(tmp_path) -> None:
    def fake_runtime(state: BgmToTmdbBridgeToolState) -> dict[str, Any]:
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'tool_results': []}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi')}):
        result = run_bgm_to_tmdb_bridge_agent(
            compiled_plan=_plan(),
            artifact_path='accepted.json',
            sample_id='sample_no_final',
            initial_legal_graph=_graph(),
            runtime_invoker=fake_runtime,
        )

    assert result.status == 'fail_closed'
    assert result.final_action == 'fail_closed'
    assert result.summary == 'budget_exhausted'
    assert result.tool_call_counts == {'fail_closed': 1}


def test_pi_runner_invalid_draft_records_verifier_issues(tmp_path) -> None:
    invalid = BgmToTmdbRecipeParams.model_validate({
        'rules': [
            {
                'name': 'bad',
                'rule_type': 'episode_sequence',
                'select_bgm': {'source_paths': ['E01.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '99'},
                'confidence': 'High',
                'reason': 'bad legal node',
            }
        ]
    }).model_dump(mode='json')

    def fake_runtime(state: BgmToTmdbBridgeToolState) -> dict[str, Any]:
        result = state.handle_tool('submit_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': invalid})
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'tool_results': [result]}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi')}):
        result = run_bgm_to_tmdb_bridge_agent(
            compiled_plan=_plan(),
            artifact_path='accepted.json',
            sample_id='sample_invalid',
            initial_legal_graph=_graph(),
            runtime_invoker=fake_runtime,
        )

    tool_result = result.raw_runtime_result['tool_results'][0]
    issue_codes = {issue['issue_code'] for issue in tool_result['verifier_result']['issues']}
    assert result.status == 'fail_closed'
    assert tool_result['accepted'] is False
    assert 'unknown_tmdb_legal_node' in issue_codes


def test_pi_runner_review_params_do_not_auto_finalize(tmp_path) -> None:
    review_params = BgmToTmdbRecipeParams.model_validate({
        'rules': [
            {
                'name': 'weak',
                'rule_type': 'episode_sequence',
                'select_bgm': {'source_paths': ['E01.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'Low',
            }
        ]
    }).model_dump(mode='json')

    def fake_runtime(state: BgmToTmdbBridgeToolState) -> dict[str, Any]:
        result = state.handle_tool('validate_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': review_params})
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'tool_results': [result]}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi')}):
        result = run_bgm_to_tmdb_bridge_agent(
            compiled_plan=_plan(),
            artifact_path='accepted.json',
            sample_id='sample_review',
            initial_legal_graph=_graph(),
            runtime_invoker=fake_runtime,
    )

    assert result.status == 'fail_closed'
    assert result.tool_call_counts['validate_bgm_to_tmdb_bridge_recipe_params'] == 1
    assert 'submit_bgm_to_tmdb_bridge_recipe_params' not in result.tool_call_counts
    assert result.tool_call_counts['fail_closed'] == 1


def test_pi_runner_model_config_inherits_without_leaking_secret(tmp_path) -> None:
    def fake_runtime(state: BgmToTmdbBridgeToolState) -> dict[str, Any]:
        result = state.handle_tool(
            'fail_closed',
            {'reason': 'not enough TMDB evidence', 'reason_kind': 'insufficient_evidence'},
        )
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'tool_results': [result]}

    secret = 'sk-test-secret'
    with cm.temporary_config({
        'rename_local_bangumi_pi_case_root': str(tmp_path / 'pi'),
        'rename_local_bangumi_pi_model': 'gpt-5.4-mini',
        'rename_local_bangumi_pi_base_url': 'https://api.example.test/v1',
        'rename_local_bangumi_pi_api_key': secret,
        'rename_local_bangumi_pi_provider': 'test-provider',
        'rename_local_bangumi_pi_timeout_seconds': 77,
    }):
        result = run_bgm_to_tmdb_bridge_agent(
            compiled_plan=_plan(),
            artifact_path='accepted.json',
            sample_id='sample_config',
            runtime_invoker=fake_runtime,
        )

    case_input_path = next((tmp_path / 'pi' / 'bgm_to_tmdb' / 'runs').glob('*/case_input.json'))
    case_input_text = case_input_path.read_text(encoding='utf-8')
    case_input = json.loads(case_input_text)

    assert result.pi_model == 'gpt-5.4-mini'
    assert result.pi_provider == 'test-provider'
    assert case_input['pi_model'] == 'gpt-5.4-mini'
    assert case_input['pi_base_url'] == 'https://api.example.test/v1'
    assert case_input['runtime_policy']['wall_clock_timeout_seconds'] == 77
    assert secret not in case_input_text


def test_node_sidecar_uses_pi_core_bridge_tools_and_read_only_native_tools() -> None:
    text = Path('tools/pi_bgm_to_tmdb_bridge_runner.mjs').read_text(encoding='utf-8')
    native_match = re.search(r'const NATIVE_TOOL_NAMES = \[(.*?)\];', text, re.DOTALL)
    assert native_match is not None
    native_names = set(re.findall(r'"([^"]+)"', native_match.group(1)))

    assert 'createAgentSession' in text
    assert native_names.issubset({'read', 'grep', 'find', 'ls'})
    assert 'read' in native_names
    assert native_names.isdisjoint({'write', 'edit', 'bash', 'shell', 'delete'})
    assert 'get_bgm_to_tmdb_bridge_context' in text
    assert 'search_tmdb_candidates' in text
    assert 'get_tmdb_legal_graph' in text
    assert 'validate_bgm_to_tmdb_bridge_recipe_params' in text
    assert 'submit_bgm_to_tmdb_bridge_recipe_params' in text
    assert 'promptSnippet' in text
    assert 'promptGuidelines' in text
    assert 'StringEnum' in text
    assert 'Json.Any(' not in text
    assert 'Type.Any(' not in text
    assert 'Do not hand-write per-source TMDB node mappings' in text
    assert 'do not keep searching recap/summary/CM/bonus title variants' in text
    assert 'streamingBehavior: "followUp"' in text
    assert re.search(r'(?<![A-Za-z0-9_])validate_bgm_to_tmdb_bridge(?![A-Za-z0-9_])', text) is None
    assert re.search(r'(?<![A-Za-z0-9_])submit_bgm_to_tmdb_bridge(?![A-Za-z0-9_])', text) is None
    assert 'tmdb-bridge-contract' in text
    assert 'one anchor search' in text
    assert 'hydrated legal graph as the next evidence layer' in text
    assert 'Treat the accepted BGM plan as the frontier.' in text
    assert 'TMDB side frontier' in text
    assert 'search additional TMDB titles only for graph misses or conflicting candidates' in text
    assert 'Do not convert BGM-mapped OVA/OAD/SP/movie/side-story nodes to supplemental' in text
    assert 'supplemental_group is only for assignments already supplemental in the Local-to-Bangumi plan' in text
    assert 'Use tmdb_absent_group for BGM nodes that TMDB does not expose' in text


def _plan() -> CompiledOrganizePlan:
    return CompiledOrganizePlan(
        assignments=[
            CompiledOrganizeAssignment(
                source_path='E01.mkv',
                disposition='map_to_bangumi',
                target=CompiledTarget(
                    bangumi_subject_id=100,
                    media_kind='tv',
                    episode_id=101,
                    episode_type='regular',
                    sort=1,
                    ep=1,
                    title='Bangumi episode 1',
                ),
            )
        ]
    )


def _plan_with_special() -> CompiledOrganizePlan:
    plan = _plan()
    plan.assignments.append(
        CompiledOrganizeAssignment(
            source_path='SP01.mkv',
            disposition='map_to_bangumi',
            target=CompiledTarget(
                bangumi_subject_id=100,
                media_kind='tv',
                episode_id=201,
                episode_type='special',
                sort=1,
                ep=1,
                title='Bangumi special 1',
            ),
        )
    )
    return plan


def _graph():
    return build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [
                {
                    'legal_node_id': tv_legal_node_id(42, 1, 1),
                    'media_type': 'tv',
                    'tmdb_id': 42,
                    'season_number': 1,
                    'episode_number': 1,
                }
            ],
        }
    ])


def _draft() -> dict[str, Any]:
    return {
        'summary': 'map accepted BGM episode to TMDB episode node',
        'mappings': [
            {
                'source_path': 'E01.mkv',
                'disposition': 'map_to_tmdb',
                'tmdb_legal_node_ids': ['tv:42:S01E01'],
                'confidence': 'High',
                'reason': 'fake legal graph exposes this node',
            }
        ],
    }


def _recipe_params() -> dict[str, Any]:
    return {
        'summary': 'map accepted BGM episode to TMDB episode node',
        'rules': [
            {
                'name': 'main',
                'rule_type': 'episode_sequence',
                'select_bgm': {'source_paths': ['E01.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'High',
                'reason': 'fake legal graph exposes this node and title evidence matches',
            }
        ],
    }
