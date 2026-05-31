from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..case_agent.models import CaseVerifierResult, VerifierIssue
from .models import (
    BgmAssignmentRef,
    BgmToTmdbBgmSelector,
    BgmToTmdbInput,
    BgmToTmdbMapping,
    BgmToTmdbMappingDraft,
    BgmToTmdbRecipeParams,
    BgmToTmdbRecipeRule,
    BgmToTmdbTmdbTarget,
    TmdbLegalGraph,
    movie_legal_node_id,
    normalize_source_path,
    tv_legal_node_id,
)
from .verifier import verify_bgm_to_tmdb_draft


@dataclass
class BgmToTmdbRecipeCompileResult:
    bridge_draft: BgmToTmdbMappingDraft
    verifier_result: CaseVerifierResult
    review_warnings: list[dict[str, Any]] = field(default_factory=list)
    rule_match_counts: dict[str, int] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return bool(self.verifier_result.passed and not self.review_warnings)


def compile_and_verify_bgm_to_tmdb_recipe_params(
    bridge_input: BgmToTmdbInput,
    legal_graph: TmdbLegalGraph,
    recipe_params: BgmToTmdbRecipeParams,
) -> BgmToTmdbRecipeCompileResult:
    draft, recipe_issues, review_warnings, rule_match_counts = compile_bgm_to_tmdb_recipe_params(
        bridge_input,
        recipe_params,
    )
    node_result = verify_bgm_to_tmdb_draft(bridge_input, legal_graph, draft)
    issues = [*recipe_issues, *node_result.issues]
    blocking = [issue for issue in issues if issue.severity == 'blocked']
    verifier_result = CaseVerifierResult(
        passed=not blocking,
        issues=issues,
        summary='accepted' if not blocking else f'{len(blocking)} blocking BGM->TMDB recipe issue(s)',
    )
    return BgmToTmdbRecipeCompileResult(
        bridge_draft=draft,
        verifier_result=verifier_result,
        review_warnings=review_warnings if verifier_result.passed else [],
        rule_match_counts=rule_match_counts,
    )


def compile_bgm_to_tmdb_recipe_params(
    bridge_input: BgmToTmdbInput,
    recipe_params: BgmToTmdbRecipeParams,
) -> tuple[BgmToTmdbMappingDraft, list[VerifierIssue], list[dict[str, Any]], dict[str, int]]:
    issues: list[VerifierIssue] = []
    review_warnings: list[dict[str, Any]] = []
    mappings: list[BgmToTmdbMapping] = []
    assignments = list(bridge_input.assignments)
    matches_by_rule: dict[str, list[BgmAssignmentRef]] = {}
    coverage: dict[str, list[str]] = defaultdict(list)

    if not recipe_params.rules:
        issues.append(_issue('recipe_params', 'missing_recipe_rules', 'BGM->TMDB recipe params must include at least one rule.'))

    for index, rule in enumerate(recipe_params.rules, start=1):
        rule_ref = _rule_ref(rule, index)
        matched, selector_issues = _match_assignments(assignments, rule)
        issues.extend(_issue(rule_ref, code, message, related_refs=refs) for code, message, refs in selector_issues)
        matches_by_rule[rule_ref] = matched
        if not matched:
            issues.append(_issue(rule_ref, 'zero_bgm_assignment_match', 'recipe rule selected no accepted BGM assignments.'))
        for assignment in matched:
            coverage[normalize_source_path(assignment.source_path)].append(rule_ref)
        mappings.extend(_compile_rule_mappings(rule, rule_ref, matched, issues))
        review_warnings.extend(_review_warnings_for_rule(rule, rule_ref, matched))

    assignment_paths = [normalize_source_path(assignment.source_path) for assignment in assignments if normalize_source_path(assignment.source_path)]
    for source_path in assignment_paths:
        covering_rules = coverage.get(source_path, [])
        if not covering_rules:
            issues.append(_issue(
                source_path,
                'uncovered_bgm_assignment',
                'every accepted BGM assignment must be covered exactly once by BGM->TMDB recipe params',
                related_refs=[source_path],
            ))
        elif len(covering_rules) > 1:
            issues.append(_issue(
                source_path,
                'overlapping_bgm_rules',
                'a BGM assignment is selected by more than one BGM->TMDB recipe rule',
                related_refs=[source_path, *covering_rules],
            ))

    rule_match_counts = {rule_ref: len(matched) for rule_ref, matched in matches_by_rule.items()}
    return (
        BgmToTmdbMappingDraft(summary=recipe_params.summary, mappings=mappings),
        issues,
        _dedupe_review_warnings(review_warnings),
        rule_match_counts,
    )


