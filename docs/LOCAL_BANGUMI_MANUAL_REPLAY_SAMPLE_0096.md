# Local to Bangumi Manual Replay: sample_0096

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

## Required Artifact

`sample_id`: `sample_0096_vcb_studio_overlord`

`manual_human_path`:

1. Read the package as an `OVERLORD` franchise release:
   - two `Gekijouban Soushuuhen OVERLORD` compilation movie files, labeled part 1 `Fushisha no Ou` and part 2 `Shikkoku no Senshi`.
   - `OVERLORD` TV 01-13.
   - `OVERLORD II` TV 01-13.
   - `OVERLORD III` TV 01-13.
   - one `OVERLORD Ple Ple Pleiades` chibi/extra file.
   - large SP/previews/menu/CM/PV/NCOP/NCED groups for the movie and TV seasons.
2. Search/inspect the `OVERLORD` family surface.
3. Map the three TV spans to their inspected 13-episode subjects.
4. For the two movie files, do not map the two-file parent local locator to one movie target. Split the parent by visible local episode slices and map each slice to the matching inspected one-item movie subject.
5. Treat package previews/menu/CM/PV/NCOP/NCED as supplemental/non-Bangumi material.
6. Treat numbered SP groups as supplemental/target_absent only after citing inspected same-series target-side support showing no corresponding positive SP/OAD item.
7. Map `Ple Ple Pleiades` only if a visible inspected target supports that chibi/extra title; otherwise fail or exclude with a concrete visible-evidence reason.

`agent_actual_trace`:

- Focused run output: `tests/sample_pool/generated/local_bangumi_mapping_sample_0096_spot_check_20260518`
- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> inspect -> submit -> submit -> submit -> submit -> submit`
- `turn_count=11`
- `submit_rejection_count=6`
- `submit_rejection_issue_counts={composite_feature_shape_invalid: 4, count_mismatch: 2, duplicate_target: 2, coverage_missing: 2, locator_not_found: 1}`
- `near_turn_limit_unhealthy_count=1`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`

`divergence_point`:

The first useful divergence is after the initial submit rejection on the two-file `Gekijouban Soushuuhen OVERLORD main-episodes` locator. A human would immediately split the two visible local slices and search/inspect the second movie title (`Shikkoku no Senshi`) rather than repeatedly resubmitting the parent local locator against a one-item target. The Agent eventually found the correct structure, but only after several shape/count/duplicate rejections and near turn-cap pressure.

`gap_category`: `verifier_feedback`

`is_generic_architecture_gap`: `true`

`proposed_fix_layer`:

Improve generic submit repair feedback for `composite_feature_shape_invalid` / count-mismatch cases where:

- the local side is an episode-like multi-file locator,
- the selected target side has fewer visible target items, often one,
- visible local sub-locators exist,
- representative labels contain distinct title-tail tokens.

The repair agenda should expose compact split-first guidance and title-tail search queries for the non-mapped or candidate local slices. It should also avoid carrying huge repeated locator-detail payloads into every turn; saved work units and repair rows should be summarized enough for the Agent to act without ballooning context.

This fix must remain an evidence/shape prompt improvement. It must not choose the second movie target, split the package automatically, or mark any SP group target_absent.

`fixed_layer_boundary_check`:

- Allowed: expose legal local slice locators, count-compatible pairings, local representative labels, and title-tail search queries derived from visible local filenames.
- Allowed: compact repeated mechanical feedback and saved work-unit summaries.
- Forbidden: hard-code `OVERLORD`, movie titles, Bangumi subject ids, or a file-to-target mapping.
- Forbidden: fixed layer decides whether a movie part, chibi extra, numbered SP group, or target_absent outcome is semantically correct.

`rerun_gate`:

After the generic feedback/context fix:

```powershell
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_mapping_sample_pool.py
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0096 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_manual_replay_gate_20260518
```

Focused acceptance target:

- preferred: `accepted_contract_ok=true`, no unsafe accepted, lower submit-loop pressure than the current 7-submit near-cap run.
- acceptable intermediate only if not accepted: no naked `budget_exhausted`, no repeated same submit rejection, `legacy_subagent_call_count=0`, and fail_closed must name a concrete unresolved movie-part/SP evidence blocker.

## Spot Check Of Current Accepted Run

The current accepted mapping appears semantically safe:

- `OVERLORD` 01-13 -> `target://bangumi/112146-overlord/episodes/1-13`
- `OVERLORD II` 01-13 -> `target://bangumi/211027-overlord-第二季/episodes/1-13`
- `OVERLORD III` 01-13 -> `target://bangumi/242170-overlord-第三季/episodes/1-13`
- `Gekijouban Soushuuhen OVERLORD` part 1 -> `target://bangumi/194036-剧场版总集篇-overlord-不死者之王/episode/1`
- `Gekijouban Soushuuhen OVERLORD` part 2 -> `target://bangumi/198968-剧场版总集篇-overlord-漆黑的英雄/episode/1`
- Historical run note, now superseded: `OVERLORD Ple Ple Pleiades`
  previously mapped to `target://bangumi/193953-play-play-昴宿星团/episode/1`,
  but the current policy treats that as unsafe without stronger compilation or
  item-level evidence.
- movie SP/theater manners and TV SP/previews/menu/CM/PV/NCOP/NCED groups -> supplemental/non-Bangumi/target_absent with inspected same-series support where required.

Safety conclusion:

- No unsafe accepted found in the current accepted run.
- This sample still drives a generic runtime-health fix because the route to acceptance had too much submit shape repair and reached near turn-cap pressure.

## 2026-05-18 Generic Fix Validation

Implemented generic changes:

- Removed work-specific query aliases from the fixed layer.
- Added split-first repair feedback for count/composite shape mismatches.
- Added title-tail search hints from visible local slice labels.
- Compacted cognitive workspace and submit repair payloads.
- Promoted unsearched/unbridged title-tail exclusion from passive diagnostics to blocking evidence-surface feedback.

Focused rerun:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0096 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_manual_replay_gate5_20260518
```

Result:

- `status=fail_closed`
- `summary=agent_fail_closed_from_submit`
- `accepted_contract_ok=false`
- `final_verifier_passed=true`
- `turn_count=6`
- `tool_sequence=search -> inspect -> submit -> submit -> submit -> submit`
- `submit_rejection_count=3`
- `near_turn_limit_unhealthy_count=0`
- `legacy_subagent_call_count=0`

Current safe-fail blocker:

- Historical run note, now superseded: `OVERLORD Ple Ple Pleiades` previously
  mapped to `target://bangumi/193953-play-play-昴宿星团/episode/1`; current
  handling sends this singleton to `manual_review` instead.
