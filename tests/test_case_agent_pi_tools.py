from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.rename.case_agent.local_bangumi_entry import _build_workspace
from src.rename.case_agent.models import BangumiItemCard, CaseBudget, CaseContract, CaseHeader, LocalFileCard
from src.rename.case_agent.pi_tools import PiCaseToolState
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


REPO_ROOT = Path(__file__).resolve().parents[1]


class _File:
    def __init__(self, file_id: str, name: str, relative_path: str):
        self.file_id = file_id
        self.name = name
        self.relative_path = relative_path
        self.is_main_video_candidate = True
        self.is_video = True
        self.suffix = '.mkv'


class _BangumiClient:
    def search_subjects(self, query, _subject_type):
        assert query
        return [SimpleNamespace(id=200, type=2, name='Searched', name_cn='Search Result', eps=2)]

    def get_subject(self, subject_id):
        subject_type = 1 if subject_id in {301, 302} else 2
        return SimpleNamespace(id=subject_id, type=subject_type, name=f'Subject {subject_id}', eps=2)

    def get_related_subjects(self, subject_id):
        assert subject_id
        return [
            SimpleNamespace(id=201, type=2, relation='side_story'),
            SimpleNamespace(id=301, type=1, relation='manga'),
            SimpleNamespace(id=302, type=1, relation='side_story'),
        ]

    def get_episodes(self, subject_id):
        return [
            SimpleNamespace(id=subject_id * 10 + 1, sort=1, ep=1, type=0, name='Episode 1'),
            SimpleNamespace(id=subject_id * 10 + 2, sort=2, ep=2, type=0, name='Episode 2'),
        ]


class _OvaBangumiClient:
    def search_subjects(self, query, _subject_type):
        assert 'Neptune' in query
        return [SimpleNamespace(id=351253, name='Neptune OVA2', name_cn='瓒呮鍏冩父鎴?娴风帇鏄?OVA2', eps=1, total_episodes=1, platform='OVA')]

    def get_subject(self, subject_id):
        return SimpleNamespace(id=subject_id, name='Neptune OVA2', name_cn='瓒呮鍏冩父鎴?娴风帇鏄?OVA2', eps=1, total_episodes=1, platform='OVA')

    def get_episodes(self, subject_id):
        return [SimpleNamespace(id=1075427, sort=1, ep=1, type=1, name='OVA2', name_cn='OVA2', source_form_hint='ova')]


class _OvaSubjectRegularEpisodeBangumiClient(_OvaBangumiClient):
    def get_episodes(self, subject_id):
        return [SimpleNamespace(id=1075427, sort=1, ep=1, type=0, name='OVA2', name_cn='OVA2')]


class _UnsortedEpisodeBangumiClient(_BangumiClient):
    def get_episodes(self, subject_id):
        return [
            SimpleNamespace(id=subject_id * 10 + 3, sort=3, ep=3, type=0, name='Episode 3'),
            SimpleNamespace(id=subject_id * 10 + 1, sort=1, ep=1, type=0, name='Episode 1'),
            SimpleNamespace(id=subject_id * 10 + 2, sort=2, ep=2, type=0, name='Episode 2'),
        ]


class _NoEpisodeEvidenceBangumiClient(_BangumiClient):
    def get_episodes(self, subject_id):
        raise RuntimeError(f'no episode evidence for {subject_id}')


class _EpRestartBangumiClient(_BangumiClient):
    def get_episodes(self, subject_id):
        return [
            SimpleNamespace(id=subject_id * 100 + 1, sort=14, ep=1, type=0, name='Episode 1'),
            SimpleNamespace(id=subject_id * 100 + 2, sort=15, ep=2, type=0, name='Episode 2'),
        ]


class _CountingBangumiClient(_BangumiClient):
    def __init__(self):
        self.search_queries = []
        self.related_subject_ids = []
        self.episode_subject_ids = []

    def search_subjects(self, query, _subject_type):
        self.search_queries.append(query)
        if 'Second' in query or 'II' in query:
            return [SimpleNamespace(id=201, type=2, name='Show Second Season', name_cn='Show II', eps=2)]
        return [
            SimpleNamespace(id=200, type=2, name='Show', name_cn='Show', eps=2),
            SimpleNamespace(id=301, type=1, name='Show Book', name_cn='Show Book', eps=0),
        ]

    def get_related_subjects(self, subject_id):
        self.related_subject_ids.append(subject_id)
        return [
            SimpleNamespace(id=201, type=2, relation='sequel'),
            SimpleNamespace(id=301, type=1, relation='book'),
        ]

    def get_episodes(self, subject_id):
        self.episode_subject_ids.append(subject_id)
        return [
            SimpleNamespace(id=subject_id * 10 + 1, sort=1, ep=1, type=0, name='Episode 1'),
            SimpleNamespace(id=subject_id * 10 + 2, sort=2, ep=2, type=0, name='Episode 2'),
            SimpleNamespace(id=subject_id * 10 + 99, sort=99, ep=1, type=1, name='Special 1'),
        ]


class _RelationGraphBangumiClient:
    def __init__(self):
        self.subjects = {
            100: SimpleNamespace(id=100, type=2, name='Series Root', name_cn='Series Root', eps=10, platform='TV'),
            201: SimpleNamespace(id=201, type=2, name='Series Second Cour', name_cn='Series Second Cour', eps=10, platform='TV'),
            202: SimpleNamespace(id=202, type=2, name='Series Movie Special', name_cn='Series Movie Special', eps=1, total_episodes=1, platform='movie'),
            301: SimpleNamespace(id=301, type=1, name='Series Book', name_cn='Series Book'),
            302: SimpleNamespace(id=302, type=1, name='Strict Relation Book Sequel', name_cn='Strict Relation Book Sequel'),
            401: SimpleNamespace(id=401, type=2, name='Weak Crossover Anime', name_cn='Weak Crossover Anime', eps=12, platform='TV'),
        }
        self.relations = {
            100: [
                SimpleNamespace(id=201, type=2, relation='sequel'),
                SimpleNamespace(id=301, type=1, relation='book'),
                SimpleNamespace(id=302, type=1, relation='sequel'),
                SimpleNamespace(id=401, type=2, relation='角色出演'),
            ],
            201: [SimpleNamespace(id=202, type=2, relation='sequel')],
            202: [],
            301: [],
            401: [],
        }

    def search_subjects(self, query, _subject_type):
        assert query
        return [self.subjects[100]]

    def get_subject(self, subject_id):
        return self.subjects.get(subject_id)

    def get_related_subjects(self, subject_id):
        return list(self.relations.get(subject_id, []))

    def get_episodes(self, subject_id):
        return [SimpleNamespace(id=subject_id * 10 + 1, sort=1, ep=1, type=0, name='Episode 1')]


def _workspace():
    local = SimpleNamespace(source_path='tests/sample', files=[_File('f1', 'ep1.mkv', 'ep1.mkv'), _File('f2', 'ep2.mkv', 'ep2.mkv')])
    bangumi_contexts = [{
        'context': {
            'episode_structure': {
                'subject_id': 100,
                'title': 'Test',
                'episodes': [
                    {'episode_id': 1001, 'title': 'Episode 1', 'sort': 1, 'ep': 1, 'kind': 'regular'},
                    {'episode_id': 1002, 'title': 'Episode 2', 'sort': 2, 'ep': 2, 'kind': 'regular'},
                ],
            },
        },
    }]
    return _build_workspace(local_evidence=local, bangumi_contexts=bangumi_contexts)


def _multi_episode_workspace():
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1']),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path='merged.mkv',
                is_main=True,
                container_facts={
                    'probe_status': 'available',
                    'duration_seconds': 3618.368,
                    'chapter_count': 4,
                    'chapter_durations_seconds': [881.589, 1075.074, 1520.936, 140.769],
                },
            )
        ],
        bangumi_items=[
            BangumiItemCard(
                ref=f'episode:{1000 + index}',
                item_kind='episode',
                episode_id=1000 + index,
                type='0',
                sort=index,
                ep=index,
                subject_ref='subject:100',
                duration_seconds=duration,
                title=f'Episode {index}',
            )
            for index, duration in enumerate([990, 1200, 1650], start=1)
        ],
    )


def _filename_range_multi_episode_workspace(path: str = 'merged [01-03].mkv'):
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1']),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path=path,
                is_main=True,
                container_facts={'probe_status': 'available', 'duration_seconds': 0, 'chapter_count': 0},
            )
        ],
        bangumi_items=[
            BangumiItemCard(
                ref=f'episode:{1000 + index}',
                item_kind='episode',
                episode_id=1000 + index,
                type='0',
                sort=index,
                ep=index,
                subject_ref='subject:100',
                title=f'Episode {index}',
            )
            for index in range(1, 4)
        ],
    )


def _accepted_recipe():
    return {
        'version': 1,
        'summary': 'map ep files by captured episode number',
        'rules': [
            {
                'name': 'tv_episodes',
                'select': {'filename_regex': 'ep{ep}.mkv'},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
                'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
                'disposition': 'map_to_bangumi',
                'reason': 'filename episode number matches Bangumi sort',
            }
        ],
    }


def test_pi_submit_organize_recipe_accepts_verifier_clean_recipe(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('submit_organize_recipe', {'organize_recipe': _accepted_recipe(), 'summary': 'accepted'})

    assert result['accepted'] is True
    assert state.final_result['status'] == 'accepted'
    assert state.submit_rejection_count == 0
    assert (tmp_path / 'run' / 'final_result.json').exists()
    assert (tmp_path / 'run' / 'artifacts' / 'organize_recipe.json').exists()
    assert (tmp_path / 'run' / 'artifacts' / 'compiled_plan.json').exists()
    assert result['accounting']['matched_path_count'] == 2


def test_pi_submit_organize_recipe_rejects_duplicate_target_and_can_retry(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    duplicate = {
        'version': 1,
        'summary': 'bad duplicate target',
        'rules': [
            {
                'name': 'first',
                'select': {'exact_paths': ['ep1.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'second',
                'select': {'exact_paths': ['ep2.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                'disposition': 'map_to_bangumi',
            },
        ],
    }

    rejected = state.handle_tool('submit_organize_recipe', {'organize_recipe': duplicate})
    accepted = state.handle_tool('submit_organize_recipe', {'organize_recipe': _accepted_recipe()})

    assert rejected['accepted'] is False
    assert state.submit_rejection_count == 1
    duplicate_issue = next(issue for issue in rejected['verifier_result']['issues'] if issue['issue_code'] == 'duplicate_target')
    assert set(duplicate_issue['related_refs']) == {'ep1.mkv', 'ep2.mkv'}
    assert 'episode:1001' in duplicate_issue['message']
    assert any('Duplicate Bangumi target episode:1001' in hint for hint in rejected['repair_hints'])
    assert any('adjacent numbered files' in hint for hint in rejected['repair_hints'])
    assert any('split/variant paths' in hint for hint in rejected['repair_hints'])
    assert any('do not fail_closed the whole case' in hint for hint in rejected['repair_hints'])
    assert any('movie or one-file exact rules' in hint for hint in rejected['repair_hints'])
    assert accepted['accepted'] is True
    assert state.final_result['status'] == 'accepted'


def test_pi_validate_recipe_params_hints_multi_file_group_ref_with_fixed_episode_id(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'bad grouped sequence',
                        'group_ref': 'LG1',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_id': 1001,
                        'reason': 'two local files accidentally reuse one fixed row',
                    }
                ]
            }
        },
    )

    assert result['accepted'] is False
    assert any(issue['issue_code'] == 'duplicate_target' for issue in result['verifier_result']['issues'])
    assert any('Rule "bad grouped sequence" matches 2 visible files but fixes episode_id:1001' in hint for hint in result['repair_hints'])
    assert any('unset episode_id/sort/ep' in hint and 'derive from {ep}' in hint for hint in result['repair_hints'])
    assert any('split separate movie/OVA/special files into exact_path rules' in hint or 'separate exact_paths rules' in hint for hint in result['repair_hints'])


def test_pi_validate_recipe_params_hints_duplicate_locator_variants(tmp_path):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(ref='LF1', path='SP08_1.mkv', is_main=True),
            LocalFileCard(ref='LF2', path='SP08_2.mkv', is_main=True),
        ],
        bangumi_items=[
            BangumiItemCard(
                ref='episode:1008',
                item_kind='episode',
                episode_id=1008,
                type='0',
                sort=8,
                ep=8,
                subject_ref='subject:100',
            )
        ],
    )
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'SP split variants',
                        'source_pattern': 'SP{ep:02}_{part}.mkv',
                        'subject_id': 100,
                        'media_kind': 'sp',
                        'episode_type': 'regular',
                        'episode_range': '8',
                        'reason': 'both local files expose the same SP08 locator',
                    }
                ]
            }
        },
    )

    assert result['accepted'] is False
    assert any(issue['issue_code'] == 'duplicate_target' for issue in result['verifier_result']['issues'])
    assert any('same extracted episode number(s) [8]' in hint for hint in result['repair_hints'])
    assert any('duplicate local locator or split/variant case' in hint for hint in result['repair_hints'])
    assert any('append an exact supplemental rule for only that extra path' in hint for hint in result['repair_hints'])


def test_pi_validate_organize_recipe_does_not_finalize(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': _accepted_recipe()})

    assert result['accepted'] is True
    assert state.final_result is None
    assert state.compiled_plan is not None
    assert len(state.compiled_plan.assignments) == 2


def test_pi_validate_organize_recipe_rejects_json_string_payload(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': json.dumps(_accepted_recipe())})

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'JSON strings and wrapper objects are not accepted' in result['error']
    assert state.final_result is None
    assert state.compiled_plan is None


def test_pi_validate_organize_recipe_params_rejects_json_string_payload(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'map two exact files',
        'rules': [
            {
                'name': 'tv episodes',
                'source_pattern': 'ep{ep}.mkv',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'episode_range': '1-2',
                'disposition': 'map_to_bangumi',
                'reason': 'semantic params identify a numbered TV run',
            },
        ],
    }

    result = state.handle_tool('validate_organize_recipe_params', {'recipe_params': json.dumps(recipe_params), 'detail': True})

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'JSON strings and wrapper objects are not accepted' in result['error']
    assert state.final_result is None
    assert state.compiled_plan is None


def test_pi_validate_organize_recipe_params_defaults_to_compact_result_and_detail_restores_debug_fields(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'map local group by selector shorthand',
        'rules': [
            {
                'name': 'group shorthand',
                'group_ref': 'LG1',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'reason': 'Bangumi subject evidence supports this local group.',
            },
        ],
    }

    compact = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params})
    detailed = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})

    assert compact['accepted'] is True
    assert compact['detail_available'] is True
    assert 'artifact_paths' in compact
    assert 'organize_recipe' not in compact
    assert 'compiled_plan' not in compact
    assert 'accounting' not in compact
    assert detailed['accepted'] is True
    assert detailed['organize_recipe']['rules'][0]['episode']['range'] == '1-2'
    assert detailed['compiled_plan']['assignments']
    assert detailed['accounting']['matched_path_count'] == 2


