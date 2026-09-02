"""配置页字段元数据（纯数据层，驱动 config_page 渲染）。

设计目标：
- 把 ``config_page.py`` 里 500 行 if/elif 控件分支收敛成「元数据驱动渲染」。
- 区分 **常用（用户傻瓜配置）** 与 **高级（开发者/运维）** 两个可见层级：
  普通用户默认只看到常用项，高级项折叠在「高级设置」开关后。
- 每项配一句白话 ``help`` 文本 + 默认值提示，降低术语门槛。

约束：
- 仅描述 ``cm.config`` 中 **现有且已在 config_page 渲染** 的 key，不新增、不丢失。
- 纯数据模块，无 UI 依赖，便于单独测试与回归。
- 控件类型 (control) 取值固定为：
  ``toggle`` / ``select`` / ``ordered_select`` / ``number`` / ``input`` / ``secret`` / ``path``
  其中 ``path`` = 普通 input + 「选择」按钮（local_file_picker）；``secret`` = password input。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..config.config_manager import CN_MAP, TITLE_LANGUAGE_OPTIONS



# --------------------------------------------------------------------------- #
# 控件类型常量
# --------------------------------------------------------------------------- #
TOGGLE = "toggle"
SELECT = "select"
ORDERED_SELECT = "ordered_select"
NUMBER = "number"
INPUT = "input"
SECRET = "secret"  # 密钥脱敏输入框
PATH = "path"  # input + 本地选择按钮


# 可见层级
LEVEL_BASIC = "basic"  # 常用：普通用户必见
LEVEL_ADVANCED = "advanced"  # 高级：开发者/运维，默认折叠

# 场景 Tab（按「用户做什么」分，而非重要程度）
# general=基础与路径 / ai=AI 识别 / subtitle=字幕 / notify=通知 / advanced=高级运维
TAB_GENERAL = "general"
TAB_AI = "ai"
TAB_SUBTITLE = "subtitle"
TAB_NOTIFY = "notify"
TAB_ADVANCED = "advanced"

# Tab 顺序（驱动前端 SettingsTabs 导航顺序）
TAB_ORDER = [
    TAB_GENERAL,
    TAB_AI,
    TAB_SUBTITLE,
    TAB_NOTIFY,
    TAB_ADVANCED,
]


# 分组 key（同时用于常用/高级区的卡片分组顺序）
GRP_PATHS = "媒体库路径"
GRP_RENAME = "重命名标题"
GRP_TRANSFER = "传输与覆盖"
GRP_AI = "AI 识别"
GRP_SUBTITLE_FETCH = "字幕自动抓取"
GRP_SYNC = "字幕对齐（ffsubsync）"
GRP_EMBY = "通知：Emby"
GRP_TELEGRAM = "通知：Telegram"
GRP_SKIP = "Webhook 过滤与分类"
GRP_AI_ADV = "AI 高级路由"
GRP_CASE_AGENT = "Case Agent 运维"
GRP_BGM_TMDB = "BGM→TMDB 产品链路"
GRP_SUBTITLE_AGENT = "字幕/抓取 Case Agent"
GRP_MOVIEPILOT = "moviepilot"
GRP_FETCH_ADV = "抓取高级"
GRP_RUNTIME = "运行时"
GRP_CACHE = "元数据缓存"


# 常用区分组顺序
BASIC_GROUP_ORDER = [
    GRP_PATHS,
    GRP_RENAME,
    GRP_TRANSFER,
    GRP_AI,
    GRP_SUBTITLE_FETCH,
    GRP_SYNC,
    GRP_EMBY,
    GRP_TELEGRAM,
    GRP_SKIP,
]

# 高级区分组顺序
ADVANCED_GROUP_ORDER = [
    GRP_AI_ADV,
    GRP_CASE_AGENT,
    GRP_BGM_TMDB,
    GRP_SUBTITLE_AGENT,
    GRP_MOVIEPILOT,
    GRP_FETCH_ADV,
    GRP_RUNTIME,
    GRP_CACHE,
]

# 分组 → Material icon 名（导航与卡片标题用）
GROUP_ICON = {
    GRP_PATHS: "folder",
    GRP_RENAME: "title",
    GRP_TRANSFER: "swap_horiz",
    GRP_AI: "psychology",
    GRP_SUBTITLE_FETCH: "subtitles",
    GRP_SYNC: "sync_alt",
    GRP_EMBY: "cast",
    GRP_TELEGRAM: "send",
    GRP_SKIP: "block",
    GRP_AI_ADV: "route",
    GRP_CASE_AGENT: "verified_user",
    GRP_BGM_TMDB: "compare_arrows",
    GRP_SUBTITLE_AGENT: "rule",
    GRP_MOVIEPILOT: "hub",
    GRP_FETCH_ADV: "tune",
    GRP_RUNTIME: "settings",
    GRP_CACHE: "cached",
}


# --------------------------------------------------------------------------- #
# 字段规格
# --------------------------------------------------------------------------- #
# 每个 entry：
#   key:        配置 key（与 CONFIG_DEFAULT 对齐）
#   control:    控件类型
#   level:      basic / advanced
#   group:      所属分组
#   options:    toggle/select 的可选值列表（toggle 为中文/枚举标签，即直接写入配置的值）
#   min/step:   number 控件约束（max 可选）
#   placeholder: input 占位提示（可选）
#   help:       白话说明
#   default_hint: 默认值的人话提示（可选，用于在标签旁/tooltip 展示）
# --------------------------------------------------------------------------- #
FIELD_SPEC: list[Mapping[str, Any]] = [
    # ============================ 媒体库路径 ============================ #
    {
        "key": "api_key",
        "control": SECRET,
        "level": LEVEL_BASIC,
        "group": GRP_PATHS,
        "tab": TAB_GENERAL,
        "help": "TMDB 的 API 密钥，用于获取剧集/电影的元数据。在 themoviedb.org 注册后申请。",
        "default_hint": "必填",
    },
    {
        "key": "tv_path",
        "control": PATH,
        "level": LEVEL_BASIC,
        "group": GRP_PATHS,
        "tab": TAB_GENERAL,
        "subgroup": "输出路径",
        "help": "整理后电视剧 TV 正片的落盘根目录。",
    },
    {
        "key": "anime_path",
        "control": PATH,
        "level": LEVEL_BASIC,
        "group": GRP_PATHS,
        "tab": TAB_GENERAL,
        "subgroup": "输出路径",
        "help": "整理后动漫 TV 正片的落盘根目录。",
    },
    {
        "key": "movie_path",
        "control": PATH,
        "level": LEVEL_BASIC,
        "group": GRP_PATHS,
        "tab": TAB_GENERAL,
        "subgroup": "输出路径",
        "help": "整理后电影的落盘根目录。",
    },
    {
        "key": "anime_movie_path",
        "control": PATH,
        "level": LEVEL_BASIC,
        "group": GRP_PATHS,
        "tab": TAB_GENERAL,
        "subgroup": "输出路径",
        "help": "整理后动漫电影（剧场版）的落盘根目录。",
    },
    {
        "key": "rename_output_title_language_order",
        "control": ORDERED_SELECT,
        "level": LEVEL_BASIC,
        "group": GRP_RENAME,
        "tab": TAB_GENERAL,
        "options": list(TITLE_LANGUAGE_OPTIONS),
        "help": "选择最终目录名和文件名的标题语言；可多选，按选择顺序作为优先级。自动判断保持当前行为。",
        "default_hint": "默认 自动判断",
    },
    # ============================ 传输与覆盖 ============================ #
    {
        "key": "mode",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_TRANSFER,
        "tab": TAB_GENERAL,
        "options": ["链接", "复制", "剪切"],
        "help": "文件落地方式。「链接」= 硬链接，不占额外空间、源删则失效，需同盘；跨盘请改「复制」。",
        "default_hint": "默认 链接（硬链接）",
    },
    {
        "key": "overwrite_existing",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_TRANSFER,
        "tab": TAB_GENERAL,
        "options": ["覆盖", "跳过"],
        "help": "目标文件已存在时：「覆盖」删除旧文件重新落地；「跳过」保留已存在、继续处理其他。",
        "default_hint": "默认 跳过",
    },
    {
        "key": "hardlink_fallback_to_symlink",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_TRANSFER,
        "tab": TAB_ADVANCED,
        "options": ["启用", "禁用"],
        "help": "链接模式下硬链接失败（如跨文件系统）时是否降级为软链接。禁用则记为部分失败、不静默降级。",
        "default_hint": "默认 启用",
        "bool_toggle": True,
    },
    # ============================ AI 识别 ============================ #
    {
        "key": "ai_api_key",
        "control": SECRET,
        "level": LEVEL_BASIC,
        "group": GRP_AI,
        "tab": TAB_AI,
        "help": "OpenAI（或兼容接口）的 API 密钥，驱动标题提取、候选选择、Case Agent 等 AI 能力。",
        "default_hint": "必填（启用 AI 识别时）",
    },
    {
        "key": "ai_base_url",
        "control": INPUT,
        "level": LEVEL_BASIC,
        "group": GRP_AI,
        "tab": TAB_AI,
        "help": "模型网关根地址（全局唯一，不要带 /v1）。Pi sidecar 会按所选协议调用该地址。",
        "default_hint": "默认 https://api.openai.com（无 /v1）",
    },
    {
        "key": "ai_model",
        "control": INPUT,
        "level": LEVEL_BASIC,
        "group": GRP_AI,
        "tab": TAB_AI,
        "help": "调用的模型名，需与上方接口地址匹配。",
        "default_hint": "默认 gpt-4o-mini",
    },
    {
        "key": "openai_api_interface",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_AI_ADV,
        "tab": TAB_AI,
        "options": ["responses_api", "chat_completions"],
        "help": "Pi Case Agent 使用的模型协议。responses_api / chat_completions：OpenAI 兼容网关。改后需重启进程并点「测试 AI」。",
        "default_hint": "默认 responses_api",
    },
    # ============================ 字幕自动抓取 ============================ #
    {
        "key": "subtitle_auto_fetch_enabled",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_SUBTITLE_FETCH,
        "tab": TAB_SUBTITLE,
        "options": ["启用", "禁用"],
        "help": "任务重命名完成后，自动为缺字幕的落地视频抓取字幕。",
        "default_hint": "默认 禁用",
        "bool_toggle": True,
    },
    {
        "key": "subtitle_auto_fetch_preferred_language",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_SUBTITLE_FETCH,
        "tab": TAB_SUBTITLE,
        "options": ["zh-CN", "zh-TW"],
        "help": "优先抓取的字幕语言：简体或繁体。",
        "default_hint": "默认 zh-CN",
    },
    {
        "key": "subtitle_auto_fetch_skip_if_embedded_language",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_SUBTITLE_FETCH,
        "tab": TAB_SUBTITLE,
        "options": ["启用", "禁用"],
        "help": "视频内嵌字幕轨已含优先语言时跳过抓取（ffprobe 探轨；探轨失败回退外挂判定）。",
        "default_hint": "默认 启用",
        "bool_toggle": True,
    },
    {
        "key": "subtitle_auto_fetch_provider",
        "control": SELECT,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "options": ["acgrip", "moviepilot", "acgrip_moviepilot"],
        "help": "字幕抓取源。组合模式并行搜索 ACGRIP 与 MoviePilot，候选仍由 Case Agent 选择。",
        "default_hint": "默认 acgrip",
    },
    {
        "key": "subtitle_auto_fetch_candidate_limit",
        "control": NUMBER,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "min": 1,
        "max": 50,
        "step": 1,
        "help": "每个搜索词、每个字幕来源最多返回的候选数量。",
        "default_hint": "默认 10",
    },
    {
        "key": "subtitle_auto_fetch_timeout_seconds",
        "control": NUMBER,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "min": 5,
        "step": 1,
        "help": "抓取 Case Agent 单次执行的超时秒数。多季大样本建议调大。",
        "default_hint": "默认 30",
    },
    {
        "key": "subtitle_auto_fetch_browser_enabled",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "options": ["启用", "禁用"],
        "help": "是否启用动态浏览器抓取（需要额浏览器依赖）。",
        "default_hint": "默认 禁用",
        "bool_toggle": True,
    },
    {
        "key": "subtitle_auto_fetch_acgrip_base_url",
        "control": INPUT,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "help": "ACGRIP 站点地址，仅在使用镜像/反代时修改。",
        "default_hint": "默认 https://bbs.acgrip.com",
    },
    {
        "key": "subtitle_auto_fetch_moviepilot_save_path",
        "control": INPUT,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "help": "MoviePilot 可写且 BAR 可见的专用字幕暂存根目录。必须位于 MoviePilot 允许的下载目录内。",
        "default_hint": "示例 H:/Subtitle Staging",
    },
    {
        "key": "subtitle_auto_fetch_use_ai_rerank",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "options": ["启用", "禁用"],
        "help": "是否用 AI 对抓取候选重新排序。",
        "default_hint": "默认 启用",
        "bool_toggle": True,
    },
    {
        "key": "subtitle_auto_fetch_search_mode",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "options": ["auto"],
        "help": "字幕搜索模式。当前仅 auto。",
        "default_hint": "默认 auto",
    },
    {
        "key": "subtitle_auto_fetch_save_reason",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_FETCH_ADV,
        "tab": TAB_SUBTITLE,
        "options": ["启用", "禁用"],
        "help": "是否保存 AI 重排的决策原因，便于审计。",
        "default_hint": "默认 启用",
        "bool_toggle": True,
    },
    # ============================ 字幕对齐 ============================ #
    {
        "key": "subtitle_sync_enabled",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_SYNC,
        "tab": TAB_SUBTITLE,
        "options": ["启用", "禁用"],
        "help": "用 ffsubsync 把字幕时间轴对齐到视频。需本机安装 ffsubsync。",
        "default_hint": "默认 禁用",
        "bool_toggle": True,
    },
    {
        "key": "subtitle_sync_mode",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_SYNC,
        "tab": TAB_SUBTITLE,
        "options": ["best_effort", "strict"],
        "help": "best_effort 对齐失败时回退原字幕；strict 对齐失败视为失败。",
        "default_hint": "默认 best_effort",
    },
    {
        "key": "subtitle_sync_executable",
        "control": PATH,
        "level": LEVEL_ADVANCED,
        "group": GRP_SYNC,
        "tab": TAB_SUBTITLE,
        "select_mode": "file",
        "help": "ffsubsync 可执行文件名或完整路径。",
        "default_hint": "默认 ffsubsync",
    },
    {
        "key": "subtitle_sync_extra_args",
        "control": INPUT,
        "level": LEVEL_ADVANCED,
        "group": GRP_SYNC,
        "tab": TAB_SUBTITLE,
        "help": "传给 ffsubsync 的额外命令行参数。",
        "default_hint": "默认 空",
    },
    {
        "key": "subtitle_sync_timeout_seconds",
        "control": NUMBER,
        "level": LEVEL_ADVANCED,
        "group": GRP_SYNC,
        "tab": TAB_SUBTITLE,
        "min": 10,
        "step": 1,
        "help": "单条字幕对齐的超时秒数。",
        "default_hint": "默认 120",
    },
    {
        "key": "subtitle_sync_overwrite_policy",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_SYNC,
        "tab": TAB_SUBTITLE,
        "options": ["follow_global", "overwrite", "skip"],
        "help": "对齐产物覆盖策略：follow_global 跟随全局「已存在策略」，overwrite 强制覆盖，skip 跳过已存在。",
        "default_hint": "默认 follow_global",
    },
    # ============================ 通知：Emby ============================ #
    {
        "key": "emby_enabled",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_EMBY,
        "tab": TAB_NOTIFY,
        "options": ["启用", "禁用"],
        "help": "批次结束后通知 Emby 刷新媒体库。",
        "default_hint": "默认 禁用",
        "bool_toggle": True,
    },
    {
        "key": "emby_host",
        "control": INPUT,
        "level": LEVEL_BASIC,
        "group": GRP_EMBY,
        "tab": TAB_NOTIFY,
        "help": "Emby 服务器地址，含协议与端口。",
        "default_hint": "默认 http://localhost:8096",
    },
    {
        "key": "emby_api_key",
        "control": SECRET,
        "level": LEVEL_BASIC,
        "group": GRP_EMBY,
        "tab": TAB_NOTIFY,
        "help": "Emby 的 API 密钥，在 Emby 后台「高级 → API 密钥」生成。",
        "default_hint": "启用 Emby 时必填",
    },
    # ============================ 通知：Telegram ============================ #
    {
        "key": "telegram_enabled",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_TELEGRAM,
        "tab": TAB_NOTIFY,
        "options": ["启用", "禁用"],
        "help": "批次结束后发送 Telegram 汇总通知。",
        "default_hint": "默认 禁用",
        "bool_toggle": True,
    },
    {
        "key": "telegram_bot_token",
        "control": SECRET,
        "level": LEVEL_BASIC,
        "group": GRP_TELEGRAM,
        "tab": TAB_NOTIFY,
        "help": "Telegram Bot 的 Token，从 @BotFather 获取。",
        "default_hint": "启用 Telegram 时必填",
    },
    {
        "key": "telegram_chat_id",
        "control": INPUT,
        "level": LEVEL_BASIC,
        "group": GRP_TELEGRAM,
        "tab": TAB_NOTIFY,
        "help": "接收通知的会话 ID（个人/群组）。",
        "default_hint": "启用 Telegram 时必填",
    },
    {
        "key": "telegram_notify_on_success",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_TELEGRAM,
        "tab": TAB_NOTIFY,
        "options": ["启用", "禁用"],
        "help": "批次有成功任务时发送通知。",
        "default_hint": "默认 启用",
        "bool_toggle": True,
    },
    {
        "key": "telegram_notify_on_failure",
        "control": TOGGLE,
        "level": LEVEL_BASIC,
        "group": GRP_TELEGRAM,
        "tab": TAB_NOTIFY,
        "options": ["启用", "禁用"],
        "help": "批次有失败任务时发送通知。",
        "default_hint": "默认 启用",
        "bool_toggle": True,
    },
    {
        "key": "telegram_base_url",
        "control": INPUT,
        "level": LEVEL_ADVANCED,
        "group": GRP_TELEGRAM,
        "tab": TAB_NOTIFY,
        "help": "Telegram Bot API 地址，使用反代时修改。",
        "default_hint": "默认 https://api.telegram.org",
    },
    # ============================ Webhook 过滤与分类 ============================ #
    {
        "key": "allowed_categories",
        "control": INPUT,
        "level": LEVEL_BASIC,
        "group": GRP_SKIP,
        "tab": TAB_GENERAL,
        "help": "仅影响 qBittorrent webhook；留空不限制。填写后必须通过 category=%L 命中至少一个精确分类才会入队，no_process 和跳过标签优先。",
        "default_hint": "留空=不限制；示例 动漫,电影,tv",
    },
    {
        "key": "skip_tags",
        "control": INPUT,
        "level": LEVEL_BASIC,
        "group": GRP_SKIP,
        "tab": TAB_GENERAL,
        "help": "qBittorrent webhook 带有这些标签的任务将被跳过不处理，逗号分隔。",
        "default_hint": "默认 iyuu,辅种,reseed,skip,no_process",
    },
    # ============================ BGM→TMDB 产品链路 ============================ #
    {
        "key": "rename_bgm_to_tmdb_product_pipeline_enabled",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_BGM_TMDB,
        "tab": TAB_ADVANCED,
        "options": ["启用", "禁用"],
        "help": "启用 BGM→TMDB 产品桥接链路（Local→Bangumi→TMDB 全链路核心）。",
        "default_hint": "默认 启用",
        "bool_toggle": True,
    },
    {
        "key": "rename_bgm_to_tmdb_execute_enabled",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_BGM_TMDB,
        "tab": TAB_ADVANCED,
        "options": ["启用", "禁用"],
        "help": "是否真正执行 BGM→TMDB 迁移落盘；关闭则只规划不落盘。",
        "default_hint": "默认 启用",
        "bool_toggle": True,
    },
    {
        "key": "rename_bgm_external_hints_mode",
        "control": SELECT,
        "level": LEVEL_ADVANCED,
        "group": GRP_BGM_TMDB,
        "tab": TAB_ADVANCED,
        "options": ["off", "shadow", "assist"],
        "help": "使用 Fribb Anime Lists / BangumiExtLinker 作为 BGM→TMDB 候选召回证据。off=关闭；shadow=只记录命中；assist=将候选提示提供给 Pi。不会绕过 TMDB legal graph 或 verifier。",
        "default_hint": "默认 off",
    },
    {
        "key": "rename_bgm_extlinker_snapshot_path",
        "control": PATH,
        "level": LEVEL_ADVANCED,
        "group": GRP_BGM_TMDB,
        "tab": TAB_ADVANCED,
        "select_mode": "file",
        "help": "BangumiExtLinker 的 anime_map.json 本地快照路径。只读加载，记录 SHA-256 revision；留空表示不使用。",
        "default_hint": "默认 空",
    },
    {
        "key": "rename_bgm_fribb_snapshot_path",
        "control": PATH,
        "level": LEVEL_ADVANCED,
        "group": GRP_BGM_TMDB,
        "tab": TAB_ADVANCED,
        "select_mode": "file",
        "help": "Fribb anime-list-full.json 本地快照路径。通过 ExtLinker 的 AniDB ID 关联；season/offset 仅是提示。留空表示不使用。",
        "default_hint": "默认 空",
    },
    # ============================ MoviePilot ============================ #
    {
        "key": "moviepilot_base_url",
        "control": INPUT,
        "level": LEVEL_ADVANCED,
        "group": GRP_MOVIEPILOT,
        "tab": TAB_ADVANCED,
        "help": "MoviePilot API 地址。Docker 部署通常使用 host.docker.internal 访问宿主机。",
        "default_hint": "默认 http://host.docker.internal:3333",
    },
    {
        "key": "moviepilot_api_token",
        "control": SECRET,
        "level": LEVEL_ADVANCED,
        "group": GRP_MOVIEPILOT,
        "tab": TAB_ADVANCED,
        "help": "MoviePilot API_TOKEN 具备管理员权限；供字幕与漏单恢复共用，仅服务端保存。",
    },
    # ============================ 运行时 ============================ #
    {
        "key": "log_level",
        "control": TOGGLE,
        "level": LEVEL_ADVANCED,
        "group": GRP_RUNTIME,
        "tab": TAB_ADVANCED,
        "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
        "help": "日志等级，影响 data/log/BAR.log 的输出详细度。",
        "default_hint": "默认 INFO",
    },
    {
        "key": "queue_max_workers",
        "control": NUMBER,
        "level": LEVEL_ADVANCED,
        "group": GRP_RUNTIME,
        "tab": TAB_ADVANCED,
        "min": 1,
        "step": 1,
        "help": "队列并行处理数。过高会放大 AI/网络并发压力，建议 1-5。",
        "default_hint": "默认 1",
    },
    {
        "key": "docker_mnt",
        "control": INPUT,
        "level": LEVEL_ADVANCED,
        "group": GRP_RUNTIME,
        "tab": TAB_ADVANCED,
        "help": "Docker 容器内的媒体挂载根路径。与「宿主机路径前缀」配合：webhook 收到宿主机路径后，去掉前缀、拼到此根下，得到容器内可访问路径。例如前缀 H:\\ + 此处 /media → H:\\Emby\\X 转成 /media/Emby/X。非 Docker / 原生运行留空即可。",
        "default_hint": "默认 /media",
    },
    {
        "key": "host_path_prefix",
        "control": INPUT,
        "level": LEVEL_ADVANCED,
        "group": GRP_RUNTIME,
        "tab": TAB_ADVANCED,
        "help": "qBittorrent 所在宿主机的路径前缀，用于 webhook 路径转换（Windows 为主）。留空 = 不转换，webhook 路径原样使用（适合 qB 与本程序同机同路径的裸机部署）。Docker 部署填 qB 实际 save path 的盘符根，如 H:\\；盘符大小写无关，H:\\ 与 h:\\ 等价。",
        "default_hint": "默认 空",
    },
    # ============================ 元数据缓存 ============================ #
    {
        "key": "metadata_cache_mode",
        "control": SELECT,
        "level": LEVEL_ADVANCED,
        "group": GRP_CACHE,
        "tab": TAB_ADVANCED,
        "options": ["read-write", "cache-only", "refresh", "off"],
        "help": "TMDB/Bangumi API 元数据缓存模式。read-write=命中取缓存否则拉取并写；cache-only=只读缓存不拉网（miss 抛错，适合离线回归）；refresh=强制重拉；off=完全不缓存。改后重启生效。",
        "default_hint": "默认 read-write",
    },
    {
        "key": "metadata_cache_ttl_days",
        "control": NUMBER,
        "level": LEVEL_ADVANCED,
        "group": GRP_CACHE,
        "tab": TAB_ADVANCED,
        "min": 1,
        "step": 1,
        "help": "正向结果（有数据的 API 响应）缓存天数。改后对新写入条目生效，已有条目按原 TTL。",
        "default_hint": "默认 30",
    },
    {
        "key": "metadata_cache_negative_ttl_hours",
        "control": NUMBER,
        "level": LEVEL_ADVANCED,
        "group": GRP_CACHE,
        "tab": TAB_ADVANCED,
        "min": 1,
        "step": 1,
        "help": "空结果（[] / {}）缓存小时数，避免短时间内反复打空查询。",
        "default_hint": "默认 6",
    },
    {
        "key": "metadata_cache_max_size_mb",
        "control": NUMBER,
        "level": LEVEL_ADVANCED,
        "group": GRP_CACHE,
        "tab": TAB_ADVANCED,
        "min": 1,
        "step": 1,
        "help": "缓存磁盘上限（MB）。批次收尾/启动时若超限按 LRU 淘汰旧条目。改后重启生效（size_limit 在打开缓存时确定）。",
        "default_hint": "默认 500",
    },
]


# --------------------------------------------------------------------------- #
# 查询辅助
# --------------------------------------------------------------------------- #
def spec_by_key() -> dict[str, Mapping[str, Any]]:
    """返回 key -> spec 映射，便于渲染层与测试快速查找。"""
    return {entry["key"]: entry for entry in FIELD_SPEC}


def keys_for_level(level: str) -> list[str]:
    """返回指定层级的所有 key（保持 FIELD_SPEC 顺序）。"""
    return [e["key"] for e in FIELD_SPEC if e["level"] == level]


def entries_for_level_grouped(level: str) -> dict[str, list[Mapping[str, Any]]]:
    """按分组聚合指定层级的字段，返回 {group: [spec, ...]}，分组按预设顺序。"""
    order = BASIC_GROUP_ORDER if level == LEVEL_BASIC else ADVANCED_GROUP_ORDER
    grouped: dict[str, list[Mapping[str, Any]]] = {g: [] for g in order}
    for entry in FIELD_SPEC:
        if entry["level"] != level:
            continue
        g = entry["group"]
        grouped.setdefault(g, []).append(entry)
    # 过滤空分组
    return {g: items for g, items in grouped.items() if items}


def tab_for_key(key: str) -> str | None:
    """返回某 key 所属的场景 Tab（无则 None）。"""
    for entry in FIELD_SPEC:
        if entry["key"] == key:
            return entry.get("tab")
    return None


def get_field_spec_with_labels() -> list[dict[str, Any]]:
    """Return a copy of structural field metadata.

    Wording, help text and option labels are intentionally resolved by the
    frontend locale dictionaries, never by this backend serializer.
    """
    out: list[dict[str, Any]] = []
    for entry in FIELD_SPEC:
        item = dict(entry)
        item.setdefault("label", CN_MAP.get(entry["key"]) or entry["key"])
        out.append(item)
    return out


def groups_for_tab(tab: str) -> list[str]:
    """返回某 Tab 下出现的 group 列表（保持 FIELD_SPEC 顺序去重）。"""
    seen: list[str] = []
    for entry in FIELD_SPEC:
        if entry.get("tab") != tab:
            continue
        g = entry["group"]
        if g not in seen:
            seen.append(g)
    return seen


def entries_for_tab_grouped(tab: str) -> dict[str, list[Mapping[str, Any]]]:
    """按场景 Tab 聚合字段，返回 {group: [spec, ...]}，group 顺序按该 Tab 内首次出现顺序。"""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for entry in FIELD_SPEC:
        if entry.get("tab") != tab:
            continue
        g = entry["group"]
        grouped.setdefault(g, []).append(entry)
    return grouped


def covered_keys() -> set[str]:
    """FIELD_SPEC 覆盖的全部 key（用于与 config_page 渲染清单对齐校验）。"""
    return {e["key"] for e in FIELD_SPEC}
