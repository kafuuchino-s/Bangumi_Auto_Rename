# AI SUBSYSTEM

## OVERVIEW
`src/ai/` 提供统一 AI facade、OpenAI 运行时适配、结构化输出 schema、视频分析与配置页 API 测试能力；它本身不决定最终落地，最终结果仍需由下游 strict 校验接管。

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| facade 入口 | `client.py` | OpenAI 运行时入口、缓存、schema helper、通用 prompt 调用 |
| OpenAI 适配 | `openai_client.py` | 输出格式与 API interface 路由 |
| 抽象基类 | `base_client.py` | 公共调用约束 |
| 结构化模型 | `models.py` | episode mapping、subtitle mapping、title extraction 等 schema |
| 视频分析 | `video_analyzer.py` | 本地文件结构化分析，给 TV prompt 提供上下文 |
| API 测试器 | `unified_ai_tester.py` | 配置页多格式测试相关能力 |

## LOCAL CONVENTIONS
- `AIClient` 是唯一推荐入口；业务层不要直接散落地 new provider 客户端。
- OpenAI 走结构化输出优先；JSON schema 和结果模型是稳定契约的一部分。
- facade 自带标题提取缓存；改标题提取逻辑时要留意 cache key 规范化。
- AI 结果应尽量返回“最可执行的合法子集”；拿不准时宁可 `unmatched`，不要硬凑。
- 本目录输出的是“建议/候选/结构化分析结果”，不是最终权威状态；最终合法性由 rename/subtitle 侧校验。

## ANTI-PATTERNS
- 不要在业务模块直接复制 prompt 拼接或 provider 分支逻辑；统一收敛到 facade/adapter。
- 不要放松 schema 或吞掉解析失败；结构化失败本身就是重要信号。
- 不要把 provider 差异泄漏到业务层；兼容性处理应尽量留在 adapter 内。
- 不要把 AI 输出当成可信路径或可信 season/episode，必须交给下游 strict 校验。

## CHANGE CHECKLIST
- 改 title extraction：看 `client.py` 缓存、schema、`tools/test_ai_recognition.py`、配置页 API 测试。
- 改 provider 路由：同时看 `openai_client.py` 与 `config_manager.py` 的相关配置项。
- 改 response model：确认 rename/subtitle/bangumi 相关调用方仍兼容。
