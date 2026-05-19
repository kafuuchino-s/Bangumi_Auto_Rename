# Local to Bangumi HumanCaseAgent 鲁棒性修复计划

日期: 2026-05-19

范围: Local to Bangumi `HumanCaseAgent` / focused gate。目标不是让固定层替 Agent 判断 target ownership，而是让 Agent 在任意一步随机走偏后，仍能识别低信息增益路径、保留已正确工作单元、切换到更高质量的证据 frontier，或以可审计的 exact work-unit blocker 收敛。

## 背景

`sample_0096` 的一次 fresh run 暴露了 Pleiades noisy evidence path:

- 正确的 unseasoned/root owner 没有稳定进入可用 surface。
- `source-query-only related` 被升级成 blocking `target_surface_actions`。
- OP/ED/角色歌/游戏/书籍/其他/角色出演/跨作品动画等低 owner 相关性的 related 条目消耗了 inspect/submit 预算。
- 最终 `fail_closed_title_tail_bridge_uninspected` 更像 `agent_recovery_failed`，不是样本本身不可判定。

当前已修复的局部问题:

- HumanCaseAgent legacy `inspect(..., related)` 已过滤非动画和 disallowed relation。
- 历史 registry 中的 disallowed related 不再进入 `visible_source_query_bridge_targets`。
- `sample_0096` related-filter focused gate 已 accepted。

但这只能切掉一类噪声，不能系统性保证任意一步随机走偏后都能恢复。

## 核心判断

不要实现状态机。

状态机容易把语义探索变成硬流程，并把固定层推向 target/special/target_absent 的语义裁决。需要实现的是一个可恢复的 agent scaffold:

1. evidence hygiene: 固定层过滤和标注证据质量。
2. action quality: 只有能回答当前 blocker 的高质量 action 才能成为 blocking action。
3. reflective repair: 每次 submit rejection 后，给 Agent 一个 compact 的反思面板，说明已尝试、低质量路径、下一 frontier、终止边界。
4. no-progress escape: 连续低信息增益时，降权重复路径，要求换 frontier 或 exact fail_closed。

固定层只做确定性、可验证、可审计的事情。Agent 仍然是唯一语义判断者。

## 非目标

- 不 hard-code `OVERLORD`、`Ple Ple Pleiades`、`193953` 或任何 sample-specific alias。
- 不自动选择 Bangumi target。
- 不把 source-query overlap 解释成 title alias。
- 不把 `fail_closed` 当作普通成功路径掩盖 retrieval/recovery failure。
- 不使用 Responses HTTP 不支持的 `previous_response_id`。
- 不为了缓存命中率引入 provider-side conversation state。

## 目标

任意一步随机走偏时，系统应满足:

- 错 search: 能发现 search 没覆盖 unresolved title-tail，要求 title-preserving/root/continuation frontier，而不是追 broad related noise。
- 错 inspect: 能判断 inspected target 没回答当前 blocker，把它写入 noisy/rejected memory，避免重复 inspect。
- 错 submit: verifier 挡住机械错误，并把 issue 编译成 exact local work-unit 的下一证据 frontier。
- 错 fail_closed: 只有高质量未检查证据能阻止 fail_closed；低质量 related/source-query-only noise 不能消耗最后预算。
- 进入循环: no-progress detector 能识别连续没有新增高质量候选、没有关闭 blocker、没有保留新 work unit 的回合，并强制换 frontier 或 exact fail_closed。

## 设计原则

- AI-first: Agent 判断 ownership、split、target_absent、supplemental、special 语义。
- Strict: verifier 继续挡错 mapping，不接受未覆盖或重复 target 的 package。
- Fixed layer only validates and surfaces: 固定层可以过滤非动画/弱 relation、计算 token overlap、统计是否新增高质量 evidence、检测重复 action，但不能决定 target。
- Evidence quality is not semantic ownership: 质量标签只描述“这条证据是否值得消耗预算”，不描述“它是否正确”。
- Frontier before finish: 未解决 blocker 必须先走有限、高质量 frontier；frontier 耗尽后才允许 exact fail_closed。
- Audit-first: 每个 recovery 决策都要能从 trace 解释，不依赖事后人工知道答案。

