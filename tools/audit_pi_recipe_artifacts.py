from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LONG_SINGLE_EPISODE_SECONDS = 45 * 60
LONG_EXCLUDED_SECONDS = 15 * 60


@dataclass(frozen=True)
class RunRef:
    run_dir: Path
    sample: str = ''
    row: dict[str, Any] | None = None


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    if not isinstance(payload, dict):
        raise ValueError(f'JSON root must be an object: {path}')
    return payload


def _norm_path(value: object) -> str:
    return str(value or '').replace('\\', '/').strip().lstrip('./')


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


def _discover_runs(input_path: Path) -> list[RunRef]:
    input_path = input_path.resolve()
    if input_path.is_file():
        payload = _load_json(input_path)
        rows = payload.get('rows') if isinstance(payload.get('rows'), list) else []
        refs = _runs_from_rows(rows)
        if refs:
            return refs
        raise ValueError(f'No pi_run_dir rows found in {input_path}')
    if (input_path / 'summary.json').exists():
        summary = _load_json(input_path / 'summary.json')
        refs = _runs_from_rows(summary.get('rows') if isinstance(summary.get('rows'), list) else [])
        if refs:
            return refs
    if (input_path / 'final_result.json').exists() or (input_path / 'artifacts' / 'compiled_plan.json').exists():
        return [RunRef(run_dir=input_path)]
    refs = [RunRef(run_dir=path) for path in sorted(input_path.iterdir()) if path.is_dir() and (path / 'final_result.json').exists()]
    if refs:
        return refs
    raise ValueError(f'No Pi run artifacts found under {input_path}')


def _runs_from_rows(rows: list[Any]) -> list[RunRef]:
    refs: list[RunRef] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        run_dir = Path(str(row.get('pi_run_dir') or '').strip())
        if not str(run_dir):
            continue
        refs.append(RunRef(run_dir=run_dir, sample=str(row.get('sample') or ''), row=row))
    return refs


def audit_path(input_path: Path) -> dict[str, Any]:
    run_refs = _discover_runs(input_path)
    runs = [audit_run(ref) for ref in run_refs]
    issue_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    for run in runs:
        for issue in run.get('issues') or []:
            issue_counts[str(issue.get('code') or 'unknown')] += 1
            severity_counts[str(issue.get('severity') or 'unknown')] += 1
    return {
        'input': str(input_path),
        'run_count': len(runs),
        'issue_count': sum(issue_counts.values()),
        'issue_counts': dict(sorted(issue_counts.items())),
        'severity_counts': dict(sorted(severity_counts.items())),
        'runs_with_review_count': sum(1 for run in runs if any(issue.get('severity') == 'review' for issue in run.get('issues') or [])),
        'runs': runs,
    }


