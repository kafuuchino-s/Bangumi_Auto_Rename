from __future__ import annotations

from pathlib import Path
from typing import Iterable, Any

from .models import (
    BgmAssignmentRef,
    BgmTargetRef,
    BgmTargetSpanRef,
    BgmToTmdbInput,
    TmdbCandidateCard,
    TmdbLegalGraph,
    normalize_source_path,
)


def compile_bgm_to_tmdb_input(compiled_plan: Any, source_path: str | Path = '') -> BgmToTmdbInput:
    assignments: list[BgmAssignmentRef] = []
    for raw_assignment in getattr(compiled_plan, 'assignments', []) or []:
        target = getattr(raw_assignment, 'target', None)
        target_span = getattr(raw_assignment, 'target_span', None)
        assignments.append(
            BgmAssignmentRef(
                source_path=normalize_source_path(getattr(raw_assignment, 'source_path', '')),
                disposition=getattr(raw_assignment, 'disposition', 'map_to_bangumi'),
                rule_name=str(getattr(raw_assignment, 'rule_name', '') or ''),
                target=BgmTargetRef.model_validate(_model_payload(target)),
                target_span=BgmTargetSpanRef.model_validate(_model_payload(target_span)),
                extracted_episode_number=getattr(raw_assignment, 'extracted_episode_number', None),
                reason=str(getattr(raw_assignment, 'reason', '') or ''),
            )
        )
    return BgmToTmdbInput(
        source_path=normalize_source_path(source_path),
        assignments=assignments,
    )


def build_tmdb_legal_graph(
    candidates: Iterable[TmdbCandidateCard | dict[str, Any]],
    *,
    generated_by: str = '',
) -> TmdbLegalGraph:
    return TmdbLegalGraph(
        candidates=[
            candidate if isinstance(candidate, TmdbCandidateCard) else TmdbCandidateCard.model_validate(candidate)
            for candidate in candidates
        ],
        generated_by=generated_by,
    )


def _model_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return value
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith('_') and not callable(getattr(value, key))
    }
