---
name: anime-release-reading
description: Use when local anime release structure is ambiguous, especially folder groups, episode tokens, seasons, cours, specials, OVAs, OADs, movies, recaps, duration hints, or obvious non-main extras.
---

# Anime Release Reading

Treat the local file universe in the case context as already hard-filtered. OP, ED, NCOP, NCED, PV, CM, Menu, Preview, Trailer, Teaser, Scans, CDs, non-video, and obvious extras may simply be absent.

Read local structure first:

1. Read `case_input.local_structure_summary` when it exists. List the visible `source_path` values and read their directory structure before searching Bangumi.
2. Treat `local_structure_summary` as factual grouping evidence only. It summarizes folders, filename prefixes, locator-number runs, repeated starts, content-shape words, and duration ranges; it does not decide the Bangumi target.
3. Infer groups from relative folders, repeated title prefixes, season/movie/OVA/SP words, and episode-number runs. A group can be a TV season, a movie, an OVA/OAD entry, a special set, or supplemental extras.
4. Notice when numbering restarts. Multiple `01` files under different folders or title prefixes usually means multiple groups, not duplicate episode 1.
5. For each group, identify the shared title/qualifier and the changing locator token such as `01`, `#01`, `SP01`, `OAD1`, movie subtitle, or part marker.
6. Choose the shortest recipe rule per group after the group identity is supportable. Use one rule for a coherent sequence, exact paths for standalone exceptions, and supplemental rules for bonus-like groups with no clear Bangumi target.

Then read names in this order:

1. Ignore release group, source, codec, audio, resolution, CRC, and version tags.
2. Read the title, season/subtitle qualifier, and content-shape tokens that remain.
3. Confirm the Bangumi subject identity before choosing episode targets. A mechanically valid recipe is still wrong if the subject is the wrong season, movie, OVA, or franchise entry.
4. Pick the shortest recipe rule that explains each inferred group after the subject identity is supportable.
5. Expand investigation when candidates conflict, target evidence is missing, the title qualifier is not accounted for, or the verifier blocks.

High-priority checks:

- Keep season and title qualifiers that distinguish entries inside one franchise. Examples include numbered seasons, `Part 2`, `Final`, short all-caps season codes, movie subtitles, and OVA/OAD subtitles. They are part of the target identity, not disposable release metadata.
- If a local title contains a distinct qualifier, treat a franchise-root or earlier-season match as weak evidence. Prefer exact title evidence, related subjects, or a fail-closed result when the matching subject cannot be exposed.
- Numbered special files such as `SP01`, `SP02`, `Special 1`, or `S00E01` are candidate special entries, not automatic extras. Check the selected same-season Bangumi subject for matching special rows by sort/order before treating those files as supplemental.

Large release boxes:

- The directory-structure-first workflow applies to every case. A large box only means there are more groups and less time for per-file investigation.
- When one case contains many folders or many seasons/movies, split the visible paths by folder title and content shape before searching broadly. Treat each folder-level title such as a TV season, movie title, OVA title, or special collection as its own recipe group.
- In movie collections, package labels like `#01`, `#02`, or `MOVIE 01-09` are release locators, not final Bangumi episode numbers. Identify each file title/subtitle first, then confirm its Bangumi subject or episode from evidence.
- For a movie/special collection from one franchise, make a local title checklist first. After one anchor search exposes the franchise, prefer the Bangumi relation graph as the series map; direct title searches are most useful for names still missing or conflicting after checking the graph.
- When each movie-box file has a distinctive movie title and a plausible one-movie Bangumi subject, exact-path movie rules with `subject_id` and `media_kind: "movie"` are usually enough for validation. Episode-list fetches are most useful for non-movie exceptions, multi-row subjects, or verifier blockers.
- For each group, use one representative `source_path` with `find_bangumi_targets_for_local_file`, then expose the subject's episode list or target window only as needed for that group. Avoid searching every file individually when the group has a clear sequential pattern.
- If the same franchise/title words keep appearing in searches while the package structure is already clear, prefer drafting a grouped recipe and letting validation reveal concrete missing target evidence.
- Validate a partial grouped recipe once the main groups are supportable. If validation reports missing target episodes for one group, repair that group using targeted evidence instead of restarting the whole investigation.
- If one short or bonus-like group remains ambiguous after a clean direct title search and the relevant Bangumi anime/video relation frontier is exhausted, a supplemental rule with a clear evidence gap is usually the right compact outcome. If exact Bangumi subject/episode evidence exists, map it and validate; frontier exhaustion is not required for a positive mapping.
- For companion extras such as recording diaries, interviews, cast/staff talks, travel/location features, making-of, stage greetings, memorial clips, or short bonus documentaries, the whole franchise relation graph usually adds little. After the main subject is anchored, one exact title search or one representative targeted lookup is enough evidence for a supplemental rule unless it exposes a plausible anime/video target.
- If a long standalone file has a distinctive title but no episode number, first compare it against the selected subject's episode list and related anime/video graph. If validation flags that exact path with a review warning, call `find_bangumi_targets_for_local_file` for the exact `source_path`; if that still exposes no supportable target, validate the same supplemental rule again.
- If time is running out and a large box still has unresolved groups, finish with a tool result: submit a recipe that covers all supportable groups and marks genuinely unsupported bonus-like groups with `disposition: "non_bangumi_or_supplemental"`, or fail closed with the unresolved group names.

