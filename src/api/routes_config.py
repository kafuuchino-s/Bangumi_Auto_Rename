"""配置 API 路由。

- GET/PUT 配置（密钥脱敏读取）
- 测试连接（AI/Emby/Telegram），临时应用配置后调 notifier/tester
- 暴露 field-spec（前端元数据驱动渲染）
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.config_manager import cm
from ..notification.emby_notify import EmbyNotifier
from ..notification.telegram_notify import TelegramNotifier
from ..pages.config_field_spec import FIELD_SPEC
from .serializers import _is_secret_key, get_all_config, mask_secrets

router = APIRouter(prefix="/config", tags=["config"])


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


# 运行时统计字段，保存时跳过（由测试流程维护）
_RUNTIME_MANAGED_KEYS = {
    "openai_auto_routing_enabled",
    "openai_auto_format_order",
    "openai_format_stats",
}


@router.get("")
def get_config() -> dict[str, Any]:
    """读取全部配置（密钥脱敏）。"""
    return {"config": mask_secrets(get_all_config())}


@router.put("")
def update_config(req: ConfigUpdateRequest) -> dict[str, Any]:
    """保存配置（校验 URL + 跳过运行时统计字段）。"""
    # URL 校验
    url_keys = ["ai_base_url", "telegram_base_url", "subtitle_auto_fetch_acgrip_base_url"]
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

    return {"code": 200, "data": "配置保存成功"}


@router.get("/field-spec")
def get_field_spec() -> dict[str, Any]:
    """暴露字段元数据（前端元数据驱动渲染）。"""
    return {"field_spec": list(FIELD_SPEC)}


@router.post("/test-ai")
async def test_ai() -> dict[str, Any]:
    """测试 AI 连通性（使用当前已保存配置）。

    走与生产 Case Agent 完全相同的 Pi 链路（/responses + provider/baseUrl/apiKey），
    起最小 agent session 验证「连通 + 会发起 tool_call」两件事。
    旧的 Python AIClient /chat/completions 门禁已移除（与生产脱节、且只测「能回一个字」
    无法发现「能回话但不会调工具」的 agentic 能力问题）。
    """
    from ..ai.pi_healthcheck import run_healthcheck

    def _do_test() -> tuple[bool, str]:
        return run_healthcheck()

    success, message = await asyncio.get_event_loop().run_in_executor(
        None, _do_test
    )
    return {"success": success, "message": message}


@router.post("/test-emby")
async def test_emby() -> dict[str, Any]:
    """测试 Emby 连接。"""
    notifier = EmbyNotifier()
    success, message = await asyncio.get_event_loop().run_in_executor(
        None, notifier.test_connection
    )
    return {"success": success, "message": message}


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
        return {"success": success, "message": message}
    finally:
        cm.set_config("telegram_enabled", original_enabled)
