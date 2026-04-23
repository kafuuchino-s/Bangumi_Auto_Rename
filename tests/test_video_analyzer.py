from src.ai.video_analyzer import VideoAnalyzer


def test_zero_byte_video_skips_duration_probe_without_warning(monkeypatch, tmp_path):
    file_path = tmp_path / "empty.mkv"
    file_path.touch()

    create_parser_called = False
    warning_calls = []

    def fake_create_parser(*args, **kwargs):
        nonlocal create_parser_called
        create_parser_called = True
        raise AssertionError("duration probe should be skipped for zero-byte files")

    monkeypatch.setattr("src.ai.video_analyzer.createParser", fake_create_parser)
    monkeypatch.setattr(
        "src.ai.video_analyzer.logger.warning",
        lambda msg: warning_calls.append(msg),
    )

    duration = VideoAnalyzer.get_video_duration(file_path)
    result = VideoAnalyzer.analyze_video_files(tmp_path, [file_path])

    assert duration is None
    assert create_parser_called is False
    assert warning_calls == []
    assert result == [
        {
            "filename": "empty.mkv",
            "path": "empty.mkv",
            "size": 0,
            "duration": None,
        }
    ]
