"""字幕映射合同校验器。

对齐 ``src.rename.bgm_to_tmdb.verifier`` 的形状：复用
``CaseVerifierResult`` / ``VerifierIssue``，返回 passed + issues + summary。

固定层只做确定性、可验证的事情：
- coverage：每个解压字幕必须在 draft 中出现一次（map_to_video / unmatched）。
- duplicate_source：一个字幕 ref 不能在 draft 中出现两次。
- duplicate_target：同一 (target_ref) 除非语言不同，否则不能被重复覆盖。
  （同一视频允许挂多语言字幕，但同语言重复视为冲突。）
- valid_target：map_to_video 行的 target_ref 必须是固定层暴露的合法 TV* ref，
  且 language 非空。
- content_language: 高置信字幕正文繁简事实与 draft 中文语言标签不得冲突。
- accounting：mapped + unmatched + needs == subtitle_count，且 needs == 0 才
  accepted readiness。
- unknown_ref：subtitle_ref / target_ref 必须在固定层事实集合内。

不确定判断（候选归属、版本/语言歧义、跨季归属）不在此层裁决，交由 AI 通过
draft 的 needs_more_evidence / unmatched + reason 表达，或经 Case Agent
evidence request 引导。
"""

from __future__ import annotations

from collections import Counter, defaultdict

from src.rename.case_agent.models import CaseVerifierResult, VerifierIssue

from ..language import normalize_language

from .mapping_draft import compute_subtitle_mapping_accounting
from .models import (
    CompiledSubtitleMapping,
    CompiledSubtitlePlan,
    CompiledUnmatchedEntry,
    SubtitleFileCard,
    SubtitleMappingDraft,
    SubtitleTargetVideoCard,
    is_subtitle_ref,
    is_target_ref,
)


def verify_subtitle_mapping_draft(
    *,
    subtitle_files: list[SubtitleFileCard],
    target_videos: list[SubtitleTargetVideoCard],
    draft: SubtitleMappingDraft,
) -> CaseVerifierResult:
    """校验字幕映射草稿，返回合同结果。"""
    issues = _collect_issues(subtitle_files, target_videos, draft)
    blocking = [issue for issue in issues if issue.severity == 'blocked']
    return CaseVerifierResult(
        passed=not blocking,
        issues=issues,
        summary='accepted' if not blocking else f'{len(blocking)} blocking subtitle mapping issue(s)',
    )


def verify_and_compile_subtitle_plan(
    *,
    subtitle_files: list[SubtitleFileCard],
    target_videos: list[SubtitleTargetVideoCard],
    draft: SubtitleMappingDraft,
    language_resolver,
) -> tuple[CompiledSubtitlePlan | None, CaseVerifierResult]:
    """校验草稿，通过则编译为可落盘的 CompiledSubtitlePlan。

    Args:
        language_resolver: ``Callable[[str], tuple[str, bool]]``，把原始语言标签
            归一到 ``(emby_lang, is_simplified)``。由 processor 注入
            ``LANGUAGE_MAP``-based resolver，保持语言归一逻辑单一来源。
    """
    result = verify_subtitle_mapping_draft(
        subtitle_files=subtitle_files,
        target_videos=target_videos,
        draft=draft,
    )
    if not result.passed:
        return None, result

    subtitle_by_ref = {card.ref: card for card in subtitle_files if card.ref}
    target_by_ref = {card.ref: card for card in target_videos if card.ref}
    compiled_mappings: list[CompiledSubtitleMapping] = []
    unmatched_entries: list[CompiledUnmatchedEntry] = []
    for row in draft.rows:
        if row.disposition == 'unmatched':
            unmatched_entries.append(
                CompiledUnmatchedEntry(
                    ref=row.subtitle_ref,
                    reason_kind=row.unmatched_reason_kind or 'unknown',
                    reason=row.reason or '',
                )
            )
            continue
        if row.disposition != 'map_to_video':
            # needs_more_evidence 已被 accounting 校验拦在 passed 之外，这里防御。
            continue
        subtitle_card = subtitle_by_ref.get(row.subtitle_ref)
        target_card = target_by_ref.get(row.target_ref)
        if subtitle_card is None or target_card is None:
            continue
        emby_lang, is_simplified = language_resolver(row.language)
        compiled_mappings.append(
            CompiledSubtitleMapping(
                subtitle_ref=row.subtitle_ref,
                subtitle_archive_path=subtitle_card.archive_path,
                target_ref=row.target_ref,
                task_uuid=target_card.task_uuid,
                video=target_card.video,
                target_dir=target_card.target_dir,
                emby_lang=emby_lang,
                is_simplified=is_simplified,
                is_movie=target_card.is_movie,
            )
        )
    plan = CompiledSubtitlePlan(
        mappings=compiled_mappings,
        unmatched=unmatched_entries,
        summary=draft.summary or 'accepted subtitle mapping plan',
    )
    return plan, result


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

