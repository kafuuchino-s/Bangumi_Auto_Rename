from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self


BgmDisposition = Literal[
    'map_to_bangumi',
    'non_bangumi_or_supplemental',
    'needs_more_evidence',
    'unaligned_fail_closed',
]
TmdbMediaType = Literal['tv', 'movie']
BridgeMappingDisposition = Literal['map_to_tmdb', 'tmdb_target_absent', 'unmapped_supplemental']
BgmToTmdbRuleType = Literal[
    'episode_sequence',
    'movie',
    'special_sequence',
    'span',
    'tmdb_absent_group',
    'supplemental_group',
]
BgmToTmdbNumberField = Literal['sort', 'ep', 'extracted_episode_number']

TV_LEGAL_NODE_RE = re.compile(r'^tv:(?P<tmdb_id>\d+):S(?P<season>\d{2})E(?P<episode>\d{2,4})$')
MOVIE_LEGAL_NODE_RE = re.compile(r'^movie:(?P<tmdb_id>\d+)$')
TMDB_REF_RE = re.compile(r'^(?:tv|movie):\d+$')


def normalize_source_path(path: object) -> str:
    text = str(path or '').strip().replace('\\', '/')
    while text.startswith('./'):
        text = text[2:]
    return text


def tmdb_ref(media_type: TmdbMediaType, tmdb_id: int) -> str:
    return f'{media_type}:{int(tmdb_id)}'


def tv_legal_node_id(tmdb_id: int, season_number: int, episode_number: int) -> str:
    return f'tv:{int(tmdb_id)}:S{int(season_number):02d}E{int(episode_number):02d}'


def movie_legal_node_id(tmdb_id: int) -> str:
    return f'movie:{int(tmdb_id)}'


