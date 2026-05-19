from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evidence_menu import build_executable_evidence_menu
from .mapping_draft import compute_mapping_draft_accounting
from .models import (
    CaseBriefingEvidenceQuestion,
    CaseBriefingTitleHypothesis,
    CaseBriefingWorkUnit,
    CaseResolutionLedgerRow,
    EvidenceRequestType,
    MappingIntent,
    NotebookUpdate,
    QueryCandidate,
    SplitCaseSpec,
)
from .notebook import build_notebook
from .supplemental_policy import ALLOWED_SUPPLEMENTAL_REASON_KINDS
from .verifier import verify_mapping_draft_accounting
from .workspace import CaseEvidenceWorkspace


OrchestratorToolName = Literal[
    'propose_case_understanding',
    'materialize_queries',
    'execute_evidence',
    'propose_case_resolution_ledger',
    'propose_mapping_intents',
    'update_notebook',
    'reconsider_split',
    'split_into_child_cases',
    'finish_case',
]


class ProposeCaseUnderstandingToolArgs(BaseModel):
    reason: str = ''
    expected_observation: str = ''
    package_shape: str = ''
    work_units: list[CaseBriefingWorkUnit] = Field(default_factory=list)
    title_hypotheses: list[CaseBriefingTitleHypothesis] = Field(default_factory=list)
    split_hints: list[str] = Field(default_factory=list)
    evidence_questions: list[CaseBriefingEvidenceQuestion] = Field(default_factory=list)
    summary: str = ''

    model_config = ConfigDict(extra='forbid')


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


class ProposeCaseResolutionLedgerToolArgs(BaseModel):
    reason: str = ''
    expected_observation: str = ''
    ledger_rows: list[CaseResolutionLedgerRow] = Field(default_factory=list)
    summary: str = ''
    plan_row_refs: list[str] = Field(default_factory=list)
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


class SplitIntoChildCasesToolArgs(BaseModel):
    reason: str = ''
    expected_observation: str = ''
    execution_mode: Literal['run_child_cases', 'record_split_plan_only'] = 'run_child_cases'
    coverage_mode: Literal['complete_root_coverage', 'selected_child_cases'] = 'complete_root_coverage'
    split_cases: list[SplitCaseSpec] = Field(default_factory=list)
    recorded_child_case_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')


class FinishWorkUnitReview(BaseModel):
    row_ref: str = ''
    local_ref: str = ''
    outcome_kind: Literal[
        'mapped',
        'target_absent',
        'supplemental',
        'fail_closed',
        'open',
        'mixed',
    ] = 'open'
    file_count: int = 0
    support_refs: list[str] = Field(default_factory=list)
    reason: str = ''

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
    reviewed_outcome_projection: bool = False
    acknowledged_mapped_file_count: int | None = None
    acknowledged_excluded_file_count: int | None = None
    acknowledged_open_file_count: int | None = None
    acknowledged_unresolved_count: int | None = None
    work_unit_reviews: list[FinishWorkUnitReview] = Field(default_factory=list)
    final_case_review: str = ''

    model_config = ConfigDict(extra='forbid')


TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    'propose_case_understanding': ProposeCaseUnderstandingToolArgs,
    'materialize_queries': MaterializeQueriesToolArgs,
    'execute_evidence': ExecuteEvidenceToolArgs,
    'propose_case_resolution_ledger': ProposeCaseResolutionLedgerToolArgs,
    'propose_mapping_intents': ProposeMappingIntentsToolArgs,
    'update_notebook': UpdateNotebookToolArgs,
    'reconsider_split': ReconsiderSplitToolArgs,
    'split_into_child_cases': SplitIntoChildCasesToolArgs,
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
    session_mode: str = 'http_history_replay'
    provider_session_enabled: bool = False
    provider_response_id: str = ''
    provider_conversation_id: str = ''
    http_session_id: str = ''
    prompt_cache_key: str = ''
    cache_mode: str = 'unknown'
    cache_key: str = 'unknown'
    cache_event: str = 'unknown'
    turn_count: int = 0
    compact_count: int = 0
    context_soft_limit_hit_count: int = 0
    context_hard_limit_hit_count: int = 0
    tool_rejection_count: int = 0
    near_turn_limit_unhealthy_count: int = 0
    stall_suspected_count: int = 0
    consecutive_stall_count: int = 0
    input_token_estimate: int = 0
    output_token_estimate: int = 0
    stable_cache_prefix: str = ''
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


def _sequence_item_ref_limit(file_count: int) -> int:
    return max(24, min(max(int(file_count or 0), 0), 256))


def _sequence_title_sample_limit(file_count: int) -> int:
    return max(6, min(max(int(file_count or 0), 0), 24))


def _target_ownership_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, dict[str, object]]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return {}
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    ownership: dict[str, dict[str, object]] = {}
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') != 'map_to_bangumi':
            continue
        row_ref = str(getattr(row, 'row_ref', '') or '')
        local_ref = str(getattr(row, 'local_ref', '') or '')
        selected = str(getattr(row, 'selected_target_ref', '') or '')
        if not selected:
            continue
        if str(getattr(row, 'mapping_mode', '') or '') == 'span_by_index' and selected in span_by_ref:
            target_refs = [str(ref or '') for ref in list(getattr(span_by_ref[selected], 'target_refs', []) or []) if str(ref or '')]
        else:
            target_refs = [selected]
        for target_ref in target_refs:
            ownership.setdefault(target_ref, {
                'target_ref': target_ref,
                'owner_row_ref': row_ref,
                'owner_local_ref': local_ref,
                'owner_selected_target_ref': selected,
            })
    return ownership


def _candidate_conflicts_for_agent(workspace: CaseEvidenceWorkspace, candidate_refs: list[str]) -> list[dict[str, object]]:
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    ownership = _target_ownership_for_agent(workspace)
    conflicts: list[dict[str, object]] = []
    for candidate_ref in _dedupe([str(ref or '') for ref in list(candidate_refs or [])]):
        if candidate_ref in span_by_ref:
            target_refs = [str(ref or '') for ref in list(getattr(span_by_ref[candidate_ref], 'target_refs', []) or []) if str(ref or '')]
        else:
            target_refs = [candidate_ref]
        occupied = [ownership[ref] for ref in target_refs if ref in ownership]
        if occupied:
            conflicts.append({
                'candidate_target_ref': candidate_ref,
                'occupied_target_refs': [str(item.get('target_ref') or '') for item in occupied[:12]],
                'owner_row_refs': _dedupe([str(item.get('owner_row_ref') or '') for item in occupied])[:8],
                'owner_local_refs': _dedupe([str(item.get('owner_local_ref') or '') for item in occupied])[:8],
            })
    return conflicts


def _unowned_candidate_refs_for_agent(workspace: CaseEvidenceWorkspace, candidate_refs: list[str]) -> list[str]:
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    ownership = _target_ownership_for_agent(workspace)
    result: list[str] = []
    for candidate_ref in _dedupe([str(ref or '') for ref in list(candidate_refs or [])]):
        if candidate_ref in span_by_ref:
            target_refs = [str(ref or '') for ref in list(getattr(span_by_ref[candidate_ref], 'target_refs', []) or []) if str(ref or '')]
        else:
            target_refs = [candidate_ref]
        if not any(ref in ownership for ref in target_refs):
            result.append(candidate_ref)
    return result


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
        'propose_case_understanding': 'Describe the local package work units, title hypotheses, split hints, and evidence questions before mapping.',
        'materialize_queries': 'Create Bangumi subject-search query cards from OrchestratorAgent-provided clean title or alias queries.',
        'execute_evidence': 'Execute visible evidence menu requests through the Bangumi evidence broker.',
        'propose_case_resolution_ledger': 'Write a package-level resolution ledger: what each local row/unit is, its intended outcome, target refs or missing evidence. Use MDR row_ref/local_ref for draft rows, or plan_row_refs=RSP* for Agent-recorded split plan rows. Fixed layer only validates and compiles explicit ledger intent.',
        'propose_mapping_intents': 'Write OrchestratorAgent semantic mapping intents; the fixed layer compiles them into internal draft patches or returns missing evidence.',
        'update_notebook': 'Persist notebook-only notes after mapping work is settled.',
        'reconsider_split': 'Ask fixed layer to reconsider child-case boundaries when a large or mixed package remains unresolved.',
        'split_into_child_cases': 'Explicitly split a large or mixed local package into child cases and run those child sessions. Use LF file refs or LG local group refs from local_main_file_groups; the fixed layer expands/validates refs, non-overlap, and coverage mode.',
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
You investigate like a careful human in one continuous case session: understand the local package, form title/work-unit hypotheses, materialize clean Bangumi title/alias queries, request evidence, write semantic mapping intents, self-review, and only finish when fixed-layer verification can succeed or evidence is genuinely exhausted. Notebook updates are trailing bookkeeping, not a substitute for semantic mapping or evidence.

Use native function tools for actions. Do not emit business mapping JSON directly.
Choose one tool call per turn.
The fixed layer will reject hidden refs, stale duplicate actions, budget excess, failed accounting, invalid schemas, and mechanical loop/no-op failures.
If a tool is rejected, inspect the rejection and choose a corrected next action.
Repeated finish_case calls without changing open rows are a mistake: if draft accounting is not ready, continue investigation or mapping intents.
The explicit workspace/notebook is the source of truth; do not rely on hidden memory alone.
You own the semantic work. Do not wait for a separate QueryComposer, MappingDraftEditor, or Judge role: provide query texts through materialize_queries, summarize package-level row outcomes through propose_case_resolution_ledger, provide structured semantic decisions through propose_mapping_intents, and explain fail_closed through finish_case.
Usually start by understanding the local package shape, work units, title hypotheses, and open evidence questions from the visible local files. You may choose another tool first when that is the best human-like next step.
For large packages with several clear work units, choose between root ledger and child sessions yourself. split_into_child_cases can either run child sessions or record a boundary plan without running children. Use complete_root_coverage for a full split or selected_child_cases for selected major units while other rows stay in root ledger. The fixed layer validates refs, non-overlap, execution mode, and coverage mode; it does not decide which units deserve child sessions.
If the previous tool observation includes split_case_skeleton_from_work_units, you may copy or edit it, but do not turn packaging extras into full child sessions unless they really need independent context. Human-like handling usually keeps CM/Menu/PV/creditless extras in the root ledger and deep-dives only coherent major season/movie/special units.
For query text, prefer distinctive local title tokens and aliases. Parenthetical title tokens from file labels are stronger than generic words such as Gekijouban/movie/theater alone.
Do not write internal MappingDraftPatch objects. For mapping decisions, say what local row/work unit maps to which visible Bangumi subject/item/span and why; if a visible candidate is wrong, explicitly reject that candidate. The fixed layer compiles those semantic intents into BE/BES draft patches or asks for the missing evidence. For large or mixed packages, prefer a case resolution ledger before low-level mapping retries: every row should say map, target_absent, supplemental, needs_evidence, split_needed, or fail_blocker.
Important product semantics: SP/OVA/OAD/special rows are still Bangumi mapping rows when Bangumi exposes matching BE/BES targets. Use bangumi_target_absent only when your investigated conclusion is that Bangumi has no corresponding target, not merely because a local row is supplemental packaging.
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


def _main_file_overview(workspace: CaseEvidenceWorkspace, *, limit: int = 128) -> list[dict[str, object]]:
    main_refs = list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])
    main_ref_set = set(main_refs)
    ordered_files = [
        card for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '') in main_ref_set or (not main_ref_set and bool(getattr(card, 'is_main', False)))
    ]
    by_ref = {str(getattr(card, 'ref', '') or ''): card for card in ordered_files}
    if main_refs:
        ordered_files = [by_ref[ref] for ref in main_refs if ref in by_ref]
    result: list[dict[str, object]] = []
    for card in ordered_files[:limit]:
        path = str(getattr(card, 'path', '') or getattr(card, 'label', '') or '')
        label = str(getattr(card, 'label', '') or path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1])
        marker_matches = re.findall(
            r'(?i)(?:^|[\[\]\s._-])((?:NC)?OP\d{0,2}|(?:NC)?ED\d{0,2}|SP\d{0,3}|OVA\d{0,3}|OAD\d{0,3}|OAV\d{0,3}|MENU|PV\d{0,3}|CM\d{0,3}|PREVIEW|TRAILER|\d{1,3})(?:$|[\[\]\s._-])',
            label,
        )
        result.append({
            'ref': str(getattr(card, 'ref', '') or ''),
            'label': label[:160],
            'path': path[:220],
            'size_bytes': int(getattr(card, 'size_bytes', 0) or 0),
            'parent_display': str(getattr(card, 'parent_display', '') or '')[:120],
            'visible_tokens': _dedupe([str(match or '') for match in marker_matches])[:8],
        })
    return result


def _main_file_group_overview(workspace: CaseEvidenceWorkspace, *, max_groups: int = 32) -> list[dict[str, object]]:
    main_refs = list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])
    main_ref_set = set(main_refs)
    ordered_files = [
        card for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '') in main_ref_set or (not main_ref_set and bool(getattr(card, 'is_main', False)))
    ]
    by_ref = {str(getattr(card, 'ref', '') or ''): card for card in ordered_files}
    if main_refs:
        ordered_files = [by_ref[ref] for ref in main_refs if ref in by_ref]
    groups: dict[str, dict[str, object]] = {}
    for card in ordered_files:
        ref = str(getattr(card, 'ref', '') or '')
        if not ref:
            continue
        path = str(getattr(card, 'path', '') or getattr(card, 'label', '') or '')
        normalized_path = path.replace('\\', '/')
        parts = [part for part in normalized_path.split('/') if part]
        parent_parts = parts[:-1]
        group_key = '/'.join(parent_parts) if parent_parts else str(getattr(card, 'parent_display', '') or '<root>')
        top_group = parent_parts[0] if parent_parts else group_key
        entry = groups.setdefault(group_key, {
            'group_ref': f'LG{len(groups) + 1}',
            'group_key': group_key[:160],
            'top_group': top_group[:160],
            'file_refs': [],
            'file_ref_count': 0,
            'file_ref_range': [],
            'label_samples': [],
            'marker_samples': [],
        })
        refs = entry['file_refs']
        if isinstance(refs, list):
            refs.append(ref)
            entry['file_ref_count'] = len(refs)
            entry['file_ref_range'] = [refs[0], refs[-1]]
        labels = entry['label_samples']
        if isinstance(labels, list) and len(labels) < 6:
            label = str(getattr(card, 'label', '') or (parts[-1] if parts else path))
            labels.append({'ref': ref, 'label': label[:160]})
        markers = entry['marker_samples']
        if isinstance(markers, list) and len(markers) < 12:
            label_for_markers = str(getattr(card, 'label', '') or path)
            for marker in re.findall(
                r'(?i)(?:^|[\[\]\s._-])((?:NC)?OP\d{0,2}|(?:NC)?ED\d{0,2}|SP\d{0,3}|OVA\d{0,3}|OAD\d{0,3}|OAV\d{0,3}|MENU|PV\d{0,3}|CM\d{0,3}|PREVIEW|TRAILER|\d{1,3})(?:$|[\[\]\s._-])',
                label_for_markers,
            ):
                marker_text = str(marker or '')
                if marker_text and marker_text not in markers:
                    markers.append(marker_text)
                if len(markers) >= 12:
                    break
    return list(groups.values())[:max_groups]


def _local_file_label_samples_for_refs(
    file_by_ref: dict[str, Any],
    file_refs: list[str],
    *,
    limit: int = 8,
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for ref in list(file_refs or []):
        card = file_by_ref.get(str(ref or ''))
        if card is None:
            continue
        path = str(getattr(card, 'path', '') or '')
        label = str(getattr(card, 'label', '') or path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1])
        bracket_title_tokens: list[str] = []
        for token in re.findall(r'\[([^\[\]]{2,80})\]', label):
            normalized = str(token or '').strip()
            folded = normalized.casefold()
            if (
                not normalized
                or folded in {'vcb-studio', 'ma10p_1080p', 'x265_flac', 'x265_flac_aac'}
                or re.fullmatch(r'\d{1,3}', normalized)
                or re.search(r'(?i)(1080p|720p|x26[45]|flac|aac|hevc|avc|ma10p)', normalized)
            ):
                continue
            bracket_title_tokens.append(normalized[:80])
        samples.append({
            'ref': str(ref or ''),
            'label': label[:180],
            'path': path[:240],
            'bracket_title_tokens': _dedupe(bracket_title_tokens)[:4],
        })
        if len(samples) >= limit:
            break
    return samples


