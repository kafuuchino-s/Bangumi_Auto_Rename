from __future__ import annotations

from types import SimpleNamespace

from src.rename.case_agent.local_bangumi_entry import _build_workspace, run_local_bangumi_case_agent_mapping


class _File:
    def __init__(
        self,
        file_id: str,
        name: str,
        relative_path: str,
        is_main_video_candidate: bool = True,
        is_video: bool = True,
        suffix: str = '.mkv',
    ):
        self.file_id = file_id
        self.name = name
        self.relative_path = relative_path
        self.is_main_video_candidate = is_main_video_candidate
        self.is_video = is_video
        self.suffix = suffix


def _local_evidence():
    return SimpleNamespace(source_path='tests/sample', files=[_File('f1', 'ep1.mkv', 'ep1.mkv'), _File('f2', 'ep2.mkv', 'ep2.mkv')])


def _bangumi_contexts():
    return [{
        'context': {
            'episode_structure': {
                'subject_id': 100,
                'title': 'Test Subject',
                'name': 'Test Subject',
                'name_cn': '测试条目',
                'source_role': 'source_cue',
                'episodes': [
                    {'episode_id': 1001, 'title': 'Episode 1', 'sort': 1, 'ep': 1, 'kind': 'regular'},
                    {'episode_id': 1002, 'title': 'Episode 2', 'sort': 2, 'ep': 2, 'kind': 'regular'},
                ],
            },
        },
    }]


def test_local_bangumi_workspace_extracts_visible_cards():
    workspace = _build_workspace(local_evidence=_local_evidence(), bangumi_contexts=_bangumi_contexts())

    assert len(workspace.bangumi_subjects) >= 1
    assert len(workspace.bangumi_groups) >= 1
    assert len(workspace.bangumi_items) >= 1
    assert workspace.contract.visible_target_refs == [card.ref for card in workspace.bangumi_items]
    assert workspace.contract.visible_target_refs == ['episode:1001', 'episode:1002']

    query_cards = [card for card in workspace.query_cards if card.ref.startswith('SQ')]
    assert query_cards
    assert all(card.query_text.strip() for card in query_cards)
    assert all(card.result_refs == [] for card in query_cards)


def test_local_bangumi_workspace_hard_filters_definite_noise_from_visible_universe():
    local_evidence = SimpleNamespace(source_path='tests/sample', files=[
        _File('f1', 'ep1.mkv', 'ep1.mkv'),
        _File('f2', 'NCOP.mkv', 'SPs/NCOP.mkv', is_main_video_candidate=False, is_video=True),
        _File('f3', 'PV01.mkv', 'PV01.mkv'),
        _File('f4', 'Menu.mkv', 'Menu/Menu.mkv'),
        _File('f5', 'scan.jpg', 'Scans/scan.jpg', is_main_video_candidate=False, is_video=False, suffix='.jpg'),
        _File('f6', 'IV02.mkv', 'SPs/[IV02].mkv'),
        _File('f7', 'Bonus01.mkv', 'Bonus01.mkv'),
    ])

    workspace = _build_workspace(local_evidence=local_evidence, bangumi_contexts=[])

    assert workspace.contract.main_file_refs == ['ep1.mkv']
    assert workspace.contract.supplemental_file_refs == []
    assert [card.path for card in workspace.local_files if card.is_main] == ['ep1.mkv']
    assert all('NCOP' not in card.path for card in workspace.local_files)
    assert all('PV01' not in card.path for card in workspace.local_files)
    assert all('Menu' not in card.path for card in workspace.local_files)
    assert all('IV02' not in card.path for card in workspace.local_files)
    assert all('Bonus01' not in card.path for card in workspace.local_files)
    assert workspace.judge_request_audits == []