def test_pi_validate_organize_recipe_params_default_invalid_result_is_compact(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'recipe_params': {
                'rules': [
                    {
                        'name': 'bad grouped sequence',
                        'group_ref': 'LG1',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_id': 1001,
                    }
                ]
            }
        },
    )

    assert result['accepted'] is False
    assert result['status'] == 'invalid'
    assert len(result['repair_hints']) <= 4
    assert 'compiled_plan' not in result
    assert 'organize_recipe' not in result
    assert 'accounting' not in result
    assert 'artifact_paths' in result
    assert result['verifier_result']['issues'][0]['issue_code'] == 'duplicate_target'
    assert set(result['verifier_result']['issues'][0]) == {'issue_code', 'severity', 'ref', 'message', 'related_refs'}


def test_pi_validate_organize_recipe_params_expands_group_ref_selector(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'map local group by selector shorthand',
        'rules': [
            {
                'name': 'group shorthand',
                'group_ref': 'LG1',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'reason': 'Bangumi subject evidence supports this local group.',
            },
        ],
    }

    result = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})

    assert result['accepted'] is True
    rule = result['organize_recipe']['rules'][0]
    assert rule['select']['exact_paths'] == []
    assert '(?P<ep>' in rule['select']['filename_regex']
    assert rule['episode']['range'] == '1-2'
    assert len(state.compiled_plan.assignments) == 2


def test_pi_validate_organize_recipe_params_rejects_group_ref_bad_source_pattern(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'map local group by selector shorthand',
        'rules': [
            {
                'name': 'group shorthand with stale pattern',
                'group_ref': 'LG1',
                'source_pattern': 'EP{ep:02}.mkv',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'reason': 'Bangumi subject evidence supports this local group.',
            },
        ],
    }

    result = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'source_pattern that matches none of that group' in result['error']
    assert any('Fix the template or use group_ref alone' in hint for hint in result['repair_hints'])


def test_pi_validate_organize_recipe_params_rejects_unknown_group_ref(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'bad group',
                        'group_ref': 'LG404',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                    },
                ],
            }
        },
    )

    assert result['ok'] is False
    assert 'unknown group_ref' in result['error']
    assert any('group_ref only expands a local selector' in hint for hint in result['repair_hints'])


def test_pi_validate_organize_recipe_params_rejects_array_episode_range(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'recipe_params': {
                'rules': [
                    {
                        'name': 'array range',
                        'group_ref': 'LG1',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_range': [1, 2],
                    },
                ],
            }
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'episode_range must be a compact string' in result['error']
    assert any('do not pass [1,13]' in hint for hint in result['repair_hints'])


def test_pi_validate_organize_recipe_params_rejects_raw_media_kind(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'recipe_params': {
                'rules': [
                    {
                        'name': 'raw media kind',
                        'group_ref': 'LG1',
                        'subject_id': 100,
                        'media_kind': 'web',
                        'episode_type': 'regular',
                    },
                ],
            }
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert "media_kind 'web' is not legal" in result['error']
    assert any("raw source/API values" in hint for hint in result['repair_hints'])


def test_pi_validate_organize_recipe_params_patch_reuses_latest_params(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'map incomplete run',
        'rules': [
            {
                'name': 'tv episodes',
                'source_pattern': 'ep{ep}.mkv',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'episode_range': '1',
                'reason': 'first draft only covered one file',
            },
        ],
    }
    first = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})

    patched = state.handle_tool(
        'validate_organize_recipe_params_patch',
        {
            'detail': True,
            'patch_delta': 'tv episodes: expand local range from 1 to 1-2',
            'recipe_params_patch': {
                'patch_rules': [
                    {'name': 'tv episodes', 'updates': {'episode_range': '1-2'}},
                ],
            }
        },
    )

    assert first['accepted'] is False
    assert (tmp_path / 'run' / 'artifacts' / 'recipe_params.json').exists()
    assert patched['accepted'] is True
    assert patched['params_patch_applied'] is True
    assert patched['case_board_transaction']['patch_delta']['section_type'] == 'Patch Delta'
    assert state.latest_recipe_params_payload['rules'][0]['episode_range'] == '1-2'
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert '## Patch Delta' in notes
    assert 'expand local range' in notes


def test_pi_validate_organize_recipe_params_patch_rejects_nested_select_update(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'patch supplemental selector',
        'rules': [
            {
                'name': 'extras',
                'exact_paths': ['ep1.mkv'],
                'disposition': 'non_bangumi_or_supplemental',
                'reason': 'first draft covers one supplemental file',
            },
        ],
    }

    first = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})
    patched = state.handle_tool(
        'validate_organize_recipe_params_patch',
        {
            'detail': True,
            'recipe_params_patch': {
                'patch_rules': [
                    {
                        'name': 'extras',
                            'updates': {
                                'select': {
                                    'exact_paths': ['ep1.mkv', 'ep2.mkv'],
                                    'filename_regex': '',
                                'path_glob': '**/*.mkv',
                            }
                        },
                    },
                ],
            }
        },
    )

    assert first['accepted'] is False
    assert patched['ok'] is False
    assert patched['accepted'] is False
    assert 'non-canonical nested raw object' in patched['error']
    assert 'select' in patched['error']
    assert state.latest_recipe_params_payload['rules'][0]['exact_paths'] == ['ep1.mkv']


def test_pi_validate_organize_recipe_params_patch_appends_new_complete_rule(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'map one file then patch the uncovered file',
        'rules': [
            {
                'name': 'episode 1',
                'exact_paths': ['ep1.mkv'],
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_id': 1001,
                'reason': 'first draft covers one file',
            },
        ],
    }

    first = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})
    patched = state.handle_tool(
        'validate_organize_recipe_params_patch',
        {
            'detail': True,
            'recipe_params_patch': {
                'append_rules': [
                    {
                        'name': 'episode 2 supplemental',
                        'exact_paths': ['ep2.mkv'],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'no supportable target in this minimal repair test',
                    }
                ]
            },
            'detail': True,
        },
    )

    assert first['accepted'] is False
    assert patched['accepted'] is True
    assert [rule['name'] for rule in state.latest_recipe_params_payload['rules']] == ['episode 1', 'episode 2 supplemental']


def test_pi_validate_organize_recipe_params_patch_rejects_patch_fields_misplaced_in_patch_delta(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'bad fixed target over a group',
        'rules': [
            {
                'name': 'both files wrong',
                'group_ref': 'LG1',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_id': 1001,
                'reason': 'bad draft intentionally reuses one episode target',
            },
        ],
    }
    replacement_rows = [
        {
            'name': 'episode 1 exact',
            'exact_paths': ['ep1.mkv'],
            'subject_id': 100,
            'media_kind': 'tv',
            'episode_id': 1001,
            'reason': 'first file maps to episode 1',
        },
        {
            'name': 'episode 2 exact',
            'exact_paths': ['ep2.mkv'],
            'subject_id': 100,
            'media_kind': 'tv',
            'episode_id': 1002,
            'reason': 'second file maps to episode 2',
        },
    ]

    first = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})
    patched = state.handle_tool(
        'validate_organize_recipe_params_patch',
        {
            'detail': True,
            'patch_delta': {
                'summary': 'Split the bad group rule into exact rows and remove the old group rule.',
                'remove_rule_names': ['both files wrong'],
                'rules': replacement_rows,
            },
            'recipe_params_patch': {
                'rules': replacement_rows,
            },
        },
    )

    assert first['accepted'] is False
    assert patched['ok'] is False
    assert patched['accepted'] is False
    assert 'non-canonical field(s)' in patched['error']
    assert 'rules' in patched['error']
    assert [rule['name'] for rule in state.latest_recipe_params_payload['rules']] == ['both files wrong']
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert 'Split the bad group rule into exact rows' in notes


def test_pi_validate_organize_recipe_params_patch_still_rejects_missing_update_target(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'rules': [
            {
                'name': 'tv episodes',
                'source_pattern': 'ep{ep}.mkv',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'episode_range': '1-2',
                'reason': 'complete run',
            },
        ],
    }

    state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})
    result = state.handle_tool(
        'validate_organize_recipe_params_patch',
        {
            'detail': True,
            'recipe_params_patch': {
                'patch_rules': [
                    {'name': 'typo rule name', 'updates': {'exclude_regex': 'SP08_2'}},
                ],
            }
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'patch_rules target not found: typo rule name' in result['error']


def test_pi_submit_organize_recipe_params_patch_reuses_accepted_patch_without_reapplying_append(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'summary': 'map one file then patch the uncovered file',
        'rules': [
            {
                'name': 'episode 1',
                'exact_paths': ['ep1.mkv'],
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_id': 1001,
                'reason': 'first draft covers one file',
            },
        ],
    }
    patch = {
        'append_rules': [
            {
                'name': 'episode 2 supplemental',
                'exact_paths': ['ep2.mkv'],
                'disposition': 'non_bangumi_or_supplemental',
                'reason': 'no supportable target in this minimal repair test',
            }
        ]
    }

    first = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})
    patched = state.handle_tool('validate_organize_recipe_params_patch', {'recipe_params_patch': patch})
    submitted = state.handle_tool(
        'submit_organize_recipe_params_patch',
        {
            'detail': True,
            'recipe_params_patch': {**patch, 'remove_rule_names': []},
            'submit_snapshot': 'episode 1 mapped; episode 2 supplemental; patch validation already accepted',
        },
    )

    assert first['accepted'] is False
    assert patched['accepted'] is True
    assert submitted['accepted'] is True
    assert submitted['params_patch_applied'] is True
    assert submitted['params_patch_reused_from_accepted_validation'] is True
    assert submitted['case_board_transaction']['submit_snapshot']['section_type'] == 'Submit Snapshot'
    assert len(state.latest_recipe_params_payload['rules']) == 2
    assert state.final_result['status'] == 'accepted'
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert notes.count('## Submit Snapshot') == 1
    assert 'patch validation already accepted' in notes


def test_pi_submit_organize_recipe_params_invalid_returns_patch_repair_mode(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'submit_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'summary': 'submit an incomplete draft',
                'rules': [
                    {
                        'name': 'episode 1 only',
                        'exact_paths': ['ep1.mkv'],
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_id': 1001,
                    },
                ],
            }
        },
    )

    assert result['accepted'] is False
    assert result['submit_rejected'] is True
    assert result['finalizes_case'] is False
    assert result['repair_mode']['preferred_tool'] == 'validate_organize_recipe_params_patch'
    assert result['repair_mode']['latest_params_available'] is True


def test_invalid_episode_offset_hint_rejects_sp_as_offset(tmp_path):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1']),
        local_files=[
            LocalFileCard(ref='LF1', path='SP01.mkv', is_main=True),
        ],
        bangumi_items=[
            BangumiItemCard(
                ref='episode:1001',
                item_kind='episode',
                episode_id=1001,
                type='0',
                sort=1,
                ep=1,
                subject_ref='subject:100',
                title='Special 1',
            ),
        ],
    )
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'bad sp offset',
                        'source_pattern': 'SP{ep:02}.mkv',
                        'subject_id': 100,
                        'media_kind': 'special',
                        'episode_type': 'regular',
                        'episode_range': '1-1',
                        'episode_offset': 'SP',
                    }
                ]
            }
        },
    )

    assert result['accepted'] is False
    assert any('do not use SP as episode_offset' in hint for hint in result['repair_hints'])


def test_uncovered_and_duplicate_coverage_hints_prefer_replacing_partial_supplemental_rule(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    uncovered_result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'partial supplemental',
                        'exact_paths': ['ep1.mkv'],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'partial coverage for repair hint',
                    }
                ]
            }
        },
    )

    duplicate_result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'supplemental a',
                        'exact_paths': ['ep1.mkv'],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'first coverage',
                    },
                    {
                        'name': 'supplemental b',
                        'exact_paths': ['ep1.mkv'],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'duplicate coverage',
                    },
                    {
                        'name': 'supplemental c',
                        'exact_paths': ['ep2.mkv'],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'cover remaining file',
                    },
                ]
            }
        },
    )

    assert uncovered_result['accepted'] is False
    assert any('patch or replace that existing rule so one rule covers the intended group exactly once' in hint for hint in uncovered_result['repair_hints'])
    assert duplicate_result['accepted'] is False
    assert any('Do not append a second supplemental rule' in hint for hint in duplicate_result['repair_hints'])


def test_uncovered_hint_prefers_patching_existing_supplemental_sibling_rule(tmp_path):
    paths = [
        'Movie/SPs/Show [SP01].mkv',
        'Movie/SPs/Show [SP02].mkv',
        'Movie/SPs/Show [SP03].mkv',
        'Movie/SPs/Show [SP04].mkv',
    ]
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2', 'LF3', 'LF4']),
        local_files=[
            LocalFileCard(ref=f'LF{index}', path=path, is_main=True)
            for index, path in enumerate(paths, start=1)
        ],
    )
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'movie package extras',
                        'exact_paths': paths[2:],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'known supplemental extras',
                    }
                ]
            }
        },
    )

    assert result['accepted'] is False
    assert any('already has supplemental rule(s)' in hint for hint in result['repair_hints'])
    assert any('movie package extras' in hint for hint in result['repair_hints'])
    assert any('leave unrelated mapped exact-path rules unchanged' in hint for hint in result['repair_hints'])


def test_missing_target_episode_hint_checks_row_episode_type_before_supplemental(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'wrong type sequence',
                        'source_pattern': 'ep{ep}.mkv',
                        'subject_id': 100,
                        'media_kind': 'sp',
                        'episode_type': 'special',
                        'episode_range': '1-2',
                    }
                ]
            }
        },
    )

    assert result['accepted'] is False
    assert any('do not imply episode_type:"special"' in hint for hint in result['repair_hints'])


