from src.rename.case_agent.models import AssignmentIntent, CaseContract, CaseDossier, CaseHeader, CaseJudgeOutput, EvidenceBatchResult, EvidenceGap, EvidenceRequestResult, Finding, FailClosedReason, LocalFileCard, SelfCheck, VisibleRefCatalog
from src.rename.case_agent.verifier import verify_judge_output


def make_dossier() -> CaseDossier:
    return CaseDossier(
        header=CaseHeader(case_id='CASE-1'),
        visible_refs=VisibleRefCatalog(
            local_file_refs=['LF1'],
            query_refs=['F1'],
            target_refs=['BE1', 'BE2'],
        ),
        detailed_card_refs=['BE1'],
        assignable_target_refs=['BE1'],
        seen_detail_refs=['BE1', 'BE2'],
        contract=CaseContract(
            main_file_refs=['LF1'],
            supplemental_file_refs=['LF2'],
            allowed_file_refs=['LF1', 'LF2'],
            visible_target_refs=['BE1', 'BE2'],
        ),
        previous_hypotheses=[{'ref': 'H1', 'claim': 'trace', 'evidence_refs': ['BE1']}],
    )


def verdict(*assignments: AssignmentIntent, findings=None, fail_closed_reasons=None):
    return CaseJudgeOutput(
        action='submit_verdict',
        findings=findings or [Finding(ref='F1', finding_kind='pass', description='ok')],
        assignment_intents=list(assignments),
        fail_closed_reasons=fail_closed_reasons or [],
    )


def test_main_files_exactly_once_supplemental_absent_passes():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1'))
    result = verify_judge_output(dossier, output)
    assert result.passed is True


def test_submit_verdict_with_zero_assignments_rejected_when_main_files_exist():
    dossier = make_dossier()
    output = verdict()
    result = verify_judge_output(dossier, output)
    assert any(issue.issue_code == 'action_inconsistent' for issue in result.issues)


def test_supplemental_in_assignment_with_be_rejected():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF2', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF2', 'BE1'], reason='r1'))
    result = verify_judge_output(dossier, output)
    assert any(issue.issue_code == 'coverage_error' for issue in result.issues)


def test_supplemental_in_assignment_with_unaligned_rejected():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF2', target_ref='UNALIGNED', support_finding_refs=['F1'], support_card_refs=['LF2'], reason='r1'))
    result = verify_judge_output(dossier, output)
    assert any(issue.issue_code == 'coverage_error' for issue in result.issues)


def test_supplemental_only_in_finding_evidence_gap_passes():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok'), Finding(ref='F2', finding_kind='warning', description='supplemental only')],
        evidence_gaps=[EvidenceGap(ref='G1', description='uses supplemental')],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    assert verify_judge_output(dossier, output).passed is True


def test_main_unaligned_rejected_for_accepted_verdict():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='UNALIGNED', support_finding_refs=['F1'], support_card_refs=['LF1'], reason='r1'))
    result = verify_judge_output(dossier, output)
    assert result.passed is False
    assert any(issue.issue_code == 'unaligned_not_accepted' for issue in result.issues)


def test_main_file_missing_coverage_rejected():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF2', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF2', 'BE1'], reason='r1')],
    )
    result = verify_judge_output(dossier, output)
    assert any(issue.issue_code == 'coverage_error' for issue in result.issues)


def test_missing_contract_main_refs_with_main_local_files_rejected():
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-1'),
        visible_refs=VisibleRefCatalog(local_file_refs=['LF1'], target_refs=['BE1']),
        local_files=[LocalFileCard(ref='LF1', is_main=True)],
        contract=CaseContract(main_file_refs=[], supplemental_file_refs=[], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
    )
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1'))
    result = verify_judge_output(dossier, output)
    assert any(issue.issue_code == 'coverage_error' for issue in result.issues)


def test_be_assignment_missing_file_in_support_cards_rejected():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['BE1'], reason='r1'))
    assert any(issue.message == 'support_card_refs must include file_ref and target_ref' for issue in verify_judge_output(dossier, output).issues)


def test_be_assignment_missing_target_in_support_cards_rejected():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1'], reason='r1'))
    assert any(issue.message == 'support_card_refs must include file_ref and target_ref' for issue in verify_judge_output(dossier, output).issues)


def test_support_card_refs_contains_finding_ref_rejected():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1', 'F1'], reason='r1'))
    assert any('finding refs' in issue.message for issue in verify_judge_output(dossier, output).issues)


def test_support_finding_refs_contains_visible_card_rejected():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['BE1'], support_card_refs=['LF1', 'BE1'], reason='r1'))
    assert any('must not include visible card refs' in issue.message for issue in verify_judge_output(dossier, output).issues)


def test_hypothesis_ref_known_in_hypothesis_evidence_context():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        hypotheses=[{'ref': 'H2', 'claim': 'trace', 'evidence_refs': ['H1', 'BE1']}],
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    assert verify_judge_output(dossier, output).passed is True


