from __future__ import annotations

from typing import Any, ClassVar, Literal, Mapping

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


CaseType = Literal['local_bangumi']
EvidenceRequestType = Literal[
    'subject_lookup',
    'subject_search',
    'related_expansion',
    'episode_list',
    'episode_detail',
    'local_file_detail',
    'target_detail',
    'target_window',
    'target_span',
]
CaseAction = Literal['request_evidence', 'submit_verdict', 'fail_closed', 'issue_response']


class EvidencePlanStep(BaseModel):
    step_kind: Literal['select_menu_requests'] = 'select_menu_requests'
    selected_menu_request_ids: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class EvidencePlan(BaseModel):
    plan_id: str = ''
    plan_kind: Literal['subject_recall', 'episode_recall', 'span_proof', 'special_recall'] = 'span_proof'
    selected_menu_request_ids: list[str] = Field(default_factory=list)
    completed_menu_request_ids: list[str] = Field(default_factory=list)
    failed_menu_request_ids: list[str] = Field(default_factory=list)
    ready_span_refs: list[str] = Field(default_factory=list)
    planned_span_request_count: int = 0
    selected_span_request_count: int = 0
    completed_span_request_count: int = 0
    span_rows_with_candidates: int = 0
    span_rows_without_candidates: int = 0
    plan_status: Literal['idle', 'in_progress', 'completed', 'blocked', 'exhausted'] = 'idle'
    goal: str = ''
    stop_conditions: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    steps: list[EvidencePlanStep] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CaseHeader(BaseModel):
    case_id: str = ''
    case_type: CaseType = 'local_bangumi'
    round_index: int = 0
    max_rounds: int = 0
    evidence_batches_used: int = 0
    issue_response_used: int = 0
    status: Literal['unknown', 'open', 'closed', 'failed_closed'] = 'unknown'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CaseBudget(BaseModel):
    max_judge_rounds: int = 0
    max_evidence_batches: int = 0
    max_issue_response_rounds: int = 0
    max_requests_per_batch: int = 0
    max_api_calls_per_case: int = 0
    max_subject_searches: int = 0
    max_search_results_per_query: int = 0
    max_related_depth: int = 0
    max_new_subject_cards: int = 0
    max_new_episode_cards: int = 0
    used_judge_rounds: int = 0
    used_evidence_batches: int = 0
    used_issue_response_rounds: int = 0
    used_requests: int = 0
    used_api_calls: int = 0
    used_subject_searches: int = 0
    used_search_results: int = 0
    used_related_depth: int = 0
    used_new_subject_cards: int = 0
    used_new_episode_cards: int = 0

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class VisibleRefCatalog(BaseModel):
    local_file_refs: list[str] = Field(default_factory=list)
    local_cluster_refs: list[str] = Field(default_factory=list)
    bangumi_subject_refs: list[str] = Field(default_factory=list)
    bangumi_relation_refs: list[str] = Field(default_factory=list)
    bangumi_group_refs: list[str] = Field(default_factory=list)
    bangumi_item_refs: list[str] = Field(default_factory=list)
    query_refs: list[str] = Field(default_factory=list)
    target_refs: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class ProvenanceCard(BaseModel):
    ref: str = ''
    retrieval_round: int = 0
    request_ref: str = ''
    source_operation: str = ''
    api_subject_id: int = 0
    api_episode_id: int = 0
    parent_refs: list[str] = Field(default_factory=list)
    raw_response_hash: str = ''
    raw_response_count: int = 0
    created_at: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class LocalFileCard(BaseModel):
    ref: str = ''
    source_file_id: str = ''
    path: str = ''
    is_main: bool = False
    size_bytes: int = 0
    parent_display: str = ''
    cluster_ref: str = ''
    label: str = 'unknown'
    file_kind: Literal['video', 'subtitle', 'unknown'] = 'unknown'
    related_refs: list[str] = Field(default_factory=list)
    path_facts: Mapping[str, object] = Field(default_factory=dict)
    container_facts: Mapping[str, object] = Field(default_factory=dict)
    subtitle_facts: Mapping[str, object] = Field(default_factory=dict)
    subtitle_compact_facts: Mapping[str, object] = Field(default_factory=dict)
    stream_facts: Mapping[str, object] = Field(default_factory=dict)
    missing_facts: list[Mapping[str, object]] = Field(default_factory=list)
    fact_summary: Mapping[str, object] = Field(default_factory=dict)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    @property
    def basename(self) -> str:
        return self.path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]


