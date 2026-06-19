"""字幕 Case Agent 数据模型

对齐 ``src.rename.bgm_to_tmdb.models`` 的形状：pydantic + ``extra='forbid'`` +
disposition Literal + 路径归一化 field_validator。

复用 ``src.rename.case_agent.models.CaseVerifierResult`` / ``VerifierIssue`` 作为
合同校验结果容器，不在此重造。
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# 路径归一化
# ---------------------------------------------------------------------------

def normalize_subtitle_archive_path(path: object) -> str:
    """归一化压缩包内字幕相对路径：统一正斜杠、去首尾斜杠、去 ``./`` 前缀。"""
    text = str(path or '').strip().replace('\\', '/')
    while text.startswith('./'):
        text = text[2:]
    return text.strip('/')


# ---------------------------------------------------------------------------
# 语言标签
# ---------------------------------------------------------------------------

# Emby 标准语言码 -> 是否简体（用于决定是否追加 .default）。
# 与 processor.py::LANGUAGE_MAP 保持同一口径，但这里只保留判定所需的最小集合；
# 实际归一化仍由 processor 的 LANGUAGE_MAP 完成。
SIMPLIFIED_CHINESE_CODE = 'zh-CN'


# ---------------------------------------------------------------------------
# 事实卡片（固定层抽取，AI 不可改）
# ---------------------------------------------------------------------------

class SubtitleFileCard(BaseModel):
    """解压后的字幕文件事实。

    ``ref`` 是固定层分配的短 ref（``SF<idx>``），供 AI 在 draft 里引用，
    与 readable card 绑定出现（对齐 rename 的短 ref 约定）。
    """

    ref: str = ''
    archive_path: str = ''
    filename: str = ''
    # 语言提示：从文件名后缀提取的原始标签（如 chs/cht/jpn），可为空。
    language_hint: str = ''

    @field_validator('archive_path', mode='before')
    @classmethod
    def _normalize_archive_path(cls, value: object) -> str:
        return normalize_subtitle_archive_path(value)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class SubtitleTargetVideoCard(BaseModel):
    """来自已处理任务记录的目标视频事实。

    ``ref`` 是固定层分配的短 ref（``TV<idx>``），唯一标识 (task_uuid, video) 组合。
    ``video`` 是重命名后的目标文件名（合法落点，verifier 据此校验）。
    ``source_video`` 是重命名前的 local 原始文件名（仅作 AI 匹配证据，不参与
    合同裁决）；字幕包与 local 命名高度一致时，这是比目标名更强的配对线索。
    可为空（旧 record 无 source / 直传字幕文件场景）。
    """

    ref: str = ''
    task_uuid: str = ''
    task_title: str = ''
    season: int | None = None
    is_movie: bool = False
    video: str = ''
    # 重命名前的 local 原始文件名（AI 证据，非合法落点）。
    source_video: str = ''
    target_dir: str = ''
    # 该任务下视频总数，供 AI 判断"单视频电影直接配对"等。
    task_video_count: int = 0

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------
# Mapping draft（AI 产出，固定层校验）
# ---------------------------------------------------------------------------

SubtitleDisposition = Literal[
    'map_to_video',
    'unmatched',
    'needs_more_evidence',
]


class SubtitleMappingRow(BaseModel):
    """字幕映射草稿行。

    一行 = 一个字幕文件（``subtitle_ref``）的处理决定：
    - ``map_to_video``：映射到某个目标视频（``target_ref``）+ 语言
    - ``unmatched``：无法匹配，合格结果（需说明原因）
    - ``needs_more_evidence``：尚不确定，保持未决（accepted readiness 不允许）
    """

    row_ref: str = ''
    subtitle_ref: str = ''
    disposition: SubtitleDisposition = 'map_to_video'
    target_ref: str = ''
    # 原始语言标签（如 chs/cht/jpn），由 processor 的 LANGUAGE_MAP 归一到 Emby 码。
    language: str = ''
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class SubtitleMappingAccounting(BaseModel):
    """草稿 accounting：coverage / duplicate / 未决统计。"""

    subtitle_count: int = 0
    mapped_count: int = 0
    unmatched_count: int = 0
    needs_more_evidence_count: int = 0
    accounted_for_count: int = 0
    # accepted readiness 要求：accounted_for == subtitle_count 且 needs_more_evidence == 0
    accepted_accounting_ready: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class SubtitleMappingDraft(BaseModel):
    """字幕映射草稿。"""

    rows: list[SubtitleMappingRow] = Field(default_factory=list)
    summary: str = ''
    confidence: Literal['High', 'Medium', 'Low'] = 'Medium'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------
# Compiled plan（verifier 通过后的最终映射，喂给落盘层）
# ---------------------------------------------------------------------------

class CompiledSubtitleMapping(BaseModel):
    """单条已验证映射：subtitle_ref -> target_ref + Emby 语言码。"""

    subtitle_ref: str = ''
    subtitle_archive_path: str = ''
    target_ref: str = ''
    task_uuid: str = ''
    video: str = ''
    target_dir: str = ''
    # Emby 标准语言码（已归一）。
    emby_lang: str = ''
    is_simplified: bool = False
    is_movie: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CompiledSubtitlePlan(BaseModel):
    """verified 后的最终字幕计划，供 processor 落盘。"""

    mappings: list[CompiledSubtitleMapping] = Field(default_factory=list)
    unmatched_refs: list[str] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------
# 辅助：ref 体系
# ---------------------------------------------------------------------------

_SUBTITLE_REF_RE = re.compile(r'^SF\d+$')
_TARGET_REF_RE = re.compile(r'^TV\d+$')


def is_subtitle_ref(ref: str) -> bool:
    return bool(_SUBTITLE_REF_RE.fullmatch(ref or ''))


def is_target_ref(ref: str) -> bool:
    return bool(_TARGET_REF_RE.fullmatch(ref or ''))
