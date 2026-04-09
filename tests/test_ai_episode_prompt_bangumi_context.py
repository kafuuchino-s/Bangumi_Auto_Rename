from src.ai.client import AIClient


def test_build_common_prompt_includes_bangumi_context_section():
    anime_info = {
        "name": "超次元游戏 海王星",
        "original_name": "超次元ゲーム ネプテューヌ THE ANIMATION",
        "first_air_date": "2013-07-12",
        "number_of_seasons": 2,
        "number_of_episodes": 14,
        "seasons": [
            {
                "season_number": 0,
                "name": "Specials",
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "True End",
                        "air_date": "2014-03-26",
                    }
                ],
            },
            {
                "season_number": 1,
                "name": "Season 1",
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Episode 1",
                        "air_date": "2013-07-12",
                    }
                ],
            },
        ],
    }
    local_files = [
        {
            "path": "Disc1/[VCB-Studio] Choujigen Game Neptune The Animation [13].mkv",
            "duration": 24.0,
        }
    ]
    bangumi_context = {
        "source": "bangumi",
        "search_keywords": ["超次元游戏 海王星", "超次元ゲーム ネプテューヌ THE ANIMATION"],
        "selected_subject_id": 47957,
        "selected_subject_reason": "匹配得分最高",
        "subjects": [
            {
                "relation_to_main": "main",
                "score": 9.8,
                "subject": {
                    "id": 47957,
                    "name": "超次元ゲーム ネプテューヌ THE ANIMATION",
                    "name_cn": "超次元游戏 海王星",
                    "date": "2013-07-12",
                    "platform": "TV",
                },
                "episodes": [
                    {
                        "sort": 13,
                        "ep": 0,
                        "type": 1,
                        "airdate": "2014-03-26",
                        "name": "約束の永遠(トゥルーエンド)",
                        "name_cn": "永恒的承诺（True End）",
                        "duration_seconds": 1420,
                        "desc": "2014年10月16日在Animax播放。",
                    }
                ],
            }
        ],
    }

    prompt = AIClient.build_common_prompt(
        anime_info,
        local_files,
        bangumi_context=bangumi_context,
    )

    assert "Bangumi 辅助上下文" in prompt
    assert "主条目 ID: 47957" in prompt
    assert "sort=13 ep=0 type=1" in prompt
    assert "duration_seconds=1420" in prompt
    assert "desc=2014年10月16日在Animax播放。" in prompt
    assert "Disc1" in prompt
    assert "顶层子目录: Disc1" in prompt
    assert "如果不同文件落在不同子目录" in prompt
    assert "[001] source_index=1 source_path=`Disc1/[VCB-Studio] Choujigen Game Neptune The Animation [13].mkv`" in prompt
    assert "只能从上面 `[编号] source_index=... source_path=` 列表里原样选择" in prompt
    assert "先定位对应的 `[编号]` 行" in prompt
    assert "路径中的目录名、文件名、以及 `[]` 内的版本标签都属于路径的一部分" in prompt
    assert "不要把短标签/简称扩写回主标题" in prompt
    assert "MagiRepo [01]" in prompt
    assert "SP01][Hi10p_1080p][x264_flac]" in prompt
    assert "即使同一作品的大多数文件都落在某个子目录中，也不能据此给其他文件自动补同样的子目录" in prompt
    assert "如果某个 `source_index` 已能确定合法落点，但你不确定自己是否能把 `file_path` 逐字抄对，就只返回 `source_index`" in prompt
    assert "当目录里同时包含 TMDB 已存在的正片/特典与 TMDB 不存在的附加短片时，先覆盖可合法落点的那部分" in prompt
    assert "不要因为仍有一部分 extras / SP 无法落到 TMDB，就返回空的 `file_mapping`" in prompt
    assert "Bangumi 使用规则：relation 只是辅助语义，不等于 TMDB season；" in prompt
    assert "最终输出只能使用上面 TMDB 中真实存在的 SxxExx；" in prompt
    assert "若文件名只出现 `OVA3 / SP3 / [13]` 这类顺序编号" in prompt
    assert "但 `OVA3` 不等于 `S00E03`" in prompt


def test_build_common_prompt_marks_tmdb_only_when_bangumi_context_missing():
    prompt = AIClient.build_common_prompt(
        {"name": "测试动画", "seasons": []},
        [{"path": "ep01.mkv"}],
        bangumi_context=None,
    )

    assert "Bangumi 辅助上下文：不可用（本次按 TMDB-only 处理）" in prompt
