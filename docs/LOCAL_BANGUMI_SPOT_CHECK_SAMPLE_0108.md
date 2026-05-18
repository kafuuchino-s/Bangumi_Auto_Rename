# Local to Bangumi Spot Check: sample_0108

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

`sample_id`: `sample_0108_vcb_studio_senki_zesshou_symphogear_xv_ma10p_1080p`

## Focused Run

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0108 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0108_spot_check_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit`
- `submit_rejection_count=0`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`
- `mapped_file_count=13`
- `excluded_file_count=18`

## Spot Check

Raw local surface:

- Main video files are `Senki Zesshou Symphogear XV [01]` through `[13]`.
- SP folder includes `[IV]`, `Preview01` through `Preview13`, and `SP01` through `SP04`.

Agent result:

- Main `01-13` mapped to inspected XV regular span `target://bangumi/170689-.../episodes/1-13`.
- Local `[IV]` was treated as supplemental SP/package material, not as a regular Bangumi episode item.
- `Preview01-Preview13` were submitted as supplemental promotional/packaging material.
- `SP01-SP04` were submitted as supplemental package special-marker material.

Safety conclusion:

- No unsafe accepted found in this spot check.
- The accepted output carries a `numbered_special_exclusion_needs_target_evidence` semantic diagnostic for `SP01-SP04`. The submitted support points at the inspected XV target surface and the reason states the finite negative target-side check. This is diagnostic debt around support-shape wording, not a wrong accepted mapping.
- No generic structure fix is required from this sample at this point.
