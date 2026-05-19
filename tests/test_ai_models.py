from src.ai.client import AIClient
from src.ai.models import AIProposalCriticResult, SemanticReviewFinding
from src.ai.openai_client import OpenAIClient
from src.config.config_manager import cm

def test_ai_proposal_critic_result_requires_findings():
    try:
        AIProposalCriticResult.model_validate(
            {
                'semantic_status': 'pass',
                'confidence': 'High',
                'reason': 'ok',
                'repair_suggestion': None,
            }
        )
        assert False, 'expected validation error'
    except Exception as exc:
        assert 'Field required' in str(exc)


def test_semantic_review_finding_requires_all_ref_fields_and_nullable_repair_suggestion():
    try:
        SemanticReviewFinding.model_validate(
            {
                'status': 'blocked',
                'issue_code': 'missing_refs',
                'file_refs': [],
                'evidence_refs': [],
                'reason': 'missing target refs',
                'repair_suggestion': None,
            }
        )
        assert False, 'expected validation error'
    except Exception as exc:
        assert 'Field required' in str(exc)


def test_ai_proposal_critic_result_allows_inconsistent_findings_and_keeps_top_level_gate():
    result_pass = AIProposalCriticResult.model_validate(
        {
            'semantic_status': 'pass',
            'confidence': 'High',
            'reason': 'ok',
            'repair_suggestion': None,
            'findings': [
                {
                    'status': 'blocked',
                    'issue_code': 'x',
                    'file_refs': [],
                    'target_refs': [],
                    'evidence_refs': [],
                    'reason': 'diagnostic only',
                    'repair_suggestion': None,
                }
            ],
        }
    )
    assert result_pass.semantic_status == 'pass'
    assert result_pass.findings[0].status == 'blocked'

    result_non_pass = AIProposalCriticResult.model_validate(
        {
            'semantic_status': 'invalid',
            'confidence': 'High',
            'reason': 'bad',
            'repair_suggestion': None,
            'findings': [
                {
                    'status': 'warning',
                    'issue_code': 'x',
                    'file_refs': [],
                    'target_refs': [],
                    'evidence_refs': [],
                    'reason': 'diagnostic only',
                    'repair_suggestion': None,
                }
            ],
        }
    )
    assert result_non_pass.semantic_status == 'invalid'
    assert result_non_pass.findings[0].status == 'warning'


def test_ai_response_cache_no_longer_writes_local_files(monkeypatch, tmp_path):
    calls = {'count': 0}

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def resolve_api_interface(self, value):
            return 'responses_api'

        def call_via_responses_api(self, request_params):
            calls['count'] += 1
            return {'content': '{"title":"cached"}'}

    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    client = AIClient()
    client._client = FakeAdapter()
    with cm.temporary_config({'ai_response_cache_enabled': True}):
        assert client._call_openai_simple('system', 'prompt', validation_key='title', streaming=False) == '{"title":"cached"}'
        assert client._call_openai_simple('system', 'prompt', validation_key='title', streaming=False) == '{"title":"cached"}'

    assert calls['count'] == 2
    assert not list(tmp_path.glob('*.json'))


def test_ai_response_cache_mode_does_not_enable_local_file_cache(monkeypatch, tmp_path):
    calls = {'count': 0}

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def resolve_api_interface(self, value):
            return 'responses_api'

        def call_via_responses_api(self, request_params):
            calls['count'] += 1
            return {'content': '{"title":"live"}'}

    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_MODE', 'off')
    client = AIClient()
    client._client = FakeAdapter()
    with cm.temporary_config({'ai_response_cache_enabled': False}):
        assert client._call_openai_simple('system', 'prompt', validation_key='title', streaming=False) == '{"title":"live"}'
        assert client._call_openai_simple('system', 'prompt', validation_key='title', streaming=False) == '{"title":"live"}'

    assert calls['count'] == 2
    assert not list(tmp_path.glob('*.json'))


