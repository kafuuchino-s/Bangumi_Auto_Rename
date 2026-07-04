"""Python OpenAIClient 与 Pi 协议配置 ``anthropic_messages`` 的边界单测。"""

from __future__ import annotations

import logging

from src.ai.openai_client import OpenAIClient
from src.config.config_manager import cm


def test_resolve_api_interface_recognizes_anthropic_messages():
    client = OpenAIClient.__new__(OpenAIClient)
    assert client._resolve_api_interface("anthropic_messages") == "anthropic_messages"
    assert client._resolve_api_interface("ANTHROPIC-MESSAGES") == "anthropic_messages"


def test_anthropic_messages_does_not_emit_unknown_interface_warning(caplog):
    with caplog.at_level(logging.WARNING):
        with cm.temporary_config(
            {
                "ai_api_key": "sk-test",
                "ai_base_url": "https://api.bbbc.eu.org",
                "ai_model": "grok-composer-2.5-fast",
                "openai_api_interface": "anthropic_messages",
            }
        ):
            client = OpenAIClient()

    assert client.api_interface == "anthropic_messages"
    assert not any("未知接口类型" in r.message for r in caplog.records)


def test_is_unavailable_when_anthropic_messages_configured():
    with cm.temporary_config(
        {
            "ai_api_key": "sk-test",
            "ai_base_url": "https://api.bbbc.eu.org",
            "ai_model": "grok-composer-2.5-fast",
            "openai_api_interface": "anthropic_messages",
        }
    ):
        client = OpenAIClient()

    assert client.api_interface == "anthropic_messages"
    assert client.is_available() is False