from __future__ import annotations

import json

from src.rename.bgm_to_tmdb import (
    BgmToTmdbBridgeToolState,
    BgmToTmdbMapping,
    BgmToTmdbMappingDraft,
    BgmToTmdbRecipeParams,
    TmdbLegalNode,
    build_tmdb_legal_graph_from_payloads,
    build_tmdb_legal_graph,
    build_tmdb_tv_candidate_card,
    compile_and_verify_bgm_to_tmdb_recipe_params,
    compile_bgm_to_tmdb_input,
    extract_accepted_compiled_plan_payload,
    iter_accepted_compiled_plan_artifacts,
    load_accepted_compiled_plan_artifact,
    movie_legal_node_id,
    run_bgm_to_tmdb_bridge_dry_run,
    tv_legal_node_id,
    verify_and_compile_bgm_to_tmdb_plan,
    verify_bgm_to_tmdb_draft,
)
from src.rename.case_agent.recipe import (
    CompiledOrganizeAssignment,
    CompiledOrganizePlan,
    CompiledTarget,
    CompiledTargetSpan,
)


def test_tv_bridge_accepts_id_plus_semantic_title_cards() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('Space Battleship Yamato 2199/Episode 01.mkv', sort=1, ep=1),
            ]
        ),
        source_path='Space Battleship Yamato 2199',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 45844,
            'display_title': 'Star Blazers [Space Battleship Yamato] 2199',
            'original_name': '宇宙戦艦ヤマト２１９９',
            'slug': '45844-space-battleship-yamato-2199',
            'year': 2013,
            'aliases': ['Space Battleship Yamato 2199'],
            'legal_nodes': [
                _tv_node(45844, 1, 1, title='Messenger of Iscandar'),
            ],
        }
    ])
    draft = BgmToTmdbMappingDraft(
        summary='semantic title card chooses TMDB id 45844',
        mappings=[
            BgmToTmdbMapping(
                source_path='Space Battleship Yamato 2199/Episode 01.mkv',
                tmdb_legal_node_ids=[tv_legal_node_id(45844, 1, 1)],
                confidence='High',
                reason='title/original_name/slug evidence matches',
            )
        ],
    )

    plan, result = verify_and_compile_bgm_to_tmdb_plan(bridge_input, graph, draft)

    assert result.passed is True
    assert plan is not None
    assert plan.tmdb_target_count == 1
    assert graph.candidates[0].tmdb_ref == 'tv:45844'
    assert graph.candidates[0].slug == '45844-space-battleship-yamato-2199'


def test_movie_bridge_accepts_movie_legal_node() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment(
                    'Movie.mkv',
                    media_kind='movie',
                    episode_type='movie',
                    episode_id=0,
                    sort=None,
                    ep=None,
                ),
            ]
        ),
        source_path='Movie',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'movie',
            'tmdb_id': 1234,
            'display_title': 'The Movie',
            'original_title': '映画',
            'slug': '1234-the-movie',
            'legal_nodes': [
                TmdbLegalNode(
                    legal_node_id=movie_legal_node_id(1234),
                    media_type='movie',
                    tmdb_id=1234,
                    title='The Movie',
                ),
            ],
        }
    ])
    draft = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(
                source_path='Movie.mkv',
                tmdb_legal_node_ids=[movie_legal_node_id(1234)],
                reason='movie candidate id matches',
            )
        ],
    )

    plan, result = verify_and_compile_bgm_to_tmdb_plan(bridge_input, graph, draft)

    assert result.passed is True
    assert plan is not None
    assert plan.mappings[0].tmdb_legal_node_ids == ['movie:1234']


def test_special_maps_to_season_zero_and_supplemental_stays_unmapped() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('SP01.mkv', episode_type='special', sort=1, ep=1),
                CompiledOrganizeAssignment(
                    source_path='Bonus Interview.mkv',
                    disposition='non_bangumi_or_supplemental',
                    reason='package extra',
                ),
            ]
        ),
        source_path='Specials',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 222,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(222, 0, 1, episode_type='special')],
        }
    ])
    draft = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(
                source_path='SP01.mkv',
                tmdb_legal_node_ids=['tv:222:S00E01'],
                reason='numbered special maps to exposed season 0 node',
            ),
            BgmToTmdbMapping(
                source_path='Bonus Interview.mkv',
                disposition='unmapped_supplemental',
                tmdb_legal_node_ids=[],
                reason='compiled plan says supplemental',
            ),
        ]
    )

    assert verify_bgm_to_tmdb_draft(bridge_input, graph, draft).passed is True


