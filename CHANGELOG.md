# Changelog

本项目从 `v0.3.0` 起在此记录面向部署者的产品变更。提交级细节仍保留在 Git 历史中。

## [0.3.0] - Unreleased

`0.3.0` 是当前代码的发布准备基线，尚未创建 tag 或 GitHub Release。

### Added

- Vite + React 19 管理界面，覆盖任务、字幕、日志和配置工作流。
- AI 模型发现、qBittorrent category/tag 过滤、Windows 便携包与版本化 GHCR 发布流程。
- Local→Bangumi→TMDB、字幕导入和字幕自动抓取的 Pi Case Agent 合同链路。
- 发布前质量门禁：Python 合同测试、Pi sidecar 语法检查、前端 lint/test/i18n/build。

### Changed

- 后端统一为 FastAPI/Uvicorn，并由同一端口提供 `/api/*`、`/sendTask`、`/health` 和构建后的 SPA。
- 元数据缓存从碎 JSON/lock 文件迁移到 diskcache。
- Docker 镜像改为多阶段构建，并内置 Node.js、ffmpeg/ffprobe、ffsubsync 和 unrar。
- 配置键 `bangumi_path` 更名为 `tv_path`；启动时自动执行幂等迁移。

### Breaking Changes

- 旧 Python AI provider client 和语义 fallback 已删除；生产语义执行必须使用 Pi sidecar。
- 源码和便携构建要求 Node.js `>=22.19.0`，并需要分别安装根目录与 `frontend/` 的 npm 依赖。
- 旧 NiceGUI/Next.js 管理界面不再提供；外部 UI/API 集成应使用当前 `/api/*` v2 响应合同。qBittorrent webhook `/sendTask` 保留。
- 严格模式下 provider、合同或合法图失败会记录失败或 `fail_closed`，不会静默回退旧映射规则。

### Upgrade Notes

从 `v0.2.4` 或更早版本升级前，请完整阅读 [`docs/UPGRADING.md`](docs/UPGRADING.md)。本质量门禁不运行 full146；该样本池保留为语义边界变化或阶段收口时的手动回归。
