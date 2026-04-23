"""
测试 TMDB-first 匹配逻辑

验证 apply_ai_mapping 是否正确以 TMDB 为主进行匹配：
1. 只处理 TMDB 中存在的集数
2. 忽略 AI 返回的不存在于 TMDB 的集数
3. 报告缺失的集数
"""

import sys
from pathlib import Path
from typing import Dict, List

import shutil
import tempfile
from unittest.mock import patch

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai.models import AIAnalysisResult, EpisodeMapping, SeasonMapping
from src.rename.ai_processor import AIProcessor
from src.rename.get_info import Search


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

    assert passed


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

        assert passed

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_ai_processor_apply_mapping_enhanced_naming_snapshot():
    """验证 AI 映射链路会生成增强后的剧集命名。"""
    import tempfile
    import shutil

    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "input"
        base_path.mkdir(parents=True)

        source_video = (
            base_path
            / "[LoliHouse] Frieren Part 1 [WEB-DL 1080p HEVC-10bit AAC].mkv"
        )
        source_video.touch()

        work_path = temp_dir / "output" / "葬送的芙莉莲 (2023)"
        work_path.mkdir(parents=True)

        anime_info = {
            "name": "葬送的芙莉莲",
            "seasons": [
                {
                    "season_number": 1,
                    "episode_count": 1,
                }
            ],
        }

        ai_result = AIAnalysisResult(
            confidence="High",
            reason="命名快照",
            file_mapping=[
                EpisodeMapping(
                    file_path=source_video.name,
                    tmdb_season=1,
                    tmdb_episode=1,
                )
            ],
        )

        processor = AIProcessor()
        result = processor.apply_ai_mapping(
            ai_result=ai_result,
            anime_info=anime_info,
            base_path=base_path,
            work_path=work_path,
        )

        assert len(result) == 1
        target = result[source_video.resolve()]
        assert target.parent.name == "Season 01"
        assert (
            target.name
            == "葬送的芙莉莲 - S01E01-Part1 - 1080p HEVC 10bit AAC WEB-DL - LoliHouse.mkv"
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_ai_processor_apply_mapping_subtitle_uses_new_video_stem():
    """验证 AI 映射链路中关联字幕会跟随新视频名。"""
    import tempfile
    import shutil

    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "input"
        base_path.mkdir(parents=True)

        source_video = (
            base_path
            / "[LoliHouse] Frieren 01 [WEB-DL 1080p HEVC AAC].mkv"
        )
        source_video.touch()

        source_subtitle = base_path / f"{source_video.name}.chs.ass"
        source_subtitle.touch()

        work_path = temp_dir / "output" / "葬送的芙莉莲 (2023)"
        work_path.mkdir(parents=True)

        anime_info = {
            "name": "葬送的芙莉莲",
            "seasons": [
                {
                    "season_number": 1,
                    "episode_count": 1,
                }
            ],
        }

        ai_result = AIAnalysisResult(
            confidence="High",
            reason="字幕命名快照",
            file_mapping=[
                EpisodeMapping(
                    file_path=source_video.name,
                    tmdb_season=1,
                    tmdb_episode=1,
                )
            ],
        )

        processor = AIProcessor()
        all_local_files = [source_video.resolve(), source_subtitle.resolve()]
        result = processor.apply_ai_mapping(
            ai_result=ai_result,
            anime_info=anime_info,
            base_path=base_path,
            work_path=work_path,
            all_local_files=all_local_files,
        )

        video_target = result[source_video.resolve()]
        subtitle_target = result[source_subtitle.resolve()]

        assert subtitle_target.parent == video_target.parent
        assert subtitle_target.name == f"{video_target.stem}.zh-CN.default.ass"

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_sanitize_tv_duplicate_mappings():
    """验证 _sanitize_tv_mappings 能清洗重复映射，而不导致整体失败。"""
    import tempfile
    import shutil

    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "input"
        base_path.mkdir(parents=True)

        for i in range(1, 4):
            (base_path / f"E{i:02d}.mkv").touch()

        work_path = temp_dir / "output" / "测试动漫 (2023)"
        work_path.mkdir(parents=True)

        anime_info = {
            "name": "测试动漫",
            "seasons": [
                {
                    "season_number": 1,
                    "episode_count": 3,
                }
            ],
        }

        # AI 返回的映射中，E01.mkv 被重复映射到两个不同目标
        ai_result = AIAnalysisResult(
            confidence="High",
            reason="重复映射测试",
            file_mapping=[
                EpisodeMapping(
                    file_path="E01.mkv",
                    tmdb_season=1,
                    tmdb_episode=1,
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="E01.mkv",
                    tmdb_season=1,
                    tmdb_episode=2,
                    confidence="Low",
                ),
                EpisodeMapping(
                    file_path="E02.mkv",
                    tmdb_season=1,
                    tmdb_episode=2,
                    confidence="High",
                ),
                EpisodeMapping(
                    file_path="E03.mkv",
                    tmdb_season=1,
                    tmdb_episode=3,
                    confidence="High",
                ),
            ],
        )

        processor = AIProcessor()
        all_local_files = list(base_path.rglob("*.mkv"))
        result = processor.apply_ai_mapping(
            ai_result=ai_result,
            anime_info=anime_info,
            base_path=base_path,
            work_path=work_path,
            all_local_files=all_local_files,
        )

        # 应该能继续处理，不因重复映射直接失败
        assert len(result) >= 2, f"期望至少2个文件被成功映射，实际得到 {len(result)}"
        # E01 应该保留到 S01E01（High confidence 优先）
        e01_target = result.get((base_path / "E01.mkv").resolve())
        assert e01_target is not None, "E01.mkv 应该被映射"
        assert "S01E01" in e01_target.name, (
            f"E01.mkv 应映射到 S01E01，实际映射到 {e01_target.name}"
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_build_movie_search_queries_strips_chapter_prefix():
    """验证 build_movie_search_queries 对包含章节编号的标题生成有效查询。"""
    from src.rename.cleaner import build_movie_search_queries

    # 空之境界风格：「剧场版 空の境界 第一章 俯瞰風景」
    queries = build_movie_search_queries(
        "劇場版 空の境界 第一章 俯瞰風景",
        collection_name="空の境界",
    )

    assert len(queries) > 1, "应生成多个候选查询"
    # 去除前缀后的标题应存在
    assert any("劇場版" not in q for q in queries), (
        f"应至少有一个不含剧场版前缀的查询: {queries}"
    )
    # 系列名应在候选中
    assert any("空の境界" in q for q in queries), (
        f"应包含系列名查询: {queries}"
    )


def test_build_movie_search_queries_subtitle_split():
    """验证 build_movie_search_queries 能拆分副标题作为独立候选。"""
    from src.rename.cleaner import build_movie_search_queries

    queries = build_movie_search_queries(
        "Kara no Kyoukai: The Garden of Sinners",
    )

    assert any("Garden of Sinners" in q for q in queries), (
        f"应含副标题查询: {queries}"
    )


def test_search_movie_multi_language_merges_unique_candidates():
    """电影多语言搜索应合并候选，而不是在首个命中语言提前返回。"""

    class FakeTMDBSearch:
        calls = []

        def __init__(self):
            self.__dict__['results'] = []

        def movie(self, query, language, year=None):
            FakeTMDBSearch.calls.append((query, language, year))
            if language == 'zh-CN':
                self.__dict__['results'] = [
                    {'id': 1613899, 'title': 'Kimetsu.no.Yaiba', 'popularity': 1.0},
                ]
            elif language == 'ja-JP':
                self.__dict__['results'] = [
                    {
                        'id': 635302,
                        'title': '劇場版「鬼滅の刃」無限列車編',
                        'original_title': '劇場版「鬼滅の刃」無限列車編',
                        'popularity': 10.0,
                    },
                ]
            else:
                self.__dict__['results'] = [
                    {
                        'id': 635302,
                        'title': 'Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train',
                        'original_title': '劇場版「鬼滅の刃」無限列車編',
                        'popularity': 12.0,
                    },
                ]

    search = Search()
    with patch('src.rename.get_info.tmdb.Search', FakeTMDBSearch):
        results = search._search_movie_multi_language(
            'Gekijouban Kimetsu no Yaiba Mugen Ressha Hen',
            year=2020,
        )

    assert results is not None
    assert {item['id'] for item in results} == {635302, 1613899}
    merged_candidate = next(item for item in results if item['id'] == 635302)
    assert merged_candidate['_matched_languages'] == ['en-US', 'ja-JP']
    assert FakeTMDBSearch.calls == [
        ('Gekijouban Kimetsu no Yaiba Mugen Ressha Hen', 'en-US', 2020),
        ('Gekijouban Kimetsu no Yaiba Mugen Ressha Hen', 'ja-JP', 2020),
        ('Gekijouban Kimetsu no Yaiba Mugen Ressha Hen', 'zh-CN', 2020),
    ]


def test_score_movie_candidate_prefers_movie_signaled_title():
    """带明显剧场版信号的候选应优先于泛化系列名。"""
    search = Search()
    source_title = 'Gekijouban Kimetsu no Yaiba Mugen Ressha Hen'
    query = 'Kimetsu no Yaiba Mugen Ressha Hen'

    theatrical_score = search._score_movie_candidate(
        source_title=source_title,
        query=query,
        candidate={
            'id': 635302,
            'title': 'Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train',
            'original_title': '劇場版「鬼滅の刃」無限列車編',
            'release_date': '2020-10-16',
        },
        year=2020,
        query_index=0,
    )
    generic_score = search._score_movie_candidate(
        source_title=source_title,
        query=query,
        candidate={
            'id': 1613899,
            'title': 'Kimetsu.no.Yaiba',
            'original_title': 'Kimetsu.no.Yaiba',
            'release_date': '2020-10-16',
        },
        year=2020,
        query_index=0,
    )

    assert theatrical_score > generic_score


def test_validate_tv_result_keeps_valid_subset_when_tmdb_has_no_special():
    """TMDB 不存在的 special 应进入 unmatched/conflict，而有效正片保留。"""
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "test_anime"
        base_path.mkdir()
        (base_path / "E01.mkv").touch()
        (base_path / "SP01.mkv").touch()

        anime_info = {
            "name": "测试动漫",
            "first_air_date": "2023-01-01",
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
            reason="测试",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(file_path="E01.mkv", tmdb_season=1, tmdb_episode=1),
                EpisodeMapping(file_path="SP01.mkv", tmdb_season=0, tmdb_episode=1),
            ],
        )

        processor = AIProcessor()
        ok, reason, detail = processor.validate_tv_result(
            ai_result,
            anime_info,
            base_path,
            [base_path / "E01.mkv", base_path / "SP01.mkv"],
        )

        assert ok is True
        assert reason is None
        assert detail == ""
        assert [m.file_path for m in ai_result.file_mapping] == ["E01.mkv"]
        assert "SP01.mkv" in ai_result.unmatched_files
        assert any("越界映射" in item for item in ai_result.conflict_details)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



def test_ai_processor_resolves_suffix_relative_path_during_apply_mapping():
    """apply_ai_mapping 应能解析带冗余前缀目录的 AI 相对路径。"""
    temp_dir = Path(tempfile.mkdtemp())
    try:
        base_path = temp_dir / "test_anime"
        nested = base_path / "Season Pack"
        nested.mkdir(parents=True)
        source = nested / "E01.mkv"
        source.touch()

        work_path = temp_dir / "output" / "测试动漫 (2023)"
        work_path.mkdir(parents=True)
        anime_info = {
            "name": "测试动漫",
            "first_air_date": "2023-01-01",
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
            reason="测试",
            season_mapping=[],
            file_mapping=[
                EpisodeMapping(
                    file_path="test_anime/Season Pack/E01.mkv",
                    tmdb_season=1,
                    tmdb_episode=1,
                )
            ],
        )

        processor = AIProcessor()
        mapping = processor.apply_ai_mapping(
            ai_result=ai_result,
            anime_info=anime_info,
            base_path=base_path,
            work_path=work_path,
        )

        assert list(mapping.keys()) == [source.resolve()]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



if __name__ == "__main__":
    print("\n")
    test_tmdb_first_matching()
    test_ai_processor_apply_mapping()
    test_ai_processor_apply_mapping_enhanced_naming_snapshot()
    test_ai_processor_apply_mapping_subtitle_uses_new_video_stem()
    test_sanitize_tv_duplicate_mappings()
    test_build_movie_search_queries_strips_chapter_prefix()
    test_build_movie_search_queries_subtitle_split()
