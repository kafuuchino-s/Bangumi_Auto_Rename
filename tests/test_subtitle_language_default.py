"""字幕语言归一化单测：未命中已知标签时不误标简体中文 default。

验证 ``_build_emby_subtitle_name`` / ``_normalize_subtitle_language``
（src/rename/process.py）和 ``SubtitleProcessor._normalize_language``
（src/subtitle/processor.py）在未检测到语言标记时返回中性中文 ``zh`` 且
不加 ``.default``，避免日文/英文/未标记字幕被 Emby 当默认中文字幕选中。
"""
from __future__ import annotations

from pathlib import Path

from src.rename.process import (
    _build_emby_subtitle_name,
    _normalize_subtitle_language,
)
from src.subtitle.processor import SubtitleProcessor


def test_normalize_subtitle_language_unknown_no_default():
    """未命中语言 → ('zh', False)，不返回简体 default。"""
    assert _normalize_subtitle_language(None) == ('zh', False)
    assert _normalize_subtitle_language('') == ('zh', False)
    # 未知非空标签保持原样、不 default
    assert _normalize_subtitle_language('xyz') == ('xyz', False)


def test_normalize_subtitle_language_known_simplified_keeps_default():
    """明确命中简体标记 → ('zh-CN', True)，保留 .default。"""
    assert _normalize_subtitle_language('chs') == ('zh-CN', True)
    assert _normalize_subtitle_language('简体') == ('zh-CN', True)


def test_build_emby_subtitle_name_unknown_no_default_suffix():
    """无语言标记的字幕文件名 → ``video.zh.ass``，不含 ``.default``。"""
    name = _build_emby_subtitle_name(Path('video.ass'), 'Show - S01E01')
    assert name == 'Show - S01E01.zh.ass'
    assert '.default' not in name


def test_build_emby_subtitle_name_simplified_keeps_default():
    """简体标记字幕 → ``video.zh-CN.default.ass``。"""
    name = _build_emby_subtitle_name(
        Path('video.chs.ass'), 'Show - S01E01'
    )
    assert name == 'Show - S01E01.zh-CN.default.ass'


def test_processor_normalize_language_unknown_no_default():
    """processor 路径同样：未检测到语言 → ('zh', False)。"""
    proc = SubtitleProcessor.__new__(SubtitleProcessor)
    assert proc._normalize_language(None) == ('zh', False)
    assert proc._normalize_language('') == ('zh', False)
    assert proc._normalize_language('chs') == ('zh-CN', True)
