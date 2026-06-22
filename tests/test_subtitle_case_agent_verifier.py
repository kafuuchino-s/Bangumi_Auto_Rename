"""字幕 Case Agent 固定层合同校验单测。

对齐 tests/test_case_agent_mapping_draft_verifier.py 风格：直接构造事实卡片与
草稿，断言 issue_code。覆盖 coverage / duplicate_source / duplicate_target /
unknown_target / missing_language / needs_more_evidence / unmatched 行为 /
accounting / 编译。
"""

from __future__ import annotations

from src.subtitle.case_agent.evidence_broker import build_target_video_cards
from src.subtitle.case_agent.mapping_draft import compute_subtitle_mapping_accounting
from src.subtitle.case_agent.models import (
    SubtitleFileCard,
    SubtitleMappingDraft,
    SubtitleMappingRow,
    SubtitleTargetVideoCard,
)
from src.subtitle.case_agent.verifier import (
    verify_and_compile_subtitle_plan,
    verify_subtitle_mapping_draft,
)
from src.subtitle.case_agent.workspace import build_subtitle_case_workspace


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _tasks() -> list[dict[str, object]]:
    return [
        {
            'uuid': 't1',
            'title': 'Foo',
            'season': 1,
            'is_movie': False,
            'videos': ['Foo - S01E01 - A.mkv', 'Foo - S01E02 - B.mkv'],
            'target_dir': '/lib/Foo (2020)/Season 01',
            'video_targets': {},
        }
    ]


def _subs(count: int = 2) -> list[SubtitleFileCard]:
    return [
        SubtitleFileCard(
            ref='',
            archive_path=f'S1/0{i}.ass',
            filename=f'0{i}.ass',
            language_hint='chs',
        )
        for i in range(1, count + 1)
    ]


def _workspace(count: int = 2):
    return build_subtitle_case_workspace(
        archive_name='foo.zip',
        subtitle_files=_subs(count),
        target_videos=build_target_video_cards(_tasks()),
    )


def _lang_resolver(lang: str) -> tuple[str, bool]:
    table = {'chs': ('zh-CN', True), 'cht': ('zh-TW', False), 'jpn': ('ja', False)}
    return table.get((lang or '').lower().strip(), ('zh-CN', True))


def _row(row_ref: str, subtitle_ref: str, disposition: str = 'map_to_video',
         target_ref: str = '', language: str = 'chs', reason: str = '',
         unmatched_reason_kind: str = 'unknown') -> SubtitleMappingRow:
    return SubtitleMappingRow(
        row_ref=row_ref,
        subtitle_ref=subtitle_ref,
        disposition=disposition,  # type: ignore[arg-type]
        target_ref=target_ref,
        language=language,
        reason=reason,
        unmatched_reason_kind=unmatched_reason_kind,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# workspace / broker
# ---------------------------------------------------------------------------

def test_workspace_assigns_short_refs():
    ws = _workspace()
    assert ws.subtitle_refs == ['SF1', 'SF2']
    assert ws.target_refs == ['TV1', 'TV2']
    by_ref = ws.target_card_by_ref()
    assert by_ref['TV1'].video == 'Foo - S01E01 - A.mkv'
    assert by_ref['TV2'].task_video_count == 2


def test_broker_dedupes_duplicate_target_keys():
    tasks = _tasks() + _tasks()
    cards = build_target_video_cards(tasks)
    # 同 (task_uuid, video) 不应重复
    keys = {f'{c.task_uuid}::{c.video}' for c in cards}
    assert len(keys) == 2


# ---------------------------------------------------------------------------
# accounting
# ---------------------------------------------------------------------------

def test_accounting_ready_when_all_mapped_or_unmatched():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', disposition='unmatched', reason='no matching video'),
    ])
    acc = compute_subtitle_mapping_accounting(draft, subtitle_count=2)
    assert acc.mapped_count == 1
    assert acc.unmatched_count == 1
    assert acc.needs_more_evidence_count == 0
    assert acc.accepted_accounting_ready is True


def test_accounting_not_ready_when_needs_more_evidence():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', disposition='needs_more_evidence'),
    ])
    acc = compute_subtitle_mapping_accounting(draft, subtitle_count=2)
    assert acc.needs_more_evidence_count == 1
    assert acc.accepted_accounting_ready is False


# ---------------------------------------------------------------------------
# verifier: happy path
# ---------------------------------------------------------------------------

def test_valid_draft_passes():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', target_ref='TV2'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is True
    assert res.summary == 'accepted'


def test_unmatched_without_target_passes():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', disposition='unmatched', reason='cannot determine season'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is True


# ---------------------------------------------------------------------------
# verifier: coverage
# ---------------------------------------------------------------------------

def test_missing_subtitle_blocked_coverage():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        # SF2 漏掉
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'coverage_error' for i in res.issues)


def test_extra_row_blocked_coverage():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', target_ref='TV2'),
        _row('R3', 'SF3', target_ref='TV1'),  # SF3 不存在
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'unknown_subtitle_ref' for i in res.issues)
    assert any(i.issue_code == 'coverage_error' for i in res.issues)


# ---------------------------------------------------------------------------
# verifier: duplicate_source
# ---------------------------------------------------------------------------

