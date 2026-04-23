# AI-first Strict 逻辑清理待办（本轮主体已完成）

更新时间：2026-04-10
状态：本轮主体已完成，保留观察项

## 当前结论（2026-04-10）

- 本轮 AI-first strict cleanup 的明确实施项已完成：回归入口已与主流程对齐，第一轮 brittle 前置逻辑瘦身已落地，相关定向回归与真实样本复验已完成。
- 2026-04-09/10 全量只读回归 `data/ai_batch_regression/20260409_235254` 已完成：`total_dirs=199`，且 `tmdb_not_found_dirs=0`、`validation_failed_dirs=0`、`exception_dirs=0`。
- 对该轮 `ai_empty_mapping` 做定点并行复验后（`data/ai_batch_regression/20260410_021437`），剩余稳定空映射均属于 **TMDB 当前没有合法季集落点** 的 side content / OVA / special 边界 case；按当前产品要求，这属于正确收口，不再视为待修 bug。
- 当前没有明确卡住的未完成功能项；剩余内容主要是观察性 follow-up，而不是必须继续推进的阻塞任务。
- 后续若继续推进，应以“更大样本回归后出现的新真实失败样本”为驱动，而不是预设继续削薄 `cleaner.py` / `get_info.py`。

---

## 背景

在 `data/ai_batch_regression/20260407_220952` 这轮失败过滤回归中，批量脚本已切到主流程 **AI-first 标题提取 + TMDB 候选选择** 入口。

结果显示：
- `tmdb_not_found`: `5 -> 0`
- `ai_invalid_mapping`: `2 -> 0`
- `timeout`: `1 -> 0`
- 失败过滤集仅剩 1 个 `ai_empty_mapping`

这说明很多此前依赖 strict / cleaner-first 逻辑暴露出来的问题，并不代表主流程 AI-first 链路的真实表现。

当前方向应改为：
- 优先让 AI 处理标题解析、query 构造、候选选择、复杂目录语义理解
- 逐步清理脆弱的 pre-AI strict / cleaner-first 逻辑
- 只保留少量低风险输入规范化、边界保护和最终合法性校验

**注意：本计划要清理的不是 strict final validation，而是 brittle 的前置语义规则。**

---

## 核心原则

### 1. 让 AI 负责理解，让 strict 负责收口

优先交给 AI 的部分：
- 标题提取
- query 构造
- TMDB 候选歧义选择
- 复杂目录语义理解
- 多来源元数据桥接

必须继续保留 strict 的部分：
- 路径安全与存在性校验
- TMDB season / episode 合法空间校验
- 重复映射 / 越界映射 / 冲突映射清洗
- 低置信度拒绝
- 明确的超时保护与批量回归保护参数

### 2. 不为个别样本回退到 cleaner-first

若某个失败样本可以：
- 通过 prompt 增强解决
- 通过 AI 标题 / fallback title / 候选选择解决
- 通过最终合法性校验收口解决

则优先走 AI-first，不再优先给 `cleaner.py`、query token、目录硬规则继续加补丁。

### 3. 回归链路尽量与主流程同构

若回归工具链的目标是验证主流程：
- 应优先复用主流程的 AI-first 标题解析与候选选择
- 不应长期维持 cleaner-first 的分叉入口

---

## 清理目标

1. 减少 `cleaner-first`、`rule-first`、`strict pre-processing` 对主链路结果的主导
2. 降低为了个别样本不断叠加硬编码 token / 特判 / query 变体的趋势
3. 让主流程、回归脚本、定点测试尽量共享同一套 AI-first 标题与候选选择入口
4. 保留最终合法性校验，避免 AI 放宽后导致错误落盘

---

## 清理边界

以下内容 **可以积极瘦身 / 删除 / 下放给 AI**：
- 标题清洗中的大量语义性硬规则
- query 扩词 / 变体构造中的个案 token 拼装
- 候选预排序中大量为个别标题服务的加减权逻辑
- 复杂目录下过多的中间推断层

