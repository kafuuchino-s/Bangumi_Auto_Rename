# 测试场景分析：自动重命名兼容性评估

本文档记录了对 `H:\Anime\Anime Series` 和 `H:\Anime\Anime Movie` 目录中文件的兼容性分析，评估程序能否成功自动重命名各种格式的文件。

---

## 测试结果总结

**测试脚本**: `tests/test_filename_parsing.py`

| 指标 | 结果 |
|------|------|
| 总测试数 | 64 |
| 通过数 | 56 |
| 通过率 | 87.5% |

---

## Anime Series 分析

### 可以成功处理的格式

| 文件示例 | 原因 |
|----------|------|
| `Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.mkv` | 标准 S01E01 格式，`match_and_extract()` 完美匹配 |
| `Nanatsu.no.Taizai.2024.S01E25.1080p.WEB-DL.mp4` | 标准 S01E01 + 年份，完美支持 |
| `[Uchuu Senkan Yamato 2202][01][BDRIP].mkv` | 方括号集数 `[01]` 可被 `episode_partten` 匹配 |
| `Majimoji Rurumo Kanketsu-hen - 01 [WebRip].mkv` | 横线后的集数，正则可匹配 |

### 潜在问题的格式

#### 1. 编码问题导致的乱码文件夹名
```
[AI-Raws][ギャッチマンクラウズ_Gatchaman Crowds]
    显示为: [AI-Raws][���å���ޥ󥯥饦��_Gatchaman Crowds]
```
- **风险**: TMDB 搜索可能失败
- **缓解**: 依赖英文名部分 `Gatchaman Crowds` 进行搜索

#### 2. 多季合集
```
[Moozzi2] Aria Series Blu-ray BOX - Animation + Natural + Origination
```
- **风险**: 包含多季内容，季度识别困难
- **预期行为**: 可能将所有内容归为 S01，或需要 AI 辅助

#### 3. 电影放在剧集目录
```
[2021 Movie][Uchuu Senkan Yamato 2205][BDRIP][1080P][01-08Fin+SP]
```
- **风险**: 文件夹名含 "Movie"，但实际是剧场版系列
- **修复**: 已添加 MOVIE 关键词检测

---

## Anime Movie 分析

### 可以成功处理的格式

| 文件示例 | 原因 |
|----------|------|
| `Omoide.no.Mani.2014.BluRay.1080p.x265.mkv` | 标准电影格式：`标题.年份.质量` |
| `The.Seven.Deadly.Sins.Cursed.by.Light.2021.1080p.mkv` | 标准格式，年份明确 |
| `The.Seven.Deadly.Sins.Part.1.2022.1080p.mkv` | Part.1 格式，已修复支持 |

### 潜在问题的格式

#### 1. 剧场版系列 (Movie Collection)
```
[AI-Raws][空之境界][MOVIE 01-09+SP Fin]
├── [AI-Raws] 空之境界 #01 俯瞰风景.mkv
├── [AI-Raws] 空之境界 #02 杀人考察(前).mkv
└── ... (9部电影 + SP)
```
- **风险**: 剧场版系列可能被误判为 TV
- **修复**: 已添加 MOVIE 关键词检测

#### 2. 前篇/后篇 命名
```
[ANK-Raws] Wake Up, Girls! 前篇「青春之影」
[ANK-Raws] Wake Up, Girls! 后篇「Beyond the Bottom」
```
- **状态**: 已支持 `前[篇编]` 和 `[後后][篇编]` 模式

---

## 详细测试场景

### 场景 1: 标准 S01E01 格式 (100% 通过)

**目录**: `H:\Anime\Anime Series\Love.Death.&.Robots.S04.1080p`

| 源文件名 | 预期行为 | 预期输出 |
|---------|---------|---------|
| `Love.Death.&.Robots.S04E01.1080p.mkv` | `match_and_extract()` 提取 | `爱，死亡和机器人 - S04E01.mkv` |

**代码路径**:
```
_process() → remove_tag() → check_task_type() → process_sub()
    └── match_and_extract() → r'S(\d+)E(\d+)' → (4, 1)
```

---

### 场景 2: 方括号集数格式 [01] (100% 通过)

**目录**: `H:\Anime\Anime Series\[Uchuu Senkan Yamato 2202][BDRIP]`