def test_mapped_bgm_target_absent_is_accepted_without_tmdb_node() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('E01.mkv', sort=1, ep=1),
                _assignment('SP01.mkv', episode_id=201, episode_type='special', sort=1, ep=1),
            ]
        ),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 1, 1)],
        }
    ])
    draft = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(source_path='E01.mkv', tmdb_legal_node_ids=['tv:42:S01E01']),
            BgmToTmdbMapping(
                source_path='SP01.mkv',
                disposition='tmdb_target_absent',
                tmdb_legal_node_ids=[],
                confidence='High',
                reason='TMDB season 0 and episode-title checks expose no legal node for this BGM special.',
            ),
        ],
    )

    plan, result = verify_and_compile_bgm_to_tmdb_plan(bridge_input, graph, draft)

    assert result.passed is True
    assert plan is not None
    assert plan.tmdb_target_count == 1
    assert plan.tmdb_absent_count == 1


def test_span_bridge_requires_explicit_existing_tmdb_nodes() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment(
                    'Merged OVA.mkv',
                    target_span=CompiledTargetSpan(
                        bangumi_subject_id=100,
                        media_kind='ova',
                        episode_ids=[11, 12],
                        sort_start=1,
                        sort_end=2,
                        episode_type='regular',
                    ),
                ),
            ]
        ),
        source_path='Merged',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 555,
            'display_title': 'OVA Collection',
            'legal_nodes': [
                _tv_node(555, 1, 1),
                _tv_node(555, 1, 2),
            ],
        }
    ])
    accepted = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(
                source_path='Merged OVA.mkv',
                tmdb_legal_node_ids=['tv:555:S01E01', 'tv:555:S01E02'],
            )
        ],
    )
    missing_one = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(
                source_path='Merged OVA.mkv',
                tmdb_legal_node_ids=['tv:555:S01E01'],
            )
        ],
    )

    assert verify_bgm_to_tmdb_draft(bridge_input, graph, accepted).passed is True
    result = verify_bgm_to_tmdb_draft(bridge_input, graph, missing_one)
    assert result.passed is False
    assert _issue_codes(result) == {'tmdb_target_count_mismatch'}


def test_span_bridge_can_map_to_one_tmdb_movie_node() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment(
                    'Three Part Movie.mkv',
                    media_kind='movie',
                    episode_type='regular',
                    target_span=CompiledTargetSpan(
                        bangumi_subject_id=100,
                        media_kind='movie',
                        episode_ids=[101, 102, 103],
                        sort_start=1,
                        sort_end=3,
                        episode_type='regular',
                    ),
                ),
            ]
        ),
        source_path='Three Part Movie',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'movie',
            'tmdb_id': 655431,
            'display_title': 'PSYCHO-PASS 3 FIRST INSPECTOR',
            'legal_nodes': [TmdbLegalNode(legal_node_id='movie:655431', media_type='movie', tmdb_id=655431)],
        }
    ])
    draft = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(
                source_path='Three Part Movie.mkv',
                tmdb_legal_node_ids=['movie:655431'],
                reason='TMDB models the three-part BGM span as one movie node.',
            )
        ],
    )

    plan, result = verify_and_compile_bgm_to_tmdb_plan(bridge_input, graph, draft)

    assert result.passed is True
    assert plan is not None
    assert plan.tmdb_target_count == 1


def test_rejects_unknown_duplicate_and_bare_tmdb_nodes() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('E01.mkv', sort=1, ep=1),
                _assignment('E02.mkv', episode_id=102, sort=2, ep=2),
                _assignment('E03.mkv', episode_id=103, sort=3, ep=3),
            ]
        ),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 333,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(333, 1, 1)],
        }
    ])
    draft = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(source_path='E01.mkv', tmdb_legal_node_ids=['tv:333:S01E01']),
            BgmToTmdbMapping(source_path='E02.mkv', tmdb_legal_node_ids=['tv:333:S01E01']),
            BgmToTmdbMapping(source_path='E03.mkv', tmdb_legal_node_ids=['tmdb:S01E03']),
        ],
    )

    result = verify_bgm_to_tmdb_draft(bridge_input, graph, draft)

    assert result.passed is False
    assert {'duplicate_tmdb_target', 'bare_tmdb_node_not_allowed'} <= _issue_codes(result)


def test_rejects_title_targets_unknown_source_and_missing_source() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('E01.mkv', sort=1, ep=1),
                _assignment('E02.mkv', episode_id=102, sort=2, ep=2),
            ]
        ),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 777,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(777, 1, 1)],
        }
    ])
    draft = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(
                source_path='E01.mkv',
                tmdb_legal_node_ids=['Star Blazers [Space Battleship Yamato] 2199'],
            ),
            BgmToTmdbMapping(source_path='Not In Plan.mkv', tmdb_legal_node_ids=['tv:777:S01E01']),
        ]
    )

    result = verify_bgm_to_tmdb_draft(bridge_input, graph, draft)

    assert result.passed is False
    assert {
        'unknown_tmdb_legal_node',
        'unknown_source_path',
        'missing_source_mapping',
    } <= _issue_codes(result)


