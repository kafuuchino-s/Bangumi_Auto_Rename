from types import SimpleNamespace

from src.rename.case_agent.broker_budget import BudgetLedger
from src.rename.case_agent.broker_registry import EvidenceCardRegistry
from src.rename.case_agent.broker_search import execute_subject_search
from src.rename.case_agent.models import CaseBudget, CaseHeader, EvidenceRequest, LocalFileCard, QueryCard
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class FakeBangumiClient:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def search_subjects(self, query_text, year_hint=None):
        self.calls.append((query_text, year_hint))
        if self.error:
            raise self.error
        return self.results


def _workspace():
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(round_index=2),
        budget=CaseBudget(max_api_calls_per_case=3, max_subject_searches=2, max_new_subject_cards=2),
        local_files=[LocalFileCard(ref='LF1', path='foo.mkv')],
        query_cards=[QueryCard(ref='SQ1', query_text='foo', query_kind='subject_search', source_refs=['LF1'])],
    )


def test_subject_search_generates_subject_and_provenance_cards():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    ledger = BudgetLedger(workspace.budget)
    client = FakeBangumiClient([SimpleNamespace(id=11, type=2, name='Anime A', name_cn='A CN', search_rank=3)])
    request = EvidenceRequest(request_ref='ER1', request_type='subject_search', query_refs=['SQ1'], max_subjects=5)

    subjects, provenance, result, next_budget = execute_subject_search(request, workspace, registry, ledger, client)

    assert client.calls == [('foo', None)]
    assert [s.ref for s in subjects] == ['BS1']
    assert subjects[0].search_query_ref == 'SQ1'
    assert subjects[0].search_rank == 3
    assert len(provenance) == 1 and provenance[0].ref == 'PV1'
    assert result.accepted is True
    assert result.response_refs == ['BS1']
    assert next_budget.budget.used_api_calls == 1
    assert next_budget.budget.used_subject_searches == 1


def test_duplicate_existing_subject_skips_new_card():
    workspace = _workspace()
    workspace = workspace.with_added_evidence(subjects=[])
    registry = EvidenceCardRegistry.from_workspace(workspace)
    registry.subject_id_to_ref[11] = 'BS_EXISTING'
    ledger = BudgetLedger(workspace.budget)
    client = FakeBangumiClient([SimpleNamespace(id=11, type=2, name='Anime A')])
    request = EvidenceRequest(request_ref='ER1', request_type='subject_search', query_refs=['SQ1'], max_subjects=5)

    subjects, provenance, result, _ = execute_subject_search(request, workspace, registry, ledger, client)

    assert subjects == []
    assert provenance == []
    assert result.response_refs == ['BS_EXISTING']


def test_non_anime_results_are_skipped_and_empty_is_reported():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    ledger = BudgetLedger(workspace.budget)
    client = FakeBangumiClient([SimpleNamespace(id=11, type=1, name='Book'), SimpleNamespace(id=12, type=2, name='Anime B')])
    request = EvidenceRequest(request_ref='ER1', request_type='subject_search', query_refs=['SQ1'], max_subjects=2)

    subjects, provenance, result, _ = execute_subject_search(request, workspace, registry, ledger, client)

    assert len(subjects) == 1
    assert len(provenance) == 1
    assert result.response_refs == ['BS1']


def test_rejects_unknown_or_not_allowed_query_refs():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    ledger = BudgetLedger(workspace.budget)
    client = FakeBangumiClient([])

    bad_request = EvidenceRequest(request_ref='ER1', request_type='subject_search', query_refs=['SQ_UNKNOWN'])
    _, _, result, _ = execute_subject_search(bad_request, workspace, registry, ledger, client)
    assert result.accepted is False

    workspace.query_cards[0].query_kind = 'episode_search'
    _, _, result2, _ = execute_subject_search(EvidenceRequest(request_ref='ER1', request_type='subject_search', query_refs=['SQ1']), workspace, registry, ledger, client)
    assert result2.accepted is False


def test_budget_and_max_subjects_truncate_results_stably():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    ledger = BudgetLedger(CaseBudget(max_api_calls_per_case=1, max_subject_searches=1, max_new_subject_cards=1))
    client = FakeBangumiClient([
        SimpleNamespace(id=11, type=2, name='Anime A'),
        SimpleNamespace(id=12, type=2, name='Anime B'),
    ])
    request = EvidenceRequest(request_ref='ER1', request_type='subject_search', query_refs=['SQ1'], max_subjects=2)

    subjects, _, result, next_budget = execute_subject_search(request, workspace, registry, ledger, client)

    assert len(subjects) == 1
    assert result.response_refs == ['BS1']
    assert next_budget.budget.used_new_subject_cards == 0
