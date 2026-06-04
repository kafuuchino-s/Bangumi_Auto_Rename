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


def _recipe_params(target_two: int = 1002):
    rules = [
        {
            'name': 'ep1',
            'exact_paths': ['ep1.mkv'],
            'subject_id': 100,
            'media_kind': 'tv',
            'episode_id': 1001,
            'disposition': 'map_to_bangumi',
        },
        {
            'name': 'ep2',
            'exact_paths': ['ep2.mkv'],
            'subject_id': 100,
            'media_kind': 'tv',
            'episode_id': target_two,
            'disposition': 'map_to_bangumi',
        },
    ]
    return {'version': 1, 'summary': 'runner test params', 'rules': rules}


def test_pi_runner_fake_runtime_accepts_organize_recipe(tmp_path):
    def fake_runtime(state):
        return {
            'ok': True,
            'returncode': 0,
            'argv': ['fake'],
            'tool_result': state.handle_tool('submit_organize_recipe_params', {'recipe_params': _recipe_params(), 'summary': 'done'}),
        }

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path), 'rename_local_bangumi_pi_command': 'fake-pi'}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.final_verifier_result.passed is True
    assert result.organize_recipe is not None
    assert result.compiled_plan is not None
    assert result.mapping_draft is None
    assert result.tool_call_counts == {'submit_organize_recipe_params': 1}
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
            'tool_result': state.handle_tool('submit_organize_recipe_params', {'recipe_params': _recipe_params(), 'summary': 'done'}),
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


def test_pi_runner_auto_finalizes_accepted_params_validation_without_final_submit(tmp_path):
    def fake_runtime(state):
        return {
            'ok': True,
            'returncode': 0,
            'argv': ['fake'],
            'tool_result': state.handle_tool('validate_organize_recipe_params', {'recipe_params': _recipe_params()}),
        }

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path)}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.tool_sequence == ['validate_organize_recipe_params', 'submit_organize_recipe_params']
    assert result.raw_runtime_result['post_runtime_auto_finalization']['accepted'] is True
    assert result.raw_runtime_result['post_runtime_auto_finalization']['auto_finalized_from_validated_recipe_params'] is True
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
        first = state.handle_tool('submit_organize_recipe_params', {'recipe_params': _recipe_params(1001)})
        second = state.handle_tool('submit_organize_recipe_params', {'recipe_params': _recipe_params(1002)})
        return {'ok': True, 'returncode': 0, 'argv': ['fake'], 'first': first, 'second': second}

    with cm.temporary_config({'rename_local_bangumi_pi_case_root': str(tmp_path)}):
        result = run_pi_case_agent(workspace=_workspace(), bangumi_client=object(), source_path='tests/sample', runtime_invoker=fake_runtime)

    assert result.ok is True
    assert result.status == 'accepted'
    assert result.submit_rejection_count == 1
    assert result.tool_sequence == ['submit_organize_recipe_params', 'submit_organize_recipe_params']


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
            'tool_result': state.handle_tool('submit_organize_recipe_params', {'recipe_params': _recipe_params(), 'summary': 'done'}),
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
            'tool_result': state.handle_tool('submit_organize_recipe_params', {'recipe_params': _recipe_params(), 'summary': 'done'}),
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


