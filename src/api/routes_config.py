"""配置 API 路由。

- GET/PUT 配置（密钥脱敏读取）
- 测试连接（AI/Emby/Telegram），临时应用配置后调 notifier/tester
- 暴露 field-spec（前端元数据驱动渲染）
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ai.pi_api_config import pi_api_from_config
from ..config.config_manager import cm
from ..notification.emby_notify import EmbyNotifier
from ..notification.telegram_notify import TelegramNotifier
from ..moviepilot import MoviePilotAPIError, MoviePilotClient
from ..pages.config_field_spec import get_field_spec_with_labels
from .serializers import _is_secret_key, get_all_config, mask_secrets
from .contract import canonical_field_spec, ok

router = APIRouter(prefix="/config", tags=["config"])


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


# 运行时统计字段，保存时跳过（由测试流程维护）
_RUNTIME_MANAGED_KEYS: set[str] = set()
_PI_MODEL_DISCOVERY_API_KEY_ENV = "BAR_PI_CASE_AGENT_API_KEY"
_PI_MODEL_DISCOVERY_SCRIPT = (
    Path(__file__).resolve().parents[2] / "tools" / "pi_model_discovery.mjs"
)


class ModelDiscoveryRequest(BaseModel):
    """Model discovery request; the API key is used only for this request."""

    base_url: str | None = None
    api_key: str | None = None
    api_interface: str | None = None


def _config_text(key: str) -> str:
    try:
        value = cm.get_config(key)
    except Exception:
        value = None
    return str(value or "").strip()


def _is_masked_secret(value: str | None) -> bool:
    return bool(value) and set(value or "") == {"*"}


def _effective_model_discovery_config(
    req: ModelDiscoveryRequest,
) -> tuple[str, str, str]:
    """Resolve request values; a masked key reuses the saved server-side key."""
    configured_base_url = (
        _config_text("rename_local_bangumi_pi_base_url")
        or _config_text("ai_base_url")
    )
    configured_api_key = (
        _config_text("rename_local_bangumi_pi_api_key")
        or _config_text("ai_api_key")
    )
    configured_interface = (
        _config_text("rename_local_bangumi_pi_api_interface")
        or _config_text("openai_api_interface")
        or "responses_api"
    )

    base_url = (
        str(req.base_url).strip()
        if req.base_url is not None
        else configured_base_url
    )
    if req.api_key is None or _is_masked_secret(req.api_key):
        api_key = configured_api_key
    else:
        api_key = str(req.api_key).strip()
    api_interface = (
        str(req.api_interface).strip()
        if req.api_interface is not None
        else configured_interface
    )
    return base_url, api_key, api_interface


def _run_model_discovery(
    base_url: str,
    api_key: str,
    api_interface: str,
    *,
    timeout_seconds: int = 20,
) -> list[str]:
    """Query models through the Pi-side Node tool, not a Python provider client."""
    if not _PI_MODEL_DISCOVERY_SCRIPT.exists():
        raise HTTPException(
            status_code=500,
            detail="模型发现脚本缺失",
        )

    api = pi_api_from_config(api_interface)
    argv = [
        "node",
        str(_PI_MODEL_DISCOVERY_SCRIPT),
        "--base-url",
        base_url,
        "--api",
        api,
        "--timeout",
        str(max(5, min(timeout_seconds - 5, 30))),
    ]
    env = os.environ.copy()
    env[_PI_MODEL_DISCOVERY_API_KEY_ENV] = api_key

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=env,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail="模型列表拉取超时",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail="模型列表拉取失败：未找到 node 可执行文件",
        ) from exc

    result: dict[str, Any] | None = None
    stdout = (completed.stdout or "").strip()
    if stdout:
        try:
            result = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            result = None

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=502,
            detail="模型列表拉取失败：sidecar 未返回有效结果",
        )
    if not result.get("ok"):
        error = str(result.get("error") or "模型服务未返回模型列表")
        code = str(result.get("code") or "provider_error")
        if code in {"incomplete_config", "unsupported_protocol"}:
            status_code = 400
        elif code == "unauthorized":
            status_code = 502
        elif "超时" in error:
            status_code = 504
        else:
            status_code = 502
        raise HTTPException(
            status_code=status_code,
            detail=f"模型列表拉取失败：{error}",
        )

    raw_models = result.get("models")
    if not isinstance(raw_models, list):
        raise HTTPException(
            status_code=502,
            detail="模型列表拉取失败：返回格式不受支持",
        )
    models = sorted(
        {str(model).strip() for model in raw_models if str(model).strip()},
        key=str.casefold,
    )
    return models


@router.get("")
def get_config() -> dict[str, Any]:
    """读取全部配置（密钥脱敏）。"""
    return ok({"config": mask_secrets(get_all_config())})


@router.put("")
def update_config(req: ConfigUpdateRequest) -> dict[str, Any]:
    """保存配置（校验 URL + 跳过运行时统计字段）。"""
    # URL 校验
    url_keys = [
        "ai_base_url",
        "telegram_base_url",
        "moviepilot_base_url",
        "subtitle_auto_fetch_acgrip_base_url",
    ]
    for k in url_keys:
        v = req.config.get(k)
        if v and not cm.validate_url(str(v)):
            raise HTTPException(status_code=400, detail=f"{k} 格式无效: {v}")

    for k, v in req.config.items():
        if k in _RUNTIME_MANAGED_KEYS:
            continue
        # 密钥字段防覆盖：GET /config 返回的是脱敏星号，若前端原样回传
        # （值全为星号 = 未改动），跳过 set_config，避免把真密钥覆盖成星号串。
        # 用户输入新明文才会覆盖；空字符串视为显式清空，照常写入。
        if (
            _is_secret_key(k)
            and isinstance(v, str)
            and v
            and set(v) == {"*"}
        ):
            continue
        cm.set_config(k, v)

    return ok({}, result="config_saved")


@router.get("/field-spec")
def get_field_spec() -> dict[str, Any]:
    """暴露字段元数据（含中文 label，前端元数据驱动渲染）。"""
    return ok({"field_spec": canonical_field_spec(get_field_spec_with_labels())})


@router.post("/discover-models")
def discover_models(req: ModelDiscoveryRequest) -> dict[str, Any]:
    """Fetch available model IDs from the current OpenAI-compatible gateway.

    The request may contain unsaved settings from the UI. A masked or omitted
    API key reuses the saved server-side key and is never returned.
    """
    base_url, api_key, api_interface = _effective_model_discovery_config(req)
    if not base_url or not cm.validate_url(base_url):
        raise HTTPException(status_code=400, detail="模型接口地址无效")
    if not api_key:
        raise HTTPException(status_code=400, detail="模型列表拉取失败：请填写 API 密钥")

    models = _run_model_discovery(base_url, api_key, api_interface)
    return ok({"models": models}, result="models_discovered")


@router.post("/test-ai")
async def test_ai() -> dict[str, Any]:
    """测试 AI 连通性（使用当前已保存配置）。

    走与生产 Case Agent 完全相同的 Pi 链路（/responses + provider/baseUrl/apiKey），
    起最小 agent session 验证「连通 + 会发起 tool_call」两件事。
    Pi healthcheck 使用与生产 sidecar 相同的协议和 tool-call 路径。
    无法发现「能回话但不会调工具」的 agentic 能力问题）。
    """
    from ..ai.pi_healthcheck import run_healthcheck

    def _do_test() -> tuple[bool, str]:
        return run_healthcheck()

    success, message = await asyncio.get_event_loop().run_in_executor(
        None, _do_test
    )
    return ok(
        {"success": success, "message": message},
        result="test_passed" if success else "test_failed",
    )


@router.post("/test-moviepilot")
async def test_moviepilot() -> dict[str, Any]:
    """测试共享 MoviePilot 连接（使用当前已保存配置）。"""

    def _do_test() -> tuple[bool, str]:
        try:
            downloaders = MoviePilotClient.configured().list_downloaders()
        except MoviePilotAPIError as exc:
            return False, str(exc)
        return True, f"连接成功，已启用下载器 {len(downloaders)} 个"

    success, message = await asyncio.to_thread(_do_test)
    return ok(
        {"success": success, "message": message},
        result="test_passed" if success else "test_failed",
    )


@router.post("/test-emby")
async def test_emby() -> dict[str, Any]:
    """测试 Emby 连接。"""
    notifier = EmbyNotifier()
    success, message = await asyncio.get_event_loop().run_in_executor(
        None, notifier.test_connection
    )
    return ok(
        {"success": success, "message": message},
        result="test_passed" if success else "test_failed",
    )


@router.post("/test-telegram")
async def test_telegram() -> dict[str, Any]:
    """测试 Telegram 连接（临时启用）。"""
    original_enabled = cm.get_config("telegram_enabled")
    try:
        cm.set_config("telegram_enabled", True)
        notifier = TelegramNotifier()
        success, message = await asyncio.get_event_loop().run_in_executor(
            None, notifier.test_connection
        )
        return ok(
            {"success": success, "message": message},
            result="test_passed" if success else "test_failed",
        )
    finally:
        cm.set_config("telegram_enabled", original_enabled)
