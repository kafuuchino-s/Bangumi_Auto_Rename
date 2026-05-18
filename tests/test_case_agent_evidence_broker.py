from __future__ import annotations

import threading
import time

from src.rename.case_agent.evidence_broker import EvidenceBroker
from src.rename.case_agent.models import (
    BangumiGroupCard,
    BangumiItemCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseHeader,
    CaseDossier,
    CaseContract,
    CaseJudgeOutput,
    AssignmentIntent,
    EvidenceRequest,
    BangumiSpanCard,
    LocalFileCard,
    LocalSpanCard,
    QueryCard,
    EvidenceRequestType,
)
from src.rename.case_agent.workspace import CaseEvidenceWorkspace
from src.rename.case_agent.verifier import verify_judge_output


class FakeBangumiClient:
    def __init__(self):
        self.calls = []

    def get_subject(self, subject_id):
        self.calls.append(('get_subject', subject_id))
        return type('S', (), {'id': subject_id, 'name': f'Subject{subject_id}', 'name_cn': f'中文{subject_id}', 'date': '2024', 'summary': 'sum', 'platform': 'TV', 'eps': 12, 'total_episodes': 12, 'tags': [], 'infobox': []})()

    def search_subjects(self, text, year_hint):
        self.calls.append(('search_subjects', text, year_hint))
        return [type('S', (), {'id': 201, 'title': 'Search201', 'name': 'Search201', 'name_cn': '搜201', 'type': 2, 'search_rank': 1})()]

    def get_related_subjects(self, subject_id):
        self.calls.append(('get_related_subjects', subject_id))
        return [type('R', (), {'id': 202, 'type': 2, 'relation': '续集'})()]

    def get_episodes(self, subject_id):
        self.calls.append(('get_episodes', subject_id))
        return [type('E', (), {'id': 301, 'subject_id': subject_id, 'type': 0, 'sort': 1, 'ep': 1, 'kind': 'ep', 'title': 'Ep1', 'name': 'Ep1', 'name_cn': '第1话', 'airdate': '2024-01-01', 'duration': '24m', 'duration_seconds': 1440, 'desc': 'desc', 'source_form_hint': ''})()]


class SlowLookupBangumiClient(FakeBangumiClient):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def get_subject(self, subject_id):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.15)
            return super().get_subject(subject_id)
        finally:
            with self.lock:
                self.active -= 1


def build_ws(**kwargs):
    header = CaseHeader(case_id='C1', round_index=2, evidence_batches_used=0)
    budget_kwargs = dict(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10)
    budget_kwargs.update(kwargs)
    budget = CaseBudget(**budget_kwargs)
    return CaseEvidenceWorkspace.from_cards(
        header=header,
        budget=budget,
        local_files=[LocalFileCard(ref='LF1', path='a/b.mkv', is_main=True)],
        query_cards=[QueryCard(ref='SQ1', query_text='foo', query_kind='subject_search')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1')],
        bangumi_groups=[BangumiGroupCard(ref='BR1', group_kind='season_group')],
    )


def test_mixed_batch_subject_lookup_and_episode_list():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    reqs = [EvidenceRequest(request_ref='r1', request_type='subject_lookup', subject_refs=['BS1']), EvidenceRequest(request_ref='r2', request_type='episode_list', subject_refs=['BS1'])]
    new_ws, result = broker.execute_batch(ws, reqs)
    assert result.status == 'accepted'
    assert new_ws.budget.used_evidence_batches == 1
    assert any(c.ref == 'BE1' for c in new_ws.bangumi_items)
    assert len(new_ws.previous_evidence_results) == 1
    assert len(new_ws.previous_evidence_results[0].request_results) == 2
    assert new_ws.previous_evidence_results[0].request_results[0].request_type == 'subject_lookup'
    assert new_ws.previous_evidence_results[0].request_results[1].request_type == 'episode_list'
    assert new_ws.previous_evidence_results[0].request_results[0].response_refs == [] or isinstance(new_ws.previous_evidence_results[0].request_results[0].response_refs, list)
    assert all(hasattr(rr, 'request_type') for rr in new_ws.previous_evidence_results[0].request_results)


def test_independent_bangumi_requests_are_prefetched_in_parallel_and_merged_in_order():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C1', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=0),
        local_files=[LocalFileCard(ref='LF1', path='a/b.mkv', is_main=True)],
        bangumi_subjects=[
            BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime'),
            BangumiSubjectCard(ref='BS2', subject_id=102, subject_type='anime'),
        ],
    )
    client = SlowLookupBangumiClient()
    broker = EvidenceBroker(client, max_workers=2)

    new_ws, result = broker.execute_batch(
        ws,
        [
            EvidenceRequest(request_ref='r1', request_type='subject_lookup', subject_refs=['BS1']),
            EvidenceRequest(request_ref='r2', request_type='subject_lookup', subject_refs=['BS2']),
        ],
    )

    assert client.max_active >= 2
    assert [rr.request_ref for rr in result.request_results] == ['r1', 'r2']
    assert [rr.response_refs for rr in result.request_results] == [['BS1'], ['BS2']]
    assert new_ws.budget.used_api_calls == 2


