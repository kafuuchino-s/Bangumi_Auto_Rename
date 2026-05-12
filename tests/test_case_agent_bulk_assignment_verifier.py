from src.rename.case_agent.models import (
    AssignmentIntent,
    BangumiSpanCard,
    BulkAssignmentIntent,
    CaseContract,
    CaseDossier,
    CaseHeader,
    CaseJudgeOutput,
    Finding,
    LocalSpanCard,
    SpanAlignmentClaim,
    VisibleRefCatalog,
)
from src.rename.case_agent.verifier import verify_judge_output


def make_bulk_dossier() -> CaseDossier:
    local_refs = [f'LF{i}' for i in range(1, 109)]
    target_refs = [f'BE{i}' for i in range(1, 109)]
    return CaseDossier(
        header=CaseHeader(case_id='CASE-BULK'),
        visible_refs=VisibleRefCatalog(local_file_refs=local_refs, target_refs=target_refs, query_refs=['F1']),
        contract=CaseContract(main_file_refs=local_refs, allowed_file_refs=local_refs, visible_target_refs=target_refs),
        local_span_cards=[LocalSpanCard(ref='LS1', file_refs=local_refs, file_ref_count=108, file_ref_range=local_refs, file_ref_samples=local_refs[:3])],
        bangumi_span_cards=[BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=target_refs, target_ref_count=108, target_ref_range=target_refs, target_ref_samples=target_refs[:3], detail_equivalent=True)],
        detailed_card_refs=target_refs,
        assignable_target_refs=target_refs,
        seen_detail_refs=target_refs,
    )


def make_multi_bulk_dossier() -> CaseDossier:
    local_refs = [f'LF{i}' for i in range(1, 109)]
    target_refs = [f'BE{i}' for i in range(1, 109)]
    return CaseDossier(
        header=CaseHeader(case_id='CASE-BULK-MULTI'),
        visible_refs=VisibleRefCatalog(local_file_refs=local_refs, target_refs=target_refs, query_refs=['F1']),
        contract=CaseContract(main_file_refs=local_refs, allowed_file_refs=local_refs, visible_target_refs=target_refs),
        local_span_cards=[
            LocalSpanCard(ref='LS1', file_refs=local_refs[:54], file_ref_count=54, file_ref_range=local_refs[:54], file_ref_samples=local_refs[:3]),
            LocalSpanCard(ref='LS2', file_refs=local_refs[54:], file_ref_count=54, file_ref_range=local_refs[54:], file_ref_samples=local_refs[54:57]),
        ],
        bangumi_span_cards=[
            BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=target_refs[:54], target_ref_count=54, target_ref_range=target_refs[:54], target_ref_samples=target_refs[:3], detail_equivalent=True),
            BangumiSpanCard(ref='BS2', subject_ref='SUB1', group_ref='GR1', target_refs=target_refs[54:], target_ref_count=54, target_ref_range=target_refs[54:], target_ref_samples=target_refs[54:57], detail_equivalent=True),
        ],
        detailed_card_refs=target_refs,
        assignable_target_refs=target_refs,
        seen_detail_refs=target_refs,
    )


def make_bulk_output(**overrides) -> CaseJudgeOutput:
    base = dict(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        span_alignment_claims=[SpanAlignmentClaim(ref='SC1', local_span_ref='LS1', bangumi_span_ref='BS1')],
        bulk_assignment_intents=[BulkAssignmentIntent(ref='BI1', local_span_ref='LS1', bangumi_span_ref='BS1', alignment_ref='SC1', support_finding_refs=['F1'], support_card_refs=['LS1', 'BS1'])],
    )
    base.update(overrides)
    return CaseJudgeOutput(**base)


def make_multi_bulk_output() -> CaseJudgeOutput:
    return CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        span_alignment_claims=[
            SpanAlignmentClaim(ref='SC1', local_span_ref='LS1', bangumi_span_ref='BS1'),
            SpanAlignmentClaim(ref='SC2', local_span_ref='LS2', bangumi_span_ref='BS2'),
        ],
        bulk_assignment_intents=[
            BulkAssignmentIntent(ref='BI1', local_span_ref='LS1', bangumi_span_ref='BS1', alignment_ref='SC1', support_finding_refs=['F1'], support_card_refs=['LS1', 'BS1']),
            BulkAssignmentIntent(ref='BI2', local_span_ref='LS2', bangumi_span_ref='BS2', alignment_ref='SC2', support_finding_refs=['F1'], support_card_refs=['LS2', 'BS2']),
        ],
    )


