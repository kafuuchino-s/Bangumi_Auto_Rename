"""Pi sidecar 模型协议：配置值 → pi-ai ``api`` 字段。

全项目 4 套 Case Agent 与 ``pi_healthcheck`` 共用本模块，避免
``pi_runner`` / ``pi_healthcheck`` 各写一份映射。
"""

from __future__ import annotations

# 配置页 ``openai_api_interface`` 可选值（历史键名保留，语义已覆盖 Anthropic）。
CONFIG_INTERFACE_RESPONSES = 'responses_api'
CONFIG_INTERFACE_CHAT = 'chat_completions'
CONFIG_INTERFACE_ANTHROPIC = 'anthropic_messages'

PI_API_OPENAI_RESPONSES = 'openai-responses'
PI_API_OPENAI_COMPLETIONS = 'openai-completions'
PI_API_ANTHROPIC_MESSAGES = 'anthropic-messages'


def pi_api_from_config(value: str) -> str:
    """将 ``openai_api_interface``（或 ``*_pi_api_interface``）映射为 Pi ``models.json`` 的 ``api``。"""
    interface = str(value or '').strip().casefold().replace('-', '_')
    if interface in {CONFIG_INTERFACE_CHAT, 'chat_completions'}:
        return PI_API_OPENAI_COMPLETIONS
    if interface in {CONFIG_INTERFACE_ANTHROPIC, 'anthropic_messages'}:
        return PI_API_ANTHROPIC_MESSAGES
    return PI_API_OPENAI_RESPONSES


def pi_provider_uses_bearer_auth(pi_api: str) -> bool:
    """OpenAI 兼容网关用 ``authHeader: true``；Anthropic Messages 走 SDK ``x-api-key``。"""
    return pi_api in {PI_API_OPENAI_RESPONSES, PI_API_OPENAI_COMPLETIONS}


def healthcheck_api_label(pi_api: str) -> str:
    """门禁成功提示里的协议简称。"""
    if pi_api == PI_API_ANTHROPIC_MESSAGES:
        return 'Anthropic Messages'
    if pi_api == PI_API_OPENAI_COMPLETIONS:
        return 'Chat Completions'
    return 'Responses'