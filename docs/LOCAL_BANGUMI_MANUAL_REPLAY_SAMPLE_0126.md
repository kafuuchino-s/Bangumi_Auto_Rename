# Local to Bangumi Manual Replay: sample_0126

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

## Required Artifact

`sample_id`: `sample_0126_sample`

`manual_human_path`:

1. Treat the package as a multi-work-unit Hidamari Sketch package with TV spans and SP/extra groups.
2. Search and inspect the relevant Bangumi subjects, including episode and special/EX surfaces.
3. Map only regular spans or explicit special items whose visible target surface supports the local unit.
4. For numbered SP local files, do not clear them as supplemental/target_absent until the target-side SP/EX surface has been checked.
5. If one singleton SP file remains ambiguous after visible target-side evidence, close only that exact local slice as fail_closed with a concrete blocker.

`agent_actual_trace`:

- Focused run output: `tests/sample_pool/generated/local_bangumi_mapping_sample_0126_spot_check_20260518`
- `status=fail_closed`
- `summary=agent_fail_closed_from_submit`
- `accepted_contract_ok=false`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> search -> submit -> submit -> submit`
- `turn_count=9`
- `submit_rejection_count=4`
- `submit_rejection_issue_counts={target_episode_surface_missing: 2, count_mismatch: 3, episode_range_required: 1, duplicate_target: 2, coverage_missing: 1}`
- `near_turn_limit_unhealthy_count=0`
- `legacy_subagent_call_count=0`

`divergence_point`:

The Agent reaches a stable package resolution but leaves one `x365 SP01` local slice unresolved. The blocker is no longer a structural loop: the final fail_closed names the specific local slice and the exact ambiguity around whether it owns the visible `x365` EX/special target surface.

`gap_category`: `safe_fail`

`is_generic_architecture_gap`: `false`

`proposed_fix_layer`:

No code fix from this run alone. The run produced a concrete evidence blocker rather than naked budget failure, unsafe accepted, or legacy fallback. A later manual replay can revisit whether additional generic special/EX title-surface evidence should be exposed, but this artifact does not justify a new rule.

`fixed_layer_boundary_check`:

- Allowed: require visible target-side SP/EX evidence before accepting numbered SP exclusion.
- Forbidden: fixed layer decides that `SP01` maps to episode 14/EX or is supplemental.
- Current result keeps that semantic choice with the Agent and closes the unresolved slice explicitly.

`rerun_gate`:

If future generic SP/EX evidence-surface changes are made, rerun only:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0126 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0126_<gate>_YYYYMMDD
```

## 2026-05-18 Post Pairing/Composite Guard Validation

Focused rerun:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample 0126 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0126_post_pairing_guard_gate_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `turn_count=6`
- `submit_rejection_count=1`
- `near_turn_limit_unhealthy_count=0`
- `legacy_subagent_call_count=0`

Spot check:

- `×365 main-episodes` maps to `target://bangumi/330-向阳素描-x-365/episodes/1-13`.
- The single `×365 main` EX file maps to `target://bangumi/330-向陽素描-x-365/episode/14`.
- The two `×365 special-marker` OVA files map to `target://bangumi/2662-向阳素描-x-365-特别篇/episodes/15-16`.
- The `×365 SPs special-marker` bundle is not forced onto the EX/special targets; it is resolved as supplemental with same-series/special-subject support.

Safety conclusion:

- No unsafe accepted found in this focused run.
- The earlier safe-fail blocker is resolved by Agent judgment using visible target-side evidence; no fixed-layer special/EX semantic rule was added.

## 2026-05-18 Regression After Fixed-Layer Title-Bridge Removal

Focused rerun:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample 0126 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0126_no_fixed_title_bridge_regression_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `turn_count=6`
- `submit_rejection_count=2`
- `near_turn_limit_unhealthy_count=0`
- `legacy_subagent_call_count=0`
- `tool_rejection_count=0`

Conclusion:

- Removing fixed-layer translated title-token bridges did not regress this special/EX sample.
- The accepted result still comes from Agent judgment over visible target-side evidence, not from fixed-layer special/OVA/EX semantic rules.
