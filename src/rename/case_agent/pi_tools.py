from __future__ import annotations

import json
import re
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from .dossier import build_bounded_case_dossier
from .models import (
    BangumiItemCard,
    BangumiSubjectCard,
    CaseJudgeOutput,
    CaseVerifierResult,
    FailClosedReason,
    VerifierIssue,
)
from .recipe import (
    CompiledOrganizePlan,
    OrganizeRecipeDraft,
    compile_and_verify_organize_recipe,
    recipe_accounting,
)
from .workspace import CaseEvidenceWorkspace


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _counter(values: list[str]) -> dict[str, int]:
    return dict(Counter(value for value in values if value))


_CONTENT_SHAPE_TOKEN_RE = re.compile(
    r'(?i)\b('
    r'OVA|OAV|OAD|ONA|SP|SPECIAL|TVSP|MOVIES?|FILM|GEKIJOU?BAN|EIGA|RECAP|DIGEST|'
    r'SOUSHUUHEN|SOSHUUHEN|OMAKE|EXTRA|BONUS|PART|CD|DVD|DISC|DISK'
    r')\b'
)
_LOCATOR_TOKEN_RE = re.compile(
    r'(?i)'
    r'(S\d{1,2}E\d{1,3}(?:\.\d+)?)'
    r'|((?<![A-Za-z0-9])(?:SP|SPECIAL|OVA|OAV|OAD|ONA|MOVIE|PART|CD|DVD|DISC|DISK)(?=[\s._-]*\d)[\s._-]*[0-9A-Z]{1,4})'
    r'|((?<![A-Za-z0-9])(?:EP|E)(?=[\s._-]*\d)[\s._-]*\d{1,3}(?:\.\d+)?)'
    r'|((?<![A-Za-z0-9])#?\d{1,3}(?:\.\d+)?(?![A-Za-z0-9]))'
)
_TECH_NUMERIC_TOKENS = {
    '480', '540', '720', '1080', '1440', '1920', '2160', '264', '265', '266',
    '10bit', '8bit',
}
_REVIEW_LONG_EXCLUDED_SECONDS = 15 * 60
_REVIEW_OBVIOUS_EXTRA_RE = re.compile(r'(?i)(?:^|[/\[\]\s_-])iv\d{1,3}(?:$|[/\[\]\s_-])')
_REVIEW_SUPPLEMENTAL_DIR_RE = re.compile(r'(?i)^(?:SPs?|Specials?|Bonus|Extras?)$')
_REVIEW_BRACKETED_BARE_IV_RE = re.compile(r'(?i)\[\s*IV\s*\]')
_REVIEW_OBVIOUS_EXTRA_TERMS = [
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


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stem_for_path(path: str) -> str:
    basename = _norm_path(path).rsplit('/', 1)[-1]
    if '.' in basename:
        return basename.rsplit('.', 1)[0]
    return basename


def _compact_text(value: str, *, limit: int = 140) -> str:
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + '...'


def _common_suffix(values: list[str]) -> str:
    if not values:
        return ''
    reversed_values = [value[::-1] for value in values if value]
    if not reversed_values:
        return ''
    return _common_prefix(reversed_values)[::-1]


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ''
    prefix = values[0]
    for value in values[1:]:
        while prefix and not value.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            break
    return prefix


def _locator_tokens(stem_or_name: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for match in _LOCATOR_TOKEN_RE.finditer(str(stem_or_name or '')):
        raw = next((group for group in match.groups() if group), '')
        normalized = re.sub(r'\s+', '', raw.upper().replace('_', '').replace('-', '').replace('.', '.'))
        if normalized.casefold() in _TECH_NUMERIC_TOKENS:
            continue
        numeric_match = re.search(r'\d+(?:\.\d+)?', normalized)
        numeric = numeric_match.group(0) if numeric_match else ''
        tokens.append({
            'token': raw,
            'normalized': normalized,
            'number': numeric,
            'start': match.start(),
            'end': match.end(),
        })
    return tokens


def _prefix_before_first_locator(stem: str) -> str:
    tokens = _locator_tokens(stem)
    if not tokens:
        return _compact_text(stem)
    prefix = stem[: int(tokens[0]['start'] or 0)]
    return _compact_text(prefix.strip(' ._-'))


def _content_shape_counts(values: list[str]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for value in values:
        for match in _CONTENT_SHAPE_TOKEN_RE.finditer(str(value or '')):
            token = match.group(1).upper()
            if token == 'MOVIES':
                token = 'MOVIE'
            counter[token] += 1
    return dict(sorted(counter.items()))


def _numeric_run_summary(numbers: list[str]) -> dict[str, Any]:
    parsed_ints: list[int] = []
    decimal_or_other: list[str] = []
    for number in numbers:
        text = str(number or '').strip()
        if not text:
            continue
        if re.fullmatch(r'\d+', text):
            parsed_ints.append(int(text))
        else:
            decimal_or_other.append(text)
    unique_ints = sorted(set(parsed_ints))
    ranges: list[str] = []
    if unique_ints:
        start = prev = unique_ints[0]
        for value in unique_ints[1:]:
            if value == prev + 1:
                prev = value
                continue
            ranges.append(str(start) if start == prev else f'{start}-{prev}')
            start = prev = value
        ranges.append(str(start) if start == prev else f'{start}-{prev}')
    return {
        'count': len(numbers),
        'unique_count': len(set(numbers)),
        'integer_ranges': ranges,
        'other_values_sample': sorted(set(decimal_or_other))[:12],
    }


def _duration_summary(cards: list[Any]) -> dict[str, Any]:
    durations: list[int] = []
    available_count = 0
    for card in cards:
        facts = getattr(card, 'container_facts', {}) or {}
        if str(facts.get('probe_status') or '') == 'available':
            available_count += 1
        try:
            seconds = int(facts.get('duration_seconds') or 0)
        except Exception:
            seconds = 0
        if seconds > 0:
            durations.append(seconds)
    if not durations:
        return {'available_count': available_count, 'with_duration_count': 0}
    values = sorted(durations)
    return {
        'available_count': available_count,
        'with_duration_count': len(values),
        'min_seconds': values[0],
        'max_seconds': values[-1],
        'median_seconds': values[len(values) // 2],
    }


def _duration_seconds_for_card(card: Any) -> float | None:
    if card is None:
        return None
    container = getattr(card, 'container_facts', {}) or {}
    facts = getattr(card, 'fact_summary', {}) or {}
    if isinstance(container, dict):
        value = _float_or_none(container.get('duration_seconds'))
        if value is not None:
            return value
    if isinstance(facts, dict):
        value = _float_or_none(facts.get('duration_seconds'))
        if value is not None:
            return value
    return None


def _looks_like_obvious_extra_for_review(source_path: str, reason: str) -> bool:
    text = f'{source_path} {reason}'.casefold()
    if _REVIEW_OBVIOUS_EXTRA_RE.search(text):
        return True
    if _looks_like_supplemental_dir_bracketed_iv(source_path):
        return True
    return any(term.casefold() in text for term in _REVIEW_OBVIOUS_EXTRA_TERMS)


def _looks_like_supplemental_dir_bracketed_iv(source_path: str) -> bool:
    path = _norm_path(source_path)
    parts = [part for part in path.split('/') if part]
    if len(parts) < 2:
        return False
    if not any(_REVIEW_SUPPLEMENTAL_DIR_RE.fullmatch(part) for part in parts[:-1]):
        return False
    return bool(_REVIEW_BRACKETED_BARE_IV_RE.search(parts[-1]))


def _review_sequence_key_and_number(source_path: str) -> tuple[tuple[str, str, str], int] | None:
    path = _norm_path(source_path)
    if not path:
        return None
    parent, basename = path.rsplit('/', 1) if '/' in path else ('', path)
    stem = basename.rsplit('.', 1)[0] if '.' in basename else basename
    for token in _locator_tokens(stem):
        number_text = str(token.get('number') or '')
        if not number_text or '.' in number_text:
            continue
        try:
            number = int(number_text)
        except ValueError:
            continue
        start = int(token.get('start') or 0)
        end = int(token.get('end') or 0)
        prefix = re.sub(r'\s+', ' ', stem[:start]).strip().casefold()
        suffix = re.sub(r'\s+', ' ', stem[end:]).strip().casefold()
        return (parent.casefold(), prefix, suffix), number
    return None


def _prefix_group_summary(cards: list[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    buckets: dict[str, list[Any]] = {}
    for card in cards:
        path = _norm_path(str(getattr(card, 'path', '') or ''))
        stem = _stem_for_path(path)
        prefix = _prefix_before_first_locator(stem)
        buckets.setdefault(prefix or '(no locator prefix)', []).append(card)
    rows: list[dict[str, Any]] = []
    for prefix, members in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))[:limit]:
        stems = [_stem_for_path(str(getattr(card, 'path', '') or '')) for card in members]
        first_tokens = [_locator_tokens(stem)[0] for stem in stems if _locator_tokens(stem)]
        rows.append({
            'prefix': prefix,
            'file_count': len(members),
            'first_locator_tokens_sample': [token['normalized'] for token in first_tokens[:20]],
            'first_locator_numbers': _numeric_run_summary([str(token.get('number') or '') for token in first_tokens if token.get('number')]),
            'source_paths_sample': [_norm_path(str(getattr(card, 'path', '') or '')) for card in members[:5]],
        })
    return rows


def _local_structure_summary(cards: list[Any]) -> dict[str, Any]:
    visible_cards = [
        card for card in cards
        if bool(getattr(card, 'is_main', False)) and _norm_path(str(getattr(card, 'path', '') or ''))
    ]
    folders: dict[str, list[Any]] = {}
    for card in visible_cards:
        path = _norm_path(str(getattr(card, 'path', '') or ''))
        parent = str(getattr(card, 'parent_display', '') or '')
        if not parent and '/' in path:
            parent = path.rsplit('/', 1)[0]
        folders.setdefault(parent or '.', []).append(card)

    folder_groups: list[dict[str, Any]] = []
    repeated_starts: dict[str, set[str]] = {}
    for folder, members in sorted(folders.items(), key=lambda item: (-len(item[1]), item[0]))[:40]:
        paths = [_norm_path(str(getattr(card, 'path', '') or '')) for card in members]
        basenames = [path.rsplit('/', 1)[-1] for path in paths]
        stems = [_stem_for_path(path) for path in paths]
        first_tokens = [_locator_tokens(stem)[0] for stem in stems if _locator_tokens(stem)]
        for token in first_tokens:
            repeated_starts.setdefault(str(token.get('normalized') or ''), set()).add(folder)
        folder_groups.append({
            'folder': folder,
            'file_count': len(members),
            'common_filename_prefix': _compact_text(_common_prefix(basenames)),
            'common_filename_suffix': _compact_text(_common_suffix(basenames)),
            'content_shape_token_counts': _content_shape_counts([folder, *basenames]),
            'first_locator_tokens_sample': [token['normalized'] for token in first_tokens[:24]],
            'first_locator_numbers': _numeric_run_summary([str(token.get('number') or '') for token in first_tokens if token.get('number')]),
            'duration_seconds': _duration_summary(members),
            'prefix_groups': _prefix_group_summary(members),
            'source_paths_sample': paths[:8],
        })

    repeated_numbering_starts = [
        {
            'first_locator': token,
            'folder_count': len(folders_for_token),
            'folders_sample': sorted(folders_for_token)[:10],
        }
        for token, folders_for_token in sorted(repeated_starts.items())
        if token and len(folders_for_token) > 1
    ]
    return {
        'visible_file_count': len(visible_cards),
        'folder_count': len(folders),
        'folder_groups': folder_groups,
        'repeated_numbering_starts': repeated_numbering_starts[:24],
        'summary_policy': 'Factual grouping aid only. It is not a semantic mapping decision; verify subject identity with Bangumi evidence.',
    }


@dataclass
class PiCaseToolState:
    workspace: CaseEvidenceWorkspace
    bangumi_client: Any
    run_dir: Path
    repo_root: Path
    source_path: str = ''
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    final_result: dict[str, Any] | None = None
    last_invalid_submission: dict[str, Any] | None = None
    submit_rejection_count: int = 0
    organize_recipe: OrganizeRecipeDraft | None = None
    compiled_plan: CompiledOrganizePlan | None = None
    recipe_verifier_result: CaseVerifierResult | None = None
    _request_index: int = 0
    _query_index: int = 0

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / 'artifacts').mkdir(parents=True, exist_ok=True)

    @property
    def case_id(self) -> str:
        return str(getattr(self.workspace.header, 'case_id', '') or 'local-bangumi-pi')

    def case_input(self, *, pi_command: str = '', timeout_seconds: int = 0) -> dict[str, Any]:
        artifacts_dir = self.run_dir / 'artifacts'
        return {
            'case_id': self.case_id,
            'case_agent_mode': 'pi_case_agent',
            'task_source_path': self.source_path,
            'local_structure_summary': _local_structure_summary(list(self.workspace.local_files)),
            'visible_source_paths': self._visible_main_paths(),
            'local_identity_policy': 'Only values from visible_source_paths or context.local_files[].source_path may be passed as a tool source_path. task_source_path is the original task/sample path, not a local file identity.',
            'pi_command': pi_command,
            'run_dir': str(self.run_dir),
            'runtime_policy': {
                'turn_cap_enabled': False,
                'turn_count_is_audit_only': True,
                'wall_clock_timeout_seconds': max(0, int(timeout_seconds or 0)),
                'suggested_finish_before_seconds': max(0, max(0, int(timeout_seconds or 0)) - 15),
                'finalization_buffer_seconds': 15 if int(timeout_seconds or 0) > 15 else 0,
                'bangumi_evidence_requests_allowed': True,
                'empty_visible_target_refs_means_search_first': True,
            },
            'scratch_paths': {
                'artifacts_dir': str(artifacts_dir),
                'organize_recipe': str(artifacts_dir / 'organize_recipe.json'),
                'notes': str(artifacts_dir / 'notes.md'),
                'helper_check': str(artifacts_dir / 'organize_recipe_helper_check.json'),
            },
            'case_goal': {
                'objective': 'Produce a Python-verifier accepted OrganizeRecipeDraft or fail closed.',
                'done_when': [
                    'organize_recipe scratch artifact is updated by validate or submit; notes.md is optional for complex investigations',
                    'local skill helper has checked the recipe shape',
                    'validate_organize_recipe returns no blocking issues',
                    'validate_organize_recipe returns no review_warnings, or you have resolved each warning with targeted evidence and validated again',
                    'submit_organize_recipe returns accepted=true',
                    'goal_complete is called only after accepted=true',
                ],
            },
            'context': self._case_context_payload(detail=True),
        }

    def handle_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.time()
        args = dict(arguments or {})
        trace_row = {
            'index': len(self.tool_trace) + 1,
            'tool': str(name or ''),
            'arguments': _json_safe(args),
            'started_at': started,
        }
        try:
            handler = getattr(self, f'tool_{name}', None)
            if handler is None:
                result = {'ok': False, 'error': f'unknown tool: {name}'}
            else:
                result = handler(**args)
        except Exception as exc:
            result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
        trace_row['elapsed_ms'] = int((time.time() - started) * 1000)
        trace_row['ok'] = bool(result.get('ok')) if isinstance(result, dict) else False
        trace_row['result_summary'] = self._compact_result_summary(result)
        self.tool_trace.append(trace_row)
        with (self.run_dir / 'tool_trace.jsonl').open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(_json_safe(trace_row), ensure_ascii=False, sort_keys=True))
            fh.write('\n')
        return _json_safe(result)

    def tool_get_case_context(self, detail: bool = False) -> dict[str, Any]:
        return {'ok': True, 'data': self._case_context_payload(detail=bool(detail))}

    def tool_search_bangumi_subjects(self, query: str, max_subjects: int = 5) -> dict[str, Any]:
        try:
            results = self.bangumi_client.search_subjects(str(query or ''), None)
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
        limit = max(1, int(max_subjects or 5))
        subjects = [self._subject_card_from_api(subject) for subject in list(results or [])[:limit]]
        self._upsert_subject_cards(subjects)
        return {
            'ok': True,
            'subjects': [self._subject_payload(card) for card in subjects],
            'usage_hint': 'If the local case is a same-franchise bundle with several named movies, OVAs, specials, or side stories, use the returned anime subject IDs as anchors for expand_related_graph before doing per-title broad searches. Direct title search is the fallback for visible titles missing from that relation graph.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_lookup_bangumi_subject(self, subject_ids: list[int]) -> dict[str, Any]:
        subjects: list[BangumiSubjectCard] = []
        for value in subject_ids or []:
            subject_id = int(value or 0)
            if subject_id <= 0:
                continue
            try:
                subject = self.bangumi_client.get_subject(subject_id)
            except Exception as exc:
                return {'ok': False, 'error': str(exc), 'subject_id': subject_id}
            if subject is not None:
                subjects.append(self._subject_card_from_api(subject))
        self._upsert_subject_cards(subjects)
        return {'ok': True, 'subjects': [self._subject_payload(card) for card in subjects], 'context': self._case_context_payload(detail=False)}

    def tool_expand_related_subjects(
        self,
        subject_id: int,
        relation_kinds: list[str] | None = None,
        subject_types: list[str] | None = None,
        max_subjects: int = 8,
    ) -> dict[str, Any]:
        subject_id = int(subject_id or 0)
        if subject_id <= 0:
            return {'ok': False, 'error': 'subject_id is required'}
        wanted = {str(kind).casefold() for kind in (relation_kinds or []) if str(kind)}
        wanted_types = {str(kind).strip().casefold() for kind in (subject_types or []) if str(kind).strip()}
        try:
            relations = self.bangumi_client.get_related_subjects(subject_id) or []
        except Exception as exc:
            return {'ok': False, 'error': str(exc), 'subject_id': subject_id}
        subjects: list[BangumiSubjectCard] = []
        rows: list[dict[str, Any]] = []
        compact_rows: list[dict[str, Any]] = []
        for relation in relations:
            relation_kind = str(getattr(relation, 'relation', '') or '')
            if wanted and relation_kind.casefold() not in wanted:
                continue
            related_id = int(getattr(relation, 'id', 0) or 0)
            if related_id <= 0:
                continue
            try:
                detail = self.bangumi_client.get_subject(related_id)
            except Exception:
                detail = None
            card = self._subject_card_from_api(detail or relation, relation_to_main=relation_kind)
            if wanted_types and card.subject_type.casefold() not in wanted_types:
                continue
            subjects.append(card)
            subject_payload = self._subject_payload(card, include_summary=False)
            rows.append({'relation': relation_kind, 'subject': subject_payload})
            compact_rows.append({
                'relation': relation_kind,
                'subject_id': subject_payload['subject_id'],
                'subject_type': subject_payload['subject_type'],
                'title': subject_payload['title'],
                'name': subject_payload['name'],
                'name_cn': subject_payload['name_cn'],
                'platform': subject_payload['platform'],
                'eps': subject_payload['eps'],
                'total_episodes': subject_payload['total_episodes'],
                'date': subject_payload['date'],
            })
            if len(subjects) >= max(1, int(max_subjects or 8)):
                break
        self._upsert_subject_cards(subjects)
        return {
            'ok': True,
            'relation_subjects': compact_rows,
            'relations': rows,
            'usage_hint': 'Use relation_subjects as the series map. Fetch get_episode_list for related anime subjects that match visible local groups; compare local numbering with Bangumi sort/ep, and split local ranges when the first subject does not expose matching rows.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_expand_related_graph(
        self,
        subject_id: int = 0,
        subject_ids: list[int] | None = None,
        relation_kinds: list[str] | None = None,
        subject_types: list[str] | None = None,
        max_depth: int = 3,
        max_subjects: int = 32,
    ) -> dict[str, Any]:
        seeds = [int(value or 0) for value in (subject_ids or []) if int(value or 0) > 0]
        if not seeds and int(subject_id or 0) > 0:
            seeds = [int(subject_id or 0)]
        seeds = list(dict.fromkeys(seeds))
        if not seeds:
            return {'ok': False, 'error': 'subject_id or subject_ids is required'}

        wanted_relations = {str(kind).casefold() for kind in (relation_kinds or []) if str(kind)}
        wanted_types = {str(kind).strip().casefold() for kind in (subject_types or []) if str(kind).strip()}
        depth_limit = max(1, min(4, int(max_depth or 3)))
        subject_limit = max(1, min(80, int(max_subjects or 32)))

        queue: list[tuple[int, int]] = [(seed, 0) for seed in seeds]
        visited_for_traversal: set[int] = set()
        relation_checked: set[int] = set()
        seen_subjects: set[int] = set(seeds)
        subject_cards: dict[int, BangumiSubjectCard] = {}
        node_depth: dict[int, int] = {seed: 0 for seed in seeds}
        edges: list[dict[str, Any]] = []
        skipped: list[str] = []
        relation_fetch_failed_subject_ids: list[int] = []

        for seed in seeds:
            try:
                detail = self.bangumi_client.get_subject(seed)
            except Exception:
                detail = None
            if detail is not None:
                subject_cards[seed] = self._subject_card_from_api(detail)

        while queue and len(seen_subjects - set(seeds)) < subject_limit:
            current_id, depth = queue.pop(0)
            if current_id in visited_for_traversal:
                continue
            visited_for_traversal.add(current_id)
            if depth >= depth_limit:
                continue
            try:
                relations = self.bangumi_client.get_related_subjects(current_id) or []
            except Exception as exc:
                skipped.append(f'{current_id}: relation fetch failed: {exc}')
                relation_fetch_failed_subject_ids.append(current_id)
                continue
            relation_checked.add(current_id)
            for relation in relations:
                relation_kind = str(getattr(relation, 'relation', '') or '')
                if wanted_relations and relation_kind.casefold() not in wanted_relations:
                    continue
                related_id = int(getattr(relation, 'id', 0) or 0)
                if related_id <= 0:
                    continue
                try:
                    detail = self.bangumi_client.get_subject(related_id)
                except Exception:
                    detail = None
                card = self._subject_card_from_api(detail or relation, relation_to_main=relation_kind)
                if wanted_types and card.subject_type.casefold() not in wanted_types:
                    skipped.append(f'{current_id}->{related_id}: skipped subject_type={card.subject_type or "unknown"}')
                    continue
                edge = {
                    'from_subject_id': current_id,
                    'to_subject_id': related_id,
                    'relation': relation_kind,
                    'depth': depth + 1,
                }
                if edge not in edges:
                    edges.append(edge)
                if related_id not in seen_subjects:
                    if len(seen_subjects - set(seeds)) >= subject_limit:
                        skipped.append(f'{current_id}->{related_id}: skipped over max_subjects')
                        continue
                    seen_subjects.add(related_id)
                    node_depth[related_id] = depth + 1
                    subject_cards[related_id] = card
                    queue.append((related_id, depth + 1))
                elif related_id not in subject_cards:
                    subject_cards[related_id] = card

        ordered_cards = [
            subject_cards[subject_id]
            for subject_id in sorted(subject_cards, key=lambda sid: (node_depth.get(sid, 999), sid))
        ]
        self._upsert_subject_cards(ordered_cards)

        nodes = []
        for card in ordered_cards:
            payload = self._subject_payload(card, include_summary=False)
            payload['depth'] = node_depth.get(int(card.subject_id or 0), 0)
            payload['is_seed'] = int(card.subject_id or 0) in set(seeds)
            nodes.append(payload)

        related_rows = [node for node in nodes if not node.get('is_seed')]
        ordered_seen_ids = sorted(seen_subjects, key=lambda sid: (node_depth.get(sid, 999), sid))
        ordered_checked_ids = sorted(relation_checked, key=lambda sid: (node_depth.get(sid, 999), sid))
        next_expand_subject_ids = [
            subject_id
            for subject_id in ordered_seen_ids
            if subject_id not in relation_checked and subject_id not in relation_fetch_failed_subject_ids
        ]
        hit_subject_limit = bool(queue) or len(seen_subjects - set(seeds)) >= subject_limit
        if relation_fetch_failed_subject_ids:
            stop_reason = 'relation_fetch_failed'
        elif hit_subject_limit:
            stop_reason = 'subject_limit_reached'
        elif next_expand_subject_ids:
            stop_reason = 'depth_limit_reached'
        else:
            stop_reason = 'frontier_exhausted'
        return {
            'ok': True,
            'seed_subject_ids': seeds,
            'max_depth': depth_limit,
            'subject_count': len(nodes),
            'edge_count': len(edges),
            'traversal_status': {
                'frontier_exhausted': stop_reason == 'frontier_exhausted',
                'stop_reason': stop_reason,
                'seen_subject_ids': ordered_seen_ids,
                'new_related_subject_ids': [subject_id for subject_id in ordered_seen_ids if subject_id not in set(seeds)],
                'relation_checked_subject_ids': ordered_checked_ids,
                'next_subject_ids_to_expand': next_expand_subject_ids[:20],
                'relation_fetch_failed_subject_ids': relation_fetch_failed_subject_ids[:20],
            },
            'relation_subjects': related_rows,
            'subjects': nodes,
            'edges': edges,
            'skipped': skipped[:20],
            'usage_hint': 'Use this recursive relation graph as evidence only. If traversal_status.next_subject_ids_to_expand is non-empty and a named local group is unresolved, expand again from those subject IDs before treating it as supplemental.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_get_episode_list(self, subject_id: int, episode_scope: str = 'all_if_small', max_episode_cards: int = 240) -> dict[str, Any]:
        subject_id = int(subject_id or 0)
        if subject_id <= 0:
            return {'ok': False, 'error': 'subject_id is required'}
        try:
            episodes = self.bangumi_client.get_episodes(subject_id) or []
        except Exception as exc:
            return {'ok': False, 'error': str(exc), 'subject_id': subject_id}
        selected = self._select_episodes(list(episodes), str(episode_scope or 'all_if_small'))
        item_cards = sorted(
            [self._episode_card_from_api(subject_id, episode) for episode in selected],
            key=_episode_card_order_key,
        )[: max(1, int(max_episode_cards or 240))]
        self._ensure_subject_known(subject_id)
        self._upsert_item_cards(item_cards)
        return {
            'ok': True,
            'subject_id': subject_id,
            'episodes': [self._episode_payload(card) for card in item_cards],
            'context': self._case_context_payload(detail=False),
        }

    def tool_get_target_detail(self, episode_ids: list[int] | None = None, subject_id: int = 0, sort: int = 0) -> dict[str, Any]:
        episode_ids = [int(value or 0) for value in (episode_ids or []) if int(value or 0) > 0]
        subject_id = int(subject_id or 0)
        if episode_ids:
            existing = [card for card in self.workspace.bangumi_items if int(getattr(card, 'episode_id', 0) or 0) in set(episode_ids)]
            if existing:
                return {'ok': True, 'episodes': [self._episode_payload(card) for card in existing]}
        if subject_id <= 0:
            return {'ok': False, 'error': 'subject_id is required when episode_ids are not already visible'}
        result = self.tool_get_episode_list(subject_id=subject_id, episode_scope='all_if_small')
        if not result.get('ok'):
            return result
        episodes = [
            item for item in result.get('episodes', [])
            if (not episode_ids or int(item.get('episode_id') or 0) in episode_ids)
            and (not sort or int(item.get('sort') or 0) == int(sort))
        ]
        return {'ok': True, 'episodes': episodes}

    def tool_get_local_file_detail(self, paths: list[str]) -> dict[str, Any]:
        wanted = {
            canonical_path
            for path in paths or []
            for _card, canonical_path, _original_path in [self._resolve_local_file_path(path)]
            if canonical_path
        }
        files = [
            self._local_file_payload(card, detail=True)
            for card in self.workspace.local_files
            if not wanted or _norm_path(card.path) in wanted
        ]
        return {'ok': True, 'files': files}

    def tool_find_bangumi_targets_for_local_file(
        self,
        source_path: str,
        title_query: str = '',
        kind_hint: str = '',
        max_subjects: int = 5,
        max_episode_cards: int = 24,
    ) -> dict[str, Any]:
        """Compact fact helper for simple path-to-Bangumi investigations.

        This deliberately returns search and episode facts only. Pi still has to
        choose the semantic target, write a recipe, and pass the verifier gate.
        """
        normalized_path = _norm_path(source_path)
        local_card, normalized_path, original_path = self._resolve_local_file_path(normalized_path)
        if local_card is None:
            visible_paths = self._visible_main_paths()
            basename_matches = [
                path for path in visible_paths
                if _norm_path(path).rsplit('/', 1)[-1] == normalized_path.rsplit('/', 1)[-1]
            ]
            repair_hint = 'Retry with an exact value from visible_source_paths; do not use task_source_path or a sample JSON path as a local file identity.'
            if len(basename_matches) > 1:
                repair_hint = f'This basename is ambiguous; use one exact visible_source_paths value. Matching visible paths: {basename_matches[:8]}'
            return {
                'ok': False,
                'error': f'source_path is not in the visible file universe: {normalized_path}',
                'visible_source_paths': visible_paths,
                'basename_match_count': len(basename_matches),
                'repair_hints': [repair_hint],
            }

        query = str(title_query or '').strip() or _query_from_source_path(normalized_path)
        queries = _dedupe_nonempty([
            query,
            _query_without_terminal_kind(query, kind_hint),
        ])[:2]
        subject_limit = max(1, int(max_subjects or 5))
        episode_limit = max(1, int(max_episode_cards or 24))

        subjects_by_id: dict[int, BangumiSubjectCard] = {}
        search_rows: list[dict[str, Any]] = []
        for search_query in queries:
            try:
                results = self.bangumi_client.search_subjects(search_query, None)
            except Exception as exc:
                return {'ok': False, 'error': str(exc), 'query': search_query}
            cards = [self._subject_card_from_api(subject) for subject in list(results or [])[:subject_limit]]
            self._upsert_subject_cards(cards)
            search_rows.append({'query': search_query, 'subject_ids': [int(card.subject_id or 0) for card in cards if int(card.subject_id or 0) > 0]})
            for card in cards:
                if int(card.subject_id or 0) > 0:
                    subjects_by_id.setdefault(int(card.subject_id or 0), card)
            if len(subjects_by_id) >= subject_limit:
                break

        subject_episode_groups: list[dict[str, Any]] = []
        for subject in list(subjects_by_id.values())[:subject_limit]:
            subject_id = int(subject.subject_id or 0)
            if subject_id <= 0:
                continue
            try:
                episodes = self.bangumi_client.get_episodes(subject_id) or []
            except Exception as exc:
                subject_episode_groups.append({
                    'subject': self._subject_payload(subject),
                    'episodes': [],
                    'episode_list_error': str(exc),
                })
                continue
            episode_values = list(episodes or [])
            item_cards = sorted(
                [self._episode_card_from_api(subject_id, episode) for episode in episode_values],
                key=_episode_card_order_key,
            )[:episode_limit]
            self._ensure_subject_known(subject_id)
            self._upsert_item_cards(item_cards)
            subject_episode_groups.append({
                'subject': self._subject_payload(subject),
                'episodes': [self._episode_payload(episode) for episode in item_cards],
                'episode_count_available': len(episode_values),
                'episode_count_returned': len(item_cards),
                'episode_rows_limited': len(episode_values) > len(item_cards),
            })
        return {
            'ok': True,
            'source_path': normalized_path,
            'source_path_canonicalized_from': original_path,
            'local_file': self._local_file_payload(local_card, detail=True),
            'title_query': query,
            'queries_used': search_rows,
            'subject_episode_groups': subject_episode_groups,
            'episode_order': 'sort, then ep, then episode_id',
            'usage_hint': 'Use these as facts only. If a plausible subject is visible or episode_rows_limited is true, do not keep broad-searching because episode rows look truncated; call get_episode_list/get_target_window or draft validate_organize_recipe_params so Python can hydrate declared subject evidence.',
            'context': self._case_context_payload(detail=False),
        }

    def tool_get_target_window(self, subject_id: int, sort_start: int = 0, sort_end: int = 0) -> dict[str, Any]:
        subject_id = int(subject_id or 0)
        if subject_id <= 0:
            return {'ok': False, 'error': 'subject_id is required'}
        if not any(self._subject_id_for_item(card) == subject_id for card in self.workspace.bangumi_items):
            result = self.tool_get_episode_list(subject_id=subject_id, episode_scope='all_if_small')
            if not result.get('ok'):
                return result
        episodes = [
            self._episode_payload(card)
            for card in sorted(self.workspace.bangumi_items, key=_episode_card_order_key)
            if self._subject_id_for_item(card) == subject_id
            and (not sort_start or int(getattr(card, 'sort', 0) or 0) >= int(sort_start))
            and (not sort_end or int(getattr(card, 'sort', 0) or 0) <= int(sort_end))
        ]
        return {'ok': True, 'subject_id': subject_id, 'episodes': episodes}

    def tool_validate_organize_recipe(self, organize_recipe: dict[str, Any] | None = None) -> dict[str, Any]:
        recipe, error = self._parse_recipe_payload(organize_recipe)
        if error:
            return {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
        assert recipe is not None
        self._hydrate_recipe_target_evidence(recipe)
        plan, verifier_result = compile_and_verify_organize_recipe(self.workspace, recipe)
        self.organize_recipe = recipe
        self.compiled_plan = plan
        self.recipe_verifier_result = verifier_result
        repair_hints = self._recipe_repair_hints(verifier_result)
        review_warnings = self._recipe_review_warnings(plan)
        all_hints = _dedupe_nonempty([*repair_hints, *_review_warning_hints(review_warnings)])
        accepted = bool(verifier_result.passed and not review_warnings)
        status = 'accepted' if accepted else ('review' if verifier_result.passed else 'invalid')
        summary = (
            verifier_result.summary
            if accepted or not verifier_result.passed
            else f'accepted mechanically, but {len(review_warnings)} review warning(s) need targeted evidence'
        )
        self._write_recipe_artifacts(recipe, plan, verifier_result, repair_hints=repair_hints, review_warnings=review_warnings)
        return {
            'ok': True,
            'accepted': accepted,
            'status': status,
            'summary': summary,
            'review_warnings': review_warnings,
            'repair_hints': all_hints,
            'accounting': recipe_accounting(plan),
            'verifier_result': verifier_result.model_dump(mode='json'),
            'compiled_plan': plan.model_dump(mode='json'),
        }

    def tool_validate_organize_recipe_params(self, recipe_params: dict[str, Any] | None = None) -> dict[str, Any]:
        recipe, error = self._parse_recipe_params_payload(recipe_params)
        if error:
            return {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
        assert recipe is not None
        result = self.tool_validate_organize_recipe(organize_recipe=recipe.model_dump(mode='json'))
        result['organize_recipe'] = recipe.model_dump(mode='json')
        result['params_compiled'] = True
        return result

    def tool_submit_organize_recipe(self, organize_recipe: dict[str, Any] | None = None, summary: str = '') -> dict[str, Any]:
        recipe, error = self._parse_recipe_payload(organize_recipe)
        if error:
            return {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
        assert recipe is not None
        self._hydrate_recipe_target_evidence(recipe)
        plan, verifier_result = compile_and_verify_organize_recipe(self.workspace, recipe)
        self.organize_recipe = recipe
        self.compiled_plan = plan
        self.recipe_verifier_result = verifier_result
        repair_hints = self._recipe_repair_hints(verifier_result)
        review_warnings = self._recipe_review_warnings(plan)
        self._write_recipe_artifacts(recipe, plan, verifier_result, repair_hints=repair_hints, review_warnings=review_warnings)
        if not verifier_result.passed or review_warnings:
            self.submit_rejection_count += 1
            all_hints = _dedupe_nonempty([*repair_hints, *_review_warning_hints(review_warnings)])
            self.last_invalid_submission = {
                'organize_recipe': recipe.model_dump(mode='json'),
                'compiled_plan': plan.model_dump(mode='json'),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'accounting': recipe_accounting(plan),
                'repair_hints': all_hints,
                'review_warnings': review_warnings,
            }
            summary_text = (
                'Review warnings need targeted evidence; revise and submit again.'
                if verifier_result.passed
                else 'Verifier rejected the recipe; revise and submit again.'
            )
            return {
                'ok': True,
                'accepted': False,
                'status': 'review' if verifier_result.passed else 'invalid',
                'summary': summary_text,
                'review_warnings': review_warnings,
                'repair_hints': all_hints,
                'accounting': recipe_accounting(plan),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'compiled_plan': plan.model_dump(mode='json'),
            }

        final_output = CaseJudgeOutput(
            action='submit_verdict',
            summary=str(summary or 'Pi submitted a verifier-accepted organize recipe.'),
        )
        self.final_result = {
            'ok': True,
            'status': 'accepted',
            'summary': final_output.summary,
            'final_action': 'submit_verdict',
            'final_output': final_output.model_dump(mode='json'),
            'organize_recipe': recipe.model_dump(mode='json'),
            'compiled_plan': plan.model_dump(mode='json'),
            'final_verifier_result': verifier_result.model_dump(mode='json'),
            'accounting': recipe_accounting(plan),
            'expanded_assignment_count': len(plan.assignments),
            'repair_hints': [],
            'review_warnings': review_warnings,
        }
        self._write_final_result()
        return {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': final_output.summary,
            'review_warnings': review_warnings,
            'repair_hints': [],
            'accounting': recipe_accounting(plan),
            'verifier_result': verifier_result.model_dump(mode='json'),
            'compiled_plan': plan.model_dump(mode='json'),
            'expanded_assignment_count': len(plan.assignments),
        }

    def tool_submit_organize_recipe_params(self, recipe_params: dict[str, Any] | None = None, summary: str = '') -> dict[str, Any]:
        recipe, error = self._parse_recipe_params_payload(recipe_params)
        if error:
            return {'ok': False, 'accepted': False, 'error': error, 'repair_hints': _parse_error_repair_hints(error, self._visible_main_paths())}
        assert recipe is not None
        result = self.tool_submit_organize_recipe(organize_recipe=recipe.model_dump(mode='json'), summary=summary)
        result['organize_recipe'] = recipe.model_dump(mode='json')
        result['params_compiled'] = True
        return result

    def auto_finalize_accepted_validation(self) -> dict[str, Any]:
        """Finalize a recipe Pi already validated but did not submit.

        Some non-interactive Pi runs can stop after an accepted
        validate_organize_recipe call. This recovery path still requires the
        same deterministic helper and submit_organize_recipe verifier gate; it
        only avoids spending another model turn to repeat the accepted recipe.
        """
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        if self.organize_recipe is None:
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'no organize_recipe has been validated'}
        if self.recipe_verifier_result is None or not self.recipe_verifier_result.passed:
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'latest recipe verifier result is not accepted'}

        helper_check = self._run_recipe_helper_check()
        if not helper_check.get('ok'):
            return {
                'ok': False,
                'accepted': False,
                'skipped': False,
                'reason': 'helper check rejected the validated recipe',
                'helper_check': helper_check,
                'repair_hints': _helper_check_repair_hints(helper_check),
            }

        result = self.handle_tool(
            'submit_organize_recipe',
            {
                'organize_recipe': self.organize_recipe.model_dump(mode='json'),
                'summary': 'Runner finalized a Pi-validated organize recipe after validate_organize_recipe returned accepted=true.',
            },
        )
        if result.get('accepted') and self.final_result:
            self.final_result['auto_finalized_from_validated_recipe'] = True
            self.final_result['helper_check'] = helper_check
            self._write_final_result()
            result['auto_finalized_from_validated_recipe'] = True
            result['helper_check'] = helper_check
        return result

    def auto_fail_closed_no_final_result(self, *, reason: str = '') -> dict[str, Any]:
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        if self.recipe_verifier_result is not None and self.recipe_verifier_result.passed:
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'latest recipe verifier result is accepted'}
        tool_summary = self.tool_summary()
        return self.handle_tool(
            'fail_closed',
            {
                'reason': str(reason or f'Pi ended without submit_organize_recipe or fail_closed after investigation. Tool sequence: {tool_summary["tool_sequence"][:24]}'),
                'reason_kind': 'budget_exhausted',
                'related_refs': self._visible_main_paths()[:12],
                'allow_runner_budget_exhausted': True,
            },
        )

    def _parse_recipe_payload(self, organize_recipe: dict[str, Any] | None) -> tuple[OrganizeRecipeDraft | None, str]:
        if isinstance(organize_recipe, str):
            try:
                organize_recipe = json.loads(organize_recipe)
            except (json.JSONDecodeError, TypeError) as exc:
                return None, f'invalid OrganizeRecipeDraft payload: not valid JSON string: {exc}'
        payload = dict(organize_recipe or {})
        if 'organize_recipe' in payload and isinstance(payload.get('organize_recipe'), dict):
            payload = dict(payload['organize_recipe'])
        try:
            return OrganizeRecipeDraft.model_validate(payload), ''
        except Exception as exc:
            if _looks_like_recipe_params_payload(payload):
                recipe, params_error = self._parse_recipe_params_payload(payload)
                if recipe is not None:
                    return recipe, ''
                return None, f'invalid OrganizeRecipeDraft payload: {exc}; also failed as recipe_params: {params_error}'
            return None, f'invalid OrganizeRecipeDraft payload: {exc}'

    def _parse_recipe_params_payload(self, recipe_params: dict[str, Any] | None) -> tuple[OrganizeRecipeDraft | None, str]:
        if isinstance(recipe_params, str):
            try:
                recipe_params = json.loads(recipe_params)
            except (json.JSONDecodeError, TypeError) as exc:
                return None, f'invalid OrganizeRecipeParams payload: not valid JSON string: {exc}'
        payload = dict(recipe_params or {})
        if 'recipe_params' in payload and isinstance(payload.get('recipe_params'), dict):
            payload = dict(payload['recipe_params'])
        try:
            recipe_payload = self._recipe_payload_from_params(payload)
            return OrganizeRecipeDraft.model_validate(recipe_payload), ''
        except Exception as exc:
            return None, f'invalid OrganizeRecipeParams payload: {exc}'

    def _recipe_payload_from_params(self, payload: dict[str, Any]) -> dict[str, Any]:
        rules = payload.get('rules')
        if not isinstance(rules, list) or not rules:
            raise ValueError('recipe_params.rules must be a non-empty array')
        return {
            'version': int(payload.get('version') or 1),
            'summary': _string_or_default(payload.get('summary'), 'Pi generated recipe parameters.'),
            'rules': [self._recipe_rule_payload_from_params(rule, index) for index, rule in enumerate(rules, start=1)],
        }

    def _recipe_rule_payload_from_params(self, rule: Any, index: int) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise ValueError(f'rules[{index - 1}] must be an object')
        select = rule.get('select') if isinstance(rule.get('select'), dict) else {}
        target = rule.get('target') if isinstance(rule.get('target'), dict) else {}
        episode = rule.get('episode') if isinstance(rule.get('episode'), dict) else {}
        disposition = _string_or_default(rule.get('disposition'), '')
        _reject_unsupported_disposition_flags(rule, index=index, disposition=disposition)
        source_pattern = _source_pattern_from_params(rule, select)
        exact_paths = _coerce_string_list(_first_present(rule, select, keys=('exact_paths', 'paths', 'source_paths', 'source_path', 'path')))
        if not exact_paths:
            literal_exact_paths = self._literal_exact_paths_from_source_pattern(source_pattern)
            if literal_exact_paths:
                exact_paths = literal_exact_paths
                source_pattern = ''
        subject_id = int(_first_present(rule, target, keys=('bangumi_subject_id', 'subject_id', 'target_subject_id')) or 0)
        episode_id = int(_first_present(rule, target, keys=('episode_id', 'target_episode_id')) or 0)
        episode_type = _episode_type_from_params(rule, target)
        if episode_id > 0:
            episode_type = self._recipe_episode_type_for_episode_id(episode_id, subject_id=subject_id) or episode_type
        filename_regex = (
            _source_pattern_to_regex(source_pattern)
            if source_pattern
            else _string_or_default(_first_present(rule, select, keys=('filename_regex', 'regex')), '')
        )
        episode_range = _episode_range_from_params(rule, episode)
        episode_offset = _episode_offset_from_params(rule, episode)
        episode_number_field = _episode_number_field_from_params(rule, episode)
        if source_pattern and not exact_paths and disposition in {'', 'map_to_bangumi'} and not episode_id:
            episode_range, episode_offset = self._normalize_shifted_sequence_params(
                filename_regex=filename_regex,
                subject_id=subject_id,
                episode_type=episode_type,
                episode_range=episode_range,
                episode_offset=episode_offset,
                episode_number_field=episode_number_field,
            )
        return {
            'name': _string_or_default(rule.get('name'), f'rule_{index}'),
            'source_unit': _source_unit_from_params(rule, index=index),
            'select': {
                'path_glob': _string_or_default(_first_present(rule, select, keys=('path_glob', 'glob')), '**/*.mkv'),
                'filename_regex': filename_regex,
                'exact_paths': [self._canonicalize_exact_path(path) for path in exact_paths],
                'exclude_regex': _string_or_default(_first_present(rule, select, keys=('exclude_regex', 'exclude')), ''),
            },
            'target': {
                'bangumi_subject_id': subject_id,
                'media_kind': _media_kind_from_params(rule, target),
                'episode_id': episode_id,
                'episode_type': episode_type,
                'sort': _optional_int(_first_present(rule, target, keys=('sort', 'episode_sort'))),
                'ep': _optional_int(_first_present(rule, target, keys=('ep', 'episode_ep'))),
            },
            'episode': {
                'capture': _string_or_default(_first_present(rule, episode, keys=('episode_capture', 'capture')), 'ep'),
                'offset': episode_offset,
                'range': episode_range,
                'number_field': episode_number_field,
            },
            'disposition': disposition or 'map_to_bangumi',
            'reason': _string_or_default(rule.get('reason'), ''),
        }

    def _normalize_shifted_sequence_params(
        self,
        *,
        filename_regex: str,
        subject_id: int,
        episode_type: str,
        episode_range: str,
        episode_offset: str,
        episode_number_field: str,
    ) -> tuple[str, str]:
        offset_delta = _simple_episode_offset_delta(episode_offset)
        if offset_delta is None or offset_delta == 0:
            return episode_range, episode_offset
        target_numbers = _episode_range_numbers(episode_range)
        if not target_numbers:
            return episode_range, episode_offset
        raw_numbers = self._matching_source_pattern_episode_numbers(filename_regex)
        if not raw_numbers or not _is_contiguous_numbers(raw_numbers):
            return episode_range, episode_offset
        if set(raw_numbers) & set(target_numbers):
            return episode_range, episode_offset
        inverted_target_numbers = [number - offset_delta for number in raw_numbers]
        if sorted(inverted_target_numbers) != sorted(target_numbers):
            return episode_range, episode_offset
        if not self._has_exposed_target_numbers(
            subject_id=subject_id,
            numbers=target_numbers,
            episode_type=episode_type,
            episode_number_field=episode_number_field,
        ):
            return episode_range, episode_offset
        return _numbers_to_episode_range(raw_numbers), _numeric_offset_to_expr(-offset_delta)

    def _matching_source_pattern_episode_numbers(self, filename_regex: str) -> list[int]:
        if not filename_regex:
            return []
        try:
            regex = re.compile(str(filename_regex or '').replace('(?<', '(?P<'), re.IGNORECASE)
        except re.error:
            return []
        numbers: list[int] = []
        for path in self._visible_main_paths():
            match = regex.search(path.rsplit('/', 1)[-1]) or regex.search(path)
            if match is None:
                continue
            raw_value = match.groupdict().get('ep') if 'ep' in match.groupdict() else (match.group(1) if match.groups() else None)
            try:
                numbers.append(int(str(raw_value).lstrip('0') or '0'))
            except (TypeError, ValueError):
                return []
        return sorted(set(numbers))

    def _has_exposed_target_numbers(
        self,
        *,
        subject_id: int,
        numbers: list[int],
        episode_type: str,
        episode_number_field: str,
    ) -> bool:
        if subject_id <= 0 or not numbers:
            return False
        wanted = set(numbers)
        visible: set[int] = set()
        for card in self.workspace.bangumi_items:
            if self._subject_id_for_item(card) != subject_id:
                continue
            if not _episode_type_matches_recipe(card, episode_type):
                continue
            value = getattr(card, 'ep', 0) if str(episode_number_field or 'sort') == 'ep' else getattr(card, 'sort', 0)
            try:
                number = int(value or 0)
            except (TypeError, ValueError):
                continue
            if number > 0:
                visible.add(number)
        return wanted.issubset(visible)

    def _recipe_episode_type_for_episode_id(self, episode_id: int, *, subject_id: int = 0) -> str:
        episode_id = int(episode_id or 0)
        subject_id = int(subject_id or 0)
        if episode_id <= 0:
            return ''
        for card in self.workspace.bangumi_items:
            if int(getattr(card, 'episode_id', 0) or 0) != episode_id:
                continue
            if subject_id > 0 and self._subject_id_for_item(card) != subject_id:
                continue
            return self._recipe_episode_type_for_item(card)
        return ''

    def _canonicalize_exact_path(self, source_path: str) -> str:
        _card, canonical_path, _original_path = self._resolve_local_file_path(source_path)
        return canonical_path or _norm_path(source_path)

    def _literal_exact_paths_from_source_pattern(self, source_pattern: str) -> list[str]:
        pattern = _norm_path(source_pattern)
        if (
            not pattern
            or _SOURCE_PATTERN_TOKEN_RE.search(pattern)
            or '*' in pattern
            or '?' in pattern
        ):
            return []
        _card, canonical_path, _original_path = self._resolve_local_file_path(pattern)
        if canonical_path and canonical_path in set(self._visible_main_paths()):
            return [canonical_path]
        return []

    def tool_fail_closed(
        self,
        reason: str,
        reason_kind: str = 'insufficient_evidence',
        related_refs: list[str] | None = None,
        allow_runner_budget_exhausted: bool = False,
    ) -> dict[str, Any]:
        if (
            not bool(allow_runner_budget_exhausted)
            and (
                str(reason_kind or '').strip().casefold() == 'budget_exhausted'
                or str(reason or '').strip().casefold() == 'budget_exhausted'
            )
        ):
            return {
                'ok': False,
                'accepted': False,
                'error': 'budget_exhausted is runner-only; validate a best-effort recipe or use a semantic fail_closed reason.',
                'repair_hints': [
                    'Call validate_organize_recipe_params with the best supportable params before fail_closed.',
                    'If evidence is insufficient after validation, use reason_kind insufficient_evidence, contradiction, or unknown with a concrete reason.',
                ],
            }
        allowed = {'budget_exhausted', 'contradiction', 'insufficient_evidence', 'unknown'}
        kind = str(reason_kind or 'insufficient_evidence')
        if kind not in allowed:
            kind = 'unknown'
        final_output = CaseJudgeOutput(
            action='fail_closed',
            fail_closed_reasons=[
                FailClosedReason(
                    ref='FC_PI_1',
                    reason_kind=kind,
                    description=str(reason or ''),
                    related_refs=[str(ref) for ref in (related_refs or []) if str(ref)],
                )
            ],
            summary=str(reason or 'Pi failed closed.'),
        )
        self.final_result = {
            'ok': True,
            'status': 'fail_closed',
            'summary': final_output.summary,
            'final_action': 'fail_closed',
            'final_output': final_output.model_dump(mode='json'),
            'final_verifier_result': CaseVerifierResult(passed=True, issues=[], summary='fail_closed').model_dump(mode='json'),
            'accounting': {},
            'expanded_assignment_count': 0,
        }
        self._write_final_result()
        return {'ok': True, 'accepted': True, 'status': 'fail_closed', 'summary': final_output.summary}

    def _case_context_payload(self, *, detail: bool = False) -> dict[str, Any]:
        dossier = self.workspace.to_dossier(round_context='pi_context')
        bounded = build_bounded_case_dossier(dossier)
        payload = {
            'case_id': self.case_id,
            'task_source_path': self.source_path,
            'counts': bounded.counts,
            'local_structure_summary': _local_structure_summary(list(self.workspace.local_files)),
            'local_files': [self._local_file_payload(card, detail=detail) for card in self.workspace.local_files if bool(getattr(card, 'is_main', False))],
            'bangumi_subjects': [self._subject_payload(card) for card in self.workspace.bangumi_subjects],
            'bangumi_episodes': [self._episode_payload(card) for card in self.workspace.bangumi_items[:240]],
            'recipe_contract': {
                'identity_policy': 'Use real source_path strings for local files and Bangumi subject_id/episode_id/type/sort/ep for targets.',
                'final_tools': ['validate_organize_recipe_params', 'submit_organize_recipe_params', 'validate_organize_recipe', 'submit_organize_recipe', 'fail_closed'],
            },
        }
        if detail:
            payload['last_invalid_submission'] = _json_safe(self.last_invalid_submission)
            payload['current_organize_recipe'] = self.organize_recipe.model_dump(mode='json') if self.organize_recipe is not None else None
            payload['current_compiled_plan'] = self.compiled_plan.model_dump(mode='json') if self.compiled_plan is not None else None
        return payload

    def _next_request_ref(self, prefix: str) -> str:
        self._request_index += 1
        return f'REQ_PI_{prefix}_{self._request_index}'

    def _compact_result_summary(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {'type': type(result).__name__}
        keys = ['ok', 'accepted', 'status', 'error', 'summary', 'returncode', 'expanded_assignment_count']
        summary = {key: result.get(key) for key in keys if key in result}
        if 'repair_hints' in result and isinstance(result['repair_hints'], list):
            summary['repair_hint_count'] = len(result['repair_hints'])
        if 'review_warnings' in result and isinstance(result['review_warnings'], list):
            summary['review_warning_count'] = len(result['review_warnings'])
        if 'batch' in result and isinstance(result['batch'], dict):
            summary['batch_status'] = result['batch'].get('status')
            summary['request_count'] = len(result['batch'].get('request_results') or [])
        if 'verifier_result' in result and isinstance(result['verifier_result'], dict):
            summary['verifier_passed'] = result['verifier_result'].get('passed')
            summary['verifier_issue_count'] = len(result['verifier_result'].get('issues') or [])
        return summary

    def _visible_main_paths(self) -> list[str]:
        return [
            _norm_path(str(getattr(card, 'path', '') or ''))
            for card in self.workspace.local_files
            if bool(getattr(card, 'is_main', False)) and _norm_path(str(getattr(card, 'path', '') or ''))
        ]

    def _local_card_by_path(self) -> dict[str, Any]:
        return {
            _norm_path(str(getattr(card, 'path', '') or '')): card
            for card in self.workspace.local_files
            if _norm_path(str(getattr(card, 'path', '') or ''))
        }

    def _targeted_evidence_paths(self) -> set[str]:
        paths: set[str] = set()
        for row in self.tool_trace:
            if str(row.get('tool') or '') != 'find_bangumi_targets_for_local_file':
                continue
            arguments = row.get('arguments') if isinstance(row.get('arguments'), dict) else {}
            source_path = _norm_path(str(arguments.get('source_path') or ''))
            if source_path:
                paths.add(source_path)
        return paths

    def _recipe_review_warnings(self, plan: CompiledOrganizePlan) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        local_by_path = self._local_card_by_path()
        targeted_paths = self._targeted_evidence_paths()
        sequence_targeted_paths = self._sequence_targeted_review_paths(plan, targeted_paths)
        for assignment in plan.assignments:
            if assignment.disposition != 'non_bangumi_or_supplemental':
                continue
            source_path = _norm_path(assignment.source_path)
            if not source_path or source_path in targeted_paths:
                continue
            if source_path in sequence_targeted_paths:
                continue
            local = local_by_path.get(source_path)
            duration = _duration_seconds_for_card(local)
            if duration is None or duration < _REVIEW_LONG_EXCLUDED_SECONDS:
                continue
            if _looks_like_obvious_extra_for_review(source_path, assignment.reason):
                continue
            warnings.append({
                'severity': 'review',
                'code': 'long_supplemental_without_targeted_evidence',
                'source_path': source_path,
                'message': 'A long visible file is covered as non_bangumi_or_supplemental before targeted evidence for that exact source_path was recorded.',
                'metrics': {'duration_seconds': duration},
                'repair_hint': f'For {source_path}, call find_bangumi_targets_for_local_file with this exact source_path, or map it if Bangumi evidence appears. If the targeted lookup still exposes no supportable anime target, validate the same supplemental rule again.',
            })
        return warnings

    def _sequence_targeted_review_paths(self, plan: CompiledOrganizePlan, targeted_paths: set[str]) -> set[str]:
        by_rule_and_sequence: dict[tuple[str, tuple[str, str, str]], list[tuple[str, int]]] = {}
        for assignment in plan.assignments:
            if assignment.disposition != 'non_bangumi_or_supplemental':
                continue
            source_path = _norm_path(assignment.source_path)
            sequence = _review_sequence_key_and_number(source_path)
            if source_path and sequence is not None:
                sequence_key, number = sequence
                by_rule_and_sequence.setdefault((str(assignment.rule_name or ''), sequence_key), []).append((source_path, number))

        covered: set[str] = set()
        for members in by_rule_and_sequence.values():
            if len(members) < 2:
                continue
            paths = [path for path, _number in members]
            if not any(path in targeted_paths for path in paths):
                continue
            numbers = [number for _path, number in members]
            if len(set(numbers)) != len(numbers) or not _is_contiguous_numbers(numbers):
                continue
            covered.update(paths)
        return covered

    def _resolve_local_file_path(self, source_path: str) -> tuple[Any | None, str, str]:
        normalized_path = _norm_path(source_path)
        if not normalized_path:
            return None, '', ''
        local_card = next(
            (card for card in self.workspace.local_files if _norm_path(str(getattr(card, 'path', '') or '')) == normalized_path),
            None,
        )
        if local_card is not None:
            return local_card, normalized_path, ''
        basename = normalized_path.rsplit('/', 1)[-1]
        matches = [path for path in self._visible_main_paths() if path.rsplit('/', 1)[-1] == basename]
        if len(matches) != 1:
            return None, normalized_path, ''
        canonical_path = matches[0]
        local_card = next(
            (card for card in self.workspace.local_files if _norm_path(str(getattr(card, 'path', '') or '')) == canonical_path),
            None,
        )
        if local_card is None:
            return None, normalized_path, ''
        return local_card, canonical_path, normalized_path

    def _recipe_repair_hints(self, verifier_result: CaseVerifierResult) -> list[str]:
        visible_paths = self._visible_main_paths()
        hints: list[str] = []
        for issue in verifier_result.issues:
            code = str(getattr(issue, 'issue_code', '') or '')
            if code == 'zero_match':
                hints.append(f'Rule matched no visible files. Use exact_paths from visible source_path values or fix selector. Visible source_path sample: {visible_paths[:8]}')
            elif code == 'unknown_exact_path':
                hints.append(f'exact_paths must use source_path exactly as shown in case context. Visible source_path sample: {visible_paths[:8]}')
            elif code == 'uncovered_path':
                related = list(getattr(issue, 'related_refs', []) or [])
                hints.append(f'Every visible main source_path must be covered exactly once. Add a rule for: {related or visible_paths[:8]}. If this came from a sequence source_pattern that matched only one file, replace changing release tokens such as CRC/hash/checksum/bracket IDs with a wildcard placeholder like {{hash}} or {{a}}, then validate again.')
            elif code == 'duplicate_coverage':
                hints.append('A source_path matched more than one rule. Narrow selectors or add exclude_regex so each file is covered once.')
            elif code == 'duplicate_target':
                related = list(getattr(issue, 'related_refs', []) or [])
                target = str(getattr(issue, 'ref', '') or 'the same target')
                hints.append(f'Duplicate Bangumi target {target} is used by these source paths: {related[:6]}. Repair only the affected rules, then validate again. For movie or one-file exact rules, replace the incorrect file with a distinct exposed subject_id/episode_id or fail closed if evidence is insufficient. Only for a sequence rule should you check whether fixed episode_id/sort/ep was accidentally reused instead of deriving targets from {{ep}}.')
            elif code == 'missing_target_episode':
                related = list(getattr(issue, 'related_refs', []) or [])
                hints.append(f'Target episode is not visible to the verifier for {related[:4] or str(getattr(issue, "ref", "") or "this rule")}. Fetch the smallest missing evidence with find_bangumi_targets_for_local_file, get_episode_list, get_target_window, or get_target_detail, then validate again. For sequence rules, compare the local file number with Bangumi episode sort and ep values: keep episode_number_field:\"sort\" with offset EP when sort matches local numbering; use episode_number_field:\"ep\" when local numbering matches Bangumi ep but sort continues across an earlier season/cour; use arithmetic offsets only when the target number field is correct but shifted. If the chosen subject lacks the needed rows, split to a related season/cour/part subject.')
            elif code == 'missing_subject_id':
                hints.append('Mapped rules need target.bangumi_subject_id from Bangumi evidence.')
            elif code == 'unknown_subject_id':
                related = list(getattr(issue, 'related_refs', []) or [])
                source_paths = [ref for ref in related if not str(ref).startswith('subject:')]
                subject_refs = [ref for ref in related if str(ref).startswith('subject:')]
                hints.append(f'Unexposed {subject_refs[0] if subject_refs else "bangumi_subject_id"} cannot be used; do not invent subject IDs. For the affected source_path {source_paths[:2] or str(getattr(issue, "ref", "") or "")}, fetch targeted evidence with find_bangumi_targets_for_local_file, search/lookup, or relation graph, replace the ID with an exposed subject_id, then validate again.')
            elif code == 'missing_episode_locator':
                hints.append('For ordinary multi-file runs, add source_pattern/filename_regex with an episode capture and offset. For one exact file that intentionally covers multiple Bangumi episodes, keep the exact path and use source_unit: "single_file_multi_episode" with episode_range; do not degrade it to the first episode_id.')
            elif code == 'invalid_source_unit_selector':
                hints.append('source_unit: "single_file_multi_episode" is only for exactly one visible source_path. Use one exact_paths entry for the merged file, or use an ordinary source_pattern rule for multi-file sequences.')
            elif code == 'invalid_multi_episode_target_locator':
                hints.append('For source_unit: "single_file_multi_episode", remove episode_id/sort/ep and keep subject_id plus episode_range so the verifier can cover every episode in the span.')
            elif code == 'invalid_episode_range':
                hints.append('For source_unit: "single_file_multi_episode", episode_range must name at least two target episode sort numbers, such as "1-3"; apply episode_offset only when the Bangumi sort values restart.')
            elif code == 'missing_multi_episode_evidence':
                hints.append('A single file covering multiple episodes needs mechanical evidence. Check local container_facts for chapter_count/chapter_durations, an explicit filename episode range such as [01-03] matching episode_range, or local duration close to the sum of exposed Bangumi episode durations; if those facts are absent or contradictory, fail_closed instead of mapping it to episode 1.')
            elif code in {'invalid_filename_regex', 'invalid_exclude_regex', 'invalid_episode_offset', 'invalid_episode_capture', 'episode_locator_miss', 'episode_out_of_range'}:
                hints.append('Fix the selector/episode expression, then validate again. Use source_pattern only for repeated file groups with a numeric {ep} capture and EP, EP-10, or EP*2-1 offsets. For a single movie/OVA/SP/special file, use exact_paths or source_path instead of source_pattern/episode_range.')
            elif code == 'unresolved_assignment':
                hints.append('Accepted recipes cannot contain needs_more_evidence or unaligned_fail_closed. Either resolve the path or call fail_closed for the whole case.')
        return _dedupe_nonempty(hints)

    def _hydrate_recipe_target_evidence(self, recipe: OrganizeRecipeDraft) -> None:
        subject_ids = sorted({
            int(getattr(rule.target, 'bangumi_subject_id', 0) or 0)
            for rule in recipe.rules
            if str(getattr(rule, 'disposition', '') or '') == 'map_to_bangumi'
        })
        for subject_id in subject_ids:
            if subject_id <= 0:
                continue
            try:
                episodes = self.bangumi_client.get_episodes(subject_id) or []
            except Exception:
                continue
            self._ensure_subject_known(subject_id)
            self._upsert_item_cards([self._episode_card_from_api(subject_id, episode) for episode in list(episodes)[:500]])

    def _write_recipe_artifacts(
        self,
        recipe: OrganizeRecipeDraft,
        plan: CompiledOrganizePlan,
        verifier_result: CaseVerifierResult,
        *,
        repair_hints: list[str] | None = None,
        review_warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        artifacts = self.run_dir / 'artifacts'
        (artifacts / 'organize_recipe.json').write_text(json.dumps(recipe.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        (artifacts / 'compiled_plan.json').write_text(json.dumps(plan.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        verifier_payload = verifier_result.model_dump(mode='json')
        if repair_hints is not None:
            verifier_payload['repair_hints'] = _dedupe_nonempty(repair_hints)
        if review_warnings is not None:
            verifier_payload['review_warnings'] = review_warnings
        (artifacts / 'recipe_verifier_result.json').write_text(json.dumps(verifier_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')

    def _run_recipe_helper_check(self) -> dict[str, Any]:
        artifacts = self.run_dir / 'artifacts'
        recipe_path = artifacts / 'organize_recipe.json'
        case_input_path = self.run_dir / 'case_input.json'
        helper_path = artifacts / 'organize_recipe_helper_check.json'
        script_path = self.repo_root / '.pi' / 'skills' / 'organize-recipe-contract' / 'scripts' / 'check-organize-recipe.mjs'
        if not case_input_path.exists():
            case_input_path.write_text(
                json.dumps(self.case_input(timeout_seconds=0), ensure_ascii=False, indent=2, sort_keys=True),
                encoding='utf-8',
            )
        command = ['node', str(script_path), str(recipe_path), str(case_input_path)]
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
                check=False,
            )
            try:
                payload = json.loads(completed.stdout or '{}')
            except json.JSONDecodeError:
                payload = {}
            artifact = {
                **payload,
                'ok': bool(payload.get('ok')) and completed.returncode == 0,
                'helper_command': command,
                'returncode': completed.returncode,
                'timed_out': False,
                'stderr': completed.stderr,
            }
            if not payload and completed.stdout:
                artifact['stdout'] = completed.stdout
        except subprocess.TimeoutExpired as exc:
            artifact = {
                'ok': False,
                'helper_command': command,
                'returncode': None,
                'timed_out': True,
                'stderr': str(exc.stderr or ''),
                'error': 'helper check timed out',
            }
        except Exception as exc:
            artifact = {
                'ok': False,
                'helper_command': command,
                'returncode': None,
                'timed_out': False,
                'stderr': '',
                'error': f'{type(exc).__name__}: {exc}',
            }
        helper_path.write_text(json.dumps(_json_safe(artifact), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        return _json_safe(artifact)

    def _write_final_result(self) -> None:
        if self.final_result is None:
            return
        (self.run_dir / 'final_result.json').write_text(
            json.dumps(_json_safe(self.final_result), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )

    def tool_summary(self) -> dict[str, Any]:
        return {
            'tool_trace_count': len(self.tool_trace),
            'tool_call_counts': _counter([str(row.get('tool') or '') for row in self.tool_trace]),
            'tool_sequence': [str(row.get('tool') or '') for row in self.tool_trace],
            'submit_rejection_count': self.submit_rejection_count,
        }

    def _subject_card_from_api(self, subject: Any, *, relation_to_main: str = '') -> BangumiSubjectCard:
        subject_id = int(getattr(subject, 'id', getattr(subject, 'subject_id', 0)) or 0)
        raw_type = getattr(subject, 'type', getattr(subject, 'subject_type', 0))
        return BangumiSubjectCard(
            ref=f'subject:{subject_id}',
            subject_id=subject_id,
            subject_type=_subject_type_from_api(raw_type),
            title=getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', ''),
            name=getattr(subject, 'name', '') or '',
            name_cn=getattr(subject, 'name_cn', '') or '',
            date=getattr(subject, 'date', '') or '',
            summary_short=getattr(subject, 'summary', '') or '',
            platform=getattr(subject, 'platform', '') or '',
            eps=int(getattr(subject, 'eps', 0) or 0),
            total_episodes=int(getattr(subject, 'total_episodes', 0) or 0),
            tags=list(getattr(subject, 'tags', []) or []),
            relation_to_main=relation_to_main,
            retrieval_round=1,
        )

    def _episode_card_from_api(self, subject_id: int, episode: Any) -> BangumiItemCard:
        episode_id = int(getattr(episode, 'id', 0) or 0)
        ep_type = getattr(episode, 'type', 0)
        item_kind = 'episode' if ep_type in (0, '0', 'regular', '') else 'special'
        source_hint = str(getattr(episode, 'source_form_hint', '') or '')
        if source_hint == 'movie':
            item_kind = 'movie'
        return BangumiItemCard(
            ref=f'episode:{episode_id}' if episode_id else f'subject:{subject_id}:sort:{int(getattr(episode, "sort", 0) or 0)}',
            item_kind=item_kind,
            episode_id=episode_id,
            kind=str(getattr(episode, 'kind', '') or ''),
            type=str(ep_type),
            sort=int(getattr(episode, 'sort', 0) or 0),
            ep=int(getattr(episode, 'ep', 0) or 0),
            subject_ref=f'subject:{subject_id}',
            title=getattr(episode, 'title', '') or getattr(episode, 'name_cn', '') or getattr(episode, 'name', '') or '',
            name=getattr(episode, 'name', '') or '',
            name_cn=getattr(episode, 'name_cn', '') or '',
            airdate=getattr(episode, 'airdate', '') or '',
            duration=getattr(episode, 'duration', '') or '',
            duration_seconds=int(getattr(episode, 'duration_seconds', 0) or 0),
            desc_short=(getattr(episode, 'desc', '') or '')[:160],
            synthetic=bool(getattr(episode, 'synthetic', False)),
            source_form_hint=source_hint,
            relation_to_main=getattr(episode, 'relation_to_main', '') or getattr(episode, 'relation', '') or '',
            episode_number=int(getattr(episode, 'ep', 0) or 0),
        )

    def _upsert_subject_cards(self, cards: list[BangumiSubjectCard]) -> None:
        if not cards:
            return
        self.workspace = self.workspace.with_replaced_cards(subjects=cards)

    def _upsert_item_cards(self, cards: list[BangumiItemCard]) -> None:
        if not cards:
            return
        self.workspace = self.workspace.with_replaced_cards(items=cards)

    def _ensure_subject_known(self, subject_id: int) -> None:
        if subject_id <= 0:
            return
        if any(int(getattr(card, 'subject_id', 0) or 0) == subject_id for card in self.workspace.bangumi_subjects):
            return
        try:
            subject = self.bangumi_client.get_subject(subject_id)
        except Exception:
            subject = None
        if subject is not None:
            self._upsert_subject_cards([self._subject_card_from_api(subject)])

    def _select_episodes(self, episodes: list[Any], scope: str) -> list[Any]:
        if scope == 'regular':
            selected = [ep for ep in episodes if getattr(ep, 'type', 0) in (0, '0')]
        elif scope == 'special':
            selected = [ep for ep in episodes if getattr(ep, 'type', 0) not in (0, '0')]
        else:
            selected = list(episodes)
        return selected

    def _subject_payload(self, card: BangumiSubjectCard, *, include_summary: bool = True) -> dict[str, Any]:
        payload = {
            'subject_id': card.subject_id,
            'subject_type': card.subject_type,
            'title': card.title or card.name_cn or card.name,
            'name': card.name,
            'name_cn': card.name_cn,
            'date': card.date,
            'platform': card.platform,
            'eps': card.eps,
            'total_episodes': card.total_episodes,
            'source_form_hint': card.source_form_hint,
            'relation_to_main': card.relation_to_main,
        }
        if include_summary:
            payload['summary_short'] = card.summary_short
        return payload

    def _subject_id_for_item(self, card: BangumiItemCard) -> int:
        subject_ref = str(getattr(card, 'subject_ref', '') or '')
        for subject in self.workspace.bangumi_subjects:
            if subject.ref == subject_ref:
                return int(subject.subject_id or 0)
        if subject_ref.startswith('subject:'):
            try:
                return int(subject_ref.split(':', 1)[1])
            except ValueError:
                return 0
        return 0

    def _episode_payload(self, card: BangumiItemCard, *, kind_hint: str = '') -> dict[str, Any]:
        return {
            'subject_id': self._subject_id_for_item(card),
            'episode_id': card.episode_id,
            'episode_type': self._recipe_episode_type_for_item(card, kind_hint=kind_hint),
            'api_item_kind': card.item_kind,
            'api_type': card.type,
            'api_kind': card.kind,
            'type': card.type,
            'sort': card.sort,
            'ep': card.ep,
            'title': card.title or card.name_cn or card.name,
            'name': card.name,
            'name_cn': card.name_cn,
            'airdate': card.airdate,
            'duration': card.duration,
            'source_form_hint': card.source_form_hint,
            'relation_to_main': card.relation_to_main,
        }

    def _recipe_episode_type_for_item(self, card: BangumiItemCard, *, kind_hint: str = '') -> str:
        source_hint = _normalize_kind_hint(str(getattr(card, 'source_form_hint', '') or ''))
        item_kind = str(getattr(card, 'item_kind', '') or '').casefold()
        if item_kind == 'episode':
            return 'regular'
        if item_kind == 'movie':
            return 'movie'
        if item_kind == 'special':
            hinted = _recipe_episode_type_from_kind(source_hint) if source_hint in {'ova', 'oad'} else _recipe_episode_type_from_kind(kind_hint)
            return hinted if hinted in {'ova', 'oad'} else 'special'
        if item_kind == 'unknown':
            return 'unknown'
        ep_type = str(getattr(card, 'type', '') or '').casefold()
        return 'regular' if ep_type in {'', '0', 'regular', 'main', 'episode'} else 'special'

    def _local_file_payload(self, card: Any, *, detail: bool = False) -> dict[str, Any]:
        payload = {
            'source_path': _norm_path(str(getattr(card, 'path', '') or '')),
            'basename': _norm_path(str(getattr(card, 'path', '') or '')).rsplit('/', 1)[-1],
            'size_bytes': int(getattr(card, 'size_bytes', 0) or 0),
            'is_main': bool(getattr(card, 'is_main', False)),
            'label': str(getattr(card, 'label', '') or ''),
            'parent_display': str(getattr(card, 'parent_display', '') or ''),
            'fact_summary': _json_safe(getattr(card, 'fact_summary', {}) or {}),
        }
        if detail:
            payload.update({
                'path_facts': _json_safe(getattr(card, 'path_facts', {}) or {}),
                'container_facts': _json_safe(getattr(card, 'container_facts', {}) or {}),
                'stream_facts': _json_safe(getattr(card, 'stream_facts', {}) or {}),
                'subtitle_facts': _json_safe(getattr(card, 'subtitle_facts', {}) or {}),
            })
        return payload

def _norm_path(path: str) -> str:
    return str(path or '').replace('\\', '/').strip().lstrip('./')


def _string_or_default(value: Any, default: str = '') -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_norm_path(value)] if _norm_path(value) else []
    if isinstance(value, list):
        return [_norm_path(str(item)) for item in value if _norm_path(str(item))]
    return []


def _optional_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    return int(value)


def _episode_card_order_key(card: BangumiItemCard) -> tuple[int, int, int]:
    sort_value = int(getattr(card, 'sort', 0) or 0)
    ep_value = int(getattr(card, 'ep', 0) or 0)
    episode_id = int(getattr(card, 'episode_id', 0) or 0)
    return (
        sort_value if sort_value > 0 else 999999,
        ep_value if ep_value > 0 else 999999,
        episode_id if episode_id > 0 else 999999999,
    )


def _subject_type_from_api(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {'anime', 'book', 'game', 'music', 'real'}:
            return normalized
        if normalized.isdigit():
            value = int(normalized)
        else:
            return 'unknown'
    try:
        numeric = int(value or 0)
    except (TypeError, ValueError):
        return 'unknown'
    return {
        1: 'book',
        2: 'anime',
        3: 'music',
        4: 'game',
        6: 'real',
    }.get(numeric, 'unknown')


def _first_present(*mappings: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for mapping in mappings:
        for key in keys:
            if key in mapping:
                return mapping.get(key)
    return None


_SOURCE_PATTERN_TOKEN_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)(?::0?(\d+)d?)?\}')
_LEGAL_MEDIA_KINDS = {'tv', 'movie', 'ova', 'oad', 'sp', 'special', 'unknown'}
_LEGAL_EPISODE_TYPES = {'main', 'regular', 'special', 'ova', 'oad', 'movie', 'unknown'}
_LEGAL_SOURCE_UNITS = {'single_file', 'single_file_multi_episode'}
_PARAMS_RULE_HINT_KEYS = {
    'bangumi_subject_id',
    'episode_id',
    'episode_number_field',
    'episode_range',
    'episode_start',
    'episode_end',
    'episode_type',
    'exact_paths',
    'media_kind',
    'path',
    'paths',
    'range',
    'range_start',
    'range_end',
    'source_path',
    'source_paths',
    'source_pattern',
    'source_template',
    'subject_id',
    'target_episode_id',
    'target_number_field',
    'target_subject_id',
}
_RAW_RECIPE_RULE_KEYS = {'select', 'target', 'episode'}
_UNSUPPORTED_DISPOSITION_FLAG_HINTS = {
    'non_bangumi_or_supplemental': 'disposition: "non_bangumi_or_supplemental"',
    'supplemental': 'disposition: "non_bangumi_or_supplemental"',
    'exclude': 'disposition: "non_bangumi_or_supplemental"',
    'excluded': 'disposition: "non_bangumi_or_supplemental"',
    'unmapped': 'disposition: "non_bangumi_or_supplemental"',
    'map_to_bangumi': 'disposition: "map_to_bangumi"',
    'needs_more_evidence': 'disposition: "needs_more_evidence"',
    'unaligned_fail_closed': 'disposition: "unaligned_fail_closed"',
}
_UNSUPPORTED_SOURCE_UNIT_FLAG_HINTS = {
    'single_file_multi_episode': 'source_unit: "single_file_multi_episode"',
    'multi_episode': 'source_unit: "single_file_multi_episode"',
    'multi_episode_file': 'source_unit: "single_file_multi_episode"',
    'merged': 'source_unit: "single_file_multi_episode"',
}


def _looks_like_recipe_params_payload(payload: dict[str, Any]) -> bool:
    candidate = payload.get('recipe_params') if isinstance(payload.get('recipe_params'), dict) else payload
    rules = candidate.get('rules') if isinstance(candidate, dict) else None
    if not isinstance(rules, list) or not rules:
        return False
    saw_params_hint = False
    for rule in rules:
        if not isinstance(rule, dict):
            return False
        if _RAW_RECIPE_RULE_KEYS.intersection(rule):
            return False
        if _PARAMS_RULE_HINT_KEYS.intersection(rule):
            saw_params_hint = True
    return saw_params_hint


def _source_pattern_from_params(rule: dict[str, Any], select: dict[str, Any]) -> str:
    candidates = [
        _string_or_default(_first_present(mapping, keys=(key,)), '')
        for key in ('source_pattern', 'source_template', 'filename_pattern')
        for mapping in (rule, select)
    ]
    with_ep = [candidate for candidate in candidates if re.search(r'\{ep(?::[^}]*)?\}', candidate)]
    return with_ep[0] if with_ep else next((candidate for candidate in candidates if candidate), '')


def _reject_unsupported_disposition_flags(rule: dict[str, Any], *, index: int, disposition: str) -> None:
    flagged = [
        key
        for key in _UNSUPPORTED_DISPOSITION_FLAG_HINTS
        if key in rule
        and isinstance(rule.get(key), bool)
    ]
    if not flagged:
        return
    if disposition:
        raise ValueError(
            f'rules[{index - 1}] mixes disposition with unsupported boolean field(s) {flagged}; '
            'remove the boolean field(s) and keep the single disposition enum.'
        )
    suggested = _UNSUPPORTED_DISPOSITION_FLAG_HINTS.get(flagged[0], 'disposition: "non_bangumi_or_supplemental"')
    raise ValueError(
        f'rules[{index - 1}] uses unsupported boolean field(s) {flagged}. '
        f'Use the disposition enum instead, for example {suggested}.'
    )


def _source_unit_from_params(rule: dict[str, Any], *, index: int) -> str:
    flagged = [
        key
        for key in _UNSUPPORTED_SOURCE_UNIT_FLAG_HINTS
        if key in rule
        and isinstance(rule.get(key), bool)
    ]
    if flagged:
        suggested = _UNSUPPORTED_SOURCE_UNIT_FLAG_HINTS[flagged[0]]
        raise ValueError(
            f'rules[{index - 1}] uses unsupported boolean source-unit field(s) {flagged}. '
            f'Use the source_unit enum instead, for example {suggested}.'
        )
    value = _string_or_default(_first_present(rule, keys=('source_unit', 'source_kind', 'unit')), '').casefold()
    return value if value in _LEGAL_SOURCE_UNITS else 'single_file'


def _media_kind_from_params(rule: dict[str, Any], target: dict[str, Any]) -> str:
    for key in ('media_kind', 'target_media_kind'):
        value = _string_or_default(_first_present(rule, target, keys=(key,)), '').casefold()
        if value in _LEGAL_MEDIA_KINDS:
            return value
    for key in ('kind',):
        value = _string_or_default(_first_present(rule, target, keys=(key,)), '').casefold()
        if value in _LEGAL_MEDIA_KINDS:
            return value
    return 'unknown'


def _episode_type_from_params(rule: dict[str, Any], target: dict[str, Any]) -> str:
    for key in ('episode_type', 'episode_kind', 'target_episode_type'):
        value = _string_or_default(_first_present(rule, target, keys=(key,)), '').casefold()
        if value in _LEGAL_EPISODE_TYPES:
            return value
    for key in ('type',):
        value = _string_or_default(_first_present(rule, target, keys=(key,)), '').casefold()
        if value in _LEGAL_EPISODE_TYPES:
            return value
    return 'unknown'


def _source_pattern_to_regex(pattern: str) -> str:
    text = _norm_path(pattern)
    parts: list[str] = []
    index = 0
    for match in _SOURCE_PATTERN_TOKEN_RE.finditer(text):
        parts.append(_escape_source_pattern_segment(text[index:match.start()]))
        name = match.group(1)
        width = int(match.group(2) or 0)
        if name == 'ep':
            parts.append(rf'(?P<{name}>\d{{{width}}})' if width > 0 else rf'(?P<{name}>\d+)')
        else:
            parts.append(r'.*?')
        index = match.end()
    parts.append(_escape_source_pattern_segment(text[index:]))
    return ''.join(parts)


def _escape_source_pattern_segment(segment: str) -> str:
    parts: list[str] = []
    for char in str(segment or ''):
        if char == '*':
            parts.append(r'.*?')
        elif char == '?':
            parts.append(r'.')
        else:
            parts.append(re.escape(char))
    return ''.join(parts)


def _episode_range_from_params(rule: dict[str, Any], episode: dict[str, Any]) -> str:
    explicit = _string_or_default(_first_present(rule, episode, keys=('episode_range', 'range')), '')
    if explicit:
        return explicit
    start = _first_present(rule, episode, keys=('episode_range_start', 'range_start', 'episode_start'))
    end = _first_present(rule, episode, keys=('episode_range_end', 'range_end', 'episode_end'))
    start_text = _string_or_default(start, '')
    end_text = _string_or_default(end, '')
    if start_text and end_text:
        return f'{start_text}-{end_text}'
    return start_text or end_text


def _episode_offset_from_params(rule: dict[str, Any], episode: dict[str, Any]) -> str:
    value = _first_present(rule, episode, keys=('episode_offset', 'offset'))
    if value is None:
        return 'EP'
    if isinstance(value, (int, float)) and float(value).is_integer():
        return _numeric_offset_to_expr(int(value))
    text = str(value).strip()
    if not text:
        return 'EP'
    if re.fullmatch(r'[+-]?\d+', text):
        return _numeric_offset_to_expr(int(text))
    if text.casefold() == 'ep':
        return 'EP'
    return text


def _episode_number_field_from_params(rule: dict[str, Any], episode: dict[str, Any]) -> str:
    value = _string_or_default(
        _first_present(
            rule,
            episode,
            keys=(
                'episode_number_field',
                'target_number_field',
                'number_field',
                'match_number_field',
                'match_field',
            ),
        ),
        'sort',
    ).casefold()
    aliases = {
        'sort': 'sort',
        'episode_sort': 'sort',
        'bangumi_sort': 'sort',
        'target_sort': 'sort',
        'ep': 'ep',
        'episode_ep': 'ep',
        'bangumi_ep': 'ep',
        'target_ep': 'ep',
    }
    return aliases.get(value, 'sort')


def _numeric_offset_to_expr(value: int) -> str:
    if value == 0:
        return 'EP'
    if value > 0:
        return f'EP+{value}'
    return f'EP{value}'


def _simple_episode_offset_delta(expr: str) -> int | None:
    text = str(expr or '').replace(' ', '').upper()
    if text == 'EP':
        return 0
    match = re.fullmatch(r'EP([+-]\d+)', text)
    if not match:
        return None
    return int(match.group(1))


def _episode_range_numbers(spec: str) -> list[int]:
    numbers: list[int] = []
    for part in [item.strip() for item in str(spec or '').split(',') if item.strip()]:
        if '-' in part:
            left_text, right_text = part.split('-', 1)
            try:
                left = int(left_text)
                right = int(right_text)
            except ValueError:
                return []
            if right < left:
                return []
            numbers.extend(range(left, right + 1))
        else:
            try:
                numbers.append(int(part))
            except ValueError:
                return []
    return list(dict.fromkeys(numbers))


def _is_contiguous_numbers(numbers: list[int]) -> bool:
    if not numbers:
        return False
    ordered = sorted(set(numbers))
    return ordered == list(range(ordered[0], ordered[-1] + 1))


def _numbers_to_episode_range(numbers: list[int]) -> str:
    ordered = sorted(set(numbers))
    if not ordered:
        return ''
    if len(ordered) == 1:
        return str(ordered[0])
    return f'{ordered[0]}-{ordered[-1]}'


def _episode_type_matches_recipe(card: BangumiItemCard, expected: str) -> bool:
    expected = str(expected or 'unknown')
    if expected in {'', 'unknown'}:
        return True
    item_type = str(getattr(card, 'type', '') or '').casefold()
    item_kind = str(getattr(card, 'kind', '') or '').casefold()
    item_item_kind = str(getattr(card, 'item_kind', '') or '').casefold()
    if expected in {'main', 'regular'}:
        return item_type in {'0', 'regular', 'main', ''} and item_item_kind in {'episode', ''}
    if expected in {'special', 'sp', 'ova', 'oad'}:
        return item_type not in {'0', 'regular', 'main'} or item_kind in {'special', expected} or item_item_kind == 'special'
    if expected == 'movie':
        return item_item_kind == 'movie' or item_kind == 'movie'
    return True


def _parse_error_repair_hints(error: str, visible_paths: list[str]) -> list[str]:
    text = str(error or '')
    hints: list[str] = []
    if 'episode_type' in text and 'episode' in text:
        hints.append("target.episode_type cannot be raw API value 'episode'. Use 'regular' for normal TV episodes, or use 'special', 'ova', 'oad', or 'movie' when evidence supports that shape.")
    if 'media_kind' in text and 'web' in text.casefold():
        hints.append("target.media_kind cannot be raw source/API value 'web'. Use one of tv, movie, ova, oad, sp, special, or unknown.")
    if 'extra_forbidden' in text or 'Extra inputs are not permitted' in text:
        hints.append('Remove unknown fields from the recipe. The verifier accepts only the OrganizeRecipeDraft schema from the skill.')
    if 'unsupported boolean field' in text and 'disposition' in text:
        hints.append('Use a single disposition enum. For supplemental/excluded files, write disposition: "non_bangumi_or_supplemental"; do not write non_bangumi_or_supplemental: true, supplemental: true, or exclude: true.')
    if 'unsupported boolean source-unit field' in text or ('source_unit' in text and 'boolean' in text.casefold()):
        hints.append('Use source_unit: "single_file_multi_episode" for one visible file that intentionally covers multiple Bangumi episodes; do not write boolean flags such as multi_episode: true or merged: true.')
    if 'rules' in text and ('missing' in text.casefold() or 'Field required' in text):
        hints.append('OrganizeRecipeDraft must include version, summary, and a non-empty rules array.')
    if visible_paths:
        hints.append(f'Use exact source_path strings from the visible local universe when writing exact_paths. Visible source_path sample: {visible_paths[:8]}')
    return _dedupe_nonempty(hints)


def _helper_check_repair_hints(helper_check: dict[str, Any]) -> list[str]:
    if helper_check.get('ok'):
        return []
    hints: list[str] = []
    issues = helper_check.get('issues')
    if isinstance(issues, list) and issues:
        hints.append(f'Local helper check reported issues: {issues[:5]}')
    error = str(helper_check.get('error') or '')
    if error:
        hints.append(f'Local helper check failed: {error}')
    stderr = str(helper_check.get('stderr') or '').strip()
    if stderr:
        hints.append(f'Local helper stderr: {stderr[:500]}')
    if not hints:
        hints.append('Local helper check did not accept the recipe; inspect organize_recipe_helper_check.json and repair the draft.')
    return _dedupe_nonempty(hints)


def _review_warning_hints(review_warnings: list[dict[str, Any]]) -> list[str]:
    hints: list[str] = []
    for warning in review_warnings:
        if not isinstance(warning, dict):
            continue
        hint = str(warning.get('repair_hint') or '').strip()
        if hint:
            hints.append(hint)
    return _dedupe_nonempty(hints)


def _dedupe_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value or '').strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _normalize_kind_hint(value: str) -> str:
    key = str(value or '').strip().casefold().replace('-', '_').replace(' ', '_')
    aliases = {
        'movie': 'movie',
        'film': 'movie',
        'theatrical': 'movie',
        'ova': 'ova',
        'oad': 'oad',
        'sp': 'special',
        'special': 'special',
        'tvsp': 'special',
        'tv_special': 'special',
        'regular': 'regular',
        'main': 'regular',
        'tv': 'regular',
        'episode': 'regular',
    }
    return aliases.get(key, '')


def _query_from_source_path(source_path: str) -> str:
    basename = _norm_path(source_path).rsplit('/', 1)[-1]
    stem = re.sub(r'\.[^.]+$', '', basename)
    stem = re.sub(r'\[[^\]]+\]|\([^)]+\)', ' ', stem)
    stem = re.sub(r'\b(BDRip|BluRay|BD|WEB[- ]?DL|WEBRip|x264|x265|HEVC|AVC|FLAC|AAC|Hi10P|10bit|1080p|720p|2160p)\b', ' ', stem, flags=re.IGNORECASE)
    stem = re.sub(r'\s+', ' ', stem).strip(' -_')
    return stem or basename


def _query_without_terminal_kind(query: str, kind_hint: str) -> str:
    if not query:
        return ''
    if _normalize_kind_hint(kind_hint) in {'ova', 'oad', 'special', 'movie'}:
        return re.sub(r'\b(OVA|OAD|SP|Special|TVSP|Movie)\s*\d*\b', ' ', query, flags=re.IGNORECASE).strip(' -_')
    return ''


def _recipe_episode_type_from_kind(kind_hint: str) -> str:
    kind = _normalize_kind_hint(kind_hint)
    if kind in {'movie', 'ova', 'oad'}:
        return kind
    if kind == 'special':
        return 'special'
    if kind == 'regular':
        return 'regular'
    return 'unknown'