class LocalClusterCard(BaseModel):
    ref: str = ''
    cluster_name: str = ''
    title_cues: list[str] = Field(default_factory=list)
    file_refs: list[str] = Field(default_factory=list)
    cluster_kind: Literal['local', 'mixed', 'unknown'] = 'unknown'
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BangumiSubjectCard(BaseModel):
    ref: str = ''
    subject_id: int = 0
    subject_type: Literal['anime', 'book', 'game', 'music', 'real', 'unknown'] = 'unknown'
    title: str = ''
    name: str = ''
    name_cn: str = ''
    date: str = ''
    summary_short: str = ''
    platform: str = ''
    eps: int = 0
    total_episodes: int = 0
    tags: list[str] = Field(default_factory=list)
    infobox_facts: list[str] = Field(default_factory=list)
    source_form_hint: str = ''
    source_form_evidence: list[str] = Field(default_factory=list)
    relation_to_main: str = ''
    search_query_ref: str = ''
    search_rank: int = 0
    retrieval_round: int = 0
    provenance_ref: str = ''
    source_role: str = ''
    relation_path_refs: list[str] = Field(default_factory=list)
    relation_refs: list[str] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BangumiRelationCard(BaseModel):
    ref: str = ''
    relation_kind: Literal['prequel', 'sequel', 'side_story', 'adaptation', 'parent', 'child', 'unknown'] = 'unknown'
    source_subject_ref: str = ''
    target_subject_ref: str = ''
    evidence_refs: list[str] = Field(default_factory=list)
    provenance_ref: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BangumiGroupCard(BaseModel):
    ref: str = ''
    group_kind: Literal['season_group', 'collection_group', 'special_group', 'unknown'] = 'unknown'
    member_refs_visible: list[str] = Field(default_factory=list)
    sort_start: int = 0
    sort_end: int = 0
    ep_start: int = 0
    ep_end: int = 0
    title_examples: list[str] = Field(default_factory=list)
    subject_refs: list[str] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)
    provenance_ref: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BangumiItemCard(BaseModel):
    ref: str = ''
    item_kind: Literal['episode', 'special', 'movie', 'unknown'] = 'unknown'
    episode_id: int = 0
    kind: str = ''
    type: str = ''
    sort: int = 0
    ep: int = 0
    subject_ref: str = ''
    title: str = ''
    name: str = ''
    name_cn: str = ''
    airdate: str = ''
    duration: str = ''
    duration_seconds: int = 0
    desc_short: str = ''
    synthetic: bool = False
    subject_level_target: str = ''
    source_form_hint: str = ''
    relation_to_main: str = ''
    provenance_ref: str = ''
    episode_number: int = 0
    parent_refs: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class QueryCard(BaseModel):
    ref: str = ''
    query_text: str = ''
    query_kind: Literal['subject_lookup', 'subject_search', 'relation_search', 'episode_search', 'unknown'] = 'unknown'
    query_origin: Literal['local_raw', 'agent_composed', 'bangumi_seed', 'unknown'] = 'unknown'
    source_refs: list[str] = Field(default_factory=list)
    result_refs: list[str] = Field(default_factory=list)
    included_terms: list[str] = Field(default_factory=list)
    ignored_terms: list[str] = Field(default_factory=list)
    reason: str = ''
    confidence: Literal['high', 'medium', 'low', 'unknown'] = 'unknown'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class QueryCandidate(BaseModel):
    query_text: str = ''
    source_refs: list[str] = Field(default_factory=list)
    included_terms: list[str] = Field(default_factory=list)
    ignored_terms: list[str] = Field(default_factory=list)
    reason: str = ''
    confidence: Literal['high', 'medium', 'low'] = 'low'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class QueryComposerOutput(BaseModel):
    queries: list[QueryCandidate] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class LocalStructureSpanSpec(BaseModel):
    span_ref: str = ''
    span_scope: Literal['package', 'directory', 'token_segment', 'residual', 'unpartitioned'] = 'unpartitioned'
    file_refs: list[str] = Field(default_factory=list)
    ordinal_start: int | None = None
    ordinal_end: int | None = None
    ordinal_count: int = 0
    ordering_basis: Literal['filename_ordinal_order', 'path_order', 'mixed', 'unknown'] = 'unknown'
    title_cues: list[str] = Field(default_factory=list)
    release_group_cues: list[str] = Field(default_factory=list)
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class LocalStructureOutput(BaseModel):
    spans: list[LocalStructureSpanSpec] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CaseContract(BaseModel):
    summary: str = ''
    expected_outcome: Literal['unknown', 'pass', 'fail_closed', 'needs_issue_response'] = 'unknown'
    main_file_refs: list[str] = Field(default_factory=list)
    supplemental_file_refs: list[str] = Field(default_factory=list)
    allowed_file_refs: list[str] = Field(default_factory=list)
    visible_target_refs: list[str] = Field(default_factory=list)
    final_target_rule: str = ''
    coverage_rule: str = ''
    duplicate_rule: str = ''
    support_rule: str = ''
    constraints: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class EvidenceRequest(BaseModel):
    request_ref: str = ''
    request_type: EvidenceRequestType = 'subject_lookup'
    anchor_file_refs: list[str] = Field(default_factory=list)
    local_span_ref: str = ''
    subject_refs: list[str] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)
    item_kind: str = ''
    group_refs: list[str] = Field(default_factory=list)
    query_refs: list[str] = Field(default_factory=list)
    relation_kinds: list[Literal['prequel', 'sequel', 'side_story', 'adaptation', 'parent', 'child', 'unknown']] = Field(default_factory=list)
    episode_scope: str = ''
    sort_start: int = 0
    sort_end: int = 0
    include_episode_cards: bool = False
    max_subjects: int = 0
    max_episode_cards: int = 0
    expected_count: int = 0
    local_count: int = 0
    reason: str = ''
    expected_decision: Literal['unknown', 'accept', 'reject', 'defer', 'need_more_evidence'] = 'unknown'
    priority: Literal['low', 'normal', 'high', 'urgent'] = 'normal'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class EvidenceMenuRequest(BaseModel):
    request_id: str = ''
    request_type: str = ''
    summary: str = ''
    neutral: bool = True
    source_refs: list[str] = Field(default_factory=list)
    expected_result: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class EvidenceMenuRequestSummary(BaseModel):
    request_ids: list[str] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class EvidenceRequestResult(BaseModel):
    request_ref: str = ''
    request_type: EvidenceRequestType = 'subject_lookup'
    accepted: bool = False
    response_refs: list[str] = Field(default_factory=list)
    response_ref_count: int = 0
    response_ref_range: str = ''
    response_ref_samples: list[str] = Field(default_factory=list)
    returned_card_count: int = 0
    returned_card_count_range: str = ''
    returned_card_count_samples: list[str] = Field(default_factory=list)
    returned_card_count_summary: str = ''
    returned_card_count_truncated: bool = False
    truncated_for_prompt: bool = False
    bangumi_span_cards: list['BangumiSpanCard'] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class EvidenceBatchResult(BaseModel):
    batch_ref: str = ''
    round_index: int = 0
    status: Literal['accepted', 'partial', 'rejected', 'error', 'empty'] = 'accepted'
    request_results: list[EvidenceRequestResult] = Field(default_factory=list)
    provenance_refs: list[str] = Field(default_factory=list)
    results: list[EvidenceRequestResult] = Field(default_factory=list)
    added_subject_cards: list[BangumiSubjectCard] = Field(default_factory=list)
    added_relation_cards: list[BangumiRelationCard] = Field(default_factory=list)
    added_group_cards: list[BangumiGroupCard] = Field(default_factory=list)
    added_item_cards: list[BangumiItemCard] = Field(default_factory=list)
    added_provenance_cards: list[ProvenanceCard] = Field(default_factory=list)
    enriched_subject_cards: list[BangumiSubjectCard] = Field(default_factory=list)
    enriched_item_cards: list[BangumiItemCard] = Field(default_factory=list)
    budget_after: CaseBudget = Field(default_factory=CaseBudget)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class Hypothesis(BaseModel):
    ref: str = ''
    claim: str = ''
    confidence: Literal['High', 'Medium', 'Low', 'unknown'] = 'unknown'
    evidence_refs: list[str] = Field(default_factory=list)
    status: Literal['active', 'rejected', 'unknown'] = 'unknown'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class EvidenceGap(BaseModel):
    ref: str = ''
    gap_kind: Literal['missing_subject', 'missing_episode', 'missing_relation', 'missing_query', 'unknown'] = 'unknown'
    description: str = ''
    needed_refs: list[str] = Field(default_factory=list)
    needed_ref_summary: 'RefSummary' = Field(default_factory=lambda: RefSummary())

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CandidateComparison(BaseModel):
    ref: str = ''
    left_ref: str = ''
    right_ref: str = ''
    winner_ref: str = ''
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class MappingDraftRow(BaseModel):
    row_ref: str = ''
    local_ref: str = ''
    local_ref_kind: Literal['file', 'span'] = 'file'
    candidate_target_refs: list[str] = Field(default_factory=list)
    selected_target_ref: str = ''
    selected_target_kind: Literal['item', 'span', 'none'] = 'none'
    mapping_mode: Literal['explicit', 'span_by_index', 'unresolved'] = 'unresolved'
    support_refs: list[str] = Field(default_factory=list)
    requested_request_types: list[EvidenceRequestType] = Field(default_factory=list)
    query_hints: list[str] = Field(default_factory=list)
    query_refs: list[str] = Field(default_factory=list)
    subject_refs: list[str] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)
    local_refs: list[str] = Field(default_factory=list)
    status: Literal['open', 'proposed', 'verified', 'rejected', 'unresolved'] = 'open'
    disposition: Literal[
        'open',
        'map_to_bangumi',
        'non_bangumi_or_supplemental',
        'needs_more_evidence',
        'unaligned_fail_closed',
    ] = 'open'
    reason_kind: str = ''
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class MappingDraft(BaseModel):
    draft_ref: str = 'MD1'
    rows: list[MappingDraftRow] = Field(default_factory=list)
    version: int = 0

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class MappingDraftCoverageSummary(BaseModel):
    main_file_count: int = 0
    covered_main_file_count: int = 0
    missing_main_file_count: int = 0
    overlap_count: int = 0
    partition_complete: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class MappingDraftAccounting(BaseModel):
    main_file_count: int = 0
    draft_row_count: int = 0
    mapped_file_count: int = 0
    excluded_file_count: int = 0
    needs_more_evidence_file_count: int = 0
    unaligned_file_count: int = 0
    open_file_count: int = 0
    accounted_for_count: int = 0
    unresolved_count: int = 0
    duplicate_local_ref_count: int = 0
    missing_main_file_count: int = 0
    overlap_main_file_count: int = 0
    accepted_accounting_ready: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class MappingDraftPatch(BaseModel):
    op: Literal[
        'add_candidate',
        'reject_candidate',
        'propose_span_mapping',
        'propose_explicit_mapping',
        'mark_unresolved',
        'retract_mapping',
        'map_to_bangumi',
        'mark_non_bangumi_or_supplemental',
        'needs_more_evidence',
        'mark_unaligned_fail_closed',
    ] = 'mark_unresolved'
    local_ref: str = ''
    target_ref: str = ''
    target_span_ref: str = ''
    mapping_mode: Literal['explicit', 'span_by_index', 'unresolved'] = 'unresolved'
    support_refs: list[str] = Field(default_factory=list)
    reason_kind: str = ''
    needed_evidence_type: str = ''
    menu_request_ids: list[str] = Field(default_factory=list)
    requested_request_types: list[EvidenceRequestType] = Field(default_factory=list)
    query_hints: list[str] = Field(default_factory=list)
    subject_refs: list[str] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)
    local_refs: list[str] = Field(default_factory=list)
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class Contradiction(BaseModel):
    ref: str = ''
    contradiction_kind: Literal['subject_mismatch', 'episode_mismatch', 'relation_mismatch', 'scope_mismatch', 'unknown'] = 'unknown'
    evidence_refs: list[str] = Field(default_factory=list)
    description: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class Finding(BaseModel):
    ref: str = Field(default='', description='A judge-created finding ref declared in this output. AssignmentIntent.support_finding_refs may cite only refs that actually appear in this findings list.')
    finding_kind: Literal['pass', 'warning', 'blocked', 'unknown'] = 'unknown'
    description: str = ''
    evidence_refs: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class RejectedCandidate(BaseModel):
    ref: str = ''
    candidate_ref: str = ''
    reason: str = ''
    evidence_refs: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class LocalPartitionDecision(BaseModel):
    ref: str = ''
    partition_kind: Literal['keep', 'split', 'merge', 'unknown'] = 'unknown'
    file_refs: list[str] = Field(default_factory=list)
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class AssignmentIntent(BaseModel):
    ref: str = ''
    file_ref: str = ''
    target_ref: str = ''
    target_refs: list[str] = Field(default_factory=list, description='Optional visible BE refs when one local file semantically covers a multi-part Bangumi surface. target_ref is the primary representative and must be included when target_refs is non-empty.')
    support_finding_refs: list[str] = Field(default_factory=list, description='Finding refs from this same output only. Reuse a broad finding ref for many assignments when it supports a contiguous span; do not invent per-assignment finding refs unless those findings are explicitly present in findings.')
    support_card_refs: list[str] = Field(default_factory=list, description='Visible dossier card refs only. For BE targets include both file_ref and target_ref; for UNALIGNED include file_ref and do not include UNALIGNED.')
    confidence: Literal['high', 'medium', 'low'] = 'low'
    risk_flags: list[Literal['none', 'ambiguous_ownership', 'weak_title_match', 'sequence_gap', 'duplicate_like', 'compact_evidence', 'related_subject', 'search_result', 'synthetic_singleton', 'special_or_ova']] = Field(default_factory=list)
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class LocalSpanCard(BaseModel):
    ref: str = ''
    span_scope: Literal['package', 'directory', 'token_segment', 'residual', 'unpartitioned', 'unknown'] = 'unknown'
    parent_key: str = ''
    season_cue: str = ''
    file_refs: list[str] = Field(default_factory=list)
    file_ref_count: int = 0
    file_ref_range: list[str] = Field(default_factory=list)
    file_ref_samples: list[str] = Field(default_factory=list)
    ordering_basis: Literal['episode_token_order', 'path_order', 'mixed', 'unknown'] = 'unknown'
    episode_token_start: int | None = None
    episode_token_end: int | None = None
    episode_token_count: int = 0
    gap_count: int = 0
    duplicate_count: int = 0
    title_cues: list[str] = Field(default_factory=list)
    release_group_cues: list[str] = Field(default_factory=list)
    attention_file_refs: list[str] = Field(default_factory=list, validation_alias=AliasChoices('attention_file_refs', 'abnormal_file_refs'))
    confidence_facts: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BangumiSpanCard(BaseModel):
    ref: str = ''
    subject_ref: str = ''
    group_ref: str = ''
    target_refs: list[str] = Field(default_factory=list)
    target_ref_count: int = 0
    target_ref_range: list[str] = Field(default_factory=list)
    target_ref_samples: list[str] = Field(default_factory=list)
    sort_start: int | None = None
    sort_end: int | None = None
    ep_start: int | None = None
    ep_end: int | None = None
    item_kind: Literal['regular', 'special', 'mixed', 'unknown'] = 'unknown'
    gap_count: int = 0
    duplicate_count: int = 0
    special_count: int = 0
    title_samples: list[str] = Field(default_factory=list)
    detail_equivalent: bool = False
    source_request_ref: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class SpanAlignmentClaim(BaseModel):
    ref: str = ''
    local_span_ref: str = ''
    bangumi_span_ref: str = ''
    alignment_type: Literal['ordered_one_to_one'] = 'ordered_one_to_one'
    start_basis: str = ''
    end_basis: str = ''
    order_basis: str = ''
    support_refs: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    confidence: Literal['high', 'medium', 'low'] = 'low'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BulkAssignmentIntent(BaseModel):
    ref: str = ''
    local_span_ref: str = ''
    bangumi_span_ref: str = ''
    alignment_ref: str = ''
    mode: Literal['by_index'] = 'by_index'
    support_finding_refs: list[str] = Field(default_factory=list)
    support_card_refs: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class FailClosedReason(BaseModel):
    ref: str = ''
    reason_kind: Literal['budget_exhausted', 'contradiction', 'insufficient_evidence', 'unknown'] = 'unknown'
    description: str = ''
    related_refs: list[str] = Field(default_factory=list)
    related_ref_summary: 'RefSummary' = Field(default_factory=lambda: RefSummary())

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class IssueResponse(BaseModel):
    ref: str = ''
    issue_kind: Literal['clarify_scope', 'request_more_evidence', 'explain_failure', 'unknown'] = 'unknown'
    message: str = ''
    related_refs: list[str] = Field(default_factory=list)
    related_ref_summary: 'RefSummary' = Field(default_factory=lambda: RefSummary())

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class SelfCheck(BaseModel):
    ref: str = ''
    check_kind: Literal['consistency', 'coverage', 'budget', 'unknown'] = 'unknown'
    passed: bool = False
    findings: list[Finding] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class RefSummary(BaseModel):
    count: int = 0
    sample_refs: list[str] = Field(default_factory=list)
    ref_range: str = ''
    description: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CaseJudgeOutput(BaseModel):
    action: CaseAction = 'request_evidence'
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    evidence_menu_request_ids: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    candidate_comparisons: list[CandidateComparison] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    local_partition_decisions: list[LocalPartitionDecision] = Field(default_factory=list)
    assignment_intents: list[AssignmentIntent] = Field(default_factory=list)
    span_alignment_claims: list[SpanAlignmentClaim] = Field(default_factory=list)
    bulk_assignment_intents: list[BulkAssignmentIntent] = Field(default_factory=list)
    fail_closed_reasons: list[FailClosedReason] = Field(default_factory=list)
    issue_responses: list[IssueResponse] = Field(default_factory=list)
    self_checks: list[SelfCheck] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


