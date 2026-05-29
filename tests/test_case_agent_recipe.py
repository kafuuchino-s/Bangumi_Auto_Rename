from __future__ import annotations

from types import SimpleNamespace

from src.rename.case_agent.local_bangumi_entry import _build_workspace
from src.rename.case_agent.models import BangumiItemCard, CaseBudget, CaseContract, CaseHeader, LocalFileCard
from src.rename.case_agent.recipe import OrganizeRecipeDraft, compile_and_verify_organize_recipe
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


class _File:
    def __init__(self, name: str, relative_path: str | None = None):
        self.file_id = name
        self.name = name.rsplit('/', 1)[-1]
        self.relative_path = relative_path or name
        self.is_main_video_candidate = True
        self.is_video = True
        self.suffix = '.mkv'


def _workspace(files, contexts):
    local = SimpleNamespace(source_path='tests/sample', files=[_File(path) for path in files])
    return _build_workspace(local_evidence=local, bangumi_contexts=contexts)


def _tv_context(subject_id: int = 100, count: int = 3):
    return {
        'context': {
            'episode_structure': {
                'subject_id': subject_id,
                'title': f'TV {subject_id}',
                'episodes': [
                    {'episode_id': subject_id * 10 + index, 'title': f'Episode {index}', 'sort': index, 'ep': index, 'kind': 'regular'}
                    for index in range(1, count + 1)
                ],
            }
        }
    }


def _special_context(subject_id: int = 200):
    return {
        'context': {
            'episode_structure': {
                'subject_id': subject_id,
                'title': f'Special {subject_id}',
                'episodes': [
                    {'episode_id': subject_id * 10 + 1, 'title': 'OVA', 'sort': 1, 'ep': 1, 'kind': 'special'},
                    {'episode_id': subject_id * 10 + 2, 'title': 'SP 2', 'sort': 2, 'ep': 2, 'kind': 'special'},
                ],
            }
        }
    }


def _movie_context(subject_id: int):
    return {
        'context': {
            'episode_structure': {
                'subject_id': subject_id,
                'title': f'Movie {subject_id}',
                'source_form_hint': 'movie',
                'episodes': [],
            }
        }
    }


def _verify(workspace, payload):
    plan, result = compile_and_verify_organize_recipe(workspace, OrganizeRecipeDraft.model_validate(payload))
    return plan, result


def _multi_episode_workspace(
    *,
    local_container: dict[str, object] | None = None,
    target_duration_seconds: list[int] | None = None,
    files: list[str] | None = None,
) -> CaseEvidenceWorkspace:
    paths = files or ['merged.mkv']
    refs = [f'LF{index}' for index, _path in enumerate(paths, start=1)]
    durations = target_duration_seconds if target_duration_seconds is not None else [990, 1200, 1650]
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=refs),
        local_files=[
            LocalFileCard(
                ref=ref,
                path=path,
                is_main=True,
                container_facts=local_container or {
                    'probe_status': 'available',
                    'duration_seconds': 3618.368,
                    'chapter_count': 4,
                    'chapter_durations_seconds': [881.589, 1075.074, 1520.936, 140.769],
                },
            )
            for ref, path in zip(refs, paths, strict=True)
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
            for index, duration in enumerate(durations, start=1)
        ],
    )


