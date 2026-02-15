"""
字幕导入页面

提供字幕压缩包选择、拖放上传、确认流程和结果展示功能。
"""

from pathlib import Path
from typing import Optional, Sequence

from nicegui import ui, run
from nicegui.events import UploadEventArguments

from ..logger import logger
from ..notification.emby_notify import get_emby_notifier
from ..subtitle.processor import SubtitleProcessor
from ..pages.data_table_page import create_subtitle_table
from ..element.red import RedButton, notify
from ..component.local_file_picker import local_file_picker
from ..utils.path import SUBTITLE_UPLOAD_PATH


# 支持的文件格式
SUPPORTED_EXTENSIONS = {".zip", ".rar", ".ass", ".ssa", ".srt", ".sub", ".vtt"}


class ChooseTaskDialog(ui.dialog):
    """选择目标任务对话框"""

    def __init__(self, available_tasks: list) -> None:
        super().__init__()
        self.available_tasks = available_tasks
        self.selected_uuid = None

        _s = "width: 50%; max-width: 600px;"
        with self, ui.card().style(_s):
            ui.label("选择目标动漫").style("font-size: 20px; font-weight: bold")
            ui.separator()

            ui.label("AI 无法自动匹配，请手动选择目标动漫：").style("color: orange")

            if available_tasks:
                options = {
                    t["uuid"]: f"{t['title']} (Season {t.get('season', 1)})"
                    for t in available_tasks
                }
                self.task_select = ui.select(
                    options=options,
                    value=list(options.keys())[0] if options else None,
                    label="选择动漫",
                ).classes("w-full")
            else:
                ui.label("无可用任务").style("color: red")

            ui.separator()

            with ui.row().classes("w-full justify-end"):
                RedButton("取消", on_click=self.close).props("outline")
                if available_tasks:
                    RedButton("确认", on_click=self._handle_ok)

    def _handle_ok(self) -> None:
        if hasattr(self, "task_select") and self.task_select.value:
            self.selected_uuid = self.task_select.value
        self.close()
        self.submit(self.selected_uuid)


