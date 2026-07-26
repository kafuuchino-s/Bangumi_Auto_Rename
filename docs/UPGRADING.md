# 升级到 0.3.0

`0.3.0` 是从 `v0.2.4` 之后 166 个提交整理出的发布准备基线。本文只描述部署与合同变化；它不表示已经创建 `v0.3.0` tag 或发布产物。

## 升级前

1. 停止正在运行的后端和任务 worker。
2. 备份整个 `data/`，其中包含配置、任务记录、日志、缓存和 Case Agent audit artifacts。
3. 记录当前镜像 tag 或代码 commit，确保可回滚。
4. 确认源码环境使用 Python 3.10+ 和 Node.js 22.19+。

## 依赖与构建

源码部署需要更新两套 Node 依赖，并重新构建前端：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install
Set-Location frontend
npm install
npm run build
Set-Location ..
```

Docker 部署应使用明确版本 tag，而不是仅依赖 `latest`。正式发布后可将 compose 中的镜像改为：

```yaml
image: ghcr.io/kafuuchino-s/bangumi-auto-rename:v0.3.0
```

## 必须检查的变化

### Pi 是唯一语义执行面

旧 Python AI client 和语义 fallback 已删除。升级后请在配置页重新执行模型发现或 AI 连接测试，并确认：

- API base URL、API key 和 interface 与 provider 匹配；
- Pi sidecar 使用的模型可见且可调用；
- 根目录 `npm install` 已完成；
- 严格模式失败会被记录，不会回退旧规则。

### 管理界面与 API

管理界面已迁移为 Vite SPA，由 FastAPI 在端口 `5999` 同源托管。旧 NiceGUI/Next.js 前端不再使用。反向代理至少应转发：

- `/api/*`
- `/sendTask`
- `/health`
- SPA 静态资源与 fallback

自定义客户端应按当前 `/api/*` v2 envelope 处理错误；不要依赖旧 UI 内部接口。

### 配置迁移

启动时会将历史 `bangumi_path` 幂等迁移为 `tv_path`，旧键随后移除。升级后在配置页核对：

- `tv_path`、`movie_path`、`anime_path`、`anime_movie_path`；
- Docker 的 `host_path_prefix` 与 `docker_mnt`；
- 传输模式和覆盖策略；
- 字幕自动抓取、字幕同步、Emby 与 Telegram 开关。

### 缓存迁移

元数据缓存已迁移到 diskcache。首次启动会保留旧碎 JSON 树作为回滚保险；确认新缓存正常前不要手动删除旧目录。

## 升级后验证

1. 请求 `GET /health`，确认后端存活。
2. 打开配置页执行模型发现，并保存一次非敏感配置变更。
3. 创建一个小型测试任务，确认任务进入队列且详情页可重试。
4. 检查 `data/logs/` 与任务详情中的 Case Agent 状态。
5. Docker 用户确认挂载目录对非 root `app` 用户可写。

完整 full146 不属于日常升级门禁。只有修改全局 verifier、preflight、语义执行路径或准备阶段性正式发布时才运行该回归。

## 回滚

停止新版本，恢复升级前的代码或镜像 tag，并还原备份的 `data/`。不要只回滚代码而继续复用已由新版本写入的配置或任务状态。
