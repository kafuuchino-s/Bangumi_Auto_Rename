#!/usr/bin/env node

// 字幕自动抓取 Case Agent Pi sidecar（Phase 3）。
//
// 对齐 tools/pi_subtitle_case_agent_runner.mjs 的工具分发契约，但 auto_fetch 是
// candidate ranking（选帖/选包），不是 mapping。工具面 6 个：
// get_auto_fetch_context / search_candidates / load_candidate_packages /
// inspect_package / submit_candidate / submit_package / fail_closed / need_confirm。
//
// 与 subtitle sidecar 一致的基础设施：argv 解析、callPythonTool、proxyTool、
// Pi 会话创建、事件捕获、final 轮询 + nudge、产物写出。

import {
  AuthStorage,
  createAgentSession,
  DefaultResourceLoader,
  defineTool,
  ModelRegistry,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import fs from "node:fs/promises";
import path from "node:path";

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith("--")) continue;
    const name = key.slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : "true";
    args[name] = value;
  }
  return args;
}

const args = parseArgs(process.argv);
const inputPath = args.input;
const outputPath = args.output;
const server = args.server;
const token = args.token;
const repoRoot = args["repo-root"] || process.cwd();
const agentDir = args["agent-dir"] || process.env.PI_CODING_AGENT_DIR || "";
const provider = args.provider || "";
const modelId = args.model || "";
const runtimeApiKeyEnv = args["api-key-env"] || "BAR_PI_CASE_AGENT_API_KEY";

const NATIVE_TOOL_NAMES = ["read"];
const EXTENSION_TOOL_NAMES = ["goal_complete"];
const RETRY_STALL_TIMEOUT_ENV = "PI_RETRY_STALL_TIMEOUT_MS";
if (!process.env[RETRY_STALL_TIMEOUT_ENV]) {
  process.env[RETRY_STALL_TIMEOUT_ENV] = "0";
}
const EXTENSION_PATHS = [
  path.join(repoRoot, "node_modules", "@narumitw", "pi-goal", "src", "goal.ts"),
  path.join(repoRoot, "node_modules", "@narumitw", "pi-retry", "src", "retry.ts"),
];
const REQUIRED_SKILL_NAMES = ["auto-fetch-contract"];
const PRIMARY_SKILL_NAME = "auto-fetch-contract";
const PRIMARY_SKILL_LOAD_COMMAND =
  `/skill:${PRIMARY_SKILL_NAME} Load this skill as method context for the upcoming auto fetch task. ` +
  "During this skill-load step only, do not run fetch tools; the next prompt is the task to execute immediately.";

const ACTION_AGENT_OUTPUT_CONTRACT = [
  "Visible output contract: act through tools and artifacts, not reasoning prose.",
  "Do not write headings such as Deciding, Evaluating, Considering, or explain why a tool should be called.",
  "When a custom tool or goal_complete is available, call it directly with no prose; otherwise write one short blocker sentence.",
  "Do not print candidate tables, full verifier issues, or old artifact excerpts in assistant text.",
  "Tool arguments count as output: keep reasons and summaries compact.",
  "First move: get_auto_fetch_context to read the missing videos and scan scope.",
  "Then search_candidates / inspect / submit_candidate -> submit_package.",
].join("\n");

const ACTION_AGENT_SYSTEM_PROMPT_SECTION = `
## Auto Fetch Action Agent Output Protocol

This session runs as an action-oriented subtitle fetch agent. Reason internally, then externalize durable work through custom tools and artifacts.

- Assistant-visible text is a status channel, not a scratchpad.
- On a turn that can call a custom tool or goal_complete, call the tool directly and omit explanatory prose.
- Do not print chain-of-thought, self-review headings, candidate tables, or copied tool JSON.
- A normal non-final text-only response is at most one short blocker sentence naming the next missing fact or terminal fail_closed reason.
- When the candidate is selected, submit_candidate is the next visible action; then load packages and submit_package.
`.trim();

if (!inputPath || !outputPath || !server || !token) {
  throw new Error("required args: --input --output --server --token");
}

const caseInput = JSON.parse(await fs.readFile(inputPath, "utf8"));
const eventLog = [];
const assistantMessages = [];
let turnCount = 0;
let authStorage;
let modelRegistry;
let selectedModel;
let assistantTextBuffer = "";
let assistantLogCharCount = 0;
const MAX_ASSISTANT_LOG_CHARS = 200000;