- The two `Gekijouban Soushuuhen OVERLORD` movie files no longer get unsafe accepted as supplemental/target_absent.
- They end as `fail_closed` because the searched title-tail aliases did not expose a reliable visible Bangumi owner after removing the previous work-specific alias bridge.

This is not final convergence acceptance. It is a structural improvement from unsafe/near-cap submit repair into a concrete evidence blocker. A future generic fix should improve romanized Japanese movie-title bridge evidence without reintroducing work-specific aliases.

## 2026-05-18 Follow-up Generic Fix Validation

Implemented additional generic evidence-surface changes:

- Search pairing now separates visible title tokens from source-query provenance, so broad search terms do not make every returned subject look equally relevant.
- Pairing candidates include generic media-form overlap (`movie`, `recap`, `OVA/OAD`) and translated title-token equivalents for common romanized Japanese title words.
- Excluding or fail-closing a singleton/main title with visible title-tail pairing candidates now returns a mechanical repair; fail_closed must address the listed candidate target/episode evidence directly.
- `mapped_composite_feature` now requires a single-file feature/composite shape cue instead of allowing any singleton local file to cover a multi-episode target span.

Focused rerun:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample 0096 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_pairing_failclosed_address_gate_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `legacy_subagent_call_count=0`
- `tool_rejection_count=0`
- `near_turn_limit_unhealthy_count=1`
- `submit_rejection_count=7`

Spot check:

- `Gekijouban Soushuuhen OVERLORD` part 1 maps to `target://bangumi/194036-剧场版总集篇-overlord-不死者之王/episode/1`.
- `Gekijouban Soushuuhen OVERLORD` part 2 maps to `target://bangumi/198968-剧场版总集篇-overlord-漆黑的英雄/episode/1`.
- Historical run note, now superseded: `OVERLORD Ple Ple Pleiades` previously
  mapped to `target://bangumi/193953-play-play-昴宿星团/episode/1`; current
  handling sends this singleton to `manual_review` instead.
- `OVERLORD`, `OVERLORD II`, and `OVERLORD III` TV spans map to their 13-episode subjects.
- Movie SP/theater-manners and TV SP/previews/menu/CM/PV/NCOP/NCED groups are supplemental with same-series support.

Safety conclusion:

- No unsafe accepted found in this focused run.
- Runtime health is still not acceptable as final convergence proof because the sample accepted on turn 12 with 7 submit rejections. The remaining generic gap is repair-feedback compactness/action ordering, not target semantics.

## 2026-05-18 Boundary Correction: Remove Fixed-Layer Title Bridges

Architecture review found that the previous follow-up crossed the plan boundary:

- fixed-layer title-token equivalents such as romanized title words to translated title tokens were sample/work-specific semantic bridges;
- a specific title/franchise token exclusion in singleton pairing scoring was also a fixed-layer semantic patch;
- source-query-only and media-form-only pairing candidates were being promoted into hard submit blockers.

Correction:

- Removed fixed-layer translated title-token equivalents.
- Removed the specific title/franchise token exception.
- Kept source-query provenance and media-form overlap as Agent-visible evidence leads only.
- Restricted `excluded_local_has_visible_title_pairing_target` hard rejection to visible title-tail overlap, not source-query-only candidates.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_mapping_sample_pool.py
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample 0096 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_no_fixed_title_bridge_repair_gate_20260518
```

Result:

- `status=fail_closed`
- `summary=agent_fail_closed_from_submit`
- `accepted_contract_ok=false`
- `final_verifier_passed=true`
- `turn_count=6`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> submit`
- `submit_rejection_count=2`
- `near_turn_limit_unhealthy_count=0`
- `legacy_subagent_call_count=0`
- `tool_rejection_count=0`

Current blocker:

- The package is mechanically ready as fail_closed.
- `OVERLORD Ple Ple Pleiades` remains `fail_closed` because no visible lexical/source-query provenance safely bridges the local title to the Bangumi target without reintroducing fixed-layer translation knowledge.
- The second movie part is not accepted as convergence proof in this run; the remaining generic gap is still evidence-surface/provenance quality for romanized local titles versus translated Bangumi titles, and must not be solved by hard-coded title equivalents.

Conclusion:

- This focused gate is healthy and boundary-correct, but not convergence complete.
- Future fixes must improve generic evidence surface, for example by exposing official aliases/source-query provenance from actual search results, not by adding work-specific token dictionaries or fixed-layer semantic target selection.

## 2026-05-18 Generic Evidence-Surface Delta: Subject Infobox Alias Facts

`divergence_point`:

- The Agent can inspect a Bangumi target, but HumanCaseAgent currently keeps only `title`, `name`, and `name_cn` as visible target alias markers on its direct search/inspect path.
- Bangumi subject detail responses can include `infobox` entries such as official aliases or alternate titles, but this path does not normalize those facts into `BangumiSubjectCard.infobox_facts`, inspect observations, or target locator alias markers.
- As a result, the Agent may be unable to use provider-visible official alias evidence even after doing the human-like action of inspecting the subject.

`gap_category`: `evidence_surface`

`is_generic_architecture_gap`: true

`proposed_fix_layer`:

- Parse compact subject infobox facts from the Bangumi subject object in the HumanCaseAgent API-card builder.
- Expose title/name/alias-like infobox values in `inspect(..., scope=["aliases","details","surface"])` observations.
- Register those provider-returned alias values as target locator markers so existing mechanical bridge checks can see official alias provenance.

`fixed_layer_boundary_check`:

- Allowed: copy and compact provider-returned subject facts into the Agent-facing evidence surface; use those visible facts in existing locator/support/provenance checks.
- Forbidden: add title-token translations, work-specific aliases, Bangumi ids, file-to-target mappings, or automatic target/special/target_absent choices.
- Boundary expectation: if the provider does not expose a useful alias fact, the sample must remain fail_closed with a concrete blocker rather than being forced through.

