from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from typing import Iterable, Mapping, Sequence

from .models import (
    BangumiGroupCard,
    BangumiItemCard,
    BangumiRelationCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseContract,
    CaseDossier,
    CaseHeader,
    CandidateComparison,
    EvidencePlan,
    EvidenceBatchResult,
    EvidenceRequestResult,
    LocalClusterCard,
    LocalFileCard,
    LocalSpanCard,
    MappingDraft,
    MappingDraftPatch,
    ProvenanceCard,
    QueryCard,
    VerifierIssue,
    VisibleRefCatalog,
)
from .span_builder import build_bangumi_span_cards, compact_span_card


@dataclass(frozen=True)
class CaseEvidenceWorkspace:
    header: CaseHeader
    budget: CaseBudget
    contract: CaseContract = field(default_factory=CaseContract)
    local_files: list[LocalFileCard] = field(default_factory=list)
    local_clusters: list[LocalClusterCard] = field(default_factory=list)
    local_span_cards: list[LocalSpanCard] = field(default_factory=list)
    bangumi_subjects: list[BangumiSubjectCard] = field(default_factory=list)
    bangumi_relations: list[BangumiRelationCard] = field(default_factory=list)
    bangumi_groups: list[BangumiGroupCard] = field(default_factory=list)
    bangumi_items: list[BangumiItemCard] = field(default_factory=list)
    bangumi_span_cards: list = field(default_factory=list)
    query_cards: list[QueryCard] = field(default_factory=list)
    provenance_cards: list[ProvenanceCard] = field(default_factory=list)
    previous_hypotheses: list = field(default_factory=list)
    previous_evidence_results: list[EvidenceBatchResult] = field(default_factory=list)
    verifier_issues: list[VerifierIssue] = field(default_factory=list)
    verifier_issue_summary: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    seen_detail_refs: list[str] = field(default_factory=list)
    judge_request_audits: list[dict[str, object]] = field(default_factory=list)
    mapping_draft: MappingDraft | None = None
    mapping_draft_patches: list[MappingDraftPatch] = field(default_factory=list)
    mapping_draft_candidate_comparisons: list[CandidateComparison] = field(default_factory=list)
    plan_state: EvidencePlan = field(default_factory=EvidencePlan)

    @classmethod
    def from_cards(
        cls,
        *,
        header: CaseHeader,
        budget: CaseBudget,
        local_files: Sequence[LocalFileCard] = (),
        local_clusters: Sequence[LocalClusterCard] = (),
        local_span_cards: Sequence[LocalSpanCard] = (),
        bangumi_subjects: Sequence[BangumiSubjectCard] = (),
        bangumi_relations: Sequence[BangumiRelationCard] = (),
        bangumi_groups: Sequence[BangumiGroupCard] = (),
        bangumi_items: Sequence[BangumiItemCard] = (),
        bangumi_span_cards: Sequence = (),
        query_cards: Sequence[QueryCard] = (),
        provenance_cards: Sequence[ProvenanceCard] = (),
        contract: CaseContract | None = None,
        plan_state: EvidencePlan | None = None,
        mapping_draft: MappingDraft | None = None,
        mapping_draft_patches: Sequence[MappingDraftPatch] = (),
        mapping_draft_candidate_comparisons: Sequence[CandidateComparison] = (),
        previous_hypotheses: Sequence = (),
        previous_evidence_results: Sequence[EvidenceBatchResult] = (),
        verifier_issues: Sequence[VerifierIssue] = (),
        diagnostics: Sequence[str] = (),
    ) -> 'CaseEvidenceWorkspace':
        active_contract = contract or CaseContract(
            main_file_refs=[card.ref for card in local_files if getattr(card, 'is_main', False)],
            supplemental_file_refs=[card.ref for card in local_files if not getattr(card, 'is_main', False)],
            allowed_file_refs=[card.ref for card in local_files],
            visible_target_refs=[card.ref for card in bangumi_items if getattr(card, 'ref', '')],
        )
        return cls(
            header=header,
            budget=budget,
            contract=active_contract,
            local_files=list(local_files),
            local_clusters=list(local_clusters),
            local_span_cards=list(local_span_cards),
            bangumi_subjects=list(bangumi_subjects),
            bangumi_relations=list(bangumi_relations),
            bangumi_groups=list(bangumi_groups),
            bangumi_items=list(bangumi_items),
            bangumi_span_cards=list(bangumi_span_cards) or list(build_bangumi_span_cards(bangumi_items=list(bangumi_items))),
            query_cards=list(query_cards),
            provenance_cards=list(provenance_cards),
            previous_hypotheses=list(previous_hypotheses),
            previous_evidence_results=list(previous_evidence_results),
            verifier_issues=list(verifier_issues),
            verifier_issue_summary=[issue.message for issue in verifier_issues],
            diagnostics=list(diagnostics),
            seen_detail_refs=[],
            judge_request_audits=[],
            mapping_draft=mapping_draft,
            mapping_draft_patches=list(mapping_draft_patches),
            mapping_draft_candidate_comparisons=list(mapping_draft_candidate_comparisons),
            plan_state=plan_state or EvidencePlan(),
        )

    def visible_refs(self) -> VisibleRefCatalog:
        raw_target_refs = list(self.contract.visible_target_refs or [])
        target_refs = list(dict.fromkeys(raw_target_refs))
        duplicates = [ref for ref, count in Counter(raw_target_refs).items() if count > 1]
        if duplicates and 'dossier_target_ref_duplicate' not in self.diagnostics:
            object.__setattr__(self, 'diagnostics', [*self.diagnostics, 'dossier_target_ref_duplicate'])
        return VisibleRefCatalog(
            local_file_refs=[card.ref for card in self.local_files],
            local_cluster_refs=[card.ref for card in self.local_clusters],
            # local_span_cards are propagated separately via dossier/prompt payloads
            bangumi_subject_refs=[card.ref for card in self.bangumi_subjects],
            bangumi_relation_refs=[card.ref for card in self.bangumi_relations],
            bangumi_group_refs=[card.ref for card in self.bangumi_groups],
            bangumi_item_refs=[card.ref for card in self.bangumi_items],
            query_refs=[card.ref for card in self.query_cards],
            target_refs=target_refs,
        )

    def all_visible_ref_set(self) -> set[str]:
        catalog = self.visible_refs()
        return {
            *catalog.local_file_refs,
            *catalog.local_cluster_refs,
            *catalog.bangumi_subject_refs,
            *catalog.bangumi_relation_refs,
            *catalog.bangumi_group_refs,
            *catalog.bangumi_item_refs,
            *catalog.query_refs,
            *catalog.target_refs,
            *[card.ref for card in self.provenance_cards],
        }

    def has_ref(self, ref: str) -> bool:
        return self.get_ref_kind(ref) != 'unknown'

    def get_ref_kind(self, ref: str) -> str:
        lookup = self._ref_kind_map()
        return lookup.get(ref, 'unknown')

    def is_visible_target(self, ref: str) -> bool:
        return ref == 'UNALIGNED' or ref in self.visible_refs().target_refs

    def with_added_evidence(
        self,
        *,
        subjects: Sequence[BangumiSubjectCard] = (),
        relations: Sequence[BangumiRelationCard] = (),
        groups: Sequence[BangumiGroupCard] = (),
        items: Sequence[BangumiItemCard] = (),
        provenance: Sequence[ProvenanceCard] = (),
        evidence_results: Sequence[EvidenceBatchResult | EvidenceRequestResult] = (),
    ) -> 'CaseEvidenceWorkspace':
        additions = {
            'bangumi_subjects': list(subjects),
            'bangumi_relations': list(relations),
            'bangumi_groups': list(groups),
            'bangumi_items': list(items),
            'provenance_cards': list(provenance),
        }
        self._ensure_no_duplicate_refs(additions)
        returned_refs: list[str] = []
        for rr in evidence_results:
            returned_refs.extend([ref for ref in (getattr(rr, 'response_refs', None) or []) if ref])
        merged_seen = list(dict.fromkeys([*self.seen_detail_refs, *returned_refs]))
        result = CaseEvidenceWorkspace.from_cards(
            header=self.header,
            budget=self.budget,
            contract=_contract_with_added_target_refs(self.contract, items),
            local_files=self.local_files,
            local_clusters=self.local_clusters,
            local_span_cards=self.local_span_cards,
            bangumi_subjects=[*self.bangumi_subjects, *subjects],
            bangumi_relations=[*self.bangumi_relations, *relations],
            bangumi_groups=[*self.bangumi_groups, *groups],
            bangumi_items=[*self.bangumi_items, *items],
            bangumi_span_cards=[*self.bangumi_span_cards, *[span for rr in evidence_results for span in (getattr(rr, 'bangumi_span_cards', []) or [])]],
            query_cards=self.query_cards,
            provenance_cards=[*self.provenance_cards, *provenance],
            mapping_draft=self.mapping_draft,
            mapping_draft_patches=self.mapping_draft_patches,
            mapping_draft_candidate_comparisons=self.mapping_draft_candidate_comparisons,
            previous_hypotheses=self.previous_hypotheses,
            previous_evidence_results=[*self.previous_evidence_results, *[rr for rr in evidence_results if isinstance(rr, EvidenceBatchResult)]],
            verifier_issues=self.verifier_issues,
            diagnostics=self.diagnostics,
            plan_state=self.plan_state,
        )
        object.__setattr__(result, 'seen_detail_refs', merged_seen)
        object.__setattr__(result, 'previous_evidence_results', [*self.previous_evidence_results, *[rr for rr in evidence_results if isinstance(rr, EvidenceBatchResult)]])
        object.__setattr__(result, 'judge_request_audits', list(self.judge_request_audits))
        return result

    def with_replaced_cards(
        self,
        *,
        subjects: Sequence[BangumiSubjectCard] = (),
        items: Sequence[BangumiItemCard] = (),
        relations: Sequence[BangumiRelationCard] = (),
        groups: Sequence[BangumiGroupCard] = (),
        provenance: Sequence[ProvenanceCard] = (),
        evidence_results: Sequence[EvidenceBatchResult | EvidenceRequestResult] = (),
    ) -> 'CaseEvidenceWorkspace':
        subject_map = {card.ref: card for card in self.bangumi_subjects}
        item_map = {card.ref: card for card in self.bangumi_items}
        relation_map = {card.ref: card for card in self.bangumi_relations}
        group_map = {card.ref: card for card in self.bangumi_groups}
        prov_map = {card.ref: card for card in self.provenance_cards}
        for card in subjects:
            subject_map[card.ref] = card
        for card in items:
            item_map[card.ref] = card
        for card in relations:
            relation_map[card.ref] = card
        for card in groups:
            group_map[card.ref] = card
        for card in provenance:
            prov_map[card.ref] = card
        updated = CaseEvidenceWorkspace.from_cards(
            header=self.header,
            budget=self.budget,
            contract=_contract_with_added_target_refs(self.contract, items),
            local_files=self.local_files,
            local_clusters=self.local_clusters,
            local_span_cards=self.local_span_cards,
            bangumi_subjects=list(subject_map.values()),
            bangumi_relations=list(relation_map.values()),
            bangumi_groups=list(group_map.values()),
            bangumi_items=list(item_map.values()),
            bangumi_span_cards=[*self.bangumi_span_cards, *[span for rr in evidence_results for span in (getattr(rr, 'bangumi_span_cards', []) or [])]],
            query_cards=self.query_cards,
            provenance_cards=list(prov_map.values()),
            mapping_draft=self.mapping_draft,
            mapping_draft_patches=self.mapping_draft_patches,
            mapping_draft_candidate_comparisons=self.mapping_draft_candidate_comparisons,
            previous_hypotheses=self.previous_hypotheses,
            previous_evidence_results=[*self.previous_evidence_results, *[rr for rr in evidence_results if isinstance(rr, EvidenceBatchResult)]],
            verifier_issues=self.verifier_issues,
            plan_state=self.plan_state,
        )
        object.__setattr__(updated, 'seen_detail_refs', list(self.seen_detail_refs))
        object.__setattr__(updated, 'judge_request_audits', list(self.judge_request_audits))
        return updated

    def with_mapping_draft(self, draft: MappingDraft | None) -> 'CaseEvidenceWorkspace':
        updated = CaseEvidenceWorkspace.from_cards(
            header=self.header,
            budget=self.budget,
            contract=self.contract,
            local_files=self.local_files,
            local_clusters=self.local_clusters,
            local_span_cards=self.local_span_cards,
            bangumi_subjects=self.bangumi_subjects,
            bangumi_relations=self.bangumi_relations,
            bangumi_groups=self.bangumi_groups,
            bangumi_items=self.bangumi_items,
            bangumi_span_cards=self.bangumi_span_cards,
            query_cards=self.query_cards,
            provenance_cards=self.provenance_cards,
            mapping_draft=draft,
            mapping_draft_patches=self.mapping_draft_patches,
            mapping_draft_candidate_comparisons=self.mapping_draft_candidate_comparisons,
            previous_hypotheses=self.previous_hypotheses,
            previous_evidence_results=self.previous_evidence_results,
            verifier_issues=self.verifier_issues,
            diagnostics=self.diagnostics,
            plan_state=self.plan_state,
        )
        object.__setattr__(updated, 'seen_detail_refs', list(self.seen_detail_refs))
        object.__setattr__(updated, 'judge_request_audits', list(self.judge_request_audits))
        return updated

    def with_query_cards(self, query_cards: Sequence[QueryCard]) -> 'CaseEvidenceWorkspace':
        existing_by_ref = {card.ref: card for card in self.query_cards if card.ref}
        merged = list(self.query_cards)
        seen_refs = set(existing_by_ref)
        seen_text_origin = {
            (str(card.query_origin or ''), str(card.query_text or '').strip().casefold())
            for card in self.query_cards
            if str(card.query_text or '').strip()
        }
        for card in query_cards:
            if not card.ref or card.ref in seen_refs:
                continue
            text_key = (str(card.query_origin or ''), str(card.query_text or '').strip().casefold())
            if text_key in seen_text_origin:
                continue
            merged.append(card)
            seen_refs.add(card.ref)
            seen_text_origin.add(text_key)
        updated = CaseEvidenceWorkspace.from_cards(
            header=self.header,
            budget=self.budget,
            contract=self.contract,
            local_files=self.local_files,
            local_clusters=self.local_clusters,
            local_span_cards=self.local_span_cards,
            bangumi_subjects=self.bangumi_subjects,
            bangumi_relations=self.bangumi_relations,
            bangumi_groups=self.bangumi_groups,
            bangumi_items=self.bangumi_items,
            bangumi_span_cards=self.bangumi_span_cards,
            query_cards=merged,
            provenance_cards=self.provenance_cards,
            mapping_draft=self.mapping_draft,
            mapping_draft_patches=self.mapping_draft_patches,
            mapping_draft_candidate_comparisons=self.mapping_draft_candidate_comparisons,
            previous_hypotheses=self.previous_hypotheses,
            previous_evidence_results=self.previous_evidence_results,
            verifier_issues=self.verifier_issues,
            diagnostics=self.diagnostics,
            plan_state=self.plan_state,
        )
        object.__setattr__(updated, 'seen_detail_refs', list(self.seen_detail_refs))
        object.__setattr__(updated, 'judge_request_audits', list(self.judge_request_audits))
        return updated

    def to_dossier(self, contract: CaseContract | None = None, *, round_context: str = 'initial') -> CaseDossier:
        active_contract = contract or self.contract
        catalog = self.visible_refs()
        detailed_target_cards = _boundary_cards(self.bangumi_items, limit=8, seen_refs=self.seen_detail_refs)
        detailed_target_refs = [card.ref for card in detailed_target_cards]
        legal_target_refs = set(catalog.target_refs or [])
        detail_surface_refs = _merge_detail_surface_refs(self.seen_detail_refs, detailed_target_refs)
        assignable_target_refs = [ref for ref in detail_surface_refs if ref in legal_target_refs]
        prompt_assignable_target_refs = assignable_target_refs[:12]
        prompt_seen_detail_refs = list(dict.fromkeys(self.seen_detail_refs))[:12]
        detailed_local_cards = [card for card in self.local_files if card.ref in self.seen_detail_refs]
        bangumi_span_cards = list(self.bangumi_span_cards)
        for batch in self.previous_evidence_results:
            for rr in getattr(batch, 'request_results', []) or []:
                bangumi_span_cards.extend(list(getattr(rr, 'bangumi_span_cards', []) or []))
        return CaseDossier(
            header=self.header,
            budget=self.budget,
            contract=active_contract,
            visible_refs=catalog,
            local_files=list(self.local_files),
            local_clusters=list(self.local_clusters),
            local_span_cards=list(self.local_span_cards),
            bangumi_subjects=list(self.bangumi_subjects),
            bangumi_relations=list(self.bangumi_relations),
            bangumi_groups=list(self.bangumi_groups),
            bangumi_items=list(self.bangumi_items),
            query_cards=list(self.query_cards),
            provenance_cards=list(self.provenance_cards),
            mapping_draft=self.mapping_draft,
            mapping_draft_patches=self.mapping_draft_patches,
            mapping_draft_candidate_comparisons=self.mapping_draft_candidate_comparisons,
            detailed_card_refs=detailed_target_refs,
            assignable_target_refs=assignable_target_refs,
            seen_detail_refs=list(dict.fromkeys([*self.seen_detail_refs, *detailed_target_refs])),
            detailed_local_file_cards=detailed_local_cards,
            previous_hypotheses=list(self.previous_hypotheses),
            previous_evidence_results=[card for card in self.previous_evidence_results if hasattr(card, 'batch_ref')],
            verifier_issues=list(self.verifier_issues),
            bangumi_span_cards=bangumi_span_cards or build_bangumi_span_cards(bangumi_items=list(self.bangumi_items)),
            plan_state=self.plan_state,
            round_context=round_context,
        )

    def with_seen_detail_refs(self, refs: Sequence[str]) -> 'CaseEvidenceWorkspace':
        merged = list(dict.fromkeys([*self.seen_detail_refs, *refs]))
        updated = CaseEvidenceWorkspace.from_cards(
            header=self.header,
            budget=self.budget,
            contract=self.contract,
            local_files=self.local_files,
            local_clusters=self.local_clusters,
            local_span_cards=self.local_span_cards,
            bangumi_subjects=self.bangumi_subjects,
            bangumi_relations=self.bangumi_relations,
            bangumi_groups=self.bangumi_groups,
            bangumi_items=self.bangumi_items,
            bangumi_span_cards=self.bangumi_span_cards,
            query_cards=self.query_cards,
            provenance_cards=self.provenance_cards,
            mapping_draft=self.mapping_draft,
            mapping_draft_patches=self.mapping_draft_patches,
            mapping_draft_candidate_comparisons=self.mapping_draft_candidate_comparisons,
            previous_hypotheses=self.previous_hypotheses,
            previous_evidence_results=self.previous_evidence_results,
            verifier_issues=self.verifier_issues,
            diagnostics=self.diagnostics,
            plan_state=self.plan_state,
        )
        object.__setattr__(updated, 'seen_detail_refs', merged)
        object.__setattr__(updated, 'judge_request_audits', list(self.judge_request_audits))
        return updated

    def _ref_kind_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for card in self.local_files:
            mapping[card.ref] = 'local_file'
        for card in self.local_clusters:
            mapping[card.ref] = 'local_cluster'
        for card in self.bangumi_subjects:
            mapping[card.ref] = 'bangumi_subject'
        for card in self.bangumi_relations:
            mapping[card.ref] = 'bangumi_relation'
        for card in self.bangumi_groups:
            mapping[card.ref] = 'bangumi_group'
        for card in self.bangumi_items:
            mapping[card.ref] = 'bangumi_item'
        for card in self.query_cards:
            mapping[card.ref] = 'query'
        for card in self.provenance_cards:
            mapping[card.ref] = 'provenance'
        return mapping

    def _ensure_no_duplicate_refs(self, additions: Mapping[str, Sequence]) -> None:
        seen = self.all_visible_ref_set()
        for cards in additions.values():
            for card in cards:
                ref = getattr(card, 'ref', '')
                if not ref:
                    continue
                if ref in seen:
                    raise ValueError(f'duplicate ref: {ref}')
                seen.add(ref)


