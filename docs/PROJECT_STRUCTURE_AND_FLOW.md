# 项目结构与主流程

本文档描述当前 `Bangumi Auto Rename` 的**整体结构与运行流程**，覆盖：
- Webhook / Web UI 任务入口
- 队列调度
- 主重命名链路
- TV / Movie 分支
- Bangumi 接入后的 TV 映射流程
- 字幕导入
- 字幕自动抓取
- Emby / Telegram 批次收尾

若与旧文档或历史实现描述冲突，**以当前代码与 `CLAUDE.md` 为准**。

相关文档：
- `docs/AI_FIRST_STRICT_CLEANUP_TODO.md`
- `docs/BANGUMI_API_REFERENCE.md`

---

## 1. 项目定位

Bangumi Auto Rename 已不只是“自动重命名器”，而是一个以 **AI-first 主流程** 为核心、串联以下能力的媒体整理工具：

- 基于 TMDB 元数据整理动漫 / 剧集 / 电影
- AI 标题提取、媒体类型判断、TMDB 候选选择、剧集映射
- 支持硬链接 / 复制 / 移动
- 字幕压缩包导入与 AI 映射
- 缺失字幕自动抓取
- ffsubsync 字幕对齐
- 批次完成后刷新 Emby 并发送 Telegram 汇总通知

当前主链路是：**AI-first + strict**。

含义是：
- 标题提取、媒体类型判断、TMDB 候选选择、复杂目录映射优先交给 AI
- Bangumi 只作为 TV 映射阶段的辅助证据
- 最终输出仍必须回到 **TMDB 合法空间**
- 路径存在性、重复映射、越界映射、Season 0 / special 语义冲突等后置校验继续保留

---

## 2. 目录结构与模块职责

### 2.1 关键目录

- `src/rename/`
  - 主重命名链路；包含 AI 分类与映射、TMDB 查询、命名规则、文件迁移
- `src/ai/`
  - 统一 AI 客户端、OpenAI 运行时适配、结构化输出模型、视频分析
- `src/bangumi/`
  - Bangumi API 读取、subject / episode 上下文构造；只为 TV 映射提供辅助证据
- `src/subtitle/`
  - 字幕解压、导入、对齐、自动抓取、provider 适配
- `src/queue/`
  - 队列调度、任务状态、批次汇总与收尾
- `src/notification/`
  - Emby / Telegram 通知单例
- `src/pages/`
  - NiceGUI 页面：配置、任务表格、字幕导入等
- `src/config/`
  - 配置中心与默认值
- `data/`
  - 运行时数据、日志、任务记录、AI 快照等
- `tests/`
  - 平铺的功能/回归测试、AI 接口与 Bangumi 映射测试样例

### 2.2 关键入口文件

- `src/start.py`：应用入口
- `src/web.py`：FastAPI + `/sendTask` webhook
- `src/main_page.py`：主页面布局
- `src/config/config_manager.py`：配置默认值与配置中心
- `src/queue/task_queue.py`：队列调度与批次收尾
- `src/rename/process.py`：主重命名引擎
- `src/rename/ai_processor.py`：TV AI 映射、Bangumi 上下文接入、后置 strict 校验、映射应用
- `src/rename/get_info.py`：TMDB 搜索与 TV season info 补齐
- `src/rename/trans.py`：最终文件迁移与 `record` 写入
- `src/ai/video_analyzer.py`：本地视频结构化分析
- `src/subtitle/processor.py`：字幕导入主入口
- `src/subtitle/auto_fetch.py`：字幕自动抓取入口

---

## 3. 全局主链路总览

```text
Web UI / qBittorrent webhook
→ src/web.py 接收任务、修复路径、过滤 skip_tags、做队列去重
→ src/queue/task_queue.py 入队并按 queue_max_workers 并发调度
→ src/rename/process.py::process()
→ AI 提取标题 / 类型 + TMDB 候选搜索与选择
→ Movie / TV 分支处理
→ TV 分支中：本地文件分析 + Bangumi context + AI 剧集映射 + strict 校验
→ Trans.trans_file() 执行硬链接 / 复制 / 移动
→ 写入 task / record / ai_analysis
→ 可选：字幕自动抓取
→ 批次结束后：Emby 刷新 + Telegram 汇总
```

可以把系统理解成三条主链路：

1. **重命名主链路**：下载完成后的主处理流程
2. **字幕导入链路**：用户上传字幕压缩包后导入到既有视频
3. **字幕自动抓取链路**：任务成功后扫描缺失字幕并自动补齐

---

## 4. 任务接入层：Webhook / Web UI

### 4.1 qBittorrent webhook

入口：`src/web.py`

