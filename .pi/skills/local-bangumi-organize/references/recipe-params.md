# Organize Recipe Params Reference

Use this file only when `validate_organize_recipe_params` repair hints are not enough.

## Low-Friction Group Decisions

Prefer `upsert_recipe_group_decision_one` while exploring. It saves one Pi-owned judgment and Python compiles it into `recipe_params_draft`. Do not wait to assemble the whole case.

Tool call arguments are part of model output. Keep decisions compact: short names, short reasons, `group_ref` selectors, and `file_numbers` / `file_number_range` / `path_contains` for numbered subclusters before long `exact_paths`.

```json
{
  "decisions": [
    {
      "name": "LG6 side shorts",
      "group_ref": "LG6",
      "file_number_range": "1-13",
      "exclude_path_contains": ["Variant-B"],
      "subject_id": 234089,
      "media_kind": "sp",
      "episode_type": "regular",
      "reason": "local side shorts match the exposed 1-13 rows"
    },
    {
      "name": "LG6 duplicate variant",
      "group_ref": "LG6",
      "path_contains": ["Variant-B"],
      "disposition": "non_bangumi_or_supplemental",
      "reason": "duplicate variant with no distinct Bangumi row"
    },
    {
      "name": "LG9 exact specials",
      "group_ref": "LG9",
      "file_numbers": [1, 2],
      "episode_ids": [1206551, 1206552],
      "subject_id": 193953,
      "media_kind": "sp",
      "reason": "numbered long files match exposed special rows"
    }
  ]
}
```

Decision selectors are mechanical: `group_ref`, `file_numbers`, `file_number_range`, `path_contains`, `exclude_path_contains`, and `exact_paths` only select local files. They do not decide the Bangumi target.

Use a group decision only when the selected local files share the same Bangumi target surface. If one local group contains two named movies, a mini-anime run plus a duplicate variant, or numbered SP files plus theater-manner extras, write separate decisions for those subclusters. The split is still Pi-owned semantics; Python only expands the selectors you provide.

## Preferred Params Shape