def test_recipe_compiler_accepts_tv_continuous_episode_rule():
    workspace = _workspace(['ep1.mkv', 'ep2.mkv', 'ep3.mkv'], [_tv_context()])
    plan, result = _verify(workspace, {
        'version': 1,
        'summary': 'TV rule',
        'rules': [{
            'name': 'tv',
            'select': {'filename_regex': 'ep{ep}.mkv'},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
            'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-3'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is True
    assert [item.target.episode_id for item in plan.assignments] == [1001, 1002, 1003]
    assert plan.uncovered_paths == []


def test_recipe_compiler_uses_episode_subject_ref_when_subject_card_is_missing():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id='test'),
        budget=CaseBudget(),
        contract=CaseContract(main_file_refs=['LF1']),
        local_files=[LocalFileCard(ref='LF1', path='ep1.mkv', is_main=True)],
        bangumi_items=[
            BangumiItemCard(
                ref='episode:1001',
                item_kind='episode',
                episode_id=1001,
                type='0',
                sort=1,
                ep=1,
                subject_ref='subject:100',
            )
        ],
    )

    plan, result = _verify(workspace, {
        'version': 1,
        'summary': 'episode-only target evidence',
        'rules': [{
            'name': 'tv',
            'select': {'filename_regex': 'ep{ep}.mkv'},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
            'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is True
    assert plan.assignments[0].target.episode_id == 1001


def test_recipe_compiler_supports_offset_expression():
    workspace = _workspace(['show 11.mkv', 'show 12.mkv'], [_tv_context(count=2)])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'offset',
            'select': {'filename_regex': 'show (?<ep>\\d+)\\.mkv'},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
            'episode': {'capture': 'ep', 'offset': 'EP-10', 'range': '11-12'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is True
    assert [item.extracted_episode_number for item in plan.assignments] == [1, 2]


def test_recipe_compiler_uses_episode_range_to_split_continuous_run():
    files = [f'show {index:02d}.mkv' for index in range(1, 21)]
    workspace = _workspace(files, [_tv_context(subject_id=100, count=10), _tv_context(subject_id=200, count=10)])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [
            {
                'name': 'first cour',
                'select': {'filename_regex': 'show {ep}.mkv'},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
                'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-10'},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'second cour',
                'select': {'filename_regex': 'show {ep}.mkv'},
                'target': {'bangumi_subject_id': 200, 'media_kind': 'tv', 'episode_type': 'regular'},
                'episode': {'capture': 'ep', 'offset': 'EP-10', 'range': '11-20'},
                'disposition': 'map_to_bangumi',
            },
        ],
    })

    assert result.passed is True
    assert plan.duplicate_coverage_paths == []
    assert plan.uncovered_paths == []
    assert [item.source_path for item in plan.assignments[:10]] == files[:10]
    assert [item.source_path for item in plan.assignments[10:]] == files[10:]
    assert [item.target.episode_id for item in plan.assignments] == [
        *[1000 + index for index in range(1, 11)],
        *[2000 + index for index in range(1, 11)],
    ]


def test_recipe_compiler_accepts_regex_style_token_template():
    workspace = _workspace(['show.E01.1080p.mkv', 'show.E02.1080p.mkv'], [_tv_context(count=2)])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'regex template',
            'select': {'filename_regex': 'show\\.E{ep}\\.1080p\\.mkv'},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
            'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is True
    assert [item.target.episode_id for item in plan.assignments] == [1001, 1002]


def test_recipe_compiler_accepts_zero_padded_episode_template():
    workspace = _workspace(['show 01.mkv', 'show 02.mkv'], [_tv_context(count=2)])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'zero padded template',
            'select': {'filename_regex': 'show {ep:02d}.mkv'},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
            'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is True
    assert [item.extracted_episode_number for item in plan.assignments] == [1, 2]
    assert [item.target.episode_id for item in plan.assignments] == [1001, 1002]


def test_recipe_compiler_accepts_single_ova_exact_path():
    workspace = _workspace(['show OVA.mkv'], [_special_context()])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'ova',
            'select': {'exact_paths': ['show OVA.mkv']},
            'target': {'bangumi_subject_id': 200, 'media_kind': 'ova', 'episode_id': 2001, 'episode_type': 'ova'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is True
    assert plan.assignments[0].target.episode_id == 2001


def test_recipe_compiler_accepts_single_file_multi_episode_span():
    workspace = _multi_episode_workspace()
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'merged ova',
            'source_unit': 'single_file_multi_episode',
            'select': {'exact_paths': ['merged.mkv']},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'ova', 'episode_type': 'regular'},
            'episode': {'range': '1-3', 'offset': 'EP'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is True
    assert len(plan.assignments) == 1
    assignment = plan.assignments[0]
    assert assignment.source_path == 'merged.mkv'
    assert assignment.target.episode_id == 1001
    assert assignment.target_span.episode_ids == [1001, 1002, 1003]
    assert assignment.target_span.sort_start == 1
    assert assignment.target_span.sort_end == 3


def test_recipe_compiler_requires_source_unit_for_exact_path_episode_range():
    workspace = _multi_episode_workspace()
    _plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'missing source unit',
            'select': {'exact_paths': ['merged.mkv']},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'ova', 'episode_type': 'regular'},
            'episode': {'range': '1-3'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is False
    assert any(issue.issue_code == 'missing_episode_locator' for issue in result.issues)


def test_recipe_compiler_rejects_single_file_multi_episode_with_fixed_episode_id():
    workspace = _multi_episode_workspace()
    _plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'bad fixed locator',
            'source_unit': 'single_file_multi_episode',
            'select': {'exact_paths': ['merged.mkv']},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'ova', 'episode_id': 1001, 'episode_type': 'regular'},
            'episode': {'range': '1-3'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is False
    assert any(issue.issue_code == 'invalid_multi_episode_target_locator' for issue in result.issues)


def test_recipe_compiler_rejects_single_file_multi_episode_without_single_exact_path():
    workspace = _multi_episode_workspace(files=['merged-a.mkv', 'merged-b.mkv'])
    _plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'too many paths',
            'source_unit': 'single_file_multi_episode',
            'select': {'exact_paths': ['merged-a.mkv', 'merged-b.mkv']},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'ova', 'episode_type': 'regular'},
            'episode': {'range': '1-3'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is False
    assert any(issue.issue_code == 'invalid_source_unit_selector' for issue in result.issues)


def test_recipe_compiler_rejects_single_file_multi_episode_one_episode_range():
    workspace = _multi_episode_workspace()
    _plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'one episode',
            'source_unit': 'single_file_multi_episode',
            'select': {'exact_paths': ['merged.mkv']},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'ova', 'episode_type': 'regular'},
            'episode': {'range': '1'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is False
    assert any(issue.issue_code == 'invalid_episode_range' for issue in result.issues)


def test_recipe_compiler_rejects_single_file_multi_episode_without_mechanical_evidence():
    workspace = _multi_episode_workspace(
        local_container={'probe_status': 'available', 'duration_seconds': 0, 'chapter_count': 0},
        target_duration_seconds=[0, 0, 0],
    )
    _plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'no support',
            'source_unit': 'single_file_multi_episode',
            'select': {'exact_paths': ['merged.mkv']},
            'target': {'bangumi_subject_id': 100, 'media_kind': 'ova', 'episode_type': 'regular'},
            'episode': {'range': '1-3'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is False
    assert any(issue.issue_code == 'missing_multi_episode_evidence' for issue in result.issues)


def test_recipe_compiler_detects_duplicate_target_inside_multi_episode_span():
    workspace = _multi_episode_workspace(files=['merged.mkv', 'ep2.mkv'])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [
            {
                'name': 'merged',
                'source_unit': 'single_file_multi_episode',
                'select': {'exact_paths': ['merged.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'ova', 'episode_type': 'regular'},
                'episode': {'range': '1-3'},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'duplicate ep2',
                'select': {'exact_paths': ['ep2.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'ova', 'episode_id': 1002, 'episode_type': 'regular'},
                'disposition': 'map_to_bangumi',
            },
        ],
    })

    assert result.passed is False
    assert plan.duplicate_target_keys == ['episode:1002']
    duplicate_issue = next(issue for issue in result.issues if issue.issue_code == 'duplicate_target')
    assert set(duplicate_issue.related_refs) == {'merged.mkv', 'ep2.mkv'}


def test_recipe_verifier_blocks_episode_id_with_wrong_episode_type():
    workspace = _workspace(['show OVA.mkv'], [_special_context()])
    _plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'wrong_type',
            'select': {'exact_paths': ['show OVA.mkv']},
            'target': {'bangumi_subject_id': 200, 'media_kind': 'tv', 'episode_id': 2001, 'episode_type': 'regular'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is False
    assert any(issue.issue_code == 'missing_target_episode' for issue in result.issues)


def test_recipe_compiler_accepts_sp_range_rule():
    workspace = _workspace(['SP1.mkv', 'SP2.mkv'], [_special_context()])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [{
            'name': 'sp',
            'select': {'filename_regex': 'SP{ep}.mkv'},
            'target': {'bangumi_subject_id': 200, 'media_kind': 'sp', 'episode_type': 'special'},
            'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
            'disposition': 'map_to_bangumi',
        }],
    })

    assert result.passed is True
    assert [item.target.episode_id for item in plan.assignments] == [2001, 2002]


def test_recipe_compiler_accepts_movie_collection_by_exact_path_rules():
    workspace = _workspace(['movie-a.mkv', 'movie-b.mkv'], [_movie_context(301), _movie_context(302)])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [
            {
                'name': 'movie_a',
                'select': {'exact_paths': ['movie-a.mkv']},
                'target': {'bangumi_subject_id': 301, 'media_kind': 'movie', 'episode_type': 'movie'},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'movie_b',
                'select': {'exact_paths': ['movie-b.mkv']},
                'target': {'bangumi_subject_id': 302, 'media_kind': 'movie', 'episode_type': 'movie'},
                'disposition': 'map_to_bangumi',
            },
        ],
    })

    assert result.passed is True
    assert [item.target.bangumi_subject_id for item in plan.assignments] == [301, 302]


def test_recipe_compiler_accepts_supplemental_exclusion_rule():
    workspace = _workspace(['ep1.mkv', 'side-material.mkv'], [_tv_context(count=1)])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [
            {
                'name': 'main',
                'select': {'exact_paths': ['ep1.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'bonus',
                'select': {'exact_paths': ['side-material.mkv']},
                'disposition': 'non_bangumi_or_supplemental',
                'reason': 'supplemental side material',
            },
        ],
    })

    assert result.passed is True
    by_path = {item.source_path: item.disposition for item in plan.assignments}
    assert by_path == {'ep1.mkv': 'map_to_bangumi', 'side-material.mkv': 'non_bangumi_or_supplemental'}


def test_recipe_verifier_blocks_zero_match_overlap_uncovered_and_duplicate_target():
    workspace = _workspace(['ep1.mkv', 'ep2.mkv'], [_tv_context(count=2)])
    plan, result = _verify(workspace, {
        'version': 1,
        'rules': [
            {
                'name': 'duplicate_a',
                'select': {'exact_paths': ['ep1.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'duplicate_b',
                'select': {'path_glob': '*.mkv'},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1001},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'zero',
                'select': {'exact_paths': ['missing.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_id': 1002},
                'disposition': 'map_to_bangumi',
            },
        ],
    })

    codes = {issue.issue_code for issue in result.issues}
    assert result.passed is False
    assert 'duplicate_coverage' in codes
    assert 'duplicate_target' in codes
    assert 'zero_match' in codes
    assert 'unknown_exact_path' in codes
    assert plan.duplicate_target_keys == ['episode:1001']
    duplicate_issue = next(issue for issue in result.issues if issue.issue_code == 'duplicate_target')
    assert set(duplicate_issue.related_refs) == {'ep1.mkv', 'ep2.mkv'}
    assert 'episode:1001' in duplicate_issue.message
    assert 'duplicate_a' in duplicate_issue.message