`rerun_gate`:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_mapping_sample_pool.py
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample 0096 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_infobox_alias_surface_gate_20260518
```

Validation result:

- `status=fail_closed`
- `summary=unresolved_submit_repair`
- `accepted_contract_ok=false`
- `final_verifier_passed=false`
- `turn_count=12`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> inspect -> submit -> submit -> search -> submit -> submit -> submit`
- `submit_rejection_count=7`
- `tool_rejection_count=1` (`all_query_variants_already_searched`)
- `near_turn_limit_unhealthy_count=1`
- `legacy_subagent_call_count=0`

New divergence:

- The infobox alias surface is structurally valid, but it did not resolve this sample.
- The first unhealthy blocker is now noise promotion: isolated tail-token matches such as one local title word matching unrelated one-item targets are elevated to `excluded_local_has_visible_title_pairing_target`.
- That makes low-relevance search noise act like a hard mechanical submit blocker, contrary to the search/noise layering goal.

Follow-up `gap_category`: `evidence_surface`

Follow-up generic fix:

- Keep title-tail pairing candidates visible to the Agent.
- Downgrade weak single-token tail matches without any same-title context or multi-token tail support so they do not become hard submit blockers.
- Do not choose the target, do not add aliases, and do not encode any work-specific title translation.

Validation result after noise hard-blocker narrowing:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample 0096 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_pairing_noise_suppression_gate_20260518
```

- `status=fail_closed`
- `summary=unresolved_submit_repair`
- `accepted_contract_ok=false`
- `final_verifier_passed=false`
- `turn_count=12`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> search -> submit -> submit -> submit -> submit -> submit -> submit`
- `submit_rejection_count=8`
- `near_turn_limit_unhealthy_count=1`
- `legacy_subagent_call_count=0`
- `excluded_local_has_visible_title_pairing_target` no longer appears in the final issue counts.

New divergence:

- The correct evidence lead for the second movie part is visible as a `title_plus_source_query` / media-form pairing, but weaker unrelated single-token title hits can still rank ahead of it in the pairing option list.
- The Agent keeps resubmitting instead of stabilizing the work-unit decision, which leaves `target_episode_surface_missing`, `numbered_special_exclusion_needs_target_evidence`, and `fail_closed_singleton_with_unassigned_visible_target_items` in the final readiness summary.

Next generic fix:

- Re-rank pairing options so a candidate with non-tail title context plus source-query/form evidence outranks isolated one-word title-tail hits.
- This remains evidence ordering only. The fixed layer still does not choose the movie target or encode title translations.

Validation result after pairing re-rank:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample 0096 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_pairing_rank_gate_20260518
```

- `status=fail_closed`
- `summary=unresolved_submit_repair`
- `accepted_contract_ok=false`
- `final_verifier_passed=false`
- `turn_count=12`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> submit -> submit -> inspect -> submit -> submit -> submit -> submit`
- `submit_rejection_count=8`
- `tool_rejection_count=0`
- `near_turn_limit_unhealthy_count=1`
- `legacy_subagent_call_count=0`

Observed improvement:

- The final blocker is reduced to `OVERLORD Ple Ple Pleiades` plus a duplicate-target conflict caused by a wrong movie-part target choice.
- The Pleiades target can be inspected and surfaced, but `mapped_target_title_bridge_missing` still reports empty `target_source_query_texts`.

New divergence:

- Source-query provenance is not reliably retained when the same Bangumi subject is first added by a broad query and then matched again by a more specific query in the same `search` tool call.
- Without that provenance, the fixed layer cannot expose the exact search query as support, and the Agent sees a target title that lacks lexical overlap with the local title.

Next generic fix:

- Merge source-query provenance for subjects already added earlier in the same `search` invocation, not just for subjects that were present in the workspace before the tool call.
- This is still mechanical evidence bookkeeping; it does not invent aliases or choose the target.

Validation result after search provenance merge:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample 0096 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_search_provenance_merge_gate_20260518
```

- `status=fail_closed`
- `summary=unresolved_submit_repair`
- `accepted_contract_ok=false`
- `final_verifier_passed=false`
- `turn_count=12`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> submit -> submit -> submit -> submit -> submit -> search -> submit`
- `submit_rejection_count=8`
- `tool_rejection_count=1` (`turn_budget_requires_resolution`)
- `near_turn_limit_unhealthy_count=1`
- `legacy_subagent_call_count=0`

Observed improvement:

- Pleiades source-query provenance now appears in target-side evidence (`target_source_query_texts` includes `OVERLORD Ple Ple Pleiades`).
- The movie part mappings became mechanically OK in this run.

New divergence:

- Near the turn cap, the Agent wrote a nonexistent target locator, `target://bangumi/2784-overlord-ple-ple-pleiades`, for the Pleiades work unit.
- The submit feedback reports `locator_not_found`, but does not provide visible target locator candidates derived from the raw target text and already visible query provenance.

Next generic fix:

- Add target-side candidate locator suggestions for `locator_not_found`, using only visible target locators, visible title/alias markers, and source-query provenance.
- Do not canonicalize or auto-select the candidate; the Agent must resubmit the corrected locator or fail_closed.

## 2026-05-19 Tooling Gate And Latest Focused Result

Tooling fixed before further convergence work:

- `tools/run_local_bangumi_human_gate.py` now creates its output directory before running and only summarizes final `sample_*.json` results, not progress files or generated trace summaries.
- `tools/summarize_local_bangumi_human_trace.py` now emits the runtime review fields required by the plan: status, accepted contract, verifier result, tool sequence, turn count, submit rejection count, loop health, legacy subagent count, provider input cache ratio, local response cache file count, divergence point, and readiness summary.
- `tools/scaffold_local_bangumi_manual_replay.py` now uses the plan's `gap_category` enum.
- `tools/scan_local_bangumi_boundary_risks.py` now scans untracked new files as well as git diff added lines, so new fixed-layer files are not silently skipped.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py --json
```

Result:

- unit: `31 passed`
- compile: passed
- boundary scan: `finding_count=0`

Focused rerun:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_tool_fixed_gate_20260519
```

Trace summary:

- `status=fail_closed`
- `summary=unresolved_submit_repair`
- `accepted_contract_ok=false`
- `final_verifier_passed=false`
- `turn_count=12`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> inspect -> inspect -> inspect -> submit -> submit -> submit -> submit`
- `submit_rejection_count=6`
- `near_turn_limit_unhealthy_count=1`
- `legacy_subagent_call_count=0`
- `tool_rejection_count=0`
- `provider_cached_input_ratio=0.10084942173760517`
- `attention_focus_change_count=0`
- `agenda_open_count=1`
- `agenda_closed_count=0`

Current mechanical readiness blockers:

- `composite_feature_shape_invalid` on the two-file `Gekijouban Soushuuhen OVERLORD main-episodes part 1-2` work unit mapped to a one-item target span.
- `mapped_title_season_mismatch` on `OVERLORD Ple Ple Pleiades main` mapped to a target with explicit season suffix `4`.

`divergence_point`:

- After the Agent inspected multiple candidate targets, the workspace did not convert the visible mechanical repair agenda into a closed work-unit decision. It returned to repeated `submit` calls near the turn cap.
- The fixed layer correctly rejected the final package for mechanical reasons, but runtime health is still poor: no focus change, no agenda closure, and four consecutive submit turns at the end.

`gap_category`: `state_structure`

`is_generic_architecture_gap`: `true`

`proposed_fix_layer`:

- Improve durable workspace agenda/focus updates around submit repairs so visible mechanical blockers stay attached to the named work units until the Agent either fixes them or explicitly fail-closes them.
- Keep the fixed layer limited to mechanical feedback. It must not choose the second movie target, choose a Pleiades target, declare target_absent, or auto-split the parent locator.

`fixed_layer_boundary_check`:

- Allowed: preserve and compact repair agenda, focus, and readiness deltas; expose visible local slice options and inspected target surface facts; mark repeated submit-without-agenda-progress as runtime unhealthy.
- Forbidden: encode `OVERLORD`, title bridges, Bangumi ids, or file-to-target rules.

Conclusion:

- Tooling is now a stable precondition for the workflow.
- `sample_0096` remains not converged. The next code change must be a generic workspace/agenda durability fix, not another target-specific evidence patch.

## 2026-05-19 Durable Repair Agenda Gate

Implemented generic state-structure changes:

- `submit` rejection now creates durable `REPAIR-*` investigation agenda items.
- Each active repair agenda item carries `blocking_issue`, `locators`, `required_next_action`, and `closure_condition`.
- The next turn's `CASE_STATE.case_memory.active_repair_agenda` is displayed ahead of ordinary observations.
- Blocking work units record the same issue/action/closure fields in the cognitive workspace.
- Visible repair options are derived from the latest submit observation for prompt display only; they are not accepted through the `note` schema.
- The OpenAI strict schema error caused by `visible_options` in the `note` tool schema was corrected by keeping `visible_options` out of `InvestigationAgendaItem`.
- `coverage_missing` repair rows now preserve list locators as locators instead of stringifying them.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py --json
```

Result:

- unit: `35 passed`
- compile: passed
- boundary scan: `finding_count=0`
- generated `note` tool schema no longer contains `visible_options`

Focused rerun:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_durable_repair_agenda_gate3_20260519
```

Trace summary:

- `status=fail_closed`
- `summary=unresolved_submit_repair`
- `accepted_contract_ok=false`
- `final_verifier_passed=false`
- `turn_count=12`
- `tool_sequence=search -> inspect -> submit -> submit -> submit -> inspect -> submit -> submit -> inspect -> submit -> inspect -> submit`
- `submit_rejection_count=7`
- `legacy_subagent_call_count=0`
- `tool_rejection_count=0`
- `near_turn_limit_unhealthy_count=1`
- `attention_focus_change_count=7`
- `agenda_open_count=2`
- `agenda_closed_count=3`

Current blocker:

- The run is not a naked `budget_exhausted` or provider/schema failure.
- The final active repair agenda is `OVERLORD Ple Ple Pleiades main` with `fail_closed_title_tail_bridge_uninspected`.
- Latest `target_surface_actions` are visible but noisy/unrelated candidates; the Agent did not reach an accepted or verifier-passed fail_closed resolution within the turn budget.

`divergence_point`:

- The durable agenda mechanism now works: focus and agenda changed across turns, and several repair agenda items closed.
- The remaining divergence is that active repair agenda closure can still consume too many submit turns before the Agent makes a terminal semantic decision for a single unresolved companion/special title. This is a runtime health/action-ordering problem, not a fixed-layer target selection problem.

`gap_category`: `state_structure`

`is_generic_architecture_gap`: `true`

`proposed_fix_layer`:

- Keep the durable active repair agenda implementation.
- Next generic improvement should make near-cap active repair agenda handling stricter: when one blocker remains and no new target evidence is added, the prompt/tool guard should force either an evidence-producing action from listed `target_surface_actions` or a concrete `fail_closed` for that exact work unit, instead of additional broad submit attempts.

`fixed_layer_boundary_check`:

- Allowed: agenda/action-ordering, loop health, readiness/focus closure, and mechanical prompt priority.
- Forbidden: choosing the Pleiades target, adding title bridges, hard-coding Bangumi ids, or deciding target_absent/supplemental in fixed code.

## 2026-05-19 Repair Finalization Gate

Implemented final generic state-structure changes:

- Added a prompt-visible `near_cap_repair_finalization_guard` for active submit repair agenda items.
- Near-cap non-accepted submit attempts now require exact `fail_closed` coverage for every open active repair locator unless the package actually passes mechanical verification.
- The guard reports missing exact repair locators and remains a runtime-health/action-ordering check; it does not choose target, special, target_absent, or split semantics.
- Tightened the generic sibling-slice blocker: an episode slice beside a mapped sibling cannot be cleared by a broad `extra` / `recap` / `overflow` reason. Only hard non-owner reasons such as duplicate/copy/menu/preview/packaging bypass that blocker; otherwise the Agent must map a visible target, give a hard non-owner reason, or exact-fail-close the slice.

Intermediate spot-check finding:

- One accepted replay mapped the first recap movie correctly but cleared `[02(Shikkoku no Senshi)]` as supplemental/overflow.
- That accepted result was rejected by manual spot check and drove the generic sibling-slice blocker above.
- No Overlord title, Bangumi subject id, or file-to-target rule was added.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py --json
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_repair_finalization_gate_20260519
```

Result:

