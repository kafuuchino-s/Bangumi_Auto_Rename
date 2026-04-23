from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


VIDEO_SUFFIXES = {
    ".mkv",
    ".mp4",
    ".avi",
    ".m2ts",
    ".mov",
    ".wmv",
    ".flv",
    ".ts",
}


def slugify(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized.lower() or "sample"


def iter_source_entries(source_root: Path) -> Iterable[Path]:
    for item in sorted(source_root.iterdir(), key=lambda p: p.name.lower()):
        if item.is_dir() or item.is_file():
            yield item


def collect_files(target: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    if target.is_file():
        files.append({
            "path": target.name,
            "size": target.stat().st_size,
        })
        return files

    for item in sorted(target.rglob("*"), key=lambda p: str(p).lower()):
        if not item.is_file():
            continue
        relative = item.relative_to(target).as_posix()
        files.append({
            "path": relative,
            "size": item.stat().st_size,
        })
    return files


def has_video(files: list[dict[str, object]]) -> bool:
    for file_info in files:
        path_value = str(file_info.get("path", ""))
        if Path(path_value).suffix.lower() in VIDEO_SUFFIXES:
            return True
    return False


def build_sample_payload(target: Path) -> dict[str, object]:
    return {
        "root_name": target.name,
        "files": collect_files(target),
    }


def write_sample(output_dir: Path, index: int, payload: dict[str, object]) -> Path:
    root_name = str(payload["root_name"])
    file_name = f"sample_{index:04d}_{slugify(root_name)}.json"
    output_path = output_dir / file_name
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate raw sample pool JSON files from source directories")
    parser.add_argument("source", help="Source root directory to scan")
    parser.add_argument("output", help="Output directory under tests/sample_pool/raw/... ")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of samples to generate (0 = unlimited)")
    args = parser.parse_args()

    source_root = Path(args.source)
    output_dir = Path(args.output)
    if not source_root.exists() or not source_root.is_dir():
        raise SystemExit(f"Source root does not exist or is not a directory: {source_root}")

    generated = 0
    for entry in iter_source_entries(source_root):
        payload = build_sample_payload(entry)
        files = payload["files"]
        if not isinstance(files, list) or not files or not has_video(files):
            continue
        generated += 1
        path = write_sample(output_dir, generated, payload)
        print(f"[generated] {path}")
        if args.limit and generated >= args.limit:
            break

    print(f"Generated {generated} raw sample(s) into {output_dir}")


if __name__ == "__main__":
    main()