def _draft_open_rows_for_agent(workspace: CaseEvidenceWorkspace, *, limit: int = 16) -> list[dict[str, object]]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return []
    latest_blocked_subject_refs_by_local: dict[str, list[str]] = {}
    latest_blocked_item_refs_by_local: dict[str, list[str]] = {}
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict) or audit.get('note') != 'orchestrator_mapping_intents_result':
            continue
        for blocked_intent in list(audit.get('blocked_intents') or []):
            if not isinstance(blocked_intent, dict):
                continue
            local_ref = str(blocked_intent.get('local_ref') or '')
            if not local_ref:
                continue
            latest_blocked_subject_refs_by_local.setdefault(local_ref, [])
            latest_blocked_item_refs_by_local.setdefault(local_ref, [])
            latest_blocked_subject_refs_by_local[local_ref] = _dedupe([
                *latest_blocked_subject_refs_by_local[local_ref],
                *[str(ref or '') for ref in list(blocked_intent.get('subject_refs') or [])],
            ])
            latest_blocked_item_refs_by_local[local_ref] = _dedupe([
                *latest_blocked_item_refs_by_local[local_ref],
                *[str(ref or '') for ref in list(blocked_intent.get('item_refs') or [])],
            ])
        break
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
    target_ownership = _target_ownership_for_agent(workspace)
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
                'subject_title': subject_title_by_ref.get(str(getattr(item, 'subject_ref', '') or ''), ''),
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
                'subject_title': subject_title_by_ref.get(str(getattr(span, 'subject_ref', '') or ''), ''),
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
            span_file_refs = [str(ref or '') for ref in list(getattr(span, 'file_refs', []) or []) if str(ref or '')]
            file_count = int(getattr(span, 'file_ref_count', 0) or len(span_file_refs) or 0)
            local_payload.update({
                'local_ref_kind': 'span',
                'file_ref_count': file_count,
                'file_ref_samples': list(getattr(span, 'file_ref_samples', []) or [])[:8] or span_file_refs[:8],
                'file_label_samples': _local_file_label_samples_for_refs(file_by_ref, span_file_refs, limit=8),
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
            *latest_blocked_subject_refs_by_local.get(local_ref, []),
            *[
                str(target_briefs.get(ref, {}).get('subject_ref') or '')
                for ref in candidate_refs
            ],
        ])
        has_target_side_anchor = bool(
            subject_refs_for_sequences
            or candidate_refs
            or list(getattr(row, 'item_refs', []) or [])
        )
        has_row_evidence_surface = bool(
            candidate_refs
            or list(getattr(row, 'subject_refs', []) or [])
            or latest_blocked_subject_refs_by_local.get(local_ref, [])
            or list(getattr(row, 'item_refs', []) or [])
            or list(getattr(row, 'requested_request_types', []) or [])
        )
        all_visible_subject_refs = _dedupe([
            str(getattr(item, 'subject_ref', '') or '')
            for item in list(getattr(workspace, 'bangumi_items', []) or [])
            if str(getattr(item, 'subject_ref', '') or '')
        ])
        # Evidence surface only: expose all visible same-count item sequences so
        # the agent can recover from an earlier wrong subject hypothesis.
        if has_target_side_anchor:
            subject_refs_for_sequences = _dedupe([*subject_refs_for_sequences, *all_visible_subject_refs])
        if not subject_refs_for_sequences and not has_row_evidence_surface:
            subject_refs_for_sequences = all_visible_subject_refs
        has_visible_special_items = any(
            str(getattr(item, 'item_kind', '') or '') in {'special', 'movie'}
            and str(getattr(item, 'subject_ref', '') or '') in set(subject_refs_for_sequences)
            for item in list(getattr(workspace, 'bangumi_items', []) or [])
        )
        sequence_filters: list[tuple[str, set[str]]] = [('regular', {'episode', 'regular', 'unknown', ''})]
        if has_visible_special_items:
            sequence_filters.append(('special', {'special', 'movie'}))
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
                occupied_refs = [ref for ref in refs if ref in target_ownership]
                item_ref_limit = _sequence_item_ref_limit(file_count)
                all_item_refs_unowned = bool(refs and not occupied_refs)
                visible_subject_item_sequences.append({
                    'subject_ref': subject_ref,
                    'subject_title': subject_title_by_ref.get(subject_ref, ''),
                    'sequence_kind': sequence_kind,
                    'item_refs': refs[:item_ref_limit],
                    'item_ref_count': len(refs),
                    'matches_local_file_count': bool(file_count and len(refs) == file_count),
                    'item_refs_truncated': len(refs) > item_ref_limit,
                    'unowned_item_ref_count': len([ref for ref in refs if ref not in target_ownership]),
                    'occupied_item_refs': occupied_refs[:12],
                    'owner_row_refs': _dedupe([str(target_ownership[ref].get('owner_row_ref') or '') for ref in occupied_refs])[:8],
                    'all_item_refs_unowned': all_item_refs_unowned,
                    'mapping_legality': 'available' if all_item_refs_unowned else 'occupied_by_existing_rows',
                    'sort_start': getattr(ordered_items[0], 'sort', None),
                    'sort_end': getattr(ordered_items[min(len(refs), len(ordered_items)) - 1], 'sort', None) if refs else None,
                    'title_samples': [
                        str(getattr(item, 'title', '') or getattr(item, 'name_cn', '') or getattr(item, 'name', '') or '')
                        for item in ordered_items[: _sequence_title_sample_limit(file_count)]
                    ],
                })
                if len(visible_subject_item_sequences) >= 6:
                    break
            if len(visible_subject_item_sequences) >= 6:
                break
        protocol_warning = ''
        recommended_exit = ''
        same_count_sequences = [
            sequence for sequence in visible_subject_item_sequences
            if isinstance(sequence, dict) and bool(sequence.get('matches_local_file_count'))
        ]
        matching_sequences = [
            sequence for sequence in same_count_sequences
            if not bool(sequence.get('item_refs_truncated'))
            and int(sequence.get('unowned_item_ref_count') or 0) == int(sequence.get('item_ref_count') or 0)
        ]
        occupied_same_count_sequences = [
            sequence for sequence in same_count_sequences
            if sequence not in matching_sequences
        ]
        occupied_same_count_owner_rows = _dedupe([
            str(owner or '')
            for sequence in occupied_same_count_sequences
            for owner in list(sequence.get('owner_row_refs') or [])
            if str(owner or '')
        ])
        intent_examples: list[dict[str, object]] = []
        for sequence in matching_sequences[:2]:
            item_refs = [str(ref or '') for ref in list(sequence.get('item_refs') or []) if str(ref or '')]
            if item_refs:
                intent_examples.append({
                    'decision': 'map_regular_span',
                    'local_ref': local_ref,
                    'chosen_subject_ref': str(sequence.get('subject_ref') or ''),
                    'item_refs': item_refs,
                    'support_refs_should_include': _dedupe([local_ref, str(sequence.get('subject_ref') or ''), *item_refs[:6]]),
                    'when_to_use': 'only if you judge this visible same-count Bangumi item sequence semantically matches the local row',
                })
        singleton_candidate_refs = [
            ref for ref in candidate_refs
            if ref not in target_ownership
            and str(target_briefs.get(ref, {}).get('target_kind') or '') == 'item'
            and str(target_briefs.get(ref, {}).get('item_kind') or '') in {'movie', 'special'}
        ]
        multi_singleton_candidate_pool = {
            'choose_exactly': file_count,
            'candidate_item_refs': singleton_candidate_refs[:12],
            'candidate_target_briefs': [target_briefs.get(ref, {'ref': ref}) for ref in singleton_candidate_refs[:12]],
            'valid_intent_shape': 'map_regular_span with item_refs containing exactly one BE per local file',
        } if file_count > 1 and len(singleton_candidate_refs) >= file_count else {}
        if multi_singleton_candidate_pool:
            intent_examples.append({
                'decision': 'map_regular_span',
                'local_ref': local_ref,
                'item_refs': f'choose exactly {file_count} BE refs from multi_singleton_candidate_pool.candidate_item_refs',
                'support_refs_should_include': _dedupe([local_ref, *singleton_candidate_refs[: min(len(singleton_candidate_refs), file_count)]]),
                'when_to_use': 'only if you judge the row is a sequence of singleton movie/special targets rather than one regular episode span',
            })
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
                    'or use target_absent/supplemental if you judge the visible candidates do not correspond'
                )
            elif file_count > 1:
                recommended_exit = (
                    'for this multi-file row, use map_regular_span with visible item_refs that cover the row, '
                    'or use target_absent/supplemental if you judge the visible candidates do not correspond'
                )
                if multi_singleton_candidate_pool:
                    recommended_exit = (
                        'if this row is multiple singleton movie/special targets, choose exactly one visible BE per local file '
                        'from multi_singleton_candidate_pool with map_regular_span item_refs; repartition if the files need separate rows; '
                        'or use target_absent/supplemental if candidates do not correspond'
                    )
            else:
                recommended_exit = (
                    'choose one visible candidate with a mapping intent; if a candidate is semantically wrong, '
                    'you may reject it or use target_absent/supplemental with a clear reason'
                )
        if occupied_same_count_sequences and not matching_sequences:
            recommended_exit = (
                'same-count visible item sequences exist, but their BE refs are already occupied by existing rows '
                f'{occupied_same_count_owner_rows[:8]}. Do not reuse those occupied targets. If the owner row is semantically wrong, '
                'revise that owner row; otherwise choose a non-overlapping target, repartition/split, request concrete evidence, '
                'or mark target_absent/supplemental if that is your investigated conclusion.'
            )
        if file_count > 1:
            regular_same_count = next((seq for seq in matching_sequences if seq.get('sequence_kind') == 'regular' and int(seq.get('item_ref_count') or 0) == file_count), None)
            special_same_count = next((seq for seq in matching_sequences if seq.get('sequence_kind') == 'special' and int(seq.get('item_ref_count') or 0) == file_count), None)
            if regular_same_count is None and special_same_count is None:
                regular_near = next((seq for seq in matching_sequences if seq.get('sequence_kind') == 'regular' and int(seq.get('item_ref_count') or 0) == file_count - 1), None)
                special_one = next((seq for seq in matching_sequences if seq.get('sequence_kind') == 'special' and int(seq.get('item_ref_count') or 0) == 1), None)
                if regular_near is not None and special_one is not None:
                    recommended_exit = (
                        f'the visible target surface looks like a mixed row: {regular_near.get("subject_title") or regular_near.get("subject_ref")} regular {regular_near.get("item_ref_count")} '
                        f'+ {special_one.get("subject_title") or special_one.get("subject_ref")} special 1. Repartition the local work unit and map the pieces separately, or use target_absent/supplemental for the piece that truly lacks a Bangumi target.'
                    )
        if not recommended_exit and disposition == 'needs_more_evidence':
            if matching_sequences:
                recommended_exit = 'if the same-count visible sequence is semantically correct, use map_regular_span with item_refs; otherwise execute concrete evidence, reject candidates, or state a blocker'
            elif occupied_same_count_sequences:
                recommended_exit = (
                    'same-count visible sequences are already occupied by other rows; do not reuse them. '
                    'Revise target ownership, repartition/split, request concrete evidence, or choose a terminal row outcome.'
                )
            else:
                recommended_exit = 'execute the requested evidence or revise the row with a concrete mapping/exclusion intent'
        elif not recommended_exit:
            recommended_exit = 'map, request evidence, or mark an accepted non-Bangumi/supplemental disposition'
        result.append({
            'row_ref': str(getattr(row, 'row_ref', '') or ''),
            'status': status,
            'disposition': disposition,
            'reason_kind': str(getattr(row, 'reason_kind', '') or ''),
            'selected_target_ref': str(getattr(row, 'selected_target_ref', '') or ''),
            'candidate_target_refs': candidate_refs[:12],
            'candidate_target_briefs': [target_briefs.get(ref, {'ref': ref, 'target_kind': 'unknown'}) for ref in candidate_refs[:12]],
            'candidate_target_conflicts': _candidate_conflicts_for_agent(workspace, candidate_refs),
            'unowned_candidate_target_refs': _unowned_candidate_refs_for_agent(workspace, candidate_refs)[:12],
            'requested_request_types': list(getattr(row, 'requested_request_types', []) or [])[:8],
            'query_hints': list(getattr(row, 'query_hints', []) or [])[:8],
            'subject_refs': list(getattr(row, 'subject_refs', []) or [])[:8],
            'item_refs': list(getattr(row, 'item_refs', []) or [])[:8],
            'latest_blocked_subject_refs': latest_blocked_subject_refs_by_local.get(local_ref, [])[:8],
            'latest_blocked_item_refs': latest_blocked_item_refs_by_local.get(local_ref, [])[:12],
            'visible_subject_item_sequences': visible_subject_item_sequences[:4],
            'multi_singleton_candidate_pool': multi_singleton_candidate_pool,
            'intent_examples': intent_examples,
            'protocol_warning': protocol_warning,
            'recommended_exit': recommended_exit,
            **local_payload,
        })
        if len(result) >= limit:
            break
    return result


def _local_file_refs_for_agent_row(workspace: CaseEvidenceWorkspace, local_ref: str) -> list[str]:
    local_ref = str(local_ref or '')
    if not local_ref:
        return []
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    file_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    if local_ref in span_by_ref:
        return _dedupe([str(ref or '') for ref in list(getattr(span_by_ref[local_ref], 'file_refs', []) or [])])
    if local_ref in file_refs:
        return [local_ref]
    return []


def _outcome_kind_for_agent_row(row: object) -> str:
    disposition = str(getattr(row, 'disposition', '') or '')
    reason_kind = str(getattr(row, 'reason_kind', '') or '')
    status = str(getattr(row, 'status', '') or '')
    if disposition == 'map_to_bangumi':
        return 'mapped'
    if disposition == 'non_bangumi_or_supplemental':
        return 'target_absent' if reason_kind == 'bangumi_target_absent' else 'supplemental'
    if disposition == 'needs_more_evidence':
        return 'needs_more_evidence'
    if disposition == 'unaligned_fail_closed':
        return 'fail_closed'
    if status == 'verified':
        return 'mapped'
    return 'open'


def _global_outcome_projection_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return {
            'has_mapping_draft': False,
            'review_notice': 'No mapping draft is available yet. If you finish now, fixed-layer verification will reject accepted finish.',
        }
    dossier = workspace.to_dossier(round_context='orchestrator_agent_global_outcome_projection')
    accounting = compute_mapping_draft_accounting(draft, dossier)
    file_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    outcome_file_counts: dict[str, int] = {}
    excluded_reason_file_counts: dict[str, int] = {}
    row_summaries: list[dict[str, object]] = []
    for row in list(getattr(draft, 'rows', []) or []):
        local_ref = str(getattr(row, 'local_ref', '') or '')
        row_ref = str(getattr(row, 'row_ref', '') or '')
        file_refs = _local_file_refs_for_agent_row(workspace, local_ref)
        file_count = len(file_refs)
        outcome_kind = _outcome_kind_for_agent_row(row)
        reason_kind = str(getattr(row, 'reason_kind', '') or '')
        outcome_file_counts[outcome_kind] = outcome_file_counts.get(outcome_kind, 0) + file_count
        if outcome_kind in {'supplemental', 'target_absent'}:
            excluded_reason_file_counts[reason_kind or outcome_kind] = excluded_reason_file_counts.get(reason_kind or outcome_kind, 0) + file_count
        row_summaries.append({
            'row_ref': row_ref,
            'local_ref': local_ref,
            'outcome_kind': outcome_kind,
            'disposition': str(getattr(row, 'disposition', '') or ''),
            'status': str(getattr(row, 'status', '') or ''),
            'reason_kind': reason_kind,
            'selected_target_ref': str(getattr(row, 'selected_target_ref', '') or ''),
            'file_count': file_count,
            'file_ref_samples': file_refs[:8],
            'file_label_samples': _local_file_label_samples_for_refs(file_by_ref, file_refs, limit=6),
            'support_ref_count': len(list(getattr(row, 'support_refs', []) or [])),
            'candidate_target_refs': list(getattr(row, 'candidate_target_refs', []) or [])[:8],
            'requested_request_types': list(getattr(row, 'requested_request_types', []) or [])[:8],
            'agent_reason': str(getattr(row, 'reason', '') or '')[:240],
        })
    work_units = list(getattr(getattr(workspace, 'case_briefing', None), 'work_units', []) or [])
    split_result_count = sum(
        1
        for audit in list(getattr(workspace, 'judge_request_audits', []) or [])
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_split_into_child_cases_result'
    )
    ledger = getattr(workspace, 'case_resolution_ledger', None)
    main_count = int(getattr(accounting, 'main_file_count', 0) or 0)
    excluded_count = int(getattr(accounting, 'excluded_file_count', 0) or 0)
    mapped_count = int(getattr(accounting, 'mapped_file_count', 0) or 0)
    review_flags: list[str] = []
    if main_count and excluded_count / max(1, main_count) >= 0.5:
        review_flags.append('majority_of_main_files_are_accepted_exclusions')
    if split_result_count == 0 and len(work_units) >= 2:
        review_flags.append('multiple_work_units_without_child_split')
    if ledger is None and len(work_units) >= 2:
        review_flags.append('multiple_work_units_without_case_resolution_ledger')
    if mapped_count == 0 and main_count:
        review_flags.append('no_main_files_are_mapped_to_bangumi')
    return {
        'has_mapping_draft': True,
        'draft_accounting': accounting.model_dump(mode='json'),
        'outcome_file_counts': outcome_file_counts,
        'excluded_reason_file_counts': excluded_reason_file_counts,
        'terminal_row_summaries': row_summaries[:32],
        'terminal_row_count': len(row_summaries),
        'case_understanding_work_unit_count': len(work_units),
        'split_child_result_count': split_result_count,
        'case_resolution_ledger_row_count': len(list(getattr(ledger, 'rows', []) or [])) if ledger is not None else 0,
        'review_flags': review_flags,
        'finish_review_required_fields': [
            'reviewed_outcome_projection=true',
            'acknowledged_mapped_file_count',
            'acknowledged_excluded_file_count',
            'acknowledged_open_file_count',
            'acknowledged_unresolved_count',
            'work_unit_reviews covering each current draft row',
            'final_case_review',
        ],
        'review_notice': (
            'These are mechanical consequences of your current draft, not fixed-layer semantic judgments. '
            'Before accepted finish, explicitly acknowledge the counts and provide one work_unit_reviews entry per draft row. '
            'If the projection does not match your semantic belief, revise understanding, split, query/evidence, ledger, or mapping intents instead of finishing.'
        ),
    }


def _latest_blocked_intents_by_local_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, list[dict[str, object]]]:
    blockers: dict[str, list[dict[str, object]]] = {}
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict) or audit.get('note') != 'orchestrator_mapping_intents_result':
            continue
        for blocked in list(audit.get('blocked_intents') or []):
            if not isinstance(blocked, dict):
                continue
            local_ref = str(blocked.get('local_ref') or '')
            if not local_ref:
                continue
            blockers.setdefault(local_ref, []).append(blocked)
        if blockers:
            return blockers
    return blockers