def test_pi_validate_review_warns_for_long_supplemental_until_targeted_lookup(tmp_path):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path='ep1.mkv',
                is_main=True,
                container_facts={'probe_status': 'available', 'duration_seconds': 1440.0},
                fact_summary={'duration_seconds': 1440.0},
            ),
            LocalFileCard(
                ref='LF2',
                path='Bonus Main.mkv',
                is_main=True,
                container_facts={'probe_status': 'available', 'duration_seconds': 1800.0},
                fact_summary={'duration_seconds': 1800.0},
            ),
        ],
        bangumi_items=[
            BangumiItemCard(
                ref='episode:1001',
                item_kind='episode',
                episode_id=1001,
                type='0',
                sort=1,
                ep=1,
                subject_ref='subject:100',
                title='Episode 1',
            )
        ],
    )
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'rules': [
            {'name': 'main', 'exact_paths': ['ep1.mkv'], 'subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
            {
                'name': 'bonus',
                'exact_paths': ['Bonus Main.mkv'],
                'disposition': 'non_bangumi_or_supplemental',
                'reason': 'Standalone bonus-like file with no supportable Bangumi episode target.',
            },
        ]
    }

    warning = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})
    state.handle_tool('find_bangumi_targets_for_local_file', {'source_path': 'Bonus Main.mkv', 'max_subjects': 1, 'max_episode_cards': 2})
    accepted = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})

    assert warning['accepted'] is False
    assert warning['status'] == 'review'
    assert warning['review_warnings'][0]['source_path'] == 'Bonus Main.mkv'
    assert any('find_bangumi_targets_for_local_file' in hint for hint in warning['repair_hints'])
    warning_keys = list(warning)
    assert warning_keys.index('review_warnings') < warning_keys.index('compiled_plan')
    assert warning_keys.index('repair_hints') < warning_keys.index('compiled_plan')
    assert accepted['accepted'] is True
    assert accepted['review_warnings'] == []


def test_pi_validate_review_allows_bracketed_iv_in_supplemental_dir(tmp_path):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path='Movie.mkv',
                is_main=True,
                container_facts={'probe_status': 'available', 'duration_seconds': 7200.0},
                fact_summary={'duration_seconds': 7200.0},
            ),
            LocalFileCard(
                ref='LF2',
                path='SPs/Movie [IV].mkv',
                is_main=True,
                container_facts={'probe_status': 'available', 'duration_seconds': 1378.61},
                fact_summary={'duration_seconds': 1378.61},
            ),
        ],
        bangumi_items=[
            BangumiItemCard(
                ref='episode:1001',
                item_kind='episode',
                episode_id=1001,
                type='0',
                sort=1,
                ep=1,
                subject_ref='subject:100',
                title='Movie',
            )
        ],
    )
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {'name': 'movie', 'exact_paths': ['Movie.mkv'], 'subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                    {
                        'name': 'interview',
                        'exact_paths': ['SPs/Movie [IV].mkv'],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'Covered as a supplemental file in the SPs directory.',
                    },
                ],
            }
        },
    )

    assert result['accepted'] is True
    assert result['review_warnings'] == []


def test_pi_validate_review_does_not_treat_bare_iv_title_as_obvious_extra(tmp_path):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2']),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path='Overlord IV - 01.mkv',
                is_main=True,
                container_facts={'probe_status': 'available', 'duration_seconds': 1440.0},
                fact_summary={'duration_seconds': 1440.0},
            ),
            LocalFileCard(
                ref='LF2',
                path='Overlord IV - 02.mkv',
                is_main=True,
                container_facts={'probe_status': 'available', 'duration_seconds': 1440.0},
                fact_summary={'duration_seconds': 1440.0},
            ),
        ],
        bangumi_items=[
            BangumiItemCard(
                ref='episode:1001',
                item_kind='episode',
                episode_id=1001,
                type='0',
                sort=1,
                ep=1,
                subject_ref='subject:100',
                title='Episode 1',
            )
        ],
    )
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {'name': 'episode', 'exact_paths': ['Overlord IV - 01.mkv'], 'subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                    {
                        'name': 'excluded',
                        'exact_paths': ['Overlord IV - 02.mkv'],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'No supported target.',
                    },
                ],
            }
        },
    )

    assert result['accepted'] is False
    assert result['status'] == 'review'
    assert result['review_warnings'][0]['source_path'] == 'Overlord IV - 02.mkv'


def test_pi_validate_review_accepts_targeted_representative_for_supplemental_sequence(tmp_path):
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1', 'LF2', 'LF3', 'LF4']),
        local_files=[
            LocalFileCard(
                ref='LF1',
                path='ep1.mkv',
                is_main=True,
                container_facts={'probe_status': 'available', 'duration_seconds': 1440.0},
                fact_summary={'duration_seconds': 1440.0},
            ),
            *[
                LocalFileCard(
                    ref=f'LF{index + 1}',
                    path=f'SPs/Side Story {index:02}.mkv',
                    is_main=True,
                    container_facts={'probe_status': 'available', 'duration_seconds': 1800.0},
                    fact_summary={'duration_seconds': 1800.0},
                )
                for index in range(1, 4)
            ],
        ],
        bangumi_items=[
            BangumiItemCard(
                ref='episode:1001',
                item_kind='episode',
                episode_id=1001,
                type='0',
                sort=1,
                ep=1,
                subject_ref='subject:100',
                title='Episode 1',
            )
        ],
    )
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    recipe_params = {
        'rules': [
            {'name': 'episode', 'exact_paths': ['ep1.mkv'], 'subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
            {
                'name': 'side-story-sequence',
                'source_pattern': 'SPs/Side Story {ep:02}.mkv',
                'disposition': 'non_bangumi_or_supplemental',
                'reason': 'Same named supplemental sequence with no supportable target.',
            },
        ]
    }

    warning = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})
    state.handle_tool('find_bangumi_targets_for_local_file', {'source_path': 'SPs/Side Story 01.mkv', 'max_subjects': 1, 'max_episode_cards': 2})
    accepted = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})

    assert warning['accepted'] is False
    assert warning['status'] == 'review'
    assert [item['source_path'] for item in warning['review_warnings']] == [
        'SPs/Side Story 01.mkv',
        'SPs/Side Story 02.mkv',
        'SPs/Side Story 03.mkv',
    ]
    assert accepted['accepted'] is True
    assert accepted['review_warnings'] == []


