from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evidence_menu import build_executable_evidence_menu
from .mapping_draft import compute_mapping_draft_accounting
from .models import EvidenceRequestType, MappingIntent, NotebookUpdate, QueryCandidate
from .notebook import build_notebook
from .special_investigation import is_special_eligible_span
from .workspace import CaseEvidenceWorkspace


OrchestratorToolName = Literal[
    'materialize_queries',
    'execute_evidence',
    'propose_mapping_intents',
    'update_notebook',
    'reconsider_split',
    'finish_case',
]


class MaterializeQueriesToolArgs(BaseModel):
    reason: str = ''
    expected_observation: str = ''
    queries: list[QueryCandidate] = Field(default_factory=list)
    query_hints: list[str] = Field(default_factory=list)
    notebook_refs: list[str] = Field(default_factory=list)
    local_refs: list[str] = Field(default_factory=list)
    failed_query_refs: list[str] = Field(default_factory=list)
    ignored_noise_terms: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')


class ExecuteEvidenceToolArgs(BaseModel):
    reason: str = ''
    expected_observation: str = ''
    selected_menu_request_ids: list[str] = Field(default_factory=list)
    requested_request_types: list[EvidenceRequestType] = Field(default_factory=list)
    notebook_refs: list[str] = Field(default_factory=list)
    local_refs: list[str] = Field(default_factory=list)
    query_refs: list[str] = Field(default_factory=list)
    subject_refs: list[str] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')


class ProposeMappingIntentsToolArgs(BaseModel):
    reason: str = ''
    expected_observation: str = ''
    mapping_intents: list[MappingIntent] = Field(default_factory=list)
    notebook_updates: list[NotebookUpdate] = Field(default_factory=list)
    row_refs: list[str] = Field(default_factory=list)
    local_refs: list[str] = Field(default_factory=list)
    subject_refs: list[str] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)
    target_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')


class UpdateNotebookToolArgs(BaseModel):
    reason: str = ''
    expected_observation: str = ''
    notebook_updates: list[NotebookUpdate] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')


class ReconsiderSplitToolArgs(BaseModel):
    reason: str = ''
    expected_observation: str = ''
    local_refs: list[str] = Field(default_factory=list)
    notebook_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')


class FinishCaseToolArgs(BaseModel):
    status: Literal['accepted', 'fail_closed'] = 'fail_closed'
    finish_kind: Literal[
        'accepted',
        'no_new_evidence',
        'semantic_target_conflict',
        'budget_exhausted',
        'tool_loop_blocked',
    ] = 'no_new_evidence'
    reason: str = ''
    expected_observation: str = ''
    related_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')


TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    'materialize_queries': MaterializeQueriesToolArgs,
    'execute_evidence': ExecuteEvidenceToolArgs,
    'propose_mapping_intents': ProposeMappingIntentsToolArgs,
    'update_notebook': UpdateNotebookToolArgs,
    'reconsider_split': ReconsiderSplitToolArgs,
    'finish_case': FinishCaseToolArgs,
}


@dataclass
class OrchestratorAgentToolCall:
    tool_name: str
    arguments: BaseModel
    raw_arguments: dict[str, Any]
    call_id: str = ''
    response_id: str = ''


@dataclass
class OrchestratorAgentSession:
    case_id: str
    turn_count: int = 0
    compact_count: int = 0
    context_soft_limit_hit_count: int = 0
    context_hard_limit_hit_count: int = 0
    tool_rejection_count: int = 0
    input_token_estimate: int = 0
    output_token_estimate: int = 0
    tool_sequence: list[str] = field(default_factory=list)
    history_items: list[dict[str, object]] = field(default_factory=list)
    compacted_history_summary: str = ''


@dataclass
class OrchestratorAgentCallResult:
    ok: bool
    session: OrchestratorAgentSession
    tool_call: OrchestratorAgentToolCall | None = None
    audit: dict[str, object] = field(default_factory=dict)
    error: str = ''