def _fresh_evidence_request_ids_by_type_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, list[str]]:
    by_type: dict[str, list[str]] = {}
    for summary in _fresh_evidence_menu_summaries_for_agent(workspace):
        if not isinstance(summary, dict):
            continue
        request_type = str(summary.get('request_type') or '')
        request_id = str(summary.get('request_id') or '')
        if not request_type or not request_id:
            continue
        by_type.setdefault(request_type, []).append(request_id)
    return by_type


def _request_summary_source_refs_for_agent(summary: dict[str, object]) -> list[str]:
    return _dedupe([
        str(ref or '')
        for ref in list(summary.get('source_refs') or [])
        if str(ref or '')
    ])


def _matching_fresh_request_ids_for_agent(
    summaries: list[dict[str, object]],
    *,
    requested_types: list[str],
    subject_refs: list[str],
    workspace: CaseEvidenceWorkspace | None = None,
) -> tuple[list[str], list[str]]:
    compatible_types = _compatible_executable_request_types({
        str(value or '')
        for value in list(requested_types or [])
        if str(value or '')
    })
    if not compatible_types:
        return [], []
    requested_subject_refs = {
        str(ref or '')
        for ref in list(subject_refs or [])
        if str(ref or '')
    }
    matching_ids: list[str] = []
    matching_types: list[str] = []
    for summary in list(summaries or []):
        if not isinstance(summary, dict):
            continue
        request_id = str(summary.get('request_id') or '')
        request_type = str(summary.get('request_type') or '')
        if not request_id or request_type not in compatible_types:
            continue
        if request_id.startswith('REQ_NEUTRAL_'):
            continue
        if requested_subject_refs and not requested_subject_refs.intersection(_request_summary_source_refs_for_agent(summary)):
            continue
        matching_ids.append(request_id)
        matching_types.append(request_type)
    if workspace is not None and requested_subject_refs:
        completed_or_failed = {
            str(ref or '')
            for ref in [
                *list(getattr(getattr(workspace, 'plan_state', None), 'completed_menu_request_ids', []) or []),
                *list(getattr(getattr(workspace, 'plan_state', None), 'failed_menu_request_ids', []) or []),
            ]
            if str(ref or '')
        }
        visible_subject_refs = {
            str(getattr(subject, 'ref', '') or '')
            for subject in list(getattr(workspace, 'bangumi_subjects', []) or [])
            if str(getattr(subject, 'ref', '') or '')
        }
        for subject_ref in sorted(requested_subject_refs):
            if subject_ref not in visible_subject_refs:
                continue
            if 'subject_lookup' in compatible_types:
                request_id = f'REQ_SUBJECT_LOOKUP_{subject_ref}'
                if request_id not in completed_or_failed:
                    matching_ids.append(request_id)
                    matching_types.append('subject_lookup')
            if 'episode_list' in compatible_types:
                request_id = f'REQ_EPISODE_LIST_{subject_ref}'
                if request_id not in completed_or_failed:
                    matching_ids.append(request_id)
                    matching_types.append('episode_list')
    return _dedupe(matching_ids), _dedupe(matching_types)


def _latest_blocked_evidence_agenda_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    latest: dict[str, object] | None = None
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note in {'orchestrator_mapping_intents_result', 'orchestrator_case_resolution_ledger_result'}:
            latest = audit
            break
    if latest is None:
        return {
            'active': False,
            'source_note': '',
            'requested_request_types': [],
            'subject_refs': [],
            'matching_executable_request_ids': [],
            'matching_executable_request_types': [],
            'blocked_rows': [],
            'suggested_execute_evidence_args': {},
            'protocol': 'No latest blocked mapping/ledger evidence agenda is recorded.',
        }

    requested_types = _dedupe([
        str(value or '')
        for value in list(latest.get('requested_evidence') or [])
        if str(value or '')
    ])
    fresh_summaries = _fresh_evidence_menu_summaries_for_agent(workspace)
    blocked_items: list[dict[str, object]] = []
    for source_key, source_kind in (
        ('blocked_ledger_rows', 'ledger'),
        ('blocked_intents', 'mapping_intent'),
    ):
        for item in list(latest.get(source_key) or []):
            if isinstance(item, dict):
                copied = dict(item)
                copied['agenda_source'] = source_kind
                blocked_items.append(copied)

    row_agendas: list[dict[str, object]] = []
    all_subject_refs: list[str] = []
    all_matching_request_ids: list[str] = []
    all_matching_request_types: list[str] = []
    for item in blocked_items[:16]:
        row_requested_types = _dedupe([
            *requested_types,
            *[
                str(value or '')
                for value in list(item.get('requested_request_types') or [])
                if str(value or '')
            ],
        ])
        row_subject_refs = _dedupe([
            str(ref or '')
            for ref in list(item.get('subject_refs') or [])
            if str(ref or '')
        ])
        matching_ids, matching_types = _matching_fresh_request_ids_for_agent(
            fresh_summaries,
            requested_types=row_requested_types,
            subject_refs=row_subject_refs,
            workspace=workspace,
        )
        all_subject_refs.extend(row_subject_refs)
        all_matching_request_ids.extend(matching_ids)
        all_matching_request_types.extend(matching_types)
        next_action = 'revise_mapping_or_ledger'
        if matching_ids:
            next_action = 'execute_evidence'
        elif row_requested_types:
            next_action = 'materialize_query_or_revise_evidence_request'
        row_agendas.append({
            'agenda_source': str(item.get('agenda_source') or ''),
            'ledger_row_ref': str(item.get('ledger_row_ref') or ''),
            'intent_ref': str(item.get('intent_ref') or ''),
            'row_ref': str(item.get('row_ref') or ''),
            'local_ref': str(item.get('local_ref') or ''),
            'outcome_or_decision': str(item.get('outcome') or item.get('decision') or ''),
            'issue_codes': _dedupe([str(code or '') for code in list(item.get('issue_codes') or []) if str(code or '')]),
            'requested_request_types': row_requested_types,
            'subject_refs': row_subject_refs,
            'matching_executable_request_ids': matching_ids[:12],
            'matching_executable_request_types': matching_types,
            'recommended_next_tool': next_action,
            'recommended_next_observation': str(item.get('recommended_next_observation') or ''),
            'ref_namespace_reminder': (
                'BS* refs are subjects for chosen_subject_ref/subject_refs. '
                'BE* refs are items for chosen_item_ref/item_refs. '
                'BES* refs are spans for chosen_span_ref.'
            ),
        })

    if not row_agendas and requested_types:
        matching_ids, matching_types = _matching_fresh_request_ids_for_agent(
            fresh_summaries,
            requested_types=requested_types,
            subject_refs=[],
            workspace=workspace,
        )
        all_matching_request_ids.extend(matching_ids)
        all_matching_request_types.extend(matching_types)

    matching_request_ids = _dedupe(all_matching_request_ids)
    matching_request_types = _dedupe(all_matching_request_types)
    subject_refs = _dedupe(all_subject_refs)
    return {
        'active': bool(requested_types or row_agendas),
        'source_note': str(latest.get('note') or ''),
        'status': str(latest.get('status') or ''),
        'requested_request_types': requested_types,
        'subject_refs': subject_refs,
        'matching_executable_request_ids': matching_request_ids[:16],
        'matching_executable_request_types': matching_request_types,
        'blocked_row_count': len(row_agendas),
        'blocked_rows': row_agendas,
        'suggested_execute_evidence_args': ({
            'selected_menu_request_ids': matching_request_ids[:8],
            'requested_request_types': requested_types,
            'subject_refs': subject_refs,
        } if matching_request_ids else {}),
        'protocol': (
            'If matching_executable_request_ids is non-empty and you are not making a new terminal semantic decision '
            'from visible BE/BES refs, call execute_evidence with suggested_execute_evidence_args before retrying the same mapping or ledger intent.'
        ),
    }


def _row_outcome_closure_for_agent(workspace: CaseEvidenceWorkspace) -> list[dict[str, object]]:
    rows = _draft_open_rows_for_agent(workspace, limit=16)
    latest_blockers = _latest_blocked_intents_by_local_for_agent(workspace)
    fresh_by_type = _fresh_evidence_request_ids_by_type_for_agent(workspace)
    fresh_types = set(fresh_by_type)
    result: list[dict[str, object]] = []
    allowed_reason_kinds = sorted(ALLOWED_SUPPLEMENTAL_REASON_KINDS)
    for row in rows:
        if not isinstance(row, dict):
            continue
        local_ref = str(row.get('local_ref') or '')
        row_ref = str(row.get('row_ref') or '')
        file_count = int(row.get('file_ref_count') or 0)
        candidate_refs = [str(ref or '') for ref in list(row.get('candidate_target_refs') or []) if str(ref or '')]
        sequences = [
            sequence for sequence in list(row.get('visible_subject_item_sequences') or [])
            if isinstance(sequence, dict)
        ]
        matching_sequences = [
            sequence for sequence in sequences
            if bool(sequence.get('matches_local_file_count'))
            and list(sequence.get('item_refs') or [])
            and not bool(sequence.get('item_refs_truncated'))
            and int(sequence.get('unowned_item_ref_count') or 0) == int(sequence.get('item_ref_count') or 0)
        ]
        occupied_matching_sequences = [
            sequence for sequence in sequences
            if bool(sequence.get('matches_local_file_count'))
            and list(sequence.get('item_refs') or [])
            and sequence not in matching_sequences
        ]
        blockers = list(latest_blockers.get(local_ref, []) or [])
        blocker_codes = _dedupe([
            str(code or '')
            for blocked in blockers
            for code in list(blocked.get('issue_codes') or [])
            if str(code or '')
        ])
        requested_types = _dedupe([
            str(value or '')
            for value in list(row.get('requested_request_types') or [])
            if str(value or '')
        ])
        compatible_types = _compatible_executable_request_types(set(requested_types))
        matching_request_ids = _dedupe([
            request_id
            for request_type in sorted(compatible_types & fresh_types)
            for request_id in fresh_by_type.get(request_type, [])
        ])
        valid_intent_templates: list[dict[str, object]] = []
        if file_count == 1:
            valid_intent_templates.append({
                'decision': 'map_explicit_item',
                'required_fields': ['local_ref', 'chosen_item_ref=visible BE*', 'support_refs including local_ref and BE*'],
                'use_when': 'only if the chosen visible BE item semantically matches this singleton row',
            })
        if any(str(ref).startswith('BES') for ref in candidate_refs):
            valid_intent_templates.append({
                'decision': 'map_regular_span',
                'required_fields': ['local_ref', 'chosen_span_ref=visible BES*', 'support_refs including local_ref and BES*'],
                'use_when': 'only if the visible BES span semantically matches this row and its count/order is correct',
            })
        if matching_sequences:
            valid_intent_templates.append({
                'decision': 'map_regular_span',
                'required_fields': ['local_ref', 'chosen_subject_ref', 'item_refs copied from one matching visible_subject_item_sequences entry', 'support_refs'],
                'matching_sequence_count': len(matching_sequences),
                'use_when': 'only if one same-count visible BE item sequence semantically matches this row',
            })
        if occupied_matching_sequences:
            valid_intent_templates.append({
                'decision': 'resolve_target_ownership_before_mapping',
                'occupied_same_count_sequence_count': len(occupied_matching_sequences),
                'owner_row_refs': _dedupe([
                    str(owner or '')
                    for sequence in occupied_matching_sequences
                    for owner in list(sequence.get('owner_row_refs') or [])
                    if str(owner or '')
                ])[:8],
                'use_when': 'same-count sequences are visible but already occupied; revise owner rows, choose non-overlapping targets, repartition/split, request concrete evidence, or choose a terminal row outcome',
            })
        if candidate_refs:
            valid_intent_templates.append({
                'decision': 'reject_candidate',
                'required_fields': ['local_ref', 'chosen_item_ref/item_refs/target_refs naming visible candidate refs to reject', 'support_refs'],
                'use_when': 'when visible candidate refs are semantically wrong for this row',
            })
        if matching_request_ids:
            valid_intent_templates.append({
                'decision': 'needs_more_evidence',
                'paired_tool': 'execute_evidence',
                'matching_executable_request_ids': matching_request_ids[:12],
                'use_when': 'only if these fresh evidence requests answer a concrete missing fact for this row',
            })
        valid_intent_templates.append({
            'decision': 'mark_non_bangumi_or_supplemental',
            'required_fields': ['local_ref', 'reason_kind', 'support_refs including local_ref or covered LF refs'],
            'allowed_reason_kinds': allowed_reason_kinds,
            'use_when': 'when your semantic conclusion is that the row is accepted accounting but not mapped to Bangumi',
        })
        valid_intent_templates.append({
            'decision': 'mark_unaligned_fail_closed',
            'required_fields': ['local_ref', 'reason_kind from allowed_unaligned_reason_kinds', 'support_refs'],
            'use_when': 'only when this row should make the case fail_closed rather than accepted',
        })
        must_not_repeat: list[str] = []
        if 'invalid_explicit_multi_file_mapping' in blocker_codes:
            must_not_repeat.append('do not use map_explicit_item with one BE item for this multi-file row')
        if 'item_ref_count_mismatch' in blocker_codes:
            must_not_repeat.append('do not use a BE item_refs list whose length differs from file_ref_count')
        if 'target_span_not_detail_equivalent' in blocker_codes and not matching_request_ids:
            must_not_repeat.append('do not repeat the same unavailable target_span request; use visible item_refs, another visible target, or a terminal row outcome')
        if 'invalid_reason_kind' in blocker_codes:
            must_not_repeat.append('do not invent reason_kind values; use one allowed_supplemental_reason_kinds value exactly')
        result.append({
            'row_ref': row_ref,
            'local_ref': local_ref,
            'file_ref_count': file_count,
            'latest_blocker_issue_codes': blocker_codes,
            'matching_executable_request_ids': matching_request_ids[:12],
            'valid_intent_templates': valid_intent_templates,
            'must_not_repeat': must_not_repeat,
            'closure_rule': (
                'This row must move to one concrete outcome: legal Bangumi mapping, accepted '
                'target_absent/supplemental, concrete fresh evidence, or real fail_closed. '
                'The fixed layer only validates refs/count/accounting; it does not choose the semantic outcome.'
            ),
        })
    return result


