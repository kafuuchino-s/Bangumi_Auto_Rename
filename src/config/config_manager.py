import json
import os
import tempfile
import time
import platform
import threading
import shutil
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict

from ..utils.path import CONFIG_PATH

CONFIG_DEFAULT = {
    "config_schema_version": 2,
    "api_key": "",
    "tv_path": "",
    "movie_path": "",
    "anime_path": "",
    "anime_movie_path": "",
    "mode": "link",
    "overwrite_existing": "skip",  # canonical IDs; old Chinese values/bools are accepted at the read boundary
    "hardlink_fallback_to_symlink": True,  # 链接模式下硬链接失败（如跨文件系统）时是否降级为软链接。False 时硬链失败记 partial_failure，不静默降级（软链接源删则失效，语义与硬链接不同）
    "docker_mnt": "/media",
    "host_path_prefix": "",  # Windows宿主机路径前缀，用于qBittorrent路径转换
    "ai_api_key": "",
    "ai_base_url": "https://api.openai.com",
    "ai_model": "gpt-4o-mini",
    "openai_api_interface": "responses_api",  # Pi 协议：anthropic_messages/responses_api/chat_completions
    "rename_local_bangumi_case_agent_primary_enabled": True,
    "rename_local_bangumi_case_agent_backend": "pi",
    "rename_local_bangumi_case_agent_max_evidence_batches": 12,
    "rename_local_bangumi_case_agent_max_issue_response_rounds": 1,
    "rename_local_bangumi_case_agent_max_requests_per_batch": 8,
    "rename_local_bangumi_pi_case_root": "data/pi_case_agent",
    "rename_local_bangumi_pi_max_turns": 48,
    "rename_local_bangumi_pi_timeout_seconds": 300,
    "rename_local_bangumi_pi_command": "",
    "rename_local_bangumi_case_agent_snapshot_debug": False,
    "rename_bgm_to_tmdb_product_pipeline_enabled": True,
    "rename_bgm_to_tmdb_execute_enabled": True,
    "rename_bgm_to_tmdb_retry_on_fail_closed": True,  # 段2 BGM→TMDB 桥接 Pi 有非确定性，fail_closed 可能是假阴性（本可桥接却判不能）。True 时对 fail_closed 单次重试纠回假阴性；重试仍 fail_closed 则接受为终态失败。不对 invalid/need_confirm/error 重试
    "rename_bgm_external_hints_mode": "off",  # off | shadow | assist；外部映射只提供 BGM→TMDB 候选证据
    "rename_bgm_extlinker_snapshot_path": "",  # BangumiExtLinker anime_map.json 本地快照
    "rename_bgm_fribb_snapshot_path": "",  # Fribb anime-list-full.json 本地快照
    "rename_bgm_to_tmdb_pi_command": "",
    "log_level": "INFO",  # 日志等级
    "queue_max_workers": 1,  # 队列并行处理数
    # 字幕同步（ffsubsync）配置
    "subtitle_sync_enabled": False,
    "subtitle_sync_mode": "best_effort",  # best_effort | strict
    "subtitle_sync_executable": "ffsubsync",
    "subtitle_sync_extra_args": "",
    "subtitle_sync_timeout_seconds": 120,
    "subtitle_sync_overwrite_policy": "follow_global",  # follow_global | overwrite | skip
    # 字幕导入 Case Agent（对齐 rename Local→Bangumi Case Agent）
    "subtitle_case_agent_primary_enabled": True,  # Case Agent contract path remains enabled
    "subtitle_case_agent_backend": "pi",  # Pi is the only supported subtitle backend
    "subtitle_case_agent_pi_case_root": "data/subtitle_case_agent",
    "subtitle_case_agent_pi_max_turns": 48,  # 兼容保留，Pi native 模式由 wall-clock timeout 约束
    "subtitle_case_agent_pi_timeout_seconds": 300,
    "subtitle_case_agent_pi_command": "",  # 运行命令覆盖（默认走 core sidecar）
    # 字幕自动抓取配置
    "subtitle_auto_fetch_enabled": False,
    "subtitle_auto_fetch_provider": "acgrip",
    "subtitle_auto_fetch_candidate_limit": 10,
    "subtitle_auto_fetch_timeout_seconds": 30,
    "subtitle_auto_fetch_browser_enabled": False,
    "subtitle_auto_fetch_acgrip_base_url": "https://bbs.acgrip.com",
    "subtitle_auto_fetch_preferred_language": "zh-CN",
    "subtitle_auto_fetch_skip_if_embedded_language": True,
    "subtitle_auto_fetch_use_ai_rerank": True,
    "subtitle_auto_fetch_search_mode": "auto",
    "subtitle_auto_fetch_save_reason": True,
    # 多 selection（多 subject）下载+配对并发数。_execute_fetch 逐 selection 串行是
    # 多 subject 大样本（0042 5 subject 618s）速度瓶颈；并发各 selection 的下载+processor
    # 配对可显著提速。保守默认 3：每个 selection 跑独立字幕 Case Agent Pi sidecar（Node
    # 子进程），过高会放大内存/CPU + 触发 acgrip 限流。1 = 串行（旧行为）。
    "subtitle_auto_fetch_selection_concurrency": 3,
    # 字幕自动抓取 Case Agent（对齐 rename / 字幕导入 Case Agent）
    "subtitle_auto_fetch_case_agent_backend": "pi",  # Pi is the only supported auto-fetch backend
    "subtitle_auto_fetch_case_agent_pi_case_root": "data/auto_fetch_case_agent",
    "subtitle_auto_fetch_case_agent_pi_max_turns": 48,  # 兼容保留，Pi native 模式由 wall-clock timeout 约束
    "subtitle_auto_fetch_case_agent_pi_timeout_seconds": 600,
    "subtitle_auto_fetch_case_agent_pi_command": "",  # 运行命令覆盖（默认走 core sidecar）
    "allowed_categories": "",  # 仅处理的 qBittorrent 分类；空值表示不限制
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
    # 元数据缓存（cache/metadata，diskcache/SQLite 后端）
    "metadata_cache_mode": "read-write",  # 缓存模式：read-write/cache-only/refresh/off
    "metadata_cache_ttl_days": 30,  # 正向结果缓存天数
    "metadata_cache_negative_ttl_hours": 6,  # 空结果（[]/{})缓存小时数
    "metadata_cache_max_size_mb": 500,  # 缓存磁盘上限（MB），超限 gc 时按 LRU 淘汰
}

