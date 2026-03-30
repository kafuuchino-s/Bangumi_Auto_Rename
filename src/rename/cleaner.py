import re
from typing import List, Optional, Tuple

from ..logger import logger
from .utils import PROMO_TAGS

BRACKET_PATTERNS = [
    r'\[.*?\]',
    r'【.*?】',
    r'《.*?》',
    r'<.*?>',
    r'\(.*?\)',
    r'（.*?）',
]

SEASON_PATTERNS = [
    r' (I{2,3})',
    r' (I{1,3}V)',
    r' (VI{2,3})',
    r'S([\d]{1,2})',
    r'第([\d一二三四五六七八九零]{1,2})(季|部分|部)',
    r'([\d]{1,2})nd Season',
    r'Season ([\d]{1,2})',
    r' ([\d]{1,2}) ',
    r'(First|Second|Third|Fourth|Fifth) Season',
]

EPISODE_PATTERNS = [
    r'[Ee]([\d]{1,2})',
    r'第([\d一二三四五六七八九零]{1,2})话',
    r'([\d]{1,2})[Ee]pisode',
    r'([\d]{1,2})[Ee]ps',
    r'\[(\d{1,2})\]',
]

KEYWORDS = [
    '01',
    '1080P',
    'FLAC',
    '简繁',
    '外挂',
    'MKV',
    'MP4',
    'TV',
    '全集',
    'HEVC',
    '8bit',
    '10bit',
    '720P',
    '2160P',
    '4K',
    'BD',
    'RIP',
    'DBD-raws',
]


def _normalize_movie_query_text(value: str) -> str:
    value = value.strip()
    if not value:
        return ""

    value = re.sub(r'[：:·•|｜/]+', ' ', value)
    value = re.sub(r'\s+', ' ', value)
    return value.strip(' -_:.')


def build_movie_search_queries(
    title: str,
    collection_name: Optional[str] = None,
) -> List[str]:
    """根据电影标题构建更稳健的 TMDB 查询候选。"""
    queries: List[str] = []

    def append_query(value: Optional[str]) -> None:
        if not value:
            return

        normalized = _normalize_movie_query_text(value)
        if not normalized or normalized in queries:
            return

        queries.append(normalized)

    raw_title = _normalize_movie_query_text(title)
    append_query(raw_title)

    title_no_prefix = re.sub(
        r'^(剧场版|劇場版|Movie|movie|Film|film|Theatrical|theatrical)\s+',
        '',
        raw_title,
        flags=re.IGNORECASE,
    )
    append_query(title_no_prefix)

    normalized_title = title_no_prefix
    normalized_title = re.sub(
        r'第\s*([0-9零〇一二三四五六七八九十百]+)\s*[章节話话篇部]\s*',
        r'\1 ',
        normalized_title,
    )
    normalized_title = re.sub(
        r'\b(?:chapter|chap\.?)\s*([0-9]+)\b',
        r'\1 ',
        normalized_title,
        flags=re.IGNORECASE,
    )
    append_query(normalized_title)

    subtitle_variants = [
        re.sub(r'^.+?[：:]\s*', '', title_no_prefix),
        re.sub(r'^.+?\s+-\s+', '', title_no_prefix),
        re.sub(r'^.+?\s+/\s+', '', title_no_prefix),
    ]
    # 处理无空格斜线："A/B" 拆成 A 和 B 两个候选
    slash_match = re.search(r'^(.+?)\s*/\s*(.+)$', title_no_prefix)
    if slash_match:
        subtitle_variants.append(slash_match.group(1).strip())
        subtitle_variants.append(slash_match.group(2).strip())
    for variant in subtitle_variants:
        append_query(variant)

    if collection_name:
        normalized_collection = _normalize_movie_query_text(collection_name)
        append_query(normalized_collection)

        suffix_candidates = []
        for source in (title_no_prefix, normalized_title):
            suffix = source
            if normalized_collection:
                escaped = re.escape(normalized_collection)
                suffix = re.sub(
                    rf'^({escaped})\s*[：:：\-–—/｜|]*\s*',
                    '',
                    suffix,
                    flags=re.IGNORECASE,
                )
                suffix = re.sub(
                    rf'\b({escaped})\b',
                    '',
                    suffix,
                    flags=re.IGNORECASE,
                )
            suffix_candidates.append(suffix)

        for suffix in suffix_candidates:
            cleaned_suffix = _normalize_movie_query_text(suffix)
            if not cleaned_suffix:
                continue
            append_query(cleaned_suffix)
            append_query(f"{normalized_collection} {cleaned_suffix}")

    return queries


def is_promotional_content(filename: str) -> bool:
    """
    检测文件是否为宣传内容（NCOP、NCED、PV、CM 等）

    这些内容通常不在 TMDB 的 Season 0 中，应该跳过处理。

    Args:
        filename: 文件名

    Returns:
        True 如果是宣传内容，否则 False
    """
    for tag in PROMO_TAGS:
        # 使用边界匹配，避免误判
        # 匹配格式如: [NCOP], (PV01), [CM 01], NCOP.mkv 等
        pattern = rf'[\[\(\s\._]{re.escape(tag)}\d*[\]\)\s\._]'
        if re.search(pattern, filename, re.IGNORECASE):
            return True
        # 也检查文件名开头的情况
        if filename.upper().startswith(tag.upper()):
            return True
    return False


