from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from src.config.config_manager import cm
from src.rename.case_agent.local_bangumi_entry import _build_workspace
from src.rename.case_agent.pi_runner import _runtime_command, run_pi_case_agent


REPO_ROOT = Path(__file__).resolve().parents[1]


class _File:
    def __init__(self, file_id: str, name: str, relative_path: str):
        self.file_id = file_id
        self.name = name
        self.relative_path = relative_path
        self.is_main_video_candidate = True
        self.is_video = True
        self.suffix = '.mkv'


def _workspace():
    local = SimpleNamespace(source_path='tests/sample', files=[_File('f1', 'ep1.mkv', 'ep1.mkv'), _File('f2', 'ep2.mkv', 'ep2.mkv')])
    bangumi_contexts = [{
        'context': {
            'episode_structure': {
                'subject_id': 100,
                'title': 'Test',
                'episodes': [
                    {'episode_id': 1001, 'title': 'Episode 1', 'sort': 1, 'ep': 1, 'kind': 'regular'},
                    {'episode_id': 1002, 'title': 'Episode 2', 'sort': 2, 'ep': 2, 'kind': 'regular'},
                ],
            },
        },
    }]
    return _build_workspace(local_evidence=local, bangumi_contexts=bangumi_contexts)


def _recipe(target_two: int = 1002):
    return {
        'version': 1,
        'summary': 'runner test recipe',
        'rules': [
            {
                'name': 'ep1',
                'select': {'exact_paths': ['ep1.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'ep2',
                'select': {'exact_paths': ['ep2.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': target_two},
                'disposition': 'map_to_bangumi',
            },
        ],
    }


def test_pi_runner_fake_runtime_accepts_organize_recipe(tmp_path):
    def fake_runtime(state):
        return {
            'ok': True,
            'returncode': 0,
            'argv': ['fake'],
            'tool_result': state.handle_tool('submit_organize_recipe', {'organize_recipe': _recipe(), 'summary': 'done'}),
        }

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path), 'rename_local_bangumi_pi_command': 'fake-pi'}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.final_verifier_result.passed is True
    assert result.organize_recipe is not None
    assert result.compiled_plan is not None
    assert result.mapping_draft is None
    assert result.tool_call_counts == {'submit_organize_recipe': 1}
    assert (result.run_dir / 'case_input.json').exists()
    assert (result.run_dir / 'tool_trace.jsonl').exists()
    case_input = json.loads((result.run_dir / 'case_input.json').read_text(encoding='utf-8'))
    assert 'max_turns' not in case_input
    assert case_input['runtime_policy']['turn_cap_enabled'] is False
    assert case_input['runtime_policy']['turn_count_is_audit_only'] is True
    assert case_input['pi_command'] == 'fake-pi'
    assert case_input['scratch_paths']['organize_recipe'].endswith('artifacts\\organize_recipe.json') or case_input['scratch_paths']['organize_recipe'].endswith('artifacts/organize_recipe.json')


def test_pi_runner_default_core_runtime_does_not_report_cli_command(tmp_path):
    def fake_runtime(state):
        case_input = json.loads((state.run_dir / 'case_input.json').read_text(encoding='utf-8'))
        assert case_input['pi_command'] == ''
        return {
            'ok': True,
            'returncode': 0,
            'argv': ['node', 'tools/pi_case_agent_runner.mjs'],
            'tool_result': state.handle_tool('submit_organize_recipe', {'organize_recipe': _recipe(), 'summary': 'done'}),
        }

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path), 'rename_local_bangumi_pi_command': ''}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.pi_command == ''
    assert result.runtime_command == ['node', 'tools/pi_case_agent_runner.mjs']


def test_pi_runner_fake_runtime_goal_complete_without_final_is_invalid(tmp_path):
    def fake_runtime(_state):
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'goal_complete': True}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path)}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert result.raw_runtime_result['post_runtime_auto_fail_closed']['status'] == 'fail_closed'
    assert 'error_kind=pi_no_final_result' not in result.errors


def test_pi_runner_auto_finalizes_accepted_validation_without_final_submit(tmp_path):
    def fake_runtime(state):
        return {
            'ok': True,
            'returncode': 0,
            'argv': ['fake'],
            'tool_result': state.handle_tool('validate_organize_recipe', {'organize_recipe': _recipe()}),
        }

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path)}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.tool_sequence == ['validate_organize_recipe', 'submit_organize_recipe']
    assert result.raw_runtime_result['post_runtime_auto_finalization']['accepted'] is True
    assert result.raw_runtime_result['post_runtime_auto_finalization']['auto_finalized_from_validated_recipe'] is True
    assert result.final_verifier_result.passed is True
    assert result.organize_recipe is not None


def test_pi_runner_timeout_without_final_becomes_fail_closed(tmp_path):
    def fake_runtime(_state):
        return {'ok': False, 'returncode': None, 'argv': ['fake'], 'error': 'timeout', 'timeout_seconds': 3}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path), 'rename_local_bangumi_pi_timeout_seconds': 3}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert result.summary == 'Pi runtime exceeded wall-clock timeout of 3 seconds without an accepted recipe.'
    assert result.raw_runtime_result['post_runtime_timeout_fail_closed']['status'] == 'fail_closed'
    assert 'error_kind=pi_runtime_failed' in result.errors


