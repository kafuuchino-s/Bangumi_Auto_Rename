from src.rename.case_agent.evidence_menu import build_executable_evidence_menu
from src.rename.case_agent.models import CaseBudget, CaseHeader, LocalFileCard, BangumiItemCard, BangumiSubjectCard, LocalSpanCard
from src.rename.case_agent.workspace import CaseEvidenceWorkspace
from src.rename.case_agent.evidence_menu import validate_prompt_summary_ids_subset


def test_executable_menu_emits_span_ids_and_omits_package():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c0066'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref=f'LF{i}', path=f'{i}.mkv', is_main=True) for i in range(1, 25)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', sort=i, ep=i, subject_ref='BS1') for i in range(1, 25)],
    )
    object.__setattr__(ws, 'local_span_cards', [
        LocalSpanCard(ref='LS1', span_scope='directory', file_refs=[f'LF{i}' for i in range(1, 13)], file_ref_count=12, file_ref_samples=['LF1', 'LF2'], ordering_basis='episode_token_order', episode_token_start=1, episode_token_end=12, episode_token_count=12),
        LocalSpanCard(ref='LS2', span_scope='token_segment', file_refs=[f'LF{i}' for i in range(13, 25)], file_ref_count=12, file_ref_samples=['LF13', 'LF24'], ordering_basis='episode_token_order', episode_token_start=13, episode_token_end=24, episode_token_count=12),
        LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_refs=[f'LF{i}' for i in range(1, 25)], file_ref_count=24, file_ref_samples=['LF1', 'LF24']),
    ])

    menu = build_executable_evidence_menu(ws)
    ids = [item['request_id'] for item in menu['prompt_summaries']]

    assert 'REQ_TARGET_SPAN_LS1' in ids
    assert 'REQ_TARGET_SPAN_LS2' in ids
    assert all('LS_PACKAGE' not in request_id for request_id in ids)


def test_executable_menu_summary_is_compact_and_payload_uses_span_count():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c0067'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref=f'LF{i}', path=f'{i}.mkv', is_main=True) for i in range(1, 25)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref=f'BE{i}', sort=i, ep=i, subject_ref='BS1') for i in range(1, 25)],
    )
    object.__setattr__(ws, 'local_span_cards', [
        LocalSpanCard(ref='LS1', span_scope='directory', file_refs=[f'LF{i}' for i in range(1, 13)], file_ref_count=12, file_ref_samples=['LF1', 'LF2'], ordering_basis='episode_token_order', episode_token_start=1, episode_token_end=12, episode_token_count=12),
    ])

    menu = build_executable_evidence_menu(ws)
    summary = next(item for item in menu['prompt_summaries'] if item['request_id'] == 'REQ_TARGET_SPAN_LS1')
    payload = menu['payload_registry']['REQ_TARGET_SPAN_LS1']
    text = str(menu).lower()

    assert 'LF1' not in str(summary['summary'])
    assert 'LF2' not in str(summary['summary'])
    assert all(not ref.startswith('LF') for ref in summary['source_refs'] if ref != 'LS1')
    assert payload.expected_count == 12
    assert payload.local_span_ref == 'LS1'
    assert 'semantic score' not in text
    assert 'winner' not in text


def test_prompt_summary_ids_match_registry():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c0068'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='1.mkv', is_main=True)],
        bangumi_subjects=[BangumiSubjectCard(ref='BS1')],
        bangumi_items=[BangumiItemCard(ref='BE1', sort=1, ep=1, subject_ref='BS1')],
    )
    object.__setattr__(ws, 'local_span_cards', [LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=2, file_ref_samples=['LF1'])])
    menu = build_executable_evidence_menu(ws)

    assert validate_prompt_summary_ids_subset(menu['prompt_summaries'], menu['payload_registry']) == []
