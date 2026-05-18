from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from importlib import resources
import time
from typing import Any

from pydantic import ValidationError

from .dossier import build_bounded_case_dossier
from .notebook import compact_case_briefing, compact_investigation_notebook, filter_case_briefing_for_child, filter_investigation_notebook_for_child
from .models import (
    BangumiGroupCard,
    BangumiItemCard,
    BangumiRelationCard,
    BangumiSubjectCard,
    CaseDossier,
    CasePlanningOutput,
    CaseVerifierResult,
    EvidenceRequest,
    LocalClusterCard,
    LocalFileCard,
    LocalSpanCard,
    ProvenanceCard,
    QueryCard,
    SplitCaseSpec,
    VerifierIssue,
)
from .workspace import CaseEvidenceWorkspace


@dataclass
class CasePlanningCallResult:
    ok: bool
    output: CasePlanningOutput | None
    prompt: str
    error: str = ''
    raw_response: object | None = None
    elapsed_ms: int = 0
    request_audit: dict[str, object] | None = None


def render_case_planner_prompt(dossier: CaseDossier) -> str:
    template = resources.files(__package__).joinpath('prompts/local_bangumi_case_planner.md').read_text(encoding='utf-8')
    bounded = build_bounded_case_dossier(dossier)
    payload = bounded.model_dump(mode='json') if hasattr(bounded, 'model_dump') else bounded
    payload['case_briefing'] = compact_case_briefing(getattr(dossier, 'case_briefing', None))
    payload['investigation_notebook'] = compact_investigation_notebook(getattr(dossier, 'investigation_notebook', None))
    return template.replace('{{DOSSIER_JSON}}', json.dumps(payload, ensure_ascii=False, indent=2))


def _extract_response_content(response: object) -> object | None:
    if response is None:
        return None
    if isinstance(response, CasePlanningOutput):
        return response
    if isinstance(response, dict):
        if 'content' in response:
            return response.get('content')
        return response
    return getattr(response, 'content', response)


def _planner_transport_available(ai_client: object) -> bool:
    return (
        hasattr(ai_client, 'call_case_planner')
        and callable(getattr(ai_client, 'call_case_planner'))
    ) or (
        hasattr(ai_client, '_call_openai_simple')
        and callable(getattr(ai_client, '_call_openai_simple'))
    )


def _call_ai_with_schema(ai_client: object, prompt: str) -> object:
    if hasattr(ai_client, 'call_case_planner') and callable(getattr(ai_client, 'call_case_planner')):
        return getattr(ai_client, 'call_case_planner')(prompt, CasePlanningOutput)
    if hasattr(ai_client, '_call_openai_simple') and callable(getattr(ai_client, '_call_openai_simple')):
        system_prompt = 'You are a Local to Bangumi case planner. Return strict JSON only.'
        return getattr(ai_client, '_call_openai_simple')(
            system_prompt,
            prompt,
            validation_key='action',
            schema=CasePlanningOutput,
            streaming=False,
        )
    raise AttributeError('ai_client does not provide a case planner transport')


def _provider_retry_delay(attempt_index: int) -> None:
    time.sleep(min(0.2, 0.05 * max(1, attempt_index)))


