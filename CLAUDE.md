# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## 项目概述

Bangumi Auto Rename（番剧自动重命名）是一个 Python Web 应用。当前它不只是“重命名器”，而是一个以 **AI-first 主流程** 为核心、串联 **任务队列、字幕导入、字幕自动抓取、字幕对齐、Emby 刷新、Telegram 通知** 的媒体整理工具。

核心能力：

- 基于 TMDB 元数据整理动漫 / 剧集 / 电影
- AI 标题提取、媒体类型判断、TMDB 候选选择、剧集映射
- 支持硬链接 / 复制 / 移动
- 字幕压缩包导入与 AI 映射
- 缺失字幕自动抓取
- ffsubsync 字幕对齐
- 批次完成后刷新 Emby 并发送 Telegram 汇总通知

## 常用命令

```bash
# 启动应用（默认端口 5999）
python -m src.start
pdm run start

# 安装依赖
pip install -r requirements.txt

# Docker 构建和运行
docker build -t bangumi-auto-rename .
docker run -p 5999:5999 bangumi-auto-rename

# 快速语法回归
python -m compileall src

# AI 识别测试（示例）
python tools/test_ai_recognition.py --mode auto --input tests/example_test_case.json
```

补充：

- `.dockerignore` 会忽略整个 `tests/`，但显式保留 `tests/example_test_case.json` 和 `tests/example_expected.json`，配置页 AI 多格式测试依赖它们。
- 当前环境里 `pytest` 若报 `ValueError: I/O operation on closed file`，先怀疑终端 / capture 问题，不要直接判定业务回归失败。

## 代码风格

- Black + isort（`profile = black`, `line_length = 79`）
- basedpyright（basic）
- VSCode 保存时自动格式化
- 注释与 UI 文案以中文为主

## 关键入口

- `src/start.py`：应用入口
- `src/web.py`：FastAPI + `/sendTask` webhook
- `src/main_page.py`：主页面布局
- `src/config/config_manager.py`：配置默认值与配置中心
- `src/queue/task_queue.py`：队列调度与批次收尾
- `src/rename/process.py`：主重命名引擎
- `src/rename/ai_processor.py`：TV AI 映射、后置校验、最终路径生成
- `src/subtitle/processor.py`：字幕导入主入口
- `src/subtitle/auto_fetch.py`：字幕自动抓取入口

## 项目结构与完整流程

详见：`docs/PROJECT_STRUCTURE_AND_FLOW.md`

主流程速记：

```text
Web UI / qBittorrent webhook
→ src/web.py（路径修复 / skip_tags / 去重）
→ src/queue/task_queue.py（并发 worker + 批次收尾）
→ src/rename/process.py
→ AI 提取标题 / 类型 + TMDB 候选选择
→ Movie / TV 分支
→ TV：VideoAnalyzer + Bangumi context + AI 映射 + strict 校验
→ Trans.trans_file()
→ 写入 task / record / ai_analysis
→ 可选：字幕自动抓取
→ 批次结束后：Emby 刷新 + Telegram 汇总
```

补充：
- `src/web.py` 负责 `skip_tags`、宿主机 → Docker 路径映射、URL 编码路径修复、队列去重
- `src/rename/process.py` 对“非视频直系目录”会拆成子任务重新入队
- `src/queue/task_queue.py` 用 `queue_max_workers` 控制并发 worker 数

## 当前实现认知

### AI-first + strict

当前主链路是 **AI-first + strict**：

- `src/rename/process.py` 会先清洗标题，再让 AI 提取标题与媒体类型
- TV / Movie 都先走 TMDB 搜索，再由确定性规则或 AI 从候选中选择
- 若 AI 不可用、超时、低置信度、空映射或非法映射，任务会按失败记录
- 任务记录会写入：`ai_attempted`、`ai_used`、`ai_confidence`、`failure_reason`、`pipeline_mode`

### AI-first 实现偏好

实现新需求时，优先判断这件事是否本质上属于：
- 标题提取 / 标题清洗 / query 扩词
- 候选排序 / 候选选择
- 复杂目录语义理解
- 多来源元数据桥接

若这类问题 **可以较快通过 AI + 结构化上下文 + 后置严格校验解决**，优先走 AI-first，不要先堆大量硬编码规则。

