import json
import re
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Type, Tuple

from pydantic import BaseModel, ValidationError
from google.genai.types import GenerateContentConfig

from ..logger import logger
from .models import (
    AIAnalysisResult,
    MovieCollectionResult,
    MovieSearchQueriesResult,
    SubtitleCandidateDecision,
    SubtitleMappingResult,
    SubtitleSearchQueriesResult,
    SubtitleThreadPackageDecision,
    TitleExtractionResult,
)
from ..utils.path import AI_ANALYSIS_PATH
from ..config.config_manager import cm
from ..rename.cleaner import remove_episode, remove_season
from ..rename.utils import PROMO_TAGS, SPECIAL_FOLDER_NAMES
from .gemini_client import GeminiClient
from .openai_client import OpenAIClient
from .base_client import BaseAIClient


class AIClient:
    """AI客户端工厂类，根据配置选择合适的AI提供商"""

    _TITLE_EXTRACTION_CACHE_MAX_SIZE = 128
    _title_cache_lock = Lock()
    _title_metadata_cache: "OrderedDict[str, TitleExtractionResult]" = OrderedDict()

    @classmethod
    def _title_cache_get(
        cls,
        cache_key: str,
    ) -> Optional[TitleExtractionResult]:
        if not cache_key:
            return None
        with cls._title_cache_lock:
            if cache_key not in cls._title_metadata_cache:
                return None
            value = cls._title_metadata_cache.pop(cache_key)
            cls._title_metadata_cache[cache_key] = value
        return deepcopy(value)

    @classmethod
    def _title_cache_set(
        cls,
        cache_key: str,
        value: TitleExtractionResult,
    ) -> None:
        if not cache_key:
            return
        with cls._title_cache_lock:
            if cache_key in cls._title_metadata_cache:
                cls._title_metadata_cache.pop(cache_key)
            cls._title_metadata_cache[cache_key] = deepcopy(value)
            while len(cls._title_metadata_cache) > cls._TITLE_EXTRACTION_CACHE_MAX_SIZE:
                cls._title_metadata_cache.popitem(last=False)

    @staticmethod
    def _strip_title_extraction_noise(value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            return ""

        cleaned = re.sub(r"\[.*?\]|【.*?】|《.*?》|<.*?>|\(.*?\)|（.*?）", " ", cleaned)
        cleaned = re.sub(
            r"\b(?:vol|volume|disc|cd|bd)\.?\s*\d+(?:\s*[-~]\s*(?:(?:vol|volume|disc|cd|bd)\.?\s*)?\d+)?\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:bdrip|dvdrip|webrip|web[-\s]?dl|blu[\s-]*ray(?:\s*box)?|bd[\s._-]*box|box\s+set|complete\s+(?:series|collection|box|edition)|x26[45]|hevc|avc|flac|aac|2160p|1080p|720p|480p|10bit|8bit|hi10p)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:ep|episode|e)\s*\d{1,3}\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"第\s*\d{1,3}\s*[话話集]", " ", cleaned)
        cleaned = re.sub(r"[\[\]【】()（）<>《》._]+", " ", cleaned)
        cleaned = re.sub(r"\s*[-_]+\s*$", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" -_:.!/")

    @classmethod
    def _normalize_title_extraction_key(cls, filename: str) -> str:
        raw_value = str(filename or "").strip()
        if not raw_value:
            return ""

        parts = [part.strip() for part in re.split(r"[\\/]+", raw_value) if part.strip()]
        cleaned_parts: List[str] = []
        for index, part in enumerate(parts):
            part_value = Path(part).stem if index == len(parts) - 1 else part
            cleaned_part = cls._strip_title_extraction_noise(part_value)
            if cleaned_part:
                cleaned_parts.append(cleaned_part)

        normalized = " / ".join(cleaned_parts)
        if not normalized:
            normalized = cls._strip_title_extraction_noise(Path(raw_value).stem) or raw_value
        return normalized.casefold()

    @staticmethod
    def _build_openai_strict_schema(schema_model: Type[BaseModel]) -> Dict:
        """构建符合 OpenAI strict structured output 要求的 JSON Schema。"""
        schema = deepcopy(schema_model.model_json_schema())

        def ensure_required_fields(obj):
            if isinstance(obj, dict):
                properties = obj.get("properties")
                if isinstance(properties, dict):
                    obj["required"] = list(properties.keys())
                    obj["additionalProperties"] = False

                for value in obj.values():
                    ensure_required_fields(value)
            elif isinstance(obj, list):
                for item in obj:
                    ensure_required_fields(item)

        ensure_required_fields(schema)
        return schema

    def __init__(self):
        self.provider = cm.get_config("ai_provider") or "openai"
        self.enabled = True
        self.confidence_threshold = cm.get_config("ai_confidence_threshold")

        # 根据提供商创建相应的客户端
        if self.provider.lower() == "gemini":
            self._client: BaseAIClient = GeminiClient()
        else:  # 默认使用OpenAI
            self._client: BaseAIClient = OpenAIClient()

    def is_available(self) -> bool:
        """检查AI客户端是否可用"""
        return bool(self.enabled and self._client and self._client.is_available())

    def extract_title(self, filename: str) -> Optional[str]:
        """
        使用AI从复杂的文件名中提取动漫/电影标题

        Args:
            filename: 原始文件名

        Returns:
            提取出的标题，如果失败则返回None
        """
        result = self.extract_title_metadata(filename)
        if result:
            return result.title
        return None

    def extract_title_metadata(
        self, filename: str
    ) -> Optional[TitleExtractionResult]:
        """使用结构化输出提取标题、回退标题与内容类型。"""
        if not self.is_available():
            logger.warning(f"[AI提取标题] AI功能未启用或{self.provider}客户端不可用")
            return None

        cache_key = self._normalize_title_extraction_key(filename)
        cached_result = self._title_cache_get(cache_key)
        if cached_result:
            logger.info(
                f"[AI提取标题] 命中进程缓存: {filename} -> {cached_result.title}"
            )
            return cached_result

        logger.info(f"[AI提取标题] 使用 {self.provider.upper()} 提取标题: {filename}")

        try:
            prompt = f"""从以下文件名中提取动漫或电影的搜索标题，并判断内容类型。

文件名: {filename}

返回要求：
1. title：最优先的 TMDB 查询标题
   - 返回最可能直接搜到结果的正式作品名
   - 不要包含字幕组名、分辨率、编码、来源、集数等噪音信息
   - 不要包含“剧场版”“劇場版”“theatrical”“movie”等泛化前缀，除非它本身就是作品正式名的一部分
2. fallback_title：仅当 title 搜不到时再尝试的基础回退标题
   - 只返回一个最有价值的回退词，不是 alias 列表
   - 当 title 本身已经足够基础，或没有更好的回退词时，返回 null
   - 常用于去掉版本名、副标题、续作标记、修饰后缀等，让标题更基础
3. type：根据文件名判断是 "movie"、"tv" 或 null

类型判断依据：
- 包含"劇場版"、"剧场版"、"MOVIE"、"movie"、"Film"、"theatrical"、"Movie"等关键词 → movie
- 包含"OVA"、"OAD"但不是剧场版 → tv
- 文件名中有明确的季度信息（S01、第一季等）→ tv
- 文件名中有剧集编号格式（E01、第01话等）→ tv
- 不确定时可返回 tv；若确实无法判断也可返回 null

请严格按照以下JSON格式返回，不要有其他文字：
{{
  "title": "主搜索标题",
  "fallback_title": "备选基础标题或null",
  "type": "movie或tv或null"
}}

示例：
输入: [LoliHouse] 葬送的芙莉莲 / Sousou no Frieren [01-28 Fin][WebRip 1080p]
输出: {{"title": "葬送的芙莉莲", "fallback_title": null, "type": "tv"}}

输入: [AI-Raws][劇場版 空の境界][MOVIE 01-09][BDRip]
输出: {{"title": "空之境界", "fallback_title": null, "type": "movie"}}

输入: [VCB-Studio] Fate Zero [Ma10p_1080p]
输出: {{"title": "Fate/Zero", "fallback_title": null, "type": "tv"}}

输入: [字幕组] 生徒会の一存 Lv.2 [BDRip]
输出: {{"title": "生徒会の一存 Lv.2", "fallback_title": "生徒会の一存", "type": "tv"}}"""

            system_prompt = (
                "你是一个专业的动漫文件命名解析助手。"
                "你的任务是从复杂的文件名中准确提取出动漫或电影的标题、"
                "回退标题，并判断内容类型。只输出JSON格式结果，不要有任何额外的解释。"
            )

            if self.provider.lower() == "gemini":
                result = self._call_gemini_simple(
                    system_prompt,
                    prompt,
                    schema=TitleExtractionResult,
                )
            else:
                result = self._call_openai_simple(
                    system_prompt,
                    prompt,
                    validation_key="title",
                    schema=TitleExtractionResult,
                )

            if not result:
                return None

            parsed = TitleExtractionResult.model_validate_json(result)
            self._title_cache_set(cache_key, parsed)
            logger.info(
                "[AI提取标题] 提取结果: "
                f"title={parsed.title}, fallback_title={parsed.fallback_title}, "
                f"type={parsed.type}"
            )
            return parsed
        except ValidationError as e:
            logger.error(f"[AI提取标题] 结构化解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[AI提取标题] 提取失败: {e}")
            return None

    def extract_title_and_type(
        self, filename: str
    ) -> Optional[tuple[str, Optional[str]]]:
        """
        使用AI从复杂的文件名中提取动漫/电影标题和类型

        Args:
            filename: 原始文件名

        Returns:
            元组 (标题, 类型)，类型为 "movie" 或 "tv" 或 None
            如果失败则返回 None
        """
        result = self.extract_title_metadata(filename)
        if not result:
            return None
        return (result.title, result.type)

    def generate_movie_search_queries(
        self,
        movie_title: str,
        collection_name: Optional[str] = None,
    ) -> Optional[List[str]]:
        """使用AI为单部电影生成TMDB搜索查询候选列表。"""
        if not self.is_available():
            return None

        collection_hint = (
            f"\n所属系列: {collection_name}" if collection_name else ""
        )
        prompt = f"""为以下电影标题生成最多5条TMDB搜索查询候选，按命中可能性从高到低排序。

电影标题: {movie_title}{collection_hint}

要求：
1. 第一条必须是最可能直接在TMDB搜到的正式标题（优先使用中文官方译名）
2. 可以包含：去掉章节前缀的版本、原文/译文互换、去掉副标题的基础名
3. 不要包含文件名噪音（分辨率、字幕组、编码等）
4. 斜线分隔的多个别名（如"A/B"）应拆成独立查询
5. 最多5条，去掉重复

示例：
标题: 空の境界 終章/空の境界, 系列: 空の境界
输出: {{"queries": ["空之境界 终章", "空の境界 終章", "空之境界", "空の境界"]}}

标题: 空の境界 第三章 痛覚残留, 系列: 空の境界
输出: {{"queries": ["空之境界 第三章 痛觉残留", "空の境界 痛覚残留", "空之境界 痛觉残留", "空の境界 第三章 痛覚残留"]}}

请严格按照JSON格式返回：
{{"queries": ["查询1", "查询2", ...]}}"""

        system_prompt = (
            "你是一个专业的动漫数据库搜索专家，熟悉TMDB的收录规则和标题命名习惯。"
            "只输出JSON格式结果，不要有任何额外解释。"
        )

        try:
            if self.provider.lower() == "gemini":
                result = self._call_gemini_simple(
                    system_prompt,
                    prompt,
                    schema=MovieSearchQueriesResult,
                )
            else:
                result = self._call_openai_simple(
                    system_prompt,
                    prompt,
                    validation_key="queries",
                    schema=MovieSearchQueriesResult,
                )

            if not result:
                return None

            parsed = MovieSearchQueriesResult.model_validate_json(result)
            queries = [q for q in parsed.queries if q and q.strip()]
            return queries if queries else None
        except Exception as e:
            logger.warning(f"[AI查询生成] 生成失败: {e}")
            return None

    def generate_subtitle_search_queries(
        self,
        task_data: Dict,
    ) -> Optional[List[str]]:
        """使用AI为字幕自动抓取生成补充搜索词。"""
        if not self.is_available():
            return None

        title = str(task_data.get("tmdb_name") or task_data.get("name") or "").strip()
        if not title:
            return None

        media_type = "电影" if task_data.get("is_movie") else "剧集"
        season_id = task_data.get("season_id")
        year_value = task_data.get("tmdb_year") or task_data.get("year")
        source_path_basename = str(
            task_data.get("subtitle_auto_fetch_source_path_basename") or ""
        ).strip()
        source_title_hint = str(
            task_data.get("subtitle_auto_fetch_source_title_hint") or ""
        ).strip()
        source_video_names = task_data.get("subtitle_auto_fetch_source_video_names") or []
        target_video_names = (
            task_data.get("subtitle_auto_fetch_missing_target_video_names") or []
        )
        scan_scope_root = str(
            task_data.get("subtitle_auto_fetch_scan_scope_root") or ""
        ).strip()
        is_season_zero_tv = bool(
            task_data.get("subtitle_auto_fetch_is_season_zero_tv")
        )
        existing_keywords = task_data.get("subtitle_auto_fetch_existing_keywords") or []

        context_lines = [
            f"标题: {title}",
            f"类型: {media_type}",
        ]
        if isinstance(season_id, int) and season_id > 0 and not task_data.get("is_movie"):
            context_lines.append(f"季度: S{season_id:02d}")
        if year_value:
            context_lines.append(f"年份: {year_value}")
        if is_season_zero_tv:
            context_lines.append("目标是 TV Season 0 / 特别篇 / 剧场版相关条目")
        if source_path_basename:
            context_lines.append(f"源目录名: {source_path_basename}")
        if source_title_hint:
            context_lines.append(f"源标题线索: {source_title_hint}")
        if source_video_names:
            context_lines.append(
                f"源视频文件: {' | '.join(map(str, source_video_names[:3]))}"
            )
        if target_video_names:
            context_lines.append(
                f"缺字幕目标文件: {' | '.join(map(str, target_video_names[:3]))}"
            )
        if scan_scope_root:
            context_lines.append(f"扫描根目录: {scan_scope_root}")
        if existing_keywords:
            context_lines.append(
                f"已尝试搜索词: {' | '.join(map(str, existing_keywords[:8]))}"
            )

        prompt = f"""请为以下字幕自动抓取任务生成最多5条补充搜索词，按命中可能性从高到低排序。

任务上下文：
{chr(10).join(context_lines)}

要求：
1. 只生成搜索词候选，不要输出解释文字
2. 最多5条，去掉重复
3. 可以输出中文、日文、英文或罗马字别名
4. 可以把同一篇章的常见译名/标题写法互相转换，例如剧场版、篇章名、正式副标题
5. 对普通 TV 条目（非电影、非 Season 0 / 特别篇 / 剧场版相关条目），如果标题明显是“系列名 + 副标题/篇章名”，可以补 1 条更宽一点的基础系列名作为最后兜底搜索词
6. 对 Season 0 / 特别篇 / 剧场版相关条目，不能退化成过宽的系列大词
7. 不要包含字幕组名、分辨率、编码、来源、BDRip、WebRip 等噪音
8. 不要凭空发明上下文完全没有依据的新篇章名或新作品名
9. 若已尝试搜索词里已经有某个写法，不要重复输出

示例：
- 若标题是“夜樱四重奏：花之歌”，普通 TV 条目，可以输出“夜樱四重奏”作为较宽搜索词之一
- 若标题线索明确指向“鬼灭之刃 无限列车篇 / Mugen Ressha Hen”，则不能只输出“鬼灭之刃”这种过宽系列词

请严格按照JSON格式返回：
{{"queries": ["查询1", "查询2", ...]}}"""

        system_prompt = (
            "你是一个动漫字幕站搜索助手，擅长把同一作品整理成更容易命中字幕站的多语言标题写法。"
            "你只能输出少量高质量搜索词，不要扩展成宽泛系列词，不要输出解释。"
        )

        try:
            if self.provider.lower() == "gemini":
                result = self._call_gemini_simple(
                    system_prompt,
                    prompt,
                    schema=SubtitleSearchQueriesResult,
                )
            else:
                result = self._call_openai_simple(
                    system_prompt,
                    prompt,
                    validation_key="queries",
                    schema=SubtitleSearchQueriesResult,
                )

            if not result:
                return None

            parsed = SubtitleSearchQueriesResult.model_validate_json(result)
            queries = [q for q in parsed.queries if q and q.strip()]
            return queries if queries else None
        except Exception as e:
            logger.warning(f"[AI字幕搜索词] 生成失败: {e}")
            return None

    def _call_openai_simple(
        self,
        system_prompt: str,
        prompt: str,
        max_retries: int = 2,
        validation_key: str = "title",
        schema: Optional[Type[BaseModel]] = None,
    ) -> Optional[str]:
        """简单调用OpenAI API获取文本响应，支持重试和结构化输出

        Args:
            system_prompt: 系统提示词
            prompt: 用户提示词
            max_retries: 最大重试次数
            validation_key: 用于验证响应完整性的JSON键名，默认为"title"
            schema: 可选的Pydantic模型类，用于结构化输出
        """
        output_format = cm.get_config("openai_output_format") or "function_calling"

        for attempt in range(max_retries + 1):
            try:
                client = self._client
                if not hasattr(client, 'client') or not client.client:
                    return None

                request_params = {
                    "model": client.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": client.temperature,
                    "max_tokens": 16384,  # 限制输出长度，避免无限生成
                }

                # 根据配置和schema添加结构化输出参数
                if schema and output_format != "json_object":
                    request_params["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__.lower(),
                            "strict": True,
                            "schema": self._build_openai_strict_schema(schema),
                        },
                    }
                elif schema and output_format == "json_object":
                    request_params["response_format"] = {"type": "json_object"}

                interface = "responses_api"
                if hasattr(client, "_resolve_api_interface"):
                    interface = client._resolve_api_interface(
                        cm.get_config("openai_api_interface")
                    )

                content = None
                actual_interface = interface

                if (
                    interface == "responses_api"
                    and hasattr(client, "_call_via_responses_api")
                    and hasattr(client, "_convert_chat_request_to_responses")
                ):
                    try:
                        message = client._call_via_responses_api(request_params)
                        content = message.get("content") if isinstance(message, dict) else None
                        actual_interface = "responses_api"
                    except Exception as e:
                        logger.warning(
                            "[AI] 简单调用 responses_api 失败，回退 chat_completions: "
                            f"{e}"
                        )
                        message = client._call_via_chat_completions(request_params)
                        content = message.get("content") if isinstance(message, dict) else None
                        actual_interface = "chat_completions"
                else:
                    message = client._call_via_chat_completions(request_params)
                    content = message.get("content") if isinstance(message, dict) else None
                    actual_interface = "chat_completions"

                if hasattr(client, "last_configured_api_interface"):
                    client.last_configured_api_interface = interface
                if hasattr(client, "last_actual_api_interface"):
                    client.last_actual_api_interface = actual_interface
                if hasattr(client, "last_api_interface_fallback"):
                    client.last_api_interface_fallback = (
                        interface == "responses_api"
                        and actual_interface == "chat_completions"
                    )
                if hasattr(client, "last_api_interface_fallback_reason"):
                    client.last_api_interface_fallback_reason = (
                        "simple_call responses_api 调用失败，自动回退 chat_completions"
                        if (
                            interface == "responses_api"
                            and actual_interface == "chat_completions"
                        )
                        else ""
                    )

                logger.info(
                    "[AI] 简单调用OpenAI接口: "
                    f"configured={interface}, actual={actual_interface}"
                )

                if content:
                    # 检查返回内容是否是完整的JSON（有开闭括号）
                    if content and f'"{validation_key}"' in content and '}' in content:
                        return content
                    elif attempt < max_retries:
                        logger.warning(
                            f"[AI] 响应格式不完整，重试第{attempt + 1}次"
                        )
                        continue
                    return content

                return None
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(
                        f"[AI] OpenAI调用失败，重试第{attempt + 1}次: {e}"
                    )
                    continue
                logger.error(f"[AI] OpenAI调用失败: {e}")
                return None
        return None

    def _call_gemini_simple(
        self,
        system_prompt: str,
        prompt: str,
        max_retries: int = 2,
        validation_key: str = "title",
        schema: Optional[Type[BaseModel]] = None,
    ) -> Optional[str]:
        """简单调用Gemini API获取文本响应，支持重试和结构化输出

        Args:
            system_prompt: 系统提示词
            prompt: 用户提示词
            max_retries: 最大重试次数
            validation_key: 用于验证响应完整性的JSON键名，默认为"title"
            schema: 可选的Pydantic模型类，用于结构化输出
        """
        # Gemini 使用结构化输出时需要配置
        use_structured = schema is not None

        for attempt in range(max_retries + 1):
            try:
                client = self._client
                if not hasattr(client, 'client') or not client.client:
                    return None

                full_prompt = f"{system_prompt}\n\n{prompt}"

                if use_structured:
                    # 使用结构化输出
                    config = GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema.gemini_json_schema(),
                        temperature=client.temperature,
                        max_output_tokens=16384,  # 限制输出长度
                    )
                    response = client.client.models.generate_content(
                        model=client.model,
                        contents=full_prompt,
                        config=config,
                    )
                else:
                    # 普通文本输出
                    config = GenerateContentConfig(
                        temperature=client.temperature,
                        max_output_tokens=16384,  # 限制输出长度
                    )
                    response = client.client.models.generate_content(
                        model=client.model,
                        contents=full_prompt,
                        config=config,
                    )

                if response and response.text:
                    content = response.text
                    # 检查返回内容是否是完整的JSON（有开闭括号）
                    if content and f'"{validation_key}"' in content and '}' in content:
                        return content
                    elif attempt < max_retries:
                        logger.warning(
                            f"[AI] 响应格式不完整，重试第{attempt + 1}次"
                        )
                        continue
                    return content

                return None
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(
                        f"[AI] Gemini调用失败，重试第{attempt + 1}次: {e}"
                    )
                    continue
                logger.error(f"[AI] Gemini调用失败: {e}")
                return None
        return None

    def analyze_episode_mapping(
        self,
        anime_info: Dict,
        local_files: List[Dict],
        bangumi_context: Optional[Dict] = None,
    ) -> Optional[AIAnalysisResult]:
        """
        分析本地文件与TMDB剧集的映射关系

        Args:
            anime_info: TMDB动漫信息
            local_files: 本地文件信息列表，包含文件名、路径、时长等
            bangumi_context: Bangumi 辅助上下文，失败时为 None

        Returns:
            验证后的AIAnalysisResult对象
        """
        if not self.is_available():
            logger.warning(f"[AI识别] AI功能未启用或{self.provider}客户端不可用")
            return None

        logger.info(f"[AI识别] 使用 {self.provider.upper()} 进行分析")
        result = self._client.analyze_episode_mapping(
            anime_info,
            local_files,
            bangumi_context=bangumi_context,
        )

        # 统一在此处保存分析数据
        self._save_analysis_snapshot(
            analysis_kind="tv_episode_mapping",
            source_name=anime_info.get("name", "unknown"),
            source_payload=anime_info,
            local_files=local_files,
            result=result,
            force=not bool(result),
            bangumi_context=bangumi_context,
        )

        # 保持兼容：沿用 provider 客户端原保存逻辑（受 ai_auto_save 控制）
        self._client._save_analysis_data(
            anime_info,
            local_files,
            result,
            bangumi_context=bangumi_context,
        )

        return result

    @staticmethod
    def _looks_like_special_path(path_value: str) -> bool:
        normalized = str(path_value or '').replace('\\', '/').casefold()
        if any(folder in normalized for folder in SPECIAL_FOLDER_NAMES):
            return True
        upper_value = Path(str(path_value or '')).name.upper()
        if any(tag.upper() in upper_value for tag in PROMO_TAGS):
            return True
        if re.search(r'\b(?:ova|oad|special|sp)\b', normalized, re.IGNORECASE):
            return True
        if re.search(r'(?<!\d)(?:0|\d+\.5)(?!\d)', upper_value):
            return True
        return False

    @staticmethod
    def _extract_episode_hints(local_files: List[Dict]) -> Tuple[set[int], bool]:
        episode_numbers: set[int] = set()
        should_include_season_zero = False

        for file_info in local_files:
            path_value = str(file_info.get('path') or file_info.get('filename') or '')
            if not path_value:
                continue
            if AIClient._looks_like_special_path(path_value):
                should_include_season_zero = True

            candidates = [
                Path(path_value).stem,
                remove_season(Path(path_value).stem),
                remove_episode(remove_season(Path(path_value).stem)),
            ]
            for candidate in candidates:
                for match in re.finditer(r'(?<!\d)(\d{1,3})(?:\.5)?(?!\d)', candidate):
                    episode_num = int(match.group(1))
                    if 0 < episode_num <= 999:
                        episode_numbers.add(episode_num)
                if re.search(r'(?<!\d)\d+\.5(?!\d)', candidate):
                    should_include_season_zero = True
        return episode_numbers, should_include_season_zero

    @staticmethod
    def _select_prompt_seasons(anime_info: Dict, local_files: List[Dict]) -> List[Dict]:
        seasons = [
            season
            for season in anime_info.get('seasons', [])
            if isinstance(season, dict)
        ]
        if not seasons:
            return []

        preferred_seasons: set[int] = set()
        episode_numbers, should_include_season_zero = AIClient._extract_episode_hints(
            local_files
        )
        has_non_special_file = False

        name_candidates = [
            str(anime_info.get('name') or ''),
            str(anime_info.get('original_name') or ''),
            str(anime_info.get('original_title') or ''),
        ]
        for file_info in local_files:
            path_value = str(file_info.get('path') or file_info.get('filename') or '')
            if not path_value:
                continue
            if not AIClient._looks_like_special_path(path_value):
                has_non_special_file = True
            path_name = Path(path_value).stem
            for text in [path_value, path_name, remove_episode(path_name)] + name_candidates:
                for match in re.finditer(
                    r'\b(?:season|s)\s*0*([0-9]{1,2})\b',
                    text,
                    re.IGNORECASE,
                ):
                    preferred_seasons.add(int(match.group(1)))
                for match in re.finditer(r'第\s*([0-9]{1,2})\s*季', text):
                    preferred_seasons.add(int(match.group(1)))

        explicit_preferred_seasons = set(preferred_seasons)
        selected_numbers: set[int] = set(explicit_preferred_seasons)
        if should_include_season_zero:
            selected_numbers.add(0)

        if episode_numbers and not explicit_preferred_seasons:
            season_match_counts: Dict[int, int] = {}
            for season in seasons:
                season_number = season.get('season_number')
                if not isinstance(season_number, int):
                    continue
                episode_numbers_in_season = {
                    ep.get('episode_number')
                    for ep in season.get('episodes', []) or []
                    if isinstance(ep, dict)
                    and isinstance(ep.get('episode_number'), int)
                    and ep.get('episode_number') > 0
                }
                direct_match_count = len(episode_numbers_in_season & episode_numbers)
                if direct_match_count > 0:
                    season_match_counts[season_number] = direct_match_count

            non_special_match_counts = {
                season_number: match_count
                for season_number, match_count in season_match_counts.items()
                if season_number != 0
            }
            if non_special_match_counts:
                best_match_count = max(non_special_match_counts.values())
                selected_numbers.add(
                    min(
                        season_number
                        for season_number, match_count in non_special_match_counts.items()
                        if match_count == best_match_count
                    )
                )
            elif has_non_special_file:
                return seasons

        if not selected_numbers or len(selected_numbers) >= len(seasons):
            return seasons

        filtered_seasons = [
            season
            for season in seasons
            if season.get('season_number') in selected_numbers
        ]
        return filtered_seasons or seasons

    @staticmethod
    def _build_tmdb_prompt_section(anime_info: Dict, local_files: List[Dict]) -> str:
        seasons = anime_info.get('seasons', []) or []
        prompt_seasons = AIClient._select_prompt_seasons(anime_info, local_files)

        lines = [
            'TMDB 季度摘要：',
        ]
        for season in seasons:
            season_num = season.get('season_number', 0)
            season_name = season.get('name', f'Season {season_num}')
            episode_count = season.get('episode_count', 0)
            lines.append(
                f"- Season {season_num}: {season_name} (共 {episode_count} 集)"
            )

        lines.append('')
        lines.append('TMDB 候选季度详细集目：')
        for season in prompt_seasons:
            season_num = season.get('season_number', 0)
            season_name = season.get('name', f'Season {season_num}')
            episodes = season.get('episodes', []) or []
            episode_count = len(episodes) if episodes else season.get('episode_count', 0)
            lines.append(
                f"【Season {season_num}】{season_name} (共 {episode_count} 集)"
            )
            if episodes:
                for ep in episodes:
                    ep_num = ep.get('episode_number', 0)
                    ep_name = ep.get('name', '')
                    runtime = ep.get('runtime')
                    line = f"  S{season_num:02d}E{ep_num:02d}: {ep_name}"
                    if runtime:
                        line += f" (runtime={runtime}m)"
                    lines.append(line)
            elif episode_count > 0:
                lines.append(f"  E01 - E{episode_count:02d}")
            lines.append('')

        if len(prompt_seasons) < len(seasons):
            lines.append(
                '提示: 以上只展开高相关季度；最终仍只能映射到全部 TMDB 真实存在的 SxxExx。'
            )
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _build_bangumi_prompt_section(bangumi_context: Optional[Dict]) -> str:
        if not bangumi_context:
            return "Bangumi 辅助上下文：不可用（本次按 TMDB-only 处理）\n"

        subjects = bangumi_context.get("subjects", []) or []
        if not subjects:
            return "Bangumi 辅助上下文：不可用（本次按 TMDB-only 处理）\n"

        lines = [
            "Bangumi 辅助上下文（仅作辅助证据，不能直接决定最终季号）：",
            f"主条目 ID: {bangumi_context.get('selected_subject_id', '未知')}",
        ]
        reason = str(bangumi_context.get("selected_subject_reason") or "").strip()
        if reason:
            lines.append(f"主条目选择原因: {reason}")
        keywords = bangumi_context.get("search_keywords", []) or []
        if keywords:
            lines.append(f"搜索词: {', '.join(str(item) for item in keywords[:6])}")

        for subject_item in subjects[:4]:
            subject = subject_item.get("subject", {}) or {}
            relation = subject_item.get("relation_to_main", "") or "main"
            lines.append(
                "- "
                f"subject_id={subject.get('id', '未知')} "
                f"relation={relation} "
                f"title={subject.get('name_cn') or subject.get('name') or '未知'}"
            )
            alt_name = subject.get("name") or ""
            if alt_name and alt_name != (subject.get("name_cn") or ""):
                lines.append(f"  原标题: {alt_name}")
            if subject.get("date"):
                lines.append(f"  放送日期: {subject.get('date')}")
            if subject.get("platform"):
                lines.append(f"  平台: {subject.get('platform')}")

            episodes = subject_item.get("episodes", []) or []
            if not episodes:
                lines.append("  episodes: 无")
                continue

            lines.append("  episodes:")
            for episode in episodes[:60]:
                episode_type = episode.get("type")
                duration_seconds = episode.get("duration_seconds")
                duration_text = episode.get("duration") or ""
                episode_line = (
                    "    - "
                    f"sort={episode.get('sort', 0)} "
                    f"ep={episode.get('ep')} "
                    f"type={episode_type} "
                    f"airdate={episode.get('airdate') or ''} "
                    f"title={episode.get('name_cn') or episode.get('name') or ''}"
                )
                if duration_seconds:
                    episode_line += f" duration_seconds={duration_seconds}"
                elif duration_text:
                    episode_line += f" duration={duration_text}"
                lines.append(episode_line)
                desc = str(episode.get("desc") or "").strip()
                if desc:
                    lines.append(f"      desc={desc[:120]}")

        lines.append(
            "Bangumi 使用规则：relation 只是辅助语义，不等于 TMDB season；"
            "最终输出只能使用上面 TMDB 中真实存在的 SxxExx；"
            "拿不准时宁可放到 unmatched_files。"
        )
        lines.append(
            "若文件名只出现 `OVA3 / SP3 / [13]` 这类顺序编号，"
            "可以把 Bangumi 的 `sort / ep / type / 标题 / 日期 / 时长 / desc` 当作辅助证据，"
            "先判断它是不是 special，再回到 TMDB 真实存在的 Season 0 条目；"
            "但 `OVA3` 不等于 `S00E03`，最终仍要按 TMDB 合法条目落点。"
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def build_common_prompt(
        anime_info: Dict,
        local_files: List[Dict],
        bangumi_context: Optional[Dict] = None,
    ) -> str:
        """
        构建通用的分析提示词，不包含JSON格式要求

        Args:
            anime_info: TMDB动漫信息
            local_files: 本地文件信息列表，包含文件名、路径、时长等
            bangumi_context: Bangumi 辅助上下文，失败时为 None

        Returns:
            通用的分析提示词
        """
        # 构建TMDB信息
        tmdb_info = f"""
动漫名称: {anime_info.get('name', '未知')}
首播日期: {anime_info.get('first_air_date', '未知')}
总季数: {anime_info.get('number_of_seasons', 0)}
总集数: {anime_info.get('number_of_episodes', 0)}
"""
        tmdb_details = AIClient._build_tmdb_prompt_section(anime_info, local_files)

        # 构建本地文件信息
        files_info = "本地文件信息（每行一个稳定 source_index + 可直接复制的 source_path，路径均为相对路径）:\n"
        top_level_dirs: list[str] = []
        for i, file_info in enumerate(local_files, 1):
            path_value = str(file_info.get("path") or "")
            duration_str = ""
            if file_info.get("duration"):
                duration_str = f" (时长: {file_info['duration']:.1f}分钟)"
            files_info += f"  - [{i:03d}] source_index={i} source_path=`{path_value}`{duration_str}\n"

            parts = [part for part in path_value.split("/") if part]
            if len(parts) >= 2:
                top_dir = parts[0]
                if top_dir not in top_level_dirs:
                    top_level_dirs.append(top_dir)

        if top_level_dirs:
            files_info += (
                "顶层子目录: "
                + ", ".join(top_level_dirs[:12])
                + "\n"
            )
            files_info += (
                "提示: 如果不同文件落在不同子目录（如 Disc/SP/OVA/NCOP/NCED/Extras），"
                "这些子目录语义通常很重要，不能忽略。\n"
            )

        # 构建 Bangumi 辅助上下文
        bangumi_info = AIClient._build_bangumi_prompt_section(bangumi_context)

        prompt = f"""请分析以下动漫的本地文件与TMDB数据的对应关系：

{tmdb_info}

{tmdb_details}

{bangumi_info}

{files_info}

请根据以下规则进行映射：

1. **输出字段要求**：
   - 每个 `file_mapping` 项必须包含：`source_index`, `tmdb_season`, `tmdb_episode`, `episode_type`, `confidence`
   - `file_path` 为可选；若填写，必须与 `source_index` 指向同一输入文件

2. **匹配优先级**（按顺序尝试）：
   - 文件名中的集数与 TMDB 集数号直接匹配
   - 文件名中的标题与 TMDB 集标题相似度匹配
   - 文件名中的日期与 TMDB 播出日期匹配

2. **Season 0 特典规则**：
   - OVA、OAD、SP、Special 等标签 → Season 0
   - 小数集数（如 5.5、12.5）→ Season 0（总集篇）
   - 第00集、E00、[00] → Season 0（序章/先行篇）
   - **重要**: 文件名中的 SP01、OVA01 不一定对应 S0E1，需要根据标题匹配
   - 如果 TMDB 条目是“有声小说 / audio drama / sound novel”类 special，而本地文件名更像 `Talk / Event / Cast / Seiyuu / Radio / Day Ver / Ending Talk / Recitation`，则不要强行映射，宁可放入 `unmatched_files`
   - `Part1/Part2` 只有在能明确证明它们是同一条 TMDB special 的拆分文件时才能映射；否则放入 `unmatched_files`

3. **多季度处理**：
   - 本地目录可能将多季合并，需要根据集数范围判断
   - 本地目录可能使用总集号（如 E14 可能是 S2E01）
   - 不同季度可能仅用名称区分（如 \"Okawari\"、\"Okaeri\" 等后缀）

4. **只输出匹配到的文件**，未匹配到 TMDB 的文件不要输出

5. **路径约束（必须满足）**：
   - `file_mapping` 中优先填写 `source_index`
   - `source_index` 必须直接使用上面 `[编号]` 里的数字（例如 `[001]` 就填 `1`）
   - 若已提供正确 `source_index`，`file_path` 可留空或填写与该编号完全一致的原始相对路径
   - 如果同时填写 `source_index` 和 `file_path`，两者必须指向同一条输入文件
   - `file_mapping.file_path` 若填写，必须逐字复用输入里的相对路径
   - 只能从上面 `[编号] source_index=... source_path=` 列表里原样选择，禁止自行拼接、改写或脑补任何新路径
   - 如果你要输出某个文件，先定位对应的 `[编号]` 行；最佳做法是直接返回该行的 `source_index`
   - 若你额外填写 `file_path`，必须逐字复制该行反引号中的 `source_path`
   - 必须复制完整相对路径，不能截断为 basename，也不能省略中间子目录
   - 路径中的目录名、文件名、以及 `[]` 内的版本标签都属于路径的一部分；`Ma10p / Hi10p / x265 / x264 / flac / aac` 等字样也必须逐字照抄，不能替换成“更统一”的版本
   - `[]` 内外的 token 顺序、空格和简称都必须保持原样；不要把短标签/简称扩写回主标题，也不要把多个片段重新拼成新的文件名
   - 例如：若输入是 `.../SPs/[Group] MagiRepo [01][Ma10p_1080p][x265_flac].mkv`，就不能改写成 `.../[Group] Magia Record [MagiRepo 01][Ma10p_1080p][x265_flac].mkv`
   - 例如：若输入是 `.../[SP01][Hi10p_1080p][x264_flac].mkv`，就不能改写成 `.../[SP01][Ma10p_1080p][x265_flac].mkv`
   - 即使同一作品的大多数文件都落在某个子目录中，也不能据此给其他文件自动补同样的子目录；文件是在根目录、`SPs/` 还是其他子目录，只能以输入列表为准
   - 不要补 base folder 名，不要改写目录层级
   - 不要只输出 basename
   - 不要混用新的路径分隔符格式

6. **可观测性要求（必须满足）**：
   - 返回 `unmatched_files`，列出所有未匹配文件路径（相对路径）
   - `conflict_details` 仅填写“硬冲突”（例如：重复映射、集数越界、文件不存在）
   - 证据不足/不确定但可执行的说明（例如仅凭小数集数推断）请写入 `extra_notes`，不要写入 `conflict_details`
   - 如果 confidence 为 High/Medium，`file_mapping` 必须尽量覆盖可匹配文件

7. **TMDB 合法性约束**：
   - 只能使用上面 TMDB 列表中真实存在的 `SxxExx`
   - 不要因为文件名带 `SP / OVA / Part` 就自行发明新的 Season 0 / special 编号
   - 对拿不准或 TMDB 中不存在的文件，宁可放入 `unmatched_files`

8. **Bangumi 使用约束**：
   - Bangumi 只作为辅助证据，不是最终编号体系
   - Bangumi 的 relation（如前传 / 续集 / 番外篇 / 总集篇）不能直接等价为某个 TMDB season
   - 当本地文件只有编号或标题模糊时，可以参考 Bangumi 的 `sort / ep / type / 标题 / 日期`
   - 如果 Bangumi 与 TMDB 存在结构差异，最终仍必须回到 TMDB 已存在的 `SxxExx`
   - 无法自信桥接时，宁可将文件放入 `unmatched_files`

9. **复杂目录处理约束**：
   - 多子目录场景下，必须同时结合“文件名 + 所在子目录语义 + 时长”判断
   - `SP / OVA / OAD / Special / NCOP / NCED / Extras / Bonus / Menu` 等目录或标签通常不是正片
   - 如果输入列表里同时存在“根目录文件 + 子目录文件”或“同编号不同版本文件”，必须按输入中那条完整路径逐字复制；不要把别的文件所在目录或版本标签套到当前文件上
   - 如果只能确定部分文件，优先输出有把握的合法映射，其余文件放入 `unmatched_files`
   - 对 `SP01 / SP02 / OVA01` 这类 special 编号，若只能看到近似路径但无法确认哪一条才是输入中的真实文件，宁可放入 `unmatched_files`
   - 如果某个 `source_index` 已能确定合法落点，但你不确定自己是否能把 `file_path` 逐字抄对，就只返回 `source_index`，不要硬写一个可能出错的 `file_path`
   - 当目录里同时包含 TMDB 已存在的正片/特典与 TMDB 不存在的附加短片时，先覆盖可合法落点的那部分，其余放入 `unmatched_files`
   - 不要因为目录复杂就返回空的 `file_mapping`
   - 不要因为仍有一部分 extras / SP 无法落到 TMDB，就返回空的 `file_mapping`

"""
        return prompt

    @staticmethod
    def get_system_prompt() -> str:
        """
        获取通用的系统提示词

        Returns:
            系统提示词
        """
        return (
            "你是一个专业的动漫文件重命名助手。你需要分析本地动漫文件与TMDB数据库中剧集信息的对应关系，特别关注动漫BD发布与官方分季的差异。"
            + "请你只输出匹配到的季度和剧集信息，不要输出其他未匹配到tmdb信息的内容。"
        )

    def analyze_movie_collection(
        self,
        folder_name: str,
        local_files: List[Dict],
    ) -> Optional[MovieCollectionResult]:
        """
        分析电影合集目录，识别每个文件对应的电影

        Args:
            folder_name: 文件夹名称
            local_files: 本地文件信息列表

        Returns:
            MovieCollectionResult对象或None
        """
        if not self.is_available():
            logger.warning(f"[AI电影合集] AI功能未启用或{self.provider}客户端不可用")
            return None

        logger.info(f"[AI电影合集] 使用 {self.provider.upper()} 分析电影合集")

        # 构建文件列表
        files_info = "本地文件信息 (路径均为相对路径):\n"
        for file_info in local_files:
            duration_str = ""
            if file_info.get("duration"):
                duration_str = f" (时长: {file_info['duration']:.1f}分钟)"
            files_info += f"  {file_info['path']}{duration_str}\n"

        prompt = f"""请分析以下电影合集目录:

文件夹名称: {folder_name}

{files_info}

请判断:
1. 这是否是一个电影合集（多部电影在同一个文件夹中）
2. 如果是合集，提取合集名称（系列名称）
3. 为每个视频文件识别对应的电影标题

⚠️ **movie_title 字段重要要求**：
- 输出的 movie_title 必须是 TMDB 上能搜索到的标题
- **不要包含** "剧场版"、"劇場版"、"theatrical"、"movie" 等前缀
- 使用电影的正式中文名或日文名
- 例如：文件名 "劇場版 空の境界 #01 俯瞰風景" → movie_title 应为 "空之境界 第一章 俯瞰风景"
- 例如：文件名 "MOVIE 01" → 根据上下文推断完整电影名

注意事项:
- 文件名中的#01、#02等编号通常表示系列中的电影序号
- SP、特典、Preview、PV、CM等通常是附加内容，不要输出这些文件
- 只输出正片电影的映射

⚠️ **输出要求**：
- 只输出 JSON，不要有任何解释或额外文字
- reason 字段简短说明（10-30字）
- extra_notes 通常为 null
- 返回 `unmatched_files`（未匹配文件）和 `conflict_details`（冲突原因）
- 若 confidence 为 High/Medium 且 is_collection=true，应尽量覆盖全部正片文件

请严格按照以下JSON格式返回:
{{
    "is_collection": true或false,
    "collection_name": "合集系列名称",
    "confidence": "High/Medium/Low",
    "reason": "简短分析理由（10-30字）",
    "file_mapping": [
        {{
            "file_path": "相对文件路径",
            "movie_title": "TMDB可搜索的电影标题（不含剧场版等前缀）",
            "movie_number": 电影序号或null,
            "year": 年份或null,
            "confidence": "High/Medium/Low"
        }}
    ],
    "unmatched_files": ["未匹配文件路径"],
    "conflict_details": ["冲突原因"],
    "extra_notes": null
}}"""


        system_prompt = (
            "你是一个专业的电影文件分析助手。你的任务是分析电影合集目录，"
            "识别每个文件对应的具体电影。只输出JSON格式结果。"
        )

        try:
            if self.provider.lower() == "gemini":
                result = self._call_gemini_simple(
                    system_prompt, prompt,
                    validation_key="is_collection",
                    schema=MovieCollectionResult,
                )
            else:
                result = self._call_openai_simple(
                    system_prompt, prompt,
                    validation_key="is_collection",
                    schema=MovieCollectionResult,
                )

            if result:
                result = result.strip()
                logger.debug(f"[AI电影合集] 原始响应: {result[:500]}...")
                # 尝试解析JSON
                try:
                    json_match = re.search(r'\{.*\}', result, re.DOTALL)
                    if json_match:
                        json_str = json_match.group()
                        data = json.loads(json_str)
                        collection_result = MovieCollectionResult(**data)

                        # 兜底补全可观测字段
                        if not collection_result.unmatched_files:
                            mapped = {
                                i.file_path.replace('\\\\', '/').lstrip('/')
                                for i in collection_result.file_mapping
                            }
                            local = {
                                f.get('path', '').replace('\\\\', '/').lstrip('/')
                                for f in local_files
                                if f.get('path')
                            }
                            collection_result.unmatched_files = sorted(local - mapped)

                        logger.info(
                            f"[AI电影合集] 分析完成: "
                            f"is_collection={collection_result.is_collection}, "
                            f"collection_name={collection_result.collection_name}, "
                            f"置信度={collection_result.confidence}, "
                            f"unmatched={len(collection_result.unmatched_files)}, "
                            f"conflicts={len(collection_result.conflict_details)}"
                        )

                        self._save_analysis_snapshot(
                            analysis_kind="movie_collection",
                            source_name=folder_name,
                            source_payload={"folder_name": folder_name},
                            local_files=local_files,
                            result=collection_result,
                            force=False,
                        )
                        return collection_result
                except json.JSONDecodeError as e:
                    logger.error(f"[AI电影合集] JSON解析失败: {e}")
                    logger.debug(f"[AI电影合集] 响应内容: {result}")
                except Exception as e:
                    logger.error(f"[AI电影合集] 模型验证失败: {e}")
                    logger.debug(f"[AI电影合集] 解析的数据: {data if 'data' in dir() else 'N/A'}")

            self._save_analysis_snapshot(
                analysis_kind="movie_collection",
                source_name=folder_name,
                source_payload={"folder_name": folder_name},
                local_files=local_files,
                result=None,
                force=True,
            )
            return None
        except Exception as e:
            logger.error(f"[AI电影合集] 分析失败: {e}")
            self._save_analysis_snapshot(
                analysis_kind="movie_collection",
                source_name=folder_name,
                source_payload={"folder_name": folder_name},
                local_files=local_files,
                result=None,
                force=True,
            )
            return None

    def _save_analysis_snapshot(
        self,
        analysis_kind: str,
        source_name: str,
        source_payload: Dict,
        local_files: List[Dict],
        result: Optional[BaseModel],
        force: bool = False,
        bangumi_context: Optional[Dict] = None,
    ) -> None:
        """保存 AI 分析快照；失败场景可强制落盘。"""
        if not (force or cm.get_config("ai_auto_save")):
            return

        try:
            AI_ANALYSIS_PATH.mkdir(parents=True, exist_ok=True)
            safe_name = (
                source_name.replace('/', '_').replace('\\', '_').strip() or "unknown"
            )
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = (
                f"{safe_name}_{analysis_kind}_{self.provider}_{timestamp}.json"
            )
            output_path = Path(AI_ANALYSIS_PATH) / filename

            payload = {
                "metadata": {
                    "created_at": datetime.now().isoformat(),
                    "analysis_kind": analysis_kind,
                    "provider": self.provider,
                    "source_name": source_name,
                    "file_count": len(local_files),
                    "forced": force,
                },
                "source": source_payload,
                "local_files": local_files,
                "bangumi_context": bangumi_context,
                "analysis_result": result.model_dump() if result else None,
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            logger.info(f"[AI快照] 已保存: {output_path}")
        except Exception as e:
            logger.error(f"[AI快照] 保存失败: {e}")

    def analyze_subtitle_mapping(
        self,
        archive_name: str,
        archive_structure: Dict[str, List[str]],
        processed_tasks: List[Dict],
    ) -> Optional[SubtitleMappingResult]:
        """
        分析字幕文件与已处理任务的映射关系（支持多季度/多任务）

        Args:
            archive_name: 压缩包名称
            archive_structure: 压缩包文件夹结构，格式为 {文件夹路径: [文件名列表]}
                例如: {
                    "/": ["01.ass", "02.ass"],
                    "S1": ["01.ass", "02.ass"],
                    "S2": ["01.ass", "02.ass"],
                }
            processed_tasks: 已处理任务列表，每个包含:
                - uuid: 任务ID
                - title: 动漫名称
                - year: 年份
                - season: 季度
                - target_dir: 目标目录
                - videos: 视频文件名列表

        Returns:
            SubtitleMappingResult 对象，每个字幕可映射到不同任务
        """
        if not self.is_available():
            logger.warning(
                f"[AI字幕匹配] AI功能未启用或{self.provider}客户端不可用"
            )
            return None

        if not archive_structure:
            logger.info("[AI字幕匹配] 无字幕文件需要处理")
            return None

        if not processed_tasks:
            logger.warning("[AI字幕匹配] 无已处理任务可匹配")
            return None

        logger.info(
            f"[AI字幕匹配] 使用 {self.provider.upper()} 分析字幕映射"
        )

        # 构建任务信息
        tasks_info = "已处理的动漫/电影任务列表：\n"
        for task in processed_tasks:
            is_movie = task.get('is_movie', False)
            tasks_info += f"\n任务 UUID: {task['uuid']}\n"
            tasks_info += f"  {'电影' if is_movie else '动漫'}名称: {task.get('title', '未知')}\n"
            if task.get('year'):
                tasks_info += f"  年份: {task['year']}\n"
            if not is_movie:
                tasks_info += f"  季度: Season {task.get('season', 1)}\n"
            else:
                tasks_info += f"  类型: 电影\n"
            tasks_info += f"  目标目录: {task.get('target_dir', '')}\n"
            tasks_info += "  视频文件:\n"
            for video in task.get('videos', []):  # 显示所有视频
                tasks_info += f"    - {video}\n"

        # 构建带结构的字幕信息
        subtitles_info = "压缩包结构（文件夹 -> 字幕文件）：\n"
        total_files = 0
        for folder, files in archive_structure.items():
            subtitles_info += f"\n📁 {folder}/\n"
            for f in files:  # 显示所有字幕
                subtitles_info += f"    - {f}\n"
                total_files += 1

        prompt = f"""请分析以下字幕压缩包与已处理动漫/电影任务的对应关系：

压缩包名称: {archive_name}

{tasks_info}

{subtitles_info}

请完成以下任务：
1. **多季度支持**: 压缩包可能包含多个季度的字幕，分别放在不同文件夹中
   - 例如 S1/, S2/, Season 1/, Season 2/ 等文件夹
   - 每个文件夹中的字幕需要匹配到对应季度的任务
2. **电影支持**: 如果任务类型是电影，则只有一个视频文件
   - 电影字幕通常直接匹配到唯一的电影视频文件
   - 电影可能有多个字幕（不同语言版本）
3. 将每个字幕文件与对应的视频文件进行匹配
4. 识别字幕的语言标签（如 chs/cht/jpn/eng）

⚠️ **最重要的规则 - 必须遵守**：
- **必须处理压缩包中的每一个字幕文件，绝对不允许省略或简化！**
- 即使文件名格式相似（如 01.ass, 02.ass, 03.ass, ..., 13.ass），也必须逐个处理
- **禁止只给出首尾代表**（如只处理 01 和 13 而省略 02-12）
- 每个字幕文件必须出现在 mappings 或 unmatched_files 其中之一
- **mappings 数量 + unmatched_files 数量 = 压缩包中字幕文件总数**

注意事项：
1. 压缩包名可能是罗马音、日文、中文或混合格式
2. 字幕文件名中的 [01]、#01、01 等表示集数（仅适用于剧集）
3. **文件夹名称是判断季度的重要依据**：
   - S1, S2, Season 1, Season 2 → 对应不同季度
   - 根目录 "/" 的字幕可能属于任意季度，需根据文件名判断
4. 语言标签：
   - .sc/.chs = 简体中文
   - .tc/.cht = 繁体中文
   - .jpn/.jp = 日语
   - .eng/.en = 英语
   - 无标签时默认为 chs (简体中文)
5. **重要**: video 字段必须使用视频文件列表中的**精确完整文件名**，不要自己编造或修改！
6. **每个字幕的 task_uuid 可以不同**，取决于它属于哪个季度或电影

请严格按照以下JSON格式返回：
{{
    "mappings": [
        {{
            "subtitle_path": "字幕在压缩包中的相对路径（如 S1/01.ass）",
            "task_uuid": "匹配到的任务UUID",
            "video": "必须是视频文件列表中的精确文件名，原样复制",
            "language": "语言标签(chs/cht/jpn/eng)或null"
        }}
    ],
    "unmatched_files": ["无法匹配的字幕路径1", "无法匹配的字幕路径2"],
    "confidence": "High/Medium/Low",
    "reason": "匹配理由说明，如有无法匹配的文件请说明原因"
}}"""

        system_prompt = (
            "你是一个专业的字幕文件匹配助手。你的任务是分析字幕压缩包中的文件"
            "与已处理的动漫/电影视频文件的对应关系。压缩包可能包含多个季度的字幕，"
            "需要根据文件夹结构和文件名判断每个字幕属于哪个季度/任务/电影。"
            "只输出 JSON 格式结果。"
        )

        max_retries = 2  # 最多重试2次（共3次尝试）
        last_result = None

        for attempt in range(max_retries + 1):
            try:
                # 如果是重试，修改 prompt 强调数量问题
                current_prompt = prompt
                if attempt > 0 and last_result is not None:
                    matched_count = len(last_result.mappings)
                    unmatched_count = len(last_result.unmatched_files)
                    retry_hint = (
                        f"\n\n⚠️ **重试提醒**: 上次返回了 {matched_count} 个映射 + "
                        f"{unmatched_count} 个无法匹配 = {matched_count + unmatched_count}，"
                        f"但压缩包中共有 {total_files} 个字幕文件。"
                        f"请确保每个字幕文件都出现在 mappings 或 unmatched_files 中！"
                    )
                    current_prompt = prompt + retry_hint
                    logger.info(
                        f"[AI字幕匹配] 文件数量不匹配，重试第 {attempt} 次"
                    )

                if self.provider.lower() == "gemini":
                    result = self._call_gemini_simple(
                        system_prompt, current_prompt,
                        validation_key="mappings",
                        schema=SubtitleMappingResult,
                    )
                else:
                    result = self._call_openai_simple(
                        system_prompt, current_prompt,
                        validation_key="mappings",
                        schema=SubtitleMappingResult,
                    )

                if result:
                    result = result.strip()
                    logger.debug(f"[AI字幕匹配] 原始响应: {result[:500]}...")

                    try:
                        json_match = re.search(r'\{.*\}', result, re.DOTALL)
                        if json_match:
                            json_str = json_match.group()
                            data = json.loads(json_str)
                            mapping_result = SubtitleMappingResult(**data)
                            # 统计匹配到的任务数
                            matched_tasks = set(
                                m.task_uuid for m in mapping_result.mappings
                            )

                            # 验证：已匹配 + 无法匹配 = 总数
                            matched_count = len(mapping_result.mappings)
                            unmatched_count = len(mapping_result.unmatched_files)
                            processed_total = matched_count + unmatched_count

                            if processed_total != total_files:
                                if attempt < max_retries:
                                    # 还有重试机会，保存结果并继续
                                    last_result = mapping_result
                                    logger.warning(
                                        f"[AI字幕匹配] 文件数量不匹配: "
                                        f"已匹配({matched_count}) + 无法匹配({unmatched_count}) "
                                        f"= {processed_total}, 期望 {total_files}"
                                    )
                                    continue
                                else:
                                    # 最后一次尝试仍然不匹配，记录警告但返回结果
                                    logger.warning(
                                        f"[AI字幕匹配] 重试后数量仍不匹配: "
                                        f"已匹配({matched_count}) + 无法匹配({unmatched_count}) "
                                        f"= {processed_total}, 共 {total_files} 个字幕"
                                    )

                            # 记录无法匹配的文件
                            if unmatched_count > 0:
                                logger.info(
                                    f"[AI字幕匹配] {unmatched_count} 个字幕无法匹配: "
                                    f"{mapping_result.unmatched_files[:5]}..."
                                    if unmatched_count > 5
                                    else f"[AI字幕匹配] {unmatched_count} 个字幕无法匹配: "
                                    f"{mapping_result.unmatched_files}"
                                )

                            logger.info(
                                f"[AI字幕匹配] 分析完成: "
                                f"匹配到 {len(matched_tasks)} 个任务, "
                                f"映射={matched_count}, 无法匹配={unmatched_count}, "
                                f"置信度={mapping_result.confidence}"
                            )
                            return mapping_result
                    except json.JSONDecodeError as e:
                        logger.error(f"[AI字幕匹配] JSON解析失败: {e}")
                        logger.debug(f"[AI字幕匹配] 响应内容: {result}")
                    except Exception as e:
                        logger.error(f"[AI字幕匹配] 模型验证失败: {e}")

            except Exception as e:
                logger.error(f"[AI字幕匹配] 分析失败: {e}")
                if attempt < max_retries:
                    continue
                return None

        # 所有尝试都失败，返回最后一次的结果（如果有）
        return last_result

    def choose_subtitle_candidate(
        self,
        task_data: Dict,
        ranked_candidates: List[Dict],
    ) -> Optional[SubtitleCandidateDecision]:
        """让 AI 在搜索结果中直接选择最佳字幕候选。"""
        if not self.is_available() or not ranked_candidates:
            return None

        title = str(task_data.get("tmdb_name") or task_data.get("name") or "未知标题")
        media_type = "电影" if task_data.get("is_movie") else "剧集"
        season_id = task_data.get("season_id")
        year_value = task_data.get("tmdb_year") or task_data.get("year")
        preferred_language = str(
            task_data.get("subtitle_auto_fetch_preferred_language") or "zh-CN"
        )

        source_path_basename = str(
            task_data.get("subtitle_auto_fetch_source_path_basename") or ""
        ).strip()
        source_title_hint = str(
            task_data.get("subtitle_auto_fetch_source_title_hint") or ""
        ).strip()
        source_video_names = task_data.get("subtitle_auto_fetch_source_video_names") or []
        target_video_names = (
            task_data.get("subtitle_auto_fetch_missing_target_video_names") or []
        )
        scan_scope_root = str(
            task_data.get("subtitle_auto_fetch_scan_scope_root") or ""
        ).strip()
        is_season_zero_tv = bool(
            task_data.get("subtitle_auto_fetch_is_season_zero_tv")
        )

        context_lines = [
            f"标题: {title}",
            f"类型: {media_type}",
            f"偏好语言: {preferred_language}",
        ]
        if isinstance(season_id, int) and season_id > 0 and not task_data.get("is_movie"):
            context_lines.append(f"季度: S{season_id:02d}")
        if year_value:
            context_lines.append(f"年份: {year_value}")
        if is_season_zero_tv:
            context_lines.append("目标是 TV Season 0 / 特别篇 / 剧场版相关条目")
        if source_path_basename:
            context_lines.append(f"源目录名: {source_path_basename}")
        if source_title_hint:
            context_lines.append(f"源标题线索: {source_title_hint}")
        if source_video_names:
            context_lines.append(
                f"源视频文件: {' | '.join(map(str, source_video_names[:3]))}"
            )
        if target_video_names:
            context_lines.append(
                f"缺字幕目标文件: {' | '.join(map(str, target_video_names[:3]))}"
            )
        if scan_scope_root:
            context_lines.append(f"扫描根目录: {scan_scope_root}")

        candidates_text = []
        for item in ranked_candidates:
            candidates_text.append(
                "\n".join(
                    [
                        f"候选索引: {item.get('index')}",
                        f"标题: {item.get('title')}",
                        f"摘要: {item.get('snippet') or ''}",
                        f"附件数: {item.get('attachment_count')}",
                        f"外链数: {item.get('external_count')}",
                        f"详情页: {item.get('detail_url')}",
                    ]
                )
            )

        prompt = f"""请从以下字幕搜索结果中直接选择最适合自动导入的一个候选。

任务上下文：
{chr(10).join(context_lines)}

候选列表：

{chr(10).join(candidates_text)}

选择规则：
1. 允许从宽松搜索词返回的相关结果中直接判断，不要因为没有季号就直接否定候选
2. 优先选择最像同一作品、且最适合自动导入的字幕结果
3. 优先简体中文，其次包含简体中文的双语
4. 当存在多个简体中文候选时，优先选择更像“正片全集/整季”的资源，而不是零散单集、特典或补丁
5. 当存在多个简体中文候选时，优先选择标题/摘要里更像当前片源版本的候选；若无法确认片源版本，再优先信息更完整、覆盖更完整、资源更稳定的候选
6. 当存在多个简体中文候选时，优先修正版、v2/v3、明确标注修订/校对完成的版本；若无明显差异，再选择更适合自动导入的常规字幕包
7. 如果候选明显是错误作品、错误媒体类型、特典/NCOP/NCED/PV/CM 或不可用资源，再降低优先级
8. 同一 IP 但不同篇章、不同季度、不同剧场版/主线弧线，不应视为可自动导入；若任务线索指向某个特定篇章（如源目录、源文件名、目标文件名），而候选明确指向其他篇章，则必须返回 should_use=false
9. 对 TV Season 0 / 特别篇 / 剧场版相关条目，要优先匹配这些线索，不能因为同属一个系列就默认可用
10. selected_index 必须来自给定候选索引
11. 只有在所有候选都明显不适合自动导入时，should_use 才返回 false

请严格返回 JSON：
{{
  "selected_index": 0,
  "should_use": true,
  "confidence": "High",
  "language_assessment": "简体中文",
  "reason": "选择理由",
  "warnings": ["可选警告"]
}}"""

        system_prompt = (
            "你是一个字幕搜索结果裁决助手。"
            "用户会先用宽松标题词搜出相关结果，再由你直接判断最适合自动导入的候选。"
            "优先正确作品和简体中文字幕。"
            "只输出 JSON。"
        )

        try:
            if self.provider.lower() == "gemini":
                result = self._call_gemini_simple(
                    system_prompt,
                    prompt,
                    validation_key="selected_index",
                    schema=SubtitleCandidateDecision,
                )
            else:
                result = self._call_openai_simple(
                    system_prompt,
                    prompt,
                    validation_key="selected_index",
                    schema=SubtitleCandidateDecision,
                )

            if not result:
                return None

            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if not json_match:
                return None
            data = json.loads(json_match.group())
            return SubtitleCandidateDecision(**data)
        except Exception as e:
            logger.warning(f"[AI字幕候选] 选择失败: {e}")
            return None

    def choose_subtitle_thread_package(
        self,
        task_data: Dict,
        candidate: Dict,
        package_summaries: List[Dict],
    ) -> Optional[SubtitleThreadPackageDecision]:
        """让 AI 在单个帖子内选择最适合自动导入的字幕包。"""
        if not self.is_available() or not package_summaries:
            return None

        title = str(task_data.get("tmdb_name") or task_data.get("name") or "未知标题")
        media_type = "电影" if task_data.get("is_movie") else "剧集"
        season_id = task_data.get("season_id")
        year_value = task_data.get("tmdb_year") or task_data.get("year")
        preferred_language = str(
            task_data.get("subtitle_auto_fetch_preferred_language") or "zh-CN"
        )
        missing_video_count = int(task_data.get("missing_video_count") or 0)
        source_path_basename = str(
            task_data.get("subtitle_auto_fetch_source_path_basename") or ""
        ).strip()
        source_title_hint = str(
            task_data.get("subtitle_auto_fetch_source_title_hint") or ""
        ).strip()
        source_video_names = task_data.get("subtitle_auto_fetch_source_video_names") or []
        target_video_names = (
            task_data.get("subtitle_auto_fetch_missing_target_video_names") or []
        )
        scan_scope_root = str(
            task_data.get("subtitle_auto_fetch_scan_scope_root") or ""
        ).strip()
        is_season_zero_tv = bool(
            task_data.get("subtitle_auto_fetch_is_season_zero_tv")
        )

        context_lines = [
            f"标题: {title}",
            f"类型: {media_type}",
            f"偏好语言: {preferred_language}",
            f"缺字幕视频数: {missing_video_count}",
            f"帖子标题: {candidate.get('title')}",
            f"帖子详情页: {candidate.get('detail_url')}",
        ]
        if isinstance(season_id, int) and season_id > 0 and not task_data.get("is_movie"):
            context_lines.append(f"季度: S{season_id:02d}")
        if year_value:
            context_lines.append(f"年份: {year_value}")
        if is_season_zero_tv:
            context_lines.append("目标是 TV Season 0 / 特别篇 / 剧场版相关条目")
        if source_path_basename:
            context_lines.append(f"源目录名: {source_path_basename}")
        if source_title_hint:
            context_lines.append(f"源标题线索: {source_title_hint}")
        if source_video_names:
            context_lines.append(
                f"源视频文件: {' | '.join(map(str, source_video_names[:3]))}"
            )
        if target_video_names:
            context_lines.append(
                f"缺字幕目标文件: {' | '.join(map(str, target_video_names[:3]))}"
            )
        if scan_scope_root:
            context_lines.append(f"扫描根目录: {scan_scope_root}")
        if candidate.get("pagination_truncated"):
            context_lines.append("注意: 当前只扫描了帖子前几页，后续分页未完全展开")

        packages_text = []
        for item in package_summaries:
            flags = ", ".join(item.get("package_flags") or [])
            packages_text.append(
                "\n".join(
                    [
                        f"包索引: {item.get('index')}",
                        f"页码: {item.get('page_number')}",
                        f"楼层: {item.get('floor_label') or ''}",
                        f"作者: {item.get('post_author') or ''}",
                        f"时间: {item.get('post_time') or ''}",
                        f"标记: {flags}",
                        f"直连下载: {item.get('has_direct_download')}",
                        f"链接摘要: {item.get('link_summary') or ''}",
                        f"楼层正文摘要: {item.get('post_text') or ''}",
                    ]
                )
            )

        prompt = f"""请从以下同一个帖子内的多个字幕包中，选择最适合自动导入的一个包。

任务上下文：
{chr(10).join(context_lines)}

字幕包列表：

{chr(10).join(packages_text)}

选择规则：
1. 这些包都来自同一个帖子，不要只按文件名机械判断，要综合楼层正文、附件名、修订说明、补丁说明、片源说明来判断
2. 优先正确作品、正确媒体类型、正确季度或全集范围的字幕包
3. 优先简体中文，其次包含简体中文的双语包
4. 多个简中包并存时，优先正片全集/整季，而不是单集修补、特典、字体包或补丁包
5. 若能看出当前片源版本（如 ReinForce、ANK-Raws、BDRip、TVRip），优先更匹配当前片源的字幕包
6. 修正版、v2/v3、明确标注修订完成的包可优先，但仅补丁包、仅单集修复包应降权
7. 字体包、网盘说明、非直连资源、特典/NCOP/NCED/PV/CM 应降权
8. 若楼层正文或附件说明已明确这是其他篇章/季度/剧场版，即便质量更高也不能用于当前任务；这类情况必须返回 should_use=false
9. 对 TV Season 0 / 特别篇 / 剧场版相关条目，要优先匹配源目录、源文件名、目标文件名中的篇章线索
10. selected_index 必须来自给定包索引
11. 只有当所有包都明显不适合自动导入时，should_use 才返回 false

请严格返回 JSON：
{{
  "selected_index": 0,
  "should_use": true,
  "confidence": "High",
  "language_assessment": "简体中文",
  "reason": "选择理由",
  "warnings": ["可选警告"]
}}"""

        system_prompt = (
            "你是一个字幕帖子内选包助手。"
            "用户已经先选中了正确的帖子，现在需要你从帖内多个楼层附件里判断最适合自动导入的字幕包。"
            "你必须理解楼层正文是在说明什么，再结合附件名选择。"
            "只输出 JSON。"
        )

        try:
            if self.provider.lower() == "gemini":
                result = self._call_gemini_simple(
                    system_prompt,
                    prompt,
                    validation_key="selected_index",
                    schema=SubtitleThreadPackageDecision,
                )
            else:
                result = self._call_openai_simple(
                    system_prompt,
                    prompt,
                    validation_key="selected_index",
                    schema=SubtitleThreadPackageDecision,
                )

            if not result:
                return None

            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if not json_match:
                return None
            data = json.loads(json_match.group())
            return SubtitleThreadPackageDecision(**data)
        except Exception as e:
            logger.warning(f"[AI字幕包] 选择失败: {e}")
            return None
