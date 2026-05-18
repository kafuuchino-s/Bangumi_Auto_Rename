# Local to Bangumi Spot Check: sample_0106

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

`sample_id`: `sample_0106_vcb_studio_senki_zesshou_symphogear_axz_ma10p_1080p`

## Focused Run

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0106 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0106_spot_check_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit`
- `submit_rejection_count=0`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`
- `mapped_file_count=14`
- `excluded_file_count=17`

## Spot Check

Raw local surface:

- Main video files are `Senki Zesshou Symphogear AXZ [01]` through `[13]`.
- SP folder includes `[IV]`, `Preview01` through `Preview13`, and `SP01` through `SP04`.

Agent result:

- Main `01-13` mapped to inspected AXZ regular span `target://bangumi/163711-.../episodes/1-13`.
- Local `[IV]` mapped to inspected `target://bangumi/163711-.../special/4`.
- `Preview01-Preview13` were submitted as supplemental package preview material.
- `SP01-SP04` were submitted as supplemental package special-marker material.

Safety conclusion:

- No unsafe accepted found in this spot check.
- The only semantic diagnostic is `mapped_packaging_extra_marker_without_specific_target` for `[IV] -> special/4`. For this sample, the mapping is acceptable because the local marker `IV` is a roman numeral 4 and the selected target is the visible special item 4 on the inspected AXZ subject. The fixed layer did not choose this mapping; it only accepted a visible locator with exact accounting.
- No generic structure fix is required from this sample at this point.
