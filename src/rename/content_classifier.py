import re
from pathlib import Path

from .utils import PROMO_TAGS


SUPPLEMENTAL_FOLDER_NAMES = {
    'extras',
    'extra',
    'bonus',
    'sps',
    'specials',
    'creditless op-ed',
    'creditless op',
    'creditless ed',
    '映像特典',
    '特典',
}

SUPPLEMENTAL_PATTERNS = (
    r'(^|[^a-z0-9])extras?([^a-z0-9]|$)',
    r'(^|[^a-z0-9])bonus([^a-z0-9]|$)',
    r'(^|[^a-z0-9])menu([^a-z0-9]|$)',
    r'(^|[^a-z0-9])trailer([^a-z0-9]|$)',
    r'(^|[^a-z0-9])teaser([^a-z0-9]|$)',
    r'(^|[^a-z0-9])preview([^a-z0-9]|$)',
    r'(^|[^a-z0-9])commentary([^a-z0-9]|$)',
    r'(^|[^a-z0-9])interview([^a-z0-9]|$)',
    r'(^|[^a-z0-9])talk([^a-z0-9]|$)',
    r'(^|[^a-z0-9])making([^a-z0-9]|$)',
    r'(^|[^a-z0-9])radio([^a-z0-9]|$)',
    r'(^|[^a-z0-9])bar([^a-z0-9]|$)',
    r'(^|[^a-z0-9])live([^a-z0-9]|$)',
    r'(^|[^a-z0-9])digest([^a-z0-9]|$)',
    r'(^|[^a-z0-9])(?:op|ed)(?:\d{0,2}|v\d)?([^a-z0-9]|$)',
    r'(^|[^a-z0-9])pv\d*([^a-z0-9]|$)',
    r'(^|[^a-z0-9])theater[\s._-]*greeting([^a-z0-9]|$)',
    r'(^|[^a-z0-9])stage[\s._-]*(?:greeting|event)([^a-z0-9]|$)',
    r'(^|[^a-z0-9])greeting([^a-z0-9]|$)',
    r'(^|[^a-z0-9])lecture([^a-z0-9]|$)',
    r'(^|[^a-z0-9])web[\s._-]*(?:broadcast|program|stream)([^a-z0-9]|$)',
    r'(^|[^a-z0-9])(?:announcement|notice)([^a-z0-9]|$)',
    r'(^|[^a-z0-9])after[\s._-]*movie([^a-z0-9]|$)',
    r'(^|[^a-z0-9])yokoku([^a-z0-9]|$)',
    r'(^|[^a-z0-9])story[\s._-]*summary([^a-z0-9]|$)',
    r'(^|[^a-z0-9])main[\s._-]*story[\s._-]*straight[\s._-]*play([^a-z0-9]|$)',
    r'(^|[^a-z0-9])game[\s._-]*anime([^a-z0-9]|$)',
    r'全話[\s._-]*ぶっ続け',
    r'(^|[^a-z0-9])info\d*([^a-z0-9]|$)',
    r'(^|[^a-z0-9])theme[\s._-]*song([^a-z0-9]|$)',
    r'(^|[^a-z0-9])recitation[\s._-]*drama([^a-z0-9]|$)',
    r'(^|[^a-z0-9])short[\s._-]*animation([^a-z0-9]|$)',
    r'(^|[^a-z0-9])mini[\s._-]*(?:anime|theater|gekijyo|gekijou)([^a-z0-9]|$)',
    r'(^|[^a-z0-9])chibi(?:[\s._-]*gekij(?:yo|ou))?([^a-z0-9]|$)',
    r'(^|[^a-z0-9])travel(?:[\s._-]*diary|[\s._-]*special)?(?:\s*#?\d+)?([^a-z0-9]|$)',
    r'memorial[\s._-]*note',
    r'tv[\s._-]*spot',
    r'短(?:編|篇)[\s._-]*(?:アニメ|anime|动画|動畫)',
    r'旅#\d+',
    r'オリジナル脚本',
    r'設定画',
    r'デザイン画',
    r'完成披露イベント',
    r'特別先行版',
    r'先行上映',
    r'振り返り',
    r'[「『][^」』]{0,24}(?:記録|軌跡)[0-9０-９]*[」』]',
    r'(?:production|making|behind[\s._-]*the[\s._-]*scenes)[\s._-]*(?:record|documentary|video|movie)?\d*',
    r'(?:documentary|chronicle)[\s._-]*(?:record|video|movie)?\d*',
    r'配信用ショートストーリー',
    r'公開直前特別番組',
    r'直前特別番組',
    r'出張版',
    r'特別番組',
    r'全[一二三四五六七八九十0-9]+章の軌跡',
    r'映像特典',
    r'特典',
    r'メニュー',
    r'ノ[\s._-]*クレジット[\s._-]*(?:op|ed|ＯＰ|ＥＤ)?',
    r'ノンクレジット[\s._-]*(?:op|ed|ＯＰ|ＥＤ)?',
    r'予告',
    r'番宣',
    r'舞台[\s._-]*挨拶',
    r'講座',
    r'講義',
    r'告知',
    r'上映[\s._-]*記念',
)