def _clean_title_case_insensitive(title: str):
    # 将关键词和标题转换为小写进行匹配
    lower_keywords = [kw.lower() for kw in KEYWORDS]
    j = '|'.join(re.escape(kw) for kw in lower_keywords)
    keyword_regex = re.compile(j)

    # 遍历所有括号类型
    for pattern in BRACKET_PATTERNS:
        # 查找所有匹配的括号内容
        matches = re.findall(pattern, title)  # 保留原始大小写内容
        for match in matches:
            # 转为小写进行匹配
            if keyword_regex.search(match.lower()):
                title = title.replace(match, '')  # 删除原始大小写内容

    # 返回清理后的标题
    return title.strip()[1:-1]


def remove_tag(title: str, skip=False):
    '''
    该步骤将带括号的文件名中，包含【指定关键词】的【任意括号】内容删除。

    [LoliHouse] Shangri / 香格里拉 [WebRip 1080p HEVC-10bit AAC]【简繁内封字幕】

    将会变为

    Shangri / 香格里拉

    如果指定`skip=True`，则会保留第二个匹配的括号，将会变为

    Shangri / 香格里拉 [WebRip 1080p HEVC-10bit AAC]

    当指定文件夹名字是下面类型的，会很有用

    [LoliHouse] [Shangri / 香格里拉] [WebRip 1080p HEVC-10bit AAC]【简繁内封字幕】
    '''
    s = title
    if skip:
        # 创建一个字典来追踪每种括号的匹配次数
        counts = {pattern: 0 for pattern in BRACKET_PATTERNS}

        # 定义替换函数，追踪匹配次数并决定是否保留第二个匹配
        def replace_match(pattern, match):
            counts[pattern] += 1
            # 保留每种括号的第二个匹配，否则去除
            if counts[pattern] == 2:
                return match.group(0)
            else:
                return ''

        # 对每个模式应用相应的匹配逻辑
        for pattern in BRACKET_PATTERNS:
            s = re.sub(pattern, lambda m: replace_match(pattern, m), s)
    else:
        # 不启用跳过规则，正常删除所有匹配项
        for pattern in BRACKET_PATTERNS:
            s = re.sub(pattern, '', s)

    remove_tag_s = s.strip()
    logger.info(f'[移除标签工具] {remove_tag_s}')
    if not remove_tag_s:
        s = _clean_title_case_insensitive(title)

    return s.strip()


def divide_by_year(filename: str) -> Tuple[str, int]:
    '''
    该步骤将文件名中，按照年份分割，并提取年份前面的内容。

    Shangri / 香格里拉.2022

    将会变为

    Shangri / 香格里拉.
    '''
    match = re.findall(r'\d+', filename)
    for i in match:
        if 2035 >= float(i) >= 1901:
            name = filename.split(i)
            return name[0], int(i)
    else:
        return filename, 0


def remove_season(s: str):
    '''
    该步骤将文件名中, 类似季度的内容剔除

    Shangri / 香格里拉.S01E01

    将会变为

    Shangri / 香格里拉.E01
    '''
    for p in SEASON_PATTERNS:
        s = re.sub(p, '', s)
    return s.strip()


def remove_episode(s: str):
    '''
    该步骤将文件名中, 类似剧集的内容剔除
    Shangri / 香格里拉.E01
    将会变为
    Shangri / 香格里拉.
    '''
    for p in EPISODE_PATTERNS:
        s = re.sub(p, '', s)
    return s.strip()


def is_chinese_percentage_sufficient(text: str):
    '''
    用于判断字符串中 中文字符的比例是否至少占 25%
    '''
    chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
    chinese_chars = chinese_pattern.findall(text)
    total_chars = len(text)
    chinese_char_count = len(chinese_chars)
    if total_chars > 0:
        return chinese_char_count / total_chars >= 0.25
    else:
        return False


def extract_video_format(filename: str) -> Optional[str]:
    """
    从文件名提取视频格式 (1080p/720p/4K 等)

    示例:
        "[SubGroup] Title [1080p].mkv" -> "1080p"
        "Title.2160p.mkv" -> "4K"
        "Title [4K HDR].mkv" -> "4K"
        "Title.720p.HEVC.mkv" -> "720p"

    Returns:
        标准化的格式字符串 (如 "1080p", "720p", "4K") 或 None
    """
    # 按优先级匹配
    format_patterns = [
        (r'2160[pP]', '4K'),
        (r'4[kK]', '4K'),
        (r'1080[pP]', '1080p'),
        (r'720[pP]', '720p'),
        (r'480[pP]', '480p'),
    ]

    for pattern, normalized in format_patterns:
        if re.search(pattern, filename):
            return normalized

    return None


def extract_part(filename: str) -> Optional[str]:
    """
    从文件名提取分集信息 (Part1/Part2 等)

    示例:
        "Title - Part 1.mkv" -> "Part1"
        "Title pt2.mkv" -> "Part2"
        "Title (Part A).mkv" -> "PartA"
        "Title - 前编.mkv" -> "Part1"
        "Title - 后编.mkv" -> "Part2"

    Returns:
        标准化的分集字符串 (如 "Part1", "Part2") 或 None
    """
    # 英文 Part 模式 (支持空格、横线、下划线、点分隔)
    match = re.search(r'[Pp]art[\s\-_\.]*([1-9A-Za-z])', filename)
    if match:
        return f"Part{match.group(1).upper()}"

    # pt1, pt2 模式
    match = re.search(r'[Pp]t[\s\-_]*([1-9])', filename)
    if match:
        return f"Part{match.group(1)}"

    # 日语/中文模式
    if re.search(r'前[篇编]', filename):
        return "Part1"
    if re.search(r'[後后][篇编]', filename):
        return "Part2"
    if re.search(r'上[篇编]', filename):
        return "Part1"
    if re.search(r'下[篇编]', filename):
        return "Part2"
    if re.search(r'中[篇编]', filename):
        return "Part2"

    return None
