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
from .trans import Trans
from .utils import VIDEO_SUFFIX


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
            rename_plan, rename_verifier_result = compile_verified_bgm_to_tmdb_rename_plan(
                bridge_input=bridge_input,
                legal_graph=legal_graph,
                verified_plan=verified_plan,
                roots=roots,
                source_root=self._source_root_for_rename_plan(path),
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
                    'transferred_file_count': len(transfer_mapping),
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
            'poster_path': None,
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
        }
        task_data.update(extra_task_data)
        return task_data

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

    def _resolve_task_poster_path(
        self,
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
