#!/usr/bin/env node

import {
  AuthStorage,
  createAgentSession,
  DefaultResourceLoader,
  defineTool,
  ModelRegistry,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
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

const NATIVE_TOOL_NAMES = ["read", "grep", "find", "ls", "bash", "edit", "write"];
const EXTENSION_TOOL_NAMES = ["goal_complete"];
const RETRY_STALL_TIMEOUT_ENV = "PI_RETRY_STALL_TIMEOUT_MS";
// The non-interactive Python subprocess timeout is the hard stall boundary here.
// pi-retry's watchdog keeps a timer with a session ctx; in SDK mode that timer can
// fire after session.dispose() and crash the Node process with a stale ctx error.
if (!process.env[RETRY_STALL_TIMEOUT_ENV]) {
  process.env[RETRY_STALL_TIMEOUT_ENV] = "0";
}
const EXTENSION_PATHS = [
  path.join(repoRoot, "node_modules", "@narumitw", "pi-goal", "src", "goal.ts"),
  path.join(repoRoot, "node_modules", "@narumitw", "pi-retry", "src", "retry.ts"),
];
const REQUIRED_SKILL_NAMES = [
  "bangumi-api",
  "anime-release-reading",
  "organize-recipe-contract",
];

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
  Any: () => ({}),
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
  Union: (schemas) => ({ anyOf: schemas }),
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
  const row = {
    type: event.type,
    turn_count: turnCount,
    at: new Date().toISOString(),
  };
  if ("toolName" in event && event.toolName) row.tool_name = String(event.toolName);
  if ("toolCallId" in event && event.toolCallId) row.tool_call_id = String(event.toolCallId);
  if (event.type === "tool_execution_start" || event.type === "tool_execution_update") {
    row.args_preview = safePreview(event.args);
  }
  if (event.type === "tool_execution_end") {
    row.is_error = Boolean(event.isError);
    row.result_preview = safePreview(event.result);
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
  const rawDelta = assistantEvent?.delta ?? assistantEvent?.text ?? assistantEvent?.content ?? "";
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

async function fileExists(filePath) {
  if (!filePath) return false;
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function runProcess(argv, options = {}) {
  const timeoutMs = options.timeoutMs ?? 30000;
  const maxOutputChars = options.maxOutputChars ?? 200000;
  return new Promise((resolve) => {
    const child = spawn(argv[0], argv.slice(1), {
      cwd: options.cwd || repoRoot,
      windowsHide: true,
      shell: false,
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, timeoutMs);
    child.stdout?.on("data", (chunk) => {
      if (stdout.length < maxOutputChars) stdout += chunk.toString("utf8").slice(0, maxOutputChars - stdout.length);
    });
    child.stderr?.on("data", (chunk) => {
      if (stderr.length < maxOutputChars) stderr += chunk.toString("utf8").slice(0, maxOutputChars - stderr.length);
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      resolve({ code, signal, timedOut, stdout, stderr });
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ code: null, signal: null, timedOut, stdout, stderr, error: error?.message || String(error) });
    });
  });
}

async function ensureHelperCheckArtifact() {
  const helperCheckPath = caseInput.scratch_paths?.helper_check;
  const recipePath = caseInput.scratch_paths?.organize_recipe;
  if (!helperCheckPath || !recipePath) {
    return { ok: false, skipped: true, reason: "missing scratch helper_check or organize_recipe path" };
  }
  if (!(await fileExists(recipePath))) {
    const payload = { ok: false, skipped: true, reason: "organize_recipe artifact does not exist", recipe_path: recipePath };
    await fs.writeFile(helperCheckPath, JSON.stringify(payload, null, 2), "utf8");
    return payload;
  }
  const scriptPath = path.join(repoRoot, ".pi", "skills", "organize-recipe-contract", "scripts", "check-organize-recipe.mjs");
  const completed = await runProcess([process.execPath, scriptPath, recipePath, inputPath], { cwd: repoRoot, timeoutMs: 30000 });
  let payload;
  try {
    payload = JSON.parse(completed.stdout || "{}");
  } catch {
    payload = {};
  }
  const artifact = {
    ...payload,
    ok: Boolean(payload.ok) && completed.code === 0 && !completed.timedOut,
    helper_command: [process.execPath, scriptPath, recipePath, inputPath],
    returncode: completed.code,
    timed_out: completed.timedOut,
    stderr: completed.stderr,
  };
  if (completed.error) artifact.error = completed.error;
  if (!Object.keys(payload).length && completed.stdout) artifact.stdout = completed.stdout;
  await fs.writeFile(helperCheckPath, JSON.stringify(artifact, null, 2), "utf8");
  return artifact;
}

async function readFinalResult() {
  const finalResponse = await fetch(`${server}/final`);
  return finalResponse.json();
}

async function readLatestVerifierNudgeLines() {
  const artifactsDir = caseInput.scratch_paths?.artifacts_dir;
  if (!artifactsDir) return [];
  const verifierPath = path.join(artifactsDir, "recipe_verifier_result.json");
  if (!(await fileExists(verifierPath))) return [];
  try {
    const verifier = JSON.parse(await fs.readFile(verifierPath, "utf8"));
    const issues = Array.isArray(verifier.issues) ? verifier.issues : [];
    const reviewWarnings = Array.isArray(verifier.review_warnings) ? verifier.review_warnings : [];
    if (verifier.passed === true && reviewWarnings.length) {
      const lines = [
        `Latest validate_organize_recipe passed mechanically but has ${reviewWarnings.length} review warning(s).`,
        "Review mode: resolve only these warnings with targeted evidence, then call validate_organize_recipe_params again:",
      ];
      for (const warning of reviewWarnings.slice(0, 6)) {
        const code = warning.code || "review_warning";
        const sourcePath = warning.source_path || "";
        const message = warning.message || "";
        lines.push(`- ${code}: ${message}${sourcePath ? ` (${sourcePath})` : ""}`);
        if (warning.repair_hint) {
          lines.push(`  repair_hint: ${warning.repair_hint}`);
        }
      }
      return lines;
    }
    if (verifier.passed === true) {
      return [
        "Latest validate_organize_recipe result is accepted=true. Submit the same recipe now; do not rewrite it.",
      ];
    }
    if (!issues.length) return [];
    const repairHints = Array.isArray(verifier.repair_hints) ? verifier.repair_hints : [];
    const lines = [
      `Latest validate_organize_recipe is still blocked: ${verifier.summary || "see issues"}.`,
      "Repair mode: fix only these verifier issues, then call validate_organize_recipe_params again:",
    ];
    for (const issue of issues.slice(0, 6)) {
      const code = issue.issue_code || "issue";
      const ref = issue.ref || "";
      const message = issue.message || "";
      lines.push(`- ${code}: ${message}${ref ? ` (${ref})` : ""}`);
      const relatedRefs = Array.isArray(issue.related_refs) ? issue.related_refs.filter(Boolean).slice(0, 4) : [];
      if (relatedRefs.length) {
        lines.push(`  related_refs: ${JSON.stringify(relatedRefs)}`);
      }
    }
    if (repairHints.length) {
      lines.push("Top repair_hints:");
      for (const hint of repairHints.slice(0, 3)) {
        lines.push(`- ${hint}`);
      }
    }
    return lines;
  } catch {
    return [];
  }
}

async function readRunnerProgressNudgeLines() {
  const lines = [];
  const recipePath = caseInput.scratch_paths?.organize_recipe;
  const artifactsDir = caseInput.scratch_paths?.artifacts_dir;
  const verifierPath = artifactsDir ? path.join(artifactsDir, "recipe_verifier_result.json") : "";
  const recipeExists = await fileExists(recipePath);
  const verifierExists = await fileExists(verifierPath);
  const tracePath = path.join(path.dirname(outputPath), "tool_trace.jsonl");
  let traceRows = [];
  if (await fileExists(tracePath)) {
    try {
      const text = await fs.readFile(tracePath, "utf8");
      traceRows = text
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
      traceRows = [];
    }
  }
  const toolNames = traceRows.map((row) => String(row.tool || "")).filter(Boolean);
  const recentTools = toolNames.slice(-6);
  const uniqueTools = [...new Set(toolNames)];
  if (!toolNames.length) {
    lines.push("Progress so far: no custom tool calls were completed, and no final result exists.");
  } else {
    lines.push(
      `Progress so far: ${toolNames.length} custom tool call(s); recent tools: ${recentTools.join(", ") || "none"}.`,
    );
    lines.push(`Completed tool types: ${uniqueTools.join(", ")}.`);
  }
  lines.push(`Recipe artifact exists: ${recipeExists ? "yes" : "no"}. Verifier artifact exists: ${verifierExists ? "yes" : "no"}.`);
  const exposedSubjectIds = [];
  for (const row of traceRows) {
    const resultSummary = row.result_summary && typeof row.result_summary === "object" ? row.result_summary : {};
    const args = row.arguments && typeof row.arguments === "object" ? row.arguments : {};
    for (const value of [
      args.subject_id,
      resultSummary.subject_id,
      ...(Array.isArray(resultSummary.exposed_subject_ids) ? resultSummary.exposed_subject_ids : []),
    ]) {
      const id = Number(value);
      if (Number.isFinite(id) && !exposedSubjectIds.includes(id)) exposedSubjectIds.push(id);
    }
  }
  if (exposedSubjectIds.length) {
    lines.push(`Already exposed subject_id values include: ${exposedSubjectIds.slice(0, 12).join(", ")}.`);
  }
  return lines;
}

async function submitValidatedRecipeIfNeeded(finalPayload, helperCheck) {
  return { finalPayload, autoSubmit: null };
}

async function waitForFinalResultOrIdle(session, promptDone, options = {}) {
  const timeoutSeconds = Number(caseInput.runtime_policy?.wall_clock_timeout_seconds || 0);
  const finishBeforeSeconds = Number(caseInput.runtime_policy?.suggested_finish_before_seconds || 0);
  const defaultWaitMs = Math.max(1_000, (finishBeforeSeconds || Math.max(1, timeoutSeconds - 5)) * 1_000);
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
      return {
        payload: lastPayload,
        waitIterations,
        wait_timeout_ms: waitMs,
        idle_drained: false,
        prompt_settled: promptState.settled,
        prompt_error: promptState.outcome?.ok === false ? promptState.outcome.error : "",
      };
    }
    await sleep(500);
    lastPayload = await readFinalResult();
    if (lastPayload.final_result) {
      return {
        payload: lastPayload,
        waitIterations,
        wait_timeout_ms: waitMs,
        idle_drained: false,
        prompt_settled: promptState.settled,
        prompt_error: promptState.outcome?.ok === false ? promptState.outcome.error : "",
      };
    }
    const busy = Boolean(session.isStreaming || session.pendingMessageCount > 0 || !promptState.settled);
    if (busy && promptState.outcome?.ok !== false) {
      idleSince = 0;
      await sleep(250);
      continue;
    }
    if (!idleSince) idleSince = Date.now();
    if (Date.now() - idleSince >= 3_000) {
      return {
        payload: lastPayload,
        waitIterations,
        wait_timeout_ms: waitMs,
        idle_drained: true,
        prompt_settled: promptState.settled,
        prompt_error: promptState.outcome?.ok === false ? promptState.outcome.error : "",
      };
    }
    await sleep(250);
  }
  return {
    payload: lastPayload,
    waitIterations,
    wait_timeout_ms: waitMs,
    idle_drained: false,
    prompt_settled: promptState.settled,
    prompt_error: promptState.outcome?.ok === false ? promptState.outcome.error : "",
  };
}

