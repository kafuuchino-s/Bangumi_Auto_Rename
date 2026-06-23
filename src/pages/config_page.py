from collections.abc import Mapping, Sequence
from types import SimpleNamespace

from nicegui import ui

from ..logger import logger, update_log_level_from_config
from ..config.config_manager import CN_MAP, cm
from ..element.red import RedButton, RedToogle
from ..component.local_file_picker import local_file_picker


class ConfigPage(ui.dialog):

    config: SimpleNamespace
    _current_queue_max_workers: int
    _current_subtitle_auto_fetch_candidate_limit: int
    _current_subtitle_auto_fetch_timeout_seconds: int
    _current_subtitle_sync_timeout_seconds: int

    def __init__(self) -> None:
        super().__init__()
        # 使用 get_config 获取配置值，确保路径转换生效
        config_dict = {key: cm.get_config(key) for key in cm.config}
        self.config = SimpleNamespace(**config_dict)
        self._current_queue_max_workers = self._get_int_config("queue_max_workers", 1)
        self._current_subtitle_auto_fetch_candidate_limit = self._get_int_config(
            "subtitle_auto_fetch_candidate_limit", 10
        )
        self._current_subtitle_auto_fetch_timeout_seconds = self._get_int_config(
            "subtitle_auto_fetch_timeout_seconds", 30
        )
        self._current_subtitle_sync_timeout_seconds = self._get_int_config(
            "subtitle_sync_timeout_seconds", 120
        )

        _s = "width: 60%; flex-wrap: nowrap; max-height: 80vh; overflow-y: auto;"
        with self, ui.card().style(_s).classes("flex"):
            ui.label("配置").style("font-size: 20px; font-weight: bold")
            ui.separator()

            # 基础配置
            ui.label("基础配置").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            basic_configs = [
                "api_key",
                "bangumi_path",
                "movie_path",
                "anime_path",
                "anime_movie_path",
                "mode",
                "overwrite_existing",
                "hardlink_fallback_to_symlink",
                "rename_bgm_to_tmdb_product_pipeline_enabled",
                "rename_bgm_to_tmdb_execute_enabled",
                "docker_mnt",
                "host_path_prefix",
                "log_level",
                "queue_max_workers",
                "skip_tags",
            ]
            for cn in basic_configs:
                self._create_config_row(cn)

            ui.separator().style("margin: 20px 0;")

            # AI配置
            ui.label("AI识别配置").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            ai_configs = [
                "ai_auto_save",
                "ai_confidence_threshold",
                "ai_api_key",
                "ai_base_url",
                "ai_model",
                "openai_output_format",
                "openai_api_interface",
                "ai_temperature",
            ]
            for cn in ai_configs:
                self._create_config_row(cn)

            # AI功能测试按钮
            with ui.row(wrap=False).classes("w-full justify-center mt-4 gap-2"):
                RedButton(
                    "🧪 测试AI识别功能", on_click=self._test_ai_recognition
                ).props("outline")
                RedButton("⚙️ 测试OpenAI API功能", on_click=self._test_openai_api).props(
                    "outline"
                )

            ui.separator().style("margin: 20px 0;")

            # 字幕同步配置
            ui.label("字幕同步（ffsubsync）").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            subtitle_sync_configs = [
                "subtitle_sync_enabled",
                "subtitle_sync_mode",
                "subtitle_sync_executable",
                "subtitle_sync_extra_args",
                "subtitle_sync_timeout_seconds",
                "subtitle_sync_overwrite_policy",
                "subtitle_case_agent_primary_enabled",
            ]
            for cn in subtitle_sync_configs:
                self._create_config_row(cn)

            ui.separator().style("margin: 20px 0;")

            # 字幕自动抓取配置
            ui.label("字幕自动抓取").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            subtitle_auto_fetch_configs = [
                "subtitle_auto_fetch_enabled",
                "subtitle_auto_fetch_provider",
                "subtitle_auto_fetch_candidate_limit",
                "subtitle_auto_fetch_timeout_seconds",
                "subtitle_auto_fetch_browser_enabled",
                "subtitle_auto_fetch_acgrip_base_url",
                "subtitle_auto_fetch_preferred_language",
                "subtitle_auto_fetch_use_ai_rerank",
                "subtitle_auto_fetch_search_mode",
                "subtitle_auto_fetch_save_reason",
            ]
            for cn in subtitle_auto_fetch_configs:
                self._create_config_row(cn)

            ui.separator().style("margin: 20px 0;")

            # Emby通知配置
            ui.label("Emby通知配置").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            emby_configs = [
                "emby_enabled",
                "emby_host",
                "emby_api_key",
            ]
            for cn in emby_configs:
                self._create_config_row(cn)

            # Emby测试按钮
            with ui.row(wrap=False).classes("w-full justify-center mt-4 gap-2"):
                RedButton(
                    "🧪 测试Emby连接", on_click=self._test_emby_connection
                ).props("outline")

            ui.separator().style("margin: 20px 0;")

            # Telegram通知配置
            ui.label("Telegram通知配置").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            telegram_configs = [
                "telegram_enabled",
                "telegram_bot_token",
                "telegram_chat_id",
                "telegram_notify_on_success",
                "telegram_notify_on_failure",
                "telegram_base_url",
            ]
            for cn in telegram_configs:
                self._create_config_row(cn)

            # Telegram测试按钮
            with ui.row(wrap=False).classes("w-full justify-center mt-4 gap-2"):
                RedButton(
                    "测试Telegram连接",
                    on_click=self._test_telegram_connection,
                ).props("outline")

            ui.separator()

            with ui.row(wrap=False).classes("w-full justify-end"):
                RedButton("取消", on_click=self.close).props("outline")
                RedButton("确认修改", on_click=self._handle_ok)

    def _create_config_row(self, cn: str):
        with ui.column(wrap=False).classes("flex no-wrap w-full"):
            with ui.row(wrap=False).classes("flex justify-space-between w-full"):
                with ui.row(wrap=False, align_items="baseline") as row:
                    row.classes("flex w-full")
                    # 配置标签
                    label = CN_MAP.get(cn, cn)
                    ui.label(label).style("min-width: 150px")

                    if cn == "mode":
                        tg = RedToogle(
                            ["链接", "复制", "剪切"],
                            value=cm.get_config(cn),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "ai_confidence_threshold":
                        tg = RedToogle(
                            ["High", "Medium", "Low"],
                            value=cm.get_config(cn),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "log_level":
                        tg = RedToogle(
                            ["DEBUG", "INFO", "WARNING", "ERROR"],
                            value=cm.get_config(cn),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "queue_max_workers":
                        ui.number(
                            value=self._current_queue_max_workers,
                            min=1,
                            step=1,
                            on_change=lambda e, c=cn: self._change(
                                c, int(e.value) if e.value else 1
                            ),
                        ).props('filled dense').style('flex-grow: 2').bind_value(
                            self.config, cn
                        )
                    elif cn == "ai_auto_save":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "emby_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "telegram_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "telegram_notify_on_success":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "telegram_notify_on_failure":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_sync_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_case_agent_primary_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_auto_fetch_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_auto_fetch_provider":
                        ui.select(
                            options=["acgrip"],
                            value=(
                                cm.get_config(cn)
                                if cm.get_config(cn) in {"acgrip"}
                                else "acgrip"
                            ),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        ).props("filled dense").style("flex-grow: 2").bind_value(
                            self.config, cn
                        )
                    elif cn == "subtitle_auto_fetch_browser_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_auto_fetch_use_ai_rerank":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_auto_fetch_save_reason":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_auto_fetch_preferred_language":
                        tg = RedToogle(
                            ["zh-CN", "zh-TW"],
                            value=cm.get_config(cn) or "zh-CN",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_auto_fetch_search_mode":
                        tg = RedToogle(
                            ["auto"],
                            value=cm.get_config(cn) or "auto",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_auto_fetch_candidate_limit":
                        ui.number(
                            value=self._current_subtitle_auto_fetch_candidate_limit,
                            min=1,
                            max=50,
                            step=1,
                            on_change=lambda e, c=cn: self._change(
                                c, int(e.value) if e.value else 10
                            ),
                        ).props('filled dense').style('flex-grow: 2').bind_value(
                            self.config, cn
                        )
                    elif cn == "subtitle_auto_fetch_timeout_seconds":
                        ui.number(
                            value=self._current_subtitle_auto_fetch_timeout_seconds,
                            min=5,
                            step=1,
                            on_change=lambda e, c=cn: self._change(
                                c, int(e.value) if e.value else 30
                            ),
                        ).props('filled dense').style('flex-grow: 2').bind_value(
                            self.config, cn
                        )
                    elif cn == "subtitle_sync_mode":
                        tg = RedToogle(
                            ["best_effort", "strict"],
                            value=cm.get_config(cn) or "best_effort",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_sync_overwrite_policy":
                        tg = RedToogle(
                            ["follow_global", "overwrite", "skip"],
                            value=cm.get_config(cn) or "follow_global",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "subtitle_sync_timeout_seconds":
                        ui.number(
                            value=self._current_subtitle_sync_timeout_seconds,
                            min=10,
                            step=1,
                            on_change=lambda e, c=cn: self._change(
                                c, int(e.value) if e.value else 120
                            ),
                        ).props('filled dense').style('flex-grow: 2').bind_value(
                            self.config, cn
                        )
                    elif cn == "subtitle_sync_executable":
                        ui.input(
                            value=cm.get_config(cn) or "ffsubsync",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        ).props("filled").props("dense").style(
                            "flex-grow: 2"
                        ).bind_value(
                            self.config, cn
                        )
                    elif cn == "subtitle_sync_extra_args":
                        ui.input(
                            value=cm.get_config(cn) or "",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        ).props("filled").props("dense").style(
                            "flex-grow: 2"
                        ).bind_value(
                            self.config, cn
                        )
                    elif cn == "overwrite_existing":
                        # 两态：覆盖（删旧重落）/ 跳过（跳过已存在继续处理其他）。
                        # 兼容旧 bool：True→覆盖，False→跳过。
                        _ow_val = cm.get_config(cn)
                        if isinstance(_ow_val, bool):
                            _ow_cur = "覆盖" if _ow_val else "跳过"
                        else:
                            _ow_cur = _ow_val if _ow_val in ("覆盖", "跳过") else "跳过"
                        tg = RedToogle(
                            ["覆盖", "跳过"],
                            value=_ow_cur,
                            on_change=lambda e, c=cn: self._change(
                                c, e.value
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "hardlink_fallback_to_symlink":
                        # 链接模式下硬链失败是否降级软链接。True=降级（兼容），
                        # False=硬链失败记 partial_failure 不静默降级。
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "rename_bgm_to_tmdb_product_pipeline_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "rename_bgm_to_tmdb_execute_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "openai_output_format":
                        tg = RedToogle(
                            [
                                "structured_output",
                                "function_calling",
                                "text",
                            ],
                            value=cm.get_config(cn) or "structured_output",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "openai_api_interface":
                        tg = RedToogle(
                            ["responses_api", "chat_completions"],
                            value=cm.get_config(cn) or "responses_api",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    else:
                        ui.input(
                            value=cm.get_config(cn),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        ).props("filled").props("dense").style(
                            "flex-grow: 2"
                        ).bind_value(
                            self.config, cn
                        )

                    if cn.endswith("path"):
                        RedButton(
                            "选择",
                            on_click=lambda e, c=cn: self.pick(key=c),
                        ).style("min-width: 60px")
                    else:
                        ui.label("").style("min-width: 60px")

    async def pick(self, *, key: str) -> None:
        result = await local_file_picker('~', multiple=True)
        if isinstance(result, Sequence):
            result = result[0]
        logger.info(f'[配置] {key} 选择了 {result}')
        self._change(key, result)

    def _change(self, key: str, value: object) -> None:
        setattr(self.config, key, value)

    def _get_int_config(self, key: str, default: int) -> int:
        value = cm.get_config(key)
        return value if isinstance(value, int) else default

    def _handle_ok(self):
        # 验证URL配置项
        url_configs = [
            "ai_base_url",
            "telegram_base_url",
            "subtitle_auto_fetch_acgrip_base_url",
        ]
        for url_config in url_configs:
            if hasattr(self.config, url_config):
                url_value = getattr(self.config, url_config)
                if url_value and not cm.validate_url(url_value):
                    ui.notify(
                        f"❌ {CN_MAP.get(url_config, url_config)} 格式无效",
                        type="negative",
                    )
                    return

        old_openai_model = cm.get_config("ai_model")
        new_openai_model = getattr(self.config, "ai_model", None)

        # 保存所有配置（部分运行时统计字段由测试流程自动维护，避免被弹窗旧值覆盖）
        runtime_managed_keys = {
            "openai_auto_routing_enabled",
            "openai_auto_format_order",
            "openai_format_stats",
        }
        for cn in self.config.__dict__:
            if cn in runtime_managed_keys:
                continue
            cm.set_config(
                cn,
                getattr(self.config, cn),
            )

        model_reset_messages = []
        if old_openai_model != new_openai_model:
            cm.set_config("openai_format_stats", {})
            model_reset_messages.append("OpenAI累计测试统计已清零")
            logger.info(
                "[配置] 检测到OpenAI模型变更，已清空openai_format_stats: "
                f"{old_openai_model} -> {new_openai_model}"
            )

        config_show = cm.config.copy()
        for key in config_show.keys():
            if "api_key" in key or key == "telegram_bot_token":
                config_show[key] = len(str(config_show[key])) * "*"

        logger.info('[配置] 配置已修改为： {}'.format(config_show))
        ui.notify("✅ 配置保存成功", type="positive")
        for msg in model_reset_messages:
            ui.notify(f"🧹 {msg}", type="info")
        # 更新运行时日志级别
        try:
            update_log_level_from_config()
            logger.info(f"[配置] 运行时日志级别已更新为 {cm.get_config('log_level')}")
        except Exception as e:
            logger.error(f"[配置] 更新运行时日志级别失败: {e}")
        # 更新Emby通知服务配置
        try:
            from ..notification.emby_notify import refresh_emby_notifier

            refresh_emby_notifier()
            logger.info("[配置] Emby通知服务配置已更新")
        except Exception as e:
            logger.error(f"[配置] 更新Emby通知服务配置失败: {e}")

        # 更新Telegram通知服务配置
        try:
            from ..notification.telegram_notify import (
                refresh_telegram_notifier,
            )

            refresh_telegram_notifier()
            logger.info("[配置] Telegram通知服务配置已更新")
        except Exception as e:
            logger.error(f"[配置] 更新Telegram通知服务配置失败: {e}")
        self.close()

    def _get_current_ui_config(self) -> dict[str, object]:
        """获取当前界面的配置（未保存的）"""
        current_config = {}
        ai_config_keys = [
            "ai_auto_save",
            "ai_confidence_threshold",
            "openai_output_format",
            "openai_api_interface",
            "openai_auto_routing_enabled",
            "openai_auto_format_order",
            "openai_format_stats",
            "ai_api_key",
            "ai_base_url",
            "ai_model",
            "ai_temperature",
        ]
        for key in ai_config_keys:
            # 优先使用界面中的值，如果没有则使用配置文件中的值
            if hasattr(self.config, key):
                current_config[key] = getattr(self.config, key)
            else:
                current_config[key] = cm.get_config(key)
        return current_config

    @staticmethod
    def _as_mapping(value: object) -> Mapping[str, object]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _as_str_list(value: object) -> list[str]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return []
        return [str(item) for item in value]

    @staticmethod
    def _as_mapping_list(value: object) -> list[Mapping[str, object]]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return []
        return [item for item in value if isinstance(item, Mapping)]

    async def _test_ai_recognition(self):
        """测试AI识别功能（使用当前界面配置）"""
        try:
            ui.notify("🧪 开始测试AI识别功能，请稍候...", type="info")
            current_config = self._get_current_ui_config()

            from ..ai.unified_ai_tester import UnifiedAITester

            tester = UnifiedAITester(current_config)

            import asyncio

            result = await asyncio.get_event_loop().run_in_executor(
                None, tester.test_ai_recognition
            )

            self._show_ai_test_results(result)
        except Exception as e:
            logger.error(f"[配置] AI识别测试失败: {str(e)}")
            ui.notify(f"❌ AI识别测试失败: {str(e)}", type="negative")

    async def _test_openai_api(self):
        """测试OpenAI API功能（使用当前界面配置，测试多种输出格式）"""
        try:
            ui.notify("⚙️ 开始测试OpenAI API功能，请稍候...", type="info")
            current_config = self._get_current_ui_config()

            # 检查基本配置
            if not current_config.get("ai_api_key"):
                ui.notify("❌ 请先配置OpenAI API密钥", type="negative")
                return

            from ..ai.unified_ai_tester import UnifiedAITester

            tester = UnifiedAITester(current_config)

            import asyncio

            results = await asyncio.get_event_loop().run_in_executor(
                None, tester.test_openai_api_formats
            )

            self._show_openai_formats_test_results(results)

            # 记忆测试后推荐格式，界面内同步为推荐值
            recommended = results.get("recommended_format")
            if recommended:
                self._change("openai_output_format", recommended)
                ui.notify(
                    f"✅ 已记忆OpenAI格式顺序，当前推荐: {recommended}",
                    type="positive",
                )
        except Exception as e:
            logger.error(f"[配置] OpenAI API测试失败: {str(e)}")
            ui.notify(f"❌ OpenAI API测试失败: {str(e)}", type="negative")

    def _show_ai_test_results(self, result: Mapping[str, object]) -> None:
        """显示AI识别测试结果"""
        with ui.dialog() as dialog, ui.card().classes("w-[600px]"):
            ui.label("🧪 AI识别功能测试结果").classes("text-h6 mb-4")

            # 配置提示
            ui.label("💡 此测试使用界面中的配置，但不会保存配置").classes(
                "text-sm text-blue mb-4"
            )

            with ui.column().classes("w-full gap-3"):
                # 基本信息 - 根据结果状态显示
                result_status = result.get("result_status", "unknown")

                if result_status == "perfect":
                    status_icon = "✅"
                    status_text = "完全正确"
                    status_color = "text-green"
                elif result_status == "validation_failed":
                    status_icon = "⚠️"
                    status_text = "验证失败"
                    status_color = "text-orange"
                elif result_status == "ai_failed":
                    status_icon = "❌"
                    status_text = "AI失败"
                    status_color = "text-red"
                else:
                    status_icon = "❓"
                    status_text = "未知状态"
                    status_color = "text-gray"

                ui.label(f"{status_icon} 测试状态: {status_text}").classes(
                    f"font-bold {status_color}"
                )

                # 配置信息
                config_used = result.get("config_used", {})
                if not isinstance(config_used, Mapping):
                    config_used = {}
                ui.label("🤖 AI提供商: OPENAI")
                ui.label(f"⏱️ 耗时: {result.get('duration', 0):.2f}秒")

                output_format = config_used.get("openai_output_format", "unknown")
                ui.label(f"📋 输出格式: {output_format}")
                configured_interface = result.get("configured_interface")
                actual_interface = result.get("actual_interface")
                if configured_interface:
                    ui.label(f"🌐 配置接口: {configured_interface}")
                if actual_interface:
                    ui.label(f"🚀 实际接口: {actual_interface}")
                if result.get("interface_fallback"):
                    fallback_reason = result.get("interface_fallback_reason")
                    ui.label(
                        f"↩️ 接口回退: 已触发 ({fallback_reason or '未提供原因'})"
                    ).classes("text-orange")
                # AI失败情况：显示错误信息
                if result_status == "ai_failed":
                    ui.separator()
                    ui.label("❌ 错误详情").classes("font-bold text-red")
                    if result.get("error"):
                        ui.label(f"错误信息: {result['error']}").classes("text-red")
                    else:
                        ui.label("AI分析返回None，可能是API调用失败或解析错误").classes(
                            "text-red"
                        )

                # 验证失败和完全正确情况：显示详细结果
                elif result_status in ["validation_failed", "perfect"] and result.get(
                    "validation"
                ):
                    validation = self._as_mapping(result["validation"])
                    ui.separator()
                    ui.label("📊 分析结果").classes("font-bold")

                    confidence = validation.get("confidence", "None")
                    ui.label(f"🎯 置信度: {confidence}")

                    file_count = validation.get("file_mapping_count", 0)
                    ui.label(f"📁 映射文件数: {file_count}")

                    # 验证详情
                    if "validation_details" in validation:
                        details = self._as_mapping(validation["validation_details"])
                        if "accuracy" in details:
                            accuracy_value = details.get("accuracy", 0)
                            accuracy = float(accuracy_value) * 100 if isinstance(accuracy_value, (int, float, str)) else 0.0
                            accuracy_color = (
                                "text-green" if accuracy == 100 else "text-orange"
                            )
                            ui.label(f"✅ 准确率: {accuracy:.1f}%").classes(
                                accuracy_color
                            )

                            matched_count_value = details.get("matched_count", 0)
                            matched_count = (
                                int(matched_count_value)
                                if isinstance(matched_count_value, (int, float, str))
                                else 0
                            )
                            expected_count_value = details.get("expected_count", 0)
                            expected_count = (
                                int(expected_count_value)
                                if isinstance(expected_count_value, (int, float, str))
                                else 0
                            )
                            ui.label(f"📈 匹配情况: {matched_count}/{expected_count}")

                            # 显示详细的文件匹配情况
                            missing_files = self._as_str_list(details.get("missing_files", []))
                            extra_files = self._as_str_list(details.get("extra_files", []))
                            matched_files = self._as_str_list(details.get("matched_files", []))

                            if matched_files:
                                ui.label(
                                    f"✅ 正确匹配 ({len(matched_files)}):"
                                ).classes("text-green font-bold")
                                for file_path in matched_files:
                                    ui.label(f"  • {file_path}").classes(
                                        "text-sm text-green"
                                    )

                            if missing_files:
                                ui.label(
                                    f"❌ 遗漏文件 ({len(missing_files)}):"
                                ).classes("text-red font-bold")
                                for file_path in missing_files:
                                    ui.label(f"  • {file_path}").classes(
                                        "text-sm text-red"
                                    )

                            if extra_files:
                                ui.label(f"⚠️ 多余文件 ({len(extra_files)}):").classes(
                                    "text-orange font-bold"
                                )
                                for file_path in extra_files:
                                    ui.label(f"  • {file_path}").classes(
                                        "text-sm text-orange"
                                    )

            # 关闭按钮
            with ui.row().classes("w-full justify-end mt-4"):
                RedButton("关闭", on_click=dialog.close)

        dialog.open()

    def _render_provider_routing_stats(self, provider_name: str) -> None:
        """渲染提供商自动路由顺序与累计测试成功率"""
        provider_key = provider_name.lower()
        auto_order = self._as_str_list(cm.get_config(f"{provider_key}_auto_format_order") or [])
        format_stats_value = cm.get_config(f"{provider_key}_format_stats") or {}
        format_stats = format_stats_value if isinstance(format_stats_value, Mapping) else {}

        if auto_order:
            ui.label(f"🧭 自动路由顺序: {' -> '.join(auto_order)}").classes(
                "text-blue"
            )

        if isinstance(format_stats, dict) and format_stats:
            ui.label("📊 累计测试成功率:").classes("text-blue")

            display_order = [fmt for fmt in auto_order if fmt in format_stats]
            for fmt in format_stats:
                if fmt not in display_order:
                    display_order.append(fmt)

            for fmt in display_order:
                stat_value = format_stats.get(fmt, {})
                stat = stat_value if isinstance(stat_value, Mapping) else {}
                if not stat:
                    continue

                total_runs_value = stat.get("total_runs", 0)
                success_runs_value = stat.get("success_runs", 0)
                perfect_runs_value = stat.get("perfect_runs", 0)
                total_runs = int(total_runs_value) if isinstance(total_runs_value, (int, float, str)) else 0
                success_runs = int(success_runs_value) if isinstance(success_runs_value, (int, float, str)) else 0
                perfect_runs = int(perfect_runs_value) if isinstance(perfect_runs_value, (int, float, str)) else 0

                if total_runs <= 0:
                    continue

                success_rate = success_runs / total_runs * 100
                perfect_rate = perfect_runs / total_runs * 100

                ui.label(
                    f"  • {fmt}: 成功 {success_runs}/{total_runs} ({success_rate:.1f}%), 完全正确 {perfect_runs}/{total_runs} ({perfect_rate:.1f}%)"
                ).classes("text-sm text-blue-grey")

    def _show_openai_formats_test_results(self, results: Mapping[str, object]) -> None:
        """显示OpenAI多格式测试结果"""
        self._show_provider_formats_test_results("OpenAI", results)

    def _show_provider_formats_test_results(
        self, provider_name: str, results: Mapping[str, object]
    ) -> None:
        """显示指定提供商的多格式测试结果"""
        with ui.dialog() as dialog, ui.card().classes("w-[700px]"):
            ui.label(f"⚙️ {provider_name} API多格式测试结果").classes("text-h6 mb-4")

            # 配置提示
            ui.label("💡 此测试使用界面中的配置，但不会保存配置").classes(
                "text-sm text-blue mb-4"
            )

            with ui.column().classes("w-full gap-3"):
                # 总体结果
                overall_success = results.get("success", False)
                success_icon = "✅" if overall_success else "❌"
                ui.label(
                    f"{success_icon} 总体状态: {'至少一种格式成功' if overall_success else '所有格式均失败'}"
                ).classes("font-bold")

                if results.get("error"):
                    ui.label(f"❌ 错误信息: {results['error']}").classes("text-red")

                # 推荐格式
                if overall_success:
                    recommended = results.get("recommended_format", "text")
                    ui.label(f"🌟 推荐格式: {recommended}").classes(
                        "text-green font-bold"
                    )

                self._render_provider_routing_stats(provider_name)

                ui.separator()

                # 各格式详细结果
                format_results = self._as_mapping_list(results.get("format_results", []))
                for format_result in format_results:
                    output_format = str(format_result.get("output_format", "unknown"))
                    result_status = str(format_result.get("result_status", "unknown"))

                    # 根据结果状态确定图标和标题
                    if result_status == "perfect":
                        icon = "✅"
                        status_text = "完全正确"
                        status_color = "text-green"
                    elif result_status == "validation_failed":
                        icon = "⚠️"
                        status_text = "验证失败"
                        status_color = "text-orange"
                    elif result_status == "ai_failed":
                        icon = "❌"
                        status_text = "AI失败"
                        status_color = "text-red"
                    else:
                        icon = "❓"
                        status_text = "未知状态"
                        status_color = "text-gray"

                    with ui.expansion(
                        f"{icon} {output_format} - {status_text}", icon="settings"
                    ).classes("w-full"):
                        with ui.column().classes("gap-2 p-2"):
                            ui.label(f"状态: {status_text}").classes(
                                status_color + " font-bold"
                            )
                            duration_value = format_result.get("duration", 0)
                            duration = (
                                float(duration_value)
                                if isinstance(duration_value, (int, float, str))
                                else 0.0
                            )
                            ui.label(f"耗时: {duration:.2f}秒")

                            configured_interface = format_result.get(
                                "configured_interface"
                            )
                            actual_interface = format_result.get("actual_interface")
                            if configured_interface:
                                ui.label(f"配置接口: {configured_interface}")
                            if actual_interface:
                                ui.label(f"实际接口: {actual_interface}")
                            if format_result.get("interface_fallback"):
                                fallback_reason = format_result.get(
                                    "interface_fallback_reason"
                                )
                                ui.label(
                                    "接口回退: 已触发 "
                                    f"({fallback_reason or '未提供原因'})"
                                ).classes("text-orange")

                            # AI失败情况：显示错误信息
                            if result_status == "ai_failed":
                                if format_result.get("error"):
                                    ui.label(
                                        f"错误详情: {format_result['error']}"
                                    ).classes("text-red")
                                else:
                                    ui.label(
                                        "AI分析返回None，可能是API调用失败或解析错误"
                                    ).classes("text-red")

                            # 验证失败和完全正确情况：显示详细结果
                            elif result_status in [
                                "validation_failed",
                                "perfect",
                            ] and format_result.get("validation"):
                                validation = self._as_mapping(format_result["validation"])
                                confidence = validation.get("confidence", "None")
                                ui.label(f"置信度: {confidence}")

                                file_count = validation.get("file_mapping_count", 0)
                                ui.label(f"映射文件数: {file_count}")

                                if "validation_details" in validation:
                                    details = self._as_mapping(validation["validation_details"])
                                    if "accuracy" in details:
                                        accuracy_value = details.get("accuracy", 0)
                                        accuracy = (
                                            float(accuracy_value) * 100
                                            if isinstance(accuracy_value, (int, float, str))
                                            else 0.0
                                        )
                                        accuracy_color = (
                                            "text-green"
                                            if accuracy == 100
                                            else "text-orange"
                                        )
                                        ui.label(f"准确率: {accuracy:.1f}%").classes(
                                            accuracy_color
                                        )

                                        matched_count = details.get("matched_count", 0)
                                        expected_count = details.get(
                                            "expected_count", 0
                                        )
                                        ui.label(
                                            f"匹配情况: {matched_count}/{expected_count}"
                                        )

                                        # 显示详细的文件匹配情况
                                        missing_files = self._as_str_list(details.get("missing_files", []))
                                        extra_files = self._as_str_list(details.get("extra_files", []))
                                        matched_files = self._as_str_list(details.get("matched_files", []))

                                        if matched_files:
                                            ui.label(
                                                f"✅ 正确匹配 ({len(matched_files)}):"
                                            ).classes("text-green font-bold")
                                            for file_path in matched_files:
                                                ui.label(f"  • {file_path}").classes(
                                                    "text-sm text-green"
                                                )

                                        if missing_files:
                                            ui.label(
                                                f"❌ 遗漏文件 ({len(missing_files)}):"
                                            ).classes("text-red font-bold")
                                            for file_path in missing_files:
                                                ui.label(f"  • {file_path}").classes(
                                                    "text-sm text-red"
                                                )

                                        if extra_files:
                                            ui.label(
                                                f"⚠️ 多余文件 ({len(extra_files)}):"
                                            ).classes("text-orange font-bold")
                                            for file_path in extra_files:
                                                ui.label(f"  • {file_path}").classes(
                                                    "text-sm text-orange"
                                                )

            # 关闭按钮
            with ui.row().classes("w-full justify-end mt-4"):
                RedButton("关闭", on_click=dialog.close)

        dialog.open()

    async def _test_emby_connection(self):
        """测试Emby连接"""
        try:
            ui.notify("🧪 正在测试Emby连接...", type="info")

            from ..notification.emby_notify import EmbyNotifier

            # 临时保存原配置
            original_host = cm.get_config("emby_host")
            original_key = cm.get_config("emby_api_key")

            # 临时应用当前界面配置
            if hasattr(self.config, "emby_host"):
                cm.set_config("emby_host", getattr(self.config, "emby_host"))
            if hasattr(self.config, "emby_api_key"):
                cm.set_config("emby_api_key", getattr(self.config, "emby_api_key"))

            try:
                notifier = EmbyNotifier()

                import asyncio

                success, message = await asyncio.get_event_loop().run_in_executor(
                    None, notifier.test_connection
                )

                if success:
                    ui.notify(f"✅ {message}", type="positive")
                else:
                    ui.notify(f"❌ {message}", type="negative")
            finally:
                # 恢复原配置
                cm.set_config("emby_host", original_host)
                cm.set_config("emby_api_key", original_key)

        except Exception as e:
            logger.error(f"[配置] Emby连接测试失败: {str(e)}")
            ui.notify(f"❌ 测试失败: {str(e)}", type="negative")

    async def _test_telegram_connection(self):
        """测试Telegram连接"""
        try:
            ui.notify("正在测试Telegram连接...", type="info")

            from ..notification.telegram_notify import TelegramNotifier

            # 临时保存原配置
            original_enabled = cm.get_config("telegram_enabled")
            original_token = cm.get_config("telegram_bot_token")
            original_chat_id = cm.get_config("telegram_chat_id")
            original_base_url = cm.get_config("telegram_base_url")

            # 临时应用当前界面配置
            cm.set_config("telegram_enabled", True)
            if hasattr(self.config, "telegram_bot_token"):
                cm.set_config(
                    "telegram_bot_token",
                    getattr(self.config, "telegram_bot_token"),
                )
            if hasattr(self.config, "telegram_chat_id"):
                cm.set_config(
                    "telegram_chat_id",
                    getattr(self.config, "telegram_chat_id"),
                )
            if hasattr(self.config, "telegram_base_url"):
                cm.set_config(
                    "telegram_base_url",
                    getattr(self.config, "telegram_base_url"),
                )

            try:
                notifier = TelegramNotifier()

                import asyncio

                success, message = await asyncio.get_event_loop().run_in_executor(
                    None, notifier.test_connection
                )

                if success:
                    ui.notify(message, type="positive")
                else:
                    ui.notify(message, type="negative")
            finally:
                # 恢复原配置
                cm.set_config("telegram_enabled", original_enabled)
                cm.set_config("telegram_bot_token", original_token)
                cm.set_config("telegram_chat_id", original_chat_id)
                cm.set_config("telegram_base_url", original_base_url)

        except Exception as e:
            logger.error(f"[配置] Telegram连接测试失败: {str(e)}")
            ui.notify(f"测试失败: {str(e)}", type="negative")


async def config_page() -> None:
    await ConfigPage()
