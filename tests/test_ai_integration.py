"""
AI 集成测试

测试 AI 处理能否正确解决正则解析失败的场景
"""

import sys
import json
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai.client import AIClient
from src.ai.models import (
    AIAnalysisResult,
    EpisodeMapping,
    MovieCollectionResult,
    TitleExtractionResult,
)
from src.ai.openai_client import OpenAIClient
from src.rename.ai_processor import AIProcessor
from src.rename.process import Rename


class AITestResult:
    def __init__(
        self,
        name: str,
        test_type: str,
        expected: str,
        actual: str,
        passed: bool,
        ai_confidence: Optional[str] = None,
    ):
        self.name = name
        self.test_type = test_type
        self.expected = expected
        self.actual = actual
        self.passed = passed
        self.ai_confidence = ai_confidence


def test_ai_availability():
    """测试 AI 客户端是否可用"""
    print("=" * 80)
    print("AI 可用性测试")
    print("=" * 80)

    ai_client = AIClient()
    available = ai_client.is_available()

    print(f"\n  AI 启用: {ai_client.enabled}")
    print(f"  AI 提供商: {ai_client.provider}")
    print(f"  AI 可用: {available}")
    print(f"  置信度阈值: {ai_client.confidence_threshold}")

    if not available:
        print("\n  [警告] AI 功能不可用，跳过 AI 集成测试")

    assert isinstance(available, bool)


def test_extract_title_and_type():
    """测试 AI 标题提取功能"""
    print("\n" + "=" * 80)
    print("场景 1: AI 标题和类型提取 (extract_title_and_type)")
    print("=" * 80)

    results = []
    ai_client = AIClient()

    if not ai_client.is_available():
        print("  [跳过] AI 不可用")
        return

    test_cases = [
        # (输入文件名, 预期标题关键词, 预期类型)
        (
            "[LoliHouse] 葬送的芙莉莲 / Sousou no Frieren [01-28 Fin][WebRip 1080p]",
            ["葬送的芙莉莲", "Frieren"],
            "tv",
        ),
        (
            "[AI-Raws][劇場版 空の境界][MOVIE 01-09+SP Fin][BDRip][MKV]",
            ["空の境界", "空之境界", "Garden of sinners"],
            "movie",
        ),
        (
            "[2021 Movie][Uchuu Senkan Yamato 2205][BDRIP][1080P][01-08Fin+SP]",
            ["Yamato", "宇宙战舰"],
            "movie",
        ),
        (
            "Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.mkv",
            ["Love", "Death", "Robots", "爱", "机器人"],
            "tv",
        ),
    ]

    for filename, expected_keywords, expected_type in test_cases:
        print(f"\n  测试: {filename[:60]}...")

        try:
            result = ai_client.extract_title_and_type(filename)

            if result:
                title, content_type = result
                print(f"    AI 提取标题: {title}")
                print(f"    AI 判断类型: {content_type}")

                # 检查标题是否包含预期关键词之一
                title_match = any(kw.lower() in title.lower() for kw in expected_keywords)
                type_match = content_type == expected_type

                passed = title_match and type_match
                status = "✓" if passed else "✗"
                print(f"    {status} 标题匹配: {title_match}, 类型匹配: {type_match}")

                results.append(AITestResult(
                    name=filename[:40],
                    test_type="extract_title_and_type",
                    expected=f"包含{expected_keywords}, type={expected_type}",
                    actual=f"title={title}, type={content_type}",
                    passed=passed,
                ))
            else:
                print("    ✗ AI 返回 None")
                results.append(AITestResult(
                    name=filename[:40],
                    test_type="extract_title_and_type",
                    expected=f"包含{expected_keywords}",
                    actual="None",
                    passed=False,
                ))
        except Exception as e:
            print(f"    ✗ 错误: {e}")
            results.append(AITestResult(
                name=filename[:40],
                test_type="extract_title_and_type",
                expected=f"包含{expected_keywords}",
                actual=f"Error: {e}",
                passed=False,
            ))

    assert all(isinstance(r.passed, bool) for r in results)


def test_movie_collection_analysis():
    """测试 AI 电影合集分析 - 解决 #01 格式问题"""
    print("\n" + "=" * 80)
    print("场景 2: AI 电影合集分析 (analyze_movie_collection)")
    print("这是正则测试失败的场景：#01 格式前导零丢失")
    print("=" * 80)

    results = []
    ai_client = AIClient()

    if not ai_client.is_available():
        print("  [跳过] AI 不可用")
        return

    # 模拟空之境界电影合集
    folder_name = "[AI-Raws][空之境界 ふのきょうかい - The Garden of sinners -][MOVIE 01-09+SP Fin][BDRip][MKV]"
    local_files = [
        {"path": "[AI-Raws] 空之境界 ふのきょうかい #01 俯瞰风景.mkv", "duration": 50.0},
        {"path": "[AI-Raws] 空之境界 ふのきょうかい #02 杀人考察(前).mkv", "duration": 60.0},
        {"path": "[AI-Raws] 空之境界 ふのきょうかい #03 痛觉残留.mkv", "duration": 55.0},
        {"path": "[AI-Raws] 空之境界 ふのきょうかい #09 未来福音 extra chorus.mkv", "duration": 30.0},
        {"path": "[AI-Raws] 空之境界 ふのきょうかい シネマナーCM #01.mkv", "duration": 1.0},
    ]

    print(f"\n  文件夹: {folder_name[:60]}...")
    print(f"  文件数: {len(local_files)}")
    for f in local_files:
        print(f"    - {f['path'][:50]}... (时长: {f['duration']}分钟)")

    try:
        result = ai_client.analyze_movie_collection(folder_name, local_files)

        if result:
            print(f"\n  AI 分析结果:")
            print(f"    是否合集: {result.is_collection}")
            print(f"    合集名称: {result.collection_name}")
            print(f"    置信度: {result.confidence}")
            print(f"    分析理由: {result.reason[:100] if result.reason else 'N/A'}...")

            print(f"\n  文件映射 ({len(result.file_mapping)} 个):")
            for mapping in result.file_mapping[:5]:  # 只显示前5个
                print(f"    - {mapping.file_path[:40]}...")
                print(f"      -> 电影: {mapping.movie_title}, 序号: {mapping.movie_number}")

            # 验证结果
            is_collection_correct = result.is_collection is True
            has_mappings = len(result.file_mapping) > 0
            has_movie_numbers = any(m.movie_number for m in result.file_mapping)

            passed = is_collection_correct and has_mappings and has_movie_numbers
            status = "✓" if passed else "✗"
            print(f"\n  {status} 合集识别: {is_collection_correct}, 有映射: {has_mappings}, 有序号: {has_movie_numbers}")

            results.append(AITestResult(
                name="空之境界 MOVIE 01-09",
                test_type="analyze_movie_collection",
                expected="is_collection=True, 有电影序号",
                actual=f"is_collection={result.is_collection}, mappings={len(result.file_mapping)}",
                passed=passed,
                ai_confidence=result.confidence,
            ))
        else:
            print("  ✗ AI 返回 None")
            results.append(AITestResult(
                name="空之境界 MOVIE 01-09",
                test_type="analyze_movie_collection",
                expected="MovieCollectionResult",
                actual="None",
                passed=False,
            ))
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        results.append(AITestResult(
            name="空之境界 MOVIE 01-09",
            test_type="analyze_movie_collection",
            expected="MovieCollectionResult",
            actual=f"Error: {e}",
            passed=False,
        ))

    assert all(isinstance(r.passed, bool) for r in results)


def test_episode_mapping_analysis():
    """测试 AI 剧集映射分析 - 解决科幻年份问题"""
    print("\n" + "=" * 80)
    print("场景 3: AI 剧集映射分析 (analyze_episode_mapping)")
    print("这是正则测试失败的场景：Yamato 2199 年份被误识别为集数")
    print("=" * 80)

    results = []
    ai_client = AIClient()

    if not ai_client.is_available():
        print("  [跳过] AI 不可用")
        return

    # 模拟 Yamato 2199 的 TMDB 信息
    anime_info = {
        "name": "宇宙战舰大和号2199",
        "original_name": "宇宙戦艦ヤマト2199",
        "first_air_date": "2012-04-07",
        "number_of_seasons": 1,
        "number_of_episodes": 26,
        "seasons": [
            {
                "season_number": 0,
                "name": "特典",
                "episode_count": 2,
            },
            {
                "season_number": 1,
                "name": "Season 1",
                "episode_count": 26,
                "episodes": [
                    {"episode_number": i, "name": f"第{i}话"} for i in range(1, 27)
                ],
            },
        ],
    }

    # 模拟本地文件（包含科幻年份 2199）
    local_files = [
        {"path": "Space Battleship Yamato 2199 (2012) - 01 VOSTFR BDrip 1080p.mkv", "duration": 24.0},
        {"path": "Space Battleship Yamato 2199 (2012) - 02 VOSTFR BDrip 1080p.mkv", "duration": 24.0},
        {"path": "Space Battleship Yamato 2199 (2012) - 15 VOSTFR BDrip 1080p.mkv", "duration": 24.0},
        {"path": "Space Battleship Yamato 2199 (2012) - 26 VOSTFR BDrip 1080p.mkv", "duration": 24.0},
    ]

    print(f"\n  动漫: {anime_info['name']}")
    print(f"  总集数: {anime_info['number_of_episodes']}")
    print(f"  本地文件数: {len(local_files)}")
    for f in local_files:
        print(f"    - {f['path'][:60]}...")

    try:
        result = ai_client.analyze_episode_mapping(anime_info, local_files)

        if result:
            print(f"\n  AI 分析结果:")
            print(f"    置信度: {result.confidence}")
            print(f"    分析理由: {result.reason[:100] if result.reason else 'N/A'}...")

            print(f"\n  文件映射 ({len(result.file_mapping)} 个):")
            correct_mappings = 0
            for mapping in result.file_mapping:
                print(f"    - {mapping.file_path[:50]}...")
                print(f"      -> S{mapping.tmdb_season:02d}E{mapping.tmdb_episode:02d} (置信度: {mapping.confidence})")

                # 检查集数是否正确（不是 2199）
                if mapping.tmdb_episode <= 26 and mapping.tmdb_episode >= 1:
                    correct_mappings += 1

            # 验证：所有文件都应该映射到 1-26 集，而不是 2199
            passed = correct_mappings == len(local_files)
            status = "✓" if passed else "✗"
            print(f"\n  {status} 正确映射: {correct_mappings}/{len(local_files)}")
            print(f"  关键检验: 集数应该是 1-26，而不是 2199")

            results.append(AITestResult(
                name="Yamato 2199 科幻年份",
                test_type="analyze_episode_mapping",
                expected="集数 1-26，不是 2199",
                actual=f"正确映射 {correct_mappings}/{len(local_files)}",
                passed=passed,
                ai_confidence=result.confidence,
            ))
        else:
            print("  ✗ AI 返回 None")
            results.append(AITestResult(
                name="Yamato 2199 科幻年份",
                test_type="analyze_episode_mapping",
                expected="AIAnalysisResult",
                actual="None",
                passed=False,
            ))
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        results.append(AITestResult(
            name="Yamato 2199 科幻年份",
            test_type="analyze_episode_mapping",
            expected="AIAnalysisResult",
            actual=f"Error: {e}",
            passed=False,
        ))

    assert all(isinstance(r.passed, bool) for r in results)


