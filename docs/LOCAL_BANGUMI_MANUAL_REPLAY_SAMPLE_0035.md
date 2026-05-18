# Local to Bangumi Manual Replay: sample_0035

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

## Required Artifact

`sample_id`: `sample_0035_ktxp_mushishi_zoku_shou_bdrip`

`manual_human_path`:

1. Read the local package shape from the raw sample. The root is `[KTXP][Mushishi Zoku Shou][BDrip]`. The main video surface has:
   - standalone `Mushishi Tokubetsu Hen_Hihamu Kage`
   - `Mushishi Zoku Shou` Vol.1 episodes 01-04
   - `Mushishi Zoku Shou` Vol.2 episodes 05-08
   - standalone `Mushishi Tokubetsu Hen_Odoro no Michi`
   - `Mushishi Zoku Shou` Vol.3 episodes 09-10
   - `Mushishi Zoku Shou` Vol.4 episodes 11-14
   - `Mushishi Zoku Shou` Vol.5 episodes 15-18
   - standalone `Mushishi Tokubetsu Hen_Suzu no Shizuku`
   - `Mushishi Zoku Shou` Vol.6 episodes 19-20
2. Search/inspect the visible `Mushishi Zoku Shou` subject. The run surfaced `target://bangumi/92705-虫师-续章`, whose visible regular episode surface is 1-10.
3. Map Vol.1 01-04, Vol.2 05-08, and Vol.3 09-10 to that inspected 1-10 subject.
4. When the target surface stops at 10 but the local package continues 11-20 under the same title, do not keep retrying `target://bangumi/92705-虫师-续章/episodes/11-20`. A human would treat this as a continuation/second-part ownership question and search or inspect additional same-title continuation surfaces from the visible title and local episode-range mismatch.
5. Use only newly surfaced Bangumi evidence to finish: either map local 11-20 to an inspected continuation subject, or fail only if the continuation/special/related search surface remains exhausted. The current run did not exhaust that continuation path.

`agent_actual_trace`:

- Focused run output: `.tmp/human_cognitive_sample0035/sample_0035_ktxp_mushishi_zoku_shou_bdrip.json`
- `status`: `fail_closed`
- `summary`: `agent_fail_closed_from_submit`
- `final_verifier_passed`: `true`
- `tool_sequence`: `search -> inspect -> submit -> search -> inspect -> search -> submit`
- `tool_call_counts`: `search=3`, `inspect=2`, `submit=2`
- `submit_rejection_count`: `1`
- first submit rejection: `target_episode_surface_missing=3` for requested ranges 11-14, 15-18, and 19-20 on `target://bangumi/92705-虫师-续章`.
- final submit mapped 01-10 and specials, then returned Agent-authored fail_closed for 11-14, 15-18, and 19-20.
- `legacy_subagent_call_count`: `0`
- `semantic_subagent_call_count`: `0`
- `stall_warning_count`: `0`
- `resolution_readiness_summary.status`: `ready`

`divergence_point`:

The first useful divergence is after the first submit rejection. The fixed layer exposed that `target://bangumi/92705-虫师-续章` has visible episodes 1-10 only, while local work units still require 11-14, 15-18, and 19-20. The next human step is to search/inspect a continuation or second-part subject from the same title and episode-range mismatch. The Agent instead spent the remaining evidence turns on broad same-family or unrelated alternates and then fail_closed the continuation work units.

`gap_category`: `evidence_surface`

`is_generic_architecture_gap`: `true`

`proposed_fix_layer`:

Improve submit feedback / evidence surface for `target_episode_surface_missing` when:

- selected target episode surface is visible,
- requested range starts after the visible regular max,
- local locator title matches the selected subject title family,
- local package has later contiguous numbered groups under the same title.

The repair agenda should expose generic continuation-search actions and agenda wording, for example title-preserving variants such as `<local title> 2`, `<local title> second season`, `<local title> part 2`, or related/same-title continuation inspect actions when visible. This must remain an evidence request hint only. It must not choose a Bangumi subject, split the local files, or mark target_absent.

`fixed_layer_boundary_check`:

- Allowed: generate mechanical evidence-surface hints from visible title, visible target max episode, and local requested range.
- Allowed: tell the Agent that the current target range is mechanically absent and a continuation/same-title target should be searched or inspected.
- Forbidden: hard-code `Mushishi`, a Bangumi subject id, a specific Japanese title alias, or a file-to-target mapping.
- Forbidden: fixed layer accepts/rejects a semantic outcome because a candidate is a second season, special, or target_absent.

`rerun_gate`:

After the generic evidence-surface hint is implemented:

```powershell
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_mapping_sample_pool.py
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0035 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0035_manual_replay_gate_20260518
```

Focused acceptance target:

- preferred: `accepted_contract_ok=true` after Agent maps all visible, human-confirmed work units using inspected evidence.
- acceptable intermediate only if not accepted: no `budget_exhausted`, no submit loop, `legacy_subagent_call_count=0`, and fail_closed must name a newly specific continuation-search blocker rather than only "92705 stops at 10".

## Runtime Review

- No naked budget failure in the focused run.
- No submit loop: only two submit calls; one rejection, one final fail_closed.
- No legacy subagent path.
- The remaining fail_closed blocker is concrete but not evidence-exhausted under the manual path because the continuation/same-title search surface was not made explicit enough after the target range miss.

## Architecture Delta

This sample should drive a generic `target_episode_surface_missing` evidence-surface improvement, not a sample-specific mapping. The current attention workspace is present, but the repair agenda should better preserve the continuation ownership question as an active work unit after the first target-range miss.

## Post-Fix Validation

Date: 2026-05-18

Implemented generic evidence-surface feedback for visible `target_episode_surface_missing` cases where the requested target range starts after the visible regular max and the local locator shares the selected target title family. The fixed layer now exposes title-preserving continuation search queries and a `continuation_evidence_hint`; it still does not choose a Bangumi subject, split local files, or mark `target_absent`.

Focused rerun:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0035 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0035_manual_replay_gate_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit -> search -> note -> search -> submit`
- `submit_rejection_count=1`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`
- `budget_exhausted=0`

Spot check:

- Main continuation work units 11-14, 15-18, and 19-20 were mapped by the Agent to inspected continuation target `target://bangumi/106207-.../episodes/11-20` ranges after additional search evidence.
- This confirms the original divergence point was addressed for the main continuation path.
- The accepted output still contains semantic diagnostics around singleton special/bonus exclusions. Those diagnostics are outside this continuation-specific fix and should drive a separate manual replay bucket if they become a convergence safety concern.
