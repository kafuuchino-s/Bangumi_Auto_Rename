---
name: organize-recipe-contract
description: Use when recipe params, selectors, helper scripts, or verifier repair remain unclear after a validate/submit issue; not needed for an ordinary first draft.
---

# Organize Recipe Contract

The final output is a Python-verifier accepted `OrganizeRecipeDraft` submitted with `submit_organize_recipe_params` or `submit_organize_recipe`, or a safe `fail_closed`.

Use real identifiers. Local identity is the real `source_path` from `get_local_group_detail`, `get_local_file_detail`, or `case_input.context.local_files`. `task_source_path` is only the task root. Bangumi identity is `subject_id`, `episode_id`, legal `episode_type`, `sort`, and `ep`.

The verifier is a strict mechanical gate. It checks local coverage, duplicate targets, legal exposed Bangumi targets, selector shape, and review warnings. It does not prove that the selected subject is the right season, movie, OVA, special, or franchise entry; that semantic choice comes from Pi's evidence reading.

## Working Board

Keep a small mental board while you work. One row per local group is enough:

```text
local_group | target evidence | recipe rule | status | open issue
```

Use these statuses as your own thinking labels, not as fixed-layer commands:

- `unknown`: local group exists, but target evidence has not been checked.
- `anchored`: plausible Bangumi subject or episode evidence exists.
- `draftable`: a mapped recipe rule can be trial-validated.
- `supplemental_candidate`: current evidence does not expose a supportable Bangumi target, but the group can be covered as supplemental.
- `side_frontier`: a non-main anime/video-shaped group still needs relation-graph or targeted title evidence.
- `repairing`: verifier or review feedback named this group, rule, path, or target.
- `accepted`: validation/submit accepted this group without open warnings.

The board is useful because every tool call should move one row forward: expose one missing fact, draft one rule, repair one named issue, submit, or fail closed.

## Reading Path

Start with the navigable context, not a full JSON dump:

1. Use `case_input.case_overview` or `get_case_overview()` as the map.
2. Use `list_local_groups(detail=false)` as the local group index.
3. Open `get_local_group_detail(group_ref, detail=false)` only for groups you choose to inspect; use `detail=true` only when source paths or file facts matter.
4. Use Bangumi tools to expose subject and episode evidence for the board rows you are actively filling.
5. Use `get_local_selector_scaffold(group_ref)` or `get_local_recipe_params_scaffold(group_ref)` when selector shape is the blocker. These tools copy local selector facts only.

`group_ref` is a local selector shorthand. It expands local source selectors/ranges; it never chooses `subject_id`, `episode_id`, `media_kind`, `episode_type`, disposition, or supplemental status.

For one standalone main-title group, direct Bangumi search can be the shortest anchor. For a package with multiple seasons, movies, OVAs, specials, or side-content groups under one franchise, use the human workflow: search one reliable anchor, expand its anime related graph, and match the remaining local group titles against that graph. Direct searches for every group are a fallback for graph misses or conflicts, not the default.

## Related Graph Closure

After the main TV/movie anchors are mapped, do not treat the case as complete just because the regular episodes line up. Put every remaining anime/video-shaped non-main group into a side frontier: OAD, OVA, SP, mini anime, chibi short, recap movie, side story, special, or named movie-like file.

Close that frontier the way a human would:

1. Expand the related graph from confirmed anime anchors.
2. Match related anime/video subjects against the frontier's local titles, qualifiers, counts, episode titles, and durations.
3. When a related subject explains a frontier group, draft the mapped rule and add that subject to the anchor set.
4. Expand from newly mapped side subjects when their graph can explain remaining frontier groups.
5. Repeat until a graph/search pass adds no new plausible anime/video mapping for the remaining frontier.

Only after this closure stalls should unresolved frontier groups become final supplemental candidates. Supplemental is the result of "the closure found no supportable target", not a shortcut after regular episodes already pass.

## First Validation

`validate_organize_recipe_params` is a trial check, not final submission. A first validation does not need to be accepted or warning-free.

Call the first validation when every visible local group has either:

- a testable mapped rule backed by current Bangumi evidence, or
- a testable `disposition: "non_bangumi_or_supplemental"` rule with a clear evidence gap.

