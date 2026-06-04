# Bangumi Evidence

This reference is about evidence, not recipe orchestration. Use case-scoped Python tools; do not call public HTTP APIs directly. Pi chooses the semantic target; Python tools return facts and later verify legality.

## Atlas-First Evidence

For a single standalone title, direct `search_bangumi_subjects` plus episode rows can be enough.

For franchise or mixed packages, use the human route:

1. Search one reliable main TV/movie anchor; the first evidence batch should not be side-title fanout.
2. Call `select_bangumi_anchor_subject(anchor_subject_id, reason)` for that anchor; it records Pi's anchor choice and builds the full reachable anime/video atlas in one evidence bootstrap transaction.
3. Match atlas subjects against the local frontier by title words, qualifiers, dates, counts, episode titles, duration shape, platform, and relation path.
4. Fetch `get_episode_list(episode_scope:"all")`, `get_target_window`, or `get_target_detail` only for named target-surface gaps after reading the atlas.
5. Use broad side-title search only for atlas misses, conflicts, or a named frontier item still unresolved after the atlas pass.

Do not treat each local group as an independent search problem. Side-frontier board rows are a checklist for the relation graph before they become a search queue.

The atlas is not a recommendation list. It is the same works-family evidence a person would lay out before deciding which local group maps to which subject or row surface.

## Target Surfaces

A Bangumi subject is not one flat target. Read these surfaces separately:

- regular row sequence;
- special rows;
- OVA/OAD rows;
- movie-like rows;
- exact one-off rows with distinctive title, date, duration, or relation position.

One subject may support multiple recipe rules. If its regular rows explain one local SP mini-anime group, its special rows may still explain exact files in another movie-bundle `SPs` folder.

A regular-only row view, small episode-card limit, or narrow target window does not rule out special rows. Use `episode_scope:"all"` with enough cards when a mixed side folder still has numbered anime-shaped files.

Do not make a parent TV subject carry every side-shaped local group. If a side title or mini-anime title has its own related subject, inspect that subject's rows before using parent special rows or declaring the group supplemental. Parent special rows are target evidence only when the exposed rows themselves match the local side files.

For movie pairs and recap boxes, subject identity matters more than package order. If Bangumi represents each feature as a separate movie subject, save exact movie decisions per file; a sequence across one subject is only evidence-backed when that subject exposes multiple matching rows.

## Enough Evidence

Evidence is enough for a testable group/subcluster decision when you have:

- a plausible `subject_id` whose title/alias/date/platform/relation position fits the local group;
- exposed rows for numbered sequences, exact episode IDs, special/OVA/OAD/movie-like rows, or a target window;
- enough row evidence to choose the Bangumi target or to name a concrete evidence gap for supplemental.

Episode count alone is not identity evidence. Duration supports title and row evidence; it does not decide by itself.

If the remaining uncertainty is mostly params wording, selector syntax, `media_kind`, or `episode_type`, save the compact decision and let params validation repair mechanics. Use more Bangumi calls only for a named missing target surface or contradiction.

If one evidence batch makes a group or subcluster stable, save that judgment with `upsert_recipe_group_decision_one` before continuing exploration. A stable judgment is durable only after it becomes a saved decision or draft row.

If validation says a mapped row has no target episode, first ask whether the saved decision used the wrong target surface: one group-level decision may need to become exact movie rows, a parent-TV special row may need a related side subject, or a single exact file may need an explicit episode row or supported span.

## Tool Notes

- `search_bangumi_subjects`: anchor or fallback search. Use clean title terms; do not add database words such as Bangumi, BGM, subject, or anime database.
- `select_bangumi_anchor_subject`: default complex-package evidence bootstrap after one reliable anchor search. Pi chooses the anchor; Python records it, builds the atlas, and does not rank or choose targets.
- `build_bangumi_relation_atlas`: debug/manual fallback when an atlas must be rebuilt from a known anchor.
- `expand_related_graph`: smaller bounded graph helper for simple or targeted follow-up. Prefer `subject_types:["anime"]`; leave `relation_kinds` empty unless filtering a real relation label.
- `get_episode_list`: expose row IDs, legal recipe row type, sort/ep, titles, dates, and durations. Use `episode_scope:"all"` for side subjects and mixed folders.
- `get_target_window` / `get_target_detail`: focused row checks after you know the likely subject or episode IDs.
- `find_bangumi_targets_for_local_file`: compact fact helper for one exact visible file or subcluster; it does not choose the target or recipe.

Read `references/python-custom-tools.md` only when tool arguments, traversal status, or raw result fields are unclear.