| 源文件名 | 预期行为 |
|---------|---------|
| `[Uchuu Senkan Yamato 2202][01][BDRIP].mkv` | `episode_partten` 匹配 `\[(\d{1,2})\]` |
| `[Uchuu Senkan Yamato 2202][26][BDRIP].mkv` | 提取 01-26 集 |

**代码路径**:
```
process_sub()
    └── for pattern in episode_partten: → 匹配 [01]
```

---

### 场景 3: 前篇/后篇电影 (100% 通过)

| 源文件名 | 结果 |
|---------|------|
| `前篇「青春之影」.mkv` | Part1 |
| `后篇「Beyond the Bottom」.mkv` | Part2 |
| `Part.1.2022.mkv` | Part1 |
| `Part.2.2023.mkv` | Part2 |
| `上篇.mkv` | Part1 |
| `下篇.mkv` | Part2 |

**代码路径**:
```
extract_part()
    └── r'前[篇编]' → Part1
    └── r'[後后][篇编]' → Part2
    └── r'[Pp]art[\s\-_\.]*([1-9])' → Part1/Part2
```

---

### 场景 4: 季度提取 (100% 通过)

| 输入 | 结果 |
|------|------|
| `Love.Death.&.Robots.S04.1080p` | 4 |
| `Anime 第2季` | 2 |
| `Anime 第三季` | 3 |
| `Anime Season 2` | 2 |
| `Anime 2nd Season` | 2 |
| `Anime III` | 3 |
| `Anime IV` | 4 |

---

### 场景 5: 视频格式提取 (100% 通过)

| 输入 | 结果 |
|------|------|
| `Movie.1080p.BluRay.mkv` | 1080p |
| `Movie.720p.WEB-DL.mkv` | 720p |
| `Movie.2160p.UHD.mkv` | 4K |
| `Movie.4K.HDR.mkv` | 4K |

---

### 场景 6: 特典标签检测 (100% 通过)

| 输入 | 识别为特典 | 匹配标签 |
|------|-----------|----------|
| `[VCB-Studio] Anime [NCOP].mkv` | 是 | NCOP |
| `[VCB-Studio] Anime [NCED].mkv` | 是 | NCED |
| `[VCB-Studio] Anime [PV01].mkv` | 是 | PV |
| `[VCB-Studio] Anime [Menu].mkv` | 是 | Menu |
| `[VCB-Studio] Anime [CM01].mkv` | 是 | CM |
| `[VCB-Studio] Anime [01].mkv` | 否 | - |

---

## 已修复的问题

### 1. `extract_part()` 不支持 `Part.1` 格式

**问题**: 点分隔符 `Part.1` 无法匹配

**修复**: `src/rename/cleaner.py:409`
```python
# 修复前
r'[Pp]art[\s\-_]*([1-9A-Za-z])'

# 修复后
r'[Pp]art[\s\-_\.]*([1-9A-Za-z])'
```

### 2. 集数提取优先使用 episode_partten

**问题**: `extract_number()` 可能匹配到标题中的年份 (如 2199, 2202)

**修复**: `src/rename/process.py:175-200`
```python
# 优先尝试 S01E01 格式
epp = extract_base_num(_item_name)
if epp is not None:
    ep = int(epp)
else:
    # 然后尝试 episode_partten 中的模式
    for pattern in episode_partten:
        match = re.search(pattern, _item_name)
        if match:
            ep = int(match.group(1))
            break
    # 最后回退到 extract_number
    if ep is None:
        ep = extract_number(_item_name)
```

### 3. 添加 MOVIE 关键词检测

**问题**: 目录名含 "MOVIE" 但仍被判定为 TV

**修复**: `src/rename/process.py:350-357`
```python
movie_keywords = ['MOVIE', 'FILM', '剧场版', '劇場版', '电影', '電影']
path_name_lower = path.name.lower()
for kw in movie_keywords:
    if kw.lower() in path_name_lower:
        pos -= 1.0  # 强烈倾向于电影
        break
```

---

## 已知限制

### 1. 标题含科幻年份时的集数提取

**示例**: `Space Battleship Yamato 2199 - 01.mkv`

**问题**: 如果 `episode_partten` 都不匹配，`extract_number()` 可能错误匹配到 2199

**缓解**: 已通过优先使用 `episode_partten` 部分解决

### 2. 电影子目录递归处理

**示例**:
```
[VCB-Studio] ARIA The BENEDIZIONE/
├── main.mkv
└── SPs/
    └── [PV01].mkv  ← 不会被处理
```

