from __future__ import annotations

from pathlib import Path

from src.rename.bgm_to_tmdb import (
    BgmToTmdbMapping,
    BgmToTmdbMappingDraft,
    TmdbLegalNode,
    TmdbRenameDestination,
    TmdbRenamePlan,
    TmdbRenamePlanItem,
    TmdbRenamePlanRoots,
    build_tmdb_legal_graph,
    compile_bgm_to_tmdb_input,
    compile_verified_bgm_to_tmdb_rename_plan,
    run_bgm_to_tmdb_rename_plan_dry_run,
    tv_legal_node_id,
    verify_and_compile_bgm_to_tmdb_plan,
    verify_bgm_to_tmdb_rename_plan,
)
from src.rename.case_agent.recipe import (
    CompiledOrganizeAssignment,
    CompiledOrganizePlan,
    CompiledTarget,
    CompiledTargetSpan,
)


def test_tv_episode_compiles_to_dry_run_season_path_without_mutation(tmp_path: Path) -> None:
    tv_root = tmp_path / 'Anime'
    movie_root = tmp_path / 'Anime Movies'
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[_assignment('Show/E01.mkv', sort=1, ep=1)],
        candidates=[{
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Example Show',
            'year': 2024,
            'legal_nodes': [_tv_node(42, 1, 1, title='Start')],
        }],
        mappings=[BgmToTmdbMapping(source_path='Show/E01.mkv', tmdb_legal_node_ids=['tv:42:S01E01'])],
    )

    plan, result = compile_verified_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        roots=TmdbRenamePlanRoots(tv_root=str(tv_root), movie_root=str(movie_root)),
        source_root=tmp_path / 'Source',
    )

    assert result.passed is True
    assert plan.dry_run is True
    assert plan.file_mutation_allowed is False
    assert plan.items[0].source_abs_path.endswith(str(Path('Source') / 'Show' / 'E01.mkv'))
    assert plan.items[0].destination is not None
    assert Path(plan.items[0].destination.target_path) == (
        tv_root / 'Example Show (2024)' / 'Season 01' / 'Example Show - S01E01.mkv'
    )
    assert not (tv_root / 'Example Show (2024)').exists()


def test_movie_compiles_to_movie_root(tmp_path: Path) -> None:
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[_assignment('Movie.mkv', media_kind='movie', episode_type='movie', sort=None, ep=None)],
        candidates=[{
            'media_type': 'movie',
            'tmdb_id': 900,
            'display_title': 'Example Movie',
            'year': 2023,
            'legal_nodes': [TmdbLegalNode(legal_node_id='movie:900', media_type='movie', tmdb_id=900)],
        }],
        mappings=[BgmToTmdbMapping(source_path='Movie.mkv', tmdb_legal_node_ids=['movie:900'])],
    )

    plan, result = compile_verified_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        roots={'tv_root': str(tmp_path / 'Anime'), 'movie_root': str(tmp_path / 'Movies')},
    )

    assert result.passed is True
    assert plan.items[0].destination is not None
    assert Path(plan.items[0].destination.target_path) == (
        tmp_path / 'Movies' / 'Example Movie (2023)' / 'Example Movie (2023).mkv'
    )


def test_special_season_zero_uses_season_00_path(tmp_path: Path) -> None:
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[_assignment('SP01.mkv', episode_type='special', sort=1, ep=1)],
        candidates=[{
            'media_type': 'tv',
            'tmdb_id': 77,
            'display_title': 'Special Show',
            'year': 2022,
            'legal_nodes': [_tv_node(77, 0, 1, episode_type='special')],
        }],
        mappings=[BgmToTmdbMapping(source_path='SP01.mkv', tmdb_legal_node_ids=['tv:77:S00E01'])],
    )

    plan, result = compile_verified_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        roots={'tv_root': str(tmp_path / 'Anime'), 'movie_root': str(tmp_path / 'Movies')},
    )

    assert result.passed is True
    assert plan.items[0].destination is not None
    assert plan.items[0].destination.season_folder == 'Season 00'
    assert plan.items[0].destination.episode_token == 'S00E01'