## Work Package 1: Evidence Quality Model

### 要改的地方

- `src/rename/case_agent/human_case_agent.py`
  - `AgentLocator`
  - `_register_existing_targets`
  - `_subject_with_search_query_provenance`
  - `_subject_with_related_query_provenance`
  - `_visible_source_query_bridge_targets`
  - repair agenda 构建函数

### 具体实现

给 target subject / target episode surface 增加 compact quality metadata:

- `evidence_origin`
  - `direct_search`
  - `direct_existing_surface`
  - `related_expansion`
  - `episode_surface`
- `relation_quality`
  - `direct`
  - `owner_relevant_related`
  - `weak_related`
  - `disallowed_related`
- `title_bridge_quality`
  - `title_or_alias_overlap`
  - `source_query_direct_overlap`
  - `source_query_related_only`
  - `no_title_bridge`
- `answers_current_blocker`
  - derived boolean, only true when evidence is connected to the exact local title-tail/range/special blocker by title/alias/source query from direct search, or by owner-relevant relation plus title-family/episode-shape support.

Rules:

- `disallowed_related` cannot be registered into active target surface.
- `weak_related` can appear only as diagnostic if already present.
- `source_query_related_only + no_title_bridge` cannot create blocking `target_surface_actions`.
- `owner_relevant_related + no_title_bridge` may be diagnostic; it becomes blocking only if it also has same-title-family, title-tail, or episode-shape support relevant to the exact blocker.

### Tests

- Related OP/ED/music/game/book/other/role entries never become blocking repair actions.
- Allowed related with title-family support can become a candidate action.
- Allowed related with no title bridge and no shape support remains diagnostic.

## Work Package 2: Repair Frontier Ledger

### 要改的地方

- submit rejection feedback compaction
- `_repair_agenda_from_submit_feedback`
- `_active_repair_agenda_for_prompt`
- session audit fields / trace summary

### 具体实现

For every open blocker, build a compact `repair_frontier` row:

```json
{
  "local": "local://...",
  "blocker": "fail_closed_title_tail_bridge_uninspected",
  "open_question": "root owner for title-tail is not established",
  "already_tried": ["search: Ple Ple Pleiades", "inspect: broad franchise subject"],
  "bad_paths": ["related_source_query_only: OP/ED/game/book"],
  "high_quality_next_actions": ["search: title-tail/root/seasonless", "inspect: same-title owner-relevant related"],
  "diagnostic_only": ["source-query-only weak related"],
  "terminal_boundary": "if high-quality frontier is exhausted, submit exact fail_closed naming retrieval gap"
}
```

This row is not a state machine. It is a current-turn evidence brief that helps the Agent recover from randomness.

### Tests

- A submit rejection with noisy related candidates produces `diagnostic_only`, not blocking `target_surface_actions`.
- A submit rejection with a direct visible title candidate produces a high-quality next action.
- Repeated rejection carries forward `already_tried` and does not erase saved mechanically-ok work units.

## Work Package 3: Action Quality Gate

### 要改的地方

- `_target_surface_actions_from_repair`
- `_repair_has_uninspected_target_surface_action`
- `_budget_pressure_tool_choice`
- `_budget_pressure_tool_rejection`

### 具体实现

Split actions into:

- `blocking_target_surface_actions`: high-quality and answers current blocker.
- `diagnostic_target_surface_actions`: useful context but not required before submit/fail_closed.
- `rejected_or_noisy_actions`: already tried or weak/no owner relevance.

Only `blocking_target_surface_actions` can force `inspect` near cap.

If only diagnostic/noisy actions remain:

- do not force inspect;
- ask Agent to choose another search frontier or submit exact fail_closed with the remaining gap;
- record `recovery_no_high_quality_action=true`.

### Tests

