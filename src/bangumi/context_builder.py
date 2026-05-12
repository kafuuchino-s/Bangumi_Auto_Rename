from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal, TypedDict, cast

from ..logger import logger
from .client import BangumiClient
from .models import (
    BangumiEpisode,
    BangumiRelationEdge,
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
    relation_to_main: str
    score: float
    distance: int
    parent_subject_id: int
    relation_path: list[dict[str, int | str]]


class BangumiPromptContext(TypedDict, total=False):
    search_keywords: list[str]
    selected_subject_id: int
    selected_subject_reason: str
    subjects: list[object]


class BangumiContextBuilder:
    MAX_KEYWORDS: int = 12
    MAX_MAIN_CANDIDATES: int = 20
    MAX_TOTAL_SUBJECTS: int = 24
    MAX_RELATED_TRAVERSAL_SUBJECTS: int = 48
    MAX_EPISODES_PER_SUBJECT: int = 60
    ANIME_TYPE: int = 2
    SINGLETON_SOURCE_HINTS: frozenset[str] = frozenset({"movie", "ova", "special"})
    RELATION_PRIORITY: dict[str, int] = {
        "续集": 5,
        "前传": 4,
        "番外篇": 4,
        "总集篇": 3,
        "衍生": 3,
        "其他": 2,
        "不同演绎": 1,
    }
    ALLOWED_RELATED_RELATIONS: frozenset[str] = frozenset({"续集", "前传", "番外篇", "不同演绎", "演绎", "总集篇", "衍生"})

    def __init__(self, client: BangumiClient | None = None) -> None:
        self.client: BangumiClient = client or BangumiClient()

    def build_tv_context(
        self,
        anime_info: AnimeInfoDict,
        local_files: list[LocalFileDict],
        local_title_seeds: list[str] | None = None,
    ) -> BangumiPromptContext | None:
        try:
            keywords = self._build_search_keywords(anime_info, local_files, local_title_seeds=local_title_seeds)
            if not keywords:
                return None

            main_candidates = self._collect_main_candidates(anime_info, keywords)
            if not main_candidates:
                return None

            selected = main_candidates[0]
            selected_subject = selected["subject"]
            subject_contexts: list[BangumiSubjectContext] = []
            included_subject_ids: set[int] = set()

            selected_context = self._build_subject_context(
                selected_subject,
                "main",
                selected["score"],
                source_kind="selected",
                relation="",
                distance=0,
                parent_subject_id=None,
                relation_path=[],
            )
            if selected_context.episodes:
                subject_contexts.append(selected_context)
                included_subject_ids.add(selected_subject.id)
            elif self._should_create_singleton_episode(selected_subject, selected_context.source_form_hint, selected_context.relation, selected_context.relation_to_main):
                selected_context.episodes = [self._build_synthetic_singleton_episode(selected_context)]
                subject_contexts.append(selected_context)
                included_subject_ids.add(selected_subject.id)

            related = self._collect_related_subjects(
                selected_subject.id,
                anime_info,
                seen_subject_ids=included_subject_ids,
                limit=max(0, self.MAX_TOTAL_SUBJECTS - len(subject_contexts)),
            )
            for item in related:
                if len(subject_contexts) >= self.MAX_TOTAL_SUBJECTS:
                    break
                if item["subject"].id in included_subject_ids:
                    continue
                context = self._build_subject_context(
                    item["subject"],
                    item["relation_to_main"],
                    item["score"],
                    source_kind="related",
                    relation=item["relation"],
                    distance=item["distance"],
                    parent_subject_id=item["parent_subject_id"],
                    relation_path=[BangumiRelationEdge(**edge) for edge in item["relation_path"]],
                )
                if context.episodes:
                    subject_contexts.append(context)
                    included_subject_ids.add(item["subject"].id)
                elif self._should_create_singleton_episode(context.subject, context.source_form_hint, context.relation, context.relation_to_main):
                    context.episodes = [self._build_synthetic_singleton_episode(context)]
                    subject_contexts.append(context)
                    included_subject_ids.add(item["subject"].id)

            subject_contexts = [ctx for ctx in subject_contexts if ctx.episodes]
            if not subject_contexts:
                return None

            context = BangumiTVContext(
                search_keywords=keywords,
                selected_subject_id=selected_subject.id,
                selected_subject_reason=(
                    f"排序第一的源侧候选: {selected['score']:.2f}, 标题={selected_subject.name_cn or selected_subject.name}"
                ),
                subjects=subject_contexts,
            )
            return cast(BangumiPromptContext, cast(object, context.to_prompt_dict()))
        except Exception as exc:
            logger.warning(f"[Bangumi] 构建上下文失败，回退 TMDB-only: {exc}")
            return None

    def _build_search_keywords(
        self,
        anime_info: AnimeInfoDict,
        local_files: list[LocalFileDict],
        *,
        local_title_seeds: list[str] | None = None,
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
            release_groups, title_cues = self._extract_release_group_and_title_cues(filename)
            for cue in title_cues:
                self._append_keyword(keywords, cue)
                self._append_keyword(keywords, self._strip_season_suffix(cue))
            parent = str(file_info.get('parent_display') or '')
            parent_groups, parent_cues = self._extract_release_group_and_title_cues(parent)
            for cue in parent_cues:
                self._append_keyword(keywords, cue)
                self._append_keyword(keywords, self._strip_season_suffix(cue))
            for group in parent_groups:
                self._append_keyword(keywords, group)

        for seed in local_title_seeds or []:
            self._append_keyword(keywords, str(seed))
            self._append_keyword(keywords, self._strip_season_suffix(str(seed)))

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

            for rank, subject in enumerate(subjects, start=1):
                if subject.id in seen or subject.type != self.ANIME_TYPE:
                    continue
                if rank > 1 and not self._has_main_candidate_title_alignment(keyword, anime_info, subject):
                    continue
                seen.add(subject.id)
                subject.search_keyword = keyword
                subject.search_rank = rank
                score = self._score_subject(anime_info, keyword, subject)
                scored.append({"subject": subject, "score": score})

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[: self.MAX_MAIN_CANDIDATES]

    def _collect_related_subjects(
        self,
        subject_id: int,
        anime_info: AnimeInfoDict,
        *,
        seen_subject_ids: set[int] | None = None,
        limit: int | None = None,
    ) -> list[RelatedCandidate]:
        max_results = self.MAX_TOTAL_SUBJECTS if limit is None else max(0, min(limit, self.MAX_TOTAL_SUBJECTS))
        if max_results <= 0:
            return []

        seen: set[int] = set(seen_subject_ids or set())
        seen.add(subject_id)
        queue: list[dict[str, object]] = [{"subject_id": subject_id, "distance": 0, "relation_path": []}]
        scored_by_id: dict[int, RelatedCandidate] = {}
        traversed = 0

        while queue and len(scored_by_id) < max_results and traversed < self.MAX_RELATED_TRAVERSAL_SUBJECTS:
            current = queue.pop(0)
            current_subject_id = int(current["subject_id"])
            current_distance = int(current["distance"])
            current_path = list(cast(list[dict[str, int | str]], current["relation_path"]))
            traversed += 1
            current_subject = self.client.get_subject(current_subject_id)
            if not current_subject:
                continue
            relations = self.client.get_related_subjects(current_subject_id)

            for relation in relations:
                if len(scored_by_id) >= max_results:
                    break
                if relation.id in seen or relation.type != self.ANIME_TYPE:
                    continue
                relation_name = (relation.relation or "").strip()
                if relation_name not in self.ALLOWED_RELATED_RELATIONS:
                    continue
                seen.add(relation.id)

                full_subject = self.client.get_subject(relation.id)
                if not full_subject:
                    continue
                if not self._is_relevant_related_subject(full_subject, relation, anime_info):
                    continue

                score = self._score_related_subject(full_subject, relation, anime_info)
                scored_by_id[full_subject.id] = {
                    "subject": full_subject,
                    "relation": relation.relation or "related",
                    "relation_to_main": relation.relation or "related",
                    "score": score,
                    "distance": current_distance + 1,
                    "parent_subject_id": current_subject_id,
                    "relation_path": [*current_path, self._make_relation_edge(current_subject_id, current_subject, relation.relation or "related", full_subject)],
                }
                queue.append({"subject_id": full_subject.id, "distance": current_distance + 1, "relation_path": [*current_path, self._make_relation_edge(current_subject_id, current_subject, relation.relation or "related", full_subject)]})

        scored = list(scored_by_id.values())
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:max_results]

    def _build_subject_context(
        self,
        subject: BangumiSubject,
        relation_to_main: str,
        score: float,
        *,
        source_kind: Literal["selected", "main_candidate", "related"],
        relation: str,
        distance: int | None,
        parent_subject_id: int | None,
        relation_path: list[BangumiRelationEdge],
    ) -> BangumiSubjectContext:
        episodes = self.client.get_episodes(subject.id)
        compact_episodes = self._compact_episodes(episodes)
        source_form_hint, source_form_evidence = self._derive_source_form_hint(
            subject,
            compact_episodes,
        )
        if self._should_create_singleton_episode(subject, source_form_hint, relation, relation_to_main) and len(compact_episodes) == 1:
            compact_episodes = [self._singletonify_episode(subject, compact_episodes[0], source_form_hint, relation, relation_to_main)]
        return BangumiSubjectContext(
            subject=subject,
            source_kind=source_kind,
            relation_to_main=relation_to_main,
            relation=relation,
            distance=distance,
            parent_subject_id=parent_subject_id,
            relation_path=relation_path,
            score=round(score, 3),
            source_form_hint=source_form_hint,
            source_form_evidence=source_form_evidence,
            episodes=compact_episodes,
            subject_id=subject.id,
            name=subject.name,
            name_cn=subject.name_cn,
            title=subject.name_cn or subject.name,
            platform=subject.platform,
            date=subject.date,
            source_role=source_kind,
            relation_path_text=self._format_relation_path_text(relation_path),
        )

    def _compact_episodes(self, episodes: list[BangumiEpisode]) -> list[BangumiEpisode]:
        filtered = [ep for ep in episodes if ep.sort >= 0]
        filtered.sort(key=lambda ep: (ep.sort, ep.id))
        return filtered[: self.MAX_EPISODES_PER_SUBJECT]

    def _build_synthetic_singleton_episode(self, context: BangumiSubjectContext) -> BangumiEpisode:
        subject = context.subject
        return BangumiEpisode(
            id=subject.id * 10 + 1,
            subject_id=subject.id,
            type=0,
            sort=1,
            ep=1,
            synthetic=True,
            synthetic_reason="subject_singleton_no_episode_items",
            subject_level_target=True,
            kind="subject_singleton",
            title=subject.name_cn or subject.name,
            name=subject.name,
            name_cn=subject.name_cn,
            source_form_hint=context.source_form_hint,
            relation=context.relation,
            relation_to_main=context.relation_to_main,
            source_role=context.source_role,
        )

    def _singletonify_episode(
        self,
        subject: BangumiSubject,
        episode: BangumiEpisode,
        source_form_hint: str,
        relation: str,
        relation_to_main: str,
    ) -> BangumiEpisode:
        if episode.synthetic and episode.kind == "subject_singleton":
            return episode
        title_fallback = subject.name_cn or subject.name
        name_fallback = subject.name or subject.name_cn
        payload = episode.model_dump()
        payload.update({
            "synthetic": True,
            "synthetic_reason": episode.synthetic_reason or "subject_level_target",
            "subject_level_target": True,
            "kind": "subject_singleton",
            "title": episode.title or title_fallback,
            "name": episode.name or name_fallback,
            "name_cn": episode.name_cn or title_fallback,
            "source_form_hint": source_form_hint,
            "relation": relation,
            "relation_to_main": relation_to_main,
        })
        return BangumiEpisode(**payload)

    def _should_create_singleton_episode(
        self,
        subject: BangumiSubject,
        source_form_hint: Literal["tv_series", "movie", "ova", "web", "special", "unknown"],
        relation: str,
        relation_to_main: str,
    ) -> bool:
        if subject.type != self.ANIME_TYPE:
            return False
        if source_form_hint == "tv_series" and max(subject.total_episodes, subject.eps) > 1:
            return False
        allowed_relations = {"总集篇", "番外篇", "演绎", "不同演绎", "衍生"}
        if source_form_hint not in self.SINGLETON_SOURCE_HINTS and relation not in allowed_relations and relation_to_main not in allowed_relations:
            return False
        return True

    def _derive_source_form_hint(
        self,
        subject: BangumiSubject,
        episodes: list[BangumiEpisode],
    ) -> tuple[Literal["tv_series", "movie", "ova", "web", "special", "unknown"], list[str]]:
        """Summarize Bangumi's source-side subject form for AI evidence.

        This is deliberately not a TMDB media-type decision.  It only exposes
        Bangumi's own platform/meta/episode-shape facts so the Case Agent can
        reason about Local -> Bangumi mapping without making a TMDB decision.
        """

        evidence: list[str] = []
        platform = str(subject.platform or '').strip()
        platform_key = platform.casefold()
        if platform:
            evidence.append(f"platform={platform}")

        tags = [str(item or '').strip() for item in [*subject.meta_tags, *subject.tags] if str(item or '').strip()]
        tag_keys = {tag.casefold() for tag in tags}
        for tag in tags[:6]:
            evidence.append(f"tag={tag}")

        regular_count = sum(1 for episode in episodes if episode.type == 0)
        special_count = sum(1 for episode in episodes if episode.type != 0)
        if regular_count or special_count:
            evidence.append(f"episode_structure=regular:{regular_count},special:{special_count}")

        text = ' '.join([platform_key, *tag_keys, subject.name.casefold(), subject.name_cn.casefold()])
        if any(token in text for token in ('剧场版', '劇場版', '映画', 'movie', 'the movie')):
            return 'movie', evidence[:10]
        if any(token in text for token in ('ova', 'oad')):
            return 'ova', evidence[:10]
        if any(token in text for token in ('web', '配信')):
            return 'web', evidence[:10]
        if any(token in text for token in ('sp', 'special', '番外', '特别篇', '特別編')):
            return 'special', evidence[:10]
        if platform_key == 'tv' or 'tv' in tag_keys or regular_count >= 2:
            return 'tv_series', evidence[:10]
        return 'unknown', evidence[:10]

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
        if relation_name in {"", "其他", "角色", "出演", "制作人员", "角色出演", "游戏", "书籍", "音乐"}:
            return False

        return True

    def _make_relation_edge(
        self,
        from_subject_id: int,
        from_subject: BangumiSubject,
        relation: str,
        to_subject: BangumiSubject,
    ) -> dict[str, int | str]:
        return {
            "from_subject_id": from_subject_id,
            "from_subject_name": from_subject.name,
            "from_subject_name_cn": from_subject.name_cn,
            "relation": relation,
            "to_subject_id": to_subject.id,
            "to_subject_name": to_subject.name,
            "to_subject_name_cn": to_subject.name_cn,
        }

    def _format_relation_path_text(self, relation_path: list[BangumiRelationEdge]) -> str:
        if not relation_path:
            return ""
        parts: list[str] = []
        for edge in relation_path:
            left = edge.from_subject_name_cn or edge.from_subject_name or str(edge.from_subject_id)
            right = edge.to_subject_name_cn or edge.to_subject_name or str(edge.to_subject_id)
            relation = edge.relation or "相关"
            parts.append(f"{left} --{relation}--> {right}")
        return " | ".join(parts)

    def _has_main_candidate_title_alignment(
        self,
        keyword: str,
        anime_info: AnimeInfoDict,
        subject: BangumiSubject,
    ) -> bool:
        query_texts = [
            keyword,
            anime_info.get("name"),
            anime_info.get("original_name"),
            anime_info.get("original_title"),
            anime_info.get("name_cn"),
        ]
        subject_texts = [subject.name, subject.name_cn]
        normalized_queries = [self._normalize_title_alignment_text(value) for value in query_texts if str(value or "").strip()]
        normalized_subjects = [self._normalize_title_alignment_text(value) for value in subject_texts if str(value or "").strip()]

        for query in normalized_queries:
            if not query:
                continue
            for candidate in normalized_subjects:
                if not candidate:
                    continue
                if query == candidate:
                    return True
                if len(query) >= 6 and len(candidate) >= 6 and (query in candidate or candidate in query):
                    return True
                if SequenceMatcher(None, query, candidate).ratio() >= 0.82:
                    return True
                if self._shared_alignment_token(query, candidate):
                    return True
        return False

    def _shared_alignment_token(self, left: str, right: str) -> bool:
        left_tokens = self._alignment_tokens(left)
        right_tokens = self._alignment_tokens(right)
        if not left_tokens or not right_tokens:
            return False
        shared = left_tokens.intersection(right_tokens)
        return any(len(token) >= 4 for token in shared)

    def _alignment_tokens(self, value: str) -> set[str]:
        normalized = self._normalize_title_alignment_text(value)
        if not normalized:
            return set()
        return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", normalized))

    def _normalize_title_alignment_text(self, value: object) -> str:
        text = self._normalize_text(str(value or ""))
        return text

    def _guess_title_from_filename(self, filename: str) -> str:
        stem = re.sub(r"\.[^.]+$", "", filename or "")
        stem = re.sub(r"\[[^\]]*\]", " ", stem)
        stem = re.sub(r"\([^\)]*\)", " ", stem)
        stem = re.sub(r"\b(?:S\d+E?\d*|E\d+|\d{1,3}v\d)\b", " ", stem, flags=re.I)
        stem = re.sub(r"\b(?:WEB-DL|WEBRIP|BDRIP|BLURAY|HEVC|x264|x265|1080p|720p)\b", " ", stem, flags=re.I)
        stem = re.sub(r"\s+", " ", stem)
        return stem.strip(" -_")

    def _extract_release_group_and_title_cues(self, filename: str) -> tuple[list[str], list[str]]:
        stem = re.sub(r"\.[^.]+$", "", filename or "")
        match = re.match(r'^\[(?P<group>[^\[\]]+)\]\s*(?P<title>.+)$', stem)
        if not match:
            title = self._guess_title_from_filename(filename)
            return [], [title] if title else []
        title = str(match.group('title') or '').strip()
        return ([str(match.group('group') or '').strip()] if match.group('group') else []), ([title] if title else [])

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