def test_rejects_supplemental_mapped_to_tmdb() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                CompiledOrganizeAssignment(
                    source_path='Bonus.mkv',
                    disposition='non_bangumi_or_supplemental',
                ),
            ]
        ),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 888,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(888, 1, 1)],
        }
    ])
    draft = BgmToTmdbMappingDraft(
        mappings=[
            BgmToTmdbMapping(source_path='Bonus.mkv', tmdb_legal_node_ids=['tv:888:S01E01'])
        ]
    )

    result = verify_bgm_to_tmdb_draft(bridge_input, graph, draft)

    assert result.passed is False
    assert 'supplemental_mapped_to_tmdb' in _issue_codes(result)


def test_graph_builder_hydrates_tmdb_payload_semantics_and_nodes() -> None:
    tv_payload = {
        'id': 45844,
        'name': 'Star Blazers [Space Battleship Yamato] 2199',
        'original_name': '宇宙戦艦ヤマト２１９９',
        'first_air_date': '2013-04-07',
        'overview': 'A remake of Space Battleship Yamato.',
        '_metadata_alias_titles': ['Space Battleship Yamato 2199'],
        'seasons': [
            {
                'season_number': 1,
                'name': 'Season 1',
                'episode_count': 2,
                'air_date': '2013-04-07',
                'episodes': [
                    {'episode_number': 1, 'name': 'Messenger of Iscandar', 'episode_type': 'regular'},
                    {'episode_number': 2, 'name': 'Toward a Sea of Stars', 'episode_type': 'regular'},
                ],
            }
        ],
    }
    candidate = build_tmdb_tv_candidate_card(tv_payload, slug='45844-space-battleship-yamato-2199')
    graph = build_tmdb_legal_graph_from_payloads(tv_payloads=[tv_payload])

    assert candidate.tmdb_ref == 'tv:45844'
    assert candidate.web_url == 'https://www.themoviedb.org/tv/45844-space-battleship-yamato-2199'
    assert candidate.legal_nodes[0].legal_node_id == 'tv:45844:S01E01'
    assert graph.legal_node_map()['tv:45844:S01E02'].title == 'Toward a Sea of Stars'


def test_tool_state_validates_submits_and_writes_artifacts(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 1, 1)],
        }
    ])
    state = BgmToTmdbBridgeToolState(bridge_input=bridge_input, legal_graph=graph, run_dir=tmp_path)
    draft = {
        'mappings': [
            {
                'source_path': 'E01.mkv',
                'disposition': 'map_to_tmdb',
                'tmdb_legal_node_ids': ['tv:42:S01E01'],
                'reason': 'exposed legal node',
            }
        ]
    }

    context = state.handle_tool('get_bgm_to_tmdb_bridge_context', {'detail': True})
    validated = state.handle_tool('validate_bgm_to_tmdb_bridge', {'bridge_draft': draft})
    submitted = state.handle_tool('submit_bgm_to_tmdb_bridge', {'bridge_draft': draft})

    assert context['data']['bridge_contract']['dry_run_only'] is True
    assert validated['accepted'] is True
    assert submitted['accepted'] is True
    assert (tmp_path / 'artifacts' / 'bgm_to_tmdb_bridge_draft.json').exists()
    assert (tmp_path / 'artifacts' / 'bgm_to_tmdb_verified_plan.json').exists()
    assert (tmp_path / 'final_result.json').exists()
    assert state.tool_summary()['tool_call_counts']['submit_bgm_to_tmdb_bridge'] == 1
    assert context['data']['bridge_contract']['final_tools'] == [
        'validate_bgm_to_tmdb_bridge_recipe_params',
        'submit_bgm_to_tmdb_bridge_recipe_params',
        'fail_closed',
    ]


def test_recipe_sequence_compiles_bgm_sort_range_to_tmdb_episode_nodes() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('E01.mkv', sort=1, ep=1),
                _assignment('E02.mkv', episode_id=102, sort=2, ep=2),
                _assignment('E03.mkv', episode_id=103, sort=3, ep=3),
            ]
        ),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 1, index) for index in range(1, 4)],
        }
    ])
    params = BgmToTmdbRecipeParams.model_validate({
        'summary': 'recipe maps one BGM regular sequence to TMDB season 1',
        'rules': [
            {
                'name': 'main',
                'rule_type': 'episode_sequence',
                'select_bgm': {'bangumi_subject_id': 100, 'episode_type': 'regular', 'sort_range': '1-3'},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1-3', 'number_field': 'sort'},
                'confidence': 'High',
                'reason': 'title and season card evidence match',
            }
        ],
    })

    result = compile_and_verify_bgm_to_tmdb_recipe_params(bridge_input, graph, params)

    assert result.accepted is True
    assert result.verifier_result.passed is True
    assert [mapping.tmdb_legal_node_ids[0] for mapping in result.bridge_draft.mappings] == [
        'tv:42:S01E01',
        'tv:42:S01E02',
        'tv:42:S01E03',
    ]
    assert result.rule_match_counts == {'main': 3}


