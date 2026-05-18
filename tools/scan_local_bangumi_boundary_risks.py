from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


DEFAULT_PATHS = ("src/rename/case_agent",)
RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sample_id_literal", re.compile(r"\bsample[_-]?\d{3,5}\b", re.IGNORECASE)),
    ("target_locator_literal", re.compile(r"target://bangumi/\d{4,7}", re.IGNORECASE)),
    ("local_lf_literal", re.compile(r"\bLF\d{1,4}\b")),
    ("hardcoded_subject_id_comparison", re.compile(r"\bsubject_id\b\s*(?:==|!=|in)\s*[\[{(]?\s*\d{4,7}\b")),
)


def _git_diff(paths: list[str]) -> str:
    command = ["git", "diff", "--", *paths]
    return subprocess.check_output(command, text=True, encoding="utf-8", errors="replace")


def _git_untracked(paths: list[str]) -> list[str]:
    command = ["git", "ls-files", "--others", "--exclude-standard", "--", *paths]
    output = subprocess.check_output(command, text=True, encoding="utf-8", errors="replace")
    return [line.strip() for line in output.splitlines() if line.strip()]


def _iter_added_lines(diff_text: str) -> list[tuple[str, int, str]]:
    current_file = ""
    old_line = 0
    new_line = 0
    rows: list[tuple[str, int, str]] = []
    hunk_re = re.compile(r"@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        match = hunk_re.match(line)
        if match:
            old_line = int(match.group("old"))
            new_line = int(match.group("new"))
            continue
        if not current_file or line.startswith(("diff --git", "index ", "--- ")):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            rows.append((current_file, new_line, line[1:]))
            new_line += 1
            continue
        if line.startswith("-") and not line.startswith("---"):
            old_line += 1
            continue
        old_line += 1
        new_line += 1
    return rows


def _iter_file_lines(path: Path) -> list[tuple[str, int, str]]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [(path.as_posix(), index, line) for index, line in enumerate(text.splitlines(), start=1)]


def scan_diff(paths: list[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    rows = _iter_added_lines(_git_diff(paths))
    for untracked in _git_untracked(paths):
        rows.extend(_iter_file_lines(Path(untracked)))
    for file_path, line_no, text in rows:
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for kind, pattern in RISK_PATTERNS:
            if pattern.search(stripped):
                findings.append(
                    {
                        "risk": kind,
                        "file": file_path,
                        "line": line_no,
                        "text": stripped[:240],
                    }
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan changed Local->Bangumi fixed-layer code for sample/title/id hardcoding risk."
    )
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS), help="Paths to scan in git diff.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--fail-on-risk", action="store_true", help="Exit 1 when findings are present.")
    args = parser.parse_args()

    paths = [Path(path).as_posix() for path in args.paths]
    findings = scan_diff(paths)
    payload = {"paths": paths, "finding_count": len(findings), "findings": findings}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"boundary_risk_findings={len(findings)}")
        for item in findings:
            print(f"- {item['risk']} {item['file']}:{item['line']} {item['text']}")
    return 1 if args.fail_on_risk and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
