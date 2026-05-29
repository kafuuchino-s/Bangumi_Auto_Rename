from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

from ...config.config_manager import cm
from .models import CaseJudgeOutput, CaseVerifierResult, MappingDraft
from .pi_tools import PiCaseToolState, _json_safe
from .recipe import CompiledOrganizePlan, OrganizeRecipeDraft
from .workspace import CaseEvidenceWorkspace


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_NODE_RUNNER = REPO_ROOT / 'tools' / 'pi_case_agent_runner.mjs'
_PI_CONFIG_API_KEY_ENV = 'BAR_PI_CASE_AGENT_API_KEY'
_DEFAULT_PI_PROVIDER = 'bangumi-config-openai'


@dataclass
class PiCaseAgentRunResult:
    ok: bool
    status: str
    case_id: str
    summary: str
    final_action: str
    final_workspace: CaseEvidenceWorkspace
    run_dir: Path
    errors: list[str] = field(default_factory=list)
    evidence_batches: list[Any] = field(default_factory=list)
    judge_outputs: list[Any] = field(default_factory=list)
    final_output: CaseJudgeOutput | None = None
    final_verifier_result: CaseVerifierResult | None = None
    mapping_draft: MappingDraft | None = None
    organize_recipe: OrganizeRecipeDraft | None = None
    compiled_plan: CompiledOrganizePlan | None = None
    raw_runtime_result: dict[str, Any] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    tool_sequence: list[str] = field(default_factory=list)
    submit_rejection_count: int = 0
    pi_command: str = ''
    pi_provider: str = ''
    pi_model: str = ''
    pi_base_url: str = ''
    runtime_command: list[str] = field(default_factory=list)
    runtime_returncode: int | None = None


@dataclass(frozen=True)
class PiRuntimeModelConfig:
    agent_dir: Path
    provider: str
    model: str
    base_url: str
    api: str
    env: dict[str, str] = field(default_factory=dict)


