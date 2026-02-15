# Bangumi Auto Rename - 自动重命名逻辑详解

本文档详细描述了 Bangumi Auto Rename 的完整自动重命名处理流程。

## 目录

1. [整体架构](#整体架构)
2. [任务触发](#任务触发)
3. [文件名清洗与解析](#文件名清洗与解析)
4. [TMDB 搜索逻辑](#tmdb-搜索逻辑)
5. [类型判断（电影 vs 剧集）](#类型判断)
6. [季度与集数识别](#季度与集数识别)
7. [AI 增强功能](#ai-增强功能)
8. [文件传输与映射](#文件传输与映射)
9. [数据模型](#数据模型)

---

## 整体架构

```
用户输入（Web UI / qBittorrent Webhook）
           ↓
    FastAPI 路由 (/sendTask)
           ↓
    Rename.process() [src/rename/process.py]
           ↓
    ┌──────┴──────┐
    │  文件名清洗  │ [src/rename/cleaner.py]
    └──────┬──────┘
           ↓
    ┌──────┴──────┐
    │ TMDB 元数据  │ [src/rename/get_info.py]
    └──────┬──────┘
           ↓
    ┌──────┴──────┐
    │  类型判断    │ (电影/剧集/动漫)
    └──────┬──────┘
           ↓
    ┌──────┴──────┐
    │ 季度/集数识别│ (传统正则 或 AI分析)
    └──────┬──────┘
           ↓
    ┌──────┴──────┐
    │  文件映射    │ 源路径 → 目标路径
    └──────┬──────┘
           ↓
    Trans.trans_file() [src/rename/trans.py]
           ↓
    文件操作（硬链接/复制/移动）
```

---

## 任务触发

### 1. qBittorrent Webhook 触发

**入口**: `src/web.py` - `/sendTask` 端点

```python
@app.get("/sendTask")
async def send_task(path: str = Query(...)):
    """
    接收 qBittorrent 完成下载后的回调
    参数: path - 下载完成的文件/目录路径
    """
```

qBittorrent 配置示例：
```
完成时运行命令: curl "http://localhost:5999/sendTask?path=%F"
```

### 2. Web UI 手动触发

**入口**: `src/pages/task.py`

用户可通过 Web 界面：
- 输入路径手动添加任务
- 指定是否为动漫 (`is_anime`)
- 指定是否为电影 (`is_movie`)
- 自定义搜索名称 (`cus_name`)
- 自定义季度号 (`cus_season_id`)

---

## 文件名清洗与解析

**核心模块**: `src/rename/cleaner.py`

### 处理流程

```python
# Step 1: 移除标签（字幕组、分辨率、编码等）
rtpath_name = remove_tag(path.name)

# Step 2: 处理多点分隔的规范命名
if rtpath_name.count('.') >= 3:
    rtpath_name = ' '.join(rtpath_name.split('.'))
    rtpath_name, year = divide_by_year(rtpath_name)

# Step 3: 移除季度和集数信息
rtpath_name = remove_season(rtpath_name)
rtpath_name = remove_episode(rtpath_name)
```

### 关键函数

| 函数 | 功能 | 示例 |
|------|------|------|
| `remove_tag()` | 移除方括号标签 | `[LoliHouse] 葬送的芙莉莲` → `葬送的芙莉莲` |
| `remove_season()` | 移除季度标识 | `第二季` / `S02` / `Season 2` |
| `remove_episode()` | 移除集数标识 | `第01话` / `E01` / `[01]` |
| `divide_by_year()` | 按年份分割 | `Anime.2024.S01` → `(Anime, 2024)` |
| `extract_season()` | 提取季度号 | `S02` → `2` |
| `extract_number()` | 提取集数 | `[12]` → `12` |

### 常见标签模式

```python
# src/rename/utils.py

# 特典/OVA 标签（放入 Season0）
S0_TAG = ['ova', 'oad', 'sp', 'special', '特典', 'extra', 'bonus']

# 额外内容标签（放入 extra 目录）
EXTRA_TAG = ['ncop', 'nced', 'pv', 'cm', 'menu', 'preview', 'trailer']

# 忽略的文件后缀
IGNORE_SUFFIX = ['.txt', '.nfo', '.jpg', '.png', '.ass', '.srt']

# 视频文件后缀
VIDEO_SUFFIX = ['.mkv', '.mp4', '.avi', '.wmv', '.flv', '.mov', '.m2ts']
```

---

## TMDB 搜索逻辑

**核心模块**: `src/rename/get_info.py`

### 搜索流程

```python
class Search:
    def get_tv_info(self, query: str, year: int):
        """搜索电视剧信息"""
        search = tmdb.Search()
        search.tv(query=query, language='zh-CN', first_air_date_year=year)
        # 返回: (name, tv_info_dict)

    def get_movie_info(self, query: str, year: int):
        """搜索电影信息"""
        search = tmdb.Search()
        search.movie(query=query, language='zh-CN', year=year)
        # 返回: (name, movie_info_dict)

    def fill_season_info(self, tv_info: Dict):
        """填充详细的季度和剧集信息"""
        for season in tv_info['seasons']:
            detailed = self.get_season_info(tv_id, season_number)
            season.update(detailed)  # 包含每集的详细信息
```

### 搜索优先级

1. 使用清洗后的文件名搜索
2. 如果有年份信息，优先带年份搜索
3. 如果搜索失败，移除年份重试
4. 如果都失败，尝试 AI 提取标题后重试

---

## 类型判断

**位置**: `src/rename/process.py` - `check_task_type()`

### 判断逻辑

```python
def check_task_type(self, _uuid, rtpath_name, year, path, is_anime, is_movie):
    # 同时搜索电视剧和电影
    s1_name, s1_info = self.search.get_tv_info(rtpath_name, year)
    s2_name, s2_info = self.search.get_movie_info(rtpath_name, year)

    # 计算评分
    pos = 0
    if s1_name:
        pos += 1  # 电视剧搜到了
    elif s2_name:
        pos -= 1  # 电影搜到了

    season_id = extract_season(rtpath_name)
    if season_id == -1:
        pos -= 0.6  # 无季度信息，可能是电影
        if path.is_file():
            pos -= 0.5  # 单文件更可能是电影
    else:
        pos += 0.6  # 有季度信息，可能是剧集

    if path.is_dir():
        file_count = len([f for f in path.iterdir() if f.is_file()])
        if file_count > 6:
            pos += 0.4  # 文件多，可能是剧集
        else:
            pos -= 0.4  # 文件少，可能是电影

    # pos > 0 判断为剧集，pos <= 0 判断为电影
```

### AI 辅助类型判断

当 TMDB 搜索都失败时，使用 AI 提取标题和类型：

```python
if not s1_name and not s2_name:
    ai_result = ai_client.extract_title_and_type(path.name)
    # 返回: (title, "movie" | "tv")

    # 类型判断依据:
    # - "劇場版"、"剧场版"、"MOVIE" → movie
    # - "OVA"、"OAD" → tv (特典)
    # - 季度信息 (S01、第一季) → tv
    # - 集数格式 (E01、第01话) → tv
```

### 动漫判断

```python
# 根据 TMDB 的 genres 判断
for genre in info['genres']:
    if genre['name'].lower() in ['animation', 'anime']:
        is_anime = True
        break
```

---

## 季度与集数识别

### 传统正则方法

**位置**: `src/rename/process.py` - `get_season_id()`, `process_sub()`

#### 季度识别

```python
def get_season_id(self, tv_info, work_path, path, titles):
    # 1. 从路径名提取季度号
    int_rtpath_name = extract_season(path_name)
    if info_season_id == int_rtpath_name:
        return int_rtpath_name

    # 2. 检查季度名称是否在路径中
    if sname in path.name:
        return info_season_id

    # 3. 计算相似度匹配
    for title in titles:
        similarity = SequenceMatcher(None, sname, path_name).ratio()
        similaritys[similarity] = season_id

    return to_sim_max(all_similaritys)
```

#### 集数识别

```python
def process_sub(self, ...):
    # 1. 检查特殊标签
    for s0 in S0_TAG:  # OVA, OAD, SP...
        if s0 in item_name:
            → Season0/

    for ex in EXTRA_TAG:  # NCOP, NCED, PV...
        if ex in item_name:
            → extra/

    # 2. 提取集数
    ep = extract_base_num(item_name)  # 优先提取基础数字
    if ep is None:
        ep = extract_number(item_name)  # 备用提取方法

    # 3. 检查 S01E01 格式
    _idata = match_and_extract(item_name)  # 匹配 S\d+E\d+ 格式
    if _idata:
        season_id, ep = _idata

    # 4. 生成目标文件名
    target = f'S{ss}E{ep} - {item_name}'
```

### AI 增强方法

**位置**: `src/rename/ai_processor.py`

```python
class AIProcessor:
    def analyze_anime_files(self, path: Path, tv_info: Dict):
        """使用 AI 分析动漫文件与 TMDB 的映射关系"""

        # 1. 收集本地文件信息（包括时长）
        local_files = VideoAnalyzer.analyze_video_files(path, video_files)

        # 2. 调用 AI 分析
        result = self.ai_client.analyze_episode_mapping(tv_info, local_files)

        # 返回 AIAnalysisResult 包含:
        # - season_mapping: 目录到TMDB季度的映射
        # - file_mapping: 文件到具体集数的映射
        # - confidence: 置信度
```

---

## AI 增强功能

**核心模块**: `src/ai/`

### AI 客户端架构

```python
# src/ai/client.py - 工厂模式
class AIClient:
    def __init__(self):
        if self.provider == "gemini":
            self._client = GeminiClient()
        else:
            self._client = OpenAIClient()
```

### 功能一：标题提取

```python
def extract_title_and_type(self, filename: str):
    """
    从复杂文件名提取标题和类型

    输入: [LoliHouse] 葬送的芙莉莲 / Sousou no Frieren [01-28][WebRip 1080p]
    输出: ("葬送的芙莉莲", "tv")

    输入: [AI-Raws][劇場版 空の境界][MOVIE 01-09][BDRip]
    输出: ("空の境界", "movie")
    """
```

### 功能二：剧集映射分析

```python
def analyze_episode_mapping(self, anime_info: Dict, local_files: List[Dict]):
    """
    分析本地文件与 TMDB 剧集的映射关系

    输入:
    - anime_info: TMDB 动漫信息（包含季度和剧集列表）
    - local_files: 本地文件列表（包含路径和时长）

    输出: AIAnalysisResult
    - confidence: "High" | "Medium" | "Low"
    - season_mapping: 目录到TMDB季度的映射
    - file_mapping: 每个文件的季集信息
    """
```

### 功能三：电影合集分析

```python
def analyze_movie_collection(self, folder_name: str, local_files: List[Dict]):
    """
    分析电影合集目录

    输入:
    - folder_name: "[AI-Raws][劇場版 空の境界][MOVIE 01-09][BDRip]"
    - local_files: 目录中的视频文件列表

    输出: MovieCollectionResult
    - is_collection: 是否为电影合集
    - collection_name: 合集名称（如 "空の境界"）
    - file_mapping: 每个文件对应的电影信息
        - file_path: 相对路径
        - movie_title: 电影标题（用于TMDB搜索）
        - movie_number: 系列编号
        - year: 年份
    """
```

### AI 置信度阈值

```python
# 配置项: ai_confidence_threshold = "High" | "Medium" | "Low"

# 判断逻辑
if threshold == "High" and result.confidence == "High":
    use_ai = True
elif threshold == "Medium" and result.confidence in ["High", "Medium"]:
    use_ai = True
elif threshold == "Low":
    use_ai = True

# 如果置信度不足，回退到传统正则方法
```

---

## 文件传输与映射

**核心模块**: `src/rename/trans.py`

### 传输模式

```python
# 配置项: mode = "链接" | "复制" | "移动"

class Trans:
    def trans_file(self):
        for src, dst in self.R.items():
            dst.parent.mkdir(parents=True, exist_ok=True)

            if self.mode == "链接":
                os.link(src, dst)  # 硬链接（推荐）
            elif self.mode == "复制":
                shutil.copy2(src, dst)
            elif self.mode == "移动":
                shutil.move(src, dst)
```

### 目标路径结构

#### 剧集（动漫）
```
{ANIME_PATH}/{name} ({year})/
├── Season1/
│   ├── S01E01 - [原文件名].mkv
│   ├── S01E02 - [原文件名].mkv
│   └── ...
├── Season2/
│   └── ...
├── Season0/        # 特典/OVA
│   └── ...
└── extra/          # NCOP/NCED/PV 等
    └── ...
```

#### 剧集（非动漫）
```
{BANGUMI_PATH}/{name} ({year})/
└── (同上结构)
```

#### 电影
```
{MOVIE_PATH}/{name} ({year})/
└── {name} - [原文件名].mkv
```

#### 动漫电影
```
{ANIME_MOVIE_PATH}/{name} ({year})/
└── {name} - [原文件名].mkv
```

#### 电影合集
```
{ANIME_MOVIE_PATH}/
├── {电影1标题} ({年份})/
│   └── {电影1标题} - [原文件名].mkv
├── {电影2标题} ({年份})/
│   └── ...
└── {合集名}/extra/
    └── [未识别的额外文件]
```

---

## 数据模型

**位置**: `src/ai/models.py`

### 剧集映射模型

```python
class SeasonMapping(BaseModel):
    """季度映射"""
    local_group_name: str  # 本地目录名
    maps_to_tmdb_seasons: List[int]  # 对应的TMDB季度列表

class EpisodeMapping(BaseModel):
    """单个剧集映射"""
    file_path: str  # 相对路径
    tmdb_season: int  # TMDB季号
    tmdb_episode: int  # TMDB集号
    episode_type: Literal["regular", "special", "movie"]
    confidence: Literal["High", "Medium", "Low"]

class AIAnalysisResult(BaseModel):
    """AI分析结果"""
    confidence: Literal["High", "Medium", "Low"]
    reason: str  # 分析理由
    season_mapping: List[SeasonMapping]
    file_mapping: List[EpisodeMapping]
    extra_notes: Optional[str]
```

### 电影合集模型

```python
class MovieFileMapping(BaseModel):
    """电影文件映射"""
    file_path: str  # 相对路径
    movie_title: str  # 电影标题（用于TMDB搜索）
    movie_number: Optional[int]  # 系列编号
    year: Optional[int]  # 年份
    confidence: Literal["High", "Medium", "Low"]

class MovieCollectionResult(BaseModel):
    """电影合集分析结果"""
    is_collection: bool  # 是否为合集
    collection_name: str  # 合集名称
    confidence: Literal["High", "Medium", "Low"]
    reason: str  # 分析理由
    file_mapping: List[MovieFileMapping]
    extra_notes: Optional[str]
```

---

## 完整处理流程示例

### 示例 1：普通动漫剧集

**输入**: `[LoliHouse] 葬送的芙莉莲 / Sousou no Frieren [01-28 Fin][WebRip 1080p]/`

**处理步骤**:
1. 清洗文件名 → `葬送的芙莉莲`
2. TMDB 搜索 → 找到电视剧 "葬送的芙莉莲" (2023)
3. 类型判断 → 剧集 + 动漫
4. AI 分析（如启用）→ 映射每个文件到 S01E01-E28
5. 文件传输 → 硬链接到 `Anime Series/葬送的芙莉莲 (2023)/Season1/`

### 示例 2：电影合集

**输入**: `[AI-Raws][劇場版 空の境界][MOVIE 01-09][BDRip]/`

**处理步骤**:
1. 清洗文件名 → `劇場版 空の境界`
2. TMDB 搜索 → 找到电影 "空の境界"
3. 类型判断 → 电影 + 动漫
4. 检测到多个视频文件 → 触发电影合集分析
5. AI 分析 → 识别为电影合集，每个文件映射到对应电影
6. 逐个搜索 TMDB → 获取每部电影的准确信息
7. 文件传输 → 每部电影创建独立文件夹

**输出结构**:
```
Anime Movie/
├── 空之境界 第一章 俯瞰风景 (2007)/
│   └── 空之境界 第一章 俯瞰风景 - [原文件名].mkv
├── 空之境界 第二章 杀人考察（前） (2007)/
│   └── ...
└── ...
```

---

## 配置说明

**配置文件**: `data/config.json`

```json
{
    "api_key": "TMDB_API_KEY",
    "bangumi_path": "电视剧存放路径",
    "movie_path": "电影存放路径",
    "anime_path": "动漫剧集存放路径",
    "anime_movie_path": "动漫电影存放路径",
    "mode": "链接|复制|移动",

    "ai_enabled": true,
    "ai_provider": "openai|gemini",
    "ai_api_key": "API密钥",
    "ai_base_url": "API地址",
    "ai_model": "模型名称",
    "ai_confidence_threshold": "High|Medium|Low"
}
```

---

## 错误处理与回退

1. **TMDB 搜索失败** → 尝试 AI 提取标题后重试
2. **AI 分析失败** → 回退到传统正则方法
3. **AI 置信度不足** → 回退到传统正则方法
4. **文件传输失败** → 记录错误到任务 JSON

所有任务结果保存在 `data/task/{uuid}.json`，包含成功/失败状态和错误信息。
