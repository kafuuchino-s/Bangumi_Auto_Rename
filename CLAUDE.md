# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## 项目概述

Bangumi Auto Rename（番剧自动重命名）是一个 Python Web 应用。核心不是"重命名器"，而是 **AI-first + strict** 的媒体整理流水线，串联 **任务队列、字幕导入、字幕自动抓取、字幕对齐、Emby 刷新、Telegram 通知**。

当前 Local→Bangumi 主线已完成：**Case Agent evidence-driven mapping + BGM→TMDB 桥接 + 可执行重命名落地**，最终生成目标目录与文件，并触发 Emby 刷新 / Telegram 通知。

核心能力：

- 基于 TMDB/Bangumi 元数据整理动漫 / 剧集 / 电影
- Case Agent：Pi 后端驱动 evidence request → MappingDraft → Verifier 合同校验
- 支持硬链接 / 复制 / 移动
- 字幕压缩包导入与 AI 映射
- 缺失字幕自动抓取
- ffsubsync 字幕对齐
- 批次完成后刷新 Emby 并发送 Telegram 汇总通知

子系统补充文档（代码与本文冲突时以本文件为准）：

- `AGENTS.md`（根目录）：全局代码地图、反模式、约定
- `src/rename/AGENTS.md`：rename 子系统当前阶段边界与 change checklist
- `docs/PROJECT_STRUCTURE_AND_FLOW.md`：架构总览
- `docs/RENAME_REGRESSION_WORKFLOW.md`：Case Agent mapping-only 回归合同

## 常用命令

```bash
# 启动应用（默认端口 5999）
python -m src.start
pdm run start

# 安装依赖
pip install -r requirements.txt

# 快速语法回归
python -m compileall src

# Case Agent 回归测试
python -m pytest tests/test_case_agent_*.py tests/test_process_local_bangumi_case_agent_path.py tests/test_config_local_bangumi_case_agent_defaults.py -q

# AI 识别测试（示例）
python tools/test_ai_recognition.py --mode auto --input tests/example_test_case.json

# Docker 构建和运行
docker build -t bangumi-auto-rename .
docker run -p 5999:5999 bangumi-auto-rename
```

补充：

- `pytest` 若报 `ValueError: I/O operation on closed file`，先怀疑终端 / capture 问题，不要直接判定业务回归失败。
- `.dockerignore` 会忽略整个 `tests/`，但显式保留 `tests/example_test_case.json` 和 `tests/example_expected.json`，配置页 AI 多格式测试依赖它们。

## 代码风格

- Black + isort（`profile = black`, `line_length = 79`, `length_sort = true`）
- basedpyright（basic）
- 注释与 UI 文案以中文为主

## 关键入口