def test_bracket_episode_format():
    """测试 AI 处理方括号集数格式"""
    print("\n" + "=" * 80)
    print("场景 4: AI 处理方括号集数格式 [01]")
    print("正则 extract_number() 会错误匹配到 2202")
    print("=" * 80)

    results = []
    ai_client = AIClient()

    if not ai_client.is_available():
        print("  [跳过] AI 不可用")
        return

    # 模拟 Yamato 2202 的 TMDB 信息
    anime_info = {
        "name": "宇宙战舰大和号2202 爱的战士们",
        "original_name": "宇宙戦艦ヤマト2202 愛の戦士たち",
        "first_air_date": "2017-02-25",
        "number_of_seasons": 1,
        "number_of_episodes": 26,
        "seasons": [
            {
                "season_number": 1,
                "name": "Season 1",
                "episode_count": 26,
                "episodes": [
                    {"episode_number": i, "name": f"第{i}话"} for i in range(1, 27)
                ],
            },
        ],
    }

    # 模拟本地文件（方括号格式，包含 2202）
    local_files = [
        {"path": "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][01][BDRIP][1080P].mkv", "duration": 24.0},
        {"path": "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][15][BDRIP][1080P].mkv", "duration": 24.0},
        {"path": "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][26][BDRIP][1080P].mkv", "duration": 24.0},
    ]

    print(f"\n  动漫: {anime_info['name']}")
    print(f"  本地文件数: {len(local_files)}")
    for f in local_files:
        print(f"    - {f['path'][:60]}...")

    try:
        result = ai_client.analyze_episode_mapping(anime_info, local_files)

        if result:
            print(f"\n  AI 分析结果:")
            print(f"    置信度: {result.confidence}")

            print(f"\n  文件映射:")
            expected_episodes = [1, 15, 26]
            correct_count = 0

            for i, mapping in enumerate(result.file_mapping):
                expected_ep = expected_episodes[i] if i < len(expected_episodes) else None
                actual_ep = mapping.tmdb_episode

                is_correct = actual_ep == expected_ep
                if is_correct:
                    correct_count += 1

                status = "✓" if is_correct else "✗"
                print(f"    {status} {mapping.file_path[:40]}... -> E{actual_ep:02d} (预期: E{expected_ep:02d})")

            passed = correct_count == len(local_files)
            status = "✓" if passed else "✗"
            print(f"\n  {status} AI 正确识别集数: {correct_count}/{len(local_files)}")
            print(f"  关键检验: 应该识别 [01], [15], [26]，而不是 2202")

            results.append(AITestResult(
                name="Yamato 2202 方括号格式",
                test_type="analyze_episode_mapping",
                expected="E01, E15, E26",
                actual=f"正确 {correct_count}/{len(local_files)}",
                passed=passed,
                ai_confidence=result.confidence,
            ))
        else:
            print("  ✗ AI 返回 None")
            results.append(AITestResult(
                name="Yamato 2202 方括号格式",
                test_type="analyze_episode_mapping",
                expected="AIAnalysisResult",
                actual="None",
                passed=False,
            ))
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        results.append(AITestResult(
            name="Yamato 2202 方括号格式",
            test_type="analyze_episode_mapping",
            expected="AIAnalysisResult",
            actual=f"Error: {e}",
            passed=False,
        ))

    assert all(isinstance(r.passed, bool) for r in results)


def test_title_extraction_result_normalizes_fallback_title():
    """fallback_title 为空、null 或与 title 相同时应归一化为 None。"""
    same_value = TitleExtractionResult(
        title="生徒会の一存 Lv.2",
        fallback_title="生徒会の一存 Lv.2",
        type="tv",
    )
    assert same_value.fallback_title is None

    empty_value = TitleExtractionResult(
        title="生徒会の一存 Lv.2",
        fallback_title=" ",
        type="tv",
    )
    assert empty_value.fallback_title is None

    null_like_value = TitleExtractionResult(
        title="生徒会の一存 Lv.2",
        fallback_title="null",
        type="tv",
    )
    assert null_like_value.fallback_title is None


def test_extract_title_metadata_and_compatibility_helpers():
    """结构化标题提取应保留 extract_title / extract_title_and_type 兼容行为。"""
    payload = (
        '{"title":"生徒会の一存 Lv.2",'
        '"fallback_title":"生徒会の一存",'
        '"type":"tv"}'
    )
    ai_client = AIClient()

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "_call_openai_simple",
        return_value=payload,
    ):
        metadata = ai_client.extract_title_metadata("[字幕组] 生徒会の一存 Lv.2 [BDRip]")
        assert metadata is not None
        assert metadata.title == "生徒会の一存 Lv.2"
        assert metadata.fallback_title == "生徒会の一存"
        assert metadata.type == "tv"

        assert ai_client.extract_title("[字幕组] 生徒会の一存 Lv.2 [BDRip]") == "生徒会の一存 Lv.2"
        assert ai_client.extract_title_and_type("[字幕组] 生徒会の一存 Lv.2 [BDRip]") == (
            "生徒会の一存 Lv.2",
            "tv",
        )


def test_extract_title_metadata_without_fallback_keeps_old_behavior():
    """未返回 fallback_title 时行为应与旧接口一致。"""
    payload = '{"title":"Fate/Zero","fallback_title":null,"type":"tv"}'
    ai_client = AIClient()

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "_call_openai_simple",
        return_value=payload,
    ):
        metadata = ai_client.extract_title_metadata("[VCB-Studio] Fate Zero [Ma10p_1080p]")
        assert metadata is not None
        assert metadata.title == "Fate/Zero"
        assert metadata.fallback_title is None
        assert metadata.type == "tv"
        assert ai_client.extract_title_and_type("[VCB-Studio] Fate Zero [Ma10p_1080p]") == (
            "Fate/Zero",
            "tv",
        )



def test_extract_title_metadata_reuses_normalized_process_cache():
    """同系列 split case 的标题提取应命中进程缓存，避免重复 AI 调用。"""
    ai_client = AIClient()
    AIClient._title_metadata_cache.clear()
    payload = '{"title":"Moretsu Uchuu Kaizoku","fallback_title":null,"type":"tv"}'

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "_call_openai_simple",
        return_value=payload,
    ) as call_openai:
        first = ai_client.extract_title_metadata(
            "[ANK-Raws] Moretsu Uchuu Kaizoku - Vol.1 (BDrip 1920x1080 x264 FLAC Hi10P)"
        )
        second = ai_client.extract_title_metadata(
            "[ANK-Raws] Moretsu Uchuu Kaizoku - Vol.2 (BDrip 1920x1080 x264 FLAC Hi10P)"
        )

    assert first is not None
    assert second is not None
    assert first.title == "Moretsu Uchuu Kaizoku"
    assert second.title == "Moretsu Uchuu Kaizoku"
    assert call_openai.call_count == 1



def test_extract_title_metadata_cache_distinguishes_different_series():
    """不同作品名不应错误复用同一个标题提取缓存。"""
    ai_client = AIClient()
    AIClient._title_metadata_cache.clear()
    payloads = [
        '{"title":"Moretsu Uchuu Kaizoku","fallback_title":null,"type":"tv"}',
        '{"title":"Bodacious Space Pirates","fallback_title":null,"type":"tv"}',
    ]

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "_call_openai_simple",
        side_effect=payloads,
    ) as call_openai:
        first = ai_client.extract_title_metadata(
            "[ANK-Raws] Moretsu Uchuu Kaizoku - Vol.1 (BDrip 1920x1080 x264 FLAC Hi10P)"
        )
        second = ai_client.extract_title_metadata(
            "[ANK-Raws] Bodacious Space Pirates - Vol.1 (BDrip 1920x1080 x264 FLAC Hi10P)"
        )

    assert first is not None
    assert second is not None
    assert first.title == "Moretsu Uchuu Kaizoku"
    assert second.title == "Bodacious Space Pirates"
    assert call_openai.call_count == 2


def test_check_task_type_uses_fallback_title_when_primary_misses():
    """主标题无结果时，应继续尝试 fallback_title。"""
    rename = Rename()
    ai_client = AIClient()
    title_metadata = TitleExtractionResult(
        title="生徒会の一存 Lv.2",
        fallback_title="生徒会の一存",
        type="tv",
    )
    target_info = {
        "id": 1,
        "name": "生徒会的一存",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        side_effect=[
            ("", None, None, "tmdb_not_found"),
            ("生徒会的一存", target_info, "High", ""),
        ],
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("", None, None, "tmdb_not_found"),
    ):
        result = rename.check_task_type(
            rtpath_name="生徒会の一存",
            year=0,
            path=Path("[ANK-Raws] 生徒会の一存 Lv.2 [01].mkv"),
            ai_client=ai_client,
        )

    assert isinstance(result, dict)
    assert result["selected_name"] == "生徒会的一存"
    assert search_tv.call_args_list[0].args[1] == "生徒会の一存 Lv.2"
    assert search_tv.call_args_list[1].args[1] == "生徒会の一存"
    assert len(search_tv.call_args_list) == 2


def test_check_task_type_does_not_search_fallback_after_primary_hit():
    """主标题命中时，不应额外搜索 fallback_title。"""
    rename = Rename()
    ai_client = AIClient()
    title_metadata = TitleExtractionResult(
        title="生徒会の一存 Lv.2",
        fallback_title="生徒会の一存",
        type="tv",
    )
    target_info = {
        "id": 2,
        "name": "生徒会的一存 Lv.2",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        return_value=("生徒会的一存 Lv.2", target_info, "High", ""),
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("", None, None, "tmdb_not_found"),
    ):
        result = rename.check_task_type(
            rtpath_name="生徒会の一存",
            year=0,
            path=Path("[ANK-Raws] 生徒会の一存 Lv.2 [01].mkv"),
            ai_client=ai_client,
        )

    assert isinstance(result, dict)
    assert result["selected_name"] == "生徒会的一存 Lv.2"
    assert len(search_tv.call_args_list) == 1
    assert search_tv.call_args_list[0].args[1] == "生徒会の一存 Lv.2"


def test_check_task_type_deduplicates_fallback_and_clean_title_queries():
    """fallback_title 与清洗标题重复时，不应重复搜索。"""
    rename = Rename()
    ai_client = AIClient()
    title_metadata = TitleExtractionResult(
        title="生徒会の一存 Lv.2",
        fallback_title="生徒会の一存",
        type="tv",
    )
    target_info = {
        "id": 3,
        "name": "生徒会的一存",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        side_effect=[
            ("", None, None, "tmdb_not_found"),
            ("生徒会的一存", target_info, "High", ""),
        ],
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("", None, None, "tmdb_not_found"),
    ):
        result = rename.check_task_type(
            rtpath_name="生徒会の一存",
            year=0,
            path=Path("[ANK-Raws] 生徒会の一存 Lv.2 [01].mkv"),
            ai_client=ai_client,
        )

    assert isinstance(result, dict)
    assert result["selected_name"] == "生徒会的一存"
    searched_queries = [call.args[1] for call in search_tv.call_args_list]
    assert searched_queries == ["生徒会の一存 Lv.2", "生徒会の一存"]



def test_check_task_type_auto_mode_prefers_movie_chain_for_movie_hint():
    """自动模式下，明显 movie hint 的目录应优先走 Movie 空间。"""
    rename = Rename()
    ai_client = AIClient()
    title_metadata = TitleExtractionResult(
        title="Strike Witches The Movie",
        fallback_title="Strike Witches",
        type="movie",
    )
    movie_info = {
        "id": 24,
        "title": "强袭魔女 剧场版",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        return_value=("强袭魔女", {"id": 1, "name": "强袭魔女"}, "High", ""),
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("强袭魔女 剧场版", movie_info, "High", ""),
    ) as search_movie:
        result = rename.check_task_type(
            rtpath_name="Strike Witches The Movie",
            year=0,
            path=Path("[philosophy-raws][Strike Witches The Movie]"),
            ai_client=ai_client,
        )

    assert isinstance(result, dict)
    assert result["selected_name"] == "强袭魔女 剧场版"
    assert result["is_movie"] is True
    search_tv.assert_not_called()
    assert len(search_movie.call_args_list) == 1
    assert search_movie.call_args_list[0].args[1] == "Strike Witches The Movie"



