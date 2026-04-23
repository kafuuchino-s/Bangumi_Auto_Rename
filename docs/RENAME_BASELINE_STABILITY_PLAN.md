# Rename baseline 稳定性维护计划

更新时间：2026-04-24
状态：进行中

> 本文档维护 **baseline 稳定性主线** 的目标、当前结论、证据、观察项与下一步动作。
> 具体执行命令、回归模式、baseline 刷新动作请看 `docs/RENAME_REGRESSION_WORKFLOW.md`。

## 1. 文档目的与边界

本文档回答的是：

- 当前 rename baseline 是否足够稳定
- 哪些现象属于 baseline 机制问题，哪些属于业务运行结果漂移
- 哪些样本可以进入日常阻塞集，哪些仍应继续观察
- 下一轮 soak / 复核应该怎么推进

本文档**不是**回归操作手册，不重复维护完整命令说明，也不取代 `RENAME_REGRESSION_WORKFLOW.md`。

## 2. 目标与非目标

### 当前目标

1. 确认当前 `check` 集在多轮运行下是否稳定。
2. 确认保护样本 `sample_0091_vcb_studio_kimetsu_no_yaiba` 是否仍存在间歇性漂移。
3. 区分“baseline 文件需要刷新”和“真实执行产物在漂”这两类问题。
4. 为后续是否补 flaky / baseline 治理机制提供依据。

### 非目标

1. 不在本文档里重复记录所有 CLI 参数与命令样例。
2. 不把单次样本通过等同于 baseline 已稳定。
3. 不把个别样本的业务修复自动等同于 baseline 机制已系统治理。

## 3. 与现有 workflow 文档的分工

- `docs/RENAME_REGRESSION_WORKFLOW.md`：负责 **怎么跑**
  - `check / full / update-baseline`
  - manifest / baseline / run artifact 位置
  - baseline 刷新规则
  - 结果状态定义

- `docs/RENAME_BASELINE_STABILITY_PLAN.md`：负责 **为什么这样维护、当前稳定到什么程度、下一步还要验证什么**
  - 稳定性目标
  - 观察重点
  - soak 计划
  - run 证据
  - 失败分类
  - 后续动作

## 4. 当前已确认事实

### 4.1 baseline 机制层面的现状

当前仓库已经有：

- baseline 读写
- `check / full / update-baseline` 三种模式
- compare 归一化
- 单次失败后重试并标记 `flaky` 的基础逻辑

当前仓库**还没有**单独成体系的：

- 多轮一致性判定框架
- soak 结果汇总机制
- 更强的 flaky 识别与升级策略
- baseline 稳定性长期治理文档

因此，当前 baseline 主线仍需要通过本文档持续维护。

### 4.2 本轮已经解决的真实漂移问题

本轮确认并修复了两类真实业务漂移：

1. **显式 `SxxEyy` 文件被音轨数字误导**
   - 典型误导源：`DDP5.1`、`FLAC 2.0`
   - 影响：TV 文件被错误当作全局集号参与重映射
   - 相关修复文件：`src/rename/ai_processor.py`

2. **`sample_0091_vcb_studio_kimetsu_no_yaiba` 的 TV/Movie 边界漂移**
   - movie collection 在 `is_collection=False` 且空 mapping 时偶发失败
   - TV arc 目录曾被误推入 movie 分支，额外落出失败 movie task
   - 相关修复文件：`src/rename/process.py`

### 4.3 本轮已经落地的提交

- `33f502a` `🧪 支持回归 CLI 多样本选择并收紧 changed_paths 探测`
- `dd38788` `🛡️ 稳定 TV/Movie 边界并收敛 0091 漂移`

## 5. 样本分层与维护口径

### 日常阻塞集

以 `manifest` 中 `check=true` 的样本为准。当前 workflow 文档记录的 `check` 集包含 5 个样本。

### 保护/观察样本

`sample_0091_vcb_studio_kimetsu_no_yaiba` 当前仍是：

- `check = false`
- `anchor = true`

