# AI / Pi 验证

Python 直连 AIClient 已移除。生产与测试统一走 Pi sidecar。

## 网关健康检查

在仓库根目录执行：

```powershell
$cfg = Get-Content data/config.json -Raw | ConvertFrom-Json
$env:BAR_PI_CASE_AGENT_API_KEY = [string]$cfg.ai_api_key
try {
  node tools/pi_ai_healthcheck.mjs `
    --provider bangumi-config-openai `
    --model $cfg.ai_model `
    --base-url $cfg.ai_base_url `
    --api openai-responses
} finally {
  Remove-Item Env:BAR_PI_CASE_AGENT_API_KEY -ErrorAction SilentlyContinue
}
```

健康检查同时验证 endpoint connectivity 和一次 Pi custom tool call。

## 样本池

Local→Bangumi：

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --limit 3 --workers 1
```

字幕 Case Agent 与 auto-fetch 使用各自的 Pi fake-runtime 单元测试。
