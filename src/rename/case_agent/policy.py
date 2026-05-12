from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


Action = Literal['request_evidence', 'submit_verdict', 'fail_closed', 'issue_response']


@dataclass(frozen=True)
class PolicyDecision:
    allowed_actions: list[Action]
    disallowed_actions: list[Action]
    final_opportunity: bool
    budget: dict[str, Any]
    recommended_next_action: Action


def normalize_fail_closed(*, final_request_evidence: bool, prior_evidence: bool, exhausted: bool, final_opportunity: bool) -> bool:
    return bool(final_request_evidence and prior_evidence and (exhausted or final_opportunity))


def build_action_policy(*, has_evidence: bool, can_request_more: bool, final_opportunity: bool, budget: dict[str, Any] | None = None) -> PolicyDecision:
    budget = dict(budget or {})
    allowed: list[Action] = ['submit_verdict', 'fail_closed']
    if can_request_more:
        allowed.insert(0, 'request_evidence')
    if has_evidence and not final_opportunity:
        allowed.append('issue_response')
    disallowed = [action for action in ('request_evidence', 'submit_verdict', 'fail_closed', 'issue_response') if action not in allowed]
    recommended = 'fail_closed' if (final_opportunity and not can_request_more) else ('request_evidence' if can_request_more else 'submit_verdict')
    return PolicyDecision(allowed_actions=allowed, disallowed_actions=disallowed, final_opportunity=final_opportunity, budget=budget, recommended_next_action=recommended)
