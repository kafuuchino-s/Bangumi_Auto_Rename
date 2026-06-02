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

async function readJsonFile(filePath) {
  if (!(await fileExists(filePath))) return null;
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch {
    return null;
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
        `Working Board review checkpoint: latest validation passed mechanically but has ${reviewWarnings.length} review warning(s).`,
        "Resolve only the warnings listed below, then validate params again. Do not switch to raw JSON or broad search:",
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
        "Working Board submit path: latest validation is accepted=true with no review warnings. Submit the same params or recipe now; do not rewrite it.",
      ];
    }
    if (!issues.length) return [];
    const repairHints = Array.isArray(verifier.repair_hints) ? verifier.repair_hints : [];
    const issueCodes = new Set(issues.map((issue) => String(issue.issue_code || "")));
    const lines = [
      `Working Board repair checkpoint: latest verifier is blocked: ${verifier.summary || "see issues"}.`,
      "Patch only these named issues, then validate params again. Fetch evidence only when the issue or hint asks for targeted evidence:",
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
    if (issueCodes.has("missing_episode_locator") || issueCodes.has("duplicate_target")) {
      lines.push(
        "Mechanical selector repair: a numbered multi-file mapped sequence needs group_ref/source_pattern/filename_regex with {ep} plus episode_range; do not enumerate many exact_paths with episode_range. Cover split variants with exclude_regex plus a supplemental exact_paths rule.",
      );
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
  const validationCalls = toolNames.filter((name) => name.includes("validate_organize_recipe")).length;
  const episodeEvidenceCalls = toolNames.filter((name) =>
    ["get_episode_list", "get_target_window", "get_target_detail"].includes(name),
  ).length;
  const subjectEvidenceCalls = toolNames.filter((name) =>
    [
      "search_bangumi_subjects",
      "lookup_bangumi_subject",
      "expand_related_graph",
      "expand_related_subjects",
      "find_bangumi_targets_for_local_file",
    ].includes(name),
  ).length;
  if (!verifierExists && validationCalls === 0 && toolNames.length) {
    lines.push(
      `Run progress fact: no params validation has completed yet. Evidence calls so far: ${subjectEvidenceCalls} subject/search/graph/lookup call(s), ${episodeEvidenceCalls} episode/window/detail call(s). This is telemetry for your Working Board, not a target recommendation.`,
    );
    if (subjectEvidenceCalls + episodeEvidenceCalls >= 4 || toolNames.length >= 8) {
      lines.push(
        "Validation debt checkpoint: no trial validation has run after substantial evidence gathering. The next custom tool should be validate_organize_recipe_params with the best testable mapped/supplemental rules, or fail_closed with a concrete blocker if even a trial rule cannot be written. For an uncertain group, write a supplemental test rule instead of delaying first validation. Do not call more search, episode, local-detail, or selector tools in response to this checkpoint.",
        "If subject/episode evidence exists but selector details are awkward, validate anyway. Duplicate local locators, split files, variant suffixes, and uncertain exclude_regex choices should become verifier feedback, not a no-validation timeout.",
      );
    }
  }
  return lines;
}

async function submitValidatedRecipeIfNeeded(finalPayload, helperCheck) {
  if (finalPayload?.final_result) {
    return { finalPayload, autoSubmit: null };
  }
  const artifactsDir = caseInput.scratch_paths?.artifacts_dir;
  if (!artifactsDir || helperCheck?.ok !== true) {
    return { finalPayload, autoSubmit: null };
  }
  const verifier = await readJsonFile(path.join(artifactsDir, "recipe_verifier_result.json"));
  const reviewWarnings = Array.isArray(verifier?.review_warnings) ? verifier.review_warnings : [];
  const issues = Array.isArray(verifier?.issues) ? verifier.issues : [];
  if (verifier?.passed !== true || reviewWarnings.length || issues.length) {
    return { finalPayload, autoSubmit: null };
  }

  const paramsPath = path.join(artifactsDir, "recipe_params.json");
  const recipePath = caseInput.scratch_paths?.organize_recipe;
  const params = await readJsonFile(paramsPath);
  let tool = "";
  let args = null;
  if (params && typeof params === "object") {
    tool = "submit_organize_recipe_params";
    args = { recipe_params: params, summary: "auto-submit after accepted params validation" };
  } else {
    const recipe = await readJsonFile(recipePath);
    if (recipe && typeof recipe === "object") {
      tool = "submit_organize_recipe";
      args = { organize_recipe: recipe, summary: "auto-submit after accepted recipe validation" };
    }
  }
  if (!tool || !args) {
    return { finalPayload, autoSubmit: { attempted: false, reason: "accepted validation exists but no params or recipe artifact was readable" } };
  }

  const result = await callPythonTool(tool, args);
  const updatedFinalPayload = await readFinalResult();
  return {
    finalPayload: updatedFinalPayload?.final_result ? updatedFinalPayload : finalPayload,
    autoSubmit: {
      attempted: true,
      tool,
      accepted: Boolean(result?.accepted),
      status: result?.status || "",
      ok: Boolean(result?.ok),
      summary: result?.summary || "",
      final_result_present: Boolean(updatedFinalPayload?.final_result),
    },
  };
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
  const totalBudgetMs = Math.max(30_000, effectiveRuntimeBudgetSeconds() * 1_000);
  const firstWaitMs = Math.min(totalBudgetMs, Math.max(35_000, Math.min(60_000, Math.floor(totalBudgetMs * 0.25))));
  let finalWait = await waitForFinalResultOrIdle(session, promptDone, { waitMs: firstWaitMs });
  const nudgeAttempts = [];
  if (finalWait.payload?.final_result) {
    return { ...finalWait, nudge_attempts: nudgeAttempts };
  }

  const verifierNudgeLines = await readLatestVerifierNudgeLines();
  const progressNudgeLines = await readRunnerProgressNudgeLines();
  const nudgeText = [
    "No final result has been recorded yet.",
    ...progressNudgeLines,
    "Time-boxed Working Board checkpoint: finish through one of three paths.",
    "Path 1: if validation is accepted with no review warnings, submit the same params/recipe now.",
    "Path 2: if verifier issues or review warnings exist, patch only the named rule/path/target and validate again.",
    "Path 3: if a supportable recipe cannot be built after targeted evidence, call fail_closed with the concrete group/reason.",
    ...verifierNudgeLines,
    "If no verifier feedback exists and every visible group has a mapped or supplemental test rule, call validate_organize_recipe_params.",
    "If no validation has run yet and one group remains uncertain, include that group as a supplemental test rule and validate. Validation is the trial that tells you what to repair.",
    "If mapped target evidence exists but duplicate/split selector handling is uncertain, validate the best mapped rule now and let duplicate_target or uncovered_path feedback name the repair.",
    "If validation rejects a mapped anime/video frontier rule, repair that mapped rule shape before converting it to supplemental. Supplemental is for closure-stalled or contradicted targets, not for escaping a mechanical issue.",
    "If your own reasoning says ready, enough, validate, or submit, the next action should be validate, submit, or fail_closed unless you can name one concrete missing evidence item.",
    "Do not print recipe JSON as prose, inspect old artifacts/tests, or restart broad search during this checkpoint.",
  ].join("\n");
  const nudgeDone = session
    .prompt(nudgeText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
    .then(() => ({ ok: true }))
    .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
  const remainingMs = Math.max(30_000, totalBudgetMs - firstWaitMs);
  const nudgeWait = await waitForFinalResultOrIdle(session, nudgeDone, { waitMs: Math.min(90_000, remainingMs) });
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
  const hardWaitMs = Math.min(90_000, hardRemainingMs);
  if (hardWaitMs >= 20_000) {
    const progressLines = await readRunnerProgressNudgeLines();
    const latestVerifierLines = await readLatestVerifierNudgeLines();
    const hardFinishText = [
      "Hard finish Working Board checkpoint: you stopped again without a final accepted recipe or fail_closed result.",
      ...progressLines,
      ...latestVerifierLines,
      "Choose one final path now: submit accepted params/recipe; patch named issues and validate; or fail_closed with a concrete evidence gap.",
      "If no params validation has happened yet, the next custom tool must be validate_organize_recipe_params with best-effort mapped/supplemental rules, unless you call fail_closed for a concrete blocker.",
      "Budget pressure is not a fail_closed reason. If target evidence exists and only selector/duplicate handling is uncertain, validate the best draft instead of calling fail_closed.",
      "Do not lower a plausibly mapped OVA/OAD/SP/movie/side-story group to supplemental just to pass validation; patch its target fields or selector first.",
      "Use params patch tools when a previous params validation/submit exists and only a few rules changed.",
      "Do not explain, browse old artifacts/tests, or gather broad evidence.",
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
  const maxRepairAttempts = 3;
  while (!finalWait.payload?.final_result && repairAttempt < maxRepairAttempts) {
    const remainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
    if (remainingMs < 20_000) break;
    const verifierLines = await readLatestVerifierNudgeLines();
    const progressLines = await readRunnerProgressNudgeLines();
    const attemptNumber = repairAttempt + 1;
    const repairText = [
      "Final Working Board repair loop: no final result exists, but wall-clock budget remains.",
      ...progressLines,
      ...verifierLines,
      "Act only on the current board state and the latest verifier/review feedback.",
      "If no verifier feedback exists and the board has testable coverage for all visible groups, validate params.",
      "If no validation has run yet, do not fetch more evidence here; validate best-effort params or fail_closed with the one concrete blocker.",
      "Do not call fail_closed with reason budget_exhausted. The runner records budget exhaustion; your job is to validate the best draft or name a real evidence contradiction.",
      "If feedback exists, patch only the named issue or warning, then validate again.",
      "For a rejected mapped frontier rule, prefer target/selector repair over supplemental downgrade unless evidence has contradicted that target.",
      `If validation is accepted with no review_warnings, submit the same params immediately. A submit result with \`status: "review"\` is not final.`,
      "If targeted evidence is still insufficient, call fail_closed with the unresolved group and evidence gap.",
      "Do not restart broad search, inspect old artifacts/tests, or print JSON as prose.",
    ].join("\n");
    const repairDone = session
      .prompt(repairText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
      .then(() => ({ ok: true }))
      .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
    const repairWait = await waitForFinalResultOrIdle(session, repairDone, { waitMs: Math.min(90_000, remainingMs) });
    nudgeAttempts.push({
      phase: `final_repair_${attemptNumber}`,
      wait_iterations: repairWait.waitIterations,
      wait_timeout_ms: repairWait.wait_timeout_ms,
      idle_drained: repairWait.idle_drained,
      prompt_settled: repairWait.prompt_settled,
      prompt_error: repairWait.prompt_error,
      final_result_present: Boolean(repairWait.payload?.final_result),
    });
    finalWait = repairWait;
    const remainingAfterRepairMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
    if (!finalWait.payload?.final_result && !finalWait.idle_drained && remainingAfterRepairMs >= 20_000) {
      const settleWait = await waitForFinalResultOrIdle(session, repairDone, { waitMs: Math.min(90_000, remainingAfterRepairMs) });
      nudgeAttempts.push({
        phase: `final_repair_${attemptNumber}_settle`,
        wait_iterations: settleWait.waitIterations,
        wait_timeout_ms: settleWait.wait_timeout_ms,
        idle_drained: settleWait.idle_drained,
        prompt_settled: settleWait.prompt_settled,
        prompt_error: settleWait.prompt_error,
        final_result_present: Boolean(settleWait.payload?.final_result),
      });
      finalWait = settleWait;
    }
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
  "Minimal recipe_params shape: {\"rules\":[{\"name\":\"TV 1-10\",\"group_ref\":\"LG1\",\"subject_id\":123,\"media_kind\":\"tv\",\"episode_type\":\"regular\",\"reason\":\"local group maps to this Bangumi episode run\"},{\"name\":\"TV explicit selector\",\"source_pattern\":\"Folder {vol}/Episode {ep:02}.mkv\",\"subject_id\":123,\"media_kind\":\"tv\",\"episode_type\":\"regular\",\"episode_range\":\"1-10\",\"episode_number_field\":\"sort\",\"episode_offset\":\"EP\",\"reason\":\"...\"},{\"name\":\"Movie\",\"exact_paths\":[\"real/source.mkv\"],\"subject_id\":456,\"media_kind\":\"movie\",\"episode_id\":789,\"reason\":\"...\"},{\"name\":\"Merged OVA\",\"source_unit\":\"single_file_multi_episode\",\"exact_paths\":[\"merged.mkv\"],\"subject_id\":246,\"media_kind\":\"ova\",\"episode_type\":\"regular\",\"episode_range\":\"1-3\",\"reason\":\"one file has chapters/duration supporting the exposed episode span\"},{\"name\":\"Bonus extras\",\"group_ref\":\"LG9\",\"disposition\":\"non_bangumi_or_supplemental\",\"reason\":\"package bonus with no supportable Bangumi episode target\"}]}",
  "Accepted params aliases: group_ref/local_group_ref for a local selector shorthand from list_local_groups or get_local_selector_scaffold; source_template for source_pattern; range or range_start/range_end or episode_start/episode_end for episode_range; offset for episode_offset; number_field or target_number_field for episode_number_field; subject_id for bangumi_subject_id; exact_paths, paths, source_path, or path for one-file rules.",
  "Supplemental/excluded files must use disposition:\"non_bangumi_or_supplemental\". Do not write boolean flags such as non_bangumi_or_supplemental:true, supplemental:true, or exclude:true. For one file that covers multiple episodes, use source_unit:\"single_file_multi_episode\" with episode_range; do not write merged:true or map it only to episode 1.",
  "Supplemental group rules do not need subject_id, episode_id, episode_type, episode_range, or episode_offset. Use one group_ref/path_glob/filename_regex/exact_paths rule that covers the intended supplemental paths exactly once.",
  "For a multi-file group_ref/source_pattern sequence, do not include episode_id/sort/ep unless every selected file intentionally maps to the same exact row. Let targets derive from {ep}; split separate movie/OVA/special files into exact_path rules with distinct exposed targets.",
  "Repair patch shape after a params validation: {\"patch_rules\":[{\"name\":\"Existing rule\",\"set\":{\"episode_id\":0,\"exclude_regex\":\"SP08_2\"},\"unset\":[\"episode_id\"]}],\"append_rules\":[{\"name\":\"Bonus\",\"exact_paths\":[\"real/source.mkv\"],\"disposition\":\"non_bangumi_or_supplemental\",\"reason\":\"...\"}],\"remove_rule_names\":[\"Bad rule\"]}. Use validate_organize_recipe_params_patch before submit_organize_recipe_params_patch; after an accepted patch validation, the same submit patch reuses the accepted merged params instead of applying append_rules again.",
  "Legal media_kind values are tv, movie, ova, oad, sp, special, unknown. Do not use rule-shape words such as numbered_run or exact_paths as media_kind.",
  "Legal episode_type values are regular, special, ova, oad, movie, unknown. For exact episode_id rules, omit episode_type unless copying it from an episode row; Python canonicalizes it.",
  "Legal episode_offset values are EP arithmetic only, such as EP, EP-10, or EP*2-1. Do not use SP as episode_offset; SP belongs in the filename selector or content evidence. For SP01-SP13 mapping to rows 1-13, use episode_offset:\"EP\".",
  "validate_organize_recipe_params is a trial check, not final submission. invalid/review results are normal contract feedback for repair_hints; accepted validation still needs submit_organize_recipe_params.",
  "A first trial validation does not need to be accepted or warning-free. It is a way to expose concrete coverage, duplicate, selector, missing-row, and review feedback before final submission.",
  "Do not inspect repository tests or Python schema to learn params. When this quick reference is insufficient, validation repair_hints are the contract feedback surface. Do not hand-translate these params into raw organize_recipe JSON to bypass review.",
].join("\n");

const tools = [
  proxyTool(
    "get_case_overview",
    "Get Case Overview",
    "Return the case map: counts, compact local group index, seen Bangumi evidence counts, recipe state, and navigation handles. It does not recommend a route.",
    objectSchema({}),
  ),
  proxyTool(
    "list_local_groups",
    "List Local Groups",
    "Return the local group index. With detail=true, returns expanded local group facts. Pi chooses which group to inspect.",
    objectSchema({ detail: Json.Optional(Json.Boolean()) }),
  ),
  proxyTool(
    "get_local_group_detail",
    "Get Local Group Detail",
    "Expand one local group by group_ref with source paths and optional detailed local file facts. It does not choose Bangumi targets or disposition.",
    objectSchema({
      group_ref: Json.String(),
      detail: Json.Optional(Json.Boolean()),
    }),
  ),
  proxyTool(
    "get_local_selector_scaffold",
    "Get Local Selector Scaffold",
    "Return selector/range params stubs for one local group_ref, or all groups when group_ref is omitted. Pi can use group_ref as a local selector shorthand, and fills target or supplemental fields from evidence.",
    objectSchema({
      group_ref: Json.Optional(Json.String()),
      detail: Json.Optional(Json.Boolean()),
    }),
  ),
  proxyTool(
    "get_case_context",
    "Get Case Context",
    "Read bounded Local to Bangumi case context. detail=false returns navigation context; detail=true expands the legacy full debug context.",
    objectSchema({ detail: Json.Optional(Json.Boolean()) }),
  ),
  proxyTool(
    "get_local_recipe_params_scaffold",
    "Get Local Recipe Params Scaffold",
    "Return local selector/range params stubs copied from local facts only. It does not choose Bangumi targets, media kind, episode type, disposition, or supplemental status.",
    objectSchema({ detail: Json.Optional(Json.Boolean()), group_ref: Json.Optional(Json.String()) }),
  ),
  proxyTool(
    "get_recipe_state",
    "Get Recipe State",
    "Return latest params, verifier, submit, and final-result state without changing the case.",
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
    `Trial-check semantic rule parameters: build an OrganizeRecipeDraft, compile it, and return verifier issues or review warnings without finishing the case. Accepted validation still requires submit_organize_recipe_params.\n${recipeParamsQuickReference}`,
    objectSchema({
      recipe_params: Json.Any(),
    }),
  ),
  proxyTool(
    "validate_organize_recipe_params_patch",
    "Validate Organize Recipe Params Patch",
    "Patch the latest recipe params from the previous params validate/submit, then validate. Use this in repair mode to change only affected rules.",
    objectSchema({
      recipe_params_patch: Json.Any(),
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
    "submit_organize_recipe_params_patch",
    "Submit Organize Recipe Params Patch",
    "Patch the latest recipe params from the previous params validate/submit, then submit. If the same patch was just accepted by validate_organize_recipe_params_patch, submit reuses that accepted merged params instead of applying append_rules twice.",
    objectSchema({
      recipe_params_patch: Json.Any(),
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
  "Human workflow: read local groups, anchor the main line, close the side frontier through related graph evidence, validate compact params, repair mechanical issues, then submit.",
  "Use the navigable hierarchy: get_case_overview is the map, list_local_groups is the group index, get_local_group_detail expands a chosen group, and get_recipe_state shows verifier progress.",
  "Local group facts are not target decisions. group_ref is only a selector shorthand; subject_id, episode_id, media_kind, episode_type, and supplemental status must come from Bangumi evidence.",
  "For one standalone main-title group, direct Bangumi search is fine. For multi-season, movie-box, OVA/special-box, or franchise side-content packages, search one reliable anchor first, then use expand_related_graph as the series map.",
  "After main anchors map, keep a side frontier of remaining anime/video-shaped groups, including parent-titled SP folders and long standalone OVA/OAD/SP files. When graph evidence maps a frontier group by season qualifier, count, duration, title, or episode rows, add that subject as a new anchor and continue closure.",
  "Draft recipe_params when every visible group has either a testable mapped rule or a testable supplemental rule. Validation is the trial that exposes selector, range, row-type, coverage, and duplicate repairs.",
  "For numbered multi-file mapped sequences, use group_ref/source_pattern/filename_regex with {ep}; reserve exact_paths for one-file rules, separate one-file entries, and supplemental extras.",
  "Mechanical accepted is the floor, not the quality target. Do not downgrade a plausible OVA/OAD/SP/movie/side-story mapping to supplemental just to clear a verifier issue; repair target fields, selector, range/offset, or duplicate/split handling first.",
  "Supplemental is for closure-stalled or contradicted targets, plus true extras. Do not use parent-season searches or missing parent SP rows as negative evidence for named side-content groups.",
  "After invalid or review feedback, stop broad exploration. Patch only verifier_result.issues, repair_hints, review_warnings, or repair_mode, using params patch tools for small repairs.",
  "Never call fail_closed with budget_exhausted, never inspect old artifacts/tests to copy an answer, and call goal_complete immediately after an accepted submit.",
].join("\n");
const instructionPath = path.join(path.dirname(outputPath), "pi_goal_instructions.md");

const instructionText = `
Complete this Local-to-Bangumi organize recipe case.
Case input JSON is available at: ${inputPath}
For tool arguments and recipe exact_paths, use only source_path values exposed by get_local_group_detail, get_local_file_detail, or case_input.context.local_files[].source_path. Never pass case_input.task_source_path as a local source_path.
Use the navigable custom-tool hierarchy rather than expanding every JSON layer at once: get_case_overview for the map, list_local_groups for group index, get_local_group_detail for a chosen group, get_local_selector_scaffold for selector stubs, Bangumi tools for chosen subject/episode evidence, and get_recipe_state for verifier progress. These tools expose pages; they do not choose the semantic route for you.
Available lazy skills, to load only when the current instruction file, case input, and tool results are insufficient:
${lazySkillMenu}
Pi has already discovered the skills by name and description. Do not read every SKILL.md at startup; use the relevant /skill:name command or read the matching SKILL.md only after a real blocker: Bangumi evidence confusion, local package interpretation ambiguity, or verifier/schema repair.
Use scratch paths from case_input.scratch_paths for notes and organize_recipe JSON.
${recipeGuidance}
Use Bangumi custom tools for subject/episode evidence, then write a MoviePilot-like OrganizeRecipeDraft using real source paths and Bangumi subject_id/episode_id/type/sort/ep.
Prefer the params path: validate_organize_recipe_params trial-checks semantic parameters and returns repair feedback; the first trial check may be invalid or reviewed. submit_organize_recipe_params finalizes only after there are no blocking issues and no review_warnings. Python turns semantic parameters into the full JSON recipe. The raw validate_organize_recipe and submit_organize_recipe tools remain available for debugging generated JSON, not for bypassing params review. Run the bash helper only when debugging a schema/selector problem.
Use the Working Board method from the guidance: keep one row per local group, validate when each visible group has a testable mapped or supplemental rule, then repair only named verifier/review feedback.
find_bangumi_targets_for_local_file is a fact lookup only. It can expose search results and episode rows, but it will not choose a target or generate recipe JSON for you. Do not inspect repository tests or Python schema just to confirm the recipe shape.
The submit/validate tools write recipe artifacts. Write notes.md only for complex evidence, contradictions, or fail_closed reasoning.
Only after submit_organize_recipe_params or submit_organize_recipe returns accepted=true may you call goal_complete. If strict evidence is insufficient or contradictory, call fail_closed, then goal_complete. After accepted=true, do not call any other tool except goal_complete.
Try to finish before ${caseInput.runtime_policy?.suggested_finish_before_seconds ?? 0} seconds so the final submit has time to complete.
`.trim();

await fs.writeFile(instructionPath, instructionText, "utf8");

const goalObjective = `
Produce a Python-verifier accepted OrganizeRecipeDraft or fail closed.
The full compact guidance is included below and also saved at ${instructionPath} for audit.
Use case-scoped custom tools for navigable facts; the raw case input at ${inputPath} is a fallback, not the normal working surface.
Choose recipe parameters from exposed facts, validate until there are no blocking issues and no review_warnings, submit them, then call goal_complete immediately after accepted=true.
Use the Working Board: each local group needs target evidence, a recipe rule, a status, and an open issue if verifier feedback named one.

${instructionText}
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