def _config_int(key: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = cm.get_config(key)
        return max(minimum, int(value if value is not None else default))
    except Exception:
        return max(minimum, int(default))


def _config_str(key: str, default: str = '') -> str:
    try:
        value = cm.get_config(key)
    except Exception:
        value = None
    return str(value if value is not None else default)


def _case_root() -> Path:
    raw = _config_str('rename_local_bangumi_pi_case_root', 'data/pi_case_agent').strip() or 'data/pi_case_agent'
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _safe_case_id(value: str) -> str:
    text = ''.join(ch if ch.isalnum() or ch in '-_.' else '-' for ch in str(value or 'case'))
    text = '-'.join(part for part in text.split('-') if part)
    return (text or 'case')[:80]


def _resolve_pi_cli_command(configured: str = '') -> str:
    configured = str(configured or '').strip()
    if configured:
        return configured
    local_bin = REPO_ROOT / 'node_modules' / '.bin' / ('pi.cmd' if os.name == 'nt' else 'pi')
    if local_bin.exists():
        return str(local_bin)
    found = shutil.which('pi')
    return found or ''


def _pi_api_from_config(value: str) -> str:
    interface = str(value or '').strip().casefold()
    if interface == 'chat_completions':
        return 'openai-completions'
    return 'openai-responses'


def _prepare_pi_runtime_model_config(run_dir: Path) -> PiRuntimeModelConfig | None:
    model = _config_str('rename_local_bangumi_pi_model', '').strip() or _config_str('ai_model', '').strip()
    base_url = _config_str('rename_local_bangumi_pi_base_url', '').strip() or _config_str('ai_base_url', '').strip()
    api_key = _config_str('rename_local_bangumi_pi_api_key', '').strip() or _config_str('ai_api_key', '').strip()
    if not model or not base_url or not api_key:
        return None
    provider = _config_str('rename_local_bangumi_pi_provider', _DEFAULT_PI_PROVIDER).strip() or _DEFAULT_PI_PROVIDER
    api = _pi_api_from_config(_config_str('rename_local_bangumi_pi_api_interface', '').strip() or _config_str('openai_api_interface', 'responses_api'))
    agent_dir = run_dir / 'pi_agent_config'
    agent_dir.mkdir(parents=True, exist_ok=True)
    models_payload = {
        'providers': {
            provider: {
                'baseUrl': base_url.rstrip('/'),
                'api': api,
                'apiKey': _PI_CONFIG_API_KEY_ENV,
                'authHeader': True,
                'models': [
                    {
                        'id': model,
                        'name': f'Bangumi config {model}',
                        'reasoning': True,
                        'input': ['text'],
                        'contextWindow': 400000,
                        'maxTokens': 32000,
                        'cost': {
                            'input': 0,
                            'output': 0,
                            'cacheRead': 0,
                            'cacheWrite': 0,
                        },
                    }
                ],
            }
        }
    }
    (agent_dir / 'models.json').write_text(json.dumps(models_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    return PiRuntimeModelConfig(
        agent_dir=agent_dir,
        provider=provider,
        model=model,
        base_url=base_url.rstrip('/'),
        api=api,
        env={
            _PI_CONFIG_API_KEY_ENV: api_key,
            'PI_CODING_AGENT_DIR': str(agent_dir),
        },
    )


def _runtime_command(
    configured: str,
    *,
    case_input_path: Path,
    output_path: Path,
    server_url: str,
    token: str,
    runtime_model_config: PiRuntimeModelConfig | None = None,
) -> list[str]:
    configured = str(configured or '').strip()
    if configured:
        argv = shlex.split(configured, posix=os.name != 'nt')
    else:
        argv = ['node', str(DEFAULT_NODE_RUNNER)]
    argv = [
        *argv,
        '--input',
        str(case_input_path),
        '--output',
        str(output_path),
        '--server',
        server_url,
        '--token',
        token,
        '--repo-root',
        str(REPO_ROOT),
    ]
    if runtime_model_config is not None:
        argv.extend([
            '--agent-dir',
            str(runtime_model_config.agent_dir),
            '--provider',
            runtime_model_config.provider,
            '--model',
            runtime_model_config.model,
        ])
    return argv


class _ToolRequestHandler(BaseHTTPRequestHandler):
    server: '_ToolServer'

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == '/health':
            self._send_json({'ok': True})
            return
        if self.path == '/final':
            self._send_json({'ok': True, 'final_result': _json_safe(self.server.state.final_result)})
            return
        self._send_json({'ok': False, 'error': 'not found'}, status=404)

    def do_POST(self) -> None:
        if self.path != '/tool':
            self._send_json({'ok': False, 'error': 'not found'}, status=404)
            return
        try:
            length = int(self.headers.get('content-length') or 0)
            payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except Exception as exc:
            self._send_json({'ok': False, 'error': f'invalid json: {exc}'}, status=400)
            return
        if payload.get('token') != self.server.token:
            self._send_json({'ok': False, 'error': 'unauthorized'}, status=401)
            return
        result = self.server.state.handle_tool(str(payload.get('tool') or ''), payload.get('arguments') if isinstance(payload.get('arguments'), dict) else {})
        self._send_json(result)

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(_json_safe(payload), ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ToolServer(ThreadingHTTPServer):
    def __init__(self, state: PiCaseToolState, token: str) -> None:
        super().__init__(('127.0.0.1', 0), _ToolRequestHandler)
        self.state = state
        self.token = token


@contextmanager
def _running_tool_server(state: PiCaseToolState, token: str) -> Iterator[str]:
    server = _ToolServer(state, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f'http://{host}:{port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _invoke_pi_runtime(
    state: PiCaseToolState,
    *,
    case_input_path: Path,
    timeout_seconds: int,
    command: str,
    runtime_model_config: PiRuntimeModelConfig | None = None,
) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    output_path = state.run_dir / 'pi_runtime_result.json'
    with _running_tool_server(state, token) as server_url:
        argv = _runtime_command(
            command,
            case_input_path=case_input_path,
            output_path=output_path,
            server_url=server_url,
            token=token,
            runtime_model_config=runtime_model_config,
        )
        started = time.time()
        env = os.environ.copy()
        if runtime_model_config is not None:
            env.update(runtime_model_config.env)
        try:
            completed = subprocess.run(
                argv,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout_seconds,
                env=env,
                shell=False,
            )
            runtime = {
                'ok': completed.returncode == 0,
                'returncode': completed.returncode,
                'argv': argv,
                'elapsed_ms': int((time.time() - started) * 1000),
                'stdout_path': str(state.run_dir / 'pi_runtime_stdout.txt'),
                'stderr_path': str(state.run_dir / 'pi_runtime_stderr.txt'),
            }
            (state.run_dir / 'pi_runtime_stdout.txt').write_text(completed.stdout or '', encoding='utf-8')
            (state.run_dir / 'pi_runtime_stderr.txt').write_text(completed.stderr or '', encoding='utf-8')
        except subprocess.TimeoutExpired as exc:
            (state.run_dir / 'pi_runtime_stdout.txt').write_text(exc.stdout or '', encoding='utf-8')
            (state.run_dir / 'pi_runtime_stderr.txt').write_text(exc.stderr or '', encoding='utf-8')
            return {
                'ok': False,
                'returncode': None,
                'argv': argv,
                'elapsed_ms': int((time.time() - started) * 1000),
                'error': 'timeout',
                'timeout_seconds': timeout_seconds,
            }
    if output_path.exists():
        try:
            runtime['runner_result'] = json.loads(output_path.read_text(encoding='utf-8'))
        except Exception as exc:
            runtime['runner_result_parse_error'] = str(exc)
    return runtime


def _apply_fake_runtime(state: PiCaseToolState, payload: dict[str, Any]) -> dict[str, Any]:
    results = []
    for call in list(payload.get('tool_calls') or []):
        if not isinstance(call, dict):
            continue
        results.append(state.handle_tool(str(call.get('tool') or ''), call.get('arguments') if isinstance(call.get('arguments'), dict) else {}))
    return {'ok': True, 'returncode': 0, 'argv': ['fake-pi-runtime'], 'fake': True, 'tool_results': results}


def run_pi_case_agent(
    *,
    workspace: CaseEvidenceWorkspace,
    bangumi_client: Any,
    source_path: str = '',
    runtime_invoker: Callable[[PiCaseToolState], dict[str, Any]] | None = None,
) -> PiCaseAgentRunResult:
    timeout_seconds = _config_int('rename_local_bangumi_pi_timeout_seconds', 300, minimum=1)
    configured_command = _config_str('rename_local_bangumi_pi_command', '')
    pi_cli_command = _resolve_pi_cli_command(configured_command)
    root = _case_root()
    root.mkdir(parents=True, exist_ok=True)
    case_id = _safe_case_id(str(getattr(workspace.header, 'case_id', '') or 'local-bangumi'))
    run_dir = root / 'runs' / f'{time.strftime("%Y%m%d-%H%M%S")}-{case_id}-{secrets.token_hex(4)}'
    state = PiCaseToolState(
        workspace=workspace,
        bangumi_client=bangumi_client,
        run_dir=run_dir,
        repo_root=REPO_ROOT,
        source_path=source_path,
    )
    runtime_model_config = _prepare_pi_runtime_model_config(run_dir)
    case_input = state.case_input(pi_command=pi_cli_command, timeout_seconds=timeout_seconds)
    if runtime_model_config is not None:
        case_input.update({
            'pi_provider': runtime_model_config.provider,
            'pi_model': runtime_model_config.model,
            'pi_base_url': runtime_model_config.base_url,
            'pi_api': runtime_model_config.api,
        })
    case_input_path = run_dir / 'case_input.json'
    case_input_path.write_text(json.dumps(_json_safe(case_input), ensure_ascii=False, indent=2), encoding='utf-8')

    fake_payload_text = os.environ.get('BAR_PI_CASE_AGENT_FAKE_RESULT_JSON', '').strip()
    if runtime_invoker is not None:
        runtime = runtime_invoker(state)
    elif fake_payload_text:
        runtime = _apply_fake_runtime(state, json.loads(fake_payload_text))
    else:
        runtime = _invoke_pi_runtime(
            state,
            case_input_path=case_input_path,
            timeout_seconds=timeout_seconds,
            command=configured_command,
            runtime_model_config=runtime_model_config,
        )

    if state.final_result is None and runtime.get('error') == 'timeout':
        auto_timeout_fail_closed = state.auto_fail_closed_no_final_result(
            reason=f'Pi runtime exceeded wall-clock timeout of {timeout_seconds} seconds without an accepted recipe.',
        )
        if not auto_timeout_fail_closed.get('skipped'):
            runtime['post_runtime_timeout_fail_closed'] = auto_timeout_fail_closed
    if state.final_result is None:
        auto_finalization = state.auto_finalize_accepted_validation()
        if not auto_finalization.get('skipped'):
            runtime['post_runtime_auto_finalization'] = auto_finalization
    if state.final_result is None:
        auto_fail_closed = state.auto_fail_closed_no_final_result(
            reason='budget_exhausted',
        )
        if not auto_fail_closed.get('skipped'):
            runtime['post_runtime_auto_fail_closed'] = auto_fail_closed

    final = state.final_result or {}
    status = str(final.get('status') or ('error' if not runtime.get('ok') else 'invalid'))
    ok = bool(final.get('ok')) if final else False
    summary = str(final.get('summary') or runtime.get('error') or 'Pi runtime ended without a final submit_organize_recipe/fail_closed result.')
    errors: list[str] = []
    if not runtime.get('ok'):
        errors.append(f"error_kind=pi_runtime_failed")
        if runtime.get('error'):
            errors.append(str(runtime.get('error')))
    if not final:
        errors.append('error_kind=pi_no_final_result')
    final_output = None
    if isinstance(final.get('final_output'), dict):
        try:
            final_output = CaseJudgeOutput.model_validate(final['final_output'])
        except Exception as exc:
            errors.append(f'final_output_parse_error={exc}')
    final_verifier_result = None
    if isinstance(final.get('final_verifier_result'), dict):
        try:
            final_verifier_result = CaseVerifierResult.model_validate(final['final_verifier_result'])
        except Exception as exc:
            errors.append(f'final_verifier_parse_error={exc}')
    mapping_draft = None
    if isinstance(final.get('mapping_draft'), dict):
        try:
            mapping_draft = MappingDraft.model_validate(final['mapping_draft'])
        except Exception as exc:
            errors.append(f'mapping_draft_parse_error={exc}')
    organize_recipe = None
    if isinstance(final.get('organize_recipe'), dict):
        try:
            organize_recipe = OrganizeRecipeDraft.model_validate(final['organize_recipe'])
        except Exception as exc:
            errors.append(f'organize_recipe_parse_error={exc}')
    compiled_plan = None
    if isinstance(final.get('compiled_plan'), dict):
        try:
            compiled_plan = CompiledOrganizePlan.model_validate(final['compiled_plan'])
        except Exception as exc:
            errors.append(f'compiled_plan_parse_error={exc}')

    trace_summary = state.tool_summary()
    evidence_batches = list(getattr(state.workspace, 'previous_evidence_results', []) or [])
    result = PiCaseAgentRunResult(
        ok=ok,
        status=status,
        case_id=state.case_id,
        summary=summary,
        final_action=str(final.get('final_action') or ('fail_closed' if status == 'fail_closed' else '')),
        final_workspace=state.workspace,
        run_dir=run_dir,
        errors=errors,
        evidence_batches=evidence_batches,
        judge_outputs=[final_output] if final_output is not None else [],
        final_output=final_output,
        final_verifier_result=final_verifier_result,
        mapping_draft=mapping_draft or state.workspace.mapping_draft,
        organize_recipe=organize_recipe or state.organize_recipe,
        compiled_plan=compiled_plan or state.compiled_plan,
        raw_runtime_result=runtime,
        tool_trace=state.tool_trace,
        tool_call_counts=dict(trace_summary['tool_call_counts']),
        tool_sequence=list(trace_summary['tool_sequence']),
        submit_rejection_count=state.submit_rejection_count,
        pi_command=pi_cli_command,
        pi_provider=runtime_model_config.provider if runtime_model_config is not None else '',
        pi_model=runtime_model_config.model if runtime_model_config is not None else '',
        pi_base_url=runtime_model_config.base_url if runtime_model_config is not None else '',
        runtime_command=list(runtime.get('argv') or []),
        runtime_returncode=runtime.get('returncode') if isinstance(runtime.get('returncode'), int) else None,
    )
    (run_dir / 'run_result_summary.json').write_text(
        json.dumps(_json_safe({
            'ok': result.ok,
            'status': result.status,
            'summary': result.summary,
            'errors': result.errors,
            'tool_summary': trace_summary,
            'runtime': runtime,
        }), ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    return result
