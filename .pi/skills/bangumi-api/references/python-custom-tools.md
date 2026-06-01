# Python Custom Tools

All tools return JSON with an `ok` field. On failure, read `error` and retry with narrower IDs, paths, or a different query.

## Case Context

`get_case_context({ "detail": true })`

Returns hard-filtered local files as real `source_path` strings, known Bangumi subjects, known Bangumi episodes, and the recipe contract. Use `detail: false` when you only need a compact refresh.

## Subject Evidence

`search_bangumi_subjects({ "query": "...", "max_subjects": 5 })`

Adds candidate subject IDs. Good queries include original title, Chinese title, romaji-like root names, release folder names without group/resolution tags, and relation-specific terms such as Movie, OVA, OAD, SP only when they are present in the local evidence. Do not append site words such as `Bangumi`, `BGM`, `subject`, or `anime database`; this tool is already scoped to Bangumi.

`lookup_bangumi_subject({ "subject_ids": [12345] })`

Fetches subject details for subject IDs.

`expand_related_subjects({ "subject_id": 12345, "subject_types": ["anime"], "relation_kinds": [], "max_subjects": 8 })`

Leave `relation_kinds` empty when unsure. Relation strings come from Bangumi and are evidence, not final recipe values. Do not pass `relation_kinds: ["anime"]`; that field filters relation labels, not subject type. For normal anime rename cases, use `subject_types: ["anime"]` to keep only anime/video subjects and drop book/music/game/etc. relations from the tool result. This tool returns one relation hop.

`expand_related_graph({ "subject_ids": [12345], "subject_types": ["anime"], "relation_kinds": [], "max_depth": 3, "max_subjects": 32 })`

Use this when a case has unresolved seasons, specials, OVAs, OADs, movies, side stories, or recap-like files after you have at least one plausible anime subject. It recursively expands a bounded related-subject graph and returns compact `relation_subjects`, `subjects`, and `edges`. Filter returned subjects to anime/video-shaped entries; ignore book, manga, novel, music, game, radio, drama CD, soundtrack, and real-person/live/event relations unless the local video evidence explicitly points there.

After an anchor is known, use related subjects as the series map. Fetch episode lists for related anime subjects whose titles, relation labels, dates, or platforms match visible local subgroups. If a matching related subject is still part of the same series but points onward to another season/movie/special, another graph traversal can be useful, but validation should remain the main checkpoint once you have enough evidence for a testable recipe.

For a same-folder movie/special collection with many named files from one franchise, one clean anchor search plus `expand_related_graph` should come before per-title broad searches. Build a local title checklist, match it against graph subjects, and search individual missing titles only when the relation graph does not expose them or exposes conflicting possibilities.

One `max_depth` value is only a bounded tool call, not a proof that the series graph is complete. Read `traversal_status`:

- `frontier_exhausted`: true only when this bounded traversal has no more seen anime/video subjects that still need relation expansion and did not stop on a subject limit or fetch failure.
- `next_subject_ids_to_expand`: subject IDs to use as the next `subject_ids` seed when a named local group is still unresolved.
- `new_related_subject_ids`: mechanically ordered related IDs discovered by this call, not a recommendation.

If `next_subject_ids_to_expand` is non-empty, call `expand_related_graph` again only for targeted repair or when a specific named local group still has no supportable target. Do not postpone the first params validation just because the frontier is not exhausted. Frontier exhaustion is strongest as final fail-closed or final supplemental evidence.

Read `relation_subjects` first. It is the compact relation index with subject IDs, titles, type, platform, date, and relation labels. Use graph `edges` only when you need to understand how a subject was reached.

## Episode And Target Evidence

`get_episode_list({ "subject_id": 12345, "episode_scope": "all_if_small", "max_episode_cards": 240 })`

Episode rows expose `episode_id`, recipe `episode_type`, `sort`, `ep`, API type fields, title, and duration. Use the row's recipe `episode_type` in the recipe target. A one-episode movie, OVA, or special subject may still expose its row as `episode_type: "regular"`; that is valid. Keep `media_kind` as your organize category.

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

Use this when a specific visible file needs compact Bangumi subject and episode evidence before drafting or repairing a recipe rule. It performs a compact subject search plus episode lookup and updates the Python evidence workspace. It does not rank semantic candidates, choose a target, or return recipe JSON.

The result preserves mechanical order: Bangumi search result order for subjects, and Bangumi episode `sort`, then `ep`, then `episode_id` order for episode rows. Use the returned subject IDs and episode rows as facts only; Pi chooses the semantic target and then calls the params validation/submit tools.

This helper intentionally provides no fixed-layer recipe, no readiness flag, and no target recommendation. If you have enough evidence, write recipe params yourself and validate.

## Params Validation And Submit

`validate_organize_recipe_params({ "recipe_params": <params> })`

Use this for hand-authored work and trial checks. Params are semantic rule fields such as `source_pattern` or `source_template`, `exact_paths` or `source_path`, `source_unit`, `episode_range` or `range`, `episode_offset` or `offset`, `subject_id`, `media_kind`, `episode_type`, `disposition`, and `reason`. Python turns `source_pattern` into escaped recipe regex, treats extra placeholders such as `{a}` as wildcard spans, fills defaults such as `episode_offset: "EP"`, writes `organize_recipe.json`, and returns the generated `organize_recipe` plus verifier issues or review warnings. The first trial check does not need to be accepted or warning-free. Invalid/review feedback is part of the repair loop; it does not finalize the case. Use `source_pattern` only when the local group has an episode token such as `{ep}`; use `exact_paths`/`source_path` for one visible movie, OVA, SP, or special file.

For one visible local file that intentionally covers multiple Bangumi episode rows, set `source_unit: "single_file_multi_episode"`, one `exact_paths` entry, and `episode_range`. Do not use boolean aliases such as `merged: true`, and do not collapse the file to the first `episode_id`.

`submit_organize_recipe_params({ "recipe_params": <params>, "summary": "..." })`

Use this after params validation has no blocking issues. If it returns `accepted: true`, call `goal_complete` immediately.

## Raw Validation And Submit

`validate_organize_recipe({ "organize_recipe": <recipe> })`

Use this only when debugging generated raw recipe JSON. It is faster and more reliable than searching old artifacts for examples. The result includes concrete verifier issues and repair hints.

`submit_organize_recipe({ "organize_recipe": <recipe>, "summary": "..." })`

Use this after validation has no blocking issues. If it returns `accepted: true`, call `goal_complete` immediately.

For a visible file that should be covered but does not have a clear Bangumi episode target, use a rule with `disposition: "non_bangumi_or_supplemental"` and a plain reason instead of leaving it uncovered or mapping it to a duplicate episode.
