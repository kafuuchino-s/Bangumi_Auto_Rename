from __future__ import annotations

import json
import time
import unicodedata
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, cast

from ..case_agent.models import CaseVerifierResult
from ..get_info import Search
from .compiler import build_tmdb_legal_graph
from .external_hints import (
    ExternalMappingIndex,
    load_configured_external_mapping_index,
)
from .graph_builder import (
    build_tmdb_movie_candidate_card,
    build_tmdb_tv_candidate_card,
)
from .models import (
    BgmToTmdbInput,
    BgmToTmdbMappingDraft,
    BgmToTmdbRecipeParams,
    TmdbCandidateCard,
    TmdbLegalGraph,
    VerifiedBgmToTmdbPlan,
    normalize_source_path,
)
from .recipe import (
    compile_and_verify_bgm_to_tmdb_recipe_params,
    declared_tmdb_refs,
)
from .verifier import verify_and_compile_bgm_to_tmdb_plan, verify_bgm_to_tmdb_draft


@dataclass
class BgmToTmdbBridgeToolState:
    bridge_input: BgmToTmdbInput
    legal_graph: TmdbLegalGraph
    run_dir: Path
    artifact_path: str = ''
    sample_id: str = ''
    tmdb_search: Any = None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    bridge_draft: BgmToTmdbMappingDraft | None = None
    recipe_params: BgmToTmdbRecipeParams | None = None
    bridge_verifier_result: CaseVerifierResult | None = None
    verified_plan: VerifiedBgmToTmdbPlan | None = None
    recipe_review_warnings: list[dict[str, Any]] = field(default_factory=list)
    final_result: dict[str, Any] | None = None
    last_invalid_submission: dict[str, Any] | None = None
    submit_rejection_count: int = 0
    search_call_count: int = 0
    search_guidance_soft_limit: int = 8
    external_hints_mode: str = ''
    external_mapping_index: ExternalMappingIndex | None = None
    external_hint_hydrated_refs: set[str] = field(default_factory=set)
    external_hint_prefetch_enabled: bool = True
    external_hint_prefetch_errors: list[str] = field(default_factory=list)
    external_hint_prefetch_omitted_refs: list[str] = field(default_factory=list)
    external_hint_search_shortcut_count: int = 0

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / 'artifacts').mkdir(parents=True, exist_ok=True)
        if self.tmdb_search is None:
            self.tmdb_search = Search()
        if self.external_mapping_index is None:
            self.external_hints_mode, self.external_mapping_index = load_configured_external_mapping_index()
        else:
            self.external_hints_mode = str(self.external_hints_mode or 'off').strip().casefold()
            if self.external_hints_mode not in {'off', 'shadow', 'assist'}:
                self.external_hints_mode = 'off'
        self._write_external_mapping_audit()
        if self.external_hints_mode == 'assist' and self.external_hint_prefetch_enabled:
            self._prefetch_external_hint_graph()
            self._write_external_mapping_audit()

    def _write_external_mapping_audit(self) -> None:
        assert self.external_mapping_index is not None
        (self.run_dir / 'artifacts' / 'external_mapping_hint_audit.json').write_text(
            json.dumps(
                {
                    'mode': self.external_hints_mode,
                    'agent_visible': self.external_hints_mode == 'assist',
                    'index': self.external_mapping_index.audit_payload(),
                    'runtime': {
                        'prefetch_enabled': bool(self.external_hint_prefetch_enabled),
                        'prefetched_refs': sorted(self.external_hint_hydrated_refs),
                        'prefetch_errors': list(self.external_hint_prefetch_errors),
                        'prefetch_omitted_refs': list(self.external_hint_prefetch_omitted_refs),
                        'search_shortcut_count': self.external_hint_search_shortcut_count,
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding='utf-8',
        )


    def case_input(self) -> dict[str, Any]:
        artifacts_dir = self.run_dir / 'artifacts'
        return {
            'case_agent_mode': 'bgm_to_tmdb_bridge_dry_run',
            'accepted_artifact_path': self.artifact_path,
            'sample_id': self.sample_id,
            'runtime_policy': {
                'dry_run_only': True,
                'file_mutation_allowed': False,
            },
            'scratch_paths': {
                'artifacts_dir': str(artifacts_dir),
                'bridge_draft': str(artifacts_dir / 'bgm_to_tmdb_bridge_draft.json'),
                'bridge_verifier_result': str(artifacts_dir / 'bgm_to_tmdb_bridge_verifier_result.json'),
            },
            'case_goal': {
                'objective': 'Produce verifier-accepted BGM-to-TMDB recipe params or fail closed for global ambiguity.',
                'done_when': [
                    'validate_bgm_to_tmdb_bridge_recipe_params returns accepted=true',
                    'submit_bgm_to_tmdb_bridge_recipe_params returns accepted=true',
                    'each accepted BGM assignment has exactly one bridge outcome: map_to_tmdb, tmdb_target_absent, or unmapped_supplemental',
                    'no file move/copy/link/rename operation is performed',
                ],
            },
            'context': self._context_payload(detail=True),
        }

    def handle_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.time()
        args = dict(arguments or {})
        trace_row = {
            'index': len(self.tool_trace) + 1,
            'tool': str(name or ''),
            'arguments': _json_safe(args),
            'started_at': started,
        }
        try:
            handler = getattr(self, f'tool_{name}', None)
            if handler is None:
                result = {'ok': False, 'accepted': False, 'error': f'unknown tool: {name}'}
            else:
                result = handler(**args)
        except Exception as exc:
            result = {'ok': False, 'accepted': False, 'error': f'{type(exc).__name__}: {exc}'}
        trace_row['elapsed_ms'] = int((time.time() - started) * 1000)
        trace_row['ok'] = bool(result.get('ok')) if isinstance(result, dict) else False
        trace_row['result_summary'] = self._compact_result_summary(result)
        self.tool_trace.append(trace_row)
        with (self.run_dir / 'tool_trace.jsonl').open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(_json_safe(trace_row), ensure_ascii=False, sort_keys=True))
            fh.write('\n')
        return _json_safe(result)

    def tool_get_bgm_to_tmdb_bridge_context(self, detail: bool = False) -> dict[str, Any]:
        return {'ok': True, 'data': self._context_payload(detail=bool(detail))}

    def tool_search_tmdb_candidates(
        self,
        query: str,
        media_type: str = 'multi',
        year: int = 0,
        max_candidates: int = 6,
    ) -> dict[str, Any]:
        query = str(query or '').strip()
        if not query:
            return {'ok': False, 'accepted': False, 'error': 'query is required'}
        self.search_call_count += 1
        search_guidance = self._search_guidance_payload()
        media_type = str(media_type or 'multi').strip().casefold()
        limit = max(1, min(20, int(max_candidates or 6)))
        year_value = int(year or 0) or None
        shortcut = self._prefetched_anchor_candidates(media_type=media_type, query=query, limit=limit)
        if shortcut is not None:
            self.external_hint_search_shortcut_count += 1
            return {
                'ok': True,
                'query': query,
                'media_type': media_type,
                'candidate_count': len(shortcut),
                'candidates': [candidate.model_dump(mode='json') for candidate in shortcut],
                'search_source': 'external_hint_prefetch',
                'network_request_skipped': True,
                'search_guidance': search_guidance,
                'search_strategy_hints': [
                    'These candidates were pre-hydrated from read-only external mapping hints; compare them with the accepted BGM plan before drafting.',
                    'If none fits the BGM frontier, run another title search to widen the candidate set.',
                    *self._target_shape_search_hints(media_type),
                ],
            }
        if media_type not in {'multi', 'tv', 'movie'}:
            return {'ok': False, 'accepted': False, 'error': 'media_type must be multi, tv, or movie'}
        if media_type == 'tv':
            raw_candidates = self.tmdb_search.search_tv_by_query(query, year=year_value, limit=limit) or []
            media_hint = 'tv'
        elif media_type == 'movie':
            raw_candidates = self.tmdb_search.search_movies_by_title(query, year=year_value, limit=limit) or []
            media_hint = 'movie'
        elif media_type == 'multi':
            raw_candidates = self.tmdb_search.search_multi_by_query(query, limit=limit) or []
            media_hint = ''
        else:
            return {'ok': False, 'accepted': False, 'error': 'media_type must be multi, tv, or movie'}
        candidates = [
            _candidate_search_payload(candidate, media_hint=media_hint)
            for candidate in raw_candidates[:limit]
            if isinstance(candidate, dict)
        ]
        result = {
            'ok': True,
            'query': query,
            'media_type': media_type,
            'candidate_count': len(candidates),
            'candidates': candidates,
            'search_guidance': search_guidance,
            'search_strategy_hints': [
                'For multi-season franchise packages, once this search returns a plausible series candidate, prefer legal-graph hydration as the next evidence layer before deciding whether additional season/OVA/OAD title searches are useful.',
                'Use hydrated season cards, season 0 cards, and episode titles to decide whether additional title searches are actually needed.',
                *self._target_shape_search_hints(media_type),
            ],
        }
        if self.search_call_count >= self.search_guidance_soft_limit:
            result['search_guidance_warning'] = 'Search count is getting high; prefer anchor-first hydration and recipe validation over more title variant searches.'
            result['repair_hints'] = [
                'For multi-season franchise cases, use one plausible series anchor and its hydrated season/episode cards before spending more searches on each season title.',
                'Prefer validate_bgm_to_tmdb_bridge_recipe_params over more title variant searches.',
                'For recap/summary/CM/package extras, use tmdb_absent_group when BGM has a mapped node but TMDB exposes no legal node after targeted checks.',
            ]
        return result

    def _prefetched_anchor_candidates(
        self,
        *,
        media_type: str,
        query: str,
        limit: int,
    ) -> list[TmdbCandidateCard] | None:
        if self.external_hints_mode != 'assist' or self.search_call_count != 1:
            return None
        if not self.external_hint_hydrated_refs:
            return None
        candidates = [
            candidate
            for candidate in self.legal_graph.candidates
            if candidate.tmdb_ref in self.external_hint_hydrated_refs
            and (media_type == 'multi' or candidate.media_type == media_type)
        ]
        if not candidates:
            return None
        del query
        return candidates[: max(1, min(20, int(limit or 6)))]

    def tool_get_tmdb_legal_graph(self, tmdb_refs: list[str] | None = None) -> dict[str, Any]:
        refs = _dedupe_nonempty([str(ref or '').strip() for ref in (tmdb_refs or [])])
        if not refs:
            return {
                'ok': True,
                'accepted': False,
                'candidate_count': 0,
                'errors': [],
                'tmdb_legal_graph': self.legal_graph.model_dump(mode='json'),
            }
        return self._hydrate_tmdb_refs(refs)

    def tool_validate_bgm_to_tmdb_bridge_recipe_params(self, recipe_params: dict[str, Any] | None = None) -> dict[str, Any]:
        params, error = self._parse_recipe_params_payload(recipe_params)
        if error:
            return {'ok': False, 'accepted': False, 'status': 'invalid', 'error': error, 'repair_hints': ['Submit a JSON object shaped like BgmToTmdbRecipeParams.']}
        assert params is not None
        hydrate_result = self._hydrate_tmdb_refs(declared_tmdb_refs(params))
        compile_result = compile_and_verify_bgm_to_tmdb_recipe_params(self.bridge_input, self.legal_graph, params)
        self.recipe_params = params
        self.bridge_draft = compile_result.bridge_draft
        self.bridge_verifier_result = compile_result.verifier_result
        self.recipe_review_warnings = compile_result.review_warnings
        repair_hints = _bridge_repair_hints(compile_result.verifier_result)
        review_hints = _review_warning_hints(compile_result.review_warnings)
        self._write_bridge_artifacts(
            compile_result.bridge_draft,
            compile_result.verifier_result,
            recipe_params=params,
            review_warnings=compile_result.review_warnings,
            rule_match_counts=compile_result.rule_match_counts,
        )
        accepted = bool(compile_result.accepted)
        status = 'accepted' if accepted else ('review' if compile_result.verifier_result.passed else 'invalid')
        summary = (
            compile_result.verifier_result.summary
            if status != 'review'
            else f'accepted mechanically, but {len(compile_result.review_warnings)} review warning(s) need targeted evidence'
        )
        return {
            'ok': True,
            'accepted': accepted,
            'status': status,
            'summary': summary,
            'review_warnings': compile_result.review_warnings,
            'repair_hints': _dedupe_nonempty([*repair_hints, *review_hints]),
            'tmdb_hydration': hydrate_result,
            'rule_match_counts': compile_result.rule_match_counts,
            'verifier_result': compile_result.verifier_result.model_dump(mode='json'),
            'bridge_draft': compile_result.bridge_draft.model_dump(mode='json'),
            'recipe_params': params.model_dump(mode='json'),
        }

    def tool_submit_bgm_to_tmdb_bridge_recipe_params(self, recipe_params: dict[str, Any] | None = None, summary: str = '') -> dict[str, Any]:
        params, error = self._parse_recipe_params_payload(recipe_params)
        if error:
            return {'ok': False, 'accepted': False, 'status': 'invalid', 'error': error, 'repair_hints': ['Submit a JSON object shaped like BgmToTmdbRecipeParams.']}
        assert params is not None
        hydrate_result = self._hydrate_tmdb_refs(declared_tmdb_refs(params))
        compile_result = compile_and_verify_bgm_to_tmdb_recipe_params(self.bridge_input, self.legal_graph, params)
        verified_plan, final_verifier_result = (None, compile_result.verifier_result)
        if compile_result.verifier_result.passed and not compile_result.review_warnings:
            verified_plan, final_verifier_result = verify_and_compile_bgm_to_tmdb_plan(
                self.bridge_input,
                self.legal_graph,
                compile_result.bridge_draft,
            )
        self.recipe_params = params
        self.bridge_draft = compile_result.bridge_draft
        self.bridge_verifier_result = final_verifier_result
        self.verified_plan = verified_plan
        self.recipe_review_warnings = compile_result.review_warnings
        repair_hints = _bridge_repair_hints(final_verifier_result)
        review_hints = _review_warning_hints(compile_result.review_warnings)
        self._write_bridge_artifacts(
            compile_result.bridge_draft,
            final_verifier_result,
            verified_plan=verified_plan,
            recipe_params=params,
            review_warnings=compile_result.review_warnings,
            rule_match_counts=compile_result.rule_match_counts,
        )
        if not final_verifier_result.passed or compile_result.review_warnings or verified_plan is None:
            self.submit_rejection_count += 1
            self.last_invalid_submission = {
                'recipe_params': params.model_dump(mode='json'),
                'bridge_draft': compile_result.bridge_draft.model_dump(mode='json'),
                'verifier_result': final_verifier_result.model_dump(mode='json'),
                'review_warnings': compile_result.review_warnings,
                'repair_hints': _dedupe_nonempty([*repair_hints, *review_hints]),
                'tmdb_hydration': hydrate_result,
            }
            return {
                'ok': True,
                'accepted': False,
                'status': 'review' if final_verifier_result.passed else 'invalid',
                'summary': 'Review warnings need targeted evidence; revise and submit again.' if final_verifier_result.passed else 'Verifier rejected the BGM-to-TMDB recipe params; revise and submit again.',
                'review_warnings': compile_result.review_warnings,
                'repair_hints': _dedupe_nonempty([*repair_hints, *review_hints]),
                'tmdb_hydration': hydrate_result,
                'rule_match_counts': compile_result.rule_match_counts,
                'verifier_result': final_verifier_result.model_dump(mode='json'),
                'bridge_draft': compile_result.bridge_draft.model_dump(mode='json'),
            }
        self.final_result = {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': str(summary or verified_plan.summary or 'Pi submitted verifier-accepted BGM-to-TMDB recipe params.'),
            'final_action': 'submit_bgm_to_tmdb_bridge_recipe_params',
            'recipe_params': params.model_dump(mode='json'),
            'bridge_draft': compile_result.bridge_draft.model_dump(mode='json'),
            'tmdb_legal_graph': self.legal_graph.model_dump(mode='json'),
            'verified_plan': verified_plan.model_dump(mode='json'),
            'final_verifier_result': final_verifier_result.model_dump(mode='json'),
            'review_warnings': compile_result.review_warnings,
            'rule_match_counts': compile_result.rule_match_counts,
        }
        self._write_final_result()
        return {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': self.final_result['summary'],
            'review_warnings': [],
            'repair_hints': [],
            'tmdb_hydration': hydrate_result,
            'rule_match_counts': compile_result.rule_match_counts,
            'verifier_result': final_verifier_result.model_dump(mode='json'),
            'verified_plan': verified_plan.model_dump(mode='json'),
        }

    def _prefetch_external_hint_graph(self) -> None:
        if self.external_mapping_index is None:
            return
        subject_ids = {
            int(assignment.target_span.bangumi_subject_id or assignment.target.bangumi_subject_id or 0)
            for assignment in self.bridge_input.assignments
            if assignment.is_mapped_bangumi
        }
        refs = sorted({
            hint.tmdb_ref
            for subject_id in subject_ids
            for hint in self.external_mapping_index.hints_for_subject(subject_id)
        })
        max_refs = 16
        if len(refs) > max_refs:
            self.external_hint_prefetch_omitted_refs = refs[max_refs:]
            refs = refs[:max_refs]
        if not refs:
            return
        try:
            result = self._hydrate_tmdb_refs(refs)
        except Exception as exc:
            self.external_hint_prefetch_errors.append(f'{type(exc).__name__}: {exc}')
            return
        self.external_hint_prefetch_errors.extend(str(error) for error in result.get('errors') or [])

    def _hydrate_tmdb_refs(self, refs: list[str]) -> dict[str, Any]:
        refs = _dedupe_nonempty([str(ref or '').strip() for ref in refs])
        if not refs:
            return {
                'ok': True,
                'errors': [],
                'candidate_count': 0,
                'tmdb_legal_graph': self.legal_graph.model_dump(mode='json'),
            }
        hint_refs = {
            hint.tmdb_ref
            for hints in (self.external_mapping_index.hints_by_subject.values() if self.external_mapping_index else ())
            for hint in hints
        }
        candidates: list[TmdbCandidateCard] = []
        errors: list[str] = []
        existing = self.legal_graph.candidate_map()
        for ref in refs:
            if ref in existing:
                candidates.append(existing[ref])
                continue
            media_type, tmdb_id = _parse_tmdb_ref(ref)
            if not media_type or tmdb_id <= 0:
                errors.append(f'invalid tmdb_ref: {ref}')
                continue
            if media_type == 'tv':
                tv_info = self._get_bgm_aligned_tv_info(tmdb_id)
                if not tv_info:
                    errors.append(f'tv:{tmdb_id} details not found')
                    continue
                candidates.append(build_tmdb_tv_candidate_card(tv_info))
            else:
                movie_info = self.tmdb_search.get_movie_info_by_id(tmdb_id)
                if not movie_info:
                    errors.append(f'movie:{tmdb_id} details not found')
                    continue
                alternative_titles = _safe_call(self.tmdb_search._tmdb_movie_alternative_titles, tmdb_id) or {}
                translations = _safe_call(self.tmdb_search._tmdb_movie_translations, tmdb_id) or {}
                candidates.append(
                    build_tmdb_movie_candidate_card(
                        movie_info,
                        alternative_titles=alternative_titles,
                        translations=translations,
                    )
                )
        loaded_refs = {
            candidate.tmdb_ref
            for candidate in candidates
        }
        self.external_hint_hydrated_refs.update(loaded_refs.intersection(hint_refs))
        if candidates:
            self._merge_legal_graph(candidates)
        target_shape_guidance: list[str] = []
        for ref in refs:
            media_type, _ = _parse_tmdb_ref(ref)
            target_shape_guidance.extend(self._target_shape_search_hints(media_type))
        return {
            'ok': not errors or bool(candidates),
            'errors': errors,
            'candidate_count': len(candidates),
            'tmdb_legal_graph': self.legal_graph.model_dump(mode='json'),
            'target_shape_guidance': _dedupe_nonempty(target_shape_guidance),
        }

    def _get_bgm_aligned_tv_info(self, tmdb_id: int) -> dict[str, Any] | None:
        best_info: dict[str, Any] | None = None
        best_score = (-1, -1)
        for language in _bgm_aligned_tmdb_language_order(self.bridge_input):
            tv_info = self._fetch_tv_info_in_language(tmdb_id, language)
            if not tv_info:
                continue
            score = _score_tmdb_tv_alignment(tv_info, self.bridge_input)
            if score > best_score:
                best_info = tv_info
                best_score = score
        if best_info is not None:
            return best_info

        tv_info = _safe_call(getattr(self.tmdb_search, 'get_tv_info_by_id', None), tmdb_id)
        if not isinstance(tv_info, dict) or not tv_info:
            return None
        tv_info = _safe_call(getattr(self.tmdb_search, 'fill_season_info', None), tv_info) or tv_info
        return self._enrich_tv_alias_metadata(tv_info)

    def _fetch_tv_info_in_language(self, tmdb_id: int, language: str) -> dict[str, Any] | None:
        tv_info = _safe_call(getattr(self.tmdb_search, '_tmdb_tv_info', None), tmdb_id, language=language)
        if not isinstance(tv_info, dict) or not tv_info:
            tv_info = _safe_call(getattr(self.tmdb_search, 'get_tv_info_by_id', None), tmdb_id)
        if not isinstance(tv_info, dict) or not tv_info:
            return None
        tv_info = deepcopy(tv_info)

        seasons = tv_info.get('seasons')
        if not isinstance(seasons, list) or not seasons:
            fallback_info = _safe_call(getattr(self.tmdb_search, 'get_tv_info_by_id', None), tmdb_id)
            if isinstance(fallback_info, dict) and isinstance(fallback_info.get('seasons'), list):
                tv_info['seasons'] = deepcopy(fallback_info['seasons'])
                seasons = tv_info['seasons']

        if isinstance(seasons, list) and seasons:
            hydrated_seasons: list[dict[str, Any]] = []
            season_fetcher = getattr(self.tmdb_search, '_tmdb_season_info', None)
            for season in seasons:
                if not isinstance(season, dict):
                    continue
                season_payload = deepcopy(season)
                season_number = _int_or_none(season_payload.get('season_number'))
                if season_number is not None and callable(season_fetcher):
                    detailed = _safe_call(season_fetcher, tmdb_id, season_number, language=language)
                    if isinstance(detailed, dict) and detailed:
                        season_payload.update(deepcopy(detailed))
                        season_payload.setdefault('season_number', season_number)
                        season_payload['_episodes_loaded'] = bool(season_payload.get('episodes'))
                hydrated_seasons.append(season_payload)
            tv_info['seasons'] = hydrated_seasons
        elif callable(getattr(self.tmdb_search, 'fill_season_info', None)):
            tv_info = _safe_call(getattr(self.tmdb_search, 'fill_season_info', None), tv_info) or tv_info

        return self._enrich_tv_alias_metadata(tv_info)

    def _enrich_tv_alias_metadata(self, tv_info: dict[str, Any]) -> dict[str, Any]:
        enriched = _safe_call(getattr(self.tmdb_search, 'enrich_tv_alias_metadata', None), tv_info)
        return enriched if isinstance(enriched, dict) and enriched else tv_info

    def tool_validate_bgm_to_tmdb_bridge(self, bridge_draft: dict[str, Any] | None = None) -> dict[str, Any]:
        draft, error = self._parse_bridge_draft_payload(bridge_draft)
        if error:
            return {'ok': False, 'accepted': False, 'status': 'invalid', 'error': error, 'repair_hints': ['Submit a JSON object shaped like BgmToTmdbMappingDraft.']}
        assert draft is not None
        verifier_result = verify_bgm_to_tmdb_draft(self.bridge_input, self.legal_graph, draft)
        self.bridge_draft = draft
        self.bridge_verifier_result = verifier_result
        repair_hints = _bridge_repair_hints(verifier_result)
        self._write_bridge_artifacts(draft, verifier_result)
        accepted = bool(verifier_result.passed)
        return {
            'ok': True,
            'accepted': accepted,
            'status': 'accepted' if accepted else 'invalid',
            'summary': verifier_result.summary,
            'repair_hints': repair_hints,
            'verifier_result': verifier_result.model_dump(mode='json'),
            'bridge_draft': draft.model_dump(mode='json'),
        }

    def tool_submit_bgm_to_tmdb_bridge(self, bridge_draft: dict[str, Any] | None = None, summary: str = '') -> dict[str, Any]:
        draft, error = self._parse_bridge_draft_payload(bridge_draft)
        if error:
            return {'ok': False, 'accepted': False, 'status': 'invalid', 'error': error, 'repair_hints': ['Submit a JSON object shaped like BgmToTmdbMappingDraft.']}
        assert draft is not None
        verified_plan, verifier_result = verify_and_compile_bgm_to_tmdb_plan(self.bridge_input, self.legal_graph, draft)
        self.bridge_draft = draft
        self.bridge_verifier_result = verifier_result
        self.verified_plan = verified_plan
        self._write_bridge_artifacts(draft, verifier_result, verified_plan=verified_plan)
        if not verifier_result.passed or verified_plan is None:
            self.submit_rejection_count += 1
            repair_hints = _bridge_repair_hints(verifier_result)
            self.last_invalid_submission = {
                'bridge_draft': draft.model_dump(mode='json'),
                'verifier_result': verifier_result.model_dump(mode='json'),
                'repair_hints': repair_hints,
            }
            return {
                'ok': True,
                'accepted': False,
                'status': 'invalid',
                'summary': 'Verifier rejected the BGM-to-TMDB bridge draft; revise and submit again.',
                'repair_hints': repair_hints,
                'verifier_result': verifier_result.model_dump(mode='json'),
            }
        self.final_result = {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': str(summary or verified_plan.summary or 'Pi submitted a verifier-accepted BGM-to-TMDB bridge draft.'),
            'final_action': 'submit_bgm_to_tmdb_bridge',
            'bridge_draft': draft.model_dump(mode='json'),
            'verified_plan': verified_plan.model_dump(mode='json'),
            'final_verifier_result': verifier_result.model_dump(mode='json'),
        }
        self._write_final_result()
        return {
            'ok': True,
            'accepted': True,
            'status': 'accepted',
            'summary': self.final_result['summary'],
            'repair_hints': [],
            'verifier_result': verifier_result.model_dump(mode='json'),
            'verified_plan': verified_plan.model_dump(mode='json'),
        }

    def tool_fail_closed(
        self,
        reason: str,
        reason_kind: str = 'insufficient_evidence',
        related_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        allowed = {'contradiction', 'insufficient_evidence', 'provider_failure', 'unknown'}
        kind = str(reason_kind or 'insufficient_evidence')
        if kind not in allowed:
            kind = 'unknown'
        summary = str(reason or 'BGM-to-TMDB bridge failed closed.')
        verifier_result = CaseVerifierResult(passed=True, issues=[], summary='fail_closed')
        self.final_result = {
            'ok': True,
            'accepted': True,
            'status': 'fail_closed',
            'summary': summary,
            'final_action': 'fail_closed',
            'reason_kind': kind,
            'related_refs': [str(ref) for ref in (related_refs or []) if str(ref)],
            'final_verifier_result': verifier_result.model_dump(mode='json'),
        }
        self._write_final_result()
        return {'ok': True, 'accepted': True, 'status': 'fail_closed', 'summary': summary}

    def auto_finalize_accepted_validation(self) -> dict[str, Any]:
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        if self.recipe_params is not None:
            if self.bridge_verifier_result is None or not self.bridge_verifier_result.passed:
                return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'latest recipe verifier result is not accepted'}
            if self.recipe_review_warnings:
                return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'latest recipe validation has review warnings'}
            result = self.handle_tool(
                'submit_bgm_to_tmdb_bridge_recipe_params',
                {
                    'recipe_params': self.recipe_params.model_dump(mode='json'),
                    'summary': 'Runner finalized Pi-validated BGM-to-TMDB recipe params after validate returned accepted=true.',
                },
            )
            if result.get('accepted') and self.final_result:
                self.final_result['auto_finalized_from_validated_recipe_params'] = True
                self._write_final_result()
            return result
        return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'no validated recipe_params has been accepted'}

    def auto_fail_closed_no_final_result(self, reason: str) -> dict[str, Any]:
        if self.final_result:
            return {'ok': True, 'accepted': True, 'skipped': True, 'reason': 'final result already exists'}
        return self.handle_tool(
            'fail_closed',
            {
                'reason': reason,
                'reason_kind': 'provider_failure' if 'timeout' in str(reason).casefold() or str(reason).casefold() == 'budget_exhausted' else 'unknown',
            },
        )

    def tool_summary(self) -> dict[str, Any]:
        return {
            'tool_trace_count': len(self.tool_trace),
            'tool_call_counts': _counter([str(row.get('tool') or '') for row in self.tool_trace]),
            'tool_sequence': [str(row.get('tool') or '') for row in self.tool_trace],
            'submit_rejection_count': self.submit_rejection_count,
            'external_hints_mode': self.external_hints_mode,
            'external_hint_count': self.external_mapping_index.hint_count if self.external_mapping_index else 0,
            'external_hint_subject_count': self.external_mapping_index.subject_count if self.external_mapping_index else 0,
            'external_hint_hydrated_refs': sorted(self.external_hint_hydrated_refs),
            'external_hint_prefetch_errors': list(self.external_hint_prefetch_errors),
            'external_hint_prefetch_omitted_refs': list(self.external_hint_prefetch_omitted_refs),
            'external_hint_search_shortcut_count': self.external_hint_search_shortcut_count,
        }

    def _external_hint_action_payload(self) -> dict[str, Any]:
        if self.external_hints_mode != 'assist' or self.external_mapping_index is None:
            return {
                'hint_refs': [],
                'prefetched_refs': sorted(self.external_hint_hydrated_refs),
                'unique_prefetched_candidate_ready': False,
                'next_action': 'Use normal title search when a TMDB anchor is needed.',
            }
        subject_ids = {
            int(assignment.target_span.bangumi_subject_id or assignment.target.bangumi_subject_id or 0)
            for assignment in self.bridge_input.assignments
            if assignment.is_mapped_bangumi
        }
        hint_refs = sorted({
            hint.tmdb_ref
            for subject_id in subject_ids
            for hint in self.external_mapping_index.hints_for_subject(subject_id)
        })
        ready = len(hint_refs) == 1 and hint_refs[0] in self.external_hint_hydrated_refs
        if ready:
            next_action = (
                f'First action: call get_tmdb_legal_graph with {hint_refs[0]}. '
                'This is a read-only external candidate hint; compare the hydrated graph before drafting. '
                'Use title search only if the graph does not fit or the verifier reports a gap.'
            )
        elif hint_refs:
            next_action = (
                'External candidate hints exist but are missing or conflicting. '
                'Hydrate the visible hint refs together when useful, then use title search for unresolved candidates.'
            )
        else:
            next_action = 'Use normal title search when a TMDB anchor is needed.'
        return {
            'hint_refs': hint_refs,
            'prefetched_refs': sorted(self.external_hint_hydrated_refs),
            'unique_prefetched_candidate_ready': ready,
            'next_action': next_action,
        }

    def _target_shape_search_hints(self, media_type: str) -> list[str]:
        shapes = _ordered_movie_source_shapes(self.bridge_input)
        if not shapes:
            return []
        shape_label = '; '.join(
            f"subject {shape['bangumi_subject_id']} has {shape['source_item_count']} ordered source items"
            + (f" ({shape['sort_range']})" if shape['sort_range'] else '')
            for shape in shapes
        )
        base = (
            f'BGM source-shape evidence: {shape_label}. '
            'source media_kind="movie" is source-side catalog evidence, not a TMDB target-media decision.'
        )
        if media_type == 'movie':
            return [
                base,
                'Before drafting a movie rule or tmdb_absent_group, search the same anchor with media_type="tv" and compare episode count, order, title, and runtime against the ordered BGM cards.',
            ]
        if media_type == 'tv':
            return [
                base,
                'Compare this TV episode graph with a movie aggregate graph when the source card may represent a chapterized film; choose only after checking which graph covers the complete accepted source frontier.',
            ]
        return [
            base,
            'Compare both TV episode-sequence and movie-aggregate candidates before drafting a target or tmdb_absent_group for this ordered source surface.',
        ]

    def _context_payload(self, *, detail: bool = False) -> dict[str, Any]:
        external_hint_action = self._external_hint_action_payload()
        payload: dict[str, Any] = {
            'bridge_input': self.bridge_input.model_dump(mode='json'),
            'bangumi_subject_cards': _subject_cards(
                self.bridge_input,
                external_mapping_index=self.external_mapping_index,
                external_hints_mode=self.external_hints_mode,
            ),
            'tmdb_legal_graph': self.legal_graph.model_dump(mode='json'),
            'external_mapping': {
                'mode': self.external_hints_mode,
                'agent_visible': self.external_hints_mode == 'assist',
                'audit': self.external_mapping_index.audit_payload() if self.external_mapping_index else {},
                **external_hint_action,
            },
            'bridge_contract': {
                'identity_policy': 'TMDB titles, original names, aliases, and slugs are semantic evidence only; mapped targets must use tv:<tmdb_id>:SxxEyy or movie:<tmdb_id> legal nodes. BGM assignments that TMDB does not expose may be marked tmdb_target_absent.',
                'final_tools': ['validate_bgm_to_tmdb_bridge_recipe_params', 'submit_bgm_to_tmdb_bridge_recipe_params', 'fail_closed'],
                'tmdb_tools': ['search_tmdb_candidates', 'get_tmdb_legal_graph'],
                'primary_workflow': 'Use recipe params for the normal TV/movie/special/span/tmdb_absent/supplemental workflow.',
                'accepted_mapping_outcomes': [
                    'map_to_tmdb for BGM assignments with exposed TMDB legal nodes',
                    'tmdb_target_absent for BGM assignments that TMDB does not expose as legal nodes after targeted title/episode-title checks',
                    'unmapped_supplemental only for Local-to-Bangumi supplemental/non-Bangumi assignments',
                ],
                'search_policy': (
                    external_hint_action['next_action']
                    if external_hint_action['unique_prefetched_candidate_ready']
                    else 'Search enough to identify plausible TMDB refs, then validate. Prefer anchor-first hydration over searching every season/special title. Do not exhaust turns searching recap/summary/CM/bonus variants when TMDB exposes no legal node.'
                ),
                'franchise_anchor_policy': 'For multi-season franchise packages, search one strong franchise/series anchor, hydrate it, and compare season/S00/episode cards before doing separate season, OVA, OAD, or special title searches.',
                'episode_title_policy': 'When series title evidence is ambiguous, compare BGM episode_title_cards_sample with the hydrated TMDB legal-node episode titles shown in this context. The fixed layer presents one BGM-aligned TMDB evidence view when it can, so recipe params can stay language-agnostic and focus on visible title/order/count alignment.',
                'target_shape_policy': _target_shape_policy(self.bridge_input),
                'search_guidance': self._search_guidance_payload(),
                'dry_run_only': True,
            },
        }
        if detail:
            payload['current_recipe_params'] = self.recipe_params.model_dump(mode='json') if self.recipe_params else None
            payload['current_bridge_draft'] = self.bridge_draft.model_dump(mode='json') if self.bridge_draft else None
            payload['current_verifier_result'] = self.bridge_verifier_result.model_dump(mode='json') if self.bridge_verifier_result else None
            payload['current_review_warnings'] = _json_safe(self.recipe_review_warnings)
            payload['last_invalid_submission'] = _json_safe(self.last_invalid_submission)
        return payload

    def _parse_recipe_params_payload(self, payload: dict[str, Any] | None) -> tuple[BgmToTmdbRecipeParams | None, str]:
        if payload is None:
            return None, 'missing recipe_params'
        if not isinstance(payload, dict):
            return None, 'recipe_params must be a canonical JSON object'
        try:
            return BgmToTmdbRecipeParams.model_validate(payload), ''
        except Exception as exc:
            return None, str(exc)

    def _parse_bridge_draft_payload(self, payload: dict[str, Any] | None) -> tuple[BgmToTmdbMappingDraft | None, str]:
        if payload is None:
            return None, 'missing bridge_draft'
        if not isinstance(payload, dict):
            return None, 'bridge_draft must be a canonical JSON object'
        try:
            return BgmToTmdbMappingDraft.model_validate(payload), ''
        except Exception as exc:
            return None, str(exc)

    def _write_bridge_artifacts(
        self,
        draft: BgmToTmdbMappingDraft,
        verifier_result: CaseVerifierResult,
        *,
        verified_plan: VerifiedBgmToTmdbPlan | None = None,
        recipe_params: BgmToTmdbRecipeParams | None = None,
        review_warnings: list[dict[str, Any]] | None = None,
        rule_match_counts: dict[str, int] | None = None,
    ) -> None:
        self._write_external_mapping_audit()
        artifacts_dir = self.run_dir / 'artifacts'
        if recipe_params is not None:
            (artifacts_dir / 'bgm_to_tmdb_recipe_params.json').write_text(
                json.dumps(recipe_params.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
                encoding='utf-8',
            )
        (artifacts_dir / 'bgm_to_tmdb_bridge_draft.json').write_text(
            json.dumps(draft.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        verifier_payload = verifier_result.model_dump(mode='json')
        if review_warnings is not None:
            verifier_payload['review_warnings'] = review_warnings
        if rule_match_counts is not None:
            verifier_payload['rule_match_counts'] = rule_match_counts
        (artifacts_dir / 'bgm_to_tmdb_bridge_verifier_result.json').write_text(
            json.dumps(verifier_payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        (artifacts_dir / 'bgm_to_tmdb_legal_graph.json').write_text(
            json.dumps(self.legal_graph.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        if verified_plan is not None:
            (artifacts_dir / 'bgm_to_tmdb_verified_plan.json').write_text(
                json.dumps(verified_plan.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
                encoding='utf-8',
            )

    def _write_final_result(self) -> None:
        (self.run_dir / 'final_result.json').write_text(
            json.dumps(_json_safe(self.final_result), ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )

    def _compact_result_summary(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {'type': type(result).__name__}
        summary = {key: result.get(key) for key in ('ok', 'accepted', 'status', 'summary', 'error') if key in result}
        if isinstance(result.get('repair_hints'), list):
            summary['repair_hint_count'] = len(result['repair_hints'])
        if isinstance(result.get('review_warnings'), list):
            summary['review_warning_count'] = len(result['review_warnings'])
        if isinstance(result.get('rule_match_counts'), dict):
            summary['rule_count'] = len(result['rule_match_counts'])
        graph = result.get('tmdb_legal_graph')
        if isinstance(graph, dict):
            summary['tmdb_candidate_count'] = len(graph.get('candidates') or [])
        hydration = result.get('tmdb_hydration')
        if isinstance(hydration, dict):
            summary['hydrated_candidate_count'] = hydration.get('candidate_count')
            summary['hydration_error_count'] = len(hydration.get('errors') or [])
        verifier = result.get('verifier_result')
        if isinstance(verifier, dict):
            summary['verifier_passed'] = verifier.get('passed')
            summary['verifier_issue_count'] = len(verifier.get('issues') or [])
        return summary

    def _search_guidance_payload(self) -> dict[str, int]:
        return {
            'used': int(self.search_call_count),
            'soft_limit': int(self.search_guidance_soft_limit),
        }

    def _merge_legal_graph(self, candidates: list[TmdbCandidateCard]) -> None:
        merged = self.legal_graph.candidate_map()
        for candidate in candidates:
            merged[candidate.tmdb_ref] = candidate
        self.legal_graph = build_tmdb_legal_graph(
            [candidate.model_dump(mode='json') for candidate in merged.values()],
            generated_by='bgm_to_tmdb_bridge_tools',
        )


def _bridge_repair_hints(verifier_result: CaseVerifierResult) -> list[str]:
    hints: list[str] = []
    for issue in verifier_result.issues:
        code = issue.issue_code
        if code == 'missing_source_mapping':
            hints.append(f'Add exactly one bridge mapping for source_path {issue.ref}.')
        elif code == 'unknown_source_path':
            hints.append(f'Remove source_path {issue.ref}; it is not in the accepted BGM compiled plan.')
        elif code == 'unknown_tmdb_legal_node':
            hints.append('Copy TMDB legal node IDs only from tmdb_legal_graph. Titles, URLs, and slugs are evidence, not target IDs.')
        elif code == 'bare_tmdb_node_not_allowed':
            hints.append('Replace bare tmdb:SxxEyy with tv:<tmdb_id>:SxxEyy from the candidate legal graph.')
        elif code == 'duplicate_tmdb_target':
            hints.append(f'Choose a distinct exposed TMDB node; duplicate target {issue.ref} is not allowed in this dry-run contract.')
        elif code == 'supplemental_mapped_to_tmdb':
            hints.append(f'Keep supplemental/non-Bangumi source {issue.ref} as disposition unmapped_supplemental with no TMDB nodes.')
        elif code == 'tmdb_target_count_mismatch':
            hints.append(f'Check source {issue.ref}: ordinary mappings need one TMDB node, BGM TV spans must list every covered TMDB node, and a BGM span may map to one TMDB movie node when TMDB models the span as a movie. If TMDB lacks the needed node, use a tmdb_absent_group rule instead.')
        elif code == 'mapped_bangumi_assignment_unmapped':
            hints.append(f'Map BGM source {issue.ref} to exposed TMDB legal nodes, or cover it with tmdb_absent_group after targeted title/episode-title checks show TMDB lacks the node.')
        elif code == 'tmdb_absent_mapping_has_targets':
            hints.append(f'Remove TMDB node IDs from tmdb_target_absent source {issue.ref}; absent mappings are explicit no-node outcomes.')
        elif code == 'tmdb_absent_rule_selected_supplemental_assignment':
            hints.append(f'Source {issue.ref} is supplemental/non-Bangumi; cover it with supplemental_group instead of tmdb_absent_group.')
    return _dedupe_nonempty(hints)


def _review_warning_hints(review_warnings: list[dict[str, Any]]) -> list[str]:
    hints: list[str] = []
    for warning in review_warnings:
        hint = str(warning.get('repair_hint') or '').strip()
        if hint:
            hints.append(hint)
            continue
        code = str(warning.get('code') or '').strip()
        rule = str(warning.get('rule') or '').strip()
        if code == 'low_confidence_tmdb_recipe_rule':
            hints.append(f'Add concrete TMDB title/original/alias/year/season/episode-title evidence for {rule}, or fail_closed for global ambiguity.')
        elif code == 'missing_tmdb_semantic_reason':
            hints.append(f'Add one concise semantic evidence sentence for {rule}.')
    return _dedupe_nonempty(hints)


def _subject_cards(
    bridge_input: BgmToTmdbInput,
    *,
    external_mapping_index: ExternalMappingIndex | None = None,
    external_hints_mode: str = 'off',
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[Any]] = {}
    supplemental: list[Any] = []
    for assignment in bridge_input.assignments:
        if not assignment.is_mapped_bangumi:
            supplemental.append(assignment)
            continue
        subject_id = int(assignment.target_span.bangumi_subject_id or assignment.target.bangumi_subject_id or 0)
        media_kind = str(assignment.target_span.media_kind or assignment.target.media_kind or '')
        grouped.setdefault((subject_id, media_kind), []).append(assignment)

    cards: list[dict[str, Any]] = []
    for (subject_id, media_kind), assignments in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        sorts = _numbers([
            assignment.target.sort
            for assignment in assignments
            if assignment.target.sort is not None
        ])
        eps = _numbers([
            assignment.target.ep
            for assignment in assignments
            if assignment.target.ep is not None
        ])
        episode_type_counts = _counter([
            str(assignment.target_span.episode_type or assignment.target.episode_type or '')
            for assignment in assignments
        ])
        rule_counts = _counter([str(assignment.rule_name or '') for assignment in assignments])
        card = {
            'bangumi_subject_id': subject_id,
            'media_kind': media_kind,
            'assignment_count': len(assignments),
            'mapped_assignment_count': len(assignments),
            'supplemental_assignment_count': 0,
            'episode_type_counts': episode_type_counts,
            'sort_range': _range_label(sorts),
            'sort_values_sample': sorts[:12],
            'ep_range': _range_label(eps),
            'ep_values_sample': eps[:12],
            'rule_counts': rule_counts,
            'episode_id_sample': _numbers([
                assignment.target.episode_id
                for assignment in assignments
                if assignment.target.episode_id
            ])[:12],
            'title_sample': _dedupe_nonempty([
                assignment.target.title
                for assignment in assignments
                if assignment.target.title
            ])[:8],
            'episode_title_cards_sample': _episode_title_cards(assignments),
            'source_shape_observation': _source_shape_observation(assignments, media_kind),
            'source_path_sample': [
                normalize_source_path(assignment.source_path)
                for assignment in assignments[:8]
            ],
            'span_assignment_count': sum(1 for assignment in assignments if assignment.is_span),
        }
        if external_hints_mode == 'assist' and external_mapping_index is not None:
            card['external_mapping_hints'] = [
                hint.payload()
                for hint in external_mapping_index.hints_for_subject(subject_id)
            ]
        cards.append(card)

    if supplemental:
        cards.append({
            'bangumi_subject_id': 0,
            'media_kind': 'supplemental',
            'assignment_count': len(supplemental),
            'mapped_assignment_count': 0,
            'supplemental_assignment_count': len(supplemental),
            'episode_type_counts': _counter([
                str(assignment.target_span.episode_type or assignment.target.episode_type or assignment.disposition or '')
                for assignment in supplemental
            ]),
            'sort_range': '',
            'ep_range': '',
            'rule_counts': _counter([str(assignment.rule_name or '') for assignment in supplemental]),
            'source_path_sample': [
                normalize_source_path(assignment.source_path)
                for assignment in supplemental[:12]
            ],
        })
    return cards


def _source_shape_observation(assignments: list[Any], media_kind: str) -> dict[str, Any]:
    mapped = [assignment for assignment in assignments if assignment.is_mapped_bangumi]
    sorts = _numbers([
        assignment.target.sort
        for assignment in mapped
        if assignment.target.sort is not None
    ])
    span_item_count = sum(
        len(assignment.target_span.episode_ids)
        for assignment in mapped
    )
    source_item_count = max(len(mapped), len(sorts), span_item_count)
    ordered = source_item_count > 1 and bool(sorts or span_item_count)
    movie_shape_comparison = media_kind == 'movie' and ordered
    return {
        'source_media_kind': media_kind,
        'source_item_count': source_item_count,
        'ordered_source_items': ordered,
        'sort_range': _range_label(sorts),
        'span_item_count': span_item_count,
        'source_media_kind_is_not_tmdb_media_type': True,
        'target_shape_comparison_required': movie_shape_comparison,
        'target_shape_candidates': (
            ['tv_episode_sequence', 'movie_aggregate']
            if movie_shape_comparison
            else []
        ),
    }


def _ordered_movie_source_shapes(bridge_input: BgmToTmdbInput) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[Any]] = {}
    for assignment in bridge_input.assignments:
        if not assignment.is_mapped_bangumi:
            continue
        subject_id = int(
            assignment.target_span.bangumi_subject_id
            or assignment.target.bangumi_subject_id
            or 0
        )
        media_kind = str(
            assignment.target_span.media_kind
            or assignment.target.media_kind
            or ''
        )
        grouped.setdefault((subject_id, media_kind), []).append(assignment)

    shapes: list[dict[str, Any]] = []
    for (subject_id, media_kind), assignments in sorted(grouped.items()):
        observation = _source_shape_observation(assignments, media_kind)
        if not observation['target_shape_comparison_required']:
            continue
        shapes.append({
            'bangumi_subject_id': subject_id,
            'source_item_count': observation['source_item_count'],
            'sort_range': observation['sort_range'],
            'span_item_count': observation['span_item_count'],
        })
    return shapes


def _target_shape_policy(bridge_input: BgmToTmdbInput) -> dict[str, Any]:
    shapes = _ordered_movie_source_shapes(bridge_input)
    return {
        'source_media_kind_is_not_tmdb_media_type': True,
        'comparison_required': bool(shapes),
        'ordered_movie_source_shapes': shapes,
        'candidate_target_shapes': (
            ['tv_episode_sequence', 'movie_aggregate']
            if shapes
            else []
        ),
        'instruction': (
            'Compare TV episode-sequence and movie-aggregate legal graphs before drafting a target or tmdb_absent_group.'
            if shapes
            else 'Use the accepted BGM assignment shape and hydrated legal graph as semantic evidence.'
        ),
    }


def _episode_title_cards(assignments: list[Any], *, limit: int = 16) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for assignment in assignments[:limit]:
        target = assignment.target
        target_span = assignment.target_span
        cards.append({
            'source_path': normalize_source_path(assignment.source_path),
            'episode_id': int(target.episode_id or 0),
            'sort': target.sort,
            'ep': target.ep,
            'episode_type': str(target_span.episode_type or target.episode_type or ''),
            'title': str(target.title or ''),
            'span_episode_ids': [int(item) for item in (target_span.episode_ids or [])],
            'span_sort_range': _span_range_label(target_span.sort_start, target_span.sort_end),
        })
    return cards


def _candidate_search_payload(candidate: dict[str, Any], *, media_hint: str = '') -> dict[str, Any]:
    media_type = str(candidate.get('media_type') or media_hint or '').casefold()
    if media_type not in {'tv', 'movie'}:
        media_type = 'movie' if candidate.get('title') else 'tv'
    tmdb_id = int(candidate.get('id') or 0)
    title = str(candidate.get('name') or candidate.get('title') or '')
    original = str(candidate.get('original_name') or candidate.get('original_title') or '')
    date = str(candidate.get('first_air_date') or candidate.get('release_date') or '')
    slug = _semantic_slug(tmdb_id, title or original)
    return {
        'media_type': media_type,
        'tmdb_id': tmdb_id,
        'tmdb_ref': f'{media_type}:{tmdb_id}' if tmdb_id else '',
        'display_title': title,
        'original_name': original if media_type == 'tv' else '',
        'original_title': original if media_type == 'movie' else '',
        'slug': slug,
        'web_url': f'https://www.themoviedb.org/{media_type}/{slug}' if slug else '',
        'year': _year_from_date(date),
        'overview': str(candidate.get('overview') or ''),
        'match_score': candidate.get('_match_score'),
        'matched_query': candidate.get('_matched_query'),
    }


def _parse_tmdb_ref(ref: str) -> tuple[str, int]:
    media_type, _, raw_id = str(ref or '').partition(':')
    media_type = media_type.strip().casefold()
    if media_type not in {'tv', 'movie'}:
        return '', 0
    try:
        return media_type, int(raw_id)
    except ValueError:
        return '', 0


def _semantic_slug(tmdb_id: int, title: str) -> str:
    import re
    import unicodedata

    normalized = unicodedata.normalize('NFKD', str(title or ''))
    ascii_title = normalized.encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', ascii_title).strip('-').lower()
    if tmdb_id <= 0:
        return slug
    return f'{int(tmdb_id)}-{slug}' if slug else str(int(tmdb_id))


def _year_from_date(value: str) -> int | None:
    import re

    match = re.match(r'^(\d{4})', str(value or ''))
    return int(match.group(1)) if match else None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == '':
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_call(func: Any, *args: Any, **kwargs: Any) -> Any:
    if not callable(func):
        return None
    try:
        return func(*args, **kwargs)
    except Exception:
        return None


def _bgm_aligned_tmdb_language_order(bridge_input: BgmToTmdbInput) -> list[str]:
    evidence = ' '.join(_bgm_alignment_texts(bridge_input))
    if _contains_japanese_kana(evidence):
        order = ['ja-JP', 'zh-TW', 'zh-CN', 'en-US']
    elif _contains_cjk(evidence):
        order = ['zh-CN', 'zh-TW', 'ja-JP', 'en-US']
    else:
        order = ['en-US', 'ja-JP', 'zh-CN', 'zh-TW']
    return _dedupe_nonempty(order)


def _score_tmdb_tv_alignment(tv_info: dict[str, Any], bridge_input: BgmToTmdbInput) -> tuple[int, int]:
    evidence = _bgm_alignment_texts(bridge_input)
    if not evidence:
        return (0, 0)
    episode_scores: list[int] = []
    all_scores: list[int] = []
    for title in _tmdb_tv_alignment_titles(tv_info, include_series=True):
        score = max((_text_alignment_score(title, item) for item in evidence), default=0)
        if score:
            all_scores.append(score)
    for title in _tmdb_tv_alignment_titles(tv_info, include_series=False):
        score = max((_text_alignment_score(title, item) for item in evidence), default=0)
        if score:
            episode_scores.append(score)
    return (sum(sorted(episode_scores, reverse=True)[:8]), sum(sorted(all_scores, reverse=True)[:12]))


def _tmdb_tv_alignment_titles(tv_info: dict[str, Any], *, include_series: bool) -> list[str]:
    titles: list[str] = []
    if include_series:
        titles.extend([
            str(tv_info.get('name') or ''),
            str(tv_info.get('title') or ''),
            str(tv_info.get('original_name') or ''),
            str(tv_info.get('original_title') or ''),
        ])
        titles.extend(_list_texts(tv_info.get('_metadata_alias_titles')))
    for season in tv_info.get('seasons') or []:
        if not isinstance(season, dict):
            continue
        if include_series:
            titles.append(str(season.get('name') or ''))
        for episode in season.get('episodes') or []:
            if not isinstance(episode, dict):
                continue
            titles.extend([
                str(episode.get('name') or ''),
                str(episode.get('title') or ''),
            ])
    return _dedupe_nonempty(titles)


def _bgm_alignment_texts(bridge_input: BgmToTmdbInput) -> list[str]:
    values = [bridge_input.source_path]
    for assignment in bridge_input.assignments:
        if not assignment.is_mapped_bangumi:
            continue
        values.extend([
            assignment.source_path,
            assignment.target.title,
            assignment.reason,
        ])
    return _dedupe_nonempty([str(value or '') for value in values])


def _text_alignment_score(left: str, right: str) -> int:
    left_norm = _normalize_alignment_text(left)
    right_norm = _normalize_alignment_text(right)
    if not left_norm or not right_norm:
        return 0
    short, long = sorted([left_norm, right_norm], key=len)
    score = 0
    if len(short) >= 4 and short in long:
        score += len(short) + 8
    left_words = set(_alignment_words(left_norm))
    right_words = set(_alignment_words(right_norm))
    score += len(left_words & right_words) * 4
    left_chars = {char for char in left_norm if char.isalnum() or _is_cjk_or_kana(char)}
    right_chars = {char for char in right_norm if char.isalnum() or _is_cjk_or_kana(char)}
    common_chars = len(left_chars & right_chars)
    if common_chars >= 3:
        score += common_chars * 2
    elif common_chars:
        score += common_chars
    return score


def _normalize_alignment_text(value: str) -> str:
    normalized = unicodedata.normalize('NFKC', str(value or '')).casefold()
    normalized = normalized.translate(_CJK_ALIGNMENT_TRANSLATION)
    return ''.join(char for char in normalized if char.isalnum() or _is_cjk_or_kana(char))


def _alignment_words(value: str) -> list[str]:
    import re

    return [word for word in re.findall(r'[a-z0-9]+', value) if len(word) >= 2]


def _contains_japanese_kana(value: str) -> bool:
    return any('\u3040' <= char <= '\u30ff' or '\u31f0' <= char <= '\u31ff' for char in str(value or ''))


def _contains_cjk(value: str) -> bool:
    return any('\u4e00' <= char <= '\u9fff' for char in str(value or ''))


def _is_cjk_or_kana(char: str) -> bool:
    return (
        '\u3040' <= char <= '\u30ff'
        or '\u31f0' <= char <= '\u31ff'
        or '\u3400' <= char <= '\u9fff'
        or '\uf900' <= char <= '\ufaff'
    )


_CJK_ALIGNMENT_TRANSLATION = str.maketrans({
    '極': '极',
    '東': '东',
    '來': '来',
    '講': '讲',
    '線': '线',
    '區': '区',
    '總': '总',
    '話': '话',
    '號': '号',
    '學': '学',
    '戰': '战',
    '艦': '舰',
    '國': '国',
    '廣': '广',
    '畫': '画',
    '終': '终',
    '體': '体',
    '聲': '声',
    '劇': '剧',
    '員': '员',
    '錄': '录',
    '長': '长',
    '門': '门',
    '間': '间',
    '後': '后',
    '龍': '龙',
    '與': '与',
    '對': '对',
    '異': '异',
    '雙': '双',
    '鬥': '斗',
    '舊': '旧',
    '臺': '台',
})


def _json_safe(value: Any) -> Any:
    if isinstance(value, type):
        return str(value)
    if is_dataclass(value):
        return asdict(cast(Any, value))
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _counter(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _numbers(values: list[int | None]) -> list[int]:
    return sorted({int(value) for value in values if value is not None})


def _range_label(values: list[int]) -> str:
    if not values:
        return ''
    ranges: list[str] = []
    start = previous = int(values[0])
    for raw in values[1:]:
        value = int(raw)
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f'{start}-{previous}')
        start = previous = value
    ranges.append(str(start) if start == previous else f'{start}-{previous}')
    return ','.join(ranges)


def _span_range_label(start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return ''
    if start is None:
        return str(int(end)) if end is not None else ''
    if end is None:
        return str(int(start))
    return str(int(start)) if int(start) == int(end) else f'{int(start)}-{int(end)}'


def _list_texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or '').strip()]


def _dedupe_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
