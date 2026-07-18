from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, is_dataclass
from types import SimpleNamespace
from typing import Any

from .models import (
    BangumiGroupCard,
    BangumiItemCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseContract,
    CaseDossier,
    CaseHeader,
    LocalClusterCard,
    LocalFileCard,
    LocalSpanCard,
    ProvenanceCard,
    QueryCard,
)
from .dossier import build_bounded_case_dossier
from .query_cards import build_query_cards
from .audit import summarize_case_agent_snapshot_refs
from .assignment_expander import expand_bulk_assignment_intents
from .mapping_draft import compute_local_span_partition_coverage
from .mapping_draft import compute_mapping_draft_accounting
from .pi_runner import PiCaseAgentRunResult, run_pi_case_agent
from .recipe import recipe_accounting
from .workspace import CaseEvidenceWorkspace
from ..local_fact_surface import (
    compact_fact_surface_summary,
    compact_file_fact_for_card,
    compact_file_fact_summary,
    local_fact_surface_to_dict,
)
from ..local_supplemental_filter import classify_local_video_supplemental
from ...config.config_manager import cm
from ...bangumi.client import BangumiClient


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


def _is_case_judge_audit(audit: dict[str, Any]) -> bool:
    round_kind = str(audit.get('round_kind') or '').strip()
    if round_kind not in {'initial', 'policy_retry', 'evidence_rejudge', 'issue_response'}:
        return False
    return True


def _canonical_case_agent_status(result) -> str:
    status = str(getattr(result, 'status', '') or '')
    if status == 'fail_closed':
        return 'fail_closed'
    if status == 'accepted':
        return 'accepted'
    if status in {'invalid', 'error'}:
        return status
    return 'unknown'


def _is_case_judge_call_audit(audit: dict[str, Any]) -> bool:
    return _is_case_judge_audit(audit) and str(audit.get('call_name') or '').strip() == 'call_case_judge'


