"""
字幕压缩包解压模块

支持 ZIP、RAR 和 7z 格式，处理中日文文件名编码问题。
保留压缩包内的文件夹结构信息。
"""

import hashlib
import shutil
import subprocess
import tempfile
import time
import zipfile
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Protocol, cast

from ..logger import logger


class _RarInfoProtocol(Protocol):
    filename: str

    def is_dir(self) -> bool: ...


class _RarArchiveProtocol(Protocol):
    def __enter__(self) -> "_RarArchiveProtocol": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...
    def infolist(self) -> list[_RarInfoProtocol]: ...
    def open(self, info: _RarInfoProtocol) -> BinaryIO: ...


class _RarModuleProtocol(Protocol):
    def RarFile(self, archive_path: Path, mode: str) -> _RarArchiveProtocol: ...


class _SevenZipArchiveProtocol(Protocol):
    def __enter__(self) -> "_SevenZipArchiveProtocol": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...
    def extractall(self, path: Path) -> None: ...


class _Py7zrModuleProtocol(Protocol):
    def SevenZipFile(self, archive_path: Path, mode: str) -> _SevenZipArchiveProtocol: ...

def _load_optional_module(module_name: str) -> object | None:
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


_rarfile_module = _load_optional_module("rarfile")
RAR_AVAILABLE = _rarfile_module is not None

_py7zr_module = _load_optional_module("py7zr")
SEVEN_Z_AVAILABLE = _py7zr_module is not None

# Bandizip CLI 可作为 Windows 下的 RAR 回退解压器
BANDIZIP_CANDIDATES = [
    Path("C:/Program Files/Bandizip/bz.exe"),
    Path("C:/Program Files (x86)/Bandizip/bz.exe"),
]

# 支持的字幕格式
SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt"}

# 压缩包后缀（用于嵌套压缩包递归解压判定）
_ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z"}

# 嵌套压缩包递归解压深度上限（防 zip bomb / 病毒套娃）
# TID=346 楼主包是 1 层套娃（外层 RAR 含 4 个内层 RAR），2 层足够覆盖正常场景
_MAX_NEST_DEPTH = 3

