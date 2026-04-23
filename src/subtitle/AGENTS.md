# SUBTITLE SUBSYSTEM

## OVERVIEW
`src/subtitle/` 负责两条字幕链路：手动导入压缩包/字幕文件，以及主任务成功后的自动抓取与可选 ffsubsync 调轴。

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| 字幕导入主入口 | `processor.py` | 解压、任务记录读取、AI 映射、目标字幕路径生成 |
| 自动抓取主入口 | `auto_fetch.py` | 扫缺失字幕、搜索候选、AI 重排、下载后回调导入 |
| 批量调轴 | `batch_sync.py` | 树级别 ffsubsync 处理 |
| 单字幕调轴封装 | `syncer.py` | ffsubsync runner、成功/失败状态 |
| 压缩包解压 | `extractor.py` | 支持字幕文件与压缩包输入 |
| provider 适配 | `providers/` | 站点搜索、候选页、附件包加载 |
| 规则/排序补充 | `ranker.py` | 抓取候选辅助逻辑 |

## LOCAL CONVENTIONS
- 字幕链路大量依赖 `data/task` 和 `data/record`；它不是纯文件扫描工具，而是“基于最近已处理任务的二次处理系统”。
- `SubtitleProcessor.process()` 既支持压缩包导入，也会被自动抓取流程复用；改这里要同时考虑手动导入与自动抓取。
- 语言后缀会归一到 Emby 风格，如 `zh-CN.default`；简体中文默认带 `.default`。
- ffsubsync 是可选能力，策略由配置控制：`best_effort` 与 `strict` 语义不同。
- 自动抓取是“候选搜索 → AI/规则筛选 → 下载 → 再走导入处理”，不是直接把网络资源塞到目标目录。

## ANTI-PATTERNS
- 不要绕过任务/记录数据结构自己猜目标视频；现有流程已经用持久化记录做范围收敛。
- 不要把自动抓取写成站点特化硬编码；provider 与候选/包选择是独立层。
- 不要把字幕语言后缀原样透传到最终文件名；最终命名必须符合 Emby 习惯。
- 不要忽略 `need_confirm` 分支；AI 无法确定匹配对象时应保留人工确认出口。

## CHANGE CHECKLIST
- 改导入逻辑：看 `processor.py`、`extractor.py`、相关字幕导入/同步测试。
- 改自动抓取：看 `auto_fetch.py`、`providers/`、`tests/test_subtitle_auto_fetch.py`。
- 改调轴：看 `syncer.py`、`batch_sync.py`、`tests/test_subtitle_processor_sync.py`。
