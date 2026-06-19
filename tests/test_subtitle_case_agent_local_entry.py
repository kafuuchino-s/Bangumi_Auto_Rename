"""字幕 Case Agent 本地入口单测（Phase 2）。

覆盖 ``run_subtitle_case_agent_mapping`` 的四态：
- accepted（含 unmatched 子项）
- fail_closed（coverage / 解析失败 / duplicate / needs_more_evidence）
- need_confirm（AI 空映射 / 无目标视频）
- invalid（AI 调用异常）

对齐 tests/test_subtitle_case_agent_verifier.py 风格：直接构造事实 + mock AI，
断言 status / compiled_plan 形状。AI mock 返回同 ``SubtitleMappingResult`` 形状
的 SimpleNamespace（mappings / unmatched_files / confidence / reason）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.subtitle.case_agent.local_subtitle_entry import (
    build_subtitle_file_cards,
    run_subtitle_case_agent_mapping,
)


def _ss(**kwargs):
    """单轮后端快捷入口：强制 backend=single_shot，绕过 Pi sidecar（单元测试用）。"""
    return run_subtitle_case_agent_mapping(backend="single_shot", **kwargs)


def _pi(**kwargs):
    """Pi 后端快捷入口：强制 backend=pi（需 runtime_invoker/fake env，集成测试用）。"""
    return run_subtitle_case_agent_mapping(backend="pi", **kwargs)


def test_subtitle_case_agent_primary_enabled_default_is_true():
    """对齐 rename：Case Agent 主路径默认启用。"""
    from src.config.config_manager import CONFIG_DEFAULT

    assert CONFIG_DEFAULT["subtitle_case_agent_primary_enabled"] is True


# ---------------------------------------------------------------------------
# processor.process() 端到端（Case Agent 主路径）
# ---------------------------------------------------------------------------

def _processor_with_case_agent(monkeypatch, tmp_path):
    """构造 SubtitleProcessor，强制走 Case Agent 主路径，TASK_PATH 隔离。"""
    from src.subtitle.processor import SubtitleProcessor

    task_dir = tmp_path / "task_data"
    task_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.subtitle.processor.TASK_PATH", task_dir)

    config = {
        "subtitle_case_agent_primary_enabled": True,
        # 单测走 Phase 2 单轮后端，避免起 Pi sidecar（Phase 3 有独立 pi_runner 单测）
        "subtitle_case_agent_backend": "single_shot",
    }
    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: config.get(key),
    )
    processor = SubtitleProcessor()
    return processor


def test_process_case_agent_accepted_lands_and_records_unmatched_todo(
    monkeypatch, tmp_path
):
    """accepted + unmatched：落盘已匹配部分，unmatched 写进任务 JSON 待人工，整体 success。"""
    from src.subtitle.extractor import ExtractedSubtitle

    processor = _processor_with_case_agent(monkeypatch, tmp_path)

    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    video_name = "Foo - S01E01 - A.mkv"
    video_path = target_dir / video_name
    video_path.write_text("v", encoding="utf-8")
    tasks = [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": [video_name],
            "target_dir": str(target_dir),
            "video_targets": {video_name: str(video_path)},
        }
    ]
    monkeypatch.setattr(
        processor, "_load_processed_tasks", lambda max_tasks=10: tasks
    )

    s1 = tmp_path / "01.ass"
    s1.write_text("a", encoding="utf-8")
    s2 = tmp_path / "02.ass"
    s2.write_text("b", encoding="utf-8")
    subs = [
        ExtractedSubtitle(temp_path=s1, archive_path="S1/01.ass", filename="01.ass"),
        ExtractedSubtitle(temp_path=s2, archive_path="S1/02.ass", filename="02.ass"),
    ]
    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: subs)
    monkeypatch.setattr(
        processor.extractor, "get_archive_structure", lambda sf: {"S1": ["01.ass", "02.ass"]}
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)

    processor.ai_client.analyze_subtitle_mapping = lambda **kwargs: SimpleNamespace(
        mappings=[_mapping("S1/01.ass", "t1", video_name, "chs")],
        unmatched_files=["S1/02.ass"],
        confidence="High",
        reason="ep02 no video",
    )

    trans_call = {}

    class FakeTrans:
        def __init__(self, R, uuid, force_mode=None, force_overwrite=None):
            trans_call["R"] = dict(R)

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "success"
    assert result["matched_count"] == 1
    assert result["total_subtitles"] == 2
    assert result["pipeline_mode"] == "subtitle_case_agent_primary"
    assert result["unmatched"] == [{"ref": "SF2", "archive_path": "S1/02.ass"}]
    assert s1 in trans_call["R"]
    # 任务 JSON 写盘
    import json
    task_files = list((tmp_path / "task_data").glob("*.json"))
    assert len(task_files) == 1
    data = json.loads(task_files[0].read_text(encoding="utf-8"))
    assert data["type"] == "subtitle"
    assert data["status"] == "success"
    assert data["unmatched"] == [{"ref": "SF2", "archive_path": "S1/02.ass"}]


def test_process_case_agent_fail_closed_maps_to_need_confirm(monkeypatch, tmp_path):
    """fail_closed：合同不通过 -> 对外 need_confirm，带 case_agent_status=fail_closed，不落盘。"""
    from src.subtitle.extractor import ExtractedSubtitle

    processor = _processor_with_case_agent(monkeypatch, tmp_path)

    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    video_name = "Foo - S01E01 - A.mkv"
    (target_dir / video_name).write_text("v", encoding="utf-8")
    tasks = [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": [video_name],
            "target_dir": str(target_dir),
            "video_targets": {video_name: str(target_dir / video_name)},
        }
    ]
    monkeypatch.setattr(
        processor, "_load_processed_tasks", lambda max_tasks=10: tasks
    )

    s1 = tmp_path / "01.ass"
    s1.write_text("a", encoding="utf-8")
    s2 = tmp_path / "02.ass"
    s2.write_text("b", encoding="utf-8")
    subs = [
        ExtractedSubtitle(temp_path=s1, archive_path="S1/01.ass", filename="01.ass"),
        ExtractedSubtitle(temp_path=s2, archive_path="S1/02.ass", filename="02.ass"),
    ]
    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: subs)
    monkeypatch.setattr(
        processor.extractor, "get_archive_structure", lambda sf: {"S1": ["01.ass", "02.ass"]}
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)

    # AI 只映射 SF1，漏掉 SF2 且不 unmatched -> coverage error -> fail_closed
    processor.ai_client.analyze_subtitle_mapping = lambda **kwargs: SimpleNamespace(
        mappings=[_mapping("S1/01.ass", "t1", video_name, "chs")],
        unmatched_files=[],
        confidence="Medium",
        reason="",
    )

    trans_called = {"value": False}

    class FakeTrans:
        def __init__(self, *a, **k):
            trans_called["value"] = True

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "need_confirm"
    assert result["case_agent_status"] == "fail_closed"
    assert result["pipeline_mode"] == "subtitle_case_agent_primary"
    assert trans_called["value"] is False  # 不落盘部分匹配


def test_process_case_agent_need_confirm_when_ai_empty(monkeypatch, tmp_path):
    """need_confirm：AI 空映射 -> need_confirm，available_tasks 暴露供人工选择。"""
    from src.subtitle.extractor import ExtractedSubtitle

    processor = _processor_with_case_agent(monkeypatch, tmp_path)

    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": ["Foo - S01E01 - A.mkv"],
            "target_dir": str(target_dir),
            "video_targets": {},
        }
    ]
    monkeypatch.setattr(
        processor, "_load_processed_tasks", lambda max_tasks=10: tasks
    )

    s1 = tmp_path / "01.ass"
    s1.write_text("a", encoding="utf-8")
    subs = [ExtractedSubtitle(temp_path=s1, archive_path="S1/01.ass", filename="01.ass")]
    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: subs)
    monkeypatch.setattr(
        processor.extractor, "get_archive_structure", lambda sf: {"S1": ["01.ass"]}
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)

    processor.ai_client.analyze_subtitle_mapping = lambda **kwargs: SimpleNamespace(
        mappings=[], unmatched_files=[], confidence="Low", reason=""
    )

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "need_confirm"
    assert result["available_tasks"] == [{"uuid": "t1", "title": "Foo", "season": 1}]
    assert "case_agent_status" not in result  # need_confirm 不带 fail_closed 标记


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _subs(tmp_path) -> list[Any]:
    """构造两个 ExtractedSubtitle-shaped 事实（archive_path 精确）。"""
    s1 = tmp_path / "01.ass"
    s1.write_text("a", encoding="utf-8")
    s2 = tmp_path / "02.ass"
    s2.write_text("b", encoding="utf-8")
    return [
        SimpleNamespace(temp_path=s1, archive_path="S1/01.ass", filename="01.ass"),
        SimpleNamespace(temp_path=s2, archive_path="S1/02.ass", filename="02.ass"),
    ]


def _tasks(tmp_path) -> list[dict[str, object]]:
    target_dir = tmp_path / "lib" / "Foo (2020)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    v1 = "Foo - S01E01 - A.mkv"
    v2 = "Foo - S01E02 - B.mkv"
    (target_dir / v1).write_text("v", encoding="utf-8")
    (target_dir / v2).write_text("v", encoding="utf-8")
    return [
        {
            "uuid": "t1",
            "title": "Foo",
            "season": 1,
            "is_movie": False,
            "videos": [v1, v2],
            "target_dir": str(target_dir),
            "video_targets": {
                v1: str(target_dir / v1),
                v2: str(target_dir / v2),
            },
        }
    ]


def _lang_resolver(lang: str) -> tuple[str, bool]:
    table = {
        "chs": ("zh-CN", True),
        "cht": ("zh-TW", False),
        "jpn": ("ja", False),
    }
    return table.get((lang or "").lower().strip(), ("zh-CN", True))


class _FakeAIClient:
    """模拟 AIClient：按构造时给定的返回值/异常响应 analyze_subtitle_mapping。"""

    def __init__(self, *, result: Any = None, raise_exc: Exception | None = None):
        self._result = result
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    def analyze_subtitle_mapping(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return self._result


def _mapping(subtitle_path: str, task_uuid: str, video: str, language: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        subtitle_path=subtitle_path,
        task_uuid=task_uuid,
        video=video,
        language=language,
    )


# ---------------------------------------------------------------------------
# build_subtitle_file_cards
# ---------------------------------------------------------------------------

def test_build_subtitle_file_cards_extracts_language_hint(tmp_path):
    subs = [
        SimpleNamespace(temp_path=tmp_path / "a.ass", archive_path="a.ass", filename="01.chs.ass"),
        SimpleNamespace(temp_path=tmp_path / "b.ass", archive_path="b.ass", filename="02.jpn.ass"),
    ]
    cards = build_subtitle_file_cards(subs)
    assert cards[0].archive_path == "a.ass"
    assert cards[0].language_hint == "chs"
    assert cards[1].language_hint == "jpn"


# ---------------------------------------------------------------------------
# accepted
# ---------------------------------------------------------------------------

def test_entry_accepted_full_mapping(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    v2 = tasks[0]["videos"][1]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                _mapping("S1/01.ass", "t1", v1, "chs"),
                _mapping("S1/02.ass", "t1", v2, "cht"),
            ],
            unmatched_files=[],
            confidence="High",
            reason="ok",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
        archive_name="foo.zip",
    )
    assert res["status"] == "accepted"
    assert res["ok"] is True
    plan = res["compiled_plan"]
    assert plan is not None
    assert len(plan.mappings) == 2
    assert plan.mappings[0].emby_lang == "zh-CN"
    assert plan.mappings[0].is_simplified is True
    assert plan.mappings[1].emby_lang == "zh-TW"
    assert plan.mappings[1].is_simplified is False
    assert plan.unmatched_refs == []


def test_entry_accepted_with_unmatched(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[_mapping("S1/01.ass", "t1", v1, "chs")],
            unmatched_files=["S1/02.ass"],
            confidence="Medium",
            reason="ep02 no matching video",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "accepted"
    plan = res["compiled_plan"]
    assert plan is not None
    assert len(plan.mappings) == 1
    assert plan.unmatched_refs == ["SF2"]


# ---------------------------------------------------------------------------
# fail_closed
# ---------------------------------------------------------------------------

def test_entry_fail_closed_coverage_missing(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[_mapping("S1/01.ass", "t1", v1, "chs")],  # SF2 漏掉
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    assert res["compiled_plan"] is None
    assert res["ok"] is True  # 合格业务结果
    issues = res["snapshot"]["verifier"]["issues"]
    assert any(i["issue_code"] == "coverage_error" for i in issues)


def test_entry_fail_closed_unresolvable_subtitle_path(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                # subtitle_path 在固定层找不到 -> needs_more_evidence
                _mapping("NOT_EXIST/01.ass", "t1", v1, "chs"),
                _mapping("S1/02.ass", "t1", tasks[0]["videos"][1], "chs"),
            ],
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    issues = res["snapshot"]["verifier"]["issues"]
    # 解析失败落 needs_more_evidence -> not_ready
    assert any(i["issue_code"] == "not_ready" for i in issues)


def test_entry_fail_closed_unresolvable_target(tmp_path):
    tasks = _tasks(tmp_path)
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                # video 不在任务视频清单 -> target 解析失败 -> needs_more_evidence
                _mapping("S1/01.ass", "t1", "WRONG.mkv", "chs"),
                _mapping("S1/02.ass", "t1", tasks[0]["videos"][1], "chs"),
            ],
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    issues = res["snapshot"]["verifier"]["issues"]
    assert any(i["issue_code"] == "not_ready" for i in issues)


def test_entry_fail_closed_duplicate_target_language(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                _mapping("S1/01.ass", "t1", v1, "chs"),
                _mapping("S1/02.ass", "t1", v1, "chs"),  # 同 target 同语言冲突
            ],
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    issues = res["snapshot"]["verifier"]["issues"]
    assert any(i["issue_code"] == "duplicate_target_language" for i in issues)


def test_entry_fail_closed_missing_language(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                _mapping("S1/01.ass", "t1", v1, None),  # 语言缺 -> missing_language
                _mapping("S1/02.ass", "t1", tasks[0]["videos"][1], "chs"),
            ],
            unmatched_files=[],
            confidence="Medium",
            reason="",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "fail_closed"
    issues = res["snapshot"]["verifier"]["issues"]
    assert any(i["issue_code"] == "missing_language" for i in issues)


# ---------------------------------------------------------------------------
# need_confirm
# ---------------------------------------------------------------------------

def test_entry_need_confirm_ai_empty_mappings(tmp_path):
    tasks = _tasks(tmp_path)
    ai = _FakeAIClient(
        result=SimpleNamespace(mappings=[], unmatched_files=[], confidence="Low", reason="")
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "need_confirm"
    assert res["compiled_plan"] is None


def test_entry_need_confirm_no_target_videos(tmp_path):
    ai = _FakeAIClient(result=None)
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=[],  # 无已处理任务 -> 无目标视频
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "need_confirm"
    assert ai.calls == []  # 无目标视频时不应调 AI


def test_entry_need_confirm_ai_returns_none(tmp_path):
    tasks = _tasks(tmp_path)
    ai = _FakeAIClient(result=None)
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "need_confirm"


# ---------------------------------------------------------------------------
# invalid
# ---------------------------------------------------------------------------

def test_entry_invalid_ai_call_raises(tmp_path):
    tasks = _tasks(tmp_path)
    ai = _FakeAIClient(raise_exc=RuntimeError("ai boom"))
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "invalid"
    assert res["ok"] is False
    assert "ai_call_error" in res["summary"]


# ---------------------------------------------------------------------------
# 同 target 不同语言：accepted（双语合法）
# ---------------------------------------------------------------------------

def test_entry_accepted_same_target_different_language(tmp_path):
    tasks = _tasks(tmp_path)
    v1 = tasks[0]["videos"][0]
    ai = _FakeAIClient(
        result=SimpleNamespace(
            mappings=[
                _mapping("S1/01.ass", "t1", v1, "chs"),
                _mapping("S1/02.ass", "t1", v1, "cht"),  # 同视频挂简繁双语
            ],
            unmatched_files=[],
            confidence="High",
            reason="bilingual",
        )
    )
    res = _ss(
        subtitle_files=_subs(tmp_path),
        processed_tasks=tasks,
        ai_client=ai,
        source_path=tmp_path / "foo.zip",
        language_resolver=_lang_resolver,
    )
    assert res["status"] == "accepted"
    plan = res["compiled_plan"]
    assert plan is not None
    assert len(plan.mappings) == 2
    langs = {m.emby_lang for m in plan.mappings}
    assert langs == {"zh-CN", "zh-TW"}