class SubtitleUploadDialog(ui.dialog):
    """字幕上传对话框（支持拖放、确认和结果展示，支持批量处理）"""

    def __init__(self) -> None:
        super().__init__()
        self.upload_dir = SUBTITLE_UPLOAD_PATH
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.current_files: list[Path] = []  # 支持多个文件
        self.processor = SubtitleProcessor()
        self.batch_results: list[dict] = []  # 批量处理结果

        _s = "width: 60%; max-width: 700px; max-height: 85vh;"
        with self, ui.card().style(_s):
            # 标题
            self.title_label = ui.label("字幕导入").style(
                "font-size: 20px; font-weight: bold"
            )
            ui.separator()

            # 内容区域（动态更新）
            self.content_container = ui.column().classes("w-full")

            # 初始显示上传界面
            self._show_upload_view()

    def _show_upload_view(self) -> None:
        """显示上传界面"""
        self.content_container.clear()

        with self.content_container:
            ui.label(
                "拖放字幕文件或压缩包到下方区域，或点击选择文件（支持多选）"
            ).style("color: #666; font-size: 13px; margin-bottom: 8px;")

            # 拖放上传区域（支持多文件）
            self.upload = ui.upload(
                label="点击选择文件",
                on_upload=self._handle_upload,
                auto_upload=True,
                multiple=True,  # 启用多文件上传
            ).props(
                'accept=".zip,.rar,.ass,.ssa,.srt,.sub,.vtt" color="red-4" flat bordered'
            ).classes("w-full").style("min-height: 100px;")

            ui.separator()

            with ui.row().classes("w-full justify-end gap-2"):
                RedButton("📂 浏览本地", on_click=self._pick_local_file).props(
                    "outline"
                )
                RedButton("关闭", on_click=self._close_and_cleanup).props("outline")

    def _show_confirm_view(self, filenames: list[str]) -> None:
        """显示确认界面（支持多个文件）"""
        self.content_container.clear()
        self.title_label.set_text("确认导入")

        with self.content_container:
            with ui.column().classes("w-full gap-2"):
                file_count = len(filenames)
                ui.label(f"已选择 {file_count} 个文件:").style("font-weight: bold;")

                with ui.scroll_area().style("max-height: 150px").classes("w-full"):
                    for i, filename in enumerate(filenames, 1):
                        ui.label(f"📦 {i}. {filename}").style(
                            "font-size: 14px; color: #333; padding: 5px; "
                            "background: #f5f5f5; border-radius: 4px; margin: 2px 0;"
                        )

                ui.label(
                    f"点击「开始导入」后，将依次处理 {file_count} 个压缩包，"
                    "AI 将分析字幕并匹配到对应的动漫。"
                ).style("color: #666; font-size: 13px; margin-top: 10px;")

            ui.separator()

            with ui.row().classes("w-full justify-end gap-2"):
                RedButton("← 重新选择", on_click=self._reset_to_upload).props(
                    "outline"
                )
                RedButton("开始导入", on_click=self._start_batch_processing)

    def _show_processing_view(self, current: int = 0, total: int = 1) -> None:
        """显示处理中界面"""
        self.content_container.clear()
        self.title_label.set_text(f"正在处理... ({current}/{total})")

        with self.content_container:
            with ui.column().classes("w-full items-center gap-4 py-8"):
                ui.spinner("dots", size="lg", color="red")
                if total > 1:
                    ui.label(f"正在处理第 {current} 个压缩包，共 {total} 个...").style(
                        "color: #666;"
                    )
                    # 进度条
                    ui.linear_progress(value=current / total, show_value=False).props(
                        "color=red"
                    ).classes("w-full")
                else:
                    ui.label("正在解压并分析字幕文件...").style("color: #666;")

    def _show_batch_result_view(self, results: list[dict]) -> None:
        """显示批量处理结果界面"""
        self.content_container.clear()

        success_count = sum(1 for r in results if r["status"] == "success")
        total_count = len(results)

        if success_count == total_count:
            self.title_label.set_text(f"全部导入成功 ({success_count}/{total_count})")
            title_color = "green"
        elif success_count > 0:
            self.title_label.set_text(f"部分导入成功 ({success_count}/{total_count})")
            title_color = "orange"
        else:
            self.title_label.set_text(f"导入失败 (0/{total_count})")
            title_color = "red"

        self.title_label.style(
            f"font-size: 20px; font-weight: bold; color: {title_color}"
        )

        with self.content_container:
            with ui.scroll_area().style("max-height: 350px").classes("w-full"):
                for result in results:
                    is_success = result["status"] == "success"
                    bg_color = "#e8f5e9" if is_success else "#ffebee"
                    icon = "✅" if is_success else "❌"

                    with ui.card().style(
                        f"background: {bg_color}; margin: 5px 0; padding: 10px;"
                    ).classes("w-full"):
                        ui.label(
                            f"{icon} {Path(result['archive_path']).name}"
                        ).style("font-weight: bold;")

                        if is_success:
                            ui.label(
                                f"匹配: {result.get('matched_task', '')} | "
                                f"字幕: {result.get('matched_count', 0)}/"
                                f"{result.get('total_subtitles', 0)}"
                            ).style("font-size: 13px; color: #666;")
                        else:
                            ui.label(
                                f"错误: {result.get('error', '未知错误')}"
                            ).style("font-size: 13px; color: red;")

            ui.separator()

            # 统计信息
            total_matched = sum(r.get("matched_count", 0) for r in results)
            total_subtitles = sum(r.get("total_subtitles", 0) for r in results)
            ui.label(
                f"总计: {success_count}/{total_count} 个压缩包成功, "
                f"{total_matched}/{total_subtitles} 个字幕匹配"
            ).style("color: #666; font-size: 13px;")

            ui.separator()

            with ui.row().classes("w-full justify-end gap-2"):
                RedButton("继续导入", on_click=self._reset_to_upload).props("outline")
                RedButton("关闭", on_click=self._close_and_cleanup)

    def _show_result_view(self, result: dict) -> None:
        """显示结果界面"""
        self.content_container.clear()

        if result["status"] == "success":
            self.title_label.set_text("导入成功")
            title_color = "green"
        else:
            self.title_label.set_text("导入失败")
            title_color = "red"

        self.title_label.style(f"font-size: 20px; font-weight: bold; color: {title_color}")

        with self.content_container:
            with ui.column().classes("w-full"):
                ui.label(f"压缩包: {Path(result['archive_path']).name}")

                if result["status"] == "success":
                    ui.label(f"匹配动漫: {result.get('matched_task', '')}")
                    ui.label(
                        f"成功匹配: {result.get('matched_count', 0)} / "
                        f"{result.get('total_subtitles', 0)} 个字幕"
                    )
                    ui.label(f"置信度: {result.get('confidence', '')}")

                    # 显示映射详情
                    if result.get("mappings"):
                        ui.label("映射详情:").style(
                            "margin-top: 10px; font-weight: bold"
                        )
                        with ui.scroll_area().style("max-height: 250px").classes(
                            "w-full"
                        ):
                            for m in result["mappings"]:
                                with ui.row().classes("w-full items-center gap-2"):
                                    ui.label(f"{m['subtitle']}").style(
                                        "font-size: 12px; color: #666;"
                                    )
                                    ui.label("→").style("color: gray")
                                    ui.label(f"{m['target']}").style(
                                        "font-size: 12px; color: #333;"
                                    )

                else:
                    ui.label(f"错误: {result.get('error', '未知错误')}").style(
                        "color: red"
                    )

            ui.separator()

            with ui.row().classes("w-full justify-end gap-2"):
                RedButton("继续导入", on_click=self._reset_to_upload).props("outline")
                RedButton("关闭", on_click=self._close_and_cleanup)

    def _reset_to_upload(self) -> None:
        """重置到上传界面"""
        self.current_files = []
        self.batch_results = []
        self.title_label.set_text("字幕导入")
        self.title_label.style("font-size: 20px; font-weight: bold; color: inherit")
        self._show_upload_view()

    async def _handle_upload(self, e: UploadEventArguments) -> None:
        """处理上传的文件"""
        # 兼容 NiceGUI 2.10+ (file_name) 和旧版本 (name)
        filename = getattr(e, "file_name", None) or getattr(e, "name", None)
        if filename is None:
            notify("无法获取文件名!")
            return
        content = e.content.read()

        # 检查文件格式
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            notify("请选择支持的文件格式（ZIP/RAR/ASS/SRT等）!")
            return

        # 保存到上传目录
        upload_path = self.upload_dir / filename
        with open(upload_path, "wb") as f:
            f.write(content)

        logger.info(f"[字幕导入] 上传文件保存到: {upload_path}")
        self.current_files.append(upload_path)

        # 显示确认界面
        self._show_confirm_view([f.name for f in self.current_files])

    async def _pick_local_file(self) -> None:
        """使用文件选择器选择本地文件（支持多选）"""
        result: Optional[Sequence[str]] = await local_file_picker(
            "~",
            multiple=True,  # 启用多选
        )

        if result is None:
            return

        valid_files = []
        for file_path in result:
            file_p = Path(file_path)
            # 检查文件格式
            if file_p.suffix.lower() in SUPPORTED_EXTENSIONS:
                valid_files.append(file_p)
            else:
                logger.warning(f"[字幕导入] 跳过不支持的文件: {file_p.name}")

        if not valid_files:
            notify("请选择支持的文件格式（ZIP/RAR/ASS/SRT等）!")
            return

        self.current_files = valid_files

        # 显示确认界面
        self._show_confirm_view([f.name for f in self.current_files])

    async def _start_batch_processing(self) -> None:
        """开始批量处理字幕"""
        if not self.current_files:
            notify("请先选择文件!")
            return

        total = len(self.current_files)
        self.batch_results = []

        for i, file_path in enumerate(self.current_files, 1):
            # 更新处理中界面
            self._show_processing_view(current=i, total=total)

            # 处理字幕
            process_result = await run.io_bound(
                self.processor.process,
                file_path,
            )

            if process_result["status"] == "need_confirm":
                # 需要用户选择任务
                available_tasks = process_result.get("available_tasks", [])
                dialog = ChooseTaskDialog(available_tasks)
                selected_uuid = await dialog

                if selected_uuid:
                    # 使用选中的任务重新处理
                    self._show_processing_view(current=i, total=total)
                    process_result = await run.io_bound(
                        self.processor.process,
                        file_path,
                        selected_uuid,
                    )
                else:
                    # 用户取消，标记为失败
                    process_result = {
                        "status": "error",
                        "archive_path": str(file_path),
                        "error": "用户取消选择",
                    }

            self.batch_results.append(process_result)

        # 显示批量结果
        self._show_batch_result_view(self.batch_results)

        # 统计并通知
        success_count = sum(1 for r in self.batch_results if r["status"] == "success")
        total_matched = sum(r.get("matched_count", 0) for r in self.batch_results)

        if success_count > 0:
            notify(
                f"批量导入完成! {success_count}/{total} 成功, 共匹配 {total_matched} 个字幕"
            )

            # 批量完成后，仅刷新一次 Emby（避免每个压缩包都触发刷新）
            try:
                emby = get_emby_notifier()
                if emby.is_available():
                    success, message = emby.refresh_library()
                    if success:
                        notify(f"已通知 Emby 刷新媒体库: {message}", type="positive")
                    else:
                        notify(f"Emby 刷新失败: {message}", type="warning")
                else:
                    logger.info("[字幕导入] Emby 通知未启用或未配置，跳过刷新")
            except Exception as e:
                logger.error(f"[字幕导入] Emby 通知异常: {e}")
                notify(f"Emby 通知异常: {e}", type="warning")
        else:
            notify("批量导入失败!")

        create_subtitle_table.refresh()

    def _close_and_cleanup(self) -> None:
        """关闭对话框（不清理压缩包，保留以便重试）"""
        self.close()


async def pick_subtitle_file() -> None:
    """打开字幕上传对话框"""
    dialog = SubtitleUploadDialog()
    dialog.open()
