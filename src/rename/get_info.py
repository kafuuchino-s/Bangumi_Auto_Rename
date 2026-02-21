import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep
from typing import Any, Dict, List, Optional

import tmdbsimple as tmdb

from ..logger import logger
from ..config.config_manager import cm
from .cleaner import is_chinese_percentage_sufficient


class Search:
    def __init__(self) -> None:
        self.TMDB_KEY = cm.get_config('api_key')
        tmdb.API_KEY = self.TMDB_KEY

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

        for index, language in enumerate(languages):
            search = tmdb.Search()
            search.movie(
                query=query,
                language=language,
                year=year if year else None,
            )
            results = search.__dict__['results']
            if results:
                if index > 0:
                    logger.info(
                        f'[电影搜索] 语言回退命中: {language}, query={query}'
                    )
                return results
        return None

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

        for index, language in enumerate(languages):
            search = tmdb.Search()
            search.tv(
                query=query,
                language=language,
                first_air_date_year=year if year and year != 0 else None,
            )
            results = search.__dict__['results']
            if results:
                if index > 0:
                    logger.info(
                        f'[电视剧搜索] 语言回退命中: {language}, query={query}'
                    )
                return results
        return None

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
        for i in range(3):
            try:
                season = tmdb.TV_Seasons(tv_id, season_number)
                season_info = season.info(language="zh-CN")

                if not season_info:
                    logger.warning(f"[季度信息] 未获取到Season {season_number}的信息")
                    return None

                # 筛选季度信息，只保留需要的字段
                filtered_season = {
                    "air_date": season_info.get("air_date"),
                    "episode_count": season_info.get("episode_count", 0),
                    "id": season_info.get("id"),
                    "name": season_info.get("name", ""),
                    "overview": season_info.get("overview", ""),
                    "season_number": season_info.get("season_number", season_number),
                    "episodes": [],
                }

                # 处理剧集信息
                episodes: List[Dict] = season_info.get("episodes", [])
                for episode in episodes:
                    filtered_episode = {
                        "air_date": episode.get("air_date"),
                        "episode_number": episode.get("episode_number"),
                        "episode_type": episode.get("episode_type", "regular"),
                        "name": episode.get("name", ""),
                        "overview": episode.get("overview", ""),
                        "runtime": episode.get("runtime"),
                        "season_number": episode.get("season_number", season_number),
                    }
                    filtered_season["episodes"].append(filtered_episode)

                logger.info(
                    f'[季度信息] 获取Season {season_number}信息成功，包含{len(filtered_season["episodes"])}集'
                )
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
        seasons = tv_info.get("seasons", [])

        if not seasons:
            logger.warning("[季度信息] 电视剧没有季度信息，尝试获取...")
            # 如果没有季度信息，尝试获取
            name, detailed_tv_info = self.get_tv_info_with_seasons(
                tv_info["name"], tv_info.get("first_air_date", 0)
            )
            if detailed_tv_info:
                return detailed_tv_info
            else:
                logger.error("[季度信息] 获取季度信息失败")
                return tv_info

        # 获取每个季度的详细信息（并行拉取）
        season_entries = [
            season
            for season in seasons
            if season.get("season_number") is not None
        ]

        for season in seasons:
            if season.get("season_number") is None:
                logger.warning(f"[季度信息] 跳过无效季度: {season}")

        max_workers = min(8, max(1, len(season_entries)))

        def _fetch_season_detail(entry: Dict[str, Any]):
            season_number = entry.get("season_number")
            logger.info(f"[季度信息] 正在获取Season {season_number}的详细信息...")
            return season_number, self.get_season_info(tv_id, season_number)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_season = {
                executor.submit(_fetch_season_detail, season): season
                for season in season_entries
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
                else:
                    logger.warning(
                        f"[季度信息] Season {season_number}获取详细信息失败，使用原始数据"
                    )

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
        for i in range(3):
            try:
                for _ in range(3):
                    target_list = self._search_tv_multi_language(
                        query=query,
                        year=year,
                    )
                    if target_list:
                        target = target_list[0]
                        name = target['name']
                        tv = tmdb.TV(target['id'])
                        tv.info()
                        logger.debug(str(tv.__dict__))
                        return name, tv.__dict__
                    else:
                        if is_chinese_percentage_sufficient(query):
                            query = re.sub(r'[a-zA-Z]', '', query)
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
        for i in range(3):
            try:
                movie_obj = tmdb.Movies(movie_id)
                movie_obj.info(language='zh-CN')
                return movie_obj.__dict__
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[电影信息] 网络错误, 重试第{i + 1}次中...')
        return None

    def search_movies_by_title(
        self,
        title: str,
        year: Optional[int] = None,
        limit: int = 5,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        根据标题搜索电影，返回多个候选结果

        Args:
            title: 电影标题
            year: 年份（可选）
            limit: 返回结果数量限制

        Returns:
            电影信息列表或None
        """
        # 清理标题，去掉常见前缀
        clean_title = self._clean_movie_title(title)

        for i in range(3):
            try:
                results = self._search_movie_multi_language(
                    query=clean_title,
                    year=year,
                )
                if results:
                    return results[:limit]

                # 如果清理后的标题搜不到，尝试原标题
                if clean_title != title:
                    results = self._search_movie_multi_language(
                        query=title,
                        year=year,
                    )
                    if results:
                        return results[:limit]

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
        import re

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
    ) -> Optional[List[Dict[str, Any]]]:
        """
        根据关键词搜索电视剧，返回多个候选结果

        Args:
            query: 搜索关键词
            year: 年份（可选）
            limit: 返回结果数量限制

        Returns:
            电视剧信息列表或None
        """
        for i in range(3):
            try:
                results = self._search_tv_multi_language(
                    query=query,
                    year=year,
                )
                if results:
                    return results[:limit]
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
        for i in range(3):
            try:
                tv = tmdb.TV(tv_id)
                tv.info(language='zh-CN')
                return tv.__dict__
            except:  # noqa:E722, B001
                sleep(5)
                logger.warning(f'[电视剧信息] 网络错误, 重试第{i + 1}次中...')
        return None