def _merge_detail_surface_refs(*refs: Sequence[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for seq in refs:
        for ref in seq:
            if ref and ref not in seen:
                seen.add(ref)
                merged.append(ref)
    return merged


def _contract_with_added_target_refs(contract: CaseContract, items: Sequence[BangumiItemCard]) -> CaseContract:
    new_refs = [card.ref for card in items if getattr(card, 'ref', '')]
    if not new_refs:
        return contract
    merged = list(dict.fromkeys([*list(contract.visible_target_refs or []), *new_refs]))
    return contract.model_copy(update={'visible_target_refs': merged})


def _boundary_cards(cards: Sequence[BangumiItemCard], *, limit: int, seen_refs: Sequence[str]) -> list[BangumiItemCard]:
    if len(cards) <= limit:
        return list(cards)
    half = max(1, limit // 2)
    refs = list(dict.fromkeys([*(card.ref for card in cards[:half]), *(card.ref for card in cards[-half:]), *seen_refs]))
    lookup = {card.ref: card for card in cards}
    return [lookup[ref] for ref in refs if ref in lookup]


def _boundary_cards(cards: Sequence[BangumiItemCard], *, limit: int, seen_refs: Sequence[str]) -> list[BangumiItemCard]:
    if len(cards) <= limit:
        return list(cards)
    half = max(1, limit // 2)
    refs = list(dict.fromkeys([*(card.ref for card in cards[:half]), *(card.ref for card in cards[-half:]), *seen_refs]))
    lookup = {card.ref: card for card in cards}
    return [lookup[ref] for ref in refs if ref in lookup]