def test_ai_client_responses_tool_agent_passes_input_and_tools(monkeypatch, tmp_path):
    calls = []

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def call_via_responses_api(self, request_params):
            calls.append(request_params)
            return {'id': 'resp_1', 'tool_calls': []}

    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_MODE', 'refresh')
    client = AIClient()
    client._client = FakeAdapter()

    result = client.call_responses_tool_agent(
        instructions='system',
        input_items=[{'role': 'user', 'content': 'state'}],
        tools=[{'type': 'function', 'function': {'name': 'do_it', 'parameters': {'type': 'object', 'properties': {}}}}],
        parallel_tool_calls=False,
    )

    assert result == {'id': 'resp_1', 'tool_calls': []}
    assert 'conversation' not in calls[0]
    assert calls[0]['responses_input'][0]['content'] == 'state'
    assert calls[0]['tools'][0]['function']['name'] == 'do_it'
    assert calls[0]['tool_choice'] == 'required'
    assert calls[0]['parallel_tool_calls'] is False


def test_ai_client_responses_tool_agent_passes_function_tool_choice(monkeypatch, tmp_path):
    calls = []

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def call_via_responses_api(self, request_params):
            calls.append(request_params)
            return {'id': 'resp_1', 'tool_calls': []}

    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    client = AIClient()
    client._client = FakeAdapter()
    choice = {'type': 'function', 'function': {'name': 'submit'}}

    result = client.call_responses_tool_agent(
        instructions='system',
        input_items=[{'role': 'user', 'content': 'state'}],
        tools=[{'type': 'function', 'function': {'name': 'submit', 'parameters': {'type': 'object', 'properties': {}}}}],
        tool_choice=choice,
    )

    assert result == {'id': 'resp_1', 'tool_calls': []}
    assert calls[0]['tool_choice'] == choice


def test_ai_client_responses_tool_agent_passes_conversation_id(monkeypatch, tmp_path):
    calls = []

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def call_via_responses_api(self, request_params):
            calls.append(request_params)
            return {'id': 'resp_1', 'tool_calls': []}

    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_MODE', 'refresh')
    client = AIClient()
    client._client = FakeAdapter()

    result = client.call_responses_tool_agent(
        instructions='system',
        input_items=[{'role': 'user', 'content': 'state'}],
        tools=[{'type': 'function', 'function': {'name': 'do_it', 'parameters': {'type': 'object', 'properties': {}}}}],
        conversation_id='conv_123',
    )

    assert result == {'id': 'resp_1', 'tool_calls': []}
    assert calls[0]['conversation'] == 'conv_123'


def test_ai_client_responses_tool_agent_passes_prompt_cache_key_without_session_headers(monkeypatch, tmp_path):
    calls = []

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def call_via_responses_api(self, request_params):
            calls.append(request_params)
            return {'id': 'resp_1', 'tool_calls': []}

    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_MODE', 'refresh')
    client = AIClient()
    client._client = FakeAdapter()

    result = client.call_responses_tool_agent(
        instructions='system',
        input_items=[{'role': 'user', 'content': 'state'}],
        tools=[{'type': 'function', 'function': {'name': 'do_it', 'parameters': {'type': 'object', 'properties': {}}}}],
        prompt_cache_key='cache_case_1',
    )

    assert result == {'id': 'resp_1', 'tool_calls': []}
    assert calls[0]['prompt_cache_key'] == 'cache_case_1'
    assert 'session_id' not in calls[0]
    assert calls[0]['prompt_cache_retention'] == '24h'


