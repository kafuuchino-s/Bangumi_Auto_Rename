"""Pi AI 健康门禁。

用与生产 Case Agent 完全相同的 /responses 链路 + provider/baseUrl/apiKey 配置，
subprocess 调 `tools/pi_ai_healthcheck.mjs` 起一个最小 agent session（挂 1 个 ping_reply
customTool），验证模型「连通 + 会发起 tool_call」两件事。

替换旧的 `AIClient.test_connection()`（走 Python OpenAI SDK /chat/completions，与生产
Pi sidecar 的 /responses 脱节，且只测「能回一个字」不测工具调用，无法发现今天
deepseek-v4-flash 那种「能回话但不会调工具推进 workflow」的问题）。

配置读取口径与 `src/rename/case_agent/pi_runner.py::_prepare_pi_runtime_model_config`
保持一致：rename_local_bangumi_pi_* 覆盖键优先，回落 ai_* 全局键。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ..config.config_manager import cm

# 与 pi_runner / pi_ai_healthcheck.mjs 同口径。
_PI_API_KEY_ENV = 'BAR_PI_CASE_AGENT_API_KEY'
_DEFAULT_PI_PROVIDER = 'bangumi-config-openai'
_HEALTHCHECK_SCRIPT = Path(__file__).resolve().parents[2] / 'tools' / 'pi_ai_healthcheck.mjs'


def _config_str(key: str, default: str = '') -> str:
    try:
        value = cm.get_config(key)
    except Exception:
        value = None
    return str(value if value is not None else default)


def _pi_api_from_config(value: str) -> str:
    """与 pi_runner._pi_api_from_config 同口径。"""
    interface = str(value or '').strip().casefold()
    if interface == 'chat_completions':
        return 'openai-completions'
    return 'openai-responses'


def _resolve_model_config() -> dict[str, str] | None:
    """解析 healthcheck 需要的 provider/model/base_url/api/api_key。

    缺 model/base_url/api_key 任一返回 None（调用方给「配置不完整」人话）。
    """
    model = _config_str('rename_local_bangumi_pi_model', '').strip() or _config_str('ai_model', '').strip()
    base_url = _config_str('rename_local_bangumi_pi_base_url', '').strip() or _config_str('ai_base_url', '').strip()
    api_key = _config_str('rename_local_bangumi_pi_api_key', '').strip() or _config_str('ai_api_key', '').strip()
    if not model or not base_url or not api_key:
        return None
    provider = _config_str('rename_local_bangumi_pi_provider', _DEFAULT_PI_PROVIDER).strip() or _DEFAULT_PI_PROVIDER
    api = _pi_api_from_config(
        _config_str('rename_local_bangumi_pi_api_interface', '').strip()
        or _config_str('openai_api_interface', 'responses_api')
    )
    return {
        'provider': provider,
        'model': model,
        'base_url': base_url,
        'api': api,
        'api_key': api_key,
    }


def _classify_error(error: str) -> str:
    """把底层错误串归类成人话，与旧 AIClient.test_connection 的口径对齐。"""
    low = (error or '').lower()
    if '401' in low or 'unauthorized' in low or 'invalid api key' in low:
        return 'AI 连通失败：API 密钥无效或未授权（401）'
    if '404' in low or 'not found' in low:
        return 'AI 连通失败：模型不存在或地址错误'
    if 'timeout' in low or 'timed out' in low:
        return 'AI 门禁失败：请求超时，检查 base_url 网络可达性或模型响应速度'
    if 'connection' in low or 'resolve' in low or 'getaddrinfo' in low or 'econnrefused' in low:
        return 'AI 连通失败：无法连接到 API 地址，检查 base_url'
    return f'AI 门禁失败：{error[:200]}'


def run_healthcheck(timeout_seconds: int = 35) -> tuple[bool, str]:
    """运行 Pi AI 健康门禁。

    Returns:
        (success, message)：成功返回 (True, 人话)；失败返回 (False, 人话)。
    """
    cfg = _resolve_model_config()
    if cfg is None:
        return False, 'AI 配置不完整：请填写 API 地址 / 密钥 / 模型'

    if not _HEALTHCHECK_SCRIPT.exists():
        return False, f'AI 门禁脚本缺失：{_HEALTHCHECK_SCRIPT}'

    argv = [
        'node',
        str(_HEALTHCHECK_SCRIPT),
        '--provider', cfg['provider'],
        '--model', cfg['model'],
        '--base-url', cfg['base_url'],
        '--api', cfg['api'],
        '--timeout', str(max(5, min(timeout_seconds - 5, 60))),
    ]
    env = os.environ.copy()
    env[_PI_API_KEY_ENV] = cfg['api_key']

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout_seconds,
            env=env,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return False, f'AI 门禁失败：整体超时 {timeout_seconds}s'
    except FileNotFoundError:
        return False, 'AI 门禁失败：未找到 node 可执行文件，请确认 Node.js 已安装'

    # 解析 stdout 最后一行 JSON。
    result: dict[str, Any] | None = None
    stdout = (completed.stdout or '').strip()
    if stdout:
        last_line = stdout.splitlines()[-1].strip()
        try:
            result = json.loads(last_line)
        except json.JSONDecodeError:
            result = None

    if result is None:
        # 非零退出且无 JSON：拿 stderr 兜底。
        err = (completed.stderr or '').strip().splitlines()[-1:] or ['no stderr']
        return False, f'AI 门禁执行失败：{err[0][:200]}'

    connectivity_ok = bool(result.get('connectivity_ok'))
    tool_call_ok = bool(result.get('tool_call_ok'))
    error = str(result.get('error') or '')
    model = str(result.get('model') or cfg['model'])

    if connectivity_ok and tool_call_ok:
        preview = str(result.get('reply_preview') or '').strip()
        suffix = f'（{preview[:60]}）' if preview else ''
        return True, f'AI 门禁通过（模型 {model}，/responses 链路 + 工具调用正常）{suffix}'

    if not connectivity_ok:
        return False, _classify_error(error or 'model did not return an assistant message')

    # 连通但未调工具：这是 agentic 能力短板信号（今天 deepseek fail_closed 的同款）。
    return (
        False,
        f'AI 连通但模型未发起工具调用：模型 {model} 可能不适配 agentic 工具编排，'
        f'建议换用 code/reasoning 级模型。({error[:120]})'
    )