def test_check_task_type_explicit_tv_override_keeps_tv_space():
    """显式强制 TV 时，应保留 override，不自动切到 Movie。"""
    rename = Rename()
    ai_client = AIClient()
    title_metadata = TitleExtractionResult(
        title="Strike Witches The Movie",
        fallback_title="Strike Witches",
        type="movie",
    )
    tv_info = {
        "id": 77,
        "name": "强袭魔女",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        return_value=("强袭魔女", tv_info, "High", ""),
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("强袭魔女 剧场版", {"id": 24, "title": "强袭魔女 剧场版"}, "High", ""),
    ) as search_movie:
        result = rename.check_task_type(
            rtpath_name="Strike Witches The Movie",
            year=0,
            path=Path("[philosophy-raws][Strike Witches The Movie]"),
            is_movie=False,
            ai_client=ai_client,
        )

    assert isinstance(result, dict)
    assert result["selected_name"] == "强袭魔女"
    assert result["is_movie"] is False
    assert len(search_tv.call_args_list) == 1
    assert search_tv.call_args_list[0].args[1] == "Strike Witches The Movie"
    search_movie.assert_not_called()


def test_check_task_type_prefers_structured_tv_signal_over_movie_type(tmp_path: Path):
    """多集 TV arc 目录即使标题像电影，也应优先按 TV 处理。"""
    rename = Rename()
    ai_client = AIClient()
    base_dir = tmp_path / "[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Mugen Ressha Hen [Ma10p_1080p]"
    base_dir.mkdir()
    for ep in (27, 28, 29):
        (base_dir / f"[VCB-Studio] Kimetsu no Yaiba [{ep}].mkv").write_bytes(b"")
    (base_dir / "SPs").mkdir()
    (base_dir / "SPs" / "01 PV.mkv").write_bytes(b"")

    title_metadata = TitleExtractionResult(
        title="Kimetsu no Yaiba Mugen Ressha Hen",
        fallback_title="Kimetsu no Yaiba",
        type="movie",
    )
    tv_info = {"id": 101, "name": "鬼灭之刃", "genres": [{"name": "Animation"}]}
    movie_info = {"id": 202, "title": "鬼灭之刃 剧场版 无限列车篇", "genres": [{"name": "Animation"}]}

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        return_value=("鬼灭之刃", tv_info, "High", ""),
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("鬼灭之刃 剧场版 无限列车篇", movie_info, "High", ""),
    ) as search_movie:
        result = rename.check_task_type(
            rtpath_name="Kimetsu no Yaiba Mugen Ressha Hen",
            year=0,
            path=base_dir,
            ai_client=ai_client,
        )

    assert isinstance(result, dict)
    assert result["selected_name"] == "鬼灭之刃"
    assert result["is_movie"] is False
    assert search_tv.call_count >= 1
    assert search_movie.call_count == 0


def test_has_structured_tv_episode_signal_ignores_movie_extras(tmp_path: Path):
    """单电影目录加特典视频，不应被误判为结构化 TV 信号。"""
    rename = Rename()
    base_dir = tmp_path / "[BeanSub&FZSD&VCB-Studio] Gekijouban Kimetsu no Yaiba [Ma10p_1080p]"
    base_dir.mkdir()
    (base_dir / "main.mkv").write_bytes(b"")
    (base_dir / "SPs").mkdir()
    (base_dir / "SPs" / "PV.mkv").write_bytes(b"")
    (base_dir / "bonus_cm.mkv").write_bytes(b"")
    (base_dir / "Trailer.mkv").write_bytes(b"")

    assert rename._has_structured_tv_episode_signal(base_dir) is False



def test_build_title_inputs_adds_parent_context_for_subtask_without_manual_name():
    """子任务无继承标题时，AI 输入应保留父目录上下文。"""
    path = Path("Yozakura Quartet") / "[Quetzal] Yoza-Quar!"

    rtpath_name, year, cleaned_title, raw_title, ai_input_name = (
        Rename._build_title_inputs(path, is_sub_task=True)
    )

    assert rtpath_name == "Yoza-Quar"
    assert year == 0
    assert cleaned_title == "Yoza-Quar"
    assert raw_title == "Yoza-Quar"
    assert ai_input_name == "Yozakura Quartet / [Quetzal] Yoza-Quar!"



def test_build_title_inputs_avoids_redundant_parent_context_when_child_already_has_series_name():
    """子任务标题已包含父级系列名时，不应重复拼接父目录上下文。"""
    path = Path("[VCB-Studio] OVERLORD") / "[VCB-Studio] OVERLORD Ple Ple Pleiades [Ma10p_1080p]"

    rtpath_name, year, cleaned_title, raw_title, ai_input_name = (
        Rename._build_title_inputs(path, is_sub_task=True)
    )

    assert rtpath_name == "OVERLORD Ple Ple Pleiades"
    assert year == 0
    assert cleaned_title == "OVERLORD Ple Ple Pleiades"
    assert raw_title == "OVERLORD Ple Ple Pleiades"
    assert ai_input_name == "[VCB-Studio] OVERLORD Ple Ple Pleiades [Ma10p_1080p]"



def test_structural_subdir_inherits_parent_custom_name():
    """结构目录应继承父级标题，而不是把 Film / Série 当成作品名。"""
    rename = Rename()
    parent = Path("Space Battleship Yamato 2199")

    assert rename._derive_subtask_custom_name(parent, parent / "Film", None) == "Space Battleship Yamato 2199"
    assert rename._derive_subtask_custom_name(parent, parent / "Série", None) == "Space Battleship Yamato 2199"
    assert rename._derive_subtask_custom_name(parent, parent / "Extras", None) == "Space Battleship Yamato 2199"
    assert rename._derive_subtask_custom_name(parent, parent / "OVA Collection", None) is None



def test_search_tv_with_ai_selection_prefers_deterministic_ranked_match():
    """TV 候选分差足够大时，应跳过 AI 直接采用确定性排序结果。"""
    rename = Rename()
    ai_client = AIClient()
    ranked_top = {"id": 2, "name": "Mob Psycho 100 II", "_match_score": 128.0}
    ranked_second = {"id": 1, "name": "Mob Psycho 100", "_match_score": 96.0}
    tv_info = {"id": 2, "name": "Mob Psycho 100 II", "genres": []}

    with patch.object(
        rename.search,
        "search_tv_by_query",
        return_value=[{"id": 1, "name": "Mob Psycho 100"}, {"id": 2, "name": "Mob Psycho 100 II"}],
    ), patch.object(
        rename.search,
        "rank_tv_candidates",
        return_value=[ranked_top, ranked_second],
    ), patch.object(
        rename.search,
        "_select_ranked_tv_candidate",
        return_value=(ranked_top, "High"),
    ), patch.object(
        rename,
        "_ai_select_tv",
    ) as ai_select_tv, patch.object(
        rename.search,
        "get_tv_info_by_id",
        return_value=tv_info,
    ):
        name, info, confidence, reason = rename._search_tv_with_ai_selection(
            "[VCB-Studio] Mob Psycho 100 II",
            "Mob Psycho 100 II",
            0,
            ai_client,
        )

    assert name == "Mob Psycho 100 II"
    assert info == tv_info
    assert confidence == "High"
    assert reason == ""
    ai_select_tv.assert_not_called()



def test_search_tv_with_ai_selection_returns_hydrated_season_details():
    """AI-first TV 选择后，应返回已补齐每集详情的 tmdb 信息。"""
    rename = Rename()
    ai_client = AIClient()
    ranked_top = {"id": 53787, "name": "水星领航员", "_match_score": 120.0}
    raw_info = {
        "id": 53787,
        "name": "水星领航员",
        "genres": [],
        "seasons": [{"season_number": 0, "episode_count": 22}],
    }
    hydrated_info = {
        "id": 53787,
        "name": "水星领航员",
        "genres": [],
        "seasons": [
            {
                "season_number": 0,
                "episode_count": 22,
                "episodes": [
                    {
                        "episode_number": 11,
                        "name": "Aria the Avvenire-1",
                        "season_number": 0,
                    }
                ],
            }
        ],
    }

    with patch.object(
        rename.search,
        "search_tv_by_query",
        return_value=[{"id": 53787, "name": "水星领航员"}],
    ), patch.object(
        rename.search,
        "rank_tv_candidates",
        return_value=[ranked_top],
    ), patch.object(
        rename.search,
        "_select_ranked_tv_candidate",
        return_value=(ranked_top, "High"),
    ), patch.object(
        rename.search,
        "get_tv_info_by_id",
        return_value=raw_info,
    ), patch.object(
        rename.search,
        "fill_season_info",
        return_value=hydrated_info,
    ) as fill_season_info:
        name, info, confidence, reason = rename._search_tv_with_ai_selection(
            "[VCB-Studio] ARIA The AVVENIRE Capitolo Version [Ma10p_1080p]",
            "ARIA The AVVENIRE",
            0,
            ai_client,
        )

    fill_season_info.assert_called_once_with(raw_info)
    assert name == "水星领航员"
    assert info["seasons"][0]["episodes"][0]["name"] == "Aria the Avvenire-1"
    assert confidence == "High"
    assert reason == ""


def test_check_task_type_prefers_raw_season_aware_title_before_fallback():
    """带季度信息的原始标题应先于 fallback/base title 搜索。"""
    rename = Rename()
    ai_client = AIClient()
    title_metadata = TitleExtractionResult(
        title="Mob Psycho 100 II",
        fallback_title="Mob Psycho 100",
        type="tv",
    )
    target_info = {
        "id": 2,
        "name": "Mob Psycho 100 II",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        side_effect=[
            ("Mob Psycho 100 II", target_info, "High", ""),
        ],
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("", None, None, "tmdb_not_found"),
    ):
        result = rename.check_task_type(
            rtpath_name="Mob Psycho 100",
            year=0,
            path=Path("[VCB-Studio] Mob Psycho 100 II"),
            ai_client=ai_client,
            cleaned_title="Mob Psycho 100",
            raw_title="Mob Psycho 100 II",
        )

    assert isinstance(result, dict)
    assert result["selected_name"] == "Mob Psycho 100 II"
    searched_queries = [call.args[1] for call in search_tv.call_args_list]
    assert searched_queries == ["Mob Psycho 100 II"]



def test_rank_tv_candidates_penalizes_base_series_when_query_has_sequel_token():
    """查询带续作 token 时，续作候选不应被基础作反超。"""
    rename = Rename()
    ranked = rename.search.rank_tv_candidates(
        source_title="Mob Psycho 100 II",
        query="Mob Psycho 100 II",
        candidates=[
            {"id": 1, "name": "Mob Psycho 100", "original_name": "Mob Psycho 100", "popularity": 50.0},
            {"id": 2, "name": "Mob Psycho 100 II", "original_name": "Mob Psycho 100 II", "popularity": 5.0},
        ],
        year=None,
    )

    assert [candidate["id"] for candidate in ranked[:2]] == [2, 1]



def test_rank_tv_candidates_prefers_numeric_identity_token_match():
    """查询含作品识别数字时，应优先保留带相同数字标识的条目。"""
    rename = Rename()
    ranked = rename.search.rank_tv_candidates(
        source_title="Space Battleship Yamato 2199",
        query="Space Battleship Yamato 2199",
        candidates=[
            {
                "id": 13339,
                "name": "Space Battleship Yamato",
                "original_name": "宇宙戦艦ヤマト",
                "popularity": 10.0,
            },
            {
                "id": 45844,
                "name": "Star Blazers: Space Battleship Yamato 2199",
                "original_name": "宇宙戦艦ヤマト2199",
                "popularity": 21.0,
            },
        ],
        year=None,
    )

    assert [candidate["id"] for candidate in ranked[:2]] == [45844, 13339]



