from __future__ import annotations

from src.rename.case_agent.dossier import build_case_dossier
from src.rename.case_agent.models import CaseBudget, CaseHeader, LocalFileCard, BangumiItemCard
from src.rename.case_agent.prompting import render_local_bangumi_judge_prompt


def test_span_prompt_mentions_span_policy_and_target_span_request():
    header = CaseHeader(case_id='CASE-SPAN-1', case_type='local_bangumi', round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=1, max_issue_response_rounds=1, max_requests_per_batch=2)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'pkg/ep{i}.mkv', is_main=True, parent_display='[Grp] Big Package', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 109)]
    bangumi_items = [BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i, kind='episode', item_kind='episode', title=f'Ep {i}', name=f'Ep {i}', name_cn=f'第{i}话', source_form_hint='hint', synthetic=True) for i in range(1, 109)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], bangumi_items, [], [], [])

    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')

    assert 'span_assignment_policy' in prompt
    assert 'large continuous packages should use span proof' in prompt
    assert 'target_span' in prompt
    assert 'span_alignment_claims' in prompt
    assert 'bulk_assignment_intents' in prompt
    assert 'evidence_menu_request_ids' in prompt
    assert 'REQ_TARGET_SPAN_LS1' in prompt


def test_span_prompt_does_not_full_dump_108_ref_lists():
    header = CaseHeader(case_id='CASE-SPAN-2', case_type='local_bangumi', round_index=2, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=1, max_issue_response_rounds=1, max_requests_per_batch=2)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'pkg/ep{i}.mkv', is_main=True, parent_display='[Grp] Big Package', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 109)]
    bangumi_items = [BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i, kind='episode', item_kind='episode', title=f'Ep {i}', name=f'Ep {i}', name_cn=f'第{i}话', source_form_hint='hint', synthetic=True) for i in range(1, 109)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], bangumi_items, [], [], [])

    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='evidence_rejudge')

    assert prompt.count('LF') <= 80
    assert prompt.count('BE') <= 120
    assert '"file_refs"' not in prompt
    assert '"target_refs"' not in prompt
    assert 'count' in prompt and 'range' in prompt and 'samples' in prompt
    assert 'evidence_menu_request_ids' in prompt
