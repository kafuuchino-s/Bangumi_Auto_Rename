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

from src.rename.case_agent.models import CaseVerifierResult, VerifierIssue

from .evidence_broker import candidate_card_from_provider
from .models import (
    AutoFetchDecision,
    AutoFetchSelectedCandidate,
    CandidateCard,
    ThreadPackageCard,
    build_selection_key,
)
from .verifier import auto_fetch_repair_hints, verify_auto_fetch_decision
from .workspace import AutoFetchCaseWorkspace


# 渐进式取证上限（参考 rename case_agent._EVIDENCE_BATCH_LIMIT）：
# 大样本（多季番多 BGM 名变体）一次性搜全部词 + 加载全部帖会撑爆 wall-clock。
# 单次 search 最多搜 N 个词（主词在前），剩余留给 Pi 后续轮次按需搜；
# 单次 load 最多加载 N 个候选帖，Pi 看完少数不满意再 load 更多。
_SEARCH_KEYWORD_BATCH_LIMIT = 4
_LOAD_CANDIDATE_BATCH_LIMIT = 3
# 并发 I/O 上限（架构加速 A/C）：acgrip search/load 是独立 HTTP 请求，实测
# 并发 4.9x 加速且无限流。上限 4 避免过高并发触发站点限流；分批 limit 仍控
# 单次总 I/O 量，并发只改批内执行方式（串行→并行）。
_SEARCH_CONCURRENCY = 4
_LOAD_CONCURRENCY = 4


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
    # 多季覆盖：累加的选中结果（每 subject 一条），submit_package 不落 final 而是append。
    # submit_complete 时落 final_result.selections = list[AutoFetchSelectedCandidate]。
    selections: list[AutoFetchSelectedCandidate] = field(default_factory=list)
    # submit_complete 确认计数：多 subject 任务 Pi 选部分包就 submit_complete 时，
    # 第一次不落 final 返回"还有 uncovered subject 确认搜过无帖"提示，逼 Pi 再确认；
    # 第二次（confirmations>=1）才落 final。防 Pi 偷懒选 1 包就停（0042/0062 波动）。
    # 不违反"不强制全处置"：第二次 submit_complete 仍可留 uncovered（用户拍板合格）。
    submit_complete_confirmations: int = 0
    # B 缓存：已 load 过楼包的 candidate_ref 集合。合帖场景（一帖覆盖多 subject，
    # 如 0042 ARIA tid=3582 覆盖 3 subject）下 Pi 对同帖多次 load_candidate_packages
    # 时跳过 provider HTTP，直接复用 workspace 已有包事实卡，省重复 I/O。
    _loaded_candidate_refs: set[str] = field(default_factory=set)

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
    # 工具：search_candidates（批量搜索，AI 主动取证）
    # ------------------------------------------------------------------

    def tool_search_candidates(
        self,
        keyword: Any = None,
        keywords: Any = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """批量搜索字幕候选：注入 CD<idx> 事实卡。

        参数兼容：``keywords``（list[str]）批量优先；``keyword``（str）单词兼容旧调用。

        渐进式分批（参考 rename atlas-first）：单次最多搜
        ``_SEARCH_KEYWORD_BATCH_LIMIT`` 个词（主词 name_cn/name 在前，由
        ``_build_search_keywords`` 保序产出），超出部分本次不搜，返回里告知 Pi
        "还有 N 个词未搜，可再次调用 search_candidates 搜剩余"。大样本（多季番
        多 BGM 名变体）避免一次性爬全部词撑爆 wall-clock。
        """
        kw_list = _coerce_string_list(keywords)
        if kw_list and isinstance(keyword, str) and keyword.strip():
            kw_list.append(keyword)
        if not kw_list and isinstance(keyword, str) and keyword.strip():
            kw_list = [keyword]
        kw_list = [str(k or '').strip() for k in kw_list if str(k or '').strip()]
        if not kw_list:
            return {'ok': False, 'accepted': False, 'error': 'keyword(s) required'}

        # 分批：本次只搜前 N 个词，剩余留给 Pi 后续轮次
        searched_kws = kw_list[:_SEARCH_KEYWORD_BATCH_LIMIT]
        remaining_kws = kw_list[_SEARCH_KEYWORD_BATCH_LIMIT:]

        seen_detail_urls = {
            candidate.detail_url
            for candidate in self.workspace.candidates
            if candidate.detail_url
        }
        all_new_refs: list[str] = []
        per_keyword: list[dict[str, Any]] = []

        # 并发 search（架构加速 A）：多个关键词的 provider.search 是独立 HTTP
        # 请求；provider 内部负责自身并发安全。并发上限
        # min(词数, _SEARCH_CONCURRENCY) 避免过高并发触发站点限流。
        from concurrent.futures import ThreadPoolExecutor

        search_kw_results: dict[str, Any] = {}

        def _search_one(kw: str) -> tuple[str, Any]:
            try:
                raw_candidates = self.provider.search(kw, limit=int(limit) or 10)
                return kw, raw_candidates
            except Exception as exc:
                return kw, exc

        with ThreadPoolExecutor(max_workers=min(len(searched_kws), _SEARCH_CONCURRENCY)) as ex:
            for kw, result in ex.map(_search_one, searched_kws):
                search_kw_results[kw] = result

        # 偶发空结果重试：来源站搜索可能短暂返回空页；对并发轮返回 0 命中
        # 或异常的词串行重试 1 次。provider 自身缓存可避免结构化精确搜索重复
        # 发网请求，不改变 Pi 决策语义。
        retry_kws = [
            kw for kw in searched_kws
            if not search_kw_results.get(kw)
            or isinstance(search_kw_results.get(kw), Exception)
        ]
        for kw in retry_kws:
            try:
                raw_retry = self.provider.search(kw, limit=int(limit) or 10)
                if raw_retry:
                    search_kw_results[kw] = raw_retry
            except Exception:
                pass  # 保留原异常结果，per_keyword 报 error

        for kw in searched_kws:
            result = search_kw_results[kw]
            if isinstance(result, Exception):
                per_keyword.append({'keyword': kw, 'candidate_count': 0, 'error': f'provider search failed: {result}'})
                continue
            raw_candidates = result
            new_refs: list[str] = []
            for raw in raw_candidates:
                detail_url = str(getattr(raw, 'detail_url', '') or '')
                if detail_url and detail_url in seen_detail_urls:
                    continue
                if detail_url:
                    seen_detail_urls.add(detail_url)
                card = candidate_card_from_provider(raw)
                indexed = self.workspace.add_candidate(card)
                self.provider_candidates_by_ref[indexed.ref] = raw
                new_refs.append(indexed.ref)
            all_new_refs.extend(new_refs)
            per_keyword.append({'keyword': kw, 'candidate_count': len(raw_candidates), 'new_candidate_refs': new_refs})

        base: dict[str, Any] = {
            'keywords': searched_kws,
            'candidate_count': len(all_new_refs),
            'new_candidate_refs': all_new_refs,
            'per_keyword': per_keyword,
            'candidates': self.workspace.readable_candidate_cards(),
        }
        if remaining_kws:
            base['remaining_keywords'] = remaining_kws
            base['next_action_hint'] = (
                f'{len(remaining_kws)} keyword(s) not searched this turn (batch limit '
                f'{_SEARCH_KEYWORD_BATCH_LIMIT}). If no candidate matches the arc, call '
                f'search_candidates again with the remaining keywords.'
            )

        if not all_new_refs:
            return {
                'ok': True,
                'accepted': False,
                'status': 'no_candidates',
                **base,
            }
        return {
            'ok': True,
            'accepted': False,
            'status': 'candidates_loaded',
            **base,
        }

    # ------------------------------------------------------------------
    # 工具：load_candidate_packages（批量深解析楼包）
    # ------------------------------------------------------------------

    def tool_load_candidate_packages(
        self,
        candidate_ref: Any = None,
        candidate_refs: Any = None,
    ) -> dict[str, Any]:
        """批量深解析楼包：加载候选帖的 thread packages 成 PK<idx> 事实卡。

        参数兼容：``candidate_refs``（list[str]）批量优先；``candidate_ref``（str）单帖兼容。

        渐进式分批（参考 rename 按需取证）：单次最多加载
        ``_LOAD_CANDIDATE_BATCH_LIMIT`` 个候选帖，超出部分本次不加载，返回里告知
        Pi "还有 N 个候选未加载，可再次调用"。大命中数时避免一次性爬全部帖楼包
        撑爆 wall-clock；Pi 先 inspect 少数候选选定帖，再 load 选定帖的包。
        """
        refs = _coerce_string_list(candidate_refs)
        if isinstance(candidate_ref, str) and candidate_ref.strip():
            refs.append(candidate_ref)
        if not refs and isinstance(candidate_ref, str) and candidate_ref.strip():
            refs = [candidate_ref]
        refs = [str(r or '').strip() for r in refs if str(r or '').strip()]
        if not refs:
            return {'ok': False, 'accepted': False, 'error': 'candidate_ref(s) required'}

        # 分批：本次只加载前 N 个候选，剩余留给 Pi 后续轮次
        loaded_target_refs = refs[:_LOAD_CANDIDATE_BATCH_LIMIT]
        remaining_refs = refs[_LOAD_CANDIDATE_BATCH_LIMIT:]

        loaded_refs: list[str] = []
        all_package_refs: list[str] = []
        per_candidate: list[dict[str, Any]] = []

        # B 缓存：跳过已 load 的 candidate_ref（合帖多 subject 复用，省重复 HTTP）。
        # 幂等：workspace 已有该 ref 的 packages_loaded=True 事实卡，直接报已有包 ref。
        refs_to_load: list[str] = []
        cached_refs: list[str] = []
        for candidate_ref_item in loaded_target_refs:
            if candidate_ref_item in self._loaded_candidate_refs:
                cached_refs.append(candidate_ref_item)
            else:
                refs_to_load.append(candidate_ref_item)

        # C 并发 load：未缓存的 ref 并发调 provider.prepare_candidate + load_thread_packages
        # （独立 HTTP，实测并发安全）。结果回主线程串行写 workspace（避免并发写共享态）。
        from concurrent.futures import ThreadPoolExecutor

        def _load_one(ref: str) -> tuple[str, Any]:
            raw = self.provider_candidates_by_ref.get(ref)
            if raw is None:
                return ref, ValueError('unknown candidate_ref')
            try:
                prepared = self.provider.prepare_candidate(raw)
                loaded = self.provider.load_thread_packages(prepared)
                return ref, loaded
            except Exception as exc:
                return ref, exc

        load_results: dict[str, Any] = {}
        if refs_to_load:
            with ThreadPoolExecutor(max_workers=min(len(refs_to_load), _LOAD_CONCURRENCY)) as ex:
                for ref, result in ex.map(_load_one, refs_to_load):
                    load_results[ref] = result

        # 主线程串行写 workspace（避免并发写共享态）
        for candidate_ref_item in loaded_target_refs:
            # B 缓存命中：复用已有包，不重复 HTTP
            if candidate_ref_item in cached_refs:
                ws_candidate = self.workspace.candidate_by_ref().get(candidate_ref_item)
                pkg_refs = [pkg.ref for pkg in (ws_candidate.packages if ws_candidate else []) if pkg.ref]
                loaded_refs.append(candidate_ref_item)
                all_package_refs.extend(pkg_refs)
                per_candidate.append({'candidate_ref': candidate_ref_item, 'package_refs': pkg_refs, 'cached': True})
                continue

            result = load_results.get(candidate_ref_item)
            if isinstance(result, Exception) or result is None:
                err = 'unknown candidate_ref' if result is None else f'provider load failed: {result}'
                per_candidate.append({'candidate_ref': candidate_ref_item, 'error': err})
                continue
            loaded = result
            self.provider_candidates_by_ref[candidate_ref_item] = loaded
            self._loaded_candidate_refs.add(candidate_ref_item)
            new_card = candidate_card_from_provider(loaded)
            new_card = new_card.model_copy(update={'ref': candidate_ref_item})
            ws_candidate = self.workspace.candidate_by_ref().get(candidate_ref_item)
            pkg_refs: list[str] = []
            if ws_candidate is not None:
                packages: list[ThreadPackageCard] = []
                # 复用已分配的包 ref（幂等：重复 load 同一候选不重复分配），
                # 对新加载的包（idx 超出已有 ref）分配新 PK<idx>。
                # 修复：search 阶段候选常不带 packages（acgrip search 只返帖子标题），
                # load 阶段才填充包；旧逻辑从空 existing_pkg_refs 取 ref 全得空，
                # 导致 Pi 拿到 PK ref 无法 submit_package。
                existing_pkg_refs = [pkg.ref for pkg in ws_candidate.packages]
                existing_pkg_count = len(existing_pkg_refs)
                for idx, pkg_card in enumerate(new_card.packages):
                    if idx < existing_pkg_count and existing_pkg_refs[idx]:
                        ref = existing_pkg_refs[idx]
                    else:
                        # 基数 = 当前 workspace 全部已有 package ref 数 + 已本轮新分配数 + 1
                        base = len(self.workspace.package_refs) + (idx - existing_pkg_count) + 1
                        ref = f'PK{base}'
                    packages.append(pkg_card.model_copy(update={'ref': ref, 'candidate_ref': candidate_ref_item}))
                    if ref:
                        self.provider_packages_by_ref[ref] = loaded.thread_packages[idx]
                        pkg_refs.append(ref)
                updated = ws_candidate.model_copy(update={
                    'packages': packages,
                    'pages_scanned': new_card.pages_scanned,
                    'pagination_truncated': new_card.pagination_truncated,
                    'has_downloadable_attachment': new_card.has_downloadable_attachment,
                    'packages_loaded': True,
                })
                self.workspace.candidates = [
                    updated if c.ref == candidate_ref_item else c for c in self.workspace.candidates
                ]
            loaded_refs.append(candidate_ref_item)
            all_package_refs.extend(pkg_refs)
            per_candidate.append({'candidate_ref': candidate_ref_item, 'package_refs': pkg_refs})

        base: dict[str, Any] = {
            'candidate_refs': loaded_refs,
            'package_refs': all_package_refs,
            'per_candidate': per_candidate,
            'packages': self.workspace.readable_candidate_cards(),
        }
        if remaining_refs:
            base['remaining_candidate_refs'] = remaining_refs
            base['next_action_hint'] = (
                f'{len(remaining_refs)} candidate(s) not loaded this turn (batch limit '
                f'{_LOAD_CANDIDATE_BATCH_LIMIT}). Inspect the loaded candidates first; '
                f'if none matches the arc, call load_candidate_packages again with the remaining refs.'
            )
        return {
            'ok': True,
            'accepted': False,
            'status': 'packages_loaded',
            **base,
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
        bangumi_subject_id: int = 0,
    ) -> dict[str, Any]:
        decision = AutoFetchDecision(
            disposition='select_candidate',
            candidate_ref=str(candidate_ref or ''),
            bangumi_subject_id=int(bangumi_subject_id or 0),
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
        # 多季覆盖：把 Pi 声明的 bangumi_subject_id 存进 candidate card，
        # 供后续 submit_package 据此给 selection 记 subject_id（审计 + auto_fetch 下载）。
        if candidate is not None and decision.bangumi_subject_id:
            self.workspace.candidates = [
                c.model_copy(update={'bangumi_subject_id': decision.bangumi_subject_id})
                if c.ref == decision.candidate_ref else c
                for c in self.workspace.candidates
            ]
            candidate = candidate.model_copy(
                update={'bangumi_subject_id': decision.bangumi_subject_id}
            )
        self.final_result = None  # 候选阶段不终止
        return {
            'ok': True,
            'accepted': True,
            'status': 'candidate_accepted',
            'summary': f'Candidate {decision.candidate_ref} accepted; next: load_candidate_packages + submit_package.',
            'candidate_ref': decision.candidate_ref,
            'language': decision.language,
            'bangumi_subject_id': decision.bangumi_subject_id,
            'next_action': 'submit_package',
        }

    # ------------------------------------------------------------------
    # 工具：submit_package（选中楼包，轻 gate → append selection，不落 final）
    # ------------------------------------------------------------------

    def tool_submit_package(
        self,
        package_ref: str,
        reason: str = '',
        link_url: Any = None,
        bangumi_subject_id: Any = None,
    ) -> dict[str, Any]:
        package_ref = str(package_ref or '').strip()
        pkg_index = self.workspace.package_by_ref()
        pkg = pkg_index.get(package_ref)
        candidate_ref = pkg.candidate_ref if pkg else ''
        # Pi 指定具体附件 url（AI-first 附件选择）：包内含多个正片附件时（如前篇 zip
        # + 後篇 7z 分开发在同一楼），Pi 据 link label/filename + post_text 选具体
        # 附件，透传给 provider.download 按此 url 下。固定层不打分选附件。校验 url
        # 必须是该包的某个可下载 link（防 Pi 编造 url）。
        requested_url = str(link_url or '').strip()
        download_url = ''
        if pkg:
            pkg_link_urls = {
                link.url for link in pkg.links if link.is_direct_download and link.url
            }
            if requested_url:
                if requested_url not in pkg_link_urls:
                    return {
                        'ok': False,
                        'accepted': False,
                        'status': 'invalid',
                        'error': (
                            f'link_url not a direct-download link of package '
                            f'{package_ref}; call inspect_package to see its links'
                        ),
                    }
                download_url = requested_url
            elif pkg_link_urls:
                # Pi 未指定：单附件包直接用；多附件包提示 Pi 应指定
                download_url = next(iter(pkg_link_urls))
        decision = AutoFetchDecision(
            disposition='select_package',
            candidate_ref=candidate_ref,
            package_ref=package_ref,
            language='',
            confidence='Medium',
            reason=str(reason or ''),
        )
        candidate = self.workspace.candidate_by_ref().get(candidate_ref)
        selection_key = build_selection_key(
            source=candidate.source if candidate else '',
            detail_url=candidate.detail_url if candidate else '',
            package_id=pkg.package_id if pkg else '',
            download_url=download_url,
        )
        rejected_raw = self.task_data.get(
            'subtitle_auto_fetch_rejected_selection_keys'
        )
        rejected_keys = {
            str(value)
            for value in (
                rejected_raw
                if isinstance(rejected_raw, (list, tuple, set))
                else []
            )
            if str(value)
        }
        if selection_key in rejected_keys:
            issue_code = 'prior_download_language_mismatch'
            verifier_result = CaseVerifierResult(
                passed=False,
                issues=[
                    VerifierIssue(
                        ref=package_ref,
                        issue_code=issue_code,
                        severity='blocked',
                        message=(
                            'This exact candidate/package/link was already '
                            'downloaded or selected; choose a different '
                            'candidate, package, or attachment.'
                        ),
                        related_refs=[package_ref],
                    )
                ],
                summary=issue_code,
            )
            self.decision = decision
            self.verifier_result = verifier_result
            self.submit_rejection_count += 1
            self.last_invalid_submission = {
                'decision': decision.model_dump(mode='json'),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'repair_hints': [
                    'Choose a different candidate, package, or attachment.'
                ],
            }
            self._write_artifacts(decision, verifier_result)
            return {
                'ok': True,
                'accepted': False,
                'status': 'invalid',
                'summary': verifier_result.summary,
                'repair_hints': [
                    'Do not resubmit this package/link. Inspect prior_download_'
                    'feedback and choose another plausible preferred-language '
                    'candidate or fail closed after alternatives are exhausted.'
                ],
                'verifier_result': verifier_result.model_dump(mode='json'),
            }
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
        # subject 归属优先用 Pi 在 submit_package 显式声明的 bangumi_subject_id
        # （与 link_url 配合：前篇 link→319390，後篇 link→352905）。Pi 对同 CD 多次
        # submit_candidate 声明不同 subject 时，candidate.bangumi_subject_id 会被
        # 后声明覆盖，导致 selection subject 错乱（0002 前篇/後篇都被记 319390）。
        # Pi 显式传 bangumi_subject_id 修正此。未传时回退 candidate 值（兼容）。
        candidate_sid = int(getattr(candidate, 'bangumi_subject_id', 0) or 0) if candidate else 0
        try:
            declared_sid = int(bangumi_subject_id) if bangumi_subject_id is not None else 0
        except (TypeError, ValueError):
            declared_sid = 0
        selection_sid = declared_sid or candidate_sid
        # 多季覆盖：append 到 selections（不落 final），等全部 subject 处置完
        # submit_complete 落 final。provider 原始对象已在 provider_packages_by_ref/
        # provider_candidates_by_ref 按 ref 索引，auto_fetch 下载时按 ref 取。
        selection = AutoFetchSelectedCandidate(
            candidate_ref=candidate_ref,
            package_ref=package_ref,
            detail_url=candidate.detail_url if candidate else '',
            title=candidate.title if candidate else '',
            language='',
            download_url=download_url,
            selection_key=selection_key,
            bangumi_subject_id=selection_sid,
        )
        self.selections.append(selection)
        self._write_artifacts(decision, verifier_result)
        covered_subjects = sorted({s.bangumi_subject_id for s in self.selections if s.bangumi_subject_id})
        return {
            'ok': True,
            'accepted': True,
            'status': 'package_selected',
            'summary': (
                f'Package {package_ref} selected (subject {selection.bangumi_subject_id or "?"}); '
                f'{len(self.selections)} selection(s) so far. '
                f'Check remaining uncovered missing videos; search by their subject name '
                f'for more packages, or submit_complete when all covered / no more candidates.'
            ),
            'selected_candidate_ref': candidate_ref,
            'selected_package_ref': package_ref,
            'bangumi_subject_id': selection.bangumi_subject_id,
            'selections_count': len(self.selections),
            'covered_subject_ids': covered_subjects,
            'next_action': 'submit_package_or_submit_complete',
            'verifier_result': verifier_result.model_dump(mode='json'),
        }

    # ------------------------------------------------------------------
    # 工具：submit_complete（多季覆盖全部选完 → 落 final accepted）
    # ------------------------------------------------------------------

    def tool_submit_complete(self, reason: str = '', *, force: bool = False) -> dict[str, Any]:
        """所有 subject 处置完（选中包或搜尽无帖）→ 落 final accepted。

        Verifier gate：selections 非空 + 每 selection 已过 package gate
        （submit_package 时已过）。不强制每 subject 处置（真实合帖让"每 subject
        一选"不成立）。selections 空 → invalid（Pi 没选任何包，应 fail_closed
        而非 submit_complete）。

        ``force=True`` 跳过 uncovered 确认机制（auto 兜底用：Pi 已结束，nudge 无意义，
        直接落 final 避免误走 fail_closed）。
        """
        decision = AutoFetchDecision(
            disposition='submit_complete',
            confidence='Medium',
            reason=str(reason or ''),
        )
        if not self.selections:
            verifier_result = CaseVerifierResult(
                passed=False,
                issues=[VerifierIssue(
                    ref='', issue_code='no_selections',
                    severity='blocked',
                    message='submit_complete requires at least one selection; '
                            'if no package found for any subject, call fail_closed instead.',
                )],
                summary='no_selections',
            )
            self.decision = decision
            self.verifier_result = verifier_result
            self.submit_rejection_count += 1
            self._write_artifacts(decision, verifier_result)
            return {
                'ok': True, 'accepted': False, 'status': 'invalid',
                'summary': 'submit_complete rejected: no selections; use fail_closed if nothing found.',
                'repair_hints': ['Call fail_closed if no package was found for any subject.'],
                'verifier_result': verifier_result.model_dump(mode='json'),
            }
        covered_subjects = sorted(
            {s.bangumi_subject_id for s in self.selections if s.bangumi_subject_id}
        )
        # 多 subject 确认机制：uncovered subject > 0 且 total > 1 且 Pi 还没确认过时，
        # 不落 final，返回提示逼 Pi 再确认"这些 subject 搜过无帖"。第二次 submit_complete
        # （confirmations>=1）才落 final。防 Pi 偷懒选 1 包就停（0042/0062 偶发波动）。
        # 不违反"不强制全处置"：第二次仍可留 uncovered 合格。单 subject 任务不受影响。
        all_subject_ids: set[int] = set()
        for card in self.workspace.missing_videos:
            sid = getattr(card, 'bangumi_subject_id', 0) or 0
            all_subject_ids.add(sid)
        uncovered_subjects = sorted(all_subject_ids - set(covered_subjects))
        total_subjects = len(all_subject_ids)
        if (
            uncovered_subjects
            and total_subjects > 1
            and self.submit_complete_confirmations < 1
            and not force
        ):
            self.submit_complete_confirmations += 1
            self.decision = decision
            hint = (
                f'Still {len(uncovered_subjects)} of {total_subjects} subject(s) '
                f'uncovered (ids: {uncovered_subjects}). You selected '
                f'{len(self.selections)} package(s) covering {covered_subjects}. '
                f'BEFORE finishing, confirm you actually searched each uncovered '
                f"subject's name (search_candidates with that subject's "
                f'subject_name/subject_name_cn) and found no downloadable package. '
                f'If you have not searched them yet, do so now '
                f'(search_candidates + load_candidate_packages + submit_package). '
                f'If you genuinely searched and found no thread/package for them, '
                f'call submit_complete AGAIN to confirm and finish (uncovered is a '
                f'valid outcome). DO NOT stop just because one season is covered.'
            )
            return {
                'ok': True, 'accepted': False, 'status': 'need_confirm',
                'summary': hint,
                'selections_count': len(self.selections),
                'covered_subject_ids': covered_subjects,
                'uncovered_subject_ids': uncovered_subjects,
                'total_subject_count': total_subjects,
                'next_action': 'search_uncovered_or_confirm_submit_complete',
                'repair_hints': [hint],
            }
        verifier_result = CaseVerifierResult(passed=True, issues=[], summary='submit_complete')
        summary = str(reason or f'Pi submitted {len(self.selections)} selection(s) '
                                f'covering subject(s) {covered_subjects}.')
        self.final_result = {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': summary,
            'final_action': 'submit_complete',
            'decision': decision.model_dump(mode='json'),
            'selections': [s.model_dump(mode='json') for s in self.selections],
            'selections_count': len(self.selections),
            'covered_subject_ids': covered_subjects,
            'final_verifier_result': verifier_result.model_dump(mode='json'),
        }
        self.decision = decision
        self.verifier_result = verifier_result
        self._write_artifacts(decision, verifier_result)
        self._write_final_result()
        return {
            'ok': True, 'accepted': True, 'status': 'accepted',
            'summary': summary,
            'selections_count': len(self.selections),
            'covered_subject_ids': covered_subjects,
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
            'prior_download_feedback': _prior_download_feedback(
                self.task_data
            ),
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

def _prior_download_feedback(
    task_data: dict[str, Any],
) -> list[dict[str, Any]]:
    raw = task_data.get('subtitle_auto_fetch_prior_download_feedback')
    if not isinstance(raw, list):
        return []
    allowed = {
        'selection_key',
        'source',
        'title',
        'package_label',
        'attachment_label',
        'actual_languages',
        'preferred_language',
        'outcome',
    }
    return [
        {key: _json_safe(value) for key, value in item.items() if key in allowed}
        for item in raw[:10]
        if isinstance(item, dict)
    ]


def _coerce_string_list(value: Any) -> list[str]:
    """把工具参数里的 list[str] 规范成 list[str]（兼容 None/str/单元素）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item or '') for item in value if str(item or '').strip()]
    return []


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