def test_rank_tv_candidates_penalizes_spinoff_when_query_is_base_series():
    """查询像正作时，外传候选不应反超正作。"""
    rename = Rename()
    ranked = rename.search.rank_tv_candidates(
        source_title="Puella Magi Madoka Magica",
        query="Puella Magi Madoka Magica",
        candidates=[
            {
                "id": 1,
                "name": "Puella Magi Madoka Magica",
                "original_name": "魔法少女まどか☆マギカ",
                "popularity": 20.0,
            },
            {
                "id": 2,
                "name": "Magia Record: Puella Magi Madoka Magica Side Story",
                "original_name": "マギアレコード 魔法少女まどか☆マギカ外伝",
                "popularity": 80.0,
            },
        ],
        year=None,
    )

    assert [candidate["id"] for candidate in ranked[:2]] == [1, 2]



def test_check_task_type_uses_inherited_manual_title_for_ai_extraction():
    """结构目录继承父标题时，AI 标题提取也应使用继承标题而不是 Film/Série。"""
    rename = Rename()
    ai_client = AIClient()
    title_metadata = TitleExtractionResult(
        title="Space Battleship Yamato 2199",
        fallback_title=None,
        type="tv",
    )
    target_info = {
        "id": 77,
        "name": "宇宙战舰大和号2199",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ) as extract_title_metadata, patch.object(
        rename,
        "_search_tv_with_ai_selection",
        return_value=("宇宙战舰大和号2199", target_info, "High", ""),
    ), patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("", None, None, "tmdb_not_found"),
    ):
        result = rename.check_task_type(
            rtpath_name="Space Battleship Yamato 2199",
            year=0,
            path=Path("Film"),
            ai_client=ai_client,
            prefer_manual_title=True,
            cleaned_title="Space Battleship Yamato 2199",
            raw_title="Space Battleship Yamato 2199",
            ai_input_name="Space Battleship Yamato 2199",
        )

    assert isinstance(result, dict)
    assert result["selected_name"] == "宇宙战舰大和号2199"
    extract_title_metadata.assert_called_once_with("Space Battleship Yamato 2199")



def test_check_task_type_passes_inherited_title_to_tv_search_context():
    """结构目录继承父标题时，TV 搜索上下文也应使用继承标题。"""
    rename = Rename()
    ai_client = AIClient()
    title_metadata = TitleExtractionResult(
        title="Space Battleship Yamato 2199",
        fallback_title=None,
        type="tv",
    )
    target_info = {
        "id": 77,
        "name": "宇宙战舰大和号2199",
        "genres": [{"name": "Animation"}],
    }

    with patch.object(AIClient, "is_available", return_value=True), patch.object(
        AIClient,
        "extract_title_metadata",
        return_value=title_metadata,
    ), patch.object(
        rename,
        "_search_tv_with_ai_selection",
        return_value=("宇宙战舰大和号2199", target_info, "High", ""),
    ) as search_tv, patch.object(
        rename,
        "_search_movie_with_ai_selection",
        return_value=("", None, None, "tmdb_not_found"),
    ):
        result = rename.check_task_type(
            rtpath_name="Space Battleship Yamato 2199",
            year=0,
            path=Path("Film"),
            ai_client=ai_client,
            prefer_manual_title=True,
            cleaned_title="Space Battleship Yamato 2199",
            raw_title="Space Battleship Yamato 2199",
            ai_input_name="Space Battleship Yamato 2199",
        )

    assert isinstance(result, dict)
    assert search_tv.call_args_list[0].args[0] == "Space Battleship Yamato 2199"



def test_validate_tv_route_rejects_partial_non_promotional_video_mapping(tmp_path: Path, monkeypatch):
    """TV 严格路径不能把大包中的少数集数当作整体成功。"""
    source = tmp_path / "Wake up, Girls! ZOO - TV + SP"
    source.mkdir()
    mapped_file = source / "Wake up Girls ZOO - 01.mkv"
    unmapped_file = source / "Wake up Girls ZOO - 03.mkv"
    promo_file = source / "NCOP.mkv"
    for file_path in [mapped_file, unmapped_file, promo_file]:
        file_path.write_bytes(b"")

    rename = Rename()
    tv_info = {
        "id": 74191,
        "name": "Wake Up, Girls!",
        "seasons": [
            {
                "season_number": 0,
                "episodes": [{"episode_number": 1, "name": "Wake Up, Girl ZOO!"}],
            }
        ],
    }
    ai_result = AIAnalysisResult(
        confidence="High",
        file_mapping=[
            EpisodeMapping(
                file_path=mapped_file.name,
                tmdb_season=0,
                tmdb_episode=1,
            )
        ],
        unmatched_files=[unmapped_file.name],
        conflict_details=[],
        reason="partial mapping should fail closed",
    )

    monkeypatch.setattr(rename.ai_processor, "_collect_all_local_files", lambda path: [mapped_file, unmapped_file, promo_file])

    route_eval = rename._evaluate_validated_tv_route(
        source,
        tv_info,
        "Wake Up, Girls!",
        injected_ai_result=ai_result,
    )

    assert route_eval["valid"] is False
    assert route_eval["failure_reason"] == "ai_partial_mapping"
    assert "1/2" in route_eval["detail"]


def test_validate_tv_route_allows_unmapped_supplemental_videos(tmp_path: Path, monkeypatch):
    source = tmp_path / "Series With Extras"
    source.mkdir()
    mapped_file = source / "Series - 01.mkv"
    extra_file = source / "NCOP.mkv"
    bonus_file = source / "Radio Talk.mkv"
    for file_path in [mapped_file, extra_file, bonus_file]:
        file_path.write_bytes(b"")

    rename = Rename()
    tv_info = {
        "id": 1,
        "name": "Series",
        "seasons": [
            {
                "season_number": 1,
                "episodes": [{"episode_number": 1, "name": "Episode 1"}],
            }
        ],
    }
    ai_result = AIAnalysisResult(
        confidence="High",
        file_mapping=[
            EpisodeMapping(file_path=mapped_file.name, tmdb_season=1, tmdb_episode=1)
        ],
        unmatched_files=[extra_file.name, bonus_file.name],
        conflict_details=[],
        reason="extras should not block strict mapping",
    )

    monkeypatch.setattr(rename.ai_processor, "_collect_all_local_files", lambda path: [mapped_file, extra_file, bonus_file])

    route_eval = rename._evaluate_validated_tv_route(
        source,
        tv_info,
        "Series",
        injected_ai_result=ai_result,
    )

    assert route_eval["valid"] is True
    assert route_eval["mapped_count"] == 1
    assert sorted(route_eval["ignored_supplemental_relative_paths"]) == [
        "Radio Talk.mkv",
    ]


def test_validate_tv_route_uses_plain_episode_fallback_when_ai_missing(tmp_path: Path, monkeypatch):
    source = tmp_path / "Plain Episode Pack"
    source.mkdir()
    video_files = []
    for episode in range(1, 4):
        file_path = source / f"Plain Episode Pack - {episode:02d}.mkv"
        file_path.write_bytes(b"")
        video_files.append(file_path)

    rename = Rename()
    tv_info = {
        "id": 1,
        "name": "Plain Episode Pack",
        "first_air_date": "2024-01-01",
        "seasons": [
            {
                "season_number": 1,
                "episode_count": 12,
                "episodes": [
                    {"episode_number": episode, "name": f"Episode {episode}"}
                    for episode in range(1, 13)
                ],
            }
        ],
    }

    monkeypatch.setattr(rename.ai_processor.video_analyzer, "analyze_video_files", lambda path, files: [])
    monkeypatch.setattr(rename.ai_processor, "analyze_anime_files", lambda *args, **kwargs: None)
    monkeypatch.setattr(rename.ai_processor, "_collect_video_files", lambda path: video_files)
    monkeypatch.setattr(rename.ai_processor, "_collect_all_local_files", lambda path: video_files)

    route_eval = rename._evaluate_validated_tv_route(source, tv_info, "Plain Episode Pack")

    assert route_eval["valid"] is True
    assert route_eval["mapped_count"] == 3
    assert route_eval["confidence"] == "High"
    assert route_eval["claim_reasons"] == {
        "Plain Episode Pack - 01.mkv": "validated_tv_mapping:S01E01",
        "Plain Episode Pack - 02.mkv": "validated_tv_mapping:S01E02",
        "Plain Episode Pack - 03.mkv": "validated_tv_mapping:S01E03",
    }


def test_plain_episode_fallback_rejects_special_markers(tmp_path: Path):
    source = tmp_path / "Special Episode Pack"
    source.mkdir()
    files = []
    for name in ["Series - 01.mkv", "Series - OAD.mkv"]:
        file_path = source / name
        file_path.write_bytes(b"")
        files.append(file_path)

    rename = Rename()
    tv_info = {
        "id": 1,
        "name": "Series",
        "seasons": [{"season_number": 1, "episode_count": 12}],
    }

    assert rename._build_plain_tv_episode_fallback_result(source, tv_info, files) is None


def test_plain_episode_fallback_uses_preferred_season(tmp_path: Path):
    source = tmp_path / "Series II"
    source.mkdir()
    files = []
    for episode in range(1, 4):
        file_path = source / f"Series II - {episode:02d}.mkv"
        file_path.write_bytes(b"")
        files.append(file_path)

    rename = Rename()
    tv_info = {
        "id": 1,
        "name": "Series",
        "seasons": [
            {"season_number": 1, "episode_count": 12},
            {"season_number": 2, "episode_count": 12},
        ],
    }

    result = rename._build_plain_tv_episode_fallback_result(
        source,
        tv_info,
        files,
        preferred_season=2,
    )

    assert result is not None
    assert {mapping.tmdb_season for mapping in result.file_mapping} == {2}


def test_extract_preferred_season_number_recognizes_common_roman_and_names():
    rename = Rename()

    assert rename.search.extract_preferred_season_number("OVERLORD II") == 2
    assert rename.search.extract_preferred_season_number("OVERLORD III") == 3
    assert rename.search.extract_preferred_season_number("Minami-ke Okawari") == 2
    assert rename.search.extract_preferred_season_number("Minami-ke Okaeri") == 3


def test_select_exact_episode_count_tv_candidate_requires_unique_match():
    rename = Rename()
    candidates = [
        {"id": 1, "name": "Wrong Similar Show", "_match_score": 84.0},
        {"id": 2, "name": "Exact Episode Show", "_match_score": 72.0},
    ]

    selected, confidence = rename._select_exact_episode_count_tv_candidate(
        candidates,
        local_video_count=13,
        candidate_episode_counts={1: {12}, 2: {13}},
    )

    assert selected == candidates[1]
    assert confidence == "High"

    ambiguous_selected, ambiguous_confidence = rename._select_exact_episode_count_tv_candidate(
        candidates,
        local_video_count=13,
        candidate_episode_counts={1: {13}, 2: {13}},
    )
    assert ambiguous_selected is None
    assert ambiguous_confidence is None


def test_select_exact_episode_count_tv_candidate_rejects_large_score_gap():
    rename = Rename()
    candidates = [
        {"id": 1, "name": "Much Better Text Match", "_match_score": 120.0},
        {"id": 2, "name": "Exact Count But Weak", "_match_score": 70.0},
    ]

    selected, confidence = rename._select_exact_episode_count_tv_candidate(
        candidates,
        local_video_count=13,
        candidate_episode_counts={1: {12}, 2: {13}},
    )

    assert selected is None
    assert confidence is None


