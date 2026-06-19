"""字幕导入 Case Agent 子系统

把字幕压缩包 -> 视频文件映射从单轮 AI prompt + 散落规则模糊匹配，升级为
AI-first + evidence-driven + Verifier 合同校验模式，与 rename 链路（Local->Bangumi
Case Agent / BGM->TMDB 桥接）同构。

固定层只做事实抽取与合同校验（coverage / duplicate / accounting / 合法目标视频）；
候选归属、版本/语言歧义、跨季归属这类不确定判断交给 AI，通过 Case Agent 的
evidence request / MappingDraft / Verifier issue/audit 引导。

复用通用基础件：``src.rename.case_agent.models.CaseVerifierResult`` /
``VerifierIssue``，不重造结果容器。
"""

from .models import (
    SubtitleFileCard,
    SubtitleTargetVideoCard,
    SubtitleMappingRow,
    SubtitleMappingDraft,
    SubtitleMappingAccounting,
    CompiledSubtitlePlan,
    normalize_subtitle_archive_path,
)
from .verifier import (
    verify_subtitle_mapping_draft,
    verify_and_compile_subtitle_plan,
)
from .evidence_broker import build_target_video_cards
from .workspace import SubtitleCaseWorkspace, build_subtitle_case_workspace
from .local_subtitle_entry import (
    build_subtitle_file_cards,
    run_subtitle_case_agent_mapping,
)

__all__ = [
    'SubtitleFileCard',
    'SubtitleTargetVideoCard',
    'SubtitleMappingRow',
    'SubtitleMappingDraft',
    'SubtitleMappingAccounting',
    'CompiledSubtitlePlan',
    'SubtitleCaseWorkspace',
    'normalize_subtitle_archive_path',
    'verify_subtitle_mapping_draft',
    'verify_and_compile_subtitle_plan',
    'build_target_video_cards',
    'build_subtitle_case_workspace',
    'build_subtitle_file_cards',
    'run_subtitle_case_agent_mapping',
]
