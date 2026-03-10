#!/usr/bin/env python3
"""OpenAI 接口双链路验证脚本。

验证内容：
1. 主链路（OpenAIClient.analyze_episode_mapping）
2. 简单链路（AIClient.extract_title_and_type -> _call_openai_simple）

验证场景：
- responses_supported: 配置 responses_api，实际命中 responses_api
- responses_fallback: 配置 responses_api，responses 失败后回退 chat_completions
- chat_only: 配置 chat_completions，始终命中 chat_completions

说明：
- 本脚本使用方法打桩验证接口分发与回退逻辑，不依赖真实 API 请求。

用法：
  python tests/test_openai_interface_e2e.py --all
  python tests/test_openai_interface_e2e.py --scenario responses_fallback
"""

import argparse
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai.client import AIClient
from src.ai.models import AIAnalysisResult
from src.config.config_manager import cm
from src.logger import logger


@dataclass
class ScenarioResult:
    scenario: str
    passed: bool
    details: str


def _build_main_success_payload(*_args, **_kwargs) -> Dict[str, object]:
    """主链路可解析 payload。"""
    return {
        "content": json.dumps(
            {
                "confidence": "High",
                "reason": "interface dispatch test",
                "season_mapping": [],
                "file_mapping": [
                    {
                        "file_path": "test01.mkv",
                        "tmdb_season": 1,
                        "tmdb_episode": 1,
                        "episode_type": "regular",
                        "confidence": "High",
                    }
                ],
                "unmatched_files": [],
                "conflict_details": [],
                "extra_notes": None,
            },
            ensure_ascii=False,
        ),
        "tool_calls": [],
    }


def _build_simple_success_payload(*_args, **_kwargs) -> Dict[str, object]:
    """简单链路可解析 payload。"""
    return {
        "content": (
            '{"title":"Test Anime",'
            '"fallback_title":"Test",'
            '"type":"tv"}'
        ),
        "tool_calls": [],
    }


def _raise_responses_unsupported(*_args, **_kwargs):
    raise RuntimeError("mock /responses unsupported")


@contextmanager
def _patch_methods(client_obj, patches: Dict[str, Callable]):
    originals = {}
    try:
        for name, replacement in patches.items():
            originals[name] = getattr(client_obj, name)
            setattr(client_obj, name, replacement)
        yield
    finally:
        for name, original in originals.items():
            setattr(client_obj, name, original)


def _snapshot_configs() -> Dict[str, object]:
    keys = [
        "ai_provider",
        "openai_api_interface",
        "openai_output_format",
        "openai_auto_routing_enabled",
    ]
    return {k: cm.get_config(k) for k in keys}


def _restore_configs(snapshot: Dict[str, object]) -> None:
    for key, value in snapshot.items():
        cm.set_config(key, value)


def _prepare_openai_base(interface_value: str) -> None:
    cm.set_config("ai_provider", "openai")
    cm.set_config("openai_api_interface", interface_value)
    cm.set_config("openai_output_format", "function_calling")
    cm.set_config("openai_auto_routing_enabled", False)


def _run_main_chain(ai_client: AIClient) -> Optional[AIAnalysisResult]:
    anime_info = {
        "name": "Test Anime",
        "seasons": [{"season_number": 1, "episode_count": 1}],
    }
    local_files = [{"path": "test01.mkv", "duration": 24.0}]
    return ai_client.analyze_episode_mapping(anime_info, local_files)


def _run_simple_chain(ai_client: AIClient) -> Optional[tuple[str, Optional[str]]]:
    return ai_client.extract_title_and_type(
        "[TestGroup] Test Anime S01E01 [WEB-DL 1080p].mkv"
    )


def _capture_interface_state(ai_client: AIClient) -> Dict[str, object]:
    client = ai_client._client
    return {
        "configured": getattr(client, "last_configured_api_interface", None),
        "actual": getattr(client, "last_actual_api_interface", None),
        "fallback": bool(getattr(client, "last_api_interface_fallback", False)),
        "fallback_reason": getattr(client, "last_api_interface_fallback_reason", None),
    }


def _assert_state(
    label: str,
    state: Dict[str, object],
    expected_configured: str,
    expected_actual: str,
    expected_fallback: bool,
) -> None:
    assert state["configured"] == expected_configured, (
        f"{label}: configured mismatch, got={state['configured']}, "
        f"expected={expected_configured}"
    )
    assert state["actual"] == expected_actual, (
        f"{label}: actual mismatch, got={state['actual']}, expected={expected_actual}"
    )
    assert state["fallback"] == expected_fallback, (
        f"{label}: fallback mismatch, got={state['fallback']}, "
        f"expected={expected_fallback}"
    )