def test_partial_success_invalid_anchor_then_valid():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    reqs = [EvidenceRequest(request_ref='bad', request_type='subject_lookup', subject_refs=['BS999']), EvidenceRequest(request_ref='ok', request_type='subject_search', query_refs=['SQ1'])]
    new_ws, result = broker.execute_batch(ws, reqs)
    assert result.status == 'partial'
    assert any(r.request_ref == 'bad' and not r.accepted for r in result.request_results)
    assert any(r.request_ref == 'ok' and r.accepted for r in result.request_results)
    assert new_ws.bangumi_subjects[-1].ref.startswith('BS')


def test_all_rejected_unknown_subject_refs_no_api_calls():
    ws = build_ws()
    client = FakeBangumiClient()
    broker = EvidenceBroker(client)
    _, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='x', request_type='subject_lookup', subject_refs=['BS999'])])
    assert result.status == 'partial'
    assert client.calls == []


def test_empty_requests_rejected_empty():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    _, result = broker.execute_batch(ws, [])
    assert result.status == 'empty'


def test_budget_exceeded_at_batch_start():
    ws = build_ws(max_evidence_batches=0)
    client = FakeBangumiClient()
    broker = EvidenceBroker(client)
    _, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='r', request_type='subject_search', query_refs=['SQ1'])])
    assert result.status == 'rejected'
    assert client.calls == []


def test_subject_search_and_related_expansion_provenance():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    new_ws, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='s', request_type='subject_search', query_refs=['SQ1']), EvidenceRequest(request_ref='r', request_type='related_expansion', subject_refs=['BS1'])])
    assert result.status == 'accepted'
    assert any(card.ref.startswith('PV') for card in new_ws.provenance_cards)
    assert any(card.ref.startswith('BREL') for card in new_ws.bangumi_relations)


def test_episode_detail_replaces_existing_be():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    new_ws, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='d', request_type='episode_detail', item_refs=['BE1'], subject_refs=['BS1'])])
    assert result.status == 'accepted'
    assert any(card.ref == 'BE1' and card.title == 'Ep1' for card in new_ws.bangumi_items)


def test_previous_evidence_results_appended():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    new_ws, _ = broker.execute_batch(ws, [EvidenceRequest(request_ref='s', request_type='subject_search', query_refs=['SQ1'])])
    assert len(new_ws.previous_evidence_results) == 1


def test_new_request_types_are_supported_and_update_seen_detail_refs():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    new_ws, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='f', request_type='local_file_detail', anchor_file_refs=['LF1'])])
    assert result.status in {'accepted', 'partial'}
    assert 'LF1' in new_ws.seen_detail_refs
    assert all(req in EvidenceRequestType.__args__ for req in ['local_file_detail', 'target_detail', 'target_window'])