def test_bulk_assignment_expands_and_passes():
    dossier = make_bulk_dossier()
    result = verify_judge_output(dossier, make_bulk_output())
    assert result.passed is True


def test_bulk_assignment_expands_to_108_and_passes():
    dossier = make_bulk_dossier()
    result = verify_judge_output(dossier, make_bulk_output())
    assert result.passed is True
    assert len(result.issues) == 0


def test_multiple_bulk_intents_expand_and_cover_all_files():
    dossier = make_multi_bulk_dossier()
    output = make_multi_bulk_output()
    result = verify_judge_output(dossier, output)
    assert result.passed is True
    assert len(result.issues) == 0


def test_bulk_count_mismatch_blocked():
    dossier = make_bulk_dossier().model_copy(update={
        'bangumi_span_cards': [BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=[f'BE{i}' for i in range(1, 108)], target_ref_count=107, target_ref_range=[f'BE{i}' for i in range(1, 108)], target_ref_samples=['BE1'], detail_equivalent=True)],
    })
    result = verify_judge_output(dossier, make_bulk_output())
    assert any(issue.issue_code == 'count_mismatch' for issue in result.issues)


def test_bulk_detail_equivalent_false_blocked():
    dossier = make_bulk_dossier().model_copy(update={
        'bangumi_span_cards': [BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=[f'BE{i}' for i in range(1, 109)], target_ref_count=108, target_ref_range=[f'BE{i}' for i in range(1, 109)], target_ref_samples=['BE1'], detail_equivalent=False)],
    })
    result = verify_judge_output(dossier, make_bulk_output())
    assert any(issue.issue_code == 'invalid_span_alignment' for issue in result.issues)


def test_bulk_hidden_target_blocked():
    dossier = make_bulk_dossier().model_copy(update={
        'bangumi_span_cards': [BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=[f'BX{i}' for i in range(1, 109)], target_ref_count=108, target_ref_range=[f'BX{i}' for i in range(1, 109)], target_ref_samples=['BX1'], detail_equivalent=True)],
    })
    result = verify_judge_output(dossier, make_bulk_output())
    assert any(issue.issue_code == 'invalid_target' for issue in result.issues)


def test_bulk_duplicate_target_blocked():
    dossier = make_bulk_dossier().model_copy(update={
        'bangumi_span_cards': [BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=['BE1'] * 108, target_ref_count=108, target_ref_range=['BE1'], target_ref_samples=['BE1'], detail_equivalent=True)],
    })
    result = verify_judge_output(dossier, make_bulk_output())
    assert any(issue.issue_code == 'duplicate_target' for issue in result.issues)


def test_bulk_missing_alignment_blocked():
    dossier = make_bulk_dossier()
    output = make_bulk_output(span_alignment_claims=[])
    result = verify_judge_output(dossier, output)
    assert any(issue.issue_code == 'missing_span_ref' for issue in result.issues)


def test_bulk_missing_one_span_blocked():
    dossier = make_multi_bulk_dossier().model_copy(update={'local_span_cards': [LocalSpanCard(ref='LS1', file_refs=[f'LF{i}' for i in range(1, 55)], file_ref_count=54, file_ref_range=[f'LF{i}' for i in range(1, 55)], file_ref_samples=['LF1', 'LF2', 'LF3'])]})
    result = verify_judge_output(dossier, make_multi_bulk_output())
    assert any(issue.issue_code == 'missing_span_ref' or issue.issue_code == 'coverage_error' for issue in result.issues)


def test_overlapping_local_spans_blocked():
    dossier = make_multi_bulk_dossier().model_copy(update={
        'local_span_cards': [
            LocalSpanCard(ref='LS1', file_refs=[f'LF{i}' for i in range(1, 55)], file_ref_count=54, file_ref_range=[f'LF{i}' for i in range(1, 55)], file_ref_samples=['LF1', 'LF2', 'LF3']),
            LocalSpanCard(ref='LS2', file_refs=[f'LF{i}' for i in range(54, 109)], file_ref_count=55, file_ref_range=[f'LF{i}' for i in range(54, 109)], file_ref_samples=['LF54', 'LF55', 'LF56']),
        ]
    })
    result = verify_judge_output(dossier, make_multi_bulk_output())
    assert result.passed is False
    assert any(issue.issue_code == 'coverage_error' for issue in result.issues)


