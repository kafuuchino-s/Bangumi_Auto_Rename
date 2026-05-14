from src.rename.case_agent.evidence_menu import build_executable_evidence_menu, build_recommended_neutral_requests
from src.rename.case_agent.models import CaseBudget, CaseContract, CaseHeader, EvidencePlan, LocalFileCard, BangumiItemCard, BangumiSubjectCard, LocalSpanCard, MappingDraft, MappingDraftRow, QueryCard
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def test_evidence_menu_avoids_semantic_winner_and_limits_window():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c1'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref='BE1', sort=1, ep=1), BangumiItemCard(ref='BE2', sort=2, ep=2)],
    )
    menu = build_recommended_neutral_requests(ws, max_width=1)
    text = str(menu).lower()
    assert 'semantic score' not in text
    assert all(len(req.get('item_refs', req.get('anchor_file_refs', []))) <= 1 for req in menu['recommended_neutral_requests'])


def test_evidence_menu_allows_subject_search_retry_with_existing_surface():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c1-weak-recall'),
        budget=CaseBudget(max_api_calls_per_case=5, max_subject_searches=5),
        local_files=[LocalFileCard(ref='LF1', path='Show 01.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref='BE1', subject_ref='BS1', sort=1, ep=1)],
        query_cards=[
            QueryCard(ref='QC1', query_text='Show', query_kind='subject_search', query_origin='agent_composed', source_refs=['LF1']),
        ],
        diagnostics=['weak_subject_recall_retry_pending'],
    )

    menu = build_executable_evidence_menu(ws)

    assert 'REQ_SUBJECT_SEARCH_QC1' in [item['request_id'] for item in menu['prompt_summaries']]


def test_evidence_menu_executes_pending_agent_query_when_open_row_requests_subject_search():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c1-open-row-subject-search'),
        budget=CaseBudget(max_api_calls_per_case=10, max_subject_searches=10),
        local_files=[LocalFileCard(ref='LF1', path='Show SP01.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS_WRONG')],
        query_cards=[
            QueryCard(ref='QC1', query_text='Show episode list', query_kind='subject_search', query_origin='agent_composed', source_refs=['LF1']),
            QueryCard(ref='QC2', query_text='Show', query_kind='subject_search', query_origin='agent_composed', source_refs=['LF1']),
        ],
        mapping_draft=MappingDraft(rows=[
            MappingDraftRow(
                row_ref='MDR1',
                local_ref='LS1',
                local_ref_kind='span',
                requested_request_types=['subject_search'],
                query_hints=['Show'],
            )
        ]),
        plan_state=EvidencePlan(completed_menu_request_ids=['REQ_SUBJECT_SEARCH_QC1']),
    )

    menu = build_executable_evidence_menu(ws)
    request_ids = [item['request_id'] for item in menu['prompt_summaries']]

    assert 'REQ_SUBJECT_SEARCH_QC1' not in request_ids
    assert 'REQ_SUBJECT_SEARCH_QC2' in request_ids


def test_large_local_span_recommends_target_span():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c2'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref=f'LF{i}', path=f'{i}.mkv', is_main=True) for i in range(1, 25)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', sort=i, ep=i, subject_ref='BS1') for i in range(1, 25)],
    )
    object.__setattr__(ws, 'local_span_cards', [
        LocalSpanCard(ref='LS1', file_refs=[f'LF{i}' for i in range(1, 13)], file_ref_count=12, episode_token_start=1, episode_token_end=12, episode_token_count=12),
        LocalSpanCard(ref='LS2', file_refs=[f'LF{i}' for i in range(13, 25)], file_ref_count=12, episode_token_start=13, episode_token_end=24, episode_token_count=12),
    ])
    menu = build_recommended_neutral_requests(ws)
    reqs = [r for r in menu['recommended_neutral_requests'] if r['request_type'] == 'target_span']
    assert len(reqs) == 2
    assert {req['expected_count'] for req in reqs} == {12}
    assert {req['local_count'] for req in reqs} == {12}
    assert all(req['local_span_ref'] in {'LS1', 'LS2'} for req in reqs)
    assert all(req['item_kind'] == 'regular' for req in reqs)
    assert {req['sort_start'] for req in reqs} == {1, 13}
    assert {req['sort_end'] for req in reqs} == {12, 24}


