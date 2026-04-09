from pathlib import Path

from src.subtitle.batch_sync import SubtitleBatchSyncer
from src.subtitle.syncer import SyncResult
from tools.subtitle_batch_sync.sync_existing_subtitles import (
    filter_result_to_simplified_chinese,
    is_likely_simplified_chinese_subtitle,
)


class _FakeRunner:
    def __init__(self, result_factory):
        self.result_factory = result_factory
        self.calls = []

    def sync_subtitle(self, video_path, subtitle_path, output_dir):
        self.calls.append(
            {
                "video_path": video_path,
                "subtitle_path": subtitle_path,
                "output_dir": output_dir,
            }
        )
        return self.result_factory(video_path, subtitle_path, output_dir)


def test_sync_tree_dry_run_collects_text_subtitles(tmp_path):
    root = tmp_path / "Anime Series"
    season = root / "Show" / "Season 01"
    season.mkdir(parents=True, exist_ok=True)

    video = season / "Show - S01E01 - Pilot.mkv"
    subtitle = season / "Show - S01E01 - Pilot.zh-CN.ass"
    image_sub = season / "Show - S01E01 - Pilot.sup.idx"
    unrelated = season / "Other Show - S01E01.ass"

    video.write_text("video", encoding="utf-8")
    subtitle.write_text("subtitle", encoding="utf-8")
    image_sub.write_text("idx", encoding="utf-8")
    unrelated.write_text("subtitle", encoding="utf-8")

    syncer = SubtitleBatchSyncer()
    result = syncer.sync_tree(root, dry_run=True)

    assert result.scanned_videos == 1
    assert result.videos_with_subtitles == 1
    assert result.subtitle_candidates == 2
    assert result.planned == 1
    assert result.attempted == 0
    assert result.success == 0
    assert result.skipped == 1
    assert result.failed == 0
    status_by_path = {item.subtitle_path: item.status for item in result.items}
    assert status_by_path == {
        subtitle: "dry_run",
        image_sub: "skipped",
    }


def test_sync_tree_overwrites_subtitle_on_success(tmp_path):
    root = tmp_path / "Anime Series"
    season = root / "Show" / "Season 01"
    season.mkdir(parents=True, exist_ok=True)

    video = season / "Show - S01E01 - Pilot.mkv"
    subtitle = season / "Show - S01E01 - Pilot.zh-CN.ass"
    video.write_text("video", encoding="utf-8")
    subtitle.write_text("original", encoding="utf-8")

    def _result_factory(video_path, subtitle_path, output_dir):
        output = output_dir / subtitle_path.name
        output.write_text("synced", encoding="utf-8")
        return SyncResult(
            success=True,
            used_fallback=False,
            reason="",
            output_path=output,
            duration=0.1,
        )

    runner = _FakeRunner(_result_factory)
    result = SubtitleBatchSyncer(runner=runner).sync_tree(root, dry_run=False)

    assert len(runner.calls) == 1
    assert result.attempted == 1
    assert result.success == 1
    assert result.failed == 0
    assert subtitle.read_text(encoding="utf-8") == "synced"
    assert result.items[0].status == "success"
    assert result.items[0].synced_to == subtitle


def test_sync_tree_records_failure_without_overwrite(tmp_path):
    root = tmp_path / "Anime Series"
    season = root / "Show" / "Season 01"
    season.mkdir(parents=True, exist_ok=True)

    video = season / "Show - S01E01 - Pilot.mkv"
    subtitle = season / "Show - S01E01 - Pilot.zh-CN.ass"
    video.write_text("video", encoding="utf-8")
    subtitle.write_text("original", encoding="utf-8")

    runner = _FakeRunner(
        lambda video_path, subtitle_path, output_dir: SyncResult(
            success=False,
            used_fallback=True,
            reason="mock failed",
            output_path=None,
            duration=0.1,
        )
    )
    result = SubtitleBatchSyncer(runner=runner).sync_tree(root, dry_run=False)

    assert result.attempted == 1
    assert result.success == 0
    assert result.failed == 1
    assert subtitle.read_text(encoding="utf-8") == "original"
    assert result.items[0].status == "failed"
    assert "mock failed" in result.items[0].reason


def test_sync_tree_supports_parallel_workers(tmp_path):
    root = tmp_path / "Anime Series"
    season = root / "Show" / "Season 01"
    season.mkdir(parents=True, exist_ok=True)

    subtitles = []
    for episode in (1, 2, 3):
        video = season / f"Show - S01E0{episode} - Pilot.mkv"
        subtitle = season / f"Show - S01E0{episode} - Pilot.zh-CN.ass"
        video.write_text("video", encoding="utf-8")
        subtitle.write_text("original", encoding="utf-8")
        subtitles.append(subtitle)

    def _result_factory(video_path, subtitle_path, output_dir):
        output = output_dir / subtitle_path.name
        output.write_text(f"synced:{subtitle_path.name}", encoding="utf-8")
        return SyncResult(
            success=True,
            used_fallback=False,
            reason="",
            output_path=output,
            duration=0.1,
        )

    runner = _FakeRunner(_result_factory)
    result = SubtitleBatchSyncer(runner=runner).sync_tree(
        root,
        dry_run=False,
        workers=3,
    )

    assert result.workers == 3
    assert result.attempted == 3
    assert result.success == 3
    assert result.failed == 0
    assert len(runner.calls) == 3
    for subtitle in subtitles:
        assert subtitle.read_text(encoding="utf-8").startswith("synced:")


def test_cli_filter_result_to_simplified_chinese(tmp_path):
    root = tmp_path / "Anime Series"
    season = root / "Show" / "Season 01"
    season.mkdir(parents=True, exist_ok=True)

    video = season / "Show - S01E01 - Pilot.mkv"
    zh_cn = season / "Show - S01E01 - Pilot.zh-CN.default.ass"
    zh_tw = season / "Show - S01E01 - Pilot.zh-TW.ass"
    video.write_text("video", encoding="utf-8")
    zh_cn.write_text("subtitle", encoding="utf-8")
    zh_tw.write_text("subtitle", encoding="utf-8")

    result = SubtitleBatchSyncer().sync_tree(root, dry_run=True)
    filtered = filter_result_to_simplified_chinese(result)

    status_by_path = {item.subtitle_path: item.status for item in filtered.items}
    assert status_by_path[zh_cn] == "dry_run"
    assert zh_tw not in status_by_path
    assert filtered.planned == 1
    assert filtered.attempted == 0
    assert filtered.failed == 0


def test_cli_simplified_chinese_detection():
    assert is_likely_simplified_chinese_subtitle(Path("Show.zh-CN.default.ass"))
    assert is_likely_simplified_chinese_subtitle(Path("Show.chs.ass"))
    assert not is_likely_simplified_chinese_subtitle(Path("Show.zh-TW.ass"))
    assert not is_likely_simplified_chinese_subtitle(Path("Show.tc.ass"))


def test_is_sidecar_subtitle_requires_same_stem_prefix():
    video = Path("Show - S01E01 - Pilot.mkv")

    assert SubtitleBatchSyncer._is_sidecar_subtitle(
        video, Path("Show - S01E01 - Pilot.zh-CN.ass")
    )
    assert SubtitleBatchSyncer._is_sidecar_subtitle(
        video, Path("Show - S01E01 - Pilot.ass")
    )
    assert not SubtitleBatchSyncer._is_sidecar_subtitle(
        video, Path("Show - S01E02 - Pilot.ass")
    )
    assert not SubtitleBatchSyncer._is_sidecar_subtitle(
        video, Path("Other Show - S01E01.ass")
    )