def call_case_planner(ai_client: object, dossier: CaseDossier, *, max_provider_retries: int = 2) -> CasePlanningCallResult:
    prompt = render_case_planner_prompt(dossier)
    started = time.time()
    audit: dict[str, object] = {
        'planning_round_kind': 'case_planning',
        'call_name': 'call_case_planner',
        'action_expected': 'process_as_one_case_or_split_into_cases_or_request_evidence_or_fail_closed',
        'input_projection_bytes': len(prompt.encode('utf-8')),
        'rendered_prompt_bytes': len(prompt.encode('utf-8')),
        'request_body_bytes_estimate': len(prompt.encode('utf-8')) + 1024,
        'configured_interface': 'responses_api',
        'actual_interface': 'unavailable',
        'streaming': False,
    }
    if not _planner_transport_available(ai_client):
        return CasePlanningCallResult(
            ok=True,
            output=CasePlanningOutput(action='process_as_one_case', summary='planner transport unavailable; process as one case'),
            prompt=prompt,
            elapsed_ms=0,
            request_audit={**audit, 'actual_interface': 'fallback', 'fallback_used': True},
        )
    retry_audits: list[dict[str, object]] = []
    attempts = max(1, int(max_provider_retries or 0) + 1)
    last_response: object | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = _call_ai_with_schema(ai_client, prompt)
        except Exception as exc:
            return CasePlanningCallResult(
                ok=False,
                output=None,
                prompt=prompt,
                error=f'case planner call failed: {exc}',
                elapsed_ms=int((time.time() - started) * 1000),
                request_audit={**audit, 'error_kind': 'call_failed', 'error_message': str(exc), 'provider_retry_count': attempt - 1, 'provider_retry_audits': retry_audits},
            )
        last_response = response
        raw_response = _extract_response_content(response)
        if raw_response is None:
            retry_audits.append({'attempt': attempt, 'error_kind': 'provider_no_response', 'error_message': 'provider returned None'})
            if attempt < attempts:
                _provider_retry_delay(attempt)
                continue
            return CasePlanningCallResult(
                ok=False,
                output=None,
                prompt=prompt,
                error='case planner no response: provider returned None',
                raw_response=response,
                elapsed_ms=int((time.time() - started) * 1000),
                request_audit={**audit, 'error_kind': 'provider_no_response', 'error_message': 'provider returned None', 'provider_retry_count': attempt - 1, 'provider_retry_audits': retry_audits},
            )
        if isinstance(raw_response, CasePlanningOutput):
            output = raw_response
        else:
            try:
                output = CasePlanningOutput.model_validate_json(raw_response) if isinstance(raw_response, str) else CasePlanningOutput.model_validate(raw_response)
            except ValidationError as exc:
                return CasePlanningCallResult(
                    ok=False,
                    output=None,
                    prompt=prompt,
                    error=f'case planner schema parse error: {exc}',
                    raw_response=response,
                    elapsed_ms=int((time.time() - started) * 1000),
                    request_audit={**audit, 'error_kind': 'schema_parse_error', 'error_message': str(exc), 'provider_retry_count': attempt - 1, 'provider_retry_audits': retry_audits},
                )
        return CasePlanningCallResult(
            ok=True,
            output=output,
            prompt=prompt,
            raw_response=response,
            elapsed_ms=int((time.time() - started) * 1000),
            request_audit={
                **audit,
                'actual_interface': 'responses_api',
                'action_actual': output.action,
                'split_case_count': len(output.split_cases),
                'evidence_request_count': len(output.evidence_requests),
                'evidence_menu_request_count': len(output.evidence_menu_request_ids),
                'provider_retry_count': attempt - 1,
                'provider_retry_audits': retry_audits,
            },
        )
    return CasePlanningCallResult(
        ok=False,
        output=None,
        prompt=prompt,
        error='case planner no response: provider returned None',
        raw_response=last_response,
        elapsed_ms=int((time.time() - started) * 1000),
        request_audit={**audit, 'error_kind': 'provider_no_response', 'error_message': 'provider returned None', 'provider_retry_count': max(0, attempts - 1), 'provider_retry_audits': retry_audits},
    )


def verify_case_planning_output(dossier: CaseDossier, output: CasePlanningOutput) -> CaseVerifierResult:
    issues: list[VerifierIssue] = []
    visible_refs = _visible_ref_set(dossier)
    main_refs = set(dossier.contract.main_file_refs)
    supplemental_refs = set(dossier.contract.supplemental_file_refs)
    allowed_file_refs = set(dossier.contract.allowed_file_refs) | main_refs | supplemental_refs

    if output.action == 'process_as_one_case':
        if output.split_cases:
            issues.append(_issue('case_planning', 'action_inconsistent', 'process_as_one_case must not include split_cases'))
    elif output.action == 'split_into_cases':
        _verify_split_cases(issues, output.split_cases, main_refs=main_refs, supplemental_refs=supplemental_refs, allowed_file_refs=allowed_file_refs, visible_refs=visible_refs)
    elif output.action == 'request_evidence':
        if not output.evidence_requests and not output.evidence_menu_request_ids:
            issues.append(_issue('case_planning', 'action_inconsistent', 'request_evidence requires evidence requests or menu ids'))
    elif output.action == 'fail_closed':
        if not output.fail_closed_reasons:
            issues.append(_issue('case_planning', 'action_inconsistent', 'fail_closed requires fail_closed_reasons'))

    for request in output.evidence_requests:
        _verify_evidence_request_refs(issues, request, dossier)

    for reason in output.fail_closed_reasons:
        if any(ref and ref not in visible_refs and ref not in {r.ref for r in output.fail_closed_reasons} for ref in reason.related_refs):
            issues.append(_issue(reason.ref or 'fail_closed', 'unknown_ref', 'fail_closed related_refs must be visible planner refs'))

    blocking = [issue for issue in issues if issue.severity == 'blocked']
    return CaseVerifierResult(passed=not blocking, issues=issues, summary='case planning verified' if not blocking else 'case planning rejected')


