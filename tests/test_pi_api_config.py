from src.ai.pi_api_config import (
    PI_API_ANTHROPIC_MESSAGES,
    PI_API_OPENAI_COMPLETIONS,
    PI_API_OPENAI_RESPONSES,
    healthcheck_api_label,
    pi_api_from_config,
    pi_provider_uses_bearer_auth,
)


def test_pi_api_from_config_mappings():
    assert pi_api_from_config('responses_api') == PI_API_OPENAI_RESPONSES
    assert pi_api_from_config('chat_completions') == PI_API_OPENAI_COMPLETIONS
    assert pi_api_from_config('anthropic_messages') == PI_API_ANTHROPIC_MESSAGES
    assert pi_api_from_config('ANTHROPIC-MESSAGES') == PI_API_ANTHROPIC_MESSAGES
    assert pi_api_from_config('') == PI_API_OPENAI_RESPONSES


def test_pi_provider_uses_bearer_auth():
    assert pi_provider_uses_bearer_auth(PI_API_OPENAI_RESPONSES) is True
    assert pi_provider_uses_bearer_auth(PI_API_OPENAI_COMPLETIONS) is True
    assert pi_provider_uses_bearer_auth(PI_API_ANTHROPIC_MESSAGES) is False


def test_healthcheck_api_label():
    assert healthcheck_api_label(PI_API_ANTHROPIC_MESSAGES) == 'Anthropic Messages'
    assert healthcheck_api_label(PI_API_OPENAI_COMPLETIONS) == 'Chat Completions'
    assert healthcheck_api_label(PI_API_OPENAI_RESPONSES) == 'Responses'


def test_pi_api_from_config_rejects_unknown_to_openai_responses():
    assert pi_api_from_config('totally_unknown') == PI_API_OPENAI_RESPONSES