from __future__ import annotations

import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..logger import logger
from ..rename.utils import VIDEO_SUFFIX
from .extractor import SUBTITLE_EXTENSIONS
from .syncer import FFsubsyncRunner

TEXT_SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt", ".vtt"}
IMAGE_SUBTITLE_EXTENSIONS = SUBTITLE_EXTENSIONS - TEXT_SUBTITLE_EXTENSIONS


@dataclass
class BatchSyncItemResult:
    video_path: Path
    subtitle_path: Path
    status: str
    reason: str = ""
    synced_to: Optional[Path] = None


@dataclass
class BatchSyncResult:
    root: Path
    dry_run: bool
    workers: int = 1
    scanned_videos: int = 0
    videos_with_subtitles: int = 0
    subtitle_candidates: int = 0
    planned: int = 0
    attempted: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    items: List[BatchSyncItemResult] = field(default_factory=list)


@dataclass
class _SyncJob:
    video_path: Path
    subtitle_path: Path
    output_dir: Path


class SubtitleBatchSyncer:
    def __init__(self, runner: Optional[FFsubsyncRunner] = None) -> None:
        self.runner = runner or FFsubsyncRunner()

    def sync_tree(
        self,
        root: Path,
        dry_run: bool = False,
        workers: int = 1,
    ) -> BatchSyncResult:
        target_root = Path(root)
        if not target_root.exists():
            raise ValueError(f"目录不存在: {target_root}")
        if not target_root.is_dir():
            raise ValueError(f"不是目录: {target_root}")

        max_workers = max(1, int(workers or 1))
        result = BatchSyncResult(
            root=target_root,
            dry_run=dry_run,
            workers=max_workers,
        )
        logger.info(
            f"[字幕批量调轴] 开始扫描: {target_root} "
            f"(dry_run={dry_run}, workers={max_workers})"
        )

        with tempfile.TemporaryDirectory(
            prefix="bangumi_batch_sync_"
        ) as temp_dir_raw:
            temp_root = Path(temp_dir_raw)
            jobs: List[_SyncJob] = []

            for video_path in self._iter_video_files(target_root):
                result.scanned_videos += 1
                subtitle_paths = self._find_sidecar_subtitles(video_path)
                if not subtitle_paths:
                    continue

                result.videos_with_subtitles += 1
                for subtitle_path in subtitle_paths:
                    result.subtitle_candidates += 1
                    ext = subtitle_path.suffix.lower()
                    if ext in IMAGE_SUBTITLE_EXTENSIONS:
                        self._record_item(
                            result,
                            BatchSyncItemResult(
                                video_path=video_path,
                                subtitle_path=subtitle_path,
                                status="skipped",
                                reason=f"暂不支持图形字幕: {ext}",
                            ),
                        )
                        logger.info(
                            f"[字幕批量调轴] 跳过图形字幕: {subtitle_path}"
                        )
                        continue

                    result.planned += 1
                    if dry_run:
                        self._record_item(
                            result,
                            BatchSyncItemResult(
                                video_path=video_path,
                                subtitle_path=subtitle_path,
                                status="dry_run",
                                reason="dry-run",
                            ),
                        )
                        logger.info(
                            f"[字幕批量调轴] dry-run 命中: {subtitle_path}"
                        )
                        continue

                    output_dir = temp_root / f"item_{len(jobs) + 1:05d}"
                    output_dir.mkdir(parents=True, exist_ok=True)
                    jobs.append(
                        _SyncJob(
                            video_path=video_path,
                            subtitle_path=subtitle_path,
                            output_dir=output_dir,
                        )
                    )

            if jobs and not dry_run:
                result.attempted = len(jobs)
                if max_workers == 1:
                    for job in jobs:
                        self._record_item(result, self._run_sync_job(job))
                else:
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_job = {
                            executor.submit(self._run_sync_job, job): job
                            for job in jobs
                        }
                        for future in as_completed(future_to_job):
                            job = future_to_job[future]
                            try:
                                item = future.result()
                            except Exception as e:
                                logger.exception(
                                    "[字幕批量调轴] 并发任务异常: %s",
                                    job.subtitle_path,
                                )
                                item = BatchSyncItemResult(
                                    video_path=job.video_path,
                                    subtitle_path=job.subtitle_path,
                                    status="failed",
                                    reason=f"执行异常: {e}",
                                )
                            self._record_item(result, item)

        result.items.sort(
            key=lambda item: (
                str(item.video_path).casefold(),
                str(item.subtitle_path).casefold(),
                item.status,
            )
        )
        logger.info(
            f"[字幕批量调轴] 扫描完成: 视频={result.scanned_videos}, "
            f"命中视频={result.videos_with_subtitles}, "
            f"字幕={result.subtitle_candidates}, 计划={result.planned}, "
            f"尝试={result.attempted}, 成功={result.success}, "
            f"跳过={result.skipped}, 失败={result.failed}, "
            f"workers={result.workers}"
        )
        return result

    def _run_sync_job(self, job: _SyncJob) -> BatchSyncItemResult:
        sync_result = self.runner.sync_subtitle(
            video_path=job.video_path,
            subtitle_path=job.subtitle_path,
            output_dir=job.output_dir,
        )

        if not sync_result.success or not sync_result.output_path:
            reason = sync_result.reason or "字幕调轴失败"
            logger.warning(
                f"[字幕批量调轴] 调轴失败: {job.subtitle_path} ({reason})"
            )
            return BatchSyncItemResult(
                video_path=job.video_path,
                subtitle_path=job.subtitle_path,
                status="failed",
                reason=reason,
            )

        try:
            sync_result.output_path.replace(job.subtitle_path)
        except Exception as e:
            reason = f"覆盖原字幕失败: {e}"
            logger.error(
                f"[字幕批量调轴] 覆盖失败: {job.subtitle_path} ({reason})"
            )
            return BatchSyncItemResult(
                video_path=job.video_path,
                subtitle_path=job.subtitle_path,
                status="failed",
                reason=reason,
            )

        logger.info(f"[字幕批量调轴] 覆盖成功: {job.subtitle_path}")
        return BatchSyncItemResult(
            video_path=job.video_path,
            subtitle_path=job.subtitle_path,
            status="success",
            synced_to=job.subtitle_path,
        )

    @staticmethod
    def _record_item(
        result: BatchSyncResult,
        item: BatchSyncItemResult,
    ) -> None:
        result.items.append(item)
        if item.status == "success":
            result.success += 1
        elif item.status == "skipped":
            result.skipped += 1
        elif item.status == "failed":
            result.failed += 1

    def _iter_video_files(self, root: Path) -> List[Path]:
        video_files = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIX
        ]
        return sorted(video_files)

    def _find_sidecar_subtitles(self, video_path: Path) -> List[Path]:
        matched: dict[str, Path] = {}
        for ext in sorted(SUBTITLE_EXTENSIONS):
            for candidate in sorted(
                video_path.parent.glob(f"{video_path.stem}*{ext}")
            ):
                if not candidate.is_file():
                    continue
                if not self._is_sidecar_subtitle(video_path, candidate):
                    continue
                matched.setdefault(str(candidate).casefold(), candidate)
        return sorted(matched.values())

    @staticmethod
    def _is_sidecar_subtitle(video_path: Path, subtitle_path: Path) -> bool:
        if subtitle_path.suffix.lower() not in SUBTITLE_EXTENSIONS:
            return False

        pattern = re.compile(
            rf"^{re.escape(video_path.stem)}(?:$|[.\s_\-\[\(])",
            re.IGNORECASE,
        )
        return bool(pattern.match(subtitle_path.stem))