if (agentDir || provider || modelId) {
  authStorage = AuthStorage.create(agentDir ? path.join(agentDir, "auth.json") : undefined);
  if (provider && process.env[runtimeApiKeyEnv]) {
    authStorage.setRuntimeApiKey(provider, process.env[runtimeApiKeyEnv]);
    delete process.env[runtimeApiKeyEnv];
  }
  modelRegistry = ModelRegistry.create(authStorage, agentDir ? path.join(agentDir, "models.json") : undefined);
  if (provider && modelId) {
    selectedModel = modelRegistry.find(provider, modelId);
    if (!selectedModel) {
      throw new Error(`Pi model not found: provider=${provider} model=${modelId}`);
    }
  }
}

const Json = {
  Array: (items) => ({ type: "array", items }),
  Boolean: () => ({ type: "boolean" }),
  Number: () => ({ type: "number" }),
  Object: (properties, required = []) => ({
    type: "object",
    properties,
    required,
    additionalProperties: false,
  }),
  Optional: (schema) => ({ ...schema, optional: true }),
  String: () => ({ type: "string" }),
};

function objectSchema(properties) {
  const required = Object.entries(properties)
    .filter(([, schema]) => !schema.optional)
    .map(([name]) => name);
  const cleanProperties = Object.fromEntries(
    Object.entries(properties).map(([name, schema]) => {
      const { optional: _optional, ...cleanSchema } = schema;
      return [name, cleanSchema];
    }),
  );
  return Json.Object(cleanProperties, required);
}

const selectionQuickReference = [
  "Selection workflow: search_candidates(keyword) -> inspect candidate titles/arcs -> submit_candidate(candidate_ref, language, reason) -> load_candidate_packages(candidate_ref) -> inspect_package(package_ref) -> submit_package(package_ref, reason).",
  "Use the CD<idx> candidate refs and PK<idx> package refs shown in context / search results. Titles, detail URLs, and filenames are evidence only; submit must reference the short refs.",
  "Each MV<idx> missing video card has both `video` (post-rename target filename) and `source_video` (pre-rename local original filename, evidence only, may be empty). When the subtitle release group / naming matches the original local files, `source_video` is a stronger pairing hint; prefer it for matching when non-empty.",
  "submit_candidate gate: candidate must have downloadable attachment or packages.",
  "submit_package gate: package must have a downloadable link and must NOT be font/patch-only (needs batch/simplified/traditional/bilingual marker).",
  "If no candidate matches the arc (wrong season/OVA/特别篇), call fail_closed with concrete reason. If genuinely uncertain between candidates, call need_confirm.",
  "Do not download files inside the agent; submit_package returns the selection for the Python layer to download.",
].join("\n");

async function callPythonTool(tool, toolArgs) {
  const response = await fetch(`${server}/tool`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token, tool, arguments: toolArgs || {} }),
  });
  const text = await response.text();
  let payload;
  try {
    payload = JSON.parse(text || "{}");
  } catch {
    payload = { ok: false, error: text };
  }
  if (!response.ok) {
    return { ok: false, error: payload.error || `HTTP ${response.status}`, payload };
  }
  return payload;
}

