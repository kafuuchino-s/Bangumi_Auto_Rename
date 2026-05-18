# Local to Bangumi HumanCaseAgent 人工外层工作流内化计划

更新日期: 2026-05-19

## Summary

本计划覆盖之前的局部注意力/repair agenda 计划。当前目标不是追单个样本 accepted，也不是继续给某个标题、Bangumi subject、special、target_absent 或 file-to-target 写补丁。

目标是把人类复盘 Local to Bangumi 样本时的外层工作流内化到 `HumanCaseAgent` 的运行结构里:

1. 稳定理解本地 case 和 work units。
2. 主动用 `search` / `inspect` 查 Bangumi subject、episodes、related、special、aliases 和 details。
3. 持续维护案头状态，而不是只看最后一条 tool observation。
4. 把 submit rejection 转成 durable repair agenda。
5. 让 active repair agenda 驱动下一步行动。
6. 在 stall 或 near-cap 时，围绕当前 blocker 收尾: 补证据，或对 exact work unit 给出具体 `fail_closed`。
7. 固定层只做机械验证、状态保存、工具执行、运行健康审计和反馈呈现。

一句话: HumanCaseAgent 仍然是唯一语义判断者；固定层负责把“人类会执行的工作流纪律”机械地钉住，但不能替 Agent 判断作品语义。

## Core Principle

HumanCaseAgent 是唯一语义判断者。

固定层允许做:

- locator 解析和 canonicalize
- schema 校验
- ref 可见性校验
- support 可见性校验
- coverage / overlap / duplicate / accounting 校验
- evidence tool 执行
- cognitive workspace 的结构保存和 compact
- submit rejection 的机械归因
- budget / loop / provider / context health 审计
- 把机械事实反馈给 Agent

固定层禁止做:

- 自动选择 Bangumi target
- 自动判断 special / OVA / OAD / SP
- 自动判断 target_absent
- 自动 split work unit
- 用语义理由拒绝 Agent 的 terminal outcome
- 写样本专属 title、Bangumi id、Bangumi path、file-to-target 规则
- 恢复旧 Orchestrator / Planner / Judge / Editor 作为主路径

## What “Human-Like” Means

人类复盘失败样本时的关键行为不是“知道更多作品知识”，而是外层执行纪律:

1. 看到当前主问题。
2. 把问题落成一个 work unit / agenda item。
3. 下一步只围绕这个 agenda 行动。
4. 如果证据不足，明确要查哪个 visible locator / target surface。
5. 如果快到预算上限，停止泛搜和泛 submit。
6. 对 exact work unit 收尾: accepted、supplemental、target_absent 或 fail_closed，但语义判断必须来自 Agent。

因此要修的是结构闭环:

```text
submit rejection
-> active repair agenda
-> required next action
-> closure condition
-> progress/stall audit
-> near-cap finalization
-> exact work-unit resolution or concrete blocker
```

## Agent-Facing Workspace

新增或等价扩展 `CaseCognitiveWorkspace`，作为每 turn prompt 的优先上下文。

建议字段:

- `primary_hypotheses`
- `active_work_units`
- `attention_focus`
- `investigation_agenda`
- `active_repair_agenda`
- `rejected_or_noisy_candidates`
- `evidence_gaps`
- `resolution_readiness`

固定层职责:

- 保存这些状态。
- compact 过长内容。
- 校验 locator/ref 是否可见。
- 校验字段结构是否符合 tool schema。
- 不判断这些语义内容是否“对”。

Agent 职责:

- 通过 `note` 表达语义判断和 workspace update。
- 决定哪个 target 值得 inspect。
- 决定 special / target_absent / split / supplemental 是否成立。
- 决定 terminal submit 内容。

## Tool Surface

v1 保持简单工具:

- `inspect`
- `search`
- `note`
- `submit`

`note` 可以承载 workspace update。只有当实现明显更清楚时，才允许新增轻量 `update_workspace`。不得引入 planner/editor/judge 式子 Agent。

`inspect` 应支持从已确认 subject 出发查看:

- `episodes`
- `related`
- `specials`
- `aliases`
- `details`

`search` 结果展示应分层:

- title relevance
- subject relationship
- previous focus
- rejected/noisy candidate suppression
- source-query provenance

被 Agent 标记为 low relevance 的候选进入 `rejected_or_noisy_candidates`，后续不能作为同权候选反复污染主线。

## Submit As Resolution Checkpoint

`submit` 是唯一 terminal resolution 通道，不是试错工具。

submit rejection 只能返回机械缺口，例如:

- local locator 未覆盖
- target locator 未 inspect
- hidden/unknown locator
- duplicate target
- accounting 不完整
- support 不可见
- coverage mismatch
- composite/count shape mismatch

