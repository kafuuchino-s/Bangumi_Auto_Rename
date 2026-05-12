from __future__ import annotations

from src.config.config_manager import cm
from src.rename.process import Rename, _run_local_bangumi_case_agent_primary


class _File:
    def __init__(self, file_id: str, name: str, relative_path: str, is_main_video_candidate: bool = True):
        self.file_id = file_id
        self.name = name
        self.relative_path = relative_path
        self.is_main_video_candidate = is_main_video_candidate


def test_local_bangumi_primary_writes_only_case_agent_stage(monkeypatch):
    stages: list[str] = []

    monkeypatch.setattr('src.rename.process.write_decision_snapshot', lambda stage, *args, **kwargs: stages.append(stage))
    monkeypatch.setattr('src.rename.process.run_local_bangumi_case_agent_mapping', lambda **kwargs: {'ok': True, 'status': 'accepted', 'summary': 'ok'})

    result = _run_local_bangumi_case_agent_primary(
        local_evidence=type('LocalEvidence', (), {'source_path': 'tests/sample', 'files': [_File('f1', 'ep1.mkv', 'ep1.mkv')]})(),
        bangumi_contexts=[],
        ai_client=object(),
        source_path='tests/sample',
    )

    assert result['status'] == 'accepted'
    assert stages == ['rename_local_bangumi_case_agent_result']


def test_case_agent_primary_receives_parent_directory_without_fixed_split(tmp_path, monkeypatch):
    parent = tmp_path / 'Series Pack'
    season_1 = parent / 'Season 1'
    season_2 = parent / 'Season 2'
    season_1.mkdir(parents=True)
    season_2.mkdir(parents=True)
    (season_1 / '01.mkv').write_bytes(b'')
    (season_2 / '01.mkv').write_bytes(b'')

    captured: dict[str, object] = {}

    class FakeAIClient:
        def is_available(self):
            return True

        def analyze_local_package(self, *args, **kwargs):
            raise AssertionError('Case Agent primary must not use fixed pre-agent package analysis')

    def fake_case_agent_primary(**kwargs):
        local_evidence = kwargs['local_evidence']
        captured['paths'] = [file.relative_path for file in local_evidence.files]
        captured['bangumi_contexts'] = kwargs['bangumi_contexts']
        return {'ok': True, 'status': 'fail_closed', 'summary': 'agent handled parent'}

    def fail_enqueue(**kwargs):
        raise AssertionError('Case Agent primary should decide splitting, not Rename.process')

    def fake_error_reply(self, task_uuid, message, path, *args, extra_task_data=None, **kwargs):
        return {'task_uuid': task_uuid, 'message': message, 'extra_task_data': extra_task_data or {}}

    monkeypatch.setattr('src.rename.process.AIClient', FakeAIClient)
    monkeypatch.setattr('src.rename.process._run_local_bangumi_case_agent_primary', fake_case_agent_primary)
    monkeypatch.setattr(Rename, 'error_reply', fake_error_reply)

    rename = Rename()
    assert not hasattr(rename, 'search')

    with cm.temporary_config({'rename_local_bangumi_case_agent_primary_enabled': True}):
        result = rename.process(parent, _tuuid='case-agent-task', _enqueue_task=fail_enqueue)

    assert captured['paths'] == ['Season 1/01.mkv', 'Season 2/01.mkv']
    assert captured['bangumi_contexts'] == []
    assert result['extra_task_data']['case_agent_result']['summary'] == 'agent handled parent'


def test_case_agent_primary_does_not_require_tmdb_key(tmp_path, monkeypatch):
    parent = tmp_path / 'Series Pack'
    parent.mkdir()
    (parent / '01.mkv').write_bytes(b'')

    captured: dict[str, object] = {}

    class FakeAIClient:
        def is_available(self):
            return True

    def fake_case_agent_primary(**kwargs):
        captured['called'] = True
        return {'ok': True, 'status': 'accepted', 'summary': 'mapped to bangumi only'}

    def fake_error_reply(self, task_uuid, message, path, *args, extra_task_data=None, **kwargs):
        return {'task_uuid': task_uuid, 'message': message, 'extra_task_data': extra_task_data or {}}

    monkeypatch.setattr('src.rename.process.AIClient', FakeAIClient)
    monkeypatch.setattr('src.rename.process._run_local_bangumi_case_agent_primary', fake_case_agent_primary)
    monkeypatch.setattr(Rename, 'error_reply', fake_error_reply)

    rename = Rename()
    assert not hasattr(rename, 'search')

    with cm.temporary_config({'rename_local_bangumi_case_agent_primary_enabled': True}):
        result = rename.process(parent, _tuuid='case-agent-no-tmdb')

    assert captured['called'] is True
    assert result['extra_task_data']['case_agent_result']['status'] == 'accepted'