def _target_ref_brief_for_agent(
    workspace: CaseEvidenceWorkspace,
    ref: str,
    subject_title_by_ref: dict[str, str] | None = None,
) -> dict[str, object]:
    value = str(ref or '')
    subject_titles = dict(subject_title_by_ref or {})
    if not subject_titles:
        subject_titles = {
            str(getattr(subject, 'ref', '') or ''): str(getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', '') or '')
            for subject in list(getattr(workspace, 'bangumi_subjects', []) or [])
            if str(getattr(subject, 'ref', '') or '')
        }
    for subject in list(getattr(workspace, 'bangumi_subjects', []) or []):
        if str(getattr(subject, 'ref', '') or '') == value:
            return {
                'ref': value,
                'ref_kind': 'BS_subject',
                'title': str(getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', '') or '')[:120],
                'source_form_hint': str(getattr(subject, 'source_form_hint', '') or ''),
                'eps': getattr(subject, 'eps', None),
                'total_episodes': getattr(subject, 'total_episodes', None),
            }
    for item in list(getattr(workspace, 'bangumi_items', []) or []):
        if str(getattr(item, 'ref', '') or '') == value:
            subject_ref = str(getattr(item, 'subject_ref', '') or '')
            return {
                'ref': value,
                'ref_kind': 'BE_item',
                'subject_ref': subject_ref,
                'subject_title': subject_titles.get(subject_ref, '')[:120],
                'item_kind': str(getattr(item, 'item_kind', '') or ''),
                'sort': getattr(item, 'sort', None),
                'ep': getattr(item, 'ep', None),
                'title': str(getattr(item, 'title', '') or getattr(item, 'name_cn', '') or getattr(item, 'name', '') or '')[:120],
            }
    for span in list(getattr(workspace, 'bangumi_span_cards', []) or []):
        if str(getattr(span, 'ref', '') or '') == value:
            subject_ref = str(getattr(span, 'subject_ref', '') or '')
            return {
                'ref': value,
                'ref_kind': 'BES_span',
                'subject_ref': subject_ref,
                'subject_title': subject_titles.get(subject_ref, '')[:120],
                'item_kind': str(getattr(span, 'item_kind', '') or ''),
                'target_ref_count': int(getattr(span, 'target_ref_count', 0) or len(list(getattr(span, 'target_refs', []) or [])) or 0),
                'target_ref_samples': list(getattr(span, 'target_ref_samples', []) or [])[:8],
                'sort_start': getattr(span, 'sort_start', None),
                'sort_end': getattr(span, 'sort_end', None),
                'ep_start': getattr(span, 'ep_start', None),
                'ep_end': getattr(span, 'ep_end', None),
                'title_samples': list(getattr(span, 'title_samples', []) or [])[:6],
            }
    return {'ref': value, 'ref_kind': 'unknown_or_not_visible'}


def _work_unit_resolution_board_for_agent(workspace: CaseEvidenceWorkspace, *, limit: int = 32) -> dict[str, object]:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return {
            'has_mapping_draft': False,
            'rows': [],
            'protocol': 'No mapping draft exists yet. Use propose_case_understanding to create explicit work units before mapping.',
        }
    file_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    span_by_ref = {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(workspace, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    subject_title_by_ref = {
        str(getattr(subject, 'ref', '') or ''): str(getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', '') or '')
        for subject in list(getattr(workspace, 'bangumi_subjects', []) or [])
        if str(getattr(subject, 'ref', '') or '')
    }
    latest_agenda = _latest_blocked_evidence_agenda_for_agent(workspace)
    agenda_rows_by_local: dict[str, list[dict[str, object]]] = {}
    for item in list(latest_agenda.get('blocked_rows') or []):
        if not isinstance(item, dict):
            continue
        local_ref = str(item.get('local_ref') or '')
        if local_ref:
            agenda_rows_by_local.setdefault(local_ref, []).append(item)
    open_rows_by_local = {
        str(row.get('local_ref') or ''): row
        for row in _draft_open_rows_for_agent(workspace, limit=64)
        if isinstance(row, dict) and str(row.get('local_ref') or '')
    }
    closure_by_local = {
        str(row.get('local_ref') or ''): row
        for row in _row_outcome_closure_for_agent(workspace)
        if isinstance(row, dict) and str(row.get('local_ref') or '')
    }
    outcome_counts: dict[str, int] = {}
    rows: list[dict[str, object]] = []
    for row in list(getattr(draft, 'rows', []) or [])[:limit]:
        row_ref = str(getattr(row, 'row_ref', '') or '')
        local_ref = str(getattr(row, 'local_ref', '') or '')
        file_refs = _local_file_refs_for_agent_row(workspace, local_ref)
        file_count = len(file_refs)
        outcome_kind = _outcome_kind_for_agent_row(row)
        outcome_counts[outcome_kind] = outcome_counts.get(outcome_kind, 0) + file_count
        span = span_by_ref.get(local_ref)
        candidate_refs = _dedupe([
            str(ref or '')
            for ref in list(getattr(row, 'candidate_target_refs', []) or [])
            if str(ref or '')
        ])
        selected_target_ref = str(getattr(row, 'selected_target_ref', '') or '')
        row_agenda = list(agenda_rows_by_local.get(local_ref, []) or [])
        closure = closure_by_local.get(local_ref, {})
        open_row = open_rows_by_local.get(local_ref, {})
        next_actions: list[str] = []
        if row_agenda:
            if any(list(item.get('matching_executable_request_ids') or []) for item in row_agenda if isinstance(item, dict)):
                next_actions.append('execute_evidence_with_matching_request_ids')
            next_actions.append('revise_ledger_or_mapping_intent')
        if outcome_kind in {'open', 'needs_more_evidence', 'fail_closed'}:
            if closure:
                next_actions.extend([
                    str(template.get('decision') or '')
                    for template in list(closure.get('valid_intent_templates') or [])
                    if isinstance(template, dict) and str(template.get('decision') or '')
                ])
            if not next_actions:
                next_actions.extend(['propose_mapping_intents', 'execute_evidence_if_needed'])
        current_target_refs = _dedupe([
            selected_target_ref,
            *candidate_refs,
            *[str(ref or '') for ref in list(getattr(row, 'subject_refs', []) or [])],
            *[str(ref or '') for ref in list(getattr(row, 'item_refs', []) or [])],
        ])
        rows.append({
            'row_ref': row_ref,
            'local_ref': local_ref,
            'local_ref_kind': str(getattr(row, 'local_ref_kind', '') or ''),
            'file_count': file_count,
            'file_ref_samples': file_refs[:12],
            'file_label_samples': _local_file_label_samples_for_refs(file_by_ref, file_refs, limit=8),
            'title_cues': list(getattr(span, 'title_cues', []) or [])[:8] if span is not None else [],
            'span_scope': str(getattr(span, 'span_scope', '') or '') if span is not None else '',
            'current_outcome_kind': outcome_kind,
            'disposition': str(getattr(row, 'disposition', '') or ''),
            'status': str(getattr(row, 'status', '') or ''),
            'reason_kind': str(getattr(row, 'reason_kind', '') or ''),
            'selected_target_ref': selected_target_ref,
            'selected_target_brief': _target_ref_brief_for_agent(workspace, selected_target_ref, subject_title_by_ref) if selected_target_ref else {},
            'candidate_target_refs': candidate_refs[:16],
            'candidate_target_briefs': [_target_ref_brief_for_agent(workspace, ref, subject_title_by_ref) for ref in candidate_refs[:12]],
            'target_ownership_conflicts': _candidate_conflicts_for_agent(workspace, [selected_target_ref, *candidate_refs]),
            'unowned_candidate_target_refs': _unowned_candidate_refs_for_agent(workspace, candidate_refs)[:16],
            'subject_refs': list(getattr(row, 'subject_refs', []) or [])[:12],
            'subject_briefs': [_target_ref_brief_for_agent(workspace, ref, subject_title_by_ref) for ref in list(getattr(row, 'subject_refs', []) or [])[:8]],
            'item_refs': list(getattr(row, 'item_refs', []) or [])[:16],
            'requested_request_types': list(getattr(row, 'requested_request_types', []) or [])[:12],
            'query_hints': list(getattr(row, 'query_hints', []) or [])[:12],
            'visible_same_count_sequences': [
                sequence for sequence in list(open_row.get('visible_subject_item_sequences') or [])
                if isinstance(sequence, dict) and bool(sequence.get('matches_local_file_count'))
            ][:4],
            'latest_blocked_evidence_agenda_rows': row_agenda[:4],
            'closure_templates': list(closure.get('valid_intent_templates') or [])[:6] if isinstance(closure, dict) else [],
            'must_not_repeat': list(closure.get('must_not_repeat') or [])[:8] if isinstance(closure, dict) else [],
            'recommended_next_actions': _dedupe(next_actions)[:8],
            'agent_reason': str(getattr(row, 'reason', '') or '')[:360],
            'ref_namespace_reminder': 'local_ref uses LF/LS; chosen_subject_ref uses BS; chosen_item_ref/item_refs use BE; chosen_span_ref uses BES.',
        })
    return {
        'has_mapping_draft': True,
        'row_count': len(list(getattr(draft, 'rows', []) or [])),
        'shown_row_count': len(rows),
        'outcome_file_counts': outcome_counts,
        'latest_blocked_evidence_agenda_active': bool(latest_agenda.get('active')),
        'latest_matching_executable_request_ids': list(latest_agenda.get('matching_executable_request_ids') or [])[:16],
        'rows': rows,
        'protocol': (
            'This is the human-readable case board. It is a factual workspace view, not a fixed-layer semantic verdict. '
            'For each row/work unit, choose the next semantic action yourself: split, gather listed evidence, map visible targets, '
            'mark accepted target_absent/supplemental, or finish fail_closed with a real blocker.'
        ),
    }


def _work_unit_resolution_board_focus_for_agent(
    workspace: CaseEvidenceWorkspace,
    refs: list[str],
    *,
    limit: int = 6,
) -> dict[str, object]:
    requested_refs = _dedupe([str(ref or '') for ref in list(refs or []) if str(ref or '')])
    if not requested_refs:
        return {'refs': [], 'rows': []}
    ref_set = set(requested_refs)
    board = _work_unit_resolution_board_for_agent(workspace)
    rows: list[dict[str, object]] = []
    for row in list(board.get('rows') or []):
        if not isinstance(row, dict):
            continue
        row_refs = {
            str(row.get('row_ref') or ''),
            str(row.get('local_ref') or ''),
            *[str(ref or '') for ref in list(row.get('file_ref_samples') or [])],
            *[str(ref or '') for ref in list(row.get('candidate_target_refs') or [])],
            *[str(ref or '') for ref in list(row.get('subject_refs') or [])],
            *[str(ref or '') for ref in list(row.get('item_refs') or [])],
            str(row.get('selected_target_ref') or ''),
        }
        if ref_set & {ref for ref in row_refs if ref}:
            rows.append(row)
            if len(rows) >= limit:
                break
    return {
        'refs': requested_refs[:24],
        'row_count': len(rows),
        'rows': rows,
        'protocol': 'Focused subset of work_unit_resolution_board for the rejected refs; facts only, not a semantic verdict.',
    }


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


def _semantic_decision_call_count_after_latest_evidence_for_agent(workspace: CaseEvidenceWorkspace) -> int:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_evidence_index = -1
    for index, audit in enumerate(audits):
        if not isinstance(audit, dict):
            continue
        if str(audit.get('note') or '') in {
            'planner_selected_menu_request_ids',
            'evidence_menu_resolution',
            'orchestrator_execute_evidence_menu_resolution',
            'orchestrator_execute_evidence',
            'editor_evidence_menu_resolution',
            'planner_batch_result',
            'evidence_batch_result',
        }:
            latest_evidence_index = index
    return sum(
        1
        for audit in audits[latest_evidence_index + 1:]
        if isinstance(audit, dict)
        and str(audit.get('note') or '') in {
            'mapping_draft_editor_called',
            'orchestrator_mapping_intents_result',
            'orchestrator_case_resolution_ledger_result',
        }
    )


def _reconsider_split_already_observed(workspace: CaseEvidenceWorkspace) -> bool:
    return any(
        isinstance(audit, dict)
        and audit.get('note') == 'orchestrator_reconsider_split_observation'
        for audit in list(getattr(workspace, 'judge_request_audits', []) or [])
    )


def _case_understanding_revised_after_latest_reconsider_split(workspace: CaseEvidenceWorkspace) -> bool:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_reconsider_index = -1
    for index in range(len(audits) - 1, -1, -1):
        audit = audits[index]
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_reconsider_split_observation':
            latest_reconsider_index = index
            break
    if latest_reconsider_index < 0:
        return False
    return any(
        isinstance(audit, dict)
        and audit.get('note') == 'case_understanding_revised'
        for audit in audits[latest_reconsider_index + 1:]
    )


def _notebook_requests_case_understanding_revision(workspace: CaseEvidenceWorkspace) -> bool:
    notebook = getattr(workspace, 'investigation_notebook', None)
    if notebook is None:
        return False
    markers = (
        'work_unit_repartition',
        'repartition',
        're-partition',
        'resplit',
        'unit boundary',
        'too broad',
        'mixed unit',
        'mixed row',
    )
    entries: list[Any] = [
        *list(getattr(notebook, 'open_questions', []) or []),
        *list(getattr(notebook, 'next_actions', []) or []),
    ]
    for entry in entries:
        status = str(getattr(entry, 'status', '') or 'open')
        if status != 'open':
            continue
        text = ' '.join([
            str(getattr(entry, 'question_kind', '') or ''),
            str(getattr(entry, 'action_type', '') or ''),
            str(getattr(entry, 'question', '') or ''),
            str(getattr(entry, 'reason', '') or ''),
        ]).casefold()
        if 'case_understanding' in text and any(marker in text for marker in ('revision', 'revise', 'repartition', 'split', 'boundary')):
            return True
        if any(marker in text for marker in markers):
            return True
    return False


def _case_understanding_revision_available(workspace: CaseEvidenceWorkspace) -> bool:
    if getattr(workspace, 'case_briefing', None) is None:
        return False
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return False
    accounting = compute_mapping_draft_accounting(draft, workspace.to_dossier(round_context='orchestrator_agent_case_understanding_revision_gate'))
    if bool(getattr(accounting, 'accepted_accounting_ready', False)):
        return False
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note in {'case_understanding_applied', 'case_understanding_revised'}:
            break
        if note in {'case_understanding_repartition_requested', 'orchestrator_reconsider_split_requested'}:
            break
        if note == 'orchestrator_reconsider_split_observation' and bool(audit.get('repartition_requested')):
            break
        if note == 'case_understanding_rejected':
            issue_codes = {
                str(code or '')
                for code in list(audit.get('issue_codes') or [])
                if str(code or '')
            }
            if 'case_understanding_noop_repartition' in issue_codes:
                return False
    if _notebook_requests_case_understanding_revision(workspace):
        return True
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note in {'case_understanding_applied', 'case_understanding_revised'}:
            break
        if note == 'case_understanding_rejected':
            issue_codes = {
                str(code or '')
                for code in list(audit.get('issue_codes') or [])
                if str(code or '')
            }
            if 'case_understanding_noop_repartition' in issue_codes:
                return False
        if note == 'case_understanding_repartition_requested':
            return True
    if _reconsider_split_already_observed(workspace) and not _case_understanding_revised_after_latest_reconsider_split(workspace):
        return True
    if _mapping_draft_has_terminal_progress(workspace):
        return False
    return False


def _case_understanding_revision_must_run_once(workspace: CaseEvidenceWorkspace) -> bool:
    if not _case_understanding_revision_available(workspace):
        return False
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note == 'case_understanding_rejected':
            issue_codes = {
                str(code or '')
                for code in list(audit.get('issue_codes') or [])
                if str(code or '')
            }
            if 'case_understanding_noop_repartition' in issue_codes:
                return False
        if note == 'case_understanding_revised':
            return False
        if note == 'case_understanding_repartition_requested':
            return True
        if note in {'case_understanding_applied'}:
            return False
    return False


def _split_decision_required_for_agent(workspace: CaseEvidenceWorkspace) -> bool:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_required_index = max(
        [
            index for index, audit in enumerate(audits)
            if isinstance(audit, dict) and audit.get('note') == 'orchestrator_split_decision_required'
        ],
        default=-1,
    )
    if latest_required_index < 0:
        return False
    for audit in audits[latest_required_index + 1:]:
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if (
            note == 'orchestrator_tool_output_rejected'
            and str(audit.get('tool_name') or '') == 'split_into_child_cases'
            and str(audit.get('reason') or '') == 'split_depth_limit_reached'
        ):
            return False
        if note in {
            'orchestrator_split_into_child_cases_result',
            'orchestrator_selected_child_cases_result',
            'orchestrator_split_decision_deferred_by_mapping_progress',
            'finish_case_fail_closed_verified',
            'finish_case_accepted_accounting_checked',
        }:
            return False
    return True


def _split_decision_observation_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    audits = list(getattr(workspace, 'judge_request_audits', []) or [])
    latest_required: dict[str, object] = {}
    for audit in reversed(audits):
        if isinstance(audit, dict) and audit.get('note') == 'orchestrator_split_decision_required':
            latest_required = audit
            break
    pending = _split_decision_required_for_agent(workspace)
    briefing = getattr(workspace, 'case_briefing', None)
    work_units = list(getattr(briefing, 'work_units', []) or []) if briefing is not None else []
    return {
        'pending': pending,
        'work_unit_count': len(work_units),
        'main_file_count': len(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])),
        'large_multi_unit_package': bool(pending and len(work_units) >= 4 and len(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])) >= 50),
        'reason': str(latest_required.get('reason') or '') if latest_required else '',
        'recommended_next_observation': (
            str(latest_required.get('recommended_next_observation') or '')
            if latest_required
            else ''
        ),
        'recommended_boundary_action': (
            'split_into_child_cases from split_case_skeleton_from_work_units'
            if pending and len(work_units) >= 4 and len(list(getattr(getattr(workspace, 'contract', None), 'main_file_refs', []) or [])) >= 50
            else 'agent chooses split, root ledger, evidence, mapping, or finish according to case state'
        ),
        'split_case_skeleton_from_work_units': (
            latest_required.get('split_case_skeleton_from_work_units')
            if isinstance(latest_required.get('split_case_skeleton_from_work_units'), list)
            else []
        ),
        'protocol': (
            'This is a procedural boundary decision, not a semantic fixed-layer verdict. '
            'If pending is true, the current understanding describes multiple work units without a later split or terminal root resolution. '
            'The agent remains free to split, continue root-level ledger/mapping, gather evidence, or finish if verification can pass. '
            'For large packages, the copyable split_case_skeleton_from_work_units is usually the safest way to move independent work units into child sessions. '
            'Repeating duplicate queries without executing evidence or resolving rows is likely a stall, but the fixed layer will not choose the boundary semantics.'
        ),
    }


def _package_boundary_decision_board_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    split_decision = _split_decision_observation_for_agent(workspace)
    recorded_plan = _recorded_split_plan_for_agent(workspace)
    recorded_plan_active = bool(recorded_plan.get('active'))
    skeleton = [
        item for item in list(split_decision.get('split_case_skeleton_from_work_units') or [])
        if isinstance(item, dict)
    ]
    major_units: list[dict[str, object]] = []
    packaging_units: list[dict[str, object]] = []
    for item in skeleton:
        title_text = ' '.join(
            str(value or '')
            for value in [
                item.get('child_case_ref'),
                item.get('reason'),
                *list(item.get('title_hints') or []),
            ]
        ).casefold()
        count = int(item.get('expanded_main_file_count') or 0)
        is_packaging = any(
            marker in title_text
            for marker in (
                'preview',
                'menu',
                'cm',
                'pv',
                'ncop',
                'nced',
                'creditless',
                'trailer',
                'theater manners',
                'sp extras',
                'supplemental',
            )
        )
        row = {
            'child_case_ref': str(item.get('child_case_ref') or ''),
            'main_group_refs': list(item.get('main_group_refs') or []),
            'expanded_main_file_count': count,
            'expanded_main_file_range': list(item.get('expanded_main_file_range') or []),
            'title_hints': list(item.get('title_hints') or [])[:8],
            'reason': str(item.get('reason') or ''),
            'classification_hint': 'packaging_or_extra_like' if is_packaging else 'major_unit_like',
        }
        if is_packaging:
            packaging_units.append(row)
        else:
            major_units.append(row)
    pending = bool(split_decision.get('pending'))
    main_file_count = int(split_decision.get('main_file_count') or 0)
    return {
        'active': pending,
        'purpose': (
            'Top-level boundary desk for large/mixed packages. It is a factual planning board, not a fixed-layer semantic verdict. '
            'Use it to decide whether this root case should stay in root ledger, run selected child cases, or run a complete split.'
        ),
        'main_file_count': main_file_count,
        'work_unit_count': int(split_decision.get('work_unit_count') or 0),
        'major_unit_like_count': len(major_units),
        'packaging_or_extra_like_count': len(packaging_units),
        'major_unit_like_rows': major_units[:16],
        'packaging_or_extra_like_rows': packaging_units[:16],
        'copyable_split_skeleton': skeleton[:24],
        'human_like_options': [
            {
                'option': 'record_split_plan_only',
                'tool': 'split_into_child_cases',
                'arguments_hint': {
                    'execution_mode': 'record_split_plan_only',
                    'coverage_mode': 'selected_child_cases',
                    'split_cases': 'boundary plan rows to preserve before root ledger or selected child deep-dive',
                },
                'when_useful': 'Use when you want to preserve a human-readable package boundary plan before deciding which units need child execution.',
            },
            {
                'option': 'selected_child_deep_dive',
                'tool': 'split_into_child_cases',
                'arguments_hint': {
                    'execution_mode': 'run_child_cases',
                    'coverage_mode': 'selected_child_cases',
                    'split_cases': 'major season/movie/special units that need independent Bangumi context',
                },
                'when_useful': 'Use for coherent major units that need focused Bangumi recall while extras remain in root ledger.',
            },
            {
                'option': 'root_resolution_ledger',
                'tool': 'propose_case_resolution_ledger',
                'arguments_hint': {
                    'ledger_rows': 'cover every current MDR row exactly once, or cite recorded_split_plan RSP rows with plan_row_refs when resolving recorded work units',
                },
                'when_useful': 'Use when root evidence already supports every row without separate child context.',
            },
            {
                'option': 'complete_split',
                'tool': 'split_into_child_cases',
                'arguments_hint': {
                    'execution_mode': 'run_child_cases',
                    'coverage_mode': 'complete_root_coverage',
                    'split_cases': 'all root main LF refs exactly once',
                },
                'when_useful': 'Use only when every work unit really deserves a full child case; avoid for packaging-heavy extras.',
            },
        ] if pending and not recorded_plan_active else [
            {
                'option': 'run_from_recorded_split_plan',
                'tool': 'split_into_child_cases',
                'arguments_hint': {
                    'execution_mode': 'run_child_cases',
                    'coverage_mode': 'selected_child_cases',
                    'recorded_child_case_refs': 'copy child_case_ref values from recorded_split_plan.child_case_refs',
                    'split_cases': [],
                },
                'when_useful': 'Use selected child_case_ref values from the recorded plan when major units need focused child context.',
            },
            {
                'option': 'root_resolution_ledger',
                'tool': 'propose_case_resolution_ledger',
                'arguments_hint': {
                    'ledger_rows': 'cover every current MDR row exactly once, or cite recorded_split_plan RSP rows with plan_row_refs when resolving recorded work units',
                },
                'when_useful': 'Use when root evidence already supports every row without separate child context.',
            },
            {
                'option': 'selected_child_deep_dive_with_explicit_cases',
                'tool': 'split_into_child_cases',
                'arguments_hint': {
                    'execution_mode': 'run_child_cases',
                    'coverage_mode': 'selected_child_cases',
                    'split_cases': 'explicit child specs if you want to revise the recorded boundary while running children',
                },
                'when_useful': 'Use if the recorded boundary needs revision before child execution.',
            },
        ] if pending else [],
        'self_review_flags': [
            flag for flag, enabled in {
                'large_multi_unit_package_boundary_unresolved': pending and len(skeleton) >= 2,
                'complete_split_may_be_too_expensive_for_packaging_heavy_case': pending and bool(packaging_units) and len(skeleton) >= 4,
                'root_mapping_before_boundary_decision_may_stall': pending and main_file_count >= 20,
            }.items()
            if enabled
        ],
        'protocol': (
            'When active, first use this board to choose a boundary strategy. This is not a forced phase gate: '
            'the fixed layer will still accept any hard-legal tool call. But mapping a very large mixed root without an explicit '
            'boundary strategy is likely to repeat the non-human path seen in audit.'
        ) if pending else 'No boundary decision is pending.',
    }


def _recorded_split_plan_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    latest: dict[str, object] | None = None
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note == 'orchestrator_split_plan_recorded':
            latest = audit
            break
        if note in {
            'finish_case_fail_closed_verified',
            'finish_case_accepted_accounting_checked',
        }:
            break
    if latest is None:
        return {
            'active': False,
            'split_case_count': 0,
            'split_cases': [],
            'protocol': 'No recorded split plan is active.',
        }
    split_cases = [
        item for item in list(latest.get('split_cases') or [])
        if isinstance(item, dict)
    ]
    visible_split_cases: list[dict[str, object]] = []
    for index, item in enumerate(split_cases[:24], start=1):
        row = dict(item)
        row['plan_row_ref'] = str(row.get('plan_row_ref') or f'RSP{index}')
        main_file_refs = [str(ref or '') for ref in list(row.get('main_file_refs') or []) if str(ref or '')]
        supplemental_file_refs = [str(ref or '') for ref in list(row.get('supplemental_file_refs') or []) if str(ref or '')]
        row['main_file_ref_count'] = len(main_file_refs)
        row['main_file_refs'] = main_file_refs[:24]
        row['main_file_refs_truncated'] = len(main_file_refs) > 24
        row['supplemental_file_ref_count'] = len(supplemental_file_refs)
        row['supplemental_file_refs'] = supplemental_file_refs[:24]
        row['supplemental_file_refs_truncated'] = len(supplemental_file_refs) > 24
        visible_split_cases.append(row)
    child_case_refs = list(latest.get('child_case_refs') or [])[:24]
    return {
        'active': True,
        'execution_mode': str(latest.get('execution_mode') or ''),
        'coverage_mode': str(latest.get('coverage_mode') or ''),
        'split_case_count': int(latest.get('split_case_count') or len(split_cases)),
        'child_case_refs': child_case_refs,
        'plan_row_refs': [
            str(row.get('plan_row_ref') or '')
            for row in visible_split_cases
            if str(row.get('plan_row_ref') or '')
        ],
        'split_cases': visible_split_cases,
        'run_selected_child_cases_args_template': {
            'tool': 'split_into_child_cases',
            'execution_mode': 'run_child_cases',
            'coverage_mode': 'selected_child_cases',
            'recorded_child_case_refs': child_case_refs,
            'split_cases': [],
        },
        'missing_main_refs': list(latest.get('missing_main_refs') or [])[:24],
        'duplicate_main_refs': list(latest.get('duplicate_main_refs') or [])[:24],
        'extra_main_refs': list(latest.get('extra_main_refs') or [])[:24],
        'reason': str(latest.get('reason') or ''),
        'next_action_options': [
            'root_resolution_ledger_for_recorded_plan',
            'run_selected_child_cases_from_recorded_plan_using_recorded_child_case_refs',
            'execute_evidence_for_plan_rows',
            'propose_mapping_intents_for_plan_rows',
            'revise_case_understanding_if_plan_boundary_was_wrong',
        ],
        'protocol': (
            'This is the Agent-recorded split plan. It is durable factual memory, not a fixed-layer semantic verdict. '
            'Use it to keep major units and root-ledger extras visible while deciding whether to run selected child cases, '
            'resolve root rows, gather evidence, or revise your own boundary. In propose_case_resolution_ledger, cite these '
            'rows with plan_row_refs like RSP1 instead of inventing row_ref labels such as ROOT/SUPP/TVS/SPC.'
        ),
    }


def _case_desk_priority_for_agent(workspace: CaseEvidenceWorkspace) -> list[dict[str, object]]:
    priorities: list[dict[str, object]] = []
    if bool(_split_decision_observation_for_agent(workspace).get('pending')):
        priorities.append({
            'desk': 'package_boundary_decision_board',
            'why_first': 'The case has multiple work units and no later split/root terminal resolution. Choose a boundary strategy before broad root mapping to avoid large-package stalls.',
            'available_paths': [
                'record_split_plan_only',
                'selected_child_deep_dive',
                'root_resolution_ledger',
                'complete_split',
            ],
            'fixed_layer_semantics': 'none; this is a factual navigation hint, not a semantic verdict or tool gate',
        })
    if bool(_recorded_split_plan_for_agent(workspace).get('active')):
        priorities.append({
            'desk': 'recorded_split_plan',
            'why_first': 'A split boundary plan was recorded without child execution. Keep that plan visible while choosing root ledger, selected child deep-dive, evidence, or mapping intents.',
            'available_paths': [
                'root_resolution_ledger_for_recorded_plan',
                'run_selected_child_cases_from_recorded_plan',
                'execute_evidence_for_plan_rows',
                'propose_mapping_intents_for_plan_rows',
            ],
            'fixed_layer_semantics': 'none; this is Agent-authored boundary memory and mechanical refs/coverage only',
        })
    if getattr(workspace, 'mapping_draft', None) is not None:
        priorities.append({
            'desk': 'work_unit_resolution_board',
            'why_first': 'Use this board to inspect each current row/work unit, target surface, ownership facts, and evidence agenda.',
            'fixed_layer_semantics': 'none; rows summarize current facts and legal shapes only',
        })
    priorities.append({
        'desk': 'recent_tool_observations',
        'why_first': 'Use recent observations to avoid repeating stale evidence, wrong ref namespaces, or non-progressing intent shapes.',
        'fixed_layer_semantics': 'procedural telemetry only',
    })
    return priorities


def _mapping_draft_has_terminal_progress(workspace: CaseEvidenceWorkspace) -> bool:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return False
    for row in list(getattr(draft, 'rows', []) or []):
        disposition = str(getattr(row, 'disposition', '') or '')
        status = str(getattr(row, 'status', '') or '')
        if disposition in {'map_to_bangumi', 'non_bangumi_or_supplemental'} or status == 'verified':
            return True
    return False


def _open_rows_have_actionable_mapping_surface(workspace: CaseEvidenceWorkspace) -> bool:
    for row in _draft_open_rows_for_agent(workspace, limit=64):
        if not isinstance(row, dict):
            continue
        if list(row.get('candidate_target_refs') or []):
            return True
        for sequence in list(row.get('visible_subject_item_sequences') or []):
            if isinstance(sequence, dict) and bool(sequence.get('matches_local_file_count')):
                return True
    return False


def _agent_row_has_actionable_mapping_surface(row: dict[str, object]) -> bool:
    if list(row.get('candidate_target_refs') or []):
        return True
    for sequence in list(row.get('visible_subject_item_sequences') or []):
        if isinstance(sequence, dict) and bool(sequence.get('matches_local_file_count')):
            return True
    return False


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


def _fresh_evidence_menu_summaries_for_agent(workspace: CaseEvidenceWorkspace, *, max_requests: int = 24) -> list[dict[str, object]]:
    menu = build_executable_evidence_menu(workspace, max_requests=max_requests)
    completed_or_failed = {
        str(ref or '')
        for ref in [
            *list(getattr(getattr(workspace, 'plan_state', None), 'completed_menu_request_ids', []) or []),
            *list(getattr(getattr(workspace, 'plan_state', None), 'failed_menu_request_ids', []) or []),
        ]
        if str(ref or '')
    }
    return [
        summary for summary in list(menu.get('prompt_summaries') or [])
        if isinstance(summary, dict)
        and str(summary.get('request_id') or '')
        and str(summary.get('request_id') or '') not in completed_or_failed
    ]


def _remaining_query_evidence_request_ids_for_agent(workspace: CaseEvidenceWorkspace) -> list[str]:
    return [
        str(summary.get('request_id') or '')
        for summary in _fresh_evidence_menu_summaries_for_agent(workspace)
        if isinstance(summary, dict)
        and str(summary.get('request_type') or '') == 'subject_search'
        and str(summary.get('request_id') or '')
    ]


def _target_side_request_summaries_for_agent(summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    target_side_types = {
        'subject_search',
        'subject_lookup',
        'episode_list',
        'episode_detail',
        'target_span',
        'target_window',
        'target_detail',
        'related_expansion',
    }
    return [
        summary for summary in summaries
        if str(summary.get('request_type') or '') in target_side_types
        or str(summary.get('request_id') or '').startswith((
            'REQ_SUBJECT',
            'REQ_EPISODE',
            'REQ_TARGET',
            'REQ_SPECIAL',
            'REQ_RELATED',
        ))
    ]


def _request_types_available_in_summaries(summaries: list[dict[str, object]]) -> set[str]:
    return {
        str(summary.get('request_type') or '')
        for summary in list(summaries or [])
        if str(summary.get('request_type') or '')
    }


_EVIDENCE_REQUEST_TYPE_COMPATIBILITY: dict[str, set[str]] = {
    'subject_search': {'subject_search'},
    'subject_lookup': {'subject_lookup', 'episode_list', 'related_expansion'},
    'related_expansion': {'related_expansion', 'subject_lookup', 'episode_list'},
    'episode_list': {'episode_list', 'subject_lookup', 'related_expansion'},
    'episode_detail': {'episode_detail', 'target_detail', 'target_window', 'episode_list', 'subject_lookup', 'related_expansion'},
    'target_detail': {'target_detail', 'target_window', 'episode_detail', 'episode_list', 'subject_lookup', 'related_expansion'},
    'target_window': {'target_window', 'target_detail', 'episode_detail', 'episode_list', 'subject_lookup', 'related_expansion'},
    'target_span': {'target_span', 'target_window', 'target_detail', 'episode_detail', 'episode_list', 'subject_lookup', 'related_expansion'},
}


def _compatible_executable_request_types(requested_types: set[str]) -> set[str]:
    compatible: set[str] = set()
    for requested_type in set(requested_types or set()):
        value = str(requested_type or '')
        if not value:
            continue
        compatible.update(_EVIDENCE_REQUEST_TYPE_COMPATIBILITY.get(value, {value}))
    return compatible


def _requested_evidence_matches_available(requested_types: set[str], available_types: set[str]) -> bool:
    return bool(_compatible_executable_request_types(requested_types) & set(available_types or set()))


def _latest_blocked_requested_evidence_has_fresh_match(
    workspace: CaseEvidenceWorkspace,
    summaries: list[dict[str, object]],
) -> bool:
    available_types = _request_types_available_in_summaries(summaries)
    if not available_types:
        return False
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if (
            note in {'orchestrator_tool_selected', 'orchestrator_tool_output_rejected'}
            and str(audit.get('tool_name') or '') == 'execute_evidence'
            and audit.get('accepted') is False
            and str(audit.get('reason') or '') in {
                'no_executable_menu_request',
                'stale_or_no_executable_menu_request',
                'evidence_phase_prerequisite_missing',
            }
        ):
            return False
        if note in {
            'orchestrator_materialize_queries_result',
            'case_understanding_applied',
            'case_understanding_revised',
        }:
            return False
        if note not in {'orchestrator_mapping_intents_result', 'orchestrator_case_resolution_ledger_result'}:
            continue
        if int(audit.get('blocked_intent_count') or audit.get('blocked_ledger_row_count') or 0) <= 0:
            return False
        issue_codes = {
            str(code or '')
            for code in [*list(audit.get('blocked_intent_issue_codes') or []), *list(audit.get('blocked_ledger_issue_codes') or [])]
            if str(code or '')
        }
        if issue_codes & {'invalid_explicit_multi_file_mapping', 'item_ref_count_mismatch', 'count_mismatch'}:
            return False
        requested = {
            str(value or '')
            for value in list(audit.get('requested_evidence') or [])
            if str(value or '')
        }
        return _requested_evidence_matches_available(requested, available_types)
    return False


def _open_rows_have_matching_executable_evidence(
    workspace: CaseEvidenceWorkspace,
    summaries: list[dict[str, object]],
) -> bool:
    available_types = _request_types_available_in_summaries(summaries)
    if not available_types:
        return False
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note in {
            'orchestrator_execute_evidence',
            'orchestrator_execute_evidence_menu_resolution',
            'orchestrator_mapping_intents_result',
            'orchestrator_case_resolution_ledger_result',
            'orchestrator_materialize_queries_result',
            'case_understanding_applied',
            'case_understanding_revised',
        }:
            break
        if (
            note in {'orchestrator_tool_selected', 'orchestrator_tool_output_rejected'}
            and str(audit.get('tool_name') or '') == 'execute_evidence'
            and audit.get('accepted') is False
            and str(audit.get('reason') or '') in {
                'no_executable_menu_request',
                'stale_or_no_executable_menu_request',
                'evidence_phase_prerequisite_missing',
            }
        ):
            return False
    for row in _draft_open_rows_for_agent(workspace, limit=64):
        if not isinstance(row, dict):
            continue
        if _agent_row_has_actionable_mapping_surface(row):
            continue
        requested = {
            str(value or '')
            for value in list(row.get('requested_request_types') or [])
            if str(value or '')
        }
        if _requested_evidence_matches_available(requested, available_types):
            return True
    return False


def _draft_has_unresolved_evidence_agenda(workspace: CaseEvidenceWorkspace) -> bool:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return False
    for row in list(getattr(draft, 'rows', []) or []):
        if str(getattr(row, 'disposition', '') or '') in {'map_to_bangumi', 'non_bangumi_or_supplemental'}:
            continue
        if str(getattr(row, 'status', '') or '') == 'verified':
            continue
        if list(getattr(row, 'requested_request_types', []) or []):
            return True
    return False


def _evidence_should_precede_mapping_intents(workspace: CaseEvidenceWorkspace, summaries: list[dict[str, object]]) -> bool:
    available_types = _request_types_available_in_summaries(summaries)
    if not available_types:
        return False
    if _case_understanding_revision_available(workspace):
        return False
    if _latest_blocked_requested_evidence_has_fresh_match(workspace, summaries):
        return True
    if _open_rows_have_actionable_mapping_surface(workspace):
        return False
    if not list(getattr(workspace, 'bangumi_subjects', []) or []) and 'subject_search' in available_types:
        return True
    target_surface_has_items = bool(
        list(getattr(workspace, 'bangumi_items', []) or [])
        or list(getattr(workspace, 'bangumi_span_cards', []) or [])
    )
    if not target_surface_has_items and {'episode_list', 'subject_lookup', 'related_expansion'} & available_types:
        return True
    return False


def _query_materialization_should_precede_other_tools(workspace: CaseEvidenceWorkspace) -> bool:
    if (
        list(getattr(workspace, 'bangumi_subjects', []) or [])
        or list(getattr(workspace, 'bangumi_items', []) or [])
        or list(getattr(workspace, 'bangumi_span_cards', []) or [])
    ):
        return False
    return not any(
        str(getattr(card, 'query_kind', '') or '') == 'subject_search'
        for card in list(getattr(workspace, 'query_cards', []) or [])
    )


def _no_new_evidence_gate_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    fresh_summaries = _fresh_evidence_menu_summaries_for_agent(workspace)
    target_summaries = _target_side_request_summaries_for_agent(fresh_summaries)
    draft = getattr(workspace, 'mapping_draft', None)
    durable_draft_evidence_intent_count = 0
    if draft is not None:
        for row in list(getattr(draft, 'rows', []) or []):
            if str(getattr(row, 'disposition', '') or '') in {'map_to_bangumi', 'non_bangumi_or_supplemental'}:
                continue
            if str(getattr(row, 'status', '') or '') == 'verified':
                continue
            if list(getattr(row, 'requested_request_types', []) or []):
                durable_draft_evidence_intent_count += 1
    notebook = getattr(workspace, 'investigation_notebook', None)
    human_next_action_blocked_no_new_evidence_count = 0
    if notebook is not None:
        for item in [
            *list(getattr(notebook, 'open_questions', []) or []),
            *list(getattr(notebook, 'next_actions', []) or []),
        ]:
            if str(getattr(item, 'status', '') or 'open') != 'open':
                continue
            if list(getattr(item, 'requested_request_types', []) or []) or list(getattr(item, 'query_hints', []) or []):
                human_next_action_blocked_no_new_evidence_count += 1
    return {
        'remaining_target_side_executable_request_count': len(target_summaries),
        'remaining_target_side_executable_request_ids': [
            str(summary.get('request_id') or '') for summary in target_summaries[:12]
        ],
        'durable_draft_evidence_intent_count': durable_draft_evidence_intent_count,
        'human_next_action_blocked_no_new_evidence_count': human_next_action_blocked_no_new_evidence_count,
    }


def _finish_case_available_for_workspace(workspace: CaseEvidenceWorkspace) -> bool:
    draft = getattr(workspace, 'mapping_draft', None)
    if draft is None:
        return False
    dossier = workspace.to_dossier(round_context='orchestrator_agent_finish_tool_gate')
    accounting = compute_mapping_draft_accounting(draft, dossier)
    verifier_result = verify_mapping_draft_accounting(dossier, draft)
    if bool(getattr(accounting, 'accepted_accounting_ready', False)) and bool(getattr(verifier_result, 'passed', False)):
        return True
    if _open_rows_all_terminal_fail_closed(workspace):
        return True
    budget = getattr(workspace, 'budget', None)
    budget_exhausted = bool(
        getattr(budget, 'max_evidence_batches', 0)
        and getattr(budget, 'used_evidence_batches', 0) >= getattr(budget, 'max_evidence_batches', 0)
    )
    if budget_exhausted and _semantic_decision_call_count_after_latest_evidence_for_agent(workspace) > 0:
        return True
    no_new_gate = _no_new_evidence_gate_for_agent(workspace)
    return bool(
        _semantic_decision_call_count_after_latest_evidence_for_agent(workspace) > 0
        and int(no_new_gate.get('remaining_target_side_executable_request_count') or 0) == 0
        and int(no_new_gate.get('durable_draft_evidence_intent_count') or 0) == 0
        and int(no_new_gate.get('human_next_action_blocked_no_new_evidence_count') or 0) == 0
    )


def _compact_notebook_for_agent(notebook: dict[str, object]) -> dict[str, object]:
    if not isinstance(notebook, dict):
        return {}
    typed = notebook.get('investigation_notebook') if isinstance(notebook.get('investigation_notebook'), dict) else {}
    briefing = notebook.get('case_briefing') if isinstance(notebook.get('case_briefing'), dict) else {}
    return {
        'counts': {
            'rounds': notebook.get('rounds'),
            'evidence_requests': notebook.get('evidence_requests'),
            'results_count': len(list(notebook.get('results') or [])) if isinstance(notebook.get('results'), list) else 0,
            'verifier_issue_count': len(list(notebook.get('verifier_issues') or [])) if isinstance(notebook.get('verifier_issues'), list) else 0,
            **(typed.get('counts') if isinstance(typed.get('counts'), dict) else {}),
        },
        'plan_state': notebook.get('plan_state') if isinstance(notebook.get('plan_state'), dict) else {},
        'mapping_draft_summary': notebook.get('mapping_draft_summary') if isinstance(notebook.get('mapping_draft_summary'), dict) else {},
        'case_briefing': {
            'package_shape': briefing.get('package_shape') if isinstance(briefing, dict) else '',
            'summary': briefing.get('summary') if isinstance(briefing, dict) else '',
            'work_unit_count': briefing.get('work_unit_count') if isinstance(briefing, dict) else 0,
            'work_units': list(briefing.get('work_units') or [])[:12] if isinstance(briefing, dict) else [],
            'title_hypotheses': list(briefing.get('title_hypotheses') or [])[:8] if isinstance(briefing, dict) else [],
            'evidence_questions': list(briefing.get('evidence_questions') or [])[:8] if isinstance(briefing, dict) else [],
        },
        'open_questions': list(typed.get('open_questions') or [])[:8] if isinstance(typed, dict) else [],
        'next_actions': list(typed.get('next_actions') or [])[:8] if isinstance(typed, dict) else [],
        'work_unit_states': list(typed.get('work_unit_states') or [])[:12] if isinstance(typed, dict) else [],
        'target_ownership': list(typed.get('target_ownership') or [])[:12] if isinstance(typed, dict) else [],
        'rejected_candidates': list(typed.get('rejected_candidates') or [])[:8] if isinstance(typed, dict) else [],
    }


def _compact_case_resolution_ledger_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    ledger = getattr(workspace, 'case_resolution_ledger', None)
    if ledger is None:
        return {'applied': False, 'row_count': 0, 'rows': [], 'summary': ''}
    rows: list[dict[str, object]] = []
    for row in list(getattr(ledger, 'rows', []) or [])[:64]:
        payload = row.model_dump(mode='json') if hasattr(row, 'model_dump') else dict(row)
        rows.append({
            key: payload.get(key)
            for key in (
                'ledger_row_ref',
                'row_ref',
                'local_ref',
                'local_refs',
                'file_refs',
                'span_refs',
                'role',
                'outcome',
                'chosen_subject_ref',
                'chosen_item_ref',
                'chosen_span_ref',
                'item_refs',
                'target_refs',
                'requested_request_types',
                'query_hints',
                'query_refs',
                'reason_kind',
                'confidence',
                'reason',
            )
            if payload.get(key) not in (None, '', [], {})
        })
    return {
        'applied': True,
        'ledger_ref': str(getattr(ledger, 'ledger_ref', '') or ''),
        'row_count': len(list(getattr(ledger, 'rows', []) or [])),
        'summary': str(getattr(ledger, 'summary', '') or ''),
        'rows': rows,
    }


def _visible_ref_catalog_for_agent(workspace: CaseEvidenceWorkspace) -> dict[str, object]:
    return {
        'local_file_refs': [
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(workspace, 'local_files', []) or [])
            if str(getattr(card, 'ref', '') or '')
        ],
        'local_span_refs': [
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(workspace, 'local_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '')
        ],
        'bangumi_subject_refs': [
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(workspace, 'bangumi_subjects', []) or [])
            if str(getattr(card, 'ref', '') or '')
        ],
        'bangumi_item_refs': [
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(workspace, 'bangumi_items', []) or [])
            if str(getattr(card, 'ref', '') or '')
        ],
        'bangumi_span_refs': [
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(workspace, 'bangumi_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '')
        ],
        'query_refs': [
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(workspace, 'query_cards', []) or [])
            if str(getattr(card, 'ref', '') or '')
        ],
        'rule': 'Only these refs are visible in this turn. Do not guess BE/BS/BES numbers; execute evidence or materialize queries when the needed ref is absent.',
    }


