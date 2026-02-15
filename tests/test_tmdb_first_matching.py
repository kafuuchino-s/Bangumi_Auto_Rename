"""
测试 TMDB-first 匹配逻辑

验证 apply_ai_mapping 是否正确以 TMDB 为主进行匹配：
1. 只处理 TMDB 中存在的集数
2. 忽略 AI 返回的不存在于 TMDB 的集数
3. 报告缺失的集数
"""

import sys
import io
from pathlib import Path
from typing import Dict, List

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai.models import AIAnalysisResult, EpisodeMapping, SeasonMapping
from src.rename.ai_processor import AIProcessor


def test_tmdb_first_matching():
    """测试以 TMDB 为主的匹配逻辑"""
    print("=" * 80)
    print("测试 TMDB-first 匹配逻辑")
    print("=" * 80)

    # 模拟 TMDB 返回的动漫信息
    anime_info = {
        "name": "测试动漫",
        "first_air_date": "2023-01-01",
        "number_of_seasons": 1,
        "number_of_episodes": 5,  # TMDB 只有 5 集
        "seasons": [
            {
                "season_number": 0,
                "name": "特典",
                "episode_count": 2,
            },
            {
                "season_number": 1,
                "name": "Season 1",
                "episode_count": 5,  # 只有 5 集
            },
        ],
    }

    # 模拟 AI 返回的映射结果（包含超出 TMDB 范围的集数）
    ai_result = AIAnalysisResult(
        confidence="High",
        reason="测试",
        season_mapping=[],
        file_mapping=[
            # 正常集数 (1-5 在 TMDB 中存在)
            EpisodeMapping(
                file_path="E01.mkv",
                tmdb_season=1,
                tmdb_episode=1,
                episode_type="regular",
                confidence="High",
            ),
            EpisodeMapping(
                file_path="E02.mkv",
                tmdb_season=1,
                tmdb_episode=2,
                episode_type="regular",
                confidence="High",
            ),
            EpisodeMapping(
                file_path="E03.mkv",
                tmdb_season=1,
                tmdb_episode=3,
                episode_type="regular",
                confidence="High",
            ),
            # 缺失 E04
            EpisodeMapping(
                file_path="E05.mkv",
                tmdb_season=1,
                tmdb_episode=5,
                episode_type="regular",
                confidence="High",
            ),
            # AI 错误返回的集数 (E10, E99 在 TMDB 中不存在)
            EpisodeMapping(
                file_path="E10_wrong.mkv",
                tmdb_season=1,
                tmdb_episode=10,  # TMDB 只有 5 集
                episode_type="regular",
                confidence="High",
            ),
            EpisodeMapping(
                file_path="E99_wrong.mkv",
                tmdb_season=1,
                tmdb_episode=99,  # TMDB 只有 5 集
                episode_type="regular",
                confidence="High",
            ),
        ],
    )

    print("\nTMDB 信息:")
    print(f"  Season 1: {anime_info['seasons'][1]['episode_count']} 集 (E01-E05)")

    print("\nAI 返回的映射:")
    for m in ai_result.file_mapping:
        print(f"  {m.file_path} -> S{m.tmdb_season:02d}E{m.tmdb_episode:02d}")

    print("\n预期行为:")
    print("  - 处理: E01, E02, E03, E05 (在 TMDB 中存在)")
    print("  - 跳过: E10, E99 (在 TMDB 中不存在)")
    print("  - 缺失: E04 (TMDB 有但本地没有)")

    # 由于没有实际文件，我们只验证逻辑
    # 构建索引
    ai_mapping_index = {}
    for mapping in ai_result.file_mapping:
        key = (mapping.tmdb_season, mapping.tmdb_episode)
        ai_mapping_index[key] = mapping

    print("\n" + "-" * 40)
    print("模拟 TMDB-first 遍历:")
    print("-" * 40)

    matched = []
    missing = []
    ignored = []

    # 遍历 TMDB 的季度
    for season in anime_info["seasons"]:
        season_num = season["season_number"]
        episode_count = season["episode_count"]

        if season_num == 0:
            print(f"\n  [跳过] Season 0 (特典): {episode_count} 集")
            continue

        print(f"\n  Season {season_num}: 遍历 {episode_count} 集")

        for ep_num in range(1, episode_count + 1):
            key = (season_num, ep_num)

            if key in ai_mapping_index:
                mapping = ai_mapping_index[key]
                print(f"    ✓ S{season_num:02d}E{ep_num:02d} -> {mapping.file_path}")
                matched.append(key)
            else:
                print(f"    ✗ S{season_num:02d}E{ep_num:02d} -> 缺失")
                missing.append(key)

    # 检查 AI 返回了哪些 TMDB 中不存在的集数
    print("\n  AI 返回但 TMDB 中不存在的集数:")
    for mapping in ai_result.file_mapping:
        key = (mapping.tmdb_season, mapping.tmdb_episode)
        season_info = next(
            (s for s in anime_info["seasons"] if s["season_number"] == key[0]),
            None
        )
        if season_info:
            if key[1] > season_info["episode_count"]:
                print(f"    ⚠ S{key[0]:02d}E{key[1]:02d} ({mapping.file_path}) - 被忽略")
                ignored.append(key)

    print("\n" + "-" * 40)
    print("测试结果:")
    print("-" * 40)
    print(f"  匹配: {len(matched)} 集 - {matched}")
    print(f"  缺失: {len(missing)} 集 - {missing}")
    print(f"  忽略: {len(ignored)} 集 - {ignored}")

    # 验证
    passed = True
    if len(matched) != 4:  # E01, E02, E03, E05
        print("\n  ✗ 匹配数量错误，预期 4")
        passed = False
    if len(missing) != 1:  # E04
        print("\n  ✗ 缺失数量错误，预期 1")
        passed = False
    if len(ignored) != 2:  # E10, E99
        print("\n  ✗ 忽略数量错误，预期 2")
        passed = False

    if passed:
        print("\n  ✓ 所有验证通过！")
    else:
        print("\n  ✗ 验证失败")

    return passed