def test_local_bangumi_workspace_keeps_mapping_relevant_specials_but_filters_recaps():
    local_evidence = SimpleNamespace(source_path='tests/sample', files=[
        _File('f1', 'show #00.mkv', 'show #00.mkv'),
        _File('f2', 'show #12DC.mkv', 'show #12DC.mkv'),
        _File('f3', 'show OVA.mkv', 'show OVA.mkv'),
        _File('f4', 'show SP.mkv', 'show SP.mkv'),
        _File('f5', 'show Movie.mkv', 'show Movie.mkv'),
        _File('f6', 'show Recap.mkv', 'show [S1 Recap].mkv'),
        _File('f7', 'show special dir.mkv', 'Specials/show special dir.mkv'),
    ])

    workspace = _build_workspace(local_evidence=local_evidence, bangumi_contexts=[])

    assert workspace.contract.main_file_refs == [
        'show #00.mkv',
        'show #12DC.mkv',
        'show OVA.mkv',
        'show SP.mkv',
        'show Movie.mkv',
    ]
    assert [card.path for card in workspace.local_files] == [
        'show #00.mkv',
        'show #12DC.mkv',
        'show OVA.mkv',
        'show SP.mkv',
        'show Movie.mkv',
    ]
    assert all('Recap' not in card.path for card in workspace.local_files)
    assert all('Specials/' not in card.path for card in workspace.local_files)


def test_local_bangumi_workspace_enforces_investigation_batch_floor(monkeypatch):
    monkeypatch.setattr(
        'src.rename.case_agent.local_bangumi_entry.cm.get_config',
        lambda key: 2 if key == 'rename_local_bangumi_case_agent_max_evidence_batches' else None,
    )

    workspace = _build_workspace(local_evidence=_local_evidence(), bangumi_contexts=[])

    assert workspace.budget.max_evidence_batches == 12


def test_local_bangumi_workspace_pi_native_mode_has_no_turn_cap(monkeypatch):
    monkeypatch.setattr(
        'src.rename.case_agent.local_bangumi_entry.cm.get_config',
        lambda key: 3 if key == 'rename_local_bangumi_pi_max_turns' else None,
    )

    workspace = _build_workspace(local_evidence=_local_evidence(), bangumi_contexts=[])

    assert workspace.header.max_rounds == 0
    assert workspace.budget.max_judge_rounds == 0


def test_local_bangumi_entry_uses_pi_backend_snapshot(monkeypatch, tmp_path):
    def fake_run_pi_case_agent(*, workspace, bangumi_client, source_path):
        from src.rename.case_agent.pi_runner import PiCaseAgentRunResult
        from src.rename.case_agent.models import CaseJudgeOutput, CaseVerifierResult

        final_output = CaseJudgeOutput(action='fail_closed', summary='fake pi handled it')
        return PiCaseAgentRunResult(
            ok=True,
            status='fail_closed',
            case_id=workspace.header.case_id,
            summary='fake pi handled it',
            final_action='fail_closed',
            final_workspace=workspace,
            run_dir=tmp_path / 'pi-run',
            judge_outputs=[final_output],
            final_output=final_output,
            final_verifier_result=CaseVerifierResult(passed=True, issues=[]),
            tool_trace=[{'tool': 'fail_closed', 'ok': True, 'elapsed_ms': 1, 'result_summary': {'status': 'fail_closed'}}],
            tool_call_counts={'fail_closed': 1},
            tool_sequence=['fail_closed'],
            pi_command='pi',
            runtime_command=['fake-pi'],
            runtime_returncode=0,
        )

    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.run_pi_case_agent', fake_run_pi_case_agent)
    monkeypatch.setattr('src.rename.case_agent.local_bangumi_entry.BangumiClient', lambda: object())

    result = run_local_bangumi_case_agent_mapping(
        local_evidence=_local_evidence(),
        bangumi_contexts=[],
        source_path='tests/sample',
    )

    assert result['ok'] is True
    assert result['status'] == 'fail_closed'
    assert result['snapshot']['case_agent_mode'] == 'pi_case_agent'
    assert result['snapshot']['mapping_only'] is True
    assert result['snapshot']['pi_tool_trace_count'] == 1
    assert result['snapshot']['pi_tool_call_counts'] == {'fail_closed': 1}
    assert result['snapshot']['case_judge_request_audits'][-1]['tool_name'] == 'fail_closed'
