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
    assert 'Working Board' in text
    assert 'local_group, target evidence, recipe rule, status, and open issue' in text
    assert 'unknown, anchored, draftable, side_frontier, supplemental_candidate, repairing, accepted' in text
    assert 'Every tool call should move one board row forward' in text
    assert 'Call the first validate_organize_recipe_params when every visible local group has either a testable mapped rule or a testable supplemental rule.' in text
    assert 'If your own reasoning says ready, enough, validate, or submit' in text
    assert 'After invalid/review feedback, stop broad exploration.' in text
    assert 'Repair only verifier_result.issues, repair_hints, review_warnings, or repair_mode entries.' in text
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
    assert 'the next custom tool must be validate_organize_recipe_params with best-effort mapped/supplemental rules' in text
    assert 'Budget pressure is not a fail_closed reason.' in text
    assert 'Do not lower a plausibly mapped OVA/OAD/SP/movie/side-story group to supplemental just to pass validation' in text
    assert 'Do not call fail_closed with reason budget_exhausted.' in text
    assert 'For a rejected mapped frontier rule, prefer target/selector repair over supplemental downgrade' in text
    assert 'For one standalone main-title group, direct Bangumi search can be enough.' in text
    assert 'search one reliable anchor first, then use expand_related_graph as the series map for the remaining local groups' in text
    assert 'direct per-group search is a fallback for graph misses or conflicts' in text
    assert 'keep a side frontier of remaining anime/video-shaped groups' in text
    assert 'add that subject as a new anchor and continue graph closure' in text
    assert 'Mechanical accepted is the floor, not the quality target.' in text
    assert 'Do not downgrade an anime/video frontier group with plausible target evidence to supplemental just to clear a verifier issue' in text
    assert 'Treat numbered SP/bonus groups as their own board rows.' in text
    assert 'A missing parent-TV SP list is weak negative evidence for a side-content title' in text
    assert 'For numbered side-content, prefer the anchor related graph for the local side-title' in text
    assert 'Only cover as supplemental after targeted title/episode evidence does not fit.' in text
    assert 'Do not use parent-season searches such as Franchise II or Franchise III as negative evidence for side-title groups.' in text
    assert 'Graph from the side-title anchor or search the qualified side title itself before supplemental.' in text
    assert 'Do not use SP as episode_offset' in text
    assert 'For SP filename sequences, keep SP in source_pattern and use episode_offset:\\"EP\\"' in text
    assert 'SP filenames and media_kind:\\"sp\\" do not imply episode_type:\\"special\\"' in text
    assert 'When uncovered_path and duplicate_coverage are in the same local group' in text
    assert 'When uncovered_path names a sibling of an existing supplemental rule' in text
    assert 'do not change unrelated mapped movie/OVA/special exact rules just for coverage' in text
    assert 'duplicate_episode_numbers_in_group' in text
    assert 'do not include episode_id/sort/ep unless every selected file intentionally maps to the same exact row' in text
    assert 'If duplicate_target names a multi-file rule that fixed episode_id/sort/ep' in text
    assert 'exact_paths must be complete visible source_path strings' in text
    assert 'Supplemental group rules do not need subject_id, episode_id, episode_type, episode_range, or episode_offset.' in text
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
    return
    assert 'Core loop: infer local groups, expose enough Bangumi evidence for a testable recipe' in text
    assert 'get_case_overview is the map' in text
    assert 'list_local_groups is the index' in text
    assert 'get_local_group_detail expands one local group' in text
    assert 'get_local_selector_scaffold expands selector stubs' in text
    assert "the reading path is Pi's choice" in text
    assert 'The raw case_input JSON path is a fallback' in text
    assert 'It does not choose Bangumi targets, media kind, episode type, disposition, or supplemental status.' in text
    assert 'Validation is the main checkpoint.' in text
    assert 'Before first validation, keep evidence practical' in text
    assert 'get_case_overview().data.run_progress' in text
    assert 'get_recipe_state() are factual telemetry only' in text
    assert 'they are not semantic recommendations' in text
    assert 'Before Bangumi search, inspect the overview/local group index' in text
    assert 'Expand source paths with get_local_group_detail only for groups you choose to inspect.' in text
    assert 'When selector construction feels risky for a chosen group, use get_local_selector_scaffold' in text
    assert 'subject_id, media_kind, episode_type or episode_id, and supplemental disposition still come from your Bangumi evidence' in text
    assert 'selector and verifier-repair aids after you have chosen a local group' in text
    assert 'use its group_ref shorthand or copy its source_pattern' in text
    assert 'episode_range is the local captured file-number range' in text
    assert 'Treat numbering restarts such as multiple 01 files under different folders' in text
    assert 'Draft the smallest adequate recipe first' in text
    assert 'not a chosen target' in text
    assert 'Choose the semantic subject/episode yourself' in text
    assert 'draft_recipe' not in text
    assert 'recommended_recipe' not in text
    assert 'prefer validate_organize_recipe_params' in text
    assert 'zero-padded {ep:02}/{ep:02d}' in text
    assert 'Minimal recipe_params shape:' in text
    assert 'group_ref/local_group_ref for a local selector shorthand' in text
    assert 'range_start/range_end or episode_start/episode_end' in text
    assert 'number_field or target_number_field for episode_number_field' in text
    assert 'source_path, or path for one-file rules' in text
    assert 'Do not use rule-shape words such as numbered_run or exact_paths as media_kind.' in text
    assert 'validate_organize_recipe_params is a trial check, not final submission.' in text
    assert 'invalid/review results are normal contract feedback for repair_hints' in text
    assert 'validation repair_hints are the contract feedback surface' in text
    assert 'Trial-check semantic rule parameters' in text
    assert 'After representative lookups for the active groups, validate a params draft' in text
    assert 'main TV/movie representative lookups are not evidence that the SP group lacks a target' in text
    assert 'Do not mark a numbered SP sequence supplemental just because the main-season lookup did not include SP rows.' in text
    assert 'search_bangumi_subjects is already scoped to Bangumi' in text
    assert 'one bounded expand_related_graph call is often enough to build a testable recipe' in text
    assert 'same-folder movie/special collection with many visible named files' in text
    assert 'Search individual titles only for graph misses, verifier/review feedback, or real conflicts' in text
    assert 'use confirmed anime subject IDs with subject_types' in text
    assert 'not as proof of completeness' in text
    assert 'traversal_status.next_subject_ids_to_expand' in text
    assert 'not as a reason to postpone first validation' in text
    assert 'exact Bangumi episode_id, draft and validate that mapped rule now' in text
    assert 'Relation frontier exhaustion is only for final fail_closed or final supplemental justification' in text
    assert 'one-file movie-shaped Bangumi subjects' in text
    assert 'recording diaries, interviews, cast/staff talks' in text
    assert 'episode_type shown by Bangumi episode rows' in text
    assert 'media_kind is the organize category, episode_type is the row type' in text
    assert 'Use minimal semantic params' in text
    assert 'source_pattern can include folder segments' in text
    assert 'Do not use source_pattern for a single literal filename' in text
    assert 'Python treats placeholders other than {ep}/{ep:02}/{ep:02d} as wildcard text' in text
    assert 'changing CRC/hash/checksum brackets' in text
    assert 'technical suffixes such as FLAC versus FLACx2' in text
    assert 'omit episode_type unless you are copying it from the returned episode row' in text
    assert 'Do not open organize-recipe-contract skill or template files for a first draft' in text
    assert 'Do not use repeated broad searches to check missing episode rows' in text
    assert 'validation hydrates declared subject evidence' in text
    assert 'Before fail_closed, validate a best-effort params recipe once' in text
    assert 'Do not call fail_closed with budget_exhausted yourself' in text
    assert 'relation_kinds filters relation labels, not subject type' in text
    assert 'prefer subject_types:[\\"anime\\"]' in text
    assert 'ignore book/manga/novel/music/game/radio/soundtrack/live-event relations' in text
    assert 'Read relation_subjects first' in text
    assert 'Keep rule reasons short' in text
    assert 'Run progress fact: no params validation has completed yet. Evidence calls so far:' in text
    assert 'not a target recommendation or next-step instruction' in text
    assert 'Progress telemetry is factual only.' in text
    assert 'Repair mode: fix only these verifier issues' in text
    assert 'Hard finish checkpoint: you stopped again without a final accepted recipe or fail_closed result.' in text
    assert 'Progress so far: no custom tool calls were completed' in text
    assert 'Recipe artifact exists:' in text
    assert 'Use your semantic judgment to choose between validation/submission and fail_closed' in text
    assert 'phase: "hard_finish"' in text
    assert 'Final repair loop: no final result exists, but wall-clock budget remains.' in text
    assert 'maxRepairAttempts = 6' in text
    assert 'phase: `final_repair_${attemptNumber}`' in text
    assert 'phase: `final_repair_${attemptNumber}_settle`' in text
    assert 'For duplicate_target across adjacent numbered files' in text
    assert 'For duplicate_target caused by local split or variant locators such as _1/_2' in text
    assert 'exclude only those split/variant paths from the mapped sequence' in text
    assert 'evidence collection telemetry, not a semantic blocker by itself' in text
    assert 'A submit result with \\`status: "review"\\` is not final' in text
    assert 'do not hand-write or translate a raw OrganizeRecipeDraft' in text
    assert 'do not switch to raw submit_organize_recipe' in text
    assert 'raw tools are for debugging already-generated JSON only' in text
    assert 'validate_organize_recipe_params_patch is available when only a few rules changed' in text
    assert 'Patch the latest recipe params from the previous params validate/submit' in text
    assert 'Repair patch shape after a params validation' in text
    assert 'For large packages, do not enumerate dozens of obvious supplemental extras as exact_paths' in text
    assert 'keep broad supplemental groups compact with path_glob/filename_regex selectors' in text
    assert 'A related Bangumi special/OVA subject is only candidate evidence' in text
    assert 'short package SP/bonus groups, one group-specific targeted lookup plus missing/contradictory episode rows' in text
    assert 'missing_target_episode for a special_or_bonus_candidate group' in text
    assert 'related_refs' in text
    assert 'Top repair_hints' in text
    assert 'repair mode is scoped to the reported issues: change only the affected params/rules' in text
    assert 'Python turns semantic parameters into the full JSON recipe' in text
    assert 'Case input JSON is available at:' in text
    assert 'Use the navigable custom-tool hierarchy rather than expanding every JSON layer at once' in text
    assert 'not the normal working surface' in text
    assert 'compare the local file number with Bangumi episode sort and ep values' in text
    assert 'episode_number_field:\\"ep\\"' in text
    assert 'Do not print recipe JSON as plain text' in text
    assert 'split that range to a related season/cour/part subject' in text
    assert 'Do not search old run artifacts' in text
    assert 'For named anime specials/movies, use bounded relation evidence before final supplemental decisions' in text
    assert 'Do not write boolean flags such as non_bangumi_or_supplemental:true' in text
    assert 'exact_paths plus disposition:\\"non_bangumi_or_supplemental\\"' in text
    assert 'Run the bash helper only when debugging a schema/selector problem' in text
    assert 'goal_complete immediately after accepted=true' in text
    assert 'streamingBehavior: "followUp"' in text
    assert 'recipe_verifier_result.json' in text
    assert 'Fast path for this single visible file' not in text
    assert 'Required skill excerpts' not in text
    assert 'skillBlocks' not in text
    assert 'Available lazy skills' in text
    assert 'Pi has already discovered the skills by name and description.' in text
    assert '/skill:bangumi-api: use when Bangumi search results' in text
    assert '/skill:anime-release-reading: use when local anime release folders' in text
    assert '/skill:organize-recipe-contract: use when recipe params' in text
    assert 'use exactly one relevant /skill:name command or read one matching .pi/skills/<name>/SKILL.md; do not load all skills' in text
    assert 'Skill files are debug references only' not in text
    assert 'REQUIRED_SKILL_NAMES.map((name) => `.pi/skills/${name}/SKILL.md`)' not in text
    assert 'pi_assistant_messages.json' in text
    assert 'tool_name' in text
