from pathlib import Path

from src.rename.local_evidence import build_local_evidence


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'')
    return path


def test_local_evidence_filters_clear_video_extras_from_main_refs(tmp_path: Path):
    root = tmp_path / '[VCB-Studio] ARIA The AVVENIRE [Ma10p_1080p]'
    _touch(root / '[VCB-Studio] ARIA The AVVENIRE [Ma10p_1080p][x265_flac].mkv')
    _touch(root / 'SPs' / '[VCB-Studio] ARIA The AVVENIRE [Menu][Ma10p_1080p][x265].mkv')
    _touch(root / 'SPs' / '[VCB-Studio] ARIA The AVVENIRE [PV01][Ma10p_1080p][x265_flac].mkv')
    _touch(root / 'SPs' / '[VCB-Studio] ARIA The AVVENIRE [Preview01_1][Ma10p_1080p][x265_flac].mkv')
    _touch(root / 'SPs' / '[VCB-Studio] ARIA The AVVENIRE [NCED][Ma10p_1080p][x265_flac].mkv')

    evidence = build_local_evidence(root)

    assert evidence.root_name == root.name
    assert evidence.video_count == 5
    assert evidence.main_video_count == 1
    assert evidence.supplemental_candidate_count == 4
    assert evidence.directory_structure == ['SPs']
    main_files = [file for file in evidence.files if file.is_main_video_candidate]
    assert len(main_files) == 1
    assert main_files[0].file_id == 'file_001'
    assert main_files[0].name == '[VCB-Studio] ARIA The AVVENIRE [Ma10p_1080p][x265_flac].mkv'


def test_local_evidence_does_not_extract_filename_semantics(tmp_path: Path):
    root = tmp_path / 'Series TV OVA'
    for episode in range(1, 14):
        _touch(root / f'Series - {episode:02d}.mkv')
    _touch(root / 'Series - 14 OVA.mkv')
    _touch(root / 'Series - 15 SP.mkv')

    evidence = build_local_evidence(root)

    assert evidence.main_video_count == 15
    by_name = {file.name: file for file in evidence.files}
    assert by_name['Series - 14 OVA.mkv'].is_main_video_candidate is True
    assert by_name['Series - 15 SP.mkv'].is_main_video_candidate is True
    assert not hasattr(by_name['Series - 14 OVA.mkv'], 'number_tokens')
    assert not hasattr(by_name['Series - 14 OVA.mkv'], 'generic_cues')
    assert evidence.fact_surface is not None
    fact_by_path = {fact.relative_path: fact for fact in evidence.fact_surface.files}
    assert fact_by_path['Series - 14 OVA.mkv'].path_facts.raw_number_tokens
    assert fact_by_path['Series - 14 OVA.mkv'].path_facts.raw_number_tokens[0]['source'] == 'raw_path_text'


def test_local_evidence_leaves_sxxeyy_and_hash_episode_to_ai(tmp_path: Path):
    root = tmp_path / 'Example S2'
    _touch(root / 'Example - S02E03.mkv')
    _touch(root / 'Example #00.mkv')

    evidence = build_local_evidence(root)
    by_name = {file.name: file for file in evidence.files}
    assert by_name['Example - S02E03.mkv'].is_main_video_candidate is True
    assert by_name['Example #00.mkv'].is_main_video_candidate is True
    assert not hasattr(by_name['Example - S02E03.mkv'], 'season_episode_tokens')
    assert evidence.fact_surface is not None
