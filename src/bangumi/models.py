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

    model_config = ConfigDict(extra="forbid")


class BangumiSubjectRelation(BaseModel):
    id: int
    type: int = Field(default=2)
    relation: str = ""
    name: str = ""
    name_cn: str = ""

    model_config = ConfigDict(extra="forbid")


class BangumiEpisode(BaseModel):
    id: int
    subject_id: int
    type: int = 0
    sort: int = 0
    ep: Optional[int] = None
    disc: Optional[int] = None
    name: str = ""
    name_cn: str = ""
    airdate: str = ""
    duration: str = ""
    duration_seconds: Optional[int] = None
    desc: str = ""

    model_config = ConfigDict(extra="forbid")


class BangumiSubjectContext(BaseModel):
    subject: BangumiSubject
    relation_to_main: str = "main"
    score: float = 0.0
    episodes: List[BangumiEpisode] = Field(default_factory=list)

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