以下内容 **仍应保留**：
- 少量低风险输入规范化
- 路径安全与存在性校验
- TMDB season / episode 合法空间校验
- 重复映射 / 越界映射 / 冲突映射清洗
- 明确的超时保护与批量回归保护参数

---

## 删除 / 瘦身准入标准

每次清理一类逻辑前，先满足：
- 能说明这段逻辑属于 **前置语义补丁**，而不是最终硬边界校验
- 已确认当前主流程或失败过滤回归中，这段逻辑不是核心收益来源
- 能明确替代者是什么：
  - AI 标题提取
  - AI fallback title
  - AI 候选选择
  - prompt 增强
  - final validation

每次清理后，至少要跑：
- `python -m compileall src`
- 相关定点测试
- 失败过滤回归
- 一小批原本成功样本 smoke 回归

通过后才允许继续下一轮瘦身。

---

## 模块盘点规则

下面每个模块都按三类看：
- **可删**：优先瘦身 / 删除
- **慎删**：保守处理，先验证替代链路稳定
- **必留**：不应随本计划删除

---

## 1. `src/rename/cleaner.py`

### 可删
- 仅为 TV query 命中服务的个案 token 清洗
- 已可由 AI 标题提取替代的标题拆分逻辑
- 持续膨胀的打包词 / 版本词 / 季度词硬编码
- 为极少数失败样本新增、且不具备普适性的 query 修补逻辑

### 慎删
- 低风险的噪声移除（如明显的发布组 / 画质 / 编码噪声）
- 对标题边界帮助明显、且不会引入强语义判断的规范化
- 对目录名 / 文件名分词有明显稳定收益的轻量清洗

### 必留
- 不涉及语义推断的基础规范化
- 对后续 AI 输入稳定性明显有帮助的最小清洗
- 被多个主流程入口共同依赖的基础工具函数

### 处理目标
- 仅保留低风险规范化
- 将语义判断优先交回 AI 标题提取 / fallback title

---

## 2. `src/rename/get_info.py`

### 可删
- 只为个别标题服务的 query token 拼装
- 为少量样本临时加的 brittle 候选排序补丁
- 为旧回归脚本保命而加、但主流程已不再依赖的特殊分支

### 慎删
- 轻量 query 变体构造
- 候选召回相关的薄规则
- 对 sequel / spinoff / special / movie 等 token 的少量辅助排序

### 必留
- TMDB 搜索本身
- 候选聚合与基础结果筛选
- 对主流程候选召回稳定性有明显帮助的轻量确定性辅助

### 处理目标
- 保持轻量 query 变体
- 排序只做薄的确定性辅助
- 把最终歧义选择继续交给 AI 候选选择

### 特别提醒
- 这里可以削弱“个案化排序补丁”，但不要轻易打坏“候选召回能力”
- 原则是：**排序可以变薄，召回不能塌**

---

## 3. `src/rename/process.py`

### 可删
- 标题输入准备中明显 legacy cleaner-first 的冗余前置逻辑
- 主流程与回归脚本重复维护、但本质相同的前置分流代码
- 可以统一抽为 AI-first 共用入口的旧分叉

### 慎删
- 目录分流逻辑
- Movie / TV 分支选择前的最小 deterministic 守卫
- 与任务记录、失败原因写入紧密耦合的前置行为

### 必留
- 任务级错误返回
- 路径存在性判断
- 非视频守卫
- 主流程共享入口与任务记录落盘逻辑

### 处理目标
- 继续强化共享入口
- 避免主流程 / 回归脚本 / 定点测试再次分叉

---

## 4. `src/ai/client.py`

### 可删
- 已被更清晰 prompt 段落覆盖的旧 prompt 冗余描述
- 与当前输出契约不一致的历史 prompt 文案

### 慎删
- 已证明对空映射、非法路径、候选误选有帮助的 prompt 约束
- 复杂目录提示
- partial mapping / unmatched_files 相关引导

