import json
import re
from typing import Dict, List, Optional

from google import genai
from pydantic import ValidationError
from google.genai.types import HttpOptions, GenerateContentConfig

from ..logger import logger
from .base_client import BaseAIClient
from .models import AIAnalysisResult
from ..config.config_manager import cm


class GeminiClient(BaseAIClient):
    """Google Gemini API客户端，支持结构化输出"""

    def __init__(self):
        super().__init__("gemini")
        self.api_key = cm.get_config("gemini_api_key")
        self.base_url = (
            cm.get_config("gemini_base_url")
            or "https://generativelanguage.googleapis.com"
        )
        self.model = cm.get_config("gemini_model") or "gemini-2.5-flash"
        self.temperature = float(cm.get_config("gemini_temperature") or 0.5)
        self.output_format = (
            cm.get_config("gemini_output_format") or "structured_output"
        )
        auto_routing = cm.get_config("gemini_auto_routing_enabled")
        self.auto_routing_enabled = bool(
            auto_routing if auto_routing is not None else True
        )

        if self.enabled and self.api_key:
            try:
                # 构建http_options以支持自定义base_url和超时
                http_options = HttpOptions(timeout=120000)  # 超时 120 秒
                if (
                    self.base_url
                    and self.base_url != "https://generativelanguage.googleapis.com"
                ):
                    http_options.base_url = self.base_url

                self.client = genai.Client(
                    api_key=self.api_key, http_options=http_options
                )

                logger.info(f"[Gemini客户端] 初始化成功，使用API地址: {self.base_url}")
            except Exception as e:
                logger.error(f"[Gemini客户端] 初始化失败: {e}")
                self.client = None
        else:
            self.client = None

    def is_available(self) -> bool:
        """检查Gemini客户端是否可用"""
        return bool(self.enabled and self.client and self.api_key)

    def analyze_episode_mapping(
        self,
        anime_info: Dict,
        local_files: List[Dict],
        bangumi_context: Optional[Dict] = None,
    ) -> Optional[AIAnalysisResult]:
        """
        使用Gemini API分析本地文件与TMDB剧集的映射关系

        Args:
            anime_info: TMDB动漫信息
            local_files: 本地文件信息列表，包含文件名、路径、时长等
            bangumi_context: Bangumi 辅助上下文，失败时为 None

        Returns:
            验证后的AIAnalysisResult对象
        """
        if not self.is_available():
            logger.warning("[Gemini识别] Gemini功能未启用或配置不完整")
            return None

        if not self.client:
            logger.error("[Gemini识别] Gemini 客户端未初始化")
            return None

        try:
            # 导入AIClient以使用通用prompt方法
            from .client import AIClient

            base_prompt = AIClient.build_common_prompt(
                anime_info,
                local_files,
                bangumi_context=bangumi_context,
            )
            system_prompt = AIClient.get_system_prompt()
            output_format = self._resolve_output_format()

            if output_format == "structured_output":
                prompt = self._add_gemini_instructions(base_prompt)
            else:
                prompt = self._add_json_output_instructions(base_prompt)

            request_contents = f"{prompt}"

            logger.debug(
                "[Gemini识别] 使用输出格式降级链，首选: "
                f"{output_format}, model={self.model}"
            )
            return self._call_with_format_fallback(
                system_prompt=system_prompt,
                request_contents=request_contents,
                preferred_format=output_format,
            )

        except Exception as e:
            logger.error(f"[Gemini识别] 分析失败: {str(e)}")
            return None

    def _get_priority_order(self) -> List[str]:
        """Gemini 固定格式优先级（不依赖历史成功率）。"""
        return ["structured_output", "json_object", "text"]

    def _resolve_output_format(self) -> str:
        """解析Gemini首选输出格式：自动路由开启时使用固定优先级。"""
        priority_order = self._get_priority_order()

        if self.auto_routing_enabled:
            return priority_order[0]

        if self.output_format in priority_order:
            return self.output_format

        logger.warning(
            f"[Gemini识别] 未知输出格式: {self.output_format}，回退到 {priority_order[0]}"
        )
        return priority_order[0]

    def _build_fallback_format_chain(self, preferred_format: str) -> List[str]:
        """构建Gemini无感降级链：按固定优先级降级。"""
        priority_order = self._get_priority_order()

        if preferred_format not in priority_order:
            preferred_format = priority_order[0]

        chain = [preferred_format]
        for fmt in priority_order:
            if fmt not in chain:
                chain.append(fmt)

        return chain

    def _call_with_format_fallback(
        self,
        system_prompt: str,
        request_contents: str,
        preferred_format: str,
    ) -> Optional[AIAnalysisResult]:
        """按输出格式降级链调用Gemini，直到成功解析并校验为止"""
        format_chain = self._build_fallback_format_chain(preferred_format)

        last_error: Optional[Exception] = None
        for output_format in format_chain:
            try:
                request_config = self._build_generate_content_config(
                    system_prompt, output_format
                )

                logger.debug(
                    f"[Gemini识别] 尝试输出格式: {output_format}, model={self.model}"
                )
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=request_contents,
                    config=request_config,
                )

                result = self._parse_response_result(response, output_format)
                if result:
                    if output_format != preferred_format:
                        logger.warning(
                            "[Gemini识别] 输出格式已自动降级: "
                            f"{preferred_format} -> {output_format}"
                        )
                    return result

                logger.warning(
                    f"[Gemini识别] format={output_format} 解析或校验失败，尝试降级"
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[Gemini识别] format={output_format} 调用失败，尝试降级: {str(e)}"
                )

        if last_error:
            raise last_error
        return None

    def _build_generate_content_config(
        self, system_prompt: str, output_format: str
    ) -> GenerateContentConfig:
        """根据输出格式构建Gemini请求配置"""
        config_kwargs = {
            "system_instruction": system_prompt,
            "temperature": self.temperature,
        }

        if output_format == "structured_output":
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = AIAnalysisResult.gemini_json_schema()
        elif output_format == "json_object":
            config_kwargs["response_mime_type"] = "application/json"

        return GenerateContentConfig(**config_kwargs)

    def _parse_response_result(
        self, response, output_format: str
    ) -> Optional[AIAnalysisResult]:
        """按输出格式解析Gemini响应并做Pydantic校验"""
        response_text = getattr(response, "text", "") if response else ""
        logger.debug(f"[Gemini识别] Raw response text: {response_text}")

        if not response:
            logger.error("[Gemini识别] Gemini 响应为空")
            return None

        if output_format == "structured_output" and hasattr(response, "parsed") and response.parsed:
            try:
                logger.debug(f"[Gemini识别] 解析后的JSON: {response.parsed}")
                result = AIAnalysisResult.model_validate(response.parsed)
                logger.info(
                    f"[Gemini识别] 使用解析后的结果，置信度: {result.confidence}"
                )
                return result
            except ValidationError as e:
                logger.error(f"[Gemini识别] parsed校验失败: {e}")

        if not response_text:
            logger.error("[Gemini识别] Gemini 响应内容为空")
            return None

        json_data = self._extract_json_payload(response_text)
        if json_data is None:
            logger.error(f"[Gemini识别] JSON提取失败，原始响应: {response_text[:200]}...")
            return None

        try:
            result = AIAnalysisResult.model_validate(json_data)
            logger.info(f"[Gemini识别] 手动解析成功，置信度: {result.confidence}")
            return result
        except ValidationError as e:
            logger.error(f"[Gemini识别] JSON结构校验失败: {e}")
            logger.error(f"[Gemini识别] 原始JSON: {str(json_data)[:200]}...")
            return None

    def _extract_json_payload(self, content: str) -> Optional[Dict]:
        """从模型输出中提取JSON对象"""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        json_patterns = [
            r"```json\s*(\{.*?\})\s*```",
            r"```\s*(\{.*?\})\s*```",
            r"(\{[\s\S]*\})",
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, content, re.DOTALL)
            for match in matches:
                try:
                    return json.loads(match)
                except json.JSONDecodeError:
                    continue

        return None

    def _add_json_output_instructions(self, base_prompt: str) -> str:
        """为非schema模式补充严格JSON输出说明"""
        json_instructions = """
注意：请仅输出一个合法 JSON 对象，不要输出 Markdown 代码块或额外解释。
JSON 必须严格符合 AIAnalysisResult 结构，字段类型和枚举值必须准确。
"""
        return base_prompt + json_instructions

    def _add_gemini_instructions(self, base_prompt: str) -> str:
        """为Gemini结构化输出模式添加指令"""
        gemini_instructions = """
注意：请确保返回的数据严格符合AIAnalysisResult的结构定义，所有字段类型和枚举值必须准确。
"""
        return base_prompt + gemini_instructions