def audit_run(ref: RunRef) -> dict[str, Any]:
    run_dir = ref.run_dir.resolve()
    final_result = _load_optional_json(run_dir / 'final_result.json')
    compiled_plan = _compiled_plan(run_dir, final_result)
    organize_recipe = _organize_recipe(run_dir, final_result)
    case_input = _load_optional_json(run_dir / 'case_input.json')
    local_index = _local_index(case_input)
    row = ref.row or {}
    accounting = final_result.get('accounting') if isinstance(final_result.get('accounting'), dict) else {}
    assignments = compiled_plan.get('assignments') if isinstance(compiled_plan.get('assignments'), list) else []
    targeted_evidence_paths = _targeted_evidence_paths(run_dir)
    rule_contexts = _rule_contexts(assignments, local_index)

    issues: list[dict[str, Any]] = []

    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        source_path = _norm_path(assignment.get('source_path'))
        local = local_index.get(source_path, {})
        container = local.get('container_facts') if isinstance(local.get('container_facts'), dict) else {}
        fact_summary = local.get('fact_summary') if isinstance(local.get('fact_summary'), dict) else {}
        duration = _float_or_none(container.get('duration_seconds')) or _float_or_none(fact_summary.get('duration_seconds'))
        chapter_count = _int_or_none(container.get('chapter_count')) or _int_or_none(fact_summary.get('chapter_count')) or 0
        target = assignment.get('target') if isinstance(assignment.get('target'), dict) else {}
        target_span = assignment.get('target_span') if isinstance(assignment.get('target_span'), dict) else {}
        span_episode_ids = [item for item in target_span.get('episode_ids') or [] if item]
        disposition = str(assignment.get('disposition') or '')

        if disposition == 'map_to_bangumi':
            issues.extend(_mapped_assignment_issues(
                source_path=source_path,
                assignment=assignment,
                target=target,
                span_episode_ids=span_episode_ids,
                duration=duration,
                chapter_count=chapter_count,
                rule_contexts=rule_contexts,
            ))
        elif disposition == 'non_bangumi_or_supplemental':
            issues.extend(_excluded_assignment_issues(
                source_path=source_path,
                duration=duration,
                chapter_count=chapter_count,
                reason=str(assignment.get('reason') or ''),
                targeted_evidence_paths=targeted_evidence_paths,
            ))

    mapped_file_count = _int_or_none(accounting.get('mapped_file_count')) or _int_or_none(row.get('mapped_file_count')) or 0
    excluded_file_count = _int_or_none(accounting.get('excluded_path_count')) or _int_or_none(row.get('excluded_file_count')) or 0
    mapped_target_episode_count = _int_or_none(accounting.get('mapped_target_episode_count')) or _int_or_none(row.get('mapped_target_episode_count')) or 0
    single_file_multi_episode_count = _int_or_none(accounting.get('single_file_multi_episode_count')) or _int_or_none(row.get('single_file_multi_episode_count')) or 0
    if mapped_target_episode_count > mapped_file_count:
        issues.append(_issue(
            'info',
            'multi_episode_target_accounting',
            'Accepted recipe maps fewer files than target episodes.',
            metrics={
                'mapped_file_count': mapped_file_count,
                'mapped_target_episode_count': mapped_target_episode_count,
                'single_file_multi_episode_count': single_file_multi_episode_count,
            },
        ))
    if excluded_file_count >= 5 and mapped_file_count <= 1:
        issues.append(_issue(
            'info',
            'many_exclusions_relative_to_mapped',
            'Accepted recipe maps one or fewer files while excluding many visible files; skim excluded groups if this package shape is surprising.',
            metrics={'mapped_file_count': mapped_file_count, 'excluded_file_count': excluded_file_count},
        ))
    if _int_or_none(row.get('tool_rejection_count')) or row.get('tool_rejection_reason_counts'):
        issues.append(_issue(
            'info',
            'accepted_after_tool_rejection',
            'Pi reached accepted after at least one tool rejection; review repair behavior if this case is surprising.',
            metrics={'tool_rejection_count': row.get('tool_rejection_count'), 'tool_rejection_reason_counts': row.get('tool_rejection_reason_counts')},
        ))
    if (_int_or_none(row.get('pi_turn_count')) or 0) >= 15 or (_int_or_none(row.get('pi_tool_trace_count')) or 0) >= 20:
        issues.append(_issue(
            'info',
            'expensive_accepted_case',
            'Accepted case used a relatively large number of turns or tools.',
            metrics={'pi_turn_count': row.get('pi_turn_count'), 'pi_tool_trace_count': row.get('pi_tool_trace_count')},
        ))

    return {
        'sample': ref.sample,
        'run_dir': str(run_dir),
        'status': str(final_result.get('status') or row.get('status') or ''),
        'summary': str(final_result.get('summary') or row.get('summary') or ''),
        'metrics': {
            'rule_count': len(organize_recipe.get('rules') if isinstance(organize_recipe.get('rules'), list) else []),
            'assignment_count': len(assignments),
            'mapped_file_count': mapped_file_count,
            'mapped_target_episode_count': mapped_target_episode_count,
            'single_file_multi_episode_count': single_file_multi_episode_count,
            'excluded_file_count': excluded_file_count,
            'pi_turn_count': row.get('pi_turn_count'),
            'pi_tool_trace_count': row.get('pi_tool_trace_count'),
        },
        'issue_count': len(issues),
        'issues': issues,
    }


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _load_json(path)


def _compiled_plan(run_dir: Path, final_result: dict[str, Any]) -> dict[str, Any]:
    plan = final_result.get('compiled_plan')
    if isinstance(plan, dict):
        return plan
    return _load_optional_json(run_dir / 'artifacts' / 'compiled_plan.json')


def _organize_recipe(run_dir: Path, final_result: dict[str, Any]) -> dict[str, Any]:
    recipe = final_result.get('organize_recipe')
    if isinstance(recipe, dict):
        return recipe
    return _load_optional_json(run_dir / 'artifacts' / 'organize_recipe.json')