**状态**: 低优先级，暂未修复

---

## 运行测试

```bash
cd C:/Users/kafuuchino/CodeProjects/Bangumi_Auto_Rename
.venv/Scripts/python.exe tests/test_filename_parsing.py
```

---

## 更新记录

- **2026-01-04**: 初始分析，修复 3 个问题，通过率从 79.5% 提升至 84.1%
- **2026-01-04**: 新增 20 个测试场景（日期格式、Info标签、季度格式、OVA/OAD/SP、年份范围、中英混合、Vol分卷），通过率提升至 87.5%

---

## 新增测试场景 (2026-01-04)

### 场景 10: 日期格式文件名 (100% 通过)

| 输入 | 提取年份 |
|------|----------|
| `(2023.12.20)Psycho-Pass Providence-[1080p].mkv` | 2023 |

---

### 场景 11: 新发现的特典标签 (80% 通过)

| 输入 | 是否特典 | 匹配标签 |
|------|----------|----------|
| `Movie ABYSS OF HYPERSPACE Info01.mkv` | 否 | - |
| `Movie ABYSS OF HYPERSPACE CM01.mkv` | 是 | CM |
| `Movie ABYSS OF HYPERSPACE Trailer.mkv` | 是 | Trailer |
| `ARIA [Menu][Ma10p].mkv` | 是 | Menu |
| `Movie Remix - Gate of Seventh Heaven.mkv` | 否(误报) | Event |

**问题**: `Remix` 被误识别为特典（因为包含 `Event` 子串 `event` != `Event`，但 `Remix` 包含 `Event` 的字母? 不，实际是因为 `EXTRA_TAG` 中的其他标签被匹配）

---

### 场景 12: 季度格式在标题中间 (100% 通过)

| 输入 | 提取季度 |
|------|----------|
| `[AI-Raws][Kimetsu No Yaiba S5 Hashira Geiko Hen]` | 5 |
| `[ANK-Raws] Strike Witches Season 2` | 2 |
| `Gatchaman Crowds insight TV S2 00-12` | 2 |

---

### 场景 13: OVA/OAD/SP 标签检测 (100% 通过)

| 输入 | 是否 S0 | 匹配标签 |
|------|---------|----------|
| `[Moozzi2] Watamote - TV + OAD` | 是 | OAD |
| `[Moozzi2] Strike Witches - TV + SP` | 是 | SP |
| `[ANK-Raws] Anime OVA [BDrip].mkv` | 是 | OVA |
| `[ANK-Raws] Anime Special Episode.mkv` | 是 | Special |
| `[ANK-Raws] Anime Episode 01.mkv` | 否 | - |

---

### 场景 14: 年份范围格式 (100% 通过)

| 输入 | 提取年份 |
|------|----------|
| `[2017-19][Uchuu Senkan Yamato 2202]` | 2017 |
| `[2021 Movie][Uchuu Senkan Yamato 2205]` | 2021 |

---

### 场景 15: 中英混合文件名 (100% 通过)

| 输入 | Part提取 |
|------|----------|
| `七大罪 怨恨的爱丁堡 后篇.The.Seven.Deadly.Sins.Part.2.2023.mkv` | Part2 |
| `[ANK-Raws] 劇場版 Wake Up, Girls! 青春の影.mkv` | None |

---

### 场景 16: Vol.xx 分卷格式 (100% 通过)

| 输入 | 提取 Vol |
|------|----------|
| `[Space Dandy Vol.1-Vol.5][BDRIP]` | Vol=1 |
| `[Space Dandy Vol.6-10][BDRIP]` | Vol=6 |

---

## 仍然存在的问题

### 1. 科幻年份被误识别为集数 ✅ AI可覆盖

**示例**: `Space Battleship Yamato 2199 (2012) - 01.mkv`

**问题**: `extract_number()` 匹配到 `2199` 而非 `01`

**状态**:
- 正则测试失败
- **实际运行时**: 如果是动漫，`analyze_episode_mapping()` 会分析完整上下文，正确识别集数

### 2. #01 格式前导零丢失 ✅ AI可覆盖

**示例**: `空之境界 #01 俯瞰风景.mkv`

**问题**: `extract_number()` 返回 `1` 而非 `01`

**影响**: 低（功能正确，仅格式差异）

