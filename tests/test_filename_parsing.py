"""测试 cleaner.py 与 filename_builder.py 的命名能力"""

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
from src.rename.filename_builder import (  # noqa: E402
    EpisodeMetadata,
    FilenameBuilder,
    MovieMetadata,
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


def test_build_movie_filename_with_full_resource_snapshot():
    meta = MovieMetadata(
        title="Inception",
        year="2010",
        resource_term="BluRay 2160p HEVC 10bit HDR10 DTS",
        release_group="FraMeSToR",
        file_ext=".mkv",
    )

    assert (
        FilenameBuilder.build_movie_filename(meta)
        == "Inception (2010) - BluRay 2160p HEVC 10bit HDR10 DTS - FraMeSToR.mkv"
    )


def test_build_movie_filename_with_part_snapshot():
    meta = MovieMetadata(
        title="空之境界",
        year="2007",
        part="Part1",
        resource_term="BluRay 1080p HEVC",
        release_group="VCB-Studio",
        file_ext=".mkv",
    )

    assert (
        FilenameBuilder.build_movie_filename(meta)
        == "空之境界 (2007)-Part1 - BluRay 1080p HEVC - VCB-Studio.mkv"
    )


def test_build_episode_filename_with_full_resource_snapshot():
    meta = EpisodeMetadata(
        title="葬送的芙莉莲",
        season=1,
        episode=1,
        resource_term="WEB-DL 1080p HEVC 10bit AAC",
        release_group="LoliHouse",
        file_ext=".mkv",
    )

    assert (
        FilenameBuilder.build_episode_filename(meta)
        == "葬送的芙莉莲 - S01E01 - WEB-DL 1080p HEVC 10bit AAC - LoliHouse.mkv"
    )


def test_build_episode_filename_with_part_snapshot():
    meta = EpisodeMetadata(
        title="葬送的芙莉莲",
        season=1,
        episode=1,
        part="Part1",
        resource_term="WEB-DL 1080p HEVC",
        release_group="LoliHouse",
        file_ext=".mkv",
    )

    assert (
        FilenameBuilder.build_episode_filename(meta)
        == "葬送的芙莉莲 - S01E01-Part1 - WEB-DL 1080p HEVC - LoliHouse.mkv"
    )


def test_build_episode_filename_minimal_snapshot():
    meta = EpisodeMetadata(
        title="葬送的芙莉莲",
        season=1,
        episode=1,
        file_ext=".mkv",
    )

    assert FilenameBuilder.build_episode_filename(meta) == "葬送的芙莉莲 - S01E01.mkv"