| Task | Location | Notes |
|------|----------|-------|
| 进程启动 | `src/start.py` | `python -m src.start` 最终落到 `ui.run(...)` |
| Webhook 入站 | `src/web.py` | skip_tags、宿主机→Docker 路径转换、URL 路径修复、队列去重 |
| UI 主页 | `src/main_page.py` | 连接添加任务、字幕导入、配置页、表格刷新 |
| 队列与批次收尾 | `src/queue/task_queue.py` | worker 懒启动；成功任务后可触发字幕自动抓取；drain 后统一通知 |
| 主重命名入口 | `src/rename/process.py` | `Rename.process()` / `_process()`：Case Agent primary 路由、目录拆子任务、迁移落盘 |
| Local→Bangumi Case Agent | `src/rename/case_agent/` | Pi runner/tools、evidence broker、MappingDraft、AssignmentExpander、Verifier |
| 本地事实抽取 | `src/rename/local_evidence.py` | 本地包文件、主视频候选、补充文件事实 |
| 补充文件过滤 | `src/rename/local_supplemental_filter.py` | 非正片文件判定 |
| 关联字幕跟随 / 侧车迁移 | `src/rename/process.py` | `_collect_and_transfer_subtitle_sidecars`：同目录同 stem 字幕跟随视频迁移、语言后缀→Emby 码、`.zh-CN.default` 生成、复制写入 |
| AI 提供商 | `src/ai/client.py` | facade；OpenAI 运行时入口、缓存、schema 构建；生产链路下 Pi sidecar 接管语义推理，Python AIClient 仅作门禁/测试器 |
| Bangumi 辅助上下文 | `src/bangumi/context_builder.py` | 只给 TV prompt 提供桥接证据，不直接决定最终季集 |
| 字幕导入 | `src/subtitle/processor.py` | 薄入口：解压→Case Agent 入口→accepted 落盘 / fail_closed·need_confirm 合格结果 |
| 字幕 Case Agent | `src/subtitle/case_agent/` | AI-first + evidence-driven + Verifier 合同；`local_subtitle_entry.py` 分发 pi/single_shot；`pi_runner.py` Pi 后端 |
| 字幕自动抓取 | `src/subtitle/auto_fetch.py` | 薄入口：扫缺失字幕→Case Agent 入口选帖/选包→下载→落 processor；多季覆盖消费多 selection 逐条下载到独立子目录 + processor 配对合并 mapping；processor fail_closed 视为该包未配对成功的合格结果 |
| 抓取 Case Agent | `src/subtitle/auto_fetch_case_agent/` | AI-first + evidence-driven + 轻 submit gate（candidate ranking，无 mapping 合同）；`local_auto_fetch_entry.py` 统一走 Pi 后端（single_shot 已移除）；`pi_runner.py` Pi 后端 |
| 配置中心 | `src/config/config_manager.py` | config 默认值、线程内临时覆盖、路径转换、URL 标准化 |
| Pi sidecar 运行时 | `tools/pi_case_agent_runner.mjs` | Node.js 进程，Case Agent Pi 后端执行 AI 驱动调查 |
| BGM→TMDB 桥接 | `src/rename/bgm_to_tmdb/` | 将 Bangumi 映射结果桥接到 TMDB 目标路径并执行迁移 |

## 主流程

```text
Web UI / qBittorrent webhook
→ src/web.py（路径修复 / skip_tags / 去重）
→ src/queue/task_queue.py（并发 worker + 批次收尾）
→ src/rename/process.py
→ Case Agent primary: `local_evidence` → `case_agent/local_bangumi_entry` → `pi_runner` → `evidence_broker` → `MappingDraft` → `Verifier`
→ 接受（`accepted`）的映射经 BGM→TMDB 桥接、目标文件名生成、迁移落盘
→ 可选：字幕 sidecar 跟随 / 自动抓取
→ 批次结束后：Emby 刷新 + Telegram 汇总
```

> 旧 Python 端 AI 映射链路（ai_processor.py）已移除，全链路走 Pi Case Agent + bgm_to_tmdb 桥接。Season 0/special 合法落点由 bgm_to_tmdb legal_graph + Verifier 节点存在性校验承载；重复/越界由两个 Verifier 合同校验；关联字幕跟随由 `process.py::_collect_and_transfer_subtitle_sidecars` 承载。

## 当前实现认知

### Case Agent mapping + BGM→TMDB 落地

- 当前 Local→Bangumi→TMDB 全链路已完成：Case Agent 负责 evidence request、MappingDraft、Verifier 合同校验，判定为 `accepted` 的结果通过 BGM→TMDB 桥接生成 TMDB 目标路径并执行迁移落盘
- `Rename.process()` 不预先语义拆包；是否拆成 child cases 由 Case Agent planning phase 判断
- 固定层只做事实抽取和合同校验：visible refs、coverage、duplicate、accounting、hidden refs
- 不确定判断（候选 ownership、相似作品取舍、special/extra 语义）必须交给 Case Agent 通过 evidence request / MappingDraft / Verifier issue/audit guidance 引导
- 短 ref（F\*/G\*/C\*/season/node/evidence refs）必须和同 payload 的可读 semantic card 绑定出现
- `fail_closed` 是合格业务结果；`invalid` 只代表实现或合同错误
- 已验证 full146 为代表的多样本可端到端落地（包括字幕 sidecar 跟随、Emby 刷新、Telegram 汇总）

### Pi 后端

- Case Agent 当前 backend 为 `pi`，通过 `src/rename/case_agent/pi_runner.py` 调度
- Pi runner 启动 Node.js sidecar (`tools/pi_case_agent_runner.mjs`)，依赖 `@earendil-works/pi-coding-agent`（见 `package.json`）
- 默认执行路径是 Node sidecar 直接调用 Pi SDK/core (`createAgentSession`)；`rename_local_bangumi_pi_command` 仅作为显式 runtime command override，不是默认 CLI 路径
- Pi tools 在 `src/rename/case_agent/pi_tools.py` 中注册，提供 Bangumi evidence 查询等工具

