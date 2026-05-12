from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
import time

from pydantic import ValidationError

from .models import CaseDossier, CaseJudgeOutput
from .prompting import render_local_bangumi_judge_prompt
from .verifier import _output_budget_issues


@dataclass
class CaseJudgeTransportInfo:
    configured_interface: str = ''
    actual_interface: str = ''
    planned_streaming: bool | None = None
    actual_streaming: bool | None = None


@dataclass
class CaseJudgeCallResult:
    ok: bool
    output: CaseJudgeOutput | None
    prompt: str
    error: str
    raw_response: object | None = None
    configured_interface: str = 'call_case_judge'
    actual_interface: str = 'call_case_judge'
    streaming: bool | None = False
    elapsed_ms: int = 0
    request_audit: dict[str, Any] | None = None


def _extract_response_content(response: object) -> object | None:
    if response is None:
        return None
    if isinstance(response, CaseJudgeOutput):
        return response
    if isinstance(response, dict):
        if 'content' in response:
            return response.get('content')
        return response
    return getattr(response, 'content', response)


def _call_ai_with_schema(ai_client: object, prompt: str, schema: type[CaseJudgeOutput]) -> object:
    if hasattr(ai_client, '_call_with_schema') and callable(getattr(ai_client, '_call_with_schema')):
        return getattr(ai_client, '_call_with_schema')(prompt, schema=schema)
    if hasattr(ai_client, 'call_with_schema') and callable(getattr(ai_client, 'call_with_schema')):
        return getattr(ai_client, 'call_with_schema')(prompt, schema=schema)
    if hasattr(ai_client, 'call_case_judge') and callable(getattr(ai_client, 'call_case_judge')):
        return getattr(ai_client, 'call_case_judge')(prompt, schema)
    if hasattr(ai_client, '_call_openai_simple') and callable(getattr(ai_client, '_call_openai_simple')):
        system_prompt = 'You are a Local→Bangumi Case Judge. Return strict JSON only.'
        return getattr(ai_client, '_call_openai_simple')(
            system_prompt,
            prompt,
            validation_key='action',
            schema=schema,
            streaming=False,
        )
    raise AttributeError('ai_client does not provide a schema-aware judge call method')


def _output_budget_audit(output: CaseJudgeOutput) -> dict[str, object]:
    from .models import iter_case_judge_ref_lists

    output_dump = output.model_dump(mode='json')
    ref_lists = [list(refs or []) for _, refs in iter_case_judge_ref_lists(output)]
    max_len = max((len(v) for v in ref_lists), default=0)
    total_count = sum(len(v) for v in ref_lists)
    issues = _output_budget_issues(output)
    oversized_reasons = [issue.message for issue in issues if issue.issue_code == 'output_budget_exceeded']
    return {
        'output_bytes_estimate': len(json.dumps(output_dump, ensure_ascii=False).encode('utf-8')),
        'output_ref_list_max_length': max_len,
        'output_ref_total_count': total_count,
        'oversized_output': bool(oversized_reasons),
        'oversized_output_reason': '; '.join(dict.fromkeys(oversized_reasons)),
    }


