from src.rename.case_agent.broker_budget import BudgetExceeded, BudgetLedger
from src.rename.case_agent.models import CaseBudget


def _ledger():
    return BudgetLedger(CaseBudget(max_api_calls_per_case=2, max_new_subject_cards=1, max_new_episode_cards=1, max_subject_searches=1, max_evidence_batches=1, max_issue_response_rounds=1))


def test_budget_consume_updates_immutably():
    ledger = _ledger()
    next_ledger = ledger.consume_api_calls(2)
    assert ledger.budget.used_api_calls == 0
    assert next_ledger.budget.used_api_calls == 2
    assert next_ledger.to_budget().used_api_calls == 2


def test_budget_exceeded_raises():
    ledger = _ledger().consume_api_calls(2)
    try:
        ledger.consume_api_calls(1)
        assert False, 'expected budget exceeded'
    except BudgetExceeded:
        pass


def test_counters_increment_for_batches_and_issue_response():
    ledger = _ledger()
    ledger = ledger.add_evidence_batch()
    ledger = ledger.use_issue_response()
    ledger = ledger.add_subject_cards(1)
    ledger = ledger.add_episode_cards(1)
    ledger = ledger.use_subject_search(1)
    assert ledger.budget.used_evidence_batches == 1
    assert ledger.budget.used_issue_response_rounds == 1
    assert ledger.budget.used_new_subject_cards == 1
    assert ledger.budget.used_new_episode_cards == 1
    assert ledger.budget.used_subject_searches == 1
