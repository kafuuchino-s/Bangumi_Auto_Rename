from src.rename.case_agent.models import BangumiSubjectCard, LocalClusterCard, LocalFileCard
from src.rename.case_agent.query_cards import build_query_cards


def test_build_query_cards_from_basename_parent_cluster_and_subjects_dedupes():
    local_files = [
        LocalFileCard(ref='LF1', path=r'C:\media\A\foo.mkv', parent_display='Show A'),
        LocalFileCard(ref='LF2', path=r'C:\media\B\foo.mkv', parent_display='Show A'),
    ]
    local_clusters = [LocalClusterCard(ref='LC1', title_cues=['Show A', 'Show B'])]
    subjects = [BangumiSubjectCard(ref='BS1', title='Show B', name='Show C', name_cn='Show D')]

    cards = build_query_cards(local_files, local_clusters, subjects)

    assert cards[0].query_text in {'foo.mkv', 'foo'}
    assert 'Show A' in [card.query_text for card in cards]
    assert 'Show B' in [card.query_text for card in cards]
    assert 'Show C' in [card.query_text for card in cards]
    assert 'Show D' in [card.query_text for card in cards]
    assert 'LF1' in cards[1].source_refs and 'LF2' in cards[1].source_refs
    assert cards[0].result_refs == []
    assert cards[0].source_refs == ['LF1', 'LF2']
    assert all(card.query_kind == 'subject_search' for card in cards)


def test_duplicate_query_text_merges_source_refs():
    cards = build_query_cards(
        [LocalFileCard(ref='LF1', path='foo.mp4', parent_display='X')],
        [LocalClusterCard(ref='LC1', title_cues=['X'])],
        [BangumiSubjectCard(ref='BS1', title='X')],
    )

    assert len(cards) >= 2
    assert any(card.query_text == 'X' and card.source_refs == ['LF1', 'LC1', 'BS1'] for card in cards)
    assert cards[1].result_refs == []


def test_query_cards_source_refs_stay_separate_from_results():
    cards = build_query_cards(
        [LocalFileCard(ref='LF1', path='foo.mp4', parent_display='X')],
        [LocalClusterCard(ref='LC1', title_cues=['X'])],
        [BangumiSubjectCard(ref='BS1', title='X')],
    )

    assert any(card.query_text == 'X' and card.source_refs == ['LF1', 'LC1', 'BS1'] for card in cards)
    assert cards[1].result_refs == []


def test_query_cards_keep_release_group_wrapped_text_as_raw_material():
    cards = build_query_cards(
        [LocalFileCard(ref='LF1', path='[Snow-Raws] てーきゅう 2期.mkv', parent_display='[Snow-Raws] てーきゅう 2期')],
        [],
        [],
    )

    texts = [card.query_text for card in cards]
    assert '[Snow-Raws] てーきゅう 2期.mkv' in texts
    assert '[Snow-Raws] てーきゅう 2期' in texts
    assert 'てーきゅう 2期' not in texts


def test_query_cards_keep_works_titles_without_release_group_as_primary():
    cards = build_query_cards(
        [LocalFileCard(ref='LF1', path='[VCB-Studio] OVERLORD IV.mkv', parent_display='[VCB-Studio] OVERLORD IV')],
        [],
        [],
    )

    texts = [card.query_text for card in cards]
    assert '[VCB-Studio] OVERLORD IV.mkv' in texts
    assert '[VCB-Studio] OVERLORD IV' in texts
    assert 'OVERLORD IV' not in texts
