from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.rename.case_agent.dossier import build_bounded_case_dossier, build_case_dossier, build_initial_compact_projection
from src.rename.case_agent.local_bangumi_entry import run_local_bangumi_case_agent_mapping
from src.rename.case_agent.models import BangumiGroupCard, BangumiItemCard, BangumiSubjectCard, CaseBudget, CaseHeader, LocalFileCard, QueryCard


def _make_local_files() -> list[LocalFileCard]:
    files: list[LocalFileCard] = []
    for i in range(1, 479):
        parent = f"[Snow-Raws] Sample Series 2 / Disc {((i - 1) // 40) + 1}"
        files.append(
            LocalFileCard(
                ref=f"LF{i}",
                path=f"root/{((i - 1) // 40) + 1}/ep{i:03d}.mkv",
                is_main=True,
                parent_display=parent,
                cluster_ref='LC1',
                label=f'ep{i:03d}',
                file_kind='video',
            )
        )
    return files


def _make_items() -> list[BangumiItemCard]:
    return [BangumiItemCard(ref=f"BE{i}", subject_ref='BS1', sort=i, ep=i) for i in range(1, 479)]


def _make_dossier():
    header = CaseHeader(case_id='CASE-LPA-PROJECTION', case_type='local_bangumi', round_index=1, max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3, max_evidence_batches=2, max_issue_response_rounds=1, max_requests_per_batch=8)
    local_files = _make_local_files()
    bangumi_subjects = [BangumiSubjectCard(ref='BS1', subject_id=1, subject_type='anime', title='Test Subject')]
    bangumi_groups = [BangumiGroupCard(ref='BR1', group_kind='season_group', member_refs_visible=[f'BE{i}' for i in range(1, 479)])]
    bangumi_items = _make_items()
    query_cards = [QueryCard(ref='SQ1', query_text='[Snow-Raws] Sample Series 2', query_kind='subject_search', source_refs=[f'LF{i}' for i in range(1, 479)])]
    dossier = build_case_dossier(header, budget, local_files, [], bangumi_subjects, [], bangumi_groups, bangumi_items, query_cards, [])
    return dossier, build_bounded_case_dossier(dossier)


def test_lpa_projection_is_compact_and_sampled() -> None:
    dossier, bounded = _make_dossier()
    projection = build_initial_compact_projection(bounded)

    payload = json.dumps(projection, ensure_ascii=False).encode('utf-8')
    assert len(payload) <= 45_000

    assert 'detailed_visible_cards' in projection
    assert 'detailed_local_file_cards' in projection
    assert len(projection['detailed_visible_cards']) <= 10
    assert len(projection['detailed_local_file_cards']) <= 10
    assert len(projection['query_card_sample']) <= 8
    assert len(projection['contract_overview']['main_file_ref_samples']) <= 8
    assert len(projection['catalog_summary']['target_ref_samples']) <= 8

    # No full file rows or path lists leak into the projection.
    assert all('path' not in card or card['path'] for card in projection['detailed_visible_cards'])
    assert all('path' not in card or card['path'] for card in projection['detailed_local_file_cards'])
    assert all('source_refs' not in card for card in projection['query_card_sample'])
    assert projection['contract_overview']['main_file_count'] == 478
    assert projection['catalog_summary']['target_ref_count'] == 478

    # Representative samples should cover the series, not full lists.
    assert 'LF1' in projection['contract_overview']['main_file_ref_samples']
    assert 'LF478' in projection['contract_overview']['main_file_ref_samples']
    assert any(ref in projection['query_card_sample'][0]['source_ref_samples'] for ref in ['LF1', 'LF239', 'LF478'])

    # Release group must remain separate from primary title cue extraction.
    ai_client = SimpleNamespace(
        analyze_local_package=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('LPA should not run')),
        get_last_ai_call_audit=lambda: {'call_name': 'LocalPackageAnalysis', 'unexpected': True},
    )

    def fake_run_pi_case_agent(*, workspace, bangumi_client, source_path):
        from src.rename.case_agent.pi_runner import PiCaseAgentRunResult
        from src.rename.case_agent.models import CaseJudgeOutput, CaseVerifierResult

        final_output = CaseJudgeOutput(action='fail_closed', summary='projection test')
        return PiCaseAgentRunResult(
            ok=True,
            status='fail_closed',
            case_id=workspace.header.case_id,
            summary='projection test',
            final_action='fail_closed',
            final_workspace=workspace,
            run_dir=Path('tests/tmp/pi-run'),
            judge_outputs=[final_output],
            final_output=final_output,
            final_verifier_result=CaseVerifierResult(passed=True, issues=[]),
        )

    # Verify the mapping entry leaves query/title semantics to the Pi runtime.
    from src.rename.case_agent import local_bangumi_entry as lbe

    original = lbe.run_pi_case_agent
    try:
        lbe.run_pi_case_agent = fake_run_pi_case_agent
        result = run_local_bangumi_case_agent_mapping(
            local_evidence=SimpleNamespace(
                source_path='tests/sample',
                files=[SimpleNamespace(file_id='x1', name='ep001.mkv', relative_path='root/ep001.mkv', is_main_video_candidate=True)],
            ),
            bangumi_contexts=[],
            ai_client=ai_client,
            source_path='tests/sample',
        )
    finally:
        lbe.run_pi_case_agent = original

    audit = result['snapshot']['local_package_analysis_audit']
    assert audit['skipped'] is True
    assert audit['reason'] == 'pi_case_agent_mapping_only_path'
    assert result['snapshot']['primary_title_cues']
    assert '[Snow-Raws]' not in result['snapshot']['primary_title_cues'][0]


def test_lpa_audit_fields_exist_in_fallback_snapshot() -> None:
    _, bounded = _make_dossier()
    projection = build_initial_compact_projection(bounded)
    assert projection['round_budget']['max_judge_rounds'] == 3
    assert 'episode' not in projection['contract_overview']['coverage_rule'].lower()
    assert projection['budget']['max_requests_per_batch'] == 8