def test_pi_validate_organize_recipe_hydrates_declared_subject_targets(tmp_path):
    local = SimpleNamespace(source_path='tests/sample', files=[_File('f1', 'ep1.mkv', 'ep1.mkv'), _File('f2', 'ep2.mkv', 'ep2.mkv')])
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'validate_organize_recipe',
        {
            'organize_recipe': {
                'version': 1,
                'summary': 'validate can expose target episodes for declared subjects',
                'rules': [
                    {
                        'name': 'episodes',
                        'select': {'filename_regex': 'ep{ep}.mkv'},
                        'target': {'bangumi_subject_id': 200, 'media_kind': 'tv', 'episode_type': 'regular'},
                        'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
                        'disposition': 'map_to_bangumi',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    assert any(item.episode_id == 2001 for item in state.workspace.bangumi_items)
    assert any(subject.subject_id == 200 for subject in state.workspace.bangumi_subjects)
    assert state.final_result is None


def test_pi_validate_organize_recipe_params_builds_recipe_from_semantic_params(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'version': 1,
                'summary': 'params map ep files',
                'rules': [
                        {
                            'name': 'tv episodes',
                            'source_pattern': 'ep{ep}.mkv',
                            'subject_id': 100,
                            'media_kind': 'tv',
                            'episode_type': 'regular',
                        'episode_offset': None,
                        'episode_range': '1-2',
                        'disposition': 'map_to_bangumi',
                        'reason': 'semantic params identify a numbered TV run',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    assert result['params_compiled'] is True
    assert result['validation_role'] == 'trial_check'
    assert result['finalizes_case'] is False
    assert 'accepted validation still requires submit_organize_recipe_params' in result['feedback_semantics']
    recipe = result['organize_recipe']
    assert recipe['rules'][0]['select']['filename_regex'] == 'ep(?P<ep>\\d+)\\.mkv'
    assert recipe['rules'][0]['episode']['offset'] == 'EP'
    assert result['accounting']['matched_path_count'] == 2
    assert state.final_result is None


def test_pi_validate_organize_recipe_params_can_match_sequence_by_bangumi_ep(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('f1', 'Show 01.mkv', 'Show 01.mkv'), _File('f2', 'Show 02.mkv', 'Show 02.mkv')],
    )
    workspace = _build_workspace(local_evidence=local, bangumi_contexts=[{
        'context': {
            'episode_structure': {
                'subject_id': 541,
                'title': 'Later Season',
                'episodes': [
                    {'episode_id': 54101, 'title': 'Episode 1', 'sort': 14, 'ep': 1, 'kind': 'regular'},
                    {'episode_id': 54102, 'title': 'Episode 2', 'sort': 15, 'ep': 2, 'kind': 'regular'},
                ],
            },
        },
    }])
    state = PiCaseToolState(workspace=workspace, bangumi_client=_EpRestartBangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'later season',
                        'source_pattern': 'Show {ep:02}.mkv',
                        'subject_id': 541,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_range': '1-2',
                        'episode_number_field': 'ep',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    assert result['organize_recipe']['rules'][0]['episode']['number_field'] == 'ep'
    assert [item['target']['sort'] for item in result['compiled_plan']['assignments']] == [14, 15]
    assert [item['target']['ep'] for item in result['compiled_plan']['assignments']] == [1, 2]


def test_pi_validate_organize_recipe_params_normalizes_inverted_shifted_range(tmp_path):
    files = [
        _File(f'f{number}', f'Show [{number}].mkv', f'Show Later/Show [{number}].mkv')
        for number in range(34, 45)
    ]
    local = SimpleNamespace(source_path='tests/sample', files=files)
    workspace = _build_workspace(local_evidence=local, bangumi_contexts=[{
        'context': {
            'episode_structure': {
                'subject_id': 328195,
                'title': 'Later Subject',
                'episodes': [
                    {'episode_id': 32819500 + index, 'title': f'Episode {index}', 'sort': index, 'ep': index, 'kind': 'regular'}
                    for index in range(1, 12)
                ],
            },
        },
    }])
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'later subject',
                        'source_pattern': 'Show Later/Show [{ep}].mkv',
                        'subject_id': 328195,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_range': '1-11',
                        'episode_offset': 'EP+33',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    rule = result['organize_recipe']['rules'][0]
    assert rule['episode']['range'] == '34-44'
    assert rule['episode']['offset'] == 'EP-33'
    assert result['accounting']['matched_path_count'] == 11
    assert [item['target']['sort'] for item in result['compiled_plan']['assignments']] == list(range(1, 12))


def test_pi_validate_organize_recipe_params_accepts_subject_level_movie_rule(tmp_path):
    local = SimpleNamespace(source_path='tests/sample', files=[_File('f1', 'Movie.mkv', 'Movie.mkv')])
    workspace = _build_workspace(local_evidence=local, bangumi_contexts=[{
        'context': {
            'episode_structure': {
                'subject_id': 301,
                'title': 'Movie',
                'source_form_hint': 'movie',
                'episodes': [],
            },
        },
    }])
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'movie subject',
                        'exact_paths': ['Movie.mkv'],
                        'subject_id': 301,
                        'media_kind': 'movie',
                        'reason': 'file title matches one-movie subject',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    assert result['organize_recipe']['rules'][0]['target']['episode_id'] == 0
    assignment = result['compiled_plan']['assignments'][0]
    assert assignment['target']['bangumi_subject_id'] == 301
    assert assignment['target']['media_kind'] == 'movie'


def test_pi_validate_organize_recipe_params_treats_literal_source_pattern_as_exact_movie_path(tmp_path):
    local = SimpleNamespace(source_path='tests/sample', files=[_File('f1', 'Movie [ABC123].mkv', 'Movie [ABC123].mkv')])
    workspace = _build_workspace(local_evidence=local, bangumi_contexts=[{
        'context': {
            'episode_structure': {
                'subject_id': 301,
                'title': 'Movie',
                'source_form_hint': 'movie',
                'episodes': [],
            },
        },
    }])
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'movie subject',
                        'source_pattern': 'Movie [ABC123].mkv',
                        'subject_id': 301,
                        'media_kind': 'movie',
                        'episode_range': '1',
                        'reason': 'literal filename names one movie file',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    rule = result['organize_recipe']['rules'][0]
    assert rule['select']['exact_paths'] == ['Movie [ABC123].mkv']
    assert rule['select']['filename_regex'] == ''
    assert result['accounting']['mapped_file_count'] == 1


def test_pi_validate_organize_recipe_params_rejects_source_path_alias_for_one_file(tmp_path):
    local = SimpleNamespace(source_path='tests/sample', files=[_File('f1', 'Movie.mkv', 'Movie.mkv')])
    workspace = _build_workspace(local_evidence=local, bangumi_contexts=[{
        'context': {
            'episode_structure': {
                'subject_id': 301,
                'title': 'Movie',
                'source_form_hint': 'movie',
                'episodes': [],
            },
        },
    }])
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'movie subject',
                        'source_path': 'Movie.mkv',
                        'subject_id': 301,
                        'media_kind': 'movie',
                        'reason': 'file title matches one-movie subject',
                    }
                ],
            }
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'non-canonical field(s)' in result['error']
    assert 'source_path' in result['error']


def test_pi_validate_organize_recipe_params_accepts_single_file_multi_episode_source_unit(tmp_path):
    state = PiCaseToolState(workspace=_multi_episode_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'summary': 'merged file maps a target span',
                'rules': [
                    {
                        'name': 'merged ova',
                        'source_unit': 'single_file_multi_episode',
                        'exact_paths': ['merged.mkv'],
                        'subject_id': 100,
                        'media_kind': 'ova',
                        'episode_type': 'regular',
                        'episode_range': '1-3',
                        'reason': 'one local file has chapters/duration supporting the three exposed episode rows',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    assert result['organize_recipe']['rules'][0]['source_unit'] == 'single_file_multi_episode'
    assert result['compiled_plan']['assignments'][0]['target_span']['episode_ids'] == [1001, 1002, 1003]
    assert result['accounting']['mapped_file_count'] == 1
    assert result['accounting']['mapped_target_episode_count'] == 3
    assert result['accounting']['single_file_multi_episode_count'] == 1
    assert state.final_result is None


def test_pi_validate_organize_recipe_params_accepts_single_file_multi_episode_filename_range(tmp_path):
    state = PiCaseToolState(
        workspace=_filename_range_multi_episode_workspace(),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'summary': 'merged file names the full range',
                'rules': [
                    {
                        'name': 'merged ova',
                        'source_unit': 'single_file_multi_episode',
                        'exact_paths': ['merged [01-03].mkv'],
                        'subject_id': 100,
                        'media_kind': 'ova',
                        'episode_type': 'regular',
                        'episode_range': '1-3',
                        'reason': 'one local file filename explicitly carries the target span [01-03]',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    assert result['compiled_plan']['assignments'][0]['target_span']['episode_ids'] == [1001, 1002, 1003]
    assert result['accounting']['single_file_multi_episode_count'] == 1


def test_pi_validate_organize_recipe_params_rejects_mismatched_single_file_multi_episode_filename_range(tmp_path):
    state = PiCaseToolState(
        workspace=_filename_range_multi_episode_workspace(path='merged [01-02].mkv'),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'summary': 'filename range does not cover the declared target span',
                'rules': [
                    {
                        'name': 'merged ova',
                        'source_unit': 'single_file_multi_episode',
                        'exact_paths': ['merged [01-02].mkv'],
                        'subject_id': 100,
                        'media_kind': 'ova',
                        'episode_type': 'regular',
                        'episode_range': '1-3',
                        'reason': 'mismatched filename range should not be accepted as evidence',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is False
    assert any(issue['issue_code'] == 'missing_multi_episode_evidence' for issue in result['verifier_result']['issues'])


def test_pi_validate_organize_recipe_params_rejects_zero_padded_ep_and_natural_range_aliases(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('f1', 'Show 01.mkv', 'Vol.1/Show 01.mkv'),
            _File('f2', 'Show 02.mkv', 'Vol.1/Show 02.mkv'),
        ],
    )
    workspace = _build_workspace(local_evidence=local, bangumi_contexts=[{
        'context': {
            'episode_structure': {
                'subject_id': 100,
                'title': 'Test',
                'episodes': [
                    {'episode_id': 1001, 'title': 'Episode 1', 'sort': 1, 'ep': 1, 'kind': 'regular'},
                    {'episode_id': 1002, 'title': 'Episode 2', 'sort': 2, 'ep': 2, 'kind': 'regular'},
                ],
            },
        },
    }])
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'summary': 'params with natural moviepilot-style fields',
                'rules': [
                    {
                        'name': 'tv episodes',
                        'kind': 'numbered_run',
                        'source_pattern': 'Vol.*/Show ??.mkv',
                        'source_template': 'Show {ep:02d}.mkv',
                        'subject_id': 100,
                        'episode_type': 'regular',
                        'offset': 0,
                        'episode_start': 1,
                        'episode_end': 2,
                        'disposition': 'map_to_bangumi',
                    }
                ],
            }
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'non-canonical field(s)' in result['error']
    assert 'source_template' in result['error']
    assert 'offset' in result['error']


def test_pi_validate_organize_recipe_params_treats_source_pattern_globs_as_wildcards(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('f1', 'Show 01.mkv', 'Vol.1/Show 01.mkv'),
            _File('f2', 'Show 02.mkv', 'Vol.2/Show 02.mkv'),
        ],
    )
    workspace = _build_workspace(local_evidence=local, bangumi_contexts=[{
        'context': {
            'episode_structure': {
                'subject_id': 100,
                'title': 'Test',
                'episodes': [
                    {'episode_id': 1001, 'title': 'Episode 1', 'sort': 1, 'ep': 1, 'kind': 'regular'},
                    {'episode_id': 1002, 'title': 'Episode 2', 'sort': 2, 'ep': 2, 'kind': 'regular'},
                ],
            },
        },
    }])
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'source_pattern': 'Vol.*/Show {ep:02d}.mkv',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_range': '1-2',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    assert result['accounting']['matched_path_count'] == 2


def test_pi_validate_organize_recipe_params_rejects_natural_aliases(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'summary': 'params with natural aliases',
                'rules': [
                    {
                        'name': 'tv episodes',
                        'source_template': 'ep{ep}.mkv',
                        'subject_id': 100,
                        'kind': 'tv',
                        'type': 'regular',
                        'range': '1-2',
                        'disposition': 'map_to_bangumi',
                    }
                ],
            }
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'non-canonical field(s)' in result['error']
    assert 'source_template' in result['error']
    assert 'range' in result['error']


def test_pi_validate_organize_recipe_params_turns_non_ep_placeholders_into_wildcards(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('f1', 'Show 01 Title A.mkv', 'Show 01 Title A.mkv'),
            _File('f2', 'Show 02 Title B.mkv', 'Show 02 Title B.mkv'),
        ],
    )
    workspace = _build_workspace(local_evidence=local, bangumi_contexts=[{
        'context': {
            'episode_structure': {
                'subject_id': 100,
                'title': 'Test',
                'episodes': [
                    {'episode_id': 1001, 'title': 'Episode 1', 'sort': 1, 'ep': 1, 'kind': 'regular'},
                    {'episode_id': 1002, 'title': 'Episode 2', 'sort': 2, 'ep': 2, 'kind': 'regular'},
                ],
            },
        },
    }])
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'summary': 'params with ignored variable text',
                'rules': [
                    {
                        'name': 'tv episodes',
                        'source_pattern': 'Show {ep} {title}.mkv',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_range': '1-2',
                        'disposition': 'map_to_bangumi',
                    }
                ],
            }
        },
    )

    assert result['accepted'] is True
    recipe = result['organize_recipe']
    assert recipe['rules'][0]['select']['filename_regex'] == 'Show\\ (?P<ep>\\d+)\\ .*?\\.mkv'
    assert result['accounting']['matched_path_count'] == 2


def test_pi_submit_organize_recipe_params_accepts_and_finalizes(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'submit_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'summary': 'params final',
                'rules': [
                    {
                        'name': 'tv episodes',
                        'source_pattern': 'ep{ep}.mkv',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_range': '1-2',
                        'disposition': 'map_to_bangumi',
                    }
                ],
            },
            'summary': 'accepted via params',
        },
    )

    assert result['accepted'] is True
    assert result['params_compiled'] is True
    assert state.final_result['status'] == 'accepted'
    assert state.final_result['summary'] == 'accepted via params'


def test_pi_submit_organize_recipe_rejects_params_shaped_payload(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'submit_organize_recipe',
        {
            'organize_recipe': {
                'summary': 'params accidentally passed to raw submit',
                'rules': [
                    {
                        'name': 'tv episodes',
                        'source_pattern': 'ep{ep}.mkv',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_range': '1-2',
                        'reason': 'semantic params identify a numbered TV run',
                    }
                ],
            },
            'summary': 'accepted despite raw tool name',
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'invalid OrganizeRecipeDraft payload' in result['error']
    assert state.final_result is None


def test_pi_fail_closed_rejects_model_reported_budget_exhausted(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('fail_closed', {'reason': 'budget_exhausted', 'reason_kind': 'budget_exhausted'})

    assert result['ok'] is False
    assert 'runner-only' in result['error']
    assert state.final_result is None


def test_pi_fail_closed_rejects_model_budget_exhausted_escape_hatch(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'fail_closed',
        {'reason': 'budget_exhausted', 'reason_kind': 'budget_exhausted', 'allow_runner_budget_exhausted': True},
    )

    assert result['ok'] is False
    assert 'runner-only' in result['error']
    assert state.final_result is None


def test_pi_fail_closed_rejects_empty_draft_without_evidence(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'fail_closed',
        {
            'reason': 'No recipe artifact, no validation artifact, and no subject/episode evidence were collected.',
            'reason_kind': 'insufficient_evidence',
        },
    )

    assert result['ok'] is False
    assert result['error'] == 'fail_closed_requires_evidence'
    assert 'An empty draft or missing recipe artifact is not a semantic fail_closed reason.' in result['repair_hints']
    assert state.final_result is None


def test_pi_fail_closed_rejects_atlas_ready_without_saved_decision(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    atlas = state.handle_tool('build_bangumi_relation_atlas', {'anchor_subject_id': 100, 'max_subjects': 10})

    result = state.handle_tool(
        'fail_closed',
        {
            'reason': 'Concrete evidence block: atlas is ready but no stable mapped or supplemental row has been saved, recipe artifact does not exist, draft rule count is 0, and validation/submit cannot proceed.',
            'reason_kind': 'evidence_gap',
            'related_refs': [atlas['atlas_id']],
        },
    )

    assert result['ok'] is False
    assert result['error'] == 'fail_closed_requires_decision_or_concrete_evidence_gap'
    assert result['atlas_count'] == 1
    assert result['draft_rule_count'] == 0
    assert any('Save any stable target-surface judgment with upsert_recipe_group_decision_one' in hint for hint in result['repair_hints'])
    assert state.final_result is None


def test_pi_auto_fail_closed_can_record_runner_budget_exhausted(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.auto_fail_closed_no_final_result(reason='timeout')

    assert result['ok'] is True
    assert result['status'] == 'fail_closed'
    assert state.final_result is not None
    assert state.final_result['final_output']['fail_closed_reasons'][0]['reason_kind'] == 'budget_exhausted'


def test_pi_search_bangumi_subjects_adds_id_based_subject_context(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('search_bangumi_subjects', {'query': 'Uchuu Senkan Yamato 2202'})

    assert result['ok'] is True
    assert result['subjects'][0]['subject_id'] == 200
    assert result['subjects'][0]['subject_type'] == 'anime'
    assert 'factual anchors' in result['usage_hint']
    assert 'select_bangumi_anchor_subject' in result['usage_hint']
    assert 'not target recommendations' in result['context']['run_progress']['note']
    assert any(subject.ref == 'subject:200' for subject in state.workspace.bangumi_subjects)


def test_pi_expand_related_subjects_strictly_filters_relation_kind_before_subject_type(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('expand_related_subjects', {'subject_id': 100, 'max_subjects': 5})

    assert result['ok'] is True
    subjects = [row['subject'] for row in result['relations']]
    assert [subject['subject_id'] for subject in subjects] == [201]
    assert subjects[0]['subject_type'] == 'anime'
    assert result['relation_subjects'][0]['subject_id'] == 201
    assert any('skipped disallowed relation=manga' in skipped for skipped in result['skipped'])
    assert any('skipped non-anime subject_type=1' in skipped for skipped in result['skipped'])
    assert 'summary_short' not in result['relations'][0]['subject']
    assert 'compact series map fact surface' in result['usage_hint']


def test_pi_expand_related_subjects_can_filter_by_subject_type(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('expand_related_subjects', {'subject_id': 100, 'subject_types': ['anime'], 'max_subjects': 5})

    assert result['ok'] is True
    subjects = [row['subject'] for row in result['relations']]
    assert [subject['subject_id'] for subject in subjects] == [201]
    assert all(subject['subject_type'] == 'anime' for subject in subjects)


def test_pi_context_reports_run_progress_as_facts_not_recommendations(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    state.handle_tool('search_bangumi_subjects', {'query': 'Uchuu Senkan Yamato 2202'})
    state.handle_tool('get_episode_list', {'subject_id': 100, 'max_episode_cards': 2})
    context = state.handle_tool('get_case_context', {'detail': False})['data']

    progress = context['run_progress']
    assert progress['params_validation_seen'] is False
    assert progress['verifier_feedback_available'] is False
    assert progress['subject_evidence_call_count'] == 1
    assert progress['episode_evidence_call_count'] == 1
    assert progress['recent_tool_names'] == ['search_bangumi_subjects', 'get_episode_list']
    assert 'not target recommendations' in progress['note']
    assert 'next_step_hint' not in context


def test_pi_expand_related_graph_recurses_anime_subjects_without_semantic_scoring(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_RelationGraphBangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'expand_related_graph',
        {'subject_id': 100, 'subject_types': ['anime'], 'max_depth': 2, 'max_subjects': 10},
    )

    assert result['ok'] is True
    assert [subject['subject_id'] for subject in result['relation_subjects']] == [201, 202]
    assert {edge['to_subject_id'] for edge in result['edges']} == {201, 202}
    assert all(subject['subject_type'] == 'anime' for subject in result['relation_subjects'])
    assert result['traversal_status']['frontier_exhausted'] is False
    assert result['traversal_status']['stop_reason'] == 'depth_limit_reached'
    assert result['traversal_status']['seen_subject_ids'] == [100, 201, 202]
    assert result['traversal_status']['relation_checked_subject_ids'] == [100, 201]
    assert result['traversal_status']['next_subject_ids_to_expand'] == [202]
    assert any('skipped disallowed relation=book' in skipped for skipped in result['skipped'])
    assert any('skipped disallowed relation=角色出演' in skipped for skipped in result['skipped'])
    assert any('skipped non-anime subject_type=1' in skipped for skipped in result['skipped'])
    assert any(subject.subject_id == 202 for subject in state.workspace.bangumi_subjects)
    assert not any(subject.subject_id == 302 for subject in state.workspace.bangumi_subjects)
    assert not any(subject.subject_id == 401 for subject in state.workspace.bangumi_subjects)
    assert 'semantic' not in result['usage_hint'].casefold()
    assert 'next_subject_ids_to_expand' in result['usage_hint']
    assert 'not a recommendation' in result['usage_hint']


def test_pi_build_bangumi_relation_atlas_exhausts_anime_graph_and_hydrates_surfaces(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_RelationGraphBangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'build_bangumi_relation_atlas',
        {'anchor_subject_id': 100, 'max_subjects': 10, 'hydrate_episode_surfaces': True},
    )

    assert result['ok'] is True
    assert result['anchor_subject_id'] == 100
    assert result['subject_count'] == 3
    assert result['edge_count'] == 2
    assert result['traversal_status']['frontier_exhausted'] is True
    assert result['traversal_status']['stop_reason'] == 'frontier_exhausted'
    assert result['traversal_status']['seen_subject_ids'] == [100, 201, 202]
    assert any('skipped disallowed relation=book' in skipped for skipped in result['skipped'])
    assert any('skipped disallowed relation=角色出演' in skipped for skipped in result['skipped'])
    assert any('skipped non-anime subject_type=1' in skipped for skipped in result['skipped'])
    assert Path(result['atlas_path']).exists()
    assert Path(result['atlas_markdown_path']).exists()
    payload = json.loads(Path(result['atlas_path']).read_text(encoding='utf-8'))
    by_id = {subject['subject_id']: subject for subject in payload['subjects']}
    assert 302 not in by_id
    assert 401 not in by_id
    assert all(edge['relation'] != '角色出演' for edge in payload['edges'])
    assert by_id[100]['episode_surface']['row_surface_counts']['regular'] == 1
    assert by_id[202]['relation_path_text']
    assert 'summary_short' not in by_id[100]
    assert 'recommend' not in payload
    assert any(item.episode_id == 1001 for item in state.workspace.bangumi_items)
    progress = state.handle_tool('get_case_overview', {})['data']['run_progress']['bangumi_relation_atlas']
    assert progress['atlas_count'] == 1
    assert progress['latest_atlas_ids'] == [result['atlas_id']]


def test_pi_select_bangumi_anchor_subject_records_anchor_and_builds_atlas(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_RelationGraphBangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'select_bangumi_anchor_subject',
        {'anchor_subject_id': 100, 'reason': 'main TV anchor from first search result'},
    )

    assert result['ok'] is True
    assert result['status'] == 'anchor_atlas_ready'
    assert result['selected_anchor_subject_id'] == 100
    assert result['subject_count'] == 3
    assert result['traversal_status']['frontier_exhausted'] is True
    assert Path(result['atlas_path']).exists()
    assert Path(result['atlas_markdown_path']).exists()
    assert 'Python built the evidence atlas only' in result['anchor_selection_policy']
    assert 'atlas_result' not in result
    assert result['case_board_transaction']['anchor_atlas_bootstrap']['section_type'] == 'Board Delta'
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert 'main TV anchor from first search result' in notes
    progress = state.handle_tool('get_case_overview', {})['data']['run_progress']['bangumi_relation_atlas']
    assert progress['atlas_count'] == 1
    assert progress['latest_atlas_ids'] == [result['atlas_id']]
    assert state.tool_trace[-2]['result_summary']['selected_anchor_subject_id'] == 100
    assert state.tool_trace[-2]['result_summary']['bangumi_relation_atlas_id'] == result['atlas_id']


def test_pi_build_bangumi_relation_atlas_reports_subject_limit_guard(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_RelationGraphBangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'build_bangumi_relation_atlas',
        {'anchor_subject_id': 100, 'max_subjects': 2, 'hydrate_episode_surfaces': False},
    )

    assert result['ok'] is True
    assert result['traversal_status']['frontier_exhausted'] is False
    assert result['traversal_status']['stop_reason'] == 'subject_limit_reached'
    assert result['traversal_status']['seen_subject_ids'] == [100, 201]
    assert any('skipped over max_subjects' in skipped for skipped in result['skipped'])


def test_pi_case_input_exposes_recipe_scratch_paths_without_turn_cap(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    payload = state.case_input(pi_command='pi', timeout_seconds=300)

    assert 'max_turns' not in payload
    assert 'tool_boundary' not in payload
    assert payload['runtime_policy']['turn_cap_enabled'] is False
    assert payload['runtime_policy']['wall_clock_timeout_seconds'] == 300
    assert payload['runtime_policy']['suggested_finish_before_seconds'] == 285
    assert payload['runtime_policy']['finalization_buffer_seconds'] == 15
    assert payload['task_source_path'] == ''
    assert 'visible_source_paths' not in payload
    assert 'source_path' not in payload
    assert 'task_source_path is the original task/sample path' in payload['local_identity_policy']
    assert payload['scratch_paths']['artifacts_dir'] == str(tmp_path / 'run' / 'artifacts')
    assert payload['scratch_paths']['organize_recipe'] == str(tmp_path / 'run' / 'artifacts' / 'organize_recipe.json')
    assert payload['scratch_paths']['recipe_group_decisions'] == str(tmp_path / 'run' / 'artifacts' / 'recipe_group_decisions.json')
    assert payload['scratch_paths']['recipe_params_draft'] == str(tmp_path / 'run' / 'artifacts' / 'recipe_params_draft.json')
    assert payload['scratch_paths']['bangumi_relation_atlas_dir'] == str(tmp_path / 'run' / 'artifacts' / 'bangumi_relation_atlas')
    assert payload['scratch_paths']['notes'] == str(tmp_path / 'run' / 'artifacts' / 'notes.md')
    assert payload['scratch_paths']['helper_check'] == str(tmp_path / 'run' / 'artifacts' / 'organize_recipe_helper_check.json')
    assert 'Trial-check semantic params' in payload['tool_semantics']['validate_organize_recipe_params']
    assert 'group/subcluster semantic decisions' in payload['tool_semantics']['upsert_recipe_group_decision']
    assert 'working memory only' in payload['tool_semantics']['upsert_recipe_params_draft']
    assert 'coverage preview' in payload['tool_semantics']['get_recipe_params_draft']
    assert 'Validate the current draft only after' in payload['tool_semantics']['validate_recipe_params_draft']
    assert 'atomically build the relation atlas' in payload['tool_semantics']['select_bangumi_anchor_subject']
    assert 'fully traverse strict relation-filtered reachable Bangumi anime/video related subjects' in payload['tool_semantics']['build_bangumi_relation_atlas']
    assert 'prepare_bangumi_relation_atlas_scout_packets' not in payload['tool_semantics']
    assert 'prepare_bangumi_frontier_scout_packet' not in payload['tool_semantics']
    assert 'get_case_overview' in payload['tool_semantics']
    assert 'get_local_group_detail' in payload['tool_semantics']
    assert 'get_local_selector_scaffold' in payload['tool_semantics']
    assert payload['case_overview']['visible_file_count'] == 2
    assert payload['case_overview']['local_group_count'] == 1
    assert payload['case_overview']['overview_policy'].startswith('Case map only')
    assert payload['navigation']['navigation_policy'].startswith('Fixed navigation handles only')
    assert payload['context']['local_files'][0]['source_path'] == 'ep1.mkv'
    assert list(payload['context']['local_files'][0].keys()) == ['source_path']
    assert 'ref' not in payload['context']['local_files'][0]
    assert payload['run_progress']['params_validation_seen'] is False
    assert payload['run_progress']['recipe_group_decisions']['exists'] is False
    assert payload['run_progress']['recipe_group_decisions']['decision_count'] == 0
    assert payload['run_progress']['recipe_params_draft']['exists'] is False
    assert payload['run_progress']['recipe_params_draft']['rule_count'] == 0
    assert payload['context']['run_progress']['verifier_feedback_available'] is False
    assert 'not target recommendations' in payload['run_progress']['note']
    assert 'case_quick_start' not in payload
    assert 'local_structure_summary' not in payload
    assert 'local_recipe_skeleton' not in payload
    assert 'local_recipe_params_scaffold' not in payload
    assert 'get_local_recipe_params_scaffold' in payload['tool_semantics']
    assert 'case_overview' in payload['context']['startup_evidence_locations']
    assert 'local_selector_scaffold' in payload['context']['startup_evidence_locations']
    assert 'bangumi_relation_atlas' in payload['context']['startup_evidence_locations']
    assert 'recipe_group_decisions' in payload['context']['startup_evidence_locations']
    assert 'recipe_params_draft' in payload['context']['startup_evidence_locations']
    assert 'early_bangumi_evidence_bundle' not in payload
    assert 'early_bangumi_evidence_bundle' not in payload['context']['startup_evidence_locations']
    overview_tool = state.handle_tool('get_case_overview', {})['data']
    assert overview_tool['local_group_index'][0]['group_ref'] == 'LG1'
    assert 'not recommend which group' in overview_tool['overview_policy']
    context_tool = state.handle_tool('get_case_context', {'detail': False})['data']
    assert context_tool['local_group_index']['group_count'] >= 1
    assert context_tool['case_overview']['local_group_count'] == 1
    assert context_tool['run_progress']['tool_call_count'] == 1
    assert 'early_bangumi_evidence_bundle' not in context_tool


def test_pi_case_board_tools_append_and_read_notes_tail(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    notes_path = tmp_path / 'run' / 'artifacts' / 'notes.md'

    missing = state.handle_tool('get_case_board_notes', {'mode': 'tail'})
    assert missing['ok'] is True
    assert missing['exists'] is False
    assert missing['content'] == ''

    first = state.handle_tool(
        'append_case_board_note',
        {
            'section_type': 'Initial Board',
            'content': 'LG1 | local: TV 01-02 | evidence: none yet | rule: pending | open: anchor main',
            'next_action': 'search main title',
        },
    )
    second = state.handle_tool(
        'append_case_board_note',
        {
            'section_type': 'Validation Snapshot',
            'content': {'LG1': 'ready: mapped sequence -> subject 200'},
            'next_action': 'validate_organize_recipe_params',
        },
    )

    assert first['ok'] is True
    assert second['ok'] is True
    assert second['board_next_action']['next_tool'] == 'validate_organize_recipe_params'
    assert 'Validation Snapshot is committed' in second['board_next_action']['instruction']
    assert first['path'] == str(notes_path)
    assert second['path'] == str(notes_path)
    text = notes_path.read_text(encoding='utf-8')
    assert text.count('## Initial Board') == 1
    assert text.count('## Validation Snapshot') == 1
    assert 'search main title' in text
    assert 'validate_organize_recipe_params' in text
    assert text.index('## Initial Board') < text.index('## Validation Snapshot')

    tail = state.handle_tool('get_case_board_notes', {'mode': 'tail', 'max_chars': 120})
    assert tail['ok'] is True
    assert tail['exists'] is True
    assert tail['mode'] == 'tail'
    assert tail['truncated'] is True
    assert 'Validation Snapshot' in tail['content']

    latest = state.handle_tool('get_case_board_notes', {'mode': 'latest'})
    assert latest['ok'] is True
    assert latest['content'].startswith('## Validation Snapshot')
    assert 'LG1' in latest['content']
    assert 'Initial Board' not in latest['content']


def test_pi_case_board_rejects_large_json_like_notes(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    notes_path = tmp_path / 'run' / 'artifacts' / 'notes.md'

    result = state.handle_tool(
        'append_case_board_note',
        {
            'section_type': 'Initial Board',
            'content': {'groups': [{'group_ref': f'LG{index}', 'source_paths': ['x' * 120]} for index in range(30)]},
        },
    )

    assert result['ok'] is False
    assert result['error'] == 'case_board_note_too_large'
    assert result['content_chars'] > result['max_content_chars']
    assert any('Do not paste local group' in hint for hint in result['repair_hints'])
    assert not notes_path.exists()


def test_pi_recipe_params_draft_upsert_overwrite_remove_and_board_delta(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    draft_path = tmp_path / 'run' / 'artifacts' / 'recipe_params_draft.json'

    created = state.handle_tool(
        'upsert_recipe_params_draft',
        {
            'summary': 'incremental draft',
            'board_delta': 'LG1 judged as TV sequence; save draft row.',
            'rules': [
                {
                    'name': 'LG1 TV',
                    'group_ref': 'LG1',
                    'subject_id': 100,
                    'media_kind': 'tv',
                    'episode_type': 'regular',
                    'reason': 'Bangumi rows 1-2 match local files.',
                }
            ],
        },
    )

    assert created['ok'] is True
    assert created['draft_updated'] is True
    assert created['rule_count'] == 1
    assert created['ready_for_full_validation'] is True
    assert created['coverage_preview']['covered_group_refs'] == ['LG1']
    assert created['coverage_preview']['missing_group_refs'] == []
    assert created['board_delta']['section_type'] == 'Board Delta'
    assert draft_path.exists()

    overwritten = state.handle_tool(
        'upsert_recipe_params_draft',
        {
            'rules': {
                'name': 'LG1 TV',
                'group_ref': 'LG1',
                'disposition': 'non_bangumi_or_supplemental',
                'reason': 'temporary replacement for test',
            }
        },
    )

    assert overwritten['ok'] is True
    assert overwritten['rule_count'] == 1
    detail = state.handle_tool('get_recipe_params_draft', {'detail': True})
    assert detail['recipe_params_draft']['rules'][0]['disposition'] == 'non_bangumi_or_supplemental'

    removed = state.handle_tool('upsert_recipe_params_draft', {'remove_rule_names': ['LG1 TV']})

    assert removed['ok'] is True
    assert removed['rule_count'] == 0
    assert removed['coverage_preview']['missing_group_refs'] == ['LG1']


def test_pi_recipe_params_draft_group_ref_coverage_uses_full_internal_paths_when_skeleton_is_truncated(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File(f'f{number:02d}', f'Long Show - {number:02d}.mkv', f'Long Show/Long Show - {number:02d}.mkv')
            for number in range(1, 19)
        ],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    skeleton_group = state.handle_tool('list_local_groups', {'detail': True})['data']['groups'][0]

    assert skeleton_group['group_ref'] == 'LG1'
    assert skeleton_group['source_path_count'] == 18
    assert len(skeleton_group['source_paths']['sample']) == 12
    assert 'all' not in skeleton_group['source_paths']
    assert skeleton_group['source_paths']['omitted_count'] == 6

    detail_group = state.handle_tool('get_local_group_detail', {'group_ref': 'LG1', 'detail': True})['data']

    assert len(detail_group['source_paths']['all']) == 18
    assert len(detail_group['local_files']) == 18

    created = state.handle_tool(
        'upsert_recipe_params_draft',
        {
            'rules': [
                {
                    'name': 'LG1 long sequence',
                    'group_ref': 'LG1',
                    'subject_id': 100,
                    'media_kind': 'tv',
                    'episode_type': 'regular',
                    'reason': 'test target; coverage preview should use full internal LG1 paths.',
                }
            ],
        },
    )

    assert created['ok'] is True
    coverage = created['coverage_preview']
    assert coverage['covered_group_refs'] == ['LG1']
    assert coverage['missing_group_refs'] == []
    assert coverage['covered_path_count'] == 18
    assert coverage['visible_path_count'] == 18
    assert coverage['uncovered_path_count'] == 0
    assert coverage['ready_for_full_validation'] is True


def test_pi_recipe_params_draft_flags_skeletal_rows_before_validation(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    created = state.handle_tool(
        'upsert_recipe_params_draft',
        {
            'rules': [
                {
                    'name': 'LG1 unfinished row',
                    'group_ref': 'LG1',
                }
            ],
        },
    )

    assert created['ok'] is True
    assert created['rule_count'] == 1
    assert created['coverage_preview']['local_coverage_complete'] is True
    assert created['ready_for_full_validation'] is False
    assert created['draft_quality_issue_count'] == 1
    assert 'draft_quality_issues' not in created
    issue = created['coverage_preview']['draft_quality_issues'][0]
    assert issue['rule_name'] == 'LG1 unfinished row'
    assert issue['issue_code'] == 'missing_bangumi_target_or_supplemental_disposition'

    progress = state.handle_tool('get_local_group_detail', {'group_ref': 'LG1', 'detail': False})
    assert progress['recipe_params_draft_next_action']['next_tool'] == 'upsert_recipe_group_decision_one'
    assert progress['recipe_params_draft_next_action']['draft_quality_issue_count'] == 1
    assert progress['recipe_params_draft_next_action']['affected_rule_names'] == ['LG1 unfinished row']

    result = state.handle_tool('validate_recipe_params_draft', {'validation_snapshot': 'should not be written'})

    assert result['ok'] is False
    assert result['status'] == 'draft_quality_incomplete'
    assert result['accepted'] is False
    assert result['draft_quality_issues'][0]['issue_code'] == 'missing_bangumi_target_or_supplemental_disposition'
    assert not (tmp_path / 'run' / 'artifacts' / 'organize_recipe.json').exists()
    assert not (tmp_path / 'run' / 'artifacts' / 'notes.md').exists()


def test_pi_recipe_group_decision_compiles_to_params_draft_and_validates(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'upsert_recipe_group_decision_one',
        {
            'summary': 'decision workpaper',
            'board_delta': 'LG1 mapped to subject 100.',
            'decision': {
                'name': 'LG1 TV decision',
                'group_ref': 'LG1',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'reason': 'local episode numbers match Bangumi sort 1-2.',
            },
        },
    )

    assert result['ok'] is True
    assert result['one_row_decision_tool_used'] is True
    assert result['decision_count'] == 1
    assert result['compiled_recipe_params_draft']['ready_for_full_validation'] is True
    assert (tmp_path / 'run' / 'artifacts' / 'recipe_group_decisions.json').exists()
    draft = state.handle_tool('get_recipe_params_draft', {'detail': True})
    assert draft['recipe_params_draft']['compiled_from_group_decisions'] is True
    assert draft['recipe_params_draft']['rules'][0]['group_ref'] == 'LG1'

    validated = state.handle_tool('validate_recipe_params_draft', {'validation_snapshot': 'LG1 decision ready'})

    assert validated['ok'] is True
    assert validated['accepted'] is True
    assert validated['validated_from_recipe_params_draft'] is True
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert 'LG1 mapped to subject 100' in notes
    assert 'LG1 decision ready' in notes


def test_pi_recipe_group_decision_selects_subcluster_and_requires_remaining_paths(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'upsert_recipe_group_decision_one',
        {
            'decision': {
                'name': 'LG1 first file exact',
                'group_ref': 'LG1',
                'file_numbers': [1],
                'episode_id': 1001,
                'subject_id': 100,
                'media_kind': 'tv',
                'reason': 'first file maps exactly.',
            },
        },
    )

    assert result['ok'] is True
    compiled = result['compiled_recipe_params_draft']
    assert compiled['rule_count'] == 1
    assert compiled['ready_for_full_validation'] is False
    coverage = compiled['coverage_preview']
    assert coverage['covered_group_refs'] == ['LG1']
    assert coverage['uncovered_path_count'] == 1
    assert 'ep2.mkv' in coverage['uncovered_path_sample'][0]

    result = state.handle_tool(
        'upsert_recipe_group_decision_one',
        {
            'decision': {
                'name': 'LG1 second file supplemental',
                'group_ref': 'LG1',
                'file_numbers': [2],
                'disposition': 'non_bangumi_or_supplemental',
                'reason': 'second file intentionally left supplemental for test.',
            },
        },
    )

    assert result['compiled_recipe_params_draft']['ready_for_full_validation'] is True
    detail = state.handle_tool('get_recipe_group_decisions', {'detail': True})
    assert detail['decision_count'] == 2
    assert detail['recipe_params_draft']['rules'][1]['disposition'] == 'non_bangumi_or_supplemental'


def test_pi_recipe_group_decision_rejects_large_batch_before_writing(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'upsert_recipe_group_decision',
        {
            'decisions': [
                {
                    'name': f'row {index}',
                    'group_ref': 'LG1',
                    'file_numbers': [1],
                    'disposition': 'non_bangumi_or_supplemental',
                    'reason': 'test row.',
                }
                for index in range(6)
            ],
        },
    )

    assert result['ok'] is False
    assert result['error'] == 'too_many_group_decisions_for_one_call'
    assert result['decision_count'] == 6
    assert any('upsert_recipe_group_decision_one' in hint for hint in result['repair_hints'])
    assert not (tmp_path / 'run' / 'artifacts' / 'recipe_group_decisions.json').exists()


def test_pi_recipe_group_decision_one_rejects_long_reason(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'upsert_recipe_group_decision_one',
        {
            'decision': {
                'name': 'LG1 long reason',
                'group_ref': 'LG1',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'reason': 'This reason repeats detailed evidence. ' * 20,
            }
        },
    )

    assert result['ok'] is False
    assert result['error'] == 'group_decision_reason_too_long'
    assert result['reason_chars'] > result['max_reason_chars']
    assert not (tmp_path / 'run' / 'artifacts' / 'recipe_group_decisions.json').exists()


def test_pi_recipe_group_decision_rejects_many_exact_paths_with_selector_hint(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File(f'f{index}', f'Show - {index:02d}.mkv', f'Show/Show - {index:02d}.mkv') for index in range(1, 6)],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'upsert_recipe_group_decision_one',
        {
            'decision': {
                'name': 'too many exact paths',
                'exact_paths': [f'Show/Show - {index:02d}.mkv' for index in range(1, 6)],
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'reason': 'selector should be compact.',
            }
        },
    )

    assert result['ok'] is False
    assert result['error'] == 'group_decision_exact_paths_too_many'
    assert result['exact_path_count'] == 5
    assert any('group_ref plus file_numbers' in hint for hint in result['repair_hints'])
    assert not (tmp_path / 'run' / 'artifacts' / 'recipe_group_decisions.json').exists()


def test_pi_recipe_group_decision_rejects_plural_subject_targets(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'upsert_recipe_group_decision_one',
        {
            'decision': {
                'name': 'two movies in one row',
                'group_ref': 'LG1',
                'file_numbers': [1, 2],
                'target_subject_ids': [100, 200],
                'media_kind': 'movie',
                'reason': 'two targets must be split into two decision rows.',
            }
        },
    )

    assert result['ok'] is False
    assert 'non-canonical field(s)' in result['error']
    assert 'target_subject_ids' in result['error']
    assert result['repair_hints']
    assert not (tmp_path / 'run' / 'artifacts' / 'recipe_group_decisions.json').exists()


def test_pi_recipe_group_decision_preserves_compact_group_range_selector(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File(f'f{index}', f'Show - {index:02d}.mkv', f'Show/Show - {index:02d}.mkv') for index in range(1, 6)],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'upsert_recipe_group_decision_one',
        {
            'decision': {
                'name': 'LG1 compact range',
                'group_ref': 'LG1',
                'file_number_range': '1-5',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'reason': 'compact range selector.',
            }
        },
    )

    assert result['ok'] is True
    draft = state.handle_tool('get_recipe_params_draft', {'detail': True})['recipe_params_draft']
    rule = draft['rules'][0]
    assert rule['group_ref'] == 'LG1'
    assert 'source_pattern' in rule
    assert rule['episode_range'] == '1-5'
    assert 'exact_paths' not in rule


def test_pi_recipe_params_preserves_explicit_source_pattern_with_group_ref(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File(f'f{index}', f'Show SP{index:02d}.mkv', f'Show/SPs/Show SP{index:02d}.mkv') for index in range(1, 3)],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )
    explicit_pattern = 'Show/SPs/Show SP{ep:02}.mkv'

    result = state.handle_tool(
        'upsert_recipe_group_decision_one',
        {
            'decision': {
                'name': 'LG1 explicit pattern',
                'group_ref': 'LG1',
                'source_pattern': explicit_pattern,
                'episode_range': '1-2',
                'subject_id': 100,
                'media_kind': 'tv',
                'episode_type': 'regular',
                'reason': 'explicit selector should survive group_ref defaults.',
            }
        },
    )

    assert result['ok'] is True
    validated = state.handle_tool('validate_recipe_params_draft', {'validation_snapshot': 'LG1 explicit pattern'})
    recipe = json.loads((tmp_path / 'run' / 'artifacts' / 'organize_recipe.json').read_text(encoding='utf-8'))
    rule = recipe['rules'][0]
    assert rule['select']['filename_regex'] == r'Show/SPs/Show\ SP(?P<ep>\d{2})\.mkv'
    assert rule['select']['exact_paths'] == []
    assert rule['episode']['range'] == '1-2'
    assert validated['ok'] is True


def test_pi_validate_recipe_params_draft_refuses_incomplete_without_writing_recipe(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('a1', 'Show A - 01.mkv', 'A/Show A - 01.mkv'),
            _File('a2', 'Show A - 02.mkv', 'A/Show A - 02.mkv'),
            _File('b1', 'Show B - 01.mkv', 'B/Show B - 01.mkv'),
        ],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )
    groups = state.handle_tool('list_local_groups', {'detail': False})['data']['groups']
    first_group = groups[0]['group_ref']

    state.handle_tool(
        'upsert_recipe_params_draft',
        {
            'rules': [
                {
                    'name': 'first group supplemental',
                    'group_ref': first_group,
                    'disposition': 'non_bangumi_or_supplemental',
                    'reason': 'test only',
                }
            ]
        },
    )
    result = state.handle_tool('validate_recipe_params_draft', {'validation_snapshot': 'should not be written'})

    assert result['ok'] is False
    assert result['status'] == 'draft_incomplete'
    assert result['accepted'] is False
    assert first_group not in result['missing_group_refs']
    assert result['missing_group_refs']
    assert not (tmp_path / 'run' / 'artifacts' / 'organize_recipe.json').exists()
    assert not (tmp_path / 'run' / 'artifacts' / 'notes.md').exists()


def test_pi_validate_recipe_params_draft_complete_runs_full_validation_and_writes_snapshot(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    state.handle_tool(
        'upsert_recipe_params_draft',
        {
            'rules': [
                {
                    'name': 'LG1 TV',
                    'group_ref': 'LG1',
                    'subject_id': 100,
                    'media_kind': 'tv',
                    'episode_type': 'regular',
                    'reason': 'Bangumi rows 1-2 match local files.',
                }
            ]
        },
    )
    result = state.handle_tool('validate_recipe_params_draft', {'validation_snapshot': {'LG1': 'ready from draft'}})

    assert result['ok'] is True
    assert result['accepted'] is True
    assert result['validated_from_recipe_params_draft'] is True
    assert result['coverage_preview']['ready_for_full_validation'] is True
    assert state.final_result is None
    assert (tmp_path / 'run' / 'artifacts' / 'organize_recipe.json').exists()
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert '## Validation Snapshot' in notes
    assert 'ready from draft' in notes


def test_pi_validate_recipe_params_draft_invalid_auto_records_verifier_delta(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    state.handle_tool(
        'upsert_recipe_params_draft',
        {
            'rules': [
                {
                    'name': 'bad fixed target',
                    'group_ref': 'LG1',
                    'subject_id': 100,
                    'media_kind': 'tv',
                    'episode_id': 1001,
                    'reason': 'bad test rule fixes both local files to one episode.',
                }
            ]
        },
    )
    result = state.handle_tool('validate_recipe_params_draft', {'validation_snapshot': {'LG1': 'bad fixed target'}})

    assert result['ok'] is True
    assert result['accepted'] is False
    assert result['status'] == 'invalid'
    assert result['validated_from_recipe_params_draft'] is True
    assert result['case_board_transaction']['validation_snapshot']['section_type'] == 'Validation Snapshot'
    assert result['case_board_transaction']['verifier_delta']['section_type'] == 'Verifier Delta'
    assert any(issue['issue_code'] == 'duplicate_target' for issue in result['verifier_result']['issues'])
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert notes.index('## Validation Snapshot') < notes.index('## Verifier Delta')


def test_pi_params_patch_before_validation_updates_recipe_params_draft(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('f1', 'ep1.mkv', 'ep1.mkv'),
            _File('f2', 'ep2.mkv', 'ep2.mkv'),
        ],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )
    state.handle_tool(
        'upsert_recipe_params_draft',
        {
            'rules': [
                {
                    'name': 'episode 1 only',
                    'exact_paths': ['ep1.mkv'],
                    'disposition': 'non_bangumi_or_supplemental',
                    'reason': 'first draft row',
                }
            ]
        },
    )

    patched = state.handle_tool(
        'validate_organize_recipe_params_patch',
        {
            'detail': True,
            'patch_delta': 'Add the uncovered second file to the draft row before first validation.',
            'recipe_params_patch': {
                'patch_rules': [
                    {'name': 'episode 1 only', 'updates': {'exact_paths': ['ep1.mkv', 'ep2.mkv']}},
                ],
            },
        },
    )

    assert patched['ok'] is True
    assert patched['accepted'] is False
    assert patched['status'] == 'draft_patch_applied'
    assert patched['params_patch_applied_to_recipe_params_draft'] is True
    assert patched['ready_for_full_validation'] is True
    assert patched['next_tool'] == 'validate_recipe_params_draft'
    assert not (tmp_path / 'run' / 'artifacts' / 'recipe_params.json').exists()
    detail = state.handle_tool('get_recipe_params_draft', {'detail': True})
    assert detail['recipe_params_draft']['rules'][0]['exact_paths'] == ['ep1.mkv', 'ep2.mkv']
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert '## Patch Delta' in notes
    assert 'uncovered second file' in notes


def test_pi_progress_tools_hint_to_save_partial_recipe_params_draft(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = {}
    for _ in range(6):
        result = state.handle_tool('get_local_group_detail', {'group_ref': 'LG1', 'detail': False})

    assert result['ok'] is True
    assert result['recipe_params_draft_next_action']['next_tool'] == 'upsert_recipe_group_decision_one'
    assert 'Partial workpaper rows are for stable judgments only.' in result['recipe_params_draft_next_action']['partial_draft_policy']
    trace_tail = state.tool_trace[-1]['result_summary']
    assert trace_tail['recipe_params_draft_next_tool'] == 'upsert_recipe_group_decision_one'


def test_pi_evidence_tools_keep_returning_evidence_after_unpersisted_search(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    for _ in range(10):
        state.handle_tool('search_bangumi_subjects', {'query': 'OVERLORD', 'max_subjects': 1})

    result = state.handle_tool('get_episode_list', {'subject_id': 1001})

    assert result['ok'] is True
    assert result.get('status') != 'workpaper_checkpoint_required'
    assert result.get('no_new_evidence_returned') is not True
    assert result['recipe_params_draft_next_action']['next_tool'] == 'upsert_recipe_group_decision_one'
    assert 'Do not write uncertain duplicate/supplemental rows just to make draft coverage grow.' in result['recipe_params_draft_next_action']['partial_draft_policy']
    assert state.tool_trace[-1]['result_summary']['recipe_params_draft_next_tool'] == 'upsert_recipe_group_decision_one'

    state.handle_tool(
        'append_case_board_note',
        {
            'section_type': 'Board Delta',
            'content': 'No group is decidable yet; need one exact episode row for the current blocker.',
            'next_action': 'fetch one exact row',
        },
    )
    allowed = state.handle_tool('get_episode_list', {'subject_id': 1001})

    assert allowed['ok'] is True
    assert allowed.get('status') != 'workpaper_checkpoint_required'


def test_pi_evidence_batch_requires_workpaper_checkpoint_before_more_evidence(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    for _ in range(state._EVIDENCE_BATCH_LIMIT):
        result = state.handle_tool('search_bangumi_subjects', {'query': 'OVERLORD', 'max_subjects': 1})
        assert result['ok'] is True

    result = state.handle_tool('get_episode_list', {'subject_id': 1001})

    assert result['ok'] is False
    assert result['error'] == 'evidence_batch_checkpoint_required'
    assert result['workpaper_checkpoint']['next_tools'][0] == 'upsert_recipe_group_decision_one'
    assert 'subagent' not in result['workpaper_checkpoint']['next_tools']
    assert 'prepare_bangumi_frontier_scout_packet' not in result['workpaper_checkpoint']['next_tools']
    assert 'prepare_bangumi_relation_atlas_scout_packets' not in result['workpaper_checkpoint']['next_tools']
    assert result['workpaper_checkpoint']['evidence_calls_since_workpaper'] == state._EVIDENCE_BATCH_LIMIT
    assert 'Python is not choosing a Bangumi target' in result['workpaper_checkpoint']['policy']
    assert state.tool_trace[-1]['result_summary']['workpaper_checkpoint_next_tool'] == 'upsert_recipe_group_decision_one'
    assert state.tool_trace[-1]['result_summary']['evidence_calls_since_workpaper'] == state._EVIDENCE_BATCH_LIMIT

    board = state.handle_tool(
        'append_case_board_note',
        {
            'section_type': 'Board Delta',
            'content': 'Still no stable row; need one exact episode row for LG2 only.',
            'next_action': 'fetch LG2 exact row',
        },
    )
    allowed = state.handle_tool('get_episode_list', {'subject_id': 1001})

    assert board['ok'] is True
    assert allowed['ok'] is True


def test_pi_evidence_tools_keep_returning_evidence_after_partial_draft_stalls(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('f1', 'Season 1/ep1.mkv', 'Season 1/ep1.mkv'),
            _File('f2', 'Season 2/ep1.mkv', 'Season 2/ep1.mkv'),
        ],
    )
    workspace = _build_workspace(
        local_evidence=local,
        bangumi_contexts=[
            {
                'context': {
                    'episode_structure': {
                        'subject_id': 100,
                        'title': 'Season 1',
                        'episodes': [
                            {'episode_id': 1001, 'title': 'Episode 1', 'sort': 1, 'ep': 1, 'kind': 'regular'},
                        ],
                    },
                },
            }
        ],
    )
    state = PiCaseToolState(workspace=workspace, bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    saved = state.handle_tool(
        'upsert_recipe_group_decision',
        {
            'decisions': [
                {
                    'name': 'S1',
                    'group_ref': 'LG1',
                    'subject_id': 100,
                    'media_kind': 'tv',
                    'episode_type': 'regular',
                    'reason': 'stable first group',
                }
            ]
        },
    )
    assert saved['ok'] is True
    draft = state.handle_tool('get_recipe_params_draft', {'detail': False})
    assert draft['coverage_preview']['missing_group_refs'] == ['LG2']

    for _ in range(6):
        allowed = state.handle_tool('search_bangumi_subjects', {'query': 'OVERLORD', 'max_subjects': 1})
        assert allowed['ok'] is True
        assert allowed.get('status') != 'workpaper_checkpoint_required'

    result = state.handle_tool('get_episode_list', {'subject_id': 100})

    assert result['ok'] is True
    assert result.get('status') != 'workpaper_checkpoint_required'
    assert result.get('no_new_evidence_returned') is not True
    assert result['recipe_params_draft_next_action']['next_tool'] == 'upsert_recipe_params_draft'
    assert result['recipe_params_draft_next_action']['missing_group_refs'] == ['LG2']
    assert 'still needs a side-surface check' in result['recipe_params_draft_next_action']['reason']
    assert 'upsert_recipe_params_draft' in result['recipe_params_draft_next_action']['allowed_next_tools']


def test_pi_validate_params_returns_case_board_next_action_for_repair(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'validation_snapshot': {'LG1': 'ready: supplemental exact path missing.mkv'},
            'recipe_params': {
                'rules': [
                    {
                        'name': 'missing exact supplemental',
                        'exact_paths': ['missing.mkv'],
                        'disposition': 'non_bangumi_or_supplemental',
                        'reason': 'test missing path',
                    }
                ]
            }
        },
    )

    assert result['ok'] is True
    assert result['accepted'] is False
    assert result['status'] == 'invalid'
    assert result['case_board_next_action']['section_type'] == 'Verifier Delta'
    assert result['case_board_next_action']['next_tool'] == 'validate_organize_recipe_params_patch'
    assert 'Patch blocking verifier issues first' in result['case_board_next_action']['instruction']
    assert result['case_board_next_action']['blocking_issue_count'] >= 1
    assert result['case_board_transaction']['validation_snapshot']['section_type'] == 'Validation Snapshot'
    assert result['case_board_transaction']['verifier_delta']['section_type'] == 'Verifier Delta'
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    assert notes.index('## Validation Snapshot') < notes.index('## Verifier Delta')
    assert 'Mechanical verifier/review feedback only' in notes


def test_pi_submit_params_transaction_writes_submit_snapshot_before_final_result(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'submit_organize_recipe_params',
        {
            'detail': True,
            'submit_snapshot': {'LG1': 'ready: mapped TV 1-2 -> subject 100 regular'},
            'recipe_params': {
                'rules': [
                    {
                        'name': 'group shorthand',
                        'group_ref': 'LG1',
                        'subject_id': 100,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'reason': 'Bangumi subject evidence supports this local group.',
                    },
                ]
            },
            'summary': 'accepted transaction submit',
        },
    )

    assert result['accepted'] is True
    assert result['case_board_transaction']['submit_snapshot']['section_type'] == 'Submit Snapshot'
    assert state.final_result['status'] == 'accepted'
    notes = (tmp_path / 'run' / 'artifacts' / 'notes.md').read_text(encoding='utf-8')
    final_path = tmp_path / 'run' / 'final_result.json'
    assert notes.index('## Submit Snapshot') >= 0
    assert final_path.exists()


def test_pi_case_input_exposes_factual_local_structure_groups(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('a1', 'Show A - 01.mkv', 'Season A/Show A - 01.mkv'),
            _File('a2', 'Show A - 02.mkv', 'Season A/Show A - 02.mkv'),
            _File('b1', 'Show B - 01.mkv', 'Season B/Show B - 01.mkv'),
            _File('b2', 'Show B - 02.mkv', 'Season B/Show B - 02.mkv'),
            _File('m1', 'Show Movie [01].mkv', 'Movies/Show Movie [01].mkv'),
        ],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    summary = state.handle_tool('get_case_context', {'detail': True})['data']['local_structure_summary']

    assert summary['visible_file_count'] == 5
    assert summary['folder_count'] == 3
    by_folder = {group['folder']: group for group in summary['folder_groups']}
    assert by_folder['Season A']['file_count'] == 2
    assert by_folder['Season A']['prefix_groups'][0]['prefix'] == 'Show A'
    assert by_folder['Season A']['prefix_groups'][0]['first_locator_numbers']['integer_ranges'] == ['1-2']
    assert by_folder['Movies']['content_shape_token_counts']['MOVIE'] == 2
    repeated = {row['first_locator']: row for row in summary['repeated_numbering_starts']}
    assert repeated['01']['folder_count'] == 3
    assert 'semantic mapping decision' in summary['summary_policy']


def test_pi_case_input_exposes_recipe_skeleton_groups_and_selector_previews(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('s1e1', 'Show [01][x265_2flac].mkv', 'Show [01][x265_2flac].mkv'),
            _File('s1e1v2', 'Show [01v2][x265_2flac].mkv', 'Show [01v2][x265_2flac].mkv'),
            _File('s1e2', 'Show [02][x265_flac].mkv', 'Show [02][x265_flac].mkv'),
            _File('s2e1', 'Show II [01].mkv', 'Show II [01].mkv'),
            _File('s3e1', 'Show III [01].mkv', 'Show III [01].mkv'),
            _File('s3e2', 'Show III [02].mkv', 'Show III [02].mkv'),
            _File('lecture1', 'Show - 極東なるほど講座 [01].mkv', 'SPs/Show - 極東なるほど講座 [01].mkv'),
            _File('sp1', 'Show [SP01].mkv', 'SPs/Show [SP01].mkv'),
        ],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    skeleton = state.handle_tool('list_local_groups', {'detail': True})['data']
    scaffold = state.handle_tool('get_local_recipe_params_scaffold', {'detail': True})['data']

    assert skeleton['visible_file_count'] == 8
    assert 'not choose Bangumi subject_id' in skeleton['skeleton_policy']
    groups = skeleton['groups']
    show_group = next(group for group in groups if group['title_hint'] == 'Show' and group['folder'] == 'tests/sample')
    assert show_group['source_path_count'] == 3
    assert show_group['number_summary']['integer_ranges'] == ['1-2']
    assert '{ver}' in show_group['selector_hint']['source_pattern']
    assert show_group['selector_hint']['coverage_preview']['safe'] is True
    assert 'duplicate_episode_numbers_in_group' in show_group['boundary_warnings']
    assert any('technical tokens vary' in note for note in show_group['variation_notes'])
    assert any('version suffixes' in note for note in show_group['variation_notes'])
    assert any(group['title_hint'] == 'Show II' for group in groups)
    lecture_group = next(group for group in groups if '極東なるほど講座' in group['title_hint'])
    assert lecture_group['folder'] == 'SPs'
    sp_group = next(group for group in groups if group['locator_kind_hint'] == 'special')
    assert sp_group['representative_source_path'] == 'SPs/Show [SP01].mkv'
    sp_detail = state.handle_tool('get_local_group_detail', {'group_ref': sp_group['group_ref'], 'detail': True})['data']
    assert sp_detail['source_paths']['all'] == ['SPs/Show [SP01].mkv']
    assert sp_detail['local_files'][0]['source_path'] == 'SPs/Show [SP01].mkv'
    assert scaffold['visible_file_count'] == 8
    assert 'does not choose Bangumi subject_id' in scaffold['scaffold_policy']
    scaffold_group = next(group for group in scaffold['groups'] if group['title_hint'] == 'Show' and group['source_path_count'] == 3)
    assert scaffold_group['params_rule_stub']['group_ref'] == scaffold_group['group_ref']
    assert scaffold_group['params_rule_stub']['episode_range'] == '1-2'
    unique_scaffold_group = next(group for group in scaffold['groups'] if group['title_hint'] == 'Show III')
    assert unique_scaffold_group['params_rule_stub']['episode_range'] == '1-2'
    assert unique_scaffold_group['params_rule_stub']['episode_offset'] == 'EP'
    assert scaffold_group['target_fields_for_mapped_rule']
    assert scaffold_group['supplemental_fields_if_evidence_does_not_support_mapping']['disposition'] == 'non_bangumi_or_supplemental'
    tool_payload = state.handle_tool('get_local_recipe_params_scaffold', {'detail': False})['data']
    assert tool_payload['group_count'] == scaffold['group_count']
    assert 'scaffold_policy' in tool_payload
    single_scaffold = state.handle_tool('get_local_selector_scaffold', {'group_ref': unique_scaffold_group['group_ref']})['data']
    assert single_scaffold['group']['params_rule_stub']['episode_range'] == '1-2'


def test_pi_case_input_keeps_bangumi_evidence_out_of_startup_payload(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('s1e1', 'Show [01].mkv', 'Show [01].mkv'),
            _File('s1e2', 'Show [02].mkv', 'Show [02].mkv'),
            _File('s2e1', 'Show II [01].mkv', 'Show II [01].mkv'),
            _File('sp1', 'Show [SP01].mkv', 'SPs/Show [SP01].mkv'),
        ],
    )
    client = _CountingBangumiClient()
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=client,
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
        source_path='tests/sample_pool/raw/tv/sample_0096_vcb_studio_overlord.json',
    )

    payload = state.case_input(timeout_seconds=300)
    search_count_after_case_input = len(client.search_queries)
    context = state.handle_tool('get_case_context', {'detail': False})['data']

    assert 'early_bangumi_evidence_bundle' not in payload
    assert 'early_bangumi_evidence_bundle' not in context
    assert 'case_quick_start' not in payload
    assert payload['case_overview']['local_group_index']
    assert 'local_recipe_skeleton' not in payload
    assert 'local_recipe_params_scaffold' not in payload
    assert len(client.search_queries) == search_count_after_case_input
    assert client.search_queries == []
    assert client.related_subject_ids == []
    assert client.episode_subject_ids == []
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert 'candidate_subjects' not in payload_text
    assert 'subject_episode_windows' not in payload_text
    assert 'mapping_decision' not in payload_text


def test_pi_target_helper_rejects_non_visible_source_path_with_visible_path_hint(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': 'tests/sample_pool/raw/sample.json',
            'title_query': 'Test',
            'kind_hint': 'tv',
        },
    )

    assert result['ok'] is False
    assert result['visible_source_paths'] == ['ep1.mkv', 'ep2.mkv']
    assert any('task_source_path' in hint for hint in result['repair_hints'])


def test_pi_target_helper_canonicalizes_unique_visible_basename(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('f1', 'ep1.mkv', 'Season 1/ep1.mkv'), _File('f2', 'ep2.mkv', 'Season 1/ep2.mkv')],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': 'ep1.mkv',
            'title_query': 'Test',
            'kind_hint': 'tv',
        },
    )

    assert result['ok'] is True
    assert result['source_path'] == 'Season 1/ep1.mkv'
    assert result['source_path_canonicalized_from'] == 'ep1.mkv'
    assert result['local_file']['source_path'] == 'Season 1/ep1.mkv'


def test_pi_target_helper_rejects_ambiguous_visible_basename(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('a1', 'ep1.mkv', 'Season A/ep1.mkv'), _File('b1', 'ep1.mkv', 'Season B/ep1.mkv')],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': 'ep1.mkv',
            'title_query': 'Test',
            'kind_hint': 'tv',
        },
    )

    assert result['ok'] is False
    assert result['basename_match_count'] == 2
    assert any('ambiguous' in hint for hint in result['repair_hints'])


def test_pi_target_helper_returns_subject_episode_facts_for_single_ova(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('f1', '[ReinForce] Neptune OVA2.mkv', '[ReinForce] Neptune OVA2.mkv')],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_OvaBangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': '[ReinForce] Neptune OVA2.mkv',
            'title_query': 'Neptune OVA2',
            'kind_hint': 'tv',
        },
    )

    assert result['ok'] is True
    assert 'ambiguity' not in result
    assert 'should_expand_related' not in result
    assert 'ready_to_validate' not in result
    assert 'recommended_recipe' not in result
    assert 'draft_recipe' not in result
    assert 'candidates' not in result
    assert result['episode_order'] == 'sort, then ep, then episode_id'
    assert 'episode_rows_limited' in result['usage_hint']
    assert 'chosen target' in result['usage_hint']
    group = result['subject_episode_groups'][0]
    assert group['subject']['subject_id'] == 351253
    assert group['episodes'][0]['episode_id'] == 1075427
    assert group['episodes'][0]['episode_type'] == 'ova'
    assert group['episode_count_available'] == 1
    assert group['episode_count_returned'] == 1
    assert group['episode_rows_limited'] is False
    assert 'recipe_target' not in group
    assert any(subject.subject_id == 351253 for subject in state.workspace.bangumi_subjects)
    assert any(item.episode_id == 1075427 for item in state.workspace.bangumi_items)


def test_pi_target_helper_returns_facts_without_batch_draft_recipe(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': 'ep1.mkv',
            'title_query': 'Test',
            'kind_hint': 'tv',
        },
    )
    assert 'draft_recipe' not in result
    assert 'draft_recipe_coverage' not in result
    assert result['subject_episode_groups'][0]['subject']['subject_id'] == 200
    assert [episode['sort'] for episode in result['subject_episode_groups'][0]['episodes']] == [1, 2]
    assert any(item.episode_id == 2001 for item in state.workspace.bangumi_items)
    assert any(item.episode_id == 2002 for item in state.workspace.bangumi_items)


def test_pi_episode_tools_use_mechanical_sort_ep_id_order_before_limit(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_UnsortedEpisodeBangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    episode_list = state.handle_tool('get_episode_list', {'subject_id': 200, 'max_episode_cards': 2})
    helper = state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': 'ep1.mkv',
            'title_query': 'Test',
            'kind_hint': 'tv',
            'max_episode_cards': 2,
        },
    )

    assert [episode['sort'] for episode in episode_list['episodes']] == [1, 2]
    assert [episode['sort'] for episode in helper['subject_episode_groups'][0]['episodes']] == [1, 2]
    assert helper['subject_episode_groups'][0]['episode_count_available'] == 3
    assert helper['subject_episode_groups'][0]['episode_count_returned'] == 2
    assert helper['subject_episode_groups'][0]['episode_rows_limited'] is True


def test_pi_target_helper_returns_facts_for_visible_subset_without_draft(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('f1', 'ep1.mkv', 'ep1.mkv'),
            _File('f2', 'ep2.mkv', 'ep2.mkv'),
            _File('bonus', 'bonus.mkv', 'bonus.mkv'),
        ],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': 'ep1.mkv',
            'title_query': 'Test',
            'kind_hint': 'tv',
        },
    )
    assert 'draft_recipe' not in result
    assert 'draft_recipe_coverage' not in result
    assert result['subject_episode_groups'][0]['subject']['subject_id'] == 200
    assert len(result['subject_episode_groups'][0]['episodes']) == 2


def test_pi_validate_recipe_parse_error_returns_actionable_episode_type_hint(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    bad_recipe = {
        'version': 1,
        'summary': 'bad raw api episode type',
        'rules': [
            {
                'name': 'bad',
                'select': {'exact_paths': ['ep1.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001, 'episode_type': 'episode'},
                'disposition': 'map_to_bangumi',
            }
        ],
    }

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': bad_recipe})

    assert result['ok'] is False
    assert result['accepted'] is False
    assert any('Use' in hint and "'regular'" in hint for hint in result['repair_hints'])


def test_pi_validate_recipe_zero_match_returns_visible_source_path_hint(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    zero_match = {
        'version': 1,
        'summary': 'bad selector',
        'rules': [
            {
                'name': 'zero',
                'select': {'exact_paths': ['missing.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                'disposition': 'map_to_bangumi',
            }
        ],
    }

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': zero_match})

    assert result['ok'] is True
    assert result['accepted'] is False
    assert any('ep1.mkv' in hint for hint in result['repair_hints'])


def test_pi_validate_recipe_invalid_episode_capture_tells_pi_to_use_exact_path_for_one_file(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    no_capture = {
        'version': 1,
        'summary': 'bad selector capture',
        'rules': [
            {
                'name': 'one file as regex',
                'select': {'filename_regex': 'ep1\\.mkv'},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
                'disposition': 'map_to_bangumi',
            }
        ],
    }

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': no_capture})

    assert result['accepted'] is False
    issue = next(issue for issue in result['verifier_result']['issues'] if issue['issue_code'] == 'invalid_episode_capture')
    assert 'sequence rules need {ep}' in issue['message']
    assert any('single movie/OVA/SP/special file' in hint for hint in result['repair_hints'])


def test_pi_validate_recipe_unknown_subject_returns_targeted_repair_hint(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_NoEpisodeEvidenceBangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)
    unknown_subject = {
        'version': 1,
        'summary': 'bad subject',
        'rules': [
            {
                'name': 'unknown',
                'select': {'exact_paths': ['ep1.mkv']},
                'target': {'bangumi_subject_id': 999999, 'media_kind': 'tv', 'episode_id': 9999991},
                'disposition': 'map_to_bangumi',
            }
        ],
    }

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': unknown_subject})

    assert result['accepted'] is False
    issue = next(issue for issue in result['verifier_result']['issues'] if issue['issue_code'] == 'unknown_subject_id')
    assert issue['related_refs'] == ['ep1.mkv', 'subject:999999']
    assert 'cannot be used' in issue['message']
    assert any('do not invent subject IDs' in hint for hint in result['repair_hints'])
    assert any('ep1.mkv' in hint for hint in result['repair_hints'])


def test_pi_validate_recipe_params_rejects_boolean_disposition_alias(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'bonus',
                        'exact_paths': ['ep1.mkv'],
                        'non_bangumi_or_supplemental': True,
                        'reason': 'package bonus',
                    }
                ]
            }
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'unsupported boolean field' in result['error']
    assert 'disposition: "non_bangumi_or_supplemental"' in result['error']
    assert any('do not write non_bangumi_or_supplemental: true' in hint for hint in result['repair_hints'])


def test_pi_validate_recipe_params_rejects_boolean_source_unit_alias(tmp_path):
    state = PiCaseToolState(workspace=_multi_episode_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'rules': [
                    {
                        'name': 'merged',
                        'merged': True,
                        'exact_paths': ['merged.mkv'],
                        'subject_id': 100,
                        'episode_range': '1-3',
                    }
                ]
            }
        },
    )

    assert result['ok'] is False
    assert result['accepted'] is False
    assert 'unsupported boolean source-unit field' in result['error']
    assert 'source_unit: "single_file_multi_episode"' in result['error']
    assert any('multi_episode: true' in hint for hint in result['repair_hints'])


def test_pi_validate_recipe_rejects_episode_id_with_wrong_episode_type(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('f1', '[ReinForce] Neptune OVA2.mkv', '[ReinForce] Neptune OVA2.mkv')],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_OvaBangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )
    state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': '[ReinForce] Neptune OVA2.mkv',
            'title_query': 'Neptune OVA2',
            'kind_hint': 'ova',
        },
    )
    wrong_type = {
        'version': 1,
        'summary': 'wrong type',
        'rules': [
            {
                'name': 'wrong',
                'select': {'exact_paths': ['[ReinForce] Neptune OVA2.mkv']},
                'target': {'bangumi_subject_id': 351253, 'media_kind': 'tv', 'episode_id': 1075427, 'episode_type': 'regular'},
                'disposition': 'map_to_bangumi',
            }
        ],
    }

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': wrong_type})

    assert result['accepted'] is False
    assert any(issue['issue_code'] == 'missing_target_episode' for issue in result['verifier_result']['issues'])
    assert any('Bangumi episode sort and ep values' in hint for hint in result['repair_hints'])
    assert any('episode_number_field:"ep"' in hint for hint in result['repair_hints'])
    assert any('related season/cour/part subject' in hint for hint in result['repair_hints'])


def test_pi_validate_recipe_hints_supplemental_for_bonus_group_missing_rows(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[
            _File('sp1', '[Group] Show [SP01].mkv', 'SPs/[Group] Show [SP01].mkv'),
            _File('sp2', '[Group] Show [SP02].mkv', 'SPs/[Group] Show [SP02].mkv'),
        ],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_BangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )
    recipe_params = {
        'rules': [
            {
                'name': 'sp rows',
                'source_pattern': 'SPs/[Group] Show [SP{ep:02}].mkv',
                'subject_id': 200,
                'media_kind': 'sp',
                'episode_type': 'special',
                'episode_range': '1-2',
                'reason': 'SP folder looked like a special sequence',
            }
        ]
    }

    result = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})

    assert result['accepted'] is False
    assert any(issue['issue_code'] == 'missing_target_episode' for issue in result['verifier_result']['issues'])
    assert any('special_or_bonus_candidate' in hint for hint in result['repair_hints'])
    assert any('A related Bangumi subject alone is not enough' in hint for hint in result['repair_hints'])
    assert any('disposition:"non_bangumi_or_supplemental"' in hint for hint in result['repair_hints'])


def test_pi_fixed_layer_recommendation_submit_tool_is_not_available(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('f1', '[ReinForce] Neptune OVA2.mkv', '[ReinForce] Neptune OVA2.mkv')],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_OvaSubjectRegularEpisodeBangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=REPO_ROOT,
    )
    result = state.handle_tool(
        'submit_recommended_recipe',
        {
            'organize_recipe': _accepted_recipe(),
            'summary': 'fast accepted',
        },
    )

    assert result['ok'] is False
    assert 'unknown tool' in result['error']
    assert state.final_result is None


def test_pi_target_helper_keeps_media_kind_separate_from_bangumi_episode_type(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('f1', '[ReinForce] Neptune OVA2.mkv', '[ReinForce] Neptune OVA2.mkv')],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_OvaSubjectRegularEpisodeBangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )

    result = state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': '[ReinForce] Neptune OVA2.mkv',
            'title_query': 'Neptune OVA2',
            'kind_hint': 'tv',
        },
    )
    recipe_params = {
        'version': 1,
        'summary': 'Pi chooses media_kind from subject evidence',
        'rules': [
            {
                'name': 'neptune ova',
                'exact_paths': ['[ReinForce] Neptune OVA2.mkv'],
                'subject_id': 351253,
                'media_kind': 'ova',
                'episode_id': 1075427,
                'episode_type': 'regular',
                'disposition': 'map_to_bangumi',
                'reason': 'Pi selected the OVA media kind while Bangumi exposes the row as regular.',
            }
        ],
    }
    validate = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params, 'detail': True})

    assert result['subject_episode_groups'][0]['subject']['subject_id'] == 351253
    assert result['subject_episode_groups'][0]['episodes'][0]['episode_type'] == 'regular'
    assert validate['accepted'] is True


