# RENAME SUBSYSTEM

## OVERVIEW
`src/rename/` 负责主媒体整理链路：标题预处理、TMDB 查询、AI 分类与映射、目标文件名生成、文件迁移、失败落盘。

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| 主入口 | `process.py` | `Rename.process()` / `_process()` |
| TV 映射与严格校验 | `ai_processor.py` | Bangumi 上下文接入、映射路径回填、合法性过滤 |
| TMDB 查询 | `get_info.py` | TV/Movie 搜索与 season info 填充 |
| 标题清洗 | `cleaner.py` | 只应保留低风险 deterministic 规则 |
| 目标文件名 | `filename_builder.py` | Movie/Episode 命名与元数据拼装 |
| 文件迁移/记录 | `trans.py` | 链接/复制/剪切与 record 写入 |
| 通用常量 | `utils.py` | 视频后缀、特殊 token 等 |

## LOCAL CONVENTIONS
- 这里的主原则是 **AI-first + strict**。标题清洗是辅助，不是主决策器。
- `Rename.process()` 对“第一层没有视频的目录”会拆成子任务重新入队；不要把复杂容器目录硬塞进单次串行处理。
- `check_task_type()` 是当前 TV/Movie 判定核心；理解流程时优先看它，而不是旧文档里的 cleaner 思路。
- 电影与 TV 分支共用前置 AI 分类，但后续处理截然不同：TV 会补齐 TMDB 合法季集空间并走映射校验。
- 输出记录必须和 `data/task` / `data/record` 契约兼容，因为字幕链路会复用这些数据。

## ANTI-PATTERNS
- 不要把 AI 不可用当成“自动回退老规则”的许可；当前严格模式下应明确失败。
- 不要为了少数样本继续扩张大量标题、目录、season 特判表。
- 不要让 AI 返回的路径或季集号直接落地；必须经过后置严格校验。
- 不要在 bugfix 时顺手重构整条链路；这里模块长、耦合重，最怕顺便改坏。

## TV-SPECIFIC GOTCHAS
- TV 映射必须回到 TMDB 真实存在的 `SxxExx` 空间。
- Bangumi 关系只提供桥接语义，不直接等价到某个 TMDB season。
- `ai_processor.py` 会处理重复映射、越界映射、Season 0/special 语义冲突、路径脑补、编号回填等问题。
- 同 stem 字幕会跟随映射生成 Emby 风格语言后缀文件名，这是主链路的一部分，不是字幕页专属行为。

## CHANGE CHECKLIST
- 改分类逻辑：同时看 `process.py`、`ai_processor.py`、`tools/test_ai_recognition.py`、相关 TMDB/Bangumi 回归。
- 改命名格式：同时看 `filename_builder.py`、`trans.py`、字幕跟随命名是否受影响。
- 改 strict 校验：确认合法子集保留、非法映射剔除、失败原因落盘都没破坏。
