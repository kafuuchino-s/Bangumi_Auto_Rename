from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any


def _normalize_path(text: object) -> str:
    return str(text or '').strip().replace('\\', '/')


def _basename(text: object) -> str:
    path = _normalize_path(text)
    return path.rsplit('/', 1)[-1] if path else ''


def _parent(text: object) -> str:
    path = _normalize_path(text)
    return path.rsplit('/', 1)[0] if '/' in path else ''


def _safe_json_size_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode('utf-8'))


def _sample_unique(values: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def build_local_package_projection(local_evidence_summary: dict[str, Any] | None, *, hard_limit_bytes: int = 75_000) -> dict[str, Any]:
    summary = dict(local_evidence_summary or {})
    root_name = str(summary.get('root_name') or '').strip()
    root_path = str(summary.get('root_path') or '').strip()
    files = [dict(item) for item in (summary.get('files') or []) if isinstance(item, dict)]
    directory_structure = [str(item) for item in (summary.get('directory_structure') or []) if str(item).strip()]

    video_files = [item for item in files if item.get('is_video', True)]
    main_files = [item for item in video_files if item.get('is_main_video_candidate', True)]
    subtitle_files = [item for item in files if str(item.get('name') or item.get('relative_path') or '').lower().endswith(('.srt', '.ass', '.ssa', '.vtt', '.sub'))]
    support_files = [item for item in files if item not in video_files and item not in subtitle_files]

    ext_counts = Counter((str(item.get('suffix') or _basename(item.get('relative_path'))).rsplit('.', 1)[-1].lower() if '.' in str(item.get('relative_path') or item.get('name') or '') else str(item.get('suffix') or '').lstrip('.').lower()) for item in files)
    media_counts = Counter({'main': len(main_files), 'subtitle': len(subtitle_files), 'support': len(support_files)})

    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        rel = _normalize_path(item.get('relative_path'))
        parent = _parent(rel)
        clusters[parent].append(item)

    directory_clusters = []
    for parent, members in sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        basenames = [_basename(member.get('relative_path') or member.get('name')) for member in members]
        directory_clusters.append({
            'directory': parent or '.',
            'count': len(members),
            'range': [basenames[0], basenames[-1]] if basenames else [],
            'sample_basenames': _sample_unique(basenames, 3),
        })

    file_entries = [
        {
            'relative_path': _normalize_path(item.get('relative_path')),
            'name': str(item.get('name') or ''),
            'is_main_video_candidate': bool(item.get('is_main_video_candidate')),
            'is_supplemental_candidate': bool(item.get('is_supplemental_candidate')),
            'suffix': str(item.get('suffix') or ''),
        }
        for item in files
    ]

    first_samples = file_entries[:5]
    last_samples = file_entries[-5:] if len(file_entries) > 5 else []
    middle_index = len(file_entries) // 2
    middle_samples = file_entries[max(0, middle_index - 2): middle_index + 3]

    per_directory_samples = []
    for cluster in directory_clusters[:8]:
        directory = cluster['directory']
        members = [item for item in file_entries if (_parent(item['relative_path']) or '.') == directory]
        sample = members[:2]
        per_directory_samples.append({'directory': directory, 'samples': sample})

    raw_path_text_samples: list[str] = []
    for item in files:
        for cue in (_parent(item.get('relative_path')), _basename(item.get('relative_path'))):
            if cue and cue not in raw_path_text_samples:
                raw_path_text_samples.append(cue)
    raw_bracket_text_samples = []
    for item in files:
        text = f"{item.get('name') or ''} {item.get('relative_path') or ''}"
        raw_bracket_text_samples.extend(re.findall(r'\[[^[\]]{2,80}\]', text))
    raw_bracket_text_samples = _sample_unique(raw_bracket_text_samples, 8)

    projection: dict[str, Any] = {
        'root_name': root_name,
        'root_path': root_path,
        'file_count': len(files),
        'media_counts': dict(media_counts),
        'extension_counts': dict(ext_counts),
        'directory_structure': directory_structure[:40],
        'directory_cluster_summary': directory_clusters[:20],
        'representative_samples': {
            'first': first_samples,
            'last': last_samples,
            'middle': middle_samples,
            'per_directory': per_directory_samples,
        },
        'raw_main_file_order_summary': {
            'count': len(main_files),
            'sample_basenames': _sample_unique([
                _basename(item.get('relative_path') or item.get('name'))
                for item in main_files
            ], 12),
            'note': 'raw filenames only; ordinal and episode-like tokens are interpreted by LocalStructureAgent',
        },
        'raw_bracket_text_samples': raw_bracket_text_samples,
        'raw_path_text_samples': raw_path_text_samples[:20],
        'projection_limited': False,
        'lpa_projection_truncated': False,
    }

    projection['projection_bytes'] = _safe_json_size_bytes(projection)

    rendered = _safe_json_size_bytes(projection)
    if rendered > hard_limit_bytes:
        projection['representative_samples']['middle'] = []
        projection['representative_samples']['per_directory'] = projection['representative_samples']['per_directory'][:4]
        projection['directory_cluster_summary'] = projection['directory_cluster_summary'][:10]
        projection['raw_main_file_order_summary']['sample_basenames'] = projection['raw_main_file_order_summary']['sample_basenames'][:4]
        projection['raw_path_text_samples'] = projection['raw_path_text_samples'][:10]
        projection['raw_bracket_text_samples'] = projection['raw_bracket_text_samples'][:5]
        projection['projection_limited'] = True
        projection['lpa_projection_truncated'] = True
        rendered = _safe_json_size_bytes(projection)
        if rendered > hard_limit_bytes:
            projection = {
                'root_name': root_name,
                'root_path': root_path,
                'file_count': len(files),
                'media_counts': dict(media_counts),
                'extension_counts': dict(ext_counts),
                'directory_cluster_summary': directory_clusters[:5],
                'representative_samples': {
                    'first': first_samples[:2],
                    'last': last_samples[:2],
                },
                'raw_main_file_order_summary': {
                    'count': len(main_files),
                    'sample_basenames': _sample_unique([
                        _basename(item.get('relative_path') or item.get('name'))
                        for item in main_files
                    ], 4),
                    'note': 'raw filenames only; ordinal and episode-like tokens are interpreted by LocalStructureAgent',
                },
                'raw_bracket_text_samples': raw_bracket_text_samples[:3],
                'raw_path_text_samples': raw_path_text_samples[:5],
                'projection_limited': True,
                'lpa_projection_truncated': True,
            }
    projection['projection_bytes'] = _safe_json_size_bytes(projection)
    return projection
