from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalSupplementalDecision:
    is_supplemental: bool
    reason_kind: str = ''
    rule_id: str = ''
    reason: str = ''


_VIDEO_EXTRA_RULES: list[tuple[str, str, str, re.Pattern[str]]] = [
    (
        'wiki_sample',
        'sample',
        'WiKi.sample local sample asset',
        re.compile(r'(?i)WiKi\.sample'),
    ),
    (
        'special_ending_movie',
        'creditless_op_ed',
        'special ending movie',
        re.compile(r'(?i)Special\s+Ending\s+Movie'),
    ),
    (
        'non_telop',
        'creditless_op_ed',
        'non-telop/creditless video asset',
        re.compile(r'(?i)(?:Non[\s._-]*Telop|No[\s._-]*Telop|ノンテロップ)'),
    ),
    (
        'bracketed_cm',
        'pv_cm',
        'bracketed CM asset',
        re.compile(r'(?i)\[(?:(?:TV|BD|Blu-ray)\s*)?CM\s*\d{2,3}\]'),
    ),
    (
        'bracketed_teaser',
        'trailer',
        'bracketed teaser asset',
        re.compile(r'(?i)\[Teaser[^\]]*\]'),
    ),
    (
        'bracketed_pv',
        'pv_cm',
        'bracketed PV asset',
        re.compile(r'(?i)\[PV[^\]]*\]'),
    ),
    (
        'bracketed_preview',
        'pv_cm',
        'bracketed preview asset',
        re.compile(r'(?i)\[(?:(?:TV|BD|Blu-ray)\s*)?Preview(?:\s*\d{0,3}(?:[_-]\d+)?)?(?:[\s_-][^\]]*)?\]'),
    ),
    (
        'bracketed_creditless_op_ed',
        'creditless_op_ed',
        'bracketed NCOP/NCED asset',
        re.compile(r'(?i)\[NC(?:OP|ED)[^\]]*\]'),
    ),
    (
        'bracketed_recap',
        'non_episode_video',
        'bracketed recap asset',
        re.compile(r'(?i)\[S\d+\s+Recap(?:\s+\d+)?\]'),
    ),
    (
        'recap_token',
        'non_episode_video',
        'recap asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])Recap(?:\d{0,3}|_ALL)?(?![A-Za-z0-9])'),
    ),
    (
        'jp_recap_avan',
        'non_episode_video',
        'Japanese recap/avant segment asset',
        re.compile(r'(?:振り返り|ふり返り|振返り)\s*アバン'),
    ),
    (
        'interview_video_token',
        'bonus_video',
        'interview-video asset',
        re.compile(r'(?i)(?:^|[/\[\]\s._-])IV\d{1,3}(?:$|[/\[\]\s._-])'),
    ),
    (
        'bonus_token',
        'bonus_video',
        'bonus video asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])Bonus(?:\d{0,3}|_ALL)?(?![A-Za-z0-9])'),
    ),
    (
        'creditless_textless_token',
        'creditless_op_ed',
        'creditless/textless OP/ED asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])(?:Creditless|Textless|Clean[\s._-]*(?:OP|ED))(?:\d{0,3}|_ALL)?(?![A-Za-z0-9])'),
    ),
    (
        'talk_token',
        'bonus_video',
        'cast/staff/after talk asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])(?:Cast|Staff|After)[\s._-]*Talk(?:\d{0,3}|_ALL)?(?![A-Za-z0-9])'),
    ),
    (
        'making_featurette_token',
        'bonus_video',
        'making/featurette asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])(?:Making|Featurette)(?:\d{0,3}|_ALL)?(?![A-Za-z0-9])'),
    ),
    (
        'spot_token',
        'pv_cm',
        'spot promo asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])SPOT(?:\d{0,3}|_ALL)?(?![A-Za-z0-9])'),
    ),
    (
        'navigation_token',
        'menu_or_navigation',
        'navigation asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])Navigation(?:\d{0,3}|_ALL)?(?![A-Za-z0-9])'),
    ),
    (
        'menu_token',
        'menu_or_navigation',
        'menu/navigation asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])Menu(?![A-Za-z0-9])'),
    ),
    (
        'preview_token',
        'pv_cm',
        'preview asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])Preview(?![A-Za-z0-9])'),
    ),
    (
        'promo_or_disc_token',
        'pv_cm',
        'promo/navigation token asset',
        re.compile(
            r'(?i)(?<![A-Za-z0-9])(?:NC)?'
            r'(?:OP|ED|Advice|Trailer|PV|CM|Info|EDPV|SongSpot|BDSpot)'
            r'(?:\d{0,2}|_ALL)(?![A-Za-z0-9])'
        ),
    ),
    (
        'disc_token',
        'duplicate_packaging',
        'disc packaging asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])Disc(?:\d{0,2}|_ALL)?(?![A-Za-z0-9])'),
    ),
    (
        'menu_upper_token',
        'menu_or_navigation',
        'menu/navigation asset',
        re.compile(r'(?i)(?<![A-Za-z0-9])MENU(?:\d{0,2}|_ALL)?(?![A-Za-z0-9])'),
    ),
]