- unit: `37 passed`
- compile: passed
- boundary scan: `finding_count=0`
- focused gate: `status=accepted`, `accepted_contract_ok=true`, `final_verifier_passed=true`
- `turn_count=7`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> submit -> submit`
- `submit_rejection_count=3`
- `tool_rejection_count=0`
- `legacy_subagent_call_count=0`
- `near_turn_limit_unhealthy_count=0`
- `stall_warning_count=0`
- `agenda_open_count=0`, `agenda_closed_count=3`

Spot check:

- `OVERLORD` 01-13 -> `target://bangumi/112146-overlord/episodes/1-13`
- `OVERLORD II` 01-13 -> `target://bangumi/211027-overlord-第二季/episodes/1-13`
- `OVERLORD III` 01-13 -> `target://bangumi/242170-overlord-第三季/episodes/1-13`
- `Gekijouban Soushuuhen OVERLORD` part 1 -> `target://bangumi/194036-剧场版总集篇-overlord-不死者之王/episode/1`
- `Gekijouban Soushuuhen OVERLORD` part 2 -> `target://bangumi/198968-剧场版总集篇-overlord-漆黑的英雄/episode/1`
- Historical run note, now superseded: `OVERLORD Ple Ple Pleiades`
  previously mapped to `target://bangumi/193953-play-play-昴宿星团/episode/1`;
  current handling sends this singleton to `manual_review`.
- movie/TV SP, preview, menu, CM, PV, NCOP/NCED groups remain supplemental with inspected same-series support.

Safety conclusion:

- No unsafe accepted mapping found in the final focused replay.
- The final fix is a generic active-repair/finalization and sibling-slice evidence blocker, not a sample-specific mapping rule.

## 2026-05-19 Final Audit Rerun

A fresh focused rerun exposed one remaining state-contract mismatch:

- Guard logic already required every open repair locator to be closed near the turn cap.
- The prompt/rejection text still said to close "one of" the finalization locators.
- That wording let the Agent exact-fail-close only one open locator and hit `unresolved_submit_repair`.

Final generic correction:

- Aligned prompt and rejection text with the enforced all-open-locators contract: submit exact `fail_closed` rows for every listed `finalization_target_locator`, unless the submitted package actually passes mechanical verification.
- Added a unit assertion that rejects the old singular `"one of finalization_target_locators"` wording.
- This remains action-ordering/runtime-health guidance only; fixed code still does not choose target, special/OVA/SP, target_absent, or split semantics.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py --json
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_repair_finalization_gate_final2_20260519
```

Result:

- unit: `38 passed`
- compile: passed
- boundary scan: `finding_count=0`
- focused gate: `status=accepted`, `accepted_contract_ok=true`, `final_verifier_passed=true`
- `turn_count=8`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> submit -> note -> submit`
- `submit_rejection_count=3`
- `tool_rejection_count=0`
- `legacy_subagent_call_count=0`
- `near_turn_limit_unhealthy_count=0`
- `stall_warning_count=0`
- `agenda_open_count=0`, `agenda_closed_count=3`

Final spot check:

- `OVERLORD` 01-13 -> `target://bangumi/112146-overlord/episodes/1-13`
- `OVERLORD II` 01-13 -> `target://bangumi/211027-overlord-第二季/episodes/1-13`
- `OVERLORD III` 01-13 -> `target://bangumi/242170-overlord-第三季/episodes/1-13`
- `Gekijouban Soushuuhen OVERLORD` part 1 -> `target://bangumi/194036-剧场版总集篇-overlord-不死者之王/episodes/1-1`
- `Gekijouban Soushuuhen OVERLORD` part 2 -> `target://bangumi/198968-剧场版总集篇-overlord-漆黑的英雄/episode/1`
- Historical run note, now superseded: `OVERLORD Ple Ple Pleiades`
  previously mapped to `target://bangumi/193953-play-play-昴宿星团/episode/1`;
  current handling sends this singleton to `manual_review`.
- SP/previews/menu/CM/PV/NCOP/NCED groups remain excluded/supplemental with visible support or mechanical verification.

Safety conclusion:

- The current focused replay has no open repair agenda, no tool rejection, and no unsafe accepted recap split observed.
- The final code change is generic wording/contract alignment around active repair finalization.

## 2026-05-19 No-Previous Structural Repair Replay

Question:

- After removing `previous_response_id`, `cache_audit_no_previous_20260519` failed closed while an earlier run named `cache_audit_previous_response2_20260519` had accepted. This looked like a possible request/cache structure regression.

Manual replay conclusion:

- This was not caused by `previous_response_id`, provider cache, or byte-level request prefix instability.
- The `previous_response2` run is not valid evidence for response chaining because the provider rejected the sent `previous_response_id` once and the run continued on the ordinary HTTP Responses path.
- The behavioral difference was the submitted repair path for `OVERLORD Ple Ple Pleiades main`:
  - accepted run: the Agent eventually mapped it to subject `193953`, episode 1;
  - no-previous failed run: the Agent drifted to a season-suffixed `Play Play` target, `234089`, then got pulled back by `mapped_title_season_mismatch` / `count_mismatch` but did not receive a good enough repair search lead before hitting fail-closed.

Root cause:

- `mapped_title_season_mismatch` repair feedback was too broad and not sufficiently actionable for unseasoned local titles mapped to season-suffixed targets.
- It exposed unrelated same-franchise/noisy alternates too easily and did not derive a seasonless search query from the selected target title.
- The structure problem was repair guidance and candidate surfacing, not the request cache structure.

Generic correction:

- Added a seasonless target-title query helper so a selected target such as `Play Play Stars 4` produces a search lead like `Play Play Stars`.
- Added same-title-family overlap filtering for visible unseasoned alternates so unrelated subjects found only through broad franchise terms do not become primary repair leads.
- Promoted `mapped_title_season_mismatch_repairs[*].search_queries_to_try` into the active repair agenda.

Boundary check:

- Allowed: derive evidence-search queries from the selected visible target title; expose same-family visible alternates; ask the Agent to inspect/search or fail-close.
- Forbidden and not done: hard-code `OVERLORD`, `Ple Ple Pleiades`, Bangumi subject ids, aliases, or a file-to-target mapping.
- The fixed layer still does not choose the Pleiades target; it only makes the season-mismatch repair path actionable.

