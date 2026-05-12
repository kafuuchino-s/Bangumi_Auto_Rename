from src.rename.case_agent.evidence_request_normalizer import normalize_evidence_requests
from src.rename.case_agent.models import CaseBudget, CaseHeader, EvidenceRequest, LocalSpanCard
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def build_ws():
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='C1'),
        budget=CaseBudget(max_requests_per_batch=3),
        local_span_cards=[
            LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_ref_count=9),
            LocalSpanCard(ref='LS1', span_scope='directory', file_ref_count=3, file_ref_samples=['F1', 'F2']),
            LocalSpanCard(ref='LS2', span_scope='directory', file_ref_count=3, file_ref_samples=['F3', 'F4']),
            LocalSpanCard(ref='LS3', span_scope='directory', file_ref_count=3, file_ref_samples=['F5', 'F6']),
        ],
    )


def test_package_target_span_splits_into_child_spans():
    ws = build_ws()
    req = EvidenceRequest(request_ref='R1', request_type='target_span', local_span_ref='LS_PACKAGE', expected_count=9, subject_refs=['BS1'], group_refs=['BR1'])
    normalized, audits = normalize_evidence_requests(ws, [req])
    assert [r.local_span_ref for r in normalized] == ['LS1', 'LS2', 'LS3']
    assert [r.expected_count for r in normalized] == [3, 3, 3]
    assert all(r.subject_refs == ['BS1'] for r in normalized)
    assert audits and audits[0]['note'] == 'package_span_request_split_to_child_spans'


def test_cap_works():
    ws = build_ws()
    req = EvidenceRequest(request_ref='R1', request_type='target_span', local_span_ref='LS_PACKAGE')
    normalized, audits = normalize_evidence_requests(ws, [req], max_requests=2)
    assert len(normalized) == 2
    assert audits[0]['truncated'] is True


def test_non_package_unchanged():
    ws = build_ws()
    req = EvidenceRequest(request_ref='R1', request_type='target_span', local_span_ref='LS1')
    normalized, audits = normalize_evidence_requests(ws, [req])
    assert normalized == [req]
    assert audits == []


def test_no_child_unchanged_note():
    ws = CaseEvidenceWorkspace.from_cards(header=CaseHeader(case_id='C2'), budget=CaseBudget(max_requests_per_batch=3), local_span_cards=[LocalSpanCard(ref='LS_PACKAGE', span_scope='package', file_ref_count=9)])
    req = EvidenceRequest(request_ref='R1', request_type='target_span', local_span_ref='LS_PACKAGE')
    normalized, audits = normalize_evidence_requests(ws, [req])
    assert normalized == [req]
    assert audits == []
