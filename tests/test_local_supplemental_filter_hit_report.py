import json

from tools.report_local_supplemental_filter_hits import build_report


def _write_sample(path, paths):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'files': [{'path': item} for item in paths]}, ensure_ascii=False), encoding='utf-8')


def test_hit_report_counts_rule_sample_distribution(tmp_path):
    raw_root = tmp_path / 'raw'
    _write_sample(raw_root / 'a.json', ['SPs/Show [IV01].mkv', 'Show 01.mkv'])
    _write_sample(raw_root / 'nested' / 'b.json', ['SPs/Other [IV02].mkv', 'SPs/Other [PV01].mkv'])

    report = build_report(raw_root, min_sample_count=2, sample_limit=1)
    rules = {rule['rule_id']: rule for rule in report['rules']}

    assert report['sample_count'] == 2
    assert report['video_count'] == 4
    assert report['filtered_video_count'] == 3
    assert rules['interview_video_token']['file_count'] == 2
    assert rules['interview_video_token']['sample_count'] == 2
    assert rules['interview_video_token']['low_sample_coverage'] is False
    assert rules['bracketed_pv']['file_count'] == 1
    assert rules['bracketed_pv']['sample_count'] == 1
    assert rules['bracketed_pv']['low_sample_coverage'] is True
    assert len(rules['interview_video_token']['examples']) == 1