### AI-first + strict

- 全链路走 Pi Case Agent：语义推理（标题/类型/映射/special 判定）由 Pi sidecar 完成，Python 端 AIClient 不再参与生产推理
- 若 Pi 不可用、超时、空映射或合同校验不通过，任务按失败记录（`fail_closed`/`invalid`）
- 任务记录会写入：`ai_attempted`、`ai_used`、`ai_confidence`、`failure_reason`、`pipeline_mode`

### AI-first 实现偏好

实现新需求时，优先判断这件事是否本质上属于：

- 标题提取 / 标题清洗 / query 扩词
- 候选排序 / 候选选择
- 复杂目录语义理解
- 多来源元数据桥接

若这类问题 **可以较快通过 AI + 结构化上下文 + 后置严格校验解决**，优先走 AI-first，不要先堆大量硬编码规则。

具体要求：

- 能复用现有 AI 链路时，优先复用 `src/rename/process.py`、`src/ai/client.py`（Python 端 AI 仅测试器/门禁用）、Pi Case Agent 入口
- `cleaner.py` / 纯规则逻辑只保留少量低风险、确定性的兜底规范化
- 不要为了个别样本继续无限扩张标题硬规则、目录硬规则、特判表
- 回归工具链若与主流程目标一致，尽量也复用主流程的 AI-first 标题解析与候选选择思路
- AI-first 不等于宽：最终仍必须经过 TMDB 合法空间、路径存在性、重复映射、越界映射等 strict 校验

### webhook / 路径 / 队列

`src/web.py` 负责：

- `skip_tags` 跳过标签
- `host_path_prefix` / `docker_mnt` 的 Windows 宿主机 → Docker 路径映射
- URL 编码异常路径修复（如 `+` 被解成空格）
- 队列去重：同一路径不重复入队

`src/rename/process.py` 对"非视频直系目录"会拆成子任务重新入队；`src/queue/task_queue.py` 用 `queue_max_workers` 控制并行 worker 数。

### Season 0 / special

Season 0 / special 合法落点由 **bgm_to_tmdb legal_graph** 决定，非"先映射再过滤"：

- `bgm_to_tmdb/recipe.py` 的 `special_sequence` rule → season_number=0（special 显式落 Season 0）
- `bgm_to_tmdb/graph_builder.py` 按 TMDB season payload 构造 legal_nodes，每季每集一个节点；special/season0 作为 season_number=0 节点存在
- `bgm_to_tmdb/verifier.py` 校验 mapping 的 `tmdb_legal_node_ids` 必须在 legal_graph 中存在（`unknown_tmdb_legal_node` issue）——无合法节点则 Pi 无法提交合法 mapping
- 重复/越界由 `case_agent/verifier.py`（coverage/duplicate/accounting）+ `bgm_to_tmdb/verifier.py`（duplicate_target/unknown_tmdb_legal_node）合同校验，不再有"先映射再清洗"中间态
- 按 TMDB 季度信息生成最终文件名在 `bgm_to_tmdb/rename_plan.py` + `filename_builder`

### 关联字幕跟随重命名

BGM→TMDB 落地不只处理视频。若同目录存在同 stem 的字幕（如 `.chs.ass`、`.tc.srt`），`src/rename/process.py::_collect_and_transfer_subtitle_sidecars` 会：

- 遍历 transfer_mapping，对每个视频找同目录同 stem 字幕匹配
- 解析语言后缀（`_SUBTITLE_LANGUAGE_MAP`）并转成 Emby 语言码
- 生成如 `xxx.zh-CN.default.ass` 的目标文件名（简体加 `.default`，未命中语言不加 default）
- 在主任务落地后以复制模式写入目标目录，结果写入 `subtitle_mapping`

### 电影合集

- 单电影：Case Agent 映射 + TMDB 目标文件名生成
- 多视频目录：可能进入电影合集分析
- 若合集候选被判定为"单电影 + 附加内容"，会回退到单电影处理并忽略附加内容

### 字幕导入 / 自动抓取 / 通知

