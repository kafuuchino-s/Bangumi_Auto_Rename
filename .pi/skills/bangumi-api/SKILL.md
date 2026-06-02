---
name: bangumi-api
description: Use when Bangumi evidence is confusing or tool semantics are unclear, especially relation graph traversal, subject IDs, episode IDs, sort/ep, target windows, specials, OVAs, OADs, or movies.
---

# Bangumi API

Use the Python custom tools as the case-scoped Bangumi evidence API. They expose facts for Pi's semantic choice and for the recipe verifier. Direct public HTTP calls are not part of this workflow.

This skill is about evidence, not recipe orchestration. The organizing loop belongs to `organize-recipe-contract`.

## Evidence Enough

For a local group, Bangumi evidence is usually enough to draft or repair params when you have:

- a plausible `subject_id` whose title, aliases, relation position, date, or media kind matches the local group;
- exposed episode rows when the group maps to episode numbers, specials, OVAs, OADs, or a specific `episode_id`;
- enough sort/ep/title/count evidence to choose `episode_number_field`, `episode_range`, or a one-file exact rule.

Episode count alone is not identity evidence. A franchise root or earlier season remains weak when a qualified local title points to a later season, movie, OVA, special, or side story.

The Python verifier checks mechanical legality and coverage. It does not prove semantic title correctness.

## Tool Use

- `search_bangumi_subjects`: search clean title terms from the local folder/file and known aliases. Queries work best without `Bangumi`, `BGM`, `subject`, or database words.
- `lookup_bangumi_subject`: fetch details for known subject IDs.
- `expand_related_graph`: bounded anime relation map from one or more confirmed anchors. Use `subject_types: ["anime"]` and leave `relation_kinds` empty unless you are filtering by a real relation label.
- `expand_related_subjects`: one-hop relation lookup when you intentionally need one hop.
- `get_episode_list`: expose episode IDs, `sort`, `ep`, type, title, and duration rows for subjects you plan to use.
- `get_target_window`: inspect a sort range for a sequence rule.
- `get_target_detail`: expose exact episode details by ID or subject/sort.
- `find_bangumi_targets_for_local_file`: compact fact lookup for one visible `source_path`; it returns search results and episode rows, not a chosen target.

Read `relation_subjects` first, then `edges` if the path through the graph matters. Keep anime/video-shaped nodes and ignore book, manga, novel, music, game, radio, soundtrack, live, and event nodes unless the local video evidence explicitly points there.

## Search Discipline

For one standalone main-title group, direct subject search plus episode evidence is usually enough.

For a multi-season, multi-entry, movie-box, OVA/special-box, or franchise side-content package, prefer the human workflow: search one reliable anchor first, then use `expand_related_graph` from that anchor to find the remaining seasons, movies, OVAs, OADs, specials, and short side entries. This is usually more stable than direct-searching every local group independently.

A numbered run usually needs one representative search plus episode evidence, not one search per file. If repeated searches reuse the same franchise/title words without new target evidence, switch to related-graph evidence, episode tools, or validate a params draft rather than searching the same space again.

One bounded related graph is useful evidence, not proof that the whole franchise is exhausted. Use more graph expansion only for a named conflict, a missing named group, or verifier/review feedback.

For a same-folder movie/special/OVA collection, start from one anchor, compare the bounded relation graph against the local title list, then search individual titles only for graph misses, conflicts, or verifier/review feedback.

## Related Graph Closure

Use related graph closure for packages with non-main local groups:

1. Anchor one or more confirmed anime subjects.
2. Keep a frontier of remaining local anime/video-shaped groups: OVA, OAD, SP, mini anime, chibi short, recap movie, side story, named special, or movie-like file.
3. Expand the graph from the current anchors and compare `relation_subjects` with the frontier titles, qualifiers, dates, counts, episode titles, and durations.
4. For every related subject that explains a frontier group, fetch episode rows, draft a mapped rule, and add that subject to the anchor set.
5. Repeat from newly mapped side subjects while new anime/video targets explain remaining frontier groups.
6. Use direct title search for graph misses, conflicts, or a qualified local group still unresolved after the graph pass.

