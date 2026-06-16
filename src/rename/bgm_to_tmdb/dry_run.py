from __future__ import annotations

from pathlib import Path
from typing import Any

from ..decision_snapshot import write_decision_snapshot
from .compiler import build_tmdb_legal_graph, compile_bgm_to_tmdb_input
from .models import BgmToTmdbMappingDraft, TmdbLegalGraph
from .verifier import verify_and_compile_bgm_to_tmdb_plan


BGM_TO_TMDB_BRIDGE_RESULT_STAGE = 'rename_bgm_to_tmdb_bridge_result'


def run_bgm_to_tmdb_bridge_dry_run(
    *,
    compiled_plan: Any,
    legal_graph: TmdbLegalGraph | list[dict[str, Any]],
    bridge_draft: BgmToTmdbMappingDraft | dict[str, Any],
    source_path: str | Path = '',
    write_snapshot: bool = True,
) -> dict[str, Any]:
    bridge_input = compile_bgm_to_tmdb_input(compiled_plan, source_path=source_path)
    graph = legal_graph if isinstance(legal_graph, TmdbLegalGraph) else build_tmdb_legal_graph(legal_graph)
    draft = bridge_draft if isinstance(bridge_draft, BgmToTmdbMappingDraft) else BgmToTmdbMappingDraft.model_validate(bridge_draft)
    verified_plan, verifier_result = verify_and_compile_bgm_to_tmdb_plan(bridge_input, graph, draft)
    payload: dict[str, Any] = {
        'ok': bool(verifier_result.passed),
        'status': 'accepted' if verifier_result.passed else 'invalid',
        'dry_run': True,
        'file_mutation_allowed': False,
        'mode': 'bgm_to_tmdb_bridge_dry_run',
        'bridge_input': bridge_input.model_dump(mode='json'),
        'tmdb_legal_graph': graph.model_dump(mode='json'),
        'bridge_draft': draft.model_dump(mode='json'),
        'verifier_result': verifier_result.model_dump(mode='json'),
        'verified_plan': verified_plan.model_dump(mode='json') if verified_plan is not None else None,
    }
    if write_snapshot:
        write_decision_snapshot(BGM_TO_TMDB_BRIDGE_RESULT_STAGE, payload, source_path=source_path)
    return payload
