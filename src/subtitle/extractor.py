"""
字幕压缩包解压模块

支持 ZIP 和 RAR 格式，处理中日文文件名编码问题。
保留压缩包内的文件夹结构信息。
"""

import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..logger import logger

# 尝试导入 rarfile
try:
    import rarfile

    RAR_AVAILABLE = True
except ImportError:
    RAR_AVAILABLE = False

# 支持的字幕格式
SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt"}


@dataclass
class ExtractedSubtitle:
    """解压后的字幕文件信息"""

    # 解压后的临时文件路径
    temp_path: Path
    # 压缩包内的原始相对路径（保留文件夹结构）
    archive_path: str
    # 文件名（不含路径）
    filename: str


class SubtitleExtractor:
    """压缩包解压器"""

    def __init__(self, temp_dir: Optional[Path] = None):
        self.temp_dir = temp_dir or Path(tempfile.gettempdir()) / "bangumi_subtitle"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, archive_path: Path) -> Optional[List[ExtractedSubtitle]]:
        """
        解压压缩包或处理单个字幕文件，返回字幕文件列表（保留路径结构信息）

        Args:
            archive_path: 压缩包或字幕文件路径

        Returns:
            ExtractedSubtitle 列表，失败返回 None
        """
        if not archive_path.exists():
            logger.error(f"[字幕解压] 文件不存在: {archive_path}")
            return None

        suffix = archive_path.suffix.lower()

        if suffix == ".zip":
            return self._extract_zip(archive_path)
        elif suffix == ".rar":
            return self._extract_rar(archive_path)
        elif suffix in SUBTITLE_EXTENSIONS:
            # 直接字幕文件，不需要解压
            return self._handle_direct_subtitle(archive_path)
        else:
            logger.error(f"[字幕解压] 不支持的格式: {suffix}")
            return None

    def _handle_direct_subtitle(
        self, subtitle_path: Path
    ) -> Optional[List[ExtractedSubtitle]]:
        """处理直接上传的字幕文件（非压缩包）"""
        try:
            # 复制到临时目录
            extract_dir = self.temp_dir / f"direct_{subtitle_path.stem}"
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)

            target_path = extract_dir / subtitle_path.name
            shutil.copy2(subtitle_path, target_path)

            logger.info(f"[字幕导入] 直接字幕文件: {subtitle_path.name}")

            return [
                ExtractedSubtitle(
                    temp_path=target_path,
                    archive_path=subtitle_path.name,
                    filename=subtitle_path.name,
                )
            ]
        except Exception as e:
            logger.error(f"[字幕导入] 处理字幕文件失败: {e}")
            return None

    def _extract_zip(self, archive_path: Path) -> Optional[List[ExtractedSubtitle]]:
        """解压 ZIP 文件"""
        try:
            extract_dir = self.temp_dir / archive_path.stem
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)

            subtitle_files: List[ExtractedSubtitle] = []

            with zipfile.ZipFile(archive_path, "r") as zf:
                for info in zf.infolist():
                    # 跳过目录
                    if info.is_dir():
                        continue

                    # 处理文件名编码
                    try:
                        # 尝试 UTF-8
                        filename = info.filename
                        # 检测是否为乱码 (cp437 编码的中文)
                        if any(ord(c) > 127 for c in filename):
                            try:
                                # 尝试从 cp437 解码为 GBK
                                filename = info.filename.encode("cp437").decode(
                                    "gbk"
                                )
                            except (UnicodeDecodeError, UnicodeEncodeError):
                                pass
                    except Exception:
                        filename = info.filename

                    # 只提取字幕文件
                    file_path = Path(filename)
                    if file_path.suffix.lower() not in SUBTITLE_EXTENSIONS:
                        continue

                    # 保留文件夹结构解压
                    # 使用安全的路径（避免路径遍历攻击）
                    safe_path = Path(*file_path.parts) if file_path.parts else file_path
                    target_path = extract_dir / safe_path

                    # 创建父目录
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    with zf.open(info) as source:
                        with open(target_path, "wb") as target:
                            target.write(source.read())

                    subtitle_files.append(
                        ExtractedSubtitle(
                            temp_path=target_path,
                            archive_path=filename,
                            filename=file_path.name,
                        )
                    )

            logger.info(f"[字幕解压] ZIP 解压成功，找到 {len(subtitle_files)} 个字幕文件")
            return subtitle_files

        except Exception as e:
            logger.error(f"[字幕解压] ZIP 解压失败: {e}")
            return None

    def _extract_rar(self, archive_path: Path) -> Optional[List[ExtractedSubtitle]]:
        """解压 RAR 文件"""
        if not RAR_AVAILABLE:
            logger.error("[字幕解压] rarfile 库未安装，无法处理 RAR 文件")
            return None

        try:
            extract_dir = self.temp_dir / archive_path.stem
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)

            subtitle_files: List[ExtractedSubtitle] = []

            with rarfile.RarFile(archive_path, "r") as rf:
                for info in rf.infolist():
                    if info.is_dir():
                        continue

                    filename = info.filename
                    file_path = Path(filename)

                    # 只提取字幕文件
                    if file_path.suffix.lower() not in SUBTITLE_EXTENSIONS:
                        continue

                    # 保留文件夹结构解压
                    safe_path = Path(*file_path.parts) if file_path.parts else file_path
                    target_path = extract_dir / safe_path

                    # 创建父目录
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    with rf.open(info) as source:
                        with open(target_path, "wb") as target:
                            target.write(source.read())

                    subtitle_files.append(
                        ExtractedSubtitle(
                            temp_path=target_path,
                            archive_path=filename,
                            filename=file_path.name,
                        )
                    )

            logger.info(f"[字幕解压] RAR 解压成功，找到 {len(subtitle_files)} 个字幕文件")
            return subtitle_files

        except Exception as e:
            logger.error(f"[字幕解压] RAR 解压失败: {e}")
            return None

    def get_archive_structure(
        self, subtitle_files: List[ExtractedSubtitle]
    ) -> Dict[str, List[str]]:
        """
        获取压缩包的文件夹结构

        Returns:
            文件夹路径 -> 文件名列表 的映射
        """
        structure: Dict[str, List[str]] = {}
        for sub in subtitle_files:
            parent = str(Path(sub.archive_path).parent)
            if parent == ".":
                parent = "/"  # 根目录
            if parent not in structure:
                structure[parent] = []
            structure[parent].append(sub.filename)
        return structure

    def cleanup(self, archive_path: Path):
        """清理临时文件"""
        extract_dir = self.temp_dir / archive_path.stem
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
            logger.info(f"[字幕解压] 已清理临时目录: {extract_dir}")

    def get_extract_dir(self, archive_path: Path) -> Path:
        """获取解压目录"""
        return self.temp_dir / archive_path.stem
