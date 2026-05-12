from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import (
    BangumiGroupCard,
    BangumiItemCard,
    BangumiRelationCard,
    BangumiSubjectCard,
    ProvenanceCard,
)
from .workspace import CaseEvidenceWorkspace


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_provenance_card(
    ref: str,
    retrieval_round: int,
    request_ref: str,
    source_operation: str,
    api_subject_id: int = 0,
    api_episode_id: int = 0,
    parent_refs: list[str] | None = None,
    raw_response_hash: str = '',
    raw_response_count: int = 0,
    created_at: str = '',
) -> ProvenanceCard:
    return ProvenanceCard(
        ref=ref,
        retrieval_round=retrieval_round,
        request_ref=request_ref,
        source_operation=source_operation,
        api_subject_id=api_subject_id,
        api_episode_id=api_episode_id,
        parent_refs=list(parent_refs or []),
        raw_response_hash=raw_response_hash,
        raw_response_count=raw_response_count,
        created_at=created_at or _utc_now_iso(),
    )


@dataclass
class EvidenceCardRegistry:
    subject_id_to_ref: dict[int, str]
    episode_id_to_ref: dict[int, str]
    synthetic_item_key_to_ref: dict[str, str]
    relation_key_to_ref: dict[tuple[str, str, str], str]
    group_key_to_ref: dict[tuple[str, str], str]
    visible_refs: set[str]
    _subject_seq: int = 0
    _relation_seq: int = 0
    _group_seq: int = 0
    _item_seq: int = 0
    _provenance_seq: int = 0

    @classmethod
    def from_workspace(cls, workspace: CaseEvidenceWorkspace) -> 'EvidenceCardRegistry':
        subject_id_to_ref: dict[int, str] = {}
        episode_id_to_ref: dict[int, str] = {}
        synthetic_item_key_to_ref: dict[str, str] = {}
        relation_key_to_ref: dict[tuple[str, str, str], str] = {}
        group_key_to_ref: dict[tuple[str, str], str] = {}
        visible_refs = set(workspace.all_visible_ref_set())

        for card in workspace.bangumi_subjects:
            if card.subject_id > 0:
                subject_id_to_ref.setdefault(card.subject_id, card.ref)
        for card in workspace.bangumi_items:
            if card.episode_id > 0:
                episode_id_to_ref.setdefault(card.episode_id, card.ref)
            if card.synthetic:
                key = card.title or card.name or card.ref
                synthetic_item_key_to_ref.setdefault(key, card.ref)
        for card in workspace.bangumi_relations:
            key = (card.source_subject_ref, card.target_subject_ref, card.relation_kind)
            relation_key_to_ref.setdefault(key, card.ref)
        for card in workspace.bangumi_groups:
            entity_ref = card.subject_refs[0] if card.subject_refs else card.item_refs[0] if card.item_refs else card.member_refs_visible[0] if card.member_refs_visible else card.ref
            key = (entity_ref, card.group_kind)
            group_key_to_ref.setdefault(key, card.ref)

        return cls(
            subject_id_to_ref=subject_id_to_ref,
            episode_id_to_ref=episode_id_to_ref,
            synthetic_item_key_to_ref=synthetic_item_key_to_ref,
            relation_key_to_ref=relation_key_to_ref,
            group_key_to_ref=group_key_to_ref,
            visible_refs=visible_refs,
            _subject_seq=_max_prefix_seq(visible_refs, 'BS'),
            _relation_seq=_max_prefix_seq(visible_refs, 'BREL'),
            _group_seq=_max_prefix_seq(visible_refs, 'BR'),
            _item_seq=_max_prefix_seq(visible_refs, 'BE'),
            _provenance_seq=_max_prefix_seq(visible_refs, 'PV'),
        )

    def ensure_new_ref_not_visible(self, ref: str) -> None:
        if ref in self.visible_refs:
            raise ValueError(f'ref already visible: {ref}')

    def allocate_subject_ref(self, subject_id: int) -> tuple[str, bool]:
        if subject_id > 0 and subject_id in self.subject_id_to_ref:
            return self.subject_id_to_ref[subject_id], False
        ref = self._next_ref('BS', '_subject_seq')
        if subject_id > 0:
            self.subject_id_to_ref[subject_id] = ref
        return ref, True

    def allocate_relation_ref(self, from_ref: str, to_ref: str, relation: str) -> tuple[str, bool]:
        key = (from_ref, to_ref, relation)
        if key in self.relation_key_to_ref:
            return self.relation_key_to_ref[key], False
        ref = self._next_ref('BREL', '_relation_seq')
        self.relation_key_to_ref[key] = ref
        return ref, True

    def allocate_group_ref(self, entity_ref: str, group_kind: str) -> tuple[str, bool]:
        key = (entity_ref, group_kind)
        if key in self.group_key_to_ref:
            return self.group_key_to_ref[key], False
        ref = self._next_ref('BR', '_group_seq')
        self.group_key_to_ref[key] = ref
        return ref, True

    def allocate_item_ref(self, episode_id: int, synthetic_key: str = '') -> tuple[str, bool]:
        if episode_id > 0 and episode_id in self.episode_id_to_ref:
            return self.episode_id_to_ref[episode_id], False
        if synthetic_key and synthetic_key in self.synthetic_item_key_to_ref:
            return self.synthetic_item_key_to_ref[synthetic_key], False
        ref = self._next_ref('BE', '_item_seq')
        if episode_id > 0:
            self.episode_id_to_ref[episode_id] = ref
        if synthetic_key:
            self.synthetic_item_key_to_ref[synthetic_key] = ref
        return ref, True

    def allocate_provenance_ref(self) -> str:
        return self._next_ref('PV', '_provenance_seq')

    def _next_ref(self, prefix: str, seq_attr: str) -> str:
        seq = getattr(self, seq_attr) + 1
        setattr(self, seq_attr, seq)
        ref = f'{prefix}{seq}'
        self.ensure_new_ref_not_visible(ref)
        self.visible_refs.add(ref)
        return ref


def _max_prefix_seq(refs: set[str], prefix: str) -> int:
    max_seq = 0
    for ref in refs:
        if ref.startswith(prefix):
            suffix = ref[len(prefix):]
            if suffix.isdigit():
                max_seq = max(max_seq, int(suffix))
    return max_seq
