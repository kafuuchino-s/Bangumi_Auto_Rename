---
name: tmdb-bridge-contract
description: Use when bridging an accepted Local-to-Bangumi compiled plan to TMDB with recipe params, especially when TMDB title/slug/original-name evidence or verifier repair is unclear.
---

# TMDB Bridge Contract

The final output for this stage is Python-verifier accepted BGM-to-TMDB recipe params submitted with `submit_bgm_to_tmdb_bridge_recipe_params`, or a safe fail-closed result for global ambiguity. This stage is dry-run only: no moving, copying, linking, renaming, or writing final media files.

Use recipe params as the primary workflow. Per-source `source_path -> tv:<id>:SxxEyy` mappings are debug/fallback material for exact edge cases, not the normal TV episode sequence workflow. Python compiles compact rules into raw bridge outcomes, then the existing verifier checks coverage, duplicates, legal nodes, spans, target-absent boundaries, and supplemental boundaries.

Bangumi identity comes from the accepted compiled plan: `source_path`, `bangumi_subject_id`, `episode_id`, `sort`, `ep`, title, media kind, episode type, and span episode IDs. TMDB identity in recipe params is `tmdb_ref` (`tv:<tmdb_id>` or `movie:<tmdb_id>`), plus season/range fields when needed. Compiled legal nodes are `tv:<tmdb_id>:SxxEyy` for TV episodes and `movie:<tmdb_id>` for movies.

TMDB names are semantic evidence, not output identity. Use `display_title`, `original_name`, `original_title`, aliases, year, overview, season cards, episode cards, and URL slug text such as `45844-space-battleship-yamato-2199` to decide whether a TMDB candidate matches the Bangumi plan. The verifier accepts only explicit bridge outcomes: `map_to_tmdb` with TMDB IDs/legal nodes, `tmdb_target_absent` for BGM nodes that TMDB does not expose, or `unmapped_supplemental` for Local-to-Bangumi supplemental files.

## Workflow

1. Read grouped BGM subject cards and assignments with `get_bgm_to_tmdb_bridge_context`. Accepted Local-to-Bangumi artifacts can be reused directly; a Local-to-Bangumi rerun is unnecessary.
2. Search TMDB candidates by Bangumi/local human titles. Compare candidate cards by ID, title, original name, aliases, year, overview, season names, and episode lists.
3. Draft compact recipe params. Prefer one rule for a contiguous TV sequence, one rule for a movie, one rule for a special sequence, one span rule for one source covering multiple episodes, one `tmdb_absent_group` rule for BGM nodes that TMDB lacks, and one supplemental rule for extras.
4. Call `validate_bgm_to_tmdb_bridge_recipe_params`. Validation hydrates declared `tmdb_ref` values, compiles params to a raw bridge draft, runs the node verifier, and returns repair hints or review warnings.
5. Keep repairs targeted to the rule named by the verifier. Broad search is useful again only when the verifier asks for missing TMDB evidence.
6. After validation returns `accepted:true`, call `submit_bgm_to_tmdb_bridge_recipe_params` with the same params.

Search is for finding plausible TMDB refs, not for exhaustively proving every recap/summary/CM/bonus title. After a plausible series/movie candidate is found and hydrated, recipe validation is the next stronger evidence layer. If validation shows a mapped Bangumi episode/special has no concrete TMDB legal node, a targeted season-0/episode-title check can justify `tmdb_absent_group` for that BGM node instead of failing the whole case.

Raw `validate_bgm_to_tmdb_bridge` and `submit_bgm_to_tmdb_bridge` are debug/fallback tools only. Use them for exact edge cases or inspecting generated JSON, not as the normal path.

## Franchise Anchor First

For multi-season franchise packages, searching every season, OVA, OAD, and special title first is usually wasteful. Search one strong franchise/series anchor, then treat its hydrated TMDB legal graph as the strongest next evidence layer before deciding whether more searches are useful. Inspect its season cards, season 0 cards, episode titles, aliases, years, and overviews.

