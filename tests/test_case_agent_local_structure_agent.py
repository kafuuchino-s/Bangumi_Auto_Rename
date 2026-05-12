from src.rename.case_agent.local_structure_agent import call_local_structure_agent, fallback_local_structure_spans
from src.rename.case_agent.models import (
    CaseBudget,
    CaseContract,
    CaseDossier,
    CaseHeader,
    LocalFileCard,
    LocalStructureOutput,
    LocalStructureSpanSpec,
    VisibleRefCatalog,
)


class FakeLocalStructureClient:
    def __init__(self, output):
        self.outputs = list(output) if isinstance(output, list) else [output]
        self.prompts = []

    def call_local_structure_agent(self, prompt, schema):
        self.prompts.append(prompt)
        if len(self.outputs) > 1:
            return self.outputs.pop(0)
        return self.outputs[0]


def _dossier():
    files = [
        LocalFileCard(ref=f'LF{i}', path=f'[VCB-Studio] Yamada-kun to 7-nin no Majo [{i:02d}][Ma10p_1080p].mkv', is_main=True, file_kind='video')
        for i in range(1, 13)
    ]
    files.extend([
        LocalFileCard(ref='LF13', path='[VCB-Studio] Yamada-kun to 7-nin no Majo [OAD1][Hi10p_720p].mkv', is_main=True, file_kind='video'),
        LocalFileCard(ref='LF14', path='[VCB-Studio] Yamada-kun to 7-nin no Majo [OAD2][Hi10p_720p].mkv', is_main=True, file_kind='video'),
    ])
    main_refs = [card.ref for card in files]
    return CaseDossier(
        header=CaseHeader(case_id='CASE-LS-AI'),
        budget=CaseBudget(),
        visible_refs=VisibleRefCatalog(local_file_refs=main_refs),
        local_files=files,
        contract=CaseContract(main_file_refs=main_refs, allowed_file_refs=main_refs),
    )


def test_local_structure_agent_materializes_ai_span_plan_without_mechanical_token_guessing():
    dossier = _dossier()
    client = FakeLocalStructureClient(LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=[f'LF{i}' for i in range(1, 15)], ordering_basis='path_order', reason='whole package'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='token_segment', file_refs=[f'LF{i}' for i in range(1, 13)], ordinal_start=1, ordinal_end=12, ordinal_count=12, ordering_basis='filename_ordinal_order', reason='visible [01]-[12] release ordinals; title 7-nin ignored'),
        LocalStructureSpanSpec(span_ref='LS2', span_scope='token_segment', file_refs=['LF13', 'LF14'], ordinal_start=1, ordinal_end=2, ordinal_count=2, ordering_basis='filename_ordinal_order', reason='visible [OAD1]-[OAD2] release ordinals'),
    ]))

    result = call_local_structure_agent(client, dossier)

    assert result.ok is True
    assert [card.ref for card in result.local_span_cards] == ['LS_PACKAGE', 'LS1', 'LS2']
    assert result.local_span_cards[1].file_refs == [f'LF{i}' for i in range(1, 13)]
    assert result.local_span_cards[1].episode_token_start == 1
    assert result.local_span_cards[1].episode_token_end == 12
    assert result.local_span_cards[2].file_refs == ['LF13', 'LF14']
    assert result.request_audit is not None
    assert result.request_audit['span_count'] == 3


def test_local_structure_agent_rejects_missing_coverage_and_uses_raw_shell_fallback():
    dossier = _dossier()
    client = FakeLocalStructureClient(LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS1', span_scope='token_segment', file_refs=['LF1'], ordinal_start=1, ordinal_end=1, ordinal_count=1, ordering_basis='filename_ordinal_order'),
    ]))

    result = call_local_structure_agent(client, dossier)

    assert result.ok is True
    assert [card.ref for card in result.local_span_cards] == ['LS_PACKAGE', 'LS1']
    assert result.local_span_cards[1].span_scope == 'unpartitioned'
    assert result.request_audit is not None
    assert result.request_audit['fallback_used'] is True
    assert any(str(issue).startswith('missing_main_refs:') for issue in result.request_audit['validation_issues'])
    assert result.request_audit['repair_attempted'] is True


