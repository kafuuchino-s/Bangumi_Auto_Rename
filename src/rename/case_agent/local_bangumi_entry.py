from __future__ import annotations

import json
import re
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
from .orchestrator import CaseAgentRunResult, run_local_bangumi_case_agent
from .mapping_draft import compute_local_span_partition_coverage, summarize_mapping_draft_coverage
from .mapping_draft import compute_mapping_draft_accounting
from .workspace import CaseEvidenceWorkspace
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
    mapped = excluded = needs = unaligned = open_count = 0
    for assignment in assignments:
        file_ref = str(getattr(assignment, 'file_ref', '') or '')
        if file_ref not in main_refs:
            continue
        counts[file_ref] = counts.get(file_ref, 0) + 1
        target_ref = str(getattr(assignment, 'target_ref', '') or '')
        if target_ref == 'UNALIGNED':
            reason = str(getattr(assignment, 'reason', '') or '')
            if reason.startswith('mapping_draft:') and ':supplemental:' in reason:
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


def build_local_bangumi_cards_view(local_evidence, bangumi_contexts: list[dict[str, object]]):
    root_name = str(getattr(local_evidence, 'root_name', '') or getattr(local_evidence, 'source_path', '') or 'local package')
    local_files: list[dict[str, Any]] = []
    filtered_files: list[dict[str, Any]] = []
    cluster_refs: dict[str, str] = {}
    cluster_members: dict[str, list[str]] = {}
    main_file_refs: list[str] = []
    supplemental_file_refs: list[str] = []

    visible_index = 1
    for source_index, file in enumerate(list(getattr(local_evidence, 'files', []) or []), start=1):
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
        if not is_main:
            filtered_files.append({
                'source_index': source_index,
                'path': relative_path or name,
                'relative_path': relative_path or name,
                'is_video': is_video,
                'is_supplemental_candidate': bool(is_supplemental),
                'rule_id': local_filter.rule_id or ('non_video' if not is_video else 'explicit_supplemental_candidate'),
                'reason_kind': local_filter.reason_kind or ('non_video_support' if not is_video else 'other_supplemental'),
                'reason': local_filter.reason or ('non-video local support file' if not is_video else 'explicit supplemental local file'),
            })
            continue

        ref = f'LF{visible_index}'
        visible_index += 1
        cluster_ref = cluster_refs.setdefault(parent_display, f'LC{len(cluster_refs) + 1}')
        cluster_members.setdefault(parent_display, []).append(ref)
        if is_main:
            main_file_refs.append(ref)
        else:
            supplemental_file_refs.append(ref)
        local_files.append({
            'ref': ref,
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
        subject_ref = f'BS{subject_index}'
        group_ref = f'BR{subject_index}'
        episodes = [item for item in (structure.get('episodes') or []) if isinstance(item, dict)]
        member_refs: list[str] = []
        bangumi_cards.append({
            'ref': subject_ref,
            'entity_ref': subject_ref,
            'subject_id': int(structure.get('subject_id') or structure.get('id') or 0),
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
            item_ref = f'BE{episode_ref_index}'
            episode_ref_index += 1
            member_refs.append(item_ref)
            bangumi_cards.append({
                'ref': item_ref,
                'item_ref': item_ref,
                'group_ref': group_ref,
                'entity_ref': subject_ref,
                'episode_id': int(episode.get('episode_id') or episode.get('id') or 0),
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
    local_files = [LocalFileCard(ref=card['ref'], path=card.get('path') or card.get('relative_path') or '', is_main=bool(card.get('is_main_video_candidate')), size_bytes=int(card.get('size_bytes') or 0), parent_display=card.get('parent_display') or '', cluster_ref=views.ref_map.source_ref_to_parent_ref.get(card['ref'], ''), label=card.get('label') or card.get('basename') or '', file_kind='video' if bool(card.get('is_main_video_candidate')) else 'unknown', related_refs=list(card.get('related_refs') or [])) for card in (views.local_source_view.get('files') or []) if isinstance(card, dict) and card.get('ref')]
    local_clusters = [LocalClusterCard(ref=card['ref'], cluster_name=card.get('display_title') or card.get('title') or '', title_cues=list(card.get('title_tokens') or []), file_refs=list(card.get('member_refs') or []), cluster_kind='mixed' if card.get('kind') in {'movie_like', 'spinoff_like'} else 'local', summary=card.get('display_title') or '') for card in (views.local_bangumi_direct_view.get('local_cluster_cards') or []) if isinstance(card, dict) and card.get('ref')]
    bangumi_cards = [card for card in (views.local_bangumi_direct_view.get('bangumi_cards') or []) if isinstance(card, dict)]
    bangumi_subjects = [BangumiSubjectCard(ref=str(card.get('ref') or card.get('entity_ref') or ''), subject_id=int(card.get('subject_id') or 0), subject_type='anime', title=card.get('title') or card.get('subject_title') or '', name=card.get('name') or '', name_cn=card.get('name_cn') or '', summary_short=card.get('summary_short') or '', eps=int(card.get('episode_count') or 0), total_episodes=int(card.get('episode_count') or 0), source_form_hint=card.get('source_form_hint') or '', relation_to_main=card.get('relation_to_main') or '', source_role=card.get('source_role') or '') for card in bangumi_cards if str(card.get('ref') or card.get('entity_ref') or '').startswith('BS')]
    bangumi_groups = [BangumiGroupCard(ref=str(card.get('ref') or card.get('group_ref') or ''), group_kind='special_group' if str(card.get('kind') or '').endswith('special') else 'season_group', member_refs_visible=list(card.get('member_refs') or []), sort_start=int((card.get('sort_range') or [0, 0])[0] if card.get('sort_range') else 0), sort_end=int((card.get('sort_range') or [0, 0])[-1] if card.get('sort_range') else 0), title_examples=list(card.get('title_examples') or []), subject_refs=[str(card.get('entity_ref') or '')] if card.get('entity_ref') else [], item_refs=list(card.get('member_refs_sample') or [])) for card in bangumi_cards if str(card.get('ref') or card.get('group_ref') or '').startswith('BR')]
    bangumi_items = [BangumiItemCard(ref=str(card.get('ref') or card.get('item_ref') or ''), item_kind='episode' if str(card.get('kind') or '') != 'special' else 'special', episode_id=int(card.get('episode_id') or 0), kind=card.get('kind') or '', type=card.get('kind') or '', sort=int(card.get('sort') or 0), ep=int(card.get('ep') or 0), subject_ref=card.get('entity_ref') or '', title=card.get('title') or '', name=card.get('name') or '', name_cn=card.get('name_cn') or '', duration=str(card.get('runtime') or ''), desc_short=card.get('summary_short') or '', synthetic=bool(card.get('synthetic')), source_form_hint=card.get('source_form_hint') or '', relation_to_main=card.get('relation_to_main') or '', parent_refs=[str(card.get('group_ref') or '')] if card.get('group_ref') else []) for card in bangumi_cards if str(card.get('ref') or card.get('item_ref') or '').startswith('BE')]
    derived_main_file_refs = list(views.ref_map.main_file_refs or views.ref_map.file_refs or [card.ref for card in local_files if card.is_main])
    query_cards = build_query_cards(local_files, local_clusters, bangumi_subjects)
    provenance_cards = [ProvenanceCard(ref='PV1', source_operation='local_bangumi_case_agent_entry', raw_response_count=len(bangumi_items), parent_refs=[card.ref for card in local_files[:2]])]
    contract = CaseContract(summary='Local->Bangumi case agent mapping workspace', expected_outcome='unknown', main_file_refs=derived_main_file_refs, supplemental_file_refs=list(views.ref_map.supplemental_file_refs or []), allowed_file_refs=list(views.ref_map.file_refs or [card.ref for card in local_files]), visible_target_refs=[card.ref for card in bangumi_items], coverage_rule='main files must be covered exactly once or fail closed', duplicate_rule='bangumi item refs must not be duplicated', support_rule='only visible bangumi cards may be referenced')
    header = CaseHeader(case_id=f'local-bangumi-{getattr(local_evidence, "source_path", "") or "mapping"}', max_rounds=_config_int('rename_local_bangumi_case_agent_max_rounds', 3), status='open')
    budget = CaseBudget(max_judge_rounds=header.max_rounds, max_evidence_batches=_config_int_at_least('rename_local_bangumi_case_agent_max_evidence_batches', 8, 8), max_issue_response_rounds=_config_int('rename_local_bangumi_case_agent_max_issue_response_rounds', 1), max_requests_per_batch=_config_int('rename_local_bangumi_case_agent_max_requests_per_batch', 8))
    local_span_cards = _build_raw_local_span_shells(local_files, contract)
    workspace = CaseEvidenceWorkspace.from_cards(header=header, budget=budget, contract=contract, local_files=local_files, local_clusters=local_clusters, local_span_cards=local_span_cards, bangumi_subjects=bangumi_subjects, bangumi_groups=bangumi_groups, bangumi_items=bangumi_items, query_cards=query_cards, provenance_cards=provenance_cards)
    filtered_files = list(getattr(views.ref_map, 'filtered_files', []) or [])
    if filtered_files:
        object.__setattr__(
            workspace,
            'judge_request_audits',
            [
                *list(getattr(workspace, 'judge_request_audits', []) or []),
                {
                    'note': 'deterministic_local_supplemental_projection',
                    'filtered_file_count': len(filtered_files),
                    'filtered_video_count': sum(1 for file in filtered_files if bool(file.get('is_video'))),
                    'filtered_file_samples': [
                        {
                            'path': str(file.get('relative_path') or file.get('path') or ''),
                            'rule_id': str(file.get('rule_id') or ''),
                            'reason_kind': str(file.get('reason_kind') or ''),
                        }
                        for file in filtered_files[:12]
                    ],
                },
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


def run_local_bangumi_case_agent_mapping(*, local_evidence, bangumi_contexts: list[dict[str, object]], ai_client, source_path, bangumi_client=None) -> dict[str, Any]:
    workspace = _build_workspace(
        local_evidence=local_evidence,
        bangumi_contexts=bangumi_contexts,
    )
    effective_bangumi_client = bangumi_client if bangumi_client is not None else BangumiClient()
    result: CaseAgentRunResult = run_local_bangumi_case_agent(workspace, ai_client, effective_bangumi_client)
    bounded = build_bounded_case_dossier(result.final_workspace)
    final_output = getattr(result, 'final_output', None)
    final_verifier_result = getattr(result, 'final_verifier_result', None)
    evidence_response_refs = [ref for batch in result.evidence_batches for req in (getattr(batch, 'request_results', []) or []) for ref in (getattr(req, 'response_refs', []) or []) if ref]
    request_audits = list(getattr(result.final_workspace, 'judge_request_audits', []) or [])
    deterministic_projection_audits = [
        audit
        for audit in request_audits
        if isinstance(audit, dict)
        and str(audit.get('note') or '') == 'deterministic_local_supplemental_projection'
    ]
    deterministic_projection_audit = deterministic_projection_audits[-1] if deterministic_projection_audits else {}
    selected_menu_request_ids = []
    unknown_menu_request_ids = []
    evidence_menu_request_ids = []
    evidence_menu_span_request_ids = []
    resolved_menu_request_count = 0
    legacy_raw_request_count = 0
    normalized_legacy_request_count = 0
    for audit in request_audits:
        if not isinstance(audit, dict):
            continue
        if not _is_case_judge_audit(audit):
            continue
        evidence_menu_request_ids.extend([str(v) for v in (audit.get('evidence_menu_request_ids') or []) if str(v)])
        evidence_menu_span_request_ids.extend([str(v) for v in (audit.get('evidence_menu_span_request_ids') or []) if str(v)])
        selected_menu_request_ids.extend([str(v) for v in (audit.get('selected_menu_request_ids') or []) if str(v)])
        unknown_menu_request_ids.extend([str(v) for v in (audit.get('unknown_menu_request_ids') or []) if str(v)])
        resolved_menu_request_count += int(audit.get('resolved_menu_request_count') or audit.get('menu_request_count') or 0)
        legacy_raw_request_count += int(audit.get('legacy_raw_request_count') or 0)
        normalized_legacy_request_count += int(audit.get('normalized_legacy_request_count') or 0)
    seen_detail_refs = list(dict.fromkeys([*(getattr(result.final_workspace, 'seen_detail_refs', []) or []), *evidence_response_refs]))
    initial_projection = _initial_projection_for_snapshot(bounded)
    request_audits = list(getattr(result.final_workspace, 'judge_request_audits', []) or [])
    local_package_analysis_audit = {
        'call_name': 'LocalPackageAnalysis',
        'skipped': True,
        'reason': 'query_composer_orchestrated_main_path',
    }
    if request_audits:
        top_config = request_audits[-1].get('configured_interface', 'unknown')
        top_actual = request_audits[-1].get('actual_interface', 'unknown')
        top_streaming = request_audits[-1].get('streaming', 'unknown')
    else:
        top_config = top_actual = top_streaming = 'unknown'
    include_full_dump = _snapshot_debug_enabled()
    case_judge_audits = [a for a in request_audits if isinstance(a, dict) and _is_case_judge_audit(a)]
    final_dossier = None
    if hasattr(result.final_workspace, 'to_dossier'):
        try:
            final_dossier = result.final_workspace.to_dossier(round_context='snapshot')
        except Exception:
            final_dossier = None
    local_span_cards = list(getattr(result.final_workspace, 'local_span_cards', []) or [])
    bangumi_span_cards = list(getattr(final_dossier, 'bangumi_span_cards', []) or []) if final_dossier is not None else list(getattr(result.final_workspace, 'bangumi_span_cards', []) or [])
    local_span_count = len(local_span_cards)
    span_rows_with_candidates = 0
    span_rows_without_candidates = 0
    planned_span_request_count = 0
    selected_span_request_count = 0
    completed_span_request_count = 0
    local_coverage = compute_local_span_partition_coverage(result.final_workspace, getattr(result.final_workspace, 'mapping_draft', None))
    draft_coverage = summarize_mapping_draft_coverage(result.final_workspace.to_dossier(round_context='snapshot'), getattr(result.final_workspace, 'mapping_draft', None) or MappingDraft()) if getattr(result.final_workspace, 'mapping_draft', None) is not None else None
    local_span_main_file_count = int(local_coverage['main_file_count'])
    for card in local_span_cards:
        candidates = list(getattr(card, 'candidate_refs', []) or [])
        if candidates:
            span_rows_with_candidates += 1
        else:
            span_rows_without_candidates += 1
        planned_span_request_count += int(getattr(card, 'planned_span_request_count', 0) or 0)
        selected_span_request_count += int(getattr(card, 'selected_span_request_count', 0) or 0)
        completed_span_request_count += int(getattr(card, 'completed_span_request_count', 0) or 0)
    local_child_span_count = int(local_coverage['local_child_span_count'])
    local_span_covered_main_count = int(local_coverage.get('span_covered_main_file_count', local_coverage['covered_main_file_count']))
    local_span_missing_main_count = int(local_coverage.get('span_missing_main_file_count', local_coverage['missing_main_file_count']))
    local_span_overlap_count = int(local_coverage.get('span_overlap_count', local_coverage['overlap_count']))
    local_span_partition_complete = bool(local_coverage.get('span_partition_complete', local_coverage['partition_complete']))
    bangumi_span_count = len(bangumi_span_cards)
    detail_equivalent_target_span_count = sum(1 for card in bangumi_span_cards if bool(getattr(card, 'detail_equivalent', False)))
    span_alignment_claim_count = len([a for a in case_judge_audits if any(str(a.get(k) or '').strip() for k in ('action_actual', 'action_expected', 'summary'))])
    bulk_assignment_intent_count = _count_bulk_assignment_intents(final_output, final_verifier_result, result)
    mapping_draft_metrics = _count_mapping_draft_metrics(result, final_output, final_verifier_result)
    accounting = compute_mapping_draft_accounting(getattr(result.final_workspace, 'mapping_draft', None), result.final_workspace) if getattr(result.final_workspace, 'mapping_draft', None) is not None else None
    expanded_assignment_count = mapping_draft_metrics['expanded_assignment_count']
    actual_target_span_request_count = len([req for batch in result.evidence_batches for req in (getattr(batch, 'request_results', []) or []) if str(getattr(req, 'request_type', '') or '').endswith('span') or str(getattr(req, 'request_type', '') or '').endswith('window')])
    recommended_target_span_request_count = len([req for batch in result.evidence_batches for req in (getattr(batch, 'request_results', []) or []) if str(getattr(req, 'request_type', '') or '').endswith('span') or str(getattr(req, 'request_type', '') or '').endswith('window')])
    accepted_target_span_request_count = actual_target_span_request_count if result.status in {'accepted', 'fail_closed'} else 0
    planning_output = getattr(result, 'planning_output', None)
    child_results = list(getattr(result, 'child_results', []) or [])
    split_child_assignment_count = sum(
        len(getattr(getattr(child, 'final_output', None), 'assignment_intents', []) or [])
        for child in child_results
    )
    split_child_main_file_count = 0
    for child in child_results:
        child_workspace = getattr(child, 'final_workspace', None)
        child_contract = getattr(child_workspace, 'contract', None)
        split_child_main_file_count += len(getattr(child_contract, 'main_file_refs', []) or [])
    canonical_snapshot = {
        'ok': bool(result.ok),
        'status': result.status,
        'case_agent_status': _canonical_case_agent_status(result),
        'case_agent_ok': bool(result.ok),
        'case_agent_error_kind': next((str(err).split('=', 1)[1] for err in result.errors if str(err).startswith('error_kind=')), ''),
        'product_result_kind': 'fail_closed' if result.status == 'fail_closed' else ('accepted' if result.status == 'accepted' else result.status),
        'case_id': result.case_id,
        'summary': result.summary,
        'final_action': result.final_action,
        'case_planning_action': str(getattr(planning_output, 'action', '') or ''),
        'split_child_case_count': len(child_results),
        'split_child_statuses': [str(getattr(child, 'status', '') or '') for child in child_results],
        'split_child_case_ids': [str(getattr(child, 'case_id', '') or '') for child in child_results],
        'split_child_assignment_count': split_child_assignment_count,
        'final_output': _json_safe(final_output) if final_output is not None else None,
        'assignment_intent_count': len(getattr(final_output, 'assignment_intents', []) or []),
        'bulk_assignment_intent_count': bulk_assignment_intent_count,
        'expanded_assignment_count': expanded_assignment_count,
        'mapping_draft_row_count': int(local_coverage['mapping_draft_row_count']),
        'mapping_draft_local_coverage_count': int(local_coverage['mapping_draft_covered_main_count']),
        'mapping_draft_missing_main_count': int(local_coverage['mapping_draft_missing_main_count']),
        **mapping_draft_metrics,
        'mapping_draft_open_count': mapping_draft_metrics['mapping_draft_open_count'],
        'mapping_draft_proposed_count': mapping_draft_metrics['mapping_draft_proposed_count'],
        'mapping_draft_verified_count': mapping_draft_metrics['mapping_draft_verified_count'],
        'mapping_draft_unresolved_count': mapping_draft_metrics['mapping_draft_unresolved_count'],
        'mapping_draft_patch_count': mapping_draft_metrics['mapping_draft_patch_count'],
        'span_mapping_patch_count': mapping_draft_metrics['span_mapping_patch_count'],
        'candidate_comparison_count': mapping_draft_metrics['candidate_comparison_count'],
        'main_file_count': int(getattr(accounting, 'main_file_count', 0) or 0),
        'mapped_file_count': int(getattr(accounting, 'mapped_file_count', 0) or 0),
        'excluded_file_count': int(getattr(accounting, 'excluded_file_count', 0) or 0),
        'needs_more_evidence_file_count': int(getattr(accounting, 'needs_more_evidence_file_count', 0) or 0),
        'unaligned_file_count': int(getattr(accounting, 'unaligned_file_count', 0) or 0),
        'open_file_count': int(getattr(accounting, 'open_file_count', 0) or 0),
        'accounted_for_count': int(getattr(accounting, 'accounted_for_count', 0) or 0),
        'unresolved_count': int(getattr(accounting, 'unresolved_count', 0) or 0),
        'accepted_accounting_ready': bool(getattr(accounting, 'accepted_accounting_ready', False)),
        'span_rows_with_candidates': span_rows_with_candidates,
        'span_rows_without_candidates': span_rows_without_candidates,
        'planned_span_request_count': planned_span_request_count,
        'selected_span_request_count': selected_span_request_count,
        'completed_span_request_count': completed_span_request_count,
        'local_child_span_count': local_child_span_count,
        'local_span_covered_main_count': local_span_covered_main_count,
        'local_span_missing_main_count': local_span_missing_main_count,
        'local_span_overlap_count': local_span_overlap_count,
        'local_span_partition_complete': local_span_partition_complete,
        'bangumi_span_count': bangumi_span_count,
        'detail_equivalent_target_span_count': detail_equivalent_target_span_count,
        'recommended_target_span_request_count': recommended_target_span_request_count,
        'actual_target_span_request_count': actual_target_span_request_count,
        'accepted_target_span_request_count': accepted_target_span_request_count,
        'target_span_request_count': actual_target_span_request_count,
        'final_output_assignment_count': len(getattr(final_output, 'assignment_intents', []) or []),
        'final_output_main_file_count': len(result.final_workspace.contract.main_file_refs),
        'final_output_main_file_range': f"{result.final_workspace.contract.main_file_refs[0]}..{result.final_workspace.contract.main_file_refs[-1]}" if result.final_workspace.contract.main_file_refs else '',
        'final_output_main_file_samples': list(result.final_workspace.contract.main_file_refs[:8]),
        'contract_main_file_count': len(result.final_workspace.contract.main_file_refs),
        'contract_main_file_range': f"{result.final_workspace.contract.main_file_refs[0]}..{result.final_workspace.contract.main_file_refs[-1]}" if result.final_workspace.contract.main_file_refs else '',
        'contract_main_file_samples': list(result.final_workspace.contract.main_file_refs[:8]),
        'visible_target_count': len(result.final_workspace.contract.visible_target_refs),
        'visible_target_range': f"{result.final_workspace.contract.visible_target_refs[0]}..{result.final_workspace.contract.visible_target_refs[-1]}" if result.final_workspace.contract.visible_target_refs else '',
        'visible_target_samples': list(result.final_workspace.contract.visible_target_refs[:8]),
        'duplicate_visible_target_refs': sorted({ref for ref in result.final_workspace.contract.visible_target_refs if result.final_workspace.contract.visible_target_refs.count(ref) > 1}),
        'visible_target_sample': _sample_cards(result.final_workspace.bangumi_items),
        'main_file_sample': _sample_cards(result.final_workspace.local_files),
        'query_card_sample': _query_card_sample(result.final_workspace.query_cards),
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
        'requested_detailed_card_count': len([ref for ref in evidence_response_refs if any(card.ref == ref for card in [*result.final_workspace.bangumi_items, *result.final_workspace.local_files])]),
        'seen_detail_ref_source': 'workspace+evidence_results',
        'seen_detail_ref_sample': seen_detail_refs[:8],
        'seen_detail_ref_count': len(seen_detail_refs),
        'prompt_be_ref_occurrences': len(re.findall(r'BE\d+', json.dumps(initial_projection, ensure_ascii=False))) if result.judge_outputs else 0,
        'prompt_file_ref_occurrences': len(re.findall(r'LF\d+', json.dumps(initial_projection, ensure_ascii=False))) if result.judge_outputs else 0,
        'initial_be_ref_occurrences': len(re.findall(r'BE\d+', json.dumps(initial_projection, ensure_ascii=False))) if result.judge_outputs else 0,
        'initial_file_ref_occurrences': len(re.findall(r'LF\d+', json.dumps(initial_projection, ensure_ascii=False))) if result.judge_outputs else 0,
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
            'local_file_count': len(result.final_workspace.local_files),
            'bangumi_subject_count': len(result.final_workspace.bangumi_subjects),
            'bangumi_group_count': len(result.final_workspace.bangumi_groups),
            'bangumi_item_count': len(result.final_workspace.bangumi_items),
        },
        'judge_round_actions': _derive_case_judge_round_actions(case_judge_audits),
        'judge_round_kinds': [str(a.get('round_kind') or '') for a in case_judge_audits] or [
            'issue_response' if i == len(result.judge_outputs) - 1 and getattr(result.final_workspace, 'verifier_issues', []) else ('evidence_rejudge' if i > 0 and result.evidence_batches else 'initial')
            for i, _ in enumerate(result.judge_outputs)
        ],
        'evidence_request_count': sum(len(getattr(batch, 'request_results', []) or []) for batch in result.evidence_batches),
        'evidence_request_types': [getattr(req, 'request_type', '') for batch in result.evidence_batches for req in (getattr(batch, 'request_results', []) or []) if getattr(req, 'request_type', '')],
        'evidence_response_ref_count': len(evidence_response_refs),
        'evidence_response_ref_samples': evidence_response_refs[:8],
        'requested_detail_ref_count': len(evidence_response_refs),
        'requested_detail_ref_sample': evidence_response_refs[:8],
        'requested_detailed_card_count': len([ref for ref in evidence_response_refs if any(card.ref == ref for card in [*result.final_workspace.bangumi_items, *result.final_workspace.local_files])]),
        'assignable_target_count': len(getattr(bounded, 'assignable_target_refs', []) or []),
        'seen_detail_ref_count': len(seen_detail_refs),
        'detailed_local_file_count': len(getattr(bounded, 'detailed_local_file_cards', []) or []),
        'case_judge_configured_interface': top_config,
        'case_judge_actual_interface': top_actual,
        'case_judge_streaming': top_streaming,
        'case_judge_request_audits': [_json_safe(a) for a in request_audits],
        'case_judge_request_audit_count': len(request_audits),
        'case_judge_request_audit_round_kinds': [str(a.get('round_kind') or '') for a in case_judge_audits],
        'surface_ledger_count': int(bounded.counts.get('visible_target_count') or 0),
        'plan_status': str(getattr(getattr(result.final_workspace, 'plan_state', None), 'plan_status', 'idle') or 'idle'),
        'plan_completed_count': len(getattr(getattr(result.final_workspace, 'plan_state', None), 'completed_menu_request_ids', []) or []),
        'plan_failed_count': len(getattr(getattr(result.final_workspace, 'plan_state', None), 'failed_menu_request_ids', []) or []),
        'plan_selected_count': len(getattr(getattr(result.final_workspace, 'plan_state', None), 'selected_menu_request_ids', []) or []),
        'evidence_menu_count': len(bounded.available_detail_request_types),
        'evidence_menu_request_count': len(evidence_menu_request_ids),
        'evidence_menu_span_request_count': len(evidence_menu_span_request_ids),
        'selected_menu_request_ids': selected_menu_request_ids,
        'unknown_menu_request_ids': unknown_menu_request_ids,
        'resolved_menu_request_count': resolved_menu_request_count,
        'legacy_raw_request_count': legacy_raw_request_count,
        'normalized_legacy_request_count': normalized_legacy_request_count,
        'action_policy_allowed': ['request_evidence', 'submit_verdict', 'fail_closed', 'issue_response'],
        'action_policy_disallowed': [],
        'action_policy_final_opportunity': bool(final_output is not None and result.status in {'fail_closed', 'accepted'}),
        'notebook_compact_counts': {'rounds': len(result.judge_outputs), 'evidence_requests': len(result.evidence_batches)},
        'issue_router_issue_counts': {'count': len(getattr(final_verifier_result, 'issues', []) or [])},
        'local_package_analysis_audit': _json_safe(local_package_analysis_audit) if local_package_analysis_audit is not None else {
            'call_name': 'LocalPackageAnalysis',
            'schema_name': 'LocalPackageAnalysis',
            'validation_key': 'search_titles',
            'input_projection_bytes': 'unavailable',
            'rendered_prompt_bytes': 'unavailable',
            'request_body_bytes_estimate': 'unavailable',
            'output_bytes_estimate': 'unavailable',
            'search_titles_count': 'unavailable',
            'title_cues_count': 'unavailable',
            'release_group_cues_count': 'unavailable',
            'cache_mode': 'unknown',
            'cache_key': 'unknown',
            'cache_event': 'unknown',
            'configured_interface': 'unknown',
            'actual_interface': 'unknown',
            'streaming': 'unknown',
            'elapsed_ms': 'unavailable',
            'error_kind': 'unavailable',
            'message': 'unavailable',
        },
        'deterministic_local_supplemental_projection': _json_safe(deterministic_projection_audit),
        'deterministic_filtered_file_count': int(deterministic_projection_audit.get('filtered_file_count') or 0) if deterministic_projection_audit else 0,
        'deterministic_filtered_video_count': int(deterministic_projection_audit.get('filtered_video_count') or 0) if deterministic_projection_audit else 0,
        'error_kind': next((str(err).split('=', 1)[1] for err in result.errors if str(err).startswith('error_kind=')), ''),
    }
    verdict_accounting = _verdict_assignment_accounting(final_dossier, final_output)
    if canonical_snapshot['status'] == 'accepted' and verdict_accounting:
        for key, value in verdict_accounting.items():
            if key == 'expanded_assignment_count':
                canonical_snapshot[key] = max(int(canonical_snapshot.get(key) or 0), int(value or 0))
            else:
                canonical_snapshot[key] = value
    if canonical_snapshot['status'] == 'accepted' and canonical_snapshot['assignment_intent_count'] > 0:
        if int(canonical_snapshot.get('contract_main_file_count') or 0) <= 0 and int(bounded.counts.get('main_file_count') or 0) > 0:
            canonical_snapshot['contract_main_file_count'] = int(bounded.counts['main_file_count'])
        if int(canonical_snapshot.get('final_output_main_file_count') or 0) <= 0 and int(bounded.counts.get('main_file_count') or 0) > 0:
            canonical_snapshot['final_output_main_file_count'] = int(bounded.counts['main_file_count'])
    if canonical_snapshot['status'] == 'accepted' and child_results:
        child_main_count = split_child_main_file_count or len(result.final_workspace.contract.main_file_refs)
        child_assignment_count = split_child_assignment_count
        canonical_snapshot['assignment_intent_count'] = child_assignment_count
        canonical_snapshot['final_output_assignment_count'] = child_assignment_count
        canonical_snapshot['expanded_assignment_count'] = max(int(canonical_snapshot.get('expanded_assignment_count') or 0), child_assignment_count)
        canonical_snapshot['main_file_count'] = child_main_count
        canonical_snapshot['mapped_file_count'] = child_assignment_count
        canonical_snapshot['accounted_for_count'] = child_main_count
        canonical_snapshot['unresolved_count'] = 0
        canonical_snapshot['needs_more_evidence_file_count'] = 0
        canonical_snapshot['unaligned_file_count'] = 0
        canonical_snapshot['open_file_count'] = 0
        canonical_snapshot['accepted_accounting_ready'] = child_main_count > 0 and child_assignment_count >= child_main_count
        canonical_snapshot['contract_main_file_count'] = child_main_count
        canonical_snapshot['final_output_main_file_count'] = child_main_count
    if include_full_dump:
        canonical_snapshot['contract_main_file_refs'] = list(result.final_workspace.contract.main_file_refs)
        canonical_snapshot['final_output_main_file_refs'] = list(result.final_workspace.contract.main_file_refs)
        canonical_snapshot['visible_target_refs'] = list(result.final_workspace.contract.visible_target_refs)
        canonical_snapshot['query_card_sample'] = _query_card_sample(result.final_workspace.query_cards)
    canonical_snapshot.update(summarize_case_agent_snapshot_refs(canonical_snapshot))
    canonical_snapshot['oversized_input_call_count'] = sum(1 for row in canonical_snapshot.get('case_judge_request_audits', []) if isinstance(row, dict) and bool(row.get('oversized_input'))) + (1 if bool(getattr(local_package_analysis_audit, 'get', lambda _k, _d=None: None)('oversized_input') if local_package_analysis_audit is not None else False) else 0)
    return {
        'ok': bool(result.ok),
        'status': result.status,
        'summary': result.summary,
        'snapshot': canonical_snapshot,
        'result': {'status': result.status, 'ok': bool(result.ok)},
    }


def _initial_projection_for_snapshot(bounded):
    from .dossier import build_initial_compact_projection
    return build_initial_compact_projection(bounded)
