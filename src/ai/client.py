import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Type

from pydantic import BaseModel
from google.genai.types import GenerateContentConfig

from ..logger import logger
from .models import AIAnalysisResult, MovieCollectionResult, SubtitleMappingResult
from ..utils.path import AI_ANALYSIS_PATH
from ..config.config_manager import cm
from .gemini_client import GeminiClient
from .openai_client import OpenAIClient
from .base_client import BaseAIClient


class AIClient:
    """AI客户端工厂类，根据配置选择合适的AI提供商"""

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
        result = self.extract_title_and_type(filename)
        if result:
            return result[0]
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
        if not self.is_available():
            logger.warning(f"[AI提取标题] AI功能未启用或{self.provider}客户端不可用")
            return None

        logger.info(f"[AI提取标题] 使用 {self.provider.upper()} 提取标题: {filename}")

        try:
            prompt = f"""从以下文件名中提取动漫或电影的标题名称，并判断内容类型。

文件名: {filename}

要求：
1. 提取标题：返回可在 TMDB 上搜索到的标题
   - 不要包含字幕组名、分辨率、编码等信息
   - 不要包含 "剧场版"、"劇場版"、"theatrical"、"movie" 等前缀
   - 使用作品的正式名称（中文名或日文名均可）
2. 判断类型：根据文件名判断是"movie"（电影/剧场版）还是"tv"（电视剧/番剧）

类型判断依据：
- 包含"劇場版"、"剧场版"、"MOVIE"、"movie"、"Film"、"theatrical"、"Movie"等关键词 → movie
- 包含"OVA"、"OAD"但不是剧场版 → tv (特典)
- 文件名中有明确的季度信息（S01、第一季等）→ tv
- 文件名中有剧集编号格式（E01、第01话等）→ tv
- 不确定时默认为 tv

请严格按照以下JSON格式返回，不要有其他文字：
{{"title": "TMDB可搜索的标题", "type": "movie或tv"}}

示例：
输入: [LoliHouse] 葬送的芙莉莲 / Sousou no Frieren [01-28 Fin][WebRip 1080p]
输出: {{"title": "葬送的芙莉莲", "type": "tv"}}

输入: [AI-Raws][劇場版 空の境界][MOVIE 01-09][BDRip]
输出: {{"title": "空之境界", "type": "movie"}}

输入: [VCB-Studio] Fate Zero [Ma10p_1080p]
输出: {{"title": "Fate/Zero", "type": "tv"}}

输入: [字幕组] 剧场版 鬼灭之刃 无限列车篇 [BDRip]
输出: {{"title": "鬼灭之刃 无限列车篇", "type": "movie"}}"""

            system_prompt = "你是一个专业的动漫文件命名解析助手。你的任务是从复杂的文件名中准确提取出动漫或电影的标题，并判断内容类型。只输出JSON格式结果，不要有任何额外的解释。"

            # 直接调用底层客户端的API
            if self.provider.lower() == "gemini":
                result = self._call_gemini_simple(system_prompt, prompt)
            else:
                result = self._call_openai_simple(system_prompt, prompt)

            if result:
                # 清理结果，解析JSON
                result = result.strip()
                # 尝试提取JSON部分 - 使用更健壮的正则
                import re

                # 先尝试直接解析整个结果
                try:
                    data = json.loads(result)
                    title = data.get("title", "").strip().strip('"\'')
                    content_type = data.get("type", "").strip().lower()

                    if content_type not in ["movie", "tv"]:
                        content_type = None

                    logger.info(
                        f"[AI提取标题] 提取结果: {title}, 类型: {content_type}"
                    )
                    return (title, content_type)
                except json.JSONDecodeError:
                    pass

                # 尝试提取 {...} 部分（支持嵌套引号）
                json_match = re.search(r'\{.*\}', result, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group())
                        title = data.get("title", "").strip().strip('"\'')
                        content_type = data.get("type", "").strip().lower()

                        if content_type not in ["movie", "tv"]:
                            content_type = None

                        logger.info(
                            f"[AI提取标题] 提取结果: {title}, 类型: {content_type}"
                        )
                        return (title, content_type)
                    except json.JSONDecodeError:
                        pass

                # 尝试用正则直接提取 title 和 type 字段
                title_match = re.search(
                    r'"title"\s*:\s*"([^"]+)"', result
                )
                type_match = re.search(r'"type"\s*:\s*"([^"]+)"', result)

                if title_match:
                    title = title_match.group(1).strip()
                    content_type = None
                    if type_match:
                        t = type_match.group(1).strip().lower()
                        if t in ["movie", "tv"]:
                            content_type = t

                    logger.info(
                        f"[AI提取标题] 提取结果: {title}, 类型: {content_type}"
                    )
                    return (title, content_type)

                # 如果都失败了，返回原始结果
                result = result.strip().strip('"\'')
                logger.info(f"[AI提取标题] 提取结果: {result} (类型未知)")
                return (result, None)

            return None

        except Exception as e:
            logger.error(f"[AI提取标题] 提取失败: {e}")
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
                if schema and output_format == "structured_output":
                    request_params["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__.lower(),
                            "schema": schema.model_json_schema(),
                        },
                    }
                elif schema and output_format == "json_object":
                    request_params["response_format"] = {"type": "json_object"}

                response = client.client.chat.completions.create(**request_params)

                if response.choices and response.choices[0].message:
                    content = response.choices[0].message.content
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
    ) -> Optional[AIAnalysisResult]:
        """
        分析本地文件与TMDB剧集的映射关系

        Args:
            anime_info: TMDB动漫信息
            local_files: 本地文件信息列表，包含文件名、路径、时长等

        Returns:
            验证后的AIAnalysisResult对象
        """
        if not self.is_available():
            logger.warning(f"[AI识别] AI功能未启用或{self.provider}客户端不可用")
            return None

        logger.info(f"[AI识别] 使用 {self.provider.upper()} 进行分析")
        result = self._client.analyze_episode_mapping(anime_info, local_files)

        # 统一在此处保存分析数据
        self._save_analysis_snapshot(
            analysis_kind="tv_episode_mapping",
            source_name=anime_info.get("name", "unknown"),
            source_payload=anime_info,
            local_files=local_files,
            result=result,
            force=not bool(result),
        )

        # 保持兼容：沿用 provider 客户端原保存逻辑（受 ai_auto_save 控制）
        self._client._save_analysis_data(anime_info, local_files, result)

        return result

    @staticmethod
    def build_common_prompt(anime_info: Dict, local_files: List[Dict]) -> str:
        """
        构建通用的分析提示词，不包含JSON格式要求

        Args:
            anime_info: TMDB动漫信息
            local_files: 本地文件信息列表，包含文件名、路径、时长等

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

        # 构建详细的季度和集数信息
        seasons_info = "TMDB 季度和集数详情：\n"
        seasons = anime_info.get("seasons", [])
        for season in seasons:
            season_num = season.get("season_number", 0)
            season_name = season.get("name", f"Season {season_num}")
            episodes = season.get("episodes", [])
            episode_count = len(episodes) if episodes else season.get("episode_count", 0)

            seasons_info += f"\n【Season {season_num}】{season_name} (共 {episode_count} 集)\n"

            # 如果有详细的剧集信息，列出每集
            if episodes:
                for ep in episodes:
                    ep_num = ep.get("episode_number", 0)
                    ep_name = ep.get("name", "")
                    air_date = ep.get("air_date", "")
                    seasons_info += f"  S{season_num:02d}E{ep_num:02d}: {ep_name}"
                    if air_date:
                        seasons_info += f" ({air_date})"
                    seasons_info += "\n"
            elif episode_count > 0:
                # 没有详细信息，只显示集数范围
                seasons_info += f"  E01 - E{episode_count:02d}\n"

        # 构建本地文件信息
        files_info = "本地文件信息 (路径均为相对路径):\n"
        for i, file_info in enumerate(local_files, 1):
            duration_str = ""
            if file_info.get("duration"):
                duration_str = f" (时长: {file_info['duration']:.1f}分钟)"
            files_info += f"  {file_info['path']}{duration_str}\n"

        prompt = f"""请分析以下动漫的本地文件与TMDB数据的对应关系：