def test_evidence_menu_prioritizes_agent_chosen_row_subject_beyond_visible_window():
    subjects = [BangumiSubjectCard(ref=f'BS{i}') for i in range(1, 6)]
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c2-agent-subject'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(1, 13)], allowed_file_refs=[f'LF{i}' for i in range(1, 13)]),
        local_files=[LocalFileCard(ref=f'LF{i}', path=f'Show {i:02d}.mkv', is_main=True) for i in range(1, 13)],
        bangumi_subjects=subjects,
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='directory',
                file_refs=[f'LF{i}' for i in range(1, 13)],
                file_ref_count=12,
                episode_token_start=1,
                episode_token_end=12,
                episode_token_count=12,
            )
        ],
    )
    object.__setattr__(ws, 'mapping_draft', MappingDraft(rows=[
        MappingDraftRow(
            local_ref='LS1',
            local_ref_kind='span',
            candidate_target_refs=[],
            subject_refs=['BS5'],
            requested_request_types=['episode_list', 'target_span'],
        )
    ]))

    menu = build_executable_evidence_menu(ws)
    target_span_request = menu['payload_registry']['REQ_TARGET_SPAN_LS1']
    request_ids = [item['request_id'] for item in menu['prompt_summaries']]

    assert target_span_request.subject_refs[0] == 'BS5'
    assert 'BS5' in target_span_request.subject_refs
    assert 'REQ_EPISODE_LIST_BS5' in request_ids


def test_zero_based_local_span_recommends_target_span():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c2-zero'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref=f'LF{i}', path=f'{i}.mkv', is_main=True) for i in range(13)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', sort=i, ep=i, subject_ref='BS1') for i in range(13)],
    )
    object.__setattr__(ws, 'local_span_cards', [
        LocalSpanCard(
            ref='LS1',
            file_refs=[f'LF{i}' for i in range(13)],
            file_ref_count=13,
            episode_token_start=0,
            episode_token_end=12,
            episode_token_count=13,
        ),
    ])

    menu = build_recommended_neutral_requests(ws)
    reqs = [r for r in menu['recommended_neutral_requests'] if r['request_type'] == 'target_span']

    assert len(reqs) == 1
    assert reqs[0]['local_span_ref'] == 'LS1'
    assert reqs[0]['expected_count'] == 13
    assert reqs[0]['sort_start'] == 0
    assert reqs[0]['sort_end'] == 12


def test_executable_menu_builds_all_child_span_requests_and_counts():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c3'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref=f'LF{i}', path=f'{i}.mkv', is_main=True) for i in range(1, 10)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', sort=i, ep=i, subject_ref='BS1') for i in range(1, 10)],
        local_span_cards=[LocalSpanCard(ref=f'LS{i}', span_scope='directory', file_ref_count=1, file_ref_samples=[f'LF{i}'], episode_token_start=i, episode_token_end=i, episode_token_count=1) for i in range(1, 10)],
    )
    object.__setattr__(ws, 'mapping_draft', MappingDraft(rows=[MappingDraftRow(local_ref=f'LS{i}', local_ref_kind='span', candidate_target_refs=[]) for i in range(1, 10)]))
    menu = build_executable_evidence_menu(ws)
    span_ids = [item['request_id'] for item in menu['prompt_summaries'] if item['request_id'].startswith('REQ_TARGET_SPAN_')]
    assert len(span_ids) == 9
    assert len(set(span_ids)) == 9
    assert menu['audit']['planned_span_request_count'] == 9
    assert menu['audit']['span_rows_without_candidates'] == 9


def test_executable_menu_does_not_emit_unwindowed_residual_span_requests():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c3b'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='Special Hihamu Kage.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref='BE1', sort=1, ep=1, subject_ref='BS1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_ref_count=1, file_ref_samples=['LF1'])],
    )
    object.__setattr__(ws, 'mapping_draft', MappingDraft(rows=[MappingDraftRow(local_ref='LS1', local_ref_kind='span', candidate_target_refs=[])]))

    menu = build_executable_evidence_menu(ws)

    assert not any(item['request_id'] == 'REQ_TARGET_SPAN_LS1' for item in menu['prompt_summaries'])
    assert menu['audit']['planned_span_request_count'] == 0
    assert menu['audit']['span_rows_without_candidates'] == 1
    special_ids = [item['request_id'] for item in menu['prompt_summaries'] if item['request_id'].startswith('REQ_SPECIAL_')]
    assert 'REQ_SPECIAL_EPISODE_LIST_BS1' in special_ids
    assert 'REQ_SPECIAL_RELATED_BS1' in special_ids
    assert menu['audit']['special_candidate_row_count'] == 1


def test_executable_menu_does_not_emit_special_recall_for_regular_numbered_span():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c3c'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='Show 01.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref='BE1', sort=1, ep=1, subject_ref='BS1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], episode_token_start=1, episode_token_end=1, episode_token_count=1)],
    )
    object.__setattr__(ws, 'mapping_draft', MappingDraft(rows=[MappingDraftRow(local_ref='LS1', local_ref_kind='span', candidate_target_refs=[])]))

    menu = build_executable_evidence_menu(ws)

    assert not any(item['request_id'].startswith('REQ_SPECIAL_') for item in menu['prompt_summaries'])


