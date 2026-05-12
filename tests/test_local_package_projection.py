from src.rename.case_agent.local_package_projection import build_local_package_projection


def test_local_package_projection_is_compact_and_marks_truncation():
    files = []
    for index in range(1, 5001):
        files.append({
            'relative_path': f'Series/Very Long Disc Folder Name {index:04d}/Very Long Episode File Name {index:04d} - Extended Edition - Special Cut - Bonus Material.mkv',
            'name': f'Very Long Episode File Name {index:04d} - Extended Edition - Special Cut - Bonus Material.mkv',
            'suffix': '.mkv',
            'is_video': True,
            'is_main_video_candidate': True,
            'is_supplemental_candidate': False,
        })
    files.extend([
        {'relative_path': 'Series/[GroupA] OP01.mkv', 'name': '[GroupA] OP01.mkv', 'suffix': '.mkv', 'is_video': True, 'is_main_video_candidate': False, 'is_supplemental_candidate': True},
        {'relative_path': 'Series/Subs/EP01.ass', 'name': 'EP01.ass', 'suffix': '.ass', 'is_video': False, 'is_main_video_candidate': False, 'is_supplemental_candidate': False},
    ])

    projection = build_local_package_projection(
        {
            'root_name': 'Series',
            'root_path': 'D:/Media/Series',
            'directory_structure': [f'Series/Very Long Disc Folder Name {i:04d}' for i in range(1, 220)],
            'files': files,
        },
        hard_limit_bytes=10_000,
    )

    assert projection['root_name'] == 'Series'
    assert projection['root_path'] == 'D:/Media/Series'
    assert projection['file_count'] == 5002
    assert projection['lpa_projection_truncated'] is True
    assert projection['projection_bytes'] <= 10_000
    assert 'files' not in projection
    assert projection['representative_samples']['first']
    assert projection['directory_cluster_summary']
    assert projection['raw_path_text_samples']
    assert 'abnormal' not in projection['representative_samples']
    assert 'release_group_candidates' not in projection
    assert 'title_cue_candidates' not in projection


def test_local_package_projection_counts_supplemental_video_out_of_main():
    projection = build_local_package_projection(
        {
            'root_name': 'Series',
            'root_path': 'D:/Media/Series',
            'files': [
                {'relative_path': 'Series/Ep01.mkv', 'name': 'Ep01.mkv', 'suffix': '.mkv', 'is_video': True, 'is_main_video_candidate': True, 'is_supplemental_candidate': False},
                {'relative_path': 'Series/[NCOP].mkv', 'name': '[NCOP].mkv', 'suffix': '.mkv', 'is_video': True, 'is_main_video_candidate': False, 'is_supplemental_candidate': True},
            ],
        },
    )

    assert projection['media_counts']['main'] == 1
