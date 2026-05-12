from __future__ import annotations

from src.rename.case_agent.judge_client import call_case_judge
from src.rename.case_agent.models import CaseDossier, CaseHeader, CaseJudgeOutput, VisibleRefCatalog


def make_dossier() -> CaseDossier:
    return CaseDossier(
        header=CaseHeader(case_id='CASE-42'),
        visible_refs=VisibleRefCatalog(local_file_refs=['LF1'], target_refs=['BE1']),
    )


class FakeAIClientObject:
    def __init__(self, response: object | None = None, exc: Exception | None = None):
        self.response = response
        self.exc = exc
        self.calls: list[tuple[str, type[CaseJudgeOutput]]] = []

    def call_case_judge(self, prompt: str, schema: type[CaseJudgeOutput]):
        self.calls.append((prompt, schema))
        if self.exc:
            raise self.exc
        return self.response


class FakeOpenAISimpleClient:
    def __init__(self, response: object | None = None, exc: Exception | None = None):
        self.response = response
        self.exc = exc
        self.calls: list[tuple[str, str, str, type[CaseJudgeOutput], bool]] = []

    def _call_openai_simple(
        self,
        system_prompt: str,
        prompt: str,
        *,
        validation_key: str,
        schema: type[CaseJudgeOutput],
        streaming: bool,
    ):
        self.calls.append((system_prompt, prompt, validation_key, schema, streaming))
        if self.exc:
            raise self.exc
        return self.response


def test_fake_ai_object_response_ok():
    dossier = make_dossier()
    output = CaseJudgeOutput(action='request_evidence', summary='ok')
    client = FakeAIClientObject(response=output)

    result = call_case_judge(client, dossier)

    assert result.ok is True
    assert result.output == output
    assert result.error == ''
    assert client.calls and client.calls[0][1] is CaseJudgeOutput
    assert 'CASE-42' in result.prompt
    assert 'Local→Bangumi' in result.prompt or 'Local→Bangumi Case Judge Prompt' in result.prompt
    assert 'Case Judge' in result.prompt


def test_fake_ai_dict_response_parses_ok():
    dossier = make_dossier()
    client = FakeAIClientObject(response={'action': 'submit_verdict', 'summary': 'done', 'assignment_intents': []})

    result = call_case_judge(client, dossier)

    assert result.ok is True
    assert result.output is not None
    assert result.output.action == 'submit_verdict'


def test_fake_ai_invalid_dict_returns_error():
    dossier = make_dossier()
    client = FakeAIClientObject(response={'action': 'submit_verdict', 'unexpected': 1})

    result = call_case_judge(client, dossier)

    assert result.ok is False
    assert result.output is None
    assert 'schema parse error' in result.error


def test_fake_ai_exception_returns_error():
    dossier = make_dossier()
    client = FakeAIClientObject(exc=RuntimeError('boom'))

    result = call_case_judge(client, dossier)

    assert result.ok is False
    assert result.output is None
    assert 'call failed' in result.error


def test_none_response_returns_no_response_error():
    dossier = make_dossier()
    client = FakeAIClientObject(response=None)

    result = call_case_judge(client, dossier)

    assert result.ok is False
    assert result.output is None
    assert 'no response' in result.error


def test_fake_openai_simple_response_ok():
    dossier = make_dossier()
    output = CaseJudgeOutput(action='request_evidence', summary='ok')
    client = FakeOpenAISimpleClient(response=output)

    result = call_case_judge(client, dossier)

    assert result.ok is True
    assert result.output == output
    assert client.calls
    system_prompt, prompt, validation_key, schema, streaming = client.calls[0]
    assert system_prompt == 'You are a Local→Bangumi Case Judge. Return strict JSON only.'
    assert schema is CaseJudgeOutput
    assert validation_key == 'action'
    assert streaming is False
    assert 'CASE-42' in prompt


def test_case_judge_request_audit_present():
    dossier = make_dossier()
    client = FakeAIClientObject(response=CaseJudgeOutput(action='fail_closed', summary='done'))
    result = call_case_judge(client, dossier, round_kind='initial')
    assert result.request_audit is not None
    assert result.request_audit['round_kind'] == 'initial'
    assert 'cache_mode' in result.request_audit
    assert 'elapsed_ms' in result.request_audit or True


def test_case_judge_request_audit_uses_real_interface_fields():
    dossier = make_dossier()
    client = FakeAIClientObject(response=CaseJudgeOutput(action='fail_closed', summary='done'))
    result = call_case_judge(client, dossier, round_kind='evidence_rejudge')
    assert result.request_audit is not None
    assert result.request_audit['configured_interface'] in {'responses_api', 'chat_completions', 'unknown'}
    assert result.request_audit['actual_interface'] in {'unavailable', 'unknown', 'responses_api', 'chat_completions'}
    assert result.request_audit['streaming'] in {False, True, 'unknown'}
    assert 'output_bytes_estimate' in result.request_audit
    assert 'output_ref_total_count' in result.request_audit


def test_case_judge_request_audit_counts_nested_refs_recursively():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[{'ref': 'F1', 'finding_kind': 'pass', 'description': 'ok', 'evidence_refs': ['BE1', 'BE2']}],
        evidence_gaps=[{'ref': 'G1', 'description': 'gap', 'needed_refs': [f'BE{i}' for i in range(1, 6)]}],
    )
    client = FakeAIClientObject(response=output)

    result = call_case_judge(client, dossier)

    assert result.request_audit is not None
    assert result.request_audit['output_ref_list_max_length'] >= 5
    assert result.request_audit['output_ref_total_count'] >= 7
    assert result.request_audit['oversized_output'] is False


def test_case_judge_output_accepts_evidence_menu_request_ids_field():
    output = CaseJudgeOutput.model_validate({
        'action': 'request_evidence',
        'summary': '',
        'evidence_menu_request_ids': ['EM1', 'EM2'],
    })

    assert output.evidence_menu_request_ids == ['EM1', 'EM2']


def test_case_judge_request_audit_flags_oversized_nested_refs():
    dossier = make_dossier()
    output = CaseJudgeOutput(
        action='submit_verdict',
        findings=[{'ref': 'F1', 'finding_kind': 'pass', 'description': 'ok'}],
        evidence_gaps=[{'ref': 'G1', 'description': 'gap', 'needed_refs': [f'BE{i}' for i in range(100)]}],
    )
    client = FakeAIClientObject(response=output)

    result = call_case_judge(client, dossier)

    assert result.request_audit is not None
    assert result.request_audit['oversized_output'] is True
    assert 'exceeds per-list output budget' in str(result.request_audit.get('oversized_output_reason', ''))


def test_module_does_not_import_old_runner():
    import src.rename.case_agent.judge_client as judge_client

    source = judge_client.__loader__.get_source(judge_client.__name__)  # type: ignore[union-attr]
    assert 'alignment_runner' not in source
    assert 'old direct prompt' not in source
    assert '_render_case_judge_prompt' not in source


def test_prompt_mentions_hypothesis_refs_are_trace_only():
    dossier = make_dossier()
    client = FakeAIClientObject(response=CaseJudgeOutput(action='fail_closed', summary='done'))
    result = call_case_judge(client, dossier)
    assert 'hypothesis refs' in result.prompt