具体要求：
- 能复用现有 AI 链路时，优先复用 `src/rename/process.py`、`src/ai/client.py`、`src/rename/ai_processor.py`
- `cleaner.py` / 纯规则逻辑只保留少量低风险、确定性的兜底规范化
- 不要为了个别样本继续无限扩张标题硬规则、目录硬规则、特判表
- 回归工具链若与主流程目标一致，尽量也复用主流程的 AI-first 标题解析与候选选择思路，而不是长期维持 cleaner-first 分叉
- AI-first 不等于放宽：最终仍必须经过 TMDB 合法空间、路径存在性、重复映射、越界映射等 strict 校验

### webhook / 路径 / 队列

`src/web.py` 负责：

- `skip_tags` 跳过标签
- `host_path_prefix` / `docker_mnt` 的 Windows 宿主机 → Docker 路径映射
- URL 编码异常路径修复（如 `+` 被解成空格）
- 队列去重：同一路径不重复入队

`src/rename/process.py` 对“非视频直系目录”会拆成子任务重新入队；`src/queue/task_queue.py` 用 `queue_max_workers` 控制并行 worker 数。

### Season 0 / special

不要再依赖旧版文档里的 `_collect_season0_files()`、`SPECIAL_FOLDER_NAMES` 等历史实现名。

当前 Season 0 / special 逻辑以 **AI 映射结果 + TMDB 季集信息** 为准，主要在 `src/rename/ai_processor.py` 中完成：

- 重复映射清洗
- 越界映射清洗
- Season 0 / special 语义冲突过滤
- 按 TMDB 季度信息生成最终文件名

### 关联字幕跟随重命名

AI TV 映射不只处理视频。若同目录存在同 stem 的字幕（如 `.chs.ass`、`.tc.srt`），`src/rename/ai_processor.py` 会：

- 把字幕作为关联文件加入映射
- 解析语言后缀并转成 Emby 语言码
- 生成如 `xxx.zh-CN.default.ass` 的目标文件名
- 在主任务完成后以复制模式写入目标目录

### 电影合集

- 单电影：AI 标题提取 + TMDB 候选选择 + 目标文件名生成
- 多视频目录：可能进入电影合集分析
- 若合集候选被判定为“单电影 + 附加内容”，会回退到单电影处理并忽略附加内容

### 字幕导入 / 自动抓取 / 通知

- `src/subtitle/processor.py`：字幕包解压、AI 映射、语言归一化、可选 ffsubsync、写入目标目录
- `src/subtitle/auto_fetch.py`：只扫描缺失字幕视频；先加载候选帖 / 附件包信息，再让 AI 选择
- `src/queue/task_queue.py::_start_workers`：批次结束后触发 Emby 刷新与 Telegram 汇总通知

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

### 字幕

- 对齐：`subtitle_sync_enabled` / `subtitle_sync_mode` / `subtitle_sync_executable` / `subtitle_sync_extra_args` / `subtitle_sync_timeout_seconds` / `subtitle_sync_overwrite_policy`
- 自动抓取：`subtitle_auto_fetch_enabled` / `subtitle_auto_fetch_provider` / `subtitle_auto_fetch_candidate_limit` / `subtitle_auto_fetch_timeout_seconds` / `subtitle_auto_fetch_browser_enabled` / `subtitle_auto_fetch_acgrip_base_url` / `subtitle_auto_fetch_preferred_language` / `subtitle_auto_fetch_use_ai_rerank` / `subtitle_auto_fetch_search_mode` / `subtitle_auto_fetch_save_reason`

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
- `subtitle_upload/`

`src/logger.py` 当前行为：

- 文件日志：JSON 结构化、按天轮转、保留 7 天
- 控制台日志：彩色开发输出
- `log_level` 可在运行时更新

## 测试与回归

优先快速回归：

```bash
python -m compileall src
```

AI 测试入口：

- 文档：`tests/README_AI_Testing.md`
- 脚本：`tools/test_ai_recognition.py`
- 模式：`manual` / `auto` / `save`

## 注意事项

- 当前环境以 Windows 为主，代码里包含 Windows 路径与 `pywin32` 假设
- qBittorrent webhook 的路径未必可直接使用，先看 `src/web.py` 的路径修复与 Docker 映射
- 默认传输模式是硬链接；字幕导入和关联字幕通常走复制
- 若要看“为什么任务完成后又触发字幕 / 通知”，先看 `src/queue/task_queue.py`
- 若要看“为什么字幕被改成 Emby 风格语言后缀”，先看 `src/subtitle/processor.py` 和 `src/rename/ai_processor.py`
