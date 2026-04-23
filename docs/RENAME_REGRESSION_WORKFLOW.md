# Rename 回归工作流

以当前代码和本文档为准。

## 1. 当前目标

rename 回归做的是一条真实执行链：

- 读取 `tests/sample_pool/manifest/manifest.json`
- 根据 sample JSON 重建最小源目录树
- 运行真实 `Rename.process(...)`
- 收集 task / record / output tree
- 与 baseline 对比
- 输出 `report.json` / `report.md`

## 2. 关键位置

- CLI：`src/regression/cli.py`
- runner：`src/regression/runner.py`
- manifest：`src/regression/manifest.py`
- 执行实现：`src/regression/lanes/rename.py`
- compare：`src/regression/compare/rename.py`

数据位置：

- manifest：`tests/sample_pool/manifest/manifest.json`
- baseline：`data/regression/baselines/`
- run artifacts：`data/regression/runs/<run_id>/`

单次 run 关键产物：

- `manifest_snapshot.json`
- `run_context.json`
- `report.json`
- `report.md`
- `sample_results/<sample_id>.json`
- `sandbox/<sample_id>/attempt_n/...`

## 3. manifest 结构

当前 manifest 是 rename-only 的最小结构：

```json
{
  "manifest_version": "...",
  "entries": [
    {
      "sample_id": "...",
      "sample_json": "tests/sample_pool/raw/...json",
      "check": true,
      "anchor": false
    }
  ]
}
```

语义只有两条：

- `check = true`：进入 `check`
- `anchor = true`：启用更严格的 anchor compare

`full` 和 `update-baseline` 都会看到 manifest 中的全部样本；`check` 只看 `check=true` 的样本。

## 4. 模式

### `check`

- 日常主入口
- 阻塞式校验当前核心样本集

### `update-baseline`

- 有意刷新 baseline
- 跑完后需要人工 review
- 确认稳定后，再把对应样本改成 `check = true`

### `full`

- 人工观察
- 扩 coverage
- 跑不想纳入日常阻塞集的样本

## 5. 当前样本集

当前 `check` 集包含 5 个样本：

1. `sample_0117_love_death_robots_s04_1080p_nf_web_dl_ddp5_1_atmos_h_264_aric`
2. `sample_0014_the_seven_deadly_sins_grudge_of_edinburgh_part_1_2022_1080p_nf_web_dl_ddp5_1_x264_pterweb_mkv`
3. `sample_0013_the_seven_deadly_sins_cursed_by_light_2021_1080p_nf_web_dl_h_264_ddp_5_1_frogweb`
4. `sample_0006_bdrip_psycho_pass_providence_2023_7_acg`
5. `sample_0123_the_disastrous_life_of_saiki_k_s01_2016_1080p_bluray_x265_10bit_flac_2_0_ade`

当前 `sample_0091_vcb_studio_kimetsu_no_yaiba`：

- `check = false`
- `anchor = true`
- 只放在 `full` 里观察，不进日常阻塞集

## 6. 日常命令

```powershell
& ".venv\Scripts\python.exe" -m src.regression.cli --mode check
& ".venv\Scripts\python.exe" -m src.regression.cli --mode check --sample-id <sample_id>
& ".venv\Scripts\python.exe" -m src.regression.cli --mode full
& ".venv\Scripts\python.exe" -m src.regression.cli --mode full --sample-id <sample_id>
& ".venv\Scripts\python.exe" -m src.regression.cli --mode update-baseline --sample-id <sample_id>
```

## 7. baseline 刷新规则

当你要把新样本收进 `check`：

1. 先跑 `full` 或单样本 `update-baseline`
2. 人工 review `report / task / record / baseline`
3. 确认稳定后，把 manifest 里的 `check` 改成 `true`
4. 跑单样本 `check`
5. 再跑全量 `check`

## 8. 结果解释

- `passed`：和 baseline 一致
- `baseline_updated`：baseline 已刷新
- `baseline_missing`：还没有 baseline
- `observation_failed`：样本不在阻塞集里，但结果和 baseline 不一致
- `product_failed`：阻塞样本失配；在 `check` 中会导致失败
- `infra_failed`：执行异常
- `flaky`：首轮失败、重跑通过

## 9. 当前原则

- 只维护 rename 回归
- 不保留兼容模式或历史别名
- 不保留无用框架包袱
- 不把不稳定样本硬塞进 `check`
