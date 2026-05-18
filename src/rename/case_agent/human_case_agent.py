from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import (
    AssignmentIntent,
    BangumiGroupCard,
    BangumiItemCard,
    BangumiRelationCard,
    BangumiSubjectCard,
    CaseJudgeOutput,
    CaseVerifierResult,
    EvidenceBatchResult,
    FailClosedReason,
    Finding,
    ProvenanceCard,
    VerifierIssue,
)
from .workspace import CaseEvidenceWorkspace


LOCAL_MARKER_RE = re.compile(
    r"(?i)(?:^|[\[\]\s._-])"
    r"(?P<marker>ncop\d{0,2}[a-z]?|nced\d{0,2}[a-z]?|op\d{0,2}[a-z]?|ed\d{0,2}[a-z]?|"
    r"preview\d{0,3}(?:_\d+)?|spot\d{0,3}|pv\d{0,3}|cm\d{0,3}|menu\d{0,3}|"
    r"iv\d{1,3}|"
    r"sp\d{0,3}(?:_\d+)?|ova\d{0,3}|oad\d{0,3}|oav\d{0,3}|"
    r"special\d{0,3}|trailer\d{0,3})"
    r"(?:$|[\[\]\s._-])"
)
EPISODE_TOKEN_RE = re.compile(
    r"(?i)(?:#\s*(\d{1,3})|\[(\d{1,3})(?:[^\d\]]|])|(?:^|[\s._-])(\d{1,3})(?:$|[\s._-]))"
)
TECH_TOKEN_RE = re.compile(
    r"(?i)\b(?:bd(?:rip)?|bdrip|blu[- ]?ray|hevc|avc|x26[45]|"
    r"h\.?26[45]|flac|aac|ma10p|hi10p|1080p|720p|mkv|mp4|"
    r"vcb-studio|ai-raws|caso)\b"
)
QUERY_NOISE_TOKEN_RE = re.compile(
    r"(?i)\b(?:vcb\s*-?\s*studio|ai\s*-?\s*raws?|ktxp|caso|moozzi2|snow\s*-?\s*raws?|"
    r"bikko|ank\s*-?\s*raws?|subsplease|animef|adweb|frds|cxcy|"
    r"bd(?:rip)?|blu\s*-?\s*ray|nf|web(?:\s*-?\s*dl|rip)?|dl|hevc|avc|x26[45]|h\.?26[45]|"
    r"flac|aac|ac3|ddp|dts|hdma|ma10p|hi10p|yuv420p10|1080p|720p|mkv|mp4|"
    r"fin|hash|proper|repack)\b"
)
ROMAJI_TO_HIRAGANA = {
    "kya": "きゃ", "kyu": "きゅ", "kyo": "きょ",
    "sha": "しゃ", "shu": "しゅ", "sho": "しょ",
    "cha": "ちゃ", "chu": "ちゅ", "cho": "ちょ",
    "nya": "にゃ", "nyu": "にゅ", "nyo": "にょ",
    "hya": "ひゃ", "hyu": "ひゅ", "hyo": "ひょ",
    "mya": "みゃ", "myu": "みゅ", "myo": "みょ",
    "rya": "りゃ", "ryu": "りゅ", "ryo": "りょ",
    "gya": "ぎゃ", "gyu": "ぎゅ", "gyo": "ぎょ",
    "ja": "じゃ", "ju": "じゅ", "jo": "じょ",
    "bya": "びゃ", "byu": "びゅ", "byo": "びょ",
    "pya": "ぴゃ", "pyu": "ぴゅ", "pyo": "ぴょ",
    "shi": "し", "chi": "ち", "tsu": "つ", "fu": "ふ", "ji": "じ",
    "ka": "か", "ki": "き", "ku": "く", "ke": "け", "ko": "こ",
    "sa": "さ", "si": "し", "su": "す", "se": "せ", "so": "そ",
    "ta": "た", "ti": "ち", "tu": "つ", "te": "て", "to": "と",
    "na": "な", "ni": "に", "nu": "ぬ", "ne": "ね", "no": "の",
    "ha": "は", "hi": "ひ", "hu": "ふ", "he": "へ", "ho": "ほ",
    "ma": "ま", "mi": "み", "mu": "む", "me": "め", "mo": "も",
    "ya": "や", "yu": "ゆ", "yo": "よ",
    "ra": "ら", "ri": "り", "ru": "る", "re": "れ", "ro": "ろ",
    "wa": "わ", "wo": "を",
    "ga": "が", "gi": "ぎ", "gu": "ぐ", "ge": "げ", "go": "ご",
    "za": "ざ", "zi": "じ", "zu": "ず", "ze": "ぜ", "zo": "ぞ",
    "da": "だ", "di": "ぢ", "du": "づ", "de": "で", "do": "ど",
    "ba": "ば", "bi": "び", "bu": "ぶ", "be": "べ", "bo": "ぼ",
    "pa": "ぱ", "pi": "ぴ", "pu": "ぷ", "pe": "ぺ", "po": "ぽ",
    "a": "あ", "i": "い", "u": "う", "e": "え", "o": "お",
}
ROMAJI_KEYS = sorted(ROMAJI_TO_HIRAGANA, key=len, reverse=True)
REGULAR_EPISODE_KINDS = {"", "regular", "episode", "0"}


HumanToolName = Literal["inspect", "search", "note", "submit"]
REPAIR_FINALIZATION_TURN_WINDOW = 4
SEARCH_TOOL_CALL_BUDGET = 3
SEARCH_RESULTS_PER_VARIANT = 8
SEMANTIC_SUBMIT_DIAGNOSTIC_CODES = {
    "duplicate_like_singleton_exclusion_title_mismatch",
    "excluded_count_matched_uninspected_subject",
    "excluded_main_locator_with_mapped_title_sibling",
    "excluded_singleton_visible_subject_candidate",
    "excluded_singleton_with_unassigned_visible_target_items",
    "fail_closed_count_matched_target_sibling",
    "fail_closed_mapped_sibling",
    "fail_closed_negative_target_absence_outcome_inconsistent",
    "fail_closed_singleton_with_unassigned_visible_target_items",
    "mapped_packaging_extra_marker_without_specific_target",
    "mapped_target_title_bridge_missing",
    "numbered_special_exclusion_needs_target_evidence",
    "singleton_target_alias_matches_excluded_local_better",
    "supplemental_main_episodes_without_concrete_extra_reason",
}


class InspectToolArgs(BaseModel):
    locators: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    reason: str = ""

    model_config = ConfigDict(extra="forbid")


class SearchToolArgs(BaseModel):
    queries: list[str] = Field(default_factory=list)
    reason: str = ""

    model_config = ConfigDict(extra="forbid")


class AttentionFocus(BaseModel):
    summary: str = ""
    locators: list[str] = Field(default_factory=list)
    next_action: str = ""

    model_config = ConfigDict(extra="forbid")


class WorkUnitFocus(BaseModel):
    work_unit_id: str = ""
    label: str = ""
    status: Literal["open", "investigating", "ready", "blocked", "closed"] = "open"
    local: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    support: list[str] = Field(default_factory=list)
    hypothesis: str = ""
    evidence_summary: str = ""
    evidence_locators: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    blocking_issue: str = ""
    required_next_action: str = ""
    closure_condition: str = ""

    model_config = ConfigDict(extra="forbid")


class InvestigationAgendaItem(BaseModel):
    agenda_id: str = ""
    question: str = ""
    status: Literal["open", "closed", "blocked"] = "open"
    locators: list[str] = Field(default_factory=list)
    next_action: str = ""
    blocking_issue: str = ""
    closure_condition: str = ""
    closed_reason: str = ""

    model_config = ConfigDict(extra="forbid")


class RejectedCandidate(BaseModel):
    locator: str = ""
    title: str = ""
    reason: str = ""
    source_query: str = ""
    work_unit_id: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")


class ReadinessGap(BaseModel):
    issue_code: str = ""
    count: int = 0
    unit: str = ""
    local: list[str] = Field(default_factory=list)
    target: str = ""
    detail: str = ""

    model_config = ConfigDict(extra="forbid")


class ResolutionReadiness(BaseModel):
    status: Literal["not_ready", "partial", "ready", "blocked"] = "not_ready"
    ready_work_units: list[str] = Field(default_factory=list)
    blocking_work_units: list[str] = Field(default_factory=list)
    mechanical_gaps: list[ReadinessGap] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    repeated_rejection: bool = False
    summary: str = ""

    model_config = ConfigDict(extra="forbid")


class CaseCognitiveWorkspace(BaseModel):
    primary_hypotheses: list[str] = Field(default_factory=list)
    active_work_units: list[WorkUnitFocus] = Field(default_factory=list)
    attention_focus: AttentionFocus = Field(default_factory=AttentionFocus)
    investigation_agenda: list[InvestigationAgendaItem] = Field(default_factory=list)
    rejected_or_noisy_candidates: list[RejectedCandidate] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    resolution_readiness: ResolutionReadiness = Field(default_factory=ResolutionReadiness)

    model_config = ConfigDict(extra="forbid")


class NoteToolArgs(BaseModel):
    claims: list[str] = Field(default_factory=list)
    locators: list[str] = Field(default_factory=list)
    reason: str = ""
    cognitive_workspace: CaseCognitiveWorkspace | None = None

    model_config = ConfigDict(extra="forbid")


class ResolutionWorkUnit(BaseModel):
    unit_label: str = ""
    local: list[str] = Field(default_factory=list)
    outcome: Literal[
        "mapped_regular_span",
        "mapped_explicit_item",
        "mapped_special_or_ova",
        "mapped_composite_feature",
        "bangumi_target_absent",
        "supplemental",
        "non_bangumi",
        "fail_closed",
    ] = "fail_closed"
    target: str = ""
    episode_start: int | None = None
    episode_end: int | None = None
    episode_label: str = ""
    support: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    reason: str = ""
    open_questions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PackageResolution(BaseModel):
    work_units: list[ResolutionWorkUnit] = Field(default_factory=list)
    package_reason: str = ""

    model_config = ConfigDict(extra="forbid")


class SubmitToolArgs(BaseModel):
    resolution: PackageResolution = Field(default_factory=PackageResolution)
    reason: str = ""
    dry_run: bool = False

    model_config = ConfigDict(extra="forbid")


TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    "inspect": InspectToolArgs,
    "search": SearchToolArgs,
    "note": NoteToolArgs,
    "submit": SubmitToolArgs,
}


@dataclass(frozen=True)
class AgentLocator:
    locator: str
    kind: Literal["local", "target_subject", "target_episode", "target_span", "support"]
    title: str = ""
    contract_role: Literal["must_account", "support_only", "unknown_contract"] = "unknown_contract"
    file_refs: tuple[str, ...] = ()
    subject_ref: str = ""
    subject_id: int = 0
    subject_eps: int = 0
    item_refs: tuple[str, ...] = ()
    episode_start: int | None = None
    episode_end: int | None = None
    markers: tuple[str, ...] = ()
    query_markers: tuple[str, ...] = ()
    search_rank: int = 0
    source_role: str = ""
    relation_to_main: str = ""
    relation_path_refs: tuple[str, ...] = ()
    representative_labels: tuple[str, ...] = ()
    episode_file_refs: tuple[tuple[int, str], ...] = ()
    episode_file_labels: tuple[tuple[int, str, str], ...] = ()
    debug_refs: tuple[str, ...] = ()


@dataclass
class LocatorRegistry:
    locators: dict[str, AgentLocator] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    subject_locator_by_id: dict[int, str] = field(default_factory=dict)
    subject_ref_by_id: dict[int, str] = field(default_factory=dict)
    item_ref_by_subject_sort: dict[tuple[int, int], str] = field(default_factory=dict)

    def add(self, locator: AgentLocator) -> str:
        canonical = locator.locator
        self.locators[canonical] = locator
        self.aliases[canonical.casefold()] = canonical
        if locator.subject_id:
            if locator.kind == "target_subject":
                self.subject_locator_by_id[locator.subject_id] = canonical
                if locator.subject_ref:
                    self.subject_ref_by_id[locator.subject_id] = locator.subject_ref
            if locator.kind == "target_episode" and locator.episode_start is not None:
                key = (locator.subject_id, int(locator.episode_start))
                if locator.item_refs:
                    self.item_ref_by_subject_sort.setdefault(key, locator.item_refs[0])
        return canonical

    def add_alias(self, alias: str, canonical: str) -> None:
        alias = str(alias or "").strip()
        canonical = str(canonical or "").strip()
        if not alias or not canonical or canonical not in self.locators:
            return
        self.aliases[alias.casefold()] = canonical

    def resolve(self, locator: str) -> tuple[AgentLocator | None, dict[str, object] | None]:
        raw = str(locator or "").strip()
        if not raw:
            return None, {"issue": "locator_not_found", "locator": raw}
        canonical = self.aliases.get(raw.casefold())
        if canonical:
            return self.locators.get(canonical), None
        parsed = self._parse_target_locator(raw)
        if parsed is not None:
            return parsed, None
        parsed_local, local_issue = self._parse_local_locator(raw)
        if parsed_local is not None or local_issue is not None:
            return parsed_local, local_issue
        matches = [
            value
            for key, value in self.locators.items()
            if raw.casefold() in key.casefold() or raw.casefold() in value.title.casefold()
        ]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, {
                "issue": "locator_ambiguous",
                "locator": raw,
                "matches": [item.locator for item in matches[:8]],
            }
        return None, {"issue": "locator_not_found", "locator": raw}

    def _parse_target_locator(self, raw: str) -> AgentLocator | None:
        match = re.match(
            r"^target://bangumi/(?P<sid>\d+)(?:-[^/]+)?(?:/(?P<kind>episode|episodes)/(?P<start>\d+)(?:-(?P<end>\d+))?)?$",
            raw,
        )
        if not match:
            return None
        subject_id = int(match.group("sid"))
        subject_locator = self.subject_locator_by_id.get(subject_id)
        subject_ref = self.subject_ref_by_id.get(subject_id, "")
        subject_card = self.locators.get(subject_locator or "")
        subject_eps = int(getattr(subject_card, "subject_eps", 0) or 0)
        if not subject_locator:
            return None
        kind = match.group("kind") or ""
        if not kind:
            return self.locators.get(subject_locator)
        start = int(match.group("start") or 0)
        end = int(match.group("end") or start)
        item_refs = [
            ref
            for sort in range(start, end + 1)
            for ref in [self.item_ref_by_subject_sort.get((subject_id, sort), "")]
            if ref
        ]
        locator_kind: Literal["target_episode", "target_span"] = (
            "target_episode" if kind == "episode" and start == end else "target_span"
        )
        return AgentLocator(
            locator=raw,
            kind=locator_kind,
            title=str(getattr(subject_card, "title", "") or raw),
            subject_ref=subject_ref,
            subject_id=subject_id,
            subject_eps=subject_eps,
            item_refs=tuple(item_refs),
            episode_start=start,
            episode_end=end,
            contract_role="support_only",
            markers=tuple(getattr(subject_card, "markers", ()) or ()),
            query_markers=tuple(getattr(subject_card, "query_markers", ()) or ()),
            search_rank=int(getattr(subject_card, "search_rank", 0) or 0),
            debug_refs=tuple([subject_ref, *item_refs]),
        )

    def _parse_local_locator(self, raw: str) -> tuple[AgentLocator | None, dict[str, object] | None]:
        match = re.match(
            r"^(?P<base>local://.+?)/(?P<kind>episode|episodes)/(?P<start>\d+)(?:-(?P<end>\d+))?$",
            raw,
        )
        if not match:
            return None, None
        base_raw = match.group("base")
        base = self.aliases.get(base_raw.casefold(), base_raw)
        base_locator = self.locators.get(base)
        if base_locator is None:
            return None, {"issue": "locator_not_found", "locator": raw, "base_locator": base_raw}
        if base_locator.kind != "local":
            return None, {
                "issue": "locator_scope_not_available",
                "locator": raw,
                "expected": "local:// episode-capable locator",
            }
        episode_pairs = list(base_locator.episode_file_refs)
        if not episode_pairs:
            return None, {
                "issue": "local_episode_surface_missing",
                "locator": raw,
                "base_locator": base,
                "available_action": f'inspect(["{base}"], scope=["files","coverage"])',
            }
        start = int(match.group("start") or 0)
        end = int(match.group("end") or start)
        if end < start:
            start, end = end, start
        available_numbers = {num for num, _ref in episode_pairs}
        missing_numbers = [num for num in range(start, end + 1) if num not in available_numbers]
        if missing_numbers:
            return None, {
                "issue": "local_episode_range_missing",
                "locator": raw,
                "base_locator": base,
                "missing_episode_numbers": missing_numbers[:16],
                "available_episode_numbers": sorted(available_numbers)[:48],
            }
        selected_refs = [ref for num, ref in episode_pairs if start <= num <= end]
        label_by_ref = {ref: label for _num, ref, label in _episode_label_triples(base_locator)}
        selected_labels = [label_by_ref.get(ref, ref) for ref in selected_refs]
        locator_kind: Literal["local"] = "local"
        return AgentLocator(
            locator=raw,
            kind=locator_kind,
            title=f"{base_locator.title} episodes {start}-{end}",
            contract_role=base_locator.contract_role,
            file_refs=tuple(selected_refs),
            episode_start=start,
            episode_end=end,
            markers=base_locator.markers,
            representative_labels=tuple(_representative_labels(selected_labels)),
            episode_file_refs=tuple((num, ref) for num, ref in episode_pairs if start <= num <= end),
            episode_file_labels=tuple((num, ref, label_by_ref.get(ref, ref)) for num, ref in episode_pairs if start <= num <= end),
            debug_refs=tuple(selected_refs),
        ), None


@dataclass
class HumanCaseSession:
    case_id: str
    turn_count: int = 0
    tool_sequence: list[str] = field(default_factory=list)
    tool_rejection_count: int = 0
    submit_rejection_count: int = 0
    submit_rejection_issue_counts: dict[str, int] = field(default_factory=dict)
    history_items: list[dict[str, object]] = field(default_factory=list)
    notes: list[dict[str, object]] = field(default_factory=list)
    cognitive_workspace: CaseCognitiveWorkspace = field(default_factory=CaseCognitiveWorkspace)
    observations: list[dict[str, object]] = field(default_factory=list)
    response_id: str = ""
    http_session_id: str = ""
    prompt_cache_key: str = "bar:lbg:human-case-agent:v1"
    first_turn_estimated_tokens: int = 0
    stable_prefix_estimated_tokens: int = 0
    turn_tail_estimated_tokens: int = 0
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0
    cached_input_tokens: int = 0
    last_tail_bytes: bytes = b""
    last_tail_sha256: str = ""
    last_tool_name: str = ""
    current_consecutive_tool_count: int = 0
    max_consecutive_tool_count: int = 0
    single_tool_loop_suspected_count: int = 0
    last_submit_dry_run_accepted: bool = False
    last_submit_rejection_fingerprint: str = ""
    repeated_submit_rejection_count: int = 0
    draft_work_units: list[dict[str, object]] = field(default_factory=list)
    draft_revision_count: int = 0
    max_turns: int = 0
    search_call_count: int = 0
    search_new_subject_count: int = 0
    search_existing_only_count: int = 0
    search_no_result_count: int = 0
    last_search_progress: str = ""
    searched_query_variant_keys: set[str] = field(default_factory=set)
    noise_candidate_count: int = 0
    attention_focus_change_count: int = 0
    agenda_closed_count: int = 0
    stall_warning_count: int = 0
    no_progress_turn_count: int = 0
    last_turn_health: dict[str, object] = field(default_factory=dict)


@dataclass
class HumanToolCall:
    tool_name: str
    arguments: BaseModel
    raw_arguments: dict[str, Any]
    call_id: str = ""
    response_id: str = ""


@dataclass
class SubmitCompileResult:
    accepted: bool
    output: CaseJudgeOutput | None
    verifier: CaseVerifierResult
    feedback: dict[str, object]
    mapped_file_count: int = 0
    excluded_file_count: int = 0


def _strict_schema_for_model(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()

    def visit(node: object) -> None:
        if not isinstance(node, dict):
            return
        node.pop("title", None)
        node.pop("description", None)
        if node.get("type") == "object":
            props = node.get("properties")
            if isinstance(props, dict):
                node["required"] = list(props.keys())
                for child in props.values():
                    visit(child)
            node["additionalProperties"] = False
        for key in ("$defs", "items", "anyOf", "oneOf", "allOf"):
            value = node.get(key)
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

    visit(schema)
    return schema


def human_case_tool_definitions() -> list[dict[str, object]]:
    descriptions = {
        "inspect": (
            "Expand local:// or target:// locators. Use scope such as files, samples, "
            "details, episodes, related, aliases, surface, or coverage. Batch multiple locators in one call."
        ),
        "search": "Search Bangumi subjects from your own clean title queries. Batch all needed queries in one call.",
        "note": (
            "Update the cognitive workspace: hypotheses, active work units, attention focus, agenda, "
            "rejected/noisy candidates, evidence gaps, and readiness. Use visible locators only."
        ),
        "submit": (
            "Submit package work-unit decisions. The first submit should try to cover every must_account "
            "local locator exactly once. After a rejection, mechanically-ok work units are saved by the "
            "fixed layer, so you may submit only changed, blocked, or missing work units; saved units are "
            "merged and the full package is re-verified. "
            "Search results alone are not enough for mapped episode ranges: inspect target subjects with "
            "episodes/details/related before submit. Prefer dry_run=false for final answers. "
            "The fixed layer performs only schema, locator, coverage, duplicate-target, and accounting checks."
        ),
    }
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": descriptions[name],
                "parameters": _strict_schema_for_model(model),
            },
        }
        for name, model in TOOL_ARG_MODELS.items()
    ]


def _estimate_tokens(value: object) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return max(1, len(str(text).encode("utf-8")) // 4)


def _stable_json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_text(_stable_json_text(value))


def _common_prefix_byte_count(left: bytes, right: bytes) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _slug(value: str, *, fallback: str = "item") -> str:
    text = str(value or "").strip().casefold()
    text = text.replace("×", " x ").replace("☆", " star ").replace("★", " star ")
    text = re.sub(r"\[[^\]]*\]|\([^\)]*\)|【[^】]*】", " ", text)
    text = TECH_TOKEN_RE.sub(" ", text)
    text = re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "-", text, flags=re.I)
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        text = fallback
    return text[:80].strip("-") or fallback


def _basename(path: str) -> str:
    return str(path or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _parent(path: str) -> str:
    text = str(path or "").replace("\\", "/")
    if "/" not in text:
        return "package"
    return text.rsplit("/", 1)[0] or "package"


def _top_dir(path: str) -> str:
    text = str(path or "").replace("\\", "/")
    return text.split("/", 1)[0] if "/" in text else "package"


def _episode_numbers(text: str) -> list[int]:
    raw_text = str(text or "")
    cleaned = re.sub(r"\[[0-9A-Fa-f]{8,}\]", " ", raw_text)
    cleaned = re.sub(r"\b\d{3,4}\s*x\s*\d{3,4}\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d{3,4}p\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(?:x26[45]|h\.?26[45]|hevc|avc|flac|aac|ma10p|bd(?:rip)?|blu[- ]?ray)\b", " ", cleaned, flags=re.I)

    def _collect_from(source: str, pattern: str) -> list[int]:
        values: list[int] = []
        for match in re.finditer(pattern, source, flags=re.I):
            raw = next((group for group in match.groups() if group), "")
            if not raw:
                continue
            try:
                value = int(raw)
            except ValueError:
                continue
            if 0 <= value <= 200:
                values.append(value)
        return values

    def _collect(pattern: str) -> list[int]:
        return _collect_from(cleaned, pattern)

    marker_numbers = _collect(
        r"(?:^|[\[\]\s._-])(?:sp|special|ova|oad|oav)\s*0*(\d{1,3})(?:$|[\]\s._-])"
    )
    if marker_numbers:
        return marker_numbers
    suffix_marker_numbers = _collect(
        r"(?:^|[\[\]\s._-])0*(\d{1,3})\s*(?:sp|special|ova|oad|oav)(?:$|[\]\s._-])"
    )
    if suffix_marker_numbers:
        return suffix_marker_numbers

    explicit = _collect(r"(?:#|第)\s*(\d{1,3})(?:\s*(?:話|话|集|$|[^\d]))")
    if explicit:
        return explicit

    season_episode_numbers = _collect(r"(?:^|[\s._-])s\d{1,2}e0*(\d{1,3})(?:$|[\s._-])")
    if season_episode_numbers:
        return season_episode_numbers

    bracket_numbers = _collect_from(cleaned, r"\[(\d{1,3})(?:[^\d\]]|])")
    if bracket_numbers:
        return bracket_numbers

    titled_bracket_numbers: list[int] = []
    for match in re.finditer(r"(?:^|[\s._-])0*(\d{1,3})\s*\[([^\]]+)\]", cleaned, flags=re.I):
        content = re.sub(r"\s+", " ", match.group(2) or "").strip()
        if re.fullmatch(
            r"(?i)(?:sp\d{0,3}(?:_\d+)?|ova\d{0,3}|oad\d{0,3}|oav\d{0,3}|special\d{0,3}|"
            r"iv\d{1,3}|ncop\d{0,2}[a-z]?|nced\d{0,2}[a-z]?|op\d{0,2}[a-z]?|ed\d{0,2}[a-z]?|"
            r"preview\d{0,3}(?:_\d+)?|spot\d{0,3}|pv\d{0,3}|cm\d{0,3}|menu\d{0,3}|trailer\d{0,3})",
            content,
        ):
            continue
        if re.fullmatch(r"(?i)(?:\d{3,4}p|x26[45]|h\.?26[45]|flac|aac|ma10p|hi10p|bd(?:rip)?)", content):
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if 0 <= value <= 200:
            titled_bracket_numbers.append(value)
    if titled_bracket_numbers:
        return titled_bracket_numbers

    markers = [marker.casefold() for marker in _markers(raw_text)]
    no_fallback_markers = (
        "sp", "special", "ova", "oad", "oav", "iv", "ncop", "nced",
        "op", "ed", "preview", "spot", "pv", "cm", "menu", "trailer",
    )
    if any(marker.startswith(prefix) for marker in markers for prefix in no_fallback_markers):
        return []

    fallback_text = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", cleaned)
    fallback_text = re.sub(r"\.[^.]+$", " ", fallback_text)
    fallback_text = re.sub(r"\s+", " ", fallback_text).strip()
    titled_trailing = _collect_from(fallback_text, r"(?:^|[\s._-])0*(\d{1,3})\s+(?:end|fin|final)(?:\s*$)")
    if titled_trailing:
        return titled_trailing
    trailing = _collect_from(fallback_text, r"(?:^|[\s._-])0*(\d{1,3})(?:\s*$)")
    if trailing:
        return trailing

    return []


def _markers(text: str) -> list[str]:
    return list(dict.fromkeys(match.group("marker").upper() for match in LOCAL_MARKER_RE.finditer(str(text or ""))))


def _series_key(path: str) -> str:
    name = _basename(path)
    episode_like = bool(_episode_numbers(name))
    fallback_name = re.sub(r"\[[^\]]+\]", " ", name)
    fallback_name = re.sub(r"\.[^.]+$", " ", fallback_name)
    fallback_name = re.sub(r"#\s*\d{1,3}", " ", fallback_name)
    fallback_name = re.sub(r"(?i)(?:^|[\s._-])s\d{1,2}e\d{1,3}(?:$|[\s._-])", " ", fallback_name)
    if episode_like:
        fallback_name = re.sub(
            r"(?i)(?:^|[\s._-])0*\d{1,3}\s+(?:end|fin|final)(?=\s*(?:$|[\[(._-]))",
            " ",
            fallback_name,
        )
    fallback_name = re.sub(
        r"(?i)(?:^|[\s._-])(?:\d{1,3}|sp\d{0,3}(?:_\d+)?|ova\d{0,3}|oad\d{0,3}|oav\d{0,3}|"
        r"preview\d{0,3}(?:_\d+)?|spot\d{0,3}|ncop\d{0,2}[a-z]?|nced\d{0,2}[a-z]?|"
        r"op\d{0,2}[a-z]?|ed\d{0,2}[a-z]?|iv\d{1,3}|cm\d{0,3}|menu\d{0,3})"
        r"(?:$|[\s._-])",
        " ",
        fallback_name,
    )
    fallback_name = TECH_TOKEN_RE.sub(" ", fallback_name)
    fallback_name = re.sub(r"\s+", " ", fallback_name).strip(" -_")
    if fallback_name and re.search(r"[0-9a-z\u3040-\u30ff\u3400-\u9fff]", fallback_name, flags=re.I):
        return fallback_name[:100]
    bracket_tokens = []
    for token in re.findall(r"\[([^\]]+)\]", name):
        if re.search(r"(?i)\b(?:raws?|studio|caso|ktxp|moozzi2|bikko|snow)\b", str(token or "")):
            continue
        clean = str(token or "").replace("_", " ")
        clean = TECH_TOKEN_RE.sub(" ", clean)
        clean = re.sub(r"\b[0-9A-Fa-f]{8}\b", " ", clean)
        clean = re.sub(r"(?i)^(?:\d{1,3}|sp\d{0,3}|preview\d{0,3}(?:_\d+)?|ncop\d{0,2}|nced\d{0,2}|cm\d{0,3}|menu\d{0,3})$", " ", clean)
        clean = re.sub(r"(?i)\b(?:raws?|studio|caso|ktxp|moozzi2|bikko|snow)\b", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip(" -_")
        if clean:
            bracket_tokens.append(clean)
    if bracket_tokens:
        return " ".join(bracket_tokens[:2])[:100]
    name = re.sub(r"\[[0-9A-Fa-f]{8}\]", " ", name)
    name = re.sub(r"\[[^\]]+\]", " ", name)
    name = re.sub(r"\.[^.]+$", " ", name)
    name = re.sub(r"#\s*\d{1,3}", " ", name)
    name = re.sub(r"\[(?:\d{1,3}|SP\d{0,3}|Preview\d{0,3}(?:_\d+)?|NCOP\d{0,2}|NCED\d{0,2}|CM\d{0,3}|Menu\d{0,3})[^\]]*\]", " ", name, flags=re.I)
    name = TECH_TOKEN_RE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:100] or _parent(path)


def _display_title(value: str) -> str:
    text = str(value or "").replace("\\", " / ").replace("/", " ")
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\([^\)]*\)", " ", text)
    text = TECH_TOKEN_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    return text or str(value or "").strip()


def _has_explicit_special_marker(basename: str, markers: list[str]) -> bool:
    folded_markers = [marker.casefold() for marker in markers]
    if any(
        re.fullmatch(r"sp\d{0,3}(?:_\d+)?", marker)
        or marker.startswith(("ova", "oad", "oav"))
        for marker in folded_markers
    ):
        return True
    if not any(marker.startswith("special") for marker in folded_markers):
        return False
    for content in re.findall(r"\[([^\]]+)\]", str(basename or "")):
        if re.fullmatch(r"(?i)\s*special\d{0,3}\s*", content):
            return True
    outside_brackets = re.sub(r"\[[^\]]+\]", " ", str(basename or ""))
    return bool(re.search(r"(?i)(?:^|[\s._-])special\d{0,3}(?:$|[\s._-])", outside_brackets))


def _group_category(path: str) -> str:
    basename = _basename(path)
    markers = _markers(basename)
    marker_text = " ".join(markers).casefold()
    episode_numbers = _episode_numbers(basename)
    if "preview" in marker_text:
        return "previews"
    if _has_explicit_special_marker(basename, markers):
        return "special-marker"
    if "special" in marker_text and not episode_numbers:
        return "special-marker"
    if any(prefix in marker_text for prefix in ("ncop", "nced", " op", " ed", "spot", "pv", "cm", "menu", "trailer", "iv")):
        return "packaging-extras"
    return "main-episodes" if episode_numbers else "main"


def _representative_labels(paths: list[str], *, limit: int = 6) -> list[str]:
    labels = [_basename(path) for path in paths]
    if len(labels) <= limit:
        return labels
    edge = max(1, limit // 2)
    return [*labels[:edge], *labels[-edge:]]


def _range_summary(paths: list[str]) -> dict[str, object]:
    nums: list[int] = []
    for path in paths:
        values = _episode_numbers(_basename(path))
        if values:
            nums.append(values[-1])
    if not nums:
        return {"episode_like": False}
    unique = sorted(set(nums))
    return {
        "episode_like": True,
        "start": min(unique),
        "end": max(unique),
        "count": len(unique),
        "gap_count": max(0, (max(unique) - min(unique) + 1) - len(unique)),
        "duplicates": len(nums) - len(unique),
    }


def _range_summary_from_episode_pairs(pairs: tuple[tuple[int, str], ...]) -> dict[str, object]:
    nums = [int(num) for num, _ref in pairs]
    if not nums:
        return {"episode_like": False}
    unique = sorted(set(nums))
    return {
        "episode_like": True,
        "start": min(unique),
        "end": max(unique),
        "count": len(unique),
        "gap_count": max(0, (max(unique) - min(unique) + 1) - len(unique)),
        "duplicates": len(nums) - len(unique),
    }


def _episode_label_triples(locator: AgentLocator) -> list[tuple[int, str, str]]:
    return [(int(num), str(ref), str(label)) for num, ref, label in locator.episode_file_labels]


def _episode_pairs_for_group(refs: list[str], local_by_ref: dict[str, object]) -> tuple[tuple[tuple[int, str], ...], tuple[tuple[int, str, str], ...]]:
    ref_pairs: list[tuple[int, str]] = []
    label_pairs: list[tuple[int, str, str]] = []
    for ref in refs:
        card = local_by_ref.get(ref)
        path = str(getattr(card, "path", "") or "")
        nums = _episode_numbers(_basename(path))
        if not nums:
            continue
        num = int(nums[-1])
        ref_pairs.append((num, ref))
        label_pairs.append((num, ref, _basename(path)))
    return tuple(ref_pairs), tuple(label_pairs)


def _episode_locator_hints(locator: str, episode_pairs: tuple[tuple[int, str], ...]) -> dict[str, object]:
    numbers = sorted({int(num) for num, _ref in episode_pairs})
    if not numbers:
        return {}
    episode_counts = Counter(int(num) for num, _ref in episode_pairs)
    hints: dict[str, object] = {
        "episode_locator_syntax": {
            "single": f"{locator}/episode/{numbers[0]}",
            "range": f"{locator}/episodes/{numbers[0]}-{numbers[-1]}",
        },
        "available_episode_numbers": numbers[:64],
        "episode_locators": [
            {
                "locator": f"{locator}/episode/{num}",
                "episode_number": num,
                "file_count": episode_counts.get(num, 0),
            }
            for num in numbers[:16]
        ],
    }
    if 0 in numbers and any(num > 0 for num in numbers):
        positive = [num for num in numbers if num > 0]
        hints["common_split_examples"] = [
            {
                "locator": f"{locator}/episode/0",
                "episode_numbers": [0],
                "file_count": sum(1 for num, _ref in episode_pairs if int(num) == 0),
            },
            {
                "locator": f"{locator}/episodes/{min(positive)}-{max(positive)}",
                "episode_numbers": [min(positive), max(positive)],
                "file_count": sum(1 for num, _ref in episode_pairs if min(positive) <= int(num) <= max(positive)),
            },
        ]
    return hints


def _local_locator_needs_short_alias(locator: str) -> bool:
    return len(locator) > 72 or len(locator.encode("utf-8")) > 120


def _short_local_locator(index: int, category: str, *, episode_capable: bool) -> str:
    suffix = "episodes" if episode_capable else str(category or "files")
    if suffix in {"main", "main-episodes"}:
        suffix = "main-episodes"
    return f"local://u{index:02d}/{_slug(suffix, fallback='files')}"


def _work_unit_query_base(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)\b(?:main[-\s]?episodes|special[-\s]?marker|packaging[-\s]?extras|previews|main)\b\s*$",
        " ",
        text,
    )
    text = re.sub(r"(?i)\b(?:episodes?|files?)\b\s*$", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    return text


def _useful_query_hint(value: str) -> bool:
    folded = str(value or "").casefold().strip()
    if not folded:
        return False
    if folded in {"main", "main episodes", "special", "special marker", "sp", "sps", "oad", "ova", "packaging extras"}:
        return False
    return bool(re.search(r"[a-z\u3040-\u30ff\u3400-\u9fff]", folded, flags=re.I))


def _work_unit_query_hints(title: str, representative_labels: list[str], *, limit: int = 4) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = _work_unit_query_base(value)
        variants = _search_query_variants(value) or [value]
        for raw_variant in variants:
            variant = _work_unit_query_base(raw_variant)
            if not _useful_query_hint(variant):
                continue
            folded = variant.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            hints.append(variant)
            if len(hints) >= limit:
                return

    for label in representative_labels[:2]:
        add(label)
        if len(hints) >= limit:
            break
    add(title)
    for label in representative_labels[2:4]:
        add(label)
        if len(hints) >= limit:
            break
    return hints[:limit]


def _recommended_search_queries(local_locators: list[dict[str, object]], title_cues: list[str], *, limit: int = 24) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = _work_unit_query_base(value)
        if not _useful_query_hint(value):
            return
        folded = value.casefold()
        if folded in seen:
            return
        seen.add(folded)
        queries.append(value)

    def row_priority(row: dict[str, object]) -> tuple[int, str]:
        locator = str(row.get("locator") or "")
        if locator.endswith("/main-episodes"):
            priority = 0
        elif locator.endswith("/special-marker"):
            priority = 1
        else:
            priority = 2
        return priority, locator

    for row in sorted(local_locators, key=row_priority):
        locator = str(row.get("locator") or "")
        file_count = int(row.get("file_count") or 0)
        per_row_limit = 12 if file_count <= 3 else (3 if locator.endswith("/main") else 1)
        for hint in list(row.get("search_query_hints") or [])[:per_row_limit]:
            add(str(hint or ""))
    for cue in title_cues:
        variants = _search_query_variants(cue)
        add(variants[0] if variants else cue)
    return queries[:limit]


def build_human_case_desk(workspace: CaseEvidenceWorkspace) -> tuple[dict[str, object], LocatorRegistry]:
    registry = LocatorRegistry()
    local_by_ref = {card.ref: card for card in workspace.local_files if card.ref}
    main_refs = [ref for ref in list(workspace.contract.main_file_refs or []) if ref in local_by_ref]
    records: list[tuple[str, str, str, str]] = []
    series_by_parent_category: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ref in main_refs:
        card = local_by_ref[ref]
        path = str(card.path or "")
        category = _group_category(path)
        parent = _parent(path)
        file_series = _series_key(path)
        records.append((ref, parent, category, file_series))
        series_by_parent_category[(parent, category)].add(_slug(file_series, fallback="series"))

    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for ref, parent, category, file_series in records:
        split_parent_category = len(series_by_parent_category[(parent, category)]) > 1
        if parent == "package" or split_parent_category:
            series = file_series
        else:
            series = parent.rsplit("/", 1)[-1]
        if parent != "package" and category not in {"main-episodes", "main"} and not split_parent_category:
            series = category
        grouped[(parent, category, series)].append(ref)

    local_locators: list[dict[str, object]] = []
    used_locators: Counter[str] = Counter()
    group_entries: list[tuple[str, str, str, list[str]]] = []
    for parent, category, series in grouped:
        refs = grouped[(parent, category, series)]
        episode_file_refs, _episode_file_labels = _episode_pairs_for_group(refs, local_by_ref)
        if category == "main" and len(refs) > 1 and not episode_file_refs:
            for item_ref in refs:
                item_path = str(local_by_ref[item_ref].path or "")
                group_entries.append((parent, category, _series_key(item_path), [item_ref]))
            continue
        group_entries.append((parent, category, series, refs))

    for index, (parent, category, series, refs) in enumerate(group_entries, start=1):
        paths = [str(local_by_ref[ref].path or "") for ref in refs]
        root_name = series if parent == "package" or len(series_by_parent_category.get((parent, category), set())) > 1 else parent
        root_slug = _slug(root_name, fallback=f"group-{index}")
        suffix = category
        if parent == "package" and category in {"main-episodes", "main"}:
            series_slug = _slug(series, fallback="main")
            suffix = "main-episodes" if index == 1 or len(grouped) == 1 else f"{series_slug}-episodes"
        markers = list(dict.fromkeys(marker for path in paths for marker in _markers(_basename(path))))[:12]
        episode_file_refs, episode_file_labels = _episode_pairs_for_group(refs, local_by_ref)
        readable_locator = f"local://{root_slug}/{suffix}"
        alias_locators: list[str] = []
        locator = readable_locator
        if _local_locator_needs_short_alias(readable_locator):
            locator = _short_local_locator(index, category, episode_capable=bool(episode_file_refs))
            alias_locators.append(readable_locator)
        used_locators[locator] += 1
        if used_locators[locator] > 1:
            locator = f"{locator}-{used_locators[locator]}"
        entry = AgentLocator(
            locator=locator,
            kind="local",
            title=f"{_display_title(root_name)} {category}".strip(),
            contract_role="must_account",
            file_refs=tuple(refs),
            markers=tuple(markers),
            representative_labels=tuple(_representative_labels(paths)),
            episode_file_refs=episode_file_refs,
            episode_file_labels=episode_file_labels,
            debug_refs=tuple(refs),
        )
        registry.add(entry)
        for alias in alias_locators:
            registry.add_alias(alias, locator)
        local_entry = {
            "locator": locator,
            "alias_locators": alias_locators,
            "contract_role": "must_account",
            "title": entry.title,
            "file_count": len(refs),
            "episode_range": _range_summary(paths),
            "markers": markers,
            "representative_labels": list(entry.representative_labels),
            "search_query_hints": _work_unit_query_hints(entry.title, list(entry.representative_labels), limit=12),
        }
        local_entry.update(_episode_locator_hints(locator, episode_file_refs))
        local_locators.append(local_entry)

    filtered_audits = [
        audit
        for audit in list(workspace.judge_request_audits or [])
        if isinstance(audit, dict) and audit.get("note") == "deterministic_local_supplemental_projection"
    ]
    support_locators: list[dict[str, object]] = []
    if filtered_audits:
        audit = filtered_audits[-1]
        locator = "local://support-only/filtered-non-contract"
        samples = [
            str(item.get("path") or "")
            for item in list(audit.get("filtered_file_samples") or [])
            if isinstance(item, dict)
        ]
        registry.add(
            AgentLocator(
                locator=locator,
                kind="support",
                title="filtered support-only local material",
                contract_role="support_only",
                representative_labels=tuple(_representative_labels(samples)),
            )
        )
        support_locators.append(
            {
                "locator": locator,
                "contract_role": "support_only",
                "file_count": int(audit.get("filtered_file_count") or 0),
                "video_count": int(audit.get("filtered_video_count") or 0),
                "representative_labels": _representative_labels(samples),
            }
        )

    title_cues = _title_cues_for_desk(workspace)
    recommended_search_queries = _recommended_search_queries(local_locators, title_cues)
    desk = {
        "case_id": workspace.header.case_id,
        "package_summary": {
            "main_file_count": len(main_refs),
            "local_locator_count": len(local_locators),
            "support_only_locator_count": len(support_locators),
            "top_directories": sorted(set(_top_dir(str(local_by_ref[ref].path or "")) for ref in main_refs))[:16],
        },
        "resolution_contract": {
            "must_account_locator_count": len(local_locators),
            "support_only_locator_count": len(support_locators),
            "coverage_rule": "Every must_account local locator must appear exactly once in submit.work_units[].local.",
            "coverage_note": "Locators are selectable file surfaces and may overlap; the verifier checks exact-once file coverage, not semantic correctness.",
            "support_rule": "support_only locators may appear in support but do not require an outcome.",
        },
        "local_locators": local_locators,
        "support_only_locators": support_locators,
        "possible_title_cues": title_cues[:16],
        "recommended_search_queries": recommended_search_queries,
        "tool_guide": {
            "tools": ["inspect", "search", "note", "submit"],
            "submit_outcomes": [
                "mapped_regular_span",
                "mapped_explicit_item",
                "mapped_special_or_ova",
                "bangumi_target_absent",
                "supplemental",
                "non_bangumi",
                "fail_closed",
            ],
        },
    }
    return desk, registry


def _title_cues_for_desk(workspace: CaseEvidenceWorkspace) -> list[str]:
    values: list[str] = []
    for card in workspace.local_files:
        path = str(card.path or "")
        for part in path.replace("\\", "/").split("/")[:-1]:
            cleaned = re.sub(r"\[[^\]]+\]", " ", part)
            cleaned = TECH_TOKEN_RE.sub(" ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
            if cleaned:
                values.append(cleaned)
        key = _series_key(path)
        if key:
            values.append(key)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(value)
    return result


def _subject_title(subject: object) -> str:
    return (
        str(getattr(subject, "name_cn", "") or "")
        or str(getattr(subject, "name", "") or "")
        or str(getattr(subject, "title", "") or "")
    )


def _subject_locator(subject_id: int, title: str) -> str:
    return f"target://bangumi/{int(subject_id)}-{_slug(title, fallback='subject')}"


def _next_ref(prefix: str, existing: list[str]) -> str:
    nums = []
    for ref in existing:
        match = re.match(rf"^{re.escape(prefix)}(\d+)$", str(ref or ""))
        if match:
            nums.append(int(match.group(1)))
    return f"{prefix}{(max(nums) if nums else 0) + 1}"


INFOBOX_ALIAS_KEY_RE = re.compile(
    r"(?i)(?:alias|aka|alternate|alternative|title|name|别名|中文名|英文名|日文名|原名|原题|原題|又名|译名|譯名)"
)


def _compact_infobox_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -_:;")
    if not text or len(text) > 120:
        return ""
    if re.match(r"(?i)^https?://", text):
        return ""
    return text


def _infobox_text_values(value: object, *, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, dict):
        preferred_keys = ("v", "value", "name", "title")
        values: list[str] = []
        for key in preferred_keys:
            if key in value:
                values.extend(_infobox_text_values(value.get(key), depth=depth + 1))
        if values:
            return values
        for item in value.values():
            values.extend(_infobox_text_values(item, depth=depth + 1))
        return values
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_infobox_text_values(item, depth=depth + 1))
        return values
    text = _compact_infobox_text(value)
    return [text] if text else []


def _subject_infobox_facts(subject: object) -> list[str]:
    facts: list[str] = []
    seen: set[str] = set()
    for item in list(getattr(subject, "infobox", []) or [])[:32]:
        key = ""
        value: object = item
        if isinstance(item, dict):
            key = _compact_infobox_text(item.get("key") or item.get("name") or item.get("label") or "")
            value = item.get("value", "")
        values = []
        for raw_value in _infobox_text_values(value):
            folded = raw_value.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            values.append(raw_value)
            if len(values) >= 6:
                break
        if not values:
            continue
        fact = f"{key}: {' / '.join(values)}" if key else " / ".join(values)
        fact = _compact_infobox_text(fact)
        if not fact:
            continue
        folded_fact = fact.casefold()
        if folded_fact in seen:
            continue
        seen.add(folded_fact)
        facts.append(fact)
        if len(facts) >= 16:
            break
    return facts


def _infobox_alias_values_from_facts(facts: object) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        text = _compact_infobox_text(value)
        if not text:
            return
        folded = text.casefold()
        if folded in seen:
            return
        seen.add(folded)
        aliases.append(text)

    for fact in list(facts or [])[:24]:
        fact_text = str(fact or "")
        key_hint = fact_text.split(":", 1)[0] if ":" in fact_text else fact_text[:80]
        if not INFOBOX_ALIAS_KEY_RE.search(key_hint) and not INFOBOX_ALIAS_KEY_RE.search(fact_text[:120]):
            continue
        extracted = re.findall(r"""['"]v['"]\s*:\s*['"]([^'"]+)['"]""", fact_text)
        if not extracted:
            extracted = re.findall(r"""['"]value['"]\s*:\s*['"]([^'"]+)['"]""", fact_text)
        if not extracted:
            _key, sep, rest = fact_text.partition(":")
            extracted = [rest if sep else fact_text]
        for raw_value in extracted:
            for part in re.split(r"\s*/\s*|\s*\|\s*", str(raw_value or "")):
                add(part)
        if len(aliases) >= 16:
            break
    return aliases


def _subject_alias_markers(subject: BangumiSubjectCard) -> tuple[str, ...]:
    return _target_alias_markers(
        subject.title,
        subject.name,
        subject.name_cn,
        *_infobox_alias_values_from_facts(subject.infobox_facts),
    )


def _subject_card_from_api(subject: object, ref: str) -> BangumiSubjectCard:
    return BangumiSubjectCard(
        ref=ref,
        subject_id=int(getattr(subject, "id", 0) or 0),
        subject_type="anime",
        title=_subject_title(subject),
        name=str(getattr(subject, "name", "") or ""),
        name_cn=str(getattr(subject, "name_cn", "") or ""),
        date=str(getattr(subject, "date", "") or ""),
        summary_short=str(getattr(subject, "summary", "") or "")[:240],
        platform=str(getattr(subject, "platform", "") or ""),
        eps=int(getattr(subject, "eps", 0) or getattr(subject, "total_episodes", 0) or 0),
        total_episodes=int(getattr(subject, "total_episodes", 0) or getattr(subject, "eps", 0) or 0),
        tags=list(getattr(subject, "tags", []) or [])[:12],
        infobox_facts=_subject_infobox_facts(subject),
        search_query_ref=str(getattr(subject, "search_keyword", "") or ""),
        search_rank=int(getattr(subject, "search_rank", 0) or 0),
    )


def _episode_kind(episode: object) -> str:
    kind = str(getattr(episode, "kind", "") or "").casefold()
    if kind:
        return kind
    type_value = int(getattr(episode, "type", 0) or 0)
    if type_value == 0:
        return "regular"
    if type_value in {1, 2}:
        return "special"
    return "episode"


def _episode_card_from_api(episode: object, ref: str, subject_ref: str) -> BangumiItemCard:
    kind = _episode_kind(episode)
    return BangumiItemCard(
        ref=ref,
        item_kind="special" if kind == "special" else "episode",
        episode_id=int(getattr(episode, "id", 0) or 0),
        kind=kind,
        type=kind,
        sort=int(getattr(episode, "sort", 0) or 0),
        ep=int(getattr(episode, "ep", 0) or getattr(episode, "sort", 0) or 0),
        subject_ref=subject_ref,
        title=str(getattr(episode, "title", "") or ""),
        name=str(getattr(episode, "name", "") or ""),
        name_cn=str(getattr(episode, "name_cn", "") or ""),
        airdate=str(getattr(episode, "airdate", "") or ""),
        duration=str(getattr(episode, "duration", "") or ""),
        desc_short=str(getattr(episode, "desc", "") or "")[:240],
        source_form_hint=str(getattr(episode, "source_form_hint", "") or "unknown"),
        relation_to_main=str(getattr(episode, "relation_to_main", "") or ""),
        episode_number=int(getattr(episode, "sort", 0) or 0),
    )


def _item_sort_value(item: object) -> int | None:
    raw_sort = getattr(item, "sort", None)
    if raw_sort not in (None, ""):
        try:
            return int(raw_sort)
        except (TypeError, ValueError):
            return None
    raw_ep = getattr(item, "ep", None)
    if raw_ep not in (None, ""):
        try:
            return int(raw_ep)
        except (TypeError, ValueError):
            return None
    return None


def _item_kind_value(item: object) -> str:
    return str(getattr(item, "kind", "") or getattr(item, "item_kind", "") or "").casefold()


def _is_regular_item(item: object) -> bool:
    return _item_kind_value(item) in REGULAR_EPISODE_KINDS


def _target_alias_markers(*values: object) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip()))


def _search_query_ref_parts(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(" | ") if part.strip()]


def _search_query_markers(value: object, query_text_by_ref: dict[str, str] | None = None) -> tuple[str, ...]:
    query_text_by_ref = query_text_by_ref or {}
    markers: list[str] = []
    for part in _search_query_ref_parts(value):
        markers.append(query_text_by_ref.get(part, part))
    return _target_alias_markers(*markers)


def _subject_with_search_query_provenance(
    subject: BangumiSubjectCard,
    *,
    query: str,
    matched_query: str,
    rank: int,
) -> BangumiSubjectCard:
    parts = _search_query_ref_parts(getattr(subject, "search_query_ref", ""))
    for item in (query, matched_query):
        text = str(item or "").strip()
        if text and text not in parts:
            parts.append(text)
    if not parts:
        return subject
    current_rank = int(getattr(subject, "search_rank", 0) or 0)
    next_rank = current_rank if current_rank and current_rank <= rank else rank
    return subject.model_copy(update={"search_query_ref": " | ".join(parts), "search_rank": next_rank})


def _subject_with_related_query_provenance(
    subject: BangumiSubjectCard,
    *,
    source_subject: BangumiSubjectCard,
) -> BangumiSubjectCard:
    source_parts = _search_query_ref_parts(getattr(source_subject, "search_query_ref", ""))
    if not source_parts:
        return subject
    parts = _search_query_ref_parts(getattr(subject, "search_query_ref", ""))
    for part in source_parts:
        if part not in parts:
            parts.append(part)
    relation_path_refs = list(getattr(subject, "relation_path_refs", []) or [])
    source_ref = str(getattr(source_subject, "ref", "") or "").strip()
    if source_ref and source_ref not in relation_path_refs:
        relation_path_refs.append(source_ref)
    subject_rank = int(getattr(subject, "search_rank", 0) or 0)
    source_rank = int(getattr(source_subject, "search_rank", 0) or 0)
    next_rank = subject_rank if subject_rank else source_rank
    return subject.model_copy(
        update={
            "search_query_ref": " | ".join(parts),
            "search_rank": next_rank,
            "source_role": str(getattr(subject, "source_role", "") or "related_from_query_subject"),
            "relation_path_refs": relation_path_refs,
        }
    )


def _register_existing_targets(workspace: CaseEvidenceWorkspace, registry: LocatorRegistry) -> None:
    query_text_by_ref = {
        str(card.ref or ""): str(getattr(card, "query_text", "") or "")
        for card in workspace.query_cards
        if str(card.ref or "")
    }
    for subject in workspace.bangumi_subjects:
        subject_id = int(getattr(subject, "subject_id", 0) or 0)
        if not subject_id:
            continue
        locator = _subject_locator(subject_id, _subject_title(subject))
        registry.add(
            AgentLocator(
                locator=locator,
                kind="target_subject",
                title=_subject_title(subject),
                subject_ref=subject.ref,
                subject_id=subject_id,
                subject_eps=int(getattr(subject, "eps", 0) or getattr(subject, "total_episodes", 0) or 0),
                contract_role="support_only",
                markers=_subject_alias_markers(subject),
                query_markers=_search_query_markers(getattr(subject, "search_query_ref", ""), query_text_by_ref),
                search_rank=int(getattr(subject, "search_rank", 0) or 0),
                source_role=str(getattr(subject, "source_role", "") or ""),
                relation_to_main=str(getattr(subject, "relation_to_main", "") or ""),
                relation_path_refs=tuple(str(ref) for ref in list(getattr(subject, "relation_path_refs", []) or []) if str(ref)),
                debug_refs=(subject.ref,),
            )
        )
    subject_id_by_ref = {card.ref: int(card.subject_id or 0) for card in workspace.bangumi_subjects}
    locator_by_subject_ref = {
        card.ref: registry.subject_locator_by_id.get(int(card.subject_id or 0), "")
        for card in workspace.bangumi_subjects
    }
    for item in workspace.bangumi_items:
        sid = subject_id_by_ref.get(str(getattr(item, "subject_ref", "") or ""), 0)
        subject_locator = locator_by_subject_ref.get(str(getattr(item, "subject_ref", "") or ""), "")
        sort = _item_sort_value(item)
        if not sid or not subject_locator or sort is None:
            continue
        locator = _target_episode_locator_for_item(registry, subject_locator, item, sort)
        registry.add(
            AgentLocator(
                locator=locator,
                kind="target_episode",
                title=str(getattr(item, "title", "") or getattr(item, "name_cn", "") or getattr(item, "name", "") or locator),
                subject_ref=str(getattr(item, "subject_ref", "") or ""),
                subject_id=sid,
                item_refs=(str(getattr(item, "ref", "") or ""),),
                episode_start=sort,
                episode_end=sort,
                contract_role="support_only",
                markers=_target_alias_markers(
                    getattr(item, "title", ""),
                    getattr(item, "name", ""),
                    getattr(item, "name_cn", ""),
                ),
                debug_refs=(str(getattr(item, "ref", "") or ""),),
            )
        )


def _workspace_add_audits(workspace: CaseEvidenceWorkspace, audits: list[dict[str, object]]) -> CaseEvidenceWorkspace:
    object.__setattr__(workspace, "judge_request_audits", [*list(workspace.judge_request_audits or []), *audits])
    return workspace


def _workspace_add_targets(
    workspace: CaseEvidenceWorkspace,
    *,
    subjects: list[BangumiSubjectCard] | None = None,
    items: list[BangumiItemCard] | None = None,
    groups: list[BangumiGroupCard] | None = None,
    relations: list[BangumiRelationCard] | None = None,
    provenance: list[ProvenanceCard] | None = None,
) -> CaseEvidenceWorkspace:
    subjects = list(subjects or [])
    items = list(items or [])
    groups = list(groups or [])
    relations = list(relations or [])
    provenance = list(provenance or [])
    if not (subjects or items or groups or relations or provenance):
        return workspace
    existing_subject_ref_by_id = {
        int(card.subject_id or 0): str(card.ref or "")
        for card in workspace.bangumi_subjects
        if int(card.subject_id or 0)
    }
    existing_episode_ids = {int(card.episode_id or 0) for card in workspace.bangumi_items}
    subjects = [
        card
        for card in subjects
        if int(card.subject_id or 0) not in existing_subject_ref_by_id
        or str(card.ref or "") == existing_subject_ref_by_id.get(int(card.subject_id or 0), "")
    ]
    items = [card for card in items if int(card.episode_id or 0) not in existing_episode_ids]
    try:
        return workspace.with_added_evidence(
            subjects=subjects,
            items=items,
            groups=groups,
            relations=relations,
            provenance=provenance,
        )
    except ValueError:
        return workspace.with_replaced_cards(
            subjects=subjects,
            items=items,
            groups=groups,
            relations=relations,
            provenance=provenance,
        )


def _register_subject(registry: LocatorRegistry, subject: BangumiSubjectCard) -> str:
    subject_id = int(subject.subject_id or 0)
    locator = _subject_locator(subject_id, _subject_title(subject))
    registry.add(
        AgentLocator(
            locator=locator,
            kind="target_subject",
            title=_subject_title(subject),
            subject_ref=subject.ref,
            subject_id=subject_id,
            subject_eps=int(getattr(subject, "eps", 0) or getattr(subject, "total_episodes", 0) or 0),
            contract_role="support_only",
            markers=_subject_alias_markers(subject),
            query_markers=_search_query_markers(getattr(subject, "search_query_ref", "")),
            search_rank=int(getattr(subject, "search_rank", 0) or 0),
            source_role=str(getattr(subject, "source_role", "") or ""),
            relation_to_main=str(getattr(subject, "relation_to_main", "") or ""),
            relation_path_refs=tuple(str(ref) for ref in list(getattr(subject, "relation_path_refs", []) or []) if str(ref)),
            debug_refs=(subject.ref,),
        )
    )
    return locator


def _register_episode(registry: LocatorRegistry, subject_locator: str, subject_id: int, item: BangumiItemCard) -> str:
    sort = _item_sort_value(item)
    locator = _target_episode_locator_for_item(registry, subject_locator, item, sort)
    registry.add(
        AgentLocator(
            locator=locator,
            kind="target_episode",
            title=str(item.title or item.name_cn or item.name or locator),
            subject_ref=item.subject_ref,
            subject_id=subject_id,
            item_refs=(item.ref,),
            episode_start=sort,
            episode_end=sort,
            contract_role="support_only",
            markers=_target_alias_markers(item.title, item.name, item.name_cn),
            debug_refs=(item.ref,),
        )
    )
    return locator


def _target_episode_locator_for_item(
    registry: LocatorRegistry,
    subject_locator: str,
    item: object,
    sort: int | None,
) -> str:
    """Return a stable, unique visible locator for one Bangumi episode item.

    Bangumi can expose regular episodes and specials with the same sort number
    under one subject. The human agent must see a unique submit locator for each
    item; /episode/N remains the regular/default shape, while colliding non-
    regular items receive /special/N.
    """

    item_ref = str(getattr(item, "ref", "") or "").strip()
    if sort is None:
        return f"{subject_locator}/item/{item_ref.casefold()}" if item_ref else f"{subject_locator}/episode/unknown"
    base = f"{subject_locator}/episode/{sort}"
    existing = registry.locators.get(base)
    if existing is None:
        return base
    if item_ref and item_ref in existing.item_refs:
        return base
    if not _is_regular_item(item):
        return f"{subject_locator}/special/{sort}"
    return f"{subject_locator}/item/{item_ref.casefold()}" if item_ref else f"{subject_locator}/episode/{sort}"


def _inspect_local(locator: AgentLocator, scope: set[str]) -> dict[str, object]:
    episode_range = (
        _range_summary_from_episode_pairs(locator.episode_file_refs)
        if locator.episode_file_refs
        else _range_summary(list(locator.representative_labels))
    )
    result = {
        "locator": locator.locator,
        "kind": locator.kind,
        "contract_role": locator.contract_role,
        "title": locator.title,
        "file_count": len(locator.file_refs),
        "markers": list(locator.markers),
        "representative_labels": list(locator.representative_labels),
        "episode_range": episode_range,
        "files": list(locator.representative_labels) if "files" in scope or "samples" in scope else list(locator.representative_labels[:6]),
    }
    result.update(_episode_locator_hints(locator.locator, locator.episode_file_refs))
    return result


def _romaji_word_to_hiragana(word: str) -> str | None:
    text = re.sub(r"[^A-Za-z]", "", str(word or "")).casefold()
    if len(text) < 2:
        return None
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if (
            index + 1 < len(text)
            and char == text[index + 1]
            and char not in {"a", "i", "u", "e", "o", "n"}
        ):
            out.append("っ")
            index += 1
            continue
        if char == "n":
            next_char = text[index + 1] if index + 1 < len(text) else ""
            if not next_char or next_char not in {"a", "i", "u", "e", "o", "y"}:
                out.append("ん")
                index += 1
                continue
        matched = False
        for key in ROMAJI_KEYS:
            if text.startswith(key, index):
                out.append(ROMAJI_TO_HIRAGANA[key])
                index += len(key)
                matched = True
                break
        if not matched:
            return None
    return "".join(out)


def _romaji_to_hiragana_phrase(value: str) -> str | None:
    raw = re.sub(r"\s+", " ", str(value or "").strip(" ._-/[](){}"))
    if not raw or not re.fullmatch(r"[A-Za-z][A-Za-z\s_-]*", raw):
        return None
    words: list[str] = []
    converted_count = 0
    for word in raw.split():
        segments = [segment for segment in re.split(r"[-_]+", word) if segment]
        if not segments:
            return None
        converted_segments: list[str] = []
        for segment in segments:
            converted = _romaji_word_to_hiragana(segment)
            if not converted:
                return None
            converted_segments.append(converted)
            converted_count += 1
        words.append("".join(converted_segments))
    if converted_count == 0:
        return None
    result = " ".join(words)
    return result if re.search(r"[\u3040-\u309f]", result) else None


def _query_variant_priority(variant: str) -> int:
    text = str(variant or "")
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text):
        return 0
    return 1


def _distinctive_title_tail_variants(value: str) -> list[str]:
    raw = re.sub(r"\s+", " ", str(value or "").strip(" ._-/[](){}"))
    if not raw:
        return []
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\s'-]*", raw):
        return []
    tokens = [token for token in raw.split() if token]
    if len(tokens) < 4:
        return []
    suffix = tokens[-1]
    if not re.fullmatch(r"(?:[A-Z]{2,5}|[IVXLCDM]{2,6})", suffix):
        return []
    title_token = tokens[-2].strip("'-")
    if len(title_token) < 4 or title_token.casefold() in {"season", "part", "episode"}:
        return []
    return [f"{title_token} {suffix}"]


def _short_title_tail_variants(value: str) -> list[str]:
    raw = re.sub(r"\s+", " ", str(value or "").strip(" ._-/[](){}"))
    if not raw or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\s'-]*", raw):
        return []
    tokens = [token for token in raw.split() if token]
    if len(tokens) < 4:
        return []
    suffix = tokens[-1].strip("'-")
    title_token = tokens[-2].strip("'-")
    if not re.fullmatch(r"[A-Z0-9]", suffix):
        return []
    if len(title_token) < 4 or title_token.casefold() in {"season", "part", "episode"}:
        return []
    return [f"{title_token} {suffix}"]


def _suffix_title_query_variants(value: str) -> list[str]:
    raw = re.sub(r"\s+", " ", str(value or "").strip(" ._-/[](){}"))
    if not raw or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\s'-]*", raw):
        return []
    tokens = [token.strip("'-") for token in raw.split() if token.strip("'-")]
    if len(tokens) < 3:
        return []
    generic = _TITLE_TAIL_GENERIC_TOKENS.union({"season", "part", "episode", "movie", "the"})
    variants: list[str] = []
    seen: set[str] = set()
    for size in (3, 2):
        if len(tokens) <= size:
            continue
        suffix = tokens[-size:]
        suffix_folded = [token.casefold() for token in suffix]
        if any(token in generic for token in suffix_folded):
            continue
        has_distinctive_anchor = (
            any(len(token) >= 5 for token in suffix_folded)
            or len(set(suffix_folded)) < len(suffix_folded)
        )
        if not has_distinctive_anchor:
            continue
        value = " ".join(suffix)
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        variants.append(value)
    return variants[:2]


def _movie_title_query_variants(value: str) -> list[str]:
    raw = re.sub(r"\s+", " ", str(value or "").strip(" ._-/[](){}"))
    if not raw:
        return []
    match = re.fullmatch(r"(?i)(?P<title>.+?)\s+(?:the\s+)?movie", raw)
    if not match:
        return []
    title = re.sub(r"\s+", " ", match.group("title").strip(" ._-/[](){}"))
    if not title or not re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", title):
        return []
    return [f"\u5287\u5834\u7248 {title}", f"{title} \u5287\u5834\u7248"]


def _bracket_subtitle_query_variants(raw_value: str, base_title: str) -> list[str]:
    raw = str(raw_value or "")
    base = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", raw)
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", " ", base)
    base = QUERY_NOISE_TOKEN_RE.sub(" ", base)
    base = re.sub(r"(?i)\b(?:\d{3,4}p|[0-9a-f]{6,10})\b", " ", base)
    base = base.replace("_", " ").replace(".", " ")
    base = re.sub(r"\s+", " ", base).strip(" ._-/[](){}")
    if not base:
        base = re.sub(r"\s+", " ", str(base_title or "").strip(" ._-/[](){}"))
    if not raw or not base:
        return []
    generic_base_tokens = {
        "bd",
        "bdrip",
        "gekijouban",
        "main",
        "movie",
        "soushuuhen",
        "special",
        "sp",
        "the",
    }
    base_tokens = [
        token
        for token in re.split(r"\s+", base)
        if token and token.casefold() not in generic_base_tokens
    ]
    anchor = ""
    for token in reversed(base_tokens):
        cleaned = token.strip("'\"-")
        if len(cleaned) >= 3 and re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", cleaned):
            anchor = cleaned
            break
    if not anchor:
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", str(value or "").strip(" ._-/[](){}"))
        if not value:
            return
        folded = value.casefold()
        if folded in seen:
            return
        seen.add(folded)
        variants.append(value)

    for match in re.finditer(r"\[([^\]]+)\]|\(([^\)]+)\)", raw):
        content = str(match.group(1) or match.group(2) or "")
        content = re.sub(r"(?i)\b(?:\d{3,4}p|x26[45]|flac|aac|ac3|ma10p|hi10p)\b", " ", content)
        content = re.sub(r"(?i)^\s*\d{1,3}\s*[-_.:]?\s*", " ", content)
        content = content.replace("_", " ").replace(".", " ")
        content = re.sub(r"\s+", " ", content).strip(" ._-/[](){}")
        if not content or QUERY_NOISE_TOKEN_RE.search(content):
            continue
        if not re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", content):
            continue
        if len([token for token in content.split() if token]) < 2 and not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", content):
            continue
        add(content)
        add(f"{anchor} {content}")
        add(f"{base} {content}")
        if len(variants) >= 4:
            break
    return variants[:4]


def _known_alias_query_variants(value: str) -> list[str]:
    # Keep this hook generic. Work-specific aliases belong in visible evidence,
    # not in the fixed layer's query shaping.
    return []


def _local_special_season_search_context(workspace: CaseEvidenceWorkspace) -> tuple[int, bool]:
    local_by_ref = {card.ref: card for card in workspace.local_files if card.ref}
    main_refs = [ref for ref in list(workspace.contract.main_file_refs or []) if ref in local_by_ref]
    labels = " ".join(str(local_by_ref[ref].path or "") for ref in main_refs)
    special_hint = bool(
        re.search(
            r"(?i)(?:\bS00(?:E\d{1,3})?\b|\b(?:special|ova|oad|oav|sp|final|finale)\b|完结|完結|完结篇|完結編)",
            labels,
        )
    )
    return len(main_refs), special_hint


def _search_query_variants(query: str) -> list[str]:
    raw = re.sub(r"\s+", " ", str(query or "").strip())
    if not raw:
        return []
    result: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", str(value or "").strip(" ._-/[](){}"))
        if not value:
            return
        folded = value.casefold()
        if folded in seen:
            return
        seen.add(folded)
        result.append(value)

    def drop_or_keep_bracket(match: re.Match[str]) -> str:
        content = re.sub(r"\s+", " ", match.group(1).strip())
        if not content:
            return " "
        outside = raw[: match.start()] + raw[match.end() :]
        has_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", content))
        first_bracket = not raw[: match.start()].strip()
        noisy = bool(
            QUERY_NOISE_TOKEN_RE.search(content)
            or re.fullmatch(r"(?i)(?:\d{1,3}|s\d{1,2}e\d{1,3}|s\d{1,2}|[0-9a-f]{6,10})", content)
            or re.fullmatch(r"(?i)(?:\d{3,4}p|x26[45]|h\.?26[45]|flac|aac|ac3|ma10p|hi10p)(?:[_\s-].*)?", content)
        )
        if noisy or (first_bracket and not has_cjk and re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", outside)):
            return " "
        return f" {content} "

    def clean_title(value: str) -> str:
        value = re.sub(r"\.[A-Za-z0-9]{2,4}$", " ", value)
        value = re.sub(r"@\S+", " ", value)
        value = re.sub(r"\[([^\]]*)\]", drop_or_keep_bracket, value)
        value = re.sub(r"(?i)\bS\d{1,2}E\d{1,3}\b", " ", value)
        value = re.sub(r"(?i)\bS\d{1,2}\b", " ", value)
        value = re.sub(r"(?i)\bE\d{1,3}\b", " ", value)
        value = re.sub(r"(?i)(?:^|[\s._-])\d(?:[._-]\d){1,2}(?:$|[\s._-])", " ", value)
        value = re.sub(r"(?i)(?:^|[\s._-])\d{1,3}(?:$|[\s._-])", " ", value)
        value = re.sub(r"(?i)\b(?:19|20)\d{2}\b", " ", value)
        value = QUERY_NOISE_TOKEN_RE.sub(" ", value)
        value = re.sub(r"(?i)\b(?:\d{3,4}p|[0-9a-f]{6,10})\b", " ", value)
        value = value.replace("_", " ").replace(".", " ")
        value = re.sub(r"\s+", " ", value).strip(" ._-/[](){}")
        return value

    cleaned = clean_title(raw)
    raw_is_noisy = bool(cleaned and cleaned.casefold() != raw.casefold())
    bases = [cleaned] if raw_is_noisy else [cleaned, raw]
    for base in bases:
        if not base:
            continue
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
        spaced = re.sub(r"\s+", " ", spaced).strip()
        if spaced and spaced != base:
            for subtitle_variant in _bracket_subtitle_query_variants(raw, spaced):
                add(subtitle_variant)
            for alias in _known_alias_query_variants(spaced):
                add(alias)
            for tail in _distinctive_title_tail_variants(spaced):
                add(tail)
            for movie_variant in _movie_title_query_variants(spaced):
                add(movie_variant)
        for subtitle_variant in _bracket_subtitle_query_variants(raw, base):
            add(subtitle_variant)
        for alias in _known_alias_query_variants(base):
            add(alias)
        for movie_variant in _movie_title_query_variants(base):
            add(movie_variant)
        for tail in _distinctive_title_tail_variants(base):
            add(tail)
        base_has_unbalanced_bracket_fragment = bool(re.search(r"[()]", base))
        if not base_has_unbalanced_bracket_fragment:
            add(base)
            for tail in _short_title_tail_variants(base):
                add(tail)
            for suffix_variant in _suffix_title_query_variants(base):
                add(suffix_variant)
        if spaced and spaced != base:
            add(spaced)
            for tail in _short_title_tail_variants(spaced):
                add(tail)
            for suffix_variant in _suffix_title_query_variants(spaced):
                add(suffix_variant)
            tokens = spaced.split()
            if len(tokens) >= 3 and tokens[0].casefold() == tokens[1].casefold():
                add(" ".join([tokens[0] + tokens[1], *tokens[2:]]))
        compact = re.sub(r"[\s_-]+", "", base)
        if compact and compact != base and not base_has_unbalanced_bracket_fragment:
            add(compact)
        hiragana = _romaji_to_hiragana_phrase(base)
        if hiragana and len(base.split()) <= 2:
            add(hiragana)
    return result[:6]


def _search_tool(
    workspace: CaseEvidenceWorkspace,
    registry: LocatorRegistry,
    bangumi_client: object,
    args: SearchToolArgs,
    *,
    seen_variant_keys: set[str] | None = None,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    cards: list[dict[str, object]] = []
    new_subjects: list[BangumiSubjectCard] = []
    updated_subjects_by_ref: dict[str, BangumiSubjectCard] = {}
    repeated_existing_subject_count = 0
    skipped_repeated_variant_count = 0
    seen_variant_keys = seen_variant_keys if seen_variant_keys is not None else set()
    existing_subject_ids = {int(card.subject_id or 0) for card in workspace.bangumi_subjects}
    existing_subject_by_id = {
        int(card.subject_id or 0): card
        for card in workspace.bangumi_subjects
        if int(card.subject_id or 0)
    }
    existing_refs = [card.ref for card in workspace.bangumi_subjects]
    next_index = 0
    local_special_count, has_special_season_hint = _local_special_season_search_context(workspace)

    def local_count_priority(subject: object) -> int:
        if not has_special_season_hint or local_special_count <= 0:
            return 1
        eps = int(getattr(subject, "eps", 0) or getattr(subject, "total_episodes", 0) or 0)
        if eps == local_special_count:
            return 0
        if eps > local_special_count:
            return 2
        return 3

    for query in [str(item).strip() for item in args.queries if str(item).strip()][:8]:
        variant_payloads: list[dict[str, object]] = []
        subjects_by_id: dict[int, tuple[int, int, object, str]] = {}
        for variant in _search_query_variants(query):
            variant_key = variant.casefold()
            variant_priority = _query_variant_priority(variant)
            if variant_key in seen_variant_keys:
                skipped_repeated_variant_count += 1
                variant_payloads.append({"query": variant, "skipped": "already_searched_in_this_case"})
                continue
            seen_variant_keys.add(variant_key)
            try:
                subjects = list(getattr(bangumi_client, "search_subjects")(variant))[:SEARCH_RESULTS_PER_VARIANT]
            except Exception as exc:
                variant_payloads.append({"query": variant, "error": f"bangumi_search_failed: {exc}"})
                continue
            variant_payloads.append({"query": variant, "result_count": len(subjects)})
            for rank, subject in enumerate(subjects, start=1):
                subject_id = int(getattr(subject, "id", 0) or 0)
                if not subject_id:
                    continue
                existing = subjects_by_id.get(subject_id)
                candidate = (variant_priority, rank, subject, variant)
                if existing is None or (candidate[0], candidate[1]) < (existing[0], existing[1]):
                    subjects_by_id[subject_id] = candidate
        subjects = [
            (rank, subject, matched_query)
            for _subject_id, (priority, rank, subject, matched_query) in sorted(
                subjects_by_id.items(),
                key=lambda item: (local_count_priority(item[1][2]), item[1][0], item[1][1]),
            )
        ][:8]
        query_results: list[dict[str, object]] = []
        for rank, subject, matched_query in subjects:
            subject_id = int(getattr(subject, "id", 0) or 0)
            if not subject_id:
                continue
            workspace_ref = next(
                (card.ref for card in workspace.bangumi_subjects if int(card.subject_id or 0) == subject_id),
                "",
            )
            new_subject_index = next(
                (
                    index
                    for index, card in enumerate(new_subjects)
                    if int(card.subject_id or 0) == subject_id
                ),
                None,
            )
            new_subject_card = new_subjects[new_subject_index] if new_subject_index is not None else None
            ref = workspace_ref or (str(new_subject_card.ref or "") if new_subject_card is not None else "")
            if not ref and subject_id not in existing_subject_ids:
                next_index += 1
                ref = _next_ref("BS", [*existing_refs, *[card.ref for card in new_subjects]])
                card = _subject_card_from_api(subject, ref)
                card.search_query_ref = " | ".join(
                    dict.fromkeys(
                        item
                        for item in (query, matched_query)
                        if str(item or "").strip()
                    )
                )
                card.search_rank = rank
                new_subjects.append(card)
                existing_subject_ids.add(subject_id)
            elif ref:
                repeated_existing_subject_count += 1
                existing_card = existing_subject_by_id.get(subject_id) or new_subject_card
                if existing_card is not None:
                    updated_card = _subject_with_search_query_provenance(
                        existing_card,
                        query=query,
                        matched_query=matched_query,
                        rank=rank,
                    )
                    if workspace_ref:
                        updated_subjects_by_ref[updated_card.ref] = updated_card
                    elif new_subject_index is not None:
                        new_subjects[new_subject_index] = updated_card
            subject_card = next(
                (
                    card
                    for card in [
                        *updated_subjects_by_ref.values(),
                        *workspace.bangumi_subjects,
                        *new_subjects,
                    ]
                    if int(card.subject_id or 0) == subject_id
                ),
                None,
            )
            if subject_card is None:
                continue
            locator = _register_subject(registry, subject_card)
            query_results.append(
                {
                    "target": locator,
                    "title": _subject_title(subject_card),
                    "name": subject_card.name,
                    "name_cn": subject_card.name_cn,
                    "date": subject_card.date,
                    "eps": subject_card.eps or subject_card.total_episodes,
                    "rank": rank,
                    "matched_query": matched_query,
                    "match_source": "bangumi_search",
                }
            )
        cards.append({"query": query, "query_variants": variant_payloads, "results": query_results})
    workspace = _workspace_add_targets(workspace, subjects=[*new_subjects, *updated_subjects_by_ref.values()])
    total_result_count = sum(len(list(card.get("results") or [])) for card in cards)
    new_subject_count = len(new_subjects)
    search_progress = "new_subjects_added" if new_subject_count else ("existing_subjects_only" if total_result_count else "no_subject_results")
    all_variants_repeated = bool(cards) and skipped_repeated_variant_count > 0 and total_result_count == 0 and not new_subject_count
    return workspace, {
        "accepted": not all_variants_repeated,
        "issue": "all_query_variants_already_searched" if all_variants_repeated else "",
        "queries": cards,
        "search_progress": search_progress,
        "new_subject_count": new_subject_count,
        "existing_subject_result_count": repeated_existing_subject_count,
        "skipped_repeated_variant_count": skipped_repeated_variant_count,
        "total_result_count": total_result_count,
        "next_action_hint": (
            "Search only creates target subject locators. If this search added no new subject, change to a cleaner title "
            "query or inspect an existing plausible target; repeating broad release/file queries usually makes no progress. "
            "Repeated query variants are skipped within the same case. "
            "After an all_query_variants_already_searched rejection, do not search the same title family again; inspect visible candidates or submit a concrete resolution/fail_closed. "
            "Before submitting mapped episode ranges, inspect each chosen target subject with scope [details, episodes, related]."
        ),
    }


def _search_result_relevance_layer(
    result: dict[str, object],
    query: str,
    cognitive_workspace: CaseCognitiveWorkspace,
    rejected_targets: set[str],
) -> str:
    target = str(result.get("target") or "")
    if target.casefold() in rejected_targets:
        return "rejected_or_noisy"
    focus_locators = {item.casefold() for item in cognitive_workspace.attention_focus.locators}
    for unit in cognitive_workspace.active_work_units:
        focus_locators.update(item.casefold() for item in unit.targets)
        focus_locators.update(item.casefold() for item in unit.support)
    if target.casefold() in focus_locators:
        return "attention_focus"
    query_tokens = _distinctive_tokens(query)
    title_tokens = _distinctive_tokens(
        " ".join(
            str(result.get(key) or "")
            for key in ("title", "name", "name_cn", "matched_query")
        )
    )
    if query_tokens and title_tokens and query_tokens.intersection(title_tokens):
        return "title_relevant"
    return "other_visible_candidate"


def _layer_search_output_for_workspace(
    output: dict[str, object],
    cognitive_workspace: CaseCognitiveWorkspace,
) -> dict[str, object]:
    rejected_targets = {
        str(candidate.locator or "").casefold()
        for candidate in cognitive_workspace.rejected_or_noisy_candidates
        if str(candidate.locator or "").strip()
    }
    layered_queries: list[dict[str, object]] = []
    noise_count = 0
    for query_row in list(output.get("queries") or []):
        if not isinstance(query_row, dict):
            continue
        query_text = str(query_row.get("query") or "")
        tiers: dict[str, list[dict[str, object]]] = {
            "attention_focus": [],
            "title_relevant": [],
            "other_visible_candidate": [],
            "rejected_or_noisy": [],
        }
        for result in list(query_row.get("results") or []):
            if not isinstance(result, dict):
                continue
            layer = _search_result_relevance_layer(
                result,
                query_text,
                cognitive_workspace,
                rejected_targets,
            )
            row = {**result, "relevance_layer": layer}
            if layer == "rejected_or_noisy":
                noise_count += 1
                row["suppression_reason"] = "marked low relevance in cognitive_workspace.rejected_or_noisy_candidates"
            tiers[layer].append(row)
        tier_payload = [
            {"layer": layer, "results": rows}
            for layer, rows in tiers.items()
            if rows
        ]
        primary_results = [
            *tiers["attention_focus"],
            *tiers["title_relevant"],
            *tiers["other_visible_candidate"],
        ]
        layered_queries.append(
            {
                **query_row,
                "results": primary_results,
                "result_tiers": tier_payload,
            }
        )
    return {
        **output,
        "queries": layered_queries,
        "noise_candidate_count": noise_count,
        "search_surface_note": (
            "Candidates in rejected_or_noisy were not promoted as equal choices because the cognitive workspace "
            "already marked them low relevance."
            if noise_count
            else output.get("search_surface_note", "")
        ),
    }


def _inspect_target(
    workspace: CaseEvidenceWorkspace,
    registry: LocatorRegistry,
    bangumi_client: object,
    locator: AgentLocator,
    scope: set[str],
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    subject_id = int(locator.subject_id or 0)
    if not subject_id:
        return workspace, {"locator": locator.locator, "issue": "target_locator_missing_subject_id"}
    subject_ref = locator.subject_ref or registry.subject_ref_by_id.get(subject_id, "")
    subjects: list[BangumiSubjectCard] = []
    subject_card = next((card for card in workspace.bangumi_subjects if int(card.subject_id or 0) == subject_id), None)
    if subject_card is None or "details" in scope or "surface" in scope or "aliases" in scope:
        previous_subject_card = subject_card
        try:
            subject = getattr(bangumi_client, "get_subject")(subject_id)
        except Exception:
            subject = None
        if subject is not None:
            subject_ref = subject_ref or _next_ref("BS", [card.ref for card in workspace.bangumi_subjects])
            subject_card = _subject_card_from_api(subject, subject_ref)
            if (
                previous_subject_card is not None
                and not subject_card.search_query_ref
                and previous_subject_card.search_query_ref
            ):
                subject_card = subject_card.model_copy(
                    update={
                        "search_query_ref": previous_subject_card.search_query_ref,
                        "search_rank": previous_subject_card.search_rank,
                    }
                )
            subjects.append(subject_card)
    if subject_card is None:
        return workspace, {"locator": locator.locator, "issue": "target_subject_not_found"}
    subject_locator = _register_subject(registry, subject_card)
    items: list[BangumiItemCard] = []
    groups: list[BangumiGroupCard] = []
    episode_surface: dict[str, object] = {}
    if scope.intersection({"episodes", "specials", "surface", "details"}) or locator.kind in {"target_episode", "target_span"}:
        try:
            episodes = list(getattr(bangumi_client, "get_episodes")(subject_id))
        except Exception:
            episodes = []
        existing_episode_ids = {int(card.episode_id or 0) for card in workspace.bangumi_items}
        item_refs: list[str] = []
        existing_item_refs = [card.ref for card in workspace.bangumi_items]
        for episode in episodes:
            episode_id = int(getattr(episode, "id", 0) or 0)
            existing = next((card for card in workspace.bangumi_items if int(card.episode_id or 0) == episode_id and episode_id), None)
            if existing is not None:
                item = existing
            elif episode_id and episode_id not in existing_episode_ids:
                ref = _next_ref("BE", [*existing_item_refs, *[card.ref for card in items]])
                item = _episode_card_from_api(episode, ref, subject_ref)
                items.append(item)
                existing_episode_ids.add(episode_id)
            else:
                continue
            item_refs.append(item.ref)
            _register_episode(registry, subject_locator, subject_id, item)
        regular_sorts = [
            int(sort_value)
            for item in [*workspace.bangumi_items, *items]
            for sort_value in [_item_sort_value(item)]
            if str(getattr(item, "subject_ref", "") or "") == subject_ref
            and str(getattr(item, "kind", "") or "").casefold() in {"", "regular", "episode", "0"}
            and sort_value is not None
            and int(sort_value) >= 0
        ]
        if regular_sorts:
            span_locator = f"{subject_locator}/episodes/{min(regular_sorts)}-{max(regular_sorts)}"
            span_refs = [
                ref
                for sort in range(min(regular_sorts), max(regular_sorts) + 1)
                for ref in [registry.item_ref_by_subject_sort.get((subject_id, sort), "")]
                if ref
            ]
            registry.add(
                AgentLocator(
                    locator=span_locator,
                    kind="target_span",
                    title=f"{_subject_title(subject_card)} episodes {min(regular_sorts)}-{max(regular_sorts)}",
                    subject_ref=subject_ref,
                    subject_id=subject_id,
                    item_refs=tuple(span_refs),
                    episode_start=min(regular_sorts),
                    episode_end=max(regular_sorts),
                    contract_role="support_only",
                    debug_refs=tuple(span_refs),
                )
            )
            episode_surface["regular_span_locator"] = span_locator
        episode_cards = [
            card for card in [*workspace.bangumi_items, *items]
            if str(getattr(card, "subject_ref", "") or "") == subject_ref
        ]
        episode_surface.update(
            {
                "episode_count": len(episode_cards),
                "regular_count": len(
                    [
                        card for card in episode_cards
                        if str(getattr(card, "kind", "") or "").casefold() in {"", "regular", "episode", "0"}
                    ]
                ),
                "special_count": len(
                    [
                        card for card in episode_cards
                        if str(getattr(card, "kind", "") or "").casefold() not in {"", "regular", "episode", "0"}
                    ]
                ),
                "episode_samples": [
                    {
                        "target": _target_item_locator_for_ref(registry, card.ref),
                        "sort": int(_item_sort_value(card) or 0),
                        "kind": card.kind,
                        "title": card.title or card.name_cn or card.name,
                    }
                    for card in episode_cards[:6]
                ],
                "episode_tail_samples": [
                    {
                        "target": _target_item_locator_for_ref(registry, card.ref),
                        "sort": int(_item_sort_value(card) or 0),
                        "kind": card.kind,
                        "title": card.title or card.name_cn or card.name,
                    }
                    for card in episode_cards[-4:]
                ],
            }
        )
        if item_refs:
            group_ref = _next_ref("BR", [card.ref for card in workspace.bangumi_groups])
            groups.append(
                BangumiGroupCard(
                    ref=group_ref,
                    group_kind="season_group",
                    member_refs_visible=item_refs,
                    sort_start=min([int(value) for card in items for value in [_item_sort_value(card)] if value is not None] or [0]),
                    sort_end=max([int(value) for card in items for value in [_item_sort_value(card)] if value is not None] or [0]),
                    subject_refs=[subject_ref],
                    item_refs=item_refs,
                )
            )
    related_surface: list[dict[str, object]] = []
    if "related" in scope:
        try:
            relations = list(getattr(bangumi_client, "get_related_subjects")(subject_id))[:12]
        except Exception:
            relations = []
        new_subject_ids = {int(card.subject_id or 0) for card in [*workspace.bangumi_subjects, *subjects]}
        for relation in relations:
            rid = int(getattr(relation, "id", 0) or 0)
            if not rid or rid in new_subject_ids:
                continue
            ref = _next_ref("BS", [*[card.ref for card in workspace.bangumi_subjects], *[card.ref for card in subjects]])
            try:
                related_detail = getattr(bangumi_client, "get_subject")(rid)
            except Exception:
                related_detail = None
            if related_detail is not None:
                subject = _subject_card_from_api(related_detail, ref)
                subject = subject.model_copy(update={"relation_to_main": str(getattr(relation, "relation", "") or "")})
            else:
                subject = BangumiSubjectCard(
                    ref=ref,
                    subject_id=rid,
                    subject_type="anime",
                    title=str(getattr(relation, "name_cn", "") or getattr(relation, "name", "") or ""),
                    name=str(getattr(relation, "name", "") or ""),
                    name_cn=str(getattr(relation, "name_cn", "") or ""),
                    relation_to_main=str(getattr(relation, "relation", "") or ""),
                )
            subject = _subject_with_related_query_provenance(subject, source_subject=subject_card)
            subjects.append(subject)
            new_subject_ids.add(rid)
            rel_locator = _register_subject(registry, subject)
            related_surface.append(
                {
                    "target": rel_locator,
                    "relation": str(getattr(relation, "relation", "") or ""),
                    "title": _subject_title(subject),
                    "eps": subject.eps or subject.total_episodes,
                    "date": subject.date,
                    "source_role": subject.source_role,
                    "source_query_texts": _search_query_markers(subject.search_query_ref),
                }
            )
    workspace = _workspace_add_targets(workspace, subjects=subjects, items=items, groups=groups)
    workspace = workspace.with_seen_detail_refs([subject_ref, *[item.ref for item in items]])
    result = {
        "locator": subject_locator,
        "kind": "target_subject",
        "subject_id": subject_id,
        "subject_ref_debug": subject_ref,
        "title": _subject_title(subject_card),
        "name": subject_card.name,
        "name_cn": subject_card.name_cn,
        "date": subject_card.date,
        "eps": subject_card.eps or subject_card.total_episodes,
        "platform": subject_card.platform,
    }
    if scope.intersection({"aliases", "details", "surface"}):
        result["aliases"] = list(
            dict.fromkeys(
                [
                    value
                    for value in (
                        subject_card.title,
                        subject_card.name,
                        subject_card.name_cn,
                        *_infobox_alias_values_from_facts(subject_card.infobox_facts),
                    )
                    if str(value or "").strip()
                ]
            )
        )
        if subject_card.infobox_facts:
            result["infobox_facts"] = list(subject_card.infobox_facts[:12])
    if episode_surface:
        result["episodes"] = episode_surface
    if related_surface:
        result["related"] = related_surface
    return workspace, result


def _inspect_tool(
    workspace: CaseEvidenceWorkspace,
    registry: LocatorRegistry,
    bangumi_client: object,
    args: InspectToolArgs,
) -> tuple[CaseEvidenceWorkspace, dict[str, object]]:
    scope = {str(item).strip().casefold() for item in list(args.scope or []) if str(item).strip()}
    if not scope:
        scope = {"surface", "samples"}
    observations: list[dict[str, object]] = []
    for raw_locator in [str(item).strip() for item in args.locators if str(item).strip()][:12]:
        locator, issue = registry.resolve(raw_locator)
        if issue:
            observations.append(issue)
            continue
        if locator is None:
            observations.append({"issue": "locator_not_found", "locator": raw_locator})
            continue
        if locator.kind in {"local", "support"}:
            observations.append(_inspect_local(locator, scope))
        elif locator.kind in {"target_subject", "target_episode", "target_span"}:
            workspace, obs = _inspect_target(workspace, registry, bangumi_client, locator, scope)
            observations.append(obs)
    return workspace, {"accepted": True, "observations": observations}


def _workspace_update_locators(update: CaseCognitiveWorkspace) -> list[str]:
    locators: list[str] = []
    locators.extend(update.attention_focus.locators)
    for unit in update.active_work_units:
        locators.extend(unit.local)
        locators.extend(unit.targets)
        locators.extend(unit.support)
        locators.extend(unit.evidence_locators)
    for item in update.investigation_agenda:
        locators.extend(item.locators)
    for candidate in update.rejected_or_noisy_candidates:
        if candidate.locator:
            locators.append(candidate.locator)
    return [str(item).strip() for item in locators if str(item).strip()]


def _validate_workspace_update_locators(
    registry: LocatorRegistry,
    update: CaseCognitiveWorkspace,
) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for locator in list(dict.fromkeys(_workspace_update_locators(update))):
        _, issue = registry.resolve(locator)
        if issue:
            issues.append(issue)
    return issues


def _compact_cognitive_workspace(workspace: CaseCognitiveWorkspace) -> dict[str, object]:
    payload = workspace.model_dump(mode="json")
    payload["primary_hypotheses"] = payload.get("primary_hypotheses", [])[:8]
    active_units: list[dict[str, object]] = []
    for unit in list(payload.get("active_work_units") or [])[:16]:
        if not isinstance(unit, dict):
            continue
        active_units.append(
            {
                "work_unit_id": unit.get("work_unit_id"),
                "label": _truncate_repair_text(unit.get("label"), limit=120),
                "status": unit.get("status"),
                "local": list(unit.get("local") or [])[:4],
                "targets": list(unit.get("targets") or [])[:4],
                "support": list(unit.get("support") or [])[:4],
                "hypothesis": _truncate_repair_text(unit.get("hypothesis"), limit=160),
                "evidence_summary": _truncate_repair_text(unit.get("evidence_summary"), limit=260),
                "evidence_locators": list(unit.get("evidence_locators") or [])[:6],
                "gaps": list(dict.fromkeys(str(item) for item in list(unit.get("gaps") or []) if str(item).strip()))[:8],
                "blocking_issue": _truncate_repair_text(unit.get("blocking_issue"), limit=100),
                "required_next_action": _truncate_repair_text(unit.get("required_next_action"), limit=220),
                "closure_condition": _truncate_repair_text(unit.get("closure_condition"), limit=220),
            }
        )
    payload["active_work_units"] = active_units
    payload["investigation_agenda"] = _compact_repair_payload(payload.get("investigation_agenda", [])[:12], list_limit=12, text_limit=220)
    payload["rejected_or_noisy_candidates"] = _compact_repair_payload(payload.get("rejected_or_noisy_candidates", [])[:16], list_limit=16, text_limit=180)
    payload["evidence_gaps"] = [
        _truncate_repair_text(item, limit=180)
        for item in list(payload.get("evidence_gaps") or [])[:12]
        if str(item).strip()
    ]
    readiness = payload.get("resolution_readiness")
    if isinstance(readiness, dict):
        readiness["ready_work_units"] = list(readiness.get("ready_work_units") or [])[:16]
        readiness["blocking_work_units"] = list(readiness.get("blocking_work_units") or [])[:16]
        readiness["mechanical_gaps"] = _compact_repair_payload(readiness.get("mechanical_gaps") or [], list_limit=12, text_limit=180)
        readiness["evidence_gaps"] = [
            _truncate_repair_text(item, limit=180)
            for item in list(readiness.get("evidence_gaps") or [])[:12]
            if str(item).strip()
        ]
        readiness["summary"] = _truncate_repair_text(readiness.get("summary"), limit=220)
    return payload


def _initial_cognitive_workspace_from_desk(desk: dict[str, object]) -> CaseCognitiveWorkspace:
    local_rows = [row for row in list(desk.get("local_locators") or []) if isinstance(row, dict)]
    active_units: list[WorkUnitFocus] = []
    for index, row in enumerate(local_rows[:24], start=1):
        locator = str(row.get("locator") or "").strip()
        if not locator:
            continue
        active_units.append(
            WorkUnitFocus(
                work_unit_id=f"WU{index}",
                label=str(row.get("title") or locator),
                status="open",
                local=[locator],
                hypothesis=str(row.get("title") or ""),
                evidence_summary=(
                    f"file_count={row.get('file_count')}; "
                    f"episode_range={row.get('episode_range')}; "
                    f"markers={list(row.get('markers') or [])[:6]}; "
                    f"labels={[ _truncate_repair_text(label, limit=100) for label in list(row.get('representative_labels') or [])[:2] ]}"
                ),
                gaps=["target_not_chosen"],
            )
        )
    focus = AttentionFocus(
        summary="Resolve the first open must_account work unit, then keep the agenda synchronized.",
        locators=active_units[0].local if active_units else [],
        next_action="search clean title aliases or inspect visible targets",
    )
    title_cues = [str(item) for item in list(desk.get("possible_title_cues") or []) if str(item).strip()][:8]
    return CaseCognitiveWorkspace(
        primary_hypotheses=title_cues,
        active_work_units=active_units,
        attention_focus=focus,
        investigation_agenda=[
            InvestigationAgendaItem(
                agenda_id="AG1",
                question="Choose a semantic outcome for every must_account local locator.",
                status="open",
                locators=[unit.local[0] for unit in active_units if unit.local][:16],
                next_action="Use batch search/inspect, then submit a package resolution.",
            )
        ]
        if active_units
        else [],
        evidence_gaps=["target evidence not inspected yet"] if active_units else [],
        resolution_readiness=ResolutionReadiness(
            status="not_ready",
            blocking_work_units=[unit.work_unit_id for unit in active_units[:16]],
            evidence_gaps=["target evidence not inspected yet"] if active_units else [],
            summary="Initial desk built from local locators; no package resolution is ready yet.",
        ),
    )


def _cognitive_signature(workspace: CaseCognitiveWorkspace) -> str:
    return json.dumps(workspace.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, default=str)


def _readiness_signature(workspace: CaseCognitiveWorkspace) -> str:
    readiness = workspace.resolution_readiness.model_dump(mode="json")
    readiness.pop("repeated_rejection", None)
    return json.dumps(readiness, ensure_ascii=False, sort_keys=True, default=str)


def _closed_agenda_count(workspace: CaseCognitiveWorkspace) -> int:
    return sum(1 for item in workspace.investigation_agenda if item.status == "closed")


def _workspace_counts(workspace: CaseEvidenceWorkspace) -> dict[str, int]:
    return {
        "subjects": len(workspace.bangumi_subjects),
        "items": len(workspace.bangumi_items),
        "relations": len(workspace.bangumi_relations),
        "groups": len(workspace.bangumi_groups),
        "seen_detail_refs": len(workspace.seen_detail_refs),
    }


def _mechanical_gap_rows_from_repair(agenda: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    issue_counts = agenda.get("issue_counts") if isinstance(agenda.get("issue_counts"), dict) else {}
    mechanical_issue_codes = {str(code) for code in issue_counts}
    for code, count in issue_counts.items():
        rows.append({"issue_code": str(code), "count": int(count or 0)})
    required_missing = agenda.get("required_missing_work_units")
    if isinstance(required_missing, list) and required_missing:
        rows.append({"issue_code": "coverage_missing", "count": len(required_missing)})
    blocking_units = agenda.get("blocking_units")
    if isinstance(blocking_units, list):
        for unit in blocking_units[:12]:
            if not isinstance(unit, dict):
                continue
            issue_value = unit.get("issue") or unit.get("issues") or unit.get("issue_codes")
            issue_codes = _issue_codes_from_value(issue_value) or ["unit_mechanical_gap"]
            issue_codes = [
                code
                for code in issue_codes
                if code in mechanical_issue_codes or code not in SEMANTIC_SUBMIT_DIAGNOSTIC_CODES
            ]
            if not issue_codes:
                continue
            local_value = unit.get("local")
            if isinstance(local_value, list):
                local_list = [str(item) for item in local_value if str(item).strip()]
            elif str(local_value or "").strip():
                local_list = [str(local_value).strip()]
            else:
                local_list = []
            for issue_code in issue_codes[:4]:
                rows.append(
                    {
                        "issue_code": issue_code,
                        "unit": str(unit.get("unit") or ""),
                        "local": local_list,
                        "target": str(unit.get("target") or ""),
                        "detail": issue_code,
                    }
                )
    return rows[:24]


def _locators_from_repair_row(row: dict[str, object]) -> set[str]:
    result: set[str] = set()
    for key in ("local", "target", "locator"):
        value = row.get(key)
        if isinstance(value, list):
            result.update(str(item) for item in value if str(item).strip())
        elif str(value or "").strip():
            result.add(str(value).strip())
    return result


def _primary_issue_code(row: dict[str, object]) -> str:
    issue_value = row.get("issue") or row.get("issue_codes") or row.get("issues")
    return (_issue_codes_from_value(issue_value) or ["mechanical_repair"])[0]


def _repair_row_label(row: dict[str, object]) -> str:
    label = str(row.get("unit") or "").strip()
    if label:
        return label
    local = row.get("local")
    if isinstance(local, list) and local:
        return str(local[0])
    if str(local or "").strip():
        return str(local).strip()
    return str(row.get("target") or row.get("locator") or "mechanical repair").strip()


def _repair_required_next_action(row: dict[str, object]) -> str:
    issue_code = _primary_issue_code(row)
    if row.get("split_first_repair") or row.get("local_slice_mapping_options"):
        return "Resolve the parent locator at visible local slice granularity, or fail_closed the exact unresolved slice."
    if row.get("target_surface_repairs") or row.get("visible_alternate_subjects") or row.get("target_surface_visible"):
        return "Inspect the visible target surface or change the mapped target/range before another submit."
    if row.get("candidate_local_locators"):
        return "Replace the invalid local reference with a visible local:// locator, if it matches the intended work unit."
    if row.get("candidate_target_locators"):
        return "Replace the invalid target reference with a visible target:// locator, if it matches the intended target."
    if row.get("search_queries_to_try"):
        return "Run one batched search for the listed queries, inspect plausible targets, or fail_closed this exact locator."
    if issue_code in {"duplicate_target", "duplicate_target_item"}:
        return "Change one conflicting unit's target/outcome, or fail_closed the exact unresolved conflicting unit."
    if issue_code in {"coverage_missing", "count_mismatch", "composite_feature_shape_invalid"}:
        return "Change local granularity, target range, outcome, or fail_closed the exact unresolved local locator."
    if issue_code in {"mapped_title_season_mismatch", "mapped_target_title_bridge_missing"}:
        return "Choose a visible target with adequate title/season support, inspect/search more evidence, or fail_closed this locator."
    return "Change the cited locator/range/support/outcome, inspect/search needed facts, or fail_closed this exact work unit."


def _repair_closure_condition(row: dict[str, object]) -> str:
    issue_code = _primary_issue_code(row)
    if row.get("split_first_repair") or row.get("local_slice_mapping_options"):
        return "Closed when every intended local slice is submitted exactly once, or unresolved slices are fail_closed explicitly."
    if row.get("target_surface_repairs") or row.get("visible_alternate_subjects") or row.get("target_surface_visible"):
        return "Closed when the work unit cites inspected target evidence and no longer retries the same absent episode/range."
    if row.get("candidate_local_locators") or row.get("candidate_target_locators"):
        return "Closed when the submitted locator resolves to a visible locator, or the unresolved locator is fail_closed explicitly."
    if row.get("search_queries_to_try"):
        return "Closed when the listed search/inspect evidence is used, or the exact locator is fail_closed with the remaining blocker."
    if issue_code in {"duplicate_target", "duplicate_target_item"}:
        return "Closed when the merged package has no duplicate target items for these work units."
    if issue_code in {"coverage_missing", "count_mismatch", "composite_feature_shape_invalid"}:
        return "Closed when coverage/count/shape checks pass for this work unit, or the exact unit is fail_closed with a blocker."
    if issue_code in {"mapped_title_season_mismatch", "mapped_target_title_bridge_missing"}:
        return "Closed when target title/season provenance is adequate for the submitted outcome, or the unit is fail_closed."
    return "Closed when this issue no longer appears in submit feedback, or the exact work unit is fail_closed with a blocker."


def _repair_agenda_id(row: dict[str, object], index: int) -> str:
    seed = "|".join([_primary_issue_code(row), _repair_row_label(row), *sorted(_locators_from_repair_row(row))])
    return f"REPAIR-{index}-{_slug(seed, fallback='mechanical')[:64]}"


def _repair_agenda_rows(agenda: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    raw_rows = [row for row in list(agenda.get("blocking_units") or []) if isinstance(row, dict)]
    raw_missing = [row for row in list(agenda.get("required_missing_work_units") or []) if isinstance(row, dict)]
    surface_rows = [
        row
        for row in list(agenda.get("visible_target_surface_missing_units") or [])
        if isinstance(row, dict)
    ]

    def enrich_with_surface(row: dict[str, object]) -> dict[str, object]:
        merged = dict(row)
        unit = str(row.get("unit") or "").strip()
        row_locators = _locators_from_repair_row(row)
        for surface in surface_rows:
            surface_unit = str(surface.get("unit") or "").strip()
            surface_locators = _locators_from_repair_row(surface)
            same_scope = bool(unit and surface_unit and unit == surface_unit) or bool(
                row_locators.intersection(surface_locators)
            )
            if not same_scope:
                continue
            merged["target_surface_visible"] = True
            for key in (
                "available_target_episode_numbers",
                "search_queries_to_try",
                "continuation_evidence_hint",
                "local_slice_mapping_options",
                "local_target_title_pairing_options",
            ):
                if surface.get(key) and not merged.get(key):
                    merged[key] = surface.get(key)
        return merged

    for original_row in raw_rows:
        row = enrich_with_surface(original_row)
        locators = sorted(_locators_from_repair_row(row))
        visible_options = {
            key: _compact_repair_field(key, row.get(key))
            for key in (
                "local_slice_mapping_options",
                "local_target_title_pairing_options",
                "candidate_local_locators",
                "candidate_target_locators",
                "single_file_target_item_options",
                "visible_alternate_subjects",
                "search_queries_to_try",
            )
            if row.get(key)
        }
        rows.append(
            {
                "label": _repair_row_label(row),
                "issue": _primary_issue_code(row),
                "locators": locators,
                "local": row.get("local"),
                "target": row.get("target"),
                "required_next_action": _repair_required_next_action(row),
                "closure_condition": _repair_closure_condition(row),
                "visible_options": visible_options,
            }
        )
    for row in raw_missing:
        raw_locator = row.get("locator") or row.get("local") or row.get("locators")
        if isinstance(raw_locator, list):
            locators = [str(item).strip() for item in raw_locator if str(item).strip()]
        elif str(raw_locator or "").strip():
            locators = [str(raw_locator).strip()]
        else:
            locators = []
        for locator in locators:
            rows.append(
                {
                    "label": str(row.get("label") or row.get("title") or locator),
                    "issue": "coverage_missing",
                    "locators": [locator],
                    "local": [locator],
                    "target": "",
                    "required_next_action": "Submit this missing local locator exactly once with the Agent's chosen semantic outcome.",
                    "closure_condition": "Closed when this local locator is covered exactly once or fail_closed explicitly.",
                    "visible_options": {},
                }
            )
    result: list[dict[str, object]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for index, row in enumerate(rows, start=1):
        key = (str(row.get("issue") or ""), tuple(str(item) for item in list(row.get("locators") or [])))
        if key in seen:
            continue
        seen.add(key)
        row["agenda_id"] = _repair_agenda_id(
            {
                "unit": row.get("label"),
                "issue": row.get("issue"),
                "local": row.get("local"),
                "target": row.get("target"),
            },
            index,
        )
        result.append(row)
        if len(result) >= 12:
            break
    return result


def _repair_focus_from_rows(rows: list[dict[str, object]], *, repeated: bool) -> AttentionFocus:
    if not rows:
        return AttentionFocus()
    first = rows[0]
    locators: list[str] = []
    for row in rows[:4]:
        locators.extend(str(item) for item in list(row.get("locators") or []) if str(item).strip())
    return AttentionFocus(
        summary=(
            "Active submit repair agenda is open; close these mechanical work-unit blockers before broad reasoning."
            if not repeated
            else "Repeated submit repair agenda is still open; change evidence/focus/readiness or fail_closed the exact blocker."
        ),
        locators=list(dict.fromkeys(locators))[:12],
        next_action=str(first.get("required_next_action") or "Close the active repair agenda."),
    )


def _agenda_items_with_repair_rows(
    existing: list[InvestigationAgendaItem],
    rows: list[dict[str, object]],
) -> list[InvestigationAgendaItem]:
    active_ids = {str(row.get("agenda_id") or "") for row in rows if str(row.get("agenda_id") or "")}
    repair_items = [
        InvestigationAgendaItem(
            agenda_id=str(row.get("agenda_id") or ""),
            question=f"Close submit repair for {_repair_row_label({'unit': row.get('label')})}",
            status="open",
            locators=[str(item) for item in list(row.get("locators") or []) if str(item).strip()][:12],
            next_action=str(row.get("required_next_action") or ""),
            blocking_issue=str(row.get("issue") or ""),
            closure_condition=str(row.get("closure_condition") or ""),
        )
        for row in rows
        if str(row.get("agenda_id") or "")
    ]
    carried: list[InvestigationAgendaItem] = []
    for item in existing:
        if str(item.agenda_id or "").startswith("REPAIR-"):
            if item.agenda_id in active_ids:
                continue
            carried.append(
                item.model_copy(
                    update={
                        "status": "closed",
                        "closed_reason": "No longer present in the latest mechanical submit rejection.",
                    }
                )
            )
        else:
            carried.append(item)
    return [*repair_items, *carried][:24]


def _workspace_with_submit_rejection(
    workspace: CaseCognitiveWorkspace,
    agenda: dict[str, object],
    *,
    repeated: bool,
) -> CaseCognitiveWorkspace:
    gaps = _mechanical_gap_rows_from_repair(agenda)
    repair_rows = _repair_agenda_rows(agenda)
    blocking_rows = [row for row in agenda.get("blocking_units") or [] if isinstance(row, dict)]
    blocking_labels = [str(row.get("unit") or "") for row in blocking_rows if str(row.get("unit") or "").strip()]
    blocking_locators: set[str] = set()
    for row in blocking_rows:
        blocking_locators.update(_locators_from_repair_row(row))
    required_missing = agenda.get("required_missing_work_units")
    if isinstance(required_missing, list):
        for item in required_missing:
            if isinstance(item, dict):
                locator = str(item.get("locator") or "").strip()
                if locator:
                    blocking_locators.add(locator)
    updated_units: list[WorkUnitFocus] = []
    seen_unit_keys: set[str] = set()
    repair_by_locator: dict[str, dict[str, object]] = {}
    repair_by_label: dict[str, dict[str, object]] = {}
    for row in repair_rows:
        if str(row.get("label") or "").strip():
            repair_by_label[str(row.get("label"))] = row
        for locator in list(row.get("locators") or []):
            repair_by_locator[str(locator)] = row
    for unit in workspace.active_work_units:
        unit_locators = set(unit.local).union(unit.targets).union(unit.support)
        blocked = bool(unit_locators.intersection(blocking_locators)) or (
            bool(unit.label) and unit.label in blocking_labels
        )
        repair_row = None
        for locator in unit_locators:
            repair_row = repair_by_locator.get(locator)
            if repair_row:
                break
        if repair_row is None and unit.label:
            repair_row = repair_by_label.get(unit.label)
        next_gap_codes = list(unit.gaps)
        if blocked:
            for gap in gaps:
                code = str(gap.get("issue_code") or "").strip()
                if code and code not in next_gap_codes:
                    next_gap_codes.append(code)
        target = str((repair_row or {}).get("target") or "").strip()
        next_targets = list(unit.targets)
        if blocked and target.startswith("target://") and target not in next_targets:
            next_targets.append(target)
        next_evidence_locators = list(unit.evidence_locators)
        for locator in list((repair_row or {}).get("locators") or []):
            locator = str(locator or "").strip()
            if locator and locator not in next_evidence_locators:
                next_evidence_locators.append(locator)
        next_unit = unit.model_copy(
            update={
                "status": "blocked" if blocked else unit.status,
                "gaps": next_gap_codes[:12] if blocked else unit.gaps,
                "targets": next_targets[:8],
                "evidence_summary": (
                    f"submit repair: {(repair_row or {}).get('issue')}; "
                    f"{(repair_row or {}).get('closure_condition')}"
                    if blocked and repair_row
                    else unit.evidence_summary
                ),
                "evidence_locators": next_evidence_locators[:12],
                "blocking_issue": str((repair_row or {}).get("issue") or unit.blocking_issue),
                "required_next_action": str((repair_row or {}).get("required_next_action") or unit.required_next_action),
                "closure_condition": str((repair_row or {}).get("closure_condition") or unit.closure_condition),
            }
        )
        updated_units.append(next_unit)
        seen_unit_keys.update(unit.local)
        seen_unit_keys.update(unit.targets)
        seen_unit_keys.update(unit.support)
    for row in repair_rows:
        label = str(row.get("label") or "").strip()
        locators = [str(item) for item in list(row.get("locators") or []) if str(item).strip()]
        if not locators:
            continue
        if any(locator in seen_unit_keys for locator in locators):
            continue
        target = str(row.get("target") or "").strip()
        updated_units.append(
            WorkUnitFocus(
                work_unit_id=f"repair:{len(updated_units) + 1}",
                label=label or locators[0],
                status="blocked",
                local=[locator for locator in locators if locator.startswith("local://")][:4],
                targets=([target] if target.startswith("target://") else []) + [
                    locator for locator in locators if locator.startswith("target://")
                ][:4],
                evidence_locators=locators[:8],
                gaps=[str(gap.get("issue_code") or "") for gap in gaps if gap.get("issue_code")][:8],
                blocking_issue=str(row.get("issue") or ""),
                required_next_action=str(row.get("required_next_action") or ""),
                closure_condition=str(row.get("closure_condition") or ""),
            )
        )
        seen_unit_keys.update(locators)
    focus = _repair_focus_from_rows(repair_rows, repeated=repeated)
    agenda_items = _agenda_items_with_repair_rows(workspace.investigation_agenda, repair_rows)
    evidence_gaps = list(workspace.evidence_gaps)[:12]
    for row in repair_rows[:8]:
        issue = str(row.get("issue") or "").strip()
        label = str(row.get("label") or "").strip()
        gap = f"{label}: {issue}" if label and issue else issue or label
        if gap and gap not in evidence_gaps:
            evidence_gaps.append(gap)
    readiness = ResolutionReadiness(
        status="blocked",
        ready_work_units=list(workspace.resolution_readiness.ready_work_units),
        blocking_work_units=list(
            dict.fromkeys(
                [
                    *[str(row.get("label") or "") for row in repair_rows if str(row.get("label") or "").strip()],
                    *blocking_labels,
                    *sorted(blocking_locators),
                ]
            )
        )[:24],
        mechanical_gaps=gaps,
        evidence_gaps=evidence_gaps[:16],
        repeated_rejection=repeated,
        summary=(
            "Same mechanical submit gap repeated; return to inspect/search/note or change the named work unit."
            if repeated
            else "Submit rejected on mechanical readiness gaps; close the active repair agenda before resubmitting."
        ),
    )
    return workspace.model_copy(
        update={
            "active_work_units": updated_units[:24],
            "attention_focus": focus if repair_rows else workspace.attention_focus,
            "investigation_agenda": agenda_items,
            "evidence_gaps": evidence_gaps[:16],
            "resolution_readiness": readiness,
        }
    )


def _workspace_with_submit_acceptance(workspace: CaseCognitiveWorkspace) -> CaseCognitiveWorkspace:
    closed_agenda = [
        item.model_copy(
            update={
                "status": "closed",
                "closed_reason": item.closed_reason or "Package resolution passed mechanical readiness checks.",
            }
        )
        if item.status == "open"
        else item
        for item in workspace.investigation_agenda
    ]
    return workspace.model_copy(
        update={
            "investigation_agenda": closed_agenda[:24],
            "resolution_readiness": ResolutionReadiness(
                status="ready",
                ready_work_units=[
                    str(unit.work_unit_id or unit.label)
                    for unit in workspace.active_work_units
                    if str(unit.work_unit_id or unit.label).strip()
                ][:24],
                blocking_work_units=[],
                mechanical_gaps=[],
                evidence_gaps=[],
                summary="Package resolution passed mechanical readiness checks.",
            )
        }
    )


def _note_tool(registry: LocatorRegistry, session: HumanCaseSession, args: NoteToolArgs) -> dict[str, object]:
    issues: list[dict[str, object]] = []
    for locator in args.locators:
        _, issue = registry.resolve(locator)
        if issue:
            issues.append(issue)
    workspace_update = args.cognitive_workspace
    if workspace_update is not None:
        issues.extend(_validate_workspace_update_locators(registry, workspace_update))
    if issues:
        return {"accepted": False, "issues": issues}
    note = {
        "claims": [str(item) for item in args.claims if str(item).strip()][:12],
        "locators": [str(item) for item in args.locators if str(item).strip()][:24],
        "reason": args.reason,
    }
    session.notes.append(note)
    workspace_changed = False
    if workspace_update is not None:
        before = _cognitive_signature(session.cognitive_workspace)
        session.cognitive_workspace = workspace_update
        workspace_changed = before != _cognitive_signature(session.cognitive_workspace)
    return {
        "accepted": True,
        "note_count": len(session.notes),
        "recorded": note,
        "cognitive_workspace_changed": workspace_changed,
        "cognitive_workspace": _compact_cognitive_workspace(session.cognitive_workspace),
    }


def _issue(ref: str, code: str, message: str, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(ref=ref, issue_code=code, severity="blocked", message=message, related_refs=list(related_refs or []))


def _file_refs_for_locators(
    registry: LocatorRegistry,
    locators: list[str],
) -> tuple[list[str], list[dict[str, object]], list[str]]:
    refs: list[str] = []
    issues: list[dict[str, object]] = []
    canonical: list[str] = []
    for raw in locators:
        locator, issue = registry.resolve(raw)
        if issue:
            candidates = _candidate_local_locators_for_raw_text(registry, raw)
            if candidates:
                issue = {**issue, "candidate_local_locators": candidates}
            issues.append(issue)
            continue
        if locator is None or locator.kind != "local":
            issues.append(
                {
                    "issue": "locator_scope_not_available",
                    "locator": raw,
                    "actual_kind": getattr(locator, "kind", "") if locator is not None else "",
                    "contract_role": getattr(locator, "contract_role", "") if locator is not None else "",
                    "expected": "local:// must_account locator",
                    "repair_instruction": (
                        "Do not create submit.work_units for support_only locators. "
                        "Use support_only locators only in work_units[].support when they help justify a must_account local locator, "
                        "or omit them if they do not change the resolution."
                    ),
                }
            )
            continue
        refs.extend(locator.file_refs)
        canonical.append(locator.locator)
    return refs, issues, canonical


def _normalized_locator_match_text(value: str) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\.[a-z0-9]{1,6}$", " ", text)
    text = TECH_TOKEN_RE.sub(" ", text)
    text = re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _candidate_local_locators_for_raw_text(
    registry: LocatorRegistry,
    raw: str,
    *,
    limit: int = 6,
) -> list[dict[str, object]]:
    raw_text = str(raw or "").strip()
    if not raw_text:
        return []
    raw_basename = _basename(raw_text)
    raw_fold = raw_text.casefold()
    basename_fold = raw_basename.casefold()
    raw_norm = _normalized_locator_match_text(raw_text)
    basename_norm = _normalized_locator_match_text(raw_basename)
    scored: list[tuple[int, str, AgentLocator, str]] = []
    for locator in registry.locators.values():
        if locator.kind != "local":
            continue
        best_score = 0
        best_reason = ""
        locator_norm = _normalized_locator_match_text(locator.locator.removeprefix("local://"))
        raw_locator_norm = _normalized_locator_match_text(raw_text.removeprefix("local://"))
        if raw_locator_norm and locator_norm:
            raw_tokens = {token for token in raw_locator_norm.split() if token not in {"local"}}
            locator_tokens = {token for token in locator_norm.split() if token not in {"local"}}
            if raw_locator_norm == locator_norm:
                best_score, best_reason = 100, "locator_exact"
            elif len(raw_locator_norm) >= 12 and (raw_locator_norm in locator_norm or locator_norm in raw_locator_norm):
                best_score, best_reason = 85, "locator_substring"
            elif len(raw_tokens) >= 3 and raw_tokens.issubset(locator_tokens):
                best_score, best_reason = 65, "locator_token_subset"
        for label in locator.representative_labels:
            label_text = str(label or "").strip()
            if not label_text:
                continue
            label_fold = label_text.casefold()
            label_norm = _normalized_locator_match_text(label_text)
            if raw_fold == label_fold or basename_fold == label_fold:
                best_score, best_reason = max((best_score, best_reason), (100, "representative_label_exact"))
            elif len(label_fold) >= 12 and (label_fold in raw_fold or basename_fold in label_fold):
                best_score, best_reason = max((best_score, best_reason), (80, "representative_label_substring"))
            elif (
                len(label_norm) >= 12
                and len(raw_norm) >= 12
                and (label_norm in raw_norm or raw_norm in label_norm or label_norm in basename_norm or basename_norm in label_norm)
            ):
                best_score, best_reason = max((best_score, best_reason), (60, "representative_label_normalized"))
        title_norm = _normalized_locator_match_text(locator.title)
        if best_score < 40 and title_norm and len(title_norm) >= 12 and title_norm in raw_norm:
            best_score, best_reason = 40, "title_normalized"
        if best_score:
            scored.append((best_score, locator.locator, locator, best_reason))
    scored.sort(key=lambda item: (-item[0], len(item[2].file_refs), item[1]))
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for score, _key, locator, reason in scored:
        if locator.locator in seen:
            continue
        seen.add(locator.locator)
        rows.append(
            {
                "locator": locator.locator,
                "file_count": len(locator.file_refs),
                "representative_labels": list(locator.representative_labels[:4]),
                "match_reason": reason,
                "match_score": score,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _candidate_target_locators_for_raw_text(
    registry: LocatorRegistry,
    raw: str,
    *,
    limit: int = 6,
) -> list[dict[str, object]]:
    raw_text = str(raw or "").strip()
    if not raw_text:
        return []
    raw_norm = _normalized_locator_match_text(raw_text)
    raw_tokens = {
        token
        for token in raw_norm.split()
        if token not in {"target", "bangumi", "episode", "episodes"}
    }
    if not raw_tokens:
        return []
    scored: list[tuple[int, str, AgentLocator, list[str], list[str]]] = []
    for locator in registry.locators.values():
        if locator.kind not in {"target_subject", "target_episode", "target_span"}:
            continue
        subject = _target_subject_locator_for(registry, locator)
        visible_tokens = (
            _target_visible_title_tokens(locator)
            .union(_target_visible_title_tokens(subject))
            .union(_target_query_distinctive_tokens(locator))
            .union(_target_query_distinctive_tokens(subject))
        )
        shared_tokens = sorted(raw_tokens.intersection(visible_tokens))
        if not shared_tokens:
            continue
        query_texts = list(dict.fromkeys([*locator.query_markers, *subject.query_markers]))[:6]
        query_overlap = [
            query_text
            for query_text in query_texts
            for query_norm in [_normalized_locator_match_text(query_text)]
            if query_norm and (query_norm in raw_norm or raw_norm in query_norm)
        ]
        locator_norm = _normalized_locator_match_text(locator.locator)
        title_norm = _normalized_locator_match_text(locator.title)
        score = len(shared_tokens) * 20
        if query_overlap:
            score += 120
        if title_norm and (title_norm in raw_norm or raw_norm in title_norm):
            score += 80
        if locator_norm and raw_norm in locator_norm:
            score += 60
        if locator.kind == "target_subject":
            score += 10
        scored.append((score, locator.locator, locator, shared_tokens, query_overlap))
    scored.sort(key=lambda item: (-item[0], item[1]))
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for score, _key, locator, shared_tokens, query_overlap in scored:
        subject = _target_subject_locator_for(registry, locator)
        candidate_locator = subject.locator if locator.kind != "target_subject" else locator.locator
        if candidate_locator.casefold() in seen:
            continue
        seen.add(candidate_locator.casefold())
        target_numbers = _target_episode_numbers_for_subject(registry, int(subject.subject_id or 0))
        rows.append(
            {
                "target": candidate_locator,
                "title": subject.title,
                "subject_id": subject.subject_id,
                "shared_visible_tokens": shared_tokens[:8],
                "matched_source_query_texts": query_overlap[:4],
                "candidate_episode_locators": [
                    f"{candidate_locator}/episode/{int(number)}" for number in target_numbers[:6]
                ],
                "score": score,
                "repair_note": (
                    "Visible target candidate for the rejected raw target text. "
                    "The fixed layer is not choosing it; resubmit only if it is your intended target."
                ),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _validate_support_locators(
    registry: LocatorRegistry,
    locators: list[str],
) -> tuple[list[dict[str, object]], list[str]]:
    issues: list[dict[str, object]] = []
    canonical: list[str] = []
    for raw in [str(item).strip() for item in locators if str(item).strip()]:
        locator, issue = registry.resolve(raw)
        if issue:
            candidates = _candidate_local_locators_for_raw_text(registry, raw)
            if candidates:
                issue = {**issue, "candidate_local_locators": candidates}
            issues.append(issue)
            continue
        if locator is None:
            issues.append({"issue": "locator_not_found", "locator": raw})
            continue
        canonical.append(locator.locator)
    return issues, canonical


_MEDIA_FORM_ALIASES: dict[str, tuple[str, ...]] = {
    "movie": (
        "movie",
        "film",
        "gekijouban",
        "gekijo ban",
        "theatrical",
        "theater",
        "theatre",
        "\u5287\u5834\u7248",
        "\u5267\u573a\u7248",
    ),
    "recap": (
        "recap",
        "compilation",
        "digest",
        "soushuuhen",
        "soshuuhen",
        "soushu hen",
        "\u7dcf\u96c6\u7de8",
        "\u7e3d\u96c6\u7bc7",
        "\u603b\u96c6\u7bc7",
    ),
    "ova": ("ova", "oav", "original video animation"),
    "oad": ("oad", "original animation dvd"),
    "special": ("special", "sp", "tokuten", "bonus", "\u7279\u5178", "\u756a\u5916"),
}


def _media_form_tokens(*parts: object) -> set[str]:
    raw = " ".join(str(part or "") for part in parts if str(part or "").strip()).casefold()
    if not raw:
        return set()
    normalized = _normalized_locator_match_text(raw)
    tokens: set[str] = set()
    for form, aliases in _MEDIA_FORM_ALIASES.items():
        for alias in aliases:
            alias_norm = _normalized_locator_match_text(alias)
            if not alias_norm:
                continue
            if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", alias_norm):
                if alias_norm in normalized:
                    tokens.add(form)
                    break
                continue
            if re.search(rf"(?:^|\s){re.escape(alias_norm)}(?:\s|$)", normalized):
                tokens.add(form)
                break
    return tokens


def _local_target_title_pairing_options(
    registry: LocatorRegistry,
    locators: list[str],
    *,
    limit: int = 8,
    demoted_targets: set[str] | None = None,
) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    scored_options: list[tuple[int, dict[str, object]]] = []
    demoted_target_keys = {str(item or "").casefold() for item in (demoted_targets or set()) if str(item or "").strip()}
    target_subjects = [
        locator
        for locator in registry.locators.values()
        if locator.kind == "target_subject"
        and int(locator.subject_id or 0)
        and int(locator.subject_eps or 0) <= 1
    ]
    if not target_subjects:
        return []
    generic_tokens = _TITLE_TAIL_GENERIC_TOKENS.union({"main", "episodes", "episode"})

    def is_demoted_target(subject: AgentLocator, target: str) -> bool:
        return subject.locator.casefold() in demoted_target_keys or str(target or "").casefold() in demoted_target_keys

    for raw in locators:
        locator, issue = registry.resolve(raw)
        if issue or locator is None or locator.kind != "local":
            continue
        triples = _episode_label_triples(locator)
        if not (2 <= len(triples) <= 4):
            continue
        for number, _ref, label in triples:
            local_tokens = _distinctive_tokens(label) - generic_tokens
            if len(local_tokens) < 2:
                continue
            tail_tokens = set()
            for match in re.finditer(r"\[([^\]]+)\]|\(([^\)]+)\)", str(label or "")):
                content = str(match.group(1) or match.group(2) or "")
                content = re.sub(r"(?i)^\s*\d{1,3}\s*[-_.:]?\s*", " ", content)
                content = re.sub(r"(?i)\b(?:\d{3,4}p|x26[45]|flac|aac|ac3|ma10p|hi10p)\b", " ", content)
                content = re.sub(r"\s+", " ", content).strip(" ._-/[](){}")
                if not content or QUERY_NOISE_TOKEN_RE.search(content):
                    continue
                tail_tokens.update(_distinctive_tokens(content) - generic_tokens)
            local_form_tokens = _media_form_tokens(locator.title, label)
            local_slice = f"{locator.locator}/episode/{int(number)}"
            for subject in target_subjects:
                title_tokens = _target_visible_title_tokens(subject) - generic_tokens
                query_tokens = _target_query_distinctive_tokens(subject) - generic_tokens
                shared_title = sorted(local_tokens.intersection(title_tokens))
                shared_query = sorted(local_tokens.intersection(query_tokens))
                tail_title_shared = sorted(tail_tokens.intersection(title_tokens))
                tail_query_shared = sorted(tail_tokens.intersection(query_tokens))
                target_form_tokens = _media_form_tokens(subject.locator, subject.title, " ".join(subject.markers[:8]))
                shared_form = sorted(local_form_tokens.intersection(target_form_tokens))
                has_title_bridge = bool(shared_title or tail_title_shared)
                has_query_tail_bridge = bool(shared_title and tail_tokens and tail_query_shared)
                has_form_family_bridge = bool(shared_title and shared_form)
                if tail_tokens and not (tail_title_shared or has_query_tail_bridge or has_form_family_bridge):
                    continue
                if not has_title_bridge:
                    continue
                if len(shared_title) < 2 and not tail_title_shared and not has_query_tail_bridge and not has_form_family_bridge:
                    continue
                target_numbers = _target_episode_numbers_for_subject(registry, int(subject.subject_id or 0))
                if len(target_numbers) == 1:
                    target = f"{subject.locator}/episode/{int(target_numbers[0])}"
                else:
                    target = subject.locator
                key = (local_slice, target)
                if key in seen:
                    continue
                seen.add(key)
                context_title_shared = sorted(set(shared_title) - set(tail_title_shared) - generic_tokens)
                weak_isolated_tail_hit = bool(tail_title_shared and len(tail_title_shared) < 2 and not context_title_shared)
                already_mapped_sibling_target = is_demoted_target(subject, target)
                score = (
                    len(context_title_shared) * 140
                    + len(shared_form) * 70
                    + len(tail_query_shared) * 30
                    + len(tail_title_shared) * 60
                    + len(shared_title) * 15
                    + len(shared_query) * 2
                )
                if weak_isolated_tail_hit:
                    score -= 160
                if already_mapped_sibling_target:
                    score -= 100
                scored_options.append(
                    (
                        score,
                        {
                            "local_slice": local_slice,
                            "target": target,
                            "target_subject": subject.locator,
                            "target_title": subject.title,
                            "local_label": label,
                            "shared_title_tokens": shared_title[:8],
                            "shared_context_title_tokens": context_title_shared[:8],
                            "shared_title_tail_tokens": tail_title_shared[:8],
                            "shared_source_query_tokens": shared_query[:8],
                            "shared_source_query_tail_tokens": tail_query_shared[:8],
                            "shared_media_form_tokens": shared_form[:6],
                            "target_source_query_texts": list(subject.query_markers[:4]),
                            "already_mapped_sibling_target": already_mapped_sibling_target,
                            "match_strength": (
                                "title"
                                if tail_title_shared
                                else ("title_plus_source_query" if tail_query_shared else "title_plus_media_form")
                            ),
                            "mechanical_note": (
                                "This is a title-token pairing candidate between one local numbered slice and one visible "
                                "single-item target. Source-query tokens are provenance only, not target title aliases. "
                                "The Agent must decide whether the semantic ownership is correct."
                            ),
                        },
                    )
                )
    scored_options.sort(key=lambda item: (-item[0], str(item[1].get("local_slice") or ""), str(item[1].get("target") or "")))
    for _score, option in scored_options[:limit]:
        options.append(option)
    return options


def _local_target_title_pairing_options_for_slice(
    registry: LocatorRegistry,
    local_locator: str,
    *,
    limit: int = 8,
    demoted_targets: set[str] | None = None,
) -> list[dict[str, object]]:
    """Expose visible one-item target pairings for a specific local slice.

    This is evidence-shape feedback only: it pairs title tokens between visible
    local slices and visible target subjects. The agent still decides whether
    any pairing is semantically correct.
    """

    local_locator = str(local_locator or "").strip()
    if not local_locator:
        return []
    parent = _episode_slice_parent_locator(local_locator)
    candidate_inputs = [parent] if parent and parent != local_locator else [local_locator]
    options = _local_target_title_pairing_options(
        registry,
        candidate_inputs,
        limit=limit * 2,
        demoted_targets=demoted_targets,
    )
    if parent and parent != local_locator:
        options = [
            item
            for item in options
            if str(item.get("local_slice") or "").casefold() == local_locator.casefold()
        ]
    return options[:limit]


def _local_slice_mapping_options_from_title_pairings(
    pairing_options: list[dict[str, object]],
    *,
    limit: int = 8,
) -> list[dict[str, object]]:
    """Expose legal split-submit shapes from title-pairing evidence."""

    options: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in pairing_options:
        if not isinstance(item, dict):
            continue
        local_slice = str(item.get("local_slice") or "").strip()
        target = str(item.get("target") or "").strip()
        if not local_slice or not target:
            continue
        key = (local_slice.casefold(), target.casefold())
        if key in seen:
            continue
        seen.add(key)
        options.append(
            {
                "local": local_slice,
                "target": target,
                "outcome": "mapped_explicit_item",
                "target_subject": item.get("target_subject"),
                "target_title": item.get("target_title"),
                "local_label": item.get("local_label"),
                "shared_title_tail_tokens": item.get("shared_title_tail_tokens") or [],
                "shared_source_query_tail_tokens": item.get("shared_source_query_tail_tokens") or [],
                "shared_media_form_tokens": item.get("shared_media_form_tokens") or [],
                "target_source_query_texts": item.get("target_source_query_texts") or [],
                "already_mapped_sibling_target": bool(item.get("already_mapped_sibling_target")),
                "required": (
                    "This local://.../episode/N slice is a legal visible local locator. "
                    "If you judge the pairing semantically correct, submit it as a separate work unit; "
                    "the fixed layer will verify coverage/duplicates."
                ),
            }
        )
        if len(options) >= limit:
            break
    return options


def _clean_search_query_seed(value: object) -> str:
    text = re.sub(r"[_/]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text.replace("-", " ")).strip()
    text = re.sub(
        r"(?i)\b(?:main\s+episodes?|special\s+marker|packaging\s+extras?|main)\b",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _continuation_surface_miss_feedback(
    registry: LocatorRegistry,
    target_locator: AgentLocator,
    *,
    local_locators: list[str],
    requested_start: int | None,
    requested_end: int | None,
    available_episode_numbers: list[int],
    limit: int = 8,
) -> dict[str, object]:
    visible_numbers = sorted({int(num) for num in available_episode_numbers if int(num or 0) > 0})
    if not visible_numbers or requested_start is None:
        return {}
    visible_max = max(visible_numbers)
    if int(requested_start) <= visible_max:
        return {}
    subject_locator = registry.locators.get(registry.subject_locator_by_id.get(int(target_locator.subject_id or 0), "") or "")
    if subject_locator is None:
        return {}
    resolved_local: list[AgentLocator] = []
    for raw in local_locators:
        locator, issue = registry.resolve(raw)
        if issue is None and locator is not None and locator.kind == "local":
            resolved_local.append(locator)
    if not resolved_local:
        return {}
    target_tokens = _target_distinctive_tokens(subject_locator).union(_target_query_distinctive_tokens(subject_locator))
    local_tokens: set[str] = set()
    for locator in resolved_local:
        local_tokens.update(_locator_distinctive_tokens(locator))
    shared_tokens = sorted(target_tokens.intersection(local_tokens))
    if len(shared_tokens) < 2 and not any(len(token) >= 6 for token in shared_tokens):
        return {}

    seed_candidates: list[str] = []
    for marker in list(subject_locator.query_markers):
        seed_candidates.append(str(marker))
    for locator in resolved_local:
        seed_candidates.append(locator.title)
        seed_candidates.extend(str(marker) for marker in list(locator.markers[:4]))
    seed_candidates.append(subject_locator.title)
    seed_candidates.extend(str(marker) for marker in list(subject_locator.markers[:4]))

    shared_set = set(shared_tokens)

    def seed_score(value: str) -> tuple[int, int, int]:
        tokens = _distinctive_tokens(value)
        return (
            len(tokens.intersection(shared_set)),
            len(tokens.intersection(_target_query_distinctive_tokens(subject_locator))),
            len(value),
        )

    cleaned_seeds = [
        seed
        for seed in [_clean_search_query_seed(item) for item in seed_candidates]
        if seed and len(seed) >= 3
    ]
    if not cleaned_seeds:
        return {}
    base_query = max(cleaned_seeds, key=seed_score)
    requested_range = (
        str(int(requested_start))
        if requested_end is None or int(requested_end or requested_start) == int(requested_start)
        else f"{int(requested_start)}-{int(requested_end)}"
    )
    query_candidates = [
        f"{base_query} 2",
        f"{base_query} part 2",
        f"{base_query} second season",
        f"{base_query} 2nd season",
        f"{base_query} second cour",
        f"{base_query} cour 2",
        f"{base_query} {requested_range}",
    ]
    queries: list[str] = []
    seen: set[str] = set()
    for query in query_candidates:
        cleaned = re.sub(r"\s+", " ", query).strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        queries.append(cleaned)
        if len(queries) >= limit:
            break
    if not queries:
        return {}
    return {
        "search_queries_to_try": queries,
        "continuation_evidence_hint": {
            "issue": "visible_target_surface_ends_before_requested_range",
            "target_subject": subject_locator.locator,
            "target_title": subject_locator.title,
            "visible_regular_episode_max": visible_max,
            "requested_episode_start": int(requested_start),
            "requested_episode_end": int(requested_end or requested_start),
            "shared_title_family_tokens": shared_tokens[:12],
            "local_title_evidence": [
                {
                    "locator": locator.locator,
                    "title": locator.title,
                    "episode_start": locator.episode_start,
                    "episode_end": locator.episode_end,
                }
                for locator in resolved_local[:4]
            ],
            "required": (
                "The selected target surface is visible but ends before this requested same-title-family range. "
                "Search or inspect title-preserving continuation/part/cour evidence before retrying the same range "
                "or concluding target_absent/fail_closed. This hint does not choose a target."
            ),
        },
    }


def _target_items_for_unit(
    registry: LocatorRegistry,
    unit: ResolutionWorkUnit,
    *,
    local_file_count: int = 0,
    local_locators: list[str] | None = None,
) -> tuple[list[str], list[dict[str, object]], str]:
    local_locators = list(local_locators or [])
    target = str(unit.target or "").strip()
    if not target:
        return [], [{"issue": "target_required", "unit_label": unit.unit_label}], ""
    locator, issue = registry.resolve(target)
    if issue:
        target_candidates = _candidate_target_locators_for_raw_text(registry, target)
        if target_candidates:
            issue = {**issue, "candidate_target_locators": target_candidates}
        return [], [issue], ""
    if locator is None:
        return [], [{"issue": "locator_not_found", "locator": target}], ""
    subject_locator = registry.subject_locator_by_id.get(locator.subject_id, "")
    subject_episode_numbers = _target_episode_numbers_for_subject(registry, locator.subject_id)
    target_surface_visible = bool(subject_episode_numbers)
    pairing_options = _local_target_title_pairing_options(registry, local_locators)
    local_slice_mapping_options = _local_slice_mapping_options_from_title_pairings(pairing_options)
    target_missing_action = (
        (
            "The requested episode number/range is not present in the already visible target episode surface. "
            "Do not resubmit the same target/range; switch to a different visible target, split the local locator, "
            "or choose target_absent/supplemental/non_bangumi/fail_closed according to your semantic judgment."
        )
        if target_surface_visible
        else f'inspect(["{subject_locator}"], scope=["details","episodes","related"])'
    )
    subject_level_singleton_ok = (
        int(local_file_count or 0) == 1
        and bool(locator.subject_ref)
        and unit.outcome.startswith("mapped_")
        and int(getattr(locator, "subject_eps", 0) or 0) <= 1
        and not subject_episode_numbers
    )
    singleton_visible_item: tuple[list[str], str] | None = None
    if (
        int(local_file_count or 0) == 1
        and unit.outcome.startswith("mapped_")
        and _target_subject_eps(registry, locator) <= 1
        and len(subject_episode_numbers) == 1
    ):
        single_sort = int(subject_episode_numbers[0])
        single_ref = registry.item_ref_by_subject_sort.get((locator.subject_id, single_sort), "")
        if single_ref:
            singleton_visible_item = ([single_ref], f"{subject_locator}/episode/{single_sort}")
    if locator.kind == "target_episode":
        zero_mismatch = _target_zero_episode_mismatch(
            registry,
            local_locators=local_locators,
            target=target,
            episode_start=locator.episode_start,
            episode_end=locator.episode_end,
        )
        if zero_mismatch:
            return [], [zero_mismatch], locator.locator
        if not locator.item_refs:
            if singleton_visible_item is not None:
                return singleton_visible_item[0], [], singleton_visible_item[1]
            if subject_level_singleton_ok:
                return [locator.subject_ref], [], subject_locator
            return [], [
                {
                    "issue": "target_episode_surface_missing",
                    "target": target,
                    "episode_start": locator.episode_start,
                    "episode_end": locator.episode_end,
                    "available_target_episode_numbers": subject_episode_numbers[:64],
                    "target_surface_visible": target_surface_visible,
                    "visible_alternate_subjects": _visible_subject_candidates_for_feedback(
                        registry,
                        current_subject_id=locator.subject_id,
                        local_count=local_file_count,
                    ),
                    "local_target_title_pairing_options": pairing_options,
                    "local_slice_mapping_options": local_slice_mapping_options,
                    "available_action": target_missing_action,
                    **_continuation_surface_miss_feedback(
                        registry,
                        locator,
                        local_locators=local_locators,
                        requested_start=locator.episode_start,
                        requested_end=locator.episode_end,
                        available_episode_numbers=subject_episode_numbers,
                    ),
                }
            ], locator.locator
        return list(locator.item_refs), [], locator.locator
    if locator.kind == "target_span":
        zero_mismatch = _target_zero_episode_mismatch(
            registry,
            local_locators=local_locators,
            target=target,
            episode_start=locator.episode_start,
            episode_end=locator.episode_end,
        )
        if zero_mismatch:
            return [], [zero_mismatch], locator.locator
        expected = 0
        if locator.episode_start is not None and locator.episode_end is not None:
            expected = int(locator.episode_end) - int(locator.episode_start) + 1
        if expected and len(locator.item_refs) != expected:
            if singleton_visible_item is not None:
                return singleton_visible_item[0], [], singleton_visible_item[1]
            if subject_level_singleton_ok:
                return [locator.subject_ref], [], subject_locator
            return [], [
                {
                    "issue": "target_episode_surface_missing",
                    "target": target,
                    "episode_start": locator.episode_start,
                    "episode_end": locator.episode_end,
                    "target_count": len(locator.item_refs),
                    "expected_target_count": expected,
                    "available_target_episode_numbers": subject_episode_numbers[:64],
                    "target_surface_visible": target_surface_visible,
                    "visible_alternate_subjects": _visible_subject_candidates_for_feedback(
                        registry,
                        current_subject_id=locator.subject_id,
                        local_count=local_file_count,
                    ),
                    "local_target_title_pairing_options": pairing_options,
                    "local_slice_mapping_options": local_slice_mapping_options,
                    "available_action": target_missing_action,
                    **_continuation_surface_miss_feedback(
                        registry,
                        locator,
                        local_locators=local_locators,
                        requested_start=locator.episode_start,
                        requested_end=locator.episode_end,
                        available_episode_numbers=subject_episode_numbers,
                    ),
                }
            ], locator.locator
        return list(locator.item_refs), [], locator.locator
    if locator.kind == "target_subject":
        start = unit.episode_start
        end = unit.episode_end if unit.episode_end is not None else start
        if start is None or end is None:
            if singleton_visible_item is not None:
                return singleton_visible_item[0], [], singleton_visible_item[1]
            if subject_level_singleton_ok:
                return [locator.subject_ref], [], subject_locator
            subject_episode_numbers = _target_episode_numbers_for_subject(registry, locator.subject_id)
            return [], [
                {
                    "issue": "episode_range_required",
                    "target": target,
                    "unit_label": unit.unit_label,
                    "available_target_episode_numbers": subject_episode_numbers[:64],
                    "target_episode_locator_samples": _target_episode_locator_samples_for_subject(registry, locator.subject_id),
                    "target_span_examples": _target_span_examples_for_subject(registry, locator.subject_id),
                    "target_surface_visible": bool(subject_episode_numbers),
                    "visible_alternate_subjects": _visible_subject_candidates_for_feedback(
                        registry,
                        current_subject_id=locator.subject_id,
                        local_count=local_file_count,
                    ),
                    "local_target_title_pairing_options": pairing_options,
                    "local_slice_mapping_options": local_slice_mapping_options,
                    "available_action": (
                        f'inspect(["{subject_locator}"], scope=["details","episodes","related"])'
                        if subject_locator and not subject_episode_numbers
                        else ""
                    ),
                    "repair_instruction": (
                        "Use either episode_start/episode_end with this subject target, or a concrete "
                        "target://.../episode/N or target://.../episodes/A-B locator."
                    ),
                }
            ], subject_locator
        item_refs = [
            ref
            for sort in range(int(start), int(end) + 1)
            for ref in [registry.item_ref_by_subject_sort.get((locator.subject_id, sort), "")]
            if ref
        ]
        zero_mismatch = _target_zero_episode_mismatch(
            registry,
            local_locators=local_locators,
            target=target,
            episode_start=int(start),
            episode_end=int(end),
        )
        if zero_mismatch:
            return [], [zero_mismatch], subject_locator
        if len(item_refs) != int(end) - int(start) + 1:
            if singleton_visible_item is not None:
                return singleton_visible_item[0], [], singleton_visible_item[1]
            if subject_level_singleton_ok:
                return [locator.subject_ref], [], subject_locator
            return [], [
                {
                    "issue": "target_episode_surface_missing",
                    "target": target,
                    "episode_start": start,
                    "episode_end": end,
                    "target_count": len(item_refs),
                    "expected_target_count": int(end) - int(start) + 1,
                    "available_target_episode_numbers": subject_episode_numbers[:64],
                    "target_surface_visible": target_surface_visible,
                    "visible_alternate_subjects": _visible_subject_candidates_for_feedback(
                        registry,
                        current_subject_id=locator.subject_id,
                        local_count=local_file_count,
                    ),
                    "local_target_title_pairing_options": pairing_options,
                    "local_slice_mapping_options": local_slice_mapping_options,
                    "available_action": target_missing_action,
                    **_continuation_surface_miss_feedback(
                        registry,
                        locator,
                        local_locators=local_locators,
                        requested_start=int(start),
                        requested_end=int(end),
                        available_episode_numbers=subject_episode_numbers,
                    ),
                }
            ], subject_locator
        span_locator = f"{subject_locator}/episodes/{int(start)}-{int(end)}"
        return item_refs, [], span_locator
    return [], [{"issue": "target_locator_wrong_kind", "target": target}], ""


def _local_locator_feedback(registry: LocatorRegistry, locators: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in locators:
        locator, issue = registry.resolve(raw)
        if issue or locator is None:
            rows.append({"locator": raw, "issue": issue or {"issue": "locator_not_found", "locator": raw}})
            continue
        if locator.kind != "local":
            rows.append({"locator": raw, "kind": locator.kind})
            continue
        row: dict[str, object] = {
            "locator": locator.locator,
            "file_count": len(locator.file_refs),
            "episode_range": (
                _range_summary_from_episode_pairs(locator.episode_file_refs)
                if locator.episode_file_refs
                else _range_summary(list(locator.representative_labels))
            ),
            "representative_labels": list(locator.representative_labels[:4]),
        }
        row.update(_episode_locator_hints(locator.locator, locator.episode_file_refs))
        rows.append(row)
    return rows


def _local_episode_split_options_from_feedback(
    details: list[dict[str, object]],
    *,
    limit: int = 8,
) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    seen: set[str] = set()
    for detail in details:
        if not isinstance(detail, dict):
            continue
        raw_options = [
            *list(detail.get("common_split_examples") or []),
            *list(detail.get("suggested_episode_slices") or []),
            *list(detail.get("episode_locators") or []),
        ]
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            locator = str(option.get("locator") or "").strip()
            if not locator or locator in seen:
                continue
            seen.add(locator)
            options.append(option)
            if len(options) >= limit:
                return options
    return options


def _local_target_count_pairing_options(
    registry: LocatorRegistry,
    locators: list[str],
    *,
    target_count: int,
    limit: int = 8,
) -> list[dict[str, object]]:
    if target_count <= 0:
        return []
    options: list[dict[str, object]] = []
    for raw in locators:
        locator, issue = registry.resolve(raw)
        if issue or locator is None or locator.kind != "local":
            continue
        triples = _episode_label_triples(locator)
        if len(triples) <= target_count:
            continue
        ordered_numbers = sorted({int(num) for num, _ref, _label in triples})
        if len(ordered_numbers) <= target_count:
            continue
        windows: list[list[int]] = []
        windows.append(ordered_numbers[:target_count])
        windows.append(ordered_numbers[-target_count:])
        if int(locator.episode_start or 0) and int(locator.episode_end or 0):
            natural = [
                num
                for num in ordered_numbers
                if int(locator.episode_start or 0) <= num <= int(locator.episode_start or 0) + target_count - 1
            ]
            if len(natural) == target_count:
                windows.append(natural)
        seen: set[tuple[int, ...]] = set()
        for window in windows:
            key = tuple(window)
            if key in seen:
                continue
            seen.add(key)
            matched_refs = {ref for num, ref, _label in triples if int(num) in set(window)}
            leftover_refs = {ref for num, ref, _label in triples if int(num) not in set(window)}
            if not matched_refs or not leftover_refs:
                continue
            mapped_slices = _episode_slice_locators(locator, matched_refs)
            leftover_slices = _episode_slice_locators(locator, leftover_refs)
            if not mapped_slices or not leftover_slices:
                continue
            options.append(
                {
                    "matching_local_slice": mapped_slices[0],
                    "leftover_local_slices": leftover_slices[:4],
                    "target_count": target_count,
                    "mechanical_note": (
                        "This is only a count-compatible local split option. The Agent must decide which slice maps "
                        "and what semantic outcome applies to leftovers."
                    ),
                }
            )
            if len(options) >= limit:
                return options
    return options[:limit]


def _split_option_rows_from_count_pairings(pairing_options: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()

    def add(value: object, *, role: str) -> None:
        if not isinstance(value, dict):
            return
        locator = str(value.get("locator") or "").strip()
        if not locator or locator in seen:
            return
        seen.add(locator)
        rows.append({**value, "split_role": role})

    for option in pairing_options:
        if not isinstance(option, dict):
            continue
        add(option.get("matching_local_slice"), role="count_compatible_target_owner_candidate")
        for leftover in list(option.get("leftover_local_slices") or []):
            add(leftover, role="leftover_slice_needing_own_outcome")
    return rows


def _search_queries_from_local_split_options(
    split_options: list[dict[str, object]],
    *,
    limit: int = 8,
) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = _work_unit_query_base(value)
        if not _useful_query_hint(value):
            return
        folded = value.casefold()
        if folded in seen:
            return
        seen.add(folded)
        queries.append(value)

    for option in split_options:
        if not isinstance(option, dict):
            continue
        labels = [
            str(label)
            for label in list(option.get("representative_labels") or [])
            if str(label).strip()
        ]
        local_label = str(option.get("local_label") or "").strip()
        if local_label:
            labels.append(local_label)
        for query in _work_unit_query_hints("", labels, limit=4):
            add(query)
            if len(queries) >= limit:
                return queries
    return queries[:limit]


def _split_first_repair_feedback(
    *,
    target_count: int,
    local_episode_split_options: list[dict[str, object]],
    local_target_count_pairing_options: list[dict[str, object]],
) -> dict[str, object]:
    split_rows = [
        *local_episode_split_options,
        *_split_option_rows_from_count_pairings(local_target_count_pairing_options),
    ]
    compact_rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in split_rows:
        if not isinstance(row, dict):
            continue
        locator = str(row.get("locator") or "").strip()
        if not locator or locator in seen:
            continue
        seen.add(locator)
        compact_rows.append(
            {
                key: row.get(key)
                for key in (
                    "locator",
                    "split_role",
                    "episode_start",
                    "episode_end",
                    "episode_number",
                    "episode_numbers",
                    "file_count",
                    "representative_labels",
                )
                if key in row
            }
        )
        if len(compact_rows) >= 8:
            break
    if not compact_rows:
        return {}
    return {
        "required": (
            "The selected target has fewer visible target items than the local locator. "
            "First split the local side with these visible local:// episode locators, then give each slice "
            "its own mapped/excluded/fail_closed outcome by semantic judgment."
        ),
        "target_count": int(target_count or 0),
        "legal_local_split_locators": compact_rows,
        "fixed_layer_boundary": (
            "This exposes legal split locators and title-derived search hints only; it does not choose a target "
            "or decide whether a split is semantically correct."
        ),
    }


def _contiguous_ranges(numbers: list[int]) -> list[tuple[int, int]]:
    if not numbers:
        return []
    ranges: list[tuple[int, int]] = []
    start = prev = numbers[0]
    for value in numbers[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append((start, prev))
        start = prev = value
    ranges.append((start, prev))
    return ranges


def _episode_slice_locators(locator: AgentLocator, refs: set[str]) -> list[dict[str, object]]:
    triples = _episode_label_triples(locator)
    numbers = sorted({int(num) for num, ref, _label in triples if ref in refs})
    slices: list[dict[str, object]] = []
    for start, end in _contiguous_ranges(numbers):
        matched = [(num, ref, label) for num, ref, label in triples if start <= int(num) <= end and ref in refs]
        if not matched:
            continue
        slice_locator = f"{locator.locator}/episode/{start}" if start == end else f"{locator.locator}/episodes/{start}-{end}"
        slices.append(
            {
                "locator": slice_locator,
                "episode_start": start,
                "episode_end": end,
                "file_count": len(matched),
                "representative_labels": [label for _num, _ref, label in matched[:4]],
            }
        )
    return slices


def _local_locator_hints_for_refs(registry: LocatorRegistry, refs: list[str], *, limit: int = 12) -> list[dict[str, object]]:
    refset = {str(ref) for ref in refs if str(ref)}
    if not refset:
        return []
    hints: list[dict[str, object]] = []
    seen: set[str] = set()
    local_locators = [locator for locator in registry.locators.values() if locator.kind == "local"]
    local_locators.sort(key=lambda item: (len(item.file_refs), item.locator))
    for locator in local_locators:
        matched_refs = [ref for ref in locator.file_refs if ref in refset]
        if not matched_refs:
            continue
        key = locator.locator
        if key in seen:
            continue
        seen.add(key)
        row: dict[str, object] = {
            "locator": locator.locator,
            "contract_role": locator.contract_role,
            "matched_file_count": len(matched_refs),
            "locator_file_count": len(locator.file_refs),
            "representative_labels": list(locator.representative_labels[:4]),
        }
        if locator.episode_file_refs:
            row["suggested_episode_slices"] = _episode_slice_locators(locator, refset)[:6]
            row.update(_episode_locator_hints(locator.locator, locator.episode_file_refs))
        hints.append(row)
        if len(hints) >= limit:
            break
    return hints


def _missing_work_unit_repairs(missing_locator_hints: list[dict[str, object]]) -> list[dict[str, object]]:
    repairs: list[dict[str, object]] = []
    for item in missing_locator_hints:
        if not isinstance(item, dict):
            continue
        locator = str(item.get("locator") or "").strip()
        if not locator:
            continue
        repairs.append(
            {
                "local": [locator],
                "file_count": int(item.get("matched_file_count") or item.get("locator_file_count") or 0),
                "required": "include this local locator exactly once in the next submit resolution",
                "choose_outcome": [
                    "mapped_regular_span",
                    "mapped_explicit_item",
                    "mapped_special_or_ova",
                    "mapped_composite_feature",
                    "bangumi_target_absent",
                    "supplemental",
                    "non_bangumi",
                    "fail_closed",
                ],
                "suggested_episode_slices": item.get("suggested_episode_slices") or [],
            }
        )
    return repairs


def _target_item_locator_for_ref(registry: LocatorRegistry, target_ref: str) -> str:
    for locator in registry.locators.values():
        if locator.kind == "target_episode" and target_ref in locator.item_refs:
            return locator.locator
    return ""


def _target_episode_numbers_for_subject(registry: LocatorRegistry, subject_id: int) -> list[int]:
    return sorted(
        {
            int(sort)
            for sid, sort in registry.item_ref_by_subject_sort
            if int(sid) == int(subject_id)
        }
    )


def _local_episode_numbers_for_locators(registry: LocatorRegistry, locators: list[str]) -> list[int]:
    numbers: list[int] = []
    for raw in locators:
        locator, issue = registry.resolve(raw)
        if issue or locator is None or locator.kind != "local":
            continue
        numbers.extend(int(number) for number, _ref in locator.episode_file_refs)
    return sorted(dict.fromkeys(numbers))


def _target_zero_episode_mismatch(
    registry: LocatorRegistry,
    *,
    local_locators: list[str],
    target: str,
    episode_start: int | None,
    episode_end: int | None,
) -> dict[str, object] | None:
    if int(episode_start or 0) != 0:
        return None
    local_numbers = _local_episode_numbers_for_locators(registry, local_locators)
    if not local_numbers or 0 in local_numbers:
        return None
    return {
        "issue": "target_episode_zero_mismatch",
        "target": target,
        "episode_start": episode_start,
        "episode_end": episode_end,
        "local": local_locators,
        "local_episode_numbers": local_numbers[:24],
        "required": (
            "The local locator has positive episode numbers but the selected target range starts at episode 0. "
            "Use the target range whose episode numbers align with the local locator, split the local locator, "
            "or choose a different semantic outcome. The fixed layer is checking numeric alignment only."
        ),
    }


def _inspected_subject_ids_from_workspace(workspace: CaseEvidenceWorkspace) -> set[int]:
    seen_refs = {str(ref) for ref in workspace.seen_detail_refs}
    inspected: set[int] = set()
    subject_id_by_ref = {
        str(subject.ref or ""): int(subject.subject_id or 0)
        for subject in workspace.bangumi_subjects
        if int(subject.subject_id or 0)
    }
    for subject in workspace.bangumi_subjects:
        if str(subject.ref or "") in seen_refs and int(subject.subject_id or 0):
            inspected.add(int(subject.subject_id or 0))
    for item in workspace.bangumi_items:
        if str(item.ref or "") not in seen_refs:
            continue
        subject_id = subject_id_by_ref.get(str(item.subject_ref or ""), 0)
        if subject_id:
            inspected.add(subject_id)
    return inspected


def _target_episode_locator_samples_for_subject(
    registry: LocatorRegistry,
    subject_id: int,
    *,
    limit: int = 8,
) -> list[str]:
    subject_locator = registry.subject_locator_by_id.get(int(subject_id), "")
    if not subject_locator:
        return []
    return [
        f"{subject_locator}/episode/{sort}"
        for sort in _target_episode_numbers_for_subject(registry, subject_id)[:limit]
    ]


def _single_file_target_item_options(
    registry: LocatorRegistry,
    *,
    target: str,
    local_locators: list[str],
    local_count: int,
    limit: int = 8,
) -> list[dict[str, object]]:
    if int(local_count or 0) != 1:
        return []
    target_locator, target_issue = registry.resolve(target)
    if target_issue or target_locator is None or not target_locator.subject_id:
        return []
    local_facts = _local_locator_feedback(registry, local_locators)
    subject_id = int(target_locator.subject_id or 0)
    rows: list[dict[str, object]] = []
    for episode_number in _target_episode_numbers_for_subject(registry, subject_id)[:limit]:
        item_ref = registry.item_ref_by_subject_sort.get((subject_id, int(episode_number)), "")
        item_locator = _target_item_locator_for_ref(registry, item_ref) if item_ref else ""
        if not item_locator:
            continue
        item_agent_locator = registry.locators.get(item_locator)
        rows.append(
            {
                "target": item_locator,
                "episode_number": int(episode_number),
                "target_title": getattr(item_agent_locator, "title", "") if item_agent_locator is not None else "",
                "target_title_aliases": list(getattr(item_agent_locator, "markers", ()) or ())[:6]
                if item_agent_locator is not None
                else [],
                "local_facts": local_facts,
                "submit_shape": {
                    "local": local_locators,
                    "target": item_locator,
                    "outcome": "mapped_explicit_item or mapped_special_or_ova if this single item is the semantic owner",
                },
                "mechanical_note": (
                    "The local side is one file while the selected target has multiple visible items. "
                    "Use one item locator only if your semantic judgment says the local file is that item; "
                    "use mapped_composite_feature only if the local file covers multiple target items."
                ),
            }
        )
    return rows


def _target_span_examples_for_subject(
    registry: LocatorRegistry,
    subject_id: int,
    *,
    max_spans: int = 6,
) -> list[dict[str, object]]:
    subject_locator = registry.subject_locator_by_id.get(int(subject_id), "")
    if not subject_locator:
        return []
    examples: list[dict[str, object]] = []
    for start, end in _contiguous_ranges(_target_episode_numbers_for_subject(registry, subject_id)):
        examples.append(
            {
                "locator": f"{subject_locator}/episode/{start}" if start == end else f"{subject_locator}/episodes/{start}-{end}",
                "episode_start": start,
                "episode_end": end,
                "target_count": end - start + 1,
            }
        )
        if len(examples) >= max_spans:
            break
    return examples


def _visible_subject_candidates_for_feedback(
    registry: LocatorRegistry,
    *,
    current_subject_id: int = 0,
    local_count: int = 0,
    limit: int = 8,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[int] = set()
    for locator in registry.locators.values():
        if locator.kind != "target_subject":
            continue
        sid = int(locator.subject_id or 0)
        if not sid or sid in seen or (current_subject_id and sid == int(current_subject_id)):
            continue
        seen.add(sid)
        rows.append(
            {
                "target": locator.locator,
                "title": locator.title,
                "eps": int(locator.subject_eps or 0),
                "same_count_as_local": bool(local_count and int(locator.subject_eps or 0) == int(local_count)),
                "available_action": f'inspect(["{locator.locator}"], scope=["details","episodes","related"])',
            }
        )
    rows.sort(key=lambda row: (not bool(row.get("same_count_as_local")), str(row.get("title") or "")))
    return rows[:limit]


def _target_locator_feedback(registry: LocatorRegistry, target: str, target_refs: list[str]) -> dict[str, object]:
    locator, issue = registry.resolve(target)
    if issue:
        return {"target": target, "issue": issue}
    if locator is None:
        return {"target": target, "issue": {"issue": "locator_not_found", "locator": target}}
    return {
        "target": locator.locator,
        "kind": locator.kind,
        "title": locator.title,
        "title_aliases": list(locator.markers[:8]),
        "source_query_texts": list(locator.query_markers[:4]),
        "episode_start": locator.episode_start,
        "episode_end": locator.episode_end,
        "target_item_count": len(target_refs),
        "target_item_locators": [_target_item_locator_for_ref(registry, ref) for ref in target_refs[:12]],
        "available_target_episode_numbers": _target_episode_numbers_for_subject(registry, locator.subject_id)[:64],
    }


def _is_single_non_episode_feature_local(
    registry: LocatorRegistry,
    locators: list[str],
    *,
    local_file_count: int,
) -> bool:
    if int(local_file_count or 0) != 1:
        return False
    for raw in locators:
        locator, issue = registry.resolve(raw)
        if issue or locator is None or locator.kind != "local":
            return False
        if locator.episode_file_refs:
            return False
        feature_tokens = _media_form_tokens(
            locator.title,
            " ".join(locator.markers),
            " ".join(locator.representative_labels[:4]),
        )
        feature_text = _normalized_locator_match_text(
            " ".join([locator.title, " ".join(locator.markers), " ".join(locator.representative_labels[:4])])
        )
        if not (
            feature_tokens.intersection({"movie", "recap", "ova", "oad"})
            or re.search(r"\b(?:complete|collection|all[\s_-]?in[\s_-]?one|batch|gekijouban|soushuuhen)\b", feature_text)
        ):
            return False
    return bool(locators)


def _target_span_visible(registry: LocatorRegistry, subject_id: int, start: int, end: int) -> bool:
    if end < start:
        start, end = end, start
    return all(registry.item_ref_by_subject_sort.get((int(subject_id), sort)) for sort in range(int(start), int(end) + 1))


def _same_count_target_span_candidates(
    registry: LocatorRegistry,
    *,
    target: str,
    local_locators: list[str],
    local_count: int,
    limit: int = 6,
) -> list[dict[str, object]]:
    target_locator, _issue = registry.resolve(target)
    if target_locator is None or not target_locator.subject_id or local_count <= 0:
        return []
    subject_locator = registry.subject_locator_by_id.get(target_locator.subject_id)
    if not subject_locator:
        return []
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()

    def add(start: int, end: int, reason: str) -> None:
        if end < start:
            start, end = end, start
        if (end - start + 1) != local_count:
            return
        if not _target_span_visible(registry, target_locator.subject_id, start, end):
            return
        locator = f"{subject_locator}/episodes/{start}-{end}"
        if locator in seen:
            return
        seen.add(locator)
        candidates.append(
            {
                "locator": locator,
                "episode_start": start,
                "episode_end": end,
                "target_count": local_count,
                "reason": reason,
            }
        )

    for raw in local_locators:
        local_locator, local_issue = registry.resolve(raw)
        if local_issue or local_locator is None or local_locator.kind != "local":
            continue
        numbers = sorted({int(num) for num, _ref in local_locator.episode_file_refs})
        if not numbers:
            continue
        start, end = min(numbers), max(numbers)
        add(start, end, "same episode-number range as the local locator")

    return candidates[:limit]


def _duplicate_target_repair_units(
    registry: LocatorRegistry,
    duplicate_targets: list[str],
    target_usage: dict[str, list[dict[str, object]]],
    *,
    limit: int = 8,
) -> list[dict[str, object]]:
    def fact(raw_locator: str) -> dict[str, object]:
        locator, issue = registry.resolve(raw_locator)
        if issue or locator is None:
            return {"locator": raw_locator, "issue": (issue or {}).get("issue", "locator_not_found")}
        payload: dict[str, object] = {
            "locator": locator.locator,
            "kind": locator.kind,
            "title": locator.title,
        }
        if locator.kind == "local":
            search_queries: list[str] = []
            for label in [*locator.representative_labels[:3], locator.title]:
                for variant in _search_query_variants(str(label or "")):
                    if variant and variant not in search_queries:
                        search_queries.append(variant)
                    if len(search_queries) >= 5:
                        break
                if len(search_queries) >= 5:
                    break
            payload.update(
                {
                    "file_count": len(locator.file_refs),
                    "category": locator.locator.rsplit("/", 1)[-1],
                    "markers": list(locator.markers[:8]),
                    "representative_labels": list(locator.representative_labels[:3]),
                    "search_queries_to_try": search_queries,
                }
            )
        if locator.kind in {"target_subject", "target_episode", "target_span"}:
            payload.update(
                {
                    "subject_id": locator.subject_id,
                    "subject_eps": locator.subject_eps,
                    "episode_start": locator.episode_start,
                    "episode_end": locator.episode_end,
                    "title_aliases": list(locator.markers[:8]),
                    "source_query_texts": list(locator.query_markers[:4]),
                }
            )
        return payload

    repairs: list[dict[str, object]] = []
    for ref in duplicate_targets:
        usages = target_usage.get(ref, [])
        if len(usages) < 2:
            continue
        repairs.append(
            {
                "target_item": _target_item_locator_for_ref(registry, ref),
                "conflicting_units": [
                    {
                        "unit": usage.get("unit"),
                        "local": usage.get("local"),
                        "target": usage.get("target"),
                        "local_facts": [
                            fact(str(local))
                            for local in list(usage.get("local") or [])
                            if str(local).strip()
                        ],
                        "target_facts": [fact(str(usage.get("target") or ""))] if usage.get("target") else [],
                    }
                    for usage in usages[:6]
                ],
                "required": (
                    "Only one conflicting unit can keep this target item. Change the other unit target, "
                    "split its local locator, or choose target_absent/supplemental/non_bangumi according to your semantic judgment. "
                    "Use local_facts and target_facts to check whether a conflict is a real duplicate local copy or a target-choice mistake. "
                    "For named singleton specials/movies, compare representative_labels with target_facts.title_aliases/source_query_texts; "
                    "do not let a different named special take a target that more specifically matches another conflicting unit. "
                    "Do not mark a multi-file main-episodes local unit supplemental merely to clear a duplicate if its title/labels indicate a distinct season/work unit. "
                    "If both local units are duplicate copies or alternate packaging of the same Bangumi item, keep one "
                    "semantic owner mapped and mark the other as supplemental/non_bangumi/target_absent with that reason."
                ),
            }
        )
        if len(repairs) >= limit:
            break
    return repairs


def _unit_statuses_by_label(feedback_units: list[dict[str, object]]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for item in feedback_units:
        if not isinstance(item, dict):
            continue
        unit = str(item.get("unit") or "")
        if not unit:
            continue
        raw_issues = item.get("issues")
        if item.get("issue") or (isinstance(raw_issues, list) and raw_issues):
            statuses[unit] = "blocked"
        else:
            statuses[unit] = "mechanically_ok"
    return statuses


def _duplicate_target_conflict_labels(
    duplicate_target_repair_units: list[dict[str, object]] | None,
) -> set[str]:
    labels: set[str] = set()
    for repair in list(duplicate_target_repair_units or []):
        if not isinstance(repair, dict):
            continue
        for unit in list(repair.get("conflicting_units") or []):
            if not isinstance(unit, dict):
                continue
            label = str(unit.get("unit") or "").strip()
            if label:
                labels.add(label)
    return labels


def _merge_draft_work_units(
    registry: LocatorRegistry,
    previous_units: list[dict[str, object]],
    new_units: list[ResolutionWorkUnit],
    feedback_units: list[dict[str, object]],
    duplicate_target_repair_units: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    statuses = _unit_statuses_by_label(feedback_units)
    conflict_units = _duplicate_target_conflict_labels(duplicate_target_repair_units)
    incoming_labels = {
        str(unit.unit_label or "").strip()
        for unit in new_units
        if str(unit.unit_label or "").strip()
    }
    invalidated_labels = {
        label
        for label, status in statuses.items()
        if status != "mechanically_ok" and label not in incoming_labels
    }.union(
        label
        for label in conflict_units
        if label in statuses and label not in incoming_labels
    )
    new_unit_refs: dict[str, list[str]] = {}
    ref_counts: Counter[str] = Counter()
    for unit in new_units:
        local_refs, local_issues, _canonical_locators = _file_refs_for_locators(registry, unit.local)
        if local_issues:
            continue
        label = str(unit.unit_label or "").strip()
        new_unit_refs[label] = local_refs
        ref_counts.update(local_refs)
    merged: list[dict[str, object]] = [
        dict(item)
        for item in previous_units
        if isinstance(item, dict)
        and str(item.get("unit_label") or "").strip() not in invalidated_labels
    ]
    for unit in new_units:
        label = str(unit.unit_label or "").strip()
        if statuses.get(label) != "mechanically_ok" or label in conflict_units:
            continue
        local_refs, local_issues, _canonical_locators = _file_refs_for_locators(registry, unit.local)
        support_issues, _canonical_support = _validate_support_locators(registry, unit.support)
        if local_issues or support_issues:
            continue
        if any(ref_counts.get(ref, 0) > 1 for ref in local_refs):
            continue
        next_payload = unit.model_dump(mode="json")
        next_payload["_covered_file_refs"] = local_refs
        merged = [
            item
            for item in merged
            if not set(item.get("_covered_file_refs") or []).intersection(local_refs)
            and str(item.get("unit_label") or "") != label
        ]
        merged.append(next_payload)
    return merged[-48:]


def _submit_args_with_saved_draft(
    registry: LocatorRegistry,
    args: SubmitToolArgs,
    draft_units: list[dict[str, object]],
) -> SubmitToolArgs:
    if not draft_units:
        return args
    incoming_units = list(args.resolution.work_units)
    incoming_labels = {str(unit.unit_label or "").strip() for unit in incoming_units if str(unit.unit_label or "").strip()}
    incoming_refs: set[str] = set()
    for unit in incoming_units:
        local_refs, local_issues, _canonical = _file_refs_for_locators(registry, unit.local)
        if not local_issues:
            incoming_refs.update(local_refs)
    saved_units: list[ResolutionWorkUnit] = []
    for item in draft_units:
        if not isinstance(item, dict):
            continue
        label = str(item.get("unit_label") or "").strip()
        saved_refs = {str(ref) for ref in list(item.get("_covered_file_refs") or []) if str(ref)}
        if label and label in incoming_labels:
            continue
        if saved_refs and saved_refs.intersection(incoming_refs):
            continue
        payload = {key: value for key, value in item.items() if not str(key).startswith("_")}
        try:
            saved_units.append(ResolutionWorkUnit.model_validate(payload))
        except ValidationError:
            continue
    if not saved_units:
        return args
    resolution = PackageResolution(
        work_units=[*saved_units, *incoming_units],
        package_reason=args.resolution.package_reason,
    )
    return SubmitToolArgs(resolution=resolution, reason=args.reason, dry_run=args.dry_run)


def _latest_submit_repair_observation(session: HumanCaseSession) -> dict[str, object]:
    for item in reversed(session.observations):
        if isinstance(item, dict) and item.get("tool") == "submit":
            output = item.get("output")
            return dict(output) if isinstance(output, dict) else {}
    return {}


def _immediate_repair_focus(session: HumanCaseSession) -> dict[str, object]:
    repair = _latest_submit_repair_observation(session)
    if not repair or repair.get("accepted"):
        return {}
    required_missing = [
        item
        for item in list(repair.get("required_missing_work_units") or [])
        if isinstance(item, dict)
    ]
    duplicate_repairs = [
        item
        for item in list(repair.get("duplicate_target_repair_units") or [])
        if isinstance(item, dict)
    ]
    blocking_units = [
        item
        for item in list(repair.get("blocking_units") or [])
        if isinstance(item, dict)
    ]
    visible_target_surface_missing_units = [
        item
        for item in list(repair.get("visible_target_surface_missing_units") or [])
        if isinstance(item, dict)
    ]
    diagnostic_units = [
        item
        for item in list(repair.get("diagnostic_units") or [])
        if isinstance(item, dict)
    ]
    if not required_missing and not duplicate_repairs and not blocking_units and not visible_target_surface_missing_units:
        return {}
    focus: dict[str, object] = {
        "status": "open",
        "mechanical_constraint": (
            "The next corrected submit must resolve these exact mechanical gaps. "
            "Saved mechanically-ok work units are merged automatically; do not resubmit the full package unless you are changing it."
        ),
    }
    if required_missing:
        focus["must_cover_missing_local_locators"] = [
            {
                "local": item.get("local"),
                "file_count": item.get("file_count"),
                "choose_outcome": item.get("choose_outcome"),
                "suggested_episode_slices": item.get("suggested_episode_slices") or [],
                "submit_shape": {
                    "unit_label": "choose a concise label for this missing local locator",
                    "local": item.get("local"),
                    "outcome": "choose one value from choose_outcome",
                    "target": "only if you choose a mapped_* outcome",
                    "support": "optional visible local:// or target:// locators",
                    "reason": "required concrete reason for the chosen outcome",
                },
            }
            for item in required_missing[:8]
        ]
        focus["coverage_rule"] = (
            "Every listed local locator is still missing from the merged package resolution. "
            "Include each one exactly once in resolution.work_units, or include one of its suggested local episode slices "
            "only if that slice covers the missing files you intend to resolve."
        )
    if duplicate_repairs:
        focus["duplicate_target_repairs"] = duplicate_repairs[:6]
    if blocking_units:
        focus["blocking_units"] = blocking_units[:8]
    if visible_target_surface_missing_units:
        focus["visible_target_surface_missing_units"] = visible_target_surface_missing_units[:4]
    if diagnostic_units:
        focus["diagnostic_units_non_blocking"] = diagnostic_units[:6]
    count_matched_repairs = [
        item
        for item in list(repair.get("fail_closed_count_matched_target_sibling_repairs") or [])
        if isinstance(item, dict)
    ]
    if count_matched_repairs:
        focus["fail_closed_count_matched_target_sibling_repairs"] = count_matched_repairs[:6]
    uninspected_subject_repairs = [
        item
        for item in list(repair.get("excluded_count_matched_uninspected_subject_repairs") or [])
        if isinstance(item, dict)
    ]
    if uninspected_subject_repairs:
        focus["excluded_count_matched_uninspected_subject_repairs"] = uninspected_subject_repairs[:6]
    singleton_subject_repairs = [
        item
        for item in list(repair.get("excluded_singleton_visible_subject_repairs") or [])
        if isinstance(item, dict)
    ]
    if singleton_subject_repairs:
        focus["excluded_singleton_visible_subject_repairs"] = singleton_subject_repairs[:6]
    singleton_alias_repairs = [
        item
        for item in list(repair.get("singleton_target_alias_repairs") or [])
        if isinstance(item, dict)
    ]
    if singleton_alias_repairs:
        focus["singleton_target_alias_repairs"] = singleton_alias_repairs[:6]
    mapped_bridge_repairs = [
        item
        for item in list(repair.get("mapped_target_title_bridge_repairs") or [])
        if isinstance(item, dict)
    ]
    if mapped_bridge_repairs:
        focus["mapped_target_title_bridge_repairs"] = mapped_bridge_repairs[:6]
    mapped_season_repairs = [
        item
        for item in list(repair.get("mapped_title_season_mismatch_repairs") or [])
        if isinstance(item, dict)
    ]
    if mapped_season_repairs:
        focus["mapped_title_season_mismatch_repairs"] = mapped_season_repairs[:6]
    numbered_special_repairs = [
        item
        for item in list(repair.get("numbered_special_exclusion_repairs") or [])
        if isinstance(item, dict)
    ]
    if numbered_special_repairs:
        focus["numbered_special_exclusion_repairs"] = numbered_special_repairs[:6]
    title_tail_repairs = [
        item
        for item in list(repair.get("excluded_title_tail_search_repairs") or [])
        if isinstance(item, dict)
    ]
    if title_tail_repairs:
        focus["excluded_title_tail_search_repairs"] = title_tail_repairs[:6]
    title_pairing_repairs = [
        item
        for item in list(repair.get("excluded_visible_title_pairing_repairs") or [])
        if isinstance(item, dict)
    ]
    if title_pairing_repairs:
        focus["excluded_visible_title_pairing_repairs"] = title_pairing_repairs[:6]
    title_unresolved_repairs = [
        item
        for item in list(repair.get("excluded_title_tail_unresolved_repairs") or [])
        if isinstance(item, dict)
    ]
    if title_unresolved_repairs:
        focus["excluded_title_tail_unresolved_repairs"] = title_unresolved_repairs[:6]
    fail_closed_slice_pairing_repairs = [
        item
        for item in list(repair.get("fail_closed_slice_pairing_repairs") or [])
        if isinstance(item, dict)
    ]
    if fail_closed_slice_pairing_repairs:
        focus["fail_closed_slice_pairing_repairs"] = fail_closed_slice_pairing_repairs[:6]
    fail_closed_title_tail_bridge_repairs = [
        item
        for item in list(repair.get("fail_closed_title_tail_bridge_repairs") or [])
        if isinstance(item, dict)
    ]
    if fail_closed_title_tail_bridge_repairs:
        focus["fail_closed_title_tail_bridge_repairs"] = fail_closed_title_tail_bridge_repairs[:6]
    excluded_unassigned_repairs = [
        item
        for item in list(repair.get("excluded_singleton_unassigned_target_repairs") or [])
        if isinstance(item, dict)
    ]
    if excluded_unassigned_repairs:
        focus["excluded_singleton_unassigned_target_repairs"] = excluded_unassigned_repairs[:6]
    fail_closed_unassigned_repairs = [
        item
        for item in list(repair.get("fail_closed_singleton_unassigned_target_repairs") or [])
        if isinstance(item, dict)
    ]
    if fail_closed_unassigned_repairs:
        focus["fail_closed_singleton_unassigned_target_repairs"] = fail_closed_unassigned_repairs[:6]
    if session.repeated_submit_rejection_count:
        focus["repeat_warning"] = {
            "repeat_count": session.repeated_submit_rejection_count,
            "required": (
                "The previous submit repeated the same mechanical rejection. The next submit must change the cited "
                "local locator, target locator, episode range, support, or outcome."
            ),
        }
    return focus


def _active_repair_agenda_for_prompt(session: HumanCaseSession) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    visible_options_by_id = {
        str(row.get("agenda_id") or ""): row.get("visible_options") or {}
        for row in _repair_agenda_rows(_latest_submit_repair_observation(session))
        if str(row.get("agenda_id") or "")
    }
    for item in session.cognitive_workspace.investigation_agenda:
        if item.status != "open" or not str(item.agenda_id or "").startswith("REPAIR-"):
            continue
        visible_options = visible_options_by_id.get(item.agenda_id) or {}
        rows.append(
            {
                "agenda_id": item.agenda_id,
                "blocking_issue": item.blocking_issue,
                "locators": list(item.locators)[:8],
                "required_next_action": item.next_action,
                "closure_condition": item.closure_condition,
                "visible_options": _compact_repair_payload(visible_options, list_limit=4, text_limit=220),
            }
        )
        if len(rows) >= 8:
            break
    return rows


def _has_open_submit_repair(repair: dict[str, object]) -> bool:
    return bool(
        repair.get("required_missing_work_units")
        or repair.get("blocking_units")
        or repair.get("duplicate_target_repair_units")
        or repair.get("fail_closed_mapped_sibling_repairs")
        or repair.get("excluded_slice_mapped_sibling_repairs")
        or repair.get("fail_closed_count_matched_target_sibling_repairs")
        or repair.get("excluded_count_matched_uninspected_subject_repairs")
        or repair.get("excluded_singleton_visible_subject_repairs")
        or repair.get("singleton_target_alias_repairs")
        or repair.get("mapped_target_title_bridge_repairs")
        or repair.get("mapped_title_season_mismatch_repairs")
        or repair.get("excluded_main_mapped_sibling_repairs")
        or repair.get("supplemental_main_episode_repairs")
        or repair.get("numbered_special_exclusion_repairs")
        or repair.get("excluded_title_tail_search_repairs")
        or repair.get("excluded_visible_title_pairing_repairs")
        or repair.get("excluded_title_tail_unresolved_repairs")
        or repair.get("fail_closed_slice_pairing_repairs")
        or repair.get("fail_closed_title_tail_bridge_repairs")
        or repair.get("excluded_singleton_unassigned_target_repairs")
        or repair.get("fail_closed_singleton_unassigned_target_repairs")
    )


def _repair_has_target_surface_action(repair: dict[str, object]) -> bool:
    return bool(_target_surface_actions_from_repair(repair))


def _inspect_action_locators(action: str) -> list[str]:
    action = str(action or "")
    if not action.startswith("inspect("):
        return []
    return re.findall(r"[\"']((?:target|local)://[^\"']+)[\"']", action)


def _target_subject_id_from_locator(locator: str) -> int:
    match = re.match(r"^target://bangumi/(?P<sid>\d+)(?:-|/|$)", str(locator or "").strip())
    return int(match.group("sid") or 0) if match else 0


def _inspected_locators_from_session(session: HumanCaseSession) -> set[str]:
    locators: set[str] = set()
    for observation in session.observations:
        if not isinstance(observation, dict) or observation.get("tool") != "inspect":
            continue
        output = observation.get("output") if isinstance(observation.get("output"), dict) else {}
        for item in list(output.get("observations") or []):
            if not isinstance(item, dict):
                continue
            locator = str(item.get("locator") or "").strip()
            if locator:
                locators.add(locator)
            episodes = item.get("episodes")
            if isinstance(episodes, dict):
                regular_span = str(episodes.get("regular_span_locator") or "").strip()
                if regular_span:
                    locators.add(regular_span)
    return locators


def _inspected_target_subject_ids_from_session(session: HumanCaseSession) -> set[int]:
    subject_ids: set[int] = set()
    for locator in _inspected_locators_from_session(session):
        subject_id = _target_subject_id_from_locator(locator)
        if subject_id:
            subject_ids.add(subject_id)
    return subject_ids


def _is_repair_locator_already_inspected(locator: str, inspected_locators: set[str], inspected_subject_ids: set[int]) -> bool:
    locator = str(locator or "").strip()
    if not locator:
        return True
    if locator in inspected_locators:
        return True
    subject_id = _target_subject_id_from_locator(locator)
    return bool(subject_id and subject_id in inspected_subject_ids)


def _repair_has_uninspected_target_surface_action(
    session: HumanCaseSession,
    repair: dict[str, object],
) -> bool:
    inspected_locators = _inspected_locators_from_session(session)
    inspected_subject_ids = _inspected_target_subject_ids_from_session(session)
    for action in _target_surface_actions_from_repair(repair):
        action_locators = _inspect_action_locators(action)
        if not action_locators or any(
            not _is_repair_locator_already_inspected(locator, inspected_locators, inspected_subject_ids)
            for locator in action_locators
        ):
            return True
    return False


def _repair_search_queries_to_try(repair: dict[str, object] | None = None) -> list[str]:
    repair = repair if isinstance(repair, dict) else {}
    queries: list[str] = []

    def add_from(value: object) -> None:
        if not isinstance(value, list):
            return
        for item in value:
            text = str(item or "").strip()
            if text:
                queries.append(text)

    add_from(repair.get("search_queries_to_try"))
    for key in (
        "blocking_units",
        "mapped_target_title_bridge_repairs",
        "mapped_title_season_mismatch_repairs",
        "excluded_title_tail_search_repairs",
        "excluded_episode_title_search_repairs",
        "excluded_title_tail_unresolved_repairs",
        "fail_closed_title_tail_bridge_repairs",
    ):
        value = repair.get(key)
        if not isinstance(value, list):
            continue
        for row in value:
            if not isinstance(row, dict):
                continue
            add_from(row.get("search_queries_to_try"))
            raw_issues = row.get("issues")
            if isinstance(raw_issues, list):
                for issue in raw_issues:
                    if isinstance(issue, dict):
                        add_from(issue.get("search_queries_to_try"))
    return list(dict.fromkeys(queries))


def _target_surface_actions_from_repair(repair: dict[str, object]) -> list[str]:
    actions: list[str] = []

    def append_action(value: object) -> None:
        action = str(value or "").strip()
        if action.startswith("inspect(") and action not in actions:
            actions.append(action)

    raw_actions = repair.get("target_surface_actions")
    if isinstance(raw_actions, list):
        for action in raw_actions:
            append_action(action)
            if len(actions) >= 4:
                return actions
    for key in (
        "excluded_count_matched_uninspected_subject_repairs",
        "excluded_singleton_visible_subject_repairs",
        "fail_closed_count_matched_target_sibling_repairs",
        "fail_closed_title_tail_bridge_repairs",
    ):
        rows = repair.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                append_action(row.get("available_action"))
                for target in list(row.get("visible_source_query_bridge_targets") or []):
                    if isinstance(target, dict):
                        append_action(target.get("available_action"))
                        if len(actions) >= 4:
                            return actions
                if len(actions) >= 4:
                    return actions
    numbered_repairs = repair.get("numbered_special_exclusion_repairs")
    if isinstance(numbered_repairs, list):
        for row in numbered_repairs:
            if not isinstance(row, dict):
                continue
            for subject in list(row.get("same_count_visible_subjects") or []):
                if isinstance(subject, dict):
                    append_action(subject.get("available_action"))
                    if len(actions) >= 4:
                        return actions
    blocking_units = repair.get("blocking_units")
    if isinstance(blocking_units, list):
        for unit in blocking_units:
            if not isinstance(unit, dict):
                continue
            repairs = unit.get("target_surface_repairs")
            if isinstance(repairs, list):
                for item in repairs:
                    if isinstance(item, dict):
                        append_action(item.get("available_action"))
                        if len(actions) >= 4:
                            return actions
            issues = unit.get("issue") or unit.get("issues")
            issue_rows = issues if isinstance(issues, list) else [issues]
            for item in issue_rows:
                if isinstance(item, dict):
                    append_action(item.get("available_action"))
                    if len(actions) >= 4:
                        return actions
    return actions


def _local_locator_scope_matches(candidate: str, target: str) -> bool:
    candidate_key = str(candidate or "").strip().casefold()
    target_key = str(target or "").strip().casefold()
    if not candidate_key or not target_key:
        return False
    if candidate_key == target_key:
        return True
    for suffix in ("/episode/", "/episodes/"):
        if candidate_key.startswith(f"{target_key}{suffix}"):
            return True
        if target_key.startswith(f"{candidate_key}{suffix}"):
            return True
    return False


def _repair_finalization_target_locators(session: HumanCaseSession, *, limit: int = 12) -> list[str]:
    locators: list[str] = []

    def add(value: object) -> None:
        locator = str(value or "").strip()
        if locator.startswith("local://") and locator not in locators:
            locators.append(locator)

    for item in _active_repair_agenda_for_prompt(session):
        for locator in list(item.get("locators") or []):
            add(locator)
        visible_options = item.get("visible_options") if isinstance(item.get("visible_options"), dict) else {}
        for key in ("local_slice_mapping_options", "local_target_title_pairing_options"):
            rows = visible_options.get(key) if isinstance(visible_options, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                add(row.get("local") or row.get("local_slice"))
    readiness = session.cognitive_workspace.resolution_readiness
    for item in readiness.blocking_work_units:
        add(item)
    return locators[:limit]


def _submit_exact_fail_closed_rows_for_repair(
    session: HumanCaseSession,
    args: SubmitToolArgs,
    *,
    limit: int = 12,
) -> list[dict[str, object]]:
    target_locators = _repair_finalization_target_locators(session)
    if not target_locators:
        return []
    rows: list[dict[str, object]] = []
    for unit in args.resolution.work_units:
        if unit.outcome != "fail_closed" or not str(unit.reason or "").strip():
            continue
        unit_locators = [str(locator or "").strip() for locator in unit.local if str(locator or "").strip()]
        matched = [
            target
            for target in target_locators
            if any(_local_locator_scope_matches(locator, target) for locator in unit_locators)
        ]
        if not matched:
            continue
        rows.append(
            {
                "unit_label": unit.unit_label,
                "local": unit_locators,
                "matched_active_repair_locators": matched[:6],
                "reason": str(unit.reason or "").strip()[:320],
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _repair_finalization_guard_for_prompt(
    session: HumanCaseSession,
    *,
    max_turns: int,
) -> dict[str, object]:
    latest_repair = _latest_submit_repair_observation(session)
    active_repair_agenda = _active_repair_agenda_for_prompt(session)
    if not active_repair_agenda or not _has_open_submit_repair(latest_repair):
        return {}
    remaining_turns = max(0, int(max_turns) - int(session.turn_count))
    near_cap = remaining_turns <= REPAIR_FINALIZATION_TURN_WINDOW
    stalled = bool(session.stall_warning_count or session.no_progress_turn_count >= 2)
    if not (near_cap or stalled):
        return {}
    target_surface_actions = _target_surface_actions_from_repair(latest_repair)
    uninspected_targets = _uninspected_target_surface_action_locators(session, latest_repair)
    search_queries = _repair_search_queries_to_try(latest_repair)
    finalization_locators = _repair_finalization_target_locators(session)
    if uninspected_targets:
        required_next_action = (
            "Use inspect on the listed target_surface_actions, or use note to record why the exact active "
            "work unit remains unresolved after visible evidence. Do not resubmit a broad package first."
        )
    elif search_queries and session.search_call_count < SEARCH_TOOL_CALL_BUDGET and remaining_turns > 2:
        required_next_action = (
            "Use one batched search for the listed repair queries, then inspect visible candidates or exact-fail-close "
            "the active work unit. Do not use submit as another exploratory try."
        )
    else:
        required_next_action = (
            "Submit only concrete repairs for the active work units. If they are still unsafe, submit every "
            "listed finalization_target_locator as outcome=fail_closed with visible-evidence blockers."
        )
    return {
        "issue": "near_cap_repair_finalization_guard" if near_cap else "stall_repair_finalization_guard",
        "remaining_turns": remaining_turns,
        "stall_warning_active": stalled,
        "active_repair_agenda": active_repair_agenda[:4],
        "finalization_target_locators": finalization_locators[:8],
        "target_surface_actions": target_surface_actions[:4],
        "uninspected_target_surface_locators": uninspected_targets[:4],
        "search_queries_to_try": search_queries[:8],
        "allowed_actions": [
            "inspect/search/note only when it adds evidence for the current active_repair_agenda",
            "submit accepted mapping/exclusion only if the active repair agenda is actually closed by the submitted fields",
            "submit outcome=fail_closed for every listed finalization_target_locator with concrete visible-evidence blockers",
        ],
        "forbidden_fixed_layer_choices": [
            "fixed layer does not choose target",
            "fixed layer does not choose special/OVA/OAD/SP",
            "fixed layer does not choose target_absent",
            "fixed layer does not split work units",
        ],
        "required_next_action": required_next_action,
    }


def _near_cap_submit_finalization_guard_output(
    session: HumanCaseSession,
    args: SubmitToolArgs,
    *,
    max_turns: int,
) -> dict[str, object]:
    guard = _repair_finalization_guard_for_prompt(session, max_turns=max_turns)
    if not guard:
        return {}
    remaining_turns = int(guard.get("remaining_turns") or 0)
    if remaining_turns > 2 and not guard.get("stall_warning_active"):
        return {}
    exact_fail_closed_rows = _submit_exact_fail_closed_rows_for_repair(session, args)
    finalization_target_locators = _repair_finalization_target_locators(session)
    covered_locators = {
        str(locator)
        for row in exact_fail_closed_rows
        for locator in list(row.get("matched_active_repair_locators") or [])
    }
    missing_exact_fail_closed_locators = [
        locator
        for locator in finalization_target_locators
        if not any(_local_locator_scope_matches(covered, locator) for covered in covered_locators)
    ]
    if finalization_target_locators and not missing_exact_fail_closed_locators:
        return {}
    if guard.get("uninspected_target_surface_locators"):
        required_next_action = (
            "The active repair agenda still has uninspected target-surface evidence. Inspect those locators, "
            "then submit the repaired work unit or exact fail_closed blocker."
        )
    else:
        required_next_action = (
            "The active repair agenda is near the turn cap. Submit exact fail_closed rows for every listed "
            "finalization_target_locator, with concrete visible-evidence blockers, unless you can submit a "
            "package that actually passes mechanical verification."
        )
    return {
        "accepted": False,
        "status": "near_cap_repair_finalization_guard",
        "issue": "near_cap_repair_finalization_requires_exact_work_unit_closure",
        "near_cap_repair_finalization_guard": guard,
        "exact_fail_closed_rows": exact_fail_closed_rows,
        "missing_exact_fail_closed_locators": missing_exact_fail_closed_locators,
        "required_next_action": required_next_action,
    }


def _fail_closed_blocker_from_open_repair(session: HumanCaseSession) -> dict[str, object]:
    latest_repair = _latest_submit_repair_observation(session)
    if not _repair_has_uninspected_target_surface_action(session, latest_repair):
        return {}
    inspected_locators = _inspected_locators_from_session(session)
    inspected_subject_ids = _inspected_target_subject_ids_from_session(session)
    actions = [
        action
        for action in _target_surface_actions_from_repair(latest_repair)
        if any(
            not _is_repair_locator_already_inspected(locator, inspected_locators, inspected_subject_ids)
            for locator in _inspect_action_locators(action)
        )
    ][:4]
    return {
        "accepted": False,
        "status": "finish_blocked",
        "issue_counts": {"fail_closed_with_executable_target_surface_action": 1},
        "issue": "fail_closed_with_executable_target_surface_action",
        "target_surface_actions": actions,
        "blocking_units": latest_repair.get("blocking_units") or [],
        "saved_mechanically_ok_work_units": _draft_work_unit_summary(session.draft_work_units),
        "required_next_action": (
            "fail_closed is blocked because the previous submit rejection exposed executable target-side evidence. "
            "Run one of target_surface_actions first, then submit accepted mapping/exclusion or fail_closed with the new observation. "
            "This is an evidence-exhaustion gate, not a semantic target decision."
        ),
    }


def _budget_pressure_tool_choice(
    session: HumanCaseSession,
    *,
    max_turns: int,
) -> str | dict[str, object]:
    remaining_turns = max(0, int(max_turns) - int(session.turn_count))
    latest_repair = _latest_submit_repair_observation(session)
    has_open_repair = _has_open_submit_repair(latest_repair)
    repair_finalization_pressure = remaining_turns <= REPAIR_FINALIZATION_TURN_WINDOW
    target_surface_action_open = _repair_has_uninspected_target_surface_action(session, latest_repair)
    repair_queries = _repair_search_queries_to_try(latest_repair)
    if repair_finalization_pressure and has_open_repair:
        if target_surface_action_open and remaining_turns > 1:
            return {"type": "function", "function": {"name": "inspect"}}
        if (
            remaining_turns > 2
            and session.last_tool_name != "search"
            and session.search_call_count < SEARCH_TOOL_CALL_BUDGET
            and repair_queries
        ):
            return {"type": "function", "function": {"name": "search"}}
        if remaining_turns <= 2 and session.draft_work_units:
            return {"type": "function", "function": {"name": "submit"}}
    if (
        remaining_turns > 1
        and session.last_tool_name != "search"
        and session.search_call_count < SEARCH_TOOL_CALL_BUDGET
        and repair_queries
    ):
        return {"type": "function", "function": {"name": "search"}}
    if target_surface_action_open and remaining_turns > 1:
        return {"type": "function", "function": {"name": "inspect"}}
    if remaining_turns > REPAIR_FINALIZATION_TURN_WINDOW or not session.draft_work_units:
        return "required"
    if not has_open_repair:
        return "required"
    if remaining_turns > 1 and target_surface_action_open:
        return "required"
    return {"type": "function", "function": {"name": "submit"}}


def _search_budget_tool_choice(session: HumanCaseSession) -> str | dict[str, object]:
    if session.search_call_count < SEARCH_TOOL_CALL_BUDGET:
        return "required"
    if "inspect" in session.tool_sequence:
        return {"type": "function", "function": {"name": "submit"}}
    return {"type": "function", "function": {"name": "inspect"}}


def _budget_pressure_tool_rejection(
    session: HumanCaseSession,
    tool_name: str,
    *,
    max_turns: int,
) -> dict[str, object] | None:
    remaining_turns = max(0, int(max_turns) - int(session.turn_count))
    if remaining_turns > 1 or tool_name not in {"search", "inspect"}:
        return None
    latest_repair = _latest_submit_repair_observation(session)
    if not session.draft_work_units or not latest_repair:
        return None
    if not _has_open_submit_repair(latest_repair):
        return None
    if tool_name == "inspect" and _repair_has_uninspected_target_surface_action(session, latest_repair):
        return None
    return {
        "accepted": False,
        "issue": "turn_budget_requires_resolution",
        "rejected_tool": tool_name,
        "remaining_turns": remaining_turns,
        "saved_mechanically_ok_work_units": _draft_work_unit_summary(session.draft_work_units),
        "latest_submit_repair": latest_repair,
        "required_next_action": (
            "Use submit for the remaining blocking/missing local units, or submit fail_closed units with concrete "
            "reasons for the unresolved locators. Search/inspect is blocked here by budget/loop guard, not by semantic judgment."
        ),
    }


def _uninspected_target_surface_action_locators(
    session: HumanCaseSession,
    repair: dict[str, object] | None = None,
    *,
    limit: int = 4,
) -> list[str]:
    repair = repair if isinstance(repair, dict) else _latest_submit_repair_observation(session)
    inspected_locators = _inspected_locators_from_session(session)
    inspected_subject_ids = _inspected_target_subject_ids_from_session(session)
    required: list[str] = []
    for action in _target_surface_actions_from_repair(repair):
        for locator in _inspect_action_locators(action):
            if (
                _is_repair_locator_already_inspected(locator, inspected_locators, inspected_subject_ids)
                or locator in required
            ):
                continue
            required.append(locator)
            if len(required) >= limit:
                return required
    return required


def _inspect_args_with_required_repair_locators(
    session: HumanCaseSession,
    args: InspectToolArgs,
    *,
    max_locators: int = 12,
) -> tuple[InspectToolArgs, dict[str, object]]:
    """Carry out explicit target-surface repair actions when Agent chose inspect.

    This does not choose a semantic target. It only consumes target locators that
    the previous submit verifier already exposed as mechanically required
    evidence, preventing an inspect turn from drifting away from the open repair.
    """

    required = _uninspected_target_surface_action_locators(session)
    if not required:
        return args, {}
    requested = [str(item).strip() for item in list(args.locators or []) if str(item).strip()]
    merged: list[str] = []
    for locator in [*required, *requested]:
        if locator and locator not in merged:
            merged.append(locator)
        if len(merged) >= max_locators:
            break
    scope = [str(item).strip() for item in list(args.scope or []) if str(item).strip()]
    for required_scope in ("details", "episodes", "related"):
        if required_scope not in {item.casefold() for item in scope}:
            scope.append(required_scope)
    if merged == requested and scope == list(args.scope or []):
        return args, {}
    updated = args.model_copy(update={"locators": merged, "scope": scope})
    added = [locator for locator in required if locator not in requested]
    return updated, {
        "required_repair_inspect_locators": required,
        "required_repair_inspect_locators_added": added,
        "original_requested_locators": requested,
        "effective_locators": merged,
        "effective_scope": scope,
        "reason": (
            "Previous submit feedback exposed target_surface_actions. The fixed layer consumed those "
            "mechanically required target surfaces in this inspect call; semantic ownership remains Agent-decided."
        ),
    }


def _submit_result_with_auto_target_surface_inspect(
    workspace: CaseEvidenceWorkspace,
    registry: LocatorRegistry,
    bangumi_client: object,
    session: HumanCaseSession,
    args: SubmitToolArgs,
    submit_result: SubmitCompileResult,
    *,
    searched_query_variant_keys: set[str] | None = None,
) -> tuple[CaseEvidenceWorkspace, HumanCaseSession, SubmitCompileResult, dict[str, object]]:
    if submit_result.accepted:
        return workspace, session, submit_result, {}
    agenda = _repair_agenda_from_submit_feedback(submit_result.feedback, repeated=False)
    locators = _uninspected_target_surface_action_locators(session, agenda, limit=4)
    if not locators:
        return workspace, session, submit_result, {}
    workspace, inspect_output = _inspect_tool(
        workspace,
        registry,
        bangumi_client,
        InspectToolArgs(
            locators=locators,
            scope=["details", "episodes", "related"],
            reason="Auto-consume target_surface_actions exposed by submit verifier before retrying the same Agent resolution.",
        ),
    )
    session.observations.append(
        {
            "tool": "inspect",
            "output": {
                **inspect_output,
                "auto_from_submit_repair": True,
                "required_repair_inspect_locators": locators,
            },
        }
    )
    retried = _submit_tool(
        workspace,
        registry,
        args,
        searched_query_variant_keys=searched_query_variant_keys,
    )
    return workspace, session, retried, {
        "note": "human_case_agent_submit_auto_target_surface_inspect",
        "locators": locators,
        "original_issue_counts": (
            submit_result.feedback.get("package", {}).get("issue_counts", {})
            if isinstance(submit_result.feedback.get("package"), dict)
            else {}
        ),
        "retry_accepted": retried.accepted,
        "retry_issue_counts": (
            retried.feedback.get("package", {}).get("issue_counts", {})
            if isinstance(retried.feedback.get("package"), dict)
            else {}
        ),
        "boundary": (
            "Mechanical target-surface completion only. The fixed layer inspected target locators exposed by "
            "the verifier for the Agent's submitted resolution; it did not choose a Bangumi target or outcome."
        ),
    }


def _action_health_observation(session: HumanCaseSession, *, max_turns: int) -> dict[str, object]:
    remaining_turns = max(0, int(max_turns) - int(session.turn_count))
    latest_repair = _latest_submit_repair_observation(session)
    has_open_repair = _has_open_submit_repair(latest_repair)
    active_repair_agenda = _active_repair_agenda_for_prompt(session)
    finalization_guard = _repair_finalization_guard_for_prompt(session, max_turns=max_turns)
    search_stall = bool(
        session.search_call_count >= 3
        and session.search_new_subject_count == 0
    )
    broad_search_pressure = bool(
        session.search_call_count >= 4
        and session.search_existing_only_count + session.search_no_result_count >= 2
    )
    if search_stall:
        guidance = (
            "Search has not added new target subjects. Prefer inspect/submit using visible locators "
            "unless you have a new clean title alias."
        )
    elif broad_search_pressure:
        guidance = (
            "Search has consumed several turns. Prefer inspecting visible candidates or repairing the latest submit "
            "unless a new title alias is necessary."
        )
    else:
        guidance = "No search stall detected."
    return {
        "search_call_count": session.search_call_count,
        "search_new_subject_count": session.search_new_subject_count,
        "search_existing_only_count": session.search_existing_only_count,
        "search_no_result_count": session.search_no_result_count,
        "last_search_progress": session.last_search_progress,
        "has_open_submit_repair": has_open_repair,
        "active_repair_agenda_count": len(active_repair_agenda),
        "current_consecutive_tool_count": session.current_consecutive_tool_count,
        "last_turn_health": dict(session.last_turn_health),
        "remaining_turns": remaining_turns,
        "search_stall_suspected": search_stall,
        "broad_search_pressure": broad_search_pressure,
        "mechanical_guidance": guidance,
        "near_cap_repair_finalization_guard": finalization_guard,
    }


def _draft_work_unit_summary(draft_units: list[dict[str, object]], *, limit: int = 24) -> list[dict[str, object]]:
    return [
        {
            "unit_label": unit.get("unit_label"),
            "local": unit.get("local"),
            "outcome": unit.get("outcome"),
            "target": unit.get("target"),
            "episode_start": unit.get("episode_start"),
            "episode_end": unit.get("episode_end"),
            "covered_file_count": len(unit.get("_covered_file_refs") or []),
        }
        for unit in draft_units[:limit]
        if isinstance(unit, dict)
    ]


def _unit_mechanical_checklist(
    feedback_units: list[dict[str, object]],
    duplicate_target_repair_units: list[dict[str, object]],
    *,
    limit: int = 24,
) -> list[dict[str, object]]:
    conflict_units = {
        str(unit.get("unit") or "")
        for repair in duplicate_target_repair_units
        if isinstance(repair, dict)
        for unit in list(repair.get("conflicting_units") or [])
        if isinstance(unit, dict)
    }
    checklist: list[dict[str, object]] = []
    for item in feedback_units:
        if not isinstance(item, dict):
            continue
        unit = str(item.get("unit") or "")
        issue_codes: list[str] = []
        if item.get("issue"):
            issue_codes.append(str(item.get("issue")))
        raw_issues = item.get("issues")
        if isinstance(raw_issues, list):
            issue_codes.extend(
                str(issue.get("issue") or "issue")
                for issue in raw_issues
                if isinstance(issue, dict)
            )
        status = "mechanically_ok"
        required_change = "keep unless you intentionally change the semantic judgment"
        if issue_codes:
            status = "blocked"
            required_change = "change this unit's local locator, target locator, episode range, support, or outcome"
        elif unit in conflict_units:
            status = "global_duplicate_target_conflict"
            required_change = "this unit shares a target item with another unit; only one conflicting unit can keep that target item"
        checklist.append(
            {
                "unit": unit,
                "status": status,
                "local": item.get("local"),
                "target": item.get("target"),
                "outcome": item.get("outcome"),
                "issue_codes": issue_codes,
                "required_change": required_change,
            }
        )
        if len(checklist) >= limit:
            break
    return checklist


def _repair_unit_labels(repair: dict[str, object]) -> set[str]:
    labels: set[str] = set()
    for key in ("unit", "excluded_unit", "mapped_unit", "fail_closed_unit"):
        label = str(repair.get(key) or "").strip()
        if label:
            labels.add(label)
    return labels


def _repair_local_locators(repair: dict[str, object]) -> set[str]:
    locators: set[str] = set()
    for key in ("local", "excluded_local", "mapped_local", "fail_closed_local"):
        value = repair.get(key)
        if isinstance(value, list):
            locators.update(str(item).strip() for item in value if str(item).strip())
        elif str(value or "").strip():
            locators.add(str(value).strip())
    return locators


def _feedback_units_with_package_repairs(
    feedback_units: list[dict[str, object]],
    repair_groups: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Attach package-level mechanical repair issues to the affected unit rows.

    Some checks are only computable after the whole package is compiled, but the
    agent still needs row-level feedback so it can revise the exact work unit.
    """
    issues_by_label: dict[str, list[dict[str, object]]] = defaultdict(list)
    issues_by_local: dict[str, list[dict[str, object]]] = defaultdict(list)
    for issue_code, repairs in repair_groups.items():
        for repair in repairs:
            if not isinstance(repair, dict):
                continue
            issue_payload = {
                "issue": issue_code,
                **{
                    key: repair.get(key)
                    for key in (
                        "required",
                        "local",
                        "excluded_local",
                        "mapped_local",
                        "fail_closed_local",
                        "target",
                        "visible_subject",
                        "available_action",
                        "unassigned_target_candidates",
                        "allowed_with_negative_target_absence_evidence",
                        "negative_target_absence_support_candidates",
                        "negative_target_absence_submit_shape",
                        "target_title",
                        "target_title_aliases",
                        "selected_target_title",
                        "selected_target_title_aliases",
                        "selected_target_title_tokens",
                        "target_source_query_texts",
                        "mapped_local",
                        "excluded_local",
                        "mapped_overlap_score",
                        "excluded_overlap_score",
                        "search_queries_to_try",
                        "unsearched_title_tokens",
                        "visible_target_title_tokens",
                        "support_targets",
                        "parent_local",
                        "parent_local_fact",
                        "mapped_siblings",
                        "local_target_title_pairing_options",
                        "local_slice_mapping_options",
                        "unassigned_target_candidates",
                        "unbridged_title_tail_tokens",
                        "searched_query_hints",
                        "visible_source_query_bridge_targets",
                    )
                    if key in repair
                },
            }
            for label in _repair_unit_labels(repair):
                issues_by_label[label].append(issue_payload)
            for local in _repair_local_locators(repair):
                issues_by_local[local].append(issue_payload)

    if not issues_by_label and not issues_by_local:
        return feedback_units

    annotated: list[dict[str, object]] = []
    for item in feedback_units:
        if not isinstance(item, dict):
            annotated.append(item)
            continue
        unit = str(item.get("unit") or "").strip()
        local_values = {str(local).strip() for local in list(item.get("local") or []) if str(local).strip()}
        extra_issues: list[dict[str, object]] = []
        extra_issues.extend(issues_by_label.get(unit, []))
        for local in local_values:
            extra_issues.extend(issues_by_local.get(local, []))
        if not extra_issues:
            annotated.append(item)
            continue
        deduped: list[dict[str, object]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for issue in extra_issues:
            key = (
                str(issue.get("issue") or ""),
                str(issue.get("local") or issue.get("excluded_local") or issue.get("mapped_local") or ""),
                str(issue.get("target") or issue.get("visible_subject") or ""),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(issue)
        next_item = dict(item)
        raw_issues = next_item.get("issues")
        existing_issues = list(raw_issues) if isinstance(raw_issues, list) else []
        next_item["issues"] = [*existing_issues, *deduped]
        annotated.append(next_item)
    return annotated


def _episode_slice_parent_locator(locator: str) -> str:
    return re.sub(r"/episodes/\d+-\d+$|/episode/\d+$", "", str(locator or "").strip())


def _fail_closed_mapped_sibling_repairs(
    registry: LocatorRegistry,
    fail_closed_units: list[dict[str, object]],
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    mapped_by_parent: dict[str, list[dict[str, object]]] = defaultdict(list)
    for unit in feedback_units:
        if not isinstance(unit, dict) or not str(unit.get("outcome") or "").startswith("mapped_"):
            continue
        target = str(unit.get("target") or "").strip()
        if not target:
            continue
        for local in list(unit.get("local") or []):
            parent = _episode_slice_parent_locator(str(local))
            if parent and parent != str(local):
                mapped_by_parent[parent].append(unit)
    repairs: list[dict[str, object]] = []
    for unit in fail_closed_units:
        for local in list(unit.get("local") or []):
            parent = _episode_slice_parent_locator(str(local))
            if not parent or parent == str(local):
                continue
            siblings = mapped_by_parent.get(parent) or []
            if not siblings:
                continue
            parent_locator, parent_issue = registry.resolve(parent)
            demoted_targets: set[str] = set()
            for sibling in siblings:
                sibling_target_locator, sibling_target_issue = registry.resolve(str(sibling.get("target") or ""))
                if sibling_target_issue or sibling_target_locator is None:
                    continue
                demoted_targets.add(sibling_target_locator.locator)
                if sibling_target_locator.subject_id:
                    subject_locator = registry.subject_locator_by_id.get(int(sibling_target_locator.subject_id), "")
                    if subject_locator:
                        demoted_targets.add(subject_locator)
            repair = {
                "issue": "fail_closed_with_mapped_sibling",
                "unit": unit.get("unit"),
                "local": local,
                "parent_local": parent,
                "parent_local_fact": {
                    "locator": parent,
                    "title": getattr(parent_locator, "title", "") if parent_locator is not None else "",
                    "file_count": len(getattr(parent_locator, "file_refs", ()) or ())
                    if parent_locator is not None
                    else 0,
                    "issue": (parent_issue or {}).get("issue", "") if parent_issue else "",
                },
                "mapped_siblings": [
                    {
                        "unit": sibling.get("unit"),
                        "local": sibling.get("local"),
                        "target": sibling.get("target"),
                        "outcome": sibling.get("outcome"),
                    }
                    for sibling in siblings[:4]
                ],
                "required": (
                    "This fail_closed local slice belongs to the same episode-like parent locator as an already "
                    "mapped sibling. Do not leave a leftover slice fail_closed solely after splitting for count. "
                    "Inspect or use the sibling target subject if semantically correct, change target, classify as "
                    "supplemental/non_bangumi/target_absent with a concrete reason, or fail_closed only after "
                    "addressing this parent/sibling contradiction."
                ),
            }
            pairing_options = _local_target_title_pairing_options_for_slice(
                registry,
                str(local),
                demoted_targets=demoted_targets,
            )
            if pairing_options:
                repair["local_target_title_pairing_options"] = pairing_options
            repairs.append(repair)
            break
    return repairs[:8]


def _excluded_slice_mapped_sibling_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    mapped_by_parent: dict[str, list[dict[str, object]]] = defaultdict(list)
    for unit in feedback_units:
        if not isinstance(unit, dict) or not str(unit.get("outcome") or "").startswith("mapped_"):
            continue
        target = str(unit.get("target") or "").strip()
        if not target:
            continue
        for local in list(unit.get("local") or []):
            parent = _episode_slice_parent_locator(str(local))
            if parent and parent != str(local):
                mapped_by_parent[parent].append(unit)
    if not mapped_by_parent:
        return []

    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") not in {"supplemental", "bangumi_target_absent", "non_bangumi"}:
            continue
        for local in list(unit.get("local") or []):
            parent = _episode_slice_parent_locator(str(local))
            if not parent or parent == str(local):
                continue
            siblings = mapped_by_parent.get(parent) or []
            if not siblings:
                continue
            local_locator, local_issue = registry.resolve(str(local))
            if local_issue or local_locator is None or local_locator.kind != "local":
                continue
            if _has_hard_non_owner_reason(
                unit.get("unit"),
                unit.get("reason"),
                local_locator.title,
                " ".join(local_locator.markers),
                " ".join(local_locator.representative_labels[:3]),
            ) or _has_contextual_packaging_extra_reason(
                local_locator,
                unit.get("unit"),
                unit.get("reason"),
            ):
                continue
            parent_locator, parent_issue = registry.resolve(parent)
            demoted_targets: set[str] = set()
            for sibling in siblings:
                sibling_target_locator, sibling_target_issue = registry.resolve(str(sibling.get("target") or ""))
                if sibling_target_issue or sibling_target_locator is None:
                    continue
                demoted_targets.add(sibling_target_locator.locator)
                if sibling_target_locator.subject_id:
                    subject_locator = registry.subject_locator_by_id.get(int(sibling_target_locator.subject_id), "")
                    if subject_locator:
                        demoted_targets.add(subject_locator)
            repair = {
                "issue": "excluded_slice_with_mapped_sibling",
                "unit": unit.get("unit"),
                "outcome": unit.get("outcome"),
                "local": local_locator.locator,
                "parent_local": parent,
                "parent_local_fact": {
                    "locator": parent,
                    "title": getattr(parent_locator, "title", "") if parent_locator is not None else "",
                    "file_count": len(getattr(parent_locator, "file_refs", ()) or ())
                    if parent_locator is not None
                    else 0,
                    "issue": (parent_issue or {}).get("issue", "") if parent_issue else "",
                },
                "representative_labels": list(local_locator.representative_labels[:4]),
                "mapped_siblings": [
                    {
                        "unit": sibling.get("unit"),
                        "local": sibling.get("local"),
                        "target": sibling.get("target"),
                        "outcome": sibling.get("outcome"),
                    }
                    for sibling in siblings[:4]
                ],
                "required": (
                    "This local slice belongs to the same episode-like parent as an already mapped sibling, but "
                    "it is being excluded without a hard duplicate/copy/packaging reason. Resolve the sibling "
                    "ownership explicitly: map a visible matching target, give a hard non-owner reason, or "
                    "fail_closed this slice with the remaining evidence gap. The fixed layer is checking slice "
                    "accounting consistency; it is not choosing the target."
                ),
            }
            pairing_options = _local_target_title_pairing_options_for_slice(
                registry,
                local_locator.locator,
                demoted_targets=demoted_targets,
            )
            if pairing_options:
                repair["local_target_title_pairing_options"] = pairing_options
            repairs.append(repair)
            break
        if len(repairs) >= 8:
            break
    return repairs[:8]


def _fail_closed_with_visible_slice_pairing_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") != "fail_closed":
            continue
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if issue or locator is None or locator.kind != "local":
                continue
            triples = _episode_label_triples(locator)
            if not (2 <= len(triples) <= 4):
                continue
            category = locator.locator.rsplit("/", 1)[-1]
            if category not in {"main", "main-episodes", "episodes"}:
                continue
            pairing_options = _local_target_title_pairing_options(
                registry,
                [locator.locator],
                limit=8,
            )
            if not pairing_options:
                continue
            repairs.append(
                {
                    "issue": "fail_closed_with_visible_slice_pairing",
                    "unit": unit.get("unit"),
                    "local": [locator.locator],
                    "reason": unit.get("reason"),
                    "file_count": len(locator.file_refs),
                    "representative_labels": list(locator.representative_labels[:4]),
                    "local_target_title_pairing_options": pairing_options,
                    "local_slice_mapping_options": _local_slice_mapping_options_from_title_pairings(pairing_options),
                    "required": (
                        "This multi-file local locator is fail_closed at parent level while visible local://.../episode/N "
                        "slice locators and one-item target pairing candidates are available. Split the local side into "
                        "the listed slices, then map, exclude, or fail_closed each exact slice by semantic judgment. "
                        "The fixed layer is checking resolution granularity; it is not choosing the target."
                    ),
                }
            )
            break
        if len(repairs) >= 8:
            break
    return repairs


def _title_family_overlap(left: str, right: str) -> bool:
    left_norm = _normalized_locator_match_text(left)
    right_norm = _normalized_locator_match_text(right)
    if not left_norm or not right_norm:
        return False
    if len(left_norm) >= 8 and len(right_norm) >= 8 and (left_norm in right_norm or right_norm in left_norm):
        return True
    left_tokens = {token for token in left_norm.split() if len(token) >= 3 and not token.isdigit()}
    right_tokens = {token for token in right_norm.split() if len(token) >= 3 and not token.isdigit()}
    return len(left_tokens.intersection(right_tokens)) >= 3


def _distinctive_title_tokens(value: str) -> set[str]:
    stop_tokens = {
        "episode",
        "episodes",
        "main",
        "marker",
        "menu",
        "movie",
        "ova",
        "oad",
        "preview",
        "season",
        "special",
        "sp",
        "sps",
        "the",
    }
    values = [str(value or "")]
    values.extend(_known_alias_query_variants(value))
    result: set[str] = set()
    for item in values:
        result.update(
            token
            for token in _normalized_locator_match_text(item).split()
            if len(token) >= 4 and token not in stop_tokens and not token.isdigit()
        )
    return result


def _shared_distinctive_title_token(left: str, right: str) -> bool:
    left_tokens = _distinctive_title_tokens(left)
    right_tokens = _distinctive_title_tokens(right)
    return bool(left_tokens.intersection(right_tokens))


def _title_season_number_hint(value: str) -> int | None:
    text = _normalized_locator_match_text(value)
    if not text:
        return None
    roman_map = {"ii": 2, "iii": 3, "iv": 4, "v": 5}
    tokens = [token for token in text.split() if token]
    for token in reversed(tokens):
        if token in roman_map:
            return roman_map[token]
        if token in {"2", "3", "4", "5"}:
            return int(token)
    return None


def _seasonless_title_query(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip(" \t\r\n._-/[](){}"))
    if not text:
        return ""
    replacements = [
        (r"(?i)\s+(?:season\s*)?[2-5]\s*$", ""),
        (r"(?i)\s+(?:ii|iii|iv|v)\s*$", ""),
        (r"\s+(?:Ⅱ|Ⅲ|Ⅳ|Ⅴ)\s*$", ""),
        (r"(?i)\s+(?:2nd|3rd|4th|5th)\s+season\s*$", ""),
        (r"(?i)\s+season\s+(?:[2-5]|ii|iii|iv|v)\s*$", ""),
        (r"\s+第(?:二|三|四|五|2|3|4|5)季\s*$", ""),
        (r"\s+第(?:二|三|四|五|2|3|4|5)期\s*$", ""),
    ]
    for pattern, replacement in replacements:
        candidate = re.sub(pattern, replacement, text).strip(" \t\r\n._-/[](){}")
        if candidate and candidate != text:
            return candidate
    return ""


def _title_family_overlap_score(left_tokens: set[str], right_tokens: set[str]) -> int:
    return len((left_tokens - _TITLE_TAIL_GENERIC_TOKENS).intersection(right_tokens - _TITLE_TAIL_GENERIC_TOKENS))


def _target_subject_eps(registry: LocatorRegistry, locator: AgentLocator | None) -> int:
    if locator is None:
        return 0
    eps = int(locator.subject_eps or 0)
    if eps:
        return eps
    subject_locator = registry.locators.get(registry.subject_locator_by_id.get(int(locator.subject_id or 0), ""))
    return int(getattr(subject_locator, "subject_eps", 0) or 0)


def _locator_count_match_fact(
    registry: LocatorRegistry,
    locator: AgentLocator | None,
    raw: str,
    issue: dict[str, object] | None = None,
) -> dict[str, object]:
    if locator is None:
        return {"locator": raw, "issue": (issue or {}).get("issue", "locator_not_found")}
    payload: dict[str, object] = {
        "locator": locator.locator,
        "kind": locator.kind,
        "title": locator.title,
    }
    if locator.kind == "local":
        payload.update(
            {
                "file_count": len(locator.file_refs),
                "episode_numbers": [num for num, _ref in list(locator.episode_file_refs)[:24]],
                "markers": list(locator.markers[:8]),
                "representative_labels": list(locator.representative_labels[:4]),
            }
        )
    if locator.kind in {"target_subject", "target_episode", "target_span"}:
        target_item_count = len(locator.item_refs)
        if not target_item_count and locator.episode_start is not None and locator.episode_end is not None:
            target_item_count = abs(int(locator.episode_end) - int(locator.episode_start)) + 1
        payload.update(
            {
                "subject_id": locator.subject_id,
                "subject_eps": _target_subject_eps(registry, locator),
                "episode_start": locator.episode_start,
                "episode_end": locator.episode_end,
                "target_item_count": target_item_count,
            }
        )
    return payload


def _fail_closed_count_matched_target_sibling_repairs(
    registry: LocatorRegistry,
    fail_closed_units: list[dict[str, object]],
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    mapped_units: list[dict[str, object]] = [
        unit
        for unit in feedback_units
        if isinstance(unit, dict) and str(unit.get("outcome") or "").startswith("mapped_")
    ]
    repairs: list[dict[str, object]] = []
    for fail_unit in fail_closed_units:
        for raw_fail_local in list(fail_unit.get("local") or []):
            fail_locator, fail_issue = registry.resolve(str(raw_fail_local))
            if fail_issue or fail_locator is None or fail_locator.kind != "local":
                continue
            fail_count = len(fail_locator.file_refs)
            if fail_count < 2 or not fail_locator.episode_file_refs:
                continue
            for mapped in mapped_units:
                target = str(mapped.get("target") or "").strip()
                if not target:
                    continue
                target_locator, target_issue = registry.resolve(target)
                target_eps = _target_subject_eps(registry, target_locator)
                if target_issue or target_locator is None or not target_eps:
                    continue
                if target_eps != fail_count:
                    continue
                for raw_mapped_local in list(mapped.get("local") or []):
                    mapped_locator, mapped_issue = registry.resolve(str(raw_mapped_local))
                    if mapped_issue or mapped_locator is None or mapped_locator.kind != "local":
                        continue
                    if set(fail_locator.file_refs).intersection(mapped_locator.file_refs):
                        continue
                    if len(mapped_locator.file_refs) >= fail_count:
                        continue
                    if not _shared_distinctive_title_token(fail_locator.title, mapped_locator.title):
                        continue
                    repairs.append(
                        {
                            "issue": "fail_closed_count_matched_target_sibling",
                            "fail_closed_unit": fail_unit.get("unit"),
                            "fail_closed_local": fail_locator.locator,
                            "fail_closed_local_fact": _locator_count_match_fact(registry, fail_locator, fail_locator.locator),
                            "mapped_sibling_unit": mapped.get("unit"),
                            "mapped_sibling_local": mapped_locator.locator,
                            "mapped_sibling_local_fact": _locator_count_match_fact(registry, mapped_locator, mapped_locator.locator),
                            "mapped_sibling_target": target_locator.locator,
                            "mapped_sibling_target_fact": _locator_count_match_fact(registry, target_locator, target_locator.locator),
                            "required": (
                                "A numbered multi-file fail_closed local locator has the same count as the target "
                                "subject currently owned by a smaller same-title-family local sibling. Re-check "
                                "ownership before finishing: the fixed layer is not choosing the semantic owner, but "
                                "the package cannot leave this mechanical count/ownership contradiction unresolved."
                            ),
                        }
                    )
                    break
                if repairs and repairs[-1].get("fail_closed_local") == fail_locator.locator:
                    break
        if len(repairs) >= 8:
            break
    return repairs[:8]


def _excluded_count_matched_uninspected_subject_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
    *,
    inspected_subject_ids: set[int] | None = None,
) -> list[dict[str, object]]:
    inspected_subject_ids = set(inspected_subject_ids or set())
    mapped_subject_ids: set[int] = set()
    local_units: list[tuple[dict[str, object], AgentLocator]] = []
    excluded_units: list[tuple[dict[str, object], AgentLocator]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict):
            continue
        outcome = str(unit.get("outcome") or "")
        if outcome.startswith("mapped_"):
            target_locator, target_issue = registry.resolve(str(unit.get("target") or ""))
            if not target_issue and target_locator is not None and target_locator.subject_id:
                mapped_subject_ids.add(int(target_locator.subject_id))
        for raw_local in list(unit.get("local") or []):
            local_locator, local_issue = registry.resolve(str(raw_local))
            if local_issue or local_locator is None or local_locator.kind != "local":
                continue
            local_units.append((unit, local_locator))
            if outcome in {"supplemental", "bangumi_target_absent", "non_bangumi", "fail_closed"}:
                excluded_units.append((unit, local_locator))

    subjects = [
        locator
        for locator in registry.locators.values()
        if locator.kind == "target_subject"
        and locator.subject_id
        and int(locator.subject_id) not in mapped_subject_ids
        and int(locator.subject_id) not in inspected_subject_ids
        and int(locator.subject_eps or 0) >= 2
        and not _target_episode_numbers_for_subject(registry, int(locator.subject_id or 0))
    ]
    repairs: list[dict[str, object]] = []
    for excluded_unit, excluded_locator in excluded_units:
        local_count = len(excluded_locator.file_refs)
        if local_count < 2 or not excluded_locator.episode_file_refs:
            continue
        local_season_hint = _title_season_number_hint(excluded_locator.title)
        for subject in subjects:
            if int(subject.subject_eps or 0) != local_count:
                continue
            subject_season_hint = _title_season_number_hint(subject.title)
            if local_season_hint is None and subject_season_hint is not None:
                continue
            if local_season_hint is not None and subject_season_hint is not None and local_season_hint != subject_season_hint:
                continue
            bridge: dict[str, object] | None = None
            if _shared_distinctive_title_token(excluded_locator.title, subject.title):
                bridge = {
                    "bridge_kind": "direct_title_overlap",
                    "local": excluded_locator.locator,
                    "target": subject.locator,
                }
            else:
                for _sibling_unit, sibling_locator in local_units:
                    if sibling_locator.locator == excluded_locator.locator:
                        continue
                    if set(sibling_locator.file_refs).intersection(excluded_locator.file_refs):
                        continue
                    if not _shared_distinctive_title_token(excluded_locator.title, sibling_locator.title):
                        continue
                    if not _shared_distinctive_title_token(sibling_locator.title, subject.title):
                        continue
                    bridge = {
                        "bridge_kind": "package_sibling_title_bridge",
                        "sibling_local": sibling_locator.locator,
                        "sibling_title": sibling_locator.title,
                        "sibling_file_count": len(sibling_locator.file_refs),
                    }
                    break
            if bridge is None:
                local_tokens = _locator_distinctive_tokens(excluded_locator)
                subject_tokens = _target_visible_title_tokens(subject)
                special_marker_tokens = {"sp", "special", "specials", "ova", "oad", "oav"}
                if (
                    str(excluded_unit.get("outcome") or "") == "fail_closed"
                    and subject_tokens.intersection(local_tokens.union(special_marker_tokens))
                ):
                    bridge = {
                        "bridge_kind": "same_count_fail_closed_candidate",
                        "local": excluded_locator.locator,
                        "target": subject.locator,
                        "shared_tokens": sorted(subject_tokens.intersection(local_tokens.union(special_marker_tokens)))[:8],
                    }
                else:
                    continue
            repairs.append(
                {
                    "issue": "excluded_count_matched_uninspected_subject",
                    "unit": excluded_unit.get("unit"),
                    "outcome": excluded_unit.get("outcome"),
                    "local": excluded_locator.locator,
                    "local_fact": _locator_count_match_fact(registry, excluded_locator, excluded_locator.locator),
                    "visible_subject": subject.locator,
                    "visible_subject_fact": _locator_count_match_fact(registry, subject, subject.locator),
                    "bridge": bridge,
                    "available_action": f'inspect(["{subject.locator}"], scope=["details","episodes","related"])',
                    "required": (
                        "A numbered excluded local group count-matches a visible target subject whose episode surface "
                        "has not been inspected, and the package contains title evidence linking them. Inspect that "
                        "subject or map/exclude again with the inspected surface; this is an evidence-completeness "
                        "gate, not a semantic target choice."
                    ),
                }
            )
            break
        if len(repairs) >= 8:
            break
    return repairs[:8]


def _excluded_singleton_visible_subject_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    mapped_subject_ids: set[int] = set()
    excluded_units: list[tuple[dict[str, object], AgentLocator]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict):
            continue
        outcome = str(unit.get("outcome") or "")
        if outcome.startswith("mapped_"):
            target_locator, target_issue = registry.resolve(str(unit.get("target") or ""))
            if not target_issue and target_locator is not None and target_locator.subject_id:
                mapped_subject_ids.add(int(target_locator.subject_id))
        if outcome not in {"supplemental", "bangumi_target_absent", "non_bangumi"}:
            continue
        for raw_local in list(unit.get("local") or []):
            local_locator, local_issue = registry.resolve(str(raw_local))
            if local_issue or local_locator is None or local_locator.kind != "local":
                continue
            if len(local_locator.file_refs) != 1 or local_locator.episode_file_refs:
                continue
            if any(marker.casefold().startswith(("cm", "menu", "preview", "pv")) for marker in local_locator.markers):
                continue
            excluded_units.append((unit, local_locator))

    subjects = [
        locator
        for locator in registry.locators.values()
        if locator.kind == "target_subject"
        and locator.subject_id
        and int(locator.subject_id) not in mapped_subject_ids
        and int(locator.subject_eps or 0) >= 2
    ]
    repairs: list[dict[str, object]] = []
    for unit, local_locator in excluded_units:
        local_season_hint = _title_season_number_hint(local_locator.title)
        unseasoned_matching_subject_exists = any(
            _title_season_number_hint(subject.title) is None
            and _shared_distinctive_title_token(local_locator.title, subject.title)
            for subject in subjects
        )
        for subject in subjects:
            subject_season_hint = _title_season_number_hint(subject.title)
            if local_season_hint is None and subject_season_hint is not None and unseasoned_matching_subject_exists:
                continue
            if local_season_hint is not None and subject_season_hint is not None and local_season_hint != subject_season_hint:
                continue
            local_tokens = _distinctive_title_tokens(local_locator.title)
            subject_tokens = _distinctive_title_tokens(subject.title)
            overlap_tokens = local_tokens.intersection(subject_tokens)
            if not overlap_tokens:
                continue
            local_base_tokens = [
                token
                for token in _normalized_locator_match_text(local_locator.title).split()
                if token in local_tokens
            ]
            if len(local_tokens) >= 2 and local_base_tokens and overlap_tokens == {local_base_tokens[0]}:
                continue
            episode_numbers = _target_episode_numbers_for_subject(registry, int(subject.subject_id or 0))
            target_span_examples = _target_span_examples_for_subject(registry, int(subject.subject_id or 0), max_spans=3)
            repair: dict[str, object] = {
                "issue": "excluded_singleton_visible_subject_candidate",
                "unit": unit.get("unit"),
                "outcome": unit.get("outcome"),
                "local": local_locator.locator,
                "local_fact": _locator_count_match_fact(registry, local_locator, local_locator.locator),
                "visible_subject": subject.locator,
                "visible_subject_fact": _locator_count_match_fact(registry, subject, subject.locator),
                "available_target_episode_numbers": episode_numbers[:48],
                "target_span_examples": target_span_examples,
                "required": (
                    "A singleton local title matches a visible multi-episode target subject. Before excluding it, "
                    "inspect/use that target surface and decide whether this is a composite feature, a real extra, "
                    "or target_absent. The fixed layer is exposing the composite candidate; it is not choosing the "
                    "semantic outcome."
                ),
            }
            if episode_numbers:
                repair["suggested_submit_shape"] = {
                    "local": local_locator.locator,
                    "outcome": "mapped_composite_feature if this one file semantically covers the visible target span",
                    "target": target_span_examples[0]["locator"] if target_span_examples else subject.locator,
                }
            else:
                repair["available_action"] = f'inspect(["{subject.locator}"], scope=["details","episodes","related"])'
            repairs.append(repair)
            break
        if len(repairs) >= 8:
            break
    return repairs[:8]


def _mapped_title_season_mismatch_repair(
    registry: LocatorRegistry,
    *,
    local_locators: list[str],
    target: str,
) -> dict[str, object] | None:
    target_locator, target_issue = registry.resolve(target)
    if target_issue or target_locator is None or not target_locator.subject_id:
        return None
    target_subject_locator = registry.locators.get(registry.subject_locator_by_id.get(int(target_locator.subject_id), ""))
    target_title = getattr(target_subject_locator, "title", "") or target_locator.title
    target_season = _title_season_number_hint(target_title)
    if target_season is None:
        return None
    local_facts: list[dict[str, object]] = []
    local_titles: list[str] = []
    local_seasons: set[int] = set()
    for raw_local in local_locators:
        local_locator, local_issue = registry.resolve(raw_local)
        if local_issue or local_locator is None or local_locator.kind != "local":
            continue
        local_facts.append(_locator_count_match_fact(registry, local_locator, local_locator.locator))
        local_titles.append(local_locator.title)
        local_season = _title_season_number_hint(local_locator.title)
        if local_season is not None:
            local_seasons.add(local_season)
    if not local_titles or target_season in local_seasons:
        return None
    seasonless_query = _seasonless_title_query(target_title)
    search_queries_to_try = [seasonless_query] if seasonless_query else []
    if local_seasons:
        return {
            "issue": "mapped_title_season_mismatch",
            "local": local_locators,
            "local_facts": local_facts,
            "target": target_locator.locator,
            "target_fact": _locator_count_match_fact(registry, target_locator, target_locator.locator),
            "target_subject": getattr(target_subject_locator, "locator", ""),
            "target_subject_title": target_title,
            "local_season_hints": sorted(local_seasons),
            "target_season_hint": target_season,
            "search_queries_to_try": search_queries_to_try,
            "required": (
                "The mapped target subject season suffix conflicts with the local title season suffix. "
                "Change target or explain via a different visible target; the fixed layer is checking title consistency only."
            ),
        }

    alternates: list[dict[str, object]] = []
    target_base_tokens = _distinctive_title_tokens(seasonless_query or target_title)
    target_required_overlap = 1 if len(target_base_tokens) <= 1 else 2
    for locator in registry.locators.values():
        if locator.kind != "target_subject" or not locator.subject_id:
            continue
        if int(locator.subject_id) == int(target_locator.subject_id):
            continue
        if _title_season_number_hint(locator.title) is not None:
            continue
        alternate_tokens = _distinctive_title_tokens(locator.title)
        if target_base_tokens and _title_family_overlap_score(target_base_tokens, alternate_tokens) < target_required_overlap:
            continue
        alternates.append(
            {
                "target": locator.locator,
                "title": locator.title,
                "eps": int(locator.subject_eps or 0),
                "available_target_episode_numbers": _target_episode_numbers_for_subject(registry, int(locator.subject_id or 0))[:48],
                "available_action": f'inspect(["{locator.locator}"], scope=["details","episodes","related"])',
            }
        )
    if not alternates and not search_queries_to_try:
        return None
    return {
        "issue": "mapped_title_season_mismatch",
        "local": local_locators,
        "local_facts": local_facts,
        "target": target_locator.locator,
        "target_fact": _locator_count_match_fact(registry, target_locator, target_locator.locator),
        "target_subject": getattr(target_subject_locator, "locator", ""),
        "target_subject_title": target_title,
        "local_season_hints": [],
        "target_season_hint": target_season,
        "visible_unseasoned_alternates": alternates[:6],
        "search_queries_to_try": search_queries_to_try,
        "required": (
            "The local title has no season suffix, but the mapped target subject does. Re-check target ownership; "
            "inspect a listed unseasoned same-title-family subject or run search_queries_to_try for the seasonless "
            "target title. The fixed layer is not choosing the target."
        ),
    }


def _excluded_main_with_mapped_title_sibling_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    mapped_units: list[dict[str, object]] = []
    excluded_units: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict):
            continue
        outcome = str(unit.get("outcome") or "")
        if outcome.startswith("mapped_"):
            mapped_units.append(unit)
        elif outcome in {"supplemental", "bangumi_target_absent", "non_bangumi"}:
            excluded_units.append(unit)

    repairs: list[dict[str, object]] = []
    for excluded in excluded_units:
        for raw_local in list(excluded.get("local") or []):
            excluded_locator, excluded_issue = registry.resolve(str(raw_local))
            if excluded_issue or excluded_locator is None or excluded_locator.kind != "local":
                continue
            excluded_category = excluded_locator.locator.rsplit("/", 1)[-1]
            if excluded_category not in {"main", "main-episodes"} or len(excluded_locator.file_refs) < 4:
                continue
            for mapped in mapped_units:
                target = str(mapped.get("target") or "").strip()
                if not target:
                    continue
                for mapped_raw_local in list(mapped.get("local") or []):
                    mapped_locator, mapped_issue = registry.resolve(str(mapped_raw_local))
                    if mapped_issue or mapped_locator is None or mapped_locator.kind != "local":
                        continue
                    if set(excluded_locator.file_refs).intersection(mapped_locator.file_refs):
                        continue
                    if len(mapped_locator.file_refs) >= len(excluded_locator.file_refs):
                        continue
                    if not _title_family_overlap(excluded_locator.title, mapped_locator.title):
                        continue
                    repairs.append(
                        {
                            "issue": "excluded_main_locator_with_mapped_title_sibling",
                            "excluded_unit": excluded.get("unit"),
                            "excluded_outcome": excluded.get("outcome"),
                            "excluded_local": excluded_locator.locator,
                            "excluded_title": excluded_locator.title,
                            "excluded_file_count": len(excluded_locator.file_refs),
                            "mapped_sibling_unit": mapped.get("unit"),
                            "mapped_sibling_local": mapped_locator.locator,
                            "mapped_sibling_title": mapped_locator.title,
                            "mapped_sibling_file_count": len(mapped_locator.file_refs),
                            "mapped_sibling_target": target,
                            "required": (
                                "A multi-file main local locator is excluded while a smaller same-title-family local "
                                "locator is mapped. Re-check target ownership: do not let a special/half/extra-style "
                                "local slice take the target that belongs to the main span. Map the main span if it is "
                                "the semantic owner, or provide a concrete reason why the main span is genuinely "
                                "supplemental/non_bangumi/target_absent."
                            ),
                        }
                    )
                    break
                if repairs and repairs[-1].get("excluded_local") == excluded_locator.locator:
                    break
        if len(repairs) >= 8:
            break
    return repairs[:8]


_CONCRETE_EXTRA_REASON_MARKERS = {
    "alternate",
    "bonus",
    "cast",
    "cm",
    "companion",
    "compilation",
    "copy",
    "digest",
    "duplicate",
    "extra",
    "interview",
    "menu",
    "nced",
    "ncop",
    "oad",
    "ova",
    "packaging",
    "preview",
    "pv",
    "recap",
    "special",
    "summary",
    "theater",
    "trailer",
}


def _has_concrete_extra_reason(*parts: object) -> bool:
    text = " ".join(str(part or "") for part in parts).casefold()
    if not text:
        return False
    return any(marker in text for marker in _CONCRETE_EXTRA_REASON_MARKERS)


def _has_concrete_non_owning_reason(*parts: object) -> bool:
    text = " ".join(str(part or "") for part in parts).casefold()
    if not text:
        return False
    markers = {
        "alternate",
        "cast",
        "cm",
        "companion",
        "copy",
        "interview",
        "menu",
        "nced",
        "ncop",
        "packaging",
        "preview",
        "pv",
        "trailer",
    }
    return any(marker in text for marker in markers)


def _has_negative_target_absence_reason(*parts: object) -> bool:
    text = " ".join(str(part or "") for part in parts).casefold()
    if not text:
        return False
    markers = (
        "target_absent",
        "no bangumi",
        "no corresponding",
        "no exact",
        "no matching",
        "no safe",
        "no usable",
        "no visible",
        "not exposed",
        "not found",
        "not listed",
        "absent",
        "missing target",
        "without target",
        "without a target",
        "without corresponding",
        "without matching",
        "没有",
        "未找到",
        "无对应",
        "不存在",
    )
    return any(marker in text for marker in markers)


def _numbered_special_negative_evidence_sufficient(
    registry: LocatorRegistry,
    unit: dict[str, object],
    support_target_locators: list[AgentLocator],
    *,
    inspected_subject_ids: set[int],
    local_bridge_tokens: set[str],
) -> bool:
    if not _has_negative_target_absence_reason(
        unit.get("unit"),
        unit.get("reason"),
        " ".join(str(item) for item in list(unit.get("support") or [])),
    ):
        return False
    return bool(
        _numbered_special_negative_evidence_support_candidates(
            registry,
            support_target_locators,
            inspected_subject_ids=inspected_subject_ids,
            local_bridge_tokens=local_bridge_tokens,
        )
    )


def _numbered_special_negative_evidence_support_candidates(
    registry: LocatorRegistry,
    support_target_locators: list[AgentLocator],
    *,
    inspected_subject_ids: set[int],
    local_bridge_tokens: set[str],
    limit: int = 6,
) -> list[dict[str, object]]:
    special_marker_tokens = {"sp", "special", "specials", "ova", "oad", "oav"}
    candidate_locators = list(support_target_locators)
    if not candidate_locators:
        subject_ids_with_visible_items = {
            int(key_subject_id)
            for key_subject_id, _sort in registry.item_ref_by_subject_sort
        }
        candidate_locators.extend(
            locator
            for locator in registry.locators.values()
            if locator.kind == "target_subject"
            and int(locator.subject_id or 0) in inspected_subject_ids.union(subject_ids_with_visible_items)
        )
    candidates: list[dict[str, object]] = []
    seen_subject_ids: set[int] = set()
    for support_locator in candidate_locators:
        support_subject = _target_subject_locator_for(registry, support_locator)
        subject_id = int(getattr(support_subject, "subject_id", 0) or 0)
        visible_episode_surface = any(
            int(key_subject_id) == subject_id
            for key_subject_id, _sort in registry.item_ref_by_subject_sort
        )
        if not subject_id or subject_id in seen_subject_ids or (
            subject_id not in inspected_subject_ids and not visible_episode_surface
        ):
            continue
        support_tokens = (
            _target_visible_title_tokens(support_subject)
            .union(_target_distinctive_tokens(support_locator))
            .union(_target_query_distinctive_tokens(support_subject))
        )
        shared_tokens = support_tokens.intersection(local_bridge_tokens)
        special_tokens = support_tokens.intersection(special_marker_tokens)
        if not shared_tokens and not special_tokens:
            continue
        seen_subject_ids.add(subject_id)
        candidates.append(
            {
                "target": support_subject.locator,
                "title": support_subject.title,
                "subject_id": subject_id,
                "subject_eps": int(getattr(support_subject, "subject_eps", 0) or 0),
                "surface_evidence": "seen_detail_refs" if subject_id in inspected_subject_ids else "visible_episode_items",
                "shared_title_tokens": sorted(shared_tokens)[:8],
                "shared_special_tokens": sorted(special_tokens)[:8],
                "submit_support": [support_subject.locator],
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def _has_contextual_packaging_extra_reason(locator: AgentLocator, *parts: object) -> bool:
    text = " ".join(
        [
            locator.title,
            " ".join(locator.markers),
            " ".join(locator.representative_labels[:4]),
            *[str(part or "") for part in parts],
        ]
    ).casefold()
    if not text:
        return False
    in_sp_packaging = bool(re.search(r"(?:^|[\\/])sps?(?:[\\/]|$)|\bsps?\b", text))
    if not in_sp_packaging:
        return False
    return bool(
        re.search(r"(?:^|[\s._\-\[\]()])iv(?:$|[\s._\-\[\]()])", text)
    )


def _contextual_packaging_extra_tokens(locator: AgentLocator) -> set[str]:
    text = " ".join(
        [
            locator.title,
            " ".join(locator.markers),
            " ".join(locator.representative_labels[:4]),
        ]
    ).casefold()
    in_sp_packaging = bool(re.search(r"(?:^|[\\/])sps?(?:[\\/]|$)|\bsps?\b", text))
    if not in_sp_packaging:
        return set()
    tokens: set[str] = set()
    if re.search(r"(?:^|[\s._\-\[\]()])iv(?:$|[\s._\-\[\]()])", text):
        tokens.add("iv")
    if re.search(r"(?:^|[\s._\-\[\]()])sp\d{1,3}(?:$|[\s._\-\[\]()])", text):
        tokens.add("sp")
    return tokens


def _supplemental_main_episode_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") != "supplemental":
            continue
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if issue or locator is None or locator.kind != "local":
                continue
            category = locator.locator.rsplit("/", 1)[-1]
            multi_file_regular_main = category == "main-episodes" and len(locator.file_refs) >= 4
            singleton_main_feature = category == "main" and len(locator.file_refs) == 1
            if not (multi_file_regular_main or singleton_main_feature):
                continue
            if _has_concrete_non_owning_reason(
                unit.get("unit"),
                unit.get("reason"),
                locator.title,
                " ".join(locator.markers),
                " ".join(locator.representative_labels[:3]),
            ):
                continue
            if _has_contextual_packaging_extra_reason(locator, unit.get("unit"), unit.get("reason")):
                continue
            repairs.append(
                {
                    "issue": "supplemental_main_episodes_without_concrete_extra_reason",
                    "unit": unit.get("unit"),
                    "local": locator.locator,
                    "title": locator.title,
                    "file_count": len(locator.file_refs),
                    "category": category,
                    "episode_range": {
                        "start": locator.episode_file_refs[0][0] if locator.episode_file_refs else None,
                        "end": locator.episode_file_refs[-1][0] if locator.episode_file_refs else None,
                    },
                    "representative_labels": list(locator.representative_labels[:3]),
                    "required": (
                        "A main/main-episodes locator cannot be cleared as supplemental without a concrete "
                        "extra/duplicate/recap/packaging reason. Map it to a visible target, inspect/search the "
                        "missing target surface, choose target_absent/non_bangumi with a concrete semantic reason, "
                        "or fail_closed if it remains unresolved."
                    ),
                }
            )
            break
        if len(repairs) >= 8:
            break
    return repairs


def _numbered_special_exclusion_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
    *,
    inspected_subject_ids: set[int] | None = None,
) -> list[dict[str, object]]:
    inspected_subject_ids = set(inspected_subject_ids or set())
    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") not in {"supplemental", "bangumi_target_absent", "non_bangumi", "fail_closed"}:
            continue
        support_target_locators: list[AgentLocator] = []
        for raw_support in list(unit.get("support") or []):
            support_locator, support_issue = registry.resolve(str(raw_support))
            if not support_issue and support_locator is not None and support_locator.kind in {"target_subject", "target_episode", "target_span"}:
                support_target_locators.append(support_locator)
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if issue or locator is None or locator.kind != "local":
                continue
            parent_locator = registry.locators.get(_episode_slice_parent_locator(locator.locator)) or locator
            category = parent_locator.locator.rsplit("/", 1)[-1]
            if category != "special-marker" or not locator.episode_file_refs:
                continue
            label_text = " ".join(
                [
                    locator.title,
                    parent_locator.title,
                    " ".join(locator.markers),
                    " ".join(parent_locator.markers),
                    " ".join(locator.representative_labels[:6]),
                    " ".join(parent_locator.representative_labels[:6]),
                    str(unit.get("unit") or ""),
                    str(unit.get("reason") or ""),
                ]
            )
            if not re.search(r"(?i)(?:^|[\s._\-\[\]()])sp\d{1,3}(?:$|[\s._\-\[\]()])", label_text):
                continue
            local_bridge_tokens = _locator_distinctive_tokens(locator).union(_locator_distinctive_tokens(parent_locator))
            special_marker_tokens = {"sp", "special", "specials", "ova", "oad", "oav"}
            support_sufficient = False
            for support_locator in support_target_locators:
                support_subject = _target_subject_locator_for(registry, support_locator)
                support_tokens = _target_visible_title_tokens(support_subject).union(_target_distinctive_tokens(support_locator))
                if support_tokens.intersection(special_marker_tokens):
                    support_sufficient = True
                    break
            if support_sufficient:
                continue
            same_count_subjects: list[dict[str, object]] = []
            for target in registry.locators.values():
                if target.kind != "target_subject":
                    continue
                if int(target.subject_id or 0) in inspected_subject_ids:
                    continue
                if int(target.subject_eps or 0) != len(locator.file_refs):
                    continue
                target_tokens = _target_visible_title_tokens(target)
                if not target_tokens.intersection(local_bridge_tokens) and not target_tokens.intersection(special_marker_tokens):
                    continue
                same_count_subjects.append(
                    {
                        "target": target.locator,
                        "title": target.title,
                        "subject_id": target.subject_id,
                        "subject_eps": target.subject_eps,
                        "available_action": f'inspect(["{target.locator}"], scope=["details","episodes","related"])',
                    }
                )
                if len(same_count_subjects) >= 6:
                    break
            negative_evidence_support_candidates = _numbered_special_negative_evidence_support_candidates(
                registry,
                support_target_locators,
                inspected_subject_ids=inspected_subject_ids,
                local_bridge_tokens=local_bridge_tokens,
            )
            if (
                not same_count_subjects
                and negative_evidence_support_candidates
                and _has_negative_target_absence_reason(
                    unit.get("unit"),
                    unit.get("reason"),
                    " ".join(str(item) for item in list(unit.get("support") or [])),
                )
            ):
                continue
            repairs.append(
                {
                    "issue": "numbered_special_exclusion_needs_target_evidence",
                    "shape_issue": (
                        "negative_evidence_shape_missing"
                        if negative_evidence_support_candidates and not same_count_subjects
                        else "target_evidence_missing"
                    ),
                    "unit": unit.get("unit"),
                    "outcome": unit.get("outcome"),
                    "invalid_current_outcome": unit.get("outcome"),
                    "allowed_without_target_evidence": "fail_closed",
                    "allowed_with_negative_target_absence_evidence": [
                        "bangumi_target_absent",
                        "supplemental",
                        "non_bangumi",
                    ],
                    "negative_target_absence_support_candidates": negative_evidence_support_candidates,
                    "negative_target_absence_submit_shape": {
                        "local": locator.locator,
                        "outcome": "bangumi_target_absent, supplemental, or non_bangumi when that is your semantic conclusion",
                        "support": (
                            "include one negative_target_absence_support_candidates[*].target value if provided; "
                            "otherwise include an inspected same-series/related target locator whose surface did not "
                            "expose a corresponding SP/OVA/OAD item. The previous rejection means support/reason shape "
                            "was missing, not that target_absent/supplemental is forbidden"
                        ),
                        "reason": (
                            "state the finite target-side check, e.g. no corresponding Bangumi SP/OAD item is visible "
                            "after inspecting the same-series/related target surface; do not express that same "
                            "negative-target conclusion as fail_closed"
                        ),
                    },
                    "local": locator.locator,
                    "parent_local": parent_locator.locator,
                    "file_count": len(locator.file_refs),
                    "parent_file_count": len(parent_locator.file_refs),
                    "episode_numbers": [number for number, _ref in locator.episode_file_refs[:12]],
                    "representative_labels": list(locator.representative_labels[:6]),
                    "same_count_visible_subjects": same_count_subjects,
                    "required": (
                        "The current supplemental/target_absent/non_bangumi outcome for this numbered SP group is "
                        "not mechanically acceptable without target-side support or negative target-absence evidence. "
                        "Search/inspect a special/OVA/OAD/SP target surface if same_count_visible_subjects names one; "
                        "map it if it has a Bangumi owner. If no such target remains visible after inspecting the "
                        "same-series/related surface, submit target_absent/supplemental/non_bangumi with that "
                        "inspected target locator in support and a concrete no-corresponding-target reason. If "
                        "negative_target_absence_support_candidates is non-empty, use one of those target locators "
                        "as support for that negative evidence. Use "
                        "fail_closed only when the local SP group is still semantically ambiguous, not merely because "
                        "a positive Bangumi SP target was absent. Do not resubmit the same unsupported exclusion "
                        "outcome. The fixed layer is checking evidence support, not choosing the target."
                    ),
                }
            )
            break
        if len(repairs) >= 8:
            break
    return repairs


def _fail_closed_negative_target_absence_repairs(
    registry: LocatorRegistry,
    fail_closed_units: list[dict[str, object]],
    *,
    inspected_subject_ids: set[int] | None = None,
) -> list[dict[str, object]]:
    inspected_subject_ids = set(inspected_subject_ids or set())
    repairs: list[dict[str, object]] = []
    for unit in fail_closed_units:
        if not isinstance(unit, dict):
            continue
        reason = str(unit.get("reason") or "")
        if not _has_negative_target_absence_reason(unit.get("unit"), reason):
            continue
        support_target_locators: list[AgentLocator] = []
        for raw_support in list(unit.get("support") or []):
            support_locator, support_issue = registry.resolve(str(raw_support))
            if (
                not support_issue
                and support_locator is not None
                and support_locator.kind in {"target_subject", "target_episode", "target_span"}
            ):
                support_target_locators.append(support_locator)
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if issue or locator is None or locator.kind != "local":
                continue
            parent_locator = registry.locators.get(_episode_slice_parent_locator(locator.locator)) or locator
            category = parent_locator.locator.rsplit("/", 1)[-1]
            if category != "special-marker" or not locator.episode_file_refs:
                continue
            label_text = " ".join(
                [
                    locator.title,
                    parent_locator.title,
                    " ".join(locator.markers),
                    " ".join(parent_locator.markers),
                    " ".join(locator.representative_labels[:6]),
                    " ".join(parent_locator.representative_labels[:6]),
                    str(unit.get("unit") or ""),
                    reason,
                ]
            )
            if not re.search(r"(?i)(?:^|[\s._\-\[\]()])sp\d{1,3}(?:$|[\s._\-\[\]()])", label_text):
                continue
            local_bridge_tokens = _locator_distinctive_tokens(locator).union(_locator_distinctive_tokens(parent_locator))
            support_candidates = _numbered_special_negative_evidence_support_candidates(
                registry,
                support_target_locators,
                inspected_subject_ids=inspected_subject_ids,
                local_bridge_tokens=local_bridge_tokens,
            )
            if not support_candidates:
                continue
            repairs.append(
                {
                    "issue": "fail_closed_negative_target_absence_outcome_inconsistent",
                    "unit": unit.get("unit"),
                    "local": locator.locator,
                    "parent_local": parent_locator.locator,
                    "file_count": len(locator.file_refs),
                    "reason": reason,
                    "negative_target_absence_support_candidates": support_candidates,
                    "allowed_with_negative_target_absence_evidence": [
                        "bangumi_target_absent",
                        "supplemental",
                        "non_bangumi",
                    ],
                    "negative_target_absence_submit_shape": {
                        "local": locator.locator,
                        "outcome": "bangumi_target_absent, supplemental, or non_bangumi when that is your semantic conclusion",
                        "support": "include one negative_target_absence_support_candidates[*].target value",
                        "reason": (
                            "state that a finite same-series/related Bangumi target-side check exposed no "
                            "corresponding SP/OVA/OAD item. If you still believe fail_closed is correct, rewrite "
                            "the reason to name the unresolved semantic ambiguity instead of only target absence."
                        ),
                    },
                    "required": (
                        "This fail_closed unit's reason already states a negative target-absence conclusion, and "
                        "there is inspected same-series/related target evidence available. That is not an unresolved "
                        "evidence gap by itself. If your semantic judgment is that the local numbered SP group is "
                        "bonus/extra or target_absent, resubmit it as target_absent/supplemental/non_bangumi with "
                        "one listed support target and a concrete no-corresponding-target reason. Use fail_closed "
                        "only if you can state a remaining semantic ambiguity beyond positive Bangumi SP target absence. "
                        "The fixed layer is checking outcome/reason consistency and support shape, not choosing the outcome."
                    ),
                }
            )
            break
        if len(repairs) >= 8:
            break
    return repairs


_DISTINCTIVE_TOKEN_STOPWORDS = {
    "anime",
    "and",
    "bangumi",
    "bd",
    "bdrip",
    "episode",
    "episodes",
    "file",
    "girls",
    "group",
    "hen",
    "main",
    "movie",
    "of",
    "ova",
    "special",
    "season",
    "show",
    "target",
    "the",
    "tokubetsu",
    "vol",
    "zoku",
}


def _distinctive_tokens(*parts: object) -> set[str]:
    expanded_parts: list[str] = []
    for part in parts:
        value = str(part or "")
        if not value:
            continue
        expanded_parts.append(value)
        expanded_parts.extend(_known_alias_query_variants(value))
        hiragana_candidate = re.sub(r"\s+", " ", value.replace("-", " ")).strip()
        hiragana = _romaji_to_hiragana_phrase(value)
        if hiragana and len(hiragana_candidate.split()) <= 2:
            expanded_parts.append(hiragana)
    text = _normalized_locator_match_text(" ".join(expanded_parts))
    text = QUERY_NOISE_TOKEN_RE.sub(" ", text)
    text = re.sub(r"\b\d+(?:flac|aac|ac3|ch)\b", " ", text)
    tokens = {
        token
        for token in text.split()
        if (
            (len(token) >= 3 or (len(token) >= 2 and re.search(r"[\u3040-\u30ff\u3400-\u9fff]", token)))
            and not token.isdigit()
            and token not in _DISTINCTIVE_TOKEN_STOPWORDS
        )
    }
    for token in list(tokens):
        for latin_fragment in re.findall(r"[a-z]{3,}", token):
            if latin_fragment not in _DISTINCTIVE_TOKEN_STOPWORDS:
                tokens.add(latin_fragment)
        marker_match = re.fullmatch(r"(sp|ova|oad|oav|special)\d{1,3}", token)
        if marker_match:
            tokens.add(marker_match.group(1))
    return tokens


def _locator_distinctive_tokens(locator: AgentLocator) -> set[str]:
    return _distinctive_tokens(locator.title, " ".join(locator.representative_labels[:4]))


def _target_distinctive_tokens(locator: AgentLocator) -> set[str]:
    return _distinctive_tokens(locator.locator, locator.title, " ".join(locator.markers[:8]))


def _target_query_distinctive_tokens(locator: AgentLocator) -> set[str]:
    return _distinctive_tokens(" ".join(locator.query_markers))


def _target_visible_title_tokens(locator: AgentLocator) -> set[str]:
    return _distinctive_tokens(locator.locator, locator.title, " ".join(locator.markers[:8]))


def _singleton_target_alias_owner_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    mapped_units = [
        unit for unit in feedback_units
        if isinstance(unit, dict) and str(unit.get("outcome") or "").startswith("mapped_")
    ]
    excluded_units = [
        unit for unit in feedback_units
        if isinstance(unit, dict) and unit.get("outcome") in {"supplemental", "bangumi_target_absent", "non_bangumi", "fail_closed"}
    ]
    repairs: list[dict[str, object]] = []
    for mapped in mapped_units:
        target_locator, target_issue = registry.resolve(str(mapped.get("target") or ""))
        if target_issue or target_locator is None or target_locator.kind not in {"target_subject", "target_episode"}:
            continue
        target_title_tokens = _target_distinctive_tokens(target_locator)
        target_query_tokens = _target_query_distinctive_tokens(target_locator)
        target_tokens = target_title_tokens.union(target_query_tokens)
        if len(target_tokens) < 2:
            continue
        mapped_locators: list[AgentLocator] = []
        for raw_local in list(mapped.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                mapped_locators.append(locator)
        if sum(len(locator.file_refs) for locator in mapped_locators) != 1:
            continue
        mapped_tokens = set().union(*[_locator_distinctive_tokens(locator) for locator in mapped_locators]) if mapped_locators else set()
        mapped_score = len(target_tokens.intersection(mapped_tokens))
        for excluded in excluded_units:
            excluded_locators: list[AgentLocator] = []
            for raw_local in list(excluded.get("local") or []):
                locator, issue = registry.resolve(str(raw_local))
                if not issue and locator is not None and locator.kind == "local":
                    excluded_locators.append(locator)
            if sum(len(locator.file_refs) for locator in excluded_locators) != 1:
                continue
            excluded_tokens = set().union(*[_locator_distinctive_tokens(locator) for locator in excluded_locators]) if excluded_locators else set()
            excluded_score = len(target_tokens.intersection(excluded_tokens))
            if excluded_score < 2 or excluded_score < mapped_score + 2:
                continue
            repairs.append(
                {
                    "issue": "singleton_target_alias_matches_excluded_local_better",
                    "mapped_unit": mapped.get("unit"),
                    "mapped_local": [locator.locator for locator in mapped_locators],
                    "mapped_title_tokens": sorted(mapped_tokens),
                    "mapped_overlap_score": mapped_score,
                    "excluded_unit": excluded.get("unit"),
                    "excluded_local": [locator.locator for locator in excluded_locators],
                    "excluded_title_tokens": sorted(excluded_tokens),
                    "excluded_overlap_score": excluded_score,
                    "target": target_locator.locator,
                    "target_title": target_locator.title,
                    "target_title_aliases": list(target_locator.markers[:8]),
                    "target_source_query_texts": list(target_locator.query_markers[:4]),
                    "target_title_tokens": sorted(target_title_tokens),
                    "target_query_tokens": sorted(target_query_tokens),
                    "target_tokens": sorted(target_tokens),
                    "required": (
                        "Visible target title aliases or source-query provenance match another singleton local more strongly "
                        "than the mapped singleton local. Re-check semantic ownership: keep the matching named local "
                        "on this target, search/inspect a different target for the other named local, or fail_closed "
                        "if the other local remains unresolved."
                    ),
                }
            )
            break
        if len(repairs) >= 8:
            break
    return repairs


def _target_subject_locator_for(registry: LocatorRegistry, locator: AgentLocator) -> AgentLocator:
    if locator.kind == "target_subject":
        return locator
    if locator.subject_id:
        subject_locator = registry.locators.get(registry.subject_locator_by_id.get(int(locator.subject_id), "") or "")
        if subject_locator is not None:
            return subject_locator
    return locator


def _reason_mentions_visible_target_alias(unit: dict[str, object], target_locator: AgentLocator) -> bool:
    raw_reason_text = " ".join(
        str(value or "")
        for value in [unit.get("unit"), unit.get("reason")]
    )
    if re.search(
        r"(?i)\b(?:no|not|does\s+not|do\s+not|without|lacks?|lack(?:ing)?)\b\s+"
        r"(?:lexical|title|visible\s+title|token|word|name)?\s*"
        r"(?:overlap|match|bridge)",
        raw_reason_text,
    ):
        return False
    if re.search(
        r"(?i)\bonly\s+(?:visible|plausible|matched|available|candidate)\b.*\b(?:target|subject|evidence|season)\b",
        raw_reason_text,
    ):
        return False
    if re.search(r"(?i)\bselected\s+title\s+bridge\b", raw_reason_text):
        return False
    reason_text = _normalized_locator_match_text(
        raw_reason_text
    )
    if not reason_text:
        return False
    for marker in [target_locator.title, *list(target_locator.markers)]:
        marker_text = _normalized_locator_match_text(str(marker or ""))
        if len(marker_text) >= 3 and marker_text in reason_text:
            return True
    return False


def _mapped_target_title_bridge_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
    *,
    searched_query_variant_keys: set[str] | None = None,
) -> list[dict[str, object]]:
    searched_query_variant_keys = {str(item).casefold() for item in (searched_query_variant_keys or set()) if str(item)}
    repairs: list[dict[str, object]] = []
    excluded_packaging_title_tokens: set[str] = set()
    for excluded in feedback_units:
        if not isinstance(excluded, dict) or excluded.get("outcome") not in {"supplemental", "bangumi_target_absent", "non_bangumi"}:
            continue
        reason_text = str(excluded.get("unit") or "") + " " + str(excluded.get("reason") or "")
        if not re.search(r"(?i)\b(?:compilation|copy|duplicate|alternate)\b", reason_text):
            continue
        for raw_local in list(excluded.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                excluded_packaging_title_tokens.update(_locator_distinctive_tokens(locator))
    for unit in feedback_units:
        if not isinstance(unit, dict) or not str(unit.get("outcome") or "").startswith("mapped_"):
            continue
        target_locator, target_issue = registry.resolve(str(unit.get("target") or ""))
        if target_issue or target_locator is None or target_locator.kind not in {"target_subject", "target_episode", "target_span"}:
            continue
        target_subject = _target_subject_locator_for(registry, target_locator)
        target_subject_tokens = _target_visible_title_tokens(target_subject)
        target_item_tokens = (
            _target_visible_title_tokens(target_locator)
            if target_locator.locator != target_subject.locator
            else set()
        )
        target_tokens = target_subject_tokens.union(target_item_tokens)
        if not target_tokens:
            continue
        local_locators: list[AgentLocator] = []
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                local_locators.append(locator)
        if not local_locators:
            continue
        local_file_count = sum(len(locator.file_refs) for locator in local_locators)
        target_subject_total = int(target_subject.subject_eps or 0)
        local_has_special_marker = any(
            (registry.locators.get(_episode_slice_parent_locator(locator.locator)) or locator).locator.rsplit("/", 1)[-1]
            == "special-marker"
            for locator in local_locators
        )
        if target_subject_total and target_subject_total == local_file_count and local_file_count > 1 and not local_has_special_marker:
            continue
        local_tokens = set().union(*[_locator_distinctive_tokens(locator) for locator in local_locators])
        if not local_tokens or target_tokens.intersection(local_tokens):
            continue
        target_query_tokens = _target_query_distinctive_tokens(target_subject)
        query_bridge_tokens = sorted(local_tokens.intersection(target_query_tokens))
        unbridged_query_tokens = sorted(local_tokens - target_tokens - target_query_tokens)
        if len(query_bridge_tokens) >= 2 and not (local_file_count == 1 and len(unbridged_query_tokens) >= 2):
            continue
        if local_has_special_marker and target_tokens.intersection(excluded_packaging_title_tokens):
            continue
        query_hints: list[str] = []
        seen_hints: set[str] = set()
        for locator in local_locators:
            for hint in _query_hints_for_locator(locator, limit=8):
                folded = hint.casefold()
                if folded in seen_hints:
                    continue
                seen_hints.add(folded)
                query_hints.append(hint)
        unsearched_hints = [
            hint
            for hint in query_hints
            if not _search_hint_was_executed(hint, searched_query_variant_keys)
        ]
        repairs.append(
            {
                "issue": "mapped_target_title_bridge_missing",
                "unit": unit.get("unit"),
                "local": [locator.locator for locator in local_locators],
                "local_title_tokens": sorted(local_tokens),
                "target": target_locator.locator,
                "target_subject": target_subject.locator,
                "target_title": target_subject.title,
                "target_title_aliases": list(target_subject.markers[:8]),
                "selected_target_title": target_locator.title,
                "selected_target_title_aliases": list(target_locator.markers[:8]),
                "target_source_query_texts": list(target_subject.query_markers[:4]),
                "target_query_bridge_tokens": query_bridge_tokens,
                "unbridged_local_title_tokens": unbridged_query_tokens[:12],
                "target_title_tokens": sorted(target_subject_tokens),
                "selected_target_title_tokens": sorted(target_item_tokens),
                "search_queries_to_try": unsearched_hints[:8],
                "searched_query_count": len(searched_query_variant_keys),
                "required": (
                    "The selected target's visible title aliases have no lexical overlap with the local title/labels. "
                    "A clean source query that overlaps the local title can also act as provenance, but this target "
                    "does not have enough local/source-query overlap. Do not use the selected target title itself "
                    "as the bridge: search/inspect a better title alias, choose a target whose visible title facts "
                    "or source-query provenance bridge to the local title, or fail_closed if the visible evidence "
                    "remains insufficient. This is a support/provenance check; the fixed layer is not choosing the target."
                ),
            }
        )
        if len(repairs) >= 8:
            break
    return repairs


def _mapped_contextual_packaging_extra_target_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    def target_supports_extra_tokens(extra_tokens: set[str], target_tokens: set[str]) -> bool:
        if extra_tokens.intersection(target_tokens):
            return True
        if "sp" in extra_tokens and target_tokens.intersection({"sp", "special", "ova", "oad", "oav"}):
            return True
        if "iv" in extra_tokens and target_tokens.intersection({"iv", "interview"}):
            return True
        return False

    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or not str(unit.get("outcome") or "").startswith("mapped_"):
            continue
        local_locators: list[AgentLocator] = []
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                local_locators.append(locator)
        if sum(len(locator.file_refs) for locator in local_locators) != 1:
            continue
        extra_tokens = set().union(*[_contextual_packaging_extra_tokens(locator) for locator in local_locators]) if local_locators else set()
        if not extra_tokens:
            continue
        target_locator, target_issue = registry.resolve(str(unit.get("target") or ""))
        if target_issue or target_locator is None or target_locator.kind not in {"target_subject", "target_episode", "target_span"}:
            continue
        target_subject = _target_subject_locator_for(registry, target_locator)
        target_tokens = _target_visible_title_tokens(target_subject).union(_target_distinctive_tokens(target_locator))
        if target_supports_extra_tokens(extra_tokens, target_tokens):
            continue
        repairs.append(
            {
                "issue": "mapped_packaging_extra_marker_without_specific_target",
                "unit": unit.get("unit"),
                "local": [locator.locator for locator in local_locators],
                "local_packaging_extra_tokens": sorted(extra_tokens),
                "representative_labels": [
                    label
                    for locator in local_locators
                    for label in list(locator.representative_labels[:3])
                ][:8],
                "target": target_locator.locator,
                "target_subject": target_subject.locator,
                "target_title": target_subject.title,
                "target_title_aliases": list(target_subject.markers[:8]),
                "target_title_tokens": sorted(target_tokens),
                "required": (
                    "The local unit is a packaging/SP extra marker, but the selected target title facts do not "
                    "explicitly carry that marker. Do not map it to a same-series subject merely because it is visible; "
                    "choose supplemental/non_bangumi/target_absent if you judge it packaging material, inspect a more "
                    "specific target that carries the marker, or fail_closed if unresolved."
                ),
            }
        )
        if len(repairs) >= 8:
            break
    return repairs


def _excluded_episode_title_search_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    target_subjects = [
        locator
        for locator in registry.locators.values()
        if locator.kind == "target_subject"
    ]
    if not target_subjects:
        return []
    visible_target_tokens = set().union(*[_target_visible_title_tokens(locator) for locator in target_subjects])
    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") not in {"supplemental", "bangumi_target_absent", "non_bangumi", "fail_closed"}:
            continue
        local_locators: list[AgentLocator] = []
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                local_locators.append(locator)
        if not local_locators:
            continue
        episode_like_locators = [
            locator
            for locator in local_locators
            if locator.episode_file_refs or locator.locator.rsplit("/", 1)[-1] in {"main-episodes", "episodes"}
        ]
        if not episode_like_locators:
            continue
        local_tokens = set().union(*[_locator_distinctive_tokens(locator) for locator in episode_like_locators])
        latin_tokens = {token for token in local_tokens if re.fullmatch(r"[a-z][a-z0-9'-]*", token)}
        if len(latin_tokens) < 3:
            continue
        if visible_target_tokens.intersection(local_tokens):
            continue
        if _has_concrete_non_owning_reason(
            unit.get("unit"),
            unit.get("reason"),
            " ".join(locator.title for locator in episode_like_locators),
            " ".join(" ".join(locator.markers) for locator in episode_like_locators),
            " ".join(" ".join(locator.representative_labels[:3]) for locator in episode_like_locators),
        ):
            continue
        if all(
            _has_contextual_packaging_extra_reason(locator, unit.get("unit"), unit.get("reason"))
            for locator in episode_like_locators
        ):
            continue
        repairs.append(
            {
                "issue": "excluded_episode_title_search_not_exhausted",
                "unit": unit.get("unit"),
                "local": [locator.locator for locator in episode_like_locators],
                "local_title_tokens": sorted(local_tokens),
                "visible_target_title_tokens": sorted(visible_target_tokens)[:32],
                "visible_target_subject_samples": [
                    {
                        "target": locator.locator,
                        "title": locator.title,
                        "title_aliases": list(locator.markers[:6]),
                    }
                    for locator in target_subjects[:6]
                ],
                "required": (
                    "This episode-like local title is being accepted as supplemental/target_absent/non_bangumi, but the visible "
                    "target subjects have no title-token bridge to the local title. That only proves the current "
                    "search surface is poor, not that Bangumi has no target. Search cleaner official/original/"
                    "localized aliases if you know them, or submit fail_closed if the unit remains unresolved. "
                    "The fixed layer is checking evidence support only; it is not choosing a Bangumi target."
                ),
            }
        )
        if len(repairs) >= 8:
            break
    return repairs


_TITLE_TAIL_GENERIC_TOKENS = {
    "compilation",
    "digest",
    "gekijouban",
    "movie",
    "recap",
    "soushuuhen",
    "summary",
    "theater",
}


def _search_hint_was_executed(hint: str, searched_query_variant_keys: set[str]) -> bool:
    if not searched_query_variant_keys:
        return False
    for variant in _search_query_variants(hint) or [hint]:
        if str(variant or "").casefold() in searched_query_variant_keys:
            return True
    return False


def _query_hints_for_locator(locator: AgentLocator, *, limit: int = 8) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for hint in _work_unit_query_hints(
        locator.title,
        list(locator.representative_labels[:4]),
        limit=limit,
    ):
        folded = hint.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        hints.append(hint)
        if len(hints) >= limit:
            break
    return hints


def _excluded_title_tail_search_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
    *,
    searched_query_variant_keys: set[str] | None = None,
) -> list[dict[str, object]]:
    """Require evidence for named main/movie-style target_absent decisions.

    This does not choose a Bangumi target. It only rejects a target_absent-style
    conclusion when the local main locator carries distinctive title-tail tokens
    that have not been searched and are not bridged by the visible target surface.
    """

    searched_query_variant_keys = {str(item).casefold() for item in (searched_query_variant_keys or set()) if str(item)}
    all_local_locators: list[AgentLocator] = []
    for unit in feedback_units:
        if not isinstance(unit, dict):
            continue
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                all_local_locators.append(locator)
    token_counts: Counter[str] = Counter()
    for locator in all_local_locators:
        token_counts.update(_locator_distinctive_tokens(locator))

    visible_target_tokens = set().union(
        *[
            _target_visible_title_tokens(locator)
            for locator in registry.locators.values()
            if locator.kind == "target_subject"
        ]
    ) if any(locator.kind == "target_subject" for locator in registry.locators.values()) else set()

    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") not in {
            "supplemental",
            "bangumi_target_absent",
            "non_bangumi",
            "fail_closed",
        }:
            continue
        reason = str(unit.get("unit") or "") + " " + str(unit.get("reason") or "")
        if unit.get("outcome") != "bangumi_target_absent" and not _has_negative_target_absence_reason(reason):
            continue
        local_locators: list[AgentLocator] = []
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                local_locators.append(locator)
        candidate_locators = [
            locator
            for locator in local_locators
            if (registry.locators.get(_episode_slice_parent_locator(locator.locator)) or locator).locator.rsplit("/", 1)[-1]
            in {"main", "main-episodes", "episodes"}
            and 1 <= len(locator.file_refs) <= 3
        ]
        if not candidate_locators:
            continue
        if all(_has_contextual_packaging_extra_reason(locator, unit.get("unit"), unit.get("reason")) for locator in candidate_locators):
            continue
        local_tokens = set().union(*[_locator_distinctive_tokens(locator) for locator in candidate_locators])
        unique_latin_tokens = {
            token
            for token in local_tokens
            if token_counts.get(token, 0) <= 1
            and re.fullmatch(r"[a-z][a-z0-9'-]*", token)
            and token not in _TITLE_TAIL_GENERIC_TOKENS
        }
        unbridged_tokens = sorted(unique_latin_tokens - visible_target_tokens)
        if len(unbridged_tokens) < 2:
            continue
        query_hints: list[str] = []
        seen_hints: set[str] = set()
        for locator in candidate_locators:
            for hint in _query_hints_for_locator(locator, limit=8):
                folded = hint.casefold()
                if folded in seen_hints:
                    continue
                seen_hints.add(folded)
                query_hints.append(hint)
        unsearched_hints = [
            hint
            for hint in query_hints
            if not _search_hint_was_executed(hint, searched_query_variant_keys)
        ]
        if not unsearched_hints:
            continue
        support_targets: list[dict[str, object]] = []
        for raw_support in list(unit.get("support") or []):
            support_locator, support_issue = registry.resolve(str(raw_support))
            if support_issue or support_locator is None or support_locator.kind not in {"target_subject", "target_episode", "target_span"}:
                continue
            support_subject = _target_subject_locator_for(registry, support_locator)
            support_targets.append(
                {
                    "target": support_subject.locator,
                    "title": support_subject.title,
                    "subject_id": support_subject.subject_id,
                    "subject_eps": support_subject.subject_eps,
                    "title_tokens": sorted(_target_visible_title_tokens(support_subject))[:12],
                }
            )
        repairs.append(
            {
                "issue": "excluded_title_tail_search_not_exhausted",
                "unit": unit.get("unit"),
                "outcome": unit.get("outcome"),
                "local": [locator.locator for locator in candidate_locators],
                "local_titles": [locator.title for locator in candidate_locators],
                "representative_labels": [
                    label
                    for locator in candidate_locators
                    for label in list(locator.representative_labels[:3])
                ][:8],
                "unsearched_title_tokens": unbridged_tokens[:12],
                "visible_target_title_tokens": sorted(visible_target_tokens)[:32],
                "support_targets": support_targets[:6],
                "search_queries_to_try": unsearched_hints[:8],
                "searched_query_count": len(searched_query_variant_keys),
                "required": (
                    "This small main/movie-like local unit is being accepted as target_absent/supplemental/non_bangumi, "
                    "but its distinctive title-tail tokens are not bridged by the visible target surface and the listed "
                    "clean query hints have not been searched in this case. Run a batch search for search_queries_to_try "
                    "or submit fail_closed for this local locator if the title remains unresolved. This is an evidence "
                    "support check; the fixed layer is not choosing a Bangumi target."
                ),
            }
        )
        if len(repairs) >= 8:
            break
    return repairs


def _has_hard_non_owner_reason(*parts: object) -> bool:
    text = " ".join(str(part or "") for part in parts).casefold()
    if not text:
        return False
    markers = {
        "alternate encode",
        "alternate version",
        "copy",
        "menu",
        "nced",
        "ncop",
        "packaging",
        "preview",
        "sample",
        "trailer",
    }
    return any(marker in text for marker in markers)


def _title_pairing_option_is_hard_blocker(item: dict[str, object]) -> bool:
    """Return True only for high-signal visible title pairings.

    A single local tail word can surface useful evidence leads, but it is too
    noisy to block a terminal outcome by itself. Hard repair needs either a
    multi-token tail bridge or separate title context tying the candidate back
    to the same local work unit.
    """

    shared_tail = {
        str(token or "").casefold()
        for token in list(item.get("shared_title_tail_tokens") or [])
        if str(token or "").strip()
    }
    if not shared_tail:
        return False
    if len(shared_tail) >= 2:
        return True
    shared_title = {
        str(token or "").casefold()
        for token in list(item.get("shared_title_tokens") or [])
        if str(token or "").strip()
    }
    non_tail_title_context = shared_title - shared_tail - _TITLE_TAIL_GENERIC_TOKENS
    return bool(non_tail_title_context)


def _excluded_visible_title_pairing_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Reject silent exclusion when a visible title-tail target pairing exists.

    The fixed layer is not choosing the target. It is only preventing a main or
    movie-like local slice from being cleared as extra material while the visible
    evidence already exposes a stronger title-tail pairing candidate.
    """

    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") not in {
            "supplemental",
            "bangumi_target_absent",
            "non_bangumi",
            "fail_closed",
        }:
            continue
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if issue or locator is None or locator.kind != "local":
                continue
            parent_key = _episode_slice_parent_locator(locator.locator)
            parent_locator = registry.locators.get(parent_key) or locator
            category = parent_locator.locator.rsplit("/", 1)[-1]
            is_episode_slice = bool(parent_key and parent_key != locator.locator)
            if category not in {"main", "main-episodes", "episodes"}:
                continue
            if not is_episode_slice and len(parent_locator.file_refs) > 3:
                continue
            if is_episode_slice:
                pairing_options = _local_target_title_pairing_options_for_slice(registry, locator.locator)
            else:
                pairing_options = _local_target_title_pairing_options(registry, [locator.locator])
            if not pairing_options and len(locator.file_refs) == 1:
                local_tokens = _locator_distinctive_tokens(locator) - _TITLE_TAIL_GENERIC_TOKENS
                title_tail_tokens: set[str] = set()
                for hint in _query_hints_for_locator(parent_locator, limit=8):
                    title_tail_tokens.update(_distinctive_tokens(hint) - _TITLE_TAIL_GENERIC_TOKENS)
                visible_title_token_counts: Counter[str] = Counter()
                for subject_for_counts in [
                    item
                    for item in registry.locators.values()
                    if item.kind == "target_subject" and int(item.subject_id or 0)
                ]:
                    visible_title_token_counts.update(
                        _target_visible_title_tokens(subject_for_counts) - _TITLE_TAIL_GENERIC_TOKENS
                    )
                singleton_pairings: list[dict[str, object]] = []
                for subject in [
                    item
                    for item in registry.locators.values()
                    if item.kind == "target_subject" and int(item.subject_id or 0)
                ]:
                    subject_tokens = _target_visible_title_tokens(subject).union(_target_distinctive_tokens(subject)) - _TITLE_TAIL_GENERIC_TOKENS
                    shared = sorted(local_tokens.intersection(subject_tokens))
                    shared_tail = sorted(set(shared).intersection(title_tail_tokens))
                    strong_tail_bridge = len(shared_tail) >= 2 or any(
                        len(token) >= 6 and visible_title_token_counts.get(token, 0) <= 1
                        for token in shared_tail
                    )
                    if not strong_tail_bridge:
                        continue
                    target_numbers = _target_episode_numbers_for_subject(registry, int(subject.subject_id or 0))
                    target = f"{subject.locator}/episode/{int(target_numbers[0])}" if len(target_numbers) == 1 else subject.locator
                    singleton_pairings.append(
                        {
                            "local_slice": locator.locator,
                            "target": target,
                            "target_subject": subject.locator,
                            "target_title": subject.title,
                            "local_label": (list(locator.representative_labels[:1]) or [locator.title])[0],
                            "shared_title_tokens": shared[:8],
                            "shared_title_tail_tokens": shared_tail[:8],
                            "candidate_target_episode_locators": [
                                f"{subject.locator}/episode/{int(number)}" for number in target_numbers[:6]
                            ],
                            "mechanical_note": (
                                "This is a visible singleton target whose title tokens overlap the local title-tail. "
                                "singleton. The Agent must decide whether it is the semantic owner."
                            ),
                        }
                    )
                    if len(singleton_pairings) >= 8:
                        break
                pairing_options = singleton_pairings
            pairing_options = [
                item
                for item in pairing_options
                if _title_pairing_option_is_hard_blocker(item)
            ]
            if not pairing_options:
                continue
            if unit.get("outcome") == "fail_closed" and _fail_closed_reason_addresses_visible_pairing(
                unit,
                pairing_options,
            ):
                continue
            repairs.append(
                {
                    "issue": "excluded_local_has_visible_title_pairing_target",
                    "unit": unit.get("unit"),
                    "outcome": unit.get("outcome"),
                    "local": [locator.locator],
                    "parent_local": parent_locator.locator,
                    "local_title": locator.title,
                    "representative_labels": list(locator.representative_labels[:4]),
                    "local_target_title_pairing_options": pairing_options[:8],
                    "required": (
                        "This main/movie-like local locator is being excluded or fail_closed, but visible target evidence contains "
                        "visible title-tail pairing candidates for it. Map one pairing if semantically correct, "
                        "choose another visible target, provide a hard duplicate/packaging non-owner reason, or "
                        "fail_closed only after directly addressing why the listed target or candidate_target_episode_locators "
                        "are not safe owners. The fixed layer is exposing target surface, not choosing it."
                    ),
                }
            )
            break
        if len(repairs) >= 8:
            break
    return repairs


def _fail_closed_reason_addresses_visible_pairing(
    unit: dict[str, object],
    pairing_options: list[dict[str, object]],
) -> bool:
    candidates = [
        {
            "target": item.get("target"),
            "target_title": item.get("target_title"),
            "subject_title": item.get("target_title"),
            "target_title_aliases": item.get("candidate_target_episode_locators") or [],
        }
        for item in pairing_options
        if isinstance(item, dict)
    ]
    if not _unit_reason_mentions_unassigned_candidate(unit, candidates):
        return False
    reason = _normalized_locator_match_text(str(unit.get("reason") or ""))
    if not reason:
        return False
    mentions_episode_candidate = bool(
        re.search(r"\b(?:episode\s*1|ep\s*1|target\s+episode|candidate\s+episode|candidate\s+item)\b", reason)
    )
    names_contradiction = bool(
        re.search(r"\b(?:contradict|mismatch|different|wrong|season|not\s+owner|not\s+the\s+owner)\b", reason)
    )
    return mentions_episode_candidate and names_contradiction


def _visible_source_query_bridge_targets(
    registry: LocatorRegistry,
    locator: AgentLocator,
    parent_locator: AgentLocator,
    title_tail_tokens: set[str],
    *,
    limit: int = 12,
) -> list[dict[str, object]]:
    local_tokens = _locator_distinctive_tokens(locator).union(_locator_distinctive_tokens(parent_locator))
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for subject in [
        item
        for item in registry.locators.values()
        if item.kind == "target_subject" and int(item.subject_id or 0)
    ]:
        query_tokens = _target_query_distinctive_tokens(subject) - _TITLE_TAIL_GENERIC_TOKENS
        shared_query_tail = sorted(title_tail_tokens.intersection(query_tokens))
        if len(shared_query_tail) < 2:
            continue
        title_tokens = _target_visible_title_tokens(subject) - _TITLE_TAIL_GENERIC_TOKENS
        shared_title = sorted(local_tokens.intersection(title_tokens))
        relation_path_refs = tuple(ref for ref in subject.relation_path_refs if ref)
        relation_to_main = str(subject.relation_to_main or "").strip()
        source_role = str(subject.source_role or "").strip()
        related_query_bridge = bool(relation_to_main or relation_path_refs or source_role.startswith("related_"))
        target_numbers = _target_episode_numbers_for_subject(registry, int(subject.subject_id or 0))
        target = subject.locator
        candidate_items: list[str] = []
        if len(target_numbers) == 1:
            target = f"{subject.locator}/episode/{int(target_numbers[0])}"
        elif target_numbers:
            candidate_items = [f"{subject.locator}/episode/{int(number)}" for number in target_numbers[:6]]
        key = target.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "target": target,
                "target_subject": subject.locator,
                "target_title": subject.title,
                "subject_eps": int(subject.subject_eps or 0),
                "search_rank": int(subject.search_rank or 0),
                "shared_title_tokens": shared_title[:8],
                "shared_source_query_tail_tokens": shared_query_tail[:8],
                "target_source_query_texts": list(subject.query_markers[:6]),
                "relation_to_query_subject": relation_to_main,
                "source_role": source_role,
                "relation_path_refs": list(relation_path_refs[:4]),
                "relevance_layer": "related_source_query_bridge" if related_query_bridge else "source_query_bridge",
                "candidate_target_episode_locators": candidate_items,
                "available_action": f'inspect(["{subject.locator}"], scope=["details","episodes","related"])',
                "mechanical_note": (
                    "This target is visible because source-query or related-source provenance overlaps the local title-tail. "
                    "That provenance is not a title alias or fixed-layer target choice; inspect or map only if your semantic judgment supports it."
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            len(row.get("shared_title_tokens") or []) < 2,
            row.get("relevance_layer") != "related_source_query_bridge",
            not bool(row.get("shared_title_tokens")),
            int(row.get("search_rank") or 999999),
            str(row.get("target_title") or ""),
            str(row.get("target") or ""),
        )
    )
    return rows[:limit]


def _excluded_title_tail_unresolved_after_search_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
    *,
    searched_query_variant_keys: set[str] | None = None,
    allowed_outcomes: set[str] | None = None,
    issue_code: str = "excluded_title_tail_unresolved_after_search",
    require_uninspected_bridge_target: bool = False,
    inspected_subject_ids: set[int] | None = None,
) -> list[dict[str, object]]:
    searched_query_variant_keys = {str(item).casefold() for item in (searched_query_variant_keys or set()) if str(item)}
    allowed_outcomes = allowed_outcomes or {"supplemental", "bangumi_target_absent", "non_bangumi"}
    inspected_subject_ids = set(inspected_subject_ids or set())
    visible_target_tokens = set().union(
        *[
            _target_visible_title_tokens(locator)
            for locator in registry.locators.values()
            if locator.kind == "target_subject"
        ]
    ) if any(locator.kind == "target_subject" for locator in registry.locators.values()) else set()
    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") not in allowed_outcomes:
            continue
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if issue or locator is None or locator.kind != "local":
                continue
            parent_key = _episode_slice_parent_locator(locator.locator)
            parent_locator = registry.locators.get(parent_key) or locator
            category = parent_locator.locator.rsplit("/", 1)[-1]
            is_episode_slice = bool(parent_key and parent_key != locator.locator)
            if category not in {"main", "main-episodes", "episodes"}:
                continue
            if not is_episode_slice and not (1 <= len(parent_locator.file_refs) <= 3):
                continue
            local_tokens = _locator_distinctive_tokens(locator)
            if is_episode_slice:
                local_tokens = local_tokens.union(_distinctive_tokens(parent_locator.title))
            else:
                local_tokens = local_tokens.union(_locator_distinctive_tokens(parent_locator))
            title_tail_tokens = {
                token
                for token in local_tokens
                if token not in _TITLE_TAIL_GENERIC_TOKENS
                and (re.fullmatch(r"[a-z][a-z0-9'-]*", token) or re.search(r"[\u3040-\u30ff\u3400-\u9fff]", token))
            }
            unbridged_tokens = sorted(title_tail_tokens - visible_target_tokens)
            if not unbridged_tokens:
                continue
            query_locator = locator if is_episode_slice else parent_locator
            query_hints = _query_hints_for_locator(query_locator, limit=8)
            unsearched_hints = [
                hint
                for hint in query_hints
                if not _search_hint_was_executed(hint, searched_query_variant_keys)
            ]
            if unsearched_hints:
                continue
            source_query_bridge_targets = _visible_source_query_bridge_targets(
                registry,
                locator,
                parent_locator,
                title_tail_tokens,
            )
            if require_uninspected_bridge_target:
                source_query_bridge_targets = [
                    item
                    for item in source_query_bridge_targets
                    if _target_subject_id_from_locator(str(item.get("target_subject") or item.get("target") or ""))
                    not in inspected_subject_ids
                ]
                if not source_query_bridge_targets:
                    continue
            required = (
                "This fail_closed main/movie-like local locator still has distinctive title-tail tokens with no "
                "visible title bridge after searched query hints, while visible source-query/related bridge "
                "candidates remain uninspected. Inspect one listed candidate, search another concrete alias, or "
                "resubmit fail_closed only after the remaining evidence gap names why those visible candidates are "
                "not sufficient. The fixed layer is requiring evidence exhaustion; it is not choosing a target."
                if issue_code == "fail_closed_title_tail_bridge_uninspected"
                else (
                    "This main/movie-like local locator still has distinctive title-tail tokens with no visible "
                    "target bridge after its current query hints were searched. Do not clear it as supplemental/"
                    "target_absent/non_bangumi from a broad same-franchise target alone. If visible_source_query_bridge_targets "
                    "is non-empty, inspect or map one only if your semantic judgment supports it; otherwise search another "
                    "concrete alias or fail_closed this exact local locator with the remaining evidence gap."
                )
            )
            repairs.append(
                {
                    "issue": issue_code,
                    "unit": unit.get("unit"),
                    "outcome": unit.get("outcome"),
                    "local": [locator.locator],
                    "parent_local": parent_locator.locator,
                    "local_title": locator.title,
                    "representative_labels": list(locator.representative_labels[:4]),
                    "unbridged_title_tail_tokens": unbridged_tokens[:12],
                    "searched_query_hints": query_hints[:8],
                    "visible_target_title_tokens": sorted(visible_target_tokens)[:32],
                    "visible_source_query_bridge_targets": source_query_bridge_targets,
                    "required": required,
                }
            )
            break
        if len(repairs) >= 8:
            break
    return repairs


def _excluded_singleton_with_unassigned_target_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
    target_counts: Counter[str],
    *,
    allowed_outcomes: set[str] | None = None,
    issue_code: str = "excluded_singleton_with_unassigned_visible_target_items",
) -> list[dict[str, object]]:
    allowed_outcomes = allowed_outcomes or {"supplemental", "bangumi_target_absent", "non_bangumi"}
    used_subject_ids: set[int] = set()
    for unit in feedback_units:
        if not isinstance(unit, dict) or not str(unit.get("outcome") or "").startswith("mapped_"):
            continue
        target_locator, target_issue = registry.resolve(str(unit.get("target") or ""))
        if not target_issue and target_locator is not None and target_locator.subject_id:
            used_subject_ids.add(int(target_locator.subject_id))
    if not used_subject_ids:
        return []

    unassigned_by_subject: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
    for locator in registry.locators.values():
        if locator.kind != "target_episode" or int(locator.subject_id or 0) not in used_subject_ids:
            continue
        if not locator.item_refs:
            continue
        item_ref = locator.item_refs[0]
        if target_counts.get(item_ref, 0):
            continue
        unassigned_by_subject[int(locator.subject_id)].append((int(locator.episode_start or 0), item_ref, locator.locator))
    for rows in unassigned_by_subject.values():
        rows.sort(key=lambda row: row[0])

    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or str(unit.get("outcome") or "") not in allowed_outcomes:
            continue
        outcome = str(unit.get("outcome") or "")
        candidate_locators: list[AgentLocator] = []
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                candidate_locators.append(locator)
        if sum(len(locator.file_refs) for locator in candidate_locators) != 1:
            continue
        if outcome in {"supplemental", "non_bangumi"} and _has_concrete_extra_reason(
            unit.get("unit"),
            unit.get("reason"),
            " ".join(locator.title for locator in candidate_locators),
            " ".join(" ".join(locator.markers) for locator in candidate_locators),
            " ".join(" ".join(locator.representative_labels[:2]) for locator in candidate_locators),
        ):
            continue
        unassigned_candidates: list[dict[str, object]] = []
        for subject_id, rows in sorted(unassigned_by_subject.items()):
            subject_locator = registry.locators.get(registry.subject_locator_by_id.get(subject_id, "") or "")
            for episode_number, _item_ref, item_locator in rows[:6]:
                item_agent_locator = registry.locators.get(item_locator)
                unassigned_candidates.append(
                    {
                        "target": item_locator,
                        "subject_id": subject_id,
                        "subject_title": getattr(subject_locator, "title", "") if subject_locator is not None else "",
                        "episode_number": episode_number,
                        "target_title": getattr(item_agent_locator, "title", "") if item_agent_locator is not None else "",
                        "target_title_aliases": list(getattr(item_agent_locator, "markers", ()) or ())[:6]
                        if item_agent_locator is not None
                        else [],
                    }
                )
            if len(unassigned_candidates) >= 8:
                break
        if not unassigned_candidates:
            continue
        if outcome in {"bangumi_target_absent", "fail_closed"} and _unit_reason_mentions_unassigned_candidate(
            unit,
            unassigned_candidates,
        ):
            continue
        repairs.append(
            {
                "issue": issue_code,
                "unit": unit.get("unit"),
                "outcome": unit.get("outcome"),
                "local": [locator.locator for locator in candidate_locators],
                "local_titles": [locator.title for locator in candidate_locators],
                "representative_labels": [
                    label
                    for locator in candidate_locators
                    for label in list(locator.representative_labels[:3])
                ],
                "unassigned_target_candidates": unassigned_candidates[:8],
                "required": (
                    "This singleton local is not labeled as a concrete extra/duplicate/packaging item, and visible "
                    "mapped subjects still have unassigned episode items. Inspect or map a matching unassigned "
                    "target item if it is the semantic owner; otherwise provide a concrete target_absent/non_bangumi/"
                    "supplemental reason or fail_closed."
                ),
            }
        )
        if len(repairs) >= 8:
            break
    return repairs


def _unit_reason_mentions_unassigned_candidate(
    unit: dict[str, object],
    candidates: list[dict[str, object]],
) -> bool:
    reason_text = _normalized_locator_match_text(
        " ".join(str(unit.get(key) or "") for key in ("unit", "reason"))
    )
    if not reason_text:
        return False
    for candidate in candidates:
        values = [
            candidate.get("target"),
            candidate.get("target_title"),
            candidate.get("subject_title"),
            *list(candidate.get("target_title_aliases") or []),
        ]
        for value in values:
            marker = _normalized_locator_match_text(str(value or ""))
            if len(marker) >= 3 and marker in reason_text:
                return True
    return False


def _duplicate_like_singleton_exclusion_mismatch_repairs(
    registry: LocatorRegistry,
    feedback_units: list[dict[str, object]],
) -> list[dict[str, object]]:
    mapped_token_rows: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or not str(unit.get("outcome") or "").startswith("mapped_"):
            continue
        tokens: set[str] = set()
        local_locators: list[str] = []
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                local_locators.append(locator.locator)
                tokens.update(_locator_distinctive_tokens(locator))
        if tokens:
            mapped_token_rows.append(
                {
                    "unit": unit.get("unit"),
                    "local": local_locators,
                    "target": unit.get("target"),
                    "tokens": tokens,
                }
            )

    repairs: list[dict[str, object]] = []
    for unit in feedback_units:
        if not isinstance(unit, dict) or unit.get("outcome") not in {"supplemental", "bangumi_target_absent", "non_bangumi"}:
            continue
        reason_text = str(unit.get("reason") or unit.get("unit") or "")
        if any(marker in reason_text.casefold() for marker in ("compilation", "recap", "digest", "summary")):
            continue
        if re.search(
            r"(?i)\b(?:not|no|cannot|can't|unsafe|without|isn't|is\s+not|not\s+safely)\b.{0,48}"
            r"\b(?:alternate|duplicate|copy|packaging)\b",
            reason_text,
        ):
            continue
        if not any(marker in reason_text.casefold() for marker in ("alternate", "duplicate", "copy", "packaging")):
            continue
        excluded_locators: list[AgentLocator] = []
        for raw_local in list(unit.get("local") or []):
            locator, issue = registry.resolve(str(raw_local))
            if not issue and locator is not None and locator.kind == "local":
                excluded_locators.append(locator)
        if sum(len(locator.file_refs) for locator in excluded_locators) != 1:
            continue
        if not any(locator.locator.rsplit("/", 1)[-1] in {"main", "main-episodes"} for locator in excluded_locators):
            continue
        if any(_has_contextual_packaging_extra_reason(locator, unit.get("unit"), unit.get("reason")) for locator in excluded_locators):
            continue
        excluded_tokens = set().union(*[_locator_distinctive_tokens(locator) for locator in excluded_locators]) if excluded_locators else set()
        if len(excluded_tokens) < 2:
            continue
        best_row: dict[str, object] | None = None
        best_score = 0
        for row in mapped_token_rows:
            score = len(excluded_tokens.intersection(row["tokens"]))
            if score > best_score:
                best_score = score
                best_row = row
        best_tokens = set((best_row or {}).get("tokens") or set())
        unmatched_excluded_tokens = sorted(excluded_tokens - best_tokens)
        if best_score >= 2 and len(unmatched_excluded_tokens) < 2:
            continue
        repairs.append(
            {
                "issue": "duplicate_like_singleton_exclusion_title_mismatch",
                "unit": unit.get("unit"),
                "local": [locator.locator for locator in excluded_locators],
                "reason": reason_text,
                "excluded_title_tokens": sorted(excluded_tokens),
                "unmatched_excluded_title_tokens": unmatched_excluded_tokens[:12],
                "best_mapped_overlap_score": best_score,
                "best_mapped_unit": (best_row or {}).get("unit"),
                "best_mapped_local": (best_row or {}).get("local"),
                "best_mapped_target": (best_row or {}).get("target"),
                "required": (
                    "The unit is excluded as duplicate/alternate/copy/packaging, but its distinctive title-tail "
                    "tokens are not covered by the mapped owner. Re-check ownership: map/search this named "
                    "singleton, use target_absent/non_bangumi with a concrete non-duplicate reason, or fail_closed."
                ),
            }
        )
        if len(repairs) >= 8:
            break
    return repairs


def _submit_tool(
    workspace: CaseEvidenceWorkspace,
    registry: LocatorRegistry,
    args: SubmitToolArgs,
    *,
    searched_query_variant_keys: set[str] | None = None,
) -> SubmitCompileResult:
    issues: list[VerifierIssue] = []
    feedback_units: list[dict[str, object]] = []
    main_refs = list(workspace.contract.main_file_refs or [])
    covered: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    findings: list[Finding] = []
    assignments: list[AssignmentIntent] = []
    target_usage: dict[str, list[dict[str, object]]] = defaultdict(list)
    fail_closed_units: list[dict[str, object]] = []
    fail_closed_file_count = 0
    mapped_file_count = 0
    excluded_file_count = 0
    finding_index = 1
    assignment_index = 1
    allowed_exclusion_outcomes = {"supplemental", "bangumi_target_absent", "non_bangumi"}
    mapped_title_season_mismatch_repairs: list[dict[str, object]] = []

    for unit_index, unit in enumerate(args.resolution.work_units, start=1):
        local_refs, local_issues, canonical_locators = _file_refs_for_locators(registry, unit.local)
        support_issues, canonical_support = _validate_support_locators(registry, unit.support)
        if local_issues:
            for item in local_issues:
                issues.append(_issue(unit.unit_label or f"WU{unit_index}", str(item.get("issue") or "locator_error"), json.dumps(item, ensure_ascii=False)))
            feedback_units.append(
                {
                    "unit": unit.unit_label or f"WU{unit_index}",
                    "local": list(unit.local),
                    "support": canonical_support,
                    "outcome": unit.outcome,
                    "issues": local_issues,
                }
            )
            continue
        if not local_refs:
            issue_payload = {
                "issue": "local_locator_required",
                "local": list(unit.local),
                "outcome": unit.outcome,
                "required": "Every submit work unit must cite at least one visible local:// locator and cover at least one local file.",
            }
            issues.append(
                _issue(
                    unit.unit_label or f"WU{unit_index}",
                    "local_locator_required",
                    json.dumps(issue_payload, ensure_ascii=False),
                )
            )
            feedback_units.append({"unit": unit.unit_label or f"WU{unit_index}", **issue_payload})
            continue
        if support_issues:
            for item in support_issues:
                issues.append(_issue(unit.unit_label or f"WU{unit_index}", str(item.get("issue") or "support_locator_error"), json.dumps(item, ensure_ascii=False)))
            feedback_units.append({"unit": unit.unit_label or f"WU{unit_index}", "issues": support_issues})
            continue
        for ref in local_refs:
            covered[ref] += 1
        finding_ref = f"HF{finding_index}"
        finding_index += 1
        findings.append(
            Finding(
                ref=finding_ref,
                finding_kind="pass" if unit.outcome != "fail_closed" else "blocked",
                description=unit.reason or unit.unit_label or unit.outcome,
                evidence_refs=[],
            )
        )
        if unit.outcome.startswith("mapped_"):
            target_refs, target_issues, canonical_target = _target_items_for_unit(
                registry,
                unit,
                local_file_count=len(local_refs),
                local_locators=canonical_locators,
            )
            if target_issues:
                for item in target_issues:
                    issues.append(_issue(unit.unit_label or f"WU{unit_index}", str(item.get("issue") or "target_error"), json.dumps(item, ensure_ascii=False)))
                feedback_units.append(
                    {
                        "unit": unit.unit_label or f"WU{unit_index}",
                        "local": canonical_locators,
                        "local_locator_details": _local_locator_feedback(registry, canonical_locators),
                        "target": unit.target,
                        "outcome": unit.outcome,
                        "issues": target_issues,
                    }
                )
                continue
            composite_feature_shape = (
                len(target_refs) > 1
                and _is_single_non_episode_feature_local(
                    registry,
                    canonical_locators,
                    local_file_count=len(local_refs),
                )
            )
            composite_feature_intent = (
                unit.outcome == "mapped_composite_feature"
                or (
                    unit.outcome in {"mapped_regular_span", "mapped_special_or_ova"}
                    and composite_feature_shape
                    and len(target_refs) != len(local_refs)
                )
            )
            if composite_feature_intent:
                if not composite_feature_shape:
                    resolved_target_locator, _resolved_target_issue = registry.resolve(canonical_target)
                    local_locator_details = _local_locator_feedback(registry, canonical_locators)
                    local_episode_split_options = _local_episode_split_options_from_feedback(local_locator_details)
                    local_target_count_pairing_options = _local_target_count_pairing_options(
                        registry,
                        canonical_locators,
                        target_count=len(target_refs),
                    )
                    split_search_queries = _search_queries_from_local_split_options(
                        [
                            *local_episode_split_options,
                            *_split_option_rows_from_count_pairings(local_target_count_pairing_options),
                        ],
                    )
                    split_first_repair = _split_first_repair_feedback(
                        target_count=len(target_refs),
                        local_episode_split_options=local_episode_split_options,
                        local_target_count_pairing_options=local_target_count_pairing_options,
                    )
                    issue_payload = {
                        "issue": "composite_feature_shape_invalid",
                        "local_count": len(local_refs),
                        "target_count": len(target_refs),
                        "target": canonical_target,
                        "target_locator_details": _target_locator_feedback(registry, canonical_target, target_refs),
                        "local": canonical_locators,
                        "local_locator_details": local_locator_details,
                        "local_episode_split_options": local_episode_split_options,
                        "local_target_count_pairing_options": local_target_count_pairing_options,
                        "single_file_target_item_options": _single_file_target_item_options(
                            registry,
                            target=canonical_target,
                            local_locators=canonical_locators,
                            local_count=len(local_refs),
                        ),
                        "split_first_repair": split_first_repair,
                        "search_queries_to_try": split_search_queries,
                        "visible_alternate_subjects": _visible_subject_candidates_for_feedback(
                            registry,
                            current_subject_id=getattr(resolved_target_locator, "subject_id", 0)
                            if resolved_target_locator is not None
                            else 0,
                            local_count=len(local_refs),
                            limit=3 if split_first_repair else 8,
                        ),
                        "required_shape": "mapped_composite_feature requires exactly one local file and two or more visible target episode items",
                        "actionable_options": [
                            "If the local locator contains multiple numbered files but the target has one item, split the local side with local://.../episode/N or local://.../episodes/A-B locators.",
                            "If each local file is a separate movie/feature/special, submit one work unit per local episode slice and choose the matching visible target for each slice.",
                            "If this one local file corresponds to one visible target item inside the subject, use that target://.../episode/N item instead of the full subject span.",
                            "If one local feature file semantically covers multiple Bangumi episode parts, use outcome=mapped_composite_feature with that single local file and a visible target span.",
                            "If a local slice has no safe Bangumi target, submit that slice as bangumi_target_absent, supplemental, non_bangumi, or fail_closed according to your semantic judgment.",
                        ],
                    }
                    issues.append(_issue(unit.unit_label or f"WU{unit_index}", "composite_feature_shape_invalid", json.dumps(issue_payload, ensure_ascii=False)))
                    feedback_units.append({"unit": unit.unit_label or f"WU{unit_index}", **issue_payload})
                    continue
                local_ref = local_refs[0]
                primary_target_ref = target_refs[0]
                for target_ref in target_refs:
                    target_counts[target_ref] += 1
                    target_usage[target_ref].append(
                        {
                            "unit": unit.unit_label or f"WU{unit_index}",
                            "local": canonical_locators,
                            "target": canonical_target,
                            "target_item": _target_item_locator_for_ref(registry, target_ref),
                            "composite_target_count": len(target_refs),
                        }
                    )
                assignments.append(
                    AssignmentIntent(
                        ref=f"HA{assignment_index}",
                        file_ref=local_ref,
                        target_ref=primary_target_ref,
                        target_refs=list(target_refs),
                        support_finding_refs=[finding_ref],
                        support_card_refs=list(dict.fromkeys([local_ref, *target_refs])),
                        confidence=unit.confidence,
                        reason=unit.reason or f"human_case_agent:{unit.unit_label}:composite_feature",
                    )
                )
                assignment_index += 1
                mapped_file_count += 1
                feedback_units.append(
                    {
                        "unit": unit.unit_label or f"WU{unit_index}",
                        "local": canonical_locators,
                        "target": canonical_target,
                        "support": canonical_support,
                        "outcome": unit.outcome,
                        "reason": unit.reason,
                        "compiled_as": "mapped_composite_feature",
                        "mapped_files": 1,
                        "composite_target_item_count": len(target_refs),
                    }
                )
                continue
            if len(target_refs) != len(local_refs):
                resolved_target_locator, _resolved_target_issue = registry.resolve(canonical_target)
                local_locator_details = _local_locator_feedback(registry, canonical_locators)
                local_episode_split_options = _local_episode_split_options_from_feedback(local_locator_details)
                local_target_count_pairing_options = _local_target_count_pairing_options(
                    registry,
                    canonical_locators,
                    target_count=len(target_refs),
                )
                split_search_queries = _search_queries_from_local_split_options(
                    [
                        *local_episode_split_options,
                        *_split_option_rows_from_count_pairings(local_target_count_pairing_options),
                    ],
                )
                split_first_repair = _split_first_repair_feedback(
                    target_count=len(target_refs),
                    local_episode_split_options=local_episode_split_options,
                    local_target_count_pairing_options=local_target_count_pairing_options,
                )
                issue_payload = {
                    "issue": "count_mismatch",
                    "local_count": len(local_refs),
                    "target_count": len(target_refs),
                    "target": canonical_target,
                    "target_locator_details": _target_locator_feedback(registry, canonical_target, target_refs),
                    "local": canonical_locators,
                    "local_locator_details": local_locator_details,
                    "same_count_target_span_candidates": _same_count_target_span_candidates(
                        registry,
                        target=canonical_target,
                        local_locators=canonical_locators,
                        local_count=len(local_refs),
                    ),
                    "local_episode_split_options": local_episode_split_options,
                    "local_target_count_pairing_options": local_target_count_pairing_options,
                    "single_file_target_item_options": _single_file_target_item_options(
                        registry,
                        target=canonical_target,
                        local_locators=canonical_locators,
                        local_count=len(local_refs),
                    ),
                    "split_first_repair": split_first_repair,
                    "search_queries_to_try": split_search_queries,
                    "visible_alternate_subjects": _visible_subject_candidates_for_feedback(
                        registry,
                        current_subject_id=getattr(resolved_target_locator, "subject_id", 0)
                        if resolved_target_locator is not None
                        else 0,
                        local_count=len(local_refs),
                        limit=3 if split_first_repair else 8,
                    ),
                    "actionable_options": [
                        "If the target is correct but the local locator contains extra numbered files, split the local side with local://.../episode/N or local://.../episodes/A-B locators.",
                        "If Bangumi has no target item for part of the local locator, submit that local sub-locator as bangumi_target_absent, supplemental, or non_bangumi according to your semantic judgment.",
                        "If this one local file corresponds to one visible target item inside the subject, use that target://.../episode/N item instead of the full subject span.",
                        "If one local feature/movie file semantically covers multiple Bangumi episode parts, use outcome=mapped_composite_feature with that visible target span.",
                        "If the target surface is incomplete, inspect the target subject with scope [details, episodes, related] before resubmitting.",
                    ],
                }
                issues.append(_issue(unit.unit_label or f"WU{unit_index}", "count_mismatch", json.dumps(issue_payload, ensure_ascii=False)))
                feedback_units.append({"unit": unit.unit_label or f"WU{unit_index}", **issue_payload})
                continue
            season_mismatch_repair = _mapped_title_season_mismatch_repair(
                registry,
                local_locators=canonical_locators,
                target=canonical_target,
            )
            if season_mismatch_repair:
                season_mismatch_repair = {
                    "unit": unit.unit_label or f"WU{unit_index}",
                    **season_mismatch_repair,
                }
                mapped_title_season_mismatch_repairs.append(season_mismatch_repair)
                issues.append(
                    _issue(
                        unit.unit_label or f"WU{unit_index}",
                        "mapped_title_season_mismatch",
                        json.dumps(season_mismatch_repair, ensure_ascii=False),
                    )
                )
            for local_ref, target_ref in zip(local_refs, target_refs):
                target_counts[target_ref] += 1
                target_usage[target_ref].append(
                    {
                        "unit": unit.unit_label or f"WU{unit_index}",
                        "local": canonical_locators,
                        "target": canonical_target,
                        "target_item": _target_item_locator_for_ref(registry, target_ref),
                    }
                )
                assignments.append(
                    AssignmentIntent(
                        ref=f"HA{assignment_index}",
                        file_ref=local_ref,
                        target_ref=target_ref,
                        support_finding_refs=[finding_ref],
                        support_card_refs=[local_ref, target_ref],
                        confidence=unit.confidence,
                        reason=unit.reason or f"human_case_agent:{unit.unit_label}",
                    )
                )
                assignment_index += 1
                mapped_file_count += 1
            feedback_units.append(
                {
                    "unit": unit.unit_label or f"WU{unit_index}",
                    "local": canonical_locators,
                    "target": canonical_target,
                    "support": canonical_support,
                    "outcome": unit.outcome,
                    "reason": unit.reason,
                    "mapped_files": len(local_refs),
                    "season_mismatch_repair": season_mismatch_repair,
                    "semantic_diagnostics": [season_mismatch_repair] if season_mismatch_repair else [],
                }
            )
        elif unit.outcome in allowed_exclusion_outcomes:
            reason_kind = "supplemental"
            if unit.outcome == "bangumi_target_absent":
                reason_kind = "bangumi_target_absent"
            elif unit.outcome == "non_bangumi":
                reason_kind = "non_bangumi"
            for local_ref in local_refs:
                assignments.append(
                    AssignmentIntent(
                        ref=f"HA{assignment_index}",
                        file_ref=local_ref,
                        target_ref="UNALIGNED",
                        support_finding_refs=[finding_ref],
                        support_card_refs=[local_ref],
                        confidence=unit.confidence,
                        reason=f"mapping_draft:human_case_agent:supplemental:{reason_kind}:{unit.reason or unit.unit_label}",
                    )
                )
                assignment_index += 1
                excluded_file_count += 1
            feedback_units.append(
                {
                    "unit": unit.unit_label or f"WU{unit_index}",
                    "local": canonical_locators,
                    "support": canonical_support,
                    "outcome": unit.outcome,
                    "reason_kind": reason_kind,
                    "reason": unit.reason,
                    "excluded_files": len(local_refs),
                }
            )
        elif unit.outcome == "fail_closed":
            reason = str(unit.reason or "").strip()
            if not reason:
                issues.append(
                    _issue(
                        unit.unit_label or f"WU{unit_index}",
                        "fail_closed_reason_required",
                        "fail_closed work units must include a concrete reason",
                        related_refs=local_refs[:8],
                    )
                )
                feedback_units.append(
                    {
                        "unit": unit.unit_label or f"WU{unit_index}",
                        "local": canonical_locators,
                        "support": canonical_support,
                        "outcome": "fail_closed",
                        "issues": [{"issue": "fail_closed_reason_required"}],
                    }
                )
                continue
            fail_closed_file_count += len(local_refs)
            fail_closed_units.append(
                {
                    "unit": unit.unit_label or f"WU{unit_index}",
                    "local": canonical_locators,
                    "support": canonical_support,
                    "reason": reason,
                    "local_refs": local_refs[:8],
                    "file_count": len(local_refs),
                }
            )
            feedback_units.append(
                {
                    "unit": unit.unit_label or f"WU{unit_index}",
                    "local": canonical_locators,
                    "support": canonical_support,
                    "outcome": "fail_closed",
                    "reason": reason,
                    "blocked_files": len(local_refs),
                }
            )

    missing = [ref for ref in main_refs if covered.get(ref, 0) == 0]
    duplicates = [ref for ref, count in covered.items() if count > 1]
    extra = [ref for ref in covered if ref not in main_refs]
    duplicate_targets = [ref for ref, count in target_counts.items() if count > 1]
    if missing:
        issues.append(_issue("package", "coverage_missing", "must_account local refs missing", related_refs=missing[:12]))
    if duplicates:
        issues.append(_issue("package", "coverage_overlap", "must_account local refs covered more than once", related_refs=duplicates[:12]))
    if extra:
        issues.append(_issue("package", "coverage_extra", "resolution covered non-contract local refs", related_refs=extra[:12]))
    if duplicate_targets:
        issues.append(_issue("package", "duplicate_target", "duplicate target item refs", related_refs=duplicate_targets[:12]))
    inspected_subject_ids = _inspected_subject_ids_from_workspace(workspace)
    fail_closed_sibling_repairs = _fail_closed_mapped_sibling_repairs(
        registry,
        fail_closed_units,
        feedback_units,
    )
    excluded_slice_sibling_repairs = _excluded_slice_mapped_sibling_repairs(
        registry,
        feedback_units,
    )
    fail_closed_count_sibling_repairs = _fail_closed_count_matched_target_sibling_repairs(
        registry,
        fail_closed_units,
        feedback_units,
    )
    excluded_count_uninspected_repairs = _excluded_count_matched_uninspected_subject_repairs(
        registry,
        feedback_units,
        inspected_subject_ids=inspected_subject_ids,
    )
    excluded_singleton_subject_repairs: list[dict[str, object]] = []
    excluded_main_sibling_repairs = _excluded_main_with_mapped_title_sibling_repairs(
        registry,
        feedback_units,
    )
    supplemental_main_episode_repairs = _supplemental_main_episode_repairs(
        registry,
        feedback_units,
    )
    numbered_special_exclusion_repairs = _numbered_special_exclusion_repairs(
        registry,
        feedback_units,
        inspected_subject_ids=inspected_subject_ids,
    )
    fail_closed_negative_target_absence_repairs = _fail_closed_negative_target_absence_repairs(
        registry,
        fail_closed_units,
        inspected_subject_ids=inspected_subject_ids,
    )
    singleton_target_alias_repairs = _singleton_target_alias_owner_repairs(
        registry,
        feedback_units,
    )
    mapped_target_title_bridge_repairs = _mapped_target_title_bridge_repairs(
        registry,
        feedback_units,
        searched_query_variant_keys=searched_query_variant_keys,
    )
    mapped_packaging_extra_target_repairs = _mapped_contextual_packaging_extra_target_repairs(
        registry,
        feedback_units,
    )
    excluded_episode_title_search_repairs = _excluded_episode_title_search_repairs(
        registry,
        feedback_units,
    )
    excluded_title_tail_search_repairs = _excluded_title_tail_search_repairs(
        registry,
        feedback_units,
        searched_query_variant_keys=searched_query_variant_keys,
    )
    excluded_visible_title_pairing_repairs = _excluded_visible_title_pairing_repairs(
        registry,
        feedback_units,
    )
    excluded_title_tail_unresolved_repairs = _excluded_title_tail_unresolved_after_search_repairs(
        registry,
        feedback_units,
        searched_query_variant_keys=searched_query_variant_keys,
    )
    fail_closed_slice_pairing_repairs = _fail_closed_with_visible_slice_pairing_repairs(
        registry,
        feedback_units,
    )
    fail_closed_title_tail_bridge_repairs = _excluded_title_tail_unresolved_after_search_repairs(
        registry,
        feedback_units,
        searched_query_variant_keys=searched_query_variant_keys,
        allowed_outcomes={"fail_closed"},
        issue_code="fail_closed_title_tail_bridge_uninspected",
        require_uninspected_bridge_target=True,
        inspected_subject_ids=inspected_subject_ids,
    )
    excluded_singleton_unassigned_repairs = _excluded_singleton_with_unassigned_target_repairs(
        registry,
        feedback_units,
        target_counts,
    )
    fail_closed_singleton_unassigned_repairs = _excluded_singleton_with_unassigned_target_repairs(
        registry,
        feedback_units,
        target_counts,
        allowed_outcomes={"fail_closed"},
        issue_code="fail_closed_singleton_with_unassigned_visible_target_items",
    )
    duplicate_like_singleton_exclusion_repairs = _duplicate_like_singleton_exclusion_mismatch_repairs(
        registry,
        feedback_units,
    )
    for repair in fail_closed_sibling_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "fail_closed_with_mapped_sibling",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_slice_sibling_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "excluded_slice_with_mapped_sibling",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in fail_closed_count_sibling_repairs:
        issues.append(
            _issue(
                str(repair.get("fail_closed_unit") or "package"),
                "fail_closed_count_matched_target_sibling",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_count_uninspected_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "excluded_count_matched_uninspected_subject",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_singleton_subject_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "excluded_singleton_visible_subject_candidate",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_main_sibling_repairs:
        issues.append(
            _issue(
                str(repair.get("excluded_unit") or "package"),
                "excluded_main_locator_with_mapped_title_sibling",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in supplemental_main_episode_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "supplemental_main_episodes_without_concrete_extra_reason",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in numbered_special_exclusion_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "numbered_special_exclusion_needs_target_evidence",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in fail_closed_negative_target_absence_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "fail_closed_negative_target_absence_outcome_inconsistent",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in singleton_target_alias_repairs:
        issues.append(
            _issue(
                str(repair.get("mapped_unit") or "package"),
                "singleton_target_alias_matches_excluded_local_better",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in mapped_target_title_bridge_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "mapped_target_title_bridge_missing",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in mapped_packaging_extra_target_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "mapped_packaging_extra_marker_without_specific_target",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_episode_title_search_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "excluded_episode_title_search_not_exhausted",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_title_tail_search_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "excluded_title_tail_search_not_exhausted",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_visible_title_pairing_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "excluded_local_has_visible_title_pairing_target",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_title_tail_unresolved_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "excluded_title_tail_unresolved_after_search",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in fail_closed_slice_pairing_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "fail_closed_with_visible_slice_pairing",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in fail_closed_title_tail_bridge_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "fail_closed_title_tail_bridge_uninspected",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in excluded_singleton_unassigned_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "excluded_singleton_with_unassigned_visible_target_items",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in fail_closed_singleton_unassigned_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "fail_closed_singleton_with_unassigned_visible_target_items",
                json.dumps(repair, ensure_ascii=False),
            )
        )
    for repair in duplicate_like_singleton_exclusion_repairs:
        issues.append(
            _issue(
                str(repair.get("unit") or "package"),
                "duplicate_like_singleton_exclusion_title_mismatch",
                json.dumps(repair, ensure_ascii=False),
            )
        )

    semantic_submit_diagnostics = [issue for issue in issues if issue.issue_code in SEMANTIC_SUBMIT_DIAGNOSTIC_CODES]
    if semantic_submit_diagnostics:
        issues = [issue for issue in issues if issue.issue_code not in SEMANTIC_SUBMIT_DIAGNOSTIC_CODES]

    if issues:
        package_repair_groups = {
            "fail_closed_with_mapped_sibling": fail_closed_sibling_repairs,
            "excluded_slice_with_mapped_sibling": excluded_slice_sibling_repairs,
            "fail_closed_count_matched_target_sibling": fail_closed_count_sibling_repairs,
            "excluded_count_matched_uninspected_subject": excluded_count_uninspected_repairs,
            "excluded_singleton_visible_subject_candidate": excluded_singleton_subject_repairs,
            "excluded_main_locator_with_mapped_title_sibling": excluded_main_sibling_repairs,
            "supplemental_main_episodes_without_concrete_extra_reason": supplemental_main_episode_repairs,
            "numbered_special_exclusion_needs_target_evidence": numbered_special_exclusion_repairs,
            "fail_closed_negative_target_absence_outcome_inconsistent": fail_closed_negative_target_absence_repairs,
            "singleton_target_alias_matches_excluded_local_better": singleton_target_alias_repairs,
            "mapped_target_title_bridge_missing": mapped_target_title_bridge_repairs,
            "mapped_title_season_mismatch": mapped_title_season_mismatch_repairs,
            "mapped_packaging_extra_marker_without_specific_target": mapped_packaging_extra_target_repairs,
            "excluded_episode_title_search_not_exhausted": excluded_episode_title_search_repairs,
            "excluded_title_tail_search_not_exhausted": excluded_title_tail_search_repairs,
            "excluded_local_has_visible_title_pairing_target": excluded_visible_title_pairing_repairs,
            "excluded_title_tail_unresolved_after_search": excluded_title_tail_unresolved_repairs,
            "fail_closed_with_visible_slice_pairing": fail_closed_slice_pairing_repairs,
            "fail_closed_title_tail_bridge_uninspected": fail_closed_title_tail_bridge_repairs,
            "excluded_singleton_with_unassigned_visible_target_items": excluded_singleton_unassigned_repairs,
            "fail_closed_singleton_with_unassigned_visible_target_items": fail_closed_singleton_unassigned_repairs,
            "duplicate_like_singleton_exclusion_title_mismatch": duplicate_like_singleton_exclusion_repairs,
        }
        feedback_units_for_feedback = _feedback_units_with_package_repairs(
            feedback_units,
            package_repair_groups,
        )
        counts: dict[str, int] = {}
        for issue in issues:
            counts[issue.issue_code] = counts.get(issue.issue_code, 0) + 1
        missing_locator_hints = _local_locator_hints_for_refs(registry, missing)
        duplicate_local_hints = _local_locator_hints_for_refs(registry, duplicates)
        required_missing_work_units = _missing_work_unit_repairs(missing_locator_hints)
        duplicate_target_repair_units = _duplicate_target_repair_units(registry, duplicate_targets, target_usage)
        unit_mechanical_checklist = _unit_mechanical_checklist(feedback_units_for_feedback, duplicate_target_repair_units)
        repair_hints: list[str] = []
        if missing:
            repair_hints.append("Cover each missing must_account local locator exactly once, or cover the suggested local episode slices if only part of a group remains.")
        if duplicates:
            repair_hints.append("Remove the duplicated local locator from all but one work unit; overlap is mechanical and cannot be accepted.")
        if duplicate_targets:
            repair_hints.append("A target episode item may be assigned at most once; change one unit's target/outcome based on your semantic judgment.")
        if any(issue.issue_code == "target_episode_surface_missing" for issue in issues):
            repair_hints.append("Inspect the target subject with details/episodes/related before using an episode range whose item surface is not visible.")
        if fail_closed_sibling_repairs:
            repair_hints.append("Do not fail_closed a leftover episode slice while adjacent slices from the same local parent are mapped; resolve the parent/sibling contradiction.")
        if excluded_slice_sibling_repairs:
            repair_hints.append(
                "Do not clear a leftover episode slice as supplemental/target_absent while adjacent slices from the same local parent are mapped unless the slice has a hard duplicate/copy/packaging reason."
            )
        if fail_closed_count_sibling_repairs:
            repair_hints.append(
                "Do not finish fail_closed while a same-title-family numbered local group count-matches a target subject owned by a smaller local sibling; resolve ownership first."
            )
        if excluded_count_uninspected_repairs:
            repair_hints.append(
                "Inspect visible same-count target subjects before excluding a numbered local group linked by package title evidence."
            )
        if excluded_singleton_subject_repairs:
            repair_hints.append(
                "Do not exclude a singleton local title that matches a visible multi-episode subject before considering mapped_composite_feature or inspecting the target surface."
            )
        if excluded_main_sibling_repairs:
            repair_hints.append("Do not exclude a multi-file main locator while a smaller same-title-family local slice is mapped; resolve target ownership.")
        if supplemental_main_episode_repairs:
            repair_hints.append(
                "Do not clear main/main-episodes locators as supplemental without a concrete extra/duplicate/recap/packaging reason."
            )
        if numbered_special_exclusion_repairs:
            repair_hints.append(
                "Do not clear numbered SP groups as supplemental/target_absent/non_bangumi without target-side special evidence or a concrete unresolved fail_closed reason."
            )
        if fail_closed_negative_target_absence_repairs:
            repair_hints.append(
                "Do not express a resolved no-corresponding-target conclusion as fail_closed; choose target_absent/supplemental/non_bangumi with inspected support, or state the remaining ambiguity."
            )
        if singleton_target_alias_repairs:
            repair_hints.append(
                "For singleton named specials/movies, do not assign a target to a local unit when the target title aliases or source-query provenance match an excluded local unit more strongly."
            )
        if mapped_target_title_bridge_repairs:
            repair_hints.append(
                "When a mapped target's visible title aliases do not overlap the local title/labels, do not use the selected target title itself as the bridge; search/inspect a better alias, choose another target, or fail_closed."
            )
        if mapped_title_season_mismatch_repairs:
            repair_hints.append(
                "Do not map an unseasoned or conflicting local title to an explicitly season-suffixed target while visible alternate candidates remain unaddressed."
            )
        if excluded_title_tail_search_repairs:
            repair_hints.append(
                "Do not accept target_absent/supplemental/non_bangumi for a small named main/movie unit while its distinctive title-tail query hints remain unsearched; batch search those hints or fail_closed the unit."
            )
        if excluded_visible_title_pairing_repairs:
            repair_hints.append(
                "Do not exclude a main/movie local slice while visible title-tail target pairing options exist; map one if semantically correct, choose another target, give a hard duplicate/packaging reason, or fail_closed."
            )
        if excluded_title_tail_unresolved_repairs:
            repair_hints.append(
                "Do not exclude a named main/movie local slice after searched title-tail queries still have no visible bridge; search another concrete alias or fail_closed that exact locator."
            )
        if fail_closed_slice_pairing_repairs:
            repair_hints.append(
                "Do not fail_closed a multi-file parent while visible local episode slice pairing options remain; split the local side and resolve each exact slice."
            )
        if fail_closed_title_tail_bridge_repairs:
            repair_hints.append(
                "Do not finish fail_closed for a searched title-tail while visible source-query bridge candidates remain uninspected; inspect a candidate or name the remaining evidence gap."
            )
        if excluded_episode_title_search_repairs:
            repair_hints.append(
                "Do not accept supplemental/target_absent/non_bangumi for an episode-like translated/localized title when the only visible target subjects have no title-token bridge; search original/localized aliases or fail_closed."
            )
        if excluded_singleton_unassigned_repairs:
            repair_hints.append(
                "Do not exclude a singleton local without a concrete extra/duplicate reason while mapped subjects still have unassigned visible episode items."
            )
        if fail_closed_singleton_unassigned_repairs:
            repair_hints.append(
                "Do not fail_closed a singleton local while mapped subjects still expose unassigned visible episode items; map one if semantically correct or name why each visible candidate is not the owner."
            )
        if duplicate_like_singleton_exclusion_repairs:
            repair_hints.append(
                "Do not use duplicate/alternate/copy/packaging exclusion for a singleton main local whose distinctive title tokens do not match a mapped owner."
            )
        verifier = CaseVerifierResult(passed=False, issues=issues, summary="human_case_submit_rejected")
        feedback = {
            "accepted": False,
            "status": "rejected",
            "package": {
                "issue_count": len(issues),
                "issue_counts": counts,
                "missing_local_ref_count": len(missing),
                "duplicate_local_ref_count": len(duplicates),
                "duplicate_target_count": len(duplicate_targets),
                "missing_local_ref_sample": missing[:12],
                "duplicate_local_ref_sample": duplicates[:12],
                "missing_local_locator_hints": missing_locator_hints,
                "required_missing_work_units": required_missing_work_units,
                "coverage_missing_instruction": (
                    "Your next submit must add one work unit for every required_missing_work_units entry, "
                    "unless you intentionally cover the same missing files with one of its suggested episode slices."
                ) if required_missing_work_units else "",
                "duplicate_local_locator_hints": duplicate_local_hints,
                "duplicate_target_details": [
                    {
                        "target_item": _target_item_locator_for_ref(registry, ref),
                        "usage": target_usage.get(ref, [])[:8],
                    }
                    for ref in duplicate_targets[:12]
                ],
                "duplicate_target_repair_units": duplicate_target_repair_units,
                "fail_closed_mapped_sibling_repairs": fail_closed_sibling_repairs,
                "excluded_slice_mapped_sibling_repairs": excluded_slice_sibling_repairs,
                "fail_closed_count_matched_target_sibling_repairs": fail_closed_count_sibling_repairs,
                "excluded_count_matched_uninspected_subject_repairs": excluded_count_uninspected_repairs,
                "excluded_singleton_visible_subject_repairs": excluded_singleton_subject_repairs,
                "excluded_main_mapped_sibling_repairs": excluded_main_sibling_repairs,
                "supplemental_main_episode_repairs": supplemental_main_episode_repairs,
                "numbered_special_exclusion_repairs": numbered_special_exclusion_repairs,
                "fail_closed_negative_target_absence_repairs": fail_closed_negative_target_absence_repairs,
                "singleton_target_alias_repairs": singleton_target_alias_repairs,
                "mapped_target_title_bridge_repairs": mapped_target_title_bridge_repairs,
                "mapped_title_season_mismatch_repairs": mapped_title_season_mismatch_repairs,
                "excluded_episode_title_search_repairs": excluded_episode_title_search_repairs,
                "excluded_title_tail_search_repairs": excluded_title_tail_search_repairs,
                "excluded_visible_title_pairing_repairs": excluded_visible_title_pairing_repairs,
                "excluded_title_tail_unresolved_repairs": excluded_title_tail_unresolved_repairs,
                "fail_closed_slice_pairing_repairs": fail_closed_slice_pairing_repairs,
                "fail_closed_title_tail_bridge_repairs": fail_closed_title_tail_bridge_repairs,
                "excluded_singleton_unassigned_target_repairs": excluded_singleton_unassigned_repairs,
                "fail_closed_singleton_unassigned_target_repairs": fail_closed_singleton_unassigned_repairs,
                "duplicate_like_singleton_exclusion_mismatch_repairs": duplicate_like_singleton_exclusion_repairs,
                "unit_mechanical_checklist": unit_mechanical_checklist,
                "mechanical_repair_hints": repair_hints,
                "duplicate_target_repair_instruction": (
                    "A target episode item may be assigned at most once. Keep the semantically correct owner, "
                    "and change the other local unit to a different visible target or to target_absent/"
                    "supplemental/non_bangumi if that is your judgment."
                ) if duplicate_targets else "",
            },
            "units": feedback_units_for_feedback[:24],
            "semantic_diagnostics": [
                {
                    "issue_code": issue.issue_code,
                    "ref": issue.ref,
                    "message": issue.message,
                    "related_refs": list(issue.related_refs or []),
                }
                for issue in semantic_submit_diagnostics[:24]
            ],
            "available_action": (
                "Revise the same package-level submit resolution. You may split local episode-like locators "
                "with /episode/N or /episodes/A-B, inspect missing target episode surfaces, or mark only the "
                "semantically unmatched local sub-locator as bangumi_target_absent/supplemental/non_bangumi. "
                "The fixed layer will only recheck locators, coverage, duplicate targets, and accounting."
            ),
        }
        return SubmitCompileResult(False, None, verifier, feedback, mapped_file_count, excluded_file_count)

    if fail_closed_units:
        output = CaseJudgeOutput(
            action="fail_closed",
            findings=findings,
            assignment_intents=assignments,
            fail_closed_reasons=[
                FailClosedReason(
                    ref=f"HFR{index}",
                    reason_kind="insufficient_evidence",
                    description=str(unit.get("reason") or ""),
                    related_refs=list(unit.get("local_refs") or [])[:8],
                )
                for index, unit in enumerate(fail_closed_units, start=1)
            ],
            summary="agent_fail_closed_from_submit",
        )
        verifier = CaseVerifierResult(passed=True, issues=[], summary="human_case_fail_closed_submit_accepted")
        feedback_status = "fail_closed"
    else:
        output = CaseJudgeOutput(
            action="submit_verdict",
            findings=findings,
            assignment_intents=assignments,
            summary=args.resolution.package_reason or args.reason or "human_case_agent package resolution accepted",
        )
        verifier = CaseVerifierResult(passed=True, issues=[], summary="human_case_submit_accepted")
        feedback_status = "accepted"
    feedback = {
        "accepted": True,
        "status": feedback_status,
        "mapped_file_count": mapped_file_count,
        "excluded_file_count": excluded_file_count,
        "fail_closed_file_count": fail_closed_file_count,
        "assignment_count": len(assignments),
        "units": feedback_units[:24],
        "semantic_diagnostics": [
            {
                "issue_code": issue.issue_code,
                "ref": issue.ref,
                "message": issue.message,
                "related_refs": list(issue.related_refs or []),
            }
            for issue in semantic_submit_diagnostics[:24]
        ],
    }
    return SubmitCompileResult(True, output, verifier, feedback, mapped_file_count, excluded_file_count)


def _parse_tool_call(response: dict[str, object]) -> tuple[HumanToolCall | None, str]:
    calls = response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else []
    if not calls:
        return None, "human_case_agent_no_tool_call"
    first = calls[0]
    if not isinstance(first, dict):
        return None, "human_case_agent_invalid_tool_call"
    tool_name = str(first.get("name") or "")
    if tool_name not in TOOL_ARG_MODELS:
        return None, f"human_case_agent_unknown_tool:{tool_name}"
    try:
        parsed = json.loads(str(first.get("arguments") or "{}"))
    except json.JSONDecodeError as exc:
        return None, f"human_case_agent_tool_args_json_error:{exc}"
    if not isinstance(parsed, dict):
        return None, "human_case_agent_tool_args_not_object"
    try:
        arguments = TOOL_ARG_MODELS[tool_name].model_validate(parsed)
    except ValidationError as exc:
        return None, f"human_case_agent_tool_args_schema_error:{exc}"
    return HumanToolCall(
        tool_name=tool_name,
        arguments=arguments,
        raw_arguments=parsed,
        call_id=str(first.get("call_id") or first.get("id") or ""),
        response_id=str(response.get("id") or ""),
    ), ""


HUMAN_CASE_AGENT_INSTRUCTIONS = """You are HumanCaseAgent for one Local->Bangumi package.

Use natural locators, not LF/BS/BE refs. You have four tools only:
inspect, search, note, submit.

You are the only semantic brain. There are no child sessions and no separate
QueryComposer, MappingDraftEditor, Judge, CasePlanner, ledger, board, or patch
roles. Decide work units, targets, target_absent, supplemental, non_bangumi, or
fail_closed yourself.

The fixed layer only checks mechanics: locator resolution, schema, exact-once
coverage for must_account local locators, support locator existence, work-unit
overlap, duplicate target items, accounting, loop/budget.

Maintain CASE_STATE.case_memory.cognitive_workspace as your desk. Keep primary
hypotheses, active work units, attention focus, agenda, rejected/noisy
candidates, evidence gaps, and resolution readiness current through note when
your view changes. Treat that workspace as higher priority than raw search
results or old repair text. If a candidate is low relevance, mark it rejected
or noisy so later searches do not keep presenting it as an equal choice.

Prefer batch actions. Simple TV packages should usually be search -> inspect ->
submit. Mixed or multi-season packages must use batch search/inspect: put all
clean title aliases you need into one search call, then inspect all plausible
target subjects in one inspect call. Do not spend one turn per season/title when
the tool can batch them. One repair submit is fine if mechanical feedback
rejects it.
CASE_STATE.desk.recommended_search_queries is mechanically derived from local
work-unit titles and filenames. For multi-season/OAD/special packages, use
those work-unit title queries as the first batch search surface unless you have
a better clean official/original alias. Do not search one raw release filename
per episode.
Do not use submit as an exploration tool. If submit is rejected, read the
locator/work-unit feedback and change the specific local locator, target
locator, episode range, support, or outcome before submitting again. If the
same submit rejection repeats, use inspect/search/note or make a concrete
resolution change first.
After a submit rejection, CASE_STATE.case_memory.active_repair_agenda is your
top-priority desk. Each item names the work unit/locator, blocking issue,
required_next_action, and closure_condition. Do not call submit again merely to
try the same plan. Before another submit, close the agenda item by adding needed
search/inspect evidence, changing the cited resolution fields, or submitting
that exact local locator as fail_closed with a concrete evidence blocker.
Search results only identify candidate subjects. Before submitting any
mapped_* work unit that uses target episodes or episode_start/episode_end,
inspect the chosen target subject with scope [details, episodes, related].
Use note after a rejection when your semantic hypothesis or work-unit agenda
changed and that update needs to stay in the desk; otherwise use inspect/search
or a corrected submit that satisfies the active repair agenda's closure condition.
If CASE_STATE.case_memory.immediate_repair_focus is present, handle that
mechanical agenda before broader package reasoning. It names exact local
locators or target conflicts that are still not mechanically covered; choose
their semantic outcome yourself, but do not omit those locators again.
If feedback.package.required_missing_work_units is present, the next submit must
include those local locators exactly once, choosing the semantic outcome yourself.
If submit feedback for a mapped range includes visible_alternate_subjects or an
available_action to inspect a target, use that inspect surface before
fail_closed, especially when an alternate subject has the same episode count as
the local locator. The fixed layer is not choosing the subject; it is exposing a
mechanically visible candidate for your semantic comparison.
For romanized title cues, choose your own likely official/original title aliases
as search queries when you know them; the fixed layer will not invent aliases for
you. Do the same for English localized titles: if an English search returns
unrelated candidates, use your own knowledge to try the original Japanese title,
Chinese title, season subtitle, or special/OVA subtitle before fail_closed. For
franchise/multi-season packages, include each distinct season/special title
alias in the same search call whenever possible.
When a long romanized franchise title contains a distinctive season/subtitle
token such as an uppercase code or roman numeral, compare the specific title-tail
candidate with the broad franchise candidate; episode count alone is not enough
to choose the broad first-season subject.
Search has a small tool-call budget because each search call accepts multiple
queries. After the budget is exhausted, continue with inspect or submit using
visible evidence instead of spending more turns on search.

Never ask to call split_work_units, patches, ledgers, hidden refs, or child
agents. Submit work-unit decisions using local:// and target:// locators. The
first submit should be a complete package-level resolution. After a submit
rejection, CASE_STATE.case_memory.saved_mechanically_ok_work_units lists the
agent decisions that the fixed layer can mechanically reuse; you may then
submit only the blocked, missing, or intentionally changed units. The fixed
layer will merge saved units and recheck the full package mechanically.
For regular TV spans you may use a subject target with episode_start
and episode_end after inspecting episodes, or a target://.../episodes/A-B span.
For a single local movie/feature file that semantically covers a Bangumi subject
represented as multiple episode parts, use outcome=mapped_composite_feature with
the visible target://.../episodes/A-B locator. This is your semantic judgment;
the fixed layer only checks that the local side is one file, target parts are
visible, support exists, and target items are not duplicated elsewhere.
Do not mark such a main movie/feature locator as supplemental merely because the
Bangumi surface is split into multiple parts; supplemental is for actual extras,
packaging material, duplicate local copies, menus, previews, NCOP/NCED, etc.
If a local locator is episode-like, the desk or inspect output may show
local://.../episode/N and local://.../episodes/A-B sub-locators. Use them when
only part of a local group maps to Bangumi and another part is target_absent,
supplemental, or non_bangumi. The fixed layer is not choosing that split; it
only exposes filename-numbered slices you can cite.
If submit feedback includes local_target_title_pairing_options, it has found
visible one-item target candidates whose title/search tokens pair with specific
local numbered slices. Use those slice locators only if you judge the semantic
ownership correct; otherwise choose another target/outcome or fail_closed.
The local locator category is a filename/packaging clue. Treat special-marker,
previews, packaging-extras, CM/Menu/NCOP/NCED/PV style groups as separate work
units from regular main-episodes. Do not map those groups to a regular TV
episode span only because their numbers overlap. Search/inspect a special,
OVA/OAD/SP, related, or subject-level target if you believe they are mapped;
otherwise classify them as supplemental/non_bangumi/target_absent according to
your semantic judgment.
For numbered SP groups, lack of a positive Bangumi special target after a finite
same-series/related inspection is not automatically fail_closed. If the local
SP labels and package context make the group bonus/extra material, submit
target_absent/supplemental/non_bangumi and cite the inspected target locator as
support for the negative target-side evidence. Use fail_closed only when you
cannot safely decide whether the SP group is extra material, a mapped special,
or target_absent from the visible evidence.
A target-side check that exposes the same-series/related subject but no
corresponding SP/OVA/OAD item is target-side evidence. Do not require a positive
Bangumi item in order to choose target_absent/supplemental for local bonus SP
material; the positive item is only required when you choose a mapped_* outcome.
If a previous submit rejected target_absent/supplemental/non_bangumi for a
numbered SP group, that rejection means the support/negative-evidence shape was
missing. It does not mean target_absent/supplemental is forbidden. Add the
inspected same-series/related target support and resubmit that semantic outcome
when that is your conclusion.
If submit feedback returns numbered_special_exclusion_repairs, your previous
supplemental/target_absent/non_bangumi outcome for that numbered SP group is not
mechanically accepted. Use any listed same_count_visible_subjects or
target_surface_actions for more target evidence; if
negative_target_absence_support_candidates is listed, cite one candidate target
locator as support and state the concrete negative evidence that no
corresponding Bangumi item is visible. If no such evidence is listed or the
evidence remains unresolved, submit target_absent/supplemental/non_bangumi only
if you cite an inspected same-series or related target locator as support.
Otherwise change that exact local locator to outcome=fail_closed with a concrete
evidence gap. Do not resubmit the same unsupported exclusion outcome.
If submit feedback returns fail_closed_negative_target_absence_repairs, your
fail_closed reason already describes target absence instead of unresolved
ambiguity. Either submit the listed target_absent/supplemental/non_bangumi shape
with one negative_target_absence_support_candidates target as support, or rewrite
fail_closed to name the remaining ambiguity beyond positive-target absence.
If local labels use S00, Special, OVA/OAD/SP, final/finale, or similar
special-season wording, compare visible same-count special/finale subjects before
mapping the files to episode 1..N of a broad regular season. A mechanically available regular season slice is not enough by itself when the local title surface signals special-season ownership.
Use target_absent/supplemental/non_bangumi only when that is your actual
semantic conclusion after search/inspect. Do not use them to bypass a visible
matching Bangumi subject or to avoid a duplicate-target repair.
For small named main/movie-style units, target_absent requires that you have
searched the unit's own distinctive title-tail aliases, not only the broad
franchise title. If submit feedback returns excluded_title_tail_search_repairs,
run one batch search for search_queries_to_try or submit that exact local
locator as fail_closed with the remaining evidence gap; do not use a broad
same-franchise support target as proof that the named unit has no Bangumi owner.
If submit feedback returns excluded_visible_title_pairing_repairs, the visible
target surface already contains title-tail pairing candidates for a main/movie
local locator you excluded. Map a pairing if you judge it correct, choose a
different visible target, give a hard duplicate/packaging non-owner reason, or
fail_closed that exact local locator if the evidence is still unsafe.
If submit feedback returns excluded_title_tail_unresolved_repairs, you have
searched the current title-tail hints but still have no visible target bridge.
Do not clear that main/movie local locator from only a broad same-franchise
subject. If visible_source_query_bridge_targets is listed, inspect or use one
only when you judge it semantically correct; source-query provenance is a lead,
not a title alias. Otherwise search another concrete alias you know or
fail_closed that exact locator with the remaining evidence gap.
For duplicate-target repairs, compare the conflicting units' local_facts and
target_facts. A multi-file main-episodes locator with a distinct season/title
suffix is normally a separate work unit, not a duplicate local copy. Do not
drop such a unit to supplemental merely to make duplicate_target pass; correct
the target locator, inspect/search another visible subject, split the local
locator, or fail_closed if it is genuinely unresolved. Mark it supplemental
only if your semantic conclusion is that it is really an extra, alternate copy,
recap/preview, or packaging duplicate, and say that concrete reason.
If two local work units are duplicate local copies, alternate encodes, recap
variants, previews, or packaging versions of the same Bangumi episode item, the
fixed layer cannot accept assigning both to that same target item. Choose the
one unit that semantically owns the Bangumi target, and submit the other local
unit as supplemental/non_bangumi/target_absent with a concrete duplicate-copy or
extra-material reason.
For duplicate-target conflicts between named singleton specials/movies, compare
each conflicting local_facts.representative_labels against target_facts.title
and target_facts.title_aliases. Use target_facts.source_query_texts only as
provenance for why that target was surfaced, not as a positive title alias. If
the visible title facts or source-query provenance match one named local unit
more specifically than the other, keep that matching unit as owner;
search/inspect a distinct target for the other named unit or fail_closed if it
remains unresolved. Do not let a different named special take a target only
because that target is already visible.
Use fail_closed only when the local locator remains semantically unresolved or
unsafe after visible evidence. If you can positively conclude a locator is a
bonus, preview, packaging extra, NCOP/NCED, menu, SP bundle extra, or otherwise
should not be assigned to a Bangumi episode item, submit that locator as
supplemental/non_bangumi or target_absent with your concrete reason. A missing
one-to-one Bangumi episode surface for such extra material is not by itself a
fail_closed reason.
The same applies to distinctive companion/travel/interview/live-action/behind-
the-scenes/spin-off/minisode/chibi-web-short bonus titles inside an otherwise
identified anime release: if search does not expose a safe Bangumi item but the
local title and package context make it clear that the unit is extra material,
classify it as supplemental or non_bangumi rather than fail_closed.
Also treat recap, compilation, digest, theater-manners, cast-talk, and summary
materials this way when your semantic conclusion is that they should not own a
Bangumi episode item in this package.
If an inspected target has the right title family but the episode count does
not fit the local main span, do not fail_closed solely on that candidate. Inspect
visible related/prequel/sequel/same-title candidates or search cleaner original
or Chinese title aliases, then choose the semantically correct target yourself.
For split-cour or multi-season releases, local filenames may continue numbering
across discs while Bangumi separates later cours/seasons into subjects whose
episodes start again at 1. In that case, split by local locator or episode slice
and map the later local slice to the later visible subject with the appropriate
target episode_start/episode_end. The fixed layer checks only counts and visible
target items; you decide whether the later subject is semantically correct.
When submit feedback exposes local_slice_mapping_options, the fixed layer is
telling you the parent local locator has legal visible local://.../episode/N
sub-locators. If the listed title pairing is semantically correct, submit each
slice as its own work unit; do not fail_closed only because the original parent
unit was too broad.
Do not map an unseasoned local companion title to a season-suffixed target if a
visible unseasoned same-title-family subject exists; inspect or use the
unseasoned subject unless your semantic evidence says otherwise.
Do not assign a partial target span to a local unit solely because the count matches.
Compare the local title/representative labels with the target subject
title and with other local locators in the same package. If another local
locator has the distinctive title and file count matching the full target
subject, that locator is the likely owner; search/inspect the non-matching local
unit's own title or classify it as supplemental/non_bangumi/target_absent if it
is companion/travel/interview/bonus material.
If a numbered SP/OVA/OAD/special local group has the same file count as a visible
target subject, while a smaller same-title-family singleton is mapped to one item
of that subject, explicitly compare ownership. Do not finish fail_closed for the
numbered group until you decide whether the numbered group owns that target span
or the smaller singleton/compilation is supplemental.
If submit feedback says a count-matched visible subject is uninspected, inspect it
before declaring the matching numbered local group supplemental or target_absent.
For a singleton local file whose title matches a visible multi-episode target
subject, consider mapped_composite_feature when the one file is a compilation or
combined feature covering that target span; do not discard it solely because
file_count differs from target episode count.
Avoid dry_run during normal operation; it costs an extra turn. Use dry_run only
when you intentionally want a mechanical preflight. For normal final answers,
prefer dry_run=false. If a dry_run submit is accepted, the case is not finished;
next call submit the same resolution with dry_run=false unless you intentionally
changed the resolution.
"""


def _turn_tail(desk: dict[str, object], session: HumanCaseSession, *, max_turns: int) -> dict[str, object]:
    def _compact_memory_observation(observation: dict[str, object]) -> dict[str, object]:
        tool = str(observation.get("tool") or "")
        output = observation.get("output") if isinstance(observation.get("output"), dict) else {}
        if tool == "submit":
            return {
                "tool": tool,
                "output": {
                    key: output.get(key)
                    for key in (
                        "accepted",
                        "status",
                        "active_repair_agenda",
                        "issue_counts",
                        "required_missing_work_units",
                        "duplicate_target_repair_units",
                        "blocking_units",
                        "fail_closed_count_matched_target_sibling_repairs",
                        "excluded_count_matched_uninspected_subject_repairs",
                        "excluded_singleton_visible_subject_repairs",
                        "fail_closed_mapped_sibling_repairs",
                        "excluded_main_mapped_sibling_repairs",
                        "supplemental_main_episode_repairs",
                        "numbered_special_exclusion_repairs",
                        "excluded_title_tail_search_repairs",
                        "excluded_visible_title_pairing_repairs",
                        "excluded_title_tail_unresolved_repairs",
                        "mechanical_repair_hints",
                        "visible_target_surface_missing_units",
                        "repeat_rejection_warning",
                        "required_next_action",
                    )
                    if key in output
                },
            }
        if tool == "inspect":
            observations = output.get("observations") if isinstance(output.get("observations"), list) else []
            compact_observations: list[dict[str, object]] = []
            for item in observations[:8]:
                if not isinstance(item, dict):
                    continue
                compact_observations.append(
                    {
                        key: item.get(key)
                        for key in (
                            "locator",
                            "issue",
                            "kind",
                            "title",
                            "file_count",
                            "episode_range",
                            "episode_locator_syntax",
                            "available_episode_numbers",
                            "episode_locators",
                            "episodes",
                            "related",
                            "aliases",
                        )
                        if key in item
                    }
                )
            return {"tool": tool, "output": {"accepted": output.get("accepted"), "observations": compact_observations}}
        if tool == "search":
            queries = output.get("queries") if isinstance(output.get("queries"), list) else []
            def _compact_result(result: dict[str, object]) -> dict[str, object]:
                return {
                    key: result.get(key)
                    for key in (
                        "target",
                        "title",
                        "eps",
                        "date",
                        "matched_query",
                        "relation",
                        "relevance_layer",
                        "suppression_reason",
                    )
                    if key in result
                }

            return {
                "tool": tool,
                "output": {
                    "accepted": output.get("accepted"),
                    "queries": [
                        {
                            "query": item.get("query"),
                            "result_tiers": [
                                {
                                    "layer": tier.get("layer"),
                                    "results": [
                                        _compact_result(result)
                                        for result in list(tier.get("results") or [])[:6]
                                        if isinstance(result, dict)
                                    ],
                                }
                                for tier in list(item.get("result_tiers") or [])[:4]
                                if isinstance(tier, dict)
                            ],
                            "results": [
                                _compact_result(result)
                                for result in list(item.get("results") or [])[:8]
                                if isinstance(result, dict)
                            ],
                        }
                        for item in queries[-4:]
                        if isinstance(item, dict)
                    ],
                    "search_progress": output.get("search_progress"),
                    "new_subject_count": output.get("new_subject_count"),
                    "total_result_count": output.get("total_result_count"),
                    "noise_candidate_count": output.get("noise_candidate_count"),
                    "search_surface_note": output.get("search_surface_note"),
                    "next_action_hint": output.get("next_action_hint"),
                },
            }
        return {"tool": tool, "output": output}

    recent_observations = [
        _compact_memory_observation(item)
        for item in session.observations[-5:]
        if isinstance(item, dict)
    ]
    latest_submit = next(
        (
            _compact_memory_observation(item)
            for item in reversed(session.observations)
            if isinstance(item, dict) and item.get("tool") == "submit"
        ),
        None,
    )
    remaining_turns = max(0, int(max_turns) - int(session.turn_count))
    must_account_count = int((desk.get("resolution_contract") or {}).get("must_account_locator_count") or 0)
    saved_count = len(session.draft_work_units)
    budget_pressure = remaining_turns <= 2
    repair_finalization_pressure = remaining_turns <= REPAIR_FINALIZATION_TURN_WINDOW
    latest_repair_observation = _latest_submit_repair_observation(session)
    has_open_repair = _has_open_submit_repair(latest_repair_observation)
    target_surface_action_open = _repair_has_uninspected_target_surface_action(session, latest_repair_observation)
    repair_search_queries = _repair_search_queries_to_try(latest_repair_observation)
    forced_finalization = (
        budget_pressure
        and has_open_repair
        and bool(session.draft_work_units)
        and (remaining_turns <= 1 or not target_surface_action_open)
    )
    repair_finalization_guard = _repair_finalization_guard_for_prompt(session, max_turns=max_turns)
    return {
        "case_desk": desk,
        "case_memory": {
            "active_repair_agenda": _active_repair_agenda_for_prompt(session),
            "near_cap_repair_finalization_guard": repair_finalization_guard,
            "immediate_repair_focus": _immediate_repair_focus(session),
            "cognitive_workspace": _compact_cognitive_workspace(session.cognitive_workspace),
            "notes": session.notes[-12:],
            "recent_observations": recent_observations,
            "latest_submit_repair": latest_submit,
            "saved_mechanically_ok_work_units": _draft_work_unit_summary(session.draft_work_units),
            "saved_work_unit_count": len(session.draft_work_units),
            "draft_revision_count": session.draft_revision_count,
            "action_health": _action_health_observation(session, max_turns=max_turns),
            "tool_sequence": session.tool_sequence[-12:],
            "submit_rejection_count": session.submit_rejection_count,
            "submit_rejection_issue_counts": session.submit_rejection_issue_counts,
            "search_budget": {
                "max_search_tool_calls": SEARCH_TOOL_CALL_BUDGET,
                "used_search_tool_calls": session.search_call_count,
                "exhausted": session.search_call_count >= SEARCH_TOOL_CALL_BUDGET,
                "repair_search_queries_to_try": repair_search_queries[:8],
                "instruction": (
                    "The latest repair has explicit search_queries_to_try. Use one batched search call for those queries before another submit."
                    if repair_search_queries and session.search_call_count < SEARCH_TOOL_CALL_BUDGET
                    else
                    "Search tool-call budget is exhausted. Because search accepts batched queries, do not call search again; inspect visible candidates or submit a concrete resolution/fail_closed."
                    if session.search_call_count >= SEARCH_TOOL_CALL_BUDGET
                    else "Use batch search if more title aliases are needed."
                ),
            },
            "turn_budget": {
                "max_turns": max_turns,
                "completed_turns": session.turn_count,
                "remaining_turns": remaining_turns,
                "budget_pressure": budget_pressure,
                "repair_finalization_pressure": repair_finalization_pressure,
                "forced_finalization": forced_finalization,
                "allowed_tool_when_forced": "submit" if forced_finalization else "",
                "target_surface_action_open": target_surface_action_open,
                "saved_work_unit_count": saved_count,
                "must_account_locator_count": must_account_count,
                "finalization_guard_issue": repair_finalization_guard.get("issue") if repair_finalization_guard else "",
                "instruction": (
                    "A submit repair agenda is open and remaining turns are limited. The next tool call must be submit. "
                    "Submit the remaining blocking/missing local locators exactly once. If you cannot safely map or exclude "
                    "a remaining locator from visible evidence, submit it as outcome=fail_closed with a concrete reason. "
                    "Saved mechanically-ok units are merged by the fixed layer."
                    if forced_finalization
                    else "Budget pressure is high, but the latest repair names target_surface_actions. Inspect those target locators now, then submit the repaired units on the next turn."
                    if budget_pressure and target_surface_action_open
                    else "Budget pressure is high. Do not search unless the latest repair explicitly requires a new target. "
                    "Prefer submit for the remaining blocking/missing local units; saved mechanically-ok units are merged."
                    if budget_pressure
                    else "Continue normal inspect/search/submit investigation."
                ),
            },
        },
    }


def _call_human_agent(
    ai_client: object,
    desk: dict[str, object],
    session: HumanCaseSession,
    *,
    max_turns: int,
) -> tuple[HumanToolCall | None, HumanCaseSession, dict[str, object], str]:
    call_fn = getattr(ai_client, "call_responses_tool_agent", None)
    if not callable(call_fn):
        return None, session, {"note": "human_case_agent_transport_unavailable"}, "human_case_agent_transport_unavailable"
    tail = _turn_tail(desk, session, max_turns=max_turns)
    tail_text = _stable_json_text(tail)
    case_state_text = f"CASE_STATE:\n{tail_text}"
    tail_bytes = case_state_text.encode("utf-8")
    previous_tail_bytes = session.last_tail_bytes or b""
    tail_lcp_bytes = _common_prefix_byte_count(previous_tail_bytes, tail_bytes) if previous_tail_bytes else 0
    instructions_sha256 = _sha256_text(HUMAN_CASE_AGENT_INSTRUCTIONS)
    tool_definitions = human_case_tool_definitions()
    tools_sha256 = _sha256_json(tool_definitions)
    case_desk_sha256 = _sha256_json(desk)
    tail_sha256 = hashlib.sha256(tail_bytes).hexdigest()
    session.turn_tail_estimated_tokens = max(session.turn_tail_estimated_tokens, _estimate_tokens(tail_text))
    if not session.stable_prefix_estimated_tokens:
        session.stable_prefix_estimated_tokens = _estimate_tokens(HUMAN_CASE_AGENT_INSTRUCTIONS)
    if not session.first_turn_estimated_tokens:
        session.first_turn_estimated_tokens = session.stable_prefix_estimated_tokens + _estimate_tokens(tail_text)
    tool_choice = _budget_pressure_tool_choice(session, max_turns=max_turns)
    if tool_choice == "required":
        tool_choice = _search_budget_tool_choice(session)
    response = call_fn(
        instructions=HUMAN_CASE_AGENT_INSTRUCTIONS,
        input_items=[{"role": "user", "content": case_state_text}],
        tools=tool_definitions,
        max_output_tokens=4096,
        parallel_tool_calls=False,
        tool_choice=tool_choice,
        conversation_id="",
        prompt_cache_key=session.prompt_cache_key,
        session_id=session.http_session_id,
    )
    if not isinstance(response, dict):
        return None, session, {"note": "human_case_agent_call_failed", "error_kind": "human_case_agent_no_response"}, "human_case_agent_no_response"
    tool_call, parse_error = _parse_tool_call(response)
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else usage.get("prompt_tokens_details")
    cached_tokens = int((details or {}).get("cached_tokens") or (details or {}).get("cache_read_input_tokens") or 0) if isinstance(details, dict) else 0
    next_tool_sequence = [*session.tool_sequence, tool_call.tool_name] if tool_call else list(session.tool_sequence)
    next_last_tool = tool_call.tool_name if tool_call else session.last_tool_name
    next_consecutive = session.current_consecutive_tool_count
    next_max_consecutive = session.max_consecutive_tool_count
    next_loop_suspected = session.single_tool_loop_suspected_count
    if tool_call:
        dry_run_finish_repeat = tool_call.tool_name == "submit" and session.last_submit_dry_run_accepted
        next_consecutive = (
            session.current_consecutive_tool_count + 1
            if session.last_tool_name == tool_call.tool_name and not dry_run_finish_repeat
            else 1
        )
        next_max_consecutive = max(session.max_consecutive_tool_count, next_consecutive)
    updated = replace(
        session,
        turn_count=session.turn_count + 1,
        response_id=str(response.get("id") or session.response_id),
        usage_input_tokens=session.usage_input_tokens + input_tokens,
        usage_output_tokens=session.usage_output_tokens + output_tokens,
        cached_input_tokens=session.cached_input_tokens + cached_tokens,
        last_tail_bytes=tail_bytes,
        last_tail_sha256=tail_sha256,
        tool_sequence=next_tool_sequence,
        last_tool_name=next_last_tool,
        current_consecutive_tool_count=next_consecutive,
        max_consecutive_tool_count=next_max_consecutive,
        single_tool_loop_suspected_count=next_loop_suspected,
        last_submit_dry_run_accepted=False,
    )
    audit = {
        "note": "orchestrator_agent_called",
        "call_name": "call_human_case_agent",
        "case_agent_mode": "human_case_agent",
        "tool_name": tool_call.tool_name if tool_call else "",
        "turn_count": updated.turn_count,
        "provider_retry_count": int(getattr(ai_client, "_last_tool_agent_provider_retry_count", 0) or 0),
        "consecutive_same_tool_count": updated.current_consecutive_tool_count,
        "single_tool_loop_suspected_count": updated.single_tool_loop_suspected_count,
        "session_mode": "human_case_agent",
        "provider_session_enabled": False,
        "provider_response_id": updated.response_id,
        "provider_conversation_id": "",
        "http_session_id": updated.http_session_id,
        "prompt_cache_key": updated.prompt_cache_key,
        "prompt_cache_retention": "24h",
        "instructions_sha256": instructions_sha256,
        "tools_sha256": tools_sha256,
        "case_desk_sha256": case_desk_sha256,
        "tail_sha256": tail_sha256,
        "previous_tail_sha256": session.last_tail_sha256,
        "tail_lcp_with_previous_bytes": tail_lcp_bytes,
        "tail_lcp_with_previous_estimated_tokens": tail_lcp_bytes // 4,
        "tool_choice": tool_choice,
        "provider_input_tokens": input_tokens,
        "provider_cached_input_tokens": cached_tokens,
        "provider_cached_input_ratio": (cached_tokens / input_tokens) if input_tokens else 0.0,
        "stable_prefix_estimated_tokens": updated.stable_prefix_estimated_tokens,
        "turn_tail_estimated_tokens": updated.turn_tail_estimated_tokens,
        "first_turn_estimated_tokens": updated.first_turn_estimated_tokens,
        "usage": usage,
    }
    if parse_error:
        audit["error"] = parse_error
        return None, updated, audit, parse_error
    return tool_call, updated, audit, ""


def _apply_turn_health(
    session: HumanCaseSession,
    output: dict[str, object],
    *,
    before_workspace_counts: dict[str, int],
    after_workspace: CaseEvidenceWorkspace,
    before_cognitive: CaseCognitiveWorkspace,
    repeated_submit_rejection: bool = False,
) -> tuple[HumanCaseSession, dict[str, object]]:
    after_counts = _workspace_counts(after_workspace)
    before_focus = json.dumps(before_cognitive.attention_focus.model_dump(mode="json"), sort_keys=True, default=str)
    after_focus = json.dumps(session.cognitive_workspace.attention_focus.model_dump(mode="json"), sort_keys=True, default=str)
    before_readiness = _readiness_signature(before_cognitive)
    after_readiness = _readiness_signature(session.cognitive_workspace)
    active_focus_changed = before_focus != after_focus
    new_evidence_added = any(after_counts.get(key, 0) > before_workspace_counts.get(key, 0) for key in after_counts)
    agenda_item_closed = _closed_agenda_count(session.cognitive_workspace) > _closed_agenda_count(before_cognitive)
    resolution_readiness_changed = before_readiness != after_readiness and not repeated_submit_rejection
    made_progress = any(
        [
            active_focus_changed,
            new_evidence_added,
            agenda_item_closed,
            resolution_readiness_changed,
            bool(output.get("cognitive_workspace_changed")),
        ]
    )
    no_progress_turn_count = 0 if made_progress else session.no_progress_turn_count + 1
    if repeated_submit_rejection and not made_progress:
        no_progress_turn_count = max(2, no_progress_turn_count)
    stall_warning_count = session.stall_warning_count
    turn_health: dict[str, object] = {
        "active_focus_changed": active_focus_changed,
        "new_evidence_added": new_evidence_added,
        "agenda_item_closed": agenda_item_closed,
        "resolution_readiness_changed": resolution_readiness_changed,
        "no_progress_turn_count": no_progress_turn_count,
    }
    if no_progress_turn_count >= 2:
        stall_warning_count += 1
        turn_health["stall_warning"] = {
            "issue": "cognitive_workspace_stalled",
            "message": (
                "No focus, evidence, agenda, or readiness change was recorded for two consecutive turns. "
                "Re-focus the cognitive workspace before repeating the same path."
            ),
        }
    updated = replace(
        session,
        attention_focus_change_count=session.attention_focus_change_count + (1 if active_focus_changed else 0),
        agenda_closed_count=session.agenda_closed_count + (1 if agenda_item_closed else 0),
        stall_warning_count=stall_warning_count,
        no_progress_turn_count=no_progress_turn_count,
        last_turn_health=turn_health,
    )
    return updated, {**output, "turn_health": turn_health}


def _record_tool_output(session: HumanCaseSession, tool_call: HumanToolCall, output: dict[str, object]) -> HumanCaseSession:
    repeated_submit_rejection = bool(output.get("repeat_rejection_warning"))
    current_submit_repair_open = bool(
        tool_call.tool_name == "submit"
        and not output.get("accepted")
        and _has_open_submit_repair(output)
    )
    loop_warning = bool(
        session.current_consecutive_tool_count >= 4
        and (
            tool_call.tool_name != "submit"
            or repeated_submit_rejection
            or current_submit_repair_open
        )
    )
    if loop_warning:
        output = {
            **output,
            "loop_health_warning": {
                "issue": "same_tool_repeated",
                "tool": tool_call.tool_name,
                "consecutive_count": session.current_consecutive_tool_count,
                "message": (
                    "You have repeated the same tool several turns in a row. "
                    "Before calling it again, make a concrete change based on the latest mechanical feedback, "
                    "or use inspect/search/note if you need more facts."
                ),
            },
        }
    compact = {
        "tool": tool_call.tool_name,
        "output": output,
    }
    session.observations.append(compact)
    if tool_call.call_id:
        session.history_items.append(
            {
                "type": "function_call_output",
                "call_id": tool_call.call_id,
                "output": json.dumps(_jsonable(output), ensure_ascii=False),
            }
        )
    if loop_warning:
        return replace(session, single_tool_loop_suspected_count=session.single_tool_loop_suspected_count + 1)
    return session


def _truncate_repair_text(value: object, *, limit: int = 320) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _issue_codes_from_value(value: object) -> list[str]:
    codes: list[str] = []

    def add(raw: object) -> None:
        code = str(raw or "").strip()
        if not code or code in codes:
            return
        codes.append(code)

    if isinstance(value, dict):
        add(value.get("issue") or value.get("issue_code") or "mechanical_issue")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                add(item.get("issue") or item.get("issue_code") or "mechanical_issue")
            else:
                add(item)
    elif value:
        add(value)
    return codes


def _compact_repair_payload(value: object, *, list_limit: int = 6, text_limit: int = 320, depth: int = 0) -> object:
    if isinstance(value, str):
        return _truncate_repair_text(value, limit=text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [
            _compact_repair_payload(item, list_limit=list_limit, text_limit=text_limit, depth=depth + 1)
            for item in value[:list_limit]
        ]
    if isinstance(value, dict):
        if depth >= 3:
            return _truncate_repair_text(json.dumps(value, ensure_ascii=False, default=str), limit=text_limit)
        return {
            str(key): _compact_repair_payload(val, list_limit=list_limit, text_limit=text_limit, depth=depth + 1)
            for key, val in value.items()
        }
    return _truncate_repair_text(value, limit=text_limit)


def _compact_repair_field(key: str, value: object) -> object:
    if key == "search_queries_to_try":
        return [str(item) for item in list(value or []) if str(item).strip()][:8] if isinstance(value, list) else []
    if key == "actionable_options":
        return [_truncate_repair_text(item, limit=220) for item in list(value or [])[:4]] if isinstance(value, list) else []
    if key in {
        "visible_alternate_subjects",
        "same_count_target_span_candidates",
        "support_targets",
        "unassigned_target_candidates",
        "visible_source_query_bridge_targets",
        "single_file_target_item_options",
    }:
        return _compact_repair_payload(value, list_limit=4, text_limit=220)
    if key == "local_episode_split_options":
        return _compact_repair_payload(value, list_limit=8, text_limit=220)
    if key in {"local_target_count_pairing_options", "local_target_title_pairing_options", "local_slice_mapping_options"}:
        return _compact_repair_payload(value, list_limit=6, text_limit=220)
    if key == "local_locator_details":
        return _compact_repair_payload(value, list_limit=4, text_limit=220)
    if key in {"target_locator_details", "split_first_repair", "continuation_evidence_hint"}:
        return _compact_repair_payload(value, list_limit=8, text_limit=260)
    return _compact_repair_payload(value)


def _compact_submit_feedback_for_audit(feedback: dict[str, object]) -> dict[str, object]:
    package = feedback.get("package") if isinstance(feedback.get("package"), dict) else {}
    units = feedback.get("units") if isinstance(feedback.get("units"), list) else []
    compact_units: list[dict[str, object]] = []
    for unit in units[:8]:
        if not isinstance(unit, dict):
            continue
        compact_units.append(
            {
                key: unit.get(key)
                for key in (
                    "unit",
                    "local",
                    "target",
                    "issue",
                    "issues",
                    "local_locator_details",
                    "target_locator_details",
                    "same_count_target_span_candidates",
                    "local_episode_split_options",
                    "local_target_count_pairing_options",
                    "local_target_title_pairing_options",
                    "single_file_target_item_options",
                    "local_slice_mapping_options",
                    "season_mismatch_repair",
                    "split_first_repair",
                    "visible_alternate_subjects",
                    "candidate_local_locators",
                    "unassigned_target_candidates",
                    "negative_target_absence_support_candidates",
                    "negative_target_absence_submit_shape",
                    "target_count",
                    "expected_target_count",
                    "available_action",
                    "search_queries_to_try",
                    "unbridged_title_tail_tokens",
                    "searched_query_hints",
                )
                if key in unit
            }
        )
        compact_units[-1] = {
            key: _compact_repair_field(key, value)
            for key, value in compact_units[-1].items()
        }
    return {
            "package": {
                "issue_counts": package.get("issue_counts"),
                "missing_local_ref_count": package.get("missing_local_ref_count"),
                "duplicate_local_ref_count": package.get("duplicate_local_ref_count"),
                "duplicate_target_count": package.get("duplicate_target_count"),
                "missing_local_locator_hints": package.get("missing_local_locator_hints"),
                "required_missing_work_units": package.get("required_missing_work_units"),
                "coverage_missing_instruction": package.get("coverage_missing_instruction"),
                "duplicate_local_locator_hints": package.get("duplicate_local_locator_hints"),
                "duplicate_target_details": package.get("duplicate_target_details"),
                "duplicate_target_repair_units": package.get("duplicate_target_repair_units"),
                "fail_closed_mapped_sibling_repairs": package.get("fail_closed_mapped_sibling_repairs"),
                "excluded_slice_mapped_sibling_repairs": package.get("excluded_slice_mapped_sibling_repairs"),
                "fail_closed_count_matched_target_sibling_repairs": package.get("fail_closed_count_matched_target_sibling_repairs"),
                "excluded_count_matched_uninspected_subject_repairs": package.get("excluded_count_matched_uninspected_subject_repairs"),
                "excluded_singleton_visible_subject_repairs": package.get("excluded_singleton_visible_subject_repairs"),
                "singleton_target_alias_repairs": package.get("singleton_target_alias_repairs"),
                "mapped_target_title_bridge_repairs": package.get("mapped_target_title_bridge_repairs"),
                "mapped_title_season_mismatch_repairs": package.get("mapped_title_season_mismatch_repairs"),
                "excluded_main_mapped_sibling_repairs": package.get("excluded_main_mapped_sibling_repairs"),
                "supplemental_main_episode_repairs": package.get("supplemental_main_episode_repairs"),
                "numbered_special_exclusion_repairs": package.get("numbered_special_exclusion_repairs"),
                "fail_closed_negative_target_absence_repairs": package.get("fail_closed_negative_target_absence_repairs"),
                "excluded_title_tail_search_repairs": package.get("excluded_title_tail_search_repairs"),
                "excluded_visible_title_pairing_repairs": package.get("excluded_visible_title_pairing_repairs"),
                "excluded_title_tail_unresolved_repairs": package.get("excluded_title_tail_unresolved_repairs"),
                "fail_closed_slice_pairing_repairs": package.get("fail_closed_slice_pairing_repairs"),
                "fail_closed_title_tail_bridge_repairs": package.get("fail_closed_title_tail_bridge_repairs"),
                "excluded_singleton_unassigned_target_repairs": package.get("excluded_singleton_unassigned_target_repairs"),
                "fail_closed_singleton_unassigned_target_repairs": package.get("fail_closed_singleton_unassigned_target_repairs"),
                "unit_mechanical_checklist": package.get("unit_mechanical_checklist"),
                "mechanical_repair_hints": package.get("mechanical_repair_hints"),
        },
        "units": compact_units,
        "semantic_diagnostics": feedback.get("semantic_diagnostics"),
        "repeat_rejection_warning": feedback.get("repeat_rejection_warning"),
    }


def _repair_agenda_from_submit_feedback(feedback: dict[str, object], *, repeated: bool) -> dict[str, object]:
    package = feedback.get("package") if isinstance(feedback.get("package"), dict) else {}
    units = feedback.get("units") if isinstance(feedback.get("units"), list) else []
    issue_counts = package.get("issue_counts") if isinstance(package.get("issue_counts"), dict) else {}
    mechanical_issue_codes = {str(code) for code in issue_counts}
    blocking_units: list[dict[str, object]] = []
    diagnostic_units: list[dict[str, object]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        issue_value = unit.get("issue") or unit.get("issues")
        issue_codes = _issue_codes_from_value(issue_value)
        if not issue_codes:
            continue
        blocking_issue_codes = [
            code
            for code in issue_codes
            if code in mechanical_issue_codes or code not in SEMANTIC_SUBMIT_DIAGNOSTIC_CODES
        ]
        if not blocking_issue_codes:
            diagnostic_units.append(
                {
                    "unit": unit.get("unit"),
                    "local": unit.get("local"),
                    "target": unit.get("target"),
                    "issue": issue_codes[0] if len(issue_codes) == 1 else issue_codes,
                    "issue_codes": issue_codes,
                }
            )
            continue
        row = {
            "unit": unit.get("unit"),
            "local": unit.get("local"),
            "target": unit.get("target"),
            "issue": blocking_issue_codes[0] if len(blocking_issue_codes) == 1 else blocking_issue_codes,
            "issue_codes": blocking_issue_codes,
        }
        for key in (
            "local_locator_details",
            "target_locator_details",
            "local_episode_split_options",
            "local_target_count_pairing_options",
            "local_target_title_pairing_options",
            "single_file_target_item_options",
            "local_slice_mapping_options",
            "same_count_target_span_candidates",
            "split_first_repair",
            "visible_alternate_subjects",
            "actionable_options",
            "season_mismatch_repair",
            "negative_target_absence_support_candidates",
            "negative_target_absence_submit_shape",
            "search_queries_to_try",
            "unsearched_title_tokens",
            "visible_target_title_tokens",
            "support_targets",
            "unassigned_target_candidates",
            "unbridged_title_tail_tokens",
            "searched_query_hints",
            "visible_source_query_bridge_targets",
            "continuation_evidence_hint",
        ):
            if unit.get(key):
                row[key] = _compact_repair_field(key, unit.get(key))
        if unit.get("same_count_target_span_candidates"):
            row["same_count_target_span_candidates"] = _compact_repair_field(
                "same_count_target_span_candidates",
                unit.get("same_count_target_span_candidates"),
            )
        if unit.get("local_episode_split_options"):
            row["local_episode_split_options"] = _compact_repair_field(
                "local_episode_split_options",
                unit.get("local_episode_split_options"),
            )
        if unit.get("local_target_count_pairing_options"):
            row["local_target_count_pairing_options"] = _compact_repair_field(
                "local_target_count_pairing_options",
                unit.get("local_target_count_pairing_options"),
            )
        if unit.get("local_target_title_pairing_options"):
            row["local_target_title_pairing_options"] = _compact_repair_field(
                "local_target_title_pairing_options",
                unit.get("local_target_title_pairing_options"),
            )
        if unit.get("visible_alternate_subjects"):
            row["visible_alternate_subjects"] = _compact_repair_field(
                "visible_alternate_subjects",
                unit.get("visible_alternate_subjects"),
            )
        candidate_local_locators: list[dict[str, object]] = []
        candidate_target_locators: list[dict[str, object]] = []
        raw_candidates = unit.get("candidate_local_locators")
        if isinstance(raw_candidates, list):
            candidate_local_locators.extend([item for item in raw_candidates if isinstance(item, dict)])
        raw_target_candidates = unit.get("candidate_target_locators")
        if isinstance(raw_target_candidates, list):
            candidate_target_locators.extend([item for item in raw_target_candidates if isinstance(item, dict)])
        raw_issues = unit.get("issues")
        if isinstance(raw_issues, list):
            for issue_item in raw_issues:
                if not isinstance(issue_item, dict):
                    continue
                issue_candidates = issue_item.get("candidate_local_locators")
                if isinstance(issue_candidates, list):
                    candidate_local_locators.extend([item for item in issue_candidates if isinstance(item, dict)])
                issue_target_candidates = issue_item.get("candidate_target_locators")
                if isinstance(issue_target_candidates, list):
                    candidate_target_locators.extend([item for item in issue_target_candidates if isinstance(item, dict)])
        if candidate_local_locators:
            seen_candidate_locators: set[str] = set()
            deduped_candidates: list[dict[str, object]] = []
            for item in candidate_local_locators:
                candidate_locator = str(item.get("locator") or "")
                if not candidate_locator or candidate_locator in seen_candidate_locators:
                    continue
                seen_candidate_locators.add(candidate_locator)
                deduped_candidates.append(item)
                if len(deduped_candidates) >= 6:
                    break
            row["candidate_local_locators"] = deduped_candidates
        if candidate_target_locators:
            seen_candidate_targets: set[str] = set()
            deduped_targets: list[dict[str, object]] = []
            for item in candidate_target_locators:
                candidate_target = str(item.get("target") or "")
                if not candidate_target or candidate_target in seen_candidate_targets:
                    continue
                seen_candidate_targets.add(candidate_target)
                deduped_targets.append(_compact_repair_payload(item, list_limit=4, text_limit=220))
                if len(deduped_targets) >= 6:
                    break
            row["candidate_target_locators"] = deduped_targets
        negative_support_candidates: list[dict[str, object]] = []
        negative_submit_shape: dict[str, object] = {}
        if isinstance(raw_issues, list):
            for issue_item in raw_issues:
                if not isinstance(issue_item, dict):
                    continue
                issue_support = issue_item.get("negative_target_absence_support_candidates")
                if isinstance(issue_support, list):
                    negative_support_candidates.extend([item for item in issue_support if isinstance(item, dict)])
                issue_shape = issue_item.get("negative_target_absence_submit_shape")
                if isinstance(issue_shape, dict) and not negative_submit_shape:
                    negative_submit_shape = issue_shape
        if negative_support_candidates:
            seen_targets: set[str] = set()
            deduped_support: list[dict[str, object]] = []
            for item in negative_support_candidates:
                target = str(item.get("target") or "")
                if not target or target in seen_targets:
                    continue
                seen_targets.add(target)
                deduped_support.append(item)
                if len(deduped_support) >= 6:
                    break
            row["negative_target_absence_support_candidates"] = deduped_support
        if negative_submit_shape:
            row["negative_target_absence_submit_shape"] = negative_submit_shape
        issue_title_pairing_options: list[dict[str, object]] = []
        if isinstance(raw_issues, list):
            for issue_item in raw_issues:
                if not isinstance(issue_item, dict):
                    continue
                raw_pairing_options = issue_item.get("local_target_title_pairing_options")
                if isinstance(raw_pairing_options, list):
                    issue_title_pairing_options.extend(
                        [item for item in raw_pairing_options if isinstance(item, dict)]
                    )
        if issue_title_pairing_options and not row.get("local_target_title_pairing_options"):
            seen_pairings: set[tuple[str, str]] = set()
            deduped_pairings: list[dict[str, object]] = []
            for item in issue_title_pairing_options:
                key = (str(item.get("local_slice") or ""), str(item.get("target") or ""))
                if not key[0] or not key[1] or key in seen_pairings:
                    continue
                seen_pairings.add(key)
                deduped_pairings.append(item)
                if len(deduped_pairings) >= 8:
                    break
            if deduped_pairings:
                row["local_target_title_pairing_options"] = deduped_pairings
        title_tail_queries: list[str] = []
        title_tail_tokens: list[str] = []
        title_tail_support_targets: list[dict[str, object]] = []
        if isinstance(raw_issues, list):
            for issue_item in raw_issues:
                if not isinstance(issue_item, dict):
                    continue
                raw_queries = issue_item.get("search_queries_to_try")
                if isinstance(raw_queries, list):
                    title_tail_queries.extend(str(item) for item in raw_queries if str(item).strip())
                raw_tokens = issue_item.get("unsearched_title_tokens")
                if isinstance(raw_tokens, list):
                    title_tail_tokens.extend(str(item) for item in raw_tokens if str(item).strip())
                raw_support_targets = issue_item.get("support_targets")
                if isinstance(raw_support_targets, list):
                    title_tail_support_targets.extend([item for item in raw_support_targets if isinstance(item, dict)])
        if title_tail_queries:
            row["search_queries_to_try"] = list(dict.fromkeys(title_tail_queries))[:8]
        if title_tail_tokens:
            row["unsearched_title_tokens"] = list(dict.fromkeys(title_tail_tokens))[:12]
        if title_tail_support_targets:
            row["support_targets"] = title_tail_support_targets[:6]
        unassigned_target_candidates: list[dict[str, object]] = []
        if isinstance(raw_issues, list):
            for issue_item in raw_issues:
                if not isinstance(issue_item, dict):
                    continue
                raw_unassigned = issue_item.get("unassigned_target_candidates")
                if isinstance(raw_unassigned, list):
                    unassigned_target_candidates.extend(
                        [item for item in raw_unassigned if isinstance(item, dict)]
                    )
        if unassigned_target_candidates and not row.get("unassigned_target_candidates"):
            seen_targets: set[str] = set()
            deduped_unassigned: list[dict[str, object]] = []
            for item in unassigned_target_candidates:
                target = str(item.get("target") or "")
                if not target or target in seen_targets:
                    continue
                seen_targets.add(target)
                deduped_unassigned.append(item)
                if len(deduped_unassigned) >= 8:
                    break
            if deduped_unassigned:
                row["unassigned_target_candidates"] = deduped_unassigned
        if unit.get("actionable_options"):
            row["actionable_options"] = unit.get("actionable_options")
        top_level_issue = str(unit.get("issue") or "").strip()
        if top_level_issue in {"target_episode_surface_missing", "episode_range_required"}:
            top_level_repair = {
                key: unit.get(key)
                for key in (
                    "issue",
                    "target",
                    "episode_start",
                    "episode_end",
                    "target_count",
                    "expected_target_count",
                    "available_target_episode_numbers",
                    "target_episode_locator_samples",
                    "target_span_examples",
                    "visible_alternate_subjects",
                    "local_target_title_pairing_options",
                    "local_slice_mapping_options",
                    "available_action",
                    "repair_instruction",
                    "search_queries_to_try",
                    "continuation_evidence_hint",
                )
                if key in unit
            }
            if top_level_repair:
                row["target_surface_repairs"] = [top_level_repair]
        issues = unit.get("issues")
        if isinstance(issues, list):
            target_surface_repairs = [
                {
                    key: issue_item.get(key)
                for key in (
                    "issue",
                    "locator",
                    "actual_kind",
                    "contract_role",
                    "expected",
                    "repair_instruction",
                    "candidate_local_locators",
                    "target",
                    "available_target_episode_numbers",
                    "target_surface_visible",
                        "target_episode_locator_samples",
                        "target_span_examples",
                        "visible_alternate_subjects",
                        "local_target_title_pairing_options",
                        "local_slice_mapping_options",
                        "available_action",
                        "repair_instruction",
                        "search_queries_to_try",
                        "continuation_evidence_hint",
                    )
                    if key in issue_item
                }
                for issue_item in issues
                if isinstance(issue_item, dict)
                and issue_item.get("issue") in {"target_episode_surface_missing", "episode_range_required"}
            ]
            if target_surface_repairs:
                row["target_surface_repairs"] = target_surface_repairs
        blocking_units.append(row)
        if len(blocking_units) >= 8:
            break
    duplicate_target_details = package.get("duplicate_target_details")
    if isinstance(duplicate_target_details, list):
        duplicate_target_details = duplicate_target_details[:4]
    duplicate_target_repair_units = package.get("duplicate_target_repair_units")
    if isinstance(duplicate_target_repair_units, list):
        duplicate_target_repair_units = duplicate_target_repair_units[:6]
    fail_closed_mapped_sibling_repairs = package.get("fail_closed_mapped_sibling_repairs")
    if isinstance(fail_closed_mapped_sibling_repairs, list):
        fail_closed_mapped_sibling_repairs = fail_closed_mapped_sibling_repairs[:6]
    fail_closed_count_matched_target_sibling_repairs = package.get("fail_closed_count_matched_target_sibling_repairs")
    if isinstance(fail_closed_count_matched_target_sibling_repairs, list):
        fail_closed_count_matched_target_sibling_repairs = fail_closed_count_matched_target_sibling_repairs[:6]
    excluded_slice_mapped_sibling_repairs = package.get("excluded_slice_mapped_sibling_repairs")
    if isinstance(excluded_slice_mapped_sibling_repairs, list):
        excluded_slice_mapped_sibling_repairs = excluded_slice_mapped_sibling_repairs[:6]
    excluded_count_matched_uninspected_subject_repairs = package.get("excluded_count_matched_uninspected_subject_repairs")
    if isinstance(excluded_count_matched_uninspected_subject_repairs, list):
        excluded_count_matched_uninspected_subject_repairs = excluded_count_matched_uninspected_subject_repairs[:6]
    excluded_singleton_visible_subject_repairs = package.get("excluded_singleton_visible_subject_repairs")
    if isinstance(excluded_singleton_visible_subject_repairs, list):
        excluded_singleton_visible_subject_repairs = excluded_singleton_visible_subject_repairs[:6]
    singleton_target_alias_repairs = package.get("singleton_target_alias_repairs")
    if isinstance(singleton_target_alias_repairs, list):
        singleton_target_alias_repairs = singleton_target_alias_repairs[:6]
    mapped_target_title_bridge_repairs = package.get("mapped_target_title_bridge_repairs")
    if isinstance(mapped_target_title_bridge_repairs, list):
        mapped_target_title_bridge_repairs = mapped_target_title_bridge_repairs[:6]
    mapped_title_season_mismatch_repairs = package.get("mapped_title_season_mismatch_repairs")
    if isinstance(mapped_title_season_mismatch_repairs, list):
        mapped_title_season_mismatch_repairs = mapped_title_season_mismatch_repairs[:6]
    excluded_main_mapped_sibling_repairs = package.get("excluded_main_mapped_sibling_repairs")
    if isinstance(excluded_main_mapped_sibling_repairs, list):
        excluded_main_mapped_sibling_repairs = excluded_main_mapped_sibling_repairs[:6]
    numbered_special_exclusion_repairs = package.get("numbered_special_exclusion_repairs")
    if isinstance(numbered_special_exclusion_repairs, list):
        numbered_special_exclusion_repairs = numbered_special_exclusion_repairs[:6]
    fail_closed_negative_target_absence_repairs = package.get("fail_closed_negative_target_absence_repairs")
    if isinstance(fail_closed_negative_target_absence_repairs, list):
        fail_closed_negative_target_absence_repairs = fail_closed_negative_target_absence_repairs[:6]
    excluded_title_tail_search_repairs = package.get("excluded_title_tail_search_repairs")
    if isinstance(excluded_title_tail_search_repairs, list):
        excluded_title_tail_search_repairs = excluded_title_tail_search_repairs[:6]
    excluded_visible_title_pairing_repairs = package.get("excluded_visible_title_pairing_repairs")
    if isinstance(excluded_visible_title_pairing_repairs, list):
        excluded_visible_title_pairing_repairs = excluded_visible_title_pairing_repairs[:6]
    excluded_title_tail_unresolved_repairs = package.get("excluded_title_tail_unresolved_repairs")
    if isinstance(excluded_title_tail_unresolved_repairs, list):
        excluded_title_tail_unresolved_repairs = excluded_title_tail_unresolved_repairs[:6]
    fail_closed_slice_pairing_repairs = package.get("fail_closed_slice_pairing_repairs")
    if isinstance(fail_closed_slice_pairing_repairs, list):
        fail_closed_slice_pairing_repairs = fail_closed_slice_pairing_repairs[:6]
    fail_closed_title_tail_bridge_repairs = package.get("fail_closed_title_tail_bridge_repairs")
    if isinstance(fail_closed_title_tail_bridge_repairs, list):
        fail_closed_title_tail_bridge_repairs = fail_closed_title_tail_bridge_repairs[:6]
    excluded_singleton_unassigned_target_repairs = package.get("excluded_singleton_unassigned_target_repairs")
    if isinstance(excluded_singleton_unassigned_target_repairs, list):
        excluded_singleton_unassigned_target_repairs = excluded_singleton_unassigned_target_repairs[:6]
    fail_closed_singleton_unassigned_target_repairs = package.get("fail_closed_singleton_unassigned_target_repairs")
    if isinstance(fail_closed_singleton_unassigned_target_repairs, list):
        fail_closed_singleton_unassigned_target_repairs = fail_closed_singleton_unassigned_target_repairs[:6]
    unit_mechanical_checklist = package.get("unit_mechanical_checklist")
    if isinstance(unit_mechanical_checklist, list):
        unit_mechanical_checklist = unit_mechanical_checklist[:16]
    target_surface_actions: list[str] = []
    visible_target_surface_missing_units: list[dict[str, object]] = []
    search_queries_to_try: list[str] = []

    def add_search_queries(value: object) -> None:
        if not isinstance(value, list):
            return
        for item in value:
            query = str(item or "").strip()
            if query and query not in search_queries_to_try:
                search_queries_to_try.append(query)
            if len(search_queries_to_try) >= 8:
                return

    if isinstance(duplicate_target_repair_units, list):
        for repair in duplicate_target_repair_units:
            if not isinstance(repair, dict):
                continue
            for unit in list(repair.get("conflicting_units") or []):
                if not isinstance(unit, dict):
                    continue
                for fact in list(unit.get("local_facts") or []):
                    if isinstance(fact, dict):
                        add_search_queries(fact.get("search_queries_to_try"))
                    if len(search_queries_to_try) >= 8:
                        break
                if len(search_queries_to_try) >= 8:
                    break
            if len(search_queries_to_try) >= 8:
                break

    if isinstance(mapped_title_season_mismatch_repairs, list):
        for repair in mapped_title_season_mismatch_repairs:
            if isinstance(repair, dict):
                add_search_queries(repair.get("search_queries_to_try"))
            if len(search_queries_to_try) >= 8:
                break

    for unit in blocking_units:
        add_search_queries(unit.get("search_queries_to_try"))
        for repair in list(unit.get("target_surface_repairs") or []):
            if not isinstance(repair, dict):
                continue
            add_search_queries(repair.get("search_queries_to_try"))
            if repair.get("target_surface_visible"):
                visible_target_surface_missing_units.append(
                    {
                        "unit": unit.get("unit"),
                        "local": unit.get("local"),
                        "target": repair.get("target") or unit.get("target"),
                        "available_target_episode_numbers": repair.get("available_target_episode_numbers") or [],
                        "search_queries_to_try": repair.get("search_queries_to_try") or [],
                        "continuation_evidence_hint": repair.get("continuation_evidence_hint") or {},
                        "local_slice_mapping_options": _compact_repair_field(
                            "local_slice_mapping_options",
                            repair.get("local_slice_mapping_options") or [],
                        ),
                        "local_target_title_pairing_options": _compact_repair_field(
                            "local_target_title_pairing_options",
                            repair.get("local_target_title_pairing_options") or [],
                        ),
                        "required": (
                            "The target episode surface is already visible and does not contain the requested episode "
                            "number/range. Do not retry this same target/range; change target, split local, or choose "
                            "target_absent/supplemental/non_bangumi/fail_closed according to your semantic judgment."
                        ),
                    }
                )
            action = str(repair.get("available_action") or "").strip()
            if action.startswith("inspect(") and action not in target_surface_actions:
                target_surface_actions.append(action)
            if len(target_surface_actions) >= 4:
                break
        if len(target_surface_actions) >= 4:
            break
    for action in _target_surface_actions_from_repair(package):
        if action not in target_surface_actions:
            target_surface_actions.append(action)
        if len(target_surface_actions) >= 4:
            break
    return {
        "accepted": False,
        "status": "repair_required",
        "issue_counts": issue_counts,
        "required_missing_work_units": package.get("required_missing_work_units") or [],
        "duplicate_target_details": duplicate_target_details or [],
        "duplicate_target_repair_units": duplicate_target_repair_units or [],
        "fail_closed_mapped_sibling_repairs": fail_closed_mapped_sibling_repairs or [],
        "excluded_slice_mapped_sibling_repairs": excluded_slice_mapped_sibling_repairs or [],
        "fail_closed_count_matched_target_sibling_repairs": fail_closed_count_matched_target_sibling_repairs or [],
        "excluded_count_matched_uninspected_subject_repairs": excluded_count_matched_uninspected_subject_repairs or [],
        "excluded_singleton_visible_subject_repairs": excluded_singleton_visible_subject_repairs or [],
        "singleton_target_alias_repairs": singleton_target_alias_repairs or [],
        "mapped_target_title_bridge_repairs": mapped_target_title_bridge_repairs or [],
        "mapped_title_season_mismatch_repairs": mapped_title_season_mismatch_repairs or [],
        "excluded_main_mapped_sibling_repairs": excluded_main_mapped_sibling_repairs or [],
        "numbered_special_exclusion_repairs": numbered_special_exclusion_repairs or [],
        "fail_closed_negative_target_absence_repairs": fail_closed_negative_target_absence_repairs or [],
        "excluded_title_tail_search_repairs": excluded_title_tail_search_repairs or [],
        "excluded_visible_title_pairing_repairs": excluded_visible_title_pairing_repairs or [],
        "excluded_title_tail_unresolved_repairs": excluded_title_tail_unresolved_repairs or [],
        "fail_closed_slice_pairing_repairs": fail_closed_slice_pairing_repairs or [],
        "fail_closed_title_tail_bridge_repairs": fail_closed_title_tail_bridge_repairs or [],
        "excluded_singleton_unassigned_target_repairs": excluded_singleton_unassigned_target_repairs or [],
        "fail_closed_singleton_unassigned_target_repairs": fail_closed_singleton_unassigned_target_repairs or [],
        "unit_mechanical_checklist": unit_mechanical_checklist or [],
        "blocking_units": blocking_units,
        "diagnostic_units": diagnostic_units[:8],
        "target_surface_actions": target_surface_actions,
        "search_queries_to_try": search_queries_to_try,
        "visible_target_surface_missing_units": visible_target_surface_missing_units[:8],
        "mechanical_repair_hints": package.get("mechanical_repair_hints") or [],
        "coverage_missing_instruction": package.get("coverage_missing_instruction") or "",
        "repeat_rejection_warning": feedback.get("repeat_rejection_warning") if repeated else None,
        "required_next_action": (
            "Submit changed, blocked, or missing work units. Mechanically-ok saved work units are carried "
            "forward by the fixed layer and the merged package is re-verified. Include every "
            "required_missing_work_units local locator exactly once unless it is already saved. "
            "If target_surface_actions is non-empty, inspect those target locators before another mapped episode submit. "
            "If candidate_local_locators appears under a blocking unit, replace the invalid raw filename/path with one "
            "of those visible local:// locators if it matches your intended local work unit. "
            "If candidate_target_locators appears under a blocking unit, replace the invalid raw target with one "
            "of those visible target:// locators only if it matches your intended Bangumi target. "
            "If visible_target_surface_missing_units is non-empty, the target surface was already visible and the requested "
            "episode/range is absent; do not retry the same mapped target/range. Change the target, split the local locator, "
            "or use supplemental/non_bangumi/target_absent/fail_closed according to your semantic judgment. If those units "
            "include search_queries_to_try, run a title-preserving continuation/part/cour search before fail_closed; this "
            "is only an evidence request, not a fixed-layer target choice. "
            "If split_first_repair appears on a count/composite shape blocker, split the local parent with the listed "
            "local:// episode locators before another mapped submit; use search_queries_to_try to find target evidence "
            "for the individual slices, but make the semantic target/exclusion decision yourself. "
            "If local_slice_mapping_options appears, those local://.../episode/N locators are visible legal split locators; "
            "you may submit them as separate work units if you judge the listed target pairing correct. "
            "If single_file_target_item_options appears, the local side has one file but the target subject has multiple "
            "visible items; choose one listed target://.../episode/N item only if it is the semantic owner, choose "
            "mapped_composite_feature only if the one file covers multiple target items, or fail_closed with a concrete "
            "reason after addressing those item candidates. "
            "If duplicate_target_details is non-empty, only one conflicting unit may keep that target item; every other "
            "conflicting unit must change its target locator, change to supplemental/non_bangumi/target_absent, or "
            "fail_closed with a concrete unresolved reason. Do not resubmit multiple title/work-unit groups to the same "
            "target subject after a duplicate-target rejection. Compare duplicate_target_repairs.local_facts and "
            "target_facts before excluding a main-episodes unit: a multi-file main-episodes locator with a distinct "
            "season/title suffix is usually a separate work unit, so correct its target/search/inspect instead of "
            "marking it supplemental solely to clear duplicate_target. If the conflict is caused by duplicate local copies, "
            "alternate encodes, previews, recap variants, or packaging versions of the same Bangumi item, keep one "
            "semantic owner mapped and mark the other local unit supplemental/non_bangumi/target_absent with a concrete "
            "duplicate-copy or extra-material reason. "
            "If your semantic judgment is that a blocking unit is bonus/SP/preview/extra material that should not map "
            "to a Bangumi episode item, submit it as supplemental/non_bangumi or target_absent with a concrete reason. "
            "If your semantic judgment is that a blocking unit cannot be safely mapped or excluded from the visible "
            "evidence, submit that exact local locator as outcome=fail_closed with a concrete reason; this is a valid "
            "terminal package result when coverage is exact. If fail_closed_mapped_sibling_repairs is non-empty, first "
            "resolve those parent/sibling contradictions; do not leave a leftover episode slice fail_closed while "
            "adjacent slices from the same local parent are mapped. "
            "If excluded_slice_mapped_sibling_repairs is non-empty, do not clear a leftover local slice beside a "
            "mapped sibling without a hard duplicate/copy/packaging reason; map a visible matching target, "
            "give a hard non-owner reason, or fail_closed the exact slice. "
            "If fail_closed_count_matched_target_sibling_repairs "
            "is non-empty, compare the fail_closed numbered local group, the mapped smaller sibling, and the target "
            "subject count/title before finishing; choose mapping or exclusion by semantic judgment after resolving "
            "that ownership contradiction. If excluded_count_matched_uninspected_subject_repairs is non-empty, inspect "
            "the listed visible subject first; the fixed layer has found same-count target evidence that has not yet "
            "been surfaced as episodes. If excluded_singleton_visible_subject_repairs is non-empty, decide whether the "
            "single local file is a mapped_composite_feature over the visible target span or a true extra after using "
            "the listed target evidence. "
            "If singleton_target_alias_repairs is non-empty, a mapped singleton target's title/source-query "
            "matches another excluded or unresolved singleton more strongly; keep the stronger owner on that target "
            "and resolve the weaker local separately. "
            "If mapped_target_title_bridge_repairs is non-empty, the selected target lacks visible title/alias/source-query "
            "provenance for the local title; run one batch search for search_queries_to_try when present, choose a "
            "better visible target, or submit fail_closed for that exact local locator. "
            "If mapped_title_season_mismatch_repairs is non-empty, the selected target has an explicit season suffix "
            "that is absent from or conflicts with the local title; choose a season-compatible visible target, inspect "
            "a listed alternate, or fail_closed with the unresolved season-ownership blocker. "
            "If excluded_main_mapped_sibling_repairs is non-empty, re-check target ownership; do not exclude a "
            "multi-file main locator while a smaller same-title-family local slice is mapped unless you give a concrete "
            "extra/duplicate/non-anime reason. If numbered_special_exclusion_repairs is non-empty, search/inspect "
            "the special/OVA/OAD/SP target surface; if no corresponding target remains visible after that finite "
            "target-side check and the local SP group is semantically bonus/extra or target_absent, submit "
            "target_absent/supplemental/non_bangumi with inspected support and a concrete negative-target reason. "
            "Use fail_closed only for true unresolved ambiguity. "
            "If fail_closed_negative_target_absence_repairs is non-empty, your fail_closed reason already describes "
            "target absence rather than unresolved ambiguity; either submit the listed target_absent/supplemental/"
            "non_bangumi shape with one support candidate, or rewrite fail_closed to name the remaining ambiguity. "
            "If excluded_title_tail_search_repairs is non-empty, run a batch search for search_queries_to_try "
            "or submit fail_closed for that exact local locator; do not claim target_absent from only a broad "
            "same-franchise support target. If excluded_visible_title_pairing_repairs is non-empty, a visible "
            "title-tail target candidate exists for an excluded main/movie locator; map one candidate if you judge "
            "it correct, choose another target, give a hard duplicate/packaging reason, or fail_closed. If "
            "excluded_title_tail_unresolved_repairs is non-empty, the searched title-tail still has no visible "
            "bridge; if visible_source_query_bridge_targets is listed, inspect or map one only if your semantic "
            "judgment supports it. Otherwise search another alias or fail_closed; do not exclude it from a broad "
            "same-franchise subject alone. "
            "If fail_closed_singleton_unassigned_target_repairs is non-empty, visible mapped subjects still expose "
            "unassigned target items for a singleton local; map one if you judge it correct, or state why the listed "
            "candidates are not owners before fail_closed. "
            "The fixed layer is only checking mechanics."
        ),
    }


def _submit_rejection_fingerprint(feedback: dict[str, object]) -> str:
    compact = _compact_submit_feedback_for_audit(feedback)
    package = compact.get("package") if isinstance(compact.get("package"), dict) else {}
    units = compact.get("units") if isinstance(compact.get("units"), list) else []
    payload = {
        "issue_counts": package.get("issue_counts"),
        "missing_local_locator_hints": [
            str(item.get("locator") or "")
            for item in package.get("missing_local_locator_hints") or []
            if isinstance(item, dict)
        ],
        "duplicate_local_locator_hints": [
            str(item.get("locator") or "")
            for item in package.get("duplicate_local_locator_hints") or []
            if isinstance(item, dict)
        ],
        "duplicate_target_items": [
            str(item.get("target_item") or "")
            for item in package.get("duplicate_target_details") or []
            if isinstance(item, dict)
        ],
        "unit_issues": [
            {
                "unit": item.get("unit"),
                "issue": item.get("issue"),
                "target": item.get("target"),
                "issues": item.get("issues"),
            }
            for item in units
            if isinstance(item, dict)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _session_summary(session: HumanCaseSession, registry: LocatorRegistry) -> dict[str, object]:
    counts: dict[str, int] = {}
    for tool in session.tool_sequence:
        counts[tool] = counts.get(tool, 0) + 1
    near_turn_limit = bool(session.max_turns and session.turn_count >= max(1, session.max_turns - 1))
    return {
        "note": "orchestrator_agent_session_summary",
        "case_agent_mode": "human_case_agent",
        "legacy_orchestrator_main_path_used": False,
        "legacy_subagent_call_count": 0,
        "semantic_subagent_call_count": 0,
        "orchestrator_turn_count": session.turn_count,
        "orchestrator_tool_sequence": list(session.tool_sequence),
        "orchestrator_tool_call_counts": counts,
        "tool_rejection_count": session.tool_rejection_count,
        "submit_rejection_count": session.submit_rejection_count,
        "submit_rejection_issue_counts": dict(session.submit_rejection_issue_counts),
        "near_turn_limit_unhealthy_count": 1 if near_turn_limit else 0,
        "max_turns": session.max_turns,
        "stall_suspected_count": session.single_tool_loop_suspected_count,
        "consecutive_stall_count": session.current_consecutive_tool_count if session.current_consecutive_tool_count >= 4 else 0,
        "max_consecutive_tool_count": session.max_consecutive_tool_count,
        "repeated_submit_rejection_count": session.repeated_submit_rejection_count,
        "search_call_count": session.search_call_count,
        "search_new_subject_count": session.search_new_subject_count,
        "search_existing_only_count": session.search_existing_only_count,
        "search_no_result_count": session.search_no_result_count,
        "last_search_progress": session.last_search_progress,
        "attention_focus_change_count": session.attention_focus_change_count,
        "agenda_open_count": sum(1 for item in session.cognitive_workspace.investigation_agenda if item.status == "open"),
        "agenda_closed_count": session.agenda_closed_count,
        "noise_candidate_count": session.noise_candidate_count,
        "stall_warning_count": session.stall_warning_count,
        "manual_vs_agent_divergence_point": "",
        "resolution_readiness_summary": session.cognitive_workspace.resolution_readiness.model_dump(mode="json"),
        "cognitive_workspace": _compact_cognitive_workspace(session.cognitive_workspace),
        "last_turn_health": dict(session.last_turn_health),
        "action_health": _action_health_observation(session, max_turns=session.max_turns or session.turn_count),
        "saved_work_unit_count": len(session.draft_work_units),
        "saved_mechanically_ok_work_units": _draft_work_unit_summary(session.draft_work_units),
        "draft_revision_count": session.draft_revision_count,
        "latest_submit_repair": _latest_submit_repair_observation(session),
        "compact_count": 0,
        "context_soft_limit_hit_count": 0,
        "context_hard_limit_hit_count": 0,
        "session_mode": "human_case_agent",
        "provider_session_enabled": False,
        "provider_response_id": session.response_id,
        "provider_conversation_id": "",
        "http_session_id": session.http_session_id,
        "prompt_cache_key": session.prompt_cache_key,
        "last_tail_sha256": session.last_tail_sha256,
        "first_turn_estimated_tokens": session.first_turn_estimated_tokens,
        "agent_facing_locator_count": len(registry.locators),
        "tool_schema_token_estimate": _estimate_tokens(human_case_tool_definitions()),
        "case_memory_token_estimate": _estimate_tokens(
            {
                "notes": session.notes,
                "cognitive_workspace": _compact_cognitive_workspace(session.cognitive_workspace),
                "observations": session.observations[-8:],
            }
        ),
        "orchestrator_usage_input_tokens": session.usage_input_tokens,
        "orchestrator_usage_output_tokens": session.usage_output_tokens,
        "orchestrator_provider_cached_input_tokens": session.cached_input_tokens,
    }


def _budget_fallback_fail_closed_output(
    session: HumanCaseSession,
    last_verifier: CaseVerifierResult | None,
) -> CaseJudgeOutput:
    latest_repair = _latest_submit_repair_observation(session)
    issue_counts = latest_repair.get("issue_counts") if isinstance(latest_repair.get("issue_counts"), dict) else {}
    blocking_units = latest_repair.get("blocking_units") if isinstance(latest_repair.get("blocking_units"), list) else []
    required_missing = (
        latest_repair.get("required_missing_work_units")
        if isinstance(latest_repair.get("required_missing_work_units"), list)
        else []
    )
    verifier_issues = [
        str(getattr(issue, "issue_code", "") or "")
        for issue in list(getattr(last_verifier, "issues", []) or [])
        if str(getattr(issue, "issue_code", "") or "").strip()
    ]
    if issue_counts or blocking_units or required_missing or verifier_issues:
        compact_blockers: list[str] = []
        for unit in blocking_units[:5]:
            if not isinstance(unit, dict):
                continue
            unit_name = str(unit.get("unit") or unit.get("local") or "unit").strip()
            issue = unit.get("issue") or unit.get("issues") or "mechanical_blocker"
            compact_blockers.append(f"{unit_name}: {issue}")
        if required_missing:
            compact_blockers.append(f"missing_work_units={len(required_missing)}")
        if verifier_issues:
            compact_blockers.append(f"verifier_issues={','.join(verifier_issues[:6])}")
        if issue_counts:
            compact_blockers.append(
                "issue_counts="
                + ",".join(f"{key}:{value}" for key, value in list(issue_counts.items())[:8])
            )
        description = (
            "HumanCaseAgent reached the turn budget with a concrete unresolved submit repair agenda: "
            + "; ".join(compact_blockers or ["unresolved submit repair"])
        )
        return CaseJudgeOutput(
            action="fail_closed",
            fail_closed_reasons=[
                FailClosedReason(
                    ref="HFR1",
                    reason_kind="insufficient_evidence",
                    description=description,
                    related_refs=[],
                )
            ],
            summary="unresolved_submit_repair",
        )
    return CaseJudgeOutput(
        action="fail_closed",
        fail_closed_reasons=[
            FailClosedReason(
                ref="HFR1",
                reason_kind="budget_exhausted",
                description="HumanCaseAgent reached turn budget without an accepted package-level submit.",
                related_refs=[],
            )
        ],
        summary="budget_exhausted",
    )


def _progress_path() -> str:
    return str(os.environ.get("LOCAL_BANGUMI_CASE_AGENT_PROGRESS_PATH") or "").strip()


def _write_progress(workspace: CaseEvidenceWorkspace, session: HumanCaseSession, phase: str, registry: LocatorRegistry) -> None:
    path = _progress_path()
    if not path:
        return
    payload = {
        "kind": "local_bangumi_human_case_agent_progress",
        "updated_at_ms": int(time.time() * 1000),
        "case_id": workspace.header.case_id,
        "phase": phase,
        "session": _session_summary(session, registry),
    }
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
    except Exception:
        return


def _error_result(workspace: CaseEvidenceWorkspace, summary: str, error_kind: str, audits: list[dict[str, object]]):
    from .orchestrator import CaseAgentRunResult

    verifier = CaseVerifierResult(
        passed=False,
        issues=[_issue("human_case_agent", error_kind, summary)],
        summary=summary,
    )
    workspace = _workspace_add_audits(workspace, audits)
    return CaseAgentRunResult(
        ok=False,
        case_id=workspace.header.case_id,
        status="error",
        final_action="human_case_agent",
        final_output=None,
        final_verifier_result=verifier,
        final_workspace=workspace,
        judge_outputs=[],
        evidence_batches=[],
        summary=summary,
        errors=[summary, f"error_kind={error_kind}"],
    )


def _recoverable_tool_call_error(error: str) -> bool:
    return str(error or "").startswith(
        (
            "human_case_agent_no_tool_call",
            "human_case_agent_invalid_tool_call",
            "human_case_agent_unknown_tool",
            "human_case_agent_tool_args_json_error",
            "human_case_agent_tool_args_not_object",
            "human_case_agent_tool_args_schema_error",
        )
    )


def run_human_case_agent(
    initial_workspace: CaseEvidenceWorkspace,
    ai_client: object,
    bangumi_client: object,
    *,
    max_rounds: int | None = None,
    **_: object,
):
    from .orchestrator import CaseAgentRunResult

    desk, registry = build_human_case_desk(initial_workspace)
    _register_existing_targets(initial_workspace, registry)
    session = HumanCaseSession(
        case_id=initial_workspace.header.case_id,
        http_session_id=f"bar_human_lbg_{_slug(initial_workspace.header.case_id, fallback='case')}_{abs(hash(initial_workspace.header.case_id)) & 0xffff:x}",
        cognitive_workspace=_initial_cognitive_workspace_from_desk(desk),
    )
    desk_bytes = len(json.dumps(desk, ensure_ascii=False, default=str).encode("utf-8"))
    desk_audit = {
        "note": "human_case_agent_desk_built",
        "case_agent_mode": "human_case_agent",
        "desk_bytes": desk_bytes,
        "desk_estimated_tokens": _estimate_tokens(desk),
        "desk_group_count": len(desk.get("local_locators") or []),
        "agent_facing_locator_count": len(registry.locators),
        "first_turn_contains_full_lf_list": False,
        "must_account_locator_count": int((desk.get("resolution_contract") or {}).get("must_account_locator_count") or 0),
        "support_only_locator_count": int((desk.get("resolution_contract") or {}).get("support_only_locator_count") or 0),
        "cognitive_workspace_initial_work_unit_count": len(session.cognitive_workspace.active_work_units),
    }
    workspace = _workspace_add_audits(initial_workspace, [desk_audit])
    max_turns = max(1, int(max_rounds or initial_workspace.header.max_rounds or 12))
    session = replace(session, max_turns=max_turns)
    audits: list[dict[str, object]] = []
    final_submit: SubmitCompileResult | None = None
    last_verifier: CaseVerifierResult | None = None
    _write_progress(workspace, session, "started", registry)

    for _turn in range(max_turns):
        tool_call, session, call_audit, error = _call_human_agent(ai_client, desk, session, max_turns=max_turns)
        audits.append(call_audit)
        if error:
            if _recoverable_tool_call_error(error):
                session.tool_rejection_count += 1
                session.observations.append(
                    {
                        "tool": "tool_call_parser",
                        "output": {
                            "accepted": False,
                            "issue": error,
                            "available_action": "Call exactly one of inspect, search, note, or submit with valid JSON arguments.",
                        },
                    }
                )
                _write_progress(workspace, session, "tool_call_rejected", registry)
                continue
            summary = "HumanCaseAgent provider/tool call failed"
            audits.append(_session_summary(session, registry))
            return _error_result(workspace, summary, error, audits)
        if tool_call is None:
            audits.append(_session_summary(session, registry))
            return _error_result(workspace, "HumanCaseAgent returned no tool call", "human_case_agent_no_tool_call", audits)
        output: dict[str, object]
        before_workspace_counts = _workspace_counts(workspace)
        before_cognitive = session.cognitive_workspace.model_copy(deep=True)
        repeated_for_health = False
        budget_rejection = _budget_pressure_tool_rejection(session, tool_call.tool_name, max_turns=max_turns)
        if budget_rejection is not None:
            output = budget_rejection
            session.tool_rejection_count += 1
            audits.append(
                {
                    "note": "human_case_agent_tool_rejected",
                    "reason": "turn_budget_requires_resolution",
                    "tool_name": tool_call.tool_name,
                    "remaining_turns": output.get("remaining_turns"),
                }
            )
        elif tool_call.tool_name == "search":
            workspace, output = _search_tool(
                workspace,
                registry,
                bangumi_client,
                tool_call.arguments,  # type: ignore[arg-type]
                seen_variant_keys=session.searched_query_variant_keys,
            )
            output = _layer_search_output_for_workspace(output, session.cognitive_workspace)
            progress = str(output.get("search_progress") or "")
            if not output.get("accepted"):
                session.tool_rejection_count += 1
                audits.append(
                    {
                        "note": "human_case_agent_tool_rejected",
                        "reason": str(output.get("issue") or "search_rejected"),
                        "tool_name": tool_call.tool_name,
                    }
                )
            session = replace(
                session,
                search_call_count=session.search_call_count + 1,
                search_new_subject_count=session.search_new_subject_count + int(output.get("new_subject_count") or 0),
                search_existing_only_count=session.search_existing_only_count + (1 if progress == "existing_subjects_only" else 0),
                search_no_result_count=session.search_no_result_count + (1 if progress == "no_subject_results" else 0),
                last_search_progress=progress,
                noise_candidate_count=session.noise_candidate_count + int(output.get("noise_candidate_count") or 0),
            )
        elif tool_call.tool_name == "inspect":
            inspect_args, inspect_adjustment = _inspect_args_with_required_repair_locators(
                session,
                tool_call.arguments,  # type: ignore[arg-type]
            )
            workspace, output = _inspect_tool(workspace, registry, bangumi_client, inspect_args)
            if inspect_adjustment:
                output = {**output, "required_repair_inspect": inspect_adjustment}
        elif tool_call.tool_name == "note":
            output = _note_tool(registry, session, tool_call.arguments)  # type: ignore[arg-type]
            if not output.get("accepted"):
                session.tool_rejection_count += 1
        elif tool_call.tool_name == "submit":
            effective_submit_args = _submit_args_with_saved_draft(
                registry,
                tool_call.arguments,  # type: ignore[arg-type]
                session.draft_work_units,
            )
            submit_result = _submit_tool(
                workspace,
                registry,
                effective_submit_args,
                searched_query_variant_keys=session.searched_query_variant_keys,
            )
            workspace, session, submit_result, auto_inspect_audit = _submit_result_with_auto_target_surface_inspect(
                workspace,
                registry,
                bangumi_client,
                session,
                effective_submit_args,
                submit_result,
                searched_query_variant_keys=session.searched_query_variant_keys,
            )
            if auto_inspect_audit:
                audits.append(auto_inspect_audit)
            last_verifier = submit_result.verifier
            output = submit_result.feedback
            if effective_submit_args is not tool_call.arguments:
                output = {
                    **output,
                    "saved_work_units_applied": True,
                    "saved_work_unit_count": len(session.draft_work_units),
                }
            requested_dry_run = bool(getattr(tool_call.arguments, "dry_run", False))
            dry_run = requested_dry_run
            if submit_result.accepted:
                dry_run_promoted_to_final = bool(requested_dry_run and session.turn_count >= max_turns)
                if dry_run_promoted_to_final:
                    dry_run = False
                    output = {
                        **output,
                        "requested_dry_run": True,
                        "dry_run_promoted_to_final": True,
                        "promotion_reason": (
                            "The package resolution already passed verifier/accounting on the final allowed turn; "
                            "the fixed layer finalized the same resolution instead of spending an impossible extra turn."
                        ),
                    }
                fail_closed_blocker = (
                    _fail_closed_blocker_from_open_repair(session)
                    if submit_result.output is not None
                    and submit_result.output.action == "fail_closed"
                    and not dry_run
                    else {}
                )
                if fail_closed_blocker:
                    output = fail_closed_blocker
                    session.submit_rejection_count += 1
                    session.submit_rejection_issue_counts["fail_closed_with_executable_target_surface_action"] = (
                        session.submit_rejection_issue_counts.get("fail_closed_with_executable_target_surface_action", 0) + 1
                    )
                    audits.append(
                        {
                            "note": "human_case_agent_submit_result",
                            "accepted": False,
                            "issue_counts": output.get("issue_counts"),
                            "finish_blocked": True,
                        }
                    )
                    session = replace(
                        session,
                        cognitive_workspace=_workspace_with_submit_rejection(
                            session.cognitive_workspace,
                            output,
                            repeated=False,
                        ),
                    )
                    session, output = _apply_turn_health(
                        session,
                        {
                            **output,
                            "active_repair_agenda": _active_repair_agenda_for_prompt(session),
                            "resolution_readiness": session.cognitive_workspace.resolution_readiness.model_dump(mode="json"),
                        },
                        before_workspace_counts=before_workspace_counts,
                        after_workspace=workspace,
                        before_cognitive=before_cognitive,
                        repeated_submit_rejection=False,
                    )
                    session = _record_tool_output(session, tool_call, output)
                    _write_progress(workspace, session, f"tool_{tool_call.tool_name}", registry)
                    continue
                if dry_run:
                    output = {
                        **output,
                        "dry_run": True,
                        "accepted_but_not_final": True,
                        "required_next_action": "Submit the same package resolution with dry_run=false to finish. Repeating an accepted dry_run makes no progress.",
                    }
                if not dry_run:
                    final_submit = submit_result
                session = replace(
                    session,
                    cognitive_workspace=_workspace_with_submit_acceptance(session.cognitive_workspace),
                )
                output = {
                    **output,
                    "resolution_readiness": session.cognitive_workspace.resolution_readiness.model_dump(mode="json"),
                }
                audits.append(
                    {
                        "note": "human_case_agent_submit_result",
                        "accepted": True,
                        "output_action": submit_result.output.action if submit_result.output is not None else "",
                        "dry_run": requested_dry_run,
                        "dry_run_promoted_to_final": dry_run_promoted_to_final,
                        "mapped_file_count": submit_result.mapped_file_count,
                        "excluded_file_count": submit_result.excluded_file_count,
                    }
                )
                if not dry_run:
                    session, output = _apply_turn_health(
                        session,
                        output,
                        before_workspace_counts=before_workspace_counts,
                        after_workspace=workspace,
                        before_cognitive=before_cognitive,
                    )
                    session = _record_tool_output(session, tool_call, output)
                    break
                session, output = _apply_turn_health(
                    session,
                    output,
                    before_workspace_counts=before_workspace_counts,
                    after_workspace=workspace,
                    before_cognitive=before_cognitive,
                )
                session = _record_tool_output(session, tool_call, output)
                session = replace(session, last_submit_dry_run_accepted=True)
                _write_progress(workspace, session, f"tool_{tool_call.tool_name}", registry)
                continue
            session.submit_rejection_count += 1
            issue_counts = output.get("package", {}).get("issue_counts", {}) if isinstance(output.get("package"), dict) else {}
            if isinstance(issue_counts, dict):
                for key, value in issue_counts.items():
                    session.submit_rejection_issue_counts[str(key)] = session.submit_rejection_issue_counts.get(str(key), 0) + int(value or 0)
            fingerprint = _submit_rejection_fingerprint(output)
            repeated_fingerprint = bool(fingerprint and fingerprint == session.last_submit_rejection_fingerprint)
            if repeated_fingerprint:
                session.repeated_submit_rejection_count += 1
                repeated_for_health = True
                output = {
                    **output,
                    "repeat_rejection_warning": {
                        "issue": "same_submit_rejection_repeated",
                        "repeat_count": session.repeated_submit_rejection_count,
                        "message": (
                            "This submit was rejected for the same mechanical reason as the previous rejected submit. "
                            "Do not submit again until you change the cited locator/resolution field, or inspect/search "
                            "the cited target/local locator for missing facts."
                        ),
                    },
                }
            observation_output = _repair_agenda_from_submit_feedback(output, repeated=repeated_fingerprint)
            session = replace(
                session,
                cognitive_workspace=_workspace_with_submit_rejection(
                    session.cognitive_workspace,
                    observation_output,
                    repeated=repeated_fingerprint,
                ),
            )
            observation_output = {
                **observation_output,
                "active_repair_agenda": _active_repair_agenda_for_prompt(session),
                "resolution_readiness": session.cognitive_workspace.resolution_readiness.model_dump(mode="json"),
            }
            finalization_guard_output = _near_cap_submit_finalization_guard_output(
                session,
                effective_submit_args,
                max_turns=max_turns,
            )
            if finalization_guard_output:
                session.tool_rejection_count += 1
                audits.append(
                    {
                        "note": "human_case_agent_tool_rejected",
                        "reason": finalization_guard_output.get("issue"),
                        "tool_name": tool_call.tool_name,
                        "remaining_turns": (
                            finalization_guard_output.get("near_cap_repair_finalization_guard", {})
                            if isinstance(finalization_guard_output.get("near_cap_repair_finalization_guard"), dict)
                            else {}
                        ).get("remaining_turns"),
                    }
                )
                observation_output = {
                    **observation_output,
                    **finalization_guard_output,
                }
            previous_saved_count = len(session.draft_work_units)
            package_feedback = output.get("package") if isinstance(output.get("package"), dict) else {}
            duplicate_target_repair_units = (
                list(package_feedback.get("duplicate_target_repair_units") or [])
                if isinstance(package_feedback, dict)
                else []
            )
            merged_draft_units = _merge_draft_work_units(
                registry,
                session.draft_work_units,
                list(effective_submit_args.resolution.work_units),
                list(output.get("units") or []),
                duplicate_target_repair_units,
            )
            if len(merged_draft_units) != previous_saved_count:
                observation_output = {
                    **observation_output,
                    "saved_mechanically_ok_work_units": _draft_work_unit_summary(merged_draft_units),
                    "saved_work_unit_count": len(merged_draft_units),
                    "draft_merge_instruction": (
                        "You may keep these saved mechanically-ok work units in the next submit. "
                        "Submit only changed or missing units plus any saved units you intentionally revise."
                    ),
                }
            session = replace(
                session,
                draft_work_units=merged_draft_units,
                draft_revision_count=session.draft_revision_count + (1 if len(merged_draft_units) != previous_saved_count else 0),
            )
            session.last_submit_rejection_fingerprint = fingerprint
            audits.append(
                {
                    "note": "human_case_agent_submit_result",
                    "accepted": False,
                    "issue_counts": issue_counts,
                    "repeated_submit_rejection": repeated_fingerprint,
                    "feedback": _compact_submit_feedback_for_audit(output),
                }
            )
            output = observation_output
        else:
            output = {"accepted": False, "issue": "unknown_tool"}
            session.tool_rejection_count += 1
        session, output = _apply_turn_health(
            session,
            output,
            before_workspace_counts=before_workspace_counts,
            after_workspace=workspace,
            before_cognitive=before_cognitive,
            repeated_submit_rejection=repeated_for_health,
        )
        session = _record_tool_output(session, tool_call, output)
        _write_progress(workspace, session, f"tool_{tool_call.tool_name}", registry)

    audits.append(_session_summary(session, registry))
    if final_submit is not None and final_submit.output is not None:
        workspace = workspace.with_seen_detail_refs(
            [
                assignment.target_ref
                for assignment in final_submit.output.assignment_intents
                if assignment.target_ref and assignment.target_ref != "UNALIGNED"
            ]
        )
        workspace = _workspace_add_audits(workspace, audits)
        if final_submit.output.action == "fail_closed":
            return CaseAgentRunResult(
                ok=True,
                case_id=workspace.header.case_id,
                status="fail_closed",
                final_action="submit",
                final_output=final_submit.output,
                final_verifier_result=final_submit.verifier,
                final_workspace=workspace,
                judge_outputs=[final_submit.output],
                evidence_batches=[],
                summary=final_submit.output.summary or "agent_fail_closed_from_submit",
                errors=[],
            )
        return CaseAgentRunResult(
            ok=True,
            case_id=workspace.header.case_id,
            status="accepted",
            final_action="submit",
            final_output=final_submit.output,
            final_verifier_result=final_submit.verifier,
            final_workspace=workspace,
            judge_outputs=[final_submit.output],
            evidence_batches=[],
            summary="accepted_from_human_case_submit",
            errors=[],
        )

    workspace = _workspace_add_audits(workspace, audits)
    fail_output = _budget_fallback_fail_closed_output(session, last_verifier)
    verifier = last_verifier or CaseVerifierResult(
        passed=True,
        issues=[],
        summary=f"HumanCaseAgent fail_closed after {fail_output.summary or 'budget exhaustion'}",
    )
    return CaseAgentRunResult(
        ok=True,
        case_id=workspace.header.case_id,
        status="fail_closed",
        final_action="fail_closed",
        final_output=fail_output,
        final_verifier_result=verifier,
        final_workspace=workspace,
        judge_outputs=[fail_output],
        evidence_batches=[],
        summary=fail_output.summary or "budget_exhausted",
        errors=[],
    )
