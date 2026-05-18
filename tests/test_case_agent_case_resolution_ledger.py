from __future__ import annotations

from src.rename.case_agent.case_resolution_ledger import CaseResolutionLedgerCompiler, validate_case_resolution_ledger
from src.rename.case_agent.mapping_draft import apply_mapping_patches
from src.rename.case_agent.models import (
    BangumiItemCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseContract,
    CaseHeader,
    CaseResolutionLedger,
    CaseResolutionLedgerRow,
    EvidenceBatchResult,
    EvidenceRequestResult,
    LocalFileCard,
    LocalSpanCard,
    MappingDraft,
    MappingDraftRow,
)
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def _target_evidence() -> list[EvidenceBatchResult]:
    return [
        EvidenceBatchResult(
            batch_ref='EB1',
            status='accepted',
            request_results=[
                EvidenceRequestResult(
                    request_ref='REQ_SUBJECT_SEARCH_QC1',
                    request_type='subject_search',
                    accepted=True,
                    response_refs=[],
                )
            ],
        )
    ]


def _workspace(*, file_count: int = 1, subjects=None, items=None, previous_evidence_results=None) -> CaseEvidenceWorkspace:
    file_refs = [f'LF{i}' for i in range(1, file_count + 1)]
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-LEDGER'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=file_refs, allowed_file_refs=file_refs),
        local_files=[
            LocalFileCard(ref=ref, path=f'Title {index:02d}.mkv', label=f'Title {index:02d}.mkv', is_main=True)
            for index, ref in enumerate(file_refs, 1)
        ],
        local_span_cards=[
            LocalSpanCard(
                ref='LS1',
                span_scope='token_segment',
                file_refs=file_refs,
                file_ref_count=file_count,
                file_ref_samples=file_refs,
                episode_token_start=1,
                episode_token_end=file_count,
                episode_token_count=file_count,
            )
        ],
        bangumi_subjects=list(subjects or []),
        bangumi_items=list(items or []),
        previous_evidence_results=list(previous_evidence_results or []),
    )


def _draft() -> MappingDraft:
    return MappingDraft(rows=[MappingDraftRow(row_ref='MDR1', local_ref='LS1', local_ref_kind='span')])


def test_resolution_ledger_validates_visible_refs_and_exact_coverage():
    workspace = _workspace(file_count=2)
    dossier = workspace.to_dossier(round_context='ledger-test')
    ledger = CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            row_ref='MDR1',
            local_ref='LS1',
            local_refs=['LS1'],
            file_refs=['LF1', 'LF2'],
            outcome='needs_evidence',
            requested_request_types=['subject_search'],
            support_refs=['LS1'],
            reason='need Bangumi surface',
        )
    ])

    assert validate_case_resolution_ledger(dossier, _draft(), ledger) == []


def test_resolution_ledger_allows_visible_local_group_support_refs():
    workspace = _workspace(file_count=2)
    dossier = workspace.to_dossier(round_context='ledger-test')
    ledger = CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            row_ref='MDR1',
            local_ref='LS1',
            local_refs=['LS1'],
            file_refs=['LF1', 'LF2'],
            outcome='needs_evidence',
            requested_request_types=['subject_search'],
            support_refs=['LG1'],
            source_refs=['LG1'],
            reason='visible local group supports the query/work-unit claim',
        )
    ])

    assert validate_case_resolution_ledger(dossier, _draft(), ledger) == []


def test_resolution_ledger_rejects_hidden_refs_and_overlap_mechanically():
    workspace = _workspace(file_count=2)
    dossier = workspace.to_dossier(round_context='ledger-test')
    ledger = CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            local_ref='LF1',
            file_refs=['LF1'],
            outcome='needs_evidence',
            support_refs=['LF1'],
        ),
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR2',
            local_ref='LF1',
            file_refs=['LF1', 'LF999'],
            outcome='needs_evidence',
            support_refs=['LF999'],
        ),
    ])

    issue_codes = {issue.issue_code for issue in validate_case_resolution_ledger(dossier, _draft(), ledger)}
    assert 'ledger_unknown_file_refs' in issue_codes
    assert 'ledger_unknown_support_ref' in issue_codes
    assert 'ledger_missing_main_refs' in issue_codes
    assert 'ledger_duplicate_main_refs' in issue_codes


def test_resolution_ledger_compiles_visible_be_mapping_without_semantic_choice():
    workspace = _workspace(
        file_count=1,
        subjects=[BangumiSubjectCard(ref='BS1', title='Title')],
        items=[BangumiItemCard(ref='BE1', subject_ref='BS1', item_kind='episode', ep=1, sort=1)],
    )
    dossier = workspace.to_dossier(round_context='ledger-test')
    ledger = CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            row_ref='MDR1',
            local_ref='LS1',
            local_refs=['LS1'],
            file_refs=['LF1'],
            outcome='map_to_bangumi',
            chosen_item_ref='BE1',
            support_refs=['LS1', 'BE1'],
            reason='Agent chose this visible item',
        )
    ])

    result = CaseResolutionLedgerCompiler().compile(dossier, _draft(), ledger)

    assert result.blocked_rows == []
    assert len(result.compiled_patches) == 1
    assert result.compiled_patches[0].target_ref == 'BE1'
    updated, issues = apply_mapping_patches(_draft(), result.compiled_patches, dossier)
    assert issues == []
    assert updated.rows[0].disposition == 'map_to_bangumi'


