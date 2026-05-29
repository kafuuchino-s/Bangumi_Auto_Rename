from __future__ import annotations

import re
import json
import shutil
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from .utils import VIDEO_SUFFIX


STREAM_SUFFIXES = {'.strm'}
SUBTITLE_EXTENSIONS = {'.ass', '.ssa', '.srt', '.sub', '.idx', '.vtt'}
TEXT_SUBTITLE_SUFFIXES = {'.ass', '.ssa', '.srt', '.vtt'}
SUBTITLE_SNIPPET_BYTE_LIMIT = 65536
SUBTITLE_SNIPPET_LINE_LIMIT = 3
SUBTITLE_SNIPPET_TEXT_LIMIT = 120
_SUBTITLE_METADATA_PREFIXES = (
    '[script info]',
    '[v4',
    '[events]',
    '[aegisub',
    'audio file:',
    'collisions:',
    'dialogue:',
    'format:',
    'last style storage:',
    'playdepth:',
    'playresx:',
    'playresy:',
    'scaledborderandshadow:',
    'script updated by:',
    'scripttype:',
    'style:',
    'synch point:',
    'timer:',
    'title:',
    'video aspect ratio:',
    'video file:',
    'video position:',
    'wrapstyle:',
    'ycbcr matrix:',
)
_SUBTITLE_BOILERPLATE_PATTERNS = (
    r'字幕[组組]',
    r'僅供[試试]看',
    r'请支持[购購]买正版',
    r'請支持[購购]買正版',
    r'\bdmguo\.org\b',
    r'\baegisub\b',
    r'\bbdrip\b',
    r'手打提供',
    r'^\s*(翻[译譯]|校[对對]|[时時]轴|後期|后期|特效)\s*[:：]',
)
KNOWN_SUBTITLE_LANGUAGE_MARKERS = {
    'chs',
    'cht',
    'cn',
    'eng',
    'en',
    'gb',
    'ja',
    'jpn',
    'jp',
    'sc',
    'tc',
    'zh',
    'zho',
}


@dataclass(frozen=True)
class LocalMissingFact:
    fact_class: str
    status: str
    reason: str
    attempted: bool
    source: str = 'local_fact_surface'
    locator_ref: str = ''


@dataclass(frozen=True)
class LocalPathFacts:
    directory_segments: list[str] = field(default_factory=list)
    parent_folder: str = ''
    basename: str = ''
    filename_stem: str = ''
    extension: str = ''
    raw_number_tokens: list[dict[str, object]] = field(default_factory=list)
    raw_marker_tokens: list[str] = field(default_factory=list)
    sibling_summary: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalContainerFacts:
    probe_status: str = 'not_attempted'
    duration_seconds: float | None = None
    container_format: str = ''
    video_stream_count: int | None = None
    audio_stream_count: int | None = None
    subtitle_stream_count: int | None = None
    chapter_count: int | None = None
    chapter_durations_seconds: list[float] = field(default_factory=list)
    resolution: str = ''
    probe_error_class: str = ''


@dataclass(frozen=True)
class LocalSubtitleFacts:
    external_subtitle_refs: list[dict[str, object]] = field(default_factory=list)
    embedded_track_summary: list[dict[str, object]] = field(default_factory=list)
    language_markers: list[str] = field(default_factory=list)
    bounded_text_snippets: list[dict[str, object]] = field(default_factory=list)
    snippet_source: str = ''


@dataclass(frozen=True)
class LocalStreamFacts:
    is_stream_file: bool = False
    stream_scheme: str = ''
    sanitized_target_summary: str = ''
    probe_limitation: str = ''


@dataclass(frozen=True)
class LocalFileFact:
    file_id: str
    relative_path: str
    path_facts: LocalPathFacts
    classification_facts: dict[str, object] = field(default_factory=dict)
    container_facts: LocalContainerFacts = field(default_factory=LocalContainerFacts)
    subtitle_facts: LocalSubtitleFacts = field(default_factory=LocalSubtitleFacts)
    stream_facts: LocalStreamFacts = field(default_factory=LocalStreamFacts)
    missing_facts: list[LocalMissingFact] = field(default_factory=list)


@dataclass(frozen=True)
class LocalFactSurface:
    root_name: str
    root_path: str
    files: list[LocalFileFact] = field(default_factory=list)
    directory_summaries: list[dict[str, object]] = field(default_factory=list)
    missing_fact_summary: dict[str, object] = field(default_factory=dict)