- Near-cap does not force inspect of source-query-only weak related.
- Near-cap still forces inspect for direct title/alias visible target surface.
- If no high-quality action remains and all local work units are covered, exact fail_closed is allowed.

## Work Package 4: No-Progress Detector

### 要改的地方

- `HumanCaseSession`
- tool output recording
- repair agenda tracking
- prompt health audit

### 具体实现

After each tool result, compute deltas:

- `new_high_quality_candidate_count`
- `new_blocking_action_count`
- `closed_blocker_count`
- `saved_work_unit_delta`
- `repeated_action_count`
- `new_noisy_candidate_count`

No progress if two consecutive tool turns meet all:

- no closed blocker;
- no saved mechanically-ok work unit delta;
- no new high-quality candidate;
- repeated same blocker;
- actions are weak related/search-query-only or repeat the same failed submit shape.

When no progress:

- downgrade repeated diagnostic actions;
- add prompt note: `current path is low information gain`;
- require a different frontier query/action or exact fail_closed for the blocker;
- increment audit counter `no_progress_escape_count`.

### Tests

- Repeating the same weak related inspect path triggers no-progress escape.
- Repeating a submit with unchanged invalid target triggers no-progress escape.
- New high-quality candidate resets the no-progress counter.

## Work Package 5: Generic Frontier Policies

These are not target choices. They generate evidence requests/search hints from visible local/target facts.

### 5.1 Title-Tail Root Owner Recovery

Trigger:

- local is main/movie/singleton;
- distinctive title-tail remains unbridged;
- visible same-title-family candidates are numbered/sequel/spinoff, or search-query-only related candidates dominate.

Actions:

- create title-preserving search hints from local label;
- derive root/unnumbered query from visible same-title-family target title by removing season/number suffix;
- inspect owner-relevant same-title related only if relation quality and title family support are present.

Forbidden:

- hard-code known aliases or Bangumi ids.

### 5.2 Continuation / Cour Recovery

Trigger:

- target episode surface ends before local range;
- local title/range suggests continuation or second part.

Actions:

- search title-preserving continuation/part/cour variants;
- prioritize direct title/range surface over broad same-franchise related.

### 5.3 Numbered Special Target-Side Check

Trigger:

- local numbered SP/OVA/OAD group is excluded/target_absent.

Actions:

- require finite target-side support from same-series/related inspected surface;
- if no corresponding target remains visible, allow target_absent/supplemental/non_bangumi with concrete negative-target reason.

### 5.4 Singleton Unassigned Target Check

Trigger:

- local singleton excluded/fail_closed while mapped subject still has visible unassigned episode item.

Actions:

- surface the specific item candidates;
- Agent must map, reject with reason, or exact fail_closed.

## Work Package 6: Reflective Prompt Patch

### 要改的地方

- HumanCaseAgent instructions/prompt construction
- active repair agenda prompt section

### 具体实现

Add a compact `RECOVERY_BRIEF` section after tool observations and before action selection:

```text
RECOVERY_BRIEF:
- Current blocker:
- What has already reduced uncertainty:
- What did not reduce uncertainty:
- High-quality next frontier:
- Diagnostic-only evidence:
- Terminal boundary:
```

Prompt instruction:

- Do not spend budget on diagnostic-only evidence unless you can explain how it answers the exact blocker.
- If no high-quality frontier remains, submit exact fail_closed with the remaining retrieval/evidence gap.
- Preserve saved mechanically-ok work units; change only blocked/missing units.

### Tests

- Prompt audit contains `RECOVERY_BRIEF` only when there is an open repair agenda.
- The brief is compact and bounded.
- Stable prefix/cache audit remains stable for instructions/tools/case desk where expected.

## Work Package 7: Audit And Replay Tooling

### 要改的地方

- `tools/summarize_local_bangumi_human_trace.py`
- per-turn audit payload

### 具体实现

Emit:

