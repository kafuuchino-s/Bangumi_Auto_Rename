"""测试 cleaner.py 当前公开的文件名解析能力"""

import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rename.cleaner import (  # noqa: E402
    divide_by_year,
    extract_part,
    extract_video_format,
    is_chinese_percentage_sufficient,
    is_promotional_content,
    remove_episode,
    remove_season,
    remove_tag,
)


def test_divide_by_year():
    name, year = divide_by_year("Omoide.no.Mani.2014.BluRay.1080p.mkv")
    assert year == 2014
    assert "Omoide.no.Mani." in name

    # 科幻标题中的 2202 不应被当作年份
    _, sci_fi_year = divide_by_year("Uchuu Senkan Yamato 2202.mkv")
    assert sci_fi_year == 0


def test_extract_part():
    assert extract_part("Movie - Part 1.mkv") == "Part1"
    assert extract_part("Movie pt2.mkv") == "Part2"
    assert extract_part("电影 前编.mkv") == "Part1"
    assert extract_part("电影 后编.mkv") == "Part2"
    assert extract_part("普通电影.mkv") is None


def test_extract_video_format():
    assert extract_video_format("Movie.1080p.BluRay.mkv") == "1080p"
    assert extract_video_format("Movie.720p.WEB-DL.mkv") == "720p"
    assert extract_video_format("Movie.2160p.UHD.mkv") == "4K"
    assert extract_video_format("Movie.4K.HDR.mkv") == "4K"
    assert extract_video_format("Movie.DVDRip.mkv") is None


def test_remove_season_and_episode():
    mid = remove_season("Love.Death.&.Robots.S04E05.1080p.mkv")
    assert "S04" not in mid
    assert "E05" in mid

    final = remove_episode(mid)
    assert "E05" not in final


def test_is_promotional_content():
    assert is_promotional_content("[VCB-Studio] Anime [NCOP].mkv")
    assert is_promotional_content("[VCB-Studio] Anime [PV01].mkv")
    assert is_promotional_content("CM_01.mkv")
    assert not is_promotional_content("[VCB-Studio] Anime [01].mkv")
    assert not is_promotional_content("Anime Episode 01.mkv")


def test_remove_tag():
    title = "[LoliHouse] Shangri / 香格里拉 [WebRip 1080p HEVC-10bit AAC]【简繁内封字幕】"
    cleaned = remove_tag(title)

    assert "Shangri" in cleaned
    assert "WebRip" not in cleaned
    assert "简繁" not in cleaned


def test_is_chinese_percentage_sufficient():
    assert is_chinese_percentage_sufficient("香格里拉 Shangri")
    assert not is_chinese_percentage_sufficient("Love Death Robots")
