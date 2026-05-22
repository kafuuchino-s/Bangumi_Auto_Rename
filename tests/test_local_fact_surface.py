from pathlib import Path

from src.rename.local_evidence import LocalEvidence, LocalFileEvidence, build_local_evidence
from src.rename.local_fact_surface import build_local_fact_surface


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _evidence(files: list[LocalFileEvidence]) -> LocalEvidence:
    return LocalEvidence(
        root_name="root",
        root_path="root",
        files=files,
        video_count=sum(1 for item in files if item.is_video),
        main_video_count=sum(1 for item in files if item.is_main_video_candidate),
        supplemental_candidate_count=sum(1 for item in files if item.is_supplemental_candidate),
        directory_structure=[],
    )


def test_path_facts_for_nested_directory_are_raw_only(tmp_path: Path):
    root = tmp_path / "Package"
    _touch(root / "Season 01" / "Show - 01.mkv")

    evidence = build_local_evidence(root)

    assert evidence.fact_surface is not None
    fact = evidence.fact_surface.files[0]
    assert fact.path_facts.directory_segments == ["Season 01"]
    assert fact.path_facts.parent_folder == "Season 01"
    assert fact.path_facts.basename == "Show - 01.mkv"
    assert fact.path_facts.extension == ".mkv"
    assert fact.path_facts.raw_number_tokens[0]["raw_text"] == "01"
    assert "episode" not in fact.path_facts.raw_number_tokens[0]


def test_missing_container_facts_cover_stream_missing_unsupported_and_zero_byte(tmp_path: Path):
    missing_path = tmp_path / "missing.mkv"
    zero_path = _touch(tmp_path / "zero.mkv", b"")
    stream_path = _touch(tmp_path / "remote.strm", b"https://example.test/video.m3u8?token=secret\n")
    text_path = _touch(tmp_path / "note.txt", b"note")
    files = [
        LocalFileEvidence("file_001", "missing.mkv", "missing.mkv", ".mkv", True, False, True),
        LocalFileEvidence("file_002", "zero.mkv", "zero.mkv", ".mkv", True, False, True, size_bytes=0),
        LocalFileEvidence("file_003", "remote.strm", "remote.strm", ".strm", False, False, False),
        LocalFileEvidence("file_004", "note.txt", "note.txt", ".txt", False, False, False),
    ]

    surface = build_local_fact_surface(
        _evidence(files),
        actual_paths={
            "file_001": missing_path,
            "file_002": zero_path,
            "file_003": stream_path,
            "file_004": text_path,
        },
    )

    by_id = {fact.file_id: fact for fact in surface.files}
    assert by_id["file_001"].container_facts.probe_status == "missing_file"
    assert by_id["file_002"].container_facts.probe_error_class == "zero_byte_file"
    assert by_id["file_003"].stream_facts.is_stream_file is True
    assert by_id["file_003"].stream_facts.sanitized_target_summary.endswith("token=<redacted>")
    assert by_id["file_003"].missing_facts[0].reason == "stream_file"
    assert by_id["file_004"].container_facts.probe_status == "unsupported"


def test_probeable_media_uses_mocked_probe(tmp_path: Path, monkeypatch):
    video_path = _touch(tmp_path / "video.mkv", b"not really media")
    files = [LocalFileEvidence("file_001", "video.mkv", "video.mkv", ".mkv", True, False, True, size_bytes=16)]

    def fake_probe(path: Path):
        assert path == video_path
        return {"duration": 2.5, "width": 1920, "height": 1080, "audio_codec": "aac"}, ""

    monkeypatch.setattr("src.rename.local_fact_surface.probe_media_file", fake_probe)

    surface = build_local_fact_surface(_evidence(files), actual_paths={"file_001": video_path}, probe_media=True)

    fact = surface.files[0]
    assert fact.container_facts.probe_status == "available"
    assert fact.container_facts.duration_seconds == 150.0
    assert fact.container_facts.resolution == "1920x1080"
    assert fact.container_facts.video_stream_count == 1
    assert fact.container_facts.audio_stream_count == 1


def test_external_subtitle_facts_include_bounded_snippets(tmp_path: Path):
    root = tmp_path / "Package"
    _touch(root / "Show - 01.mkv", b"video bytes")
    _touch(
        root / "Show - 01.chs.ass",
        "[Script Info]\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,A short subtitle line\n".encode("utf-8"),
    )

    evidence = build_local_evidence(root)

    assert evidence.fact_surface is not None
    video_fact = next(fact for fact in evidence.fact_surface.files if fact.relative_path.endswith(".mkv"))
    assert video_fact.subtitle_facts.external_subtitle_refs
    assert video_fact.subtitle_facts.language_markers == ["chs"]
    snippets = video_fact.subtitle_facts.bounded_text_snippets
    assert snippets
    assert snippets[0]["text"] == "A short subtitle line"
    assert len(snippets[0]["text"]) <= 120


def test_external_subtitle_snippets_decode_gb18030_without_utf16_mojibake(tmp_path: Path):
    root = tmp_path / "Package"
    _touch(root / "Show - 02.mkv", b"video bytes")
    _touch(
        root / "Show - 02.chs.ass",
        (
            "[Script Info]\n"
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,你好，雅儿贝德\n"
        ).encode("gb18030"),
    )

    evidence = build_local_evidence(root)

    assert evidence.fact_surface is not None
    video_fact = next(fact for fact in evidence.fact_surface.files if fact.relative_path.endswith(".mkv"))
    snippets = video_fact.subtitle_facts.bounded_text_snippets
    assert snippets[0]["text"] == "你好，雅儿贝德"
    assert "卛" not in snippets[0]["text"]


def test_external_subtitle_snippets_skip_ass_metadata_and_keep_body(tmp_path: Path):
    root = tmp_path / "Package"
    _touch(root / "Show - 03.mkv", b"video bytes")
    long_style_header = "\n".join(
        f"Style: Default{i},Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000"
        for i in range(180)
    )
    _touch(
        root / "Show - 03.chs.ass",
        (
            "\ufeff[Script Info]\n"
            "; Script generated by Aegisub\n"
            "Title: Metadata title should be ignored\n"
            "[V4+ Styles]\n"
            f"{long_style_header}\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,First body line\n"
            "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Second body line\n"
        ).encode("utf-8-sig"),
    )

    evidence = build_local_evidence(root)

    assert evidence.fact_surface is not None
    video_fact = next(fact for fact in evidence.fact_surface.files if fact.relative_path.endswith(".mkv"))
    snippets = video_fact.subtitle_facts.bounded_text_snippets
    assert [item["text"] for item in snippets] == ["First body line", "Second body line"]


def test_external_subtitle_snippets_skip_common_credit_boilerplate(tmp_path: Path):
    root = tmp_path / "Package"
    _touch(root / "Show - 04.mkv", b"video bytes")
    _touch(
        root / "Show - 04.chs.ass",
        (
            "[Events]\n"
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,本字幕由动漫国字幕组制作(dmguo.org)\\N仅供试看\n"
            "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,翻译:小呆 时轴:kwalice 后期&BDRip:小圣\n"
            "Dialogue: 0,0:00:07.00,0:00:09.00,Default,,0,0,0,,Play1 完蛋与开始\n"
        ).encode("utf-8"),
    )

    evidence = build_local_evidence(root)

    assert evidence.fact_surface is not None
    video_fact = next(fact for fact in evidence.fact_surface.files if fact.relative_path.endswith(".mkv"))
    snippets = video_fact.subtitle_facts.bounded_text_snippets
    assert [item["text"] for item in snippets] == ["Play1 完蛋与开始"]
