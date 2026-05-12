# Local→Bangumi Case Agent Plan

## Current Scope

先完成本地包到 Bangumi refs 的 mapping-only 判断。

本阶段不做：

- TMDB 映射
- 最终文件名
- 文件迁移
- Emby 刷新

## Architecture

```text
Rename.process()
→ collect local video files
→ build LocalEvidence
→ run Local→Bangumi Case Agent
→ write rename_local_bangumi_case_agent_result
```

Case Agent 自己负责：

- planning phase
- 是否拆 child cases
- Bangumi evidence request
- MappingDraft
- AssignmentIntent
- accepted / fail_closed / invalid

固定层只负责：

- 本地事实抽取
- ref catalog
- readable cards
- query hints
- coverage / duplicate / hidden-ref / accounting 校验

## Result Contract

`accepted` 要求：

- 所有 main files exactly-once accounted
- 每个 main file 已映射到 Bangumi refs，或明确标为可接受的 supplemental / non-Bangumi
- 没有 open、unresolved、needs_more_evidence、unaligned rows
- 没有重复 refs、隐藏 refs、非法 refs

`fail_closed` 表示番剧判断失败或证据不足，但流程行为正确。

`invalid` 表示实现或合同错误。

## Test Gate

```powershell
.venv\Scripts\python.exe -m pytest tests/test_case_agent_*.py tests/test_process_local_bangumi_case_agent_path.py tests/test_config_local_bangumi_case_agent_defaults.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent src\rename\process.py src\config\config_manager.py
```
