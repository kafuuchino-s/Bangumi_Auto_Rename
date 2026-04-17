# Bangumi API 参考

本文档基于 Bangumi API Swagger / OpenAPI 文档整理，供本项目后续接入 **BGM episode 元数据辅助 TMDB 剧集映射** 时快速查阅。

- 文档入口：`https://bangumi.github.io/api/`
- OpenAPI JSON：`https://bangumi.github.io/api/dist.json`
- API 基础地址：`https://api.bgm.tv`

> 说明：本文档只整理了当前项目最相关的 **subject / episode 查询能力**。写操作、收藏、用户状态等未展开。

---

## 1. 本项目为什么需要 Bangumi API

当前 TV 剧集匹配的最终输出仍应以 **TMDB `SxxExx`** 为准，因为 Emby 对 TMDB 的兼容最好。

但部分动画在 **Bangumi / 本地发布习惯** 与 **TMDB 季集拆分** 之间存在错位，例如：

- 本地文件被命名为“第 13 话”
- Bangumi 也将其视为正片顺序中的第 13 集
- 但 TMDB 实际可能将其挂在 `Season 0`，例如 `S00E01`

这类情况下，可以把 **Bangumi episode 元数据** 作为辅助证据输入给 AI，帮助把“本地编号 / 标题 / 放送顺序”正确映射回 **TMDB 的真实季集结构**。

建议定位：

- **TMDB**：最终输出标准
- **Bangumi**：辅助理解作品原始 episode 顺序、类型、标题、放送日期

---

## 2. 推荐接入思路

建议让 AI 同时看到三类信息：

1. **本地文件分析结果**
   - 文件名中的集号
   - 标题关键词
   - 日期 / 版本信息
2. **TMDB 季集元数据**
   - `season_number`
   - `episode_number`
   - `name`
   - `air_date`
3. **Bangumi episode 元数据**
   - `sort`
   - `type`
   - `name`
   - `name_cn`
   - `airdate`
   - `duration`
   - `desc`

核心原则：

- AI 可以参考 Bangumi 的 episode 顺序和标题语义做判断
- **最终输出仍只能使用 TMDB 中真实存在的 `SxxExx`**
- 如果 Bangumi 与 TMDB 不一致，Bangumi 只作为“辅助证据”，不作为最终编号体系

---

## 3. 相关接口总览

### 3.1 搜索条目

#### `POST /v0/search/subjects`

用途：按关键词搜索条目。

适合场景：

- 已知作品标题，但还没有 `subject_id`
- 需要用标题先定位 Bangumi 条目

请求体里当前项目最相关的字段：

- `keyword`
- `sort`
- `filter`
  - `type`
  - `air_date`
  - `tag`
  - `meta_tags`

说明：

- 文档标注为实验性接口，后续行为可能变动
- 可结合年份 / 类型收窄结果

---

### 3.2 浏览条目

#### `GET /v0/subjects`

用途：按条件浏览条目。

当前项目一般不如搜索接口直接，但可用于：

- 按 `type`、年份等条件筛选条目

关键查询参数：

- `type`（必填）
- `year`
- `month`
- `sort`
- `limit`
- `offset`

---

### 3.3 获取条目详情

#### `GET /v0/subjects/{subject_id}`

用途：通过 `subject_id` 获取作品详情。

适合场景：

- 已锁定 Bangumi 条目
- 需要作品级标题信息作为 episode 匹配上下文

当前项目建议关注：

- `subject_id`
- 标题 / 中文标题 / 别名（以接口实际返回为准）

---

### 3.4 获取作品的章节列表

#### `GET /v0/episodes`

用途：列出某个条目的章节（episode）列表。

**这是当前项目最关键的接口之一。**

关键查询参数：

- `subject_id`（必填）
- `type`（可选，按章节类型筛选）
- `limit`
- `offset`

适合场景：

- 拉取整部作品的 episode 元数据
- 构造 AI 辅助映射输入
- 区分本篇 / 特别篇 / OP / ED / PV / 其他

---

### 3.5 获取单集详情

#### `GET /v0/episodes/{episode_id}`

用途：获取单个 episode 详情。

适合场景：

- 已通过列表拿到 `episode_id`
- 需要按单集补充更细节的信息

当前从文档摘要里只能确认该接口存在并返回 `EpisodeDetail`，但其完整字段定义未在本次提取结果中展开。

---

## 4. episode 字段：当前已确认最有价值的部分

从当前 OpenAPI 摘要里，能明确看到的 episode 相关 schema 为 `Legacy_Episode`。

已确认字段：

- `id`
- `url`
- `type`
- `sort`
- `name`
- `name_cn`
- `duration`
- `airdate`
- `comment`
- `desc`
- `status`

这些字段里，对本项目最重要的是：

| 字段 | 用途 | 对 TV 映射价值 |
|---|---|---|
| `id` | Bangumi episode 唯一 ID | 可用于缓存、日志、对照 |
| `type` | 章节类型 | 区分本篇 / 特别篇 / OP / ED / PV |
| `sort` | 集数 / 顺序号 | 用于理解 Bangumi 的播放顺序 |
| `name` | 原始标题 | 与本地文件标题、TMDB 集标题比对 |
| `name_cn` | 中文标题 | 中文资源名匹配时很有帮助 |
| `airdate` | 放送日期 | 可作为跨库对齐的重要证据 |
| `duration` | 时长 | 特定场景下可辅助判断正片/特典 |
| `desc` | 简介 | 只建议在疑难 case 里作为弱证据 |

---

## 5. `type` 的语义

根据 `Legacy_EpisodeType`：

