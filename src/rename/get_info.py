import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from difflib import SequenceMatcher
from threading import Lock
from time import sleep
from typing import Any, Dict, List, Optional, Set, Tuple, TypeAlias, cast

import tmdbsimple as tmdb

from ..logger import logger
from ..config.config_manager import cm
from .cleaner import (
    build_movie_search_queries,
    build_tv_search_queries,
    is_chinese_percentage_sufficient,
)

TMDBDict: TypeAlias = dict[str, Any]
TMDBList: TypeAlias = list[TMDBDict]
SeasonPayload: TypeAlias = dict[str, Any]


class Search:
    _CACHE_MAX_SIZE = 128
    _cache_lock = Lock()
    _tv_search_cache: "OrderedDict[Tuple[Any, ...], Optional[List[Dict[str, Any]]]]" = (
        OrderedDict()
    )
    _movie_search_cache: "OrderedDict[Tuple[Any, ...], Optional[List[Dict[str, Any]]]]" = (
        OrderedDict()
    )
    _tv_info_cache: "OrderedDict[Tuple[int, str], Optional[Dict[str, Any]]]" = (
        OrderedDict()
    )
    _movie_info_cache: "OrderedDict[Tuple[int, str], Optional[Dict[str, Any]]]" = (
        OrderedDict()
    )
    _season_info_cache: "OrderedDict[Tuple[int, int, str], Optional[Dict[str, Any]]]" = (
        OrderedDict()
    )
    _hydrated_tv_cache: "OrderedDict[Tuple[int, str], Dict[str, Any]]" = OrderedDict()

    def __init__(self) -> None:
        self.TMDB_KEY = cm.get_config('api_key')
        tmdb.API_KEY = self.TMDB_KEY

    @classmethod
    def _cache_get(cls, cache: OrderedDict[tuple[Any, ...], Any], key: tuple[Any, ...]) -> Any:
        with cls._cache_lock:
            if key not in cache:
                return None, False
            value = cache.pop(key)
            cache[key] = value
        return deepcopy(value), True

    @classmethod
    def _cache_set(
        cls,
        cache: OrderedDict[tuple[Any, ...], Any],
        key: tuple[Any, ...],
        value: Any,
    ) -> None:
        with cls._cache_lock:
            if key in cache:
                cache.pop(key)
            cache[key] = deepcopy(value)
            while len(cache) > cls._CACHE_MAX_SIZE:
                cache.popitem(last=False)

    @staticmethod
    def _normalize_cache_text(value: Optional[str]) -> str:
        return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()

    @classmethod
    def _normalize_queries_key(
        cls,
        queries: Optional[List[str]],
    ) -> Tuple[str, ...]:
        return tuple(cls._normalize_cache_text(query) for query in (queries or []))

    @staticmethod
    def _has_detailed_episodes(season: Dict[str, Any]) -> bool:
        if season.get('_episodes_loaded') is True:
            return True
        episodes = season.get('episodes')
        return isinstance(episodes, list) and len(episodes) > 0

    @classmethod
    def _is_tv_info_hydrated(cls, tv_info: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(tv_info, dict):
            return False
        seasons = tv_info.get('seasons')
        if not isinstance(seasons, list) or not seasons:
            return False
        valid_seasons = [
            season
            for season in seasons
            if isinstance(season, dict) and season.get('season_number') is not None
        ]
        if not valid_seasons:
            return False
        return all(cls._has_detailed_episodes(season) for season in valid_seasons)

    def _get_search_languages(self, query: str) -> List[str]:
        """根据查询内容推断最接近的搜索语言顺序"""
        text = query or ''
        has_kana = bool(re.search(r'[\u3040-\u30ff\u31f0-\u31ff]', text))
        has_cjk = bool(re.search(r'[\u4e00-\u9fff]', text))
        has_latin = bool(re.search(r'[A-Za-z]', text))

        if has_kana:
            return ['ja-JP', 'zh-CN', 'en-US']
        if has_cjk and not has_latin:
            return ['zh-CN', 'ja-JP', 'en-US']
        if has_latin and not has_cjk:
            return ['en-US', 'ja-JP', 'zh-CN']
        if has_latin and has_cjk:
            if is_chinese_percentage_sufficient(text):
                return ['zh-CN', 'ja-JP', 'en-US']
            return ['en-US', 'ja-JP', 'zh-CN']
        return ['zh-CN', 'ja-JP', 'en-US']

    def _search_movie_multi_language(
        self,
        query: str,
        year: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """按语言优先级搜索电影候选"""
        languages = self._get_search_languages(query)
        logger.debug(
            f'[电影搜索] 语言顺序: {languages}, query={query}, year={year}'
        )

        ranked_candidates: Dict[int, Dict[str, Any]] = {}
        for index, language in enumerate(languages):
            search = tmdb.Search()
            search.movie(
                query=query,
                language=language,
                year=year if year else None,
            )
            results = search.__dict__['results']
            if not results:
                continue

            if index > 0:
                logger.info(
                    f'[电影搜索] 语言回退命中: {language}, query={query}'
                )

            for candidate in results:
                movie_id = candidate.get('id')
                if not isinstance(movie_id, int):
                    continue

                current = ranked_candidates.get(movie_id)
                candidate_copy = dict(candidate)
                if current:
                    matched_languages = list(current.get('_matched_languages', []))
                    if language not in matched_languages:
                        matched_languages.append(language)
                    current['_matched_languages'] = matched_languages
                    for key, value in candidate_copy.items():
                        if current.get(key) in (None, '', []) and value not in (None, '', []):
                            current[key] = value
                    continue

                candidate_copy['_matched_language'] = language
                candidate_copy['_matched_languages'] = [language]
                ranked_candidates[movie_id] = candidate_copy

        if not ranked_candidates:
            return None

        return list(ranked_candidates.values())

    @staticmethod
    def _has_movie_title_signal(text: str) -> bool:
        normalized = re.sub(r'[\s._\-]+', ' ', text or '').casefold()
        return bool(
            re.search(
                r'(剧场版|劇場版|gekijouban|\bthe movie\b|\bmovie\b|\bfilm\b|\btheatrical\b)',
                normalized,
                flags=re.IGNORECASE,
            )
        )

    def _search_tv_multi_language(
        self,
        query: str,
        year: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """按语言优先级搜索电视剧候选"""
        languages = self._get_search_languages(query)
        logger.debug(
            f'[电视剧搜索] 语言顺序: {languages}, query={query}, year={year}'
        )

        ranked_candidates: Dict[int, Dict[str, Any]] = {}
        for index, language in enumerate(languages):
            search = tmdb.Search()
            search.tv(
                query=query,
                language=language,
                first_air_date_year=year if year and year != 0 else None,
            )
            results = search.__dict__['results']
            if not results:
                continue

            if index > 0:
                logger.info(
                    f'[电视剧搜索] 语言回退命中: {language}, query={query}'
                )

            for candidate in results:
                tv_id = candidate.get('id')
                if not isinstance(tv_id, int):
                    continue
                current = ranked_candidates.get(tv_id)
                candidate_copy = dict(candidate)
                candidate_copy['_matched_language'] = language
                if not current:
                    ranked_candidates[tv_id] = candidate_copy

        if not ranked_candidates:
            return None

        return list(ranked_candidates.values())

    def _normalize_tv_score_text(self, text: str) -> str:
        normalized = re.sub(r'[：:·•|｜/\\\-\._]+', ' ', text or '')
        normalized = re.sub(r'\s+', ' ', normalized)
        return normalized.strip().casefold()

    def _extract_tv_candidate_year(self, candidate: Dict[str, Any]) -> Optional[int]:
        first_air_date = candidate.get('first_air_date', '')
        if not isinstance(first_air_date, str) or not re.match(r'^\d{4}', first_air_date):
            return None
        return int(first_air_date[:4])

    def _extract_season_tokens(self, text: str) -> Set[str]:
        normalized = self._normalize_tv_score_text(text)
        tokens: Set[str] = set()

        roman_map = {
            ' ii ': 's2',
            ' iii ': 's3',
            ' iv ': 's4',
            ' v ': 's5',
        }
        padded = f' {normalized} '
        for raw, mapped in roman_map.items():
            if raw in padded:
                tokens.add(mapped)

        patterns = [
            (r'\b(?:season|s)\s*0*([2-9])\b', 's{}'),
            (r'\b([2-9])(st|nd|rd|th)\s+season\b', 's{}'),
            (r'第\s*([二三四五六七八九2-9])\s*季', 's{}'),
        ]
        cn_map = {'二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
        for pattern, template in patterns:
            for match in re.finditer(pattern, normalized, re.IGNORECASE):
                raw_value = match.group(1)
                if raw_value in cn_map:
                    season_num = cn_map[raw_value]
                else:
                    season_num = int(raw_value)
                tokens.add(template.format(season_num))

        keyword_map = {
            'zoku shou': 's2',
            '续篇': 's2',
            '続編': 's2',
            'second': 's2',
            '2nd': 's2',
            'ii': 's2',
            'third': 's3',
            '3rd': 's3',
            'iii': 's3',
            'fourth': 's4',
            '4th': 's4',
            'iv': 's4',
            'fifth': 's5',
            '5th': 's5',
            'v': 's5',
            'okawari': 's2',
            'okaeri': 's3',
        }
        for keyword, token in keyword_map.items():
            if re.search(rf'(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])', normalized):
                tokens.add(token)

        return tokens

    def _extract_franchise_root_tokens(self, text: str) -> Set[str]:
        normalized = self._normalize_tv_score_text(text)
        if not normalized:
            return set()

        sequel_patterns = [
            r'\bseason\s*[2-9]\b',
            r'\b[2-9](?:st|nd|rd|th)\s+season\b',
            r'\b(?:ii|iii|iv|v)\b',
            r'第\s*[二三四五六七八九2-9]\s*季',
            r'\b2nd\b',
            r'\bsecond\b',
            r'\bthird\b',
            r'\bfourth\b',
            r'\bfifth\b',
            r'\bzoku\s+shou\b',
            r'\bokawari\b',
            r'\bokaeri\b',
            r'\bside\s+story\b',
            r'\bgaiden\b',
            r'\bspinoff\b',
            r'\bspecials?\b',
            r'\bova\b',
            r'\boad\b',
            r'\bmovie\b',
            r'\bthe\s+movie\b',
            r'\bfilm\b',
            r'\brecord\b',
        ]
        for pattern in sequel_patterns:
            normalized = re.sub(pattern, ' ', normalized, flags=re.IGNORECASE)

        tokens = {
            token
            for token in normalized.split()
            if token and len(token) >= 3
        }
        return tokens

    def _extract_numeric_identity_tokens(self, text: str) -> Set[str]:
        normalized = self._normalize_tv_score_text(text)
        return {
            match.group(0)
            for match in re.finditer(r'(?<!\d)\d{3,4}(?!\d)', normalized)
        }

    def _collect_spinoff_tokens(self, text: str) -> Set[str]:
        normalized = self._normalize_tv_score_text(text)
        token_patterns = {
            'record': r'\brecord\b',
            'gaiden': r'\bgaiden\b',
            'side_story': r'\bside\s+story\b',
            'spinoff': r'\bspinoff\b',
            'movie': r'\bmovie\b|\bthe\s+movie\b|\bfilm\b',
            'special': r'\bspecials?\b|\bova\b|\boad\b',
        }
        return {
            token
            for token, pattern in token_patterns.items()
            if re.search(pattern, normalized, re.IGNORECASE)
        }

    def extract_preferred_season_number(self, *texts: Optional[str]) -> Optional[int]:
        season_numbers: List[int] = []
        for text in texts:
            if not text:
                continue
            for token in self._extract_season_tokens(text):
                if re.fullmatch(r's\d+', token):
                    season_numbers.append(int(token[1:]))

        if not season_numbers:
            return None

        return min(season_numbers)

    def _score_tv_candidate(
        self,
        source_title: str,
        query: str,
        candidate: Dict[str, Any],
        year: Optional[int],
        query_index: int = 0,
    ) -> float:
        candidate_titles = [
            candidate.get('name', ''),
            candidate.get('original_name', ''),
        ]
        normalized_queries = [
            self._normalize_tv_score_text(source_title),
            self._normalize_tv_score_text(query),
        ]

        best_similarity = 0.0
        exact_boost = 0.0
        query_tokens = self._extract_season_tokens(source_title) | self._extract_season_tokens(query)
        candidate_tokens = set()
        source_root_tokens = self._extract_franchise_root_tokens(source_title)
        query_root_tokens = self._extract_franchise_root_tokens(query)
        expected_root_tokens = source_root_tokens | query_root_tokens
        expected_numeric_tokens = self._extract_numeric_identity_tokens(
            str(candidate.get('_matched_query') or query)
        )
        query_spinoff_tokens = self._collect_spinoff_tokens(source_title) | self._collect_spinoff_tokens(query)
        candidate_spinoff_tokens: Set[str] = set()
        candidate_numeric_tokens: Set[str] = set()

        for query_text in normalized_queries:
            if not query_text:
                continue
            for candidate_title in candidate_titles:
                normalized_candidate = self._normalize_tv_score_text(candidate_title)
                if not normalized_candidate:
                    continue

                similarity = SequenceMatcher(
                    None,
                    query_text,
                    normalized_candidate,
                ).ratio()
                best_similarity = max(best_similarity, similarity)
                candidate_tokens |= self._extract_season_tokens(candidate_title)
                candidate_spinoff_tokens |= self._collect_spinoff_tokens(candidate_title)
                candidate_numeric_tokens |= self._extract_numeric_identity_tokens(
                    candidate_title
                )

                if query_text == normalized_candidate:
                    exact_boost = max(exact_boost, 24.0)
                elif (
                    query_text in normalized_candidate
                    or normalized_candidate in query_text
                ):
                    exact_boost = max(exact_boost, 10.0)

        score = best_similarity * 100 + exact_boost
        score += max(0, 8 - query_index * 2)

        if query_tokens and candidate_tokens:
            overlap = query_tokens & candidate_tokens
            if overlap:
                score += 18 * len(overlap)
            else:
                score -= 12
        elif query_tokens and not candidate_tokens:
            score -= 18

        if expected_root_tokens:
            matched_root_tokens = set()
            for candidate_title in candidate_titles:
                matched_root_tokens |= (
                    expected_root_tokens
                    & self._extract_franchise_root_tokens(candidate_title)
                )

            if matched_root_tokens:
                score += min(len(matched_root_tokens) * 6, 18)
            elif len(expected_root_tokens) >= 2:
                score -= 18

        if expected_numeric_tokens:
            matched_numeric_tokens = (
                expected_numeric_tokens & candidate_numeric_tokens
            )
            if matched_numeric_tokens:
                score += min(len(matched_numeric_tokens) * 18, 24)
            else:
                score -= min(len(expected_numeric_tokens) * 20, 28)

        extra_spinoff_tokens = candidate_spinoff_tokens - query_spinoff_tokens
        if extra_spinoff_tokens:
            score -= 14 * len(extra_spinoff_tokens)

        if year:
            candidate_year = self._extract_tv_candidate_year(candidate)
            if candidate_year == year:
                score += 12
            elif candidate_year and abs(candidate_year - year) == 1:
                score += 6
            elif candidate_year and abs(candidate_year - year) <= 2:
                score += 3

        return score

    def rank_tv_candidates(
        self,
        source_title: str,
        query: str,
        candidates: List[Dict[str, Any]],
        year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        for candidate in candidates:
            ranked_candidate = dict(candidate)
            ranked_candidate['_match_score'] = round(
                self._score_tv_candidate(source_title, query, candidate, year),
                3,
            )
            ranked.append(ranked_candidate)

        return sorted(
            ranked,
            key=lambda item: (
                item.get('_match_score', 0),
                item.get('popularity', 0),
            ),
            reverse=True,
        )

    def _select_ranked_tv_candidate(
        self,
        candidates: List[Dict[str, Any]],
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not candidates:
            return None, None

        if len(candidates) == 1:
            candidate = candidates[0]
            if self._is_ranked_tv_candidate_plausible(candidate):
                return candidate, 'High'
            return None, 'Low'

        first = candidates[0]
        second = candidates[1]
        first_score = float(first.get('_match_score', 0) or 0)
        second_score = float(second.get('_match_score', 0) or 0)
        score_gap = first_score - second_score

        if first_score >= 120 or (first_score >= 105 and score_gap >= 15):
            return first, 'High'

        if first_score >= 96 and score_gap >= 10:
            return first, 'Medium'

        return None, None

    @staticmethod
    def _is_ranked_tv_candidate_plausible(candidate: Dict[str, Any]) -> bool:
        score = candidate.get('_match_score')
        if not isinstance(score, (int, float)):
            return False

        numeric_score = float(score)
        if numeric_score >= 70.0:
            return True

        # TMDB 对罗马音/英译标题有时只能返回一个接近候选，分数会略低于
        # 通用阈值（例如 Choujigen Game Neptune -> Hyperdimension Neptunia）。
        # 只允许真实查询命中的单候选继续进入后续 strict TV 映射；没有
        # `_matched_query` 的低分候选仍然 fail-closed。
        return numeric_score >= 66.0 and bool(candidate.get('_matched_query'))

    @staticmethod
    def _is_ranked_movie_candidate_plausible(candidate: Dict[str, Any]) -> bool:
        score = candidate.get('_match_score')
        if isinstance(score, (int, float)):
            return float(score) >= 70.0
        return False

    def _search_tvs_with_queries(
        self,
        source_title: str,
        queries: List[str],
        year: Optional[int],
        limit: int,
    ) -> Optional[List[Dict[str, Any]]]:
        ranked_candidates: Dict[int, Dict[str, Any]] = {}
        per_query_limit = max(limit * 2, 8)

        for query_index, query in enumerate(queries):
            results = self._search_tv_multi_language(
                query=query,
                year=year,
            )
            if not results:
                continue

            for candidate in results[:per_query_limit]:
                tv_id = candidate.get('id')
                if not isinstance(tv_id, int):
                    continue

                score = self._score_tv_candidate(
                    source_title=source_title,
                    query=query,
                    candidate=candidate,
                    year=year,
                    query_index=query_index,
                )
                ranked_candidate = dict(candidate)
                ranked_candidate['_match_score'] = round(score, 3)
                ranked_candidate['_matched_query'] = query

                current = ranked_candidates.get(tv_id)
                if not current or score > current.get('_match_score', 0):
                    ranked_candidates[tv_id] = ranked_candidate

        if not ranked_candidates:
            return None

        return sorted(
            ranked_candidates.values(),
            key=lambda item: (
                item.get('_match_score', 0),
                item.get('popularity', 0),
            ),
            reverse=True,
        )[:limit]

    def get_season_info(
        self, tv_id: int, season_number: int
    ) -> Optional[Dict[str, Any]]:
        """
        获取指定季度的详细信息，包括剧集列表

        Args:
            tv_id: 电视剧ID
            season_number: 季度号

        Returns:
            筛选后的季度信息字典，失败返回None
        """
        cache_key = (tv_id, season_number, 'zh-CN')
        cached_value, cache_hit = self._cache_get(self._season_info_cache, cache_key)
        if cache_hit:
            return cached_value

        for i in range(3):
            try:
                season = tmdb.TV_Seasons(tv_id, season_number)
                season_info = season.info(language="zh-CN")

                if not season_info:
                    logger.warning(f"[季度信息] 未获取到Season {season_number}的信息")
                    self._cache_set(self._season_info_cache, cache_key, None)
                    return None

                filtered_season: SeasonPayload = {
                    "air_date": season_info.get("air_date"),
                    "episode_count": season_info.get("episode_count", 0),
                    "id": season_info.get("id"),
                    "name": season_info.get("name", ""),
                    "season_number": season_info.get("season_number", season_number),
                    "episodes": cast(list[TMDBDict], []),
                    "_episodes_loaded": True,
                }

                episodes = season_info.get("episodes", [])
                if not isinstance(episodes, list):
                    episodes = []
                for episode in episodes:
                    if not isinstance(episode, dict):
                        continue
                    filtered_episode = {
                        "air_date": episode.get("air_date"),
                        "episode_number": episode.get("episode_number"),
                        "episode_type": episode.get("episode_type", "regular"),
                        "name": episode.get("name", ""),
                        "runtime": episode.get("runtime"),
                        "season_number": episode.get("season_number", season_number),
                        "overview": episode.get("overview", ""),
                    }
                    filtered_season["episodes"].append(filtered_episode)

                logger.info(
                    f'[季度信息] 获取Season {season_number}信息成功，包含{len(filtered_season["episodes"])}集'
                )
                self._cache_set(self._season_info_cache, cache_key, filtered_season)
                return filtered_season

            except Exception as e:
                logger.warning(
                    f"[季度信息] 获取Season {season_number}信息失败，重试第{i + 1}次: {str(e)}"
                )
                sleep(5)

        logger.error(f"[季度信息] 获取Season {season_number}信息最终失败")
        return None

    def get_tv_info_with_seasons(
        self, query: str, year: int
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        """
        获取电视剧信息，包含详细的季度和剧集信息

        Args:
            query: 搜索关键词
            year: 年份

        Returns:
            (剧集名称, 包含详细季度信息的tv_info字典)
        """
        # 首先获取基本的电视剧信息
        name, tv_info = self.get_tv_info(query, year)

        if not name or not tv_info:
            return name, tv_info

        # 填充季度信息
        tv_info = self.fill_season_info(tv_info)
        return name, tv_info

    def fill_season_info(self, tv_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        填充电视剧信息中的季度信息

        Args:
            tv_info: 包含电视剧基本信息的字典

        Returns:
            填充后的tv_info字典
        """
        if not tv_info or "id" not in tv_info:
            logger.error("[季度信息] 无效的电视剧信息，无法填充季度信息")
            return tv_info

        tv_id = tv_info["id"]
        hydrated_cache_key = (tv_id, 'hydrated')
        cached_hydrated, hydrated_hit = self._cache_get(
            self._hydrated_tv_cache,
            hydrated_cache_key,
        )
        if hydrated_hit:
            return cached_hydrated

        seasons = tv_info.get("seasons", [])
        if not seasons:
            logger.warning("[季度信息] 电视剧没有季度信息，尝试重新获取详情...")
            detailed_tv_info = self.get_tv_info_by_id(tv_id)
            if detailed_tv_info and detailed_tv_info.get("seasons"):
                tv_info = detailed_tv_info
                seasons = tv_info.get("seasons", [])
            else:
                logger.error("[季度信息] 获取季度信息失败")
                return tv_info

        if self._is_tv_info_hydrated(tv_info):
            self._cache_set(self._hydrated_tv_cache, hydrated_cache_key, tv_info)
            return tv_info

        season_entries = [
            season
            for season in seasons
            if isinstance(season, dict) and season.get("season_number") is not None
        ]

        missing_seasons: List[Dict[str, Any]] = []
        for season in season_entries:
            if self._has_detailed_episodes(season):
                continue
            missing_seasons.append(season)

        for season in seasons:
            if isinstance(season, dict) and season.get("season_number") is None:
                logger.warning(f"[季度信息] 跳过无效季度: {season}")

        if not missing_seasons:
            self._cache_set(self._hydrated_tv_cache, hydrated_cache_key, tv_info)
            return tv_info

        max_workers = min(8, max(1, len(missing_seasons)))

        def _fetch_season_detail(entry: Dict[str, Any]):
            season_number = entry.get("season_number")
            if not isinstance(season_number, int):
                raise ValueError(f"invalid season_number: {season_number!r}")
            logger.info(f"[季度信息] 正在获取Season {season_number}的详细信息...")
            return season_number, self.get_season_info(tv_id, season_number)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_season = {
                executor.submit(_fetch_season_detail, season): season
                for season in missing_seasons
            }

            for future in as_completed(future_to_season):
                season = future_to_season[future]
                season_number = season.get("season_number")
                try:
                    _, detailed_season = future.result()
                except Exception as e:
                    logger.warning(
                        f"[季度信息] Season {season_number}获取详细信息失败，"
                        f"使用原始数据: {str(e)}"
                    )
                    continue

                if detailed_season:
                    season.update(detailed_season)
                    season['_episodes_loaded'] = True
                else:
                    logger.warning(
                        f"[季度信息] Season {season_number}获取详细信息失败，使用原始数据"
                    )

        if self._is_tv_info_hydrated(tv_info):
            self._cache_set(self._hydrated_tv_cache, hydrated_cache_key, tv_info)

        logger.info(
            f'[季度信息] 电视剧《{tv_info["name"]}》的季度信息填充完成，共{len(seasons)}个季度'
        )
        return tv_info

    def get_movie_info(
        self,
        query: str,
        year: int,
    ):
        for i in range(3):
            try:
                results = self._search_movie_multi_language(
                    query=query,
                    year=year if year != 0 else None,
                )
                if results:
                    target = results[0]
                    name = target['title']
                    movie = tmdb.Movies(target['id'])
                    movie.info()
                    logger.debug(str(movie.__dict__))
                    return name, movie.__dict__
                return '', None
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[电影搜索] 网络错误, 重试第{i + 1}次中...')
        return '', None

    def get_tv_info(
        self,
        query: str,
        year: int,
    ):
        queries = build_tv_search_queries(query)
        if not queries:
            return '', None

        for i in range(3):
            try:
                results = self._search_tvs_with_queries(
                    source_title=query,
                    queries=queries,
                    year=year if year != 0 else None,
                    limit=1,
                )
                if not results and year != 0:
                    results = self._search_tvs_with_queries(
                        source_title=query,
                        queries=queries,
                        year=None,
                        limit=1,
                    )
                    if results:
                        logger.info(f'[电视剧搜索] 去年份回退命中: {queries[:4]}')

                if results:
                    target = results[0]
                    name = target['name']
                    tv = tmdb.TV(target['id'])
                    tv.info()
                    logger.debug(str(tv.__dict__))
                    logger.info(f'[电视剧搜索] 候选查询命中: {queries[:4]}, year={year}')
                    return name, tv.__dict__
                return '', None
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[电视剧搜索] 网络错误, 重试第{i + 1}次中...')
        return '', None

    def search_movie_collection(
        self,
        query: str,
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        """
        搜索电影合集

        Args:
            query: 搜索关键词（合集名称）

        Returns:
            (合集名称, 合集信息字典) 或 ('', None)
        """
        for i in range(3):
            try:
                results = self._search_movie_multi_language(query=query)
                if results:
                    collection = results[0]
                    collection_id = collection['id']
                    collection_name = collection['name']
                    # 获取合集详细信息
                    col = tmdb.Collections(collection_id)
                    col.info(language='zh-CN')
                    logger.info(f'[合集搜索] 找到合集: {collection_name}')
                    return collection_name, col.__dict__
                return '', None
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[合集搜索] 网络错误, 重试第{i + 1}次中...')
        return '', None

    def get_movie_info_by_id(self, movie_id: int) -> Optional[Dict[str, Any]]:
        """
        根据 TMDB ID 获取电影详细信息

        Args:
            movie_id: TMDB 电影 ID

        Returns:
            电影信息字典或 None
        """
        cache_key = (movie_id, 'zh-CN')
        cached_value, cache_hit = self._cache_get(self._movie_info_cache, cache_key)
        if cache_hit:
            return cached_value

        for i in range(3):
            try:
                movie_obj = tmdb.Movies(movie_id)
                movie_obj.info(language='zh-CN')
                movie_info = dict(movie_obj.__dict__)
                self._cache_set(self._movie_info_cache, cache_key, movie_info)
                return movie_info
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[电影信息] 网络错误, 重试第{i + 1}次中...')
        return None

    def _normalize_movie_score_text(self, text: str) -> str:
        cleaned = self._clean_movie_title(text)
        cleaned = re.sub(r'[：:·•|｜/\\\-\._]+', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip().casefold()

    def _extract_movie_candidate_year(
        self,
        candidate: Dict[str, Any],
    ) -> Optional[int]:
        release_date = candidate.get('release_date', '')
        if not isinstance(release_date, str):
            return None
        if not re.match(r'^\d{4}', release_date):
            return None
        return int(release_date[:4])

    def _score_movie_candidate(
        self,
        source_title: str,
        query: str,
        candidate: Dict[str, Any],
        year: Optional[int],
        query_index: int,
    ) -> float:
        candidate_titles = [
            candidate.get('title', ''),
            candidate.get('original_title', ''),
        ]
        normalized_queries = [
            self._normalize_movie_score_text(source_title),
            self._normalize_movie_score_text(query),
        ]

        best_similarity = 0.0
        exact_boost = 0.0
        source_has_movie_signal = any(
            self._has_movie_title_signal(text)
            for text in (source_title, query)
        )
        candidate_has_movie_signal = any(
            self._has_movie_title_signal(candidate_title)
            for candidate_title in candidate_titles
            if isinstance(candidate_title, str)
        )
        for query_text in normalized_queries:
            if not query_text:
                continue
            for candidate_title in candidate_titles:
                normalized_candidate = self._normalize_movie_score_text(candidate_title)
                if not normalized_candidate:
                    continue

                similarity = SequenceMatcher(
                    None,
                    query_text,
                    normalized_candidate,
                ).ratio()
                best_similarity = max(best_similarity, similarity)

                if query_text == normalized_candidate:
                    exact_boost = max(exact_boost, 25.0)
                elif (
                    query_text in normalized_candidate
                    or normalized_candidate in query_text
                ):
                    exact_boost = max(exact_boost, 12.0)

        score = best_similarity * 100 + exact_boost
        if source_has_movie_signal and candidate_has_movie_signal:
            score += 30
        score += max(0, 8 - query_index * 2)

        if year:
            candidate_year = self._extract_movie_candidate_year(candidate)
            if candidate_year == year:
                score += 15
            elif candidate_year and abs(candidate_year - year) == 1:
                score += 8
            elif candidate_year and abs(candidate_year - year) <= 2:
                score += 4

        return score

    def _search_movies_with_queries(
        self,
        source_title: str,
        queries: List[str],
        year: Optional[int],
        limit: int,
    ) -> Optional[List[Dict[str, Any]]]:
        ranked_candidates: Dict[int, Dict[str, Any]] = {}
        per_query_limit = max(limit * 2, 8)

        for query_index, query in enumerate(queries):
            results = self._search_movie_multi_language(
                query=query,
                year=year,
            )
            if not results:
                continue

            for candidate in results[:per_query_limit]:
                movie_id = candidate.get('id')
                if not isinstance(movie_id, int):
                    continue

                score = self._score_movie_candidate(
                    source_title=source_title,
                    query=query,
                    candidate=candidate,
                    year=year,
                    query_index=query_index,
                )
                ranked_candidate = dict(candidate)
                ranked_candidate['_match_score'] = round(score, 3)
                ranked_candidate['_matched_query'] = query

                current = ranked_candidates.get(movie_id)
                if not current or score > current.get('_match_score', 0):
                    ranked_candidates[movie_id] = ranked_candidate

        if not ranked_candidates:
            return None

        return sorted(
            ranked_candidates.values(),
            key=lambda item: (
                item.get('_match_score', 0),
                item.get('popularity', 0),
            ),
            reverse=True,
        )[:limit]

    def search_movies_by_title(
        self,
        title: str,
        year: Optional[int] = None,
        limit: int = 5,
        collection_name: Optional[str] = None,
        query_variants: Optional[List[str]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        根据标题搜索电影，返回多个候选结果

        Args:
            title: 电影标题
            year: 年份（可选）
            limit: 返回结果数量限制
            collection_name: 系列名（用于构造额外查询）
            query_variants: 显式指定查询候选

        Returns:
            电影信息列表或None
        """
        queries = query_variants or build_movie_search_queries(title, collection_name)
        if not queries:
            return None

        cache_key = (
            self._normalize_cache_text(title),
            year,
            limit,
            self._normalize_cache_text(collection_name),
            self._normalize_queries_key(queries),
        )
        cached_value, cache_hit = self._cache_get(self._movie_search_cache, cache_key)
        if cache_hit:
            return cached_value

        for i in range(3):
            try:
                results = self._search_movies_with_queries(
                    source_title=title,
                    queries=queries,
                    year=year,
                    limit=limit,
                )
                if results:
                    logger.info(
                        f"[电影搜索] 候选查询命中: {queries[:4]}, year={year}"
                    )
                    self._cache_set(self._movie_search_cache, cache_key, results)
                    return results

                if year:
                    results = self._search_movies_with_queries(
                        source_title=title,
                        queries=queries,
                        year=None,
                        limit=limit,
                    )
                    if results:
                        logger.info(
                            f"[电影搜索] 去年份回退命中: {queries[:4]}"
                        )
                        self._cache_set(self._movie_search_cache, cache_key, results)
                        return results

                self._cache_set(self._movie_search_cache, cache_key, None)
                return None
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[电影搜索] 网络错误, 重试第{i + 1}次中...')
        return None

    def _clean_movie_title(self, title: str) -> str:
        """
        清理电影标题，去掉常见的前缀和后缀

        Args:
            title: 原始标题

        Returns:
            清理后的标题
        """
        # 需要去除的前缀
        prefixes = [
            r'^剧场版\s*',
            r'^劇場版\s*',
            r'^theatrical\s*',
            r'^movie\s*',
            r'^film\s*',
        ]

        clean = title
        for prefix in prefixes:
            clean = re.sub(prefix, '', clean, flags=re.IGNORECASE)

        return clean.strip()

    def search_tv_by_query(
        self,
        query: str,
        year: Optional[int] = None,
        limit: int = 5,
        query_variants: Optional[List[str]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        根据关键词搜索电视剧，返回多个候选结果

        Args:
            query: 搜索关键词
            year: 年份（可选）
            limit: 返回结果数量限制
            query_variants: 显式指定查询候选

        Returns:
            电视剧信息列表或None
        """
        queries = query_variants or build_tv_search_queries(query)
        if not queries:
            return None

        cache_key = (
            self._normalize_cache_text(query),
            year,
            limit,
            self._normalize_queries_key(queries),
        )
        cached_value, cache_hit = self._cache_get(self._tv_search_cache, cache_key)
        if cache_hit:
            return cached_value

        for i in range(3):
            try:
                results = self._search_tvs_with_queries(
                    source_title=query,
                    queries=queries,
                    year=year,
                    limit=limit,
                )
                if results:
                    logger.info(
                        f"[电视剧搜索] 候选查询命中: {queries[:4]}, year={year}"
                    )
                    self._cache_set(self._tv_search_cache, cache_key, results)
                    return results

                if year:
                    results = self._search_tvs_with_queries(
                        source_title=query,
                        queries=queries,
                        year=None,
                        limit=limit,
                    )
                    if results:
                        logger.info(
                            f"[电视剧搜索] 去年份回退命中: {queries[:4]}"
                        )
                        self._cache_set(self._tv_search_cache, cache_key, results)
                        return results

                self._cache_set(self._tv_search_cache, cache_key, None)
                return None
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[电视剧搜索] 网络错误, 重试第{i + 1}次中...')
        return None

    def get_tv_info_by_id(self, tv_id: int) -> Optional[Dict[str, Any]]:
        """
        根据 TMDB ID 获取电视剧详细信息

        Args:
            tv_id: TMDB 电视剧 ID

        Returns:
            电视剧信息字典或 None
        """
        cache_key = (tv_id, 'zh-CN')
        cached_value, cache_hit = self._cache_get(self._tv_info_cache, cache_key)
        if cache_hit:
            return cached_value

        for i in range(3):
            try:
                tv = tmdb.TV(tv_id)
                tv.info(language='zh-CN')
                tv_info = dict(tv.__dict__)
                self._cache_set(self._tv_info_cache, cache_key, tv_info)
                return tv_info
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[电视剧信息] 网络错误, 重试第{i + 1}次中...')
        return None
