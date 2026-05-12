from src.rename.case_agent.issue_router import route_verifier_issues
from src.rename.case_agent.models import VerifierIssue


def test_issue_router_produces_compact_remediation():
    routed = route_verifier_issues([
        VerifierIssue(ref='I1', issue_code='invalid_target', message='bad target'),
        VerifierIssue(ref='I2', issue_code='duplicate_target', message='dup target'),
    ])
    assert routed['no_full_dump'] is True
    assert routed['no_mapping_fix'] is True
    assert routed['samples']['invalid_target']['count'] == 1
