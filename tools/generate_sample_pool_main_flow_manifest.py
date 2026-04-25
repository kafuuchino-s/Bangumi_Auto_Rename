from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = PROJECT_ROOT / "tests" / "sample_pool" / "raw"
DEFAULT_OUTPUT = PROJECT_ROOT / "tests" / "sample_pool" / "manifest" / "manifest_main_flow_full.json"


def rel(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(resolved).replace("\\", "/")


def build_entry(path: Path) -> dict[str, Any]:
    raw_bucket = path.parent.name
    sample_id = path.stem
    tags = [f"raw_{raw_bucket}", "main_flow_preview"]
    if raw_bucket == "movie":
        tags.append("movie_resolution")
    if raw_bucket == "tv":
        tags.append("tv_strict_mapping")
    return {
        "sample_id": sample_id,
        "sample_json": rel(path),
        "check": False,
        "anchor": True,
        "tags": tags,
    }


def build_manifest(raw_root: Path = RAW_ROOT) -> dict[str, Any]:
    raw_files = sorted(raw_root.glob("*/*.json"), key=lambda item: rel(item).casefold())
    return {
        "manifest_version": "2026-04-24-main-flow-full",
        "entries": [build_entry(path) for path in raw_files],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a full raw-sample manifest for main-flow preview reruns.")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = build_manifest(args.raw_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": rel(args.output), "entry_count": len(payload["entries"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
