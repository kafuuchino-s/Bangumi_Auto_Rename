"""字幕自动抓取 Case Agent 本地入口（Phase 2：合同先行，AI 仍走单轮）。

对齐 ``subtitle.case_agent.local_subtitle_entry.run_subtitle_case_agent_mapping`` 的
角色，但 auto_fetch 是 candidate ranking（选帖/选包），不是 mapping。固定层抽事实
（scan_scope + missing_videos + 当前关键词的候选/楼包）→ build workspace → 调现有
``AIClient.choose_subtitle_candidate`` / ``choose_subtitle_thread_package`` 单轮 →
把 AI 决策翻译成 ``AutoFetchDecision`` → ``verify_auto_fetch_decision`` 轻 gate
校验 → 返回四态结果。

固定层只做事实 + 轻 gate：
- candidate_ref / package_ref 必须能在固定层事实集合里精确解析到 CD* / PK* ref。
- 候选含可下载附件 / 楼包非 font-patch-only 由轻 gate 裁决。
- arc 归属 / 版本语言歧义 / 正片 vs 特典 这类不确定判断交 AI。

返回::

    {
        'ok': bool,
        'status': 'accepted' | 'fail_closed' | 'invalid' | 'need_confirm',
        'summary': str,
        'snapshot': dict,
        'decision': AutoFetchDecision | None,
        'selected_candidate': dict,   # accepted 时供 auto_fetch 下载
        'selected_package': dict,
    }

- ``accepted``：轻 gate 通过，selected_candidate/package 非空，可下载+落 processor。
- ``fail_closed``：AI 拒绝所有候选（``should_use=False``）或搜不到正片——合格
  业务结果，触发换关键词重试。
- ``need_confirm``：AI 未给出可用决策（空选/不可用），需人工。
- ``invalid``：实现错误（AI 不可用、未返回结构化结果等）。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from src.rename.case_agent.models import CaseVerifierResult

from .audit import build_auto_fetch_case_snapshot
from .evidence_broker import candidate_card_from_provider
from .models import (
    AutoFetchDecision,
    AutoFetchSelectedCandidate,
    CandidateCard,
    ThreadPackageCard,
)
from .verifier import auto_fetch_repair_hints, verify_auto_fetch_decision
from .workspace import AutoFetchCaseWorkspace


# ---------------------------------------------------------------------------
# 事实卡片：从 provider SubtitleCandidate 抽 CandidateCard
# ---------------------------------------------------------------------------

def build_candidate_cards(
    candidates: "list[Any]",
) -> list[CandidateCard]:
    """把 provider 的 SubtitleCandidate 列表抽成固定层 CandidateCard（不入 workspace）。"""
    return [candidate_card_from_provider(c) for c in candidates]


# ---------------------------------------------------------------------------
# AI 决策 -> AutoFetchDecision
# ---------------------------------------------------------------------------

def _build_select_candidate_decision(
    *,
    ai_choice: Any,
    workspace: AutoFetchCaseWorkspace,
) -> AutoFetchDecision | None:
    """把 ``choose_subtitle_candidate`` 的结果翻译成 select_candidate 决策。

    AI schema（``SubtitleCandidateDecision``）：``selected_index`` / ``should_use`` /
    ``confidence`` / ``language_assessment`` / ``reason``。
    ``should_use=False`` → 返回 ``None``，调用方落 fail_closed（AI 拒绝）。
    """
    if ai_choice is None:
        return None
    if not getattr(ai_choice, 'should_use', False):
        return None
    selected_index = getattr(ai_choice, 'selected_index', None)
    if not isinstance(selected_index, int) or selected_index < 0:
        return None
    candidate_refs = workspace.candidate_refs
    if selected_index >= len(candidate_refs):
        return None
    candidate_ref = candidate_refs[selected_index]
    language = str(getattr(ai_choice, 'language_assessment', '') or '').strip()
    return AutoFetchDecision(
        disposition='select_candidate',
        candidate_ref=candidate_ref,
        language=language,
        confidence=str(getattr(ai_choice, 'confidence', 'Medium') or 'Medium'),  # type: ignore[arg-type]
        reason=str(getattr(ai_choice, 'reason', '') or ''),
    )


def _build_select_package_decision(
    *,
    ai_choice: Any,
    candidate: CandidateCard,
) -> AutoFetchDecision | None:
    """把 ``choose_subtitle_thread_package`` 的结果翻译成 select_package 决策。"""
    if ai_choice is None:
        return None
    if not getattr(ai_choice, 'should_use', False):
        return None
    selected_index = getattr(ai_choice, 'selected_index', None)
    if not isinstance(selected_index, int) or selected_index < 0:
        return None
    package_refs = [pkg.ref for pkg in candidate.packages if pkg.ref]
    if selected_index >= len(package_refs):
        return None
    package_ref = package_refs[selected_index]
    return AutoFetchDecision(
        disposition='select_package',
        candidate_ref=candidate.ref,
        package_ref=package_ref,
        confidence=str(getattr(ai_choice, 'confidence', 'Medium') or 'Medium'),  # type: ignore[arg-type]
        reason=str(getattr(ai_choice, 'reason', '') or ''),
    )


def _ai_choice_to_dict(ai_choice: Any) -> dict[str, Any]:
    """把 AI 决策对象转成 dict。兼容 pydantic model_dump（支持 mode）与
    测试 stub 的无参 model_dump/普通对象。"""
    if ai_choice is None:
        return {}
    model_dump = getattr(ai_choice, 'model_dump', None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode='json')
        except TypeError:
            try:
                dumped = model_dump()
            except TypeError:
                dumped = None
        if isinstance(dumped, dict):
            return dumped
    # 兜底：从属性手动抽
    keys = ('selected_index', 'should_use', 'confidence',
            'language_assessment', 'reason', 'warnings')
    out: dict[str, Any] = {}
    for key in keys:
        if hasattr(ai_choice, key):
            out[key] = getattr(ai_choice, key)
    return out


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _run_pi_backend(
    *,
    workspace: AutoFetchCaseWorkspace,
    candidates: "list[Any]",
    task_data: Mapping[str, object],
    ai_client: Any,
    candidate_summaries: "list[dict[str, Any]]",
    provider: Any = None,
) -> dict[str, Any]:
    """Pi 多轮 evidence-driven 后端（Phase 3）。

    把预搜索的候选注入 workspace 作初始上下文，调
    ``run_auto_fetch_case_agent_pi`` 让 Pi sidecar 多轮调查（AI 可再调
    search_candidates/load_candidate_packages/inspect_package 取证）→ submit。
    运行结果归一到四态 dict。

    单测用 ``runtime_invoker`` / fake env 注入，不真起 node。
    """
    from .pi_runner import run_auto_fetch_case_agent_pi

    # 注入预搜索候选作初始事实卡
    for candidate in candidates:
        card = candidate_card_from_provider(candidate)
        workspace.add_candidate(card)

    run_result = run_auto_fetch_case_agent_pi(
        workspace=workspace,
        provider=provider,
        task_data=dict(task_data),
        source_label=str(workspace.task_uuid or ''),
    )
    status = str(run_result.status or 'invalid')
    snapshot_data: dict[str, Any] = {
        'pi_run': {
            'ok': run_result.ok,
            'status': run_result.status,
            'sample_id': run_result.sample_id,
            'final_action': run_result.final_action,
            'errors': list(run_result.errors),
            'tool_call_counts': dict(run_result.tool_call_counts),
            'tool_sequence': list(run_result.tool_sequence),
            'submit_rejection_count': run_result.submit_rejection_count,
            'selected_candidate_ref': run_result.selected_candidate_ref,
            'selected_package_ref': run_result.selected_package_ref,
            'pi_provider': run_result.pi_provider,
            'pi_model': run_result.pi_model,
        },
    }
    if run_result.decision is not None:
        snapshot_data['decision'] = run_result.decision.model_dump(mode='json')
    if run_result.final_verifier_result is not None:
        snapshot_data['verifier'] = run_result.final_verifier_result.model_dump(mode='json')

    if status == 'accepted':
        candidate_card = workspace.candidate_by_ref().get(run_result.selected_candidate_ref)
        package_card = workspace.package_by_ref().get(run_result.selected_package_ref)
        selected = AutoFetchSelectedCandidate(
            candidate_ref=run_result.selected_candidate_ref,
            package_ref=run_result.selected_package_ref,
            detail_url=candidate_card.detail_url if candidate_card else '',
            title=candidate_card.title if candidate_card else '',
            language=str(run_result.decision.language if run_result.decision else ''),
            download_url=_first_downloadable_url(package_card) if package_card else '',
        )
        return {
            'ok': True,
            'status': 'accepted',
            'summary': run_result.summary,
            'snapshot': snapshot_data,
            'decision': run_result.decision.model_dump(mode='json') if run_result.decision else None,
            'selected_candidate': selected.model_dump(mode='json'),
            'selected_candidate_ref': run_result.selected_candidate_ref,
            'selected_package_ref': run_result.selected_package_ref,
        }
    if status == 'fail_closed':
        return {
            'ok': True,
            'status': 'fail_closed',
            'summary': run_result.summary,
            'snapshot': snapshot_data,
            'decision': run_result.decision.model_dump(mode='json') if run_result.decision else None,
            'reason_kind': 'pi_fail_closed',
        }
    if status == 'need_confirm':
        return {
            'ok': True,
            'status': 'need_confirm',
            'summary': run_result.summary,
            'snapshot': snapshot_data,
            'decision': run_result.decision.model_dump(mode='json') if run_result.decision else None,
        }
    # invalid / error
    return {
        'ok': False,
        'status': 'invalid',
        'summary': run_result.summary,
        'snapshot': snapshot_data,
        'decision': None,
    }


def run_auto_fetch_case_agent(
    *,
    workspace: AutoFetchCaseWorkspace,
    candidates: "list[Any]",
    task_data: Mapping[str, object],
    ai_client: Any,
    candidate_summaries: "list[dict[str, Any]]",
    backend: str = 'single_shot',
    provider: Any = None,
) -> dict[str, Any]:
    """单次关键词下的候选→选包决策（四态）。

    ``candidates`` 是 provider 返回的 SubtitleCandidate 列表（已 prepare +
    load_thread_packages）。本 entry 把它们注入 workspace 成事实卡，调 AI 选帖→
    选包→轻 gate 校验。

    关键词循环 / 重试由 ``auto_fetch.process_task`` 编排（薄入口分发），本 entry
    只负责"给定这批候选，AI 选哪个 + 轻 gate 校验"。

    Args:
        backend: ``single_shot``（Phase 2 单轮 AI）；``pi`` 走 Phase 3 Pi 多轮
            后端（需传 ``provider`` 供 sidecar 主动取证）。
        provider: Pi 后端取证用（search/prepare/load_thread_packages）；single_shot
            不需要。
    """
    if backend == 'pi':
        # Phase 3：Pi 多轮 evidence-driven 后端
        return _run_pi_backend(
            workspace=workspace,
            candidates=candidates,
            task_data=task_data,
            ai_client=ai_client,
            candidate_summaries=candidate_summaries,
            provider=provider,
        )

    # 注入候选事实卡到 workspace
    for candidate in candidates:
        card = candidate_card_from_provider(candidate)
        workspace.add_candidate(card)

    if not workspace.candidates:
        return _fail_closed_result(
            workspace=workspace,
            decision=None,
            verifier_result=None,
            summary='搜索无候选帖',
            reason_kind='no_candidates',
        )

    if not ai_client or not getattr(ai_client, 'is_available', lambda: False)():
        return _invalid_result(
            workspace=workspace,
            summary='AI 不可用，auto_fetch Case Agent 单轮后端无法决策',
        )

    # Step 1: 选候选帖
    ai_candidate_choice = ai_client.choose_subtitle_candidate(
        dict(task_data), candidate_summaries
    )
    candidate_decision = _build_select_candidate_decision(
        ai_choice=ai_candidate_choice, workspace=workspace
    )
    if candidate_decision is None:
        # AI 拒绝（should_use=False）或空 → fail_closed（合格，换关键词重试）
        rejection = bool(
            ai_candidate_choice is not None
            and getattr(ai_candidate_choice, 'should_use', None) is False
        )
        return _fail_closed_result(
            workspace=workspace,
            decision=None,
            verifier_result=None,
            summary='AI 拒绝所有候选帖' if rejection else 'AI 未给出可用候选决策',
            reason_kind='candidate_ai_rejected' if rejection else 'no_usable_candidate',
            ai_rerank_result=_ai_choice_to_dict(ai_candidate_choice),
        )

    # 轻 gate 校验选帖
    candidate_verifier = verify_auto_fetch_decision(
        workspace=workspace, decision=candidate_decision
    )
    if not candidate_verifier.passed:
        return _fail_closed_result(
            workspace=workspace,
            decision=candidate_decision,
            verifier_result=candidate_verifier,
            summary='选中候选帖未通过轻 gate',
            reason_kind='candidate_gate_rejected',
            ai_rerank_result=_ai_choice_to_dict(ai_candidate_choice),
        )

    selected_candidate_card = workspace.candidate_by_ref().get(
        candidate_decision.candidate_ref
    )
    if selected_candidate_card is None:
        return _invalid_result(
            workspace=workspace,
            summary='选中候选 ref 解析失败',
        )

    # Step 2: 选楼包
    package_summaries = _package_summaries_from_card(selected_candidate_card)
    ai_package_choice = ai_client.choose_subtitle_thread_package(
        dict(task_data),
        _candidate_card_to_dict(selected_candidate_card),
        package_summaries,
    )
    package_decision = _build_select_package_decision(
        ai_choice=ai_package_choice, candidate=selected_candidate_card
    )
    if package_decision is None:
        rejection = bool(
            ai_package_choice is not None
            and getattr(ai_package_choice, 'should_use', None) is False
        )
        if rejection:
            # AI 显式拒绝包：合格跳过（skipped 语义由调用方映射），换关键词
            return _fail_closed_result(
                workspace=workspace,
                decision=candidate_decision,
                verifier_result=None,
                summary='AI 拒绝所有楼包（package_ai_rejected）',
                reason_kind='package_ai_rejected',
                ai_rerank_result=_ai_choice_to_dict(ai_candidate_choice),
                package_ai_result=_ai_choice_to_dict(ai_package_choice),
                selected_candidate_ref=candidate_decision.candidate_ref,
            )
        return _need_confirm_result(
            workspace=workspace,
            decision=candidate_decision,
            verifier_result=None,
            summary='AI 未给出可用楼包决策',
            ai_rerank_result=_ai_choice_to_dict(ai_candidate_choice),
            package_ai_result=_ai_choice_to_dict(ai_package_choice),
        )

    # 轻 gate 校验选包
    package_verifier = verify_auto_fetch_decision(
        workspace=workspace, decision=package_decision
    )
    if not package_verifier.passed:
        return _fail_closed_result(
            workspace=workspace,
            decision=package_decision,
            verifier_result=package_verifier,
            summary='选中楼包未通过轻 gate',
            reason_kind='package_gate_rejected',
            ai_rerank_result=_ai_choice_to_dict(ai_candidate_choice),
            package_ai_result=_ai_choice_to_dict(ai_package_choice),
        )

    selected_package_card = workspace.package_by_ref().get(package_decision.package_ref)
    if selected_package_card is None:
        return _invalid_result(
            workspace=workspace,
            summary='选中楼包 ref 解析失败',
        )

    # accepted：构造选中结果
    selected = AutoFetchSelectedCandidate(
        candidate_ref=selected_candidate_card.ref,
        package_ref=selected_package_card.ref,
        detail_url=selected_candidate_card.detail_url,
        title=selected_candidate_card.title,
        language=package_decision.language or candidate_decision.language,
        download_url=_first_downloadable_url(selected_package_card),
    )
    snapshot = build_auto_fetch_case_snapshot(
        workspace=workspace,
        decision=package_decision,
        verifier_result=package_verifier,
        status='accepted',
        summary='AI 选中候选帖 + 楼包，轻 gate 通过',
    )
    return {
        'ok': True,
        'status': 'accepted',
        'summary': snapshot['summary'],
        'snapshot': snapshot,
        'decision': package_decision.model_dump(mode='json'),
        'selected_candidate': selected.model_dump(mode='json'),
        'selected_candidate_ref': selected_candidate_card.ref,
        'selected_package_ref': selected_package_card.ref,
        'ai_rerank_result': _ai_choice_to_dict(ai_candidate_choice),
        'package_ai_result': _ai_choice_to_dict(ai_package_choice),
    }


# ---------------------------------------------------------------------------
# 四态结果构造
# ---------------------------------------------------------------------------

def _fail_closed_result(
    *,
    workspace: AutoFetchCaseWorkspace,
    decision: AutoFetchDecision | None,
    verifier_result: CaseVerifierResult | None,
    summary: str,
    reason_kind: str = 'insufficient_evidence',
    ai_rerank_result: dict[str, Any] | None = None,
    package_ai_result: dict[str, Any] | None = None,
    selected_candidate_ref: str = '',
) -> dict[str, Any]:
    snapshot = build_auto_fetch_case_snapshot(
        workspace=workspace,
        decision=decision,
        verifier_result=verifier_result,
        status='fail_closed',
        summary=summary,
    )
    snapshot['reason_kind'] = reason_kind
    if ai_rerank_result is not None:
        snapshot['ai_rerank_result'] = ai_rerank_result
    if package_ai_result is not None:
        snapshot['package_ai_result'] = package_ai_result
    result: dict[str, Any] = {
        'ok': True,
        'status': 'fail_closed',
        'summary': summary,
        'snapshot': snapshot,
        'decision': decision.model_dump(mode='json') if decision else None,
        'reason_kind': reason_kind,
    }
    if selected_candidate_ref:
        result['selected_candidate_ref'] = selected_candidate_ref
    if ai_rerank_result is not None:
        result['ai_rerank_result'] = ai_rerank_result
    if package_ai_result is not None:
        result['package_ai_result'] = package_ai_result
    return result


def _need_confirm_result(
    *,
    workspace: AutoFetchCaseWorkspace,
    decision: AutoFetchDecision | None,
    verifier_result: CaseVerifierResult | None,
    summary: str,
    ai_rerank_result: dict[str, Any] | None = None,
    package_ai_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = build_auto_fetch_case_snapshot(
        workspace=workspace,
        decision=decision,
        verifier_result=verifier_result,
        status='need_confirm',
        summary=summary,
    )
    if ai_rerank_result is not None:
        snapshot['ai_rerank_result'] = ai_rerank_result
    if package_ai_result is not None:
        snapshot['package_ai_result'] = package_ai_result
    result: dict[str, Any] = {
        'ok': True,
        'status': 'need_confirm',
        'summary': summary,
        'snapshot': snapshot,
        'decision': decision.model_dump(mode='json') if decision else None,
    }
    if ai_rerank_result is not None:
        result['ai_rerank_result'] = ai_rerank_result
    if package_ai_result is not None:
        result['package_ai_result'] = package_ai_result
    return result


def _invalid_result(
    *,
    workspace: AutoFetchCaseWorkspace,
    summary: str,
) -> dict[str, Any]:
    snapshot = build_auto_fetch_case_snapshot(
        workspace=workspace,
        decision=None,
        verifier_result=None,
        status='invalid',
        summary=summary,
    )
    return {
        'ok': False,
        'status': 'invalid',
        'summary': summary,
        'snapshot': snapshot,
        'decision': None,
    }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _package_summaries_from_card(candidate: CandidateCard) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for index, pkg in enumerate(candidate.packages):
        link_summary = ' | '.join(
            filter(
                None,
                [link.filename_hint or link.label or link.url for link in pkg.links[:5]],
            )
        )
        summaries.append(
            {
                'index': index,
                'package_id': pkg.package_id,
                'page_number': pkg.page_number,
                'floor_label': pkg.floor_label,
                'post_author': pkg.post_author,
                'post_time': pkg.post_time,
                'has_direct_download': pkg.has_direct_download,
                'package_flags': list(pkg.package_flags),
                'post_text': pkg.post_text,
                'context_text': pkg.context_text,
                'link_summary': link_summary,
                'link_count': len(pkg.links),
            }
        )
    return summaries


def _candidate_card_to_dict(candidate: CandidateCard) -> dict[str, Any]:
    return {
        'title': candidate.title,
        'detail_url': candidate.detail_url,
        'source': candidate.source,
        'snippet': candidate.snippet,
        'pages_scanned': candidate.pages_scanned,
        'pagination_truncated': candidate.pagination_truncated,
        'has_downloadable_attachment': candidate.has_downloadable_attachment,
        'packages': [
            {
                'package_id': pkg.package_id,
                'page_number': pkg.page_number,
                'floor_label': pkg.floor_label,
                'post_text': pkg.post_text,
                'context_text': pkg.context_text,
                'has_direct_download': pkg.has_direct_download,
                'package_flags': list(pkg.package_flags),
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
            }
            for pkg in candidate.packages
        ],
    }


def _first_downloadable_url(package: ThreadPackageCard) -> str:
    for link in package.links:
        if link.is_direct_download and link.url:
            return link.url
    return ''
