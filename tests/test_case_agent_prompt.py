from __future__ import annotations

from src.rename.case_agent.dossier import build_case_dossier
from src.rename.case_agent.models import BangumiItemCard, CaseBudget, CaseHeader, EvidenceBatchResult, EvidenceRequestResult, LocalFileCard
from src.rename.case_agent.prompting import render_local_bangumi_judge_prompt


def _minimal_dossier():
    header = CaseHeader(case_id="CASE-001", case_type="local_bangumi", round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=3, max_api_calls_per_case=5, max_subject_searches=2, max_search_results_per_query=5, max_related_depth=2, max_new_subject_cards=2, max_new_episode_cards=2)
    local_files = [LocalFileCard(ref="LF1", path="folder/ep1.mkv", is_main=True, parent_display="demo", cluster_ref="LC1", label="main", file_kind="video")]
    return build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], [])


def test_render_local_bangumi_judge_prompt_contains_required_terms():
    dossier = _minimal_dossier()
    prompt = render_local_bangumi_judge_prompt(dossier)

    assert "Local→Bangumi" in prompt
    assert "main_file_refs" in prompt
    assert "supplemental_file_refs" in prompt
    assert "assignment_intents" in prompt
    assert "must never appear" in prompt or "never appear in `assignment_intents`" in prompt
    assert "BE*" in prompt
    assert "UNALIGNED" in prompt
    assert "support_finding_refs" in prompt
    assert "request_evidence" in prompt
    assert "submit_verdict" in prompt
    assert "fail_closed" in prompt
    assert "issue_response" in prompt
    assert "accepted" in prompt
    assert "If you cannot do that safely, use `fail_closed`" in prompt
    assert "ROUND_KIND" in prompt
    assert "issue_response" in prompt and "submit_verdict" in prompt and "fail_closed" in prompt
    assert "EvidenceBroker" in prompt
    assert "subject_search" in prompt
    assert "SQ*" in prompt
    assert "no TMDB" in prompt or "禁止 TMDB" in prompt or "TMDB" in prompt
    assert "no web/filesystem" in prompt or "web" in prompt and "filesystem" in prompt
    assert "Role boundary contract" in prompt
    assert "planner/orchestrator" in prompt
    assert "evidence_requests are a legacy defensive fallback only" in prompt or "legacy defensive fallback" in prompt
    assert "detail-equivalent `BangumiSpanCard` refs" in prompt
    assert "Do not invent new `REQ_*` IDs after planner evidence has been executed" in prompt
    assert "support_card_refs" in prompt
    assert "Reuse broad finding refs" in prompt
    assert "undeclared finding ref" in prompt
    assert "matches a `ref` in your own `findings` array" in prompt
    assert "explanation-only state" in prompt
    assert "file_ref" in prompt
    assert "target_ref" in prompt
    assert "Do not put finding refs in `support_card_refs`" in prompt or "finding refs in support_card_refs" in prompt
    assert "Do not put visible card refs in `support_finding_refs`" in prompt or "visible card refs in support_finding_refs" in prompt
    assert "self_check" in prompt
    assert "visible refs" in prompt.lower()
    assert "Output Budget" in prompt
    assert "sampled refs" in prompt
    assert "coverage self_check" in prompt
    assert "span_alignment_claims" in prompt
    assert "bulk_assignment_intents" in prompt
    assert "target_span" in prompt
    assert "span reasoning" in prompt.lower()
    assert "108 assignments" in prompt or "Do **not** list 108 assignments" in prompt


def test_render_local_bangumi_judge_prompt_embeds_case_payload():
    dossier = _minimal_dossier()
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind="judge")

    assert "CASE-001" in prompt
    assert '"case_id": "CASE-001"' in prompt
    assert '"local_bangumi"' in prompt


def test_render_local_bangumi_judge_prompt_uses_bounded_payload():
    header = CaseHeader(case_id="CASE-002", case_type="local_bangumi", round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = [LocalFileCard(ref=f"LF{i}", path=f"folder/ep{i}.mkv", is_main=True, parent_display="[Snow-Raws] てーきゅう 2期", cluster_ref="LC1", label="main", file_kind="video") for i in range(1, 5)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], [])

    prompt = render_local_bangumi_judge_prompt(dossier)

    assert '"counts"' in prompt
    assert '"available_detail_request_types"' in prompt
    assert '"detailed_visible_cards"' in prompt
    assert '"primary_title_cues"' in prompt
    assert '"assignable_target_refs"' in prompt
    assert 'previous_evidence_results' in prompt
    assert 'verifier_issue_summary' in prompt
    assert 'catalog_refs' in prompt
    assert 'salience_overview' in prompt
    assert 'factual map' in prompt or 'not a decision' in prompt
    assert 'local_file_detail' in prompt
    assert 'target_detail' in prompt
    assert 'target_window' in prompt
    assert 'must request evidence first' in prompt or 'premature_fail_closed' in prompt or 'do **not** immediately choose `fail_closed`' in prompt


