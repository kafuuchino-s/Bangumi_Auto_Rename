"""Pi AI 健康门禁单测。

不真起 node/subprocess：monkeypatch `subprocess.run` 返回构造的 CompletedProcess，
覆盖 run_healthcheck 的 JSON 解析与人话映射路径。重点是「连通但未调工具」这条
（今天 deepseek-v4-flash fail_closed 的同款信号）能被正确识别并给出 agentic 能力提示。
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.ai import pi_healthcheck
from src.config.config_manager import cm


@pytest.fixture(autouse=True)
def _configured_healthcheck():
    """为 mock subprocess 测试提供确定且不含真实密钥的 AI 配置。"""
    with cm.temporary_config(
        {
            'rename_local_bangumi_pi_model': '',
            'rename_local_bangumi_pi_base_url': '',
            'rename_local_bangumi_pi_api_key': '',
            'rename_local_bangumi_pi_api_interface': '',
            'ai_model': 'test-model',
            'ai_base_url': 'https://example.invalid',
            'ai_api_key': 'sk-test',
            'openai_api_interface': 'responses_api',
        }
    ):
        yield


def _completed(stdout: str = '', stderr: str = '', returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=['node', 'x'], returncode=returncode, stdout=stdout, stderr=stderr)


def _hc_result(**over) -> str:
    base = {
        'ok': True,
        'connectivity_ok': True,
        'tool_call_ok': True,
        'model': 'deepseek-v4-flash',
        'provider': 'bangumi-config-openai',
        'base_url': 'https://api.bbbc.eu.org',
        'reply_preview': 'all good',
        'elapsed_ms': 3000,
        'error': '',
    }
    base.update(over)
    import json
    return json.dumps(base)


def test_run_healthcheck_success(monkeypatch):
    """连通 + 工具调用都通过 → success=True，message 含协议标签 + 模型名。"""
    monkeypatch.setattr(pi_healthcheck.subprocess, 'run', lambda *a, **k: _completed(_hc_result()))
    with cm.temporary_config(
        {
            'ai_model': 'deepseek-v4-flash',
            'ai_base_url': 'https://api.bbbc.eu.org',
            'ai_api_key': 'sk-test',
            'openai_api_interface': 'responses_api',
        }
    ):
        ok, msg = pi_healthcheck.run_healthcheck()
    assert ok is True
    assert 'deepseek-v4-flash' in msg
    assert 'Responses' in msg


def test_run_healthcheck_success_anthropic_messages_label(monkeypatch):
    """anthropic_messages 配置 → 门禁成功文案为 Anthropic Messages（非 Responses）。"""
    captured: dict[str, list[str]] = {}

    def _capture_run(argv, **kwargs):
        captured['argv'] = list(argv)
        return _completed(_hc_result(model='grok-composer-2.5-fast'))

    monkeypatch.setattr(pi_healthcheck.subprocess, 'run', _capture_run)

    with cm.temporary_config(
        {
            'ai_model': 'grok-composer-2.5-fast',
            'ai_base_url': 'https://api.bbbc.eu.org',
            'ai_api_key': 'sk-test',
            'openai_api_interface': 'anthropic_messages',
        }
    ):
        ok, msg = pi_healthcheck.run_healthcheck()

    assert ok is True
    assert 'Anthropic Messages' in msg
    assert 'grok-composer-2.5-fast' in msg
    argv = captured.get('argv') or []
    api_idx = argv.index('--api')
    assert argv[api_idx + 1] == 'anthropic-messages'


def test_run_healthcheck_connected_but_no_tool_call(monkeypatch):
    """连通但模型未调工具 → success=False，message 提示 agentic 能力短板。"""
    monkeypatch.setattr(
        pi_healthcheck.subprocess,
        'run',
        lambda *a, **k: _completed(
            _hc_result(ok=False, connectivity_ok=True, tool_call_ok=False, error='model connected but did not issue a tool call')
        ),
    )
    ok, msg = pi_healthcheck.run_healthcheck()
    assert ok is False
    assert '未发起工具调用' in msg
    assert 'agentic' in msg


def test_run_healthcheck_connectivity_failed_classifies_error(monkeypatch):
    """连通失败 → 人话归类（401/404/timeout/connection）。"""
    monkeypatch.setattr(
        pi_healthcheck.subprocess,
        'run',
        lambda *a, **k: _completed(
            _hc_result(ok=False, connectivity_ok=False, tool_call_ok=False, error='Unauthorized: 401 invalid api key')
        ),
    )
    ok, msg = pi_healthcheck.run_healthcheck()
    assert ok is False
    assert '401' in msg or '密钥' in msg


def test_run_healthcheck_connection_error_classified(monkeypatch):
    """连接类错误（ECONNREFUSED 等）归类为「无法连接」。"""
    monkeypatch.setattr(
        pi_healthcheck.subprocess,
        'run',
        lambda *a, **k: _completed(
            _hc_result(ok=False, connectivity_ok=False, tool_call_ok=False, error='fetch failed: ECONNREFUSED')
        ),
    )
    ok, msg = pi_healthcheck.run_healthcheck()
    assert ok is False
    assert '连接' in msg or 'base_url' in msg


def test_run_healthcheck_nonzero_exit_no_json_uses_stderr(monkeypatch):
    """进程非零退出且无 JSON → 拿 stderr 兜底。"""
    monkeypatch.setattr(
        pi_healthcheck.subprocess,
        'run',
        lambda *a, **k: _completed(stdout='', stderr='Error: node module not found\n', returncode=1),
    )
    ok, msg = pi_healthcheck.run_healthcheck()
    assert ok is False
    assert '执行失败' in msg


def test_run_healthcheck_subprocess_timeout(monkeypatch):
    """subprocess 整体超时 → 明确超时人话。"""
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=['node'], timeout=35)
    monkeypatch.setattr(pi_healthcheck.subprocess, 'run', _raise)
    ok, msg = pi_healthcheck.run_healthcheck()
    assert ok is False
    assert '超时' in msg


def test_run_healthcheck_node_missing(monkeypatch):
    """node 不存在（FileNotFoundError）→ 明确提示 Node.js。"""
    def _raise(*a, **k):
        raise FileNotFoundError(2, 'No such file', 'node')
    monkeypatch.setattr(pi_healthcheck.subprocess, 'run', _raise)
    ok, msg = pi_healthcheck.run_healthcheck()
    assert ok is False
    assert 'node' in msg.lower() or 'Node.js' in msg


def test_resolve_model_config_missing_returns_none(monkeypatch):
    """缺 api_key 等必填 → _resolve_model_config 返回 None，run_healthcheck 给「配置不完整」。"""
    # 临时清空所有支持的模型配置来源。
    with cm.temporary_config({
        'rename_local_bangumi_pi_model': '',
        'rename_local_bangumi_pi_base_url': '',
        'rename_local_bangumi_pi_api_key': '',
        'ai_model': '',
        'ai_base_url': '',
        'ai_api_key': '',
    }):
        ok, msg = pi_healthcheck.run_healthcheck()
    assert ok is False
    assert '配置不完整' in msg