def _collect_issues(
    subtitle_files: list[SubtitleFileCard],
    target_videos: list[SubtitleTargetVideoCard],
    draft: SubtitleMappingDraft,
) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []

    subtitle_refs = {card.ref for card in subtitle_files if card.ref}
    subtitle_by_ref = {card.ref: card for card in subtitle_files if card.ref}
    target_refs = {card.ref for card in target_videos if card.ref}
    subtitle_count = len(subtitle_files)

    # 1. ref 形状 + 已知性
    for row in draft.rows:
        if not row.subtitle_ref:
            issues.append(_issue('', 'missing_subtitle_ref', 'each draft row requires a subtitle_ref'))
            continue
        if not is_subtitle_ref(row.subtitle_ref):
            issues.append(_issue(row.subtitle_ref, 'invalid_ref_shape', 'subtitle_ref must use SF<idx> form'))
        elif row.subtitle_ref not in subtitle_refs:
            issues.append(_issue(row.subtitle_ref, 'unknown_subtitle_ref', 'subtitle_ref must reference a fixed-layer subtitle card'))

    # 2. duplicate_source：一个字幕 ref 只能出现一次
    subtitle_ref_counts = Counter(row.subtitle_ref for row in draft.rows if row.subtitle_ref)
    for ref, count in subtitle_ref_counts.items():
        if count > 1:
            issues.append(_issue(ref, 'duplicate_subtitle_ref', 'a subtitle may appear only once in the draft'))

    # 3. disposition 行为校验
    for row in draft.rows:
        if row.disposition == 'map_to_video':
            if not row.target_ref:
                issues.append(_issue(row.subtitle_ref, 'missing_target_ref', 'map_to_video rows require a target_ref'))
            elif not is_target_ref(row.target_ref):
                issues.append(_issue(row.subtitle_ref, 'invalid_ref_shape', 'target_ref must use TV<idx> form'))
            elif row.target_ref not in target_refs:
                issues.append(_issue(row.subtitle_ref, 'unknown_target_ref', 'target_ref must reference a fixed-layer target video card'))
            if not row.language:
                issues.append(_issue(row.subtitle_ref, 'missing_language', 'map_to_video rows require a language tag (use chs/cht/jpn/eng etc.)'))
            else:
                subtitle_card = subtitle_by_ref.get(row.subtitle_ref)
                script = (
                    subtitle_card.content_chinese_script
                    if subtitle_card is not None
                    else 'unknown'
                )
                emby_lang, is_simplified = normalize_language(row.language)
                language_matches = (
                    script == 'unknown'
                    or (script == 'simplified' and is_simplified)
                    or (
                        script == 'traditional'
                        and emby_lang in {'zh-TW', 'zh-HK'}
                    )
                )
                if not language_matches:
                    issues.append(
                        _issue(
                            row.subtitle_ref,
                            'content_language_conflict',
                            f'language "{row.language}" conflicts with high-confidence '
                            f'{script} Chinese dialogue evidence',
                            related_refs=[row.subtitle_ref],
                        )
                    )
        elif row.disposition == 'unmatched':
            if row.target_ref:
                issues.append(_issue(row.subtitle_ref, 'invalid_target_on_unmatched', 'unmatched rows must not carry a target_ref'))
        elif row.disposition == 'needs_more_evidence':
            if row.target_ref:
                issues.append(_issue(row.subtitle_ref, 'invalid_target_on_needs_more_evidence', 'needs_more_evidence rows must not carry a target_ref'))
        else:
            issues.append(_issue(row.subtitle_ref, 'invalid_disposition', 'disposition must be map_to_video / unmatched / needs_more_evidence'))

    # 4. duplicate_target：同一 target_ref 除非语言不同，否则冲突
    target_lang_groups: dict[str, list[str]] = defaultdict(list)
    for row in draft.rows:
        if row.disposition == 'map_to_video' and row.target_ref:
            target_lang_groups[row.target_ref].append((row.language or '').lower().strip())
    for target_ref, langs in target_lang_groups.items():
        lang_counts = Counter(langs)
        for lang, count in lang_counts.items():
            if count > 1:
                issues.append(_issue(
                    target_ref,
                    'duplicate_target_language',
                    f'a target video may carry only one subtitle per language; language "{lang or "(empty)"}" repeated {count} times',
                    related_refs=[target_ref],
                ))

    # 5. accounting：coverage + needs == 0 才 ready
    accounting = compute_subtitle_mapping_accounting(draft, subtitle_count)
    if accounting.accounted_for_count != accounting.subtitle_count:
        issues.append(_issue(
            'accounting',
            'coverage_error',
            f'accounted_for_count ({accounting.accounted_for_count}) must equal subtitle_count ({accounting.subtitle_count}); every subtitle must appear as map_to_video or unmatched',
        ))
    if accounting.needs_more_evidence_count != 0:
        issues.append(_issue(
            'accounting',
            'not_ready',
            'needs_more_evidence rows keep accounting unresolved; resolve to map_to_video or unmatched before accepted readiness',
        ))

    return issues


def _issue(
    ref: str,
    issue_code: str,
    message: str,
    *,
    related_refs: list[str] | None = None,
) -> VerifierIssue:
    return VerifierIssue(
        ref=ref,
        issue_code=issue_code,
        severity='blocked',
        message=message,
        related_refs=[str(ref) for ref in (related_refs or []) if str(ref)],
    )
