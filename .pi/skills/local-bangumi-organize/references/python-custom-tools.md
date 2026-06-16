# Python Custom Tools

All tools return JSON with an `ok` field. On failure, read `error` and retry with narrower IDs, paths, or a different query.

## Case Navigation

`get_case_overview({})`

Returns the compact case map: counts, local group cards, seen Bangumi evidence counts, recipe state, and navigation handles. It is an overview page, not a semantic route recommendation.

`list_local_groups({ "detail": false })`

Returns the local group index. Use `detail: true` only when you need the expanded fixed local grouping facts. Group cards expose folders, title hints, locator kinds, number ranges, duration summaries, representative paths, and boundary warnings; they do not choose subject IDs, episode IDs, media kind, or supplemental status.

`get_local_group_detail({ "group_ref": "LG1", "detail": true })`

Expands one local group. This is the normal way to read real `source_path` strings and file-level facts for a chosen group. Use `detail: false` for a smaller group page when file-level facts are not needed.

`get_local_selector_scaffold({ "group_ref": "LG1" })`

Returns selector/range params stubs for one group. It copies local selector facts only. Params may use `group_ref` as a local selector shorthand; fill Bangumi target fields or supplemental disposition yourself from evidence.

`get_recipe_state({ "detail": false })`

Returns latest params, verifier, submit, and final-result state. Use `detail: true` for full verifier/params/debug payloads after a validation.

`get_case_context({ "detail": false })`

Returns bounded navigation context for a compact refresh. `detail: true` expands the legacy full debug context for helper/debug use, not normal startup reading.

## Subject Evidence

`search_bangumi_subjects({ "query": "...", "max_subjects": 5 })`

Adds candidate subject IDs. Good queries include original title, Chinese title, romaji-like root names, release folder names without group/resolution tags, and relation-specific terms such as Movie, OVA, OAD, SP only when they are present in the local evidence. Do not append site words such as `Bangumi`, `BGM`, `subject`, or `anime database`; this tool is already scoped to Bangumi.

`select_bangumi_anchor_subject({ "anchor_subject_id": 12345, "reason": "main TV/movie anchor because ..." })`

Use this as the normal first graph move for complex packages after one reliable main anime/video anchor search. Pi chooses the anchor; Python records that choice, immediately builds the full relation atlas, and appends a Board Delta. It is an evidence bootstrap transaction only: no subject ranking, local group match, recipe row, or supplemental recommendation.

`lookup_bangumi_subject({ "subject_ids": [12345] })`

Fetches subject details for subject IDs.

`expand_related_subjects({ "subject_id": 12345, "subject_types": ["anime"], "relation_kinds": [], "max_subjects": 8 })`

Leave `relation_kinds` empty when unsure. Relation strings come from Bangumi and are evidence, not final recipe values. Do not pass `relation_kinds: ["anime"]`; that field filters relation labels, not subject type. For normal anime rename cases, use `subject_types: ["anime"]` to keep only anime/video subjects and drop book/music/game/etc. relations from the tool result. This tool returns one relation hop.

`expand_related_graph({ "subject_ids": [12345], "subject_types": ["anime"], "relation_kinds": [], "max_depth": 3, "max_subjects": 32 })`

Use this when a case has unresolved seasons, specials, OVAs, OADs, movies, side stories, or recap-like files after you have at least one plausible anime subject. It recursively expands a bounded related-subject graph and returns compact `relation_subjects`, `subjects`, and `edges`. Filter returned subjects to anime/video-shaped entries; ignore book, manga, novel, music, game, radio, drama CD, soundtrack, and real-person/live/event relations unless the local video evidence explicitly points there.

`build_bangumi_relation_atlas({ "anchor_subject_id": 12345, "max_subjects": 160, "hydrate_episode_surfaces": true })`

Debug/manual fallback when you need to rebuild an atlas from a known anchor. It recursively follows reachable Bangumi anime/video related subjects until the frontier is exhausted, or until an abnormal guard such as `max_subjects`, `max_relation_fetches`, relation fetch failure, or emergency depth stops it. It writes `artifacts/bangumi_relation_atlas/<atlas_id>.json/.md`.

Read `traversal_status.frontier_exhausted` and `stop_reason`. If the atlas hits a guard, treat it as incomplete evidence and use targeted follow-up; do not pretend the works family is complete. The atlas returns compact subject cards and `episode_surface` counts/samples for regular, special, OVA/OAD, movie-like, or exact rows. It is facts only: no subject ranking, local group match, recipe row, or supplemental recommendation.

