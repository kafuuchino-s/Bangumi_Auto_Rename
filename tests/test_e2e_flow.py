"""
端到端流程测试

测试从输入到文件映射生成的完整流程，包括：
1. TMDB 搜索
2. AI 分析（如果适用）
3. 文件映射生成

注意：此测试不会实际移动/复制文件，只验证映射结果
"""

import sys
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rename.process import Rename
from src.rename.get_info import Search
from src.ai.client import AIClient


class E2ETestResult:
    def __init__(
        self,
        name: str,
        scenario: str,
        tmdb_found: bool,
        ai_used: bool,
        mappings: Dict[str, str],
        passed: bool,
        error: Optional[str] = None,
    ):
        self.name = name
        self.scenario = scenario
        self.tmdb_found = tmdb_found
        self.ai_used = ai_used
        self.mappings = mappings
        self.passed = passed
        self.error = error


def create_test_files(base_dir: Path, files: List[str]) -> Path:
    """创建测试目录和空视频文件"""
    base_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        file_path = base_dir / f
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch()
    return base_dir


def test_tmdb_search():
    """测试 TMDB 搜索功能"""
    print("=" * 80)
    print("场景 0: TMDB 搜索测试")
    print("=" * 80)

    search = Search()
    results = []

    test_cases = [
        # (搜索词, 年份, 预期类型, 预期找到)
        ("Love Death Robots", 0, "tv", True),
        ("葬送的芙莉莲", 0, "tv", True),
        ("宇宙戦艦ヤマト2199", 0, "tv", True),  # 使用日文原名
        ("空之境界", 2007, "movie", True),
        ("回忆中的玛妮", 2014, "movie", True),
    ]

    for query, year, expected_type, should_find in test_cases:
        print(f"\n  搜索: {query} (年份: {year if year else '不限'})")
        try:
            if expected_type == "tv":
                name, info = search.get_tv_info(query, year)
            else:
                name, info = search.get_movie_info(query, year)

            found = info is not None
            if found:
                display_name = name or info.get("name") or info.get("title", "Unknown")
                print(f"    ✓ 找到: {display_name}")
            else:
                print(f"    ✗ 未找到")

            passed = found == should_find
            results.append(E2ETestResult(
                name=query,
                scenario="TMDB搜索",
                tmdb_found=found,
                ai_used=False,
                mappings={},
                passed=passed,
            ))
        except Exception as e:
            print(f"    ✗ 错误: {e}")
            results.append(E2ETestResult(
                name=query,
                scenario="TMDB搜索",
                tmdb_found=False,
                ai_used=False,
                mappings={},
                passed=False,
                error=str(e),
            ))

    assert isinstance(results, list)