def build_child_workspace(parent: CaseEvidenceWorkspace, spec: SplitCaseSpec) -> CaseEvidenceWorkspace:
    main_refs = set(spec.main_file_refs)
    supplemental_refs = set(spec.supplemental_file_refs)
    support_refs = set(spec.support_refs)
    parent_local_span_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in parent.local_span_cards
        if str(getattr(card, 'ref', '') or '')
    }
    child_support_refs = {ref for ref in support_refs if ref not in parent_local_span_refs}
    child_local_refs = main_refs | supplemental_refs | {ref for ref in support_refs if parent.get_ref_kind(ref) == 'local_file'}

    local_files = [
        _copy_local_file(card, is_child_main=card.ref in main_refs)
        for card in parent.local_files
        if card.ref in child_local_refs
    ]
    local_file_ref_set = {card.ref for card in local_files}
    local_clusters = [
        _copy_local_cluster(card, local_file_ref_set)
        for card in parent.local_clusters
        if card.ref in child_support_refs or any(ref in local_file_ref_set for ref in card.file_refs)
    ]
    local_cluster_refs = {card.ref for card in local_clusters}
    bangumi_subjects = [card for card in parent.bangumi_subjects if card.ref in child_support_refs]
    bangumi_relations = [card for card in parent.bangumi_relations if card.ref in child_support_refs]
    bangumi_groups = [_copy_bangumi_group(card, child_support_refs) for card in parent.bangumi_groups if card.ref in child_support_refs]
    bangumi_items = [card for card in parent.bangumi_items if card.ref in child_support_refs]
    included_bangumi_refs = {card.ref for card in bangumi_subjects} | {card.ref for card in bangumi_relations} | {card.ref for card in bangumi_groups} | {card.ref for card in bangumi_items}
    query_cards = [
        _copy_query_card(card, source_refs=local_file_ref_set | local_cluster_refs | child_support_refs, result_refs=included_bangumi_refs)
        for card in parent.query_cards
        if card.ref in child_support_refs or any(ref in (local_file_ref_set | local_cluster_refs | child_support_refs) for ref in card.source_refs)
    ]
    provenance_cards = [card for card in parent.provenance_cards if card.ref in child_support_refs]
    local_span_cards: list[LocalSpanCard] = []
    allowed_child_refs = {
        *child_local_refs,
        *local_cluster_refs,
        *included_bangumi_refs,
        *{card.ref for card in query_cards if getattr(card, 'ref', '')},
        *{card.ref for card in provenance_cards if getattr(card, 'ref', '')},
        *{card.ref for card in local_span_cards if getattr(card, 'ref', '')},
    }
    child_briefing = filter_case_briefing_for_child(getattr(parent, 'case_briefing', None), allowed_refs=allowed_child_refs)
    child_notebook = filter_investigation_notebook_for_child(getattr(parent, 'investigation_notebook', None), allowed_refs=allowed_child_refs)

    header = parent.header.model_copy(update={'case_id': f'{parent.header.case_id}:{spec.child_case_ref}', 'round_index': 0, 'issue_response_used': 0})
    contract = parent.contract.model_copy(update={
        'main_file_refs': list(spec.main_file_refs),
        'supplemental_file_refs': list(spec.supplemental_file_refs),
        'allowed_file_refs': list(dict.fromkeys([*spec.main_file_refs, *spec.supplemental_file_refs])),
        'visible_target_refs': [card.ref for card in bangumi_items],
        'summary': f'Child split case {spec.child_case_ref}: {spec.reason}',
    })
    child = CaseEvidenceWorkspace.from_cards(
        header=header,
        budget=parent.budget.model_copy(deep=True),
        contract=contract,
        local_files=local_files,
        local_clusters=local_clusters,
        local_span_cards=local_span_cards,
        bangumi_subjects=bangumi_subjects,
        bangumi_relations=bangumi_relations,
        bangumi_groups=bangumi_groups,
        bangumi_items=bangumi_items,
        query_cards=query_cards,
        provenance_cards=provenance_cards,
        case_briefing=child_briefing,
        investigation_notebook=child_notebook,
        diagnostics=[*parent.diagnostics, 'derived_from_case_planning_split'],
    )
    object.__setattr__(child, 'seen_detail_refs', [ref for ref in parent.seen_detail_refs if ref in child.all_visible_ref_set()])
    object.__setattr__(child, 'judge_request_audits', [
        {
            'round_kind': 'case_planning_child',
            'child_case_ref': spec.child_case_ref,
            'main_file_refs': list(spec.main_file_refs),
            'supplemental_file_refs': list(spec.supplemental_file_refs),
            'support_refs': list(spec.support_refs),
            'title_hints': list(spec.title_hints),
            'query_hints': list(spec.query_hints),
            'reason': spec.reason,
        }
    ])
    return child


