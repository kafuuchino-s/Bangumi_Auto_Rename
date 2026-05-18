# Local to Bangumi Spot Check: sample_0114

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

`sample_id`: `sample_0114_vcb_studio_world_trigger`

## Focused Run

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0114 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0114_spot_check_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit -> submit`
- `submit_rejection_count=1`
- `submit_rejection_issue_counts={coverage_missing: 1}`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`
- `mapped_file_count=99`
- `excluded_file_count=22`

## Manual Replay Check

`manual_human_path`:

1. Read the package as a multi-season `World Trigger` release:
   - original `World Trigger` 01-73.
   - `World Trigger 2nd Season` 01-12.
   - `World Trigger 3rd Season` 01-14.
   - SP folders containing `Event`, `SP`, `Cast Talk`, `Jump Super Stage`, and `Shinjuku Night Event` material.
2. Search/inspect the `World Trigger` family surface.
3. Map the three complete episode spans to matching inspected TV subjects.
4. Exclude extras only with concrete package-extra/event/talk reasons; do not use vague supplemental outcomes for main-looking files.

`agent_actual_trace`:

- Focused run output: `tests/sample_pool/generated/local_bangumi_mapping_sample_0114_spot_check_20260518`
- The first submit missed coverage for extra/SP units.
- Final submit covered every main local locator, mapping the three TV spans and excluding extras with concrete reasons.

`divergence_point`: none in the accepted current-code run.

`gap_category`: `model_variance`

`is_generic_architecture_gap`: `false`

`proposed_fix_layer`: none.

`fixed_layer_boundary_check`:

- The fixed layer only enforced coverage/accounting and concrete support shape.
- It did not decide which season target owns each span and did not semantically classify Event/Cast Talk material.
- The Agent supplied the final semantic mapping and exclusion reasons.

`rerun_gate`: no code change is proposed from this sample.

## Spot Check

Accepted mapping:

- `World Trigger` 01-73 -> `target://bangumi/104906-境界触发者/episodes/1-73`
- `World Trigger 2nd Season` 01-12 -> `target://bangumi/296875-境界触发者-第二季/episodes/1-12`
- `World Trigger 3rd Season` 01-14 -> `target://bangumi/322967-境界触发者-第三季/episodes/1-14`
- `World Trigger 2nd Season [Event]` -> supplemental / unaligned
- `World Trigger 2nd Season [SP]` -> supplemental / unaligned
- original-season `Cast Talk`, `Cast Talk Extra`, `Jump Super Stage`, and `Shinjuku Night Event` files -> supplemental / unaligned

Safety conclusion:

- No unsafe accepted found.
- The three mapped spans match visible target episode counts exactly.
- The excluded files have concrete event/talk/package-extra descriptions and were not assigned to regular episode targets.
- No generic structure fix is required from this sample at this point.
