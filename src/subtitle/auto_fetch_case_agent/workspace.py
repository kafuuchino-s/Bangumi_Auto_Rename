"""字幕自动抓取 Case Agent 证据工作区。

承载固定层抽取的事实集合（scan_scope + missing_videos + 已用关键词）与
AI 多轮调查中动态加入的候选/楼包卡片，暴露给 AI 的合同边界。

对齐 ``subtitle.case_agent.workspace`` 的角色，但事实是"目标空间 + 候选空间"
两类：missing_videos 是目标事实（固定），candidates/packages 是 AI 取证动态
加入的事实卡。固定层职责：
- 分配短 ref（MV<idx> / KW<idx> / CD<idx> / PK<idx>）并保证唯一。
- 暴露合法 ref 集合给 AI（决策只能引用这些 ref）。
- 提供 readable card 视图（与短 ref 绑定出现，对齐 rename 约定）。
- 候选/楼包的事实由 provider 抓取结果注入（fixed-layer fact, AI 不可改），
  AI 只决定"选哪个 ref"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import (
    CandidateCard,
    MissingVideoCard,
    ScanScopeCard,
    SearchKeywordCard,
    ThreadPackageCard,
)


def _compact_text(value: str, *, limit: int = 200) -> str:
    """压缩长文本喂 Pi（参考 rename case_agent._compact_text）。

    readable card 的 post_text/context_text 可能很长（楼层正文全文），
    一次性灌给 Pi 会撑大 context 导致卡死。readable card 用此压缩成摘要；
    inspect_package 保留全文（按需深入单包）。
    """
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + '...'


@dataclass
class AutoFetchCaseWorkspace:
    """字幕自动抓取证据工作区。"""

    task_uuid: str = ''
    scan_scope: ScanScopeCard | None = None
    missing_videos: list[MissingVideoCard] = field(default_factory=list)
    keywords: list[SearchKeywordCard] = field(default_factory=list)
    candidates: list[CandidateCard] = field(default_factory=list)

    # ------------------------------------------------------------------
    # ref 集合
    # ------------------------------------------------------------------

    @property
    def missing_video_refs(self) -> list[str]:
        return [card.ref for card in self.missing_videos if card.ref]

    @property
    def keyword_refs(self) -> list[str]:
        return [card.ref for card in self.keywords if card.ref]

    @property
    def candidate_refs(self) -> list[str]:
        return [card.ref for card in self.candidates if card.ref]

    @property
    def package_refs(self) -> list[str]:
        refs: list[str] = []
        for candidate in self.candidates:
            refs.extend(pkg.ref for pkg in candidate.packages if pkg.ref)
        return refs

    # ------------------------------------------------------------------
    # 查找
    # ------------------------------------------------------------------

    def missing_video_by_ref(self) -> dict[str, MissingVideoCard]:
        return {card.ref: card for card in self.missing_videos if card.ref}

    def keyword_by_ref(self) -> dict[str, SearchKeywordCard]:
        return {card.ref: card for card in self.keywords if card.ref}

    def candidate_by_ref(self) -> dict[str, CandidateCard]:
        return {card.ref: card for card in self.candidates if card.ref}

    def package_by_ref(self) -> dict[str, ThreadPackageCard]:
        index: dict[str, ThreadPackageCard] = {}
        for candidate in self.candidates:
            for pkg in candidate.packages:
                if pkg.ref:
                    index[pkg.ref] = pkg
        return index

    def keyword_text_by_ref(self) -> dict[str, str]:
        return {card.ref: card.keyword for card in self.keywords if card.ref}

    # ------------------------------------------------------------------
    # readable 视图（喂 AI）
    # ------------------------------------------------------------------

    def readable_scan_scope(self) -> dict[str, object]:
        if self.scan_scope is None:
            return {}
        return {
            'scope_type': self.scan_scope.scope_type,
            'root': self.scan_scope.root,
            'source': self.scan_scope.source,
        }

    def readable_missing_video_cards(self) -> list[dict[str, object]]:
        return [
            {
                'ref': card.ref,
                'task_uuid': card.task_uuid,
                'task_title': card.task_title,
                'season': card.season,
                'is_movie': card.is_movie,
                'video': card.video,
                'target_path': card.target_path,
                # 重命名前 local 原始文件名（AI 证据，非合法落点）；可能为空。
                'source_video': card.source_video,
                # 方向 A：BGM subject 名（auto_fetch 搜索词来源，Pi 据此 search_candidates_batch）
                'bgm_subject_name': card.bgm_subject_name,
                'bgm_subject_name_cn': card.bgm_subject_name_cn,
                # 多季覆盖：per-video BGM subject（Pi 按 subject 分组多帖多包）。
                # bangumi_subject_id>0 时本 card 属该 subject；=0 时旧 task 无映射，
                # Pi 回退 task 级 bgm_subject_name。subject_name=日文原名命中干净但
                # 可能漏，subject_name_cn=中文命中全含噪音，Pi 多变体搜。
                'bangumi_subject_id': card.bangumi_subject_id,
                'subject_name': card.subject_name,
                'subject_name_cn': card.subject_name_cn,
                # 用户字幕语言偏好（Pi 据此在简繁双语包间抉择）
                'preferred_language': card.preferred_language,
            }
            for card in self.missing_videos
        ]

    def readable_keyword_cards(self) -> list[dict[str, object]]:
        return [
            {
                'ref': card.ref,
                'keyword': card.keyword,
                'source': card.source,
            }
            for card in self.keywords
        ]

    def readable_candidate_cards(self) -> list[dict[str, object]]:
        return [
            {
                'ref': card.ref,
                'title': card.title,
                'detail_url': card.detail_url,
                'snippet': card.snippet,
                'source': card.source,
                'pages_scanned': card.pages_scanned,
                'pagination_truncated': card.pagination_truncated,
                # 未 load（packages_loaded=False）时包/附件未知，渲染为 null
                # 表示"未探测"，AI 不得据此判定无附件或 fail_closed。
                'packages_loaded': card.packages_loaded,
                'package_count': (
                    len(card.packages) if card.packages_loaded else None
                ),
                'has_downloadable_attachment': (
                    card.has_downloadable_attachment
                    if card.packages_loaded
                    else None
                ),
                'packages': self._readable_packages(card),
            }
            for card in self.candidates
        ]

    def _readable_packages(
        self, candidate: CandidateCard
    ) -> list[dict[str, object]]:
        return [
            {
                'ref': pkg.ref,
                'candidate_ref': pkg.candidate_ref,
                'package_id': pkg.package_id,
                'page_number': pkg.page_number,
                'floor_label': pkg.floor_label,
                'post_author': pkg.post_author,
                'post_time': pkg.post_time,
                # post_text/context_text 压缩成摘要（全文用 inspect_package 按需读）
                'post_text': _compact_text(pkg.post_text, limit=200),
                'context_text': _compact_text(pkg.context_text, limit=200),
                'has_direct_download': pkg.has_direct_download,
                'package_flags': pkg.package_flags,
                'has_downloadable_link': pkg.has_downloadable_link,
                # links 压成 count + 前 2 个样例（完整链接用 inspect_package）
                'link_count': len(pkg.links),
                'links': [
                    {
                        'url': link.url,
                        'kind': link.kind,
                        'label': link.label,
                        'filename_hint': link.filename_hint,
                        'is_direct_download': link.is_direct_download,
                    }
                    for link in pkg.links[:2]
                ],
            }
            for pkg in candidate.packages
        ]

    # ------------------------------------------------------------------
    # 动态注入候选/楼包（AI 取证后由 tool 调用）
    # ------------------------------------------------------------------

    def add_candidate(self, card: CandidateCard) -> CandidateCard:
        """注入一个候选卡，分配 CD<idx> ref；对其 packages 分配 PK<idx>。"""
        next_cd = len(self.candidates) + 1
        ref = f'CD{next_cd}'
        packages: list[ThreadPackageCard] = []
        for pkg in card.packages:
            next_pk = self.package_refs.__len__() + len(packages) + 1
            packages.append(
                pkg.model_copy(
                    update={'ref': f'PK{next_pk}', 'candidate_ref': ref}
                )
            )
        indexed = card.model_copy(update={'ref': ref, 'packages': packages})
        self.candidates.append(indexed)
        return indexed


def build_auto_fetch_case_workspace(
    *,
    task_uuid: str,
    scan_scope: ScanScopeCard,
    missing_videos: list[MissingVideoCard],
    keywords: list[SearchKeywordCard],
) -> AutoFetchCaseWorkspace:
    """构建工作区并分配 MV<idx> / KW<idx> 短 ref。

    候选/楼包 ref 在 AI 取证动态注入时由 ``add_candidate`` 分配。
    分配规则：MV/KW/CD/PK<idx>，idx 从 1 开始，按入参顺序。
    """
    indexed_missing: list[MissingVideoCard] = []
    seen_missing: set[str] = set()
    for idx, card in enumerate(missing_videos, start=1):
        key = card.video or card.target_path
        if key in seen_missing:
            continue
        seen_missing.add(key)
        if not card.ref:
            card = card.model_copy(update={'ref': f'MV{idx}'})
        indexed_missing.append(card)

    indexed_keywords: list[SearchKeywordCard] = []
    seen_keywords: set[str] = set()
    for idx, card in enumerate(keywords, start=1):
        folded = card.keyword.casefold()
        if folded in seen_keywords:
            continue
        seen_keywords.add(folded)
        if not card.ref:
            card = card.model_copy(update={'ref': f'KW{idx}'})
        indexed_keywords.append(card)

    return AutoFetchCaseWorkspace(
        task_uuid=task_uuid,
        scan_scope=scan_scope,
        missing_videos=indexed_missing,
        keywords=indexed_keywords,
    )