def test_hypothesis_ref_used_as_assignment_support_card_rejected_with_role_issue():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1', 'H1'], reason='r1'))
    issues = verify_judge_output(dossier, output).issues
    assert any(issue.issue_code == 'invalid_ref_role' for issue in issues)
    assert not any(issue.issue_code == 'unknown_ref' for issue in issues if issue.ref == 'A1')


def test_unknown_hypothesis_ref_rejected_as_unknown_ref():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        hypotheses=[{'ref': 'H2', 'claim': 'trace', 'evidence_refs': ['H999']}],
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    assert any(issue.issue_code == 'unknown_ref' for issue in verify_judge_output(dossier, output).issues)


def test_assignment_uses_hypothesis_only_missing_support_and_role_misuse():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['H1'], reason='r1')],
    )
    issues = verify_judge_output(dossier, output).issues
    assert any(issue.issue_code == 'invalid_ref_role' for issue in issues)
    assert any(issue.issue_code == 'missing_support' for issue in issues)


def test_unaligned_support_requires_file_ref_but_not_unaligned():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='UNALIGNED', support_finding_refs=['F1'], support_card_refs=[], reason='r1'))
    issues = verify_judge_output(dossier, output).issues
    assert any('support_card_refs must include file_ref and target_ref' in issue.message for issue in issues)
    assert not any('UNALIGNED support card' in issue.message for issue in issues)


def test_fail_closed_reason_invented_related_ref_is_sanitized():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='x', related_refs=['FAKE1'])],
    )
    result = verify_judge_output(dossier, output)
    assert result.passed is True
    assert any(issue.issue_code == 'auxiliary_ref_sanitized' for issue in result.issues)
    assert not any(issue.issue_code == 'unknown_ref' for issue in result.issues)


def test_duplicate_non_unaligned_target_rejected_only_for_real_targets():
    dossier = make_dossier()
    output = verdict(
        AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1'),
        AssignmentIntent(ref='A2', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r2'),
        AssignmentIntent(ref='A3', file_ref='LF1', target_ref='UNALIGNED', support_finding_refs=['F1'], support_card_refs=['LF1'], reason='r3'),
    )
    assert any(issue.issue_code == 'duplicate_target' for issue in verify_judge_output(dossier, output).issues)


def test_duplicate_visible_target_refs_preflight_diagnostic_present_in_workspace():
    from src.rename.case_agent.workspace import CaseEvidenceWorkspace
    from src.rename.case_agent.models import CaseBudget, CaseHeader, BangumiItemCard, CaseContract

    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-dup'),
        budget=CaseBudget(max_judge_rounds=1),
        contract=CaseContract(visible_target_refs=['BE1', 'BE1']),
        bangumi_items=[BangumiItemCard(ref='BE1')],
    )
    _ = workspace.visible_refs()
    assert 'dossier_target_ref_duplicate' in workspace.diagnostics


def test_visible_but_not_detail_assignment_target_rejected():
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-1'),
        visible_refs=VisibleRefCatalog(local_file_refs=['LF1'], query_refs=['F1'], target_refs=['BE1']),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1'], visible_target_refs=['BE1']),
        detailed_card_refs=[],
        assignable_target_refs=[],
        seen_detail_refs=[],
    )
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1'))
    assert any(issue.issue_code == 'invalid_target' for issue in verify_judge_output(dossier, output).issues)


def test_assignable_be_target_passes_target_surface_check():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1'))
    assert verify_judge_output(dossier, output).passed is True


def test_hidden_ref_rejected_when_not_in_detail_surface():
    dossier = make_dossier().model_copy(update={'detailed_card_refs': [], 'assignable_target_refs': [], 'seen_detail_refs': []})
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1'))
    assert any(issue.issue_code == 'invalid_target' for issue in verify_judge_output(dossier, output).issues)


def test_visible_but_detail_missing_then_passes_after_seen_ref():
    dossier = make_dossier().model_copy(update={'detailed_card_refs': ['BE1'], 'assignable_target_refs': ['BE1'], 'seen_detail_refs': ['BE1']})
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1'))
    assert verify_judge_output(dossier, output).passed is True


def test_finding_evidence_fake_ref_rejected():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok', evidence_refs=['FAKE'])],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    assert any(issue.issue_code == 'unknown_ref' for issue in verify_judge_output(dossier, output).issues)


def test_nested_evidence_gap_needed_refs_over_budget_rejected():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        evidence_gaps=[EvidenceGap(ref='G1', description='many', needed_refs=[f'BE{i}' for i in range(100)])],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    issues = verify_judge_output(dossier, output).issues
    assert any(issue.issue_code == 'output_budget_exceeded' for issue in issues)


def test_fail_closed_auxiliary_unknown_self_ref_is_sanitized_and_passes():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='fail_closed',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok', evidence_refs=['FN1'])],
        fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='stop', related_refs=['BE1'])],
    )
    result = verify_judge_output(dossier, output)
    assert result.passed is True


