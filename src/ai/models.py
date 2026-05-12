from typing import ClassVar, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
    ValidationInfo,
)


class LocalExtractionStep(BaseModel):
    """AI 选择的受限本地切片步骤。固定层只执行这里列出的 op。"""

    op: Literal[
        "strip_bracket_prefixes",
        "strip_bracket_suffixes",
        "split",
        "take_part",
        "first_number",
        "last_number",
        "text_before_first_number",
        "text_after_first_number",
        "bracket_group",
        "parent_dir",
    ]
    separator: str | None = Field(default=None, description="split 使用的分隔符")
    part_index: int | None = Field(default=None, description="take_part/bracket_group/parent_dir 使用的索引")

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class TitleExtractionResult(BaseModel):
    """AI 提取的标题与内容类型。"""

    title: str = Field(..., description="主搜索标题")
    fallback_title: str | None = Field(default=None, description="备选基础标题")
    type: Literal["movie", "tv"] | None = Field(default=None, description="内容类型")

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class LocalExtractionFieldSpec(BaseModel):
    """从某个切片步骤结果中抽取一个本地字段。"""

    step_index: int = Field(default=-1, description="引用 steps 的 0-based index；-1 表示最后一步")
    part_index: int | None = Field(default=None, description="当步骤结果是列表时取第几个 part")
    part_range: list[int] | None = Field(default=None, description="当步骤结果是列表时取 [start, end) 范围并用空格拼接")
    op: Literal["identity", "first_number", "last_number", "text_before_first_number", "text_after_first_number"] = "identity"

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class RuleExtractSpec(BaseModel):
    """单条本地提取规则的字段抽取规格。"""

    series_title: LocalExtractionFieldSpec | None = Field(
        default=None,
        description="抽取 series_title 的规则；为空表示不抽取",
    )
    source_episode_label: LocalExtractionFieldSpec | None = Field(
        default=None,
        description="抽取 source_episode_label 的规则；为空表示不抽取",
    )
    source_part_label: LocalExtractionFieldSpec | None = Field(
        default=None,
        description="抽取 source_part_label 的规则；为空表示不抽取",
    )
    local_role_label: LocalExtractionFieldSpec | None = Field(
        default=None,
        description="抽取 local_role_label 的规则；为空表示不抽取",
    )
    technical_label: LocalExtractionFieldSpec | None = Field(
        default=None,
        description="抽取 technical_label 的规则；为空表示不抽取",
    )
    edition_label: LocalExtractionFieldSpec | None = Field(
        default=None,
        description="抽取 edition_label 的规则；为空表示不抽取",
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


RuleStep = LocalExtractionStep


class LocalExtractionRule(BaseModel):
    """AI 生成的固定层可执行本地命名切片规则。"""

    rule_id: str = Field(..., description="规则 ID，如 R1；只用于追踪本地命名格式")
    applies_to_file_ids: list[str] = Field(default_factory=list, description="这条命名规则覆盖的 file_id")
    source_text: Literal["stem", "name", "relative_path", "parent_dir"] = "stem"
    steps: list[LocalExtractionStep] = Field(default_factory=list)
    extract: RuleExtractSpec = Field(default_factory=RuleExtractSpec, description="series_title/source_episode_label/source_part_label/local_role_label/technical_label/edition_label 等本地字段抽取规格")
    sequence_kind_hint: Literal["episode_like", "sp_like", "ova_like", "oad_like", "part_like", "movie_collection_like", "unknown"] = "unknown"
    confidence: Literal["High", "Medium", "Low"] = "Medium"
    reason: str = Field(default="", description="说明本地命名规则；不得引用 TMDB/Bangumi 结论")

    @field_validator("rule_id", mode="before")
    @classmethod
    def validate_rule_id(cls, v: object) -> str:
        text = str(v or '').strip()
        if not text:
            raise ValueError("rule_id不能为空")
        return text[:40]

    @field_validator("applies_to_file_ids", mode="before")
    @classmethod
    def validate_file_ids(cls, v: object) -> list[str]:
        values = v if isinstance(v, list) else []
        result: list[str] = []
        seen: set[str] = set()
        for item in values:
            text = str(item or '').strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    @field_validator("extract", mode="before")
    @classmethod
    def validate_extract(cls, v: object) -> object:
        if isinstance(v, RuleExtractSpec):
            return v
        return v if isinstance(v, dict) else {}

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class LocalPackageAnalysis(BaseModel):
    """本地包分析：只输出召回标题和固定层可执行的本地切片规则。"""

    search_titles: list[str] = Field(default_factory=list, description="候选召回标题，按优先级排序；第一个是主召回标题")
    recall_intent: str | None = Field(default=None, description="一句话说明为什么这些标题用于召回；不是映射结论")
    extraction_rules: list[LocalExtractionRule] = Field(default_factory=list, description="本地命名切片规则，可多条；rule group 不是最终实体组")
    input_sufficiency: Literal["sufficient", "partial", "insufficient"] | None = Field(
        default=None,
        description="对当前压缩输入是否足以稳定提取 search_titles 的自评；不影响固定层合法性",
    )
    evidence_gaps: list[str] = Field(
        default_factory=list,
        description="当前 projection 缺失或不可靠的证据点，保持简短",
    )
    sample_refs_used: list[str] = Field(
        default_factory=list,
        description="AI 实际参考的代表性 sample_ref / evidence_ref；只列少量关键样本",
    )
    title_cue_confidence_reason: str | None = Field(
        default=None,
        description="说明 search_titles 选择置信度与 title cue 依据；避免展开为逐文件判断",
    )

    @field_validator("search_titles", mode="before")
    @classmethod
    def validate_search_titles(cls, v: object) -> list[str]:
        if v is None:
            return []
        values = [v] if isinstance(v, str) else v if isinstance(v, list) else []
        titles: list[str] = []
        seen: set[str] = set()
        for item in values:
            text = str(item or '').strip().strip('"\'')
            if not text or text.lower() == 'null':
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            titles.append(text[:160])
        return titles[:16]

    @field_validator("recall_intent", mode="before")
    @classmethod
    def validate_recall_intent(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text[:300] if text else None

    @field_validator("extraction_rules", mode="before")
    @classmethod
    def validate_extraction_rules(cls, v: object) -> list[object]:
        return v if isinstance(v, list) else []

    @model_validator(mode="after")
    def require_search_title(self) -> Self:
        if not self.search_titles:
            raise ValueError("search_titles不能为空")
        self.extraction_rules = self.extraction_rules[:80]
        self.evidence_gaps = self.evidence_gaps[:20]
        self.sample_refs_used = self.sample_refs_used[:20]
        return self

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')

class MovieSearchQueriesResult(BaseModel):
    """AI生成的电影TMDB搜索查询候选"""

    queries: list[str] = Field(
        ...,
        description="TMDB搜索查询候选列表，按优先级从高到低排序，最多5条",
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class RouteArbitrationResult(BaseModel):
    """AI 在已给定 TMDB 候选之间做受约束路由仲裁。"""

    selected_route: Literal["movie", "tv", "mixed", "ambiguous"] = Field(
        ..., description="只能在 movie/tv/mixed/ambiguous 中选择，不能发明新候选"
    )
    confidence: Literal["High", "Medium", "Low"] = Field(
        ..., description="仲裁置信度"
    )
    reason: str = Field(..., description="选择理由，必须引用本地结构和候选元数据")
    risk_notes: list[str] = Field(
        default_factory=list,
        description="潜在风险或需要后续 child-route planner 处理的点",
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class ChildRouteReproposalRoute(BaseModel):
    """AI 对 mixed child-route 边界的一条受限重提案。"""

    route_id: Literal["tv_subset", "movie_subset"] = Field(..., description="只能调整现有 TV/Movie child route")
    route_type: Literal["tv", "movie"] = Field(..., description="route 类型必须与 route_id 对应")
    file_ids: list[str] = Field(default_factory=list, description="该 child route claim 的 file_id 列表")
    reason: str = Field(..., description="为什么这些文件属于该 child route")

    @model_validator(mode="after")
    def validate_route_consistency(self) -> Self:
        if self.route_id == "tv_subset" and self.route_type != "tv":
            raise ValueError("tv_subset 必须是 tv route_type")
        if self.route_id == "movie_subset" and self.route_type != "movie":
            raise ValueError("movie_subset 必须是 movie route_type")
        return self

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class ChildRouteReproposalResult(BaseModel):
    """AI 对 mixed child-route overlap/coverage 的受限重提案。"""

    reproposed: bool = Field(..., description="是否给出新的 child route 边界")
    confidence: Literal["High", "Medium", "Low"] = Field(..., description="重提案置信度")
    reason: str = Field(..., description="重提案或无法重提案的原因")
    routes: list[ChildRouteReproposalRoute] = Field(default_factory=list, description="新的 TV/Movie child route 边界")
    unresolved_errors: list[str] = Field(default_factory=list, description="仍无法解决的问题")

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class AcceptedSemanticException(BaseModel):
    """AI semantic review 接受的语义例外。"""

    type: Literal[
        "trailing_special_numbering",
        "local_total_numbering_to_season_zero",
        "movie_vs_tv_special_preference",
        "multi_part_movie",
        "other",
    ] = Field(..., description="语义例外类型")
    file_ids: list[str] = Field(default_factory=list, description="相关 file_id")
    explanation: str = Field(..., description="为什么该例外合理")

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class SemanticReviewFinding(BaseModel):
    """结构化语义审查发现项；仅用于诊断解释和 repair 上下文，不是 fixed-layer 行动计划。"""

    status: Literal["pass", "blocked", "warning"] = Field(
        ..., description="发现级别；仅用于解释语义审查结果，不是 fixed-layer 行动计划或执行 gate"
    )
    issue_code: str = Field(..., description="问题代码")
    file_refs: list[str] = Field(..., description="相关 file_ref 列表；必须显式提供，空则 []")
    target_refs: list[str] = Field(..., description="相关 target_ref 列表；必须显式提供，空则 []")
    evidence_refs: list[str] = Field(..., description="相关 evidence_ref 列表；必须显式提供，空则 []")
    reason: str = Field(..., description="发现原因")
    repair_suggestion: str | None = Field(
        ...,
        description=(
            "可选修复建议；必须显式提供键位，空则 null；"
            "这是诊断信息，不是 fixed-layer 执行指令，不要在 findings 中输出 remap/action/proposed target"
        ),
    )

    @field_validator("file_refs", "target_refs", "evidence_refs", mode="before")
    @classmethod
    def normalize_ref_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if not isinstance(value, list):
            return [str(value).strip()] if str(value).strip() else []

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                normalized.append(text)
        return normalized

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class AIProposalCriticResult(BaseModel):
    """AI 对 canonical proposal 的语义审查结果。"""

    semantic_status: Literal["pass", "suspicious", "ambiguous", "invalid"] = Field(
        ..., description="语义审查状态"
    )
    confidence: Literal["High", "Medium", "Low"] = Field(
        ..., description="审查置信度"
    )
    reason: str = Field(..., description="语义审查理由")
    findings: list[SemanticReviewFinding] = Field(
        ...,
        description=(
            "结构化语义审查发现项，仅作为诊断解释与 repair 上下文；"
            "findings.status 只能辅助解释 top-level semantic_status，不是独立执行 gate 或 remap 裁决"
        ),
    )
    accepted_exceptions: list[AcceptedSemanticException] = Field(
        default_factory=list,
        description="被接受的非机械编号一致性例外，例如 14/15 -> S00E01/S00E02",
    )
    risk_notes: list[str] = Field(default_factory=list, description="风险说明")
    repair_suggestion: str | None = Field(
        default=None,
        description="若 suspicious/ambiguous，可给出修复建议；不直接修改 proposal",
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class SubtitleSearchQueriesResult(BaseModel):
    """AI生成的字幕搜索查询候选"""

    queries: list[str] = Field(
        ...,
        description="字幕搜索查询候选列表，按优先级从高到低排序，最多5条",
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')

class SeasonMapping(BaseModel):
    """季度映射对象"""

    local_group_name: str = Field(..., description="本地组名称，例如目录名")
    maps_to_tmdb_seasons: list[int] = Field(
        ..., description="对应的TMDB季度列表，无需包括第0季"
    )

    @field_validator("maps_to_tmdb_seasons")
    @classmethod
    def validate_tmdb_seasons(cls, v: object) -> list[int]:
        """验证TMDB季度列表"""
        if not isinstance(v, list):
            raise ValueError("maps_to_tmdb_seasons必须是列表类型")

        season_values = cast(list[object], v)
        validated_seasons: list[int] = []

        # 有时子路径中完全无匹配项或仅有第零季的特典，不验证。
        # if not v:
        #     raise ValueError("maps_to_tmdb_seasons不能为空")

        for season in season_values:
            if not isinstance(season, int) or season < 0:
                raise ValueError(f"季度号必须是非负整数: {season}")
            validated_seasons.append(season)

        return validated_seasons

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class AIEpisodeMapping(BaseModel):
    """AI 原始剧集映射：以 TMDB legal node 为主语，选择承载它的本地文件。"""

    legal_node_id: str = Field(
        ...,
        pattern=r"^tmdb:S\d{2}E\d{2,4}$",
        description="AI 从提示词 TMDB legal node 列表中逐字选择的节点 ID，格式固定为 tmdb:SxxEyy；这是映射主语和最终落点来源",
    )
    source_index: int | None = Field(
        default=None,
        ge=1,
        description="承载该 TMDB 节点的本地文件编号（对应 prompt 里的 [001]/[002] ...）",
    )
    file_path: str | None = Field(
        default=None,
        description="承载该 TMDB 节点的本地文件相对路径；优先通过 source_index 回填",
    )
    episode_type: Literal["regular", "special", "movie"] = Field(
        default="regular", description="剧集类型"
    )
    confidence: Literal["High", "Medium", "Low"] = Field(
        default="Medium", description="置信度等级"
    )
    segment_label: str | None = Field(
        default=None,
        description="当一个 TMDB legal node 被明确拆成多个 Part/前後編/segment 文件承载时的分段标签，例如 Part1、Part2；普通映射留空。",
    )

    @field_validator("file_path", mode="before")
    @classmethod
    def normalize_file_path(cls, v: object) -> str | None:
        if v is None or v == "null":
            return None
        text = str(v).strip()
        return text or None

    @field_validator("segment_label", mode="before")
    @classmethod
    def normalize_segment_label(cls, v: object) -> str | None:
        if v is None or v == "null":
            return None
        text = str(v).strip()
        return text or None

    @model_validator(mode="after")
    def validate_mapping_reference(self) -> Self:
        if self.source_index is None and not self.file_path:
            raise ValueError("每个 TMDB legal_node_id 必须选择一个 source_index 或 file_path 作为承载文件")
        return self

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class EpisodeMapping(AIEpisodeMapping):
    """系统 canonical 剧集映射；TMDB 季集由 legal_node_id 校验后派生。"""

    legal_node_id: str = Field(
        ...,
        pattern=r"^(?:tmdb:S\d{2}E\d{2,4}|tv:\d+:S\d{2}E\d{2,4})$",
        description="系统 canonical legal node，可兼容 AI 短节点和内部完整 TV 节点",
    )

    tmdb_season: int | None = Field(
        default=None,
        ge=0,
        description="系统从 legal_node_id 派生的 TMDB 季号",
    )
    tmdb_episode: int | None = Field(
        default=None,
        ge=1,
        description="系统从 legal_node_id 派生的 TMDB 集号",
    )


class MovieFileMapping(BaseModel):
    """电影文件映射（用于电影合集）"""

    file_path: str = Field(..., description="本地文件的相对路径")
    movie_title: str = Field(default="", description="电影标题（用于TMDB搜索）")

    @field_validator("movie_title", mode="before")
    @classmethod
    def validate_movie_title(cls, v: object) -> str:
        """处理movie_title可能是null的情况（如特典文件）"""
        if v is None or v == "null":
            return ""
        return str(v)
    movie_number: int | None = Field(
        default=None, description="系列中的电影编号（如有）"
    )
    year: int | None = Field(default=None, description="电影年份（如有）")
    confidence: Literal["High", "Medium", "Low"] = Field(
        default="Medium", description="置信度等级"
    )
    segment_label: str | None = Field(
        default=None,
        description="当一个 TMDB legal node 被明确拆成多个 Part/前後編/segment 文件承载时的分段标签，例如 Part1、Part2；普通映射留空。",
    )

    @field_validator("movie_number", mode="before")
    @classmethod
    def validate_movie_number(cls, v: object) -> int | None:
        """处理movie_number可能是字符串或null的情况"""
        if v is None or v == "null":
            return None
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return None
        return cast(int | None, v)

    @field_validator("year", mode="before")
    @classmethod
    def validate_year(cls, v: object) -> int | None:
        """处理year可能是字符串或null的情况"""
        if v is None or v == "null":
            return None
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return None
        return cast(int | None, v)

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class MovieCollectionResult(BaseModel):
    """电影合集AI分析结果"""

    is_collection: bool = Field(..., description="是否为电影合集")
    collection_name: str = Field(..., description="合集名称")
    confidence: Literal["High", "Medium", "Low"] = Field(
        ..., description="总体置信度等级"
    )
    reason: str = Field(..., description="简短分析理由说明，保持一句话")
    file_mapping: list[MovieFileMapping] = Field(
        default_factory=list, description="电影文件映射列表"
    )
    unmatched_files: list[str] = Field(
        default_factory=list,
        description="未匹配到电影的本地文件路径列表",
    )
    conflict_details: list[str] = Field(
        default_factory=list,
        description="映射冲突信息（重复文件、缺失标题等）",
    )
    extra_notes: str | None = Field(default=None, description="额外特殊情况说明")

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')

    @model_validator(mode="after")
    def validate_collection_mapping(self) -> Self:
        if self.is_collection and self.confidence in ["High", "Medium"]:
            if not self.file_mapping:
                raise ValueError("电影合集高/中置信度结果必须包含 file_mapping")
        return self

class AIAnalysisResult(BaseModel):
    """AI分析结果"""

    confidence: Literal["High", "Medium", "Low"] = Field(
        ..., description="总体置信度等级"
    )
    reason: str = Field(..., description="分析理由说明")
    season_mapping: list[SeasonMapping] = Field(
        default_factory=list,
        description="季度映射列表，如整个子路径下均无匹配命中项，则无需包含",
    )
    file_mapping: list[AIEpisodeMapping] = Field(
        default_factory=list, description="剧集映射列表"
    )
    unmatched_files: list[str] = Field(
        default_factory=list,
        description="未匹配到 TMDB 的代表性本地文件路径列表（无需穷举全部）",
    )
    conflict_details: list[str] = Field(
        default_factory=list,
        description="映射冲突信息（只保留最关键的少量冲突，如重复映射、越界集数等）",
    )
    extra_notes: str | None = Field(default=None, description="额外特殊情况说明")

    @field_validator("file_mapping")
    @classmethod
    def validate_mapping_not_empty(
        cls, v: list[AIEpisodeMapping], info: ValidationInfo
    ) -> list[AIEpisodeMapping]:
        """验证映射列表不为空（当置信度足够高时）"""
        if info.data:
            confidence = cast(object, info.data.get("confidence", "Low"))
            if confidence in ["High", "Medium"] and not v:
                raise ValueError("高置信度结果必须包含映射信息")
        return v

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')

# ============ 字幕映射模型 ============


class SubtitleMapping(BaseModel):
    """单个字幕文件映射"""

    # 字幕在压缩包中的原始路径（包含文件夹结构）
    subtitle_path: str = Field(..., description="字幕在压缩包中的相对路径")
    # 匹配到的任务UUID
    task_uuid: str = Field(..., description="匹配到的任务UUID")
    # 对应的视频文件名
    video: str = Field(..., description="对应的视频文件名")
    # 语言标签
    language: str | None = Field(
        default=None,
        description="语言标签，如 chs(简体), cht(繁体), jpn(日语), eng(英语)",
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')


class SubtitleMappingResult(BaseModel):
    """字幕映射AI分析结果（支持多季度/多任务）"""

    mappings: list[SubtitleMapping] = Field(
        default_factory=list, description="字幕到视频的映射列表，每个字幕可映射到不同任务"
    )
    unmatched_files: list[str] = Field(
        default_factory=list,
        description="无法匹配的字幕文件路径列表（如任务中没有对应集数）"
    )
    confidence: Literal["High", "Medium", "Low"] = Field(
        default="Medium", description="匹配置信度"
    )
    reason: str | None = Field(default=None, description="匹配理由说明")

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')

class SubtitleCandidateDecision(BaseModel):
    """字幕候选选择结果"""

    selected_index: int = Field(..., ge=0, description="最终选中的候选索引")
    should_use: bool = Field(..., description="是否建议使用该候选")
    confidence: Literal["High", "Medium", "Low"] = Field(
        default="Medium", description="候选选择置信度"
    )
    language_assessment: str | None = Field(
        default=None, description="对候选语言的判断，如简体中文/繁体中文/双语"
    )
    reason: str = Field(..., description="选择理由")
    warnings: list[str] = Field(default_factory=list, description="风险或警告说明")

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')

class SubtitleThreadPackageDecision(BaseModel):
    """帖子内字幕包选择结果"""

    selected_index: int = Field(..., ge=0, description="最终选中的字幕包索引")
    should_use: bool = Field(..., description="是否建议使用该字幕包")
    confidence: Literal["High", "Medium", "Low"] = Field(
        default="Medium", description="字幕包选择置信度"
    )
    language_assessment: str | None = Field(
        default=None,
        description="对字幕包语言的判断，如简体中文/繁体中文/双语",
    )
    reason: str = Field(..., description="选择理由")
    warnings: list[str] = Field(default_factory=list, description="风险或警告说明")

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra='forbid')
