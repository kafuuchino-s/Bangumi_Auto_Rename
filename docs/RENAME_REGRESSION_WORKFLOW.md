# Rename 回归工作流

以当前代码和本文档为准。

## 1. 当前目标

rename 回归做的是一条真实执行链，也是 sample-pool 的权威验证入口：

- 读取 `tests/sample_pool/manifest/manifest.json`
- 根据 sample JSON 重建最小源目录树
- 运行真实 `Rename.process(...)`
- 收集 task / record / output tree
- 与 baseline 对比
- 输出 `report.json` / `report.md`

样本池建立过程中不再维护独立 candidate/observer 流程。raw 样本进入验证资产的路径只能是：

```text
raw sample JSON
  -> src.regression.lanes.rename._execute_sample
  -> Rename.process(...)
  -> task / record / output tree
  -> baseline / observation review
```

因此，修 sample-pool 回归时就是在修真实重命名主流程；反过来，主流程行为变化也必须通过这条 lane 观察样本影响。

## 2. 关键位置

- CLI：`src/regression/cli.py`
- runner：`src/regression/runner.py`
- manifest：`src/regression/manifest.py`
- 执行实现：`src/regression/lanes/rename.py`
- compare：`src/regression/compare/rename.py`

lane 契约：

- `runner_kind = rename_lane_main_flow`
- `runtime_entrypoint = src.regression.lanes.rename._execute_sample -> src.rename.process.Rename.process`
- `uses_runtime_rename_process = true`
- `uses_shadow_candidate_logic = false`
- `authoritative_for_sample_pool = true`

`tools/run_sample_pool_main_flow_preview.py` 只是这条 lane 的薄包装，用于批量观察 raw sample；它不拥有独立判断逻辑。

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

## 9. 样本池建立规则

1. 新 raw 样本先进入 `tests/sample_pool/raw/`。
2. 用 `tools/generate_sample_pool_main_flow_manifest.py` 生成 manifest。
3. 用 `tools/run_sample_pool_main_flow_preview.py` 或 `src.regression.cli --mode full` 调用真实 rename lane。
4. 人工 review observation / task / record / output tree。
5. 若结果代表当前主流程正确行为，则用 `update-baseline` 固化 baseline。
6. 稳定且有保护价值的样本再设为 `check=true`。
7. 若结果暴露主流程问题，优先修 `src/rename/*`，再重跑同一 lane。

## 10. 当前原则

- 只维护 rename 回归
- 不保留兼容模式或历史别名
- 不保留无用框架包袱
- 不把不稳定样本硬塞进 `check`
- 不维护独立于 `Rename.process` 的样本池判断流程
