# Local to Bangumi Spot Check: sample_0093

Date: 2026-05-18

Plan: `docs/LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md`

`sample_id`: `sample_0093_vcb_studio_minami_ke`

## Focused Run

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0093 --limit 1 --max-rounds 12 --sample-timeout-seconds 300 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0093_spot_check_20260518
```

Result:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit -> search -> inspect -> search -> submit`
- `submit_rejection_count=1`
- `submit_rejection_issue_counts={locator_not_found: 1, coverage_missing: 1}`
- `legacy_subagent_call_count=0`
- `stall_warning_count=0`
- `mapped_file_count=55`
- `excluded_file_count=1`

## Manual Replay Check

`manual_human_path`:

1. Read the local package as four `Minami-ke` TV seasons plus OAD/SP material:
   - `Minami-ke` 01-13.
   - `Minami-ke Okaeri` 01-13.
   - `Minami-ke Okawari` 01-13.
   - `Minami-ke Tadaima` 01-13.
   - OAD files: `Betsubara OAD01`, `Omatase OAD02`, `Natsuyasumi OAD03`.
   - SP folder material, including a singleton `[IV]`.
2. Search/inspect the `Minami-ke` family surface and related named OAD subjects.
3. Map the four complete TV runs to inspected 13-episode spans.
4. Map the named OAD files only when inspected target aliases bridge to their visible titles.
5. Do not force the singleton `[IV]` onto a target if no safely visible Bangumi owner is present.

`agent_actual_trace`:

- Focused run output: `tests/sample_pool/generated/local_bangumi_mapping_sample_0093_spot_check_20260518`
- First submit exposed one hidden/invalid related locator plus missing coverage for SP/OAD material.
- The Agent searched and inspected additional OAD surfaces, then submitted an accepted package resolution.

`divergence_point`: none in the accepted current-code run.

`gap_category`: `model_variance`

`is_generic_architecture_gap`: `false`

`proposed_fix_layer`: none.

`fixed_layer_boundary_check`:

- The fixed layer rejected only hidden/unknown locator and coverage gaps during the first submit.
- It did not decide which OAD target was correct and did not force `[IV]` to a target.
- Final OAD choices came from Agent-authored semantic judgment over visible inspected locators.

`rerun_gate`: no code change is proposed from this sample.

## Spot Check

Accepted mapping:

- `Minami-ke` 01-13 -> `target://bangumi/283-南家三姐妹/episodes/1-13`
- `Minami-ke Okaeri` 01-13 -> `target://bangumi/889-南家三姐妹-欢迎回来/episodes/1-13`
- `Minami-ke Okawari` 01-13 -> `target://bangumi/890-南家三姐妹-再来一碗/episodes/1-13`
- `Minami-ke Tadaima` 01-13 -> `target://bangumi/47685-南家三姐妹-我回来了/episodes/1-13`
- `Betsubara OAD01` -> `target://bangumi/3016-南家三姐妹-饭后甜点/episode/1`
- `Omatase OAD02` -> `target://bangumi/47684-南家三姐妹-久候多时/episode/1`
- `Natsuyasumi OAD03` -> `target://bangumi/80205-南家三姐妹-夏日假期/episode/14`
- singleton `[IV]` -> supplemental / unaligned

Safety conclusion:

- No unsafe accepted found.
- The named OAD mappings are supported by visible aliases such as `みなみけ べつばら`, `みなみけ おまたせ`, and `みなみけ 夏やすみ`.
- The singleton `[IV]` exclusion is safer than claiming an unverified target.
- No generic structure fix is required from this sample at this point.