def _visible_item_ref_table_for_agent(workspace: CaseEvidenceWorkspace, *, limit: int = 80) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    subject_title_by_ref = {
        str(getattr(subject, 'ref', '') or ''): str(getattr(subject, 'title', '') or getattr(subject, 'name_cn', '') or getattr(subject, 'name', '') or '')
        for subject in list(getattr(workspace, 'bangumi_subjects', []) or [])
        if str(getattr(subject, 'ref', '') or '')
    }
    for item in list(getattr(workspace, 'bangumi_items', []) or [])[:limit]:
        ref = str(getattr(item, 'ref', '') or '')
        if not ref:
            continue
        subject_ref = str(getattr(item, 'subject_ref', '') or '')
        rows.append({
            'ref': ref,
            'subject_ref': subject_ref,
            'subject_title': subject_title_by_ref.get(subject_ref, '')[:80],
            'item_kind': str(getattr(item, 'item_kind', '') or ''),
            'sort': getattr(item, 'sort', None),
            'ep': getattr(item, 'ep', None),
            'title': str(getattr(item, 'title', '') or getattr(item, 'name_cn', '') or getattr(item, 'name', '') or '')[:100],
        })
    return rows


def _visible_span_ref_table_for_agent(workspace: CaseEvidenceWorkspace, *, limit: int = 40) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for span in list(getattr(workspace, 'bangumi_span_cards', []) or [])[:limit]:
        ref = str(getattr(span, 'ref', '') or '')
        if not ref:
            continue
        rows.append({
            'ref': ref,
            'subject_ref': str(getattr(span, 'subject_ref', '') or ''),
            'target_ref_count': int(getattr(span, 'target_ref_count', 0) or len(list(getattr(span, 'target_refs', []) or [])) or 0),
            'target_ref_samples': list(getattr(span, 'target_ref_samples', []) or [])[:8],
            'sort_start': getattr(span, 'sort_start', None),
            'sort_end': getattr(span, 'sort_end', None),
            'ep_start': getattr(span, 'ep_start', None),
            'ep_end': getattr(span, 'ep_end', None),
            'item_kind': str(getattr(span, 'item_kind', '') or ''),
            'title_samples': list(getattr(span, 'title_samples', []) or [])[:6],
        })
    return rows