def test_pi_runner_fake_runtime_fail_closed(tmp_path):
    def fake_runtime(state):
        return {
            'ok': True,
            'returncode': 0,
            'argv': ['fake'],
            'tool_result': state.handle_tool('fail_closed', {'reason': 'not enough evidence'}),
        }

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path)}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'fail_closed'
    assert result.final_output.action == 'fail_closed'


def test_pi_runner_fake_runtime_retries_after_verifier_rejection(tmp_path):
    def fake_runtime(state):
        first = state.handle_tool('submit_organize_recipe', {'organize_recipe': _recipe(1001)})
        second = state.handle_tool('submit_organize_recipe', {'organize_recipe': _recipe(1002)})
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'first': first, 'second': second}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path)}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.submit_rejection_count == 1
    assert result.tool_sequence == ['submit_organize_recipe', 'submit_organize_recipe']


def test_pi_runner_builds_model_config_from_python_ai_config_without_secret(tmp_path):
    def fake_runtime(state):
        models_path = state.run_dir / 'pi_agent_config' / 'models.json'
        case_input_path = state.run_dir / 'case_input.json'
        models_text = models_path.read_text(encoding='utf-8')
        case_input_text = case_input_path.read_text(encoding='utf-8')
        assert 'sk-test-secret' not in models_text
        assert 'sk-test-secret' not in case_input_text

        models_payload = json.loads(models_text)
        provider = models_payload['providers']['bangumi-config-openai']
        assert provider['baseUrl'] == 'https://example.test/v1'
        assert provider['api'] == 'openai-responses'
        assert provider['apiKey'] == 'BAR_PI_CASE_AGENT_API_KEY'
        assert provider['authHeader'] is True
        assert provider['models'][0]['id'] == 'gpt-test'

        case_input = json.loads(case_input_text)
        assert case_input['pi_provider'] == 'bangumi-config-openai'
        assert case_input['pi_model'] == 'gpt-test'
        assert case_input['pi_base_url'] == 'https://example.test/v1'
        assert case_input['pi_api'] == 'openai-responses'
        return {
            'ok': True,
            'returncode': 0,
            'argv': ['fake'],
            'tool_result': state.handle_tool('submit_organize_recipe', {'organize_recipe': _recipe(), 'summary': 'done'}),
        }

    with cm.temporary_config({
        'rename_local_bangumi_pi_case_root': str(tmp_path),
        'ai_model': 'gpt-test',
        'ai_base_url': 'https://example.test/v1/',
        'ai_api_key': 'sk-test-secret',
        'openai_api_interface': 'responses_api',
    }):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.pi_provider == 'bangumi-config-openai'
    assert result.pi_model == 'gpt-test'
    assert result.pi_base_url == 'https://example.test/v1'


def test_pi_runner_maps_chat_completions_config_for_pi(tmp_path):
    def fake_runtime(state):
        models_payload = json.loads((state.run_dir / 'pi_agent_config' / 'models.json').read_text(encoding='utf-8'))
        assert models_payload['providers']['bangumi-config-openai']['api'] == 'openai-completions'
        case_input = json.loads((state.run_dir / 'case_input.json').read_text(encoding='utf-8'))
        assert case_input['pi_api'] == 'openai-completions'
        return {
            'ok': True,
            'returncode': 0,
            'argv': ['fake'],
            'tool_result': state.handle_tool('submit_organize_recipe', {'organize_recipe': _recipe(), 'summary': 'done'}),
        }

    with cm.temporary_config({
        'rename_local_bangumi_pi_case_root': str(tmp_path),
        'ai_model': 'gpt-test',
        'ai_base_url': 'https://example.test/v1',
        'ai_api_key': 'sk-test-secret',
        'openai_api_interface': 'chat_completions',
    }):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True


def test_pi_runtime_command_uses_timeout_only_without_turn_cap(tmp_path):
    argv = _runtime_command(
        '',
        case_input_path=tmp_path / 'case_input.json',
        output_path=tmp_path / 'result.json',
        server_url='http://127.0.0.1:1234',
        token='token',
    )

    assert '--max-turns' not in argv
    assert '--input' in argv
    assert '--output' in argv
    assert '--repo-root' in argv


