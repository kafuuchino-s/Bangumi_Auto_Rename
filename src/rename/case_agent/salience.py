from __future__ import annotations

import re
from collections import Counter, defaultdict

from .models import BangumiItemCard, CaseDossier


def _dedupe(values):
    out = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _extract_release_group(text: str) -> str:
    raw = str(text or '').strip()
    match = re.match(r'^\[(?P<group>[^\]]+)\]\s*(?P<title>.+)$', raw)
    return match.group('group').strip() if match else ''


def _sample_refs(values: list[str], *, limit: int = 20) -> list[str]:
    values = [value for value in values if value]
    if len(values) <= limit:
        return list(values)
    half = max(1, limit // 2)
    return _dedupe([*values[:half], *values[-half:]])


def build_salience_overview(dossier: CaseDossier) -> dict[str, object]:
    local_files = list(dossier.local_files)
    bangumi_items = list(dossier.bangumi_items)
    main_files = [card for card in local_files if card.ref in dossier.contract.main_file_refs or getattr(card, 'is_main', False)]
    supplemental_files = [card for card in local_files if card.ref not in {file.ref for file in main_files}]
    local_file_count = len(local_files)
    target_count = len(bangumi_items)
    large_case = local_file_count >= 100 or target_count >= 200

    parent_dirs = Counter(
        (
            card.parent_display
            or card.path.rsplit('\\', 1)[0].rsplit('/', 1)[0]
            if '/' in card.path or '\\' in card.path
            else ''
        )
        for card in local_files
    )
    release_groups = Counter(
        _extract_release_group(card.parent_display or card.path)
        for card in local_files
        if _extract_release_group(card.parent_display or card.path)
    )

    boundaries = [main_files[0].ref, main_files[-1].ref] if main_files else []
    main_ref_samples = _sample_refs([card.ref for card in main_files], limit=20)

    target_groups: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            'count': 0,
            'sort_range': [0, 0],
            'ep_range': [0, 0],
            'sample_refs': [],
            'subject_ref': '',
            'kind': '',
            'source_form_hint': '',
        }
    )
    duplicate_targets = [ref for ref, count in Counter(card.ref for card in bangumi_items).items() if count > 1]
    synthetic_target_count = sum(1 for card in bangumi_items if getattr(card, 'synthetic', False))
    for card in bangumi_items:
        key = card.subject_ref or card.source_form_hint or card.item_kind or card.kind or 'unknown'
        group = target_groups[key]
        group['count'] += 1
        group['subject_ref'] = card.subject_ref or group['subject_ref']
        group['kind'] = card.item_kind or card.kind or group['kind']
        group['source_form_hint'] = card.source_form_hint or group['source_form_hint']
        if not group['sort_range'][0] or card.sort < group['sort_range'][0]:
            group['sort_range'][0] = card.sort
        if card.sort > group['sort_range'][1]:
            group['sort_range'][1] = card.sort
        if not group['ep_range'][0] or card.ep < group['ep_range'][0]:
            group['ep_range'][0] = card.ep
        if card.ep > group['ep_range'][1]:
            group['ep_range'][1] = card.ep
        if len(group['sample_refs']) < 2:
            group['sample_refs'].append(card.ref)

    detail_card_count = len(dossier.detailed_card_refs)
    primary_title_cues = _dedupe([cue for card in dossier.local_clusters for cue in getattr(card, 'title_cues', [])])
    risk_flags = {
        'large_case': large_case,
        'target_surface_large': target_count >= 300,
        'insufficient_detail_cards': detail_card_count < min(10, target_count),
        'context_budget_risk': local_file_count + target_count > 250,
    }

    target_density = target_count / max(1, len({card.subject_ref or card.source_form_hint or card.kind or card.item_kind for card in bangumi_items}))

    return {
        'local': {
            'main_file_count': len(main_files),
            'supplemental_file_count': len(supplemental_files),
            'path_cluster_count': len(parent_dirs),
            'path_cluster_distribution': parent_dirs.most_common(10),
            'release_group_distribution': release_groups.most_common(10),
            'title_cue_distribution': Counter(primary_title_cues).most_common(10),
            'raw_order_summary': {
                'count': len(main_files),
                'boundary_file_refs': boundaries,
                'sample_refs': main_ref_samples,
                'note': 'raw local order only; filename numbering is interpreted by LocalStructureAgent',
            },
            'boundary_file_refs': boundaries,
        },
        'bangumi': {
            'subject_count': len({card.subject_ref for card in bangumi_items if card.subject_ref}),
            'target_count': target_count,
            'target_groups': list(target_groups.values()),
            'synthetic_target_count': synthetic_target_count,
            'duplicate_target_refs': duplicate_targets,
            'target_density': target_density,
            'large_target_surface': target_count >= 300,
        },
        'risk_flags': risk_flags,
    }
