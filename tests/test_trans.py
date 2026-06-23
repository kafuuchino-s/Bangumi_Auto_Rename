from pathlib import Path

from src.rename.trans import Trans
from src.utils.path import RECORD_PATH

_EXDEV = 18  # errno.EXDEV: cross-device link


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


def test_trans_skip_mode_record_only_lands_actually_written(
    tmp_path: Path, monkeypatch
):
    """跳过模式：record 只写本次实际落地的 {源:目标}，跳过的不进 record；
    landed_mapping 也只含落地的。避免上层 transferred_file_count / TG「已入库 N
    个文件」把跳过的也算进去导致虚高。"""
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)

    # 两个源 → 两个目标，其中一个目标已存在（会被跳过），另一个会实际落地
    src_new = tmp_path / 'new.mkv'
    src_skip = tmp_path / 'skip.mkv'
    tgt_new = tmp_path / 'out' / 'new.mkv'
    tgt_skip = tmp_path / 'out' / 'skip.mkv'
    src_new.write_bytes(b'new')
    src_skip.write_bytes(b'skip')
    tgt_skip.parent.mkdir(parents=True, exist_ok=True)
    tgt_skip.write_bytes(b'preexisting')

    trans = Trans(
        {src_new: tgt_new, src_skip: tgt_skip},
        'uuid-skip-mix',
        force_mode='复制',
        force_overwrite='跳过',
    )
    result = trans.trans_file()

    assert result is True
    # landed_mapping 只含实际落地的那个
    assert trans.landed_mapping == {src_new: tgt_new}
    assert src_skip not in trans.landed_mapping
    # skipped_mapping 只含被跳过的那个
    assert trans.skipped_mapping == {src_skip: tgt_skip}
    assert src_new not in trans.skipped_mapping
    # record 只写实际落地的
    import json as _json
    record = _json.load(
        open(tmp_path / 'record' / 'uuid-skip-mix.json', encoding='utf-8')
    )
    assert len(record) == 1
    assert str(tgt_new) in record.values()
    assert str(tgt_skip) not in record.values()


def test_trans_skip_mode_all_skipped_writes_empty_record(
    tmp_path: Path, monkeypatch
):
    """全跳过：record 为空 dict（反映本次 0 入库），landed_mapping 空，
    skipped_mapping 含全部被跳过的，仍返回 True（任务本身无错，只是无需落地）。"""
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)

    src = tmp_path / 'source.mkv'
    tgt = tmp_path / 'out' / 'target.mkv'
    src.write_bytes(b'a')
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tgt.write_bytes(b'preexisting')

    trans = Trans(
        {src: tgt},
        'uuid-all-skip',
        force_mode='复制',
        force_overwrite='跳过',
    )
    result = trans.trans_file()

    assert result is True
    assert trans.landed_mapping == {}
    assert trans.skipped_mapping == {src: tgt}
    import json as _json
    record = _json.load(
        open(tmp_path / 'record' / 'uuid-all-skip.json', encoding='utf-8')
    )
    assert record == {}


def test_trans_overwrite_mode_reland_counts_as_landed(
    tmp_path: Path, monkeypatch
):
    """覆盖模式：目标已存在删旧重落，算本次实际落地，进 record + landed_mapping。"""
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)

    src = tmp_path / 'source.mkv'
    tgt = tmp_path / 'out' / 'target.mkv'
    src.write_bytes(b'fresh')
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tgt.write_bytes(b'stale')

    trans = Trans(
        {src: tgt},
        'uuid-overwrite',
        force_mode='复制',
        force_overwrite='覆盖',
    )
    result = trans.trans_file()

    assert result is True
    assert trans.landed_mapping == {src: tgt}
    assert trans.skipped_mapping == {}
    assert tgt.read_bytes() == b'fresh'


def test_trans_hardlink_failure_falls_back_to_symlink_when_enabled(
    tmp_path: Path, monkeypatch
):
    """hardlink_fallback_to_symlink=True（默认）：硬链失败降级软链接，落地成功。"""
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        'src.config.config_manager.cm.get_config',
        lambda key, _default=None: True if key == 'hardlink_fallback_to_symlink' else '链接',
    )

    src = tmp_path / 'source.mkv'
    tgt = tmp_path / 'out' / 'target.mkv'
    src.write_bytes(b'data')
    tgt.parent.mkdir(parents=True, exist_ok=True)

    # 模拟硬链失败
    import src.rename.trans as trans_mod
    real_link = trans_mod.os.link

    def fail_link(src_p, tgt_p):
        raise OSError(_EXDEV, 'cross-device link')

    monkeypatch.setattr(trans_mod.os, 'link', fail_link)
    monkeypatch.setattr(trans_mod.os, 'symlink', trans_mod.os.symlink)

    trans = Trans({src: tgt}, 'uuid-fallback', force_mode='链接')
    result = trans.trans_file()

    assert result is True
    assert tgt.is_symlink()
    assert trans.landed_mapping == {src: tgt}


def test_trans_hardlink_failure_no_fallback_when_disabled(
    tmp_path: Path, monkeypatch
):
    """hardlink_fallback_to_symlink=False：硬链失败不降级，记 partial_failure。"""
    monkeypatch.setattr('src.rename.trans.RECORD_PATH', tmp_path / 'record')
    (tmp_path / 'record').mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        'src.config.config_manager.cm.get_config',
        lambda key, _default=None: False if key == 'hardlink_fallback_to_symlink' else '链接',
    )

    src = tmp_path / 'source.mkv'
    tgt = tmp_path / 'out' / 'target.mkv'
    src.write_bytes(b'data')
    tgt.parent.mkdir(parents=True, exist_ok=True)

    import src.rename.trans as trans_mod

    def fail_link(src_p, tgt_p):
        raise OSError(_EXDEV, 'cross-device link')

    monkeypatch.setattr(trans_mod.os, 'link', fail_link)

    trans = Trans({src: tgt}, 'uuid-no-fallback', force_mode='链接')
    result = trans.trans_file()

    assert isinstance(result, str)
    assert 'partial_failure' in result
    assert not tgt.exists()
    assert trans.landed_mapping == {}