# 解压重试（对齐 acgrip download retry）：解压是文件系统操作，Windows 下并发
# 下载/配对时 extract_dir 可能因文件句柄未释放被占用，shutil.rmtree/mkdir 抛
# PermissionError(13)，或 rarfile/Bandizip 偶发失败。实测同一包首跑"解压失败"
# 重跑成功（0066 teekyu 5 个包 post-76298/76301/76920/76921/76927 首跑
# processor_failed、重跑同包 success；0088 COMPLETE BATCH 首跑 PermissionError、
# 重跑 success）→ 瞬时失败，重试可恢复。重试前清理该 archive 的 temp_dir 残留。
_EXTRACT_MAX_ATTEMPTS = 3
_EXTRACT_RETRY_BACKOFF_SECONDS = 2.0


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

    def _scoped_subdir(self, base_name: str, archive_path: Path) -> Path:
        """为一次解压生成与 archive_path 绑定的唯一 temp 子目录名。

        旧实现 extract_dir = temp / archive_path.stem，同 stem 包（如同一帖
        附件被多 subject 各选一次，下载到 sel_3/xx.rar 与 sel_4/xx.rar）stem
        相同 → 共用同一 extract_dir → 串行第二次 rmtree 时 Windows 文件句柄
        未释放抛 PermissionError(13)（实测复现：同 stem 串行连续 extract 第二次
        必 PermissionError，重试 3 次仍失败）。

        加 archive_path 绝对路径短哈希后缀：同路径（重复 extract 同一文件）哈希
        相同 → cleanup 幂等；不同路径（sel_3 vs sel_4）哈希不同 → 目录隔离，
        互不 rmtree。base_name 已含 stem，哈希只作区分用。
        """
        try:
            path_key = str(archive_path.resolve())
        except Exception:
            path_key = str(archive_path)
        suffix = hashlib.md5(path_key.encode("utf-8")).hexdigest()[:8]
        safe_base = base_name or "archive"
        # Windows 目录名长度上限 255，stem 可能很长（含特殊字符合集名），
        # 截断保安全 + 留哈希后缀空间。
        max_base = 200
        if len(safe_base) > max_base:
            safe_base = safe_base[:max_base]
        return self.temp_dir / f"{safe_base}_{suffix}"

    @staticmethod
    def _collect_extracted_subtitles(
        extract_dir: Path,
    ) -> List[ExtractedSubtitle]:
        subtitle_files: List[ExtractedSubtitle] = []
        for target_path in extract_dir.rglob("*"):
            if not target_path.is_file():
                continue
            if target_path.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue

            archive_member = str(target_path.relative_to(extract_dir)).replace(
                "\\", "/"
            )
            subtitle_files.append(
                ExtractedSubtitle(
                    temp_path=target_path,
                    archive_path=archive_member,
                    filename=target_path.name,
                )
            )
        return subtitle_files

    @staticmethod
    def _find_bandizip_executable() -> Optional[Path]:
        for candidate in BANDIZIP_CANDIDATES:
            if candidate.exists():
                return candidate
        return None

    def extract(self, archive_path: Path) -> Optional[List[ExtractedSubtitle]]:
        """
        解压压缩包或处理单个字幕文件，返回字幕文件列表（保留路径结构信息）

        支持嵌套压缩包（套娃包）：解完一层后若结果含内层 .rar/.zip/.7z，
        递归解到无内层压缩包或达到 _MAX_NEST_DEPTH 上限。
        例：acgrip TID=346 楼主包外层 RAR 含 4 个内层 RAR（每季一个），
        不递归则 extractor 只看到 4 个 .rar 报"0 字幕"，递归后解出 138 字幕。

        解压重试：文件系统操作（shutil.rmtree/mkdir/rarfile/Bandizip）在 Windows
        并发场景下偶发 PermissionError(13) 或返回 None，实测同包重跑可恢复
        （见 _EXTRACT_MAX_ATTEMPTS 注释）。对异常 + None 重试，空列表（解压成功
        但 0 字幕，可能套娃包）不重试——交给后续嵌套递归处理。

        Args:
            archive_path: 压缩包或字幕文件路径

        Returns:
            ExtractedSubtitle 列表，失败返回 None
        """
        if not archive_path.exists():
            logger.error(f"[字幕解压] 文件不存在: {archive_path}")
            return None

        # 单字幕文件不需要解压，直接处理（无重试必要）
        if archive_path.suffix.lower() in SUBTITLE_EXTENSIONS:
            return self._handle_direct_subtitle(archive_path)

        last_result: Optional[List[ExtractedSubtitle]] = None
        for attempt in range(1, _EXTRACT_MAX_ATTEMPTS + 1):
            # 重试前清理该 archive 的 temp_dir 残留（上一次失败可能留下半解
            # 目录或被占用文件，Windows 下 rmtree 偶发 PermissionError，重试
            # 间隔给文件句柄释放时间）。
            if attempt > 1:
                self._cleanup_archive_temp(archive_path)
                time.sleep(_EXTRACT_RETRY_BACKOFF_SECONDS)
                logger.info(
                    f"[字幕解压] 第 {attempt}/{_EXTRACT_MAX_ATTEMPTS} 次重试: "
                    f"{archive_path.name}"
                )
            try:
                result = self._extract_once(archive_path)
            except (PermissionError, OSError) as exc:
                logger.warning(
                    f"[字幕解压] 解压异常 (attempt {attempt}/"
                    f"{_EXTRACT_MAX_ATTEMPTS}): {exc!r}"
                )
                last_result = None
                continue
            # None = 解压彻底失败（rarfile/Bandizip 报错），重试可能恢复
            if result is None:
                logger.warning(
                    f"[字幕解压] 解压返回 None (attempt {attempt}/"
                    f"{_EXTRACT_MAX_ATTEMPTS}): {archive_path.name}"
                )
                last_result = None
                continue
            # 成功（含空列表 = 解压成功 0 字幕，交给嵌套递归）→ 立即返回
            return result

        logger.error(
            f"[字幕解压] 解压重试 {_EXTRACT_MAX_ATTEMPTS} 次仍失败: "
            f"{archive_path.name}"
        )
        return last_result

    def _cleanup_archive_temp(self, archive_path: Path) -> None:
        """清理该 archive 在 temp_dir 的解压目录残留（重试前调用）。

        精确清理 _scoped_subdir(stem, archive_path) 对应的目录（同路径哈希
        相同，幂等）。_extract_rar/_extract_zip 写入的就是这个目录。
        """
        stem = archive_path.stem
        if not stem:
            return
        extract_dir = self._scoped_subdir(stem, archive_path)
        try:
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception:
            # 清理本身失败不影响重试（extract_once 内还会 rmtree/mkdir）
            pass

    def _extract_once(
        self, archive_path: Path
    ) -> Optional[List[ExtractedSubtitle]]:
        """单次解压尝试（extract 的核心逻辑，不含重试编排）。"""
        suffix = archive_path.suffix.lower()

        if suffix == ".zip":
            primary = self._extract_zip(archive_path)
        elif suffix == ".rar":
            primary = self._extract_rar(archive_path)
        elif suffix == ".7z":
            primary = self._extract_7z(archive_path)
        else:
            logger.error(f"[字幕解压] 不支持的格式: {suffix}")
            return None

        if primary is None:
            return None

        # 外层已解出字幕 → 直接返回（绝大多数字幕包到此结束）
        if primary:
            return primary

        # 外层 0 字幕 → 尝试递归解嵌套压缩包（套娃包）
        nested = self._extract_nested_archives(archive_path, depth=1)
        if nested:
            logger.info(
                f"[字幕解压] 嵌套压缩包递归解压成功，"
                f"外层 0 字幕 → 递归解出 {len(nested)} 个字幕"
            )
            return nested

        logger.info("[字幕解压] 外层与内层均无字幕文件")
        return primary

    def _extract_nested_archives(
        self, outer_archive: Path, depth: int
    ) -> List[ExtractedSubtitle]:
        """
        递归解压外层压缩包内的内层压缩包（套娃包）。

        纯机械操作：扫描外层解压目录里的 .rar/.zip/.7z，逐个解压，
        收集所有层级解出的字幕。深度上限 _MAX_NEST_DEPTH 防 zip bomb。

        例：acgrip TID=346 楼主包 = 外层 RAR(4 内层 RAR)，每内层 RAR 含
        一季的 .ass。不递归只看到 4 个 .rar，递归后 138 字幕。
        """
        if depth > _MAX_NEST_DEPTH:
            logger.warning(
                f"[字幕解压] 嵌套深度超上限 {_MAX_NEST_DEPTH}，停止递归"
            )
            return []

        # 外层解压目录名 = _scoped_subdir(stem, outer_archive)，与 _extract_rar/
        # _extract_zip 写入的 extract_dir 一致（同路径哈希相同）。嵌套层用
        # _nest{depth} 后缀隔离（内层 archive path 不同 → 哈希不同，天然隔离）。
        if depth == 1:
            extract_dir = self._scoped_subdir(outer_archive.stem, outer_archive)
        else:
            extract_dir = self._scoped_subdir(
                outer_archive.stem + f"_nest{depth - 1}", outer_archive
            )
        if not extract_dir.exists():
            return []

        all_subtitles: List[ExtractedSubtitle] = []
        for inner in extract_dir.rglob("*"):
            if not inner.is_file():
                continue
            if inner.suffix.lower() not in _ARCHIVE_SUFFIXES:
                continue

            inner_subs = self._extract_one_to_nest_dir(inner, depth)
            if inner_subs:
                all_subtitles.extend(inner_subs)
            else:
                # 这层解出 0 字幕，可能还有更内层，继续递归
                deeper = self._extract_nested_archives(inner, depth + 1)
                all_subtitles.extend(deeper)

        return all_subtitles

    def _extract_one_to_nest_dir(
        self, archive_path: Path, depth: int
    ) -> Optional[List[ExtractedSubtitle]]:
        """
        嵌套解压用：解单个内层压缩包到独立 _nest{depth} 目录（防 stem 撞）。
        优先用 Bandizip CLI 直接解到目标目录；不可用时回退 _extract_*。
        """
        suffix = archive_path.suffix.lower()
        nest_dir = self._scoped_subdir(
            archive_path.stem + f"_nest{depth}", archive_path
        )
        if nest_dir.exists():
            shutil.rmtree(nest_dir, ignore_errors=True)

        bandizip = self._find_bandizip_executable()
        if bandizip is not None and suffix in _ARCHIVE_SUFFIXES:
            try:
                nest_dir.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    [
                        str(bandizip),
                        "x",
                        "-y",
                        "-aoa",
                        f"-o:{nest_dir}",
                        str(archive_path),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    check=False,
                )
                if result.returncode == 0:
                    return self._collect_extracted_subtitles(nest_dir)
            except Exception as e:
                logger.warning(
                    f"[字幕解压] 嵌套解压失败 (depth={depth}): "
                    f"{archive_path.name} - {e}"
                )
                return None

        # Bandizip 不可用：回退 _extract_*（stem 撞风险低，因内层包名通常不同）
        if suffix == ".zip":
            return self._extract_zip(archive_path)
        if suffix == ".rar":
            return self._extract_rar(archive_path)
        if suffix == ".7z":
            return self._extract_7z(archive_path)
        return None

    def _handle_direct_subtitle(
        self, subtitle_path: Path
    ) -> Optional[List[ExtractedSubtitle]]:
        """处理直接上传的字幕文件（非压缩包）"""
        try:
            # 复制到临时目录
            extract_dir = self._scoped_subdir(
                f"direct_{subtitle_path.stem}", subtitle_path
            )
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
            extract_dir = self._scoped_subdir(archive_path.stem, archive_path)
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

                    # 提取字幕文件；内层压缩包（套娃包）也一并写出供递归解压
                    file_path = Path(filename)
                    member_suffix = file_path.suffix.lower()
                    if member_suffix not in SUBTITLE_EXTENSIONS:
                        if member_suffix not in _ARCHIVE_SUFFIXES:
                            continue
                        # 内层压缩包：写到 extract_dir 供 _extract_nested_archives 扫到
                        safe_path = (
                            Path(*file_path.parts) if file_path.parts else file_path
                        )
                        target_path = extract_dir / safe_path
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as source:
                            with open(target_path, "wb") as target:
                                target.write(source.read())
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
        extract_dir = self._scoped_subdir(archive_path.stem, archive_path)
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)

        if RAR_AVAILABLE and _rarfile_module is not None:
            try:
                subtitle_files: List[ExtractedSubtitle] = []

                rarfile_module = cast(_RarModuleProtocol, _rarfile_module)
                with rarfile_module.RarFile(archive_path, "r") as rf:
                    for info in rf.infolist():
                        if info.is_dir():
                            continue

                        filename = info.filename
                        file_path = Path(filename)
                        member_suffix = file_path.suffix.lower()

                        # 提取字幕文件；内层压缩包（套娃包）也写出供递归解压
                        if member_suffix not in SUBTITLE_EXTENSIONS:
                            if member_suffix not in _ARCHIVE_SUFFIXES:
                                continue
                            # 内层压缩包：写到 extract_dir 供 _extract_nested_archives 扫到
                            safe_path = (
                                Path(*file_path.parts) if file_path.parts else file_path
                            )
                            target_path = extract_dir / safe_path
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            with rf.open(info) as source:
                                with open(target_path, "wb") as target:
                                    target.write(source.read())
                            continue

                        # 保留文件夹结构解压
                        safe_path = (
                            Path(*file_path.parts) if file_path.parts else file_path
                        )
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

                logger.info(
                    f"[字幕解压] RAR 解压成功，找到 {len(subtitle_files)} 个字幕文件"
                )
                return subtitle_files
            except Exception as e:
                logger.warning(f"[字幕解压] rarfile 解压失败，尝试回退 Bandizip: {e}")

        bandizip = self._find_bandizip_executable()
        if bandizip is None:
            logger.error(
                "[字幕解压] rarfile 不可用，且未找到 Bandizip CLI，无法处理 RAR 文件"
            )
            return None

        try:
            result = subprocess.run(
                [
                    str(bandizip),
                    "x",
                    "-y",
                    "-aoa",
                    f"-o:{extract_dir}",
                    str(archive_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
            if result.returncode != 0:
                logger.error(
                    "[字幕解压] Bandizip 解压 RAR 失败: %s",
                    (result.stderr or result.stdout or "未知错误").strip(),
                )
                return None

            subtitle_files = self._collect_extracted_subtitles(extract_dir)
            logger.info(
                "[字幕解压] Bandizip 解压 RAR 成功，找到 %s 个字幕文件",
                len(subtitle_files),
            )
            return subtitle_files
        except Exception as e:
            logger.error(f"[字幕解压] Bandizip 解压 RAR 失败: {e}")
            return None

    def _extract_7z(self, archive_path: Path) -> Optional[List[ExtractedSubtitle]]:
        """解压 7z 文件"""
        if not SEVEN_Z_AVAILABLE or _py7zr_module is None:
            logger.error("[字幕解压] py7zr 库未安装，无法处理 7z 文件")
            return None

        try:
            extract_dir = self._scoped_subdir(archive_path.stem, archive_path)
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)

            py7zr_module = cast(_Py7zrModuleProtocol, _py7zr_module)
            with py7zr_module.SevenZipFile(archive_path, "r") as zf:
                zf.extractall(path=extract_dir)

            subtitle_files = self._collect_extracted_subtitles(extract_dir)

            logger.info(f"[字幕解压] 7z 解压成功，找到 {len(subtitle_files)} 个字幕文件")
            return subtitle_files

        except Exception as e:
            logger.error(f"[字幕解压] 7z 解压失败: {e}")
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
        extract_dir = self._scoped_subdir(archive_path.stem, archive_path)
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
            logger.info(f"[字幕解压] 已清理临时目录: {extract_dir}")

    def get_extract_dir(self, archive_path: Path) -> Path:
        """获取解压目录"""
        return self._scoped_subdir(archive_path.stem, archive_path)