**状态**:
- **实际运行时**: 电影合集会触发 `analyze_movie_collection()`，AI 可正确识别电影序号

### 3. Remix 被误识别为特典 ❌ 需要修复

**示例**: `Movie Remix - Gate of Seventh Heaven.mkv`

**问题**: 被 `Event` 标签误匹配（`EXTRA_TAG` 检测使用子串包含判断）

**建议修复**:
```python
# 当前逻辑 (有问题)
if tag.lower() in tc.lower():  # "event" in "seventh" = False, 但其他标签可能误匹配

# 建议改为单词边界匹配
import re
if re.search(rf'\\b{re.escape(tag)}\\b', tc, re.IGNORECASE):
```

---

## AI 处理覆盖情况

| 场景 | 正则测试 | AI覆盖 | 说明 |
|------|----------|--------|------|
| 动漫剧集 | 部分失败 | ✅ | `analyze_episode_mapping()` 分析完整上下文 |
| 电影合集 | 部分失败 | ✅ | `analyze_movie_collection()` 识别电影系列 |
| TMDB搜索失败 | - | ✅ | `extract_title_and_type()` AI提取标题 |
| 特典标签检测 | 1个误报 | ❌ | 正则逻辑需要修复 |

### AI 触发条件

```python
# 动漫剧集 - process.py:708
if is_anime and self.ai_processor.ai_client.is_available():
    ai_result = self.ai_processor.analyze_anime_files(path, tv_info)

# 电影合集 - process.py:542-570
if len(video_files) > 1:
    collection_result = ai_client.analyze_movie_collection(path.name, local_files)
```

---

## AI 集成测试结果 (2026-01-04)

**测试脚本**: `tests/test_ai_integration.py`

| 指标 | 结果 |
|------|------|
| 总测试数 | 7 |
| 通过数 | 7 |
| 通过率 | 100% |
| AI 置信度 | High: 3, Medium: 0, Low: 0 |

### 测试场景详情

#### 场景 1: AI 标题和类型提取 (4/4 通过)

| 输入 | 提取标题 | 类型 |
|------|----------|------|
| `[LoliHouse] 葬送的芙莉莲 / Sousou no Frieren [01-28 Fin]` | 葬送的芙莉莲 | tv |
| `[AI-Raws][劇場版 空の境界][MOVIE 01-09+SP Fin]` | 空の境界 | movie |
| `[2021 Movie][Uchuu Senkan Yamato 2205]` | Uchuu Senkan Yamato 2205 | movie |
| `Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.mkv` | Love Death & Robots | tv |

#### 场景 2: AI 电影合集分析 (1/1 通过)