- `src/subtitle/processor.py`：字幕导入薄入口——解压→事实卡片→Case Agent 入口→accepted 落盘 / fail_closed·need_confirm 合格结果；语言归一化、可选 ffsubsync、写入目标目录
- `src/subtitle/case_agent/`：字幕 Case Agent 子系统（对齐 rename）。`local_subtitle_entry.py` 按 `subtitle_case_agent_backend` 分发 `pi`（多轮 evidence-driven）/ `single_shot`（单轮 AI + 合同）；`pi_runner.py`+`pi_tools.py` 跑 Pi sidecar；`verifier.py` 合同校验 coverage/duplicate/accounting/合法目标视频
- `tools/pi_subtitle_case_agent_runner.mjs` + `.pi/skills/subtitle-mapping-contract/`：Pi sidecar（4 工具）+ 合同 skill
- `src/subtitle/auto_fetch.py`：自动抓取薄入口——扫缺失字幕→Case Agent 入口选帖/选包→下载→落 `SubtitleProcessor.process`；单次线性流程（无外层换词重试，Pi 内部可试多个 BGM 名变体）；processor 落盘产 `fail_closed` 视为"该包未配对成功"的合格结果，透传 `processor_case_agent_status` 审计
- `src/subtitle/auto_fetch_case_agent/`：抓取 Case Agent 子系统（对齐 rename / 字幕导入，但 candidate ranking 无 mapping 合同）。`local_auto_fetch_entry.py` 统一走 Pi evidence-driven 后端（single_shot 已移除，`backend` 参数保留兼容但忽略）；Pi sidecar 主动 `search_candidates`/`load_candidate_packages`/`inspect_package` 取证；`pi_runner.py`+`pi_tools.py` 跑 Pi sidecar（6 工具）；`verifier.py` 轻 submit gate（候选可下载 / 楼包非 font-patch-only）。**渐进式分批**（参考 rename atlas-first）：`search_candidates` 单次最多搜 `_SEARCH_KEYWORD_BATCH_LIMIT=4` 词、`load_candidate_packages` 单次最多加载 `_LOAD_CANDIDATE_BATCH_LIMIT=3` 帖，超出部分 `remaining_keywords`/`remaining_candidate_refs` 延后，Pi 按需再调；readable card `_compact_text` 压缩 post_text/links 防 context 撑爆；sidecar nudge 含 final_repair while 循环拉回卡死 Pi。**固定层并发加速 A+B+C**（架构改进，治多季超时根因——acgrip search/load 是 40-80s/次的网络 I/O，串行多 subject 累计超 600s timeout，实测并发 4.9x）：`tool_search_candidates` 批内多词 `ThreadPoolExecutor` 并发搜（`_SEARCH_CONCURRENCY=4`）；`tool_load_candidate_packages` 批内多 ref 并发 load（`_LOAD_CONCURRENCY=4`）+ **B 缓存** `state._loaded_candidate_refs` 记已 load 的 ref，合帖多 subject（如 0042 ARIA tid=3582 一帖覆盖 3 subject）复用时跳过 provider HTTP 直接报已有包 ref；workspace 写入回主线程串行避免并发写共享态；分批 limit 不变（只改批内执行方式串行→并行）。SKILL 教 Pi 多 subject 时一次 search 传全部 subject 词 / 一次 load 多帖利用并发。**search/load 分离 + null 语义**（关键机制，修复 Pi 跳过 load 误判无包）：acgrip search 只返帖子标题、不返附件，`CandidateCard.packages_loaded` 标记是否已 load；未 load 时 readable card 的 `package_count`/`has_downloadable_attachment` 渲染为 `null`（未探测）而非 `0`/`false`，Pi 必须先 `load_candidate_packages` 才能判定可下载，禁止跳过 load 直接 `fail_closed no_downloadable`；submit gate 对未 load 候选给 "load first" hint。`preferred_language`（默认 zh-CN）经 evidence_broker → MissingVideoCard → Pi，用于简繁抉择（非 gate）。回归 smoke `tools/run_auto_fetch_mapping_smoke.py` 支持 `--workers`（默认 10，ThreadPoolExecutor 并发，每样本独立 task uuid + Pi sidecar）。**多季覆盖**（进行中，阶段 1+2 已落地）：auto_fetch 单帖单包模型只配一季就 accepted，多季番（0091 鬼灭 S01+S02+S03+剧场版=4 BGM subject）其余季缺字幕被掩盖。阶段 1 `process.py::_collect_bgm_subject_names` 改保留每 subject name/name_cn + 建 `bgm_video_subject_map`(video_basename→subject_id) 写 task_data（不只主体单值）；阶段 2 `MissingVideoCard` 加 `bangumi_subject_id`/`subject_name`(日文)/`subject_name_cn`(中文)，evidence_broker 按 video 查映射填 per-video subject，readable 暴露 subject 分组给 Pi。搜索词策略（acgrip 实搜验证）：日文原名命中干净但可能漏（無限列車編 只命中剧场版漏 TV 篇），中文名命中全含噪音，Pi 多变体搜 + 从混结果按 subject 分辨；不堆 TMDB 英文季名（0 命中）。**多季覆盖阶段 3 已落地**（用户拍板"不强制全处置"）：Pi 合同改多帖多包——`submit_package` 不落 final 而是 append 到 `state.selections`（返回 selections_count/covered_subject_ids/next_action），新增 `submit_complete` 作终止点（落 final_result.selections，要求 ≥1 selection，**不要求每 subject 都处置**：无帖 subject 留 uncovered 合格）；`pi_runner` auto-submit_complete 兜底（Pi 选了包没调 submit_complete 就结束时自动补）；Verifier submit_complete gate 只校验 selections 非空（无 coverage 合同）；新增 `/state` HTTP 端点暴露 selections_count/covered/uncovered subject_ids，sidecar nudge 据此检测"Pi 选一包就停"并提醒继续其他 subject；`auto_fetch._execute_fetch` 消费多 selection 逐条下载到独立 `sel_<idx>` 子目录 + processor 配对，合并 mappings/unmatched/no_target_videos，accepted=≥1 success/部分失败仍 accepted/全失败 failed；`local_auto_fetch_entry` accepted 透传 selections + selections_provider。0091 端到端验证：Pi 一次选 3 帖 3 包覆盖 S01+S02(無限列車編)+S03(遊郭編)=**matched 70**（单帖单包旧模型只配一季）。**剧场版纠正**（2026-06-21 实证）：剧场版 291494 acgrip 有帖（tid=7842，5 可下载包）+ TMDB 有落点 movie:635302，非"无帖合格"；漏覆盖真因是 smoke `synthesize_targets` tv 分支对 movie legal node 跳过（`_parse_tv_legal_node` 返回 None），剧场版没进 missing_videos Pi 看不到，已修 tv 分支遇 movie 节点按 movie 规则合成 target
- `tools/pi_auto_fetch_case_agent_runner.mjs` + `.pi/skills/auto-fetch-contract/`：抓取 Pi sidecar（8 代理工具）+ 合同 skill
- `src/queue/task_queue.py::_start_workers`：批次结束后触发 Emby 刷新与 Telegram 汇总通知