`/sendTask` 负责：
- 接收下载完成后的路径与 tag
- 处理可能的编码异常路径
- 将 Windows 宿主机路径转换为 Docker 内路径
- 修复 URL 编码问题（如 `+` 被解为空格）
- 根据 `skip_tags` 决定是否直接忽略
- 检查路径是否已在队列中，避免重复入队

核心职责：
- `skip_tags` 跳过标签
- `host_path_prefix` / `docker_mnt` 的宿主机 → Docker 路径映射
- URL 编码异常路径修复
- 队列去重

### 4.2 Web UI 手动添加任务

用户也可以通过 NiceGUI 页面手动添加任务，最终仍会进入同一个队列与主重命名链路。

---

## 5. 队列层：任务调度与批次收尾

入口：`src/queue/task_queue.py`

### 5.1 入队

`TaskQueueManager.enqueue()` 负责：
- 创建任务 ID
- 记录任务元数据
- 放入 asyncio 队列
- 在必要时启动 worker
- 通知 UI 刷新

### 5.2 并发消费

`_start_workers()` 会按 `queue_max_workers` 启动多个 worker，并发处理任务。

### 5.3 单任务执行

worker 内部会在线程池中调用真正的重命名逻辑：
- `Rename.process()`

### 5.4 批次收尾

当前设计是“**批次**”而不是“每个任务单独通知”：
- 成功任务可触发字幕自动抓取
- 所有 worker 结束后统一：
  - 触发 Emby 刷新
  - 发送 Telegram 批次汇总通知

这也是当前系统里“为什么任务结束后还会继续做字幕 / 通知”的原因。

---

## 6. 主重命名链路

入口：`src/rename/process.py`

### 6.1 `Rename.process()` 的第一层分流

传入路径后会先判断：

#### 情况 A：目录第一层就有视频文件
直接进入 `_process()`。

#### 情况 B：目录第一层没有视频文件
说明这是“非视频直系目录”。此时：
- 父任务不会强行在当前层处理
- 会把子目录 / 子文件拆成多个子任务重新入队
- 由队列并发处理

这样可以避免复杂外层目录拖慢整批任务，也能让子目录更贴近真实媒体单元。

### 6.2 `_process()` 的前置守卫

正式处理前会先检查：
- TMDB Key 是否存在
- 路径是否存在
- 如果是单文件，是否为视频后缀

任何前置条件不满足，都会按失败任务写入记录并返回。

---

## 7. AI-first 标题解析与 TMDB 定位

### 7.1 标题预处理

`_process()` 会先对目录名 / 文件名做少量 deterministic 清洗，提取：
- 基础标题
- 年份
- season-aware 标题
- AI 输入标题

这里的角色是“低风险规范化”，不是旧式 cleaner-first 主路径。

### 7.2 `check_task_type()`：当前核心入口

当前 TV / Movie 判定与 TMDB 定位都统一经过：
- `src/rename/process.py::check_task_type()`

它会先调用 AI 提取：
- `title`
- `fallback_title`
- `type`

然后按优先级构造多个 TMDB 查询候选，尝试：
- TV 搜索链
- Movie 搜索链

再结合：
- AI 提取类型
- 路径与文件数量特征
- TMDB 命中结果

最终确定：
- 最终标题
- TMDB 条目
- 是否动漫
- 是 TV 还是 Movie
- 当前步骤的 AI 置信度

这也是为什么当前主流程已经不应再被理解成“纯 cleaner + TMDB 搜索”，而是：

```text
少量规则清洗
→ AI 提取标题 / fallback_title / type
→ 多 query TMDB 搜索
→ 候选选择
→ 判定 TV / Movie
```

---

## 8. Movie 分支

### 8.1 单电影

对于单电影：
- `check_task_type()` 已经确定 TMDB 电影条目
- 主流程只需要生成目标目录与目标文件名
- 然后交给 `Trans` 执行文件迁移

### 8.2 多视频目录：电影合集候选

如果目录下包含多个视频文件，系统会先把它视为“电影合集候选”：
- 先做本地文件分析
- 调用 AI 做合集分析
- 判断它到底是：
  - 真正的电影合集
  - 还是“单电影 + 特典 / 花絮 / 附加内容”

若 AI 判断是后者：
- 会回退为单电影流程
- 只保留高置信正片文件
- 忽略附加内容

若 AI 判断是真正合集：
- 会逐部电影完成 TMDB 解析与目标文件生成
- 再逐项写入任务记录并迁移

---

## 9. TV 分支

TV 是当前最核心的 AI-first 链路。

总体流程（概念层）如下；实际实现分散在 `process.py`、`get_info.py`、`ai_processor.py`、`trans.py` 等模块中，而不是单文件线性串完：

