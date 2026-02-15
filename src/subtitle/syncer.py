"""字幕对齐模块（ffsubsync）"""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..config.config_manager import cm
from ..logger import logger


@dataclass
class SyncResult:
    """字幕对齐执行结果"""

    success: bool
    used_fallback: bool
    reason: str
    output_path: Optional[Path]
    duration: float


class FFsubsyncRunner:
    """ffsubsync 执行封装"""

    @staticmethod
    def _parse_extra_args(extra_args_raw: str) -> List[str]:
        """解析额外参数，支持双引号包裹参数值"""
        extra_args: List[str] = []
        current = ""
        in_quotes = False

        for char in str(extra_args_raw):
            if char == '"':
                in_quotes = not in_quotes
                continue
            if char.isspace() and not in_quotes:
                if current:
                    extra_args.append(current)
                    current = ""
                continue
            current += char

        if current:
            extra_args.append(current)

        if in_quotes:
            raise ValueError("双引号未闭合")

        return extra_args

    def sync_subtitle(
        self,
        video_path: Path,
        subtitle_path: Path,
        output_dir: Path,
    ) -> SyncResult:
        start_time = time.perf_counter()
        output_path = output_dir / subtitle_path.name

        executable = cm.get_config("subtitle_sync_executable") or "ffsubsync"
        timeout_seconds = cm.get_config("subtitle_sync_timeout_seconds") or 120
        extra_args_raw = cm.get_config("subtitle_sync_extra_args") or ""

        command = [
            str(executable),
            str(video_path),
            "-i",
            str(subtitle_path),
            "-o",
            str(output_path),
        ]

        try:
            extra_args = self._parse_extra_args(str(extra_args_raw))
            if extra_args:
                command.extend(extra_args)
        except ValueError as e:
            duration = time.perf_counter() - start_time
            reason = f"extra_args 解析失败: {e}"
            logger.error(f"[字幕同步] {reason}")
            return SyncResult(
                success=False,
                used_fallback=True,
                reason=reason,
                output_path=None,
                duration=duration,
            )

        logger.info(
            f"[字幕同步] 开始对齐: {subtitle_path.name} -> {video_path.name}"
        )

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=int(timeout_seconds),
                check=False,
            )
        except FileNotFoundError:
            duration = time.perf_counter() - start_time
            reason = f"未找到可执行文件: {executable}"
            logger.error(f"[字幕同步] {reason}")
            return SyncResult(
                success=False,
                used_fallback=True,
                reason=reason,
                output_path=None,
                duration=duration,
            )
        except subprocess.TimeoutExpired:
            duration = time.perf_counter() - start_time
            reason = f"执行超时（>{timeout_seconds}s）"
            logger.error(f"[字幕同步] {reason}")
            return SyncResult(
                success=False,
                used_fallback=True,
                reason=reason,
                output_path=None,
                duration=duration,
            )
        except Exception as e:
            duration = time.perf_counter() - start_time
            reason = f"执行异常: {e}"
            logger.error(f"[字幕同步] {reason}")
            return SyncResult(
                success=False,
                used_fallback=True,
                reason=reason,
                output_path=None,
                duration=duration,
            )

        duration = time.perf_counter() - start_time

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            message = stderr or stdout or "未知错误"
            reason = f"ffsubsync 返回非零退出码({result.returncode}): {message}"
            logger.error(f"[字幕同步] {reason}")
            return SyncResult(
                success=False,
                used_fallback=True,
                reason=reason,
                output_path=None,
                duration=duration,
            )

        if not output_path.exists():
            reason = "ffsubsync 执行成功但未生成输出文件"
            logger.error(f"[字幕同步] {reason}")
            return SyncResult(
                success=False,
                used_fallback=True,
                reason=reason,
                output_path=None,
                duration=duration,
            )

        return SyncResult(
            success=True,
            used_fallback=False,
            reason="",
            output_path=output_path,
            duration=duration,
        )
