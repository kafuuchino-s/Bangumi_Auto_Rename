"""rename process.py `_collect_bgm_subject_names` 单测（阶段 1：多季覆盖数据层）。

验证多 subject 合集（如 0091 鬼灭 S01+S02+S03+剧场版）落盘时：
- 每 subject 的 name/name_cn 都查都写（不只主体）
- per-video→subject 映射（video_subject_map）正确
- 主体单值 name/name_cn + subject_ids 向后兼容
- Bangumi 查询失败不阻塞（该 subject name 空）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.rename.process import Rename


def _item(source: str, target_path: str, subject_id: int, media_kind: str = 'tv',
          disposition: str = 'map_to_tmdb') -> SimpleNamespace:
    """用 SimpleNamespace 绕过 TmdbRenameDestination 必填字段——
    _collect_bgm_subject_names 只读 item.disposition / item.target_path /
    item.bangumi_assignment.target.bangumi_subject_id / media_kind，不碰其他字段。"""
    return SimpleNamespace(
        disposition=disposition,
        target_path=target_path,
        bangumi_assignment=SimpleNamespace(
            target=SimpleNamespace(
                bangumi_subject_id=subject_id, media_kind=media_kind,
            ),
        ),
    )


def _fake_subject(sid: int):
    """每个 subject 返回不同 name/name_cn。"""
    table = {
        245665: ('鬼滅の刃', '鬼灭之刃'),
        350764: ('鬼滅の刃 無限列車編', '鬼灭之刃 无限列车编'),
        328195: ('鬼滅の刃 遊郭編', '鬼灭之刃 游郭编'),
        291494: ('劇場版 鬼滅の刃 無限列車編', '剧场版 鬼灭之刃 无限列车编'),
    }
    name, name_cn = table.get(sid, ('', ''))
    return SimpleNamespace(name=name, name_cn=name_cn)


def _plan(items):
    return SimpleNamespace(items=items)


def test_collect_multi_subject_names_and_video_map():
    """多 subject 合集：每 subject name/name_cn 都查 + video_subject_map per-video。"""
    plan = _plan([
        # S01 subject 245665 (26 video，主体)
        *[_item(f'src/S01/{i:02d}.mkv',
                f'H:/Anime/Demon Slayer - S01E{i:02d}.mkv', 245665)
          for i in range(1, 27)],
        # S02 subject 350764 (7 video)
        *[_item(f'src/S02/{i:02d}.mkv',
                f'H:/Anime/Demon Slayer - S02E{i:02d}.mkv', 350764)
          for i in range(1, 8)],
        # S03 subject 328195 (11 video)
        *[_item(f'src/S03/{i:02d}.mkv',
                f'H:/Anime/Demon Slayer - S03E{i:02d}.mkv', 328195)
          for i in range(1, 12)],
        # 剧场版 subject 291494 (1 movie)
        _item('src/movie.mkv', 'H:/Anime/Demon Slayer Movie.mkv', 291494, 'movie'),
        # supplemental（不进 missing，不应进 video_subject_map）
        _item('src/PV.mkv', '', 245665, disposition='unmapped_supplemental'),
    ])
    with patch('src.bangumi.client.BangumiClient') as MockClient:
        MockClient.return_value.get_subject.side_effect = _fake_subject
        info = Rename._collect_bgm_subject_names(plan)
    # 4 个 subject 都查到 name/name_cn
    subjects = info['subjects']
    assert len(subjects) == 4
    by_id = {s['id']: s for s in subjects}
    assert by_id[245665]['name'] == '鬼滅の刃'
    assert by_id[245665]['name_cn'] == '鬼灭之刃'
    assert by_id[350764]['name'] == '鬼滅の刃 無限列車編'
    assert by_id[328195]['name_cn'] == '鬼灭之刃 游郭编'
    assert by_id[291494]['media_kind'] == 'movie'
    assert by_id[245665]['assignment_count'] == 26
    # 主体（assignment 最多）= 245665，向后兼容 name/name_cn
    assert info['name'] == '鬼滅の刃'
    assert info['name_cn'] == '鬼灭之刃'
    assert info['subject_ids'] == [245665, 291494, 328195, 350764]
    # video_subject_map：每 video basename → subject_id，supplemental 不进
    vmap = info['video_subject_map']
    assert vmap['Demon Slayer - S01E01.mkv'] == 245665
    assert vmap['Demon Slayer - S02E01.mkv'] == 350764
    assert vmap['Demon Slayer - S03E11.mkv'] == 328195
    assert vmap['Demon Slayer Movie.mkv'] == 291494
    # supplemental 的 PV 不在 map（target_path 空）
    assert 'PV.mkv' not in vmap
    assert len(vmap) == 26 + 7 + 11 + 1


def test_collect_single_subject_back_compat():
    """单 subject：subjects 1 个 + video_subject_map + 旧字段兼容。"""
    plan = _plan([
        _item('src/01.mkv', 'H:/Anime/Foo - S01E01.mkv', 100),
        _item('src/02.mkv', 'H:/Anime/Foo - S01E02.mkv', 100),
    ])
    with patch('src.bangumi.client.BangumiClient') as MockClient:
        MockClient.return_value.get_subject.side_effect = _fake_subject
        info = Rename._collect_bgm_subject_names(plan)
    assert len(info['subjects']) == 1
    assert info['subjects'][0]['id'] == 100
    assert info['name'] == ''  # 100 不在 _fake_subject table，name 空
    assert info['video_subject_map']['Foo - S01E01.mkv'] == 100


def test_collect_bangumi_failure_does_not_block():
    """Bangumi 查询失败：subjects 仍有条目（name 空），不抛异常。"""
    plan = _plan([
        _item('src/01.mkv', 'H:/Anime/Foo - S01E01.mkv', 999),
    ])
    with patch('src.bangumi.client.BangumiClient') as MockClient:
        MockClient.return_value.get_subject.side_effect = RuntimeError('network')
        info = Rename._collect_bgm_subject_names(plan)
    assert len(info['subjects']) == 1
    assert info['subjects'][0]['name'] == ''  # 失败回退空
    assert info['video_subject_map']['Foo - S01E01.mkv'] == 999  # map 仍有


def test_collect_no_subjects_empty():
    """无 subject（assignment 全空）：返回空结构，不崩。"""
    plan = _plan([
        SimpleNamespace(disposition='map_to_tmdb', target_path='H:/Anime/Foo.mkv',
                        bangumi_assignment=None),
    ])
    info = Rename._collect_bgm_subject_names(plan)
    assert info['subjects'] == []
    assert info['video_subject_map'] == {}
    assert info['name'] == ''
