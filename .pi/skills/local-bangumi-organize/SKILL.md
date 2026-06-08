---
name: local-bangumi-organize
description: |
  Use for the full Local-to-Bangumi organize workflow: read local anime release
  structure, gather Bangumi subject/episode evidence, save group decisions,
  validate recipe params, repair verifier feedback, and submit.
---

# Local Bangumi Organize

Finish with a Python-verifier accepted organize recipe submitted through `submit_organize_recipe_params` / `submit_organize_recipe_params_patch`, or a concrete `fail_closed`. Pi chooses Bangumi subjects, episode rows, specials, movies, and supplemental status. Python only persists Pi's work, expands local selectors, and verifies coverage, duplicate targets, exposed legal rows, and selector shape.

Use real identifiers: visible `source_path` strings for local files, and Bangumi `subject_id`, `episode_id`, `episode_type`, `sort`, and `ep` for targets. `task_source_path` is the task root, not a file selector.

## Action-First Output Mode

Keep visible prose short: 1-3 sentences is usually enough. Long reasoning can stay internal; evidence can stay in tool results and artifacts.

Do not paste full mapping tables, full recipe JSON, full drafts, or full verifier issue lists as prose. Save rows with `upsert_recipe_group_decision_one` or draft tools, validate complete drafts with `validate_recipe_params_draft`, patch named issues with params patch tools, and submit accepted params.

Do not emit visible self-reasoning headings such as "Deciding", "Evaluating", or "Considering". Do not write "enough evidence", "figured out", "should save", or "ready to validate" as prose; call the matching tool instead. Use `detail:false` defaults for normal work; pass `detail:true` only when debugging full `repair_hints`, `compiled_plan`, `organize_recipe`, or full draft details.

Tool arguments count as output too. Keep `board_delta`, `validation_snapshot`, `submit_snapshot`, `reason`, and `summary` short. Do not paste `get_case_overview`, `list_local_groups`, `get_local_group_detail`, or atlas JSON into notes; cite `LG*`, subject IDs, episode IDs, and one-line blockers.

For complex packages, the first Bangumi move is one reliable main-title search, then `select_bangumi_anchor_subject(anchor_subject_id, reason)`; do not fail_closed from an empty draft before that anchor exists unless the case input is malformed.

Prefer `group_ref` for ordinary continuous groups. Do not restate a complete group with a full copied `source_pattern`; release codec/audio/hash tokens often vary and cause partial coverage. For numbered one-file or subcluster exceptions, prefer `group_ref + file_numbers/file_number_range/path_contains` before long `exact_paths`. Use full `exact_paths` only for unnumbered, path-ambiguous, or truly mixed exceptions. This output discipline does not reduce the semantic method: still use anchor atlas, row-surface closure, duration/title/content-shape evidence, and targeted episode rows when they help.

## References

This skill is the one Local-to-Bangumi entrypoint. Read references only when that layer is the active uncertainty:

- `references/local-release-reading.md`: local folder/title/token/duration/chapter interpretation.
- `references/bangumi-evidence.md`: Bangumi search, related graph, target surfaces, episode rows, and enough-evidence judgment.
- `references/recipe-params.md`: strict params fields, selector syntax, patch shape, and helper JSON.
- `references/python-custom-tools.md`: exact custom-tool arguments or graph traversal fields.

## Human Closure Loop

Work like a person sorting a release:

1. Read the local package shape from `get_case_overview`, `list_local_groups`, and only the group details you need.
2. Choose one reliable main TV/movie anchor. For franchise, multi-season, movie-box, OVA/OAD/SP, mini-anime, recap, or mixed packages, call `select_bangumi_anchor_subject(anchor_subject_id, reason)` so the anchor choice and full anime/video relation atlas are recorded together before broad side-title searches.
3. Build a side frontier from local groups that are anime/video-shaped but not the main run: SP, OVA, OAD, mini anime, recap, side story, parent-titled `SPs`, long standalone special-like files, and movie-like files.
4. Close the frontier at the Bangumi row-surface level. A related subject can expose regular rows, special rows, OVA/OAD rows, movie-like rows, or one-off exact rows; one subject may support more than one local group or subcluster.
5. When one group or subcluster becomes stable enough to test, save it with `upsert_recipe_group_decision_one`. Do not wait for the whole case, and do not narrate the stable judgment instead of saving it.
6. When saved decisions compile into a draft covering every visible group, call `validate_recipe_params_draft(validation_snapshot=...)`.
7. After validation, patch only named mechanical issues or targeted semantic gaps, then submit accepted params and call `goal_complete`.

The important rhythm is not a fixed checklist. It is: evidence changes judgment, judgment becomes a saved row, a complete saved draft gets validated, verifier feedback gets patched.

Do not let targeted evidence become a substitute for a work product. After a useful evidence burst, save the stable rows, update draft params, validate, or record one compact named blocker; then keep the next evidence call tied to that blocker.

