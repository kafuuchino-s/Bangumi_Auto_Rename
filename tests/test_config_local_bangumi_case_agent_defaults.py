from src.config.config_manager import CONFIG_DEFAULT


def test_local_bangumi_case_agent_defaults_are_present():
    assert CONFIG_DEFAULT['rename_local_bangumi_case_agent_primary_enabled'] is True
    assert CONFIG_DEFAULT['rename_local_bangumi_case_agent_backend'] == 'pi'
    assert CONFIG_DEFAULT['rename_local_bangumi_case_agent_max_evidence_batches'] == 12
    assert CONFIG_DEFAULT['rename_local_bangumi_case_agent_max_issue_response_rounds'] == 1
    assert CONFIG_DEFAULT['rename_local_bangumi_case_agent_max_requests_per_batch'] == 8
    assert CONFIG_DEFAULT['rename_local_bangumi_pi_case_root'] == 'data/pi_case_agent'
    assert CONFIG_DEFAULT['rename_local_bangumi_pi_max_turns'] == 48
    assert CONFIG_DEFAULT['rename_local_bangumi_pi_timeout_seconds'] == 300
    assert CONFIG_DEFAULT['rename_local_bangumi_pi_command'] == ''
    assert CONFIG_DEFAULT['rename_local_bangumi_case_agent_snapshot_debug'] is False

    assert 'rename_llm_planning_view_enabled' not in CONFIG_DEFAULT
