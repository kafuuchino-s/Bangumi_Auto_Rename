---
name: auto-fetch-contract
description: Use when selecting a subtitle candidate thread + package to fetch for missing videos via the auto_fetch Case Agent, especially when selection workflow, ref policy, or gate repair is unclear.
---

# Auto Fetch Contract

The final output for this stage is a Python-gate-accepted candidate + package selection submitted with `submit_package` (after `submit_candidate`), or a safe `fail_closed` / `need_confirm` result for global ambiguity. This stage is dry-run only: no downloading, moving, copying, linking, or renaming files inside the agent. The Python layer downloads after `submit_package` returns accepted.

**auto_fetch is candidate ranking (select thread → select package), not mapping.** There is no coverage / duplicate / accounting contract. The fixed layer only runs light submit gates.

**Pi drives crawling (aligned with rename chain).** The Python layer does NOT pre-search/pre-load candidates. Pi starts from the BGM subject name on the missing-video cards and calls `search_candidates_batch` / `load_candidate_packages_batch` itself.

## Fact catalogs

The fixed layer presents facts via `get_auto_fetch_context`:

- **scan scope**: `scope_type` (series / movie / task), `root`, `source`.
- **missing videos** (`MV<idx>` short refs): `task_uuid`, `task_title`, `season`, `is_movie`, `video` (post-rename landed filename), `source_video` (pre-rename local original filename — evidence only, may be empty), `target_path`, **`bgm_subject_name` / `bgm_subject_name_cn`** (task-level main Bangumi subject names — fallback search terms; may be empty for old tasks), **`bangumi_subject_id` / `subject_name` / `subject_name_cn`** (per-video BGM subject — the primary search-term source for multi-season coverage; `subject_name`=Japanese original, `subject_name_cn`=Chinese; may be 0/empty for old tasks, then fall back to task-level `bgm_subject_name`), **`preferred_language`** (user subtitle language preference, default `zh-CN` — use it to break ties between simplified/traditional/bilingual packages; may be empty for old tasks).
- **keywords** (`KW<idx>`): deterministic search keywords already prepared from BGM names (use them as the `keywords[]` for `search_candidates_batch`).
- **candidates** (`CD<idx>`): provider search hits with `title`, `detail_url`, `snippet`, `pages_scanned`, `packages_loaded`, `package_count`, `has_downloadable_attachment`, and nested **packages** (`PK<idx>`): `floor_label`, `post_text`, `context_text`, `has_downloadable_link`, `links` (each link: `url`, `kind`=`attachment`/`external`, `label`, `filename_hint`, `is_direct_download`).

**The fixed layer does NOT tag package nature.** There is no `package_flags` / `is_font_or_patch_only` field — the fixed layer no longer runs keyword detection (batch/font/special/simplified/traditional/bilingual). Package nature is a semantic judgment for YOU to make from `post_text` + `links[].label`/`filename_hint` (see "Package nature judgment" below). The only fixed-layer package gate is `has_downloadable_link` (pure fact: does the package have a direct-download link).

These short refs (`CD<idx>` / `PK<idx>`) are the only identifiers submit may use. Titles, detail URLs, and filenames are evidence for reasoning; they are **not** selection IDs. Copy the refs exactly.

**`package_count` / `has_downloadable_attachment` are `null` until the candidate is loaded.** A search hit only returns the thread title — the fixed layer has NOT probed the thread's attachments yet. Until you call `load_candidate_packages` for a candidate, its `packages_loaded = false` and `package_count` / `has_downloadable_attachment` read as `null` (unknown), not `0` / `false`. **Never read `null` as "no package" and never `fail_closed` with `no_downloadable_candidates` based on `null` package fields.** You MUST `load_candidate_packages` first to turn `null` into a real count. Only after `packages_loaded = true` can `package_count = 0` / `has_downloadable_attachment = false` legitimately mean "this thread has no downloadable attachment" — and even then, try another candidate before `fail_closed`.

**`bgm_subject_name` / `bgm_subject_name_cn` are the primary search terms.** Subtitle groups name threads by Bangumi Chinese/Japanese names far more often than by TMDB localized titles. When non-empty, pass their variants (name_cn, name, and the deterministic KW cards) as `keywords[]` to `search_candidates`. The primary name_cn/name variants are searched first (batch-limited); remaining variants are deferred and reported back — only search them if no loaded candidate matches the arc. Fall back to `source_video` / `task_title` variants only when BGM names are empty. **No AI keyword expansion** — use only the deterministic BGM-name variants the fixed layer provides.

