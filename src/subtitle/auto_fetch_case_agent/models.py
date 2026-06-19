"""字幕自动抓取 Case Agent 数据模型

对齐 ``src.subtitle.case_agent.models`` 的形状：pydantic + ``extra='forbid'`` +
Literal disposition + 字段归一。但 **auto_fetch 是 candidate ranking（选帖/选包），
不是 mapping**——没有 coverage / duplicate / accounting 合同，所以这里没有
SubtitleMappingDraft / CompiledPlan 那套；模型是"事实卡 + 决策草稿 + 选中结果"。

复用 ``src.rename.case_agent.models.CaseVerifierResult`` / ``VerifierIssue`` 作为
issue/审计载体（轻 submit gate：候选含可下载附件 / 非 font/patch-only），不引入
完整 Verifier 合同。

证据口径：``source_video``（重命名前 local 原始文件名，来自 record key）与字幕导入
侧 ``SubtitleTargetVideoCard.source_video`` 同义，统一命名避免两套说法。
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# 事实卡片（固定层抽取，AI 不可改）
# ---------------------------------------------------------------------------

class MissingVideoCard(BaseModel):
    """缺失字幕的目标视频事实。

    auto_fetch 的"目标空间"就是 scan_scope 内缺 sidecar 字幕的本地视频。
    ``ref`` 是固定层分配的短 ref（``MV<idx>``），供 AI 在决策里引用。
    ``source_video`` 是重命名前 local 原始文件名（record key），与字幕导入
    ``SubtitleTargetVideoCard.source_video`` 同口径——字幕包与 local 命名高度
    一致时，这是比目标名更强的配对线索。可为空（旧 record 无 source）。
    """

    ref: str = ''
    task_uuid: str = ''
    video: str = ''
    target_path: str = ''
    # 重命名前 local 原始文件名（AI 证据，非合法落点）；可能为空。
    source_video: str = ''
    task_title: str = ''
    season: int | None = None
    is_movie: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class ScanScopeCard(BaseModel):
    """扫描作用域事实（series / movie / task）。

    直接对应 ``auto_fetch._resolve_scan_scope`` 的输出，作为固定层事实卡暴露给 AI，
    告诉它"在哪个目录范围找缺字幕视频"。
    """

    scope_type: Literal['series', 'movie', 'task'] = 'task'
    root: str = ''
    source: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class SearchKeywordCard(BaseModel):
    """一次搜索关键词的事实。

    ``source`` 区分 deterministic（tmdb_name/name/源目录标题变体）与
    ai_expansion（``generate_subtitle_search_queries`` 扩词）。AI 在多轮调查中
    也可主动发起 ``search_candidates(keyword)``，每次搜索登记一张卡。
    """

    ref: str = ''
    keyword: str = ''
    source: Literal['deterministic', 'ai_expansion', 'ai_tool'] = 'deterministic'

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CandidateLinkCard(BaseModel):
    """候选帖内一个下载链接事实（attachment / external）。"""

    url: str = ''
    kind: Literal['attachment', 'external'] = 'external'
    label: str = ''
    filename_hint: str = ''
    is_direct_download: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class CandidateCard(BaseModel):
    """acgrip 搜索命中的帖子候选事实。

    ``ref`` 是固定层分配的短 ref（``CD<idx>``）。``packages`` 是该帖楼包列表
    （``PK<idx>`` ref 由 workspace 在 load 后分配）。AI 选帖后用 candidate_ref
    锁定，再在 packages 里选包。
    """

    ref: str = ''
    title: str = ''
    detail_url: str = ''
    snippet: str = ''
    source: str = 'acgrip'
    pages_scanned: int = 0
    pagination_truncated: bool = False
    packages: list['ThreadPackageCard'] = Field(default_factory=list)
    # 候选是否有任何可下载附件（轻 gate 用）
    has_downloadable_attachment: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    @property
    def package_refs(self) -> list[str]:
        return [pkg.ref for pkg in self.packages if pkg.ref]


class ThreadPackageCard(BaseModel):
    """帖内一个楼层包事实。

    ``ref`` 是固定层分配的短 ref（``PK<idx>``），``candidate_ref`` 指回所属
    ``CD<idx>``。``package_flags`` 来自 provider 的 ``_detect_package_flags``
    （batch/revision/patch/special/font/simplified/traditional/bilingual），
    供轻 gate 判 font/patch-only 与 AI 判正片 vs 特典。
    """

    ref: str = ''
    candidate_ref: str = ''
    package_id: str = ''
    page_number: int = 1
    floor_label: str = ''
    post_author: str = ''
    post_time: str = ''
    post_text: str = ''
    context_text: str = ''
    has_direct_download: bool = False
    package_flags: list[str] = Field(default_factory=list)
    links: list[CandidateLinkCard] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    @property
    def has_downloadable_link(self) -> bool:
        return any(link.is_direct_download for link in self.links)

    @property
    def is_font_or_patch_only(self) -> bool:
        """轻 gate：是否仅含字体包/补丁包（正片包不应是 font/patch-only）。

        判定：flags 同时命中 font 或 patch，且不命中 batch/simplified/
        traditional/bilingual 任一正片语言标记。这是 submit_package 的 gate 之一。
        """
        flags = {str(flag).lower() for flag in self.package_flags}
        has_content_marker = bool(
            flags & {'batch', 'simplified', 'traditional', 'bilingual'}
        )
        has_font_or_patch = bool(flags & {'font', 'patch'})
        return has_font_or_patch and not has_content_marker


# ---------------------------------------------------------------------------
# 决策草稿（AI 产出，固定层轻 gate 校验）
# ---------------------------------------------------------------------------

AutoFetchDisposition = Literal[
    'select_candidate',  # 选中某帖 + 语言（先选帖）
    'select_package',  # 选中某楼包（再选包）
    'need_more_evidence',  # 不确定，继续调查
    'unmatched',  # 该关键词搜不到正片候选，合格跳过
]


class AutoFetchDecision(BaseModel):
    """AI 的选中决策（草稿，不是 verified plan）。

    两步：先 ``select_candidate`` 锁定帖 + 语言评估，再 ``select_package`` 锁定
    楼包。两步都过轻 gate 后才 accepted。``reason`` 供审计。
    """

    disposition: AutoFetchDisposition = 'select_candidate'
    candidate_ref: str = ''
    package_ref: str = ''
    language: str = ''  # 原始语言标签，与字幕导入同口径
    confidence: Literal['High', 'Medium', 'Low'] = 'Medium'
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------
# 四态 result（accepted / fail_closed / need_confirm / invalid）
# ---------------------------------------------------------------------------

AutoFetchStatus = Literal['accepted', 'fail_closed', 'need_confirm', 'invalid']


class AutoFetchSelectedCandidate(BaseModel):
    """accepted 时锁定的选中结果（供 auto_fetch 下载 + 落 processor）。"""

    candidate_ref: str = ''
    package_ref: str = ''
    detail_url: str = ''
    title: str = ''
    language: str = ''
    download_url: str = ''
    # 原始 provider 对象的 JSON 快照（供 auto_fetch 复用 provider.download）
    candidate_snapshot: dict[str, object] = Field(default_factory=dict)
    package_snapshot: dict[str, object] = Field(default_factory=dict)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------
# 辅助：ref 体系
# ---------------------------------------------------------------------------

_MISSING_VIDEO_REF_RE = re.compile(r'^MV\d+$')
_CANDIDATE_REF_RE = re.compile(r'^CD\d+$')
_PACKAGE_REF_RE = re.compile(r'^PK\d+$')
_KEYWORD_REF_RE = re.compile(r'^KW\d+$')


def is_missing_video_ref(ref: str) -> bool:
    return bool(_MISSING_VIDEO_REF_RE.fullmatch(ref or ''))


def is_candidate_ref(ref: str) -> bool:
    return bool(_CANDIDATE_REF_RE.fullmatch(ref or ''))


def is_package_ref(ref: str) -> bool:
    return bool(_PACKAGE_REF_RE.fullmatch(ref or ''))


def is_keyword_ref(ref: str) -> bool:
    return bool(_KEYWORD_REF_RE.fullmatch(ref or ''))


# 延迟解析 forward ref（CandidateCard.packages -> ThreadPackageCard）
CandidateCard.model_rebuild()
