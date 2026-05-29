---
name: organize-recipe-contract
description: Use when recipe params, selectors, helper scripts, or verifier repair remain unclear after a validate/submit issue; not needed for an ordinary first draft.
---

# Organize Recipe Contract

The final output is a Python-verifier accepted `OrganizeRecipeDraft` submitted with `submit_organize_recipe`, or a safe `fail_closed`.

Use real identifiers in recipes. Local identity is the real `source_path` from `case_input.visible_source_paths` or `case_input.context.local_files`; `task_source_path` is only the original task/sample path. Bangumi identity is `subject_id`, `episode_id`, legal recipe `episode_type`, `sort`, and `ep`.

The recipe verifier is a strict mechanical gate, not a semantic title matcher. It can tell you that paths are covered and target IDs are exposed; it cannot prove that the selected subject is the correct season, movie, OVA, or franchise entry. Resolve that with Bangumi subject evidence before final submit.

## Workflow

1. Read `case_input.json` and inspect `case_input.scratch_paths`.
2. Read `case_input.local_structure_summary` if present. Inspect the visible `source_path` values and infer local groups from folders, repeated title prefixes, season/movie/OVA/SP words, and numbering runs before searching Bangumi. The summary is a factual grouping aid, not a target decision.
3. Use Bangumi tools to expose subject and episode evidence by ID. A representative `source_path` lookup is a compact evidence call; it does not choose a target or generate recipe JSON.
4. Prefer params tools: call `validate_organize_recipe_params({ "recipe_params": <params> })`, then `submit_organize_recipe_params({ "recipe_params": <same params>, "summary": "..." })` when accepted and `review_warnings` is empty. Use raw `validate_organize_recipe` only when debugging generated JSON.
5. Validate early when local grouping is clear. Validation can fetch episode evidence for subject IDs already declared in your params or recipe, then run the strict mechanical verifier.
6. After one representative lookup per visible local group, validate a params draft instead of continuing broad search. If a named one-file group has exact `episode_id` evidence, validate it as a mapped rule now.
7. Use `disposition: "non_bangumi_or_supplemental"` only for unresolved bonus-like visible files after the relevant evidence path is exhausted; let the verifier report concrete blockers.
8. If validation has blocking issues, enter repair mode: read the issue list, modify only the affected params/rules, fetch only the targeted evidence required by those issues, then call `validate_organize_recipe_params` again. If validation is accepted but has `review_warnings`, resolve only those warnings; for a long supplemental file warning, call `find_bangumi_targets_for_local_file` with the exact `source_path` from the warning, then validate again. A submit result with `status: "review"` is not final; repair the warning and resubmit. Do not restart broad search, inspect old artifacts/tests, or write prose instead of validating.
9. If validation returns `accepted: true` and `review_warnings` is empty, submit that same accepted recipe next. Do not rewrite an accepted `filename_regex` rule into a longer `exact_paths` rule.
10. If `submit_organize_recipe_params` or `submit_organize_recipe` returns `accepted: true`, call `goal_complete` immediately and do not call more tools. If evidence is insufficient, call `fail_closed` and then `goal_complete`.

Do not search old run artifacts, old `final_result.json` files, or tests to copy an answer. They are not evidence for the current case. Do not finish by printing recipe JSON as plain text; call `validate_organize_recipe_params` or `submit_organize_recipe_params`.

Before `fail_closed`, validate a best-effort params recipe once if you have plausible subject IDs and visible paths. Do not self-report `budget_exhausted`; that is a runner outcome, not a semantic case conclusion.

## Common Params

Use minimal semantic params. Python turns them into the full JSON recipe, escapes source patterns, canonicalizes paths, infers exact episode row type when possible, and fills mechanical defaults.

```json
{
  "rules": [
    {
      "name": "TV episodes",
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
      "exact_paths": ["bonus.mkv"],
      "disposition": "non_bangumi_or_supplemental",
      "reason": "package bonus with no supportable Bangumi episode target"
    }
  ]
}
```

Use `source_pattern` with `{ep}` or `{ep:02}` / `{ep:02d}` for ordinary batch mapping. Use `exact_paths` or `source_path` for a single OVA, SP, movie, or irregular exception. Do not paste one literal filename into `source_pattern`; if there is no `{ep}` token, it is not a sequence locator. For a multi-file sequence, leave `episode_id`, `sort`, and `ep` empty unless every selected file should target one exact episode.

For large packages, avoid listing dozens of obvious supplemental extras as `exact_paths`. Use selector params such as `path_glob` and `filename_regex` for repeated bonus/design/material groups, then use `exact_paths` only for irregular exceptions or the exact long file named by a review warning.

For a one-file movie-shaped subject, you may validate an exact-path rule with `subject_id` and `media_kind: "movie"` without first fetching that subject's `episode_id`. Do this when the subject itself is the movie target. Fetch `get_episode_list` only when the subject has multiple episode rows, the media kind is not movie-shaped, or the verifier asks for a missing episode.

For sequence rules, Python resolves the calculated number against Bangumi `sort` by default. If the local files are numbered `01-13` but the matching Bangumi subject has `sort` continuing from an earlier season while `ep` is `1-13`, set `episode_number_field: "ep"`. Use this only after checking the episode list; it is not a way to force the wrong subject to pass.

If a repeated sequence has changing release tags such as CRC/hash strings, checksum brackets, per-file IDs, audio-track suffixes, or other technical metadata variants, replace those changing parts with a non-episode placeholder such as `{hash}`, `{audio}`, or `{a}` in `source_pattern`. Do not paste the first file's hash or audio suffix into a rule for the whole group.

For one visible file that intentionally covers multiple Bangumi episodes, use `source_unit: "single_file_multi_episode"` with exactly one `exact_paths` or `source_path` value, `subject_id`, `episode_type`, and `episode_range`. Do not map that file to only the first `episode_id`. The verifier accepts this only when local chapter count or duration mechanically supports the episode span.

Supplemental or excluded files must use the enum field `disposition: "non_bangumi_or_supplemental"`. Do not write boolean flags such as `non_bangumi_or_supplemental: true`, `supplemental: true`, or `exclude: true`.

Legal `target.media_kind` values are `tv`, `movie`, `ova`, `oad`, `sp`, `special`, and `unknown`. Legal `target.episode_type` values are `main`, `regular`, `special`, `ova`, `oad`, `movie`, and `unknown`. Keep `media_kind` and `episode_type` separate; a movie-shaped subject can have a `regular` episode row.

## References

- Read `references/recipe-params.md` only when params aliases, selector syntax, raw recipe JSON, or verifier repair details remain unclear after reading repair hints.
- Use `assets/organize_recipe.template.json` as a raw JSON starting shape only when debugging generated recipe JSON.
- Run the local helper only when debugging a schema/selector problem:

```bash
node .pi/skills/organize-recipe-contract/scripts/check-organize-recipe.mjs "<scratch organize_recipe.json>" "<case_input.json>" > "<scratch helper_check.json>"
```