def test_responses_tool_agent_does_not_write_local_response_cache(monkeypatch, tmp_path):
    calls = {'count': 0}

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def call_via_responses_api(self, request_params):
            calls['count'] += 1
            return {
                'id': f'resp_{calls["count"]}',
                'tool_calls': [
                    {
                        'call_id': 'call_1',
                        'name': 'do_it',
                        'arguments': '{}',
                    }
                ],
            }

    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_MODE', 'read-write')
    client = AIClient()
    client._client = FakeAdapter()
    kwargs = {
        'instructions': 'system',
        'input_items': [{'role': 'user', 'content': 'state'}],
        'tools': [{'type': 'function', 'function': {'name': 'do_it', 'parameters': {'type': 'object', 'properties': {}}}}],
        'prompt_cache_key': 'cache_case_1',
    }

    with cm.temporary_config({'ai_response_cache_enabled': True}):
        first = client.call_responses_tool_agent(**kwargs)
        assert client._last_tool_agent_cache_event == 'not_applicable'
        second = client.call_responses_tool_agent(**kwargs)
        assert client._last_tool_agent_cache_event == 'not_applicable'

    assert first != second
    assert calls['count'] == 2
    assert not list(tmp_path.glob('*.json'))


def test_responses_tool_agent_cache_only_mode_still_calls_provider(monkeypatch, tmp_path):
    calls = {'count': 0}

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def call_via_responses_api(self, request_params):
            calls['count'] += 1
            return {'id': 'resp_live', 'tool_calls': []}

    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_MODE', 'cache-only')
    client = AIClient()
    client._client = FakeAdapter()

    with cm.temporary_config({'ai_response_cache_enabled': True}):
        result = client.call_responses_tool_agent(
            instructions='system',
            input_items=[{'role': 'user', 'content': 'state'}],
            tools=[{'type': 'function', 'function': {'name': 'do_it', 'parameters': {'type': 'object', 'properties': {}}}}],
        )

    assert result == {'id': 'resp_live', 'tool_calls': []}
    assert calls['count'] == 1
    assert client._last_tool_agent_cache_event == 'not_applicable'
    assert not list(tmp_path.glob('*.json'))


def test_responses_tool_agent_retries_transient_provider_errors(monkeypatch, tmp_path):
    calls = {'count': 0}

    class FakeAdapter:
        model = 'fake-model'
        temperature = 0.0
        client = object()

        def call_via_responses_api(self, request_params):
            calls['count'] += 1
            if calls['count'] < 3:
                raise RuntimeError('503 temporary unavailable')
            return {'id': 'resp_live', 'tool_calls': []}

    monkeypatch.setattr('src.ai.client.time.sleep', lambda _seconds: None)
    monkeypatch.setenv('BAR_AI_RESPONSE_CACHE_DIR', str(tmp_path))
    client = AIClient()
    client._client = FakeAdapter()

    result = client.call_responses_tool_agent(
        instructions='system',
        input_items=[{'role': 'user', 'content': 'state'}],
        tools=[{'type': 'function', 'function': {'name': 'do_it', 'parameters': {'type': 'object', 'properties': {}}}}],
        max_retries=2,
    )

    assert result == {'id': 'resp_live', 'tool_calls': []}
    assert calls['count'] == 3
    assert client._last_tool_agent_provider_retry_count == 2


def test_openai_client_ignores_session_id_and_does_not_set_sticky_headers(monkeypatch):
    captured = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured['kwargs'] = kwargs
            return type(
                'Response',
                (),
                {
                    'id': 'resp_1',
                    'usage': None,
                    'output': [],
                    'output_text': '',
                },
            )()

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            captured.setdefault('clients', []).append(kwargs)
            self.responses = FakeResponses()

    monkeypatch.setattr('src.ai.openai_client.OpenAI', FakeOpenAI)
    monkeypatch.setattr(cm, 'get_config', lambda key: {
        'ai_api_key': 'sk-test',
        'ai_base_url': 'https://example.test',
        'ai_model': 'fake-model',
        'ai_temperature': 0,
        'openai_output_format': 'structured_output',
        'openai_api_interface': 'responses_api',
        'openai_auto_routing_enabled': True,
    }.get(key))

    client = OpenAIClient()
    result = client.call_via_responses_api({
        'model': 'fake-model',
        'responses_input': 'hello',
        'session_id': 'session_case_1',
        'prompt_cache_key': 'cache_case_1',
    })

    assert result['id'] == 'resp_1'
    assert captured['kwargs']['prompt_cache_key'] == 'cache_case_1'
    assert captured['kwargs']['prompt_cache_retention'] == '24h'
    assert 'extra_headers' not in captured['kwargs']
    assert all('http_client' not in kwargs for kwargs in captured['clients'])