def test_local_structure_agent_repairs_invalid_first_output_before_fallback():
    dossier = _dossier()
    invalid = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS1', span_scope='token_segment', file_refs=['LF1'], ordinal_start=1, ordinal_end=1, ordinal_count=1, ordering_basis='filename_ordinal_order'),
    ])
    repaired = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=[f'LF{i}' for i in range(1, 15)], ordering_basis='path_order', reason='whole package'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='token_segment', file_refs=[f'LF{i}' for i in range(1, 13)], ordinal_start=1, ordinal_end=12, ordinal_count=12, ordering_basis='filename_ordinal_order', reason='visible [01]-[12] release ordinals; title 7-nin ignored'),
        LocalStructureSpanSpec(span_ref='LS2', span_scope='token_segment', file_refs=['LF13', 'LF14'], ordinal_start=1, ordinal_end=2, ordinal_count=2, ordering_basis='filename_ordinal_order', reason='visible [OAD1]-[OAD2] release ordinals'),
    ])
    client = FakeLocalStructureClient([invalid, repaired])

    result = call_local_structure_agent(client, dossier)

    assert result.ok is True
    assert [card.ref for card in result.local_span_cards] == ['LS_PACKAGE', 'LS1', 'LS2']
    assert result.local_span_cards[1].file_refs == [f'LF{i}' for i in range(1, 13)]
    assert result.local_span_cards[2].file_refs == ['LF13', 'LF14']
    assert len(client.prompts) == 2
    assert 'repair_context' in client.prompts[1]
    assert result.request_audit is not None
    assert result.request_audit['repair_attempted'] is True
    assert result.request_audit['repair_succeeded'] is True
    assert 'fallback_used' not in result.request_audit


def test_local_structure_agent_repairs_single_span_that_mixes_numbered_and_oad_markers():
    dossier = _dossier()
    invalid = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=[f'LF{i}' for i in range(1, 15)], ordering_basis='path_order'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='unpartitioned', file_refs=[f'LF{i}' for i in range(1, 15)], ordering_basis='path_order'),
    ])
    repaired = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=[f'LF{i}' for i in range(1, 15)], ordering_basis='path_order'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='token_segment', file_refs=[f'LF{i}' for i in range(1, 13)], ordinal_start=1, ordinal_end=12, ordinal_count=12, ordering_basis='filename_ordinal_order'),
        LocalStructureSpanSpec(span_ref='LS2', span_scope='token_segment', file_refs=['LF13', 'LF14'], ordinal_start=1, ordinal_end=2, ordinal_count=2, ordering_basis='filename_ordinal_order'),
    ])
    client = FakeLocalStructureClient([invalid, repaired])

    result = call_local_structure_agent(client, dossier)

    assert [card.ref for card in result.local_span_cards] == ['LS_PACKAGE', 'LS1', 'LS2']
    assert len(client.prompts) == 2
    assert result.request_audit is not None
    assert result.request_audit['repair_succeeded'] is True
    assert any(
        str(issue).startswith('mixed_raw_special_marker_and_other_refs_in_child_span:')
        for issue in result.request_audit['initial_validation_issues']
    )


def test_local_structure_agent_repairs_volume_span_that_mixes_special_title_and_numbered_refs():
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-LS-SPECIAL-TITLE'),
        budget=CaseBudget(),
        visible_refs=VisibleRefCatalog(local_file_refs=['LF1', 'LF2', 'LF3']),
        local_files=[
            LocalFileCard(ref='LF1', path='Vol.6/[KTXP][Mushishi Tokubetsu Hen_Suzu no Shizuku][BDrip].mkv', parent_display='Vol.6', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF2', path='Vol.6/[KTXP][Mushishi Zoku Shou][19][BDrip].mkv', parent_display='Vol.6', is_main=True, file_kind='video'),
            LocalFileCard(ref='LF3', path='Vol.6/[KTXP][Mushishi Zoku Shou][20][BDrip].mkv', parent_display='Vol.6', is_main=True, file_kind='video'),
        ],
        contract=CaseContract(main_file_refs=['LF1', 'LF2', 'LF3'], allowed_file_refs=['LF1', 'LF2', 'LF3']),
    )
    invalid = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=['LF1', 'LF2', 'LF3'], ordering_basis='path_order'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='directory', file_refs=['LF1', 'LF2', 'LF3'], ordinal_start=19, ordinal_end=20, ordinal_count=2, ordering_basis='filename_ordinal_order'),
    ])
    repaired = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=['LF1', 'LF2', 'LF3'], ordering_basis='path_order'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='residual', file_refs=['LF1'], ordering_basis='path_order', reason='special-title singleton'),
        LocalStructureSpanSpec(span_ref='LS2', span_scope='token_segment', file_refs=['LF2', 'LF3'], ordinal_start=19, ordinal_end=20, ordinal_count=2, ordering_basis='filename_ordinal_order'),
    ])
    client = FakeLocalStructureClient([invalid, repaired])

    result = call_local_structure_agent(client, dossier)

    assert [card.file_refs for card in result.local_span_cards if card.ref != 'LS_PACKAGE'] == [['LF1'], ['LF2', 'LF3']]
    assert result.request_audit is not None
    assert result.request_audit['repair_succeeded'] is True
    assert any(
        str(issue).startswith('mixed_raw_special_marker_and_other_refs_in_child_span:')
        for issue in result.request_audit['initial_validation_issues']
    )