Runtime and duration evidence:

- Local runtime evidence may appear in `case_input.context.local_files[].container_facts.duration_seconds` with `probe_status: "available"`. Bangumi episode rows may also expose `duration_seconds`.
- Chapter evidence may appear as `container_facts.chapter_count` and `container_facts.chapter_durations_seconds`. A single long file with chapters can be a merged presentation of several Bangumi episodes.
- Use duration as supporting evidence after title, subject identity, episode type, and sort/order. It can help distinguish ordinary TV-length items, short specials, long movies, recaps, split parts, and package extras.
- Missing local duration is not negative evidence. If `container_facts` is absent, `duration_seconds` is null, or `probe_status` is not `available`, ignore duration and continue with title, file numbering, folder structure, subject identity, episode type, and Bangumi sort/ep evidence.
- Missing Bangumi duration is also not negative evidence. A missing runtime should not override otherwise supported title/subject/episode evidence.
- Treat large runtime mismatches as a recheck signal, not an automatic verdict. For example, a 90-minute local file mapped to a 24-minute TV episode, or a 2-minute local file mapped to a normal-length main episode, should trigger subject/episode review.
- Some legitimate anime targets are long by design. A named special, OVA/OAD, or movie-shaped entry can be 40-60 minutes and still map to one Bangumi episode row when the title/subject/episode evidence matches.
- Some re-edit, extended, director's cut, omnibus, or compilation subjects intentionally have one long local file per Bangumi episode. If the visible files form a contiguous sequence and the selected Bangumi subject exposes the same one-row-per-file shape, keep the one-file-one-row interpretation unless other evidence contradicts it.
- TV premieres and finales are sometimes broadcast as enlarged single episodes. If only the first or last file of an otherwise contiguous same-subject sequence is long, treat duration as a recheck signal, then keep the one-file-one-episode mapping when subject, sort/ep order, and title evidence agree.
- If one exact visible file appears to cover multiple exposed episodes, prefer `source_unit: "single_file_multi_episode"` with one `exact_paths` entry and an `episode_range` over collapsing it to the first episode. Validation can check chapter or duration support; absent or contradictory support is a reason to fail closed.
- When duration is unavailable but title and numbering are coherent, prefer validating a compact recipe over researching only to find runtime data. Continue investigating or fail closed when title, subject, and episode evidence are also insufficient or contradictory.

Release and content-shape tokens:

- `OVA` / `OAV`: original video animation. Often direct-to-video content, side story, sequel, bonus episode, or a small standalone entry.
- `OAD`: original animation DVD, commonly bundled with source material. Treat like OVA evidence, not as a TV episode number by itself.
- `ONA`: original net animation. It may be a real series, a short web entry, or a preview-like web release; verify with Bangumi.
- `SP`, `Special`, `TVSP`: special-like content. It can be meaningful S00-style content or low-value packaging; verify target evidence before treating it as supplemental.
- `Movie`, `The Movie`, `Gekijouban`, `Film`: often a separate subject or movie-shaped target, not a regular TV episode.
- `Recap`, `Digest`, `Summary`: recap-like content. It can be a real special or low-priority supplemental item; verify target evidence.
- `Omake`, `Extra`, `Bonus`: supplemental-looking content. Map when Bangumi evidence makes it meaningful; otherwise treat it as supplemental or fail closed.