def test_recipe_sequence_supports_tmdb_season_offset() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('S2E01.mkv', sort=1, ep=1),
                _assignment('S2E02.mkv', episode_id=102, sort=2, ep=2),
            ]
        ),
        source_path='Show Season 2',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 2, index) for index in range(1, 3)],
        }
    ])
    params = BgmToTmdbRecipeParams.model_validate({
        'rules': [
            {
                'name': 'season_2',
                'rule_type': 'episode_sequence',
                'select_bgm': {'bangumi_subject_id': 100, 'sort_range': '1-2'},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 2, 'episode_offset': 'EP', 'number_field': 'sort'},
                'confidence': 'High',
                'reason': 'Bangumi subject corresponds to TMDB season 2',
            }
        ],
    })

    result = compile_and_verify_bgm_to_tmdb_recipe_params(bridge_input, graph, params)

    assert result.accepted is True
    assert [mapping.tmdb_legal_node_ids[0] for mapping in result.bridge_draft.mappings] == [
        'tv:42:S02E01',
        'tv:42:S02E02',
    ]


def test_recipe_movie_special_span_and_supplemental_rules_compile() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('Movie.mkv', bangumi_subject_id=200, media_kind='movie', episode_type='movie', sort=None, ep=None),
                _assignment('SP01.mkv', bangumi_subject_id=300, media_kind='tv', episode_type='special', sort=1, ep=1),
                _assignment(
                    'Merged.mkv',
                    bangumi_subject_id=400,
                    target_span=CompiledTargetSpan(
                        bangumi_subject_id=400,
                        media_kind='ova',
                        episode_ids=[401, 402],
                        sort_start=1,
                        sort_end=2,
                        episode_type='regular',
                    ),
                ),
                _assignment('Missing SP.mkv', bangumi_subject_id=500, media_kind='tv', episode_type='special', sort=1, ep=1),
                CompiledOrganizeAssignment(
                    source_path='Bonus.mkv',
                    disposition='non_bangumi_or_supplemental',
                    reason='package extra',
                ),
            ]
        ),
        source_path='Mixed',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'movie',
            'tmdb_id': 900,
            'display_title': 'Movie',
            'legal_nodes': [TmdbLegalNode(legal_node_id='movie:900', media_type='movie', tmdb_id=900)],
        },
        {
            'media_type': 'tv',
            'tmdb_id': 901,
            'display_title': 'Specials',
            'legal_nodes': [_tv_node(901, 0, 1, episode_type='special')],
        },
        {
            'media_type': 'tv',
            'tmdb_id': 902,
            'display_title': 'OVA',
            'legal_nodes': [_tv_node(902, 1, 1), _tv_node(902, 1, 2)],
        },
    ])
    params = BgmToTmdbRecipeParams.model_validate({
        'rules': [
            {
                'name': 'movie',
                'rule_type': 'movie',
                'select_bgm': {'bangumi_subject_id': 200, 'media_kind': 'movie'},
                'target_tmdb': {'tmdb_ref': 'movie:900'},
                'confidence': 'High',
                'reason': 'movie ID matches',
            },
            {
                'name': 'special',
                'rule_type': 'special_sequence',
                'select_bgm': {'bangumi_subject_id': 300, 'episode_type': 'special'},
                'target_tmdb': {'tmdb_ref': 'tv:901', 'episode_range': '1'},
                'confidence': 'High',
                'reason': 'special maps to TMDB season 0',
            },
            {
                'name': 'span',
                'rule_type': 'span',
                'select_bgm': {'bangumi_subject_id': 400, 'source_paths': ['Merged.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:902', 'season_number': 1, 'episode_range': '1-2'},
                'confidence': 'High',
                'reason': 'one BGM span covers two TMDB OVA episodes',
            },
            {
                'name': 'missing_tmdb_special',
                'rule_type': 'tmdb_absent_group',
                'select_bgm': {'bangumi_subject_id': 500, 'episode_type': 'special'},
                'confidence': 'High',
                'reason': 'hydrated TMDB graph exposes no season 0 legal node matching this BGM special title',
            },
            {
                'name': 'extras',
                'rule_type': 'supplemental_group',
                'select_bgm': {},
                'confidence': 'Medium',
                'reason': 'compiled plan marks extras supplemental',
            },
        ],
    })

    result = compile_and_verify_bgm_to_tmdb_recipe_params(bridge_input, graph, params)

    by_source = {mapping.source_path: mapping for mapping in result.bridge_draft.mappings}
    assert result.accepted is True
    assert by_source['Movie.mkv'].tmdb_legal_node_ids == ['movie:900']
    assert by_source['SP01.mkv'].tmdb_legal_node_ids == ['tv:901:S00E01']
    assert by_source['Merged.mkv'].tmdb_legal_node_ids == ['tv:902:S01E01', 'tv:902:S01E02']
    assert by_source['Missing SP.mkv'].disposition == 'tmdb_target_absent'
    assert result.verifier_result.passed is True
    plan, _ = verify_and_compile_bgm_to_tmdb_plan(bridge_input, graph, result.bridge_draft)
    assert plan is not None
    assert plan.tmdb_absent_count == 1
    assert by_source['Bonus.mkv'].disposition == 'unmapped_supplemental'


def test_recipe_rejects_overlap_uncovered_unknown_and_missing_graph() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('E01.mkv', sort=1, ep=1),
                _assignment('E02.mkv', episode_id=102, sort=2, ep=2),
            ]
        ),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 1, 1)],
        }
    ])
    params = BgmToTmdbRecipeParams.model_validate({
        'rules': [
            {
                'name': 'known',
                'rule_type': 'episode_sequence',
                'select_bgm': {'source_paths': ['E01.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'High',
                'reason': 'known node',
            },
            {
                'name': 'overlap_unknown',
                'rule_type': 'episode_sequence',
                'select_bgm': {'source_paths': ['E01.mkv', 'Missing.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '99'},
                'confidence': 'High',
                'reason': 'bad node',
            },
        ],
    })

    result = compile_and_verify_bgm_to_tmdb_recipe_params(bridge_input, graph, params)
    codes = _issue_codes(result.verifier_result)

    assert result.accepted is False
    assert {
        'overlapping_bgm_rules',
        'uncovered_bgm_assignment',
        'unknown_bgm_source_path',
        'unknown_tmdb_legal_node',
    } <= codes


def test_recipe_rejects_zero_match_count_mismatch_duplicate_and_supplemental_mapping() -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(
            assignments=[
                _assignment('E01.mkv', sort=1, ep=1),
                _assignment('E02.mkv', episode_id=102, sort=2, ep=2),
                CompiledOrganizeAssignment(
                    source_path='Bonus.mkv',
                    disposition='non_bangumi_or_supplemental',
                    reason='package extra',
                ),
            ]
        ),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 1, 1), _tv_node(42, 1, 2)],
        }
    ])
    params = BgmToTmdbRecipeParams.model_validate({
        'rules': [
            {
                'name': 'duplicate_a',
                'rule_type': 'episode_sequence',
                'select_bgm': {'source_paths': ['E01.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'High',
                'reason': 'first duplicate target',
            },
            {
                'name': 'duplicate_b',
                'rule_type': 'episode_sequence',
                'select_bgm': {'source_paths': ['E02.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'High',
                'reason': 'second duplicate target',
            },
            {
                'name': 'bad_supplemental',
                'rule_type': 'episode_sequence',
                'select_bgm': {'source_paths': ['Bonus.mkv']},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '2'},
                'confidence': 'High',
                'reason': 'supplemental must not map',
            },
            {
                'name': 'unknown_subject',
                'rule_type': 'episode_sequence',
                'select_bgm': {'bangumi_subject_id': 999999},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1-2'},
                'confidence': 'High',
                'reason': 'unknown subject selector',
            },
            {
                'name': 'bad_absent_supplemental',
                'rule_type': 'tmdb_absent_group',
                'select_bgm': {'source_paths': ['Bonus.mkv']},
                'confidence': 'High',
                'reason': 'supplemental files must not use BGM TMDB-absent outcome',
            },
        ],
    })

    result = compile_and_verify_bgm_to_tmdb_recipe_params(bridge_input, graph, params)
    codes = _issue_codes(result.verifier_result)

    assert result.accepted is False
    assert {
        'duplicate_tmdb_target',
        'mapped_rule_selected_supplemental_assignment',
        'supplemental_mapped_to_tmdb',
        'tmdb_absent_rule_selected_supplemental_assignment',
        'zero_bgm_assignment_match',
        'tmdb_episode_range_count_mismatch',
    } <= codes


def test_validate_recipe_params_hydrates_declared_tmdb_ref_and_rejects_json_string(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        tmdb_search=_FakeTmdbSearch(),
    )
    params = {
        'rules': [
            {
                'name': 'hydrated',
                'rule_type': 'episode_sequence',
                'select_bgm': {'bangumi_subject_id': 100},
                'target_tmdb': {'tmdb_ref': 'tv:45844', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'High',
                'reason': 'validate hydrates tv:45844 and exposes the legal node',
            }
        ],
    }

    result = state.handle_tool(
        'validate_bgm_to_tmdb_bridge_recipe_params',
        {'recipe_params': json.dumps(params)},
    )

    assert result['accepted'] is False
    assert result['status'] == 'invalid'
    assert result['error'] == 'recipe_params must be a canonical JSON object'
    assert 'tmdb_hydration' not in result


def test_validate_recipe_params_hydrates_declared_tmdb_ref_and_accepts_canonical_dict(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        tmdb_search=_FakeTmdbSearch(),
    )
    params = {
        'rules': [
            {
                'name': 'hydrated',
                'rule_type': 'episode_sequence',
                'select_bgm': {'bangumi_subject_id': 100},
                'target_tmdb': {'tmdb_ref': 'tv:45844', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'High',
                'reason': 'validate hydrates tv:45844 and exposes the legal node',
            }
        ],
    }

    result = state.handle_tool(
        'validate_bgm_to_tmdb_bridge_recipe_params',
        {'recipe_params': params},
    )

    assert result['accepted'] is True
    assert result['tmdb_hydration']['candidate_count'] == 1
    assert state.legal_graph.legal_node_map()['tv:45844:S01E01'].title == 'Messenger of Iscandar'


def test_raw_bridge_tools_reject_string_payloads(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
    )

    result = state.handle_tool(
        'validate_bgm_to_tmdb_bridge',
        {
            'bridge_draft': json.dumps({
                'mappings': [
                    {
                        'source_path': 'E01.mkv',
                        'disposition': 'map_to_tmdb',
                        'tmdb_legal_node_ids': ['tv:42:S01E01'],
                        'reason': 'exposed legal node',
                    }
                ]
            }),
        },
    )

    assert result['accepted'] is False
    assert result['status'] == 'invalid'
    assert result['error'] == 'bridge_draft must be a canonical JSON object'


def test_recipe_review_warning_blocks_submit_until_evidence_is_added(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 1, 1)],
        }
    ])
    state = BgmToTmdbBridgeToolState(bridge_input=bridge_input, legal_graph=graph, run_dir=tmp_path)
    params = {
        'rules': [
            {
                'name': 'weak',
                'rule_type': 'episode_sequence',
                'select_bgm': {'bangumi_subject_id': 100},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'Low',
            }
        ],
    }

    validated = state.handle_tool('validate_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': params})
    submitted = state.handle_tool('submit_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': params})

    assert validated['status'] == 'review'
    assert validated['accepted'] is False
    assert submitted['accepted'] is False
    assert submitted['status'] == 'review'
    assert not (tmp_path / 'final_result.json').exists()


