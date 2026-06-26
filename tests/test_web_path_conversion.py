"""web.py::convert_host_path_to_docker 宿主机→Docker 路径转换测试。

覆盖：
- 前缀匹配（正斜杠 / 反斜杠两种宿主机前缀写法）
- Windows 盘符大小写无关（H:\\ 与 h:\\ 同一卷，均应转换）
- 前缀为空 → 不转换（原样返回）
- 不匹配前缀 → 原样返回
- docker_mnt 结尾斜杠归一
- 子路径大小写原样保留（只对前缀段做大小写无关匹配）
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.config.config_manager import cm
from src.web import convert_host_path_to_docker


def _patch_config(monkeypatch, **overrides):
    """用 monkeypatch 注入 host_path_prefix / docker_mnt 等配置。"""
    base = {
        "host_path_prefix": "",
        "docker_mnt": "/media",
    }
    base.update(overrides)

    def fake_get_config(key, default=None):
        if key in base:
            return base[key]
        return default

    # web.py 用 `from .config.config_manager import cm` 拿单例，patch 实例方法。
    monkeypatch.setattr(cm, "get_config", fake_get_config)


def test_empty_prefix_no_conversion(monkeypatch):
    """host_path_prefix 为空 → 不做转换，原样返回。"""
    _patch_config(monkeypatch, host_path_prefix="", docker_mnt="/media")
    assert convert_host_path_to_docker("H:\\Emby\\Movie\\film.mkv") == "H:\\Emby\\Movie\\film.mkv"


def test_uppercase_drive_matches(monkeypatch):
    """大写盘符 H:\\ 前缀匹配 → 转换为 /media 下路径。"""
    _patch_config(monkeypatch, host_path_prefix="H:\\", docker_mnt="/media")
    out = convert_host_path_to_docker("H:\\Emby\\Movie\\film.mkv")
    assert out == "/media/Emby/Movie/film.mkv"


def test_lowercase_drive_matches(monkeypatch):
    """小写盘符 h:\\ 与配置大写 H:\\ 同一卷，应同样转换（Windows 盘符大小写无关）。"""
    _patch_config(monkeypatch, host_path_prefix="H:\\", docker_mnt="/media")
    out = convert_host_path_to_docker("h:\\Emby\\Movie\\film.mkv")
    assert out == "/media/Emby/Movie/film.mkv"


def test_prefix_case_mismatch_lowercase_config(monkeypatch):
    """配置小写 h:\\，路径大写 H:\\ 也应转换（双向大小写无关）。"""
    _patch_config(monkeypatch, host_path_prefix="h:\\", docker_mnt="/media")
    out = convert_host_path_to_docker("H:\\Emby\\Movie\\film.mkv")
    assert out == "/media/Emby/Movie/film.mkv"


def test_suffix_case_preserved(monkeypatch):
    """前缀段大小写无关匹配，但后段子路径原始大小写必须保留。"""
    _patch_config(monkeypatch, host_path_prefix="H:\\", docker_mnt="/media")
    out = convert_host_path_to_docker("h:\\Emby\\MyMovie\\Film.MKV")
    assert out == "/media/Emby/MyMovie/Film.MKV"


def test_no_match_returns_as_is(monkeypatch):
    """路径不以配置前缀开头 → 原样返回（不强行转换）。"""
    _patch_config(monkeypatch, host_path_prefix="H:\\", docker_mnt="/media")
    assert convert_host_path_to_docker("D:\\Other\\dir\\x.mkv") == "D:\\Other\\dir\\x.mkv"


def test_forward_slash_prefix(monkeypatch):
    """宿主机前缀用正斜杠写法（部分 qB 配置）也应匹配。"""
    _patch_config(monkeypatch, host_path_prefix="H:/", docker_mnt="/media")
    out = convert_host_path_to_docker("H:/Emby/Movie/film.mkv")
    assert out == "/media/Emby/Movie/film.mkv"


def test_docker_mnt_trailing_slash_normalized(monkeypatch):
    """docker_mnt 带结尾斜杠不应产生双斜杠。"""
    _patch_config(monkeypatch, host_path_prefix="H:\\", docker_mnt="/media/")
    out = convert_host_path_to_docker("H:\\Emby\\Movie\\film.mkv")
    assert out == "/media/Emby/Movie/film.mkv"


def test_prefix_trailing_slash_normalized(monkeypatch):
    """host_path_prefix 带结尾斜杠也能匹配。"""
    _patch_config(monkeypatch, host_path_prefix="H:\\\\", docker_mnt="/media")
    out = convert_host_path_to_docker("H:\\Emby\\Movie\\film.mkv")
    assert out == "/media/Emby/Movie/film.mkv"


def test_subpath_only_under_prefix(monkeypatch):
    """只有前缀本身、无子路径时返回 docker_mnt 根（输入带尾斜杠则输出带尾斜杠，既定行为）。"""
    _patch_config(monkeypatch, host_path_prefix="H:\\", docker_mnt="/media")
    out = convert_host_path_to_docker("H:\\")
    assert out in {"/media", "/media/"}