### 必留
- 路径必须原样引用输入文件列表
- 最终输出只能使用 TMDB 合法空间
- 拿不准时放入 `unmatched_files`
- Bangumi 只是辅助证据，不直接决定 season
- 低置信度 / 非法结果后续仍由 strict validation 拦截

### 处理目标
- 用 prompt 质量替代中间硬规则
- 保持 strict final validation，不放宽输出契约

---

## 5. `tests/`

### 可删 / 可改
- 明显在固化 cleaner-first 中间行为的测试
- 仅验证某条旧 query 规则或旧 token 处理必须存在的测试
- 与主流程目标脱节、只是在绑定历史实现细节的断言

### 慎删 / 慎改
- 当前失败样本的最小回归入口
- 已证明能覆盖主流程关键失败模式的测试
- 对 prompt 约束、路径原样引用、Bangumi context 传递的断言

### 必留
- 最终结果导向测试
- strict validation 行为测试
- AI-first 主入口相关测试
- 失败样本回归
- 成功样本 smoke 回归

### 处理目标
- 回归测试围绕主流程真实目标建立
- 避免测试反向绑死需要清理的旧规则

---

## 建议执行顺序

### 阶段 A：先盘点，不急着删
- 列出各模块中明显偏 strict / brittle / 个案补丁的逻辑
- 标记哪些已经被 AI-first 回归结果证明“不是主收益点”
- 给每项逻辑打标签：`可删 / 慎删 / 必留`

### 阶段 B：小步瘦身
- 每次只删一小类 strict 逻辑
- 优先删最明显的 cleaner-first 冗余
- 每轮后跑 compileall + 定点测试 + 失败过滤回归 + 成功样本 smoke

### 阶段 C：保留硬边界
- 不删除最终合法性校验
- 不删除路径存在性与 TMDB 合法空间校验
- 不用“AI 更强”作为放宽错误输出的理由

---

## 已归档的阶段性优先事项

ARIA 遗留样本已于 2026-04-08 修复完成。

当前应进入两步：
1. 先产出第一轮 strict 清理盘点表
2. 再从第一个未完成项开始做小步瘦身

### 已完成（2026-04-08）

- `tools/test_ai_recognition.py` 已改为复用 `Rename._build_title_inputs()` 与当前 TV 搜索入口
- 脚本内手工复刻的 `remove_tag -> divide_by_year -> remove_season -> remove_episode -> get_tv_info_with_seasons()` cleaner-first TV 识别链路已移除
- `tests/test_filename_parsing.py` 中对 `build_tv_search_queries()` 的断言已收窄为低风险规范化结果导向断言，不再绑定 query 列表首项与过细 patch 产物
- `tests/test_ai_integration.py` 中对 `rank_tv_candidates()` 的 sequel/spinoff 排序测试已改为结果导向断言，不再绑定具体 `_match_score` 分差
- `src/rename/cleaner.py` 已做第一轮瘦身：去掉了更脆弱的破折号 subtitle 拆分与“中文标题再剥英文” query 回退
- `src/rename/get_info.py` 已做第一轮瘦身：移除重复的 `_search_tv_multi_language()` 旧实现，并去掉多季条目的弱加分补丁，仅保留季号 / 系列词 / 年份等薄排序骨架
- `src/rename/process.py` 的共享标题输入已进一步收敛为统一的点号标准化 + 年份提取入口
- 相关位置：`tools/test_ai_recognition.py`、`tests/test_filename_parsing.py`、`tests/test_ai_integration.py`、`src/rename/cleaner.py`、`src/rename/get_info.py`、`src/rename/process.py`

### 当前状态