def test_tv_span_compiles_to_single_multi_episode_target(tmp_path: Path) -> None:
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[
            _assignment(
                'Merged.mkv',
                target_span=CompiledTargetSpan(
                    bangumi_subject_id=100,
                    media_kind='tv',
                    episode_ids=[101, 102],
                    sort_start=1,
                    sort_end=2,
                    episode_type='regular',
                ),
            )
        ],
        candidates=[{
            'media_type': 'tv',
            'tmdb_id': 88,
            'display_title': 'Span Show',
            'year': 2021,
            'legal_nodes': [_tv_node(88, 1, 1), _tv_node(88, 1, 2)],
        }],
        mappings=[
            BgmToTmdbMapping(
                source_path='Merged.mkv',
                tmdb_legal_node_ids=['tv:88:S01E01', 'tv:88:S01E02'],
            )
        ],
    )

    plan, result = compile_verified_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        roots={'tv_root': str(tmp_path / 'Anime'), 'movie_root': str(tmp_path / 'Movies')},
    )

    assert result.passed is True
    assert len(plan.items) == 1
    assert plan.items[0].destination is not None
    assert plan.items[0].destination.episode_token == 'S01E01-E02'
    assert Path(plan.items[0].destination.target_path).name == 'Span Show - S01E01-E02.mkv'


def test_absent_and_supplemental_items_do_not_get_target_paths(tmp_path: Path) -> None:
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[
            _assignment('Missing SP.mkv', episode_type='special', sort=1, ep=1),
            CompiledOrganizeAssignment(
                source_path='Bonus.mkv',
                disposition='non_bangumi_or_supplemental',
                reason='bonus',
            ),
        ],
        candidates=[],
        mappings=[
            BgmToTmdbMapping(
                source_path='Missing SP.mkv',
                disposition='tmdb_target_absent',
                tmdb_legal_node_ids=[],
                reason='TMDB exposes no matching special node',
            ),
            BgmToTmdbMapping(
                source_path='Bonus.mkv',
                disposition='unmapped_supplemental',
                tmdb_legal_node_ids=[],
                reason='supplemental',
            ),
        ],
    )

    plan, result = compile_verified_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        roots={'tv_root': str(tmp_path / 'Anime'), 'movie_root': str(tmp_path / 'Movies')},
    )

    assert result.passed is True
    assert plan.target_item_count == 0
    assert plan.tmdb_absent_count == 1
    assert plan.supplemental_count == 1
    assert [item.target_path for item in plan.items] == ['', '']


def test_duplicate_target_path_is_rejected(tmp_path: Path) -> None:
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[
            _assignment('Movie A.mkv', media_kind='movie', episode_type='movie', sort=None, ep=None),
            _assignment('Movie B.mkv', bangumi_subject_id=101, media_kind='movie', episode_type='movie', sort=None, ep=None),
        ],
        candidates=[
            {
                'media_type': 'movie',
                'tmdb_id': 900,
                'display_title': 'Same Title',
                'year': 2020,
                'legal_nodes': [TmdbLegalNode(legal_node_id='movie:900', media_type='movie', tmdb_id=900)],
            },
            {
                'media_type': 'movie',
                'tmdb_id': 901,
                'display_title': 'Same Title',
                'year': 2020,
                'legal_nodes': [TmdbLegalNode(legal_node_id='movie:901', media_type='movie', tmdb_id=901)],
            },
        ],
        mappings=[
            BgmToTmdbMapping(source_path='Movie A.mkv', tmdb_legal_node_ids=['movie:900']),
            BgmToTmdbMapping(source_path='Movie B.mkv', tmdb_legal_node_ids=['movie:901']),
        ],
    )

    _plan, result = compile_verified_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        roots={'tv_root': str(tmp_path / 'Anime'), 'movie_root': str(tmp_path / 'Movies')},
    )

    assert result.passed is False
    assert 'duplicate_target_path' in _issue_codes(result)


def test_target_path_outside_root_is_rejected(tmp_path: Path) -> None:
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[_assignment('E01.mkv')],
        candidates=[{
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Example',
            'legal_nodes': [_tv_node(42, 1, 1)],
        }],
        mappings=[BgmToTmdbMapping(source_path='E01.mkv', tmdb_legal_node_ids=['tv:42:S01E01'])],
    )
    bad_plan = TmdbRenamePlan(
        roots=TmdbRenamePlanRoots(tv_root=str(tmp_path / 'Anime'), movie_root=str(tmp_path / 'Movies')),
        items=[
            TmdbRenamePlanItem(
                source_path='E01.mkv',
                tmdb_legal_node_ids=['tv:42:S01E01'],
                destination=TmdbRenameDestination(
                    media_type='tv',
                    tmdb_ref='tv:42',
                    tmdb_id=42,
                    legal_node_ids=['tv:42:S01E01'],
                    title='Example',
                    root_key='tv_root',
                    root_path=str(tmp_path / 'Anime'),
                    file_name='Example - S01E01.mkv',
                    target_path=str(tmp_path / 'Outside' / 'Example - S01E01.mkv'),
                ),
            )
        ],
    )

    result = verify_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        rename_plan=bad_plan,
    )

    assert result.passed is False
    assert 'target_path_outside_root' in _issue_codes(result)


