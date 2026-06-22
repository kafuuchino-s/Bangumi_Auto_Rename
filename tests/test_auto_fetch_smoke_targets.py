"""smoke 脚本 synthesize_targets 回归测试（不起 Pi/acgrip，纯函数）。

重点：多 movie 合集（如空之境界 7 部剧场版）每个 movie:<id> 有独立 TMDB
title/year，必须合成各自 target 文件名，不能塌缩成同名导致 missing 去重
只剩 1 个。该 bug 曾导致 sample_0002 空之境界 11 部电影只配 1 字幕 + 9 unmatched。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SMOKE_PATH = REPO_ROOT / "tools" / "run_auto_fetch_mapping_smoke.py"
_spec = importlib.util.spec_from_file_location("run_auto_fetch_mapping_smoke", _SMOKE_PATH)
assert _spec is not None and _spec.loader is not None
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def _movie_ctx(movies: list[dict]) -> dict:
    """构造多 movie 合集 ctx。movies = [{"node":"movie:23150","title":"...","year":2007}, ...]."""
    mapped = [{"source_path": f"src/{i}.mkv", "tmdb_legal_node": m["node"]} for i, m in enumerate(movies)]
    movie_meta = {m["node"]: {"title": m["title"], "year": m["year"]} for m in movies}
    return {
        "mapped": mapped,
        "display_title": movies[0]["title"],
        "original_title": movies[0]["title"],
        "year": movies[0]["year"],
        "media_type": "movie",
        "movie_meta": movie_meta,
        "stage2_file_name": "sample.json",
    }


def test_synthesize_targets_multi_movie_each_distinct():
    """多 movie 合集：每个 movie 合成独立 target 文件名，无塌缩。"""
    movies = [
        {"node": "movie:23150", "title": "空之境界 第一章 俯瞰风景", "year": 2007},
        {"node": "movie:23151", "title": "空之境界 第二章 杀人考察（前）", "year": 2007},
        {"node": "movie:23153", "title": "空之境界 第三章 痛觉残留", "year": 2008},
        {"node": "movie:253987", "title": "空之境界 未来福音 extra chorus", "year": 2013},
    ]
    ctx = _movie_ctx(movies)
    _root, targets = smoke.synthesize_targets(ctx)
    assert len(targets) == 4
    target_files = [t["target_file"] for t in targets]
    # 关键：4 个 target 文件名全不同（不塌缩）
    assert len(set(target_files)) == 4, f"targets collapsed: {target_files}"
    # 每个 target 含对应 movie 的 title
    for t, m in zip(targets, movies):
        assert m["title"] in t["target_file"]
        assert str(m["year"]) in t["target_file"]


def test_synthesize_targets_single_movie_unchanged():
    """单 movie：行为不变，target = <root>/<Title> (<year>)/<Title> (<year>).mkv。"""
    movies = [{"node": "movie:123", "title": "Foo Movie", "year": 2020}]
    ctx = _movie_ctx(movies)
    _root, targets = smoke.synthesize_targets(ctx)
    assert len(targets) == 1
    tf = targets[0]["target_file"]
    assert "Foo Movie (2020)" in tf
    assert tf.endswith("Foo Movie (2020).mkv")


def test_synthesize_targets_movie_without_meta_falls_back_to_series_title():
    """movie_meta 缺失某节点时回退 series 级 display_title/year（不崩）。"""
    mapped = [
        {"source_path": "src/a.mkv", "tmdb_legal_node": "movie:1"},
        {"source_path": "src/b.mkv", "tmdb_legal_node": "movie:2"},
    ]
    ctx = {
        "mapped": mapped,
        "display_title": "Series Title",
        "original_title": "",
        "year": 2019,
        "media_type": "movie",
        "movie_meta": {},  # 无 per-movie 元数据
        "stage2_file_name": "sample.json",
    }
    _root, targets = smoke.synthesize_targets(ctx)
    # 回退到 series title，两个 target 同名（旧行为），不崩
    assert len(targets) == 2
    assert all("Series Title (2019)" in t["target_file"] for t in targets)


def test_synthesize_targets_tv_unchanged():
    """TV 分支不受 movie 修复影响：按 S<ss>E<ee> 合成。"""
    mapped = [
        {"source_path": "src/01.mkv", "tmdb_legal_node": "tv:61527:S01E01"},
        {"source_path": "src/02.mkv", "tmdb_legal_node": "tv:61527:S01E02"},
    ]
    ctx = {
        "mapped": mapped,
        "display_title": "Gatchaman Crowds",
        "original_title": "",
        "year": 2013,
        "media_type": "tv",
        "movie_meta": {},
        "stage2_file_name": "sample.json",
    }
    _root, targets = smoke.synthesize_targets(ctx)
    assert len(targets) == 2
    assert any("S01E01" in t["target_file"] for t in targets)
    assert any("S01E02" in t["target_file"] for t in targets)



def test_synthesize_targets_mixed_tv_plus_movie_keeps_movie():
    """mixed tv+movie 任务（如 0091 鬼灭 44 TV + 1 剧场版）：tv 主体走 tv 分支时，
    movie legal node 不能被丢弃，必须合成 movie target，否则 missing_videos 漏掉
    剧场版，Pi 看不到就不会选（曾误判"剧场版无帖"，实为 smoke 漏合成 target）。"""
    mapped = [
        {"source_path": "src/TV01.mkv", "tmdb_legal_node": "tv:85937:S01E01"},
        {"source_path": "src/TV02.mkv", "tmdb_legal_node": "tv:85937:S01E02"},
        {"source_path": "src/Gekijouban.mkv", "tmdb_legal_node": "movie:635302"},
    ]
    movie_meta = {"movie:635302": {"title": "Demon Slayer: Mugen Train", "year": 2020}}
    ctx = {
        "mapped": mapped,
        "display_title": "Demon Slayer Kimetsu no Yaiba",
        "original_title": "",
        "year": 2019,
        "media_type": "tv",  # 主体 TV，含 1 movie
        "movie_meta": movie_meta,
        "stage2_file_name": "sample.json",
    }
    _root, targets = smoke.synthesize_targets(ctx)
    # 3 个 target 全保留（2 TV + 1 movie），movie 不丢
    assert len(targets) == 3
    # TV target 走 series root + Season dir
    tv_targets = [t for t in targets if "S01E0" in t["target_file"]]
    assert len(tv_targets) == 2
    # movie target 走 movie root + 独立 title/year 目录
    movie_targets = [t for t in targets if "movie" not in t["target_file"] and "S0" not in t["target_file"]]
    assert len(movie_targets) == 1
    mt = movie_targets[0]
    assert "Demon Slayer Mugen Train (2020)" in mt["target_file"]  # sanitize 去掉冒号
    assert mt["target_file"].endswith(".mkv")
    # movie target 的 source 仍是剧场版 source
    assert mt["source_path"] == "src/Gekijouban.mkv"


def test_synthesize_targets_multi_tv_series_no_basename_collision():
    """多 TV series 合集（0099 P4 本篇 tv:46388 + P4 Golden tv:61465）：
    两个 series 都用 S01E01 编号，须按各自 series title 生成 target_file，
    否则同名撞 → missing_videos 重复（39=26 真实 + 13 同名撞），且 Pi 用错
    subject 名搜不到 P4 Golden 字幕。tv_meta 按 series ref 给各 series title/year。
    """
    mapped = [
        {"source_path": "src/P4_01.mkv", "tmdb_legal_node": "tv:46388:S01E01"},
        {"source_path": "src/P4_02.mkv", "tmdb_legal_node": "tv:46388:S01E02"},
        {"source_path": "src/P4G_01.mkv", "tmdb_legal_node": "tv:61465:S01E01"},
        {"source_path": "src/P4G_02.mkv", "tmdb_legal_node": "tv:61465:S01E02"},
    ]
    tv_meta = {
        "tv:46388": {"title": "Persona4 the ANIMATION", "year": 2011},
        "tv:61465": {"title": "Persona 4 The Golden Animation", "year": 2014},
    }
    ctx = {
        "mapped": mapped,
        "display_title": "Persona4 the ANIMATION",
        "original_title": "",
        "year": 2011,
        "media_type": "tv",
        "movie_meta": {},
        "tv_meta": tv_meta,
        "stage2_file_name": "sample.json",
    }
    _root, targets = smoke.synthesize_targets(ctx)
    assert len(targets) == 4
    # 4 个 target_file 全唯一（不撞 basename）
    target_files = [t["target_file"] for t in targets]
    assert len(set(target_files)) == 4
    # P4 本篇 target 用 P4 本篇 title
    p4 = [t for t in targets if "Persona4 the ANIMATION" in t["target_file"]]
    assert len(p4) == 2
    # P4 Golden target 用 P4 Golden title（独立目录），不用 P4 本篇 title
    p4g = [t for t in targets if "Golden" in t["target_file"]]
    assert len(p4g) == 2
    # P4 Golden 的 S01E01 target 不该和 P4 本篇 S01E01 同路径
    p4_e01 = [t for t in p4 if "S01E01" in t["target_file"]][0]["target_file"]
    p4g_e01 = [t for t in p4g if "S01E01" in t["target_file"]][0]["target_file"]
    assert p4_e01 != p4g_e01


def test_extract_sample_context_series_title_uses_actually_mapped_candidate():
    """B8：series title/year 必须取 verified_plan 实际映的 candidate，而非
    legal_graph.candidates[0]。0002 大和号2205 曾因 candidates[0]=movie:860104
    (前章) 而实际映 tv:157583，synthesize 用前章 title 合成 series_root，
    "前章目录装 8 集"假错位。修后按 mapped 的 tmdb_legal_node 前缀查候选。
    """
    # stage2 产物：3 候选 [movie:860104, movie:923659, tv:157583]，实际映 tv:157583
    arts = {
        "stage1_data": {
            "snapshot": {
                "compiled_plan": {
                    "assignments": [
                        {
                            "disposition": "map_to_bangumi",
                            "source_path": "src/01.mkv",
                            "target": {"bangumi_subject_id": 319390, "media_kind": "movie"},
                        },
                    ],
                },
            },
        },
        "stage2_data": {
            "bridge_run_result": {
                "verified_plan": {
                    "mappings": [
                        {
                            "disposition": "map_to_tmdb",
                            "source_path": "src/01.mkv",
                            "tmdb_legal_node_ids": ["tv:157583:S01E01"],
                        },
                    ],
                },
                "tmdb_legal_graph": {
                    "candidates": [
                        {
                            "tmdb_ref": "movie:860104",
                            "tmdb_id": 860104,
                            "type": "movie",
                            "display_title": "前章 -TAKE OFF-",
                            "year": 2021,
                        },
                        {
                            "tmdb_ref": "movie:923659",
                            "tmdb_id": 923659,
                            "type": "movie",
                            "display_title": "後章 -STASHA-",
                            "year": 2022,
                        },
                        {
                            "tmdb_ref": "tv:157583",
                            "tmdb_id": 157583,
                            "type": "tv",
                            "display_title": "Star Blazers 2205 New Voyage",
                            "original_title": "宇宙戦艦ヤマト2205",
                            "year": 2024,
                        },
                    ],
                },
            },
        },
        "stage2_file": type("F", (), {"name": "sample.json"})(),
    }
    ctx = smoke.extract_sample_context(arts)
    # 取实际映的 tv:157583 title，不是 candidates[0] movie:860104 "前章"
    assert ctx["display_title"] == "Star Blazers 2205 New Voyage"
    assert ctx["year"] == 2024
    assert "前章" not in ctx["display_title"]
