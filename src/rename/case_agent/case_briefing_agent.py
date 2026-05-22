from __future__ import annotations

from dataclasses import dataclass
import json
from importlib import resources
import time

from pydantic import ValidationError

from .models import (
    CaseBriefingEvidenceQuestion,
    CaseBriefingOutput,
    CaseBriefingTitleHypothesis,
    CaseBriefingWorkUnit,
    CaseDossier,
)
from .notebook import validate_case_briefing_refs
from ..local_fact_surface import compact_file_fact_for_card


@dataclass
class CaseBriefingCallResult:
    ok: bool
    output: CaseBriefingOutput | None
    prompt: str
    error: str = ''
    raw_response: object | None = None
    elapsed_ms: int = 0
    request_audit: dict[str, object] | None = None


def _jsonable(value: object) -> object:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if hasattr(value, '__dataclass_fields__'):
        return {key: _jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _sample(values: list[str], *, limit: int = 8) -> list[str]:
    values = [value for value in values if value]
    if len(values) <= limit:
        return values
    head = values[: max(1, limit // 2)]
    tail = values[-max(1, limit // 2):]
    return list(dict.fromkeys([*head, *tail]))[:limit]


def _compact_local_file(card: object) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    path = str(data.get('path', '') or '').replace('\\', '/')
    compact = {
        'ref': data.get('ref', ''),
        'basename': path.rsplit('/', 1)[-1],
        'path_tail': path.rsplit('/', 2)[-1] if '/' in path else path,
        'parent_display': data.get('parent_display', ''),
        'label': data.get('label', ''),
        'file_kind': data.get('file_kind', ''),
        'is_main': data.get('is_main', False),
        'size_bytes': data.get('size_bytes', 0),
        'fact_summary': data.get('fact_summary') or {},
    }
    fact_card = compact_file_fact_for_card(data, detail=False)
    if fact_card:
        compact['local_facts'] = fact_card
    return compact


def _compact_local_span(card: object) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    file_refs = list(data.get('file_refs') or [])
    return {
        'ref': data.get('ref', ''),
        'span_scope': data.get('span_scope', ''),
        'parent_key': data.get('parent_key', ''),
        'season_cue': data.get('season_cue', ''),
        'file_refs': _sample(file_refs, limit=12),
        'file_ref_count': data.get('file_ref_count', len(file_refs)),
        'file_ref_range': list(data.get('file_ref_range') or [])[:4],
        'file_ref_samples': _sample(list(data.get('file_ref_samples') or []), limit=6),
        'ordering_basis': data.get('ordering_basis', ''),
        'episode_token_start': data.get('episode_token_start'),
        'episode_token_end': data.get('episode_token_end'),
        'episode_token_count': data.get('episode_token_count', 0),
        'title_cues': list(data.get('title_cues') or [])[:8],
        'release_group_cues': list(data.get('release_group_cues') or [])[:8],
        'confidence_facts': list(data.get('confidence_facts') or [])[:8],
    }


def render_case_briefing_prompt(dossier: CaseDossier) -> str:
    template = resources.files(__package__).joinpath('prompts/local_bangumi_case_briefing.md').read_text(encoding='utf-8')
    main_ref_set = set(getattr(dossier.contract, 'main_file_refs', []) or [])
    files = [card for card in list(dossier.local_files or []) if getattr(card, 'ref', '') in main_ref_set]
    payload = {
        'case_id': dossier.header.case_id,
        'contract': {
            'main_file_refs': list(dossier.contract.main_file_refs or []),
            'supplemental_file_refs': list(dossier.contract.supplemental_file_refs or []),
            'allowed_file_refs': list(dossier.contract.allowed_file_refs or []),
        },
        'local_files': [_compact_local_file(card) for card in files[:80]],
        'local_span_cards': [_compact_local_span(card) for card in list(dossier.local_span_cards or [])[:48]],
        'local_clusters': [
            {
                'ref': card.ref,
                'cluster_name': card.cluster_name,
                'title_cues': list(card.title_cues or [])[:8],
                'file_refs': _sample(list(card.file_refs or []), limit=12),
                'summary': card.summary,
            }
            for card in list(dossier.local_clusters or [])[:32]
        ],
        'query_cards': [
            {
                'ref': card.ref,
                'query_text': card.query_text,
                'query_kind': card.query_kind,
                'query_origin': card.query_origin,
                'source_refs': _sample(list(card.source_refs or []), limit=8),
                'ignored_terms': list(card.ignored_terms or [])[:8],
            }
            for card in list(dossier.query_cards or [])[:48]
        ],
    }
    return template.replace('{{DOSSIER_JSON}}', json.dumps(_jsonable(payload), ensure_ascii=False, indent=2))


def fallback_case_briefing(dossier: CaseDossier, *, reason: str = 'case briefing fallback') -> CaseBriefingOutput:
    child_spans = [
        card for card in list(dossier.local_span_cards or [])
        if str(getattr(card, 'span_scope', '') or '') != 'package'
    ]
    if not child_spans and list(getattr(dossier, 'local_span_cards', []) or []):
        child_spans = list(dossier.local_span_cards or [])[:1]
    if not child_spans:
        main_refs = list(getattr(dossier.contract, 'main_file_refs', []) or [])
        work_units = [
            CaseBriefingWorkUnit(
                work_unit_ref='WU1',
                label='unpartitioned local package',
                unit_kind='unpartitioned',
                local_refs=main_refs,
                file_refs=main_refs,
                status='open',
                reason=reason,
            )
        ] if main_refs else []
    else:
        work_units = []
        for index, span in enumerate(child_spans, start=1):
            file_refs = list(getattr(span, 'file_refs', []) or [])
            work_units.append(
                CaseBriefingWorkUnit(
                    work_unit_ref=f'WU{index}',
                    label=' / '.join(list(getattr(span, 'title_cues', []) or [])[:2]) or getattr(span, 'ref', f'LS{index}'),
                    unit_kind=str(getattr(span, 'span_scope', '') or 'local_span'),
                    local_refs=[str(getattr(span, 'ref', '') or '')],
                    file_refs=file_refs,
                    span_refs=[str(getattr(span, 'ref', '') or '')],
                    title_hints=list(getattr(span, 'title_cues', []) or [])[:8],
                    source_form_hints=[str(getattr(span, 'season_cue', '') or '')] if getattr(span, 'season_cue', '') else [],
                    status='open',
                    reason=reason,
                )
            )
    titles: list[CaseBriefingTitleHypothesis] = []
    seen_titles: set[str] = set()
    for span in child_spans:
        source_ref = str(getattr(span, 'ref', '') or '')
        for cue in list(getattr(span, 'title_cues', []) or [])[:4]:
            text = str(cue or '').strip()
            if not text or text.casefold() in seen_titles:
                continue
            seen_titles.add(text.casefold())
            titles.append(
                CaseBriefingTitleHypothesis(
                    title=text,
                    hypothesis_kind='local_span_title_cue',
                    source_refs=[source_ref] if source_ref else [],
                    confidence='medium',
                    reason=reason,
                )
            )
    if not titles:
        for cluster in list(dossier.local_clusters or [])[:6]:
            text = str(getattr(cluster, 'cluster_name', '') or '').strip()
            if not text or text.casefold() in seen_titles:
                continue
            seen_titles.add(text.casefold())
            titles.append(
                CaseBriefingTitleHypothesis(
                    title=text,
                    hypothesis_kind='local_cluster_title_cue',
                    source_refs=[cluster.ref] if getattr(cluster, 'ref', '') else [],
                    confidence='low',
                    reason=reason,
                )
            )
    has_target_surface = bool(
        list(getattr(dossier, 'bangumi_subjects', []) or [])
        or list(getattr(dossier, 'bangumi_items', []) or [])
        or list(getattr(dossier, 'bangumi_span_cards', []) or [])
    )
    questions = [
        CaseBriefingEvidenceQuestion(
            question_ref='BQ_SUBJECT_RECALL',
            question_kind='subject_recall',
            question='Find the Bangumi subject(s) matching the local package title hypotheses.',
            local_refs=list(getattr(dossier.contract, 'main_file_refs', []) or [])[:8],
            query_hints=[item.title for item in titles[:6]],
            requested_request_types=['subject_search'],
            reason=reason,
        )
    ] if not has_target_surface else []
    package_shape = 'multi_work_unit' if len(work_units) > 1 else 'single_work_unit'
    return CaseBriefingOutput(
        package_shape=package_shape,
        work_units=work_units,
        title_hypotheses=titles[:12],
        split_hints=[],
        evidence_questions=questions,
        summary=reason,
    )


def _transport_available(ai_client: object) -> bool:
    return (
        hasattr(ai_client, 'call_case_briefing_agent')
        and callable(getattr(ai_client, 'call_case_briefing_agent'))
    ) or (
        hasattr(ai_client, '_call_with_schema')
        and callable(getattr(ai_client, '_call_with_schema'))
    ) or (
        hasattr(ai_client, 'call_with_schema')
        and callable(getattr(ai_client, 'call_with_schema'))
    ) or (
        hasattr(ai_client, '_call_openai_simple')
        and callable(getattr(ai_client, '_call_openai_simple'))
    )


def _call_ai_with_schema(ai_client: object, prompt: str) -> object:
    if hasattr(ai_client, 'call_case_briefing_agent') and callable(getattr(ai_client, 'call_case_briefing_agent')):
        return getattr(ai_client, 'call_case_briefing_agent')(prompt, CaseBriefingOutput)
    if hasattr(ai_client, '_call_with_schema') and callable(getattr(ai_client, '_call_with_schema')):
        return getattr(ai_client, '_call_with_schema')(prompt, schema=CaseBriefingOutput)
    if hasattr(ai_client, 'call_with_schema') and callable(getattr(ai_client, 'call_with_schema')):
        return getattr(ai_client, 'call_with_schema')(prompt, schema=CaseBriefingOutput)
    if hasattr(ai_client, '_call_openai_simple') and callable(getattr(ai_client, '_call_openai_simple')):
        return getattr(ai_client, '_call_openai_simple')(
            'You are a Local to Bangumi case briefing agent. Return strict JSON only.',
            prompt,
            validation_key='work_units',
            schema=CaseBriefingOutput,
            streaming=False,
        )
    raise AttributeError('ai_client does not provide a case briefing transport')


def _extract_response_content(response: object) -> object | None:
    if response is None:
        return None
    if isinstance(response, CaseBriefingOutput):
        return response
    if isinstance(response, dict):
        if 'content' in response:
            return response.get('content')
        return response
    return getattr(response, 'content', response)


def call_case_briefing_agent(ai_client: object, dossier: CaseDossier, *, max_provider_retries: int = 1) -> CaseBriefingCallResult:
    prompt = render_case_briefing_prompt(dossier)
    started = time.time()
    base_audit = {
        'planning_round_kind': 'case_briefing',
        'call_name': 'call_case_briefing_agent',
        'input_projection_bytes': len(prompt.encode('utf-8')),
        'rendered_prompt_bytes': len(prompt.encode('utf-8')),
        'request_body_bytes_estimate': len(prompt.encode('utf-8')) + 1024,
        'configured_interface': 'responses_api',
        'actual_interface': 'unavailable',
        'streaming': False,
    }
    if not _transport_available(ai_client):
        output = fallback_case_briefing(dossier, reason='case briefing transport unavailable')
        return CaseBriefingCallResult(
            ok=True,
            output=output,
            prompt=prompt,
            elapsed_ms=0,
            request_audit={**base_audit, 'actual_interface': 'fallback', 'fallback_used': True},
        )
    retry_audits: list[dict[str, object]] = []
    attempts = max(1, int(max_provider_retries or 0) + 1)
    last_response: object | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = _call_ai_with_schema(ai_client, prompt)
        except Exception as exc:
            output = fallback_case_briefing(dossier, reason=f'case briefing call failed: {exc}')
            return CaseBriefingCallResult(
                ok=True,
                output=output,
                prompt=prompt,
                error=str(exc),
                elapsed_ms=int((time.time() - started) * 1000),
                request_audit={**base_audit, 'actual_interface': 'fallback', 'fallback_used': True, 'error_kind': 'call_failed', 'error_message': str(exc), 'provider_retry_count': attempt - 1, 'provider_retry_audits': retry_audits},
            )
        last_response = response
        raw_response = _extract_response_content(response)
        if raw_response is None:
            retry_audits.append({'attempt': attempt, 'error_kind': 'provider_no_response'})
            continue
        if isinstance(raw_response, CaseBriefingOutput):
            output = raw_response
        else:
            try:
                output = CaseBriefingOutput.model_validate_json(raw_response) if isinstance(raw_response, str) else CaseBriefingOutput.model_validate(raw_response)
            except ValidationError as exc:
                fallback = fallback_case_briefing(dossier, reason=f'case briefing schema parse fallback: {exc}')
                return CaseBriefingCallResult(
                    ok=True,
                    output=fallback,
                    prompt=prompt,
                    error=str(exc),
                    raw_response=response,
                    elapsed_ms=int((time.time() - started) * 1000),
                    request_audit={**base_audit, 'actual_interface': 'fallback', 'fallback_used': True, 'error_kind': 'schema_parse_error', 'error_message': str(exc), 'provider_retry_count': attempt - 1, 'provider_retry_audits': retry_audits},
                )
        issues = validate_case_briefing_refs(output, dossier)
        if issues:
            fallback = fallback_case_briefing(dossier, reason='case briefing hidden refs rejected; fallback used')
            return CaseBriefingCallResult(
                ok=True,
                output=fallback,
                prompt=prompt,
                raw_response=response,
                elapsed_ms=int((time.time() - started) * 1000),
                request_audit={**base_audit, 'actual_interface': 'fallback', 'fallback_used': True, 'error_kind': 'hidden_ref', 'verifier_issue_codes': [issue.issue_code for issue in issues], 'provider_retry_count': attempt - 1, 'provider_retry_audits': retry_audits},
            )
        return CaseBriefingCallResult(
            ok=True,
            output=output,
            prompt=prompt,
            raw_response=response,
            elapsed_ms=int((time.time() - started) * 1000),
            request_audit={
                **base_audit,
                'actual_interface': 'responses_api',
                'work_unit_count': len(output.work_units or []),
                'title_hypothesis_count': len(output.title_hypotheses or []),
                'evidence_question_count': len(output.evidence_questions or []),
                'provider_retry_count': attempt - 1,
                'provider_retry_audits': retry_audits,
            },
        )
    output = fallback_case_briefing(dossier, reason='case briefing provider no response')
    return CaseBriefingCallResult(
        ok=True,
        output=output,
        prompt=prompt,
        raw_response=last_response,
        elapsed_ms=int((time.time() - started) * 1000),
        request_audit={**base_audit, 'actual_interface': 'fallback', 'fallback_used': True, 'error_kind': 'provider_no_response', 'provider_retry_count': max(0, attempts - 1), 'provider_retry_audits': retry_audits},
    )
