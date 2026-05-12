from pathlib import Path

from src.rename.trans import Trans
from src.utils.path import RECORD_PATH


def test_trans_failure_does_not_write_record(tmp_path: Path, monkeypatch):
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)

    source = tmp_path / 'missing.mkv'
    target = tmp_path / 'target.mkv'

    result = Trans({source: target}, 'uuid-1', force_mode='复制').trans_file()

    assert isinstance(result, str)
    assert not (tmp_path / 'record' / 'uuid-1.json').exists()


def test_trans_partial_failure_cleans_previous_targets(tmp_path: Path, monkeypatch):
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)

    source_a = tmp_path / 'a.mkv'
    source_b = tmp_path / 'b.mkv'
    target_a = tmp_path / 'out' / 'a.mkv'
    target_b = tmp_path / 'out' / 'b.mkv'
    source_a.write_bytes(b'a')

    result = Trans(
        {source_a: target_a, source_b: target_b},
        'uuid-2',
        force_mode='复制',
    ).trans_file()

    assert isinstance(result, str)
    assert not target_a.exists()
    assert not (tmp_path / 'record' / 'uuid-2.json').exists()


def test_trans_late_target_exists_refuses_overwrite(tmp_path: Path, monkeypatch):
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)

    source = tmp_path / 'source.mkv'
    target = tmp_path / 'target.mkv'
    source.write_bytes(b'a')
    target.write_bytes(b'preexisting')

    result = Trans(
        {source: target},
        'uuid-3',
        force_mode='复制',
        force_overwrite=True,
    ).trans_file()

    assert isinstance(result, str)
    assert target.read_bytes() == b'preexisting'
    assert not (tmp_path / 'record' / 'uuid-3.json').exists()
