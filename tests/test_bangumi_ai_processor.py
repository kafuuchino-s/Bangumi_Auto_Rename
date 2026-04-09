from pathlib import Path
from types import SimpleNamespace

from src.ai.models import AIAnalysisResult
from src.rename.ai_processor import AIProcessor


class StubContextBuilder:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def build_tv_context(self, anime_info, local_files):
        self.calls.append((anime_info, local_files))
        return self.context


class StubAIClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def is_available(self):
        return True

    def analyze_episode_mapping(self, anime_info, local_files, bangumi_context=None):
        self.calls.append(
            {
                "anime_info": anime_info,
                "local_files": local_files,
                "bangumi_context": bangumi_context,
            }
        )
        return self.result


class StubVideoAnalyzer:
    def __init__(self, file_analysis):
        self.file_analysis = file_analysis

    def analyze_video_files(self, path, current_video_files):
        return list(self.file_analysis)


class DummySubtitleProcessor:
    pass


def test_ai_processor_threads_bangumi_context_into_ai_mapping(tmp_path):
    video_path = tmp_path / "[VCB-Studio] Choujigen Game Neptune The Animation [13].mkv"
    video_path.write_text("video", encoding="utf-8")
    file_analysis = [
        {
            "filename": video_path.name,
            "path": video_path.name,
            "duration": 24.0,
        }
    ]
    bangumi_context = {
        "source": "bangumi",
        "selected_subject_id": 47957,
        "subjects": [{"subject": {"id": 47957}, "episodes": []}],
    }
    ai_result = AIAnalysisResult(
        confidence="Low",
        reason="ok",
        season_mapping=[],
        file_mapping=[],
        unmatched_files=[video_path.name],
        conflict_details=[],
        extra_notes=None,
    )

    processor = AIProcessor()
    processor.ai_client = StubAIClient(ai_result)
    processor.video_analyzer = StubVideoAnalyzer(file_analysis)
    processor.subtitle_processor = DummySubtitleProcessor()
    processor.bangumi_context_builder = StubContextBuilder(bangumi_context)

    anime_info = {
        "name": "超次元游戏 海王星",
        "original_name": "超次元ゲーム ネプテューヌ THE ANIMATION",
        "first_air_date": "2013-07-12",
    }

    result = processor.analyze_anime_files(
        tmp_path,
        anime_info,
        video_files=[video_path],
    )

    assert result is ai_result
    assert len(processor.bangumi_context_builder.calls) == 1
    assert processor.ai_client.calls[0]["bangumi_context"] == bangumi_context
    assert processor.ai_client.calls[0]["local_files"] == file_analysis
