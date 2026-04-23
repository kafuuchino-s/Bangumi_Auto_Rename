from __future__ import annotations

import json
from pathlib import Path

from .models import RunReport


def write_report_json(path: Path, report: RunReport) -> None:
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(report.to_dict(), file, indent=2, ensure_ascii=False)
        file.write('\n')


def build_report_markdown(report: RunReport) -> str:
    report_dict = report.to_dict()
    run_context = report_dict['run_context']
    gate_result = report_dict['gate_result']
    summary = report_dict.get('summary') or {}
    lines = [
        '# Regression Run Summary',
        '',
        '## Run',
        f"- mode: `{run_context.get('mode')}`",
        f"- run_id: `{run_context.get('run_id')}`",
        f"- manifest snapshot: `{run_context.get('manifest_snapshot_path')}`",
        f"- baseline root: `{run_context.get('baseline_root')}`",
        f"- gate failed: `{gate_result.get('gate_failed')}`",
        f"- product failures: `{gate_result.get('product_failure_count')}`",
        f"- infra failures: `{gate_result.get('infra_failure_count')}`",
        f"- flaky count: `{gate_result.get('flaky_count')}`",
        f"- observation failures: `{len(report_dict.get('observation_failures') or [])}`",
        f"- quarantine candidates: `{len(report_dict.get('quarantine_candidates') or [])}`",
        '',
        '## Summary',
    ]

    lines.extend(
        [
            f"- selected: `{summary.get('selected_count')}`",
            f"- completed: `{summary.get('completed_count')}`",
            f"- passed: `{summary.get('passed_count')}`",
            f"- product failures: `{summary.get('product_failure_count')}`",
            f"- infra failures: `{summary.get('infra_failure_count')}`",
            f"- flaky: `{summary.get('flaky_count')}`",
            f"- baseline missing: `{summary.get('baseline_missing_count')}`",
            f"- manual review: `{summary.get('manual_review_count')}`",
            '',
        ]
    )

    sample_results = summary.get('sample_results') or []
    if sample_results:
        lines.append('#### Sample Statuses')
        for sample in sample_results:
            lines.append(f"- `{sample.get('sample_id')}` → `{sample.get('status')}`")
        lines.append('')

    if report_dict.get('observation_failures'):
        lines.append('## Observation Failures')
        lines.extend(f'- `{sample_id}`' for sample_id in report_dict['observation_failures'])
        lines.append('')

    if report_dict.get('infra_failures'):
        lines.append('## Infra Failures')
        lines.extend(f'- `{sample_id}`' for sample_id in report_dict['infra_failures'])
        lines.append('')

    if report_dict.get('flaky_samples'):
        lines.append('## Flaky Samples')
        lines.extend(f'- `{sample_id}`' for sample_id in report_dict['flaky_samples'])
        lines.append('')

    if report_dict.get('quarantine_candidates'):
        lines.append('## Quarantine Candidates')
        lines.extend(f'- `{sample_id}`' for sample_id in report_dict['quarantine_candidates'])
        lines.append('')

    return '\n'.join(lines) + '\n'


def write_report_markdown(path: Path, report: RunReport) -> None:
    path.write_text(build_report_markdown(report), encoding='utf-8')
