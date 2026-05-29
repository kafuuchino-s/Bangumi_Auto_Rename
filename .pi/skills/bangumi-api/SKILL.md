---
name: bangumi-api
description: Use when Bangumi evidence is confusing or tool semantics are unclear, especially relation graph traversal, subject IDs, episode IDs, sort/ep, target windows, specials, OVAs, OADs, or movies.
---

# Bangumi API

Use the Python custom tools as the case-scoped Bangumi API. They expose evidence to the recipe verifier; direct public HTTP calls do not.

## Operating Loop

1. Read the visible local universe from `case_input.visible_source_paths` or `get_case_context({"detail": true})`.
2. Infer local groups from folder structure, title qualifiers, content-shape words, and numbering runs.
3. Search one representative title only until you have one or more plausible anime subject anchors.
4. Once an anchor exists, prefer `expand_related_graph` over more broad searches. Use it as the series map for seasons, cours, specials, OVAs, OADs, movies, side stories, recaps, and alternate versions.
5. For a same-folder collection with many named movie/special/OVA files from one franchise, do one anchor search first, then expand the relation graph from the returned anime subject IDs and compare graph titles against the local file-title list. Search individual titles only for graph misses or real conflicts.
6. Read `relation_subjects` first, then `edges` if the path through the graph matters. Keep anime/video-shaped nodes and ignore book/music/game/radio/soundtrack/live/event nodes unless the local file explicitly points there.
7. Fetch `get_episode_list` or `get_target_window` for matching subjects before recipe submission.
8. For an unresolved named special/movie/side-story local group, expand the related graph layer by layer from the current anime anchors until `traversal_status.frontier_exhausted` is true, or until the case budget requires `fail_closed`. This frontier rule is for supplemental decisions; if you already found a matching subject and exact episode row, draft and validate the mapping instead of continuing graph expansion.
9. Validate a compact params recipe early. If accepted, submit the same recipe and stop. If blocked, fix only the reported issue.

## Subject Rules

- Match the actual local title, including season/subtitle qualifiers. Do not map a qualified entry to a franchise root or earlier season just because the episode count fits.
- The related-subject graph is evidence, not a verdict. Compare graph node titles, relation labels, dates, platforms, episode rows, and local file names.
- Episode count alone is not identity evidence.
- Do not stretch one subject past the episode rows it exposes. Split ranges to related season/cour subjects when the graph and episode lists support that.
- Bangumi `sort` is the default target number for sequence rules. `ep` may restart while `sort` continues. If local filenames use `01-13` and the selected subject's episode list has `ep` 1-13 but `sort` 14-26, use recipe params with `episode_number_field: "ep"` after confirming the subject identity.
- Recipe `episode_type` comes from the Bangumi episode row. It may be `regular` for a one-episode movie/special subject; do not force it to match `media_kind`.
- The Python verifier checks mechanical legality and coverage; it does not prove semantic title correctness.

## Search Discipline

- `search_bangumi_subjects` is already scoped to Bangumi. Do not append `Bangumi`, `BGM`, `subject`, or database words to queries.
- Search clean title terms from the local folder/file and known aliases.
- Do not search every file in a numbered run.
- If repeated searches reuse the same franchise/title words without new target evidence, stop searching and validate a params draft.
- Do not use repeated broad searches to check whether a subject has later episode rows. If a helper result is truncated or a subject ID is plausible, call `get_episode_list`, `get_target_window`, or `validate_organize_recipe_params`; validation can hydrate declared subject evidence.
- Use direct title search for a special subtitle only when no useful anchor exists yet, or after the relevant related graph call reports `frontier_exhausted: true`.

## Specials, OVAs, OADs, Movies

