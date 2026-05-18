from __future__ import annotations

from .models import RecordedSplitPlanRow


def latest_recorded_split_plan_rows(audits: list[dict[str, object]] | tuple[dict[str, object], ...]) -> list[RecordedSplitPlanRow]:
    """Return the latest Agent-authored split plan as stable RSP* rows."""
    latest: dict[str, object] | None = None
    for audit in reversed(list(audits or [])):
        if not isinstance(audit, dict):
            continue
        note = str(audit.get('note') or '')
        if note == 'orchestrator_split_plan_recorded':
            latest = audit
            break
        if note in {
            'finish_case_fail_closed_verified',
            'finish_case_accepted_accounting_checked',
        }:
            break
    if latest is None:
        return []

    rows: list[RecordedSplitPlanRow] = []
    for index, item in enumerate(list(latest.get('split_cases') or []), start=1):
        if not isinstance(item, dict):
            continue
        rows.append(RecordedSplitPlanRow(
            plan_row_ref=str(item.get('plan_row_ref') or f'RSP{index}'),
            child_case_ref=str(item.get('child_case_ref') or ''),
            main_file_refs=[
                str(ref or '')
                for ref in list(item.get('main_file_refs') or [])
                if str(ref or '')
            ],
            main_group_refs=[
                str(ref or '')
                for ref in list(item.get('main_group_refs') or [])
                if str(ref or '')
            ],
            supplemental_file_refs=[
                str(ref or '')
                for ref in list(item.get('supplemental_file_refs') or [])
                if str(ref or '')
            ],
            supplemental_group_refs=[
                str(ref or '')
                for ref in list(item.get('supplemental_group_refs') or [])
                if str(ref or '')
            ],
            support_refs=[
                str(ref or '')
                for ref in list(item.get('support_refs') or [])
                if str(ref or '')
            ],
            title_hints=[
                str(value or '')
                for value in list(item.get('title_hints') or [])
                if str(value or '')
            ],
            query_hints=[
                str(value or '')
                for value in list(item.get('query_hints') or [])
                if str(value or '')
            ],
            reason=str(item.get('reason') or ''),
        ))
    return rows
