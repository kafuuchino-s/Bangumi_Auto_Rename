import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import cast

from ..logger import logger
from .utils import VIDEO_SUFFIX

from ..ai.client import AIClient
from ..ai.models import AIAnalysisResult, EpisodeMapping
from ..ai.video_analyzer import VideoAnalyzer
from ..bangumi.context_builder import (
    AnimeInfoDict,
    BangumiContextBuilder,
    BangumiPromptContext,
    LocalFileDict,
)
from .cleaner import extract_part, extract_video_format, is_promotional_content
from .filename_builder import FilenameBuilder, EpisodeMetadata
from ..subtitle.processor import SubtitleProcessor
from ..subtitle.extractor import SUBTITLE_EXTENSIONS

AnimeEpisodeDict = dict[str, object]
FileAnalysisDict = LocalFileDict


class AIProcessor:
    """AI辅助处理器，用于智能分析和重命名"""

    AUDIO_SPECIAL_TOKENS: tuple[str, ...] = (
        "sound novel",
        "audio novel",
        "有声小说",
        "有聲小說",
        "audio drama",
        "drama cd",
        "广播剧",
        "廣播劇",
        "radio drama",
        "voice drama",
        "オーディオドラマ",
        "サウンドノベル",
    )
    SPECIAL_EVENT_TOKENS: tuple[str, ...] = (
        "recitation",
        "朗读",
        "朗讀",
        "reading event",
        "talk",
        "event",
        "cast",
        "seiyuu",
        "radio",
        "day ver",
        "dayver",
        "ending talk",
        "greeting",
        "stage",
        "live",
    )
    AUXILIARY_SPECIAL_TOKENS: tuple[str, ...] = (
        "avant",
        "review",
        "information",
        "digest",
        "guide",
        "menu",
        "preview",
        "recap",
        "commentary",
        "interview",
        "enroku",
        "promotion",
        "reel",
    )
    SEMANTIC_NOISE_TOKENS: tuple[str, ...] = (
        "mkv",
        "mp4",
        "ass",
        "srt",
        "1080p",
        "2160p",
        "720p",
        "x265",
        "x264",
        "hevc",
        "av1",
        "aac",
        "flac",
        "ddp",
        "ac3",
        "ma10p",
        "vcb",
        "studio",
        "beansub",
        "fzsd",
        "web",
        "webrip",
        "webdl",
        "bdrip",
        "bluray",
        "episode",
        "season",
        "special",
        "specials",
        "movie",
        "the",
        "and",
        "part",
        "ver",
        "hen",
    )

    RESOURCE_TOKEN_MAP: list[tuple[str, str]] = [
        ("HEVC", "HEVC"),
        ("X265", "x265"),
        ("X264", "x264"),
        ("AV1", "AV1"),
        ("10BIT", "10bit"),
        ("8BIT", "8bit"),
        ("HDR10", "HDR10"),
        ("HDR", "HDR"),
        ("DOLBY VISION", "Dolby Vision"),
        ("DV", "DV"),
        ("FLAC", "FLAC"),
        ("AAC", "AAC"),
        ("DTS", "DTS"),
        ("DDP", "DDP"),
        ("AC3", "AC3"),
        ("WEBRIP", "WebRip"),
        ("WEB-DL", "WEB-DL"),
        ("BDRIP", "BDRip"),
        ("BLURAY", "BluRay"),
    ]

    def __init__(self) -> None:
        self.ai_client: AIClient
        self.video_analyzer: VideoAnalyzer
        self.subtitle_processor: SubtitleProcessor
        self.bangumi_context_builder: BangumiContextBuilder
        self.ai_client = AIClient()
        self.video_analyzer = VideoAnalyzer()
        self.subtitle_processor = SubtitleProcessor()
        self.bangumi_context_builder = BangumiContextBuilder()

    def analyze_anime_files(
        self,
        path: Path,
        anime_info: AnimeInfoDict,
        video_files: list[Path] | None = None,
        file_analysis: list[FileAnalysisDict] | None = None,
    ) -> AIAnalysisResult | None:
        """
        使用AI分析动漫文件的映射关系

        Args:
            path: 本地文件路径
            anime_info: TMDB动漫信息
            video_files: 可选，预收集的视频文件列表
            file_analysis: 可选，预分析的视频信息列表

        Returns:
            验证后的AI分析结果
        """
        if not self.ai_client.is_available():
            logger.info("[AI处理] AI功能未启用，跳过AI分析")
            return None

        # 收集视频文件（允许复用调用方已收集结果）
        current_video_files = video_files or self._collect_video_files(path)
        if not current_video_files:
            logger.warning("[AI处理] 未找到视频文件")
            return None

        # 分析视频文件（允许复用调用方已分析结果）
        raw_file_analysis = file_analysis or self.video_analyzer.analyze_video_files(
            path, current_video_files
        )
        current_file_analysis: list[FileAnalysisDict] = []
        for item in raw_file_analysis:
            filename_value = item.get("filename")
            path_value = item.get("path")
            normalized_item: FileAnalysisDict = {}
            if isinstance(filename_value, str):
                normalized_item["filename"] = filename_value
            if isinstance(path_value, str):
                normalized_item["path"] = path_value
            current_file_analysis.append(normalized_item)

        bangumi_context: BangumiPromptContext | None = self.bangumi_context_builder.build_tv_context(
            anime_info,
            current_file_analysis,
        )
        if bangumi_context:
            subjects = bangumi_context.get('subjects', [])
            logger.info(
                f"[AI处理] 已构建 Bangumi 辅助上下文: subject_count={len(subjects)}"
            )
        else:
            logger.info("[AI处理] Bangumi 上下文不可用，回退 TMDB-only")

        # 使用AI分析映射关系
        ai_result = self.ai_client.analyze_episode_mapping(
            anime_info,
            current_file_analysis,
            bangumi_context=bangumi_context,
        )

        if ai_result:
            logger.info(f"[AI处理] AI分析完成，置信度: {ai_result.confidence}")

            # 记录低置信度结果到单独日志
            if ai_result.confidence == "Low":
                self._log_low_confidence_result(path, ai_result)

        return ai_result

    def _normalize_match_text(self, value: str) -> str:
        normalized = re.sub(r'[\W_]+', '', value or '', flags=re.UNICODE)
        return normalized.casefold()

    def _normalize_mapping_path(self, value: str) -> str:
        normalized = str(value or '').strip().strip('"\'')
        normalized = normalized.replace('\\', '/')
        normalized = re.sub(r'^(?:\./)+', '', normalized)
        normalized = normalized.lstrip('/')
        normalized = re.sub(r'/+', '/', normalized)
        parts = [part for part in normalized.split('/') if part and part != '.']
        return '/'.join(parts)

    def _generate_path_suffixes(self, normalized_path: str) -> list[str]:
        parts = [part for part in normalized_path.split('/') if part]
        suffixes: list[str] = []
        for index in range(len(parts)):
            suffix = '/'.join(parts[index:])
            if suffix and suffix not in suffixes:
                suffixes.append(suffix)
        return suffixes

    def _build_relative_file_index(
        self,
        base_path: Path,
        local_files: list[Path],
    ) -> dict[str, set[Path]]:
        index: dict[str, set[Path]] = {}

        for file_path in local_files:
            if not file_path.exists():
                continue

            try:
                rel_path = file_path.relative_to(base_path).as_posix()
            except ValueError:
                rel_path = file_path.name

            normalized_rel = self._normalize_mapping_path(rel_path)
            for key in self._generate_path_suffixes(normalized_rel):
                index.setdefault(key, set()).add(file_path.resolve())

            basename = file_path.name
            index.setdefault(basename, set()).add(file_path.resolve())

        return index

    def _resolve_mapping_source_path(
        self,
        mapping_path: str,
        base_path: Path,
        relative_file_index: dict[str, set[Path]],
    ) -> tuple[Path | None, str | None, str]:
        normalized_path = self._normalize_mapping_path(mapping_path)
        if not normalized_path:
            return None, '路径为空', ''

        path_parts = [part for part in normalized_path.split('/') if part]
        if any(part == '..' for part in path_parts):
            return None, f'非法路径:{normalized_path}', normalized_path

        exact_path = (base_path / normalized_path).resolve()
        if exact_path.exists() and exact_path.is_file():
            return exact_path, None, normalized_path
        if exact_path.exists() and not exact_path.is_file():
            return None, f'路径不是文件:{normalized_path}', normalized_path

        has_directory = len(path_parts) >= 2
        suffix_candidates: list[Path] = []
        seen: set[Path] = set()
        if has_directory:
            for key in self._generate_path_suffixes(normalized_path):
                if '/' not in key:
                    continue
                for candidate in relative_file_index.get(key, set()):
                    if candidate in seen:
                        continue
                    suffix_candidates.append(candidate)
                    seen.add(candidate)

            if len(suffix_candidates) == 1:
                return suffix_candidates[0], None, normalized_path
            if len(suffix_candidates) > 1:
                display = ', '.join(candidate.name for candidate in suffix_candidates[:3])
                return None, f'路径不唯一:{normalized_path} -> {display}', normalized_path

        basename = Path(normalized_path).name
        basename_candidates = [
            candidate for candidate in relative_file_index.get(basename, set())
        ]
        if basename_candidates:
            if has_directory:
                if len(basename_candidates) > 1:
                    display = ', '.join(candidate.name for candidate in basename_candidates[:3])
                    return None, f'路径不唯一:{normalized_path} -> {display}', normalized_path
                return None, f'文件不存在:{normalized_path}', normalized_path
            if len(basename_candidates) == 1:
                return basename_candidates[0], None, normalized_path
            display = ', '.join(candidate.name for candidate in basename_candidates[:3])
            return None, f'路径不唯一:{normalized_path} -> {display}', normalized_path

        return None, f'文件不存在:{normalized_path}', normalized_path

    def _score_mapping_path(self, file_path: str | None) -> tuple[int, float, int]:
        file_path = file_path or ''
        normalized_path = file_path.replace('\\', '/').lower()
        promo_penalty = 1 if is_promotional_content(Path(file_path).name) else 0
        path_bonus = 0.0
        if 'special' in normalized_path or 'sp' in normalized_path:
            path_bonus += 0.5
        if 'part' in normalized_path:
            path_bonus -= 0.5
        return (promo_penalty, -path_bonus, len(file_path))

    def _build_source_index_map(
        self,
        base_path: Path,
        video_files: list[Path],
    ) -> dict[int, tuple[Path, str]]:
        index_map: dict[int, tuple[Path, str]] = {}
        for idx, file_path in enumerate(video_files, 1):
            if not file_path.exists() or not file_path.is_file():
                continue
            try:
                rel_path = file_path.relative_to(base_path).as_posix()
            except ValueError:
                rel_path = file_path.name
            index_map[idx] = (file_path.resolve(), rel_path)
        return index_map

    def _hydrate_mapping_file_paths(
        self,
        ai_result: AIAnalysisResult,
        base_path: Path,
        video_files: list[Path],
    ) -> tuple[list[str], list[str]]:
        source_index_map = self._build_source_index_map(base_path, video_files)
        relative_file_index = self._build_relative_file_index(base_path, video_files)
        strict_conflicts: list[str] = []
        hydration_notes: list[str] = []

        for mapping in ai_result.file_mapping:
            source_index = getattr(mapping, 'source_index', None)
            raw_file_path = getattr(mapping, 'file_path', None)
            normalized_file_path = None
            if isinstance(raw_file_path, str) and raw_file_path.strip():
                normalized_file_path = self._normalize_mapping_path(raw_file_path)

            if source_index is None:
                if normalized_file_path:
                    mapping.file_path = normalized_file_path
                continue

            if not isinstance(source_index, int):
                strict_conflicts.append(f'编号非法:{source_index}')
                continue

            indexed = source_index_map.get(source_index)
            if indexed is None:
                strict_conflicts.append(f'编号不存在:{source_index}')
                continue

            indexed_abs_path, indexed_rel_path = indexed
            hydration_note = None
            if normalized_file_path and normalized_file_path != indexed_rel_path:
                resolved_path, path_error, _ = self._resolve_mapping_source_path(
                    normalized_file_path,
                    base_path,
                    relative_file_index,
                )
                if path_error:
                    if path_error != f'文件不存在:{normalized_file_path}':
                        strict_conflicts.append(
                            f'编号路径不一致:{source_index}:{normalized_file_path} != {indexed_rel_path}'
                        )
                        continue
                    hydration_note = (
                        f'编号忽略脏路径:{source_index}:{normalized_file_path} -> {indexed_rel_path}'
                    )
                elif resolved_path != indexed_abs_path:
                    strict_conflicts.append(
                        f'编号路径不一致:{source_index}:{normalized_file_path} != {indexed_rel_path}'
                    )
                    continue

            if normalized_file_path != indexed_rel_path:
                hydration_notes.append(
                    hydration_note
                    or f'编号回填路径:{source_index}:{indexed_rel_path}'
                )
            mapping.file_path = indexed_rel_path

        return strict_conflicts, hydration_notes

    def _pick_best_mapping_candidate(self, mappings: list[EpisodeMapping]) -> EpisodeMapping:
        def sort_key(mapping: EpisodeMapping) -> tuple[int, int, float, int]:
            confidence_rank = {'High': 0, 'Medium': 1, 'Low': 2}
            confidence = mapping.confidence
            promo_penalty, negative_bonus, path_length = self._score_mapping_path(
                mapping.file_path
            )
            return (
                confidence_rank.get(confidence, 3),
                promo_penalty,
                negative_bonus,
                path_length,
            )

        return sorted(mappings, key=sort_key)[0]

    def _has_any_token(self, value: str, tokens: tuple[str, ...]) -> bool:
        normalized = value.casefold()
        return any(token in normalized for token in tokens)

    def _extract_special_semantic_text(self, value: str) -> str:
        stem = Path(value or '').stem.casefold()
        normalized = re.sub(r'[\[\]\(\){}【】<>|｜/\\\-\._:+]+', ' ', stem)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        raw_tokens = re.findall(r'[a-z]{2,}|[\u3040-\u30ff]{2,}|[\u4e00-\u9fff]{2,}', normalized)
        tokens: list[str] = []
        for token in raw_tokens:
            if token in self.SEMANTIC_NOISE_TOKENS:
                continue
            if re.fullmatch(r'(?:ep|e|sp|ova|oad|ona|iv)\d*', token):
                continue
            tokens.append(token)
        return ' '.join(tokens)

    def _has_special_title_overlap(self, file_name: str, episode_text: str) -> bool:
        file_semantic = self._extract_special_semantic_text(file_name)
        episode_semantic = self._extract_special_semantic_text(episode_text)
        if not file_semantic or not episode_semantic:
            return False

        file_tokens = set(file_semantic.split())
        episode_tokens = set(episode_semantic.split())
        if len(file_tokens & episode_tokens) >= 2:
            return True

        similarity = SequenceMatcher(None, file_semantic, episode_semantic).ratio()
        return similarity >= 0.5

    def _is_auxiliary_special_filename(self, file_name: str) -> bool:
        normalized = Path(file_name or '').stem.casefold()
        return (
            is_promotional_content(file_name)
            or self._has_any_token(normalized, self.SPECIAL_EVENT_TOKENS)
            or self._has_any_token(normalized, self.AUXILIARY_SPECIAL_TOKENS)
            or re.search(r'\biv\s*0*\d+\b', normalized, re.IGNORECASE) is not None
        )

    def _build_tmdb_episode_lookup(
        self, anime_info: AnimeInfoDict
    ) -> dict[tuple[int, int], AnimeEpisodeDict]:
        lookup: dict[tuple[int, int], AnimeEpisodeDict] = {}
        for season in anime_info.get("seasons", []):
            season_num = season.get("season_number", 0)
            for ep in season.get("episodes", []) or []:
                episode = cast(AnimeEpisodeDict, ep)
                ep_num = ep.get("episode_number")
                if isinstance(season_num, int) and isinstance(ep_num, int) and ep_num > 0:
                    lookup[(season_num, ep_num)] = episode
        return lookup

    def _build_global_episode_lookup(
        self,
        anime_info: AnimeInfoDict,
    ) -> dict[int, tuple[int, int]]:
        lookup: dict[int, tuple[int, int]] = {}
        global_episode = 1

        seasons = sorted(
            (
                season
                for season in anime_info.get("seasons", [])
                if isinstance(season.get("season_number"), int)
                and cast(int, season.get("season_number")) > 0
            ),
            key=lambda season: cast(int, season.get("season_number")),
        )

        for season in seasons:
            season_number = cast(int, season.get("season_number"))
            episode_numbers = sorted(
                ep_num
                for ep in season.get("episodes", []) or []
                for ep_num in [ep.get("episode_number")]
                if isinstance(ep_num, int) and ep_num > 0
            )
            if not episode_numbers:
                episode_count = season.get("episode_count", 0)
                if isinstance(episode_count, int) and episode_count > 0:
                    episode_numbers = list(range(1, episode_count + 1))

            for local_episode in episode_numbers:
                lookup[global_episode] = (season_number, local_episode)
                global_episode += 1

        return lookup

    def _extract_global_episode_number(self, file_path: str) -> int | None:
        file_name = Path(file_path or '').stem
        patterns = [
            r'\[(\d{1,4})\]',
            r'(?<![A-Za-z0-9])(\d{1,3})(?![A-Za-z0-9])',
        ]
        for pattern in patterns:
            match = re.search(pattern, file_name, flags=re.IGNORECASE)
            if match:
                try:
                    episode_number = int(match.group(1))
                except (TypeError, ValueError):
                    continue
                if episode_number > 0:
                    return episode_number
        return None

    def _extract_explicit_episode_number(self, file_path: str) -> int | None:
        file_name = Path(file_path or '').stem
        patterns = [
            r'\[(\d{1,4})\]',
            r'\bEP(?:ISODE)?\s*0*(\d{1,4})\b',
            r'\b第\s*0*(\d{1,4})\s*[话話集]\b',
            r'(?<!\d)(\d{1,4})(?!\d)',
        ]
        for pattern in patterns:
            match = re.search(pattern, file_name, flags=re.IGNORECASE)
            if match:
                try:
                    episode_number = int(match.group(1))
                except (TypeError, ValueError):
                    continue
                if episode_number > 0:
                    return episode_number
        return None

    def _is_semantically_conflicting_special(
        self,
        mapping: EpisodeMapping,
        episode_info: AnimeEpisodeDict | None,
    ) -> bool:
        file_path = mapping.file_path or ''
        file_name = Path(file_path).name
        episode_name = episode_info.get('name', '') if episode_info else ''
        episode_overview = episode_info.get('overview', '') if episode_info else ''
        episode_text = f"{episode_name} {episode_overview}"

        file_is_event = self._has_any_token(file_name, self.SPECIAL_EVENT_TOKENS)
        file_is_audio = self._has_any_token(file_name, self.AUDIO_SPECIAL_TOKENS)
        episode_is_audio = self._has_any_token(episode_text, self.AUDIO_SPECIAL_TOKENS)
        file_is_auxiliary = self._is_auxiliary_special_filename(file_name)

        if getattr(mapping, 'tmdb_season', 0) > 0 and file_is_auxiliary and not file_is_audio:
            return True

        if getattr(mapping, 'tmdb_season', 0) != 0:
            return False

        if file_is_event and episode_is_audio and not file_is_audio:
            return True

        if file_is_auxiliary and not file_is_audio:
            return not self._has_special_title_overlap(file_name, episode_name)

        return False

    def _filter_semantic_special_mappings(
        self,
        mappings: list[EpisodeMapping],
        anime_info: AnimeInfoDict,
    ) -> tuple[list[EpisodeMapping], list[str], list[str]]:
        episode_lookup = self._build_tmdb_episode_lookup(anime_info)
        kept: list[EpisodeMapping] = []
        removed_paths: list[str] = []
        notes: list[str] = []

        for mapping in mappings:
            key = (mapping.tmdb_season, mapping.tmdb_episode)
            episode_info = episode_lookup.get(key)
            if self._is_semantically_conflicting_special(mapping, episode_info):
                file_path = mapping.file_path or ''
                removed_paths.append(file_path)
                notes.append(
                    f"语义过滤:S{key[0]:02d}E{key[1]:02d}:{Path(file_path).name}"
                )
                continue
            kept.append(mapping)

        return kept, removed_paths, notes

    def _sanitize_illegal_episode_mappings(
        self,
        mappings: list[EpisodeMapping],
        anime_info: AnimeInfoDict,
        tmdb_episode_keys: set[tuple[int, int]],
    ) -> tuple[list[EpisodeMapping], list[str], list[str]]:
        kept: list[EpisodeMapping] = []
        removed_paths: list[str] = []
        notes: list[str] = []
        global_episode_lookup = self._build_global_episode_lookup(anime_info)

        for mapping in mappings:
            key = (mapping.tmdb_season, mapping.tmdb_episode)
            file_path = mapping.file_path or ''
            file_is_auxiliary = self._is_auxiliary_special_filename(Path(file_path).name)
            global_episode_number = self._extract_global_episode_number(file_path)
            remapped_key = (
                global_episode_lookup.get(global_episode_number)
                if (
                    mapping.tmdb_season > 0
                    and not file_is_auxiliary
                    and global_episode_number is not None
                )
                else None
            )

            if (
                remapped_key is not None
                and remapped_key in tmdb_episode_keys
                and remapped_key != key
            ):
                old_season, old_episode = key
                new_season, new_episode = remapped_key
                mapping.tmdb_season = new_season
                mapping.tmdb_episode = new_episode
                kept.append(mapping)
                notes.append(
                    f"全局编号重映射:S{old_season:02d}E{old_episode:02d}->S{new_season:02d}E{new_episode:02d}:{Path(file_path).name}"
                )
                continue

            if key in tmdb_episode_keys:
                kept.append(mapping)
                continue

            global_episode_number = self._extract_explicit_episode_number(file_path)
            remapped_key = (
                global_episode_lookup.get(global_episode_number)
                if mapping.tmdb_season > 0
                and not file_is_auxiliary
                and global_episode_number is not None
                and global_episode_number == mapping.tmdb_episode
                else None
            )
            if remapped_key and remapped_key in tmdb_episode_keys:
                old_season, old_episode = key
                new_season, new_episode = remapped_key
                mapping.tmdb_season = new_season
                mapping.tmdb_episode = new_episode
                kept.append(mapping)
                notes.append(
                    f"全局编号重映射:S{old_season:02d}E{old_episode:02d}->S{new_season:02d}E{new_episode:02d}:{Path(file_path).name}"
                )
                continue

            removed_paths.append(file_path)
            notes.append(
                f"越界映射清洗:S{key[0]:02d}E{key[1]:02d}:{Path(file_path).name}"
            )

        return kept, removed_paths, notes

    def _remap_global_episode_mappings(
        self,
        mappings: list[EpisodeMapping],
        anime_info: AnimeInfoDict,
        tmdb_episode_keys: set[tuple[int, int]],
    ) -> list[str]:
        notes: list[str] = []
        global_episode_lookup = self._build_global_episode_lookup(anime_info)

        for mapping in mappings:
            key = (mapping.tmdb_season, mapping.tmdb_episode)
            file_path = mapping.file_path or ''
            file_is_auxiliary = self._is_auxiliary_special_filename(Path(file_path).name)
            global_episode_number = self._extract_global_episode_number(file_path)
            remapped_key = (
                global_episode_lookup.get(global_episode_number)
                if (
                    mapping.tmdb_season > 0
                    and not file_is_auxiliary
                    and global_episode_number is not None
                )
                else None
            )

            if (
                remapped_key is not None
                and remapped_key in tmdb_episode_keys
                and remapped_key != key
            ):
                old_season, old_episode = key
                new_season, new_episode = remapped_key
                mapping.tmdb_season = new_season
                mapping.tmdb_episode = new_episode
                notes.append(
                    f"全局编号重映射:S{old_season:02d}E{old_episode:02d}->S{new_season:02d}E{new_episode:02d}:{Path(file_path).name}"
                )

        return notes

    def _sanitize_tv_mappings(
        self,
        mappings: list[EpisodeMapping],
    ) -> tuple[list[EpisodeMapping], list[str]]:
        sanitized: list[EpisodeMapping] = []
        sanitizer_notes: list[str] = []
        by_file: dict[str, EpisodeMapping] = {}

        for mapping in mappings:
            raw_file_path = mapping.file_path
            if not isinstance(raw_file_path, str) or not raw_file_path.strip():
                sanitizer_notes.append(
                    f"空路径映射丢弃:#{mapping.source_index if mapping.source_index is not None else 'unknown'}"
                )
                continue

            normalized_path = raw_file_path.replace('\\', '/').lstrip('/')
            existing = by_file.get(normalized_path)
            if existing is None:
                by_file[normalized_path] = mapping
                continue

            old_key = (existing.tmdb_season, existing.tmdb_episode)
            new_key = (mapping.tmdb_season, mapping.tmdb_episode)
            if old_key == new_key:
                chosen = self._pick_best_mapping_candidate([existing, mapping])
                by_file[normalized_path] = chosen
                sanitizer_notes.append(f"重复文件去重:{normalized_path}")
            else:
                chosen = self._pick_best_mapping_candidate([existing, mapping])
                by_file[normalized_path] = chosen
                sanitizer_notes.append(f"同文件多目标保留:{normalized_path}")

        by_episode: dict[tuple[int, int], list[EpisodeMapping]] = {}
        for mapping in by_file.values():
            key = (mapping.tmdb_season, mapping.tmdb_episode)
            by_episode.setdefault(key, []).append(mapping)

        for key, conflict_mappings in by_episode.items():
            if len(conflict_mappings) == 1:
                sanitized.append(conflict_mappings[0])
                continue

            best = self._pick_best_mapping_candidate(conflict_mappings)
            sanitized.append(best)
            removed_paths = sorted(
                m.file_path or ''
                for m in conflict_mappings
                if m is not best
            )
            sanitizer_notes.append(
                f"重复映射清洗:S{key[0]:02d}E{key[1]:02d}:{', '.join(removed_paths[:3])}"
            )

        return sanitized, sanitizer_notes

    def validate_tv_result(
        self,
        ai_result: AIAnalysisResult,
        anime_info: AnimeInfoDict,
        base_path: Path,
        video_files: list[Path] | None = None,
    ) -> tuple[bool, str | None, str]:
        """验证 TV AI 结果可执行性。"""
        if not ai_result or not ai_result.file_mapping:
            return False, "ai_empty_mapping", "AI 未返回 file_mapping"

        # 统计 TMDB 可用剧集
        tmdb_episode_keys: set[tuple[int, int]] = set()
        for season in anime_info.get("seasons", []):
            season_num = season.get("season_number", 0)
            episodes = season.get("episodes", [])
            if episodes:
                for ep in episodes:
                    ep_num = ep.get("episode_number")
                    if isinstance(season_num, int) and ep_num > 0:
                        tmdb_episode_keys.add((season_num, ep_num))
            else:
                episode_count = season.get("episode_count", 0)
                if (
                    isinstance(season_num, int)
                    and episode_count > 0
                ):
                    for ep_num in range(1, episode_count + 1):
                        tmdb_episode_keys.add((season_num, ep_num))

        seen_keys: set[tuple[int, int]] = set()
        ai_reported_conflicts: list[str] = list(
            getattr(ai_result, "conflict_details", []) or []
        )
        strict_conflicts: list[str] = []
        mapped_files: set[str] = set()
        all_video_files = video_files or self._collect_video_files(base_path)
        source_hydration_conflicts, source_hydration_notes = self._hydrate_mapping_file_paths(
            ai_result,
            base_path,
            all_video_files,
        )
        strict_conflicts.extend(source_hydration_conflicts)
        relative_file_index = self._build_relative_file_index(base_path, all_video_files)

        pre_sanitize_remap_notes = self._remap_global_episode_mappings(
            ai_result.file_mapping,
            anime_info,
            tmdb_episode_keys,
        )
        ai_reported_conflicts.extend(pre_sanitize_remap_notes)

        semantic_removed_paths: list[str] = []
        semantic_notes: list[str] = []
        sanitized_mappings, semantic_removed_paths, semantic_notes = (
            self._filter_semantic_special_mappings(ai_result.file_mapping, anime_info)
        )
        ai_reported_conflicts.extend(semantic_notes)
        # 先做语义过滤，避免辅助 SP 候选在重复季集去重阶段顶掉正片映射
        sanitized_mappings, sanitizer_notes = self._sanitize_tv_mappings(sanitized_mappings)
        sanitizer_notes = pre_sanitize_remap_notes + semantic_notes + sanitizer_notes
        illegal_removed_paths: list[str] = []
        illegal_notes: list[str] = []
        sanitized_mappings, illegal_removed_paths, illegal_notes = (
            self._sanitize_illegal_episode_mappings(
                sanitized_mappings,
                anime_info,
                tmdb_episode_keys,
            )
        )
        ai_reported_conflicts.extend(illegal_notes)
        sanitizer_notes.extend(illegal_notes)
        removed_paths = semantic_removed_paths + illegal_removed_paths
        if removed_paths:
            ai_result.unmatched_files = sorted(
                set((ai_result.unmatched_files or []) + removed_paths)
            )
        if sanitizer_notes or source_hydration_notes:
            logger.info(
                f"[AI处理] 映射清洗: {'; '.join((source_hydration_notes + sanitizer_notes)[:5])}"
            )
        ai_result.file_mapping = sanitized_mappings

        for mapping in ai_result.file_mapping:
            source_path, path_error, normalized_path = self._resolve_mapping_source_path(
                mapping.file_path or '',
                base_path,
                relative_file_index,
            )

            if path_error:
                strict_conflicts.append(path_error)
                continue

            assert source_path is not None

            key = (mapping.tmdb_season, mapping.tmdb_episode)
            if key in seen_keys:
                strict_conflicts.append(f"重复映射:S{key[0]:02d}E{key[1]:02d}")
            seen_keys.add(key)

            mapped_files.add(normalized_path)

        # 可观测字段回写
        existing_rel_paths = {
            str(p.relative_to(base_path)).replace('\\', '/')
            for p in all_video_files
        }
        unmatched_files = sorted(existing_rel_paths - mapped_files)

        ai_result.unmatched_files = sorted(
            set((ai_result.unmatched_files or []) + unmatched_files)
        )
        ai_result.conflict_details = sorted(
            set(ai_reported_conflicts + strict_conflicts)
        )

        soft_conflicts = sorted(
            set(ai_result.conflict_details) - set(strict_conflicts)
        )
        if soft_conflicts:
            logger.warning(
                f"[AI处理] 检测到AI提示的不确定项(不阻断): {'; '.join(soft_conflicts[:5])}"
            )

        if strict_conflicts:
            return (
                False,
                "ai_invalid_mapping",
                '; '.join(sorted(set(strict_conflicts))[:5]),
            )

        if not seen_keys:
            return False, "ai_empty_mapping", "AI 未返回任何有效映射"

        return True, None, ''

    def apply_ai_mapping(
        self,
        ai_result: AIAnalysisResult | None,
        anime_info: AnimeInfoDict,
        base_path: Path,
        work_path: Path,
        all_local_files: list[Path] | None = None,
    ) -> dict[Path, Path]:
        """
        以 TMDB 为主应用AI分析结果生成文件映射。

        遍历 TMDB 的季度和集数，在 AI 映射中查找对应的本地文件，
        只处理 TMDB 中存在的集数，忽略 AI 返回的不存在于 TMDB 的集数。

        Args:
            ai_result: 验证后的AI分析结果
            anime_info: TMDB动漫信息（包含seasons列表）
            base_path: 媒体文件扫描的根目录
            work_path: 目标工作目录路径

        Returns:
            一个全新的文件映射字典
        """
        if not ai_result or not ai_result.file_mapping:
            logger.info("[AI处理] 无有效AI分析结果，返回空映射")
            return {}

        if not anime_info or "seasons" not in anime_info:
            logger.warning("[AI处理] 无有效TMDB信息，返回空映射")
            return {}

        new_mapping: dict[Path, Path] = {}
        resolved_local_files = all_local_files or self._collect_all_local_files(base_path)
        associated_file_index = self._build_associated_file_index(resolved_local_files)
        relative_file_index = self._build_relative_file_index(
            base_path,
            [file_path for file_path in resolved_local_files if file_path.suffix.lower() in VIDEO_SUFFIX],
        )

        # 构建 AI 映射的快速查找索引: (season, episode) -> mapping
        ai_mapping_index: dict[tuple[int, int], EpisodeMapping] = {}
        for mapping in ai_result.file_mapping:
            key = (mapping.tmdb_season, mapping.tmdb_episode)
            ai_mapping_index[key] = mapping

        # 记录季度映射信息，并提取 AI 识别的相关季度
        relevant_seasons: set[int] = set()
        if ai_result.season_mapping:
            logger.info("[AI处理] AI季度映射:")
            for season_map in ai_result.season_mapping:
                logger.info(
                    f"  {season_map.local_group_name} -> TMDB季度: {season_map.maps_to_tmdb_seasons}"
                )
                # 收集 AI 识别的相关季度
                for s in season_map.maps_to_tmdb_seasons:
                    relevant_seasons.add(s)

        # 无论 season_mapping 是否完整，都并入 file_mapping 中出现的季度
        for mapping in ai_result.file_mapping:
            relevant_seasons.add(mapping.tmdb_season)

        # 从 work_path 提取剧集标题
        series_title = FilenameBuilder.extract_title_from_folder(work_path.name)

        try:
            # 以 TMDB 季度为主遍历，但只处理 AI 识别的相关季度
            tmdb_seasons = anime_info.get("seasons", [])
            matched_count = 0
            missing_count = 0

            for season in tmdb_seasons:
                season_num = season.get("season_number", 0)
                # 优先使用 episodes 数组长度，因为 episode_count 可能为 0
                episodes = season.get("episodes", [])
                raw_episode_count = season.get("episode_count", 0)
                episode_count = len(episodes) if episodes else (
                    raw_episode_count if isinstance(raw_episode_count, int) else 0
                )

                # 跳过 AI 未识别的季度
                if season_num not in relevant_seasons:
                    continue

                logger.info(
                    f"[AI处理] 处理 Season {season_num}，TMDB 共 {episode_count} 集"
                )

                # 遍历该季度的每一集
                for ep_num in range(1, episode_count + 1):
                    key = (season_num, ep_num)

                    # 在 AI 映射中查找对应的本地文件
                    if key not in ai_mapping_index:
                        logger.warning(
                            f"[AI处理] 缺失: S{season_num:02d}E{ep_num:02d} "
                            f"- 未在本地文件中找到匹配"
                        )
                        missing_count += 1
                        continue

                    mapping = ai_mapping_index[key]
                    relative_path_str = mapping.file_path or ''
                    confidence = mapping.confidence

                    source_path, path_error, _ = self._resolve_mapping_source_path(
                        relative_path_str,
                        base_path,
                        relative_file_index,
                    )

                    if path_error or source_path is None:
                        logger.warning(
                            f"[AI处理] S{season_num:02d}E{ep_num:02d} "
                            f"- 文件路径无效: {path_error or relative_path_str}"
                        )
                        missing_count += 1
                        continue

                    # 确定目标目录
                    target_dir = (
                        work_path / FilenameBuilder.build_season_folder(season_num)
                    )

                    # 提取分集信息
                    part = extract_part(source_path.name)
                    resource_term = self._extract_resource_term(source_path.name)
                    release_group = self._extract_release_group(source_path.name)

                    # 生成新的文件名
                    meta = EpisodeMetadata(
                        title=series_title,
                        season=season_num,
                        episode=ep_num,
                        part=part,
                        resource_term=resource_term,
                        release_group=release_group,
                        file_ext=source_path.suffix,
                    )
                    new_video_filename = FilenameBuilder.build_episode_filename(meta)

                    # 添加视频文件映射
                    target_video_path = target_dir / new_video_filename
                    new_mapping[source_path] = target_video_path
                    matched_count += 1

                    logger.info(
                        f"[AI处理] 匹配: {source_path.name} -> {new_video_filename} "
                        f"(置信度: {confidence})"
                    )

                    # 查找并添加关联文件的映射
                    self._add_associated_files(
                        source_path,
                        new_video_filename,
                        target_dir,
                        associated_file_index,
                        new_mapping,
                    )

            logger.info(
                f"[AI处理] TMDB匹配完成: 匹配 {matched_count} 集, 缺失 {missing_count} 集"
            )

        except Exception as e:
            logger.error(
                f"[AI处理] 应用AI映射时发生严重错误: {str(e)}", exc_info=True
            )
            return {}

        return new_mapping

    def _add_associated_files(
        self,
        source_path: Path,
        new_video_filename: str,
        target_dir: Path,
        associated_file_index: dict[str, list[Path]],
        new_mapping: dict[Path, Path],
    ) -> None:
        """
        查找并添加关联文件（如字幕）的映射

        Args:
            source_path: 源视频文件路径
            new_video_filename: 新视频文件名
            target_dir: 目标目录
            associated_file_index: 关联文件索引（video_stem -> files）
            new_mapping: 映射字典（会被修改）
        """
        video_filename = source_path.stem
        source_resolved = source_path.resolve()
        candidate_files = associated_file_index.get(video_filename, [])

        for other_file in candidate_files:
            if not other_file.is_file():
                continue

            # 跳过源文件本身
            if other_file.resolve() == source_resolved:
                continue

            # 跳过已经在映射中的文件
            if other_file in new_mapping:
                continue

            # 排除视频文件本身（如 E01.mkv 不应匹配 E01.mp4）
            if other_file.suffix.lower() in VIDEO_SUFFIX:
                continue

            # 仅处理字幕关联文件
            if other_file.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue

            suffix_part = other_file.name[len(video_filename):]
            new_video_stem = new_video_filename.rsplit(".", 1)[0]

            # 把 .sc/.tc/.chs/.cht 等转换为 Emby 语言码，并按字幕导入规则命名
            emby_lang, is_simplified = self.subtitle_processor._normalize_language(
                self.subtitle_processor._extract_language_from_suffix_part(suffix_part)
            )

            subtitle_ext = other_file.suffix.lower()
            if is_simplified:
                new_associated_filename = (
                    f"{new_video_stem}.{emby_lang}.default{subtitle_ext}"
                )
            else:
                new_associated_filename = f"{new_video_stem}.{emby_lang}{subtitle_ext}"

            target_associated_path = target_dir / new_associated_filename
            new_mapping[other_file] = target_associated_path
            logger.info(
                f"[AI处理] 关联文件: {other_file.name} -> {new_associated_filename}"
            )

    def _extract_release_group(self, filename: str) -> str:
        """从文件名提取字幕组/发布组（如 [LoliHouse]）。"""
        if not filename:
            return ""

        match = re.match(r"^\s*\[([^\]]+)\]", filename)
        if not match:
            return ""

        return match.group(1).strip()

    def _extract_resource_term(self, filename: str) -> str:
        """从文件名提取质量信息（分辨率 + 编码/音频标签）。"""
        if not filename:
            return ""

        parts: list[str] = []
        video_format = extract_video_format(filename)
        if video_format:
            parts.append(video_format)

        upper_name = filename.upper()
        for token, display in self.RESOURCE_TOKEN_MAP:
            if token in upper_name and display not in parts:
                parts.append(display)

        return " ".join(parts)

    def _collect_all_local_files(self, base_path: Path) -> list[Path]:
        """收集基础路径下所有本地文件（包含视频与关联文件）。"""
        if base_path.is_dir():
            return [item for item in base_path.rglob("*") if item.is_file()]

        parent = base_path.parent
        if not parent.exists():
            return [base_path] if base_path.is_file() else []

        return [item for item in parent.iterdir() if item.is_file()]

    def _build_associated_file_index(
        self,
        all_local_files: list[Path],
    ) -> dict[str, list[Path]]:
        """按 video_stem 建立关联文件索引，避免 O(n²) 扫描。"""
        index: dict[str, list[Path]] = {}

        for file_path in all_local_files:
            if not file_path.is_file():
                continue

            if file_path.suffix.lower() in VIDEO_SUFFIX:
                continue

            name = file_path.name
            dot_index = name.find(".")
            if dot_index <= 0:
                continue

            video_stem = name[:dot_index]
            if not video_stem:
                continue

            index.setdefault(video_stem, []).append(file_path)

        return index

    def _collect_video_files(self, path: Path) -> list[Path]:
        """收集指定路径下的所有视频文件（过滤宣传内容）"""
        video_files: list[Path] = []
        skipped_promo = 0

        if path.is_file():
            if path.suffix.lower() in VIDEO_SUFFIX:
                if is_promotional_content(path.name):
                    logger.debug(f"[AI处理] 跳过宣传内容: {path.name}")
                else:
                    video_files.append(path)
        else:
            for item in path.rglob("*"):
                if item.is_file() and item.suffix.lower() in VIDEO_SUFFIX:
                    if is_promotional_content(item.name):
                        logger.debug(f"[AI处理] 跳过宣传内容: {item.name}")
                        skipped_promo += 1
                    else:
                        video_files.append(item)

        if skipped_promo > 0:
            logger.info(f"[AI处理] 跳过 {skipped_promo} 个宣传内容文件")

        return sorted(video_files)

    def _log_low_confidence_result(self, path: Path, ai_result: AIAnalysisResult):
        """记录低置信度结果到单独日志"""
        confidence = ai_result.confidence
        reason = ai_result.reason

        logger.warning(
            f"[AI低置信度] 路径: {path} | 置信度: {confidence} | "
            f"理由: {reason} | 映射数量: {len(ai_result.file_mapping)}"
        )

        # 记录季度映射
        if ai_result.season_mapping:
            for season_map in ai_result.season_mapping:
                logger.warning(
                    f"[AI低置信度] 季度映射: {season_map.local_group_name} -> {season_map.maps_to_tmdb_seasons}"
                )

        # 详细记录每个映射的置信度
        for mapping in ai_result.file_mapping:
            if mapping.confidence == "Low":
                logger.warning(
                    f"[AI低置信度文件] {mapping.file_path} -> "
                    f"S{mapping.tmdb_season:02d}E{mapping.tmdb_episode:02d} "
                    f"(类型: {mapping.episode_type}, 置信度: {mapping.confidence})"
                )

        # 记录额外说明
        if ai_result.extra_notes:
            logger.warning(f"[AI低置信度] 额外说明: {ai_result.extra_notes}")
