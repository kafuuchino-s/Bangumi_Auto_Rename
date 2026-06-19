import json
from pathlib import Path
from types import SimpleNamespace

from src.subtitle.extractor import ExtractedSubtitle
from src.subtitle.processor import SubtitleProcessor
from src.subtitle.syncer import SyncResult


def _build_process_fixture(
    monkeypatch,
    tmp_path,
    *,
    sync_enabled=True,
    sync_mode="best_effort",
    overwrite_policy="follow_global",
    sync_result=None,
    create_video=True,
):
    task_dir = tmp_path / "task_data"
    task_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.subtitle.processor.TASK_PATH", task_dir)

    config = {
        "subtitle_sync_enabled": sync_enabled,
        "subtitle_sync_mode": sync_mode,
        "subtitle_sync_overwrite_policy": overwrite_policy,
    }
    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: config.get(key),
    )

    processor = SubtitleProcessor()

    extract_dir = tmp_path / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    source_path = extract_dir / "a.ass"
    source_path.write_text("subtitle", encoding="utf-8")

    subtitle = ExtractedSubtitle(
        temp_path=source_path,
        archive_path="subs/a.ass",
        filename="a.ass",
    )

    target_dir = tmp_path / "target" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    video_name = "Show - S01E01 - Pilot.mkv"
    video_path = target_dir / video_name
    if create_video:
        video_path.write_text("video", encoding="utf-8")

    task = {
        "uuid": "task-1",
        "title": "Show",
        "season": 1,
        "target_dir": str(target_dir),
        "videos": [video_name],
        "video_targets": {video_name: str(video_path)},
        "is_movie": False,
    }

    monkeypatch.setattr(
        processor,
        "_load_processed_tasks",
        lambda max_tasks=10: [task],
    )
    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: [subtitle])
    monkeypatch.setattr(
        processor.extractor,
        "get_archive_structure",
        lambda subtitle_files: {"subs": ["a.ass"]},
    )
    monkeypatch.setattr(processor.extractor, "cleanup", lambda archive_path: None)
    monkeypatch.setattr(
        processor.extractor,
        "get_extract_dir",
        lambda archive_path: extract_dir,
    )

    ai_result = SimpleNamespace(
        mappings=[
            SimpleNamespace(
                subtitle_path="subs/a.ass",
                task_uuid="task-1",
                video=video_name,
                language="chs",
            )
        ],
        confidence="High",
    )
    monkeypatch.setattr(
        processor.ai_client,
        "analyze_subtitle_mapping",
        lambda **kwargs: ai_result,
    )

    if sync_result is None:
        synced_path = extract_dir / ".ffsubsync" / "a.ass"
        synced_path.parent.mkdir(parents=True, exist_ok=True)
        synced_path.write_text("synced", encoding="utf-8")
        sync_result = SyncResult(
            success=True,
            used_fallback=False,
            reason="",
            output_path=synced_path,
            duration=0.01,
        )

    monkeypatch.setattr(
        processor.syncer,
        "sync_subtitle",
        lambda **kwargs: sync_result,
    )

    return processor, {
        "task_dir": task_dir,
        "source_path": source_path,
        "target_dir": target_dir,
        "video_name": video_name,
    }


def test_entry_resolves_exact_subtitle_path_only(tmp_path):
    """AI-first 契约：subtitle_path 必须精确匹配固定层 archive_path，不做 suffix 模糊回退。

    取代旧的 _find_subtitle_file suffix 模糊匹配测试（已随 AI-first 改造移除）。
    固定层事实 archive_path 带多层目录前缀；AI 返回缺前缀的路径无法解析，该行落
    needs_more_evidence，由合同拦成 fail_closed。
    """
    from src.subtitle.case_agent.local_subtitle_entry import (
        build_subtitle_file_cards,
        build_subtitle_case_workspace,
        build_target_video_cards,
    )

    archive_path = (
        "[简] 夜樱四重奏 花之歌+星之海/"
        "[Quetzal] Yozakura Quartet - Hana no Uta/"
        "[Quetzal] Yozakura Quartet - Hana no Uta 01.chs.ass"
    )
    subtitle = ExtractedSubtitle(
        temp_path=tmp_path / "a.ass",
        archive_path=archive_path,
        filename="[Quetzal] Yozakura Quartet - Hana no Uta 01.chs.ass",
    )
    subtitle.temp_path.write_text("subtitle", encoding="utf-8")
    cards = build_subtitle_file_cards([subtitle])
    assert cards[0].archive_path == archive_path

    tasks = [
        {
            "uuid": "t1",
            "title": "Yozakura Quartet",
            "season": 1,
            "is_movie": False,
            "videos": ["Yozakura - S01E01.mkv"],
            "target_dir": str(tmp_path / "lib"),
            "video_targets": {},
        }
    ]
    workspace = build_subtitle_case_workspace(
        archive_name="foo.zip",
        subtitle_files=cards,
        target_videos=build_target_video_cards(tasks),
    )
    # 固定层只有 SF1，精确 archive_path 含多层前缀
    assert workspace.subtitle_refs == ["SF1"]

    # AI 给出精确路径 -> draft 行 map_to_video 解析成功
    # AI 给出缺前缀路径 -> 解析失败 -> needs_more_evidence -> fail_closed
    from src.subtitle.case_agent.local_subtitle_entry import (
        run_subtitle_case_agent_mapping,
    )
    from types import SimpleNamespace

    class _FakeAI:
        def analyze_subtitle_mapping(self, **kwargs):
            return SimpleNamespace(
                mappings=[
                    SimpleNamespace(
                        subtitle_path="[Quetzal] Yozakura Quartet - Hana no Uta/"
                        "[Quetzal] Yozakura Quartet - Hana no Uta 01.chs.ass",
                        task_uuid="t1",
                        video="Yozakura - S01E01.mkv",
                        language="chs",
                    )
                ],
                unmatched_files=[],
                confidence="High",
                reason="",
            )

    res = run_subtitle_case_agent_mapping(
        subtitle_files=[subtitle],
        processed_tasks=tasks,
        ai_client=_FakeAI(),
        source_path=tmp_path / "foo.zip",
        language_resolver=lambda lang: ("zh-CN", True),
        backend="single_shot",
    )
    # 缺最顶层前缀 -> 精确匹配失败 -> fail_closed（不自动 suffix 回退）
    assert res["status"] == "fail_closed"


