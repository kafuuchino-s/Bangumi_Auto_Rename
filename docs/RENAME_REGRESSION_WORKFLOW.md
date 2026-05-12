# Rename Regression Workflow

当前 rename 回归只服务 Local→Bangumi Case Agent mapping-only 主线。

## 目标

本阶段验证的是：本地包能否被 Case Agent 稳定判定为 Bangumi subject / episode / span refs，并产出可审计的 `accepted`、`fail_closed` 或 `invalid`。

当前不验证：

- TMDB executable target
- 最终文件名
- 文件迁移
- Emby 刷新

## 样本

`tests/sample_pool/raw` 是保留的原始样本语料。旧运行生成目录不再作为 gate：

- `tests/sample_pool/generated`
- `tests/sample_pool/manifest`
- `tests/sample_pool/anchors`

新的样本 runner 后续应直接读取 `raw/`，调用 Case Agent mapping-only 入口，并输出新的 mapping artifact。

## 快速验证

```powershell
.venv\Scripts\python.exe -m pytest tests/test_case_agent_*.py tests/test_process_local_bangumi_case_agent_path.py tests/test_config_local_bangumi_case_agent_defaults.py -q
.venv\Scripts\python.exe -m compileall src\rename\case_agent src\rename\process.py src\config\config_manager.py
```

## Accepted 合同

`accepted` 必须满足：

- 所有 main files exactly-once accounted
- 每个 main file 只能是 `map_to_bangumi` 或明确可接受的 `non_bangumi_or_supplemental`
- 没有 open row
- 没有 unresolved / needs_more_evidence / unaligned
- 没有 hidden ref、重复 target ref、非法 target ref

否则应为 `fail_closed` 或 `invalid`。

## 状态语义

- `accepted`: 映射判断完成，仍然只代表 Local→Bangumi refs。
- `fail_closed`: 证据不足或番剧判断无法安全完成，是合格业务结果。
- `invalid`: 实现或合同错误，需要修代码。