def _verify_split_cases(
    issues: list[VerifierIssue],
    split_cases: list[SplitCaseSpec],
    *,
    main_refs: set[str],
    supplemental_refs: set[str],
    allowed_file_refs: set[str],
    visible_refs: set[str],
) -> None:
    if not split_cases:
        issues.append(_issue('split_cases', 'split_empty', 'split_into_cases requires at least one child case'))
        return
    child_refs = [case.child_case_ref for case in split_cases]
    if any(not ref for ref in child_refs) or len(set(child_refs)) != len(child_refs):
        issues.append(_issue('split_cases', 'split_child_ref_invalid', 'child_case_ref must be non-empty and unique'))

    main_counts: Counter[str] = Counter()
    supplemental_counts: Counter[str] = Counter()
    for case in split_cases:
        if not case.main_file_refs:
            issues.append(_issue(case.child_case_ref or 'split_case', 'split_child_empty', 'child case must contain at least one main file'))
        hidden_main = [ref for ref in case.main_file_refs if ref not in main_refs]
        if hidden_main:
            issues.append(_issue(case.child_case_ref or 'split_case', 'split_hidden_main_ref', 'child main_file_refs must come from contract.main_file_refs'))
        hidden_supplemental = [ref for ref in case.supplemental_file_refs if ref not in supplemental_refs and ref not in allowed_file_refs]
        if hidden_supplemental:
            issues.append(_issue(case.child_case_ref or 'split_case', 'split_hidden_supplemental_ref', 'child supplemental refs must be known allowed file refs'))
        if any(ref in main_refs for ref in case.supplemental_file_refs):
            issues.append(_issue(case.child_case_ref or 'split_case', 'split_main_as_supplemental', 'main refs must not be listed as supplemental refs'))
        unknown_support = [ref for ref in case.support_refs if ref and ref not in visible_refs]
        if unknown_support:
            issues.append(_issue(case.child_case_ref or 'split_case', 'split_unknown_support_ref', 'support refs must be visible dossier refs'))
        main_counts.update(case.main_file_refs)
        supplemental_counts.update(case.supplemental_file_refs)

    if set(main_counts) != main_refs:
        missing = sorted(main_refs - set(main_counts))
        extra = sorted(set(main_counts) - main_refs)
        if missing:
            issues.append(_issue('split_cases', 'split_missing_main_ref', f'missing main refs: {", ".join(missing[:8])}'))
        if extra:
            issues.append(_issue('split_cases', 'split_hidden_main_ref', f'extra main refs: {", ".join(extra[:8])}'))
    duplicate_main = [ref for ref, count in main_counts.items() if count > 1]
    if duplicate_main:
        issues.append(_issue('split_cases', 'split_duplicate_main_ref', f'duplicate main refs: {", ".join(sorted(duplicate_main)[:8])}'))
    duplicate_supplemental = [ref for ref, count in supplemental_counts.items() if count > 1]
    if duplicate_supplemental:
        issues.append(_issue('split_cases', 'split_duplicate_supplemental_ref', f'duplicate supplemental refs: {", ".join(sorted(duplicate_supplemental)[:8])}'))


