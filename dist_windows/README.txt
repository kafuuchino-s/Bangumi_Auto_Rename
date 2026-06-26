番剧自动重命名 · Windows 便携版
================================

【启动】
  双击 start.bat 即可。浏览器访问 http://localhost:5999
  运行时（Python / Node / ffprobe / unrar）已内置，无需预装任何环境。

【首次使用】
  1. 打开网页后进入「设置」页：
     - AI：填写 API Key、Base URL、模型（Pi Case Agent 默认走此凭据链）
     - 路径：填写 Bangumi / 电影 / 动漫目录
     - 通知：按需配置 Emby / Telegram
  2. 配置与所有运行数据保存在与本程序同级的 data\ 目录：
     - data\config.json       配置
     - data\log\BAR.log       日志（报错先看这里）
     - data\record\           任务记录
     - data\cache\metadata\   TMDB/Bangumi 元数据缓存
  3. 凭据也可用环境变量注入：BAR_PI_CASE_AGENT_API_KEY 等（见源码文档）

【端口】默认 5999。如需改端口，改 src\start.py 或用环境变量。

【字幕对齐（可选）】
  默认未启用字幕自动对齐（ffsubsync，依赖 numpy ~70M）。
  需要时手动安装：
    runtime\python-embed\python.exe -m pip install ffsubsync
  然后在 设置 → 字幕 开启 subtitle_sync_enabled。

【qBittorrent webhook】
  webhook 入口：POST http://localhost:5999/sendTask
  路径需能被本机访问；宿主机→容器路径转换配置见 设置 → 通用。

【升级】
  重新下载新版 zip 解压覆盖即可，data\ 目录保留不覆盖（配置与记录不丢）。

【技术说明】
  - 本程序是 AI-first 媒体整理流水线，语义推理由内置 Node.js Pi sidecar 完成。
  - app\ 为程序主体；runtime\ 为运行时；data\ 为数据。三者分离便于升级与备份。
  - 不写注册表、不装服务、不改系统环境变量，纯绿色便携。

【反馈】
  报错请附 data\log\BAR.log 片段。
