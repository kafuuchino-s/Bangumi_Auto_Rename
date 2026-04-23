import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
import re
from typing import Any, Callable, Dict, List, Optional, cast

from openai import OpenAI
from pydantic import ValidationError

from ..logger import logger
from .base_client import BaseAIClient
from .models import AIAnalysisResult
from .prompt_support import build_common_prompt, get_system_prompt
from ..config.config_manager import cm


class OpenAIClient(BaseAIClient):
    """OpenAI API客户端，支持多种格式化输出方式"""

    def __init__(self):
        super().__init__("openai")
        self.api_key = cm.get_config("ai_api_key")
        self.base_url = cm.get_config("ai_base_url")
        self.model = cm.get_config("ai_model")
        self.temperature = float(cm.get_config("ai_temperature") or 0.1)

        # 支持多种输出格式
        self.output_format = cm.get_config("openai_output_format") or "structured_output"
        self.api_interface = self._resolve_api_interface(
            cm.get_config("openai_api_interface")
        )
        self.last_configured_api_interface = self.api_interface
        self.last_actual_api_interface = ""
        self.last_api_interface_fallback = False
        self.last_api_interface_fallback_reason = ""
        self.auto_routing_enabled = bool(
            cm.get_config("openai_auto_routing_enabled")
            if cm.get_config("openai_auto_routing_enabled") is not None
            else True
        )

        if self.enabled and self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=120.0,  # 请求超时 120 秒
            )
        else:
            self.client = None

    def is_available(self) -> bool:
        """检查OpenAI客户端是否可用"""
        return bool(self.enabled and self.client and self.api_key)

    def resolve_api_interface(self, interface_value: object) -> str:
        return self._resolve_api_interface(
            interface_value if isinstance(interface_value, str) else None
        )

    def call_via_chat_completions(
        self, request_params: dict[str, object]
    ) -> dict[str, object]:
        return cast(dict[str, object], self._call_via_chat_completions(request_params))

    def call_via_responses_api(
        self, request_params: dict[str, object]
    ) -> dict[str, object]:
        return cast(dict[str, object], self._call_via_responses_api(request_params))

    def stream_via_chat_completions(
        self,
        request_params: dict[str, object],
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        if not self.client:
            raise RuntimeError("OpenAI 客户端未初始化")

        chat_api = cast(OpenAI, self.client).chat
        create_chat_completion = cast(
            Callable[..., object],
            chat_api.completions.create,
        )
        response_stream = cast(Sequence[object], create_chat_completion(**request_params))
        chunks: list[str] = []
        for chunk in response_stream:
            choices = getattr(chunk, "choices", None)
            if not isinstance(choices, list) or not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            piece = getattr(delta, "content", None) if delta is not None else None
            if isinstance(piece, str) and piece:
                chunks.append(piece)
                if stream_callback:
                    stream_callback(piece)
        return "".join(chunks)

    def stream_via_responses_api(
        self,
        request_params: dict[str, object],
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        if not self.client:
            raise RuntimeError("OpenAI 客户端未初始化")

        responses_params = self._convert_chat_request_to_responses(request_params)
        responses_params["stream"] = True
        create_response = cast(
            Callable[..., object],
            cast(OpenAI, self.client).responses.create,
        )
        stream = cast(Sequence[object], create_response(**responses_params))
        chunks: list[str] = []
        for event in stream:
            event_type = getattr(event, "type", "")
            if event_type not in {
                "response.output_text.delta",
                "response.output_text",
            }:
                continue
            delta = getattr(event, "delta", None) or getattr(event, "text", None)
            if isinstance(delta, str) and delta:
                chunks.append(delta)
                if stream_callback:
                    stream_callback(delta)
        return "".join(chunks)

    def analyze_episode_mapping(
        self,
        anime_info: Mapping[str, object],
        local_files: Sequence[Mapping[str, object]],
        bangumi_context: Mapping[str, object] | None = None,
    ) -> Optional[AIAnalysisResult]:
        """
        使用OpenAI API分析本地文件与TMDB剧集的映射关系

        Args:
            anime_info: TMDB动漫信息
            local_files: 本地文件信息列表，包含文件名、路径、时长等
            bangumi_context: Bangumi 辅助上下文，失败时为 None

        Returns:
            验证后的AIAnalysisResult对象
        """
        if not self.is_available():
            logger.warning("[OpenAI识别] OpenAI功能未启用或配置不完整")
            return None

        if not self.client:
            logger.error("[OpenAI识别] OpenAI 客户端未初始化")
            return None

        try:
            prompt = build_common_prompt(
                anime_info,
                local_files,
                bangumi_context=bangumi_context,
            )

            system_prompt = get_system_prompt()

            normalized_format = self._resolve_output_format()
            configured_interface = self._resolve_api_interface(
                cm.get_config("openai_api_interface")
            )
            self.last_configured_api_interface = configured_interface
            self.last_actual_api_interface = ""
            self.last_api_interface_fallback = False
            self.last_api_interface_fallback_reason = ""

            # 根据输出模式补充更严格的返回约束
            if normalized_format == "structured_output":
                system_prompt += self._get_structured_output_instructions()
            elif normalized_format != "function_calling":
                system_prompt += self._get_json_instructions()

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]

            request_params = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
            }

            logger.debug(
                "[OpenAI识别] 使用输出格式降级链与接口分发，"
                f"首选格式={normalized_format}, 配置接口={configured_interface}, "
                f"base_request={json.dumps(request_params, indent=2, ensure_ascii=False)}"
            )
            result = self._call_with_format_fallback(
                request_params,
                normalized_format,
                configured_interface,
            )

            if not result:
                logger.error("[OpenAI识别] 无法解析或验证OpenAI响应")
                return None

            # 记录低置信度结果
            if result.confidence == "Low":
                logger.warning(f"[OpenAI识别] 低置信度结果: {result.reason}")

            logger.info(f"[OpenAI识别] 分析完成，置信度: {result.confidence}")
            return result

        except Exception as e:
            logger.error(f"[OpenAI识别] 分析失败: {str(e)}")
            return None

    def _get_priority_order(self) -> List[str]:
        """OpenAI 固定格式优先级（不依赖历史成功率）。"""
        return ["structured_output", "function_calling", "text"]

    def _resolve_output_format(self) -> str:
        """解析OpenAI首选输出格式：自动路由开启时使用固定优先级。"""
        priority_order = self._get_priority_order()

        if self.auto_routing_enabled:
            return priority_order[0]

        if self.output_format in priority_order:
            return self.output_format

        logger.warning(
            f"[OpenAI识别] 未知输出格式: {self.output_format}，回退到 {priority_order[0]}"
        )
        return priority_order[0]

    def _build_fallback_format_chain(self, preferred_format: str) -> List[str]:
        """构建OpenAI无感降级链：按固定优先级降级。"""
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
        base_request_params: Mapping[str, object],
        preferred_format: str,
        preferred_interface: str,
    ) -> Optional[AIAnalysisResult]:
        """按输出格式降级链调用OpenAI，直到成功解析并校验为止。"""
        format_chain = self._build_fallback_format_chain(preferred_format)

        self.last_configured_api_interface = preferred_interface
        self.last_actual_api_interface = ""
        self.last_api_interface_fallback = False
        self.last_api_interface_fallback_reason = ""

        last_error: Optional[Exception] = None
        for output_format in format_chain:
            request_params = dict(base_request_params)
            self._configure_output_format(request_params, output_format)

            logger.debug(
                "[OpenAI识别] 尝试输出格式与接口: "
                f"format={output_format}, configured_interface={preferred_interface}, "
                f"request={json.dumps(request_params, indent=2, ensure_ascii=False)}"
            )

            response_message: Optional[Dict[str, Any]] = None
            fallback_error: Optional[Exception] = None

            for interface in self._build_api_interface_chain(preferred_interface):
                try:
                    response_message = self._dispatch_openai_message(
                        interface, request_params
                    )
                    self.last_actual_api_interface = interface
                    if (
                        interface != preferred_interface
                        and preferred_interface == "responses_api"
                    ):
                        self.last_api_interface_fallback = True
                        self.last_api_interface_fallback_reason = (
                            str(fallback_error)
                            if fallback_error
                            else "responses_api 调用失败，自动回退"
                        )
                        logger.warning(
                            "[OpenAI识别] 接口已自动回退: "
                            f"{preferred_interface} -> {interface}, "
                            f"reason={self.last_api_interface_fallback_reason}"
                        )
                    break
                except Exception as e:
                    last_error = e
                    if (
                        interface == "responses_api"
                        and preferred_interface == "responses_api"
                    ):
                        fallback_error = e
                        logger.warning(
                            "[OpenAI识别] interface=responses_api 调用失败，"
                            f"将尝试 chat_completions 回退: {str(e)}"
                        )
                        continue

                    logger.warning(
                        "[OpenAI识别] 接口调用失败，尝试降级格式: "
                        f"format={output_format}, interface={interface}, error={str(e)}"
                    )
                    response_message = None
                    break

            if not response_message:
                continue

            logger.debug(
                "[OpenAI识别] format={} interface={} Response content: {}".format(
                    output_format,
                    self.last_actual_api_interface,
                    response_message.get("content"),
                )
            )

            result = self._extract_and_validate_json(response_message)
            if result:
                if output_format != preferred_format:
                    logger.warning(
                        "[OpenAI识别] 输出格式已自动降级: "
                        f"{preferred_format} -> {output_format}"
                    )
                return result

            # structured_output 首次失败时，同格式重试一次，避免偶发的非结构化漂移
            if output_format == "structured_output":
                logger.warning(
                    "[OpenAI识别] format=structured_output 首次解析失败，执行同格式重试"
                )
                try:
                    retry_message = self._dispatch_openai_message(
                        self.last_actual_api_interface or preferred_interface,
                        request_params,
                    )
                    logger.debug(
                        "[OpenAI识别] format=structured_output retry Response content: "
                        f"{retry_message.get('content')}"
                    )
                    retry_result = self._extract_and_validate_json(retry_message)
                    if retry_result:
                        if output_format != preferred_format:
                            logger.warning(
                                "[OpenAI识别] 输出格式已自动降级: "
                                f"{preferred_format} -> {output_format}"
                            )
                        return retry_result
                except Exception as e:
                    last_error = e
                    logger.warning(
                        "[OpenAI识别] format=structured_output 重试失败: "
                        f"interface={self.last_actual_api_interface or preferred_interface}, "
                        f"error={str(e)}"
                    )

            logger.warning(
                f"[OpenAI识别] format={output_format} 解析或校验失败，尝试降级"
            )

        if last_error:
            raise last_error
        return None

    def _resolve_api_interface(self, interface_value: Optional[str]) -> str:
        if interface_value in ["responses_api", "chat_completions"]:
            return interface_value

        if interface_value:
            logger.warning(
                "[OpenAI识别] 未知接口类型: "
                f"{interface_value}，回退到 responses_api"
            )
        return "responses_api"

    def _build_api_interface_chain(self, preferred_interface: str) -> List[str]:
        if preferred_interface == "chat_completions":
            return ["chat_completions"]
        return ["responses_api", "chat_completions"]

    def _dispatch_openai_message(
        self,
        interface: str,
        request_params: dict[str, object],
    ) -> dict[str, object]:
        if not self.client:
            raise RuntimeError("OpenAI 客户端未初始化")

        if interface == "responses_api":
            return self._call_via_responses_api(request_params)

        return self._call_via_chat_completions(request_params)

    def _call_via_chat_completions(
        self, request_params: dict[str, object]
    ) -> dict[str, object]:
        if not self.client:
            raise RuntimeError("OpenAI 客户端未初始化")

        chat_api = cast(OpenAI, self.client).chat
        create_chat_completion = cast(
            Callable[..., object],
            chat_api.completions.create,
        )
        response = create_chat_completion(**request_params)
        response_choices = getattr(response, "choices", None)
        if not isinstance(response_choices, list) or not response_choices:
            return {"content": "", "tool_calls": []}

        first_choice = response_choices[0]
        message = getattr(first_choice, "message", None)
        if message is None:
            return {"content": "", "tool_calls": []}

        tool_calls_payload: List[Dict[str, str]] = []
        tool_calls = getattr(message, "tool_calls", None)
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not getattr(tool_call, "function", None):
                    continue
                tool_calls_payload.append(
                    {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    }
                )

        return {
            "content": getattr(message, "content", ""),
            "tool_calls": tool_calls_payload,
        }

    def _call_via_responses_api(
        self, request_params: dict[str, object]
    ) -> dict[str, object]:
        if not self.client:
            raise RuntimeError("OpenAI 客户端未初始化")

        responses_params = self._convert_chat_request_to_responses(request_params)
        create_response = cast(
            Callable[..., object],
            cast(OpenAI, self.client).responses.create,
        )
        response = create_response(**responses_params)

        tool_calls_payload: List[Dict[str, str]] = []
        content_parts: List[str] = []

        response_output = getattr(response, "output", None)
        output_items = response_output if isinstance(response_output, list) else []
        for item in output_items:
            item_type = getattr(item, "type", "")

            if item_type == "function_call":
                name = getattr(item, "name", "")
                arguments = getattr(item, "arguments", "")
                if name and arguments is not None:
                    tool_calls_payload.append(
                        {"name": str(name), "arguments": str(arguments)}
                    )
                continue

            if item_type != "message":
                continue

            for content_item in getattr(item, "content", []) or []:
                if getattr(content_item, "type", "") == "output_text":
                    text = getattr(content_item, "text", "")
                    if text:
                        content_parts.append(str(text))

        if not content_parts:
            output_text = getattr(response, "output_text", None)
            if output_text:
                content_parts.append(str(output_text))

        content = "\n".join(part for part in content_parts if part)
        return {"content": content, "tool_calls": tool_calls_payload}

    def _convert_chat_request_to_responses(
        self, request_params: Mapping[str, object]
    ) -> dict[str, object]:
        raw_messages = request_params.get("messages", [])
        messages = cast(list[Mapping[str, object]], raw_messages) if isinstance(raw_messages, list) else []
        instructions = ""
        user_parts: list[str] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if not isinstance(content, str):
                continue

            if role == "system":
                if instructions:
                    instructions += "\n\n"
                instructions += content
            elif role == "user":
                user_parts.append(content)

        responses_params: dict[str, object] = {
            "input": "\n\n".join(user_parts),
        }
        model = request_params.get("model")
        if isinstance(model, str) and model:
            responses_params["model"] = model
        temperature = request_params.get("temperature")
        if isinstance(temperature, (int, float)):
            responses_params["temperature"] = float(temperature)

        max_tokens = request_params.get("max_tokens")
        if max_tokens is None:
            max_tokens = request_params.get("max_completion_tokens")
        if isinstance(max_tokens, int):
            responses_params["max_output_tokens"] = max_tokens

        if instructions:
            responses_params["instructions"] = instructions

        tools = request_params.get("tools")
        if isinstance(tools, list) and tools:
            responses_params["tools"] = [
                self._convert_chat_tool_to_responses(tool)
                for tool in tools
                if isinstance(tool, Mapping)
            ]

        tool_choice = request_params.get("tool_choice")
        if tool_choice:
            if isinstance(tool_choice, str):
                responses_params["tool_choice"] = tool_choice
            elif isinstance(tool_choice, Mapping):
                choice_type = tool_choice.get("type")
                if choice_type == "function":
                    function_data = tool_choice.get("function", {})
                    if isinstance(function_data, Mapping) and function_data.get("name"):
                        responses_params["tool_choice"] = {
                            "type": "function",
                            "name": str(function_data["name"]),
                        }

        response_format = request_params.get("response_format")
        if isinstance(response_format, Mapping):
            fmt_type = response_format.get("type")
            if fmt_type == "json_object":
                responses_params["text"] = {"format": {"type": "json_object"}}
            elif fmt_type == "json_schema":
                json_schema = response_format.get("json_schema", {})
                json_schema_mapping = (
                    json_schema if isinstance(json_schema, Mapping) else {}
                )
                responses_params["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": str(json_schema_mapping.get("name", "ai_analysis_result")),
                        "schema": dict(cast(Mapping[str, object], json_schema_mapping.get("schema", {}))) if isinstance(json_schema_mapping.get("schema", {}), Mapping) else {},
                        "strict": bool(json_schema_mapping.get("strict", True)),
                    }
                }

        return responses_params

    def _convert_chat_tool_to_responses(
        self, tool: Mapping[str, object]
    ) -> dict[str, object]:
        tool_type = tool.get("type")
        if tool_type != "function":
            return dict(tool)

        function_data = tool.get("function", {})
        function_mapping = function_data if isinstance(function_data, Mapping) else {}
        return {
            "type": "function",
            "name": function_mapping.get("name"),
            "description": function_mapping.get("description"),
            "parameters": function_mapping.get("parameters", {}),
            "strict": True,
        }

    def _extract_and_validate_json(
        self, response_message: Mapping[str, object]
    ) -> Optional[AIAnalysisResult]:
        """
        从OpenAI响应中提取JSON内容并使用Pydantic验证
        兼容常规内容响应和Tool-calling响应

        Args:
            response_message: 标准化后的响应message字典

        Returns:
            验证后的AIAnalysisResult对象，失败返回None
        """
        json_data: Mapping[str, object] | None = None
        raw_tool_calls = response_message.get("tool_calls")
        tool_calls = cast(list[Mapping[str, object]], raw_tool_calls) if isinstance(raw_tool_calls, list) else []

        # 检查是否是Tool-calling响应
        if tool_calls:
            tool_call = tool_calls[0]
            tool_name = tool_call.get("name")
            if tool_name == "analyze_file_structure":
                logger.debug(f"[OpenAI识别] 识别到Tool-calling: {tool_name}")
                try:
                    parsed_payload = json.loads(str(tool_call.get("arguments") or ""))
                    if isinstance(parsed_payload, Mapping):
                        json_data = dict(parsed_payload)
                except json.JSONDecodeError as e:
                    logger.error(f"[OpenAI识别] 解析Tool-calling JSON失败: {e}")
                    logger.error(
                        "[OpenAI识别] 原始数据: "
                        f"{tool_call.get('arguments')}"
                    )
                    return None
        else:
            # 否则，从内容中提取
            content = response_message.get("content")
            logger.debug(f"[OpenAI识别] 普通内容响应: {content}")
            if isinstance(content, str) and content:
                json_data = self._extract_json_from_response(content)

        if not json_data:
            logger.error("[OpenAI识别] 未能从OpenAI响应中提取到任何JSON数据")
            return None

        normalized_data = self._normalize_ai_result_payload(json_data)

        try:
            # 使用Pydantic验证和解析
            result = AIAnalysisResult.model_validate(normalized_data)
            logger.info(f"[OpenAI识别] JSON结构验证成功，置信度: {result.confidence}")
            return result
        except ValidationError as e:
            logger.error(f"[OpenAI识别] JSON结构验证失败: {e}")
            logger.error(
                f"[OpenAI识别] 原始数据: {json.dumps(json_data, ensure_ascii=False, indent=2)}"
            )
            if normalized_data != json_data:
                logger.error(
                    "[OpenAI识别] 归一化后数据: "
                    f"{json.dumps(normalized_data, ensure_ascii=False, indent=2)}"
                )
            return None
        except Exception as e:
            logger.error(f"[OpenAI识别] 解析AI结果时发生未知错误: {str(e)}")
            return None

    def _normalize_ai_result_payload(
        self, data: Mapping[str, object]
    ) -> dict[str, object]:
        """归一化常见字段别名，提升不同模型输出的兼容性。"""
        raw_conflict_details = data.get("conflict_details")
        conflict_details: List[str] = []
        if isinstance(raw_conflict_details, list):
            for item in raw_conflict_details:
                text = self._coerce_conflict_detail(item)
                if text:
                    conflict_details.append(text)

        raw_unmatched = data.get("unmatched_files")
        unmatched_files: List[str] = []
        if isinstance(raw_unmatched, list):
            unmatched_files = [str(item) for item in raw_unmatched if item is not None]

        raw_confidence = (
            data.get("confidence")
            or data.get("overall_confidence")
            or data.get("confidence_level")
        )
        confidence = self._normalize_confidence(raw_confidence)

        reason = data.get("reason") or data.get("analysis_reason") or data.get("summary")
        if not reason and conflict_details:
            reason = "；".join(conflict_details[:2])
        if not reason:
            reason = "AI返回结果缺少reason，已自动补全"

        season_mapping: list[dict[str, object]] = []
        raw_season_mapping = data.get("season_mapping")
        if isinstance(raw_season_mapping, list):
            for item in raw_season_mapping:
                if not isinstance(item, Mapping):
                    continue
                local_group_name = (
                    item.get("local_group_name")
                    or item.get("group_name")
                    or item.get("local_group")
                    or item.get("folder")
                )
                raw_maps = (
                    item.get("maps_to_tmdb_seasons")
                    or item.get("tmdb_seasons")
                    or item.get("seasons")
                )
                if not local_group_name or not isinstance(raw_maps, list):
                    continue
                maps_to_tmdb_seasons: List[int] = []
                for season in raw_maps:
                    season_int = self._coerce_int(season)
                    if season_int is not None and season_int >= 0:
                        maps_to_tmdb_seasons.append(season_int)
                season_mapping.append(
                    {
                        "local_group_name": str(local_group_name),
                        "maps_to_tmdb_seasons": maps_to_tmdb_seasons,
                    }
                )

        file_mapping: list[dict[str, object]] = []
        raw_file_mapping = data.get("file_mapping")
        if isinstance(raw_file_mapping, list):
            for item in raw_file_mapping:
                normalized_item = self._normalize_mapping_item(item, confidence)
                if normalized_item:
                    file_mapping.append(normalized_item)

        if confidence in ["High", "Medium"] and not file_mapping:
            confidence = "Low"
            if not reason:
                reason = "AI返回结果缺少有效映射，已自动降级为Low"

        extra_notes = data.get("extra_notes")
        if extra_notes is not None and not isinstance(extra_notes, str):
            extra_notes = self._coerce_conflict_detail(extra_notes)

        return {
            "confidence": confidence,
            "reason": str(reason),
            "season_mapping": season_mapping,
            "file_mapping": file_mapping,
            "unmatched_files": unmatched_files,
            "conflict_details": conflict_details,
            "extra_notes": extra_notes,
        }

    def _normalize_mapping_item(
        self, item: object, default_confidence: str
    ) -> dict[str, object] | None:
        if not isinstance(item, Mapping):
            return None

        source_index = self._coerce_int(
            item.get("source_index")
            or item.get("source_id")
            or item.get("index")
            or item.get("file_index")
        )
        if source_index is not None and source_index < 1:
            source_index = None

        file_path = (
            item.get("file_path")
            or item.get("file")
            or item.get("path")
            or item.get("filename")
        )
        if file_path is not None:
            file_path = str(file_path).strip() or None

        if source_index is None and not file_path:
            return None

        tmdb_season = self._coerce_int(item.get("tmdb_season"))
        tmdb_episode = self._coerce_int(item.get("tmdb_episode"))

        if tmdb_season is None or tmdb_episode is None:
            tmdb_code = item.get("tmdb") or item.get("episode_code") or item.get("target")
            parsed = self._parse_tmdb_code(tmdb_code)
            if parsed:
                parsed_season, parsed_episode = parsed
                if tmdb_season is None:
                    tmdb_season = parsed_season
                if tmdb_episode is None:
                    tmdb_episode = parsed_episode

        if tmdb_season is None:
            tmdb_season = self._coerce_int(item.get("season"))
        if tmdb_episode is None:
            tmdb_episode = self._coerce_int(item.get("episode"))

        if tmdb_season is None or tmdb_episode is None:
            return None
        if tmdb_season < 0 or tmdb_episode < 1:
            return None

        episode_type = item.get("episode_type") or "regular"
        if tmdb_season == 0:
            # Season 0 统一按 special 处理，避免模型在 regular/special 上抖动
            episode_type = "special"
        elif not isinstance(episode_type, str):
            episode_type = "regular"
        else:
            episode_type = episode_type.lower()
            if episode_type not in ["regular", "special", "movie"]:
                episode_type = "regular"

        confidence = self._normalize_confidence(item.get("confidence") or default_confidence)

        return {
            "source_index": source_index,
            "file_path": file_path,
            "tmdb_season": tmdb_season,
            "tmdb_episode": tmdb_episode,
            "episode_type": episode_type,
            "confidence": confidence,
        }

    def _parse_tmdb_code(self, value: object) -> Optional[tuple[int, int]]:
        if not isinstance(value, str):
            return None

        patterns = [
            r"[Ss](\d{1,2})\s*[Ee](\d{1,3})",
            r"(\d{1,2})[xX](\d{1,3})",
            r"season\D*(\d{1,2})\D*episode\D*(\d{1,3})",
        ]
        for pattern in patterns:
            match = re.search(pattern, value)
            if match:
                return int(match.group(1)), int(match.group(2))
        return None

    def _coerce_int(self, value: object) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.isdigit() or (text.startswith('-') and text[1:].isdigit()):
                return int(text)
            match = re.search(r"-?\d+", text)
            if match:
                return int(match.group())
        return None

    def _normalize_confidence(self, value: object) -> str:
        if isinstance(value, str):
            text = value.strip().lower()
            mapping = {
                "high": "High",
                "medium": "Medium",
                "mid": "Medium",
                "low": "Low",
            }
            if text in mapping:
                return mapping[text]
        elif isinstance(value, (int, float)):
            if value >= 0.75:
                return "High"
            if value >= 0.4:
                return "Medium"
            return "Low"
        return "Medium"

    def _coerce_conflict_detail(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            detail = value.get("details") or value.get("message") or value.get("reason")
            type_name = value.get("type")
            if detail and type_name:
                return f"{type_name}: {detail}"
            if detail:
                return str(detail)
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _extract_json_from_response(
        self, content: str
    ) -> dict[str, object] | None:
        """
        从OpenAI响应中提取JSON内容，兼容思维链输出

        Args:
            content: OpenAI响应内容

        Returns:
            提取的JSON字典，失败返回None
        """
        try:
            # 首先尝试直接解析整个内容
            parsed_content = json.loads(content)
            if isinstance(parsed_content, Mapping):
                return dict(parsed_content)
        except json.JSONDecodeError:
            pass

        # 如果直接解析失败，尝试提取JSON部分
        # 查找可能的JSON块
        json_patterns = [
            r"```json\s*(\{.*?\})\s*```",  # ```json {} ```
            r"```\s*(\{.*?\})\s*```",  # ``` {} ```
            r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})",  # 最外层的{}块
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, content, re.DOTALL)
            for match in matches:
                try:
                    # 清理可能的思维链内容
                    cleaned_match = self._clean_json_content(match)
                    parsed_match = json.loads(cleaned_match)
                    if isinstance(parsed_match, Mapping):
                        return dict(parsed_match)
                except json.JSONDecodeError:
                    continue

        # 如果所有方法都失败，记录错误并返回None
        logger.error(f"[OpenAI识别] 无法从响应中提取有效JSON: {content[:200]}...")
        return None

    def _clean_json_content(self, json_str: str) -> str:
        """
        清理JSON字符串中可能的思维链内容

        Args:
            json_str: 原始JSON字符串

        Returns:
            清理后的JSON字符串
        """
        # 移除可能的思维链标记
        thinking_patterns = [
            r"<thinking>.*?</thinking>",
            r"思考：.*?(?=\{)",
            r"分析：.*?(?=\{)",
            r"推理：.*?(?=\{)",
        ]

        cleaned = json_str
        for pattern in thinking_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL)

        return cleaned.strip()

    def _get_json_schema(self) -> dict[str, object]:
        """生成符合OpenAI Tool格式的JSON Schema"""
        schema = self._build_strict_json_schema()
        return {
            "type": "function",
            "function": {
                "name": "analyze_file_structure",
                "description": "分析本地文件结构并返回与TMDB的映射关系",
                "parameters": schema,
            },
        }

    def _add_openai_json_instructions(self, base_prompt: str) -> str:
        """兼容旧调用路径：在 prompt 末尾追加 JSON 输出约束。"""
        return f"{base_prompt}\n\n{self._get_json_instructions().strip()}"

    def _get_json_instructions(self) -> str:
        """非 function_calling 模式下的通用 JSON 输出约束。"""
        return """
输出要求（必须遵守）：
1. 只返回一个 JSON 对象，不要输出任何解释文字。
2. 不要使用 Markdown 代码块（例如 ```json）。
3. 顶层字段必须使用：confidence, reason, season_mapping, file_mapping, unmatched_files, conflict_details, extra_notes。
4. 若某字段无内容，也必须返回合法空值（空数组或 null）。
"""

    def _get_structured_output_instructions(self) -> str:
        """structured_output 模式下补充约束，降低输出解释文本/代码块概率。"""
        return """
输出要求（必须遵守）：
1. 只返回一个 JSON 对象，不要输出任何解释文字。
2. 不要使用 Markdown 代码块（例如 ```json）。
3. 顶层字段必须使用：confidence, reason, season_mapping, file_mapping, unmatched_files, conflict_details, extra_notes。
4. 若某字段无内容，也必须返回合法空值（空数组或 null）。
"""

        """
        获取在system prompt中使用的、详细的JSON格式指令 (使用JSON Schema)

        Returns:
            包含JSON Schema和注意事项的说明字符串
        """
        # 从Pydantic模型动态生成JSON Schema，确保与验证模型一致
        schema = AIAnalysisResult.model_json_schema()

        # 移除Pydantic生成的顶层描述，使Schema更简洁
        schema.pop("title", None)
        schema.pop("description", None)

        schema_str = json.dumps(schema, indent=2, ensure_ascii=False)

        json_instructions = f"""
请严格按照以下JSON Schema格式返回分析结果。不要添加任何额外的解释或注释，只返回JSON对象。

JSON Schema:
```json
{schema_str}
```
"""
        return json_instructions

    def _build_strict_json_schema(self) -> dict[str, object]:
        """构建符合 OpenAI strict=true 要求的 JSON Schema。"""
        schema = cast(dict[str, object], deepcopy(AIAnalysisResult.model_json_schema()))

        def ensure_required_fields(obj: object) -> None:
            if isinstance(obj, dict):
                properties = obj.get("properties")
                if isinstance(properties, dict):
                    obj["required"] = list(properties.keys())
                    obj["additionalProperties"] = False

                any_of_items = obj.get("anyOf")
                if isinstance(any_of_items, list):
                    for item in any_of_items:
                        ensure_required_fields(item)

                all_of_items = obj.get("allOf")
                if isinstance(all_of_items, list):
                    for item in all_of_items:
                        ensure_required_fields(item)

                one_of_items = obj.get("oneOf")
                if isinstance(one_of_items, list):
                    for item in one_of_items:
                        ensure_required_fields(item)

                for value in obj.values():
                    ensure_required_fields(value)
            elif isinstance(obj, list):
                for item in obj:
                    ensure_required_fields(item)

        ensure_required_fields(schema)
        return schema

    def _configure_output_format(
        self,
        request_params: dict[str, object],
        output_format: str | None = None,
    ) -> None:
        """
        根据配置的输出格式类型配置请求参数

        Args:
            request_params: 请求参数字典，会被直接修改
        """
        effective_format = output_format or self._resolve_output_format()

        if effective_format == "function_calling":
            request_params["tools"] = [self._get_json_schema()]
            request_params["tool_choice"] = {
                "type": "function",
                "function": {"name": "analyze_file_structure"},
            }
            # request_params["tool_choice"] = "auto"
        elif effective_format == "json_object":
            request_params["response_format"] = {"type": "json_object"}
        elif effective_format == "structured_output":
            # 使用新的structured output API
            request_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "ai_analysis_result",
                    "strict": True,
                    "schema": self._build_strict_json_schema(),
                },
            }
        # 如果是"text"格式，不添加任何特殊参数