def build_local_fact_surface(
    local_evidence: object,
    *,
    actual_paths: Mapping[str, Path] | None = None,
    probe_media: bool = False,
) -> LocalFactSurface:
    """Build a best-effort local fact sidecar from neutral LocalEvidence.

    The surface deliberately exposes raw local facts only. Filename numbers are
    labelled raw, and unavailable facts are represented as missing facts instead
    of mapping conclusions.
    """

    files = list(getattr(local_evidence, 'files', []) or [])
    path_map = {str(key): Path(value) for key, value in dict(actual_paths or {}).items()}
    sibling_index = _sibling_index(files)
    subtitle_index = _subtitle_index(files, path_map)
    file_facts: list[LocalFileFact] = []
    for file in files:
        file_id = str(getattr(file, 'file_id', '') or '')
        relative_path = _normalize_path(getattr(file, 'relative_path', '') or getattr(file, 'name', '') or '')
        suffix = str(getattr(file, 'suffix', '') or Path(relative_path).suffix).casefold()
        actual_path = path_map.get(file_id) or path_map.get(relative_path)
        path_facts = _build_path_facts(file, relative_path, sibling_index)
        stream_facts = _build_stream_facts(actual_path, suffix)
        container_facts, container_missing = _build_container_facts(
            file=file,
            actual_path=actual_path,
            suffix=suffix,
            stream_facts=stream_facts,
            probe_media=probe_media,
        )
        subtitle_facts, subtitle_missing = _build_subtitle_facts(
            file=file,
            relative_path=relative_path,
            suffix=suffix,
            actual_paths=path_map,
            subtitle_index=subtitle_index,
            container_facts=container_facts,
        )
        classification_facts = {
            'is_video_file': bool(getattr(file, 'is_video', False)),
            'is_stream_file': stream_facts.is_stream_file,
            'is_subtitle_file': suffix in SUBTITLE_EXTENSIONS,
            'extension': suffix,
            'size_bytes': getattr(file, 'size_bytes', None),
        }
        missing_facts = [*container_missing, *subtitle_missing]
        file_facts.append(
            LocalFileFact(
                file_id=file_id,
                relative_path=relative_path,
                path_facts=path_facts,
                classification_facts=classification_facts,
                container_facts=container_facts,
                subtitle_facts=subtitle_facts,
                stream_facts=stream_facts,
                missing_facts=missing_facts,
            )
        )

    return LocalFactSurface(
        root_name=str(getattr(local_evidence, 'root_name', '') or ''),
        root_path=str(getattr(local_evidence, 'root_path', '') or ''),
        files=file_facts,
        directory_summaries=_directory_summaries(file_facts),
        missing_fact_summary=_missing_fact_summary(file_facts),
    )


def local_fact_surface_to_dict(surface: LocalFactSurface | Mapping[str, object] | None) -> dict[str, object]:
    if surface is None:
        return {}
    if is_dataclass(surface):
        return asdict(surface)
    if isinstance(surface, Mapping):
        return {str(key): _json_safe(value) for key, value in surface.items()}
    return {}


def compact_fact_surface_summary(surface: LocalFactSurface | Mapping[str, object] | None) -> dict[str, object]:
    payload = local_fact_surface_to_dict(surface)
    files = [item for item in payload.get('files') or [] if isinstance(item, dict)]
    probe_status_counts = Counter(
        str((item.get('container_facts') or {}).get('probe_status') or 'unknown')
        for item in files
    )
    missing_fact_summary = payload.get('missing_fact_summary') if isinstance(payload.get('missing_fact_summary'), dict) else {}
    files_with_external_subtitles = sum(
        1
        for item in files
        if (item.get('subtitle_facts') or {}).get('external_subtitle_refs')
    )
    stream_file_count = sum(
        1
        for item in files
        if bool((item.get('stream_facts') or {}).get('is_stream_file'))
    )
    return {
        'file_fact_count': len(files),
        'directory_fact_count': len([item for item in payload.get('directory_summaries') or [] if isinstance(item, dict)]),
        'probe_status_counts': dict(sorted(probe_status_counts.items())),
        'missing_fact_summary': missing_fact_summary,
        'files_with_external_subtitles': files_with_external_subtitles,
        'stream_file_count': stream_file_count,
    }