def test_duplicate_subtitle_ref_blocked():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF1', target_ref='TV2'),  # SF1 重复
        _row('R3', 'SF2', disposition='unmatched'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'duplicate_subtitle_ref' for i in res.issues)


# ---------------------------------------------------------------------------
# verifier: unknown / invalid target
# ---------------------------------------------------------------------------

def test_unknown_target_ref_blocked():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV9'),
        _row('R2', 'SF2', disposition='unmatched'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'unknown_target_ref' for i in res.issues)


def test_invalid_target_ref_shape_blocked():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='BE1'),  # 非 TV* 形状
        _row('R2', 'SF2', disposition='unmatched'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'invalid_ref_shape' for i in res.issues)


def test_missing_language_blocked():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1', language=''),
        _row('R2', 'SF2', disposition='unmatched'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'missing_language' for i in res.issues)


# ---------------------------------------------------------------------------
# verifier: duplicate target language
# ---------------------------------------------------------------------------

def test_same_target_same_language_blocked():
    ws = _workspace()
    # 两个字幕都映射到 TV1 且都 chs -> 冲突
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1', language='chs'),
        _row('R2', 'SF2', target_ref='TV1', language='chs'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'duplicate_target_language' for i in res.issues)


def test_same_target_different_language_allowed():
    ws = _workspace()
    # 同一视频挂简繁双语 -> 合法
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1', language='chs'),
        _row('R2', 'SF2', target_ref='TV1', language='cht'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    # 注意：这里 TV2 未被覆盖，但 coverage 只要求每个字幕出现，不要求每个视频被覆盖
    assert res.passed is True


# ---------------------------------------------------------------------------
# verifier: needs_more_evidence blocks accepted readiness
# ---------------------------------------------------------------------------

def test_needs_more_evidence_blocks_readiness():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', disposition='needs_more_evidence'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'not_ready' for i in res.issues)


def test_needs_more_evidence_with_target_blocked():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', disposition='needs_more_evidence', target_ref='TV2'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'invalid_target_on_needs_more_evidence' for i in res.issues)


def test_unmatched_with_target_blocked():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', disposition='unmatched', target_ref='TV2'),
    ])
    res = verify_subtitle_mapping_draft(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
    )
    assert res.passed is False
    assert any(i.issue_code == 'invalid_target_on_unmatched' for i in res.issues)


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------

def test_compile_produces_plan_with_emby_lang():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1', language='chs'),
        _row('R2', 'SF2', target_ref='TV2', language='cht'),
    ])
    plan, res = verify_and_compile_subtitle_plan(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
        language_resolver=_lang_resolver,
    )
    assert res.passed is True
    assert plan is not None
    assert len(plan.mappings) == 2
    m1, m2 = plan.mappings
    assert m1.emby_lang == 'zh-CN' and m1.is_simplified is True
    assert m2.emby_lang == 'zh-TW' and m2.is_simplified is False
    assert m1.video == 'Foo - S01E01 - A.mkv'
    assert m1.task_uuid == 't1'


def test_compile_returns_none_when_blocked():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV9'),  # unknown
        _row('R2', 'SF2', disposition='unmatched'),
    ])
    plan, res = verify_and_compile_subtitle_plan(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
        language_resolver=_lang_resolver,
    )
    assert res.passed is False
    assert plan is None


def test_compile_collects_unmatched_refs():
    ws = _workspace()
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', disposition='unmatched', reason='cannot determine'),
    ])
    plan, res = verify_and_compile_subtitle_plan(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
        language_resolver=_lang_resolver,
    )
    assert res.passed is True
    assert plan is not None
    assert plan.unmatched_refs == ['SF2']
    assert len(plan.mappings) == 1


def test_compile_propagates_unmatched_reason_kind():
    """unmatched row 的 unmatched_reason_kind 透传到 compiled plan.unmatched 结构。"""
    ws = _workspace(count=4)
    draft = SubtitleMappingDraft(rows=[
        _row('R1', 'SF1', target_ref='TV1'),
        _row('R2', 'SF2', disposition='unmatched',
             reason='TV-Spot special has no matching target video',
             unmatched_reason_kind='no_target_video'),
        _row('R3', 'SF3', disposition='unmatched',
             reason='duplicate TC variant',
             unmatched_reason_kind='duplicate_language'),
        _row('R4', 'SF4', disposition='unmatched',
             reason='unsure which episode',
             unmatched_reason_kind='no_confident_match'),
    ])
    plan, res = verify_and_compile_subtitle_plan(
        subtitle_files=ws.subtitle_files,
        target_videos=ws.target_videos,
        draft=draft,
        language_resolver=_lang_resolver,
    )
    assert res.passed is True
    assert plan is not None
    # 结构化 unmatched 保留 reason_kind + reason
    by_ref = {e.ref: e for e in plan.unmatched}
    assert by_ref['SF2'].reason_kind == 'no_target_video'
    assert 'TV-Spot' in by_ref['SF2'].reason
    assert by_ref['SF3'].reason_kind == 'duplicate_language'
    assert by_ref['SF4'].reason_kind == 'no_confident_match'
    # property 兼容：unmatched_refs 仍返回全部 ref
    assert plan.unmatched_refs == ['SF2', 'SF3', 'SF4']
