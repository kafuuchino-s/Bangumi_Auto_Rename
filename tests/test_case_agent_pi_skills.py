from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / '.pi' / 'skills'
PROMPT_ROOT = REPO_ROOT / '.pi' / 'prompts'


def test_pi_thin_skills_exist_with_expected_frontmatter():
    for name in ['local-bangumi-organize', 'tmdb-bridge-contract']:
        path = SKILL_ROOT / name / 'SKILL.md'
        assert path.exists()
        text = path.read_text(encoding='utf-8')
        assert f'name: {name}' in text
        assert 'description:' in text
        assert 'disable-model-invocation: true' not in text
    for old_name in ['bangumi-api', 'anime-release-reading', 'organize-recipe-contract']:
        assert not (SKILL_ROOT / old_name / 'SKILL.md').exists()
    recipe_skill = (SKILL_ROOT / 'local-bangumi-organize' / 'SKILL.md').read_text(encoding='utf-8')
    assert 'This skill is the one Local-to-Bangumi entrypoint.' in recipe_skill
    assert 'description: |\n  Use for the full Local-to-Bangumi organize workflow:' in recipe_skill


def test_local_bangumi_prompt_template_is_official_entry_briefing():
    prompt_path = PROMPT_ROOT / 'local-bangumi-map.md'
    assert prompt_path.exists()
    prompt = prompt_path.read_text(encoding='utf-8')

    assert 'description: Complete a Local-to-Bangumi mapping case with atlas-first evidence' in prompt
    assert 'argument-hint: "<case_input_path>"' in prompt
    assert 'Complete the Local-to-Bangumi dry-run for case input `$1`' in prompt
    assert 'Use `/skill:local-bangumi-organize` as the full method.' in prompt
    assert 'Action-first output mode' in prompt
    assert 'Keep each natural-language turn to 1-3 short sentences' in prompt
    assert 'Do not paste a full mapping table, full recipe JSON, full draft, or full verifier issue list in prose.' in prompt
    assert 'Pass `detail:true` only for debugging full repair hints, compiled plans, or compiled recipe details.' in prompt
    assert 'Prefer `group_ref` for ordinary continuous groups' in prompt
    assert 'anchor-atlas-first' in prompt
    assert 'first Bangumi evidence batch one reliable main-title anchor search' in prompt
    assert 'select_bangumi_anchor_subject(anchor_subject_id, reason)' in prompt
    assert 'Do not use the first Bangumi evidence batch for side-title fanout' in prompt
    assert 'Side-title search is fallback only for a named gap or contradiction after the atlas' in prompt
    assert 'foreground parallel `bangumi-atlas-scout` reviews over atlas chunks' not in prompt
    assert 'Do not write visible self-reasoning headings such as "Deciding", "Evaluating", or "Considering"' in prompt
    assert 'Do not write "enough evidence", "figured out", "should save", "ready to validate", or the same idea as prose.' in prompt
    assert 'Tool call arguments count as output too' in prompt
    assert 'Do not paste `get_case_overview`, `list_local_groups`, `get_local_group_detail`, or atlas JSON into notes.' in prompt
    assert 'For complex packages, the first Bangumi move is one reliable main-title search' in prompt
    assert 'A stable target-surface judgment belongs in `upsert_recipe_group_decision_one`, not in assistant prose or a later batch.' in prompt
    assert 'prefer `group_ref + file_numbers/file_number_range/path_contains` before long `exact_paths`' in prompt
    assert 'Decision shape is strict. Write `episode_range` as a string such as `"1-13"`' in prompt
    assert 'One decision row has one target surface.' in prompt
    assert 'Use `subject_id`, not plural fields such as `target_subject_ids`' in prompt
    assert 'target-key feedback, not proof the local videos are the same content' in prompt
    assert 'title, runtime, folder role, or content shape makes the side file incompatible' in prompt
    assert 'leave the main exact rule intact and reopen the atlas row surface for the side group' in prompt
    assert 'validate_recipe_params_draft(validation_snapshot={summary, accepted_scope, open_issues, next_action})' in prompt
    assert 'Transaction notes use strict small envelopes, not arbitrary JSON.' in prompt
    assert 'explicitly call `submit_organize_recipe_params`' in prompt
    assert 'old artifacts or tests' in prompt
    assert 'Do not touch real media files.' in prompt
    for concrete_title in ['sample_0096', 'OVERLORD', 'Ple Ple', 'SP08_2']:
        assert concrete_title not in prompt


def test_pi_subagent_configuration_is_removed():
    assert not (REPO_ROOT / '.pi' / 'settings.json').exists()
    assert not any((REPO_ROOT / '.pi' / 'agents').glob('*.md'))


