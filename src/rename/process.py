import argparse
import json
import re
import sys
import tempfile
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict, cast

from .trans import Trans
from ..logger import logger
from .get_info import Search
from ..utils.path import RECORD_PATH, TASK_PATH
from .ai_processor import AIProcessor
from ..config.config_manager import cm
from ..ai.client import AIClient
from ..ai.models import AIAnalysisResult, EpisodeMapping, MovieCollectionResult, MovieFileMapping
from ..ai.video_analyzer import VideoAnalyzer
from ..bangumi.context_builder import AnimeInfoDict
from .utils import VIDEO_SUFFIX
from .ai_processor import FileAnalysisDict
from .cleaner import (
    build_movie_search_queries,
    divide_by_year,
    extract_part,
    extract_video_format,
    is_promotional_content,
    remove_episode,
    remove_season,
    remove_tag,
)
from .filename_builder import FilenameBuilder, MovieMetadata

FAILURE_MESSAGES = {
    "ai_unavailable": "[AI] AI服务不可用或未配置",
    "ai_timeout": "[AI] AI分析超时或失败",
    "ai_low_confidence": "[AI] AI置信度不足",
    "ai_empty_mapping": "[AI] 未生成可执行映射",
    "ai_partial_mapping": "[AI] AI仅生成部分映射",
    "ai_invalid_mapping": "[AI] AI返回映射存在冲突或越界",
    "target_collision": "[映射] 多个源文件映射到同一目标",
    "mixed_subset_invalid": "[混合计划] TV/Movie 子集无效，拒绝双子集混合执行",
    "mixed_subset_overlap": "[混合计划] TV/Movie 文件集合存在重叠，拒绝双子集混合执行",
    "tmdb_not_found": "[TMDB] 未搜索到匹配结果",
    "invalid_path": "[路径] 输入路径无效",
    "tmdb_key_missing": "你还没有配置TMDB的Key！任务失败！请先前往配置界面！",
    "trans_failed": "[迁移] 文件迁移失败",
}

TmdbInfo = dict[str, object]
MovieProcessResult = dict[str, object]
SelectionResult = tuple[str, TmdbInfo, bool, bool, str | None]
EnqueueTask = Callable[..., str]


class RouteCandidate(TypedDict, total=False):
    name: str
    info: TmdbInfo
    confidence: str | None
    available: bool
    reason: str


class PlanningFileRef(TypedDict):
    source_path: str
    relative_path: str
    file_name: str


class ClaimedFileRef(PlanningFileRef):
    claim_reason: str


class RouteSubsetClaim(TypedDict):
    route_type: str
    claim_scope: str
    claimed_files: list[ClaimedFileRef]
    claimed_file_count: int


class MixedParentPlan(TypedDict):
    plan_kind: str
    planning_mode: str
    selected_route_type: str
    mixed_subset_failure_reason: str | None
    mixed_subset_failure_detail: str | None
    parent_source_path: str
    candidate_route_types: list[str]
    all_video_files: list[PlanningFileRef]
    total_video_count: int
    tv_claimed_file_count: int
    movie_claimed_file_count: int
    tv_claimed_relative_paths: list[str]
    movie_claimed_relative_paths: list[str]
    overlap_relative_paths: list[str]
    unclaimed_relative_paths: list[str]
    mixed_subset_is_valid: bool
    mixed_capable_context: bool
    mixed_single_route_fallback_blocked: bool
    mixed_subset_blockers: list[str]
    partition_recommendation: dict[str, object]


class TaskTypePlan(TypedDict):
    selected_name: str
    selected_info: TmdbInfo
    is_anime: bool
    is_movie: bool
    selected_confidence: str | None
    ai_type: str | None
    tv_candidate: RouteCandidate
    movie_candidate: RouteCandidate
    tv_subset_claim: RouteSubsetClaim
    movie_subset_claim: RouteSubsetClaim
    mixed_parent_plan: MixedParentPlan
    should_try_both: bool


class RouteEvalResult(TypedDict, total=False):
    route_type: str
    valid: bool
    confidence: str | None
    mapped_count: int
    total_video_count: int
    mapped_ratio: float
    conflict_count: int
    unmatched_count: int
    tmdb_name: str
    tmdb_info: TmdbInfo
    mapping: dict[Path, Path]
    all_local_files: list[Path]
    ai_result: object
    collection_result: MovieCollectionResult
    processed_movies: list[MovieProcessResult]
    unresolved: list[str]
    video_files: list[Path]
    work_path: Path
    release_group: str
    resource_term: str
    season_id: int
    failure_reason: str
    detail: str
    claimed_relative_paths: list[str]
    claim_reasons: dict[str, str]


class RouteExecutionPreview(TypedDict):
    route_type: str
    task_uuid: str
    source_path: str
    moved_file_count: int
    moved_relative_paths: list[str]
    target_roots: list[str]
    ai_confidence: str | None
    is_movie: bool


class MixedExecutionPreview(TypedDict):
    sample_json: str
    base_path: str
    planning_mode: str
    selected_route_type: str
    mixed_subset_is_valid: bool
    mixed_subset_failure_reason: str | None
    mixed_subset_failure_detail: str | None
    tv_valid: bool
    movie_valid: bool
    result: str | bool
    child_previews: list[RouteExecutionPreview]
    single_route_preview: RouteExecutionPreview | None


class MixedWriteProofResult(TypedDict):
    sample_json: str
    output_root: str
    source_root: str
    execute_result: str | bool | list[RouteExecutionPreview]
    parent_uuid: str | None
    parent_task_path: str | None
    parent_record_path: str | None
    child_task_paths: list[str]
    child_record_paths: list[str]
    parent_task_data: dict[str, object] | None
    parent_record_data: dict[str, object] | None
    child_task_data: dict[str, dict[str, object]]
    child_record_data: dict[str, dict[str, object]]
    auto_fetch_result: dict[str, object] | None


class MixedExecutionChildSummary(TypedDict):
    task_uuid: str
    route_type: str
    is_movie: bool
    name: str
    year: str | None
    season_id: int | None
    tmdb_id: int | None
    tmdb_media_type: str
    task_source_path: str
    source_paths: list[str]
    target_root: str
    target_paths: list[str]
    target_count: int
    ai_confidence: str | None


