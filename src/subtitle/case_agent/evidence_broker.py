"""字幕 Case Agent 证据 broker：从已处理任务记录抽取目标视频事实。

复用 ``src.subtitle.processor.SubtitleProcessor`` 的任务加载逻辑（读
``data/task`` + ``data/record``），把每个已处理任务展开成
``SubtitleTargetVideoCard`` 列表供 workspace 使用。

不直接读 TMDB / Bangumi——字幕映射的"目标空间"就是已落盘的本地视频文件，
合法空间由 task/record 记录决定（对齐 rename 的"TMDB 才是最终合法输出空间"
在字幕侧的对应物：已处理任务的视频清单才是合法落点空间）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from .models import SubtitleTargetVideoCard

if TYPE_CHECKING:
    from ..processor import ProcessedTask


def build_target_video_cards(
    processed_tasks: "list[Mapping[str, object] | ProcessedTask]",
) -> list[SubtitleTargetVideoCard]:
    """把已处理任务列表展开为目标视频 card 列表。

    每个 (task_uuid, video) 组合对应一张 card。电影单视频任务产出一张 card。
    """
    cards: list[SubtitleTargetVideoCard] = []
    for task in processed_tasks:
        task_uuid = str(task.get('uuid') or '')
        if not task_uuid:
            continue
        is_movie = bool(task.get('is_movie', False))
        title = str(task.get('title') or '')
        season_value = task.get('season')
        season = season_value if isinstance(season_value, int) else None
        videos = list(task.get('videos') or [])
        video_targets = task.get('video_targets') or {}
        # video 名 -> 重命名前 local 原始文件名（AI 证据，非合法落点）
        source_videos = task.get('source_videos') or {}
        target_dir = str(task.get('target_dir') or '')
        task_video_count = len(videos)
        for video in videos:
            video_str = str(video or '')
            if not video_str:
                continue
            # video_targets 优先（电影合集每部电影不同目录）
            video_target = video_targets.get(video_str) if isinstance(video_targets, Mapping) else None
            if video_target:
                card_target_dir = str(video_target).rsplit('/', 1)[0].rsplit('\\', 1)[0]
            else:
                card_target_dir = target_dir
            source_video = ''
            if isinstance(source_videos, Mapping):
                source_video = str(source_videos.get(video_str) or '')
            cards.append(
                SubtitleTargetVideoCard(
                    ref='',  # 由 workspace 分配
                    task_uuid=task_uuid,
                    task_title=title,
                    season=season if not is_movie else None,
                    is_movie=is_movie,
                    video=video_str,
                    source_video=source_video,
                    target_dir=card_target_dir,
                    task_video_count=task_video_count,
                )
            )
    return cards
