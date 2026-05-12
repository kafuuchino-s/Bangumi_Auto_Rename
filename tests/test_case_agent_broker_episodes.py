from src.bangumi.models import BangumiEpisode
from src.rename.case_agent.broker_budget import BudgetLedger
from src.rename.case_agent.broker_episodes import execute_episode_detail, execute_episode_list
from src.rename.case_agent.broker_registry import EvidenceCardRegistry
from src.rename.case_agent.models import BangumiItemCard, BangumiSubjectCard, CaseBudget, CaseHeader
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class FakeBangumiClient:
    def __init__(self, episodes_by_subject: dict[int, list[BangumiEpisode]]):
        self.episodes_by_subject = episodes_by_subject
        self.calls = 0

    def get_episodes(self, subject_id: int) -> list[BangumiEpisode]:
        self.calls += 1
        return list(self.episodes_by_subject.get(subject_id, []))


def _episode(subject_id: int, episode_id: int, *, type_: int = 0, sort: int = 1, ep: int | None = 1, name: str = 'ep', name_cn: str = '集') -> BangumiEpisode:
    return BangumiEpisode(id=episode_id, subject_id=subject_id, type=type_, sort=sort, ep=ep, name=name, name_cn=name_cn, title=name_cn or name, duration='24m', duration_seconds=1440, desc=f'desc-{episode_id}')


def _workspace(*, subjects=None, items=None):
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(round_index=3),
        budget=CaseBudget(max_api_calls_per_case=10, max_new_episode_cards=10),
        bangumi_subjects=subjects or [BangumiSubjectCard(ref='BS1', subject_id=1)],
        bangumi_items=items or [],
    )


def test_regular_scope_generates_br_and_be():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: [_episode(1, 11), _episode(1, 12)]})
    from src.rename.case_agent.models import EvidenceRequest

    groups, items, provenance, result, next_budget = execute_episode_list(
        request=EvidenceRequest(request_ref='REQ0', request_type='episode_list', subject_refs=['BS1'], episode_scope='regular', max_episode_cards=10),
        workspace=workspace,
        registry=registry,
        budget=budget,
        bangumi_client=client,
    )


def test_regular_scope_generates_br_and_be_real_request():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: [_episode(1, 11), _episode(1, 12)]})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ1', request_type='episode_list', subject_refs=['BS1'], episode_scope='regular', max_episode_cards=10)
    groups, items, provenance, result, next_budget = execute_episode_list(request, workspace, registry, budget, client)
    assert len(groups) == 1
    assert len(items) == 2
    assert len(provenance) == 2
    assert result.accepted is True
    assert next_budget.budget.used_api_calls == 1
    assert next_budget.budget.used_new_episode_cards == 2


def test_special_scope_generates_special_br_be():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: [_episode(1, 11, type_=0), _episode(1, 21, type_=1)]})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ2', request_type='episode_list', subject_refs=['BS1'], episode_scope='special', max_episode_cards=10)
    groups, items, provenance, result, _ = execute_episode_list(request, workspace, registry, budget, client)
    assert len(groups) == 1
    assert groups[0].group_kind == 'special_group'
    assert len(items) == 1 and items[0].type == '1'
    assert len(provenance) == 1


def test_special_scope_creates_synthetic_subject_level_target_when_no_episode_items():
    workspace = _workspace(subjects=[BangumiSubjectCard(ref='BS1', subject_id=1, platform='Movie', source_form_hint='movie', eps=1, total_episodes=1, name='Movie Name')])
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: []})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ_SYN', request_type='episode_list', subject_refs=['BS1'], episode_scope='special', max_episode_cards=10)

    groups, items, provenance, result, _ = execute_episode_list(request, workspace, registry, budget, client)

    assert result.accepted is True
    assert len(groups) == 1
    assert len(items) == 1
    assert items[0].ref.startswith('BE')
    assert items[0].synthetic is True
    assert items[0].subject_level_target == 'true'
    assert items[0].item_kind == 'movie'
    assert items[0].source_form_hint == 'movie'
    assert len(provenance) == 1


def test_all_if_small_over_max_returns_rejected_empty():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(CaseBudget(max_api_calls_per_case=10, max_new_episode_cards=1))
    client = FakeBangumiClient({1: [_episode(1, 11), _episode(1, 12)]})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ3', request_type='episode_list', subject_refs=['BS1'], episode_scope='all_if_small', max_episode_cards=1)
    groups, items, provenance, result, next_budget = execute_episode_list(request, workspace, registry, budget, client)
    assert groups == [] and items == [] and provenance == []
    assert result.accepted is False
    assert next_budget.budget.used_api_calls == 1


def test_explicit_sort_window_filters():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: [_episode(1, 11, sort=1), _episode(1, 12, sort=2), _episode(1, 13, sort=3)]})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ4', request_type='episode_list', subject_refs=['BS1'], episode_scope='explicit_sort_window', sort_start=2, sort_end=3, max_episode_cards=10)
    _, items, _, _, _ = execute_episode_list(request, workspace, registry, budget, client)
    assert [item.sort for item in items] == [2, 3]


def test_tail_window_returns_tail_items_mechanically():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: [_episode(1, 11, sort=1), _episode(1, 12, sort=2), _episode(1, 13, sort=3)]})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ5', request_type='episode_list', subject_refs=['BS1'], episode_scope='tail_window', max_episode_cards=2)
    _, items, _, _, _ = execute_episode_list(request, workspace, registry, budget, client)
    assert [item.episode_id for item in items] == [12, 13]


def test_duplicate_episode_id_reused_no_new_be():
    workspace = _workspace(items=[BangumiItemCard(ref='BE9', episode_id=11, subject_ref='BS1')])
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: [_episode(1, 11), _episode(1, 12)]})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ6', request_type='episode_list', subject_refs=['BS1'], episode_scope='regular', max_episode_cards=10)
    _, items, _, _, next_budget = execute_episode_list(request, workspace, registry, budget, client)
    assert [item.episode_id for item in items] == [12]
    assert next_budget.budget.used_new_episode_cards == 1


def test_invisible_subject_rejected():
    workspace = _workspace(subjects=[])
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(CaseBudget(max_api_calls_per_case=10))
    client = FakeBangumiClient({})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ7', request_type='episode_list', subject_refs=['BS1'])
    groups, items, provenance, result, _ = execute_episode_list(request, workspace, registry, budget, client)
    assert groups == [] and items == [] and provenance == []
    assert result.accepted is False


def test_episode_detail_enriches_visible_be():
    workspace = _workspace(items=[BangumiItemCard(ref='BE1', episode_id=11, subject_ref='BS1', sort=1, ep=1, title='old')])
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: [_episode(1, 11, sort=1, name='new', name_cn='新集')]})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ8', request_type='episode_detail', subject_refs=['BS1'], item_refs=['BE1'])
    items, provenance, result, _ = execute_episode_detail(request, workspace, registry, budget, client)
    assert len(items) == 1
    assert items[0].ref == 'BE1'
    assert items[0].title == '新集'
    assert result.accepted is True
    assert len(provenance) == 1


def test_provenance_and_budget_consumed():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient({1: [_episode(1, 11)]})
    from src.rename.case_agent.models import EvidenceRequest
    request = EvidenceRequest(request_ref='REQ9', request_type='episode_list', subject_refs=['BS1'], episode_scope='regular', max_episode_cards=10)
    _, _, provenance, _, next_budget = execute_episode_list(request, workspace, registry, budget, client)
    assert len(provenance) == 1
    assert next_budget.budget.used_api_calls == 1
    assert next_budget.budget.used_new_episode_cards == 1