def test_tool_state_recipe_params_validate_submit_and_context_cards(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 1, 1)],
        }
    ])
    state = BgmToTmdbBridgeToolState(bridge_input=bridge_input, legal_graph=graph, run_dir=tmp_path)
    params = {
        'summary': 'recipe params submit',
        'rules': [
            {
                'name': 'main',
                'rule_type': 'episode_sequence',
                'select_bgm': {'bangumi_subject_id': 100, 'sort_range': '1'},
                'target_tmdb': {'tmdb_ref': 'tv:42', 'season_number': 1, 'episode_range': '1'},
                'confidence': 'High',
                'reason': 'title and season evidence match',
            }
        ],
    }

    context = state.handle_tool('get_bgm_to_tmdb_bridge_context', {'detail': True})
    validated = state.handle_tool('validate_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': params})
    submitted = state.handle_tool('submit_bgm_to_tmdb_bridge_recipe_params', {'recipe_params': params})

    assert context['data']['bridge_contract']['primary_workflow'].startswith('Use recipe params')
    assert 'episode_title_cards_sample' in context['data']['bangumi_subject_cards'][0]
    assert context['data']['bangumi_subject_cards'][0]['episode_title_cards_sample'][0]['title'] == 'Bangumi 1'
    assert 'episode_title_policy' in context['data']['bridge_contract']
    assert context['data']['bangumi_subject_cards'][0]['sort_range'] == '1'
    assert validated['accepted'] is True
    assert submitted['accepted'] is True
    assert submitted['verified_plan']['tmdb_target_count'] == 1
    assert submitted['verified_plan']['tmdb_absent_count'] == 0
    assert (tmp_path / 'artifacts' / 'bgm_to_tmdb_recipe_params.json').exists()
    assert state.tool_summary()['tool_call_counts']['submit_bgm_to_tmdb_bridge_recipe_params'] == 1