Common Japanese or romaji content words:

- `Gekijouban`, `Gekijoban`, `Eiga`: theatrical/movie wording.
- `Tokuten`, `Bonus`, `Omake`: bonus or bundled extra.
- `Bangaihen`: side story or extra chapter.
- `Soushuuhen`, `Soshuhen`, `Digest`: recap or compilation.
- `Yokoku`: preview.
- `Mini Anime`, `Petit`, `Puchi`, `Chibi`: short-form side content.
- `Picture Drama`, `Drama CD`, `Audio Commentary`, `Cast Talk`, `Staff Talk`: often supplemental or special-adjacent; verify before mapping as a main target.

Opening, ending, and promotion tokens:

- `OP` / `Opening` and `ED` / `Ending` usually mean theme sequences, not regular episodes.
- `NCOP`, `NCED`, `Creditless`, `Textless`, `Clean OP`, `Clean ED` are creditless opening/ending files. They are normally extras.
- `PV`, `CM`, `Preview`, `Trailer`, `Teaser`, `Spot` are promotional or preview material. They are normally extras unless the case context still exposes them for a specific reason.

Episode and locator tokens:

- `S01E01`, `S00E03`, `01`, `#01`, `EP01`, and ranges such as `01-02` are episode-locator evidence, not final authority.
- For movie-box filenames, `#01` / `#02` / `#03` usually orders the package. Counting alone is weak evidence; read the movie subtitle or named entry and verify it against Bangumi subjects/episodes.
- `S00E..` usually means a special in media-server naming, but the exact target still depends on Bangumi evidence.
- `OVA2`, `SP3`, or `Movie 2` usually means the second OVA/special/movie-shaped item, not TV episode 2. Use it as a search and target-disambiguation hint.
- Numbered special tokens such as `SP01`, `SP02`, `Special 1`, or `S00E01` are candidate special locators. If the selected same-season Bangumi subject has special episodes with matching sort/order and compatible title or duration evidence, map them to those specials. If the Bangumi subject has no matching special row, or the file title is clearly promo/live/interview/menu/theme material, cover it as supplemental instead.
- Episode `00`, `0.5`, and decimal-like numbers are ambiguous. They may be preview, recap, prologue, or chronological placement hints.
- For sequels or later cours, local files may restart at `01` while Bangumi `sort` continues from the previous subject and Bangumi `ep` is `1`. After confirming the subject identity with the episode list, use recipe params with `episode_number_field: "ep"` instead of expanding the run into many one-file rules.
- Roman numerals such as `II`, `III`, `IV`, `V`, and `XV` are ambiguous. They may be a season/title marker, disc or volume marker, special marker, or bonus label; treat them as episode numbers only with supporting evidence. If a Roman-numeral file would duplicate a numbered SP/OVA/Movie target already covered by another file, that duplicate is better handled as supplemental or fail-closed evidence than forced onto the same target.
- If the visible files look like `01` to `13`, a single TV episode rule is usually the compact main mapping. If there are also files in folders like `SPs`, `Specials`, or `Extras`, handle them after the TV rule: check numbered `SP01`-style files against Bangumi special episodes for the same subject, then check non-numbered or title-only extras separately. Numbered SP files and vague Roman-numeral/bonus files should be grouped together only when both lack a clear Bangumi target. If there is no clear Bangumi entry and the file looks like package bonus material, validate a supplemental draft with a clear reason; use fail-closed when that draft is not supportable or the verifier blocks.
- Multi-part movie files may use `Part`, `CD`, `Disc`, or `Disk`. Treat these as part locators before assuming separate Bangumi episodes.
- `Part A/B`, `Part 1/2`, `CD1/CD2`, and `Disc1/Disc2` can be split files for one long item. Treat separate-episode mapping as a hypothesis that needs supporting title/order evidence.
- A merged file is the opposite shape: one local video intentionally contains several Bangumi episodes. Look for a long runtime, chapters, title wording like complete/merged/uncut, or a subject whose episode list contains multiple short rows matching the local file's total shape.