def _local_index(case_input: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context = case_input.get('context') if isinstance(case_input.get('context'), dict) else {}
    files = context.get('local_files') if isinstance(context.get('local_files'), list) else []
    result: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        source_path = _norm_path(item.get('source_path'))
        if source_path:
            result[source_path] = item
    return result


def _rule_contexts(assignments: list[Any], local_index: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        if str(assignment.get('disposition') or '') != 'map_to_bangumi':
            continue
        rule_name = str(assignment.get('rule_name') or '')
        subject_key = _subject_sequence_key(assignment)
        if not rule_name and not subject_key:
            continue
        source_path = _norm_path(assignment.get('source_path'))
        local = local_index.get(source_path, {})
        duration, chapter_count = _media_facts(local)
        item = {
            'source_path': source_path,
            'episode_number': _assignment_episode_number(assignment),
            'duration': duration,
            'chapter_count': chapter_count,
            'text': f"{source_path} {rule_name} {assignment.get('reason') or ''}",
        }
        if rule_name:
            grouped.setdefault(rule_name, []).append(item)
        if subject_key:
            grouped.setdefault(subject_key, []).append(item)

    contexts: dict[str, dict[str, Any]] = {}
    for rule_name, items in grouped.items():
        numbers = sorted({int(item['episode_number']) for item in items if item.get('episode_number') is not None})
        contexts[rule_name] = {
            'numbers': numbers,
            'mapped_count': len(items),
            'contiguous_numbers': bool(numbers and len(numbers) >= 3 and numbers == list(range(numbers[0], numbers[-1] + 1))),
            'long_no_chapter_count': sum(
                1
                for item in items
                if _float_or_none(item.get('duration')) is not None
                and LONG_SINGLE_EPISODE_SECONDS <= float(item['duration']) < 60 * 60
                and not (_int_or_none(item.get('chapter_count')) or 0)
            ),
            'has_long_format_term': any(_looks_like_long_format_release(str(item.get('text') or '')) for item in items),
        }
    return contexts


def _subject_sequence_key(assignment: dict[str, Any]) -> str:
    target = assignment.get('target') if isinstance(assignment.get('target'), dict) else {}
    subject_id = _int_or_none(target.get('bangumi_subject_id'))
    if not subject_id:
        return ''
    episode_type = str(target.get('episode_type') or '')
    media_kind = str(target.get('media_kind') or '')
    return f'subject:{subject_id}:{media_kind}:{episode_type}'


def _assignment_episode_number(assignment: dict[str, Any]) -> int | None:
    episode_number = _int_or_none(assignment.get('extracted_episode_number'))
    if episode_number is not None:
        return episode_number
    target = assignment.get('target') if isinstance(assignment.get('target'), dict) else {}
    return _int_or_none(target.get('sort')) or _int_or_none(target.get('ep'))


def _media_facts(local: dict[str, Any]) -> tuple[float | None, int]:
    container = local.get('container_facts') if isinstance(local.get('container_facts'), dict) else {}
    fact_summary = local.get('fact_summary') if isinstance(local.get('fact_summary'), dict) else {}
    duration = _float_or_none(container.get('duration_seconds')) or _float_or_none(fact_summary.get('duration_seconds'))
    chapter_count = _int_or_none(container.get('chapter_count')) or _int_or_none(fact_summary.get('chapter_count')) or 0
    return duration, chapter_count


def _mapped_assignment_issues(
    *,
    source_path: str,
    assignment: dict[str, Any],
    target: dict[str, Any],
    span_episode_ids: list[Any],
    duration: float | None,
    chapter_count: int,
    rule_contexts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    media_kind = str(target.get('media_kind') or '')
    episode_type = str(target.get('episode_type') or '')
    if len(span_episode_ids) >= 2:
        issues.append(_issue(
            'info',
            'single_file_multi_episode_mapping',
            'Mapped assignment covers multiple Bangumi episode targets.',
            source_path=source_path,
            target=target,
            metrics={'span_episode_count': len(span_episode_ids), 'duration_seconds': duration, 'chapter_count': chapter_count},
        ))
        return issues
    if duration is not None and duration >= LONG_SINGLE_EPISODE_SECONDS and media_kind != 'movie':
        if _looks_like_single_special_target(media_kind, episode_type, duration, chapter_count):
            issues.append(_issue(
                'info',
                'long_single_special_mapping',
                'Long local file maps to a single special/OVA-like Bangumi target; this can be a normal long-format entry.',
                source_path=source_path,
                target=target,
                metrics={'duration_seconds': duration, 'chapter_count': chapter_count, 'media_kind': media_kind, 'episode_type': episode_type},
            ))
            return issues
        if _looks_like_long_format_sequence_episode(assignment, target, duration, chapter_count, rule_contexts):
            issues.append(_issue(
                'info',
                'long_format_sequence_mapping',
                'Long local file is part of a contiguous extended/recut-style sequence; this can be a normal one-file-to-one-target mapping.',
                source_path=source_path,
                target=target,
                metrics={
                    'duration_seconds': duration,
                    'chapter_count': chapter_count,
                    'media_kind': media_kind,
                    'episode_type': episode_type,
                    'extracted_episode_number': assignment.get('extracted_episode_number'),
                },
            ))
            return issues
        if _looks_like_sequence_boundary_long_episode(assignment, target, duration, chapter_count, rule_contexts):
            issues.append(_issue(
                'info',
                'long_sequence_boundary_mapping',
                'Long local file maps to a single non-movie target, but it is a boundary episode in a multi-file sequence.',
                source_path=source_path,
                target=target,
                metrics={
                    'duration_seconds': duration,
                    'chapter_count': chapter_count,
                    'media_kind': media_kind,
                    'episode_type': episode_type,
                    'extracted_episode_number': assignment.get('extracted_episode_number'),
                },
            ))
            return issues
        issues.append(_issue(
            'review',
            'long_single_episode_mapping',
            'Long local file maps to a single non-movie Bangumi target; review whether it is a merged multi-episode file or the wrong target shape.',
            source_path=source_path,
            target=target,
            metrics={'duration_seconds': duration, 'chapter_count': chapter_count, 'media_kind': media_kind, 'episode_type': episode_type},
        ))
    if chapter_count >= 2 and not span_episode_ids:
        issues.append(_issue(
            'review',
            'chaptered_file_single_episode_mapping',
            'Chaptered local file maps to one target only; review whether chapters represent multiple episodes or ordinary chapters.',
            source_path=source_path,
            target=target,
            metrics={'duration_seconds': duration, 'chapter_count': chapter_count, 'media_kind': media_kind, 'episode_type': episode_type},
        ))
    return issues


def _looks_like_single_special_target(media_kind: str, episode_type: str, duration: float, chapter_count: int) -> bool:
    if chapter_count:
        return False
    if duration >= 60 * 60:
        return False
    return media_kind in {'ova', 'oad', 'sp', 'special'} or episode_type in {'ova', 'oad', 'sp', 'special'}


def _looks_like_long_format_sequence_episode(
    assignment: dict[str, Any],
    target: dict[str, Any],
    duration: float,
    chapter_count: int,
    rule_contexts: dict[str, dict[str, Any]],
) -> bool:
    if chapter_count:
        return False
    if duration >= 60 * 60:
        return False
    for context in _sequence_contexts_for_assignment(assignment, target, rule_contexts):
        numbers = sorted(set(context.get('numbers') or []))
        if len(numbers) < 3 or not context.get('contiguous_numbers'):
            continue
        if not context.get('has_long_format_term'):
            continue
        mapped_count = _int_or_none(context.get('mapped_count')) or 0
        long_no_chapter_count = _int_or_none(context.get('long_no_chapter_count')) or 0
        if long_no_chapter_count < max(2, int(mapped_count * 0.6)):
            continue
        episode_number = _assignment_episode_number(assignment)
        if episode_number in set(numbers):
            return True
    return False


def _looks_like_sequence_boundary_long_episode(
    assignment: dict[str, Any],
    target: dict[str, Any],
    duration: float,
    chapter_count: int,
    rule_contexts: dict[str, dict[str, Any]],
) -> bool:
    if chapter_count:
        return False
    if duration >= 60 * 60:
        return False
    for context in _sequence_contexts_for_assignment(assignment, target, rule_contexts):
        numbers = sorted(set(context.get('numbers') or []))
        if len(numbers) < 3:
            continue
        episode_number = _assignment_episode_number(assignment)
        if episode_number is not None and episode_number in {numbers[0], numbers[-1]}:
            return True
    return False


def _sequence_contexts_for_assignment(
    assignment: dict[str, Any],
    target: dict[str, Any],
    rule_contexts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    keys = [str(assignment.get('rule_name') or '')]
    subject_id = _int_or_none(target.get('bangumi_subject_id'))
    if subject_id:
        keys.append(f"subject:{subject_id}:{target.get('media_kind') or ''}:{target.get('episode_type') or ''}")
    contexts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        context = rule_contexts.get(key)
        if context:
            contexts.append(context)
    return contexts


def _looks_like_long_format_release(text: str) -> bool:
    text = text.casefold()
    long_format_terms = [
        'complete edition',
        'compilation',
        "director's cut",
        'directors cut',
        'extended',
        'omnibus',
        're-edit',
        'reedit',
        'recut',
        'remix',
        'uncut',
    ]
    return any(term in text for term in long_format_terms)


def _excluded_assignment_issues(
    *,
    source_path: str,
    duration: float | None,
    chapter_count: int,
    reason: str,
    targeted_evidence_paths: set[str],
) -> list[dict[str, Any]]:
    if duration is None or duration < LONG_EXCLUDED_SECONDS:
        return []
    if source_path in targeted_evidence_paths:
        return [_issue(
            'info',
            'long_excluded_after_targeted_lookup',
            'Long visible file was excluded after targeted evidence lookup for this exact source path.',
            source_path=source_path,
            metrics={'duration_seconds': duration, 'chapter_count': chapter_count},
            details={'reason': reason},
        )]
    if _looks_like_obvious_extra(source_path, reason):
        return [_issue(
            'info',
            'long_obvious_extra_excluded',
            'Long visible file was excluded, but its name/reason looks like obvious event, talk, promo, commentary, or drama extra material.',
            source_path=source_path,
            metrics={'duration_seconds': duration, 'chapter_count': chapter_count},
            details={'reason': reason},
        )]
    return [_issue(
        'review',
        'long_excluded_file',
        'Long visible file was excluded as supplemental; review whether it is actually an OVA/SP/movie/drama target.',
        source_path=source_path,
        metrics={'duration_seconds': duration, 'chapter_count': chapter_count},
        details={'reason': reason},
    )]


def _looks_like_obvious_extra(source_path: str, reason: str) -> bool:
    text = f'{source_path} {reason}'.casefold()
    if re.search(r'(?:^|[/\[\]\s_-])iv\d{1,3}(?:$|[/\[\]\s_-])', text):
        return True
    obvious_terms = [
        'after talk',
        'audio commentary',
        'cast',
        'commentary',
        'drama',
        'event',
        'greeting',
        'interview',
        'journey',
        'location',
        'live',
        'making',
        'memorial',
        'museum',
        'pre-release',
        'pv',
        'recitation',
        'recap',
        'redubbing',
        'stage',
        'special program',
        'summary',
        'talk',
        'tour',
        'travel',
        'travelogue',
        'tv-spot',
        'ロケ',
        '旅',
        '出張',
        '公开直前',
        '公开前',
        '特别节目',
        '特別番組',
        '特番',
        '軌跡',
        '轨迹',
    ]
    return any(term in text for term in obvious_terms)


def _targeted_evidence_paths(run_dir: Path) -> set[str]:
    trace_path = run_dir / 'tool_trace.jsonl'
    if not trace_path.exists():
        return set()
    paths: set[str] = set()
    for line in trace_path.read_text(encoding='utf-8', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get('tool') or '') != 'find_bangumi_targets_for_local_file':
            continue
        arguments = row.get('arguments') if isinstance(row.get('arguments'), dict) else {}
        source_path = _norm_path(arguments.get('source_path'))
        if source_path:
            paths.add(source_path)
    return paths


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    source_path: str = '',
    target: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {'severity': severity, 'code': code, 'message': message}
    if source_path:
        payload['source_path'] = source_path
    if target:
        payload['target'] = target
    if metrics:
        payload['metrics'] = metrics
    if details:
        payload['details'] = details
    return payload


def _print_text(report: dict[str, Any]) -> None:
    print(f"input: {report['input']}")
    print(
        f"runs={report['run_count']} issues={report['issue_count']} "
        f"review_runs={report['runs_with_review_count']} severities={report['severity_counts']}"
    )
    print(f"issue_counts={report['issue_counts']}")
    for run in report['runs']:
        issues = list(run.get('issues') or [])
        if not issues:
            continue
        print()
        print(f"- {Path(str(run.get('sample') or run.get('run_dir') or '')).name}: {run.get('status')} / {run.get('summary')}")
        print(f"  run_dir: {run.get('run_dir')}")
        print(f"  metrics: {run.get('metrics')}")
        for issue in issues:
            source = f" source={issue.get('source_path')}" if issue.get('source_path') else ''
            print(f"  [{issue.get('severity')}] {issue.get('code')}{source}: {issue.get('message')}")
            if issue.get('metrics'):
                print(f"    metrics: {issue.get('metrics')}")


def main() -> int:
    parser = argparse.ArgumentParser(description='Read-only semantic audit for accepted Pi organize recipe artifacts.')
    parser.add_argument('input', type=Path, help='Batch output dir/summary.json, a Pi run dir, or a parent directory of run dirs.')
    parser.add_argument('--json', action='store_true', help='Print machine-readable JSON.')
    parser.add_argument('--output', type=Path, default=None, help='Optional path to write the JSON report.')
    args = parser.parse_args()

    report = audit_path(args.input)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
