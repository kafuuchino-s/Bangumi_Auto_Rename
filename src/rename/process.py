from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..ai.client import AIClient
from ..config.config_manager import cm
from ..logger import logger
from ..utils.path import RECORD_PATH, TASK_PATH
from .bgm_to_tmdb import (
    TmdbLegalGraph,
    TmdbRenamePlan,
    TmdbRenamePlanRoots,
    VerifiedBgmToTmdbPlan,
    compile_bgm_to_tmdb_input,
    compile_verified_bgm_to_tmdb_rename_plan,
    run_bgm_to_tmdb_bridge_agent,
    write_bgm_to_tmdb_rename_plan_artifacts,
)
from .case_agent.local_bangumi_entry import run_local_bangumi_case_agent_mapping
from .case_agent.recipe import CompiledOrganizePlan
from .cleaner import remove_episode, remove_season, remove_tag
from .decision_snapshot import write_decision_snapshot
from .local_evidence import LocalEvidence, build_local_evidence
from .local_fact_surface import SUBTITLE_EXTENSIONS, _subtitle_matches_video
from .get_info import Search
from .trans import Trans
from .utils import VIDEO_SUFFIX


# 复用字幕导入的语言映射，用于同目录字幕跟随迁移
_SUBTITLE_LANGUAGE_MAP: dict[str, tuple[str, bool]] = {
    # 简体中文
    'chs': ('zh-CN', True),
    'sc': ('zh-CN', True),
    'gb': ('zh-CN', True),
    '简': ('zh-CN', True),
    '简体': ('zh-CN', True),
    '简中': ('zh-CN', True),
    'zh-hans': ('zh-CN', True),
    'zh-cn': ('zh-CN', True),
    'cn': ('zh-CN', True),
    'chinese': ('zh-CN', True),
    # 繁体中文
    'cht': ('zh-TW', False),
    'tc': ('zh-TW', False),
    'big5': ('zh-TW', False),
    '繁': ('zh-TW', False),
    '繁体': ('zh-TW', False),
    '繁中': ('zh-TW', False),
    'zh-hant': ('zh-TW', False),
    'zh-tw': ('zh-TW', False),
    'tw': ('zh-TW', False),
    'zh-hk': ('zh-HK', False),
    'hk': ('zh-HK', False),
    # 日语
    'jp': ('ja', False),
    'jpn': ('ja', False),
    'ja': ('ja', False),
    'japanese': ('ja', False),
    '日': ('ja', False),
    '日语': ('ja', False),
    # 英语
    'en': ('en', False),
    'eng': ('en', False),
    'english': ('en', False),
    # 韩语
    'ko': ('ko', False),
    'kor': ('ko', False),
    'korean': ('ko', False),
}


EnqueueTask = Callable[..., str]
TmdbInfo = dict[str, object]


FAILURE_MESSAGES = {
    'ai_unavailable': '[AI] AI service is unavailable',
    'ai_empty_mapping': '[Case Agent] no processable video files found',
    'invalid_path': '[Path] input path is invalid',
    'local_bangumi_case_agent_primary': '[Case Agent] Local->Bangumi mapping-only result written',
    'local_bangumi_case_agent_primary_error': '[Case Agent] Local->Bangumi Case Agent failed',
    'bgm_to_tmdb_bridge_failed': '[BGM->TMDB] bridge failed',
    'bgm_to_tmdb_rename_plan_invalid': '[BGM->TMDB] final rename plan rejected',
    'bgm_to_tmdb_rename_plan_dry_run': '[BGM->TMDB] final rename dry-run plan accepted',
    'bgm_to_tmdb_no_targetable_files': '[BGM->TMDB] no targetable files to migrate',
    'bgm_to_tmdb_transfer_failed': '[BGM->TMDB] transfer failed',
    'bgm_to_tmdb_product_pipeline_error': '[BGM->TMDB] product pipeline failed',
}

LOCAL_BANGUMI_CASE_AGENT_RESULT_STAGE = 'rename_local_bangumi_case_agent_result'
LOCAL_BANGUMI_CASE_AGENT_ERROR_STAGE = 'rename_local_bangumi_case_agent_error'


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ''


