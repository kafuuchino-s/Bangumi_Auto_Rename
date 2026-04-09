import json
import platform
import threading
from contextlib import contextmanager
from urllib.parse import urlparse
from typing import Any, Dict, Union

from ..utils.path import CONFIG_PATH

CONFIG_DEFAULT = {
    "api_key": "",
    "bangumi_path": "",
    "movie_path": "",
    "anime_path": "",
    "anime_movie_path": "",
    "mode": "链接",
    "overwrite_existing": False,  # 是否覆盖已存在的文件
    "docker_mnt": "/media",
    "host_path_prefix": "",  # Windows宿主机路径前缀，用于qBittorrent路径转换
    "ai_provider": "openai",
    "ai_api_key": "",
    "ai_base_url": "https://api.openai.com/v1",
    "ai_model": "gpt-4o-mini",
    "ai_temperature": 0.1,  # OpenAI温度
    "gemini_api_key": "",
    "gemini_base_url": "https://generativelanguage.googleapis.com",
    "gemini_model": "gemini-2.5-flash",
    "gemini_temperature": 0.5,  # Gemini温度
    "ai_force_strict": True,
    "ai_confidence_threshold": "Medium",
    "openai_output_format": "function_calling",  # OpenAI输出格式选择（兼容旧配置）
    "openai_api_interface": "responses_api",  # OpenAI接口类型：responses_api/chat_completions
    "openai_auto_routing_enabled": True,  # OpenAI自动路由
    "openai_auto_format_order": [
        "function_calling",
        "json_object",
        "structured_output",
        "text",
    ],  # OpenAI自动路由顺序
    "openai_format_stats": {},  # OpenAI格式测试统计
    "gemini_output_format": "structured_output",  # Gemini输出格式选择（兼容旧配置）
    "gemini_auto_routing_enabled": True,  # Gemini自动路由
    "gemini_auto_format_order": [
        "structured_output",
        "json_object",
        "text",
    ],  # Gemini自动路由顺序
    "gemini_format_stats": {},  # Gemini格式测试统计
    "ai_auto_save": False,  # 是否自动保存AI分析结果
    "log_level": "INFO",  # 日志等级
    "queue_max_workers": 1,  # 队列并行处理数
    # 字幕同步（ffsubsync）配置
    "subtitle_sync_enabled": False,
    "subtitle_sync_mode": "best_effort",  # best_effort | strict
    "subtitle_sync_executable": "ffsubsync",
    "subtitle_sync_extra_args": "",
    "subtitle_sync_timeout_seconds": 120,
    "subtitle_sync_overwrite_policy": "follow_global",  # follow_global | overwrite | skip
    # 字幕自动抓取配置
    "subtitle_auto_fetch_enabled": False,
    "subtitle_auto_fetch_provider": "acgrip",
    "subtitle_auto_fetch_candidate_limit": 10,
    "subtitle_auto_fetch_timeout_seconds": 30,
    "subtitle_auto_fetch_browser_enabled": False,
    "subtitle_auto_fetch_acgrip_base_url": "https://bbs.acgrip.com",
    "subtitle_auto_fetch_preferred_language": "zh-CN",
    "subtitle_auto_fetch_use_ai_rerank": True,
    "subtitle_auto_fetch_search_mode": "auto",
    "subtitle_auto_fetch_save_reason": True,
    "skip_tags": "iyuu,辅种,reseed,skip,no_process",  # 跳过处理的标签
    # Emby通知配置
    "emby_enabled": False,  # 是否启用Emby通知
    "emby_host": "http://localhost:8096",  # Emby服务器地址
    "emby_api_key": "",  # Emby API密钥
    # Telegram通知配置
    "telegram_enabled": False,  # 是否启用Telegram通知
    "telegram_bot_token": "",  # Telegram Bot Token
    "telegram_chat_id": "",  # Telegram Chat ID
    "telegram_notify_on_success": True,  # 成功时通知
    "telegram_notify_on_failure": True,  # 失败时通知
    "telegram_base_url": "https://api.telegram.org",  # Telegram API地址
}

# 需要自动添加 docker_mnt 前缀的路径配置项
PATH_CONFIG_KEYS = {"bangumi_path", "movie_path", "anime_path", "anime_movie_path"}

