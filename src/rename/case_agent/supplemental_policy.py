from __future__ import annotations

import re

from .models import CaseDossier, LocalFileCard, LocalSpanCard, MappingDraftRow, VerifierIssue


ALLOWED_SUPPLEMENTAL_REASON_KINDS = {
    'bangumi_target_absent',
    'bonus_video',
    'pv_cm',
    'creditless_op_ed',
    'trailer',
    'sample',
    'duplicate_packaging',
    'non_episode_video',
    'making_of',
    'menu_or_navigation',
    'other_supplemental',
}

_MULTI_FILE_SUPPLEMENTAL_REASON_KINDS = {
    'bonus_video',
    'pv_cm',
    'creditless_op_ed',
    'trailer',
    'sample',
    'duplicate_packaging',
    'non_episode_video',
    'making_of',
    'menu_or_navigation',
}

_SMALL_MULTI_FILE_REASON_KINDS = {'bonus_video', 'non_episode_video', 'making_of'}
_SMALL_MULTI_FILE_LIMIT = 4
_BROAD_TARGET_ABSENT_LIMIT = 24
_BANGUMI_TARGET_ABSENT_REASON_KIND = 'bangumi_target_absent'


def _issue(ref: str, issue_code: str, message: str, *, related_refs: list[str] | None = None) -> VerifierIssue:
    return VerifierIssue(ref=ref, issue_code=issue_code, severity='blocked', message=message, related_refs=list(related_refs or []))


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _spans_by_ref(dossier: CaseDossier) -> dict[str, LocalSpanCard]:
    return {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(dossier, 'local_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }


def _files_by_ref(dossier: CaseDossier) -> dict[str, LocalFileCard]:
    return {
        str(getattr(card, 'ref', '') or ''): card
        for card in list(getattr(dossier, 'local_files', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }


def main_file_refs_for_mapping_row(dossier: CaseDossier, row: MappingDraftRow) -> list[str]:
    contract_main_refs = list(getattr(getattr(dossier, 'contract', None), 'main_file_refs', []) or [])
    main_ref_set = set(contract_main_refs)
    spans = _spans_by_ref(dossier)
    files = _files_by_ref(dossier)
    local_ref = str(getattr(row, 'local_ref', '') or '')
    if local_ref in spans:
        refs = [str(ref or '') for ref in list(getattr(spans[local_ref], 'file_refs', []) or []) if str(ref or '')]
    elif local_ref in files or local_ref in main_ref_set:
        refs = [local_ref]
    else:
        refs = []
    if main_ref_set:
        refs = [ref for ref in refs if ref in main_ref_set]
    return _dedupe_preserve_order(refs)


def local_ref_text_for_supplemental_issue(dossier: CaseDossier, local_ref: str) -> str:
    spans = _spans_by_ref(dossier)
    files = _files_by_ref(dossier)
    texts = [str(local_ref or '')]
    span = spans.get(str(local_ref or ''))
    if span is not None:
        texts.extend([
            str(getattr(span, 'span_scope', '') or ''),
            str(getattr(span, 'parent_key', '') or ''),
            str(getattr(span, 'season_cue', '') or ''),
        ])
        texts.extend(str(value or '') for value in list(getattr(span, 'title_cues', []) or []))
        texts.extend(str(value or '') for value in list(getattr(span, 'release_group_cues', []) or []))
        texts.extend(str(value or '') for value in list(getattr(span, 'confidence_facts', []) or []))
        file_refs = list(getattr(span, 'file_refs', []) or []) or list(getattr(span, 'file_ref_samples', []) or [])
    else:
        file_refs = [local_ref]
    for file_ref in file_refs:
        file_card = files.get(str(file_ref or ''))
        if file_card is None:
            continue
        texts.extend([
            str(getattr(file_card, 'ref', '') or ''),
            str(getattr(file_card, 'path', '') or ''),
            str(getattr(file_card, 'label', '') or ''),
            str(getattr(file_card, 'parent_display', '') or ''),
            str(getattr(file_card, 'file_kind', '') or ''),
        ])
    return ' '.join(text for text in texts if text)


def supplemental_category_supported_by_text(reason_kind: str, text: str) -> bool:
    category = str(reason_kind or '').casefold()
    lowered = ' '.join(str(text or '').casefold().split())
    if not category or not lowered:
        return False
    markers_by_category = {
        'creditless_op_ed': (
            'non-credit',
            'non credit',
            'creditless',
            'ncop',
            'nced',
            'non telop',
            'non-telop',
            'no telop',
            'no-telop',
            'ノンクレジット',
            'ノクレジット',
            'ノンテロップ',
            'opening',
            'ending',
            ' op',
            ' ed',
            'オープニング',
            'エンディング',
        ),
        'pv_cm': (
            'pv',
            'cm',
            'trailer',
            'preview',
            'promo',
            'promotional',
            '予告',
            'プロモ',
        ),
        'trailer': (
            'trailer',
            'teaser',
            'preview',
            '予告',
        ),
        'menu_or_navigation': (
            'menu',
            'bdmenu',
            'dvdmenu',
            'blu-ray menu',
            'navigation',
        ),
        'sample': (
            'sample',
            'サンプル',
        ),
        'making_of': (
            'making',
            'interview',
            'behind',
            'travel',
            'location',
            'trip',
            'tour',
            'journey',
            'field',
            '五島',
            '旅',
            'メイキング',
            'インタビュー',
        ),
        'bonus_video': (
            'bonus video',
            'bonus',
            'extra video',
            'extras',
            '映像特典',
            '特典映像',
        ),
        'non_episode_video': (
            'recap',
            'digest',
            'advice',
            'non episode',
            'non-episode',
            '番外',
        ),
        'duplicate_packaging': (
            'disc',
            'bd disc',
            'dvd disc',
        ),
        'other_supplemental': (
            'supplemental',
            'supplementary',
            'extra',
            'extras',
            'bonus',
            '映像特典',
            '特典映像',
            '特典',
        ),
    }
    return any(marker in lowered for marker in markers_by_category.get(category, ()))


def classify_supplemental_reason(text: str) -> str:
    lowered = str(text or '').casefold()
    if (
        any(marker in lowered for marker in ('non-credit', 'non credit', 'creditless', 'ncop', 'nced', 'non telop', 'non-telop', 'no telop', 'no-telop', 'ノンクレジット', 'ノクレジット', 'ノンテロップ'))
        or re.search(r'(?<![a-z0-9])(?:op|ed)(?![a-z0-9])', lowered)
    ):
        return 'creditless_op_ed'
    if any(marker in lowered for marker in ('trailer', 'teaser')):
        return 'trailer'
    if any(marker in lowered for marker in ('pv', 'cm', 'preview', '予告', 'promo')):
        return 'pv_cm'
    if any(marker in lowered for marker in ('menu', 'bdmenu', 'dvdmenu')):
        return 'menu_or_navigation'
    if any(marker in lowered for marker in ('sample',)):
        return 'sample'
    if any(marker in lowered for marker in ('making', 'interview', 'behind', 'travel', 'location', 'trip', 'tour', 'journey', 'field', '五島', '旅')):
        return 'making_of'
    if any(marker in lowered for marker in ('recap', 'digest', 'advice', 'non episode', 'non-episode')):
        return 'non_episode_video'
    if any(marker in lowered for marker in ('bonus video', 'extra video', 'extras', '映像特典', '特典映像')):
        return 'bonus_video'
    if any(marker in lowered for marker in ('disc',)):
        return 'duplicate_packaging'
    return 'other_supplemental'


def supplemental_reason_from_local_ref(dossier: CaseDossier, local_ref: str) -> str:
    return classify_supplemental_reason(local_ref_text_for_supplemental_issue(dossier, local_ref))


def _span_for_row(dossier: CaseDossier, row: MappingDraftRow) -> LocalSpanCard | None:
    return _spans_by_ref(dossier).get(str(getattr(row, 'local_ref', '') or ''))


def _is_regular_numbered_like_span(span: LocalSpanCard | None, file_count: int) -> bool:
    if span is None or file_count < 2:
        return False
    episode_token_count = int(getattr(span, 'episode_token_count', 0) or 0)
    ordering_basis = str(getattr(span, 'ordering_basis', '') or '')
    if ordering_basis == 'episode_token_order' and episode_token_count >= 2:
        return True
    if episode_token_count >= 2 and episode_token_count == file_count:
        return True
    return False


def _visible_assignable_candidate_refs(dossier: CaseDossier, row: MappingDraftRow) -> list[str]:
    detail_span_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'bangumi_span_cards', []) or [])
        if str(getattr(card, 'ref', '') or '') and bool(getattr(card, 'detail_equivalent', False))
    }
    item_refs = {
        str(getattr(card, 'ref', '') or '')
        for card in list(getattr(dossier, 'bangumi_items', []) or [])
        if str(getattr(card, 'ref', '') or '')
    }
    visible_refs = getattr(dossier, 'visible_refs', None)
    item_refs.update(str(ref or '') for ref in list(getattr(visible_refs, 'bangumi_item_refs', []) or []) if str(ref or ''))
    item_refs.update(str(ref or '') for ref in list(getattr(visible_refs, 'target_refs', []) or []) if str(ref or ''))
    item_refs.update(str(ref or '') for ref in list(getattr(dossier, 'assignable_target_refs', []) or []) if str(ref or ''))
    item_refs.update(str(ref or '') for ref in list(getattr(dossier, 'detailed_card_refs', []) or []) if str(ref or ''))
    item_refs.update(str(ref or '') for ref in list(getattr(dossier, 'seen_detail_refs', []) or []) if str(ref or ''))
    candidates = []
    for ref in list(getattr(row, 'candidate_target_refs', []) or []):
        ref = str(ref or '')
        if not ref:
            continue
        if ref in detail_span_refs or (ref.startswith('BE') and ref in item_refs):
            candidates.append(ref)
    return _dedupe_preserve_order(candidates)


def _text_supports_target_absent_shape(text: str) -> bool:
    lowered = ' '.join(str(text or '').casefold().split())
    if not lowered:
        return False
    markers = (
        'ova',
        'oav',
        'oad',
        'sp',
        'special',
        'tokubetsu',
        'extra',
        'bonus',
        'non episode',
        'non-episode',
        'recap',
        'digest',
        'side story',
        'side-story',
        '番外',
        '特别',
        '特別',
        '映像特典',
        '特典映像',
    )
    return any(marker in lowered for marker in markers)


def _bangumi_target_absent_policy_issues(
    dossier: CaseDossier,
    row: MappingDraftRow,
    *,
    row_ref: str,
    local_ref: str,
    covered_main_refs: list[str],
    span: LocalSpanCard | None,
    file_count: int,
    local_text: str,
    regular_like: bool,
) -> list[VerifierIssue]:
    support_refs = set(str(ref or '') for ref in list(getattr(row, 'support_refs', []) or []) if str(ref or ''))
    if local_ref not in support_refs and not (support_refs & set(covered_main_refs)):
        return [_issue(
            row_ref,
            'missing_support_refs',
            'bangumi_target_absent requires support_refs containing the local row or covered file refs',
            related_refs=[local_ref, *covered_main_refs[:8]],
        )]
    candidate_refs = _visible_assignable_candidate_refs(dossier, row)
    if candidate_refs:
        return [_issue(
            row_ref,
            'bangumi_target_absent_has_visible_candidate',
            'bangumi_target_absent cannot be used while visible assignable candidate refs remain on the row',
            related_refs=[local_ref, *candidate_refs[:8]],
        )]
    # Shape/regular-vs-special is semantic and belongs to MappingDraftEditor.
    # The fixed layer only verifies support refs and visible-candidate misuse.
    return []


def _each_file_supports_category(dossier: CaseDossier, file_refs: list[str], reason_kind: str) -> bool:
    return all(
        supplemental_category_supported_by_text(reason_kind, local_ref_text_for_supplemental_issue(dossier, ref))
        for ref in file_refs
    )


def supplemental_row_policy_issues(dossier: CaseDossier, row: MappingDraftRow) -> list[VerifierIssue]:
    reason_kind = str(getattr(row, 'reason_kind', '') or '')
    local_ref = str(getattr(row, 'local_ref', '') or '')
    row_ref = str(getattr(row, 'row_ref', '') or local_ref or 'mapping_draft_row')
    if reason_kind not in ALLOWED_SUPPLEMENTAL_REASON_KINDS:
        return [_issue(row_ref, 'invalid_reason_kind', 'supplemental rows require allowlisted reason_kind', related_refs=[local_ref])]

    covered_main_refs = main_file_refs_for_mapping_row(dossier, row)
    if not covered_main_refs:
        return []

    span = _span_for_row(dossier, row)
    file_count = len(covered_main_refs)
    local_text = ' '.join([
        str(getattr(row, 'reason_kind', '') or ''),
        str(getattr(row, 'reason', '') or ''),
        ' '.join(str(ref or '') for ref in list(getattr(row, 'support_refs', []) or [])),
        local_ref_text_for_supplemental_issue(dossier, local_ref),
    ])
    category_supported = supplemental_category_supported_by_text(reason_kind, local_text)
    regular_like = _is_regular_numbered_like_span(span, file_count)

    if reason_kind == _BANGUMI_TARGET_ABSENT_REASON_KIND:
        return _bangumi_target_absent_policy_issues(
            dossier,
            row,
            row_ref=row_ref,
            local_ref=local_ref,
            covered_main_refs=covered_main_refs,
            span=span,
            file_count=file_count,
            local_text=local_text,
            regular_like=regular_like,
        )

    if reason_kind == 'other_supplemental' and file_count > 1:
        return [_issue(
            row_ref,
            'regular_main_span_cannot_be_supplemental',
            'multi-file main rows cannot be accepted as generic supplemental/non-Bangumi accounting',
            related_refs=[local_ref, *covered_main_refs[:8]],
        )]

    if reason_kind != 'other_supplemental' and not category_supported:
        return [_issue(
            row_ref,
            'supplemental_text_unsupported',
            'supplemental accounting requires visible local text supporting the selected reason_kind',
            related_refs=[local_ref, *covered_main_refs[:8]],
        )]

    if reason_kind == 'other_supplemental' and not category_supported:
        return [_issue(
            row_ref,
            'supplemental_text_unsupported',
            'generic supplemental accounting requires explicit visible extra/bonus/supplemental evidence',
            related_refs=[local_ref, *covered_main_refs[:8]],
        )]

    if file_count > 1 and reason_kind not in _MULTI_FILE_SUPPLEMENTAL_REASON_KINDS:
        return [_issue(
            row_ref,
            'regular_main_span_cannot_be_supplemental',
            'multi-file main rows require a specific supplemental reason_kind, not a generic exclusion',
            related_refs=[local_ref, *covered_main_refs[:8]],
        )]

    if file_count > _SMALL_MULTI_FILE_LIMIT and reason_kind in _SMALL_MULTI_FILE_REASON_KINDS:
        return [_issue(
            row_ref,
            'supplemental_span_too_broad',
            'broad multi-file main spans cannot be accepted as supplemental without a precise per-file extra category',
            related_refs=[local_ref, *covered_main_refs[:8]],
        )]

    if file_count > 1 and regular_like and not _each_file_supports_category(dossier, covered_main_refs, reason_kind):
        return [_issue(
            row_ref,
            'regular_main_span_cannot_be_supplemental',
            'regular numbered main spans cannot be accepted as supplemental unless each covered file visibly carries the supplemental category',
            related_refs=[local_ref, *covered_main_refs[:8]],
        )]

    return []
