from src.ai.client import AIClient
from src.ai.models import AIAnalysisResult, EpisodeMapping
from src.rename.ai_processor import AIProcessor


def test_neptune_like_special_mapping_stays_valid_with_bangumi_context(tmp_path):
    video_path = tmp_path / "[VCB-Studio] Choujigen Game Neptune The Animation [13].mkv"
    video_path.write_text("video", encoding="utf-8")

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
                        "overview": "special",
                        "air_date": "2014-03-26",
                    }
                ],
            },
            {
                "season_number": 1,
                "name": "Season 1",
                "episodes": [
                    {
                        "episode_number": i,
                        "name": f"Episode {i}",
                        "overview": "",
                        "air_date": f"2013-07-{11 + i:02d}",
                    }
                    for i in range(1, 14)
                ],
            },
        ],
    }
    local_files = [
        {
            "filename": video_path.name,
            "path": video_path.name,
            "duration": 24.0,
        }
    ]
    bangumi_context = {
        "source": "bangumi",
        "search_keywords": ["超次元游戏 海王星", "超次元ゲーム ネプテューヌ THE ANIMATION"],
        "selected_subject_id": 47957,
        "selected_subject_reason": "Neptune 主条目命中",
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

    assert "sort=13 ep=0 type=1" in prompt
    assert "duration_seconds=1420" in prompt
    assert "desc=2014年10月16日在Animax播放。" in prompt
    assert "Season 0" in prompt
    assert "只能从上面 `[编号] source_index=... source_path=` 列表里原样选择" in prompt
    assert "最终输出只能使用上面 TMDB 中真实存在的 SxxExx；" in prompt
    assert "若文件名只出现 `OVA3 / SP3 / [13]` 这类顺序编号" in prompt
    assert "但 `OVA3` 不等于 `S00E03`" in prompt

    ai_result = AIAnalysisResult(
        confidence="High",
        reason="Bangumi 第13条为 special，映射到 TMDB Season 0",
        season_mapping=[],
        file_mapping=[
            EpisodeMapping(
                file_path=video_path.name,
                tmdb_season=0,
                tmdb_episode=1,
                episode_type="special",
                confidence="High",
            )
        ],
        unmatched_files=[],
        conflict_details=[],
        extra_notes="Bangumi sort=13 / type=1 辅助判断为 special",
    )

    processor = AIProcessor()
    ok, reason, detail = processor.validate_tv_result(
        ai_result,
        anime_info,
        base_path=tmp_path,
        video_files=[video_path],
    )

    assert ok is True
    assert reason is None
    assert detail == ""
    assert len(ai_result.file_mapping) == 1
    assert ai_result.file_mapping[0].tmdb_season == 0
    assert ai_result.file_mapping[0].tmdb_episode == 1
    assert ai_result.unmatched_files == []
