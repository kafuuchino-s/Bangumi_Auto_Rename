from src.rename.case_agent.models import CaseBudget, CaseContract, CaseHeader, LocalFileCard, BangumiItemCard
from src.rename.case_agent.workspace import CaseEvidenceWorkspace
from src.rename.case_agent.surface_ledger import build_surface_ledger


def test_surface_ledger_keeps_be_refs_opaque_and_no_gap_inference():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='c1'),
        budget=CaseBudget(),
        contract=CaseContract(visible_target_refs=[]),
        local_files=[LocalFileCard(ref='LF1', path='a.mkv', is_main=True)],
        bangumi_items=[BangumiItemCard(ref='BE1', sort=1, ep=1), BangumiItemCard(ref='BE3', sort=3, ep=3)],
    )
    ledger = build_surface_ledger(ws)
    assert ledger['summary']['be_ref_opaque'] is True
    assert ledger['summary']['no_continuous_gap_inference'] is True
    assert ledger['catalog_visible']
    assert ledger['ref_kind_counts']['bangumi_item'] == 2
    assert ledger['assignable']['count'] == 0
