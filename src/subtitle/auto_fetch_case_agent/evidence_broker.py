"""字幕自动抓取 Case Agent 证据 broker。

从 ``task_data`` / ``record_data`` 抽取固定层事实：
- ``ScanScopeCard``：扫描作用域（series / movie / task）。
- ``MissingVideoCard``：缺失 sidecar 字幕的目标视频，含 ``source_video``
  （重命名前 local 原始文件名，来自 record 的 key），与字幕导入
  ``SubtitleTargetVideoCard.source_video`` 同口径。
- ``SearchKeywordCard``：确定性关键词（tmdb_name / name / original_name / 源目录
  标题变体），AI 扩词后续由 entry 追加 ``source='ai_expansion'`` 卡。

不直接读 TMDB / Bangumi——auto_fetch 的"目标空间"是已落盘的本地视频缺失字幕
清单，合法空间由 record 决定。

scan_scope / missing_videos 的扫描逻辑复用 ``auto_fetch.SubtitleAutoFetcher``
的现有实现（``_resolve_scan_scope`` / ``_collect_videos_missing_subtitles``），
本 broker 只负责把扫描结果翻译成事实卡，不重写扫描。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .models import (
    CandidateCard,
    CandidateLinkCard,
    MissingVideoCard,
    ScanScopeCard,
    SearchKeywordCard,
    ThreadPackageCard,
)

if TYPE_CHECKING:
    from ..auto_fetch import SubtitleAutoFetcher
    from ..providers.base import SubtitleCandidate, SubtitleThreadPackage


def build_scan_scope_card(
    scan_scope: Mapping[str, object],
) -> ScanScopeCard:
    """把 ``_resolve_scan_scope`` 的 dict 输出翻译成事实卡。"""
    scope_type = str(scan_scope.get('type') or 'task').strip()
    if scope_type not in ('series', 'movie', 'task'):
        scope_type = 'task'
    return ScanScopeCard(
        scope_type=scope_type,  # type: ignore[arg-type]
        root=str(scan_scope.get('root') or ''),
        source=str(scan_scope.get('source') or ''),
    )


def build_missing_video_cards(
    *,
    task_data: Mapping[str, object],
    record_data: Mapping[str, object],
    missing_videos: list[Path],
) -> list[MissingVideoCard]:
    """把缺失字幕视频列表翻译成事实卡。

    ``missing_videos`` 是目路径（重命名后）。``source_video`` 通过反查
    ``record_data``（key=local 源路径, value=目标路径）得到——目标路径名匹配
    value 的 basename 即视为同一条 record，取 key 的 basename 作 source_video。
    """
    # target_name -> source_name（record key basename），与 processor 同口径
    source_index: dict[str, str] = {}
    for source, target in record_data.items():
        if not isinstance(target, str) or not isinstance(source, str):
            continue
        if not source:
            continue
        target_name = Path(target).name
        if not target_name:
            continue
        # setdefault：一个 target_name 只记第一个 source（与 processor 一致）
        source_name = Path(source).name
        if source_name and source_name not in source_index.values():
            source_index.setdefault(target_name, source_name)

    task_uuid = str(task_data.get('uuid') or '')
    task_title = str(task_data.get('name') or task_data.get('tmdb_name') or '')
    is_movie = bool(task_data.get('is_movie', False))
    season_value = task_data.get('season_id')
    season = season_value if isinstance(season_value, int) else None
    # 方向 A：Bangumi subject 名（重命名落盘字段，auto_fetch 搜索词来源）
    bgm_subject_name = str(task_data.get('bgm_subject_name') or '')
    bgm_subject_name_cn = str(task_data.get('bgm_subject_name_cn') or '')
    # 多季覆盖：per-video BGM subject 映射 + 每 subject name/name_cn。
    # 重命名链路落盘时写（process.py::_collect_bgm_subject_names），旧 task 无此
    # 字段时为空，回退 task 级主体单值 bgm_subject_name。
    video_subject_map = task_data.get('bgm_video_subject_map') or {}
    if not isinstance(video_subject_map, Mapping):
        video_subject_map = {}
    bgm_subjects_raw = task_data.get('bgm_subjects') or []
    subject_meta: dict[int, dict[str, str]] = {}
    if isinstance(bgm_subjects_raw, list):
        for entry in bgm_subjects_raw:
            if not isinstance(entry, Mapping):
                continue
            sid = entry.get('id')
            if sid is None:
                continue
            try:
                sid_int = int(sid)
            except (TypeError, ValueError):
                continue
            subject_meta[sid_int] = {
                'name': str(entry.get('name') or ''),
                'name_cn': str(entry.get('name_cn') or ''),
                'media_kind': str(entry.get('media_kind') or ''),
            }
    # 用户字幕语言偏好（auto_fetch 主进程塞入 task_context 的
    # subtitle_auto_fetch_preferred_language，默认 zh-CN）。Pi 据此抉择简繁。
    preferred_language = str(
        task_data.get('subtitle_auto_fetch_preferred_language') or ''
    )

    cards: list[MissingVideoCard] = []
    for video_path in missing_videos:
        video_name = video_path.name
        # per-video subject：按 video basename 查映射，再查 subject name/name_cn
        sid = 0
        s_name = ''
        s_name_cn = ''
        if video_name in video_subject_map:
            try:
                sid = int(video_subject_map[video_name] or 0)
            except (TypeError, ValueError):
                sid = 0
            meta = subject_meta.get(sid) or {}
            s_name = meta.get('name', '')
            s_name_cn = meta.get('name_cn', '')
        cards.append(
            MissingVideoCard(
                ref='',  # 由 workspace 分配
                task_uuid=task_uuid,
                video=video_name,
                target_path=str(video_path),
                source_video=source_index.get(video_name, ''),
                task_title=task_title,
                season=season if not is_movie else None,
                is_movie=is_movie,
                bgm_subject_name=bgm_subject_name,
                bgm_subject_name_cn=bgm_subject_name_cn,
                bangumi_subject_id=sid,
                subject_name=s_name,
                subject_name_cn=s_name_cn,
                preferred_language=preferred_language,
            )
        )
    return cards


def build_deterministic_keyword_cards(
    keywords: list[str],
) -> list[SearchKeywordCard]:
    """把确定性关键词列表翻译成事实卡（source='deterministic'）。"""
    cards: list[SearchKeywordCard] = []
    for keyword in keywords:
        text = str(keyword or '').strip()
        if not text:
            continue
        cards.append(SearchKeywordCard(ref='', keyword=text, source='deterministic'))
    return cards


def collect_missing_videos(
    fetcher: "SubtitleAutoFetcher",
    task_data: Mapping[str, object],
    record_data: Mapping[str, object],
) -> tuple[dict[str, object], list[Path]]:
    """复用 fetcher 现有扫描逻辑，返回 (scan_scope, missing_videos)。

    薄封装，避免在 broker 里重写 ``_resolve_scan_scope`` /
    ``_collect_videos_missing_subtitles``——扫描口径以 auto_fetch 现有实现为准。
    """
    scan_scope = fetcher._resolve_scan_scope(task_data, record_data)  # type: ignore[attr-defined]
    missing_videos = fetcher._collect_videos_missing_subtitles(scan_scope, record_data)  # type: ignore[attr-defined]
    return scan_scope, missing_videos


# ---------------------------------------------------------------------------
# provider -> 固定层卡片适配（事实注入，AI 不可改）
# ---------------------------------------------------------------------------

def candidate_card_from_provider(
    candidate: "SubtitleCandidate",
) -> CandidateCard:
    """把 provider 的 ``SubtitleCandidate`` 翻译成固定层 ``CandidateCard``。

    ref 由 ``workspace.add_candidate`` 分配，这里置空。楼包同步翻译。
    ``has_downloadable_attachment`` 由 attachment_urls/external_urls 中可下载
    后缀或 thread_packages 含可下载链接决定——与 provider ``_pick_download_url``
    口径一致，但这里只做事实标记，不选具体 URL。
    """
    packages = [package_card_from_provider(pkg) for pkg in candidate.thread_packages]
    has_downloadable = bool(candidate.attachment_urls) or _has_external_download_url(
        candidate.external_urls
    ) or any(pkg.has_downloadable_link for pkg in packages)
    return CandidateCard(
        ref='',
        title=candidate.title,
        detail_url=candidate.detail_url,
        snippet=str(candidate.snippet or ''),
        source=candidate.source,
        pages_scanned=int(candidate.pages_scanned or 0),
        pagination_truncated=bool(candidate.pagination_truncated),
        packages=packages,
        has_downloadable_attachment=has_downloadable,
    )


def package_card_from_provider(
    package: "SubtitleThreadPackage",
) -> ThreadPackageCard:
    """把 provider 的 ``SubtitleThreadPackage`` 翻译成固定层 ``ThreadPackageCard``。"""
    links = [
        CandidateLinkCard(
            url=link.url,
            kind='attachment' if link.kind == 'attachment' else 'external',  # type: ignore[arg-type]
            label=link.label,
            filename_hint=link.filename_hint,
            is_direct_download=bool(link.is_direct_download),
        )
        for link in package.links
    ]
    return ThreadPackageCard(
        ref='',
        candidate_ref='',
        package_id=str(package.package_id or ''),
        page_number=int(package.page_number or 1),
        floor_label=str(package.floor_label or ''),
        post_author=str(package.post_author or ''),
        post_time=str(package.post_time or ''),
        post_text=str(package.post_text or ''),
        context_text=str(package.context_text or ''),
        has_direct_download=bool(package.has_direct_download),
        package_flags=list(package.package_flags or []),
        links=links,
    )


def _has_external_download_url(external_urls: Any) -> bool:
    """external_urls 中是否含可下载后缀（archive / subtitle）。"""
    from ..extractor import SUBTITLE_EXTENSIONS

    archive_suffixes = {'.zip', '.rar', '.7z'}
    if not external_urls:
        return False
    for url in external_urls:
        text = str(url or '').split('?')[0].lower()
        suffix = text.rsplit('.', 1)[-1] if '.' in text else ''
        ext = f'.{suffix}' if suffix else ''
        if ext in archive_suffixes or ext in SUBTITLE_EXTENSIONS:
            return True
    return False
