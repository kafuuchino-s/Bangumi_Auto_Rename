import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..rename.cleaner import remove_episode, remove_season
from ..rename.utils import PROMO_TAGS, SPECIAL_FOLDER_NAMES


def _coerce_mapping_sequence(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []

    items: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(dict(item))
    return items


def _coerce_string_sequence(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []

    items: list[str] = []
    for item in value:
        text = item.strip() if isinstance(item, str) else str(item).strip()
        if text:
            items.append(text)
    return items


def _looks_like_special_path(path_value: str) -> bool:
    normalized = str(path_value or "").replace("\\", "/").casefold()
    if any(folder in normalized for folder in SPECIAL_FOLDER_NAMES):
        return True
    upper_value = Path(str(path_value or "")).name.upper()
    if any(tag.upper() in upper_value for tag in PROMO_TAGS):
        return True
    if re.search(r"\b(?:ova|oad|special|sp)\b", normalized, re.IGNORECASE):
        return True
    if re.search(r"(?<!\d)(?:0|\d+\.5)(?!\d)", upper_value):
        return True
    return False


def _extract_episode_hints(
    local_files: Sequence[Mapping[str, object]],
) -> tuple[set[int], bool]:
    episode_numbers: set[int] = set()
    should_include_season_zero = False
    episode_patterns = [
        re.compile(r"\[(\d{1,3})(?:v\d+)?\]", re.IGNORECASE),
        re.compile(r"\b(?:ep|episode|e)\s*0*(\d{1,3})(?:\.5)?\b", re.IGNORECASE),
        re.compile(r"第\s*0*(\d{1,3})(?:\.5)?\s*[话話集]"),
        re.compile(r"(?<![A-Za-z0-9])(\d{1,3})(?:\.5)?(?![A-Za-z0-9])"),
    ]

    for file_info in local_files:
        path_value = str(file_info.get("path") or file_info.get("filename") or "")
        if not path_value:
            continue
        if _looks_like_special_path(path_value):
            should_include_season_zero = True

        candidates = [
            Path(path_value).stem,
            remove_season(Path(path_value).stem),
            remove_episode(remove_season(Path(path_value).stem)),
        ]
        for candidate in candidates:
            for pattern in episode_patterns:
                for match in pattern.finditer(candidate):
                    episode_num = int(match.group(1))
                    if 0 < episode_num <= 999:
                        episode_numbers.add(episode_num)
            if re.search(r"(?<!\d)\d+\.5(?!\d)", candidate):
                should_include_season_zero = True
    return episode_numbers, should_include_season_zero


def _select_prompt_seasons(
    anime_info: Mapping[str, object],
    local_files: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    seasons = _coerce_mapping_sequence(anime_info.get("seasons", []))
    if not seasons:
        return []

    preferred_seasons: set[int] = set()
    episode_numbers, should_include_season_zero = _extract_episode_hints(local_files)
    has_non_special_file = False

    name_candidates = [
        str(anime_info.get("name") or ""),
        str(anime_info.get("original_name") or ""),
        str(anime_info.get("original_title") or ""),
    ]
    for file_info in local_files:
        path_value = str(file_info.get("path") or file_info.get("filename") or "")
        if not path_value:
            continue
        if not _looks_like_special_path(path_value):
            has_non_special_file = True
        path_name = Path(path_value).stem
        for text in [path_value, path_name, remove_episode(path_name)] + name_candidates:
            for match in re.finditer(r"\b(?:season|s)\s*0*([0-9]{1,2})\b", text, re.IGNORECASE):
                preferred_seasons.add(int(match.group(1)))
            for match in re.finditer(r"第\s*([0-9]{1,2})\s*季", text):
                preferred_seasons.add(int(match.group(1)))

    explicit_preferred_seasons = set(preferred_seasons)
    selected_numbers: set[int] = set(explicit_preferred_seasons)
    if should_include_season_zero:
        selected_numbers.add(0)

    if episode_numbers and not explicit_preferred_seasons:
        season_match_counts: dict[int, int] = {}
        cumulative_match_counts: dict[int, int] = {}
        ordered_non_special_seasons: list[tuple[int, int]] = []
        for season in seasons:
            season_number = season.get("season_number")
            if not isinstance(season_number, int):
                continue
            season_episodes = _coerce_mapping_sequence(season.get("episodes", []))
            episode_count_value = season.get("episode_count", 0)
            episode_numbers_in_season: set[int] = set()
            for ep in season_episodes:
                episode_number = ep.get("episode_number")
                if isinstance(episode_number, int) and episode_number > 0:
                    episode_numbers_in_season.add(episode_number)
            direct_match_count = len(episode_numbers_in_season & episode_numbers)
            if direct_match_count > 0:
                season_match_counts[season_number] = direct_match_count

            if season_number > 0:
                max_episode_number = max(episode_numbers_in_season, default=0)
                episode_count = (
                    episode_count_value
                    if isinstance(episode_count_value, int)
                    and episode_count_value > 0
                    else max_episode_number
                    if max_episode_number > 0
                    else len(episode_numbers_in_season)
                )
                if episode_count > 0:
                    ordered_non_special_seasons.append((season_number, episode_count))

        non_special_match_counts = {
            season_number: match_count
            for season_number, match_count in season_match_counts.items()
            if season_number != 0
        }

        if not non_special_match_counts and ordered_non_special_seasons:
            cumulative_start = 1
            for season_number, episode_count in sorted(ordered_non_special_seasons):
                cumulative_end = cumulative_start + episode_count - 1
                cumulative_hits = sum(
                    1
                    for episode_number in episode_numbers
                    if cumulative_start <= episode_number <= cumulative_end
                )
                if cumulative_hits > 0:
                    cumulative_match_counts[season_number] = cumulative_hits
                cumulative_start = cumulative_end + 1

            if cumulative_match_counts:
                best_cumulative_match_count = max(cumulative_match_counts.values())
                non_special_match_counts = {
                    season_number: match_count
                    for season_number, match_count in cumulative_match_counts.items()
                    if match_count == best_cumulative_match_count
                }

        if non_special_match_counts:
            best_match_count = max(non_special_match_counts.values())
            selected_numbers.add(
                min(
                    season_number
                    for season_number, match_count in non_special_match_counts.items()
                    if match_count == best_match_count
                )
            )
        elif has_non_special_file:
            return seasons

    if not selected_numbers or len(selected_numbers) >= len(seasons):
        return seasons

    filtered_seasons = [
        season for season in seasons if season.get("season_number") in selected_numbers
    ]
    return filtered_seasons or seasons


def _should_expand_tmdb_episode_details(
    local_files: Sequence[Mapping[str, object]],
    prompt_seasons: Sequence[Mapping[str, object]],
) -> bool:
    for season in prompt_seasons[:2]:
        episodes = _coerce_mapping_sequence(season.get("episodes", []))
        episode_count_value = season.get("episode_count", 0)
        episode_count = len(episodes) if episodes else episode_count_value if isinstance(episode_count_value, int) else 0
        if 0 < episode_count <= 50:
            return True

    if len(prompt_seasons) > 1:
        return True

    episode_numbers, should_include_season_zero = _extract_episode_hints(local_files)
    if should_include_season_zero:
        return True

    return len(episode_numbers) <= 2


def _should_expand_bangumi_episode_details(
    local_files: Sequence[Mapping[str, object]],
) -> bool:
    episode_numbers, should_include_season_zero = _extract_episode_hints(local_files)
    if should_include_season_zero:
        return True
    return len(episode_numbers) <= 2


def _is_video_path(path_value: str) -> bool:
    return Path(path_value).suffix.casefold() in {
        ".mkv",
        ".mp4",
        ".avi",
        ".m2ts",
        ".ts",
        ".mov",
        ".wmv",
        ".flv",
    }


def _build_tmdb_prompt_section(
    anime_info: Mapping[str, object],
    local_files: Sequence[Mapping[str, object]],
) -> str:
    seasons = _coerce_mapping_sequence(anime_info.get("seasons", []))
    prompt_seasons = _select_prompt_seasons(anime_info, local_files)
    expand_episode_details = _should_expand_tmdb_episode_details(local_files, prompt_seasons)

    lines = [
        "TMDB 全剧合法输出空间（同一 TV series 内不限制单季；最终只能逐字选择这里存在的 legal_node_id）："
    ]
    for season in seasons:
        season_num = season.get("season_number", 0)
        season_name = season.get("name", f"Season {season_num}")
        episode_count = season.get("episode_count", 0)
        episodes = _coerce_mapping_sequence(season.get("episodes", []))
        count = len(episodes) if episodes else episode_count if isinstance(episode_count, int) else 0
        if count > 0:
            lines.append(
                f"- Season {season_num}: {season_name} (共 {count} 集), legal_node_id=tmdb:S{season_num:02d}E01..tmdb:S{season_num:02d}E{count:02d}"
            )
        else:
            lines.append(f"- Season {season_num}: {season_name}")

    lines.append("")
    if expand_episode_details:
        lines.append("TMDB 全剧关键集目（S00/special 标题优先完整展开；普通季度按规模展开或给范围）：")
        for season in seasons:
            season_num = season.get("season_number", 0)
            season_name = season.get("name", f"Season {season_num}")
            episodes = _coerce_mapping_sequence(season.get("episodes", []))
            episode_count_value = season.get("episode_count", 0)
            episode_count = len(episodes) if episodes else episode_count_value if isinstance(episode_count_value, int) else 0
            lines.append(f"【Season {season_num}】{season_name} (共 {episode_count} 集)")
            if episodes:
                should_expand_season = (
                    season_num == 0
                    or len(episodes) <= 50
                    or season in prompt_seasons
                )
                selected_episodes = episodes if should_expand_season else episodes[:8]
                for ep in selected_episodes:
                    ep_num_value = ep.get("episode_number", 0)
                    ep_num = ep_num_value if isinstance(ep_num_value, int) else 0
                    ep_name = ep.get("name", "")
                    node_id = f"tmdb:S{season_num:02d}E{ep_num:02d}"
                    lines.append(f"  {node_id}: {ep_name}")
                if len(episodes) > len(selected_episodes):
                    lines.append(
                        f"  ... 其余 {len(episodes) - len(selected_episodes)} 集未展开；"
                        f"可用 legal_node_id 仍为 tmdb:S{season_num:02d}E01.."
                        f"tmdb:S{season_num:02d}E{episode_count:02d}"
                    )
            elif episode_count > 0:
                if episode_count <= 50:
                    for ep_num in range(1, episode_count + 1):
                        lines.append(f"  tmdb:S{season_num:02d}E{ep_num:02d}")
                else:
                    lines.append(
                        f"  legal_node_id 范围: tmdb:S{season_num:02d}E01 - "
                        f"tmdb:S{season_num:02d}E{episode_count:02d}"
                    )
            lines.append("")
    else:
        lines.append("TMDB 全剧合法节点范围：")
        for season in seasons:
            season_num = season.get("season_number", 0)
            season_name = season.get("name", f"Season {season_num}")
            episode_count_value = season.get("episode_count", 0)
            episode_count = episode_count_value if isinstance(episode_count_value, int) else 0
            if episode_count > 0:
                lines.append(
                    f"- Season {season_num} {season_name}: "
                    f"legal_node_id=tmdb:S{season_num:02d}E01.."
                    f"tmdb:S{season_num:02d}E{episode_count:02d}"
                )
            else:
                lines.append(f"- Season {season_num} {season_name}")

    if len(prompt_seasons) < len(seasons):
        lines.append("提示: 本地文件可能混入 S00、OVA/OAD/SP、前后季或全局编号；不要把映射限制在高相关季度，必须在整部 TV series 的合法节点内选择。")
    return "\n".join(lines).strip() + "\n"


def _build_bangumi_prompt_section(
    bangumi_context: Mapping[str, object] | None,
    local_files: Sequence[Mapping[str, object]],
) -> str:
    if not bangumi_context:
        return "Bangumi 辅助上下文：不可用（本次按 TMDB-only 处理）\n"

    subjects = _coerce_mapping_sequence(bangumi_context.get("subjects", []))
    if not subjects:
        return "Bangumi 辅助上下文：不可用（本次按 TMDB-only 处理）\n"

    lines = [
        "Bangumi 辅助上下文（仅作辅助证据，不能直接决定最终季号）：",
        f"主条目 ID: {bangumi_context.get('selected_subject_id', '未知')}",
    ]
    reason = str(bangumi_context.get("selected_subject_reason") or "").strip()
    if reason:
        lines.append(f"主条目选择原因: {reason}")
    keywords = _coerce_string_sequence(bangumi_context.get("search_keywords", []))
    if keywords:
        lines.append(f"搜索词: {', '.join(str(item) for item in keywords[:6])}")

    expand_episode_details = _should_expand_bangumi_episode_details(local_files)
    subject_limit = 4 if expand_episode_details else 2

    for subject_item in subjects[:subject_limit]:
        subject = subject_item.get("subject", {})
        subject_mapping = dict(subject) if isinstance(subject, Mapping) else {}
        relation = subject_item.get("relation_to_main", "") or "main"
        lines.append(
            f"- subject_id={subject_mapping.get('id', '未知')} relation={relation} title={subject_mapping.get('name_cn') or subject_mapping.get('name') or '未知'}"
        )
        alt_name = subject_mapping.get("name") or ""
        if alt_name and alt_name != (subject_mapping.get("name_cn") or ""):
            lines.append(f"  原标题: {alt_name}")
        if subject_mapping.get("date"):
            lines.append(f"  放送日期: {subject_mapping.get('date')}")
        if subject_mapping.get("platform"):
            lines.append(f"  平台: {subject_mapping.get('platform')}")

        episodes = _coerce_mapping_sequence(subject_item.get("episodes", []))
        if not episodes:
            lines.append("  episodes: 无")
            continue

        if expand_episode_details:
            lines.append("  episodes:")
            for episode in episodes[:60]:
                episode_type = episode.get("type")
                episode_line = (
                    "    - "
                    f"sort={episode.get('sort', 0)} "
                    f"ep={episode.get('ep')} "
                    f"type={episode_type} "
                    f"airdate={episode.get('airdate') or ''} "
                    f"title={episode.get('name_cn') or episode.get('name') or ''}"
                )
                lines.append(episode_line)
            if len(episodes) > 60:
                lines.append(f"    ... 其余 {len(episodes) - 60} 条未展开")
        else:
            lines.append(f"  episodes: 共 {len(episodes)} 条（默认不展开明细）")

    lines.append(
        "Bangumi 使用规则：relation 只是辅助语义，不等于 TMDB season；最终输出只能使用上面 TMDB 中真实存在的 SxxExx；拿不准时宁可放到 unmatched_files。"
    )
    lines.append(
        "若文件名只出现 `OVA3 / SP3 / [13]` 这类顺序编号，可以把 Bangumi 的 `sort / ep / type / 标题 / 日期 / 时长 / desc` 当作辅助证据，先判断它是不是 special，再回到 TMDB 真实存在的 Season 0 条目；但 `OVA3` 不等于 `S00E03`，最终仍要按 TMDB 合法条目落点。"
    )
    return "\n".join(lines) + "\n"


def build_common_prompt(
    anime_info: Mapping[str, object],
    local_files: Sequence[Mapping[str, object]],
    bangumi_context: Mapping[str, object] | None = None,
) -> str:
    tmdb_info = f"""
动漫名称: {anime_info.get('name', '未知')}
首播日期: {anime_info.get('first_air_date', '未知')}
总季数: {anime_info.get('number_of_seasons', 0)}
总集数: {anime_info.get('number_of_episodes', 0)}
"""
    tmdb_details = _build_tmdb_prompt_section(anime_info, local_files)

    files_info = "本地视频文件信息（每行一个稳定 source_index + 可直接复制的 source_path，路径均为相对路径）:\n"
    top_level_dirs: list[str] = []
    video_local_files = [
        file_info
        for file_info in local_files
        if _is_video_path(str(file_info.get("path") or file_info.get("filename") or ""))
    ]
    for i, file_info in enumerate(video_local_files, 1):
        path_value = str(file_info.get("path") or "")
        duration_str = ""
        duration_value = file_info.get("duration")
        if isinstance(duration_value, (int, float)):
            duration_str = f" (时长: {duration_value:.1f}分钟)"
        files_info += f"  - [{i:03d}] source_index={i} source_path=`{path_value}`{duration_str}\n"

        parts = [part for part in path_value.split("/") if part]
        if len(parts) >= 2:
            top_dir = parts[0]
            if top_dir not in top_level_dirs:
                top_level_dirs.append(top_dir)

    if top_level_dirs:
        files_info += "顶层子目录: " + ", ".join(top_level_dirs[:12]) + "\n"
        files_info += "提示: 如果不同文件落在不同子目录（如 Disc/SP/OVA/NCOP/NCED/Extras），这些子目录语义通常很重要，不能忽略。\n"

    non_video_count = len(local_files) - len(video_local_files)
    if non_video_count > 0:
        files_info += f"已忽略 {non_video_count} 个非视频文件（如字幕/压缩包/附件），不参与本次 TV 映射。\n"

    bangumi_info = _build_bangumi_prompt_section(bangumi_context, video_local_files)

    prompt = f"""请分析以下动漫的本地文件与TMDB数据的对应关系：

{tmdb_info}

{tmdb_details}

{bangumi_info}

{files_info}

请根据以下规则进行映射。注意：本任务的方向是 **从 TMDB legal node 选择承载文件**，
不是从文件名反向生成 TMDB 集号。

1. **输出字段要求**：
   - 每个 `file_mapping` 项表示“某个 TMDB legal node 由哪个本地文件承载”，必须包含：`legal_node_id`, `source_index`, `episode_type`, `confidence`
   - `legal_node_id` 是映射主语，必须逐字复制上方 TMDB 列表里的实际节点，格式固定为 `tmdb:SxxEyy`；不要输出 `Season 1`、`S01E01`、`tv:0:...` 或任何自己生成的 ID
   - 不要填写或推导 `tmdb_season` / `tmdb_episode`；系统会从 `legal_node_id` 派生最终 TMDB 季集
   - `source_index` 是承载该 TMDB 节点的本地文件编号；`file_path` 为可选，若填写必须与 `source_index` 指向同一输入文件

2. **匹配优先级**（按顺序尝试）：
   - 先阅读 TMDB seasons / episodes / specials，确定有哪些可输出的 legal nodes
   - 对每个需要落地的 TMDB legal node，再从本地文件中选择最合适的承载文件
   - 本地编号、Bangumi 全局编号、文件名标题和日期都只是帮助选择承载文件的证据；不能用它们创造新的 TMDB episode number
   - 如果没有足够证据为某个 TMDB 节点选择承载文件，就不要输出该节点；如果某个文件找不到合法 TMDB 节点，就放入 `unmatched_files`

2. **Season 0 特典规则**：
   - OVA、OAD、SP、Special 等标签 → Season 0
   - 小数集数（如 5.5、12.5）→ Season 0（总集篇）
   - 第00集、E00、[00] → Season 0（序章/先行篇）
   - **重要**: 文件名中的 SP01、OVA01 不一定对应 S0E1，需要根据标题匹配
   - 如果 TMDB 条目是“有声小说 / audio drama / sound novel”类 special，而本地文件名更像 `Talk / Event / Cast / Seiyuu / Radio / Day Ver / Ending Talk / Recitation`，则不要强行映射，宁可放入 `unmatched_files`
   - `Part1/Part2` 只有在能明确证明它们是同一条 TMDB special 的拆分文件时才能映射；否则放入 `unmatched_files`

3. **多季度处理**：
   - 本地目录可能将多季合并，需要根据集数范围判断
   - 本地目录可能使用总集号（如 E14 可能是 S2E01）
   - 不同季度可能仅用名称区分（如 \"Okawari\"、\"Okaeri\" 等后缀）
   - `Vol.1 / Vol.2 / Disc / BD` 通常只是实体卷/包装单位，不表示该子目录内的 TMDB episode 从 E01 重新开始；如果文件名本身有连续正片编号（如 03、04、05、06），应把这些编号作为选择承载 TMDB 节点的证据，而不是每个 Vol 都选择 `tmdb:S01E01` 起步
   - 如果根目录正片文件使用连续全局编号，且数量/目录篇章能对应某个非 Season 0 的 TMDB season，必须优先把这些正片映射到该 regular season 的 E01..E{{N}}；不要因为存在 SPs/Extras 子目录就把正片映射到 Season 0
   - 当同一目录同时有根目录正片和 `SPs/OVA/Extras/NCOP/NCED` 等特典子目录时，先覆盖根目录正片；Season 0 只用于有明确 special 标题/语义的特典文件

4. **只输出匹配到的文件**，未匹配到 TMDB 的文件不要输出

5. **路径约束（必须满足）**：
   - `file_mapping` 中优先填写 `source_index`
   - `source_index` 必须直接使用上面 `[编号]` 里的数字（例如 `[001]` 就填 `1`）
   - 若已提供正确 `source_index`，`file_path` 可留空或填写与该编号完全一致的原始相对路径
   - 如果同时填写 `source_index` 和 `file_path`，两者必须指向同一条输入文件
   - `file_mapping.file_path` 若填写，必须逐字复用输入里的相对路径
   - 只能从上面 `[编号] source_index=... source_path=` 列表里原样选择，禁止自行拼接、改写或脑补任何新路径
   - 如果你要输出某个文件，先定位对应的 `[编号]` 行；最佳做法是直接返回该行的 `source_index`
   - 若你额外填写 `file_path`，必须逐字复制该行反引号中的 `source_path`
   - 必须复制完整相对路径，不能截断为 basename，也不能省略中间子目录
   - 路径中的目录名、文件名、以及 `[]` 内的版本标签都属于路径的一部分；`Ma10p / Hi10p / x265 / x264 / flac / aac` 等字样也必须逐字照抄，不能替换成“更统一”的版本
   - `[]` 内外的 token 顺序、空格和简称都必须保持原样；不要把短标签/简称扩写回主标题，也不要把多个片段重新拼成新的文件名
   - 例如：若输入是 `.../SPs/[Group] MagiRepo [01][Ma10p_1080p][x265_flac].mkv`，就不能改写成 `.../[Group] Magia Record [MagiRepo 01][Ma10p_1080p][x265_flac].mkv`
   - 例如：若输入是 `.../[SP01][Hi10p_1080p][x264_flac].mkv`，就不能改写成 `.../[SP01][Ma10p_1080p][x265_flac].mkv`
   - 即使同一作品的大多数文件都落在某个子目录中，也不能据此给其他文件自动补同样的子目录；文件是在根目录、`SPs/` 还是其他子目录，只能以输入列表为准
   - 不要补 base folder 名，不要改写目录层级
   - 不要只输出 basename
   - 不要混用新的路径分隔符格式

6. **可观测性要求（必须满足）**：
   - `reason` 保持极短，只用 1 句（建议 10-30 字）概括核心判断，不要写长段解释
   - 返回 `unmatched_files` 时，优先保留最有代表性的前 8 个未匹配路径即可；如果未匹配很多，不要穷举全部
   - `conflict_details` 仅填写“硬冲突”（例如：重复映射、集数越界、文件不存在），并限制为最关键的前 5 条
   - 证据不足/不确定但可执行的说明（例如仅凭小数集数推断）请写入 `extra_notes`，不要写入 `conflict_details`
   - 如果 confidence 为 High/Medium，`file_mapping` 必须尽量覆盖可匹配文件

7. **TMDB 合法性约束**：
   - 最终映射只能引用上面 TMDB 列表中真实存在的 `legal_node_id`
   - 不要自由生成 TMDB episode number；只能先选 TMDB legal_node_id，再选择承载它的本地文件
   - 本地编号或 Bangumi `sort/ep` 可能是系列全局编号；TMDB `episode_number` 是当前 TMDB season 内编号
   - 例如本地 `[45]~[55]` / Bangumi 全局 45~55 如果语义上对应某个 TMDB 11 集季度，应从该季度上方列出的 11 个 actual legal_node_id 中依次选择 E01..E11，不得输出 E45..E55
   - 不要因为文件名带 `SP / OVA / Part` 就自行发明新的 Season 0 / special 编号
   - 对拿不准或 TMDB 中不存在的文件，宁可放入 `unmatched_files`

8. **Bangumi 使用约束**：
   - Bangumi 只作为辅助证据，不是最终编号体系
   - Bangumi 的 relation（如前传 / 续集 / 番外篇 / 总集篇）不能直接等价为某个 TMDB season
   - 当本地文件只有编号或标题模糊时，可以参考 Bangumi 的 `sort / ep / type / 标题 / 日期`
   - 如果 Bangumi 与 TMDB 存在结构差异，最终仍必须回到 TMDB 已存在的 `SxxExx`
   - 无法自信桥接时，宁可将文件放入 `unmatched_files`

9. **复杂目录处理约束**：
   - 多子目录场景下，必须同时结合“文件名 + 所在子目录语义 + 时长”判断
   - `SP / OVA / OAD / Special / NCOP / NCED / Extras / Bonus / Menu` 等目录或标签通常不是正片
   - 如果输入列表里同时存在“根目录文件 + 子目录文件”或“同编号不同版本文件”，必须按输入中那条完整路径逐字复制；不要把别的文件所在目录或版本标签套到当前文件上
    - 如果只能确定部分文件，优先输出有把握的合法映射，其余文件放入 `unmatched_files`
   - 对 `SP01 / SP02 / OVA01` 这类 special 编号，若只能看到近似路径但无法确认哪一条才是输入中的真实文件，宁可放入 `unmatched_files`
   - 如果某个 `source_index` 已能确定合法落点，但你不确定自己是否能把 `file_path` 逐字抄对，就只返回 `source_index`，不要硬写一个可能出错的 `file_path`
    - 当目录里同时包含 TMDB 已存在的正片/特典与 TMDB 不存在的附加短片时，先覆盖可合法落点的那部分，其余放入 `unmatched_files`
    - 若 `unmatched_files` 数量很多，只保留最具代表性的样例路径，不要为求完整而长列表输出
   - 不要因为目录复杂就返回空的 `file_mapping`
   - 不要因为仍有一部分 extras / SP 无法落到 TMDB，就返回空的 `file_mapping`

"""
    return prompt


def get_system_prompt() -> str:
    return (
        "你是一个专业的动漫文件重命名助手。你需要分析本地动漫文件与TMDB数据库中剧集信息的对应关系，特别关注动漫BD发布与官方分季的差异。"
        + "请你只输出匹配到的季度和剧集信息，不要输出其他未匹配到tmdb信息的内容。"
    )