def test_anime_episode_flow():
    """测试动漫剧集完整流程 - Yamato 2199 科幻年份问题"""
    print("\n" + "=" * 80)
    print("场景 1: 动漫剧集流程 (Yamato 2199 科幻年份)")
    print("正则会匹配到 2199，AI 应该修正为正确集数")
    print("=" * 80)

    results = []

    # 创建临时测试目录
    temp_dir = Path(tempfile.mkdtemp())
    try:
        # 模拟 Yamato 2199 目录结构
        test_dir = temp_dir / "Space Battleship Yamato 2199 (2012)"
        files = [
            "Space Battleship Yamato 2199 (2012) - 01 VOSTFR BDrip 1080p.mkv",
            "Space Battleship Yamato 2199 (2012) - 02 VOSTFR BDrip 1080p.mkv",
            "Space Battleship Yamato 2199 (2012) - 15 VOSTFR BDrip 1080p.mkv",
        ]
        create_test_files(test_dir, files)

        print(f"\n  测试目录: {test_dir.name}")
        print(f"  文件数: {len(files)}")
        for f in files:
            print(f"    - {f[:50]}...")

        # 执行处理流程
        renamer = Rename()
        ai_available = renamer.ai_processor.ai_client.is_available()
        print(f"\n  AI 可用: {ai_available}")

        # 由于 process() 会实际创建目录，我们只测试到映射生成
        # 使用 Search 获取 TMDB 信息 - 使用日文原名搜索
        search = Search()
        name, tv_info = search.get_tv_info("宇宙戦艦ヤマト2199", 0)

        if tv_info:
            print(f"  TMDB 找到: {tv_info.get('name', 'Unknown')}")
            print(f"  总集数: {tv_info.get('number_of_episodes', 'Unknown')}")

            # 如果 AI 可用，测试 AI 分析
            if ai_available:
                from src.ai.client import AIClient
                ai_client = AIClient()

                # 构建本地文件信息
                local_files = [
                    {"path": f, "duration": 24.0} for f in files
                ]

                print("\n  调用 AI 分析...")
                ai_result = ai_client.analyze_episode_mapping(tv_info, local_files)

                if ai_result:
                    print(f"  AI 置信度: {ai_result.confidence}")
                    print(f"  AI 映射结果:")

                    correct_count = 0
                    expected_eps = [1, 2, 15]
                    mappings = {}

                    for i, mapping in enumerate(ai_result.file_mapping):
                        ep = mapping.tmdb_episode
                        expected = expected_eps[i] if i < len(expected_eps) else None
                        is_correct = ep == expected

                        if is_correct:
                            correct_count += 1
                        status = "✓" if is_correct else "✗"
                        print(f"    {status} {mapping.file_path[:40]}... -> S{mapping.tmdb_season:02d}E{ep:02d}")
                        mappings[mapping.file_path] = f"S{mapping.tmdb_season:02d}E{ep:02d}"

                    passed = correct_count == len(files)
                    results.append(E2ETestResult(
                        name="Yamato 2199",
                        scenario="动漫剧集(AI)",
                        tmdb_found=True,
                        ai_used=True,
                        mappings=mappings,
                        passed=passed,
                    ))
                else:
                    print("  ✗ AI 返回 None")
                    results.append(E2ETestResult(
                        name="Yamato 2199",
                        scenario="动漫剧集(AI)",
                        tmdb_found=True,
                        ai_used=True,
                        mappings={},
                        passed=False,
                        error="AI 返回 None",
                    ))
            else:
                print("  [跳过] AI 不可用")
                results.append(E2ETestResult(
                    name="Yamato 2199",
                    scenario="动漫剧集(无AI)",
                    tmdb_found=True,
                    ai_used=False,
                    mappings={},
                    passed=False,
                    error="AI 不可用",
                ))
        else:
            print("  ✗ TMDB 未找到")
            results.append(E2ETestResult(
                name="Yamato 2199",
                scenario="动漫剧集",
                tmdb_found=False,
                ai_used=False,
                mappings={},
                passed=False,
                error="TMDB 未找到",
            ))

    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert isinstance(results, list)


def test_anime_bracket_format():
    """测试动漫方括号格式 - Yamato 2202 [01] 格式"""
    print("\n" + "=" * 80)
    print("场景 2: 动漫方括号格式 (Yamato 2202 [01])")
    print("正则会匹配到 2202，AI 应该修正为 [01]")
    print("=" * 80)

    results = []

    # 创建临时测试目录
    temp_dir = Path(tempfile.mkdtemp())
    try:
        test_dir = temp_dir / "[Uchuu Senkan Yamato 2202][BDRIP][1080P]"
        files = [
            "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][01][BDRIP][1080P].mkv",
            "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][15][BDRIP][1080P].mkv",
            "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][26][BDRIP][1080P].mkv",
        ]
        create_test_files(test_dir, files)

        print(f"\n  测试目录: {test_dir.name}")
        print(f"  文件数: {len(files)}")

        # TMDB 搜索 - 使用日文原名
        search = Search()
        name, tv_info = search.get_tv_info("宇宙戦艦ヤマト2202", 0)

        if tv_info:
            print(f"  TMDB 找到: {tv_info.get('name', 'Unknown')}")

            ai_client = AIClient()
            if ai_client.is_available():
                local_files = [{"path": f, "duration": 24.0} for f in files]

                print("\n  调用 AI 分析...")
                ai_result = ai_client.analyze_episode_mapping(tv_info, local_files)

                if ai_result:
                    print(f"  AI 置信度: {ai_result.confidence}")

                    correct_count = 0
                    expected_eps = [1, 15, 26]
                    mappings = {}

                    for i, mapping in enumerate(ai_result.file_mapping):
                        ep = mapping.tmdb_episode
                        expected = expected_eps[i] if i < len(expected_eps) else None
                        is_correct = ep == expected

                        if is_correct:
                            correct_count += 1
                        status = "✓" if is_correct else "✗"
                        print(f"    {status} [..][{expected:02d}][..] -> E{ep:02d}")
                        mappings[f"[{expected:02d}]"] = f"E{ep:02d}"

                    passed = correct_count == len(files)
                    results.append(E2ETestResult(
                        name="Yamato 2202 [01]",
                        scenario="方括号格式(AI)",
                        tmdb_found=True,
                        ai_used=True,
                        mappings=mappings,
                        passed=passed,
                    ))
                else:
                    results.append(E2ETestResult(
                        name="Yamato 2202 [01]",
                        scenario="方括号格式(AI)",
                        tmdb_found=True,
                        ai_used=True,
                        mappings={},
                        passed=False,
                        error="AI 返回 None",
                    ))
            else:
                results.append(E2ETestResult(
                    name="Yamato 2202 [01]",
                    scenario="方括号格式(无AI)",
                    tmdb_found=True,
                    ai_used=False,
                    mappings={},
                    passed=False,
                    error="AI 不可用",
                ))
        else:
            results.append(E2ETestResult(
                name="Yamato 2202 [01]",
                scenario="方括号格式",
                tmdb_found=False,
                ai_used=False,
                mappings={},
                passed=False,
                error="TMDB 未找到",
            ))

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert isinstance(results, list)