字幕导入四态语义（对齐 rename fail_closed）：`accepted` 落盘 / `fail_closed`（合同不通过，合格，不落盘部分匹配，对外映射 `need_confirm` 带 `case_agent_status` 审计）/ `need_confirm`（AI 空映射或无目标视频）/ `invalid`（实现错误）。`accepted + unmatched` → 落盘已匹配部分 + unmatched 写任务 JSON 待人工。固定层只做事实 + 合同，不确定判断交 AI；已移除 suffix 模糊匹配 / 集数规则 / AI 数量重试兜底。

自动抓取四态语义（对齐 rename / 字幕导入，但 candidate ranking 无 coverage/accounting 合同）：`accepted`（选中帖+包，可下载落 processor）/ `fail_closed`（候选/包被拒或搜不到，合格，`reason_kind='pi_fail_closed'`，单次结束不重试）/ `need_confirm`（Pi 不确定选哪个）/ `invalid`（实现错误）。processor 落盘产 `fail_closed` → auto_fetch 视为"该包未配对成功"的合格结果，透传 `processor_case_agent_status` / `failure_reason` 审计。**多季覆盖**（阶段 3）：Pi `submit_complete` 落 final 含 `selections` list（每 subject 一帖一包），auto_fetch `_execute_fetch` 逐 selection 下载到独立 `sel_<idx>` 子目录 + processor 配对，合并 mappings/unmatched/no_target_videos，accepted=≥1 selection 下载+processor success（部分失败仍 accepted，全失败 failed）；旧单 submit_package 路径回退单 selection 兼容。`MissingVideoCard.source_video`（record key = pre-rename local 源名）与字幕导入 `source_video` 同口径；`MissingVideoCard.bangumi_subject_id`/`subject_name`/`subject_name_cn`（阶段 2）让 Pi 按 subject 分组多帖多包搜，一帖可覆盖多 subject（合帖）。单次线性流程：无外层换词重试、无 AI 扩词、无 single_shot/`_pick_best_package_by_rules` 规则兜底（统一走 Pi evidence-driven，Pi 内部可试多个 BGM 名变体）。