**输入**: 空之境界剧场版系列 (#01-#09 格式)

| 文件 | AI识别电影 | 序号 |
|------|-----------|------|
| `#01 俯瞰风景.mkv` | 空の境界 第一章 俯瞰風景 | 1 |
| `#02 杀人考察(前).mkv` | 空の境界 第二章 殺人考察（前） | 2 |
| `#03 痛觉残留.mkv` | 空の境界 第三章 痛覚残留 | 3 |
| `#09 未来福音 extra chorus.mkv` | 空の境界 未来福音 extra chorus | 9 |
| `シネマナーCM #01.mkv` | (特典) | None |

**关键验证**: AI 正确识别电影序号，CM 特典正确返回空标题

#### 场景 3: Yamato 2199 科幻年份问题 (1/1 通过)

**问题**: 正则 `extract_number()` 会匹配到 2199 而非实际集数

| 文件 | 正则结果 | AI结果 |
|------|----------|--------|
| `Yamato 2199 - 01 VOSTFR.mkv` | 2199 ❌ | S01E01 ✅ |
| `Yamato 2199 - 02 VOSTFR.mkv` | 2199 ❌ | S01E02 ✅ |
| `Yamato 2199 - 15 VOSTFR.mkv` | 2199 ❌ | S01E15 ✅ |
| `Yamato 2199 - 26 VOSTFR.mkv` | 2199 ❌ | S01E26 ✅ |

#### 场景 4: Yamato 2202 方括号格式 (1/1 通过)

**问题**: 正则 `extract_number()` 会匹配到 2202 而非 `[01]`

| 文件 | 正则结果 | AI结果 |
|------|----------|--------|
| `[Yamato 2202][01][BDRIP].mkv` | 2202 ❌ | E01 ✅ |
| `[Yamato 2202][15][BDRIP].mkv` | 2202 ❌ | E15 ✅ |
| `[Yamato 2202][26][BDRIP].mkv` | 2202 ❌ | E26 ✅ |

### 结论

AI 功能成功覆盖了正则测试失败的所有关键场景:
- ✅ 科幻年份干扰集数提取
- ✅ 电影合集 #01 格式识别
- ✅ 方括号集数格式 [01] 识别
- ✅ 标题和类型自动提取

---

## 端到端流程测试 (2026-01-04)

**测试脚本**: `tests/test_e2e_flow.py`

| 指标 | 结果 |
|------|------|
| 总测试数 | 11 |
| 通过数 | 11 |
| 通过率 | 100% |

### 测试覆盖范围

```
输入文件/目录
    ↓
┌─────────────────────────────────────┐
│ TMDB 搜索 (get_info.py)             │  ← 场景 0: TMDB搜索
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 正则解析 (cleaner.py)               │  ← 场景 4: S01E01 格式
│ - match_and_extract()               │
│ - extract_number()                  │
│ - extract_part()                    │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ AI 分析 (ai_processor.py)           │  ← 场景 1-3: AI 分析
│ - analyze_episode_mapping()         │
│ - analyze_movie_collection()        │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 文件映射生成                         │  ← 验证输出
└─────────────────────────────────────┘
```

### 各场景测试结果

| 场景 | 描述 | TMDB | AI | 通过 |
|------|------|------|-----|------|
| 0 | TMDB 搜索 | ✅ 5/5 | - | ✅ |
| 1 | Yamato 2199 科幻年份 | ✅ | ✅ High | ✅ |
| 2 | Yamato 2202 方括号 [01] | ✅ | ✅ High | ✅ |
| 3 | 空之境界电影合集 | - | ✅ High | ✅ |
| 4 | Love Death Robots S01E01 | ✅ | 不需要 | ✅ |
| 5 | 单部电影 | ✅ 2/2 | - | ✅ |

### 关键验证点

1. **TMDB 搜索**: 使用日文原名可以更准确搜索 (宇宙戦艦ヤマト2199)
2. **正则解析**: S01E01 格式完美匹配，无需 AI
3. **AI 修正**: 科幻年份 (2199/2202) 被正则错误匹配时，AI 正确修正
4. **电影合集**: AI 识别 #01-#09 格式并返回正确电影序号

---

## Season 0 特殊场景处理 (2026-01-05)

**测试脚本**: `tests/test_season0.py`

| 指标 | 结果 |
|------|------|
| 总测试数 | 8 |
| 通过数 | 8 |
| 通过率 | 100% |

### 场景 17: 小数集数检测 (100% 通过)

**问题**: 5.5、11.5、12.5 等小数集数通常是总集篇/特别篇，在 TMDB 中归入 Season 0

**正则**: `(?<![vV\d])(\d{1,3}\.5)(?!\d)`

| 输入 | 匹配 | 说明 |
|------|------|------|
| `[Anime] - 05.5.mkv` | ✅ | 小数集数 |
| `[VCB-Studio] 葬送的芙莉莲 [12.5][Ma10p_1080p].mkv` | ✅ | 总集篇 |
| `Frieren - 12.5 - Special.mkv` | ✅ | 特别篇 |
| `Anime v1.5.mkv` | ❌ | 版本号，非集数 |
| `Anime - 720p.5mbps.mkv` | ❌ | 比特率，非集数 |

**代码路径**:
```python
# ai_processor.py:11-13
DECIMAL_EPISODE_PATTERN = re.compile(
    r'(?<![vV\d])(\d{1,3}\.5)(?!\d)'
)

# ai_processor.py:532
has_decimal_episode = bool(DECIMAL_EPISODE_PATTERN.search(f.name))
```

---

### 场景 18: 第 00 集检测 (100% 通过)

**问题**: 第00話、[00]、E00 等通常是序章/先行篇，在 TMDB 中归入 Season 0

**正则**: 匹配多种格式

| 输入 | 匹配 | 说明 |
|------|------|------|
| `[Snow-Raws] ハイスクールD×D HERO 第00話 (BD).mkv` | ✅ | 日文第00話 |
| `[Anime] - 第00话.mkv` | ✅ | 中文第00话 |
| `[Anime] [00].mkv` | ✅ | 方括号格式 |
| `Anime E00.mkv` | ✅ | E00 格式 |
| `Anime EP00.mkv` | ✅ | EP00 格式 |
| `Anime - 00 - Prologue.mkv` | ✅ | 横线分隔 |
| `[Anime] SP00.mkv` | ✅ | SP00 格式 |
| `Anime 2000.mkv` | ❌ | 年份，非集数 |
| `Anime 1080p.mkv` | ❌ | 分辨率，非集数 |

**代码路径**:
```python
# ai_processor.py:15-25
EPISODE_00_PATTERN = re.compile(
    r'(?:'
    r'\[00\]|'                      # [00]
    r'[Ee][Pp]?00(?!\d)|'           # E00, EP00
    r'第00[話话集]|'                 # 第00話, 第00话, 第00集
    r'[_\s\-]00[_\s\-\.]|'          # - 00 -, _00_, 00.
    r'SP00(?!\d)'                   # SP00
    r')'
)

# ai_processor.py:535
has_episode_00 = bool(EPISODE_00_PATTERN.search(f.name))
```

---

### 场景 19: Vol.SP 格式处理

**问题**: `[Vol.01][SP01]` 中的 Vol.01 是 BD 卷号，SP01 才是特典编号，AI 可能混淆

**解决**: AI prompt 中明确说明

```
3. **Vol.SP 格式**: 文件名如 [Vol.01][SP01] 中的 Vol.01 是 BD 卷号，SP01 才是特典编号
   - 不要将 Vol 编号误认为是集数或季度信息
   - 多个 Vol 中的同编号 SP 是不同的特典（如 Vol.01 SP01 ≠ Vol.02 SP01）
```

---

### 场景 20: SP 编号与 TMDB 映射

**问题**: 本地文件的 [SP03] 不一定对应 TMDB Season 0 的 E03

**解决**: AI prompt 中强调按标题匹配而非序号

```
2. **重要**: 文件名中的序号（如 SP01、SP02、OVA01）不一定对应 TMDB 的集数号！
   - 例如：[SP03] 可能对应 TMDB S0E1，而不是 S0E3
   - 必须根据标题内容、播出日期等信息综合判断
```

---

### 场景 21: 宣传内容过滤 (100% 通过)

**PROMO_TAGS**: `NCOP, NCED, Creditless, Non Telop, PV, CM, Menu, Trailer, Preview, Digest, Interview, Cast Talk, Making, MV, Teaser, Logo, Spot, Web Preview`

| 输入 | 过滤 | 说明 |
|------|------|------|
| `[VCB-Studio] Anime [NCOP].mkv` | ✅ | 片头 |
| `[VCB-Studio] Anime [NCED01].mkv` | ✅ | 片尾 |
| `[FreeSub] Anime [PV01].mkv` | ✅ | 预告 |
| `[FreeSub] Anime [CM01].mkv` | ✅ | 广告 |
| `[VCB-Studio] Anime [Menu].mkv` | ✅ | 菜单 |
| `[VCB-Studio] Anime [Trailer].mkv` | ✅ | 预告片 |
| `Anime (Creditless ED).mkv` | ✅ | 无字幕版片尾 |
| `Anime.Non Telop Ver.mkv` | ✅ | 无字幕版（日语） |
| `[VCB-Studio] Anime [OVA].mkv` | ❌ | OVA 是正片 |
| `[VCB-Studio] Anime [SP01].mkv` | ❌ | SP 是正片 |

**代码路径**:
```python
# cleaner.py
def is_promotional_content(filename: str) -> bool:
    # 检查 PROMO_TAGS 中的标签
```

---

### Season 0 处理流程图

```
本地文件
    ↓
┌─────────────────────────────────────┐
│ _collect_season0_files()            │
│ - 特典文件夹检测 (SPs/, Extras/)     │
│ - 特典标签检测 (OVA, OAD, SP)        │
│ - 小数集数检测 (5.5, 12.5)           │  ← 新增
│ - 第00集检测 (第00話, [00], E00)     │  ← 新增
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ is_promotional_content()            │
│ - 过滤 NCOP/NCED/PV/CM 等           │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ analyze_season0_mapping() (AI)      │
│ - 按标题相似度匹配                   │
│ - Vol.SP 格式识别                    │  ← 优化
│ - SP编号≠TMDB集数号                  │  ← 优化
└─────────────────────────────────────┘
    ↓
Season 0 文件映射
```
