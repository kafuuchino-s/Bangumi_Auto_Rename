from src.rename.case_agent.dossier import build_case_dossier
from src.rename.case_agent.models import CaseBudget, CaseContract, CaseHeader, LocalFileCard
from src.rename.case_agent.span_builder import build_local_span_cards, compact_span_card


def _make_file(ref: str, path: str, label: str = 'unknown', parent_display: str = '', is_main: bool = True):
    return LocalFileCard(ref=ref, path=path, label=label, parent_display=parent_display, is_main=is_main)


def test_build_local_span_cards_returns_raw_shell_without_mechanical_token_inference():
    local_files = [
        _make_file(
            f'LF{i:03d}',
            f'C:/show/[VCB-Studio] Yamada-kun to 7-nin no Majo [{i:02d}][Ma10p_1080p].mkv',
            label=f'[{i:02d}]',
        )
        for i in range(1, 25)
    ]
    dossier = build_case_dossier(CaseHeader(case_id='C1'), CaseBudget(), local_files, [], [], [], [], [], [], [], contract=CaseContract(main_file_refs=[card.ref for card in local_files]))

    spans = build_local_span_cards(dossier)

    assert [span.ref for span in spans] == ['LS_PACKAGE', 'LS1']
    assert spans[0].span_scope == 'package'
    assert spans[1].span_scope == 'unpartitioned'
    assert spans[1].file_refs == [card.ref for card in local_files]
    assert spans[1].ordering_basis == 'path_order'
    assert spans[1].episode_token_count == 0
    assert spans[1].episode_token_start is None
    assert spans[1].episode_token_end is None


def test_build_local_span_cards_below_min_count_returns_empty():
    local_files = [_make_file(f'LF{i:03d}', f'C:/show/Episode {i:03d}.mkv') for i in range(1, 24)]
    dossier = build_case_dossier(CaseHeader(case_id='C2'), CaseBudget(), local_files, [], [], [], [], [], [], [], contract=CaseContract(main_file_refs=[card.ref for card in local_files]))
    assert build_local_span_cards(dossier) == []


def test_compact_span_card_excludes_full_file_refs():
    local_files = [_make_file(f'LF{i:03d}', f'C:/show/Episode {i:03d}.mkv', label=str(i)) for i in range(1, 25)]
    dossier = build_case_dossier(CaseHeader(case_id='C4'), CaseBudget(), local_files, [], [], [], [], [], [], [], contract=CaseContract(main_file_refs=[card.ref for card in local_files]))
    span = build_local_span_cards(dossier)[0]
    compact = compact_span_card(span)
    assert 'file_refs' not in compact
    assert compact['file_ref_count'] == 24