def _jsonable(value: Any) -> Any:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _strict_schema_for_model(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()

    def visit(node: object) -> None:
        if not isinstance(node, dict):
            return
        node.pop('title', None)
        node.pop('description', None)
        if node.get('type') == 'object':
            props = node.get('properties')
            if isinstance(props, dict):
                node['required'] = list(props.keys())
                for child in props.values():
                    visit(child)
            node['additionalProperties'] = False
        for key in ('$defs', 'items', 'anyOf', 'oneOf', 'allOf'):
            value = node.get(key)
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

    visit(schema)
    return schema


def orchestrator_tool_definitions(allowed_tool_names: set[str] | None = None) -> list[dict[str, object]]:
    descriptions = {
        'materialize_queries': 'Create Bangumi subject-search query cards from OrchestratorAgent-provided clean title or alias queries.',
        'execute_evidence': 'Execute visible evidence menu requests through the Bangumi evidence broker.',
        'propose_mapping_intents': 'Write OrchestratorAgent semantic mapping intents; the fixed layer compiles them into internal draft patches or returns missing evidence.',
        'update_notebook': 'Persist hypotheses, open questions, closed questions, or next actions in the notebook.',
        'reconsider_split': 'Ask fixed layer to reconsider child-case boundaries when a large or mixed package remains unresolved.',
        'finish_case': 'Request accepted or fail_closed; fixed layer must still verify accounting, refs, and finish preconditions.',
    }
    return [
        {
            'type': 'function',
            'function': {
                'name': name,
                'description': descriptions[name],
                'parameters': _strict_schema_for_model(model),
            },
        }
        for name, model in TOOL_ARG_MODELS.items()
        if allowed_tool_names is None or name in allowed_tool_names
    ]


ORCHESTRATOR_AGENT_INSTRUCTIONS = """You are the Local to Bangumi OrchestratorAgent.
You investigate like a careful human in one continuous case session: understand the local package, form title/work-unit hypotheses, materialize clean Bangumi title/alias queries, request evidence, update the notebook, write semantic mapping intents, self-review, and only finish when fixed-layer verification can succeed or evidence is genuinely exhausted.

Use native function tools for actions. Do not emit business mapping JSON directly.
Choose one tool call per turn.
The fixed layer will reject hidden refs, stale duplicate actions, invalid phase ordering, budget excess, and failed accounting.
If a tool is rejected, inspect the rejection and choose a corrected next action.
Repeated finish_case calls without changing open rows are a mistake: if draft accounting is not ready, continue investigation or mapping intents.
The explicit workspace/notebook is the source of truth; do not rely on hidden memory alone.
You own the semantic work. Do not wait for a separate QueryComposer, MappingDraftEditor, or Judge role: provide query texts through materialize_queries, provide structured semantic decisions through propose_mapping_intents, and explain fail_closed through finish_case.
Do not write internal MappingDraftPatch objects. For mapping decisions, say what local row/work unit maps to which visible Bangumi subject/item/span and why; if a visible candidate is wrong, explicitly reject that candidate. The fixed layer compiles those semantic intents into BE/BES draft patches or asks for the missing evidence.
"""


def _sample_cards(cards: list[Any], *, limit: int = 8) -> list[dict[str, object]]:
    result = []
    for card in list(cards or [])[:limit]:
        if hasattr(card, 'model_dump'):
            payload = card.model_dump(mode='json')
        elif isinstance(card, dict):
            payload = dict(card)
        else:
            continue
        compact = {
            key: value
            for key, value in payload.items()
            if key in {
                'ref',
                'path',
                'label',
                'span_scope',
                'file_ref_count',
                'file_ref_samples',
                'title_cues',
                'query_text',
                'query_origin',
                'query_kind',
                'title',
                'name',
                'name_cn',
                'subject_ref',
                'item_kind',
                'source_form_hint',
                'relation_to_main',
            }
            and value not in ('', [], {}, None)
        }
        result.append(compact)
    return result


def _draft_open_rows_for_agent(workspace: CaseEvidenceWorkspace, *, limit: int = 16) -> list[dict[str, object]]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return []
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    file_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    target_briefs: dict[str, dict[str, object]] = {}
    items_by_subject: dict[str, list[Any]] = {}
    subject_title_by_ref = {
        str(getattr(subject, 'ref', '') or ''): str(getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', '') or '')
        for subject in list(getattr(workspace, 'bangumi_subjects', []) or [])
        if str(getattr(subject, 'ref', '') or '')
    }
    for item in list(getattr(workspace, 'bangumi_items', []) or []):
        ref = str(getattr(item, 'ref', '') or '')
        if ref:
            subject_ref = str(getattr(item, 'subject_ref', '') or '')
            if subject_ref:
                items_by_subject.setdefault(subject_ref, []).append(item)
            target_briefs[ref] = {
                'ref': ref,
                'target_kind': 'BE_item',
                'subject_ref': str(getattr(item, 'subject_ref', '') or ''),
                'sort': getattr(item, 'sort', None),
                'ep': getattr(item, 'ep', None),
                'item_kind': str(getattr(item, 'item_kind', '') or ''),
                'title': str(getattr(item, 'title', '') or getattr(item, 'name_cn', '') or getattr(item, 'name', '') or '')[:80],
            }
    for span in list(getattr(workspace, 'bangumi_span_cards', []) or []):
        ref = str(getattr(span, 'ref', '') or '')
        if ref:
            target_briefs[ref] = {
                'ref': ref,
                'target_kind': 'BES_span',
                'subject_ref': str(getattr(span, 'subject_ref', '') or ''),
                'target_ref_count': int(getattr(span, 'target_ref_count', 0) or len(list(getattr(span, 'target_refs', []) or [])) or 0),
                'sort_start': getattr(span, 'sort_start', None),
                'sort_end': getattr(span, 'sort_end', None),
                'ep_start': getattr(span, 'ep_start', None),
                'ep_end': getattr(span, 'ep_end', None),
                'item_kind': str(getattr(span, 'item_kind', '') or ''),
                'target_ref_samples': list(getattr(span, 'target_ref_samples', []) or [])[:6],
                'target_refs': list(getattr(span, 'target_refs', []) or [])[:12],
                'detail_equivalent': bool(getattr(span, 'detail_equivalent', False)),
            }
    result: list[dict[str, object]] = []
    for row in list(getattr(draft, 'rows', []) or []):
        disposition = str(getattr(row, 'disposition', '') or '')
        status = str(getattr(row, 'status', '') or '')
        if disposition in {'map_to_bangumi', 'non_bangumi_or_supplemental'} or status == 'verified':
            continue
        local_ref = str(getattr(row, 'local_ref', '') or '')
        local_payload: dict[str, object] = {'local_ref': local_ref}
        if local_ref in span_by_ref:
            span = span_by_ref[local_ref]
            file_count = int(getattr(span, 'file_ref_count', 0) or len(list(getattr(span, 'file_refs', []) or [])) or 0)
            local_payload.update({
                'local_ref_kind': 'span',
                'file_ref_count': file_count,
                'file_ref_samples': list(getattr(span, 'file_ref_samples', []) or [])[:8] or list(getattr(span, 'file_refs', []) or [])[:8],
                'episode_token_start': getattr(span, 'episode_token_start', None),
                'episode_token_end': getattr(span, 'episode_token_end', None),
                'title_cues': list(getattr(span, 'title_cues', []) or [])[:6],
                'span_scope': str(getattr(span, 'span_scope', '') or ''),
            })
        elif local_ref in file_by_ref:
            card = file_by_ref[local_ref]
            file_count = 1
            local_payload.update({
                'local_ref_kind': 'file',
                'file_ref_count': 1,
                'file_ref_samples': [local_ref],
                'path': str(getattr(card, 'path', '') or ''),
                'label': str(getattr(card, 'label', '') or ''),
            })
        else:
            file_count = 0
        candidate_refs = list(getattr(row, 'candidate_target_refs', []) or [])
        subject_refs_for_sequences = _dedupe([
            *[str(ref or '') for ref in list(getattr(row, 'subject_refs', []) or [])],
            *[
                str(target_briefs.get(ref, {}).get('subject_ref') or '')
                for ref in candidate_refs
            ],
        ])
        local_span = span_by_ref.get(local_ref)
        special_eligible = is_special_eligible_span(local_span, workspace.to_dossier(round_context='orchestrator_agent_open_row_special_sequence')) if local_span is not None else False
        sequence_filters: list[tuple[str, set[str]]] = []
        if special_eligible:
            sequence_filters.append(('special', {'special', 'movie'}))
        sequence_filters.append(('regular', {'episode', 'regular', 'unknown', ''}))
        visible_subject_item_sequences = []
        for subject_ref in subject_refs_for_sequences[:4]:
            for sequence_kind, allowed_kinds in sequence_filters:
                ordered_items = sorted(
                    [
                        item for item in list(items_by_subject.get(subject_ref, []) or [])
                        if str(getattr(item, 'item_kind', '') or '') in allowed_kinds
                        and str(getattr(item, 'ref', '') or '')
                    ],
                    key=lambda item: (getattr(item, 'sort', 0) or 0, getattr(item, 'ep', 0) or 0, str(getattr(item, 'ref', '') or '')),
                )
                if not ordered_items:
                    continue
                refs = [str(getattr(item, 'ref', '') or '') for item in ordered_items[: max(file_count, 1)]]
                visible_subject_item_sequences.append({
                    'subject_ref': subject_ref,
                    'subject_title': subject_title_by_ref.get(subject_ref, ''),
                    'sequence_kind': sequence_kind,
                    'item_refs': refs[:24],
                    'item_ref_count': len(refs),
                    'matches_local_file_count': bool(file_count and len(refs) == file_count),
                    'sort_start': getattr(ordered_items[0], 'sort', None),
                    'sort_end': getattr(ordered_items[min(len(refs), len(ordered_items)) - 1], 'sort', None) if refs else None,
                    'title_samples': [
                        str(getattr(item, 'title', '') or getattr(item, 'name_cn', '') or getattr(item, 'name', '') or '')
                        for item in ordered_items[:3]
                    ],
                })
                if len(visible_subject_item_sequences) >= 6:
                    break
            if len(visible_subject_item_sequences) >= 6:
                break
        protocol_warning = ''
        recommended_exit = ''
        if disposition == 'unaligned_fail_closed':
            protocol_warning = (
                'This row is currently marked as terminal fail_closed. It will keep accounting unresolved and '
                'can never produce accepted. If the semantic meaning is "Bangumi has no corresponding target '
                'but this local row is otherwise handled", revise it with '
                'mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent).'
            )
            recommended_exit = (
                'revise to a mapping or accepted target_absent/supplemental intent; only keep '
                'mark_unaligned_fail_closed when you intend the whole case to fail_closed'
            )
        elif candidate_refs:
            if file_count > 1 and any(str(ref or '').startswith('BES') for ref in candidate_refs):
                recommended_exit = (
                    'for this multi-file row, use map_regular_span with chosen_span_ref set to the visible BES* candidate, '
                    'or reject wrong candidates before target_absent/supplemental'
                )
            elif file_count > 1:
                recommended_exit = (
                    'for this multi-file row, use map_regular_span with visible item_refs that cover the row, '
                    'or reject wrong candidates before target_absent/supplemental'
                )
            else:
                recommended_exit = (
                    'choose one visible candidate with a mapping intent; if a candidate is semantically wrong, '
                    'first use reject_candidate for that candidate, then use target_absent/supplemental only after wrong candidates are removed'
                )
        elif disposition == 'needs_more_evidence':
            recommended_exit = 'execute the requested evidence or revise the row with a concrete mapping/exclusion intent'
        else:
            recommended_exit = 'map, request evidence, or mark an accepted non-Bangumi/supplemental disposition'
        result.append({
            'row_ref': str(getattr(row, 'row_ref', '') or ''),
            'status': status,
            'disposition': disposition,
            'reason_kind': str(getattr(row, 'reason_kind', '') or ''),
            'selected_target_ref': str(getattr(row, 'selected_target_ref', '') or ''),
            'candidate_target_refs': candidate_refs[:12],
            'candidate_target_briefs': [target_briefs.get(ref, {'ref': ref, 'target_kind': 'unknown'}) for ref in candidate_refs[:12]],
            'requested_request_types': list(getattr(row, 'requested_request_types', []) or [])[:8],
            'query_hints': list(getattr(row, 'query_hints', []) or [])[:8],
            'subject_refs': list(getattr(row, 'subject_refs', []) or [])[:8],
            'item_refs': list(getattr(row, 'item_refs', []) or [])[:8],
            'visible_subject_item_sequences': visible_subject_item_sequences[:4],
            'protocol_warning': protocol_warning,
            'recommended_exit': recommended_exit,
            **local_payload,
        })
        if len(result) >= limit:
            break
    return result


def _open_rows_all_terminal_fail_closed(workspace: CaseEvidenceWorkspace) -> bool:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return False
    open_rows = [
        row for row in list(getattr(draft, 'rows', []) or [])
        if str(getattr(row, 'disposition', '') or '') not in {'map_to_bangumi', 'non_bangumi_or_supplemental'}
        and str(getattr(row, 'status', '') or '') != 'verified'
    ]
    return bool(open_rows) and all(
        str(getattr(row, 'disposition', '') or '') == 'unaligned_fail_closed'
        for row in open_rows
    )


def _reconsider_split_already_observed(workspace: CaseEvidenceWorkspace) -> bool:
    return any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_reconsider_split_observation'
        for audit in list(getattr(workspace, 'judge_request_audits', []) or [])
    )


def _reconsider_split_may_change_case_boundary(workspace: CaseEvidenceWorkspace) -> bool:
    main_count = len(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or []))
    child_spans = [
        card for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'span_scope', '') or '') not in {'', 'package', 'unpartitioned'}
    ]
    # This is only a structural loop guard: small one/two-unit packages can be
    # resolved inside one case, while large boxes or many child spans may still
    # need a split reconsideration tool turn.
    return main_count >= 20 or len(child_spans) >= 3