async function readFinalResult() {
  const response = await fetch(`${server}/final`);
  if (!response.ok) return { ok: false, final_result: null };
  return await response.json();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function effectiveRuntimeBudgetSeconds() {
  const timeoutSeconds = Number(caseInput.runtime_policy?.wall_clock_timeout_seconds || 0);
  const finishBeforeSeconds = Number(caseInput.runtime_policy?.suggested_finish_before_seconds || 0);
  if (timeoutSeconds > 5) {
    return Math.max(1, Math.min(timeoutSeconds - 2, Math.max(finishBeforeSeconds, timeoutSeconds - 5)));
  }
  return finishBeforeSeconds || Math.max(1, timeoutSeconds || 30);
}

function safePreview(value, limit = 2000) {
  let text;
  try {
    text = typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    text = String(value);
  }
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit)}...truncated...` : text;
}

function summarizeEvent(event) {
  const row = { type: event.type, turn_count: turnCount, at: new Date().toISOString() };
  if ("toolName" in event && event.toolName) row.tool_name = String(event.toolName);
  if ("toolCallId" in event && event.toolCallId) row.tool_call_id = String(event.toolCallId);
  if (event.type === "tool_execution_start" || event.type === "tool_execution_update") {
    row.args_preview = safePreview(event.args);
    row.status = event.status || "";
  }
  if (event.type === "tool_execution_end") {
    row.is_error = Boolean(event.isError);
    row.result_preview = safePreview(event.result);
    row.status = event.status || "";
  }
  if (event.type === "message_start" || event.type === "message_end") {
    const role = event.message?.role;
    if (role) row.role = String(role);
  }
  if (event.type === "message_update") {
    const updateType = event.assistantMessageEvent?.type;
    if (updateType) row.message_event_type = String(updateType);
  }
  return row;
}

function captureAssistantDelta(event) {
  if (event.type !== "message_update") return;
  const assistantEvent = event.assistantMessageEvent;
  if (assistantEvent?.type !== "text_delta") return;
  const rawDelta = assistantEvent?.delta ?? "";
  const delta = typeof rawDelta === "string" ? rawDelta : "";
  if (!delta || assistantLogCharCount >= MAX_ASSISTANT_LOG_CHARS) return;
  const room = MAX_ASSISTANT_LOG_CHARS - assistantLogCharCount;
  const clipped = delta.length > room ? delta.slice(0, room) : delta;
  assistantTextBuffer += clipped;
  assistantLogCharCount += clipped.length;
}

function extractMessageText(message) {
  const content = message?.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const parts = [];
  for (const block of content) {
    if (typeof block === "string") {
      parts.push(block);
    } else if (block?.type === "text" && typeof block.text === "string") {
      parts.push(block.text);
    } else if (typeof block?.content === "string") {
      parts.push(block.content);
    }
  }
  return parts.join("");
}

function flushAssistantMessage() {
  const text = assistantTextBuffer.trim();
  if (text) {
    assistantMessages.push({ turn_count: turnCount, text });
  }
  assistantTextBuffer = "";
}

function buildAssistantOutputStats() {
  const lengths = assistantMessages.map((message) => (message.text || "").length);
  const totalTextChars = lengths.reduce((total, length) => total + length, 0);
  return {
    assistant_message_count: assistantMessages.length,
    total_text_chars: totalTextChars,
    max_text_chars: lengths.length ? Math.max(...lengths) : 0,
    long_text_message_count: lengths.filter((length) => length > 1000).length,
    log_char_count: assistantLogCharCount,
    log_truncated: assistantLogCharCount >= MAX_ASSISTANT_LOG_CHARS,
  };
}

function discoverRequiredSkills(resourceLoader) {
  const loadedSkills = resourceLoader.getSkills().skills || [];
  const discovered = [];
  const missing = [];
  for (const skillName of REQUIRED_SKILL_NAMES) {
    const skill = loadedSkills.find((candidate) => candidate.name === skillName);
    if (!skill) {
      missing.push(skillName);
      continue;
    }
    discovered.push({
      name: skill.name,
      description: skill.description || "",
      filePath: skill.filePath || "",
      baseDir: skill.baseDir || "",
    });
  }
  return { discovered, missing };
}

function stripMarkdownFrontmatter(text) {
  if (!text.startsWith("---")) return text;
  const normalized = text.replace(/\r\n/g, "\n");
  const end = normalized.indexOf("\n---", 3);
  if (end === -1) return text;
  const after = normalized.indexOf("\n", end + 4);
  return after === -1 ? "" : normalized.slice(after + 1);
}

async function buildSkillExpansionFallback(requiredSkillDiscovery) {
  const skill = requiredSkillDiscovery.discovered.find((item) => item.name === PRIMARY_SKILL_NAME);
  const skillPath = skill?.filePath || path.join(repoRoot, ".pi", "skills", PRIMARY_SKILL_NAME, "SKILL.md");
  const baseDir = skill?.baseDir || path.dirname(skillPath);
  const rawSkill = await fs.readFile(skillPath, "utf8");
  const body = stripMarkdownFrontmatter(rawSkill).trim();
  return `<skill name="${PRIMARY_SKILL_NAME}" location="${skillPath}">
References are relative to ${baseDir}.

${body}
</skill>

