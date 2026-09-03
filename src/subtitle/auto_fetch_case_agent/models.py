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

import hashlib
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
    # Bangumi subject 名（方向 A：auto_fetch 搜索词来源）。重命名链路落盘 task 时
    # 从 BangumiClient.get_subject 查得，evidence_broker 从 task_data 抽入。
    # Pi 据此调 search_candidates_batch(BGM 名变体) 起步搜帖。可能为空（旧 task）。
    # 注意：这是 task 级主体 subject 单值（向后兼容），多季合集时只代表主体季。
    bgm_subject_name: str = ''
    bgm_subject_name_cn: str = ''
    # 多季覆盖（per-video BGM subject）：每个 video 所属的 BGM subject id + 名。
    # 重命名链路落盘时建 video→subject 映射（bgm_video_subject_map）+ 每 subject
    # name/name_cn（bgm_subjects），evidence_broker 据此填本字段。多季合集（如
    # 0091 鬼灭 S01+S02+S03+剧场版 = 4 subject）时每 card 带各自 subject，Pi 据此
    # 按 subject 分组多帖多包覆盖。旧 task 无此字段时为 0/空，Pi 回退 task 级单值。
    # subject_name=日文原名（命中干净但可能漏），subject_name_cn=中文（命中全含噪音），
    # Pi 多变体搜。
    bangumi_subject_id: int = 0
    subject_name: str = ''
    subject_name_cn: str = ''
    # 用户字幕语言偏好（subtitle_auto_fetch_preferred_language，默认 zh-CN）。
    # Pi 据此在简繁双语包间抉择：zh-CN 优先 simplified/bilingual（简体含双语），
    # zh-TW 优先 traditional/bilingual。可能为空（旧 task/未配置）。
    preferred_language: str = ''

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
    # 候选是否有任何可下载附件（轻 gate 用）。仅当 packages_loaded=True 时
    # 才是已探测真值；search 后未 load 时为 False 表示"未知/未探测"，AI
    # 不得据此判定无附件，必须先 load_candidate_packages。
    has_downloadable_attachment: bool = False
    # packages 是否已通过 load_candidate_packages 探测填充。
    # False = 仅 search 命中标题，包/附件未知；True = 已加载楼包事实。
    packages_loaded: bool = False
    # 多季覆盖：submit_candidate 时 Pi 声明本帖对应哪个 BGM subject（搜索词来源季）。
    # 一个帖可能覆盖多 subject（如 Avvenire+Arietta 合帖），此处记 Pi 选帖时声明的
    # 主体 subject；submit_package 时据此给 selection 记 subject_id（审计 + auto_fetch
    # 按 subject 下载）。0 = 未声明（旧/单 subject 场景）。
    bangumi_subject_id: int = 0

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

    # is_font_or_patch_only 已删（AI-first）：判"这包是不是字幕包"是语义判断，
    # 固定层 font 关键词检测不百分百准确（日文"フォント"漏判、OAD 被误归 special
    # 等）。包性质改由 Pi 看 post_text + links[].label/filename_hint 自判，SKILL 教。
    # submit_package gate 只留 has_downloadable_link（纯事实：有无可下载 link）。


# ---------------------------------------------------------------------------
# 决策草稿（AI 产出，固定层轻 gate 校验）
# ---------------------------------------------------------------------------

AutoFetchDisposition = Literal[
    'select_candidate',  # 选中某帖 + 语言（先选帖，声明对应 BGM subject
    'select_package',  # 选中某楼包（再选包）
    'submit_complete',  # 所有 subject 都处置完，落 final（含全部 selections + 无帖 subject 列表）
    'need_more_evidence',  # 不确定，继续调查
    'unmatched',  # 该关键词搜不到正片候选，合格跳过
]


class AutoFetchDecision(BaseModel):
    """AI 的选中决策（草稿，不是 verified plan）。

    多季覆盖（多 BGM subject）：对每个 subject 独立 select_candidate(声明
    bangumi_subject_id) → select_package，累加 selections；某 subject 无帖则
    no_candidate_for_subject 声明；全部 subject 处置完 submit_complete 落 final。
    单 subject 场景仍走 select_candidate → select_package → submit_complete。
    ``reason`` 供审计。
    """

    disposition: AutoFetchDisposition = 'select_candidate'
    candidate_ref: str = ''
    package_ref: str = ''
    # select_candidate 声明本选对应哪个 BGM subject（多季分组用）；no_candidate_for_subject
    # 声明哪个 subject 无帖。单 subject 旧场景可为 0（Verifier 回退不强制）。
    bangumi_subject_id: int = 0
    language: str = ''  # 原始语言标签，与字幕导入同口径
    confidence: Literal['High', 'Medium', 'Low'] = 'Medium'
    reason: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------
# 四态 result（accepted / fail_closed / need_confirm / invalid）
# ---------------------------------------------------------------------------

AutoFetchStatus = Literal['accepted', 'fail_closed', 'need_confirm', 'invalid']


class AutoFetchSelectedCandidate(BaseModel):
    """accepted 时锁定的单条选中结果（供 auto_fetch 下载 + 落 processor）。

    多季覆盖时 final_result.selections 是本对象的列表（每 subject 一条）；
    单 subject 旧场景仍可单条。
    """

    candidate_ref: str = ''
    package_ref: str = ''
    detail_url: str = ''
    title: str = ''
    language: str = ''
    download_url: str = ''
    selection_key: str = ''
    # 本选对应的 BGM subject（多季分组审计 + auto_fetch 按.subject 下载）
    bangumi_subject_id: int = 0
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


def build_selection_key(
    *,
    source: str,
    detail_url: str,
    package_id: str,
    download_url: str,
) -> str:
    """Build opaque identity for one candidate/package/link selection."""
    payload = "\0".join(
        (source, detail_url, package_id, download_url)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
