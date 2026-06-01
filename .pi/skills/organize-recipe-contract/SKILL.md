---
name: organize-recipe-contract
description: Use when recipe params, selectors, helper scripts, or verifier repair remain unclear after a validate/submit issue; not needed for an ordinary first draft.
---

# Organize Recipe Contract

The final output is a Python-verifier accepted `OrganizeRecipeDraft` submitted with `submit_organize_recipe`, or a safe `fail_closed`.

Use real identifiers in recipes. Local identity is the real `source_path` from `case_input.visible_source_paths` or `case_input.context.local_files`; `task_source_path` is only the original task/sample path. Bangumi identity is `subject_id`, `episode_id`, legal recipe `episode_type`, `sort`, and `ep`.

The recipe verifier is a strict mechanical gate, not a semantic title matcher. It can tell you that paths are covered and target IDs are exposed; it cannot prove that the selected subject is the correct season, movie, OVA, or franchise entry. Resolve that with Bangumi subject evidence before final submit.

Core loop: build a testable recipe first, then let validation drive repair. Bangumi search should expose enough subject/episode evidence for a compact params draft; it does not need exhaustive certainty before the first validation. A practical pre-validation evidence set is a representative search/lookup for active groups, one bounded graph when it helps, and episode lists/windows only for subjects you plan to use in params. `run_progress` reports progress facts such as evidence-call counts, validation_seen, and verifier_feedback_available; it is not a target recommendation or next-step instruction. Frontier exhaustion is for final `fail_closed` or final supplemental reasoning, not a prerequisite for calling `validate_organize_recipe_params`.

`validate_organize_recipe_params` is a trial check, not final submission. It writes artifacts and returns verifier issues, repair hints, or review warnings without finishing the case. A first trial validation does not need to be accepted or warning-free. An invalid or review result is useful feedback for patch repair. Only `submit_organize_recipe_params` or `submit_organize_recipe` finalizes an accepted recipe.

`get_local_recipe_params_scaffold` can lower selector friction. It returns local selector/range stubs copied from `local_recipe_skeleton`; it does not choose Bangumi target IDs, media kind, episode type, disposition, or supplemental status. Fill those fields from Bangumi evidence before validating.

## Workflow

1. Read `case_input.json` and inspect `case_input.scratch_paths`.
2. Read `case_input.local_structure_summary` and visible `source_path` values first. `case_input.local_recipe_skeleton` is a selector and verifier-repair aid; use it after choosing a local group or when verifier issues name uncovered paths. Do not treat it as a startup semantic checklist or Bangumi candidate recommendation.
3. Inspect the visible `source_path` values and infer local groups from folders, repeated title prefixes, season/movie/OVA/SP words, and numbering runs before searching Bangumi. The summary is a factual grouping aid, not a target decision.
4. Use Bangumi tools to expose subject and episode evidence by ID. A representative `source_path` lookup is a compact evidence call; it does not choose a target or generate recipe JSON. Prefer enough high-signal evidence to draft compact params, then validate before chasing every bonus group. A related Bangumi special/OVA subject is candidate evidence only; short SP/bonus folders need exposed episode rows before they are mapped, but unresolved bonus-like groups should not block the first validation.
5. Prefer params tools: trial-check semantic params with `validate_organize_recipe_params`, repair feedback if needed, then submit the same params with `submit_organize_recipe_params` when accepted and `review_warnings` is empty. Raw `validate_organize_recipe` is for debugging generated JSON.
6. For repeated local groups, copy a safe `selector_hint.source_pattern` from `case_input.local_recipe_skeleton` or the matching `params_rule_stub` from `get_local_recipe_params_scaffold` instead of hand-writing a regex-like selector. In sequence params, `episode_range` is the local captured file-number range; if local files `27-33` map to Bangumi rows `1-7`, use `episode_range: "27-33"` plus `episode_offset: "EP-26"`.
7. Validation can be a repair checkpoint even when the first compact params are imperfect. It can fetch episode evidence for subject IDs already declared in your params or recipe, then run the strict mechanical verifier and return concrete issues.
8. After a representative lookup or bounded graph/episode check for the active groups, validation is usually more useful than continuing broad search. When `run_progress.verifier_feedback_available` is false, the factual state is simply that the verifier loop has not produced feedback yet; interpret that fact with the case evidence rather than as a fixed-layer decision. If a named one-file group has exact `episode_id` evidence, it is ready for a mapped-rule validation.
9. Use `disposition: "non_bangumi_or_supplemental"` for bonus-like visible files when the current evidence does not support a Bangumi target; let verifier issues or review warnings ask for concrete follow-up. If validation reports `missing_target_episode` for a `special_or_bonus_candidate` group and targeted episode evidence still does not expose matching rows, repair by converting that affected group to supplemental instead of broad-searching the franchise again.
10. If validation has blocking issues, repair mode should stay targeted: read the issue list, modify the affected params/rules, fetch only evidence requested by those issues, then validate again. For `duplicate_target` caused by local split/variant locators such as `_1`/`_2`, part markers, or version suffixes, either assign distinct exposed Bangumi rows or exclude only those paths from the mapped sequence and cover them with supplemental exact paths; validate that patch before considering whole-case `fail_closed`. If validation is accepted but has `review_warnings`, resolve those warnings; for a long or named supplemental file warning, a targeted `find_bangumi_targets_for_local_file` lookup with the exact `source_path` from the warning is the right evidence path. A submit result with `status: "review"` is not final; repair the warning and resubmit rather than restarting broad search, inspecting old artifacts/tests, or writing prose instead of validating.
11. After a params validation, use `validate_organize_recipe_params_patch` for small repairs instead of reconstructing the whole params object. Patch named rules with `patch_rules`, add new exact supplemental rules with `append_rules`, or remove a bad rule with `remove_rule_names`; then submit with the same accepted params or `submit_organize_recipe_params_patch`.
12. If validation returns `accepted: true` and `review_warnings` is empty, submit that same accepted recipe. Keep accepted compact selectors such as `filename_regex` unless a verifier issue asks for a different shape.
13. If `submit_organize_recipe_params` or `submit_organize_recipe` returns `accepted: true`, the case is ready for `goal_complete`. If evidence is insufficient, finish with `fail_closed` and then `goal_complete`.

