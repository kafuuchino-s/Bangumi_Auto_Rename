"""字幕自动抓取 Case Agent 轻 submit gate。

对齐 ``subtitle.case_agent.verifier`` 的形状（复用 ``CaseVerifierResult`` /
``VerifierIssue``），但 **auto_fetch 是 candidate ranking 不是 mapping**——
没有 coverage / duplicate / accounting 合同。这里只做确定性、可验证的轻 gate：

- ``select_candidate``：candidate_ref 必须是固定层 CD* ref，且该候选含可下载
  附件（``has_downloadable_attachment``）。
- ``select_package``：package_ref 必须是固定层 PK* ref，该楼包含可下载链接
  （``has_downloadable_link``），且非 font/patch-only（``is_font_or_patch_only``）。
- ref 形状与已知性校验。

不确定判断（哪个帖是正确 arc / 哪个包是正片非特典 / 版本语言歧义）不在此层
裁决，交 AI 通过 ``AutoFetchDecision`` 的 reason / ``need_more_evidence`` /
``unmatched`` 表达，或经 Pi 多轮 evidence request 引导。
"""

from __future__ import annotations

from src.rename.case_agent.models import CaseVerifierResult, VerifierIssue

from .models import (
    AutoFetchDecision,
    CandidateCard,
    is_candidate_ref,
    is_package_ref,
)
from .workspace import AutoFetchCaseWorkspace


def verify_auto_fetch_decision(
    *,
    workspace: AutoFetchCaseWorkspace,
    decision: AutoFetchDecision,
) -> CaseVerifierResult:
    """校验 AI 决策，返回轻 gate 结果。"""
    issues = _collect_issues(workspace, decision)
    blocking = [issue for issue in issues if issue.severity == 'blocked']
    return CaseVerifierResult(
        passed=not blocking,
        issues=issues,
        summary='accepted' if not blocking else f'{len(blocking)} blocking auto_fetch decision issue(s)',
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

def _collect_issues(
    workspace: AutoFetchCaseWorkspace,
    decision: AutoFetchDecision,
) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    candidate_index = workspace.candidate_by_ref()
    package_index = workspace.package_by_ref()

    disposition = decision.disposition

    if disposition == 'select_candidate':
        issues.extend(
            _check_select_candidate(decision, candidate_index)
        )
    elif disposition == 'select_package':
        issues.extend(_check_select_package(decision, package_index))
    elif disposition == 'need_more_evidence':
        # 调查中状态：不应携带 package_ref（仍未到选包阶段）；candidate_ref 可空可非空。
        if decision.package_ref:
            issues.append(
                _issue(
                    decision.package_ref,
                    'invalid_package_on_investigating',
                    'need_more_evidence must not carry a package_ref; resolve to select_candidate/select_package/unmatched first.',
                )
            )
    elif disposition == 'unmatched':
        # 合格跳过：该关键词搜不到正片候选。不应携带 candidate_ref/package_ref。
        if decision.candidate_ref:
            issues.append(
                _issue(
                    decision.candidate_ref,
                    'invalid_candidate_on_unmatched',
                    'unmatched must not carry a candidate_ref.',
                )
            )
        if decision.package_ref:
            issues.append(
                _issue(
                    decision.package_ref,
                    'invalid_package_on_unmatched',
                    'unmatched must not carry a package_ref.',
                )
            )
    elif disposition == 'submit_complete':
        # 多季覆盖终止信号：不应携带 candidate_ref/package_ref（ selections 在
        # tool_state 里，submit_package 时已逐个过 gate，submit_complete 不重查）。
        # 实质 gate（selections 非空）在 pi_tools.tool_submit_complete 内联校验
        # （因需访问 state.selections，verifier 签名只有 workspace+decision）。
        if decision.candidate_ref:
            issues.append(
                _issue(
                    decision.candidate_ref,
                    'invalid_candidate_on_submit_complete',
                    'submit_complete must not carry a candidate_ref.',
                )
            )
        if decision.package_ref:
            issues.append(
                _issue(
                    decision.package_ref,
                    'invalid_package_on_submit_complete',
                    'submit_complete must not carry a package_ref.',
                )
            )
    else:
        issues.append(
            _issue(
                '',
                'invalid_disposition',
                'disposition must be select_candidate / select_package / submit_complete / need_more_evidence / unmatched',
            )
        )

    return issues


def _check_select_candidate(
    decision: AutoFetchDecision,
    candidate_index: dict[str, CandidateCard],
) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    ref = decision.candidate_ref
    if not ref:
        issues.append(_issue('', 'missing_candidate_ref', 'select_candidate requires a candidate_ref (a CD ref)'))
        return issues
    if not is_candidate_ref(ref):
        issues.append(_issue(ref, 'invalid_ref_shape', 'candidate_ref must use CD<idx> form'))
        return issues
    candidate = candidate_index.get(ref)
    if candidate is None:
        issues.append(_issue(ref, 'unknown_candidate_ref', 'candidate_ref must reference a fixed-layer candidate card'))
        return issues
    # 轻 gate：候选含可下载附件
    if not candidate.has_downloadable_attachment and not candidate.packages:
        # packages_loaded=False 表示该候选只 search 命中标题、附件未探测；
        # 引导 Pi 先 load_candidate_packages 再判定，不要直接 fail_closed。
        if not getattr(candidate, 'packages_loaded', False):
            hint = (
                'selected candidate packages not yet loaded (only search title '
                'known). Call load_candidate_packages to probe attachments before '
                'deciding no_downloadable or submitting.'
            )
        else:
            hint = (
                'selected candidate has no downloadable attachment or package; '
                'pick a candidate with packages'
            )
        issues.append(_issue(ref, 'candidate_not_downloadable', hint))
    return issues


def _check_select_package(
    decision: AutoFetchDecision,
    package_index: dict[str, "object"],
) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    ref = decision.package_ref
    if not ref:
        issues.append(_issue('', 'missing_package_ref', 'select_package requires a package_ref (a PK ref)'))
        return issues
    if not is_package_ref(ref):
        issues.append(_issue(ref, 'invalid_ref_shape', 'package_ref must use PK<idx> form'))
        return issues
    package = package_index.get(ref)
    if package is None:
        issues.append(_issue(ref, 'unknown_package_ref', 'package_ref must reference a fixed-layer package card'))
        return issues
    # 轻 gate：楼包含可下载链接（纯事实：有无可下载 link）。
    # 包性质（font/patch-only/special）不再固定层判——AI-first，Pi 看 post_text +
    # links 自判，SKILL 教。固定层不做"这包是不是字幕包"的语义判断。
    if not getattr(package, 'has_downloadable_link', False):
        issues.append(
            _issue(ref, 'package_not_downloadable', 'selected package has no direct-download link; pick a package with downloadable links')
        )
        return issues
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
        related_refs=[str(r) for r in (related_refs or []) if str(r)],
    )


