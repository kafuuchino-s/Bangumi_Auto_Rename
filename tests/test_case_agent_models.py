from src.rename.case_agent.models import (
    BangumiSpanCard,
    BangumiItemCard,
    CaseDossier,
    CaseJudgeOutput,
    EvidenceMenuRequest,
    EvidenceMenuRequestSummary,
    BulkAssignmentIntent,
    EvidenceRequest,
    EvidenceRequestResult,
    Hypothesis,
    LocalSpanCard,
    LocalFileCard,
    SpanAlignmentClaim,
)


def test_extra_fields_rejected_for_representative_models():
    for model, payload in [
        (CaseJudgeOutput, {'action': 'request_evidence', 'summary': '', 'unexpected': 1}),
        (BangumiItemCard, {'ref': 'BI1', 'item_kind': 'episode', 'unexpected': 1}),
        (EvidenceRequest, {'request_type': 'subject_lookup', 'unexpected': 1}),
    ]:
        try:
            model.model_validate(payload)
            assert False, f'expected validation error for {model.__name__}'
        except Exception as exc:
            assert 'extra_forbidden' in str(exc)


def test_minimal_case_dossier_can_be_constructed():
    dossier = CaseDossier()
    assert dossier.header.case_type == 'local_bangumi'
    assert dossier.visible_refs.local_file_refs == []
    assert dossier.local_files == []


def test_case_judge_output_all_top_level_actions_constructible():
    for action in ['request_evidence', 'submit_verdict', 'fail_closed', 'issue_response']:
        output = CaseJudgeOutput.model_validate({'action': action, 'summary': ''})
        assert output.action == action
        assert output.evidence_menu_request_ids == []


def test_invalid_literal_values_fail():
    try:
        BangumiItemCard.model_validate({'ref': 'BI1', 'item_kind': 'invalid'})
        assert False, 'expected validation error'
    except Exception as exc:
        assert 'literal_error' in str(exc)

    try:
        EvidenceRequest.model_validate({'request_type': 'invalid'})
        assert False, 'expected validation error'
    except Exception as exc:
        assert 'literal_error' in str(exc)


def test_no_open_dict_or_any_fields_in_case_agent_models():
    models = [
        CaseJudgeOutput,
        BangumiItemCard,
        EvidenceRequest,
        CaseDossier,
        Hypothesis,
        LocalFileCard,
    ]
    for model in models:
        for field in model.model_fields.values():
            ann = field.annotation
            text = str(ann)
            assert 'dict[' not in text
            assert 'Any' not in text


def test_evidence_menu_request_models_have_expected_defaults_and_strict_schema():
    request = EvidenceMenuRequest.model_validate({'request_id': 'EM1'})
    summary = EvidenceMenuRequestSummary.model_validate({})

    assert request.request_id == 'EM1'
    assert request.request_type == ''
    assert request.summary == ''
    assert request.neutral is True
    assert request.source_refs == []
    assert request.expected_result == ''
    assert summary.request_ids == []
    assert summary.summary == ''

    for model in [EvidenceMenuRequest, EvidenceMenuRequestSummary]:
        try:
            model.model_validate({'unexpected': 1})
            assert False, f'expected validation error for {model.__name__}'
        except Exception as exc:
            assert 'extra_forbidden' in str(exc)


def test_span_models_and_extended_output_fields():
    local_span = LocalSpanCard(ref='LS1', file_refs=['LF1'], file_ref_count=1, file_ref_range=['LF1'], file_ref_samples=['LF1'])
    bangumi_span = BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=['BE1'], target_ref_count=1, target_ref_range=['BE1'], target_ref_samples=['BE1'])
    claim = SpanAlignmentClaim(ref='SC1', local_span_ref='LS1', bangumi_span_ref='BS1')
    bulk = BulkAssignmentIntent(ref='BI1', local_span_ref='LS1', bangumi_span_ref='BS1', alignment_ref='SC1')
    req = EvidenceRequest.model_validate({'request_ref': 'R1', 'request_type': 'target_span', 'sort_start': 1, 'sort_end': 3, 'expected_count': 2})
    result = EvidenceRequestResult(request_ref='R1', request_type='target_span', bangumi_span_cards=[bangumi_span])
    output = CaseJudgeOutput(action='request_evidence', span_alignment_claims=[claim], bulk_assignment_intents=[bulk], evidence_requests=[req])
    assert local_span.model_dump()['ref'] == 'LS1'
    assert bangumi_span.model_dump()['subject_ref'] == 'SUB1'
    assert output.model_dump()['span_alignment_claims'][0]['local_span_ref'] == 'LS1'
    assert result.model_dump()['bangumi_span_cards'][0]['ref'] == 'BS1'