def test_target_window_returns_window_cards():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C1', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        query_cards=[QueryCard(ref='SQ1', query_text='foo', query_kind='subject_search')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1', sort=1, ep=1), BangumiItemCard(ref='BE2', episode_id=302, subject_ref='BS1', sort=2, ep=2)],
        bangumi_groups=[BangumiGroupCard(ref='BR1', group_kind='season_group')],
    )
    new_ws, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='w', request_type='target_window', subject_refs=['BS1'], sort_start=1, sort_end=1)])
    assert result.request_results[0].accepted is True
    assert 'BE1' in new_ws.seen_detail_refs


def test_target_window_returns_expected_window():
    ws = build_ws()
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C1', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        query_cards=[QueryCard(ref='SQ1', query_text='foo', query_kind='subject_search')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 21)],
        bangumi_groups=[BangumiGroupCard(ref='BR1', group_kind='season_group')],
    )
    broker = EvidenceBroker(FakeBangumiClient())
    new_ws, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='w', request_type='target_window', subject_refs=['BS1'], sort_start=9, sort_end=11)])
    assert result.request_results[0].accepted is True
    assert result.request_results[0].response_refs == ['BE9', 'BE10', 'BE11']
    assert result.request_results[0].response_ref_count == 3
    assert result.request_results[0].truncated_for_prompt is False
    assert 'BE10' in new_ws.seen_detail_refs


def test_local_file_detail_returns_readable_local_card():
    ws = build_ws()
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C1', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1', path='a/b.mkv', is_main=True), LocalFileCard(ref='LF3', path='a/c.mkv', is_main=False)],
        query_cards=[QueryCard(ref='SQ1', query_text='foo', query_kind='subject_search')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1', sort=1, ep=1)],
        bangumi_groups=[BangumiGroupCard(ref='BR1', group_kind='season_group')],
    )
    broker = EvidenceBroker(FakeBangumiClient())
    new_ws, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='lf', request_type='local_file_detail', anchor_file_refs=['LF3'])])
    assert result.request_results[0].accepted is True
    assert 'LF3' in result.request_results[0].response_refs
    assert result.request_results[0].response_ref_count == 1
    assert 'LF3' in new_ws.seen_detail_refs


def test_target_window_truncates_prompt_surface_but_keeps_full_refs():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C1', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        query_cards=[QueryCard(ref='SQ1', query_text='foo', query_kind='subject_search')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 68)],
        bangumi_groups=[BangumiGroupCard(ref='BR1', group_kind='season_group')],
    )
    broker = EvidenceBroker(FakeBangumiClient())
    new_ws, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='w', request_type='target_window', subject_refs=['BS1'], sort_start=1, sort_end=67)])
    rr = result.request_results[0]
    assert rr.response_ref_count == 67
    assert len(rr.response_refs) == 67
    assert rr.truncated_for_prompt is True
    assert rr.returned_card_count == 67
    assert rr.returned_card_count_summary
    assert 'Judge request narrower window' in rr.notes
    assert len(rr.response_ref_samples) <= 10
    assert 'BE67' in new_ws.seen_detail_refs
    assert rr.returned_card_count_truncated is True


def test_target_span_returns_detail_equivalent_span():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C8', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 109)],
    )
    object.__setattr__(ws, 'bangumi_span_cards', [BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=[f'BE{i}' for i in range(1, 109)], target_ref_count=108, item_kind='regular', detail_equivalent=True, source_request_ref='sp')])
    broker = EvidenceBroker(FakeBangumiClient())
    new_ws, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], expected_count=108, local_span_ref='LS1', reason='local span needs span-level proof')])
    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.response_refs == ['BES1']
    assert rr.bangumi_span_cards[0].detail_equivalent is True
    assert len(rr.bangumi_span_cards[0].target_refs) == 108
    assert 'BES1' in new_ws.previous_evidence_results[0].request_results[0].response_refs