- 第一轮盘点中已标记为“是”的项目，当前已全部完成首轮处理
- `src/rename/cleaner.py` / `src/rename/get_info.py` 中标记为“观察”的项，现阶段已完成第一轮保守瘦身；后续若继续推进，应结合更大样本回归再决定是否继续削薄
- 已完成一轮更大范围定向回归：`tests/test_filename_parsing.py`、`tests/test_ai_integration.py`、`tests/test_bangumi_tv_mapping_phase2_regression.py` 共 `63` 项全部通过
- `src/rename/get_info.py` 已补充“作品识别数字”薄排序：当 query 含 `2199` / `2202` 这类标题 identity token 时，候选若缺失相同数字会被降权；用于避免 `Space Battleship Yamato 2199` 被旧版《宇宙战舰大和号》反超
- `src/rename/process.py` 已补充两层 AI-first 上下文增强：
  - 非 structural 子任务在无 `cus_name` 且**子标题本身未显式带出父系列名**时，`ai_input_name` 会自动带上父目录上下文（如 `Yozakura Quartet / [Quetzal] Yoza-Quar! ...`），避免简称子目录丢失系列语义
  - TV 候选 AI 选择会携带候选季度结构与本地视频数量，便于短篇 / 特典 / 子企划目录做更稳的候选判定
- 2026-04-09 final focused sweep（`data/ai_batch_regression/20260409_020424`）中，`Strike Witches / Neptune / Yamato / Yozakura` 均符合预期；唯一残留 `ai_empty_mapping` 为 `OVERLORD Ple Ple Pleiades` 的 `Menu` 混入导致的 harness 统计偏差，不是主流程 strict 回归
- 回归 harness 的 `collect_video_files()` 已与主流程 `AIProcessor._collect_video_files()` 对齐：同样过滤 `Menu / NCOP / PV / Trailer` 等宣传内容，避免将本就应跳过的附加视频计入 `video_file_count` / `unmatched_count` 并误记为失败
- 针对 `OVERLORD Ple Ple Pleiades`，主流程子任务标题输入已收敛为“仅当子标题本身未显式带出父系列名时，才补父目录上下文”，避免对已自带系列名的子标题重复施加父级语义偏置
- 回归 harness 已为“单文件、低置信度、无明确单集标记、时长明显高于单话特典”的 bundled special compilation 增加 `skipped_special_compilation_case` non-failure 收口；这是 harness 统计语义补充，不是放宽主流程 strict TV mapping
- 2026-04-09 最终 focused sweep（`data/ai_batch_regression/20260409_030519`）已完成：`status_counts = {"skipped_movie_case": 3, "ok": 9}`，`invalid_dirs = 0`，本轮目标样本已全部转为 non-failure，其中 `OVERLORD Ple Ple Pleiades` 在最终整批复验中直接回到 `ok`
- 基于这轮更大回归结果，当前结论是：**可以暂时停止继续削薄 `build_tv_search_queries()` 与 `rank_tv_candidates()`，先保持现状**
- 原因：目前剩余逻辑大多已经属于“薄排序 / 低风险规范化”，继续削薄的收益开始下降，而误伤召回与季号判定的风险上升
- 2026-04-08 全量真实目录回归（`data/ai_batch_regression/20260408_181046`）中，`tmdb_not_found` 保持 `0`，说明第一轮清理没有打坏 TMDB 召回
- 2026-04-09 定点复验后，上一轮遗留的真实失败样本已全部转正或被正确收口为 non-failure：
  - `Strike Witches The Movie` → `skipped_movie_case`
  - `Neptune OVA3` → `ok`（`data/ai_batch_regression/20260409_011254`）
  - `Space Battleship Yamato 2199` → `skipped_movie_case + ok`（`data/ai_batch_regression/20260409_012643`）
  - `Yozakura Quartet` → `ok * 3`（`data/ai_batch_regression/20260409_020016`）
  - `OVERLORD` → `skipped_movie_case + ok * 4`（`data/ai_batch_regression/20260409_010910`）