def test_special_like_numbered_span_uses_special_recall_not_regular_target_span():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c3sp'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=[f'LF{i}' for i in range(1, 7)], allowed_file_refs=[f'LF{i}' for i in range(1, 7)]),
        local_files=[
            LocalFileCard(ref=f'LF{i}', path=f'Yuyushiki SP{i:02d}.mkv', is_main=True, file_kind='video')
            for i in range(1, 7)
        ],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1', subject_id=1)],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', sort=i, ep=i, subject_ref='BS1') for i in range(1, 13)],
        local_span_cards=[
            LocalSpanCard(
                ref='LS_SP',
                span_scope='token_segment',
                file_refs=[f'LF{i}' for i in range(1, 7)],
                file_ref_count=6,
                file_ref_samples=[f'LF{i}' for i in range(1, 4)],
                episode_token_start=1,
                episode_token_end=6,
                episode_token_count=6,
                title_cues=['SP'],
            )
        ],
    )
    object.__setattr__(ws, 'mapping_draft', MappingDraft(rows=[MappingDraftRow(local_ref='LS_SP', local_ref_kind='span', candidate_target_refs=[])]))

    neutral = build_recommended_neutral_requests(ws)
    menu = build_executable_evidence_menu(ws)

    assert not any(req['request_type'] == 'target_span' and req.get('local_span_ref') == 'LS_SP' for req in neutral['recommended_neutral_requests'])
    assert not any(item['request_id'] == 'REQ_TARGET_SPAN_LS_SP' for item in menu['prompt_summaries'])
    special_ids = [item['request_id'] for item in menu['prompt_summaries'] if item['request_id'].startswith('REQ_SPECIAL_')]
    assert 'REQ_SPECIAL_EPISODE_LIST_BS1' in special_ids
    assert menu['audit']['planned_span_request_count'] == 0
    assert menu['audit']['special_candidate_row_count'] == 1


def test_special_recall_menu_includes_related_subjects_beyond_first_window():
    subjects = [BangumiSubjectCard(ref=f'BS{i}', subject_id=i, subject_type='anime') for i in range(1, 7)]
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c3d'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Show Special.mkv', is_main=True, file_kind='video')],
        bangumi_subjects=subjects,
        bangumi_items=[BangumiItemCard(ref='BE1', sort=1, ep=1, subject_ref='BS1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, file_ref_samples=['LF1'], episode_token_count=0)],
    )
    object.__setattr__(ws, 'mapping_draft', MappingDraft(rows=[MappingDraftRow(local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE1'])]))

    menu = build_executable_evidence_menu(ws)
    special_ids = [item['request_id'] for item in menu['prompt_summaries'] if item['request_id'].startswith('REQ_SPECIAL_')]

    assert 'REQ_SPECIAL_EPISODE_LIST_BS6' in special_ids
    assert 'REQ_SPECIAL_RELATED_BS6' in special_ids


def test_executable_menu_skips_rows_with_candidates():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c4'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='1.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref='BE1', sort=1, ep=1, subject_ref='BS1')],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=1, file_ref_samples=['LF1'])],
    )
    object.__setattr__(ws, 'mapping_draft', MappingDraft(rows=[MappingDraftRow(local_ref='LS1', local_ref_kind='span', candidate_target_refs=['BE1'])]))
    menu = build_executable_evidence_menu(ws)
    assert not any(item['request_id'] == 'REQ_TARGET_SPAN_LS1' for item in menu['prompt_summaries'])
    assert menu['audit']['span_rows_with_candidates'] == 1


def test_subject_search_menu_uses_composed_queries_not_raw_local_material():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c5'),
        budget=CaseBudget(max_api_calls_per_case=2, max_subject_searches=2),
        local_files=[LocalFileCard(ref='LF1', path='[LoliHouse] Show [BDRip 1080p FLAC].mkv', is_main=True)],
        query_cards=[
            QueryCard(ref='SQ1', query_text='[LoliHouse]', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1']),
            QueryCard(ref='SQ2', query_text='[LoliHouse] Show [BDRip 1080p FLAC]', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1']),
            QueryCard(ref='QC1', query_text='Show', query_kind='subject_search', query_origin='agent_composed', source_refs=['LF1', 'SQ2'], ignored_terms=['LoliHouse', 'BDRip', '1080p', 'FLAC']),
        ],
    )

    menu = build_executable_evidence_menu(ws)
    subject_requests = [item for item in menu['prompt_summaries'] if item['request_type'] == 'subject_search']

    assert [item['request_id'] for item in subject_requests] == ['REQ_SUBJECT_SEARCH_QC1']
    assert menu['payload_registry']['REQ_SUBJECT_SEARCH_QC1'].query_refs == ['QC1']


def test_subject_search_menu_does_not_rank_or_execute_raw_sq_queries():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c6'),
        budget=CaseBudget(max_api_calls_per_case=2, max_subject_searches=2),
        local_files=[LocalFileCard(ref='LF1', path='[KTXP] Mushishi Zoku Shou [BDRip 1080p FLAC].mkv', is_main=True)],
        query_cards=[
            QueryCard(ref='SQ1', query_text='Mushishi Zoku Shou', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1']),
            QueryCard(ref='SQ2', query_text='[KTXP]', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1']),
        ],
    )

    menu = build_executable_evidence_menu(ws)

    assert not any(item['request_type'] == 'subject_search' for item in menu['prompt_summaries'])
