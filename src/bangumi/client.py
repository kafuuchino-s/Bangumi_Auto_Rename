from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy
from threading import Lock
from typing import Any, Hashable, TypeVar

import requests

from ..logger import logger
from ..utils.metadata_cache import MetadataCacheMiss, get_or_fetch
from .models import BangumiEpisode, BangumiSubject, BangumiSubjectRelation

_CacheKey = TypeVar('_CacheKey', bound=Hashable)
_CacheValue = TypeVar('_CacheValue')


def _to_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _to_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


class BangumiClient:
    BASE_URL = "https://api.bgm.tv"
    USER_AGENT = "Bangumi-Auto-Rename/1.0"
    TIMEOUT = 8
    MAX_SEARCH_RESULTS = 20
    ANIME_SUBJECT_TYPE = 2
    MAX_RETRIES = 2
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    _CACHE_MAX_SIZE = 128
    _cache_lock = Lock()
    _search_cache: "OrderedDict[tuple[str, int | None], list[BangumiSubject]]" = (
        OrderedDict()
    )
    _subject_cache: "OrderedDict[int, BangumiSubject | None]" = OrderedDict()
    _related_cache: "OrderedDict[int, list[BangumiSubjectRelation]]" = OrderedDict()
    _episodes_cache: "OrderedDict[int, list[BangumiEpisode]]" = OrderedDict()

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": self.USER_AGENT,
            }
        )

    @classmethod
    def _cache_get(
        cls, cache: OrderedDict[_CacheKey, _CacheValue], key: _CacheKey
    ) -> tuple[_CacheValue | None, bool]:
        with cls._cache_lock:
            if key not in cache:
                return None, False
            value = cache.pop(key)
            cache[key] = value
        return deepcopy(value), True

    @classmethod
    def _cache_set(
        cls,
        cache: OrderedDict[_CacheKey, _CacheValue],
        key: _CacheKey,
        value: _CacheValue,
    ) -> None:
        with cls._cache_lock:
            if key in cache:
                cache.pop(key)
            cache[key] = deepcopy(value)
            while len(cache) > cls._CACHE_MAX_SIZE:
                cache.popitem(last=False)

    def search_subjects(
        self,
        keyword: str,
        year: int | None = None,
    ) -> list[BangumiSubject]:
        keyword = str(keyword or "").strip()
        if not keyword:
            return []

        cache_key = (keyword.casefold(), year)
        cached_value, cache_hit = self._cache_get(self._search_cache, cache_key)
        if cache_hit:
            return cached_value or []

        payload: dict[str, Any] = {
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
        subjects: list[BangumiSubject] = []
        for item in items[: self.MAX_SEARCH_RESULTS]:
            normalized = self._normalize_subject(item)
            if normalized:
                subjects.append(normalized)
        self._cache_set(self._search_cache, cache_key, subjects)
        return subjects

    def get_subject(self, subject_id: int) -> BangumiSubject | None:
        cached_value, cache_hit = self._cache_get(self._subject_cache, subject_id)
        if cache_hit:
            return cached_value

        data = self._request_json("get", f"/v0/subjects/{subject_id}")
        if not isinstance(data, dict):
            return None
        subject = self._normalize_subject(data)
        self._cache_set(self._subject_cache, subject_id, subject)
        return subject

    def get_related_subjects(self, subject_id: int) -> list[BangumiSubjectRelation]:
        cached_value, cache_hit = self._cache_get(self._related_cache, subject_id)
        if cache_hit:
            return cached_value or []

        data = self._request_json("get", f"/v0/subjects/{subject_id}/subjects")
        if not isinstance(data, list):
            return []

        relations: list[BangumiSubjectRelation] = []
        for item in data:
            normalized = self._normalize_relation(item)
            if normalized:
                relations.append(normalized)
        self._cache_set(self._related_cache, subject_id, relations)
        return relations

    def get_episodes(self, subject_id: int) -> list[BangumiEpisode]:
        cached_value, cache_hit = self._cache_get(self._episodes_cache, subject_id)
        if cache_hit:
            return cached_value or []

        all_items: list[BangumiEpisode] = []
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
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.BASE_URL}{path}"

        def fetch() -> Any:
            return self._request_json_uncached(
                method=method,
                path=path,
                params=params,
                json=json,
            )

        try:
            return get_or_fetch(
                provider='bangumi',
                endpoint=path.strip('/') or 'root',
                params={'method': method.upper(), **(params or {})},
                body=json,
                fetcher=fetch,
            )
        except MetadataCacheMiss:
            logger.warning(f"[Bangumi] 元数据缓存未命中: {path}")
            return None

    def _request_json_uncached(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
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

    def _normalize_subject(self, payload: dict[str, Any]) -> BangumiSubject | None:
        if not isinstance(payload, dict):
            return None

        try:
            rating_value = payload.get("rating")
            rating = rating_value if isinstance(rating_value, dict) else {}
            tags_value = payload.get("tags")
            tags = tags_value if isinstance(tags_value, list) else []
            infobox_value = payload.get("infobox")
            infobox = infobox_value if isinstance(infobox_value, list) else []
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
                rating_score=_to_float(rating.get("score")),
                rating_total=int(rating.get("total") or 0),
                rank=_to_int(payload.get("rank")),
                tags=[str(item.get("name") or "") for item in tags if item.get("name")],
                meta_tags=[str(item) for item in (payload.get("meta_tags") or []) if item],
                infobox=[item for item in infobox if isinstance(item, dict)],
            )
        except Exception:
            return None

    def _normalize_relation(
        self, payload: dict[str, Any]
    ) -> BangumiSubjectRelation | None:
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

    def _normalize_episode(self, payload: dict[str, Any]) -> BangumiEpisode | None:
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
