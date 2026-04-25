# Sample Pool

这个目录保存项目内置的原始样本池。

## 设计原则

- 每个样本以**一个 JSON**为主
- 默认只保留**运行时可见**的信息
- 不在样本本体里混入 `anime_info`、`first_air_date`、人工标签或人工解释
- 主流程 observation 由程序另行生成；确认前不污染样本本体

## 当前结构

```text
tests/sample_pool/
  raw/
    tv/
      sample_0001_love_death_robots_s04.json
    movie/
      sample_0001_seven_deadly_sins_grudge_of_edinburgh_part_1.json
  generated/
    .gitkeep
```

- `raw/`：样本池本体，只保存原始目录快照 JSON
- `generated/`：main-flow preview 输出目录，由脚本生成；确认前先放这里，避免直接污染样本本体

## raw sample JSON

每个样本一个 JSON，最小结构如下：

```json
{
  "root_name": "原始目录名或文件名",
  "files": [
    {
      "path": "相对路径或文件名",
      "size": 123456789
    }
  ]
}
```

- `root_name`：源目录名；单文件样本时为文件名
- `files[].path`：相对路径；单文件样本时直接是文件名
- `files[].size`：文件大小（字节）
- `files[].duration`：可选，后续若能稳定抽取再补充

若未来需要维护人工确认结果，可以把经过主流程验证和人工复核后的结果写回同一个样本 JSON，例如：

```json
{
  "root_name": "Love.Death.&.Robots.S04.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC",
  "files": [
    {
      "path": "Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv",
      "size": 266253022
    }
  ],
  "confirmed_result": {
    "type": "tv",
    "file_mapping": [
      {
        "file_path": "Love.Death.&.Robots.S04E01.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-ARiC.mkv",
        "tmdb_season": 4,
        "tmdb_episode": 1,
        "episode_type": "regular"
      }
    ]
  }
}
```

其中：

- `confirmed_result` 是**可选字段**
- 没确认过的样本，不需要这个字段
- 主流程 preview 阶段，仍然建议先输出到 `generated/`，确认后再决定是否写回样本 JSON

## 自动化流程

1. 从真实目录或单文件批量扫描，生成 `raw/*.json`
2. 用 `tools/generate_sample_pool_main_flow_manifest.py` 生成 main-flow preview manifest
3. 用 `tools/run_sample_pool_main_flow_preview.py` 在 sandbox 中调用真实 `Rename.process` 主流程
4. 后续只依据 main-flow observation 做样本池判断，不再维护独立观察流程

当前阶段的目标是让**原始样本池**通过真实主流程重新验证。

## 可用脚本

### 1. 批量生成 raw sample

```bash
python tools/generate_sample_pool_raw.py "H:\Anime\Anime Series" "tests/sample_pool/raw/tv" --limit 10
python tools/generate_sample_pool_raw.py "H:\Anime\Anime Movie" "tests/sample_pool/raw/movie" --limit 10
```

### 2. 生成 main-flow preview manifest

```bash
python tools/generate_sample_pool_main_flow_manifest.py \
  --raw-root "tests/sample_pool/raw" \
  --output "tests/sample_pool/manifest/manifest_p4_main_flow_full.json"
```

### 3. 跑真实主流程 preview

```bash
python tools/run_sample_pool_main_flow_preview.py \
  --manifest "tests/sample_pool/manifest/manifest_p4_main_flow_full.json" \
  --output-dir "tests/sample_pool/generated/main_flow_preview" \
  --workers 10
```

`generated/` 是本地运行产物，已被 `.gitignore` 忽略，不应提交。