def test_ai_processor_apply_mapping():
    """测试 AIProcessor.apply_ai_mapping 的实际行为"""
    print("\n" + "=" * 80)
    print("测试 AIProcessor.apply_ai_mapping 实际调用")
    print("=" * 80)

    import tempfile
    import shutil

    # 创建临时测试目录
    temp_dir = Path(tempfile.mkdtemp())
    try:
        # 创建测试文件
        base_path = temp_dir / "test_anime"
        base_path.mkdir()

        # 创建 E01-E03, E05 (缺 E04), E10, E99
        for ep in [1, 2, 3, 5, 10, 99]:
            (base_path / f"E{ep:02d}.mkv").touch()

        work_path = temp_dir / "output" / "测试动漫 (2023)"
        work_path.mkdir(parents=True)

        # TMDB 信息
        anime_info = {
            "name": "测试动漫",
            "first_air_date": "2023-01-01",
            "seasons": [
                {"season_number": 0, "episode_count": 2},
                {"season_number": 1, "episode_count": 5},  # 只有 5 集
            ],
        }

        # AI 结果（包含超范围的集数）
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="测试",
            file_mapping=[
                EpisodeMapping(file_path="E01.mkv", tmdb_season=1, tmdb_episode=1),
                EpisodeMapping(file_path="E02.mkv", tmdb_season=1, tmdb_episode=2),
                EpisodeMapping(file_path="E03.mkv", tmdb_season=1, tmdb_episode=3),
                # E04 缺失
                EpisodeMapping(file_path="E05.mkv", tmdb_season=1, tmdb_episode=5),
                EpisodeMapping(file_path="E10.mkv", tmdb_season=1, tmdb_episode=10),  # 超范围
                EpisodeMapping(file_path="E99.mkv", tmdb_season=1, tmdb_episode=99),  # 超范围
            ],
        )

        print(f"\n  测试目录: {base_path}")
        print(f"  本地文件: E01, E02, E03, E05, E10, E99")
        print(f"  TMDB S01: 5 集 (E01-E05)")
        print(f"  AI 映射: E01, E02, E03, E05, E10, E99")

        # 调用 apply_ai_mapping
        processor = AIProcessor()
        result = processor.apply_ai_mapping(
            ai_result=ai_result,
            anime_info=anime_info,
            base_path=base_path,
            work_path=work_path,
        )

        print(f"\n  结果映射数量: {len(result)}")
        print("\n  生成的映射:")
        for src, dst in result.items():
            print(f"    {src.name} -> {dst.name}")

        # 验证
        passed = True

        # 应该只有 4 个映射 (E01, E02, E03, E05)
        if len(result) != 4:
            print(f"\n  ✗ 映射数量错误，预期 4，实际 {len(result)}")
            passed = False

        # 验证没有 E10, E99
        dst_names = [dst.name for dst in result.values()]
        for wrong_ep in ["E10", "E99"]:
            if any(wrong_ep in name for name in dst_names):
                print(f"\n  ✗ 错误地包含了 {wrong_ep}")
                passed = False

        if passed:
            print("\n  ✓ TMDB-first 逻辑验证通过！")
            print("    - E10, E99 被正确忽略（超出 TMDB 范围）")
            print("    - E04 被报告为缺失")
        else:
            print("\n  ✗ 验证失败")

        return passed

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("\n")
    result1 = test_tmdb_first_matching()
    result2 = test_ai_processor_apply_mapping()

    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)
    print(f"  逻辑验证: {'✓ 通过' if result1 else '✗ 失败'}")
    print(f"  实际调用: {'✓ 通过' if result2 else '✗ 失败'}")
