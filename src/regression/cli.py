from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest import DEFAULT_MANIFEST_PATH
from .runner import DEFAULT_ARTIFACTS_ROOT, DEFAULT_BASELINE_ROOT, run_rename_regression
from .models import MODE_CHOICES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Rename regression runner (public modes: check, update-baseline, full)',
        epilog='Canonical modes: check, update-baseline, full.',
    )
    parser.add_argument(
        '--mode',
        choices=list(MODE_CHOICES),
        default='check',
        help='Public modes: check, update-baseline, full.',
    )
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument('--baseline-root', type=Path, default=DEFAULT_BASELINE_ROOT)
    parser.add_argument('--artifacts-root', type=Path, default=DEFAULT_ARTIFACTS_ROOT)
    parser.add_argument('--sample-id', type=str, default=None)
    parser.add_argument('--max-samples', type=int, default=None)
    parser.add_argument(
        '--changed-path',
        action='append',
        default=None,
        help='Explicit changed path to use for protected-sample inference. Repeatable.',
    )
    parser.add_argument(
        '--no-expand-protected-samples',
        action='store_true',
        help='Disable automatic protected-sample scope expansion for debugging.',
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = run_rename_regression(
        mode=args.mode,
        manifest=args.manifest,
        baseline_root=args.baseline_root,
        artifacts_root=args.artifacts_root,
        sample_id=args.sample_id,
        max_samples=args.max_samples,
        expand_protected_samples_enabled=not args.no_expand_protected_samples,
        changed_paths=args.changed_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result['exit_code'])


if __name__ == '__main__':
    raise SystemExit(main())
