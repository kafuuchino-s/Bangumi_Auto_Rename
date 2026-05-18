# Local to Bangumi Spot Check: sample_0042

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

`sample_id`: `sample_0042_moozzi2_aria_series_blu_ray_box_animation_natural_origination_avvenire_arietta`

## Focused Run

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0042 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0042_spot_check_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit -> submit -> submit -> submit -> submit`
- `submit_rejection_count=4`
- `submit_rejection_issue_counts={duplicate_target: 2, coverage_missing: 3}`
- `repeated_submit_rejection_count=0`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`
- `mapped_file_count=56`
- `excluded_file_count=1`

## Manual Replay Check

`manual_human_path`:

1. Read the local package as an ARIA Blu-ray box with three TV seasons plus singleton sequel/OVA entries:
   - `Aria The Animation` regular files 01-13 plus `Aria The Avvenire - 01`.
   - `Aria The Natural` regular files 01-26 plus `Aria The Avvenire - 02` and `Aria The Arietta OVA`.
   - `Aria The Origination` regular files 01-13 plus `Aria The Avvenire - 03` and `Aria The Origination - 05.5`.
2. Search/inspect the ARIA family surface and use only visible Bangumi locators.
3. Map the three complete TV runs to their matching inspected regular spans.
4. Map Arietta to the inspected Arietta OVA subject, and map Avvenire 01/02/03 to the inspected 3-episode AVVENIRE subject.
5. Treat the local `Origination - 05.5` as a bonus/interstitial only if the Agent explicitly chooses a supplemental outcome rather than stealing episode 5 from the 13-file Origination run.

`agent_actual_trace`:

- Focused run output: `tests/sample_pool/generated/local_bangumi_mapping_sample_0042_spot_check_20260518`
- The Agent first inspected one ARIA family surface, then iterated through submit corrections.
- Rejections were mechanical: duplicate target conflict around Origination episode 5 / 05.5, and coverage gaps for missing Avvenire slices.
- The final submit resolved those gaps without repeated identical rejection.

`divergence_point`: none in the accepted current-code run.

`gap_category`: `model_variance`

`is_generic_architecture_gap`: `false`

`proposed_fix_layer`: none.

`fixed_layer_boundary_check`:

- The fixed layer only rejected mechanical duplicate/coverage/accounting issues.
- It did not choose the ARIA target subjects, did not mark `05.5` supplemental, and did not decide special/OVA ownership.
- The Agent made the semantic choices using visible inspected locators.

`rerun_gate`: no code change is proposed from this sample.

## Spot Check

Accepted mapping:

- `Aria The Animation` 01-13 -> `target://bangumi/531-水星领航员/episodes/1-13`
- `Aria The Natural` 01-26 -> `target://bangumi/1269-水星领航员-第二季/episodes/1-26`
- `Aria The Origination` 01-13 -> `target://bangumi/1270-水星领航员-第三季/episodes/1-13`
- `Aria The Arietta OVA` -> `target://bangumi/750-水星领航员-ova-arietta/episodes/1-1`
- `Aria The Avvenire` 01/02/03 -> `target://bangumi/124341-水星领航员-the-avvenire/episodes/1-3`
- `Aria The Origination - 05.5` -> supplemental / unaligned

Safety conclusion:

- No unsafe accepted found.
- The multiple submit attempts were mechanical correction steps, not a repeated submit loop: the run reports `repeated_submit_rejection_count=0`, `stall_warning_count=0`, and final readiness is `ready`.
- No generic structure fix is required from this sample at this point.
