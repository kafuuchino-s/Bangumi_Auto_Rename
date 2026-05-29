# Organize Recipe Params Reference

Use this file only when `validate_organize_recipe_params` repair hints are not enough.

## Preferred Params Shape

```json
{
  "version": 1,
  "summary": "short case summary",
  "rules": [
    {
      "name": "TV episodes",
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
      "exact_paths": ["Movie.mkv"],
      "subject_id": 67890,
      "media_kind": "movie",
      "episode_id": 123456,
      "disposition": "map_to_bangumi",
      "reason": "exact file title matches the exposed Bangumi episode row"
    },
    {
      "name": "Movie subject",
      "exact_paths": ["Movie.mkv"],
      "subject_id": 67890,
      "media_kind": "movie",
      "disposition": "map_to_bangumi",
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
- Do not paste one literal filename into `source_pattern`. If the rule covers one file and has no `{ep}` token, use `exact_paths`, `source_path`, or `path` instead.
- Other template variables such as `{a}` or `{title}` are allowed for changing text you do not need; Python turns them into wildcard spans while keeping `{ep}` as the episode number.
- Use placeholders for every changing non-episode token in a repeated group. CRC/hash/checksum brackets, per-file IDs, and audio/source variants such as `FLAC` versus `FLACx2` should be `{hash}`, `{crc}`, `{audio}`, or `{a}`, not copied from the first file.
- `source_pattern` may include folder segments, for example `Show [Vol.{vol}]/Episode {ep}.mkv`. Use this for volume folders or other repeated package folders instead of per-file exact paths.
- For repeated supplemental extras, use compact selectors instead of huge `exact_paths` arrays. Example: `{"path_glob":"Show Vol.*/Design Materials/**/*.mkv","filename_regex":".*","disposition":"non_bangumi_or_supplemental"}`. Use exact paths only for irregular exceptions or the long file named by a review warning.
- Use `exact_paths`, `source_path`, or `path` for a single OVA, SP, movie, or irregular exception. Basenames are accepted only when they uniquely identify one visible `source_path`; full visible paths are safest.
- For a one-file movie-shaped subject, `subject_id` plus `media_kind: "movie"` is enough to validate a subject-level movie rule when the subject itself is the movie target. Do not spend one `get_episode_list` call per movie subject only to discover its single regular episode row.
- Use natural params field names. `subject_id` is accepted for the Bangumi subject; `source_template` is accepted as an alias for `source_pattern`; `source_path` and `path` are accepted as one-file `exact_paths` aliases; `range` and `offset` are accepted as aliases for `episode_range` and `episode_offset`; `range_start` plus `range_end` is accepted as a range alias.
- Keep params minimal. Do not include `episode_id: 0`, `sort: null`, `ep: null`, empty `exact_paths`, or empty `exclude_regex`; Python fills mechanical defaults.
- Use `episode_range`, `episode_offset`, and optional `episode_number_field` only when the rule needs episode derivation from `{ep}` or a multi-episode span. If `episode_offset` is omitted or null, Python defaults it to `EP`. If `episode_number_field` is omitted, Python defaults it to `sort`. A one-file exact movie/OVA/SP rule does not need `episode_range`.
- For sequence rules without fixed `episode_id`, the verifier resolves the calculated number against Bangumi episode `sort` first by default. Compare the local file number with the episode list's `sort` and `ep` values before choosing `episode_number_field` or `episode_offset`.
- Use `episode_number_field: "ep"` only when the local file numbers match Bangumi `ep` but not `sort`, such as a later season/cour subject whose `sort` continues from earlier entries while `ep` restarts at 1.
- If a rule maps a local range to a subject but validation reports `missing_target_episode`, do not just change regex syntax. Check whether the subject exposes matching `sort` or `ep` values. If neither field fits, split the local range and use a related season/cour/part subject for the later group.
- Do not automatically subtract a cour/season boundary. If the selected Bangumi number field already matches the local numbering, use `episode_offset: "EP"`. Numeric `offset: 0` is treated as no shift (`EP`); numeric offsets such as `-10` mean `EP-10`. Use arithmetic offsets such as `EP-10` only when the chosen target number field is shifted.
- For a multi-file sequence, leave `episode_id`, `sort`, and `ep` empty/zero unless every selected file should target one exact episode. Python derives target episodes from `{ep}`, `episode_range`, and `episode_offset`.
- For a one-file exact rule with `episode_id`, use the Bangumi row's `episode_type` when you know it. If you omit it or provide the wrong type, the params tool canonicalizes it from the exposed episode row when possible. Do not invent `special` merely because `media_kind` is `movie` or `special`.
- For one visible file that intentionally covers multiple Bangumi episodes, use `source_unit: "single_file_multi_episode"`, exactly one `exact_paths` entry, `subject_id`, `episode_type`, and `episode_range` such as `"1-3"`. Do not include `episode_id`, `sort`, or `ep`; those would collapse the merged file to one target. Python verifies that the range episodes are exposed and that local chapter count or local duration versus target-duration sum mechanically supports the span.
- Do not write boolean source-unit flags such as `multi_episode: true`, `merged: true`, or `single_file_multi_episode: true`. Use the enum field `source_unit`.
- `validate_organize_recipe_params` returns the generated `organize_recipe`; use verifier issues to revise params, not to hand-edit repeated JSON fields.

## Raw Recipe Shape

Use raw recipe JSON only for debugging generated JSON.

```json
{
  "version": 1,
  "summary": "short case summary",
  "rules": [
    {
      "name": "TV episodes",
      "source_unit": "single_file",
      "select": {
        "path_glob": "**/*.mkv",
        "filename_regex": "Episode {ep}.mkv",
        "exact_paths": [],
        "exclude_regex": "(NCOP|NCED|PV|CM|Menu|Trailer)"
      },
      "target": {
        "bangumi_subject_id": 12345,
        "media_kind": "tv",
        "episode_id": 0,
        "episode_type": "regular",
        "sort": null,
        "ep": null
      },
      "episode": {
        "capture": "ep",
        "offset": "EP",
        "range": "1-12",
        "number_field": "sort"
      },
      "disposition": "map_to_bangumi",
      "reason": "why this rule is supported"
    }
  ]
}
```

## Selector Rules

- Use `filename_regex` with `{ep}` or zero-padded `{ep:02}` / `{ep:02d}` for ordinary batch mapping.
- `filename_regex` is a real regular expression with `{ep}` as the episode placeholder. Escape regex metacharacters in literal release names, especially `[`, `]`, `(`, `)`, `.`, `+`, and `?`. Use `{ep}`, `{ep:02}`, or `{ep:02d}`, not Python-style `(?P<ep>...)`, for the episode capture.
- Use `exact_paths` for a single OVA, SP, movie, or irregular exception.
- Use `source_unit: "single_file_multi_episode"` only for one file that really covers two or more Bangumi episodes. It must use exactly one exact path and an `episode.range`; it is not a shortcut for multi-file batches.
- A single-file exact rule with `episode_id` can leave `episode.capture`, `episode.offset`, and `episode.range` at defaults.
- Do not cover a numbered multi-episode sequence with many `exact_paths` unless each rule has a concrete `episode_id`, `sort`, or `ep`. For TV-like `01-13` or `01-26` batches, use one `filename_regex` rule with `{ep}` so the verifier can derive each episode target.
- For a multi-file sequence rule that uses `{ep}`, do not hard-code the first episode target. Set `target.episode_id` to `0`, and set `target.sort` and `target.ep` to `null`; keep only `bangumi_subject_id`, `media_kind`, and the legal `episode_type`. The verifier will resolve each file from `episode.capture`, `episode.offset`, and `episode.range`.
- If validation reports duplicate Bangumi targets for a sequence rule, check whether the rule accidentally fixed `episode_id`, `sort`, or `ep` to the first episode. Remove those fixed locators and validate again.
- Use `disposition: "non_bangumi_or_supplemental"` for visible files that should be covered but not mapped to a Bangumi episode, such as package bonus material, interviews, creditless/theme/promo material that survived filtering, or an ambiguous extra with no clear Bangumi target. Such rules do not need a Bangumi `target`, but they need a plain-language `reason`.
- For repeated supplemental groups, prefer `path_glob` plus `filename_regex` to cover the group compactly. This is especially useful for design-material folders, repeated bonus clips, and other non-episode files that share folder/name structure. Keep separate exact rules for suspicious long files that need targeted evidence.
- Do not write boolean disposition flags such as `non_bangumi_or_supplemental: true`, `supplemental: true`, `exclude: true`, or `unmapped: true`. The params parser rejects them so you can fix the contract error explicitly.
- Keep each rule `reason` short: one clear evidence sentence is usually enough. Do not write a narrative search log in recipe reasons; use `notes.md` only for complex contradictions or fail-closed reasoning.
- Do not use `non_bangumi_or_supplemental` for a numbered `SP01` / `SP02` / `S00E01`-style file when the selected Bangumi subject has a matching special episode by sort/order and no contradictory evidence. Map the numbered file to that special, and handle vague bonus files such as Roman-numeral-only files separately.
- Use `exclude_regex` inside a rule only for extra safety; the case input has already hard-filtered obvious OP/ED/PV/Menu-like noise.
- A rule with zero matches is invalid.
- A source path may be covered by exactly one rule.

## Episode Rules

- Prefer `episode_id` for a one-file rule or a single movie/OVA/SP exception when you have exact episode evidence. For a batch rule with `{ep}`, leave `episode_id` as `0` so the verifier can calculate each target.
- For a one-file movie subject where the subject title is the target, omit `episode_id` and validate the subject-level movie rule first. Add `episode_id` only if validation asks for it or if the subject's episode list is genuinely needed to disambiguate.
- For a merged single file with chapters or a long runtime matching several exposed episode rows, prefer `source_unit: "single_file_multi_episode"` plus `episode_range` over mapping the file to only the first episode.
- Legal `target.media_kind` values are `tv`, `movie`, `ova`, `oad`, `sp`, `special`, and `unknown`. Do not use raw source/API words such as `web`; choose the closest legal kind, or use `unknown` if the kind is not important to the mapping.
- Legal `target.episode_type` values are `main`, `regular`, `special`, `ova`, `oad`, `movie`, and `unknown`.
- Do not use raw API words like `episode` in `target.episode_type`; use `regular` for normal TV episodes.
- Keep `media_kind` and `episode_type` separate. `media_kind` says how this local item should be organized; `episode_type` says how the Bangumi episode row is typed. A movie-shaped subject can have a `regular` episode row.
- For ordinary sequences, use legal `episode_type` plus `offset`.
- `offset` may use `EP`, integers, `+`, `-`, `*`, unary signs, and parentheses, for example `EP`, `EP-10`, or `EP*2-1`.
- `number_field` may be `sort` or `ep`. Keep the default `sort` unless the episode list shows that local filenames match Bangumi `ep` while `sort` continues from another season/cour.
- When validation reports `missing_target_episode` for a sequence that otherwise selected the right subject, inspect `get_episode_list` for that subject and compare local file numbers to Bangumi `sort` and `ep`. If local numbers match `sort`, keep `number_field: "sort"` and `offset: "EP"`; if local numbers match `ep`, set `number_field: "ep"`; if the correct field is shifted, use an arithmetic offset.
- Accepted recipes cannot contain `needs_more_evidence` or `unaligned_fail_closed`; use `fail_closed` when the whole case cannot be resolved.
