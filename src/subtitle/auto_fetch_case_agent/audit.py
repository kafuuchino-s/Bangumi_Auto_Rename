"""字幕自动抓取 Case Agent 审计 snapshot。

对齐 ``subtitle.case_agent.audit`` 的角色，把 workspace + decision + verifier
result 编译成可持久化的审计 snapshot，写入 fetch 状态 JSON 与 case-run artifacts。
"""

from __future__ import annotations

from typing import Any

from src.rename.case_agent.models import CaseVerifierResult

from .models import AutoFetchDecision
from .workspace import AutoFetchCaseWorkspace


def build_auto_fetch_case_snapshot(
    *,
    workspace: AutoFetchCaseWorkspace,
    decision: AutoFetchDecision | None,
    verifier_result: CaseVerifierResult | None,
    status: str,
    summary: str,
    case_agent_mode: str = 'auto_fetch_case_agent',
) -> dict[str, Any]:
    """构建 auto_fetch case agent 审计 snapshot。"""
    scan_scope = workspace.readable_scan_scope()
    return {
        'case_agent_mode': case_agent_mode,
        'status': status,
        'summary': summary,
        'task_uuid': workspace.task_uuid,
        'scan_scope': scan_scope,
        'missing_video_count': len(workspace.missing_videos),
        'missing_video_refs': workspace.missing_video_refs,
        'keyword_count': len(workspace.keywords),
        'keyword_refs': workspace.keyword_refs,
        'candidate_count': len(workspace.candidates),
        'candidate_refs': workspace.candidate_refs,
        'package_refs': workspace.package_refs,
        'decision': decision.model_dump(mode='json') if decision is not None else None,
        'verifier': verifier_result.model_dump(mode='json') if verifier_result is not None else None,
    }