## Selection workflow (multi-season coverage, uncovered-video-driven)

**Goal**: cover as many missing videos as possible with subtitles — like a human would. A task may span multiple BGM subjects (e.g. Demon Slayer S01+S02+S03 + movie = 4 subjects; Aria 3 TV seasons + 2 OVA = 5 subjects). Each subject usually has its own subtitle thread on the forum. **One thread may cover multiple subjects** (e.g. an "ARIA The AVVENIRE + Aria The Arietta" combined OVA thread) — selecting that thread's package once covers both subjects' videos; do NOT select it twice.

**Search-term strategy (verified against the forum) — SERIES NAME FIRST.** Subtitle groups name thread titles by the **series main name** (e.g. `七大罪`, `鬼灭之刃`), NOT by per-arc subject names. So a single `search_candidates(keywords=[series_main_name_cn, series_main_name])` returns threads for **every arc/season/OVA/movie of the whole series at once** — S01, S02, ..., specials, and movies — in one round-trip. The series main name is the task-level `bgm_subject_name` / `bgm_subject_name_cn` (also the first KW cards), shared across all per-video subjects. **Start multi-season tasks with the series main name.** This is strictly better than per-subject searching because:

- it catches **alias-titled threads** a per-subject name misses. Example: 七大罪 movie thread is titled `七大罪 剧场版：空中囚徒` (alias 空中囚徒, not the BGM subject name 天空の囚われ人) — searching `天空の囚われ人` returns 0, but `七大罪` returns it. Per-subject searching leaves the movie uncovered; series-name searching finds it.
- one search covers all subjects (1 round-trip vs N), saving wall-clock on big packs.

The forum search returns up to 20 threads per keyword — enough for most series. Only if a series has >20 threads, or the series-name results are all noise (PV/花屏/求字幕) and miss a specific arc, **fall back to per-subject names** (`subject_name` Japanese original e.g. `鬼滅の刃 遊郭編`, `subject_name_cn` Chinese e.g. `鬼灭之刃 游郭篇`) to disambiguate that one arc. Do NOT use TMDB English season names (0 hits on this forum).

**Workflow**: (1) `search_candidates(keywords=[series_main_name_cn, series_main_name])` first; (2) inspect the returned thread titles and **match each thread to the subject(s) it covers** by title (arc name / season / 剧场版 / OAD / special); (3) `load_candidate_packages` for the matched threads; (4) only if a subject with uncovered missing videos has NO matching thread in the series-name results, fall back to `search_candidates(keywords=[that_subject_name, that_subject_name_cn])` for that one subject.

**Prefer SHORTER keywords over suffixed ones.** A bare `圣战的预兆` or `Nanatsu no Taizai Seisen` hits reliably; appending ` 字幕` / ` 第一季` / ` 2016` often returns 0 (the forum indexes the thread title, not these suffixes). When a long/suffixed variant returns `no_candidates`, re-search the **shorter core** (the arc name alone, or the romanized arc alone) before concluding the subject has no thread. The fixed layer also auto-retries once on intermittent empty results, but a shorter keyword is the more reliable fix.

Search and load are **batch-limited** (capped per call) to avoid exhausting wall-clock on large multi-season packs. When a call returns `remaining_keywords` / `remaining_candidate_refs`, the fixed layer has deferred the rest — call again with the remaining refs only if the already-loaded candidates do not cover the missing videos.

**Concurrency (use it — wall-clock is the bottleneck on multi-season packs)**: the fixed layer runs the keywords/candidate_refs inside one `search_candidates` / `load_candidate_packages` call **in parallel** (and caches loaded candidates so re-loading the same thread is free). So prefer **one call with several subjects' keywords / several candidate_refs at once** over many serial single-subject calls — you get all results in the time of the slowest one. This is how you cover 7 subjects within timeout: batch the searches, batch the loads. Re-loading a candidate that already has `packages_loaded=true` is free (cache hit, no new HTTP).

