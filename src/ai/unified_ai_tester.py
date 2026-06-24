#!/usr/bin/env python3
"""
统一AI测试器 - 使用当前界面配置进行测试

支持功能：
1. AI识别功能测试 - 使用项目测试用例进行完整的AI识别测试
2. OpenAI API多格式测试 - 测试 structured_output、function_calling、text 三种输出格式

特点：
- 使用当前界面配置，不保存配置
- 统一的测试架构，代码复用
- 异步处理避免UI阻塞
- 项目相关测试用例
"""

import json
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import TypedDict, cast

from ..logger import logger
from ..ai.client import AIClient
from ..config.config_manager import cm
from ..ai.models import AIAnalysisResult


ConfigValue = object
ConfigDict = dict[str, ConfigValue]


class ExpectedMappingEntry(TypedDict, total=False):
    file_path: str
    tmdb_season: int
    tmdb_episode: int
    episode_type: str
    confidence: str


class ComparableMappingEntry(TypedDict):
    tmdb_season: int
    tmdb_episode: int
    episode_type: str
    confidence: str


class ExpectedResult(TypedDict, total=False):
    file_mapping: list[ExpectedMappingEntry]


class ValidationDetails(TypedDict, total=False):
    error: str
    expected_count: int
    actual_count: int
    matched_count: int
    accuracy: float
    matched_files: list[str]
    missing_files: list[str]
    extra_files: list[str]


class ValidationResult(TypedDict):
    success: bool
    confidence: str
    file_mapping_count: int
    validation_details: ValidationDetails


class TestRunResult(TypedDict, total=False):
    success: bool
    error: str | None
    duration: float
    ai_result: AIAnalysisResult | None
    validation: ValidationResult | None
    config_used: ConfigDict
    configured_interface: str | None
    actual_interface: str | None
    interface_fallback: bool
    interface_fallback_reason: str | None
    provider: str
    result_status: str
    output_format: str
    run_index: int


class PersistedFormatStat(TypedDict, total=False):
    total_runs: int
    success_runs: int
    perfect_runs: int
    last_result_status: str | None
    last_error: str | None
    last_duration: float


class AggregateRunSummary(TypedDict):
    success_runs: int
    perfect_runs: int
    validation_failed_runs: int
    ai_failed_runs: int


class AggregateStressResult(TypedDict):
    success: bool
    provider: str
    output_format: str
    rounds: int
    parallel_workers: int
    run_results: list[TestRunResult]
    summary: AggregateRunSummary


class ProviderFormatResult(TypedDict):
    success: bool
    format_results: list[TestRunResult]
    successful_formats: list[str]
    recommended_format: str