def test_render_local_bangumi_judge_prompt_policy_retry_mentions_sparse_detail_request_rule():
    dossier = _minimal_dossier()
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='policy_retry')

    assert 'policy_retry' in prompt
    assert 'policy_retry' in prompt and 'request_evidence' in prompt
    assert 'target_window' in prompt
    assert 'local_file_detail' in prompt
    assert 'boundary' in prompt and 'representative' in prompt
    assert 'sort_start' in prompt and 'sort_end' in prompt
    assert 'target sample refs' in prompt or 'target_detail' in prompt


def test_render_local_bangumi_judge_prompt_initial_mentions_policy_retry_for_large_sparse_anchor_cases():
    dossier = _minimal_dossier()
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')

    assert 'policy_retry' in prompt
    assert 'request_evidence' in prompt
    assert 'do **not** immediately choose `fail_closed`' in prompt or 'must request evidence first' in prompt


def test_render_local_bangumi_judge_prompt_final_round_disallows_more_evidence():
    dossier = _minimal_dossier()
    dossier.header.max_rounds = 2
    dossier.header.round_index = 1
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='issue_response')

    assert 'final judge opportunity' in prompt or 'final round' in prompt
    assert 'do **not** request more evidence' in prompt


def test_render_local_bangumi_judge_prompt_initial_uses_compact_projection():
    header = CaseHeader(case_id='CASE-003', case_type='local_bangumi', round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = [LocalFileCard(ref=f"LF{i}", path=f"folder/ep{i}.mkv", is_main=True, parent_display="[Snow-Raws] てーきゅう 2期", cluster_ref="LC1", label="main", file_kind="video") for i in range(1, 6)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], [])
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')
    assert 'catalog_summary' in prompt
    assert 'contract_overview' in prompt
    assert 'source_ref_count' in prompt
    assert 'source_ref_samples' in prompt
    assert 'full' not in prompt.lower() or 'visible_target_refs' not in prompt
    assert 'local_span_cards' in prompt
    assert 'bangumi_span_cards' in prompt
    assert 'span_assignment_policy' in prompt


def test_render_local_bangumi_judge_prompt_includes_phase_g_compact_sections():
    dossier = _minimal_dossier()
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')
    assert 'surface_ledger_summary' in prompt
    assert 'evidence_menu' in prompt
    assert 'executable_evidence_menu' in prompt
    assert 'evidence_menu_request_ids' in prompt
    assert 'action_policy' in prompt
    assert 'notebook_compact' in prompt
    assert 'issue_router_summary' in prompt
    assert 'winner' not in prompt.lower()
    assert 'fail_closed auxiliary evidence_refs' in prompt or 'auxiliary refs' in prompt.lower()


def test_render_local_bangumi_judge_prompt_span_fields_are_usable():
    dossier = _minimal_dossier()
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='issue_response')

    assert 'SpanAlignmentClaim' in prompt or 'span_alignment_claims' in prompt
    assert 'BulkAssignmentIntent' in prompt or 'bulk_assignment_intents' in prompt
    assert 'alignment_ref' in prompt
    assert 'mode=by_index' in prompt or 'by_index' in prompt


def test_render_local_bangumi_judge_prompt_mentions_package_overview_only():
    dossier = _minimal_dossier()
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')
    assert 'overview-only' in prompt and 'LS_PACKAGE' in prompt


def test_render_local_bangumi_judge_prompt_initial_does_not_dump_full_contract_lists():
    header = CaseHeader(case_id='CASE-004', case_type='local_bangumi', round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = [LocalFileCard(ref=f"LF{i}", path=f"folder/ep{i}.mkv", is_main=True, parent_display="[Snow-Raws] てーきゅう 2期", cluster_ref="LC1", label="main", file_kind="video") for i in range(1, 4)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], [])
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')
    assert 'LF1' in prompt
    assert 'LF3' in prompt
    assert 'visible_target_refs' not in prompt or 'BE1, BE2' not in prompt