CN_MAP = {
    "api_key": "🔑 TMDB API密钥",
    "bangumi_path": "🎬 电视剧路径",
    "movie_path": "🎬 电影路径",
    "anime_path": "🎬 动漫路径",
    "anime_movie_path": "🎬 动漫电影路径",
    "mode": "💿 重命名模式",
    "overwrite_existing": "🔄 覆盖已存在文件",
    "docker_mnt": "📁 Docker挂载路径",
    "host_path_prefix": "📁 宿主机路径前缀",
    "ai_provider": "🤖 AI提供商",
    "ai_api_key": "🤖 OpenAI API密钥",
    "ai_base_url": "🌐 OpenAI API地址",
    "ai_model": "🧠 OpenAI模型",
    "gemini_api_key": "💎 Gemini API密钥",
    "gemini_base_url": "🌐 Gemini API地址",
    "gemini_model": "💎 Gemini模型",
    "ai_force_strict": "🚨 AI严格模式（运维）",
    "ai_confidence_threshold": "📊 AI置信度阈值",
    "openai_output_format": "🎯 OpenAI输出格式",
    "openai_api_interface": "🌐 OpenAI接口类型",
    "openai_auto_routing_enabled": "🧭 OpenAI自动路由",
    "openai_auto_format_order": "📈 OpenAI自动路由顺序",
    "openai_format_stats": "🧪 OpenAI格式测试统计",
    "gemini_output_format": "💎 Gemini输出格式",
    "gemini_auto_routing_enabled": "🧭 Gemini自动路由",
    "gemini_auto_format_order": "📈 Gemini自动路由顺序",
    "gemini_format_stats": "🧪 Gemini格式测试统计",
    "ai_temperature": "🔥 OpenAI温度",
    "gemini_temperature": "🔥 Gemini温度",
    "ai_auto_save": "💾 自动保存AI分析",
    "log_level": "📝 日志等级",
    "queue_max_workers": "🔢 队列并行数（建议1-5）",
    "subtitle_sync_enabled": "🎯 启用字幕自动对齐(ffsubsync)",
    "subtitle_sync_mode": "⚙️ 字幕对齐模式",
    "subtitle_sync_executable": "🛠️ ffsubsync 可执行文件",
    "subtitle_sync_extra_args": "➕ ffsubsync 额外参数",
    "subtitle_sync_timeout_seconds": "⏱️ 对齐超时秒数",
    "subtitle_sync_overwrite_policy": "📝 字幕覆盖策略",
    "subtitle_auto_fetch_enabled": "🔎 启用字幕自动抓取",
    "subtitle_auto_fetch_provider": "🌐 字幕抓取源",
    "subtitle_auto_fetch_candidate_limit": "📚 抓取候选上限",
    "subtitle_auto_fetch_timeout_seconds": "⏱️ 抓取超时秒数",
    "subtitle_auto_fetch_browser_enabled": "🧭 启用动态浏览器抓取",
    "subtitle_auto_fetch_acgrip_base_url": "🌐 ACGRIP 地址",
    "subtitle_auto_fetch_preferred_language": "🈶 优先字幕语言",
    "subtitle_auto_fetch_use_ai_rerank": "🤖 启用AI重排",
    "subtitle_auto_fetch_search_mode": "🔍 字幕搜索模式",
    "subtitle_auto_fetch_save_reason": "📝 保存重排原因",
    "skip_tags": "🚫 跳过处理的标签（逗号分隔）",
    # Emby通知配置
    "emby_enabled": "📺 启用Emby通知",
    "emby_host": "🌐 Emby服务器地址",
    "emby_api_key": "🔑 Emby API密钥",
    # Telegram通知配置
    "telegram_enabled": "启用Telegram通知",
    "telegram_bot_token": "Telegram Bot Token",
    "telegram_chat_id": "Telegram Chat ID",
    "telegram_notify_on_success": "成功时发送Telegram通知",
    "telegram_notify_on_failure": "失败时发送Telegram通知",
    "telegram_base_url": "Telegram API地址",
}


