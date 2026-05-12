from src.rename.case_agent.models import (
    BangumiSpanCard,
    BulkAssignmentIntent,
    CaseJudgeOutput,
    EvidenceRequest,
    EvidenceRequestResult,
    LocalSpanCard,
    SpanAlignmentClaim,
)


def test_span_models_instantiate_and_dump_cleanly():
    local_span = LocalSpanCard(ref='LS1', file_refs=['LF1'], file_ref_count=1, file_ref_range=['LF1'], file_ref_samples=['LF1'])
    bangumi_span = BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=['BE1'], target_ref_count=1, target_ref_range=['BE1'], target_ref_samples=['BE1'])
    claim = SpanAlignmentClaim(ref='SC1', local_span_ref='LS1', bangumi_span_ref='BS1')
    bulk = BulkAssignmentIntent(ref='BI1', local_span_ref='LS1', bangumi_span_ref='BS1', alignment_ref='SC1')
    req = EvidenceRequest(request_ref='R1', request_type='target_span', sort_start=1, sort_end=3, expected_count=2)
    result = EvidenceRequestResult(request_ref='R1', request_type='target_span', bangumi_span_cards=[bangumi_span])
    output = CaseJudgeOutput(action='request_evidence', span_alignment_claims=[claim], bulk_assignment_intents=[bulk], evidence_requests=[req])

    assert local_span.model_dump()['file_ref_count'] == 1
    assert bangumi_span.model_dump()['target_ref_count'] == 1
    assert output.model_dump()['span_alignment_claims'][0]['local_span_ref'] == 'LS1'
    assert result.model_dump()['bangumi_span_cards'][0]['ref'] == 'BS1'
