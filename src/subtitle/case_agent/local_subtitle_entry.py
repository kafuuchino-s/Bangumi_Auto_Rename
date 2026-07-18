"""Subtitle Case Agent entry point backed exclusively by Pi.

The fixed layer builds subtitle and target evidence cards; Pi performs the
evidence-driven mapping and the verifier remains the final contract gate.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Sequence

from src.rename.case_agent.models import CaseVerifierResult

from .audit import build_subtitle_case_snapshot
from .evidence_broker import build_target_video_cards
from .models import (
    CompiledSubtitlePlan,
    SubtitleFileCard,
    SubtitleTargetVideoCard,
    normalize_subtitle_archive_path,
)
from .verifier import verify_and_compile_subtitle_plan
from .workspace import SubtitleCaseWorkspace, build_subtitle_case_workspace


LanguageResolver = Callable[[str], tuple[str, bool]]


# ---------------------------------------------------------------------------
# 事实卡片：从 ExtractedSubtitle 抽 SubtitleFileCard
# ---------------------------------------------------------------------------

def build_subtitle_file_cards(
    subtitle_files: Sequence[Any],
) -> List[SubtitleFileCard]:
    """把 extractor 的 ExtractedSubtitle 列表抽成固定层 SubtitleFileCard。

    ``subtitle_files`` 元素需有 ``archive_path`` / ``filename`` 属性
    （``ExtractedSubtitle`` 或同形 SimpleNamespace 均可）。language_hint 从
    filename 后缀提取（仅作 AI 提示，不参与合同裁决）。
    """
    cards: list[SubtitleFileCard] = []
    for sub in subtitle_files:
        archive_path = normalize_subtitle_archive_path(getattr(sub, 'archive_path', '') or '')
        filename = str(getattr(sub, 'filename', '') or '') or archive_path.rsplit('/', 1)[-1]
        cards.append(
            SubtitleFileCard(
                ref='',  # 由 workspace 分配
                archive_path=archive_path,
                filename=filename,
                language_hint=_extract_language_hint(filename),
            )
        )
    return cards


def _extract_language_hint(filename: str) -> str:
    """从文件名后缀提取语言提示（如 01.chs.ass -> chs）。仅作 AI 上下文提示。"""
    name = str(filename or '').lower()
    if not name:
        return ''
    # 取扩展名前的 stem 段，按常见分隔符拆，命中已知语言标签即返回。
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    tokens = stem.replace('.', ' ').replace('_', ' ').replace('[', ' ').replace(']', ' ')
    known = {'chs', 'sc', 'gb', 'cht', 'tc', 'big5', 'jpn', 'jp', 'ja', 'eng', 'en', 'ko', 'kor'}
    for token in tokens.split():
        if token in known:
            return token
    return ''


# ---------------------------------------------------------------------------
# ref 解析：把 AI 的 subtitle_path / (task_uuid, video) 映射到 SF* / TV*
# ---------------------------------------------------------------------------

def _build_subtitle_path_index(
    subtitle_files: Sequence[SubtitleFileCard],
) -> Dict[str, str]:
    """archive_path（归一化） -> SF ref。精确匹配，不做模糊回退。"""
    index: dict[str, str] = {}
    for card in subtitle_files:
        if card.ref and card.archive_path:
            index[card.archive_path] = card.ref
    return index


def _build_target_index(
    target_videos: Sequence[SubtitleTargetVideoCard],
) -> Dict[tuple[str, str], str]:
    """(task_uuid, video) -> TV ref。精确匹配，不做 split(" - ") 等规则回退。"""
    index: dict[tuple[str, str], str] = {}
    for card in target_videos:
        if card.ref and card.task_uuid and card.video:
            index[(card.task_uuid, card.video)] = card.ref
    return index


def _resolve_subtitle_ref(
    subtitle_path: str,
    subtitle_index: Dict[str, str],
) -> str:
    """把 AI 返回的 subtitle_path 解析到 SF ref。

    归一化后精确匹配固定层 archive_path。解析不到返回 ''（调用方落
    needs_more_evidence，由合同拦成 fail_closed）。
    """
    normalized = normalize_subtitle_archive_path(subtitle_path or '')
    if not normalized:
        return ''
    return subtitle_index.get(normalized, '')


def _resolve_target_ref(
    task_uuid: str,
    video: str,
    target_index: Dict[tuple[str, str], str],
) -> str:
    """把 AI 返回的 (task_uuid, video) 解析到 TV ref。解析不到返回 ''。"""
    task_uuid = str(task_uuid or '').strip()
    video = str(video or '').strip()
    if not task_uuid or not video:
        return ''
    return target_index.get((task_uuid, video), '')


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def run_subtitle_case_agent_mapping(
    *,
    subtitle_files: Sequence[Any],
    processed_tasks: Sequence[Mapping[str, object]],
    source_path: Any,
    language_resolver: LanguageResolver,
    archive_name: str = '',
    backend: str | None = None,
) -> Dict[str, Any]:
    """Run the subtitle Case Agent through the Pi evidence-driven backend.

    ``backend`` is retained as a compatibility keyword; Pi is the only supported
    backend. The old Python single-shot AI path has been removed.

    Returns a four-state result dictionary with the compiled plan when accepted.
    """
    # 1. 固定层事实抽取
    subtitle_cards = build_subtitle_file_cards(subtitle_files)
    target_cards = build_target_video_cards(processed_tasks)
    workspace = build_subtitle_case_workspace(
        archive_name=archive_name or str(source_path or ''),
        subtitle_files=subtitle_cards,
        target_videos=target_cards,
    )

    # 无目标视频空间：无法映射，need_confirm（无已处理任务或任务无视频）
    if not workspace.target_videos:
        snapshot = build_subtitle_case_snapshot(
            workspace=workspace,
            draft=None,
            verifier_result=None,
            status='need_confirm',
            summary='no target videos available',
        )
        return {
            'ok': True,
            'status': 'need_confirm',
            'summary': 'no target videos available',
            'snapshot': snapshot,
            'compiled_plan': None,
        }
    # Pi-only evidence-driven backend。
    return _run_pi_backend(
        workspace=workspace,
        language_resolver=language_resolver,
        source_path=source_path,
        archive_name=archive_name,
    )


def _run_pi_backend(
    *,
    workspace: SubtitleCaseWorkspace,
    language_resolver: LanguageResolver,
    source_path: Any,
    archive_name: str,
) -> Dict[str, Any]:
    """Phase 3 Pi 多轮后端：调 run_subtitle_case_agent_pi，把结果归一到四态 dict。"""
    from .pi_runner import run_subtitle_case_agent_pi

    run_result = run_subtitle_case_agent_pi(
        workspace=workspace,
        language_resolver=language_resolver,
        source_path=source_path,
        archive_name=archive_name or workspace.archive_name,
    )
    status = str(run_result.status or 'invalid')
    # invalid 包含 pi_runtime_failed / pi_no_final_result / error
    if status not in {'accepted', 'fail_closed', 'need_confirm', 'invalid'}:
        status = 'invalid'
    ok = bool(run_result.ok) if status == 'accepted' else (status != 'invalid' or run_result.ok)
    snapshot = build_subtitle_case_snapshot(
        workspace=workspace,
        draft=run_result.mapping_draft,
        verifier_result=run_result.final_verifier_result,
        status=status,
        summary=run_result.summary,
        case_agent_mode='pi_subtitle_case_agent',
    )
    # 把 Pi 运行审计信息并进 snapshot，供任务 JSON 审计
    snapshot['pi_run'] = {
        'ok': bool(run_result.ok),
        'status': run_result.status,
        'sample_id': run_result.sample_id,
        'final_action': run_result.final_action,
        'errors': list(run_result.errors),
        'tool_trace_count': len(run_result.tool_trace),
        'tool_call_counts': dict(run_result.tool_call_counts),
        'tool_sequence': list(run_result.tool_sequence),
        'submit_rejection_count': run_result.submit_rejection_count,
        'pi_command': run_result.pi_command,
        'pi_provider': run_result.pi_provider,
        'pi_model': run_result.pi_model,
        'runtime_returncode': run_result.runtime_returncode,
    }
    compiled_plan = run_result.compiled_plan if status == 'accepted' else None
    return {
        'ok': ok,
        'status': status,
        'summary': run_result.summary,
        'snapshot': snapshot,
        'compiled_plan': compiled_plan,
    }


def _classify_verifier_outcome(
    *,
    compiled_plan: CompiledSubtitlePlan | None,
    verifier_result: CaseVerifierResult,
) -> tuple[str, str, bool]:
    """把 (compiled_plan, verifier_result) 归到四态。"""
    if compiled_plan is not None and verifier_result.passed:
        return 'accepted', 'accepted subtitle mapping plan', True
    # 合同不通过：合格业务结果 fail_closed
    issue_codes = [issue.issue_code for issue in verifier_result.issues]
    summary = (
        f'fail_closed: {len(verifier_result.issues)} blocking issue(s): '
        f'{", ".join(sorted(set(issue_codes)))}'
    )
    return 'fail_closed', summary, True
