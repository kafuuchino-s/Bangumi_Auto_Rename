from __future__ import annotations

from dataclasses import dataclass

from src.rename.case_agent.broker_budget import BudgetLedger
from src.rename.case_agent.broker_registry import EvidenceCardRegistry
from src.rename.case_agent.broker_related import execute_related_expansion
from src.rename.case_agent.models import BangumiSubjectCard, CaseBudget, CaseHeader, EvidenceRequest
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


@dataclass
class FakeRelation:
    id: int
    type: int = 2
    relation: str = ''


@dataclass
class FakeSubject:
    id: int
    type: int = 2
    name: str = ''
    name_cn: str = ''
    date: str = ''
    summary: str = ''
    platform: str = ''
    eps: int = 0
    total_episodes: int = 0


class FakeClient:
    def __init__(self, relations: list[FakeRelation], subjects: dict[int, FakeSubject]):
        self.relations = relations
        self.subjects = subjects
        self.calls: list[tuple[str, int]] = []

    def get_related_subjects(self, subject_id: int):
        self.calls.append(('related', subject_id))
        return self.relations

    def get_subject(self, subject_id: int):
        self.calls.append(('subject', subject_id))
        return self.subjects.get(subject_id)


def _workspace(subjects: list[BangumiSubjectCard] | None = None, budget: CaseBudget | None = None):
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='case-1'),
        budget=budget or CaseBudget(max_api_calls_per_case=10, max_new_subject_cards=10),
        bangumi_subjects=subjects or [BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime')],
    )


def test_allowed_anime_relation_creates_cards():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    client = FakeClient([FakeRelation(2, 2, '续集')], {2: FakeSubject(2, 2, 'n', 'cn')})
    request = EvidenceRequest(request_ref='REQ1', request_type='related_expansion', subject_refs=['BS1'])

    subjects, relations, provenance, result, next_budget = execute_related_expansion(
        request, workspace, registry, BudgetLedger(workspace.budget), client
    )

    assert len(subjects) == 1
    assert len(relations) == 1
    assert len(provenance) == 1
    assert result.accepted is True
    assert subjects[0].source_role == 'related'
    assert relations[0].provenance_ref == provenance[0].ref
    assert next_budget.budget.used_api_calls == 2
    assert next_budget.budget.used_new_subject_cards == 1


def test_disallowed_relation_skipped():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    client = FakeClient([FakeRelation(2, 2, '未知关系')], {2: FakeSubject(2, 2)})
    request = EvidenceRequest(request_ref='REQ1', request_type='related_expansion', subject_refs=['BS1'])

    subjects, relations, provenance, result, _ = execute_related_expansion(request, workspace, registry, BudgetLedger(workspace.budget), client)

    assert subjects == [] and relations == [] and provenance == []
    assert any('disallowed' in note for note in result.notes)


def test_non_anime_skipped():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    client = FakeClient([FakeRelation(2, 1, '续集')], {2: FakeSubject(2, 1)})
    request = EvidenceRequest(request_ref='REQ1', request_type='related_expansion', subject_refs=['BS1'])

    subjects, relations, provenance, result, _ = execute_related_expansion(request, workspace, registry, BudgetLedger(workspace.budget), client)

    assert subjects == [] and relations == [] and provenance == []
    assert any('non-anime' in note for note in result.notes)


def test_duplicate_existing_subject_reuses_bs_but_relation_created():
    workspace = _workspace([
        BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime'),
        BangumiSubjectCard(ref='BS2', subject_id=2, subject_type='anime'),
    ])
    registry = EvidenceCardRegistry.from_workspace(workspace)
    client = FakeClient([FakeRelation(2, 2, 'prequel')], {2: FakeSubject(2, 2)})
    request = EvidenceRequest(request_ref='REQ1', request_type='related_expansion', subject_refs=['BS1'])

    subjects, relations, provenance, result, next_budget = execute_related_expansion(request, workspace, registry, BudgetLedger(workspace.budget), client)

    assert len(subjects) == 0
    assert len(relations) == 1
    assert len(provenance) == 1
    assert result.accepted is True
    assert next_budget.budget.used_new_subject_cards == 0


def test_max_subjects_and_budget_enforced():
    workspace = _workspace(budget=CaseBudget(max_api_calls_per_case=5, max_new_subject_cards=1))
    registry = EvidenceCardRegistry.from_workspace(workspace)
    client = FakeClient([
        FakeRelation(2, 2, '续集'),
        FakeRelation(3, 2, '前传'),
    ], {2: FakeSubject(2, 2), 3: FakeSubject(3, 2)})
    request = EvidenceRequest(request_ref='REQ1', request_type='related_expansion', subject_refs=['BS1'], max_subjects=1)

    subjects, relations, provenance, result, next_budget = execute_related_expansion(request, workspace, registry, BudgetLedger(workspace.budget), client)

    assert len(subjects) == 1
    assert len(relations) == 1
    assert len(provenance) == 1
    assert next_budget.budget.used_new_subject_cards == 1
    assert next_budget.budget.used_new_subject_cards == 1


def test_invisible_anchor_rejected_before_api_call():
    workspace = _workspace()
    registry = EvidenceCardRegistry.from_workspace(workspace)
    client = FakeClient([FakeRelation(2, 2, '续集')], {2: FakeSubject(2, 2)})
    request = EvidenceRequest(request_ref='REQ1', request_type='related_expansion', subject_refs=['BS999'])

    subjects, relations, provenance, result, _ = execute_related_expansion(request, workspace, registry, BudgetLedger(workspace.budget), client)

    assert subjects == [] and relations == [] and provenance == []
    assert client.calls == []
    assert result.accepted is False