def _recent_orchestrator_observations(workspace: CaseEvidenceWorkspace, *, limit: int = 8) -> list[dict[str, object]]:
    interesting_notes = {
        'orchestrator_tool_selected',
        'orchestrator_tool_output_rejected',
        'orchestrator_turn_health',
        'orchestrator_mapping_intents_result',
        'orchestrator_case_resolution_ledger_result',
        'orchestrator_execute_evidence',
        'orchestrator_execute_evidence_menu_resolution',
        'orchestrator_materialize_queries_result',
        'orchestrator_materialize_queries_noop',
        'orchestrator_split_decision_required',
        'orchestrator_selected_child_cases_result',
        'orchestrator_reconsider_split_observation',
        'case_understanding_mapping_draft_accounting',
        'case_understanding_applied',
        'case_understanding_rejected',
        'case_understanding_revised',
        'mapping_draft_initialized',
    }
    observations: list[dict[str, object]] = []
    for audit in reversed(list(getattr(workspace, 'judge_request_audits', []) or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note not in interesting_notes:
            continue
        item: dict[str, object] = {
            'note': note,
        }
        for key in (
            'tool_name',
            'accepted',
            'reason',
            'status',
            'summary',
            'recommended_next_observation',
            'verifier_passed',
            'compiled_patch_count',
            'blocked_intent_count',
            'blocked_intent_issue_codes',
            'blocked_intents',
            'patch_issue_codes',
            'accounting_issue_codes',
            'verifier_issue_codes',
            'issues',
            'missing_main_refs',
            'duplicate_main_refs',
            'extra_main_refs',
            'child_ref_counts',
            'ref_issue_codes',
            'ref_issue_refs',
            'ref_corrections',
            'requested_evidence',
            'selected_menu_request_ids',
            'stale_menu_request_ids',
            'unknown_menu_request_ids',
            'new_query_refs',
            'new_subject_refs',
            'new_item_refs',
            'new_span_refs',
            'turn_count',
            'max_turns',
            'turn_budget_ratio',
            'near_turn_limit_unhealthy',
            'stall_suspected',
            'consecutive_stall_count',
            'workspace_changed',
            'target_surface_changed',
            'matching_requested_evidence_available',
            'required_next_tools',
            'case_resolution_ledger_row_count',
            'ledger_outcome_counts',
            'ledger_compiled_patch_count',
            'ledger_blocked_row_count',
            'blocked_ledger_row_count',
            'blocked_ledger_issue_codes',
            'ledger_requested_evidence_count',
            'blocked_ledger_rows',
            'blocked_intents',
            'requested_evidence_agenda',
            'latest_blocked_evidence_agenda',
            'work_unit_count',
            'main_file_count',
            'non_singleton_work_unit_count',
            'split_decision_required',
            'split_case_skeleton_from_work_units',
            'execution_mode',
            'coverage_mode',
            'split_case_count',
            'child_case_ids',
            'child_statuses',
            'terminal_child_blockers',
        ):
            value = audit.get(key)
            if value not in (None, '', [], {}):
                item[key] = value
        observations.append(item)
        if len(observations) >= limit:
            break
    return list(reversed(observations))


def _allowed_tool_names_for_workspace(workspace: CaseEvidenceWorkspace) -> set[str]:
    _ = workspace
    return set(TOOL_ARG_MODELS.keys())


def build_orchestrator_agent_payload(workspace: CaseEvidenceWorkspace, *, reason: str = '') -> dict[str, object]:
    dossier = workspace.to_dossier(round_context='orchestrator_agent')
    menu = build_executable_evidence_menu(workspace, max_requests=24)
    completed_or_failed_request_ids = {
        str(ref or '')
        for ref in [
            *list(getattr(getattr(workspace, 'plan_state', None), 'completed_menu_request_ids', []) or []),
            *list(getattr(getattr(workspace, 'plan_state', None), 'failed_menu_request_ids', []) or []),
        ]
        if str(ref or '')
    }
    fresh_menu_summaries = [
        summary for summary in list(menu.get('prompt_summaries') or [])
        if isinstance(summary, dict)
        and str(summary.get('request_id') or '')
        and str(summary.get('request_id') or '') not in completed_or_failed_request_ids
    ]
    draft = getattr(workspace, 'mapping_draft', None)
    accounting = compute_mapping_draft_accounting(draft, dossier).model_dump(mode='json') if draft is not None else None
    allowed_tool_names = sorted(_allowed_tool_names_for_workspace(workspace))
    case_understanding_revision_available = _case_understanding_revision_available(workspace)
    recent_turn_health = next(
        (
            observation for observation in reversed(_recent_orchestrator_observations(workspace, limit=12))
            if isinstance(observation, dict) and observation.get('note') == 'orchestrator_turn_health'
        ),
        {},
    )
    visible_target_surface_present = bool(
        list(getattr(workspace, 'bangumi_subjects', []) or [])
        or list(getattr(workspace, 'bangumi_items', []) or [])
        or list(getattr(workspace, 'bangumi_span_cards', []) or [])
    )
    is_child_case = ':' in str(getattr(getattr(workspace, 'header', None), 'case_id', '') or '') or 'derived_from_case_planning_split' in {
        str(value or '') for value in list(getattr(workspace, 'diagnostics', []) or [])
    }
    child_scope = {
        'is_child_case': is_child_case,
        'parent_refs_not_visible': bool(is_child_case),
        'visible_local_file_refs_only': list(getattr(getattr(workspace, 'contract', None), 'allowed_file_refs', []) or []),
        'visible_local_span_refs_only': [
            str(getattr(card, 'ref', '') or '')
            for card in list(getattr(workspace, 'local_span_cards', []) or [])
            if str(getattr(card, 'ref', '') or '')
        ],
        'instruction': (
            'This child case has its own ref scope. Use only current child LF/LS refs from mapping_draft_rows and '
            'local_span_sample; parent/root LS, BS, BE, and BES refs remembered from split planning are invalid unless '
            'they are visible in this payload.'
        ) if is_child_case else '',
    }
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
    return {
        'case_id': workspace.header.case_id,
        'reason': reason,
        'case_desk_priority': _case_desk_priority_for_agent(workspace),
        'budget': {
            'max_evidence_batches': workspace.budget.max_evidence_batches,
            'used_evidence_batches': workspace.budget.used_evidence_batches,
            'max_api_calls_per_case': workspace.budget.max_api_calls_per_case,
            'used_api_calls': workspace.budget.used_api_calls,
            'max_subject_searches': workspace.budget.max_subject_searches,
            'used_subject_searches': workspace.budget.used_subject_searches,
        },
        'visible_refs': dossier.visible_refs.model_dump(mode='json'),
        'visible_ref_catalog': _visible_ref_catalog_for_agent(workspace),
        'local_main_file_groups': _main_file_group_overview(workspace),
        'local_main_file_overview': _main_file_overview(workspace, limit=48),
        'local_file_sample': _sample_cards(list(workspace.local_files or []), limit=10),
        'local_span_sample': _sample_cards(list(workspace.local_span_cards or []), limit=12),
        'child_case_ref_scope': child_scope,
        'query_card_sample': _sample_cards(list(workspace.query_cards or []), limit=12),
        'bangumi_subject_sample': _sample_cards(list(workspace.bangumi_subjects or []), limit=10),
        'bangumi_item_sample': _sample_cards(list(workspace.bangumi_items or []), limit=12),
        'bangumi_span_sample': _sample_cards(list(workspace.bangumi_span_cards or []), limit=12),
        'visible_item_ref_table': _visible_item_ref_table_for_agent(workspace),
        'visible_span_ref_table': _visible_span_ref_table_for_agent(workspace),
        'mapping_draft_rows': draft_rows,
        'draft_accounting': accounting,
        'open_rows_have_actionable_mapping_surface': _open_rows_have_actionable_mapping_surface(workspace),
        'visible_target_surface_present': visible_target_surface_present,
        'case_understanding': {
            'applied': getattr(workspace, 'case_briefing', None) is not None,
            'recommended_before_mapping_when_missing': getattr(workspace, 'case_briefing', None) is None,
            'revision_available_now': case_understanding_revision_available,
            'package_shape': str(getattr(getattr(workspace, 'case_briefing', None), 'package_shape', '') or ''),
            'summary': str(getattr(getattr(workspace, 'case_briefing', None), 'summary', '') or ''),
            'work_unit_count': len(list(getattr(getattr(workspace, 'case_briefing', None), 'work_units', []) or [])),
        },
        'split_decision': _split_decision_observation_for_agent(workspace),
        'package_boundary_decision_board': _package_boundary_decision_board_for_agent(workspace),
        'recorded_split_plan': _recorded_split_plan_for_agent(workspace),
        'case_resolution_ledger': _compact_case_resolution_ledger_for_agent(workspace),
        'global_outcome_projection': _global_outcome_projection_for_agent(workspace),
        'work_unit_resolution_board': _work_unit_resolution_board_for_agent(workspace),
        'open_rows_requiring_agent_action': _draft_open_rows_for_agent(workspace),
        'row_outcome_closure': _row_outcome_closure_for_agent(workspace),
        'latest_blocked_evidence_agenda': _latest_blocked_evidence_agenda_for_agent(workspace),
        'finish_protocol': {
            'accepted_finish_allowed_now': bool((accounting or {}).get('accepted_accounting_ready')) if isinstance(accounting, dict) else False,
            'finish_tool_available_now': True,
            'finish_case_is_terminal_only': True,
            'when_not_ready': 'finish_case is visible as a capability, but the fixed layer rejects it until accepted accounting or fail_closed preconditions pass. Use propose_mapping_intents for open_rows_requiring_agent_action or execute evidence_menu requests first.',
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
        'notebook': _compact_notebook_for_agent(build_notebook(dossier)),
        'evidence_menu': {
            'prompt_summaries': fresh_menu_summaries[:24],
            'audit': menu.get('audit') or {},
            'completed_or_failed_request_ids': sorted(completed_or_failed_request_ids)[:32],
        },
        'query_recall_observation': _query_recall_observation_for_agent(workspace),
        'recent_tool_observations': _recent_orchestrator_observations(workspace, limit=8),
        'turn_health': {
            'has_recent_warning': bool(recent_turn_health),
            'latest': recent_turn_health,
            'protocol': (
                'Turn health is procedural telemetry, not a semantic verdict. If stall_suspected or near_turn_limit_unhealthy is true, '
                'do not repeat the same understanding/query action. Choose a concrete new evidence request, propose mapping intents from visible refs, '
                'split/reconsider boundaries if legally available, or finish only when finish_protocol allows it.'
            ),
        },
        'rules': [
            'If case_understanding.recommended_before_mapping_when_missing is true, strongly consider propose_case_understanding. You may choose another tool first when it is a concrete useful action, but the fixed layer will not infer package semantics for you.',
            'In a child case, parent/root refs are not visible unless they appear in this payload. Use child_case_ref_scope.visible_local_file_refs_only, child_case_ref_scope.visible_local_span_refs_only, and mapping_draft_rows; do not reuse LS/BS/BE/BES refs from the root split memory.',
            'In propose_case_understanding, work_units must cover every main LF exactly once. For multiple work units, use disjoint file_refs lists. The fixed layer creates LS1/LS2 work-unit spans after this tool succeeds, so do not pre-cite future LS* refs that are not already visible.',
            'For large packages, use local_main_file_groups from STABLE_CACHE_PREFIX as your fast package table. These are leaf parent-directory groups, so a top_group may have separate regular/SP/Menu/Extra groups. In split_into_child_cases you may cite main_group_refs like LG1 instead of copying every LF ref; in query/ledger source_refs or support_refs you may cite visible LG* as local group evidence. The fixed layer only expands/validates group refs mechanically.',
            'If propose_case_understanding returns split_case_skeleton_from_work_units, you may copy or edit selected parts into split_into_child_cases. Use execution_mode=record_split_plan_only to preserve a boundary plan without running children; use coverage_mode=selected_child_cases when only major work units need child context and the remaining LF refs should be closed by root ledger/intents.',
            'Child execution is a scarce runtime budget. When using split_into_child_cases(execution_mode=run_child_cases), run only a small selected batch of child cases that truly need focused context; keep the rest in recorded_split_plan/root ledger with plan_row_refs=RSP*.',
            'Use case_desk_priority as the reading order for this turn. It is a factual navigation aid, not a fixed-layer semantic decision and not a tool gate.',
            'If package_boundary_decision_board.active is true, inspect it before broad root mapping. It summarizes copyable split skeleton rows, major-unit-like rows, packaging/extra-like rows, and legal boundary strategy options. This board is advisory factual context, not a fixed-layer semantic decision.',
            'If recorded_split_plan.active is true, keep that Agent-authored boundary memory visible while choosing root ledger, selected child execution, evidence, or mapping intents. To resolve a recorded plan row in propose_case_resolution_ledger, cite plan_row_refs like RSP1/RSP2 from recorded_split_plan.plan_row_refs; do not invent row_ref labels like ROOT, SUPP, TVS, or SPC. To run children from that recorded plan without copying LF refs again, call split_into_child_cases with execution_mode=run_child_cases, coverage_mode=selected_child_cases, split_cases=[], and recorded_child_case_refs copied from recorded_split_plan.child_case_refs. The fixed layer recorded refs and coverage only; it did not decide the semantic boundary.',
            'Notebook internal refs such as WU*, NH*, NQ*, NA*, or synthetic notebook memory refs are read-only observations. Never put them in source_refs, support_refs, local_refs, item_refs, target_refs, or query_refs.',
            'Use local_main_file_overview to separate obvious local work units such as regular numbered episodes, SP/OVA/OAD extras, NCOP/NCED, menu/navigation, PV/CM/trailers, or other supplemental videos. This is your semantic partition, not a fixed-layer decision.',
            'When materializing queries from file labels, prefer distinctive title tokens such as parenthetical subtitle tokens in file_label_samples.bracket_title_tokens; avoid generic words like movie/theater/Gekijouban by themselves.',
            'Use propose_case_resolution_ledger when a case has several rows or mixed outcomes. The ledger is your package-level resolution: each row says map_to_bangumi, target_absent, supplemental, needs_evidence, split_needed, or fail_blocker. The fixed layer validates and compiles only what you explicitly chose.',
            'For large or mixed packages with multiple obvious work units, split_into_child_cases is usually the human-like path for normal TV seasons, movies, or specials that need their own Bangumi recall. Root ledger is best for bookkeeping obvious packaging extras or for a case you can confidently map/reject row by row without losing work-unit focus.',
            'When split_decision.large_multi_unit_package is true and split_case_skeleton_from_work_units is present, consider recording a split plan or running selected child cases from that skeleton before broad root mapping. This is strategic advice to preserve work-unit focus; the fixed layer will still only validate refs/coverage if you choose split.',
            'Do not use root ledger or mark_non_bangumi_or_supplemental as a shortcut to close normal TV/movie/season work units that still have plausible Bangumi subjects/items. If a large package projection would map zero main files or exclude most normal work units, treat that as a self-review alarm: split, gather better evidence, or revise mapping intents unless every row is truly packaging/target_absent by your semantic judgment.',
            'If split_decision.pending is true, it is a factual observation that the current understanding has multiple work units without a later split or terminal root resolution. Decide freely whether to run selected child cases, run a complete split, gather evidence, ledger/map the root, or finish when verification can pass.',
            'After the first understanding turn, call propose_case_understanding again only when it will change or clarify the case memory; repeated identical understanding is a stall.',
            'When case_understanding.revision_available_now is true because of work_unit_repartition, split, or unit-boundary agenda, use propose_case_understanding if revising the local partition is the best next action.',
            'A repartition revision must actually change the local work-unit partition. If the compiler rejected one BE for many local files, split that row into smaller work_units or singleton file units; do not submit the same multi-file unit again.',
            'If a recent case_understanding_rejected observation says case_understanding_noop_repartition, do not repeat the same understanding. Choose a different capability: split_into_child_cases for a real sub-case split, propose_mapping_intents with legal same-count/target_absent/supplemental intent, or execute concrete fresh evidence if available.',
            'If case_understanding.revision_available_now is false, avoid repeating propose_case_understanding unless you have a concrete correction; use another capability when that moves the case forward.',
            'If turn_health.latest.stall_suspected or turn_health.latest.near_turn_limit_unhealthy is true, change strategy now: do not repeat the same non-progressing understanding/query tool; pursue concrete evidence, map/reject/target_absent with visible refs, split/reconsider, or legal finish.',
            'If open_rows_requiring_agent_action already show candidate_target_refs or a same-count visible_subject_item_sequences entry with all_item_refs_unowned=true, that is actionable target surface. Consider propose_mapping_intents for those rows; if you still need evidence, name the specific missing fact/request type rather than repeating a vague evidence request.',
            'Use work_unit_resolution_board as your primary case desk. It combines each row/work unit local files, current outcome, candidate targets, ownership conflicts, evidence agenda, and legal next-action templates. It is factual context, not a fixed-layer semantic verdict.',
            'When a row is unresolved, first inspect its work_unit_resolution_board row. If it has latest_blocked_evidence_agenda_rows with matching request ids, execute evidence or make a new terminal semantic decision. If it has target_ownership_conflicts, revise ownership, choose another visible target, request evidence, or mark target_absent/supplemental if that is your investigated conclusion.',
            'Use row_outcome_closure as the row-level protocol checklist. Every open row must move to one legal outcome: mapping, accepted target_absent/supplemental, concrete fresh evidence, or real fail_closed. Do not leave a row in the same unresolved shape after a blocker.',
            'If every same-count visible_subject_item_sequences entry for a row is already occupied by other rows, that row is no longer an evidence chase. Either revise ownership on the earlier row if you were wrong, or mark the local row with bangumi_target_absent/supplemental if that is your investigated conclusion.',
            'If compiler/accounting feedback says item_ref_count_mismatch, item_subject_mismatch, count_mismatch, duplicate_target, or invalid_explicit_multi_file_mapping, first ask whether the current work unit is too broad or mixed. You may revise understanding, split, revise mapping intents, use visible evidence, or request concrete evidence.',
            'If open_rows show candidate_target_conflicts or occupied_item_refs, those are fixed-layer duplicate/accounting facts. Do not reuse occupied BE/BES targets for another row; choose a non-overlapping visible target, repartition, request more evidence, or mark target_absent/supplemental if that is your semantic conclusion.',
            'unowned_candidate_target_refs and visible_subject_item_sequences with unowned_item_ref_count are legality aids only. You still decide semantically whether they match the local row.',
            'If recent_tool_observations contains ref_issue_codes/ref_corrections, repair exactly those fields before doing anything else. A hidden_or_wrong_ref_namespace issue means the ref may be visible but placed in the wrong schema field.',
            'Before writing any item_refs/chosen_item_ref/chosen_span_ref, verify the exact refs in visible_ref_catalog, visible_item_ref_table, visible_span_ref_table, or open_rows_requiring_agent_action. Do not infer that BE1..BE13 exist from episode numbers.',
            'If open_rows_have_actionable_mapping_surface is true, visible mapping surface exists. Usually inspect it with propose_mapping_intents; use materialize_queries or execute_evidence only when you can state what comparison/target fact is still missing.',
            'update_notebook is usually trailing bookkeeping. If open rows remain, prefer a tool that changes evidence, mapping, split, ledger, or finish state unless the notebook update itself is the concrete useful action.',
            'If no Bangumi subject exists, prefer materialize_queries with clean title/alias queries or execute subject_search from visible QC menu.',
            'If Latin/romanized title queries are empty or recall unrelated BS subjects, materialize title-preserving alternatives instead of repeating split: spacing/case variants, fused/split romanized words, and original Japanese kana/kanji or Chinese official title forms when you know them.',
            'For movie/special compilation rows, inspect open_rows_requiring_agent_action.file_label_samples as well as title_cues. If file labels contain distinctive part titles or romanized subtitles, use those exact cues or known title aliases in materialize_queries before accepting an unrelated broad search result.',
            'Visible BE/BES refs are legal evidence, not semantic proof. Before mapping, compare local title_cues/file_label_samples to candidate_target_briefs and visible_subject_item_sequences subject_title/title_samples. If the distinctive work title differs and you do not have an explicit alias reason, reject/request better evidence or target_absent; do not map merely because the item count fits.',
            'Do not use release scope words such as OVA/OAD/SP/year/season, "main TV series", codec, group, resolution, or file role names as standalone subject queries. Those are shape clues, not Bangumi subject titles.',
            'If subjects exist but no item targets exist, inspect subject/episode/related evidence before target_span.',
            'Use propose_mapping_intents when you have a semantic decision: map_regular_span, map_explicit_item, reject_candidate, mark_non_bangumi_or_supplemental, needs_more_evidence, or mark_unaligned_fail_closed.',
            'Use propose_case_resolution_ledger when you need to settle many rows together before or instead of low-level propose_mapping_intents. Ledger rows may cite MDR row_ref/local_ref, recorded_split_plan plan_row_refs=RSP*, and visible LF/LS/LG/BS/BE/BES refs. Ledger span_refs are local LS* only; put Bangumi BES* target spans in chosen_span_ref or target_refs.',
            'BS* subject refs are evidence choices, not final assignment targets. For regular runs, choose chosen_subject_ref plus episode_start/episode_end; the fixed layer will request or compile a BES span.',
            'LS* refs are local spans. BES* refs are Bangumi target spans. Never put LS* in chosen_span_ref; use LS* as local_ref and choose BS*/BE*/BES* as target evidence.',
            'For singleton movie/special/episode decisions, choose a visible chosen_item_ref=BE*. For an already visible span decision, choose chosen_span_ref=BES*.',
            'For any multi-file row, including SP/OVA/OAD/special rows, if open_rows show visible_subject_item_sequences with matches_local_file_count=true and all_item_refs_unowned=true and the sequence is semantically correct, use those item_refs in map_regular_span; the compiler can materialize a controlled BES span from your explicit item_refs.',
            'If a multi-file local row semantically consists of separate singleton movie/special files and you have chosen one visible BE item per local file, you may use map_regular_span with item_refs in local file order even when those BE items belong to different BS subjects. The compiler will only validate visible refs and count; you are responsible for the semantic match.',
            'For large rows, visible_subject_item_sequences.item_refs is the usable ref list, not a sample, unless item_refs_truncated=true. Copy the whole matching item_refs list when you choose that sequence.',
            'When a multi-file row has no same-count visible sequence, do not keep retrying the wrong count. Repartition the row, choose a different same-count sequence, or mark target_absent/supplemental if your investigated conclusion is that Bangumi lacks a corresponding target.',
            'For SP/Preview/PV/CM/OAD/OVA/NCOP/NCED extras, do not map them to regular TV episode sequences merely because the season title matches. First look for visible special/OAD/OVA/PV targets or request concrete special/related evidence. If the only visible targets are regular episodes or unrelated singleton specials and you judge no corresponding Bangumi target exists, use mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent/bonus_video/non_episode_video/other_supplemental) instead of retrying count-mismatched regular spans.',
            'After the same row receives repeated mechanical blockers such as item_ref_count_mismatch, count_mismatch, target_span_not_detail_equivalent, or invalid_explicit_multi_file_mapping, repeating the same shape is a procedural no-op. A useful continuation changes the decision shape: true same-count span, split/repartition if available, target_absent/supplemental, or a concrete fail_closed blocker.',
            'A propose_mapping_intents call is not a probe for known-illegal shapes. blocked_intents.observation.valid_shapes and recommended_next_observation are protocol facts: for example, if a multi-file row cannot use one BE item, repeating the same single-BE map_explicit_item cannot make progress.',
            'If a multi-file row has a visible BES* candidate whose target_ref_count matches the local file count and the candidate is semantically correct, use map_regular_span with chosen_span_ref set to that BES*. Do not keep returning needs_more_evidence for a row that already has the visible sequence you need.',
            'Put LF/LS refs in local_ref/local_refs/support_refs/source_refs. Put LG refs only in local group fields or support_refs/source_refs. Put BS refs in chosen_subject_ref/subject_refs/support_refs. Put BE refs in chosen_item_ref/item_refs/support_refs. Put BES refs in chosen_span_ref/target_refs/support_refs. Do not copy synthetic BES_INTENT_* refs back into ledger span_refs or item_refs.',
            'Never put LF*/LS* in item_refs. item_refs is not "files in this work unit"; it is the Bangumi BE* sequence you choose as the target.',
            'Use finish_case only after the latest useful evidence has been reflected in draft patches or evidence is genuinely exhausted.',
            'Before accepted finish, inspect global_outcome_projection and fill finish_case reviewed_outcome_projection, acknowledged_* counts, work_unit_reviews, and final_case_review. These fields force your own whole-case review; the fixed layer only checks that the counts/refs match the current draft.',
            'If global_outcome_projection.review_flags or the latest tool observation finish_review_flags includes no_main_files_are_mapped_to_bangumi, accepted_projection_maps_no_main_files, majority_of_main_files_are_accepted_exclusions, or accepted_projection_excludes_majority_of_main_files, do not finish reflexively. Finish only if your final_case_review explicitly concludes that every main work unit is truly supplemental or Bangumi-target-absent after investigation; otherwise revise mapping, split, or request evidence.',
            'If no_new_evidence_preconditions_ok is true and accepted_finish_allowed_now is false, finish_case(fail_closed) is available only when your semantic conclusion is that evidence is exhausted or conflicting. If you continue with a non-terminal tool, it must pursue new information or revise a concrete intent.',
            'If finish_case is rejected for accepted_accounting_not_ready/not_ready, another identical finish_case call is a no-op. Make progress on open/needs_more_evidence rows with mapping intents, evidence, split/ledger revision, or a real fail_closed basis first.',
            'If open_rows_requiring_agent_action is non-empty and finish_protocol.accepted_finish_allowed_now is false, finish_case will be rejected unless fail_closed preconditions are real; handle those rows with mapping intents or evidence requests first.',
            'When a compiler/tool output says open_rows remain, pick each listed row_ref/local_ref and either map it, request evidence for it, mark it target_absent/supplemental, or mark a concrete semantic conflict.',
            'A propose_mapping_intents call that only preserves an unchanged unresolved state gives the case no new agenda. If a row already shows a same-count visible_subject_item_sequences entry, either map that sequence if semantically correct, revise ownership, request a concrete missing comparison fact, or mark target_absent/supplemental/semantic conflict with concrete refs.',
            'You may revise your own earlier mapping or supplemental decision for a row when later evidence or ownership conflicts show it was wrong; submit a new intent for that row and the fixed layer will re-verify accounting.',
            'If recent_tool_observations contains required_next_tools, treat that as loop telemetry from a prior non-progressing action, not as a semantic order. Choose a tool that creates a concrete new observation or legal terminal outcome.',
            'candidate_target_refs are evidence suggestions, not fixed-layer semantic decisions. If they are semantically wrong, you may reject them, ask for more evidence, or use target_absent/supplemental with a clear reason.',
            'Do not reject a same-subject, same-count special/OVA/OAD/SP item sequence merely because the local files are SP/supplemental. If Bangumi exposes matching special BE* items and you judge the sequence correct, map it; bangumi_target_absent means no corresponding Bangumi target exists.',
            'For mark_non_bangumi_or_supplemental, use one allowed_supplemental_reason_kinds value exactly. bangumi_target_absent means Bangumi has no corresponding target for that local row after investigation.',
            'If a local row should still be accepted but not mapped because Bangumi has no corresponding target, use mark_non_bangumi_or_supplemental with reason_kind=bangumi_target_absent. Do not use mark_unaligned_fail_closed for accepted target-absent rows.',
            'For mark_unaligned_fail_closed, use one allowed_unaligned_reason_kinds value exactly; these rows keep the case unresolved and are for real fail_closed cases. Do not use no_legal_target for Bangumi target absence; that accepted case is bangumi_target_absent under mark_non_bangumi_or_supplemental.',
            'If latest_blocked_evidence_agenda.matching_executable_request_ids is non-empty, those ids are the direct mechanical continuation of your last blocked ledger/mapping evidence request. Use execute_evidence with latest_blocked_evidence_agenda.suggested_execute_evidence_args unless you are intentionally changing to a terminal visible mapping/exclusion decision.',
            'If a compiler result has requested_evidence or blocked intents and matching evidence is available, execute the matching evidence menu request ids instead of proposing the same intent again.',
            'For status=accepted, finish_kind must be accepted. For status=fail_closed, choose no_new_evidence, semantic_target_conflict, budget_exhausted, or tool_loop_blocked.',
            'If a tool returns blocked intents, patch/accounting issues, or requested evidence, respond with execute_evidence or revised propose_mapping_intents instead of assuming Python will repair it.',
        ],
    }


def build_orchestrator_agent_input(workspace: CaseEvidenceWorkspace, *, reason: str = '') -> str:
    return json.dumps(_jsonable(build_orchestrator_agent_payload(workspace, reason=reason)), ensure_ascii=False, indent=2)


def build_orchestrator_agent_stable_prefix(workspace: CaseEvidenceWorkspace) -> str:
    tool_schema_text = json.dumps(
        _jsonable(orchestrator_tool_definitions()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    payload = {
        'cache_prefix_version': 'local_bangumi_orchestrator_v8',
        'purpose': 'Stable case context for provider input caching. This section must remain byte-stable across turns for this case.',
        'case_id': workspace.header.case_id,
        'contract': workspace.contract.model_dump(mode='json'),
        'instructions': ORCHESTRATOR_AGENT_INSTRUCTIONS,
        'tool_schema_text_for_cache': tool_schema_text,
        'local_main_file_groups': _main_file_group_overview(workspace),
        'local_main_file_overview': _main_file_overview(workspace, limit=48),
        'local_file_sample': _sample_cards(list(workspace.local_files or []), limit=32),
        'initial_local_span_sample': _sample_cards(list(workspace.local_span_cards or []), limit=64),
        'child_case_ref_scope': {
            'is_child_case': ':' in str(getattr(getattr(workspace, 'header', None), 'case_id', '') or '') or 'derived_from_case_planning_split' in {
                str(value or '') for value in list(getattr(workspace, 'diagnostics', []) or [])
            },
            'parent_refs_not_visible': ':' in str(getattr(getattr(workspace, 'header', None), 'case_id', '') or '') or 'derived_from_case_planning_split' in {
                str(value or '') for value in list(getattr(workspace, 'diagnostics', []) or [])
            },
            'visible_local_file_refs_only': list(getattr(getattr(workspace, 'contract', None), 'allowed_file_refs', []) or []),
            'visible_local_span_refs_only': [
                str(getattr(card, 'ref', '') or '')
                for card in list(getattr(workspace, 'local_span_cards', []) or [])
                if str(getattr(card, 'ref', '') or '')
            ],
        },
        'tool_names': sorted(TOOL_ARG_MODELS.keys()),
        'tool_schema_names': [tool['function']['name'] for tool in orchestrator_tool_definitions()],
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
        'tool_protocol': {
            'public_tools': sorted(TOOL_ARG_MODELS.keys()),
            'tools_param_is_authoritative': True,
            'available_tool_names_are_public_capabilities_not_phase_gate': True,
            'ref_prefix_cheatsheet': {
                'LF': 'local file ref; use in local_ref/local_refs/support_refs/source_refs',
                'LS': 'local span ref; use as local_ref for a row/work unit, never as chosen_span_ref',
                'LG': 'local main-file group ref from local_main_file_groups; use in split group fields or support_refs/source_refs',
                'QC': 'query candidate ref; use in query_refs/support_refs',
                'BS': 'Bangumi subject ref; use as chosen_subject_ref/subject_refs/support_refs',
                'BE': 'Bangumi episode/special/movie item ref; use as chosen_item_ref/item_refs/support_refs',
                'BES': 'Bangumi episode span ref; only this prefix is valid for chosen_span_ref',
                'MDR': 'mapping draft row ref; use as row_ref/row_refs',
            },
            'fixed_layer_role': (
                'execute tools, save workspace/notebook, compile explicit mapping intents, '
                'verify refs/schema/support/coverage/duplicate/accounting/budget/loop, and return observations'
            ),
            'fixed_layer_does_not_do_semantics': (
                'It must not choose Bangumi targets, decide special/OVA/OAD/SP semantics, or invent target_absent. '
                'Those are OrchestratorAgent semantic responsibilities expressed through structured intents.'
            ),
        },
        'stable_rules': [
            'available_tool_names is the public capability set, not a semantic phase gate. The fixed layer rejects only hard illegal calls.',
            'Usually start by understanding the case before mapping or evidence, but choose the concrete next useful tool like a human investigator.',
            'For child cases, parent/root refs are invalid unless visible in the current payload. Use the child-local LF/LS refs only.',
            'Work units must cover every main LF exactly once.',
            'For large packages, prefer local_main_file_groups over long per-file scanning. They are leaf parent-directory groups; use split_into_child_cases main_group_refs=LG* for selected groups when that is clearer than copying long LF lists. LG* may also support query/ledger source_refs. Use selected_child_cases when only those groups should become child observations.',
            'Run child sessions in small selected batches only. For many work units, preserve the boundary with record_split_plan_only and resolve ordinary rows through root ledger plan_row_refs=RSP*.',
            'When TURN_STATE_TAIL.package_boundary_decision_board.active is true, inspect that board before broad root mapping. It lists human-like boundary options: record split plan, selected child deep-dive, root ledger, or complete split.',
            'When TURN_STATE_TAIL.recorded_split_plan.active is true, keep the recorded boundary rows visible while choosing root ledger, selected child execution, evidence, or mapping intents. Use recorded_split_plan.plan_row_refs as ledger plan_row_refs; row_ref is only for visible MDR* rows.',
            'If recent_tool_observations includes split_case_skeleton_from_work_units, it is a copyable split draft derived from your own work units. Use it only when child sessions are clearer than root ledger resolution; for very large multi-unit packages, prefer selected_child_cases for major units and keep packaging extras in root ledger when local markers are enough.',
            'In the first propose_case_understanding call, cite main files with LF* file_refs. Do not invent future LS1/LS2 refs; the fixed layer will create those local spans after accepting your work units.',
            'Notebook internal refs such as WU*, NH*, NQ*, NA*, or synthetic notebook refs are read-only observations.',
            'Use materialize_queries for clean title/alias queries and execute_evidence for visible evidence menu requests.',
            'Use propose_case_resolution_ledger for package-level row outcomes in mixed or large cases. The fixed layer validates refs/coverage and compiles only explicit Agent outcomes; recorded split rows are cited with plan_row_refs=RSP*.',
            'Use propose_mapping_intents for semantic decisions: map_regular_span, map_explicit_item, reject_candidate, mark_non_bangumi_or_supplemental, needs_more_evidence, or mark_unaligned_fail_closed. Do not write MappingDraftPatch objects.',
            'Use work_unit_resolution_board in TURN_STATE_TAIL as the primary human-readable case board. It organizes facts; it never decides semantics for you.',
            'Use row_outcome_closure from each turn as the checklist for legal row exits; the fixed layer will validate those shapes but will not choose the semantic outcome.',
            'For regular runs, choose chosen_subject_ref plus episode_start/episode_end or explicit item_refs/span refs.',
            'Never put an LS* local span ref in chosen_span_ref. chosen_span_ref is only for visible BES* Bangumi span refs.',
            'Never put LF*/LS* local refs in item_refs. item_refs/chosen_item_ref are only for visible BE* Bangumi item refs.',
            'In propose_case_resolution_ledger, span_refs are local LS* only. Put Bangumi BES* spans in chosen_span_ref/target_refs and never copy synthetic BES_INTENT_* refs into ledger span_refs or item_refs.',
            'For singleton movie/special/episode decisions, choose a visible chosen_item_ref=BE*.',
            'For multi-file rows with a visible same-count sequence, map that sequence if semantically correct.',
            'Use mark_non_bangumi_or_supplemental(reason_kind=bangumi_target_absent) when Bangumi has no corresponding target after investigation.',
            'Use finish_case only after accepted accounting is ready or fail_closed preconditions are real. For accepted finish, copy the mechanical counts from global_outcome_projection into the acknowledged_* fields and review every draft row.',
        ],
    }
    return json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def build_orchestrator_agent_turn_tail(workspace: CaseEvidenceWorkspace, *, reason: str = '') -> str:
    payload = build_orchestrator_agent_payload(workspace, reason=reason)
    payload.pop('local_main_file_groups', None)
    payload.pop('local_main_file_overview', None)
    payload.pop('local_file_sample', None)
    payload.pop('visible_refs', None)
    payload.pop('visible_ref_catalog', None)
    payload.pop('local_span_sample', None)
    payload.pop('visible_item_ref_table', None)
    payload.pop('visible_span_ref_table', None)
    payload.pop('child_case_ref_scope', None)
    payload.pop('allowed_supplemental_reason_kinds', None)
    payload.pop('allowed_unaligned_reason_kinds', None)
    payload.pop('rules', None)
    payload['cache_tail_version'] = 'local_bangumi_orchestrator_tail_v3'
    return json.dumps(_jsonable(payload), ensure_ascii=False, indent=2)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _stable_session_component(case_id: str) -> str:
    raw = str(case_id or '').strip() or 'case'
    slug = re.sub(r'[^A-Za-z0-9._:-]+', '_', raw).strip('_.:-') or 'case'
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]
    return f'{slug[:48]}_{digest}'


def _ensure_http_history_replay_session(session: OrchestratorAgentSession) -> OrchestratorAgentSession:
    component = _stable_session_component(session.case_id)
    http_session_id = session.http_session_id or f'bar_local_bangumi_{component}'
    prompt_cache_key = session.prompt_cache_key or 'bar:lbg:orchestrator:v8'
    return replace(
        session,
        session_mode='http_history_replay',
        provider_session_enabled=False,
        provider_conversation_id='',
        http_session_id=http_session_id,
        prompt_cache_key=prompt_cache_key,
    )


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
        history_items=[] if not session.provider_session_enabled else list(session.history_items or []),
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
    session = _ensure_http_history_replay_session(session)
    stable_prefix = session.stable_cache_prefix or build_orchestrator_agent_stable_prefix(workspace)
    if not session.stable_cache_prefix:
        session = replace(session, stable_cache_prefix=stable_prefix)
    turn_tail = build_orchestrator_agent_turn_tail(workspace, reason=reason)
    prompt = f'{ORCHESTRATOR_AGENT_INSTRUCTIONS}\n\n{stable_prefix}\n\n{turn_tail}'
    history_items = list(session.history_items or [])
    estimated_tokens = _estimate_tokens(prompt)
    if session.compacted_history_summary:
        estimated_tokens += _estimate_tokens(session.compacted_history_summary)
    session, compact_audit = _maybe_compact_session(
        session,
        estimated_input_tokens=estimated_tokens,
        soft_token_limit=soft_token_limit,
        hard_token_limit=hard_token_limit,
    )
    history_items = list(session.history_items or [])
    stable_instructions = f'{ORCHESTRATOR_AGENT_INSTRUCTIONS}\n\nSTABLE_CACHE_PREFIX:\n{stable_prefix}'
    input_items: list[dict[str, object]] = []
    tail_content = f'TURN_STATE_TAIL:\n{turn_tail}'
    if session.compacted_history_summary:
        tail_content = f'Compacted prior OrchestratorAgent context:\n{session.compacted_history_summary}\n\n{tail_content}'
    input_items.append({'role': 'user', 'content': tail_content})
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
        instructions=stable_instructions,
        input_items=input_items,
        tools=orchestrator_tool_definitions(),
        max_output_tokens=4096,
        parallel_tool_calls=False,
        tool_choice='required',
        conversation_id='',
        prompt_cache_key=session.prompt_cache_key,
    )
    if not isinstance(response, dict):
        return OrchestratorAgentCallResult(
            ok=False,
            session=session,
            error='orchestrator_agent_no_response',
            audit={
                'note': 'orchestrator_agent_call_failed',
                'error_kind': 'orchestrator_agent_no_response',
                'transport_failure': True,
                **compact_audit,
            },
        )
    response_id = str(response.get('id') or '')
    usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
    tool_call, parse_error = _parse_tool_call(response)
    updated_session = replace(
        session,
        provider_response_id=response_id or session.provider_response_id,
        provider_session_enabled=False,
        provider_conversation_id='',
        http_session_id=session.http_session_id,
        prompt_cache_key=session.prompt_cache_key,
        cache_mode=str(response.get('cache_mode') or getattr(ai_client, '_last_tool_agent_cache_mode', session.cache_mode) or 'unknown'),
        cache_key=str(response.get('cache_key') or getattr(ai_client, '_last_tool_agent_cache_key', session.cache_key) or 'unknown'),
        cache_event=str(response.get('cache_event') or getattr(ai_client, '_last_tool_agent_cache_event', session.cache_event) or 'unknown'),
        turn_count=session.turn_count + 1,
        input_token_estimate=int(usage.get('input_tokens') or usage.get('prompt_tokens') or estimated_tokens),
        output_token_estimate=int(usage.get('output_tokens') or usage.get('completion_tokens') or 0),
        tool_sequence=[*session.tool_sequence, tool_call.tool_name] if tool_call else list(session.tool_sequence),
        history_items=[*history_items, _function_call_history_item(tool_call)] if tool_call else history_items,
    )
    audit = {
        'note': 'orchestrator_agent_called',
        'response_id': response_id,
        'session_mode': updated_session.session_mode,
        'provider_session_enabled': updated_session.provider_session_enabled,
        'provider_response_id': updated_session.provider_response_id,
        'provider_conversation_id': updated_session.provider_conversation_id,
        'http_session_id': updated_session.http_session_id,
        'prompt_cache_key': updated_session.prompt_cache_key,
        'cache_mode': updated_session.cache_mode,
        'cache_key': updated_session.cache_key,
        'cache_event': updated_session.cache_event,
        'turn_count': updated_session.turn_count,
        'tool_name': tool_call.tool_name if tool_call else '',
        'tool_call_id': tool_call.call_id if tool_call else '',
        'available_tool_names': sorted(allowed_tool_names),
        'compact_count': updated_session.compact_count,
        'context_soft_limit_hit_count': updated_session.context_soft_limit_hit_count,
        'context_hard_limit_hit_count': updated_session.context_hard_limit_hit_count,
        'stable_prefix_estimated_tokens': _estimate_tokens(stable_prefix),
        'stable_prefix_transport': 'instructions',
        'turn_tail_estimated_tokens': _estimate_tokens(turn_tail),
        'prompt_cache_retention': '24h',
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
        'near_turn_limit_unhealthy_count': session.near_turn_limit_unhealthy_count,
        'stall_suspected_count': session.stall_suspected_count,
        'consecutive_stall_count': session.consecutive_stall_count,
        'compact_count': session.compact_count,
        'context_soft_limit_hit_count': session.context_soft_limit_hit_count,
        'context_hard_limit_hit_count': session.context_hard_limit_hit_count,
        'session_mode': session.session_mode,
        'provider_session_enabled': session.provider_session_enabled,
        'provider_response_id': session.provider_response_id,
        'provider_conversation_id': session.provider_conversation_id,
        'http_session_id': session.http_session_id,
        'prompt_cache_key': session.prompt_cache_key,
        'cache_mode': session.cache_mode,
        'cache_key': session.cache_key,
        'cache_event': session.cache_event,
        'history_item_count': len(session.history_items),
        'stable_cache_prefix_bytes': len(session.stable_cache_prefix.encode('utf-8')) if session.stable_cache_prefix else 0,
    }