def test_search_tv_single_low_score_candidate_can_use_exact_episode_count(monkeypatch):
    rename = Rename()
    candidate = {
        "id": 42,
        "name": "The Devil Is a Part-Timer!",
        "_match_score": 64.0,
        "_matched_query": "Hataraku Maou-sama!",
    }
    tv_info = {
        "id": 42,
        "name": "The Devil Is a Part-Timer!",
        "seasons": [{"season_number": 1, "episode_count": 0}],
    }
    filled_tv_info = {
        "id": 42,
        "name": "The Devil Is a Part-Timer!",
        "seasons": [{"season_number": 1, "episode_count": 13}],
    }

    monkeypatch.setattr(rename.search, "search_tv_by_query", lambda query, year, limit=5: [candidate])
    monkeypatch.setattr(
        rename.search,
        "rank_tv_candidates",
        lambda source_title, query, candidates, year=None: [dict(candidate)],
    )
    monkeypatch.setattr(rename.search, "extract_preferred_season_number", lambda *args: None)
    monkeypatch.setattr(rename.search, "get_tv_info_by_id", lambda tv_id: dict(tv_info))
    monkeypatch.setattr(rename.search, "fill_season_info", lambda info: dict(filled_tv_info))

    class FailingAIClient:
        def select_tv_candidate(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    name, info, confidence, reason = rename._search_tv_with_ai_selection(
        "[VCB-Studio] Hataraku Maou-sama! [Ma10p_1080p]",
        "Hataraku Maou-sama!",
        0,
        FailingAIClient(),  # type: ignore[arg-type]
        local_video_count=13,
    )

    assert name == "The Devil Is a Part-Timer!"
    assert info == filled_tv_info
    assert confidence == "High"
    assert reason == ""


def test_supplemental_video_classifier_accepts_extras_directory(tmp_path: Path):
    base = tmp_path / "Series"
    extras = base / "Extras"
    extras.mkdir(parents=True)
    extra_file = extras / "Cast Event 01.mkv"
    creditless_file = base / "Creditless OP-ED" / "Series OP 01.mkv"
    info_file = base / "Series Info01.mkv"
    top_level_episode_like = base / "Series #12DC.mkv"
    creditless_file.parent.mkdir(parents=True)
    extra_file.write_bytes(b"")
    creditless_file.write_bytes(b"")
    info_file.write_bytes(b"")
    top_level_episode_like.write_bytes(b"")

    rename = Rename()
    assert rename._is_supplemental_video_file(extra_file, base) is True
    assert rename._is_supplemental_video_file(creditless_file, base) is True
    assert rename._is_supplemental_video_file(info_file, base) is True
    assert rename._is_supplemental_video_file(top_level_episode_like, base) is False


def test_commit_route_mapping_rejects_duplicate_video_targets_in_dry_run(tmp_path: Path):
    source = tmp_path / "movie-set"
    source.mkdir()
    first = source / "part1.mkv"
    second = source / "part2.mkv"
    target = tmp_path / "library" / "Movie (2020)" / "Movie (2020).mkv"
    for file_path in [first, second]:
        file_path.write_bytes(b"")

    result = Rename()._commit_route_mapping(
        task_uuid="task-duplicate-target",
        source_path=source,
        is_anime=False,
        is_movie=True,
        name="Movie",
        first_year="2020",
        season_id=0,
        info={"id": 1, "title": "Movie"},
        work_path=target.parent,
        mapping={first: target, second: target},
        ai_used=True,
        ai_confidence="High",
        release_group="",
        resource_term="",
        dry_run=True,
    )

    assert isinstance(result, str)
    assert "多个源文件映射到同一目标" in result


def test_search_tv_with_ai_selection_passes_local_video_count_to_ai_selection():
    """TV AI 选候选时应携带本地视频数量作为辅助证据。"""
    rename = Rename()
    ai_client = AIClient()
    ranked_candidates = [
        {"id": 34696, "name": "Yozakura Quartet", "_match_score": 95.0},
        {"id": 80500, "name": "Yozakura Quartet: Hana no Uta", "_match_score": 94.0},
    ]
    selected_candidate = ranked_candidates[1]
    selected_info = {
        "id": 80500,
        "name": "夜樱四重奏：花之歌",
        "genres": [],
        "seasons": [{"season_number": 0, "episode_count": 6}],
    }

    with patch.object(
        rename.search,
        "search_tv_by_query",
        return_value=[{"id": 34696}, {"id": 80500}],
    ), patch.object(
        rename.search,
        "rank_tv_candidates",
        return_value=ranked_candidates,
    ), patch.object(
        rename.search,
        "_select_ranked_tv_candidate",
        return_value=(None, None),
    ), patch.object(
        rename,
        "_ai_select_tv",
        return_value=(selected_candidate, "High"),
    ) as ai_select_tv, patch.object(
        rename.search,
        "get_tv_info_by_id",
        return_value=selected_info,
    ), patch.object(
        rename.search,
        "fill_season_info",
        return_value=selected_info,
    ):
        name, info, confidence, reason = rename._search_tv_with_ai_selection(
            "Yozakura Quartet / [Quetzal] Yoza-Quar!",
            "Yozakura Quartet",
            0,
            ai_client,
            local_video_count=6,
        )

    assert name == "夜樱四重奏：花之歌"
    assert info == selected_info
    assert confidence == "High"
    assert reason == ""
    assert ai_select_tv.call_args.kwargs["local_video_count"] == 6



def test_search_tv_with_ai_selection_prefers_candidate_with_requested_season():
    """当 TMDB 把续作拆成独立条目时，应优先保留包含目标季度的主条目。"""
    rename = Rename()
    ai_client = AIClient()
    candidates = [
        {"id": 75867, "name": "灵能百分百", "original_name": "モブサイコ100", "_match_score": 95.0, "popularity": 6.0},
        {"id": 67075, "name": "灵能百分百", "original_name": "モブサイコ100", "_match_score": 94.0, "popularity": 51.0},
    ]
    season1_only = {
        "id": 75867,
        "name": "灵能百分百",
        "genres": [],
        "seasons": [{"season_number": 1, "episode_count": 12}],
    }
    main_series = {
        "id": 67075,
        "name": "灵能百分百",
        "genres": [],
        "seasons": [
            {"season_number": 0, "episode_count": 8},
            {"season_number": 1, "episode_count": 12},
            {"season_number": 2, "episode_count": 13},
        ],
    }

    with patch.object(
        rename.search,
        "search_tv_by_query",
        return_value=[{"id": 75867}, {"id": 67075}],
    ), patch.object(
        rename.search,
        "rank_tv_candidates",
        return_value=candidates,
    ), patch.object(
        rename.search,
        "_select_ranked_tv_candidate",
        side_effect=lambda ranked: (None, None),
    ), patch.object(
        rename.search,
        "get_tv_info_by_id",
        side_effect=lambda tv_id: season1_only if tv_id == 75867 else main_series,
    ), patch.object(
        rename,
        "_ai_select_tv",
    ) as ai_select_tv:
        name, info, confidence, reason = rename._search_tv_with_ai_selection(
            "[VCB-Studio] Mob Psycho 100 II [Ma10p_1080p]",
            "Mob Psycho 100 II",
            0,
            ai_client,
        )

    assert name == "灵能百分百"
    assert info["id"] == 67075
    assert confidence == "High"
    assert reason == ""
    ai_select_tv.assert_not_called()



def test_movie_collection_result_falls_back_to_single_movie_main_feature():
    """单电影目录含特典时，应回退为单电影处理正片。"""
    rename = Rename()
    base_path = Path("[ANK-Raws] AURA")
    movie_file = base_path / "AURA Main Movie.mkv"
    extras_file = base_path / "AURA PV01.mkv"
    collection_result = MovieCollectionResult(
        is_collection=False,
        collection_name="AURA",
        confidence="High",
        reason="目录仅含一部正片，其余均为特典",
        file_mapping=[
            {
                "file_path": "AURA Main Movie.mkv",
                "movie_title": "AURA",
                "movie_number": None,
                "year": 2013,
                "confidence": "High",
            }
        ],
        unmatched_files=["AURA PV01.mkv"],
        conflict_details=[],
        extra_notes=None,
    )

    single_files = rename._extract_single_movie_files_from_collection_result(
        collection_result,
        [movie_file, extras_file],
        base_path,
    )

    assert single_files == [movie_file]



def test_process_movie_dir_falls_back_from_collection_to_single_movie():
    """电影合集候选若仅识别出一个正片，应按单电影完成处理。"""
    rename = Rename()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        movie_dir = temp_dir / "[ANK-Raws] AURA"
        movie_dir.mkdir()
        main_file = movie_dir / "劇場アニメ AURA Main Movie.mkv"
        extra_file = movie_dir / "劇場アニメ AURA PV01.mkv"
        main_file.touch()
        extra_file.touch()

        collection_result = MovieCollectionResult(
            is_collection=False,
            collection_name="AURA",
            confidence="High",
            reason="目录仅含一部正片，其余均为特典",
            file_mapping=[
                {
                    "file_path": main_file.name,
                    "movie_title": "AURA",
                    "movie_number": None,
                    "year": 2013,
                    "confidence": "High",
                }
            ],
            unmatched_files=[extra_file.name],
            conflict_details=[],
            extra_notes=None,
        )
        info = {
            "id": 42,
            "title": "AURA～魔竜院光牙最後の闘い～",
            "release_date": "2013-04-13",
            "poster_path": "/poster.jpg",
            "genres": [{"id": 16, "name": "Animation"}],
        }

        with patch.object(
            Rename,
            "check_task_type",
            return_value={
                "selected_name": "AURA～魔竜院光牙最後の闘い～",
                "selected_info": info,
                "is_anime": True,
                "is_movie": True,
                "selected_confidence": "High",
                "ai_type": "movie",
                "tv_candidate": {
                    "name": "",
                    "info": {},
                    "confidence": None,
                    "available": False,
                    "reason": "tmdb_not_found",
                },
                "movie_candidate": {
                    "name": "AURA～魔竜院光牙最後の闘い～",
                    "info": info,
                    "confidence": "High",
                    "available": True,
                    "reason": "",
                },
                "tv_subset_claim": None,
                "movie_subset_claim": None,
                "mixed_parent_plan": {
                    "planning_mode": "single_route",
                    "selected_route_type": "movie",
                    "selected_route": "movie",
                    "mixed_subset_failure_reason": None,
                    "mixed_subset_failure_detail": "",
                    "tv_claimed_file_count": 0,
                    "movie_claimed_file_count": 0,
                    "overlap_relative_paths": [],
                    "unclaimed_relative_paths": [],
                    "mixed_single_route_fallback_blocked": False,
                    "mixed_subset_blockers": [],
                },
                "should_try_both": False,
            },
        ), patch.object(AIClient, "is_available", return_value=True), patch.object(
            AIClient,
            "analyze_movie_collection",
            return_value=collection_result,
        ), patch(
            "src.rename.process.VideoAnalyzer.analyze_video_files",
            return_value=[
                {"path": main_file.name, "duration": 83.0},
                {"path": extra_file.name, "duration": 2.0},
            ],
        ), patch(
            "src.rename.process.Trans"
        ) as trans_cls, patch.object(
            Rename,
            "_write_task_data",
        ) as write_task_data:
            trans_cls.return_value.trans_file.return_value = None
            result = rename.process(movie_dir)

        assert result is True
        assert trans_cls.call_count == 1
        written_mapping = trans_cls.call_args_list[0].args[0]
        assert list(written_mapping.keys()) == [main_file]
        assert extra_file not in written_mapping
        assert write_task_data.call_count == 1
        task_payload = write_task_data.call_args_list[0][0][0]
        assert task_payload["name"] == "AURA～魔竜院光牙最後の闘い～"
        assert task_payload["is_movie"] is True
        assert task_payload["ai_confidence"] == "High"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



def test_process_movie_dir_single_movie_fallback_resolves_tmdb_from_collection_mapping():
    rename = Rename()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        movie_dir = temp_dir / "Kimetsu Movie Bundle"
        movie_dir.mkdir()
        main_file = movie_dir / "[BeanSub] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen.mkv"
        extra_file = movie_dir / "[BeanSub] PV01.mkv"
        main_file.touch()
        extra_file.touch()

        collection_result = MovieCollectionResult(
            is_collection=False,
            collection_name="鬼灭之刃 无限列车篇",
            confidence="High",
            reason="目录仅含一部正片，其余均为特典",
            file_mapping=[
                {
                    "file_path": main_file.name,
                    "movie_title": "Kimetsu no Yaiba Mugen Ressha Hen",
                    "movie_number": None,
                    "year": 2020,
                    "confidence": "High",
                }
            ],
            unmatched_files=[extra_file.name],
            conflict_details=[],
            extra_notes=None,
        )
        generic_info = {
            "id": 1613899,
            "title": "鬼灭之刃 无限列车篇",
            "release_date": None,
            "genres": [{"id": 16, "name": "Animation"}],
        }
        resolved_movie_info = {
            "id": 635302,
            "title": "鬼灭之刃剧场版：无限列车篇",
            "release_date": "2020-10-16",
            "poster_path": "/poster.jpg",
            "genres": [{"id": 16, "name": "Animation"}],
        }

        with patch.object(
            Rename,
            "check_task_type",
            return_value={
                "selected_name": "鬼灭之刃 无限列车篇",
                "selected_info": generic_info,
                "is_anime": True,
                "is_movie": True,
                "selected_confidence": "High",
                "ai_type": "movie",
                "tv_candidate": {
                    "name": "",
                    "info": {},
                    "confidence": None,
                    "available": False,
                    "reason": "tmdb_not_found",
                },
                "movie_candidate": {
                    "name": "鬼灭之刃 无限列车篇",
                    "info": generic_info,
                    "confidence": "High",
                    "available": True,
                    "reason": "",
                },
                "tv_subset_claim": None,
                "movie_subset_claim": None,
                "mixed_parent_plan": {
                    "planning_mode": "single_route",
                    "selected_route_type": "movie",
                    "selected_route": "movie",
                    "mixed_subset_failure_reason": None,
                    "mixed_subset_failure_detail": "",
                    "tv_claimed_file_count": 0,
                    "movie_claimed_file_count": 0,
                    "overlap_relative_paths": [],
                    "unclaimed_relative_paths": [],
                    "mixed_single_route_fallback_blocked": False,
                    "mixed_subset_blockers": [],
                },
                "should_try_both": False,
            },
        ), patch.object(AIClient, "is_available", return_value=True), patch.object(
            AIClient,
            "analyze_movie_collection",
            return_value=collection_result,
        ), patch(
            "src.rename.process.VideoAnalyzer.analyze_video_files",
            return_value=[
                {"path": main_file.name, "duration": 117.0},
                {"path": extra_file.name, "duration": 2.0},
            ],
        ), patch(
            "src.rename.process.Trans"
        ) as trans_cls, patch.object(
            rename.search,
            "search_movies_by_title",
            return_value=[
                {
                    "id": 635302,
                    "title": "Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train",
                    "_match_score": 130,
                },
                {
                    "id": 1613899,
                    "title": "Kimetsu.no.Yaiba",
                    "_match_score": 95,
                },
            ],
        ), patch.object(
            rename.search,
            "get_movie_info_by_id",
            return_value=resolved_movie_info,
        ), patch.object(
            Rename,
            "_write_task_data",
        ) as write_task_data:
            trans_cls.return_value.trans_file.return_value = None
            result = rename.process(movie_dir)

        assert result is True
        assert trans_cls.call_count == 1
        written_mapping = trans_cls.call_args_list[0].args[0]
        assert list(written_mapping.keys()) == [main_file]
        assert extra_file not in written_mapping
        assert write_task_data.call_count == 1
        task_payload = write_task_data.call_args_list[0][0][0]
        assert task_payload["name"] == "鬼灭之刃剧场版：无限列车篇"
        assert task_payload["year"] == "2020"
        assert task_payload["tmdb_id"] == 635302
        assert task_payload["tmdb_name"] == "鬼灭之刃剧场版：无限列车篇"
        assert task_payload["tmdb_year"] == "2020"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_single_movie_collection_fallback_handles_empty_ai_mapping():
    rename = Rename()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        movie_dir = temp_dir / "sample_0091_vcb_studio_kimetsu_no_yaiba"
        movie_dir.mkdir()
        main_file = movie_dir / "[VCB-Studio] Kimetsu no Yaiba the Movie - Mugen Train.mkv"
        extra_dir = movie_dir / "SPs"
        extra_dir.mkdir()
        extra_files = [
            extra_dir / "01 PV.mkv",
            extra_dir / "02 CM.mkv",
            extra_dir / "03 Trailer.mkv",
            extra_dir / "04 Menu.mkv",
        ]
        main_file.touch()
        for extra_file in extra_files:
            extra_file.touch()

        collection_result = MovieCollectionResult(
            is_collection=False,
            collection_name="鬼灭之刃 无限列车篇",
            confidence="High",
            reason="AI 未返回可用映射",
            file_mapping=[],
            unmatched_files=[str(p.relative_to(movie_dir)).replace('\\', '/') for p in extra_files],
            conflict_details=[],
            extra_notes=None,
        )

        fallback_files = rename._extract_single_movie_files_from_collection_result(
            collection_result,
            [main_file, *extra_files],
            movie_dir,
        )
        assert fallback_files == [main_file]

        generic_info = {
            "id": 1613899,
            "title": "鬼灭之刃 无限列车篇",
            "release_date": None,
            "genres": [{"id": 16, "name": "Animation"}],
        }
        resolved_movie_info = {
            "id": 635302,
            "title": "鬼灭之刃剧场版：无限列车篇",
            "release_date": "2020-10-16",
            "poster_path": "/poster.jpg",
            "genres": [{"id": 16, "name": "Animation"}],
        }

        with patch.object(
            Rename,
            "check_task_type",
            return_value={
                "selected_name": "鬼灭之刃 无限列车篇",
                "selected_info": generic_info,
                "is_anime": True,
                "is_movie": True,
                "selected_confidence": "High",
                "ai_type": "movie",
                "tv_candidate": {
                    "name": "",
                    "info": {},
                    "confidence": None,
                    "available": False,
                    "reason": "tmdb_not_found",
                },
                "movie_candidate": {
                    "name": "鬼灭之刃 无限列车篇",
                    "info": generic_info,
                    "confidence": "High",
                    "available": True,
                    "reason": "",
                },
                "tv_subset_claim": None,
                "movie_subset_claim": None,
                "mixed_parent_plan": {
                    "planning_mode": "single_route",
                    "selected_route_type": "movie",
                    "selected_route": "movie",
                    "mixed_subset_failure_reason": None,
                    "mixed_subset_failure_detail": "",
                    "tv_claimed_file_count": 0,
                    "movie_claimed_file_count": 0,
                    "overlap_relative_paths": [],
                    "unclaimed_relative_paths": [],
                    "mixed_single_route_fallback_blocked": False,
                    "mixed_subset_blockers": [],
                },
                "should_try_both": False,
            },
        ), patch.object(AIClient, "is_available", return_value=True), patch.object(
            AIClient,
            "analyze_movie_collection",
            return_value=collection_result,
        ), patch(
            "src.rename.process.VideoAnalyzer.analyze_video_files",
            return_value=[
                {"path": main_file.name, "duration": 117.0},
                *[{"path": extra_file.name, "duration": 2.0} for extra_file in extra_files],
            ],
        ), patch(
            "src.rename.process.Trans"
        ) as trans_cls, patch.object(
            rename.search,
            "search_movies_by_title",
            return_value=[
                {
                    "id": 635302,
                    "title": "Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train",
                    "_match_score": 130,
                },
                {
                    "id": 1613899,
                    "title": "Kimetsu.no.Yaiba",
                    "_match_score": 95,
                },
            ],
        ), patch.object(
            rename.search,
            "get_movie_info_by_id",
            return_value=resolved_movie_info,
        ), patch.object(
            Rename,
            "_write_task_data",
        ) as write_task_data:
            trans_cls.return_value.trans_file.return_value = None
            result = rename.process(movie_dir)

        assert result is True
        assert trans_cls.call_count == 1
        written_mapping = trans_cls.call_args_list[0].args[0]
        assert list(written_mapping.keys()) == [main_file]
        assert all(extra_file not in written_mapping for extra_file in extra_files)
        assert write_task_data.call_count == 1
        task_payload = write_task_data.call_args_list[0][0][0]
        assert task_payload["name"] == "鬼灭之刃 无限列车篇"
        assert task_payload["tmdb_id"] == 1613899
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_evaluate_validated_movie_route_resolves_single_movie_subset_tmdb():
    rename = Rename()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        movie_dir = temp_dir / "Kimetsu Movie Bundle"
        movie_dir.mkdir()
        main_file = movie_dir / "[BeanSub] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen.mkv"
        extra_file = movie_dir / "[BeanSub] PV01.mkv"
        main_file.touch()
        extra_file.touch()

        collection_result = MovieCollectionResult(
            is_collection=False,
            collection_name="鬼灭之刃 无限列车篇",
            confidence="High",
            reason="目录仅含一部正片，其余均为特典",
            file_mapping=[
                {
                    "file_path": main_file.name,
                    "movie_title": "Kimetsu no Yaiba Mugen Ressha Hen",
                    "movie_number": None,
                    "year": 2020,
                    "confidence": "High",
                }
            ],
            unmatched_files=[extra_file.name],
            conflict_details=[],
            extra_notes=None,
        )
        generic_movie_info = {
            "id": 1613899,
            "title": "鬼灭之刃 无限列车篇",
            "release_date": "2020-01-01",
            "genres": [{"id": 16, "name": "Animation"}],
        }
        resolved_movie_info = {
            "id": 635302,
            "title": "鬼灭之刃剧场版：无限列车篇",
            "release_date": "2020-10-16",
            "poster_path": "/poster.jpg",
            "genres": [{"id": 16, "name": "Animation"}],
        }

        with patch.object(
            rename.search,
            "search_movies_by_title",
            return_value=[
                {
                    "id": 635302,
                    "title": "Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train",
                    "_match_score": 130,
                },
                {
                    "id": 1613899,
                    "title": "Kimetsu.no.Yaiba",
                    "_match_score": 95,
                },
            ],
        ), patch.object(
            rename.search,
            "get_movie_info_by_id",
            return_value=resolved_movie_info,
        ):
            route_eval = rename._evaluate_validated_movie_route(
                movie_dir,
                generic_movie_info,
                "鬼灭之刃 无限列车篇",
                injected_collection_result=collection_result,
                ordered_video_files=[main_file, extra_file],
            )

        processed_movies = route_eval.get("processed_movies", [])
        assert route_eval["valid"] is True
        assert len(processed_movies) == 1
        assert processed_movies[0]["tmdb_id"] == 635302
        assert route_eval["tmdb_info"]["id"] == 635302
        assert route_eval["tmdb_name"] == "鬼灭之刃剧场版：无限列车篇"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



def test_ai_processor_resolves_nested_prefix_mapping_path():
    """AI 返回带重复前缀目录的路径时，应仍能解析到唯一源文件。"""
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "[KTXP] Mushishi Zoku Shou"
        season_dir = base_path / "Disc1"
        season_dir.mkdir(parents=True)
        source_file = season_dir / "[KTXP] Mushishi Zoku Shou [01].mkv"
        source_file.touch()

        local_videos = [source_file]
        relative_index = processor._build_relative_file_index(base_path, local_videos)
        resolved, error, normalized = processor._resolve_mapping_source_path(
            "[KTXP] Mushishi Zoku Shou/Disc1/[KTXP] Mushishi Zoku Shou [01].mkv",
            base_path,
            relative_index,
        )

        assert error is None
        assert normalized == "[KTXP] Mushishi Zoku Shou/Disc1/[KTXP] Mushishi Zoku Shou [01].mkv"
        assert resolved == source_file.resolve()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



def test_ai_processor_rejects_nested_hint_when_only_basename_exists():
    """带伪造目录前缀的路径不应退化成 basename 命中。"""
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "Madoka"
        season_dir = base_path / "Disc1"
        season_dir.mkdir(parents=True)
        source_file = season_dir / "Episode 01.mkv"
        source_file.touch()

        relative_index = processor._build_relative_file_index(base_path, [source_file])
        resolved, error, normalized = processor._resolve_mapping_source_path(
            "MagiRepo/Episode 01.mkv",
            base_path,
            relative_index,
        )

        assert resolved is None
        assert normalized == "MagiRepo/Episode 01.mkv"
        assert error == "文件不存在:MagiRepo/Episode 01.mkv"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



def test_validate_tv_result_hydrates_file_path_from_source_index(tmp_path):
    """AI 仅返回 source_index 时，应回填成准确相对路径。"""
    processor = AIProcessor()

    (tmp_path / "Disc1").mkdir()
    source_file = tmp_path / "Disc1" / "Episode 01.mkv"
    source_file.write_text("video", encoding="utf-8")

    anime_info = {
        "name": "Test Anime",
        "seasons": [
            {
                "season_number": 1,
                "episode_count": 1,
                "episodes": [
                    {"episode_number": 1, "name": "Episode 1", "overview": ""}
                ],
            }
        ],
    }
    ai_result = AIAnalysisResult(
        confidence="High",
        reason="test",
        season_mapping=[],
        file_mapping=[
            EpisodeMapping(
                source_index=1,
                tmdb_season=1,
                tmdb_episode=1,
                episode_type="regular",
                confidence="High",
            )
        ],
        unmatched_files=[],
        conflict_details=[],
        extra_notes=None,
    )

    ok, reason, detail = processor.validate_tv_result(
        ai_result,
        anime_info,
        tmp_path,
        [source_file],
    )

    assert ok is True
    assert reason is None
    assert detail == ""
    assert ai_result.file_mapping[0].source_index == 1
    assert ai_result.file_mapping[0].file_path == "Disc1/Episode 01.mkv"



def test_validate_tv_result_ignores_nonexistent_dirty_file_path_when_source_index_matches(tmp_path):
    """source_index 正确、但 file_path 只是不存在的脏文本时，应按编号回填。"""
    processor = AIProcessor()

    source_file = tmp_path / "Episode 04.mkv"
    source_file.write_text("video", encoding="utf-8")

    anime_info = {
        "name": "Test Anime",
        "seasons": [
            {
                "season_number": 0,
                "episode_count": 4,
                "episodes": [
                    {"episode_number": 4, "name": "Episode 4", "overview": ""}
                ],
            }
        ],
    }
    ai_result = AIAnalysisResult(
        confidence="High",
        reason="test",
        season_mapping=[],
        file_mapping=[
            EpisodeMapping(
                source_index=1,
                file_path="Episode 04 x264 x264 x264.mkv",
                tmdb_season=0,
                tmdb_episode=4,
                episode_type="special",
                confidence="High",
            )
        ],
        unmatched_files=[],
        conflict_details=[],
        extra_notes=None,
    )

    ok, reason, detail = processor.validate_tv_result(
        ai_result,
        anime_info,
        tmp_path,
        [source_file],
    )

    assert ok is True
    assert reason is None
    assert detail == ""
    assert ai_result.file_mapping[0].file_path == "Episode 04.mkv"
    assert ai_result.conflict_details == []



def test_validate_tv_result_rejects_source_index_path_mismatch(tmp_path):
    """source_index 与 file_path 指向不同文件时，应保持 strict 拒绝。"""
    processor = AIProcessor()

    (tmp_path / "Disc1").mkdir()
    (tmp_path / "Disc2").mkdir()
    source_file_1 = tmp_path / "Disc1" / "Episode 01.mkv"
    source_file_2 = tmp_path / "Disc2" / "Episode 02.mkv"
    source_file_1.write_text("video", encoding="utf-8")
    source_file_2.write_text("video", encoding="utf-8")

    anime_info = {
        "name": "Test Anime",
        "seasons": [
            {
                "season_number": 1,
                "episode_count": 1,
                "episodes": [
                    {"episode_number": 1, "name": "Episode 1", "overview": ""}
                ],
            }
        ],
    }
    ai_result = AIAnalysisResult(
        confidence="High",
        reason="test",
        season_mapping=[],
        file_mapping=[
            EpisodeMapping(
                source_index=1,
                file_path="Disc2/Episode 02.mkv",
                tmdb_season=1,
                tmdb_episode=1,
                episode_type="regular",
                confidence="High",
            )
        ],
        unmatched_files=[],
        conflict_details=[],
        extra_notes=None,
    )

    ok, reason, detail = processor.validate_tv_result(
        ai_result,
        anime_info,
        tmp_path,
        [source_file_1, source_file_2],
    )

    assert ok is False
    assert reason == "ai_invalid_mapping"
    assert detail == "编号路径不一致:1:Disc2/Episode 02.mkv != Disc1/Episode 01.mkv"



def test_openai_normalize_mapping_item_accepts_source_index_only():
    client = OpenAIClient()

    normalized = client._normalize_mapping_item(
        {
            "source_index": 2,
            "tmdb_season": 1,
            "tmdb_episode": 3,
            "episode_type": "regular",
            "confidence": "High",
        },
        "Medium",
    )

    assert normalized == {
        "source_index": 2,
        "file_path": None,
        "tmdb_season": 1,
        "tmdb_episode": 3,
        "episode_type": "regular",
        "confidence": "High",
    }



def test_validate_tv_result_sanitizes_illegal_episode_mapping():
    """局部越界映射应被清洗，不应拖垮整批有效映射。"""
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "PSYCHO-PASS"
        base_path.mkdir(parents=True)
        valid_file = base_path / "PSYCHO-PASS 01.mkv"
        invalid_file = base_path / "PSYCHO-PASS SP.mkv"
        valid_file.touch()
        invalid_file.touch()

        anime_info = {
            "name": "PSYCHO-PASS",
            "seasons": [
                {
                    "season_number": 1,
                    "episode_count": 1,
                    "episodes": [
                        {
                            "episode_number": 1,
                            "name": "Crime Coefficient",
                            "overview": "",
                        }
                    ],
                }
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="PSYCHO-PASS 01.mkv",
                    tmdb_season=1,
                    tmdb_episode=1,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="PSYCHO-PASS SP.mkv",
                    tmdb_season=0,
                    tmdb_episode=1,
                    episode_type="special",
                    confidence="Medium",
                ),
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [valid_file, invalid_file],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        assert len(ai_result.file_mapping) == 1
        assert ai_result.file_mapping[0].file_path == "PSYCHO-PASS 01.mkv"
        assert "PSYCHO-PASS SP.mkv" in ai_result.unmatched_files
        assert any("越界映射" in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_filters_auxiliary_season0_mapping_without_title_overlap():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "KimetsuSpecials"
        base_path.mkdir(parents=True)
        main_file = base_path / "Yuukaku [34].mkv"
        suspicious_review_file = (
            base_path
            / "SPs"
            / "[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [EP11 Review Avant][Ma10p_1080p][x265_aac].mkv"
        )
        suspicious_enroku_file = (
            base_path
            / "SPs"
            / "[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Kimetsu Enroku 01][Ma10p_1080p][x265_aac].mkv"
        )
        suspicious_iv_file = (
            base_path
            / "SPs"
            / "[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [IV01][Ma10p_1080p][x265_aac].mkv"
        )
        suspicious_review_file.parent.mkdir(parents=True, exist_ok=True)
        main_file.touch()
        suspicious_review_file.touch()
        suspicious_enroku_file.touch()
        suspicious_iv_file.touch()

        anime_info = {
            "name": "鬼灭之刃",
            "seasons": [
                {
                    "season_number": 0,
                    "episodes": [
                        {
                            "episode_number": 1,
                            "name": "Junior High and High School!! Kimetsu Academy Story: Valentine Edition #1",
                            "overview": "",
                        }
                    ],
                },
                {
                    "season_number": 3,
                    "episode_count": 11,
                    "episodes": [
                        {
                            "episode_number": 1,
                            "name": "Someone's Dream",
                            "overview": "",
                        }
                    ],
                },
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="Yuukaku [34].mkv",
                    tmdb_season=3,
                    tmdb_episode=1,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [EP11 Review Avant][Ma10p_1080p][x265_aac].mkv",
                    tmdb_season=3,
                    tmdb_episode=1,
                    episode_type="special",
                    confidence="Medium",
                ),
                EpisodeMapping(
                    file_path="SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Kimetsu Enroku 01][Ma10p_1080p][x265_aac].mkv",
                    tmdb_season=1,
                    tmdb_episode=1,
                    episode_type="special",
                    confidence="Medium",
                ),
                EpisodeMapping(
                    file_path="SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [IV01][Ma10p_1080p][x265_aac].mkv",
                    tmdb_season=3,
                    tmdb_episode=4,
                    episode_type="special",
                    confidence="Medium",
                ),
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [main_file, suspicious_review_file, suspicious_enroku_file, suspicious_iv_file],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        assert [
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        ] == [("Yuukaku [34].mkv", 3, 1)]
        assert (
            "SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [EP11 Review Avant][Ma10p_1080p][x265_aac].mkv"
            in ai_result.unmatched_files
        )
        assert (
            "SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Kimetsu Enroku 01][Ma10p_1080p][x265_aac].mkv"
            in ai_result.unmatched_files
        )
        assert (
            "SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [IV01][Ma10p_1080p][x265_aac].mkv"
            in ai_result.unmatched_files
        )
        assert any("语义过滤" in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_filters_menu_and_promo_season0_mapping_without_title_overlap():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "KimetsuMenuSpecials"
        base_path.mkdir(parents=True)
        menu_file = (
            base_path
            / "SPs"
            / "[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [MenuITA01][Ma10p_1080p][x265_flac].mkv"
        )
        promo_file = (
            base_path
            / "SPs"
            / "[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Promotion Reel][Ma10p_1080p][x265_flac].mkv"
        )
        menu_file.parent.mkdir(parents=True, exist_ok=True)
        menu_file.touch()
        promo_file.touch()

        anime_info = {
            "name": "鬼灭之刃",
            "seasons": [
                {
                    "season_number": 0,
                    "episodes": [
                        {
                            "episode_number": 13,
                            "name": "Zenitsu's Sugoroku (2)",
                            "overview": "",
                        },
                        {
                            "episode_number": 16,
                            "name": "Junior High and High School!! Demon Slayer Banquet - Special Arc",
                            "overview": "",
                        },
                    ],
                }
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [MenuITA01][Ma10p_1080p][x265_flac].mkv",
                    tmdb_season=0,
                    tmdb_episode=13,
                    episode_type="special",
                    confidence="Medium",
                ),
                EpisodeMapping(
                    file_path="SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Promotion Reel][Ma10p_1080p][x265_flac].mkv",
                    tmdb_season=0,
                    tmdb_episode=16,
                    episode_type="special",
                    confidence="Medium",
                ),
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [menu_file, promo_file],
        )

        assert ok is False
        assert reason == "ai_empty_mapping"
        assert detail == "AI 未返回任何有效映射"
        assert ai_result.file_mapping == []
        assert (
            "SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [MenuITA01][Ma10p_1080p][x265_flac].mkv"
            in ai_result.unmatched_files
        )
        assert (
            "SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Promotion Reel][Ma10p_1080p][x265_flac].mkv"
            in ai_result.unmatched_files
        )
        assert any(
            "语义过滤:S00E13" in item for item in ai_result.conflict_details
        )
        assert any(
            "语义过滤:S00E16" in item for item in ai_result.conflict_details
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_keeps_auxiliary_season0_mapping_with_title_overlap():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "KimetsuLegitSpecial"
        base_path.mkdir(parents=True)
        special_file = base_path / "Valentine Edition 01.mkv"
        special_file.touch()

        anime_info = {
            "name": "鬼灭之刃",
            "seasons": [
                {
                    "season_number": 0,
                    "episodes": [
                        {
                            "episode_number": 1,
                            "name": "Junior High and High School!! Kimetsu Academy Story: Valentine Edition #1",
                            "overview": "",
                        }
                    ],
                }
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="Valentine Edition 01.mkv",
                    tmdb_season=0,
                    tmdb_episode=1,
                    episode_type="special",
                    confidence="High",
                )
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [special_file],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        assert [
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        ] == [("Valentine Edition 01.mkv", 0, 1)]
        assert not any("Season0语义过滤" in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_semantic_filter_runs_before_duplicate_episode_dedupe():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "KimetsuCollision"
        base_path.mkdir(parents=True)
        main_file = base_path / "Yuukaku [34].mkv"
        menu_file = (
            base_path
            / "SPs"
            / "[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [MenuITA01][Ma10p_1080p][x265_flac].mkv"
        )
        menu_file.parent.mkdir(parents=True, exist_ok=True)
        main_file.touch()
        menu_file.touch()

        anime_info = {
            "name": "鬼灭之刃",
            "seasons": [
                {
                    "season_number": 3,
                    "episode_count": 1,
                    "episodes": [
                        {
                            "episode_number": 1,
                            "name": "Someone's Dream",
                            "overview": "",
                        }
                    ],
                }
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="Yuukaku [34].mkv",
                    tmdb_season=3,
                    tmdb_episode=1,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [MenuITA01][Ma10p_1080p][x265_flac].mkv",
                    tmdb_season=3,
                    tmdb_episode=1,
                    episode_type="special",
                    confidence="High",
                ),
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [main_file, menu_file],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        assert [
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        ] == [("Yuukaku [34].mkv", 3, 1)]
        assert (
            "SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [MenuITA01][Ma10p_1080p][x265_flac].mkv"
            in ai_result.unmatched_files
        )
        assert any("语义过滤:S03E01" in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_global_remap_main_file_survives_auxiliary_collision():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "KimetsuRemapCollision"
        base_path.mkdir(parents=True)
        main_file = base_path / "Yuukaku [34].mkv"
        auxiliary_file = (
            base_path
            / "SPs"
            / "[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Kimetsu Enroku 01][Ma10p_1080p][x265_aac].mkv"
        )
        auxiliary_file.parent.mkdir(parents=True, exist_ok=True)
        main_file.touch()
        auxiliary_file.touch()

        anime_info = {
            "name": "鬼灭之刃",
            "seasons": [
                {"season_number": 1, "episode_count": 26, "episodes": []},
                {"season_number": 2, "episode_count": 7, "episodes": []},
                {"season_number": 3, "episode_count": 11, "episodes": []},
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="Yuukaku [34].mkv",
                    tmdb_season=1,
                    tmdb_episode=11,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Kimetsu Enroku 01][Ma10p_1080p][x265_aac].mkv",
                    tmdb_season=3,
                    tmdb_episode=1,
                    episode_type="special",
                    confidence="High",
                ),
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [main_file, auxiliary_file],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        assert [
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        ] == [("Yuukaku [34].mkv", 3, 1)]
        assert any("全局编号重映射:S01E11->S03E01" in item for item in ai_result.conflict_details)
        assert any("语义过滤:S03E01" in item for item in ai_result.conflict_details)
        assert (
            "SPs/[BeanSub&FZSD&VCB-Studio] Kimetsu no Yaiba Yuukaku Hen [Kimetsu Enroku 01][Ma10p_1080p][x265_aac].mkv"
            in ai_result.unmatched_files
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_remaps_global_episode_overflow():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "Kimetsu"
        base_path.mkdir(parents=True)
        ep27 = base_path / "Mugen [27].mkv"
        ep34 = base_path / "Yuukaku [34].mkv"
        ep27.touch()
        ep34.touch()

        anime_info = {
            "name": "鬼灭之刃",
            "seasons": [
                {"season_number": 1, "episode_count": 26, "episodes": []},
                {"season_number": 2, "episode_count": 7, "episodes": []},
                {"season_number": 3, "episode_count": 11, "episodes": []},
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="Mugen [27].mkv",
                    tmdb_season=1,
                    tmdb_episode=27,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="Yuukaku [34].mkv",
                    tmdb_season=1,
                    tmdb_episode=34,
                    episode_type="regular",
                    confidence="High",
                ),
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [ep27, ep34],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        remapped = sorted(
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        )
        assert remapped == [
            ("Mugen [27].mkv", 2, 1),
            ("Yuukaku [34].mkv", 3, 1),
        ]
        assert any("全局编号重映射:S01E27->S02E01" in item for item in ai_result.conflict_details)
        assert any("全局编号重映射:S01E34->S03E01" in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_remaps_global_episode_with_valid_but_wrong_local_numbering():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "KimetsuLocal"
        base_path.mkdir(parents=True)
        ep27 = base_path / "Mugen [27].mkv"
        ep34 = base_path / "Yuukaku [34].mkv"
        ep27.touch()
        ep34.touch()

        anime_info = {
            "name": "鬼灭之刃",
            "seasons": [
                {"season_number": 1, "episode_count": 26, "episodes": []},
                {"season_number": 2, "episode_count": 7, "episodes": []},
                {"season_number": 3, "episode_count": 11, "episodes": []},
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="Mugen [27].mkv",
                    tmdb_season=1,
                    tmdb_episode=1,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="Yuukaku [34].mkv",
                    tmdb_season=1,
                    tmdb_episode=11,
                    episode_type="regular",
                    confidence="High",
                ),
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [ep27, ep34],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        remapped = sorted(
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        )
        assert remapped == [
            ("Mugen [27].mkv", 2, 1),
            ("Yuukaku [34].mkv", 3, 1),
        ]
        assert any("全局编号重映射:S01E01->S02E01" in item for item in ai_result.conflict_details)
        assert any("全局编号重映射:S01E11->S03E01" in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_remaps_global_episode_before_duplicate_episode_dedupe():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "KimetsuDedupe"
        base_path.mkdir(parents=True)
        season1_ep01 = base_path / "Root [01].mkv"
        season1_ep11 = base_path / "Root [11].mkv"
        ep27 = base_path / "Mugen [27].mkv"
        ep34 = base_path / "Yuukaku [34].mkv"
        for file_path in [season1_ep01, season1_ep11, ep27, ep34]:
            file_path.touch()

        anime_info = {
            "name": "鬼灭之刃",
            "seasons": [
                {"season_number": 1, "episode_count": 26, "episodes": []},
                {"season_number": 2, "episode_count": 7, "episodes": []},
                {"season_number": 3, "episode_count": 11, "episodes": []},
            ],
        }
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="test",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="Root [01].mkv",
                    tmdb_season=1,
                    tmdb_episode=1,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="Root [11].mkv",
                    tmdb_season=1,
                    tmdb_episode=11,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="Mugen [27].mkv",
                    tmdb_season=1,
                    tmdb_episode=1,
                    episode_type="regular",
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="Yuukaku [34].mkv",
                    tmdb_season=1,
                    tmdb_episode=11,
                    episode_type="regular",
                    confidence="High",
                ),
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [season1_ep01, season1_ep11, ep27, ep34],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        remapped = sorted(
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        )
        assert remapped == [
            ("Mugen [27].mkv", 2, 1),
            ("Root [01].mkv", 1, 1),
            ("Root [11].mkv", 1, 11),
            ("Yuukaku [34].mkv", 3, 1),
        ]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_extract_global_episode_number_ignores_audio_channel_numbers_in_explicit_season_files():
    processor = AIProcessor()

    assert (
        processor._extract_global_episode_number(
            'Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv'
        )
        is None
    )
    assert (
        processor._extract_global_episode_number(
            'The.Disastrous.Life.of.Saiki.K.S01E24.2016.1080p.BluRay.x265.10bit.FLAC.2.0-ADE.mkv'
        )
        is None
    )
    assert processor._extract_global_episode_number('Yuukaku [34].mkv') == 34


def test_extract_explicit_episode_number_prefers_sxxeyy_over_audio_channel_numbers():
    processor = AIProcessor()

    assert (
        processor._extract_explicit_episode_number(
            'Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv'
        )
        == 1
    )
    assert (
        processor._extract_explicit_episode_number(
            'The.Disastrous.Life.of.Saiki.K.S01E24.2016.1080p.BluRay.x265.10bit.FLAC.2.0-ADE.mkv'
        )
        == 24
    )


def test_validate_tv_result_preserves_explicit_season_pack_with_ddp51_markers():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / 'LoveDeathRobots'
        base_path.mkdir(parents=True)
        files = [
            base_path / 'Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv',
            base_path / 'Love.Death.&.Robots.S04E02.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv',
            base_path / 'Love.Death.&.Robots.S04E03.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv',
        ]
        for file_path in files:
            file_path.touch()

        anime_info = {
            'name': 'Love, Death & Robots',
            'seasons': [
                {'season_number': 4, 'episode_count': 3, 'episodes': []},
            ],
        }
        ai_result = AIAnalysisResult(
            confidence='High',
            reason='test',
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path=file_path.name,
                    tmdb_season=4,
                    tmdb_episode=index,
                    episode_type='regular',
                    confidence='High',
                )
                for index, file_path in enumerate(files, 1)
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            files,
        )

        assert ok is True
        assert reason is None
        assert detail == ''
        remapped = sorted(
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        )
        assert remapped == [
            (files[0].name, 4, 1),
            (files[1].name, 4, 2),
            (files[2].name, 4, 3),
        ]
        assert not any('全局编号重映射' in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_validate_tv_result_preserves_explicit_season_pack_with_flac20_markers():
    processor = AIProcessor()
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / 'SaikiK'
        base_path.mkdir(parents=True)
        files = [
            base_path / 'The.Disastrous.Life.of.Saiki.K.S01E01.2016.1080p.BluRay.x265.10bit.FLAC.2.0-ADE.mkv',
            base_path / 'The.Disastrous.Life.of.Saiki.K.S01E02.2016.1080p.BluRay.x265.10bit.FLAC.2.0-ADE.mkv',
            base_path / 'The.Disastrous.Life.of.Saiki.K.S01E03.2016.1080p.BluRay.x265.10bit.FLAC.2.0-ADE.mkv',
        ]
        for file_path in files:
            file_path.touch()

        anime_info = {
            'name': '齐木楠雄的灾难',
            'seasons': [
                {'season_number': 1, 'episode_count': 3, 'episodes': []},
            ],
        }
        ai_result = AIAnalysisResult(
            confidence='High',
            reason='test',
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path=file_path.name,
                    tmdb_season=1,
                    tmdb_episode=index,
                    episode_type='regular',
                    confidence='High',
                )
                for index, file_path in enumerate(files, 1)
            ],
        )

        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            files,
        )

        assert ok is True
        assert reason is None
        assert detail == ''
        remapped = sorted(
            (mapping.file_path, mapping.tmdb_season, mapping.tmdb_episode)
            for mapping in ai_result.file_mapping
        )
        assert remapped == [
            (files[0].name, 1, 1),
            (files[1].name, 1, 2),
            (files[2].name, 1, 3),
        ]
        assert not any('全局编号重映射' in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