Do not wait for exhaustive relation graphs, every SP frontier, or perfect confidence before the first validation. If a numbered SP/bonus group has compatible exposed rows, map it. If targeted evidence does not expose compatible rows by sort/ep/title/count, cover it as supplemental with the evidence gap in the rule reason.

If one group remains uncertain after representative evidence, make it a testable `disposition: "non_bangumi_or_supplemental"` rule for the first validation instead of postponing the whole case. Validation is allowed to be invalid or reviewed; it gives the next scoped repair.

If target evidence exists but selector details are still awkward, validate the best testable params instead of finishing the selector mentally. Duplicate local locators, split files, variant suffixes, and uncertain `exclude_regex` choices are exactly the kind of mechanical feedback `validate_organize_recipe_params` should surface.

If a draft maps an anime/video frontier group and validation reports a mechanical issue, repair the mapped rule shape first. Do not downgrade that group to `non_bangumi_or_supplemental` just to make validation pass unless targeted title/episode evidence contradicts the mapping or the related-graph closure has stalled with no supportable target.

If you write or think "ready", "enough", "validate", or "submit", the next action should be `validate_organize_recipe_params`, `submit_organize_recipe_params`, `submit_organize_recipe_params_patch`, or `fail_closed`. The only exception is a concrete blocker such as "LG7 lacks any subject evidence"; then fetch exactly that evidence.

## Repair Mode

After `validate_organize_recipe_params`, `submit_organize_recipe_params`, or a patch tool returns invalid/review feedback, stop broad exploration. Work only from:

- `verifier_result.issues`
- `repair_hints`
- `review_warnings`
- `repair_mode`

Repair the named rule/path/target first. Fetch more evidence only when the issue or warning asks for targeted evidence. Otherwise patch the affected params and validate again.

Use `validate_organize_recipe_params_patch` when a previous params validation or submit exists and only a few rules changed. Use `submit_organize_recipe_params_patch` once after the same patch has validated accepted; the submit tool reuses the accepted merged params and does not apply `append_rules` twice.

Repair is not a permission to lower semantic quality. If the affected rule already has plausible anime/video target evidence, patch `media_kind`, `episode_type`, `episode_id`, `episode_range`, selector, offset, or duplicate/split handling before changing it to supplemental.

For selector repairs, either set selector fields directly on the rule (`exact_paths`, `source_pattern`, `filename_regex`, `path_glob`, `exclude_regex`) or set them inside `set.select`; both mean "replace or update this rule's local selector."

When uncovered paths and duplicate coverage belong to the same local group, replace or patch the existing partial rule so that one rule covers the group exactly once. Do not append a second supplemental rule over paths already covered by an earlier supplemental rule.

When an uncovered path is in the same local group as an existing supplemental rule, patch that supplemental rule to include the missing exact path or replace it with one compact supplemental selector. Do not change unrelated mapped movie/OVA/special exact-path rules just to satisfy coverage.

For duplicate targets caused by local split or variant files such as `_1`/`_2`, part markers, or version suffixes, either assign distinct exposed Bangumi rows or exclude only those variant paths from the mapped sequence and cover them as supplemental exact paths. Validate that patch before considering whole-case `fail_closed`.

For duplicate targets caused by a multi-file `group_ref`, `source_pattern`, or multi-path exact selector with one fixed `episode_id`, `sort`, or `ep`, repair the rule shape before searching again. A sequence under one subject should derive targets from `{ep}` plus `episode_range` / `episode_number_field`; separate movie/OVA/special files need separate exact-path rules with distinct exposed targets.

If a local group card or selector scaffold reports `duplicate_episode_numbers_in_group`, do not map the whole group as one sequence without handling the duplicate locator. Choose one file per target row and cover the extra split/variant file as supplemental, or validate immediately and repair the `duplicate_target`.

Do not use possible duplicate locators as a reason to skip first validation. A draft that maps the sequence and gets `duplicate_target` is better than a no-validation timeout, because the repair hint will name the affected paths and rule.

For `review_warnings`, resolve only the listed warnings. A submit result with `status: "review"` is not final and is not a reason to switch to raw JSON; keep using params tools.

## Numbered SP And Short Side Content