def test_existing_target_path_is_blocked_by_default(tmp_path: Path) -> None:
    tv_root = tmp_path / 'Anime'
    target = tv_root / 'Example Show (2024)' / 'Season 01' / 'Example Show - S01E01.mkv'
    target.parent.mkdir(parents=True)
    target.write_text('exists', encoding='utf-8')
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[_assignment('E01.mkv')],
        candidates=[{
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Example Show',
            'year': 2024,
            'legal_nodes': [_tv_node(42, 1, 1)],
        }],
        mappings=[BgmToTmdbMapping(source_path='E01.mkv', tmdb_legal_node_ids=['tv:42:S01E01'])],
    )

    _plan, result = compile_verified_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        roots={'tv_root': str(tv_root), 'movie_root': str(tmp_path / 'Movies')},
    )

    assert result.passed is False
    assert 'target_path_exists' in _issue_codes(result)


def test_run_dry_run_payload_contains_rename_plan(tmp_path: Path) -> None:
    bridge_input, graph, verified_plan = _verified_bridge(
        assignments=[_assignment('E01.mkv')],
        candidates=[{
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Example',
            'legal_nodes': [_tv_node(42, 1, 1)],
        }],
        mappings=[BgmToTmdbMapping(source_path='E01.mkv', tmdb_legal_node_ids=['tv:42:S01E01'])],
    )

    payload = run_bgm_to_tmdb_rename_plan_dry_run(
        bridge_input=bridge_input,
        legal_graph=graph,
        verified_plan=verified_plan,
        roots={'tv_root': str(tmp_path / 'Anime'), 'movie_root': str(tmp_path / 'Movies')},
        write_snapshot=False,
    )

    assert payload['ok'] is True
    assert payload['status'] == 'accepted'
    assert payload['rename_plan']['items'][0]['destination']['target_path']


def _verified_bridge(
    *,
    assignments: list[CompiledOrganizeAssignment],
    candidates: list[dict],
    mappings: list[BgmToTmdbMapping],
):
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=assignments),
        source_path='Accepted Artifact',
    )
    graph = build_tmdb_legal_graph(candidates)
    draft = BgmToTmdbMappingDraft(mappings=mappings)
    verified_plan, result = verify_and_compile_bgm_to_tmdb_plan(bridge_input, graph, draft)
    assert result.passed is True
    assert verified_plan is not None
    return bridge_input, graph, verified_plan


def _assignment(
    source_path: str,
    *,
    bangumi_subject_id: int = 100,
    episode_id: int = 101,
    media_kind: str = 'tv',
    episode_type: str = 'regular',
    sort: int | None = 1,
    ep: int | None = 1,
    target_span: CompiledTargetSpan | None = None,
) -> CompiledOrganizeAssignment:
    return CompiledOrganizeAssignment(
        source_path=source_path,
        disposition='map_to_bangumi',
        target=CompiledTarget(
            bangumi_subject_id=bangumi_subject_id,
            media_kind=media_kind,
            episode_id=episode_id,
            episode_type=episode_type,
            sort=sort,
            ep=ep,
            title=f'Bangumi {ep or sort or 1}',
        ),
        target_span=target_span or CompiledTargetSpan(),
        reason='accepted BGM mapping',
    )


def _tv_node(
    tmdb_id: int,
    season_number: int,
    episode_number: int,
    *,
    episode_type: str = 'regular',
    title: str = '',
) -> TmdbLegalNode:
    return TmdbLegalNode(
        legal_node_id=tv_legal_node_id(tmdb_id, season_number, episode_number),
        media_type='tv',
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
        episode_type=episode_type,
        title=title,
    )


def _issue_codes(result: object) -> set[str]:
    return {issue.issue_code for issue in getattr(result, 'issues', [])}
