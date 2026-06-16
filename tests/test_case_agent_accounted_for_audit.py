from src.rename.case_agent.audit import summarize_case_agent_snapshot_refs
from src.rename.case_agent.local_bangumi_entry import _verdict_assignment_accounting
from src.rename.case_agent.models import AssignmentIntent, CaseContract, CaseDossier, CaseHeader, CaseJudgeOutput


def test_accounted_for_snapshot_fields_are_present_and_default_to_zero():
    summary = summarize_case_agent_snapshot_refs({})

    assert summary['main_file_count'] == 0
    assert summary['mapped_file_count'] == 0
    assert summary['excluded_file_count'] == 0
    assert summary['manual_review_file_count'] == 0
    assert summary['needs_more_evidence_file_count'] == 0
    assert summary['unaligned_file_count'] == 0
    assert summary['open_file_count'] == 0
    assert summary['accounted_for_count'] == 0
    assert summary['unresolved_count'] == 0
    assert summary['accepted_accounting_ready'] is False


def test_accounted_for_snapshot_fields_passthrough_counts():
    summary = summarize_case_agent_snapshot_refs({
        'main_file_count': 3,
        'mapped_file_count': 2,
        'excluded_file_count': 1,
        'manual_review_file_count': 0,
        'needs_more_evidence_file_count': 0,
        'unaligned_file_count': 0,
        'open_file_count': 0,
        'accounted_for_count': 3,
        'unresolved_count': 0,
        'accepted_accounting_ready': True,
    })

    assert summary['main_file_count'] == 3
    assert summary['mapped_file_count'] == 2
    assert summary['accounted_for_count'] == 3
    assert summary['accepted_accounting_ready'] is True


def test_manual_review_assignments_are_accounted_without_unresolved_blocker():
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-MANUAL-ACCOUNTING'),
        contract=CaseContract(
            main_file_refs=['LF1', 'LF2'],
            allowed_file_refs=['LF1', 'LF2'],
            visible_target_refs=['BE1'],
        ),
    )
    output = CaseJudgeOutput(
        action='submit_verdict',
        assignment_intents=[
            AssignmentIntent(ref='A1', file_ref='LF1', target_ref='BE1'),
            AssignmentIntent(
                ref='A2',
                file_ref='LF2',
                target_ref='UNALIGNED',
                reason='mapping_draft:pi_case_agent:manual_review:manual_review:needs human review',
            ),
        ],
    )

    accounting = _verdict_assignment_accounting(dossier, output)

    assert accounting['mapped_file_count'] == 1
    assert accounting['manual_review_file_count'] == 1
    assert accounting['unaligned_file_count'] == 0
    assert accounting['unresolved_count'] == 0
    assert accounting['accepted_accounting_ready'] is True
