from src.rename.case_agent.audit import summarize_case_agent_snapshot_refs


def test_accounted_for_snapshot_fields_are_present_and_default_to_zero():
    summary = summarize_case_agent_snapshot_refs({})

    assert summary['main_file_count'] == 0
    assert summary['mapped_file_count'] == 0
    assert summary['excluded_file_count'] == 0
    assert summary['needs_more_evidence_file_count'] == 0
    assert summary['unaligned_file_count'] == 0
    assert summary['open_file_count'] == 0
    assert summary['accounted_for_count'] == 0
    assert summary['unresolved_count'] == 0
    assert summary['accepted_accounting_ready'] is False


def test_accounted_for_snapshot_fields_passthrough_counts():
    summary = summarize_case_agent_snapshot_refs({
        'main_file_count': 3,
        'mapped_file_count': 2,
        'excluded_file_count': 1,
        'needs_more_evidence_file_count': 0,
        'unaligned_file_count': 0,
        'open_file_count': 0,
        'accounted_for_count': 3,
        'unresolved_count': 0,
        'accepted_accounting_ready': True,
    })

    assert summary['main_file_count'] == 3
    assert summary['mapped_file_count'] == 2
    assert summary['accounted_for_count'] == 3
    assert summary['accepted_accounting_ready'] is True