User: Load this skill as method context for the upcoming auto fetch task. During this skill-load step only, do not run fetch tools; the next prompt is the task to execute immediately.`;
}

async function promptWithResult(session, text, options = {}) {
  try {
    await session.prompt(text, { expandPromptTemplates: true, source: "api", ...options });
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error?.stack || error?.message || String(error) };
  }
}

function proxyTool(name, label, description, parameters, options = {}) {
  const promptMetadata = {};
  if (options.promptSnippet) promptMetadata.promptSnippet = options.promptSnippet;
  if (options.promptGuidelines) promptMetadata.promptGuidelines = options.promptGuidelines;
  return defineTool({
    name,
    label,
    description,
    ...promptMetadata,
    parameters,
    executionMode: "sequential",
    async execute(_toolCallId, params) {
      const result = await callPythonTool(name, params);
      const text = JSON.stringify(result, null, 2);
      return {
        content: [{ type: "text", text: text.length > 60000 ? `${text.slice(0, 60000)}\n...truncated...` : text }],
        details: result,
      };
    },
  });
}

const tools = [
  proxyTool(
    "get_auto_fetch_context",
    "Get Auto Fetch Context",
    "Read the fixed-layer scan scope, missing video cards, keywords, and loaded candidates.",
    objectSchema({ detail: Json.Optional(Json.Boolean()) }),
    {
      promptSnippet: "Inspect missing videos and scan scope",
      promptGuidelines: [
        "Read the MV<idx> missing video cards and scan scope.",
        "Use source_video hint when subtitle naming matches local original.",
      ],
    },
  ),
  proxyTool(
    "search_candidates",
    "Search Candidates",
    `Search the subtitle provider for candidates by keyword and load them as CD<idx> facts.\n${selectionQuickReference}`,
    objectSchema({ keyword: Json.String(), limit: Json.Optional(Json.Number()) }),
    {
      promptSnippet: "Search the provider for subtitle candidates",
      promptGuidelines: [
        "Use a keyword derived from the missing video title / source_video hint.",
        "Inspect returned candidate titles to pick the matching arc.",
      ],
    },
  ),
  proxyTool(
    "load_candidate_packages",
    "Load Candidate Packages",
    "Deep-load a candidate's thread packages as PK<idx> facts.",
    objectSchema({ candidate_ref: Json.String() }),
    {
      promptSnippet: "Deep-load packages for a candidate",
      promptGuidelines: ["Call after submit_candidate to populate package cards."],
    },
  ),
  proxyTool(
    "inspect_package",
    "Inspect Package",
    "Read a package's full details (post_text, links, flags) to judge main episode vs special/font.",
    objectSchema({ package_ref: Json.String() }),
    {
      promptSnippet: "Inspect a package before selecting it",
      promptGuidelines: ["Avoid font/patch-only or special-only packages for main episodes."],
    },
  ),
  proxyTool(
    "submit_candidate",
    "Submit Candidate",
    `Select a candidate thread + language. Gate: candidate must be downloadable.\n${selectionQuickReference}`,
    objectSchema({
      candidate_ref: Json.String(),
      language: Json.Optional(Json.String()),
      reason: Json.Optional(Json.String()),
    }),
    {
      promptSnippet: "Select the matching candidate thread",
      promptGuidelines: ["Submit the candidate whose arc matches the missing videos."],
    },
  ),
  proxyTool(
    "submit_package",
    "Submit Package",
    `Select a package to download. Gate: downloadable + not font/patch-only. This is the terminal accepted path.\n${selectionQuickReference}`,
    objectSchema({ package_ref: Json.String(), reason: Json.Optional(Json.String()) }),
    {
      promptSnippet: "Select the package to fetch",
      promptGuidelines: [
        "Submit only after load_candidate_packages + inspect_package.",
        "This is the terminal structured-output path for normal completion.",
      ],
    },
  ),
  proxyTool(
    "fail_closed",
    "Fail Closed",
    "Finish safely when no candidate matches the arc or evidence is insufficient.",
    objectSchema({
      reason: Json.String(),
      reason_kind: Json.Optional(Json.String()),
      related_refs: Json.Optional(Json.Array(Json.String())),
    }),
    {
      promptSnippet: "Finish safely with fail_closed",
      promptGuidelines: ["Use fail_closed for concrete wrong-arc / no-candidate situations."],
    },
  ),
  proxyTool(
    "need_confirm",
    "Need Confirm",
    "Finish safely when genuinely uncertain between candidates (needs human).",
    objectSchema({ reason: Json.String() }),
    {
      promptSnippet: "Finish with need_confirm",
      promptGuidelines: ["Use need_confirm only when genuinely ambiguous, not as a shortcut."],
    },
  ),
];

const customToolNames = tools.map((tool) => tool.name);
const enabledToolNames = [...NATIVE_TOOL_NAMES, ...EXTENSION_TOOL_NAMES, ...customToolNames];

async function fileExists(filePath) {
  if (!filePath) return false;
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function readJsonFile(filePath) {
  if (!(await fileExists(filePath))) return null;
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch {
    return null;
  }
}

async function readToolTraceRows() {
  const tracePath = path.join(path.dirname(outputPath), "tool_trace.jsonl");
  if (!(await fileExists(tracePath))) return [];
  try {
    const text = await fs.readFile(tracePath, "utf8");
    return text
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => {
        try {
          return JSON.parse(line);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
  } catch {
    return [];
  }
}

const GATE_FEEDBACK_TOOL_NAMES = new Set(["submit_candidate", "submit_package"]);

function isGateFeedbackTraceRow(row) {
  const name = String(row?.tool || "");
  if (!GATE_FEEDBACK_TOOL_NAMES.has(name)) return false;
  const summary = row?.result_summary && typeof row.result_summary === "object" ? row.result_summary : {};
  const status = String(summary.status || "").trim().toLowerCase();
  return (
    "verifier_passed" in summary
    || Number(summary.verifier_issue_count || 0) > 0
    || ["invalid", "accepted", "candidate_accepted"].includes(status)
  );
}

function latestGateFeedbackTraceRow(traceRows) {
  return [...traceRows].reverse().find(isGateFeedbackTraceRow) || null;
}

async function readLatestGateNudgeLines() {
  const artifactsDir = path.join(path.dirname(outputPath), "artifacts");
  const verifierPath = path.join(artifactsDir, "auto_fetch_verifier_result.json");
  if (!(await fileExists(verifierPath))) return [];
  try {
    const verifier = await readJsonFile(verifierPath);
    const issues = Array.isArray(verifier?.issues) ? verifier.issues : [];
    if (verifier?.passed === true) {
      return ["Latest gate: accepted. Next: proceed to the next submit step."];
    }
    const lines = [];
    if (issues.length) {
      lines.push(`Latest gate: invalid, issue_count=${issues.length}.`);
      for (const issue of issues.slice(0, 6)) {
        const code = issue.issue_code || "issue";
        const ref = issue.ref || "";
        const message = issue.message || "";
        lines.push(`- ${code}${ref ? ` ${ref}` : ""}: ${message}`);
      }
    }
    lines.push("Next: patch the named issue and resubmit, or concrete fail_closed / need_confirm.");
    return lines;
  } catch {
    return [];
  }
}

async function readRunnerProgressNudgeLines() {
  const lines = [];
  const traceRows = await readToolTraceRows();
  const toolNames = traceRows.map((row) => String(row.tool || "")).filter(Boolean);
  const recentTools = toolNames.slice(-6);
  const uniqueTools = [...new Set(toolNames)];
  if (!toolNames.length) {
    lines.push("Progress so far: no fetch tool calls were completed, and no final result exists.");
  } else {
    lines.push(`Progress so far: ${toolNames.length} fetch tool call(s); recent tools: ${recentTools.join(", ") || "none"}.`);
    lines.push(`Completed tool types: ${uniqueTools.join(", ")}.`);
  }
  const contextCalls = toolNames.filter((name) => name === "get_auto_fetch_context").length;
  const searchCalls = toolNames.filter((name) => name === "search_candidates").length;
  const submitCandCalls = toolNames.filter((name) => name === "submit_candidate").length;
  const submitPkgCalls = toolNames.filter((name) => name === "submit_package").length;
  if (!contextCalls && toolNames.length) {
    lines.push("Next: call get_auto_fetch_context to read missing videos and scan scope.");
  }
  if (contextCalls && !searchCalls && !submitCandCalls) {
    lines.push("Next: search_candidates with a title/source hint, then submit_candidate.");
  }
  if (submitCandCalls && !submitPkgCalls) {
    lines.push("Candidate accepted but no package submitted. load_candidate_packages + inspect_package + submit_package.");
  }
  return lines;
}

async function waitForFinalResultOrIdle(session, promptDone, options = {}) {
  const defaultWaitMs = Math.max(1_000, effectiveRuntimeBudgetSeconds() * 1_000);
  const waitMs = Math.max(1_000, Number(options.waitMs || 0) || defaultWaitMs);
  const deadline = Date.now() + waitMs;
  let idleSince = 0;
  let lastPayload = await readFinalResult();
  let waitIterations = 0;
  const promptState = { settled: false, outcome: null };
  promptDone.then((outcome) => {
    promptState.settled = true;
    promptState.outcome = outcome;
  });
  while (Date.now() < deadline) {
    waitIterations += 1;
    if (lastPayload.final_result) {
      return { payload: lastPayload, waitIterations, wait_timeout_ms: waitMs, idle_drained: false, prompt_settled: promptState.settled, prompt_error: promptState.outcome?.ok === false ? promptState.outcome.error : "" };
    }
    await sleep(500);
    lastPayload = await readFinalResult();
    if (lastPayload.final_result) {
      return { payload: lastPayload, waitIterations, wait_timeout_ms: waitMs, idle_drained: false, prompt_settled: promptState.settled, prompt_error: promptState.outcome?.ok === false ? promptState.outcome.error : "" };
    }
    const busy = Boolean(session.isStreaming || session.pendingMessageCount > 0 || !promptState.settled);
    if (busy && promptState.outcome?.ok !== false) {
      idleSince = 0;
      await sleep(250);
      continue;
    }
    if (!idleSince) idleSince = Date.now();
    if (Date.now() - idleSince >= 3_000) {
      return { payload: lastPayload, waitIterations, wait_timeout_ms: waitMs, idle_drained: true, prompt_settled: promptState.settled, prompt_error: promptState.outcome?.ok === false ? promptState.outcome.error : "" };
    }
    await sleep(250);
  }
  return { payload: lastPayload, waitIterations, wait_timeout_ms: waitMs, idle_drained: false, prompt_settled: promptState.settled, prompt_error: promptState.outcome?.ok === false ? promptState.outcome.error : "" };
}

async function waitForFinalResultWithNudge(session, promptDone) {
  const startedAt = Date.now();
  const totalBudgetMs = Math.max(30_000, effectiveRuntimeBudgetSeconds() * 1_000);
  const firstWaitMs = Math.min(totalBudgetMs, Math.max(30_000, Math.min(45_000, Math.floor(totalBudgetMs * 0.15))));
  let finalWait = await waitForFinalResultOrIdle(session, promptDone, { waitMs: firstWaitMs });
  const nudgeAttempts = [];
  if (finalWait.payload?.final_result) {
    return { ...finalWait, nudge_attempts: nudgeAttempts };
  }

  const gateNudgeLines = await readLatestGateNudgeLines();
  const progressNudgeLines = await readRunnerProgressNudgeLines();
  const nudgeText = [
    "Checkpoint: continue as an auto fetch action agent.",
    "If a tool action is available, call it now with no explanation. Otherwise provide one concrete blocker sentence.",
    "- get_auto_fetch_context",
    "- search_candidates",
    "- submit_candidate",
    "- load_candidate_packages + inspect_package",
    "- submit_package",
    "- patch named gate issue",
    "- concrete fail_closed / need_confirm",
    ...progressNudgeLines,
    ...gateNudgeLines,
    "Do not show reasoning narrative, reread skills, or inspect old artifacts.",
  ].join("\n");
  const nudgeDone = session
    .prompt(nudgeText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
    .then(() => ({ ok: true }))
    .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
  const remainingMs = Math.max(30_000, totalBudgetMs - firstWaitMs);
  const nudgeWait = await waitForFinalResultOrIdle(session, nudgeDone, { waitMs: Math.min(90_000, remainingMs) });
  nudgeAttempts.push({ phase: "checkpoint", wait_iterations: nudgeWait.waitIterations, wait_timeout_ms: nudgeWait.wait_timeout_ms, idle_drained: nudgeWait.idle_drained, prompt_settled: nudgeWait.prompt_settled, prompt_error: nudgeWait.prompt_error, final_result_present: Boolean(nudgeWait.payload?.final_result) });
  if (nudgeWait.payload?.final_result) {
    return { ...nudgeWait, nudge_attempts: nudgeAttempts };
  }

  const hardRemainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
  const hardWaitMs = Math.min(90_000, hardRemainingMs);
  if (hardWaitMs >= 20_000) {
    const progressLines = await readRunnerProgressNudgeLines();
    const latestGateLines = await readLatestGateNudgeLines();
    const hardFinishText = [
      "Hard finish checkpoint: act or close. Do not narrate the decision.",
      "This turn must be exactly one custom tool call or fail_closed/need_confirm; no prose.",
      "- submit_package",
      "- patch named gate issue",
      "- concrete fail_closed / need_confirm",
      ...progressLines,
      ...latestGateLines,
      "Budget pressure is not a fail_closed reason.",
    ].join("\n");
    const hardDone = session
      .prompt(hardFinishText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
      .then(() => ({ ok: true }))
      .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
    const hardWait = await waitForFinalResultOrIdle(session, hardDone, { waitMs: hardWaitMs });
    nudgeAttempts.push({ phase: "hard_finish", wait_iterations: hardWait.waitIterations, wait_timeout_ms: hardWait.wait_timeout_ms, idle_drained: hardWait.idle_drained, prompt_settled: hardWait.prompt_settled, prompt_error: hardWait.prompt_error, final_result_present: Boolean(hardWait.payload?.final_result) });
  }
  return { ...finalWait, nudge_attempts: nudgeAttempts };
}

const result = {
  ok: false,
  status: "invalid",
  input_path: inputPath,
  output_path: outputPath,
  case_id: caseInput.sample_id || "auto-fetch-case-agent",
  instruction_path: path.join(path.dirname(outputPath), "pi_auto_fetch_goal_instructions.md"),
  event_log_path: path.join(path.dirname(outputPath), "pi_auto_fetch_event_log.json"),
  assistant_log_path: path.join(path.dirname(outputPath), "pi_auto_fetch_assistant_messages.json"),
};

let requiredSkillDiscovery = { discovered: [], missing: [] };
let extensionLoadErrors = [];
let forcedSkillLoadTelemetry = { attempted: false, succeeded: false, error: "", fallback: false, fallback_succeeded: false, fallback_error: "" };

const instructionText = `
Complete this subtitle auto fetch case.
Read the case input JSON at: ${inputPath}
Use get_auto_fetch_context for the missing video cards (MV<idx>) and scan scope.
${selectionQuickReference}
Search candidates matching the missing videos' title / source_video hint. Inspect candidate arcs; submit the matching candidate + language. Then load packages, inspect the main-episode package, and submit_package. Then call goal_complete.
If no candidate matches the arc (wrong season / OVA / 特别篇), call fail_closed with a concrete reason. If genuinely uncertain between candidates, call need_confirm.
Do not use native tools to download, move, copy, link, rename, or inspect old run artifacts for answers.
Available lazy skills:
/skill:auto-fetch-contract: use when selection workflow, ref policy, or gate repair is unclear.
${ACTION_AGENT_OUTPUT_CONTRACT}
Try to finish before ${caseInput.runtime_policy?.suggested_finish_before_seconds ?? 0} seconds.
`.trim();

const goalObjective = `
Produce an accepted candidate + package selection for fetching a subtitle archive for the missing videos, or fail closed / need confirm for global ambiguity. This is dry-run only (no download inside the agent).