def _scenario_responses_supported() -> ScenarioResult:
    """配置 responses_api，主/简单链路都命中 responses_api。"""
    snapshot = _snapshot_configs()
    try:
        _prepare_openai_base("responses_api")
        ai_client = AIClient()
        client = ai_client._client

        with _patch_methods(
            client,
            {"_call_via_responses_api": _build_main_success_payload},
        ):
            main_result = _run_main_chain(ai_client)
            assert main_result is not None
            main_state = _capture_interface_state(ai_client)
            _assert_state(
                "main_chain",
                main_state,
                expected_configured="responses_api",
                expected_actual="responses_api",
                expected_fallback=False,
            )

        with _patch_methods(
            client,
            {
                "_call_via_responses_api": _build_simple_success_payload,
                "_call_via_chat_completions": _build_simple_success_payload,
            },
        ):
            simple_result = _run_simple_chain(ai_client)
            assert simple_result is not None
            assert simple_result == ("Test Anime", "tv")
            simple_state = _capture_interface_state(ai_client)
            _assert_state(
                "simple_chain",
                simple_state,
                expected_configured="responses_api",
                expected_actual="responses_api",
                expected_fallback=False,
            )

        return ScenarioResult(
            scenario="responses_supported",
            passed=True,
            details=f"main={main_state}, simple={simple_state}",
        )
    except Exception as e:
        return ScenarioResult(
            scenario="responses_supported",
            passed=False,
            details=str(e),
        )
    finally:
        _restore_configs(snapshot)


def _scenario_responses_fallback() -> ScenarioResult:
    """配置 responses_api，responses 失败后主/简单链路回退 chat_completions。"""
    snapshot = _snapshot_configs()
    try:
        _prepare_openai_base("responses_api")
        ai_client = AIClient()
        client = ai_client._client

        with _patch_methods(
            client,
            {
                "_call_via_responses_api": _raise_responses_unsupported,
                "_call_via_chat_completions": _build_main_success_payload,
            },
        ):
            main_result = _run_main_chain(ai_client)
            assert main_result is not None
            main_state = _capture_interface_state(ai_client)
            _assert_state(
                "main_chain",
                main_state,
                expected_configured="responses_api",
                expected_actual="chat_completions",
                expected_fallback=True,
            )

        with _patch_methods(
            client,
            {
                "_call_via_responses_api": _raise_responses_unsupported,
                "_call_via_chat_completions": _build_simple_success_payload,
            },
        ):
            simple_result = _run_simple_chain(ai_client)
            assert simple_result is not None
            assert simple_result == ("Test Anime", "tv")
            simple_state = _capture_interface_state(ai_client)
            _assert_state(
                "simple_chain",
                simple_state,
                expected_configured="responses_api",
                expected_actual="chat_completions",
                expected_fallback=True,
            )

        return ScenarioResult(
            scenario="responses_fallback",
            passed=True,
            details=f"main={main_state}, simple={simple_state}",
        )
    except Exception as e:
        return ScenarioResult(
            scenario="responses_fallback",
            passed=False,
            details=str(e),
        )
    finally:
        _restore_configs(snapshot)


def _scenario_chat_only() -> ScenarioResult:
    """配置 chat_completions，主/简单链路都命中 chat_completions。"""
    snapshot = _snapshot_configs()
    try:
        _prepare_openai_base("chat_completions")
        ai_client = AIClient()
        client = ai_client._client

        with _patch_methods(
            client,
            {"_call_via_chat_completions": _build_main_success_payload},
        ):
            main_result = _run_main_chain(ai_client)
            assert main_result is not None
            main_state = _capture_interface_state(ai_client)
            _assert_state(
                "main_chain",
                main_state,
                expected_configured="chat_completions",
                expected_actual="chat_completions",
                expected_fallback=False,
            )

        with _patch_methods(
            client,
            {"_call_via_chat_completions": _build_simple_success_payload},
        ):
            simple_result = _run_simple_chain(ai_client)
            assert simple_result is not None
            assert simple_result == ("Test Anime", "tv")
            simple_state = _capture_interface_state(ai_client)
            _assert_state(
                "simple_chain",
                simple_state,
                expected_configured="chat_completions",
                expected_actual="chat_completions",
                expected_fallback=False,
            )

        return ScenarioResult(
            scenario="chat_only",
            passed=True,
            details=f"main={main_state}, simple={simple_state}",
        )
    except Exception as e:
        return ScenarioResult(
            scenario="chat_only",
            passed=False,
            details=str(e),
        )
    finally:
        _restore_configs(snapshot)


def _run_and_print(result: ScenarioResult) -> bool:
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.scenario}")
    print(f"  {result.details}")
    return result.passed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OpenAI 接口双链路验证脚本"
    )
    parser.add_argument(
        "--scenario",
        choices=["responses_supported", "responses_fallback", "chat_only"],
        help="仅运行指定场景",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="运行全部场景",
    )

    args = parser.parse_args()

    scenario_funcs: Dict[str, Callable[[], ScenarioResult]] = {
        "responses_supported": _scenario_responses_supported,
        "responses_fallback": _scenario_responses_fallback,
        "chat_only": _scenario_chat_only,
    }

    if args.all or not args.scenario:
        selected = ["responses_supported", "responses_fallback", "chat_only"]
    else:
        selected = [args.scenario]

    logger.info(f"[OpenAI接口E2E] 开始执行场景: {', '.join(selected)}")

    ok = True
    for scenario_name in selected:
        result = scenario_funcs[scenario_name]()
        if not _run_and_print(result):
            ok = False

    if ok:
        print("\nOpenAI 接口双链路验证通过")
        return 0

    print("\nOpenAI 接口双链路验证失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
