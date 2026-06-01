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
        return [SimpleNamespace(id=200, type=2, name='Searched', name_cn='检索结果', eps=2)]

    def get_subject(self, subject_id):
        subject_type = 1 if subject_id == 301 else 2
        return SimpleNamespace(id=subject_id, type=subject_type, name=f'Subject {subject_id}', eps=2)

    def get_related_subjects(self, subject_id):
        assert subject_id
        return [
            SimpleNamespace(id=201, type=2, relation='番外篇'),
            SimpleNamespace(id=301, type=1, relation='漫画'),
        ]

    def get_episodes(self, subject_id):
        return [
            SimpleNamespace(id=subject_id * 10 + 1, sort=1, ep=1, type=0, name='Episode 1'),
            SimpleNamespace(id=subject_id * 10 + 2, sort=2, ep=2, type=0, name='Episode 2'),
        ]


class _OvaBangumiClient:
    def search_subjects(self, query, _subject_type):
        assert 'Neptune' in query
        return [SimpleNamespace(id=351253, name='Neptune OVA2', name_cn='超次元游戏 海王星 OVA2', eps=1, total_episodes=1, platform='OVA')]

    def get_subject(self, subject_id):
        return SimpleNamespace(id=subject_id, name='Neptune OVA2', name_cn='超次元游戏 海王星 OVA2', eps=1, total_episodes=1, platform='OVA')

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
            100: SimpleNamespace(id=100, type=2, name='Series Root', name_cn='系列根', eps=10, platform='TV'),
            201: SimpleNamespace(id=201, type=2, name='Series Second Cour', name_cn='系列第二部分', eps=10, platform='TV'),
            202: SimpleNamespace(id=202, type=2, name='Series Movie Special', name_cn='系列剧场特别篇', eps=1, total_episodes=1, platform='剧场版'),
            301: SimpleNamespace(id=301, type=1, name='Series Book', name_cn='系列漫画'),
        }
        self.relations = {
            100: [
                SimpleNamespace(id=201, type=2, relation='续集'),
                SimpleNamespace(id=301, type=1, relation='书籍'),
            ],
            201: [SimpleNamespace(id=202, type=2, relation='续集')],
            202: [],
            301: [],
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


def test_pi_validate_organize_recipe_does_not_finalize(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': _accepted_recipe()})

    assert result['accepted'] is True
    assert state.final_result is None
    assert state.compiled_plan is not None
    assert len(state.compiled_plan.assignments) == 2


def test_pi_validate_organize_recipe_accepts_json_string_payload(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('validate_organize_recipe', {'organize_recipe': json.dumps(_accepted_recipe())})

    assert result['accepted'] is True
    assert state.final_result is None
    assert state.compiled_plan is not None
    assert len(state.compiled_plan.assignments) == 2


def test_pi_validate_organize_recipe_params_accepts_json_string_payload(tmp_path):
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

    result = state.handle_tool('validate_organize_recipe_params', {'recipe_params': json.dumps(recipe_params)})

    assert result['accepted'] is True
    assert state.final_result is None
    assert state.compiled_plan is not None
    assert len(state.compiled_plan.assignments) == 2


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
    first = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params})

    patched = state.handle_tool(
        'validate_organize_recipe_params_patch',
        {
            'recipe_params_patch': {
                'patch_rules': [
                    {'name': 'tv episodes', 'set': {'episode_range': '1-2'}},
                ],
            }
        },
    )

    assert first['accepted'] is False
    assert (tmp_path / 'run' / 'artifacts' / 'recipe_params.json').exists()
    assert patched['accepted'] is True
    assert patched['params_patch_applied'] is True
    assert state.latest_recipe_params_payload['rules'][0]['episode_range'] == '1-2'


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

    warning = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params})
    state.handle_tool('find_bangumi_targets_for_local_file', {'source_path': 'Bonus Main.mkv', 'max_subjects': 1, 'max_episode_cards': 2})
    accepted = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params})

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

    warning = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params})
    state.handle_tool('find_bangumi_targets_for_local_file', {'source_path': 'SPs/Side Story 01.mkv', 'max_subjects': 1, 'max_episode_cards': 2})
    accepted = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params})

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
            'recipe_params': {
                'version': 1,
                'summary': 'params map ep files',
                'rules': [
                    {
                        'name': 'tv episodes',
                        'source_pattern': 'ep{ep}.mkv',
                        'bangumi_subject_id': 100,
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
            'recipe_params': {
                'rules': [
                    {
                        'name': 'later subject',
                        'source_pattern': 'Show Later/Show [{ep}].mkv',
                        'subject_id': 328195,
                        'media_kind': 'tv',
                        'episode_type': 'regular',
                        'episode_range': '1-11',
                        'episode_offset': 33,
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


def test_pi_validate_organize_recipe_params_accepts_source_path_alias_for_one_file(tmp_path):
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

    assert result['accepted'] is True
    assert result['organize_recipe']['rules'][0]['select']['exact_paths'] == ['Movie.mkv']


def test_pi_validate_organize_recipe_params_accepts_single_file_multi_episode_source_unit(tmp_path):
    state = PiCaseToolState(workspace=_multi_episode_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
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


def test_pi_validate_organize_recipe_params_accepts_zero_padded_ep_and_natural_range_aliases(tmp_path):
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

    assert result['accepted'] is True
    recipe = result['organize_recipe']
    assert recipe['rules'][0]['select']['filename_regex'] == 'Show\\ (?P<ep>\\d{2})\\.mkv'
    assert recipe['rules'][0]['target']['media_kind'] == 'unknown'
    assert recipe['rules'][0]['episode']['offset'] == 'EP'
    assert recipe['rules'][0]['episode']['range'] == '1-2'
    assert result['accounting']['matched_path_count'] == 2


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


def test_pi_validate_organize_recipe_params_accepts_natural_aliases(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool(
        'validate_organize_recipe_params',
        {
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

    assert result['accepted'] is True
    recipe = result['organize_recipe']
    assert recipe['rules'][0]['target']['bangumi_subject_id'] == 100
    assert recipe['rules'][0]['target']['media_kind'] == 'tv'
    assert recipe['rules'][0]['target']['episode_type'] == 'regular'
    assert recipe['rules'][0]['episode']['range'] == '1-2'


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
            'recipe_params': {
                'summary': 'params final',
                'rules': [
                    {
                        'name': 'tv episodes',
                        'source_pattern': 'ep{ep}.mkv',
                        'bangumi_subject_id': 100,
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


def test_pi_submit_organize_recipe_accepts_params_shaped_payload(tmp_path):
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

    assert result['accepted'] is True
    assert state.final_result['status'] == 'accepted'
    assert state.final_result['summary'] == 'accepted despite raw tool name'
    assert result['accounting']['matched_path_count'] == 2


def test_pi_fail_closed_rejects_model_reported_budget_exhausted(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('fail_closed', {'reason': 'budget_exhausted', 'reason_kind': 'budget_exhausted'})

    assert result['ok'] is False
    assert 'runner-only' in result['error']
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
    assert 'not target recommendations' in result['context']['run_progress']['note']
    assert any(subject.ref == 'subject:200' for subject in state.workspace.bangumi_subjects)


def test_pi_expand_related_subjects_exposes_subject_type_for_pi_filtering(tmp_path):
    state = PiCaseToolState(workspace=_workspace(), bangumi_client=_BangumiClient(), run_dir=tmp_path / 'run', repo_root=tmp_path)

    result = state.handle_tool('expand_related_subjects', {'subject_id': 100, 'max_subjects': 5})

    assert result['ok'] is True
    subjects = [row['subject'] for row in result['relations']]
    by_id = {subject['subject_id']: subject for subject in subjects}
    assert by_id[201]['subject_type'] == 'anime'
    assert by_id[301]['subject_type'] == 'book'
    assert result['relation_subjects'][0]['subject_id'] == 201
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
    assert any('skipped subject_type=book' in skipped for skipped in result['skipped'])
    assert any(subject.subject_id == 202 for subject in state.workspace.bangumi_subjects)
    assert 'semantic' not in result['usage_hint'].casefold()
    assert 'next_subject_ids_to_expand' in result['usage_hint']
    assert 'not a recommendation' in result['usage_hint']


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
    assert payload['visible_source_paths'] == ['ep1.mkv', 'ep2.mkv']
    assert 'source_path' not in payload
    assert 'task_source_path is the original task/sample path' in payload['local_identity_policy']
    assert payload['scratch_paths']['artifacts_dir'] == str(tmp_path / 'run' / 'artifacts')
    assert payload['scratch_paths']['organize_recipe'] == str(tmp_path / 'run' / 'artifacts' / 'organize_recipe.json')
    assert payload['scratch_paths']['notes'] == str(tmp_path / 'run' / 'artifacts' / 'notes.md')
    assert payload['scratch_paths']['helper_check'] == str(tmp_path / 'run' / 'artifacts' / 'organize_recipe_helper_check.json')
    assert 'Trial-check semantic params' in payload['tool_semantics']['validate_organize_recipe_params']
    assert 'does not finalize the case' in payload['context']['recipe_contract']['validation_policy']
    assert 'finalization path' in payload['context']['recipe_contract']['submission_policy']
    assert payload['context']['local_files'][0]['source_path'] == 'ep1.mkv'
    assert 'ref' not in payload['context']['local_files'][0]
    assert payload['run_progress']['params_validation_seen'] is False
    assert payload['context']['run_progress']['verifier_feedback_available'] is False
    assert 'not target recommendations' in payload['run_progress']['note']
    assert payload['local_structure_summary']['visible_file_count'] == 2
    assert payload['context']['local_structure_summary']['folder_count'] == 1
    assert 'case_quick_start' not in payload
    assert payload['local_recipe_skeleton']['visible_file_count'] == 2
    assert payload['local_recipe_params_scaffold']['visible_file_count'] == 2
    assert 'does not choose Bangumi subject_id' in payload['local_recipe_params_scaffold']['scaffold_policy']
    assert 'get_local_recipe_params_scaffold' in payload['tool_semantics']
    assert 'selector and verifier-repair aid' in payload['context']['startup_evidence_locations']['local_recipe_skeleton']
    assert payload['context']['local_recipe_params_scaffold']['group_count'] >= 1
    assert 'local selector/range scaffolding only' in payload['context']['recipe_contract']['scaffold_policy']
    assert 'early_bangumi_evidence_bundle' not in payload
    assert 'early_bangumi_evidence_bundle' not in payload['context']['startup_evidence_locations']
    context_tool = state.handle_tool('get_case_context', {'detail': False})['data']
    assert context_tool['local_recipe_skeleton']['group_count'] >= 1
    assert context_tool['local_recipe_params_scaffold']['group_count'] >= 1
    assert context_tool['run_progress']['tool_call_count'] == 0
    assert 'early_bangumi_evidence_bundle' not in context_tool


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

    summary = state.case_input(timeout_seconds=300)['local_structure_summary']

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

    skeleton = state.case_input(timeout_seconds=300)['local_recipe_skeleton']
    scaffold = state.case_input(timeout_seconds=300)['local_recipe_params_scaffold']

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
    assert scaffold['visible_file_count'] == 8
    assert 'does not choose Bangumi subject_id' in scaffold['scaffold_policy']
    scaffold_group = next(group for group in scaffold['groups'] if group['title_hint'] == 'Show' and group['source_path_count'] == 3)
    assert 'episode_range' not in scaffold_group['params_rule_stub']
    unique_scaffold_group = next(group for group in scaffold['groups'] if group['title_hint'] == 'Show III')
    assert unique_scaffold_group['params_rule_stub']['episode_range'] == '1-2'
    assert unique_scaffold_group['params_rule_stub']['episode_offset'] == 'EP'
    assert scaffold_group['target_fields_for_mapped_rule']
    assert scaffold_group['supplemental_fields_if_evidence_does_not_support_mapping']['disposition'] == 'non_bangumi_or_supplemental'
    tool_payload = state.handle_tool('get_local_recipe_params_scaffold', {'detail': False})['data']
    assert tool_payload['group_count'] == scaffold['group_count']
    assert 'scaffold_policy' in tool_payload


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
    assert payload['local_recipe_skeleton']['groups']
    assert payload['local_recipe_params_scaffold']['groups']
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

    result = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params})

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
    validate = state.handle_tool('validate_organize_recipe_params', {'recipe_params': recipe_params})

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
