"""批量生成 BGM->TMDB 最终重命名 dry-run 计划并汇总质量。"""
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config.config_manager import cm
from src.rename.bgm_to_tmdb import run_bgm_to_tmdb_rename_plan_dry_run


RESULT_ROOT = Path(
    'tests/sample_pool/generated/bgm_to_tmdb_bridge_gate_20260615_123631_710'
)


def load_result(path: Path) -> dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def find_case_input_path(data: dict[str, Any]) -> Path | None:
    cmd = data.get('sample_runner', {}).get('runtime_command', [])
    for i, arg in enumerate(cmd):
        if arg == '--input' and i + 1 < len(cmd):
            return Path(cmd[i + 1])
    return None


def extract_bridge_input(case_input_path: Path) -> dict[str, Any] | None:
    try:
        with open(case_input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None
    return data.get('context', {}).get('bridge_input')


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    result_dir = repo_root / RESULT_ROOT
    if not result_dir.exists():
        print(f'Result directory not found: {result_dir}', file=sys.stderr)
        return 1

    result_files = sorted(
        p for p in result_dir.glob('sample_*.json')
        if not p.name.endswith('.progress.json')
    )

    roots = {
        'tv_root': cm.get_config('anime_path') or 'Z:/Anime',
        'movie_root': cm.get_config('anime_movie_path') or 'Z:/AnimeMovie',
    }

    stats: Counter = Counter()
    plan_status_stats: Counter = Counter()
    item_disposition_stats: Counter = Counter()
    issue_type_stats: Counter = Counter()
    target_paths: list[str] = []
    target_path_to_samples: dict[str, list[str]] = {}
    failed_samples: list[dict[str, Any]] = []
    accepted_samples: list[dict[str, Any]] = []
    skipped_samples: list[str] = []
    issue_samples: list[tuple[str, str, list[str]]] = []

    for path in result_files:
        data = load_result(path)
        sample_name = path.stem
        status = data.get('status', 'unknown')
        stats[status] += 1

        if status != 'accepted':
            skipped_samples.append(sample_name)
            continue

        brr = data.get('bridge_run_result', {})
        bridge = brr.get('bridge_draft', {})
        legal_graph = brr.get('tmdb_legal_graph', {})
        verified_plan = brr.get('verified_plan', {})

        case_input_path = find_case_input_path(data)
        bridge_input = extract_bridge_input(case_input_path) if case_input_path else None

        if not verified_plan or not legal_graph:
            skipped_samples.append(f'{sample_name} (missing plan/graph)')
            continue
        if not bridge_input:
            skipped_samples.append(f'{sample_name} (missing bridge_input)')
            continue

        try:
            payload = run_bgm_to_tmdb_rename_plan_dry_run(
                bridge_input=bridge_input,
                legal_graph=legal_graph,
                verified_plan=verified_plan,
                roots=roots,
                source_path=bridge_input.get('source_path', sample_name),
                write_snapshot=False,
            )
        except Exception as exc:
            failed_samples.append({
                'sample': sample_name,
                'error': f'{type(exc).__name__}: {exc}',
            })
            continue

        plan_status = payload.get('status', 'unknown')
        plan_status_stats[plan_status] += 1

        rename_plan = payload.get('rename_plan', {})
        for item in rename_plan.get('items', []):
            item_disposition_stats[item.get('disposition', 'unknown')] += 1
            dest = item.get('destination')
            if dest and dest.get('target_path'):
                target_path = dest['target_path']
                target_paths.append(target_path)
                target_path_to_samples.setdefault(target_path, []).append(sample_name)

        verifier_result = payload.get('verifier_result', {})
        for issue in verifier_result.get('issues', []):
            issue_type_stats[issue.get('issue_code', 'unknown')] += 1

        if not payload.get('ok'):
            failed_samples.append({
                'sample': sample_name,
                'status': plan_status,
                'issue_codes': [i.get('issue_code') for i in verifier_result.get('issues', [])],
                'issue_messages': [i.get('message') for i in verifier_result.get('issues', [])],
            })
            issue_samples.append((
                sample_name,
                plan_status,
                [i.get('issue_code') for i in verifier_result.get('issues', [])],
            ))
        else:
            accepted_samples.append({
                'sample': sample_name,
                'target_count': len([i for i in rename_plan.get('items', []) if i.get('destination')]),
                'absent_count': sum(1 for i in rename_plan.get('items', []) if i.get('disposition') == 'tmdb_target_absent'),
                'supplemental_count': sum(1 for i in rename_plan.get('items', []) if i.get('disposition') == 'unmapped_supplemental'),
            })

    duplicate_targets = {k: v for k, v in Counter(target_paths).items() if v > 1}

    print('=== BGM->TMDB final rename plan dry-run summary ===')
    print(f'Total bridge result files: {len(result_files)}')
    print('\n=== Bridge status ===')
    for k, v in sorted(stats.items()):
        print(f'  {k}: {v}')

    print('\n=== Rename plan dry-run status ===')
    for k, v in sorted(plan_status_stats.items()):
        print(f'  {k}: {v}')

    print('\n=== Item disposition ===')
    for k, v in sorted(item_disposition_stats.items()):
        print(f'  {k}: {v}')

    print('\n=== Verifier issue types ===')
    if issue_type_stats:
        for k, v in sorted(issue_type_stats.items()):
            print(f'  {k}: {v}')
    else:
        print('  None')

    print(f'\n=== Accepted samples: {len(accepted_samples)} ===')
    for row in accepted_samples[:10]:
        print(f"  {row['sample']}: targets={row['target_count']} absent={row['absent_count']} supplemental={row['supplemental_count']}")

    print(f'\n=== Failed samples: {len(failed_samples)} ===')
    for row in failed_samples:
        print(f"  {row['sample']}: {row.get('error') or row.get('issue_codes')}")

    print(f'\n=== Skipped samples: {len(skipped_samples)} ===')
    for name in skipped_samples[:10]:
        print(f'  {name}')
    if len(skipped_samples) > 10:
        print(f'  ... and {len(skipped_samples) - 10} more')

    print(f'\n=== Duplicate target paths: {len(duplicate_targets)} ===')
    for target in sorted(duplicate_targets)[:10]:
        count = duplicate_targets[target]
        samples = sorted(set(target_path_to_samples.get(target, [])))
        print(f'  [{count}] {target}')
        print(f'      samples: {samples}')
    if len(duplicate_targets) > 10:
        print(f'  ... and {len(duplicate_targets) - 10} more')

    print(f'\n=== Sample target path examples ===')
    for target in target_paths[:10]:
        print(f'  {target}')

    output = {
        'bridge_status': dict(sorted(stats.items())),
        'rename_plan_status': dict(sorted(plan_status_stats.items())),
        'item_disposition': dict(sorted(item_disposition_stats.items())),
        'issue_type_counts': dict(sorted(issue_type_stats.items())),
        'accepted_count': len(accepted_samples),
        'failed_count': len(failed_samples),
        'skipped_count': len(skipped_samples),
        'duplicate_target_path_count': len(duplicate_targets),
        'duplicate_target_paths': dict(sorted(duplicate_targets.items())),
        'accepted_samples': accepted_samples,
        'failed_samples': failed_samples,
        'skipped_samples': skipped_samples,
        'sample_target_paths': target_paths[:20],
    }

    output_path = result_dir / 'rename_plan_dry_run_summary.json'
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(f'\nSummary written to: {output_path}')
    return 0 if not failed_samples else 1


if __name__ == '__main__':
    sys.exit(main())