_CJK_EXTRA_TOKENS: list[tuple[str, str, str, str]] = [
    ('cjk_opening', 'creditless_op_ed', '片头', 'opening asset'),
    ('cjk_ending', 'creditless_op_ed', '片尾', 'ending asset'),
    ('jp_creditless', 'creditless_op_ed', 'ノンクレジット', 'creditless OP/ED asset'),
    ('jp_creditless_alt', 'creditless_op_ed', 'ノクレジット', 'creditless OP/ED asset'),
    ('jp_non_telop', 'creditless_op_ed', 'ノンテロップ', 'creditless OP/ED asset'),
]

_SUPPLEMENTAL_SEGMENT_RULES: list[tuple[str, str, str, re.Pattern[str]]] = [
    (
        'hidden_work_dir',
        'other_supplemental',
        'hidden work directory',
        re.compile(r'(?i)^__\w{6}$'),
    ),
    (
        'cds_dir',
        'other_supplemental',
        'CD assets directory',
        re.compile(r'(?i)^(?:CDs?|CD)$'),
    ),
    (
        'scans_dir',
        'other_supplemental',
        'scan assets directory',
        re.compile(r'(?i)^Scans?$'),
    ),
    (
        'bonus_dir',
        'bonus_video',
        'bonus assets directory',
        re.compile(r'(?i)^Bonus$'),
    ),
    (
        'extras_dir',
        'other_supplemental',
        'extras assets directory',
        re.compile(r'(?i)^Extras?$'),
    ),
    (
        'specials_dir',
        'bonus_video',
        'specials assets directory',
        re.compile(r'(?i)^specials$'),
    ),
    (
        'menu_dir',
        'menu_or_navigation',
        'menu/navigation directory',
        re.compile(r'(?i)^Menu(?:\s*\(.+\))?$'),
    ),
    (
        'logo_dir',
        'pv_cm',
        'logo asset directory',
        re.compile(r'(?i)^Logo$'),
    ),
    (
        'preview_dir',
        'pv_cm',
        'preview asset directory',
        re.compile(r'(?i)^Preview$'),
    ),
    (
        'mv_dir',
        'pv_cm',
        'music video directory',
        re.compile(r'(?i)^mv$'),
    ),
]

_SUPPLEMENTAL_SEGMENT_SUBSTRINGS: list[tuple[str, str, str, str]] = [
    ('eizou_tokuten_dir', 'bonus_video', '映像特典', 'bonus video assets directory'),
    ('eizou_dir', 'bonus_video', '映像', 'video extras directory'),
    ('tokuten_cd_dir', 'other_supplemental', '特典CD', 'bonus CD assets directory'),
]


def classify_local_video_supplemental(relative_path: object, *, is_video: bool = True) -> LocalSupplementalDecision:
    if not is_video:
        return LocalSupplementalDecision(False)

    path = _normalize_path(relative_path)
    basename = path.rsplit('/', 1)[-1] if path else ''
    search_text = f'{path} {basename}'.strip()

    for segment in [part for part in path.split('/') if part]:
        for rule_id, reason_kind, reason, pattern in _SUPPLEMENTAL_SEGMENT_RULES:
            if pattern.search(segment):
                return LocalSupplementalDecision(True, reason_kind, rule_id, reason)
        for rule_id, reason_kind, token, reason in _SUPPLEMENTAL_SEGMENT_SUBSTRINGS:
            if token in segment:
                return LocalSupplementalDecision(True, reason_kind, rule_id, reason)

    for rule_id, reason_kind, reason, pattern in _VIDEO_EXTRA_RULES:
        if pattern.search(search_text):
            if rule_id == 'promo_or_disc_token':
                token = (pattern.search(search_text).group(0) or '').upper()
                reason_kind = _reason_kind_for_promo_token(token)
            return LocalSupplementalDecision(True, reason_kind, rule_id, reason)

    for rule_id, reason_kind, token, reason in _CJK_EXTRA_TOKENS:
        if token in search_text:
            return LocalSupplementalDecision(True, reason_kind, rule_id, reason)

    return LocalSupplementalDecision(False)


def _reason_kind_for_promo_token(token: str) -> str:
    if token.startswith(('NCOP', 'NCED', 'OP')) or token == 'ED' or re.match(r'(?i)^ED\d{0,2}$', token):
        return 'creditless_op_ed'
    if token.startswith('TRAILER'):
        return 'trailer'
    return 'pv_cm'


def _normalize_path(value: object) -> str:
    return str(value or '').strip().replace('\\', '/')