def test_render_local_bangumi_judge_prompt_executable_evidence_menu_is_compact():
    header = CaseHeader(case_id='CASE-007', case_type='local_bangumi', round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=3, max_api_calls_per_case=5, max_subject_searches=2, max_search_results_per_query=5, max_related_depth=2, max_new_subject_cards=2, max_new_episode_cards=2)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'folder/ep{i}.mkv', is_main=True, parent_display='demo', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 5)]
    bangumi_items = [BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i, kind='episode', item_kind='episode', title=f'Title {i}', name=f'Name {i}', name_cn=f'名称{i}', source_form_hint='hint', synthetic=True) for i in range(1, 5)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], bangumi_items, [], [], [])

    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')

    assert 'request_summaries' in prompt
    assert 'request_id' in prompt
    assert 'expected_result' in prompt
    assert 'full payload' not in prompt.lower()
    assert '"file_refs"' not in prompt
    assert '"parent_refs"' not in prompt


def test_render_local_bangumi_judge_prompt_does_not_emit_synthetic_menu_ids():
    dossier = _minimal_dossier()
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')
    assert 'REQ_TARGET_WINDOW_4' not in prompt


def test_render_local_bangumi_judge_prompt_evidence_rejudge_keeps_compact_cards():
    header = CaseHeader(case_id='CASE-006', case_type='local_bangumi', round_index=2, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=3, max_api_calls_per_case=5, max_subject_searches=2, max_search_results_per_query=5, max_related_depth=2, max_new_subject_cards=2, max_new_episode_cards=2)
    local_files = [LocalFileCard(ref='LF1', path='folder/sub/ep1.mkv', is_main=True, parent_display='[Grp] Demo Title 2', cluster_ref='LC1', label='main', file_kind='video')]
    bangumi_items = [BangumiItemCard(ref=f'BE{i}', subject_ref='S1', sort=i, ep=i, kind='episode', item_kind='episode', title=f'Title {i}', name=f'Name {i}', name_cn=f'名称{i}', source_form_hint='hint', synthetic=True, parent_refs=[f'P{i}', f'P{i+1}']) for i in range(1, 382)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], bangumi_items, [], [], evidence_results=[])
    object.__setattr__(dossier, 'seen_detail_refs', ['LF1', 'BE1'])

    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='evidence_rejudge')

    assert 'basename' in prompt and 'path_tail' in prompt and 'parent_display' in prompt
    assert 'subject_ref' in prompt and 'source_form_hint' in prompt and 'synthetic' in prompt
    assert prompt.count('"ref": "BE') <= 20
    assert 'request_id' in prompt


def test_render_local_bangumi_judge_prompt_compacts_large_ref_sets_and_keeps_readable_cards():
    header = CaseHeader(case_id='CASE-005', case_type='local_bangumi', round_index=2, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=3, max_api_calls_per_case=5, max_subject_searches=2, max_search_results_per_query=5, max_related_depth=2, max_new_subject_cards=2, max_new_episode_cards=2)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'folder/sub/ep{i}.mkv', is_main=True, parent_display='[Grp] Demo Title 2', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 4)]
    bangumi_items = [BangumiItemCard(ref=f'BE{i}', subject_ref='S1', sort=i, ep=i, kind='episode', item_kind='episode', title=f'Title {i}', name=f'Name {i}', name_cn=f'名称{i}', source_form_hint='hint', synthetic=True, parent_refs=[f'P{i}', f'P{i+1}']) for i in range(1, 382)]
    evidence = [EvidenceBatchResult(batch_ref='B1', status='accepted', request_results=[EvidenceRequestResult(request_type='subject_search', response_refs=[f'REF{i}' for i in range(1, 101)])])]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], bangumi_items, [], [], evidence_results=evidence)
    object.__setattr__(dossier, 'seen_detail_refs', ['LF1', 'BE1'])

    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')

    assert prompt.count('"ref": "BE') <= 20
    assert prompt.count('REF') <= 20
    assert 'count' in prompt and 'range' in prompt and 'sample_refs' in prompt
    assert 'subject_ref' in prompt and 'source_form_hint' in prompt and 'synthetic' in prompt
    assert 'basename' in prompt and 'path_tail' in prompt and 'parent_display' in prompt
    assert 'Episode' in prompt or 'episode' in prompt
    assert 'Name 1' in prompt and '名称1' in prompt