- `0` = 本篇
- `1` = 特别篇
- `2` = OP
- `3` = ED
- `4` = 预告 / 宣传 / 广告
- `5` = MAD
- `6` = 其他

这对本项目特别重要，因为它能直接帮助 AI 判断：

- 本地文件是正片，还是 special
- 某个“第 13 话”到底更像本篇延伸，还是 TMDB Season 0 的 special
- OP/ED/PV 这类内容应进入 unmatched，还是落到特定 special 项

---

## 6. 目前未确认或需以实测为准的字段

在最初的 OpenAPI 摘要里，`ep` / `disc` 没有直接展开出来。

但对海王星 `subject_id=47957` 的实测接口：

- `GET https://api.bgm.tv/v0/episodes?subject_id=47957&limit=100`

返回的 episode 条目中，**实际包含**：

- `ep`
- `disc`
- `subject_id`
- `duration_seconds`

例如 special 条目实测可见：

```json
{
  "airdate": "2014-03-26",
  "name": "約束の永遠(トゥルーエンド)",
  "name_cn": "永恒的承诺（True End）",
  "duration": "00:23:40",
  "desc": "2014年10月16日在Animax播放。",
  "ep": 0,
  "sort": 13,
  "id": 299277,
  "subject_id": 47957,
  "comment": 15,
  "type": 1,
  "disc": 0,
  "duration_seconds": 1420
}
```

因此当前结论应更新为：

- `GET /v0/episodes` 的实际返回里，**很可能比 OpenAPI 摘要展示的字段更多**
- 对本项目最有价值的新增字段是：
  - `ep`
  - `disc`
  - `subject_id`
  - `duration_seconds`

其中：

- `sort` 更像 Bangumi 顺序号 / 排序号
- `ep` 更像展示集号
- 当 `type=1`（特别篇）时，可能出现 `sort=13` 但 `ep=0` 这种情况

这对本项目非常重要，因为它恰好能表达：

- **Bangumi 视角里这是作品顺序上的第 13 条 episode**
- **但它并不是普通本篇 `ep=13`，而是一个特别篇**

因此后续接入 AI 时，`sort` 和 `ep` 应当同时传入，而不是只保留其中一个。

建议优先依赖这些字段：

- `id`
- `subject_id`
- `type`
- `sort`
- `ep`
- `name`
- `name_cn`
- `airdate`
- `duration`
- `duration_seconds`
- `desc`

---

## 7. 鉴权结论

目前与本项目最相关的读取接口看起来：

- 多数可公开访问
- 部分路径支持 `OptionalHTTPBearer`
- 写操作通常需要 `HTTPBearer`

对本项目来说，如果只是：

- 搜索条目
- 拉取作品信息
- 获取 episode 列表

大概率可以先按**只读查询**集成，后续再根据实际返回补登录态支持。

---

## 8. 对当前项目的最小可用方案

如果要先把 Bangumi 用到 **TV 剧集 AI 匹配**，最小化接入建议如下：

### Step 1. 先拿到 `subject_id`

来源可以是：

- 手工指定
- 标题搜索后人工确认
- 后续做 TMDB ↔ Bangumi 条目关联缓存

### Step 2. 拉取整部作品的 episode 列表

调用：

- `GET /v0/episodes?subject_id=...`

### Step 3. 只保留 AI 最需要的字段

建议传入 AI 的 Bangumi episode 数据形态：

```json
[
  {
    "id": 1,
    "sort": 1,
    "type": 0,
    "name": "...",
    "name_cn": "...",
    "airdate": "...",
    "duration": "...",
    "desc": "..."
  }
]
```
```

### Step 4. 在提示词里明确 Bangumi 的角色

建议强调：

- Bangumi 仅用于帮助理解“本地文件在作品原始播放顺序中的含义”
- 最终映射结果**只能落到 TMDB 真实存在的 `SxxExx`**
- 当本地集号更接近 Bangumi、但与 TMDB 季结构不一致时，应优先用标题 / 日期 / 类型综合判断，再输出合法 TMDB 映射

---

## 9. 海王星这类 case 的实际用途

例如：

- 本地文件名带 `13`
- Bangumi episode 列表中它也更接近“正片顺序中的第 13 话”
- 但 TMDB 中对应项可能被拆进 `Season 0`

此时 AI 可以据此理解：

- 本地编号的来源更贴近 Bangumi / 放送顺序
- 但输出时要把它**折算回 TMDB 的 special**

也就是：

- **Bangumi 提供“语义解释”**
- **TMDB 提供“最终编号落点”**

---

## 10. 后续如果继续深化

后续可以继续补充：

1. `EpisodeDetail` 完整 schema
2. subject 返回体里标题 / 别名字段的精确定义
3. Bangumi `subject_id` 与 TMDB `tv_id` 的缓存关联策略
4. 疑难映射 case 的 few-shot 示例
5. 对 Season 0 / 第13话 / OVA / OAD / SP 的提示词模板

---

## 11. 本项目当前最值得实际使用的字段清单

如果只保留一份最小字段集，建议就是：

```text
subject_id
episode.id
episode.type
episode.sort
episode.name
episode.name_cn
episode.airdate
episode.duration
episode.desc
```

其中优先级建议：

1. `type`
2. `sort`
3. `name` / `name_cn`
4. `airdate`
5. `duration`
6. `desc`

---

## 12. 参考链接

- Swagger 页面：`https://bangumi.github.io/api/`
- OpenAPI JSON：`https://bangumi.github.io/api/dist.json`
- API Base URL：`https://api.bgm.tv`