async function waitForFinalResultWithNudge(session, promptDone) {
  const startedAt = Date.now();
  const timeoutSeconds = Number(caseInput.runtime_policy?.wall_clock_timeout_seconds || 0);
  const finishBeforeSeconds = Number(caseInput.runtime_policy?.suggested_finish_before_seconds || 0);
  const totalBudgetMs = Math.max(30_000, (finishBeforeSeconds || Math.max(30, timeoutSeconds - 15)) * 1_000);
  const firstWaitMs = Math.min(totalBudgetMs, Math.max(45_000, Math.min(90_000, Math.floor(totalBudgetMs * 0.35))));
  let finalWait = await waitForFinalResultOrIdle(session, promptDone, { waitMs: firstWaitMs });
  const nudgeAttempts = [];
  if (finalWait.payload?.final_result) {
    return { ...finalWait, nudge_attempts: nudgeAttempts };
  }

  const verifierNudgeLines = await readLatestVerifierNudgeLines();
  const nudgeText = [
    "No final result has been recorded yet.",
    "Time-boxed checkpoint: stop broad investigation and finish this case now.",
    "Do not print recipe JSON as plain text. You must call validate_organize_recipe_params, submit_organize_recipe_params, submit_organize_recipe, or fail_closed.",
    "If no recipe has been validated yet, construct recipe_params now from the visible groups you already understand: source_pattern/source_template for numbered runs, subject_id plus episode range/offset for mapped groups, and exact_paths plus disposition:\"non_bangumi_or_supplemental\" for unresolved bonus-like visible files.",
    "If your latest validation was rejected, enter repair mode: read the reported issues, change only the affected params/rules, fetch only evidence required by those issues, then validate again now.",
    ...verifierNudgeLines,
    "If you have valid recipe parameters, call submit_organize_recipe_params. If you have a raw accepted recipe, call submit_organize_recipe.",
    "If the latest params result is status:\"review\", do not hand-write or translate a raw OrganizeRecipeDraft; keep using params tools, resolve the listed review_warnings, then validate/submit params again.",
    "If evidence is insufficient, call fail_closed.",
    "Do not call more broad search tools during this checkpoint unless a verifier issue specifically requires that evidence. Do not inspect templates or old artifacts just to continue; use the case input and already exposed evidence.",
  ].join("\n");
  const nudgeDone = session
    .prompt(nudgeText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
    .then(() => ({ ok: true }))
    .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
  const remainingMs = Math.max(30_000, totalBudgetMs - firstWaitMs);
  const nudgeWait = await waitForFinalResultOrIdle(session, nudgeDone, { waitMs: Math.min(120_000, remainingMs) });
  nudgeAttempts.push({
    phase: "checkpoint",
    wait_iterations: nudgeWait.waitIterations,
    wait_timeout_ms: nudgeWait.wait_timeout_ms,
    idle_drained: nudgeWait.idle_drained,
    prompt_settled: nudgeWait.prompt_settled,
    prompt_error: nudgeWait.prompt_error,
    final_result_present: Boolean(nudgeWait.payload?.final_result),
  });
  finalWait = nudgeWait;
  if (finalWait.payload?.final_result) {
    return { ...finalWait, nudge_attempts: nudgeAttempts };
  }

  const hardRemainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
  const hardWaitMs = Math.min(120_000, hardRemainingMs);
  if (hardWaitMs >= 20_000) {
    const progressLines = await readRunnerProgressNudgeLines();
    const latestVerifierLines = await readLatestVerifierNudgeLines();
    const hardFinishText = [
      "Hard finish checkpoint: you stopped again without a final accepted recipe or fail_closed result.",
      ...progressLines,
      "Do not explain. Do not read old run artifacts or repository tests. Use tools now.",
      "If you already know the anime subject_id and episode rows, call validate_organize_recipe_params immediately with the smallest semantic params; you must not keep investigating instead.",
      "For a normal numbered TV run, use one sequence rule with source_pattern containing {ep}, subject_id, media_kind:\"tv\", episode_number_field:\"sort\" or \"ep\", range_start/range_end, and a short reason.",
      "For one independent movie/OVA/SP/special file, use exact_paths/source_path. If an exact episode row is known, include episode_id; if the subject itself is the movie target, omit episode_id.",
      "For one-file recap/movie files with a direct title or source_path lookup candidate matching the filename qualifier, draft an exact_paths movie rule and validate it; do not fail_closed before this validate attempt.",
      "For visible files that are truly bonus/interview/menu/extra after evidence, use exact_paths plus disposition:\"non_bangumi_or_supplemental\".",
      "If validation returns accepted=true, submit the same params immediately. If validation reports issues, change only the affected params and validate again.",
      "If validation or submit returns status:\"review\", do not switch to raw submit_organize_recipe. Resolve the warning repair_hints and resubmit params.",
      "Only call fail_closed when strict evidence is insufficient after that targeted validate attempt.",
      ...latestVerifierLines,
    ].join("\n");
    const hardDone = session
      .prompt(hardFinishText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
      .then(() => ({ ok: true }))
      .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
    const hardWait = await waitForFinalResultOrIdle(session, hardDone, { waitMs: hardWaitMs });
    nudgeAttempts.push({
      phase: "hard_finish",
      wait_iterations: hardWait.waitIterations,
      wait_timeout_ms: hardWait.wait_timeout_ms,
      idle_drained: hardWait.idle_drained,
      prompt_settled: hardWait.prompt_settled,
      prompt_error: hardWait.prompt_error,
      final_result_present: Boolean(hardWait.payload?.final_result),
    });
    finalWait = hardWait;
  }
  let repairAttempt = 0;
  while (!finalWait.payload?.final_result && repairAttempt < 3) {
    const remainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
    if (remainingMs < 20_000) break;
    const verifierLines = await readLatestVerifierNudgeLines();
    const progressLines = await readRunnerProgressNudgeLines();
    const repairText = [
      "Final repair loop: no final result exists, but wall-clock budget remains.",
      ...progressLines,
      ...verifierLines,
      "If there is no latest verifier feedback, that means no recipe has been validated yet: call validate_organize_recipe_params now with a compact best-effort recipe from known subject IDs and visible groups.",
      "Act only on the latest verifier/review feedback above.",
      "If the latest result has review_warnings, resolve only those warnings: call the exact targeted evidence tool named in repair_hint, then validate the same params again.",
      "For large packages, keep broad supplemental groups compact with path_glob/filename_regex selectors; use exact_paths only for irregular exceptions or the long file named by a review warning.",
      `If validate returns accepted=true with no review_warnings, submit the same params immediately. A submit result with \`status: "review"\` is not final; repair the warning and resubmit.`,
      "Do not manually convert params into a raw OrganizeRecipeDraft during review repair; raw tools are for debugging already-generated JSON only.",
      "Do not call fail_closed for uncertainty about a subset while plausible direct lookup/search candidates exist for that subset; validate the conservative candidate recipe first.",
      "If evidence is still insufficient after the targeted repair, call fail_closed with a concrete semantic reason.",
      "Do not restart broad search, do not inspect old artifacts/tests, and do not print JSON as prose.",
    ].join("\n");
    const repairDone = session
      .prompt(repairText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
      .then(() => ({ ok: true }))
      .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
    const repairWait = await waitForFinalResultOrIdle(session, repairDone, { waitMs: Math.min(120_000, remainingMs) });
    nudgeAttempts.push({
      phase: `final_repair_${repairAttempt + 1}`,
      wait_iterations: repairWait.waitIterations,
      wait_timeout_ms: repairWait.wait_timeout_ms,
      idle_drained: repairWait.idle_drained,
      prompt_settled: repairWait.prompt_settled,
      prompt_error: repairWait.prompt_error,
      final_result_present: Boolean(repairWait.payload?.final_result),
    });
    finalWait = repairWait;
    repairAttempt += 1;
  }
  return { ...finalWait, nudge_attempts: nudgeAttempts };
}

function proxyTool(name, label, description, parameters) {
  return defineTool({
    name,
    label,
    description,
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

const recipeParamsQuickReference = [
  "Minimal recipe_params shape: {\"rules\":[{\"name\":\"TV 1-10\",\"source_pattern\":\"Folder {vol}/Episode {ep:02}.mkv\",\"subject_id\":123,\"media_kind\":\"tv\",\"episode_type\":\"regular\",\"episode_range\":\"1-10\",\"episode_number_field\":\"sort\",\"episode_offset\":\"EP\",\"reason\":\"...\"},{\"name\":\"Movie\",\"exact_paths\":[\"real/source.mkv\"],\"subject_id\":456,\"media_kind\":\"movie\",\"episode_id\":789,\"reason\":\"...\"},{\"name\":\"Merged OVA\",\"source_unit\":\"single_file_multi_episode\",\"exact_paths\":[\"merged.mkv\"],\"subject_id\":246,\"media_kind\":\"ova\",\"episode_type\":\"regular\",\"episode_range\":\"1-3\",\"reason\":\"one file has chapters/duration supporting the exposed episode span\"},{\"name\":\"Bonus extras\",\"exact_paths\":[\"bonus.mkv\"],\"disposition\":\"non_bangumi_or_supplemental\",\"reason\":\"package bonus with no supportable Bangumi episode target\"}]}",
  "Accepted params aliases: source_template for source_pattern; range or range_start/range_end or episode_start/episode_end for episode_range; offset for episode_offset; number_field or target_number_field for episode_number_field; subject_id for bangumi_subject_id; exact_paths, paths, source_path, or path for one-file rules.",
  "Supplemental/excluded files must use disposition:\"non_bangumi_or_supplemental\". Do not write boolean flags such as non_bangumi_or_supplemental:true, supplemental:true, or exclude:true. For one file that covers multiple episodes, use source_unit:\"single_file_multi_episode\" with episode_range; do not write merged:true or map it only to episode 1.",
  "Legal media_kind values are tv, movie, ova, oad, sp, special, unknown. Do not use rule-shape words such as numbered_run or exact_paths as media_kind.",
  "Legal episode_type values are regular, special, ova, oad, movie, unknown. For exact episode_id rules, omit episode_type unless copying it from an episode row; Python canonicalizes it.",
  "Do not inspect repository tests or Python schema to learn params. If this quick reference is insufficient, call validate_organize_recipe_params and use repair_hints. Do not hand-translate these params into raw organize_recipe JSON to bypass review.",
].join("\n");

const tools = [
  proxyTool(
    "get_case_context",
    "Get Case Context",
    "Read the current Local to Bangumi case context with real source paths and Bangumi IDs.",
    objectSchema({ detail: Json.Optional(Json.Boolean()) }),
  ),
  proxyTool(
    "search_bangumi_subjects",
    "Search Bangumi Subjects",
    "Search Bangumi subjects from a query and add returned subject cards to the workspace.",
    objectSchema({
      query: Json.String(),
      max_subjects: Json.Optional(Json.Number()),
    }),
  ),
  proxyTool(
    "lookup_bangumi_subject",
    "Lookup Bangumi Subject",
    "Fetch details for Bangumi subject IDs.",
    objectSchema({ subject_ids: Json.Array(Json.Number()) }),
  ),
  proxyTool(
    "expand_related_subjects",
    "Expand Related Subjects",
    "Fetch related Bangumi subjects for a Bangumi subject ID.",
    objectSchema({
      subject_id: Json.Number(),
      relation_kinds: Json.Optional(Json.Array(Json.String())),
      subject_types: Json.Optional(Json.Array(Json.String())),
      max_subjects: Json.Optional(Json.Number()),
    }),
  ),
  proxyTool(
    "expand_related_graph",
    "Expand Related Graph",
    "Recursively fetch a compact Bangumi related-subject graph for one or more subject IDs.",
    objectSchema({
      subject_id: Json.Optional(Json.Number()),
      subject_ids: Json.Optional(Json.Array(Json.Number())),
      relation_kinds: Json.Optional(Json.Array(Json.String())),
      subject_types: Json.Optional(Json.Array(Json.String())),
      max_depth: Json.Optional(Json.Number()),
      max_subjects: Json.Optional(Json.Number()),
    }),
  ),
  proxyTool(
    "get_episode_list",
    "Get Episode List",
    "Fetch or expose episode cards for a Bangumi subject ID.",
    objectSchema({
      subject_id: Json.Number(),
      episode_scope: Json.Optional(Json.String()),
      max_episode_cards: Json.Optional(Json.Number()),
    }),
  ),
  proxyTool(
    "get_target_detail",
    "Get Target Detail",
    "Expose target episode details by episode IDs, or by subject ID plus sort.",
    objectSchema({
      episode_ids: Json.Optional(Json.Array(Json.Number())),
      subject_id: Json.Optional(Json.Number()),
      sort: Json.Optional(Json.Number()),
    }),
  ),
  proxyTool(
    "get_local_file_detail",
    "Get Local File Detail",
    "Expose local file detail by real source paths.",
    objectSchema({ paths: Json.Array(Json.String()) }),
  ),
  proxyTool(
    "find_bangumi_targets_for_local_file",
    "Find Bangumi Targets For Local File",
    "Fact helper: search Bangumi and return compact subject/episode rows for one visible source_path. It does not recommend targets or recipes.",
    objectSchema({
      source_path: Json.String(),
      title_query: Json.Optional(Json.String()),
      kind_hint: Json.Optional(Json.String()),
      max_subjects: Json.Optional(Json.Number()),
      max_episode_cards: Json.Optional(Json.Number()),
    }),
  ),
  proxyTool(
    "get_target_window",
    "Get Target Window",
    "Expose a target episode window by Bangumi subject ID and sort range.",
    objectSchema({
      subject_id: Json.Number(),
      sort_start: Json.Optional(Json.Number()),
      sort_end: Json.Optional(Json.Number()),
    }),
  ),
  proxyTool(
    "validate_organize_recipe",
    "Validate Organize Recipe",
    "Compile and verify an OrganizeRecipeDraft without finishing the case.",
    objectSchema({
      organize_recipe: Json.Any(),
    }),
  ),
  proxyTool(
    "validate_organize_recipe_params",
    "Validate Organize Recipe Params",
    `Build an OrganizeRecipeDraft from semantic rule parameters, then compile and verify it without finishing the case.\n${recipeParamsQuickReference}`,
    objectSchema({
      recipe_params: Json.Any(),
    }),
  ),
  proxyTool(
    "submit_organize_recipe",
    "Submit Organize Recipe",
    "Submit the final raw OrganizeRecipeDraft. For semantic recipe_params or any status:\"review\" repair, use submit_organize_recipe_params instead; do not hand-translate params into raw JSON.",
    objectSchema({
      organize_recipe: Json.Any(),
      summary: Json.Optional(Json.String()),
    }),
  ),
  proxyTool(
    "submit_organize_recipe_params",
    "Submit Organize Recipe Params",
    `Build the final OrganizeRecipeDraft from semantic rule parameters, then submit it through the strict Python verifier gate.\n${recipeParamsQuickReference}`,
    objectSchema({
      recipe_params: Json.Any(),
      summary: Json.Optional(Json.String()),
    }),
  ),
  proxyTool(
    "fail_closed",
    "Fail Closed",
    "Finish safely when the case cannot be mapped under strict evidence and verifier rules.",
    objectSchema({
      reason: Json.String(),
      reason_kind: Json.Optional(Json.String()),
      related_refs: Json.Optional(Json.Array(Json.String())),
    }),
  ),
];

const customToolNames = tools.map((tool) => tool.name);
const enabledToolNames = [...NATIVE_TOOL_NAMES, ...EXTENSION_TOOL_NAMES, ...customToolNames];
const lazySkillMenu = [
  "/skill:bangumi-api: use when Bangumi search results, relation graph traversal, subject IDs, episode IDs, sort/ep, or target-window evidence is confusing.",
  "/skill:anime-release-reading: use when local anime release folders, filenames, seasons, cours, OVA/OAD/SP, movies, recaps, duration hints, or package extras are ambiguous.",
  "/skill:organize-recipe-contract: use when recipe params, selectors, verifier issues, helper scripts, or submit/validate repair need the full contract.",
].join("\n");
const recipeGuidance = [
  "Before Bangumi search, inspect case_input.local_structure_summary and visible source_path values; infer local structure groups from folders, repeated title prefixes, content-shape words, and numbering runs.",
  "Treat numbering restarts such as multiple 01 files under different folders or title prefixes as likely separate groups, not duplicate episode 1.",
  "For each inferred group, identify the shared title/qualifier and changing locator token, then search Bangumi with one representative source_path before broad per-file investigation.",
  "Draft the smallest adequate recipe first: one rule for a coherent group, exact_paths only for independent exceptions.",
  "For large packages, do not enumerate dozens of obvious supplemental extras as exact_paths when a selector can cover them. Use path_glob/filename_regex for repeated bonus/design/material groups, and keep exact_paths for irregular exceptions or long files that need targeted evidence.",
  "For large franchise bundles with uniform short SP/bonus folders, do one representative lookup after the main anime graph is anchored; if exact Bangumi alignment is still not clear, cover the repeated short group as supplemental with a compact selector and validate instead of chasing every SP row.",
  "Use find_bangumi_targets_for_local_file as a compact fact lookup whenever a specific source_path needs Bangumi subject and episode evidence. It returns facts only, not a chosen target.",
  "Do not wait for fixed-layer recipe suggestions. Choose the semantic subject/episode yourself from local structure, Bangumi search results, relation graph evidence, and episode rows.",
  "search_bangumi_subjects is already scoped to Bangumi; do not append site words such as Bangumi, BGM, subject, or anime database to queries.",
  "Do not use repeated broad searches to check missing episode rows. If a helper result is truncated or a plausible subject_id is visible, call get_episode_list/get_target_window or validate_organize_recipe_params; validation hydrates declared subject evidence.",
  "Once one or more plausible anime subject anchors are known, prefer expand_related_graph before additional broad searches; use the graph as the series map for seasons, cours, specials, OVAs, OADs, movies, side stories, and recaps.",
  "For a same-folder movie/special collection with many visible named files from one franchise, do one anchor search, then expand_related_graph from the returned anime subject IDs and compare graph titles against the file-title list. Search individual titles only for names still missing from the graph.",
  "For movie collection folders where filenames carry parenthetical title qualifiers, use direct source_path/title lookup candidates that match those qualifiers as exact-path movie rules, then validate; do not require a fully exhausted relation graph for one-file movie subjects.",
  "For specials, OVAs, OADs, movies, and side stories, expand the Bangumi relation graph layer by layer from confirmed anime subject IDs with subject_types:[\"anime\"] and empty relation_kinds.",
  "Treat max_depth/max_subjects as one bounded graph call, not as proof of completeness. Read traversal_status.next_subject_ids_to_expand; if it is non-empty and a named local group is still unresolved, expand again from those subject IDs.",
  "Do not pass relation_kinds like anime or video; relation_kinds filters relation labels, not subject type. For anime rename work, prefer subject_types:[\"anime\"] and leave relation_kinds empty unless narrowing by a real relation label.",
  "When using related subjects, keep anime/video-shaped entries and ignore book/manga/novel/music/game/radio/soundtrack/live-event relations unless the local video evidence explicitly points there.",
  "Read relation_subjects first, compare graph nodes/edges against local subgroups, then fetch episode lists for matching subjects before drafting recipe targets.",
  "If a named local group has an exact Bangumi episode_id, draft and validate that mapped rule now. Relation frontier exhaustion is only required before declaring that named group supplemental.",
  "For one-file movie-shaped Bangumi subjects, validate exact-path rules with subject_id and media_kind:\"movie\" first; do not fetch get_episode_list for every one-episode movie subject unless validation blocks or the subject has multiple rows.",
  "For companion extras such as recording diaries, interviews, cast/staff talks, travel/location features, making-of, stage greetings, memorial clips, or short bonus documentaries, do one exact title search or one representative targeted lookup after anchoring the main anime subject; if no plausible anime/video target appears, validate a supplemental rule instead of exhausting the whole franchise graph.",
  "Use the episode_type shown by Bangumi episode rows. It may be regular even when media_kind is movie, special, ova, or oad; media_kind is the organize category, episode_type is the row type.",
  "For hand-authored work, prefer validate_organize_recipe_params and submit_organize_recipe_params. Use minimal semantic params: sequence rules need source_pattern/source_template with {ep} or zero-padded {ep:02}/{ep:02d}, range/offset, subject_id, media_kind, episode_type, and reason; one-file rules need exact_paths/source_path, subject_id, media_kind, and usually episode_id when an episode row is known. A one-file movie-shaped subject may omit episode_id when the subject itself is the movie target. Python builds the JSON recipe and fills mechanical defaults; do not hand-translate params into raw recipe JSON during review repair.",
  recipeParamsQuickReference,
  "source_pattern can include folder segments and non-episode placeholders such as {vol} or {title}; Python treats placeholders other than {ep}/{ep:02}/{ep:02d} as wildcard text. Use this for Vol.1/Vol.2 style batches instead of per-file exact_paths.",
  "Do not use source_pattern for a single literal filename. If there is no {ep} token, use exact_paths/source_path for that file; source_pattern without {ep} is only useful for non-mapped supplemental matching, not for episode derivation.",
  "If a repeated group has changing CRC/hash/checksum brackets, per-file IDs, or technical suffixes such as FLAC versus FLACx2, put a wildcard placeholder such as {hash}, {crc}, {audio}, or {a} at that position in source_pattern. Do not copy the first file's changing technical token into the whole sequence rule.",
  "For one video file that explicitly carries a range in its filename, such as [01-09], use source_unit:\"single_file_multi_episode\" with the matching episode_range after the subject's target rows are exposed.",
  "For exact one-file rules with episode_id, omit episode_type unless you are copying it from the returned episode row. The params tool canonicalizes exact episode_id rows from exposed evidence.",
  "Do not open organize-recipe-contract skill or template files for a first draft; these params rules are enough. If a verifier/schema/selector blocker remains after reading repair_hints, use /skill:organize-recipe-contract or read exactly .pi/skills/organize-recipe-contract/SKILL.md and then continue.",
  "Keep rule reasons short: one clear evidence sentence is usually enough. Do not write search logs in recipe reasons.",
  "For sequence numbering, compare the local file number with Bangumi episode sort and ep values. Keep episode_number_field:\"sort\" when sort matches; use episode_number_field:\"ep\" when local numbering matches ep while sort continues across an earlier season/cour; use arithmetic offsets only when the chosen number field is shifted.",
  "Do not stretch one Bangumi subject past the episode rows it exposes. If a later local range is missing from the first subject, split that range to a related season/cour/part subject instead of forcing one broad rule.",
  "Validate early once subject identity is supportable; validate_organize_recipe_params can fetch episode evidence for subject IDs already declared in the params-derived recipe.",
  "If validation returns review_warnings, resolve them before submit: for long supplemental files, call find_bangumi_targets_for_local_file with the exact source_path named by the warning, then validate again. These warnings are not target recommendations; they identify evidence that the verifier cannot see yet.",
  "After one representative lookup per visible local group, validate a params draft instead of continuing broad search. Cover bonus-like visible files with disposition:\"non_bangumi_or_supplemental\" only after traversal_status.frontier_exhausted is true for the relevant Bangumi anime/video relation frontier.",
  "Expand related subjects or wider windows only when candidates conflict, the subject identity is unclear, or the verifier still blocks after validation.",
  "Before fail_closed, validate a best-effort params recipe once if you have any plausible subject IDs and visible paths. Do not call fail_closed with budget_exhausted yourself; budget exhaustion is a runner outcome.",
  "Do not read repo templates, tests, or Python schema on the ordinary compact path. For genuine ambiguity, use exactly one relevant /skill:name command or read one matching .pi/skills/<name>/SKILL.md; do not load all skills.",
  "Do not search old run artifacts, previous final_result JSON files, or tests to copy an answer; validate the current recipe instead.",
  "Use disposition:\"non_bangumi_or_supplemental\" to cover visible bonus-like files that have no clear Bangumi episode target, with a plain-language reason.",
  "If one short or bonus-like group stays ambiguous after traversal_status.frontier_exhausted is true and one clean direct title search fails, cover that group with disposition:\"non_bangumi_or_supplemental\" and validate the whole recipe instead of looping on broad searches.",
  "Do not write notes for straightforward accepted candidates; notes are for complex, contradictory, or fail-closed investigations.",
  "After submit_organize_recipe_params or submit_organize_recipe returns accepted=true, call goal_complete immediately; do not do extra context refreshes or investigation. If submit returns status:\"review\", follow the warning repair_hint and validate again.",
].join("\n");
const instructionPath = path.join(path.dirname(outputPath), "pi_goal_instructions.md");

const instructionText = `
Complete this Local-to-Bangumi organize recipe case.
Read the case input JSON at: ${inputPath}
For tool arguments and recipe exact_paths, use only case_input.visible_source_paths or case_input.context.local_files[].source_path. Never pass case_input.task_source_path as a local source_path.
Start from case_input.local_structure_summary. It is factual grouping evidence only, not a semantic mapping decision.
Available lazy skills, to load only when the current instruction file, case input, and tool results are insufficient:
${lazySkillMenu}
Pi has already discovered the skills by name and description. Do not read every SKILL.md at startup; use the relevant /skill:name command or read the matching SKILL.md only after a real blocker: Bangumi evidence confusion, local package interpretation ambiguity, or verifier/schema repair.
Use scratch paths from case_input.scratch_paths for notes and organize_recipe JSON.
${recipeGuidance}
Use Bangumi custom tools for subject/episode evidence, then write a MoviePilot-like OrganizeRecipeDraft using real source paths and Bangumi subject_id/episode_id/type/sort/ep.
Prefer validate_organize_recipe_params until there are no blocking issues and no review_warnings, then submit_organize_recipe_params; Python turns semantic parameters into the full JSON recipe. The raw validate_organize_recipe and submit_organize_recipe tools remain available for debugging generated JSON, not for bypassing params review. Run the bash helper only when debugging a schema/selector problem.
find_bangumi_targets_for_local_file is a fact lookup only. It can expose search results and episode rows, but it will not choose a target or generate recipe JSON for you. Do not inspect repository tests or Python schema just to confirm the recipe shape.
The submit/validate tools write recipe artifacts. Write notes.md only for complex evidence, contradictions, or fail_closed reasoning.
Only after submit_organize_recipe_params or submit_organize_recipe returns accepted=true may you call goal_complete. If strict evidence is insufficient or contradictory, call fail_closed, then goal_complete. After accepted=true, do not call any other tool except goal_complete.
Try to finish before ${caseInput.runtime_policy?.suggested_finish_before_seconds ?? 0} seconds so the final submit has time to complete.
`.trim();

await fs.writeFile(instructionPath, instructionText, "utf8");

const goalObjective = `
Produce a Python-verifier accepted OrganizeRecipeDraft or fail closed.
First read ${instructionPath}
Then read case input ${inputPath}
Follow the compact guidance in the instruction file. Choose recipe parameters from the exposed facts, validate until there are no blocking issues and no review_warnings, submit them, then call goal_complete immediately after accepted=true.
`.trim();

const result = {
  ok: false,
  status: "error",
  case_id: caseInput.case_id || "",
  instruction_path: instructionPath,
  event_log_path: path.join(path.dirname(outputPath), "pi_event_log.json"),
  assistant_log_path: path.join(path.dirname(outputPath), "pi_assistant_messages.json"),
};

try {
  const effectiveAgentDir = agentDir || process.env.PI_CODING_AGENT_DIR || path.join(repoRoot, ".pi", "agent");
  const resourceLoader = new DefaultResourceLoader({
    cwd: repoRoot,
    agentDir: effectiveAgentDir,
    additionalExtensionPaths: EXTENSION_PATHS,
  });
  await resourceLoader.reload();
  const extensionLoadErrors = resourceLoader.getExtensions().errors || [];
  const { session } = await createAgentSession({
    cwd: repoRoot,
    agentDir: agentDir || undefined,
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
    const promptDone = session
      .prompt(`/goal ${goalObjective}`, { expandPromptTemplates: true, source: "api" })
      .then(() => ({ ok: true }))
      .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
    const finalWait = await waitForFinalResultWithNudge(session, promptDone);
    const helperCheck = await ensureHelperCheckArtifact();
    const finalized = await submitValidatedRecipeIfNeeded(finalWait.payload, helperCheck);
    const finalPayload = finalized.finalPayload;
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
      extension_load_errors: extensionLoadErrors.map((item) => ({
        path: item.path,
        error: item.error,
      })),
      custom_tools_enabled: customToolNames,
      skills_loaded: REQUIRED_SKILL_NAMES,
      helper_check: helperCheck,
      auto_submit_after_validation: finalized.autoSubmit,
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
    custom_tools_enabled: customToolNames,
    skills_loaded: REQUIRED_SKILL_NAMES,
  });
}

flushAssistantMessage();
await fs.writeFile(result.event_log_path, JSON.stringify(eventLog, null, 2), "utf8");
await fs.writeFile(result.assistant_log_path, JSON.stringify(assistantMessages, null, 2), "utf8");
await fs.writeFile(outputPath, JSON.stringify(result, null, 2), "utf8");
console.log(JSON.stringify(result));
process.exit(result.ok ? 0 : 1);