After an anchor is known, use the relation atlas as the series map. This is the preferred evidence path for multi-season, movie-box, OVA/special-box, and franchise side-content packages: one reliable anchor search, then atlas-match the remaining visible local group titles. Fetch extra episode details only for related anime subjects whose atlas row surfaces leave a named gap.

Use atlas closure for side frontiers. When an atlas subject explains a remaining local OVA/OAD/SP/mini-anime/movie/recap group, save that stable row or use one targeted row lookup for the missing surface. Stop closure only when the atlas plus targeted title/episode checks add no new plausible anime/video target for the remaining frontier.

After a related subject is found, inspect its target surfaces instead of treating the subject as one flat target. Its regular rows may explain one local group, while special, OVA/OAD, or movie-like rows may explain another local folder or subcluster. If a mixed side folder has some files matching exposed special rows and other files with no row, write exact mapped rules for the matched files and supplemental rules only for the unmatched files.

For a same-folder movie/special collection with many named files from one franchise, one clean anchor search plus `select_bangumi_anchor_subject` should come before per-title broad searches. Build a local title checklist, match it against atlas subjects, and search individual missing titles only when the relation atlas does not expose them or exposes conflicting possibilities.

One `max_depth` value is only a bounded tool call, not a proof that the series graph is complete. Read `traversal_status`:

- `frontier_exhausted`: true only when this bounded traversal has no more seen anime/video subjects that still need relation expansion and did not stop on a subject limit or fetch failure.
- `next_subject_ids_to_expand`: subject IDs to use as the next `subject_ids` seed when a named local group is still unresolved.
- `new_related_subject_ids`: mechanically ordered related IDs discovered by this call, not a recommendation.

If `next_subject_ids_to_expand` is non-empty, call `expand_related_graph` again only for targeted repair or when a specific named local group still has no supportable target. Do not postpone the first params validation just because the frontier is not exhausted. Frontier exhaustion is strongest as final fail-closed or final supplemental evidence.

Read `relation_subjects` first. It is the compact relation index with subject IDs, titles, type, platform, date, and relation labels. Use graph `edges` only when you need to understand how a subject was reached.

## Episode And Target Evidence

`get_episode_list({ "subject_id": 12345, "episode_scope": "all_if_small", "max_episode_cards": 240 })`

Episode rows expose `episode_id`, recipe `episode_type`, `sort`, `ep`, API type fields, title, and duration. Use the row's recipe `episode_type` in the recipe target. A one-episode movie, OVA, or special subject may still expose its row as `episode_type: "regular"`; that is valid. Keep `media_kind` as your organize category.

Read regular, special, OVA/OAD, and movie-like rows as separate target surfaces. A subject already used for a regular side sequence can still provide exact special rows for a different local subcluster.

If a mixed `SPs` folder still has numbered anime-shaped files after a related side subject was used for another group, fetch or inspect all relevant rows for that same subject before calling those files supplemental. A regular-only row view means only the regular surface was checked; it does not rule out special rows. Duplicate-target feedback between a main movie file and a side-folder SP file should trigger this target-surface check before a supplemental patch.

Duplicate-target repair should compare title and duration against the target surface being duplicated. If the side-folder file is not shaped like the main movie/episode and an exposed special/OVA/movie-like row fits it better, map that exact file to the distinct row instead of covering it as supplemental.

`get_target_window({ "subject_id": 12345, "sort_start": 1, "sort_end": 12 })`

Use for contiguous TV or special ranges.

`get_target_detail({ "episode_ids": [98765] })`

Use when you need exact episode title/type evidence.

`get_target_detail({ "subject_id": 12345, "sort": 1 })`

Use when you know the target sort but not the episode ID yet.

`get_local_file_detail({ "paths": ["Season 1/01.mkv"] })`

Use before writing exact-path exception rules when filenames or local facts are ambiguous.

## Compact Subject And Episode Fact Helper

`find_bangumi_targets_for_local_file({ "source_path": "...", "title_query": "...", "kind_hint": "ova", "max_subjects": 5, "max_episode_cards": 24 })`

Use this when a specific visible file needs compact Bangumi subject and episode evidence before drafting or repairing a recipe rule. It performs a compact subject search plus episode lookup, returns nearby `duration_candidate_episode_rows` when already-exposed Bangumi rows match a side/special-like local file by duration, and updates the Python evidence workspace. It does not rank semantic candidates, choose a target, or return recipe JSON.

This is the quick check for uncertain supplemental decisions. Before keeping a numbered side/SP/OVA/movie-like visible file supplemental, call this tool for the exact path or a representative path in the same uniform sequence; if the returned subject/episode rows do not support a target, record that exhausted-target reason in the supplemental rule.