def _query_recall_observation_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    query_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'query_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    searched_refs: set[str] = set()
    empty_searches: list[dict[str, object]] = []
    non_empty_searches: list[dict[str, object]] = []
    for batch in list(getattr(workspace, 'previous_evidence_results', []) or []):
        for result in list(getattr(batch, 'request_results', []) or getattr(batch, 'results', []) or []):
            if str(getattr(result, 'request_type', '') or '') != 'subject_search':
                continue
            request_ref = str(getattr(result, 'request_ref', '') or '')
            query_ref = request_ref.replace('REQ_SUBJECT_SEARCH_', '', 1) if request_ref.startswith('REQ_SUBJECT_SEARCH_') else ''
            searched_refs.add(query_ref)
            query_card = query_by_ref.get(query_ref)
            row = {
                'request_ref': request_ref,
                'query_ref': query_ref,
                'query_text': str(getattr(query_card, 'query_text', '') or ''),
                'response_refs': list(getattr(result, 'response_refs', []) or [])[:12],
                'accepted': bool(getattr(result, 'accepted', False)),
                'notes': list(getattr(result, 'notes', []) or [])[:4],
            }
            if row['response_refs']:
                non_empty_searches.append(row)
            else:
                empty_searches.append(row)
    pending_queries = [
        {
            'query_ref': ref,
            'query_text': str(getattr(card, 'query_text', '') or ''),
        }
        for ref, card in query_by_ref.items()
        if str(getattr(card, 'query_kind', '') or '') == 'subject_search'
        and str(getattr(card, 'query_origin', '') or '') == 'agent_composed'
        and ref not in searched_refs
    ]
    latin_empty = [
        row for row in empty_searches
        if re.fullmatch(r'[A-Za-z0-9 ._-]+', str(row.get('query_text') or '').strip() or ' ')
    ]
    guidance: list[str] = []
    if latin_empty:
        guidance.append(
            'Latin/romanized subject searches returned no useful subject. Try title-preserving aliases: '
            'space/case variants, fused/split romanized words, and original Japanese or Chinese official title forms if you know them.'
        )
    if non_empty_searches and not list(getattr(workspace, 'bangumi_items', []) or []):
        guidance.append(
            'Visible BS subjects are recall evidence only. If their titles do not match the local title cues, reject or ignore them semantically and materialize better title/alias queries.'
        )
    return {
        'empty_subject_searches': empty_searches[-8:],
        'non_empty_subject_searches': non_empty_searches[-8:],
        'pending_agent_subject_queries': pending_queries[:8],
        'guidance': guidance,
    }