def test_pi_skills_document_compact_evidence_flow_and_legal_recipe_episode_types():
    skill_dir = SKILL_ROOT / 'local-bangumi-organize'
    bangumi_skill = (skill_dir / 'references' / 'bangumi-evidence.md').read_text(encoding='utf-8')
    release_skill = (skill_dir / 'references' / 'local-release-reading.md').read_text(encoding='utf-8')
    recipe_skill = (skill_dir / 'SKILL.md').read_text(encoding='utf-8')
    recipe_reference = (skill_dir / 'references' / 'recipe-params.md').read_text(encoding='utf-8')
    recipe_docs = recipe_skill + '\n' + recipe_reference
    template = json.loads((skill_dir / 'assets' / 'organize_recipe.template.json').read_text(encoding='utf-8'))

    assert len(recipe_skill.splitlines()) < 140
    assert len(bangumi_skill.splitlines()) < 90
    assert len(release_skill.splitlines()) < 100
    assert 'References' in recipe_skill
    assert 'references/local-release-reading.md' in recipe_skill
    assert 'references/bangumi-evidence.md' in recipe_skill
    assert 'Human Closure Loop' in recipe_skill
    assert 'Action-First Output Mode' in recipe_skill
    assert 'Keep visible prose short: 1-3 sentences is usually enough.' in recipe_skill
    assert 'Do not paste full mapping tables, full recipe JSON, full drafts, or full verifier issue lists as prose.' in recipe_skill
    assert 'Use `detail:false` defaults for normal work; pass `detail:true` only when debugging full `repair_hints`' in recipe_skill
    assert 'Prefer `group_ref` for ordinary continuous groups.' in recipe_skill
    assert 'This output discipline does not reduce the semantic method' in recipe_skill
    assert 'call `select_bangumi_anchor_subject(anchor_subject_id, reason)`' in recipe_skill
    assert 'Make the first Bangumi evidence batch one reliable main-title search, then use `select_bangumi_anchor_subject(anchor_subject_id, reason)`' in recipe_skill
    assert 'Do not start by searching every visible side title' in recipe_skill
    assert 'Do not wait for the whole case, and do not narrate the stable judgment instead of saving it.' in recipe_skill
    assert 'After an atlas, graph pass, or episode batch changes your judgment, save the stable subset immediately.' in recipe_skill
    assert 'Do not emit visible self-reasoning headings such as "Deciding", "Evaluating", or "Considering".' in recipe_skill
    assert 'Do not write "enough evidence", "figured out", "should save", or "ready to validate" as prose; call the matching tool instead.' in recipe_skill
    assert 'Tool arguments count as output too.' in recipe_skill
    assert 'Do not paste `get_case_overview`, `list_local_groups`, `get_local_group_detail`, or atlas JSON into notes' in recipe_skill
    assert 'For complex packages, the first Bangumi move is one reliable main-title search' in recipe_skill
    assert 'For numbered one-file or subcluster exceptions, prefer `group_ref + file_numbers/file_number_range/path_contains` before long `exact_paths`.' in recipe_skill
    assert 'Frontier Review Lane' not in recipe_skill
    assert 'bangumi-frontier-scout' not in recipe_skill
    assert 'bangumi-atlas-scout' not in recipe_skill
    assert 'Subagents are review notes, not the organizing workflow.' not in recipe_skill
    assert 'foreground parallel `bangumi-atlas-scout` reviews over atlas chunks' not in recipe_skill
    assert 'the parent Pi may naturally ask for foreground parallel `bangumi-frontier-scout` reviews' not in recipe_skill
    assert 'The runner may publish a foreground parallel `bangumi-frontier-scout` checkpoint' not in recipe_skill
    assert 'When scout reports appear, read them like a second human reviewer' not in recipe_skill
    assert 'The scout is advisory/read-only' not in recipe_skill
    assert 'do not ask it to call Python custom tools, validate, submit, patch, or write recipe params' not in recipe_skill
    assert 'the parent Pi translates useful findings into normal actions' not in recipe_skill
    assert 'optional audit helpers for fresh-context/file-read review' not in recipe_skill
    assert 'evidence changes judgment, judgment becomes a saved row' in recipe_skill
    assert 'upsert_recipe_group_decision_one' in recipe_skill
    assert 'validate_recipe_params_draft(validation_snapshot={summary, accepted_scope, open_issues, next_action})' in recipe_skill
    assert 'Transaction notes use strict small envelopes, not arbitrary JSON.' in recipe_docs
    assert '`patch_delta` has `summary`, `changed_rules`, `evidence_refs`, `reason`' in recipe_skill
    assert '`submit_snapshot` has `summary`, `accepted_rule_count`, `review_notes`' in recipe_skill
    assert 'Verifier Delta' in recipe_skill
    assert 'Short duration, an `SPs` folder, or a parent TV subject with no SP rows is local shape evidence, not supplemental proof.' in recipe_skill
    assert 'Treat duplicate feedback as a surface mismatch.' in recipe_skill
    assert 'It is target-key feedback, not proof that two local videos are the same content.' in recipe_skill
    assert 'title, runtime, folder role, or content shape does not fit that main target' in recipe_skill
    assert 'If duplicate feedback pairs a short side-folder file with a long main movie/episode target' in recipe_skill
    assert 'treat the duration/content-shape mismatch as evidence against reusing that target surface' in recipe_skill
    assert 'A saved row is a target-surface claim, not just a local-group claim.' in recipe_skill
    assert 'save only the stable subcluster with `file_numbers`, `file_number_range`, `path_contains`, exclusions, or `exact_paths` when numbering/path filters are not safe' in recipe_skill
    assert 'A movie pair usually needs two exact movie rows unless one Bangumi subject exposes two legal movie rows.' in recipe_skill
    assert 'Read `references/recipe-params.md` only when canonical params fields, selector syntax, or exact patch shape remains unclear' in recipe_skill

    assert 'This reference is about evidence, not recipe orchestration.' in bangumi_skill
    assert 'Atlas-First Evidence' in bangumi_skill
    assert 'Call `select_bangumi_anchor_subject(anchor_subject_id, reason)` for that anchor' in bangumi_skill
    assert 'the first evidence batch should not be side-title fanout' in bangumi_skill
    assert 'prepare_bangumi_relation_atlas_scout_packets' not in bangumi_skill
    assert 'A stable judgment is durable only after it becomes a saved decision or draft row.' in bangumi_skill
    assert 'Do not treat each local group as an independent search problem.' in bangumi_skill
    assert 'A Bangumi subject is not one flat target.' in bangumi_skill
    assert 'Do not make a parent TV subject carry every side-shaped local group.' in bangumi_skill
    assert 'If Bangumi represents each feature as a separate movie subject, save exact movie decisions per file' in bangumi_skill
    assert 'Use more Bangumi calls only for a named missing target surface or contradiction.' in bangumi_skill
    assert 'Read `references/python-custom-tools.md` only when tool arguments, traversal status, or raw result fields are unclear.' in bangumi_skill

    assert 'This reference reads the local release. It does not choose Bangumi targets' in release_skill
    assert 'Package Shape' in release_skill
    assert 'Short runtime, an `SPs` folder, or bonus location describes local shape. It is not proof that a group is supplemental.' in release_skill
    assert 'A local group is a reading unit, not proof that every file has the same target or disposition.' in release_skill
    assert 'Duplicate readings need compatible content shape, not just a shared parent folder.' in release_skill
    assert 'When local shape plus Bangumi atlas/episode evidence supports a testable group or subcluster, save that compact judgment with `upsert_recipe_group_decision_one`' in release_skill
    assert 'The whole package does not need to be solved before the first stable row is saved.' in release_skill

    assert 'early_bangumi_evidence_bundle' not in recipe_skill
    assert 'case_quick_start' not in recipe_skill
    assert 'recommended_recipe' not in bangumi_skill
    assert 'recommended_recipe' not in recipe_docs
    assert 'draft_recipe' not in bangumi_skill
    assert 'draft_recipe' not in recipe_docs
    assert 'Use only canonical params field names.' in recipe_docs
    assert '`group_ref`, `file_numbers`, `file_number_range`' in recipe_docs
    assert 'Tool call arguments are part of model output.' in recipe_docs
    assert 'For numbered one-file movies, OVA/OAD/SP files, or mixed-folder subclusters, prefer `group_ref` plus `file_numbers`' in recipe_docs
    assert 'validate_organize_recipe_params_patch' in recipe_docs
    assert 'submit_organize_recipe_params_patch' in recipe_docs
    assert 'A parent TV subject that lacks SP rows is not enough to make a named side-content group supplemental.' in recipe_reference
    assert "The parent TV subject's regular rows are not side-folder target evidence." in recipe_reference
    assert 'A related subject is not used up after one rule.' in recipe_reference
    assert 'Use a group decision only when the selected local files share the same Bangumi target surface.' in recipe_reference
    assert 'A movie or recap pair is not automatically one sequence.' in recipe_reference
    bangumi_tools_reference = (skill_dir / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'select_bangumi_anchor_subject({ "anchor_subject_id": 12345' in bangumi_tools_reference
    assert 'Use atlas closure for side frontiers.' in bangumi_tools_reference
    assert 'prepare_bangumi_relation_atlas_scout_packets' not in bangumi_tools_reference
    assert 'record_bangumi_relation_atlas_scout_reports' not in bangumi_tools_reference
    assert 'Read regular, special, OVA/OAD, and movie-like rows as separate target surfaces.' in bangumi_tools_reference
    assert 'Keep the tool arguments compact and schema-correct.' in bangumi_tools_reference
    assert 'prefer `group_ref` plus `file_numbers`, `file_number_range`, `path_contains`, or `exclude_path_contains`' in bangumi_tools_reference
    assert 'sample_0096' not in recipe_docs
    assert 'OVERLORD' not in recipe_docs
    assert 'Ple Ple' not in recipe_docs
    assert 'SP08_2' not in recipe_docs
    assert template['rules'][0]['target']['episode_type'] == 'regular'
    assert template['rules'][0]['select']['filename_regex'] == 'Episode {ep}.mkv'

    return

    assert 'Evidence Method Comes First' in recipe_skill
    assert 'Do not let board/draft/validate mechanics replace the human evidence method.' in recipe_skill
    assert 'single standalone title: direct search plus episode rows may be enough' in recipe_skill
    assert 'search one reliable anchor, then use `expand_related_graph(subject_types: ["anime"])` as the normal way to close the remaining frontier before broad side-title searches' in recipe_skill
    assert 'The board and draft are workpaper tools for preserving decisions.' in recipe_skill
    assert 'They are not a reason to inspect every local group through separate direct searches.' in recipe_skill
    assert 'Board-First Human Closure Loop' in recipe_skill
    assert 'decide the package shape: standalone, contiguous seasons, franchise pack, movie box, side-content pack, or mixed release' in recipe_skill
    assert "For multi-series or side-content packages, use that primary anchor's related graph to answer the side frontier before side-title searches." in recipe_skill
    assert 'local group | local shape | target evidence | recipe rule | open issue' in recipe_skill
    for status in ['unknown', 'anchored', 'draftable', 'side_frontier', 'supplemental_candidate', 'repairing', 'accepted']:
        assert status in recipe_skill
    assert 'They are thinking labels, not fixed-layer commands.' in recipe_skill
    assert 'Append-Only Case Board' in recipe_skill
    assert 'Incremental Decisions And Draft' in recipe_skill
    assert 'The board records reasoning. `recipe_group_decisions` records compact group/subcluster judgments.' in recipe_skill
    assert 'Treat `upsert_recipe_group_decision_one` like saving a row in a working spreadsheet.' in recipe_skill
    assert 'Partial decisions are normal progress, but they are not a quota.' in recipe_skill
    assert 'You do not need to assemble a consistent all-group batch before saving the first stable row' in recipe_skill
    assert 'you also do not need to invent a row for every group before the side frontier is closed' in recipe_skill
    assert 'A group decision is not a local-only coverage note' in recipe_skill
    assert 'Do not leave skeletal rows in the workpaper.' in recipe_skill
    assert 'it is an unfinished spreadsheet line' in recipe_skill
    assert 'more broad evidence does not make an incomplete saved row safer' in recipe_skill
    assert 'Decide before polishing mechanical words.' in recipe_skill
    assert 'With an exact exposed `episode_id`, Python can often canonicalize the row type' in recipe_skill
    assert 'The save moment is a human moment, not a quota.' in recipe_skill
    assert 'put that stable sentence into `upsert_recipe_group_decision_one`' in recipe_skill
    assert 'A remaining unsettled side group can stay missing from the draft' in recipe_skill
    assert 'For big packages, save from the easy center outward.' in recipe_skill
    assert 'Saving those rows first is not a fixed-layer choice' in recipe_skill
    assert '`get_recipe_group_decisions(detail=false)`' in recipe_skill
    assert '`clear_recipe_group_decisions(reason=...)`' in recipe_skill
    assert 'An ambiguous side group does not block saving unrelated stable groups.' in recipe_skill
    assert 'If you catch yourself thinking "LG1 maps to subject X"' in recipe_skill
    assert '`get_recipe_params_draft(detail=false)`' in recipe_skill
    assert '`validate_recipe_params_draft(validation_snapshot={summary, accepted_scope, open_issues, next_action})`' in recipe_skill
    assert 'There is no partial verifier.' in recipe_skill
    assert 'The `Initial Board` is local-only' in recipe_skill
    assert 'It is a local memory aid, not a Bangumi decision.' in recipe_skill
    assert 'Open issues on side-frontier board rows are not direct-search tasks.' in recipe_skill
    assert 'append_case_board_note' in recipe_skill
    assert 'get_case_board_notes(mode="tail")' in recipe_skill
    assert 'rather than native edit/write' in recipe_skill
    for section in ['Initial Board', 'Board Delta', 'Validation Snapshot', 'Verifier Delta', 'Patch Delta', 'Submit Snapshot']:
        assert section in recipe_skill
    assert 'The latest `Validation Snapshot` or `Submit Snapshot` is the current board state.' in recipe_skill
    assert '`Validation Snapshot` is a final preflight, not a planning section.' in recipe_skill
    assert 'It belongs when the `recipe_params` object is already ready to send.' in recipe_skill
    assert 'validation_snapshot' in recipe_skill
    assert 'patch_delta' in recipe_skill
    assert 'submit_snapshot' in recipe_skill
    assert 'A board update exists only after a custom tool returns `ok: true`.' in recipe_skill
    assert 'Prose like "I will append", "I should snapshot", or "I have enough to validate" is only a thought' in recipe_skill
    assert 'save it through the board/draft/transaction tool' in recipe_skill
    assert 'Full draft validation becomes useful when saved group decisions have compiled into a `recipe_params_draft` where every visible local path has either' in recipe_skill
    assert 'a testable mapped rule' in recipe_skill
    assert 'a testable `disposition: "non_bangumi_or_supplemental"` rule' in recipe_skill
    assert "save that group's judgment with `upsert_recipe_group_decision_one`" in recipe_skill
    assert 'a `Board Delta` naming the group and the one missing Bangumi fact keeps the next evidence lookup focused' in recipe_skill
    assert 'If your own reasoning says "I can use exact_paths and episode_id"' in recipe_skill
    assert 'validate_recipe_params_draft(validation_snapshot={summary, accepted_scope, open_issues, next_action})' in recipe_skill
    assert 'Side Frontier Closure' in recipe_skill
    assert 'Put these local shapes into the side frontier' in recipe_skill
    assert 'add that subject as a new anchor' in recipe_skill
    assert 'The closure stops only when the graph plus targeted title/episode evidence adds no supportable anime/video target' in recipe_skill
    assert 'Close evidence at the Bangumi row-surface level, not only at the subject level.' in recipe_skill
    assert 'A related subject may expose regular rows, special rows, OVA/OAD rows, movie-like rows, and one-off exact rows.' in recipe_skill
    assert 'One related subject can support multiple recipe surfaces.' in recipe_skill
    assert 'Do not consider the subject "used up" after drafting its regular sequence.' in recipe_skill
    assert "The concrete habit is: when a side subject already supports one regular side sequence" in recipe_skill
    assert "A previous regular-only episode list, small episode-card limit, or narrow target window does not prove the subject has no special rows." in recipe_skill
    assert 'Use `get_episode_list(episode_scope="all")` with enough cards' in recipe_skill
    assert 'One local group can also split into multiple recipe rules when its files have different local shapes.' in recipe_skill
    assert 'A group-level supplemental rule is appropriate only when every selected file in that rule lacks supportable target rows.' in recipe_skill
    assert 'Feature/movie pairs and recap pairs are usually exact-row work, not automatic sequences.' in recipe_skill
    assert 'split the group into exact `exact_paths` rules with distinct exposed targets' in recipe_skill
    assert 'Use a group-level sequence only when Bangumi exposes one subject with multiple rows that match the local files by title/order/duration.' in recipe_skill
    assert 'map the matched `SP` files to the special rows and cover only the companion extras as supplemental' in recipe_skill
    assert 'Treat "duplicate" as a same-surface claim.' in recipe_skill
    assert 'title, duration, and content shape are compatible with that same target row' in recipe_skill
    assert 'The reason must apply to the exact files covered by that supplemental rule.' in recipe_skill
    assert 'Do not treat short duration, an `SPs` folder, or a parent TV subject with no SP rows as enough evidence for supplemental.' in recipe_skill
    assert "matching only the parent TV's regular episode numbers is not enough target evidence" in recipe_skill
    assert 'If duplicate target feedback pairs a side-folder `SP`/bonus file with a parent TV regular episode' in recipe_skill
    assert 'Also do not immediately turn the side-folder file supplemental just because it duplicated the main row.' in recipe_skill
    assert 'For a movie-bundle `SPs` folder, a numbered `SP01`/`SP02` file that is much shorter than the feature movie' in recipe_skill
    assert 'Treat a duplicate with the main feature row as a wrong-surface symptom' in recipe_skill
    assert 'When a duplicate repair touches only a mixed side-folder subset, keep the repair exact.' in recipe_skill
    assert 'Do not use a vague reason such as "no supportable target chosen" for a numbered side group' in recipe_skill
    assert 'do not stretch it into `source_unit: "single_file_multi_episode"` merely because its title resembles a short-series subject' in recipe_skill
    assert 'Do not change a plausible mapped OVA/OAD/SP/movie/side-story group to `non_bangumi_or_supplemental` merely to make the verifier pass.' in recipe_skill
    assert 'the tool appends a `Verifier Delta` automatically' in recipe_skill
    assert 'Validation and submit tools may also return `case_board_next_action`.' in recipe_skill
    assert 'Treat it as non-semantic routing' in recipe_skill
    assert 'If your own reasoning can already say the repair in concrete terms' in recipe_skill
    assert 'that thought belongs in `validate_organize_recipe_params_patch(..., patch_delta=...)`' in recipe_skill
    assert 'Repair lock means:' in recipe_skill
    assert 'coverage issues repair selectors or add the missing visible paths to the intended existing rule' in recipe_skill
    assert 'duplicate target issues repair sequence shape' in recipe_skill
    assert 'exact_paths` must be complete visible `source_path` strings' in recipe_skill
    assert 'multi `exact_paths` plus `episode_range` maps paths in listed order when the counts match' in recipe_skill
    assert 'Exact supplemental rules can carve split/variant leftovers out of broader mapped sequence rules' in recipe_skill
    assert 'Exact mapped rules can also carve supported special/movie-like files out of a mixed side group' in recipe_skill
    assert 'multi `exact_paths` plus `episode_range` maps paths in listed order' in recipe_reference
    assert 'Prefer `source_pattern` or `group_ref` for ordinary large numbered batches.' in recipe_reference
    assert 'Incremental draft rows are real compact params rows, not placeholders.' in recipe_reference
    assert 'Do not delay an exact mapped draft just because `media_kind` or `episode_type` feels imperfect.' in recipe_reference
    assert '`group_ref` is a local selector shorthand' in recipe_skill
    assert 'Gold shapes for `upsert_recipe_group_decision_one`' in recipe_skill
    assert '"Side mini sequence"' in recipe_skill
    assert '"episode_range":"1-13"' in recipe_skill
    assert '"Duplicate side variant"' in recipe_skill
    assert '"Movie one"' in recipe_skill
    assert '"Movie two"' in recipe_skill
    assert 'Do not write plural target fields such as `target_subject_ids`, `subject_ids`, or `bangumi_subject_ids`.' in recipe_skill
    assert 'Use `group_ref + source_pattern` only when you need an explicit side-folder/subcluster template' in recipe_skill
    assert 'Tool shape is strict. Write `episode_range` as a string such as `"1-13"`' in recipe_skill
    assert 'it never chooses `subject_id`, `episode_id`, `media_kind`, `episode_type`, disposition, or supplemental status' in recipe_skill
    assert '`group_ref` does not require one output rule per local group.' in recipe_skill
    assert 'Use `exact_paths` for supported subclusters or exceptions inside a mixed group' in recipe_skill
    assert 'Do not use `SP` as `episode_offset`' in recipe_skill
    assert 'SP filenames and `media_kind: "sp"` do not imply `episode_type: "special"`.' in recipe_skill
    assert 'Supplemental rules do not need `episode_range`, `episode_offset`, `episode_type`, `subject_id`, or `episode_id`.' in recipe_skill
    assert '`patch_delta` has `summary`, `changed_rules`, `evidence_refs`, `reason`' in recipe_skill
    assert 'explicitly call `submit_organize_recipe_params` with `submit_snapshot={summary, accepted_rule_count, review_notes}`' in recipe_skill
    assert 'budget_exhausted` is a runner outcome' in recipe_skill
    assert 'old `final_result.json` files, repository tests, and templates are not evidence' in recipe_skill

    assert 'This skill is about evidence, not recipe orchestration.' in bangumi_skill
    assert 'a small `Board Delta` preserves the reason' in bangumi_skill
    assert 'saving the judgment with `upsert_recipe_group_decision_one` is useful even while other groups remain unsettled' in bangumi_skill
    assert 'Graph-First Evidence Ladder' in bangumi_skill
    assert 'Target Surface Closure' in bangumi_skill
    assert 'do not treat each local group as an independent search problem' in bangumi_skill
    assert 'use one reliable direct search to find an anchor, then use the relation graph as the default evidence surface' in bangumi_skill
    assert 'Human evidence ladder for franchise or side-content packages:' in bangumi_skill
    assert 'Episode count alone is not identity evidence.' in bangumi_skill
    assert 'Search one reliable primary anchor: the main TV/movie title that is most stable and most likely to open the franchise graph.' in bangumi_skill
    assert 'Do not search side-frontier titles in the same first evidence move.' in bangumi_skill
    assert 'Call `expand_related_graph` from that anchor with `subject_types: ["anime"]` before more broad side-title searches.' in bangumi_skill
    assert 'Avoid the parallel search trap' in bangumi_skill
    assert 'The side-frontier board rows are not a search queue' in bangumi_skill
    assert 'Direct search is the anchor/fallback tool.' in bangumi_skill
    assert 'If you have already searched a main anchor and have not tried `expand_related_graph`, another broad search for a side group is usually premature.' in bangumi_skill
    assert 'treat the subject as a new anchor' in bangumi_skill
    assert 'use direct side-title search for graph misses, conflicts, or a named group still unresolved after the graph pass' in bangumi_skill
    assert 'A parent TV subject that lacks SP rows is not negative evidence' in bangumi_skill
    assert 'A verifier issue on a mapped rule is mechanical feedback, not proof that the target is wrong.' in bangumi_skill
    assert 'Evidence enough means enough to save a testable group/subcluster decision, not enough to prove the entire package.' in bangumi_skill
    assert 'A stable decision can go into `recipe_group_decisions` while the remaining frontier stays open.' in bangumi_skill
    assert 'Do not hold a known group in prose while trying to make every other group consistent.' in bangumi_skill
    assert 'If the uncertainty left in your head is mostly recipe wording' in bangumi_skill
    assert 'Use more Bangumi calls only for a named missing target surface or contradiction.' in bangumi_skill
    assert 'A Bangumi subject is not a single mapping surface.' in bangumi_skill
    assert 'One subject can legitimately serve more than one local group.' in bangumi_skill
    assert 'Do not stop at "this subject is already used" when another local side group matches a different row surface.' in bangumi_skill
    assert "The practical check is concrete: if you used a related subject for a regular side sequence" in bangumi_skill
    assert 'Prefer `get_episode_list(episode_scope="all", max_episode_cards=...)` with enough cards to expose all row types' in bangumi_skill
    assert 'use `episode_scope: "all"` and enough `max_episode_cards` to see non-regular rows' in bangumi_skill
    assert 'When a local side group is mixed, compare each local shape cluster to the exposed row surfaces.' in bangumi_skill
    assert 'When a local group contains multiple long movie-like or recap-like files' in bangumi_skill
    assert 'Two long files often mean two exact targets, not one subject-level sequence.' in bangumi_skill
    assert 'Do not mark a whole side group supplemental merely because part of it is theater-manner clips' in bangumi_skill
    assert 'If a related side subject exposes special rows whose titles, dates, order, or durations match a subset of files in a mixed `SPs` folder' in bangumi_skill
    assert 'Numbered `SP01`, `SP02`, `Special 1`, or `S00E01` files are candidate special locators.' in bangumi_skill
    assert 'Short duration is side-content shape evidence, not supplemental evidence.' in bangumi_skill
    assert 'Long standalone OVA/OAD/SP/movie-like files can be one-episode Bangumi subjects' in bangumi_skill
    assert 'parent regular episode rows are weak evidence even when counts line up' in bangumi_skill
    assert 'If a side-folder exact file duplicates a main movie or episode target during validation' in bangumi_skill
    assert 'Before accepting a duplicate explanation, compare the local file against the target surface it would duplicate.' in bangumi_skill
    assert 'matches an exposed special/OVA/movie-like row instead' in bangumi_skill
    assert 'For movie-bundle `SPs` folders, short or medium numbered `SP01`/`SP02` files are not automatically duplicate copies of feature-length recap movies.' in bangumi_skill
    assert 'inspect already-exposed side subjects and their special rows before deciding those numbered files are supplemental' in bangumi_skill
    assert 'One unnumbered local file is not automatically a multi-episode span.' in bangumi_skill
    assert 'SP file naming is not the same as Bangumi row type' in bangumi_skill
    assert 'not a chosen target' in bangumi_skill

    assert 'This skill is for reading the local release. It does not choose Bangumi targets' in release_skill
    assert 'Package Shape First' in release_skill
    assert 'Before choosing Bangumi evidence tools, read what kind of package this is.' in release_skill
    assert 'single standalone title' in release_skill
    assert 'franchise pack' in release_skill
    assert 'side-content pack' in release_skill
    assert 'For a single standalone title, direct title search can be enough.' in release_skill
    assert 'For contiguous season, franchise, movie-box, side-content, or mixed packages, the local reading should prepare a side frontier' in release_skill
    assert 'the append-only Case Board is a good place to preserve local-shape findings' in release_skill
    assert 'save the compact judgment with `upsert_recipe_group_decision_one` while other groups remain under investigation' in release_skill
    assert 'This is a one-row habit, not an all-groups batch.' in release_skill
    assert 'Read local structure through navigation first:' in release_skill
    assert 'Treat group cards and detail pages as factual grouping evidence only.' in release_skill
    assert 'Notice duplicate locator variants inside one group' in release_skill
    assert 'A local group is a reading unit, not proof that every file in it has the same disposition.' in release_skill
    assert 'A mixed `SPs` group can become several recipe rules later' in release_skill
    assert 'movie or recap packages with an `SPs` subfolder' in release_skill
    assert 'Do not let the parent folder title alone make those `SP` files inherit the main movie target.' in release_skill
    assert 'A duplicate reading needs compatible content shape, not just a shared parent folder.' in release_skill
    assert 'A medium `SP01` file and a feature-length recap movie are different local surfaces' in release_skill
    assert 'When a folder mixes anime-shaped specials with obvious extras, do not let the obvious extras decide the whole folder.' in release_skill
    assert 'For a mixed `SPs` folder, preserve the subclusters in the board' in release_skill
    assert 'Duration clusters are especially useful for mixed local groups.' in release_skill
    assert 'Numbered special tokens such as `SP01`, `SP02`, `Special 1`, or `S00E01` are candidate special locators.' in release_skill
    assert 'They are not automatic extras.' in release_skill
    assert 'Short duration is not supplemental evidence by itself.' in release_skill
    assert "the local numbers often reuse the parent season's numbering style" in release_skill
    assert 'A one-file local shape should be checked against one-episode OVA/OAD/special/movie targets' in release_skill
    assert 'local reading alone cannot prove it covers the whole row sequence' in release_skill
    assert 'Missing local duration is not negative evidence.' in release_skill
    assert 'File-name tokens are investigation hints only.' in release_skill
    assert 'a testable supplemental group decision makes that judgment explicit instead of leaving the group implicit' in release_skill

    assert 'early_bangumi_evidence_bundle' not in recipe_skill
    assert 'case_quick_start' not in recipe_skill
    assert 'recommended_recipe' not in bangumi_skill
    assert 'recommended_recipe' not in recipe_docs
    assert 'draft_recipe' not in bangumi_skill
    assert 'draft_recipe' not in recipe_docs
    assert 'Do not use aliases or raw nested shapes' in recipe_docs
    assert 'validate_organize_recipe_params_patch' in recipe_skill
    assert 'submit_organize_recipe_params_patch' in recipe_skill
    assert 'Side Frontier Closure' in recipe_skill
    assert 'A parent TV subject that lacks SP rows is not enough to make a named side-content group supplemental.' in recipe_reference
    assert "The parent TV subject's regular rows are not side-folder target evidence." in recipe_reference
    assert 'A related subject is not used up after one rule.' in recipe_reference
    assert 'A local group can split into multiple params rules.' in recipe_reference
    assert 'A mixed `SPs` folder is not fully supplemental just because some files are theater-manner clips' in recipe_reference
    assert 'do not make numbered SP files inherit the main movie target by folder title alone' in recipe_reference
    assert 'Do not call a side-folder SP file a duplicate of a main movie/episode unless its title, duration, and content shape fit that same target surface.' in recipe_reference
    assert 'A regular-only episode view is not enough evidence to close that subject for numbered SP files.' in recipe_reference
    assert 'Do not use `source_unit: "single_file_multi_episode"` for a single unnumbered file only because its title resembles a short-series subject.' in recipe_reference
    assert 'Do not change it to `non_bangumi_or_supplemental` merely to make the verifier pass.' in recipe_reference
    bangumi_tools_reference = (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'Use graph closure for side frontiers.' in bangumi_tools_reference
    assert 'that subject becomes a new anchor' in bangumi_tools_reference
    assert 'inspect its target surfaces instead of treating the subject as one flat target' in bangumi_tools_reference
    assert 'Read regular, special, OVA/OAD, and movie-like rows as separate target surfaces.' in bangumi_tools_reference
    assert 'A regular-only row view means only the regular surface was checked; it does not rule out special rows.' in bangumi_tools_reference
    assert 'Duplicate-target feedback between a main movie file and a side-folder SP file should trigger this target-surface check' in bangumi_tools_reference
    assert 'Duplicate-target repair should compare title and duration against the target surface being duplicated.' in bangumi_tools_reference
    assert 'sample_0096' not in recipe_docs
    assert 'OVERLORD' not in recipe_docs
    assert 'Ple Ple' not in recipe_docs
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
    assert '`source_path`, `path`, `paths`, `source_paths`' in recipe_docs
    assert 'are rejected' in recipe_docs
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
    assert 'Before finalizing a numbered side/SP/OVA/movie-like file as supplemental' in bangumi_skill
    assert 'When validation or submit returns `issue_repair_contexts`' in bangumi_skill
    assert 'Invalid duplicate-target feedback may include `issue_repair_contexts`' in bangumi_tools_reference
    assert 'quick check for uncertain supplemental decisions' in bangumi_tools_reference
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
    assert 'that field filters relation labels, not subject type' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert '`subject_types: ["anime"]` to keep only anime/video subjects' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert '`expand_related_graph({ "subject_ids": [12345]' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'Read `relation_subjects` first.' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'not a proof that the series graph is complete' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert '`frontier_exhausted`: true only when this bounded traversal has no more seen anime/video subjects' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert '`next_subject_ids_to_expand`: subject IDs to use as the next `subject_ids` seed' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'this tool is already scoped to Bangumi' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
    assert 'Filter returned subjects to anime/video-shaped entries' in (SKILL_ROOT / 'local-bangumi-organize' / 'references' / 'python-custom-tools.md').read_text(encoding='utf-8')
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
    assert 'If you want start/end fields, write `episode_range_start` and `episode_range_end`.' in recipe_docs
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
    assert 'Before keeping a numbered SP/OVA/OAD/movie-like visible file supplemental' in recipe_docs
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
            str(SKILL_ROOT / 'local-bangumi-organize' / 'scripts' / 'check-organize-recipe.mjs'),
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
            str(SKILL_ROOT / 'local-bangumi-organize' / 'scripts' / 'check-organize-recipe.mjs'),
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
            str(SKILL_ROOT / 'local-bangumi-organize' / 'scripts' / 'check-organize-recipe.mjs'),
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
            str(SKILL_ROOT / 'local-bangumi-organize' / 'scripts' / 'check-organize-recipe.mjs'),
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
            str(SKILL_ROOT / 'local-bangumi-organize' / 'scripts' / 'check-organize-recipe.mjs'),
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
