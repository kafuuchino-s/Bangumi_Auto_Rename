# RENAME SUBSYSTEM

## Overview

`src/rename/` 现在的 Local→Bangumi 主线是 Case Agent mapping-only。`Rename.process()` 只负责收集本地视频事实、构建 `LocalEvidence`、调用 Case Agent，并把 `accepted / fail_closed / invalid` 作为可审计结果落盘。

## Where To Look

| Task | Location | Notes |
| --- | --- | --- |
| 主入口 | `process.py` | `Rename.process()` / `_process()` |
| Local→Bangumi Case Agent | `case_agent/` | planning、evidence broker、MappingDraft、AssignmentExpander、Verifier、audit |
| 本地事实 | `local_evidence.py` | 本地包文件、主视频候选、补充文件事实 |
| Bangumi API | `src/bangumi/` | broker-driven evidence source |
| 标题清洗 | `cleaner.py` | 低风险 deterministic 预处理 |
| 迁移/记录 | `trans.py` | 下一阶段执行适配层会重新接入 |

## Local Rules

- 不再把旧拆包和旧映射链路接进 Local→Bangumi 主路径。
- `Rename.process()` 不预先语义拆包；是否拆成 child cases 由 Case Agent planning phase 判断。
- 固定层只做事实抽取和合同校验：visible refs、coverage、duplicate、accounting、hidden refs。
- Case Agent 请求 Bangumi 证据时必须通过 broker；短 ref 必须和同 payload 的 readable card 绑定出现。
- 当前阶段不做 TMDB executable target、不生成最终文件名、不触发 Emby。
- `fail_closed` 是合格业务结果；`invalid` 只代表实现或合同错误。

## Change Checklist

- 改入口路由：看 `process.py` 和 `case_agent/local_bangumi_entry.py`。
- 改判定语义：看 `case_agent/orchestrator.py`、`case_agent/verifier.py`、`case_agent/mapping_draft.py`。
- 改取证能力：看 `case_agent/evidence_broker.py` 和 `case_agent/broker_*.py`。
- 改样本回归：保留 `tests/sample_pool/raw`，不要恢复旧 `generated / manifest / anchors` harness。
