from __future__ import annotations

import ast
import fnmatch
import re
from collections import Counter
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import BangumiItemCard, CaseVerifierResult, VerifierIssue
from .workspace import CaseEvidenceWorkspace


RecipeDisposition = Literal[
    'map_to_bangumi',
    'non_bangumi_or_supplemental',
    'needs_more_evidence',
    'unaligned_fail_closed',
]
RecipeSourceUnit = Literal['single_file', 'single_file_multi_episode']


class RecipeSelector(BaseModel):
    path_glob: str = ''
    filename_regex: str = ''
    exact_paths: list[str] = Field(default_factory=list)
    exclude_regex: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class RecipeTarget(BaseModel):
    bangumi_subject_id: int = 0
    media_kind: Literal['tv', 'movie', 'ova', 'oad', 'sp', 'special', 'unknown'] = 'unknown'
    episode_id: int = 0
    episode_type: Literal['main', 'regular', 'special', 'ova', 'oad', 'movie', 'unknown'] = 'unknown'
    sort: int | None = None
    ep: int | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class RecipeEpisode(BaseModel):
    capture: str = 'ep'
    offset: str = 'EP'
    range: str = ''
    number_field: Literal['sort', 'ep'] = 'sort'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class OrganizeRecipeRule(BaseModel):
    name: str = ''
    source_unit: RecipeSourceUnit = 'single_file'
    select: RecipeSelector = Field(default_factory=RecipeSelector)
    target: RecipeTarget = Field(default_factory=RecipeTarget)
    episode: RecipeEpisode = Field(default_factory=RecipeEpisode)
    disposition: RecipeDisposition = 'map_to_bangumi'
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class OrganizeRecipeDraft(BaseModel):
    version: int = 1
    summary: str = ''
    rules: list[OrganizeRecipeRule] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CompiledTarget(BaseModel):
    bangumi_subject_id: int = 0
    media_kind: str = ''
    episode_id: int = 0
    episode_type: str = ''
    sort: int | None = None
    ep: int | None = None
    title: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CompiledTargetSpan(BaseModel):
    bangumi_subject_id: int = 0
    media_kind: str = ''
    episode_ids: list[int] = Field(default_factory=list)
    sort_start: int | None = None
    sort_end: int | None = None
    episode_type: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CompiledOrganizeAssignment(BaseModel):
    source_path: str = ''
    disposition: RecipeDisposition = 'map_to_bangumi'
    rule_name: str = ''
    target: CompiledTarget = Field(default_factory=CompiledTarget)
    target_span: CompiledTargetSpan = Field(default_factory=CompiledTargetSpan)
    extracted_episode_number: int | None = None
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CompiledRuleSummary(BaseModel):
    rule_name: str = ''
    matched_paths: list[str] = Field(default_factory=list)
    disposition: RecipeDisposition = 'map_to_bangumi'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CompiledOrganizePlan(BaseModel):
    assignments: list[CompiledOrganizeAssignment] = Field(default_factory=list)
    rule_summaries: list[CompiledRuleSummary] = Field(default_factory=list)
    main_paths: list[str] = Field(default_factory=list)
    covered_paths: list[str] = Field(default_factory=list)
    uncovered_paths: list[str] = Field(default_factory=list)
    duplicate_coverage_paths: list[str] = Field(default_factory=list)
    duplicate_target_keys: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