The result preserves mechanical order: Bangumi search result order for subjects, and Bangumi episode `sort`, then `ep`, then `episode_id` order for episode rows. Use the returned subject IDs, episode rows, and duration candidate rows as facts only; Pi chooses the semantic target and then calls the params validation/submit tools.

This helper intentionally provides no fixed-layer recipe, no readiness flag, and no target recommendation. If you have enough evidence, write recipe params yourself and validate.

## Params Validation And Submit

`upsert_recipe_group_decision_one({ "decision": {...} })`

Preferred normal path. Save one compact group/subcluster judgment as soon as it is stable. Use `group_ref` plus `file_numbers`, `file_number_range`, `path_contains`, or exclude filters before listing many `exact_paths`.

`upsert_recipe_group_decision({ "decisions": [...] })`

Batch path for canonical decision rows. This is useful after an atlas or verifier pass makes several rows stable at once. Valid canonical rows are saved; invalid rows are rejected by `decision_index` / `decision_name` and must be resent after repair. Rejected rows are not migrated or coerced.

Use this while exploring. It saves compact Pi-owned group/subcluster judgments and Python mechanically compiles them into `recipe_params_draft`. Useful fields include `group_ref`, `file_numbers`, `file_number_range`, `path_contains`, `exclude_path_contains`, `source_pattern`, `exact_paths`, `subject_id`, `media_kind`, `episode_type`, `episode_id`, `episode_ids`, `disposition`, and `reason`. Selector fields only choose local files; Pi still chooses Bangumi targets and supplemental status.

Keep the tool arguments compact and schema-correct. Use short reasons. For numbered one-file or subcluster decisions, prefer `group_ref` plus `file_numbers`, `file_number_range`, `path_contains`, or `exclude_path_contains`; use full `exact_paths` only when the compact selector cannot name the files safely. Use `episode_range` as a string such as `"1-13"`, not `[1,13]`. Use legal `media_kind` values only (`tv`, `movie`, `ova`, `oad`, `sp`, `special`, `unknown`).

Board and transaction notes are strict small envelopes, not arbitrary JSON. Use `board_delta`/`content` with `summary`, `observations`, `blockers`, `next_action`; `validation_snapshot` with `summary`, `accepted_scope`, `open_issues`, `next_action`; `patch_delta` with `summary`, `changed_rules`, `evidence_refs`, `reason`; and `submit_snapshot` with `summary`, `accepted_rule_count`, `review_notes`.

`get_recipe_group_decisions({ "detail": false })`

Reads saved decisions and the compiled draft coverage preview. If a group is only in reasoning, save it here before more broad evidence.

`validate_organize_recipe_params({ "recipe_params": <params> })`

Use this for hand-authored work and trial checks. Params use strict canonical rule fields: `group_ref`, `file_numbers`, `file_number_range`, `path_contains`, `exclude_path_contains`, `exact_paths`, `source_pattern`, `filename_regex`, `exclude_regex`, `source_unit`, `episode_range`, `episode_range_start`, `episode_range_end`, `episode_offset`, `episode_number_field`, `subject_id`, `media_kind`, `episode_id`, `episode_ids`, `episode_type`, `sort`, `ep`, `disposition`, and `reason`. Python expands `group_ref` into local selector facts, turns `source_pattern` into escaped recipe regex, treats extra placeholders such as `{a}` as wildcard spans, fills defaults such as `episode_offset: "EP"`, writes `organize_recipe.json`, and returns the generated `organize_recipe` plus verifier issues or review warnings. The first trial check does not need to be accepted or warning-free. Invalid/review feedback is part of the repair loop; it does not finalize the case. Use `group_ref` when the local group card already expresses the selector; combine `group_ref + source_pattern` only when the explicit pattern matches that group and includes `{ep}` for sequence mapping; use `exact_paths` only when compact group selectors cannot safely identify one visible movie, OVA, SP, or special file.

Invalid duplicate-target feedback may include `issue_repair_contexts`. Read it before patching: it names the affected files, duration/path-shape mismatch, candidate exposed rows when available, and whether to inspect or patch an alternate target surface before supplemental.

For one visible local file that intentionally covers multiple Bangumi episode rows, set `source_unit: "single_file_multi_episode"`, one `exact_paths` entry, and `episode_range`. Do not use boolean aliases such as `merged: true`, and do not collapse the file to the first `episode_id`.

`submit_organize_recipe_params({ "recipe_params": <params>, "summary": "..." })`

Use this after params validation has no blocking issues. If it returns `accepted: true`, call `goal_complete` immediately.

For a visible file that should be covered but does not have a clear Bangumi episode target, use a rule with `disposition: "non_bangumi_or_supplemental"` and a plain reason instead of leaving it uncovered or mapping it to a duplicate episode.
