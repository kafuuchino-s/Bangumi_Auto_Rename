#!/usr/bin/env node
// Pi AI 健康门禁：用与生产 Case Agent 完全相同的 /responses 链路 + provider/baseUrl/apiKey 配置，
// 起一个最小 agent session（挂 1 个 ping_reply customTool），验证两件事：
//   1. connectivity_ok：模型在配置的 endpoint 上能正常返回（不抛异常、有 assistant message）。
//   2. tool_call_ok  ：模型确实发起了对 ping_reply 的 tool_call。
// 旧 Python AIClient 门禁走 /chat/completions 与生产脱节，已替换为本脚本（见 src/ai/pi_healthcheck.py）。
//
// 用法：
//   node tools/pi_ai_healthcheck.mjs \
//     --provider bangumi-config-openai --model deepseek-v4-flash \
//     --base-url https://api.bbbc.eu.org --api openai-responses [--timeout 30]
//   环境变量 BAR_PI_CASE_AGENT_API_KEY 必须提供 API key。
//
// stdout 末行打印一行 JSON 结果，exit code 0=通过 / 1=失败（双重保险，Python 也可读 JSON）。

import { Type } from "typebox";
import {
  AuthStorage,
  SessionManager,
  createAgentSession,
  defineTool,
} from "@earendil-works/pi-coding-agent";

const API_KEY_ENV = "BAR_PI_CASE_AGENT_API_KEY";

function parseArgs(argv) {
  const out = { provider: "", model: "", baseUrl: "", api: "openai-responses", timeout: 30 };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === "--provider") out.provider = next();
    else if (a === "--model") out.model = next();
    else if (a === "--base-url") out.baseUrl = next();
    else if (a === "--api") out.api = next();
    else if (a === "--timeout") out.timeout = Number(next()) || 30;
  }
  return out;
}

function emit(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

const args = parseArgs(process.argv);
const apiKey = process.env[API_KEY_ENV] || "";

// 基础校验：缺必传项直接 fail，不起 session。
if (!args.model || !args.baseUrl || !apiKey) {
  emit({
    ok: false,
    connectivity_ok: false,
    tool_call_ok: false,
    model: args.model,
    provider: args.provider,
    base_url: args.baseUrl,
    reply_preview: "",
    elapsed_ms: 0,
    error: "missing required args: --model --base-url and env " + API_KEY_ENV,
  });
  process.exit(1);
}

// 与 pi_runner._prepare_pi_runtime_model_config 完全同口径的 model 对象。
const model = {
  id: args.model,
  name: `Healthcheck ${args.model}`,
  api: args.api,
  provider: args.provider,
  baseUrl: args.baseUrl.replace(/\/+$/, ""),
  reasoning: true,
  input: ["text"],
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  contextWindow: 400000,
  maxTokens: 32000,
};

// 最小 customTool：execute 就地执行，不起 tool server、不回连 Python。
const pingTool = defineTool({
  name: "ping_reply",
  label: "Ping Reply",
  description:
    "Reply with the given text verbatim. Call this tool exactly once to confirm you are operational, then stop.",
  parameters: Type.Object({ text: Type.String() }),
  execute: async (_toolCallId, params) => ({
    content: [{ type: "text", text: `pong: ${params?.text ?? ""}` }],
    details: {},
  }),
});

const startedAt = Date.now();
let connectivityOk = false;
let toolCallOk = false;
let replyPreview = "";
let errorMsg = "";

// wall-clock 超时兜底：session.prompt 不保证响应 AbortSignal，所以到点直接硬退出，
// 打超时 JSON + exit(1)，绝不静默挂死。
const timeoutMs = Math.max(5, args.timeout) * 1000;
let timedOut = false;
const timer = setTimeout(() => {
  timedOut = true;
  emit({
    ok: false,
    connectivity_ok: connectivityOk,
    tool_call_ok: toolCallOk,
    model: args.model,
    provider: args.provider,
    base_url: args.baseUrl,
    reply_preview: replyPreview,
    elapsed_ms: timeoutMs,
    error: `healthcheck timeout after ${args.timeout}s`,
  });
  process.exit(1);
}, timeoutMs);
// 不 unref：门禁超时兜底宁可可靠也不要“不阻止退出”的微优化。
// 正常完成路径会 clearTimeout(timer)，不会因 timer 拖延进程退出。

async function run() {
  const authStorage = AuthStorage.create();
  authStorage.setRuntimeApiKey(args.provider, apiKey);

  const { session } = await createAgentSession({
    model,
    authStorage,
    // 注意：tools 必须显式列出 customTool 名字。
    // tools:[] 会让 allowedToolNames 变成空集合，把 customTools 也一并滤掉
    // （agent-session.js _refreshToolRegistry 的 isAllowedTool 过滤），模型会拿不到工具。
    // 列出 ["ping_reply"] 既放行 customTool，又不启用 read/bash/edit/write 等内置工具。
    tools: ["ping_reply"],
    customTools: [pingTool],
    sessionManager: SessionManager.inMemory(),
  });

  session.subscribe((event) => {
    // tool_execution_start 是“模型确实发起了 tool_call”最直接的信号。
    if (event?.type === "tool_execution_start" && event?.toolName === "ping_reply") {
      toolCallOk = true;
    }
  });

  try {
    await session.prompt(
      "You are being health-checked. Call the `ping_reply` tool once with text=\"ok\", then reply with a single short sentence. Do not do anything else.",
    );
  } catch (e) {
    if (!timedOut) errorMsg = String(e?.message || e).slice(0, 300);
  }

  // prompt resolve 即 agent idle。读 messages 确认连通 + 兜底捞 toolCall。
  try {
    for (const msg of session.state?.messages ?? []) {
      if (msg?.role === "assistant") {
        connectivityOk = true;
        const blocks = Array.isArray(msg.content) ? msg.content : [];
        for (const b of blocks) {
          if (b?.type === "toolCall") toolCallOk = true;
          if (b?.type === "text" && b?.text && !replyPreview) {
            replyPreview = String(b.text).slice(0, 120);
          }
        }
      }
    }
  } catch {
    /* ignore message introspection errors */
  }

  try {
    session.dispose?.();
  } catch {
    /* ignore */
  }

  const ok = connectivityOk && toolCallOk;
  if (!ok && !errorMsg) {
    if (!connectivityOk) errorMsg = "model did not return an assistant message";
    else if (!toolCallOk) errorMsg = "model connected but did not issue a tool call";
  }

  clearTimeout(timer);
  emit({
    ok,
    connectivity_ok: connectivityOk,
    tool_call_ok: toolCallOk,
    model: args.model,
    provider: args.provider,
    base_url: args.baseUrl,
    reply_preview: replyPreview,
    elapsed_ms: Date.now() - startedAt,
    error: errorMsg,
  });
  process.exit(ok ? 0 : 1);
}

run().catch((e) => {
  clearTimeout(timer);
  emit({
    ok: false,
    connectivity_ok: false,
    tool_call_ok: false,
    model: args.model,
    provider: args.provider,
    base_url: args.baseUrl,
    reply_preview: "",
    elapsed_ms: Date.now() - startedAt,
    error: String(e?.message || e).slice(0, 300),
  });
  process.exit(1);
}).finally(() => clearTimeout(timer));