def compile_and_verify_organize_recipe(
    workspace: CaseEvidenceWorkspace,
    recipe: OrganizeRecipeDraft,
) -> tuple[CompiledOrganizePlan, CaseVerifierResult]:
    issues: list[VerifierIssue] = []
    assignments: list[CompiledOrganizeAssignment] = []
    rule_summaries: list[CompiledRuleSummary] = []
    main_files = _main_files(workspace)
    files_by_path = {_norm_path(card.path): card for card in main_files if _norm_path(card.path)}
    main_paths = sorted(files_by_path)
    target_index = _TargetIndex.from_workspace(workspace)

    if not recipe.rules:
        issues.append(_issue('recipe', 'missing_rules', 'OrganizeRecipeDraft.rules must not be empty'))

    assignment_entries: list[tuple[CompiledOrganizeAssignment, list[VerifierIssue]]] = []
    for index, rule in enumerate(recipe.rules, start=1):
        rule_ref = _rule_ref(rule, index)
        matched_paths, selector_issues = _match_rule_paths(rule, files_by_path)
        issues.extend(_issue(rule_ref, code, message, related_refs=refs) for code, message, refs in selector_issues)
        if not matched_paths:
            issues.append(_issue(rule_ref, 'zero_match', 'recipe rule matched no visible main files'))
        rule_summaries.append(CompiledRuleSummary(
            rule_name=rule_ref,
            matched_paths=matched_paths,
            disposition=rule.disposition,
        ))
        for path in matched_paths:
            assignment, assignment_issues = _compile_rule_assignment(
                rule=rule,
                rule_ref=rule_ref,
                source_path=path,
                source_file=files_by_path.get(path),
                target_index=target_index,
            )
            assignment_entries.append((assignment, assignment_issues))

    assignment_entries = _apply_exact_supplemental_overrides(assignment_entries, recipe.rules)
    for assignment, assignment_issues in assignment_entries:
        assignments.append(assignment)
        issues.extend(assignment_issues)
    coverage_counts = Counter(assignment.source_path for assignment in assignments if assignment.source_path)
    covered_paths = sorted(coverage_counts)
    duplicate_coverage_paths = sorted(path for path, count in coverage_counts.items() if count > 1)
    uncovered_paths = sorted(path for path in main_paths if coverage_counts.get(path, 0) == 0)
    duplicate_target_groups = _duplicate_target_groups(assignments)
    duplicate_target_keys = sorted(duplicate_target_groups)

    for path in duplicate_coverage_paths:
        issues.append(_issue(path, 'duplicate_coverage', 'source path is covered by more than one recipe rule', related_refs=[path]))
    for path in uncovered_paths:
        issues.append(_issue(path, 'uncovered_path', 'visible main source path is not covered by any recipe rule', related_refs=[path]))
    for key in duplicate_target_keys:
        group = duplicate_target_groups[key]
        related_paths = sorted({assignment.source_path for assignment in group if assignment.source_path})
        rule_names = sorted({assignment.rule_name for assignment in group if assignment.rule_name})
        rule_summary = ', '.join(rule_names[:4])
        if len(rule_names) > 4:
            rule_summary += f', +{len(rule_names) - 4} more'
        message = f'Bangumi target {key} is assigned by {len(related_paths) or len(group)} mapped source paths'
        if rule_summary:
            message += f' via rules: {rule_summary}'
        issues.append(_issue(key, 'duplicate_target', message, related_refs=related_paths))

    for assignment in assignments:
        if assignment.disposition in {'needs_more_evidence', 'unaligned_fail_closed'}:
            issues.append(_issue(assignment.source_path, 'unresolved_assignment', 'accepted recipe cannot contain unresolved dispositions', related_refs=[assignment.source_path]))

    plan = CompiledOrganizePlan(
        assignments=assignments,
        rule_summaries=rule_summaries,
        main_paths=main_paths,
        covered_paths=covered_paths,
        uncovered_paths=uncovered_paths,
        duplicate_coverage_paths=duplicate_coverage_paths,
        duplicate_target_keys=duplicate_target_keys,
    )
    blocking = [issue for issue in issues if issue.severity == 'blocked']
    return plan, CaseVerifierResult(
        passed=not blocking,
        issues=issues,
        summary='accepted' if not blocking else f'blocked by {len(blocking)} recipe verifier issue(s)',
    )


def recipe_accounting(plan: CompiledOrganizePlan) -> dict[str, int | bool]:
    mapped = sum(1 for item in plan.assignments if item.disposition == 'map_to_bangumi')
    excluded = sum(1 for item in plan.assignments if item.disposition == 'non_bangumi_or_supplemental')
    unresolved = sum(1 for item in plan.assignments if item.disposition in {'needs_more_evidence', 'unaligned_fail_closed'})
    mapped_target_episode_count = sum(_target_episode_count_for_assignment(item) for item in plan.assignments)
    single_file_multi_episode_count = sum(1 for item in plan.assignments if item.target_span.episode_ids)
    return {
        'recipe_rule_count': len(plan.rule_summaries),
        'main_path_count': len(plan.main_paths),
        'matched_path_count': len(plan.covered_paths),
        'mapped_path_count': mapped,
        'mapped_file_count': mapped,
        'mapped_target_episode_count': mapped_target_episode_count,
        'single_file_multi_episode_count': single_file_multi_episode_count,
        'excluded_path_count': excluded,
        'unresolved_path_count': unresolved,
        'uncovered_path_count': len(plan.uncovered_paths),
        'duplicate_coverage_count': len(plan.duplicate_coverage_paths),
        'duplicate_target_count': len(plan.duplicate_target_keys),
        'accepted_accounting_ready': bool(
            plan.main_paths
            and not plan.uncovered_paths
            and not plan.duplicate_coverage_paths
            and not plan.duplicate_target_keys
            and unresolved == 0
        ),
    }


def _main_files(workspace: CaseEvidenceWorkspace) -> list[Any]:
    main_refs = set(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])
    files = list(getattr(workspace, 'local_files', []) or [])
    if main_refs:
        return [card for card in files if getattr(card, 'ref', '') in main_refs]
    return [card for card in files if bool(getattr(card, 'is_main', False))]