def test_local_structure_agent_repairs_coarse_cross_volume_numbered_span():
    files = [
        *[
            LocalFileCard(ref=f'LF{i}', path=f'Vol.1/[KTXP][Mushishi Zoku Shou][{i:02d}][BDrip].mkv', parent_display='Vol.1', is_main=True, file_kind='video')
            for i in range(1, 5)
        ],
        *[
            LocalFileCard(ref=f'LF{i}', path=f'Vol.2/[KTXP][Mushishi Zoku Shou][{i:02d}][BDrip].mkv', parent_display='Vol.2', is_main=True, file_kind='video')
            for i in range(5, 9)
        ],
    ]
    main_refs = [card.ref for card in files]
    dossier = CaseDossier(
        header=CaseHeader(case_id='CASE-LS-CROSS-VOLUME'),
        budget=CaseBudget(),
        visible_refs=VisibleRefCatalog(local_file_refs=main_refs),
        local_files=files,
        contract=CaseContract(main_file_refs=main_refs, allowed_file_refs=main_refs),
    )
    invalid = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=main_refs, ordering_basis='path_order'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='directory', file_refs=main_refs, ordinal_start=1, ordinal_end=8, ordinal_count=8, ordering_basis='filename_ordinal_order'),
    ])
    repaired = LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=main_refs, ordering_basis='path_order'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='directory', file_refs=[f'LF{i}' for i in range(1, 5)], ordinal_start=1, ordinal_end=4, ordinal_count=4, ordering_basis='filename_ordinal_order'),
        LocalStructureSpanSpec(span_ref='LS2', span_scope='directory', file_refs=[f'LF{i}' for i in range(5, 9)], ordinal_start=5, ordinal_end=8, ordinal_count=4, ordering_basis='filename_ordinal_order'),
    ])
    client = FakeLocalStructureClient([invalid, repaired])

    result = call_local_structure_agent(client, dossier)

    assert [card.file_refs for card in result.local_span_cards if card.ref != 'LS_PACKAGE'] == [
        [f'LF{i}' for i in range(1, 5)],
        [f'LF{i}' for i in range(5, 9)],
    ]
    assert result.request_audit is not None
    assert result.request_audit['repair_succeeded'] is True
    assert any(
        str(issue).startswith('coarse_cross_parent_numbered_span:')
        for issue in result.request_audit['initial_validation_issues']
    )


def test_fallback_local_structure_spans_do_not_infer_ordinals():
    dossier = _dossier()
    spans = fallback_local_structure_spans(dossier)

    assert [card.ref for card in spans] == ['LS_PACKAGE', 'LS1']
    assert spans[1].span_scope == 'unpartitioned'
    assert spans[1].episode_token_count == 0
    assert spans[1].episode_token_start is None


def test_local_structure_marker_audit_does_not_treat_title_internal_numbers_as_numbered():
    dossier = _dossier()
    client = FakeLocalStructureClient(LocalStructureOutput(spans=[
        LocalStructureSpanSpec(span_ref='LS_PACKAGE', span_scope='package', file_refs=[f'LF{i}' for i in range(1, 15)], ordering_basis='path_order', reason='whole package'),
        LocalStructureSpanSpec(span_ref='LS1', span_scope='token_segment', file_refs=[f'LF{i}' for i in range(1, 13)], ordinal_start=1, ordinal_end=12, ordinal_count=12, ordering_basis='filename_ordinal_order', reason='visible [01]-[12] release ordinals; title 7-nin ignored'),
        LocalStructureSpanSpec(span_ref='LS2', span_scope='token_segment', file_refs=['LF13', 'LF14'], ordinal_start=1, ordinal_end=2, ordinal_count=2, ordering_basis='filename_ordinal_order', reason='visible [OAD1]-[OAD2] release ordinals'),
    ]))

    result = call_local_structure_agent(client, dossier)

    assert result.request_audit is not None
    prompt = client.prompts[0]
    assert '"NUMBERED"' not in prompt
    assert '"OAD"' in prompt
    assert any('title 7-nin ignored' in fact for fact in result.local_span_cards[1].confidence_facts)
