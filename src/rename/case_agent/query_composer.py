from __future__ import annotations

from dataclasses import dataclass
import json
from importlib import resources
import re
import time

from pydantic import ValidationError

from .dossier import build_bounded_case_dossier
from .models import CaseDossier, QueryCard, QueryComposerOutput


@dataclass
class QueryComposerCallResult:
    ok: bool
    output: QueryComposerOutput | None
    prompt: str
    query_cards: list[QueryCard]
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


def _sample(values: list[str], *, limit: int = 6) -> list[str]:
    values = [value for value in values if value]
    if len(values) <= limit:
        return values
    half = max(1, limit // 2)
    return list(dict.fromkeys([*values[:half], *values[-half:]]))


def _compact_query_card(card: QueryCard) -> dict[str, object]:
    return {
        'ref': card.ref,
        'query_text': card.query_text,
        'query_kind': card.query_kind,
        'query_origin': card.query_origin,
        'source_ref_count': len(card.source_refs),
        'source_ref_samples': _sample(list(card.source_refs or [])),
    }


def _compact_local_file_card(card: object) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    path = str(data.get('path', '') or '').replace('\\', '/')
    return {
        'ref': data.get('ref', ''),
        'basename': path.rsplit('/', 1)[-1],
        'path_tail': path.rsplit('/', 2)[-1] if '/' in path else path,
        'parent_display': data.get('parent_display', ''),
        'is_main': data.get('is_main', False),
        'label': data.get('label', ''),
        'file_kind': data.get('file_kind', ''),
    }


def _compact_local_cluster_card(card: object) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    return {
        'ref': data.get('ref', ''),
        'cluster_name': data.get('cluster_name', ''),
        'title_cues': list(data.get('title_cues') or [])[:8],
        'file_ref_count': len(list(data.get('file_refs') or [])),
        'file_ref_samples': _sample(list(data.get('file_refs') or [])),
        'summary': data.get('summary', ''),
    }


def _compact_local_span_card(card: object) -> dict[str, object]:
    data = card.model_dump(mode='json') if hasattr(card, 'model_dump') else dict(card)
    return {
        'ref': data.get('ref', ''),
        'span_scope': data.get('span_scope', ''),
        'parent_key': data.get('parent_key', ''),
        'season_cue': data.get('season_cue', ''),
        'file_ref_count': data.get('file_ref_count', 0),
        'file_ref_range': list(data.get('file_ref_range') or [])[:4],
        'file_ref_samples': _sample(list(data.get('file_ref_samples') or [])),
        'title_cues': list(data.get('title_cues') or [])[:8],
        'release_group_cues': list(data.get('release_group_cues') or [])[:8],
        'confidence_facts': list(data.get('confidence_facts') or [])[:8],
    }


def render_query_composer_prompt(
    dossier: CaseDossier,
    *,
    repair_issues: list[str] | None = None,
    previous_output: object | None = None,
) -> str:
    template = resources.files(__package__).joinpath('prompts/local_bangumi_query_composer.md').read_text(encoding='utf-8')
    bounded = build_bounded_case_dossier(dossier, query_sample_limit=32)
    payload = {
        'case_id': dossier.header.case_id,
        'counts': bounded.counts,
        'contract': {
            'main_file_refs': list(dossier.contract.main_file_refs),
            'supplemental_file_refs': list(dossier.contract.supplemental_file_refs),
        },
        'primary_title_cues': list(bounded.primary_title_cues),
        'release_group_cues': list(bounded.release_group_cues),
        'main_file_overview': bounded.main_file_overview,
        'local_files': [_compact_local_file_card(card) for card in list(dossier.local_files or [])[:40]],
        'local_clusters': [_compact_local_cluster_card(card) for card in list(dossier.local_clusters or [])[:24]],
        'local_span_cards': [_compact_local_span_card(card) for card in list(dossier.local_span_cards or [])[:24]],
        'raw_query_material': [_compact_query_card(card) for card in list(dossier.query_cards or []) if str(getattr(card, 'ref', '') or '').startswith('SQ')][:48],
        'visible_query_refs': list(dossier.visible_refs.query_refs),
        'existing_composed_queries': [_compact_query_card(card) for card in list(dossier.query_cards or []) if str(getattr(card, 'query_origin', '') or '') == 'agent_composed'],
    }
    if repair_issues:
        payload['repair_context'] = {
            'mode': 'repair_previous_invalid_output',
            'validation_issues': list(repair_issues),
            'previous_output': _jsonable(previous_output),
            'rule': 'Return complete replacement queries. Do not rely on fixed runtime cleanup; query_text must already be a clean work title or alternate-language title.',
        }
    return template.replace('{{DOSSIER_JSON}}', json.dumps(_jsonable(payload), ensure_ascii=False, indent=2))


def _extract_response_content(response: object) -> object | None:
    if response is None:
        return None
    if isinstance(response, QueryComposerOutput):
        return response
    if isinstance(response, dict):
        if 'content' in response:
            return response.get('content')
        return response
    return getattr(response, 'content', response)


def _composer_transport_available(ai_client: object) -> bool:
    return (
        hasattr(ai_client, 'call_query_composer')
        and callable(getattr(ai_client, 'call_query_composer'))
    ) or (
        hasattr(ai_client, '_call_openai_simple')
        and callable(getattr(ai_client, '_call_openai_simple'))
    ) or (
        hasattr(ai_client, '_call_with_schema')
        and callable(getattr(ai_client, '_call_with_schema'))
    ) or (
        hasattr(ai_client, 'call_with_schema')
        and callable(getattr(ai_client, 'call_with_schema'))
    )


def _call_ai_with_schema(ai_client: object, prompt: str) -> object:
    if hasattr(ai_client, 'call_query_composer') and callable(getattr(ai_client, 'call_query_composer')):
        return getattr(ai_client, 'call_query_composer')(prompt, QueryComposerOutput)
    if hasattr(ai_client, '_call_with_schema') and callable(getattr(ai_client, '_call_with_schema')):
        return getattr(ai_client, '_call_with_schema')(prompt, schema=QueryComposerOutput)
    if hasattr(ai_client, 'call_with_schema') and callable(getattr(ai_client, 'call_with_schema')):
        return getattr(ai_client, 'call_with_schema')(prompt, schema=QueryComposerOutput)
    if hasattr(ai_client, '_call_openai_simple') and callable(getattr(ai_client, '_call_openai_simple')):
        return getattr(ai_client, '_call_openai_simple')(
            'You are a Local to Bangumi query composer. Return strict JSON only.',
            prompt,
            validation_key='queries',
            schema=QueryComposerOutput,
            streaming=False,
        )
    raise AttributeError('ai_client does not provide a query composer transport')


def _next_query_index(existing: list[QueryCard]) -> int:
    max_index = 0
    for card in existing:
        ref = str(card.ref or '')
        if not ref.startswith('QC'):
            continue
        suffix = ref[2:]
        if suffix.isdigit():
            max_index = max(max_index, int(suffix))
    return max_index + 1


_BRACKETED_TEXT_RE = re.compile(r'[\[\(\uFF08\u3010\u300C\u300E]\s*([^\]\)\uFF09\u3011\u300D\u300F]{2,80}?)\s*[\]\)\uFF09\u3011\u300D\u300F]')
_TRAILING_YEAR_RE = re.compile(r'(?i)(?:[\s._-]+|\s*[\(\[\uFF08\u3010])((?:19|20)\d{2})(?:[\]\)\uFF09\u3011])?\s*$')
_TRAILING_SCOPE_RE = re.compile(
    r'(?i)(?:[\s._-]+(?:OAD|OAV|OVA|ONA|SP|S\d+|Season\s*\d+|\u7B2C\s*\d+\s*\u5B63)\s*\d*)\s*$'
)
_TECHNICAL_TEXT_RE = re.compile(
    r'(?i)(?:BDRip|Blu-?ray|WEB-?DL|HEVC|AVC|x26[45]|H\.?26[45]|1080p|720p|2160p|FLAC|AAC|Hi10P|Ma10p|YUV|CRC|\u5B57\u5E55|Sub)'
)
_CJK_TEXT_RE = re.compile(r'[\u3040-\u30ff\u3400-\u9fff]')


def _strip_search_scope_suffix(text: str) -> tuple[str, list[str]]:
    value = str(text or '').strip()
    removed: list[str] = []
    while value:
        changed = False
        year_match = _TRAILING_YEAR_RE.search(value)
        if year_match:
            before = value[:year_match.start()].strip(' ._-([{\uFF08\u3010')
            if before:
                removed.append(year_match.group(1))
                value = before
                changed = True
        scope_match = _TRAILING_SCOPE_RE.search(value)
        if scope_match:
            before = value[:scope_match.start()].strip(' ._-([{\uFF08\u3010')
            if before:
                removed.append(scope_match.group(0).strip(' ._-([{\uFF08\u3010)]}\uFF09\u3011'))
                value = before
                changed = True
        if not changed:
            break
    return value.strip(), [term for term in removed if term]


def _looks_like_metadata_only(text: str) -> bool:
    value = str(text or '').strip()
    if not value:
        return True
    if re.fullmatch(r'(?i)(?:OAD|OAV|OVA|ONA|SP|S\d+|Season\s*\d+|(?:19|20)\d{2})', value):
        return True
    if _TECHNICAL_TEXT_RE.search(value) and not _CJK_TEXT_RE.search(value):
        return True
    return False


def _title_search_variants(query_text: str) -> tuple[list[str], list[str]]:
    text = str(query_text or '').strip()
    if not text:
        return [], []

    raw_variants: list[str] = []
    bracket_values = [match.group(1).strip() for match in _BRACKETED_TEXT_RE.finditer(text)]
    outside = _BRACKETED_TEXT_RE.sub(' ', text)
    outside = re.sub(r'\s+', ' ', outside).strip()

    if outside:
        raw_variants.append(outside)
    for value in bracket_values:
        if value and not _looks_like_metadata_only(value):
            raw_variants.append(value)
    if not raw_variants:
        raw_variants.append(text)

    variants: list[str] = []
    removed_terms: list[str] = []
    seen: set[str] = set()
    for raw in raw_variants:
        normalized, removed = _strip_search_scope_suffix(raw)
        removed_terms.extend(removed)
        if _looks_like_metadata_only(normalized):
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        variants.append(normalized)
    return variants, list(dict.fromkeys(removed_terms))


def _query_contract_issue(query_text: str) -> str:
    text = re.sub(r'\s+', ' ', str(query_text or '').strip())
    if not text:
        return 'empty_query_text'
    bracket_values = [match.group(1).strip() for match in _BRACKETED_TEXT_RE.finditer(text)]
    outside = _BRACKETED_TEXT_RE.sub(' ', text)
    outside = re.sub(r'\s+', ' ', outside).strip()
    title_like_brackets = [value for value in bracket_values if value and not _looks_like_metadata_only(value)]
    if outside and title_like_brackets:
        return f'query_text_mixes_bracketed_title_variants:{text}'
    if re.fullmatch(r'(?i)(?:OAD|OAV|OVA|ONA|SP|S\d+|Season\s*\d+|(?:19|20)\d{2})', text):
        return f'metadata_only_query_text:{text}'
    normalized, removed = _strip_search_scope_suffix(text)
    if removed and normalized and normalized != text:
        return f'query_text_has_scope_or_year_suffix:{text}'
    if _TECHNICAL_TEXT_RE.search(text):
        return f'query_text_contains_technical_metadata:{text}'
    return ''


def _materialize_query_cards(output: QueryComposerOutput, dossier: CaseDossier) -> tuple[list[QueryCard], list[str]]:
    visible_refs = set(dossier.visible_refs.local_file_refs)
    visible_refs.update(dossier.visible_refs.local_cluster_refs)
    visible_refs.update(dossier.visible_refs.query_refs)
    visible_refs.update(card.ref for card in dossier.local_span_cards)
    existing_texts = {
        str(card.query_text or '').strip().casefold()
        for card in list(dossier.query_cards or [])
        if str(getattr(card, 'query_origin', '') or '') == 'agent_composed'
    }
    next_index = _next_query_index(list(dossier.query_cards or []))
    cards: list[QueryCard] = []
    dropped: list[str] = []
    seen_texts = set(existing_texts)
    for candidate in list(output.queries or []):
        raw_text = re.sub(r'\s+', ' ', str(candidate.query_text or '').strip())
        issue = _query_contract_issue(raw_text)
        if issue:
            dropped.append(issue)
            continue
        source_refs = [ref for ref in dict.fromkeys(str(ref or '') for ref in (candidate.source_refs or [])) if ref in visible_refs]
        if not source_refs:
            dropped.append(f'no_visible_source_refs:{raw_text}')
            continue
        text_key = raw_text.casefold()
        if text_key in seen_texts:
            dropped.append(f'duplicate_query_text:{raw_text}')
            continue
        ref = f'QC{next_index}'
        next_index += 1
        seen_texts.add(text_key)
        card = QueryCard(
            ref=ref,
            query_text=raw_text,
            query_kind='subject_search',
            query_origin='agent_composed',
            source_refs=source_refs,
            included_terms=list(candidate.included_terms or []),
            ignored_terms=list(candidate.ignored_terms or []),
            reason=candidate.reason,
            confidence=candidate.confidence,
        )
        cards.append(card)
    return cards, dropped


def _parse_query_composer_response(response: object) -> tuple[QueryComposerOutput | None, object | None, str]:
    raw_response = _extract_response_content(response)
    if raw_response is None:
        return None, raw_response, 'provider returned None'
    if isinstance(raw_response, QueryComposerOutput):
        return raw_response, raw_response, ''
    try:
        output = QueryComposerOutput.model_validate_json(raw_response) if isinstance(raw_response, str) else QueryComposerOutput.model_validate(raw_response)
        return output, raw_response, ''
    except ValidationError as exc:
        return None, raw_response, str(exc)


def _provider_retry_delay(attempt_index: int) -> None:
    time.sleep(min(0.2, 0.05 * max(1, attempt_index)))


def call_query_composer(ai_client: object, dossier: CaseDossier, *, max_repair_rounds: int = 1, max_provider_retries: int = 2) -> QueryComposerCallResult:
    prompt = render_query_composer_prompt(dossier)
    started = time.time()
    audit: dict[str, object] = {
        'round_kind': 'query_composer',
        'call_name': 'call_query_composer',
        'input_projection_bytes': len(prompt.encode('utf-8')),
        'rendered_prompt_bytes': len(prompt.encode('utf-8')),
        'request_body_bytes_estimate': len(prompt.encode('utf-8')) + 1024,
        'configured_interface': 'responses_api',
        'actual_interface': 'unavailable',
        'streaming': False,
    }
    if not _composer_transport_available(ai_client):
        output = QueryComposerOutput(queries=[], summary='query composer transport unavailable')
        return QueryComposerCallResult(
            ok=True,
            output=output,
            prompt=prompt,
            query_cards=[],
            elapsed_ms=0,
            request_audit={**audit, 'actual_interface': 'fallback', 'fallback_used': True, 'composed_query_count': 0},
        )
    retry_audits: list[dict[str, object]] = []
    attempts = max(1, int(max_provider_retries or 0) + 1)
    for attempt in range(1, attempts + 1):
        try:
            response = _call_ai_with_schema(ai_client, prompt)
        except Exception as exc:
            return QueryComposerCallResult(
                ok=False,
                output=None,
                prompt=prompt,
                query_cards=[],
                error=f'query composer call failed: {exc}',
                elapsed_ms=int((time.time() - started) * 1000),
                request_audit={**audit, 'error_kind': 'call_failed', 'error_message': str(exc), 'provider_retry_count': attempt - 1, 'provider_retry_audits': retry_audits},
            )
        output, raw_response, parse_error = _parse_query_composer_response(response)
        if output is None:
            error_kind = 'provider_no_response' if parse_error == 'provider returned None' else 'schema_parse_error'
            error_message = 'provider returned None' if parse_error == 'provider returned None' else parse_error
            if error_kind == 'provider_no_response':
                retry_audits.append({'attempt': attempt, 'error_kind': error_kind, 'error_message': error_message})
                if attempt < attempts:
                    _provider_retry_delay(attempt)
                    continue
                fallback_output = QueryComposerOutput(queries=[], summary='query composer provider no response; fallback to existing query hints')
                return QueryComposerCallResult(
                    ok=True,
                    output=fallback_output,
                    prompt=prompt,
                    query_cards=[],
                    error='',
                    raw_response=response,
                    elapsed_ms=int((time.time() - started) * 1000),
                    request_audit={
                        **audit,
                        'actual_interface': 'fallback',
                        'fallback_used': True,
                        'fallback_reason': 'provider_no_response',
                        'error_kind': 'provider_no_response',
                        'error_message': error_message,
                        'provider_retry_count': attempt - 1,
                        'provider_retry_audits': retry_audits,
                        'composed_query_count': 0,
                    },
                )
            return QueryComposerCallResult(
                ok=False,
                output=None,
                prompt=prompt,
                query_cards=[],
                error=f'query composer {error_kind}: {error_message}',
                raw_response=response,
                elapsed_ms=int((time.time() - started) * 1000),
                request_audit={**audit, 'error_kind': error_kind, 'error_message': error_message, 'provider_retry_count': attempt - 1, 'provider_retry_audits': retry_audits},
            )
        query_cards, dropped = _materialize_query_cards(output, dossier)
        repair_audits: list[dict[str, object]] = []
        if not query_cards and dropped and max_repair_rounds > 0:
            repair_prompt = render_query_composer_prompt(dossier, repair_issues=dropped[:8], previous_output=output)
            repair_response = _call_ai_with_schema(ai_client, repair_prompt)
            repair_output, repair_raw_response, repair_parse_error = _parse_query_composer_response(repair_response)
            repair_cards, repair_dropped = _materialize_query_cards(repair_output or QueryComposerOutput(), dossier)
            repair_audits.append({
                'round': 1,
                'validation_issues_in': dropped[:8],
                'candidate_query_count': len((repair_output or QueryComposerOutput()).queries or []),
                'composed_query_count': len(repair_cards),
                'dropped_query_reasons': repair_dropped[:8],
                'parse_error': repair_parse_error,
            })
            if repair_cards:
                return QueryComposerCallResult(
                    ok=True,
                    output=repair_output,
                    prompt=prompt,
                    query_cards=repair_cards,
                    raw_response=repair_raw_response,
                    elapsed_ms=int((time.time() - started) * 1000),
                    request_audit={
                        **audit,
                        'actual_interface': 'responses_api',
                        'repair_attempted': True,
                        'repair_succeeded': True,
                        'initial_candidate_query_count': len(output.queries or []),
                        'initial_dropped_query_reasons': dropped[:8],
                        'candidate_query_count': len((repair_output or QueryComposerOutput()).queries or []),
                        'composed_query_count': len(repair_cards),
                        'dropped_query_count': len(repair_dropped),
                        'dropped_query_reasons': repair_dropped[:8],
                        'repair_audits': repair_audits,
                        'provider_retry_count': attempt - 1,
                        'provider_retry_audits': retry_audits,
                    },
                )
            dropped = repair_dropped or dropped
        return QueryComposerCallResult(
            ok=True,
            output=output,
            prompt=prompt,
            query_cards=query_cards,
            raw_response=response,
            elapsed_ms=int((time.time() - started) * 1000),
            request_audit={
                **audit,
                'actual_interface': 'responses_api',
                'repair_attempted': bool(repair_audits),
                'repair_succeeded': False if repair_audits else None,
                'candidate_query_count': len(output.queries or []),
                'composed_query_count': len(query_cards),
                'dropped_query_count': len(dropped),
                'dropped_query_reasons': dropped[:8],
                'repair_audits': repair_audits,
                'provider_retry_count': attempt - 1,
                'provider_retry_audits': retry_audits,
            },
        )
    fallback_output = QueryComposerOutput(queries=[], summary='query composer provider no response; fallback to existing query hints')
    return QueryComposerCallResult(
        ok=True,
        output=fallback_output,
        prompt=prompt,
        query_cards=[],
        raw_response=None,
        elapsed_ms=int((time.time() - started) * 1000),
        request_audit={**audit, 'actual_interface': 'fallback', 'fallback_used': True, 'fallback_reason': 'provider_no_response', 'error_kind': 'provider_no_response', 'error_message': 'provider returned None', 'provider_retry_count': max(0, attempts - 1), 'provider_retry_audits': retry_audits, 'composed_query_count': 0},
    )
