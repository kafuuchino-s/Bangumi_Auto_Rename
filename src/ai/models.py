from typing import List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _make_gemini_compatible_schema(schema: dict) -> dict:
    """
    将Pydantic生成的JSON Schema转换为Gemini API兼容格式
    - 移除additionalProperties
    - 内联展开$defs/$ref引用
    """
    def remove_additional_properties(obj):
        """递归移除所有additionalProperties"""
        if isinstance(obj, dict):
            obj.pop('additionalProperties', None)
            for value in obj.values():
                remove_additional_properties(value)
        elif isinstance(obj, list):
            for item in obj:
                remove_additional_properties(item)

    def inline_refs(schema):
        """将$defs/$ref引用内联展开为完整定义"""
        defs = schema.pop('$defs', {})

        def resolve(obj):
            if isinstance(obj, dict):
                if '$ref' in obj:
                    ref_name = obj['$ref'].split('/')[-1]
                    return resolve(defs[ref_name].copy())
                return {k: resolve(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [resolve(item) for item in obj]
            return obj

        return resolve(schema)

    remove_additional_properties(schema)
    return inline_refs(schema)


class TitleExtractionResult(BaseModel):
    """标题提取结果"""

    title: str = Field(..., description="主搜索标题，优先用于 TMDB 查询")
    fallback_title: Optional[str] = Field(
        default=None,
        description="主标题未命中时可回退尝试的基础标题",
    )
    type: Optional[Literal["movie", "tv"]] = Field(
        default=None,
        description="内容类型，movie 或 tv",
    )

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, v):
        if v is None:
            raise ValueError("title不能为空")

        title = str(v).strip().strip('"\'')
        if not title:
            raise ValueError("title不能为空")
        return title

    @field_validator("fallback_title", mode="before")
    @classmethod
    def validate_fallback_title(cls, v):
        if v is None:
            return None

        fallback_title = str(v).strip().strip('"\'')
        if not fallback_title or fallback_title.lower() == "null":
            return None
        return fallback_title

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, v):
        if v is None:
            return None

        content_type = str(v).strip().lower()
        if content_type not in ["movie", "tv"]:
            return None
        return content_type

    @model_validator(mode="after")
    def normalize_fallback_title(self):
        if (
            self.fallback_title
            and self.fallback_title.casefold() == self.title.casefold()
        ):
            self.fallback_title = None
        return self

    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    @classmethod
    def gemini_json_schema(
        cls, by_alias: bool = True, ref_template: str = '#/$defs/{model}'
    ):
        """生成Gemini API兼容的JSON Schema"""
        schema = super().model_json_schema(by_alias=by_alias, ref_template=ref_template)
        return _make_gemini_compatible_schema(schema)


class SeasonMapping(BaseModel):
    """季度映射对象"""

    local_group_name: str = Field(..., description="本地组名称，例如目录名")
    maps_to_tmdb_seasons: List[int] = Field(
        ..., description="对应的TMDB季度列表，无需包括第0季"
    )

    @field_validator("maps_to_tmdb_seasons")
    @classmethod
    def validate_tmdb_seasons(cls, v):
        """验证TMDB季度列表"""
        if not isinstance(v, list):
            raise ValueError("maps_to_tmdb_seasons必须是列表类型")

        # 有时子路径中完全无匹配项或仅有第零季的特典，不验证。
        # if not v:
        #     raise ValueError("maps_to_tmdb_seasons不能为空")

        for season in v:
            if not isinstance(season, int) or season < 0:
                raise ValueError(f"季度号必须是非负整数: {season}")

        return v

    model_config = ConfigDict(populate_by_name=True, extra='forbid')


class EpisodeMapping(BaseModel):
    """单个剧集映射"""

    file_path: str = Field(..., description="本地文件的相对路径")
    tmdb_season: int = Field(..., ge=0, description="TMDB季号")
    tmdb_episode: int = Field(..., ge=1, description="TMDB集号")
    episode_type: Literal["regular", "special", "movie"] = Field(
        default="regular", description="剧集类型"
    )
    confidence: Literal["High", "Medium", "Low"] = Field(
        default="Medium", description="置信度等级"
    )

    model_config = ConfigDict(populate_by_name=True, extra='forbid')


class MovieFileMapping(BaseModel):
    """电影文件映射（用于电影合集）"""

    file_path: str = Field(..., description="本地文件的相对路径")
    movie_title: str = Field(default="", description="电影标题（用于TMDB搜索）")

    @field_validator("movie_title", mode="before")
    @classmethod
    def validate_movie_title(cls, v):
        """处理movie_title可能是null的情况（如特典文件）"""
        if v is None or v == "null":
            return ""
        return str(v)
    movie_number: Optional[int] = Field(
        default=None, description="系列中的电影编号（如有）"
    )
    year: Optional[int] = Field(default=None, description="电影年份（如有）")
    confidence: Literal["High", "Medium", "Low"] = Field(
        default="Medium", description="置信度等级"
    )

    @field_validator("movie_number", mode="before")
    @classmethod
    def validate_movie_number(cls, v):
        """处理movie_number可能是字符串或null的情况"""
        if v is None or v == "null":
            return None
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return None
        return v

    @field_validator("year", mode="before")
    @classmethod
    def validate_year(cls, v):
        """处理year可能是字符串或null的情况"""
        if v is None or v == "null":
            return None
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return None
        return v

    model_config = ConfigDict(populate_by_name=True, extra='forbid')


