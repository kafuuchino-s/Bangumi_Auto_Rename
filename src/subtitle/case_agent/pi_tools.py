"""字幕 Case Agent Pi 工具状态（Phase 3）。

对齐 ``src.rename.bgm_to_tmdb.tools.BgmToTmdbBridgeToolState`` 的角色，但字幕
语义简单：事实（字幕文件 + 已落盘目标视频）都是本地的，无需 TMDB 搜索 / 合法图
hydration / 跨季消歧。工具面因此远小于 bangumi 的 9201 行。

工具集（Pi sidecar 经本地 HTTP tool server 调用）：
- ``get_subtitle_mapping_context``：暴露事实卡片 + 合同边界给 AI。
- ``validate_subtitle_mapping``：解析 draft → verify → 回 issue / repair_hints。
- ``submit_subtitle_mapping``：verify_and_compile → 通过则落 final_result。
- ``fail_closed``：终止为合格 fail_closed 结果。

固定层只做事实 + 合同；候选归属 / 版本语言歧义 / 跨季归属这类不确定判断由 AI
在 draft 里表达（map_to_video / unmatched / needs_more_evidence），合同拦非法。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Callable

from src.rename.case_agent.models import CaseVerifierResult

from .models import (
    CompiledSubtitlePlan,
    SubtitleMappingDraft,
    SubtitleTargetVideoCard,
)
from .verifier import verify_and_compile_subtitle_plan, verify_subtitle_mapping_draft
from .workspace import SubtitleCaseWorkspace


LanguageResolver = Callable[[str], tuple[str, bool]]


# ---------------------------------------------------------------------------
# 工具状态
# ---------------------------------------------------------------------------

@dataclass
class SubtitleCaseToolState:
    """字幕 Case Agent Pi 工具状态：持有 workspace + draft/verifier/final 槽。"""

    workspace: SubtitleCaseWorkspace
    run_dir: Path
    language_resolver: LanguageResolver
    archive_name: str = ''
    sample_id: str = ''
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    mapping_draft: SubtitleMappingDraft | None = None
    verifier_result: CaseVerifierResult | None = None
    compiled_plan: CompiledSubtitlePlan | None = None
    final_result: dict[str, Any] | None = None
    last_invalid_submission: dict[str, Any] | None = None
    submit_rejection_count: int = 0

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / 'artifacts').mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # case_input：喂给 Pi sidecar（sidecar 只读 sample_id + runtime_policy 预算）
    # ------------------------------------------------------------------

    def case_input(self) -> dict[str, Any]:
        artifacts_dir = self.run_dir / 'artifacts'
        return {
            'case_agent_mode': 'subtitle_case_agent',
            'archive_name': self.archive_name or self.workspace.archive_name,
            'sample_id': self.sample_id,
            'runtime_policy': {
                'dry_run_only': True,
                'file_mutation_allowed': False,
            },
            'scratch_paths': {
                'artifacts_dir': str(artifacts_dir),
                'subtitle_mapping_draft': str(artifacts_dir / 'subtitle_mapping_draft.json'),
                'subtitle_verifier_result': str(artifacts_dir / 'subtitle_verifier_result.json'),
            },
            'case_goal': {
                'objective': 'Produce a verifier-accepted subtitle-to-video mapping plan (every subtitle mapped to a target video or declared unmatched with reason), or fail closed for global ambiguity.',
                'done_when': [
                    'validate_subtitle_mapping returns accepted=true',
                    'submit_subtitle_mapping returns accepted=true',
                    'every subtitle file is either map_to_video (with a valid TV ref + language) or unmatched (with reason); no needs_more_evidence rows remain',
                    'no file move/copy/link/rename operation is performed',
                ],
            },
            'context': self._context_payload(detail=True),
        }

    # ------------------------------------------------------------------
    # 工具分发
    # ------------------------------------------------------------------

    def handle_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.time()
        args = dict(arguments or {})
        trace_row = {
            'index': len(self.tool_trace) + 1,
            'tool': str(name or ''),
            'arguments': _json_safe(args),
            'started_at': started,
        }
        try:
            handler = getattr(self, f'tool_{name}', None)
            if handler is None:
                result = {'ok': False, 'accepted': False, 'error': f'unknown tool: {name}'}
            else:
                result = handler(**args)
        except Exception as exc:
            result = {'ok': False, 'accepted': False, 'error': f'{type(exc).__name__}: {exc}'}
        trace_row['elapsed_ms'] = int((time.time() - started) * 1000)
        trace_row['ok'] = bool(result.get('ok')) if isinstance(result, dict) else False
        trace_row['result_summary'] = self._compact_result_summary(result)
        self.tool_trace.append(trace_row)
        with (self.run_dir / 'tool_trace.jsonl').open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(_json_safe(trace_row), ensure_ascii=False, sort_keys=True))
            fh.write('\n')
        return _json_safe(result)

    # ------------------------------------------------------------------
    # 工具：get_context
    # ------------------------------------------------------------------

    def tool_get_subtitle_mapping_context(self, detail: bool = False) -> dict[str, Any]:
        return {'ok': True, 'data': self._context_payload(detail=bool(detail))}

    # ------------------------------------------------------------------
    # 工具：validate
    # ------------------------------------------------------------------

    def tool_validate_subtitle_mapping(self, mapping_draft: dict[str, Any] | None = None) -> dict[str, Any]:
        draft, error = self._parse_mapping_draft_payload(mapping_draft)
        if error:
            return {
                'ok': False,
                'accepted': False,
                'status': 'invalid',
                'error': error,
                'repair_hints': ['Submit a JSON object shaped like SubtitleMappingDraft (rows with subtitle_ref, disposition, target_ref, language, reason).'],
            }
        assert draft is not None
        verifier_result = verify_subtitle_mapping_draft(
            subtitle_files=self.workspace.subtitle_files,
            target_videos=self.workspace.target_videos,
            draft=draft,
        )
        self.mapping_draft = draft
        self.verifier_result = verifier_result
        self._write_artifacts(draft, verifier_result)
        accepted = bool(verifier_result.passed)
        status = 'accepted' if accepted else 'invalid'
        return {
            'ok': True,
            'accepted': accepted,
            'status': status,
            'summary': verifier_result.summary,
            'repair_hints': _subtitle_repair_hints(verifier_result),
            'verifier_result': verifier_result.model_dump(mode='json'),
            'mapping_draft': draft.model_dump(mode='json'),
        }

    # ------------------------------------------------------------------
    # 工具：submit
    # ------------------------------------------------------------------

    def tool_submit_subtitle_mapping(self, mapping_draft: dict[str, Any] | None = None, summary: str = '') -> dict[str, Any]:
        draft, error = self._parse_mapping_draft_payload(mapping_draft)
        if error:
            return {
                'ok': False,
                'accepted': False,
                'status': 'invalid',
                'error': error,
                'repair_hints': ['Submit a JSON object shaped like SubtitleMappingDraft.'],
            }
        assert draft is not None
        compiled_plan, verifier_result = verify_and_compile_subtitle_plan(
            subtitle_files=self.workspace.subtitle_files,
            target_videos=self.workspace.target_videos,
            draft=draft,
            language_resolver=self.language_resolver,
        )
        self.mapping_draft = draft
        self.verifier_result = verifier_result
        self.compiled_plan = compiled_plan
        self._write_artifacts(draft, verifier_result, compiled_plan=compiled_plan)
        if not verifier_result.passed or compiled_plan is None:
            self.submit_rejection_count += 1
            self.last_invalid_submission = {
                'mapping_draft': draft.model_dump(mode='json'),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'repair_hints': _subtitle_repair_hints(verifier_result),
            }
            return {
                'ok': True,
                'accepted': False,
                'status': 'invalid',
                'summary': 'Verifier rejected the subtitle mapping draft; revise and submit again.',
                'repair_hints': _subtitle_repair_hints(verifier_result),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'mapping_draft': draft.model_dump(mode='json'),
            }
        self.final_result = {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': str(summary or compiled_plan.summary or 'Pi submitted verifier-accepted subtitle mapping plan.'),
            'final_action': 'submit_subtitle_mapping',
            'mapping_draft': draft.model_dump(mode='json'),
            'compiled_plan': compiled_plan.model_dump(mode='json'),
            'final_verifier_result': verifier_result.model_dump(mode='json'),
        }
        self._write_final_result()
        return {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': self.final_result['summary'],
            'repair_hints': [],
            'verifier_result': verifier_result.model_dump(mode='json'),
            'compiled_plan': compiled_plan.model_dump(mode='json'),
        }

    # ------------------------------------------------------------------
    # 工具：fail_closed
    # ------------------------------------------------------------------

    def tool_fail_closed(
        self,
        reason: str,
        reason_kind: str = 'insufficient_evidence',
        related_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        allowed = {'contradiction', 'insufficient_evidence', 'provider_failure', 'unknown'}
        kind = str(reason_kind or 'insufficient_evidence')
        if kind not in allowed:
            kind = 'unknown'
        summary = str(reason or 'Subtitle mapping failed closed.')
        verifier_result = CaseVerifierResult(passed=True, issues=[], summary='fail_closed')
        self.final_result = {
            'ok': True,
            'accepted': True,
            'status': 'fail_closed',
            'summary': summary,
            'final_action': 'fail_closed',
            'reason_kind': kind,
            'related_refs': [str(ref) for ref in (related_refs or []) if str(ref)],
            'final_verifier_result': verifier_result.model_dump(mode='json'),
        }
        self._write_final_result()
        return {'ok': True, 'accepted': True, 'status': 'fail_closed', 'summary': summary}

    # ------------------------------------------------------------------
    # auto-finalize / auto-fail-closed（sidecar 预算耗尽 / 已校验通过时兜底）
    # ------------------------------------------------------------------

    def auto_finalize_accepted_validation(self) -> dict[str, Any]:
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        if self.mapping_draft is not None and self.verifier_result is not None and self.verifier_result.passed:
            result = self.handle_tool(
                'submit_subtitle_mapping',
                {
                    'mapping_draft': self.mapping_draft.model_dump(mode='json'),
                    'summary': 'Runner finalized Pi-validated subtitle mapping after validate returned accepted=true.',
                },
            )
            if result.get('accepted') and self.final_result:
                self.final_result['auto_finalized_from_validated_draft'] = True
                self._write_final_result()
            return result
        return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'no validated mapping_draft has been accepted'}

    def auto_fail_closed_no_final_result(self, reason: str) -> dict[str, Any]:
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        return self.handle_tool(
            'fail_closed',
            {
                'reason': reason,
                'reason_kind': 'provider_failure' if 'timeout' in str(reason).casefold() or str(reason).casefold() == 'budget_exhausted' else 'unknown',
            },
        )

    def tool_summary(self) -> dict[str, Any]:
        return {
            'tool_trace_count': len(self.tool_trace),
            'tool_call_counts': dict(Counter(str(row.get('tool') or '') for row in self.tool_trace)),
            'tool_sequence': [str(row.get('tool') or '') for row in self.tool_trace],
            'submit_rejection_count': self.submit_rejection_count,
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _context_payload(self, *, detail: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'archive_name': self.workspace.archive_name,
            'subtitle_files': self.workspace.readable_subtitle_cards(),
            'target_videos': self.workspace.readable_target_cards(),
            'subtitle_contract': {
                'identity_policy': 'subtitle_ref / target_ref must use the fixed-layer SF<idx> / TV<idx> short refs shown above. Archive paths, task UUIDs, and video filenames are evidence only; the draft must reference the short refs.',
                'final_tools': ['validate_subtitle_mapping', 'submit_subtitle_mapping', 'fail_closed'],
                'primary_workflow': 'For each subtitle, decide map_to_video (with a TV ref + language) or unmatched (with reason). Use needs_more_evidence only while investigating; resolve to map_to_video or unmatched before submit.',
                'disposition_semantics': [
                    'map_to_video: pair the subtitle with a target video; requires target_ref + language (chs/cht/jpn/eng/...).',
                    'unmatched: the subtitle has no confident target; requires a reason; must NOT carry a target_ref.',
                    'needs_more_evidence: still investigating; blocks accepted readiness; must NOT carry a target_ref.',
                ],
                'language_policy': (
                    'Use raw language tags (chs/cht/jpn/eng/ko) in the draft. '
                    'Filename and provider labels are weak hints; '
                    'content_chinese_script is high-confidence dialogue '
                    'evidence and must win on conflict. The fixed layer '
                    'normalizes tags to Emby codes and rejects a mapped '
                    'Chinese language that contradicts high-confidence '
                    'content. Same target video may carry multiple subtitles '
                    'only if their languages differ.'
                ),
                'coverage_policy': 'Every subtitle must appear exactly once as map_to_video or unmatched. mappings + unmatched must equal subtitle_count. No needs_more_evidence rows may remain at submit.',
                'dry_run_only': True,
            },
        }
        if detail:
            payload['current_mapping_draft'] = self.mapping_draft.model_dump(mode='json') if self.mapping_draft else None
            payload['current_verifier_result'] = self.verifier_result.model_dump(mode='json') if self.verifier_result else None
            payload['last_invalid_submission'] = _json_safe(self.last_invalid_submission)
        return payload

    def _parse_mapping_draft_payload(self, payload: dict[str, Any] | None) -> tuple[SubtitleMappingDraft | None, str]:
        if payload is None:
            return None, 'missing mapping_draft'
        if not isinstance(payload, dict):
            return None, 'mapping_draft must be a canonical JSON object'
        try:
            return SubtitleMappingDraft.model_validate(payload), ''
        except Exception as exc:
            return None, str(exc)

    def _write_artifacts(
        self,
        draft: SubtitleMappingDraft,
        verifier_result: CaseVerifierResult,
        *,
        compiled_plan: CompiledSubtitlePlan | None = None,
    ) -> None:
        artifacts_dir = self.run_dir / 'artifacts'
        (artifacts_dir / 'subtitle_mapping_draft.json').write_text(
            json.dumps(draft.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        (artifacts_dir / 'subtitle_verifier_result.json').write_text(
            json.dumps(verifier_result.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        if compiled_plan is not None:
            (artifacts_dir / 'subtitle_compiled_plan.json').write_text(
                json.dumps(compiled_plan.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
                encoding='utf-8',
            )

    def _write_final_result(self) -> None:
        (self.run_dir / 'final_result.json').write_text(
            json.dumps(_json_safe(self.final_result), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )

    def _compact_result_summary(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {'type': type(result).__name__}
        summary = {key: result.get(key) for key in ('ok', 'accepted', 'status', 'summary', 'error') if key in result}
        if isinstance(result.get('repair_hints'), list):
            summary['repair_hint_count'] = len(result['repair_hints'])
        verifier = result.get('verifier_result')
        if isinstance(verifier, dict):
            summary['verifier_passed'] = verifier.get('passed')
            summary['verifier_issue_count'] = len(verifier.get('issues') or [])
        return summary


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, 'model_dump'):
        dumped = value.model_dump(mode='json')
        return dumped if isinstance(dumped, (dict, list)) else value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


def _subtitle_repair_hints(verifier_result: CaseVerifierResult) -> list[str]:
    """把 verifier issue 翻译成给 AI 的修复提示。"""
    hints: list[str] = []
    for issue in verifier_result.issues:
        code = issue.issue_code
        if code == 'coverage_error':
            hints.append('Cover every subtitle exactly once: each subtitle must appear as map_to_video or unmatched, and no extra rows.')
        elif code == 'not_ready':
            hints.append('Resolve all needs_more_evidence rows to map_to_video or unmatched before submitting.')
        elif code == 'duplicate_subtitle_ref':
            hints.append(f'Subtitle {issue.ref} appears more than once; a subtitle may appear only once in the draft.')
        elif code == 'unknown_subtitle_ref':
            hints.append(f'Subtitle ref {issue.ref} is not a fixed-layer SF ref; copy the ref from subtitle_files in the context.')
        elif code == 'unknown_target_ref':
            hints.append(f'Target ref {issue.ref} is not a fixed-layer TV ref; copy the ref from target_videos in the context.')
        elif code == 'invalid_ref_shape':
            hints.append(f'Ref {issue.ref} must use the SF<idx> / TV<idx> short-ref form shown in the context.')
        elif code == 'missing_target_ref':
            hints.append(f'map_to_video row for {issue.ref} requires a target_ref (a TV ref).')
        elif code == 'missing_language':
            hints.append(f'map_to_video row for {issue.ref} requires a language tag (chs/cht/jpn/eng/...).')
        elif code == 'content_language_conflict':
            hints.append(
                f'Subtitle {issue.ref} language conflicts with high-confidence '
                'dialogue content; use chs for simplified or cht for traditional.'
            )
        elif code == 'duplicate_target_language':
            hints.append(f'Target {issue.ref} already has a subtitle for that language; use a different language or a different target.')
        elif code == 'invalid_target_on_unmatched':
            hints.append(f'unmatched row {issue.ref} must not carry a target_ref.')
        elif code == 'invalid_target_on_needs_more_evidence':
            hints.append(f'needs_more_evidence row {issue.ref} must not carry a target_ref.')
        elif code == 'invalid_disposition':
            hints.append(f'Row {issue.ref} disposition must be map_to_video / unmatched / needs_more_evidence.')
        elif code == 'missing_subtitle_ref':
            hints.append('Each draft row requires a subtitle_ref.')
        else:
            hints.append(f'{code}: {issue.message}')
    # 去重保序
    seen: set[str] = set()
    deduped: list[str] = []
    for hint in hints:
        if hint not in seen:
            seen.add(hint)
            deduped.append(hint)
    return deduped
