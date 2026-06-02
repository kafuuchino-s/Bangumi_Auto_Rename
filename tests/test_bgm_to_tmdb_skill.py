from __future__ import annotations

import json
import re
from pathlib import Path

from src.rename.bgm_to_tmdb import BgmToTmdbRecipeParams


def test_tmdb_bridge_skill_teaches_recipe_first_contract_and_valid_params_shape() -> None:
    text = Path('.pi/skills/tmdb-bridge-contract/SKILL.md').read_text(encoding='utf-8')

    assert 'validate_bgm_to_tmdb_bridge_recipe_params' in text
    assert 'submit_bgm_to_tmdb_bridge_recipe_params' in text
    assert 'Per-source `source_path -> tv:<id>:SxxEyy` mappings are debug/fallback material' in text
    assert 'Raw `validate_bgm_to_tmdb_bridge` and `submit_bgm_to_tmdb_bridge` are debug/fallback tools only' in text
    assert 'source_path -> tv:<id>:SxxEyy' in text
    assert 'tv:<tmdb_id>:SxxEyy' in text
    assert 'movie:<tmdb_id>' in text
    assert 'Names are semantic evidence' not in text
    assert 'TMDB names are semantic evidence, not output identity' in text
    assert 'recap/summary/CM/bonus-title searches' in text
    assert 'Episode Title Alignment' in text
    assert 'episode_title_cards_sample' in text
    assert 'use them as stronger evidence than a fuzzy series title' in text
    assert 'tmdb_absent_group' in text
    assert 'tmdb_target_absent' in text
    assert 'Franchise Anchor First' in text
    assert 'anchor search -> hydrated TMDB legal graph' in text
    assert 'strongest next evidence layer' in text
    assert 'TMDB Legal Graph Closure' in text
    assert 'Use the accepted BGM plan as the frontier, not the local file tree.' in text
    assert 'Put BGM specials, OVA/OAD, recap movies, spans, and side-story subjects' in text
    assert 'Search additional TMDB titles only for graph misses, conflicting candidates' in text
    assert 'Stop closure when another graph/search pass adds no legal TMDB nodes' in text
    assert 'use `tmdb_absent_group` for BGM-mapped nodes that Bangumi has but TMDB does not expose' in text
    assert 'Use `supplemental_group` only for assignments that were already Local-to-Bangumi supplemental.' in text
    assert 'Do not convert BGM-mapped OVA/OAD/SP/movie/side-story nodes to supplemental' in text

    example = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    assert example is not None
    payload = json.loads(example.group(1))
    params = BgmToTmdbRecipeParams.model_validate(payload)

    assert 'mappings' not in payload
    assert params.version == 1
    assert params.rules[0].rule_type == 'episode_sequence'
    assert params.rules[0].target_tmdb.tmdb_ref == 'tv:45844'
    assert params.rules[0].target_tmdb.episode_range == '1-26'
    assert params.rules[1].rule_type == 'tmdb_absent_group'
    assert params.rules[2].rule_type == 'supplemental_group'
