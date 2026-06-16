---
description: Complete a Local-to-Bangumi mapping case with atlas-first evidence
argument-hint: "<case_input_path>"
---

Complete the Local-to-Bangumi dry-run for case input `$1`. Produce accepted compact recipe params through the Python verifier, or a concrete `fail_closed` when strict evidence cannot support a mapping.

Use `/skill:local-bangumi-organize` as the full method. The runner should have loaded it before this task; this template is the task briefing, not a replacement for the skill.

Work from the case tools, not old artifacts or tests. Read local structure with `get_case_overview`, `list_local_groups`, and focused group/file detail. Do not touch real media files.

Action-first output mode:

- Keep each natural-language turn to 1-3 short sentences unless you are issuing `fail_closed`.
- Do not paste a full mapping table, full recipe JSON, full draft, or full verifier issue list in prose.
- Do not write visible self-reasoning headings such as "Deciding", "Evaluating", or "Considering"; keep that reasoning internal.
- Do not write "enough evidence", "figured out", "should save", "ready to validate", or the same idea as prose. When that condition is true, call the matching tool instead.
- Tool call arguments count as output too: keep `board_delta`, `validation_snapshot`, `patch_delta`, `submit_snapshot`, `reason`, and `summary` short. Transaction notes use strict small envelopes, not arbitrary JSON.
- Do not paste `get_case_overview`, `list_local_groups`, `get_local_group_detail`, or atlas JSON into notes. Cite `LG*`, subject IDs, episode IDs, and one-line blockers.
- For complex packages, the first Bangumi move is one reliable main-title search, then `select_bangumi_anchor_subject(anchor_subject_id, reason)`; do not fail_closed from an empty draft before that anchor exists unless the case input is malformed.
- Use `upsert_recipe_group_decision_one` for each stable row, draft tools for unusual bulk edits, `validate_recipe_params_draft` for complete draft checks, patch tools for named verifier issues, and submit tools for accepted params.
- Prefer `group_ref` for ordinary continuous groups. Do not copy a full release filename into `source_pattern` for a complete group; codec/audio/hash tokens vary and can leave files uncovered. For numbered one-file/subcluster exceptions, prefer `group_ref + file_numbers/file_number_range/path_contains` before long `exact_paths`.
- Decision shape is strict. Write `episode_range` as a string such as `"1-13"` or use `episode_range_start/end`; do not pass `[1,13]`. Use legal `media_kind` values only: `tv`, `movie`, `ova`, `oad`, `sp`, `special`, or `unknown`.
- One decision row has one target surface. Use `subject_id`, not plural fields such as `target_subject_ids`; split two movies, two specials, or mixed side-folder subclusters into separate `upsert_recipe_group_decision_one` calls.
- If you combine `group_ref` with `source_pattern`, the pattern must be a literal file template that matches the selected group and contains `{ep}` for sequence mapping. Without `file_numbers`, `file_number_range`, `path_contains`, `exclude_path_contains`, or `exact_paths`, it must match the whole group. A mismatched or partial pattern is a tool-shape error to repair, not something Python will guess around.
- Use full `exact_paths` only for unnumbered, path-ambiguous, or truly mixed exceptions.
- Tool defaults are compact. Pass `detail:true` only for debugging full repair hints, compiled plans, or compiled recipe details.

For complex, franchise, multi-season, movie-box, OVA/OAD/SP, mini-anime, recap, or mixed packages, use anchor-atlas-first:

1. Make the first Bangumi evidence batch one reliable main-title anchor search.
2. Then call `select_bangumi_anchor_subject(anchor_subject_id, reason)` for the reliable main anime/video anchor. This atomically records Pi's anchor choice and builds the full reachable Bangumi anime/video relation atlas.
3. Synthesize the atlas row surfaces before side-title search: a related subject is not used up after its regular sequence; check its special, OVA/OAD, movie-like, and exact rows for side folders.
4. Do not use the first Bangumi evidence batch for side-title fanout. Side-title search is fallback only for a named gap or contradiction after the atlas row surfaces.

Save judgments as soon as they become stable. A stable target-surface judgment belongs in `upsert_recipe_group_decision_one`, not in assistant prose or a later batch.

Stable rows can be partial progress: main TV sequence, exact movie, OVA/OAD file, mini-anime sequence, supported SP subset, or scoped evidence-gap supplemental subset. A hard side frontier can stay open while unrelated stable rows are saved.

When a workpaper checkpoint or draft preview says rows are missing, do not satisfy it by saving only the easy main-season rows and then restarting broad evidence. Save the next stable side/exception subcluster too, or record one specific blocker tied to one group/source path and one next fact.

Before finalizing a numbered side/SP/OVA/movie-like file as supplemental, call `find_bangumi_targets_for_local_file` for the exact visible `source_path` or one representative path in a uniform sequence. Treat returned `duration_candidate_episode_rows` as evidence for Pi to judge; keep supplemental only when that fact check exposes no supportable row or the target surface is explicitly exhausted. A different `subject_id` alone is not a contradiction for side/SP/OVA/movie-bundle extras; require concrete relation/title/duration/locator mismatch evidence before recording `candidate_rows_not_supportable`. If validation returns a numbered supplemental sequence review warning, do that representative targeted lookup and validate again.

When saved decisions compile into a complete draft, call `validate_recipe_params_draft(validation_snapshot={summary, accepted_scope, open_issues, next_action})`. If verifier feedback appears, patch only the named rule/path/target or gather the one targeted fact it asks for. When validation is accepted with no review warnings, explicitly call `submit_organize_recipe_params` with `submit_snapshot={summary, accepted_rule_count, review_notes}`, then call `goal_complete`.

If validation is mechanically accepted but still has review warnings with `warning_candidate_episode_rows`, either map a supportable candidate or patch the supplemental rule with `review_resolutions` for the named `source_path` and candidate episode IDs. Use `review_resolution_candidate_episode_ids` when present; compact candidate rows may show only a sample. Use `fail_closed` only when the whole case cannot be resolved after that structured judgment path.

Patch repairs use exactly one canonical intent for each rule name. To keep a rule, use `patch_rules`/`replace_rules` and do not list it in `remove_rule_names`; to split/delete a rule, list the old name in `remove_rule_names` and put only new or fully replaced rows in `append_rules`. Keep `patch_delta` top-level, never inside `recipe_params_patch`.

If validation says a side-folder file duplicated a main movie/episode target, read that as target-key feedback, not proof the local videos are the same content. When title, runtime, folder role, or content shape makes the side file incompatible with the main movie/episode, leave the main exact rule intact and reopen the atlas row surface for the side group. Patch the side rule to a distinct exposed special/side row, or to a scoped supplemental gap only after that row surface is exhausted.