def compact_file_fact_for_card(file_fact: LocalFileFact | Mapping[str, object] | None, *, detail: bool = False) -> dict[str, object]:
    data = _json_safe(file_fact)
    if not isinstance(data, dict):
        return {}
    fact_field_names = ('path_facts', 'container_facts', 'subtitle_facts', 'stream_facts', 'missing_facts')
    if not any(data.get(name) for name in fact_field_names):
        return {}
    path_facts = data.get('path_facts') if isinstance(data.get('path_facts'), dict) else {}
    container = data.get('container_facts') if isinstance(data.get('container_facts'), dict) else {}
    subtitle = data.get('subtitle_facts') if isinstance(data.get('subtitle_facts'), dict) else {}
    stream = data.get('stream_facts') if isinstance(data.get('stream_facts'), dict) else {}
    missing = [item for item in data.get('missing_facts') or [] if isinstance(item, dict)]
    compact = {
        'file_id': data.get('file_id', ''),
        'relative_path': data.get('relative_path', ''),
        'path_facts': {
            'directory_segments': list(path_facts.get('directory_segments') or [])[-4:],
            'parent_folder': path_facts.get('parent_folder', ''),
            'basename': path_facts.get('basename', ''),
            'filename_stem': path_facts.get('filename_stem', ''),
            'extension': path_facts.get('extension', ''),
            'raw_number_tokens': list(path_facts.get('raw_number_tokens') or [])[:12],
            'raw_marker_tokens': list(path_facts.get('raw_marker_tokens') or [])[:12],
            'sibling_summary': _compact_sibling_summary(path_facts.get('sibling_summary')),
        },
        'container_facts': {
            'probe_status': container.get('probe_status', ''),
            'duration_seconds': container.get('duration_seconds'),
            'container_format': container.get('container_format', ''),
            'video_stream_count': container.get('video_stream_count'),
            'audio_stream_count': container.get('audio_stream_count'),
            'subtitle_stream_count': container.get('subtitle_stream_count'),
            'chapter_count': container.get('chapter_count'),
            'chapter_durations_seconds': list(container.get('chapter_durations_seconds') or [])[:12],
            'resolution': container.get('resolution', ''),
            'probe_error_class': container.get('probe_error_class', ''),
        },
        'subtitle_facts': {
            'external_subtitle_refs': list(subtitle.get('external_subtitle_refs') or [])[:6],
            'embedded_track_summary': list(subtitle.get('embedded_track_summary') or [])[:4],
            'language_markers': list(subtitle.get('language_markers') or [])[:8],
            'bounded_text_snippets': list(subtitle.get('bounded_text_snippets') or [])[:3] if detail else [],
            'snippet_source': subtitle.get('snippet_source', ''),
        },
        'stream_facts': {
            'is_stream_file': bool(stream.get('is_stream_file')),
            'stream_scheme': stream.get('stream_scheme', ''),
            'sanitized_target_summary': stream.get('sanitized_target_summary', ''),
            'probe_limitation': stream.get('probe_limitation', ''),
        },
        'missing_facts': missing[:10 if detail else 6],
    }
    return compact


def compact_file_fact_summary(file_fact: LocalFileFact | Mapping[str, object] | None) -> dict[str, object]:
    compact = compact_file_fact_for_card(file_fact, detail=False)
    if not compact:
        return {}
    container = compact.get('container_facts') if isinstance(compact.get('container_facts'), dict) else {}
    subtitle = compact.get('subtitle_facts') if isinstance(compact.get('subtitle_facts'), dict) else {}
    stream = compact.get('stream_facts') if isinstance(compact.get('stream_facts'), dict) else {}
    missing = [item for item in compact.get('missing_facts') or [] if isinstance(item, dict)]
    path_facts = compact.get('path_facts') if isinstance(compact.get('path_facts'), dict) else {}
    return {
        'probe_status': container.get('probe_status', ''),
        'duration_seconds': container.get('duration_seconds'),
        'chapter_count': container.get('chapter_count'),
        'resolution': container.get('resolution', ''),
        'external_subtitle_count': len(list(subtitle.get('external_subtitle_refs') or [])),
        'subtitle_language_markers': list(subtitle.get('language_markers') or [])[:4],
        'is_stream_file': bool(stream.get('is_stream_file')),
        'missing_fact_classes': list(dict.fromkeys(str(item.get('fact_class') or '') for item in missing if item.get('fact_class'))),
        'raw_number_token_count': len(list(path_facts.get('raw_number_tokens') or [])),
    }


def summarize_file_fact_group(file_facts: list[LocalFileFact | Mapping[str, object]]) -> dict[str, object]:
    compact_items = [compact_file_fact_for_card(item, detail=False) for item in file_facts]
    compact_items = [item for item in compact_items if item]
    probe_status_counts = Counter(
        str((item.get('container_facts') or {}).get('probe_status') or 'unknown')
        for item in compact_items
    )
    missing_classes = Counter(
        str(missing.get('fact_class') or '')
        for item in compact_items
        for missing in (item.get('missing_facts') or [])
        if isinstance(missing, dict) and str(missing.get('fact_class') or '')
    )
    durations = [
        float((item.get('container_facts') or {}).get('duration_seconds'))
        for item in compact_items
        if (item.get('container_facts') or {}).get('duration_seconds') is not None
    ]
    language_markers = list(dict.fromkeys(
        str(marker)
        for item in compact_items
        for marker in ((item.get('subtitle_facts') or {}).get('language_markers') or [])
        if str(marker)
    ))
    return {
        'file_fact_count': len(compact_items),
        'probe_status_counts': dict(sorted(probe_status_counts.items())),
        'duration_seconds_range': [min(durations), max(durations)] if durations else [],
        'duration_seconds_samples': durations[:6],
        'stream_file_count': sum(1 for item in compact_items if bool((item.get('stream_facts') or {}).get('is_stream_file'))),
        'files_with_external_subtitles': sum(1 for item in compact_items if (item.get('subtitle_facts') or {}).get('external_subtitle_refs')),
        'subtitle_language_markers': language_markers[:8],
        'missing_fact_classes': dict(sorted(missing_classes.items())),
    }


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _normalize_path(value: object) -> str:
    return str(value or '').strip().replace('\\', '/')


