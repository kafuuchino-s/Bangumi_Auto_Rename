from src.rename.case_agent.dossier import build_case_dossier
from src.rename.case_agent.local_structure_agent import call_local_structure_agent
from src.rename.case_agent.models import CaseBudget, CaseContract, CaseHeader, LocalFileCard, LocalStructureOutput, LocalStructureSpanSpec
from src.rename.case_agent.span_builder import build_local_span_cards


class FakeLocalStructureClient:
    def __init__(self, output):
        self.output = output

    def call_local_structure_agent(self, prompt, schema):
        return self.output


def _make_file(ref: str, path: str, *, parent_display: str = '', label: str = '') -> LocalFileCard:
    return LocalFileCard(ref=ref, path=path, is_main=True, parent_display=parent_display, label=label or path.rsplit('\\', 1)[-1], file_kind='video')


def test_compat_local_span_builder_does_not_partition_by_filename_tokens():
    header = CaseHeader(case_id='CASE-RAW', max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = []
    main_refs = []
    for idx in range(1, 25):
        ref = f'LF{idx:03d}'
        main_refs.append(ref)
        local_files.append(_make_file(ref, f'C:/show/E{idx:02d}.mkv', parent_display='Show A', label=f'E{idx:02d}'))

    spans = build_local_span_cards(build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], contract=CaseContract(main_file_refs=main_refs)), min_count=2)

    assert [span.ref for span in spans] == ['LS_PACKAGE', 'LS1']
    assert spans[1].span_scope == 'unpartitioned'
    assert spans[1].file_refs == main_refs
    assert spans[1].episode_token_count == 0


def test_local_structure_agent_owns_partitioning_and_ordinal_candidates():
    header = CaseHeader(case_id='CASE-LSA', max_rounds=3)
    budget = CaseBudget(max_judge_rounds=3)
    local_files = []
    main_refs = []
    for idx in range(1, 13):
        ref = f'LF{idx:03d}'
        main_refs.append(ref)
        local_files.append(_make_file(ref, f'C:/show/A/E{idx:02d}.mkv', parent_display='Show A', label=f'E{idx:02d}'))
    for idx in range(13, 25):
        ref = f'LF{idx:03d}'
        main_refs.append(ref)
        local_files.append(_make_file(ref, f'C:/show/B/OAD{idx - 12}.mkv', parent_display='Show B', label=f'OAD{idx - 12}'))

    dossier = build_case_dossier(header, budget, local_files, [], [], [], [], [], [], [], contract=CaseContract(main_file_refs=main_refs, allowed_file_refs=main_refs))
    output = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=main_refs),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='token_segment', file_refs=main_refs[:12], ordinal_start=1, ordinal_end=12, ordinal_count=12, ordering_basis='filename_ordinal_order'),
        LocalStructureSpanSpec(span_ref='LS2', span_scope='token_segment', file_refs=main_refs[12:], ordinal_start=1, ordinal_end=12, ordinal_count=12, ordering_basis='filename_ordinal_order'),
    ])

    result = call_local_structure_agent(FakeLocalStructureClient(output), dossier)

    assert [span.ref for span in result.local_span_cards] == ['LS_PACKAGE', 'LS1', 'LS2']
    assert result.local_span_cards[1].file_refs == main_refs[:12]
    assert result.local_span_cards[1].episode_token_start == 1
    assert result.local_span_cards[2].file_refs == main_refs[12:]
    assert result.local_span_cards[2].episode_token_start == 1
    assert [ref for span in result.local_span_cards[1:] for ref in span.file_refs] == main_refs
