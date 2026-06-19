"""字幕自动抓取 Case Agent Pi 工具状态（Phase 3）。

对齐 ``subtitle.case_agent.pi_tools.SubtitleCaseToolState`` 的角色，但
auto_fetch 是 candidate ranking（选帖/选包），不是 mapping——工具面是"主动
取证 + 决策"而非"草稿 + 合同校验"。

工具集（Pi sidecar 经本地 HTTP tool server 调用）：
- ``get_auto_fetch_context``：暴露事实卡（scan_scope + missing_videos + 已用
  关键词 + 已注入候选/楼包）+ 轻 gate 边界给 AI。
- ``search_candidates(keyword)``：调 provider.search → 注入 CD 事实卡。AI 主动
  发起搜索（多轮证据驱动）。
- ``load_candidate_packages(candidate_ref)``：调 provider.prepare_candidate +
  load_thread_packages → 回填该候选的楼包 PK 事实卡。
- ``inspect_package(package_ref)``：返回楼包详情（post_text/links/flags）供 AI
  判断正片 vs 特典/字体。
- ``submit_candidate(candidate_ref, language, reason)``：选中帖，跑轻 gate
  （候选含可下载附件）→ 落 final_result（accepted 候选阶段，待选包）。
- ``submit_package(package_ref, reason)``：选中楼包，跑轻 gate（非
  font/patch-only + 可下载）→ 落 final_result（accepted，可下载落盘）。
- ``fail_closed(reason, reason_kind)``：终止为合格 fail_closed。
- ``need_confirm(reason)``：终止为合格 need_confirm（AI 不确定选哪个帖/包）。

固定层只做事实 + 轻 gate；arc 归属 / 版本语言歧义 / 正片 vs 特典 这类不确定
判断由 AI 在 submit reason / fail_closed / need_confirm 里表达。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Callable

from src.rename.case_agent.models import CaseVerifierResult

from .evidence_broker import candidate_card_from_provider
from .models import (
    AutoFetchDecision,
    CandidateCard,
    ThreadPackageCard,
)
from .verifier import auto_fetch_repair_hints, verify_auto_fetch_decision
from .workspace import AutoFetchCaseWorkspace


# ---------------------------------------------------------------------------
# 工具状态
# ---------------------------------------------------------------------------

@dataclass
class AutoFetchCaseToolState:
    """auto_fetch Case Agent Pi 工具状态：持有 workspace + provider + 决策槽。"""

    workspace: AutoFetchCaseWorkspace
    run_dir: Path
    provider: Any  # SubtitleProvider（search/prepare_candidate/load_thread_packages/download）
    task_data: dict[str, Any] = field(default_factory=dict)
    sample_id: str = ''
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    decision: AutoFetchDecision | None = None
    verifier_result: CaseVerifierResult | None = None
    # provider 原始候选/楼包对象，按 ref 索引（供 auto_fetch 下载复用）
    provider_candidates_by_ref: dict[str, Any] = field(default_factory=dict)
    provider_packages_by_ref: dict[str, Any] = field(default_factory=dict)
    final_result: dict[str, Any] | None = None
    last_invalid_submission: dict[str, Any] | None = None
    submit_rejection_count: int = 0

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / 'artifacts').mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # case_input：喂给 Pi sidecar
    # ------------------------------------------------------------------

    def case_input(self) -> dict[str, Any]:
        artifacts_dir = self.run_dir / 'artifacts'
        return {
            'case_agent_mode': 'auto_fetch_case_agent',
            'sample_id': self.sample_id,
            'task_uuid': self.workspace.task_uuid,
            'runtime_policy': {
                'dry_run_only': True,
                'file_mutation_allowed': False,
            },
            'scratch_paths': {
                'artifacts_dir': str(artifacts_dir),
                'auto_fetch_decision': str(artifacts_dir / 'auto_fetch_decision.json'),
                'auto_fetch_verifier_result': str(artifacts_dir / 'auto_fetch_verifier_result.json'),
            },
            'case_goal': {
                'objective': 'Produce an accepted candidate + package selection for fetching a subtitle archive, or fail closed / need confirm for global ambiguity.',
                'done_when': [
                    'submit_candidate returns accepted=true (candidate selected)',
                    'submit_package returns accepted=true (package selected, ready to download)',
                    'fail_closed or need_confirm returns accepted=true (qualified terminal)',
                    'no file download is performed inside the agent',
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

    def tool_get_auto_fetch_context(self, detail: bool = False) -> dict[str, Any]:
        return {'ok': True, 'data': self._context_payload(detail=bool(detail))}

    # ------------------------------------------------------------------
    # 工具：search_candidates（AI 主动取证）
    # ------------------------------------------------------------------

    def tool_search_candidates(self, keyword: str, limit: int = 10) -> dict[str, Any]:
        keyword = str(keyword or '').strip()
        if not keyword:
            return {'ok': False, 'accepted': False, 'error': 'keyword required'}
        try:
            raw_candidates = self.provider.search(keyword, limit=int(limit) or 10)
        except Exception as exc:
            return {'ok': False, 'accepted': False, 'error': f'provider search failed: {exc}'}
        if not raw_candidates:
            return {
                'ok': True,
                'accepted': False,
                'status': 'no_candidates',
                'keyword': keyword,
                'candidate_count': 0,
            }
        # 注入事实卡 + 索引 provider 原始对象
        new_refs: list[str] = []
        for raw in raw_candidates:
            card = candidate_card_from_provider(raw)
            indexed = self.workspace.add_candidate(card)
            self.provider_candidates_by_ref[indexed.ref] = raw
            new_refs.append(indexed.ref)
        return {
            'ok': True,
            'accepted': False,
            'status': 'candidates_loaded',
            'keyword': keyword,
            'candidate_count': len(raw_candidates),
            'new_candidate_refs': new_refs,
            'candidates': self.workspace.readable_candidate_cards(),
        }

    # ------------------------------------------------------------------
    # 工具：load_candidate_packages（深解析楼包）
    # ------------------------------------------------------------------

    def tool_load_candidate_packages(self, candidate_ref: str) -> dict[str, Any]:
        candidate_ref = str(candidate_ref or '').strip()
        raw = self.provider_candidates_by_ref.get(candidate_ref)
        if raw is None:
            return {'ok': False, 'accepted': False, 'error': f'unknown candidate_ref: {candidate_ref}'}
        try:
            prepared = self.provider.prepare_candidate(raw)
            loaded = self.provider.load_thread_packages(prepared)
        except Exception as exc:
            return {'ok': False, 'accepted': False, 'error': f'provider load failed: {exc}'}
        # 用加载后的 provider 对象替换槽，并重建该候选的楼包事实卡
        self.provider_candidates_by_ref[candidate_ref] = loaded
        new_card = candidate_card_from_provider(loaded)
        new_card = new_card.model_copy(update={'ref': candidate_ref})
        # 回填 workspace 中对应候选的 packages（保留 ref 分配）
        ws_candidate = self.workspace.candidate_by_ref().get(candidate_ref)
        if ws_candidate is not None:
            packages: list[ThreadPackageCard] = []
            existing_pkg_refs = [pkg.ref for pkg in ws_candidate.packages]
            for idx, pkg_card in enumerate(new_card.packages):
                ref = existing_pkg_refs[idx] if idx < len(existing_pkg_refs) else ''
                packages.append(pkg_card.model_copy(update={'ref': ref, 'candidate_ref': candidate_ref}))
                if ref:
                    self.provider_packages_by_ref[ref] = loaded.thread_packages[idx]
            updated = ws_candidate.model_copy(update={
                'packages': packages,
                'pages_scanned': new_card.pages_scanned,
                'pagination_truncated': new_card.pagination_truncated,
                'has_downloadable_attachment': new_card.has_downloadable_attachment,
            })
            self.workspace.candidates = [
                updated if c.ref == candidate_ref else c for c in self.workspace.candidates
            ]
        return {
            'ok': True,
            'accepted': False,
            'status': 'packages_loaded',
            'candidate_ref': candidate_ref,
            'package_refs': [pkg.ref for pkg in (updated.packages if ws_candidate else [])],
            'packages': self.workspace.readable_candidate_cards(),
        }

    # ------------------------------------------------------------------
    # 工具：inspect_package
    # ------------------------------------------------------------------

    def tool_inspect_package(self, package_ref: str) -> dict[str, Any]:
        package_ref = str(package_ref or '').strip()
        pkg_index = self.workspace.package_by_ref()
        pkg = pkg_index.get(package_ref)
        if pkg is None:
            return {'ok': False, 'accepted': False, 'error': f'unknown package_ref: {package_ref}'}
        return {
            'ok': True,
            'accepted': False,
            'status': 'package_inspected',
            'package': {
                'ref': pkg.ref,
                'candidate_ref': pkg.candidate_ref,
                'package_id': pkg.package_id,
                'floor_label': pkg.floor_label,
                'post_text': pkg.post_text,
                'context_text': pkg.context_text,
                'has_direct_download': pkg.has_direct_download,
                'package_flags': list(pkg.package_flags),
                'has_downloadable_link': pkg.has_downloadable_link,
                'is_font_or_patch_only': pkg.is_font_or_patch_only,
                'links': [
                    {
                        'url': link.url,
                        'kind': link.kind,
                        'label': link.label,
                        'filename_hint': link.filename_hint,
                        'is_direct_download': link.is_direct_download,
                    }
                    for link in pkg.links
                ],
            },
        }

    # ------------------------------------------------------------------
    # 工具：submit_candidate（选中帖，轻 gate）
    # ------------------------------------------------------------------

    def tool_submit_candidate(
        self,
        candidate_ref: str,
        language: str = '',
        reason: str = '',
    ) -> dict[str, Any]:
        decision = AutoFetchDecision(
            disposition='select_candidate',
            candidate_ref=str(candidate_ref or ''),
            language=str(language or ''),
            confidence='Medium',
            reason=str(reason or ''),
        )
        verifier_result = verify_auto_fetch_decision(
            workspace=self.workspace, decision=decision
        )
        self.decision = decision
        self.verifier_result = verifier_result
        self._write_artifacts(decision, verifier_result)
        if not verifier_result.passed:
            self.submit_rejection_count += 1
            self.last_invalid_submission = {
                'decision': decision.model_dump(mode='json'),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'repair_hints': auto_fetch_repair_hints(verifier_result),
            }
            return {
                'ok': True,
                'accepted': False,
                'status': 'invalid',
                'summary': 'Candidate gate rejected the selection; revise and submit again.',
                'repair_hints': auto_fetch_repair_hints(verifier_result),
                'verifier_result': verifier_result.model_dump(mode='json'),
            }
        # 候选阶段 accepted：暂存，提示 AI 继续选包（不落 final_result，等 submit_package）
        candidate = self.workspace.candidate_by_ref().get(decision.candidate_ref)
        self.final_result = None  # 候选阶段不终止
        return {
            'ok': True,
            'accepted': True,
            'status': 'candidate_accepted',
            'summary': f'Candidate {decision.candidate_ref} accepted; next: load_candidate_packages + submit_package.',
            'candidate_ref': decision.candidate_ref,
            'language': decision.language,
            'next_action': 'submit_package',
        }

    # ------------------------------------------------------------------
    # 工具：submit_package（选中楼包，轻 gate → final accepted）
    # ------------------------------------------------------------------

    def tool_submit_package(self, package_ref: str, reason: str = '') -> dict[str, Any]:
        package_ref = str(package_ref or '').strip()
        pkg_index = self.workspace.package_by_ref()
        pkg = pkg_index.get(package_ref)
        candidate_ref = pkg.candidate_ref if pkg else ''
        decision = AutoFetchDecision(
            disposition='select_package',
            candidate_ref=candidate_ref,
            package_ref=package_ref,
            language='',
            confidence='Medium',
            reason=str(reason or ''),
        )
        verifier_result = verify_auto_fetch_decision(
            workspace=self.workspace, decision=decision
        )
        self.decision = decision
        self.verifier_result = verifier_result
        if not verifier_result.passed:
            self.submit_rejection_count += 1
            self._write_artifacts(decision, verifier_result)
            self.last_invalid_submission = {
                'decision': decision.model_dump(mode='json'),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'repair_hints': auto_fetch_repair_hints(verifier_result),
            }
            return {
                'ok': True,
                'accepted': False,
                'status': 'invalid',
                'summary': 'Package gate rejected the selection; revise and submit again.',
                'repair_hints': auto_fetch_repair_hints(verifier_result),
                'verifier_result': verifier_result.model_dump(mode='json'),
            }
        candidate = self.workspace.candidate_by_ref().get(candidate_ref)
        download_url = ''
        if pkg:
            for link in pkg.links:
                if link.is_direct_download and link.url:
                    download_url = link.url
                    break
        self.final_result = {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': str(reason or 'Pi submitted accepted candidate + package selection.'),
            'final_action': 'submit_package',
            'decision': decision.model_dump(mode='json'),
            'selected_candidate_ref': candidate_ref,
            'selected_package_ref': package_ref,
            'selected_candidate_title': candidate.title if candidate else '',
            'selected_candidate_detail_url': candidate.detail_url if candidate else '',
            'download_url': download_url,
            'final_verifier_result': verifier_result.model_dump(mode='json'),
        }
        self._write_artifacts(decision, verifier_result)
        self._write_final_result()
        return {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': self.final_result['summary'],
            'selected_candidate_ref': candidate_ref,
            'selected_package_ref': package_ref,
            'verifier_result': verifier_result.model_dump(mode='json'),
        }

    # ------------------------------------------------------------------
    # 工具：fail_closed / need_confirm
    # ------------------------------------------------------------------

    def tool_fail_closed(
        self,
        reason: str,
        reason_kind: str = 'insufficient_evidence',
        related_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        allowed = {'contradiction', 'insufficient_evidence', 'provider_failure', 'no_candidates', 'unknown'}
        kind = str(reason_kind or 'insufficient_evidence')
        if kind not in allowed:
            kind = 'unknown'
        summary = str(reason or 'Auto fetch failed closed.')
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

    def tool_need_confirm(self, reason: str) -> dict[str, Any]:
        summary = str(reason or 'Auto fetch needs human confirmation.')
        verifier_result = CaseVerifierResult(passed=True, issues=[], summary='need_confirm')
        self.final_result = {
            'ok': True,
            'accepted': True,
            'status': 'need_confirm',
            'summary': summary,
            'final_action': 'need_confirm',
            'final_verifier_result': verifier_result.model_dump(mode='json'),
        }
        self._write_final_result()
        return {'ok': True, 'accepted': True, 'status': 'need_confirm', 'summary': summary}

    # ------------------------------------------------------------------
    # auto-finalize / auto-fail-closed（兜底）
    # ------------------------------------------------------------------

    def auto_finalize_accepted_validation(self) -> dict[str, Any]:
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'no accepted candidate+package to finalize'}

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
            'task_uuid': self.workspace.task_uuid,
            'scan_scope': self.workspace.readable_scan_scope(),
            'missing_videos': self.workspace.readable_missing_video_cards(),
            'keywords': self.workspace.readable_keyword_cards(),
            'candidates': self.workspace.readable_candidate_cards(),
            'auto_fetch_contract': {
                'identity_policy': 'candidate_ref / package_ref must use the fixed-layer CD<idx> / PK<idx> short refs. Use search_candidates to load candidates, load_candidate_packages to deep-load packages, inspect_package to read package details.',
                'final_tools': ['submit_candidate', 'submit_package', 'fail_closed', 'need_confirm'],
                'primary_workflow': 'search_candidates(keyword) -> inspect candidates -> submit_candidate -> load_candidate_packages -> inspect_package -> submit_package. Or fail_closed / need_confirm when no candidate fits.',
                'gate_semantics': [
                    'submit_candidate: candidate must have downloadable attachment or packages.',
                    'submit_package: package must have downloadable link and not be font/patch-only.',
                ],
                'disposition_hints': [
                    'Pick the candidate whose title/arc matches the missing videos (use source_video hint when subtitle naming matches local original).',
                    'Pick a package with batch/simplified/traditional/bilingual marker; avoid font/patch-only or special-only packages for main episodes.',
                ],
                'dry_run_only': True,
            },
        }
        if detail:
            payload['current_decision'] = self.decision.model_dump(mode='json') if self.decision else None
            payload['current_verifier_result'] = self.verifier_result.model_dump(mode='json') if self.verifier_result else None
            payload['last_invalid_submission'] = _json_safe(self.last_invalid_submission)
        return payload

    def _write_artifacts(
        self,
        decision: AutoFetchDecision,
        verifier_result: CaseVerifierResult,
    ) -> None:
        artifacts_dir = self.run_dir / 'artifacts'
        (artifacts_dir / 'auto_fetch_decision.json').write_text(
            json.dumps(decision.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        (artifacts_dir / 'auto_fetch_verifier_result.json').write_text(
            json.dumps(verifier_result.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
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