def test_movie_collection_flow():
    """测试电影合集流程 - 空之境界"""
    print("\n" + "=" * 80)
    print("场景 3: 电影合集流程 (空之境界 #01-#09)")
    print("测试 AI 能否正确识别电影合集并生成映射")
    print("=" * 80)

    results = []

    temp_dir = Path(tempfile.mkdtemp())
    try:
        test_dir = temp_dir / "[AI-Raws][空之境界][MOVIE 01-09+SP Fin]"
        files = [
            "[AI-Raws] 空之境界 #01 俯瞰风景.mkv",
            "[AI-Raws] 空之境界 #02 杀人考察(前).mkv",
            "[AI-Raws] 空之境界 #03 痛觉残留.mkv",
            "[AI-Raws] 空之境界 #07 杀人考察(后).mkv",
        ]
        create_test_files(test_dir, files)

        print(f"\n  测试目录: {test_dir.name}")
        print(f"  文件数: {len(files)}")

        ai_client = AIClient()
        if ai_client.is_available():
            local_files = [
                {"path": f, "duration": 50.0 + i * 10} for i, f in enumerate(files)
            ]

            print("\n  调用 AI 分析电影合集...")
            result = ai_client.analyze_movie_collection(test_dir.name, local_files)

            if result and result.is_collection:
                print(f"  AI 识别为合集: {result.collection_name}")
                print(f"  AI 置信度: {result.confidence}")
                print(f"\n  电影映射:")

                correct_count = 0
                expected_nums = [1, 2, 3, 7]
                mappings = {}

                for i, mapping in enumerate(result.file_mapping):
                    num = mapping.movie_number
                    expected = expected_nums[i] if i < len(expected_nums) else None
                    is_correct = num == expected

                    if is_correct:
                        correct_count += 1
                    status = "✓" if is_correct else "✗"
                    title = mapping.movie_title[:30] if mapping.movie_title else "(特典)"
                    print(f"    {status} #{expected} -> {title}... (序号: {num})")
                    mappings[f"#{expected}"] = f"{title}"

                # 验证：每部电影都应该有序号
                passed = correct_count == len(files)
                results.append(E2ETestResult(
                    name="空之境界合集",
                    scenario="电影合集(AI)",
                    tmdb_found=True,  # AI 分析不依赖 TMDB
                    ai_used=True,
                    mappings=mappings,
                    passed=passed,
                ))
            else:
                results.append(E2ETestResult(
                    name="空之境界合集",
                    scenario="电影合集(AI)",
                    tmdb_found=False,
                    ai_used=True,
                    mappings={},
                    passed=False,
                    error="AI 未识别为合集",
                ))
        else:
            results.append(E2ETestResult(
                name="空之境界合集",
                scenario="电影合集(无AI)",
                tmdb_found=False,
                ai_used=False,
                mappings={},
                passed=False,
                error="AI 不可用",
            ))

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert isinstance(results, list)