1. `get_auto_fetch_context` — read missing videos. **Group them by `bangumi_subject_id`** (per-video subject). Note which subjects have missing videos (e.g. S01=26 videos subject 245665, S02=7 videos subject 350764, ...). If `bangumi_subject_id=0` (old task), treat all missing videos as one group using task-level `bgm_subject_name`.
2. **Series-name-first search**: `search_candidates(keywords=[bgm_subject_name_cn, bgm_subject_name])` (the task-level series main name, also the first KW cards). This one call returns threads for every arc/season/OVA/movie of the series — match each returned thread to the subject(s) it covers by title (arc name / season / 剧场版 / OAD). **Only fall back to per-subject `search_candidates(keywords=[subject_name, subject_name_cn])` for a subject whose arc has NO matching thread in the series-name results.** Returns `CD<idx>` facts (title/snippet only; `package_count` is `null` — packages NOT yet probed).
3. Inspect candidate titles; pick the one(s) plausibly matching that subject's arc (right season/OVA/movie, not a PV/花屏/求字幕 noise thread). Do NOT judge downloadability from `null` package fields here.
4. `load_candidate_packages(candidate_refs=[CD idx, ...])` — deep-loads packages, turns `package_count`/`has_downloadable_attachment` from `null` into real values. If `remaining_candidate_refs`, inspect loaded ones first.
5. `submit_candidate(candidate_ref, language, bangumi_subject_id, reason)` — declare which BGM subject this thread covers. Gate: candidate must be `packages_loaded=true` with downloadable attachment/packages.
6. `inspect_package(package_ref)` — read post_text/links to judge package nature (call for EVERY package you are considering before submit). **Look at every `link` in the package** — its `label`, `filename_hint`, `kind`, `is_direct_download`. NOTE: `BD-BOX`/`BDRip`/`Raw` in a label is the VIDEO SOURCE the subtitle is timed for, NOT a video file — subtitle forums do not host video. The attachment on a subtitle forum is almost always a `.rar`/`.zip`/`.7z`/`.ass`/`.srt`. Read `post_text` (e.g. "字幕由XX字幕组制作") to confirm it is a subtitle package; do NOT reject just because the label mentions BD-BOX. A 楼主 package may be a nested archive (outer `.rar` → inner `.rar`s per season); the extractor unpacks nested archives automatically, so select it if `post_text` confirms subtitles. See "Package nature judgment".

   **Before submitting a package for a subject, confirm the package CONTENT actually covers that subject's missing videos.** After `inspect_package`, match the link `label`/`filename_hint` (and `post_text` arc/episode hints) against the subject's missing-video `source_video`/`video`/`subject_name`/`subject_name_cn`. A Gundam Build Fighters thread whose package contains `鋼彈創鬥者-001~025.ass` (S1 main episodes) + `SD Knight Fighters special` does NOT cover a subject whose missing videos are `Battlogue 01-05` — the filenames have no "Battlogue" marker and the episode range (main 001-025) does not match specials S00E06-10. Do NOT `submit_package` such a package for the Battlogue subject just because the thread title mentions "Gundam Build Fighters"; it will download and pair WRONG subtitles (S1 main subs force-paired to Battlogue specials). Instead, leave that subject's videos uncovered, search the subject's own name (`Battlogue` / `バトローグ` / `战斗部落`), and if no thread has a package whose filenames actually contain the subject's content → leave it uncovered and move on (uncovered is a valid `submit_complete` outcome, see step 9). **A package that does not contain the subject's content is NOT a match — skip it, do not submit it for that subject.**