def _norm_path(path: str) -> str:
    return str(path or '').replace('\\', '/').strip().lstrip('./')


def _basename(path: str) -> str:
    path = _norm_path(path)
    return path.rsplit('/', 1)[-1]


def _rule_ref(rule: OrganizeRecipeRule, index: int) -> str:
    return str(rule.name or f'rule_{index}').strip() or f'rule_{index}'


def _issue(ref: str, issue_code: str, message: str, *, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(ref=ref, issue_code=issue_code, severity='blocked', message=message, related_refs=list(related_refs or []))


def _match_rule_paths(rule: OrganizeRecipeRule, files_by_path: dict[str, Any]) -> tuple[list[str], list[tuple[str, str, list[str]]]]:
    selector = rule.select
    issues: list[tuple[str, str, list[str]]] = []
    exact_paths = [_norm_path(path) for path in selector.exact_paths if _norm_path(path)]
    exact_path_set = set(exact_paths)
    for path in exact_paths:
        if path not in files_by_path:
            issues.append(('unknown_exact_path', f'exact path is not in the visible file universe: {path}', [path]))

    try:
        filename_regex = _compile_filename_regex(selector.filename_regex)
    except re.error as exc:
        issues.append(('invalid_filename_regex', f'filename_regex is invalid: {exc}', []))
        filename_regex = None

    try:
        exclude_regex = re.compile(_normalize_regex(selector.exclude_regex), re.IGNORECASE) if selector.exclude_regex else None
    except re.error as exc:
        issues.append(('invalid_exclude_regex', f'exclude_regex is invalid: {exc}', []))
        exclude_regex = None

    matched: list[str] = []
    for path in sorted(files_by_path):
        if exclude_regex and (exclude_regex.search(path) or exclude_regex.search(_basename(path))):
            continue
        exact_match = bool(exact_paths) and path in exact_path_set
        pattern_match = True
        if selector.path_glob:
            pattern_match = _glob_match(path, selector.path_glob)
        if filename_regex is not None and selector.filename_regex:
            pattern_match = pattern_match and bool(filename_regex.search(_basename(path)) or filename_regex.search(path))
        if (
            not exact_match
            and pattern_match
            and filename_regex is not None
            and not _path_is_inside_rule_episode_range(rule, path, filename_regex)
        ):
            pattern_match = False
        if exact_match or (not exact_paths and (selector.path_glob or selector.filename_regex) and pattern_match):
            matched.append(path)

    if not exact_paths and not selector.path_glob and not selector.filename_regex:
        issues.append(('empty_selector', 'selector must include exact_paths, path_glob, or filename_regex', []))
    return matched, issues


def _glob_match(path: str, pattern: str) -> bool:
    pattern = _norm_path(pattern)
    variants = [pattern]
    if pattern.startswith('**/'):
        variants.append(pattern[3:])
    return any(fnmatch.fnmatch(path, item) for item in variants)


def _normalize_regex(pattern: str) -> str:
    return str(pattern or '').replace('(?<', '(?P<')


def _compile_filename_regex(pattern: str) -> re.Pattern[str] | None:
    pattern = str(pattern or '')
    if not pattern:
        return None
    if '{' in pattern and '}' in pattern:
        pattern = _token_pattern_to_regex(pattern)
    return re.compile(_normalize_regex(pattern), re.IGNORECASE)


def _token_pattern_to_regex(pattern: str) -> str:
    parts: list[str] = []
    index = 0
    regex_style = _looks_like_regex_template(pattern)
    for match in re.finditer(r'\{([A-Za-z_][A-Za-z0-9_]*)(?::0?(\d+)d?)?\}', pattern):
        segment = pattern[index:match.start()]
        parts.append(segment if regex_style else re.escape(segment))
        name = match.group(1)
        width = int(match.group(2) or 0)
        if name == 'ep':
            parts.append(rf'(?P<{name}>\d{{{width}}})' if width > 0 else rf'(?P<{name}>\d+)')
        else:
            parts.append(r'.*?')
        index = match.end()
    segment = pattern[index:]
    parts.append(segment if regex_style else re.escape(segment))
    return ''.join(parts)


def _looks_like_regex_template(pattern: str) -> bool:
    return bool(re.search(r'\\|(?<!\{)[\[\]\(\)\|\^\$\+\*\?]', str(pattern or '')))


def _extract_episode_number(rule: OrganizeRecipeRule, source_path: str) -> tuple[int | None, VerifierIssue | None]:
    if rule.source_unit == 'single_file_multi_episode':
        return None, None
    if rule.target.episode_id or rule.target.sort is not None or rule.target.ep is not None:
        return None, None
    regex = _compile_filename_regex(rule.select.filename_regex)
    if regex is None:
        return None, _issue(source_path, 'missing_episode_locator', 'mapped rule needs episode_id, target sort/ep, or filename_regex with an episode capture', related_refs=[source_path])
    raw_episode, capture_issue = _extract_raw_episode_capture(
        rule=rule,
        source_path=source_path,
        regex=regex,
    )
    if capture_issue == 'miss':
        return None, _issue(source_path, 'episode_locator_miss', 'filename_regex did not match source path during episode extraction', related_refs=[source_path])
    if capture_issue == 'missing_capture':
        return None, _issue(
            source_path,
            'invalid_episode_capture',
            'filename_regex matched but has no numeric episode capture; sequence rules need {ep}, while one-file rules should use exact_paths plus a fixed target',
            related_refs=[source_path],
        )
    if capture_issue == 'invalid':
        return None, _issue(source_path, 'invalid_episode_capture', 'episode capture is not an integer', related_refs=[source_path])
    ep = int(raw_episode or 0)
    if rule.episode.range and not _range_contains(rule.episode.range, ep):
        return ep, _issue(source_path, 'episode_out_of_range', 'episode capture is outside the rule range', related_refs=[source_path])
    try:
        return _eval_episode_expr(rule.episode.offset or 'EP', ep), None
    except ValueError as exc:
        return ep, _issue(source_path, 'invalid_episode_offset', str(exc), related_refs=[source_path])


def _path_is_inside_rule_episode_range(rule: OrganizeRecipeRule, source_path: str, regex: re.Pattern[str]) -> bool:
    if (
        rule.disposition != 'map_to_bangumi'
        or not rule.episode.range
        or rule.target.episode_id
        or rule.target.sort is not None
        or rule.target.ep is not None
    ):
        return True
    raw_episode, capture_issue = _extract_raw_episode_capture(
        rule=rule,
        source_path=source_path,
        regex=regex,
    )
    if capture_issue is not None or raw_episode is None:
        return True
    return _range_contains(rule.episode.range, raw_episode)


def _extract_raw_episode_capture(
    *,
    rule: OrganizeRecipeRule,
    source_path: str,
    regex: re.Pattern[str],
) -> tuple[int | None, str | None]:
    match = regex.search(_basename(source_path)) or regex.search(source_path)
    if match is None:
        return None, 'miss'
    capture = str(rule.episode.capture or 'ep')
    raw_value = None
    if capture in match.groupdict():
        raw_value = match.group(capture)
    elif match.groups():
        raw_value = match.group(1)
    if raw_value is None or str(raw_value) == '':
        return None, 'missing_capture'
    try:
        return int(str(raw_value).lstrip('0') or '0'), None
    except ValueError:
        return None, 'invalid'


def _range_contains(spec: str, value: int) -> bool:
    for part in [item.strip() for item in str(spec or '').split(',') if item.strip()]:
        if '-' in part:
            left, right = part.split('-', 1)
            try:
                if int(left) <= value <= int(right):
                    return True
            except ValueError:
                continue
        else:
            try:
                if int(part) == value:
                    return True
            except ValueError:
                continue
    return False


def _episode_numbers_from_range(spec: str) -> tuple[list[int], str]:
    numbers: list[int] = []
    for part in [item.strip() for item in str(spec or '').split(',') if item.strip()]:
        if '-' in part:
            left_text, right_text = part.split('-', 1)
            try:
                left = int(left_text)
                right = int(right_text)
            except ValueError:
                return [], f'invalid episode range segment: {part}'
            if right < left:
                return [], f'episode range end is before start: {part}'
            numbers.extend(range(left, right + 1))
        else:
            try:
                numbers.append(int(part))
            except ValueError:
                return [], f'invalid episode range segment: {part}'
    deduped = list(dict.fromkeys(numbers))
    if not deduped:
        return [], 'episode_range is required'
    return deduped, ''


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
        raise ValueError('episode offset expression may only use EP, integers, +, -, *, unary signs, and parentheses')

    return visit(tree)


class _TargetIndex:
    def __init__(self, *, subjects: dict[int, Any], episodes: list[tuple[int, BangumiItemCard]]) -> None:
        self.subjects = subjects
        self.episodes = episodes

    @classmethod
    def from_workspace(cls, workspace: CaseEvidenceWorkspace) -> '_TargetIndex':
        subjects_by_ref = {str(card.ref or ''): card for card in getattr(workspace, 'bangumi_subjects', []) or [] if str(card.ref or '')}
        subjects = {
            int(getattr(card, 'subject_id', 0) or 0): card
            for card in subjects_by_ref.values()
            if int(getattr(card, 'subject_id', 0) or 0) > 0
        }
        episodes: list[tuple[int, BangumiItemCard]] = []
        for item in getattr(workspace, 'bangumi_items', []) or []:
            subject = subjects_by_ref.get(str(getattr(item, 'subject_ref', '') or ''))
            subject_id = int(getattr(subject, 'subject_id', 0) or 0) if subject is not None else _subject_id_from_ref(str(getattr(item, 'subject_ref', '') or ''))
            if subject_id > 0:
                subjects.setdefault(subject_id, subject)
                episodes.append((subject_id, item))
        return cls(subjects=subjects, episodes=episodes)

    def find_episode(self, target: RecipeTarget, wanted_number: int | None, *, number_field: str = 'sort') -> BangumiItemCard | None:
        subject_id = int(target.bangumi_subject_id or 0)
        candidates = [item for sid, item in self.episodes if sid == subject_id]
        if target.episode_id:
            item = next((candidate for candidate in candidates if int(getattr(candidate, 'episode_id', 0) or 0) == int(target.episode_id)), None)
            if item is None:
                return None
            return item if _item_matches_type(item, target.episode_type) else None
        wanted_sort = target.sort
        wanted_ep = target.ep
        if wanted_number is not None and wanted_sort is None and wanted_ep is None:
            if str(number_field or 'sort') == 'ep':
                wanted_ep = wanted_number
            else:
                wanted_sort = wanted_number
        for item in candidates:
            if wanted_sort is not None and int(getattr(item, 'sort', 0) or 0) != int(wanted_sort):
                continue
            if wanted_ep is not None and int(getattr(item, 'ep', 0) or 0) != int(wanted_ep):
                continue
            if not _item_matches_type(item, target.episode_type):
                continue
            return item
        return None


def _item_matches_type(item: BangumiItemCard, expected: str) -> bool:
    expected = str(expected or 'unknown')
    if expected in {'', 'unknown'}:
        return True
    item_type = str(getattr(item, 'type', '') or '').casefold()
    item_kind = str(getattr(item, 'kind', '') or '').casefold()
    item_item_kind = str(getattr(item, 'item_kind', '') or '').casefold()
    if expected in {'main', 'regular'}:
        return item_type in {'0', 'regular', 'main', ''} and item_item_kind in {'episode', ''}
    if expected in {'special', 'sp', 'ova', 'oad'}:
        return item_type not in {'0', 'regular', 'main'} or item_kind in {'special', expected} or item_item_kind == 'special'
    if expected == 'movie':
        return item_item_kind == 'movie' or item_kind == 'movie'
    return True


def _subject_id_from_ref(ref: str) -> int:
    match = re.fullmatch(r'subject:(\d+)', str(ref or '').strip())
    return int(match.group(1)) if match else 0


def _compile_rule_assignment(
    *,
    rule: OrganizeRecipeRule,
    rule_ref: str,
    source_path: str,
    source_file: Any | None,
    target_index: _TargetIndex,
) -> tuple[CompiledOrganizeAssignment, list[VerifierIssue]]:
    issues: list[VerifierIssue] = []
    target = CompiledTarget(
        bangumi_subject_id=int(rule.target.bangumi_subject_id or 0),
        media_kind=rule.target.media_kind,
        episode_id=int(rule.target.episode_id or 0),
        episode_type=rule.target.episode_type,
        sort=rule.target.sort,
        ep=rule.target.ep,
    )
    extracted_number: int | None = None
    target_span = CompiledTargetSpan()
    if rule.disposition == 'map_to_bangumi':
        if int(rule.target.bangumi_subject_id or 0) <= 0:
            issues.append(_issue(source_path, 'missing_subject_id', 'mapped rule target must include bangumi_subject_id', related_refs=[source_path]))
        elif int(rule.target.bangumi_subject_id or 0) not in target_index.subjects:
            issues.append(_issue(
                source_path,
                'unknown_subject_id',
                f'bangumi_subject_id {int(rule.target.bangumi_subject_id or 0)} has not been exposed by Bangumi evidence tools and cannot be used',
                related_refs=[source_path, f'subject:{int(rule.target.bangumi_subject_id or 0)}'],
            ))
        if rule.source_unit == 'single_file_multi_episode':
            target_span, span_issues = _compile_single_file_multi_episode_target(
                rule=rule,
                source_path=source_path,
                source_file=source_file,
                target_index=target_index,
            )
            issues.extend(span_issues)
            if target_span.episode_ids:
                first_episode = target_index.find_episode(rule.target, target_span.sort_start)
                target = CompiledTarget(
                    bangumi_subject_id=target_span.bangumi_subject_id,
                    media_kind=target_span.media_kind,
                    episode_id=target_span.episode_ids[0],
                    episode_type=target_span.episode_type,
                    sort=target_span.sort_start,
                    ep=int(getattr(first_episode, 'ep', 0) or 0) if first_episode is not None else None,
                    title=str(getattr(first_episode, 'title', '') or getattr(first_episode, 'name_cn', '') or getattr(first_episode, 'name', '') or '') if first_episode is not None else '',
                )
            return CompiledOrganizeAssignment(
                source_path=source_path,
                disposition=rule.disposition,
                rule_name=rule_ref,
                target=target,
                target_span=target_span,
                extracted_episode_number=extracted_number,
                reason=rule.reason,
            ), issues
        subject_level_movie = (
            rule.target.media_kind == 'movie'
            and not rule.target.episode_id
            and rule.target.sort is None
            and rule.target.ep is None
            and not rule.select.filename_regex
        )
        if not subject_level_movie:
            ordered_exact_number, ordered_exact_issue = _episode_number_from_exact_path_order(rule, source_path)
            if ordered_exact_issue is not None:
                issues.append(ordered_exact_issue)
                extraction_issue = None
            elif ordered_exact_number is not None:
                extracted_number = ordered_exact_number
                extraction_issue = None
            else:
                extracted_number, extraction_issue = _extract_episode_number(rule, source_path)
            if extraction_issue is not None:
                issues.append(extraction_issue)
        episode = target_index.find_episode(rule.target, extracted_number, number_field=rule.episode.number_field)
        if episode is not None:
            target = CompiledTarget(
                bangumi_subject_id=int(rule.target.bangumi_subject_id or 0),
                media_kind=rule.target.media_kind,
                episode_id=int(getattr(episode, 'episode_id', 0) or 0),
                episode_type=rule.target.episode_type if rule.target.episode_type != 'unknown' else str(getattr(episode, 'item_kind', '') or getattr(episode, 'type', '') or ''),
                sort=int(getattr(episode, 'sort', 0) or 0),
                ep=int(getattr(episode, 'ep', 0) or 0),
                title=str(getattr(episode, 'title', '') or getattr(episode, 'name_cn', '') or getattr(episode, 'name', '') or ''),
            )
        elif subject_level_movie and extracted_number is None:
            target = target.model_copy(update={'episode_type': 'movie'})
        else:
            issues.append(_issue(source_path, 'missing_target_episode', 'mapped rule target did not resolve to a visible Bangumi episode', related_refs=[source_path, str(rule.target.bangumi_subject_id)]))
    return CompiledOrganizeAssignment(
        source_path=source_path,
        disposition=rule.disposition,
        rule_name=rule_ref,
        target=target,
        target_span=target_span,
        extracted_episode_number=extracted_number,
        reason=rule.reason,
    ), issues


def _episode_number_from_exact_path_order(rule: OrganizeRecipeRule, source_path: str) -> tuple[int | None, VerifierIssue | None]:
    if (
        rule.source_unit != 'single_file'
        or rule.disposition != 'map_to_bangumi'
        or rule.target.episode_id
        or rule.target.sort is not None
        or rule.target.ep is not None
        or rule.select.filename_regex
        or not rule.episode.range
    ):
        return None, None
    exact_paths = [_norm_path(path) for path in rule.select.exact_paths if _norm_path(path)]
    if len(exact_paths) <= 1:
        return None, None
    normalized_source = _norm_path(source_path)
    if normalized_source not in exact_paths:
        return None, None
    raw_numbers, range_error = _episode_numbers_from_range(rule.episode.range)
    if range_error:
        return None, _issue(source_path, 'invalid_episode_range', range_error, related_refs=[source_path])
    if len(raw_numbers) != len(exact_paths):
        return None, _issue(
            source_path,
            'invalid_exact_path_episode_range',
            'multi-file exact_paths with episode_range must have the same number of paths and episode numbers',
            related_refs=[source_path],
        )
    raw_number = raw_numbers[exact_paths.index(normalized_source)]
    try:
        return _eval_episode_expr(rule.episode.offset or 'EP', raw_number), None
    except ValueError as exc:
        return None, _issue(source_path, 'invalid_episode_offset', str(exc), related_refs=[source_path])


def _apply_exact_supplemental_overrides(
    assignment_entries: list[tuple[CompiledOrganizeAssignment, list[VerifierIssue]]],
    rules: list[OrganizeRecipeRule],
) -> list[tuple[CompiledOrganizeAssignment, list[VerifierIssue]]]:
    exact_supplemental_paths = {
        assignment.source_path
        for assignment, _issues in assignment_entries
        if assignment.disposition == 'non_bangumi_or_supplemental'
        and _rule_has_exact_selector(rules, assignment.rule_name)
    }
    if not exact_supplemental_paths:
        return assignment_entries
    kept_entries: list[tuple[CompiledOrganizeAssignment, list[VerifierIssue]]] = []
    for entry in assignment_entries:
        assignment = entry[0]
        if (
            assignment.source_path in exact_supplemental_paths
            and assignment.disposition == 'map_to_bangumi'
            and not _rule_has_exact_selector(rules, assignment.rule_name)
        ):
            continue
        kept_entries.append(entry)
    return kept_entries


def _rule_has_exact_selector(rules: list[OrganizeRecipeRule], rule_name: str) -> bool:
    for index, rule in enumerate(rules, start=1):
        if _rule_ref(rule, index) == rule_name:
            return any(_norm_path(path) for path in rule.select.exact_paths)
    return False


def _compile_single_file_multi_episode_target(
    *,
    rule: OrganizeRecipeRule,
    source_path: str,
    source_file: Any | None,
    target_index: _TargetIndex,
) -> tuple[CompiledTargetSpan, list[VerifierIssue]]:
    issues: list[VerifierIssue] = []
    target_span = CompiledTargetSpan(
        bangumi_subject_id=int(rule.target.bangumi_subject_id or 0),
        media_kind=rule.target.media_kind,
        episode_type=rule.target.episode_type,
    )
    exact_paths = [_norm_path(path) for path in rule.select.exact_paths if _norm_path(path)]
    if len(exact_paths) != 1:
        issues.append(_issue(
            source_path,
            'invalid_source_unit_selector',
            'source_unit single_file_multi_episode requires exactly one exact_paths entry',
            related_refs=[source_path],
        ))
    if rule.target.episode_id or rule.target.sort is not None or rule.target.ep is not None:
        issues.append(_issue(
            source_path,
            'invalid_multi_episode_target_locator',
            'single_file_multi_episode must use episode_range plus subject/type, not episode_id/sort/ep for one fixed episode',
            related_refs=[source_path],
        ))

    raw_numbers, range_error = _episode_numbers_from_range(rule.episode.range)
    if range_error:
        issues.append(_issue(source_path, 'invalid_episode_range', range_error, related_refs=[source_path]))
        return target_span, issues
    if len(raw_numbers) < 2:
        issues.append(_issue(
            source_path,
            'invalid_episode_range',
            'single_file_multi_episode requires episode_range to cover at least two episodes',
            related_refs=[source_path],
        ))

    wanted_numbers: list[int] = []
    for number in raw_numbers:
        try:
            wanted_numbers.append(_eval_episode_expr(rule.episode.offset or 'EP', number))
        except ValueError as exc:
            issues.append(_issue(source_path, 'invalid_episode_offset', str(exc), related_refs=[source_path]))
            return target_span, issues

    episodes: list[BangumiItemCard] = []
    missing_numbers: list[int] = []
    for wanted_number in wanted_numbers:
        episode = target_index.find_episode(rule.target, wanted_number, number_field=rule.episode.number_field)
        if episode is None:
            missing_numbers.append(wanted_number)
        else:
            episodes.append(episode)
    if missing_numbers:
        issues.append(_issue(
            source_path,
            'missing_target_episode',
            f'single_file_multi_episode range did not resolve exposed Bangumi episode sort(s): {missing_numbers}',
            related_refs=[source_path, f'subject:{int(rule.target.bangumi_subject_id or 0)}'],
        ))
    if episodes:
        sorts = [int(getattr(item, 'sort', 0) or 0) for item in episodes]
        target_span = CompiledTargetSpan(
            bangumi_subject_id=int(rule.target.bangumi_subject_id or 0),
            media_kind=rule.target.media_kind,
            episode_ids=[int(getattr(item, 'episode_id', 0) or 0) for item in episodes if int(getattr(item, 'episode_id', 0) or 0) > 0],
            sort_start=min(sorts) if sorts else None,
            sort_end=max(sorts) if sorts else None,
            episode_type=rule.target.episode_type if rule.target.episode_type != 'unknown' else str(getattr(episodes[0], 'item_kind', '') or getattr(episodes[0], 'type', '') or ''),
        )

    if not _single_file_multi_episode_evidence_is_supported(
        source_file,
        episodes,
        expected_count=len(raw_numbers),
        source_path=source_path,
        raw_numbers=raw_numbers,
    ):
        issues.append(_issue(
            source_path,
            'missing_multi_episode_evidence',
            'single_file_multi_episode needs mechanical support from local chapter count, an explicit filename episode range, or local duration close to the sum of target episode durations',
            related_refs=[source_path],
        ))
    return target_span, issues


def _duplicate_target_keys(assignments: list[CompiledOrganizeAssignment]) -> list[str]:
    return sorted(_duplicate_target_groups(assignments))


def _duplicate_target_groups(assignments: list[CompiledOrganizeAssignment]) -> dict[str, list[CompiledOrganizeAssignment]]:
    groups: dict[str, list[CompiledOrganizeAssignment]] = {}
    for assignment in assignments:
        for key in _target_keys_for_assignment(assignment):
            groups.setdefault(key, []).append(assignment)
    return {
        key: group
        for key, group in sorted(groups.items())
        if len(group) > 1
    }


def _target_key_for_assignment(assignment: CompiledOrganizeAssignment) -> str:
    keys = _target_keys_for_assignment(assignment)
    return keys[0] if keys else ''


def _target_keys_for_assignment(assignment: CompiledOrganizeAssignment) -> list[str]:
    if assignment.disposition != 'map_to_bangumi':
        return []
    if assignment.target_span.episode_ids:
        return [f'episode:{episode_id}' for episode_id in assignment.target_span.episode_ids if episode_id]
    target = assignment.target
    if target.episode_id:
        return [f'episode:{target.episode_id}']
    if target.bangumi_subject_id and target.sort is not None:
        return [f'subject:{target.bangumi_subject_id}:sort:{target.sort}']
    if target.bangumi_subject_id and target.media_kind == 'movie':
        return [f'subject:{target.bangumi_subject_id}:movie']
    return []


def _target_episode_count_for_assignment(assignment: CompiledOrganizeAssignment) -> int:
    if assignment.disposition != 'map_to_bangumi':
        return 0
    if assignment.target_span.episode_ids:
        return len(assignment.target_span.episode_ids)
    return 1 if _target_keys_for_assignment(assignment) else 0


def _single_file_multi_episode_evidence_is_supported(
    source_file: Any | None,
    episodes: list[BangumiItemCard],
    *,
    expected_count: int,
    source_path: str = '',
    raw_numbers: list[int] | None = None,
) -> bool:
    container = _source_container_facts(source_file)
    chapter_count = _int_or_none(container.get('chapter_count')) or len([
        item for item in container.get('chapter_durations_seconds') or [] if _float_or_none(item) is not None
    ])
    if expected_count >= 2 and expected_count <= chapter_count <= expected_count + 2:
        return True
    if (
        expected_count >= 2
        and len(episodes) == expected_count
        and _source_path_has_declared_episode_range(source_path, raw_numbers or [])
    ):
        return True

    local_duration = _float_or_none(container.get('duration_seconds'))
    target_durations = [_episode_duration_seconds(item) for item in episodes]
    target_durations = [item for item in target_durations if item is not None and item > 0]
    if local_duration is None or local_duration <= 0:
        return False
    if len(target_durations) != expected_count:
        return False
    target_duration = sum(target_durations)
    if target_duration <= 0:
        return False
    return abs(local_duration - target_duration) <= max(180.0, target_duration * 0.15)


def _source_path_has_declared_episode_range(source_path: str, raw_numbers: list[int]) -> bool:
    if len(raw_numbers) < 2:
        return False
    start = raw_numbers[0]
    end = raw_numbers[-1]
    if start > end or raw_numbers != list(range(start, end + 1)):
        return False
    basename = _basename(source_path)
    for match in re.finditer(r'(?<!\d)(?:[A-Za-z]{0,4})?0*(\d{1,3})\s*[-~]\s*(?:[A-Za-z]{0,4})?0*(\d{1,3})(?!\d)', basename):
        token_start = int(match.group(1))
        token_end = int(match.group(2))
        if token_start == start and token_end == end:
            return True
    return False


def _source_container_facts(source_file: Any | None) -> dict[str, Any]:
    if source_file is None:
        return {}
    container = getattr(source_file, 'container_facts', {}) or {}
    return dict(container) if isinstance(container, dict) else {}


def _episode_duration_seconds(item: BangumiItemCard) -> float | None:
    explicit = _float_or_none(getattr(item, 'duration_seconds', None))
    if explicit and explicit > 0:
        return explicit
    return _parse_duration_seconds(str(getattr(item, 'duration', '') or ''))


def _parse_duration_seconds(text: str) -> float | None:
    text = str(text or '').strip().casefold()
    if not text:
        return None
    clock = re.fullmatch(r'(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?', text)
    if clock:
        left = int(clock.group(1))
        middle = int(clock.group(2))
        right = int(clock.group(3) or 0)
        if clock.group(3) is None:
            return float(left * 60 + middle)
        return float(left * 3600 + middle * 60 + right)
    match = re.fullmatch(r'(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours|小时))?\s*(?:(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|分))?\s*(?:(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒))?', text)
    if match and any(match.groups()):
        hours = float(match.group(1) or 0)
        minutes = float(match.group(2) or 0)
        seconds = float(match.group(3) or 0)
        return hours * 3600.0 + minutes * 60.0 + seconds
    numeric = re.fullmatch(r'\d+(?:\.\d+)?', text)
    if numeric:
        value = float(text)
        return value * 60.0 if value < 300 else value
    return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