def declared_tmdb_refs(recipe_params: BgmToTmdbRecipeParams) -> list[str]:
    return _dedupe_nonempty([
        rule.target_tmdb.tmdb_ref
        for rule in recipe_params.rules
        if rule.rule_type != 'tmdb_absent_group'
    ])


def _compile_rule_mappings(
    rule: BgmToTmdbRecipeRule,
    rule_ref: str,
    matched: list[BgmAssignmentRef],
    issues: list[VerifierIssue],
) -> list[BgmToTmdbMapping]:
    mappings: list[BgmToTmdbMapping] = []
    if rule.rule_type == 'supplemental_group':
        for assignment in matched:
            if assignment.is_mapped_bangumi:
                issues.append(_issue(
                    normalize_source_path(assignment.source_path),
                    'supplemental_rule_selected_mapped_assignment',
                    'supplemental_group rules may only cover non-Bangumi/supplemental assignments',
                    related_refs=[rule_ref, assignment.source_path],
                ))
            mappings.append(_mapping(assignment, [], disposition='unmapped_supplemental', reason=rule.reason, confidence=rule.confidence))
        return mappings

    if rule.rule_type == 'tmdb_absent_group':
        for assignment in matched:
            if not assignment.is_mapped_bangumi:
                issues.append(_issue(
                    normalize_source_path(assignment.source_path),
                    'tmdb_absent_rule_selected_supplemental_assignment',
                    'tmdb_absent_group rules may only cover BGM-mapped assignments whose TMDB legal node is absent',
                    related_refs=[rule_ref, assignment.source_path],
                ))
            mappings.append(_mapping(assignment, [], disposition='tmdb_target_absent', reason=rule.reason, confidence=rule.confidence))
        return mappings

    for assignment in matched:
        if not assignment.is_mapped_bangumi:
            issues.append(_issue(
                normalize_source_path(assignment.source_path),
                'mapped_rule_selected_supplemental_assignment',
                'mapped BGM->TMDB rules may not cover supplemental/non-Bangumi assignments',
                related_refs=[rule_ref, assignment.source_path],
            ))

    if rule.rule_type == 'movie':
        node_id = _movie_node_for_target(rule.target_tmdb, rule_ref, issues)
        for assignment in matched:
            mappings.append(_mapping(assignment, [node_id] if node_id else [], reason=rule.reason, confidence=rule.confidence))
        return mappings

    if rule.rule_type == 'span':
        return _compile_span_rule(rule, rule_ref, matched, issues)

    return _compile_sequence_rule(rule, rule_ref, matched, issues)


