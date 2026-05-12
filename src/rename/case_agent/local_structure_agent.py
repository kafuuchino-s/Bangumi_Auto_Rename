from __future__ import annotations

from dataclasses import dataclass
import json
from importlib import resources
import re
import time
from typing import Any

from pydantic import ValidationError

from .models import CaseDossier, LocalFileCard, LocalSpanCard, LocalStructureOutput, LocalStructureSpanSpec


@dataclass
class LocalStructureCallResult:
    ok: bool
    output: LocalStructureOutput | None
    prompt: str
    local_span_cards: list[LocalSpanCard]
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


def _compact_local_file(card: LocalFileCard) -> dict[str, object]:
    path = str(card.path or '').replace('\\', '/')
    return {
        'ref': card.ref,
        'path': path,
        'basename': path.rsplit('/', 1)[-1],
        'parent_display': card.parent_display,
        'label': card.label,
        'size_bytes': card.size_bytes,
        'file_kind': card.file_kind,
        'is_main': card.is_main,
    }


def _raw_structure_markers(card: LocalFileCard) -> list[str]:
    text = f'{getattr(card, "path", "") or ""} {getattr(card, "label", "") or ""} {getattr(card, "parent_display", "") or ""}'
    markers: list[str] = []
    for marker in ('OAD', 'OAV', 'OVA', 'ONA', 'SP'):
        if re.search(rf'(?i)(?<![A-Za-z0-9]){marker}\s*\d*(?![A-Za-z0-9])', text):
            markers.append(marker)
    if re.search(r'(?i)(?:tokubetsu|special|ova|oad|oav|ona|movie|劇場|特別|特別編)', text):
        markers.append('SPECIAL_TITLE')
    return list(dict.fromkeys(markers))


def _parent_display(card: LocalFileCard) -> str:
    parent = str(getattr(card, 'parent_display', '') or '').strip().replace('\\', '/')
    if parent:
        return parent
    path = str(getattr(card, 'path', '') or '').replace('\\', '/')
    if '/' not in path:
        return ''
    return path.rsplit('/', 1)[0]


def render_local_structure_prompt(
    dossier: CaseDossier,
    *,
    repair_issues: list[str] | None = None,
    previous_output: object | None = None,
) -> str:
    template = resources.files(__package__).joinpath('prompts/local_structure_agent.md').read_text(encoding='utf-8')
    main_refs = list(dossier.contract.main_file_refs or [])
    files_by_ref = {card.ref: card for card in list(dossier.local_files or []) if card.ref}
    ordered_files = [files_by_ref[ref] for ref in main_refs if ref in files_by_ref]
    payload = {
        'case_id': dossier.header.case_id,
        'contract': {
            'main_file_refs': main_refs,
            'supplemental_file_refs': list(dossier.contract.supplemental_file_refs or []),
            'allowed_file_refs': list(dossier.contract.allowed_file_refs or []),
        },
        'main_files': [_compact_local_file(card) for card in ordered_files],
        'raw_structure_marker_audit': [
            {
                'ref': card.ref,
                'markers': _raw_structure_markers(card),
            }
            for card in ordered_files
            if _raw_structure_markers(card)
        ],
        'local_clusters': [
            {
                'ref': card.ref,
                'cluster_name': card.cluster_name,
                'title_cues': _sample(list(card.title_cues or []), limit=8),
                'file_refs': list(card.file_refs or []),
            }
            for card in list(dossier.local_clusters or [])[:24]
        ],
    }
    if repair_issues:
        payload['repair_context'] = {
            'mode': 'repair_previous_invalid_output',
            'validation_issues': list(repair_issues),
            'previous_output': _jsonable(previous_output),
            'required_partition_refs': main_refs,
            'coverage_rule': 'Every ref in contract.main_file_refs must appear exactly once across non-package spans. LS_PACKAGE is overview-only and does not count toward coverage.',
        }
    return template.replace('{{DOSSIER_JSON}}', json.dumps(_jsonable(payload), ensure_ascii=False, indent=2))