def auto_fetch_repair_hints(verifier_result: CaseVerifierResult) -> list[str]:
    """把 verifier issue 翻译成给 AI 的修复提示。"""
    hints: list[str] = []
    for issue in verifier_result.issues:
        code = issue.issue_code
        if code == 'missing_candidate_ref':
            hints.append('select_candidate requires a candidate_ref; copy a CD ref from candidates in the context.')
        elif code == 'missing_package_ref':
            hints.append('select_package requires a package_ref; copy a PK ref from a candidate\'s packages in the context.')
        elif code == 'invalid_ref_shape':
            hints.append(f'Ref {issue.ref} must use the CD<idx> / PK<idx> short-ref form shown in the context.')
        elif code == 'unknown_candidate_ref':
            hints.append(f'Candidate ref {issue.ref} is not a fixed-layer CD ref; copy the ref from candidates in the context.')
        elif code == 'unknown_package_ref':
            hints.append(f'Package ref {issue.ref} is not a fixed-layer PK ref; copy the ref from a candidate\'s packages in the context.')
        elif code == 'candidate_not_downloadable':
            hints.append('Selected candidate has no downloadable attachment/package; search again or pick a candidate with packages.')
        elif code == 'package_not_downloadable':
            hints.append('Selected package has no direct-download link; pick a package with downloadable links.')
        elif code == 'invalid_candidate_on_unmatched':
            hints.append('unmatched must not carry a candidate_ref; use unmatched only when no candidate fits.')
        elif code == 'invalid_package_on_unmatched':
            hints.append('unmatched must not carry a package_ref.')
        elif code == 'invalid_package_on_investigating':
            hints.append('need_more_evidence must not carry a package_ref; keep investigating or resolve to a disposition.')
        elif code == 'invalid_disposition':
            hints.append('disposition must be select_candidate / select_package / need_more_evidence / unmatched.')
        else:
            hints.append(f'{code}: {issue.message}')
    # 去重保序
    seen: set[str] = set()
    deduped: list[str] = []
    for hint in hints:
        if hint not in seen:
            seen.add(hint)
            deduped.append(hint)
    return deduped