Additional title searches are most useful when the hydrated graph does not expose the needed target shape:

- Search a separate movie title when BGM has a movie/span and the series graph does not expose a movie node.
- Search an OVA/OAD/special title when season 0 cards do not contain a matching title/order/count.
- Search recap/summary/CM/digest titles after the main graph is anchored; once the graph shows no legal node, `tmdb_absent_group` fits BGM-mapped nodes and `supplemental_group` fits Local-to-Bangumi supplemental files.

Preferred evidence ladder: anchor search -> hydrated TMDB legal graph -> season/episode-card comparison -> recipe validation -> targeted verifier repair. If the first hydrated graph already contains the seasons and S00 specials needed by the BGM subject cards, separate searches for each subject title are unnecessary.

## TMDB Legal Graph Closure

Use the accepted BGM plan as the frontier, not the local file tree. After the main BGM subject or movie anchor is matched to a TMDB ref, hydrate its legal graph and compare every remaining BGM-mapped anime/video group against that graph.

Close the bridge frontier like this:

1. Anchor the strongest TMDB series or movie candidate.
2. Hydrate its legal graph with seasons, season 0, episode cards, aliases, years, overviews, and slugs.
3. Map every BGM group whose title/order/count/episode-title evidence fits an exposed legal node range.
4. Put BGM specials, OVA/OAD, recap movies, spans, and side-story subjects that are not yet explained into a TMDB side frontier.
5. If the hydrated graph exposes a new relevant season/special/movie shape, draft that rule and treat the hydrated TMDB ref as an anchor for the remaining frontier evidence.
6. Search additional TMDB titles only for graph misses, conflicting candidates, or frontier groups whose BGM title clearly points outside the hydrated series graph.

Stop closure when another graph/search pass adds no legal TMDB nodes that explain the remaining BGM frontier. At that point, use `tmdb_absent_group` for BGM-mapped nodes that Bangumi has but TMDB does not expose. Use `supplemental_group` only for assignments that were already Local-to-Bangumi supplemental.

Do not convert BGM-mapped OVA/OAD/SP/movie/side-story nodes to supplemental to make validation pass. If a mapped bridge rule has a verifier issue, first repair TMDB ref, season number, episode range, number field, span/movie shape, or target-absent boundary.

## Episode Title Alignment

When the series title, translated title, or slug is ambiguous, episode titles are usually the next strongest evidence layer before choosing a TMDB ref or season. Read BGM `episode_title_cards_sample` from the subject card, then compare those BGM titles with the TMDB legal-node episode titles shown in the hydrated candidate's seasons.

The fixed layer tries to present one TMDB evidence view that aligns with the BGM assignment/source evidence, so Pi can keep recipe params language-agnostic. Compare the visible TMDB season/episode titles, order, and counts with the visible BGM cards. If a title view looks clearly off while the series anchor is otherwise strong, recipe validation or a targeted graph refresh can surface a better aligned view before declaring the BGM node absent.

Use this pattern:

- Regular sequence: compare first, last, and any distinctive middle BGM episode titles against the TMDB season episode titles. If the titles and episode count/order support the same season, submit one `episode_sequence` rule.
- Season split: if one TMDB series contains multiple seasons, choose the season whose episode title list and count align with the BGM subject, not merely the season number implied by the package name.
- Special/OVA/OAD: compare the specific BGM special title against TMDB season 0 or other exposed special nodes. If TMDB has the series but no legal node for that special, `tmdb_absent_group` should cover that BGM node without changing the rest of the plan.
- Movies: episode titles usually do not help; use movie title/original title/aliases/year/runtime evidence and map to `movie:<id>`.

Episode-title matches are semantic evidence for Pi. They still do not override the verifier: the output remains recipe params that compile to exposed `tv:<id>:SxxEyy` / `movie:<id>` legal nodes, or to `tmdb_target_absent` when the checked TMDB graph genuinely lacks the node.

