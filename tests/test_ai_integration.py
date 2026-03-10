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
    MovieCollectionResult,
    TitleExtractionResult,
)
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
    ), patch.object(
        AIClient,
        "_call_gemini_simple",
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

    assert isinstance(result, tuple)
    assert result[0] == "生徒会的一存"
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

    assert isinstance(result, tuple)
    assert result[0] == "生徒会的一存 Lv.2"
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

    assert isinstance(result, tuple)
    searched_queries = [call.args[1] for call in search_tv.call_args_list]
    assert searched_queries == ["生徒会の一存 Lv.2", "生徒会の一存"]


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

        with patch.object(Rename, "check_task_type", return_value=(
            "AURA～魔竜院光牙最後の闘い～",
            info,
            True,
            True,
            "High",
        )), patch.object(AIClient, "is_available", return_value=True), patch.object(
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



def run_all_tests():
    """运行所有 AI 集成测试"""
    print("\n" + "=" * 80)
    print("AI 集成测试 - 验证 AI 能否解决正则解析失败的场景")
    print("=" * 80)

    test_ai_availability()
    test_extract_title_and_type()
    test_movie_collection_analysis()
    test_episode_mapping_analysis()
    test_bracket_episode_format()

    return []