def test_tool_state_searches_and_hydrates_tmdb_graph_with_fake_search(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        tmdb_search=_FakeTmdbSearch(),
    )

    searched = state.handle_tool('search_tmdb_candidates', {'query': 'Space Battleship Yamato 2199'})
    hydrated = state.handle_tool('get_tmdb_legal_graph', {'tmdb_refs': ['tv:45844', 'movie:1234']})

    assert searched['candidates'][0]['tmdb_ref'] == 'tv:45844'
    assert hydrated['ok'] is True
    assert set(hydrated['tmdb_legal_graph']['candidates'][0]['season_cards'][0]['legal_node_ids']) == {
        'tv:45844:S01E01',
        'tv:45844:S01E02',
    }
    assert state.legal_graph.legal_node_map()['movie:1234'].title == 'The Movie'


def test_tool_state_hydrates_one_bgm_aligned_tmdb_episode_title_view(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[
            _assignment(
                'SPs/Kyoukai Senjou no Horizon - \u6975\u6771\u306a\u308b\u307b\u3069\u8b1b\u5ea7 [01].mkv',
                episode_type='special',
                sort=1,
                ep=1,
                title='\u6781\u4e1c\u539f\u6765\u5982\u6b64\u8bb2\u5ea7\u5176\u2460',
            )
        ]),
        source_path='Kyoukai Senjou no Horizon',
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        tmdb_search=_LanguageAlignedFakeTmdbSearch(),
    )

    hydrated = state.handle_tool('get_tmdb_legal_graph', {'tmdb_refs': ['tv:57528']})

    assert hydrated['ok'] is True
    node = state.legal_graph.legal_node_map()['tv:57528:S00E05']
    assert '\u6975\u6771' in node.title
    assert '\u8b1b\u5ea7' in node.title
    assert node.title != '\u8001\u5e08\u7684\u6559\u8bad 1'


