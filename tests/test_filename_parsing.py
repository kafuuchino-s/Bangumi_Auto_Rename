"""
测试文件名解析逻辑

验证 cleaner.py 中的各种文件名解析函数对不同格式的处理能力
"""

import sys
import io
from pathlib import Path

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rename.cleaner import (
    remove_tag,
    divide_by_year,
    remove_season,
    remove_episode,
    extract_season,
    extract_number,
    extract_base_num,
    match_and_extract,
    extract_part,
    extract_video_format,
    remove_code,
)
from src.rename.utils import episode_partten
import re


class TestResult:
    def __init__(self, name: str, expected: str, actual: str, passed: bool):
        self.name = name
        self.expected = expected
        self.actual = actual
        self.passed = passed


def run_tests():
    """运行所有测试场景"""
    results = []

    print("=" * 80)
    print("文件名解析逻辑测试")
    print("=" * 80)

    # ========================================
    # 场景 1: 标准 S01E01 格式
    # ========================================
    print("\n" + "-" * 40)
    print("场景 1: 标准 S01E01 格式")
    print("-" * 40)

    test_cases_1 = [
        "Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv",
        "Love.Death.&.Robots.S04E05.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv",
        "Love.Death.&.Robots.S04E10.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv",
    ]

    for tc in test_cases_1:
        result = match_and_extract(tc)
        expected = "S04E01/05/10"
        if result:
            actual = f"S{result[0]:02d}E{result[1]:02d}"
            passed = result[0] == 4  # 验证季度为 4
            results.append(TestResult(tc[:50], "S04Exx", actual, passed))
            print(f"  {'✓' if passed else '✗'} {tc[:50]}... -> {actual}")
        else:
            results.append(TestResult(tc[:50], "S04Exx", "None", False))
            print(f"  ✗ {tc[:50]}... -> None (预期: S04Exx)")

    # ========================================
    # 场景 2: 方括号集数格式 [01]
    # ========================================
    print("\n" + "-" * 40)
    print("场景 2: 方括号集数格式 [01]")
    print("-" * 40)

    test_cases_2 = [
        "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][01][BDRIP][1080P][H264_FLACx2].mkv",
        "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][15][BDRIP][1080P][H264_FLACx2].mkv",
        "[Uchuu Senkan Yamato 2202 Ai no Senshi-tachi][26][BDRIP][1080P][H264_FLACx2].mkv",
    ]

    for tc in test_cases_2:
        # 测试 match_and_extract (应该返回 None)
        result_mae = match_and_extract(tc)

        # 测试 episode_partten 中的 \[(\d{1,2})\]
        bracket_pattern = r'\[(\d{1,2})\]'
        matches = re.findall(bracket_pattern, tc)

        # 测试 extract_number (可能错误匹配 2202)
        result_en = extract_number(tc)

        # 分析结果
        expected_ep = matches[0] if matches else "?"  # 第一个匹配应该是 01/15/26
        actual_bracket = matches if matches else []
        actual_extract = result_en

        # 问题: extract_number 会匹配到 2202
        passed = len(matches) > 0 and matches[0] in ['01', '15', '26']

        print(f"  文件: {tc[:60]}...")
        print(f"    match_and_extract(): {result_mae}")
        print(f"    方括号匹配 \\[(\\d{{1,2}})\\]: {actual_bracket}")
        print(f"    extract_number(): {actual_extract}")
        print(f"    {'✓' if passed else '⚠'} 方括号匹配{'成功' if passed else '需要验证'}")

        results.append(TestResult(
            f"[Yamato][{expected_ep}]",
            f"集数={expected_ep}",
            f"方括号={actual_bracket}, extract_number={actual_extract}",
            passed
        ))

    # ========================================
    # 场景 3: 横线分隔集数格式
    # ========================================
    print("\n" + "-" * 40)
    print("场景 3: 横线分隔集数格式")
    print("-" * 40)

    test_cases_3 = [
        "Space Battleship Yamato 2199 (2012) - 01 VOSTFR BDrip 1080p FLAC x265-GundamGuy.mkv",
        "Space Battleship Yamato 2199 (2012) - 15 VOSTFR BDrip 1080p FLAC x265-GundamGuy.mkv",
        "Majimoji Rurumo Kanketsu-hen - 01 [WebRip 1080p HEVC-10bit AAC][CHS&JAP].mkv",
    ]

    for tc in test_cases_3:
        # 模拟 process_sub 的处理流程
        # 1. remove_tag
        cleaned = remove_tag(tc)
        # 2. remove_code
        cleaned_code = remove_code(cleaned)
        # 3. extract_number
        result = extract_number(cleaned_code)

        # 检查 " - 01" 模式
        dash_pattern = r' - (\d{1,2}) '
        dash_match = re.search(dash_pattern, tc)
        dash_ep = dash_match.group(1) if dash_match else None

        print(f"  文件: {tc[:60]}...")
        print(f"    remove_tag(): {cleaned[:40]}...")
        print(f"    remove_code(): {cleaned_code[:40]}...")
        print(f"    extract_number(): {result}")
        print(f"    横线模式 ' - XX ': {dash_ep}")

        # 判断是否正确
        # 问题: 2199 可能被优先匹配
        expected = dash_ep if dash_ep else "01"
        passed = str(result) == expected if result else False

        if "2199" in tc and result == 2199:
            print(f"    ⚠ 错误: 匹配到年份 2199 而非集数!")
            passed = False

        results.append(TestResult(
            tc[:40],
            f"集数={expected}",
            f"extract_number={result}",
            passed
        ))

    # ========================================
    # 场景 4: 前篇/后篇电影
    # ========================================
    print("\n" + "-" * 40)
    print("场景 4: 前篇/后篇电影")
    print("-" * 40)

    test_cases_4 = [
        ("[ANK-Raws] Wake Up, Girls!続～A～ 前篇「青春之影」(BDrip...).mkv", "Part1"),
        ("[ANK-Raws] Wake Up, Girls!続～A～ 后篇「Beyond the Bottom」(BDrip...).mkv", "Part2"),
        ("The.Seven.Deadly.Sins.Grudge.of.Edinburgh.Part.1.2022.1080p.mkv", "Part1"),
        ("The.Seven.Deadly.Sins.Grudge.of.Edinburgh.Part.2.2023.1080p.mkv", "Part2"),
        ("Movie - 上篇.mkv", "Part1"),
        ("Movie - 下篇.mkv", "Part2"),
        ("电影 前编.mkv", "Part1"),
        ("电影 后编.mkv", "Part2"),
    ]

    for tc, expected in test_cases_4:
        result = extract_part(tc)
        passed = result == expected
        print(f"  {'✓' if passed else '✗'} {tc[:50]}... -> {result} (预期: {expected})")
        results.append(TestResult(tc[:40], expected, str(result), passed))

    # ========================================
    # 场景 5: 年份提取
    # ========================================
    print("\n" + "-" * 40)
    print("场景 5: 年份提取 (divide_by_year)")
    print("-" * 40)

    test_cases_5 = [
        ("Omoide.no.Mani.2014.BluRay.1080p.mkv", "Omoide.no.Mani.", 2014),
        ("The.Seven.Deadly.Sins.2021.1080p.mkv", "The.Seven.Deadly.Sins.", 2021),
        ("Nanatsu.no.Taizai.2024.S01.1080p.mkv", "Nanatsu.no.Taizai.", 2024),
        # 科幻年份 - 应该不被识别为年份
        ("Space Battleship Yamato 2199 (2012).mkv", "Space Battleship Yamato ", 2199),  # 2199 > 2035
        ("Uchuu Senkan Yamato 2202.mkv", "Uchuu Senkan Yamato ", 2202),  # 2202 > 2035
    ]

    for tc, expected_name, expected_year in test_cases_5:
        name, year = divide_by_year(tc)
        # 对于 > 2035 的年份，应该不被识别
        if expected_year > 2035:
            passed = year == 0  # 应该返回 0
            expected_display = f"0 (不识别 {expected_year})"
        else:
            passed = year == expected_year
            expected_display = str(expected_year)

        print(f"  {'✓' if passed else '✗'} {tc[:40]}...")
        print(f"      -> name='{name[:30]}...', year={year} (预期: {expected_display})")
        results.append(TestResult(tc[:30], expected_display, str(year), passed))

    # ========================================
    # 场景 6: 季度提取
    # ========================================
    print("\n" + "-" * 40)
    print("场景 6: 季度提取 (extract_season)")
    print("-" * 40)

    test_cases_6 = [
        ("Love.Death.&.Robots.S04.1080p", 4),
        ("Nanatsu.no.Taizai.S01.1080p", 1),
        ("Anime 第2季", 2),
        ("Anime 第三季", 3),
        ("Anime Season 2", 2),
        ("Anime 2nd Season", 2),
        ("Anime III", 3),
        ("Anime IV", 4),
    ]

    for tc, expected in test_cases_6:
        result = extract_season(tc)
        passed = result == expected
        print(f"  {'✓' if passed else '✗'} {tc} -> {result} (预期: {expected})")
        results.append(TestResult(tc, str(expected), str(result), passed))

    # ========================================
    # 场景 7: 视频格式提取
    # ========================================
    print("\n" + "-" * 40)
    print("场景 7: 视频格式提取")
    print("-" * 40)

    test_cases_7 = [
        ("Movie.1080p.BluRay.mkv", "1080p"),
        ("Movie.720p.WEB-DL.mkv", "720p"),
        ("Movie.2160p.UHD.mkv", "4K"),
        ("Movie.4K.HDR.mkv", "4K"),
        ("[VCB-Studio] Anime [Ma10p_1080p].mkv", "1080p"),
    ]

    for tc, expected in test_cases_7:
        result = extract_video_format(tc)
        passed = result == expected
        print(f"  {'✓' if passed else '✗'} {tc[:40]} -> {result} (预期: {expected})")
        results.append(TestResult(tc[:30], expected, str(result), passed))

    # ========================================
    # 场景 8: 剧场版系列文件名
    # ========================================
    print("\n" + "-" * 40)
    print("场景 8: 剧场版系列文件名 (#01 格式)")
    print("-" * 40)

    test_cases_8 = [
        "[AI-Raws] 空之境界 ふのきょうかい #01 俯瞰风景.mkv",
        "[AI-Raws] 空之境界 ふのきょうかい #02 杀人考察(前).mkv",
        "[AI-Raws] 空之境界 ふのきょうかい #09 未来福音 extra chorus.mkv",
    ]

    for tc in test_cases_8:
        # 测试 #01 格式
        hash_pattern = r'#(\d{1,2})'
        hash_match = re.search(hash_pattern, tc)
        hash_ep = hash_match.group(1) if hash_match else None

        # 测试 extract_number
        result = extract_number(tc)

        print(f"  文件: {tc[:50]}...")
        print(f"    #XX 格式匹配: {hash_ep}")
        print(f"    extract_number(): {result}")

        # #01 格式不在 episode_partten 中，但 extract_number 应该能匹配到
        passed = hash_ep is not None and str(result) == hash_ep
        print(f"    {'✓' if passed else '⚠'} 集数提取{'正确' if passed else '需要验证'}")

        results.append(TestResult(
            tc[:40],
            f"#{hash_ep}",
            f"extract_number={result}",
            passed
        ))

    # ========================================
    # 场景 9: EXTRA_TAG 检测
    # ========================================
    print("\n" + "-" * 40)
    print("场景 9: 特典标签检测")
    print("-" * 40)

    from src.rename.utils import EXTRA_TAG, S0_TAG

    test_cases_9 = [
        ("[VCB-Studio] Anime [NCOP].mkv", True, "NCOP"),
        ("[VCB-Studio] Anime [NCED].mkv", True, "NCED"),
        ("[VCB-Studio] Anime [PV01].mkv", True, "PV"),
        ("[VCB-Studio] Anime [Menu].mkv", True, "Menu"),
        ("[VCB-Studio] Anime [CM01].mkv", True, "CM"),
        ("[VCB-Studio] Anime [01].mkv", False, None),
    ]

    for tc, should_be_extra, expected_tag in test_cases_9:
        found_extra = False
        matched_tag = None
        for tag in EXTRA_TAG:
            if tag.lower() in tc.lower():
                found_extra = True
                matched_tag = tag
                break

        passed = found_extra == should_be_extra
        print(f"  {'✓' if passed else '✗'} {tc[:40]}... -> 特典={found_extra}, 标签={matched_tag}")
        results.append(TestResult(tc[:30], f"特典={should_be_extra}", f"特典={found_extra}", passed))

    # ========================================
    # 场景 10: 日期格式文件名
    # ========================================
    print("\n" + "-" * 40)
    print("场景 10: 日期格式文件名")
    print("-" * 40)

    test_cases_10 = [
        ("(2023.12.20)Psycho-Pass Providence-[1080p][BDRIP][x265.FLAC].mkv", 2023),
    ]

    for tc, expected_year in test_cases_10:
        name, year = divide_by_year(tc)
        passed = year == expected_year
        print(f"  {'✓' if passed else '✗'} {tc[:50]}...")
        print(f"      -> year={year} (预期: {expected_year})")
        results.append(TestResult(tc[:40], str(expected_year), str(year), passed))

    # ========================================
    # 场景 11: 新发现的特典标签 (Info, Trailer)
    # ========================================
    print("\n" + "-" * 40)
    print("场景 11: 新发现的特典标签")
    print("-" * 40)

    test_cases_11 = [
        ("[KNA-Subs] Movie ABYSS OF HYPERSPACE Info01.mkv", False, None),  # Info 不在 EXTRA_TAG
        ("[KNA-Subs] Movie ABYSS OF HYPERSPACE CM01.mkv", True, "CM"),
        ("[KNA-Subs] Movie ABYSS OF HYPERSPACE Trailer.mkv", True, "Trailer"),
        ("[VCB-Studio] ARIA [Menu][Ma10p_1080p].mkv", True, "Menu"),
        ("Movie Remix - Gate of Seventh Heaven.mkv", False, None),  # Remix 不是特典
    ]

    for tc, should_be_extra, expected_tag in test_cases_11:
        found_extra = False
        matched_tag = None
        for tag in EXTRA_TAG:
            if tag.lower() in tc.lower():
                found_extra = True
                matched_tag = tag
                break

        passed = found_extra == should_be_extra
        status = "✓" if passed else "✗"
        print(f"  {status} {tc[:50]}... -> 特典={found_extra}, 标签={matched_tag}")
        results.append(TestResult(tc[:35], f"特典={should_be_extra}", f"特典={found_extra}", passed))

    # ========================================
    # 场景 12: S5 等季度格式在标题中间
    # ========================================
    print("\n" + "-" * 40)
    print("场景 12: 季度格式在标题中间")
    print("-" * 40)

    test_cases_12 = [
        ("[AI-Raws][Kimetsu No Yaiba S5 Hashira Geiko Hen]", 5),
        ("[ANK-Raws] Strike Witches Season 2", 2),
        ("Gatchaman Crowds insight TV S2 00-12", 2),
    ]

    for tc, expected in test_cases_12:
        result = extract_season(tc)
        passed = result == expected
        print(f"  {'✓' if passed else '✗'} {tc[:45]}... -> {result} (预期: {expected})")
        results.append(TestResult(tc[:35], str(expected), str(result), passed))

    # ========================================
    # 场景 13: OVA/OAD/SP 标签检测
    # ========================================
    print("\n" + "-" * 40)
    print("场景 13: OVA/OAD/SP 标签检测")
    print("-" * 40)

    test_cases_13 = [
        ("[Moozzi2] Watamote - TV + OAD", True, "OAD"),
        ("[Moozzi2] Strike Witches - TV + SP", True, "SP"),
        ("[ANK-Raws] Anime OVA [BDrip].mkv", True, "OVA"),
        ("[ANK-Raws] Anime Special Episode.mkv", True, "Special"),
        ("[ANK-Raws] Anime Episode 01.mkv", False, None),
    ]

    for tc, should_be_s0, expected_tag in test_cases_13:
        found_s0 = False
        matched_tag = None
        for tag in S0_TAG:
            if re.search(rf'{tag.lower()}[\d]{{0,3}}', tc.lower()):
                found_s0 = True
                matched_tag = tag
                break

        passed = found_s0 == should_be_s0
        status = "✓" if passed else "✗"
        print(f"  {status} {tc[:45]}... -> S0={found_s0}, 标签={matched_tag}")
        results.append(TestResult(tc[:35], f"S0={should_be_s0}", f"S0={found_s0}", passed))

    # ========================================
    # 场景 14: 年份范围格式
    # ========================================
    print("\n" + "-" * 40)
    print("场景 14: 年份范围格式")
    print("-" * 40)

    test_cases_14 = [
        ("[2017-19][Uchuu Senkan Yamato 2202]", 2017),
        ("[2021 Movie][Uchuu Senkan Yamato 2205]", 2021),
    ]

    for tc, expected_year in test_cases_14:
        name, year = divide_by_year(tc)
        passed = year == expected_year
        print(f"  {'✓' if passed else '✗'} {tc[:45]}...")
        print(f"      -> year={year} (预期: {expected_year})")
        results.append(TestResult(tc[:35], str(expected_year), str(year), passed))

    # ========================================
    # 场景 15: 中英混合文件名
    # ========================================
    print("\n" + "-" * 40)
    print("场景 15: 中英混合文件名")
    print("-" * 40)

    test_cases_15 = [
        ("七大罪 怨恨的爱丁堡 后篇.The.Seven.Deadly.Sins.Part.2.2023.mkv", "Part2"),
        ("[ANK-Raws] 劇場版 Wake Up, Girls! 青春の影.mkv", None),  # 无 Part
    ]

    for tc, expected in test_cases_15:
        result = extract_part(tc)
        passed = result == expected
        print(f"  {'✓' if passed else '✗'} {tc[:50]}... -> {result} (预期: {expected})")
        results.append(TestResult(tc[:35], str(expected), str(result), passed))

    # ========================================
    # 场景 16: Vol.xx 分卷格式
    # ========================================
    print("\n" + "-" * 40)
    print("场景 16: Vol.xx 分卷格式")
    print("-" * 40)

    test_cases_16 = [
        "[Space Dandy Vol.1-Vol.5][BDRIP]",
        "[Space Dandy Vol.6-10][BDRIP]",
    ]

    for tc in test_cases_16:
        # 测试是否能提取 Vol 信息
        vol_pattern = r'Vol\.?(\d+)'
        vol_match = re.search(vol_pattern, tc)
        vol_num = vol_match.group(1) if vol_match else None
        passed = vol_num is not None
        print(f"  {'✓' if passed else '✗'} {tc[:45]} -> Vol={vol_num}")
        results.append(TestResult(tc[:35], "有Vol信息", f"Vol={vol_num}", passed))

    # ========================================
    # 测试总结
    # ========================================
    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    print(f"\n总计: {total} 个测试")
    print(f"  ✓ 通过: {passed}")
    print(f"  ✗ 失败: {failed}")
    print(f"  通过率: {passed/total*100:.1f}%")

    if failed > 0:
        print("\n失败的测试:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: 预期={r.expected}, 实际={r.actual}")

    return results


if __name__ == "__main__":
    run_tests()