def _derive_case_judge_round_actions(request_audits: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for audit in request_audits:
        if not _is_case_judge_audit(audit):
            continue
        actions.append(str(audit.get('action_actual') or audit.get('action') or audit.get('action_name') or audit.get('call_name') or ''))
    return actions


def _sample_cards(cards: list[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    sampled: list[dict[str, Any]] = []
    for card in cards[:limit]:
        if hasattr(card, 'model_dump'):
            data = card.model_dump(mode='json')
        elif is_dataclass(card):
            data = asdict(card)
        elif isinstance(card, dict):
            data = dict(card)
        else:
            data = {'value': str(card)}
        sampled.append({k: data.get(k) for k in ('ref', 'title', 'sort', 'ep', 'subject_ref', 'path', 'label', 'is_main', 'name', 'name_cn') if k in data})
    return sampled


def _query_card_sample(cards: list[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    sampled: list[dict[str, Any]] = []
    for card in cards[:limit]:
        if hasattr(card, 'model_dump'):
            data = card.model_dump(mode='json')
        elif is_dataclass(card):
            data = asdict(card)
        elif isinstance(card, dict):
            data = dict(card)
        else:
            data = {'value': str(card)}
        refs = [str(ref) for ref in (data.get('source_refs') or []) if ref]
        item = {k: data.get(k) for k in ('ref', 'query_text', 'query_kind', 'result_refs') if k in data}
        item['source_ref_count'] = len(refs)
        item['source_ref_samples'] = refs[:8]
        if refs:
            item['source_ref_range'] = f'{refs[0]}..{refs[-1]}' if len(refs) > 1 else refs[0]
        sampled.append(item)
    return sampled


def _count_bulk_assignment_intents(final_output: Any, final_verifier_result: Any, result: Any) -> int:
    return len(getattr(final_output, 'bulk_assignment_intents', []) or [])


def _verdict_assignment_accounting(final_dossier: Any, final_output: Any) -> dict[str, int | bool]:
    if final_dossier is None or final_output is None:
        return {}
    if str(getattr(final_output, 'action', '') or '') != 'submit_verdict':
        return {}
    main_refs = list(getattr(getattr(final_dossier, 'contract', None), 'main_file_refs', []) or [])
    if not main_refs:
        return {}
    assignments = list(getattr(final_output, 'assignment_intents', []) or [])
    expanded_bulk, expansion_issues = expand_bulk_assignment_intents(final_dossier, final_output)
    assignments = [*assignments, *expanded_bulk]
    counts: dict[str, int] = {}
    mapped = excluded = manual_review = needs = unaligned = open_count = 0
    for assignment in assignments:
        file_ref = str(getattr(assignment, 'file_ref', '') or '')
        if file_ref not in main_refs:
            continue
        counts[file_ref] = counts.get(file_ref, 0) + 1
        target_ref = str(getattr(assignment, 'target_ref', '') or '')
        if target_ref == 'UNALIGNED':
            reason = str(getattr(assignment, 'reason', '') or '')
            if reason.startswith('mapping_draft:') and ':manual_review:' in reason:
                manual_review += 1
            elif reason.startswith('mapping_draft:') and ':supplemental:' in reason:
                excluded += 1
            else:
                unaligned += 1
        else:
            mapped += 1
    accounted = len(counts)
    duplicate_count = sum(max(0, count - 1) for count in counts.values())
    missing = max(0, len(main_refs) - accounted)
    unresolved = missing + duplicate_count + unaligned + needs + open_count
    return {
        'main_file_count': len(main_refs),
        'mapped_file_count': mapped,
        'excluded_file_count': excluded,
        'resolved_unmapped_file_count': excluded,
        'manual_review_file_count': manual_review,
        'needs_more_evidence_file_count': needs,
        'unaligned_file_count': unaligned,
        'open_file_count': open_count,
        'accounted_for_count': accounted,
        'unresolved_count': unresolved,
        'accepted_accounting_ready': bool(
            len(main_refs) > 0
            and not expansion_issues
            and accounted == len(main_refs)
            and duplicate_count == 0
            and unaligned == 0
            and needs == 0
            and open_count == 0
        ),
        'expanded_assignment_count': len(assignments),
    }


def _count_mapping_draft_metrics(result: Any, final_output: Any, final_verifier_result: Any) -> dict[str, int]:
    final_workspace = getattr(result, 'final_workspace', None)
    candidates: list[Any] = [final_workspace, result, final_output, final_verifier_result]
    mapping_draft = None
    for obj in candidates:
        if obj is None:
            continue
        value = getattr(obj, 'mapping_draft', None)
        if value is not None:
            mapping_draft = value
            break
    rows = list(getattr(mapping_draft, 'rows', []) or []) if mapping_draft is not None else []
    patches = list(getattr(final_workspace, 'mapping_draft_patches', []) or []) if final_workspace is not None else []
    if not patches:
        patches = list(getattr(result, 'mapping_draft_patches', []) or [])
    if not patches:
        patches = list(getattr(final_output, 'mapping_draft_patches', []) or [])
    span_patches = list(getattr(result, 'span_mapping_patches', []) or [])
    if not span_patches:
        span_patches = list(getattr(final_output, 'span_mapping_patches', []) or [])
    candidate_comparisons = []
    for source in (
        list(getattr(result, 'candidate_comparisons', []) or []),
        list(getattr(final_output, 'candidate_comparisons', []) or []),
        list(getattr(final_workspace, 'mapping_draft_candidate_comparisons', []) or []) if final_workspace is not None else [],
    ):
        by_ref = {
            str(getattr(comparison, 'ref', '') or ''): comparison
            for comparison in candidate_comparisons
            if str(getattr(comparison, 'ref', '') or '')
        }
        passthrough = [comparison for comparison in candidate_comparisons if not str(getattr(comparison, 'ref', '') or '')]
        for comparison in source:
            ref = str(getattr(comparison, 'ref', '') or '')
            if ref:
                by_ref[ref] = comparison
            else:
                passthrough.append(comparison)
        candidate_comparisons = [*passthrough, *by_ref.values()]
    local_coverage_count = sum(1 for row in rows if isinstance(row, dict) and bool(row.get('local_coverage')))
    missing_main_count = sum(1 for row in rows if isinstance(row, dict) and bool(row.get('missing_main')))
    return {
        'mapping_draft_row_count': len(rows),
        'mapping_draft_local_coverage_count': local_coverage_count,
        'mapping_draft_missing_main_count': missing_main_count,
        'mapping_draft_open_count': sum(1 for row in rows if str(getattr(row, 'status', '') or '') == 'open'),
        'mapping_draft_proposed_count': sum(1 for row in rows if str(getattr(row, 'status', '') or '') == 'proposed'),
        'mapping_draft_verified_count': sum(1 for row in rows if str(getattr(row, 'status', '') or '') == 'verified'),
        'mapping_draft_unresolved_count': sum(1 for row in rows if str(getattr(row, 'status', '') or '') == 'unresolved'),
        'mapping_draft_patch_count': len(patches),
        'span_mapping_patch_count': len(span_patches),
        'candidate_comparison_count': len(candidate_comparisons),
        'expanded_assignment_count': len(getattr(final_verifier_result, 'expanded_assignment_intents', []) or []) or len(getattr(final_output, 'assignment_intents', []) or []),
    }


def _count_span_items(cards: list[Any]) -> tuple[int, int]:
    span_count = 0
    main_count = 0
    for card in cards:
        span_refs = []
        if hasattr(card, 'model_dump'):
            data = card.model_dump(mode='json')
        elif is_dataclass(card):
            data = asdict(card)
        elif isinstance(card, dict):
            data = dict(card)
        else:
            data = {}
        for key in ('span_refs', 'target_span_refs', 'detail_span_refs'):
            value = data.get(key)
            if isinstance(value, list):
                span_refs = [str(ref) for ref in value if ref]
                break
        if not span_refs and data.get('span_count') is not None:
            try:
                span_count += int(data.get('span_count') or 0)
            except Exception:
                pass
        else:
            span_count += len(span_refs)
        if bool(data.get('is_main')):
            main_count += 1
    return span_count, main_count


def _snapshot_debug_enabled() -> bool:
    raw = cm.get_config('rename_local_bangumi_case_agent_snapshot_debug')
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'} if not isinstance(raw, bool) else raw


def _config_int(key: str, default: int) -> int:
    try:
        value = cm.get_config(key)
        return int(value if value is not None else default)
    except Exception:
        return default


def _config_int_at_least(key: str, default: int, minimum: int) -> int:
    return max(minimum, _config_int(key, default))


def _case_agent_round_safety_cap() -> int:
    # Pi native mode is bounded by wall-clock timeout, not turn count. Keep the
    # legacy header field at zero so downstream snapshots do not imply a turn cap.
    return 0


def _episode_structure_from_context(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        if hasattr(value, 'model_dump'):
            dumped = value.model_dump(mode='json')
            value = dumped if isinstance(dumped, dict) else {}
        else:
            value = {}
    context = value.get('context', value)
    if not isinstance(context, dict):
        context = {}
    episode_structure = context.get('episode_structure', context)
    return episode_structure if isinstance(episode_structure, dict) else {}


def _path_suffix(path: str) -> str:
    name = str(path or '').rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    if '.' not in name:
        return ''
    return f".{name.rsplit('.', 1)[-1].casefold()}"


def _recipe_path_id(path: str) -> str:
    return str(path or '').replace('\\', '/').strip().lstrip('./')


def _subject_identity_ref(subject_id: int, fallback_index: int) -> str:
    if int(subject_id or 0) > 0:
        return f'subject:{int(subject_id)}'
    return f'subject:context:{fallback_index}'


def _episode_identity_ref(subject_ref: str, episode_id: int, sort: int, fallback_index: int) -> str:
    if int(episode_id or 0) > 0:
        return f'episode:{int(episode_id)}'
    if subject_ref:
        return f'{subject_ref}:sort:{int(sort or 0)}'
    return f'episode:context:{fallback_index}'


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {'1', 'true', 'yes', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'off'}:
        return False
    return default


def _local_fact_lookup(local_evidence: object) -> dict[str, dict[str, object]]:
    surface = local_fact_surface_to_dict(getattr(local_evidence, 'fact_surface', None))
    lookup: dict[str, dict[str, object]] = {}
    for item in surface.get('files') or []:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get('file_id') or '')
        relative_path = str(item.get('relative_path') or '')
        if file_id:
            lookup[file_id] = item
        if relative_path:
            lookup[relative_path] = item
    return lookup


def _workspace_local_fact_summary(local_files: list[LocalFileCard]) -> dict[str, object]:
    probe_status_counts = Counter(
        str((getattr(card, 'container_facts', {}) or {}).get('probe_status') or 'unknown')
        for card in local_files
    )
    missing_class_counts = Counter(
        str(item.get('fact_class') or '')
        for card in local_files
        for item in (getattr(card, 'missing_facts', []) or [])
        if isinstance(item, dict) and str(item.get('fact_class') or '')
    )
    requestable_fact_card_count = sum(
        1
        for card in local_files
        if getattr(card, 'path_facts', None)
        or getattr(card, 'container_facts', None)
        or getattr(card, 'subtitle_facts', None)
        or getattr(card, 'stream_facts', None)
        or getattr(card, 'missing_facts', None)
    )
    return {
        'local_fact_card_count': requestable_fact_card_count,
        'local_fact_probe_status_counts': dict(sorted(probe_status_counts.items())),
        'local_fact_missing_class_counts': dict(sorted(missing_class_counts.items())),
    }


def build_local_bangumi_cards_view(local_evidence, bangumi_contexts: list[dict[str, object]]):
    root_name = str(getattr(local_evidence, 'root_name', '') or getattr(local_evidence, 'source_path', '') or 'local package')
    fact_lookup = _local_fact_lookup(local_evidence)
    local_files: list[dict[str, Any]] = []
    filtered_files: list[dict[str, Any]] = []
    cluster_refs: dict[str, str] = {}
    cluster_members: dict[str, list[str]] = {}
    main_file_refs: list[str] = []
    supplemental_file_refs: list[str] = []

    visible_index = 1
    for source_index, file in enumerate(list(getattr(local_evidence, 'files', []) or []), start=1):
        file_id = str(getattr(file, 'file_id', '') or '')
        relative_path = str(getattr(file, 'relative_path', '') or getattr(file, 'name', '') or '')
        name = str(getattr(file, 'name', '') or relative_path.rsplit('/', 1)[-1] or relative_path)
        parent_display = relative_path.rsplit('/', 1)[0] if '/' in relative_path else root_name
        suffix = str(getattr(file, 'suffix', '') or _path_suffix(relative_path or name))
        is_video = bool(getattr(file, 'is_video', False) or getattr(file, 'is_main_video_candidate', False) or suffix in {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts'})
        local_filter = classify_local_video_supplemental(relative_path or name, is_video=is_video)
        explicit_main_candidate = getattr(file, 'is_main_video_candidate', None)
        explicit_supplemental_candidate = getattr(file, 'is_supplemental_candidate', None)
        is_supplemental = (
            bool(local_filter.is_supplemental)
            or _as_bool(explicit_supplemental_candidate, False)
            or (is_video and explicit_main_candidate is not None and not _as_bool(explicit_main_candidate, True))
        )
        is_main = is_video and not is_supplemental
        raw_fact = fact_lookup.get(file_id) or fact_lookup.get(relative_path) or {}
        fact_card = compact_file_fact_for_card(raw_fact, detail=False)
        subtitle_compact_card = compact_file_fact_for_card(raw_fact, detail=True)
        subtitle_compact_facts = subtitle_compact_card.get('subtitle_facts', {}) if isinstance(subtitle_compact_card, dict) else {}
        if not (isinstance(subtitle_compact_facts, dict) and subtitle_compact_facts.get('bounded_text_snippets')):
            subtitle_compact_facts = {}
        fact_summary = compact_file_fact_summary(raw_fact)
        if not is_main:
            filtered_files.append({
                'source_index': source_index,
                'source_file_id': file_id,
                'path': relative_path or name,
                'relative_path': relative_path or name,
                'is_video': is_video,
                'is_supplemental_candidate': bool(is_supplemental),
                'rule_id': local_filter.rule_id or ('non_video' if not is_video else 'explicit_supplemental_candidate'),
                'reason_kind': local_filter.reason_kind or ('non_video_support' if not is_video else 'other_supplemental'),
                'reason': local_filter.reason or ('non-video local support file' if not is_video else 'explicit supplemental local file'),
                'fact_summary': fact_summary,
            })
            continue

        ref = _recipe_path_id(relative_path or name) or f'local-file-{visible_index}'
        visible_index += 1
        cluster_ref = cluster_refs.setdefault(parent_display, f'LC{len(cluster_refs) + 1}')
        cluster_members.setdefault(parent_display, []).append(ref)
        if is_main:
            main_file_refs.append(ref)
        else:
            supplemental_file_refs.append(ref)
        local_files.append({
            'ref': ref,
            'source_file_id': file_id,
            'path': relative_path or name,
            'relative_path': relative_path or name,
            'is_main_video_candidate': is_main,
            'is_supplemental_candidate': bool(is_supplemental),
            'local_filter_rule_id': local_filter.rule_id,
            'local_filter_reason_kind': local_filter.reason_kind,
            'local_filter_reason': local_filter.reason,
            'size_bytes': int(getattr(file, 'size_bytes', 0) or 0),
            'parent_display': parent_display,
            'label': name,
            'basename': name,
            'related_refs': [],
            'path_facts': fact_card.get('path_facts', {}),
            'container_facts': fact_card.get('container_facts', {}),
            'subtitle_facts': fact_card.get('subtitle_facts', {}),
            'subtitle_compact_facts': subtitle_compact_facts,
            'stream_facts': fact_card.get('stream_facts', {}),
            'missing_facts': list(fact_card.get('missing_facts') or []),
            'fact_summary': fact_summary,
        })

    local_cluster_cards = [
        {
            'ref': cluster_ref,
            'display_title': parent_display,
            'title': parent_display,
            'title_tokens': [parent_display] if parent_display else [],
            'member_refs': cluster_members.get(parent_display, []),
            'kind': 'local',
        }
        for parent_display, cluster_ref in cluster_refs.items()
    ]

    bangumi_cards: list[dict[str, Any]] = []
    episode_ref_index = 1
    for subject_index, context in enumerate(bangumi_contexts or [], start=1):
        structure = _episode_structure_from_context(context)
        if not structure:
            continue
        subject_id = int(structure.get('subject_id') or structure.get('id') or 0)
        subject_ref = _subject_identity_ref(subject_id, subject_index)
        group_ref = f'{subject_ref}:episodes'
        episodes = [item for item in (structure.get('episodes') or []) if isinstance(item, dict)]
        member_refs: list[str] = []
        bangumi_cards.append({
            'card_kind': 'subject',
            'ref': subject_ref,
            'entity_ref': subject_ref,
            'subject_id': subject_id,
            'title': structure.get('title') or structure.get('subject_title') or '',
            'subject_title': structure.get('subject_title') or structure.get('title') or '',
            'name': structure.get('name') or '',
            'name_cn': structure.get('name_cn') or '',
            'summary_short': structure.get('summary_short') or '',
            'episode_count': int(structure.get('episode_count') or len(episodes) or 0),
            'source_form_hint': structure.get('source_form_hint') or '',
            'relation_to_main': structure.get('relation_to_main') or '',
            'source_role': structure.get('source_role') or '',
        })
        for episode in episodes:
            episode_id = int(episode.get('episode_id') or episode.get('id') or 0)
            item_ref = _episode_identity_ref(subject_ref, episode_id, int(episode.get('sort') or 0), episode_ref_index)
            episode_ref_index += 1
            member_refs.append(item_ref)
            bangumi_cards.append({
                'card_kind': 'episode',
                'ref': item_ref,
                'item_ref': item_ref,
                'group_ref': group_ref,
                'entity_ref': subject_ref,
                'episode_id': episode_id,
                'kind': episode.get('kind') or episode.get('type') or 'regular',
                'sort': int(episode.get('sort') or 0),
                'ep': int(episode.get('ep') or episode.get('sort') or 0),
                'title': episode.get('title') or '',
                'name': episode.get('name') or '',
                'name_cn': episode.get('name_cn') or '',
                'runtime': episode.get('runtime') or episode.get('duration') or '',
                'summary_short': episode.get('summary_short') or episode.get('desc') or '',
                'synthetic': bool(episode.get('synthetic')),
                'source_form_hint': structure.get('source_form_hint') or '',
                'relation_to_main': structure.get('relation_to_main') or '',
            })
        sort_values = [
            int(item.get('sort') or 0)
            for item in episodes
        ]
        bangumi_cards.append({
            'card_kind': 'group',
            'ref': group_ref,
            'group_ref': group_ref,
            'entity_ref': subject_ref,
            'kind': 'special' if episodes and all(str(item.get('kind') or '') == 'special' for item in episodes) else 'regular',
            'member_refs': member_refs,
            'member_refs_sample': member_refs,
            'sort_range': [min(sort_values or [0]), max(sort_values or [0])],
            'title_examples': [str(item.get('title') or '') for item in episodes[:5] if item.get('title')],
        })

    source_ref_to_parent_ref = {
        file['ref']: cluster_refs.get(str(file.get('parent_display') or ''), '')
        for file in local_files
    }
    ref_map = SimpleNamespace(
        source_ref_to_parent_ref=source_ref_to_parent_ref,
        main_file_refs=main_file_refs,
        file_refs=[str(file['ref']) for file in local_files],
        supplemental_file_refs=supplemental_file_refs,
        filtered_files=filtered_files,
    )
    return SimpleNamespace(
        ref_map=ref_map,
        local_source_view={'files': local_files},
        local_bangumi_direct_view={
            'local_cluster_cards': local_cluster_cards,
            'bangumi_cards': bangumi_cards,
        },
    )


def _build_workspace(*, local_evidence, bangumi_contexts: list[dict[str, object]], local_package_analysis: Any | None = None) -> CaseEvidenceWorkspace:
    views = build_local_bangumi_cards_view(local_evidence, bangumi_contexts)
    local_files = [
        LocalFileCard(
            ref=card['ref'],
            source_file_id=str(card.get('source_file_id') or ''),
            path=card.get('path') or card.get('relative_path') or '',
            is_main=bool(card.get('is_main_video_candidate')),
            size_bytes=int(card.get('size_bytes') or 0),
            parent_display=card.get('parent_display') or '',
            cluster_ref=views.ref_map.source_ref_to_parent_ref.get(card['ref'], ''),
            label=card.get('label') or card.get('basename') or '',
            file_kind='video' if bool(card.get('is_main_video_candidate')) else 'unknown',
            related_refs=list(card.get('related_refs') or []),
            path_facts=dict(card.get('path_facts') or {}),
            container_facts=dict(card.get('container_facts') or {}),
            subtitle_facts=dict(card.get('subtitle_facts') or {}),
            subtitle_compact_facts=dict(card.get('subtitle_compact_facts') or {}),
            stream_facts=dict(card.get('stream_facts') or {}),
            missing_facts=[dict(item) for item in list(card.get('missing_facts') or []) if isinstance(item, dict)],
            fact_summary=dict(card.get('fact_summary') or {}),
        )
        for card in (views.local_source_view.get('files') or [])
        if isinstance(card, dict) and card.get('ref')
    ]
    local_clusters = [LocalClusterCard(ref=card['ref'], cluster_name=card.get('display_title') or card.get('title') or '', title_cues=list(card.get('title_tokens') or []), file_refs=list(card.get('member_refs') or []), cluster_kind='mixed' if card.get('kind') in {'movie_like', 'spinoff_like'} else 'local', summary=card.get('display_title') or '') for card in (views.local_bangumi_direct_view.get('local_cluster_cards') or []) if isinstance(card, dict) and card.get('ref')]
    bangumi_cards = [card for card in (views.local_bangumi_direct_view.get('bangumi_cards') or []) if isinstance(card, dict)]
    bangumi_subjects = [BangumiSubjectCard(ref=str(card.get('ref') or card.get('entity_ref') or ''), subject_id=int(card.get('subject_id') or 0), subject_type='anime', title=card.get('title') or card.get('subject_title') or '', name=card.get('name') or '', name_cn=card.get('name_cn') or '', summary_short=card.get('summary_short') or '', eps=int(card.get('episode_count') or 0), total_episodes=int(card.get('episode_count') or 0), source_form_hint=card.get('source_form_hint') or '', relation_to_main=card.get('relation_to_main') or '', source_role=card.get('source_role') or '') for card in bangumi_cards if card.get('card_kind') == 'subject']
    bangumi_groups = [BangumiGroupCard(ref=str(card.get('ref') or card.get('group_ref') or ''), group_kind='special_group' if str(card.get('kind') or '').endswith('special') else 'season_group', member_refs_visible=list(card.get('member_refs') or []), sort_start=int((card.get('sort_range') or [0, 0])[0] if card.get('sort_range') else 0), sort_end=int((card.get('sort_range') or [0, 0])[-1] if card.get('sort_range') else 0), title_examples=list(card.get('title_examples') or []), subject_refs=[str(card.get('entity_ref') or '')] if card.get('entity_ref') else [], item_refs=list(card.get('member_refs_sample') or [])) for card in bangumi_cards if card.get('card_kind') == 'group']
    bangumi_items = [BangumiItemCard(ref=str(card.get('ref') or card.get('item_ref') or ''), item_kind='episode' if str(card.get('kind') or '') != 'special' else 'special', episode_id=int(card.get('episode_id') or 0), kind=card.get('kind') or '', type=card.get('kind') or '', sort=int(card.get('sort') or 0), ep=int(card.get('ep') or 0), subject_ref=card.get('entity_ref') or '', title=card.get('title') or '', name=card.get('name') or '', name_cn=card.get('name_cn') or '', duration=str(card.get('runtime') or ''), desc_short=card.get('summary_short') or '', synthetic=bool(card.get('synthetic')), source_form_hint=card.get('source_form_hint') or '', relation_to_main=card.get('relation_to_main') or '', parent_refs=[str(card.get('group_ref') or '')] if card.get('group_ref') else []) for card in bangumi_cards if card.get('card_kind') == 'episode']
    derived_main_file_refs = list(views.ref_map.main_file_refs or views.ref_map.file_refs or [card.ref for card in local_files if card.is_main])
    query_cards = build_query_cards(local_files, local_clusters, bangumi_subjects)
    provenance_cards = [ProvenanceCard(ref='PV1', source_operation='local_bangumi_case_agent_entry', raw_response_count=len(bangumi_items), parent_refs=[card.ref for card in local_files[:2]])]
    contract = CaseContract(summary='Local->Bangumi case agent recipe workspace', expected_outcome='unknown', main_file_refs=derived_main_file_refs, supplemental_file_refs=list(views.ref_map.supplemental_file_refs or []), allowed_file_refs=list(views.ref_map.file_refs or [card.ref for card in local_files]), visible_target_refs=[card.ref for card in bangumi_items], coverage_rule='visible source_path values must be covered exactly once or fail closed', duplicate_rule='Bangumi episode targets must not be duplicated', support_rule='final recipe must use source_path strings and Bangumi IDs')
    round_safety_cap = _case_agent_round_safety_cap()
    header = CaseHeader(case_id=f'local-bangumi-{getattr(local_evidence, "source_path", "") or "mapping"}', max_rounds=round_safety_cap, status='open')
    budget = CaseBudget(
        max_judge_rounds=header.max_rounds,
        max_evidence_batches=_config_int_at_least('rename_local_bangumi_case_agent_max_evidence_batches', 12, 12),
        max_issue_response_rounds=_config_int('rename_local_bangumi_case_agent_max_issue_response_rounds', 1),
        max_requests_per_batch=_config_int('rename_local_bangumi_case_agent_max_requests_per_batch', 8),
        max_api_calls_per_case=240,
        max_subject_searches=48,
        max_search_results_per_query=8,
        max_related_depth=6,
        max_new_subject_cards=240,
        max_new_episode_cards=2000,
    )
    local_span_cards = _build_raw_local_span_shells(local_files, contract)
    workspace = CaseEvidenceWorkspace.from_cards(header=header, budget=budget, contract=contract, local_files=local_files, local_clusters=local_clusters, local_span_cards=local_span_cards, bangumi_subjects=bangumi_subjects, bangumi_groups=bangumi_groups, bangumi_items=bangumi_items, query_cards=query_cards, provenance_cards=provenance_cards)
    local_fact_summary = compact_fact_surface_summary(getattr(local_evidence, 'fact_surface', None))
    fact_audit = {
        'note': 'local_fact_surface_projection',
        'fact_surface_summary': local_fact_summary,
    } if int(local_fact_summary.get('file_fact_count') or 0) > 0 else {}
    if fact_audit:
        object.__setattr__(
            workspace,
            'judge_request_audits',
            [
                *list(getattr(workspace, 'judge_request_audits', []) or []),
                fact_audit,
            ],
        )
    return workspace


def _build_raw_local_span_shells(local_files: list[LocalFileCard], contract: CaseContract) -> list[LocalSpanCard]:
    main_refs = list(dict.fromkeys(list(contract.main_file_refs or [])))
    if not main_refs:
        return []
    files_by_ref = {card.ref: card for card in local_files if card.ref}
    title_cues = list(dict.fromkeys(
        str(getattr(files_by_ref.get(ref), 'parent_display', '') or '')
        for ref in main_refs
        if files_by_ref.get(ref) is not None and str(getattr(files_by_ref.get(ref), 'parent_display', '') or '')
    ))[:4]
    package = LocalSpanCard(
        ref='LS_PACKAGE',
        span_scope='package',
        file_refs=main_refs,
        file_ref_count=len(main_refs),
        file_ref_range=[main_refs[0], main_refs[-1]],
        file_ref_samples=[*main_refs[:2], *main_refs[-2:]] if len(main_refs) > 4 else main_refs,
        ordering_basis='path_order',
        title_cues=title_cues,
        confidence_facts=['raw local coverage shell; no ordinal inference'],
    )
    child = LocalSpanCard(
        ref='LS1',
        span_scope='unpartitioned',
        file_refs=main_refs,
        file_ref_count=len(main_refs),
        file_ref_range=[main_refs[0], main_refs[-1]],
        file_ref_samples=[*main_refs[:2], *main_refs[-2:]] if len(main_refs) > 4 else main_refs,
        ordering_basis='path_order',
        title_cues=title_cues,
        confidence_facts=['raw local coverage shell; local structure agent should refine'],
    )
    return [package, child]


def _pi_case_agent_audits(result: PiCaseAgentRunResult) -> list[dict[str, Any]]:
    audits = list(getattr(result.final_workspace, 'judge_request_audits', []) or [])
    audits.append({
        'note': 'pi_case_agent_session_summary',
        'case_agent_mode': 'pi_case_agent',
        'pi_run_dir': str(result.run_dir),
        'pi_case_id': result.case_id,
        'pi_command': result.pi_command,
        'runtime_command': list(result.runtime_command or []),
        'runtime_returncode': result.runtime_returncode,
        'tool_trace_count': len(result.tool_trace),
        'pi_tool_call_counts': dict(result.tool_call_counts),
        'pi_tool_sequence': list(result.tool_sequence),
        'submit_rejection_count': int(result.submit_rejection_count or 0),
    })
    for row in result.tool_trace:
        result_summary = dict(row.get('result_summary') or {})
        audits.append({
            'note': 'pi_case_agent_tool_call',
            'case_agent_mode': 'pi_case_agent',
            'tool_name': str(row.get('tool') or ''),
            'accepted': bool(result_summary.get('accepted')) if 'accepted' in result_summary else bool(row.get('ok')),
            'elapsed_ms': int(row.get('elapsed_ms') or 0),
            'result_summary': result_summary,
        })
    return audits


def _build_pi_case_agent_snapshot(result: PiCaseAgentRunResult) -> dict[str, Any]:
    final_workspace = result.final_workspace
    final_output = result.final_output
    final_verifier_result = result.final_verifier_result
    final_dossier = final_workspace.to_dossier(round_context='snapshot')
    bounded = build_bounded_case_dossier(final_dossier)
    initial_projection = _initial_projection_for_snapshot(bounded)
    request_audits = _pi_case_agent_audits(result)
    evidence_response_refs = [
        ref
        for batch in result.evidence_batches
        for req in (getattr(batch, 'request_results', []) or [])
        for ref in (getattr(req, 'response_refs', []) or [])
        if ref
    ]
    seen_detail_refs = list(dict.fromkeys([*(getattr(final_workspace, 'seen_detail_refs', []) or []), *evidence_response_refs]))
    local_span_cards = list(getattr(final_workspace, 'local_span_cards', []) or [])
    bangumi_span_cards = list(getattr(final_dossier, 'bangumi_span_cards', []) or [])
    local_coverage = compute_local_span_partition_coverage(final_workspace, None)
    compiled_plan = result.compiled_plan
    organize_recipe = result.organize_recipe
    if compiled_plan is not None:
        recipe_metrics = recipe_accounting(compiled_plan)
        recipe_assignments = list(compiled_plan.assignments or [])
        recipe_main_paths = list(compiled_plan.main_paths or [])
        recipe_covered_paths = list(compiled_plan.covered_paths or [])
        recipe_uncovered_paths = list(compiled_plan.uncovered_paths or [])
        recipe_duplicate_coverage_paths = list(compiled_plan.duplicate_coverage_paths or [])
        recipe_duplicate_target_keys = list(compiled_plan.duplicate_target_keys or [])
    else:
        recipe_assignments = []
        recipe_main_paths = [str(card.path).replace('\\', '/') for card in final_workspace.local_files if bool(getattr(card, 'is_main', False))]
        recipe_covered_paths = []
        recipe_uncovered_paths = recipe_main_paths if result.status not in {'fail_closed'} else []
        recipe_duplicate_coverage_paths = []
        recipe_duplicate_target_keys = []
        recipe_metrics = {
            'recipe_rule_count': 0,
            'main_path_count': len(recipe_main_paths),
            'matched_path_count': 0,
            'mapped_path_count': 0,
            'mapped_file_count': 0,
            'mapped_target_episode_count': 0,
            'single_file_multi_episode_count': 0,
            'excluded_path_count': 0,
            'unresolved_path_count': len(recipe_uncovered_paths),
            'uncovered_path_count': len(recipe_uncovered_paths),
            'duplicate_coverage_count': 0,
            'duplicate_target_count': 0,
            'accepted_accounting_ready': result.status == 'fail_closed',
        }
    mapping_draft_metrics = {
        'mapping_draft_row_count': 0,
        'mapping_draft_local_coverage_count': 0,
        'mapping_draft_missing_main_count': 0,
        'mapping_draft_open_count': 0,
        'mapping_draft_proposed_count': 0,
        'mapping_draft_verified_count': 0,
        'mapping_draft_unresolved_count': 0,
        'mapping_draft_patch_count': 0,
        'span_mapping_patch_count': 0,
        'candidate_comparison_count': 0,
    }
    target_span_requests = [
        req
        for batch in result.evidence_batches
        for req in (getattr(batch, 'request_results', []) or [])
        if str(getattr(req, 'request_type', '') or '').endswith(('span', 'window'))
    ]
    case_agent_error_kind = next((str(err).split('=', 1)[1] for err in result.errors if str(err).startswith('error_kind=')), '')
    local_package_analysis_audit = {
        'call_name': 'LocalPackageAnalysis',
        'skipped': True,
        'reason': 'pi_case_agent_mapping_only_path',
    }
    final_output_assignments = list(getattr(final_output, 'assignment_intents', []) or [])
    expanded_assignment_count = len(recipe_assignments) or len(final_output_assignments) or int((result.raw_runtime_result.get('expanded_assignment_count') or 0) if isinstance(result.raw_runtime_result, dict) else 0)
    canonical_snapshot = {
        'ok': bool(result.ok),
        'status': result.status,
        'case_agent_status': _canonical_case_agent_status(result),
        'case_agent_ok': bool(result.ok),
        'case_agent_mode': 'pi_case_agent',
        'mapping_only': True,
        'submit_rejection_count': int(result.submit_rejection_count or 0),
        'submit_rejection_issue_counts': {},
        'case_agent_error_kind': case_agent_error_kind,
        'product_result_kind': 'fail_closed' if result.status == 'fail_closed' else ('accepted' if result.status == 'accepted' else result.status),
        'case_id': result.case_id,
        'summary': result.summary,
        'final_action': result.final_action,
        'final_output': _json_safe(final_output) if final_output is not None else None,
        'assignment_intent_count': len(final_output_assignments),
        'bulk_assignment_intent_count': _count_bulk_assignment_intents(final_output, final_verifier_result, result),
        'expanded_assignment_count': expanded_assignment_count,
        **mapping_draft_metrics,
        'organize_recipe': _json_safe(organize_recipe) if organize_recipe is not None else None,
        'compiled_plan': _json_safe(compiled_plan) if compiled_plan is not None else None,
        'recipe_verifier_result': _json_safe(final_verifier_result) if final_verifier_result is not None else None,
        'recipe_rule_count': int(recipe_metrics.get('recipe_rule_count') or 0),
        'recipe_matched_path_count': int(recipe_metrics.get('matched_path_count') or 0),
        'recipe_mapped_path_count': int(recipe_metrics.get('mapped_path_count') or 0),
        'recipe_mapped_target_episode_count': int(recipe_metrics.get('mapped_target_episode_count') or 0),
        'recipe_single_file_multi_episode_count': int(recipe_metrics.get('single_file_multi_episode_count') or 0),
        'recipe_excluded_path_count': int(recipe_metrics.get('excluded_path_count') or 0),
        'recipe_unresolved_path_count': int(recipe_metrics.get('unresolved_path_count') or 0),
        'recipe_uncovered_path_count': int(recipe_metrics.get('uncovered_path_count') or 0),
        'recipe_duplicate_coverage_count': int(recipe_metrics.get('duplicate_coverage_count') or 0),
        'recipe_duplicate_target_count': int(recipe_metrics.get('duplicate_target_count') or 0),
        'recipe_main_paths': recipe_main_paths,
        'recipe_covered_paths': recipe_covered_paths,
        'recipe_uncovered_paths': recipe_uncovered_paths,
        'recipe_duplicate_coverage_paths': recipe_duplicate_coverage_paths,
        'recipe_duplicate_target_keys': recipe_duplicate_target_keys,
        'main_file_count': int(recipe_metrics.get('main_path_count') or len(recipe_main_paths) or 0),
        **_workspace_local_fact_summary(list(final_workspace.local_files or [])),
        'mapped_file_count': int(recipe_metrics.get('mapped_file_count') or recipe_metrics.get('mapped_path_count') or 0),
        'mapped_target_episode_count': int(recipe_metrics.get('mapped_target_episode_count') or 0),
        'single_file_multi_episode_count': int(recipe_metrics.get('single_file_multi_episode_count') or 0),
        'excluded_file_count': int(recipe_metrics.get('excluded_path_count') or 0),
        'resolved_unmapped_file_count': int(recipe_metrics.get('excluded_path_count') or 0),
        'manual_review_file_count': 0,
        'needs_more_evidence_file_count': 0,
        'unaligned_file_count': 0,
        'open_file_count': 0,
        'accounted_for_count': int(recipe_metrics.get('matched_path_count') or 0),
        'unresolved_count': int(recipe_metrics.get('unresolved_path_count') or 0) + int(recipe_metrics.get('uncovered_path_count') or 0) + int(recipe_metrics.get('duplicate_coverage_count') or 0) + int(recipe_metrics.get('duplicate_target_count') or 0),
        'accepted_accounting_ready': bool(recipe_metrics.get('accepted_accounting_ready')),
        'local_span_count': len(local_span_cards),
        'local_span_main_file_count': len(recipe_main_paths),
        'local_child_span_count': int(local_coverage['local_child_span_count']),
        'local_span_covered_main_count': int(local_coverage.get('span_covered_main_file_count', local_coverage['covered_main_file_count'])),
        'local_span_missing_main_count': int(local_coverage.get('span_missing_main_file_count', local_coverage['missing_main_file_count'])),
        'local_span_overlap_count': int(local_coverage.get('span_overlap_count', local_coverage['overlap_count'])),
        'local_span_partition_complete': bool(local_coverage.get('span_partition_complete', local_coverage['partition_complete'])),
        'bangumi_span_count': len(bangumi_span_cards),
        'detail_equivalent_target_span_count': sum(1 for card in bangumi_span_cards if bool(getattr(card, 'detail_equivalent', False))),
        'span_alignment_claim_count': 0,
        'span_rows_with_candidates': sum(1 for card in local_span_cards if list(getattr(card, 'candidate_refs', []) or [])),
        'span_rows_without_candidates': sum(1 for card in local_span_cards if not list(getattr(card, 'candidate_refs', []) or [])),
        'planned_span_request_count': sum(int(getattr(card, 'planned_span_request_count', 0) or 0) for card in local_span_cards),
        'selected_span_request_count': sum(int(getattr(card, 'selected_span_request_count', 0) or 0) for card in local_span_cards),
        'completed_span_request_count': sum(int(getattr(card, 'completed_span_request_count', 0) or 0) for card in local_span_cards),
        'recommended_target_span_request_count': len(target_span_requests),
        'actual_target_span_request_count': len(target_span_requests),
        'accepted_target_span_request_count': len([req for req in target_span_requests if bool(getattr(req, 'accepted', False))]),
        'rejected_target_span_request_count': len([req for req in target_span_requests if not bool(getattr(req, 'accepted', False))]),
        'target_span_request_count': len(target_span_requests),
        'subject_search_attempted_count': len([req for batch in result.evidence_batches for req in (getattr(batch, 'request_results', []) or []) if str(getattr(req, 'request_type', '') or '') == 'subject_search']),
        'episode_list_attempted_count': len([req for batch in result.evidence_batches for req in (getattr(batch, 'request_results', []) or []) if str(getattr(req, 'request_type', '') or '') == 'episode_list']),
        'tool_rejection_count': int(result.submit_rejection_count or 0),
        'near_turn_limit_unhealthy_count': 0,
        'stall_suspected_count': 0,
        'compact_count': 0,
        'context_soft_limit_hit_count': 0,
        'context_hard_limit_hit_count': 0,
        'final_output_assignment_count': expanded_assignment_count,
        'final_output_main_file_count': len(recipe_main_paths),
        'final_output_main_file_range': f"{recipe_main_paths[0]}..{recipe_main_paths[-1]}" if recipe_main_paths else '',
        'final_output_main_file_samples': list(recipe_main_paths[:8]),
        'contract_main_file_count': len(recipe_main_paths),
        'contract_main_file_range': f"{recipe_main_paths[0]}..{recipe_main_paths[-1]}" if recipe_main_paths else '',
        'contract_main_file_samples': list(recipe_main_paths[:8]),
        'visible_target_count': len(final_workspace.contract.visible_target_refs),
        'visible_target_range': f"{final_workspace.contract.visible_target_refs[0]}..{final_workspace.contract.visible_target_refs[-1]}" if final_workspace.contract.visible_target_refs else '',
        'visible_target_samples': list(final_workspace.contract.visible_target_refs[:8]),
        'duplicate_visible_target_refs': sorted({ref for ref in final_workspace.contract.visible_target_refs if final_workspace.contract.visible_target_refs.count(ref) > 1}),
        'visible_target_sample': _sample_cards(final_workspace.bangumi_items),
        'main_file_sample': _sample_cards(final_workspace.local_files),
        'query_card_sample': _query_card_sample(final_workspace.query_cards),
        'bounded_prompt_enabled': True,
        'bounded_payload_counts': bounded.counts,
        'bounded_payload_bytes': len(json.dumps(bounded.model_dump(mode='json'), ensure_ascii=False)),
        'initial_projection_bytes': len(json.dumps(initial_projection, ensure_ascii=False).encode('utf-8')),
        'rendered_prompt_bytes': len(json.dumps(initial_projection, ensure_ascii=False).encode('utf-8')),
        'request_body_bytes_estimate': len(json.dumps(initial_projection, ensure_ascii=False).encode('utf-8')) + len('schema') * 16,
        'detailed_visible_card_count': len(bounded.detailed_visible_cards),
        'target_overview_group_count': len(bounded.target_overview),
        'query_card_sample_count': len(bounded.query_card_sample),
        'requested_detail_ref_count': len(evidence_response_refs),
        'requested_detail_ref_sample': evidence_response_refs[:8],
        'requested_detailed_card_count': len([ref for ref in evidence_response_refs if any(card.ref == ref for card in [*final_workspace.bangumi_items, *final_workspace.local_files])]),
        'seen_detail_ref_source': 'workspace+pi_tools',
        'seen_detail_ref_sample': seen_detail_refs[:8],
        'seen_detail_ref_count': len(seen_detail_refs),
        'prompt_be_ref_occurrences': 0,
        'prompt_file_ref_occurrences': 0,
        'initial_be_ref_occurrences': len(re.findall(r'BE\d+', json.dumps(initial_projection, ensure_ascii=False))),
        'initial_file_ref_occurrences': len(re.findall(r'LF\d+', json.dumps(initial_projection, ensure_ascii=False))),
        'salience_risk_flags': bounded.salience_overview.get('risk_flags', {}),
        'salience_large_case': bool(bounded.salience_overview.get('risk_flags', {}).get('large_case', False)),
        'primary_title_cues': list(getattr(bounded, 'primary_title_cues', []) or [])[:10],
        'release_group_cues': list(getattr(bounded, 'release_group_cues', []) or [])[:10],
        'search_seed_source': 'agent_composed_query_cards',
        'final_verifier_passed': bool(getattr(final_verifier_result, 'passed', False)),
        'final_verifier_issue_count': len(getattr(final_verifier_result, 'issues', []) or []),
        'verifier_issue_count': len(getattr(final_verifier_result, 'issues', []) or []),
        'verifier_issues': [_json_safe(issue) for issue in (getattr(final_verifier_result, 'issues', []) or [])],
        'judge_round_count': len(result.judge_outputs),
        'evidence_batch_count': len(result.evidence_batches),
        'errors': list(result.errors),
        'evidence_batches': [_json_safe(batch) for batch in result.evidence_batches],
        'final_workspace': {
            'local_file_count': len(final_workspace.local_files),
            'bangumi_subject_count': len(final_workspace.bangumi_subjects),
            'bangumi_group_count': len(final_workspace.bangumi_groups),
            'bangumi_item_count': len(final_workspace.bangumi_items),
        },
        'judge_round_actions': ['submit_verdict' if result.status == 'accepted' else result.status],
        'judge_round_kinds': ['pi_final'],
        'evidence_request_count': sum(len(getattr(batch, 'request_results', []) or []) for batch in result.evidence_batches),
        'evidence_request_types': [getattr(req, 'request_type', '') for batch in result.evidence_batches for req in (getattr(batch, 'request_results', []) or []) if getattr(req, 'request_type', '')],
        'evidence_response_ref_count': len(evidence_response_refs),
        'evidence_response_ref_samples': evidence_response_refs[:8],
        'assignable_target_count': len(getattr(bounded, 'assignable_target_refs', []) or []),
        'detailed_local_file_count': len(getattr(bounded, 'detailed_local_file_cards', []) or []),
        'case_judge_configured_interface': 'pi',
        'case_judge_actual_interface': 'pi',
        'case_judge_streaming': 'unknown',
        'case_judge_request_audits': [_json_safe(a) for a in request_audits],
        'case_judge_request_audit_count': len(request_audits),
        'case_judge_request_audit_round_kinds': ['pi_tool'],
        'surface_ledger_count': int(bounded.counts.get('visible_target_count') or 0),
        'plan_status': str(getattr(getattr(final_workspace, 'plan_state', None), 'plan_status', 'idle') or 'idle'),
        'plan_completed_count': len(getattr(getattr(final_workspace, 'plan_state', None), 'completed_menu_request_ids', []) or []),
        'plan_failed_count': len(getattr(getattr(final_workspace, 'plan_state', None), 'failed_menu_request_ids', []) or []),
        'plan_selected_count': len(getattr(getattr(final_workspace, 'plan_state', None), 'selected_menu_request_ids', []) or []),
        'evidence_menu_count': len(bounded.available_detail_request_types),
        'evidence_menu_request_count': 0,
        'evidence_menu_span_request_count': 0,
        'selected_menu_request_ids': [],
        'unknown_menu_request_ids': [],
        'resolved_menu_request_count': 0,
        'action_policy_allowed': ['submit_organize_recipe_params', 'submit_organize_recipe_params_patch', 'fail_closed', 'goal_complete_after_acceptance'],
        'action_policy_disallowed': ['repo_write', 'media_mutation', 'task_record_write', 'secret_read'],
        'action_policy_final_opportunity': bool(final_output is not None and result.status in {'fail_closed', 'accepted'}),
        'issue_router_issue_counts': {'count': len(getattr(final_verifier_result, 'issues', []) or [])},
        'local_package_analysis_audit': _json_safe(local_package_analysis_audit),
        'error_kind': case_agent_error_kind,
        'pi_run_dir': str(result.run_dir),
        'pi_case_id': result.case_id,
        'pi_command': result.pi_command,
        'pi_provider': result.pi_provider,
        'pi_model': result.pi_model,
        'pi_base_url': result.pi_base_url,
        'pi_runtime_command': list(result.runtime_command or []),
        'pi_runtime_returncode': result.runtime_returncode,
        'pi_runtime_result': _json_safe(result.raw_runtime_result),
        'pi_tool_trace_count': len(result.tool_trace),
        'pi_tool_call_counts': dict(result.tool_call_counts),
        'pi_tool_sequence': list(result.tool_sequence),
    }
    if result.status == 'accepted' and compiled_plan is None:
        verdict_accounting = _verdict_assignment_accounting(final_dossier, final_output)
        if verdict_accounting:
            for key, value in verdict_accounting.items():
                if key == 'expanded_assignment_count':
                    canonical_snapshot[key] = max(int(canonical_snapshot.get(key) or 0), int(value or 0))
                else:
                    canonical_snapshot[key] = value
    if _snapshot_debug_enabled():
        canonical_snapshot['contract_main_file_refs'] = list(final_workspace.contract.main_file_refs)
        canonical_snapshot['final_output_main_file_refs'] = list(final_workspace.contract.main_file_refs)
        canonical_snapshot['visible_target_refs'] = list(final_workspace.contract.visible_target_refs)
    canonical_snapshot.update(summarize_case_agent_snapshot_refs(canonical_snapshot))
    canonical_snapshot['oversized_input_call_count'] = 0
    return canonical_snapshot


def run_local_bangumi_case_agent_mapping(*, local_evidence, bangumi_contexts: list[dict[str, object]], source_path, bangumi_client=None) -> dict[str, Any]:
    workspace = _build_workspace(
        local_evidence=local_evidence,
        bangumi_contexts=bangumi_contexts,
    )
    effective_bangumi_client = bangumi_client if bangumi_client is not None else BangumiClient()
    result = run_pi_case_agent(
        workspace=workspace,
        bangumi_client=effective_bangumi_client,
        source_path=str(source_path or getattr(local_evidence, 'source_path', '') or ''),
    )
    canonical_snapshot = _build_pi_case_agent_snapshot(result)
    return {
        'ok': bool(result.ok),
        'status': result.status,
        'summary': result.summary,
        'snapshot': canonical_snapshot,
        'result': {'status': result.status, 'ok': bool(result.ok), 'case_agent_mode': 'pi_case_agent'},
    }


def _initial_projection_for_snapshot(bounded):
    from .dossier import build_initial_compact_projection
    return build_initial_compact_projection(bounded)