def _sibling_index(files: list[object]) -> dict[str, dict[str, object]]:
    by_parent: dict[str, list[str]] = defaultdict(list)
    rel_by_id: dict[str, str] = {}
    for file in files:
        file_id = str(getattr(file, 'file_id', '') or '')
        rel = _normalize_path(getattr(file, 'relative_path', '') or getattr(file, 'name', '') or '')
        rel_by_id[file_id] = rel
        parent = rel.rsplit('/', 1)[0] if '/' in rel else '.'
        basename = rel.rsplit('/', 1)[-1]
        by_parent[parent].append(basename)
    result: dict[str, dict[str, object]] = {}
    for file_id, rel in rel_by_id.items():
        parent = rel.rsplit('/', 1)[0] if '/' in rel else '.'
        siblings = by_parent.get(parent, [])
        basename = rel.rsplit('/', 1)[-1]
        index = siblings.index(basename) + 1 if basename in siblings else 0
        result[file_id] = {
            'sibling_count': len(siblings),
            'sibling_index': index,
            'sibling_basenames_sample': _boundary_sample(siblings, limit=8),
        }
    return result


def _build_path_facts(file: object, relative_path: str, sibling_index: Mapping[str, dict[str, object]]) -> LocalPathFacts:
    path = Path(relative_path)
    parts = [part for part in relative_path.split('/') if part]
    basename = parts[-1] if parts else str(getattr(file, 'name', '') or '')
    directory_segments = parts[:-1]
    stem = Path(basename).stem
    suffix = str(getattr(file, 'suffix', '') or Path(basename).suffix).casefold()
    return LocalPathFacts(
        directory_segments=directory_segments,
        parent_folder=directory_segments[-1] if directory_segments else '',
        basename=basename,
        filename_stem=stem,
        extension=suffix,
        raw_number_tokens=_raw_number_tokens(relative_path),
        raw_marker_tokens=_raw_marker_tokens(relative_path),
        sibling_summary=dict(sibling_index.get(str(getattr(file, 'file_id', '') or ''), {})),
    )


def _raw_number_tokens(value: str) -> list[dict[str, object]]:
    tokens: list[dict[str, object]] = []
    for match in re.finditer(r'(?<![A-Za-z0-9])\d{1,4}(?![A-Za-z0-9])', value):
        raw = match.group(0)
        tokens.append(
            {
                'raw_text': raw,
                'integer_value': int(raw),
                'start': match.start(),
                'source': 'raw_path_text',
            }
        )
        if len(tokens) >= 24:
            break
    return tokens


def _raw_marker_tokens(value: str) -> list[str]:
    markers: list[str] = []
    for match in re.finditer(r'\[([^\[\]]{1,80})\]|\(([^\(\)]{1,80})\)', value):
        text = re.sub(r'\s+', ' ', str(match.group(1) or match.group(2) or '')).strip()
        if text:
            markers.append(text)
        if len(markers) >= 24:
            break
    return list(dict.fromkeys(markers))


def _build_stream_facts(actual_path: Path | None, suffix: str) -> LocalStreamFacts:
    if suffix.casefold() not in STREAM_SUFFIXES:
        return LocalStreamFacts()
    target = _read_stream_target(actual_path)
    scheme = ''
    if target:
        match = re.match(r'(?i)^([a-z][a-z0-9+.-]{1,20}):', target)
        scheme = match.group(1).casefold() if match else ('local_path' if re.search(r'[\\/]', target) else 'unknown')
    return LocalStreamFacts(
        is_stream_file=True,
        stream_scheme=scheme,
        sanitized_target_summary=_sanitize_stream_target(target),
        probe_limitation='stream_file_no_local_container',
    )


