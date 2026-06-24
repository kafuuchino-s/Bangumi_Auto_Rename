# full146 样本回归报告

> 本报告记录 **full146 样本池**（146 个真实下载包目录快照）在 Local→Bangumi→TMDB 全链路 mapping-only 回归下的结果。mapping-only 模式不落盘，仅验证 Case Agent 语义映射 + BGM→TMDB 桥接 + 合同校验能否产出合法结果。
>
> 产物来源：`tests/sample_pool/generated/`（本地回归产物，`.gitignore` 忽略，可由 `tools/run_local_bangumi_mapping_sample_pool.py` 与 `tools/run_bgm_to_tmdb_bridge_sample_pool.py` 复现）。

## 总览

| 阶段 | 链路 | 样本数 | accepted | 结果 |
|---|---|---|---|---|
| 段1 | Local → Bangumi | 146 | **146** | **146/146 accepted** |
| 段2 | Bangumi → TMDB | 146 | **146** | **146/146 accepted** |
| **联合** | Local → Bangumi → TMDB | **146** | **146** | **146/146 = 100% accepted** |

- **运行方式**：Pi Case Agent 真起（Node.js sidecar），两段映射独立运行后合并。
- **段1 产物**：`local_bangumi_mapping_gate_20260620_001437_549/summary.json` → `counts: {accepted: 146}`，146 行 per-sample 全 accepted。
- **段2 产物**：`bgm_to_tmdb_bridge_gate_20260620_091404_437/`（146 个 per-sample 产物）+ 补跑产物（`rerun_0126`、`rerun_2fail`、`c_verify`），合并后 146/146 accepted。

> 回归套件 `tests/sample_pool/suites/local_bangumi_case_agent_convergence.json` 自述为「iteration gate, not an oracle」——它是迭代守门基线，不是判定正确性的神谕。本报告数据取自最新 gate 产物。

## 样本池构成

full146 = **130 个 TV 样本 + 16 个电影样本**，从真实下载包的目录快照抽取，覆盖以下类型：

- 剧场版合集（多部剧场版 + 特典同包）
- 系列全盒 / BD-Box（一包含整个系列多季 + 衍生）
- TV 正篇 + SP / OVA 混杂包
- 跨季续篇（同系列拆成多个样本，每样本一季）
- 短篇 / OVA / 特别篇系列
- 非动漫剧集（Love, Death & Robots 等）
- 大体量包（百文件级到千文件级）

下列「复杂样本实证」按类型各取代表性案例展开。

## 复杂样本实证

下列样本是 full146 中结构最复杂、最具代表性的案例，全部 accepted。每条附一行桥接摘要（取自产物 `summary` 字段）。

### 剧场版合集

| 样本 | 内容 | 段2 桥接摘要 |
|---|---|---|
| `movie/sample_0002` | **空之境界**：7 部剧场版（#01-#09）+ 剧场マナーCM + SP Fin，20 个 mkv | Garden of Sinners (Kara no Kyoukai) movie 包：11 个 TMDB movie 节点配 11 行 BGM movie 映射 + 1 supplemental 先行特典 |
| `movie/sample_0006` | Psycho-Pass Providence 剧场版（2023） | accepted |
| `tv/sample_0011` | Psycho-Pass Sinners of the System 剧场版合集 | accepted |

### 系列全盒 / 多季全包

| 样本 | 内容 | 段2 桥接摘要 |
|---|---|---|
| `tv/sample_0042` | **ARIA 全系列 BD-Box**（Animation + Natural + Origination + Avvenire + Arietta） | ARIA 系列桥接 TMDB 53787；所有已映射 BGM 节点均有合法 TMDB 节点 |
| `tv/sample_0126` | **向阳素描 Hidamari Sketch**：**1559 个文件**，4 TV 季 + OVA/special | 桥接 TMDB 系列 45893；83 assignment / 60 target / 23 supplemental / 0 absent，正篇各季按集标题/顺序对齐 |
| `tv/sample_0103` | **魔法少女小圆 Madoka 全系列** | TV S01 + 三部主电影 + Concept Movie 配合法节点；supplemental 附加保持未映射 |
| `tv/sample_0093` | 南家系列多季合包 | accepted |
| `tv/sample_0096/0097` | **Overlord** 主体 + IV | OVERLORD IV 桥接 TMDB tv:64196：S04E01-13 主篇 + S00E43-55 Play Play Pleiades 4 特典 |

### TV + SP / OVA 混杂

| 样本 | 内容 | 段2 桥接摘要 |
|---|---|---|
| `tv/sample_0005` | **Gatchaman Crowds S1**（01-12 + SP） | S1 正篇 + special 'Embrace' 桥接 TMDB tv:61527 |
| `tv/sample_0006` | **Gatchaman Crowds Insight S2**（00-12 + SP） | 桥接 TMDB season 2；BGM sort0 special 'inbound' → TMDB S00E02 |
| `tv/sample_0044/0045/0046` | **高达创战者** TV+SP / 特典 OVA+SP / Try | 正篇 1-25 桥接 tv:60667 S1；特典盘落 season 0 及 Try Island Wars movie；Try 桥接 S2 |
| `tv/sample_0028/0056` | Yuyushiki 本篇 + OVA/SP 拆分 | accepted |

### 跨季续篇（分散多样本）

| 样本 | 内容 | 段2 桥接摘要 |
|---|---|---|
| `tv/sample_0004/0091/0092` | **鬼灭之刃** S5 柱稽古 / 合集 / 锻刀村篇 | 柱稽古篇桥接 tv:85937 Season 5；合集桥接 tv:85937 + movie:635302；锻刀篇桥接 Season 4 + 11 supplemental |
| `tv/sample_0001/0002/0120` | **宇宙战舰大和号** 2202 / 2205 / 2199 | 2202 桥接 tv:45844 Season 2（BD digest/logo 留 supplemental）；2199 TV + 关联 movie 桥接对应条目 |
| `tv/sample_0105-0108` | **战姬绝唱 Symphogear** 无印 / GX / AXZ / XV | accepted |
| `tv/sample_0060/0061` | **海王星 Neptune** OVA2 / OVA3 | OVA2 桥接 tv:63525 S00E05；OVA3 桥接 S00E02（Nep's Summer Vacation，标题/年份对齐） |

### 非动漫剧集

| 样本 | 内容 | 段2 桥接摘要 |
|---|---|---|
| `tv/sample_0117/0128/0129/0130` | **Love, Death & Robots** S04 / S01 / S02 / S03 | 全部桥接 TMDB tv:86831 对应季；S01 18 正篇标题/顺序全部对齐 |

## 体量代表：向阳素描（sample_0126）

full146 中体量最大的单个样本，也是合同校验覆盖深度的典型例：

- **1559 个文件**，4 个 TV 季 + OVA/special
- 段1 accepted，段2 桥接 TMDB 系列 45893
- **83 个 assignment**：60 个 target（正篇各季按集标题/顺序对齐）+ 23 个 supplemental（特典按 TMDB 缺失或 supplemental 落位）+ 0 absent
- `final_verifier_passed = true`，0 issue

## 复现

```bash
# 段1 Local → Bangumi（mapping-only，Pi 真起）
python tools/run_local_bangumi_mapping_sample_pool.py --limit 999 --workers 10

# 段2 Bangumi → TMDB（mapping-only，Pi 真起）
python tools/run_bgm_to_tmdb_bridge_sample_pool.py --all --workers 10
```

产物写入 `tests/sample_pool/generated/`（本地 `.gitignore` 忽略）。
