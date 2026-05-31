from __future__ import annotations

from collections import Counter, defaultdict

from ..case_agent.models import CaseVerifierResult, VerifierIssue
from .models import (
    BgmAssignmentRef,
    BgmToTmdbInput,
    BgmToTmdbMapping,
    BgmToTmdbMappingDraft,
    MOVIE_LEGAL_NODE_RE,
    TmdbLegalGraph,
    VerifiedBgmToTmdbPlan,
    normalize_source_path,
)


def verify_bgm_to_tmdb_draft(
    bridge_input: BgmToTmdbInput,
    legal_graph: TmdbLegalGraph,
    draft: BgmToTmdbMappingDraft,
) -> CaseVerifierResult:
    issues = _collect_issues(bridge_input, legal_graph, draft)
    blocking = [issue for issue in issues if issue.severity == 'blocked']
    return CaseVerifierResult(
        passed=not blocking,
        issues=issues,
        summary='accepted' if not blocking else f'{len(blocking)} blocking BGM->TMDB bridge issue(s)',
    )


def verify_and_compile_bgm_to_tmdb_plan(
    bridge_input: BgmToTmdbInput,
    legal_graph: TmdbLegalGraph,
    draft: BgmToTmdbMappingDraft,
) -> tuple[VerifiedBgmToTmdbPlan | None, CaseVerifierResult]:
    result = verify_bgm_to_tmdb_draft(bridge_input, legal_graph, draft)
    if not result.passed:
        return None, result
    mappings = [
        mapping.model_copy(update={'source_path': normalize_source_path(mapping.source_path)})
        for mapping in draft.mappings
    ]
    target_count = sum(len(mapping.tmdb_legal_node_ids) for mapping in mappings)
    absent_count = sum(1 for mapping in mappings if mapping.disposition == 'tmdb_target_absent')
    supplemental_count = sum(1 for mapping in mappings if mapping.disposition == 'unmapped_supplemental')
    return (
        VerifiedBgmToTmdbPlan(
            source_path=bridge_input.source_path,
            mappings=mappings,
            tmdb_target_count=target_count,
            tmdb_absent_count=absent_count,
            supplemental_count=supplemental_count,
            summary=draft.summary or 'accepted BGM->TMDB bridge dry-run plan',
        ),
        result,
    )


def _collect_issues(
    bridge_input: BgmToTmdbInput,
    legal_graph: TmdbLegalGraph,
    draft: BgmToTmdbMappingDraft,
) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    assignment_by_path = {
        normalize_source_path(assignment.source_path): assignment
        for assignment in bridge_input.assignments
    }
    mappings_by_path: dict[str, list[BgmToTmdbMapping]] = defaultdict(list)
    for mapping in draft.mappings:
        mappings_by_path[normalize_source_path(mapping.source_path)].append(mapping)

    for source_path, assignment in assignment_by_path.items():
        source_mappings = mappings_by_path.get(source_path, [])
        if not source_mappings:
            issues.append(_issue(
                source_path,
                'missing_source_mapping',
                'every BGM assignment must be represented in the BGM->TMDB bridge draft',
                related_refs=[source_path],
            ))
            continue
        if len(source_mappings) > 1:
            issues.append(_issue(
                source_path,
                'duplicate_source_mapping',
                'a source_path may appear only once in the BGM->TMDB bridge draft',
                related_refs=[source_path],
            ))
        for mapping in source_mappings:
            _verify_mapping_shape(issues, assignment, mapping)

    for source_path in mappings_by_path:
        if source_path not in assignment_by_path:
            issues.append(_issue(
                source_path,
                'unknown_source_path',
                'bridge draft source_path is not present in the accepted BGM compiled plan',
                related_refs=[source_path],
            ))

    node_map = legal_graph.legal_node_map()
    target_usage: Counter[str] = Counter()
    for mapping in draft.mappings:
        for node_id in mapping.tmdb_legal_node_ids:
            if node_id.startswith('tmdb:'):
                issues.append(_issue(
                    normalize_source_path(mapping.source_path),
                    'bare_tmdb_node_not_allowed',
                    'bridge draft must use tv:<tmdb_id>:SxxEyy or movie:<tmdb_id>, not bare tmdb:SxxEyy',
                    related_refs=[node_id],
                ))
                continue
            if node_id not in node_map:
                issues.append(_issue(
                    normalize_source_path(mapping.source_path),
                    'unknown_tmdb_legal_node',
                    'bridge draft target must be copied from the exposed TMDB legal graph',
                    related_refs=[node_id],
                ))
                continue
            target_usage[node_id] += 1

    for node_id, count in target_usage.items():
        if count > 1:
            issues.append(_issue(
                node_id,
                'duplicate_tmdb_target',
                'a TMDB legal node may be used by only one source_path in the dry-run bridge contract',
                related_refs=[node_id],
            ))

    return issues


def _verify_mapping_shape(
    issues: list[VerifierIssue],
    assignment: BgmAssignmentRef,
    mapping: BgmToTmdbMapping,
) -> None:
    source_path = normalize_source_path(assignment.source_path)
    target_count = len(mapping.tmdb_legal_node_ids)
    if assignment.is_mapped_bangumi:
        if mapping.disposition == 'tmdb_target_absent':
            if target_count:
                issues.append(_issue(
                    source_path,
                    'tmdb_absent_mapping_has_targets',
                    'tmdb_target_absent mappings must not include TMDB legal nodes',
                    related_refs=[source_path, *mapping.tmdb_legal_node_ids],
                ))
            return
        if mapping.disposition != 'map_to_tmdb':
            issues.append(_issue(
                source_path,
                'mapped_bangumi_assignment_unmapped',
                'map_to_bangumi assignments must either map to TMDB legal nodes or be marked tmdb_target_absent when TMDB has no legal node',
                related_refs=[source_path],
            ))
            return
        if assignment.is_span and target_count == 1 and MOVIE_LEGAL_NODE_RE.fullmatch(mapping.tmdb_legal_node_ids[0]):
            expected_count = 1
        else:
            expected_count = len(assignment.target_span.episode_ids) if assignment.is_span else 1
        if target_count != expected_count:
            issues.append(_issue(
                source_path,
                'tmdb_target_count_mismatch',
                'ordinary BGM assignments need one TMDB node; BGM TV spans must expand to the same number of TMDB nodes; a BGM span may map to one movie node when TMDB models the span as a movie',
                related_refs=[source_path],
            ))
        duplicates = [node_id for node_id, count in Counter(mapping.tmdb_legal_node_ids).items() if count > 1]
        if duplicates:
            issues.append(_issue(
                source_path,
                'duplicate_tmdb_target_in_mapping',
                'a single source_path may not repeat the same TMDB legal node',
                related_refs=duplicates,
            ))
        return

    if mapping.disposition != 'unmapped_supplemental' or target_count:
        issues.append(_issue(
            source_path,
            'supplemental_mapped_to_tmdb',
            'non-Bangumi, supplemental, needs-more-evidence, and fail-closed BGM assignments must remain unmapped in this bridge layer',
            related_refs=[source_path, *mapping.tmdb_legal_node_ids],
        ))


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