def _allowed_tool_names_for_workspace(workspace: CaseEvidenceWorkspace) -> set[str]:
    menu = build_executable_evidence_menu(workspace, max_requests=24)
    completed_or_failed = {
        str(ref or '')
        for ref in [
            *list(getattr(getattr(workspace, 'plan_state', None), 'completed_menu_request_ids', []) or []),
            *list(getattr(getattr(workspace, 'plan_state', None), 'failed_menu_request_ids', []) or []),
        ]
        if str(ref or '')
    }
    fresh_request_ids = [
        str(summary.get('request_id') or '')
        for summary in list(menu.get('prompt_summaries') or [])
        if isinstance(summary, dict)
        and str(summary.get('request_id') or '')
        and str(summary.get('request_id') or '') not in completed_or_failed
    ]
    executable_count = len(fresh_request_ids)
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_intent_index = -1
    latest_intent_result: dict[str, object] = {}
    for index in range(len(audits) - 1, -1, -1):
        audit = audits[index]
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_mapping_intents_result':
            latest_intent_index = index
            latest_intent_result = audit
            break
    evidence_attempt_after_latest_intent = any(
        isinstance(audit, dict)
        and (
            audit.get('note') == 'orchestrator_execute_evidence_menu_resolution'
            or (
                audit.get('note') == 'orchestrator_tool_selected'
                and audit.get('tool_name') == 'execute_evidence'
            )
        )
        for audit in audits[latest_intent_index + 1:]
    ) if latest_intent_index >= 0 else False
    if isinstance(latest_intent_result, dict):
        requested_evidence = list(latest_intent_result.get('requested_evidence') or [])
        blocked_count = int(latest_intent_result.get('blocked_intent_count') or 0)
        patch_issue_codes = {str(code or '') for code in list(latest_intent_result.get('patch_issue_codes') or [])}
        if (requested_evidence or blocked_count > 0) and executable_count > 0 and not evidence_attempt_after_latest_intent:
            return {'execute_evidence'}
        if patch_issue_codes and patch_issue_codes <= {'invalid_reason_kind', 'regular_main_span_cannot_be_supplemental'}:
            return {'propose_mapping_intents'}

    draft = getattr(workspace, 'mapping_draft', None)
    accounting = compute_mapping_draft_accounting(draft, workspace.to_dossier(round_context='orchestrator_agent_tool_gate')).model_dump(mode='json') if draft is not None else None
    accepted_ready = bool((accounting or {}).get('accepted_accounting_ready')) if isinstance(accounting, dict) else False
    open_rows_present = bool(_draft_open_rows_for_agent(workspace, limit=1))
    if accepted_ready:
        return {'finish_case'}

    allowed = {
        'materialize_queries',
        'execute_evidence',
        'propose_mapping_intents',
    }
    if _reconsider_split_may_change_case_boundary(workspace) and not _reconsider_split_already_observed(workspace):
        allowed.add('reconsider_split')
    if not open_rows_present:
        allowed.add('update_notebook')
    budget_exhausted = bool(
        getattr(workspace.budget, 'max_evidence_batches', 0)
        and workspace.budget.used_evidence_batches >= workspace.budget.max_evidence_batches
    )
    terminal_fail_closed_ready = _open_rows_all_terminal_fail_closed(workspace)
    if budget_exhausted or terminal_fail_closed_ready or not open_rows_present:
        allowed.add('finish_case')
    return allowed


