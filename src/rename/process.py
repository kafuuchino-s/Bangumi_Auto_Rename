import json
import re
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .trans import Trans
from ..logger import logger
from .get_info import Search
from ..utils.path import TASK_PATH
from .ai_processor import AIProcessor
from ..config.config_manager import cm
from ..ai.client import AIClient
from ..ai.models import MovieCollectionResult
from ..ai.video_analyzer import VideoAnalyzer
from ..bangumi.context_builder import AnimeInfoDict
from .utils import VIDEO_SUFFIX
from .ai_processor import FileAnalysisDict
from .cleaner import (
    build_movie_search_queries,
    divide_by_year,
    extract_part,
    extract_video_format,
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
    "ai_invalid_mapping": "[AI] AI返回映射存在冲突或越界",
    "tmdb_not_found": "[TMDB] 未搜索到匹配结果",
    "invalid_path": "[路径] 输入路径无效",
    "tmdb_key_missing": "你还没有配置TMDB的Key！任务失败！请先前往配置界面！",
    "trans_failed": "[迁移] 文件迁移失败",
}

TmdbInfo = dict[str, object]
MovieProcessResult = dict[str, object]
SelectionResult = tuple[str, TmdbInfo, bool, bool, str | None]
EnqueueTask = Callable[..., str]


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

        name, info, is_anime, is_movie, type_ai_confidence = task_type

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
            video_files = self.ai_processor._collect_video_files(path)
            primary_source_name = video_files[0].name if video_files else path.name
            release_group = self._extract_release_group(primary_source_name)
            resource_term = self._extract_resource_term(primary_source_name)
            if not video_files:
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
                )

            file_analysis = self.ai_processor.video_analyzer.analyze_video_files(
                path,
                video_files,
            )
            all_local_files = self.ai_processor._collect_all_local_files(path)

            tv_info_typed = cast(AnimeInfoDict, cast(object, tv_info))
            file_analysis_typed = cast(list[FileAnalysisDict], file_analysis)

            ai_result = self.ai_processor.analyze_anime_files(
                path,
                tv_info_typed,
                video_files=video_files,
                file_analysis=file_analysis_typed,
            )
            if not ai_result:
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

            task_ai_confidence = ai_result.confidence
            if not self._is_confidence_acceptable(ai_result.confidence):
                return self.error_reply(
                    _uuid,
                    self._failure_message(
                        "ai_low_confidence",
                        f"结果置信度={ai_result.confidence}",
                    ),
                    path,
                    is_anime,
                    is_movie,
                    name,
                    season_id,
                    failure_reason="ai_low_confidence",
                    ai_attempted=True,
                    ai_used=True,
                    ai_confidence=ai_result.confidence,
                )

            valid, reason, detail = self.ai_processor.validate_tv_result(
                ai_result,
                tv_info_typed,
                path,
                video_files=video_files,
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
                    ai_confidence=ai_result.confidence,
                )

            self.mapping = self.ai_processor.apply_ai_mapping(
                ai_result=ai_result,
                anime_info=tv_info_typed,
                base_path=path,
                work_path=work_path,
                all_local_files=all_local_files,
            )
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
    ) -> SelectionResult | str:
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

        has_tv_hint = self._has_tv_hint(path.name)
        has_movie_hint = self._has_movie_hint(path.name)
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

        return (
            selected_name,
            selected_info,
            bool(final_is_anime),
            bool(final_is_movie),
            selected_confidence,
        )

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
        deterministic_selected = None
        deterministic_confidence = None
        should_force_ai_selection = False
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

        if preferred_season and len(ranked_candidates) > 1:
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

        if not deterministic_selected and not should_force_ai_selection:
            deterministic_selected, deterministic_confidence = (
                self.search._select_ranked_tv_candidate(ranked_candidates)
            )

        if deterministic_selected:
            selected = deterministic_selected
            selection_confidence = deterministic_confidence
        elif len(ranked_candidates) == 1:
            selected = ranked_candidates[0]
            selection_confidence = 'High'
        else:
            selected, selection_confidence = self._ai_select_tv(
                ai_client,
                folder_name,
                query,
                ranked_candidates,
                local_video_count=local_video_count,
            )
            if not selected:
                return '', None, selection_confidence, 'ai_low_confidence'
            if not self._is_confidence_acceptable(selection_confidence):
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

        if deterministic_selected:
            pass
        elif len(candidates) == 1:
            selected = candidates[0]
            selection_confidence = 'High'
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
            if ai_client.provider.lower() == "gemini":
                result = ai_client._call_gemini_simple(
                    system_prompt,
                    prompt,
                    max_retries=1,
                    validation_key="index",
                )
            else:
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
            if ai_client.provider.lower() == "gemini":
                result = ai_client._call_gemini_simple(
                    system_prompt,
                    prompt,
                    max_retries=1,
                    validation_key="index",
                )
            else:
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
            elif len(candidates) == 1:
                selected = candidates[0]
                selected_confidence = mapping.confidence
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

    def _extract_single_movie_files_from_collection_result(
        self,
        collection_result: MovieCollectionResult,
        video_files: list[Path],
        base_path: Path,
    ) -> list[Path]:
        """从合集分析结果中提取可回退为单电影处理的正片文件。"""
        if collection_result.is_collection:
            return []

        if len(collection_result.file_mapping) != 1:
            return []

        mapping = collection_result.file_mapping[0]
        if not self._is_confidence_acceptable(mapping.confidence):
            return []

        rel_path = mapping.file_path.replace('\\', '/').lstrip('/')
        candidates = {
            str(p.relative_to(base_path)).replace('\\', '/'): p
            for p in video_files
        }
        matched = candidates.get(rel_path)
        if not matched:
            return []

        return [matched]

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
            return candidates[0], 'High'

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
