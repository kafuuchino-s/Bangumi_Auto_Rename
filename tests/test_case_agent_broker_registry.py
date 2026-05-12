from src.rename.case_agent.broker_registry import EvidenceCardRegistry, build_provenance_card
from src.rename.case_agent.models import BangumiGroupCard, BangumiItemCard, BangumiRelationCard, BangumiSubjectCard, ProvenanceCard
from src.rename.case_agent.models import CaseBudget, CaseHeader
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def _workspace():
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(),
        budget=CaseBudget(),
        bangumi_subjects=[BangumiSubjectCard(ref='BS7', subject_id=7)],
        bangumi_items=[BangumiItemCard(ref='BE12', episode_id=12, subject_ref='BS7')],
        bangumi_relations=[BangumiRelationCard(ref='BREL3', source_subject_ref='BS7', target_subject_ref='BS8', relation_kind='prequel')],
        bangumi_groups=[BangumiGroupCard(ref='BR9', group_kind='season_group', subject_refs=['BS7'])],
        provenance_cards=[ProvenanceCard(ref='PV4')],
    )


def test_registry_allocates_after_max_existing_ref():
    registry = EvidenceCardRegistry.from_workspace(_workspace())
    assert registry.allocate_subject_ref(8) == ('BS8', True)
    assert registry.allocate_item_ref(13) == ('BE13', True)
    assert registry.allocate_provenance_ref() == 'PV5'


def test_duplicate_ids_return_existing_ref():
    registry = EvidenceCardRegistry.from_workspace(_workspace())
    assert registry.allocate_subject_ref(7) == ('BS7', False)
    assert registry.allocate_item_ref(12) == ('BE12', False)


def test_synthetic_item_and_relation_group_deduplicate():
    workspace = _workspace()
    workspace = workspace.with_added_evidence(items=[BangumiItemCard(ref='BE99', synthetic=True, title='syn-key')])
    registry = EvidenceCardRegistry.from_workspace(workspace)

    assert registry.allocate_item_ref(0, synthetic_key='syn-key') == ('BE99', False)
    assert registry.allocate_relation_ref('BS7', 'BS8', 'prequel') == ('BREL3', False)
    assert registry.allocate_group_ref('BS7', 'season_group') == ('BR9', False)


def test_visible_ref_collision_raises():
    registry = EvidenceCardRegistry.from_workspace(_workspace())
    try:
        registry.ensure_new_ref_not_visible('BS7')
        assert False, 'expected collision'
    except ValueError as exc:
        assert 'visible' in str(exc)


def test_build_provenance_card_sets_created_at():
    card = build_provenance_card('PV1', 2, 'REQ1', 'op')
    assert card.ref == 'PV1'
    assert card.created_at != ''
