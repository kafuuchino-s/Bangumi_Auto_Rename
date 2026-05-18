# Local to Bangumi Spot Check: sample_0122

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

`sample_id`: `sample_0122_the_disastrous_life_of_saiki_k_s00_2018_1080p_nf_web_dl_x264_ddp_2_0_animef_adweb`

## Focused Run

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0122 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0122_spot_check_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> search -> inspect -> submit`
- `submit_rejection_count=0`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`
- `mapped_file_count=2`
- `excluded_file_count=0`

## Spot Check

Raw local surface:

- The package has exactly two videos: `S00E01` and `S00E02`.

Agent result:

- Both local files mapped to inspected `target://bangumi/251831-...完结篇/episodes/1-2`.
- No files were excluded.
- No semantic diagnostics were emitted.

Safety conclusion:

- No unsafe accepted found in this spot check.
- The S00 two-file local package maps to the two-episode special finale subject, not to a broad regular season.
- No generic structure fix is required from this sample at this point.