def build_orchestrator_agent_input(workspace: CaseEvidenceWorkspace, *, reason: str = '') -> str:
    dossier = workspace.to_dossier(round_context='orchestrator_agent')
    menu = build_executable_evidence_menu(workspace, max_requests=24)
    draft = getattr(workspace, 'mapping_draft', None)
    accounting = compute_mapping_draft_accounting(draft, dossier).model_dump(mode='json') if draft is not None else None
    allowed_tool_names = sorted(_allowed_tool_names_for_workspace(workspace))
    draft_rows = []
    for row in list(getattr(draft, 'rows', []) or [])[:24]:
        draft_rows.append({
            'row_ref': getattr(row, 'row_ref', ''),
            'local_ref': getattr(row, 'local_ref', ''),
            'local_ref_kind': getattr(row, 'local_ref_kind', ''),
            'candidate_target_refs': list(getattr(row, 'candidate_target_refs', []) or [])[:12],
            'selected_target_ref': getattr(row, 'selected_target_ref', ''),
            'disposition': getattr(row, 'disposition', ''),
            'status': getattr(row, 'status', ''),
            'reason_kind': getattr(row, 'reason_kind', ''),
            'requested_request_types': list(getattr(row, 'requested_request_types', []) or [])[:8],
            'query_hints': list(getattr(row, 'query_hints', []) or [])[:8],
        })
    payload = {
        'case_id': workspace.header.case_id,
        'reason': reason,
        'budget': {
            'max_evidence_batches': workspace.budget.max_evidence_batches,
            'used_evidence_batches': workspace.budget.used_evidence_batches,
            'max_api_calls_per_case': workspace.budget.max_api_calls_per_case,
            'used_api_calls': workspace.budget.used_api_calls,
            'max_subject_searches': workspace.budget.max_subject_searches,
            'used_subject_searches': workspace.budget.used_subject_searches,
        },
        'visible_refs': dossier.visible_refs.model_dump(mode='json'),
        'local_file_sample': _sample_cards(list(workspace.local_files or []), limit=10),
        'local_span_sample': _sample_cards(list(workspace.local_span_cards or []), limit=12),
        'query_card_sample': _sample_cards(list(workspace.query_cards or []), limit=12),
        'bangumi_subject_sample': _sample_cards(list(workspace.bangumi_subjects or []), limit=10),
        'bangumi_item_sample': _sample_cards(list(workspace.bangumi_items or []), limit=12),
        'bangumi_span_sample': _sample_cards(list(workspace.bangumi_span_cards or []), limit=12),
        'mapping_draft_rows': draft_rows,
        'draft_accounting': accounting,
        'open_rows_requiring_agent_action': _draft_open_rows_for_agent(workspace),
        'finish_protocol': {
            'accepted_finish_allowed_now': bool((accounting or {}).get('accepted_accounting_ready')) if isinstance(accounting, dict) else False,
            'finish_tool_available_now': 'finish_case' in allowed_tool_names,
            'finish_case_is_terminal_only': True,
            'when_not_ready': 'Do not call finish_case. Use propose_mapping_intents for open_rows_requiring_agent_action or execute evidence_menu requests first.',
            'target_absent_is_accepted_exclusion': 'Use mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent), not mark_unaligned_fail_closed, when Bangumi has no corresponding target but the row is handled.',
            'unaligned_fail_closed_is_terminal_failure': 'Rows marked mark_unaligned_fail_closed keep accounting unresolved. They are only for a real fail_closed case.',
        },
        'available_tool_names': allowed_tool_names,
        'allowed_supplemental_reason_kinds': [
            'bangumi_target_absent',
            'bonus_video',
            'pv_cm',
            'creditless_op_ed',
            'trailer',
            'sample',
            'duplicate_packaging',
            'non_episode_video',
            'making_of',
            'menu_or_navigation',
            'other_supplemental',
        ],
        'allowed_unaligned_reason_kinds': [
            'ambiguous_ownership',
            'special_regular_conflict',
            'coverage_gap_unresolved',
            'insufficient_evidence',
        ],
        'notebook': build_notebook(dossier),
        'evidence_menu': {
            'prompt_summaries': list(menu.get('prompt_summaries') or [])[:24],
            'audit': menu.get('audit') or {},
        },
        'query_recall_observation': _query_recall_observation_for_agent(workspace),
        'recent_audits': list(getattr(workspace, 'judge_request_audits', []) or [])[-12:],
        'rules': [
            'If no Bangumi subject exists, prefer materialize_queries with clean title/alias queries or execute subject_search from visible QC menu.',
            'If Latin/romanized title queries are empty or recall unrelated BS subjects, materialize title-preserving alternatives instead of repeating split: spacing/case variants, fused/split romanized words, and original Japanese kana/kanji or Chinese official title forms when you know them.',
            'Do not use release scope words such as OVA/OAD/SP/year/season, "main TV series", codec, group, resolution, or file role names as standalone subject queries. Those are shape clues, not Bangumi subject titles.',
            'If subjects exist but no item targets exist, inspect subject/episode/related evidence before target_span.',
            'Use propose_mapping_intents when you have a semantic decision: map_regular_span, map_explicit_item, reject_candidate, mark_non_bangumi_or_supplemental, needs_more_evidence, or mark_unaligned_fail_closed.',
            'BS* subject refs are evidence choices, not final assignment targets. For regular runs, choose chosen_subject_ref plus episode_start/episode_end; the fixed layer will request or compile a BES span.',
            'For singleton movie/special/episode decisions, choose a visible chosen_item_ref=BE*. For an already visible span decision, choose chosen_span_ref=BES*.',
            'For any multi-file row, including SP/OVA/OAD/special rows, if open_rows show visible_subject_item_sequences with matches_local_file_count=true and the sequence is semantically correct, use those item_refs in map_regular_span; the compiler can materialize a controlled BES span from your explicit item_refs.',
            'If a multi-file row has a visible BES* candidate whose target_ref_count matches the local file count and the candidate is semantically correct, use map_regular_span with chosen_span_ref set to that BES*. Do not keep returning needs_more_evidence for a row that already has the visible sequence you need.',
            'Put LF/LS refs in local_ref/local_refs/support_refs/source_refs. Put BS refs in chosen_subject_ref/subject_refs/support_refs. Put BE refs in chosen_item_ref/item_refs/support_refs. Put BES refs in chosen_span_ref/target_refs/support_refs.',
            'Use finish_case only after the latest useful evidence has been reflected in draft patches or evidence is genuinely exhausted.',
            'If finish_case is rejected for accepted_accounting_not_ready/not_ready, do not call finish_case again; repair open/needs_more_evidence rows with propose_mapping_intents or execute fresh evidence first.',
            'If open_rows_requiring_agent_action is non-empty and finish_protocol.accepted_finish_allowed_now is false, your next useful action is usually propose_mapping_intents for those rows or execute_evidence for their requested request types, not finish_case.',
            'When a compiler/tool output says open_rows remain, pick each listed row_ref/local_ref and either map it, request evidence for it, mark it target_absent/supplemental, or mark a concrete semantic conflict.',
            'If a row has candidate_target_refs that are semantically wrong, use reject_candidate with chosen_item_ref=BE* or chosen_span_ref=BES* before using bangumi_target_absent. The fixed layer will not treat target_absent as accepted while row candidates remain.',
            'For mark_non_bangumi_or_supplemental, use one allowed_supplemental_reason_kinds value exactly. bangumi_target_absent means Bangumi has no corresponding target for that local row after investigation.',
            'If a local row should still be accepted but not mapped because Bangumi has no corresponding target, use mark_non_bangumi_or_supplemental with reason_kind=bangumi_target_absent. Do not use mark_unaligned_fail_closed for accepted target-absent rows.',
            'For mark_unaligned_fail_closed, use one allowed_unaligned_reason_kinds value exactly; these rows keep the case unresolved and are for real fail_closed cases. Do not use no_legal_target for Bangumi target absence; that accepted case is bangumi_target_absent under mark_non_bangumi_or_supplemental.',
            'If a compiler result has requested_evidence or blocked intents and execute_evidence is the only available tool, execute the matching evidence menu request ids instead of proposing the same intent again.',
            'For status=accepted, finish_kind must be accepted. For status=fail_closed, choose no_new_evidence, semantic_target_conflict, budget_exhausted, or tool_loop_blocked.',
            'If a tool returns blocked intents, patch/accounting issues, or requested evidence, respond with execute_evidence or revised propose_mapping_intents instead of assuming Python will repair it.',
        ],
    }
    return json.dumps(_jsonable(payload), ensure_ascii=False, indent=2)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _maybe_compact_session(
    session: OrchestratorAgentSession,
    *,
    estimated_input_tokens: int,
    soft_token_limit: int,
    hard_token_limit: int,
) -> tuple[OrchestratorAgentSession, dict[str, object]]:
    audit: dict[str, object] = {
        'estimated_input_tokens': estimated_input_tokens,
        'soft_token_limit': soft_token_limit,
        'hard_token_limit': hard_token_limit,
        'compacted': False,
        'compact_mode': '',
    }
    if estimated_input_tokens < soft_token_limit:
        return session, audit
    hard_hit = estimated_input_tokens >= hard_token_limit
    recent_tools = ' -> '.join(session.tool_sequence[-16:])
    prior_summary = session.compacted_history_summary.strip()
    summary_parts = []
    if prior_summary:
        summary_parts.append(prior_summary)
    if recent_tools:
        summary_parts.append(f'Compacted local OrchestratorAgent tool history. Recent tool sequence: {recent_tools}.')
    summary_parts.append('Authoritative current state is in the visible workspace, mapping draft, notebook, and audit cards in this turn.')
    updated = replace(
        session,
        history_items=[],
        compacted_history_summary='\n'.join(summary_parts),
        compact_count=session.compact_count + 1,
        context_soft_limit_hit_count=session.context_soft_limit_hit_count + 1,
        context_hard_limit_hit_count=session.context_hard_limit_hit_count + (1 if hard_hit else 0),
    )
    audit.update({'compacted': True, 'compact_mode': 'local_history_trim_after_context_threshold'})
    return updated, audit


