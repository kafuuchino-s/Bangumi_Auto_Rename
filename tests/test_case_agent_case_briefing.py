from __future__ import annotations

import json

from src.rename.case_agent.case_briefing_agent import call_case_briefing_agent, fallback_case_briefing, render_case_briefing_prompt
from src.rename.case_agent.models import (
    CaseBriefingOutput,
    CaseBriefingWorkUnit,
    CaseBudget,
    CaseContract,
    CaseHeader,
    LocalFileCard,
    LocalSpanCard,
)
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class _BriefingClient:
    def __init__(self, output):
        self.output = output
        self.prompts: list[str] = []

    def call_case_briefing_agent(self, prompt: str, schema):
        self.prompts.append(prompt)
        return self.output


class _SimpleBriefingClient:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def _call_openai_simple(self, system_prompt: str, prompt: str, **kwargs):
        self.calls.append(dict(kwargs))
        return json.dumps(
            {
                'package_shape': 'singleton_special',
                'work_units': [
                    {
                        'work_unit_ref': 'WU1',
                        'label': 'unit',
                        'unit_kind': 'special',
                        'local_refs': ['LS1'],
                        'file_refs': ['LF1'],
                        'span_refs': ['LS1'],
                        'title_hints': [],
                        'source_form_hints': [],
                        'status': 'open',
                        'reason': '',
                    }
                ],
                'title_hypotheses': [],
                'split_hints': [],
                'evidence_questions': [],
                'summary': 'ok',
            }
        )


def _dossier():
    ws = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='CASE-BRIEF'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1'], allowed_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='pkg/Title OVA.mkv', is_main=True)],
        local_span_cards=[LocalSpanCard(ref='LS1', span_scope='residual', file_refs=['LF1'], file_ref_count=1, title_cues=['Title'])],
    )
    return ws.to_dossier(round_context='case_briefing')


def test_case_briefing_prompt_contains_local_span_cards():
    prompt = render_case_briefing_prompt(_dossier())

    assert 'CaseBriefingOutput' in prompt
    assert 'LS1' in prompt
    assert 'Title OVA.mkv' in prompt


def test_case_briefing_agent_accepts_visible_refs():
    output = CaseBriefingOutput(
        package_shape='singleton_special',
        work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', local_refs=['LS1'], file_refs=['LF1'], span_refs=['LS1'])],
    )
    client = _BriefingClient(output)

    result = call_case_briefing_agent(client, _dossier())

    assert result.ok is True
    assert result.output is not None
    assert result.output.work_units[0].work_unit_ref == 'WU1'
    assert result.request_audit is not None
    assert result.request_audit['work_unit_count'] == 1


def test_case_briefing_simple_transport_uses_real_schema_validation_key():
    client = _SimpleBriefingClient()

    result = call_case_briefing_agent(client, _dossier())

    assert result.ok is True
    assert client.calls[0]['validation_key'] == 'work_units'


def test_case_briefing_agent_falls_back_on_hidden_refs():
    output = CaseBriefingOutput(
        package_shape='bad',
        work_units=[CaseBriefingWorkUnit(work_unit_ref='WU1', local_refs=['LF404'])],
    )
    client = _BriefingClient(output)

    result = call_case_briefing_agent(client, _dossier())

    assert result.ok is True
    assert result.output is not None
    assert result.output.summary.startswith('case briefing hidden refs rejected')
    assert result.request_audit is not None
    assert result.request_audit['fallback_used'] is True


def test_fallback_case_briefing_uses_local_spans_as_work_units():
    briefing = fallback_case_briefing(_dossier(), reason='unit test')

    assert briefing.work_units[0].span_refs == ['LS1']
    assert briefing.title_hypotheses[0].title == 'Title'
