"""字幕 Case Agent 审计 snapshot。

对齐 rename 的 audit 角色，把 workspace + draft + verifier result 编译成
可持久化的审计 snapshot，写入任务 JSON 与 case-run artifacts。
"""

from __future__ import annotations

from typing import Any

from src.rename.case_agent.models import CaseVerifierResult

from .models import SubtitleMappingDraft
from .workspace import SubtitleCaseWorkspace


def build_subtitle_case_snapshot(
    *,
    workspace: SubtitleCaseWorkspace,
    draft: SubtitleMappingDraft | None,
    verifier_result: CaseVerifierResult | None,
    status: str,
    summary: str,
    case_agent_mode: str = 'subtitle_case_agent',
) -> dict[str, Any]:
    """构建字幕 case agent 审计 snapshot。"""
    return {
        'case_agent_mode': case_agent_mode,
        'status': status,
        'summary': summary,
        'archive_name': workspace.archive_name,
        'subtitle_count': len(workspace.subtitle_files),
        'target_video_count': len(workspace.target_videos),
        'subtitle_refs': workspace.subtitle_refs,
        'target_refs': workspace.target_refs,
        'draft': draft.model_dump(mode='json') if draft is not None else None,
        'verifier': verifier_result.model_dump(mode='json') if verifier_result is not None else None,
    }
