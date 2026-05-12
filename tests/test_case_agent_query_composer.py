from src.rename.case_agent.models import CaseBudget, CaseContract, CaseHeader, LocalFileCard, QueryCandidate, QueryCard, QueryComposerOutput
from src.rename.case_agent.query_composer import call_query_composer, render_query_composer_prompt
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class FakeQueryComposerClient:
    def __init__(self, output):
        self.outputs = list(output) if isinstance(output, list) else [output]
        self.prompts = []

    def call_query_composer(self, prompt, schema):
        self.prompts.append(prompt)
        if len(self.outputs) > 1:
            return self.outputs.pop(0)
        return self.outputs[0]


def test_query_composer_materializes_agent_composed_qc_cards():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-QC-1'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='[LoliHouse] Inugami-san to Nekoyama-san [BDRip 1080p FLAC].mkv', is_main=True)],
        query_cards=[QueryCard(ref='SQ1', query_text='[LoliHouse] Inugami-san to Nekoyama-san [BDRip 1080p FLAC]', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1'])],
    )
    client = FakeQueryComposerClient(QueryComposerOutput(queries=[
        QueryCandidate(
            query_text='Inugami-san to Nekoyama-san',
            source_refs=['LF1', 'SQ1'],
            included_terms=['Inugami-san to Nekoyama-san'],
            ignored_terms=['LoliHouse', 'BDRip', '1080p', 'FLAC'],
            reason='clean visible title candidate',
            confidence='high',
        )
    ]))

    result = call_query_composer(client, workspace.to_dossier(round_context='query_composer'))

    assert result.ok is True
    assert len(result.query_cards) == 1
    assert result.query_cards[0].ref == 'QC1'
    assert result.query_cards[0].query_origin == 'agent_composed'
    assert result.query_cards[0].query_text == 'Inugami-san to Nekoyama-san'
    assert result.query_cards[0].ignored_terms == ['LoliHouse', 'BDRip', '1080p', 'FLAC']
    assert result.request_audit is not None
    assert result.request_audit['composed_query_count'] == 1


def test_query_composer_rejects_hidden_source_refs():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-QC-2'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Show.mkv', is_main=True)],
    )
    client = FakeQueryComposerClient(QueryComposerOutput(queries=[
        QueryCandidate(query_text='Show', source_refs=['LF999'], reason='hidden ref', confidence='low')
    ]))

    result = call_query_composer(client, workspace.to_dossier(round_context='query_composer'))

    assert result.ok is True
    assert result.query_cards == []
    assert result.request_audit is not None
    assert result.request_audit['dropped_query_count'] == 1
    assert result.request_audit['dropped_query_reasons'] == ['no_visible_source_refs:Show']


def test_query_composer_repairs_scope_hints_instead_of_rewriting_queries():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-QC-SCOPE'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Yamada-kun to 7-nin no Majo OAD1.mkv', is_main=True)],
        query_cards=[
            QueryCard(
                ref='SQ1',
                query_text='Yamada-kun to 7-nin no Majo [Japanese title]',
                query_kind='subject_search',
                query_origin='local_raw',
                source_refs=['LF1'],
            )
        ],
    )
    invalid = QueryComposerOutput(queries=[
        QueryCandidate(
            query_text='Yamada-kun to 7-nin no Majo [Japanese title]',
            source_refs=['LF1', 'SQ1'],
            reason='two title variants squeezed into one search query',
            confidence='high',
        ),
        QueryCandidate(
            query_text='Yamada-kun to 7-nin no Majo OAD',
            source_refs=['LF1'],
            reason='scope hint should not remain in subject query',
            confidence='medium',
        ),
        QueryCandidate(
            query_text='Yamada-kun to 7-nin no Majo 2015',
            source_refs=['LF1'],
            reason='year hint should not remain in subject query',
            confidence='medium',
        ),
        QueryCandidate(
            query_text='OAD',
            source_refs=['LF1'],
            reason='metadata only',
            confidence='low',
        ),
    ])
    repaired = QueryComposerOutput(queries=[
        QueryCandidate(
            query_text='Yamada-kun to 7-nin no Majo',
            source_refs=['LF1', 'SQ1'],
            included_terms=['Yamada-kun to 7-nin no Majo'],
            ignored_terms=['OAD', '2015'],
            reason='clean work-title query',
            confidence='high',
        ),
        QueryCandidate(
            query_text='Japanese title',
            source_refs=['LF1', 'SQ1'],
            included_terms=['Japanese title'],
            ignored_terms=['OAD', '2015'],
            reason='alternate visible title query',
            confidence='high',
        ),
    ])
    client = FakeQueryComposerClient([invalid, repaired])

    result = call_query_composer(client, workspace.to_dossier(round_context='query_composer'))

    assert result.ok is True
    assert [card.query_text for card in result.query_cards] == [
        'Yamada-kun to 7-nin no Majo',
        'Japanese title',
    ]
    assert result.query_cards[0].ignored_terms == ['OAD', '2015']
    assert result.request_audit is not None
    assert result.request_audit['repair_attempted'] is True
    assert result.request_audit['repair_succeeded'] is True
    assert any(
        str(reason).startswith(('query_text_has_scope_or_year_suffix:', 'query_text_mixes_bracketed_title_variants:'))
        for reason in result.request_audit['initial_dropped_query_reasons']
    )
    assert 'repair_context' in client.prompts[1]


def test_query_composer_accepts_title_preserving_alternate_language_hypotheses():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-QC-ALT-TITLE'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='Yamada-kun to 7-nin no Majo 01.mkv', is_main=True)],
        query_cards=[QueryCard(ref='SQ1', query_text='Yamada-kun to 7-nin no Majo', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1'])],
    )
    client = FakeQueryComposerClient(QueryComposerOutput(queries=[
        QueryCandidate(
            query_text='Japanese title',
            source_refs=['LF1', 'SQ1'],
            included_terms=['Yamada-kun', '7-nin', 'Majo'],
            reason='title-preserving Japanese query hypothesis from visible romanized title',
            confidence='medium',
        ),
        QueryCandidate(
            query_text='Japanese title 2015',
            source_refs=['LF1', 'SQ1'],
            reason='year should be ignored as search scope',
            confidence='medium',
        ),
    ]))

    result = call_query_composer(client, workspace.to_dossier(round_context='query_composer'))

    assert result.ok is True
    assert [card.query_text for card in result.query_cards] == ['Japanese title']
    assert result.query_cards[0].ignored_terms == []
    assert result.request_audit is not None
    assert any(
        str(reason).startswith('query_text_has_scope_or_year_suffix:')
        for reason in result.request_audit['dropped_query_reasons']
    )


def test_query_composer_prompt_includes_raw_material_but_no_search_results():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-QC-3'),
        budget=CaseBudget(),
        local_files=[LocalFileCard(ref='LF1', path='[KTXP] Mushishi Zoku Shou [BDRip 1080p FLAC].mkv', is_main=True)],
        query_cards=[QueryCard(ref='SQ1', query_text='[KTXP] Mushishi Zoku Shou [BDRip 1080p FLAC]', query_kind='subject_search', query_origin='local_raw', source_refs=['LF1'])],
    )

    prompt = render_query_composer_prompt(workspace.to_dossier(round_context='query_composer'))

    assert 'raw_query_material' in prompt
    assert '[KTXP] Mushishi Zoku Shou' in prompt
    assert 'QueryComposerOutput' in prompt
    assert 'search Bangumi' in prompt
