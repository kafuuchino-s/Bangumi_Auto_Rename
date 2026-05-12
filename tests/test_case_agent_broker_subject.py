from __future__ import annotations

from src.bangumi.models import BangumiSubject
from src.rename.case_agent.broker_budget import BudgetLedger
from src.rename.case_agent.broker_registry import EvidenceCardRegistry
from src.rename.case_agent.broker_subject import execute_subject_lookup
from src.rename.case_agent.models import BangumiSubjectCard, CaseBudget, CaseHeader, EvidenceRequest
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class FakeBangumiClient:
    def __init__(self, subject: BangumiSubject | None):
        self.subject = subject
        self.calls: list[int] = []

    def get_subject(self, subject_id: int):
        self.calls.append(subject_id)
        return self.subject


def _workspace(subject_ref: str = 'BS1', subject_id: int = 1):
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(),
        budget=CaseBudget(max_api_calls_per_case=3),
        bangumi_subjects=[BangumiSubjectCard(ref=subject_ref, subject_id=subject_id)],
    )


def test_visible_bs_lookup_accepted_and_enriched():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient(
        BangumiSubject(
            id=1,
            name='Original Name',
            name_cn='中文名',
            date='2024-01-01',
            summary='Long summary',
            platform='TV',
            eps=12,
            total_episodes=12,
            tags=['tag1'],
            infobox=[{'key': 'studio', 'value': 'A'}],
        )
    )
    request = EvidenceRequest(request_ref='REQ1', request_type='subject_lookup', subject_refs=['BS1'])

    subjects, provenance, result, next_budget = execute_subject_lookup(request, workspace, registry, budget, client)

    assert client.calls == [1]
    assert result.accepted is True
    assert subjects[0].ref == 'BS1'
    assert subjects[0].name == 'Original Name'
    assert subjects[0].name_cn == '中文名'
    assert subjects[0].summary_short == 'Long summary'
    assert subjects[0].provenance_ref.startswith('PV')
    assert provenance[0].source_operation == 'subject_lookup'
    assert provenance[0].parent_refs == ['BS1']
    assert next_budget.budget.used_api_calls == 1
    assert next_budget.budget.used_new_subject_cards == 0


def test_invisible_bs_rejected_before_api_call():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient(None)
    request = EvidenceRequest(request_ref='REQ1', request_type='subject_lookup', subject_refs=['BS999'])

    subjects, provenance, result, next_budget = execute_subject_lookup(request, workspace, registry, budget, client)

    assert client.calls == []
    assert subjects == []
    assert provenance == []
    assert result.accepted is False
    assert next_budget.budget.used_api_calls == 0


def test_api_none_handled_as_empty_error():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient(None)
    request = EvidenceRequest(request_ref='REQ1', request_type='subject_lookup', subject_refs=['BS1'])

    subjects, provenance, result, next_budget = execute_subject_lookup(request, workspace, registry, budget, client)

    assert client.calls == [1]
    assert subjects == []
    assert provenance == []
    assert result.accepted is False
    assert next_budget.budget.used_api_calls == 1


def test_provenance_created_and_budgets_consumed():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    budget = BudgetLedger(workspace.budget)
    client = FakeBangumiClient(BangumiSubject(id=1))
    request = EvidenceRequest(request_ref='REQ1', request_type='subject_lookup', subject_refs=['BS1'])

    _, provenance, _, next_budget = execute_subject_lookup(request, workspace, registry, budget, client)

    assert len(provenance) == 1
    assert provenance[0].ref.startswith('PV')
    assert next_budget.budget.used_api_calls == 1
    assert next_budget.budget.used_new_subject_cards == 0