- 因此当前已不再建议继续围绕这些样本叠加前置硬规则；后续重点应转回更大样本回归与新增失败样本观察
- 本轮没有动 `src/ai/client.py` 的 strict prompt 契约与 `src/rename/ai_processor.py` 的 strict final validation 收口逻辑
- 2026-04-09/10 全量只读回归：`data/ai_batch_regression/20260409_235254`
  - `status_counts = {"ok": 180, "skipped_movie_case": 13, "ai_empty_mapping": 5, "skipped_special_compilation_case": 1}`
  - `tmdb_not_found_dirs = 0`
  - `validation_failed_dirs = 0`
  - `exception_dirs = 0`
- 对 full run 中的 `ai_empty_mapping` 进一步做并行复验：`data/ai_batch_regression/20260410_021437`
  - `status_counts = {"ok": 6, "ai_empty_mapping": 4}`（含 structural split 后的 10 个 case）
  - 剩余 4 个稳定空映射均为 TMDB 当前无合法季集落点的 side content / OVA / special 边界 case
  - 当前产品标准已明确为 **TMDB-only legal space**：若 TMDB 没有对应条目，则空映射 / unmatched 即为正确结果

### 本轮已解决真实失败样本（2026-04-09）

#### 1. `skipped_movie_case`：`[philosophy-raws][Strike Witches The Movie]`

- 回归 harness 已与主流程对齐：`data/ai_batch_regression/run_full_regression.py` 不再强制 `is_movie=False`，改为与 webhook 一致传入 `is_movie=None`
- 回归记录新增 `resolved_is_movie` / `resolved_is_anime`，自动判到 movie 空间时直接记为 `skipped_movie_case`
- 当前结论：该 case 已确认是“回归 harness 的 TV-only 偏差”，不是 TV mapping prompt / strict validation 问题

#### 2. `ok`：`[ReinForce] Choujigen Game Neptune The Animation OVA3 (BDRip 1920x1080 x264 FLAC)`

- 通过 Bangumi prompt 上下文增强与 Bangumi API retry，已稳定桥接到合法的 TMDB Season 0 special
- 定点复验：`data/ai_batch_regression/20260409_011254`
  - `status=ok`
  - `mapping_accuracy=1.0`
  - 合法落点：`S00E01`
- 当前结论：该 case 已证明 AI-first + Bangumi rich context 足以解决，不需要放宽 strict

#### 3. `ok`：`Space Battleship Yamato 2199 (2012) VOSTFR BDrip 1080p FLAC x265-GundamGuy`

- `Film` 子目录继续正确收口为 movie 空间：`skipped_movie_case`
- `Série` 子目录已通过 TMDB TV 候选薄排序修复：
  - `src/rename/get_info.py` 为 `2199` 这类 identity token 增加薄加权/降权
  - 缺失相同数字 identity 的旧作候选会被压下，避免 1974 版基础作反超 2013 `2199`
- 定点复验：`data/ai_batch_regression/20260409_012643`
  - `status_counts = {"skipped_movie_case": 1, "ok": 1}`
  - `Série` 映射 `26/26`，`mapping_accuracy=1.0`
- 当前结论：问题根因是 TMDB TV 候选排序，不是 path strict / AI mapping strict

#### 4. `ok`：`Yozakura Quartet`

- 原先根因是复杂目录拆分后，`Yoza-Quar!` 子目录缺少父级系列语义，AI 标题提取只看到简称，无法进入正确 TMDB 空间
- 已修复：
  - 主流程子任务在无 `cus_name` 且为 non-structural case 时，`ai_input_name` 自动带父目录上下文
  - 回归 harness 同步复用该上下文输入
  - TV AI 选候选时附带季度结构与本地视频数量，降低短篇/子企划误选基础作的概率
- 定点复验：`data/ai_batch_regression/20260409_020016`
  - `status_counts = {"ok": 3}`
  - `Yoza-Quar!` → `ok`, `mapped=6/6`
  - `Hana no Uta` → `ok`
  - `Hoshi no Umi` → `ok`
- 当前结论：问题根因是子任务语义上下文不足，不是 strict validation 过严

#### 5. `ok / skipped_movie_case / skipped_special_compilation_case`：`[VCB-Studio] OVERLORD`

