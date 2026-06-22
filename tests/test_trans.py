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


def test_trans_late_target_exists_skips_when_policy_skip(
    tmp_path: Path, monkeypatch
):
    """overwrite=跳过策略：目标已存在时跳过该文件，不失败不回滚，
    继续处理其他文件。替代旧"拒绝覆盖+partial_failure"语义（整任务失败
    在实际使用中无意义，用户要么覆盖要么跳过）。"""
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
        force_overwrite='跳过',
    ).trans_file()

    # 跳过不失败：result is True（循环正常结束），target 保持原样不被覆盖
    assert result is True
    assert target.read_bytes() == b'preexisting'


def test_trans_late_target_exists_skips_when_legacy_bool_false(
    tmp_path: Path, monkeypatch
):
    """兼容旧 bool：force_overwrite=False 归一化为'跳过'，行为同上。"""
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)

    source = tmp_path / 'source.mkv'
    target = tmp_path / 'target.mkv'
    source.write_bytes(b'a')
    target.write_bytes(b'preexisting')

    result = Trans(
        {source: target},
        'uuid-3b',
        force_mode='复制',
        force_overwrite=False,
    ).trans_file()

    assert result is True
    assert target.read_bytes() == b'preexisting'


def test_trans_late_target_exists_overwrites_when_enabled(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)

    source = tmp_path / 'source.mkv'
    target = tmp_path / 'target.mkv'
    source.write_bytes(b'a')
    target.write_bytes(b'preexisting')

    result = Trans(
        {source: target},
        'uuid-4',
        force_mode='复制',
        force_overwrite=True,
    ).trans_file()

    assert result is True
    assert target.read_bytes() == b'a'
    assert (tmp_path / 'record' / 'uuid-4.json').exists()