class UnifiedAITester:
    """统一的AI测试器，使用当前界面配置进行测试"""

    def __init__(self, current_config: Mapping[str, object]):
        """
        Args:
            current_config: 当前界面配置字典
        """
        self.current_config: ConfigDict = dict(current_config)
        self.original_config: ConfigDict = {}
        self.test_case_path: Path = (
            Path(__file__).parent.parent.parent / "tests" / "example_test_case.json"
        )
        self.expected_path: Path = (
            Path(__file__).parent.parent.parent / "tests" / "example_expected.json"
        )

    def _apply_current_config(self) -> None:
        """应用当前界面配置"""
        logger.info("[AI识别测试] 应用当前界面配置")
        for key, value in self.current_config.items():
            self.original_config[key] = cm.get_config(key)
            if isinstance(value, (str, bool)):
                _ = cm.set_config(key, value)
            if "api_key" in key:
                value = len(str(value)) * '*'
            logger.debug(f"[AI识别测试] 设置配置 {key}: {value}")


    def _restore_config(self) -> None:
        """恢复原始配置"""
        logger.info("[AI识别测试] 恢复原始配置")
        for key, value in self.original_config.items():
            if isinstance(value, (str, bool)):
                _ = cm.set_config(key, value)
            if "api_key" in key:
                value = len(str(value)) * '*'
            logger.debug(f"[AI识别测试] 恢复配置 {key}: {value}")
        self.original_config.clear()

    def _load_test_case(self) -> dict[str, object] | None:
        """加载测试用例"""
        try:
            if not self.test_case_path.exists():
                logger.error(f"[AI识别测试] 测试用例文件不存在: {self.test_case_path}")
                return None

            with open(self.test_case_path, 'r', encoding='utf-8') as f:
                raw_test_case = cast(object, json.load(f))
                if not isinstance(raw_test_case, dict):
                    logger.error("[AI识别测试] 测试用例根对象不是JSON对象")
                    return None
                test_case = cast(dict[str, object], raw_test_case)
                metadata = test_case.get("metadata")
                metadata_mapping = cast(
                    Mapping[str, object], metadata
                ) if isinstance(metadata, Mapping) else {}
                logger.info(
                    f"[AI识别测试] 成功加载测试用例: {metadata_mapping.get('path_name', 'Unknown')}"
                )
                return test_case
        except Exception as e:
            logger.error(f"[AI识别测试] 加载测试用例失败: {str(e)}")
            return None

    def _load_expected_result(self) -> ExpectedResult | None:
        """加载期望结果"""
        try:
            if not self.expected_path.exists():
                logger.warning(f"[AI识别测试] 期望结果文件不存在: {self.expected_path}")
                return None

            with open(self.expected_path, 'r', encoding='utf-8') as f:
                raw_expected = cast(object, json.load(f))
                if not isinstance(raw_expected, dict):
                    logger.error("[AI识别测试] 期望结果根对象不是JSON对象")
                    return None
                expected = cast(ExpectedResult, cast(object, raw_expected))
                logger.info("[AI识别测试] 成功加载期望结果")
                return expected
        except Exception as e:
            logger.error(f"[AI识别测试] 加载期望结果失败: {str(e)}")
            return None

    def _validate_ai_result(
        self, ai_result: AIAnalysisResult | None, expected: ExpectedResult | None = None
    ) -> ValidationResult:
        """验证AI分析结果"""
        validation_result: ValidationResult = {
            "success": False,
            "confidence": ai_result.confidence if ai_result else "None",
            "file_mapping_count": 0,
            "validation_details": {},
        }

        if not ai_result:
            validation_result["validation_details"]["error"] = "AI分析失败，返回None"
            return validation_result

        # 基本验证
        validation_result["success"] = True
        validation_result["file_mapping_count"] = len(ai_result.file_mapping)

        # 如果有期望结果，进行详细验证
        if expected and "file_mapping" in expected:
            expected_mapping_list = expected["file_mapping"]  # 这是一个字典列表

            # 将期望结果转换为字典，key为file_path
            expected_mapping: dict[str, ComparableMappingEntry] = {}
            for item in expected_mapping_list:
                file_path = item.get("file_path")
                if not file_path:
                    continue
                expected_mapping[file_path] = {
                    "tmdb_season": int(item.get("tmdb_season", 0)),
                    "tmdb_episode": int(item.get("tmdb_episode", 0)),
                    "episode_type": item.get("episode_type", "regular"),
                    "confidence": item.get("confidence", "Medium"),
                }

            # 将AI结果转换为字典，key为file_path
            # 注：AIEpisodeMapping（AI 原始输出）只含 legal_node_id（如 tmdb:S01E03），
            # 不含派生字段 tmdb_season/tmdb_episode（那是子类 EpisodeMapping 的字段）。
            # 这里从 legal_node_id 解析季集，避免 AttributeError。
            _node_re = re.compile(r"tmdb:S(\d+)E(\d+)")
            actual_mapping: dict[str, ComparableMappingEntry] = {}
            for item in ai_result.file_mapping:
                file_path = item.file_path or f"#{item.source_index}"
                season, episode = 0, 0
                m = _node_re.match(getattr(item, "legal_node_id", "") or "")
                if m:
                    season, episode = int(m.group(1)), int(m.group(2))
                actual_mapping[file_path] = {
                    "tmdb_season": season,
                    "tmdb_episode": episode,
                    "episode_type": item.episode_type,
                    "confidence": item.confidence,
                }

            # 计算匹配情况
            matched_count = 0
            total_expected = len(expected_mapping)
            matched_files: list[str] = []
            missing_files: list[str] = []
            extra_files: list[str] = []

            for file_path, expected_info in expected_mapping.items():
                if file_path in actual_mapping:
                    actual_info = actual_mapping[file_path]
                    # 检查关键字段是否匹配（不包括confidence）
                    if (
                        actual_info["tmdb_season"] == expected_info["tmdb_season"]
                        and actual_info["tmdb_episode"] == expected_info["tmdb_episode"]
                        and actual_info["episode_type"] == expected_info["episode_type"]
                    ):
                        matched_count += 1
                        matched_files.append(file_path)
                else:
                    missing_files.append(file_path)

            # 检查AI结果中是否有期望结果中没有的文件
            for file_path in actual_mapping:
                if file_path not in expected_mapping:
                    extra_files.append(file_path)

            validation_result["validation_details"] = {
                "expected_count": total_expected,
                "actual_count": len(actual_mapping),
                "matched_count": matched_count,
                "accuracy": matched_count / total_expected if total_expected > 0 else 0,
                "matched_files": matched_files,
                "missing_files": missing_files,
                "extra_files": extra_files,
            }

        return validation_result

    def _run_single_ai_test(self) -> TestRunResult:
        """运行单次AI识别测试（核心复用逻辑）"""
        start_time = time.time()
        result: TestRunResult = {
            "success": False,
            "error": None,
            "duration": 0.0,
            "ai_result": None,
            "validation": None,
            "config_used": self.current_config.copy(),
            "configured_interface": None,
            "actual_interface": None,
            "interface_fallback": False,
            "interface_fallback_reason": None,
        }

        try:
            with cm.temporary_config(self.current_config):
                # 加载测试用例
                test_case = self._load_test_case()
                if not test_case:
                    result["error"] = "无法加载测试用例"
                    return result

                # 创建AI客户端
                ai_client = AIClient()
                if not ai_client.is_available():
                    result["error"] = f"AI客户端不可用 - 提供商: {ai_client.provider}"
                    return result

                # 执行AI分析
                logger.info(f"[AI识别测试] 开始AI分析 - 提供商: {ai_client.provider}")
                anime_info = test_case.get("anime_info")
                local_files = test_case.get("local_files")
                anime_info_mapping = cast(
                    Mapping[str, object], anime_info
                ) if isinstance(anime_info, Mapping) else {}
                local_files_sequence = cast(
                    Sequence[object], local_files
                ) if isinstance(local_files, Sequence) else []
                ai_result = ai_client.analyze_episode_mapping(
                    cast(dict[str, object], dict(anime_info_mapping)),
                    [
                        dict(item)
                        for item in local_files_sequence
                        if isinstance(item, Mapping)
                    ],
                )

                # 加载期望结果并验证
                expected = self._load_expected_result()
                validation = self._validate_ai_result(ai_result, expected)

                # 分类结果状态
                if ai_result is None:
                    result_status = "ai_failed"  # AI请求或解析失败
                elif validation and validation.get("validation_details"):
                    details = validation["validation_details"]
                    accuracy = details.get("accuracy", 0)
                    missing_files = details.get("missing_files", [])
                    extra_files = details.get("extra_files", [])

                    if (
                        accuracy == 1.0
                        and len(missing_files) == 0
                        and len(extra_files) == 0
                    ):
                        result_status = "perfect"  # 完全正确
                    else:
                        result_status = "validation_failed"  # 结果验证不正确
                else:
                    result_status = "validation_failed"  # 无法验证，视为验证失败

                result.update(
                    {
                        "success": ai_result is not None,
                        "ai_result": ai_result,
                        "validation": validation,
                        "provider": ai_client.provider,
                        "result_status": result_status,
                    }
                )

                provider_runtime = ai_client.get_provider_runtime_info()
                configured_interface = provider_runtime.get("configured_interface")
                actual_interface = provider_runtime.get("actual_interface")
                interface_fallback = provider_runtime.get("interface_fallback")
                interface_fallback_reason = provider_runtime.get(
                    "interface_fallback_reason"
                )
                if isinstance(configured_interface, str) or configured_interface is None:
                    result["configured_interface"] = configured_interface
                if isinstance(actual_interface, str) or actual_interface is None:
                    result["actual_interface"] = actual_interface
                result["interface_fallback"] = bool(interface_fallback)
                if (
                    isinstance(interface_fallback_reason, str)
                    or interface_fallback_reason is None
                ):
                    result["interface_fallback_reason"] = interface_fallback_reason

                logger.info(f"[AI识别测试] AI分析完成 - 成功: {result['success']}")

        except Exception as e:
            logger.error(f"[AI识别测试] AI测试异常: {str(e)}")
            result["error"] = str(e)
            result["result_status"] = "ai_failed"
        finally:
            result["duration"] = time.time() - start_time

        return result

    def test_ai_recognition(self) -> TestRunResult:
        """测试AI识别功能"""
        logger.info("[AI识别测试] 开始AI识别功能测试")
        return self._run_single_ai_test()

    def test_openai_api_formats(self) -> ProviderFormatResult:
        """测试OpenAI API的多种输出格式支持"""
        logger.info("[AI识别测试] 开始OpenAI API多格式测试")

        result = self._test_provider_api_formats(
            provider="openai",
            format_key="openai_output_format",
            formats_to_test=["structured_output", "function_calling", "text"],
        )

        # 记忆OpenAI可用格式与排序（用于运行时自动路由）
        self._persist_provider_format_memory("openai", result)

        return result

    def stress_test_openai_structured_output(
        self,
        rounds: int = 5,
        max_workers: int | None = None,
    ) -> AggregateStressResult:
        """OpenAI structured_output 并行专项压测。"""
        return self._stress_test_single_format(
            provider="openai",
            format_key="openai_output_format",
            output_format="structured_output",
            rounds=rounds,
            max_workers=max_workers,
        )

    def _stress_test_single_format(
        self,
        provider: str,
        format_key: str,
        output_format: str,
        rounds: int,
        max_workers: int | None = None,
    ) -> AggregateStressResult:
        """对指定 provider 的单一格式执行全并行压测。"""
        total_runs = max(1, int(rounds))
        workers = max_workers if max_workers and max_workers > 0 else total_runs
        workers = max(1, min(workers, total_runs))

        logger.info(
            "[AI识别测试] 开始单格式并行压测: "
            f"provider={provider}, format={output_format}, "
            f"rounds={total_runs}, workers={workers}"
        )

        def _run_once(run_index: int) -> TestRunResult:
            temp_config = self.current_config.copy()
            temp_config[format_key] = output_format
            temp_config[f"{provider}_auto_routing_enabled"] = False

            temp_tester = UnifiedAITester(temp_config)
            result = temp_tester._run_single_ai_test()
            result["output_format"] = output_format
            result["run_index"] = run_index
            return result

        indexed_results: dict[int, TestRunResult] = {}

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_idx = {
                executor.submit(_run_once, run_index): run_index
                for run_index in range(1, total_runs + 1)
            }

            for future in as_completed(future_to_idx):
                run_index = future_to_idx[future]
                try:
                    indexed_results[run_index] = future.result()
                except Exception as e:
                    logger.error(
                        "[AI识别测试] 单格式并行压测异常: "
                        f"run={run_index}, provider={provider}, "
                        f"format={output_format}, error={str(e)}"
                    )
                    indexed_results[run_index] = {
                        "success": False,
                        "error": str(e),
                        "duration": 0.0,
                        "ai_result": None,
                        "validation": None,
                        "config_used": {
                            **self.current_config.copy(),
                            format_key: output_format,
                        },
                        "provider": provider,
                        "result_status": "ai_failed",
                        "output_format": output_format,
                        "run_index": run_index,
                    }

        run_results = [
            indexed_results[idx]
            for idx in range(1, total_runs + 1)
            if idx in indexed_results
        ]

        success_runs = sum(1 for item in run_results if item.get("success"))
        perfect_runs = sum(
            1 for item in run_results if item.get("result_status") == "perfect"
        )
        validation_failed_runs = sum(
            1
            for item in run_results
            if item.get("result_status") == "validation_failed"
        )
        ai_failed_runs = sum(
            1 for item in run_results if item.get("result_status") == "ai_failed"
        )

        result: AggregateStressResult = {
            "success": success_runs > 0,
            "provider": provider,
            "output_format": output_format,
            "rounds": total_runs,
            "parallel_workers": workers,
            "run_results": run_results,
            "summary": {
                "success_runs": success_runs,
                "perfect_runs": perfect_runs,
                "validation_failed_runs": validation_failed_runs,
                "ai_failed_runs": ai_failed_runs,
            },
        }

        logger.info(
            "[AI识别测试] 单格式并行压测完成: "
            f"provider={provider}, format={output_format}, "
            f"summary={result['summary']}"
        )
        return result

    def _test_provider_api_formats(
        self,
        provider: str,
        format_key: str,
        formats_to_test: list[str],
    ) -> ProviderFormatResult:
        """按提供商执行多格式测试并汇总结果（并行执行所有格式）"""

        def _run_single_format(output_format: str) -> TestRunResult:
            logger.info(f"[AI识别测试] 测试输出格式: {output_format}")

            temp_config = self.current_config.copy()
            temp_config[format_key] = output_format
            temp_config[f"{provider}_auto_routing_enabled"] = False

            temp_tester = UnifiedAITester(temp_config)
            result = temp_tester._run_single_ai_test()
            result["output_format"] = output_format
            return result

        indexed_results: dict[int, TestRunResult] = {}
        max_workers = max(1, len(formats_to_test))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(_run_single_format, output_format): idx
                for idx, output_format in enumerate(formats_to_test)
            }

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                output_format = formats_to_test[idx]
                try:
                    indexed_results[idx] = future.result()
                except Exception as e:
                    logger.error(
                        f"[AI识别测试] 输出格式 {output_format} 并行测试异常: {str(e)}"
                    )
                    indexed_results[idx] = {
                        "success": False,
                        "error": str(e),
                        "duration": 0.0,
                        "ai_result": None,
                        "validation": None,
                        "config_used": {
                            **self.current_config.copy(),
                            format_key: output_format,
                        },
                        "provider": provider,
                        "result_status": "ai_failed",
                        "output_format": output_format,
                    }

        format_results = [
            indexed_results[idx]
            for idx in range(len(formats_to_test))
            if idx in indexed_results
        ]
        successful_formats = [
            item.get("output_format", "")
            for item in format_results
            if item.get("success") and isinstance(item.get("output_format"), str)
        ]

        overall_result: ProviderFormatResult = {
            "success": len(successful_formats) > 0,
            "format_results": format_results,
            "successful_formats": successful_formats,
            "recommended_format": self._get_recommended_format(
                format_results, formats_to_test
            ),
        }

        logger.info(
            f"[AI识别测试] {provider.upper()}多格式测试完成 - 成功格式: {successful_formats}"
        )
        return overall_result

    def _persist_provider_format_memory(
        self,
        provider: str,
        test_result: Mapping[str, object],
    ) -> None:
        """保存指定提供商格式测试统计，并固定自动路由顺序。"""
        try:
            raw_format_results = test_result.get("format_results", [])
            format_results = (
                cast(list[Mapping[str, object]], raw_format_results)
                if isinstance(raw_format_results, list)
                else []
            )
            if not format_results:
                return

            stats_key = f"{provider}_format_stats"
            order_key = f"{provider}_auto_format_order"
            enabled_key = f"{provider}_auto_routing_enabled"

            fixed_orders = {
                "openai": [
                    "structured_output",
                    "function_calling",
                    "text",
                ],
            }
            allowed_formats = fixed_orders.get(provider, ["text"])

            existing_stats = cm.get_config(stats_key) or {}
            if not isinstance(existing_stats, dict):
                existing_stats = {}

            updated_stats = cast(dict[str, PersistedFormatStat], deepcopy(existing_stats))

            for item in format_results:
                fmt_value = item.get("output_format")
                fmt = fmt_value if isinstance(fmt_value, str) else ""
                if not fmt or fmt not in allowed_formats:
                    continue

                stat = updated_stats.get(fmt, PersistedFormatStat())

                total_runs_value = stat.get("total_runs", 0)
                success_runs_value = stat.get("success_runs", 0)
                perfect_runs_value = stat.get("perfect_runs", 0)
                total_runs = total_runs_value if isinstance(total_runs_value, int) else 0
                success_runs = success_runs_value if isinstance(success_runs_value, int) else 0
                perfect_runs = perfect_runs_value if isinstance(perfect_runs_value, int) else 0
                total_runs += 1

                if item.get("success"):
                    success_runs += 1

                if item.get("result_status") == "perfect":
                    perfect_runs += 1

                result_status_value = item.get("result_status")
                last_result_status = (
                    result_status_value if isinstance(result_status_value, str) else None
                )
                error_value = item.get("error")
                last_error = error_value if isinstance(error_value, str) else None
                duration_value = item.get("duration", 0)
                last_duration = (
                    float(duration_value)
                    if isinstance(duration_value, (int, float))
                    else 0.0
                )

                stat["total_runs"] = total_runs
                stat["success_runs"] = success_runs
                stat["perfect_runs"] = perfect_runs
                stat["last_result_status"] = last_result_status
                stat["last_error"] = last_error
                stat["last_duration"] = last_duration
                updated_stats[fmt] = stat

            _ = cm.set_config(enabled_key, True)
        
            runtime_overrides = cast(dict[str, object], cm._get_runtime_overrides())
            runtime_overrides[stats_key] = updated_stats
            runtime_overrides[order_key] = allowed_formats

            logger.info(
                f"[AI识别测试] 已更新{provider.upper()}自动路由顺序: "
                f"{allowed_formats}"
            )
        except Exception as e:
            logger.error(f"[AI识别测试] 保存{provider.upper()}格式记忆失败: {e}")


    def _get_recommended_format(
        self,
        format_results: Sequence[Mapping[str, object]],
        priority_order: Sequence[str] | None = None,
    ) -> str:
        """根据测试结果推荐最佳格式"""
        if priority_order is None:
            priority_order = ["structured_output", "function_calling", "text"]

        # 当前使用简单测试用例，不允许出错，只要有错误就标记为失败
        perfect_formats: list[str] = []  # 完全正确的格式

        for result in format_results:
            if not result.get("success", False):
                continue

            output_format_value = result.get("output_format", "")
            output_format = output_format_value if isinstance(output_format_value, str) else ""
            validation_value = result.get("validation", {})
            validation = validation_value if isinstance(validation_value, Mapping) else {}

            # 检查是否完全正确（100%准确率）
            is_perfect = False
            if validation and "validation_details" in validation:
                validation_details_value = validation["validation_details"]
                validation_details = (
                    validation_details_value
                    if isinstance(validation_details_value, Mapping)
                    else {}
                )
                accuracy_value = validation_details.get("accuracy", 0)
                accuracy = accuracy_value if isinstance(accuracy_value, (int, float)) else 0
                missing_files_value = validation_details.get("missing_files", [])
                extra_files_value = validation_details.get("extra_files", [])
                missing_files = missing_files_value if isinstance(missing_files_value, list) else []
                extra_files = extra_files_value if isinstance(extra_files_value, list) else []

                # 必须100%准确率，且没有遗漏文件和多余文件
                if (
                    accuracy == 1.0
                    and len(missing_files) == 0
                    and len(extra_files) == 0
                ):
                    is_perfect = True

            if is_perfect:
                perfect_formats.append(output_format)

        # 从完全正确的格式中按优先级选择
        if perfect_formats:
            for preferred_format in priority_order:
                if preferred_format in perfect_formats:
                    logger.info(f"[AI识别测试] 推荐格式: {preferred_format} (完全正确)")
                    return preferred_format
            # 如果优先级列表中没有，选择第一个完全正确的
            best_format = perfect_formats[0]
            logger.info(f"[AI识别测试] 推荐格式: {best_format} (完全正确)")
            return best_format

        successful_formats: list[str] = []
        for result in format_results:
            if not result.get("success", False):
                continue

            output_format_value = result.get("output_format", "")
            output_format = (
                output_format_value if isinstance(output_format_value, str) else ""
            )
            if output_format:
                successful_formats.append(output_format)

        if successful_formats:
            for preferred_format in priority_order:
                if preferred_format in successful_formats:
                    logger.info(
                        f"[AI识别测试] 推荐格式: {preferred_format} (可用但非完全正确)"
                    )
                    return preferred_format

            fallback_success_format = successful_formats[0]
            logger.info(
                f"[AI识别测试] 推荐格式: {fallback_success_format} (可用但非完全正确)"
            )
            return fallback_success_format

        # 如果没有完全正确的格式，回退到text
        logger.warning("[AI识别测试] 没有完全正确的格式，回退到text")
        return "text"
