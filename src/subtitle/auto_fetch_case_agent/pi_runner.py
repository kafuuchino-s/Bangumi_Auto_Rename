"""字幕自动抓取 Case Agent Pi runner（Phase 3）。

对齐 ``src.subtitle.case_agent.pi_runner`` 的形状：复用通用基础件
（``PiRuntimeModelConfig`` / ``_config_int`` / ``_config_str`` /
``_prepare_pi_runtime_model_config``），自有的平行件只有 tool state
（``AutoFetchCaseToolState``）与独立 sidecar
（``tools/pi_auto_fetch_case_agent_runner.mjs``）。

流程：
1. 构建 workspace（固定层事实：scan_scope + missing_videos + keywords）。
2. 分配 run_dir，写 case_input.json。
3. 起本地 HTTP tool server（Pi sidecar 经 POST /tool 调 ``AutoFetchCaseToolState``）。
4. 调 node sidecar（subprocess，wall-clock timeout 由配置控制）。
5. runtime 结束后兜底：timeout -> auto fail_closed；无 final -> auto fail_closed
   (budget_exhausted)。
6. 解析 final_result，返回 ``AutoFetchCaseAgentRunResult``。

支持 fake runtime（``BAR_PI_AUTO_FETCH_CASE_AGENT_FAKE_RESULT_JSON`` 环境变量）与
``runtime_invoker`` 注入，便于单测不真起 node。
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

from ...config.config_manager import cm
from ...rename.case_agent.models import CaseVerifierResult
from ...rename.case_agent.pi_runner import (
    PiRuntimeModelConfig,
    _config_int,
    _config_str,
    _prepare_pi_runtime_model_config,
)
from .models import AutoFetchDecision, AutoFetchSelectedCandidate
from .pi_tools import AutoFetchCaseToolState, _json_safe
from .workspace import AutoFetchCaseWorkspace


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_NODE_RUNNER = REPO_ROOT / 'tools' / 'pi_auto_fetch_case_agent_runner.mjs'
_FAKE_RUNTIME_ENV = 'BAR_PI_AUTO_FETCH_CASE_AGENT_FAKE_RESULT_JSON'


@dataclass
class AutoFetchCaseAgentRunResult:
    ok: bool
    status: str
    sample_id: str
    summary: str
    final_action: str
    run_dir: Path
    errors: list[str] = field(default_factory=list)
    decision: AutoFetchDecision | None = None
    final_verifier_result: CaseVerifierResult | None = None
    selected_candidate_ref: str = ''
    selected_package_ref: str = ''
    # Pi 驱动爬取后，selected 的 provider 原始对象（供 Python 主进程下载）。
    # sidecar 跑完 tool_state 持有 provider_candidates_by_ref/provider_packages_by_ref，
    # 这里把 selected 的对象带出，避免 Python 主进程无法下载。
    selected_provider_candidate: Any = None
    selected_provider_package: Any = None
    # 多季覆盖：submit_complete 落 final 含 selections list（每 subject 一条）。
    # 旧单 submit_package final（无 selections）回退到 selected_candidate_ref/package。
    # selections_provider 是每 selection 对应的 (candidate, package) provider 原始对象，
    # 供 auto_fetch 逐个下载。
    selections: list[AutoFetchSelectedCandidate] = field(default_factory=list)
    selections_provider: list[tuple[Any, Any]] = field(default_factory=list)
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


def run_auto_fetch_case_agent_pi(
    *,
    workspace: AutoFetchCaseWorkspace,
    provider: Any,
    task_data: dict[str, Any],
    source_label: str = '',
    runtime_invoker: Callable[[AutoFetchCaseToolState], dict[str, Any]] | None = None,
) -> AutoFetchCaseAgentRunResult:
    """运行 auto_fetch Case Agent Pi 后端。"""
    timeout_seconds = _config_int('subtitle_auto_fetch_case_agent_pi_timeout_seconds', 300, minimum=1)
    configured_command = _config_str('subtitle_auto_fetch_case_agent_pi_command', '')
    runtime_command_override = str(configured_command or '').strip()
    root = _case_root()
    root.mkdir(parents=True, exist_ok=True)
    case_id = _safe_case_id(source_label or workspace.task_uuid or 'auto_fetch')
    run_dir = root / 'runs' / f'{time.strftime("%Y%m%d-%H%M%S")}-{case_id}-{secrets.token_hex(4)}'
    state = AutoFetchCaseToolState(
        workspace=workspace,
        run_dir=run_dir,
        provider=provider,
        task_data=dict(task_data or {}),
        sample_id=case_id,
    )

    runtime_model_config = _prepare_pi_runtime_model_config(run_dir)
    case_input = state.case_input()
    case_input.setdefault('runtime_policy', {})
    case_input['runtime_policy'].update({
        'wall_clock_timeout_seconds': timeout_seconds,
        'suggested_finish_before_seconds': max(1, timeout_seconds - 30),
    })
    case_input['pi_command'] = runtime_command_override
    if runtime_model_config is not None:
        case_input.update({
            'pi_provider': runtime_model_config.provider,
            'pi_model': runtime_model_config.model,
            'pi_base_url': runtime_model_config.base_url,
            'pi_api': runtime_model_config.api,
        })
    case_input_path = run_dir / 'case_input.json'
    case_input_path.write_text(json.dumps(_json_safe(case_input), ensure_ascii=False, indent=2), encoding='utf-8')

    fake_payload_text = os.environ.get(_FAKE_RUNTIME_ENV, '').strip()
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

    # 兜底：Pi 选了包（state.selections 非空）但没调 submit_complete 就结束
    # （sidecar 旧行为或 Pi 漏调）→ 自动 submit_complete 落 final，避免误 fail_closed。
    # force=True 跳过 uncovered 确认（Pi 已结束，nudge 无意义，直接落 final）。
    if state.final_result is None and state.selections:
        state.tool_submit_complete(reason='auto: selections present but no submit_complete call', force=True)
    if state.final_result is None and runtime.get('error') == 'timeout':
        auto_timeout_fail_closed = state.auto_fail_closed_no_final_result(
            reason=f'Pi auto fetch case agent runtime exceeded wall-clock timeout of {timeout_seconds} seconds without an accepted selection.',
        )
        if not auto_timeout_fail_closed.get('skipped'):
            runtime['post_runtime_timeout_fail_closed'] = auto_timeout_fail_closed
    if state.final_result is None:
        auto_fail_closed = state.auto_fail_closed_no_final_result(reason='budget_exhausted')
        if not auto_fail_closed.get('skipped'):
            runtime['post_runtime_auto_fail_closed'] = auto_fail_closed

    final = state.final_result or {}
    status = str(final.get('status') or ('error' if not runtime.get('ok') else 'invalid'))
    ok = bool(final.get('ok')) if final else False
    summary = str(final.get('summary') or runtime.get('error') or 'Pi auto fetch case agent runtime ended without a final submit/fail_closed/need_confirm result.')
    errors: list[str] = []
    if not runtime.get('ok') and not ok:
        errors.append('error_kind=pi_runtime_failed')
        if runtime.get('error'):
            errors.append(str(runtime.get('error')))
    if not final:
        errors.append('error_kind=pi_no_final_result')
    final_verifier_result = _parse_model(final.get('final_verifier_result'), CaseVerifierResult, errors, 'final_verifier_parse_error')
    decision = _parse_model(final.get('decision'), AutoFetchDecision, errors, 'decision_parse_error')
    trace_summary = state.tool_summary()
    # 多季覆盖：从 final.selections 构造 selections list + provider 对象。
    # 兼容旧单 submit_package final（无 selections 字段）：回退到 state.selections
    # （submit_package 累加的）或 selected_candidate_ref/package。
    selections: list[AutoFetchSelectedCandidate] = []
    selections_provider: list[tuple[Any, Any]] = []
    final_selections_raw = final.get('selections') or []
    if isinstance(final_selections_raw, list) and final_selections_raw:
        for sel_raw in final_selections_raw:
            if not isinstance(sel_raw, dict):
                continue
            try:
                sel = AutoFetchSelectedCandidate(**{
                    k: v for k, v in sel_raw.items()
                    if k in AutoFetchSelectedCandidate.model_fields
                })
            except Exception:
                continue
            selections.append(sel)
            sel_cand = state.provider_candidates_by_ref.get(sel.candidate_ref)
            sel_pkg = state.provider_packages_by_ref.get(sel.package_ref)
            selections_provider.append((sel_cand, sel_pkg))
    # 兼容旧 final（单 submit_package 落 final 的 selected_candidate_ref/package）
    sel_cand_ref = str(final.get('selected_candidate_ref') or '')
    sel_pkg_ref = str(final.get('selected_package_ref') or '')
    if not selections and sel_cand_ref:
        # 旧格式或 submit_package 直接落 final 的场景，包装成单 selection
        legacy_sel = AutoFetchSelectedCandidate(
            candidate_ref=sel_cand_ref,
            package_ref=sel_pkg_ref,
            detail_url=str(final.get('selected_candidate_detail_url') or ''),
            title=str(final.get('selected_candidate_title') or ''),
            download_url=str(final.get('download_url') or ''),
        )
        selections.append(legacy_sel)
        selections_provider.append((
            state.provider_candidates_by_ref.get(sel_cand_ref),
            state.provider_packages_by_ref.get(sel_pkg_ref),
        ))
    result = AutoFetchCaseAgentRunResult(
        ok=ok,
        status=status,
        sample_id=case_id,
        summary=summary,
        final_action=str(final.get('final_action') or ('fail_closed' if status == 'fail_closed' else '')),
        run_dir=run_dir,
        errors=errors,
        decision=decision or state.decision,
        final_verifier_result=final_verifier_result or state.verifier_result,
        selected_candidate_ref=sel_cand_ref or (selections[0].candidate_ref if selections else ''),
        selected_package_ref=sel_pkg_ref or (selections[0].package_ref if selections else ''),
        selected_provider_candidate=state.provider_candidates_by_ref.get(sel_cand_ref)
        if sel_cand_ref else (selections_provider[0][0] if selections_provider else None),
        selected_provider_package=state.provider_packages_by_ref.get(sel_pkg_ref)
        if sel_pkg_ref else (selections_provider[0][1] if selections_provider else None),
        selections=selections,
        selections_provider=selections_provider,
        raw_runtime_result=runtime,
        tool_trace=state.tool_trace,
        tool_call_counts=dict(trace_summary['tool_call_counts']),
        tool_sequence=list(trace_summary['tool_sequence']),
        submit_rejection_count=state.submit_rejection_count,
        pi_command=runtime_command_override,
        pi_provider=runtime_model_config.provider if runtime_model_config is not None else '',
        pi_model=runtime_model_config.model if runtime_model_config is not None else '',
        pi_base_url=runtime_model_config.base_url if runtime_model_config is not None else '',
        runtime_command=list(runtime.get('argv') or []),
        runtime_returncode=runtime.get('returncode') if isinstance(runtime.get('returncode'), int) else None,
    )
    (run_dir / 'run_result_summary.json').write_text(
        json.dumps(
            _json_safe({
                'ok': result.ok,
                'status': result.status,
                'summary': result.summary,
                'errors': result.errors,
                'tool_summary': trace_summary,
                'runtime': runtime,
            }),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding='utf-8',
    )
    return result


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

def _case_root() -> Path:
    raw = _config_str('subtitle_auto_fetch_case_agent_pi_case_root', 'data/auto_fetch_case_agent').strip() or 'data/auto_fetch_case_agent'
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _safe_case_id(value: str) -> str:
    text = ''.join(ch if ch.isalnum() or ch in '-_.' else '-' for ch in str(value or 'case'))
    text = '-'.join(part for part in text.split('-') if part)
    return (text or 'case')[:80]


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
    argv = shlex.split(configured, posix=os.name != 'nt') if configured else ['node', str(DEFAULT_NODE_RUNNER)]
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
        if self.path == '/state':
            self._send_json(self._state_snapshot())
            return
        self._send_json({'ok': False, 'error': 'not found'}, status=404)

    def _state_snapshot(self) -> dict[str, Any]:
        """暴露 selections + missing-video subject 覆盖情况，供 sidecar nudge 判断
        "Pi 选了 1 个包就停了 / 仍有未覆盖 subject"。"""
        state = self.server.state
        ws = state.workspace
        # 所有目标 subject（去重，含 0/空 id 的归到一组）
        subject_ids: set[int] = set()
        per_subject_video_count: dict[int, int] = {}
        for card in ws.missing_videos:
            sid = getattr(card, 'bangumi_subject_id', 0) or 0
            subject_ids.add(sid)
            per_subject_video_count[sid] = per_subject_video_count.get(sid, 0) + 1
        covered_subjects = sorted(
            {s.bangumi_subject_id for s in state.selections if s.bangumi_subject_id}
        )
        uncovered_subjects = sorted(subject_ids - set(covered_subjects))
        return {
            'ok': True,
            'final_result_present': bool(state.final_result),
            'selections_count': len(state.selections),
            'covered_subject_ids': covered_subjects,
            'uncovered_subject_ids': uncovered_subjects,
            'total_subject_count': len(subject_ids),
            'per_subject_video_count': per_subject_video_count,
            'missing_video_count': len(ws.missing_videos),
        }

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
        result = self.server.state.handle_tool(
            str(payload.get('tool') or ''),
            payload.get('arguments') if isinstance(payload.get('arguments'), dict) else {},
        )
        self._send_json(result)

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(_json_safe(payload), ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ToolServer(ThreadingHTTPServer):
    def __init__(self, state: AutoFetchCaseToolState, token: str) -> None:
        super().__init__(('127.0.0.1', 0), _ToolRequestHandler)
        self.state = state
        self.token = token


@contextmanager
def _running_tool_server(state: AutoFetchCaseToolState, token: str) -> Iterator[str]:
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
    state: AutoFetchCaseToolState,
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


def _apply_fake_runtime(state: AutoFetchCaseToolState, payload: dict[str, Any]) -> dict[str, Any]:
    results = []
    for call in list(payload.get('tool_calls') or []):
        if not isinstance(call, dict):
            continue
        results.append(
            state.handle_tool(
                str(call.get('tool') or ''),
                call.get('arguments') if isinstance(call.get('arguments'), dict) else {},
            )
        )
    return {'ok': True, 'returncode': 0, 'argv': ['fake-pi-auto-fetch-runtime'], 'fake': True, 'tool_results': results}


def _parse_model(payload: Any, model: Any, errors: list[str], error_prefix: str) -> Any:
    if not isinstance(payload, dict):
        return None
    try:
        return model.model_validate(payload)
    except Exception as exc:
        errors.append(f'{error_prefix}={exc}')
        return None