def test_process_accepts_ai_exact_subtitle_path_with_nested_root(monkeypatch, tmp_path):
    """AI-first 契约：AI 返回的 subtitle_path 必须精确匹配固定层 archive_path。

    取代旧的"缺顶层前缀也能匹配"测试（suffix 模糊匹配已移除）。AI 现在需原样
    返回含目录前缀的精确路径，processor 经 Case Agent/兼容路径精确解析后落盘。
    """
    processor, fixture = _build_process_fixture(
        monkeypatch,
        tmp_path,
        sync_enabled=False,
    )

    nested_dir = tmp_path / "extract" / "[简] 合集" / "subs"
    nested_dir.mkdir(parents=True, exist_ok=True)
    source_path = nested_dir / "a.ass"
    source_path.write_text("subtitle", encoding="utf-8")
    subtitle = ExtractedSubtitle(
        temp_path=source_path,
        archive_path="[简] 合集/subs/a.ass",
        filename="a.ass",
    )

    monkeypatch.setattr(processor.extractor, "extract", lambda archive_path: [subtitle])
    monkeypatch.setattr(
        processor.extractor,
        "get_archive_structure",
        lambda subtitle_files: {"[简] 合集/subs": ["a.ass"]},
    )

    ai_result = SimpleNamespace(
        mappings=[
            SimpleNamespace(
                subtitle_path="[简] 合集/subs/a.ass",  # 精确匹配固定层 archive_path
                task_uuid="task-1",
                video=fixture["video_name"],
                language="chs",
            )
        ],
        confidence="High",
    )
    monkeypatch.setattr(
        processor.ai_client,
        "analyze_subtitle_mapping",
        lambda **kwargs: ai_result,
    )

    trans_call = {}

    class FakeTrans:
        def __init__(self, R, uuid, force_mode=None, force_overwrite=None):
            trans_call["R"] = dict(R)
            trans_call["force_mode"] = force_mode

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "success"
    assert source_path in trans_call["R"]
    assert trans_call["force_mode"] == "复制"


def test_sync_overwrite_policy_follow_global(monkeypatch):
    processor = SubtitleProcessor()

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: "follow_global" if key == "subtitle_sync_overwrite_policy" else None,
    )

    assert processor._resolve_sync_overwrite_policy() is None


def test_sync_overwrite_policy_overwrite(monkeypatch):
    processor = SubtitleProcessor()

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: "overwrite" if key == "subtitle_sync_overwrite_policy" else None,
    )

    assert processor._resolve_sync_overwrite_policy() is True


def test_sync_overwrite_policy_skip(monkeypatch):
    processor = SubtitleProcessor()

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: "skip" if key == "subtitle_sync_overwrite_policy" else None,
    )

    assert processor._resolve_sync_overwrite_policy() is False


def test_apply_subtitle_sync_best_effort_fallback(monkeypatch, tmp_path):
    processor = SubtitleProcessor()

    source_path = tmp_path / "src.ass"
    source_path.write_text("dummy", encoding="utf-8")
    target_path = tmp_path / "dst.ass"
    video_path = tmp_path / "missing_video.mkv"  # 不存在 -> skipped

    detail = {"subtitle": "A/src.ass", "sync_status": "disabled"}
    sync_items = [
        {
            "source_path": source_path,
            "target_path": target_path,
            "video_path": video_path,
            "detail": detail,
        }
    ]

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: ("best_effort" if key == "subtitle_sync_mode" else None),
    )

    summary, final_mapping = processor._apply_subtitle_sync(
        archive_path=tmp_path / "archive.zip",
        sync_items=sync_items,
        original_mapping={source_path: target_path},
    )

    assert summary["enabled"] is True
    assert summary["mode"] == "best_effort"
    assert summary["attempted"] == 1
    assert summary["skipped"] == 1
    assert summary["strict_failed"] is False
    assert detail["sync_status"] == "skipped"
    assert final_mapping == {source_path: target_path}


