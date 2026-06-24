# RENAME SUBSYSTEM

## Overview

`src/rename/` 的 Local→Bangumi→TMDB 主线已端到端落地。`Rename.process()` 负责收集本地视频事实、构建 `LocalEvidence`、调度 Case Agent 做映射，accepted 的结果再经 BGM→TMDB 桥接生成 TMDB 合法目标路径、生成最终文件名、执行迁移落盘，并随带字幕 sidecar 跟随。相关执行开关默认开启（见根 `CLAUDE.md` 关键配置项）。

## Where To Look

| Task | Location | Notes |
| --- | --- | --- |
| 主入口 | `process.py` | `Rename.process()` / `_process()`：Case Agent primary 路由、目录拆子任务、BGM→TMDB 产品链路、迁移落盘 |
| Local→Bangumi Case Agent | `case_agent/` | Pi runner/tools、evidence broker、MappingDraft、AssignmentExpander、Verifier、audit |
| BGM→TMDB 桥接 | `bgm_to_tmdb/` | compiler / graph_builder / pi_runner / tools / rename_plan / verifier / dry_run / artifacts；把 Bangumi 映射桥接到 TMDB 目标路径并执行迁移 |
| 本地事实抽取 | `local_evidence.py` | 本地包文件、主视频候选、补充文件事实 |
| 补充文件过滤 | `local_supplemental_filter.py` | 非正片文件判定 |
| 标题清洗 | `cleaner.py` | 低风险 deterministic 预处理 |
| 迁移/记录 | `trans.py` | 硬链接 / 复制 / 移动落盘适配层 |
| Bangumi API | `src/bangumi/` | broker-driven evidence source |

## Local Rules

- 不再把旧拆包和旧映射链路接进 Local→Bangumi→TMDB 主路径；旧链路仅在 Case Agent 关闭时可用。
- `Rename.process()` 不预先语义拆包；是否拆成 child cases 由 Case Agent planning phase 判断。
- 固定层只做事实抽取和合同校验：visible refs、coverage、duplicate、accounting、hidden refs。
- Case Agent 请求 Bangumi 证据时必须通过 broker；短 ref 必须和同 payload 的 readable card 绑定出现。
- TMDB 才是最终合法输出空间；Bangumi 只是辅助证据，不直接决定 season number。
- accepted 的 Local→Bangumi 映射经 BGM→TMDB 桥接生成 TMDB 合法落点、最终文件名，并执行迁移落盘。
- `fail_closed` 是合格业务结果；`invalid` 只代表实现或合同错误。
- 默认 `ai_force_strict=true`：失败按失败任务记录，不自动回退旧规则。

## Change Checklist

- 改入口路由：看 `process.py` 和 `case_agent/local_bangumi_entry.py`。
- 改判定语义：看 `case_agent/pi_runner.py`、`case_agent/pi_tools.py`、`case_agent/verifier.py`、`case_agent/mapping_draft.py`。
- 改取证能力：看 `case_agent/evidence_broker.py` 和 `case_agent/broker_*.py`。
- 改 BGM→TMDB 桥接：看 `bgm_to_tmdb/pi_runner.py`、`bgm_to_tmdb/compiler.py`、`bgm_to_tmdb/graph_builder.py`、`bgm_to_tmdb/rename_plan.py`、`bgm_to_tmdb/verifier.py`。
- 改样本回归：保留 `tests/sample_pool/raw`，不要恢复旧 `generated / manifest / anchors` harness。
