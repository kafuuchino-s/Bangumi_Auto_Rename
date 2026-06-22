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


def _parse_out_dir(command):
    """从 bandizip 命令里解析 -o:<dir> 的目标目录。"""
    for arg in command:
        if isinstance(arg, str) and arg.startswith("-o:"):
            return Path(arg[3:])
    raise AssertionError(f"missing -o: in command {command}")


def test_extract_nested_rar_recurses_into_inner_archive(monkeypatch, tmp_path):
    """套娃包：外层 RAR 解出 0 字幕但含 1 个内层 RAR，内层 RAR 含字幕。
    回归 acgrip TID=346（外层 RAR 含 4 个内层 RAR，每内层一季字幕），
    不递归则 extractor 报 0 字幕，递归后解出内层字幕。
    """
    archive_path = tmp_path / "outer.rar"
    archive_path.write_bytes(b"rar")
    root = tmp_path / "extract_root"
    extractor = SubtitleExtractor(temp_dir=root)

    monkeypatch.setattr("src.subtitle.extractor.RAR_AVAILABLE", False)
    monkeypatch.setattr(
        SubtitleExtractor,
        "_find_bandizip_executable",
        staticmethod(lambda: Path("C:/Program Files/Bandizip/bz.exe")),
    )

    # 记录每次 bandizip 调用，按目标目录区分外层/内层
    invocations: list[Path] = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, capture_output, text, encoding, errors, check):
        out_dir = _parse_out_dir(command)
        invocations.append(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if out_dir == root / archive_path.stem:
            # 外层：写出 1 个内层 RAR（无字幕）
            (out_dir / "inner.rar").write_bytes(b"rar-inner")
        else:
            # 内层：写出字幕
            (out_dir / "ep01.ass").write_text("subtitle", encoding="utf-8")
        return Result()

    monkeypatch.setattr("src.subtitle.extractor.subprocess.run", fake_run)

    subtitles = extractor.extract(archive_path)

    assert subtitles is not None
    assert len(subtitles) == 1
    assert subtitles[0].filename == "ep01.ass"
    # 外层 + 内层各调用一次 bandizip
    assert len(invocations) == 2


def test_extract_nested_respects_depth_limit(monkeypatch, tmp_path):
    """嵌套深度超 _MAX_NEST_DEPTH 应停止递归，防 zip bomb 套娃。"""
    import src.subtitle.extractor as ext_mod

    archive_path = tmp_path / "outer.rar"
    archive_path.write_bytes(b"rar")
    root = tmp_path / "extract_root"
    extractor = SubtitleExtractor(temp_dir=root)

    monkeypatch.setattr("src.subtitle.extractor.RAR_AVAILABLE", False)
    monkeypatch.setattr(
        SubtitleExtractor,
        "_find_bandizip_executable",
        staticmethod(lambda: Path("C:/Program Files/Bandizip/bz.exe")),
    )
    # 压低深度上限到 1，确保递归会被拦
    monkeypatch.setattr(ext_mod, "_MAX_NEST_DEPTH", 1)

    invocations: list[Path] = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, capture_output, text, encoding, errors, check):
        out_dir = _parse_out_dir(command)
        invocations.append(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if out_dir == root / archive_path.stem:
            (out_dir / "inner.rar").write_bytes(b"rar-inner")
        else:
            # 内层故意再放一个更内层 rar（永远套娃），但深度应被拦
            (out_dir / "deeper.rar").write_bytes(b"rar-deeper")
        return Result()

    monkeypatch.setattr("src.subtitle.extractor.subprocess.run", fake_run)

    subtitles = extractor.extract(archive_path)

    # 深度上限 1：外层解了，内层 depth=1 不超限会解一次，
    # 但 deeper.rar 在 depth=2 会被拦，且内层解出 0 字幕 → 最终 0 字幕
    assert subtitles is not None
    assert len(subtitles) == 0


def test_extract_non_nested_archive_unchanged(monkeypatch, tmp_path):
    """非嵌套包（外层直接含字幕）不应触发递归，行为不变。"""
    archive_path = tmp_path / "plain.rar"
    archive_path.write_bytes(b"rar")
    root = tmp_path / "extract_root"
    extractor = SubtitleExtractor(temp_dir=root)

    monkeypatch.setattr("src.subtitle.extractor.RAR_AVAILABLE", False)
    monkeypatch.setattr(
        SubtitleExtractor,
        "_find_bandizip_executable",
        staticmethod(lambda: Path("C:/Program Files/Bandizip/bz.exe")),
    )

    invocations: list[Path] = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, capture_output, text, encoding, errors, check):
        out_dir = _parse_out_dir(command)
        invocations.append(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "a.ass").write_text("subtitle", encoding="utf-8")
        (out_dir / "b.srt").write_text("1\nhello", encoding="utf-8")
        return Result()

    monkeypatch.setattr("src.subtitle.extractor.subprocess.run", fake_run)

    subtitles = extractor.extract(archive_path)

    assert subtitles is not None
    assert len(subtitles) == 2
    # 外层直接解出字幕，不应递归
    assert len(invocations) == 1