class MovieCollectionResult(BaseModel):
    """电影合集AI分析结果"""

    is_collection: bool = Field(..., description="是否为电影合集")
    collection_name: str = Field(..., description="合集名称")
    confidence: Literal["High", "Medium", "Low"] = Field(
        ..., description="总体置信度等级"
    )
    reason: str = Field(..., description="分析理由说明")
    file_mapping: List[MovieFileMapping] = Field(
        default_factory=list, description="电影文件映射列表"
    )
    unmatched_files: List[str] = Field(
        default_factory=list,
        description="未匹配到电影的本地文件路径列表",
    )
    conflict_details: List[str] = Field(
        default_factory=list,
        description="映射冲突信息（重复文件、缺失标题等）",
    )
    extra_notes: Optional[str] = Field(default=None, description="额外特殊情况说明")

    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    @model_validator(mode="after")
    def validate_collection_mapping(self):
        if self.is_collection and self.confidence in ["High", "Medium"]:
            if not self.file_mapping:
                raise ValueError("电影合集高/中置信度结果必须包含 file_mapping")
        return self

    @classmethod
    def gemini_json_schema(
        cls, by_alias: bool = True, ref_template: str = '#/$defs/{model}'
    ):
        """生成Gemini API兼容的JSON Schema"""
        schema = super().model_json_schema(by_alias=by_alias, ref_template=ref_template)
        return _make_gemini_compatible_schema(schema)


class AIAnalysisResult(BaseModel):
    """AI分析结果"""

    confidence: Literal["High", "Medium", "Low"] = Field(
        ..., description="总体置信度等级"
    )
    reason: str = Field(..., description="分析理由说明")
    season_mapping: List[SeasonMapping] = Field(
        default_factory=list,
        description="季度映射列表，如整个子路径下均无匹配命中项，则无需包含",
    )
    file_mapping: List[EpisodeMapping] = Field(
        default_factory=list, description="剧集映射列表"
    )
    unmatched_files: List[str] = Field(
        default_factory=list,
        description="未匹配到 TMDB 的本地文件路径列表",
    )
    conflict_details: List[str] = Field(
        default_factory=list,
        description="映射冲突信息（重复映射、越界集数等）",
    )
    extra_notes: Optional[str] = Field(default=None, description="额外特殊情况说明")

    @field_validator("file_mapping")
    @classmethod
    def validate_mapping_not_empty(cls, v, info):
        """验证映射列表不为空（当置信度足够高时）"""
        # 在Pydantic V2中，需要从info.data获取其他字段值
        if hasattr(info, 'data') and info.data:
            confidence = info.data.get("confidence", "Low")
            if confidence in ["High", "Medium"] and not v:
                raise ValueError("高置信度结果必须包含映射信息")
        return v

    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    @classmethod
    def gemini_json_schema(
        cls, by_alias: bool = True, ref_template: str = '#/$defs/{model}'
    ):
        """生成Gemini API兼容的JSON Schema"""
        schema = super().model_json_schema(by_alias=by_alias, ref_template=ref_template)
        return _make_gemini_compatible_schema(schema)

    # 为了向后兼容，保留schema方法
    @classmethod
    def schema(cls, by_alias: bool = True, ref_template: str = '#/definitions/{model}'):
        """向后兼容的schema方法"""
        return cls.gemini_json_schema(by_alias=by_alias, ref_template=ref_template)


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
    language: Optional[str] = Field(
        default=None,
        description="语言标签，如 chs(简体), cht(繁体), jpn(日语), eng(英语)",
    )

    model_config = ConfigDict(populate_by_name=True, extra='forbid')


class SubtitleMappingResult(BaseModel):
    """字幕映射AI分析结果（支持多季度/多任务）"""

    mappings: List[SubtitleMapping] = Field(
        default_factory=list, description="字幕到视频的映射列表，每个字幕可映射到不同任务"
    )
    unmatched_files: List[str] = Field(
        default_factory=list,
        description="无法匹配的字幕文件路径列表（如任务中没有对应集数）"
    )
    confidence: Literal["High", "Medium", "Low"] = Field(
        default="Medium", description="匹配置信度"
    )
    reason: Optional[str] = Field(default=None, description="匹配理由说明")

    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    @classmethod
    def gemini_json_schema(
        cls, by_alias: bool = True, ref_template: str = "#/$defs/{model}"
    ):
        """生成Gemini API兼容的JSON Schema"""
        schema = super().model_json_schema(
            by_alias=by_alias, ref_template=ref_template
        )
        return _make_gemini_compatible_schema(schema)