- 通过 structural split，对 recap movie / TV 主体目录做正确分流后，已不再出现原先的大目录超时主失败模式
- `OVERLORD Ple Ple Pleiades` 的 residual case 已按两层收口：
  - harness 侧先过滤 `Menu` 等宣传内容，避免附加视频混入统计
  - 对“单文件、低置信度、无明确单集标记、时长明显高于单话特典”的 bundled special compilation，harness 记为 `skipped_special_compilation_case` non-failure，而不是继续误记为 `ai_empty_mapping`
- 同时，主流程子任务 `ai_input_name` 已收敛为：**仅当子标题本身未显式带出父系列名时**，才补父目录上下文；因此像 `OVERLORD Ple Ple Pleiades` 这种已带系列名的子标题，不再重复叠加父级语义
- 最终 focused sweep：`data/ai_batch_regression/20260409_030519`
  - `status_counts = {"skipped_movie_case": 3, "ok": 9}`
  - `invalid_dirs = 0`
  - `OVERLORD Ple Ple Pleiades` 在整批复验中直接回到 `ok`
- 当前结论：`OVERLORD` 的剩余点已经完成收口；这次修复没有放宽 strict TV mapping，也没有回退到 cleaner-first，而是通过 harness 对齐 + 最小 AI-first 上下文修正解决了统计偏差与边界 case

### 当前待继续观察样本（2026-04-09）

- 本轮两线程大样本回归：`data/ai_batch_regression/20260409_134516`
  - `status_counts = {"ok": 182, "skipped_movie_case": 13, "ai_empty_mapping": 2, "ai_invalid_mapping": 1, "skipped_special_compilation_case": 1}`
  - `invalid_dirs = 3`
- 上述 3 个真实失败样本已完成定点修复，并在定向复验中全部转为 `ok`：`data/ai_batch_regression/20260409_163610`
  - `status_counts = {"ok": 3}`
  - `invalid_dirs = 0`
- 修复结论：
  1. `ok`：`[Moozzi2] Gundam Build Fighters Special Build Disc - OVA + SP`
     - 根因确认：AI 已给出正确 `source_index`，但可选 `file_path` 被模型污染成不存在的长字符串
     - 已修复：`src/rename/ai_processor.py` 的 strict hydration 改为——若 `source_index` 指向的真实文件存在，而额外填写的 `file_path` 只是**不存在的脏文本**，则忽略该脏 `file_path` 并按 `source_index` 回填；若它能解析到另一条真实文件，仍保持 strict 拒绝
     - 当前状态：`ok`，`mapped=7/7`，`mapping_accuracy=1.0`
  2. `ok`：`[Moozzi2] Tesagure! Bukatsumono Spin-off Purupurun Sharumu to Asobou [ x265-10Bit Ver. ] - TV + SP`
     - 根因确认：不是 strict 过严，而是上一轮 AI 在明明拿到了 TMDB Season 3 的情况下仍错误返回空映射
     - 现状复验：当前 AI-first 链路已能把正片 `01-12` 稳定映射到 TMDB Season 3，`EXTRA` 下 15 个特典继续保留在 `unmatched_files`
     - 当前状态：`ok`，`mapped=12/27`，`unmatched=15`；这符合“TMDB 已存在部分先映射，其余 extras 不强行落点”的准则
  3. `观察中但本轮复验已转正`：`[ANK-Raws] わかば＊ガール (BDrip 1920x1080 HEVC-YUV420P10 FLAC)`
     - 本轮定向复验中，模型直接把 `SP01-SP14` 映射为 `S01E01-S01E13 + S00E01`
     - 这次结果在 strict 上可执行，因此当前记录为 `ok`
     - 但从目录命名与时长看，它仍属于**高不确定性 special/短片边界样本**；后续应在更大样本中继续观察，不建议围绕它增加新的硬编码规则