## Recipe Params Shape

```json
{
  "version": 1,
  "summary": "Space Battleship Yamato 2199 Bangumi plan bridged to the matching TMDB series.",
  "rules": [
    {
      "name": "main_tv",
      "rule_type": "episode_sequence",
      "select_bgm": {
        "bangumi_subject_id": 100,
        "media_kind": "tv",
        "episode_type": "regular",
        "sort_range": "1-26"
      },
      "target_tmdb": {
        "tmdb_ref": "tv:45844",
        "season_number": 1,
        "episode_range": "1-26",
        "number_field": "sort"
      },
      "confidence": "High",
      "reason": "TMDB candidate 45844 has matching title/original-name/alias evidence and a Season 1 episode list matching the Bangumi regular sequence."
    },
    {
      "name": "missing_specials",
      "rule_type": "tmdb_absent_group",
      "select_bgm": {
        "bangumi_subject_id": 100,
        "episode_type": "special",
        "sort_range": "1-3"
      },
      "target_tmdb": {},
      "confidence": "High",
      "reason": "The matching TMDB series was hydrated, season 0 and episode-title checks expose no legal nodes for these BGM specials, so they are recorded as TMDB target absent."
    },
    {
      "name": "extras",
      "rule_type": "supplemental_group",
      "select_bgm": {},
      "target_tmdb": {},
      "confidence": "Medium",
      "reason": "The accepted BGM compiled plan classifies these sources as supplemental, so the bridge keeps them unmapped."
    }
  ]
}
```

## Rule Types

- `episode_sequence`: BGM regular episode range maps to a TMDB TV season range.
- `movie`: BGM movie assignment maps to one `movie:<id>` target.
- `special_sequence`: BGM specials, OVA, or OAD sequence maps to TMDB season 0 or another explicit season.
- `span`: one BGM assignment covering multiple episodes maps to multiple TMDB TV nodes. If TMDB models that whole BGM span as one movie, use a `movie` rule to map the span assignment to the single `movie:<id>` node.
- `tmdb_absent_group`: BGM-mapped assignments remain present in the verified bridge but are marked `tmdb_target_absent` because TMDB does not expose legal nodes for them.
- `supplemental_group`: non-Bangumi or supplemental BGM assignments remain unmapped.

## Contract Rules

- Names, slugs, aliases, overviews, and years are evidence for semantic choice only.
- Output subjects are IDs and rule fields: `tmdb_ref`, `season_number`, `episode_range`, `episode_offset`, and selectors.
- Bare `tmdb:SxxEyy`, titles, URLs, and slugs are invalid targets.
- Two source paths mapping to the same compiled TMDB legal node is invalid unless a future contract explicitly adds multi-part support.
- A single BGM span may map to one TMDB movie node when TMDB models the whole span as a movie rather than individual TV episodes.
- Supplemental, non-Bangumi, needs-more-evidence, and fail-closed BGM assignments remain outside TMDB node mappings.
- `supplemental_group` is for Local-to-Bangumi supplemental files. For BGM-mapped episodes that TMDB lacks, use `tmdb_absent_group`; it means Bangumi has a node but TMDB does not expose a matching legal node.
- If validation returns `review`, add concrete semantic evidence or fail closed. Review is not accepted.
- Repeated recap/summary/CM/bonus-title searches are usually weaker than targeted checks against the TMDB legal graph. Once the graph lacks the needed BGM-mapped node after targeted checks, use `tmdb_absent_group` rather than searching variants of the same missing item.
- When BGM and TMDB episode titles are available, use them as stronger evidence than a fuzzy series title and mention the episode-title/order/count evidence in the rule `reason`.

If evidence is insufficient to choose between TMDB candidates, fail closed with the conflicting IDs/titles and the exact missing evidence instead of guessing. One otherwise identified BGM episode/special lacking a TMDB legal node is better recorded as `tmdb_target_absent` than treated as global failure.