def _compile_sequence_rule(
    rule: BgmToTmdbRecipeRule,
    rule_ref: str,
    matched: list[BgmAssignmentRef],
    issues: list[VerifierIssue],
) -> list[BgmToTmdbMapping]:
    target = rule.target_tmdb
    media_type, tmdb_id = _parse_tmdb_ref(target.tmdb_ref)
    if media_type != 'tv' or tmdb_id <= 0:
        issues.append(_issue(rule_ref, 'invalid_tmdb_tv_target', 'episode sequence rules require target_tmdb.tmdb_ref shaped like tv:<id>.', related_refs=[target.tmdb_ref]))
        return [_mapping(assignment, [], reason=rule.reason, confidence=rule.confidence) for assignment in matched]
    season_number = target.season_number
    if season_number is None:
        if rule.rule_type == 'special_sequence':
            season_number = 0
        else:
            issues.append(_issue(rule_ref, 'missing_tmdb_season_number', 'episode sequence rules require target_tmdb.season_number.', related_refs=[target.tmdb_ref]))
            return [_mapping(assignment, [], reason=rule.reason, confidence=rule.confidence) for assignment in matched]

    sorted_assignments = _sort_assignments_by_number(matched, target.number_field)
    target_numbers, range_error = _numbers_from_range(target.episode_range)
    if range_error:
        target_numbers = []
        if target.episode_range:
            issues.append(_issue(rule_ref, 'invalid_tmdb_episode_range', range_error, related_refs=[target.episode_range]))
    if target_numbers and len(target_numbers) != len(sorted_assignments):
        issues.append(_issue(
            rule_ref,
            'tmdb_episode_range_count_mismatch',
            'target_tmdb.episode_range must expand to the same count as the selected BGM assignments',
            related_refs=[target.episode_range],
        ))

    mappings: list[BgmToTmdbMapping] = []
    for index, assignment in enumerate(sorted_assignments):
        source_number = _assignment_number(assignment, target.number_field)
        if source_number is None:
            issues.append(_issue(
                normalize_source_path(assignment.source_path),
                'missing_bgm_number_for_recipe_rule',
                f'BGM assignment lacks number_field {target.number_field}',
                related_refs=[rule_ref, assignment.source_path],
            ))
            mappings.append(_mapping(assignment, [], reason=rule.reason, confidence=rule.confidence))
            continue
        if target_numbers and index < len(target_numbers):
            episode_number = target_numbers[index]
        else:
            try:
                episode_number = _eval_episode_expr(target.episode_offset or 'EP', source_number)
            except ValueError as exc:
                issues.append(_issue(rule_ref, 'invalid_tmdb_episode_offset', str(exc), related_refs=[target.episode_offset]))
                episode_number = 0
        node_ids = [tv_legal_node_id(tmdb_id, int(season_number), episode_number)] if episode_number > 0 else []
        mappings.append(_mapping(assignment, node_ids, reason=rule.reason, confidence=rule.confidence))
    return mappings


def _compile_span_rule(
    rule: BgmToTmdbRecipeRule,
    rule_ref: str,
    matched: list[BgmAssignmentRef],
    issues: list[VerifierIssue],
) -> list[BgmToTmdbMapping]:
    target = rule.target_tmdb
    media_type, tmdb_id = _parse_tmdb_ref(target.tmdb_ref)
    if media_type != 'tv' or tmdb_id <= 0:
        issues.append(_issue(rule_ref, 'invalid_tmdb_tv_target', 'span rules require target_tmdb.tmdb_ref shaped like tv:<id>.', related_refs=[target.tmdb_ref]))
        return [_mapping(assignment, [], reason=rule.reason, confidence=rule.confidence) for assignment in matched]
    season_number = target.season_number
    if season_number is None:
        issues.append(_issue(rule_ref, 'missing_tmdb_season_number', 'span rules require target_tmdb.season_number.', related_refs=[target.tmdb_ref]))
        return [_mapping(assignment, [], reason=rule.reason, confidence=rule.confidence) for assignment in matched]

    mappings: list[BgmToTmdbMapping] = []
    for assignment in matched:
        span_count = len(assignment.target_span.episode_ids)
        if not span_count:
            issues.append(_issue(
                normalize_source_path(assignment.source_path),
                'span_rule_selected_non_span_assignment',
                'span rules may only cover BGM assignments with target_span.episode_ids',
                related_refs=[rule_ref, assignment.source_path],
            ))
        target_numbers, range_error = _numbers_from_range(target.episode_range)
        if range_error:
            target_numbers = []
            if target.episode_range:
                issues.append(_issue(rule_ref, 'invalid_tmdb_episode_range', range_error, related_refs=[target.episode_range]))
        if target_numbers and len(target_numbers) != span_count:
            issues.append(_issue(
                normalize_source_path(assignment.source_path),
                'tmdb_episode_range_count_mismatch',
                'span target_tmdb.episode_range must expand to the same count as the BGM target span',
                related_refs=[rule_ref, assignment.source_path, target.episode_range],
            ))
        if not target_numbers and span_count:
            start = assignment.target_span.sort_start or assignment.target.sort or 1
            try:
                target_numbers = [_eval_episode_expr(target.episode_offset or 'EP', int(start) + offset) for offset in range(span_count)]
            except ValueError as exc:
                issues.append(_issue(rule_ref, 'invalid_tmdb_episode_offset', str(exc), related_refs=[target.episode_offset]))
                target_numbers = []
        node_ids = [tv_legal_node_id(tmdb_id, int(season_number), number) for number in target_numbers if number > 0]
        mappings.append(_mapping(assignment, node_ids, reason=rule.reason, confidence=rule.confidence))
    return mappings