class ConfigManager:
    def __init__(self) -> None:
        self._io_lock = threading.RLock()
        self._runtime_local = threading.local()

        if not CONFIG_PATH.exists():
            with open(CONFIG_PATH, 'w', encoding='UTF-8') as file:
                json.dump(CONFIG_DEFAULT, file, indent=4, ensure_ascii=False)

        self.update_config()

    def _get_runtime_overrides(self) -> Dict[str, Any]:
        overrides = getattr(self._runtime_local, 'config_overrides', None)
        if overrides is None:
            overrides = {}
            self._runtime_local.config_overrides = overrides
        return overrides

    @contextmanager
    def temporary_config(self, overrides: Dict[str, Any]):
        """线程内临时配置覆盖，不落盘，适用于并发测试场景。"""
        runtime_overrides = self._get_runtime_overrides()
        backup = dict(runtime_overrides)
        runtime_overrides.update(overrides)

        try:
            yield
        finally:
            runtime_overrides.clear()
            runtime_overrides.update(backup)

    def write_config(self):
        with self._io_lock:
            # 使用缓存文件避免强行关闭造成文件损坏
            temp_file_path = CONFIG_PATH.parent / f'{CONFIG_PATH.name}.bak'

            if temp_file_path.exists():
                temp_file_path.unlink()

            with open(temp_file_path, 'w', encoding='UTF-8') as file:
                json.dump(self.config, file, indent=4, ensure_ascii=False)

            CONFIG_PATH.unlink()
            temp_file_path.rename(CONFIG_PATH)

    def update_config(self):
        with self._io_lock:
            # 打开config.json
            with open(CONFIG_PATH, 'r', encoding='UTF-8') as f:
                self.config: Dict[str, Any] = json.load(f)
            # 对没有的值，添加默认值
            for key in CONFIG_DEFAULT:
                if key not in self.config:
                    self.config[key] = CONFIG_DEFAULT[key]

            # 清空不存在的key
            for key in list(self.config.keys()):
                if key not in CONFIG_DEFAULT:
                    del self.config[key]

            # 按照默认key排序
            self.config = {key: self.config[key] for key in CONFIG_DEFAULT}

            # 重新写回
            self.write_config()

    def get_config(self, key: str) -> Any:
        runtime_overrides = self._get_runtime_overrides()
        if key in runtime_overrides:
            value = runtime_overrides[key]
            if key in PATH_CONFIG_KEYS and value and isinstance(value, str):
                value = self._convert_path_for_current_platform(value)
            return value

        if key in self.config:
            value = self.config[key]
            # 对路径配置项，自动转换 Windows 路径到 Docker 路径
            if key in PATH_CONFIG_KEYS and value and isinstance(value, str):
                value = self._convert_path_for_current_platform(value)
            return value
        elif key in CONFIG_DEFAULT:
            self.update_config()
            value = self.config[key]
            if key in PATH_CONFIG_KEYS and value and isinstance(value, str):
                value = self._convert_path_for_current_platform(value)
            return value
        else:
            return ''

    def _convert_path_for_current_platform(self, path: str) -> str:
        """
        根据当前运行环境转换路径

        Windows 运行: 不转换
        Linux/Docker 运行: 将 Windows 路径转换为 Docker 路径
        例如: H:\\Emby\\Anime -> /media/Emby/Anime
        """
        if platform.system() != 'Linux':
            return path

        host_prefix = self.config.get('host_path_prefix', '')
        docker_mnt = self.config.get('docker_mnt', '/media').rstrip('/')

        # 如果已经是 Linux 路径，直接返回
        if path.startswith('/'):
            return path

        # 如果配置了宿主机路径前缀，进行转换
        if host_prefix:
            host_prefix = host_prefix.rstrip('\\').rstrip('/')
            if path.startswith(host_prefix):
                # 移除宿主机前缀，替换为 docker_mnt
                relative_path = path[len(host_prefix):]
                relative_path = relative_path.replace('\\', '/')
                return docker_mnt + relative_path

        # 如果包含反斜杠但没有匹配前缀，尝试智能转换
        if '\\' in path:
            # 去掉盘符（如 H:）
            if len(path) >= 2 and path[1] == ':':
                path = path[2:]
            path = path.replace('\\', '/')
            return docker_mnt + path

        # 相对路径，添加 docker_mnt 前缀
        return f"{docker_mnt}/{path}"

    def set_config(self, key: str, value: Union[str, bool]) -> bool:
        with self._io_lock:
            if key in CONFIG_DEFAULT:
                # 对URL类型的配置项进行特殊处理
                if key.endswith('_base_url') and value and isinstance(value, str):
                    value = self._normalize_url(value)

                # 设置值（路径直接保存，不做转换）
                self.config[key] = value
                # 重新写回
                self.write_config()
                return True
            else:
                return False

    def _normalize_url(self, url: str) -> str:
        """
        标准化URL格式，去除结尾斜杠并验证有效性

        Args:
            url: 原始URL

        Returns:
            标准化后的URL
        """
        if not url:
            return url

        # 去除结尾的斜杠
        url = url.rstrip('/')

        # 如果没有协议，默认添加https
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        # 验证URL格式
        try:
            parsed = urlparse(url)
            if not parsed.netloc:
                raise ValueError("Invalid URL format")
        except Exception:
            # 如果URL无效，返回原始值让用户自己处理
            pass

        return url

    def validate_url(self, url: str) -> bool:
        """
        验证URL是否有效

        Args:
            url: 要验证的URL

        Returns:
            是否为有效URL
        """
        if not url:
            return True  # 空URL视为有效（使用默认值）

        try:
            parsed = urlparse(url)
            return bool(parsed.netloc and parsed.scheme in ('http', 'https'))
        except Exception:
            return False


cm = ConfigManager()
