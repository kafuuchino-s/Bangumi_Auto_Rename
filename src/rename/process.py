import re
import json
import uuid
from pathlib import Path
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Union, Optional
from concurrent.futures import ThreadPoolExecutor, Future

from jikanpy import Jikan

from .trans import Trans
from ..logger import logger
from .get_info import Search
from ..utils.path import TASK_PATH
from .ai_processor import AIProcessor
from ..config.config_manager import cm
from ..ai.models import AIAnalysisResult
from ..ai.client import AIClient
from ..ai.video_analyzer import VideoAnalyzer
from .utils import S0_TAG, EXTRA_TAG, IGNORE_DIR, VIDEO_SUFFIX, IGNORE_SUFFIX, episode_partten
from .cleaner import (
    remove_tag,
    to_sim_max,
    remove_code,
    remove_season,
    divide_by_year,
    extract_number,
    extract_season,
    remove_episode,
    extract_base_num,
    match_and_extract,
    remove_similar_part,
    find_unique_parts_in_videos,
    extract_video_format,
    extract_part,
    is_complex_filename,
)
from .filename_builder import (
    FilenameBuilder,
    MovieMetadata,
    EpisodeMetadata,
)

jikan = Jikan()


class Rename:
    def __init__(self):
        self.BANGUMI_PATH = Path(cm.get_config('bangumi_path'))
        self.MOVIE_PATH = Path(cm.get_config('movie_path'))
        self.ANIME_PATH = Path(cm.get_config('anime_path'))
        self.ANIME_MOVIE_PATH = Path(cm.get_config('anime_movie_path'))

        self.ANIME_MOVIE_PATH.mkdir(parents=True, exist_ok=True)
        self.MOVIE_PATH.mkdir(parents=True, exist_ok=True)
        self.ANIME_PATH.mkdir(parents=True, exist_ok=True)
        self.BANGUMI_PATH.mkdir(parents=True, exist_ok=True)
        self.search = Search()
        self.ai_processor = AIProcessor()

        self.R = {}

    def get_season_id(
        self,
        tv_info: Dict,
        work_path: Path,
        path: Path,
        titles: Optional[List[Dict]],
    ):
        season_id = 1
        path_name = path.name
        all_similaritys: List[Dict] = []

        for season in tv_info['seasons']:
            info_season_id = season['season_number']

            # 检查季度是否有集数，只有有集数的季度才创建文件夹
            episodes = season.get('episodes', [])
            episode_count = len(episodes) if episodes else season.get('episode_count', 0)
            if episode_count > 0:
                target_fold = work_path / FilenameBuilder.build_season_folder(info_season_id)
                target_fold.mkdir(parents=True, exist_ok=True)

            sname: str = season['name']
            logger.info(f'[处理任务] Season{info_season_id} 季度名: {sname}')

            '''
            int_season = extract_season(sname)
            logger.info(f'[处理任务] 提取信息季号:{int_season}')
            '''

            int_rtpath_name = extract_season(path_name)
            logger.info(f'[处理任务] 提取标题季号:{int_rtpath_name}')
            if info_season_id == int_rtpath_name:
                season_id = int_rtpath_name
                break

            # 如果不是Season1的情况下，sname处于路径之中，则直接跳过
            if not (sname.strip().startswith('Season') and '1' in sname):
                if sname in path.name:
                    sname_list = sname.split(' ')
                    path_name_list = path.stem.split(' ')
                    if len(sname_list) == len(path_name_list):
                        logger.info(f'[处理任务] 季度名称处于标题中：{sname}')
                        season_id = info_season_id
                        break
                    else:
                        logger.info(f'[处理任务] 季度名称与路径名称长度不同：{sname}')

                if titles:
                    # 或者计算相似度
                    for title in titles:
                        similaritys = {}
                        if title['type'] in [
                            'Default',
                            'Synonym',
                            'English',
                            'French',
                        ]:
                            ename = title['title']
                            path_name = path_name.replace(ename, '')
                            similarity = SequenceMatcher(
                                None,
                                sname,
                                remove_tag(path_name),
                            ).ratio()

                            # logger.debug(f'相似度{tindex}：{similarity}')
                            similaritys[similarity] = season_id
                        all_similaritys.append(similaritys)
        else:
            if all_similaritys:
                logger.info(f'[处理任务] 相似度：{all_similaritys}')
                season_id = to_sim_max(all_similaritys)

        logger.info(f'[处理任务] 识别季号：{season_id}')
        return season_id

    def process_sub(
        self,
        itme_path_main_name: str,
        item_repeat: Optional[List[str]],
        item_path: Path,
        work_path: Path,
        season_id: int,
    ):
        item_name = item_path.name
        if item_repeat:
            item_name_remove = remove_similar_part(item_repeat, item_path.stem)
        else:
            item_name_remove = item_path.stem

        item_name_l = item_name_remove.lower()
        item_suffix = item_path.suffix.lower()

        n_item_name_l = item_name.replace(itme_path_main_name, '').lower()
        logger.info(f'[处理任务] 移去主要内容后的文件名Lower：{n_item_name_l}')

        for ignore_dir in IGNORE_DIR:
            if ignore_dir in item_path.name:
                logger.info(f'[处理任务] 忽略文件夹：{item_path.name}')
                break
        else:
            for ignore_tag in IGNORE_SUFFIX:
                if ignore_tag in item_suffix:
                    logger.info(f'[处理任务] 忽略文件：{item_path.name}')
                    break
            else:
                # 不再处理 extra 目录，只处理 TMDB 有信息的文件
                # 检查是否为 Season 0 特典
                is_season0 = False
                for s0 in S0_TAG:
                    if re.search(rf'{s0.lower()}[\d]{{0,3}}', item_name_l):
                        is_season0 = True
                        break

                _item_name = remove_code(remove_season(item_name_l))
                logger.info(
                    f'[处理任务] 开始对{_item_name}处理, 寻找集数中...")'
                )

                # 优先尝试 S01E01 格式
                epp = extract_base_num(_item_name)
                if epp is not None:
                    ep = int(epp)
                else:
                    # 然后尝试 episode_partten 中的模式
                    ep = None
                    for pattern in episode_partten:
                        match = re.search(pattern, _item_name)
                        if match:
                            ep_str = match.group(1)
                            if ep_str.isdigit():
                                ep = int(ep_str)
                            else:
                                # 中文数字处理
                                from .cleaner import chinese_to_number
                                ep = chinese_to_number(ep_str)
                            if ep is not None:
                                logger.info(
                                    f'[处理任务] 使用 episode_partten 匹配到集数: {ep}'
                                )
                                break

                    # 最后回退到 extract_number
                    if ep is None:
                        ep = extract_number(_item_name)

                if ep is None:
                    if _item_name.isdigit():
                        ep = int(_item_name)
                    else:
                        # 无法识别集数，跳过此文件
                        logger.info(f'[处理任务] 无法识别集数，跳过文件：{item_path.name}')
                        return
                else:
                    ep = int(ep)

                # 如果是 Season 0 特典，覆盖 season_id
                if is_season0:
                    season_id = 0
                    logger.info(f'[处理任务] 识别为Season0特典：{item_path.name}')

                _idata = match_and_extract(item_name)
                if _idata:
                    season_id, ep = _idata[0], _idata[1]

                t = work_path / FilenameBuilder.build_season_folder(season_id)
                t.mkdir(parents=True, exist_ok=True)

                # 从 work_path 提取剧集标题
                series_title = FilenameBuilder.extract_title_from_folder(
                    work_path.name
                )

                # 提取分集信息
                part = extract_part(item_name)

                # 使用 Movie Pilot 格式生成文件名
                meta = EpisodeMetadata(
                    title=series_title,
                    season=int(season_id),
                    episode=int(ep),
                    part=part,
                    file_ext=item_path.suffix,
                )
                new_filename = FilenameBuilder.build_episode_filename(meta)
                self.R[item_path] = t / new_filename
        logger.info(f'[处理任务] 处理完成{item_name}')

    def process(
        self,
        path: Path,
        _is_anime: Optional[bool] = None,
        _is_movie: Optional[bool] = None,
        _tuuid: Optional[str] = None,
        cus_name: Optional[str] = None,
        cus_season_id: Optional[int] = None,
        _is_sub_task: bool = False,
    ):
        """
        处理文件/文件夹

        Args:
            path: 文件/文件夹路径
            _is_anime: 是否为动漫
            _is_movie: 是否为电影
            _tuuid: 任务UUID
            cus_name: 自定义名称
            cus_season_id: 自定义季度ID
            _is_sub_task: 是否为子任务（由父任务拆分出来的）
        """
        if path.is_dir():
            is_video = False
            for sub_path in path.iterdir():
                if not sub_path.is_dir() and sub_path.suffix in VIDEO_SUFFIX:
                    is_video = True

            if is_video:
                self._process(
                    path,
                    _is_anime,
                    _is_movie,
                    _tuuid,
                    cus_name,
                    cus_season_id,
                )
            else:
                # 如果不是子任务，将子文件夹作为独立任务加入队列
                # 这样多个 worker 可以并行处理
                if not _is_sub_task:
                    from ..queue.task_queue import get_queue_manager
                    queue_mgr = get_queue_manager()

                    sub_folders = [
                        sp for sp in path.iterdir()
                        if sp.is_dir() or sp.suffix in VIDEO_SUFFIX
                    ]
                    logger.info(
                        f"[处理任务] 发现 {len(sub_folders)} 个子文件夹，"
                        "分别加入队列并行处理"
                    )

                    for sub_path in sub_folders:
                        queue_mgr.enqueue(
                            path=str(sub_path),
                            is_anime=_is_anime,
                            is_movie=_is_movie,
                            _is_sub_task=True,  # 标记为子任务
                        )
                    # 父任务不需要继续处理
                    return
                else:
                    # 子任务，直接串行处理其内部的子文件夹
                    for sub_path in path.iterdir():
                        self._process(
                            sub_path,
                            _is_anime,
                            _is_movie,
                            _tuuid,
                            cus_name,
                            cus_season_id,
                        )
        else:
            self._process(
                path,
                _is_anime,
                _is_movie,
                _tuuid,
                cus_name,
                cus_season_id,
            )

    def check_task_type(
        self,
        _uuid: str,
        rtpath_name: str,
        year: int,
        path: Path,
        is_anime: Optional[bool] = None,
        is_movie: Optional[bool] = None,
        _ai_retry: bool = False,
        _ai_future: Optional[Future] = None,
    ) -> Union[Tuple[str, Dict, bool, bool], str]:
        season_id = 1
        pos = 0
        logger.info('[处理任务] 未传入任务类型，开始判断该文件是否为电影！')

        # 对于复杂文件名，并行启动 AI 提取
        ai_future = _ai_future
        if not _ai_retry and ai_future is None:
            ai_client = AIClient()
            if ai_client.is_available() and is_complex_filename(path.name):
                logger.info('[处理任务] 检测到复杂文件名，并行启动AI提取...')
                executor = ThreadPoolExecutor(max_workers=1)
                ai_future = executor.submit(
                    ai_client.extract_title_and_type, path.name
                )

        s1_name, s1_info = self._search_tv_with_ai_selection(path.name, rtpath_name, year)
        logger.info(f'[处理任务] 搜索到的电视剧名称: {s1_name}')

        s2_name, s2_info = self.search.get_movie_info(rtpath_name, year)
        logger.info(f'[处理任务] 搜索到的电影名称: {s2_name}')

        if not s2_name and year != 0:
            s2_name, s2_info = self.search.get_movie_info(
                rtpath_name,
                year,
            )
            logger.info(f'[处理任务] 未搜索到结果, 删除year后重试: {s2_name}')

        # 如果都搜索不到，尝试使用AI提取标题和类型
        if not s1_name and not s2_name and not _ai_retry:
            # 优先使用已经启动的并行任务结果
            if ai_future is not None:
                logger.info('[处理任务] TMDB搜索失败，等待AI提取结果...')
                try:
                    ai_result = ai_future.result(timeout=30)
                except Exception as e:
                    logger.warning(f'[处理任务] AI提取超时或失败: {e}')
                    ai_result = None
            else:
                # 非复杂文件名但 TMDB 失败，同步调用 AI
                ai_client = AIClient()
                if ai_client.is_available():
                    logger.info('[处理任务] TMDB搜索失败，尝试使用AI提取标题...')
                    ai_result = ai_client.extract_title_and_type(path.name)
                else:
                    ai_result = None

            if ai_result:
                ai_title, ai_type = ai_result
                if ai_title and ai_title != rtpath_name:
                    logger.info(
                        f'[处理任务] AI提取标题: {ai_title}，'
                        f'类型: {ai_type}，重新搜索TMDB'
                    )
                    # 根据AI判断的类型设置is_movie
                    ai_is_movie = None
                    if ai_type == 'movie':
                        ai_is_movie = True
                    elif ai_type == 'tv':
                        ai_is_movie = False
                    # 用AI提取的标题和类型重新搜索
                    return self.check_task_type(
                        _uuid,
                        ai_title,
                        year,
                        path,
                        is_anime,
                        ai_is_movie if ai_is_movie is not None else is_movie,
                        _ai_retry=True,  # 防止无限循环
                    )

        season_id = extract_season(rtpath_name)

        # 检测电影关键词，倾向于判定为电影
        movie_keywords = ['MOVIE', 'FILM', '剧场版', '劇場版', '电影', '電影']
        path_name_lower = path.name.lower()
        for kw in movie_keywords:
            if kw.lower() in path_name_lower:
                pos -= 1.0  # 强烈倾向于电影
                logger.info(f'[处理任务] 检测到电影关键词 "{kw}"，倾向判定为电影')
                break

        if s1_name:
            pos += 1
        elif s2_name:
            pos -= 1

        if season_id == -1:
            pos -= 0.6
            if path.is_file():
                pos -= 0.5
        else:
            pos += 0.6
            if path.is_file():
                pos += 0.5

        if path.is_dir():
            path_file_num = len([i for i in path.iterdir() if i.is_file()])
            if path_file_num > 6:
                pos += 0.4
            else:
                pos -= 0.4

        # 如果明确指定了 is_movie=True，直接按电影处理
        if is_movie is True:
            logger.info('[处理任务] 该文件被指定为电影！')
            info = s2_info
            name = s2_name

            if not info:
                logger.warning(f'[处理任务] 未搜索到电影信息, 跳过{rtpath_name}')
                return self.error_reply(
                    _uuid,
                    f'[TMDB] 未搜索到电影信息, 跳过{rtpath_name}',
                    path,
                    is_anime,
                )

            if is_anime is None:
                genres = info.get('genres', [])
                for g in genres:
                    genre_name = g['name'].lower()
                    if genre_name in ('animation', 'anime', '动画', 'アニメ'):
                        is_anime = True
                        logger.info(f'[处理任务] 检测到动画类型: {g["name"]}, is_anime=True')
                        break
                else:
                    is_anime = False
        elif pos > 0 or (is_movie is not None and not is_movie):
            logger.info('[处理任务] 该文件可能为电视剧！')
            is_movie = False
            info = s1_info
            name = s1_name

            if not info:
                logger.warning(f'[处理任务] 未搜索到电视剧信息, 跳过{rtpath_name}')
                return f'[TMDB] 未搜索到电视剧信息, 跳过{rtpath_name}'

            if is_anime is None:
                genres = info.get('genres', [])
                for g in genres:
                    genre_name = g['name'].lower()
                    if genre_name in ('animation', 'anime', '动画', 'アニメ'):
                        is_anime = True
                        logger.info(f'[处理任务] 检测到动画类型: {g["name"]}, is_anime=True')
                        break
                else:
                    is_anime = False
        else:
            logger.info('[处理任务] 该文件可能为电影！')
            is_movie = True
            info = s2_info
            name = s2_name

            if not info:
                logger.warning(f'[处理任务] 未搜索到电影信息, 跳过{rtpath_name}')
                return self.error_reply(
                    _uuid,
                    f'[TMDB] 未搜索到电影信息, 跳过{rtpath_name}',
                    path,
                    is_anime,
                )

            if is_anime is None:
                genres = info.get('genres', [])
                for g in genres:
                    genre_name = g['name'].lower()
                    if genre_name in ('animation', 'anime', '动画', 'アニメ'):
                        is_anime = True
                        logger.info(f'[处理任务] 检测到动画类型: {g["name"]}, is_anime=True')
                        break
                else:
                    is_anime = False
        return name, info, is_anime, is_movie

    def _process(
        self,
        path: Path,
        _is_anime: Optional[bool] = None,
        _is_movie: Optional[bool] = None,
        _tuuid: Optional[str] = None,
        cus_name: Optional[str] = None,
        cus_season_id: Optional[int] = None,
    ):
        if _tuuid:
            _uuid = _tuuid
        else:
            _uuid = str(uuid.uuid4())

        if not self.search.TMDB_KEY:
            return self.error_reply(
                _uuid,
                '你还没有配置TMDB的Key！任务失败！请先前往配置界面！',
                path,
                _is_anime,
                _is_movie,
            )

        # 【Step.0】 开始处理
        logger.info(f'[处理任务] 开始处理{path.name}')

        # 【Step.1】
        # 先移除无用的标签, 方便之后搜索
        year = 0
        rtpath_name = remove_tag(path.name)
        # 如果标签移除后啥都没有, 说明文件名也是标签的一部分
        if not rtpath_name:
            rtpath_name = remove_tag(path.name, True)
        # 按照空白、换行符或者连字符（-）分割成列表
        path_atri = re.split(r'[\s-]+', rtpath_name)
        # 如果该列表大于3, 不额外处理
        if len(path_atri) > 3:
            # path_atri.pop(0)
            rtpath_name = ' '.join(path_atri)
        # 如果该列表中有多个点, 则认为是一种规范命名的文件
        # 先用.分割之后, 按照年份分割后按照季度分割
        if rtpath_name.count('.') >= 3:
            rtpath_name = ' '.join(rtpath_name.split('.'))
            rtpath_name, year = divide_by_year(rtpath_name)

        rtpath_name = remove_season(rtpath_name)
        rtpath_name = remove_episode(rtpath_name)
        rtpath_name = rtpath_name.strip('!')
        logger.info(f'[处理任务] 去除标签后: {rtpath_name}')

        # 如果该路径不是一个视频文件或者不是一个文件夹, 则跳过
        if path.is_file() and path.suffix.lower() not in VIDEO_SUFFIX:
            logger.info(f'[处理任务] {path.name} 不是一个视频文件, 跳过')
            return

        # 【特殊改】
        if cus_name:
            rtpath_name = cus_name

        # 【Step.1.5】
        # 判断类型是否为电影
        task_type = self.check_task_type(
            _uuid,
            rtpath_name,
            year,
            path,
            _is_anime,
            _is_movie,
        )
        if isinstance(task_type, str):
            return self.error_reply(
                _uuid,
                task_type,
                path,
                _is_anime,
                _is_movie,
            )

        name, info, is_anime, is_movie = (
            task_type[0],
            task_type[1],
            task_type[2],
            task_type[3],
        )

        # 【Step.2】
        # 如果是电影
        if is_movie:
            if is_anime:
                _WORK_PATH = self.ANIME_MOVIE_PATH
            else:
                _WORK_PATH = self.MOVIE_PATH

            # 检查是否是电影合集（文件夹中包含多个视频文件）
            if path.is_dir():
                video_files = [
                    f for f in path.iterdir()
                    if f.is_file() and f.suffix.lower() in VIDEO_SUFFIX
                ]
                if len(video_files) > 1:
                    # 尝试使用AI分析电影合集
                    ai_client = AIClient()
                    if ai_client.is_available():
                        logger.info(
                            f'[处理任务] 检测到多个视频文件({len(video_files)}个)，'
                            '尝试AI分析电影合集'
                        )
                        # 分析视频文件
                        local_files = VideoAnalyzer.analyze_video_files(
                            path, video_files
                        )
                        collection_result = ai_client.analyze_movie_collection(
                            path.name, local_files
                        )

                        if collection_result and collection_result.is_collection:
                            logger.info(
                                f'[处理任务] AI识别为电影合集: '
                                f'{collection_result.collection_name}'
                            )
                            # 处理电影合集，获取每部电影的信息
                            processed_movies = self._process_movie_collection(
                                path,
                                collection_result,
                                _WORK_PATH,
                                is_anime,
                            )

                            if not processed_movies:
                                logger.warning('[处理任务] 电影合集无有效电影')
                                return self.error_reply(
                                    _uuid,
                                    '[电影合集] 无有效电影可处理',
                                    path,
                                    is_anime,
                                    is_movie,
                                    collection_result.collection_name,
                                    0,
                                )

                            # 为每部电影创建独立的任务记录
                            first_error = None
                            for i, movie_info in enumerate(processed_movies):
                                movie_uuid = _uuid if i == 0 else uuid.uuid4()
                                movie_R = {movie_info['file_path']: movie_info['target_file']}

                                trans_result = Trans(movie_R, movie_uuid).trans_file()

                                if isinstance(trans_result, str):
                                    # 记录第一个错误
                                    if first_error is None:
                                        first_error = trans_result
                                    logger.error(
                                        f"[电影合集] 传输失败: {movie_info['movie_name']}: {trans_result}"
                                    )
                                    continue

                                # 保存任务记录
                                task_path = TASK_PATH / f"{movie_uuid}.json"
                                task_data = {
                                    "path": str(path),
                                    "is_anime": is_anime,
                                    "is_movie": True,
                                    "is_collection": True,
                                    "collection_name": collection_result.collection_name,
                                    "name": movie_info['movie_name'],
                                    "year": movie_info['movie_year'],
                                    "season_id": 0,
                                    "uuid": str(movie_uuid),
                                    "error": None,
                                    "use_ai": True,
                                }
                                with open(task_path, "w", encoding="UTF-8") as file:
                                    json.dump(task_data, file, indent=4, ensure_ascii=False)

                                logger.info(
                                    f"[电影合集] 任务创建: {movie_info['movie_name']} ({movie_uuid})"
                                )

                            # 如果全部失败，返回错误
                            if first_error and len(processed_movies) == 1:
                                return self.error_reply(
                                    _uuid,
                                    first_error,
                                    path,
                                    is_anime,
                                    is_movie,
                                    collection_result.collection_name,
                                    0,
                                )

                            return True

            # 单个电影或AI合集分析失败，使用传统方式处理
            if not name:
                logger.warning(f'[处理任务] 未搜索到电影信息, 跳过{rtpath_name}')
                return self.error_reply(
                    _uuid,
                    f'[TMDB] 未搜索到电影信息, 跳过{rtpath_name}',
                    path,
                    is_anime,
                    is_movie,
                )

            first_data = info['release_date']
            first_year = first_data.split('-')[0] if first_data else None
            work_path = FilenameBuilder.build_movie_work_path(_WORK_PATH, name, first_year)
            work_path.mkdir(parents=True, exist_ok=True)

            if path.is_file():
                # 提取视频格式和分集信息
                video_format = extract_video_format(path.name)
                part = extract_part(path.name)
                meta = MovieMetadata(
                    title=name,
                    year=first_year,
                    video_format=video_format,
                    part=part,
                    file_ext=path.suffix,
                )
                new_filename = FilenameBuilder.build_movie_filename(meta)
                self.R[path] = work_path / new_filename
            else:
                for item_path in path.iterdir():
                    if item_path.suffix.lower() in VIDEO_SUFFIX:
                        video_format = extract_video_format(item_path.name)
                        part = extract_part(item_path.name)
                        meta = MovieMetadata(
                            title=name,
                            year=first_year,
                            video_format=video_format,
                            part=part,
                            file_ext=item_path.suffix,
                        )
                        new_filename = FilenameBuilder.build_movie_filename(meta)
                        self.R[item_path] = work_path / new_filename
                    else:
                        # 非视频文件保持原名
                        self.R[item_path] = work_path / item_path.name
            season_id = 0
        # 如果是剧集类型
        else:
            if is_anime:
                if not name:
                    logger.info('[处理任务] TMDB未搜索到!转为MyAnimeList搜索！')
                    search_result = jikan.search(
                        'anime',
                        rtpath_name,
                        page=1,
                    )
                    for i in search_result['data']:
                        if i['type'] == 'Anime':
                            data = i
                            break
                    else:
                        for i in search_result['data']:
                            if i['type'] == 'TV':
                                data = i
                                break
                        else:
                            data = search_result['data'][0]
                    titles = data['titles']
                    logger.info((f'[处理任务] MyAnimeList识别结果: {titles}'))
                else:
                    titles = None
                _WORK_PATH = self.ANIME_PATH
            else:
                titles = [{'type': 'Default', 'title': name}]
                _WORK_PATH = self.BANGUMI_PATH

            if not name:
                logger.warning(f'[处理任务] 未搜索到剧集信息, 跳过{rtpath_name}')
                return self.error_reply(
                    _uuid,
                    f'[TMDB] 未搜索到剧集信息, 跳过{rtpath_name}',
                    path,
                    is_anime,
                    is_movie,
                )

            first_data: str = info['first_air_date']
            first_year = first_data.split('-')[0]
            work_path = _WORK_PATH / f'{name} ({first_year})'

            # 只有 AI 不可用时才使用相似度计算季度
            if not self.ai_processor.ai_client.is_available():
                season_id = self.get_season_id(
                    info,
                    work_path,
                    path,
                    titles,
                )
            else:
                # AI 可用时，先设置默认值，后续由 AI 确定季度
                season_id = 1

            if cus_season_id:
                season_id = int(cus_season_id)

            # 【AI增强处理】
            # 如果启用了AI，使用AI分析文件映射（不再限制动漫类型）
            if self.ai_processor.ai_client.is_available():
                logger.info("[处理任务] 启用AI分析文件映射")
                logger.info("[处理任务] 填充详细季信息")
                tv_info = self.search.fill_season_info(info)

                # AI 重试机制
                max_retries = 3
                ai_result: AIAnalysisResult | None = None

                for attempt in range(1, max_retries + 1):
                    logger.info(f"[处理任务] AI分析尝试 {attempt}/{max_retries}")
                    ai_result = self.ai_processor.analyze_anime_files(path, tv_info)

                    if ai_result:
                        # 检查AI置信度阈值
                        confidence_threshold = cm.get_config("ai_confidence_threshold")
                        should_use_ai = False

                        if (
                            confidence_threshold == "High"
                            and ai_result.confidence == "High"
                        ):
                            should_use_ai = True
                        elif confidence_threshold == "Medium" and ai_result.confidence in [
                            "High",
                            "Medium",
                        ]:
                            should_use_ai = True
                        elif confidence_threshold == "Low":
                            should_use_ai = True

                        if should_use_ai:
                            logger.info("[处理任务] 使用AI分析结果进行文件映射")

                            # 从 AI 季度映射中提取主要季度，更新 season_id
                            if ai_result.season_mapping:
                                first_mapping = ai_result.season_mapping[0]
                                if first_mapping.maps_to_tmdb_seasons:
                                    season_id = first_mapping.maps_to_tmdb_seasons[0]
                                    logger.info(f"[处理任务] AI识别主季度: {season_id}")

                            # AI流程独立生成映射
                            self.R = self.ai_processor.apply_ai_mapping(
                                ai_result=ai_result,
                                anime_info=tv_info,
                                base_path=path,
                                work_path=work_path,
                            )

                            if self.R:
                                # 从实际映射结果中提取 season_id
                                # 检查所有映射的目标路径，提取季度信息
                                detected_seasons = set()
                                for target_path in self.R.values():
                                    # 目标路径格式: .../Season X/...
                                    parts = target_path.parts
                                    for part in parts:
                                        if part.startswith('Season '):
                                            try:
                                                s_num = int(part.replace('Season ', ''))
                                                detected_seasons.add(s_num)
                                            except ValueError:
                                                pass

                                if detected_seasons:
                                    # 如果只有 Season 0，则 season_id = 0
                                    # 否则取最小的非零季度（优先显示主季度）
                                    if detected_seasons == {0}:
                                        season_id = 0
                                        logger.info("[处理任务] 所有文件映射到 Season 0")
                                    else:
                                        non_zero = [s for s in detected_seasons if s > 0]
                                        if non_zero:
                                            season_id = min(non_zero)
                                        logger.info(
                                            f"[处理任务] 检测到季度: {detected_seasons}, "
                                            f"使用 season_id={season_id}"
                                        )

                                # AI 成功返回映射，跳出重试循环
                                break
                            else:
                                logger.warning(
                                    f"[处理任务] AI第{attempt}次尝试未返回有效映射"
                                )
                        else:
                            logger.warning(
                                f"[处理任务] AI第{attempt}次尝试置信度不足: "
                                f"{ai_result.confidence}"
                            )
                    else:
                        logger.warning(f"[处理任务] AI第{attempt}次尝试失败")

                    if attempt < max_retries:
                        logger.info("[处理任务] 准备重试AI分析...")
                else:
                    # 所有重试都失败
                    logger.error(
                        f"[处理任务] AI分析失败，已重试{max_retries}次，任务失败"
                    )
                    return self.error_reply(
                        _uuid,
                        f"[AI] 分析失败，已重试{max_retries}次",
                        path,
                        is_anime,
                        is_movie,
                        name,
                        season_id,
                    )
            else:
                # AI未启用，使用传统处理方式
                self._process_traditional(path, rtpath_name, work_path, season_id)

        task_path = TASK_PATH / f"{_uuid}.json"
        task_data = {
            "path": str(path),
            "is_anime": is_anime,
            "is_movie": is_movie,
            "name": name,
            "season_id": season_id,
            "uuid": str(_uuid),
            "error": None,
            "use_ai": self.ai_processor.ai_client.is_available(),
        }
        from ..subtitle.extractor import SUBTITLE_EXTENSIONS

        video_mapping = {}
        subtitle_mapping = {}
        for source_path, target_path in self.R.items():
            if source_path.suffix.lower() in SUBTITLE_EXTENSIONS:
                subtitle_mapping[source_path] = target_path
            else:
                video_mapping[source_path] = target_path

        # 先执行视频重命名（按主 mode），并写 record 供字幕导入使用
        trans_result = Trans(video_mapping, _uuid).trans_file()
        self.R = {}
        if isinstance(trans_result, str):
            return self.error_reply(
                _uuid,
                trans_result,
                path,
                is_anime,
                is_movie,
                name,
                season_id,
            )

        # 写入 task 记录（字幕导入会读取 data/task + data/record）
        with open(task_path, "w", encoding="UTF-8") as file:
            json.dump(task_data, file, indent=4, ensure_ascii=False)

        # 再把字幕按“字幕导入”方式强制复制到最终目录
        if subtitle_mapping:
            sub_trans = Trans(
                subtitle_mapping,
                _uuid,
                force_mode="复制",
                force_overwrite=cm.get_config('overwrite_existing'),
                write_record=False,
            )
            sub_trans_result = sub_trans.trans_file()
            if isinstance(sub_trans_result, str):
                logger.warning(f"[字幕处理] 关联字幕复制失败: {sub_trans_result}")

        return True

    def _process_movie_collection(
        self,
        path: Path,
        collection_result,
        work_path: Path,
        is_anime: bool,
    ) -> List[Dict]:
        """
        处理电影合集，返回每部电影的处理信息

        Args:
            path: 源文件夹路径
            collection_result: AI分析的MovieCollectionResult
            work_path: 工作目录（电影根目录）
            is_anime: 是否为动漫

        Returns:
            处理成功的电影信息列表，每个元素包含:
            - movie_name: 电影名称
            - movie_year: 年份
            - file_path: 源文件路径
            - target_file: 目标文件路径
        """
        import tmdbsimple as tmdb
        from ..ai.models import MovieCollectionResult
        from ..ai.client import AIClient

        if not isinstance(collection_result, MovieCollectionResult):
            logger.error("[电影合集] 无效的合集分析结果")
            return []

        logger.info(
            f"[电影合集] 开始处理合集: {collection_result.collection_name}, "
            f"包含{len(collection_result.file_mapping)}个文件映射"
        )

        processed_movies = []
        ai_client = AIClient()

        for mapping in collection_result.file_mapping:
            file_path = path / mapping.file_path
            if not file_path.exists():
                logger.warning(f"[电影合集] 文件不存在: {mapping.file_path}")
                continue

            # 搜索 TMDB 获取候选列表
            candidates = self.search.search_movies_by_title(
                mapping.movie_title,
                mapping.year,
                limit=5,
            )

            if not candidates:
                logger.info(
                    f"[电影合集] TMDB未找到: {mapping.movie_title}, 跳过"
                )
                continue

            # 如果只有一个结果，直接使用
            if len(candidates) == 1:
                selected = candidates[0]
            else:
                # 多个候选，让 AI 选择最匹配的
                selected = self._ai_select_movie(
                    ai_client,
                    file_path.name,
                    mapping.movie_title,
                    candidates,
                )
                if not selected:
                    selected = candidates[0]  # fallback 到第一个

            # 获取电影详细信息
            movie_obj = tmdb.Movies(selected['id'])
            movie_obj.info(language='zh-CN')
            movie_name = movie_obj.title
            release_date = getattr(movie_obj, 'release_date', '')
            movie_year = release_date.split('-')[0] if release_date else None

            logger.info(f'[电影搜索] 选择电影: {movie_name}')

            # 创建电影文件夹
            movie_folder = FilenameBuilder.build_movie_folder(movie_name, movie_year)
            target_folder = work_path / movie_folder
            target_folder.mkdir(parents=True, exist_ok=True)

            # 提取视频格式和分集信息
            video_format = extract_video_format(file_path.name)
            part = extract_part(file_path.name)
            meta = MovieMetadata(
                title=movie_name,
                year=movie_year,
                video_format=video_format,
                part=part,
                file_ext=file_path.suffix,
            )
            new_filename = FilenameBuilder.build_movie_filename(meta)
            target_file = target_folder / new_filename

            # 记录处理信息
            processed_movies.append({
                'movie_name': movie_name,
                'movie_year': movie_year,
                'file_path': file_path,
                'target_file': target_file,
            })

            logger.info(
                f"[电影合集] 映射: {file_path.name} -> {movie_folder}/{target_file.name}"
            )

        # 不处理未被映射的文件（TMDB 无信息的文件不处理）
        # 只记录日志
        mapped_files = {m['file_path'] for m in processed_movies}
        for item in path.iterdir():
            if item not in mapped_files and item.is_file():
                logger.info(f"[电影合集] 跳过未映射文件: {item.name}")

        return processed_movies

    def _ai_select_movie(
        self,
        ai_client,
        filename: str,
        extracted_title: str,
        candidates: List[Dict],
    ) -> Optional[Dict]:
        """
        让 AI 从 TMDB 候选列表中选择最匹配的电影

        Args:
            ai_client: AI 客户端
            filename: 原始文件名
            extracted_title: AI 提取的标题
            candidates: TMDB 候选电影列表

        Returns:
            选中的电影信息或 None
        """
        if not ai_client.is_available():
            return None

        # 构建候选列表信息
        candidates_info = ""
        for i, movie in enumerate(candidates):
            title = movie.get('title', '')
            original_title = movie.get('original_title', '')
            release_date = movie.get('release_date', '')
            overview = movie.get('overview', '')[:100]
            candidates_info += (
                f"{i+1}. {title} ({original_title}) [{release_date}]\n"
                f"   简介: {overview}...\n"
            )

        prompt = f"""请从以下TMDB搜索结果中选择最匹配的电影。

原始文件名: {filename}
提取的标题: {extracted_title}

候选电影:
{candidates_info}

请只返回最匹配的电影编号（1-{len(candidates)}），不要有其他文字。
例如文件名包含 "extra chorus" 就应该选择标题包含 "extra chorus" 的电影。"""

        system_prompt = "你是电影匹配助手。根据文件名选择最匹配的TMDB电影。只返回数字编号。"

        try:
            if ai_client.provider.lower() == "gemini":
                result = ai_client._call_gemini_simple(
                    system_prompt, prompt,
                    max_retries=1,
                    validation_key="",  # 不验证 JSON
                )
            else:
                result = ai_client._call_openai_simple(
                    system_prompt, prompt,
                    max_retries=1,
                    validation_key="",  # 不验证 JSON
                )

            if result:
                # 提取数字
                import re
                match = re.search(r'\d+', result.strip())
                if match:
                    idx = int(match.group()) - 1
                    if 0 <= idx < len(candidates):
                        logger.info(
                            f"[AI选择] 文件 {filename} -> 选择第 {idx+1} 个: "
                            f"{candidates[idx].get('title')}"
                        )
                        return candidates[idx]
        except Exception as e:
            logger.warning(f"[AI选择] AI 选择失败: {e}")

        return None

    def _ai_select_tv(
        self,
        folder_name: str,
        query: str,
        candidates: List[Dict],
    ) -> Optional[Dict]:
        """
        让 AI 从 TMDB 候选列表中选择最匹配的电视剧

        Args:
            folder_name: 原始文件夹名
            query: 搜索关键词
            candidates: TMDB 候选电视剧列表

        Returns:
            选中的电视剧信息或 None
        """
        from ..ai.client import AIClient

        ai_client = AIClient()
        if not ai_client.is_available():
            return None

        # 构建候选列表信息
        candidates_info = ""
        for i, tv in enumerate(candidates):
            name = tv.get('name', '')
            original_name = tv.get('original_name', '')
            first_air_date = tv.get('first_air_date', '')
            overview = tv.get('overview', '')[:100]
            candidates_info += (
                f"{i+1}. {name} ({original_name}) [{first_air_date}]\n"
                f"   简介: {overview}...\n"
            )

        prompt = f"""请从以下TMDB搜索结果中选择最匹配的电视剧/动漫。

原始文件夹名: {folder_name}
搜索关键词: {query}

候选电视剧:
{candidates_info}

请只返回最匹配的电视剧编号（1-{len(candidates)}），不要有其他文字。
注意：
- 文件夹名中可能包含季度信息（如 S2、第二季、Okawari 等）
- 选择与文件夹名最相关的剧集条目
- 如果文件夹名包含特定季度后缀（如 "Okawari"、"Okaeri"），应选择对应的条目"""

        system_prompt = "你是电视剧/动漫匹配助手。根据文件夹名选择最匹配的TMDB条目。只返回数字编号。"

        try:
            if ai_client.provider.lower() == "gemini":
                result = ai_client._call_gemini_simple(
                    system_prompt, prompt,
                    max_retries=1,
                    validation_key="",
                )
            else:
                result = ai_client._call_openai_simple(
                    system_prompt, prompt,
                    max_retries=1,
                    validation_key="",
                )

            if result:
                match = re.search(r'\d+', result.strip())
                if match:
                    idx = int(match.group()) - 1
                    if 0 <= idx < len(candidates):
                        logger.info(
                            f"[AI选择] 电视剧 {folder_name} -> 选择第 {idx+1} 个: "
                            f"{candidates[idx].get('name')}"
                        )
                        return candidates[idx]
        except Exception as e:
            logger.warning(f"[AI选择] AI 选择电视剧失败: {e}")

        return None

    def _search_tv_with_ai_selection(
        self,
        folder_name: str,
        query: str,
        year: int,
    ) -> Tuple[str, Optional[Dict]]:
        """
        搜索电视剧，支持 AI 选择最匹配的结果

        Args:
            folder_name: 原始文件夹名
            query: 搜索关键词
            year: 年份

        Returns:
            (剧集名称, 剧集信息) 或 ('', None)
        """
        import tmdbsimple as tmdb

        # 获取多个候选结果
        candidates = self.search.search_tv_by_query(query, year, limit=5)

        if not candidates:
            # 没有年份限制再试一次
            if year != 0:
                candidates = self.search.search_tv_by_query(query, None, limit=5)

        if not candidates:
            return '', None

        # 如果只有一个结果，直接使用
        if len(candidates) == 1:
            selected = candidates[0]
        else:
            # 多个候选，让 AI 选择
            selected = self._ai_select_tv(folder_name, query, candidates)
            if not selected:
                selected = candidates[0]  # fallback

        # 获取详细信息
        tv_info = self.search.get_tv_info_by_id(selected['id'])
        if tv_info:
            name = tv_info.get('name', selected.get('name', ''))
            logger.info(f'[电视剧搜索] 选择: {name}')
            return name, tv_info

        return '', None

    def _process_traditional(
        self, path: Path, rtpath_name: str, work_path: Path, season_id: int
    ):
        """传统处理方式"""
        if path.is_file():
            logger.info(f"[处理任务] 开始对 [单文件] {path.name}处理")
            self.process_sub(
                rtpath_name,
                None,
                path,
                work_path,
                season_id,
            )
        else:
            logger.info(f"[处理任务] 开始对 [文件夹] {path.name}处理")
            repeat = find_unique_parts_in_videos(path)
            for item_path in path.iterdir():
                logger.info(f"[处理任务] 处理嵌套文件夹 {item_path.name}")
                if item_path.is_dir():
                    repeat_2 = find_unique_parts_in_videos(item_path)
                    for sub_item in item_path.iterdir():
                        self.process_sub(
                            rtpath_name,
                            repeat_2,
                            sub_item,
                            work_path,
                            season_id,
                        )
                else:
                    self.process_sub(
                        rtpath_name,
                        repeat,
                        item_path,
                        work_path,
                        season_id,
                    )

    def error_reply(
        self,
        _uuid: str,
        error: str,
        path: Path,
        is_anime: Optional[bool] = None,
        is_movie: Optional[bool] = None,
        name: Optional[str] = None,
        season_id: Optional[int] = None,
    ):
        task_path = TASK_PATH / f'{_uuid}.json'
        task_data = {
            'path': str(path),
            'is_anime': is_anime,
            'is_movie': is_movie,
            'name': name,
            'season_id': season_id,
            'uuid': str(_uuid),
            'error': error,
        }
        with open(task_path, 'w', encoding='UTF-8') as file:
            json.dump(task_data, file, indent=4, ensure_ascii=False)
        return error
