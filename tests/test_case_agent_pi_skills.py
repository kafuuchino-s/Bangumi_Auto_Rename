from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / '.pi' / 'skills'


def test_pi_thin_skills_exist_with_expected_frontmatter():
    for name in ['bangumi-api', 'anime-release-reading', 'organize-recipe-contract']:
        path = SKILL_ROOT / name / 'SKILL.md'
        assert path.exists()
        text = path.read_text(encoding='utf-8')
        assert f'name: {name}' in text
        assert 'description:' in text
        assert 'disable-model-invocation: true' not in text
    recipe_skill = (SKILL_ROOT / 'organize-recipe-contract' / 'SKILL.md').read_text(encoding='utf-8')
    assert 'not needed for an ordinary first draft' in recipe_skill


def test_pi_skills_document_compact_evidence_flow_and_legal_recipe_episode_types():
    bangumi_skill = (SKILL_ROOT / 'bangumi-api' / 'SKILL.md').read_text(encoding='utf-8')
    release_skill = (SKILL_ROOT / 'anime-release-reading' / 'SKILL.md').read_text(encoding='utf-8')
    recipe_skill = (SKILL_ROOT / 'organize-recipe-contract' / 'SKILL.md').read_text(encoding='utf-8')
    recipe_reference = (SKILL_ROOT / 'organize-recipe-contract' / 'references' / 'recipe-params.md').read_text(encoding='utf-8')
    recipe_docs = recipe_skill + '\n' + recipe_reference
    template = json.loads((SKILL_ROOT / 'organize-recipe-contract' / 'assets' / 'organize_recipe.template.json').read_text(encoding='utf-8'))

    assert 'Working Board' in recipe_skill
    assert 'local_group | target evidence | recipe rule | status | open issue' in recipe_skill
    for status in ['unknown', 'anchored', 'draftable', 'supplemental_candidate', 'side_frontier', 'repairing', 'accepted']:
        assert status in recipe_skill
    assert 'not as fixed-layer commands' in recipe_skill
    assert 'Call the first validation when every visible local group has either' in recipe_skill
    assert 'a testable mapped rule' in recipe_skill
    assert 'a testable `disposition: "non_bangumi_or_supplemental"` rule' in recipe_skill
    assert 'If you write or think "ready", "enough", "validate", or "submit"' in recipe_skill
    assert 'Related Graph Closure' in recipe_skill
    assert 'Put every remaining anime/video-shaped non-main group into a side frontier' in recipe_skill
    assert 'add that subject to the anchor set' in recipe_skill
    assert 'Only after this closure stalls should unresolved frontier groups become final supplemental candidates.' in recipe_skill
    assert 'Do not downgrade that group to `non_bangumi_or_supplemental` just to make validation pass' in recipe_skill
    assert 'After `validate_organize_recipe_params`, `submit_organize_recipe_params`, or a patch tool returns invalid/review feedback, stop broad exploration.' in recipe_skill
    assert 'Repair the named rule/path/target first.' in recipe_skill
    assert 'Repair is not a permission to lower semantic quality.' in recipe_skill
    assert 'When uncovered paths and duplicate coverage belong to the same local group' in recipe_skill
    assert 'duplicate_episode_numbers_in_group' in recipe_skill
    assert 'exact_paths` must be complete visible `source_path` strings' in recipe_skill
    assert '`group_ref` is a local selector shorthand' in recipe_skill
    assert 'it never chooses `subject_id`, `episode_id`, `media_kind`, `episode_type`, disposition, or supplemental status' in recipe_skill
    assert 'Do not use `SP` as `episode_offset`' in recipe_skill
    assert 'For `SP01` files that map to Bangumi rows 1-13, keep `episode_range: "1-13"` and `episode_offset: "EP"`.' in recipe_skill
    assert 'SP filenames and `media_kind: "sp"` do not imply `episode_type: "special"`.' in recipe_skill
    assert 'check `episode_type` before converting the group to supplemental' in recipe_skill
    assert 'Supplemental rules do not need `episode_range`, `episode_offset`, `episode_type`, `subject_id`, or `episode_id`.' in recipe_skill
    assert 'budget_exhausted` is a runner outcome' in recipe_skill
    assert 'old `final_result.json` files, repository tests, and templates are not evidence' in recipe_skill

    assert 'This skill is about evidence, not recipe orchestration.' in bangumi_skill
    assert 'Evidence Enough' in bangumi_skill
    assert 'Episode count alone is not identity evidence.' in bangumi_skill
    assert 'One bounded related graph is useful evidence, not proof that the whole franchise is exhausted.' in bangumi_skill
    assert 'For one standalone main-title group, direct subject search plus episode evidence is usually enough.' in bangumi_skill
    assert 'search one reliable anchor first, then use `expand_related_graph` from that anchor' in bangumi_skill
    assert 'This is usually more stable than direct-searching every local group independently.' in bangumi_skill
    assert 'Use related graph closure for packages with non-main local groups:' in bangumi_skill
    assert 'Repeat from newly mapped side subjects while new anime/video targets explain remaining frontier groups.' in bangumi_skill
    assert 'Do not treat a verifier issue on a mapped frontier rule as proof that the group is supplemental.' in bangumi_skill
    assert 'Numbered `SP01`, `SP02`, `Special 1`, or `S00E01` files are candidate special entries' in bangumi_skill
    assert 'For a numbered side-content group, do not stop at the parent TV subject.' in bangumi_skill
    assert 'Direct side-title search is a fallback for graph misses, conflicts, or unresolved qualified groups.' in bangumi_skill
    assert 'Searching only the parent season title' in bangumi_skill
    assert 'is parent-season evidence, not evidence for a side-content group' in bangumi_skill
    assert 'If it does not, a supplemental rule is acceptable; the reason should say which targeted title/episode evidence failed.' in bangumi_skill
    assert '`SP` belongs in the filename selector or reasoning, not in `episode_offset`' in bangumi_skill
    assert 'SP file naming is not the same as Bangumi row type.' in bangumi_skill
    assert 'not a chosen target' in bangumi_skill

    assert 'This skill is for reading the local release. It does not choose Bangumi targets' in release_skill
    assert 'Read local structure through navigation first:' in release_skill
    assert 'Treat group cards and detail pages as factual grouping evidence only.' in release_skill
    assert 'Notice duplicate locator variants inside one group' in release_skill
    assert 'Numbered special tokens such as `SP01`, `SP02`, `Special 1`, or `S00E01` are candidate special locators.' in release_skill
    assert 'The parent TV subject lacking SP rows does not by itself make the group supplemental.' in release_skill
    assert 'It is not a recipe `episode_offset`' in release_skill
    assert 'Missing local duration is not negative evidence.' in release_skill
    assert 'File-name tokens are investigation hints only.' in release_skill

    assert 'early_bangumi_evidence_bundle' not in recipe_skill
    assert 'case_quick_start' not in recipe_skill
    assert 'recommended_recipe' not in bangumi_skill
    assert 'recommended_recipe' not in recipe_docs
    assert 'draft_recipe' not in bangumi_skill
    assert 'draft_recipe' not in recipe_docs
    assert '`group_ref` / `local_group_ref` are accepted as local selector shorthand' in recipe_docs
    assert 'validate_organize_recipe_params_patch' in recipe_skill
    assert 'submit_organize_recipe_params_patch' in recipe_skill
    assert 'Numbered SP And Short Side Content' in recipe_skill
    assert 'search one reliable anchor, expand its anime related graph, and match the remaining local group titles against that graph' in recipe_skill
    assert 'A main-season subject with no SP rows is weak negative evidence' in recipe_skill
    assert 'Do not use a missing parent-season SP list as the only reason for a numbered side-content group.' in recipe_skill
    assert 'Do not use a parent-season search as negative evidence for a side-content group.' in recipe_skill
    assert 'A parent TV subject that lacks SP rows is not enough to make a named side-content group supplemental.' in recipe_reference
    assert 'Do not change it to `non_bangumi_or_supplemental` merely to make the verifier pass.' in recipe_reference
    assert 'Use graph closure for side frontiers.' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'that subject becomes a new anchor' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert template['rules'][0]['target']['episode_type'] == 'regular'
    assert template['rules'][0]['select']['filename_regex'] == 'Episode {ep}.mkv'
    return

    assert 'find_bangumi_targets_for_local_file' in bangumi_skill
    assert 'Operating Loop' in bangumi_skill
    assert 'One useful graph pass plus matching episode rows is usually enough to draft params and validate.' in bangumi_skill
    assert 'practical evidence set' in bangumi_skill
    assert '`run_progress` may show evidence-call counts' in bangumi_skill
    assert 'not recommendations or target decisions' in bangumi_skill
    assert 'Read `relation_subjects` first' in bangumi_skill
    assert 'compact fact lookup' in bangumi_skill
    assert 'not a chosen target' in bangumi_skill
    assert 'validate_organize_recipe_params' in bangumi_skill
    assert 'submit_organize_recipe_params' in bangumi_skill
    assert 'recommended_recipe' not in bangumi_skill
    assert 'recommended_recipe' not in recipe_docs
    assert 'draft_recipe' not in bangumi_skill
    assert 'draft_recipe' not in recipe_docs
    assert 'ready_to_validate' not in bangumi_skill
    assert 'ready_to_validate' not in recipe_docs
    assert 'Preferred Params Shape' in recipe_reference
    assert 'or `{ep:02}` / `{ep:02d}` when file names use zero-padded numbers' in recipe_docs
    assert 'Python escapes regex characters' in recipe_docs
    assert 'Do not paste one literal filename into `source_pattern`' in recipe_docs
    assert '`source_path` and `path` are accepted as one-file `exact_paths` aliases' in recipe_docs
    assert '`group_ref` / `local_group_ref` are accepted as local selector shorthand' in recipe_docs
    assert 'If `episode_offset` is omitted or null, Python defaults it to `EP`.' in recipe_docs
    assert 'infers exact episode row type when possible' in recipe_skill
    assert 'Keep params minimal.' in recipe_docs
    assert '`source_pattern` may include folder segments' in recipe_docs
    assert 'canonicalizes it from the exposed episode row' in recipe_docs
    assert 'Before `fail_closed`, a best-effort params validation is useful' in recipe_skill
    assert 'If exact Bangumi subject/episode evidence exists, map it and validate' in release_skill
    assert 'the verifier resolves the calculated number against Bangumi episode `sort` first' in recipe_docs
    assert 'episode_number_field: "ep"' in recipe_docs
    assert 'CRC/hash/checksum' in recipe_docs
    assert 'FLAC` versus `FLACx2`' in recipe_docs
    assert 'split the local range and use a related season/second-part subject' not in recipe_docs
    assert 'split the local range and use a related season/cour/part subject' in recipe_docs
    assert 'use `episode_offset: "EP"`' in recipe_docs
    assert 'Treat params validation as a trial check of the current semantic recipe' in bangumi_skill
    assert 'Validation is a trial check that can return verifier issues or review warnings' in bangumi_skill
    assert 'get_local_recipe_params_scaffold' in bangumi_skill
    assert 'not a target recommendation' in bangumi_skill
    assert 'Search Discipline' in bangumi_skill
    assert 'Queries work best without `Bangumi`, `BGM`, `subject`, or database words.' in bangumi_skill
    assert 'A numbered run usually needs one representative search plus episode evidence' in bangumi_skill
    assert 'If repeated searches reuse the same franchise/title words without new target evidence' in bangumi_skill
    assert 'Repeated broad searches are weak evidence for later episode rows.' in bangumi_skill
    assert 'Specials, OVAs, OADs, Movies' in bangumi_skill
    assert 'Use frontier exhaustion for final `fail_closed` or final supplemental justification' in bangumi_skill
    assert 'same-folder collection with many named movie/special/OVA files' in bangumi_skill
    assert 'Individual title searches are most useful for graph misses, verifier/review feedback, or real conflicts.' in bangumi_skill
    assert 'After a series anchor is confirmed, use the relation graph to find specifically named movies' in bangumi_skill
    assert 'Adjacent package numbers are weak evidence for mapping two differently named movie/special files' in bangumi_skill
    assert 'not as a first-validation gate' in bangumi_skill
    assert 'testable recipe, not exhaustive graph traversal before validation' in bangumi_skill
    assert 'should not block first validation indefinitely' in bangumi_skill
    assert 'Long special/movie-shaped files can be one-episode Bangumi subjects.' in bangumi_skill
    assert 'Bangumi may expose their single episode as `episode_type: "regular"`' in bangumi_skill
    assert 'does not need to match `media_kind`' in bangumi_skill
    assert '`expand_related_graph`: recursive related-subject graph' in bangumi_skill
    assert 'If `episode_rows_limited` is true' in bangumi_skill
    assert '`task_source_path` is the task root, not a local file.' in bangumi_skill
    assert 'that field filters relation labels, not subject type' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert '`subject_types: ["anime"]` to keep only anime/video subjects' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert '`expand_related_graph({ "subject_ids": [12345]' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'Read `relation_subjects` first.' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'not a proof that the series graph is complete' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert '`frontier_exhausted`: true only when this bounded traversal has no more seen anime/video subjects' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert '`next_subject_ids_to_expand`: subject IDs to use as the next `subject_ids` seed' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'this tool is already scoped to Bangumi' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'Filter returned subjects to anime/video-shaped entries' in (SKILL_ROOT / 'bangumi-api' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'Core loop: build a testable recipe first, then let validation drive repair.' in recipe_skill
    assert '`validate_organize_recipe_params` is a trial check, not final submission.' in recipe_skill
    assert 'An invalid or review result is useful feedback for patch repair' in recipe_skill
    assert '`get_local_selector_scaffold` and `get_local_recipe_params_scaffold` can lower selector friction' in recipe_skill
    assert 'params may use `group_ref` as a local selector shorthand' in recipe_skill
    assert 'does not choose Bangumi target IDs' in recipe_skill
    assert 'A practical pre-validation evidence set' in recipe_skill
    assert '`run_progress` reports progress facts' in recipe_skill
    assert 'not a target recommendation or next-step instruction' in recipe_skill
    assert 'Frontier exhaustion is for final `fail_closed` or final supplemental reasoning' in recipe_skill
    assert 'one-file movie-shaped subject' in recipe_skill
    assert 'subject-level movie rule' in recipe_reference
    assert 'If the same franchise/title words keep appearing in searches while the package structure is already clear' in release_skill
    assert 'useful evidence budget is representative lookup/search for active groups' in release_skill
    assert 'not a fixed-layer instruction' in release_skill
    assert 'Treat params validation as a trial check of a compact grouping' in release_skill
    assert 'selector gaps, duplicate targets, missing episode rows, and review warnings' in release_skill
    assert 'use `get_local_selector_scaffold(group_ref)` or `get_local_recipe_params_scaffold(group_ref)`' in release_skill
    assert 'target semantics still come from Bangumi evidence' in release_skill
    assert 'eight custom-tool calls' not in bangumi_skill
    assert 'eight custom-tool calls' not in release_skill
    assert 'eight custom-tool calls' not in recipe_docs
    assert 'exactly one visible local file' not in bangumi_skill
    forbidden_skill_terms = [
        'single ' + 'explicit ' + 'OVA/OAD/SP/Movie',
        'local_' + 'filename_patterns',
        'local_' + 'shape_hint',
        'residual_' + 'paths',
    ]
    for term in forbidden_skill_terms:
        assert term not in bangumi_skill
        assert term not in release_skill
        assert term not in recipe_docs
    assert 'domin' + 'ant' not in release_skill
    assert 'Do not use short refs' not in recipe_skill
    assert 'Use real identifiers in recipes.' in recipe_skill
    assert 'Start from `case_input.case_overview` or `get_case_overview()` and inspect `case_input.scratch_paths`.' in recipe_skill
    assert 'case_quick_start' not in recipe_skill
    assert '`case_input.local_recipe_skeleton`' not in recipe_skill
    assert 'early_bangumi_evidence_bundle' not in recipe_skill
    assert 'Use `list_local_groups(detail=false)` as the local group index.' in recipe_skill
    assert 'Choose the drill-down path yourself.' in recipe_skill
    assert 'not a route recommendation' in recipe_skill
    assert '`episode_range` is the local captured file-number range' in recipe_skill
    assert 'The local helper is useful for debugging' in recipe_skill
    assert 'ready for `goal_complete`' in recipe_skill
    assert 'Old run artifacts' in recipe_skill
    assert 'are not evidence for the current case' in recipe_skill
    assert 'rather than by printing recipe JSON as plain text' in recipe_skill
    assert 'Use `disposition: "non_bangumi_or_supplemental"`' in recipe_docs
    assert 'Do not write boolean disposition flags such as `non_bangumi_or_supplemental: true`' in recipe_docs
    assert 'A related Bangumi special/OVA subject is candidate evidence only' in recipe_skill
    assert 'Do not hold the entire case waiting for exhaustive SP certainty' in recipe_skill
    assert 'validation reports `missing_target_episode` after a targeted episode-list/window check' in recipe_docs
    assert '`source_unit: "single_file_multi_episode"`' in recipe_docs
    assert 'Do not write boolean source-unit flags' in recipe_docs
    assert '"name": "Bonus extras"' in recipe_docs
    assert '`range_start` plus `range_end` is accepted as a range alias' in recipe_docs
    assert 'Numeric `offset: 0` is treated as no shift (`EP`)' in recipe_docs
    assert '`OVA2`, `SP3`, or `Movie 2` usually means the second OVA/special/movie-shaped item' in release_skill
    assert '`NCOP`, `NCED`, `Creditless`, `Textless`, `Clean OP`, `Clean ED`' in release_skill
    assert '`BD`, `BluRay`, `BDRip`, `DVD`, `WEB`, `WEB-DL`, `WEBRip`' in release_skill
    assert '`Bangaihen`: side story or extra chapter.' in release_skill
    assert '`Soushuuhen`, `Soshuhen`, `Digest`: recap or compilation.' in release_skill
    assert 'Keep season and title qualifiers that distinguish entries inside one franchise.' in release_skill
    assert 'Read local structure through navigation first:' in release_skill
    assert 'List visible `source_path` values by opening `get_local_group_detail(group_ref, detail=true)`' in release_skill
    assert 'Notice when numbering restarts.' in release_skill
    assert 'The directory-structure-first workflow applies to every case.' in release_skill
    assert 'treat a franchise-root or earlier-season match as weak evidence' in release_skill
    assert 'A mechanically valid recipe is still wrong if the subject is the wrong season' in release_skill
    assert '`Part A/B`, `Part 1/2`, `CD1/CD2`, and `Disc1/Disc2` can be split files' in release_skill
    assert "`TV Ver.`, `BD Ver.`, `DVD Ver.`, `Web Ver.`, `Director's Cut`, `Extended`, and `Uncut`" in release_skill
    assert '`Special Program`, `Pre-release Special`, `Before Release`, `公開直前`, `特別番組`' in release_skill
    assert '`Original Soundtrack`, `Character Song`, `Drama CD`, `Radio CD`' in release_skill
    assert 'File-name tokens are investigation hints only.' in release_skill
    assert 'Roman numerals such as `II`, `III`, `IV`, `V`, and `XV` are ambiguous.' in release_skill
    assert 'Numbered special tokens such as `SP01`, `SP02`, `Special 1`, or `S00E01` are candidate special locators.' in release_skill
    assert 'case_input.context.local_files[].container_facts.duration_seconds' in release_skill
    assert 'container_facts.chapter_count' in release_skill
    assert 'prefer `source_unit: "single_file_multi_episode"`' in release_skill
    assert 'Missing local duration is not negative evidence.' in release_skill
    assert 'Missing Bangumi duration is also not negative evidence.' in release_skill
    assert 'large runtime mismatches as a recheck signal, not an automatic verdict' in release_skill
    assert 'Some legitimate anime targets are long by design.' in release_skill
    assert 'one long local file per Bangumi episode' in release_skill
    assert '`Extended Edition`, `Director' in release_skill
    assert 'TV premieres and finales are sometimes broadcast as enlarged single episodes.' in release_skill
    assert 'When one case contains many folders or many seasons/movies, split the visible paths by folder title and content shape before searching broadly.' in release_skill
    assert 'In movie collections, package labels like `#01`, `#02`, or `MOVIE 01-09` are release locators' in release_skill
    assert 'make a local title checklist first' in release_skill
    assert 'direct title searches are most useful for names still missing or conflicting after checking the graph' in release_skill
    assert 'Episode-list fetches are most useful for non-movie exceptions' in release_skill
    assert 'For movie-box filenames, `#01` / `#02` / `#03` usually orders the package.' in release_skill
    assert 'recording diaries, interviews, cast/staff talks' in release_skill
    assert 'finish with a tool result' in release_skill
    assert 'Bangumi `sort` is the default target number' in bangumi_skill
    assert 'local filenames use `01-13` and the selected subject' in bangumi_skill
    assert 'validate exact-path movie rules with `subject_id` plus `media_kind: "movie"`' in bangumi_skill
    assert 'one clean direct title search or one `find_bangumi_targets_for_local_file` call' in bangumi_skill
    assert 'Match the actual local title, including season/subtitle qualifiers' in bangumi_skill
    assert 'Episode count alone is not identity evidence.' in bangumi_skill
    assert 'Keep one subject within the episode rows it exposes.' in bangumi_skill
    assert 'The Python verifier checks mechanical legality and coverage' in bangumi_skill
    assert 'The recipe verifier is a strict mechanical gate, not a semantic title matcher.' in recipe_skill
    assert 'Inspect the visible `source_path` values from chosen group detail pages and infer local groups' in recipe_skill
    assert 'representative `source_path` lookup' in recipe_skill
    assert '`filename_regex` is a real regular expression with `{ep}` as the episode placeholder.' in recipe_docs
    assert 'If the visible files look like `01` to `13`, a single TV episode rule is usually the compact main mapping.' in release_skill
    assert 'local files may restart at `01` while Bangumi `sort` continues' in release_skill
    assert 'represent changing CRC/hash/checksum strings, per-file IDs' in release_skill
    assert 'changing technical suffixes such as `FLAC` versus `FLACx2`' in release_skill
    assert 'would duplicate a numbered SP/OVA/Movie target' in release_skill
    assert 'check numbered `SP01`-style files against Bangumi special episodes for the same subject' in release_skill
    assert 'Numbered `SP01` / `SP02` / `S00E01` files are candidate special entries.' in bangumi_skill
    assert 'A related special/OVA subject is not enough by itself' in bangumi_skill
    assert 'Do not mark a numbered SP sequence supplemental only because the main-season lookup did not include SP rows' in bangumi_skill
    assert 'do not postpone first validation to prove the entire SP graph is exhausted' in bangumi_skill
    assert 'Main-season lookup results that omit SP rows are not evidence' in recipe_skill
    assert 'Main-season representative lookup alone is not enough to conclude that a separate numbered SP sequence has no target.' in release_skill
    assert 'A parent-titled `SPs` folder is also a side-frontier row.' in recipe_skill
    assert 'filenames only say the parent season plus `SP01-SPnn`' in bangumi_skill
    assert 'A folder may be parent-titled and still contain a related short side anime.' in release_skill
    assert 'prefer a related one-episode exact mapped rule when title, runtime, and relation evidence fit' in recipe_skill
    assert 'draft an exact mapped rule before treating it as a duplicate compilation' in bangumi_skill
    assert 'do not assume it is a duplicate compilation only because a split short-episode set exists elsewhere' in release_skill
    assert 'duplicate_target' in bangumi_skill
    assert 'split or variant locators such as `_1`/`_2`' in bangumi_skill
    assert 'cover them as supplemental exact paths' in bangumi_skill
    assert 'long unnumbered standalone title' in bangumi_skill
    assert 'use `find_bangumi_targets_for_local_file` with that exact `source_path`' in bangumi_skill
    assert 'Do not use `non_bangumi_or_supplemental` for a numbered `SP01` / `SP02` / `S00E01`-style file' in recipe_docs
    assert 'validate a supplemental draft with a clear reason' in release_skill
    assert 'validation says the candidate target is missing' in release_skill
    assert 'long standalone file has a distinctive title but no episode number' in release_skill
    assert 'If validation flags that exact path with a review warning' in release_skill
    assert 'duplicate target for split or variant local locators' in release_skill
    assert 'validate them as supplemental exact paths' in release_skill
    assert '`IV01`/`IV02`-style interview-video tokens' in release_skill
    assert '`Travel`, `Tour`, `Journey`, `Location`, `Location Hunting`' in release_skill
    assert 'Do not use raw API words like `episode`' in recipe_docs
    assert 'movie-shaped subject can have a `regular` episode row' in recipe_docs
    assert 'If validation returns `accepted: true` and `review_warnings` is empty' in recipe_skill
    assert 'repair mode should stay targeted: read the issue list, modify the affected params/rules' in recipe_skill
    assert 'make it a testable `disposition: "non_bangumi_or_supplemental"` rule for the first validation' in recipe_skill
    assert 'accepted but has `review_warnings`' in recipe_skill
    assert 'find_bangumi_targets_for_local_file` lookup with the exact `source_path` from the warning' in recipe_skill
    assert 'A submit result with `status: "review"` is not final' in recipe_skill
    assert 'rather than restarting broad search, inspecting old artifacts/tests, or writing prose instead of validating.' in recipe_skill
    assert 'For `duplicate_target` caused by local split/variant locators' in recipe_skill
    assert 'validate that patch before considering whole-case `fail_closed`' in recipe_skill
    assert 'multi-file `group_ref`, `source_pattern`, or multi-path exact selector with one fixed `episode_id`, `sort`, or `ep`' in recipe_skill
    assert 'Do not cover a numbered multi-episode mapped sequence by listing many `exact_paths` plus `episode_range`' in recipe_skill
    assert 'cover only split/variant leftovers as supplemental exact paths' in recipe_skill
    assert 'separate movie/OVA/special files need separate exact-path rules with distinct exposed targets' in recipe_skill
    assert 'patch that supplemental rule to include the missing exact path' in recipe_skill
    assert 'Do not change unrelated mapped movie/OVA/special exact-path rules just to satisfy coverage' in recipe_skill
    assert 'validate_organize_recipe_params_patch' in recipe_skill
    assert 'Patch repair shape after a params validation' in recipe_skill
    assert '`validate_organize_recipe_params_patch` can repair only the changed rules' in recipe_docs
    assert 'Use `{ep}`, `{ep:02}`, or `{ep:02d}`, not Python-style `(?P<ep>...)`, for the episode capture.' in recipe_docs
    assert 'Do not cover a numbered multi-episode sequence with many `exact_paths`' in recipe_docs
    assert 'For repeated supplemental groups, prefer `path_glob` plus `filename_regex`' in recipe_docs
    assert 'avoid listing dozens of obvious supplemental extras as `exact_paths`' in recipe_skill
    assert 'For a multi-file sequence rule that uses `{ep}`, do not hard-code the first episode target.' in recipe_docs
    assert 'Legal `target.media_kind` values are `tv`, `movie`, `ova`, `oad`, `sp`, `special`, and `unknown`.' in recipe_docs
    assert 'Raw `web` is source/API vocabulary, not a recipe field.' in bangumi_skill
    assert 'Keep each rule `reason` short' in recipe_docs
    assert template['rules'][0]['target']['episode_type'] == 'regular'
    assert template['rules'][0]['select']['filename_regex'] == 'Episode {ep}.mkv'
    for concrete_title in ['未来福音', 'Future Gospel', 'Kara no Kyoukai']:
        assert concrete_title not in bangumi_skill
        assert concrete_title not in release_skill
        assert concrete_title not in recipe_docs


def test_organize_recipe_skill_helper_checks_shape_and_coverage(tmp_path):
    recipe = {
        'version': 1,
        'summary': 'skill helper sample',
        'rules': [
            {
                'name': 'tv',
                'select': {'path_glob': '**/*.mkv', 'filename_regex': 'ep{ep}.mkv'},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
                'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
                'disposition': 'map_to_bangumi',
            }
        ],
    }
    case_input = {
        'context': {
            'local_files': [
                {'source_path': 'ep1.mkv'},
                {'source_path': 'ep2.mkv'},
            ]
        }
    }
    recipe_path = tmp_path / 'organize_recipe.json'
    case_input_path = tmp_path / 'case_input.json'
    recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
    case_input_path.write_text(json.dumps(case_input), encoding='utf-8')

    completed = subprocess.run(
        [
            'node',
            str(SKILL_ROOT / 'organize-recipe-contract' / 'scripts' / 'check-organize-recipe.mjs'),
            str(recipe_path),
            str(case_input_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload['ok'] is True


def test_organize_recipe_skill_helper_respects_episode_range_split(tmp_path):
    recipe = {
        'version': 1,
        'summary': 'split one numbered run',
        'rules': [
            {
                'name': 'first range',
                'select': {'path_glob': '**/*.mkv', 'filename_regex': r'ep(?P<ep>\d+)\.mkv'},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
                'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
                'disposition': 'map_to_bangumi',
            },
            {
                'name': 'second range',
                'select': {'path_glob': '**/*.mkv', 'filename_regex': r'ep(?P<ep>\d+)\.mkv'},
                'target': {'bangumi_subject_id': 200, 'media_kind': 'tv', 'episode_type': 'regular'},
                'episode': {'capture': 'ep', 'offset': 'EP-2', 'range': '3-4'},
                'disposition': 'map_to_bangumi',
            },
        ],
    }
    case_input = {'context': {'local_files': [{'source_path': f'ep{index}.mkv'} for index in range(1, 5)]}}
    recipe_path = tmp_path / 'split_recipe.json'
    case_input_path = tmp_path / 'case_input.json'
    recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
    case_input_path.write_text(json.dumps(case_input), encoding='utf-8')

    completed = subprocess.run(
        [
            'node',
            str(SKILL_ROOT / 'organize-recipe-contract' / 'scripts' / 'check-organize-recipe.mjs'),
            str(recipe_path),
            str(case_input_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload['ok'] is True


def test_organize_recipe_skill_helper_rejects_multi_exact_batch_without_episode_locator(tmp_path):
    recipe = {
        'version': 1,
        'summary': 'bad exact batch',
        'rules': [
            {
                'name': 'bad exact batch',
                'select': {'exact_paths': ['ep1.mkv', 'ep2.mkv']},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
                'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
                'disposition': 'map_to_bangumi',
            }
        ],
    }
    case_input = {'context': {'local_files': [{'source_path': 'ep1.mkv'}, {'source_path': 'ep2.mkv'}]}}
    recipe_path = tmp_path / 'bad_recipe.json'
    case_input_path = tmp_path / 'case_input.json'
    recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
    case_input_path.write_text(json.dumps(case_input), encoding='utf-8')

    completed = subprocess.run(
        [
            'node',
            str(SKILL_ROOT / 'organize-recipe-contract' / 'scripts' / 'check-organize-recipe.mjs'),
            str(recipe_path),
            str(case_input_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload['ok'] is False
    assert any('multi-file mapped exact_paths need filename_regex with {ep}' in issue for issue in payload['issues'])


def test_organize_recipe_skill_helper_rejects_sequence_with_fixed_first_target(tmp_path):
    recipe = {
        'version': 1,
        'summary': 'bad sequence target',
        'rules': [
            {
                'name': 'bad sequence target',
                'select': {'path_glob': '**/*.mkv', 'filename_regex': 'ep{ep}.mkv'},
                'target': {
                    'bangumi_subject_id': 100,
                    'media_kind': 'tv',
                    'episode_id': 1001,
                    'episode_type': 'regular',
                    'sort': 1,
                    'ep': 1,
                },
                'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
                'disposition': 'map_to_bangumi',
            }
        ],
    }
    case_input = {'context': {'local_files': [{'source_path': 'ep1.mkv'}, {'source_path': 'ep2.mkv'}]}}
    recipe_path = tmp_path / 'bad_sequence_target.json'
    case_input_path = tmp_path / 'case_input.json'
    recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
    case_input_path.write_text(json.dumps(case_input), encoding='utf-8')

    completed = subprocess.run(
        [
            'node',
            str(SKILL_ROOT / 'organize-recipe-contract' / 'scripts' / 'check-organize-recipe.mjs'),
            str(recipe_path),
            str(case_input_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload['ok'] is False
    assert any('sequence rule with {ep} must not hard-code episode_id/sort/ep' in issue for issue in payload['issues'])


def test_organize_recipe_skill_helper_does_not_crash_on_python_named_capture(tmp_path):
    recipe = {
        'version': 1,
        'summary': 'python capture compatibility',
        'rules': [
            {
                'name': 'tv',
                'select': {'path_glob': '**/*.mkv', 'filename_regex': r'ep(?P<ep>\d+)\.mkv'},
                'target': {'bangumi_subject_id': 100, 'media_kind': 'tv', 'episode_type': 'regular'},
                'episode': {'capture': 'ep', 'offset': 'EP', 'range': '1-2'},
                'disposition': 'map_to_bangumi',
            }
        ],
    }
    case_input = {'context': {'local_files': [{'source_path': 'ep1.mkv'}, {'source_path': 'ep2.mkv'}]}}
    recipe_path = tmp_path / 'python_capture_recipe.json'
    case_input_path = tmp_path / 'case_input.json'
    recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
    case_input_path.write_text(json.dumps(case_input), encoding='utf-8')

    completed = subprocess.run(
        [
            'node',
            str(SKILL_ROOT / 'organize-recipe-contract' / 'scripts' / 'check-organize-recipe.mjs'),
            str(recipe_path),
            str(case_input_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload['ok'] is True