submit rejection 必须映射回:

- `active_work_units`
- `active_repair_agenda`
- `resolution_readiness`

每条 repair agenda 至少包含:

- `work_unit` 或相关 local locator
- `blocking_issue`
- `locators`
- `required_next_action`
- `closure_condition`
- 可选 `target_surface_actions`

重复同类 rejection 时，固定层只能提示:

> 同一机械缺口未解决，需要回到 inspect/search/note，或对对应 exact work unit 给出具体 fail_closed。

固定层不得替 Agent 选择 target 或 outcome。

## Runtime Health And Finalization Guard

每 turn 输出或记录:

- `active_focus_changed`
- `new_evidence_added`
- `agenda_item_closed`
- `resolution_readiness_changed`

如果连续 2 turn 没有上述变化，标记 `stall_warning`。

如果 active repair agenda 存在且重复 submit 没有关闭 agenda:

- 升级 guard 文案。
- 禁止把 submit 当泛试错工具。
- 要求 Agent 回到当前 repair agenda。

接近 turn cap 时触发 `near_cap_repair_finalization_guard`:

- 不强行语义 accepted。
- 不替 Agent 选 target。
- 不替 Agent 判断 special / target_absent / split。
- 要求 Agent 只能做两类收尾动作:
  - 调用 `inspect` / `search` / `note` 补当前 agenda 列出的可见证据。
  - 对 exact work unit 给出具体 `fail_closed` blocker。

near-cap guard 的目标不是“帮样本过”，而是避免裸 budget fail、submit loop 和无具体 blocker 的结构失败。

## Manual Replay Discipline

每个 focused 或 convergence 失败样本，必须先产出人工复盘 artifact，再允许改代码。

artifact 至少包含:

- `sample_id`
- `manual_human_path`
- `agent_actual_trace`
- `divergence_point`
- `gap_category`
- `is_generic_architecture_gap`
- `proposed_generic_fix`
- `proposed_fix_layer`
- `fixed_layer_boundary_check`
- `rerun_gate`

`gap_category` 只能从这些类别中选择:

- `state_structure`
- `tool_boundary`
- `evidence_surface`
- `prompt_overconstraint`
- `verifier_feedback`
- `provider_or_context_health`
- `model_variance`
- `safe_fail`

人工复盘只能使用主流程可见 evidence:

- raw sample / local sample 文件结构
- HumanCaseAgent 可 inspect 的 local locator
- HumanCaseAgent 可 search / inspect 到的 Bangumi subject、episodes、related、details、aliases
- runner snapshot
- tool observation
- verifier issue
- audit

不能把主流程看不到的外部知识直接写成修复依据。可以用人工知识提出“人会继续查哪里”，但最终修复必须让 Agent 通过同类 evidence surface 自己完成判断。

## Fixed Tools For The Outer Workflow

外层复盘工作流可以固定成工具，但这些工具不是 HumanCaseAgent 的语义子 Agent。

固定工具链:

- `tools/run_local_bangumi_human_gate.py`: 单样本 focused gate wrapper。
- `tools/summarize_local_bangumi_human_trace.py`: 机械 trace summary。
- `tools/scaffold_local_bangumi_manual_replay.py`: manual replay artifact scaffold。
- `tools/scan_local_bangumi_boundary_risks.py`: 边界风险扫描。

这些工具允许做:

- 汇总 tool sequence、turn count、submit rejection、stall warning。
- 提取 provider/schema/budget/loop/error。
- 标记 legacy subagent 调用风险。
- 扫描样本 id、Bangumi id、硬编码标题桥、固定层语义裁决。
- 生成人工复盘模板。

这些工具禁止做:

- 替 Agent 选择 target。
- 自动判断 sample semantic correctness。
- 自动生成 file-to-target mapping。
- 把 accepted 当唯一优化目标。

## Code Change Gate

只有同时满足以下条件才允许改代码:

- 已完成对应失败样本的 manual replay artifact。
- divergence 已经定位。
- 归因为通用结构问题。
- proposed fix 不需要样本专属规则。
- fixed layer boundary check 通过。
- 能设计 unit/compile/boundary scan/focused gate 验收。

允许修复的层:

- prompt 行动空间
- cognitive workspace 结构
- active repair agenda
- resolution readiness
- search / inspect evidence surface
- submit / verifier 机械反馈
- budget / loop / provider health
- trace summary / boundary scan / focused gate 工具

禁止修复的层:

- 样本专属 alias
- 样本专属 Bangumi subject id
- 样本专属 file-to-target mapping
- 固定层语义纠偏
- 固定三阶段状态机
- 旧 Orchestrator / Planner / Judge / Editor 主路径

