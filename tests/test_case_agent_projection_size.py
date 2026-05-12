from __future__ import annotations

import json

from src.rename.case_agent.dossier import build_case_dossier, build_bounded_case_dossier, build_initial_compact_projection
from src.rename.case_agent.models import CaseBudget, CaseHeader, LocalFileCard, BangumiItemCard, BangumiSubjectCard, BangumiGroupCard, QueryCard
from src.rename.case_agent.prompting import render_local_bangumi_judge_prompt


def test_initial_projection_size_and_ref_counts_are_bounded():
    header = CaseHeader(case_id='CASE-0066-LIKE', case_type='local_bangumi', round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=8)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'folder/ep{i}.mkv', is_main=True, parent_display='[Snow-Raws] てーきゅう 2期', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 101)]
    items = [BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 301)]
    dossier = build_case_dossier(header, budget, local_files, [], [BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')], [], [BangumiGroupCard(ref='BR1', group_kind='season_group')], items, [QueryCard(ref='SQ1', query_text='query', query_kind='subject_search', source_refs=[f'LF{i}' for i in range(1, 101)])], [])
    bounded = build_bounded_case_dossier(dossier)
    projection = build_initial_compact_projection(bounded)
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')

    assert len(json.dumps(projection, ensure_ascii=False).encode('utf-8')) < 45000
    assert len(prompt.encode('utf-8')) < 75000
    assert 'target_window' in prompt and 'target_detail' in prompt and 'local_file_detail' in prompt
    assert 'BE1' in prompt and 'BE300' in prompt
    assert prompt.count('BE') <= 50
    assert prompt.count('LF') <= 50
    assert 'file_refs' not in prompt or prompt.count('file_refs') < 10
    assert 'target_refs' not in prompt or prompt.count('target_refs') < 10
    assert 'evidence_menu_request_ids' in prompt
    assert 'LS_PACKAGE' in prompt and 'overview-only' in prompt


def test_non_initial_projection_is_compact():
    header = CaseHeader(case_id='CASE-0066-LIKE', case_type='local_bangumi', round_index=2, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=8)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'folder/ep{i}.mkv', is_main=True, parent_display='[Snow-Raws] てーきゅう 2期', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 101)]
    items = [BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 301)]
    dossier = build_case_dossier(header, budget, local_files, [], [BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')], [], [BangumiGroupCard(ref='BR1', group_kind='season_group')], items, [QueryCard(ref='SQ1', query_text='query', query_kind='subject_search', source_refs=[f'LF{i}' for i in range(1, 101)])], [])
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='evidence_rejudge')
    assert len(prompt.encode('utf-8')) < 120000
    assert prompt.count('BE') <= 120
    assert 'file_refs' not in prompt or prompt.count('file_refs') < 20
    assert 'target_refs' not in prompt or prompt.count('target_refs') < 20


def test_policy_retry_projection_is_compact():
    header = CaseHeader(case_id='CASE-0066-LIKE', case_type='local_bangumi', round_index=2, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=8)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'folder/ep{i}.mkv', is_main=True, parent_display='[Snow-Raws] てーきゅう 2期', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 101)]
    items = [BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 301)]
    dossier = build_case_dossier(header, budget, local_files, [], [BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')], [], [BangumiGroupCard(ref='BR1', group_kind='season_group')], items, [QueryCard(ref='SQ1', query_text='query', query_kind='subject_search', source_refs=[f'LF{i}' for i in range(1, 101)])], [])
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='policy_retry')
    assert len(prompt.encode('utf-8')) < 120000
    assert 'policy_retry' in prompt


def test_issue_response_projection_is_compact():
    header = CaseHeader(case_id='CASE-0066-LIKE', case_type='local_bangumi', round_index=3, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=8)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'folder/ep{i}.mkv', is_main=True, parent_display='[Snow-Raws] てーきゅう 2期', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 101)]
    items = [BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 301)]
    dossier = build_case_dossier(header, budget, local_files, [], [BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')], [], [BangumiGroupCard(ref='BR1', group_kind='season_group')], items, [QueryCard(ref='SQ1', query_text='query', query_kind='subject_search', source_refs=[f'LF{i}' for i in range(1, 101)])], [])
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='issue_response')
    assert len(prompt.encode('utf-8')) < 120000
    assert prompt.count('BE') <= 120


def test_executable_menu_projection_size_remains_bounded():
    header = CaseHeader(case_id='CASE-0066-LIKE', case_type='local_bangumi', round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=8)
    local_files = [LocalFileCard(ref=f'LF{i}', path=f'folder/ep{i}.mkv', is_main=True, parent_display='[Snow-Raws] てーきゅう 2期', cluster_ref='LC1', label='main', file_kind='video') for i in range(1, 101)]
    items = [BangumiItemCard(ref=f'BE{i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 301)]
    dossier = build_case_dossier(header, budget, local_files, [], [BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')], [], [BangumiGroupCard(ref='BR1', group_kind='season_group')], items, [QueryCard(ref='SQ1', query_text='query', query_kind='subject_search', source_refs=[f'LF{i}' for i in range(1, 101)])], [])
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')
    assert len(prompt.encode('utf-8')) < 80000