Treat numbered SP/bonus groups as their own board rows, not as leftovers from the parent TV season. A main-season subject with no SP rows is weak negative evidence for a group whose local title says `Ple Ple Pleiades`, `Mini Anime`, `OAD`, `OVA`, `Bangaihen`, or another side-content name.

A parent-titled `SPs` folder is also a side-frontier row. If relation-graph evidence exposes a same-season mini-anime, chibi short, OVA/OAD, or special subject whose episode count, durations, and row order fit the local `SP01-SPnn` files, draft a mapped sequence even when the side-content title is absent from the filenames.

For a long standalone OVA/OAD/SP-like file, prefer a related one-episode exact mapped rule when title, runtime, and relation evidence fit. Treat it as a supplemental duplicate compilation only after that targeted exact-rule evidence does not support a distinct Bangumi row.

For these groups, build evidence at the title level first:

- compare the local group title and qualifier against Bangumi subject titles, aliases, relation cards, and episode titles;
- after a franchise anchor exists, check related short-anime/special/OVA subjects before judging from the parent TV episode list;
- when several local side-content groups share a base title but differ by season or part qualifier, such as `II`, `III`, `Part 2`, or a year, treat each qualified group as a separate target search/graph row;
- compare the local locator range, such as `SP01-SP13`, with exposed Bangumi `sort` or `ep` rows and count;
- use episode titles when the subject title is ambiguous or when TMDB/Bangumi language labels look noisy.

One unqualified side-title search is not evidence for all season-qualified side groups. If `Side Story`, `Side Story II`, and `Side Story III` appear as separate local groups, the board should have separate target evidence for each one, preferably from the related graph after the franchise anchor.

Do not use a parent-season search as negative evidence for a side-content group. Searching `Franchise II` can confirm the second TV season, but it does not prove that `Side Story II` or `Mini Anime II` lacks a Bangumi subject. For those groups, use the side-title anchor's related graph or a qualified side-title search.

When the exposed rows fit by title plus number/count, draft a normal mapped sequence. Keep `SP` in the selector or reason, use `episode_range` for the local numbers, and usually use `episode_offset: "EP"` with the row's real `episode_type`.

When the exposed rows do not fit, supplemental is fine. The reason should name the targeted evidence that failed, such as "searched same-title short subject and related graph; no exposed rows match SP01-SP04 by title/count." Do not use a missing parent-season SP list as the only reason for a numbered side-content group.

## Params Shape

Use compact semantic params. Python builds the full JSON recipe, escapes source patterns, canonicalizes paths, infers exact episode row type when possible, and fills mechanical defaults.

```json
{
  "rules": [
    {
      "name": "TV episodes",
      "group_ref": "LG1",
      "subject_id": 12345,
      "media_kind": "tv",
      "episode_type": "regular",
      "reason": "local group maps to this Bangumi episode run"
    },
    {
      "name": "TV explicit selector",
      "source_pattern": "Episode {ep}.mkv",
      "subject_id": 12345,
      "media_kind": "tv",
      "episode_type": "regular",
      "episode_range": "1-12",
      "episode_number_field": "sort",
      "episode_offset": "EP",
      "reason": "filename episode number matches Bangumi sort"
    },
    {
      "name": "Movie or special",
      "exact_paths": ["Movie.mkv"],
      "subject_id": 67890,
      "media_kind": "movie",
      "episode_id": 123456,
      "reason": "exact file title matches the exposed Bangumi episode row"
    },
    {
      "name": "Movie subject",
      "exact_paths": ["Movie.mkv"],
      "subject_id": 67890,
      "media_kind": "movie",
      "reason": "exact file title matches the exposed one-movie Bangumi subject"
    },
    {
      "name": "Merged episode file",
      "source_unit": "single_file_multi_episode",
      "exact_paths": ["Merged OVA.mkv"],
      "subject_id": 24680,
      "media_kind": "ova",
      "episode_type": "regular",
      "episode_range": "1-3",
      "reason": "one visible file has chapters or duration supporting the three exposed episode rows"
    },
    {
      "name": "Bonus extras",
      "group_ref": "LG9",
      "disposition": "non_bangumi_or_supplemental",
      "reason": "package bonus with no supportable Bangumi episode target"
    }
  ]
}
```

