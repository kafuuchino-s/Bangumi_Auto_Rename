from src.rename.case_agent.dossier import build_bounded_case_dossier, build_case_dossier
from src.rename.case_agent.local_bangumi_entry import _build_workspace
from src.rename.case_agent.models import CaseBudget, CaseHeader, LocalFileCard
from src.rename.case_agent.span_builder import build_local_span_cards
from src.rename.case_agent.prompting import render_local_bangumi_judge_prompt


def _make_file(ref: str, idx: int) -> LocalFileCard:
    return LocalFileCard(ref=ref, path=f'C:/show/Episode {idx:03d}.mkv', is_main=True, parent_display=f'[{idx:03d}] Show', label=str(idx), file_kind='video')


def test_build_case_dossier_does_not_implicitly_infer_local_span_cards():
    header = CaseHeader(case_id='CASE-LS', max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = [_make_file(f'LF{i:03d}', i) for i in range(1, 109)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], [])

    assert dossier.local_span_cards == []

    bounded = build_bounded_case_dossier(dossier)
    assert bounded.local_span_cards == []


def test_prompt_projection_includes_compact_local_span_cards_without_full_refs():
    header = CaseHeader(case_id='CASE-LS2', max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = [_make_file(f'LF{i:03d}', i) for i in range(1, 109)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], [])
    dossier.local_span_cards = build_local_span_cards(dossier)

    prompt = render_local_bangumi_judge_prompt(dossier, round_kind='initial')

    assert 'local_span_cards' in prompt
    assert 'count' in prompt or 'file_ref_count' in prompt
    assert '"file_refs":' not in prompt


def test_workspace_copy_and_evidence_updates_preserve_local_span_cards():
    header = CaseHeader(case_id='CASE-LS3', max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = [_make_file(f'LF{i:03d}', i) for i in range(1, 109)]
    workspace = build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], [])
    copied_workspace = _build_workspace(local_evidence=type('E', (), {'source_path': 'demo'})(), bangumi_contexts=[])
    object.__setattr__(copied_workspace, 'local_files', local_files)
    object.__setattr__(copied_workspace, 'contract', workspace.contract)
    object.__setattr__(copied_workspace, 'local_span_cards', build_local_span_cards(workspace))

    copied = copied_workspace.with_seen_detail_refs(['LF001'])
    updated = copied_workspace.with_added_evidence()

    assert copied.local_span_cards
    assert updated.local_span_cards


def test_assignment_menu_uses_non_package_spans():
    header = CaseHeader(case_id='CASE-LS4', max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = [_make_file(f'LF{i:03d}', i) for i in range(1, 109)]
    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], [])
    dossier.local_span_cards = build_local_span_cards(dossier)
    assert dossier.local_span_cards[0].span_scope == 'package'
    assert any(card.span_scope == 'unpartitioned' for card in dossier.local_span_cards[1:])