def _function_call_history_item(tool_call: OrchestratorAgentToolCall) -> dict[str, object]:
    return {
        'type': 'function_call',
        'call_id': tool_call.call_id,
        'name': tool_call.tool_name,
        'arguments': json.dumps(_jsonable(tool_call.raw_arguments), ensure_ascii=False),
    }


def _parse_tool_call(response: dict[str, object]) -> tuple[OrchestratorAgentToolCall | None, str]:
    tool_calls = response.get('tool_calls')
    calls = tool_calls if isinstance(tool_calls, list) else []
    if not calls:
        return None, 'orchestrator_agent_no_tool_call'
    first = calls[0]
    if not isinstance(first, dict):
        return None, 'orchestrator_agent_invalid_tool_call'
    tool_name = str(first.get('name') or '')
    if tool_name not in TOOL_ARG_MODELS:
        return None, f'orchestrator_agent_unknown_tool:{tool_name}'
    raw_arguments = first.get('arguments')
    try:
        parsed = json.loads(str(raw_arguments or '{}'))
    except json.JSONDecodeError as exc:
        return None, f'orchestrator_agent_tool_args_json_error:{exc}'
    if not isinstance(parsed, dict):
        return None, 'orchestrator_agent_tool_args_not_object'
    try:
        arguments = TOOL_ARG_MODELS[tool_name].model_validate(parsed)
    except ValidationError as exc:
        return None, f'orchestrator_agent_tool_args_schema_error:{exc}'
    return OrchestratorAgentToolCall(
        tool_name=tool_name,
        arguments=arguments,
        raw_arguments=parsed,
        call_id=str(first.get('call_id') or first.get('id') or ''),
        response_id=str(response.get('id') or ''),
    ), ''