For complex packages, the first anchor is the door into the whole family. Make the first Bangumi evidence batch one reliable main-title search, then use `select_bangumi_anchor_subject(anchor_subject_id, reason)` to record that Pi-chosen anchor and read the complete reachable anime/video genealogy plus compact row surfaces. Do not start by searching every visible side title; treat per-visible-title search as fallback for a named item still missing or contradictory after the atlas.

After an atlas, graph pass, or episode batch changes your judgment, save the stable subset immediately. A useful human workpaper row can be one main season, one exact movie, one OVA/OAD file, one side mini-anime sequence, or one scoped evidence-gap supplemental subset. The hardest frontier can stay open while stable rows are already saved.

When a checkpoint or draft preview reports missing rows, saving only the obvious main seasons is not enough if side/exception evidence was already gathered. Continue by saving the next stable side/exception subcluster, or record one specific blocker tied to one group/source path and one next fact.

A saved row is a target-surface claim, not just a local-group claim. Before saving a `group_ref` row, ask whether every selected file shares one Bangumi subject and one exposed row surface. If the local group contains distinct movie titles, recap parts, OVA/OAD files, numbered specials, split variants, or extras, save only the stable subcluster with `file_numbers`, `file_number_range`, `path_contains`, exclusions, or `exact_paths` when numbering/path filters are not safe.

## Board And Draft

Use the Case Board as a notebook, and group decisions as the working spreadsheet:

- `append_case_board_note(section_type:"Initial Board"|"Board Delta", ...)` records compact local shape, evidence changes, and blockers. Do not paste tool JSON into the board.
- `get_case_board_notes(mode:"tail")` restores context after drift or compression.
- `upsert_recipe_group_decision_one(decision={...})` saves one compact mapped or supplemental judgment. This is the normal incremental action path.
- `upsert_recipe_group_decision(decisions=[...])` accepts canonical decision batches. Valid rows are saved; invalid rows are rejected by `decision_index` / `decision_name` and must be resent after repair.
- `get_recipe_group_decisions(detail=false)` reads saved decisions and compiled draft coverage.
- `upsert_recipe_params_draft(rules=[...])` is for full params rows when you already know the row shape.
- `get_recipe_params_draft(detail=false)` reports local coverage preview; coverage is not semantic validation.
- `validate_recipe_params_draft(validation_snapshot=...)` runs the full verifier only after local coverage is complete.

Board transaction fields keep the note and action atomic: put `validation_snapshot` in validate tools, `patch_delta` in patch tools, and `submit_snapshot` in submit tools. `Verifier Delta` is appended by Python when validate/submit returns invalid or review.

A saved row should be testable. Do not save a row with only a name or only a local selector. Include either a Bangumi target or `disposition:"non_bangumi_or_supplemental"` plus a reason.

Do not use one broad group decision to hold several different Bangumi identities. A movie pair usually needs two exact movie rows unless one Bangumi subject exposes two legal movie rows. A side mini-anime sequence usually needs its own related subject and row surface, not the parent TV subject's regular or missing special rows.

## Side Frontier

Short duration, an `SPs` folder, or a parent TV subject with no SP rows is local shape evidence, not supplemental proof. A numbered side group becomes supplemental only after graph plus targeted title/episode evidence finds no supportable anime/video row for the exact files or subcluster.

Do not map parent-titled `SP01-SPnn` files to the parent TV's regular rows just because numbers line up; that usually duplicates the main episodes. Look for a related side subject or explicit special/OVA/movie-like rows first.

If a mixed side folder has supported files and unsupported extras, split it. Use exact mapped rules for files whose title/order/duration match exposed rows, and exact or compact supplemental rules only for the unmatched remainder. Do not let theater manners, menus, promos, or bonus-shaped files decide the whole folder.

Movie-box and recap folders are exact-surface work. Package numbers such as `01` and `02` often order different named movie subjects; map them as separate exact files when the Bangumi evidence is subject-level or one-row-per-movie. Use a group-level sequence only when one Bangumi subject exposes the matching row sequence.

Treat duplicate feedback as a surface mismatch. It is target-key feedback, not proof that two local videos are the same content. If a side-folder file duplicates a main movie/episode target but its title, runtime, folder role, or content shape does not fit that main target, keep the main exact rule intact and reopen already-exposed side subjects plus their special/OVA/movie-like rows before making the side file supplemental.

One unnumbered file is not automatically a multi-episode span. Use `source_unit:"single_file_multi_episode"` only with chapter evidence, an explicit filename range, or duration close to the target-row sum.

## Validate And Repair

Params validation is a trial check, not final submission. Invalid or review feedback is useful.

Before finalizing a numbered side/SP/OVA/movie-like file as supplemental, Pi should check that target surface itself. Use `find_bangumi_targets_for_local_file` with the exact visible `source_path` or one representative path for a uniform sequence; inspect returned `duration_candidate_episode_rows` as facts, not recommendations. Candidate rows include `ordinal_alignment` between the local group title and candidate subject title; use it as evidence when choosing among same-duration sequel/side subjects. Keep supplemental only when that evidence does not expose a supportable row or the surface is explicitly exhausted.