EXPLICIT_SPECIAL_CUE_PATTERNS = (
    r'(?i)\bS00(?:E\d{1,3})?\b',
    r'(?i)\b(?:OVA|OAD|SP)\s*\d{0,3}\b',
    r'(?i)(?<![a-z0-9])EX(?![a-z0-9])',
    r'(?i)\bSPECIALS?\b',
    r'(?i)\bTRUE[\s._-]*END\b',
    r'(?i)\bANOTHER[\s._-]*END\b',
    r'(?i)\bFINAL[\s._-]*ACT\b',
    r'(?i)\bKANKETSU[\s._-]*HEN\b',
    r'完結[\s._-]*編',
    r'(?i)\bDIRECTOR[\s._-]*\'?S?[\s._-]*CUT(?:[\s._-]*VER\.?|SION)?\b',
    r'(?i)(?<![a-z0-9])TOKUBETSU[\s._-]*HEN(?![a-z0-9])',
    r'(?<!\d)0{1,2}(?!\d)',
    r'(?<!\d)\d{1,2}\.5(?!\d)',
    r'第\s*0+\s*[话話集]',
    r'(?i)(?:bundled|limited|bd.?付|bd.?付き)[\s._-]*(?:episode|ova|oad|special)',
    r'番外',
    r'特别',
    r'特別',
)


def is_promotional_content_name(filename: str) -> bool:
    normalized_filename = re.sub(r'[\[\]\(\)\s\._\-]+', ' ', filename).strip()
    compact_filename = re.sub(r'[\[\]\(\)\s\._\-]+', '', filename).casefold()

    for tag in PROMO_TAGS:
        pattern = rf'[\[\(\s\._\-]{re.escape(tag)}\d*[\]\)\s\._\-]'
        if re.search(pattern, filename, re.IGNORECASE):
            return True
        if filename.upper().startswith(tag.upper()):
            return True

        normalized_tag = re.sub(r'[\[\]\(\)\s\._\-]+', ' ', tag).strip()
        compact_tag = re.sub(r'[\[\]\(\)\s\._\-]+', '', tag).casefold()
        if normalized_tag and re.search(
            rf'(?<!\w){re.escape(normalized_tag)}\d*(?!\w)',
            normalized_filename,
            re.IGNORECASE,
        ):
            return True
        if len(compact_tag) >= 4 and compact_tag in compact_filename:
            return True
    return False


def is_supplemental_video_path(path_text: str) -> bool:
    normalized_path = path_text.replace('\\', '/')
    file_name = Path(normalized_path).name
    if is_promotional_content_name(file_name):
        return True

    relative_parts = [part.casefold() for part in Path(normalized_path).parts[:-1]]
    if any(part in SUPPLEMENTAL_FOLDER_NAMES for part in relative_parts):
        return True

    text = f'{normalized_path} {file_name}'.casefold()
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in SUPPLEMENTAL_PATTERNS)


def has_explicit_special_cue(path_text: str) -> bool:
    normalized_path = path_text.replace('\\', '/')
    file_name = Path(normalized_path).name
    text = f'{normalized_path} {file_name}'
    return any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in EXPLICIT_SPECIAL_CUE_PATTERNS
    )
