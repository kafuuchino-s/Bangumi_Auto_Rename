# Project Structure And Flow

Bangumi Auto Rename 是 Python + NiceGUI 的媒体整理应用。rename 主线已端到端落地 **Local→Bangumi Case Agent → BGM→TMDB 桥接 → 迁移落盘**，并随带字幕 sidecar 跟随、Emby 刷新与 Telegram 汇总。

## Main Flow

```text
Web UI / qBittorrent webhook
→ src/web.py path normalization and queue entry
→ src/queue/task_queue.py workers
→ src/rename/process.py::Rename.process()
→ LocalEvidence
→ Local→Bangumi Case Agent（evidence request / MappingDraft / Verifier 合同校验）
→ accepted / fail_closed / invalid
→ accepted 经 BGM→TMDB 桥接：TMDB 合法落点、最终文件名、迁移落盘
→ 可选：字幕 sidecar 跟随 / 自动抓取
→ 批次结束后：Emby 刷新 + Telegram 汇总
```

兼容旧链路（Case Agent 不可用或关闭时）：

```text
→ AI 提取标题/类型 → TMDB 候选选择 → TV/Movie 分支 → ai_processor 严格映射 → Trans.trans_file()
```

## Rename Boundary

`Rename.process()` 的职责：

- 校验路径、收集本地视频文件。
- 构建 `LocalEvidence`。
- 调用 `run_local_bangumi_case_agent_mapping()`，得到 `accepted / fail_closed / invalid`。
- accepted 时进入 BGM→TMDB 产品链路：编译 plan、桥接 agent、verified rename plan、迁移落盘。
- 写入任务 JSON 与 decision snapshot。

它不预先语义拆包；是否拆成 child cases、是否请求更多 Bangumi 证据、哪些文件属于同一个 Bangumi 条目，全部由 Case Agent 决定。

## Case Agent

入口：`src/rename/case_agent/local_bangumi_entry.py`

核心模块：

- `pi_runner.py` / `pi_tools.py`: Pi runtime bridge and bounded tool surface，负责自由调查、case-run artifacts、Bangumi evidence、最终 submit/fail_closed gate。默认 runtime 是 `tools/pi_case_agent_runner.mjs` 通过 `@earendil-works/pi-coding-agent` SDK/core 创建 session；配置项 `rename_local_bangumi_pi_command` 只用于显式覆盖 runtime command。
- `workspace.py`: 可见 local refs、Bangumi refs、query cards 和合同。
- `evidence_broker.py` + `broker_*.py`: broker-driven Bangumi 取证。
- `mapping_draft.py`: 工作草稿和 accounting。
- `assignment_expander.py`: draft 到 assignment intents。
- `verifier.py`: refs、coverage、duplicate、accounting 合同检查。
- `audit.py`: snapshot 和审计摘要。

## BGM→TMDB Bridge

入口：`src/rename/process.py::_run_bgm_to_tmdb_product_pipeline`，调用 `bgm_to_tmdb/pi_runner.py::run_bgm_to_tmdb_bridge_agent`。

核心模块：

- `compiler.py` / `graph_builder.py`: 编译 BGM→TMDB 输入、构建 TMDB 合法图（legal graph）。
- `pi_runner.py` / `tools.py`: Pi runtime 桥接与受限工具面，调查 TMDB 候选并产出 verified plan。
- `rename_plan.py` / `verifier.py`: 生成并校验最终重命名计划（TMDB 合法空间、路径存在性、重复/越界）。
- `dry_run.py` / `artifacts.py`: dry-run inspector 与产物落盘。

## Runtime Artifacts

Local→Bangumi 阶段：

```text
rename_local_bangumi_case_agent_result
rename_local_bangumi_case_agent_error
```

BGM→TMDB 阶段：bridge run dir 下的 artifacts，含 verified_plan / rename_plan / verifier_result。

`accepted` 在 Local→Bangumi 阶段是 mapping artifact；只有 BGM→TMDB 桥接与迁移成功后才代表真正写盘完成。

## Sample Pool

`tests/sample_pool/raw` 保留为原始样本语料（TV 130 + Movie 16 = full146）。旧运行生成目录和旧观测 harness 不再是当前 gate。

样本回归链路：

```text
raw sample JSON
→ LocalEvidence
→ Local→Bangumi Case Agent
→ accepted 后 BGM→TMDB 桥接
→ mapping artifact / rename plan / audit snapshot
```