def iter_case_judge_ref_lists(output: CaseJudgeOutput):
    for item in output.hypotheses:
        yield ('hypotheses.evidence_refs', getattr(item, 'evidence_refs', []))
    for item in output.findings:
        yield ('findings.evidence_refs', getattr(item, 'evidence_refs', []))
    for item in output.evidence_gaps:
        yield ('evidence_gaps.needed_refs', getattr(item, 'needed_refs', []))
    for item in output.fail_closed_reasons:
        yield ('fail_closed_reasons.related_refs', getattr(item, 'related_refs', []))
    for item in output.issue_responses:
        yield ('issue_responses.related_refs', getattr(item, 'related_refs', []))
    for item in output.assignment_intents:
        yield ('assignment_intents.support_finding_refs', getattr(item, 'support_finding_refs', []))
        yield ('assignment_intents.support_card_refs', getattr(item, 'support_card_refs', []))
    for item in output.candidate_comparisons:
        for field in ('evidence_refs',):
            yield (f'candidate_comparisons.{field}', getattr(item, field, []))
    for item in output.local_partition_decisions:
        for field in ('file_refs',):
            yield (f'local_partition_decisions.{field}', getattr(item, field, []))


class VerifierIssue(BaseModel):
    ref: str = ''
    issue_code: str = ''
    severity: Literal['info', 'warning', 'blocked', 'unknown'] = 'unknown'
    message: str = ''
    related_refs: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CaseVerifierResult(BaseModel):
    passed: bool = False
    issues: list[VerifierIssue] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CaseAuditManifest(BaseModel):
    case_id: str = ''
    audit_round: int = 0
    verifier_refs: list[str] = Field(default_factory=list)
    issue_refs: list[str] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CaseDossier(BaseModel):
    header: CaseHeader = Field(default_factory=CaseHeader)
    budget: CaseBudget = Field(default_factory=CaseBudget)
    visible_refs: VisibleRefCatalog = Field(default_factory=VisibleRefCatalog)
    local_files: list[LocalFileCard] = Field(default_factory=list)
    local_clusters: list[LocalClusterCard] = Field(default_factory=list)
    bangumi_subjects: list[BangumiSubjectCard] = Field(default_factory=list)
    bangumi_relations: list[BangumiRelationCard] = Field(default_factory=list)
    bangumi_groups: list[BangumiGroupCard] = Field(default_factory=list)
    bangumi_items: list[BangumiItemCard] = Field(default_factory=list)
    query_cards: list[QueryCard] = Field(default_factory=list)
    provenance_cards: list[ProvenanceCard] = Field(default_factory=list)
    contract: CaseContract = Field(default_factory=CaseContract)
    detailed_card_refs: list[str] = Field(default_factory=list)
    assignable_target_refs: list[str] = Field(default_factory=list)
    seen_detail_refs: list[str] = Field(default_factory=list)
    previous_hypotheses: list[Hypothesis] = Field(default_factory=list)
    previous_evidence_results: list[EvidenceBatchResult] = Field(default_factory=list)
    verifier_issues: list[VerifierIssue] = Field(default_factory=list)
    local_span_cards: list[LocalSpanCard] = Field(default_factory=list)
    bangumi_span_cards: list[BangumiSpanCard] = Field(default_factory=list)
    plan_state: EvidencePlan = Field(default_factory=EvidencePlan)
    mapping_draft: MappingDraft | None = None
    mapping_draft_patches: list[MappingDraftPatch] = Field(default_factory=list)
    mapping_draft_candidate_comparisons: list[CandidateComparison] = Field(default_factory=list)