Stop only when a graph/search pass adds no plausible anime/video mapping for the remaining frontier. At that point, supplemental rules are reasonable for the leftovers with a reason that names the closure evidence gap.

Do not treat a verifier issue on a mapped frontier rule as proof that the group is supplemental. First repair the target row type, media kind, episode ID, range/offset, selector, or duplicate/split handling. Supplemental is appropriate after evidence contradicts the target or closure cannot find one.

## Specials, OVAs, OADs, Movies

Numbered `SP01`, `SP02`, `Special 1`, or `S00E01` files are candidate special entries, not automatic extras. Map them when exposed rows resolve by sort/ep/title/count. If targeted evidence does not expose compatible rows, validate or repair the affected group as supplemental rather than forcing it onto a non-existent target.

For a numbered side-content group, do not stop at the parent TV subject. A parent season episode list often lacks related mini-anime, chibi shorts, OADs, OVAs, or BD specials. After one franchise anchor is known, check the related graph for the local side-content title, then fetch episode rows for the same-title or related anime/video subject that looks plausible. Direct side-title search is a fallback for graph misses, conflicts, or unresolved qualified groups.

A practical side-content evidence set is:

- local group title/qualifier, locator range, and representative source path;
- one clean subject search or relation-graph hit for that side-content title;
- episode rows for the candidate side-content subject;
- a comparison of local `SP01-SPnn` numbers to Bangumi `sort` or `ep`, plus title/count checks when available.

When side-content groups differ by season/part qualifier, fetch evidence per qualified group. A result for the unqualified side title is not enough to mark `II`, `III`, `Part 2`, or later-year side groups supplemental. Traverse from the anchor for qualified related subjects first; use qualified direct search when the graph does not expose a plausible match.

Be careful about the query identity. Searching only the parent season title, such as `Franchise II` or `Franchise III`, is parent-season evidence, not evidence for a side-content group titled `Side Story II` or `Mini Anime III`. For season-qualified side groups, either graph from the side-content anchor or search the qualified side title itself, such as `Side Story II`, before deciding there are no rows.

If that evidence exposes compatible rows, draft a mapped sequence even if the files are under an `SPs` folder. If it does not, a supplemental rule is acceptable; the reason should say which targeted title/episode evidence failed.

When `SP01` style local numbers map to exposed Bangumi rows, `SP` belongs in the filename selector or reasoning, not in `episode_offset`. Use `episode_offset: "EP"` unless a real arithmetic shift is needed.

Long special/movie-shaped files can be one-episode Bangumi subjects. Bangumi may expose their single row as `episode_type: "regular"`; use the row's legal type in params.

SP file naming is not the same as Bangumi row type. If a short SP subject exposes rows as `regular`, params should use `episode_type: "regular"` even when `media_kind` is `sp` or `special`.

For one-file movie-shaped subjects, an exact-path rule with `subject_id` and `media_kind: "movie"` can be trial-validated before fetching every one-row episode list. Fetch episode lists for non-movie exceptions, multi-row subjects, or verifier blockers.

Adjacent package numbers are weak evidence for mapping differently named movie/special files to the same Bangumi episode. Re-check titles, relation nodes, and exposed episode IDs.

Companion extras such as interviews, cast/staff talks, making-of, stage greetings, memorial clips, travel/location features, or short bonus documentaries are different from named anime specials/movies/side stories. After the main anime subject is anchored, one exact title search or one representative targeted lookup is enough evidence to treat them as supplemental unless it exposes a plausible anime/video target.

## Identity Reminder

Use real identifiers in recipe params: local `source_path` values from the visible universe, and Bangumi `subject_id`, `episode_id`, `episode_type`, `sort`, and `ep`. `task_source_path` is the task root, not a local file. Raw `web` is source/API vocabulary, not a recipe field.

`references/python-custom-tools.md` is most useful when custom tool schemas or return-shape notes remain unclear.
