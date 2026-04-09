from pathlib import Path

from src.subtitle.extractor import SubtitleExtractor


def test_extract_rar_falls_back_to_bandizip(monkeypatch, tmp_path):
    archive_path = tmp_path / "sample.rar"
    archive_path.write_bytes(b"rar")

    extract_dir = tmp_path / "extract_root" / archive_path.stem
    extractor = SubtitleExtractor(temp_dir=tmp_path / "extract_root")

    monkeypatch.setattr("src.subtitle.extractor.RAR_AVAILABLE", False)
    monkeypatch.setattr(
        SubtitleExtractor,
        "_find_bandizip_executable",
        staticmethod(lambda: Path("C:/Program Files/Bandizip/bz.exe")),
    )

    calls = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, capture_output, text, encoding, errors, check):
        calls["command"] = command
        extract_dir.mkdir(parents=True, exist_ok=True)
        nested = extract_dir / "subs"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "a.ass").write_text("subtitle", encoding="utf-8")
        (extract_dir / "note.txt").write_text("ignore", encoding="utf-8")
        return Result()

    monkeypatch.setattr("src.subtitle.extractor.subprocess.run", fake_run)

    subtitles = extractor.extract(archive_path)

    assert subtitles is not None
    assert len(subtitles) == 1
    assert subtitles[0].archive_path == "subs/a.ass"
    assert subtitles[0].filename == "a.ass"
    assert subtitles[0].temp_path == extract_dir / "subs" / "a.ass"
    assert Path(calls["command"][0]).as_posix() == "C:/Program Files/Bandizip/bz.exe"
    assert calls["command"][1:5] == ["x", "-y", "-aoa", f"-o:{extract_dir}"]
    assert calls["command"][5] == str(archive_path)
