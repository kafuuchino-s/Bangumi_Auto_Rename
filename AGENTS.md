# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-17 Asia/Shanghai
**Commit:** 06ed92f
**Branch:** web

## OVERVIEW
Bangumi Auto Rename 是一个 Python + NiceGUI 的媒体整理应用，核心不是“单纯重命名”，而是 **AI-first + strict** 的整理流水线。系统把 Web UI / qBittorrent webhook、任务队列、TMDB/Bangumi/AI 映射、字幕导入/自动抓取/调轴、Emby 刷新和 Telegram 汇总串成一个完整处理链路。

## STRUCTURE
```text
Bangumi_Auto_Rename/
├── src/                    # 主应用代码；按子系统分层，不是单体脚本堆叠
│   ├── ai/                 # AI facade、OpenAI 运行时适配、结构化输出模型
│   ├── bangumi/            # Bangumi 上下文构建；只做 TV 映射辅助证据
│   ├── config/             # config.json 默认值、读写、路径/URL 规范化
│   ├── notification/       # Emby / Telegram 批次收尾通知
│   ├── pages/              # NiceGUI 页面组件与对话框
│   ├── queue/              # 懒启动 worker、批次统计、收尾触发
│   ├── rename/             # 主重命名、Local→Bangumi Case Agent 与兼容映射链路
│   ├── subtitle/           # 字幕导入、自动抓取、调轴、provider 适配
│   ├── logger.py           # 全局 structlog 配置
│   ├── main_page.py        # UI 主页装配
│   ├── start.py            # 进程启动入口
│   └── web.py              # 路由入口、/sendTask webhook、路径修复与入队
├── data/                   # 运行产物：config、日志、task/record、AI 快照、字幕下载
├── docs/                   # 架构与专项设计文档；过期描述服从代码与 CLAUDE.md
├── tests/                  # 平铺的功能/回归测试与测试样例
├── tools/                  # 独立运维脚本与脚本式回归工具；复用 src 内能力，不是另一套框架
├── CLAUDE.md               # 当前仓库最重要的工作约束文档
├── README.md               # 用户向使用说明
├── pyproject.toml          # PDM 脚本与依赖元信息
└── Dockerfile              # 运行镜像构建；只复制必要源码与测试样例
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| 进程启动 | `src/start.py` | `python -m src.start` 最终落到 `ui.run(...)` |
| Webhook 入站 | `src/web.py` | 做 skip_tags、宿主机→Docker 路径转换、URL 路径修复、队列去重 |
| UI 主页/入口按钮 | `src/main_page.py` | 连接添加任务、字幕导入、配置页、表格刷新 |
| 队列与批次收尾 | `src/queue/task_queue.py` | worker 懒启动；成功任务后可触发自动抓字幕；队列 drain 后统一通知 |
| 主重命名流程 | `src/rename/process.py` | AI-first 分类、Case Agent primary 路由、目录拆子任务、迁移落盘 |
| Local→Bangumi Case Agent | `src/rename/case_agent/` | 当前 Local→Bangumi 主线；负责 evidence request、MappingDraft、Verifier、audit |
| TV 严格映射/后置校验 | `src/rename/ai_processor.py` | 映射路径校验、重复/越界剔除、Season 0/special 过滤、关联字幕跟随 |
| AI 提供商与结构化输出 | `src/ai/client.py` | facade；OpenAI 运行时入口、缓存、schema 构建 |
| Bangumi 辅助上下文 | `src/bangumi/context_builder.py` | 只给 TV prompt 提供桥接证据，不直接决定最终季集 |
| 字幕导入 | `src/subtitle/processor.py` | 解压、AI 映射、语言后缀归一化、可选 ffsubsync |
| 字幕自动抓取 | `src/subtitle/auto_fetch.py` | 扫描缺失字幕、候选抓取、AI 重排、下载后复用导入流程 |
| 配置默认值/持久化 | `src/config/config_manager.py` | config 默认值、线程内临时覆盖、路径转换、URL 标准化 |
| 运行时路径/落盘目录 | `src/utils/path.py`, `src/utils/utils.py` | `CONFIG_PATH` / `TASK_PATH` / `RECORD_PATH` / `AI_ANALYSIS_PATH` 及读写 helper |
| 日志行为 | `src/logger.py` | JSON 文件日志 + 彩色控制台日志，支持运行时切换级别 |
| AI 回归脚本 | `tools/test_ai_recognition.py`, `tests/README_AI_Testing.md` | `manual` / `auto` / `save` 三模式 |
| 架构总览 | `docs/PROJECT_STRUCTURE_AND_FLOW.md` | 比旧文档更可信，但仍服从代码与 `CLAUDE.md` |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `ui.run(...)` | startup call | `src/start.py` | 启动 NiceGUI 应用 |
| `_send_task` | webhook handler | `src/web.py` | 外部任务入口与路径修复闸门 |
| `TaskQueueManager.enqueue` | queue API | `src/queue/task_queue.py` | 入队、懒启动 worker |
| `TaskQueueManager._start_workers` | queue lifecycle | `src/queue/task_queue.py` | drain 后触发 Emby/Telegram 汇总 |
| `Rename.process` | pipeline entry | `src/rename/process.py` | 目录拆分与主重命名入口 |
| `Rename._process` | pipeline core | `src/rename/process.py` | AI-first 分类、Case Agent/TMDB 入口分流、失败落盘 |
| `run_local_bangumi_case_agent` | Case Agent entry | `src/rename/case_agent/` | Local→Bangumi evidence-driven 判定入口 |
| `AIProcessor.analyze_anime_files` | TV mapping entry | `src/rename/ai_processor.py` | Bangumi + TMDB + 本地文件分析后做 AI 映射 |
| `SubtitleProcessor.process` | subtitle import entry | `src/subtitle/processor.py` | 压缩包到目标字幕文件的主流程 |
| `SubtitleAutoFetcher.process_task` | auto-fetch entry | `src/subtitle/auto_fetch.py` | 成功任务后抓缺失字幕 |
| `AIClient` | facade | `src/ai/client.py` | 提供商选择、结构化输出封装、缓存 |

## CONVENTIONS
- 当前心智模型必须是 **AI-first + strict**，不是老的 cleaner-first。少量 deterministic 清洗只做低风险预处理。
- Bangumi 只用于 TV 映射增强；**TMDB 才是最终合法输出空间**。
- 路径/任务/记录是文件化状态：`data/task`、`data/record`、`data/ai_analysis` 是跨模块共享契约的一部分。
- 注释与 UI 文案以中文为主。
- Windows 是一等运行环境；路径修复、宿主机→Docker 映射、`pywin32` 假设都是真实约束。
- `tests/` 以平铺文件为主，回归方式是“定向脚本 + 特定测试模块”，不是 repo 内声明好的单一 pytest 套餐。
- 分析 rename lane / sample-pool 失败时，必须使用主流程同款证据（raw sample、LocalEvidence/local_sample、TMDB legal graph、Bangumi bridge、alignment hints、AI snapshots、validator issues）来推理；主流程没看到的信息不能作为修复依据。
- 固定层只能做确定性、可验证的事情（事实抽取、合法性、coverage、duplicate、preflight）；候选 ownership、相似作品取舍、special/extra 语义成立这类不确定判断必须交给 AI，通过 Case Agent 的 evidence request、MappingDraft、Verifier issue/audit guidance 引导，不能由固定层用 `strong` hint 或 hard conflict 伪装成裁决。
- 所有给 AI 看的短 ref（`F*`/`G*`/`C*`/season/node/evidence refs）都必须和同一 payload 内的可读 semantic card 绑定出现；AI 输出仍只写短 ref，固定层只用 ref canonicalize/validate，不能让 AI 靠裸 ref 自行查表理解语义。
- 将样本经验写回主流程时，先抽象成条件树（触发条件、成立证据、不成立证据、fail-closed 边界），再检查是否和 no-sharing、re-edit guard、special pool、regular explicit SxxEyy guard 等全局边界冲突；必要时加入正反例区分特定情况。经验只能教 Case Agent 判断和调证，固定层仍只验证 legality、coverage、duplicate 和 preflight，不能自动写语义映射。
- 实现、重构、验证、测试等工作若能安全拆分且避免文件冲突，优先使用多个并行 fixer；编排者负责产品语义边界、合并验证、focused/full/audit 验收，fixer 不直接决定样本该过还是 fail-closed。
- Full146 触发策略：单样本修复只跑 focused + protection；攒够 3-5 个 bucket 修复后再跑 full；或触碰高风险全局 gate（validator、preflight、execution、全局 prompt 边界）时才立即 full。

## ANTI-PATTERNS (THIS PROJECT)
- 不要把当前链路理解成“AI 失败就自动回退旧规则”。默认 `ai_force_strict=true`，失败应按失败任务记录。
- 不要继续扩张大量标题/目录硬编码来替代现有 AI 链路，除非只是低风险规范化。
- 不要让 Bangumi 直接决定 season number；它只是辅助证据，不是权威映射源。
- 不要假设 `data/` 是临时垃圾目录；这里保存 config、task、record、AI 分析、字幕抓取产物，脚本与流程会复用它。
- 不要把队列收尾理解成“每个任务立刻通知”；当前设计是批次结束后统一 Emby/Telegram 汇总。
- 不要忽略 `src/web.py` 的路径修复与 skip_tags 逻辑；外部路径未必能直接用。
- 不要为 sample-pool 个例写样本专属 alias、硬编码 title 或固定 file -> legal_node 正映射；若人能用主流程同款证据判断正确，应把通用经验写回 Case Agent prompt、evidence request policy、MappingDraft/Verifier guidance 或 audit 反馈。
- 不要让固定层把局部、脆弱、语义性的 overlap/bridge 判断升级成 hard blocker；不确定 evidence 应作为 AI 参考或 diagnostic，而不是覆盖 AI 的全局 proposal。
- 若文档与代码冲突，以当前代码和 `CLAUDE.md` 为准。

## UNIQUE STYLES
- Webhook 入口不是纯转发：会修复 latin1/utf-8 路径、`+` 空格问题、Windows→Docker 路径、skip tag、队列去重。
- 队列是懒启动 worker 模式；成功任务后可串接字幕自动抓取，再由 batch drain 统一发收尾通知。
- 字幕链路与主重命名链路是独立流程，但会通过 `task/record` JSON 互相衔接。
- Docker 镜像显式忽略大多数 `tests/`，只保留配置页 AI 测试依赖的样例 JSON。

## COMMANDS
```bash
pip install -r requirements.txt
.venv\Scripts\python.exe -m src.start
pdm run start
.venv\Scripts\python.exe -m compileall src
.venv\Scripts\python.exe tools/test_ai_recognition.py --mode auto --input tests/example_test_case.json
docker build -t bangumi-auto-rename .
docker run -p 5999:5999 bangumi-auto-rename
```

## NOTES
- 本地运行与回归验证优先使用仓库内 `.venv\Scripts\python.exe`，避免误用系统或 PATH 上的其它 Python。
- 当前环境里若 `pytest` 报 `ValueError: I/O operation on closed file`，先怀疑终端/capture 问题，不要直接判定业务回归失败。
- `requirements_docker.txt` 与主 `requirements.txt` 分离；Docker 镜像的依赖集合和本地开发不完全等价。
- `tests/example_test_case.json` 与 `tests/example_expected.json` 是被 Docker 保留的特殊测试样例。
- 若要改主流程，优先读 `CLAUDE.md` 和 `docs/PROJECT_STRUCTURE_AND_FLOW.md`，再深入子系统代码。
- 子系统细节见：`src/rename/AGENTS.md`、`src/subtitle/AGENTS.md`、`src/ai/AGENTS.md`。

## LOCAL AGENT PREFERENCES
- Git 推送默认优先使用用户自己的 `fork` remote。
- 不直接 push `origin`，除非用户明确要求。