def test_overlapping_target_spans_blocked():
    dossier = make_multi_bulk_dossier().model_copy(update={
        'bangumi_span_cards': [
            BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=[f'BE{i}' for i in range(1, 55)], target_ref_count=54, target_ref_range=[f'BE{i}' for i in range(1, 55)], target_ref_samples=['BE1', 'BE2', 'BE3'], detail_equivalent=True),
            BangumiSpanCard(ref='BS2', subject_ref='SUB1', group_ref='GR1', target_refs=[f'BE{i}' for i in range(54, 109)], target_ref_count=55, target_ref_range=[f'BE{i}' for i in range(54, 109)], target_ref_samples=['BE54', 'BE55', 'BE56'], detail_equivalent=True),
        ]
    })
    result = verify_judge_output(dossier, make_multi_bulk_output())
    assert result.passed is False
    assert any(issue.issue_code in {'coverage_error', 'count_mismatch', 'duplicate_target'} for issue in result.issues)


def test_duplicate_target_across_spans_blocked():
    dossier = make_multi_bulk_dossier().model_copy(update={
        'bangumi_span_cards': [
            BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=[f'BE{i}' for i in range(1, 55)], target_ref_count=54, target_ref_range=[f'BE{i}' for i in range(1, 55)], target_ref_samples=['BE1', 'BE2', 'BE3'], detail_equivalent=True),
            BangumiSpanCard(ref='BS2', subject_ref='SUB1', group_ref='GR1', target_refs=['BE54'] + [f'BE{i}' for i in range(55, 109)], target_ref_count=54, target_ref_range=['BE54'] + [f'BE{i}' for i in range(55, 109)], target_ref_samples=['BE54', 'BE55', 'BE56'], detail_equivalent=True),
        ]
    })
    result = verify_judge_output(dossier, make_multi_bulk_output())
    assert result.passed is False
    assert any(issue.issue_code in {'duplicate_target', 'invalid_span_alignment', 'coverage_error'} for issue in result.issues)


def test_non_detail_equivalent_span_blocked():
    dossier = make_multi_bulk_dossier().model_copy(update={
        'bangumi_span_cards': [
            BangumiSpanCard(ref='BS1', subject_ref='SUB1', group_ref='GR1', target_refs=[f'BE{i}' for i in range(1, 55)], target_ref_count=54, target_ref_range=[f'BE{i}' for i in range(1, 55)], target_ref_samples=['BE1', 'BE2', 'BE3'], detail_equivalent=False),
            BangumiSpanCard(ref='BS2', subject_ref='SUB1', group_ref='GR1', target_refs=[f'BE{i}' for i in range(55, 109)], target_ref_count=54, target_ref_range=[f'BE{i}' for i in range(55, 109)], target_ref_samples=['BE55', 'BE56', 'BE57'], detail_equivalent=True),
        ]
    })
    result = verify_judge_output(dossier, make_multi_bulk_output())
    assert any(issue.issue_code == 'invalid_span_alignment' for issue in result.issues)


def test_bulk_and_explicit_overlap_blocked():
    dossier = make_bulk_dossier()
    output = make_bulk_output(
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='explicit')],
    )
    result = verify_judge_output(dossier, output)
    assert any(issue.message == 'bulk assignments must not overlap explicit assignment_intents' for issue in result.issues)


def test_bulk_support_refs_invalid_blocked():
    dossier = make_bulk_dossier()
    output = make_bulk_output(
        bulk_assignment_intents=[BulkAssignmentIntent(ref='BI1', local_span_ref='LS1', bangumi_span_ref='BS1', alignment_ref='SC1', support_finding_refs=['FAKE'], support_card_refs=['LS1', 'BS1'])],
    )
    result = verify_judge_output(dossier, output)
    assert any(issue.issue_code == 'missing_support' for issue in result.issues)
