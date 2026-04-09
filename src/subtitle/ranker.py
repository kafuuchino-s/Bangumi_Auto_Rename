from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List

from .providers.base import SubtitleCandidate

_SIMPLIFIED_HINTS = (
    "简体",
    "简中",
    "chs",
    "sc",
    "gb",
    "zh-cn",
    "zh-hans",
)
_TRADITIONAL_HINTS = (
    "繁体",
    "繁中",
    "cht",
    "tc",
    "big5",
    "zh-tw",
    "zh-hant",
)
_BILINGUAL_HINTS = (
    "双语",
    "中日",
    "日中",
    "简日",
    "日简",
    "chs&jpn",
    "cht&jpn",
)
_NON_SUBTITLE_NOISE_HINTS = (
    "ncop",
    "nced",
    "pv",
    "cm",
    "预告",
    "外挂字体",
    "font",
    "字体",
    "fonthelper",
)


@dataclass
class RankedSubtitleCandidate:
    candidate: SubtitleCandidate
    original_index: int
    rule_score: float
    language_hint: str
    media_hint: str
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.original_index,
            "title": self.candidate.title,
            "detail_url": self.candidate.detail_url,
            "source": self.candidate.source,
            "snippet": self.candidate.snippet,
            "language_hint": self.language_hint,
            "media_hint": self.media_hint,
            "rule_score": round(self.rule_score, 2),
            "reasons": self.reasons,
        }


class SubtitleCandidateRanker:
    def rank_candidates(
        self,
        task_data: Dict[str, Any],
        missing_videos: List[Path],
        candidates: List[SubtitleCandidate],
    ) -> List[RankedSubtitleCandidate]:
        ranked: List[RankedSubtitleCandidate] = []
        preferred_language = str(
            task_data.get("subtitle_auto_fetch_preferred_language") or "zh-CN"
        )
        media_type = self._detect_media_type(task_data)
        title_seed = str(task_data.get("tmdb_name") or task_data.get("name") or "").strip()
        year_value = task_data.get("tmdb_year") or task_data.get("year")
        season_id = task_data.get("season_id")
        video_names = [path.name for path in missing_videos]

        for index, candidate in enumerate(candidates):
            score = 0.0
            reasons: List[str] = []
            text = self._candidate_text(candidate)

            title_ratio = self._similarity(title_seed, text)
            if title_ratio > 0:
                title_score = round(title_ratio * 100, 2)
                score += title_score
                reasons.append(f"标题相似度 +{title_score:.2f}")

            language_hint, language_score = self._language_score(text, preferred_language)
            if language_score:
                score += language_score
                reasons.append(f"语言偏好({language_hint}) +{language_score:.2f}")

            media_hint, media_score = self._media_score(
                media_type,
                text,
                season_id,
                year_value,
                video_names,
            )
            if media_score:
                score += media_score
                reasons.append(f"媒体匹配({media_hint}) +{media_score:.2f}")

            penalty = self._noise_penalty(text)
            if penalty:
                score -= penalty
                reasons.append(f"噪音关键词 -{penalty:.2f}")

            ranked.append(
                RankedSubtitleCandidate(
                    candidate=candidate,
                    original_index=index,
                    rule_score=score,
                    language_hint=language_hint,
                    media_hint=media_hint,
                    reasons=reasons,
                )
            )

        ranked.sort(
            key=lambda item: (item.rule_score, -item.original_index),
            reverse=True,
        )
        return ranked

    def shortlist(
        self,
        ranked_candidates: List[RankedSubtitleCandidate],
        limit: int = 5,
    ) -> List[RankedSubtitleCandidate]:
        if limit <= 0:
            return ranked_candidates
        return ranked_candidates[:limit]

    def _detect_media_type(self, task_data: Dict[str, Any]) -> str:
        if task_data.get("is_movie"):
            return "movie"
        media_type = str(task_data.get("tmdb_media_type") or "").strip().lower()
        if media_type == "movie":
            return "movie"
        return "tv"

    def _candidate_text(self, candidate: SubtitleCandidate) -> str:
        return " ".join(
            part.strip()
            for part in [candidate.title, candidate.snippet or ""]
            if part and part.strip()
        ).lower()

    def _similarity(self, title_seed: str, candidate_text: str) -> float:
        if not title_seed or not candidate_text:
            return 0.0

        normalized_title = self._normalize_text(title_seed)
        normalized_candidate = self._normalize_text(candidate_text)
        if not normalized_title or not normalized_candidate:
            return 0.0
        if normalized_title in normalized_candidate:
            return 1.0
        return SequenceMatcher(None, normalized_title, normalized_candidate).ratio()

    def _language_score(self, text: str, preferred_language: str) -> tuple[str, float]:
        simplified = any(hint in text for hint in _SIMPLIFIED_HINTS)
        traditional = any(hint in text for hint in _TRADITIONAL_HINTS)
        bilingual = any(hint in text for hint in _BILINGUAL_HINTS)

        if preferred_language == "zh-CN":
            if simplified and bilingual:
                return ("zh-CN bilingual", 50.0)
            if simplified:
                return ("zh-CN", 45.0)
            if bilingual:
                return ("bilingual", 35.0)
            if traditional:
                return ("zh-TW", 15.0)
            if "字幕" in text or "sub" in text:
                return ("unknown", 8.0)
            return ("unknown", 0.0)

        if preferred_language == "zh-TW":
            if traditional:
                return ("zh-TW", 45.0)
            if simplified and bilingual:
                return ("zh-CN bilingual", 32.0)
            if simplified:
                return ("zh-CN", 12.0)
            return ("unknown", 0.0)

        if simplified and bilingual:
            return ("zh-CN bilingual", 40.0)
        if simplified:
            return ("zh-CN", 36.0)
        if traditional:
            return ("zh-TW", 30.0)
        return ("unknown", 0.0)

    def _media_score(
        self,
        media_type: str,
        text: str,
        season_id: Any,
        year_value: Any,
        video_names: List[str],
    ) -> tuple[str, float]:
        if media_type == "movie":
            score = 0.0
            if year_value and str(year_value) in text:
                score += 15.0
            if re.search(r"\bs\d{1,2}\b", text) or re.search(r"\be\d{1,3}\b", text):
                score -= 20.0
            if any(keyword in text for keyword in ("movie", "剧场版", "電影", "电影")):
                score += 10.0
            return ("movie", score)

        score = 0.0
        if isinstance(season_id, int) and season_id > 0:
            season_tokens = (
                f"s{season_id:02d}",
                f"season {season_id}",
                f"season{season_id}",
            )
            if any(token in text for token in season_tokens):
                score += 20.0
        if any(self._has_episode_marker(Path(name).stem, text) for name in video_names):
            score += 8.0
        if any(keyword in text for keyword in ("batch", "合集", "complete", "全集")):
            score += 4.0
        return ("tv", score)

    def _has_episode_marker(self, video_stem: str, candidate_text: str) -> bool:
        match = re.search(r"S(\d{2})E(\d{2,3})", video_stem, re.IGNORECASE)
        if match:
            season, episode = match.groups()
            return f"s{season}e{episode}" in candidate_text
        return False

    def _noise_penalty(self, text: str) -> float:
        penalty = 0.0
        for hint in _NON_SUBTITLE_NOISE_HINTS:
            if hint in text:
                penalty += 25.0
        return penalty

    def _normalize_text(self, value: str) -> str:
        lowered = str(value).lower()
        lowered = re.sub(r"[\[\](){}]", " ", lowered)
        lowered = re.sub(r"[^\w\u4e00-\u9fff]+", " ", lowered)
        return re.sub(r"\s+", " ", lowered).strip()