字幕配对 unmatched 分类（processor 层，字幕导入+auto_fetch 共用）：AI 在 mapping draft 的 unmatched 行给结构化 `unmatched_reason_kind` 枚举（`no_target_video`/`duplicate_language`/`no_confident_match`/`unknown`），verifier 透传到 `CompiledSubtitlePlan.unmatched`（结构化 `CompiledUnmatchedEntry`，保留 `unmatched_refs` property 兼容）。processor `_build_unmatched_details` 按 reason_kind 分两类输出：`no_target_video` → `no_target_videos` 字段（A 类：字幕对应源视频无 TMDB 落点，如 PV/TV-Spot/Picture Drama/OAD/special，确定的"无目标"不待人工，已过滤出 unmatched）；其余 → `unmatched`（C 类重复去重 + B 类真不确定，待人工）。result 含 `unmatched` + `no_target_videos` 两字段。配对基准 = TMDB 落地视频（auto_fetch 扫落地目录 = missing_videos，非 local/BGM/字幕数量）；字幕 Case Agent 用 `video`（合法落点）+ `source_video`（强配对证据）对齐。**展示口径 = video 维度**（smoke `run_auto_fetch_mapping_smoke.py` 主列）：`covered/total` 已配字幕落地视频数 / 总落地视频数 + 缺字幕视频数 + zh-CN 视频数；`unmatched`/`no_target_videos` 是字幕维度审计列（"没用的字幕"统计：duplicate 重复去重 / no_target 特典无落点），非配对质量指标——判断配对质量看 video 覆盖率与缺字幕数，不看 unmatched 计数。

## 关键配置项

默认值在 `src/config/config_manager.py`，UI 在 `src/pages/config_page.py`。

### 基础

- 路径：`bangumi_path` / `movie_path` / `anime_path` / `anime_movie_path`
- 传输：`mode` / `overwrite_existing`
- Docker 路径转换：`docker_mnt` / `host_path_prefix`
- 日志：`log_level`
- 队列：`queue_max_workers`
- 跳过标签：`skip_tags`

### AI

- 凭据：`ai_api_key`
- Base URL / 模型 / 温度：`ai_base_url` / `ai_model` / `ai_temperature`
- 严格模式：`ai_force_strict`
- 阈值：`ai_confidence_threshold`
- OpenAI 输出路由：`openai_output_format` / `openai_api_interface` / `openai_auto_routing_enabled` / `openai_auto_format_order`
- AI 结果保存：`ai_auto_save`

### Case Agent

- 启用：`rename_local_bangumi_case_agent_primary_enabled`
- 后端：`rename_local_bangumi_case_agent_backend`（当前为 `pi`）
- 证据批次：各种 `max_evidence_batches` 配置
- Pi 运行时：`pi_max_turns` / `pi_timeout_seconds` 等

### 字幕

- 对齐：`subtitle_sync_enabled` / `subtitle_sync_mode` / `subtitle_sync_executable` / `subtitle_sync_extra_args` / `subtitle_sync_timeout_seconds` / `subtitle_sync_overwrite_policy`
- 字幕 Case Agent：`subtitle_case_agent_primary_enabled` / `subtitle_case_agent_backend`（`pi` 默认 / `single_shot`）/ `subtitle_case_agent_pi_case_root` / `subtitle_case_agent_pi_max_turns` / `subtitle_case_agent_pi_timeout_seconds` / `subtitle_case_agent_pi_command`
- 自动抓取：`subtitle_auto_fetch_enabled` / `subtitle_auto_fetch_provider` / `subtitle_auto_fetch_candidate_limit` / `subtitle_auto_fetch_timeout_seconds` / `subtitle_auto_fetch_browser_enabled` / `subtitle_auto_fetch_acgrip_base_url` / `subtitle_auto_fetch_preferred_language` / `subtitle_auto_fetch_use_ai_rerank` / `subtitle_auto_fetch_search_mode` / `subtitle_auto_fetch_save_reason`
- 抓取 Case Agent：`subtitle_auto_fetch_case_agent_backend`（`pi` 默认；`single_shot` 已弃用，值忽略始终 pi）/ `subtitle_auto_fetch_case_agent_pi_case_root` / `subtitle_auto_fetch_case_agent_pi_max_turns` / `subtitle_auto_fetch_case_agent_pi_timeout_seconds` / `subtitle_auto_fetch_case_agent_pi_command`

