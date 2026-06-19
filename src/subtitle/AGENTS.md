# SUBTITLE SUBSYSTEM

## OVERVIEW
`src/subtitle/` 负责两条字幕链路：手动导入压缩包/字幕文件，以及主任务成功后的自动抓取与可选 ffsubsync 调轴。

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| 字幕导入主入口 | `processor.py` | 薄入口：解压→事实卡片→Case Agent 入口→accepted 落盘 / fail_closed·need_confirm 合格结果 |
| 自动抓取主入口 | `auto_fetch.py` | 扫缺失字幕、搜索候选、AI 重排、下载后回调导入 |
| 批量调轴 | `batch_sync.py` | 树级别 ffsubsync 处理 |
| 单字幕调轴封装 | `syncer.py` | ffsubsync runner、成功/失败状态 |
| 压缩包解压 | `extractor.py` | 支持字幕文件与压缩包输入 |
| provider 适配 | `providers/` | 站点搜索、候选页、附件包加载 |
| 规则/排序补充 | `ranker.py` | 抓取候选辅助逻辑 |
| 字幕 Case Agent 子系统 | `case_agent/` | AI-first + evidence-driven + Verifier 合同校验，对齐 rename 链路 |
| Case Agent 入口 | `case_agent/local_subtitle_entry.py` | `run_subtitle_case_agent_mapping`：按 `subtitle_case_agent_backend` 分发 pi / single_shot，返回四态 |
| Case Agent Pi 后端 | `case_agent/pi_runner.py` + `case_agent/pi_tools.py` | 多轮 evidence-driven：本地 HTTP tool server + node sidecar |
| Pi sidecar | `tools/pi_subtitle_case_agent_runner.mjs` | 4 工具（get_context/validate/submit/fail_closed），复用 Pi SDK |
| Pi skill | `.pi/skills/subtitle-mapping-contract/SKILL.md` | 合同：短 ref 体系、disposition 语义、coverage 规则 |

## CASE AGENT 子系统（对齐 rename）

字幕导入已升级为 **AI-first + evidence-driven + Verifier 合同校验**，与 rename 的 Local→Bangumi Case Agent / BGM→TMDB 桥接同构：

- **固定层只做事实 + 合同**：解压字幕事实（`SubtitleFileCard` SF\*）+ 已落盘目标视频事实（`SubtitleTargetVideoCard` TV\*）→ `SubtitleVerifier` 校验 coverage/duplicate/accounting/合法目标视频。不确定判断（候选归属、版本/语言歧义、跨季归属）交 AI 通过 draft 表达。
- **后端分发**：`subtitle_case_agent_backend` = `pi`（默认，多轮 evidence-driven）/ `single_shot`（Phase 2 单轮 `analyze_subtitle_mapping` + 合同）。
- **四态语义**：`accepted` 落盘 / `fail_closed`（合同不通过，合格，不落盘部分匹配）/ `need_confirm`（AI 空映射或无目标视频）/ `invalid`（实现/合同错误）。
- **产品语义**：`accepted + unmatched` → 落盘已匹配部分 + unmatched 写任务 JSON `result["unmatched"]` 待人工，整体 `status=success`。`fail_closed` 对外映射为 `need_confirm`（保留 `case_agent_status: fail_closed` 审计），供 UI/auto_fetch 触发人工/重试。
- **已移除**：`_find_subtitle_file` suffix 模糊匹配、视频集数 `split(" - ")` 规则匹配、`analyze_subtitle_mapping` 数量重试兜底——交 Verifier 合同 + fail_closed 处理。
- **保留**：`extractor.py` 解压、`LANGUAGE_MAP` 语言归一（低风险确定性）、ffsubsync 链路、task/record 读写契约。

## LOCAL CONVENTIONS
- 字幕链路大量依赖 `data/task` 和 `data/record`；它不是纯文件扫描工具，而是“基于最近已处理任务的二次处理系统”。
- `SubtitleProcessor.process()` 既支持压缩包导入，也会被自动抓取流程复用；改这里要同时考虑手动导入与自动抓取。当前为薄入口，映射语义改在 `case_agent/`。
- 语言后缀会归一到 Emby 风格，如 `zh-CN.default`；简体中文默认带 `.default`（归一在 `processor.LANGUAGE_MAP`，经 `language_resolver` 注入 Case Agent）。
- ffsubsync 是可选能力，策略由配置控制：`best_effort` 与 `strict` 语义不同。
- 自动抓取是“候选搜索 → AI/规则筛选 → 下载 → 再走导入处理”，不是直接把网络资源塞到目标目录。
- `subtitle_path` / `(task_uuid, video)` 必须精确匹配固定层 SF\*/TV\* ref；AI 返回缺前缀路径会解析失败 → fail_closed，不自动规则回退。

## ANTI-PATTERNS
- 不要绕过任务/记录数据结构自己猜目标视频；现有流程已经用持久化记录做范围收敛。
- 不要把自动抓取写成站点特化硬编码；provider 与候选/包选择是独立层。
- 不要把字幕语言后缀原样透传到最终文件名；最终命名必须符合 Emby 习惯。
- 不要忽略 `need_confirm` / `fail_closed` 分支；映射不确定时是合格结果，保留人工确认出口，不强行落盘部分匹配。
- 不要在固定层做局部、脆弱、语义性的 overlap/bridge 判断；候选归属交 AI，固定层只做事实 + 合同。
- 不要重新引入 suffix 模糊匹配 / 集数规则匹配 / AI 数量重试兜底；这些已交给 Verifier 合同 + fail_closed。

## CHANGE CHECKLIST
- 改导入落盘逻辑：看 `processor.py`（薄入口）、`case_agent/`、相关字幕测试。
- 改映射语义/合同：看 `case_agent/verifier.py`、`case_agent/mapping_draft.py`、`case_agent/pi_tools.py`、`tests/test_subtitle_case_agent_*.py`。
- 改 Pi 后端：看 `case_agent/pi_runner.py`、`tools/pi_subtitle_case_agent_runner.mjs`、`.pi/skills/subtitle-mapping-contract/`。
- 改自动抓取：看 `auto_fetch.py`、`providers/`、`tests/test_subtitle_auto_fetch.py`。
- 改调轴：看 `syncer.py`、`batch_sync.py`、`tests/test_subtitle_processor_sync.py`。
- 回归优先：`python -m compileall src` + `tests/test_subtitle_*.py`（89 例）。