def call_orchestrator_agent(
    ai_client: object,
    workspace: CaseEvidenceWorkspace,
    session: OrchestratorAgentSession,
    *,
    reason: str = '',
    soft_token_limit: int = 24000,
    hard_token_limit: int = 32000,
) -> OrchestratorAgentCallResult:
    prompt = build_orchestrator_agent_input(workspace, reason=reason)
    history_items = list(session.history_items or [])
    estimated_tokens = _estimate_tokens(prompt) + sum(_estimate_tokens(json.dumps(item, ensure_ascii=False)) for item in history_items)
    if session.compacted_history_summary:
        estimated_tokens += _estimate_tokens(session.compacted_history_summary)
    session, compact_audit = _maybe_compact_session(
        session,
        estimated_input_tokens=estimated_tokens,
        soft_token_limit=soft_token_limit,
        hard_token_limit=hard_token_limit,
    )
    history_items = list(session.history_items or [])
    input_items: list[dict[str, object]] = []
    if session.compacted_history_summary:
        input_items.append({
            'role': 'user',
            'content': f'Compacted prior OrchestratorAgent context:\n{session.compacted_history_summary}',
        })
    input_items.extend(history_items)
    input_items.append({'role': 'user', 'content': prompt})
    call_fn = getattr(ai_client, 'call_responses_tool_agent', None)
    if not callable(call_fn):
        return OrchestratorAgentCallResult(
            ok=False,
            session=session,
            error='orchestrator_agent_transport_unavailable',
            audit={'note': 'orchestrator_agent_transport_unavailable', **compact_audit},
        )
    allowed_tool_names = _allowed_tool_names_for_workspace(workspace)
    response = call_fn(
        instructions=ORCHESTRATOR_AGENT_INSTRUCTIONS,
        input_items=input_items,
        tools=orchestrator_tool_definitions(allowed_tool_names),
        max_output_tokens=4096,
        parallel_tool_calls=False,
        tool_choice='required',
    )
    if not isinstance(response, dict):
        return OrchestratorAgentCallResult(
            ok=False,
            session=session,
            error='orchestrator_agent_no_response',
            audit={'note': 'orchestrator_agent_call_failed', **compact_audit},
        )
    response_id = str(response.get('id') or '')
    usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
    tool_call, parse_error = _parse_tool_call(response)
    updated_session = replace(
        session,
        turn_count=session.turn_count + 1,
        input_token_estimate=int(usage.get('input_tokens') or usage.get('prompt_tokens') or estimated_tokens),
        output_token_estimate=int(usage.get('output_tokens') or usage.get('completion_tokens') or 0),
        tool_sequence=[*session.tool_sequence, tool_call.tool_name] if tool_call else list(session.tool_sequence),
        history_items=[*history_items, _function_call_history_item(tool_call)] if tool_call else history_items,
    )
    audit = {
        'note': 'orchestrator_agent_called',
        'response_id': response_id,
        'session_mode': 'local_explicit_history',
        'turn_count': updated_session.turn_count,
        'tool_name': tool_call.tool_name if tool_call else '',
        'tool_call_id': tool_call.call_id if tool_call else '',
        'available_tool_names': sorted(allowed_tool_names),
        'compact_count': updated_session.compact_count,
        'context_soft_limit_hit_count': updated_session.context_soft_limit_hit_count,
        'context_hard_limit_hit_count': updated_session.context_hard_limit_hit_count,
        'usage': usage,
        **compact_audit,
    }
    if parse_error:
        audit['error'] = parse_error
        return OrchestratorAgentCallResult(False, updated_session, None, audit, parse_error)
    return OrchestratorAgentCallResult(True, updated_session, tool_call, audit, '')


def record_orchestrator_tool_output(
    session: OrchestratorAgentSession,
    tool_call: OrchestratorAgentToolCall,
    output: dict[str, object],
) -> OrchestratorAgentSession:
    if not tool_call.call_id:
        return session
    item = {
        'type': 'function_call_output',
        'call_id': tool_call.call_id,
        'output': json.dumps(_jsonable(output), ensure_ascii=False),
    }
    return replace(session, history_items=[*session.history_items, item])


def orchestrator_session_audit(session: OrchestratorAgentSession) -> dict[str, object]:
    counts: dict[str, int] = {}
    for tool_name in session.tool_sequence:
        counts[tool_name] = counts.get(tool_name, 0) + 1
    return {
        'orchestrator_turn_count': session.turn_count,
        'orchestrator_tool_sequence': list(session.tool_sequence),
        'orchestrator_tool_call_counts': counts,
        'tool_rejection_count': session.tool_rejection_count,
        'compact_count': session.compact_count,
        'context_soft_limit_hit_count': session.context_soft_limit_hit_count,
        'context_hard_limit_hit_count': session.context_hard_limit_hit_count,
        'session_mode': 'local_explicit_history',
        'history_item_count': len(session.history_items),
    }