```text
TV TMDB 条目确定
→ Search.fill_season_info() 补齐 TMDB 季集空间
→ 收集本地视频文件
→ VideoAnalyzer 分析本地文件
→ BangumiContextBuilder 构造 Bangumi 辅助上下文
→ AIProcessor 调 AI 输出 file_mapping / unmatched_files
→ validate_tv_result() 做 strict 校验
→ apply_ai_mapping() 生成源 → 目标映射
→ Trans.trans_file() 执行迁移并写 record
```

### 9.1 先补齐 TMDB 合法空间

TV 分支首先会通过 `Search` 调用：
- `fill_season_info()`

作用是：
- 拉取各 season 的详细 episode 信息
- 明确系统最终可落地的合法 `SxxExx` 空间

这里非常关键，因为当前实现里：
- **TMDB 是唯一最终输出标准**
- 后续所有 AI 映射结果都必须回到这个空间里

### 9.2 本地文件分析

进入 AI 映射前，会先调用 `VideoAnalyzer.analyze_video_files()`，对本地视频做结构化分析，提取：
- 相对路径
- 文件名
- 编号线索
- 时长 / 体积等可用信息
- 子目录语义

这一步的输出会直接喂给 TV prompt。

---

## 10. Bangumi 接入后的 TV 映射流程

Bangumi 只接入 **TV AI 映射阶段**，不参与电影链路，也不直接决定最终 season number。

### 10.1 `BangumiContextBuilder` 的职责

入口：`src/bangumi/context_builder.py`

它会围绕当前已经确定的 TMDB TV 条目，构建紧凑的 `bangumi_context`；当前主入口是 `build_tv_context(...)`：

1. 根据 TMDB 标题 / 原标题 / 本地文件名生成少量搜索词
2. 搜索 Bangumi subject
3. 选择最可能的主条目
4. 保守扩一跳 related subjects
5. 拉取各 subject 的 episode 列表
6. 裁剪成紧凑上下文

目标不是替系统做 season 推断，而是给 AI 提供“额外桥接证据”。

### 10.2 Bangumi 的定位

当前实现原则是：
- Bangumi **不是权威输出源**
- Bangumi **不是直接的 season 映射规则表**
- Bangumi 的 relation（前传 / 续集 / 番外篇 / 总集篇）只是语义证据
- 最终仍必须回到 TMDB 中真实存在的 `SxxExx`

典型用途：
- 本地文件只有编号
- Bangumi episode 可以提供 `sort / ep / type / 标题 / 日期`
- AI 再用这些信息把“本地编号语义”桥接回 TMDB 真实集号

### 10.3 Bangumi 失败时的退路

如果 Bangumi 搜索失败、接口异常或上下文构造失败：
- 自动回退 **TMDB-only**
- 不能阻塞主流程

所以 Bangumi 是增强项，不是硬依赖。

---

## 11. TV Prompt 与 AI 映射输出

TV prompt 当前会同时输入三类信息：

1. **TMDB**：最终合法目标空间
2. **Bangumi**：辅助条目关系与 episode 元数据
3. **本地文件分析**：本地命名、目录结构、文件粒度线索

Prompt 中明确要求 AI：
- 只能输出 TMDB 中真实存在的 `SxxExx`
- Bangumi relation 不能直接等价为某个 TMDB season
- 复杂目录下可优先输出部分合法映射，不要直接空映射
- 拿不准时宁可放到 `unmatched_files`
- `file_mapping.file_path` 必须从输入文件列表中原样引用，不能脑补路径

因此当前 TV 链路已经是：

```text
TMDB 目标空间
+ Bangumi 辅助证据
+ 本地文件结构化分析
→ AI 生成 episode mapping
→ strict 校验
```

---

## 12. TV 映射后的 strict 校验

AI 输出不会直接使用，而是先经过 `src/rename/ai_processor.py` 的后置校验。

当前保留的核心约束包括：
- 路径必须真实存在
- 不允许 AI 脑补不存在的文件路径
- 不允许重复映射
- 不允许越界映射到 TMDB 不存在的集号
- Season 0 / special 语义冲突要过滤
- 合法子集可保留，非法映射会被剔除或整任务失败

这也是当前“AI-first 但 strict”的核心含义：
- 前面尽量让 AI 理解复杂语义
- 后面仍用确定性规则卡住输出边界

---

## 13. 最终路径生成与文件迁移

### 13.1 生成最终映射

在 TV 分支中，`apply_ai_mapping()` 会把 AI 结果转换为：
- 视频源路径 → 最终目标路径
- 同 stem 关联字幕 → Emby 风格字幕目标路径

### 13.2 关联字幕跟随重命名

若同目录存在同 stem 字幕（如 `.chs.ass`、`.tc.srt`）：
- 会被识别为关联文件
- 语言后缀会转成 Emby 风格语言码
- 与视频一起生成目标文件名
- 在主任务结束后以复制模式写入目标目录