def test_node_runner_loads_project_extension_tools_without_subagents_or_mapping_draft():
    text = (REPO_ROOT / 'tools' / 'pi_case_agent_runner.mjs').read_text(encoding='utf-8')
    extension_text = (REPO_ROOT / '.pi' / 'extensions' / 'local-bangumi-tools' / 'index.js').read_text(encoding='utf-8')
    extension_package = json.loads((REPO_ROOT / '.pi' / 'extensions' / 'local-bangumi-tools' / 'package.json').read_text(encoding='utf-8'))
    combined = text + '\n' + extension_text

    assert extension_package['type'] == 'module'
    assert '@narumitw", "pi-goal"' in text
    assert '@narumitw", "pi-retry"' in text
    assert '.pi", "extensions", "local-bangumi-tools", "index.js"' in text
    assert 'LOCAL_BANGUMI_TOOL_NAMES' in text
    assert 'customTools: tools' not in text
    assert 'pi.registerTool(tool)' in extension_text
    assert 'export const LOCAL_BANGUMI_TOOL_NAMES = tools.map((tool) => tool.name)' in extension_text
    assert '"goal_complete"' in text
    assert '"subagent"' not in combined
    assert 'pi-subagents' not in combined
    assert '"get_case_overview"' in extension_text
    assert '"list_local_groups"' in extension_text
    assert '"get_local_group_detail"' in extension_text
    assert '"get_local_selector_scaffold"' in extension_text
    assert '"get_recipe_state"' in extension_text
    assert '"get_local_recipe_params_scaffold"' in extension_text
    assert '"append_case_board_note"' in extension_text
    assert '"get_case_board_notes"' in extension_text
    assert '"select_bangumi_anchor_subject"' in extension_text
    assert '"build_bangumi_relation_atlas"' in extension_text
    assert '"prepare_bangumi_frontier_scout_packet"' not in combined
    assert '"record_bangumi_frontier_scout_report"' not in combined
    assert '"prepare_bangumi_relation_atlas_scout_packets"' not in combined
    assert '"record_bangumi_relation_atlas_scout_reports"' not in combined
    assert '"upsert_recipe_group_decision_one"' in extension_text
    assert '"upsert_recipe_group_decision"' in extension_text
    assert '"get_recipe_group_decisions"' in extension_text
    assert '"clear_recipe_group_decisions"' in extension_text
    assert '"upsert_recipe_params_draft"' in extension_text
    assert '"get_recipe_params_draft"' in extension_text
    assert '"clear_recipe_params_draft"' in extension_text
    assert '"validate_recipe_params_draft"' in extension_text
    assert '"find_bangumi_targets_for_local_file"' in extension_text
    assert '"expand_related_graph"' in extension_text
    assert '"validate_organize_recipe_params"' in extension_text
    assert '"validate_organize_recipe_params_patch"' in extension_text
    assert '"submit_organize_recipe_params"' in extension_text
    assert '"submit_organize_recipe_params_patch"' in extension_text
    assert '"validate_organize_recipe"' not in extension_text
    assert '"submit_organize_recipe"' not in extension_text
    assert '"submit_recommended_recipe"' not in combined
    assert '"submit_mapping_draft"' not in combined
    assert 'const NATIVE_TOOL_NAMES = ["read"]' in text
    native_section = text[text.index('const NATIVE_TOOL_NAMES'):text.index('const EXTENSION_TOOL_NAMES')]
    assert '"read"' in native_section
    for native_name in ['"grep"', '"find"', '"ls"', '"bash"', '"edit"', '"write"']:
        assert native_name not in native_section
    assert 'PI_RETRY_STALL_TIMEOUT_MS' in text
    assert 'ensureHelperCheckArtifact' in text
    assert 'case_quick_start' not in text
    assert 'early_bangumi_evidence_bundle' not in text
    assert 'PRIMARY_PROMPT_TEMPLATE_NAME = "local-bangumi-map"' in text
    assert 'PRIMARY_PROMPT_INVOCATION = `/${PRIMARY_PROMPT_TEMPLATE_NAME} ${inputPath}`' in text
    assert 'PRIMARY_SKILL_LOAD_COMMAND' in text
    assert '/skill:${PRIMARY_SKILL_NAME}' in text
    assert 'readExpandedPrimaryPromptTemplate' in text
    assert 'expandSimplePromptTemplate' in text
    assert 'buildSkillExpansionFallback' in text
    assert 'promptWithResult(session, PRIMARY_SKILL_LOAD_COMMAND)' in text
    assert 'prompt_template_used: PRIMARY_PROMPT_TEMPLATE_NAME' in text
    assert 'prompt_template_path: PRIMARY_PROMPT_TEMPLATE_PATH' in text
    assert 'prompt_template_invocation: PRIMARY_PROMPT_INVOCATION' in text
    assert 'forced_skill_load_attempted' in text
    assert 'forced_skill_load_succeeded' in text
    assert 'forced_skill_load_fallback' in text
    assert 'Runner checkpoints may publish a foreground parallel bangumi-frontier-scout review' not in combined
    assert 'Runner checkpoint: publish a foreground parallel frontier review now.' not in combined
    assert 'Your next assistant action must be the subagent tool call below' not in combined
    assert 'subagent(${JSON.stringify(subagentCall, null, 2)})' not in combined
    assert 'function buildParallelScoutCommand' not in combined
    assert 'publishParallelScoutCheckpointIfNeeded' not in combined
    assert 'bangumi-frontier-scout' not in combined
    assert 'parallel_scout_checkpoint' not in combined
    assert 'subagent_prompt_not_followed' not in combined
    assert 'decision_or_verifier_after_checkpoint' not in combined
    assert '/parallel' not in combined
    assert 'Review Lane: atlas_count=' not in combined
    assert 'frontier_packet_count=' not in combined
    assert 'record scout reports before more evidence' not in combined
    assert 'review_lane: reviewLaneSummary' not in combined
    assert 'runner_orchestration' not in text
    assert 'The full local-bangumi-organize skill is loaded before this goal' in text
    assert 'Required project skills discovered for audit' not in text
    assert 'Relevant discovered project skills:' not in text
    assert 'WORKPAPER ADVISORY:' not in text
    assert 'Minimal params:' not in text
    assert 'Default output is compact; pass detail:true only for debugging full repair_hints' in extension_text
    assert 'choose the anchor with select_bangumi_anchor_subject(anchor_subject_id, reason)' in text
    assert 'Anchor bootstrap facts: candidate_subject_ids=' in text
    assert 'Visible output contract: act through tools and artifacts, not reasoning prose.' in text
    assert 'ACTION_AGENT_SYSTEM_PROMPT_SECTION' in text
    assert 'appendSystemPromptOverride: (base) => [' in text
    assert '## Local-to-Bangumi Action Case Agent Output Protocol' in text
    assert 'Assistant-visible text is a status channel, not a scratchpad.' in text
    assert 'On a turn that can call a custom tool or goal_complete, call the tool directly and omit explanatory prose.' in text
    assert 'action_system_prompt_appended: true' in text
    assert 'assistant_output = buildAssistantOutputStats()' in text
    assert 'long_text_message_count' in text
    assert 'very_long_text_message_count' in text
    assert 'reasoning_heading_message_count' in text
    assert 'const recipeGroupDecisionSchema = recipeParamsRuleSchema' in extension_text
    assert 'strictObject(properties)' in extension_text
    assert 'additionalProperties: false' in extension_text
    assert 'decision: recipeGroupDecisionSchema' in extension_text
    assert 'decisions: Type.Optional(Type.Array(recipeGroupDecisionSchema))' in extension_text
    assert 'subject_id: Type.Optional(Type.Number())' in extension_text
    assert 'Do not invent plural target fields such as target_subject_ids' in extension_text
    assert 'function shouldTerminateAfterTool' in extension_text
    assert 'submit_organize_recipe_params_patch' in extension_text
    assert 'terminate: shouldTerminateAfterTool(name, result)' in extension_text
    assert 'result.accepted === true || result.status === "accepted" || Boolean(result.final_result)' in extension_text
    assert 'Do not write headings such as Deciding, Evaluating, Considering' in text
    assert 'Tool arguments count as output: keep board notes, snapshots, reasons, and summaries compact.' in text
    assert 'Do not paste get_case_overview/list_local_groups/get_local_group_detail JSON into notes' in text
    assert 'do not fail_closed from an empty draft before that anchor exists' in text
    assert 'Checkpoint: continue as an action case agent.' in text
    assert 'If a tool action is available, call it now with no explanation.' in text
    assert '- save decisions' in text
    assert '- validate complete draft' in text
    assert '- patch named verifier issue' in text
    assert '- submit accepted' in text
    assert '- concrete fail_closed' in text
    assert 'Do not show reasoning narrative, reread skills, or inspect old artifacts/tests.' in text
    assert 'Choose one action' not in text
    assert 'Keep prose short. Do not print recipe JSON' not in text
    assert 'keep it compact: cite group refs and blockers only; do not paste the local group JSON' in text
    assert 'Decision check: save any group/subcluster you already judge as mapped or evidence-gap supplemental' not in text
    assert 'sample_0096' not in text
    assert 'OVERLORD' not in text
    assert 'Ple Ple' not in text
    assert 'function effectiveRuntimeBudgetSeconds()' in text
    assert 'Math.max(finishBeforeSeconds, timeoutSeconds - 5)' in text
    assert 'Math.min(45_000, Math.floor(totalBudgetMs * 0.15))' in text
    assert 'Decision row shape is compact:' not in text
    assert 'Example decisions:' not in text
    assert 'detail: Type.Optional(Type.Boolean())' in extension_text
    assert 'async function validateReadyDraftIfNeeded' in text
    assert 'async function validateReadyDraftAtCheckpoint' in text
    assert 'auto-submit after accepted params validation' in text
    assert 'agentDir: effectiveAgentDir' in text
    assert 'agentDir: agentDir || undefined' not in text
    assert 'extensions_loaded: ["local-bangumi-tools", "@narumitw/pi-goal", "@narumitw/pi-retry"]' in text
    assert 'Fact helper: search Bangumi and return compact subject/episode rows for one visible source_path.' in extension_text
    assert 'Math.min(90_000, remainingMs)' in text
    assert 'async function readJsonFile' in text
    assert 'auto-submit after accepted params validation' in text
    assert 'auto-submit after accepted recipe validation' not in text
    assert 'async function validateReadyDraftIfNeeded' in text
    assert 'auto_validate_ready_draft' in text
    assert 'Auto-validate: recipe_params_draft covers every visible local group' in text
    assert 'async function validateReadyDraftAtCheckpoint' in text
    assert 'validateReadyDraftAtCheckpoint(finalWait, "initial_wait")' in text
    assert 'submitAcceptedValidationAtCheckpoint(finalWait, "initial_wait")' in text
    assert 'validateReadyDraftAtCheckpoint(nudgeWait, "checkpoint")' in text
    assert 'submitAcceptedValidationAtCheckpoint(finalWait, "checkpoint")' in text
    assert 'phase: "auto_validation_repair"' in text
    assert 'validateReadyDraftAtCheckpoint(autoRepairWait, "auto_validation_repair")' in text
    assert 'submitAcceptedValidationAtCheckpoint(finalWait, "auto_validation_repair")' in text
    assert 'validateReadyDraftAtCheckpoint(hardWait, "hard_finish")' in text
    assert 'phase: `${phase}_auto_validate_ready_draft`' in text
    assert 'validateReadyDraftAtCheckpoint(repairWait, `final_repair_${attemptNumber}`)' in text
    assert 'finalWait.auto_validate_ready_draft' in text
    assert 'async function repairMovieSubjectLevelLocatorIfNeeded' in text
    assert 'auto_repair_movie_subject_locator' in text
    assert 'subject_level_movie_locator' in text
    assert 'verifier?.passed !== true || reviewWarnings.length || issues.length' in text
    assert 'submit_organize_recipe_params_patch' in extension_text
    assert 'validate_organize_recipe_params_patch' in text
    assert 'After accepted=true, do not call any other tool except goal_complete.' in text
