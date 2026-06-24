"""build_task_detail 字段回退口径单测。

新 local_bangumi_to_tmdb_product 链路：record 是扁平 {源路径: 目标路径} dict，
task 顶层无 case_agent_status，成功信号分散在 case_agent_result.status /
bgm_to_tmdb_bridge_status / transferred_file_count / target_root。
旧 build_task_detail 按旧口径取 record.mappings/target_dir → 详情页全显示空/失败。
这里覆盖回退链：扁平 record、旧 mappings 结构、各 key 缺失场景。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.api import serializers


def _patch_io(monkeypatch, task: dict | None, record: dict | None, sf_child: dict | None = None):
    monkeypatch.setattr(serializers, "get_task", lambda _uuid: task or {})
    monkeypatch.setattr(serializers, "get_record", lambda _uuid: record)
    monkeypatch.setattr(serializers, "_load_subtitle_fetch_child", lambda _uuid: sf_child or {})


def test_detail_ai_section_has_no_confidence(monkeypatch):
    """主链路 ai_confidence 已移除（Pi 时代 dead，永远是 -）。"""
    task = {"name": "x", "pipeline_mode": "local_bangumi_to_tmdb_product", "ai_used": True}
    _patch_io(monkeypatch, task, {})
    d = serializers.build_task_detail("uuid-ai")
    assert "ai_confidence" not in d["ai"]
    assert d["ai"]["ai_used"] is True
    assert d["ai"]["pipeline_mode_label"] == "Local→Bangumi→TMDB 产品链路"


def test_detail_subtitle_fetch_section_from_child(monkeypatch):
    """触发过 auto_fetch：字幕区块从子任务文件读配对统计。"""
    task = {
        "name": "人类衰退之后",
        "pipeline_mode": "local_bangumi_to_tmdb_product",
        "subtitle_fetch_attempted": True,
        "subtitle_fetch_status": "success",
        "subtitle_fetch_case_agent_status": "accepted",
        "subtitle_fetch_provider": "acgrip",
        "subtitle_fetch_failure_reason": None,
    }
    sf_child = {
        "missing_video_count": 18,
        "matched_count": 18,
        "selections_count": 1,
        "unmatched": [],
        "no_target_videos": [{"x": 1}],
    }
    _patch_io(monkeypatch, task, {}, sf_child)
    d = serializers.build_task_detail("uuid-sf")
    sf = d["subtitle_fetch"]
    assert sf is not None
    assert sf["status_label"] == "成功"
    assert sf["case_agent_status_label"] == "已接受（通过合同校验）"
    assert sf["provider"] == "acgrip"
    assert sf["matched_count"] == 18
    assert sf["missing_video_count"] == 18
    assert sf["unmatched_count"] == 0
    assert sf["no_target_count"] == 1
    assert sf["selections_count"] == 1


def test_detail_subtitle_fetch_section_absent_when_not_attempted(monkeypatch):
    """没触发过 auto_fetch 且无子任务文件 → subtitle_fetch 为 None（前端不渲染）。"""
    task = {"name": "x", "pipeline_mode": "local_bangumi_to_tmdb_product"}
    _patch_io(monkeypatch, task, {}, None)
    d = serializers.build_task_detail("uuid-no-sf")
    assert d["subtitle_fetch"] is None


def test_detail_new_pipeline_flat_record_fills_fields(monkeypatch):
    """新链路：扁平 record + case_agent_result.status + transferred_file_count + target_root 全部回退命中。"""
    task = {
        "path": "H:\\Anime\\xxx",
        "name": "人类衰退之后",
        "season_id": 1,
        "is_anime": None,
        "is_movie": None,
        "pipeline_mode": "local_bangumi_to_tmdb_product",
        "ai_used": True,
        "ai_attempted": True,
        "failure_reason": None,
        "target_root": "H:\\Media\\Anime Series\\人类衰退之后 (2012)",
        "transferred_file_count": 18,
        "bgm_to_tmdb_bridge_status": "accepted",
        "case_agent_result": {"status": "accepted", "snapshot": {"product_result_kind": "accepted"}},
    }
    record = {
        "H:\\Anime\\xxx\\01.mkv": "H:\\Media\\...\\S01E01.mkv",
        "H:\\Anime\\xxx\\02.mkv": "H:\\Media\\...\\S01E02.mkv",
    }
    _patch_io(monkeypatch, task, record)
    d = serializers.build_task_detail("uuid-1")
    assert d["found"] is True
    assert d["case_agent"]["status"] == "accepted"
    assert d["case_agent"]["status_label"] == "已接受（通过合同校验）"
    assert d["case_agent"]["product_result_kind"] == "accepted"
    assert d["landing"]["target_dir"] == "H:\\Media\\Anime Series\\人类衰退之后 (2012)"
    assert d["landing"]["mapping_count"] == 2  # 扁平 record 条目数
    assert d["failure"]["reason"] == ""


def test_detail_old_pipeline_mappings_record_still_works(monkeypatch):
    """旧链路：record 是 {"mappings":[...], "target_dir":...} 结构，仍走 len(mappings)。"""
    task = {
        "name": "旧链路任务",
        "pipeline_mode": "local_bangumi_case_agent_primary",
        "case_agent_status": "accepted",
        "ai_used": True,
        "ai_attempted": False,
    }
    record = {
        "mappings": [{"source": "a", "target": "b"}, {"source": "c", "target": "d"}, {"source": "e", "target": "f"}],
        "target_dir": "H:\\Media\\Old (2000)",
        "product_result_kind": "accepted",
    }
    _patch_io(monkeypatch, task, record)
    d = serializers.build_task_detail("uuid-2")
    assert d["case_agent"]["status"] == "accepted"
    assert d["case_agent"]["product_result_kind"] == "accepted"
    assert d["landing"]["target_dir"] == "H:\\Media\\Old (2000)"
    assert d["landing"]["mapping_count"] == 3
    assert isinstance(d["landing"]["mappings"], list) and len(d["landing"]["mappings"]) == 3


def test_detail_status_falls_back_to_bridge_status(monkeypatch):
    """case_agent_result 缺失时，状态回退到 bgm_to_tmdb_bridge_status。"""
    task = {
        "name": "x",
        "pipeline_mode": "local_bangumi_to_tmdb_product",
        "bgm_to_tmdb_bridge_status": "fail_closed",
        "target_root": "H:\\Media\\X",
        "transferred_file_count": 0,
    }
    _patch_io(monkeypatch, task, {})
    d = serializers.build_task_detail("uuid-3")
    assert d["case_agent"]["status"] == "fail_closed"
    assert d["case_agent"]["status_label"] == "合同不通过（合格失败）"


def test_detail_empty_task_returns_not_found(monkeypatch):
    """task 不存在（get_task 返回 {}）→ found=False。"""
    _patch_io(monkeypatch, None, None)
    d = serializers.build_task_detail("missing")
    assert d["found"] is False
    assert d["uuid"] == "missing"


def test_detail_no_target_root_shows_empty(monkeypatch):
    """既无 record.target_dir 也无 task.target_root → target_dir 空（前端显示 -）。"""
    task = {"name": "x", "pipeline_mode": "local_bangumi_to_tmdb_product", "case_agent_result": {"status": "accepted"}}
    _patch_io(monkeypatch, task, {"src1": "tgt1"})
    d = serializers.build_task_detail("uuid-4")
    assert d["landing"]["target_dir"] == ""
    assert d["landing"]["mapping_count"] == 1
