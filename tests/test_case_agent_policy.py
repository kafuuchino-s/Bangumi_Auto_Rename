from src.rename.case_agent.policy import build_action_policy, normalize_fail_closed


def test_policy_normalize_fail_closed_helper():
    assert normalize_fail_closed(final_request_evidence=True, prior_evidence=True, exhausted=True, final_opportunity=False)
    assert not normalize_fail_closed(final_request_evidence=True, prior_evidence=False, exhausted=True, final_opportunity=False)


def test_policy_recommends_fail_closed_when_final_and_no_more_requests():
    policy = build_action_policy(has_evidence=True, can_request_more=False, final_opportunity=True)
    assert policy.recommended_next_action == 'fail_closed'
    assert 'request_evidence' not in policy.allowed_actions