Technical and release metadata tokens:

- Release group tags such as `[Group]` are provenance, not title or episode evidence.
- `BD`, `BluRay`, `BDRip`, `DVD`, `WEB`, `WEB-DL`, `WEBRip`, `AMZN`, `NF`, `DSNP` are source/platform tags.
- `1080p`, `720p`, `2160p`, `x264`, `x265`, `HEVC`, `AVC`, `Hi10P`, `FLAC`, `AAC`, `Opus`, `Dual Audio`, `v2`, and CRC/hash-like hex strings are technical metadata.
- In a `source_pattern`, represent changing CRC/hash/checksum strings, per-file IDs, and changing technical suffixes such as `FLAC` versus `FLACx2` with `{hash}`, `{crc}`, `{audio}`, or `{a}` rather than copying one file's changing token into a rule for the whole group.
- Technical tags can help compare releases but should not become Bangumi episode numbers or content shape.

Version, cut, and broadcast-variant tokens:

- `v2`, `v3`, `rev`, `proper`, `repack`, and `fixed` usually mean release revisions, not new episodes.
- `TV Ver.`, `BD Ver.`, `DVD Ver.`, `Web Ver.`, `Director's Cut`, `Extended`, and `Uncut` may be alternate cuts of the same target or special entries. Check Bangumi evidence before changing target identity.
- `Extended Edition`, `Director's Cut`, `Complete Edition`, `Compilation`, `Omnibus`, `Re-edit`, `Recut`, and `Uncut` can also be separate long-format Bangumi subjects or episode rows. Use the subject title, relation graph, episode list, and local contiguous sequence shape to decide whether they are one-file-one-row rather than merged multi-episode files.
- `Unaired`, `Unbroadcast`, `Extra Episode`, and `Web Preview` often indicate special-adjacent content rather than ordinary broadcast episodes.
- `Complete Edition`, `Compilation`, `Omnibus`, and `Remix` can indicate edited or compilation forms; one-to-one TV episode mapping needs supporting evidence.
- `Special Program`, `Pre-release Special`, `Before Release`, `公開直前`, `特別番組`, `特番`, `軌跡`, `轨迹`, `History`, or `Chronicle` often describe recap, publicity, or broadcast companion material. Treat them as supplemental-like unless Bangumi exposes a concrete anime subject or episode for that exact item.

Common package-extra words:

- Visual/navigation extras: `Menu`, `Top Menu`, `BD Menu`, `DVD Menu`, `Warning`, `Logo`.
- Promotion extras: `PV`, `CM`, `Trailer`, `Teaser`, `Spot`, `Preview`, `Yokoku`.
- Music/textless extras: `NCOP`, `NCED`, `Creditless`, `Textless`, `Clean OP`, `Clean ED`, `Karaoke`.
- Disc extras: `Scans`, `Scan`, `Booklet`, `Jacket`, `Cover`, `Gallery`, `Storyboard`.
- Audio extras: `OST`, `Original Soundtrack`, `Character Song`, `Drama CD`, `Radio CD`.
- Event/live extras: `Event`, `Live`, `Interview`, `IV01`/`IV02`-style interview-video tokens, `Making`, `Featurette`, `Commentary`, `Cast Talk`, `Staff Talk`, `Stage Greeting`, `Redubbing`, `Memorial`.
- Travel/location extras: `Travel`, `Tour`, `Journey`, `Location`, `Location Hunting`, `旅`, `ロケ`, `出張`. These are often live-action companion features; map them when Bangumi exposes a concrete anime subject or episode for that exact item.

Decision boundary:

- File-name tokens are investigation hints only.
- Bangumi subject/episode evidence decides the semantic target. The Python recipe verifier checks recipe shape, coverage, duplicates, and exposed target legality; it does not prove the chosen subject is the right franchise entry.
- If the subject identity is supportable and a compact rule can pass validation, extra research just to make the notes longer is usually not useful.

For quick local token inspection, you may run:

```bash
node .pi/skills/anime-release-reading/scripts/read-release-name.mjs "<relative-or-absolute-path>"
```