def test_tool_state_search_guidance_warns_without_blocking_broad_search(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        tmdb_search=_FakeTmdbSearch(),
        search_guidance_soft_limit=1,
    )

    first = state.handle_tool('search_tmdb_candidates', {'query': 'Show'})
    second = state.handle_tool('search_tmdb_candidates', {'query': 'Show recap'})
    third = state.handle_tool('search_tmdb_candidates', {'query': 'Show recap summary'})

    assert first['ok'] is True
    assert 'hydration as the next evidence layer' in first['search_strategy_hints'][0]
    assert first['search_guidance_warning'].startswith('Search count')
    assert second['search_guidance']['used'] == 2
    assert third['ok'] is True
    assert third['search_guidance']['used'] == 3
    assert 'validate_bgm_to_tmdb_bridge_recipe_params' in third['repair_hints'][1]


def test_tool_state_fail_closed_is_final_result(tmp_path) -> None:
    bridge_input = compile_bgm_to_tmdb_input(
        CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)]),
        source_path='Show',
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=bridge_input,
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        tmdb_search=_FakeTmdbSearch(),
    )

    result = state.handle_tool(
        'fail_closed',
        {
            'reason': 'TMDB candidates contradict BGM subject evidence',
            'reason_kind': 'contradiction',
            'related_refs': ['tv:45844'],
        },
    )

    assert result['status'] == 'fail_closed'
    assert json.loads((tmp_path / 'final_result.json').read_text(encoding='utf-8'))['reason_kind'] == 'contradiction'


def test_dry_run_payload_is_snapshot_safe_and_non_mutating() -> None:
    compiled_plan = CompiledOrganizePlan(assignments=[_assignment('E01.mkv', sort=1, ep=1)])
    graph = build_tmdb_legal_graph([
        {
            'media_type': 'tv',
            'tmdb_id': 42,
            'display_title': 'Show',
            'legal_nodes': [_tv_node(42, 1, 1)],
        }
    ])
    payload = run_bgm_to_tmdb_bridge_dry_run(
        compiled_plan=compiled_plan,
        legal_graph=graph,
        bridge_draft={
            'mappings': [
                {
                    'source_path': 'E01.mkv',
                    'tmdb_legal_node_ids': ['tv:42:S01E01'],
                }
            ]
        },
        source_path='Show',
        write_snapshot=False,
    )

    assert payload['ok'] is True
    assert payload['dry_run'] is True
    assert payload['file_mutation_allowed'] is False
    assert payload['verified_plan']['tmdb_target_count'] == 1