旧测试如果已经表达过时行为，可以删除或改写，但必须保留这些覆盖:

- fixed layer 边界
- hidden/unknown locator 拒绝
- workspace compact 后保留关键案头状态
- active repair agenda durable
- submit rejection 映射到 work unit 和 readiness 缺口
- repeated submit / near-cap guard 不选 target、不选 outcome

## Current Gate

当前 gate 使用:

`docs/LOCAL_BANGUMI_MANUAL_REPLAY_SAMPLE_0096.md`

当前已知结构结论:

- 0096 不是要靠固定层写 Overlord / Pleiades / movie title bridge 过样本。
- 当前核心 gap 是 `state_structure`: active repair agenda 能进入案头，但 near-cap/stall 时还没有足够强地驱动下一步行动。
- 目标是实现通用 `near-cap active repair agenda finalization guard`。

0096 验收不要求必须 accepted。

可接受结果:

- accepted 且人工 spot check 无 unsafe accepted。
- 或 fail_closed，但必须是具体 unresolved repair agenda / visible evidence blocker。

不可接受结果:

- provider/schema/tool-boundary error。
- 裸 `budget_exhausted`。
- submit loop。
- 无 divergence / 无 blocker 的结构失败。
- 依赖样本语义补丁才通过。

## Implementation Phases

1. 复核计划和 0096 manual replay artifact。
2. 复核当前 `HumanCaseAgent` 中 workspace、repair agenda、submit rejection、prompt health 的实现。
3. 实现 near-cap/stall repair agenda finalization guard。
4. 确保 guard 只做机械行动约束，不做语义选择。
5. 更新 unit tests。
6. 运行 unit、compile、boundary scan。
7. 只跑 sample_0096 focused gate。
8. 若 accepted，人工 spot check mapping safety。
9. 若 fail_closed，确认是具体 blocker，不是结构失败。
10. 更新 0096 manual replay artifact。

## Test Plan

Unit tests:

- cognitive workspace 保存主假设、work unit、agenda、rejected candidates，并在 compact 后保留。
- hidden/unknown locator refs 被拒绝；固定层不校验语义真假。
- search 结果中 rejected/noisy candidate 不再同权重进入下一轮 prompt。
- submit rejection 能映射到具体 work unit 和 mechanical readiness 缺口。
- repeated submit without agenda closure 触发 stronger guard。
- near-cap active repair agenda guard 出现在 prompt/tool health。
- guard 不自动选择 target 或 outcome。
- 固定层不能因 special/OVA/OAD/SP、target_absent、subject 相似度或 title bridge 做 hard reject。

Focused validation:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_case_agent_human_case_agent.py tests\test_case_agent_human_cognitive_workspace.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py tools\scaffold_local_bangumi_manual_replay.py tools\scan_local_bangumi_boundary_risks.py --json
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_repair_finalization_gate_20260519
```

不跑 full / convergence，除非后续另立 goal。

## Acceptance

结构验收:

- `legacy_subagent_call_count=0`
- `boundary scan finding_count=0`
- `invalid=0`，除非是明确 provider/tool schema 错误且已归因。
- 无 naked budget fail。
- 无 submit loop。
- active repair agenda 在 prompt 中优先显示。
- repeated submit / near-cap 能转成具体 action guard。
- 失败时能给出 exact work unit blocker。

业务安全验收:

- 若 accepted，人工 spot check 无 unsafe accepted。
- 若 fail_closed，必须有具体 evidence blocker。
- 不以 accepted 数量作为本阶段唯一成功标准。

## Goal Command

复制下面短 goal 执行；完整边界和验收以本文档正文为准:

```text
/goal 按 docs\LOCAL_BANGUMI_HUMAN_LIKE_ORCHESTRATOR_GOAL_PLAN.md 完成 HumanCaseAgent 人工外层工作流内化：只修通用结构，让 active repair agenda 驱动下一步行动，并在 stall/near-cap 时强制补证据或 exact work-unit fail_closed。固定层只做机械验证和运行健康审计，不做 target/special/target_absent/split 语义选择，不写样本规则。以 sample_0096 manual replay 为 gate，跑文档规定的 unit/compile/boundary scan/focused gate，并更新复盘 artifact。
```

## Non Goals

- 不追 full146。
- 不直接追 convergence accepted 全绿。
- 不在没有 manual replay artifact 的情况下改代码。
- 不恢复 Stage A / GlobalIngest。
- 不接 TMDB / Emby / 实际重命名执行。
- 不引入固定三阶段主循环替代 Agent 自由调查。
- 不让固定层做 subject/special/target_absent/split 语义选择。
- 不把 Bangumi target ownership 写进规则。
