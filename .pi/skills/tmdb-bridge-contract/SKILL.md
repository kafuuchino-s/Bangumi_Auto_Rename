---
name: tmdb-bridge-contract
description: Use when bridging an accepted Local-to-Bangumi compiled plan to TMDB with recipe params, especially when TMDB title/slug/original-name evidence or verifier repair is unclear.
---

# TMDB Bridge Contract

The final output for this stage is Python-verifier accepted BGM-to-TMDB recipe params submitted with `submit_bgm_to_tmdb_bridge_recipe_params`, or a safe fail-closed result. This stage is dry-run only: do not move, copy, link, rename, or write final media files.

Use recipe params as the primary workflow. Do not hand-write per-source `source_path -> tv:<id>:SxxEyy` mappings for normal TV episode sequences. Python compiles compact rules into raw node mappings, then the existing verifier checks coverage, duplicates, legal nodes, spans, and supplemental boundaries.

Bangumi identity comes from the accepted compiled plan: `source_path`, `bangumi_subject_id`, `episode_id`, `sort`, `ep`, title, media kind, episode type, and span episode IDs. TMDB identity in recipe params is `tmdb_ref` (`tv:<tmdb_id>` or `movie:<tmdb_id>`), plus season/range fields when needed. Compiled legal nodes are `tv:<tmdb_id>:SxxEyy` for TV episodes and `movie:<tmdb_id>` for movies.

TMDB names are semantic evidence, not output identity. Use `display_title`, `original_name`, `original_title`, aliases, year, overview, season cards, episode cards, and URL slug text such as `45844-space-battleship-yamato-2199` to decide whether a TMDB candidate matches the Bangumi plan. The verifier accepts only TMDB IDs and legal nodes after Python compilation.

## Workflow

1. Read grouped BGM subject cards and assignments with `get_bgm_to_tmdb_bridge_context`. Accepted Local-to-Bangumi artifacts can be reused directly; do not rerun Local-to-Bangumi.
2. Search TMDB candidates by Bangumi/local human titles. Compare candidate cards by ID, title, original name, aliases, year, overview, season names, and episode lists.
3. Draft compact recipe params. Prefer one rule for a contiguous TV sequence, one rule for a movie, one rule for a special sequence, one span rule for one source covering multiple episodes, and one supplemental rule for extras.
4. Call `validate_bgm_to_tmdb_bridge_recipe_params`. Validation hydrates declared `tmdb_ref` values, compiles params to a raw bridge draft, runs the node verifier, and returns repair hints or review warnings.
5. Repair only the targeted rule named by the verifier. Do not restart broad search unless the verifier asks for missing TMDB evidence.
6. After validation returns `accepted:true`, call `submit_bgm_to_tmdb_bridge_recipe_params` with the same params.

Search is for finding plausible TMDB refs, not for exhaustively proving every recap/summary/CM/bonus title. After a plausible series/movie candidate is found and hydrated, validate a recipe. If validation shows a mapped Bangumi special has no concrete TMDB legal node, fail closed with that exact missing-node evidence instead of searching more title variants.

Raw `validate_bgm_to_tmdb_bridge` and `submit_bgm_to_tmdb_bridge` are debug/fallback tools only. Use them for exact edge cases or inspecting generated JSON, not as the normal path.

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
- `span`: one BGM assignment covering multiple episodes maps to multiple TMDB TV nodes.
- `supplemental_group`: non-Bangumi or supplemental BGM assignments remain unmapped.

## Contract Rules

- Names, slugs, aliases, overviews, and years are evidence for semantic choice only.
- Output subjects are IDs and rule fields: `tmdb_ref`, `season_number`, `episode_range`, `episode_offset`, and selectors.
- Do not output bare `tmdb:SxxEyy`, a title, a URL, or a slug as the target.
- Do not map two source paths to the same compiled TMDB legal node unless a future contract explicitly adds multi-part support.
- Do not map supplemental, non-Bangumi, needs-more-evidence, or fail-closed BGM assignments to TMDB nodes.
- If validation returns `review`, add concrete semantic evidence or fail closed. Review is not accepted.
- Do not spend the turn budget on repeated recap/summary/CM/bonus-title searches. Once TMDB legal graph lacks the needed node, fail closed rather than searching variants of the same missing item.

If evidence is insufficient to choose between TMDB candidates, fail closed with the conflicting IDs/titles and the exact missing evidence instead of guessing.
