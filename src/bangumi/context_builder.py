from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import TypedDict, cast

from ..logger import logger
from .client import BangumiClient
from .models import (
    BangumiEpisode,
    BangumiSubject,
    BangumiSubjectContext,
    BangumiSubjectRelation,
    BangumiTVContext,
)


class AnimeInfoDict(TypedDict, total=False):
    name: str
    original_name: str
    original_title: str
    name_cn: str
    first_air_date: str


class LocalFileDict(TypedDict, total=False):
    filename: str
    path: str


class MainCandidate(TypedDict):
    subject: BangumiSubject
    score: float


class RelatedCandidate(TypedDict):
    subject: BangumiSubject
    relation: str
    score: float


class BangumiPromptContext(TypedDict, total=False):
    search_keywords: list[str]
    selected_subject_id: int
    selected_subject_reason: str
    subjects: list[object]


class BangumiContextBuilder:
    MAX_KEYWORDS: int = 6
    MAX_MAIN_CANDIDATES: int = 4
    MAX_RELATED_SUBJECTS: int = 3
    MAX_TOTAL_SUBJECTS: int = 4
    MAX_EPISODES_PER_SUBJECT: int = 60
    ANIME_TYPE: int = 2
    RELATION_PRIORITY: dict[str, int] = {
        "续集": 5,
        "前传": 4,
        "番外篇": 4,
        "衍生": 3,
        "其他": 2,
        "不同演绎": 1,
    }

    def __init__(self, client: BangumiClient | None = None) -> None:
        self.client: BangumiClient = client or BangumiClient()

    def build_tv_context(
        self,
        anime_info: AnimeInfoDict,
        local_files: list[LocalFileDict],
    ) -> BangumiPromptContext | None:
        try:
            keywords = self._build_search_keywords(anime_info, local_files)
            if not keywords:
                return None

            main_candidates = self._collect_main_candidates(anime_info, keywords)
            if not main_candidates:
                return None

            selected = main_candidates[0]
            selected_subject = selected["subject"]
            subject_contexts: list[BangumiSubjectContext] = [
                self._build_subject_context(selected_subject, "main", selected["score"])
            ]

            related = self._collect_related_subjects(selected_subject.id, anime_info)
            for item in related:
                if len(subject_contexts) >= self.MAX_TOTAL_SUBJECTS:
                    break
                context = self._build_subject_context(
                    item["subject"],
                    item["relation"],
                    item["score"],
                )
                if context.episodes:
                    subject_contexts.append(context)

            subject_contexts = [ctx for ctx in subject_contexts if ctx.episodes]
            if not subject_contexts:
                return None

            context = BangumiTVContext(
                search_keywords=keywords,
                selected_subject_id=selected_subject.id,
                selected_subject_reason=(
                    f"匹配得分最高: {selected['score']:.2f}, 标题={selected_subject.name_cn or selected_subject.name}"
                ),
                subjects=subject_contexts,
            )
            return cast(BangumiPromptContext, cast(object, context.to_prompt_dict()))
        except Exception as exc:
            logger.warning(f"[Bangumi] 构建上下文失败，回退 TMDB-only: {exc}")
            return None

    def _build_search_keywords(
        self, anime_info: AnimeInfoDict, local_files: list[LocalFileDict]
    ) -> list[str]:
        keywords: list[str] = []

        for key in ("name", "original_name", "original_title"):
            value = str(anime_info.get(key) or "").strip()
            self._append_keyword(keywords, value)
            self._append_keyword(keywords, self._strip_season_suffix(value))

        name_cn = str(anime_info.get("name_cn") or "").strip()
        self._append_keyword(keywords, name_cn)

        for file_info in local_files[:3]:
            filename = str(file_info.get("filename") or file_info.get("path") or "")
            guessed = self._guess_title_from_filename(filename)
            self._append_keyword(keywords, guessed)
            self._append_keyword(keywords, self._strip_season_suffix(guessed))

        return keywords[: self.MAX_KEYWORDS]

    def _collect_main_candidates(
        self, anime_info: AnimeInfoDict, keywords: list[str]
    ) -> list[MainCandidate]:
        year = self._extract_year(anime_info.get("first_air_date"))
        seen: set[int] = set()
        scored: list[MainCandidate] = []

        for keyword in keywords:
            subjects = self.client.search_subjects(keyword, year)
            if not subjects and year:
                subjects = self.client.search_subjects(keyword, None)

            for subject in subjects:
                if subject.id in seen or subject.type != self.ANIME_TYPE:
                    continue
                seen.add(subject.id)
                score = self._score_subject(anime_info, keyword, subject)
                scored.append({"subject": subject, "score": score})

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[: self.MAX_MAIN_CANDIDATES]

    def _collect_related_subjects(
        self,
        subject_id: int,
        anime_info: AnimeInfoDict,
    ) -> list[RelatedCandidate]:
        relations = self.client.get_related_subjects(subject_id)
        seen: set[int] = {subject_id}
        scored: list[RelatedCandidate] = []

        for relation in relations:
            if relation.id in seen or relation.type != self.ANIME_TYPE:
                continue
            seen.add(relation.id)
            full_subject = self.client.get_subject(relation.id)
            if not full_subject:
                continue
            if not self._is_relevant_related_subject(full_subject, relation, anime_info):
                continue
            score = self._score_related_subject(full_subject, relation, anime_info)
            scored.append(
                {
                    "subject": full_subject,
                    "relation": relation.relation or "related",
                    "score": score,
                }
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[: self.MAX_RELATED_SUBJECTS]

    def _build_subject_context(
        self,
        subject: BangumiSubject,
        relation_to_main: str,
        score: float,
    ) -> BangumiSubjectContext:
        episodes = self.client.get_episodes(subject.id)
        compact_episodes = self._compact_episodes(episodes)
        return BangumiSubjectContext(
            subject=subject,
            relation_to_main=relation_to_main,
            score=round(score, 3),
            episodes=compact_episodes,
        )

    def _compact_episodes(self, episodes: list[BangumiEpisode]) -> list[BangumiEpisode]:
        filtered = [ep for ep in episodes if ep.sort >= 0]
        filtered.sort(key=lambda ep: (ep.sort, ep.id))
        return filtered[: self.MAX_EPISODES_PER_SUBJECT]

    def _score_subject(
        self,
        anime_info: AnimeInfoDict,
        keyword: str,
        subject: BangumiSubject,
    ) -> float:
        score = 0.0
        target_titles = [
            str(anime_info.get("name") or ""),
            str(anime_info.get("original_name") or ""),
            keyword,
        ]
        candidate_titles = [subject.name, subject.name_cn]

        best_similarity = 0.0
        for target in target_titles:
            target_norm = self._normalize_text(target)
            if not target_norm:
                continue
            for candidate in candidate_titles:
                candidate_norm = self._normalize_text(candidate)
                if not candidate_norm:
                    continue
                best_similarity = max(
                    best_similarity,
                    SequenceMatcher(None, target_norm, candidate_norm).ratio(),
                )
                if target_norm == candidate_norm:
                    score += 2.0

        score += best_similarity * 6.0

        target_year = self._extract_year(anime_info.get("first_air_date"))
        subject_year = self._extract_year(subject.date)
        if target_year and subject_year:
            diff = abs(target_year - subject_year)
            if diff == 0:
                score += 2.0
            elif diff == 1:
                score += 1.0
            elif diff >= 3:
                score -= 2.0

        if subject.rank is not None:
            score += max(0.0, 1.5 - min(subject.rank, 5000) / 5000)
        if subject.rating_total:
            score += min(subject.rating_total, 5000) / 5000
        if any(tag in (subject.meta_tags or []) for tag in ["TV", "动画", "动画化"]):
            score += 0.5
        return score

    def _score_related_subject(
        self,
        subject: BangumiSubject,
        relation: BangumiSubjectRelation,
        anime_info: AnimeInfoDict,
    ) -> float:
        base = self._score_subject(anime_info, subject.name_cn or subject.name, subject)
        relation_bonus = self.RELATION_PRIORITY.get(relation.relation or "", 0)
        return base + relation_bonus

    def _is_relevant_related_subject(
        self,
        subject: BangumiSubject,
        relation: BangumiSubjectRelation,
        anime_info: AnimeInfoDict,
    ) -> bool:
        if subject.type != self.ANIME_TYPE:
            return False

        relation_name = relation.relation or ""
        if relation_name in {"角色", "出演", "制作人员"}:
            return False

        score = self._score_subject(anime_info, subject.name_cn or subject.name, subject)
        if score < 2.5 and relation_name not in self.RELATION_PRIORITY:
            return False
        return True

    def _guess_title_from_filename(self, filename: str) -> str:
        stem = re.sub(r"\.[^.]+$", "", filename or "")
        stem = re.sub(r"\[[^\]]*\]", " ", stem)
        stem = re.sub(r"\([^\)]*\)", " ", stem)
        stem = re.sub(r"\b(?:S\d+E?\d*|E\d+|\d{1,3}v\d)\b", " ", stem, flags=re.I)
        stem = re.sub(r"\b(?:WEB-DL|WEBRIP|BDRIP|BLURAY|HEVC|x264|x265|1080p|720p)\b", " ", stem, flags=re.I)
        stem = re.sub(r"\s+", " ", stem)
        return stem.strip(" -_")

    def _strip_season_suffix(self, value: str) -> str:
        cleaned = re.sub(
            r"(?:第\s*\d+\s*季|Season\s*\d+|S\d+)$",
            "",
            value or "",
            flags=re.I,
        )
        return cleaned.strip(" -_:")

    def _append_keyword(self, keywords: list[str], value: str) -> None:
        value = str(value or "").strip()
        if len(value) < 2:
            return
        if value not in keywords:
            keywords.append(value)

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"[\W_]+", "", str(value or "")).casefold()

    def _extract_year(self, value: object) -> int | None:
        text = str(value or "")
        match = re.match(r"^(\d{4})", text)
        if not match:
            return None
        return int(match.group(1))