class BgmTargetRef(BaseModel):
    bangumi_subject_id: int = 0
    media_kind: str = ''
    episode_id: int = 0
    episode_type: str = ''
    sort: int | None = None
    ep: int | None = None
    title: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmTargetSpanRef(BaseModel):
    bangumi_subject_id: int = 0
    media_kind: str = ''
    episode_ids: list[int] = Field(default_factory=list)
    sort_start: int | None = None
    sort_end: int | None = None
    episode_type: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmAssignmentRef(BaseModel):
    source_path: str = ''
    disposition: BgmDisposition = 'map_to_bangumi'
    rule_name: str = ''
    target: BgmTargetRef = Field(default_factory=BgmTargetRef)
    target_span: BgmTargetSpanRef = Field(default_factory=BgmTargetSpanRef)
    extracted_episode_number: int | None = None
    reason: str = ''

    @field_validator('source_path', mode='before')
    @classmethod
    def normalize_path(cls, value: object) -> str:
        return normalize_source_path(value)

    @property
    def is_mapped_bangumi(self) -> bool:
        return self.disposition == 'map_to_bangumi'

    @property
    def is_span(self) -> bool:
        return bool(self.target_span.episode_ids)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmToTmdbInput(BaseModel):
    source_path: str = ''
    assignments: list[BgmAssignmentRef] = Field(default_factory=list)

    @field_validator('source_path', mode='before')
    @classmethod
    def normalize_path(cls, value: object) -> str:
        return normalize_source_path(value)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class TmdbLegalNode(BaseModel):
    legal_node_id: str = ''
    media_type: TmdbMediaType
    tmdb_id: int
    season_number: int | None = None
    episode_number: int | None = None
    episode_type: str = ''
    title: str = ''
    air_date: str = ''
    runtime: int | None = None
    overview: str = ''

    @model_validator(mode='after')
    def validate_identity(self) -> Self:
        movie_match = MOVIE_LEGAL_NODE_RE.fullmatch(self.legal_node_id)
        tv_match = TV_LEGAL_NODE_RE.fullmatch(self.legal_node_id)
        if self.media_type == 'movie':
            if movie_match is None:
                raise ValueError('movie legal_node_id must use movie:<tmdb_id>')
            if int(movie_match.group('tmdb_id')) != self.tmdb_id:
                raise ValueError('movie legal_node_id tmdb_id must match tmdb_id')
            if self.season_number is not None or self.episode_number is not None:
                raise ValueError('movie legal nodes must not include season or episode numbers')
        else:
            if tv_match is None:
                raise ValueError('tv legal_node_id must use tv:<tmdb_id>:SxxEyy')
            if int(tv_match.group('tmdb_id')) != self.tmdb_id:
                raise ValueError('tv legal_node_id tmdb_id must match tmdb_id')
            season = int(tv_match.group('season'))
            episode = int(tv_match.group('episode'))
            if self.season_number is not None and self.season_number != season:
                raise ValueError('tv legal_node_id season must match season_number')
            if self.episode_number is not None and self.episode_number != episode:
                raise ValueError('tv legal_node_id episode must match episode_number')
            self.season_number = season
            self.episode_number = episode
        return self

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class TmdbSeasonCard(BaseModel):
    season_number: int
    name: str = ''
    episode_count: int = 0
    year: int | None = None
    overview: str = ''
    legal_node_ids: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class TmdbCandidateCard(BaseModel):
    media_type: TmdbMediaType
    tmdb_id: int
    tmdb_ref: str = ''
    display_title: str = ''
    original_title: str = ''
    original_name: str = ''
    slug: str = ''
    web_url: str = ''
    year: int | None = None
    overview: str = ''
    aliases: list[str] = Field(default_factory=list)
    season_cards: list[TmdbSeasonCard] = Field(default_factory=list)
    legal_nodes: list[TmdbLegalNode] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_identity(self) -> Self:
        expected_ref = tmdb_ref(self.media_type, self.tmdb_id)
        if not self.tmdb_ref:
            self.tmdb_ref = expected_ref
        if self.tmdb_ref != expected_ref or TMDB_REF_RE.fullmatch(self.tmdb_ref) is None:
            raise ValueError('tmdb_ref must be tv:<tmdb_id> or movie:<tmdb_id> and match media_type/tmdb_id')
        for node in self.legal_nodes:
            if node.media_type != self.media_type or node.tmdb_id != self.tmdb_id:
                raise ValueError('candidate legal nodes must match candidate media_type and tmdb_id')
        return self

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class TmdbLegalGraph(BaseModel):
    candidates: list[TmdbCandidateCard] = Field(default_factory=list)
    generated_by: str = ''

    def legal_node_map(self) -> dict[str, TmdbLegalNode]:
        return {
            node.legal_node_id: node
            for candidate in self.candidates
            for node in candidate.legal_nodes
        }

    def candidate_map(self) -> dict[str, TmdbCandidateCard]:
        return {candidate.tmdb_ref: candidate for candidate in self.candidates}

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmToTmdbMapping(BaseModel):
    source_path: str = ''
    disposition: BridgeMappingDisposition = 'map_to_tmdb'
    tmdb_legal_node_ids: list[str] = Field(default_factory=list)
    confidence: Literal['High', 'Medium', 'Low'] = 'Medium'
    reason: str = ''

    @field_validator('source_path', mode='before')
    @classmethod
    def normalize_path(cls, value: object) -> str:
        return normalize_source_path(value)

    @field_validator('tmdb_legal_node_ids', mode='before')
    @classmethod
    def normalize_node_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmToTmdbMappingDraft(BaseModel):
    mappings: list[BgmToTmdbMapping] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmToTmdbBgmSelector(BaseModel):
    bangumi_subject_id: int = 0
    media_kind: str = ''
    episode_type: str = ''
    sort_range: str = ''
    ep_range: str = ''
    episode_ids: list[int] = Field(default_factory=list)
    rule_name: str = ''
    source_paths: list[str] = Field(default_factory=list)

    @field_validator('source_paths', mode='before')
    @classmethod
    def normalize_source_paths(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [normalize_source_path(value)] if normalize_source_path(value) else []
        if isinstance(value, list):
            return [normalize_source_path(item) for item in value if normalize_source_path(item)]
        return [normalize_source_path(value)] if normalize_source_path(value) else []

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmToTmdbTmdbTarget(BaseModel):
    tmdb_ref: str = ''
    season_number: int | None = None
    episode_range: str = ''
    episode_offset: str = 'EP'
    number_field: BgmToTmdbNumberField = 'sort'
    tmdb_legal_node_id: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmToTmdbRecipeRule(BaseModel):
    name: str = ''
    rule_type: BgmToTmdbRuleType = 'episode_sequence'
    select_bgm: BgmToTmdbBgmSelector = Field(default_factory=BgmToTmdbBgmSelector)
    target_tmdb: BgmToTmdbTmdbTarget = Field(default_factory=BgmToTmdbTmdbTarget)
    confidence: Literal['High', 'Medium', 'Low'] = 'Medium'
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class BgmToTmdbRecipeParams(BaseModel):
    version: int = 1
    summary: str = ''
    rules: list[BgmToTmdbRecipeRule] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class VerifiedBgmToTmdbPlan(BaseModel):
    source_path: str = ''
    mappings: list[BgmToTmdbMapping] = Field(default_factory=list)
    tmdb_target_count: int = 0
    tmdb_absent_count: int = 0
    supplemental_count: int = 0
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')
