# Local to Bangumi Spot Check: sample_0101

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

`sample_id`: `sample_0101_vcb_studio_psycho_pass`

## Focused Run

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0101 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0101_spot_check_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit -> submit -> submit`
- `submit_rejection_count=2`
- `submit_rejection_issue_counts={locator_not_found: 1, coverage_missing: 1, duplicate_target: 1}`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`
- `mapped_file_count=34`
- `excluded_file_count=23`

## Manual Replay Check

`manual_human_path`:

1. Read the package as `PSYCHO-PASS` 01-22, `PSYCHO-PASS II` 01-11, one `PSYCHO-PASS The Movie`, previews, and SP/package extras.
2. Search/inspect the visible `PSYCHO-PASS` family subjects.
3. Map the 22-file first season to its 22-episode subject.
4. Map the 11-file second season to its 11-episode subject.
5. Map the single movie file only to an inspected movie subject.
6. Exclude preview files and packaging extras. Do not map `SP01` to regular episode 1 unless a specific visible SP/OVA/OAD item supports it.

`agent_actual_trace`:

- Focused run output: `tests/sample_pool/generated/local_bangumi_mapping_sample_0101_spot_check_20260518`
- The first submit had a hidden/unknown related locator and missing coverage for SP material.
- A later submit exposed a duplicate target conflict when `SP01` tried to claim the same regular episode 1 item as the main season.
- Final submit kept the main season owner and excluded `SP01` as supplemental.

`divergence_point`: none in the accepted current-code run.

`gap_category`: `model_variance`

`is_generic_architecture_gap`: `false`

`proposed_fix_layer`: none.

`fixed_layer_boundary_check`:

- The fixed layer rejected only hidden locator, coverage, and duplicate-target mechanics.
- It did not decide whether `SP01` was supplemental and did not choose the movie target.
- The Agent made the semantic decisions in the final submit.

`rerun_gate`: no code change is proposed from this sample.

## Spot Check

Accepted mapping:

- `PSYCHO-PASS` 01-22 -> `target://bangumi/37685-心理测量者/episodes/1-22`
- `PSYCHO-PASS II` 01-11 -> `target://bangumi/77625-心理测量者2/episodes/1-11`
- `PSYCHO-PASS The Movie` -> `target://bangumi/83067-剧场版-心理测量者/episodes/1-1`
- preview files -> supplemental / unaligned
- `PSYCHO-PASS II [IV01]` -> supplemental / unaligned
- `PSYCHO-PASS [SP01]` -> supplemental / unaligned

Safety conclusion:

- No unsafe accepted found.
- The movie is mapped to a visible movie subject, not to a TV episode.
- `SP01` was not allowed to steal regular episode 1 from the first season; the final exclusion is safer than an unsupported regular-episode mapping.
- The `numbered_special_exclusion_needs_target_evidence` diagnostic remains useful audit context, but in this accepted run it did not hide an unsafe mapping.
