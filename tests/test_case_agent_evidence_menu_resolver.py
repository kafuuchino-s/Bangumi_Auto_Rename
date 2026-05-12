from src.rename.case_agent.evidence_menu_resolver import resolve_evidence_menu_requests
from src.rename.case_agent.models import CaseBudget, CaseHeader, LocalSpanCard
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def build_ws():
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C1'),
        budget=CaseBudget(max_requests_per_batch=3),
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=3, file_ref_samples=['F1'], ordering_basis='episode_token_order', episode_token_start=1, episode_token_end=3, episode_token_count=3)],
    )


def test_resolve_menu_id_to_payload():
    ws = build_ws()
    resolved, selected, unknown, registry_count = resolve_evidence_menu_requests(ws, ['REQ_TARGET_SPAN_LS1'])
    assert resolved and resolved[0].request_type == 'target_span'
    assert resolved[0].local_span_ref == 'LS1'
    assert selected == ['REQ_TARGET_SPAN_LS1']
    assert unknown == []
    assert registry_count >= 1


def test_unknown_menu_id_recorded():
    ws = build_ws()
    resolved, selected, unknown, _ = resolve_evidence_menu_requests(ws, ['BAD'])
    assert resolved == []
    assert selected == []
    assert unknown == ['BAD']


def test_resolve_ignores_unregistered_prompt_ids():
    ws = build_ws()
    resolved, selected, unknown, _ = resolve_evidence_menu_requests(ws, ['REQ_TARGET_WINDOW_4'])
    assert resolved == []
    assert selected == []
    assert unknown == ['REQ_TARGET_WINDOW_4']