它不是日常阻塞样本，但会通过保护样本扩圈进入与 `tv_strict_mapping` 相关的检查，因此仍是 baseline 稳定性主线里的关键观察对象。

### 维护口径

1. 能稳定进入 `check` 的样本，必须经受多轮真实执行验证。
2. 仅在 `full` 中观察的样本，不因为单次通过就提升到 `check`。
3. 若样本仍存在间歇性真实漂移，不应硬塞进日常阻塞集。

## 6. baseline 健康指标

当前主要观察以下指标：

1. `check` / targeted soak 的通过率
2. `product_failure_count`
3. `flaky_count`
4. `baseline_missing_count`
5. 关键保护样本的 `task_files` / `record_files` 是否稳定
6. 是否出现新的失败形态，而不是只看是否“通过”

当前特别关注：

- `sample_0091` 是否重新出现
  - `ai_empty_mapping`
  - movie route 丢失
  - 额外失败 movie task
  - `task_files=4` 但 `record_files=3`

## 7. 当前阶段结论

截至 2026-04-24，能确认的结论是：

1. **问题主因不是 baseline 文件本身，而是业务执行结果曾经真实漂移。**
2. 之前已确认的漂移根因已经修复，并提交到：`dd38788`。
3. 以本文档为维护入口后的当前计划轮次（10 轮 targeted + 2 轮 full `check`）已执行完成，并保持连续通过。
4. 这说明：
   - “之前看起来像 baseline 不稳定”的主因已被明显收敛；
   - 但 baseline 机制层面的长期治理还没单独建设。

## 8. 关键证据记录

### 8.1 本轮重点 targeted soak 证据

修复后连续通过的 targeted run：

- `20260423T190401Z-1e194772`
- `20260423T190835Z-b00a70e0`
- `20260423T191101Z-3fd58d7b`
- `20260423T191320Z-09655900`
- `20260423T192417Z-67b566fc`

这些 run 的共同特征：

- `selected_count = 3`
- `passed_count = 3`
- `product_failure_count = 0`
- `flaky_count = 0`
- `sample_0091_status = passed`
- `task_files = 4`
- `record_files = 4`

### 8.2 本轮 full `check` 证据

- `20260423T192741Z-2b3e2af7`

该 run 的结论：

- `selected_count = 6`
- `passed_count = 6`
- `product_failure_count = 0`
- `flaky_count = 0`
- `sample_0091_status = passed`
- `task_files = 4`
- `record_files = 4`

### 8.3 历史失败形态（已知且本轮未再复现）

1. movie 子任务掉成 `ai_empty_mapping`
2. `task_files = 4` 但 `record_files = 3`
3. 额外多出一条失败 movie route / task，同时正确 movie route 仍在

这些历史失败形态是本轮 soak 重点复核对象。

### 8.4 2026-04-24 第一批 soak 结果

本计划启动后的第一批执行结果：

- targeted soak：5 轮
  - `20260423T204320Z-51b65d90`
  - `20260423T204713Z-e3fa1c38`
  - `20260423T205047Z-2b1ac83e`
  - `20260423T205406Z-91611c23`
  - `20260423T205711Z-14f6b9bb`
- full `check`：1 轮
  - `20260423T210011Z-d146946a`

这一批结果的共同结论：

1. 5 轮 targeted 全部通过：
   - `selected_count = 3`
   - `passed_count = 3`
   - `product_failure_count = 0`
   - `flaky_count = 0`
   - `sample_0091_status = passed`
   - `task_files = 4`
   - `record_files = 4`

2. 1 轮 full `check` 通过：
   - `selected_count = 6`
   - `passed_count = 6`
   - `product_failure_count = 0`
   - `flaky_count = 0`
   - `sample_0091_status = passed`
   - `task_files = 4`
   - `record_files = 4`

3. 这一批 run 的 `run_context` 显示：
   - targeted 仍为 `0117 + 0123 + 自动扩圈的 0091`
   - full `check` 仍为 5 个 `check=true` 样本 + 自动扩圈的 `0091`
   - 由于相关修复已经提交，`changed_paths` 为空，当前属于“已提交代码后的稳定性 soak”

