"""字幕 Case Agent 本地入口（Phase 2：合同先行，AI 仍走单轮）。

对齐 ``src.rename.case_agent.local_bangumi_entry.run_local_bangumi_case_agent_mapping``
的角色，但字幕语义简单：固定层抽事实（解压字幕 + 已处理任务的目标视频）→
build workspace → 调现有 ``AIClient.analyze_subtitle_mapping`` 单轮 → 把 AI 的
``SubtitleMappingResult`` 翻译成 ``SubtitleMappingDraft``（短 ref 体系）→
``verify_and_compile_subtitle_plan`` 合同校验 → 返回四态结果。

固定层只做事实 + 合同：
- ``subtitle_path`` / ``(task_uuid, video)`` 必须能在固定层事实集合里精确解析到
  SF* / TV* ref；解析不到的行落 ``needs_more_evidence``，由 accounting 拦成
  fail_closed（不自动猜、不自动规则模糊匹配，对齐 ai_force_strict）。
- coverage / duplicate / accounting / 合法目标视频 由 ``SubtitleVerifier`` 裁决。
- 语言归一仍由 processor 注入的 ``language_resolver``（LANGUAGE_MAP）完成，单一来源。

返回::

    {
        'ok': bool,
        'status': 'accepted' | 'fail_closed' | 'invalid' | 'need_confirm',
        'summary': str,
        'snapshot': dict,
        'compiled_plan': CompiledSubtitlePlan | None,
    }

- ``accepted``：合同通过，compiled_plan 非空，可落盘。
- ``fail_closed``：AI 给了草稿但合同不通过（coverage / duplicate / 解析失败 /
  needs_more_evidence 未决）——合格业务结果，不落盘部分匹配。
- ``need_confirm``：AI 未给出任何映射（空 mappings），需人工选择目标任务。
- ``invalid``：实现/合同错误（AI 不可用、未返回结构化结果等）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Sequence

from src.rename.case_agent.models import CaseVerifierResult

from .audit import build_subtitle_case_snapshot
from .evidence_broker import build_target_video_cards
from .models import (
    CompiledSubtitlePlan,
    SubtitleFileCard,
    SubtitleMappingDraft,
    SubtitleMappingRow,
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
# AI 结果 -> SubtitleMappingDraft
# ---------------------------------------------------------------------------

def _translate_ai_result_to_draft(
    *,
    ai_result: Any,
    workspace: SubtitleCaseWorkspace,
) -> SubtitleMappingDraft:
    """把 ``SubtitleMappingResult`` 翻译成 ``SubtitleMappingDraft``（短 ref 体系）。

    AI schema（``src.ai.models.SubtitleMappingResult``）字段：
    ``mappings: [{subtitle_path, task_uuid, video, language}]``，
    ``unmatched_files: [str]``，``confidence``，``reason``。

    翻译规则：
    - mapping 行：解析 subtitle_path -> SF ref，(task_uuid, video) -> TV ref。
      任一解析失败 -> 该行落 ``needs_more_evidence``（不自动猜目标），reason 记录
      哪一侧解析失败，供审计。
    - unmatched 行：subtitle_path -> SF ref；解析失败则跳过（无法定位到字幕事实，
      视为 AI 输出污染，不进 draft；该字幕会因 coverage 缺失被合同拦）。
    - language 缺省：mapping 行若无 language，置 ''，由 verifier 的
      ``missing_language`` issue 拦成 fail_closed（不自动默认 chs）。
    """
    subtitle_index = _build_subtitle_path_index(workspace.subtitle_files)
    target_index = _build_target_index(workspace.target_videos)

    rows: list[SubtitleMappingRow] = []

    mappings = list(getattr(ai_result, 'mappings', []) or [])
    for idx, mapping in enumerate(mappings, start=1):
        subtitle_path = str(getattr(mapping, 'subtitle_path', '') or '')
        task_uuid = str(getattr(mapping, 'task_uuid', '') or '')
        video = str(getattr(mapping, 'video', '') or '')
        language = getattr(mapping, 'language', None)
        language_str = str(language).strip() if language is not None else ''

        subtitle_ref = _resolve_subtitle_ref(subtitle_path, subtitle_index)
        target_ref = _resolve_target_ref(task_uuid, video, target_index)

        if not subtitle_ref or not target_ref:
            miss_parts: list[str] = []
            if not subtitle_ref:
                miss_parts.append(f'subtitle_path="{subtitle_path}" not found')
            if not target_ref:
                miss_parts.append(
                    f'(task_uuid="{task_uuid}", video="{video}") not found'
                )
            rows.append(
                SubtitleMappingRow(
                    row_ref=f'R{idx}',
                    subtitle_ref=subtitle_ref,
                    disposition='needs_more_evidence',
                    target_ref='',
                    language=language_str,
                    reason='unresolved_ai_mapping: ' + '; '.join(miss_parts),
                )
            )
            continue

        rows.append(
            SubtitleMappingRow(
                row_ref=f'R{idx}',
                subtitle_ref=subtitle_ref,
                disposition='map_to_video',
                target_ref=target_ref,
                language=language_str,
                reason=str(getattr(ai_result, 'reason', '') or ''),
            )
        )

    unmatched_files = list(getattr(ai_result, 'unmatched_files', []) or [])
    # row_ref 续编号，避免与 mapping 行冲突
    row_idx = len(rows)
    for unmatched_path in unmatched_files:
        row_idx += 1
        unmatched_path = str(unmatched_path or '')
        subtitle_ref = _resolve_subtitle_ref(unmatched_path, subtitle_index)
        if not subtitle_ref:
            # AI 给的 unmatched 路径在固定层事实里找不到 -> 视为污染，跳过；
            # 真正的字幕会因 coverage 缺失被合同拦成 fail_closed。
            continue
        rows.append(
            SubtitleMappingRow(
                row_ref=f'R{row_idx}',
                subtitle_ref=subtitle_ref,
                disposition='unmatched',
                target_ref='',
                language='',
                reason='ai_unmatched',
            )
        )

    confidence = str(getattr(ai_result, 'confidence', 'Medium') or 'Medium')
    if confidence not in {'High', 'Medium', 'Low'}:
        confidence = 'Medium'
    return SubtitleMappingDraft(
        rows=rows,
        summary=str(getattr(ai_result, 'reason', '') or ''),
        confidence=confidence,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def run_subtitle_case_agent_mapping(
    *,
    subtitle_files: Sequence[Any],
    processed_tasks: Sequence[Mapping[str, object]],
    ai_client: Any,
    source_path: Any,
    language_resolver: LanguageResolver,
    archive_name: str = '',
    archive_structure: Mapping[str, Sequence[str]] | None = None,
    backend: str | None = None,
) -> Dict[str, Any]:
    """字幕 Case Agent 映射入口。

    按 ``subtitle_case_agent_backend`` 配置分发：
    - ``pi``（默认）：Phase 3 Pi 多轮 evidence-driven 后端。
    - ``single_shot``：Phase 2 单轮 ``analyze_subtitle_mapping`` + Verifier 合同。

    Args:
        subtitle_files: extractor 的 ExtractedSubtitle 列表（事实）。
        processed_tasks: 已处理任务列表（事实，含 videos / video_targets）。
        ai_client: ``AIClient`` 实例，需有 ``analyze_subtitle_mapping``（single_shot 用）。
        source_path: 压缩包路径（审计用）。
        language_resolver: ``Callable[[str], tuple[str, bool]]``，把原始语言标签
            归一到 ``(emby_lang, is_simplified)``。由 processor 注入 LANGUAGE_MAP。
        archive_name: 压缩包名（喂 AI）。
        archive_structure: 压缩包结构（喂 AI）。若为 None，入口用 subtitle_files
            自行构建一个最小结构（folder -> [filename]）。
        backend: 显式覆盖后端（``'pi'`` / ``'single_shot'``）。None 时读配置
            ``subtitle_case_agent_backend``。测试可传 ``'single_shot'`` 绕过 Pi sidecar。

    Returns:
        ``{ok, status, summary, snapshot, compiled_plan}``。
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

    # 后端分发：pi = Phase 3 多轮 evidence-driven；否则 Phase 2 单轮 AI + 合同
    backend_value = str(backend or _get_config('subtitle_case_agent_backend', 'pi') or 'pi').strip().casefold()
    if backend_value == 'pi':
        return _run_pi_backend(
            workspace=workspace,
            language_resolver=language_resolver,
            source_path=source_path,
            archive_name=archive_name,
        )
    return _run_single_shot_backend(
        workspace=workspace,
        processed_tasks=processed_tasks,
        ai_client=ai_client,
        language_resolver=language_resolver,
        archive_name=archive_name,
        archive_structure=archive_structure,
    )