def test_fail_closed_reason_related_unknown_ref_is_sanitized_and_does_not_block():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='stop', related_refs=['BE999'])],
    )
    result = verify_judge_output(dossier, output)
    assert result.passed is True
    assert any(issue.issue_code == 'auxiliary_ref_sanitized' for issue in result.issues)
    assert not any(issue.issue_code == 'unknown_ref' for issue in result.issues)


def test_fail_closed_auxiliary_request_and_internal_refs_are_sanitized():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=[
            FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='stop', related_refs=['BE1', 'REQ_TARGET_SPAN_', 'MDR2']),
            FailClosedReason(ref='FR2', reason_kind='insufficient_evidence', description='bad target', related_refs=['BE999']),
        ],
    )

    result = verify_judge_output(dossier, output)

    assert result.passed is True
    assert any(issue.issue_code == 'auxiliary_ref_sanitized' for issue in result.issues)
    assert not any(issue.issue_code == 'unknown_ref' for issue in result.issues)


def test_fail_closed_reason_may_reference_prior_evidence_batch_refs():
    dossier = make_dossier().model_copy(update={
        'previous_evidence_results': [
            EvidenceBatchResult(
                batch_ref='EB1',
                request_results=[EvidenceRequestResult(request_ref='ER1', response_refs=['BE1'])],
            )
        ]
    })
    output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='budget_exhausted', description='stop', related_refs=['EB1', 'ER1'])],
    )
    assert verify_judge_output(dossier, output).passed is True


def test_fail_closed_with_assignments_non_empty_blocked():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='stop', related_refs=['BE1'])],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r')],
    )
    result = verify_judge_output(dossier, output)
    assert any(issue.issue_code == 'action_inconsistent' for issue in result.issues)


def test_sampled_needed_refs_pass_budget_guard():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        evidence_gaps=[EvidenceGap(ref='G1', description='sampled', needed_refs=['BE1', 'BE2', 'BE3'])],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    assert verify_judge_output(dossier, output).passed is True


def test_submit_verdict_with_evidence_requests_is_action_inconsistent():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        evidence_requests=[{'request_ref': 'R1', 'request_type': 'target_detail', 'item_refs': ['BE1']}],
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    issues = verify_judge_output(dossier, output).issues
    assert any(issue.issue_code == 'action_inconsistent' for issue in issues)


def test_consecutive_be_span_over_budget_rejected():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        evidence_gaps=[EvidenceGap(ref='G1', description='span', needed_refs=[f'BE{i}' for i in range(5, 40)])],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    assert any(issue.issue_code == 'output_budget_exceeded' for issue in verify_judge_output(dossier, output).issues)


def test_oversized_output_summary_mentions_ref_row_reason():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        evidence_gaps=[EvidenceGap(ref='G1', description='span', needed_refs=[f'BE{i}' for i in range(5, 40)])],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    from src.rename.case_agent.verifier import _oversized_output_summary

    summary = _oversized_output_summary(output)
    assert 'evidence_gaps.needed_refs' in summary or 'output' in summary


def test_rejected_candidate_unknown_ref_rejected():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        rejected_candidates=[{'ref': 'RC1', 'candidate_ref': 'FAKE', 'reason': 'x', 'evidence_refs': ['F1']}],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    assert any(issue.issue_code == 'unknown_ref' for issue in verify_judge_output(dossier, output).issues)


def test_contradiction_unknown_ref_rejected():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[Finding(ref='F1', finding_kind='pass', description='ok')],
        contradictions=[{'ref': 'C1', 'contradiction_kind': 'scope_mismatch', 'evidence_refs': ['FAKE'], 'description': 'x'}],
        assignment_intents=[AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1'], reason='r1')],
    )
    assert any(issue.issue_code == 'unknown_ref' for issue in verify_judge_output(dossier, output).issues)


def test_support_card_unknown_ref_rejected():
    dossier = make_dossier()
    output = verdict(AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1', support_finding_refs=['F1'], support_card_refs=['LF1', 'BE1', 'FAKE'], reason='r1'))
    assert any('visible dossier cards only' in issue.message for issue in verify_judge_output(dossier, output).issues)


def test_fail_closed_with_no_assignments_and_clean_self_checks_passes():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='no safe verdict', related_refs=['BE1'])],
        self_checks=[SelfCheck(ref='SC1', check_kind='coverage', passed=True)],
    )
    assert verify_judge_output(dossier, output).passed is True


def test_fail_closed_coverage_false_is_flagged_as_misuse():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='fail_closed',
        fail_closed_reasons=[FailClosedReason(ref='FR1', reason_kind='insufficient_evidence', description='no safe verdict', related_refs=['BE1'])],
        self_checks=[SelfCheck(ref='SC1', check_kind='coverage', passed=False)],
    )
    issues = verify_judge_output(dossier, output).issues
    assert any(issue.issue_code in {'self_check_misuse', 'self_check_failed'} for issue in issues)
