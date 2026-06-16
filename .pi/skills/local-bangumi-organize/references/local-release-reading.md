# Local Release Reading

This reference reads the local release. It does not choose Bangumi targets, and it does not decide supplemental status by itself.

The visible file universe has already been filtered by the case. Obvious OP/ED/NCOP/NCED/PV/CM/Menu/Trailer/Scans/CD/non-video extras may already be absent.

## Package Shape

First decide what kind of package you are reading:

- single standalone title;
- contiguous seasons or cours;
- franchise pack with seasons, movies, OVAs/OADs, specials, mini anime, recaps, or side stories;
- movie box where package numbers order movies rather than episodes;
- side-content pack around a known parent title;
- mixed release with main episodes, parent-titled `SPs`, split variants, and extras.

For single standalone titles, direct title search may be enough. For the other shapes, make a local side frontier, then anchor one main line and read the Bangumi relation atlas before side-title searches. The frontier contains anime/video-shaped non-main groups: SP, OVA, OAD, mini anime, recap, side story, named special, parent-titled `SPs`, long standalone special-like files, or movie-like files.

Short runtime, an `SPs` folder, or bonus location describes local shape. It is not proof that a group is supplemental.

## Reading Groups

Use navigation instead of expanding every JSON layer:

1. `get_case_overview()` or startup overview for the map.
2. `list_local_groups(detail=false)` for group cards.
3. `get_local_group_detail(group_ref, detail=true)` only when source paths, durations, chapters, or file-level titles matter.

For each group, notice:

- shared title/folder qualifier;
- locator token such as `01`, `#01`, `SP01`, `OAD1`, movie subtitle, part marker, or no locator;
- count/range, numbering restarts, and duplicate locator variants;
- duration/chapter shape;
- whether it looks like main TV, movie, OVA/OAD, special, mini anime, recap, merged file, split variant, or package extra.

A local group is a reading unit, not proof that every file has the same target or disposition. If one folder has different duration clusters, title tokens, or locator patterns, preserve those subclusters. A mixed `SPs` folder can later become exact mapped rules for supported files plus supplemental rules for unsupported extras.

Duplicate readings need compatible content shape, not just a shared parent folder. A medium `SP01` file and a feature-length recap movie are different local surfaces until title and duration evidence show they are the same target.

## Name And Token Hints

Ignore release group, source, codec, audio, resolution, CRC/hash, and version tags before reading content identity.

Keep qualifiers that distinguish franchise entries: season numbers, Part 2, Final, short season codes, movie subtitles, OVA/OAD subtitles, side-story names, recap subtitles, and package movie names.

Roman numerals such as `II`, `III`, `IV`, `V`, and `XV` are ambiguous. They may be season/title markers, disc markers, special markers, or bonus labels.

Movie-box labels such as `#01`, `#02`, or `MOVIE 01-09` usually order the package. Read the subtitle or named entry before treating the number as a Bangumi episode number.

Useful tokens:

- `OVA` / `OAV`, `OAD`, `ONA`: side or standalone animation evidence.
- `SP`, `Special`, `TVSP`, `S00E01`: special-like content; candidate locator, not automatic extra.
- `Movie`, `Gekijouban`, `Film`, `Eiga`: movie-shaped content.
- `Recap`, `Digest`, `Summary`, `Soushuuhen`, `Soshuhen`: recap or compilation.
- `Bangaihen`: side story or extra chapter.
- `Mini Anime`, `Petit`, `Puchi`, `Chibi`: short-form side content.
- `Omake`, `Extra`, `Bonus`, `Tokuten`, interviews, cast/staff talks, making-of, stage greetings, menus, theme videos, and promotional clips: supplemental-looking unless Bangumi exposes a concrete anime/video target.

## Duration And Chapters

Local duration may appear at `case_input.context.local_files[].container_facts.duration_seconds`; chapters may appear at `chapter_count` and `chapter_durations_seconds`.

Use duration as supporting evidence after title, subject identity, row type, and order. Missing local duration is not negative evidence. Missing Bangumi duration is also not negative evidence.

Duration clusters are valuable for mixed groups. Similar medium/long `SP01/SP02` files may be real theater shorts or special episodes, while much shorter named manners/menu/promo files in the same folder may remain supplemental after Bangumi row checks.

For one visible file that seems to cover multiple exposed rows, look for chapters, an explicit filename range, or duration close to the target row-duration sum before using `source_unit:"single_file_multi_episode"`.

When local shape plus Bangumi atlas/episode evidence supports a testable group or subcluster, save that compact judgment with `upsert_recipe_group_decision_one` so it does not stay only in memory. The whole package does not need to be solved before the first stable row is saved.