def test_apply_subtitle_sync_strict_failed(monkeypatch, tmp_path):
    processor = SubtitleProcessor()

    source_path = tmp_path / "src.ass"
    source_path.write_text("dummy", encoding="utf-8")
    target_path = tmp_path / "dst.ass"
    video_path = tmp_path / "missing_video.mkv"  # 不存在 -> strict failed

    detail = {"subtitle": "A/src.ass", "sync_status": "disabled"}
    sync_items = [
        {
            "source_path": source_path,
            "target_path": target_path,
            "video_path": video_path,
            "detail": detail,
        }
    ]

    monkeypatch.setattr(
        "src.subtitle.processor.cm.get_config",
        lambda key: ("strict" if key == "subtitle_sync_mode" else None),
    )

    summary, final_mapping = processor._apply_subtitle_sync(
        archive_path=tmp_path / "archive.zip",
        sync_items=sync_items,
        original_mapping={source_path: target_path},
    )

    assert summary["enabled"] is True
    assert summary["mode"] == "strict"
    assert summary["attempted"] == 1
    assert summary["failed"] == 1
    assert summary["strict_failed"] is True
    assert summary["strict_error"]
    assert detail["sync_status"] == "skipped"
    assert final_mapping == {source_path: target_path}


def test_process_sync_success_uses_synced_mapping(monkeypatch, tmp_path):
    processor, fixture = _build_process_fixture(
        monkeypatch,
        tmp_path,
        sync_enabled=True,
        sync_mode="best_effort",
        overwrite_policy="overwrite",
    )

    trans_call = {}

    class FakeTrans:
        def __init__(self, R, uuid, force_mode=None, force_overwrite=None):
            trans_call["R"] = dict(R)
            trans_call["uuid"] = uuid
            trans_call["force_mode"] = force_mode
            trans_call["force_overwrite"] = force_overwrite

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    expected_target = (
        fixture["target_dir"] / "Show - S01E01 - Pilot.zh-CN.default.ass"
    )

    assert result["status"] == "success"
    assert result["sync_summary"]["enabled"] is True
    assert result["sync_summary"]["success"] == 1
    assert result["sync_summary"]["fallback"] == 0
    assert result["mappings"][0]["sync_status"] == "synced"

    assert trans_call["force_mode"] == "复制"
    assert trans_call["force_overwrite"] is True
    assert fixture["source_path"] not in trans_call["R"]
    assert expected_target == trans_call["R"].get(
        next(iter(trans_call["R"].keys()))
    )


def test_process_sync_best_effort_fallback_still_success(monkeypatch, tmp_path):
    sync_result = SyncResult(
        success=False,
        used_fallback=True,
        reason="mock fail",
        output_path=None,
        duration=0.02,
    )
    processor, fixture = _build_process_fixture(
        monkeypatch,
        tmp_path,
        sync_enabled=True,
        sync_mode="best_effort",
        overwrite_policy="follow_global",
        sync_result=sync_result,
    )

    trans_call = {}

    class FakeTrans:
        def __init__(self, R, uuid, force_mode=None, force_overwrite=None):
            trans_call["R"] = dict(R)
            trans_call["force_overwrite"] = force_overwrite

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "success"
    assert result["sync_summary"]["attempted"] == 1
    assert result["sync_summary"]["success"] == 0
    assert result["sync_summary"]["fallback"] == 1
    assert result["sync_summary"]["strict_failed"] is False
    assert result["mappings"][0]["sync_status"] == "fallback"

    assert fixture["source_path"] in trans_call["R"]
    assert trans_call["force_overwrite"] is None


def test_process_sync_strict_failure_writes_error_task(monkeypatch, tmp_path):
    sync_result = SyncResult(
        success=False,
        used_fallback=True,
        reason="mock fail",
        output_path=None,
        duration=0.02,
    )
    processor, fixture = _build_process_fixture(
        monkeypatch,
        tmp_path,
        sync_enabled=True,
        sync_mode="strict",
        overwrite_policy="follow_global",
        sync_result=sync_result,
    )

    trans_called = {"value": False}

    class FakeTrans:
        def __init__(self, *args, **kwargs):
            trans_called["value"] = True

        def trans_file(self):
            return {"ok": True}

    monkeypatch.setattr("src.subtitle.processor.Trans", FakeTrans)

    result = processor.process(tmp_path / "archive.zip")

    assert result["status"] == "error"
    assert "字幕对齐失败" in result["error"]
    assert result["sync_summary"]["strict_failed"] is True
    assert result["mappings"][0]["sync_status"] == "fallback"
    assert trans_called["value"] is False

    task_files = list(fixture["task_dir"].glob("*.json"))
    assert len(task_files) == 1

    task_data = json.loads(task_files[0].read_text(encoding="utf-8"))
    assert task_data["status"] == "error"
    assert task_data["sync_summary"]["strict_failed"] is True
    assert task_data["mappings"][0]["sync_status"] == "fallback"