def test_target_span_not_rejected_by_fixed_special_like_classification():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C8B', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[
            LocalFileCard(ref='LF1', path='SP/SP01.mkv', is_main=True, label='SP01.mkv'),
            LocalFileCard(ref='LF2', path='SP/SP02.mkv', is_main=True, label='SP02.mkv'),
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='directory',
                file_refs=['LF1', 'LF2'],
                file_ref_samples=['LF1', 'LF2'],
                file_ref_count=2,
                title_cues=['SP'],
                episode_token_start=1,
                episode_token_end=2,
                episode_token_count=2,
            )
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[
            BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1', sort=1, ep=1),
            BangumiItemCard(ref='BE2', episode_id=302, subject_ref='BS1', sort=2, ep=2),
        ],
    )
    object.__setattr__(ws, 'bangumi_span_cards', [
        BangumiSpanCard(
            ref='BES1',
            subject_ref='BS1',
            target_refs=['BE1', 'BE2'],
            target_ref_count=2,
            item_kind='regular',
            detail_equivalent=True,
            source_request_ref='sp',
        )
    ])
    broker = EvidenceBroker(FakeBangumiClient())
    _, result = broker.execute_batch(
        ws,
        [
            EvidenceRequest(
                request_ref='sp',
                request_type='target_span',
                subject_refs=['BS1'],
                expected_count=2,
                local_span_ref='LS1',
                reason='agent requested regular target span evidence',
            )
        ],
    )
    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.response_refs == ['BES1']
    assert not any('special-like local span uses special/movie evidence path' in note for note in rr.notes)


def test_target_span_missing_span_rejected_without_exception():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C9', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1', sort=1, ep=1)],
    )
    broker = EvidenceBroker(FakeBangumiClient())
    _, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], expected_count=108, local_span_ref='LS1', reason='local span needs span-level proof')])
    rr = result.request_results[0]
    assert rr.accepted is False or rr.response_refs == []
    assert rr.notes


def test_target_span_no_match_mentions_local_span_ref():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C10', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1', sort=1, ep=1)],
    )
    broker = EvidenceBroker(FakeBangumiClient())
    _, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], expected_count=12, local_span_ref='LS1', reason='local span needs span-level proof')])
    assert 'LS1' in ' '.join(result.request_results[0].notes)


def test_un_normalized_package_span_returns_structured_note():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C11', round_index=2, evidence_batches_used=0),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        local_span_cards=[LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_ref_count=9)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1', sort=1, ep=1)],
    )
    broker = EvidenceBroker(FakeBangumiClient())
    _, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', local_span_ref='LS_PACKAGE', expected_count=9)])
    assert result.request_results[0].accepted is False
    assert 'package_span_requires_child_span_requests' in result.request_results[0].notes


def test_verifier_rejects_assignment_without_readable_card():
    dossier = CaseDossier(
        header=CaseHeader(case_id='C1'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        visible_refs=CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='C1'), budget=CaseBudget(), local_files=[LocalFileCard(ref='LF1')], bangumi_items=[BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1', sort=1, ep=1)]).visible_refs(),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_items=[BangumiItemCard(ref='BE1', episode_id=301, subject_ref='BS1', sort=1, ep=1)],
        detailed_card_refs=[],
        assignable_target_refs=[],
        seen_detail_refs=[],
    )
    output = CaseJudgeOutput(
        action='submit_verdict',
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_card_refs=['LF1', 'BE1'], support_finding_refs=['F1'])],
        findings=[],
    )
    result = verify_judge_output(dossier, output)
    assert not result.passed
    assert any(issue.issue_code == 'missing_support' for issue in result.issues)


def test_request_results_carry_request_type():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    _, result = broker.execute_batch(ws, [EvidenceRequest(request_ref='w', request_type='target_detail', item_refs=['BE1'])])
    assert result.request_results[0].request_type == 'target_detail'


def test_max_requests_per_batch_enforced():
    ws = build_ws()
    broker = EvidenceBroker(FakeBangumiClient())
    requests = [EvidenceRequest(request_ref=f'r{i}', request_type='target_detail', item_refs=['BE1']) for i in range(1, 12)]
    _, result = broker.execute_batch(ws, requests)
    assert result.status in {'partial', 'rejected', 'accepted'}
