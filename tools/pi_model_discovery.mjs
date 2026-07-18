#!/usr/bin/env node
// Discover models from an OpenAI-compatible gateway without persisting the API key.
// The Python API wrapper passes the key through BAR_PI_CASE_AGENT_API_KEY.

const API_KEY_ENV = "BAR_PI_CASE_AGENT_API_KEY";

function parseArgs(argv) {
  const out = { baseUrl: "", api: "openai-responses", timeout: 15 };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => argv[++i];
    if (arg === "--base-url") out.baseUrl = next();
    else if (arg === "--api") out.api = next();
    else if (arg === "--timeout") out.timeout = Number(next()) || 15;
  }
  return out;
}

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function modelIdList(payload) {
  const values = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.data)
      ? payload.data
      : Array.isArray(payload?.models)
        ? payload.models
        : [];
  const ids = new Set();
  for (const value of values) {
    const id = typeof value === "string" ? value : value?.id;
    if (typeof id === "string" && id.trim()) ids.add(id.trim());
  }
  return [...ids].sort((a, b) => a.localeCompare(b));
}

function candidateUrls(baseUrl) {
  const normalized = String(baseUrl || "").trim().replace(/\/+$/, "");
  if (!normalized) return [];

  let parsed;
  try {
    parsed = new URL(normalized);
  } catch {
    return [];
  }
  parsed.search = "";
  parsed.hash = "";

  const path = parsed.pathname.replace(/\/+$/, "");
  const candidates = [];
  const add = (pathname) => {
    const candidate = new URL(parsed.toString());
    candidate.pathname = pathname || "/";
    candidate.search = "";
    candidate.hash = "";
    candidates.push(candidate.toString().replace(/\/+$/, ""));
  };

  add(`${path}/models`);
  if (path.endsWith("/v1")) {
    add(`${path.slice(0, -3) || ""}/models`);
  } else {
    add(`${path}/v1/models`);
  }
  return [...new Set(candidates)];
}

function safeProviderMessage(status, body) {
  if (status === 401 || status === 403) return "API key 无效或未授权";
  if (status === 404 || status === 405) return "模型列表接口不存在";
  if (status >= 500) return "模型服务暂时不可用";
  const detail = typeof body?.error?.message === "string" ? body.error.message : "";
  return detail ? detail.slice(0, 160) : `模型列表请求失败（HTTP ${status}）`;
}

async function fetchModels(url, apiKey, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      signal: controller.signal,
    });
    const text = await response.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = null;
    }
    return { response, body };
  } finally {
    clearTimeout(timer);
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const apiKey = process.env[API_KEY_ENV] || "";
  if (!args.baseUrl || !apiKey) {
    emit({ ok: false, code: "incomplete_config", status: 400, models: [], error: "缺少模型接口地址或 API key" });
    process.exitCode = 1;
    return;
  }
  if (!new Set(["openai-responses", "openai-completions"]).has(args.api)) {
    emit({ ok: false, code: "unsupported_protocol", status: 400, models: [], error: "当前协议不支持自动拉取模型列表" });
    process.exitCode = 1;
    return;
  }

  const candidates = candidateUrls(args.baseUrl);
  let lastError = "模型列表请求失败";
  let lastStatus = 0;
  for (const url of candidates) {
    try {
      const { response, body } = await fetchModels(url, apiKey, Math.max(5, args.timeout) * 1000);
      if (response.ok) {
        emit({ ok: true, code: "ok", status: response.status, models: modelIdList(body) });
        return;
      }
      lastStatus = response.status;
      lastError = safeProviderMessage(response.status, body);
      // A gateway may expose the OpenAI-compatible endpoint under /v1.
      // Only fall through for a missing endpoint; auth/server errors are final.
      if (response.status !== 404 && response.status !== 405) break;
    } catch (error) {
      if (error?.name === "AbortError") {
        lastError = "模型列表请求超时";
        lastStatus = 408;
      } else {
        lastError = "无法连接模型接口";
        lastStatus = 0;
      }
    }
  }

  emit({
    ok: false,
    code: lastStatus === 401 || lastStatus === 403 ? "unauthorized" : "provider_error",
    status: lastStatus,
    models: [],
    error: lastError,
  });
  process.exitCode = 1;
}

main().catch((error) => {
  emit({
    ok: false,
    code: "provider_error",
    status: 0,
    models: [],
    error: String(error?.message || error).slice(0, 160),
  });
  process.exitCode = 1;
});
