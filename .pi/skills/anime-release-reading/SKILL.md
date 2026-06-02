---
name: anime-release-reading
description: Use when local anime release structure is ambiguous, especially folder groups, episode tokens, seasons, cours, specials, OVAs, OADs, movies, recaps, duration hints, or obvious non-main extras.
---

# Anime Release Reading

This skill is for reading the local release. It does not choose Bangumi targets and does not run the validate/repair loop.

Treat the visible local file universe as already hard-filtered by the case. OP, ED, NCOP, NCED, PV, CM, Menu, Preview, Trailer, Teaser, Scans, CDs, non-video, and obvious extras may simply be absent.

## Local Group Reading

Read local structure through navigation first:

1. Start with `get_case_overview()` or startup `case_input.case_overview`.
2. Use `list_local_groups(detail=false)` as the compact group index.
3. Open `get_local_group_detail(group_ref, detail=true)` only when source paths, durations, chapters, or file-level notes are needed.
4. Treat group cards and detail pages as factual grouping evidence only. They summarize folders, filename prefixes, locator-number runs, repeated starts, content-shape words, durations, and warnings; they do not decide Bangumi targets.

Infer groups from folder titles, repeated title prefixes, season/movie/OVA/SP words, and episode-number runs. A group can be a TV season, a cour, a movie, an OVA/OAD entry, a special set, a recap, a merged file, split parts, or supplemental extras.

Notice numbering restarts. Multiple `01` files under different folders or title prefixes usually means multiple groups, not duplicate episode 1.

Notice duplicate locator variants inside one group, such as `SP08_1` and `SP08_2`. They usually cannot both map to the same Bangumi row. Treat one as the mapped presentation only when evidence supports it, and cover the extra split/variant file as supplemental unless a distinct exposed target row exists.

For each group, identify the shared title/qualifier and the changing locator token such as `01`, `#01`, `SP01`, `OAD1`, movie subtitle, or part marker.

## Name Reading

Ignore release group, source, codec, audio, resolution, CRC, and version tags before reading the content title.

Keep season and title qualifiers that distinguish entries inside one franchise. Examples include numbered seasons, `Part 2`, `Final`, short all-caps season codes, movie subtitles, OVA/OAD subtitles, and side-story names. They are part of target identity, not disposable metadata.

If a local title contains a distinct qualifier, a franchise-root or earlier-season subject is weak evidence until the qualifier is accounted for.

Movie-box labels such as `#01`, `#02`, or `MOVIE 01-09` usually order the package. Read the movie subtitle or named entry before treating the number as a Bangumi episode number.

Roman numerals such as `II`, `III`, `IV`, `V`, and `XV` are ambiguous. They may be season/title markers, disc markers, special markers, or bonus labels. Treat them as episode numbers only with supporting evidence.

## Specials And Extras

Numbered special tokens such as `SP01`, `SP02`, `Special 1`, or `S00E01` are candidate special locators. If Bangumi exposes matching same-season or related special rows by sort/order/title/count, they can map. If targeted evidence exposes no compatible row, they can be covered as supplemental.

Read the title around the numbered special token. `SP01` under a folder titled like a mini-anime, chibi short, OAD, OVA, or named side story is stronger evidence for that side-content title than for the parent TV season's special list. The parent TV subject lacking SP rows does not by itself make the group supplemental.

Season or part qualifiers on side-content folders matter. `Side Story`, `Side Story II`, and `Side Story III` are different local group titles even when their SP numbering looks the same.

A folder may be parent-titled and still contain a related short side anime. For example, a season folder with only `SP01-SP13` files may omit the mini-anime title from filenames. Treat the parent season qualifier, local count, durations, and relation-graph side subject as evidence before calling it supplemental.

For a long standalone OVA/OAD/SP-like file, do not assume it is a duplicate compilation only because a split short-episode set exists elsewhere. If a related one-episode OVA/OAD/special subject matches by title, duration, and relation evidence, it is a mapped exact-rule candidate.

`SP` is a local filename/content token. It is not a recipe `episode_offset`; target row arithmetic still uses `EP` unless an actual numeric shift is needed.

Named anime specials, movies, side stories, OVAs, OADs, and recaps should not be dismissed just because they are outside the main TV run. They need Bangumi subject or episode evidence.

Companion extras such as interviews, cast/staff talks, travel/location features, making-of, stage greetings, memorial clips, menus, theme videos, and promotional clips are supplemental-looking unless Bangumi exposes a concrete anime/video target for that exact item.

Multi-part files may use `Part`, `CD`, `Disc`, or `Disk`. Treat these as part locators before assuming separate Bangumi episodes.

A merged file is the opposite shape: one local video intentionally contains several Bangumi episodes. Look for long runtime, chapters, range title wording like `[01-09]`, or a subject whose episode list contains multiple short rows matching the local file's total shape.

## Duration And Chapters

Local runtime evidence may appear in `case_input.context.local_files[].container_facts.duration_seconds` with `probe_status: "available"`. Chapter evidence may appear as `container_facts.chapter_count` and `container_facts.chapter_durations_seconds`.

Use duration as supporting evidence after title, subject identity, episode type, and sort/order. It can help distinguish ordinary TV-length items, short specials, long movies, recaps, split parts, and package extras.

Missing local duration is not negative evidence. Missing Bangumi duration is also not negative evidence.

Large runtime mismatches are recheck signals, not automatic verdicts. Some legitimate specials, OVAs, movies, re-edits, premieres, and finales are long by design.

If one exact visible file appears to cover multiple exposed episodes, prefer `source_unit: "single_file_multi_episode"` with one `exact_paths` entry and an `episode_range` over collapsing it to the first episode.

## Token Hints

- `OVA` / `OAV`: original video animation; often side story, sequel, or standalone entry.
- `OAD`: original animation DVD; treat like OVA evidence.
- `ONA`: web animation; verify with Bangumi.
- `SP`, `Special`, `TVSP`: special-like content; can be meaningful or packaging.
- `Movie`, `The Movie`, `Gekijouban`, `Film`, `Eiga`: movie-shaped content.
- `Recap`, `Digest`, `Summary`, `Soushuuhen`, `Soshuhen`: recap or compilation.
- `Bangaihen`: side story or extra chapter.
- `Omake`, `Extra`, `Bonus`, `Tokuten`: bonus or bundled extra.
- `Mini Anime`, `Petit`, `Puchi`, `Chibi`: short-form side content.
- `Picture Drama`, `Drama CD`, `Audio Commentary`, `Cast Talk`, `Staff Talk`: special-adjacent or supplemental; verify before mapping.
- `OP`, `ED`, `NCOP`, `NCED`, `Creditless`, `Textless`, `PV`, `CM`, `Trailer`, `Teaser`, `Spot`, `Preview`, `Yokoku`: usually theme, promotion, or preview material.
- `BD`, `BluRay`, `BDRip`, `DVD`, `WEB`, `WEB-DL`, `WEBRip`, `AMZN`, `NF`, `DSNP`, `1080p`, `720p`, `2160p`, `x264`, `x265`, `HEVC`, `AVC`, `FLAC`, `AAC`, `Opus`, `v2`, and CRC/hash-like strings: technical metadata.

File-name tokens are investigation hints only. Bangumi subject/episode evidence decides the semantic target; the Python verifier checks recipe shape, coverage, duplicates, and exposed target legality.

For quick local token inspection, you may run:

```bash
node .pi/skills/anime-release-reading/scripts/read-release-name.mjs "<relative-or-absolute-path>"
```