def test_pi_recipe_params_infers_exact_episode_type_from_exposed_row(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('f1', '[ReinForce] Neptune OVA2.mkv', '[ReinForce] Neptune OVA2.mkv')],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_OvaSubjectRegularEpisodeBangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )
    state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': '[ReinForce] Neptune OVA2.mkv',
            'title_query': 'Neptune OVA2',
            'kind_hint': 'tv',
        },
    )

    validate = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'version': 1,
                'summary': 'minimal exact params',
                'rules': [
                    {
                        'name': 'neptune ova',
                        'exact_paths': ['[ReinForce] Neptune OVA2.mkv'],
                        'subject_id': 351253,
                        'media_kind': 'ova',
                        'episode_id': 1075427,
                        'disposition': 'map_to_bangumi',
                        'reason': 'exact episode row evidence is exposed',
                    }
                ],
            }
        },
    )

    assert validate['accepted'] is True
    recipe = validate['organize_recipe']
    assert recipe['rules'][0]['target']['media_kind'] == 'ova'
    assert recipe['rules'][0]['target']['episode_type'] == 'regular'


def test_pi_recipe_params_canonicalizes_exact_episode_type_from_exposed_row(tmp_path):
    local = SimpleNamespace(
        source_path='tests/sample',
        files=[_File('f1', '[ReinForce] Neptune OVA2.mkv', '[ReinForce] Neptune OVA2.mkv')],
    )
    state = PiCaseToolState(
        workspace=_build_workspace(local_evidence=local, bangumi_contexts=[]),
        bangumi_client=_OvaBangumiClient(),
        run_dir=tmp_path / 'run',
        repo_root=tmp_path,
    )
    state.handle_tool(
        'find_bangumi_targets_for_local_file',
        {
            'source_path': '[ReinForce] Neptune OVA2.mkv',
            'title_query': 'Neptune OVA2',
            'kind_hint': 'ova',
        },
    )

    validate = state.handle_tool(
        'validate_organize_recipe_params',
        {
            'detail': True,
            'recipe_params': {
                'version': 1,
                'summary': 'canonical exact params',
                'rules': [
                    {
                        'name': 'neptune ova',
                        'exact_paths': ['[ReinForce] Neptune OVA2.mkv'],
                        'subject_id': 351253,
                        'media_kind': 'ova',
                        'episode_id': 1075427,
                        'episode_type': 'regular',
                        'disposition': 'map_to_bangumi',
                        'reason': 'params canonicalize the exact exposed episode row type',
                    }
                ],
            }
        },
    )

    assert validate['accepted'] is True
    assert validate['organize_recipe']['rules'][0]['target']['episode_type'] == 'ova'


def test_pi_old_mapping_draft_tool_is_not_registered(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    assert state.handle_tool('submit_mapping_draft', {'mapping_draft': {}})['ok'] is False
    assert state.handle_tool('read_repo_context', {'path': 'README.md'})['ok'] is False
    assert state.handle_tool('write_case_note', {'title': 'x', 'content': 'y'})['ok'] is False
    assert state.handle_tool('run_validation_command', {'command': 'git status'})['ok'] is False