def _run_single_shot_backend(
    *,
    workspace: SubtitleCaseWorkspace,
    processed_tasks: Sequence[Mapping[str, object]],
    ai_client: Any,
    language_resolver: LanguageResolver,
    archive_name: str,
    archive_structure: Mapping[str, Sequence[str]] | None,
) -> Dict[str, Any]:
    """Phase 2 单轮后端：调 analyze_subtitle_mapping → 翻译 draft → 合同校验。

    直接用调用方传入的 ``processed_tasks``（含 source_videos / video_targets），
    不从 workspace 反推，避免丢失 local 原始文件名等 AI 证据。
    """
    # 构建 AI 输入
    if archive_structure is None:
        archive_structure = _build_archive_structure_from_cards(workspace.subtitle_files)

    # 调 AI 单轮（直接用调用方的 processed_tasks，保留 source_videos / video_targets）
    try:
        ai_result = ai_client.analyze_subtitle_mapping(
            archive_name=archive_name or workspace.archive_name,
            archive_structure=archive_structure,
            processed_tasks=list(processed_tasks),
        )
    except Exception as exc:  # noqa: BLE001 - 入口要兜住 AI 实现错误
        snapshot = build_subtitle_case_snapshot(
            workspace=workspace,
            draft=None,
            verifier_result=None,
            status='invalid',
            summary=f'ai_call_error: {exc}',
        )
        return {
            'ok': False,
            'status': 'invalid',
            'summary': f'ai_call_error: {exc}',
            'snapshot': snapshot,
            'compiled_plan': None,
        }

    # AI 未启用 / 返回 None：无映射输出，需人工确认
    if ai_result is None or not getattr(ai_result, 'mappings', None):
        snapshot = build_subtitle_case_snapshot(
            workspace=workspace,
            draft=None,
            verifier_result=None,
            status='need_confirm',
            summary='ai returned no mapping',
        )
        return {
            'ok': True,
            'status': 'need_confirm',
            'summary': 'ai returned no mapping',
            'snapshot': snapshot,
            'compiled_plan': None,
        }

    # 翻译成 draft
    draft = _translate_ai_result_to_draft(ai_result=ai_result, workspace=workspace)

    # 合同校验 + 编译
    compiled_plan, verifier_result = verify_and_compile_subtitle_plan(
        subtitle_files=workspace.subtitle_files,
        target_videos=workspace.target_videos,
        draft=draft,
        language_resolver=language_resolver,
    )

    status, summary, ok = _classify_verifier_outcome(
        compiled_plan=compiled_plan,
        verifier_result=verifier_result,
    )

    snapshot = build_subtitle_case_snapshot(
        workspace=workspace,
        draft=draft,
        verifier_result=verifier_result,
        status=status,
        summary=summary,
    )
    return {
        'ok': ok,
        'status': status,
        'summary': summary,
        'snapshot': snapshot,
        'compiled_plan': compiled_plan,
    }


def _get_config(key: str, default: Any = None) -> Any:
    """读配置（延迟 import，避免循环）。"""
    from ...config.config_manager import cm

    try:
        return cm.get_config(key)
    except Exception:
        return default


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


def _build_archive_structure_from_cards(
    subtitle_cards: Sequence[SubtitleFileCard],
) -> Dict[str, List[str]]:
    """无外部 archive_structure 时，从字幕 card 构建最小结构供 AI。"""
    structure: dict[str, list[str]] = {}
    for card in subtitle_cards:
        parent = card.archive_path.rsplit('/', 1)[0] if '/' in card.archive_path else '/'
        structure.setdefault(parent, [])
        structure[parent].append(card.filename or card.archive_path.rsplit('/', 1)[-1])
    return structure