def _match_assignments(
    assignments: list[BgmAssignmentRef],
    rule: BgmToTmdbRecipeRule,
) -> tuple[list[BgmAssignmentRef], list[tuple[str, str, list[str]]]]:
    selector = rule.select_bgm
    issues: list[tuple[str, str, list[str]]] = []
    if not _selector_has_any_field(selector):
        if rule.rule_type == 'supplemental_group':
            return [assignment for assignment in assignments if not assignment.is_mapped_bangumi], []
        if rule.rule_type == 'tmdb_absent_group':
            issues.append(('empty_bgm_selector', 'tmdb_absent_group rules require a targeted BGM selector; do not mark every mapped assignment absent by default', []))
            return [], issues
        issues.append(('empty_bgm_selector', 'mapped recipe rules require at least one BGM selector field', []))
        return [], issues

    source_path_set = {normalize_source_path(path) for path in selector.source_paths}
    known_paths = {normalize_source_path(assignment.source_path) for assignment in assignments}
    for source_path in sorted(source_path_set - known_paths):
        issues.append(('unknown_bgm_source_path', f'source_path is not present in accepted BGM assignments: {source_path}', [source_path]))

    matched = [assignment for assignment in assignments if _assignment_matches_selector(assignment, selector)]
    return matched, issues


def _assignment_matches_selector(assignment: BgmAssignmentRef, selector: BgmToTmdbBgmSelector) -> bool:
    source_path = normalize_source_path(assignment.source_path)
    if selector.source_paths and source_path not in {normalize_source_path(path) for path in selector.source_paths}:
        return False
    if selector.bangumi_subject_id and _assignment_subject_id(assignment) != selector.bangumi_subject_id:
        return False
    if selector.media_kind and _norm_text(_assignment_media_kind(assignment)) != _norm_text(selector.media_kind):
        return False
    if selector.episode_type and _norm_text(_assignment_episode_type(assignment)) != _norm_text(selector.episode_type):
        return False
    if selector.rule_name and str(assignment.rule_name or '') != selector.rule_name:
        return False
    if selector.episode_ids:
        ids = set(int(item) for item in selector.episode_ids)
        assignment_ids = set(assignment.target_span.episode_ids or [])
        if assignment.target.episode_id:
            assignment_ids.add(int(assignment.target.episode_id))
        if not assignment_ids.intersection(ids):
            return False
    if selector.sort_range and not _range_contains(selector.sort_range, assignment.target.sort):
        return False
    if selector.ep_range and not _range_contains(selector.ep_range, assignment.target.ep):
        return False
    return True


def _selector_has_any_field(selector: BgmToTmdbBgmSelector) -> bool:
    return bool(
        selector.bangumi_subject_id
        or selector.media_kind
        or selector.episode_type
        or selector.sort_range
        or selector.ep_range
        or selector.episode_ids
        or selector.rule_name
        or selector.source_paths
    )


def _mapping(
    assignment: BgmAssignmentRef,
    node_ids: list[str],
    *,
    disposition: str = 'map_to_tmdb',
    reason: str = '',
    confidence: str = 'Medium',
) -> BgmToTmdbMapping:
    return BgmToTmdbMapping(
        source_path=assignment.source_path,
        disposition=disposition,
        tmdb_legal_node_ids=node_ids,
        confidence=confidence if confidence in {'High', 'Medium', 'Low'} else 'Medium',
        reason=reason or assignment.reason,
    )


def _movie_node_for_target(target: BgmToTmdbTmdbTarget, rule_ref: str, issues: list[VerifierIssue]) -> str:
    if target.tmdb_legal_node_id:
        return target.tmdb_legal_node_id
    media_type, tmdb_id = _parse_tmdb_ref(target.tmdb_ref)
    if media_type != 'movie' or tmdb_id <= 0:
        issues.append(_issue(rule_ref, 'invalid_tmdb_movie_target', 'movie rules require target_tmdb.tmdb_ref shaped like movie:<id>.', related_refs=[target.tmdb_ref]))
        return ''
    return movie_legal_node_id(tmdb_id)


def _assignment_number(assignment: BgmAssignmentRef, number_field: str) -> int | None:
    if number_field == 'ep':
        return assignment.target.ep
    if number_field == 'extracted_episode_number':
        return assignment.extracted_episode_number
    return assignment.target.sort


