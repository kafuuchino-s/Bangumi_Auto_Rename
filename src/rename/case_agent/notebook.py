from __future__ import annotations

from typing import Any, Iterable

from .models import (
    CaseBriefingOutput,
    CaseBriefingWorkUnit,
    CaseDossier,
    InvestigationNotebook,
    NotebookHypothesis,
    NotebookNextAction,
    NotebookOpenQuestion,
    NotebookRejectedCandidate,
    NotebookTargetOwnership,
    NotebookUpdate,
    NotebookWorkUnitState,
    VerifierIssue,
)
from .mapping_draft import compute_mapping_draft_accounting


def _coerce_dossier(source):
    from .workspace import CaseEvidenceWorkspace

    return source.to_dossier() if isinstance(source, CaseEvidenceWorkspace) else source


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value or '') for value in values if str(value or '')))


def _sample(values: list[str], *, limit: int = 8) -> list[str]:
    values = [value for value in values if value]
    if len(values) <= limit:
        return values
    head = values[: max(1, limit // 2)]
    tail = values[-max(1, limit // 2):]
    return _dedupe([*head, *tail])[:limit]


def visible_notebook_ref_set(source) -> set[str]:
    dossier = _coerce_dossier(source)
    visible = getattr(dossier, 'visible_refs', None)
    refs = {
        *list(getattr(visible, 'local_file_refs', []) or []),
        *list(getattr(visible, 'local_cluster_refs', []) or []),
        *list(getattr(visible, 'bangumi_subject_refs', []) or []),
        *list(getattr(visible, 'bangumi_relation_refs', []) or []),
        *list(getattr(visible, 'bangumi_group_refs', []) or []),
        *list(getattr(visible, 'bangumi_item_refs', []) or []),
        *list(getattr(visible, 'query_refs', []) or []),
        *list(getattr(visible, 'target_refs', []) or []),
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'local_span_cards', []) or [])],
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])],
        *[str(getattr(card, 'ref', '') or '') for card in list(getattr(dossier, 'provenance_cards', []) or [])],
        *list(getattr(dossier, 'seen_detail_refs', []) or []),
        *list(getattr(dossier, 'assignable_target_refs', []) or []),
        *list(getattr(dossier, 'detailed_card_refs', []) or []),
    }
    return {ref for ref in refs if ref}


def internal_notebook_ref_set(notebook: InvestigationNotebook | None) -> set[str]:
    if notebook is None:
        return set()
    refs = {
        *[str(getattr(item, 'ref', '') or '') for item in list(notebook.active_hypotheses or [])],
        *[str(getattr(item, 'work_unit_ref', '') or '') for item in list(notebook.work_unit_states or [])],
        *[str(getattr(item, 'question_ref', '') or '') for item in list(notebook.open_questions or [])],
        *[str(getattr(item, 'action_ref', '') or '') for item in list(notebook.next_actions or [])],
        *[str(getattr(item, 'ref', '') or '') for item in list(notebook.rejected_candidates or [])],
        *[str(getattr(item, 'target_ref', '') or '') for item in list(notebook.target_ownership or [])],
    }
    return {ref for ref in refs if ref}


def _unknown_refs(values: Iterable[str], visible_refs: set[str]) -> list[str]:
    return sorted({ref for ref in _dedupe(values) if ref not in visible_refs})


