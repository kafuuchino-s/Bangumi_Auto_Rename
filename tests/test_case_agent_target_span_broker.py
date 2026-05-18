from src.rename.case_agent.evidence_broker import EvidenceBroker
from src.rename.case_agent.models import BangumiSpanCard, BangumiItemCard, BangumiSubjectCard, CaseBudget, CaseContract, CaseHeader, EvidenceRequest, LocalFileCard, LocalSpanCard
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class FakeBangumiClient:
    pass


def test_target_span_broker_smoke():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 109)],
    )
    object.__setattr__(ws, 'bangumi_span_cards', [
        BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=[f'BE{i}' for i in range(1, 109)], target_ref_count=108, item_kind='regular', detail_equivalent=True),
        BangumiSpanCard(ref='BES2', subject_ref='BS1', target_refs=[f'BE{i}' for i in range(1, 13)], target_ref_count=12, item_kind='regular', detail_equivalent=True, source_request_ref='sp'),
        BangumiSpanCard(ref='BES3', subject_ref='BS1', target_refs=[f'BE{i}' for i in range(13, 25)], target_ref_count=12, item_kind='regular', detail_equivalent=True, source_request_ref='sp'),
    ])
    new_ws, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], expected_count=12, local_span_ref='LS1', reason='local span needs span-level proof')])
    assert result.request_results[0].response_refs == ['BES2', 'BES3']
    assert all(card.detail_equivalent is True for card in result.request_results[0].bangumi_span_cards)
    assert all(len(card.target_refs) == 12 for card in result.request_results[0].bangumi_span_cards)
    assert 'BES2' in new_ws.previous_evidence_results[0].request_results[0].response_refs


def test_target_span_broker_expected_count_and_group_refs():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-2'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 9)],
    )
    object.__setattr__(ws, 'bangumi_span_cards', [BangumiSpanCard(ref='BES1', subject_ref='BS1', group_ref='BR1', target_refs=[f'BE{i}' for i in range(1, 9)], target_ref_count=8, item_kind='regular', detail_equivalent=True, source_request_ref='sp')])
    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], group_refs=['BR1'], expected_count=8, local_span_ref='LS1', reason='local span needs span-level proof')])
    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.bangumi_span_cards[0].detail_equivalent is True
    assert rr.bangumi_span_cards[0].target_ref_count == 8


def test_target_span_broker_returns_multiple_candidates_for_expected_count_12():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-3'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 25)],
    )
    object.__setattr__(ws, 'bangumi_span_cards', [
        BangumiSpanCard(ref='BES1', subject_ref='BS1', target_refs=[f'BE{i}' for i in range(1, 13)], target_ref_count=12, item_kind='regular', detail_equivalent=True, source_request_ref='sp'),
        BangumiSpanCard(ref='BES2', subject_ref='BS1', target_refs=[f'BE{i}' for i in range(13, 25)], target_ref_count=12, item_kind='regular', detail_equivalent=True, source_request_ref='sp'),
    ])
    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], expected_count=12, local_span_ref='LS1', reason='local span needs span-level proof')])
    rr = result.request_results[0]
    assert rr.response_refs == ['BES1', 'BES2']
    assert rr.response_ref_count == 2


def test_target_span_broker_does_not_materialize_unanchored_count_guess():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-window'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 25)],
    )
    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], expected_count=12, local_span_ref='LS1', reason='local span needs span-level proof')])
    rr = result.request_results[0]
    assert rr.accepted is False
    assert rr.response_refs == []


def test_target_span_broker_materializes_single_full_subject_count_anchor():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-full-subject'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=20),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[
            *[BangumiItemCard(ref=f'BE{i}', episode_id=300 + i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 14)],
            BangumiItemCard(ref='BE14', episode_id=314, subject_ref='BS1', item_kind='special', sort=14, ep=0),
        ],
    )
    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], expected_count=13, local_span_ref='LS1', reason='local span covers all visible regular subject items')])
    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.response_refs == ['BES_LS1_1']
    assert rr.bangumi_span_cards[0].detail_equivalent is True
    assert rr.bangumi_span_cards[0].target_ref_count == 13
    assert rr.bangumi_span_cards[0].target_ref_range == ['BE1', 'BE13']


