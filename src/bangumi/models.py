from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class BangumiSubject(BaseModel):
    id: int
    type: int = Field(default=2)
    name: str = ""
    name_cn: str = ""
    date: str = ""
    summary: str = ""
    platform: str = ""
    total_episodes: int = 0
    eps: int = 0
    rating_score: Optional[float] = None
    rating_total: int = 0
    rank: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    meta_tags: List[str] = Field(default_factory=list)
    infobox: list[dict[str, Any]] = Field(default_factory=list)
    search_keyword: str = ""
    search_rank: int = 0

    model_config = ConfigDict(extra="forbid")


class BangumiSubjectRelation(BaseModel):
    id: int
    type: int = Field(default=2)
    relation: str = ""
    name: str = ""
    name_cn: str = ""

    model_config = ConfigDict(extra="forbid")


class BangumiRelationEdge(BaseModel):
    from_subject_id: int
    relation: str = ""
    to_subject_id: int
    from_subject_name: str = ""
    from_subject_name_cn: str = ""
    to_subject_name: str = ""
    to_subject_name_cn: str = ""

    model_config = ConfigDict(extra="forbid")


class BangumiEpisode(BaseModel):
    id: int
    subject_id: int
    type: int = 0
    sort: int = 0
    ep: Optional[int] = None
    disc: Optional[int] = None
    synthetic: bool = False
    synthetic_reason: str = ""
    subject_level_target: bool = False
    kind: str = ""
    title: str = ""
    name: str = ""
    name_cn: str = ""
    airdate: str = ""
    duration: str = ""
    duration_seconds: Optional[int] = None
    desc: str = ""
    source_form_hint: Literal["tv_series", "movie", "ova", "web", "special", "unknown"] = "unknown"
    relation: str = ""
    relation_to_main: str = ""
    source_role: str = ""

    model_config = ConfigDict(extra="forbid")


class BangumiSubjectContext(BaseModel):
    subject: BangumiSubject
    source_kind: str = "related"
    relation_to_main: str = "main"
    relation: str = ""
    distance: Optional[int] = None
    parent_subject_id: Optional[int] = None
    relation_path: List[BangumiRelationEdge] = Field(default_factory=list)
    score: float = 0.0
    source_form_hint: Literal["tv_series", "movie", "ova", "web", "special", "unknown"] = "unknown"
    source_form_evidence: List[str] = Field(default_factory=list)
    episodes: List[BangumiEpisode] = Field(default_factory=list)
    subject_id: int = 0
    name: str = ""
    name_cn: str = ""
    title: str = ""
    platform: str = ""
    date: str = ""
    source_role: str = ""
    relation_path_text: str = ""

    model_config = ConfigDict(extra="forbid")


class BangumiTVContext(BaseModel):
    source: Literal["bangumi"] = "bangumi"
    search_keywords: List[str] = Field(default_factory=list)
    selected_subject_id: int
    selected_subject_reason: str = ""
    subjects: List[BangumiSubjectContext] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    def to_prompt_dict(self) -> Dict[str, Any]:
        return self.model_dump()