```json
{
  "version": 1,
  "summary": "short case summary",
  "rules": [
    {
      "name": "TV episodes",
      "group_ref": "LG1",
      "subject_id": 12345,
      "media_kind": "tv",
      "episode_type": "regular",
      "disposition": "map_to_bangumi",
      "reason": "local group maps to this Bangumi episode run"
    },
    {
      "name": "TV explicit selector",
      "source_pattern": "Episode {ep}.mkv",
      "subject_id": 12345,
      "media_kind": "tv",
      "episode_type": "regular",
      "episode_number_field": "sort",
      "episode_offset": "EP",
      "episode_range": "1-12",
      "disposition": "map_to_bangumi",
      "reason": "why this rule is supported"
    },
    {
      "name": "Movie or special",
      "group_ref": "LG4",
      "file_numbers": [1],
      "subject_id": 67890,
      "media_kind": "movie",
      "episode_id": 123456,
      "disposition": "map_to_bangumi",
      "reason": "title matches exposed row"
    },
    {
      "name": "Movie subject",
      "group_ref": "LG4",
      "file_numbers": [2],
      "subject_id": 67890,
      "media_kind": "movie",
      "disposition": "map_to_bangumi",
      "reason": "title matches one-movie subject"
    },
    {
      "name": "Merged episode file",
      "source_unit": "single_file_multi_episode",
      "exact_paths": ["Merged OVA.mkv"],
      "subject_id": 24680,
      "media_kind": "ova",
      "episode_type": "regular",
      "episode_range": "1-3",
      "disposition": "map_to_bangumi",
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

## Params Rules

- Use `source_pattern` with `{ep}` for ordinary batch mapping, or `{ep:02}` / `{ep:02d}` when file names use zero-padded numbers such as `01`. Write it like a literal file-name template; Python escapes regex characters such as `[`, `]`, `(`, `)`, `.`, `+`, and `?`.
- Use `group_ref` when a local group card already expresses the selector you need. It expands only local selector/range facts from `list_local_groups` / `get_local_selector_scaffold`; it does not choose `subject_id`, `episode_id`, `media_kind`, `episode_type`, or supplemental disposition.
- For a complete ordinary group, prefer `group_ref` alone. Do not copy a full release filename into `source_pattern` just to restate the group; codec/audio/hash tokens often vary and will leave files uncovered. Combine `group_ref + source_pattern` only for an explicit side-folder/subcluster template; without `file_numbers`, `file_number_range`, `path_contains`, `exclude_path_contains`, or `exact_paths`, that pattern must match the whole group. If it only matches part of the group, either remove it and use `group_ref` alone, or add an explicit subcluster selector.
- For numbered one-file movies, OVA/OAD/SP files, or mixed-folder subclusters, prefer `group_ref` plus `file_numbers`, `file_number_range`, `path_contains`, or `exclude_path_contains` before listing full `exact_paths`.
- Do not paste one literal filename into `source_pattern`. If the rule covers one file and has no `{ep}` token, use `exact_paths` instead.
- Other template variables such as `{a}` or `{title}` are allowed for changing text you do not need; Python turns them into wildcard spans while keeping `{ep}` as the episode number. For a normal group, `group_ref` alone is safer than hand-writing a codec-sensitive template.
- Use placeholders for every changing non-episode token in a repeated group. CRC/hash/checksum brackets, per-file IDs, and audio/source variants such as `FLAC` versus `FLACx2` should be `{hash}`, `{crc}`, `{audio}`, or `{a}`, not copied from the first file.
- `source_pattern` may include folder segments, for example `Show [Vol.{vol}]/Episode {ep}.mkv`. Use this for volume folders or other repeated package folders instead of per-file exact paths.
- For repeated supplemental extras, use compact canonical selectors such as `group_ref`, `path_contains`, `exclude_path_contains`, `source_pattern`, or `filename_regex` instead of huge `exact_paths` arrays. Use exact paths only for irregular exceptions or the long file named by a review warning.
- Use `exact_paths` for a single OVA, SP, movie, or irregular exception only when a compact group selector is not safe. Basenames are accepted only when they uniquely identify one visible `source_path`; full visible paths are safest.
- For a small irregular ordered sequence where a compact `source_pattern` would be awkward, multi `exact_paths` plus `episode_range` maps paths in listed order when the exact path count equals the episode range count. Prefer `source_pattern` or `group_ref` for ordinary large numbered batches.
- For a one-file movie-shaped subject, `subject_id` plus `media_kind: "movie"` is enough to validate a subject-level movie rule when the subject itself is the movie target. Do not spend one `get_episode_list` call per movie subject only to discover its single regular episode row.
- Use only canonical params field names. Allowed rule fields are `group_ref`, `file_numbers`, `file_number_range`, `path_contains`, `exclude_path_contains`, `exact_paths`, `source_pattern`, `filename_regex`, `exclude_regex`, `subject_id`, `media_kind`, `episode_id`, `episode_ids`, `episode_type`, `sort`, `ep`, `source_unit`, `episode_range`, `episode_range_start`, `episode_range_end`, `episode_offset`, `episode_number_field`, `name`, `disposition`, and `reason`.
- Do not use aliases or raw nested shapes: `source_path`, `path`, `paths`, `source_paths`, `source_template`, `range`, `offset`, `bangumi_subject_id`, `target_subject_id`, nested `select`/`target`/`episode`, plural subject fields, and boolean flags are rejected.
- `episode_range` must be a compact string such as `"1-13"` or `"1,3,5"`; do not pass arrays such as `[1,13]`. If you want start/end fields, write `episode_range_start` and `episode_range_end`.
- Keep params minimal. Do not include `episode_id: 0`, `sort: null`, `ep: null`, empty `exact_paths`, or empty `exclude_regex`; Python fills mechanical defaults.
- Incremental draft rows are real compact params rows, not placeholders. Do not save a row with only `name` or only a local selector; add a Bangumi target or `disposition: "non_bangumi_or_supplemental"`, or leave the row out until it is testable.
- Use `episode_range`, `episode_offset`, and optional `episode_number_field` only when the rule needs episode derivation from `{ep}` or a multi-episode span. If `episode_offset` is omitted or null, Python defaults it to `EP`. If `episode_number_field` is omitted, Python defaults it to `sort`. A one-file exact movie/OVA/SP rule does not need `episode_range`.
- For sequence rules without fixed `episode_id`, the verifier resolves the calculated number against Bangumi episode `sort` first by default. Compare the local file number with the episode list's `sort` and `ep` values before choosing `episode_number_field` or `episode_offset`.
- Use `episode_number_field: "ep"` only when the local file numbers match Bangumi `ep` but not `sort`, such as a later season/cour subject whose `sort` continues from earlier entries while `ep` restarts at 1.
- If a rule maps a local range to a subject but validation reports `missing_target_episode`, do not just change regex syntax. Check whether the subject exposes matching `sort` or `ep` values. If neither field fits, split the local range and use a related season/cour/part subject for the later group.
- Do not automatically subtract a cour/season boundary. If the selected Bangumi number field already matches the local numbering, use `episode_offset: "EP"`. Write arithmetic offsets as strings such as `EP-10`; numeric offset aliases are rejected.
- For a multi-file sequence, leave `episode_id`, `sort`, and `ep` empty/zero unless every selected file should target one exact episode. Python derives target episodes from `{ep}`, `episode_range`, and `episode_offset`.
- For a one-file exact rule with `episode_id`, use the Bangumi row's `episode_type` when you know it. If you omit it or provide the wrong type, the params tool canonicalizes it from the exposed episode row when possible. Do not invent `special` merely because `media_kind` is `movie` or `special`.
- Do not delay an exact mapped draft just because `media_kind` or `episode_type` feels imperfect. If the exact `episode_id` and local path/subset are clear, save the compact row and validate; the params tool and verifier will report the mechanical correction without changing your semantic target choice.
- For one visible file that intentionally covers multiple Bangumi episodes, use `source_unit: "single_file_multi_episode"`, exactly one `exact_paths` entry, `subject_id`, `episode_type`, and `episode_range` such as `"1-3"`. Do not include `episode_id`, `sort`, or `ep`; those would collapse the merged file to one target. Python verifies that the range episodes are exposed and that local chapter count or local duration versus target-duration sum mechanically supports the span.
- Do not use `source_unit: "single_file_multi_episode"` for a single unnumbered file only because its title resembles a short-series subject. Without chapters, an explicit filename range, or duration close to the target row-duration sum, use a supported one-episode OVA/OAD/special/movie target or supplemental judgment instead.
- Do not write boolean source-unit flags such as `multi_episode: true`, `merged: true`, or `single_file_multi_episode: true`. Use the enum field `source_unit`.
- Transaction note fields are strict small envelopes, not arbitrary JSON. Use `board_delta`/`content` with `summary`, `observations`, `blockers`, `next_action`; `validation_snapshot` with `summary`, `accepted_scope`, `open_issues`, `next_action`; `patch_delta` with `summary`, `changed_rules`, `evidence_refs`, `reason`; and `submit_snapshot` with `summary`, `accepted_rule_count`, `review_notes`.
- `validate_organize_recipe_params` returns the generated `organize_recipe`; it is a trial check and does not finalize the case. Use verifier issues, repair hints, and review warnings to revise params, not to hand-edit repeated JSON fields.
- After a params validation, `validate_organize_recipe_params_patch` can repair only the changed rules from the latest params. Before the first params validation, prefer `upsert_recipe_params_draft`; if the patch tool is used anyway, the same patch shape updates `recipe_params_draft` and returns coverage preview without running verifier. Use `patch_rules` for top-level rule field updates/removals, `append_rules` for new exact supplemental exceptions, `replace_rules` when a rule shape changes, and `remove_rule_names` when a bad rule should disappear. Do not put the same rule name in `remove_rule_names` and `patch_rules`/`replace_rules`; choose either update/replace, or remove the old row and append replacement rows. A new supplemental rule for a split variant or side-folder duplicate is an `append_rules` row, not a `patch_rules` update.
- `patch_rules.updates` is a partial update against the named existing rule. `unset` is a sibling of `updates` inside the patch rule entry, for example `{"name":"TV run","updates":{"episode_range":"1-12"},"unset":["episode_id","sort","ep"]}`. If the existing rule already has `group_ref`, a numbered selector update such as `file_numbers` may inherit it; when splitting one broad rule into multiple new names, use `remove_rule_names` for the old rule plus `append_rules` for the replacement rows.
- `append_rules` names must not already exist unless the old name is also removed in the same patch. If the row should keep its existing name, use `patch_rules` or `replace_rules` instead. Keep `patch_delta` top-level beside `recipe_params_patch`; never place it inside `recipe_params_patch`.
- `review_resolutions` is a Pi-owned structured judgment on a supplemental rule. Use it only after validation returns candidate-row review warnings for the same `source_path`. Shape: `{"source_path":"...","candidate_episode_ids":[123,456],"decision":"candidate_rows_not_supportable","reason":"..."}`. Candidate IDs must come from `review_resolution_candidate_episode_ids` when present; compact warning rows may show only a sample. Python only checks that reference, not whether the semantic contradiction is correct.

Example params patch:

```json
{
  "patch_rules": [
    {"name": "TV specials", "updates": {"exclude_regex": "Variant-B"}, "unset": ["episode_id"]},
    {"name": "Bonus extras", "updates": {"review_resolutions": [{"source_path": "SPs/Bonus.mkv", "candidate_episode_ids": [12345], "decision": "candidate_rows_not_supportable", "reason": "candidate row is a creditless OP while the file is a release bonus reel"}]}}
  ],
  "append_rules": [
    {"name": "Duplicate variant", "exact_paths": ["Variant-B.mkv"], "disposition": "non_bangumi_or_supplemental", "reason": "duplicate package segment with no distinct Bangumi target"}
  ]
}
```

## Selector Rules

- Use `filename_regex` with `{ep}` or zero-padded `{ep:02}` / `{ep:02d}` for ordinary batch mapping.
- `filename_regex` is a real regular expression with `{ep}` as the episode placeholder. Escape regex metacharacters in literal release names, especially `[`, `]`, `(`, `)`, `.`, `+`, and `?`. Use `{ep}`, `{ep:02}`, or `{ep:02d}`, not Python-style `(?P<ep>...)`, for the episode capture.
- Use `group_ref + file_numbers/file_number_range/path_contains` for a single numbered OVA, SP, movie, or irregular exception when safe; use `exact_paths` only when the compact selector cannot name the files unambiguously.
- Use `source_unit: "single_file_multi_episode"` only for one file that really covers two or more Bangumi episodes. It must use exactly one exact path and an `episode_range`; it is not a shortcut for multi-file batches.
- A single-file exact rule with `episode_id` can leave `episode_offset` and `episode_range` at defaults.
- Do not cover an ordinary large numbered sequence with many `exact_paths` when one `filename_regex` rule with `{ep}` or one `group_ref` can express it. For small irregular ordered sequences, multi `exact_paths` plus `episode_range` is valid when the listed paths and range have the same count.
- For a multi-file sequence rule that uses `{ep}`, do not hard-code the first episode target. Omit `episode_id`, `sort`, and `ep`; keep only `subject_id`, `media_kind`, and the legal `episode_type`. The verifier will resolve each file from `source_pattern`, `episode_offset`, `episode_range`, and `episode_number_field`.
- If validation reports duplicate Bangumi targets for a sequence rule, check whether the rule accidentally fixed `episode_id`, `sort`, or `ep` to the first episode. Remove those fixed locators and validate again.
- Use `disposition: "non_bangumi_or_supplemental"` for visible files that should be covered but not mapped to a Bangumi episode, such as package bonus material, interviews, creditless/theme/promo material that survived filtering, or an ambiguous extra with no clear Bangumi target. Such rules do not need a Bangumi `target`, but they need a plain-language `reason`.
- For repeated supplemental groups, prefer canonical compact selectors such as `group_ref`, `path_contains`, `exclude_path_contains`, `source_pattern`, or `filename_regex`. This is especially useful for design-material folders, repeated bonus clips, and other non-episode files that share folder/name structure. Keep separate exact rules for suspicious long files that need targeted evidence.
- Do not write boolean disposition flags such as `non_bangumi_or_supplemental: true`, `supplemental: true`, `exclude: true`, or `unmapped: true`. The params parser rejects them so you can fix the contract error explicitly.
- Keep each rule `reason` short: one clear evidence sentence is usually enough. Do not write a narrative search log in recipe reasons; use `notes.md` only for complex contradictions or fail-closed reasoning.
- If an OVA/OAD/SP/movie/side-story rule has plausible Bangumi target evidence but validation rejects the rule, repair the mapped target fields or selector first. Do not change it to `non_bangumi_or_supplemental` merely to make the verifier pass.
- Before keeping a numbered SP/OVA/OAD/movie-like visible file supplemental, use `find_bangumi_targets_for_local_file` on the exact `source_path` or one representative path in a uniform sequence. Treat returned `duration_candidate_episode_rows` as fact rows for Pi to judge, not fixed-layer recommendations. Candidate rows include `ordinal_alignment` between the local group title and candidate subject title; use it as evidence when choosing among same-duration sequel/side subjects. A different `subject_id` is not by itself a contradiction for side/SP/OVA/movie-bundle extras; require concrete relation/title/duration/locator mismatch evidence before recording `candidate_rows_not_supportable`. If validation returns a numbered supplemental sequence review warning, run that representative lookup and validate again. If the fact check exposes a supportable target row, map it; if not, put the exhausted-target reason in the supplemental rule.
- Do not use `non_bangumi_or_supplemental` for a numbered `SP01` / `SP02` / `S00E01`-style group when same-title or related Bangumi rows match by sort/order/title/count and no contradictory evidence exists. Map the numbered group to those rows, and handle vague bonus files such as Roman-numeral-only files separately.
- A parent TV subject that lacks SP rows is not enough to make a named side-content group supplemental. Check the local side title itself, such as mini-anime, chibi short, OAD, OVA, Bangaihen, or a named special, against same-title search/related subjects and their episode rows.
- The parent TV subject's regular rows are not side-folder target evidence. If `SP01-SPnn` files would map to the same regular episodes as the main files, look for a related side subject or explicit side/special rows; if none are supportable after targeted closure, make the side group supplemental instead of duplicating the parent regular rows.
- A related subject is not used up after one rule. If its regular rows explain one local group and its special/OVA/movie-like rows explain another local subcluster, write separate mapped rules for both surfaces.
- A local group can split into multiple params rules. Use exact mapped rules for files whose title/order/duration match exposed special or movie-like rows, then cover only the unmatched files with supplemental rules.
- A movie or recap pair is not automatically one sequence. If Bangumi exposes separate movie subjects or one-row movie subjects, use exact mapped rules for each visible movie file. Use a group-level movie sequence only when one subject exposes multiple legal rows matching the local files.
- A mixed `SPs` folder is not fully supplemental just because some files are theater-manner clips, menus, promos, or bonus-shaped. Apply the supplemental reason only to the exact files or selector still unmatched after target-surface closure.
- If a mixed `SPs` folder belongs to a movie/recap package, do not make numbered SP files inherit the main movie target by folder title alone. Main movie exact files and side-folder SP files need distinct exposed rows; if validation says they duplicate the same target, check related side/special rows before making the side files supplemental.
- Do not call a side-folder SP file a duplicate of a main movie/episode unless its title, duration, and content shape fit that same target surface. A duration/title mismatch is a recheck signal for distinct special/OVA/movie-like rows, not automatic supplemental proof.
- If a side subject has already been used for a regular side sequence, its special/OVA/movie-like rows can still support exact files in another local folder. A regular-only episode view is not enough evidence to close that subject for numbered SP files.
- If targeted side-title evidence or a targeted episode-list/window check still does not expose matching rows, cover the affected SP/bonus group as supplemental with a short evidence-gap reason instead of forcing it onto non-existent rows.
- Use `exclude_regex` inside a rule only for extra safety; the case input has already hard-filtered obvious OP/ED/PV/Menu-like noise.
- A rule with zero matches is invalid.
- A source path may be covered by exactly one rule.

## Episode Rules

- Prefer `episode_id` for a one-file rule or a single movie/OVA/SP exception when you have exact episode evidence. For a batch rule with `{ep}`, omit `episode_id` so the verifier can calculate each target.
- For a one-file movie subject where the subject title is the target, omit `episode_id` and validate the subject-level movie rule first. Add `episode_id` only if validation asks for it or if the subject's episode list is genuinely needed to disambiguate.
- For a merged single file with chapters or a long runtime matching several exposed episode rows, prefer `source_unit: "single_file_multi_episode"` plus `episode_range` over mapping the file to only the first episode.
- Legal `media_kind` values are `tv`, `movie`, `ova`, `oad`, `sp`, `special`, and `unknown`. Do not use raw source/API words such as `web` or `anime`; choose the closest legal kind, or use `unknown` if the kind is not important to the mapping.
- Legal `episode_type` values are `main`, `regular`, `special`, `ova`, `oad`, `movie`, and `unknown`.
- Do not use raw API words like `episode` in `episode_type`; use `regular` for normal TV episodes.
- Keep `media_kind` and `episode_type` separate. `media_kind` says how this local item should be organized; `episode_type` says how the Bangumi episode row is typed. A movie-shaped subject can have a `regular` episode row.
- For ordinary sequences, use legal `episode_type` plus `episode_offset`.
- `episode_offset` may use `EP`, `+`, `-`, `*`, unary signs, and parentheses, for example `EP`, `EP-10`, or `EP*2-1`.
- `episode_number_field` may be `sort` or `ep`. Keep the default `sort` unless the episode list shows that local filenames match Bangumi `ep` while `sort` continues from another season/cour.
- When validation reports `missing_target_episode` for a sequence that otherwise selected the right subject, inspect `get_episode_list` for that subject and compare local file numbers to Bangumi `sort` and `ep`. If local numbers match `sort`, keep `episode_number_field: "sort"` and `episode_offset: "EP"`; if local numbers match `ep`, set `episode_number_field: "ep"`; if the correct field is shifted, use an arithmetic offset.
- Pi-facing params and decision tools do not accept `needs_more_evidence` or `unaligned_fail_closed`. Keep unresolved evidence gaps on the Case Board, save only mapped/supplemental rows, or use `fail_closed` when the whole case cannot be resolved.