{tmdb_info}

{seasons_info}

{files_info}

请根据以下规则进行映射：

1. **匹配优先级**（按顺序尝试）：
   - 文件名中的集数与 TMDB 集数号直接匹配
   - 文件名中的标题与 TMDB 集标题相似度匹配
   - 文件名中的日期与 TMDB 播出日期匹配

2. **Season 0 特典规则**：
   - OVA、OAD、SP、Special 等标签 → Season 0
   - 小数集数（如 5.5、12.5）→ Season 0（总集篇）
   - 第00集、E00、[00] → Season 0（序章/先行篇）
   - **重要**: 文件名中的 SP01、OVA01 不一定对应 S0E1，需要根据标题匹配

3. **多季度处理**：
   - 本地目录可能将多季合并，需要根据集数范围判断
   - 本地目录可能使用总集号（如 E14 可能是 S2E01）
   - 不同季度可能仅用名称区分（如 \"Okawari\"、\"Okaeri\" 等后缀）

4. **只输出匹配到的文件**，未匹配到 TMDB 的文件不要输出

5. **可观测性要求（必须满足）**：
   - 返回 `unmatched_files`，列出所有未匹配文件路径（相对路径）
   - `conflict_details` 仅填写“硬冲突”（例如：重复映射、集数越界、文件不存在）
   - 证据不足/不确定但可执行的说明（例如仅凭小数集数推断）请写入 `extra_notes`，不要写入 `conflict_details`
   - 如果 confidence 为 High/Medium，`file_mapping` 必须尽量覆盖可匹配文件

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
