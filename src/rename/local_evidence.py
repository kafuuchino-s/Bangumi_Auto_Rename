from dataclasses import dataclass, replace
from pathlib import Path

from .local_fact_surface import LocalFactSurface, build_local_fact_surface
from .local_supplemental_filter import classify_local_video_supplemental
from .utils import VIDEO_SUFFIX


@dataclass(frozen=True)
class LocalFileEvidence:
    """单个文件的中性本地事实。

    本层只描述文件系统和硬预过滤结果。文件名里的语义 cue、编号、
    SxxEyy/#12 这类结构解释必须由 LocalPackageAnalysis AI 产出，
    不能由固定层 regex 作为 AI-facing 证据提供。
    """

    file_id: str
    relative_path: str
    name: str
    suffix: str
    is_video: bool
    is_supplemental_candidate: bool
    is_main_video_candidate: bool
    size_bytes: int | None = None


@dataclass(frozen=True)
class LocalEvidence:
    """Rename proposal 链路的本地事实输入。

    本结构只描述文件系统事实和硬预过滤结果，不做作品身份、route、season 结论。
    """

    root_name: str
    root_path: str
    files: list[LocalFileEvidence]
    video_count: int
    main_video_count: int
    supplemental_candidate_count: int
    directory_structure: list[str]
    fact_surface: LocalFactSurface | None = None


def build_local_evidence(root: Path, ordered_files: list[Path] | None = None) -> LocalEvidence:
    """从文件系统构建中性本地证据。"""

    root = Path(root)
    if ordered_files is not None:
        evidence_root = root if root.is_dir() else root.parent
        root_name = root.name
        file_paths = list(ordered_files)
    elif root.is_file():
        file_paths = [root]
        evidence_root = root.parent
        root_name = root.name
    else:
        evidence_root = root
        root_name = root.name
        file_paths = sorted(path for path in root.rglob('*') if path.is_file())

    files: list[LocalFileEvidence] = []
    directories: set[str] = set()
    actual_paths: dict[str, Path] = {}
    for index, file_path in enumerate(file_paths, start=1):
        try:
            relative_path = file_path.relative_to(evidence_root).as_posix()
        except ValueError:
            relative_path = file_path.name
        directories.update(Path(relative_path).parts[:-1])
        file_evidence = _build_file_evidence(index, relative_path, file_path)
        files.append(file_evidence)
        actual_paths[file_evidence.file_id] = file_path
        actual_paths[file_evidence.relative_path] = file_path

    video_count = sum(1 for file in files if file.is_video)
    main_video_count = sum(1 for file in files if file.is_main_video_candidate)
    supplemental_count = sum(1 for file in files if file.is_supplemental_candidate)

    evidence = LocalEvidence(
        root_name=root_name,
        root_path=str(root),
        files=files,
        video_count=video_count,
        main_video_count=main_video_count,
        supplemental_candidate_count=supplemental_count,
        directory_structure=sorted(directories),
    )
    return replace(evidence, fact_surface=build_local_fact_surface(evidence, actual_paths=actual_paths))


def _build_file_evidence(
    index: int,
    relative_path: str,
    file_path: Path,
) -> LocalFileEvidence:
    suffix = file_path.suffix.casefold()
    is_video = suffix in {suffix.casefold() for suffix in VIDEO_SUFFIX}
    supplemental = classify_local_video_supplemental(relative_path, is_video=is_video)
    return LocalFileEvidence(
        file_id=f'file_{index:03d}',
        relative_path=relative_path,
        name=file_path.name,
        suffix=suffix,
        is_video=is_video,
        is_supplemental_candidate=bool(supplemental.is_supplemental),
        is_main_video_candidate=is_video and not supplemental.is_supplemental,
        size_bytes=_safe_file_size(file_path),
    )


def _safe_file_size(file_path: Path) -> int | None:
    try:
        return int(file_path.stat().st_size)
    except OSError:
        return None