def test_node_runner_registers_goal_retry_recipe_tools_without_mapping_draft():
    text = (REPO_ROOT / 'tools' / 'pi_case_agent_runner.mjs').read_text(encoding='utf-8')

    assert '@narumitw", "pi-goal"' in text
    assert '@narumitw", "pi-retry"' in text
    assert '"goal_complete"' in text
    assert '"get_case_overview"' in text
    assert '"list_local_groups"' in text
    assert '"get_local_group_detail"' in text
    assert '"get_local_selector_scaffold"' in text
    assert '"get_recipe_state"' in text
    assert '"get_local_recipe_params_scaffold"' in text
    assert '"find_bangumi_targets_for_local_file"' in text
    assert '"expand_related_graph"' in text
    assert '"validate_organize_recipe_params"' in text
    assert '"validate_organize_recipe_params_patch"' in text
    assert '"submit_organize_recipe_params"' in text
    assert '"submit_organize_recipe_params_patch"' in text
    assert '"validate_organize_recipe"' in text
    assert '"submit_organize_recipe"' in text
    assert '"submit_recommended_recipe"' not in text
    assert '"submit_mapping_draft"' not in text
    assert 'NATIVE_TOOL_NAMES = ["read", "grep", "find", "ls", "bash", "edit", "write"]' in text
    assert 'PI_RETRY_STALL_TIMEOUT_MS' in text
    assert 'ensureHelperCheckArtifact' in text
    assert 'case_quick_start' not in text
    assert 'early_bangumi_evidence_bundle' not in text
    assert 'Human workflow: read local groups, anchor the main line, close the side frontier through related graph evidence, validate compact params, repair mechanical issues, then submit.' in text
    assert 'Local group facts are not target decisions.' in text
    assert 'For one standalone main-title group, direct Bangumi search is fine.' in text
    assert 'search one reliable anchor first, then use expand_related_graph as the series map.' in text
    assert 'including parent-titled SP folders and long standalone OVA/OAD/SP files' in text
    assert 'maps a frontier group by season qualifier, count, duration, title, or episode rows' in text
    assert 'Validation is the trial that exposes selector, range, row-type, coverage, and duplicate repairs.' in text
    assert 'For numbered multi-file mapped sequences, use group_ref/source_pattern/filename_regex with {ep}' in text
    assert 'Mechanical accepted is the floor, not the quality target.' in text
    assert 'Do not downgrade a plausible OVA/OAD/SP/movie/side-story mapping to supplemental just to clear a verifier issue' in text
    assert 'Supplemental is for closure-stalled or contradicted targets' in text
    assert 'After invalid or review feedback, stop broad exploration.' in text
    assert 'Patch only verifier_result.issues, repair_hints, review_warnings, or repair_mode' in text
    assert 'Never call fail_closed with budget_exhausted' in text
    assert 'never inspect old artifacts/tests to copy an answer' in text
    assert 'Time-boxed Working Board checkpoint: finish through one of three paths.' in text
    assert 'Path 1: if validation is accepted with no review warnings, submit the same params/recipe now.' in text
    assert 'Path 2: if verifier issues or review warnings exist, patch only the named rule/path/target and validate again.' in text
    assert 'Path 3: if a supportable recipe cannot be built after targeted evidence, call fail_closed with the concrete group/reason.' in text
    assert 'Hard finish Working Board checkpoint' in text
    assert 'Final Working Board repair loop' in text
    assert 'maxRepairAttempts = 3' in text
    assert 'Working Board repair checkpoint' in text
    assert 'Working Board review checkpoint' in text
    assert 'Working Board submit path' in text
    assert 'This is telemetry for your Working Board, not a target recommendation.' in text
    assert 'Validation debt checkpoint: no trial validation has run after substantial evidence gathering.' in text
    assert 'Do not call more search, episode, local-detail, or selector tools in response to this checkpoint.' in text
    assert 'Duplicate local locators, split files, variant suffixes, and uncertain exclude_regex choices should become verifier feedback' in text
    assert 'If no validation has run yet and one group remains uncertain, include that group as a supplemental test rule and validate.' in text
    assert 'If mapped target evidence exists but duplicate/split selector handling is uncertain, validate the best mapped rule now' in text
    assert 'If validation rejects a mapped anime/video frontier rule, repair that mapped rule shape before converting it to supplemental.' in text
    assert 'Mechanical selector repair: a numbered multi-file mapped sequence needs group_ref/source_pattern/filename_regex with {ep}' in text
    assert 'the next custom tool must be validate_organize_recipe_params with best-effort mapped/supplemental rules' in text
    assert 'Budget pressure is not a fail_closed reason.' in text
    assert 'Do not lower a plausibly mapped OVA/OAD/SP/movie/side-story group to supplemental just to pass validation' in text
    assert 'Do not call fail_closed with reason budget_exhausted.' in text
    assert 'For a rejected mapped frontier rule, prefer target/selector repair over supplemental downgrade' in text
    assert 'Use the Working Board method from the guidance' in text
    assert 'validate when each visible group has a testable mapped or supplemental rule' in text
    assert 'find_bangumi_targets_for_local_file is a fact lookup only' in text
    assert 'function effectiveRuntimeBudgetSeconds()' in text
    assert 'Math.max(finishBeforeSeconds, timeoutSeconds - 5)' in text
    assert 'Math.min(60_000, Math.floor(totalBudgetMs * 0.25))' in text
    assert 'Math.min(90_000, remainingMs)' in text
    assert 'async function readJsonFile' in text
    assert 'auto-submit after accepted params validation' in text
    assert 'auto-submit after accepted recipe validation' in text
    assert 'verifier?.passed !== true || reviewWarnings.length || issues.length' in text
    assert 'submit_organize_recipe_params_patch' in text
    assert 'validate_organize_recipe_params_patch' in text
    assert 'goal_complete immediately after accepted=true' in text
