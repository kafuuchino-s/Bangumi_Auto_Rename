"""
Emby 媒体库刷新通知服务

在任务完成后通知 Emby 服务器刷新媒体库。
"""

import requests
from typing import Optional, Tuple

from ..config.config_manager import cm
from ..logger import logger


class EmbyNotifier:
    """Emby 通知服务"""

    def __init__(self):
        self.enabled = bool(cm.get_config("emby_enabled"))
        self.host = cm.get_config("emby_host") or "http://localhost:8096"
        self.api_key = cm.get_config("emby_api_key")

    def is_available(self) -> bool:
        """检查 Emby 通知服务是否可用"""
        return self.enabled and bool(self.api_key)

    def _normalize_host(self, host: str) -> str:
        """标准化主机地址"""
        host = host.rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        return host

    def refresh_library(self) -> Tuple[bool, str]:
        """
        通知 Emby 刷新整个媒体库

        Returns:
            (成功标志, 消息)
        """
        if not self.is_available():
            return False, "Emby 通知未启用或未配置 API 密钥"

        try:
            host = self._normalize_host(self.host)
            url = f"{host}/Library/Refresh"

            # Emby 支持两种认证方式：
            # 1. 查询参数: api_key=xxx
            # 2. 请求头: X-Emby-Token
            # 使用查询参数方式更简单直接
            params = {"api_key": self.api_key}

            logger.info(f"[Emby] 正在通知 Emby 服务器刷新媒体库: {host}")

            response = requests.post(url, params=params, timeout=30)

            if response.status_code in (200, 204):
                logger.info("[Emby] 媒体库刷新通知发送成功")
                return True, "媒体库刷新通知已发送"
            else:
                error_msg = f"Emby 返回错误状态码: {response.status_code}"
                logger.warning(f"[Emby] {error_msg}")
                return False, error_msg

        except requests.exceptions.Timeout:
            error_msg = "连接 Emby 服务器超时"
            logger.error(f"[Emby] {error_msg}")
            return False, error_msg
        except requests.exceptions.ConnectionError as e:
            error_msg = f"无法连接 Emby 服务器: {str(e)}"
            logger.error(f"[Emby] {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"通知 Emby 失败: {str(e)}"
            logger.error(f"[Emby] {error_msg}")
            return False, error_msg

    def test_connection(self) -> Tuple[bool, str]:
        """
        测试 Emby 服务器连接

        Returns:
            (成功标志, 消息)
        """
        if not self.api_key:
            return False, "请先配置 Emby API 密钥"

        try:
            host = self._normalize_host(self.host)
            # 使用 /System/Info 端点测试连接
            url = f"{host}/System/Info"
            params = {"api_key": self.api_key}

            logger.info(f"[Emby] 测试连接: {host}")

            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                server_name = data.get("ServerName", "Unknown")
                version = data.get("Version", "Unknown")
                logger.info(f"[Emby] 连接成功: {server_name} (版本 {version})")
                return True, f"连接成功: {server_name} (版本 {version})"
            elif response.status_code == 401:
                return False, "API 密钥无效"
            else:
                return False, f"服务器返回错误: {response.status_code}"

        except requests.exceptions.Timeout:
            return False, "连接超时"
        except requests.exceptions.ConnectionError:
            return False, "无法连接服务器"
        except Exception as e:
            return False, f"测试失败: {str(e)}"


# 单例模式，便于全局访问
_emby_notifier: Optional[EmbyNotifier] = None


def get_emby_notifier() -> EmbyNotifier:
    """获取 Emby 通知服务单例"""
    global _emby_notifier
    if _emby_notifier is None:
        _emby_notifier = EmbyNotifier()
    return _emby_notifier


def refresh_emby_notifier() -> None:
    """刷新 Emby 通知服务配置（配置更新后调用）"""
    global _emby_notifier
    _emby_notifier = EmbyNotifier()