class BoundedCaseDossier(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)
    primary_title_cues: list[str] = Field(default_factory=list)
    release_group_cues: list[str] = Field(default_factory=list)
    query_card_sample: list[QueryCard] = Field(default_factory=list)
    main_file_overview: dict[str, object] = Field(default_factory=dict)
    target_overview: list[dict[str, object]] = Field(default_factory=list)
    detailed_visible_cards: list[BangumiItemCard] = Field(default_factory=list)
    available_detail_request_types: list[str] = Field(default_factory=list)
    catalog_refs: VisibleRefCatalog = Field(default_factory=VisibleRefCatalog)
    detailed_card_refs: list[str] = Field(default_factory=list)
    assignable_target_refs: list[str] = Field(default_factory=list)
    seen_detail_refs: list[str] = Field(default_factory=list)
    previous_evidence_results: list[EvidenceBatchResult] = Field(default_factory=list)
    verifier_issue_summary: list[str] = Field(default_factory=list)
    local_span_cards: list[dict[str, object]] = Field(default_factory=list)
    bangumi_span_cards: list[dict[str, object]] = Field(default_factory=list)
    plan_state: dict[str, object] = Field(default_factory=dict)
    round_context: str = 'initial'
    salience_overview: dict[str, object] = Field(default_factory=dict)
    detailed_local_file_cards: list[LocalFileCard] = Field(default_factory=list)
    requested_detail_refs: list[str] = Field(default_factory=list)
    visible_refs: VisibleRefCatalog = Field(default_factory=VisibleRefCatalog)
    contract: CaseContract = Field(default_factory=CaseContract)
    header: CaseHeader = Field(default_factory=CaseHeader)
    budget: CaseBudget = Field(default_factory=CaseBudget)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')
