"""字幕 Case Agent 证据工作区。

承载固定层抽取的事实集合（字幕文件 + 目标视频）与暴露给 AI 的合同边界。
对齐 rename 的 workspace 角色，但字幕语义简单，无需 dossier / visible_ref_catalog
的复杂层级——事实就是两类扁平 card 列表。

固定层职责：
- 分配短 ref（SF<idx> / TV<idx>）并保证唯一。
- 暴露合法 ref 集合给 AI（draft 只能引用这些 ref）。
- 提供 readable card 视图（与短 ref 绑定出现，对齐 rename 约定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    SubtitleFileCard,
    SubtitleTargetVideoCard,
)


@dataclass
class SubtitleCaseWorkspace:
    """字幕映射证据工作区。"""

    archive_name: str = ''
    subtitle_files: list[SubtitleFileCard] = field(default_factory=list)
    target_videos: list[SubtitleTargetVideoCard] = field(default_factory=list)

    @property
    def subtitle_refs(self) -> list[str]:
        return [card.ref for card in self.subtitle_files if card.ref]

    @property
    def target_refs(self) -> list[str]:
        return [card.ref for card in self.target_videos if card.ref]

    def subtitle_card_by_ref(self) -> dict[str, SubtitleFileCard]:
        return {card.ref: card for card in self.subtitle_files if card.ref}

    def target_card_by_ref(self) -> dict[str, SubtitleTargetVideoCard]:
        return {card.ref: card for card in self.target_videos if card.ref}

    def readable_subtitle_cards(self) -> list[dict[str, object]]:
        """短 ref + 可读 card 绑定视图，供 AI payload 使用。"""
        return [
            {
                'ref': card.ref,
                'archive_path': card.archive_path,
                'filename': card.filename,
                'language_hint': card.language_hint,
            }
            for card in self.subtitle_files
        ]

    def readable_target_cards(self) -> list[dict[str, object]]:
        return [
            {
                'ref': card.ref,
                'task_uuid': card.task_uuid,
                'task_title': card.task_title,
                'season': card.season,
                'is_movie': card.is_movie,
                'video': card.video,
                'target_dir': card.target_dir,
                'task_video_count': card.task_video_count,
            }
            for card in self.target_videos
        ]


def build_subtitle_case_workspace(
    *,
    archive_name: str,
    subtitle_files: list[SubtitleFileCard],
    target_videos: list[SubtitleTargetVideoCard],
) -> SubtitleCaseWorkspace:
    """构建工作区并分配短 ref。

    分配规则：SF<idx> / TV<idx>，idx 从 1 开始，按入参顺序。重复入参会跳过
    已分配的 card（以 archive_path / (task_uuid, video) 去重）。
    """
    seen_subtitle_paths: set[str] = set()
    indexed_subtitles: list[SubtitleFileCard] = []
    for idx, card in enumerate(subtitle_files, start=1):
        key = card.archive_path or card.filename
        if key in seen_subtitle_paths:
            continue
        seen_subtitle_paths.add(key)
        if not card.ref:
            card = card.model_copy(update={'ref': f'SF{idx}'})
        indexed_subtitles.append(card)

    seen_target_keys: set[str] = set()
    indexed_targets: list[SubtitleTargetVideoCard] = []
    for idx, card in enumerate(target_videos, start=1):
        key = f'{card.task_uuid}::{card.video}'
        if key in seen_target_keys:
            continue
        seen_target_keys.add(key)
        if not card.ref:
            card = card.model_copy(update={'ref': f'TV{idx}'})
        indexed_targets.append(card)

    return SubtitleCaseWorkspace(
        archive_name=archive_name,
        subtitle_files=indexed_subtitles,
        target_videos=indexed_targets,
    )