Focused rerun:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_seasonless_repair_gate_20260519
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `turn_count=8`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> search -> submit -> submit`
- `submit_rejection_count=3`
- `legacy_subagent_call_count=0`
- `tool_rejection_count=0`
- `near_turn_limit_unhealthy_count=0`
- `stall_warning_count=0`

Spot check:

- `OVERLORD` TV span -> subject `112146`, episodes 1-13.
- `OVERLORD II` TV span -> subject `211027`, episodes 1-13.
- `OVERLORD III` TV span -> subject `242170`, episodes 1-13.
- recap movie part 1 -> subject `194036`, episode 1.
- recap movie part 2 -> subject `198968`, episode 1.
- `OVERLORD Ple Ple Pleiades main` -> subject `193953`, episode 1.
- SP/previews/menu/CM/PV/NCOP/NCED groups remain supplemental or target_absent with inspected same-series support where required.

Validation:

```powershell
.venv\Scripts\python.exe -m compileall src\ai src\rename\case_agent src\config tools\run_local_bangumi_human_gate.py tools\run_local_bangumi_mapping_sample_pool.py
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py tests\test_local_bangumi_sample_runner.py tests\test_ai_models.py tests\test_config_local_bangumi_case_agent_defaults.py -q
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\run_local_bangumi_mapping_sample_pool.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py --json
```

Result:

- compile: passed
- focused pytest: `65 passed`
- boundary scan: `finding_count=0`

## 2026-05-19 Risk-Fix Replay: Cache Session And Numbered SP Guard

Implemented risk fixes after request-structure review:

- Removed provider-facing `session_id` forwarding from HumanCaseAgent and OrchestratorAgent Responses calls.
- Removed OpenAI adapter sticky `session_id` / `conversation_id` HTTP headers.
- Kept only a local deterministic `http_session_id` for audit correlation.
- Changed `ai_response_cache_enabled` default to `false`; local response cache remains disabled/no-op.
- Promoted `numbered_special_exclusion_needs_target_evidence` from semantic diagnostic to blocking submit repair.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_models.py tests\test_case_agent_human_cognitive_workspace.py tests\test_case_agent_orchestrator_agent.py tests\test_config_local_bangumi_case_agent_defaults.py tests\test_config_manager.py -q
$files = Get-ChildItem tests -Filter 'test_case_agent_*.py' | ForEach-Object { $_.FullName }
.venv\Scripts\python.exe -m pytest $files tests\test_local_bangumi_sample_runner.py tests\test_ai_models.py tests\test_config_local_bangumi_case_agent_defaults.py tests\test_config_manager.py -q
.venv\Scripts\python.exe -m compileall src\ai src\rename\case_agent src\config tools\run_local_bangumi_human_gate.py tools\run_local_bangumi_mapping_sample_pool.py
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\run_local_bangumi_mapping_sample_pool.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py --json
```

Result:

- targeted pytest: `174 passed`
- wider case-agent pytest: `662 passed, 8 skipped`
- compile: passed
- boundary scan: `finding_count=0`

Focused reruns:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_risk_fix_gate_20260519
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_risk_fix_gate_retry_20260519
```

First fresh run:

- `status=fail_closed`
- `summary=unresolved_submit_repair`
- final blocker: `OVERLORD Ple Ple Pleiades main: fail_closed_title_tail_bridge_uninspected`
- cache/request audit was clean: stable `instructions_sha256`, stable `tools_sha256`, stable `case_desk_sha256`, `provider_session_enabled=false`

Retry result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `turn_count=5`
- `tool_sequence=search -> inspect -> submit -> submit -> submit`
- `submit_rejection_count=2`
- `legacy_subagent_call_count=0`
- `tool_rejection_count=0`
- `near_turn_limit_unhealthy_count=0`
- `semantic_diagnostics=[]`

Spot check:

- `Gekijouban Soushuuhen OVERLORD` part 1 -> `target://bangumi/194036-剧场版总集篇-overlord-不死者之王/episode/1`
- `Gekijouban Soushuuhen OVERLORD` part 2 -> `target://bangumi/198968-剧场版总集篇-overlord-漆黑的英雄/episode/1`
- `OVERLORD` -> `target://bangumi/112146-overlord/episodes/1-13`
- `OVERLORD II` -> `target://bangumi/211027-overlord-第二季/episodes/1-13`
- `OVERLORD III` -> `target://bangumi/242170-overlord-第三季/episodes/1-13`
- Historical run note, now superseded: `OVERLORD Ple Ple Pleiades`
  previously mapped to `target://bangumi/193953-play-play-昴宿星团/episode/1`;
  current handling sends this singleton to `manual_review`.
- Historical run note, now superseded: numbered SP groups previously remained
  supplemental with inspected same-series support. Current handling sends
  numbered SP groups to `manual_review` when related same-count short-series
  structure is visible but ownership is not strongly proven.

Conclusion:

- The request/cache fixes did not introduce a transport regression.
- Numbered SP target-absence is now fail-closed unless target-side evidence/support shape is present.
- One fresh run still showed stochastic Pleiades noise in the evidence path, but the retry accepted cleanly without provider sessions or response chaining. This remains a runtime robustness observation, not evidence that `previous_response_id`, `conversation`, or sticky headers are needed.

## 2026-05-19 Robustness Final Replay Batch

Implemented the HumanCaseAgent robustness pass from `LOCAL_BANGUMI_HUMAN_CASE_AGENT_ROBUSTNESS_PLAN.md`:

- evidence quality metadata on agent-facing locators and visible bridge candidates;
- blocking vs diagnostic target-surface action split;
- repair frontier rows and `RECOVERY_BRIEF` prompt surface;
- no-progress/recovery counters in trace snapshots and sample-run summaries;
- generic title-tail/root/continuation recovery guidance without fixed-layer target choice.

Boundary constraints:

