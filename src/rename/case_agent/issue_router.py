from __future__ import annotations

from collections import Counter
from typing import Any

from .models import VerifierIssue


def route_verifier_issues(issues: list[VerifierIssue]) -> dict[str, Any]:
    counts = Counter(issue.issue_code for issue in issues if getattr(issue, 'issue_code', ''))
    samples = {}
    for code in ('invalid_target', 'duplicate_target', 'coverage_error', 'missing_support', 'unknown_ref'):
        code_items = [issue for issue in issues if issue.issue_code == code]
        samples[code] = {
            'count': len(code_items),
            'range': [code_items[0].ref, code_items[-1].ref] if code_items else [],
            'sample_messages': [issue.message for issue in code_items[:3]],
        }
    return {
        'remediation_instructions': {
            'invalid_target': 'compact invalid target remediation',
            'duplicate_target': 'compact duplicate target remediation',
            'coverage_error': 'compact coverage remediation',
            'missing_support': 'compact support remediation',
            'unknown_ref': 'compact unknown ref remediation',
        },
        'counts': dict(counts),
        'samples': samples,
        'no_full_dump': True,
        'no_mapping_fix': True,
    }
