from pathlib import Path

from src.subtitle.case_agent.local_subtitle_entry import (
    build_subtitle_file_cards,
)
from src.subtitle.case_agent.models import (
    SubtitleFileCard,
    SubtitleMappingDraft,
    SubtitleMappingRow,
    SubtitleTargetVideoCard,
)
from src.subtitle.case_agent.pi_tools import SubtitleCaseToolState
from src.subtitle.case_agent.verifier import verify_subtitle_mapping_draft
from src.subtitle.case_agent.workspace import build_subtitle_case_workspace
from src.subtitle.extractor import ExtractedSubtitle
from src.subtitle.language import detect_chinese_script, normalize_language


_SIMPLIFIED = "后台发展软件里面这边还没发现问题"
_TRADITIONAL = "後臺發展軟體裡面這邊還沒發現問題"


def _write_ass(path: Path, dialogue: str, *, header: str = "") -> Path:
    path.write_text(
        "[Script Info]\n"
        f"Title: {header}\n"
        "[Events]\n"
        + "".join(
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,"
            f"{dialogue}\n"
            for _ in range(10)
        ),
        encoding="utf-8",
    )
    return path


def test_detect_chinese_script_uses_dialogue_not_filename_or_header(tmp_path):
    path = _write_ass(
        tmp_path / "episode.chs.ass",
        _TRADITIONAL,
        header=_SIMPLIFIED * 20,
    )

    evidence = detect_chinese_script(path)

    assert evidence.script == "traditional"
    assert evidence.traditional_count >= 100
    assert evidence.simplified_count == 0


def test_detect_chinese_script_classifies_simplified_srt(tmp_path):
    path = tmp_path / "episode.cht.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n" + _SIMPLIFIED * 10,
        encoding="utf-8",
    )

    evidence = detect_chinese_script(path)

    assert evidence.script == "simplified"
    assert evidence.simplified_count >= 100


def test_detect_chinese_script_keeps_mixed_and_japanese_unknown(tmp_path):
    mixed = _write_ass(tmp_path / "mixed.ass", _SIMPLIFIED + _TRADITIONAL)
    japanese = _write_ass(
        tmp_path / "japanese.ass",
        "これは日本語の字幕です。後で臺灣へ行きます。" * 10,
    )

    assert detect_chinese_script(mixed).script == "unknown"
    assert detect_chinese_script(japanese).script == "unknown"


def test_build_subtitle_cards_exposes_content_evidence(tmp_path):
    path = _write_ass(tmp_path / "episode.chs.ass", _TRADITIONAL)
    cards = build_subtitle_file_cards(
        [
            ExtractedSubtitle(
                temp_path=path,
                archive_path="episode.chs.ass",
                filename=path.name,
            )
        ]
    )

    assert cards[0].language_hint == "chs"
    assert cards[0].content_chinese_script == "traditional"
    assert cards[0].traditional_evidence_count >= 100


def test_verifier_blocks_language_that_conflicts_with_content():
    subtitle = SubtitleFileCard(
        ref="SF1",
        archive_path="episode.chs.ass",
        filename="episode.chs.ass",
        language_hint="chs",
        content_chinese_script="traditional",
        traditional_evidence_count=100,
    )
    target = SubtitleTargetVideoCard(
        ref="TV1",
        task_uuid="task-1",
        video="Show - S01E01.mkv",
        target_dir="/media/show",
    )

    conflicting = SubtitleMappingDraft(
        rows=[
            SubtitleMappingRow(
                row_ref="R1",
                subtitle_ref="SF1",
                target_ref="TV1",
                language="chs",
            )
        ]
    )
    corrected = conflicting.model_copy(
        update={
            "rows": [
                conflicting.rows[0].model_copy(update={"language": "cht"})
            ]
        }
    )

    conflicts = []
    for language in ("chs", "jpn", "zh"):
        draft = conflicting.model_copy(
            update={
                "rows": [
                    conflicting.rows[0].model_copy(
                        update={"language": language}
                    )
                ]
            }
        )
        conflicts.append(
            verify_subtitle_mapping_draft(
                subtitle_files=[subtitle],
                target_videos=[target],
                draft=draft,
            )
        )
    accepted = verify_subtitle_mapping_draft(
        subtitle_files=[subtitle], target_videos=[target], draft=corrected
    )

    assert all(not result.passed for result in conflicts)
    assert all(
        any(
            issue.issue_code == "content_language_conflict"
            for issue in result.issues
        )
        for result in conflicts
    )
    assert accepted.passed


def test_pi_tool_returns_content_language_repair_hint(tmp_path):
    workspace = build_subtitle_case_workspace(
        archive_name="wrong-label.zip",
        subtitle_files=[
            SubtitleFileCard(
                archive_path="episode.chs.ass",
                filename="episode.chs.ass",
                language_hint="chs",
                content_chinese_script="traditional",
                traditional_evidence_count=100,
            )
        ],
        target_videos=[
            SubtitleTargetVideoCard(
                task_uuid="task-1",
                video="Show - S01E01.mkv",
                target_dir="/media/show",
            )
        ],
    )
    state = SubtitleCaseToolState(
        workspace=workspace,
        run_dir=tmp_path / "run",
        language_resolver=normalize_language,
    )
    context = state.tool_get_subtitle_mapping_context()["data"]
    result = state.tool_validate_subtitle_mapping(
        {
            "rows": [
                {
                    "row_ref": "R1",
                    "subtitle_ref": "SF1",
                    "target_ref": "TV1",
                    "language": "chs",
                }
            ]
        }
    )

    assert context["subtitle_files"][0]["content_chinese_script"] == (
        "traditional"
    )
    assert not result["accepted"]
    assert any("dialogue content" in hint for hint in result["repair_hints"])


def test_normalize_language_preserves_existing_defaults():
    assert normalize_language("chs") == ("zh-CN", True)
    assert normalize_language("cht") == ("zh-TW", False)
    assert normalize_language(None) == ("zh", False)
    assert normalize_language("custom") == ("custom", False)