- 因此当前大样本遗留的 3 个失败样本表可视为已清空；下一轮重点应回到更大样本回归，而不是继续围绕这 3 个 case 堆补丁

### 后续若继续推进（更新于 2026-04-09）

1. 保持当前 `source_index` strict hydration 规则：
   - `source_index` 正确 + `file_path` 只是不存在的脏文本 → 忽略脏路径并按 index 回填
   - `source_index` 与另一条真实文件冲突 → 继续 `ai_invalid_mapping`
2. 保持 `build_tv_search_queries()` 与 `rank_tv_candidates()` 现状，不做第二轮削薄
3. 后续若再出现 special-heavy 目录，继续坚持：
   - TMDB 已存在的合法剧集优先映射
   - TMDB 不存在合法落点的 extras / SP 允许留在 `unmatched_files`
   - 不为个案回退 basename fuzzy repair 或 cleaner-first
4. 下一步仅在需要时启动更大样本回归，观察是否还有新的真实失败样本冒出

---

## 第一轮盘点（2026-04-08）

| 模块 | 逻辑位置 | 当前作用 | 标签 | 替代方式 | 是否可在第一轮处理 |
|------|----------|----------|------|----------|--------------------|
| `tools/test_ai_recognition.py` | `tools/test_ai_recognition.py` | 手工复刻 cleaner-first 标题清洗与 `get_tv_info_with_seasons()` 搜索入口 | 可删/可改 | 复用 `Rename._build_title_inputs()` + `Search.search_tv_by_query()` / 当前主流程 TV 入口 | 已完成（2026-04-08） |
| `src/rename/cleaner.py` | `src/rename/cleaner.py:156-248` | TV query 噪声剔除、标题拆分、个案 query 变体构造 | 慎删 | 保留少量低风险规范化，其余逐步交给 AI title / fallback title | 观察 |
| `src/rename/get_info.py` | `src/rename/get_info.py:152-250` | sequel / spinoff / special token 提取与候选打分补丁 | 慎删 | 保留薄排序，最终歧义选择继续交给 AI | 观察 |
| `src/rename/get_info.py` | `src/rename/get_info.py:266-385` | 候选 `_match_score` 计算与确定性排序 | 慎删 | 降低个案 token 补丁密度，保留轻量召回/排序骨架 | 否 |
| `src/rename/process.py` | `src/rename/process.py:119-155` | 主流程共享标题输入构建 | 必留 | 无；作为主流程/回归链路共享入口继续复用 | 否 |
| `src/rename/process.py` | `src/rename/process.py:834-1054` | AI-first 类型判定、query 顺序、TV/Movie 搜索链路收口 | 必留 | 无；继续作为主流程共享入口 | 否 |
| `src/rename/process.py` | `src/rename/process.py:1056-1176` | TV 候选搜索、薄排序、AI 选候选、hydrate season 详情 | 必留 | 无；这里是 AI-first + strict 的现行主链路 | 否 |
| `src/ai/client.py` | `src/ai/client.py:760-823` | TV episode mapping prompt 契约：路径原样复用、TMDB 合法空间、Bangumi 只作辅助、部分映射优先 | 必留 | 无；这是替代中间硬规则的重要 prompt 约束 | 否 |
| `tests/test_filename_parsing.py` | `tests/test_filename_parsing.py:79-105` | 固化 `build_tv_search_queries()` 的噪声剔除/拆分行为 | 慎改 | 仅保留低风险规范化断言，避免绑死不断膨胀的 query patch 细节 | 是 |
| `tests/test_ai_integration.py` | `tests/test_ai_integration.py:818-860` | 固化 `rank_tv_candidates()` 的 sequel/spinoff 排序补丁行为 | 慎改 | 逐步改成结果导向断言，避免反向绑死具体打分补丁 | 观察 |

目标不是一口气删完，而是：
- 先把“该删什么 / 不该删什么”划清楚
- 先从回归工具链与主流程分叉点开始收敛
- 避免后续清理时误伤真正的硬边界
