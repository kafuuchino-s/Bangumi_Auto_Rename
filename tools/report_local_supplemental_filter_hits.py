from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.rename.local_supplemental_filter import classify_local_video_supplemental


VIDEO_SUFFIXES = {'.mkv', '.mp4', '.m2ts'}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    if not isinstance(payload, dict):
        raise ValueError(f'raw sample must be a JSON object: {path}')
    return payload


def _norm_path(value: object) -> str:
    return str(value or '').replace('\\', '/').strip()


def _iter_sample_paths(raw_root: Path) -> list[Path]:
    if raw_root.is_file():
        return [raw_root]
    return sorted(path for path in raw_root.rglob('*.json') if path.is_file())


def build_report(raw_root: Path, *, min_sample_count: int = 2, sample_limit: int = 8) -> dict[str, Any]:
    raw_root = raw_root.resolve()
    sample_paths = _iter_sample_paths(raw_root)
    rule_file_counts: dict[str, int] = defaultdict(int)
    rule_samples: dict[str, set[str]] = defaultdict(set)
    rule_examples: dict[str, list[str]] = defaultdict(list)
    video_count = 0
    filtered_video_count = 0

    for sample_path in sample_paths:
        payload = _load_json(sample_path)
        sample_key = _sample_key(raw_root, sample_path)
        for item in payload.get('files') or []:
            if not isinstance(item, dict):
                continue
            source_path = _norm_path(item.get('path') or item.get('relative_path'))
            if Path(source_path).suffix.lower() not in VIDEO_SUFFIXES:
                continue
            video_count += 1
            decision = classify_local_video_supplemental(source_path, is_video=True)
            if not decision.is_supplemental:
                continue
            filtered_video_count += 1
            rule_id = decision.rule_id or 'unknown'
            rule_file_counts[rule_id] += 1
            rule_samples[rule_id].add(sample_key)
            if len(rule_examples[rule_id]) < sample_limit:
                rule_examples[rule_id].append(source_path)

    rules = []
    for rule_id, file_count in rule_file_counts.items():
        sample_count = len(rule_samples[rule_id])
        rules.append({
            'rule_id': rule_id,
            'file_count': file_count,
            'sample_count': sample_count,
            'low_sample_coverage': sample_count < min_sample_count,
            'examples': rule_examples[rule_id],
        })
    rules.sort(key=lambda item: (-int(item['sample_count']), -int(item['file_count']), str(item['rule_id'])))
    return {
        'raw_root': str(raw_root),
        'sample_count': len(sample_paths),
        'video_count': video_count,
        'filtered_video_count': filtered_video_count,
        'min_sample_count': min_sample_count,
        'low_sample_coverage_rule_count': sum(1 for rule in rules if rule['low_sample_coverage']),
        'rules': rules,
    }


def _sample_key(raw_root: Path, sample_path: Path) -> str:
    try:
        return sample_path.relative_to(raw_root).as_posix()
    except ValueError:
        return sample_path.as_posix()


def _print_table(report: dict[str, Any]) -> None:
    print(f"raw_root: {report['raw_root']}")
    print(f"samples: {report['sample_count']}  videos: {report['video_count']}  filtered: {report['filtered_video_count']}")
    print(f"min_sample_count: {report['min_sample_count']}  low_coverage_rules: {report['low_sample_coverage_rule_count']}")
    print()
    print(f"{'rule_id':36} {'files':>7} {'samples':>7} low")
    print(f"{'-' * 36} {'-' * 7} {'-' * 7} ---")
    for rule in report['rules']:
        low = 'yes' if rule['low_sample_coverage'] else ''
        print(f"{str(rule['rule_id'])[:36]:36} {int(rule['file_count']):7d} {int(rule['sample_count']):7d} {low}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Report sample-pool hit distribution for local supplemental hard-filter rules.')
    parser.add_argument('--raw-root', default='tests/sample_pool/raw')
    parser.add_argument('--min-sample-count', type=int, default=2)
    parser.add_argument('--sample-limit', type=int, default=8)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--output', default='')
    parser.add_argument('--fail-low-coverage', action='store_true')
    args = parser.parse_args(argv)

    report = build_report(Path(args.raw_root), min_sample_count=max(1, args.min_sample_count), sample_limit=max(0, args.sample_limit))
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_table(report)
    if args.fail_low_coverage and report['low_sample_coverage_rule_count']:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