def _build_container_facts(
    *,
    file: object,
    actual_path: Path | None,
    suffix: str,
    stream_facts: LocalStreamFacts,
    probe_media: bool,
) -> tuple[LocalContainerFacts, list[LocalMissingFact]]:
    file_id = str(getattr(file, 'file_id', '') or '')
    size_bytes = getattr(file, 'size_bytes', None)
    is_video = bool(getattr(file, 'is_video', False))
    if stream_facts.is_stream_file:
        facts = LocalContainerFacts(probe_status='unsupported', probe_error_class='stream_file')
        return facts, [_missing('container_facts', 'unsupported', 'stream_file', attempted=False, locator_ref=file_id)]
    if not is_video:
        facts = LocalContainerFacts(probe_status='unsupported', probe_error_class='non_video_file')
        return facts, [_missing('container_facts', 'unsupported', 'non_video_file', attempted=False, locator_ref=file_id)]
    if actual_path is None:
        facts = LocalContainerFacts(probe_status='not_attempted', probe_error_class='no_local_file_path')
        return facts, [_missing('container_facts', 'not_attempted', 'no_local_file_path', attempted=False, locator_ref=file_id)]
    if not actual_path.exists():
        facts = LocalContainerFacts(probe_status='missing_file', probe_error_class='missing_file')
        return facts, [_missing('container_facts', 'missing_file', 'file_does_not_exist', attempted=True, locator_ref=file_id)]
    try:
        actual_size = actual_path.stat().st_size
    except OSError:
        actual_size = None
    if actual_size == 0 or size_bytes == 0:
        facts = LocalContainerFacts(probe_status='probe_error', probe_error_class='zero_byte_file')
        return facts, [_missing('container_facts', 'probe_error', 'zero_byte_file', attempted=True, locator_ref=file_id)]
    if suffix.casefold() not in {item.casefold() for item in VIDEO_SUFFIX}:
        facts = LocalContainerFacts(probe_status='unsupported', probe_error_class='unsupported_extension')
        return facts, [_missing('container_facts', 'unsupported', 'unsupported_extension', attempted=False, locator_ref=file_id)]
    if not probe_media:
        facts = LocalContainerFacts(probe_status='not_attempted', container_format=suffix.lstrip('.'))
        return facts, [_missing('container_facts', 'not_attempted', 'media_probe_disabled', attempted=False, locator_ref=file_id)]
    probed, error_class = probe_media_file(actual_path)
    if not probed:
        facts = LocalContainerFacts(
            probe_status='probe_error',
            container_format=suffix.lstrip('.'),
            probe_error_class=error_class or 'probe_returned_no_metadata',
        )
        return facts, [_missing('container_facts', 'probe_error', facts.probe_error_class, attempted=True, locator_ref=file_id)]
    facts = _container_facts_from_probe(probed, suffix)
    missing: list[LocalMissingFact] = []
    if facts.duration_seconds is None:
        missing.append(_missing('duration_facts', facts.probe_status, 'duration_unavailable', attempted=True, locator_ref=file_id))
    return facts, missing


def probe_media_file(file_path: Path) -> tuple[dict[str, object] | None, str]:
    ffprobe_info, ffprobe_error = _probe_media_file_ffprobe(file_path)
    if ffprobe_info:
        return ffprobe_info, ''
    try:
        from ..ai.video_analyzer import VideoAnalyzer

        info = VideoAnalyzer.get_video_info(file_path)
    except Exception as exc:  # pragma: no cover - defensive wrapper for optional probe stack
        return None, exc.__class__.__name__
    if not info:
        return None, ffprobe_error or 'probe_returned_no_metadata'
    return dict(info), ''


