from __future__ import annotations

import json
from pathlib import Path

from src.rename.bgm_to_tmdb import (
    BgmAssignmentRef,
    BgmTargetRef,
    BgmToTmdbBridgeToolState,
    BgmToTmdbInput,
    BgmTargetSpanRef,
    ExternalMappingIndex,
    build_tmdb_legal_graph,
    clear_external_mapping_index_cache,
    load_configured_external_mapping_index,
    load_external_mapping_index,
)
from src.rename.bgm_to_tmdb import external_hints as external_hints_module
from src.rename.bgm_to_tmdb.external_hints import REPO_ROOT


FIXTURES = REPO_ROOT / 'tests' / 'fixtures' / 'bgm_external_hints'


def test_external_snapshots_join_bangumi_extlinker_to_fribb() -> None:
    clear_external_mapping_index_cache()
    index = load_external_mapping_index(
        extlinker_path=str(FIXTURES / 'extlinker-anime-map.json'),
        fribb_path=str(FIXTURES / 'fribb-anime-list-full.json'),
    )

    hints = index.hints_for_subject(83868)
    assert {hint.provider for hint in hints} == {'BangumiExtLinker', 'FribbAnimeLists'}
    assert {hint.tmdb_ref for hint in hints} == {'tv:67048'}
    assert {hint.season_number for hint in hints} == {0, 1}
    assert {hint.provider for hint in hints if hint.season_number == 0} == {'BangumiExtLinker'}
    assert {hint.provider for hint in hints if hint.season_number == 1} == {'FribbAnimeLists'}
    assert index.hints_for_subject(103906)[0].tmdb_ref == 'tv:324877'
    assert index.hints_for_subject(285410)[0].tmdb_ref == 'movie:632088'
    assert index.provider_status['BangumiExtLinker']['status'] == 'loaded'
    assert index.provider_status['FribbAnimeLists']['status'] == 'loaded'
    assert index.issues == ()


def test_fribb_movie_list_and_invalid_rows_are_safe(tmp_path: Path) -> None:
    extlinker_path = tmp_path / 'extlinker.json'
    fribb_path = tmp_path / 'fribb.json'
    extlinker_path.write_text(
        json.dumps([{'bgm_id': '1', 'anidb_id': '2', 'tmdb_id': 'tv/not-an-id'}, 'bad']),
        encoding='utf-8',
    )
    fribb_path.write_text(
        json.dumps([
            {
                'anidb_id': 2,
                'themoviedb_id': {'movie': [123, 124]},
                'season': {'tmdb': 0},
                'episode_offset': {'tmdb': -1},
            }
        ]),
        encoding='utf-8',
    )

    clear_external_mapping_index_cache()
    index = load_external_mapping_index(
        extlinker_path=str(extlinker_path),
        fribb_path=str(fribb_path),
    )

    assert {hint.tmdb_ref for hint in index.hints_for_subject(1)} == {'movie:123', 'movie:124'}
    assert {hint.season_number for hint in index.hints_for_subject(1)} == {0}
    assert {hint.episode_offset for hint in index.hints_for_subject(1)} == {-1}
    assert index.hint_count == 2
    assert any('non-object rows' in issue for issue in index.issues)


def test_snapshot_statuses_are_auditable(tmp_path: Path, monkeypatch) -> None:
    missing_path = tmp_path / 'missing.json'
    invalid_path = tmp_path / 'invalid.json'
    invalid_path.write_bytes(b'not-json')
    oversized_path = tmp_path / 'oversized.json'
    oversized_path.write_bytes(b'12345')

    clear_external_mapping_index_cache()
    index = load_external_mapping_index(
        extlinker_path=str(missing_path),
        fribb_path=str(invalid_path),
    )
    assert index.hint_count == 0
    assert index.provider_status['BangumiExtLinker']['status'] == 'unavailable'
    assert index.provider_status['FribbAnimeLists']['status'] == 'invalid'
    assert any('snapshot unavailable' in issue for issue in index.issues)
    assert any('invalid snapshot' in issue for issue in index.issues)

    monkeypatch.setattr(external_hints_module, '_MAX_SNAPSHOT_BYTES', 4)
    clear_external_mapping_index_cache()
    oversized = load_external_mapping_index(extlinker_path=str(oversized_path))
    assert oversized.hint_count == 0
    assert oversized.provider_status['BangumiExtLinker']['status'] == 'too_large'
    assert any('exceeds' in issue for issue in oversized.issues)


def test_off_mode_does_not_read_configured_snapshot_paths(monkeypatch) -> None:
    calls: list[str] = []

    def get_config(key: str) -> str:
        calls.append(key)
        return {
            'rename_bgm_external_hints_mode': 'off',
            'rename_bgm_extlinker_snapshot_path': 'must-not-be-read.json',
            'rename_bgm_fribb_snapshot_path': 'must-not-be-read.json',
        }.get(key, '')

    monkeypatch.setattr(external_hints_module.cm, 'get_config', get_config)
    mode, index = load_configured_external_mapping_index()

    assert mode == 'off'
    assert index == ExternalMappingIndex.empty()
    assert calls == ['rename_bgm_external_hints_mode']


