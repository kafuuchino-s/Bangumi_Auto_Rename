# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Bangumi Auto Rename（番剧自动重命名）是一个 Python Web 应用程序，可自动将动漫/电视剧集文件重命名并整理为 Emby 兼容的文件夹结构。使用 TMDB API 获取元数据，支持硬链接、复制或移动文件。项目包含实验性的 AI 剧集映射功能，支持 OpenAI 和 Google Gemini。

## 常用命令

```bash
# 启动应用（默认端口 5999）
python -m src.start
# 或使用 PDM
pdm run start

# 安装依赖
pip install -r requirements.txt

# Docker 构建和运行
docker build -t bangumi-auto-rename .
docker run -p 5999:5999 bangumi-auto-rename

# Docker 重建并重启（开发环境）
docker build -t bangumi-auto-rename:latest . && docker-compose -f "C:\Users\kafuuchino\Emby\docker-compose.yml" up -d bangumi-auto-rename
```

## 代码风格

- **格式化工具**: Black + isort（profile: black，line_length: 79）
- **类型检查**: basedpyright（basic 模式）
- VSCode 保存时自动格式化

## 架构

### 入口点
- `src/start.py` - 应用入口，初始化 NiceGUI
- `src/web.py` - FastAPI 路由和 NiceGUI 配置，包含 `/sendTask` 端点供 qBittorrent 调用

### 核心处理流程
```
用户输入（Web UI / qBittorrent webhook）
    ↓
FastAPI 路由（/sendTask）
    ↓
Rename.process() [src/rename/process.py]
    ↓
├── 搜索 TMDB 获取元数据 [src/rename/get_info.py]
├── [可选] AI 分析 [src/ai/]
├── 生成文件映射（传统正则或 AI）
    ↓
Trans.trans_file() [src/rename/trans.py]
    ↓
保存任务元数据到 JSON
```

### 核心模块

| 目录 | 功能 |
|------|------|
| `src/rename/` | 核心逻辑：`process.py`（主引擎）、`get_info.py`（TMDB API）、`trans.py`（文件操作）、`cleaner.py`（文件名解析） |
| `src/ai/` | AI 集成：`client.py`（工厂模式）、`openai_client.py`、`gemini_client.py`、`video_analyzer.py`（基于 hachoir） |
| `src/pages/` | NiceGUI Web UI 页面 |
| `src/config/` | JSON 配置管理 |
| `src/utils/` | 路径处理和工具函数 |

### AI 系统
- `src/ai/client.py` 使用工厂模式选择 OpenAI 或 Gemini 提供商
- `src/ai/models.py` 定义 Pydantic 结构化输出模型
- `src/ai/video_analyzer.py` 使用 hachoir 提取视频元数据
- 置信度阈值用于过滤不可靠的 AI 结果

### 数据存储
运行时数据存储在 `data/` 目录（已 gitignore）：
- `data/config.json` - 用户配置
- `data/log/` - 结构化日志（JSON 格式）
- `data/task/` - 任务元数据
- `data/record/` - 文件映射记录
- `data/ai_analysis/` - 保存的 AI 分析结果

### 重要常量
`src/rename/utils.py` 包含关键常量：
- 视频格式、特殊剧集标签（OVA、OAD、SP）
- 额外内容标签（NCOP、NCED、PV、CM）
- 季度/剧集提取正则表达式

### Season 0 特殊场景处理
`src/rename/ai_processor.py` 中的 `_collect_season0_files()` 负责收集需要归入 TMDB Season 0 的特典文件：

| 场景 | 检测方式 | 示例 |
|------|---------|------|
| 特典文件夹 | 目录名在 `SPECIAL_FOLDER_NAMES` 中 | `SPs/`、`Extras/`、`特典/` |
| 特典标签 | 文件名包含 OVA、OAD、SP 等 | `[Anime] OVA.mkv` |
| 小数集数 | 正则 `DECIMAL_EPISODE_PATTERN` | `12.5`、`5.5`（总集篇） |
| 第00集 | 正则 `EPISODE_00_PATTERN` | `第00話`、`[00]`、`E00`（序章） |
| 宣传内容 | `is_promotional_content()` 过滤 | NCOP、NCED、PV、CM（不处理） |

**AI Prompt 注意事项**（`client.py` 的 `analyze_season0_mapping`）：
- **SP 编号不等于 TMDB 集数**：`[SP03]` 可能对应 TMDB S0E1，需按标题匹配
- **Vol.SP 格式**：`[Vol.01][SP01]` 中 Vol 是 BD 卷号，不是集数
- 优先级：标题相似度 > 播出日期 > 序号

## 注意事项

- 代码注释和 UI 主要使用中文
- TMDB API 需要网络访问，可能存在速率限制
- AI 功能为实验性功能，失败时会回退到正则匹配
- Windows 特定：使用 pywin32 进行部分操作
- 默认传输模式为硬链接（节省空间，保留原文件）