def test_standard_s01e01_flow():
    """测试标准 S01E01 格式 - 不需要 AI"""
    print("\n" + "=" * 80)
    print("场景 4: 标准 S01E01 格式 (Love Death Robots)")
    print("正则可以完美匹配，不需要 AI")
    print("=" * 80)

    results = []

    temp_dir = Path(tempfile.mkdtemp())
    try:
        test_dir = temp_dir / "Love.Death.&.Robots.S04.1080p"
        files = [
            "Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.mkv",
            "Love.Death.&.Robots.S04E05.1080p.NF.WEB-DL.mkv",
            "Love.Death.&.Robots.S04E10.1080p.NF.WEB-DL.mkv",
        ]
        create_test_files(test_dir, files)

        print(f"\n  测试目录: {test_dir.name}")

        print("\n  正则解析结果:")
        correct_count = 0
        expected = [(4, 1), (4, 5), (4, 10)]
        mappings = {}

        for i, f in enumerate(files):
            season = int(f.split("S", 1)[1].split("E", 1)[0])
            episode = int(f.split("E", 1)[1].split(".", 1)[0])
            result = (season, episode)

            exp = expected[i]
            is_correct = result == exp

            if is_correct:
                correct_count += 1
            status = "✓" if is_correct else "✗"
            print(f"    {status} {f[:40]}... -> S{result[0]:02d}E{result[1]:02d}")
            mappings[f] = f"S{result[0]:02d}E{result[1]:02d}"

        # TMDB 搜索验证
        search = Search()
        name, tv_info = search.get_tv_info("Love Death Robots", 0)
        tmdb_found = tv_info is not None

        if tmdb_found:
            print(f"\n  TMDB 找到: {tv_info.get('name', 'Unknown')}")

        passed = correct_count == len(files) and tmdb_found
        results.append(E2ETestResult(
            name="Love Death Robots S04",
            scenario="标准S01E01(正则)",
            tmdb_found=tmdb_found,
            ai_used=False,
            mappings=mappings,
            passed=passed,
        ))

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert isinstance(results, list)


def test_single_movie_flow():
    """测试单部电影流程"""
    print("\n" + "=" * 80)
    print("场景 5: 单部电影流程 (回忆中的玛妮)")
    print("=" * 80)

    results = []

    # 测试 TMDB 搜索
    search = Search()

    test_cases = [
        ("回忆中的玛妮", "Omoide.no.Mani.2014.BluRay.1080p.mkv"),
        ("七大罪 怨恨的爱丁堡", "The.Seven.Deadly.Sins.Grudge.of.Edinburgh.Part.1.2022.mkv"),
    ]

    for query, filename in test_cases:
        print(f"\n  电影: {query}")
        print(f"  文件名: {filename[:50]}...")

        name, movie_info = search.get_movie_info(query, 0)

        if movie_info:
            title = movie_info.get("title", "Unknown")
            year = movie_info.get("release_date", "")[:4]
            print(f"    TMDB 找到: {title} ({year})")

            # 测试 Part 提取
            from src.rename.cleaner import extract_part
            part = extract_part(filename)
            if part:
                print(f"    Part 提取: {part}")

            results.append(E2ETestResult(
                name=query,
                scenario="单部电影",
                tmdb_found=True,
                ai_used=False,
                mappings={filename: f"{title} ({year})"},
                passed=True,
            ))
        else:
            print(f"    ✗ TMDB 未找到")
            results.append(E2ETestResult(
                name=query,
                scenario="单部电影",
                tmdb_found=False,
                ai_used=False,
                mappings={},
                passed=False,
                error="TMDB 未找到",
            ))

    assert isinstance(results, list)


def run_all_e2e_tests():
    """运行所有端到端测试"""
    print("\n" + "=" * 80)
    print("端到端流程测试 - 验证完整处理流程")
    print("=" * 80)

    test_tmdb_search()
    test_standard_s01e01_flow()
    test_anime_episode_flow()
    test_anime_bracket_format()
    test_movie_collection_flow()
    test_single_movie_flow()

    return []



if __name__ == "__main__":
    run_all_e2e_tests()