def _verify_evidence_request_refs(issues: list[VerifierIssue], request: EvidenceRequest, dossier: CaseDossier) -> None:
    local_file_refs = set(dossier.visible_refs.local_file_refs)
    local_span_refs = {card.ref for card in dossier.local_span_cards}
    subject_refs = set(dossier.visible_refs.bangumi_subject_refs)
    item_refs = set(dossier.visible_refs.bangumi_item_refs) | set(dossier.visible_refs.target_refs)
    group_refs = set(dossier.visible_refs.bangumi_group_refs)
    query_refs = set(dossier.visible_refs.query_refs)
    if any(ref not in local_file_refs for ref in request.anchor_file_refs):
        issues.append(_issue(request.request_ref or 'request', 'unknown_ref', 'anchor_file_refs must be visible local file refs'))
    if request.local_span_ref and request.local_span_ref not in local_span_refs:
        issues.append(_issue(request.request_ref or 'request', 'unknown_ref', 'local_span_ref must be visible local span ref'))
    if any(ref not in subject_refs for ref in request.subject_refs):
        issues.append(_issue(request.request_ref or 'request', 'unknown_ref', 'subject_refs must be visible Bangumi subject refs'))
    if any(ref not in item_refs for ref in request.item_refs):
        issues.append(_issue(request.request_ref or 'request', 'unknown_ref', 'item_refs must be visible Bangumi item refs'))
    if any(ref not in group_refs for ref in request.group_refs):
        issues.append(_issue(request.request_ref or 'request', 'unknown_ref', 'group_refs must be visible Bangumi group refs'))
    if any(ref not in query_refs for ref in request.query_refs):
        issues.append(_issue(request.request_ref or 'request', 'unknown_ref', 'query_refs must be visible query refs'))


def _visible_ref_set(dossier: CaseDossier) -> set[str]:
    return {
        *dossier.visible_refs.local_file_refs,
        *dossier.visible_refs.local_cluster_refs,
        *dossier.visible_refs.bangumi_subject_refs,
        *dossier.visible_refs.bangumi_relation_refs,
        *dossier.visible_refs.bangumi_group_refs,
        *dossier.visible_refs.bangumi_item_refs,
        *dossier.visible_refs.query_refs,
        *dossier.visible_refs.target_refs,
        *[card.ref for card in dossier.local_span_cards],
        *[card.ref for card in dossier.bangumi_span_cards],
        *[card.ref for card in dossier.provenance_cards],
    }


def _issue(ref: str, issue_code: str, message: str) -> VerifierIssue:
    return VerifierIssue(ref=ref, issue_code=issue_code, severity='blocked', message=message)


def _copy_local_file(card: LocalFileCard, *, is_child_main: bool) -> LocalFileCard:
    return card.model_copy(update={'is_main': is_child_main})


def _copy_local_cluster(card: LocalClusterCard, child_file_refs: set[str]) -> LocalClusterCard:
    return card.model_copy(update={'file_refs': [ref for ref in card.file_refs if ref in child_file_refs]})


def _copy_bangumi_group(card: BangumiGroupCard, support_refs: set[str]) -> BangumiGroupCard:
    return card.model_copy(update={
        'member_refs_visible': [ref for ref in card.member_refs_visible if ref in support_refs],
        'subject_refs': [ref for ref in card.subject_refs if ref in support_refs],
        'item_refs': [ref for ref in card.item_refs if ref in support_refs],
    })


def _copy_query_card(card: QueryCard, *, source_refs: set[str], result_refs: set[str]) -> QueryCard:
    return card.model_copy(update={
        'source_refs': [ref for ref in card.source_refs if ref in source_refs],
        'result_refs': [ref for ref in card.result_refs if ref in result_refs],
    })


def _copy_local_span(card: LocalSpanCard, child_file_refs: set[str]) -> LocalSpanCard:
    file_refs = [ref for ref in card.file_refs if ref in child_file_refs]
    samples = [ref for ref in card.file_ref_samples if ref in child_file_refs]
    range_refs = [ref for ref in card.file_ref_range if ref in child_file_refs]
    return card.model_copy(update={
        'file_refs': file_refs,
        'file_ref_count': len(file_refs) if file_refs else len(samples),
        'file_ref_samples': samples,
        'file_ref_range': range_refs,
        'attention_file_refs': [ref for ref in card.attention_file_refs if ref in child_file_refs],
    })


def _span_mentions_any(card: LocalSpanCard, refs: set[str]) -> bool:
    values = [*card.file_refs, *card.file_ref_samples, *card.file_ref_range, *card.attention_file_refs]
    return any(ref in refs for ref in values)
