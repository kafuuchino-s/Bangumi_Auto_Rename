---
name: subtitle-mapping-contract
description: Use when mapping subtitle files from an archive to already-landed target videos via the subtitle Case Agent, especially when ref policy, disposition semantics, or verifier repair is unclear.
---

# Subtitle Mapping Contract

The final output for this stage is a Python-verifier-accepted subtitle mapping plan submitted with `submit_subtitle_mapping`, or a safe `fail_closed` result for global ambiguity. This stage is dry-run only: no moving, copying, linking, renaming, or writing media/subtitle files.

The fixed layer presents two flat fact catalogs via `get_subtitle_mapping_context`:

- **subtitle files** (`SF<idx>` short refs): `archive_path`, `filename`, `language_hint` (raw tag extracted from the filename, e.g. `chs`/`cht`/`jpn`/`eng`; may be empty).
- **target videos** (`TV<idx>` short refs): `task_uuid`, `task_title`, `season`, `is_movie`, `video` (the exact landed video filename), `target_dir`, `task_video_count`.

These short refs are the only identifiers the mapping draft may use. Archive paths, task UUIDs, and video filenames are evidence for your reasoning; they are **not** target IDs in the draft. Copy the `SF<idx>` / `TV<idx>` refs exactly.

## Mapping draft shape

```json
{
  "summary": "short reason line",
  "confidence": "High",
  "rows": [
    {
      "row_ref": "R1",
      "subtitle_ref": "SF1",
      "disposition": "map_to_video",
      "target_ref": "TV3",
      "language": "chs",
      "reason": "episode 1 simplified Chinese subtitle"
    },
    {
      "row_ref": "R2",
      "subtitle_ref": "SF2",
      "disposition": "unmatched",
      "reason": "no matching video in any target task"
    }
  ]
}
```

## Disposition semantics

- `map_to_video`: pair the subtitle with a target video. Requires `target_ref` (a `TV<idx>` ref) and a `language` tag (`chs`/`cht`/`jpn`/`eng`/`ko`/...). The fixed layer normalizes the raw tag to an Emby language code.
- `unmatched`: the subtitle has no confident target. Requires a concrete `reason`. Must **not** carry a `target_ref`. This is a valid,合格 result for that subtitle; do not fail the whole case for one unmatched subtitle.
- `needs_more_evidence`: still investigating. Must **not** carry a `target_ref`. Blocks accepted readiness; you must resolve every `needs_more_evidence` row to `map_to_video` or `unmatched` before submitting.

## Coverage contract (verifier)

- Every subtitle must appear **exactly once** as `map_to_video` or `unmatched`. `rows` count (minus `needs_more_evidence`) must equal subtitle count.
- A subtitle ref may appear only once (no duplicate source).
- The same target video may carry multiple subtitles **only if their languages differ** (e.g. one `chs` + one `cht` for bilingual). Same target + same language is a conflict.
- `map_to_video` rows require a non-empty `language`; empty language is rejected.
- `target_ref` must be one of the fixed-layer `TV<idx>` refs; unknown or mis-shaped refs are rejected.

## Workflow

1. `get_subtitle_mapping_context` — read the `SF<idx>` subtitle cards and `TV<idx>` target video cards. Note `language_hint` per subtitle and `task_video_count` per task (a movie task with one video pairs any of its subtitles to that single video).
2. Draft a `mapping_draft` covering **every** subtitle. Use folder structure, filenames, language tags, and the target video filenames/seasons to decide which `TV<idx>` each `SF<idx>` maps to. For multi-season archives, folder names (`S1/`, `S2/`, ...) and the target task `season` field disambiguate which task a subtitle belongs to.
3. `validate_subtitle_mapping` — verify without finishing. Read the returned `repair_hints` and verifier `issues`; patch only the named problems.
4. After `validate_subtitle_mapping` returns `accepted=true`, `submit_subtitle_mapping` with the same draft, then `goal_complete`.

## Language inference

Use raw language tags in the draft. If a subtitle filename carries no language tag, infer from the subtitle group / archive convention; for Chinese archives with no tag, default to `chs` (simplified). The fixed layer maps `chs`/`sc`/`gb`→`zh-CN` (default flag), `cht`/`tc`/`big5`→`zh-TW`, `jpn`/`jp`→`ja`, `eng`/`en`→`en`, `ko`/`kor`→`ko`.

## fail_closed

Use `fail_closed` only for **global** ambiguity or contradiction — e.g. there are no target videos at all, or the archive does not correspond to any available task. Do **not** use `fail_closed` just because one or a few subtitles have no target; mark those `unmatched` with a reason and keep the rest accepted. `reason_kind` ∈ {`contradiction`, `insufficient_evidence`, `provider_failure`, `unknown`}.

## Output protocol

Act through tools, not prose. Call `get_subtitle_mapping_context`, then `validate_subtitle_mapping`, then `submit_subtitle_mapping` directly with no explanatory narrative. Do not print mapping tables, draft JSON, or verifier issue dumps in assistant text. A non-final text-only turn is at most one short blocker sentence.
