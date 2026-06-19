"""auto_fetch Case Agent 固定层骨架测试（Phase 1）。

对齐 ``tests/test_subtitle_case_agent_*.py`` 风格：事实卡 + 轻 gate + workspace
ref 分配。auto_fetch 没有 mapping 合同，重点验：
- evidence_broker 从 task/record 抽 scan_scope/missing_videos/source_video 口径
- provider -> 固定层卡片适配
- 轻 submit gate（候选可下载 / 非 font-patch-only）正反例
- workspace ref 分配与 readable 视图
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.subtitle.auto_fetch_case_agent import (
    AutoFetchDecision,
    AutoFetchCaseWorkspace,
    CandidateCard,
    CandidateLinkCard,
    MissingVideoCard,
    ScanScopeCard,
    SearchKeywordCard,
    ThreadPackageCard,
    build_auto_fetch_case_workspace,
    build_deterministic_keyword_cards,
    build_missing_video_cards,
    build_scan_scope_card,
    candidate_card_from_provider,
    is_candidate_ref,
    is_keyword_ref,
    is_missing_video_ref,
    is_package_ref,
    package_card_from_provider,
    verify_auto_fetch_decision,
)
from src.subtitle.providers.base import (
    SubtitleCandidate,
    SubtitleThreadPackage,
    SubtitleThreadPackageLink,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mv(**kwargs) -> MissingVideoCard:
    base = dict(
        ref='',
        task_uuid='t1',
        video='Foo - S01E01 - Pilot.mkv',
        target_path='/lib/Foo/Season 01/Foo - S01E01 - Pilot.mkv',
        source_video='[SubGroup] Foo 01.mkv',
        task_title='Foo',
        season=1,
        is_movie=False,
    )
    base.update(kwargs)
    return MissingVideoCard(**base)


def _pkg(package_id, flags, *, has_direct=True, links=None):
    pkg_links = links
    if pkg_links is None:
        pkg_links = [
            SubtitleThreadPackageLink(
                url=f'https://x/{package_id}.zip',
                kind='attachment',
                label=f'{package_id}.zip',
                filename_hint=f'{package_id}.zip',
                is_direct_download=has_direct,
            )
        ]
    return SubtitleThreadPackage(
        package_id=package_id,
        page_number=1,
        floor_label=f'{package_id}-floor',
        post_author='author',
        post_time='2023-01-01 00:00:00',
        post_text='package text',
        context_text='package context',
        links=pkg_links,
        has_direct_download=has_direct,
        package_flags=flags,
    )


# ---------------------------------------------------------------------------
# evidence_broker: scan_scope / missing_videos / source_video 口径
# ---------------------------------------------------------------------------

def test_build_scan_scope_card_translates_resolve_scope_output():
    card = build_scan_scope_card(
        {'type': 'series', 'root': '/lib/Foo', 'source': 'task_data'}
    )
    assert card.scope_type == 'series'
    assert card.root == '/lib/Foo'
    assert card.source == 'task_data'


def test_build_scan_scope_card_defaults_unknown_type_to_task():
    card = build_scan_scope_card({'type': 'weird', 'root': ''})
    assert card.scope_type == 'task'


def test_build_missing_video_cards_captures_source_video_from_record_keys():
    """record key = local 源路径；missing_videos 是目标路径。broker 反查得 source_video。"""
    task_data = {
        'uuid': 't1',
        'name': 'Foo',
        'is_movie': False,
        'season_id': 1,
    }
    record_data = {
        '/downloads/[SubGroup] Foo 01.mkv': '/lib/Foo/Season 01/Foo - S01E01 - Pilot.mkv',
        '/downloads/[SubGroup] Foo 02.mkv': '/lib/Foo/Season 01/Foo - S01E02 - Bar.mkv',
    }
    missing = [
        Path('/lib/Foo/Season 01/Foo - S01E01 - Pilot.mkv'),
        Path('/lib/Foo/Season 01/Foo - S01E02 - Bar.mkv'),
    ]
    cards = build_missing_video_cards(
        task_data=task_data, record_data=record_data, missing_videos=missing
    )
    assert len(cards) == 2
    by_video = {c.video: c for c in cards}
    assert by_video['Foo - S01E01 - Pilot.mkv'].source_video == '[SubGroup] Foo 01.mkv'
    assert by_video['Foo - S01E02 - Bar.mkv'].source_video == '[SubGroup] Foo 02.mkv'
    assert all(c.task_uuid == 't1' for c in cards)
    assert all(c.season == 1 for c in cards)


def test_build_missing_video_cards_source_video_from_record_key_basename():
    """record key 不带 SubGroup 前缀也能反查：取 key basename 作 source_video。"""
    task_data = {'uuid': 't1', 'name': 'Foo', 'is_movie': False, 'season_id': 1}
    record_data = {'/old/Foo.mkv': '/lib/Foo/Season 01/Foo - S01E01 - Pilot.mkv'}
    missing = [Path('/lib/Foo/Season 01/Foo - S01E01 - Pilot.mkv')]
    cards = build_missing_video_cards(
        task_data=task_data, record_data=record_data, missing_videos=missing
    )
    assert cards[0].source_video == 'Foo.mkv'


def test_build_missing_video_cards_source_video_empty_when_target_not_in_record():
    """missing video 的目标路径在 record 里查不到 value，source_video 为空，不报错。"""
    task_data = {'uuid': 't1', 'name': 'Foo', 'is_movie': False, 'season_id': 1}
    record_data = {'/old/Other.mkv': '/lib/Other/Season 01/Other - S01E01.mkv'}
    missing = [Path('/lib/Foo/Season 01/Foo - S01E01 - Pilot.mkv')]
    cards = build_missing_video_cards(
        task_data=task_data, record_data=record_data, missing_videos=missing
    )
    assert cards[0].source_video == ''


def test_build_deterministic_keyword_cards_preserves_order_no_dedup():
    """broker 只做卡片翻译，去重在 workspace 层（与字幕 workspace 一致）。"""
    cards = build_deterministic_keyword_cards(['Foo', 'foo', 'Bar'])
    assert [c.keyword for c in cards] == ['Foo', 'foo', 'Bar']
    assert all(c.source == 'deterministic' for c in cards)


# ---------------------------------------------------------------------------
# provider -> 固定层卡片适配
# ---------------------------------------------------------------------------

def test_candidate_card_from_provider_maps_fields_and_packages():
    candidate = SubtitleCandidate(
        title='thread-1',
        detail_url='https://bbs.acgrip.com/thread-1',
        source='acgrip',
        snippet='snip',
        attachment_urls=['https://x/a.zip'],
        thread_packages=[_pkg('p1', ['batch', 'simplified'])],
        pages_scanned=2,
        pagination_truncated=False,
    )
    card = candidate_card_from_provider(candidate)
    assert card.title == 'thread-1'
    assert card.detail_url == 'https://bbs.acgrip.com/thread-1'
    assert card.source == 'acgrip'
    assert card.pages_scanned == 2
    assert len(card.packages) == 1
    assert card.packages[0].package_flags == ['batch', 'simplified']
    assert card.packages[0].has_downloadable_link is True
    assert card.has_downloadable_attachment is True


def test_candidate_card_from_provider_external_download_url_marks_downloadable():
    candidate = SubtitleCandidate(
        title='ext',
        detail_url='https://x/t',
        source='acgrip',
        external_urls=['https://pan.x/sub.7z'],
    )
    card = candidate_card_from_provider(candidate)
    assert card.has_downloadable_attachment is True


def test_candidate_card_from_provider_no_download_when_only_html_external():
    candidate = SubtitleCandidate(
        title='html',
        detail_url='https://x/t',
        source='acgrip',
        external_urls=['https://pan.x/page.html'],
    )
    card = candidate_card_from_provider(candidate)
    assert card.has_downloadable_attachment is False


def test_package_card_from_provider_font_only_flag_detected():
    pkg = _pkg('fontpkg', ['font'])
    card = package_card_from_provider(pkg)
    assert card.is_font_or_patch_only is True


def test_package_card_from_provider_patch_with_content_marker_not_font_only():
    # patch + simplified：有正片语言标记，不算 font/patch-only
    pkg = _pkg('rev', ['patch', 'simplified'])
    card = package_card_from_provider(pkg)
    assert card.is_font_or_patch_only is False


# ---------------------------------------------------------------------------
# workspace ref 分配 + readable
# ---------------------------------------------------------------------------

def test_workspace_assigns_mv_kw_refs_and_dedupes():
    ws = build_auto_fetch_case_workspace(
        task_uuid='t1',
        scan_scope=ScanScopeCard(scope_type='series', root='/lib/Foo', source='task_data'),
        missing_videos=[
            _mv(video='a.mkv'),
            _mv(video='a.mkv'),  # 重复，去重（idx=2 位置跳过，与字幕 workspace 一致有 gap）
            _mv(video='b.mkv'),
        ],
        keywords=[SearchKeywordCard(keyword='Foo'), SearchKeywordCard(keyword='foo')],
    )
    # 与字幕 workspace 一致：ref 按原始位置分配，重复项跳过导致 ref 有 gap
    assert ws.missing_video_refs == ['MV1', 'MV3']
    assert len(ws.missing_videos) == 2
    # 'foo' 与 'Foo' casefold 相同，去重（KW2 位置跳过）
    assert ws.keyword_refs == ['KW1']
    readable = ws.readable_missing_video_cards()
    assert readable[0]['ref'] == 'MV1'
    assert readable[0]['source_video'] == '[SubGroup] Foo 01.mkv'


def test_workspace_add_candidate_assigns_cd_pk_refs():
    ws = build_auto_fetch_case_workspace(
        task_uuid='t1',
        scan_scope=ScanScopeCard(scope_type='task', root='', source=''),
        missing_videos=[_mv()],
        keywords=[SearchKeywordCard(keyword='Foo')],
    )
    card = CandidateCard(
        title='t', detail_url='https://x/t', source='acgrip',
        packages=[ThreadPackageCard(package_id='p1', package_flags=['batch'])],
    )
    indexed = ws.add_candidate(card)
    assert indexed.ref == 'CD1'
    assert indexed.packages[0].ref == 'PK1'
    assert indexed.packages[0].candidate_ref == 'CD1'
    assert ws.candidate_refs == ['CD1']
    assert ws.package_refs == ['PK1']
    # 第二个候选的 package ref 继续递增
    card2 = CandidateCard(
        title='t2', detail_url='https://x/t2', source='acgrip',
        packages=[ThreadPackageCard(package_id='p2', package_flags=['simplified'])],
    )
    indexed2 = ws.add_candidate(card2)
    assert indexed2.ref == 'CD2'
    assert indexed2.packages[0].ref == 'PK2'


# ---------------------------------------------------------------------------
# 轻 submit gate 正反例
# ---------------------------------------------------------------------------

def _ws_with_candidate(*, downloadable=True, font_only=False):
    ws = build_auto_fetch_case_workspace(
        task_uuid='t1',
        scan_scope=ScanScopeCard(scope_type='task', root='', source=''),
        missing_videos=[_mv()],
        keywords=[SearchKeywordCard(keyword='Foo')],
    )
    flags = ['font'] if font_only else ['batch', 'simplified']
    links = (
        [CandidateLinkCard(url='https://x/a.zip', kind='attachment', is_direct_download=True)]
        if downloadable
        else [CandidateLinkCard(url='https://x/a.zip', kind='external', is_direct_download=False)]
    )
    card = CandidateCard(
        title='t', detail_url='https://x/t', source='acgrip',
        has_downloadable_attachment=downloadable,
        packages=[
            ThreadPackageCard(
                package_id='p1', package_flags=flags, links=links,
                has_direct_download=downloadable,
            )
        ],
    )
    ws.add_candidate(card)
    return ws


def test_gate_accepts_select_candidate_with_downloadable_attachment():
    ws = _ws_with_candidate(downloadable=True)
    decision = AutoFetchDecision(
        disposition='select_candidate', candidate_ref='CD1', language='chs'
    )
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is True


def test_gate_rejects_select_candidate_missing_ref():
    ws = _ws_with_candidate()
    decision = AutoFetchDecision(disposition='select_candidate', candidate_ref='')
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is False
    assert any(i.issue_code == 'missing_candidate_ref' for i in result.issues)


def test_gate_rejects_select_candidate_unknown_ref():
    ws = _ws_with_candidate()
    decision = AutoFetchDecision(
        disposition='select_candidate', candidate_ref='CD99'
    )
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is False
    assert any(i.issue_code == 'unknown_candidate_ref' for i in result.issues)


def test_gate_accepts_select_package_downloadable_non_font():
    ws = _ws_with_candidate(downloadable=True, font_only=False)
    decision = AutoFetchDecision(
        disposition='select_package', package_ref='PK1', language='chs'
    )
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is True


def test_gate_rejects_select_package_font_only():
    ws = _ws_with_candidate(downloadable=True, font_only=True)
    decision = AutoFetchDecision(
        disposition='select_package', package_ref='PK1', language='chs'
    )
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is False
    assert any(i.issue_code == 'package_font_or_patch_only' for i in result.issues)


def test_gate_rejects_select_package_not_downloadable():
    ws = _ws_with_candidate(downloadable=False, font_only=False)
    decision = AutoFetchDecision(
        disposition='select_package', package_ref='PK1', language='chs'
    )
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is False
    assert any(i.issue_code == 'package_not_downloadable' for i in result.issues)


def test_gate_rejects_unmatched_carrying_candidate_ref():
    ws = _ws_with_candidate()
    decision = AutoFetchDecision(
        disposition='unmatched', candidate_ref='CD1', reason='no fit'
    )
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is False
    assert any(i.issue_code == 'invalid_candidate_on_unmatched' for i in result.issues)


def test_gate_accepts_unmatched_without_refs():
    ws = _ws_with_candidate()
    decision = AutoFetchDecision(
        disposition='unmatched', reason='no candidate fits this keyword'
    )
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is True


def test_gate_rejects_invalid_disposition():
    ws = _ws_with_candidate()
    decision = AutoFetchDecision(disposition='select_candidate')  # type: ignore[arg-type]
    # 强制非法 disposition
    decision = AutoFetchDecision.model_construct(disposition='bogus')
    result = verify_auto_fetch_decision(workspace=ws, decision=decision)
    assert result.passed is False
    assert any(i.issue_code == 'invalid_disposition' for i in result.issues)


# ---------------------------------------------------------------------------
# ref shape helpers
# ---------------------------------------------------------------------------

def test_ref_shape_helpers():
    assert is_missing_video_ref('MV1') and not is_missing_video_ref('CD1')
    assert is_candidate_ref('CD1') and not is_candidate_ref('PK1')
    assert is_package_ref('PK1') and not is_package_ref('KW1')
    assert is_keyword_ref('KW1') and not is_keyword_ref('MV1')
