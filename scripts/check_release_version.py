from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG_PATTERN = re.compile(r"v(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")


def load_versions() -> dict[str, str]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        backend = str(tomllib.load(file)["project"]["version"])
    frontend = str(
        json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))[
            "version"
        ]
    )
    frontend_lock = str(
        json.loads(
            (ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
        )["version"]
    )
    return {
        "pyproject.toml": backend,
        "frontend/package.json": frontend,
        "frontend/package-lock.json": frontend_lock,
    }


def release_tag(explicit_tag: str | None) -> str | None:
    if explicit_tag:
        return explicit_tag
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        return os.environ.get("GITHUB_REF_NAME")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Check release version consistency.")
    parser.add_argument("--tag", help="Release tag to compare, for example v0.3.0")
    args = parser.parse_args()

    versions = load_versions()
    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        details = ", ".join(f"{path}={version}" for path, version in versions.items())
        raise SystemExit(f"Version mismatch: {details}")

    version = unique_versions.pop()
    tag = release_tag(args.tag)
    if tag:
        match = TAG_PATTERN.fullmatch(tag)
        if not match:
            raise SystemExit(f"Release tag must use vX.Y.Z format: {tag}")
        if match.group("version") != version:
            raise SystemExit(f"Release tag {tag} does not match project version {version}")

    suffix = f"; tag={tag}" if tag else ""
    print(f"release version: {version}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