def test_assist_context_exposes_hints_without_creating_targets(tmp_path: Path) -> None:
    index = ExternalMappingIndex(
        hints_by_subject={
            83868: (
                load_external_mapping_index(
                    extlinker_path=str(FIXTURES / 'extlinker-anime-map.json'),
                    fribb_path='',
                ).hints_for_subject(83868)[0],
            )
        },
        provider_status={'test': {'status': 'loaded'}},
    )
    bridge_input = BgmToTmdbInput(
        assignments=[
            BgmAssignmentRef(
                source_path='Barakamon/01.mkv',
                target=BgmTargetRef(
                    bangumi_subject_id=83868,
                    media_kind='tv',
                    episode_id=1,
                    episode_type='regular',
                    sort=1,
                    ep=1,
                    title='Episode 1',
                ),
                target_span=BgmTargetSpanRef(
                    bangumi_subject_id=83868,
                    media_kind='tv',
                    episode_type='regular',
                ),
            )
        ]
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        external_hints_mode='assist',
        external_mapping_index=index,
        external_hint_prefetch_enabled=False,
    )

    context = state.handle_tool('get_bgm_to_tmdb_bridge_context', {'detail': False})
    card = context['data']['bangumi_subject_cards'][0]
    assert card['external_mapping_hints'][0]['tmdb_ref'] == 'tv:67048'
    assert card['external_mapping_hints'][0]['evidence_only'] is True
    assert context['data']['tmdb_legal_graph']['candidates'] == []
    assert context['data']['external_mapping']['agent_visible'] is True
    audit = json.loads(
        (tmp_path / 'artifacts' / 'external_mapping_hint_audit.json').read_text(
            encoding='utf-8'
        )
    )
    assert audit['mode'] == 'assist'
    assert audit['agent_visible'] is True
    assert audit['index']['hint_count'] == 1


def test_shadow_context_keeps_hints_out_of_agent_cards(tmp_path: Path) -> None:
    index = load_external_mapping_index(
        extlinker_path=str(FIXTURES / 'extlinker-anime-map.json'),
        fribb_path='',
    )
    bridge_input = BgmToTmdbInput(
        assignments=[
            BgmAssignmentRef(
                source_path='Barakamon/01.mkv',
                target=BgmTargetRef(bangumi_subject_id=83868, media_kind='tv', sort=1),
            )
        ]
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        external_hints_mode='shadow',
        external_mapping_index=index,
    )

    context = state.handle_tool('get_bgm_to_tmdb_bridge_context', {})
    assert 'external_mapping_hints' not in context['data']['bangumi_subject_cards'][0]
    assert context['data']['external_mapping']['agent_visible'] is False
    assert context['data']['external_mapping']['audit']['hint_count'] == 3


def test_first_anchor_search_can_use_prefetched_candidates_without_network(tmp_path: Path) -> None:
    index = load_external_mapping_index(
        extlinker_path=str(FIXTURES / 'extlinker-anime-map.json'),
        fribb_path='',
    )
    bridge_input = BgmToTmdbInput(
        assignments=[
            BgmAssignmentRef(
                source_path='Barakamon/01.mkv',
                target=BgmTargetRef(bangumi_subject_id=83868, media_kind='tv', sort=1),
            )
        ]
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([
            {
                'media_type': 'tv',
                'tmdb_id': 67048,
                'display_title': 'Barakamon',
            }
        ]),
        run_dir=tmp_path,
        external_hints_mode='assist',
        external_mapping_index=index,
        external_hint_prefetch_enabled=False,
    )
    state.external_hint_hydrated_refs.add('tv:67048')

    context = state.handle_tool('get_bgm_to_tmdb_bridge_context', {'detail': False})
    external_mapping = context['data']['external_mapping']
    assert external_mapping['unique_prefetched_candidate_ready'] is True
    assert external_mapping['next_action'].startswith('First action: call get_tmdb_legal_graph')

    result = state.handle_tool(
        'search_tmdb_candidates',
        {'query': 'Barakamon', 'media_type': 'tv', 'year': 2014},
    )

    assert result['search_source'] == 'external_hint_prefetch'
    assert result['network_request_skipped'] is True
    assert result['candidates'][0]['tmdb_ref'] == 'tv:67048'
    assert state.tool_summary()['external_hint_search_shortcut_count'] == 1


def test_ordered_movie_source_requires_tv_and_movie_shape_comparison(tmp_path: Path) -> None:
    bridge_input = BgmToTmdbInput(
        assignments=[
            BgmAssignmentRef(
                source_path=f'Yamato/{index:02d}.mkv',
                target=BgmTargetRef(
                    bangumi_subject_id=319390,
                    media_kind='movie',
                    episode_id=index,
                    episode_type='regular',
                    sort=index,
                    title=f'Chapter {index}',
                ),
            )
            for index in range(1, 5)
        ]
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([
            {
                'media_type': 'movie',
                'tmdb_id': 860104,
                'display_title': 'Yamato 2205 TAKE OFF',
                                    'legal_nodes': [
                        {
                            'legal_node_id': 'movie:860104',
                            'media_type': 'movie',
                            'tmdb_id': 860104,
                        }
                    ],
            }
        ]),
        run_dir=tmp_path,
        external_hints_mode='off',
        external_hint_prefetch_enabled=False,
    )

    context = state.handle_tool('get_bgm_to_tmdb_bridge_context', {'detail': False})
    card = context['data']['bangumi_subject_cards'][0]
    observation = card['source_shape_observation']
    policy = context['data']['bridge_contract']['target_shape_policy']

    assert observation['source_item_count'] == 4
    assert observation['ordered_source_items'] is True
    assert observation['target_shape_comparison_required'] is True
    assert observation['target_shape_candidates'] == ['tv_episode_sequence', 'movie_aggregate']
    assert policy['comparison_required'] is True
    assert policy['candidate_target_shapes'] == ['tv_episode_sequence', 'movie_aggregate']

    result = state.handle_tool(
        'get_tmdb_legal_graph',
        {'tmdb_refs': ['movie:860104']},
    )
    assert any('media_type="tv"' in hint for hint in result['target_shape_guidance'])
