import subprocess
from pathlib import Path
from types import SimpleNamespace

from src.subtitle.syncer import FFsubsyncRunner


def _mock_syncer_config(
    monkeypatch,
    *,
    executable: str = "ffsubsync",
    timeout: int = 120,
    extra_args: str = "",
):
    def _get_config(key):
        if key == "subtitle_sync_executable":
            return executable
        if key == "subtitle_sync_timeout_seconds":
            return timeout
        if key == "subtitle_sync_extra_args":
            return extra_args
        return None

    monkeypatch.setattr("src.subtitle.syncer.cm.get_config", _get_config)


def test_parse_extra_args_empty():
    assert FFsubsyncRunner._parse_extra_args("") == []
    assert FFsubsyncRunner._parse_extra_args("   ") == []


def test_parse_extra_args_simple():
    args = FFsubsyncRunner._parse_extra_args("--vad --max-offset-seconds 5")
    assert args == ["--vad", "--max-offset-seconds", "5"]


def test_parse_extra_args_with_quotes():
    args = FFsubsyncRunner._parse_extra_args('--model "small english" --foo bar')
    assert args == ["--model", "small english", "--foo", "bar"]


def test_parse_extra_args_unclosed_quotes():
    try:
        FFsubsyncRunner._parse_extra_args('--model "small english')
        assert False, "expected ValueError"
    except ValueError as e:
        assert "未闭合" in str(e)


def test_sync_subtitle_success(monkeypatch, tmp_path):
    runner = FFsubsyncRunner()
    video_path = tmp_path / "video.mkv"
    subtitle_path = tmp_path / "source.ass"
    output_dir = tmp_path / "out"

    video_path.write_text("video", encoding="utf-8")
    subtitle_path.write_text("subtitle", encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    _mock_syncer_config(monkeypatch)

    def _fake_run(command, capture_output, text, timeout, check):
        assert check is False
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text("synced", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("src.subtitle.syncer.subprocess.run", _fake_run)

    result = runner.sync_subtitle(
        video_path=video_path,
        subtitle_path=subtitle_path,
        output_dir=output_dir,
    )

    assert result.success is True
    assert result.used_fallback is False
    assert result.reason == ""
    assert result.output_path == output_dir / subtitle_path.name


def test_sync_subtitle_executable_not_found(monkeypatch, tmp_path):
    runner = FFsubsyncRunner()
    video_path = tmp_path / "video.mkv"
    subtitle_path = tmp_path / "source.ass"
    output_dir = tmp_path / "out"

    video_path.write_text("video", encoding="utf-8")
    subtitle_path.write_text("subtitle", encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    _mock_syncer_config(monkeypatch, executable="not-exists-ffsubsync")

    def _fake_run(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("src.subtitle.syncer.subprocess.run", _fake_run)

    result = runner.sync_subtitle(
        video_path=video_path,
        subtitle_path=subtitle_path,
        output_dir=output_dir,
    )

    assert result.success is False
    assert result.used_fallback is True
    assert "未找到可执行文件" in result.reason
    assert result.output_path is None


def test_sync_subtitle_timeout(monkeypatch, tmp_path):
    runner = FFsubsyncRunner()
    video_path = tmp_path / "video.mkv"
    subtitle_path = tmp_path / "source.ass"
    output_dir = tmp_path / "out"

    video_path.write_text("video", encoding="utf-8")
    subtitle_path.write_text("subtitle", encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    _mock_syncer_config(monkeypatch, timeout=3)

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffsubsync", timeout=3)

    monkeypatch.setattr("src.subtitle.syncer.subprocess.run", _fake_run)

    result = runner.sync_subtitle(
        video_path=video_path,
        subtitle_path=subtitle_path,
        output_dir=output_dir,
    )

    assert result.success is False
    assert result.used_fallback is True
    assert "执行超时" in result.reason
    assert result.output_path is None


def test_sync_subtitle_non_zero_returncode(monkeypatch, tmp_path):
    runner = FFsubsyncRunner()
    video_path = tmp_path / "video.mkv"
    subtitle_path = tmp_path / "source.ass"
    output_dir = tmp_path / "out"

    video_path.write_text("video", encoding="utf-8")
    subtitle_path.write_text("subtitle", encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    _mock_syncer_config(monkeypatch)

    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="mock stderr")

    monkeypatch.setattr("src.subtitle.syncer.subprocess.run", _fake_run)

    result = runner.sync_subtitle(
        video_path=video_path,
        subtitle_path=subtitle_path,
        output_dir=output_dir,
    )

    assert result.success is False
    assert result.used_fallback is True
    assert "非零退出码(2)" in result.reason
    assert "mock stderr" in result.reason
    assert result.output_path is None


def test_sync_subtitle_output_missing(monkeypatch, tmp_path):
    runner = FFsubsyncRunner()
    video_path = tmp_path / "video.mkv"
    subtitle_path = tmp_path / "source.ass"
    output_dir = tmp_path / "out"

    video_path.write_text("video", encoding="utf-8")
    subtitle_path.write_text("subtitle", encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    _mock_syncer_config(monkeypatch)

    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.subtitle.syncer.subprocess.run", _fake_run)

    result = runner.sync_subtitle(
        video_path=video_path,
        subtitle_path=subtitle_path,
        output_dir=output_dir,
    )

    assert result.success is False
    assert result.used_fallback is True
    assert "未生成输出文件" in result.reason
    assert result.output_path is None