def _probe_media_file_ffprobe(file_path: Path) -> tuple[dict[str, object] | None, str]:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None, 'ffprobe_not_found'
    try:
        completed = subprocess.run(
            [
                ffprobe,
                '-v',
                'error',
                '-print_format',
                'json',
                '-show_format',
                '-show_streams',
                '-show_chapters',
                str(file_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )
    except Exception as exc:  # pragma: no cover - defensive wrapper for optional probe stack
        return None, exc.__class__.__name__
    if completed.returncode != 0:
        stderr = re.sub(r'\s+', ' ', completed.stderr or '').strip()
        return None, f"ffprobe_exit_{completed.returncode}:{stderr[:120]}"
    try:
        payload = json.loads(completed.stdout or '{}')
    except json.JSONDecodeError:
        return None, 'ffprobe_invalid_json'
    streams = [item for item in payload.get('streams') or [] if isinstance(item, dict)]
    chapters = [item for item in payload.get('chapters') or [] if isinstance(item, dict)]
    format_info = payload.get('format') if isinstance(payload.get('format'), dict) else {}
    video_streams = [item for item in streams if item.get('codec_type') == 'video']
    audio_streams = [item for item in streams if item.get('codec_type') == 'audio']
    subtitle_streams = [item for item in streams if item.get('codec_type') == 'subtitle']
    first_video = video_streams[0] if video_streams else {}
    first_audio = audio_streams[0] if audio_streams else {}
    duration_seconds = _float_or_none(format_info.get('duration'))
    if duration_seconds is None:
        duration_seconds = _float_or_none(first_video.get('duration'))
    info: dict[str, object] = {
        'filename': file_path.name,
        'path': str(file_path),
        'size': _int_or_none(format_info.get('size')) or _safe_stat_size(file_path),
        'container': str(format_info.get('format_name') or file_path.suffix.lstrip('.')),
        'video_stream_count': len(video_streams),
        'audio_stream_count': len(audio_streams),
        'subtitle_stream_count': len(subtitle_streams),
        'chapter_count': len(chapters),
        'chapter_durations_seconds': _chapter_durations(chapters),
    }
    if duration_seconds is not None:
        info['duration'] = duration_seconds / 60.0
    width = _int_or_none(first_video.get('width'))
    height = _int_or_none(first_video.get('height'))
    if width:
        info['width'] = width
    if height:
        info['height'] = height
    if first_video.get('codec_name'):
        info['video_codec'] = str(first_video.get('codec_name') or '')
    if first_audio.get('codec_name'):
        info['audio_codec'] = str(first_audio.get('codec_name') or '')
    if first_audio.get('channels'):
        info['audio_channels'] = _int_or_none(first_audio.get('channels'))
    if format_info.get('bit_rate'):
        info['bitrate'] = _int_or_none(format_info.get('bit_rate'))
    return info, ''


def _container_facts_from_probe(info: Mapping[str, object], suffix: str) -> LocalContainerFacts:
    duration = info.get('duration')
    duration_seconds: float | None = None
    if isinstance(duration, (int, float)):
        duration_seconds = round(float(duration) * 60.0, 3)
    width = _int_or_none(info.get('width'))
    height = _int_or_none(info.get('height'))
    resolution = f'{width}x{height}' if width and height else ''
    return LocalContainerFacts(
        probe_status='available',
        duration_seconds=duration_seconds,
        container_format=str(info.get('container') or suffix.lstrip('.') or ''),
        video_stream_count=_int_or_none(info.get('video_stream_count')) or (1 if (width or height or info.get('video_codec') or duration_seconds is not None) else None),
        audio_stream_count=_int_or_none(info.get('audio_stream_count')) or (1 if (info.get('audio_codec') or info.get('audio_channels')) else None),
        subtitle_stream_count=_int_or_none(info.get('subtitle_stream_count')),
        chapter_count=_int_or_none(info.get('chapter_count')),
        chapter_durations_seconds=[
            round(float(item), 3)
            for item in (info.get('chapter_durations_seconds') or [])
            if _float_or_none(item) is not None
        ],
        resolution=resolution,
    )


def _chapter_durations(chapters: list[dict[str, object]]) -> list[float]:
    durations: list[float] = []
    for chapter in chapters:
        start = _float_or_none(chapter.get('start_time'))
        end = _float_or_none(chapter.get('end_time'))
        if start is None or end is None or end < start:
            continue
        durations.append(round(end - start, 3))
    return durations


def _safe_stat_size(file_path: Path) -> int | None:
    try:
        return int(file_path.stat().st_size)
    except OSError:
        return None


def _build_subtitle_facts(
    *,
    file: object,
    relative_path: str,
    suffix: str,
    actual_paths: Mapping[str, Path],
    subtitle_index: Mapping[str, dict[str, object]],
    container_facts: LocalContainerFacts,
) -> tuple[LocalSubtitleFacts, list[LocalMissingFact]]:
    file_id = str(getattr(file, 'file_id', '') or '')
    is_video = bool(getattr(file, 'is_video', False))
    is_subtitle = suffix in SUBTITLE_EXTENSIONS
    external_refs = list(subtitle_index.get(file_id, {}).get('external_subtitle_refs') or [])
    snippets: list[dict[str, object]] = []
    language_markers: list[str] = []
    for ref in external_refs:
        if not isinstance(ref, dict):
            continue
        language_markers.extend(str(marker) for marker in list(ref.get('language_markers') or []) if str(marker))
        subtitle_id = str(ref.get('file_id') or '')
        subtitle_path = actual_paths.get(subtitle_id) or actual_paths.get(str(ref.get('relative_path') or ''))
        if subtitle_path is not None:
            snippets.extend(_bounded_subtitle_snippets(subtitle_path, source_ref=subtitle_id or str(ref.get('relative_path') or '')))
    if is_subtitle:
        language_markers.extend(_language_markers_from_path(relative_path))
        actual_path = actual_paths.get(file_id) or actual_paths.get(relative_path)
        if actual_path is not None:
            snippets.extend(_bounded_subtitle_snippets(actual_path, source_ref=file_id))
    embedded: list[dict[str, object]] = []
    if container_facts.subtitle_stream_count:
        embedded.append({'track_count': container_facts.subtitle_stream_count, 'source': 'container_probe'})
    facts = LocalSubtitleFacts(
        external_subtitle_refs=external_refs,
        embedded_track_summary=embedded,
        language_markers=list(dict.fromkeys(language_markers)),
        bounded_text_snippets=_boundary_sample(snippets, limit=3),
        snippet_source='external_subtitle_file' if snippets else '',
    )
    missing: list[LocalMissingFact] = []
    if is_video and not external_refs and not embedded:
        missing.append(_missing('subtitle_facts', 'not_present', 'no_external_or_embedded_subtitles', attempted=True, locator_ref=file_id))
    return facts, missing


def _subtitle_index(files: list[object], actual_paths: Mapping[str, Path]) -> dict[str, dict[str, object]]:
    subtitles: list[tuple[str, str, str, str]] = []
    videos: list[tuple[str, str, str, str]] = []
    for file in files:
        file_id = str(getattr(file, 'file_id', '') or '')
        rel = _normalize_path(getattr(file, 'relative_path', '') or getattr(file, 'name', '') or '')
        suffix = str(getattr(file, 'suffix', '') or Path(rel).suffix).casefold()
        parent = rel.rsplit('/', 1)[0] if '/' in rel else '.'
        stem = Path(rel.rsplit('/', 1)[-1]).stem
        if suffix in SUBTITLE_EXTENSIONS:
            subtitles.append((file_id, rel, parent, stem))
        elif bool(getattr(file, 'is_video', False)):
            videos.append((file_id, rel, parent, stem))
    result: dict[str, dict[str, object]] = defaultdict(lambda: {'external_subtitle_refs': []})
    for video_id, _video_rel, video_parent, video_stem in videos:
        for subtitle_id, subtitle_rel, subtitle_parent, subtitle_stem in subtitles:
            if video_parent != subtitle_parent:
                continue
            if not _subtitle_matches_video(video_stem, subtitle_stem):
                continue
            result[video_id]['external_subtitle_refs'].append(
                {
                    'file_id': subtitle_id,
                    'relative_path': subtitle_rel,
                    'language_markers': _language_markers_from_path(subtitle_rel),
                    'snippet_available': (actual_paths.get(subtitle_id) or actual_paths.get(subtitle_rel)) is not None,
                }
            )
    return result


def _subtitle_matches_video(video_stem: str, subtitle_stem: str) -> bool:
    video = video_stem.casefold()
    subtitle = subtitle_stem.casefold()
    return subtitle == video or subtitle.startswith(f'{video}.') or subtitle.startswith(f'{video} ') or subtitle.startswith(f'{video}_')


def _language_markers_from_path(relative_path: str) -> list[str]:
    stem = Path(relative_path.rsplit('/', 1)[-1]).stem
    tokens = [item.casefold() for item in re.split(r'[.\s_\-\[\]()]+', stem) if item]
    return list(dict.fromkeys(token for token in tokens if token in KNOWN_SUBTITLE_LANGUAGE_MARKERS))


def _bounded_subtitle_snippets(path: Path, *, source_ref: str) -> list[dict[str, object]]:
    if path.suffix.casefold() not in TEXT_SUBTITLE_SUFFIXES:
        return []
    try:
        raw = path.read_bytes()[:SUBTITLE_SNIPPET_BYTE_LIMIT]
    except OSError:
        return []
    text = _decode_subtitle_text(raw)
    snippets: list[dict[str, object]] = []
    for cleaned in _subtitle_body_lines(text):
        snippets.append({'source_ref': source_ref, 'text': cleaned[:SUBTITLE_SNIPPET_TEXT_LIMIT]})
        if len(snippets) >= SUBTITLE_SNIPPET_LINE_LIMIT:
            break
    return snippets


def _decode_subtitle_text(raw: bytes) -> str:
    if not raw:
        return ''
    bom_encodings = (
        (b'\xef\xbb\xbf', 'utf-8-sig'),
        (b'\xff\xfe', 'utf-16'),
        (b'\xfe\xff', 'utf-16'),
    )
    for marker, encoding in bom_encodings:
        if raw.startswith(marker):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                return raw.decode(encoding, errors='ignore')

    candidates: list[tuple[float, str]] = []
    encodings = ['utf-8-sig', 'gb18030']
    if _looks_like_utf16_without_bom(raw):
        encodings.extend(['utf-16-le', 'utf-16-be'])
    encodings.append('latin1')
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        candidates.append((_subtitle_decode_quality(text), text))
    if not candidates:
        return raw.decode('utf-8', errors='replace')
    return max(candidates, key=lambda item: item[0])[1]


def _subtitle_body_lines(text: str) -> list[str]:
    dialogue_lines: list[str] = []
    fallback_lines: list[str] = []
    for line in text.splitlines():
        raw = str(line or '').strip()
        if not raw:
            continue
        if raw.casefold().startswith('dialogue:'):
            cleaned = _clean_subtitle_line(raw)
            if cleaned:
                dialogue_lines.append(cleaned)
            continue
        cleaned = _clean_subtitle_line(raw)
        if cleaned:
            fallback_lines.append(cleaned)
    return dialogue_lines or fallback_lines


def _looks_like_utf16_without_bom(raw: bytes) -> bool:
    if len(raw) < 16:
        return False
    sample = raw[: min(len(raw), 512)]
    even_nuls = sample[0::2].count(0)
    odd_nuls = sample[1::2].count(0)
    pairs = max(1, len(sample) // 2)
    return (even_nuls / pairs) > 0.2 or (odd_nuls / pairs) > 0.2


def _subtitle_decode_quality(text: str) -> float:
    if not text:
        return -1000.0
    length = max(1, len(text))
    lower = text.casefold()
    signal = 0.0
    for marker in (
        '[script info]',
        '[v4+ styles]',
        'dialogue:',
        'format:',
        'style:',
        'webvtt',
        '-->',
    ):
        if marker in lower:
            signal += 20.0
    printable = sum(1 for char in text if char.isprintable() or char in '\r\n\t')
    ascii_letters = sum(1 for char in text if char.isascii() and (char.isalpha() or char in '[]:,-.>'))
    penalties = (
        text.count('\ufffd') * 30.0
        + text.count('\x00') * 20.0
        + sum(1 for char in text if unicodedata.category(char) in {'Cc', 'Cs'} and char not in '\r\n\t') * 8.0
    )
    return signal + (printable / length) * 10.0 + (ascii_letters / length) * 5.0 - penalties


def _clean_subtitle_line(line: str) -> str:
    text = str(line or '').replace('\ufeff', '').replace('ï»¿', '')
    text = text.replace('\\N', ' ').replace('\\n', ' ')
    text = re.sub(r'\{[^{}]*\}', ' ', text)
    text = re.sub(r'<[^<>]*>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return ''
    lower = text.casefold()
    if text.startswith(';'):
        return ''
    if re.fullmatch(r'\d+', text):
        return ''
    if lower.startswith(_SUBTITLE_METADATA_PREFIXES):
        if lower.startswith('dialogue:'):
            parts = text.split(',', 9)
            text = parts[-1].strip() if len(parts) >= 10 else ''
        else:
            return ''
    if re.search(r'\d{1,2}:\d{2}:\d{2}', text):
        return ''
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _SUBTITLE_BOILERPLATE_PATTERNS):
        return ''
    return text[:120]


def _directory_summaries(file_facts: list[LocalFileFact]) -> list[dict[str, object]]:
    groups: dict[str, list[LocalFileFact]] = defaultdict(list)
    for fact in file_facts:
        parent = '/'.join(fact.path_facts.directory_segments) or '.'
        groups[parent].append(fact)
    summaries: list[dict[str, object]] = []
    for parent, members in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        basenames = [member.path_facts.basename for member in members]
        summaries.append(
            {
                'directory': parent,
                'file_count': len(members),
                'video_count': sum(1 for item in members if bool(item.classification_facts.get('is_video_file'))),
                'subtitle_count': sum(1 for item in members if bool(item.classification_facts.get('is_subtitle_file'))),
                'stream_count': sum(1 for item in members if item.stream_facts.is_stream_file),
                'sample_basenames': _boundary_sample(basenames, limit=6),
            }
        )
    return summaries


def _missing_fact_summary(file_facts: list[LocalFileFact]) -> dict[str, object]:
    by_class = Counter(missing.fact_class for file in file_facts for missing in file.missing_facts)
    by_status = Counter(missing.status for file in file_facts for missing in file.missing_facts)
    by_reason = Counter(missing.reason for file in file_facts for missing in file.missing_facts)
    return {
        'by_class': dict(sorted(by_class.items())),
        'by_status': dict(sorted(by_status.items())),
        'by_reason': dict(sorted(by_reason.items())),
    }


def _missing(fact_class: str, status: str, reason: str, *, attempted: bool, locator_ref: str) -> LocalMissingFact:
    return LocalMissingFact(
        fact_class=fact_class,
        status=status,
        reason=reason,
        attempted=attempted,
        locator_ref=locator_ref,
    )


def _read_stream_target(actual_path: Path | None) -> str:
    if actual_path is None or not actual_path.exists() or not actual_path.is_file():
        return ''
    try:
        return actual_path.read_text(encoding='utf-8', errors='replace').splitlines()[0].strip()
    except OSError:
        return ''


def _sanitize_stream_target(target: str) -> str:
    value = str(target or '').strip()
    if not value:
        return ''
    value = re.sub(r'(?i)(token|key|pass|password|auth|signature|sig|expires)=([^&\s]+)', r'\1=<redacted>', value)
    if len(value) > 180:
        value = value[:177] + '...'
    return value


def _compact_sibling_summary(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {
        'sibling_count': int(value.get('sibling_count') or 0),
        'sibling_index': int(value.get('sibling_index') or 0),
        'sibling_basenames_sample': list(value.get('sibling_basenames_sample') or [])[:8],
    }


def _boundary_sample(values: list[Any], *, limit: int) -> list[Any]:
    if len(values) <= limit:
        return list(values)
    half = max(1, limit // 2)
    return [*values[:half], *values[-half:]]


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