Use `source_pattern` with `{ep}`, `{ep:02}`, or `{ep:02d}` for ordinary numbered sequences. Use `exact_paths` or `source_path` for a single OVA, SP, movie, separate one-file entry, or irregular exception. A literal filename without an `{ep}` token is not a sequence locator.

Do not cover a numbered multi-episode mapped sequence by listing many `exact_paths` plus `episode_range`; the verifier cannot derive one target per file from that shape. Use `group_ref`, `source_pattern`, or `filename_regex` with `{ep}` for the sequence, and cover only split/variant leftovers as supplemental exact paths.

For multi-file sequence rules, do not include `episode_id`, `sort`, or `ep` unless every selected file intentionally maps to that same exact episode row. Keep those fields absent so Python derives one target per file from `{ep}`. For a two-movie recap set or other separate one-file items, split into separate exact-path rules instead of using one `group_ref` with one fixed `episode_id`.

`exact_paths` must be complete visible `source_path` strings. Do not write a prefix, basename fragment, or partially copied path. If a subset has long path names, copy them from `get_local_group_detail(detail=true)` or use one group-level supplemental selector when the whole group is supplemental.

`episode_range` is the local captured file-number range. If local files `27-33` map to Bangumi rows `1-7`, use `episode_range: "27-33"` plus `episode_offset: "EP-26"`. Use `episode_number_field: "ep"` only when local numbering matches Bangumi `ep` while `sort` continues.

`episode_offset` accepts only `EP` arithmetic such as `EP`, `EP-10`, or `EP*2-1`. Do not use `SP` as `episode_offset`; `SP` is a filename locator or content-shape token, not an offset expression. For `SP01` files that map to Bangumi rows 1-13, keep `episode_range: "1-13"` and `episode_offset: "EP"`.

Supplemental or excluded files use the enum field `disposition: "non_bangumi_or_supplemental"`. Do not write boolean flags such as `non_bangumi_or_supplemental: true`, `supplemental: true`, or `exclude: true`.

For supplemental groups, use one `group_ref`, `path_glob`/`filename_regex`, or exact_paths list that covers the intended paths exactly once. Supplemental rules do not need `episode_range`, `episode_offset`, `episode_type`, `subject_id`, or `episode_id`.

Legal `media_kind` values are `tv`, `movie`, `ova`, `oad`, `sp`, `special`, and `unknown`. Legal `episode_type` values are `main`, `regular`, `special`, `ova`, `oad`, `movie`, and `unknown`. Keep `media_kind` and `episode_type` separate; a movie-shaped subject can have a `regular` episode row.

SP filenames and `media_kind: "sp"` do not imply `episode_type: "special"`. Use the Bangumi row type. Many short SP/OVA/movie-shaped subjects expose their rows as `regular`. If `missing_target_episode` appears even though the subject has matching rows by sort/ep/title/count, check `episode_type` before converting the group to supplemental.

Patch shape after a params validation:

```json
{
  "patch_rules": [
    {"name": "Existing rule", "set": {"exclude_regex": "SP08_2"}, "unset": ["episode_id"]}
  ],
  "append_rules": [
    {"name": "Bonus exact", "exact_paths": ["real/source.mkv"], "disposition": "non_bangumi_or_supplemental", "reason": "no supportable Bangumi row"}
  ],
  "remove_rule_names": ["Bad rule"]
}
```

## Stop Rules

Submit immediately when validation is accepted and `review_warnings` is empty. After accepted submit, call `goal_complete`.

Use `fail_closed` only when strict evidence is insufficient or contradictory after a targeted attempt, or when no supportable recipe can cover the visible groups. `budget_exhausted` is a runner outcome, not a semantic reason to write yourself.

Old run artifacts, old `final_result.json` files, repository tests, and templates are not evidence for the current case. Finish through params validate/submit, raw validate/submit only for generated JSON debugging, or `fail_closed`.

## References

- `references/recipe-params.md` is useful when params aliases, selector syntax, raw recipe JSON, or verifier repair details remain unclear after reading repair hints.
- `assets/organize_recipe.template.json` is a raw JSON starting shape for debugging generated recipe JSON.
- The local helper is useful for debugging a schema/selector problem:

```bash
node .pi/skills/organize-recipe-contract/scripts/check-organize-recipe.mjs "<scratch organize_recipe.json>" "<case_input.json>" > "<scratch helper_check.json>"
```