Old run artifacts, old `final_result.json` files, and tests are not evidence for the current case. Finish through `validate_organize_recipe_params`, `submit_organize_recipe_params`, or the raw submit tools rather than by printing recipe JSON as plain text.

Before `fail_closed`, a best-effort params validation is useful when plausible subject IDs and visible paths exist. `budget_exhausted` is a runner outcome, not a semantic case conclusion.

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

Use `source_pattern` with `{ep}` or `{ep:02}` / `{ep:02d}` for ordinary batch mapping. Use `exact_paths` or `source_path` for a single OVA, SP, movie, or irregular exception. A literal filename without an `{ep}` token is not a sequence locator. For a multi-file sequence, leave `episode_id`, `sort`, and `ep` empty unless every selected file should target one exact episode.

For large packages, avoid listing dozens of obvious supplemental extras as `exact_paths`. Use selector params such as `path_glob` and `filename_regex` for repeated bonus/design/material groups, then use `exact_paths` only for irregular exceptions or the exact long file named by a review warning.

For short SP/bonus folders, a Bangumi related subject or similar title is not enough by itself. Map the group only when the subject exposes compatible rows by sort/ep/title/count. Do not hold the entire case waiting for exhaustive SP certainty: validate the supportable recipe, then use verifier issues or review warnings to decide whether the affected SP/bonus group needs targeted episode evidence, mapping, or `disposition: "non_bangumi_or_supplemental"` with a short evidence-gap reason.

For a one-file movie-shaped subject, an exact-path rule with `subject_id` and `media_kind: "movie"` can validate without first fetching that subject's `episode_id` when the subject itself is the movie target. `get_episode_list` is most useful when the subject has multiple episode rows, the media kind is not movie-shaped, or the verifier asks for a missing episode.

For sequence rules, Python resolves the calculated number against Bangumi `sort` by default. If the local files are numbered `01-13` but the matching Bangumi subject has `sort` continuing from an earlier season while `ep` is `1-13`, `episode_number_field: "ep"` expresses that evidence after checking the episode list. It is not a way to force the wrong subject to pass.

If a repeated sequence has changing release tags such as CRC/hash strings, checksum brackets, per-file IDs, audio-track suffixes, or other technical metadata variants, represent those changing parts with a non-episode placeholder such as `{hash}`, `{audio}`, or `{a}` in `source_pattern` rather than pasting the first file's hash or audio suffix into a rule for the whole group.

For one visible file that intentionally covers multiple Bangumi episodes, use `source_unit: "single_file_multi_episode"` with exactly one `exact_paths` or `source_path` value, `subject_id`, `episode_type`, and `episode_range`. Mapping that file only to the first `episode_id` loses the span evidence. The verifier accepts this only when local chapter count or duration mechanically supports the episode span.

Supplemental or excluded files use the enum field `disposition: "non_bangumi_or_supplemental"`. Boolean flags such as `non_bangumi_or_supplemental: true`, `supplemental: true`, or `exclude: true` are not part of the params contract.

Legal `target.media_kind` values are `tv`, `movie`, `ova`, `oad`, `sp`, `special`, and `unknown`. Legal `target.episode_type` values are `main`, `regular`, `special`, `ova`, `oad`, `movie`, and `unknown`. Keep `media_kind` and `episode_type` separate; a movie-shaped subject can have a `regular` episode row.

Patch repair shape after a params validation:

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

Use `validate_organize_recipe_params_patch` when only a few existing rules changed; it reuses the latest params from the previous params validate/submit.

## References

- `references/recipe-params.md` is most useful when params aliases, selector syntax, raw recipe JSON, or verifier repair details remain unclear after reading repair hints.
- `assets/organize_recipe.template.json` is a raw JSON starting shape for debugging generated recipe JSON.
- The local helper is useful for debugging a schema/selector problem:

```bash
node .pi/skills/organize-recipe-contract/scripts/check-organize-recipe.mjs "<scratch organize_recipe.json>" "<case_input.json>" > "<scratch helper_check.json>"
```