def test_target_span_broker_materializes_multiple_full_subject_count_candidates():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-full-subject-multi'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=30),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[
            BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime'),
            BangumiSubjectCard(ref='BS2', subject_id=102, subject_type='anime'),
        ],
        bangumi_items=[
            *[BangumiItemCard(ref=f'BE{i}', episode_id=300 + i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 13)],
            *[BangumiItemCard(ref=f'BE{i}', episode_id=300 + i, subject_ref='BS2', sort=i - 12, ep=i - 12) for i in range(13, 25)],
        ],
    )

    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [
        EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1', 'BS2'], expected_count=12, local_span_ref='LS1', reason='local span could match a full visible subject')
    ])

    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.response_refs == ['BES_LS1_1', 'BES_LS1_2']
    assert [card.subject_ref for card in rr.bangumi_span_cards] == ['BS1', 'BS2']


def test_target_span_broker_materializes_span_from_explicit_sort_window():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-window'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=10),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 25)],
    )
    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1'], expected_count=12, sort_start=1, sort_end=12, local_span_ref='LS1', reason='agent requested explicit target window')])
    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.response_refs == ['BES_LS1_1']
    assert rr.bangumi_span_cards[0].detail_equivalent is True
    assert rr.bangumi_span_cards[0].target_ref_count == 12
    assert rr.bangumi_span_cards[0].target_ref_range == ['BE1', 'BE12']


def test_target_span_broker_returns_multiple_subject_candidates_for_explicit_sort_window():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-window-multi-subject'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=24),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[
            BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime'),
            BangumiSubjectCard(ref='BS2', subject_id=102, subject_type='anime'),
        ],
        bangumi_items=[
            *[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 13)],
            *[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS2', sort=i - 12, ep=i - 12) for i in range(13, 25)],
        ],
    )

    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [
        EvidenceRequest(request_ref='sp', request_type='target_span', subject_refs=['BS1', 'BS2'], expected_count=12, sort_start=1, sort_end=12, local_span_ref='LS1', reason='agent requested explicit target window')
    ])

    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.response_refs == ['BES_LS1_1', 'BES_LS1_2']
    assert [card.subject_ref for card in rr.bangumi_span_cards] == ['BS1', 'BS2']


def test_target_span_broker_does_not_reject_special_like_local_span():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-special-like'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=24),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(1, 7)], allowed_file_refs=[f'LF{i}' for i in range(1, 7)]),
        local_files=[
            LocalFileCard(ref=f'LF{i}', path=f'Yuyushiki SP{i:02d}.mkv', is_main=True, file_kind='video')
            for i in range(1, 7)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS_SP',
                span_scope='token_segment',
                file_refs=[f'LF{i}' for i in range(1, 7)],
                file_ref_count=6,
                episode_token_start=1,
                episode_token_end=6,
                episode_token_count=6,
                title_cues=['SP'],
            )
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(1, 13)],
    )

    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [
        EvidenceRequest(request_ref='REQ_TARGET_SPAN_LS_SP', request_type='target_span', subject_refs=['BS1'], expected_count=6, sort_start=1, sort_end=6, local_span_ref='LS_SP', reason='stale regular target_span request')
    ])

    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.response_refs == ['BES_LS_SP_1']
    assert not any('special-like local span' in note for note in rr.notes)


def test_target_span_broker_materializes_zero_based_sort_window():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c-span-zero-window'),
        budget=CaseBudget(max_evidence_batches=3, max_api_calls_per_case=10, max_new_subject_cards=10, max_new_episode_cards=20),
        local_files=[LocalFileCard(ref='LF1')],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=101, subject_type='anime')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', episode_id=300+i, subject_ref='BS1', sort=i, ep=i) for i in range(13)],
    )

    _, result = EvidenceBroker(FakeBangumiClient()).execute_batch(ws, [
        EvidenceRequest(
            request_ref='sp',
            request_type='target_span',
            subject_refs=['BS1'],
            expected_count=13,
            sort_start=0,
            sort_end=12,
            local_span_ref='LS3',
            reason='agent requested zero-based local target window',
        )
    ])

    rr = result.request_results[0]
    assert rr.accepted is True
    assert rr.response_refs == ['BES_LS3_1']
    assert rr.bangumi_span_cards[0].target_ref_count == 13
    assert rr.bangumi_span_cards[0].target_ref_range == ['BE0', 'BE12']
