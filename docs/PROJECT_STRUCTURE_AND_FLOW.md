# Project Structure And Flow

Bangumi Auto Rename 是 Python + NiceGUI 的媒体整理应用。当前 rename 主线先收束到 **Local→Bangumi Case Agent mapping-only**：只判断本地包对应的 Bangumi refs，不做 TMDB、最终命名、文件迁移或 Emby 刷新。

## Main Flow

```text
Web UI / qBittorrent webhook
→ src/web.py path normalization and queue entry
→ src/queue/task_queue.py workers
→ src/rename/process.py::Rename.process()
→ LocalEvidence
→ Local→Bangumi Case Agent
→ accepted / fail_closed / invalid
→ task JSON + decision snapshot
```

## Rename Boundary

`Rename.process()` 的职责现在很薄：

- 校验路径。
- 收集本地视频文件。
- 构建 `LocalEvidence`。
- 调用 `run_local_bangumi_case_agent_mapping()`。
- 写入 mapping-only 结果。

它不再提前语义拆包，也不再接旧映射 fallback。是否拆成 child cases、是否请求更多 Bangumi 证据、哪些文件属于同一个 Bangumi 条目，全部由 Case Agent 决定。

## Case Agent

入口：`src/rename/case_agent/local_bangumi_entry.py`

核心模块：

- `pi_runner.py` / `pi_tools.py`: Pi runtime bridge and bounded tool surface，负责自由调查、case-run artifacts、Bangumi evidence、最终 submit/fail_closed gate。
- `workspace.py`: 可见 local refs、Bangumi refs、query cards 和合同。
- `evidence_broker.py` + `broker_*.py`: broker-driven Bangumi 取证。
- `mapping_draft.py`: 工作草稿和 accounting。
- `assignment_expander.py`: draft 到 assignment intents。
- `verifier.py`: refs、coverage、duplicate、accounting 合同检查。
- `audit.py`: snapshot 和审计摘要。

## Runtime Artifacts

当前新产物使用：

```text
rename_local_bangumi_case_agent_result
rename_local_bangumi_case_agent_error
```

`accepted` 仍只是 Local→Bangumi mapping artifact，不代表已经能写盘。

## Sample Pool

`tests/sample_pool/raw` 保留为原始样本语料。旧运行生成目录和旧观测 harness 不再是当前 gate。

下一步样本回归应围绕 Case Agent mapping-only 重新建立：

```text
raw sample JSON
→ LocalEvidence
→ Case Agent mapping-only
→ mapping artifact / audit snapshot
```