# 需要自动添加 docker_mnt 前缀的路径配置项
PATH_CONFIG_KEYS = {"tv_path", "movie_path", "anime_path", "anime_movie_path"}

# Compatibility labels for older extension imports. New field specs use locale resources.
CN_MAP = {
    "allowed_categories": "仅处理分类（白名单，逗号分隔）",
    "skip_tags": "跳过处理的标签（逗号分隔）",
    "ai_api_key": "OpenAI API 密钥",
    "ai_base_url": "OpenAI API 地址",
    "ai_model": "OpenAI 模型",
    "openai_api_interface": "Pi 模型协议",
}

class ConfigManager:
    def __init__(self) -> None:
        self._io_lock = threading.RLock()
        self._runtime_local = threading.local()
        self.config: dict[str, Any] = {}
        readonly_env = os.environ.get('BANGUMI_CONFIG_READONLY', '')
        self._readonly_mode = readonly_env.lower() in {'1', 'true', 'yes', 'on'}

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
        if self._readonly_mode:
            return
        with self._io_lock:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f'.{CONFIG_PATH.name}.',
                suffix='.tmp',
                dir=str(CONFIG_PATH.parent),
            )
            temp_file_path = Path(temp_name)
            try:
                with os.fdopen(fd, 'w', encoding='UTF-8') as file:
                    json.dump(self.config, file, indent=4, ensure_ascii=False)
                    file.write('\n')
                    file.flush()
                    os.fsync(file.fileno())
                last_error: PermissionError | None = None
                for attempt in range(5):
                    try:
                        os.replace(temp_file_path, CONFIG_PATH)
                        last_error = None
                        break
                    except PermissionError as exc:
                        last_error = exc
                        if attempt >= 4:
                            raise
                        time.sleep(0.05 * (attempt + 1))
                if last_error is not None:
                    raise last_error
            finally:
                if temp_file_path.exists():
                    temp_file_path.unlink()

    def update_config(self):
        with self._io_lock:
            # 打开config.json
            last_error: Exception | None = None
            for attempt in range(5):
                try:
                    with open(CONFIG_PATH, 'r', encoding='UTF-8') as f:
                        loaded = json.load(f)
                        self.config = loaded if isinstance(loaded, dict) else {}
                    last_error = None
                    break
                except PermissionError as exc:
                    last_error = exc
                    if attempt >= 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            if last_error is not None:
                raise last_error
            original_keys = set(self.config)
            self._migrate_i18n_v2(original_keys)

            # 对没有的值，添加默认值
            for key in CONFIG_DEFAULT:
                if key not in self.config:
                    self.config[key] = CONFIG_DEFAULT[key]

            # Pi is the only runtime AI backend; normalize retired protocol/backend values.
            if self.config.get("openai_api_interface") == "anthropic_messages":
                self.config["openai_api_interface"] = "responses_api"
            if self.config.get("subtitle_case_agent_backend") != "pi":
                self.config["subtitle_case_agent_backend"] = "pi"



            # 兼容迁移：上一版 webhook 标签白名单 → qBittorrent 分类白名单。
            # 必须检查原始 key 是否存在，避免用户显式保存空新值后被旧值复活。
            if (
                "allowed_tags" in original_keys
                and "allowed_categories" not in original_keys
            ):
                self.config["allowed_categories"] = self.config["allowed_tags"]

            # 兼容迁移：历史遗留名 bangumi_path → tv_path（语义未变，仅改名）。
            # 必须在下方「删除未知 key」之前做，否则旧值会被静默清掉导致路径丢失。
            # 幂等：迁移后旧 bangumi_path 被下方循环删除，二次启动 if 不触发。
            if 'bangumi_path' in self.config and not self.config.get('tv_path'):
                self.config['tv_path'] = self.config['bangumi_path']

            # 清空不存在的key
            for key in list(self.config.keys()):
                if key not in CONFIG_DEFAULT:
                    del self.config[key]

            # 按照默认key排序
            self.config = {key: self.config[key] for key in CONFIG_DEFAULT}

            # 重新写回
            if not self._readonly_mode:
                self.write_config()

    @staticmethod
    def _canonical_mode(value: object) -> str:
        aliases = {
            "link": "link", "链接": "link", "hardlink": "link",
            "copy": "copy", "复制": "copy",
            "move": "move", "剪切": "move", "移动": "move",
        }
        return aliases.get(str(value).strip().lower(), "link")

    @staticmethod
    def _canonical_overwrite(value: object) -> str:
        if isinstance(value, bool):
            return "overwrite" if value else "skip"
        aliases = {
            "overwrite": "overwrite", "覆盖": "overwrite",
            "skip": "skip", "跳过": "skip", "拒绝": "skip",
        }
        return aliases.get(str(value).strip().lower(), "skip")

    def _migrate_i18n_v2(self, original_keys: set[str]) -> None:
        """Normalize historic enum values once, with an atomic rollback copy."""
        raw_version = self.config.get("config_schema_version", 1)
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            version = 1
        needs_migration = version < 2 or self.config.get("mode") not in {"link", "copy", "move"}
        needs_migration = needs_migration or self.config.get("overwrite_existing") not in {"overwrite", "skip"}
        if not needs_migration:
            return
        backup = CONFIG_PATH.with_name("config.pre-i18n-v2.json")
        if CONFIG_PATH.exists() and not backup.exists() and not self._readonly_mode:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{backup.name}.", suffix=".tmp", dir=str(CONFIG_PATH.parent)
            )
            try:
                os.close(fd)
                shutil.copy2(CONFIG_PATH, temp_name)
                os.replace(temp_name, backup)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        self.config["mode"] = self._canonical_mode(self.config.get("mode"))
        self.config["overwrite_existing"] = self._canonical_overwrite(
            self.config.get("overwrite_existing")
        )
        self.config["config_schema_version"] = 2

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

        host_prefix_value = self.config.get('host_path_prefix', '')
        host_prefix = host_prefix_value if isinstance(host_prefix_value, str) else ''
        docker_mnt_value = self.config.get('docker_mnt', '/media')
        docker_mnt = docker_mnt_value if isinstance(docker_mnt_value, str) else '/media'
        docker_mnt = docker_mnt.rstrip('/')

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

    def set_config(self, key: str, value: Any) -> bool:
        with self._io_lock:
            if key in CONFIG_DEFAULT:
                if key == "mode":
                    value = self._canonical_mode(value)
                elif key == "overwrite_existing":
                    value = self._canonical_overwrite(value)
                elif key == "config_schema_version":
                    value = 2
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
