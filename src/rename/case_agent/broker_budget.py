from __future__ import annotations

from dataclasses import dataclass

from .models import CaseBudget


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class BudgetLedger:
    budget: CaseBudget

    def _consume(self, field: str, used_field: str, n: int) -> 'BudgetLedger':
        if n < 0:
            raise ValueError('n must be non-negative')
        budget = self.budget.model_copy(deep=True)
        limit = getattr(budget, field)
        used = getattr(budget, used_field)
        if limit and used + n > limit:
            raise BudgetExceeded(f'{used_field} would exceed budget')
        setattr(budget, used_field, used + n)
        return BudgetLedger(budget=budget)

    def can_consume_api_calls(self, n: int) -> bool:
        return self.budget.max_api_calls_per_case == 0 or self.budget.used_api_calls + n <= self.budget.max_api_calls_per_case

    def consume_api_calls(self, n: int) -> 'BudgetLedger':
        return self._consume('max_api_calls_per_case', 'used_api_calls', n)

    def can_add_subject_cards(self, n: int) -> bool:
        return self.budget.max_new_subject_cards == 0 or self.budget.used_new_subject_cards + n <= self.budget.max_new_subject_cards

    def add_subject_cards(self, n: int) -> 'BudgetLedger':
        return self._consume('max_new_subject_cards', 'used_new_subject_cards', n)

    def can_add_episode_cards(self, n: int) -> bool:
        return self.budget.max_new_episode_cards == 0 or self.budget.used_new_episode_cards + n <= self.budget.max_new_episode_cards

    def add_episode_cards(self, n: int) -> 'BudgetLedger':
        return self._consume('max_new_episode_cards', 'used_new_episode_cards', n)

    def can_use_subject_search(self, n: int = 1) -> bool:
        return self.budget.max_subject_searches == 0 or self.budget.used_subject_searches + n <= self.budget.max_subject_searches

    def use_subject_search(self, n: int = 1) -> 'BudgetLedger':
        return self._consume('max_subject_searches', 'used_subject_searches', n)

    def can_add_evidence_batch(self) -> bool:
        return self.budget.max_evidence_batches == 0 or self.budget.used_evidence_batches + 1 <= self.budget.max_evidence_batches

    def add_evidence_batch(self) -> 'BudgetLedger':
        return self._consume('max_evidence_batches', 'used_evidence_batches', 1)

    def can_use_issue_response(self) -> bool:
        return self.budget.max_issue_response_rounds == 0 or self.budget.used_issue_response_rounds + 1 <= self.budget.max_issue_response_rounds

    def use_issue_response(self) -> 'BudgetLedger':
        return self._consume('max_issue_response_rounds', 'used_issue_response_rounds', 1)

    def to_budget(self) -> CaseBudget:
        return self.budget