7. `submit_package(package_ref, reason, link_url=..., bangumi_subject_id=...)` — gate: `has_downloadable_link` (the ONLY package gate — fixed layer does NOT judge font/special, that's YOUR call per "Package nature judgment"). **This does NOT finish the case** — it appends a selection. The tool returns `selections_count` + `covered_subject_ids`.
   - **`link_url` — YOU pick the attachment.** A package (one floor) may contain SEVERAL direct-download attachments, e.g. a thread bundles 前篇 `[01-04].zip` AND 後篇 `[05-08].7z` as two separate attachments in the same post. The fixed layer does NOT score/pick which attachment to download — that is a semantic judgment (which attachment covers which episodes/arc/language), so YOU make it. Match the attachment by its `label`/`filename_hint` against the subject's missing videos (前篇/前章/01-04 vs 後篇/後章/05-08, simplified `.sc.`/`.SC.` vs traditional `.tc.`/`.TC.`, batch 全集 vs single-episode). Pass the EXACT `url` from `inspect_package`'s links. The `link_url` must be one of the package's `is_direct_download` links.
   - **One floor, multiple main-episode attachments covering different ranges** (e.g. 前篇+後篇 split as two archives in one post): `submit_package` once PER attachment with its `link_url` — each call appends a separate selection that downloads and pairs independently. Do NOT assume one attachment covers the whole series just because they share a thread; read the label/filename range.
   - Single-attachment packages: `link_url` may be omitted (first downloadable attachment is used).
8. **Check uncovered videos**: after each `submit_package`, look at which missing videos are still NOT covered by any selected package (a package covers the videos whose source/naming it pairs to — judge from `post_text`/filenames vs the missing video `source_video`/`video`). If uncovered videos remain, pick one of their subjects and go back to step 2 (search that subject). If a thread covers multiple subjects (e.g. AVVENIRE+Arietta combined), one `submit_package` may cover several subjects' videos at once — don't re-search those.
9. When ALL missing videos are covered, OR you have searched every subject's name variants and found no downloadable package for the remaining uncovered ones → `submit_complete(reason)`. This is the terminal accepted path (final_result contains all selections). If you found ZERO downloadable packages across all subjects → `fail_closed` instead.

**Do not stop after one subject.** A single package usually covers one season; the other seasons' videos stay uncovered. You MUST keep searching other subjects until all videos are covered or all subjects are exhausted. The nudge will remind you if you try to finish with uncovered videos remaining.

If genuinely uncertain between candidates, `need_confirm(reason)`.

## Gate semantics (fixed layer)

- `submit_candidate`: selected candidate must have `packages_loaded = true` AND (`has_downloadable_attachment` or at least one package). Submitting a candidate whose packages are still `null`/unloaded is rejected with `candidate_not_downloadable` + a "load first" hint — call `load_candidate_packages` then re-submit. Pass `bangumi_subject_id` to declare which subject this thread covers (multi-season grouping). Other rejections: `unknown_candidate_ref` / `invalid_ref_shape`.
- `submit_package`: selected package must have `has_downloadable_link` (the ONLY package gate — pure fact). The fixed layer does NOT reject font/patch/special-only packages; package nature is YOUR judgment (see "Package nature judgment"). Rejection → `package_not_downloadable` (no direct-download link) / `unknown_package_ref` / invalid `link_url` (not a package link). **Does NOT finish the case** — appends a selection, returns `selections_count`/`covered_subject_ids`, continue with more subjects or `submit_complete`. `link_url` (optional) pins the exact attachment to download; rejected if it is not one of the package's direct-download links (call `inspect_package` to see them). `bangumi_subject_id` (optional) declares which subject this selection covers — pass it when one package's links cover different subjects (e.g. 前篇 link → subject A, 後篇 link → subject B), so the selection is accounted against the right subject.
- `submit_complete`: terminal accepted path. Requires at least one selection (else rejected — use `fail_closed` if nothing found). Does NOT require every subject to have a selection (a thread may cover multiple subjects; uncovered subjects with no forum thread are simply left uncovered, their videos stay missing — that's a qualified result, not a failure).

Uncertain judgments (which arc, which version/language, main vs special) are NOT gate decisions — express them via `submit` reason, `fail_closed`, or `need_confirm`.

## Package nature judgment (YOU decide — fixed layer no longer tags)

The fixed layer gives you the raw `post_text` + `links[].label`/`filename_hint`. It does NOT pre-tag packages as font/special/batch/simplified. Judge package nature yourself:

- **Subtitle package (main content — SELECT for main episodes)**: `post_text` says "字幕" / "字幕下载" / "字幕来自XX字幕组"; attachment filename ends in `.ass`/`.srt`/`.ssa`/`.sub`/`.vtt`, OR is a `.zip`/`.7z`/`.rar` archive from a subtitle group (e.g. `BeanSub.zip`, `ASS.zip`, `[冷番补完字幕组]...zip`, `[UCCUSS]...zip`, `EMBER...rar`, `[ReinForce][...][ASS].rar`) — subtitle groups bundle `.ass` inside archives. The floor text is the strongest signal: "字幕来自豌豆字幕组 BD版" = subtitle package even if the archive name has no subtitle extension.
- **`BD-BOX`/`BDRip` in the label is NOT a video-package signal** (subtitle forums do NOT host video). These tokens describe the VIDEO SOURCE the subtitle is adapted/timed for — e.g. `[Moozzi2] Aria The Animation BD-BOX - TV [SUB].rar` is a SUBTITLE archive (the `[SUB]` marker + `.rar` + 560KB size + floor text "字幕由动漫花园制作" confirm it). Do NOT reject a package just because the label contains `BD-BOX`/`BDRip`/`BD`/`Raw` — read `post_text` and check the attachment is an archive (`.rar`/`.zip`/`.7z`) or subtitle file, which it almost always is on a subtitle forum. The real failure mode is NOT "Pi picked a video package"; it is a **nested archive** (see below).
- **Nested archive (套娃包 — the extractor now handles this, but be aware)**: some 楼主 packages are an outer `.rar` whose contents are inner `.rar`/`.zip` archives (one per season/arc), each inner archive holding the actual `.ass`. Example: TID=346 楼主 `水星领航员.rar` → 4 inner `.rar` (Animation/Natural/Origination/Arietta) → 138 `.ass` total. The fixed-layer extractor recursively unpacks nested archives up to depth 3, so a 楼主 package that looks like "just 4 rars, no subs" is still a valid subtitle package — SELECT it if `post_text` confirms it is a subtitle bundle. Do NOT skip a 楼主 package because its top level has no `.ass`.
- **Font/patch package (NOT main content — do NOT select for main episodes)**: attachment filename contains `font`/`fonts`/`字体`/`フォント`, or the only link is an external link labeled "字体下载". A floor that ONLY mentions fonts (no "字幕" word) is a font package. Note: a package may bundle BOTH subtitle + font links (one attachment subtitle zip + one external font link) — pick the SUBTITLE link via `link_url`, not the font link.
- **Special/特典 (may or may not be needed)**: `post_text`/filename mentions 特典/NCOP/NCED/PV/CM/番宣/特報/宣传片. BUT — OAD/OVA/SP may be main content for a subject whose missing videos include those episodes (e.g. Gundam BF special disc). Check the missing videos: if `MV<idx>` for that subject includes S00/OAD episodes, a special package IS the main content for them. Do NOT auto-skip special-labeled packages when the subject's missing videos are themselves specials.
- **Simplified vs traditional (language tie-break)**: attachment filename has `.sc`/`.SC`/`简体`/`简中`/`chs` → simplified; `.tc`/`.TC`/`繁体`/`繁中`/`cht`/`big5` → traditional; `双语`/`简日`/`繁日`/`中日`/`SC TC` → bilingual. Use `preferred_language` to pick (zh-CN → simplified, zh-TW → traditional). A subtitle group often ships `.SC.ass` + `.TC.ass` as two separate attachments — pick the preferred-language one via `link_url` (or both if you want both languages).
- **Batch vs single-episode**: filename contains `01-04`/`01-12`/`全集`/`合集`/`batch`/`complete` → batch (covers a range); a single episode number `01`/`[01]` → single-episode. Prefer batch when missing videos span a whole season/cour.

When in doubt, read `inspect_package` `post_text` fully — the floor text ("字幕来自XX字幕组，已经匹配BD版") is more reliable than filename guesses.

## Episode-range limiters (one package may cover only PART of a series)

A thread/floor may bundle several main-episode archives each covering a DIFFERENT episode range — do NOT assume one attachment covers the whole series just because they share a thread. Range limiters in thread title / attachment label / filename:

- `前篇` / `前章` / `前編` / `Part 1` / `上巻` / `第一章` / `01-04` — covers the first part (e.g. episodes 01-04).
- `後篇` / `後章` / `後編` / `Part 2` / `下巻` / `第二章` / `05-08` — covers the second part (e.g. episodes 05-08).
- Also `前章 -TAKE OFF-` vs `後章 -STASHA-` (named parts), `第N章`, `Vol.1/2`.

When you see range limiters: match each attachment's range to the missing videos it covers (前篇 [01-04] → S01E01-04; 後篇 [05-08] → S01E05-08). `submit_package` once PER attachment with its `link_url` + `bangumi_subject_id` (if the parts map to different BGM subjects — e.g. 前篇 → subject 319390, 後篇 → subject 352905). After submitting, re-check uncovered videos: if 後篇 range is still uncovered, the 後篇 attachment is a separate selection — do not declare one 前篇 attachment as "covers 01-08".

## Noise thread patterns (skip these — they are NOT subtitle threads)

Some search hits are help/report/promo threads, not subtitle downloads. Skip them (do not `submit_candidate`):

- **Help/request threads**: title starts with `求字幕` / `求分享` / `请问` / `有没有` / `谁有` / `哪位大佬`, or ends with `？`/`?` — these ask for subtitles, they don't provide them. Title containing `[已解决]` is an already-answered help thread — still not a subtitle source.
- **Error/report threads**: title contains `報錯` / `报错` / `花屏` / `错位` / `問題` — reporting playback issues.
- **Promo/preview**: title contains `PV` / `CM` / `番宣` / `特報` / `宣传片` / `预告` — promotional videos, not episode subtitles.
- **Discussion**: title is a question or general discussion ("请问有朋友看过XX吗", "那个XX好奇怪啊").

Subtitle threads typically have a clear title naming the work + subtitle group, e.g. `七大罪 戒律的复活 / Nanatsu no Taizai Imashime no Fukkatsu 字幕`, `[UCCUSS] XX [Subs ...]`. Prefer those.

## Multi-subject combined threads (one thread covering several subjects)

One thread may cover multiple BGM subjects (e.g. `ARIA The AVVENIRE + Aria The Arietta` combined OVA thread; or a movie + TV arc bundled in one floor). Signs: thread title lists multiple arc names, or `post_text` lists subtitles for multiple seasons/parts, or one floor has several attachments each for a different subject.

When a thread covers multiple subjects: `submit_candidate` once with one subject's id, `submit_package` for that subject's attachment (via `link_url` + `bangumi_subject_id`); then `submit_candidate` again with the OTHER subject's id (same `candidate_ref`) + `submit_package` for the other subject's attachment. Do NOT submit the same attachment twice for the same subject. If one attachment covers multiple subjects at once (e.g. a batch covering S01+S02), one `submit_package` with one subject id is enough — the other subject's videos are covered by the same download; do not download twice.

## Disposition hints

- Pick the candidate whose title/arc matches the missing videos. Use `source_video` hint when the subtitle naming matches the local original.
- Pick a subtitle package (see "Package nature judgment") for main episodes. Avoid font/patch-only packages. For special/OAD subjects, special-labeled packages ARE the main content.
- **Language preference (simplified vs traditional).** Use the missing videos' `preferred_language` to break ties between otherwise-eligible main-episode packages:
  - `preferred_language = zh-CN` (default): prefer `simplified`, then `bilingual` (simplified-containing), then `traditional`. Only pick a `traditional`-only package when no simplified/bilingual main-episode package exists.
  - `preferred_language = zh-TW`: prefer `traditional`, then `bilingual`, then `simplified`.
  - When `preferred_language` is empty/unset, language is not a tie-breaker — pick the best main-episode package by other signals.
  - This is a preference, NOT a hard gate: a non-preferred-language package that is the only main-episode package still passes `submit_package` and is a valid accepted result. Do not `fail_closed` solely because the only package is the non-preferred language.
- **Quality ordering among same-language main-episode packages.** When several packages are all eligible (correct arc, main-episode, preferred language), prefer by these signals in order:
  1. `revision` flag (`修正版` / `校对` / `v2` / `v3` / `fix` / `rev`) — corrected/proofread versions are preferred over unmarked ones.
  2. `batch` over single-episode when the missing videos span a whole season/cour.
  3. post_text indicating complete coverage vs partial; closer match to the missing-video count.
  Read `inspect_package` `post_text` to judge — filenames and flags are hints, the floor text is the strongest signal.
- `fail_closed` is for concrete wrong-arc / no-candidate situations, not for "one package looks iffy" (pick a better package instead).
- `need_confirm` is for genuine ambiguity between candidates, not a shortcut when lazy.

## Dry-run only

No file download / move / copy / link / rename inside the agent. `submit_package` returns the selection; the Python layer downloads after acceptance.