### 通知

- Emby：`emby_enabled` / `emby_host` / `emby_api_key`
- Telegram：`telegram_enabled` / `telegram_bot_token` / `telegram_chat_id` / `telegram_notify_on_success` / `telegram_notify_on_failure` / `telegram_base_url`

## 数据与日志

运行时数据在 `data/`：

- `config.json`
- `log/`（主日志 `BAR.log`）
- `task/`
- `record/`
- `ai_analysis/`
- `pi_case_agent/`
- `cache/`
- `subtitle_upload/`
- `regression/`
- `ai_batch_regression/`

`src/logger.py` 当前行为：

- 文件日志：JSON 结构化、按天轮转、保留 7 天
- 控制台日志：彩色开发输出
- `log_level` 可在运行时更新

## 测试与回归

优先快速回归：

```bash
python -m compileall src
```

Case Agent 回归：

```bash
python -m pytest tests/test_case_agent_*.py tests/test_process_local_bangumi_case_agent_path.py tests/test_config_local_bangumi_case_agent_defaults.py -q
```

样本池回归：

```bash
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py
```

pytest 标记：`slow` / `sample_pool` / `e2e`

AI 测试入口：

- 文档：`tests/README_AI_Testing.md`
- 脚本：`tools/test_ai_recognition.py`
- 模式：`manual` / `auto` / `save`

## 反模式

- 不要把当前链路理解成"AI 失败就自动回退旧规则"。默认 `ai_force_strict=true`，失败应按失败任务记录。
- 不要继续扩张大量标题/目录硬编码来替代现有 AI 链路，除非只是低风险规范化。
- 不要让 Bangumi 直接决定 season number；它只是辅助证据，不是权威映射源。
- 不要假设 `data/` 是临时垃圾目录；这里保存 config、task、record、AI 分析、字幕抓取产物。
- 不要把队列收尾理解成"每个任务立刻通知"；当前设计是批次结束后统一 Emby/Telegram 汇总。
- 不要忽略 `src/web.py` 的路径修复与 skip_tags 逻辑；外部路径未必能直接用。
- 不要为 sample-pool 个例写样本专属 alias、硬编码 title 或固定 file→legal_node 正映射。
- 不要让固定层把局部、脆弱、语义性的 overlap/bridge 判断升级成 hard blocker。
- 若文档与代码冲突，以当前代码和本文件为准。

## 注意事项

- 当前环境以 Windows 为主，代码里包含 Windows 路径与 `pywin32` 假设
- qBittorrent webhook 的路径未必可直接使用，先看 `src/web.py` 的路径修复与 Docker 映射
- 默认传输模式是硬链接；字幕导入和关联字幕通常走复制
- 若要看"为什么任务完成后又触发字幕 / 通知"，先看 `src/queue/task_queue.py`
- 若要看"为什么字幕被改成 Emby 风格语言后缀"，先看 `src/subtitle/processor.py`（字幕导入）和 `src/rename/process.py::_collect_and_transfer_subtitle_sidecars`（关联字幕跟随）
- `requirements_docker.txt` 与主 `requirements.txt` 分离；Docker 镜像的依赖集合和本地开发不完全等价
- 改 Case Agent 入口路由：看 `process.py` 和 `case_agent/local_bangumi_entry.py`
- 改判定语义：看 `case_agent/pi_runner.py`、`pi_tools.py`、`verifier.py`、`mapping_draft.py`
- 改取证能力：看 `case_agent/evidence_broker.py` 和 `case_agent/broker_*.py`
- 将样本经验写回主流程时，先抽象成条件树，不能自动写语义映射到固定层