def test_accepted_sample_artifact_can_feed_bgm_to_tmdb_without_rerun(tmp_path) -> None:
    artifact = {
        'ok': True,
        'status': 'accepted',
        'snapshot': {
            'status': 'accepted',
            'accepted_contract_ok': True,
            'compiled_plan': CompiledOrganizePlan(
                assignments=[_assignment('Accepted.mkv', media_kind='movie')]
            ).model_dump(mode='json'),
        },
    }
    artifact_path = tmp_path / 'sample.json'
    artifact_path.write_text(json.dumps(artifact), encoding='utf-8')

    plan = load_accepted_compiled_plan_artifact(artifact_path)

    assert extract_accepted_compiled_plan_payload(artifact)['assignments'][0]['source_path'] == 'Accepted.mkv'
    assert plan.assignments[0].source_path == 'Accepted.mkv'
    assert iter_accepted_compiled_plan_artifacts(tmp_path) == [artifact_path]


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
    title: str | None = None,
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
            title=title or f'Bangumi {ep or sort or 1}',
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


class _FakeTmdbSearch:
    def search_multi_by_query(self, query: str, limit: int = 20):
        return [
            {
                'id': 45844,
                'media_type': 'tv',
                'name': 'Star Blazers [Space Battleship Yamato] 2199',
                'original_name': '宇宙戦艦ヤマト2199',
                'first_air_date': '2013-04-07',
                'overview': 'A remake of Space Battleship Yamato.',
            }
        ][:limit]

    def search_tv_by_query(self, query: str, year: int | None = None, limit: int = 5):
        return self.search_multi_by_query(query, limit)

    def search_movies_by_title(self, title: str, year: int | None = None, limit: int = 5):
        return [
            {
                'id': 1234,
                'media_type': 'movie',
                'title': 'The Movie',
                'original_title': '映画',
                'release_date': '2020-01-01',
            }
        ][:limit]

    def get_tv_info_by_id(self, tmdb_id: int):
        assert tmdb_id == 45844
        return {
            'id': 45844,
            'name': 'Star Blazers [Space Battleship Yamato] 2199',
            'original_name': '宇宙戦艦ヤマト2199',
            'first_air_date': '2013-04-07',
            'seasons': [
                {
                    'season_number': 1,
                    'name': 'Season 1',
                    'episode_count': 2,
                    'episodes': [
                        {'episode_number': 1, 'name': 'Messenger of Iscandar'},
                        {'episode_number': 2, 'name': 'Toward a Sea of Stars'},
                    ],
                }
            ],
        }

    def fill_season_info(self, tv_info):
        return tv_info

    def enrich_tv_alias_metadata(self, tv_info):
        return {**tv_info, '_metadata_alias_titles': ['Space Battleship Yamato 2199']}

    def get_movie_info_by_id(self, tmdb_id: int):
        assert tmdb_id == 1234
        return {
            'id': 1234,
            'title': 'The Movie',
            'original_title': '映画',
            'release_date': '2020-01-01',
            'runtime': 90,
        }

    def _tmdb_movie_alternative_titles(self, tmdb_id: int):
        return {}

    def _tmdb_movie_translations(self, tmdb_id: int):
        return {}


class _LanguageAlignedFakeTmdbSearch:
    def get_tv_info_by_id(self, tmdb_id: int):
        assert tmdb_id == 57528
        return self._tmdb_tv_info(tmdb_id, language='zh-CN')

    def _tmdb_tv_info(self, tmdb_id: int, *, language: str = 'zh-CN'):
        assert tmdb_id == 57528
        return {
            'id': 57528,
            'name': 'Kyoukai Senjou no Horizon',
            'original_name': '\u5883\u754c\u7dda\u4e0a\u306e\u30db\u30e9\u30a4\u30be\u30f3',
            'first_air_date': '2011-10-02',
            'seasons': [
                {'season_number': 0, 'name': 'Specials', 'episode_count': 1},
            ],
        }

    def _tmdb_season_info(self, tmdb_id: int, season_number: int, *, language: str = 'zh-CN'):
        assert tmdb_id == 57528
        assert season_number == 0
        names = {
            'zh-CN': '\u8001\u5e08\u7684\u6559\u8bad 1',
            'zh-TW': '\u300e\u5883\u754c\u7dda\u4e0a\u7684\u5730\u5e73\u7dda \u6975\u6771 \u539f\u4f86\u5982\u6b64\u8b1b\u5ea7\u300f\u5176\u2460',
            'ja-JP': '\u300e\u5883\u754c\u7dda\u4e0a\u306e\u30db\u30e9\u30a4\u30be\u30f3 \u6975\u6771\u306a\u308b\u307b\u3069\u8b1b\u5ea7\u300f \u5176\u306e\u2460',
            'en-US': "Sensei's Lesson 1",
        }
        return {
            'season_number': 0,
            'name': 'Specials',
            'episode_count': 1,
            'episodes': [
                {
                    'episode_number': 5,
                    'name': names.get(language, names['zh-CN']),
                    'episode_type': 'special',
                }
            ],
        }

    def enrich_tv_alias_metadata(self, tv_info):
        return tv_info
