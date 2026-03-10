import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.pages.subtitle_page import SubtitleUploadDialog


class _FakeProcessor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def process(self, file_path, target_task_uuid=None):
        self.calls.append((Path(file_path).name, target_task_uuid))
        return self.results.pop(0)


async def _fake_io_bound(func, *args):
    return func(*args)


def test_batch_processing_skips_need_confirm_without_blocking(monkeypatch, tmp_path):
    dialog = SubtitleUploadDialog.__new__(SubtitleUploadDialog)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    dialog.current_files = [first, second]
    dialog.batch_results = []

    processor = _FakeProcessor(
        [
            {
                "status": "need_confirm",
                "archive_path": str(first),
                "available_tasks": [{"uuid": "task-1", "title": "Foo", "season": 1}],
                "error": "AI 无法确定匹配",
            },
            {
                "status": "success",
                "archive_path": str(second),
                "matched_task": "Bar",
                "matched_count": 2,
                "total_subtitles": 2,
            },
        ]
    )
    dialog.processor = processor

    monkeypatch.setattr("src.pages.subtitle_page.run.io_bound", _fake_io_bound)
    monkeypatch.setattr(
        SubtitleUploadDialog,
        "_show_processing_view",
        lambda self, current=0, total=1: None,
    )
    monkeypatch.setattr(
        SubtitleUploadDialog,
        "_show_batch_result_view",
        lambda self, results: None,
    )
    monkeypatch.setattr(
        "src.pages.subtitle_page.create_subtitle_table",
        SimpleNamespace(refresh=lambda: None),
    )
    monkeypatch.setattr(
        "src.pages.subtitle_page.notify",
        lambda *args, **kwargs: None,
    )

    class _FakeEmby:
        def is_available(self):
            return False

    monkeypatch.setattr(
        "src.pages.subtitle_page.get_emby_notifier",
        lambda: _FakeEmby(),
    )

    asyncio.run(dialog._start_batch_processing())

    assert processor.calls == [("first.zip", None), ("second.zip", None)]
    assert len(dialog.batch_results) == 2
    assert dialog.batch_results[0]["status"] == "need_confirm"
    assert "请单独导入此压缩包" in dialog.batch_results[0]["error"]
    assert dialog.batch_results[1]["status"] == "success"