When validation or submit returns `issue_repair_contexts`, use those structured facts before choosing a cheap patch. They summarize the affected source paths, duration/path shape, candidate exposed rows, and the next repair action; keep the correction in params patch tool arguments.

Repair lock:

- coverage and exact-path issues repair local selectors;
- duplicate targets repair sequence shape, split variants, wrong target surfaces, or exact exceptions;
- missing target rows repair `episode_type`, `episode_range`, `episode_number_field`, `episode_offset`, `episode_id`, or the selected exposed row;
- review warnings get only the targeted evidence they name;
- numbered supplemental sequence warnings need one representative `find_bangumi_targets_for_local_file` call for the exact sequence path, then validation again. Python is asking for Pi-owned evidence provenance, not deciding whether the returned rows are supportable.

Do not change a plausible mapped OVA/OAD/SP/movie/side-story group to supplemental merely to make the verifier pass. If duplicate feedback pairs a short side-folder file with a long main movie/episode target, treat the duration/content-shape mismatch as evidence against reusing that target surface, not as evidence that the side file is disposable. Change semantic target or supplemental status only when targeted evidence contradicts the mapping or the exact side frontier is exhausted.

When validation is accepted and there are no `review_warnings`, explicitly call `submit_organize_recipe_params` with `submit_snapshot`, then call `goal_complete`. `budget_exhausted` is a runner outcome, not a semantic `fail_closed` reason.

## Params Reminders
Gold shapes for `upsert_recipe_group_decision_one`:
```jsonl
{"decision":{"name":"Main TV","group_ref":"LG1","subject_id":123,"media_kind":"tv","episode_type":"regular","reason":"local numbering matches exposed regular rows"}}
{"decision":{"name":"Side mini sequence","group_ref":"LG6","source_pattern":"Show/SPs/Show SP{ep:02}.mkv","episode_range":"1-13","subject_id":456,"media_kind":"sp","episode_type":"regular","reason":"side-folder shorts match exposed mini-anime rows"}}
{"decision":{"name":"Duplicate side variant","group_ref":"LG6","path_contains":["Variant-B"],"disposition":"non_bangumi_or_supplemental","reason":"split package variant has no distinct exposed row"}}
{"decision":{"name":"Exact special","group_ref":"LG4","file_numbers":[2],"subject_id":456,"media_kind":"sp","episode_id":789,"reason":"title matches special row"}}
{"decision":{"name":"Movie one","group_ref":"LG4","file_numbers":[1],"subject_id":111,"media_kind":"movie","reason":"first movie file title matches this movie subject"}}
{"decision":{"name":"Movie two","group_ref":"LG4","file_numbers":[2],"subject_id":222,"media_kind":"movie","reason":"second movie file title matches this movie subject"}}
{"decision":{"name":"Bonus extras","group_ref":"LG9","file_number_range":"3-4","disposition":"non_bangumi_or_supplemental","reason":"targeted closure found no supportable Bangumi row"}}
```
`group_ref` is only local selector shorthand. It never chooses `subject_id`, `episode_id`, `media_kind`, `episode_type`, or supplemental status.
One decision row has one target surface. Do not write plural target fields such as `target_subject_ids`, `subject_ids`, or `bangumi_subject_ids`. For two movies, two exact specials, or a mixed `SPs` folder, split into multiple `upsert_recipe_group_decision_one` calls with `file_numbers`, `file_number_range`, `path_contains`, or `exact_paths`.
Use `group_ref + file_numbers/file_number_range/path_contains` for numbered one-off movies, OVA/OAD/SP files, irregular exceptions, and mixed-folder subclusters when the local numbering/filter is safe. Use full `exact_paths` only when numbering is absent, ambiguous, or the subcluster cannot be expressed compactly. Use `group_ref` alone for ordinary complete local groups whose scaffold already captures the sequence. Use `group_ref + source_pattern` only when you need an explicit side-folder/subcluster template; without `file_numbers`, `file_number_range`, `path_contains`, `exclude_path_contains`, or `exact_paths`, that `source_pattern` must match the whole group and include `{ep}` for sequence mapping.
Tool shape is strict. Write only canonical params fields; do not use aliases such as `source_path`, `path`, `source_template`, `range`, `offset`, nested `select`/`target`/`episode`, plural subject fields, or boolean flags. Write `episode_range` as a string such as `"1-13"` or use `episode_range_start/end`; do not pass `[1,13]`. Use legal `media_kind` values only: `tv`, `movie`, `ova`, `oad`, `sp`, `special`, or `unknown`; do not use raw surface words such as `web` or `anime`.
Use the Bangumi row's legal `episode_type`; SP filenames and `media_kind:"sp"` do not imply `episode_type:"special"`. For exact `episode_id` rules, omit `episode_type` if unsure; Python can canonicalize from exposed rows.
Read `references/recipe-params.md` only when canonical params fields, selector syntax, or exact patch shape remains unclear after tool repair hints.
