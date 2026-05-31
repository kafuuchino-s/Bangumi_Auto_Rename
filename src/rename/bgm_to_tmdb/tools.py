from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from ..case_agent.models import CaseVerifierResult
from ..get_info import Search
from .compiler import build_tmdb_legal_graph
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
    tmdb_search: Any | None = None
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

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / 'artifacts').mkdir(parents=True, exist_ok=True)
        if self.tmdb_search is None:
            self.tmdb_search = Search()

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

    def tool_validate_bgm_to_tmdb_bridge_recipe_params(self, recipe_params: dict[str, Any] | str | None = None) -> dict[str, Any]:
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

    def tool_submit_bgm_to_tmdb_bridge_recipe_params(self, recipe_params: dict[str, Any] | str | None = None, summary: str = '') -> dict[str, Any]:
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

    def _hydrate_tmdb_refs(self, refs: list[str]) -> dict[str, Any]:
        refs = _dedupe_nonempty([str(ref or '').strip() for ref in refs])
        if not refs:
            return {
                'ok': True,
                'errors': [],
                'candidate_count': 0,
                'tmdb_legal_graph': self.legal_graph.model_dump(mode='json'),
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
                tv_info = self.tmdb_search.get_tv_info_by_id(tmdb_id)
                if not tv_info:
                    errors.append(f'tv:{tmdb_id} details not found')
                    continue
                tv_info = self.tmdb_search.fill_season_info(tv_info)
                tv_info = self.tmdb_search.enrich_tv_alias_metadata(tv_info)
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
        if candidates:
            self._merge_legal_graph(candidates)
        return {
            'ok': not errors or bool(candidates),
            'errors': errors,
            'candidate_count': len(candidates),
            'tmdb_legal_graph': self.legal_graph.model_dump(mode='json'),
        }

    def tool_validate_bgm_to_tmdb_bridge(self, bridge_draft: dict[str, Any] | str | None = None) -> dict[str, Any]:
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

    def tool_submit_bgm_to_tmdb_bridge(self, bridge_draft: dict[str, Any] | str | None = None, summary: str = '') -> dict[str, Any]:
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
        if self.bridge_draft is None:
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'no bridge_draft has been validated'}
        if self.bridge_verifier_result is None or not self.bridge_verifier_result.passed:
            return {'ok': False, 'accepted': False, 'skipped': True, 'reason': 'latest bridge verifier result is not accepted'}
        result = self.handle_tool(
            'submit_bgm_to_tmdb_bridge',
            {
                'bridge_draft': self.bridge_draft.model_dump(mode='json'),
                'summary': 'Runner finalized a Pi-validated BGM-to-TMDB bridge draft after validate_bgm_to_tmdb_bridge returned accepted=true.',
            },
        )
        if result.get('accepted') and self.final_result:
            self.final_result['auto_finalized_from_validated_bridge'] = True
            self._write_final_result()
        return result

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
        }

    def _context_payload(self, *, detail: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'bridge_input': self.bridge_input.model_dump(mode='json'),
            'bangumi_subject_cards': _subject_cards(self.bridge_input),
            'tmdb_legal_graph': self.legal_graph.model_dump(mode='json'),
            'bridge_contract': {
                'identity_policy': 'TMDB titles, original names, aliases, and slugs are semantic evidence only; mapped targets must use tv:<tmdb_id>:SxxEyy or movie:<tmdb_id> legal nodes. BGM assignments that TMDB does not expose may be marked tmdb_target_absent.',
                'final_tools': ['validate_bgm_to_tmdb_bridge_recipe_params', 'submit_bgm_to_tmdb_bridge_recipe_params', 'validate_bgm_to_tmdb_bridge', 'submit_bgm_to_tmdb_bridge', 'fail_closed'],
                'tmdb_tools': ['search_tmdb_candidates', 'get_tmdb_legal_graph'],
                'primary_workflow': 'Use recipe params for normal TV/movie/special/span/tmdb_absent/supplemental groups. Raw node mappings are debug fallback only.',
                'accepted_mapping_outcomes': [
                    'map_to_tmdb for BGM assignments with exposed TMDB legal nodes',
                    'tmdb_target_absent for BGM assignments that TMDB does not expose as legal nodes after targeted title/episode-title checks',
                    'unmapped_supplemental only for Local-to-Bangumi supplemental/non-Bangumi assignments',
                ],
                'search_policy': 'Search enough to identify plausible TMDB refs, then validate. Prefer anchor-first hydration over searching every season/special title. Do not exhaust turns searching recap/summary/CM/bonus variants when TMDB exposes no legal node.',
                'franchise_anchor_policy': 'For multi-season franchise packages, search one strong franchise/series anchor, hydrate it, and compare season/S00/episode cards before doing separate season, OVA, OAD, or special title searches.',
                'episode_title_policy': 'When series title evidence is ambiguous, compare BGM episode_title_cards_sample with hydrated TMDB legal-node episode titles. Episode titles guide the semantic choice but cannot bypass legal node validation.',
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

    def _parse_recipe_params_payload(self, payload: dict[str, Any] | str | None) -> tuple[BgmToTmdbRecipeParams | None, str]:
        if payload is None:
            return None, 'missing recipe_params'
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                return None, f'invalid recipe_params JSON: {exc}'
        if not isinstance(payload, dict):
            return None, 'recipe_params must be a JSON object'
        try:
            return BgmToTmdbRecipeParams.model_validate(payload), ''
        except Exception as exc:
            return None, str(exc)

    def _parse_bridge_draft_payload(self, payload: dict[str, Any] | str | None) -> tuple[BgmToTmdbMappingDraft | None, str]:
        if payload is None:
            return None, 'missing bridge_draft'
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                return None, f'invalid bridge_draft JSON: {exc}'
        if not isinstance(payload, dict):
            return None, 'bridge_draft must be a JSON object'
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


def _subject_cards(bridge_input: BgmToTmdbInput) -> list[dict[str, Any]]:
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
        cards.append({
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
            'source_path_sample': [
                normalize_source_path(assignment.source_path)
                for assignment in assignments[:8]
            ],
            'span_assignment_count': sum(1 for assignment in assignments if assignment.is_span),
        })

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


def _safe_call(func: Any, *args: Any) -> Any:
    try:
        return func(*args)
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
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