${ACTION_AGENT_OUTPUT_CONTRACT}
`.trim();

try {
  const effectiveAgentDir = agentDir || process.env.PI_CODING_AGENT_DIR || path.join(repoRoot, ".pi", "agent");
  const resourceLoader = new DefaultResourceLoader({
    cwd: repoRoot,
    agentDir: effectiveAgentDir,
    additionalExtensionPaths: EXTENSION_PATHS,
    appendSystemPromptOverride: (base) => [...base, ACTION_AGENT_SYSTEM_PROMPT_SECTION],
  });
  await resourceLoader.reload();
  extensionLoadErrors = resourceLoader.getExtensions().errors || [];
  requiredSkillDiscovery = discoverRequiredSkills(resourceLoader);
  const { session } = await createAgentSession({
    cwd: repoRoot,
    agentDir: effectiveAgentDir,
    authStorage,
    modelRegistry,
    model: selectedModel,
    resourceLoader,
    tools: enabledToolNames,
    customTools: tools,
    sessionManager: SessionManager.inMemory(repoRoot),
  });
  try {
    session.subscribe((event) => {
      if (event.type === "turn_start") {
        turnCount += 1;
      }
      captureAssistantDelta(event);
      if (event.type === "message_end") {
        if (!assistantTextBuffer.trim() && event.message?.role === "assistant") {
          assistantTextBuffer = extractMessageText(event.message);
        }
        flushAssistantMessage();
      }
      eventLog.push(summarizeEvent(event));
    });
    forcedSkillLoadTelemetry.attempted = true;
    const discoveredPrimarySkill = requiredSkillDiscovery.discovered.some((item) => item.name === PRIMARY_SKILL_NAME);
    if (discoveredPrimarySkill) {
      const skillLoad = await promptWithResult(session, PRIMARY_SKILL_LOAD_COMMAND);
      forcedSkillLoadTelemetry.succeeded = Boolean(skillLoad.ok);
      if (!skillLoad.ok) {
        forcedSkillLoadTelemetry.error = skillLoad.error || "";
      }
    } else {
      forcedSkillLoadTelemetry.error = `Required skill not discovered: ${PRIMARY_SKILL_NAME}`;
    }
    if (!forcedSkillLoadTelemetry.succeeded) {
      forcedSkillLoadTelemetry.fallback = true;
      try {
        const fallbackSkillPrompt = await buildSkillExpansionFallback(requiredSkillDiscovery);
        const fallbackLoad = await promptWithResult(session, fallbackSkillPrompt);
        forcedSkillLoadTelemetry.fallback_succeeded = Boolean(fallbackLoad.ok);
        if (!fallbackLoad.ok) {
          forcedSkillLoadTelemetry.fallback_error = fallbackLoad.error || "";
        }
      } catch (error) {
        forcedSkillLoadTelemetry.fallback_error = error?.stack || error?.message || String(error);
      }
    }
    await fs.writeFile(result.instruction_path, instructionText, "utf8");
    const promptDone = session
      .prompt(`/goal ${goalObjective}`, { expandPromptTemplates: true, source: "api" })
      .then(() => ({ ok: true }))
      .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
    const finalWait = await waitForFinalResultWithNudge(session, promptDone);
    const finalPayload = finalWait.payload;
    Object.assign(result, {
      ok: Boolean(finalPayload.final_result),
      status: finalPayload.final_result?.status || "invalid",
      turn_count: turnCount,
      final_wait: {
        wait_iterations: finalWait.waitIterations,
        wait_timeout_ms: finalWait.wait_timeout_ms,
        idle_drained: finalWait.idle_drained,
        nudge_attempts: finalWait.nudge_attempts || [],
      },
      native_tools_enabled: NATIVE_TOOL_NAMES,
      extension_tools_enabled: EXTENSION_TOOL_NAMES,
      extensions_loaded: ["@narumitw/pi-goal", "@narumitw/pi-retry"],
      retry_stall_timeout_ms: process.env[RETRY_STALL_TIMEOUT_ENV],
      extension_load_errors: extensionLoadErrors.map((item) => ({ path: item.path, error: item.error })),
      required_skills_discovered: requiredSkillDiscovery.discovered.map((item) => ({ name: item.name, file_path: item.filePath, description: item.description })),
      required_skills_missing: requiredSkillDiscovery.missing,
      forced_skill_load_attempted: forcedSkillLoadTelemetry.attempted,
      forced_skill_load_succeeded: forcedSkillLoadTelemetry.succeeded,
      forced_skill_load_error: forcedSkillLoadTelemetry.error,
      forced_skill_load_fallback: forcedSkillLoadTelemetry.fallback,
      forced_skill_load_fallback_succeeded: forcedSkillLoadTelemetry.fallback_succeeded,
      forced_skill_load_fallback_error: forcedSkillLoadTelemetry.fallback_error,
      skills_loaded: REQUIRED_SKILL_NAMES,
      custom_tools_enabled: customToolNames,
      action_system_prompt_appended: true,
      final_result_present: Boolean(finalPayload.final_result),
    });
  } finally {
    session.dispose();
  }
} catch (error) {
  Object.assign(result, {
    ok: false,
    status: "error",
    error: error?.stack || error?.message || String(error),
    turn_count: turnCount,
    native_tools_enabled: NATIVE_TOOL_NAMES,
    extension_tools_enabled: EXTENSION_TOOL_NAMES,
    retry_stall_timeout_ms: process.env[RETRY_STALL_TIMEOUT_ENV],
    extension_load_errors: extensionLoadErrors.map((item) => ({ path: item.path, error: item.error })),
    required_skills_discovered: requiredSkillDiscovery.discovered.map((item) => ({ name: item.name, file_path: item.filePath, description: item.description })),
    required_skills_missing: requiredSkillDiscovery.missing,
    forced_skill_load_attempted: forcedSkillLoadTelemetry.attempted,
    forced_skill_load_succeeded: forcedSkillLoadTelemetry.succeeded,
    forced_skill_load_error: forcedSkillLoadTelemetry.error,
    forced_skill_load_fallback: forcedSkillLoadTelemetry.fallback,
    forced_skill_load_fallback_succeeded: forcedSkillLoadTelemetry.fallback_succeeded,
    forced_skill_load_fallback_error: forcedSkillLoadTelemetry.fallback_error,
    skills_loaded: REQUIRED_SKILL_NAMES,
    custom_tools_enabled: customToolNames,
    action_system_prompt_appended: true,
  });
}

flushAssistantMessage();
result.assistant_output = buildAssistantOutputStats();
await fs.writeFile(result.event_log_path, JSON.stringify(eventLog, null, 2), "utf8");
await fs.writeFile(result.assistant_log_path, JSON.stringify(assistantMessages, null, 2), "utf8");
await fs.writeFile(result.instruction_path, instructionText, "utf8");
await fs.writeFile(outputPath, JSON.stringify(result, null, 2), "utf8");
console.log(JSON.stringify(result));
process.exit(result.ok ? 0 : 1);
