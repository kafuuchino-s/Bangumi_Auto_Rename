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
    # 文件名标签仅作弱提示；正文脚本是固定层的高置信事实。
    language_hint: str = ''
    content_chinese_script: Literal[
        'simplified', 'traditional', 'unknown'
    ] = 'unknown'
    simplified_evidence_count: int = Field(default=0, ge=0)
    traditional_evidence_count: int = Field(default=0, ge=0)

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
    # 该视频所属 BGM subject 的 arc 名（日文原名 / 中文名），来自 rename 落盘的
    # task_data.bgm_video_subject_map + bgm_subjects。多季番各季 arc 名不同
    # （如鬼灭 S02 無限列車編 / S03 遊郭編），字幕包按 arc 名发帖，Case Agent
    # 据此区分同 episode 不同 season（S02E01 vs S03E01 都从 E01 开始，靠 episode
    # number 无法区分，arc 名是关键证据）。可为空（旧 task 无 subject 映射）。
    arc_name: str = ''
    arc_name_cn: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------
# Mapping draft（AI 产出，固定层校验）
# ---------------------------------------------------------------------------

SubtitleDisposition = Literal[
    'map_to_video',
    'unmatched',
    'needs_more_evidence',
]

# unmatched 行的结构化原因枚举（AI 产出，固定层透传，processor 据此分类展示）。
# - no_target_video: 字幕对应的内容（PV/TV-Spot/Picture Drama/OAD/special/花絮/
#   05.5 等）不在 target videos 里——TMDB 无对应条目或 rename 未映射非正片物料。
#   processor 把此类移出 unmatched（确定的"无目标"，非"待人工"）。
# - duplicate_language: 同一 target video 同语言已有字幕，此条是重复（去重）。
# - no_confident_match: 有 target video 但不确定配哪个（真待人工）。
# - unknown: AI 未给（兜底，processor 保守留在 unmatched，不误过滤）。
UnmatchedReasonKind = Literal[
    'no_target_video',
    'duplicate_language',
    'no_confident_match',
    'unknown',
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
    # 仅 disposition='unmatched' 时有意义：结构化原因，供 processor 分类展示。
    # map_to_video / needs_more_evidence 行忽略此字段。
    unmatched_reason_kind: UnmatchedReasonKind = 'unknown'

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


class CompiledUnmatchedEntry(BaseModel):
    """单条已验证的 unmatched 字幕：ref + 结构化原因。

    ``reason_kind`` 由 AI 在 draft row 给出（``unmatched_reason_kind``），
    verifier 透传。processor 据此把 ``no_target_video`` 移出 unmatched
    （确定的"无目标"），其余留在 unmatched（待人工）。
    """

    ref: str = ''
    reason_kind: UnmatchedReasonKind = 'unknown'
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CompiledSubtitlePlan(BaseModel):
    """verified 后的最终字幕计划，供 processor 落盘。"""

    mappings: list[CompiledSubtitleMapping] = Field(default_factory=list)
    # 结构化 unmatched（含 reason_kind，供 processor 分类展示）。
    unmatched: list[CompiledUnmatchedEntry] = Field(default_factory=list)
    summary: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    @property
    def unmatched_refs(self) -> list[str]:
        """兼容旧消费点：返回 unmatched 的 ref 列表。"""
        return [entry.ref for entry in self.unmatched if entry.ref]


# ---------------------------------------------------------------------------
# 辅助：ref 体系
# ---------------------------------------------------------------------------

_SUBTITLE_REF_RE = re.compile(r'^SF\d+$')
_TARGET_REF_RE = re.compile(r'^TV\d+$')


def is_subtitle_ref(ref: str) -> bool:
    return bool(_SUBTITLE_REF_RE.fullmatch(ref or ''))


def is_target_ref(ref: str) -> bool:
    return bool(_TARGET_REF_RE.fullmatch(ref or ''))