4. 当前解释：
   - 第一批 soak 没有重新触发历史两类 `0091` 漂移
   - 但整个计划尚未结束，仍需完成剩余 5 轮 targeted 和至少 1 轮 full `check`

### 8.5 2026-04-24 第二批 soak 结果

本计划第二批执行结果：

- targeted soak：5 轮
  - `20260423T211013Z-87242b7e`
  - `20260423T211305Z-f2841434`
  - `20260423T211533Z-0f24ee4a`
  - `20260423T211853Z-b7a0e3eb`
  - `20260423T212211Z-cb4a3b0c`
- full `check`：1 轮
  - `20260423T212545Z-a61e23e1`

这一批结果的共同结论：

1. 5 轮 targeted 全部通过：
   - `selected_count = 3`
   - `passed_count = 3`
   - `product_failure_count = 0`
   - `flaky_count = 0`
   - `sample_0091_status = passed`
   - `task_files = 4`
   - `record_files = 4`

2. 1 轮 full `check` 通过：
   - `selected_count = 6`
   - `passed_count = 6`
   - `product_failure_count = 0`
   - `flaky_count = 0`
   - `sample_0091_status = passed`
   - `task_files = 4`
   - `record_files = 4`

3. 与第一批一致：
   - `changed_paths` 仍为空
   - 当前 soak 继续属于“相关修复已提交后”的稳定性观察
   - 历史两类 `0091` 漂移仍未复现

4. 截至本批结束，当前计划中承诺的：
   - `10` 轮 targeted soak
   - `2` 轮 full `check`
   已全部完成，且未观察到新的 `product_failed` / `flaky` / `baseline_missing`

## 9. 失败分类规则

后续 soak 中若再次失败，优先按下面口径分类：

### A. baseline 机制问题

典型特征：

- `baseline_missing`
- 必须 `update-baseline` 才能继续推进
- compare / run artifact 收集逻辑本身异常

### B. 业务执行漂移问题

典型特征：

- manifest / run_context 一致，但实际产物不一致
- 同输入下 route / mapping / task / record 出现非确定性变化
- 失败样态能定位到 `Rename.process(...)` 真实执行分支

### C. flaky 识别不足

典型特征：

- 多轮 soak 已经证明存在间歇性失败
- 但 `flaky_count` 仍为 0
- 当前自动重试与状态升级规则没有有效暴露问题

## 10. 下一轮 soak 计划

当前建议的下一轮计划：

1. 当前计划轮次已经完成
   - targeted：累计 10 轮完成
   - full `check`：累计 2 轮完成

2. 如果需要继续增强把握，再决定是否进入下一轮更长 soak
   - targeted：再追加 10 轮
   - full `check`：再追加 2~3 轮

3. 每轮记录以下字段
   - `run_id`
   - `selected_count`
   - `passed_count`
   - `product_failure_count`
   - `flaky_count`
   - `sample_0091_status`
   - `task_files / record_files`

4. 若再次失败，必须记录失败形态属于：
   - 旧问题复发
   - 新失败分支
   - baseline 机制问题

## 11. 是否需要补机制的判断门槛

满足以下任一条件，应考虑继续补 baseline / flaky 治理机制：

1. 修复后 soak 仍持续出现间歇性失败
2. 失败已可稳定复现，但 `flaky_count` 仍无法正确暴露
3. `check` 集稳定，但保护样本长期依赖人工观察，无法形成明确升级标准
4. 每次回到 baseline 主线时，都需要重新人工拼接上下文才能继续推进

## 12. 当前下一步

当前优先级：

1. 用本文档持续维护 baseline 主线，不再依赖临时记忆。
2. 当前计划中的 `10` 轮 targeted + `2` 轮 full `check` 已完成，阶段性结果为稳定。
3. 下一步不是立即改代码，而是根据是否还需要更强置信度，决定是否追加更长轮次 soak。
4. 若后续继续稳定，再考虑是否把 `sample_0091` 的维护口径从“重点观察”调整为更稳定状态。