- For `Tokubetsu Hen`, `OVA`, `OAD`, `Movie`, `Gekijouban`, `Bangaihen`, side-story subtitles, or long special-looking files, use `expand_related_graph` from the confirmed anime subject IDs with `subject_types: ["anime"]`, empty `relation_kinds`, and a bounded `max_depth` / `max_subjects`.
- Treat one graph call as one bounded layer expansion, not a proof that the series graph is complete. Read `traversal_status.next_subject_ids_to_expand`; if it is non-empty and a named local group is unresolved, expand again from those subject IDs before giving up.
- Prefer current or frontier subject IDs over only the oldest franchise root. The goal is to exhaust the relevant anime/video relation frontier for this case, not to rely on a fixed depth number.
- After a series anchor is confirmed, use the relation graph to find specifically named movies, compilation/remix entries, extra/side-story entries, and alternate versions before doing more broad title searches.
- Relation labels such as sequel, prequel, side story, special, movie, OVA/OAD, recap, parent, and child are target-identity clues.
- Long special/movie-shaped files can be one-episode Bangumi subjects. Bangumi may expose their single episode as `episode_type: "regular"`; use the episode row's legal type in the recipe.
- If a named special/movie/side-story has an exact `episode_id` from `get_episode_list`, validate a mapped exact-path rule. Do not keep expanding the relation graph merely because the frontier can still grow.
- For a movie collection where each visible file title matches a separate movie-shaped Bangumi subject, validate exact-path movie rules with `subject_id` plus `media_kind: "movie"` instead of fetching `get_episode_list` for every one-episode movie subject. Pull episode lists only for non-movie exceptions, multi-row subjects, or verifier blockers.
- Do not map two differently named movie/special files to the same Bangumi episode just because their package numbers are adjacent. Re-check titles, relation nodes, and exposed episode IDs.
- Numbered `SP01` / `SP02` / `S00E01` files are candidate special entries. Check `get_episode_list({"episode_scope": "all"})` for matching special rows before treating them as supplemental.
- For a long unnumbered standalone title that is not found in the selected subject's episode list, check the related anime/video graph, then use `find_bangumi_targets_for_local_file` with that exact `source_path` if validation asks for targeted evidence. If no supportable anime subject or episode appears, mark that exact path supplemental with the evidence gap in the reason and validate again.
- Only cover a named special/movie/side-story with `disposition: "non_bangumi_or_supplemental"` after the relevant relation frontier is exhausted (`frontier_exhausted: true`) and one clean direct title search still cannot expose a supportable target.
- Companion extras such as recording diaries, interviews, cast/staff talks, travel/location features, making-of, stage greetings, memorial clips, or short bonus documentaries are different from named anime specials/movies/side stories. After the main anime subject is anchored, one clean direct title search or one `find_bangumi_targets_for_local_file` call for the representative companion title is enough before validating them as supplemental, unless the lookup exposes a plausible anime/video target.

## Tool Notes

- `find_bangumi_targets_for_local_file`: compact fact lookup for one visible `source_path`; it returns search results and episode rows, not a chosen target. If `episode_rows_limited` is true, fetch the list/window for that subject or validate params instead of searching similar words again.
- `expand_related_graph`: recursive related-subject graph from one or more `subject_id` values. This is the default after you have anchors.
- `expand_related_subjects`: one-hop relation lookup; use only when you intentionally want one hop.
- `get_episode_list`: expose episode IDs, sort/ep/type, title, and duration rows. Use the returned recipe `episode_type` for mapping.
- `get_target_window`: inspect a sort range for sequence mapping.
- `validate_organize_recipe_params` / `submit_organize_recipe_params`: preferred recipe path. Give semantic parameters; Python builds JSON and escaped regex. Use `source_pattern` for numbered groups with `{ep}`; use `exact_paths` or `source_path` for one visible movie, OVA, SP, or special file.
- `fail_closed`: use when strict evidence is insufficient or contradictory.

Use real identifiers only: local `source_path` strings from the visible universe, and Bangumi `subject_id` / `episode_id` / `episode_type` / `sort` / `ep`. Do not pass `task_source_path` as a local file. Do not copy raw `web` into a recipe. Choose a legal `media_kind`; keep it separate from Bangumi row `episode_type`.

Read `references/python-custom-tools.md` only when you need custom tool schemas or return-shape notes.