def test_resolution_ledger_compiles_agent_target_absent_as_accepted_exclusion():
    workspace = _workspace(file_count=1, previous_evidence_results=_target_evidence())
    dossier = workspace.to_dossier(round_context='ledger-test')
    ledger = CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            row_ref='MDR1',
            local_ref='LS1',
            local_refs=['LS1'],
            file_refs=['LF1'],
            outcome='target_absent',
            support_refs=['LS1'],
            reason='Agent concluded Bangumi has no corresponding target',
        )
    ])

    result = CaseResolutionLedgerCompiler().compile(dossier, _draft(), ledger)

    assert result.blocked_rows == []
    assert len(result.compiled_patches) == 1
    patch = result.compiled_patches[0]
    assert patch.op == 'mark_non_bangumi_or_supplemental'
    assert patch.reason_kind == 'bangumi_target_absent'
    updated, issues = apply_mapping_patches(_draft(), result.compiled_patches, dossier)
    assert issues == []
    assert updated.rows[0].disposition == 'non_bangumi_or_supplemental'


def test_resolution_ledger_needs_evidence_creates_durable_agenda_patch():
    workspace = _workspace(file_count=1)
    dossier = workspace.to_dossier(round_context='ledger-test')
    ledger = CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            row_ref='MDR1',
            local_ref='LS1',
            local_refs=['LS1'],
            file_refs=['LF1'],
            outcome='needs_evidence',
            requested_request_types=['subject_search'],
            query_hints=['Title'],
            support_refs=['LS1'],
            reason='need target surface',
        )
    ])

    result = CaseResolutionLedgerCompiler().compile(dossier, _draft(), ledger)

    assert result.blocked_rows == []
    assert result.requested_evidence == ['subject_search']
    assert len(result.compiled_patches) == 1
    patch = result.compiled_patches[0]
    assert patch.op == 'needs_more_evidence'
    assert patch.requested_request_types == ['subject_search']


def test_resolution_ledger_can_cite_recorded_split_plan_rows_mechanically():
    workspace = _workspace(file_count=2)
    object.__setattr__(workspace, 'judge_request_audits', [{
        'note': 'orchestrator_split_plan_recorded',
        'split_cases': [{
            'plan_row_ref': 'RSP1',
            'child_case_ref': 'SPLIT1',
            'main_file_refs': ['LF1', 'LF2'],
            'title_hints': ['Title'],
            'reason': 'Agent-recorded work unit',
        }],
    }])
    dossier = workspace.to_dossier(round_context='ledger-test')
    ledger = CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            plan_row_refs=['RSP1'],
            outcome='needs_evidence',
            requested_request_types=['subject_search'],
            query_hints=['Title'],
            support_refs=['LS1'],
            reason='use the Agent-recorded split plan row as the work unit',
        )
    ])

    assert [row.plan_row_ref for row in dossier.recorded_split_plan_rows] == ['RSP1']
    assert validate_case_resolution_ledger(dossier, _draft(), ledger) == []

    result = CaseResolutionLedgerCompiler().compile(dossier, _draft(), ledger)

    assert result.blocked_rows == []
    assert result.requested_evidence == ['subject_search']
    assert len(result.compiled_patches) == 1
    assert result.compiled_patches[0].local_ref == 'LS1'


def test_resolution_ledger_feedback_points_category_row_refs_to_recorded_plan_refs():
    workspace = _workspace(file_count=2)
    object.__setattr__(workspace, 'judge_request_audits', [{
        'note': 'orchestrator_split_plan_recorded',
        'split_cases': [{
            'plan_row_ref': 'RSP1',
            'child_case_ref': 'SPLIT1',
            'main_file_refs': ['LF1', 'LF2'],
            'title_hints': ['Title'],
        }],
    }])
    dossier = workspace.to_dossier(round_context='ledger-test')
    ledger = CaseResolutionLedger(rows=[
        CaseResolutionLedgerRow(
            ledger_row_ref='CRLR1',
            row_ref='ROOT',
            file_refs=['LF1', 'LF2'],
            outcome='needs_evidence',
            requested_request_types=['subject_search'],
            query_hints=['Title'],
            support_refs=['LS1'],
        )
    ])

    result = CaseResolutionLedgerCompiler().compile(dossier, _draft(), ledger)

    assert result.compiled_patches == []
    assert result.blocked_rows
    observation = result.blocked_rows[0].observation
    assert 'ledger_unknown_row_ref' in result.blocked_rows[0].issue_codes
    assert observation['available_plan_row_refs'] == ['RSP1']
    assert 'plan_row_refs=RSP*' in result.recommended_next_observation