- `high_quality_candidate_count_by_turn`
- `diagnostic_candidate_count_by_turn`
- `noisy_candidate_count_by_turn`
- `blocking_action_count_by_turn`
- `no_progress_escape_count`
- `recovery_frontier_switch_count`
- `exact_fail_closed_after_frontier_exhausted_count`
- `weak_related_blocking_action_count` must be zero

For search:

- log query variants tried;
- log raw provider result ids/titles/ranks compactly;
- log skipped/truncated/deduped counts;
- log whether a direct title-tail bridge appeared.

This is required to prove whether future failures are retrieval absence, truncation, prompt choice, or semantic ambiguity.

## Execution Order

1. Add evidence quality helpers and unit tests.
2. Convert `visible_source_query_bridge_targets` to emit high-quality vs diagnostic rows.
3. Split `target_surface_actions` into blocking vs diagnostic.
4. Add repair frontier ledger to submit feedback and active prompt agenda.
5. Add no-progress detector and audit counters.
6. Add title-tail root owner recovery and continuation recovery as generic frontier policies.
7. Add `RECOVERY_BRIEF` prompt patch.
8. Update trace summarizer fields.
9. Run focused tests and sample gates.
10. Update manual replay docs with actual behavior.

## Verification Gates

### Unit / Focused

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py tests\test_case_agent_orchestrator_agent.py tests\test_ai_models.py tests\test_config_local_bangumi_case_agent_defaults.py tests\test_config_manager.py -q
```

### Broader Case Agent

```powershell
$files = Get-ChildItem tests -Filter 'test_case_agent_*.py' | ForEach-Object { $_.FullName }
.venv\Scripts\python.exe -m pytest $files tests\test_local_bangumi_sample_runner.py tests\test_ai_models.py tests\test_config_local_bangumi_case_agent_defaults.py tests\test_config_manager.py -q
```

### Compile

```powershell
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py
```

### Boundary Scan

```powershell
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\run_local_bangumi_mapping_sample_pool.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py --json
```

### Focused Replays

Run multiple fresh `sample_0096` replays because the observed issue is stochastic:

```powershell
for ($i=1; $i -le 5; $i++) {
  .venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir "tests\sample_pool\generated\local_bangumi_mapping_sample_0096_robustness_gate_$i_20260519"
}
```

Protection samples:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0035 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0035_robustness_protection_20260519
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0126 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0126_robustness_protection_20260519
```

## Acceptance Criteria

- `weak_related_blocking_action_count=0`.
- `source-query-only related + no title bridge` never forces inspect near cap.
- Repeated low-information actions trigger `no_progress_escape_count > 0` in synthetic tests.
- `sample_0096` fresh replay batch:
  - no unsafe accepted;
  - no repeated noisy related loop;
  - `near_turn_limit_unhealthy_count=0`;
  - if accepted, Pleiades is recovered by visible evidence, not fixed-layer target choice;
  - if not accepted, final result is a concrete retrieval/evidence frontier exhaustion, not `unresolved_submit_repair`.
- Focused/protection samples do not regress existing strict behavior.
- Boundary scan remains `finding_count=0`.

## Expected Failure Semantics

When the Agent still cannot recover, final audit should distinguish:

- `semantic_ambiguity`: visible high-quality evidence conflicts.
- `retrieval_exhausted`: high-quality frontier searched/inspected but owner not visible.
- `agent_recovery_failed`: no-progress detector fired, but Agent still did not change frontier or exact fail_closed correctly.
- `provider_failure`: provider retry/execution failure.

Product-level result may still be `fail_closed`, but trace summary must expose the finer reason so we do not mistake structural recovery failure for valid semantic uncertainty.

## Implementation Result: 2026-05-19

Implemented in:

- `src/rename/case_agent/human_case_agent.py`
- `src/rename/case_agent/local_bangumi_entry.py`
- `tools/summarize_local_bangumi_human_trace.py`
- `tools/run_local_bangumi_mapping_sample_pool.py`
- `tests/test_case_agent_human_case_agent.py`
- `tests/test_case_agent_human_cognitive_workspace.py`
- `tests/test_local_bangumi_sample_runner.py`

Delivered:

- `AgentLocator` now carries evidence origin, relation quality, title bridge quality, and current-blocker relevance.
- Weak source-query-only related evidence is diagnostic unless it also has title/shape support for the current blocker.
- Submit repair feedback splits blocking actions, diagnostic actions, and rejected/noisy actions.
- Repair feedback includes `repair_frontier` rows with blocker, already-tried paths, high-quality next actions, diagnostic-only evidence, and terminal fail-closed boundary.
- `RECOVERY_BRIEF` is emitted in the turn tail while a repair agenda is open.
- No-progress/recovery counters are recorded in session summaries, snapshots, trace summaries, and sample-run summaries.
- Late-turn near-cap audit is suppressed when a recovery detector already explains the terminal path.

Final validation:

- focused pytest: `211 passed`
- broader case-agent pytest: `674 passed, 8 skipped`
- compile: passed
- boundary scan on fixed-layer code: `finding_count=0`
- boundary scan on touched reporting tools: `finding_count=0`

Final `sample_0096` replay batch:

- `local_bangumi_mapping_sample_0096_robustness_final_gate_1_20260519`: `fail_closed`, `agent_recovery_failed`, verifier passed, near-turn `0`, weak-related `0`, strict `0`
- `local_bangumi_mapping_sample_0096_robustness_final_gate_2_20260519`: `fail_closed`, `agent_recovery_failed`, verifier passed, near-turn `0`, weak-related `0`, strict `0`
- `local_bangumi_mapping_sample_0096_robustness_final_gate_3_20260519`: `fail_closed`, `agent_recovery_failed`, verifier passed, near-turn `0`, weak-related `0`, strict `0`
- `local_bangumi_mapping_sample_0096_robustness_final_gate_4_20260519`: `fail_closed`, `agent_recovery_failed`, verifier passed, near-turn `0`, weak-related `0`, strict `0`
- `local_bangumi_mapping_sample_0096_robustness_final_gate_5_20260519`: `fail_closed`, `agent_recovery_failed`, verifier passed, near-turn `0`, weak-related `0`, strict `0`

Protection:

- `local_bangumi_mapping_sample_0035_robustness_protection_20260519`: `fail_closed`, `agent_recovery_failed`, verifier passed, near-turn `0`, weak-related `0`, strict `0`
- `local_bangumi_mapping_sample_0126_robustness_protection_20260519`: `fail_closed`, `agent_recovery_failed`, verifier passed, near-turn `0`, weak-related `0`, strict `0`

Acceptance status:

- No unsafe accepted result in the final replay batch.
- No weak-related blocking action loop.
- No `unresolved_submit_repair` summary in the final replay batch.
- Fixed layer did not add sample-specific alias/id/target mappings and did not choose ownership/special/target_absent semantics.

## Recommended Goal Prompt

```text
/goal 按 docs\LOCAL_BANGUMI_HUMAN_CASE_AGENT_ROBUSTNESS_PLAN.md 执行 HumanCaseAgent 鲁棒性修复：不要实现状态机，不写样本专属 alias/id/target 映射；固定层只做 evidence hygiene、action quality、repair frontier、no-progress escape、audit，不做 ownership/special/target_absent 语义裁决。完成 evidence quality model、blocking/diagnostic action split、repair frontier ledger、no-progress detector、generic title-tail/root/continuation frontier、RECOVERY_BRIEF prompt、trace summary 字段和测试。以 sample_0096 多次 fresh replay 加 0035/0126 protection、unit/focused/broader pytest、compile、boundary scan 为验收，并更新相关复盘文档。
```

## Bounded CaseResolutionGoal Addendum: 2026-05-19

The robustness pass above made repair feedback more durable, but it still left
the Agent in a loose prompt loop: the fixed layer could tell it what was wrong,
yet the Agent did not have to pick a bounded recovery mode or prove progress
against the current blocker.

This follow-up adds a bounded `CaseResolutionGoal` scaffold:

- Every tool schema now exposes `repair_strategy`.
- Allowed strategies are `repair_single`, `repair_cluster`, `repartition`,
  `gather_evidence`, `revise_saved_rows`, and `terminal_fail_closed`.
- `CASE_STATE.case_memory.case_resolution_goal` shows the active objective,
  remaining turn budget, strategy history, active blockers, strong candidates,
  saved mechanically-ok rows, progress ledger, and terminal fail-closed
  contract.
- If the same blocker plus same submit shape repeats, the fixed layer records
  `strategy_change_required=true`; the next turn cannot reuse the blocked
  strategy unless it submits a valid `terminal_fail_closed`.
- A terminal fail-closed submit must name all active blocking/missing local
  locators, address strong candidate surfaces, leave no unexecuted blocking
  evidence action, preserve saved ok rows, and provide concrete
  non-progressable reasons.

Boundary:

- The fixed layer still does not choose Bangumi targets, special/OVA ownership,
  target_absent, work-unit ownership, or sample-specific mappings.
- Strong candidates are surfaced as evidence obligations, not as accepted
  semantic answers.

New audit fields:

- `case_resolution_goal_status`
- `case_resolution_goal_strategy_counts`
- `case_resolution_goal_progress_count`
- `case_resolution_goal_progress_ledger`
- `same_blocker_strategy_change_required_count`
- `case_resolution_goal_terminal_rejection_count`
- `obvious_terminal_fail_closed_count`
- `repair_strategy_missing_count`

Validation:

- focused pytest plus sample-runner unit tests: `224 passed`
- compile: passed for `src/ai`, `src/rename/case_agent`,
  `tools/run_local_bangumi_mapping_sample_pool.py`, and
  `tools/summarize_local_bangumi_human_trace.py`
- boundary scan on `src/rename/case_agent`: `finding_count=0`
- boundary scan on touched reporting tools: `finding_count=0`

Sample gates:

- `sample_0096`: `tests/sample_pool/generated/local_bangumi_mapping_gate_20260519_150428_845`,
  `status=fail_closed`, `summary=agent_fail_closed_from_submit`,
  `case_resolution_goal_status=accepted_or_idle`,
  `obvious_terminal_fail_closed_count_total=1`,
  `strict_failure_count=0`
- `sample_0035` and `sample_0126`:
  `tests/sample_pool/generated/local_bangumi_mapping_gate_20260519_150833_491`,
  both `status=fail_closed`;
  summaries are `obvious_terminal_fail_closed` and
  `agent_fail_closed_from_submit`;
  `strict_failure_count=0`

## Future Plan: Fact Surface, Not Fixed-Layer Candidate Generation

The executable goal plan for this direction is now
`docs/LOCAL_BANGUMI_LOCAL_FACT_SURFACE_GOAL_PLAN.md`.

The original discussion is documented in
`docs/LOCAL_BANGUMI_MANUAL_REPLAY_SAMPLE_0096.md` under
`2026-05-19 Next Plan: Agent-Owned Evidence Composition For Derivative Shorts`.

Key boundary:

- Fixed layer may expose raw local media facts such as duration, file count,
  numbered labels, path hierarchy, and container metadata for explicit local
  locators.
- Fixed layer may expose raw Bangumi related-subject facts such as relation
  label, subject title, aliases returned by Bangumi, episode count, and visible
  item refs.
- Fixed layer must not generate derivative mapping candidates, recommended
  targets, strong semantic candidates, target_absent decisions, supplemental
  decisions, or sample-specific title/id bridges.
- Agent must request these facts, compose the derivative/duplicate hypothesis,
  and submit the semantic decision with cited evidence.
- Verifier may check that the submitted decision cites the evidence classes it
  claims to use, and may reject duplicate target usage or unsupported claims,
  but it must not choose the target or outcome.

This keeps the system strict while giving the Agent the same kind of evidence a
human used for `sample_0096`: duration and related-graph facts. Unknown rows
remain fail_closed or future unresolved/manual-review rows; they must not be
accepted as generic supplemental merely to unblock the rest of the package.
