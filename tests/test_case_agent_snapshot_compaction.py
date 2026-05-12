from __future__ import annotations

from src.rename.case_agent.audit import summarize_case_agent_snapshot_refs


def test_snapshot_compaction_keeps_counts_ranges_and_samples_only() -> None:
    summary = summarize_case_agent_snapshot_refs({
        'contract_main_file_refs': [f'LF{i}' for i in range(1, 109)],
        'final_output_main_file_refs': [f'LF{i}' for i in range(1, 109)],
        'visible_target_refs': [f'BE{i}' for i in range(1, 13)],
        'query_card_sample': [
            {'ref': 'SQ4', 'query_text': 'q', 'source_refs': [f'F{i}' for i in range(1, 479)]},
        ],
    })

    assert summary['contract_main_file_count'] == 108
    assert summary['final_output_main_file_count'] == 108
    assert summary['visible_target_count'] == 12
    assert summary['contract_main_file_range'] == 'LF1..LF108'
    assert summary['final_output_main_file_range'] == 'LF1..LF108'
    assert summary['visible_target_range'] == 'BE1..BE12'
    assert summary['contract_main_file_samples'] == ['LF1', 'LF2', 'LF3', 'LF4', 'LF5', 'LF6', 'LF7', 'LF8']
    assert summary['final_output_main_file_samples'] == ['LF1', 'LF2', 'LF3', 'LF4', 'LF5', 'LF6', 'LF7', 'LF8']
    assert summary['visible_target_samples'] == ['BE1', 'BE2', 'BE3', 'BE4', 'BE5', 'BE6', 'BE7', 'BE8']
    assert summary['query_card_sample'][0]['source_ref_count'] == 478
    assert summary['query_card_sample'][0]['source_ref_range'] == 'F1..F478'
    assert summary['query_card_sample'][0]['source_ref_samples'] == ['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8']
    assert 'source_refs' not in summary['query_card_sample'][0]
