---
name: subtitle-mapping-contract
description: Use when mapping subtitle files from an archive to already-landed target videos via the subtitle Case Agent, especially when ref policy, disposition semantics, or verifier repair is unclear.
---

# Subtitle Mapping Contract

The final output for this stage is a Python-verifier-accepted subtitle mapping plan submitted with `submit_subtitle_mapping`, or a safe `fail_closed` result for global ambiguity. This stage is dry-run only: no moving, copying, linking, renaming, or writing media/subtitle files.

The fixed layer presents two flat fact catalogs via `get_subtitle_mapping_context`:

- **subtitle files** (`SF<idx>` short refs): `archive_path`, `filename`, `language_hint` (weak raw tag from filename), and fixed-layer dialogue evidence: `content_chinese_script` (`simplified` / `traditional` / `unknown`) plus simplified/traditional evidence counts.
- **target videos** (`TV<idx>` short refs): `task_uuid`, `task_title`, `season`, `is_movie`, `video` (the exact landed video filename), `source_video` (the pre-rename local original filename — evidence only, may be empty), `target_dir`, `task_video_count`, `arc_name` / `arc_name_cn` (the BGM-subject arc name of this video's season — Japanese original / Chinese; may be empty on old tasks).

These short refs are the only identifiers the mapping draft may use. Archive paths, task UUIDs, and video filenames are evidence for your reasoning; they are **not** target IDs in the draft. Copy the `SF<idx>` / `TV<idx>` refs exactly.

**`source_video` is a strong pairing hint**: when the subtitle archive shares its release group / naming style with the original local files (e.g. subtitle `[SubGroup] Foo 01.chs.ass` vs local source `[SubGroup] Foo 01.mkv`), the `source_video` field carries that pre-rename name and is usually a much more direct match than the post-rename `video` (e.g. `Foo - S01E01 - Pilot.mkv`). Prefer `source_video` similarity for episode/version pairing when it is non-empty; the verifier still validates against the post-rename `video` as the legal landing point.

**`arc_name` / `arc_name_cn` disambiguate same-episode-different-season** (critical for multi-season packs): subtitle archives are often posted per-arc with the arc name in the filename (e.g. `Mugen Ressha Hen 無限列車編 第01話`, `Yuukaku-hen 遊郭編 第01話`). Multiple seasons of a show all start their episodes at E01, so `S02E01` and `S03E01` both look like "episode 1". The target card's `arc_name` / `arc_name_cn` tells you which arc each season is (e.g. S02 = 鬼滅の刃 無限列車編 / Mugen Train, S03 = 鬼滅の刃 遊郭編 / Entertainment District). Match the subtitle's arc name (from its filename / folder) to the target's `arc_name` / `arc_name_cn` to pair the correct season — do NOT pair a 無限列車編 subtitle to a 遊郭編 target just because both are "episode 1".

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
      "unmatched_reason_kind": "no_target_video",
      "reason": "TV-Spot special has no matching target video"
    }
  ]
}
```

## Disposition semantics

- `map_to_video`: pair the subtitle with a target video. Requires `target_ref` (a `TV<idx>` ref) and a `language` tag (`chs`/`cht`/`jpn`/`eng`/`ko`/...). The fixed layer normalizes the raw tag to an Emby language code.
- `unmatched`: the subtitle has no confident target. Requires a concrete `reason` AND a structured `unmatched_reason_kind` enum. Must **not** carry a `target_ref`. This is a valid,合格 result for that subtitle; do not fail the whole case for one unmatched subtitle.
  - `unmatched_reason_kind` (required for unmatched rows):
    - `no_target_video`: the subtitle's content (PV / TV-Spot / Picture Drama / OAD / special / bonus / 05.5 / menu / creditless op-ed / making-of etc.) has **no matching target video** — TMDB has no episode entry for it, or the rename pipeline filtered it as non-main supplemental. These subtitles will never pair; mark them here so the processor can move them out of the "needs human" bucket.
    - `duplicate_language`: the same target video already has a subtitle in this language (e.g. two `.tc.ass` from different release groups for the same episode). Only one is kept; mark the redundant one here.
    - `no_confident_match`: target videos exist but you are not confident which one this subtitle pairs to (genuine ambiguity — stays in the "needs human" bucket).
- `needs_more_evidence`: still investigating. Must **not** carry a `target_ref`. Blocks accepted readiness; you must resolve every `needs_more_evidence` row to `map_to_video` or `unmatched` before submitting.

## Coverage contract (verifier)

- Every subtitle must appear **exactly once** as `map_to_video` or `unmatched`. `rows` count (minus `needs_more_evidence`) must equal subtitle count.
- A subtitle ref may appear only once (no duplicate source).
- The same target video may carry multiple subtitles **only if their languages differ** (e.g. one `chs` + one `cht` for bilingual). Same target + same language is a conflict.
- `map_to_video` rows require a non-empty `language`; empty language is rejected. A high-confidence `content_chinese_script` must agree with the mapped Chinese language; contradictory labels are rejected.
- `target_ref` must be one of the fixed-layer `TV<idx>` refs; unknown or mis-shaped refs are rejected.

## Workflow

1. `get_subtitle_mapping_context` — read the `SF<idx>` subtitle cards and `TV<idx>` target video cards. Note `language_hint` per subtitle and `task_video_count` per task (a movie task with one video pairs any of its subtitles to that single video).
2. Draft a `mapping_draft` covering **every** subtitle. Use folder structure, filenames, language tags, and the target video filenames/seasons to decide which `TV<idx>` each `SF<idx>` maps to. For multi-season archives, folder names (`S1/`, `S2/`, ...) and the target `season` field disambiguate which task a subtitle belongs to. **When multiple seasons share the same episode numbers (all start at E01), use the target `arc_name` / `arc_name_cn` to match the subtitle's arc name to the correct season** — never pair across arcs just because episode numbers match.

**Do NOT force-pair a subtitle to a target when the content does not match.** A subtitle whose filename/folder identifies it as a main TV episode (e.g. `鋼彈創鬥者-002.ass` = S1 episode 2) must NOT be paired to a Season 0 special/OVA target (e.g. `S00E06` Battlogue 01) just to avoid an empty mapping. The target card's `source_video` (pre-rename original filename) is the strongest content signal — e.g. `source_video = [Moozzi2] Gundam Build Fighters Battlogue - 01 ...mkv` explicitly names "Battlogue - 01"; a subtitle `鋼彈創鬥者-002.ass` (S1 main episode 2, no "Battlogue" marker, episode number 002 ≠ E06) does NOT match it. When the subtitle archive contains no subtitle for a given target video (the target is a special/OVA/Battlogue/Island-Wars whose subtitle was never posted), mark the unmatched subtitles as `unmatched` with `reason_kind=no_target_video` (or `no_confident_match` if you genuinely cannot tell) — do NOT fill the slot with an unrelated main-episode subtitle. An accepted plan with 0 mappings + all-unmatched is a CORRECT result when the archive simply has no subtitle for the landed targets; the fixed layer handles it (processor returns success + no_target_videos, not error). Never sacrifice correctness to make `mappings` non-empty.
3. `validate_subtitle_mapping` — verify without finishing. Read the returned `repair_hints` and verifier `issues`; patch only the named problems.
4. After `validate_subtitle_mapping` returns `accepted=true`, `submit_subtitle_mapping` with the same draft, then `goal_complete`.

## Language inference

Treat provider and filename language labels as weak hints. When
`content_chinese_script` is `simplified` or `traditional`, it is
high-confidence fixed-layer dialogue evidence and wins over a conflicting
provider label, directory name, or filename tag. Use `chs` for `simplified`
and `cht` for `traditional`; the verifier rejects contradictory mapped tags.
`unknown` means the dialogue was short, mixed, unreadable, or non-Chinese and
does not authorize guessing.

For `unknown`, infer from other visible archive conventions only when clear.
The fixed layer maps `chs`/`sc`/`gb`→`zh-CN` (default flag),
`cht`/`tc`/`big5`→`zh-TW`, `jpn`/`jp`→`ja`, `eng`/`en`→`en`, and
`ko`/`kor`→`ko`. Never trust a provider's Simplified/Traditional label over
contradictory dialogue-content evidence.

## fail_closed

Use `fail_closed` only for **global** ambiguity or contradiction — e.g. there are no target videos at all, or the archive does not correspond to any available task. Do **not** use `fail_closed` just because one or a few subtitles have no target; mark those `unmatched` with a reason and keep the rest accepted. `reason_kind` ∈ {`contradiction`, `insufficient_evidence`, `provider_failure`, `unknown`}.

## Output protocol

Act through tools, not prose. Call `get_subtitle_mapping_context`, then `validate_subtitle_mapping`, then `submit_subtitle_mapping` directly with no explanatory narrative. Do not print mapping tables, draft JSON, or verifier issue dumps in assistant text. A non-final text-only turn is at most one short blocker sentence.
