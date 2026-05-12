from src.rename.case_agent.notebook import build_notebook
from src.rename.case_agent.models import CaseBudget, CaseHeader, LocalFileCard, MappingDraft, MappingDraftRow
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def test_notebook_is_compact_and_has_no_full_dump_flags():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c1'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
    )
    note = build_notebook(ws)
    assert note['compact'] is True
    assert note['no_full_prompt'] is True
    assert note['no_full_raw_output'] is True
    assert note['no_full_catalog'] is True


def test_notebook_includes_compact_plan_state():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c2'),
        budget=CaseBudget(),
    )
    note = build_notebook(ws)
    assert 'plan_state' in note
    assert note['plan_state']['plan_status'] == 'idle'


def test_notebook_mapping_draft_summary_includes_accounting_counts():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c3'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True), LocalFileCard(ref='LF2', path='b.mkv', is_main=True)],
        mapping_draft=MappingDraft(rows=[MappingDraftRow(local_ref='LF1', disposition='map_to_bangumi'), MappingDraftRow(local_ref='LF2', disposition='non_bangumi_or_supplemental')]),
    )
    note = build_notebook(ws)
    summary = note['mapping_draft_summary']
    assert summary['main_file_count'] == 2
    assert summary['accounted_for_count'] == 2
    assert summary['accepted_accounting_ready'] is True