def _transport_available(ai_client: object) -> bool:
    return (
        hasattr(ai_client, 'call_local_structure_agent')
        and callable(getattr(ai_client, 'call_local_structure_agent'))
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
    if hasattr(ai_client, 'call_local_structure_agent') and callable(getattr(ai_client, 'call_local_structure_agent')):
        return getattr(ai_client, 'call_local_structure_agent')(prompt, LocalStructureOutput)
    if hasattr(ai_client, '_call_with_schema') and callable(getattr(ai_client, '_call_with_schema')):
        return getattr(ai_client, '_call_with_schema')(prompt, schema=LocalStructureOutput)
    if hasattr(ai_client, 'call_with_schema') and callable(getattr(ai_client, 'call_with_schema')):
        return getattr(ai_client, 'call_with_schema')(prompt, schema=LocalStructureOutput)
    if hasattr(ai_client, '_call_openai_simple') and callable(getattr(ai_client, '_call_openai_simple')):
        return getattr(ai_client, '_call_openai_simple')(
            'You are a Local Structure Agent. Return strict JSON only.',
            prompt,
            validation_key='spans',
            schema=LocalStructureOutput,
            streaming=False,
        )
    raise AttributeError('ai_client does not provide a local structure transport')


def _extract_response_content(response: object) -> object | None:
    if response is None:
        return None
    if isinstance(response, LocalStructureOutput):
        return response
    if isinstance(response, dict):
        if 'content' in response:
            return response.get('content')
        return response
    return getattr(response, 'content', response)


def fallback_local_structure_spans(dossier: CaseDossier, *, reason: str = 'local structure agent unavailable') -> list[LocalSpanCard]:
    main_refs = list(dict.fromkeys(list(dossier.contract.main_file_refs or [])))
    if not main_refs:
        return []
    files_by_ref = {card.ref: card for card in list(dossier.local_files or []) if card.ref}
    title_cues = _sample([
        str(getattr(files_by_ref.get(ref), 'parent_display', '') or '')
        for ref in main_refs
        if files_by_ref.get(ref) is not None
    ], limit=4)
    package = LocalSpanCard(
        ref='LS_PACKAGE',
        span_scope='package',
        file_refs=main_refs,
        file_ref_count=len(main_refs),
        file_ref_range=[main_refs[0], main_refs[-1]],
        file_ref_samples=_sample(main_refs, limit=4),
        ordering_basis='path_order',
        title_cues=title_cues,
        confidence_facts=[reason],
    )
    child = LocalSpanCard(
        ref='LS1',
        span_scope='unpartitioned',
        file_refs=main_refs,
        file_ref_count=len(main_refs),
        file_ref_range=[main_refs[0], main_refs[-1]],
        file_ref_samples=_sample(main_refs, limit=4),
        ordering_basis='path_order',
        title_cues=title_cues,
        confidence_facts=[reason],
    )
    return [package, child]


def _normalize_span_ref(spec: LocalStructureSpanSpec, *, next_index: int) -> tuple[str, bool, list[str]]:
    raw_ref = str(spec.span_ref or '').strip()
    is_package_scope = spec.span_scope == 'package'
    issues: list[str] = []
    if raw_ref == 'LS_PACKAGE' and not is_package_scope:
        issues.append('ls_package_must_be_package')
        is_package_scope = True
    if is_package_scope:
        if raw_ref and raw_ref != 'LS_PACKAGE':
            issues.append(f'package_span_ref_must_be_ls_package:{raw_ref}')
        return 'LS_PACKAGE', True, issues
    if not raw_ref or not raw_ref.startswith('LS') or raw_ref == 'LS_PACKAGE':
        return f'LS{next_index}', False, issues
    return raw_ref, False, issues


def materialize_local_structure_spans(output: LocalStructureOutput, dossier: CaseDossier) -> tuple[list[LocalSpanCard], list[str]]:
    main_refs = list(dict.fromkeys(list(dossier.contract.main_file_refs or [])))
    main_ref_set = set(main_refs)
    allowed = set(dossier.visible_refs.local_file_refs or []) | set(dossier.contract.allowed_file_refs or [])
    issues: list[str] = []
    cards: list[LocalSpanCard] = []
    child_coverage: list[str] = []
    seen_span_refs: set[str] = set()
    next_index = 1

    for spec in list(output.spans or []):
        raw_file_refs = [str(ref or '') for ref in list(spec.file_refs or []) if str(ref or '')]
        file_refs = list(dict.fromkeys(raw_file_refs))
        duplicated_within_span = [ref for ref, count in _counts(raw_file_refs).items() if count > 1]
        span_ref, is_package_span, ref_issues = _normalize_span_ref(spec, next_index=next_index)
        issues.extend(ref_issues)
        if span_ref in seen_span_refs:
            issues.append(f'duplicate_span_ref:{span_ref}')
            continue
        if duplicated_within_span:
            issues.append(f'duplicate_file_ref_in_span:{span_ref}:{",".join(duplicated_within_span[:8])}')
            continue
        if any(ref not in allowed for ref in file_refs):
            issues.append(f'hidden_file_ref:{span_ref}')
            continue
        if any(ref not in main_ref_set for ref in file_refs):
            issues.append(f'non_main_file_ref:{span_ref}')
            continue
        if not file_refs:
            issues.append(f'empty_span:{span_ref}')
            continue
        if is_package_span:
            missing_package_refs = [ref for ref in main_refs if ref not in set(file_refs)]
            if missing_package_refs:
                issues.append(f'package_missing_main_refs:{",".join(missing_package_refs[:8])}')
                continue
        else:
            child_coverage.extend(file_refs)
            next_index += 1
        seen_span_refs.add(span_ref)
        ordinal_count = int(spec.ordinal_count or 0)
        ordinal_start = spec.ordinal_start
        ordinal_end = spec.ordinal_end
        cards.append(
            LocalSpanCard(
                ref=span_ref,
                span_scope=spec.span_scope,
                file_refs=file_refs,
                file_ref_count=len(file_refs),
                file_ref_range=[file_refs[0], file_refs[-1]],
                file_ref_samples=_sample(file_refs, limit=4),
                ordering_basis='episode_token_order' if spec.ordering_basis == 'filename_ordinal_order' else spec.ordering_basis,
                episode_token_start=ordinal_start,
                episode_token_end=ordinal_end,
                episode_token_count=ordinal_count,
                gap_count=0,
                duplicate_count=0,
                title_cues=list(spec.title_cues or [])[:8],
                release_group_cues=list(spec.release_group_cues or [])[:8],
                confidence_facts=[spec.reason] if spec.reason else [],
            )
        )

    if not any(card.ref == 'LS_PACKAGE' for card in cards) and main_refs:
        cards.insert(0, fallback_local_structure_spans(dossier, reason='agent omitted package span')[0])

    issues.extend(_structure_marker_partition_issues(cards, dossier))
    issues.extend(_parent_partition_issues(cards, dossier))

    child_refs = [ref for ref in child_coverage if ref in main_ref_set]
    duplicates = [ref for ref, count in _counts(child_refs).items() if count > 1]
    missing = [ref for ref in main_refs if ref not in set(child_refs)]
    if missing:
        issues.append(f'missing_main_refs:{",".join(missing[:8])}')
    if duplicates:
        issues.append(f'duplicate_main_refs:{",".join(duplicates[:8])}')
    if issues:
        return [], issues
    return cards, []


def _structure_marker_partition_issues(cards: list[LocalSpanCard], dossier: CaseDossier) -> list[str]:
    files_by_ref = {card.ref: card for card in list(dossier.local_files or []) if getattr(card, 'ref', '')}
    all_markers = {
        ref: set(_raw_structure_markers(files_by_ref[ref]))
        for ref in list(getattr(dossier.contract, 'main_file_refs', []) or [])
        if ref in files_by_ref
    }
    special_refs = {ref for ref, markers in all_markers.items() if markers & {'OAD', 'OAV', 'OVA', 'ONA', 'SP', 'SPECIAL_TITLE'}}
    ordinary_refs = {ref for ref, markers in all_markers.items() if not markers}
    if not special_refs or not ordinary_refs:
        return []
    issues: list[str] = []
    child_cards = [card for card in cards if str(getattr(card, 'span_scope', '') or '') != 'package']
    for card in child_cards:
        refs = set(getattr(card, 'file_refs', []) or [])
        mixed_special = sorted(refs & special_refs)
        mixed_ordinary = sorted(refs & ordinary_refs)
        if mixed_special and mixed_ordinary:
            issues.append(
                'mixed_raw_special_marker_and_other_refs_in_child_span:'
                f'{getattr(card, "ref", "")}:'
                f'special={",".join(mixed_special[:4])}:other={",".join(mixed_ordinary[:4])}'
            )
    return issues


def _parent_partition_issues(cards: list[LocalSpanCard], dossier: CaseDossier) -> list[str]:
    files_by_ref = {card.ref: card for card in list(dossier.local_files or []) if getattr(card, 'ref', '')}
    child_cards = [card for card in cards if str(getattr(card, 'span_scope', '') or '') != 'package']
    issues: list[str] = []
    for card in child_cards:
        if str(getattr(card, 'ordering_basis', '') or '') != 'episode_token_order':
            continue
        file_refs = [ref for ref in list(getattr(card, 'file_refs', []) or []) if ref in files_by_ref]
        if len(file_refs) < 6:
            continue
        parent_refs: dict[str, list[str]] = {}
        for ref in file_refs:
            parent = _parent_display(files_by_ref[ref])
            if not parent:
                continue
            parent_refs.setdefault(parent, []).append(ref)
        multi_file_parents = {
            parent: refs
            for parent, refs in parent_refs.items()
            if len(refs) >= 2
        }
        if len(multi_file_parents) < 2:
            continue
        parent_summary = ';'.join(
            f'{parent}={",".join(refs[:4])}'
            for parent, refs in list(multi_file_parents.items())[:4]
        )
        issues.append(
            'coarse_cross_parent_numbered_span:'
            f'{getattr(card, "ref", "")}:'
            f'{parent_summary}'
        )
    return issues


def _parse_output_response(response: object) -> tuple[LocalStructureOutput | None, object | None, str]:
    raw_response = _extract_response_content(response)
    if raw_response is None:
        return None, raw_response, 'provider returned None'
    if isinstance(raw_response, LocalStructureOutput):
        return raw_response, raw_response, ''
    try:
        output = LocalStructureOutput.model_validate_json(raw_response) if isinstance(raw_response, str) else LocalStructureOutput.model_validate(raw_response)
        return output, raw_response, ''
    except ValidationError as exc:
        return None, raw_response, str(exc)


def _compact_output_for_audit(output: LocalStructureOutput | None) -> dict[str, object]:
    if output is None:
        return {'span_count': 0, 'spans': []}
    spans = []
    for spec in list(output.spans or [])[:8]:
        spans.append({
            'span_ref': spec.span_ref,
            'span_scope': spec.span_scope,
            'file_ref_count': len(spec.file_refs or []),
            'file_ref_samples': _sample(list(spec.file_refs or []), limit=4),
            'ordinal_start': spec.ordinal_start,
            'ordinal_end': spec.ordinal_end,
            'ordinal_count': spec.ordinal_count,
            'ordering_basis': spec.ordering_basis,
        })
    return {'span_count': len(output.spans or []), 'spans': spans}


def _call_and_materialize(
    ai_client: object,
    dossier: CaseDossier,
    *,
    prompt: str,
) -> tuple[LocalStructureOutput | None, object | None, list[LocalSpanCard], list[str], str]:
    response = _call_ai_with_schema(ai_client, prompt)
    output, raw_response, parse_error = _parse_output_response(response)
    if output is None:
        return None, raw_response, [], [f'schema_parse_error:{parse_error}' if parse_error != 'provider returned None' else 'provider_no_response'], parse_error
    cards, issues = materialize_local_structure_spans(output, dossier)
    return output, raw_response, cards, issues, ''


def _counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def call_local_structure_agent(ai_client: object, dossier: CaseDossier, *, max_repair_rounds: int = 1) -> LocalStructureCallResult:
    prompt = render_local_structure_prompt(dossier)
    started = time.time()
    audit: dict[str, object] = {
        'round_kind': 'local_structure',
        'call_name': 'call_local_structure_agent',
        'input_projection_bytes': len(prompt.encode('utf-8')),
        'rendered_prompt_bytes': len(prompt.encode('utf-8')),
        'request_body_bytes_estimate': len(prompt.encode('utf-8')) + 1024,
        'configured_interface': 'responses_api',
        'actual_interface': 'unavailable',
        'streaming': False,
    }
    fallback = fallback_local_structure_spans(dossier)
    if not _transport_available(ai_client):
        output = LocalStructureOutput(spans=[], summary='local structure agent transport unavailable')
        return LocalStructureCallResult(
            ok=True,
            output=output,
            prompt=prompt,
            local_span_cards=fallback,
            elapsed_ms=0,
            request_audit={**audit, 'actual_interface': 'fallback', 'fallback_used': True, 'span_count': len(fallback)},
        )
    try:
        output, raw_response, cards, issues, parse_error = _call_and_materialize(ai_client, dossier, prompt=prompt)
        repair_audits: list[dict[str, object]] = []
        if issues and max_repair_rounds > 0 and output is not None:
            repair_prompt = render_local_structure_prompt(dossier, repair_issues=issues, previous_output=output)
            repair_output, repair_raw_response, repair_cards, repair_issues, repair_parse_error = _call_and_materialize(ai_client, dossier, prompt=repair_prompt)
            repair_audits.append({
                'round': 1,
                'validation_issues_in': issues[:8],
                'candidate': _compact_output_for_audit(repair_output),
                'validation_issues_out': repair_issues[:8],
                'parse_error': repair_parse_error,
            })
            if not repair_issues and repair_output is not None:
                return LocalStructureCallResult(
                    ok=True,
                    output=repair_output,
                    prompt=prompt,
                    local_span_cards=repair_cards,
                    raw_response=repair_raw_response,
                    elapsed_ms=int((time.time() - started) * 1000),
                    request_audit={
                        **audit,
                        'actual_interface': 'responses_api',
                        'repair_attempted': True,
                        'repair_succeeded': True,
                        'initial_validation_issues': issues[:8],
                        'initial_candidate': _compact_output_for_audit(output),
                        'repair_audits': repair_audits,
                        'candidate_span_count': len(repair_output.spans or []),
                        'span_count': len(repair_cards),
                    },
                )
            output = repair_output or output
            raw_response = repair_raw_response if repair_raw_response is not None else raw_response
            issues = repair_issues or issues
            parse_error = repair_parse_error or parse_error

        if issues:
            error_kind = 'schema_parse_error' if any(str(issue).startswith('schema_parse_error:') for issue in issues) else 'validation_error'
            if any(str(issue) == 'provider_no_response' for issue in issues):
                error_kind = 'provider_no_response'
            return LocalStructureCallResult(
                ok=True,
                output=output or LocalStructureOutput(spans=[], summary='local structure output invalid'),
                prompt=prompt,
                local_span_cards=fallback,
                error=';'.join(issues),
                raw_response=raw_response,
                elapsed_ms=int((time.time() - started) * 1000),
                request_audit={
                    **audit,
                    'actual_interface': 'fallback',
                    'fallback_used': True,
                    'error_kind': error_kind,
                    'error_message': parse_error,
                    'repair_attempted': bool(repair_audits),
                    'repair_succeeded': False,
                    'initial_candidate': _compact_output_for_audit(output),
                    'repair_audits': repair_audits,
                    'validation_issues': issues[:8],
                    'span_count': len(fallback),
                },
            )
        return LocalStructureCallResult(
            ok=True,
            output=output or LocalStructureOutput(spans=[], summary='local structure output empty'),
            prompt=prompt,
            local_span_cards=cards,
            raw_response=raw_response,
            elapsed_ms=int((time.time() - started) * 1000),
            request_audit={**audit, 'actual_interface': 'responses_api', 'candidate_span_count': len((output or LocalStructureOutput()).spans or []), 'span_count': len(cards)},
        )
    except Exception as exc:
        return LocalStructureCallResult(
            ok=True,
            output=LocalStructureOutput(spans=[], summary='local structure call failed'),
            prompt=prompt,
            local_span_cards=fallback,
            error=f'local structure call failed: {exc}',
            elapsed_ms=int((time.time() - started) * 1000),
            request_audit={**audit, 'actual_interface': 'fallback', 'fallback_used': True, 'error_kind': 'call_failed', 'error_message': str(exc), 'span_count': len(fallback)},
        )