### 13.3 执行文件迁移

最终统一由 `Trans.trans_file()` 执行：
- 硬链接 / 复制 / 移动
- 是否覆盖由配置控制

---

## 14. 任务记录、AI 快照与运行时数据

### 14.1 任务记录

主流程完成后会写入任务数据，当前重点字段包括：
- `ai_attempted`
- `ai_used`
- `ai_confidence`
- `failure_reason`
- `pipeline_mode`
- `tmdb_id`
- `tmdb_media_type`
- `target_root`

### 14.2 AI 快照

AI 相关输入 / 输出会落到 `data/ai_analysis/`，便于排查：
- prompt
- provider 原始输出
- Bangumi context
- 映射结果

### 14.3 运行时数据目录

运行时数据主要位于：
- `data/config.json`
- `data/log/`
- `data/task/`
- `data/record/`
- `data/ai_analysis/`
- `data/subtitle_upload/`

---

## 15. 字幕导入链路

入口：`src/subtitle/processor.py`

当前链路：

```text
Web UI 字幕导入
→ SubtitleProcessor.process()
→ 解压字幕包
→ 读取近期任务记录 / 指定任务
→ AI 输出字幕文件到视频文件的映射
→ 可选 ffsubsync 对齐
→ 按 Emby 风格语言后缀写入目标目录
```

特点：
- 复用 AI 映射思路，但对象变成“字幕包内文件 -> 已入库视频”
- 支持语言归一化
- 支持可选字幕对齐
- 最终输出是 Emby 友好的字幕命名

---

## 16. 字幕自动抓取链路

入口：`src/subtitle/auto_fetch.py`

当前链路：

```text
主任务完成后
→ TaskQueueManager._execute_subtitle_auto_fetch()
→ SubtitleAutoFetcher.process_task()
→ 扫描缺失字幕视频
→ 搜索字幕源
→ 加载帖子 / 附件包信息
→ AI 选择候选帖与附件包
→ 下载字幕包
→ 复用 SubtitleProcessor 导入
```

当前实现偏好：
- 搜索 query 尽量宽松
- 候选帖 / 附件信息先加载出来
- 再让 AI 从“已加载候选”中判断
- 不继续堆大量候选级硬编码规则回退

---

## 17. Emby / Telegram 通知链路

这部分位于 `src/queue/task_queue.py` 的批次收尾逻辑中。

### Emby
- 只有当批次中存在成功任务时才会触发刷新
- 以通知单例模式调用

### Telegram
- 按是否有成功 / 失败任务以及配置开关决定是否发送
- 发送的是批次汇总，而不是每个任务独立发送

因此它属于：
- **队列层的 batch post-processing**
- 而不是重命名引擎内部直接调用

---

## 18. 当前架构原则

### 18.1 AI-first + strict

遇到以下问题时，优先考虑 AI-first：
- 标题提取 / query 扩词
- 候选排序 / 候选选择
- 复杂目录语义理解
- 多来源元数据桥接

但 AI-first 不等于放宽，最终仍要经过：
- TMDB 合法空间校验
- 路径存在性校验
- 重复映射 / 越界映射清洗
- Season 0 / special 语义冲突过滤

### 18.2 Bangumi 只是 TV 映射辅助层

Bangumi 的职责是：
- 帮助 AI 理解本地编号、Bangumi episode 语义、TMDB 最终集号之间的桥接关系

Bangumi 不负责：
- 直接决定最终 season number
- 替代 TMDB 成为输出权威
- 在失败时阻塞主流程

补充判断：
- 当前 **不建议把 Bangumi 像 TV 一样全面接入动漫电影主流程**
- 电影链路的核心问题通常是标题提取、TMDB 候选歧义、电影合集拆分，而不是 episode 编号桥接
- 若后续真实回归确认“动漫电影的主要失败点集中在剧场版 / 总集篇 / 前后篇 / 系列编号歧义”，更合适的做法也是把 Bangumi 作为**少数高歧义 anime movie 场景下的候选重排辅助层**，而不是默认全量注入电影链路

### 18.3 回归工具应尽量对齐主流程

如果回归工具链的目标是验证主流程效果：
- 应尽量复用主流程的 AI-first 标题解析与 TMDB 候选选择
- 不应长期维持明显偏离主流程的 cleaner-first 分叉

---

## 19. 一页速记

```text
Webhook / Web UI
→ src/web.py
→ src/queue/task_queue.py
→ src/rename/process.py
→ AI 提取标题 / 类型
→ TMDB 候选搜索与选择
→ Movie / TV 分支
→ TV: VideoAnalyzer + Bangumi context + AI 映射 + strict 校验
→ Trans.trans_file()
→ task / record / ai_analysis
→ 可选字幕自动抓取
→ Emby 刷新 + Telegram 汇总
```
