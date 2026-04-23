# Sample Pool

这个目录保存项目内置的原始样本池。

## 设计原则

- 每个样本以**一个 JSON**为主
- 默认只保留**运行时可见**的信息
- 不在样本本体里混入 `anime_info`、`first_air_date`、人工标签或人工解释
- 候选结果先由程序另行生成；确认后，可以再合并回样本 JSON 的可选字段中

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
- `generated/`：候选结果输出目录，由脚本生成；确认前先放这里，避免直接污染样本本体

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

确认后，也可以把程序产出的结果合并回同一个样本 JSON，例如：

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
- 候选结果生成阶段，仍然建议先输出到 `generated/`，确认后再合并回样本 JSON

## 自动化流程

1. 从真实目录或单文件批量扫描，生成 `raw/*.json`
2. 用程序读取 raw sample，生成候选结果到 `generated/`
3. 候选结果确认后，可回写到原样本 JSON 的 `confirmed_result`
4. 后续再从高价值样本中挑出需要持续精确确认的 case

当前阶段的目标是先把**原始样本池**和**候选结果生成流程**建立起来。

## 可用脚本

### 1. 批量生成 raw sample

```bash
python tools/generate_sample_pool_raw.py "H:\Anime\Anime Series" "tests/sample_pool/raw/tv" --limit 10
python tools/generate_sample_pool_raw.py "H:\Anime\Anime Movie" "tests/sample_pool/raw/movie" --limit 10
```

### 2. 生成候选结果

```bash
python tools/generate_sample_pool_candidates.py "tests/sample_pool/raw/tv" "tests/sample_pool/generated/tv"
python tools/generate_sample_pool_candidates.py "tests/sample_pool/raw/movie" "tests/sample_pool/generated/movie"
```

如果要批量并发生成，可以显式指定并发数，例如：

```bash
python tools/generate_sample_pool_candidates.py "tests/sample_pool/raw/tv" "tests/sample_pool/generated/tv" --concurrency 10
python tools/generate_sample_pool_candidates.py "tests/sample_pool/raw/movie" "tests/sample_pool/generated/movie" --concurrency 10
```

建议把 `10` 视为 aggressive 模式；如果网络波动或外部请求重试明显增多，可以退回 `6` 或 `4`。

当前仓库默认批量 candidate 生成并发就是 `10`，不传 `--concurrency` 时也会使用该值。

### 3. 将确认后的候选结果回写到样本 JSON

```bash
python tools/merge_sample_pool_confirmed_result.py \
  "tests/sample_pool/raw/tv/sample_0001_love_death_robots_s04.json" \
  "tests/sample_pool/generated/tv/sample_0001_love_death_robots_s04.candidate.json"
```

如果只想预览合并结果而不真正写回，可以加 `--stdout`。
