---
name: auto-fetch-contract
description: Use when selecting a subtitle candidate thread + package to fetch for missing videos via the auto_fetch Case Agent, especially when selection workflow, ref policy, or gate repair is unclear.
---

# Auto Fetch Contract

The final output for this stage is a Python-gate-accepted candidate + package selection submitted with `submit_package` (after `submit_candidate`), or a safe `fail_closed` / `need_confirm` result for global ambiguity. This stage is dry-run only: no downloading, moving, copying, linking, or renaming files inside the agent. The Python layer downloads after `submit_package` returns accepted.

**auto_fetch is candidate ranking (select thread → select package), not mapping.** There is no coverage / duplicate / accounting contract. The fixed layer only runs light submit gates.

## Fact catalogs

The fixed layer presents facts via `get_auto_fetch_context`:

- **scan scope**: `scope_type` (series / movie / task), `root`, `source`.
- **missing videos** (`MV<idx>` short refs): `task_uuid`, `task_title`, `season`, `is_movie`, `video` (post-rename landed filename), `source_video` (pre-rename local original filename — evidence only, may be empty), `target_path`.
- **keywords** (`KW<idx>`): deterministic search keywords already tried (tmdb_name / name / source-dir title variants).
- **candidates** (`CD<idx>`): provider search hits with `title`, `detail_url`, `snippet`, `pages_scanned`, `package_count`, `has_downloadable_attachment`, and nested **packages** (`PK<idx>`): `floor_label`, `post_text`, `package_flags`, `has_downloadable_link`, `is_font_or_patch_only`, `links`.

These short refs (`CD<idx>` / `PK<idx>`) are the only identifiers submit may use. Titles, detail URLs, and filenames are evidence for reasoning; they are **not** selection IDs. Copy the refs exactly.

**`source_video` is a strong pairing hint**: when the subtitle release group / naming matches the original local files (e.g. subtitle thread `[SubGroup] Foo 01` vs local source `[SubGroup] Foo 01.mkv`), the `source_video` field carries that pre-rename name and is a more direct match than the post-rename `video` (e.g. `Foo - S01E01 - Pilot.mkv`). Prefer `source_video` similarity when non-empty.

## Selection workflow

1. `get_auto_fetch_context` — read missing videos + scan scope + already-loaded candidates.
2. `search_candidates(keyword)` — if no candidates loaded, search with a title / `source_video` hint. Returns new `CD<idx>` refs.
3. Inspect candidate titles/arcs; pick the one matching the missing videos' arc (right season / not OVA / not 特别篇 for main episodes).
4. `submit_candidate(candidate_ref, language, reason)` — gate: candidate must have downloadable attachment or packages. On accept, proceed to package selection.
5. `load_candidate_packages(candidate_ref)` — deep-load the candidate's thread packages into `PK<idx>` facts.
6. `inspect_package(package_ref)` — read post_text / links / flags to judge main episode vs special/font.
7. `submit_package(package_ref, reason)` — gate: package must have a downloadable link and must NOT be font/patch-only (needs batch/simplified/traditional/bilingual marker). This is the terminal accepted path.
8. `goal_complete`.

If no candidate matches the arc, `fail_closed(reason, reason_kind)` with a concrete reason. If genuinely uncertain between candidates, `need_confirm(reason)`.

## Gate semantics (fixed layer)

- `submit_candidate`: selected candidate must have `has_downloadable_attachment` or at least one package. Rejection → `candidate_not_downloadable` / `unknown_candidate_ref` / `invalid_ref_shape`.
- `submit_package`: selected package must have `has_downloadable_link` and NOT `is_font_or_patch_only`. Rejection → `package_not_downloadable` / `package_font_or_patch_only` / `unknown_package_ref`.

Uncertain judgments (which arc, which version/language, main vs special) are NOT gate decisions — express them via `submit` reason, `fail_closed`, or `need_confirm`.

## Disposition hints

- Pick the candidate whose title/arc matches the missing videos. Use `source_video` hint when the subtitle naming matches the local original.
- Pick a package with `batch` / `simplified` / `traditional` / `bilingual` marker for main episodes. Avoid `font` / `patch`-only or `special`-only packages.
- `fail_closed` is for concrete wrong-arc / no-candidate situations, not for "one package looks iffy" (pick a better package instead).
- `need_confirm` is for genuine ambiguity between candidates, not a shortcut when lazy.

## Dry-run only

No file download / move / copy / link / rename inside the agent. `submit_package` returns the selection; the Python layer downloads after acceptance.
