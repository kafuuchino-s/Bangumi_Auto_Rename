"""字幕映射草稿 accounting。

对齐 ``src.rename.case_agent.mapping_draft.compute_mapping_draft_accounting`` 的
形状，但字幕语义是 1:1（一个字幕一行），无需 span / main_file / supplemental 的
复杂分区。
"""

from __future__ import annotations

from .models import SubtitleMappingAccounting, SubtitleMappingDraft


def compute_subtitle_mapping_accounting(
    draft: SubtitleMappingDraft,
    subtitle_count: int,
) -> SubtitleMappingAccounting:
    """统计草稿 coverage / 未决 / accepted readiness。

    Args:
        draft: 字幕映射草稿。
        subtitle_count: 解压出的字幕文件总数（固定层事实，不可被 draft 改写）。
    """
    mapped = unmatched = needs = 0
    seen_subtitle_refs: set[str] = set()
    duplicate_subtitle_ref_count = 0
    for row in draft.rows:
        if row.subtitle_ref in seen_subtitle_refs:
            duplicate_subtitle_ref_count += 1
        seen_subtitle_refs.add(row.subtitle_ref)
        if row.disposition == 'map_to_video':
            mapped += 1
        elif row.disposition == 'unmatched':
            unmatched += 1
        elif row.disposition == 'needs_more_evidence':
            needs += 1
    accounted_for_count = mapped + unmatched + needs
    accepted_accounting_ready = (
        accounted_for_count == subtitle_count
        and needs == 0
        and duplicate_subtitle_ref_count == 0
    )
    return SubtitleMappingAccounting(
        subtitle_count=subtitle_count,
        mapped_count=mapped,
        unmatched_count=unmatched,
        needs_more_evidence_count=needs,
        accounted_for_count=accounted_for_count,
        accepted_accounting_ready=accepted_accounting_ready,
    )