def test_openai_client_usage_includes_cached_input_tokens(monkeypatch):
    captured = {}

    class FakeUsageDetails:
        cached_tokens = 1234

    class FakeUsage:
        input_tokens = 2000
        output_tokens = 100
        total_tokens = 2100
        input_tokens_details = FakeUsageDetails()

    class FakeResponses:
        def create(self, **kwargs):
            captured['kwargs'] = kwargs
            return type(
                'Response',
                (),
                {
                    'id': 'resp_1',
                    'usage': FakeUsage(),
                    'output': [],
                    'output_text': '',
                },
            )()

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr('src.ai.openai_client.OpenAI', FakeOpenAI)
    monkeypatch.setattr(cm, 'get_config', lambda key: {
        'ai_api_key': 'sk-test',
        'ai_base_url': 'https://example.test',
        'ai_model': 'fake-model',
        'ai_temperature': 0,
        'openai_output_format': 'structured_output',
        'openai_api_interface': 'responses_api',
        'openai_auto_routing_enabled': True,
    }.get(key))

    client = OpenAIClient()
    result = client.call_via_responses_api({
        'model': 'fake-model',
        'responses_input': 'hello',
        'prompt_cache_retention': '24h',
    })

    assert result['usage']['input_tokens_details']['cached_tokens'] == 1234
    assert captured['kwargs']['prompt_cache_retention'] == '24h'


def test_openai_strict_schema_strips_defaults_and_marks_required_fields():
    schema = AIClient._build_openai_strict_schema(AIProposalCriticResult)

    assert schema['required'] == [
        'semantic_status',
        'confidence',
        'reason',
        'findings',
        'accepted_exceptions',
        'risk_notes',
        'repair_suggestion',
    ]

    properties = schema['properties']
    findings_schema = properties['findings']['items']
    assert findings_schema['$ref'] == '#/$defs/SemanticReviewFinding'

    finding_def = schema['$defs']['SemanticReviewFinding']
    assert finding_def['required'] == [
        'status',
        'issue_code',
        'file_refs',
        'target_refs',
        'evidence_refs',
        'reason',
        'repair_suggestion',
    ]
    assert finding_def['additionalProperties'] is False


def test_ai_proposal_critic_result_parses_findings_and_coerces_ref_lists():
    result = AIProposalCriticResult.model_validate(
        {
            'semantic_status': 'suspicious',
            'confidence': 'Medium',
            'reason': 'needs review',
            'repair_suggestion': None,
            'findings': [
                {
                    'status': 'blocked',
                    'issue_code': 'bridge_conflict',
                    'file_refs': 'F1',
                    'target_refs': ['T1'],
                    'evidence_refs': 'E1',
                    'reason': 'conflict',
                    'repair_suggestion': None,
                },
                {
                    'status': 'warning',
                    'issue_code': 'soft_note',
                    'file_refs': ['F2', 'F2'],
                    'target_refs': [],
                    'evidence_refs': ['E2', 'E3'],
                    'reason': 'note',
                    'repair_suggestion': 'review later',
                },
            ],
        }
    )

    assert result.findings[0].file_refs == ['F1']
    assert result.findings[0].evidence_refs == ['E1']
    assert result.findings[1].file_refs == ['F2']
    assert result.findings[1].evidence_refs == ['E2', 'E3']


def test_local_package_analysis_schema_exposes_projection_fields():
    schema = AIClient._build_openai_strict_schema(__import__('src.ai.models', fromlist=['LocalPackageAnalysis']).LocalPackageAnalysis)
    props = schema['properties']

    assert 'input_sufficiency' in props
    assert 'evidence_gaps' in props
    assert 'sample_refs_used' in props
    assert 'title_cue_confidence_reason' in props
    assert schema['additionalProperties'] is False