def _issue(ref: str, code: str, message: str, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(ref=ref, issue_code=code, severity='blocked', message=message, related_refs=list(related_refs or []))


def _briefing_work_unit_refs(unit: CaseBriefingWorkUnit) -> list[str]:
    return _dedupe([*list(unit.local_refs or []), *list(unit.file_refs or []), *list(unit.span_refs or [])])


def validate_case_briefing_refs(briefing: CaseBriefingOutput | None, source) -> list[VerifierIssue]:
    if briefing is None:
        return []
    dossier = _coerce_dossier(source)
    visible_refs = visible_notebook_ref_set(dossier)
    issues: list[VerifierIssue] = []
    covered_main_refs: list[str] = []
    main_refs = set(getattr(getattr(dossier, 'contract', None), 'main_file_refs', []) or [])
    for unit in list(briefing.work_units or []):
        unit_ref = str(getattr(unit, 'work_unit_ref', '') or 'work_unit')
        refs = _briefing_work_unit_refs(unit)
        unknown = _unknown_refs(refs, visible_refs)
        if unknown:
            issues.append(_issue(unit_ref, 'briefing_hidden_ref', 'case briefing work unit cited refs outside the visible dossier', unknown[:8]))
        covered_main_refs.extend([ref for ref in list(unit.file_refs or []) + list(unit.local_refs or []) if ref in main_refs])
    for title in list(briefing.title_hypotheses or []):
        unknown = _unknown_refs(list(title.source_refs or []), visible_refs)
        if unknown:
            issues.append(_issue(str(getattr(title, 'title', '') or 'title_hypothesis'), 'briefing_hidden_ref', 'case briefing title hypothesis cited refs outside the visible dossier', unknown[:8]))
    for question in list(briefing.evidence_questions or []):
        unknown = _unknown_refs(list(question.local_refs or []), visible_refs)
        if unknown:
            issues.append(_issue(question.question_ref or 'evidence_question', 'briefing_hidden_ref', 'case briefing evidence question cited refs outside the visible dossier', unknown[:8]))
    if main_refs:
        covered = set(covered_main_refs)
        missing = sorted(main_refs - covered)
        if missing and not list(briefing.evidence_questions or []):
            issues.append(_issue('case_briefing', 'briefing_main_refs_uncovered', 'case briefing work units must cover main refs or leave explicit evidence questions', missing[:8]))
    return issues


def validate_notebook_updates(updates: list[NotebookUpdate], source) -> list[VerifierIssue]:
    dossier = _coerce_dossier(source)
    notebook = getattr(dossier, 'investigation_notebook', None)
    visible_refs = visible_notebook_ref_set(dossier)
    internal_refs = internal_notebook_ref_set(notebook)
    issues: list[VerifierIssue] = []
    for index, update in enumerate(list(updates or []), start=1):
        refs = _dedupe([
            *list(getattr(update, 'local_refs', []) or []),
            *list(getattr(update, 'target_refs', []) or []),
            *list(getattr(update, 'query_refs', []) or []),
            *list(getattr(update, 'subject_refs', []) or []),
            *list(getattr(update, 'item_refs', []) or []),
        ])
        unknown = _unknown_refs(refs, visible_refs)
        if unknown:
            issues.append(_issue(f'NU{index}', 'notebook_update_hidden_ref', 'notebook update cited refs outside the visible dossier', unknown[:8]))
        unknown_notebook_refs = _unknown_refs(list(getattr(update, 'notebook_refs', []) or []), internal_refs)
        if unknown_notebook_refs:
            issues.append(_issue(f'NU{index}', 'notebook_update_unknown_notebook_ref', 'notebook update cited notebook refs outside the current InvestigationNotebook', unknown_notebook_refs[:8]))
    return issues


def build_initial_investigation_notebook(briefing: CaseBriefingOutput | None, source) -> InvestigationNotebook:
    dossier = _coerce_dossier(source)
    if briefing is None:
        briefing = CaseBriefingOutput(summary='no case briefing available')
    hypotheses: list[NotebookHypothesis] = []
    for index, title in enumerate(list(briefing.title_hypotheses or [])[:12], start=1):
        hypotheses.append(
            NotebookHypothesis(
                ref=f'NH{index}',
                claim=f'title hypothesis: {title.title}',
                local_refs=list(title.source_refs or []),
                query_refs=[ref for ref in list(title.source_refs or []) if str(ref).startswith(('SQ', 'QC'))],
                status='active',
                confidence=title.confidence,
                reason=title.reason,
            )
        )
    work_states: list[NotebookWorkUnitState] = []
    for unit in list(briefing.work_units or []):
        local_refs = _dedupe([*list(unit.local_refs or []), *list(unit.file_refs or []), *list(unit.span_refs or [])])
        status = 'needs_evidence' if unit.status in {'open', 'unresolved', 'unknown'} else unit.status
        if status not in {'open', 'mapped', 'excluded', 'needs_evidence', 'blocked', 'unknown'}:
            status = 'open'
        work_states.append(
            NotebookWorkUnitState(
                work_unit_ref=unit.work_unit_ref,
                local_refs=local_refs,
                status=status,
                claim=unit.label or '; '.join(unit.title_hints[:3]),
                unresolved_reason=unit.reason if status in {'open', 'needs_evidence', 'blocked'} else '',
                support_refs=local_refs[:12],
            )
        )
    questions = [
        NotebookOpenQuestion(
            question_ref=question.question_ref or f'NQ{index}',
            question_kind=question.question_kind,
                question=question.question,
                local_refs=list(question.local_refs or []),
                query_hints=list(question.query_hints or []),
                requested_request_types=list(question.requested_request_types or []),
                status='open',
                reason=question.reason,
        )
        for index, question in enumerate(list(briefing.evidence_questions or []), start=1)
    ]
    next_actions = [
        NotebookNextAction(
            action_ref=f'NA{index}',
            action_type=question.question_kind or 'evidence',
            requested_request_types=list(question.requested_request_types or []),
            query_hints=list(question.query_hints or []),
            local_refs=list(question.local_refs or []),
            status='open',
            reason=question.reason or question.question,
        )
        for index, question in enumerate(list(briefing.evidence_questions or []), start=1)
    ]
    has_target_surface = bool(
        list(getattr(dossier, 'bangumi_subjects', []) or [])
        or list(getattr(dossier, 'bangumi_items', []) or [])
        or list(getattr(dossier, 'bangumi_span_cards', []) or [])
    )
    if not questions and not has_target_surface:
        main_refs = list(getattr(getattr(dossier, 'contract', None), 'main_file_refs', []) or [])[:8]
        questions.append(
            NotebookOpenQuestion(
                question_ref='NQ_SUBJECT_RECALL',
                question_kind='subject_recall',
                question='Recall the likely Bangumi subject before mapping local work units.',
                local_refs=main_refs,
                requested_request_types=['subject_search'],
                status='open',
                reason='initial notebook fallback',
            )
        )
        next_actions.append(
            NotebookNextAction(
                action_ref='NA_SUBJECT_RECALL',
                action_type='subject_recall',
                requested_request_types=['subject_search'],
                local_refs=main_refs,
                status='open',
                reason='initial notebook fallback',
            )
        )
    return InvestigationNotebook(
        active_hypotheses=hypotheses,
        work_unit_states=work_states,
        open_questions=questions,
        next_actions=next_actions,
    )


def _update_refs(update: NotebookUpdate) -> set[str]:
    return {
        ref
        for ref in [
            *list(update.local_refs or []),
            *list(update.target_refs or []),
            *list(update.query_refs or []),
            *list(update.subject_refs or []),
            *list(update.item_refs or []),
        ]
        if ref
    }


def _refs_overlap(left: Iterable[str], right: set[str]) -> bool:
    return bool({ref for ref in _dedupe(left) if ref} & right)


def _close_related_agenda(active: InvestigationNotebook, update: NotebookUpdate, *, status: str = 'answered') -> None:
    refs = _update_refs(update)
    notebook_refs = set(list(update.notebook_refs or []))
    if not refs and not notebook_refs:
        return
    closed_question_status = status if status in {'answered', 'blocked', 'closed'} else 'answered'
    closed_action_status = 'done' if status in {'answered', 'closed'} else status
    questions = []
    for question in active.open_questions:
        question_ref = str(getattr(question, 'question_ref', '') or '')
        related_refs = [
            *list(getattr(question, 'local_refs', []) or []),
            *list(getattr(question, 'target_refs', []) or []),
            *list(getattr(question, 'query_refs', []) or []),
            *list(getattr(question, 'subject_refs', []) or []),
            *list(getattr(question, 'item_refs', []) or []),
        ]
        if question_ref in notebook_refs or _refs_overlap(related_refs, refs):
            questions.append(question.model_copy(update={'status': closed_question_status}))
        else:
            questions.append(question)
    active.open_questions = questions
    actions = []
    for action in active.next_actions:
        action_ref = str(getattr(action, 'action_ref', '') or '')
        related_refs = [
            *list(getattr(action, 'local_refs', []) or []),
            *list(getattr(action, 'target_refs', []) or []),
            *list(getattr(action, 'subject_refs', []) or []),
            *list(getattr(action, 'item_refs', []) or []),
        ]
        if action_ref in notebook_refs or _refs_overlap(related_refs, refs):
            actions.append(action.model_copy(update={'status': closed_action_status if closed_action_status in {'done', 'blocked', 'closed'} else 'done'}))
        else:
            actions.append(action)
    active.next_actions = actions


def close_notebook_agenda_for_evidence_results(
    notebook: InvestigationNotebook | None,
    request_results: list[object],
) -> InvestigationNotebook:
    active = notebook.model_copy(deep=True) if notebook is not None else InvestigationNotebook()
    completed_types = {
        str(getattr(result, 'request_type', '') or '')
        for result in list(request_results or [])
        if bool(getattr(result, 'accepted', False)) and str(getattr(result, 'request_type', '') or '')
    }
    if not completed_types:
        return active
    active.open_questions = [
        question.model_copy(update={'status': 'answered'})
        if str(getattr(question, 'status', '') or '') == 'open'
        and completed_types & {str(value or '') for value in list(getattr(question, 'requested_request_types', []) or [])}
        else question
        for question in list(active.open_questions or [])
    ]
    active.next_actions = [
        action.model_copy(update={'status': 'done'})
        if str(getattr(action, 'status', '') or '') == 'open'
        and completed_types & {str(value or '') for value in list(getattr(action, 'requested_request_types', []) or [])}
        else action
        for action in list(active.next_actions or [])
    ]
    return active


def apply_notebook_updates(
    notebook: InvestigationNotebook | None,
    updates: list[NotebookUpdate],
    source,
) -> tuple[InvestigationNotebook, list[VerifierIssue]]:
    active = notebook.model_copy(deep=True) if notebook is not None else InvestigationNotebook()
    updates = list(updates or [])
    issues = validate_notebook_updates(updates, source)
    if issues:
        return active, issues
    next_h_index = len(active.active_hypotheses) + 1
    next_q_index = len(active.open_questions) + 1
    next_a_index = len(active.next_actions) + 1
    next_r_index = len(active.rejected_candidates) + 1
    target_by_ref = {item.target_ref: item for item in active.target_ownership if item.target_ref}
    work_by_local: dict[str, NotebookWorkUnitState] = {}
    for state in active.work_unit_states:
        for ref in list(state.local_refs or []):
            work_by_local.setdefault(ref, state)
    for update in updates:
        kind = str(update.update_kind or '').strip().casefold()
        if kind in {'reject_candidate', 'candidate_rejected', 'rejected_candidate'}:
            active.rejected_candidates.append(
                NotebookRejectedCandidate(
                    ref=f'NRC{next_r_index}',
                    candidate_ref=(update.target_refs or [''])[0],
                    local_refs=list(update.local_refs or []),
                    target_refs=list(update.target_refs or []),
                    support_refs=_dedupe([*list(update.local_refs or []), *list(update.target_refs or []), *list(update.subject_refs or []), *list(update.item_refs or []), *list(update.query_refs or [])])[:12],
                    reason=update.reason or update.claim,
                )
            )
            next_r_index += 1
        elif kind in {'target_ownership', 'ownership', 'mapped', 'target_claim'}:
            for target_ref in list(update.target_refs or []):
                owner = target_by_ref.get(target_ref)
                payload = {
                    'target_ref': target_ref,
                    'owner_local_ref': (update.local_refs or [''])[0],
                    'status': 'claimed' if update.confidence != 'high' else 'confirmed',
                    'support_refs': _dedupe([*list(update.local_refs or []), target_ref, *list(update.subject_refs or []), *list(update.item_refs or []), *list(update.query_refs or [])])[:12],
                    'reason': update.reason or update.claim,
                }
                if owner is None:
                    owner = NotebookTargetOwnership(**payload)
                    active.target_ownership.append(owner)
                    target_by_ref[target_ref] = owner
                else:
                    replacement = owner.model_copy(update=payload)
                    active.target_ownership = [replacement if item is owner else item for item in active.target_ownership]
                    target_by_ref[target_ref] = replacement
            if kind == 'mapped':
                for local_ref in list(update.local_refs or []):
                    state = work_by_local.get(local_ref)
                    if state is None:
                        continue
                    replacement = state.model_copy(update={
                        'target_refs': _dedupe([*list(state.target_refs or []), *list(update.target_refs or [])]),
                        'status': 'mapped',
                        'claim': update.claim or state.claim,
                        'unresolved_reason': '',
                        'support_refs': _dedupe([*list(state.support_refs or []), *list(update.local_refs or []), *list(update.target_refs or []), *list(update.subject_refs or []), *list(update.item_refs or []), *list(update.query_refs or [])])[:12],
                    })
                    active.work_unit_states = [replacement if item is state else item for item in active.work_unit_states]
                    for ref in replacement.local_refs:
                        work_by_local[ref] = replacement
        elif kind in {'open_question', 'needs_more_evidence', 'question'}:
            active.open_questions.append(
                NotebookOpenQuestion(
                    question_ref=f'NQ{next_q_index}',
                    question_kind=kind,
                    question=update.claim or update.reason,
                    local_refs=list(update.local_refs or []),
                    target_refs=list(update.target_refs or []),
                    query_refs=list(update.query_refs or []),
                    subject_refs=list(update.subject_refs or []),
                    item_refs=list(update.item_refs or []),
                    query_hints=list(update.query_hints or []),
                    requested_request_types=list(update.requested_request_types or []),
                    status='open',
                    reason=update.reason,
                )
            )
            next_q_index += 1
        elif kind in {'next_action', 'evidence_action', 'request_evidence'}:
            active.next_actions.append(
                NotebookNextAction(
                    action_ref=f'NA{next_a_index}',
                    action_type=kind,
                    requested_request_types=list(update.requested_request_types or []),
                    query_hints=list(update.query_hints or []),
                    local_refs=list(update.local_refs or []),
                    subject_refs=list(update.subject_refs or []),
                    item_refs=list(update.item_refs or []),
                    target_refs=list(update.target_refs or []),
                    status='open',
                    reason=update.reason or update.claim,
                )
            )
            next_a_index += 1
        elif kind in {'work_unit_state', 'exclude', 'target_absent', 'supplemental'}:
            for local_ref in list(update.local_refs or []):
                state = work_by_local.get(local_ref)
                if state is None:
                    state = NotebookWorkUnitState(work_unit_ref=f'WU_NOTE_{len(active.work_unit_states) + 1}', local_refs=[local_ref])
                    active.work_unit_states.append(state)
                    work_by_local[local_ref] = state
                status = 'excluded' if kind in {'exclude', 'target_absent', 'supplemental'} else state.status
                replacement = state.model_copy(update={
                    'target_refs': _dedupe([*list(state.target_refs or []), *list(update.target_refs or [])]),
                    'status': status,
                    'claim': update.claim or state.claim,
                    'unresolved_reason': '' if status in {'mapped', 'excluded'} else (update.reason or state.unresolved_reason),
                    'support_refs': _dedupe([*list(state.support_refs or []), *list(update.local_refs or []), *list(update.target_refs or []), *list(update.subject_refs or []), *list(update.item_refs or []), *list(update.query_refs or [])])[:12],
                })
                active.work_unit_states = [replacement if item is state else item for item in active.work_unit_states]
                for ref in replacement.local_refs:
                    work_by_local[ref] = replacement
            _close_related_agenda(active, update, status='closed')
        else:
            active.active_hypotheses.append(
                NotebookHypothesis(
                    ref=f'NH{next_h_index}',
                    claim=update.claim or update.reason,
                    local_refs=list(update.local_refs or []),
                    target_refs=list(update.target_refs or []),
                    query_refs=list(update.query_refs or []),
                    status='active',
                    confidence=update.confidence,
                    reason=update.reason,
                )
            )
            next_h_index += 1
        if kind in {'target_ownership', 'ownership', 'mapped', 'target_claim'}:
            _close_related_agenda(active, update, status='answered')
        active.update_log.append(update)
    return active, []


def compact_case_briefing(briefing: CaseBriefingOutput | None) -> dict[str, Any]:
    if briefing is None:
        return {}
    return {
        'package_shape': briefing.package_shape,
        'summary': briefing.summary,
        'work_unit_count': len(briefing.work_units or []),
        'work_units': [
            {
                'work_unit_ref': unit.work_unit_ref,
                'label': unit.label,
                'unit_kind': unit.unit_kind,
                'local_refs': _sample(_dedupe([*list(unit.local_refs or []), *list(unit.file_refs or []), *list(unit.span_refs or [])]), limit=10),
                'title_hints': list(unit.title_hints or [])[:8],
                'source_form_hints': list(unit.source_form_hints or [])[:6],
                'status': unit.status,
                'reason': unit.reason,
            }
            for unit in list(briefing.work_units or [])[:24]
        ],
        'title_hypotheses': [
            {
                'title': item.title,
                'language': item.language,
                'hypothesis_kind': item.hypothesis_kind,
                'source_refs': _sample(list(item.source_refs or []), limit=8),
                'ignored_noise_terms': list(item.ignored_noise_terms or [])[:8],
                'confidence': item.confidence,
                'reason': item.reason,
            }
            for item in list(briefing.title_hypotheses or [])[:16]
        ],
        'split_hints': list(briefing.split_hints or [])[:8],
        'evidence_questions': [
            {
                'question_ref': item.question_ref,
                'question_kind': item.question_kind,
                'question': item.question,
                'local_refs': _sample(list(item.local_refs or []), limit=8),
                'query_hints': list(item.query_hints or [])[:8],
                'requested_request_types': list(item.requested_request_types or [])[:8],
                'reason': item.reason,
            }
            for item in list(briefing.evidence_questions or [])[:16]
        ],
    }


def compact_investigation_notebook(notebook: InvestigationNotebook | None) -> dict[str, Any]:
    if notebook is None:
        notebook = InvestigationNotebook()
    open_questions = [item for item in list(notebook.open_questions or []) if str(item.status or '') == 'open']
    next_actions = [item for item in list(notebook.next_actions or []) if str(item.status or '') == 'open']
    return {
        'active_hypotheses': [
            {
                'ref': item.ref,
                'claim': item.claim,
                'local_refs': _sample(list(item.local_refs or []), limit=8),
                'target_refs': _sample(list(item.target_refs or []), limit=8),
                'query_refs': _sample(list(item.query_refs or []), limit=8),
                'status': item.status,
                'confidence': item.confidence,
                'reason': item.reason,
            }
            for item in list(notebook.active_hypotheses or [])[:16]
        ],
        'work_unit_states': [
            {
                'work_unit_ref': item.work_unit_ref,
                'local_refs': _sample(list(item.local_refs or []), limit=10),
                'target_refs': _sample(list(item.target_refs or []), limit=8),
                'status': item.status,
                'claim': item.claim,
                'unresolved_reason': item.unresolved_reason,
                'support_refs': _sample(list(item.support_refs or []), limit=8),
            }
            for item in list(notebook.work_unit_states or [])[:24]
        ],
        'target_ownership': [
            {
                'target_ref': item.target_ref,
                'owner_local_ref': item.owner_local_ref,
                'owner_work_unit_ref': item.owner_work_unit_ref,
                'status': item.status,
                'support_refs': _sample(list(item.support_refs or []), limit=8),
                'reason': item.reason,
            }
            for item in list(notebook.target_ownership or [])[:24]
        ],
        'rejected_candidates': [
            {
                'ref': item.ref,
                'candidate_ref': item.candidate_ref,
                'local_refs': _sample(list(item.local_refs or []), limit=8),
                'target_refs': _sample(list(item.target_refs or []), limit=8),
                'reason': item.reason,
            }
            for item in list(notebook.rejected_candidates or [])[:16]
        ],
        'open_questions': [
            {
                'question_ref': item.question_ref,
                'question_kind': item.question_kind,
                'question': item.question,
                'local_refs': _sample(list(item.local_refs or []), limit=8),
                'target_refs': _sample(list(item.target_refs or []), limit=8),
                'query_refs': _sample(list(item.query_refs or []), limit=8),
                'subject_refs': _sample(list(item.subject_refs or []), limit=8),
                'item_refs': _sample(list(item.item_refs or []), limit=8),
                'query_hints': list(item.query_hints or [])[:8],
                'requested_request_types': list(item.requested_request_types or [])[:8],
                'reason': item.reason,
            }
            for item in open_questions[:16]
        ],
        'next_actions': [
            {
                'action_ref': item.action_ref,
                'action_type': item.action_type,
                'requested_request_types': list(item.requested_request_types or [])[:8],
                'query_hints': list(item.query_hints or [])[:8],
                'local_refs': _sample(list(item.local_refs or []), limit=8),
                'subject_refs': _sample(list(item.subject_refs or []), limit=8),
                'item_refs': _sample(list(item.item_refs or []), limit=8),
                'target_refs': _sample(list(item.target_refs or []), limit=8),
                'reason': item.reason,
            }
            for item in next_actions[:16]
        ],
        'counts': {
            'active_hypothesis_count': len(notebook.active_hypotheses or []),
            'work_unit_state_count': len(notebook.work_unit_states or []),
            'target_ownership_count': len(notebook.target_ownership or []),
            'rejected_candidate_count': len(notebook.rejected_candidates or []),
            'open_question_count': len(open_questions),
            'next_action_count': len(next_actions),
            'update_count': len(notebook.update_log or []),
        },
    }


def build_notebook(source) -> dict[str, Any]:
    dossier = _coerce_dossier(source)
    plan_state = getattr(dossier, 'plan_state', None)
    mapping_draft = getattr(dossier, 'mapping_draft', None)
    mapping_draft_patches = list(getattr(dossier, 'mapping_draft_patches', []) or [])
    accounting = compute_mapping_draft_accounting(mapping_draft, dossier) if mapping_draft is not None else None
    typed_notebook = getattr(dossier, 'investigation_notebook', None)
    briefing = getattr(dossier, 'case_briefing', None)
    compact_typed = compact_investigation_notebook(typed_notebook)
    return {
        'case_id': dossier.header.case_id,
        'rounds': dossier.header.round_index,
        'evidence_requests': len(dossier.previous_evidence_results),
        'results': [
            {'batch_ref': batch.batch_ref, 'status': batch.status, 'request_count': len(batch.request_results)}
            for batch in (dossier.previous_evidence_results or [])[:5]
        ],
        'verifier_issues': [issue.message for issue in (dossier.verifier_issues or [])[:10]],
        'fail_closed_reasons': [],
        'judge_summaries': [getattr(item, 'summary', '') for item in (getattr(dossier, 'previous_hypotheses', []) or [])[:5]],
        'assignment_draft_counts': {
            'main_files': len(dossier.contract.main_file_refs),
            'assignable_targets': len(dossier.assignable_target_refs),
        },
        'plan_state': {
            'active_plan_id': getattr(plan_state, 'plan_id', '') if plan_state else '',
            'plan_kind': getattr(plan_state, 'plan_kind', '') if plan_state else '',
            'plan_status': getattr(plan_state, 'plan_status', 'idle') if plan_state else 'idle',
            'selected_menu_request_ids': list(getattr(plan_state, 'selected_menu_request_ids', []) or [])[:12] if plan_state else [],
            'completed_menu_request_ids': list(getattr(plan_state, 'completed_menu_request_ids', []) or [])[:12] if plan_state else [],
            'failed_menu_request_ids': list(getattr(plan_state, 'failed_menu_request_ids', []) or [])[:12] if plan_state else [],
            'ready_span_refs': list(getattr(plan_state, 'ready_span_refs', []) or [])[:12] if plan_state else [],
        },
        'mapping_draft_summary': {
            'has_mapping_draft': bool(mapping_draft),
            'row_count': len(getattr(mapping_draft, 'rows', []) or []) if mapping_draft else 0,
            'patch_count': len(mapping_draft_patches),
            'main_file_count': int(getattr(accounting, 'main_file_count', 0) or 0),
            'mapped_file_count': int(getattr(accounting, 'mapped_file_count', 0) or 0),
            'excluded_file_count': int(getattr(accounting, 'excluded_file_count', 0) or 0),
            'needs_more_evidence_file_count': int(getattr(accounting, 'needs_more_evidence_file_count', 0) or 0),
            'unaligned_file_count': int(getattr(accounting, 'unaligned_file_count', 0) or 0),
            'open_file_count': int(getattr(accounting, 'open_file_count', 0) or 0),
            'accounted_for_count': int(getattr(accounting, 'accounted_for_count', 0) or 0),
            'unresolved_count': int(getattr(accounting, 'unresolved_count', 0) or 0),
            'accepted_accounting_ready': bool(getattr(accounting, 'accepted_accounting_ready', False)),
        },
        'case_briefing': compact_case_briefing(briefing),
        'investigation_notebook': compact_typed,
        'compact': True,
        'no_full_prompt': True,
        'no_full_raw_output': True,
        'no_full_catalog': True,
    }


_ACTIONABLE_REQUEST_TYPES = {
    'subject_search',
    'subject_lookup',
    'related_expansion',
    'episode_list',
    'episode_detail',
    'target_detail',
    'target_window',
    'target_span',
}


def human_next_action_blockers(source) -> list[dict[str, Any]]:
    dossier = _coerce_dossier(source)
    notebook = getattr(dossier, 'investigation_notebook', None)
    if notebook is None:
        return []
    blockers: list[dict[str, Any]] = []
    for question in list(notebook.open_questions or []):
        if str(getattr(question, 'status', '') or '') != 'open':
            continue
        requested = [value for value in list(getattr(question, 'requested_request_types', []) or []) if value in _ACTIONABLE_REQUEST_TYPES]
        kind = str(getattr(question, 'question_kind', '') or '')
        if requested or any(marker in kind for marker in ('subject', 'related', 'special', 'episode', 'target', 'alternate')):
            blockers.append({
                'source': 'open_question',
                'ref': getattr(question, 'question_ref', ''),
                'question_kind': kind,
                'requested_request_types': requested,
                'local_refs': _sample(list(getattr(question, 'local_refs', []) or []), limit=6),
                'target_refs': _sample(list(getattr(question, 'target_refs', []) or []), limit=6),
                'subject_refs': _sample(list(getattr(question, 'subject_refs', []) or []), limit=6),
                'item_refs': _sample(list(getattr(question, 'item_refs', []) or []), limit=6),
                'query_hints': list(getattr(question, 'query_hints', []) or [])[:6],
                'reason': getattr(question, 'reason', '') or getattr(question, 'question', ''),
            })
    for action in list(notebook.next_actions or []):
        if str(getattr(action, 'status', '') or '') != 'open':
            continue
        requested = [value for value in list(getattr(action, 'requested_request_types', []) or []) if value in _ACTIONABLE_REQUEST_TYPES]
        action_type = str(getattr(action, 'action_type', '') or '')
        if requested or any(marker in action_type for marker in ('subject', 'related', 'special', 'episode', 'target', 'alternate')):
            blockers.append({
                'source': 'next_action',
                'ref': getattr(action, 'action_ref', ''),
                'action_type': action_type,
                'requested_request_types': requested,
                'query_hints': list(getattr(action, 'query_hints', []) or [])[:6],
                'local_refs': _sample(list(getattr(action, 'local_refs', []) or []), limit=6),
                'target_refs': _sample(list(getattr(action, 'target_refs', []) or []), limit=6),
                'subject_refs': _sample(list(getattr(action, 'subject_refs', []) or []), limit=6),
                'item_refs': _sample(list(getattr(action, 'item_refs', []) or []), limit=6),
                'reason': getattr(action, 'reason', ''),
            })
    return blockers[:12]


def _filter_refs(values: list[str], allowed: set[str]) -> list[str]:
    return [ref for ref in _dedupe(values) if ref in allowed]


def filter_case_briefing_for_child(briefing: CaseBriefingOutput | None, *, allowed_refs: set[str]) -> CaseBriefingOutput | None:
    if briefing is None:
        return None
    work_units: list[CaseBriefingWorkUnit] = []
    for unit in list(briefing.work_units or []):
        refs = set(_briefing_work_unit_refs(unit))
        if not refs & allowed_refs:
            continue
        work_units.append(unit.model_copy(update={
            'local_refs': _filter_refs(list(unit.local_refs or []), allowed_refs),
            'file_refs': _filter_refs(list(unit.file_refs or []), allowed_refs),
            'span_refs': _filter_refs(list(unit.span_refs or []), allowed_refs),
        }))
    title_hypotheses = [
        title.model_copy(update={'source_refs': _filter_refs(list(title.source_refs or []), allowed_refs)})
        for title in list(briefing.title_hypotheses or [])
        if set(list(title.source_refs or [])) & allowed_refs or not list(title.source_refs or [])
    ]
    evidence_questions = [
        question.model_copy(update={'local_refs': _filter_refs(list(question.local_refs or []), allowed_refs)})
        for question in list(briefing.evidence_questions or [])
        if set(list(question.local_refs or [])) & allowed_refs
    ]
    return briefing.model_copy(update={
        'work_units': work_units,
        'title_hypotheses': title_hypotheses,
        'evidence_questions': evidence_questions,
    })


def filter_investigation_notebook_for_child(notebook: InvestigationNotebook | None, *, allowed_refs: set[str]) -> InvestigationNotebook:
    if notebook is None:
        return InvestigationNotebook()

    def mentions(item) -> bool:
        refs = set(_dedupe([
            *list(getattr(item, 'local_refs', []) or []),
            *list(getattr(item, 'target_refs', []) or []),
            *list(getattr(item, 'query_refs', []) or []),
            *list(getattr(item, 'subject_refs', []) or []),
            *list(getattr(item, 'item_refs', []) or []),
            *list(getattr(item, 'support_refs', []) or []),
            str(getattr(item, 'target_ref', '') or ''),
            str(getattr(item, 'owner_local_ref', '') or ''),
        ]))
        return bool(refs & allowed_refs) or not refs

    return InvestigationNotebook(
        active_hypotheses=[
            item.model_copy(update={
                'local_refs': _filter_refs(list(item.local_refs or []), allowed_refs),
                'target_refs': _filter_refs(list(item.target_refs or []), allowed_refs),
                'query_refs': _filter_refs(list(item.query_refs or []), allowed_refs),
            })
            for item in list(notebook.active_hypotheses or [])
            if mentions(item)
        ],
        work_unit_states=[
            item.model_copy(update={
                'local_refs': _filter_refs(list(item.local_refs or []), allowed_refs),
                'target_refs': _filter_refs(list(item.target_refs or []), allowed_refs),
                'support_refs': _filter_refs(list(item.support_refs or []), allowed_refs),
            })
            for item in list(notebook.work_unit_states or [])
            if mentions(item)
        ],
        target_ownership=[
            item.model_copy(update={
                'support_refs': _filter_refs(list(item.support_refs or []), allowed_refs),
            })
            for item in list(notebook.target_ownership or [])
            if mentions(item)
        ],
        rejected_candidates=[
            item.model_copy(update={
                'local_refs': _filter_refs(list(item.local_refs or []), allowed_refs),
                'target_refs': _filter_refs(list(item.target_refs or []), allowed_refs),
                'support_refs': _filter_refs(list(item.support_refs or []), allowed_refs),
            })
            for item in list(notebook.rejected_candidates or [])
            if mentions(item)
        ],
        open_questions=[
            item.model_copy(update={
                'local_refs': _filter_refs(list(item.local_refs or []), allowed_refs),
                'target_refs': _filter_refs(list(item.target_refs or []), allowed_refs),
                'query_refs': _filter_refs(list(item.query_refs or []), allowed_refs),
                'subject_refs': _filter_refs(list(item.subject_refs or []), allowed_refs),
                'item_refs': _filter_refs(list(item.item_refs or []), allowed_refs),
            })
            for item in list(notebook.open_questions or [])
            if mentions(item)
        ],
        next_actions=[
            item.model_copy(update={
                'local_refs': _filter_refs(list(item.local_refs or []), allowed_refs),
                'subject_refs': _filter_refs(list(item.subject_refs or []), allowed_refs),
                'item_refs': _filter_refs(list(item.item_refs or []), allowed_refs),
                'target_refs': _filter_refs(list(item.target_refs or []), allowed_refs),
            })
            for item in list(notebook.next_actions or [])
            if mentions(item)
        ],
        update_log=[
            item.model_copy(update={
                'local_refs': _filter_refs(list(item.local_refs or []), allowed_refs),
                'target_refs': _filter_refs(list(item.target_refs or []), allowed_refs),
                'query_refs': _filter_refs(list(item.query_refs or []), allowed_refs),
                'subject_refs': _filter_refs(list(item.subject_refs or []), allowed_refs),
                'item_refs': _filter_refs(list(item.item_refs or []), allowed_refs),
            })
            for item in list(notebook.update_log or [])
            if mentions(item)
        ],
    )
