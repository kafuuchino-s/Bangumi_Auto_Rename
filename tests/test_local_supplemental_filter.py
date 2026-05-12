from src.rename.local_supplemental_filter import classify_local_video_supplemental


def _is_filtered(path: str) -> bool:
    return classify_local_video_supplemental(path, is_video=True).is_supplemental


def test_filters_high_confidence_video_extras():
    paths = [
        'SPs/[VCB-Studio] ARIA The AVVENIRE [Menu][Ma10p_1080p][x265].mkv',
        'Series/[Group] Show [PV01][BDRip].mkv',
        'Series/[Group] Show [BD CM 001][BDRip].mkv',
        'Series/[Group] Show [NCOP][BDRip].mkv',
        'Series/[Group] Show [NCED Ver.2][BDRip].mkv',
        'Series/Special Ending Movie.mkv',
        'Series/Show 01 Non Telop Ver.mkv',
        'Series/Show 01.No_Telop_Ver.mkv',
        'Series/Show 01 ノンテロップ Ver.mkv',
        'Series/[Teaser 01].mkv',
        'Series/[S1 Recap 2].mkv',
        'Series/Show #8振り返りアバン.mkv',
        'Series/WiKi.sample.mkv',
        'Series/Show Preview.mkv',
        'Series/Show Disc01.mkv',
        'Series/Show 片头.mkv',
        'Series/Show ノンクレジットOP.mkv',
    ]

    assert all(_is_filtered(path) for path in paths)


def test_filters_high_confidence_extra_directories():
    paths = [
        '__abcdef/work.mkv',
        'CDs/01.flac',
        'Scans/cover.png',
        'Bonus/video.mkv',
        'Extra/video.mkv',
        'Extras/video.mkv',
        'specials/extra.mkv',
        '特典CD/01.flac',
        '映像特典/bonus.mkv',
        '映像/bonus.mkv',
        'Menu (前篇)/Menu.mkv',
        'Logo/logo.mkv',
        'Preview/preview.mkv',
        'mv/music_video.mkv',
    ]

    assert all(classify_local_video_supplemental(path, is_video=True).is_supplemental for path in paths)


def test_keeps_mapping_relevant_special_like_videos():
    paths = [
        'Series/Show #00.mkv',
        'Series/Show #12DC.mkv',
        'Series/Show OVA.mkv',
        'Series/Show SP.mkv',
        'Series/Show Special.mkv',
        'Series/Show Movie.mkv',
        '[KTXP] Mushishi Zoku Shou [BDRip 1080p FLAC].mkv',
        '[LoliHouse] Show [BDRip 1080p FLAC].mkv',
    ]

    assert not any(_is_filtered(path) for path in paths)


def test_does_not_filter_substrings_inside_words():
    paths = [
        'Series/Operation Victory Arrow 01.mkv',
        'Series/Comedy Show 01.mkv',
        'Series/Discotek Release 01.mkv',
    ]

    assert not any(_is_filtered(path) for path in paths)
