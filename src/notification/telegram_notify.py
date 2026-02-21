"""
Telegram 主动通知服务。

在任务批次完成后发送汇总消息到 Telegram 聊天。
"""

from typing import Optional, Tuple

import requests

from ..config.config_manager import cm
from ..logger import logger


class TelegramNotifier:
    """Telegram 通知服务"""

    def __init__(self):
        self.enabled = bool(cm.get_config("telegram_enabled"))
        self.bot_token = cm.get_config("telegram_bot_token")
        self.chat_id = cm.get_config("telegram_chat_id")
        self.base_url = (
            cm.get_config("telegram_base_url")
            or "https://api.telegram.org"
        )

    def is_available(self) -> bool:
        """检查 Telegram 通知服务是否可用"""
        return self.enabled and bool(self.bot_token) and bool(self.chat_id)

    def _normalize_base_url(self, base_url: str) -> str:
        """标准化 Telegram API 地址"""
        base_url = (base_url or "https://api.telegram.org").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url
        return base_url

    def _build_api_url(self, method: str) -> str:
        """拼接 Telegram Bot API URL"""
        base_url = self._normalize_base_url(self.base_url)
        return f"{base_url}/bot{self.bot_token}/{method}"

    def send_message(self, text: str) -> Tuple[bool, str]:
        """
        发送文本消息到 Telegram 聊天。

        Returns:
            (成功标志, 消息)
        """
        if not self.is_available():
            return (
                False,
                "Telegram 通知未启用或未完整配置 Token/Chat ID",
            )

        try:
            url = self._build_api_url("sendMessage")
            payload = {
                "chat_id": self.chat_id,
                "text": text,
            }

            logger.info("[Telegram] 正在发送批次汇总通知")
            response = requests.post(url, json=payload, timeout=30)

            if response.status_code != 200:
                error_msg = f"Telegram 返回错误状态码: {response.status_code}"
                logger.warning(f"[Telegram] {error_msg}")
                return False, error_msg

            data = response.json()
            if not data.get("ok"):
                error_msg = data.get("description", "Telegram 返回失败")
                logger.warning(f"[Telegram] 发送失败: {error_msg}")
                return False, error_msg

            logger.info("[Telegram] 消息发送成功")
            return True, "消息发送成功"

        except requests.exceptions.Timeout:
            error_msg = "连接 Telegram 服务器超时"
            logger.error(f"[Telegram] {error_msg}")
            return False, error_msg
        except requests.exceptions.ConnectionError as e:
            error_msg = f"无法连接 Telegram 服务器: {str(e)}"
            logger.error(f"[Telegram] {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"发送 Telegram 通知失败: {str(e)}"
            logger.error(f"[Telegram] {error_msg}")
            return False, error_msg

    def send_photo(self, photo_url: str, caption: str) -> Tuple[bool, str]:
        """
        发送图片消息（附带 caption）到 Telegram 聊天。

        Returns:
            (成功标志, 消息)
        """
        if not self.is_available():
            return (
                False,
                "Telegram 通知未启用或未完整配置 Token/Chat ID",
            )

        if not photo_url:
            return False, "图片地址为空"

        try:
            url = self._build_api_url("sendPhoto")
            payload = {
                "chat_id": self.chat_id,
                "photo": photo_url,
                "caption": caption,
            }

            logger.info("[Telegram] 正在发送图片通知")
            response = requests.post(url, json=payload, timeout=30)

            if response.status_code != 200:
                error_msg = f"Telegram 返回错误状态码: {response.status_code}"
                logger.warning(f"[Telegram] {error_msg}")
                return False, error_msg

            data = response.json()
            if not data.get("ok"):
                error_msg = data.get("description", "Telegram 返回失败")
                logger.warning(f"[Telegram] 发送图片失败: {error_msg}")
                return False, error_msg

            logger.info("[Telegram] 图片消息发送成功")
            return True, "图片消息发送成功"

        except requests.exceptions.Timeout:
            error_msg = "连接 Telegram 服务器超时"
            logger.error(f"[Telegram] {error_msg}")
            return False, error_msg
        except requests.exceptions.ConnectionError as e:
            error_msg = f"无法连接 Telegram 服务器: {str(e)}"
            logger.error(f"[Telegram] {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"发送 Telegram 图片通知失败: {str(e)}"
            logger.error(f"[Telegram] {error_msg}")
            return False, error_msg

    def test_connection(self) -> Tuple[bool, str]:
        """
        测试 Telegram 连接（发送测试消息）。

        Returns:
            (成功标志, 消息)
        """
        if not self.bot_token:
            return False, "请先配置 Telegram Bot Token"
        if not self.chat_id:
            return False, "请先配置 Telegram Chat ID"

        try:
            url = self._build_api_url("sendMessage")
            payload = {
                "chat_id": self.chat_id,
                "text": "Bangumi Auto Rename Telegram 连接测试成功",
            }

            logger.info("[Telegram] 正在测试 Telegram 连接")
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code != 200:
                return False, f"服务器返回错误: {response.status_code}"

            data = response.json()
            if not data.get("ok"):
                return False, data.get("description", "连接失败")

            return True, "连接成功，测试消息已发送"

        except requests.exceptions.Timeout:
            return False, "连接超时"
        except requests.exceptions.ConnectionError:
            return False, "无法连接服务器"
        except Exception as e:
            return False, f"测试失败: {str(e)}"


_telegram_notifier: Optional[TelegramNotifier] = None


def get_telegram_notifier() -> TelegramNotifier:
    """获取 Telegram 通知服务单例"""
    global _telegram_notifier
    if _telegram_notifier is None:
        _telegram_notifier = TelegramNotifier()
    return _telegram_notifier


def refresh_telegram_notifier() -> None:
    """刷新 Telegram 通知服务配置（配置更新后调用）"""
    global _telegram_notifier
    _telegram_notifier = TelegramNotifier()