def call_case_judge(ai_client: object, dossier: CaseDossier, *, round_kind: str = 'judge') -> CaseJudgeCallResult:
    prompt = render_local_bangumi_judge_prompt(dossier, round_kind=round_kind)
    started = time.time()
    projection_kind = 'initial_projection' if round_kind == 'initial' else ('evidence_rejudge_projection' if round_kind == 'evidence_rejudge' else 'issue_response_projection')
    final_round = bool(getattr(dossier.header, 'max_rounds', 0)) and getattr(dossier.header, 'round_index', 0) >= max(0, getattr(dossier.header, 'max_rounds', 0) - 1)
    audit = {
        'round_kind': round_kind,
        'context_kind': projection_kind,
        'action_expected': 'request_evidence' if round_kind == 'initial' and not final_round else ('submit_verdict_or_fail_closed_only' if final_round else ('submit_verdict_or_fail_closed' if round_kind == 'issue_response' else 'submit_verdict_or_fail_closed_or_request_evidence')),
        'input_projection_bytes': len(prompt.encode('utf-8')),
        'rendered_prompt_bytes': len(prompt.encode('utf-8')),
        'request_body_bytes_estimate': len(prompt.encode('utf-8')) + 1024,
        'prompt_be_ref_occurrences': prompt.count('BE'),
        'prompt_file_ref_occurrences': prompt.count('LF'),
        'detailed_visible_card_count': len(getattr(dossier, 'detailed_card_refs', []) or []),
        'assignable_target_count': len(getattr(dossier, 'assignable_target_refs', []) or []),
        'seen_detail_ref_count': len(getattr(dossier, 'seen_detail_refs', []) or []),
        'evidence_request_count': len(getattr(dossier, 'previous_evidence_results', []) or []),
        'evidence_request_types': [getattr(rr, 'request_type', '') for batch in (getattr(dossier, 'previous_evidence_results', []) or []) for rr in (getattr(batch, 'request_results', []) or []) if getattr(rr, 'request_type', '')],
        'cache_mode': 'planned',
        'cache_key': f'case_judge:{getattr(dossier.header, "case_id", "")}:r{getattr(dossier.header, "round_index", 0)}:{round_kind}',
        'cache_event': 'unknown',
        'configured_interface': 'responses_api',
        'actual_interface': 'unavailable',
        'streaming': False,
        'output_bytes_estimate': 0,
        'output_ref_list_max_length': 0,
        'output_ref_total_count': 0,
        'oversized_output': False,
    }
    if round_kind == 'policy_retry':
        available_request_types = sorted({str(t) for t in (getattr(dossier, 'available_detail_request_types', []) or []) if str(t)})
        legal_anchor_available = bool(getattr(dossier, 'detailed_card_refs', []) or getattr(dossier, 'assignable_target_refs', []) or getattr(dossier, 'seen_detail_refs', []) or [])
        audit['policy_retry_request_choices'] = available_request_types
        audit['policy_retry_legal_anchor_available'] = legal_anchor_available
        audit['premature_guard_decision'] = {
            'triggered': False,
            'allowed': True,
            'reason': 'policy_retry_prompt_prepared',
            'round_kind': round_kind,
            'budget_available': bool(getattr(dossier.budget, 'max_evidence_batches', 0) and getattr(dossier.budget, 'used_evidence_batches', 0) < getattr(dossier.budget, 'max_evidence_batches', 0)),
            'request_types_available': available_request_types,
            'legal_anchor_available': legal_anchor_available,
            'anchor_count': len(list(getattr(dossier, 'detailed_card_refs', []) or [])),
            'anchor_samples': list(getattr(dossier, 'detailed_card_refs', []) or [])[:8],
            'judge_no_request_reason': '',
            'fail_closed_reason_kinds': [],
        }
    if final_round:
        audit['policy_issue_summary'] = 'final_round_budget_guard: choose submit_verdict_or_fail_closed_only; request_evidence is forbidden'
    elif round_kind == 'policy_retry':
        audit['policy_issue_summary'] = 'large_case/detail_cards_insufficient/budget_or_request_types_available: choose request_evidence or explain no legal anchor'
    try:
        response = _call_ai_with_schema(ai_client, prompt, CaseJudgeOutput)
        raw_response = _extract_response_content(response)
        if raw_response is None:
            return CaseJudgeCallResult(ok=False, output=None, prompt=prompt, error='case judge no response: provider returned None', raw_response=response, elapsed_ms=int((time.time()-started)*1000), request_audit={**audit, 'error_kind': 'provider_no_response', 'error_message': 'provider returned None'})
        if isinstance(raw_response, CaseJudgeOutput):
            audit = {**audit, **_output_budget_audit(raw_response)}
            return CaseJudgeCallResult(ok=True, output=raw_response, prompt=prompt, error='', raw_response=response, elapsed_ms=int((time.time()-started)*1000), request_audit={**audit})

        try:
            if isinstance(raw_response, str):
                output = CaseJudgeOutput.model_validate_json(raw_response)
            else:
                output = CaseJudgeOutput.model_validate(raw_response)
        except ValidationError as exc:
            return CaseJudgeCallResult(ok=False, output=None, prompt=prompt, error=f'case judge schema parse error: {exc}', raw_response=response, elapsed_ms=int((time.time()-started)*1000), request_audit={**audit, 'error_kind': 'schema_parse_error', 'error_message': str(exc)})
        except Exception as exc:
            return CaseJudgeCallResult(ok=False, output=None, prompt=prompt, error=f'case judge parse error: {exc}', raw_response=response, elapsed_ms=int((time.time()-started)*1000), request_audit={**audit, 'error_kind': 'parse_error', 'error_message': str(exc)})

        audit = {**audit, **_output_budget_audit(output)}
        return CaseJudgeCallResult(ok=True, output=output, prompt=prompt, error='', raw_response=response, elapsed_ms=int((time.time()-started)*1000), request_audit={**audit})
    except Exception as exc:
        return CaseJudgeCallResult(ok=False, output=None, prompt=prompt, error=f'case judge call failed: {exc}', raw_response=None, elapsed_ms=int((time.time()-started)*1000), request_audit={**audit, 'error_kind': 'call_failed', 'error_message': str(exc)})
