from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..logger import logger
from .models import BangumiEpisode, BangumiSubject, BangumiSubjectRelation


class BangumiClient:
    BASE_URL = "https://api.bgm.tv"
    USER_AGENT = "Bangumi-Auto-Rename/1.0"
    TIMEOUT = 8
    MAX_SEARCH_RESULTS = 8
    ANIME_SUBJECT_TYPE = 2
    MAX_RETRIES = 2
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    _CACHE_MAX_SIZE = 128
    _cache_lock = Lock()
    _search_cache: "OrderedDict[Tuple[str, Optional[int]], List[BangumiSubject]]" = (
        OrderedDict()
    )
    _subject_cache: "OrderedDict[int, Optional[BangumiSubject]]" = OrderedDict()
    _related_cache: "OrderedDict[int, List[BangumiSubjectRelation]]" = OrderedDict()
    _episodes_cache: "OrderedDict[int, List[BangumiEpisode]]" = OrderedDict()

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": self.USER_AGENT,
            }
        )

    @classmethod
    def _cache_get(cls, cache: OrderedDict, key: Any) -> Tuple[Any, bool]:
        with cls._cache_lock:
            if key not in cache:
                return None, False
            value = cache.pop(key)
            cache[key] = value
        return deepcopy(value), True

    @classmethod
    def _cache_set(cls, cache: OrderedDict, key: Any, value: Any) -> None:
        with cls._cache_lock:
            if key in cache:
                cache.pop(key)
            cache[key] = deepcopy(value)
            while len(cache) > cls._CACHE_MAX_SIZE:
                cache.popitem(last=False)

    def search_subjects(
        self,
        keyword: str,
        year: Optional[int] = None,
    ) -> List[BangumiSubject]:
        keyword = str(keyword or "").strip()
        if not keyword:
            return []

        cache_key = (keyword.casefold(), year)
        cached_value, cache_hit = self._cache_get(self._search_cache, cache_key)
        if cache_hit:
            return cached_value

        payload: Dict[str, Any] = {
            "keyword": keyword,
            "sort": "rank",
            "filter": {"type": [self.ANIME_SUBJECT_TYPE]},
        }
        if year:
            payload["filter"]["air_date"] = [
                f">={year}-01-01",
                f"<{year + 1}-01-01",
            ]

        data = self._request_json(
            method="post",
            path="/v0/search/subjects",
            json=payload,
        )
        if not isinstance(data, dict):
            return []
        items = data.get("data", []) if isinstance(data, dict) else []
        subjects: List[BangumiSubject] = []
        for item in items[: self.MAX_SEARCH_RESULTS]:
            normalized = self._normalize_subject(item)
            if normalized:
                subjects.append(normalized)
        self._cache_set(self._search_cache, cache_key, subjects)
        return subjects

    def get_subject(self, subject_id: int) -> Optional[BangumiSubject]:
        cached_value, cache_hit = self._cache_get(self._subject_cache, subject_id)
        if cache_hit:
            return cached_value

        data = self._request_json("get", f"/v0/subjects/{subject_id}")
        if not isinstance(data, dict):
            return None
        subject = self._normalize_subject(data)
        self._cache_set(self._subject_cache, subject_id, subject)
        return subject

    def get_related_subjects(self, subject_id: int) -> List[BangumiSubjectRelation]:
        cached_value, cache_hit = self._cache_get(self._related_cache, subject_id)
        if cache_hit:
            return cached_value

        data = self._request_json("get", f"/v0/subjects/{subject_id}/subjects")
        if not isinstance(data, list):
            return []

        relations: List[BangumiSubjectRelation] = []
        for item in data:
            normalized = self._normalize_relation(item)
            if normalized:
                relations.append(normalized)
        self._cache_set(self._related_cache, subject_id, relations)
        return relations

    def get_episodes(self, subject_id: int) -> List[BangumiEpisode]:
        cached_value, cache_hit = self._cache_get(self._episodes_cache, subject_id)
        if cache_hit:
            return cached_value

        all_items: List[BangumiEpisode] = []
        offset = 0
        limit = 100

        while True:
            data = self._request_json(
                "get",
                "/v0/episodes",
                params={
                    "subject_id": subject_id,
                    "limit": limit,
                    "offset": offset,
                },
            )
            if not isinstance(data, dict):
                break

            items = data.get("data", []) or []
            for item in items:
                normalized = self._normalize_episode(item)
                if normalized:
                    all_items.append(normalized)

            total = int(data.get("total") or 0)
            fetched = len(items)
            offset += fetched
            if fetched <= 0 or (total and offset >= total):
                break

        self._cache_set(self._episodes_cache, subject_id, all_items)
        return all_items

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.BASE_URL}{path}"
        attempts = self.MAX_RETRIES + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=json,
                    timeout=self.TIMEOUT,
                )
                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    raise requests.HTTPError(
                        f"{response.status_code} Server Error",
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                should_retry = (
                    attempt < attempts
                    and (
                        status_code in self.RETRYABLE_STATUS_CODES
                        or isinstance(
                            exc,
                            (
                                requests.Timeout,
                                requests.ConnectionError,
                            ),
                        )
                    )
                )
                if should_retry:
                    logger.warning(
                        f"[Bangumi] 请求失败，准备重试({attempt}/{attempts - 1}): {path} - {exc}"
                    )
                    time.sleep(0.4 * attempt)
                    continue
                logger.warning(f"[Bangumi] 请求失败: {path} - {exc}")
                return None

    def _normalize_subject(self, payload: Dict[str, Any]) -> Optional[BangumiSubject]:
        if not isinstance(payload, dict):
            return None

        try:
            rating = payload.get("rating") or {}
            tags = payload.get("tags") or []
            return BangumiSubject(
                id=int(payload.get("id") or 0),
                type=int(payload.get("type") or self.ANIME_SUBJECT_TYPE),
                name=str(payload.get("name") or ""),
                name_cn=str(payload.get("name_cn") or ""),
                date=str(payload.get("date") or ""),
                summary=str(payload.get("summary") or ""),
                platform=str(payload.get("platform") or ""),
                total_episodes=int(payload.get("total_episodes") or 0),
                eps=int(payload.get("eps") or 0),
                rating_score=(
                    float(rating.get("score"))
                    if rating.get("score") is not None
                    else None
                ),
                rating_total=int(rating.get("total") or 0),
                rank=(
                    int(payload.get("rank"))
                    if payload.get("rank") is not None
                    else None
                ),
                tags=[str(item.get("name") or "") for item in tags if item.get("name")],
                meta_tags=[str(item) for item in (payload.get("meta_tags") or []) if item],
            )
        except Exception:
            return None

    def _normalize_relation(
        self, payload: Dict[str, Any]
    ) -> Optional[BangumiSubjectRelation]:
        if not isinstance(payload, dict):
            return None

        try:
            return BangumiSubjectRelation(
                id=int(payload.get("id") or 0),
                type=int(payload.get("type") or self.ANIME_SUBJECT_TYPE),
                relation=str(payload.get("relation") or ""),
                name=str(payload.get("name") or ""),
                name_cn=str(payload.get("name_cn") or ""),
            )
        except Exception:
            return None

    def _normalize_episode(self, payload: Dict[str, Any]) -> Optional[BangumiEpisode]:
        if not isinstance(payload, dict):
            return None

        try:
            ep = payload.get("ep")
            disc = payload.get("disc")
            duration_seconds = payload.get("duration_seconds")
            return BangumiEpisode(
                id=int(payload.get("id") or 0),
                subject_id=int(payload.get("subject_id") or 0),
                type=int(payload.get("type") or 0),
                sort=int(payload.get("sort") or 0),
                ep=int(ep) if ep is not None else None,
                disc=int(disc) if disc is not None else None,
                name=str(payload.get("name") or ""),
                name_cn=str(payload.get("name_cn") or ""),
                airdate=str(payload.get("airdate") or ""),
                duration=str(payload.get("duration") or ""),
                duration_seconds=(
                    int(duration_seconds)
                    if duration_seconds is not None
                    else None
                ),
                desc=str(payload.get("desc") or ""),
            )
        except Exception:
            return None
