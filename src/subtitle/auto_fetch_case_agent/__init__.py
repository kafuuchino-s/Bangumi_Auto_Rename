"""字幕自动抓取 Case Agent 子系统

把 ``auto_fetch.py`` 的"单轮 AI 选帖/选包 + 散落 retry/keyword 循环"升级为
AI-first + evidence-driven + Pi 多轮后端，与 rename / 字幕导入两条已落地链路
同构。

**auto_fetch 是 candidate ranking（选帖/选包），不是 mapping**——没有合法落点
空间 / coverage / duplicate / accounting 合同。固定层只做事实抽取（scan_scope +
missing_videos + 候选/楼包卡片来自 provider）与轻 submit gate（候选含可下载附件
/ 非 font/patch-only）；arc 归属 / 版本语言歧义 / 正片 vs 特典 这类不确定判断
全交 AI。

证据口径：``MissingVideoCard.source_video``（重命名前 local 原始文件名，来自
record key）与字幕导入 ``SubtitleTargetVideoCard.source_video`` 同义，统一命名。

复用通用基础件：``src.rename.case_agent.models.CaseVerifierResult`` /
``VerifierIssue``（作 issue/审计载体，不引入完整 Verifier 合同）。
"""

from .models import (
    AutoFetchDecision,
    AutoFetchSelectedCandidate,
    CandidateCard,
    CandidateLinkCard,
    MissingVideoCard,
    ScanScopeCard,
    SearchKeywordCard,
    ThreadPackageCard,
    is_candidate_ref,
    is_keyword_ref,
    is_missing_video_ref,
    is_package_ref,
)
from .workspace import (
    AutoFetchCaseWorkspace,
    build_auto_fetch_case_workspace,
)
from .evidence_broker import (
    build_deterministic_keyword_cards,
    build_missing_video_cards,
    build_scan_scope_card,
    candidate_card_from_provider,
    collect_missing_videos,
    package_card_from_provider,
)
from .verifier import (
    auto_fetch_repair_hints,
    verify_auto_fetch_decision,
)
from .audit import build_auto_fetch_case_snapshot
from .pi_tools import AutoFetchCaseToolState
from .pi_runner import AutoFetchCaseAgentRunResult, run_auto_fetch_case_agent_pi
from .local_auto_fetch_entry import (
    build_candidate_cards,
    run_auto_fetch_case_agent,
)

__all__ = [
    'AutoFetchDecision',
    'AutoFetchSelectedCandidate',
    'CandidateCard',
    'CandidateLinkCard',
    'MissingVideoCard',
    'ScanScopeCard',
    'SearchKeywordCard',
    'ThreadPackageCard',
    'AutoFetchCaseWorkspace',
    'build_auto_fetch_case_workspace',
    'build_deterministic_keyword_cards',
    'build_missing_video_cards',
    'build_scan_scope_card',
    'candidate_card_from_provider',
    'collect_missing_videos',
    'package_card_from_provider',
    'verify_auto_fetch_decision',
    'auto_fetch_repair_hints',
    'build_auto_fetch_case_snapshot',
    'AutoFetchCaseToolState',
    'AutoFetchCaseAgentRunResult',
    'run_auto_fetch_case_agent_pi',
    'build_candidate_cards',
    'run_auto_fetch_case_agent',
    'is_candidate_ref',
    'is_keyword_ref',
    'is_missing_video_ref',
    'is_package_ref',
]