def _sort_assignments_by_number(assignments: list[BgmAssignmentRef], number_field: str) -> list[BgmAssignmentRef]:
    return sorted(
        assignments,
        key=lambda item: (
            _assignment_number(item, number_field) is None,
            _assignment_number(item, number_field) or 0,
            normalize_source_path(item.source_path),
        ),
    )


def _numbers_from_range(spec: str) -> tuple[list[int], str]:
    numbers: list[int] = []
    if not str(spec or '').strip():
        return [], ''
    for part in [item.strip() for item in str(spec or '').split(',') if item.strip()]:
        if '-' in part:
            left_text, right_text = part.split('-', 1)
            try:
                left = int(left_text)
                right = int(right_text)
            except ValueError:
                return [], f'invalid range segment: {part}'
            if right < left:
                return [], f'range end is before start: {part}'
            numbers.extend(range(left, right + 1))
        else:
            try:
                numbers.append(int(part))
            except ValueError:
                return [], f'invalid range segment: {part}'
    return list(dict.fromkeys(numbers)), ''


def _range_contains(spec: str, value: int | None) -> bool:
    if value is None:
        return False
    numbers, error = _numbers_from_range(spec)
    return not error and int(value) in set(numbers)


def _eval_episode_expr(expr: str, ep: int) -> int:
    tree = ast.parse(str(expr or 'EP'), mode='eval')

    def visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return int(node.value)
        if isinstance(node, ast.Name) and node.id == 'EP':
            return int(ep)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            return left * right
        raise ValueError('episode_offset may only use EP, integers, +, -, *, unary signs, and parentheses')

    return visit(tree)


def _parse_tmdb_ref(ref: str) -> tuple[str, int]:
    media_type, _, raw_id = str(ref or '').partition(':')
    media_type = media_type.strip().casefold()
    if media_type not in {'tv', 'movie'}:
        return '', 0
    try:
        return media_type, int(raw_id)
    except ValueError:
        return '', 0


def _assignment_subject_id(assignment: BgmAssignmentRef) -> int:
    return int(assignment.target_span.bangumi_subject_id or assignment.target.bangumi_subject_id or 0)


def _assignment_media_kind(assignment: BgmAssignmentRef) -> str:
    return str(assignment.target_span.media_kind or assignment.target.media_kind or '')


def _assignment_episode_type(assignment: BgmAssignmentRef) -> str:
    return str(assignment.target_span.episode_type or assignment.target.episode_type or '')


def _rule_ref(rule: BgmToTmdbRecipeRule, index: int) -> str:
    return str(rule.name or f'rule_{index}').strip() or f'rule_{index}'


def _review_warnings_for_rule(rule: BgmToTmdbRecipeRule, rule_ref: str, matched: list[BgmAssignmentRef]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if not matched or rule.rule_type == 'supplemental_group':
        return warnings
    if rule.confidence == 'Low':
        warnings.append({
            'severity': 'review',
            'code': 'low_confidence_tmdb_recipe_rule',
            'rule': rule_ref,
            'message': 'A BGM->TMDB recipe rule is marked Low confidence and needs stronger semantic evidence or fail_closed.',
            'repair_hint': f'For {rule_ref}, compare TMDB title/original/alias/year/season/episode-title cards against the BGM subject and raise confidence only with concrete evidence.',
        })
    if not str(rule.reason or '').strip():
        warnings.append({
            'severity': 'review',
            'code': 'missing_tmdb_semantic_reason',
            'rule': rule_ref,
            'message': 'A BGM->TMDB recipe rule needs a concise semantic reason.',
            'repair_hint': f'Add one evidence sentence for {rule_ref}. For tmdb_absent_group, name the missing TMDB legal node boundary and the title/episode-title checks.',
        })
    return warnings


def _dedupe_review_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for warning in warnings:
        key = (str(warning.get('code') or ''), str(warning.get('rule') or warning.get('source_path') or ''))
        if key in seen:
            continue
        seen.add(key)
        result.append(warning)
    return result


def _issue(ref: str, issue_code: str, message: str, *, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(
        ref=str(ref or ''),
        issue_code=issue_code,
        severity='blocked',
        message=message,
        related_refs=[str(item) for item in (related_refs or []) if str(item)],
    )


def _norm_text(value: str) -> str:
    return str(value or '').strip().casefold()


def _dedupe_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
