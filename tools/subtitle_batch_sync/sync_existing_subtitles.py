from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.subtitle.batch_sync import BatchSyncResult, SubtitleBatchSyncer

SIMPLIFIED_CHINESE_MARKERS = {
    "chs",
    "sc",
    "gb",
    "简",
    "简体",
    "简中",
    "zh-cn",
    "zh_cn",
    "zh.hans",
    "zh-hans",
    "default",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量对已有外挂字幕执行 ffsubsync 调轴")
    parser.add_argument(
        "--root",
        type=str,
        required=True,
        help="要递归扫描的视频根目录",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描并输出命中结果，不实际覆盖字幕",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并发 worker 数，默认 1",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        type=str,
        default="",
        help="可选：ffmpeg 所在 bin 目录，会临时追加到 PATH",
    )
    return parser.parse_args()


def is_likely_simplified_chinese_subtitle(subtitle_path: Path) -> bool:
    stem = subtitle_path.stem.casefold()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", stem)
    tokens = {token for token in normalized.split() if token}
    return bool(tokens & SIMPLIFIED_CHINESE_MARKERS)


def run_simplified_chinese_sync(
    root: Path,
    dry_run: bool = False,
    workers: int = 1,
    syncer: Optional[SubtitleBatchSyncer] = None,
) -> BatchSyncResult:
    syncer = syncer or SubtitleBatchSyncer()
    original_find_sidecars = syncer._find_sidecar_subtitles

    def _find_simplified_sidecars(video_path: Path):
        return [
            subtitle_path
            for subtitle_path in original_find_sidecars(video_path)
            if is_likely_simplified_chinese_subtitle(subtitle_path)
        ]

    syncer._find_sidecar_subtitles = _find_simplified_sidecars  # type: ignore[attr-defined]
    return syncer.sync_tree(root=root, dry_run=dry_run, workers=workers)


def main() -> int:
    args = parse_args()
    root = Path(args.root)

    ffmpeg_bin = str(args.ffmpeg_bin or "").strip()
    if ffmpeg_bin:
        os.environ["PATH"] = ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

    result = run_simplified_chinese_sync(
        root=root,
        dry_run=args.dry_run,
        workers=args.workers,
    )

    print(f"root={result.root}")
    print(f"dry_run={result.dry_run}")
    print(f"workers={result.workers}")
    print(f"scanned_videos={result.scanned_videos}")
    print(f"videos_with_subtitles={result.videos_with_subtitles}")
    print(f"subtitle_candidates={result.subtitle_candidates}")
    print(f"planned={result.planned}")
    print(f"attempted={result.attempted}")
    print(f"success={result.success}")
    print(f"skipped={result.skipped}")
    print(f"failed={result.failed}")

    if result.items:
        print("items:")
        for item in result.items:
            reason = f" ({item.reason})" if item.reason else ""
            print(f"- [{item.status}] {item.subtitle_path}{reason}")

    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
