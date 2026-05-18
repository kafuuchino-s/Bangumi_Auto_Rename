# HumanCaseAgent Cache Audit: sample_0096

Date: 2026-05-19

Scope: `sample_0096_vcb_studio_overlord`, HumanCaseAgent focused gate, OpenAI Responses HTTP path.

## Conclusion

`previous_response_id` must not be used on the current Responses HTTP path.

Provider returned:

```json
{
  "error": {
    "message": "previous_response_id is only supported on Responses WebSocket v2",
    "type": "invalid_request_error"
  }
}
```

Therefore the implementation now keeps cache audit fields, but removes the HumanCaseAgent `previous_response_id` experiment switch and stops forwarding `previous_response_id` from the OpenAI HTTP adapter. `conversation` also remains unused for this path.

## Byte-Stability Boundary

The request prefix is locally stable where expected:

- `instructions_sha256` stayed stable across turns:
  `bc39399eabb20d500273868a0d48c8b21d1ada49312c1b34d2d6dc074ee093bd`
- `tools_sha256` stayed stable across turns:
  `ce00268271eec8a535c82704f3d004709866e40c30b872a50d997a31166f91ba`
- `case_desk_sha256` stayed stable across turns:
  `142f38abf9d3e2710463c6f1398b6dc34f5a926ac2d5ac7e333654d0a3a29624`
- `CASE_STATE` tail changes by design as history, observations, workspace, and repair feedback evolve.
- `tail_lcp_with_previous_estimated_tokens` still showed long stable overlap, for example default2 turn 2 had about `8169` estimated stable tokens and later turns reached about `17730`.

This means the low provider cache hit is not explained by local byte-level instability in `instructions`, tool schema, or the stable portion of `CASE_STATE`.

## Why Cached Tokens Often Stop Around 4.2K

In the default audit run, many post-first turns reported exactly `4224` cached tokens even when the local byte LCP was much larger:

| Run | Status | Input tokens | Cached tokens | Cached ratio | Notes |
| --- | --- | ---: | ---: | ---: | --- |
| `seasonless_repair_gate_20260519` | `accepted` | `415186` | `41344` | `0.0996` | Fresh no-previous accepted rerun after structural repair guidance fix |
| `cache_audit_no_previous_20260519` | `fail_closed` | `572428` | `99328` | `0.1735` | Fresh run after removing `previous_response_id`; no legacy subagent |
| `cache_audit_default2_20260519` | `fail_closed` | `305810` | `45568` | `0.1490` | 8 orchestrator turns, no legacy subagent |
| `cache_audit_default3_20260519` | `fail_closed` | `531125` | `73600` | `0.1386` | 12 turns, near turn limit once |
| `cache_audit_previous_response2_20260519` | `accepted` | `285702` | `71296` | `0.2495` | Not a valid previous-response result; provider rejected the sent id once and retried |

Fresh no-previous run health:

- `status=fail_closed`
- `summary=unresolved_submit_repair`
- `accepted_contract_ok=false`
- `final_verifier_passed=false`
- `tool_sequence=search -> inspect -> note -> submit -> submit -> submit -> search -> submit -> submit -> inspect -> submit -> submit`
- `submit_rejection_count=7`
- `near_turn_limit_unhealthy_count=1`
- `stall_warning_count=0`
- `legacy_subagent_call_count=0`

The failure blocker was submit repair around `OVERLORD Ple Ple Pleiades` evidence/shape, not transport or cache construction.

Latest no-previous accepted run health after the structural repair guidance fix:

- `status=accepted`
- `accepted_contract_ok=true`
- `final_verifier_passed=true`
- `tool_sequence=search -> inspect -> submit -> search -> submit -> search -> submit -> submit`
- `submit_rejection_count=3`
- `near_turn_limit_unhealthy_count=0`
- `stall_warning_count=0`
- `legacy_subagent_call_count=0`
- `provider_cached_input_ratio=0.09957946558891677`

The key fix was not a request-structure change. The repair feedback for a mapped unseasoned local title pointing at a season-suffixed target now produces a seasonless search lead and same-title-family unseasoned alternates. That let the Agent recover `OVERLORD Ple Ple Pleiades` through visible evidence instead of drifting on a season-suffixed `Play Play` target.

Representative default2 turns:

| Turn | Tool | Input | Cached | Ratio | Tail LCP est. tokens |
| ---: | --- | ---: | ---: | ---: | ---: |
| 2 | `inspect` | `19939` | `4224` | `0.2118` | `8169` |
| 3 | `note` | `26123` | `4224` | `0.1617` | `10822` |
| 5 | `search` | `58704` | `4224` | `0.0720` | `5848` |
| 6 | `submit` | `59082` | `4224` | `0.0715` | `17730` |

Fresh no-previous run also showed provider variability without response chaining:

| Turn | Tool | Input | Cached | Ratio | Tail LCP est. tokens |
| ---: | --- | ---: | ---: | ---: | ---: |
| 2 | `inspect` | `19939` | `19584` | `0.9822` | `8169` |
| 3 | `note` | `26123` | `25728` | `0.9849` | `10822` |
| 4 | `submit` | `25317` | `4224` | `0.1668` | `5887` |
| 8 | `submit` | `78882` | `4224` | `0.0535` | `19584` |

The repeated `4224` value is close to the stable instruction/tool-prefix scale (`orchestrator_stable_prefix_estimated_tokens=4361`). Since local hashes and LCP prove more stable bytes than that, and the fresh no-previous run sometimes reached high cache ratios without response chaining, the most likely boundary is provider-side cache segmentation/routing/accounting rather than request construction drift.

## Request-Structure Decision

Keep the current HTTP Responses structure:

- Stable `instructions`.
- Stable tool schema.
- One user item containing `CASE_STATE:\n{stable_json_tail}`.
- `prompt_cache_key` and `prompt_cache_retention=24h`.
- No `previous_response_id`.
- No `conversation` for this focused path.

Do not switch to `previous_response_id` unless the transport is intentionally changed to Responses WebSocket v2 and the new server-state semantics are separately tested. Do not switch to `conversation` as a cache fix without a separate behavior audit; it changes state ownership and replay semantics rather than simply increasing prefix cache hit rate.

If further cost work is needed, the next useful lever is reducing mutable `CASE_STATE` tail size and repeated repair payloads, not adding `previous_response_id` to HTTP Responses.