def _config_bool(key: str, *, default: bool = False) -> bool:
    value = cm.get_config(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {'1', 'true', 'yes', 'on'}


def _config_path_text(key: str) -> str:
    return str(cm.get_config(key) or '').strip()


def _local_bangumi_case_agent_primary_enabled() -> bool:
    return _config_bool('rename_local_bangumi_case_agent_primary_enabled', default=True)


def _bgm_to_tmdb_product_pipeline_enabled() -> bool:
    return _config_bool('rename_bgm_to_tmdb_product_pipeline_enabled', default=False)


def _bgm_to_tmdb_execute_enabled() -> bool:
    return _config_bool('rename_bgm_to_tmdb_execute_enabled', default=False)


def _case_agent_result_status(result: dict[str, object]) -> str:
    case_agent_result = result.get('case_agent_result') if isinstance(result.get('case_agent_result'), dict) else {}
    snapshot = case_agent_result.get('snapshot') if isinstance(case_agent_result.get('snapshot'), dict) else {}
    return str(
        result.get('product_result_kind')
        or case_agent_result.get('case_agent_status')
        or case_agent_result.get('status')
        or snapshot.get('case_agent_status')
        or snapshot.get('status')
        or ''
    ).strip()


def _write_local_bangumi_case_agent_result_snapshot(
    payload: dict[str, Any],
    *,
    source_path: Path,
) -> None:
    write_decision_snapshot(LOCAL_BANGUMI_CASE_AGENT_RESULT_STAGE, payload, source_path=source_path)


def _write_local_bangumi_case_agent_error_snapshot(
    payload: dict[str, Any],
    *,
    source_path: Path,
) -> None:
    write_decision_snapshot(LOCAL_BANGUMI_CASE_AGENT_ERROR_STAGE, payload, source_path=source_path)


def _run_local_bangumi_case_agent_primary(
    *,
    local_evidence: LocalEvidence,
    bangumi_contexts: list[dict[str, object]],
    ai_client: AIClient,
    source_path: Path,
) -> dict[str, Any]:
    try:
        if not _local_bangumi_case_agent_primary_enabled():
            payload = {
                'ok': False,
                'status': 'fail_closed',
                'summary': 'case_agent_primary_disabled',
                'case_agent': {
                    'status': 'fail_closed',
                    'summary': 'case_agent_primary_disabled',
                },
                'result': None,
                'mode': 'local_bangumi_case_agent_primary',
            }
            _write_local_bangumi_case_agent_result_snapshot(payload, source_path=source_path)
            return payload

        result = run_local_bangumi_case_agent_mapping(
            local_evidence=local_evidence,
            bangumi_contexts=bangumi_contexts,
            ai_client=ai_client,
            source_path=source_path,
        )
        payload = dict(result)
        payload.setdefault('mode', 'local_bangumi_case_agent_primary')
        _write_local_bangumi_case_agent_result_snapshot(payload, source_path=source_path)
        return payload
    except Exception as exc:
        logger.warning(
            'local-bangumi case agent primary failed',
            source_path=str(source_path),
            error=str(exc),
        )
        payload = {
            'ok': False,
            'status': 'invalid',
            'error': str(exc),
            'mode': 'local_bangumi_case_agent_primary',
        }
        _write_local_bangumi_case_agent_error_snapshot(payload, source_path=source_path)
        return payload


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
        self.BANGUMI_PATH: Path = Path(str(cm.get_config('bangumi_path') or ''))
        self.MOVIE_PATH: Path = Path(str(cm.get_config('movie_path') or ''))
        self.ANIME_PATH: Path = Path(str(cm.get_config('anime_path') or ''))
        self.ANIME_MOVIE_PATH: Path = Path(str(cm.get_config('anime_movie_path') or ''))

        for target in (
            self.BANGUMI_PATH,
            self.MOVIE_PATH,
            self.ANIME_PATH,
            self.ANIME_MOVIE_PATH,
        ):
            if str(target):
                target.mkdir(parents=True, exist_ok=True)

        self.mapping: dict[Path, Path] = {}

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
        path = Path(path)
        task_uuid = _tuuid or str(uuid.uuid4())

        if not path.exists():
            return self.error_reply(
                task_uuid,
                self._failure_message('invalid_path', str(path)),
                path,
                _is_anime,
                _is_movie,
                failure_reason='invalid_path',
                ai_attempted=False,
                ai_used=False,
            )

        if path.is_file() and path.suffix.lower() not in VIDEO_SUFFIX:
            logger.info('[process] skip non-video file', path=str(path))
            return True

        if path.is_dir() and self._count_local_videos(path) == 0:
            if _enqueue_task is not None and not _is_sub_task:
                children = [
                    child
                    for child in sorted(path.iterdir(), key=lambda item: item.name.casefold())
                    if child.is_dir() or child.suffix.lower() in VIDEO_SUFFIX
                ]
                for child in children:
                    _enqueue_task(
                        path=str(child),
                        is_anime=_is_anime,
                        is_movie=_is_movie,
                        cus_name=self._derive_subtask_custom_name(path, child, cus_name),
                        cus_season_id=cus_season_id,
                        _is_sub_task=True,
                    )
                return True

            return self.error_reply(
                task_uuid,
                self._failure_message('ai_empty_mapping'),
                path,
                _is_anime,
                _is_movie,
                failure_reason='ai_empty_mapping',
                ai_attempted=True,
                ai_used=False,
            )

        return self._process(
            path,
            _is_anime,
            _is_movie,
            task_uuid,
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
        task_uuid = _tuuid or str(uuid.uuid4())
        ai_client = AIClient()
        if not ai_client.is_available():
            return self.error_reply(
                task_uuid,
                self._failure_message('ai_unavailable'),
                path,
                _is_anime,
                _is_movie,
                name=cus_name,
                season_id=cus_season_id,
                failure_reason='ai_unavailable',
                ai_attempted=True,
                ai_used=False,
            )

        result = self._run_local_bangumi_case_agent_primary_for_path(
            path=path,
            task_uuid=task_uuid,
            ai_client=ai_client,
        )
        if result is None:
            return self.error_reply(
                task_uuid,
                self._failure_message('ai_empty_mapping'),
                path,
                _is_anime,
                _is_movie,
                name=cus_name,
                season_id=cus_season_id,
                failure_reason='ai_empty_mapping',
                ai_attempted=True,
                ai_used=True,
            )

        if _bgm_to_tmdb_product_pipeline_enabled() and _case_agent_result_status(result) == 'accepted':
            return self._run_bgm_to_tmdb_product_pipeline(
                path=path,
                task_uuid=task_uuid,
                local_bangumi_result=result,
                is_anime=_is_anime,
                is_movie=_is_movie,
                name=cus_name,
                season_id=cus_season_id,
            )

        extra_task_data: dict[str, object] = {
            'pipeline_mode': 'local_bangumi_case_agent_primary',
            'case_agent_result': result.get('case_agent_result'),
            'local_bangumi_mapping_only': bool(result.get('mapping_only')),
            'local_bangumi_product_result_kind': _as_str(result.get('product_result_kind')) or None,
        }
        return self.error_reply(
            task_uuid,
            self._failure_message(
                _as_str(result.get('reason')) or 'local_bangumi_case_agent_primary',
                _as_str(result.get('detail')) or None,
            ),
            path,
            _is_anime,
            _is_movie,
            name=cus_name,
            season_id=cus_season_id,
            failure_reason=_as_str(result.get('reason')) or 'local_bangumi_case_agent_primary',
            ai_attempted=True,
            ai_used=True,
            ai_confidence=None,
            extra_task_data=extra_task_data,
        )

    def _run_local_bangumi_case_agent_primary_for_path(
        self,
        *,
        path: Path,
        task_uuid: str,
        ai_client: AIClient,
        ordered_video_files: list[Path] | None = None,
        timings: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if not path.exists() or not ai_client.is_available():
            return None

        def _record_timing(stage: str, started_at: float) -> None:
            # Kept as a compatibility hook for callers that pass a timings dict.
            if timings is not None:
                timings[stage] = 0

        _record_timing('local_bangumi_case_agent_entry', 0)
        video_files = ordered_video_files or (
            self._collect_planning_video_files(path) if path.is_dir() else [path]
        )
        if not video_files:
            return {
                'ok': False,
                'reason': 'ai_empty_mapping',
                'detail': 'no processable video files found',
            }

        local_evidence = build_local_evidence(path, ordered_files=video_files)
        case_agent_result = _run_local_bangumi_case_agent_primary(
            local_evidence=local_evidence,
            bangumi_contexts=[],
            ai_client=ai_client,
            source_path=path,
        )
        case_agent_status = str(
            case_agent_result.get('case_agent_status')
            or case_agent_result.get('status')
            or ''
        ).strip()
        return {
            'ok': False,
            'reason': 'local_bangumi_case_agent_primary',
            'detail': (
                'Local->Bangumi Case Agent primary completed; '
                f'status={case_agent_status or "unknown"}; '
                'mapping-only phase stops before TMDB, final filename, move, and Emby'
            ),
            'case_agent_result': case_agent_result,
            'mapping_only': True,
            'product_result_kind': case_agent_status or 'unknown',
            'task_uuid': task_uuid,
        }

    def _run_bgm_to_tmdb_product_pipeline(
        self,
        *,
        path: Path,
        task_uuid: str,
        local_bangumi_result: dict[str, object],
        is_anime: bool | None,
        is_movie: bool | None,
        name: str | None,
        season_id: int | None,
    ) -> str | bool:
        try:
            compiled_plan = self._extract_compiled_plan(local_bangumi_result)
            if compiled_plan is None:
                return self.error_reply(
                    task_uuid,
                    self._failure_message('bgm_to_tmdb_product_pipeline_error', 'accepted Local->Bangumi result is missing compiled_plan'),
                    path,
                    is_anime,
                    is_movie,
                    name=name,
                    season_id=season_id,
                    failure_reason='bgm_to_tmdb_product_pipeline_error',
                    ai_attempted=True,
                    ai_used=True,
                    extra_task_data={
                        'pipeline_mode': 'local_bangumi_to_tmdb_product',
                        'case_agent_result': local_bangumi_result.get('case_agent_result'),
                    },
                )

            bridge_result = run_bgm_to_tmdb_bridge_agent(
                compiled_plan=compiled_plan,
                artifact_path='',
                source_path=path,
                sample_id=task_uuid,
            )
            bridge_input = compile_bgm_to_tmdb_input(compiled_plan, source_path=path)
            legal_graph = bridge_result.tmdb_legal_graph
            verified_plan = bridge_result.verified_plan
            bridge_extra = {
                'pipeline_mode': 'local_bangumi_to_tmdb_product',
                'case_agent_result': local_bangumi_result.get('case_agent_result'),
                'bgm_to_tmdb_bridge_status': bridge_result.status,
                'bgm_to_tmdb_bridge_summary': bridge_result.summary,
                'bgm_to_tmdb_bridge_run_dir': str(bridge_result.run_dir),
                'bgm_to_tmdb_bridge_errors': list(bridge_result.errors),
                'bgm_to_tmdb_bridge_tool_call_counts': dict(bridge_result.tool_call_counts),
            }
            if bridge_result.status != 'accepted' or not bridge_result.ok or verified_plan is None or legal_graph is None:
                return self.error_reply(
                    task_uuid,
                    self._failure_message('bgm_to_tmdb_bridge_failed', bridge_result.summary),
                    path,
                    is_anime,
                    is_movie,
                    name=name,
                    season_id=season_id,
                    failure_reason='bgm_to_tmdb_bridge_failed',
                    ai_attempted=True,
                    ai_used=True,
                    extra_task_data=bridge_extra,
                )

            roots = self._bgm_to_tmdb_rename_roots(is_anime=is_anime)
            # overwrite_existing 现为两态：'覆盖'/'跳过'（兼容旧 bool：True→覆盖，
            # False→跳过）。两种策略在 plan Verifier 阶段都不 block——具体差异
            # （覆盖=删旧重落 / 跳过=跳过已存在）交给 Trans.trans_file 层处理。
            # 旧实现漏读此 config，永远用默认 'block'，导致重复入队已落盘的包
            # 被 18 个 target_path_exists blocking 拦在 plan 阶段，用户配了覆盖
            # 也不生效。
            _ow = cm.get_config('overwrite_existing')
            existing_target_policy = 'ignore'  # 覆盖/跳过都不 block
            rename_plan, rename_verifier_result = compile_verified_bgm_to_tmdb_rename_plan(
                bridge_input=bridge_input,
                legal_graph=legal_graph,
                verified_plan=verified_plan,
                roots=roots,
                source_root=self._source_root_for_rename_plan(path),
                existing_target_policy=existing_target_policy,
            )
            write_bgm_to_tmdb_rename_plan_artifacts(
                output_dir=bridge_result.run_dir / 'artifacts',
                rename_plan=rename_plan,
                verifier_result=rename_verifier_result,
            )
            plan_extra = {
                **bridge_extra,
                'bgm_to_tmdb_verified_plan': verified_plan.model_dump(mode='json'),
                'bgm_to_tmdb_rename_plan': rename_plan.model_dump(mode='json'),
                'bgm_to_tmdb_rename_verifier_result': rename_verifier_result.model_dump(mode='json'),
                'bgm_to_tmdb_rename_plan_artifacts_dir': str(bridge_result.run_dir / 'artifacts'),
            }
            if not rename_verifier_result.passed:
                return self.error_reply(
                    task_uuid,
                    self._failure_message('bgm_to_tmdb_rename_plan_invalid', rename_verifier_result.summary),
                    path,
                    is_anime,
                    is_movie,
                    name=name,
                    season_id=season_id,
                    failure_reason='bgm_to_tmdb_rename_plan_invalid',
                    ai_attempted=True,
                    ai_used=True,
                    extra_task_data=plan_extra,
                )

            if not _bgm_to_tmdb_execute_enabled():
                return self.error_reply(
                    task_uuid,
                    self._failure_message('bgm_to_tmdb_rename_plan_dry_run', rename_plan.summary),
                    path,
                    is_anime,
                    is_movie,
                    name=name,
                    season_id=season_id,
                    failure_reason='bgm_to_tmdb_rename_plan_dry_run',
                    ai_attempted=True,
                    ai_used=True,
                    extra_task_data={
                        **plan_extra,
                        'pipeline_mode': 'local_bangumi_to_tmdb_product_dry_run',
                        'bgm_to_tmdb_execute_enabled': False,
                    },
                )

            transfer_mapping = self._transfer_mapping_from_rename_plan(rename_plan)
            if not transfer_mapping:
                return self.error_reply(
                    task_uuid,
                    self._failure_message('bgm_to_tmdb_no_targetable_files', rename_plan.summary),
                    path,
                    is_anime,
                    is_movie,
                    name=name,
                    season_id=season_id,
                    failure_reason='bgm_to_tmdb_no_targetable_files',
                    ai_attempted=True,
                    ai_used=True,
                    extra_task_data=plan_extra,
                )

            transfer_result = Trans(transfer_mapping, task_uuid).trans_file()
            if transfer_result is not True:
                return self.error_reply(
                    task_uuid,
                    self._failure_message('bgm_to_tmdb_transfer_failed', str(transfer_result)),
                    path,
                    is_anime,
                    is_movie,
                    name=name,
                    season_id=season_id,
                    failure_reason='bgm_to_tmdb_transfer_failed',
                    ai_attempted=True,
                    ai_used=True,
                    extra_task_data=plan_extra,
                )

            subtitle_mapping = self._collect_and_transfer_subtitle_sidecars(
                transfer_mapping=transfer_mapping,
                task_uuid=task_uuid,
            )

            success_data = self._success_task_data_from_rename_plan(
                task_uuid=task_uuid,
                source_path=path,
                is_anime=is_anime,
                rename_plan=rename_plan,
                verified_plan=verified_plan,
                legal_graph=legal_graph,
                extra_task_data={
                    **plan_extra,
                    'pipeline_mode': 'local_bangumi_to_tmdb_product',
                    'bgm_to_tmdb_execute_enabled': True,
                    'transferred_file_count': len(transfer_mapping) + len(subtitle_mapping),
                    'subtitle_mapping': {str(k): str(v) for k, v in subtitle_mapping.items()},
                    'subtitle_transfer_failed': False,
                },
            )
            self._write_task_data(success_data)
            return True
        except Exception as exc:
            logger.warning('BGM-to-TMDB product pipeline failed', source_path=str(path), error=str(exc))
            return self.error_reply(
                task_uuid,
                self._failure_message('bgm_to_tmdb_product_pipeline_error', f'{type(exc).__name__}: {exc}'),
                path,
                is_anime,
                is_movie,
                name=name,
                season_id=season_id,
                failure_reason='bgm_to_tmdb_product_pipeline_error',
                ai_attempted=True,
                ai_used=True,
                extra_task_data={'pipeline_mode': 'local_bangumi_to_tmdb_product'},
            )

    def _collect_and_transfer_subtitle_sidecars(
        self,
        *,
        transfer_mapping: dict[Path, Path],
        task_uuid: str,
    ) -> dict[Path, Path]:
        """收集并迁移同目录外部字幕，返回实际复制的字幕源→目标映射。"""
        subtitle_mapping: dict[Path, Path] = {}
        for source_path, target_path in transfer_mapping.items():
            if not source_path.is_file():
                continue
            video_stem = source_path.stem
            source_dir = source_path.parent
            for subtitle_path in source_dir.iterdir():
                if not subtitle_path.is_file():
                    continue
                if subtitle_path.suffix.lower() not in SUBTITLE_EXTENSIONS:
                    continue
                if not _subtitle_matches_video(video_stem, subtitle_path.stem):
                    continue
                target_dir = target_path.parent
                target_stem = target_path.stem
                emby_name = _build_emby_subtitle_name(subtitle_path, target_stem)
                target_subtitle_path = target_dir / emby_name
                # 去重：同目标路径只保留第一次出现
                if target_subtitle_path in subtitle_mapping.values():
                    continue
                subtitle_mapping[subtitle_path] = target_subtitle_path

        if not subtitle_mapping:
            return {}

        transfer_result = Trans(
            subtitle_mapping,
            task_uuid,
            force_mode='复制',
            force_overwrite=cm.get_config('overwrite_existing'),
            write_record=False,
        ).trans_file()
        if transfer_result is not True:
            logger.warning(
                '[BGM->TMDB] 字幕跟随迁移失败',
                task_uuid=task_uuid,
                error=str(transfer_result),
            )
            return {}
        return subtitle_mapping

    @staticmethod
    def _extract_compiled_plan(local_bangumi_result: dict[str, object]) -> CompiledOrganizePlan | None:
        case_agent_result = local_bangumi_result.get('case_agent_result')
        if not isinstance(case_agent_result, dict):
            return None
        snapshot = case_agent_result.get('snapshot')
        if not isinstance(snapshot, dict):
            return None
        compiled_plan = snapshot.get('compiled_plan')
        if not isinstance(compiled_plan, dict):
            return None
        return CompiledOrganizePlan.model_validate(compiled_plan)

    def _bgm_to_tmdb_rename_roots(self, *, is_anime: bool | None) -> TmdbRenamePlanRoots:
        bangumi_path = _config_path_text('bangumi_path')
        movie_path = _config_path_text('movie_path')
        anime_path = _config_path_text('anime_path')
        anime_movie_path = _config_path_text('anime_movie_path')
        if is_anime is False:
            tv_root = bangumi_path or anime_path
            movie_root = movie_path or anime_movie_path
        else:
            tv_root = anime_path or bangumi_path
            movie_root = anime_movie_path or movie_path
        return TmdbRenamePlanRoots(tv_root=tv_root, movie_root=movie_root)

    @staticmethod
    def _source_root_for_rename_plan(path: Path) -> Path:
        path = Path(path)
        return path.parent if path.is_file() else path

    @staticmethod
    def _transfer_mapping_from_rename_plan(rename_plan: TmdbRenamePlan) -> dict[Path, Path]:
        mapping: dict[Path, Path] = {}
        for item in rename_plan.items:
            if item.destination is None:
                continue
            if not item.source_abs_path or not item.destination.target_path:
                continue
            mapping[Path(item.source_abs_path)] = Path(item.destination.target_path)
        return mapping

    @staticmethod
    def _success_task_data_from_rename_plan(
        *,
        task_uuid: str,
        source_path: Path,
        is_anime: bool | None,
        rename_plan: TmdbRenamePlan,
        verified_plan: VerifiedBgmToTmdbPlan,
        legal_graph: TmdbLegalGraph,
        extra_task_data: dict[str, object],
    ) -> dict[str, object]:
        target_items = [item for item in rename_plan.items if item.destination is not None]
        first_destination = target_items[0].destination if target_items else None
        target_roots = sorted({
            str(Path(item.destination.root_path) / item.destination.work_folder)
            for item in target_items
            if item.destination is not None and item.destination.root_path and item.destination.work_folder
        })
        tmdb_refs = sorted({
            item.destination.tmdb_ref
            for item in target_items
            if item.destination is not None and item.destination.tmdb_ref
        })
        is_movie = bool(first_destination and first_destination.media_type == 'movie')
        target_root = target_roots[0] if len(target_roots) == 1 else ''
        # Bangumi subject 名（auto_fetch 字幕搜索词来源，方向 A）。
        # 从 rename_plan.items 的 bangumi_assignment.target.bangumi_subject_id 收集，
        # 调 BangumiClient.get_subject 拿 name/name_cn。失败/缺失不阻塞落盘（辅助字段）。
        bgm_subject_info = Rename._collect_bgm_subject_names(rename_plan)
        # 解析 TMDB 海报路径写进 task_data，供 Telegram 通知 send_photo 用。
        # 旧实现 _resolve_task_poster_path 定义了但从未调用，poster_path 永远硬编码
        # None，导致 TG 通知永远走 send_message 纯文字、无海报。按 first_destination
        # 的 tmdb_id/media_type/season 查 TMDB 详情（带缓存），TV 取季海报无则回退
        # series 海报。失败/缺失不阻塞落盘（辅助字段，poster_path 仍可为 None）。
        task_poster_path: str | None = None
        if first_destination is not None and first_destination.tmdb_id:
            try:
                _tmdb_search = Search()
                _tmdb_info = None
                if first_destination.media_type == 'movie':
                    _tmdb_info = _tmdb_search.get_movie_info_by_id(first_destination.tmdb_id)
                else:
                    _tmdb_info = _tmdb_search.get_tv_info_by_id(first_destination.tmdb_id)
                task_poster_path = Rename._resolve_task_poster_path(
                    _tmdb_info,
                    is_movie=bool(first_destination.media_type == 'movie'),
                    season_id=(None if first_destination.season_number is None else int(first_destination.season_number)),
                )
            except Exception as exc:
                logger.warning(f'[BGM->TMDB] 解析 TMDB 海报失败（不阻塞）: {exc!r}')
        task_data: dict[str, object] = {
            'path': str(source_path),
            'is_anime': is_anime,
            'is_movie': is_movie,
            'name': first_destination.title if first_destination is not None else None,
            'season_id': None if first_destination is None else (0 if is_movie else first_destination.season_number),
            'uuid': str(task_uuid),
            'error': '',
            'use_ai': True,
            'ai_attempted': True,
            'ai_used': True,
            'ai_confidence': None,
            'failure_reason': None,
            'pipeline_mode': 'local_bangumi_to_tmdb_product',
            'tmdb_id': first_destination.tmdb_id if first_destination is not None else None,
            'poster_path': task_poster_path,
            'tmdb_name': first_destination.title if first_destination is not None else None,
            'tmdb_year': first_destination.year if first_destination is not None else None,
            'tmdb_media_type': first_destination.media_type if first_destination is not None else None,
            'tmdb_genres': [],
            'release_group': None,
            'resource_term': None,
            'target_root': target_root,
            'target_roots': target_roots,
            'is_mixed_parent': len(target_roots) > 1 or len(tmdb_refs) > 1,
            'bgm_to_tmdb_tmdb_refs': tmdb_refs,
            'bgm_to_tmdb_target_item_count': rename_plan.target_item_count,
            'bgm_to_tmdb_target_count': verified_plan.tmdb_target_count,
            'bgm_to_tmdb_absent_count': verified_plan.tmdb_absent_count,
            'bgm_to_tmdb_supplemental_count': verified_plan.supplemental_count,
            'bgm_to_tmdb_candidate_count': len(legal_graph.candidates),
            # Bangumi subject 名（auto_fetch 字幕搜索词，方向 A）
            'bgm_subject_name': bgm_subject_info['name'],
            'bgm_subject_name_cn': bgm_subject_info['name_cn'],
            'bgm_subject_ids': bgm_subject_info['subject_ids'],
            # 多季覆盖：每 subject 的 name/name_cn（Pi 多变体搜）+
            # per-video→subject 映射（auto_fetch 给每 missing video card 填 subject）。
            # 旧 task_data 无此字段时 auto_fetch 走旧路径（主体单值）。
            'bgm_subjects': bgm_subject_info['subjects'],
            'bgm_video_subject_map': bgm_subject_info['video_subject_map'],
        }
        task_data.update(extra_task_data)
        return task_data

    @staticmethod
    def _collect_bgm_subject_names(
        rename_plan: TmdbRenamePlan,
    ) -> dict[str, object]:
        """从 rename_plan 收集 Bangumi subject 信息供 auto_fetch 多季覆盖。

        多 subject 合集（如 0091 鬼灭 S01+S02+S03+剧场版 = 4 个 BGM subject）
        需把每个 subject 的 name/name_cn + per-video→subject 映射都写进 task_data，
        auto_fetch 据此对每季独立搜字幕帖（Pi 一次选多帖多包）。

        返回：
        - ``name``/``name_cn``：主体 subject（assignment 数最多）的名，向后兼容
          旧 auto_fetch 单值字段。
        - ``subject_ids``：全部 subject id 列表（向后兼容）。
        - ``subjects``：每 subject {id, name, name_cn, media_kind, assignment_count}，
          auto_fetch 多季覆盖的搜索词来源（name=日文原名命中干净，name_cn=中文
          命中全含噪音，Pi 多变体搜）。
        - ``video_subject_map``：{video_basename: subject_id}，仅 map_to_tmdb 的 item，
          auto_fetch 据此给每 missing video card 填 per-video subject。

        Bangumi 查询失败不阻塞落盘（该 subject name 空，auto_fetch 回退源目录标题）。
        """
        subject_counts: dict[int, int] = {}
        subject_media_kind: dict[int, str] = {}
        # video_basename -> subject_id（仅 map_to_tmdb，supplemental 不进 missing）
        video_subject_map: dict[str, int] = {}
        for item in rename_plan.items:
            if item.disposition != 'map_to_tmdb':
                continue
            assignment = item.bangumi_assignment
            if assignment is None:
                continue
            subject_id = int(getattr(assignment.target, 'bangumi_subject_id', 0) or 0)
            if subject_id <= 0:
                continue
            subject_counts[subject_id] = subject_counts.get(subject_id, 0) + 1
            media_kind = str(getattr(assignment.target, 'media_kind', '') or '')
            if media_kind:
                subject_media_kind.setdefault(subject_id, media_kind)
            # 建 video -> subject 映射（用最终落地 video 文件名）
            target_path = str(item.target_path or '')
            if target_path:
                video_basename = Path(target_path).name
                if video_basename:
                    video_subject_map[video_basename] = subject_id
        if not subject_counts:
            return {
                'name': '', 'name_cn': '', 'subject_ids': [],
                'subjects': [], 'video_subject_map': {},
            }
        # 主体 subject：assignment 数最多，并列取 id 最小
        main_subject_id = min(
            subject_counts, key=lambda sid: (-subject_counts[sid], sid)
        )
        subject_ids = sorted(subject_counts.keys())
        # 对每个 subject 查 name/name_cn（不只主体），失败不阻塞
        subjects: list[dict[str, object]] = []
        name = ''
        name_cn = ''
        try:
            from ..bangumi.client import BangumiClient
            client = BangumiClient()
            for sid in subject_ids:
                s_name = ''
                s_name_cn = ''
                try:
                    subject = client.get_subject(sid)
                    if subject is not None:
                        s_name = str(subject.name or '')
                        s_name_cn = str(subject.name_cn or '')
                except Exception as exc:
                    logger.warning(
                        '收集 Bangumi subject 名失败，该 subject name 为空',
                        subject_id=sid,
                        error=str(exc),
                    )
                subjects.append({
                    'id': sid,
                    'name': s_name,
                    'name_cn': s_name_cn,
                    'media_kind': subject_media_kind.get(sid, ''),
                    'assignment_count': subject_counts[sid],
                })
                if sid == main_subject_id:
                    name = s_name
                    name_cn = s_name_cn
        except Exception as exc:
            logger.warning(
                'BangumiClient 初始化失败，auto_fetch 将回退源目录标题',
                error=str(exc),
            )
        return {
            'name': name,
            'name_cn': name_cn,
            'subject_ids': subject_ids,
            'subjects': subjects,
            'video_subject_map': video_subject_map,
        }

    @staticmethod
    def _normalize_structural_dir_name(name: str) -> str:
        normalized = unicodedata.normalize('NFKD', name or '')
        normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.casefold()
        normalized = re.sub(r'[^a-z0-9]+', ' ', normalized)
        return re.sub(r'\s+', ' ', normalized).strip()

    def _is_structural_subdir(self, path: Path) -> bool:
        normalized = self._normalize_structural_dir_name(path.name)
        if not normalized:
            return False
        if normalized in self.STRUCTURAL_DIR_TOKENS:
            return True
        return bool(re.fullmatch(r'(season|disc|vol|volume)\s*\d{1,2}', normalized))

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
        return sum(
            1
            for child in path.rglob('*')
            if child.is_file() and child.suffix.lower() in VIDEO_SUFFIX
        )

    def _collect_planning_video_files(self, path: Path) -> list[Path]:
        if path.is_file():
            return [path] if path.suffix.lower() in VIDEO_SUFFIX else []
        return sorted(
            [
                child
                for child in path.rglob('*')
                if child.is_file() and child.suffix.lower() in VIDEO_SUFFIX
            ],
            key=lambda child: child.relative_to(path).as_posix().casefold(),
        )

    @staticmethod
    def _resolve_task_poster_path(
        info: TmdbInfo | None,
        is_movie: bool,
        season_id: int | None,
    ) -> str | None:
        if not isinstance(info, dict):
            return None

        series_poster = _as_str(info.get('poster_path'))
        if is_movie:
            return series_poster
        if not isinstance(season_id, int):
            return series_poster

        seasons = info.get('seasons')
        if not isinstance(seasons, list):
            return series_poster

        for season in seasons:
            if not isinstance(season, dict):
                continue
            if season.get('season_number') != season_id:
                continue
            season_poster = season.get('poster_path')
            if isinstance(season_poster, str) and season_poster.strip():
                return season_poster

        return series_poster

    def _failure_message(self, reason: str, detail: str | None = None) -> str:
        base = FAILURE_MESSAGES.get(reason, FAILURE_MESSAGES['ai_unavailable'])
        return f'{base}: {detail}' if detail else base

    def _write_task_data(self, task_data: dict[str, object]) -> None:
        TASK_PATH.mkdir(parents=True, exist_ok=True)
        task_path = TASK_PATH / f"{task_data['uuid']}.json"
        task_path.write_text(
            json.dumps(task_data, indent=4, ensure_ascii=False, default=str),
            encoding='utf-8',
        )

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
            'pipeline_mode': 'local_bangumi_case_agent_primary',
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


def _build_emby_subtitle_name(subtitle_path: Path, video_stem: str) -> str:
    """按 Emby 标准构造字幕文件名：video_stem.{lang}[.default]{ext}。"""
    lang = _detect_subtitle_language(subtitle_path)
    emby_lang, is_simplified = _normalize_subtitle_language(lang)
    if is_simplified:
        return f'{video_stem}.{emby_lang}.default{subtitle_path.suffix.lower()}'
    return f'{video_stem}.{emby_lang}{subtitle_path.suffix.lower()}'


def _detect_subtitle_language(subtitle_path: Path) -> str | None:
    """从字幕文件名中提取字幕组语言标签（如 chs）。"""
    stem = subtitle_path.stem
    # 去掉 video_stem 前缀，保留剩余片段中的语言标记
    tokens = [item.casefold() for item in re.split(r'[.\s_\-\[\]()]+', stem) if item]
    # 简体优先命中，避免 zh 等短标签被优先匹配
    for token in sorted(tokens, key=len, reverse=True):
        if token in _SUBTITLE_LANGUAGE_MAP:
            return token
    return None


def _normalize_subtitle_language(lang: str | None) -> tuple[str, bool]:
    """将字幕组语言标签转换为 Emby 标准代码，并返回是否为简体中文。"""
    if not lang:
        return ('zh-CN', True)
    lang_lower = lang.lower().strip()
    if lang_lower in _SUBTITLE_LANGUAGE_MAP:
        return _SUBTITLE_LANGUAGE_MAP[lang_lower]
    return (lang, False)
