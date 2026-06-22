"""字幕自动抓取 Case Agent 本地入口（Pi evidence-driven 后端）。

对齐 ``subtitle.case_agent.local_subtitle_entry.run_subtitle_case_agent_mapping`` 的
角色，但 auto_fetch 是 candidate ranking（选帖/选包），不是 mapping。固定层抽事实
（scan_scope + missing_videos（含 BGM subject 名）+ 确定性关键词变体）→ build
workspace → 调 ``_run_pi_backend`` → ``run_auto_fetch_case_agent_pi``：Pi sidecar
自己 ``search_candidates_batch(BGM 名)`` / ``load_candidate_packages_batch`` /
``inspect_package`` 多轮取证后 ``submit_candidate`` / ``submit_package``，
``verify_auto_fetch_decision`` 轻 gate 校验 → 返回四态结果。

**single_shot 已移除**：auto_fetch 选帖/选包统一走 Pi evidence-driven 后端，
``backend`` 参数保留兼容但忽略（始终 Pi）；``ai_client`` / ``candidate_summaries``
保留兼容但 Pi 后端不用（Pi sidecar 自带 AI）。

固定层只做事实 + 轻 gate：
- candidate_ref / package_ref 必须能在固定层事实集合里精确解析到 CD* / PK* ref。
- 候选含可下载附件 / 楼包非 font-patch-only 由轻 gate 裁决。
- arc 归属 / 版本语言歧义 / 正片 vs 特典 这类不确定判断交 Pi（AI 多轮取证）。

返回::

    {
        'ok': bool,
        'status': 'accepted' | 'fail_closed' | 'invalid' | 'need_confirm',
        'summary': str,
        'snapshot': dict,           # 含 pi_run（工具调用统计 / 选定 refs / verifier）
        'decision': AutoFetchDecision | None,
        'selected_candidate': dict, # accepted 时供 auto_fetch 下载
        'selected_package': dict,
        'selected_provider_candidate': Any,  # provider 原始对象（sidecar tool_state）
        'selected_provider_package': Any,
    }

- ``accepted``：Pi submit_package 过轻 gate，selected_candidate/package 非空，可下载+落 processor。
- ``fail_closed``：Pi 判定无匹配候选/包（``fail_closed``）或搜不到正片——合格
  业务结果，``reason_kind='pi_fail_closed'``，auto_fetch 据此合格跳过（单次结束，无外层换词重试；Pi 内部可试多个 BGM 名变体）。
- ``need_confirm``：Pi 不定选哪个，需人工。
- ``invalid``：实现错误（Pi runtime 失败、无 final result 等）。
"""

from __future__ import annotations

from typing import Any, Mapping

from .evidence_broker import candidate_card_from_provider
from .models import (
    AutoFetchSelectedCandidate,
    CandidateCard,
    ThreadPackageCard,
)
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
    """Pi evidence-driven 后端（Pi 驱动爬取，对齐重命名链路）。

    **不预注入候选**：Pi sidecar 自己调 ``search_candidates_batch(BGM 名)`` /
    ``load_candidate_packages_batch`` / ``inspect_package`` 多轮取证后 submit。
    Python 主进程只传 provider + workspace（MV/KW 事实卡含 BGM 名），不预爬。
    ``candidates`` 参数保留兼容但 Pi 驱动模式下应为空（传非空时仍注入作初始上下文，
    仅供测试/兼容，生产 Pi 驱动不预喂）。

    运行结果归一到四态 dict。单测用 ``runtime_invoker`` / fake env 注入，不真起 node。
    """
    from .pi_runner import run_auto_fetch_case_agent_pi

    # Pi 驱动：默认不预注入候选（Pi 自己 search_candidates_batch 注入）。
    # 仅当调用方显式传入 candidates（测试/兼容）时注入作初始上下文。
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
        # 多季覆盖：把 run_result.selections（每 subject 一条）+ provider 原始对象
        # 透传给 auto_fetch，主进程逐条下载 + processor 配对后合并 mapping。
        # 旧单 submit_package 路径 selections 为空 → 透传空，auto_fetch 走单 selection 兼容。
        selections_serialized = [s.model_dump(mode='json') for s in run_result.selections]
        return {
            'ok': True,
            'status': 'accepted',
            'summary': run_result.summary,
            'snapshot': snapshot_data,
            'decision': run_result.decision.model_dump(mode='json') if run_result.decision else None,
            'selected_candidate': selected.model_dump(mode='json'),
            'selected_candidate_ref': run_result.selected_candidate_ref,
            'selected_package_ref': run_result.selected_package_ref,
            # provider 原始对象供 auto_fetch 主进程下载（Pi 驱动爬取后对象在 sidecar tool_state）
            'selected_provider_candidate': run_result.selected_provider_candidate,
            'selected_provider_package': run_result.selected_provider_package,
            # 多季覆盖：多 selection 列表 + 每条对应 (candidate, package) provider 对象
            'selections': selections_serialized,
            'selections_provider': [
                {'candidate': cand, 'package': pkg}
                for cand, pkg in run_result.selections_provider
            ],
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
    backend: str = 'pi',
    provider: Any = None,
) -> dict[str, Any]:
    """Pi 驱动选帖/选包决策（四态，对齐重命名链路）。

    **single_shot 已移除**：auto_fetch 选帖/选包统一走 Pi evidence-driven 后端。
    Pi sidecar 自己调 ``search_candidates_batch(BGM 名)`` / ``load_candidate_packages_batch``
    / ``inspect_package`` 多轮取证后 submit，Python 主进程不预爬。

    ``candidates`` 参数保留兼容（测试注入初始候选），生产 Pi 驱动模式传空。
    ``backend`` 参数保留兼容但忽略（始终 Pi）；``ai_client``/``candidate_summaries``
    保留兼容但 Pi 后端不用。

    Args:
        provider: Pi 后端取证用（search/prepare/load_thread_packages），必传。
    """
    return _run_pi_backend(
        workspace=workspace,
        candidates=candidates,
        task_data=task_data,
        ai_client=ai_client,
        candidate_summaries=candidate_summaries,
        provider=provider,
    )


def _first_downloadable_url(package: ThreadPackageCard) -> str:
    for link in package.links:
        if link.is_direct_download and link.url:
            return link.url
    return ''