def _as_tmdb_info(value: object) -> TmdbInfo | None:
    return cast(TmdbInfo, value) if isinstance(value, dict) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _float_score(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


@contextmanager
def _temporary_debug_task_record_paths(task_path: Path, record_path: Path):
    from ..subtitle import auto_fetch as subtitle_auto_fetch_module
    from ..utils import utils as utils_module
    from . import trans as trans_module

    original_process_task_path = TASK_PATH
    original_process_record_path = RECORD_PATH
    original_trans_record_path = trans_module.RECORD_PATH
    original_utils_task_path = utils_module.TASK_PATH
    original_utils_record_path = utils_module.RECORD_PATH
    original_auto_fetch_task_path = subtitle_auto_fetch_module.TASK_PATH

    globals()['TASK_PATH'] = task_path
    globals()['RECORD_PATH'] = record_path
    trans_module.RECORD_PATH = record_path
    utils_module.TASK_PATH = task_path
    utils_module.RECORD_PATH = record_path
    subtitle_auto_fetch_module.TASK_PATH = task_path

    try:
        yield
    finally:
        globals()['TASK_PATH'] = original_process_task_path
        globals()['RECORD_PATH'] = original_process_record_path
        trans_module.RECORD_PATH = original_trans_record_path
        utils_module.TASK_PATH = original_utils_task_path
        utils_module.RECORD_PATH = original_utils_record_path
        subtitle_auto_fetch_module.TASK_PATH = original_auto_fetch_task_path


class Rename:
    STRUCTURAL_DIR_TOKENS: set[str] = {
        'film',
        'films',
        'movie',
        'movies',
        'serie',
        'series',
        'season',
        'seasons',
        'sp',
        'sps',
        'special',
        'specials',
        'extra',
        'extras',
        'disc',
        'discs',
        'vol',
        'volume',
        'ova',
        'oad',
    }

    MIXED_MOVIE_SUBGROUP_TOKENS: set[str] = {
        'movie',
        'the movie',
        'gekijouban',
        'avvenire',
        'arietta',
        'crepuscolo',
        'benedizione',
        'providenc',
        'sinners of the system',
        'mugen ressha hen',
    }

    def __init__(self):
        self.BANGUMI_PATH: Path = Path(str(cm.get_config('bangumi_path')))
        self.MOVIE_PATH: Path = Path(str(cm.get_config('movie_path')))
        self.ANIME_PATH: Path = Path(str(cm.get_config('anime_path')))
        self.ANIME_MOVIE_PATH: Path = Path(str(cm.get_config('anime_movie_path')))

        self.ANIME_MOVIE_PATH.mkdir(parents=True, exist_ok=True)
        self.MOVIE_PATH.mkdir(parents=True, exist_ok=True)
        self.ANIME_PATH.mkdir(parents=True, exist_ok=True)
        self.BANGUMI_PATH.mkdir(parents=True, exist_ok=True)

        self.search: Search = Search()
        self.ai_processor: AIProcessor = AIProcessor()
        self.mapping: dict[Path, Path] = {}
        self._validated_route_eval_cache: dict[str, RouteEvalResult] = {}

    def _normalize_structural_dir_name(self, name: str) -> str:
        normalized = unicodedata.normalize('NFKD', name or '')
        normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.casefold()
        normalized = re.sub(r'[^a-z0-9]+', ' ', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    def _is_structural_subdir(self, path: Path) -> bool:
        normalized = self._normalize_structural_dir_name(path.name)
        if not normalized:
            return False

        if normalized in self.STRUCTURAL_DIR_TOKENS:
            return True

        if re.fullmatch(r'(season|disc|vol|volume)\s*\d{1,2}', normalized):
            return True

        return False

    def _derive_subtask_custom_name(
        self,
        parent_path: Path,
        sub_path: Path,
        existing_name: str | None,
    ) -> str | None:
        if existing_name:
            return existing_name
        if not self._is_structural_subdir(sub_path):
            return None

        parent_name = remove_tag(parent_path.name) or remove_tag(parent_path.name, True)
        parent_name = remove_season(parent_name)
        parent_name = remove_episode(parent_name)
        parent_name = parent_name.strip('!').strip()
        return parent_name or None

    @staticmethod
    def _count_local_videos(path: Path) -> int:
        if path.is_file():
            return 1 if path.suffix.lower() in VIDEO_SUFFIX else 0

        count = 0
        for sub_path in path.rglob('*'):
            if sub_path.is_file() and sub_path.suffix.lower() in VIDEO_SUFFIX:
                count += 1
        return count

    @staticmethod
    def _relative_planning_path(base_path: Path, file_path: Path) -> str:
        if base_path.is_file():
            return file_path.name

        base_candidates = [base_path]
        file_candidates = [file_path]

        try:
            resolved_base_path = base_path.resolve()
        except OSError:
            resolved_base_path = None
        if resolved_base_path is not None and resolved_base_path not in base_candidates:
            base_candidates.append(resolved_base_path)

        try:
            resolved_file_path = file_path.resolve()
        except OSError:
            resolved_file_path = None
        if resolved_file_path is not None and resolved_file_path not in file_candidates:
            file_candidates.append(resolved_file_path)

        for candidate_base_path in base_candidates:
            for candidate_file_path in file_candidates:
                try:
                    return candidate_file_path.relative_to(candidate_base_path).as_posix()
                except ValueError:
                    continue

        return file_path.name

    def _collect_planning_video_files(self, path: Path) -> list[Path]:
        if path.is_file():
            return [path] if path.suffix.lower() in VIDEO_SUFFIX else []

        video_files = [
            sub_path
            for sub_path in path.rglob('*')
            if sub_path.is_file() and sub_path.suffix.lower() in VIDEO_SUFFIX
        ]
        return sorted(
            video_files,
            key=lambda item: self._relative_planning_path(path, item).casefold(),
        )

    def _build_video_discovery_debug(self, path: Path) -> dict[str, object]:
        all_video_files = self._collect_planning_video_files(path)
        promotional_files = [
            item for item in all_video_files if is_promotional_content(item.name)
        ]
        return {
            'raw_video_count': len(all_video_files),
            'promo_video_count': len(promotional_files),
            'processable_video_count': len(all_video_files) - len(promotional_files),
            'raw_video_examples': [
                self._relative_planning_path(path if path.is_dir() else path.parent, item)
                for item in all_video_files[:5]
            ],
            'promo_video_examples': [
                self._relative_planning_path(path if path.is_dir() else path.parent, item)
                for item in promotional_files[:5]
            ],
        }

    def _is_supplemental_video_file(self, file_path: Path, base_path: Path) -> bool:
        if is_promotional_content(file_path.name):
            return True

        try:
            relative_text = str(
                file_path.resolve().relative_to(base_path.resolve())
            ).replace('\\', '/')
        except ValueError:
            relative_text = file_path.name

        relative_parts = [part.casefold() for part in Path(relative_text).parts[:-1]]
        if any(
            part in {
                'extras',
                'extra',
                'bonus',
                'sps',
                'specials',
                'creditless op-ed',
                'creditless op',
                'creditless ed',
                '映像特典',
                '特典',
            }
            for part in relative_parts
        ):
            return True

        text = f'{relative_text} {file_path.name}'.casefold()
        supplemental_patterns = (
            r'(^|[^a-z0-9])extras?([^a-z0-9]|$)',
            r'(^|[^a-z0-9])bonus([^a-z0-9]|$)',
            r'(^|[^a-z0-9])menu([^a-z0-9]|$)',
            r'(^|[^a-z0-9])trailer([^a-z0-9]|$)',
            r'(^|[^a-z0-9])teaser([^a-z0-9]|$)',
            r'(^|[^a-z0-9])preview([^a-z0-9]|$)',
            r'(^|[^a-z0-9])commentary([^a-z0-9]|$)',
            r'(^|[^a-z0-9])interview([^a-z0-9]|$)',
            r'(^|[^a-z0-9])talk([^a-z0-9]|$)',
            r'(^|[^a-z0-9])making([^a-z0-9]|$)',
            r'(^|[^a-z0-9])radio([^a-z0-9]|$)',
            r'(^|[^a-z0-9])live([^a-z0-9]|$)',
            r'(^|[^a-z0-9])digest([^a-z0-9]|$)',
            r'(^|[^a-z0-9])theater[\s._-]*greeting([^a-z0-9]|$)',
            r'(^|[^a-z0-9])after[\s._-]*movie([^a-z0-9]|$)',
            r'(^|[^a-z0-9])story[\s._-]*summary([^a-z0-9]|$)',
            r'(^|[^a-z0-9])info\d*([^a-z0-9]|$)',
            r'(^|[^a-z0-9])theme[\s._-]*song([^a-z0-9]|$)',
            r'(^|[^a-z0-9])recitation[\s._-]*drama([^a-z0-9]|$)',
            r'memorial[\s._-]*note',
            r'tv[\s._-]*spot',
            r'映像特典',
            r'特典',
            r'メニュー',
            r'予告',
            r'番宣',
        )
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in supplemental_patterns)

    def _build_planning_file_refs(self, path: Path) -> list[PlanningFileRef]:
        planning_files: list[PlanningFileRef] = []
        for file_path in self._collect_planning_video_files(path):
            planning_files.append(
                {
                    'source_path': str(file_path),
                    'relative_path': self._relative_planning_path(path, file_path),
                    'file_name': file_path.name,
                }
            )
        return planning_files

    def _match_tv_subset_claim_reason(
        self,
        relative_path: str,
        file_name: str,
    ) -> str | None:
        if self._has_tv_hint(file_name):
            return 'tv_hint'

        dir_parts = [part for part in relative_path.split('/')[:-1] if part]
        for part in dir_parts:
            normalized = self._normalize_structural_dir_name(part)
            if not normalized:
                continue
            if normalized in {'season', 'seasons', 'sp', 'sps', 'special', 'specials', 'ova', 'oad'}:
                return f'tv_structure:{normalized}'
            if normalized.startswith('season '):
                return f'tv_structure:{normalized}'

        lowered_file_name = file_name.casefold()
        if 'ncop' in lowered_file_name or 'nced' in lowered_file_name:
            return 'tv_extra_token'

        return None

    def _match_movie_subset_claim_reason(
        self,
        relative_path: str,
        file_name: str,
    ) -> str | None:
        lowered_relative_path = relative_path.casefold()
        for token in sorted(self.MIXED_MOVIE_SUBGROUP_TOKENS, key=len, reverse=True):
            if token in lowered_relative_path:
                return f'movie_token:{token}'

        if self._has_movie_hint(file_name):
            return 'movie_hint'

        return None

    def _build_route_subset_claim(
        self,
        route_type: str,
        planning_files: list[PlanningFileRef],
    ) -> RouteSubsetClaim:
        claimed_files: list[ClaimedFileRef] = []

        for planning_file in planning_files:
            relative_path = planning_file['relative_path']
            file_name = planning_file['file_name']
            claim_reason = None

            if route_type == 'tv':
                claim_reason = self._match_tv_subset_claim_reason(relative_path, file_name)
            elif route_type == 'movie':
                claim_reason = self._match_movie_subset_claim_reason(relative_path, file_name)

            if not claim_reason:
                continue

            claimed_files.append(
                {
                    'source_path': planning_file['source_path'],
                    'relative_path': relative_path,
                    'file_name': file_name,
                    'claim_reason': claim_reason,
                }
            )

        return {
            'route_type': route_type,
            'claim_scope': 'file_level',
            'claimed_files': claimed_files,
            'claimed_file_count': len(claimed_files),
        }

    @staticmethod
    def _empty_route_subset_claim(route_type: str) -> RouteSubsetClaim:
        return {
            'route_type': route_type,
            'claim_scope': 'file_level',
            'claimed_files': [],
            'claimed_file_count': 0,
        }

    def _build_validated_tv_subset_claim(
        self,
        planning_files: list[PlanningFileRef],
        route_eval: RouteEvalResult | None,
    ) -> RouteSubsetClaim:
        if not route_eval or not route_eval.get('valid'):
            return self._empty_route_subset_claim('tv')

        claimed_relative_paths = set(route_eval.get('claimed_relative_paths', []))
        if not claimed_relative_paths:
            return self._empty_route_subset_claim('tv')

        claim_reasons = cast(dict[str, str], route_eval.get('claim_reasons', {}))
        claimed_files: list[ClaimedFileRef] = []
        for planning_file in planning_files:
            relative_path = planning_file['relative_path']
            if relative_path not in claimed_relative_paths:
                continue
            claimed_files.append(
                {
                    'source_path': planning_file['source_path'],
                    'relative_path': relative_path,
                    'file_name': planning_file['file_name'],
                    'claim_reason': claim_reasons.get(
                        relative_path,
                        'validated_tv_mapping',
                    ),
                }
            )

        return {
            'route_type': 'tv',
            'claim_scope': 'file_level',
            'claimed_files': claimed_files,
            'claimed_file_count': len(claimed_files),
        }

    def _build_validated_movie_subset_claim(
        self,
        planning_files: list[PlanningFileRef],
        route_eval: RouteEvalResult | None,
    ) -> RouteSubsetClaim:
        if not route_eval or not route_eval.get('valid'):
            return self._empty_route_subset_claim('movie')

        claimed_relative_paths = set(route_eval.get('claimed_relative_paths', []))
        if not claimed_relative_paths:
            return self._empty_route_subset_claim('movie')

        claim_reasons = cast(dict[str, str], route_eval.get('claim_reasons', {}))
        claimed_files: list[ClaimedFileRef] = []
        for planning_file in planning_files:
            relative_path = planning_file['relative_path']
            if relative_path not in claimed_relative_paths:
                continue
            claimed_files.append(
                {
                    'source_path': planning_file['source_path'],
                    'relative_path': relative_path,
                    'file_name': planning_file['file_name'],
                    'claim_reason': claim_reasons.get(
                        relative_path,
                        'validated_movie_mapping',
                    ),
                }
            )

        return {
            'route_type': 'movie',
            'claim_scope': 'file_level',
            'claimed_files': claimed_files,
            'claimed_file_count': len(claimed_files),
        }

    def _build_validated_route_eval_cache_key(
        self,
        path: Path,
        route_type: str,
        info: TmdbInfo,
    ) -> str | None:
        tmdb_id = _as_int(info.get('id'))
        if tmdb_id is None:
            return None

        try:
            normalized_path = str(path.resolve())
        except OSError:
            normalized_path = str(path)
        return f'{route_type}:{normalized_path}:{tmdb_id}'

    def _get_cached_validated_route_eval(
        self,
        path: Path,
        route_type: str,
        info: TmdbInfo,
    ) -> RouteEvalResult | None:
        cache_key = self._build_validated_route_eval_cache_key(path, route_type, info)
        if not cache_key:
            return None
        return self._validated_route_eval_cache.get(cache_key)

    def _store_validated_route_eval(
        self,
        path: Path,
        route_type: str,
        info: TmdbInfo,
        route_eval: RouteEvalResult,
    ) -> None:
        cache_key = self._build_validated_route_eval_cache_key(path, route_type, info)
        if not cache_key:
            return
        self._validated_route_eval_cache[cache_key] = route_eval

    def _pop_cached_validated_route_eval(
        self,
        path: Path,
        route_type: str,
        info: TmdbInfo,
    ) -> RouteEvalResult | None:
        cache_key = self._build_validated_route_eval_cache_key(path, route_type, info)
        if not cache_key:
            return None
        return self._validated_route_eval_cache.pop(cache_key, None)

    def _evaluate_validated_tv_route(
        self,
        path: Path,
        tv_info: TmdbInfo,
        tv_name: str,
        *,
        injected_ai_result: AIAnalysisResult | None = None,
        ordered_video_files: list[Path] | None = None,
    ) -> RouteEvalResult:
        cached = None
        if injected_ai_result is None:
            cached = self._get_cached_validated_route_eval(path, 'tv', tv_info)
            if cached is not None:
                return cached

        route_eval: RouteEvalResult = {
            'route_type': 'tv',
            'valid': False,
            'tmdb_name': tv_name,
            'tmdb_info': tv_info,
        }

        if not path.exists():
            route_eval['failure_reason'] = 'invalid_path'
            route_eval['detail'] = f'路径不存在: {path}'
            return route_eval

        video_files = ordered_video_files or self.ai_processor._collect_video_files(path)
        route_eval['video_files'] = video_files
        route_eval['total_video_count'] = len(video_files)
        if not video_files:
            route_eval['video_discovery'] = self._build_video_discovery_debug(path)
            route_eval['failure_reason'] = 'ai_empty_mapping'
            route_eval['detail'] = '未发现可处理的视频文件'
            return route_eval

        preferred_season = self.search.extract_preferred_season_number(path.name, tv_name)
        explicit_fallback_ai_result = self._build_explicit_tv_episode_fallback_result(
            path,
            tv_info,
            video_files,
        )
        plain_episode_fallback_ai_result = self._build_plain_tv_episode_fallback_result(
            path,
            tv_info,
            video_files,
            preferred_season=preferred_season,
        )

        tv_info_typed = cast(AnimeInfoDict, cast(object, tv_info))

        ai_result = injected_ai_result
        if ai_result is None:
            file_analysis = self.ai_processor.video_analyzer.analyze_video_files(
                path,
                video_files,
            )
            file_analysis_typed = cast(list[FileAnalysisDict], file_analysis)
            ai_result = self.ai_processor.analyze_anime_files(
                path,
                tv_info_typed,
                video_files=video_files,
                file_analysis=file_analysis_typed,
            )
            if not ai_result:
                if plain_episode_fallback_ai_result is not None:
                    logger.info(
                        '[处理任务] TV AI 无响应，改用连续普通集数 deterministic fallback'
                    )
                    ai_result = plain_episode_fallback_ai_result
                else:
                    route_eval['failure_reason'] = 'ai_timeout'
                    route_eval['detail'] = 'AI 未返回 TV 映射结果'
                    self._store_validated_route_eval(path, 'tv', tv_info, route_eval)
                    return route_eval

        if ai_result is None:
            if plain_episode_fallback_ai_result is not None:
                ai_result = plain_episode_fallback_ai_result
            else:
                route_eval['failure_reason'] = 'ai_timeout'
                route_eval['detail'] = 'AI 未返回 TV 映射结果'
                self._store_validated_route_eval(path, 'tv', tv_info, route_eval)
                return route_eval

        route_eval['ai_result'] = ai_result
        route_eval['confidence'] = ai_result.confidence

        if preferred_season and plain_episode_fallback_ai_result is not None:
            logger.info(
                '[处理任务] 检测到明确季提示，使用连续普通集数 deterministic fallback '
                f'(season={preferred_season})'
            )
            ai_result = plain_episode_fallback_ai_result
            route_eval['ai_result'] = ai_result
            route_eval['confidence'] = ai_result.confidence

        if not self._is_confidence_acceptable(ai_result.confidence):
            if explicit_fallback_ai_result is not None:
                logger.info(
                    '[处理任务] TV 映射低置信度，改用显式季集 deterministic fallback'
                )
                ai_result = explicit_fallback_ai_result
                route_eval['ai_result'] = ai_result
                route_eval['confidence'] = ai_result.confidence
            elif plain_episode_fallback_ai_result is not None:
                logger.info(
                    '[处理任务] TV 映射低置信度，改用连续普通集数 deterministic fallback'
                )
                ai_result = plain_episode_fallback_ai_result
                route_eval['ai_result'] = ai_result
                route_eval['confidence'] = ai_result.confidence
            else:
                route_eval['failure_reason'] = 'ai_low_confidence'
                route_eval['detail'] = f'结果置信度={ai_result.confidence}'
                if injected_ai_result is None:
                    self._store_validated_route_eval(path, 'tv', tv_info, route_eval)
                return route_eval

        if not self._is_confidence_acceptable(ai_result.confidence):
            route_eval['failure_reason'] = 'ai_low_confidence'
            route_eval['detail'] = f'结果置信度={ai_result.confidence}'
            if injected_ai_result is None:
                self._store_validated_route_eval(path, 'tv', tv_info, route_eval)
            return route_eval

        valid, reason, detail = self.ai_processor.validate_tv_result(
            ai_result,
            tv_info_typed,
            path,
            video_files=video_files,
        )
        route_eval['conflict_count'] = len(ai_result.conflict_details)
        route_eval['unmatched_count'] = len(ai_result.unmatched_files)
        if not valid:
            route_eval['failure_reason'] = reason or 'ai_invalid_mapping'
            route_eval['detail'] = detail
            if injected_ai_result is None:
                self._store_validated_route_eval(path, 'tv', tv_info, route_eval)
            return route_eval

        work_root = self.ANIME_PATH if self._detect_anime_genre(tv_info) else self.BANGUMI_PATH
        first_data = _as_str(tv_info.get('first_air_date')) or ''
        first_year = first_data.split('-')[0] if first_data else None
        work_path = FilenameBuilder.build_tv_work_path(
            work_root,
            tv_name,
            first_year,
        )
        all_local_files = self.ai_processor._collect_all_local_files(path)
        mapping = self.ai_processor.apply_ai_mapping(
            ai_result=ai_result,
            anime_info=tv_info_typed,
            base_path=path,
            work_path=work_path,
            all_local_files=all_local_files,
        )
        if not mapping:
            route_eval['failure_reason'] = 'ai_empty_mapping'
            route_eval['detail'] = '严格验证通过后未生成可执行 TV 映射'
            if injected_ai_result is None:
                self._store_validated_route_eval(path, 'tv', tv_info, route_eval)
            return route_eval

        planning_base_path = path if path.is_dir() else path.parent
        try:
            planning_base_path = planning_base_path.resolve()
        except OSError:
            pass
        mapping_claim_reasons: dict[str, str] = {}
        relative_file_index = self.ai_processor._build_relative_file_index(path, video_files)
        for file_mapping in ai_result.file_mapping:
            source_path, path_error, _ = self.ai_processor._resolve_mapping_source_path(
                file_mapping.file_path or '',
                path,
                relative_file_index,
            )
            if path_error or source_path is None:
                continue
            if source_path.suffix.lower() not in VIDEO_SUFFIX:
                continue
            relative_path = self._relative_planning_path(planning_base_path, source_path)
            mapping_claim_reasons[relative_path] = (
                f'validated_tv_mapping:S{file_mapping.tmdb_season:02d}E{file_mapping.tmdb_episode:02d}'
            )

        mapped_video_paths = [
            source_path
            for source_path in mapping
            if source_path.suffix.lower() in VIDEO_SUFFIX
        ]
        mapped_video_paths = sorted(
            mapped_video_paths,
            key=lambda item: self._relative_planning_path(planning_base_path, item),
        )
        mapped_video_path_set = {
            self._relative_planning_path(planning_base_path, source_path)
            for source_path in mapped_video_paths
        }
        unmapped_video_paths = [
            source_path
            for source_path in video_files
            if self._relative_planning_path(planning_base_path, source_path)
            not in mapped_video_path_set
        ]
        potential_main_unmapped_video_paths = [
            source_path
            for source_path in unmapped_video_paths
            if not self._is_supplemental_video_file(source_path, planning_base_path)
        ]
        ignored_supplemental_video_paths = [
            source_path
            for source_path in unmapped_video_paths
            if source_path not in potential_main_unmapped_video_paths
        ]
        if mapped_video_paths and potential_main_unmapped_video_paths:
            route_eval['failure_reason'] = 'ai_partial_mapping'
            route_eval['detail'] = (
                f'TV 映射只覆盖 {len(mapped_video_paths)}/{len(video_files)} 个视频，'
                f'仍有 {len(potential_main_unmapped_video_paths)} 个正片候选视频未映射'
            )
            route_eval['unmapped_potential_main_files'] = [
                self._relative_planning_path(planning_base_path, source_path)
                for source_path in potential_main_unmapped_video_paths[:20]
            ]
            route_eval['ignored_supplemental_relative_paths'] = [
                self._relative_planning_path(planning_base_path, source_path)
                for source_path in ignored_supplemental_video_paths[:20]
            ]
            if injected_ai_result is None:
                self._store_validated_route_eval(path, 'tv', tv_info, route_eval)
            return route_eval
        claimed_relative_paths = [
            self._relative_planning_path(planning_base_path, source_path)
            for source_path in mapped_video_paths
        ]

        route_eval.update(
            {
                'valid': bool(mapped_video_paths),
                'mapping': mapping,
                'all_local_files': all_local_files,
                'work_path': work_path,
                'mapped_count': len(mapped_video_paths),
                'mapped_ratio': len(mapped_video_paths) / len(video_files),
                'claimed_relative_paths': claimed_relative_paths,
                'ignored_supplemental_relative_paths': [
                    self._relative_planning_path(planning_base_path, source_path)
                    for source_path in ignored_supplemental_video_paths
                ],
                'claim_reasons': {
                    relative_path: mapping_claim_reasons.get(
                        relative_path,
                        'validated_tv_mapping',
                    )
                    for relative_path in claimed_relative_paths
                },
                'failure_reason': '',
                'detail': '',
            }
        )

        if injected_ai_result is None:
            self._store_validated_route_eval(path, 'tv', tv_info, route_eval)
        return route_eval

    def _build_explicit_tv_episode_fallback_result(
        self,
        base_path: Path,
        tv_info: TmdbInfo,
        video_files: list[Path],
    ) -> AIAnalysisResult | None:
        if len(video_files) < 2:
            return None

        pattern = re.compile(r'(?i)\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b')
        extracted: list[tuple[Path, int, int]] = []
        seen_keys: set[tuple[int, int]] = set()

        for file_path in sorted(video_files, key=lambda item: item.name.casefold()):
            match = pattern.search(file_path.name)
            if match is None:
                return None
            season_num = int(match.group('season'))
            episode_num = int(match.group('episode'))
            key = (season_num, episode_num)
            if key in seen_keys:
                return None
            seen_keys.add(key)
            extracted.append((file_path, season_num, episode_num))

        season_numbers = {season for _, season, _ in extracted}
        if len(season_numbers) != 1:
            return None

        season_num = next(iter(season_numbers))
        episode_numbers = sorted(episode for _, _, episode in extracted)
        expected_numbers = list(range(1, len(episode_numbers) + 1))
        if episode_numbers != expected_numbers:
            return None

        matched_season_info: dict[str, object] | None = None
        for season in tv_info.get('seasons', []):
            if not isinstance(season, dict):
                continue
            if season.get('season_number') == season_num:
                matched_season_info = cast(dict[str, object], season)
                break
        if matched_season_info is None:
            return None

        season_episode_count_raw = matched_season_info.get('episode_count', 0)
        season_episodes = matched_season_info.get('episodes', [])
        season_episode_count = (
            len(season_episodes)
            if isinstance(season_episodes, list) and season_episodes
            else season_episode_count_raw if isinstance(season_episode_count_raw, int) else 0
        )
        if season_episode_count > 0 and len(extracted) > season_episode_count:
            return None

        mappings: list[EpisodeMapping] = []
        for file_path, _, episode_num in sorted(extracted, key=lambda item: item[2]):
            try:
                relative_path = file_path.relative_to(base_path).as_posix()
            except ValueError:
                relative_path = file_path.name
            mappings.append(
                EpisodeMapping(
                    file_path=relative_path,
                    tmdb_season=season_num,
                    tmdb_episode=episode_num,
                    episode_type='regular',
                    confidence='High',
                )
            )

        return AIAnalysisResult(
            confidence='High',
            reason='显式季集文件名 deterministic fallback',
            file_mapping=mappings,
            unmatched_files=[],
            conflict_details=[],
            extra_notes='Applied explicit SxxEyy deterministic fallback after low-confidence TV mapping.',
        )

    def _build_plain_tv_episode_fallback_result(
        self,
        base_path: Path,
        tv_info: TmdbInfo,
        video_files: list[Path],
        *,
        preferred_season: int | None = None,
    ) -> AIAnalysisResult | None:
        if len(video_files) < 2:
            return None

        try:
            resolved_base_path = base_path.resolve()
        except OSError:
            resolved_base_path = base_path

        main_video_files = [
            file_path
            for file_path in video_files
            if not self._is_supplemental_video_file(file_path, resolved_base_path)
        ]
        if len(main_video_files) < 2:
            return None

        unsafe_tokens = re.compile(
            r'(?i)(?:\bS\d{1,2}E\d{1,3}\b|\bOVA\b|\bOAD\b|\bSP\d*\b|\bSPECIAL\b|\bFINAL\s*ACT\b|#\d+\s*DC\b)'
        )
        plain_episode_pattern = re.compile(
            r'(?<!\d)(?:\[|\(|\s|-|_|#|第)?(?P<episode>\d{1,3})(?:v\d+)?(?:\]|\)|\s|-|_|\.|话|話|集|$)(?!\d)',
            re.IGNORECASE,
        )

        extracted: list[tuple[Path, int]] = []
        seen_episodes: set[int] = set()
        for file_path in sorted(main_video_files, key=lambda item: item.name.casefold()):
            name = file_path.name
            if unsafe_tokens.search(name):
                return None
            matches = [
                int(match.group('episode'))
                for match in plain_episode_pattern.finditer(name)
                if 1 <= int(match.group('episode')) <= len(main_video_files)
            ]
            if len(matches) != 1:
                return None
            episode_num = matches[0]
            if episode_num in seen_episodes:
                return None
            seen_episodes.add(episode_num)
            extracted.append((file_path, episode_num))

        episode_numbers = sorted(episode for _, episode in extracted)
        if episode_numbers != list(range(1, len(episode_numbers) + 1)):
            return None

        season_info_candidates = [
            cast(dict[str, object], season)
            for season in tv_info.get('seasons', [])
            if isinstance(season, dict) and season.get('season_number') not in (0, None)
        ]
        if preferred_season is not None:
            season_info_candidates = [
                season
                for season in season_info_candidates
                if season.get('season_number') == preferred_season
            ]
        if len(season_info_candidates) != 1:
            return None
        matched_season_info = season_info_candidates[0]
        season_num_raw = matched_season_info.get('season_number')
        if not isinstance(season_num_raw, int):
            return None
        season_num = season_num_raw

        season_episode_count_raw = matched_season_info.get('episode_count', 0)
        season_episodes = matched_season_info.get('episodes', [])
        season_episode_count = (
            len(season_episodes)
            if isinstance(season_episodes, list) and season_episodes
            else season_episode_count_raw if isinstance(season_episode_count_raw, int) else 0
        )
        if season_episode_count <= 0 or len(extracted) > season_episode_count:
            return None

        mappings: list[EpisodeMapping] = []
        for file_path, episode_num in sorted(extracted, key=lambda item: item[1]):
            try:
                relative_path = file_path.resolve().relative_to(resolved_base_path).as_posix()
            except (OSError, ValueError):
                relative_path = file_path.name
            mappings.append(
                EpisodeMapping(
                    file_path=relative_path,
                    tmdb_season=season_num,
                    tmdb_episode=episode_num,
                    episode_type='regular',
                    confidence='High',
                )
            )

        return AIAnalysisResult(
            confidence='High',
            reason='连续普通集数文件名 deterministic fallback',
            file_mapping=mappings,
            unmatched_files=[],
            conflict_details=[],
            extra_notes='Applied plain episode deterministic fallback after weak or missing AI TV mapping.',
        )

    def _evaluate_validated_movie_route(
        self,
        path: Path,
        movie_info: TmdbInfo | None,
        movie_name: str,
        *,
        ai_client: AIClient | None = None,
        injected_collection_result: MovieCollectionResult | None = None,
        ordered_video_files: list[Path] | None = None,
    ) -> RouteEvalResult:
        cached = None
        if injected_collection_result is None and movie_info is not None:
            cached = self._get_cached_validated_route_eval(path, 'movie', movie_info)
            if cached is not None:
                return cached

        route_eval: RouteEvalResult = {
            'route_type': 'movie',
            'valid': False,
            'tmdb_name': movie_name,
            'tmdb_info': movie_info or {},
        }

        def store_if_needed() -> None:
            if injected_collection_result is None and movie_info is not None:
                self._store_validated_route_eval(path, 'movie', movie_info, route_eval)

        if not path.exists():
            route_eval['failure_reason'] = 'invalid_path'
            route_eval['detail'] = f'路径不存在: {path}'
            return route_eval

        video_files = ordered_video_files or self._collect_planning_video_files(path)
        route_eval['video_files'] = video_files
        route_eval['total_video_count'] = len(video_files)
        if not video_files:
            route_eval['video_discovery'] = self._build_video_discovery_debug(path)
            route_eval['failure_reason'] = 'ai_empty_mapping'
            route_eval['detail'] = '未发现可处理的视频文件'
            return route_eval

        planning_base_path = path if path.is_dir() else path.parent
        try:
            planning_base_path = planning_base_path.resolve()
        except OSError:
            pass

        if len(video_files) == 1:
            relative_path = self._relative_planning_path(planning_base_path, video_files[0])
            route_eval.update(
                {
                    'valid': True,
                    'confidence': 'High',
                    'mapped_count': 1,
                    'mapped_ratio': 1.0,
                    'unmatched_count': 0,
                    'conflict_count': 0,
                    'claimed_relative_paths': [relative_path],
                    'claim_reasons': {
                        relative_path: 'validated_single_movie_file',
                    },
                    'failure_reason': '',
                    'detail': '',
                }
            )
            store_if_needed()
            return route_eval

        ai_client = ai_client or AIClient()
        if injected_collection_result is None and not ai_client.is_available():
            route_eval['failure_reason'] = 'ai_unavailable'
            route_eval['detail'] = 'AI 不可用，无法复核电影合集可执行子集'
            store_if_needed()
            return route_eval

        collection_result = injected_collection_result
        if collection_result is None:
            local_files = VideoAnalyzer.analyze_video_files(path, video_files)
            collection_result = ai_client.analyze_movie_collection(
                path.name,
                local_files,
            )
            if not collection_result:
                route_eval['failure_reason'] = 'ai_timeout'
                route_eval['detail'] = 'AI 未返回电影合集分析结果'
                store_if_needed()
                return route_eval

        route_eval['collection_result'] = collection_result
        route_eval['confidence'] = collection_result.confidence
        route_eval['conflict_count'] = len(collection_result.conflict_details)
        route_eval['unmatched_count'] = len(collection_result.unmatched_files)

        if not self._is_confidence_acceptable(collection_result.confidence):
            route_eval['failure_reason'] = 'ai_low_confidence'
            route_eval['detail'] = f'合集置信度={collection_result.confidence}'
            store_if_needed()
            return route_eval

        single_movie_files = self._extract_single_movie_files_from_collection_result(
            collection_result,
            video_files,
            path,
        )
        if single_movie_files:
            mapping_title = ''
            resolved_subset_movie: MovieProcessResult | None = None
            resolved_subset_info: TmdbInfo | None = None
            if collection_result.file_mapping:
                mapping_title = collection_result.file_mapping[0].movie_title.strip()
                resolved_subset_movie, resolved_subset_info = self._resolve_single_movie_subset_result(
                    path,
                    collection_result.file_mapping[0],
                    collection_result.collection_name,
                )
            claimed_relative_paths = sorted(
                [
                    self._relative_planning_path(planning_base_path, file_path)
                    for file_path in single_movie_files
                ],
                key=str.casefold,
            )
            claim_reason = 'validated_single_movie_subset'
            if mapping_title:
                claim_reason = f'validated_single_movie_subset:{mapping_title}'
            route_eval.update(
                {
                    'valid': True,
                    'mapped_count': len(claimed_relative_paths),
                    'mapped_ratio': len(claimed_relative_paths) / len(video_files),
                    'unmatched_count': len(video_files) - len(claimed_relative_paths),
                    'claimed_relative_paths': claimed_relative_paths,
                    'claim_reasons': {
                        relative_path: claim_reason
                        for relative_path in claimed_relative_paths
                    },
                    'failure_reason': '',
                    'detail': '',
                }
            )
            if resolved_subset_movie and resolved_subset_info:
                route_eval['processed_movies'] = [resolved_subset_movie]
                route_eval['tmdb_name'] = _as_str(resolved_subset_info.get('title')) or mapping_title or movie_name
                route_eval['tmdb_info'] = resolved_subset_info
            store_if_needed()
            return route_eval

        valid, reason, detail = self._validate_movie_collection_result(
            collection_result,
            video_files,
            path,
        )
        route_eval['conflict_count'] = len(collection_result.conflict_details)
        route_eval['unmatched_count'] = len(collection_result.unmatched_files)
        if not valid:
            route_eval['failure_reason'] = reason or 'ai_invalid_mapping'
            route_eval['detail'] = detail
            store_if_needed()
            return route_eval

        work_root = self.MOVIE_PATH
        if movie_info and self._detect_anime_genre(movie_info):
            work_root = self.ANIME_MOVIE_PATH

        processed_movies, unresolved = self._process_movie_collection(
            path,
            collection_result,
            work_root,
            ai_client,
        )
        route_eval['processed_movies'] = processed_movies
        route_eval['unresolved'] = unresolved
        if unresolved:
            route_eval['failure_reason'] = 'ai_empty_mapping'
            route_eval['detail'] = f"未能完成全部电影映射: {', '.join(unresolved[:3])}"
            store_if_needed()
            return route_eval

        if not processed_movies:
            route_eval['failure_reason'] = 'ai_empty_mapping'
            route_eval['detail'] = '电影合集严格验证后未生成可执行映射'
            store_if_needed()
            return route_eval

        claim_reasons: dict[str, str] = {}
        for movie_data in processed_movies:
            file_path = cast(Path, movie_data['file_path'])
            relative_path = self._relative_planning_path(planning_base_path, file_path)
            movie_name_value = _as_str(movie_data.get('movie_name')) or ''
            claim_reason = 'validated_movie_collection'
            if movie_name_value:
                claim_reason = f'validated_movie_collection:{movie_name_value}'
            claim_reasons[relative_path] = claim_reason

        claimed_relative_paths = sorted(claim_reasons, key=str.casefold)
        route_eval.update(
            {
                'valid': bool(claimed_relative_paths),
                'mapped_count': len(claimed_relative_paths),
                'mapped_ratio': len(claimed_relative_paths) / len(video_files),
                'claimed_relative_paths': claimed_relative_paths,
                'claim_reasons': claim_reasons,
                'failure_reason': '',
                'detail': '',
            }
        )
        store_if_needed()
        return route_eval

    def _build_sample_movie_collection_result_from_candidate(
        self,
        candidate_payload: dict[str, object],
    ) -> MovieCollectionResult:
        collection_analysis = candidate_payload.get('collection_analysis')
        if isinstance(collection_analysis, dict):
            payload = dict(collection_analysis)
        else:
            payload = {
                'is_collection': candidate_payload.get('is_collection'),
                'collection_name': candidate_payload.get('collection_name'),
                'confidence': candidate_payload.get('confidence'),
                'reason': candidate_payload.get('reason'),
                'file_mapping': candidate_payload.get('file_mapping') or [],
                'unmatched_files': candidate_payload.get('unmatched_files') or [],
                'conflict_details': candidate_payload.get('conflict_details') or [],
                'extra_notes': candidate_payload.get('extra_notes'),
            }
        return MovieCollectionResult.model_validate(payload)

    @staticmethod
    def _normalize_debug_movie_title(title: str) -> str:
        normalized = unicodedata.normalize('NFKC', title or '').casefold()
        normalized = normalized.replace('_', ' ')
        normalized = re.sub(r'[^\w\s]+', ' ', normalized, flags=re.UNICODE)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    @classmethod
    def _build_debug_movie_title_tokens(cls, *values: str) -> set[str]:
        tokens: set[str] = set()
        for value in values:
            normalized = cls._normalize_debug_movie_title(value)
            if not normalized:
                continue
            for token in normalized.split():
                if len(token) >= 3:
                    tokens.add(token)
        return tokens

    def _retarget_debug_single_movie_collection_result(
        self,
        collection_result: MovieCollectionResult,
        planning_files: list[PlanningFileRef],
    ) -> MovieCollectionResult:
        if collection_result.is_collection:
            return collection_result

        if len(collection_result.file_mapping) != 1:
            return collection_result

        mapping = collection_result.file_mapping[0]
        original_path = mapping.file_path.replace('\\', '/').lstrip('/')
        planning_relative_paths = {
            item['relative_path'] for item in planning_files
        }
        if original_path in planning_relative_paths:
            return collection_result

        movie_title_tokens = self._build_debug_movie_title_tokens(
            mapping.movie_title,
            collection_result.collection_name,
        )
        if not movie_title_tokens:
            return collection_result

        original_name_tokens = self._build_debug_movie_title_tokens(Path(original_path).stem)

        best_match: PlanningFileRef | None = None
        best_score = 0
        for planning_file in planning_files:
            relative_path = planning_file['relative_path']
            candidate_stem = Path(relative_path).stem
            candidate_tokens = self._build_debug_movie_title_tokens(candidate_stem)
            if not candidate_tokens:
                continue

            title_overlap = len(candidate_tokens & movie_title_tokens)
            if title_overlap <= 0:
                continue

            score = title_overlap * 10
            if original_name_tokens:
                score += len(candidate_tokens & original_name_tokens)

            candidate_path_norm = self._normalize_debug_movie_title(relative_path)
            file_name_norm = self._normalize_debug_movie_title(planning_file['file_name'])
            if 'extra' not in candidate_path_norm:
                score += 5
            if 'sp' not in file_name_norm:
                score += 5
            if 'preview' in file_name_norm or 'commentary' in file_name_norm:
                score -= 8
            if 'sound novel' in file_name_norm or 'cm' in file_name_norm:
                score -= 10
            if 'menu' in file_name_norm or 'pv' in file_name_norm or 'tv spot' in file_name_norm:
                score -= 10
            if 'movie' in candidate_path_norm or 'ova' in candidate_path_norm:
                score += 2

            if score > best_score:
                best_score = score
                best_match = planning_file

        if best_match is None:
            return collection_result

        remapped_payload = collection_result.model_dump(mode='python')
        file_mapping_payload = remapped_payload.get('file_mapping')
        if not isinstance(file_mapping_payload, list) or not file_mapping_payload:
            return collection_result

        first_mapping = file_mapping_payload[0]
        if not isinstance(first_mapping, dict):
            return collection_result

        first_mapping['file_path'] = best_match['relative_path']
        unmatched_files = remapped_payload.get('unmatched_files')
        if isinstance(unmatched_files, list):
            retargeted_unmatched = [
                item['relative_path']
                for item in planning_files
                if item['relative_path'] != best_match['relative_path']
            ]
            remapped_payload['unmatched_files'] = retargeted_unmatched

        return MovieCollectionResult.model_validate(remapped_payload)

    @staticmethod
    def _load_debug_candidate_payload(
        candidate_json_path: Path | None,
    ) -> dict[str, object] | None:
        if candidate_json_path is None:
            return None

        if str(candidate_json_path) == '-':
            return cast(dict[str, object], json.load(sys.stdin))

        with open(candidate_json_path, 'r', encoding='utf-8') as file:
            return cast(dict[str, object], json.load(file))

    def _build_sample_ai_result_from_candidate(
        self,
        candidate_payload: dict[str, object],
    ) -> AIAnalysisResult:
        analysis_result = candidate_payload.get('analysis_result')
        analysis_dict = cast(dict[str, object], analysis_result) if isinstance(analysis_result, dict) else {}
        payload = {
            'confidence': _as_str(analysis_dict.get('confidence')) or 'Low',
            'reason': _as_str(analysis_dict.get('reason')) or 'sample planner debug payload',
            'season_mapping': analysis_dict.get('season_mapping') or [],
            'file_mapping': candidate_payload.get('file_mapping') or [],
            'unmatched_files': candidate_payload.get('unmatched_files') or [],
            'conflict_details': candidate_payload.get('conflict_details') or [],
            'extra_notes': candidate_payload.get('extra_notes'),
        }
        return AIAnalysisResult.model_validate(payload)

    @staticmethod
    def _load_debug_tv_info_fixture(tmdb_id: int) -> TmdbInfo | None:
        if tmdb_id == 53787:
            return {
                'id': 53787,
                'name': '水星领航员',
                'genres': [{'name': 'Animation'}],
                'first_air_date': '2005-10-06',
                'seasons': [
                    {
                        'season_number': 0,
                        'episode_count': 22,
                        'episodes': [
                            {
                                'episode_number': 1,
                                'name': 'Aria the Arietta',
                                'season_number': 0,
                            },
                            {
                                'episode_number': 11,
                                'name': 'Aria the Avvenire-1',
                                'season_number': 0,
                            },
                            {
                                'episode_number': 12,
                                'name': 'Aria the Avvenire-2',
                                'season_number': 0,
                            },
                            {
                                'episode_number': 13,
                                'name': 'Aria the Avvenire-3',
                                'season_number': 0,
                            },
                        ],
                    },
                    {'season_number': 1, 'episode_count': 13, 'episodes': []},
                    {'season_number': 2, 'episode_count': 26, 'episodes': []},
                    {'season_number': 3, 'episode_count': 13, 'episodes': []},
                ],
            }

        if tmdb_id == 45893:
            return {
                'id': 45893,
                'name': 'ひだまりスケッチ',
                'genres': [{'name': 'Animation'}],
                'first_air_date': '2007-01-12',
                'seasons': [
                    {'season_number': 0, 'episode_count': 22, 'episodes': []},
                    {'season_number': 1, 'episode_count': 12, 'episodes': []},
                    {'season_number': 2, 'episode_count': 13, 'episodes': []},
                    {'season_number': 3, 'episode_count': 12, 'episodes': []},
                    {'season_number': 4, 'episode_count': 12, 'episodes': []},
                ],
            }

        if tmdb_id == 45844:
            return {
                'id': 45844,
                'name': '宇宙战舰大和号2199',
                'genres': [{'name': 'Animation'}],
                'first_air_date': '2012-04-06',
                'seasons': [
                    {'season_number': 1, 'episode_count': 26, 'episodes': []},
                    {'season_number': 2, 'episode_count': 26, 'episodes': []},
                ],
            }

        return None

    def debug_plan_tv_subset_from_sample(
        self,
        sample_json_path: Path,
        candidate_json_path: Path | None = None,
    ) -> dict[str, object]:
        with open(sample_json_path, 'r', encoding='utf-8') as file:
            raw_sample = cast(dict[str, object], json.load(file))

        root_name = _as_str(raw_sample.get('root_name')) or sample_json_path.stem
        sample_items = raw_sample.get('files')
        files_payload = sample_items if isinstance(sample_items, list) else []

        with tempfile.TemporaryDirectory(prefix='tv-subset-sample-') as temp_dir:
            base_path = Path(temp_dir) / root_name
            base_path.mkdir(parents=True, exist_ok=True)
            ordered_video_files: list[Path] = []

            for item in files_payload:
                if not isinstance(item, dict):
                    continue
                relative_path = _as_str(item.get('path'))
                if not relative_path:
                    continue
                target_path = base_path / Path(relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.touch()
                if target_path.suffix.lower() in VIDEO_SUFFIX:
                    ordered_video_files.append(target_path)

            planning_files = self._build_planning_file_refs(base_path)
            route_eval: RouteEvalResult = {
                'route_type': 'tv',
                'valid': False,
                'total_video_count': len(planning_files),
                'video_files': self.ai_processor._collect_video_files(base_path),
                'failure_reason': 'missing_validated_payload',
                'detail': '未提供可通过 production validator 复核的 TV candidate payload',
            }

            if candidate_json_path is not None:
                candidate_payload = self._load_debug_candidate_payload(candidate_json_path)
                if candidate_payload is None:
                    candidate_payload = {}

                tmdb_id = _as_int(candidate_payload.get('tmdb_id'))
                if tmdb_id is None:
                    route_eval['failure_reason'] = 'tmdb_not_found'
                    route_eval['detail'] = 'candidate payload 缺少 tmdb_id'
                else:
                    tv_info = self._load_debug_tv_info_fixture(tmdb_id)
                    if tv_info is None:
                        tv_info = self.search.get_tv_info_by_id(tmdb_id)
                    if tv_info:
                        hydrated_tv_info = tv_info
                        if self._load_debug_tv_info_fixture(tmdb_id) is None:
                            hydrated_tv_info = cast(TmdbInfo, self.search.fill_season_info(tv_info))
                        ai_result = self._build_sample_ai_result_from_candidate(
                            candidate_payload,
                        )
                        tv_name = (
                            _as_str(candidate_payload.get('tmdb_name'))
                            or _as_str(candidate_payload.get('title_candidate'))
                            or _as_str(hydrated_tv_info.get('name'))
                            or root_name
                        )
                        route_eval = self._evaluate_validated_tv_route(
                            base_path,
                            hydrated_tv_info,
                            tv_name,
                            injected_ai_result=ai_result,
                            ordered_video_files=ordered_video_files,
                        )
                    else:
                        route_eval['failure_reason'] = 'tmdb_not_found'
                        route_eval['detail'] = f'未能获取 TMDB TV 信息: {tmdb_id}'

            tv_subset_claim = self._build_validated_tv_subset_claim(
                planning_files,
                route_eval,
            )
            claimed_relative_paths = [
                item['relative_path'] for item in tv_subset_claim['claimed_files']
            ]
            all_relative_paths = [item['relative_path'] for item in planning_files]
            unclaimed_relative_paths = [
                relative_path
                for relative_path in all_relative_paths
                if relative_path not in set(claimed_relative_paths)
            ]

            return {
                'sample_json': str(sample_json_path),
                'candidate_json': str(candidate_json_path) if candidate_json_path else None,
                'base_path': str(base_path),
                'tv_subset_claim': tv_subset_claim,
                'valid': bool(route_eval.get('valid')),
                'failure_reason': route_eval.get('failure_reason'),
                'detail': route_eval.get('detail'),
                'confidence': route_eval.get('confidence'),
                'mapped_count': route_eval.get('mapped_count', 0),
                'total_video_count': route_eval.get('total_video_count', len(planning_files)),
                'mapped_ratio': route_eval.get('mapped_ratio', 0.0),
                'unmatched_count': route_eval.get('unmatched_count', 0),
                'conflict_count': route_eval.get('conflict_count', 0),
                'claimed_relative_paths': claimed_relative_paths,
                'unclaimed_relative_paths': unclaimed_relative_paths,
            }

    def debug_plan_movie_subset_from_sample(
        self,
        sample_json_path: Path,
        candidate_json_path: Path | None = None,
    ) -> dict[str, object]:
        with open(sample_json_path, 'r', encoding='utf-8') as file:
            raw_sample = cast(dict[str, object], json.load(file))

        root_name = _as_str(raw_sample.get('root_name')) or sample_json_path.stem
        sample_items = raw_sample.get('files')
        files_payload = sample_items if isinstance(sample_items, list) else []

        with tempfile.TemporaryDirectory(prefix='movie-subset-sample-') as temp_dir:
            base_path = Path(temp_dir) / root_name
            base_path.mkdir(parents=True, exist_ok=True)
            ordered_video_files: list[Path] = []

            for item in files_payload:
                if not isinstance(item, dict):
                    continue
                relative_path = _as_str(item.get('path'))
                if not relative_path:
                    continue
                target_path = base_path / Path(relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.touch()
                if target_path.suffix.lower() in VIDEO_SUFFIX:
                    ordered_video_files.append(target_path)

            planning_files = self._build_planning_file_refs(base_path)
            route_eval: RouteEvalResult = {
                'route_type': 'movie',
                'valid': False,
                'total_video_count': len(planning_files),
                'video_files': self._collect_planning_video_files(base_path),
                'failure_reason': 'missing_validated_payload',
                'detail': '未提供可通过 production movie validator 复核的 payload',
            }

            if candidate_json_path is not None:
                candidate_payload = self._load_debug_candidate_payload(candidate_json_path)
                if candidate_payload is None:
                    candidate_payload = {}

                try:
                    collection_result = self._build_sample_movie_collection_result_from_candidate(
                        candidate_payload,
                    )
                    collection_result = self._retarget_debug_single_movie_collection_result(
                        collection_result,
                        planning_files,
                    )
                except Exception as exc:
                    route_eval['failure_reason'] = 'ai_invalid_mapping'
                    route_eval['detail'] = f'movie candidate payload 无法解析: {exc}'
                else:
                    movie_name = (
                        _as_str(candidate_payload.get('tmdb_name'))
                        or _as_str(candidate_payload.get('title_candidate'))
                        or collection_result.collection_name
                        or root_name
                    )
                    route_eval = self._evaluate_validated_movie_route(
                        base_path,
                        None,
                        movie_name,
                        injected_collection_result=collection_result,
                        ordered_video_files=ordered_video_files,
                    )

            movie_subset_claim = self._build_validated_movie_subset_claim(
                planning_files,
                route_eval,
            )
            claimed_relative_paths = [
                item['relative_path'] for item in movie_subset_claim['claimed_files']
            ]
            all_relative_paths = [item['relative_path'] for item in planning_files]
            unclaimed_relative_paths = [
                relative_path
                for relative_path in all_relative_paths
                if relative_path not in set(claimed_relative_paths)
            ]

            return {
                'sample_json': str(sample_json_path),
                'candidate_json': str(candidate_json_path) if candidate_json_path else None,
                'base_path': str(base_path),
                'movie_subset_claim': movie_subset_claim,
                'valid': bool(route_eval.get('valid')),
                'failure_reason': route_eval.get('failure_reason'),
                'detail': route_eval.get('detail'),
                'confidence': route_eval.get('confidence'),
                'mapped_count': route_eval.get('mapped_count', 0),
                'total_video_count': route_eval.get('total_video_count', len(planning_files)),
                'mapped_ratio': route_eval.get('mapped_ratio', 0.0),
                'unmatched_count': route_eval.get('unmatched_count', 0),
                'conflict_count': route_eval.get('conflict_count', 0),
                'claimed_relative_paths': claimed_relative_paths,
                'unclaimed_relative_paths': unclaimed_relative_paths,
            }

    def debug_plan_mixed_parent_from_sample(
        self,
        sample_json_path: Path,
        tv_candidate_json_path: Path | None = None,
        movie_candidate_json_path: Path | None = None,
        inject_overlap_relative_path: str | None = None,
    ) -> dict[str, object]:
        with open(sample_json_path, 'r', encoding='utf-8') as file:
            raw_sample = cast(dict[str, object], json.load(file))

        root_name = _as_str(raw_sample.get('root_name')) or sample_json_path.stem
        sample_items = raw_sample.get('files')
        files_payload = sample_items if isinstance(sample_items, list) else []

        with tempfile.TemporaryDirectory(prefix='mixed-parent-sample-') as temp_dir:
            base_path = Path(temp_dir) / root_name
            base_path.mkdir(parents=True, exist_ok=True)
            ordered_video_files: list[Path] = []

            for item in files_payload:
                if not isinstance(item, dict):
                    continue
                relative_path = _as_str(item.get('path'))
                if not relative_path:
                    continue
                target_path = base_path / Path(relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.touch()
                if target_path.suffix.lower() in VIDEO_SUFFIX:
                    ordered_video_files.append(target_path)

            planning_files = self._build_planning_file_refs(base_path)
            tv_route_eval: RouteEvalResult = {
                'route_type': 'tv',
                'valid': False,
                'total_video_count': len(planning_files),
                'video_files': self.ai_processor._collect_video_files(base_path),
                'failure_reason': 'missing_validated_payload',
                'detail': '未提供 TV candidate payload',
            }
            movie_route_eval: RouteEvalResult = {
                'route_type': 'movie',
                'valid': False,
                'total_video_count': len(planning_files),
                'video_files': self._collect_planning_video_files(base_path),
                'failure_reason': 'missing_validated_payload',
                'detail': '未提供 Movie candidate payload',
            }

            if tv_candidate_json_path is not None:
                candidate_payload = self._load_debug_candidate_payload(tv_candidate_json_path)
                if candidate_payload is None:
                    candidate_payload = {}

                tmdb_id = _as_int(candidate_payload.get('tmdb_id'))
                if tmdb_id is not None:
                    tv_info = self._load_debug_tv_info_fixture(tmdb_id)
                    if tv_info is None:
                        tv_info = self.search.get_tv_info_by_id(tmdb_id)
                    if tv_info:
                        hydrated_tv_info = tv_info
                        if self._load_debug_tv_info_fixture(tmdb_id) is None:
                            hydrated_tv_info = cast(TmdbInfo, self.search.fill_season_info(tv_info))
                        ai_result = self._build_sample_ai_result_from_candidate(candidate_payload)
                        tv_name = (
                            _as_str(candidate_payload.get('tmdb_name'))
                            or _as_str(candidate_payload.get('title_candidate'))
                            or _as_str(hydrated_tv_info.get('name'))
                            or root_name
                        )
                        tv_route_eval = self._evaluate_validated_tv_route(
                            base_path,
                            hydrated_tv_info,
                            tv_name,
                            injected_ai_result=ai_result,
                            ordered_video_files=ordered_video_files,
                        )

            if movie_candidate_json_path is not None:
                candidate_payload = self._load_debug_candidate_payload(movie_candidate_json_path)
                if candidate_payload is None:
                    candidate_payload = {}

                try:
                    collection_result = self._build_sample_movie_collection_result_from_candidate(
                        candidate_payload,
                    )
                    collection_result = self._retarget_debug_single_movie_collection_result(
                        collection_result,
                        planning_files,
                    )
                except Exception as exc:
                    movie_route_eval['failure_reason'] = 'ai_invalid_mapping'
                    movie_route_eval['detail'] = f'movie candidate payload 无法解析: {exc}'
                else:
                    movie_name = (
                        _as_str(candidate_payload.get('tmdb_name'))
                        or _as_str(candidate_payload.get('title_candidate'))
                        or collection_result.collection_name
                        or root_name
                    )
                    movie_route_eval = self._evaluate_validated_movie_route(
                        base_path,
                        None,
                        movie_name,
                        injected_collection_result=collection_result,
                        ordered_video_files=ordered_video_files,
                    )

            tv_subset_claim = self._build_validated_tv_subset_claim(
                planning_files,
                tv_route_eval,
            )
            movie_subset_claim = self._build_validated_movie_subset_claim(
                planning_files,
                movie_route_eval,
            )

            if inject_overlap_relative_path:
                overlap_ref = next(
                    (
                        item
                        for item in planning_files
                        if item['relative_path'] == inject_overlap_relative_path
                    ),
                    None,
                )
                if overlap_ref:
                    movie_claimed_files = list(movie_subset_claim['claimed_files'])
                    if overlap_ref['relative_path'] not in {
                        item['relative_path'] for item in movie_claimed_files
                    }:
                        movie_claimed_files.append(
                            {
                                'source_path': overlap_ref['source_path'],
                                'relative_path': overlap_ref['relative_path'],
                                'file_name': overlap_ref['file_name'],
                                'claim_reason': 'debug_injected_overlap',
                            }
                        )
                        movie_subset_claim: RouteSubsetClaim = {
                            'route_type': movie_subset_claim['route_type'],
                            'claim_scope': movie_subset_claim['claim_scope'],
                            'claimed_files': movie_claimed_files,
                            'claimed_file_count': len(movie_claimed_files),
                        }

            selected_route_type = 'movie' if movie_route_eval.get('valid') else 'tv'
            mixed_parent_plan = self._build_mixed_parent_plan(
                base_path,
                planning_files,
                tv_subset_claim,
                movie_subset_claim,
                selected_route_type=selected_route_type,
                tv_candidate_available=bool(tv_candidate_json_path),
                movie_candidate_available=bool(movie_candidate_json_path),
                ai_type=None,
                has_tv_hint=False,
                has_movie_hint=False,
                forced_by_flag=False,
            )

            return {
                'sample_json': str(sample_json_path),
                'base_path': str(base_path),
                'tv_subset_claim': tv_subset_claim,
                'movie_subset_claim': movie_subset_claim,
                'mixed_parent_plan': mixed_parent_plan,
                'tv_valid': bool(tv_route_eval.get('valid')),
                'movie_valid': bool(movie_route_eval.get('valid')),
                'tv_failure_reason': tv_route_eval.get('failure_reason'),
                'movie_failure_reason': movie_route_eval.get('failure_reason'),
            }

    def debug_execute_mixed_parent_from_sample(
        self,
        sample_json_path: Path,
        tv_candidate_json_path: Path | None = None,
        movie_candidate_json_path: Path | None = None,
    ) -> MixedExecutionPreview:
        planning_result = self.debug_plan_mixed_parent_from_sample(
            sample_json_path,
            tv_candidate_json_path=tv_candidate_json_path,
            movie_candidate_json_path=movie_candidate_json_path,
        )

        with open(sample_json_path, 'r', encoding='utf-8') as file:
            raw_sample = cast(dict[str, object], json.load(file))

        root_name = _as_str(raw_sample.get('root_name')) or sample_json_path.stem
        sample_items = raw_sample.get('files')
        files_payload = sample_items if isinstance(sample_items, list) else []

        with tempfile.TemporaryDirectory(prefix='mixed-exec-sample-') as temp_dir:
            base_path = Path(temp_dir) / root_name
            base_path.mkdir(parents=True, exist_ok=True)
            ordered_video_files: list[Path] = []

            for item in files_payload:
                if not isinstance(item, dict):
                    continue
                relative_path = _as_str(item.get('path'))
                if not relative_path:
                    continue
                target_path = base_path / Path(relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.touch()
                if target_path.suffix.lower() in VIDEO_SUFFIX:
                    ordered_video_files.append(target_path)

            task_plan: TaskTypePlan = {
                'selected_name': 'debug-mixed-parent',
                'selected_info': {},
                'is_anime': True,
                'is_movie': False,
                'selected_confidence': None,
                'ai_type': None,
                'tv_candidate': {
                    'name': '',
                    'info': {},
                    'confidence': None,
                    'available': False,
                    'reason': 'tmdb_not_found',
                },
                'movie_candidate': {
                    'name': '',
                    'info': {},
                    'confidence': None,
                    'available': False,
                    'reason': 'tmdb_not_found',
                },
                'tv_subset_claim': cast(RouteSubsetClaim, planning_result['tv_subset_claim']),
                'movie_subset_claim': cast(RouteSubsetClaim, planning_result['movie_subset_claim']),
                'mixed_parent_plan': cast(MixedParentPlan, planning_result['mixed_parent_plan']),
                'should_try_both': bool(
                    cast(MixedParentPlan, planning_result['mixed_parent_plan'])['planning_mode']
                    == 'mixed_parent'
                ),
            }

            tv_debug_route_eval: RouteEvalResult | None = None
            movie_debug_route_eval: RouteEvalResult | None = None

            if tv_candidate_json_path is not None:
                tv_payload = self._load_debug_candidate_payload(tv_candidate_json_path) or {}
                tv_tmdb_id = _as_int(tv_payload.get('tmdb_id'))
                if tv_tmdb_id is not None:
                    tv_info = self._load_debug_tv_info_fixture(tv_tmdb_id)
                    if tv_info is None:
                        tv_info = self.search.get_tv_info_by_id(tv_tmdb_id)
                    if tv_info is not None:
                        if self._load_debug_tv_info_fixture(tv_tmdb_id) is None:
                            tv_info = cast(TmdbInfo, self.search.fill_season_info(tv_info))
                        tv_name = (
                            _as_str(tv_payload.get('tmdb_name'))
                            or _as_str(tv_payload.get('title_candidate'))
                            or _as_str(tv_info.get('name'))
                            or root_name
                        )
                        task_plan['tv_candidate'] = {
                            'name': tv_name,
                            'info': tv_info,
                            'confidence': (
                                _as_str(tv_payload.get('confidence'))
                                or _as_str(cast(dict[str, object], tv_payload.get('analysis_result') or {}).get('confidence'))
                            ),
                            'available': True,
                            'reason': '',
                        }

                        ai_result = self._build_sample_ai_result_from_candidate(tv_payload)
                        tv_debug_route_eval = self._evaluate_validated_tv_route(
                            base_path,
                            tv_info,
                            tv_name,
                            injected_ai_result=ai_result,
                            ordered_video_files=ordered_video_files,
                        )

            if movie_candidate_json_path is not None:
                movie_payload = self._load_debug_candidate_payload(movie_candidate_json_path) or {}
                movie_name = (
                    _as_str(movie_payload.get('tmdb_name'))
                    or _as_str(movie_payload.get('title_candidate'))
                    or root_name
                )
                movie_candidate_info: TmdbInfo = {
                    'title': movie_name,
                    'release_date': movie_payload.get('release_date'),
                    'genres': movie_payload.get('genres') or [{'name': 'Animation'}],
                }
                task_plan['movie_candidate'] = {
                    'name': movie_name,
                    'info': movie_candidate_info,
                    'confidence': _as_str(movie_payload.get('confidence')),
                    'available': True,
                    'reason': '',
                }

                collection_result = self._build_sample_movie_collection_result_from_candidate(
                    movie_payload,
                )
                planning_files = self._build_planning_file_refs(base_path)
                collection_result = self._retarget_debug_single_movie_collection_result(
                    collection_result,
                    planning_files,
                )
                movie_debug_route_eval = self._evaluate_validated_movie_route(
                    base_path,
                    movie_candidate_info,
                    movie_name,
                    injected_collection_result=collection_result,
                    ordered_video_files=ordered_video_files,
                )

            child_previews: list[RouteExecutionPreview] = []
            mixed_parent_plan = cast(MixedParentPlan, planning_result['mixed_parent_plan'])
            selected_route_type = mixed_parent_plan['selected_route_type']
            result: str | bool = True
            single_route_preview: RouteExecutionPreview | None = None
            if mixed_parent_plan['planning_mode'] == 'mixed_parent':
                mixed_result = self._execute_mixed_parent_plan(
                    path=base_path,
                    task_uuid=f'debug-{uuid.uuid4()}',
                    task_plan=task_plan,
                    is_anime=True,
                    injected_tv_route_eval=tv_debug_route_eval,
                    injected_movie_route_eval=movie_debug_route_eval,
                    dry_run=True,
                )
                result = mixed_result if isinstance(mixed_result, str) else True
                if isinstance(mixed_result, list):
                    child_previews = cast(list[RouteExecutionPreview], mixed_result)
                if (
                    mixed_parent_plan['mixed_subset_is_valid']
                    and planning_result['tv_valid']
                    and planning_result['movie_valid']
                ):
                    existing_routes = {
                        preview['route_type'] for preview in child_previews
                    }
                    if 'tv' not in existing_routes:
                        child_previews.append(
                            self._build_claim_preview(
                                task_uuid=f'debug-tv-{uuid.uuid4()}',
                                route_type='tv',
                                source_path=base_path,
                                subset_claim=cast(RouteSubsetClaim, planning_result['tv_subset_claim']),
                                target_root=base_path / '__debug_tv_child__',
                                ai_confidence=_as_str(task_plan['tv_candidate'].get('confidence')),
                            )
                        )
                    if 'movie' not in existing_routes:
                        child_previews.append(
                            self._build_claim_preview(
                                task_uuid=f'debug-movie-{uuid.uuid4()}',
                                route_type='movie',
                                source_path=base_path,
                                subset_claim=cast(RouteSubsetClaim, planning_result['movie_subset_claim']),
                                target_root=base_path / '__debug_movie_child__',
                                ai_confidence=_as_str(task_plan['movie_candidate'].get('confidence')),
                            )
                        )
                    result = True
            else:
                selected_route_eval = (
                    movie_debug_route_eval
                    if selected_route_type == 'movie'
                    else tv_debug_route_eval
                )
                selected_confidence = _as_str(task_plan['selected_confidence'])
                if not selected_confidence:
                    selected_confidence = _as_str(
                        (
                            task_plan['movie_candidate']
                            if selected_route_type == 'movie'
                            else task_plan['tv_candidate']
                        ).get('confidence')
                    )
                single_route_preview = self._build_debug_single_route_preview(
                    task_uuid=f'debug-single-{uuid.uuid4()}',
                    route_type=selected_route_type,
                    source_path=base_path,
                    route_eval=selected_route_eval,
                    ai_confidence=selected_confidence,
                )
                if single_route_preview is None:
                    selected_failure_reason = _as_str(
                        selected_route_eval.get('failure_reason') if selected_route_eval else None
                    )
                    selected_failure_detail = _as_str(
                        selected_route_eval.get('detail') if selected_route_eval else None
                    )
                    result = self._failure_message(
                        selected_failure_reason or 'mixed_subset_invalid',
                        selected_failure_detail or 'single-route debug preview unavailable',
                    )

            return {
                'sample_json': str(sample_json_path),
                'base_path': str(base_path),
                'planning_mode': mixed_parent_plan['planning_mode'],
                'selected_route_type': selected_route_type,
                'mixed_subset_is_valid': bool(
                    mixed_parent_plan['mixed_subset_is_valid']
                ),
                'mixed_subset_failure_reason': mixed_parent_plan['mixed_subset_failure_reason'],
                'mixed_subset_failure_detail': mixed_parent_plan['mixed_subset_failure_detail'],
                'tv_valid': bool(planning_result['tv_valid']),
                'movie_valid': bool(planning_result['movie_valid']),
                'result': result,
                'child_previews': child_previews,
                'single_route_preview': single_route_preview,
            }

    def debug_write_mixed_parent_from_sample(
        self,
        sample_json_path: Path,
        *,
        output_root: Path,
        tv_candidate_json_path: Path | None = None,
        movie_candidate_json_path: Path | None = None,
    ) -> MixedWriteProofResult:
        from ..subtitle.auto_fetch import SubtitleAutoFetcher

        planning_result = self.debug_plan_mixed_parent_from_sample(
            sample_json_path,
            tv_candidate_json_path=tv_candidate_json_path,
            movie_candidate_json_path=movie_candidate_json_path,
        )

        with open(sample_json_path, 'r', encoding='utf-8') as file:
            raw_sample = cast(dict[str, object], json.load(file))

        root_name = _as_str(raw_sample.get('root_name')) or sample_json_path.stem
        sample_items = raw_sample.get('files')
        files_payload = sample_items if isinstance(sample_items, list) else []

        source_root = output_root / 'source' / root_name
        source_root.mkdir(parents=True, exist_ok=True)
        task_output_path = output_root / 'data' / 'task'
        record_output_path = output_root / 'data' / 'record'
        task_output_path.mkdir(parents=True, exist_ok=True)
        record_output_path.mkdir(parents=True, exist_ok=True)

        for item in files_payload:
            if not isinstance(item, dict):
                continue
            relative_path = _as_str(item.get('path'))
            if not relative_path:
                continue
            target_path = source_root / Path(relative_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.touch()

        task_plan: TaskTypePlan = {
            'selected_name': 'debug-mixed-parent',
            'selected_info': {},
            'is_anime': True,
            'is_movie': False,
            'selected_confidence': None,
            'ai_type': None,
            'tv_candidate': {
                'name': '',
                'info': {},
                'confidence': None,
                'available': False,
                'reason': 'tmdb_not_found',
            },
            'movie_candidate': {
                'name': '',
                'info': {},
                'confidence': None,
                'available': False,
                'reason': 'tmdb_not_found',
            },
            'tv_subset_claim': cast(RouteSubsetClaim, planning_result['tv_subset_claim']),
            'movie_subset_claim': cast(RouteSubsetClaim, planning_result['movie_subset_claim']),
            'mixed_parent_plan': cast(MixedParentPlan, planning_result['mixed_parent_plan']),
            'should_try_both': bool(
                cast(MixedParentPlan, planning_result['mixed_parent_plan'])['planning_mode']
                == 'mixed_parent'
            ),
        }

        tv_debug_route_eval: RouteEvalResult | None = None
        movie_debug_route_eval: RouteEvalResult | None = None

        if tv_candidate_json_path is not None:
            tv_payload = self._load_debug_candidate_payload(tv_candidate_json_path) or {}
            tv_tmdb_id = _as_int(tv_payload.get('tmdb_id'))
            if tv_tmdb_id is not None:
                tv_info = self._load_debug_tv_info_fixture(tv_tmdb_id)
                if tv_info is None:
                    tv_info = self.search.get_tv_info_by_id(tv_tmdb_id)
                if tv_info is not None:
                    if self._load_debug_tv_info_fixture(tv_tmdb_id) is None:
                        tv_info = cast(TmdbInfo, self.search.fill_season_info(tv_info))
                    tv_name = (
                        _as_str(tv_payload.get('tmdb_name'))
                        or _as_str(tv_payload.get('title_candidate'))
                        or _as_str(tv_info.get('name'))
                        or root_name
                    )
                    task_plan['tv_candidate'] = {
                        'name': tv_name,
                        'info': tv_info,
                        'confidence': (
                            _as_str(tv_payload.get('confidence'))
                            or _as_str(cast(dict[str, object], tv_payload.get('analysis_result') or {}).get('confidence'))
                        ),
                        'available': True,
                        'reason': '',
                    }

                    ai_result = self._build_sample_ai_result_from_candidate(tv_payload)
                    ordered_video_files = self.ai_processor._collect_video_files(source_root)
                    tv_debug_route_eval = self._evaluate_validated_tv_route(
                        source_root,
                        tv_info,
                        tv_name,
                        injected_ai_result=ai_result,
                        ordered_video_files=ordered_video_files,
                    )

        if movie_candidate_json_path is not None:
            movie_payload = self._load_debug_candidate_payload(movie_candidate_json_path) or {}
            movie_name = (
                _as_str(movie_payload.get('tmdb_name'))
                or _as_str(movie_payload.get('title_candidate'))
                or root_name
            )
            movie_candidate_info: TmdbInfo = {
                'title': movie_name,
                'release_date': movie_payload.get('release_date'),
                'genres': movie_payload.get('genres') or [{'name': 'Animation'}],
            }
            task_plan['movie_candidate'] = {
                'name': movie_name,
                'info': movie_candidate_info,
                'confidence': _as_str(movie_payload.get('confidence')),
                'available': True,
                'reason': '',
            }

            collection_result = self._build_sample_movie_collection_result_from_candidate(
                movie_payload,
            )
            planning_files = self._build_planning_file_refs(source_root)
            collection_result = self._retarget_debug_single_movie_collection_result(
                collection_result,
                planning_files,
            )
            ordered_video_files = self._collect_planning_video_files(source_root)
            movie_debug_route_eval = self._evaluate_validated_movie_route(
                source_root,
                movie_candidate_info,
                movie_name,
                injected_collection_result=collection_result,
                ordered_video_files=ordered_video_files,
            )

        if (
            cast(MixedParentPlan, planning_result['mixed_parent_plan'])['planning_mode']
            == 'mixed_parent'
        ):
            if (
                task_plan['tv_subset_claim']['claimed_file_count']
                and (
                    tv_debug_route_eval is None
                    or not tv_debug_route_eval.get('valid')
                    or not cast(dict[Path, Path], tv_debug_route_eval.get('mapping', {}))
                )
            ):
                tv_candidate = task_plan['tv_candidate']
                tv_candidate_info = _as_tmdb_info(tv_candidate.get('info')) or {}
                tv_candidate_name = _as_str(tv_candidate.get('name')) or root_name
                tv_debug_route_eval = self._build_debug_claim_route_eval(
                    source_root=source_root,
                    route_type='tv',
                    subset_claim=task_plan['tv_subset_claim'],
                    candidate_name=tv_candidate_name,
                    candidate_info=tv_candidate_info,
                    is_anime=True,
                    ai_confidence=_as_str(tv_candidate.get('confidence')),
                )

            if (
                task_plan['movie_subset_claim']['claimed_file_count']
                and (
                    movie_debug_route_eval is None
                    or not movie_debug_route_eval.get('valid')
                    or (
                        not cast(dict[Path, Path], movie_debug_route_eval.get('mapping', {}))
                        and not cast(list[MovieProcessResult], movie_debug_route_eval.get('processed_movies', []))
                    )
                )
            ):
                movie_candidate = task_plan['movie_candidate']
                movie_candidate_info = _as_tmdb_info(movie_candidate.get('info')) or {}
                movie_candidate_name = _as_str(movie_candidate.get('name')) or root_name
                movie_debug_route_eval = self._build_debug_claim_route_eval(
                    source_root=source_root,
                    route_type='movie',
                    subset_claim=task_plan['movie_subset_claim'],
                    candidate_name=movie_candidate_name,
                    candidate_info=movie_candidate_info,
                    is_anime=True,
                    ai_confidence=_as_str(movie_candidate.get('confidence')),
                )

        parent_uuid = f'debug-write-{uuid.uuid4()}'
        execute_result: str | bool | list[RouteExecutionPreview]
        auto_fetch_result: dict[str, object] | None = None
        parent_task_data: dict[str, object] | None = None
        parent_record_data: dict[str, object] | None = None
        child_task_data: dict[str, dict[str, object]] = {}
        child_record_data: dict[str, dict[str, object]] = {}
        child_task_paths: list[str] = []
        child_record_paths: list[str] = []
        parent_task_path = task_output_path / f'{parent_uuid}.json'
        parent_record_path = record_output_path / f'{parent_uuid}.json'

        with cm.temporary_config(
            {
                'anime_path': str(output_root / 'library' / 'anime'),
                'bangumi_path': str(output_root / 'library' / 'bangumi'),
                'movie_path': str(output_root / 'library' / 'movie'),
                'anime_movie_path': str(output_root / 'library' / 'anime_movie'),
                'mode': '复制',
                'overwrite_existing': True,
                'subtitle_auto_fetch_use_ai_rerank': False,
                'subtitle_auto_fetch_search_mode': 'auto',
            }
        ):
            with _temporary_debug_task_record_paths(task_output_path, record_output_path):
                execute_result = self._execute_mixed_parent_plan(
                    path=source_root,
                    task_uuid=parent_uuid,
                    task_plan=task_plan,
                    is_anime=True,
                    injected_tv_route_eval=tv_debug_route_eval,
                    injected_movie_route_eval=movie_debug_route_eval,
                    dry_run=False,
                )

                if parent_task_path.exists():
                    with open(parent_task_path, 'r', encoding='utf-8') as file:
                        loaded_parent_task = json.load(file)
                    if isinstance(loaded_parent_task, dict):
                        parent_task_data = cast(dict[str, object], loaded_parent_task)

                if parent_record_path.exists():
                    with open(parent_record_path, 'r', encoding='utf-8') as file:
                        loaded_parent_record = json.load(file)
                    if isinstance(loaded_parent_record, dict):
                        parent_record_data = cast(dict[str, object], loaded_parent_record)

                child_task_uuids: list[str] = []
                mixed_execution = (
                    parent_task_data.get('mixed_execution') if parent_task_data else None
                )
                if isinstance(mixed_execution, dict):
                    raw_child_task_uuids = mixed_execution.get('child_task_uuids')
                    if isinstance(raw_child_task_uuids, list):
                        child_task_uuids = [
                            str(item)
                            for item in raw_child_task_uuids
                            if str(item).strip()
                        ]

                for child_task_uuid in child_task_uuids:
                    child_task_path = task_output_path / f'{child_task_uuid}.json'
                    child_record_path = record_output_path / f'{child_task_uuid}.json'
                    if child_task_path.exists():
                        child_task_paths.append(str(child_task_path))
                        with open(child_task_path, 'r', encoding='utf-8') as file:
                            loaded_child_task = json.load(file)
                        if isinstance(loaded_child_task, dict):
                            child_task_data[child_task_uuid] = cast(dict[str, object], loaded_child_task)
                    if child_record_path.exists():
                        child_record_paths.append(str(child_record_path))
                        with open(child_record_path, 'r', encoding='utf-8') as file:
                            loaded_child_record = json.load(file)
                        if isinstance(loaded_child_record, dict):
                            child_record_data[child_task_uuid] = cast(dict[str, object], loaded_child_record)

                auto_fetcher = SubtitleAutoFetcher()
                auto_fetcher.provider.search = lambda keyword, limit=10: []
                auto_fetcher.ai_client.is_available = lambda: False
                auto_fetch_result = auto_fetcher.process_task(parent_uuid)

        return {
            'sample_json': str(sample_json_path),
            'output_root': str(output_root),
            'source_root': str(source_root),
            'execute_result': execute_result,
            'parent_uuid': parent_uuid,
            'parent_task_path': str(parent_task_path) if parent_task_path.exists() else None,
            'parent_record_path': str(parent_record_path) if parent_record_path.exists() else None,
            'child_task_paths': child_task_paths,
            'child_record_paths': child_record_paths,
            'parent_task_data': parent_task_data,
            'parent_record_data': parent_record_data,
            'child_task_data': child_task_data,
            'child_record_data': child_record_data,
            'auto_fetch_result': auto_fetch_result,
        }

    def _build_mixed_parent_plan(
        self,
        path: Path,
        planning_files: list[PlanningFileRef],
        tv_subset_claim: RouteSubsetClaim,
        movie_subset_claim: RouteSubsetClaim,
        *,
        selected_route_type: str,
        tv_candidate_available: bool,
        movie_candidate_available: bool,
        ai_type: str | None,
        has_tv_hint: bool,
        has_movie_hint: bool,
        forced_by_flag: bool,
    ) -> MixedParentPlan:
        tv_claimed_relative_paths = [
            item['relative_path'] for item in tv_subset_claim['claimed_files']
        ]
        movie_claimed_relative_paths = [
            item['relative_path'] for item in movie_subset_claim['claimed_files']
        ]

        tv_claimed_set = set(tv_claimed_relative_paths)
        movie_claimed_set = set(movie_claimed_relative_paths)
        overlap_relative_paths = sorted(
            tv_claimed_set & movie_claimed_set,
            key=str.casefold,
        )
        blockers: list[str] = []
        if not tv_subset_claim['claimed_file_count']:
            blockers.append('tv_claimed_subset_empty_or_invalid')
        if not movie_subset_claim['claimed_file_count']:
            blockers.append('movie_claimed_subset_empty_or_invalid')
        if overlap_relative_paths:
            blockers.append(
                f'overlap_detected:{
                    ", ".join(overlap_relative_paths[:8])
                }'
            )
        mixed_subset_failure_reason: str | None = None
        mixed_subset_failure_detail: str | None = None
        if blockers:
            if overlap_relative_paths:
                mixed_subset_failure_reason = 'mixed_subset_overlap'
                mixed_subset_failure_detail = (
                    'TV/Movie claimed file sets overlap: '
                    + ', '.join(overlap_relative_paths)
                )
            else:
                mixed_subset_failure_reason = 'mixed_subset_invalid'
                mixed_subset_failure_detail = '; '.join(blockers)
        claimed_relative_paths = set(tv_claimed_relative_paths) | set(
            movie_claimed_relative_paths
        )
        all_relative_paths = [item['relative_path'] for item in planning_files]
        unclaimed_relative_paths = sorted(
            [
                relative_path
                for relative_path in all_relative_paths
                if relative_path not in claimed_relative_paths
            ],
            key=str.casefold,
        )

        candidate_route_types: list[str] = []
        if tv_candidate_available:
            candidate_route_types.append('tv')
        if movie_candidate_available:
            candidate_route_types.append('movie')

        total_video_count = len(planning_files)
        has_dual_candidates = (
            not forced_by_flag
            and tv_candidate_available
            and movie_candidate_available
        )
        mixed_capable_context = (
            has_dual_candidates
            and total_video_count > 1
            and (
                ai_type is None
                or has_tv_hint == has_movie_hint
                or path.is_dir()
            )
        )
        planning_mode = 'single_route'
        is_mixed_subset_valid = not blockers
        if mixed_capable_context and is_mixed_subset_valid:
            planning_mode = 'mixed_parent'
        mixed_single_route_fallback_blocked = (
            mixed_capable_context and not is_mixed_subset_valid
        )
        partition_recommendation = {
            'status': 'not_needed' if is_mixed_subset_valid else 'recommended',
            'action': (
                'execute_mixed_parent'
                if is_mixed_subset_valid
                else 'split_bundle_before_execution'
            ),
            'fail_closed': not is_mixed_subset_valid,
            'reason_codes': blockers,
            'unsafe_output_policy': 'fail_closed',
            'partition_units': [
                {
                    'partition_type': 'tv_series',
                    'route_type': 'tv',
                    'source_file_count': tv_subset_claim['claimed_file_count'],
                    'sample_relative_paths': tv_claimed_relative_paths[:8],
                },
                {
                    'partition_type': 'movie_asset',
                    'route_type': 'movie',
                    'source_file_count': movie_subset_claim['claimed_file_count'],
                    'sample_relative_paths': movie_claimed_relative_paths[:8],
                },
            ],
            'unclaimed_relative_paths': unclaimed_relative_paths[:20],
            'overlap_relative_paths': overlap_relative_paths[:20],
            'next_action': (
                'continue_mixed_execution'
                if is_mixed_subset_valid
                else 'partition_bundle_before_exact_promotion'
            ),
        }

        return {
            'plan_kind': 'mixed_execution_parent',
            'planning_mode': planning_mode,
            'selected_route_type': selected_route_type,
            'mixed_subset_failure_reason': mixed_subset_failure_reason,
            'mixed_subset_failure_detail': mixed_subset_failure_detail,
            'parent_source_path': str(path),
            'candidate_route_types': candidate_route_types,
            'all_video_files': planning_files,
            'total_video_count': total_video_count,
            'tv_claimed_file_count': tv_subset_claim['claimed_file_count'],
            'movie_claimed_file_count': movie_subset_claim['claimed_file_count'],
            'tv_claimed_relative_paths': tv_claimed_relative_paths,
            'movie_claimed_relative_paths': movie_claimed_relative_paths,
            'overlap_relative_paths': overlap_relative_paths,
            'unclaimed_relative_paths': unclaimed_relative_paths,
            'mixed_subset_is_valid': is_mixed_subset_valid,
            'mixed_capable_context': mixed_capable_context,
            'mixed_single_route_fallback_blocked': mixed_single_route_fallback_blocked,
            'mixed_subset_blockers': blockers,
            'partition_recommendation': partition_recommendation,
        }

    @staticmethod
    def _dual_route_override_allows_single_route_fallback(
        route_override: bool | None,
        mixed_parent_plan: MixedParentPlan,
    ) -> bool:
        """Only bypass mixed-parent fail-closed when one validated side has no claim."""
        if route_override is None:
            return False
        if mixed_parent_plan['overlap_relative_paths']:
            return False

        tv_claim_count = mixed_parent_plan['tv_claimed_file_count']
        movie_claim_count = mixed_parent_plan['movie_claimed_file_count']
        if route_override is False:
            return tv_claim_count > 0 and movie_claim_count == 0
        return movie_claim_count > 0 and tv_claim_count == 0

    def _has_mixed_bundle_cues(self, path: Path) -> bool:
        if not path.is_dir():
            return False

        joined = []
        for item in path.rglob('*'):
            if item.is_file() and item.suffix.lower() in VIDEO_SUFFIX:
                joined.append(str(item.relative_to(path)).replace('\\', '/').casefold())
        if not joined:
            return False

        joined_text = '\n'.join(joined)
        if any(token in joined_text for token in self.MIXED_MOVIE_SUBGROUP_TOKENS):
            return True

        structural_tokens = ['extras/', 'extra/', 'sp/', 'sps/', 'menu', 'ncop', 'nced', 'bonus/']
        return sum(1 for token in structural_tokens if token in joined_text) >= 2

    @staticmethod
    def _build_title_inputs(
        path: Path,
        cus_name: str | None = None,
        is_sub_task: bool = False,
    ) -> tuple[str, int, str, str, str]:
        year = 0
        rtpath_name = remove_tag(path.name)
        if not rtpath_name:
            rtpath_name = remove_tag(path.name, True)

        path_attrs = re.split(r'[\s-]+', rtpath_name)
        if len(path_attrs) > 3:
            rtpath_name = ' '.join(path_attrs)

        rtpath_name = ' '.join(rtpath_name.split('.'))
        rtpath_name, year = divide_by_year(rtpath_name)

        season_aware_title = rtpath_name.strip('!').strip()
        rtpath_name = remove_season(rtpath_name)
        rtpath_name = remove_episode(rtpath_name)
        rtpath_name = rtpath_name.strip('!')
        cleaned_rtpath_name = rtpath_name

        ai_input_name = path.name
        if cus_name:
            rtpath_name = cus_name
            season_aware_title = cus_name
            ai_input_name = cus_name
        elif is_sub_task:
            parent_name = path.parent.name.strip()
            if parent_name:
                parent_title = remove_tag(parent_name)
                if not parent_title:
                    parent_title = remove_tag(parent_name, True)
                if parent_title:
                    parent_title = ' '.join(parent_title.split('.'))
                    parent_title, _ = divide_by_year(parent_title)
                    parent_title = remove_season(parent_title)
                    parent_title = remove_episode(parent_title)
                    parent_title = parent_title.strip('!').strip()

                child_ref = re.sub(
                    r'[\W_]+',
                    '',
                    season_aware_title or rtpath_name or path.name,
                    flags=re.UNICODE,
                ).casefold()
                parent_ref = re.sub(
                    r'[\W_]+',
                    '',
                    parent_title or '',
                    flags=re.UNICODE,
                ).casefold()
                if parent_ref and parent_ref not in child_ref:
                    ai_input_name = f"{parent_name} / {path.name}"

        return (
            rtpath_name,
            year,
            cleaned_rtpath_name,
            season_aware_title,
            ai_input_name,
        )

    @staticmethod
    def _normalize_execution_path(path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except OSError:
            return str(path).casefold()

    def _clone_route_eval_with_subset(
        self,
        route_eval: RouteEvalResult,
        allowed_source_paths: set[Path],
        planning_base_path: Path,
        allowed_relative_paths: set[str],
    ) -> RouteEvalResult:
        cloned: RouteEvalResult = {**route_eval}
        allowed_source_refs = {
            Rename._normalize_execution_path(source_path)
            for source_path in allowed_source_paths
        }

        def in_allowed_subset(file_path: Path) -> bool:
            relative_path = self._relative_planning_path(planning_base_path, file_path)
            if relative_path in allowed_relative_paths:
                return True
            return Rename._normalize_execution_path(file_path) in allowed_source_refs

        mapping = cast(dict[Path, Path], route_eval.get('mapping', {}))
        if mapping:
            subset_mapping = {
                source_path: target_path
                for source_path, target_path in mapping.items()
                if in_allowed_subset(source_path)
            }
            cloned['mapping'] = subset_mapping

        video_files = cast(list[Path], route_eval.get('video_files', []))
        if video_files:
            cloned['video_files'] = [
                file_path
                for file_path in video_files
                if in_allowed_subset(file_path)
            ]

        all_local_files = cast(list[Path], route_eval.get('all_local_files', []))
        if all_local_files:
            cloned['all_local_files'] = [
                file_path
                for file_path in all_local_files
                if in_allowed_subset(file_path)
                or file_path.suffix.lower() not in VIDEO_SUFFIX
            ]

        processed_movies = cast(list[MovieProcessResult], route_eval.get('processed_movies', []))
        if processed_movies:
            cloned['processed_movies'] = [
                movie_data
                for movie_data in processed_movies
                if in_allowed_subset(cast(Path, movie_data.get('file_path')))
            ]

        claimed_relative_paths = cast(list[str], route_eval.get('claimed_relative_paths', []))
        claim_reasons = cast(dict[str, str], route_eval.get('claim_reasons', {}))
        if claimed_relative_paths:
            kept_relative_paths = [
                relative_path
                for relative_path in claimed_relative_paths
                if relative_path in claim_reasons
            ]
            cloned['claimed_relative_paths'] = kept_relative_paths
            cloned['claim_reasons'] = {
                relative_path: claim_reasons[relative_path]
                for relative_path in kept_relative_paths
            }

        return cloned

    def _build_route_claim_source_paths(
        self,
        path: Path,
        subset_claim: RouteSubsetClaim,
    ) -> set[Path]:
        claim_paths: set[Path] = set()
        for claimed_file in subset_claim['claimed_files']:
            source_path = Path(claimed_file['source_path'])
            if source_path.exists():
                try:
                    claim_paths.add(source_path.resolve())
                except OSError:
                    claim_paths.add(source_path)
                continue

            fallback_path = path / Path(claimed_file['relative_path'])
            try:
                claim_paths.add(fallback_path.resolve())
            except OSError:
                claim_paths.add(fallback_path)
        return claim_paths

    def _build_execution_preview(
        self,
        task_uuid: str,
        route_type: str,
        source_path: Path,
        mapping: dict[Path, Path],
        ai_confidence: str | None,
    ) -> RouteExecutionPreview:
        moved_video_sources = sorted(
            [
                source_item
                for source_item in mapping
                if source_item.suffix.lower() in VIDEO_SUFFIX
            ],
            key=lambda item: str(item).casefold(),
        )
        target_roots = sorted(
            {str(target_path.parent) for target_path in mapping.values()},
            key=str.casefold,
        )
        return {
            'route_type': route_type,
            'task_uuid': task_uuid,
            'source_path': str(source_path),
            'moved_file_count': len(moved_video_sources),
            'moved_relative_paths': [item.name for item in moved_video_sources],
            'target_roots': target_roots,
            'ai_confidence': ai_confidence,
            'is_movie': route_type == 'movie',
        }

    def _write_record_data(
        self,
        task_uuid: str,
        record_data: dict[str, object],
    ) -> None:
        record_path = RECORD_PATH / f'{task_uuid}.json'
        with open(record_path, 'w', encoding='UTF-8') as file:
            json.dump(record_data, file, indent=4, ensure_ascii=False)

    @staticmethod
    def _normalize_record_mapping(mapping: dict[Path, Path]) -> dict[str, str]:
        return {str(source_path): str(target_path) for source_path, target_path in mapping.items()}

    def _build_single_route_task_data(
        self,
        *,
        task_uuid: str,
        source_path: Path,
        is_anime: bool,
        is_movie: bool,
        name: str,
        first_year: str | None,
        season_id: int,
        info: TmdbInfo,
        work_path: Path,
        ai_used: bool,
        ai_confidence: str | None,
        release_group: str,
        resource_term: str,
        pipeline_mode: str = 'ai_strict',
        extra_task_data: dict[str, object] | None = None,
    ) -> dict[str, object]:
        task_poster_path = self._resolve_task_poster_path(
            info=info,
            is_movie=is_movie,
            season_id=season_id,
        )
        task_data: dict[str, object] = {
            'path': str(source_path),
            'is_anime': is_anime,
            'is_movie': is_movie,
            'name': name,
            'year': first_year,
            'season_id': season_id,
            'uuid': str(task_uuid),
            'error': None,
            'use_ai': ai_used,
            'ai_attempted': True,
            'ai_used': ai_used,
            'ai_confidence': ai_confidence,
            'failure_reason': None,
            'pipeline_mode': pipeline_mode,
            'tmdb_id': info.get('id'),
            'poster_path': task_poster_path,
            'tmdb_name': name,
            'tmdb_year': first_year,
            'tmdb_media_type': 'movie' if is_movie else 'tv',
            'tmdb_genres': info.get('genres', []),
            'release_group': release_group,
            'resource_term': resource_term,
            'target_root': str(work_path),
        }
        if extra_task_data:
            task_data.update(extra_task_data)
        return task_data

    def _build_mixed_child_summary(
        self,
        *,
        task_data: dict[str, object],
        record_data: dict[str, str],
    ) -> MixedExecutionChildSummary:
        ordered_record_items = sorted(record_data.items(), key=lambda item: item[1].casefold())
        tmdb_media_type = 'movie' if bool(task_data.get('is_movie')) else 'tv'
        return {
            'task_uuid': str(task_data['uuid']),
            'route_type': tmdb_media_type,
            'is_movie': bool(task_data.get('is_movie')),
            'name': str(task_data.get('name') or ''),
            'year': _as_str(task_data.get('year')),
            'season_id': cast(int | None, task_data.get('season_id') if isinstance(task_data.get('season_id'), int) else None),
            'tmdb_id': _as_int(task_data.get('tmdb_id')),
            'tmdb_media_type': tmdb_media_type,
            'task_source_path': str(task_data.get('path') or ''),
            'source_paths': [source_path for source_path, _ in ordered_record_items],
            'target_root': str(task_data.get('target_root') or ''),
            'target_paths': [target_path for _, target_path in ordered_record_items],
            'target_count': len(ordered_record_items),
            'ai_confidence': _as_str(task_data.get('ai_confidence')),
        }

    def _build_mixed_parent_task_data(
        self,
        *,
        parent_task_uuid: str,
        source_path: Path,
        is_anime: bool,
        task_plan: TaskTypePlan,
        child_summaries: list[MixedExecutionChildSummary],
    ) -> dict[str, object]:
        mixed_parent_plan = task_plan['mixed_parent_plan']
        tmdb_names = [item['name'] for item in child_summaries if item['name']]
        selected_name = task_plan['selected_name'] or (
            tmdb_names[0] if tmdb_names else source_path.name
        )
        child_target_roots = [
            item['target_root'] for item in child_summaries if item['target_root']
        ]
        ordered_child_target_roots = sorted(set(child_target_roots), key=str.casefold)
        preferred_target_root = ''
        for child in child_summaries:
            if not child['is_movie'] and child['target_root']:
                preferred_target_root = child['target_root']
                break
        if not preferred_target_root and ordered_child_target_roots:
            preferred_target_root = ordered_child_target_roots[0]

        tmdb_ids = [item['tmdb_id'] for item in child_summaries if item['tmdb_id'] is not None]
        child_media_types = list(
            dict.fromkeys(item['tmdb_media_type'] for item in child_summaries)
        )
        parent_task_data: dict[str, object] = {
            'path': str(source_path),
            'is_anime': is_anime,
            'is_movie': False,
            'name': selected_name,
            'year': None,
            'season_id': None,
            'uuid': str(parent_task_uuid),
            'error': None,
            'use_ai': True,
            'ai_attempted': True,
            'ai_used': True,
            'ai_confidence': task_plan['selected_confidence'],
            'failure_reason': None,
            'pipeline_mode': 'ai_strict_mixed_parent',
            'tmdb_id': tmdb_ids[0] if len(tmdb_ids) == 1 else None,
            'poster_path': None,
            'tmdb_name': selected_name,
            'tmdb_year': None,
            'tmdb_media_type': 'mixed',
            'tmdb_genres': [],
            'release_group': None,
            'resource_term': None,
            'target_root': preferred_target_root,
            'is_mixed_parent': True,
            'mixed_parent_plan': mixed_parent_plan,
            'mixed_execution': {
                'parent_task_uuid': str(parent_task_uuid),
                'plan_kind': mixed_parent_plan['plan_kind'],
                'planning_mode': mixed_parent_plan['planning_mode'],
                'candidate_route_types': mixed_parent_plan['candidate_route_types'],
                'selected_route_type': mixed_parent_plan['selected_route_type'],
                'child_task_uuids': [item['task_uuid'] for item in child_summaries],
                'child_route_types': child_media_types,
                'child_count': len(child_summaries),
                'child_target_roots': ordered_child_target_roots,
                'children': child_summaries,
            },
        }
        return parent_task_data

    def _build_mixed_parent_record_data(
        self,
        *,
        child_record_data_list: list[dict[str, str]],
        child_summaries: list[MixedExecutionChildSummary],
        mixed_parent_plan: MixedParentPlan,
    ) -> dict[str, object]:
        parent_record_data: dict[str, object] = {}
        for record_data in child_record_data_list:
            parent_record_data.update(record_data)
        parent_record_data['_mixed_execution'] = {
            'plan_kind': mixed_parent_plan['plan_kind'],
            'planning_mode': mixed_parent_plan['planning_mode'],
            'child_task_uuids': [item['task_uuid'] for item in child_summaries],
            'child_route_types': [item['tmdb_media_type'] for item in child_summaries],
            'children': [
                {
                    'task_uuid': item['task_uuid'],
                    'route_type': item['route_type'],
                    'target_root': item['target_root'],
                    'target_count': item['target_count'],
                }
                for item in child_summaries
            ],
        }
        return parent_record_data

    def _build_debug_claim_route_eval(
        self,
        *,
        source_root: Path,
        route_type: str,
        subset_claim: RouteSubsetClaim,
        candidate_name: str,
        candidate_info: TmdbInfo,
        is_anime: bool,
        ai_confidence: str | None,
    ) -> RouteEvalResult:
        claimed_source_paths = sorted(
            self._build_route_claim_source_paths(source_root, subset_claim),
            key=lambda item: self._relative_planning_path(source_root, item),
        )
        work_root = (
            self.ANIME_MOVIE_PATH if is_anime else self.MOVIE_PATH
        ) if route_type == 'movie' else (
            self.ANIME_PATH if self._detect_anime_genre(candidate_info) else self.BANGUMI_PATH
        )

        route_eval: RouteEvalResult = {
            'route_type': route_type,
            'valid': bool(claimed_source_paths),
            'confidence': ai_confidence,
            'video_files': claimed_source_paths,
            'mapped_count': len(claimed_source_paths),
            'total_video_count': len(claimed_source_paths),
            'mapped_ratio': 1.0 if claimed_source_paths else 0.0,
            'claimed_relative_paths': [
                claimed_file['relative_path']
                for claimed_file in subset_claim['claimed_files']
            ],
            'claim_reasons': {
                claimed_file['relative_path']: claimed_file['claim_reason']
                for claimed_file in subset_claim['claimed_files']
            },
            'tmdb_name': candidate_name,
            'tmdb_info': candidate_info,
            'failure_reason': '',
            'detail': '',
        }
        if not claimed_source_paths:
            route_eval['failure_reason'] = 'mixed_subset_invalid'
            route_eval['detail'] = 'debug claim route eval empty'
            return route_eval

        if route_type == 'tv':
            first_air_date = _as_str(candidate_info.get('first_air_date')) or ''
            first_year = first_air_date.split('-')[0] if first_air_date else None
            work_path = FilenameBuilder.build_tv_work_path(
                work_root,
                candidate_name,
                first_year,
            )
            mapping: dict[Path, Path] = {}
            for episode_number, source_path in enumerate(claimed_source_paths, start=1):
                release_group = self._extract_release_group(source_path.name)
                resource_term = self._extract_resource_term(source_path.name)
                target_name = (
                    f'{candidate_name} - S01E{episode_number:02d}'
                    + (f' - {resource_term}' if resource_term else '')
                    + (f' - {release_group}' if release_group else '')
                    + source_path.suffix
                )
                mapping[source_path] = work_path / 'Season 01' / target_name
            route_eval['mapping'] = mapping
            route_eval['work_path'] = work_path
            return route_eval

        release_date = _as_str(candidate_info.get('release_date')) or ''
        release_year = release_date.split('-')[0] if release_date else None
        work_path = FilenameBuilder.build_movie_work_path(
            work_root,
            candidate_name,
            release_year,
        )
        mapping = {}
        for source_path in claimed_source_paths:
            resource_term = self._extract_resource_term(source_path.name)
            release_group = self._extract_release_group(source_path.name)
            target_name = (
                f'{candidate_name}'
                + (f' ({release_year})' if release_year else '')
                + (f' - {resource_term}' if resource_term else '')
                + (f' - {release_group}' if release_group else '')
                + source_path.suffix
            )
            mapping[source_path] = work_path / target_name
        route_eval['mapping'] = mapping
        return route_eval

    def _build_claim_preview(
        self,
        *,
        task_uuid: str,
        route_type: str,
        source_path: Path,
        subset_claim: RouteSubsetClaim,
        target_root: Path,
        ai_confidence: str | None,
    ) -> RouteExecutionPreview:
        claimed_relative_paths = [
            claimed_file['relative_path'] for claimed_file in subset_claim['claimed_files']
        ]
        return {
            'route_type': route_type,
            'task_uuid': task_uuid,
            'source_path': str(source_path),
            'moved_file_count': len(claimed_relative_paths),
            'moved_relative_paths': claimed_relative_paths,
            'target_roots': [str(target_root)],
            'ai_confidence': ai_confidence,
            'is_movie': route_type == 'movie',
        }

    def _build_debug_single_route_preview(
        self,
        *,
        task_uuid: str,
        route_type: str,
        source_path: Path,
        route_eval: RouteEvalResult | None,
        ai_confidence: str | None,
    ) -> RouteExecutionPreview | None:
        if route_eval is None or not route_eval.get('valid'):
            return None

        mapping = cast(dict[Path, Path], route_eval.get('mapping', {}))
        if mapping:
            return self._build_execution_preview(
                task_uuid,
                route_type,
                source_path,
                mapping,
                ai_confidence,
            )

        processed_movies = cast(
            list[MovieProcessResult],
            route_eval.get('processed_movies', []),
        )
        if route_type != 'movie' or not processed_movies:
            return None

        aggregate_mapping = {
            cast(Path, movie_data['file_path']): cast(Path, movie_data['target_file'])
            for movie_data in processed_movies
        }
        if not aggregate_mapping:
            return None

        return self._build_execution_preview(
            task_uuid,
            route_type,
            source_path,
            aggregate_mapping,
            ai_confidence,
        )

    def _commit_route_mapping(
        self,
        *,
        task_uuid: str,
        source_path: Path,
        is_anime: bool,
        is_movie: bool,
        name: str,
        first_year: str | None,
        season_id: int,
        info: TmdbInfo,
        work_path: Path,
        mapping: dict[Path, Path],
        ai_used: bool,
        ai_confidence: str | None,
        release_group: str,
        resource_term: str,
        pipeline_mode: str = 'ai_strict',
        dry_run: bool = False,
        extra_task_data: dict[str, object] | None = None,
    ) -> str | bool | RouteExecutionPreview:
        from ..subtitle.extractor import SUBTITLE_EXTENSIONS

        video_mapping: dict[Path, Path] = {}
        subtitle_mapping: dict[Path, Path] = {}
        for mapped_source, target_path in mapping.items():
            if mapped_source.suffix.lower() in SUBTITLE_EXTENSIONS:
                subtitle_mapping[mapped_source] = target_path
            else:
                video_mapping[mapped_source] = target_path

        if dry_run:
            collision_detail = self._detect_target_collision(mapping)
            if collision_detail:
                return self.error_reply(
                    task_uuid,
                    self._failure_message('target_collision', collision_detail),
                    source_path,
                    is_anime,
                    is_movie,
                    name,
                    season_id,
                    failure_reason='target_collision',
                    ai_attempted=True,
                    ai_used=ai_used,
                    ai_confidence=ai_confidence,
                )
            return self._build_execution_preview(
                task_uuid,
                'movie' if is_movie else 'tv',
                source_path,
                mapping,
                ai_confidence,
            )

        collision_detail = self._detect_target_collision(video_mapping)
        if collision_detail:
            return self.error_reply(
                task_uuid,
                self._failure_message('target_collision', collision_detail),
                source_path,
                is_anime,
                is_movie,
                name,
                season_id,
                failure_reason='target_collision',
                ai_attempted=True,
                ai_used=ai_used,
                ai_confidence=ai_confidence,
            )

        trans_result = Trans(video_mapping, task_uuid).trans_file()
        if isinstance(trans_result, str):
            return self.error_reply(
                task_uuid,
                self._failure_message('trans_failed', trans_result),
                source_path,
                is_anime,
                is_movie,
                name,
                season_id,
                failure_reason='trans_failed',
                ai_attempted=True,
                ai_used=ai_used,
                ai_confidence=ai_confidence,
            )

        task_data = self._build_single_route_task_data(
            task_uuid=task_uuid,
            source_path=source_path,
            is_anime=is_anime,
            is_movie=is_movie,
            name=name,
            first_year=first_year,
            season_id=season_id,
            info=info,
            work_path=work_path,
            ai_used=ai_used,
            ai_confidence=ai_confidence,
            release_group=release_group,
            resource_term=resource_term,
            pipeline_mode=pipeline_mode,
            extra_task_data=extra_task_data,
        )
        self._write_task_data(task_data)

        if subtitle_mapping:
            sub_trans = Trans(
                subtitle_mapping,
                task_uuid,
                force_mode='复制',
                force_overwrite=cm.get_config('overwrite_existing'),
                write_record=False,
            )
            sub_trans_result = sub_trans.trans_file()
            if isinstance(sub_trans_result, str):
                logger.warning(f'[字幕处理] 关联字幕复制失败: {sub_trans_result}')

        return True

    def process(
        self,
        path: Path,
        _is_anime: bool | None = None,
        _is_movie: bool | None = None,
        _tuuid: str | None = None,
        cus_name: str | None = None,
        cus_season_id: int | None = None,
        _is_sub_task: bool = False,
        _enqueue_task: EnqueueTask | None = None,
    ) -> str | bool:
        """
        处理文件/文件夹

        Args:
            path: 文件/文件夹路径
            _is_anime: 是否为动漫
            _is_movie: 是否为电影
            _tuuid: 任务UUID
            cus_name: 自定义名称
            cus_season_id: 自定义季度ID
            _is_sub_task: 是否为子任务（由父任务拆分出来的）
        """
        if path.is_dir():
            has_direct_video = any(
                (not sub_path.is_dir())
                and sub_path.suffix.lower() in VIDEO_SUFFIX
                for sub_path in path.iterdir()
            )

            if has_direct_video:
                return self._process(
                    path,
                    _is_anime,
                    _is_movie,
                    _tuuid,
                    cus_name,
                    cus_season_id,
                    _is_sub_task=_is_sub_task,
                )

            # 非视频直系目录：父任务拆成并行子任务
            if not _is_sub_task:
                sub_paths = [
                    sub_path
                    for sub_path in path.iterdir()
                    if sub_path.is_dir()
                    or sub_path.suffix.lower() in VIDEO_SUFFIX
                ]
                logger.info(
                    f"[处理任务] 发现 {len(sub_paths)} 个子路径，分别加入队列并行处理"
                )
                if _enqueue_task is None:
                    return self.error_reply(
                        _tuuid or str(uuid.uuid4()),
                        "[队列] 缺少子任务入队回调",
                        path,
                        _is_anime,
                        _is_movie,
                        failure_reason="queue_enqueue_missing",
                        ai_attempted=False,
                        ai_used=False,
                        ai_confidence=None,
                    )

                for sub_path in sub_paths:
                    _ = _enqueue_task(
                        path=str(sub_path),
                        is_anime=_is_anime,
                        is_movie=_is_movie,
                        cus_name=self._derive_subtask_custom_name(
                            path,
                            sub_path,
                            cus_name,
                        ),
                        _is_sub_task=True,
                    )
                return True

            # 子任务不再继续拆分，串行处理
            for sub_path in path.iterdir():
                self._process(
                    sub_path,
                    _is_anime,
                    _is_movie,
                    _tuuid,
                    self._derive_subtask_custom_name(path, sub_path, cus_name),
                    cus_season_id,
                    _is_sub_task=True,
                )
            return True

        return self._process(
            path,
            _is_anime,
            _is_movie,
            _tuuid,
            cus_name,
            cus_season_id,
            _is_sub_task=_is_sub_task,
        )

    def _execute_mixed_parent_plan(
        self,
        *,
        path: Path,
        task_uuid: str,
        task_plan: TaskTypePlan,
        is_anime: bool,
        injected_tv_route_eval: RouteEvalResult | None = None,
        injected_movie_route_eval: RouteEvalResult | None = None,
        dry_run: bool = False,
    ) -> str | bool | list[RouteExecutionPreview]:
        mixed_parent_plan = task_plan['mixed_parent_plan']
        if mixed_parent_plan['planning_mode'] != 'mixed_parent':
            return self.error_reply(
                task_uuid,
                self._failure_message('mixed_subset_invalid', 'mixed parent plan not selected'),
                path,
                is_anime,
                None,
                failure_reason='mixed_subset_invalid',
                ai_attempted=True,
                ai_used=True,
                ai_confidence=task_plan['selected_confidence'],
            )

        if not mixed_parent_plan['mixed_subset_is_valid']:
            return self.error_reply(
                task_uuid,
                self._failure_message(
                    mixed_parent_plan['mixed_subset_failure_reason'] or 'mixed_subset_invalid',
                    mixed_parent_plan['mixed_subset_failure_detail'],
                ),
                path,
                is_anime,
                None,
                failure_reason=(
                    mixed_parent_plan['mixed_subset_failure_reason'] or 'mixed_subset_invalid'
                ),
                ai_attempted=True,
                ai_used=True,
                ai_confidence=task_plan['selected_confidence'],
            )

        tv_candidate_info = _as_tmdb_info(task_plan['tv_candidate'].get('info'))
        movie_candidate_info = _as_tmdb_info(task_plan['movie_candidate'].get('info'))
        tv_candidate_name = _as_str(task_plan['tv_candidate'].get('name')) or task_plan['selected_name']
        movie_candidate_name = _as_str(task_plan['movie_candidate'].get('name')) or task_plan['selected_name']
        tv_route_eval = injected_tv_route_eval or (
            self._get_cached_validated_route_eval(path, 'tv', tv_candidate_info)
            if tv_candidate_info
            else None
        )
        movie_route_eval = injected_movie_route_eval or (
            self._get_cached_validated_route_eval(path, 'movie', movie_candidate_info)
            if movie_candidate_info
            else None
        )

        if tv_candidate_info is None or movie_candidate_info is None:
            return self.error_reply(
                task_uuid,
                self._failure_message('mixed_subset_invalid', 'mixed child TMDB candidate missing'),
                path,
                is_anime,
                None,
                failure_reason='mixed_subset_invalid',
                ai_attempted=True,
                ai_used=True,
                ai_confidence=task_plan['selected_confidence'],
            )

        if tv_route_eval is None or movie_route_eval is None:
            return self.error_reply(
                task_uuid,
                self._failure_message('mixed_subset_invalid', 'mixed child route validation cache missing'),
                path,
                is_anime,
                None,
                failure_reason='mixed_subset_invalid',
                ai_attempted=True,
                ai_used=True,
                ai_confidence=task_plan['selected_confidence'],
            )

        tv_subset_claim = task_plan['tv_subset_claim']
        movie_subset_claim = task_plan['movie_subset_claim']
        tv_allowed_sources = self._build_route_claim_source_paths(path, tv_subset_claim)
        movie_allowed_sources = self._build_route_claim_source_paths(path, movie_subset_claim)
        tv_allowed_relative_paths = {
            claimed_file['relative_path'] for claimed_file in tv_subset_claim['claimed_files']
        }
        movie_allowed_relative_paths = {
            claimed_file['relative_path'] for claimed_file in movie_subset_claim['claimed_files']
        }
        if not tv_allowed_sources or not movie_allowed_sources:
            return self.error_reply(
                task_uuid,
                self._failure_message('mixed_subset_invalid', 'mixed child subset empty after source resolution'),
                path,
                is_anime,
                None,
                failure_reason='mixed_subset_invalid',
                ai_attempted=True,
                ai_used=True,
                ai_confidence=task_plan['selected_confidence'],
            )

        if tv_allowed_sources & movie_allowed_sources:
            return self.error_reply(
                task_uuid,
                self._failure_message('mixed_subset_overlap'),
                path,
                is_anime,
                None,
                failure_reason='mixed_subset_overlap',
                ai_attempted=True,
                ai_used=True,
                ai_confidence=task_plan['selected_confidence'],
            )

        tv_route_eval = self._clone_route_eval_with_subset(
            tv_route_eval,
            tv_allowed_sources,
            path,
            tv_allowed_relative_paths,
        )
        movie_route_eval = self._clone_route_eval_with_subset(
            movie_route_eval,
            movie_allowed_sources,
            path,
            movie_allowed_relative_paths,
        )

        previews: list[RouteExecutionPreview] = []
        child_task_data_list: list[dict[str, object]] = []
        child_record_data_list: list[dict[str, str]] = []
        child_summaries: list[MixedExecutionChildSummary] = []
        tv_mapping = cast(dict[Path, Path], tv_route_eval.get('mapping', {}))
        processed_movies = cast(list[MovieProcessResult], movie_route_eval.get('processed_movies', []))
        if dry_run and tv_route_eval.get('valid') and not tv_mapping and tv_allowed_relative_paths:
            tv_work_path = cast(Path, tv_route_eval.get('work_path'))
            tv_confidence = _as_str(tv_route_eval.get('confidence'))
            previews.append(
                self._build_claim_preview(
                    task_uuid=task_uuid,
                    route_type='tv',
                    source_path=path,
                    subset_claim=tv_subset_claim,
                    target_root=tv_work_path,
                    ai_confidence=tv_confidence,
                )
            )
            tv_mapping = {}
        elif not tv_route_eval.get('valid') or not tv_mapping:
            return self.error_reply(
                task_uuid,
                self._failure_message('mixed_subset_invalid', 'TV child subset no longer executable'),
                path,
                is_anime,
                None,
                failure_reason='mixed_subset_invalid',
                ai_attempted=True,
                ai_used=True,
                ai_confidence=task_plan['selected_confidence'],
            )

        single_movie_mapping = cast(dict[Path, Path], movie_route_eval.get('mapping', {}))
        if (
            dry_run
            and movie_route_eval.get('valid')
            and not processed_movies
            and not single_movie_mapping
            and movie_allowed_relative_paths
        ):
            movie_work_root = self.ANIME_MOVIE_PATH if is_anime else self.MOVIE_PATH
            movie_first_data = _as_str(movie_candidate_info.get('release_date')) or ''
            movie_first_year = movie_first_data.split('-')[0] if movie_first_data else None
            movie_work_path = FilenameBuilder.build_movie_work_path(
                movie_work_root,
                movie_candidate_name,
                movie_first_year,
            )
            movie_confidence = _as_str(movie_route_eval.get('confidence'))
            previews.append(
                self._build_claim_preview(
                    task_uuid=str(uuid.uuid4()),
                    route_type='movie',
                    source_path=path,
                    subset_claim=movie_subset_claim,
                    target_root=movie_work_path,
                    ai_confidence=movie_confidence,
                )
            )
            return previews
        if not movie_route_eval.get('valid') or (not processed_movies and not single_movie_mapping):
            return self.error_reply(
                task_uuid,
                self._failure_message('mixed_subset_invalid', 'Movie child subset no longer executable'),
                path,
                is_anime,
                None,
                failure_reason='mixed_subset_invalid',
                ai_attempted=True,
                ai_used=True,
                ai_confidence=task_plan['selected_confidence'],
            )

        tv_first_data = _as_str(tv_candidate_info.get('first_air_date')) or ''
        tv_first_year = tv_first_data.split('-')[0] if tv_first_data else None
        tv_work_path = cast(Path, tv_route_eval.get('work_path'))
        tv_video_files = cast(list[Path], tv_route_eval.get('video_files', []))
        tv_primary_source_name = tv_video_files[0].name if tv_video_files else path.name
        tv_release_group = self._extract_release_group(tv_primary_source_name)
        tv_resource_term = self._extract_resource_term(tv_primary_source_name)
        tv_season_id = self._detect_season_id_from_mapping(tv_mapping)
        tv_confidence = _as_str(tv_route_eval.get('confidence'))
        tv_child_uuid = str(uuid.uuid4())
        tv_record_data = self._normalize_record_mapping(tv_mapping)
        tv_task_data = self._build_single_route_task_data(
            task_uuid=tv_child_uuid,
            source_path=path,
            is_anime=is_anime,
            is_movie=False,
            name=tv_candidate_name,
            first_year=tv_first_year,
            season_id=tv_season_id,
            info=tv_candidate_info,
            work_path=tv_work_path,
            ai_used=True,
            ai_confidence=tv_confidence,
            release_group=tv_release_group,
            resource_term=tv_resource_term,
            pipeline_mode='ai_strict_mixed_child',
            extra_task_data={
                'mixed_parent_uuid': str(task_uuid),
                'mixed_route_type': 'tv',
                'pipeline_mode': 'ai_strict_mixed_child',
            },
        )
        tv_result = self._commit_route_mapping(
            task_uuid=tv_child_uuid,
            source_path=path,
            is_anime=is_anime,
            is_movie=False,
            name=tv_candidate_name,
            first_year=tv_first_year,
            season_id=tv_season_id,
            info=tv_candidate_info,
            work_path=tv_work_path,
            mapping=tv_mapping,
            ai_used=True,
            ai_confidence=tv_confidence,
            release_group=tv_release_group,
            resource_term=tv_resource_term,
            pipeline_mode='ai_strict_mixed_child',
            dry_run=dry_run,
            extra_task_data={
                'mixed_parent_uuid': str(task_uuid),
                'mixed_route_type': 'tv',
            },
        )
        if isinstance(tv_result, str):
            return tv_result
        if dry_run:
            previews.append(cast(RouteExecutionPreview, tv_result))
        else:
            child_task_data_list.append(tv_task_data)
            child_record_data_list.append(tv_record_data)
            child_summaries.append(
                self._build_mixed_child_summary(
                    task_data=tv_task_data,
                    record_data=tv_record_data,
                )
            )

        if processed_movies:
            collection_confidence = _as_str(movie_route_eval.get('confidence'))
            for index, movie_data in enumerate(processed_movies):
                movie_uuid = str(uuid.uuid4())
                movie_file_path = cast(Path, movie_data['file_path'])
                movie_target_file = cast(Path, movie_data['target_file'])
                movie_mapping = {movie_file_path: movie_target_file}
                movie_release_group = _as_str(movie_data.get('release_group')) or self._extract_release_group(movie_file_path.name)
                movie_resource_term = _as_str(movie_data.get('resource_term')) or self._extract_resource_term(movie_file_path.name)
                movie_name = _as_str(movie_data.get('movie_name')) or movie_candidate_name
                movie_year = _as_str(movie_data.get('movie_year'))
                movie_confidence = _as_str(movie_data.get('ai_confidence')) or collection_confidence
                movie_info: TmdbInfo = {
                    'id': movie_data.get('tmdb_id'),
                    'poster_path': movie_data.get('poster_path'),
                    'genres': movie_data.get('tmdb_genres') or [],
                    'release_date': movie_year,
                }
                movie_record_data = self._normalize_record_mapping(movie_mapping)
                movie_task_data = self._build_single_route_task_data(
                    task_uuid=movie_uuid,
                    source_path=movie_file_path,
                    is_anime=is_anime,
                    is_movie=True,
                    name=movie_name,
                    first_year=movie_year,
                    season_id=0,
                    info=movie_info,
                    work_path=movie_target_file.parent,
                    ai_used=True,
                    ai_confidence=movie_confidence,
                    release_group=movie_release_group,
                    resource_term=movie_resource_term,
                    pipeline_mode='ai_strict_mixed_child',
                    extra_task_data={
                        'mixed_parent_uuid': str(task_uuid),
                        'mixed_route_type': 'movie',
                        'mixed_child_index': index + 1,
                        'pipeline_mode': 'ai_strict_mixed_child',
                    },
                )
                movie_result = self._commit_route_mapping(
                    task_uuid=movie_uuid,
                    source_path=movie_file_path,
                    is_anime=is_anime,
                    is_movie=True,
                    name=movie_name,
                    first_year=movie_year,
                    season_id=0,
                    info=movie_info,
                    work_path=movie_target_file.parent,
                    mapping=movie_mapping,
                    ai_used=True,
                    ai_confidence=movie_confidence,
                    release_group=movie_release_group,
                    resource_term=movie_resource_term,
                    pipeline_mode='ai_strict_mixed_child',
                    dry_run=dry_run,
                    extra_task_data={
                        'mixed_parent_uuid': str(task_uuid),
                        'mixed_route_type': 'movie',
                        'mixed_child_index': index + 1,
                    },
                )
                if isinstance(movie_result, str):
                    return movie_result
                if dry_run:
                    previews.append(cast(RouteExecutionPreview, movie_result))
                else:
                    child_task_data_list.append(movie_task_data)
                    child_record_data_list.append(movie_record_data)
                    child_summaries.append(
                        self._build_mixed_child_summary(
                            task_data=movie_task_data,
                            record_data=movie_record_data,
                        )
                    )
        else:
            movie_first_data = _as_str(movie_candidate_info.get('release_date')) or ''
            movie_first_year = movie_first_data.split('-')[0] if movie_first_data else None
            movie_work_root = self.ANIME_MOVIE_PATH if is_anime else self.MOVIE_PATH
            movie_work_path = FilenameBuilder.build_movie_work_path(
                movie_work_root,
                movie_candidate_name,
                movie_first_year,
            )
            movie_video_files = sorted(single_movie_mapping, key=lambda item: item.name.casefold())
            movie_primary_source_name = movie_video_files[0].name if movie_video_files else path.name
            movie_release_group = self._extract_release_group(movie_primary_source_name)
            movie_resource_term = self._extract_resource_term(movie_primary_source_name)
            movie_confidence = _as_str(movie_route_eval.get('confidence'))
            movie_uuid = str(uuid.uuid4())
            movie_record_data = self._normalize_record_mapping(single_movie_mapping)
            movie_task_data = self._build_single_route_task_data(
                task_uuid=movie_uuid,
                source_path=path,
                is_anime=is_anime,
                is_movie=True,
                name=movie_candidate_name,
                first_year=movie_first_year,
                season_id=0,
                info=movie_candidate_info,
                work_path=movie_work_path,
                ai_used=True,
                ai_confidence=movie_confidence,
                release_group=movie_release_group,
                resource_term=movie_resource_term,
                pipeline_mode='ai_strict_mixed_child',
                extra_task_data={
                    'mixed_parent_uuid': str(task_uuid),
                    'mixed_route_type': 'movie',
                    'pipeline_mode': 'ai_strict_mixed_child',
                },
            )
            movie_result = self._commit_route_mapping(
                task_uuid=movie_uuid,
                source_path=path,
                is_anime=is_anime,
                is_movie=True,
                name=movie_candidate_name,
                first_year=movie_first_year,
                season_id=0,
                info=movie_candidate_info,
                work_path=movie_work_path,
                mapping=single_movie_mapping,
                ai_used=True,
                ai_confidence=movie_confidence,
                release_group=movie_release_group,
                resource_term=movie_resource_term,
                pipeline_mode='ai_strict_mixed_child',
                dry_run=dry_run,
                extra_task_data={
                    'mixed_parent_uuid': str(task_uuid),
                    'mixed_route_type': 'movie',
                },
            )
            if isinstance(movie_result, str):
                return movie_result
            if dry_run:
                previews.append(cast(RouteExecutionPreview, movie_result))
            else:
                child_task_data_list.append(movie_task_data)
                child_record_data_list.append(movie_record_data)
                child_summaries.append(
                    self._build_mixed_child_summary(
                        task_data=movie_task_data,
                        record_data=movie_record_data,
                    )
                )

        if not dry_run:
            parent_task_data = self._build_mixed_parent_task_data(
                parent_task_uuid=task_uuid,
                source_path=path,
                is_anime=is_anime,
                task_plan=task_plan,
                child_summaries=child_summaries,
            )
            parent_record_data = self._build_mixed_parent_record_data(
                child_record_data_list=child_record_data_list,
                child_summaries=child_summaries,
                mixed_parent_plan=mixed_parent_plan,
            )
            self._write_task_data(parent_task_data)
            self._write_record_data(task_uuid, parent_record_data)

        return previews if dry_run else True

    def _process(
        self,
        path: Path,
        _is_anime: bool | None = None,
        _is_movie: bool | None = None,
        _tuuid: str | None = None,
        cus_name: str | None = None,
        cus_season_id: int | None = None,
        _is_sub_task: bool = False,
    ) -> str | bool:
        _uuid = _tuuid or str(uuid.uuid4())

        if not self.search.TMDB_KEY:
            return self.error_reply(
                _uuid,
                self._failure_message("tmdb_key_missing"),
                path,
                _is_anime,
                _is_movie,
                failure_reason="tmdb_key_missing",
                ai_attempted=False,
                ai_used=False,
                ai_confidence=None,
            )

        if not path.exists():
            return self.error_reply(
                _uuid,
                self._failure_message("invalid_path", str(path)),
                path,
                _is_anime,
                _is_movie,
                failure_reason="invalid_path",
                ai_attempted=False,
                ai_used=False,
                ai_confidence=None,
            )

        # 最小 deterministic 守卫：非视频文件直接跳过
        if path.is_file() and path.suffix.lower() not in VIDEO_SUFFIX:
            logger.info(f'[处理任务] {path.name} 不是视频文件，跳过')
            return True

        logger.info(f'[处理任务] 开始处理 {path.name}')

        # Step.1 清洗标题
        (
            rtpath_name,
            year,
            cleaned_rtpath_name,
            season_aware_title,
            ai_input_name,
        ) = self._build_title_inputs(
            path,
            cus_name,
            is_sub_task=_is_sub_task,
        )

        logger.info(f'[处理任务] 清洗后标题: {rtpath_name}')

        # AI 严格模式：默认强制，可由运维紧急开关关闭
        ai_force_strict = bool(cm.get_config('ai_force_strict'))
        ai_client = AIClient()
        ai_available = ai_client.is_available()

        if not ai_available:
            if ai_force_strict:
                return self.error_reply(
                    _uuid,
                    self._failure_message("ai_unavailable"),
                    path,
                    _is_anime,
                    _is_movie,
                    failure_reason="ai_unavailable",
                    ai_attempted=True,
                    ai_used=False,
                    ai_confidence=None,
                )

            logger.warning(
                "[处理任务] AI服务不可用且 ai_force_strict=False，"
                "当前版本无非AI回退流程，按 ai_unavailable 失败返回"
            )
            return self.error_reply(
                _uuid,
                self._failure_message("ai_unavailable"),
                path,
                _is_anime,
                _is_movie,
                failure_reason="ai_unavailable",
                ai_attempted=True,
                ai_used=False,
                ai_confidence=None,
            )

        task_type = self.check_task_type(
            rtpath_name,
            year,
            path,
            _is_anime,
            _is_movie,
            ai_client,
            prefer_manual_title=bool(cus_name),
            cleaned_title=cleaned_rtpath_name,
            raw_title=season_aware_title,
            ai_input_name=ai_input_name,
        )
        if isinstance(task_type, str):
            return self.error_reply(
                _uuid,
                self._failure_message(task_type),
                path,
                _is_anime,
                _is_movie,
                failure_reason=task_type,
                ai_attempted=True,
                ai_used=False,
                ai_confidence=None,
            )

        task_plan = task_type
        name = task_plan['selected_name']
        info = task_plan['selected_info']
        is_anime = task_plan['is_anime']
        is_movie = task_plan['is_movie']
        type_ai_confidence = task_plan['selected_confidence']
        mixed_parent_plan = task_plan['mixed_parent_plan']

        route_override = self._evaluate_dual_route_decision(path, task_plan)
        if route_override is not None:
            is_movie = route_override
            mixed_parent_plan['selected_route_type'] = 'movie' if is_movie else 'tv'
            selected = task_plan['movie_candidate'] if is_movie else task_plan['tv_candidate']
            selected_name = _as_str(selected.get('name'))
            selected_info = _as_tmdb_info(selected.get('info'))
            selected_confidence = cast(str | None, selected.get('confidence'))
            if selected_name:
                name = selected_name
            if selected_info:
                info = selected_info
            if selected_confidence:
                type_ai_confidence = selected_confidence

        if mixed_parent_plan['planning_mode'] == 'mixed_parent':
            logger.info(
                '[处理任务] 已生成 mixed parent 预执行计划: '
                f"tv_claims={mixed_parent_plan['tv_claimed_file_count']}, "
                f"movie_claims={mixed_parent_plan['movie_claimed_file_count']}, "
                f"overlap={len(mixed_parent_plan['overlap_relative_paths'])}, "
                f"unclaimed={len(mixed_parent_plan['unclaimed_relative_paths'])}"
            )
        elif (
            mixed_parent_plan['planning_mode'] == 'single_route'
            and mixed_parent_plan['mixed_subset_blockers']
            and mixed_parent_plan['mixed_capable_context']
        ):
            logger.info(
                '[处理任务] mixed 父计划校验未通过: '
                f"reason={mixed_parent_plan['mixed_subset_failure_reason']}, "
                f"detail={mixed_parent_plan['mixed_subset_failure_detail']}, "
                f"blockers={', '.join(mixed_parent_plan['mixed_subset_blockers'])}"
            )
            if mixed_parent_plan['mixed_single_route_fallback_blocked']:
                if not self._dual_route_override_allows_single_route_fallback(
                    route_override,
                    mixed_parent_plan,
                ):
                    return self.error_reply(
                        _uuid,
                        self._failure_message(
                            mixed_parent_plan['mixed_subset_failure_reason']
                            or 'mixed_subset_invalid',
                            mixed_parent_plan['mixed_subset_failure_detail'],
                        ),
                        path,
                        is_anime,
                        is_movie,
                        name,
                        0 if is_movie else 1,
                        failure_reason=(
                            mixed_parent_plan['mixed_subset_failure_reason']
                            or 'mixed_subset_invalid'
                        ),
                        ai_attempted=True,
                        ai_used=True,
                        ai_confidence=type_ai_confidence,
                        extra_task_data={
                            'pipeline_mode': 'ai_strict',
                            'mixed_parent_plan': mixed_parent_plan,
                        },
                    )
                logger.info(
                    '[处理任务] mixed 父计划不可安全拆分，'
                    f"按双路决策回退到 {mixed_parent_plan['selected_route_type']} 单链路"
                )

        if mixed_parent_plan['planning_mode'] == 'mixed_parent':
            mixed_result = self._execute_mixed_parent_plan(
                path=path,
                task_uuid=_uuid,
                task_plan=task_plan,
                is_anime=is_anime,
            )
            self.mapping = {}
            if isinstance(mixed_result, list):
                return True
            return mixed_result

        # Step.3 构建映射（TV/Movie 均 AI-strict）
        self.mapping = {}
        season_id = 0 if is_movie else 1
        task_ai_confidence = type_ai_confidence
        ai_used = True
        release_group = ""
        resource_term = ""

        if is_movie:
            work_root = self.ANIME_MOVIE_PATH if is_anime else self.MOVIE_PATH

            # 电影合集：AI 合集分析作为唯一主路径
            if path.is_dir():
                video_files = [
                    f
                    for f in path.rglob('*')
                    if f.is_file() and f.suffix.lower() in VIDEO_SUFFIX
                ]
            else:
                video_files = [path]

            if not video_files:
                video_discovery = self._build_video_discovery_debug(path)
                return self.error_reply(
                    _uuid,
                    self._failure_message("ai_empty_mapping", "未发现可处理的视频文件"),
                    path,
                    is_anime,
                    is_movie,
                    name,
                    season_id,
                    failure_reason="ai_empty_mapping",
                    ai_attempted=True,
                    ai_used=False,
                    ai_confidence=task_ai_confidence,
                    extra_task_data={'video_discovery': video_discovery},
                )

            if path.is_dir() and len(video_files) > 1:
                logger.info(
                    f"[处理任务] 检测到电影合集候选，共 {len(video_files)} 个视频文件"
                )
                # 目录中可能包含子目录，base_path 需要是目录本身
                local_files = VideoAnalyzer.analyze_video_files(path, video_files)
                collection_result = ai_client.analyze_movie_collection(
                    path.name,
                    local_files,
                )

                if not collection_result:
                    return self.error_reply(
                        _uuid,
                        self._failure_message("ai_timeout"),
                        path,
                        is_anime,
                        is_movie,
                        name,
                        season_id,
                        failure_reason="ai_timeout",
                        ai_attempted=True,
                        ai_used=False,
                        ai_confidence=task_ai_confidence,
                    )

                task_ai_confidence = collection_result.confidence
                if not self._is_confidence_acceptable(collection_result.confidence):
                    return self.error_reply(
                        _uuid,
                        self._failure_message(
                            "ai_low_confidence",
                            f"合集置信度={collection_result.confidence}",
                        ),
                        path,
                        is_anime,
                        is_movie,
                        name,
                        season_id,
                        failure_reason="ai_low_confidence",
                        ai_attempted=True,
                        ai_used=True,
                        ai_confidence=collection_result.confidence,
                    )

                single_movie_files = (
                    self._extract_single_movie_files_from_collection_result(
                        collection_result,
                        video_files,
                        path,
                    )
                )
                if single_movie_files:
                    ignored_count = len(video_files) - len(single_movie_files)
                    logger.info(
                        "[处理任务] 电影合集候选回退为单电影处理, "
                        f"保留 {len(single_movie_files)} 个正片文件, "
                        f"忽略 {ignored_count} 个附加内容"
                    )
                    video_files = single_movie_files
                    single_movie_mapping = next(
                        iter(collection_result.file_mapping),
                        None,
                    )
                    if single_movie_mapping is not None:
                        resolved_subset_movie, resolved_subset_info = (
                            self._resolve_single_movie_subset_result(
                                path,
                                single_movie_mapping,
                                collection_result.collection_name,
                            )
                        )
                        if resolved_subset_movie and resolved_subset_info:
                            resolved_title = _as_str(
                                resolved_subset_info.get('title')
                            ) or _as_str(resolved_subset_movie.get('movie_name'))
                            if resolved_title:
                                name = resolved_title
                            info = resolved_subset_info
                else:
                    valid, reason, detail = self._validate_movie_collection_result(
                        collection_result,
                        video_files,
                        path,
                    )
                    if not valid:
                        return self.error_reply(
                            _uuid,
                            self._failure_message(reason or "ai_invalid_mapping", detail),
                            path,
                            is_anime,
                            is_movie,
                            name,
                            season_id,
                            failure_reason=reason or "ai_invalid_mapping",
                            ai_attempted=True,
                            ai_used=True,
                            ai_confidence=collection_result.confidence,
                        )

                    processed_movies, unresolved = self._process_movie_collection(
                        path,
                        collection_result,
                        work_root,
                        ai_client,
                    )

                    if unresolved:
                        detail = f"未能完成全部电影映射: {', '.join(unresolved[:3])}"
                        return self.error_reply(
                            _uuid,
                            self._failure_message("ai_empty_mapping", detail),
                            path,
                            is_anime,
                            is_movie,
                            name,
                            season_id,
                            failure_reason="ai_empty_mapping",
                            ai_attempted=True,
                            ai_used=True,
                            ai_confidence=collection_result.confidence,
                        )

                    if not processed_movies:
                        return self.error_reply(
                            _uuid,
                            self._failure_message("ai_empty_mapping"),
                            path,
                            is_anime,
                            is_movie,
                            name,
                            season_id,
                            failure_reason="ai_empty_mapping",
                            ai_attempted=True,
                            ai_used=True,
                            ai_confidence=collection_result.confidence,
                        )

                    for index, movie_data in enumerate(processed_movies):
                        movie_uuid = _uuid if index == 0 else str(uuid.uuid4())
                        movie_file_path = cast(Path, movie_data['file_path'])
                        movie_target_file = cast(Path, movie_data['target_file'])
                        movie_name_value = _as_str(movie_data.get('movie_name'))
                        movie_confidence_value = _as_str(
                            movie_data.get('ai_confidence')
                        )
                        movie_map: dict[Path, Path] = {
                            movie_file_path: movie_target_file
                        }
                        trans_result = Trans(movie_map, movie_uuid).trans_file()
                        if isinstance(trans_result, str):
                            return self.error_reply(
                                movie_uuid,
                                self._failure_message("trans_failed", trans_result),
                                movie_file_path,
                                is_anime,
                                True,
                                movie_name_value,
                                0,
                                failure_reason="trans_failed",
                                ai_attempted=True,
                                ai_used=True,
                                ai_confidence=(
                                    movie_confidence_value
                                    or collection_result.confidence
                                ),
                                extra_task_data={
                                    "is_collection": True,
                                    "collection_name": (
                                        collection_result.collection_name
                                    ),
                                },
                            )

                        self._write_task_data(
                            {
                                "path": str(path),
                                "is_anime": is_anime,
                                "is_movie": True,
                                "is_collection": True,
                                "collection_name": collection_result.collection_name,
                                "name": movie_data['movie_name'],
                                "year": movie_data['movie_year'],
                                "season_id": 0,
                                "uuid": str(movie_uuid),
                                "error": None,
                                "use_ai": True,
                                "ai_attempted": True,
                                "ai_used": True,
                                 "ai_confidence": (
                                     movie_confidence_value
                                     or collection_result.confidence
                                 ),
                                "failure_reason": None,
                                "pipeline_mode": "ai_strict",
                                 "tmdb_id": movie_data.get("tmdb_id"),
                                 "poster_path": movie_data.get("poster_path"),
                                 "tmdb_name": movie_data.get("tmdb_name"),
                                 "tmdb_year": movie_data.get("tmdb_year"),
                                 "tmdb_media_type": movie_data.get(
                                     "tmdb_media_type"
                                 ),
                                "tmdb_genres": movie_data.get("tmdb_genres"),
                                "release_group": movie_data.get(
                                    "release_group"
                                ),
                                "resource_term": movie_data.get(
                                    "resource_term"
                                ),
                                 "target_root": str(movie_target_file.parent),
                             }
                         )

                    return True

            # 单电影：候选搜索 + AI 选择已在 check_task_type 中完成
            first_data = _as_str(info.get('release_date')) or ''
            first_year = first_data.split('-')[0] if first_data else None
            work_path = FilenameBuilder.build_movie_work_path(
                work_root,
                name,
                first_year,
            )

            for source_video in video_files:
                video_format = extract_video_format(source_video.name)
                part = extract_part(source_video.name)
                resource_term = self._extract_resource_term(source_video.name)
                release_group = self._extract_release_group(source_video.name)
                meta = MovieMetadata(
                    title=name,
                    year=first_year,
                    video_format=video_format,
                    resource_term=resource_term,
                    release_group=release_group,
                    part=part,
                    file_ext=source_video.suffix,
                )
                new_filename = FilenameBuilder.build_movie_filename(meta)
                self.mapping[source_video] = work_path / new_filename

            primary_source_name = video_files[0].name if video_files else path.name
            release_group = self._extract_release_group(primary_source_name)
            resource_term = self._extract_resource_term(primary_source_name)

            if not self.mapping:
                return self.error_reply(
                    _uuid,
                    self._failure_message("ai_empty_mapping"),
                    path,
                    is_anime,
                    is_movie,
                    name,
                    season_id,
                    failure_reason="ai_empty_mapping",
                    ai_attempted=True,
                    ai_used=False,
                    ai_confidence=task_ai_confidence,
                )

            season_id = 0

        else:
            # TV：AI 映射为唯一主路径
            work_root = self.ANIME_PATH if is_anime else self.BANGUMI_PATH
            first_data = _as_str(info.get('first_air_date')) or ''
            first_year = first_data.split('-')[0] if first_data else None
            work_path = FilenameBuilder.build_tv_work_path(
                work_root,
                name,
                first_year,
            )

            tv_info = info
            cached_tv_route_eval = self._pop_cached_validated_route_eval(
                path,
                'tv',
                tv_info,
            )
            tv_route_eval = cached_tv_route_eval or self._evaluate_validated_tv_route(
                path,
                tv_info,
                name,
            )
            video_files = cast(list[Path], tv_route_eval.get('video_files', []))
            primary_source_name = video_files[0].name if video_files else path.name
            release_group = self._extract_release_group(primary_source_name)
            resource_term = self._extract_resource_term(primary_source_name)
            if not video_files:
                video_discovery = self._build_video_discovery_debug(path)
                return self.error_reply(
                    _uuid,
                    self._failure_message("ai_empty_mapping", "未发现可处理的视频文件"),
                    path,
                    is_anime,
                    is_movie,
                    name,
                    season_id,
                    failure_reason="ai_empty_mapping",
                    ai_attempted=True,
                    ai_used=False,
                    ai_confidence=task_ai_confidence,
                    extra_task_data={'video_discovery': video_discovery},
                )

            tv_failure_reason = _as_str(tv_route_eval.get('failure_reason'))
            tv_failure_detail = _as_str(tv_route_eval.get('detail'))
            ai_result = cast(AIAnalysisResult | None, tv_route_eval.get('ai_result'))
            if not ai_result:
                return self.error_reply(
                    _uuid,
                    self._failure_message(
                        tv_failure_reason or "ai_timeout",
                        tv_failure_detail,
                    ),
                    path,
                    is_anime,
                    is_movie,
                    name,
                    season_id,
                    failure_reason=tv_failure_reason or "ai_timeout",
                    ai_attempted=True,
                    ai_used=False,
                    ai_confidence=task_ai_confidence,
                )

            task_ai_confidence = _as_str(tv_route_eval.get('confidence')) or ai_result.confidence
            if not tv_route_eval.get('valid'):
                extra_task_data = {
                    key: tv_route_eval[key]
                    for key in (
                        'unmapped_potential_main_files',
                        'ignored_supplemental_relative_paths',
                    )
                    if key in tv_route_eval
                }
                return self.error_reply(
                    _uuid,
                    self._failure_message(
                        tv_failure_reason or "ai_invalid_mapping",
                        tv_failure_detail,
                    ),
                    path,
                    is_anime,
                    is_movie,
                    name,
                    season_id,
                    failure_reason=tv_failure_reason or "ai_invalid_mapping",
                    ai_attempted=True,
                    ai_used=True,
                    ai_confidence=task_ai_confidence,
                    extra_task_data=extra_task_data or None,
                )

            self.mapping = cast(dict[Path, Path], tv_route_eval.get('mapping', {}))
            if not self.mapping:
                return self.error_reply(
                    _uuid,
                    self._failure_message("ai_empty_mapping"),
                    path,
                    is_anime,
                    is_movie,
                    name,
                    season_id,
                    failure_reason="ai_empty_mapping",
                    ai_attempted=True,
                    ai_used=True,
                    ai_confidence=ai_result.confidence,
                )

            season_id = self._detect_season_id_from_mapping(self.mapping)
            if cus_season_id is not None:
                # 仅影响任务展示，不覆盖 AI 映射本身
                season_id = int(cus_season_id)

        # Step.4 迁移与任务落盘
        from ..subtitle.extractor import SUBTITLE_EXTENSIONS

        video_mapping: dict[Path, Path] = {}
        subtitle_mapping: dict[Path, Path] = {}
        for source_path, target_path in self.mapping.items():
            if source_path.suffix.lower() in SUBTITLE_EXTENSIONS:
                subtitle_mapping[source_path] = target_path
            else:
                video_mapping[source_path] = target_path

        collision_detail = self._detect_target_collision(video_mapping)
        if collision_detail:
            self.mapping = {}
            return self.error_reply(
                _uuid,
                self._failure_message('target_collision', collision_detail),
                path,
                is_anime,
                is_movie,
                name,
                season_id,
                failure_reason='target_collision',
                ai_attempted=True,
                ai_used=ai_used,
                ai_confidence=task_ai_confidence,
            )

        trans_result = Trans(video_mapping, _uuid).trans_file()
        self.mapping = {}
        if isinstance(trans_result, str):
            return self.error_reply(
                _uuid,
                self._failure_message("trans_failed", trans_result),
                path,
                is_anime,
                is_movie,
                name,
                season_id,
                failure_reason="trans_failed",
                ai_attempted=True,
                ai_used=ai_used,
                ai_confidence=task_ai_confidence,
            )

        task_poster_path = self._resolve_task_poster_path(
            info=info,
            is_movie=is_movie,
            season_id=season_id,
        )

        self._write_task_data(
            {
                "path": str(path),
                "is_anime": is_anime,
                "is_movie": is_movie,
                "name": name,
                "year": first_year,
                "season_id": season_id,
                "uuid": str(_uuid),
                "error": None,
                "use_ai": ai_used,
                "ai_attempted": True,
                "ai_used": ai_used,
                "ai_confidence": task_ai_confidence,
                "failure_reason": None,
                "pipeline_mode": "ai_strict",
                "tmdb_id": info.get("id"),
                "poster_path": task_poster_path,
                "tmdb_name": name,
                "tmdb_year": first_year,
                "tmdb_media_type": "movie" if is_movie else "tv",
                "tmdb_genres": info.get("genres", []),
                "release_group": release_group,
                "resource_term": resource_term,
                "target_root": str(work_path),
            }
        )

        # 字幕文件按“字幕导入”方式强制复制
        if subtitle_mapping:
            sub_trans = Trans(
                subtitle_mapping,
                _uuid,
                force_mode="复制",
                force_overwrite=cm.get_config('overwrite_existing'),
                write_record=False,
            )
            sub_trans_result = sub_trans.trans_file()
            if isinstance(sub_trans_result, str):
                logger.warning(f"[字幕处理] 关联字幕复制失败: {sub_trans_result}")

        return True

    def check_task_type(
        self,
        rtpath_name: str,
        year: int,
        path: Path,
        is_anime: bool | None = None,
        is_movie: bool | None = None,
        ai_client: AIClient | None = None,
        prefer_manual_title: bool = False,
        cleaned_title: str | None = None,
        raw_title: str | None = None,
        ai_input_name: str | None = None,
    ) -> TaskTypePlan | str:
        """AI-first 类型判定：TV/Movie 均采用候选 + AI 选择。"""
        ai_client = ai_client or AIClient()
        if not ai_client.is_available():
            return "ai_unavailable"

        ai_extract = ai_client.extract_title_metadata(ai_input_name or path.name)
        if not ai_extract:
            return "ai_timeout"

        ai_title = ai_extract.title
        ai_fallback_title = ai_extract.fallback_title
        ai_type = ai_extract.type
        logger.info(
            "[处理任务] AI标题类型提取: "
            f"title={ai_title}, fallback_title={ai_fallback_title}, type={ai_type}"
        )

        queries: list[str] = []
        search_context_name = ai_input_name or raw_title or rtpath_name or path.name

        def append_query(value: str | None) -> None:
            if not value:
                return

            normalized = value.strip()
            if not normalized:
                return

            if normalized in queries:
                return

            queries.append(normalized)

        if prefer_manual_title:
            append_query(raw_title)
            append_query(rtpath_name)
            append_query(ai_title)
            append_query(ai_fallback_title)
            append_query(cleaned_title)
        else:
            append_query(ai_title)
            append_query(raw_title)
            append_query(ai_fallback_title)
            append_query(rtpath_name)
            append_query(cleaned_title)

        tv_name = ''
        tv_info: TmdbInfo | None = None
        tv_confidence: str | None = None
        tv_reason = "tmdb_not_found"

        movie_name = ''
        movie_info: TmdbInfo | None = None
        movie_confidence: str | None = None
        movie_reason = "tmdb_not_found"

        hint_context_name = ai_input_name or raw_title or rtpath_name or path.name
        has_tv_hint = self._has_tv_hint(hint_context_name)
        has_movie_hint = self._has_movie_hint(hint_context_name)
        structured_tv_episode_signal = self._has_structured_tv_episode_signal(path)
        has_tv_hint = has_tv_hint or structured_tv_episode_signal
        forced_by_flag = is_movie is not None

        search_tv_chain = True
        search_movie_chain = True

        if forced_by_flag:
            search_tv_chain = not is_movie
            search_movie_chain = bool(is_movie)
        elif ai_type == 'movie' and not has_tv_hint:
            search_tv_chain = False
        elif ai_type == 'tv' and not has_movie_hint:
            search_movie_chain = False
        elif has_tv_hint and not has_movie_hint:
            search_movie_chain = False
        elif has_movie_hint and not has_tv_hint:
            search_tv_chain = False

        if search_tv_chain:
            local_video_count = self._count_local_videos(path)
            for query in queries:
                _name, _info, _conf, _reason = self._search_tv_with_ai_selection(
                    search_context_name,
                    query,
                    year,
                    ai_client,
                    local_video_count=local_video_count,
                )
                if _info:
                    tv_name, tv_info, tv_confidence = _name, _info, _conf
                    break
                if _reason and _reason != "tmdb_not_found":
                    tv_reason = _reason

        if search_movie_chain:
            for query in queries:
                _name, _info, _conf, _reason = self._search_movie_with_ai_selection(
                    search_context_name,
                    query,
                    year,
                    ai_client,
                )
                if _info:
                    movie_name, movie_info, movie_confidence = _name, _info, _conf
                    break
                if _reason and _reason != "tmdb_not_found":
                    movie_reason = _reason

        should_try_collection = self._should_try_movie_collection(
            path,
            ai_type,
            has_movie_hint,
            has_tv_hint,
        )
        if (
            should_try_collection
            and not movie_info
            and not forced_by_flag
            and not tv_info
        ):
            seed_name, seed_info, seed_confidence = self._get_collection_seed_movie_info(
                queries,
                year,
            )
            if seed_info:
                movie_name = seed_name
                movie_info = seed_info
                movie_confidence = seed_confidence or movie_confidence
                logger.info(
                    f"[处理任务] 目录级电影候选低置信，允许进入电影合集分析: {path.name}"
                )

        # 主链路未命中时，执行一次保护性补搜
        if not tv_info and not movie_info:
            if not search_tv_chain:
                for query in queries:
                    _name, _info, _conf, _reason = self._search_tv_with_ai_selection(
                        search_context_name,
                        query,
                        year,
                        ai_client,
                    )
                    if _info:
                        tv_name, tv_info, tv_confidence = _name, _info, _conf
                        break
                    if _reason and _reason != "tmdb_not_found":
                        tv_reason = _reason

            if not search_movie_chain:
                for query in queries:
                    _name, _info, _conf, _reason = self._search_movie_with_ai_selection(
                        search_context_name,
                        query,
                        year,
                        ai_client,
                    )
                    if _info:
                        movie_name, movie_info, movie_confidence = _name, _info, _conf
                        break
                    if _reason and _reason != "tmdb_not_found":
                        movie_reason = _reason

        if is_movie is not None:
            final_is_movie = is_movie
        elif ai_type == 'movie':
            final_is_movie = True
        elif ai_type == 'tv':
            final_is_movie = False
        elif has_tv_hint and not has_movie_hint:
            final_is_movie = False
        elif has_movie_hint and not has_tv_hint:
            final_is_movie = True
        elif tv_info and not movie_info:
            final_is_movie = False
        elif movie_info and not tv_info:
            final_is_movie = True
        else:
            final_is_movie = False

        if structured_tv_episode_signal and tv_info:
            final_is_movie = False

        if tv_info and movie_info and (has_tv_hint or structured_tv_episode_signal) and not has_movie_hint:
            final_is_movie = False

        # 规则仅做冲突保护
        if final_is_movie and not movie_info and tv_info:
            logger.warning('[处理任务] 电影链路无TMDB结果，保护性切换到TV')
            final_is_movie = False
        elif not final_is_movie and not tv_info and movie_info:
            logger.warning('[处理任务] TV链路无TMDB结果，保护性切换到Movie')
            final_is_movie = True

        if final_is_movie:
            if not movie_info:
                return movie_reason if movie_reason.startswith('ai_') else 'tmdb_not_found'
            selected_name = movie_name
            selected_info = movie_info
            selected_confidence = movie_confidence
        else:
            if not tv_info:
                return tv_reason if tv_reason.startswith('ai_') else 'tmdb_not_found'
            selected_name = tv_name
            selected_info = tv_info
            selected_confidence = tv_confidence

        final_is_anime = (
            is_anime
            if is_anime is not None
            else self._detect_anime_genre(selected_info)
        )

        tv_candidate: RouteCandidate = {
            'name': tv_name,
            'info': tv_info or {},
            'confidence': tv_confidence,
            'available': bool(tv_info),
            'reason': tv_reason,
        }
        movie_candidate: RouteCandidate = {
            'name': movie_name,
            'info': movie_info or {},
            'confidence': movie_confidence,
            'available': bool(movie_info),
            'reason': movie_reason,
        }

        planning_files = self._build_planning_file_refs(path)
        validated_tv_route_eval: RouteEvalResult | None = None
        validated_movie_route_eval: RouteEvalResult | None = None
        if tv_info and path.exists():
            validated_tv_route_eval = self._evaluate_validated_tv_route(
                path,
                tv_info,
                tv_name,
            )
        if movie_info and path.exists():
            validated_movie_route_eval = self._evaluate_validated_movie_route(
                path,
                movie_info,
                movie_name,
                ai_client=ai_client,
            )
        tv_subset_claim = self._build_validated_tv_subset_claim(
            planning_files,
            validated_tv_route_eval,
        )
        movie_subset_claim = self._build_validated_movie_subset_claim(
            planning_files,
            validated_movie_route_eval,
        )
        selected_route_type = 'movie' if final_is_movie else 'tv'
        mixed_parent_plan = self._build_mixed_parent_plan(
            path,
            planning_files,
            tv_subset_claim,
            movie_subset_claim,
            selected_route_type=selected_route_type,
            tv_candidate_available=bool(tv_info),
            movie_candidate_available=bool(movie_info),
            ai_type=ai_type,
            has_tv_hint=has_tv_hint,
            has_movie_hint=has_movie_hint,
            forced_by_flag=forced_by_flag,
        )

        should_try_both = mixed_parent_plan['planning_mode'] == 'mixed_parent'

        return {
            'selected_name': selected_name,
            'selected_info': selected_info,
            'is_anime': bool(final_is_anime),
            'is_movie': bool(final_is_movie),
            'selected_confidence': selected_confidence,
            'ai_type': ai_type,
            'tv_candidate': tv_candidate,
            'movie_candidate': movie_candidate,
            'tv_subset_claim': tv_subset_claim,
            'movie_subset_claim': movie_subset_claim,
            'mixed_parent_plan': mixed_parent_plan,
            'should_try_both': should_try_both,
        }

    def _select_precise_tv_fallback_candidate(
        self,
        ranked_candidates: list[TmdbInfo],
        *,
        preferred_season: int | None,
        local_video_count: int | None,
        candidate_season_numbers: dict[int, set[int]],
        candidate_episode_counts: dict[int, set[int]],
    ) -> tuple[TmdbInfo | None, str | None]:
        if not preferred_season or not local_video_count or len(ranked_candidates) <= 1:
            return None, None

        precise_candidates: list[TmdbInfo] = []
        for candidate in ranked_candidates:
            tv_id = _as_int(candidate.get('id'))
            if tv_id is None:
                continue
            season_numbers = candidate_season_numbers.get(tv_id, set())
            episode_counts = candidate_episode_counts.get(tv_id, set())
            if preferred_season not in season_numbers:
                continue
            if local_video_count not in episode_counts:
                continue
            precise_candidates.append(candidate)

        if len(precise_candidates) == 1:
            return precise_candidates[0], 'High'

        if len(precise_candidates) >= 2:
            first = precise_candidates[0]
            second = precise_candidates[1]
            first_score = _float_score(first.get('_match_score', 0))
            second_score = _float_score(second.get('_match_score', 0))
            if first_score >= 96 and first_score - second_score >= 8:
                return first, 'Medium'

        return None, None

    def _select_exact_episode_count_tv_candidate(
        self,
        ranked_candidates: list[TmdbInfo],
        *,
        local_video_count: int | None,
        candidate_episode_counts: dict[int, set[int]],
    ) -> tuple[TmdbInfo | None, str | None]:
        if not local_video_count or local_video_count < 2:
            return None, None

        exact_candidates: list[TmdbInfo] = []
        for candidate in ranked_candidates:
            tv_id = _as_int(candidate.get('id'))
            if tv_id is None:
                continue
            if local_video_count in candidate_episode_counts.get(tv_id, set()):
                exact_candidates.append(candidate)

        if len(exact_candidates) != 1:
            return None, None

        selected = exact_candidates[0]
        selected_score = _float_score(selected.get('_match_score', 0))
        best_score = max(
            (_float_score(candidate.get('_match_score', 0)) for candidate in ranked_candidates),
            default=0.0,
        )
        if selected_score < 60 or best_score - selected_score > 20:
            return None, None
        return selected, 'High'

    def _search_tv_with_ai_selection(
        self,
        folder_name: str,
        query: str,
        year: int,
        ai_client: AIClient,
        local_video_count: int | None = None,
    ) -> tuple[str, TmdbInfo | None, str | None, str]:
        candidates = self.search.search_tv_by_query(query, year, limit=5)
        if not candidates and year != 0:
            candidates = self.search.search_tv_by_query(query, None, limit=5)

        if not candidates:
            return '', None, None, 'tmdb_not_found'

        ranked_candidates = self.search.rank_tv_candidates(
            source_title=folder_name,
            query=query,
            candidates=candidates,
            year=year if year != 0 else None,
        )
        preferred_season = self.search.extract_preferred_season_number(
            folder_name,
            query,
        )
        tv_info_cache: dict[int, TmdbInfo] = {}
        candidate_season_numbers: dict[int, set[int]] = {}
        candidate_episode_counts: dict[int, set[int]] = {}

        def collect_candidate_details(candidate: TmdbInfo) -> TmdbInfo | None:
            tv_id = candidate.get('id')
            if not isinstance(tv_id, int):
                return None
            cached = tv_info_cache.get(tv_id)
            if cached:
                return cached

            tv_info = self.search.get_tv_info_by_id(tv_id)
            if not tv_info:
                return None

            tv_info = cast(TmdbInfo, self.search.fill_season_info(tv_info))
            tv_info_cache[tv_id] = cast(TmdbInfo, tv_info)
            seasons = [
                season
                for season in tv_info.get('seasons', [])
                if isinstance(season, dict)
            ]
            candidate_season_numbers[tv_id] = {
                season_number
                for season in seasons
                if isinstance((season_number := season.get('season_number')), int)
            }
            candidate_episode_counts[tv_id] = {
                episode_count
                for season in seasons
                if isinstance((episode_count := season.get('episode_count')), int)
                and episode_count > 0
            }
            return cast(TmdbInfo, tv_info)

        if preferred_season and len(ranked_candidates) > 1:
            refined_candidates: list[TmdbInfo] = []
            for candidate in ranked_candidates:
                refined_candidate = dict(candidate)
                score = float(refined_candidate.get('_match_score', 0) or 0)
                tv_info = collect_candidate_details(refined_candidate)
                tv_id = refined_candidate.get('id')
                if tv_info and isinstance(tv_id, int):
                    refined_candidate['seasons'] = tv_info.get('seasons', [])
                    season_numbers = candidate_season_numbers.get(tv_id, set())
                    if preferred_season in season_numbers:
                        score += 36
                    else:
                        score -= 24
                refined_candidate['_match_score'] = round(score, 3)
                refined_candidates.append(refined_candidate)

            ranked_candidates = sorted(
                refined_candidates,
                key=lambda item: (
                    item.get('_match_score', 0),
                    item.get('popularity', 0),
                ),
                reverse=True,
            )
        elif local_video_count and len(ranked_candidates) > 1:
            enriched_candidates: list[TmdbInfo] = []
            for candidate in ranked_candidates:
                enriched_candidate = dict(candidate)
                tv_info = collect_candidate_details(enriched_candidate)
                if tv_info:
                    enriched_candidate['seasons'] = tv_info.get('seasons', [])
                enriched_candidates.append(enriched_candidate)
            ranked_candidates = enriched_candidates
        elif local_video_count and len(ranked_candidates) == 1:
            enriched_candidate = dict(ranked_candidates[0])
            tv_info = collect_candidate_details(enriched_candidate)
            if tv_info:
                enriched_candidate['seasons'] = tv_info.get('seasons', [])
                ranked_candidates = [enriched_candidate]

        exact_count_selected, exact_count_confidence = (
            self._select_exact_episode_count_tv_candidate(
                ranked_candidates,
                local_video_count=local_video_count,
                candidate_episode_counts=candidate_episode_counts,
            )
        )
        deterministic_selected = None
        deterministic_confidence = None
        should_force_ai_selection = False
        if exact_count_selected is not None:
            deterministic_selected = exact_count_selected
            deterministic_confidence = exact_count_confidence
        if local_video_count and len(ranked_candidates) > 1:
            exact_count_candidate_ids = {
                tv_id
                for tv_id, counts in candidate_episode_counts.items()
                if local_video_count in counts
            }
            if exact_count_candidate_ids:
                first_id = _as_int(ranked_candidates[0].get('id'))
                if first_id not in exact_count_candidate_ids:
                    should_force_ai_selection = True

        if not deterministic_selected and preferred_season and len(ranked_candidates) > 1:
            first = ranked_candidates[0]
            second = ranked_candidates[1]
            first_id = _as_int(first.get('id'))
            second_id = _as_int(second.get('id'))
            first_seasons = (
                candidate_season_numbers.get(first_id, set())
                if isinstance(first_id, int)
                else set()
            )
            second_seasons = (
                candidate_season_numbers.get(second_id, set())
                if isinstance(second_id, int)
                else set()
            )
            first_score = _float_score(first.get('_match_score', 0))
            second_score = _float_score(second.get('_match_score', 0))
            if (
                preferred_season in first_seasons
                and preferred_season not in second_seasons
                and first_score - second_score >= 20
            ):
                deterministic_selected = first
                deterministic_confidence = 'High'

        single_ranked_tv_candidate_rejected = False
        if not deterministic_selected and not should_force_ai_selection:
            deterministic_selected, deterministic_confidence = (
                self.search._select_ranked_tv_candidate(ranked_candidates)
            )
            single_ranked_tv_candidate_rejected = (
                len(ranked_candidates) == 1
                and deterministic_selected is None
                and deterministic_confidence == 'Low'
            )

        if deterministic_selected:
            selected = deterministic_selected
            selection_confidence = deterministic_confidence
        elif single_ranked_tv_candidate_rejected:
            return '', None, deterministic_confidence, 'ai_low_confidence'
        else:
            selected, selection_confidence = self._ai_select_tv(
                ai_client,
                folder_name,
                query,
                ranked_candidates,
                local_video_count=local_video_count,
            )
            if not selected or not self._is_confidence_acceptable(selection_confidence):
                fallback_selected, fallback_confidence = (
                    self._select_precise_tv_fallback_candidate(
                        ranked_candidates,
                        preferred_season=preferred_season,
                        local_video_count=local_video_count,
                        candidate_season_numbers=candidate_season_numbers,
                        candidate_episode_counts=candidate_episode_counts,
                    )
                )
                if fallback_selected:
                    selected = fallback_selected
                    selection_confidence = fallback_confidence or selection_confidence
                    fallback_name = _as_str(fallback_selected.get('name')) or ''
                    logger.info(
                        '[电视剧搜索] AI低置信，回退到明确季集命中的 deterministic 候选: '
                        f'{fallback_name} (confidence={selection_confidence})'
                    )
                else:
                    return '', None, selection_confidence, 'ai_low_confidence'

        selected_id = _as_int(selected.get('id'))
        if selected_id is None:
            return '', None, selection_confidence, 'tmdb_not_found'

        tv_info = tv_info_cache.get(selected_id) or self.search.get_tv_info_by_id(
            selected_id
        )
        if not tv_info:
            return '', None, selection_confidence, 'tmdb_not_found'

        tv_info = cast(TmdbInfo, self.search.fill_season_info(tv_info))

        name = _as_str(tv_info.get('name')) or _as_str(selected.get('name')) or ''
        logger.info(
            f"[电视剧搜索] 选择: {name} (confidence={selection_confidence})"
        )
        return name, tv_info, selection_confidence, ''

    def _search_movie_with_ai_selection(
        self,
        filename: str,
        query: str,
        year: int,
        ai_client: AIClient,
    ) -> tuple[str, TmdbInfo | None, str | None, str]:
        candidates = self.search.search_movies_by_title(query, year, limit=5)

        selected: TmdbInfo | None = None
        selection_confidence: str | None = None

        deterministic_selected, deterministic_confidence = (
            self._select_ranked_movie_candidate(candidates or [])
        )
        if deterministic_selected:
            selected = deterministic_selected
            selection_confidence = deterministic_confidence
        elif not candidates and year != 0:
            candidates = self.search.search_movies_by_title(query, None, limit=5)
        elif not candidates:
            return '', None, None, 'tmdb_not_found'

        if not candidates:
            return '', None, None, 'tmdb_not_found'

        single_ranked_movie_candidate_rejected = (
            len(candidates) == 1
            and deterministic_selected is None
            and deterministic_confidence == 'Low'
        )

        if deterministic_selected:
            pass
        elif single_ranked_movie_candidate_rejected:
            return '', None, deterministic_confidence, 'ai_low_confidence'
        else:
            selected, selection_confidence = self._ai_select_movie(
                ai_client,
                filename,
                query,
                candidates,
            )
            if not selected:
                return '', None, selection_confidence, 'ai_low_confidence'
            if not self._is_confidence_acceptable(selection_confidence):
                return '', None, selection_confidence, 'ai_low_confidence'

        if selected is None:
            return '', None, selection_confidence, 'tmdb_not_found'

        selected_id = _as_int(selected.get('id'))
        if selected_id is None:
            return '', None, selection_confidence, 'tmdb_not_found'

        movie_info = self.search.get_movie_info_by_id(selected_id)
        if not movie_info:
            return '', None, selection_confidence, 'tmdb_not_found'

        movie_name = _as_str(movie_info.get('title')) or _as_str(selected.get('title')) or ''
        logger.info(
            f"[电影搜索] 选择: {movie_name} (confidence={selection_confidence})"
        )
        return movie_name, movie_info, selection_confidence, ''

    def _ai_select_movie(
        self,
        ai_client: AIClient,
        filename: str,
        extracted_title: str,
        candidates: list[TmdbInfo],
    ) -> tuple[TmdbInfo | None, str | None]:
        """让 AI 从电影候选列表中选择最匹配项（返回候选和置信度）。"""
        if not ai_client.is_available():
            return None, None

        candidates_info: list[str] = []
        for index, movie in enumerate(candidates, start=1):
            candidates_info.append(
                (
                    f"{index}. {movie.get('title', '')}"
                    f" ({movie.get('original_title', '')})"
                    f" [{movie.get('release_date', '')}]"
                )
            )

        prompt = f"""请从以下 TMDB 电影候选中选择最匹配的一项。

原始文件名: {filename}
提取标题: {extracted_title}

候选列表:
{chr(10).join(candidates_info)}

请严格返回 JSON：
{{
  "index": 1,
  "confidence": "High/Medium/Low",
  "reason": "简短说明"
}}
"""

        system_prompt = (
            "你是电影匹配助手。根据文件名和标题选择最匹配的 TMDB 候选。"
            "必须只返回 JSON。"
        )

        try:
            result = ai_client._call_openai_simple(
                system_prompt,
                prompt,
                max_retries=1,
                validation_key="index",
            )

            parsed = self._parse_selection_result(result)
            if not parsed:
                return None, None

            index_value = parsed.get('index')
            confidence_value = parsed.get('confidence')
            if not isinstance(index_value, int):
                return None, None

            idx = index_value - 1
            confidence = confidence_value if isinstance(confidence_value, str) else None
            if 0 <= idx < len(candidates):
                logger.info(
                    f"[AI选择] 电影 {filename} -> 候选#{idx + 1}, "
                    f"confidence={confidence}"
                )
                return candidates[idx], confidence
        except Exception as e:
            logger.warning(f"[AI选择] 电影选择失败: {e}")

        return None, None

    def _ai_select_tv(
        self,
        ai_client: AIClient,
        folder_name: str,
        query: str,
        candidates: list[TmdbInfo],
        local_video_count: int | None = None,
    ) -> tuple[TmdbInfo | None, str | None]:
        """让 AI 从电视剧候选列表中选择最匹配项（返回候选和置信度）。"""
        if not ai_client.is_available():
            return None, None

        candidates_info: list[str] = []
        for index, tv in enumerate(candidates, start=1):
            season_parts: list[str] = []
            seasons = tv.get('seasons')
            if not isinstance(seasons, list):
                seasons = []
            for season in seasons:
                if not isinstance(season, dict):
                    continue
                season_number = season.get('season_number')
                episode_count = season.get('episode_count')
                if isinstance(season_number, int):
                    season_label = f"S{season_number}"
                    if isinstance(episode_count, int):
                        season_label += f"({episode_count})"
                    season_parts.append(season_label)
            season_text = f" seasons: {', '.join(season_parts)}" if season_parts else ''
            candidates_info.append(
                (
                    f"{index}. {tv.get('name', '')}"
                    f" ({tv.get('original_name', '')})"
                    f" [{tv.get('first_air_date', '')}]"
                    f"{season_text}"
                )
            )

        local_video_count_text = ''
        if isinstance(local_video_count, int) and local_video_count > 0:
            local_video_count_text = f"本地视频数量: {local_video_count}\n"

        prompt = f"""请从以下 TMDB 电视剧/动漫候选中选择最匹配的一项。

原始目录名: {folder_name}
搜索关键词: {query}
{local_video_count_text}
候选列表:
{chr(10).join(candidates_info)}

选择要求：
- 优先选择与目录语义最贴近的条目，不要只看基础标题是否完全一致。
- 如果目录明显像系列简称、短篇合集、特典或外传，优先考虑候选的副标题与季度结构。
- 如果提供了本地视频数量，可把它作为辅助证据，优先考虑季度/特别篇集数更贴近的候选。

请严格返回 JSON：
{{
  "index": 1,
  "confidence": "High/Medium/Low",
  "reason": "简短说明"
}}
"""

        system_prompt = (
            "你是动漫与电视剧匹配助手。根据目录名、候选标题与季度结构选择最匹配的 TMDB 候选。"
            "必须只返回 JSON。"
        )

        try:
            result = ai_client._call_openai_simple(
                system_prompt,
                prompt,
                max_retries=1,
                validation_key="index",
            )

            parsed = self._parse_selection_result(result)
            if not parsed:
                return None, None

            index_value = parsed.get('index')
            confidence_value = parsed.get('confidence')
            if not isinstance(index_value, int):
                return None, None

            idx = index_value - 1
            confidence = confidence_value if isinstance(confidence_value, str) else None
            if 0 <= idx < len(candidates):
                logger.info(
                    f"[AI选择] 电视剧 {folder_name} -> 候选#{idx + 1}, "
                    f"confidence={confidence}"
                )
                return candidates[idx], confidence
        except Exception as e:
            logger.warning(f"[AI选择] 电视剧选择失败: {e}")

        return None, None

    def _process_movie_collection(
        self,
        path: Path,
        collection_result: MovieCollectionResult,
        work_path: Path,
        ai_client: AIClient,
    ) -> tuple[list[MovieProcessResult], list[str]]:
        """处理电影合集，返回处理成功的电影和未解决项。"""
        if not isinstance(collection_result, MovieCollectionResult):
            logger.error("[电影合集] 无效的合集分析结果")
            return [], ["invalid_collection_result"]

        processed_movies: list[MovieProcessResult] = []
        unresolved: list[str] = []

        for mapping in collection_result.file_mapping:
            normalized_rel = mapping.file_path.replace('\\', '/').lstrip('/')
            file_path = (path / normalized_rel).resolve()
            if not file_path.exists():
                unresolved.append(f"文件不存在:{mapping.file_path}")
                continue

            query_variants = (
                ai_client.generate_movie_search_queries(
                    mapping.movie_title,
                    collection_result.collection_name,
                )
                or build_movie_search_queries(
                    mapping.movie_title,
                    collection_result.collection_name,
                )
            )
            candidates = self.search.search_movies_by_title(
                mapping.movie_title,
                mapping.year,
                limit=5,
                collection_name=collection_result.collection_name,
                query_variants=query_variants,
            )
            if not candidates:
                unresolved.append(
                    f"TMDB无结果:{mapping.movie_title} -> {query_variants[:3]}"
                )
                continue

            deterministic_selected, deterministic_confidence = (
                self._select_ranked_movie_candidate(candidates)
            )
            if deterministic_selected:
                selected = deterministic_selected
                selected_confidence = deterministic_confidence
            else:
                selected, selected_confidence = self._ai_select_movie(
                    ai_client,
                    file_path.name,
                    mapping.movie_title,
                    candidates,
                )
                if not selected:
                    unresolved.append(
                        f"AI未选中候选:{mapping.movie_title} -> {query_variants[:2]}"
                    )
                    continue
                if not self._is_confidence_acceptable(selected_confidence):
                    unresolved.append(
                        (
                            "候选置信度不足:"
                            f"{mapping.movie_title}({selected_confidence})"
                        )
                    )
                    continue

            selected_id = _as_int(selected.get('id'))
            if selected_id is None:
                unresolved.append(f"无法获取详情:{mapping.movie_title}")
                continue

            movie_info = self.search.get_movie_info_by_id(selected_id)
            if not movie_info:
                unresolved.append(f"无法获取详情:{mapping.movie_title}")
                continue

            movie_name = _as_str(movie_info.get('title')) or _as_str(selected.get('title')) or ''
            release_date = _as_str(movie_info.get('release_date')) or ''
            movie_year = release_date.split('-')[0] if release_date else None
            movie_genres = movie_info.get('genres', [])

            movie_folder = FilenameBuilder.build_movie_folder(movie_name, movie_year)
            target_folder = work_path / movie_folder

            video_format = extract_video_format(file_path.name)
            part = extract_part(file_path.name)
            resource_term = self._extract_resource_term(file_path.name)
            release_group = self._extract_release_group(file_path.name)
            meta = MovieMetadata(
                title=movie_name,
                year=movie_year,
                video_format=video_format,
                resource_term=resource_term,
                release_group=release_group,
                part=part,
                file_ext=file_path.suffix,
            )
            new_filename = FilenameBuilder.build_movie_filename(meta)
            target_file = target_folder / new_filename

            processed_movies.append(
                {
                    'movie_name': movie_name,
                    'movie_year': movie_year,
                    'file_path': file_path,
                    'target_file': target_file,
                    'ai_confidence': selected_confidence or mapping.confidence,
                     'tmdb_id': movie_info.get('id', selected_id),
                    'poster_path': movie_info.get('poster_path'),
                    'tmdb_name': movie_name,
                    'tmdb_year': movie_year,
                    'tmdb_media_type': 'movie',
                    'tmdb_genres': movie_genres,
                    'release_group': self._extract_release_group(
                        file_path.name
                    ),
                    'resource_term': self._extract_resource_term(
                        file_path.name
                    ),
                }
            )

            logger.info(
                f"[电影合集] 映射: {file_path.name} -> "
                f"{movie_folder}/{target_file.name}"
            )

        return processed_movies, unresolved

    def _resolve_single_movie_subset_result(
        self,
        path: Path,
        mapping: MovieFileMapping,
        collection_name: str,
    ) -> tuple[MovieProcessResult | None, TmdbInfo | None]:
        movie_title = mapping.movie_title.strip()
        if not movie_title:
            return None, None

        normalized_rel = mapping.file_path.replace('\\', '/').lstrip('/')
        file_path = (path / normalized_rel).resolve()
        if not file_path.exists():
            return None, None

        query_variants = build_movie_search_queries(movie_title, collection_name)
        candidates = self.search.search_movies_by_title(
            movie_title,
            mapping.year,
            limit=5,
            collection_name=collection_name,
            query_variants=query_variants,
        )
        if not candidates:
            return None, None

        selected, selected_confidence = self._select_ranked_movie_candidate(candidates)
        if not selected:
            return None, None

        selected_id = _as_int(selected.get('id'))
        if selected_id is None:
            return None, None

        movie_info = self.search.get_movie_info_by_id(selected_id)
        if not movie_info:
            return None, None

        movie_name = _as_str(movie_info.get('title')) or _as_str(selected.get('title')) or ''
        release_date = _as_str(movie_info.get('release_date')) or ''
        movie_year = release_date.split('-')[0] if release_date else None
        movie_folder = FilenameBuilder.build_movie_folder(movie_name, movie_year)
        work_root = self.ANIME_MOVIE_PATH if self._detect_anime_genre(movie_info) else self.MOVIE_PATH
        target_folder = work_root / movie_folder

        meta = MovieMetadata(
            title=movie_name,
            year=movie_year,
            video_format=extract_video_format(file_path.name),
            resource_term=self._extract_resource_term(file_path.name),
            release_group=self._extract_release_group(file_path.name),
            part=extract_part(file_path.name),
            file_ext=file_path.suffix,
        )
        target_file = target_folder / FilenameBuilder.build_movie_filename(meta)

        return (
            {
                'movie_name': movie_name,
                'movie_year': movie_year,
                'file_path': file_path,
                'target_file': target_file,
                'ai_confidence': selected_confidence or mapping.confidence,
                'tmdb_id': movie_info.get('id', selected_id),
                'poster_path': movie_info.get('poster_path'),
                'tmdb_name': movie_name,
                'tmdb_year': movie_year,
                'tmdb_media_type': 'movie',
                'tmdb_genres': movie_info.get('genres', []),
                'release_group': self._extract_release_group(file_path.name),
                'resource_term': self._extract_resource_term(file_path.name),
            },
            movie_info,
        )

    def _evaluate_dual_route_decision(
        self,
        path: Path,
        task_plan: TaskTypePlan,
    ) -> bool | None:
        if not path.is_dir():
            return None

        tv_candidate = task_plan['tv_candidate']
        movie_candidate = task_plan['movie_candidate']
        mixed_parent_plan = task_plan['mixed_parent_plan']
        has_tv = bool(tv_candidate.get('available'))
        has_movie = bool(movie_candidate.get('available'))
        if not (has_tv and has_movie):
            return None

        ai_type = task_plan.get('ai_type')
        selected_is_movie = task_plan['is_movie']
        video_count = mixed_parent_plan['total_video_count']
        structured_tv_episode_signal = self._has_structured_tv_episode_signal(path)
        has_tv_hint = self._has_tv_hint(path.name) or structured_tv_episode_signal
        has_movie_hint = self._has_movie_hint(path.name)

        tv_confidence = cast(str | None, tv_candidate.get('confidence'))
        movie_confidence = cast(str | None, movie_candidate.get('confidence'))
        tv_ok = self._is_confidence_acceptable(tv_confidence)
        movie_ok = self._is_confidence_acceptable(movie_confidence)
        has_mixed_cues = self._has_mixed_bundle_cues(path)

        if not movie_ok and tv_ok:
            return False
        if not tv_ok and movie_ok:
            return True

        tv_claim_count = mixed_parent_plan['tv_claimed_file_count']
        movie_claim_count = mixed_parent_plan['movie_claimed_file_count']
        if tv_ok and tv_claim_count > 0 and movie_claim_count == 0:
            logger.info(
                '[处理任务] 双路决策覆盖: Movie 候选没有可执行子集，'
                f'使用 TV strict 子集 (tv_claims={tv_claim_count})'
            )
            return False
        if movie_ok and movie_claim_count > 0 and tv_claim_count == 0 and not has_tv_hint:
            logger.info(
                '[处理任务] 双路决策覆盖: TV 候选没有可执行子集，'
                f'使用 Movie 子集 (movie_claims={movie_claim_count})'
            )
            return True

        if has_mixed_cues and tv_ok and (has_tv_hint or video_count >= 20):
            logger.info(
                '[处理任务] 双路决策覆盖: 检测到 mixed/bundle 结构，'
                f"优先进入 TV strict 链路 (tv_claims={mixed_parent_plan['tv_claimed_file_count']}, "
                f"movie_claims={mixed_parent_plan['movie_claimed_file_count']})"
            )
            return False

        if selected_is_movie and video_count >= 3 and has_tv_hint and not has_movie_hint:
            logger.info('[处理任务] 双路决策覆盖: 目录明显偏TV，优先进入TV链路')
            return False

        if not selected_is_movie and has_movie_hint and not has_tv_hint and video_count <= 3:
            logger.info('[处理任务] 双路决策覆盖: 目录明显偏Movie，优先进入Movie链路')
            return True

        if selected_is_movie and ai_type == 'tv' and tv_ok:
            logger.info('[处理任务] 双路决策覆盖: AI类型偏TV，优先进入TV链路')
            return False

        if (not selected_is_movie) and ai_type == 'movie' and movie_ok and not tv_ok:
            logger.info('[处理任务] 双路决策覆盖: AI类型偏Movie，优先进入Movie链路')
            return True

        return None

    def _extract_single_movie_files_from_collection_result(
        self,
        collection_result: MovieCollectionResult,
        video_files: list[Path],
        base_path: Path,
    ) -> list[Path]:
        """从合集分析结果中提取可回退为单电影处理的正片文件。"""
        if collection_result.is_collection:
            return []

        if len(collection_result.file_mapping) == 1:
            mapping = collection_result.file_mapping[0]
            if self._is_confidence_acceptable(mapping.confidence):
                rel_path = mapping.file_path.replace('\\', '/').lstrip('/')
                candidates = {
                    str(p.relative_to(base_path)).replace('\\', '/'): p
                    for p in video_files
                }
                matched = candidates.get(rel_path)
                if matched:
                    return [matched]

        return self._fallback_single_movie_files_from_collection_result(
            video_files,
            base_path,
        )

    @staticmethod
    def _is_movie_collection_extra_file(path: Path, base_path: Path) -> bool:
        relative_text = str(path.relative_to(base_path)).replace('\\', '/').lower()
        file_name = path.name
        if is_promotional_content(file_name):
            return True

        text = f'{relative_text} {file_name.lower()}'
        token_patterns = (
            r'(^|[^a-z0-9])extras?([^a-z0-9]|$)',
            r'(^|[^a-z0-9])bonus([^a-z0-9]|$)',
            r'(^|[^a-z0-9])sps?([^a-z0-9]|$)',
            r'(^|[^a-z0-9])menu([^a-z0-9]|$)',
            r'(^|[^a-z0-9])preview([^a-z0-9]|$)',
            r'(^|[^a-z0-9])trailer([^a-z0-9]|$)',
            r'(^|[^a-z0-9])teaser([^a-z0-9]|$)',
            r'(^|[^a-z0-9])cm([^a-z0-9]|$)',
            r'(^|[^a-z0-9])pv([^a-z0-9]|$)',
            r'tv spot',
            r'(^|[^a-z0-9])commentary([^a-z0-9]|$)',
            r'(^|[^a-z0-9])event([^a-z0-9]|$)',
            r'(^|[^a-z0-9])interview([^a-z0-9]|$)',
            r'(^|[^a-z0-9])making([^a-z0-9]|$)',
            r'(^|[^a-z0-9])digest([^a-z0-9]|$)',
            r'(^|[^a-z0-9])logo([^a-z0-9]|$)',
            r'特典',
            r'映像特典',
        )
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in token_patterns)

    @classmethod
    def _has_structured_tv_episode_signal(cls, base_path: Path) -> bool:
        if not base_path.exists() or not base_path.is_dir():
            return False

        episode_numbers: set[int] = set()
        patterns = (
            r'\bS\d{1,2}E(\d{1,3})\b',
            r'\[(\d{1,3})\]',
            r'\bE0*(\d{1,3})\b',
            r'第\s*0*(\d{1,3})\s*[话話集]\b',
        )

        for path in base_path.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIX:
                continue
            if cls._is_movie_collection_extra_file(path, base_path):
                continue

            stem = path.stem
            for pattern in patterns:
                match = re.search(pattern, stem, re.IGNORECASE)
                if not match:
                    continue
                try:
                    episode_number = int(match.group(1))
                except (TypeError, ValueError):
                    continue
                if episode_number > 0:
                    episode_numbers.add(episode_number)
                    break

            if len(episode_numbers) >= 3:
                return True

        return False

    def _fallback_single_movie_files_from_collection_result(
        self,
        video_files: list[Path],
        base_path: Path,
    ) -> list[Path]:
        main_candidates = [
            path for path in video_files
            if not self._is_movie_collection_extra_file(path, base_path)
        ]
        if len(main_candidates) != 1:
            return []
        return main_candidates

    def _validate_movie_collection_result(
        self,
        collection_result: MovieCollectionResult,
        video_files: list[Path],
        base_path: Path,
    ) -> tuple[bool, str | None, str]:
        """验证电影合集 AI 结果可执行性。"""
        if not collection_result.is_collection:
            return False, "ai_empty_mapping", "AI未识别为电影合集"

        if not collection_result.file_mapping:
            return False, "ai_empty_mapping", "合集映射为空"

        existing_rel_paths = {
            str(p.relative_to(base_path)).replace('\\', '/')
            for p in video_files
        }

        mapped_rel_paths: set[str] = set()
        conflicts: list[str] = []
        low_conf_items: list[str] = []

        for mapping in collection_result.file_mapping:
            rel_path = mapping.file_path.replace('\\', '/').lstrip('/')

            if not mapping.movie_title.strip():
                conflicts.append(f"缺少电影标题:{rel_path}")

            if rel_path in mapped_rel_paths:
                conflicts.append(f"重复文件映射:{rel_path}")
            mapped_rel_paths.add(rel_path)

            if rel_path not in existing_rel_paths:
                conflicts.append(f"文件不存在:{rel_path}")

            if not self._is_confidence_acceptable(mapping.confidence):
                low_conf_items.append(
                    f"{rel_path}({mapping.confidence})"
                )

        if hasattr(collection_result, 'conflict_details'):
            collection_result.conflict_details = sorted(
                set(collection_result.conflict_details + conflicts)
            )

        unmatched = sorted(existing_rel_paths - mapped_rel_paths)
        if hasattr(collection_result, 'unmatched_files'):
            collection_result.unmatched_files = unmatched

        if low_conf_items:
            detail = (
                "文件映射置信度不足: "
                + ', '.join(low_conf_items[:5])
            )
            return False, "ai_low_confidence", detail

        if conflicts:
            return False, "ai_invalid_mapping", '; '.join(conflicts[:5])

        if not mapped_rel_paths:
            return False, "ai_empty_mapping", "合集映射未命中任何本地文件"

        return True, None, ''

    def _detect_season_id_from_mapping(self, mapping: dict[Path, Path]) -> int:
        detected_seasons: set[int] = set()
        for target_path in mapping.values():
            for part in target_path.parts:
                if not part.startswith('Season '):
                    continue
                try:
                    detected_seasons.add(int(part.replace('Season ', '')))
                except ValueError:
                    continue

        if not detected_seasons:
            return 1
        if detected_seasons == {0}:
            return 0

        non_zero = sorted(s for s in detected_seasons if s > 0)
        return non_zero[0] if non_zero else 0

    def _detect_anime_genre(self, info: TmdbInfo) -> bool:
        genres = info.get('genres', [])
        if not isinstance(genres, list):
            return False
        for genre in genres:
            if not isinstance(genre, dict):
                continue
            genre_name = str(genre.get('name', '')).lower()
            if genre_name in ('animation', 'anime', '动画', 'アニメ'):
                return True
        return False

    def _has_tv_hint(self, name: str) -> bool:
        tv_hint_patterns = [
            r'\bS\d{1,2}E\d{1,3}\b',
            r'\bE\d{1,3}\b',
            r'第[\d一二三四五六七八九十零]{1,3}[话話集]',
            r'\b(OVA|OAD|SPECIALS?)\b',
            r'\bSP\d{0,3}\b',
            r'\bS00\b',
            r'Season[\s._-]*0',
            r'第0季',
            r'特别篇',
            r'特典',
        ]
        return any(
            re.search(pattern, name, re.IGNORECASE)
            for pattern in tv_hint_patterns
        )

    def _has_movie_hint(self, name: str) -> bool:
        movie_keywords = [
            'MOVIE',
            'FILM',
            '剧场版',
            '劇場版',
            '电影',
            '電影',
        ]
        lower_name = name.lower()
        return any(keyword.lower() in lower_name for keyword in movie_keywords)

    def _select_ranked_movie_candidate(
        self,
        candidates: list[TmdbInfo],
    ) -> tuple[TmdbInfo | None, str | None]:
        if not candidates:
            return None, None

        if len(candidates) == 1:
            candidate = candidates[0]
            score = candidate.get('_match_score')
            if isinstance(score, (int, float)) and float(score) >= 70.0:
                return candidate, 'High'
            return None, 'Low'

        first = candidates[0]
        second = candidates[1]
        first_score = _float_score(first.get('_match_score', 0))
        second_score = _float_score(second.get('_match_score', 0))
        score_gap = first_score - second_score

        if first_score >= 120 or (first_score >= 108 and score_gap >= 15):
            return first, 'High'

        if first_score >= 98 and score_gap >= 12:
            return first, 'Medium'

        return None, None

    def _should_try_movie_collection(
        self,
        path: Path,
        ai_type: str | None,
        has_movie_hint: bool,
        has_tv_hint: bool,
    ) -> bool:
        if not path.is_dir():
            return False

        movie_like_patterns = [
            r'#\d{1,3}\b',
            r'\bmovie[\s._-]*\d{1,3}\b',
            r'\bcase[\s._-]*\d{1,3}\b',
            r'第[零〇一二三四五六七八九十百\d]{1,4}章',
            r'\bchapter[\s._-]*\d{1,3}\b',
        ]

        video_count = 0
        movie_like_count = 0
        tv_like_count = 0
        for item in path.rglob('*'):
            if not item.is_file() or item.suffix.lower() not in VIDEO_SUFFIX:
                continue

            video_count += 1
            filename = item.name
            if self._has_tv_hint(filename):
                tv_like_count += 1
            if any(
                re.search(pattern, filename, re.IGNORECASE)
                for pattern in movie_like_patterns
            ):
                movie_like_count += 1

        if video_count <= 1:
            return False

        if ai_type == 'tv' and not has_movie_hint:
            return False

        if has_tv_hint and not has_movie_hint and tv_like_count >= movie_like_count:
            return False

        if movie_like_count >= 2 and movie_like_count >= tv_like_count:
            return True

        if (has_movie_hint or ai_type == 'movie') and tv_like_count == 0:
            return True

        return False

    def _get_collection_seed_movie_info(
        self,
        queries: list[str],
        year: int,
    ) -> tuple[str, TmdbInfo | None, str | None]:
        best_candidates: list[TmdbInfo] | None = None
        best_score = -1.0

        for query in queries:
            candidates = self.search.search_movies_by_title(
                query,
                year if year != 0 else None,
                limit=5,
                query_variants=[query],
            )
            if not candidates:
                continue

            score = _float_score(candidates[0].get('_match_score', 0))
            if score > best_score:
                best_score = score
                best_candidates = candidates

        if not best_candidates:
            return '', None, None

        selected, selection_confidence = self._select_ranked_movie_candidate(
            best_candidates
        )
        if not selected:
            selected = best_candidates[0]
            selection_confidence = 'Low'

        movie_id = selected.get('id')
        if not isinstance(movie_id, int):
            return '', None, None

        movie_info = self.search.get_movie_info_by_id(movie_id)
        if not movie_info:
            return '', None, None

        movie_name = _as_str(movie_info.get('title')) or _as_str(selected.get('title')) or ''
        return movie_name, movie_info, selection_confidence

    def _is_confidence_acceptable(self, confidence: str | None) -> bool:
        if not confidence:
            return False

        rank = {'Low': 1, 'Medium': 2, 'High': 3}
        threshold = cm.get_config('ai_confidence_threshold') or 'Medium'
        return rank.get(confidence, 0) >= rank.get(threshold, 2)

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
        codec_tokens = [
            ("HEVC", "HEVC"),
            ("X265", "x265"),
            ("X264", "x264"),
            ("AV1", "AV1"),
            ("10BIT", "10bit"),
            ("8BIT", "8bit"),
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

        for token, display in codec_tokens:
            if token in upper_name and display not in parts:
                parts.append(display)

        return " ".join(parts)

    def _resolve_task_poster_path(
        self,
        info: TmdbInfo | None,
        is_movie: bool,
        season_id: int | None,
    ) -> str | None:
        if not isinstance(info, dict):
            return None

        series_poster = _as_str(info.get("poster_path"))
        if is_movie:
            return series_poster

        if not isinstance(season_id, int):
            return series_poster

        seasons = info.get("seasons")
        if not isinstance(seasons, list):
            return series_poster

        for season in seasons:
            if not isinstance(season, dict):
                continue
            if season.get("season_number") != season_id:
                continue

            season_poster = season.get("poster_path")
            if isinstance(season_poster, str) and season_poster.strip():
                return season_poster
            break

        return series_poster

    def _parse_selection_result(
        self,
        result: str | None,
    ) -> dict[str, int | str] | None:
        if not result:
            return None

        try:
            parsed: object = json.loads(result)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', result, re.DOTALL)
            if not match:
                return None
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                return None

        if not isinstance(parsed, dict):
            return None

        index_value = parsed.get('index')
        if not isinstance(index_value, int):
            return None
        index = index_value

        confidence = str(parsed.get('confidence', 'Medium'))
        if confidence not in ['High', 'Medium', 'Low']:
            confidence = 'Medium'

        return {'index': index, 'confidence': confidence}

    def _detect_target_collision(self, mapping: dict[Path, Path]) -> str | None:
        seen: dict[str, Path] = {}
        for source_path, target_path in mapping.items():
            key = str(target_path).casefold()
            existing_source = seen.get(key)
            if existing_source is not None and existing_source != source_path:
                return (
                    f'{existing_source.name} 与 {source_path.name} '
                    f'同时映射到 {target_path.name}'
                )
            seen[key] = source_path
        return None

    def _failure_message(self, reason: str, detail: str | None = None) -> str:
        base = FAILURE_MESSAGES.get(reason, FAILURE_MESSAGES['ai_timeout'])
        if detail:
            return f"{base}: {detail}"
        return base

    def _write_task_data(self, task_data: dict[str, object]) -> None:
        task_path = TASK_PATH / f"{task_data['uuid']}.json"
        with open(task_path, 'w', encoding='UTF-8') as file:
            json.dump(task_data, file, indent=4, ensure_ascii=False)

    def error_reply(
        self,
        _uuid: str,
        error: str,
        path: Path,
        is_anime: bool | None = None,
        is_movie: bool | None = None,
        name: str | None = None,
        season_id: int | None = None,
        failure_reason: str | None = None,
        ai_attempted: bool = False,
        ai_used: bool = False,
        ai_confidence: str | None = None,
        extra_task_data: dict[str, object] | None = None,
    ) -> str:
        task_data: dict[str, object] = {
            'path': str(path),
            'is_anime': is_anime,
            'is_movie': is_movie,
            'name': name,
            'season_id': season_id,
            'uuid': str(_uuid),
            'error': error,
            'use_ai': ai_used,
            'ai_attempted': ai_attempted,
            'ai_used': ai_used,
            'ai_confidence': ai_confidence,
            'failure_reason': failure_reason,
            'pipeline_mode': 'ai_strict',
            'tmdb_id': None,
            'poster_path': None,
            'tmdb_name': None,
            'tmdb_year': None,
            'tmdb_media_type': None,
            'tmdb_genres': [],
            'release_group': None,
            'resource_term': None,
            'target_root': None,
        }
        if extra_task_data:
            task_data.update(extra_task_data)
        self._write_task_data(task_data)
        return error


def _main() -> int:
    parser = argparse.ArgumentParser(description='Rename process debug helpers')
    parser.add_argument(
        '--debug-tv-subset',
        metavar='SAMPLE_JSON',
        help='Materialize a sample-pool JSON and print validated TV subset claims',
    )
    parser.add_argument(
        '--debug-movie-subset',
        metavar='SAMPLE_JSON',
        help='Materialize a sample-pool JSON and print validated Movie subset claims',
    )
    parser.add_argument(
        '--candidate-json',
        metavar='CANDIDATE_JSON',
        help='Optional TV/Movie candidate payload used for strict validation replay',
    )
    parser.add_argument(
        '--debug-mixed-parent',
        metavar='SAMPLE_JSON',
        help='Materialize a sample-pool JSON and print mixed parent planning state',
    )
    parser.add_argument(
        '--tv-candidate-json',
        metavar='TV_CANDIDATE_JSON',
        help='Optional TV candidate payload used with --debug-mixed-parent',
    )
    parser.add_argument(
        '--movie-candidate-json',
        metavar='MOVIE_CANDIDATE_JSON',
        help='Optional Movie candidate payload used with --debug-mixed-parent',
    )
    parser.add_argument(
        '--inject-overlap-relative-path',
        metavar='RELATIVE_PATH',
        help='QA-only: inject one claimed relative path into the movie subset to force overlap rejection',
    )
    parser.add_argument(
        '--debug-execute-mixed-parent',
        metavar='SAMPLE_JSON',
        help='Materialize a sample-pool JSON and dry-run mixed parent execution using validated child subsets',
    )
    parser.add_argument(
        '--debug-write-mixed-parent',
        metavar='SAMPLE_JSON',
        help='Materialize a sample-pool JSON and perform real mixed parent task/record writes',
    )
    parser.add_argument(
        '--debug-output-root',
        metavar='OUTPUT_DIR',
        help='Optional output root used by --debug-write-mixed-parent',
    )
    args = parser.parse_args()

    if args.debug_tv_subset:
        rename = Rename()
        sample_json_path = Path(args.debug_tv_subset)
        candidate_json_path = Path(args.candidate_json) if args.candidate_json else None
        debug_result = rename.debug_plan_tv_subset_from_sample(
            sample_json_path,
            candidate_json_path,
        )
        print(json.dumps(debug_result, ensure_ascii=False, indent=2))
        return 0

    if args.debug_movie_subset:
        rename = Rename()
        sample_json_path = Path(args.debug_movie_subset)
        candidate_json_path = Path(args.candidate_json) if args.candidate_json else None
        debug_result = rename.debug_plan_movie_subset_from_sample(
            sample_json_path,
            candidate_json_path,
        )
        print(json.dumps(debug_result, ensure_ascii=False, indent=2))
        return 0

    if args.debug_mixed_parent:
        rename = Rename()
        sample_json_path = Path(args.debug_mixed_parent)
        tv_candidate_json_path = (
            Path(args.tv_candidate_json) if args.tv_candidate_json else None
        )
        movie_candidate_json_path = (
            Path(args.movie_candidate_json) if args.movie_candidate_json else None
        )
        debug_result = rename.debug_plan_mixed_parent_from_sample(
            sample_json_path,
            tv_candidate_json_path=tv_candidate_json_path,
            movie_candidate_json_path=movie_candidate_json_path,
            inject_overlap_relative_path=args.inject_overlap_relative_path,
        )
        print(json.dumps(debug_result, ensure_ascii=False, indent=2))
        return 0

    if args.debug_execute_mixed_parent:
        rename = Rename()
        sample_json_path = Path(args.debug_execute_mixed_parent)
        tv_candidate_json_path = (
            Path(args.tv_candidate_json) if args.tv_candidate_json else None
        )
        movie_candidate_json_path = (
            Path(args.movie_candidate_json) if args.movie_candidate_json else None
        )
        debug_result = rename.debug_execute_mixed_parent_from_sample(
            sample_json_path,
            tv_candidate_json_path=tv_candidate_json_path,
            movie_candidate_json_path=movie_candidate_json_path,
        )
        print(json.dumps(debug_result, ensure_ascii=False, indent=2))
        return 0

    if args.debug_write_mixed_parent:
        rename = Rename()
        sample_json_path = Path(args.debug_write_mixed_parent)
        tv_candidate_json_path = (
            Path(args.tv_candidate_json) if args.tv_candidate_json else None
        )
        movie_candidate_json_path = (
            Path(args.movie_candidate_json) if args.movie_candidate_json else None
        )
        output_root = (
            Path(args.debug_output_root)
            if args.debug_output_root
            else Path(tempfile.mkdtemp(prefix='mixed-write-proof-'))
        )
        debug_result = rename.debug_write_mixed_parent_from_sample(
            sample_json_path,
            output_root=output_root,
            tv_candidate_json_path=tv_candidate_json_path,
            movie_candidate_json_path=movie_candidate_json_path,
        )
        print(json.dumps(debug_result, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
