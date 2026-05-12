import json

from src.rename.case_agent.dossier import (
    build_bounded_case_dossier,
    build_case_dossier,
    build_default_contract,
    build_initial_compact_projection,
    build_query_cards_from_local_cards,
    build_visible_ref_catalog,
)
from src.rename.case_agent.models import (
    BangumiItemCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseContract,
    CaseDossier,
    CaseHeader,
    LocalClusterCard,
    LocalFileCard,
    EvidenceRequestType,
)


def test_default_contract_generation():
    files = [
        LocalFileCard(ref='LF1', label='main', is_main=True),
        LocalFileCard(ref='LF2', label='subtitle'),
        LocalFileCard(ref='LF3', label='unknown'),
    ]
    items = [BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE2')]

    contract = build_default_contract(files, items)

    assert contract.visible_target_refs == ['BE1', 'BE2']
    assert "main_file_refs=['LF1']" in contract.constraints
    assert "supplemental_file_refs=['LF2', 'LF3']" in contract.constraints
    assert "allowed_file_refs=['LF1', 'LF2', 'LF3']" in contract.constraints
    assert 'target_rule=BE* or UNALIGNED' in contract.constraints


def test_visible_ref_catalog_generation():
    files = [LocalFileCard(ref='LF1'), LocalFileCard(ref='LF1'), LocalFileCard(ref='LF2')]
    clusters = [LocalClusterCard(ref='LC1'), LocalClusterCard(ref='LC2')]
    subjects = [BangumiSubjectCard(ref='BS1')]
    items = []

    catalog = build_visible_ref_catalog(files, clusters, subjects, [], [], items, [], None)

    assert catalog.local_file_refs == ['LF1', 'LF2']
    assert catalog.local_cluster_refs == ['LC1', 'LC2']
    assert catalog.bangumi_subject_refs == ['BS1']
    assert catalog.target_refs == []


def test_visible_ref_catalog_target_refs_from_items_only():
    files = []
    clusters = []
    subjects = [BangumiSubjectCard(ref='BS1')]
    items = [BangumiItemCard(ref='BE1')]

    catalog = build_visible_ref_catalog(files, clusters, subjects, [], [], items, [], None)

    assert catalog.target_refs == ['BE1']


def test_visible_target_refs_are_deduped_in_catalog():
    files = []
    clusters = []
    subjects = [BangumiSubjectCard(ref='BS1')]
    items = [BangumiItemCard(ref='BE1'), BangumiItemCard(ref='BE1')]

    catalog = build_visible_ref_catalog(files, clusters, subjects, [], [], items, [], None)

    assert catalog.target_refs == ['BE1']


def test_query_cards_generated_from_local_cards_are_mechanical_and_deduped():
    files = [
        LocalFileCard(ref='LF1', path='D:/Anime/Show/episode_01.mkv', parent_display='D:/Anime/Show', cluster_ref='LC1'),
        LocalFileCard(ref='LF2', path='D:/Anime/Show/episode_01.mkv', parent_display='D:/Anime/Show'),
    ]
    clusters = [
        LocalClusterCard(ref='LC1', cluster_name='Show', title_cues=['show-cue'], file_refs=['LF1', 'LF2']),
        LocalClusterCard(ref='LC2', cluster_name='Show', title_cues=['show-cue'], file_refs=['LF2']),
    ]

    cards = build_query_cards_from_local_cards(files, clusters)

    assert [card.ref for card in cards] == ['SQ1', 'SQ2', 'SQ3', 'SQ4']
    assert [card.query_text for card in cards] == ['D:/Anime/Show', 'episode_01.mkv', 'Show', 'show-cue']
    assert all(card.query_kind == 'subject_search' for card in cards)
    assert all(card.source_refs for card in cards)
    assert all(card.result_refs == [] for card in cards)
    assert cards[0].source_refs == ['LF1', 'LF2']


def test_build_case_dossier_minimal_usable():
    header = CaseHeader(case_id='C1')
    budget = CaseBudget(max_judge_rounds=1)
    files = [LocalFileCard(ref='LF1', label='main', path='A/B.mkv', parent_display='A')]
    clusters = [LocalClusterCard(ref='LC1', cluster_name='B', title_cues=['b'], file_refs=['LF1'])]
    subjects = [BangumiSubjectCard(ref='BS1')]
    items = [BangumiItemCard(ref='BE1')]

    dossier = build_case_dossier(
        header=header,
        budget=budget,
        local_files=files,
        local_clusters=clusters,
        bangumi_subjects=subjects,
        bangumi_relations=[],
        bangumi_groups=[],
        bangumi_items=items,
        query_cards=[],
        provenance_cards=[],
    )

    assert dossier.header.case_id == 'C1'
    assert dossier.contract.visible_target_refs == ['BE1']
    assert dossier.visible_refs.local_file_refs == ['LF1']
    assert [card.ref for card in dossier.query_cards] == ['SQ1', 'SQ2', 'SQ3', 'SQ4']
    assert dossier.previous_hypotheses == []
    assert dossier.previous_evidence_results == []
    assert dossier.verifier_issues == []


def test_build_bounded_case_dossier_compacts_overview():
    header = CaseHeader(case_id='C2')
    budget = CaseBudget(max_judge_rounds=1)
    files = [LocalFileCard(ref=f'LF{i}', label='main', is_main=True, path=f'A/B{i}.mkv', parent_display='[Snow-Raws] てーきゅう 2期') for i in range(1, 4)]
    items = [BangumiItemCard(ref=f'BE{i}', title=f'Episode {i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 6)]
    dossier = build_case_dossier(
        header=header,
        budget=budget,
        local_files=files,
        local_clusters=[],
        bangumi_subjects=[],
        bangumi_relations=[],
        bangumi_groups=[],
        bangumi_items=items,
        query_cards=[],
        provenance_cards=[],
    )

    bounded = build_bounded_case_dossier(dossier)

    assert bounded.counts['main_file_count'] == 3
    assert bounded.counts['visible_target_count'] == 5
    assert '[Snow-Raws] てーきゅう 2期' in bounded.primary_title_cues
    assert bounded.target_overview
    assert bounded.detailed_visible_cards
    assert all(req in EvidenceRequestType.__args__ for req in bounded.available_detail_request_types)
    assert set(bounded.assignable_target_refs).issubset(set(bounded.detailed_card_refs))
    assert bounded.catalog_refs.target_refs == dossier.contract.visible_target_refs
    assert bounded.previous_evidence_results == []
    assert bounded.verifier_issue_summary == []
    assert 'local_file_detail' in bounded.available_detail_request_types
    assert 'target_detail' in bounded.available_detail_request_types
    assert 'target_window' in bounded.available_detail_request_types
    assert bounded.round_context == 'initial'
    assert 'strong_candidate' not in bounded.salience_overview
    assert 'assignment' not in bounded.salience_overview


def test_bounded_dossier_keeps_hydrated_items_non_assignable_when_contract_surface_empty():
    dossier = CaseDossier(
        header=CaseHeader(case_id='C2B'),
        budget=CaseBudget(max_judge_rounds=1),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=[]),
        local_files=[LocalFileCard(ref='LF1', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1', title='Episode 1', subject_ref='BS1', sort=1, ep=1)],
        seen_detail_refs=['BE1'],
    )

    bounded = build_bounded_case_dossier(dossier)

    assert 'BE1' in bounded.catalog_refs.bangumi_item_refs
    assert 'BE1' not in bounded.catalog_refs.target_refs
    assert bounded.assignable_target_refs == []


def test_build_bounded_case_dossier_includes_salience_overview():
    header = CaseHeader(case_id='C3')
    budget = CaseBudget(max_judge_rounds=1)
    files = [LocalFileCard(ref=f'LF{i}', label='main', is_main=True, path=f'parent/ep{i}.mkv', parent_display='[Snow-Raws] てーきゅう 2期') for i in range(1, 4)]
    items = [BangumiItemCard(ref=f'BE{i}', title=f'Episode {i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 6)]
    dossier = build_case_dossier(header=header, budget=budget, local_files=files, local_clusters=[], bangumi_subjects=[], bangumi_relations=[], bangumi_groups=[], bangumi_items=items, query_cards=[], provenance_cards=[])

    bounded = build_bounded_case_dossier(dossier)

    assert 'salience_overview' in bounded.model_dump()
    assert bounded.salience_overview['bangumi']['target_count'] == 5
    assert bounded.salience_overview['risk_flags']['large_case'] is False


def test_salience_overview_large_case_flags():
    header = CaseHeader(case_id='C4')
    budget = CaseBudget(max_judge_rounds=1)
    files = [LocalFileCard(ref=f'LF{i}', label='main', is_main=True, path=f'parent/ep{i}.mkv', parent_display='[Snow-Raws] てーきゅう 2期') for i in range(1, 109)]
    items = [BangumiItemCard(ref=f'BE{i}', title=f'Episode {i}', subject_ref='BS1', sort=i, ep=i) for i in range(1, 382)]
    dossier = build_case_dossier(header=header, budget=budget, local_files=files, local_clusters=[], bangumi_subjects=[], bangumi_relations=[], bangumi_groups=[], bangumi_items=items, query_cards=[], provenance_cards=[])

    bounded = build_bounded_case_dossier(dossier)

    flags = bounded.salience_overview['risk_flags']
    assert flags['large_case'] is True
    assert flags['target_surface_large'] is True
    assert flags['insufficient_detail_cards'] is True
    assert flags['context_budget_risk'] is True


def test_salience_target_groups_aggregate_by_subject_ref():
    header = CaseHeader(case_id='C5')
    budget = CaseBudget(max_judge_rounds=1)
    files = [LocalFileCard(ref='LF1', label='main', is_main=True, path='a/b1.mkv', parent_display='[Snow-Raws] てーきゅう 2期')]
    items = [BangumiItemCard(ref='BE1', title='Ep1', subject_ref='BS1', sort=1, ep=1), BangumiItemCard(ref='BE2', title='Ep2', subject_ref='BS1', sort=2, ep=2), BangumiItemCard(ref='BE3', title='Ep1', subject_ref='BS2', sort=1, ep=1)]
    dossier = build_case_dossier(header=header, budget=budget, local_files=files, local_clusters=[], bangumi_subjects=[], bangumi_relations=[], bangumi_groups=[], bangumi_items=items, query_cards=[], provenance_cards=[])

    bounded = build_bounded_case_dossier(dossier)
    groups = bounded.salience_overview['bangumi']['target_groups']
    assert any(group['subject_ref'] == 'BS1' and group['count'] == 2 for group in groups)
    assert any(group['subject_ref'] == 'BS2' and group['count'] == 1 for group in groups)


def test_detailed_local_file_cards_included_for_seen_detail_refs():
    header = CaseHeader(case_id='C6')
    budget = CaseBudget(max_judge_rounds=1)
    files = [LocalFileCard(ref='LF1', label='main', is_main=True, path='a/b1.mkv', parent_display='[Snow-Raws] てーきゅう 2期'), LocalFileCard(ref='LF3', label='main', is_main=True, path='a/b3.mkv', parent_display='[Snow-Raws] てーきゅう 2期')]
    items = [BangumiItemCard(ref='BE1', title='Ep1', subject_ref='BS1', sort=1, ep=1)]
    dossier = build_case_dossier(header=header, budget=budget, local_files=files, local_clusters=[], bangumi_subjects=[], bangumi_relations=[], bangumi_groups=[], bangumi_items=items, query_cards=[], provenance_cards=[])
    object.__setattr__(dossier, 'seen_detail_refs', ['LF3'])
    bounded = build_bounded_case_dossier(dossier)
    assert any(card.ref == 'LF3' for card in bounded.detailed_local_file_cards)


def test_initial_compact_projection_omits_full_target_catalog():
    header = CaseHeader(case_id='C7')
    budget = CaseBudget(max_judge_rounds=1)
    files = [LocalFileCard(ref=f'LF{i}', label='main', is_main=True, path='a/b.mkv', parent_display='[Snow-Raws] てーきゅう 2期') for i in range(1, 3)]
    dossier = build_case_dossier(header, budget, files, [], [], [], [], [], [], [], [])
    bounded = build_bounded_case_dossier(dossier)
    projection = build_initial_compact_projection(bounded)
    text = json.dumps(projection, ensure_ascii=False)
    assert 'catalog_summary' in text
    assert 'target_ref_count' in text