- No `OVERLORD`, `Ple Ple Pleiades`, Bangumi id, alias, or file-to-target mapping was added.
- Fixed layer still only does evidence hygiene, action quality, repair frontier/audit, and mechanical verification.
- Ownership, special/OVA semantics, target absence, and target selection remain Agent decisions.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py tests\test_case_agent_orchestrator_agent.py tests\test_ai_models.py tests\test_config_local_bangumi_case_agent_defaults.py tests\test_config_manager.py -q
$files = Get-ChildItem tests -Filter 'test_case_agent_*.py' | ForEach-Object { $_.FullName }
.venv\Scripts\python.exe -m pytest $files tests\test_local_bangumi_sample_runner.py tests\test_ai_models.py tests\test_config_local_bangumi_case_agent_defaults.py tests\test_config_manager.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\run_local_bangumi_mapping_sample_pool.py tools\summarize_local_bangumi_human_trace.py
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py --json
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py tools\run_local_bangumi_mapping_sample_pool.py tools\summarize_local_bangumi_human_trace.py --json
```

Result:

- focused pytest: `211 passed`
- broader case-agent pytest: `674 passed, 8 skipped`
- compile: passed
- boundary scan on `src/rename/case_agent`: `finding_count=0`
- boundary scan on touched reporting tools: `finding_count=0`

Fresh `sample_0096` replay batch:

| Run | Status | Summary | Verifier | near-turn | weak-related | no-progress | strict |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| `local_bangumi_mapping_sample_0096_robustness_final_gate_1_20260519` | `fail_closed` | `agent_recovery_failed` | `true` | 0 | 0 | 3 | 0 |
| `local_bangumi_mapping_sample_0096_robustness_final_gate_2_20260519` | `fail_closed` | `agent_recovery_failed` | `true` | 0 | 0 | 0 | 0 |
| `local_bangumi_mapping_sample_0096_robustness_final_gate_3_20260519` | `fail_closed` | `agent_recovery_failed` | `true` | 0 | 0 | 0 | 0 |
| `local_bangumi_mapping_sample_0096_robustness_final_gate_4_20260519` | `fail_closed` | `agent_recovery_failed` | `true` | 0 | 0 | 2 | 0 |
| `local_bangumi_mapping_sample_0096_robustness_final_gate_5_20260519` | `fail_closed` | `agent_recovery_failed` | `true` | 0 | 0 | 4 | 0 |

Protection replays:

| Run | Status | Summary | Verifier | near-turn | weak-related | strict |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `local_bangumi_mapping_sample_0035_robustness_protection_20260519` | `fail_closed` | `agent_recovery_failed` | `true` | 0 | 0 | 0 |
| `local_bangumi_mapping_sample_0126_robustness_protection_20260519` | `fail_closed` | `agent_recovery_failed` | `true` | 0 | 0 | 0 |

Conclusion:

- The stochastic `sample_0096` path no longer reports `unresolved_submit_repair`.
- Source-query-only weak related evidence did not become a blocking inspect loop.
- Late-turn failures are now represented as explicit recovery failure/frontier exhaustion telemetry rather than an unexplained near-cap health failure.

## 2026-05-19 Bounded CaseResolutionGoal Follow-up

The later focused sample run showed that `sample_0096`, `sample_0035`, and
`sample_0126` were only safe strict fail-closed/non-regression cases, not stable
accepted mappings. The remaining architecture gap was not another missing
sample-specific repair rule: the Agent could repeatedly collide with a visible
blocker without being forced into a bounded strategy change or an auditable
terminal fail-closed.

Implemented direction:

- Add `CaseResolutionGoal` to `CASE_STATE.case_memory`.
- Require every tool call to choose `repair_strategy`.
- Track strategy history and a compact progress ledger.
- Turn repeated same-blocker/same-submit-shape feedback into a strategy-change
  gate.
- Validate terminal fail-closed against exact active blockers, strong
  candidates, pending blocking evidence actions, saved ok rows, and concrete
  non-progressable reasons.

This is intentionally not a target selector. The fixed layer only maintains the
goal board and mechanical contract; ownership, target choice, special semantics,
target_absent, and fail-closed meaning remain Agent decisions.

Validation:

- `sample_0096`: `tests/sample_pool/generated/local_bangumi_mapping_gate_20260519_150428_845`,
  `fail_closed / agent_fail_closed_from_submit`,
  `case_resolution_goal_status=accepted_or_idle`, strict failures `0`.
- Protection `sample_0035` and `sample_0126`:
  `tests/sample_pool/generated/local_bangumi_mapping_gate_20260519_150833_491`,
  both `fail_closed`; summaries are `obvious_terminal_fail_closed` and
  `agent_fail_closed_from_submit`, strict failures `0`.
- focused pytest plus sample runner tests: `224 passed`.
- compile and boundary scans passed with `finding_count=0`.

## 2026-05-19 Next Plan: Agent-Owned Evidence Composition For Derivative Shorts

Follow-up discussion found a more accurate interpretation of the remaining
`sample_0096` failures:

- The text-only evidence surface is not enough to prove the true Overlord
  `Ple Ple Pleiades` ownership.
- Human review can infer more because it uses extra evidence: video duration,
  actual playback/title cards, and the Bangumi related graph between main
  seasons and derivative short subjects.
- `OVERLORD Ple Ple Pleiades main` is likely a compilation of the first
  `Play Play` short series, not episode 1.
- `OVERLORD SP extras`, `OVERLORD II SP01-SP13`, and
  `OVERLORD III SP01-SP13` are likely derivative short episodes, not generic
  supplemental extras.
- Without those additional media/related facts, strict fail-closed is a valid
  outcome. The system must not accept unknown rows as supplemental just to make
  the rest of the package pass.

Design principle:

The fixed layer may expose facts, but must not arrange facts into the answer.
It must not emit `candidate derivative mapping`, `recommended target`,
`strong candidate`, or any target-shaped suggestion such as
`OVERLORD II SP01-SP13 -> Play Play 2`. The Agent must request the facts,
compose the hypothesis, and decide mapping, duplicate packaging,
target_absent/supplemental, or fail_closed.

Allowed fixed-layer fact surfaces:

- Local media facts for explicitly requested local locators:
  `duration_sec`, file count, numbered token sequence, path hierarchy,
  representative labels, container/video/audio/subtitle metadata, and optional
  sampled title/OCR facts if implemented.
- Bangumi related graph facts for explicitly inspected subjects:
  related subject title, aliases already returned by Bangumi, relation label,
  episode count, and visible item refs.
- Existing Agent decisions as case memory facts:
  for example, `OVERLORD II main-episodes` has already been mapped to
  `OVERLORD 第二季`.
- Mechanical validation:
  coverage, duplicate target usage, legal refs, visible target spans, support
  shape, and whether a submitted claim cites the evidence it says it used.

Forbidden fixed-layer behavior:

- Do not generate semantic candidate mappings from local SP rows to related
  Bangumi subjects.
- Do not infer that a numbered SP row is `target_absent`, `supplemental`, or
  mapped because counts happen to match.
- Do not hard-code `OVERLORD`, `Ple Ple Pleiades`, Bangumi subject ids, title
  translations, aliases, or file-to-target mappings.
- Do not label a related subject as the likely owner. Present it as a related
  subject fact only.

Agent workflow to implement:

1. For unresolved `SPxx`, `special-marker`, singleton compilation, or suspected
   duplicate-packaging rows, the Agent should request local media facts for the
   exact local locator instead of guessing from filenames only.
2. If the row belongs under a main season folder, the Agent should inspect the
   already-mapped main season's related Bangumi surface and any related subject
   it wants to use.
3. The Agent must explicitly write its own hypothesis before submit:
   local parent identity, local count/numbering, duration pattern, related
   subject relationship, target episode count, and duplicate/alternate
   packaging risk.
4. The Agent may map a numbered SP row only when its own cited evidence supports
   a derivative short subject and a legal episode span.
5. The Agent may map a singleton compilation as `mapped_composite_feature` only
   when the cited media facts support that one file covering multiple visible
   target items.
6. If both a split episode set and a compilation point at the same target span,
   the Agent must pick one mapping owner and mark the other as
   duplicate/alternate packaging, or fail_closed if overlap cannot be proven.
7. If evidence remains insufficient, the row should be `fail_closed` or a future
   unresolved/manual-review state, not accepted supplemental.

Expected `sample_0096` behavior with media-duration evidence available:

- TV main rows and the two recap movies remain mapped as before.
- `OVERLORD SP01-SP08` can map to the first visible `Play Play` short subject
  if the Agent cites related-graph, count, numbering, and duration evidence.
- `OVERLORD Ple Ple Pleiades main` should not also claim the same target span;
  if its roughly 30-minute duration matches the same short series, treat it as
  duplicate/alternate packaging unless the split episodes are absent.
- `OVERLORD II SP01-SP13` can map to the related `Play Play` season-2 short
  subject if the Agent cites the `OVERLORD II` derivative relation, 13 short
  files, and 13 visible target items.
- `OVERLORD III SP01-SP13` follows the same rule for the season-3 derivative
  short subject.
- Without media-duration and related-subject evidence, these rows should remain
  strict fail_closed rather than being accepted as generic supplemental.

Implementation phases:

1. Add a read-only `local_media_probe` evidence path or equivalent local fact
   surface for explicit local locators. Start with duration and numbered-label
   summaries; keep OCR/frame/title extraction optional.
2. Add or expose a raw related-subject fact surface in `inspect`, without
   ranking or candidate wording.
3. Extend the Agent prompt/tool contract so unresolved SP/composite rows ask for
   media facts and related facts before terminal fail_closed.
4. Extend submit validation to check evidence support shape for Agent-authored
   claims: derivative mapping claims need cited related graph + count/numbering
   + media-duration facts; duplicate-packaging claims need cited overlap facts.
   The validator must not choose the target.
5. Add trace/audit fields for media-fact requests, related-graph requests, and
   Agent-authored hypothesis text.
6. Reconsider product status semantics separately: a future
   `partial_accepted_with_unresolved` may be better than global fail_closed, but
   it must not relax what `supplemental` means.

Validation plan:

- Unit tests proving the new fact surface contains raw local media facts and
  raw related-subject facts, with no generated mapping candidate.
- Boundary scan rules for target-shaped wording such as `recommended target`,
  `candidate derivative mapping`, or sample-specific Overlord/Pleiades ids.
- A `sample_0096` run without media facts should remain safe fail_closed for
  the derivative shorts.
- A fixture-backed `sample_0096` run with duration facts may accept the
  derivative short mappings, but only if the Agent authors the hypothesis and
  cites the required facts.
- Protection samples `0035` and `0126` should not gain new accepted
  supplemental rows from unrelated SP/count coincidences.

## 2026-05-19 Local Fact Surface Validation

Plan: `docs/LOCAL_BANGUMI_LOCAL_FACT_SURFACE_GOAL_PLAN.md`

Implemented delta:

- Added a sidecar local fact surface for raw path/container/subtitle/stream and
  explicit missing facts.
- Reused the same fact builder in real `LocalEvidence` and sample-pool raw JSON
  dry builds.
- Projected compact fact summaries into Case Agent dossier/prompt/query
  surfaces and made detailed fact cards available through explicit
  HumanCaseAgent `inspect` scopes.
- Kept fact cards off the initial HumanCaseAgent desk by default after
  validation showed that unrequested missing-fact summaries could bias
  `sample_0096` into premature movie/recap fail-closed.

Focused gate:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_manual_review_hints_gate2_20260519
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `turn_count=11`
- `tool_sequence=search -> inspect -> search -> submit -> search -> submit -> submit -> submit -> submit -> submit -> submit`
- `submit_rejection_count=4`
- `strict_failure_count=0`
- `legacy_subagent_call_count=0`
- `main_file_count=81`
- `mapped_file_count=39`
- `excluded_file_count=0`
- `manual_review_file_count=42`
- `unresolved_count=0`
- `deterministic_filtered_video_count=87`

Spot check:

- Three TV spans remain mapped: `OVERLORD`, `OVERLORD II`, and
  `OVERLORD III` each map 13 files to visible Bangumi episode refs.
- The two `Gekijouban Soushuuhen OVERLORD` recap movie files are
  `manual_review` in this conservative run.
- `OVERLORD Ple Ple Pleiades` is now an Agent-authored `UNALIGNED`
  `manual_review` row. The local evidence does not prove whether this singleton
  is a whole-series compilation, duplicate packaging, or one visible `Play Play`
  item, so mapping it to `episode/1` or any other single item is unsafe.
- Preview-marked rows such as `Preview01_1` are now filtered before the
  HumanCaseAgent contract as deterministic supplemental material, alongside
  CM/Menu/PV/NCOP/NCED support-only files.
- Numbered SP groups now stay out of mapped ownership when only related
  same-count `Play Play` structure is visible. They are `manual_review` instead
  of mapped or supplemental, and they do not block package acceptance.
- `manual_review` rows may carry low-confidence `review_candidate_targets`
  such as visible `Play Play` spans for human replay. Those hints compile only
  into review metadata: final assignment rows still use `target_ref=UNALIGNED`,
  have no `target_refs`, and do not count as mapped/accepted targets.

Boundary conclusion:

- This local fact surface goal did not add fixed-layer target selection,
  sample-specific Overlord/Pleiades aliases, Bangumi ids, or file-to-target
  mappings.
- The accepted SP/theater-manners and `manual_review` outcomes are
  recorded as the Agent's terminal judgment from the current evidence path, not
  as fact-model conclusions.
- If product policy later requires derivative-short rows to remain unresolved
  unless duration/related-graph evidence is cited, that should be enforced as a
  separate Agent/validator support-shape change rather than by weakening the
  local fact model.
