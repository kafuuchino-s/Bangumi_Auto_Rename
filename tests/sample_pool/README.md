# Sample Pool

这个目录现在只保留 Local→Bangumi 主线可复用的原始样本语料。

## 目录

```text
tests/sample_pool/
  raw/
    tv/
    movie/
```

`raw/` 里的 JSON 是从真实本地包抽出来的目录快照。它们是新主线仍然要复用的样本语料，不应该被删除。

旧流程生成目录已经移除：

- `generated/`
- `manifest/`
- `anchors/`

这些目录如果本地重新生成，会被 `.gitignore` 忽略。

## Raw JSON

每个样本通常包含：

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

当前阶段不要把旧运行 observation、summary 或 snapshot 写回这里。新的 Case Agent 样本 runner 后续应直接读取 `raw/`，并生成独立的 Local→Bangumi mapping-only 产物。
