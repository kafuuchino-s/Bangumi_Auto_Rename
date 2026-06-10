#!/usr/bin/env node

import {
  AuthStorage,
  createAgentSession,
  DefaultResourceLoader,
  ModelRegistry,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import {
  LOCAL_BANGUMI_TOOL_NAMES,
  LOCAL_BANGUMI_TOOLS_ENV,
} from "../.pi/extensions/local-bangumi-tools/index.js";

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

// Local-to-Bangumi dry runs stay on the case-scoped custom tool surface, while
// retaining Pi's official progressive-disclosure path for skills/references.
// Enable read only; no shell/edit/write/listing tools are exposed.
const NATIVE_TOOL_NAMES = ["read"];
const EXTENSION_TOOL_NAMES = ["goal_complete", ...LOCAL_BANGUMI_TOOL_NAMES];
const RETRY_STALL_TIMEOUT_ENV = "PI_RETRY_STALL_TIMEOUT_MS";
// The non-interactive Python subprocess timeout is the hard stall boundary here.
// pi-retry's watchdog keeps a timer with a session ctx; in SDK mode that timer can
// fire after session.dispose() and crash the Node process with a stale ctx error.
if (!process.env[RETRY_STALL_TIMEOUT_ENV]) {
  process.env[RETRY_STALL_TIMEOUT_ENV] = "0";
}
const EXTENSION_PATHS = [
  path.join(repoRoot, ".pi", "extensions", "local-bangumi-tools", "index.js"),
  path.join(repoRoot, "node_modules", "@narumitw", "pi-goal", "src", "goal.ts"),
  path.join(repoRoot, "node_modules", "@narumitw", "pi-retry", "src", "retry.ts"),
];
const customToolNames = [...LOCAL_BANGUMI_TOOL_NAMES];
const enabledToolNames = [...NATIVE_TOOL_NAMES, ...EXTENSION_TOOL_NAMES];
const REQUIRED_SKILL_NAMES = [
  "local-bangumi-organize",
];
const PRIMARY_SKILL_NAME = "local-bangumi-organize";
const PRIMARY_PROMPT_TEMPLATE_NAME = "local-bangumi-map";
const PRIMARY_PROMPT_TEMPLATE_PATH = path.join(
  repoRoot,
  ".pi",
  "prompts",
  `${PRIMARY_PROMPT_TEMPLATE_NAME}.md`,
);
const PRIMARY_PROMPT_INVOCATION = `/${PRIMARY_PROMPT_TEMPLATE_NAME} ${inputPath}`;
const PRIMARY_SKILL_LOAD_COMMAND =
  `/skill:${PRIMARY_SKILL_NAME} Load this skill as method context for the upcoming Local-to-Bangumi case. ` +
  "During this skill-load step only, do not run case tools; the next prompt is the task to execute immediately.";
const ACTION_AGENT_OUTPUT_CONTRACT = [
  "Visible output contract: act through tools and artifacts, not reasoning prose.",
  "Do not write headings such as Deciding, Evaluating, Considering, or explain why a tool should be called.",
  "When a custom tool or goal_complete is available, call it directly with no prose; otherwise write one short blocker sentence.",
  "Do not print recipe JSON, full mapping tables, full verifier issues, or old artifact excerpts in assistant text.",
  "Tool arguments count as output: keep board notes, snapshots, reasons, and summaries compact.",
  "Do not paste get_case_overview/list_local_groups/get_local_group_detail JSON into notes; cite group refs and named blockers.",
  "For complex packages, the first Bangumi move is one reliable main-title search, then select_bangumi_anchor_subject; do not fail_closed from an empty draft before that anchor exists unless the case input is malformed.",
].join("\n");
const ACTION_AGENT_SYSTEM_PROMPT_SECTION = `
## Local-to-Bangumi Action Case Agent Output Protocol

This session runs as an action-oriented case agent. Reason internally, then externalize durable work through custom tools and artifacts.

- Assistant-visible text is a status channel, not a scratchpad.
- On a turn that can call a custom tool or goal_complete, call the tool directly and omit explanatory prose.
- Do not print chain-of-thought, self-review headings, mapping tables, recipe JSON, drafts, verifier issue dumps, or copied tool JSON.
- A normal non-final text-only response is at most one short blocker sentence naming the next missing fact or terminal fail_closed reason.
- When a group/subcluster judgment is stable enough to test, save one compact row with upsert_recipe_group_decision_one instead of explaining the row in prose.
- When a draft is complete, validate_recipe_params_draft is the next visible action; do not describe that you are ready.
- When validation is accepted, submit and then goal_complete; do not summarize before submitting.
- Keep board_delta, validation_snapshot, patch_delta, submit_snapshot, reason, and summary short. Transaction notes use strict small envelopes, not arbitrary JSON.
`.trim();

if (!inputPath || !outputPath || !server || !token) {
  throw new Error("required args: --input --output --server --token");
}
process.env[LOCAL_BANGUMI_TOOLS_ENV.server] = server;
process.env[LOCAL_BANGUMI_TOOLS_ENV.token] = token;

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

function expandSimplePromptTemplate(templateText, argsList) {
  const argsJoined = argsList.join(" ");
  let expanded = stripMarkdownFrontmatter(templateText).trim();
  for (let index = argsList.length; index >= 1; index -= 1) {
    const value = argsList[index - 1] || "";
    expanded = expanded.replaceAll(`$${index}`, value);
  }
  expanded = expanded
    .replaceAll("$ARGUMENTS", argsJoined)
    .replaceAll("$@", argsJoined);
  return expanded;
}

async function readExpandedPrimaryPromptTemplate() {
  const rawTemplate = await fs.readFile(PRIMARY_PROMPT_TEMPLATE_PATH, "utf8");
  return expandSimplePromptTemplate(rawTemplate, [inputPath]);
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

User: Load this skill as method context for the upcoming Local-to-Bangumi case. During this skill-load step only, do not run case tools; the next prompt is the task to execute immediately.`;
}

async function promptWithResult(session, text, options = {}) {
  try {
    await session.prompt(text, { expandPromptTemplates: true, source: "api", ...options });
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error?.stack || error?.message || String(error) };
  }
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
  const longTextTurns = assistantMessages
    .filter((message) => (message.text || "").length > 1000)
    .map((message) => ({
      turn_count: message.turn_count,
      text_chars: (message.text || "").length,
    }))
    .slice(0, 12);
  const reasoningHeadingPattern = /(^|\n)\s*(#{1,6}\s+|\*\*)?(Deciding|Evaluating|Considering|Analyzing|Assessing|Reviewing|Searching|Aligning)\b/i;
  const reasoningHeadingTurns = assistantMessages
    .filter((message) => reasoningHeadingPattern.test(message.text || ""))
    .map((message) => ({
      turn_count: message.turn_count,
      text_chars: (message.text || "").length,
    }))
    .slice(0, 12);
  return {
    assistant_message_count: assistantMessages.length,
    total_text_chars: totalTextChars,
    max_text_chars: lengths.length ? Math.max(...lengths) : 0,
    long_text_message_count: lengths.filter((length) => length > 1000).length,
    very_long_text_message_count: lengths.filter((length) => length > 2000).length,
    reasoning_heading_message_count: reasoningHeadingTurns.length,
    long_text_turns: longTextTurns,
    reasoning_heading_turns: reasoningHeadingTurns,
    log_char_count: assistantLogCharCount,
    log_truncated: assistantLogCharCount >= MAX_ASSISTANT_LOG_CHARS,
  };
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

async function fileMtimeMs(filePath) {
  if (!filePath) return 0;
  try {
    const stat = await fs.stat(filePath);
    return Number(stat.mtimeMs || 0);
  } catch {
    return 0;
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
  const scriptPath = path.join(repoRoot, ".pi", "skills", "local-bangumi-organize", "scripts", "check-organize-recipe.mjs");
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
    const issueRepairContexts = Array.isArray(verifier.issue_repair_contexts) ? verifier.issue_repair_contexts : [];
    if (verifier.passed === true && reviewWarnings.length) {
      const lines = [
        `Latest verifier: review, warning_count=${reviewWarnings.length}.`,
      ];
      for (const warning of reviewWarnings.slice(0, 4)) {
        const code = warning.code || "review_warning";
        const sourcePath = warning.source_path || "";
        const message = warning.message || "";
        lines.push(`- ${code}${sourcePath ? ` ${sourcePath}` : ""}: ${message}`);
        if (warning.repair_hint) {
          lines.push(`  repair_hint: ${String(warning.repair_hint).slice(0, 420)}`);
        }
        const metrics = warning.metrics && typeof warning.metrics === "object" ? warning.metrics : {};
        const resolutionCandidateIds = Array.isArray(metrics.review_resolution_candidate_episode_ids)
          ? metrics.review_resolution_candidate_episode_ids
          : (Array.isArray(warning.review_resolution_candidate_episode_ids) ? warning.review_resolution_candidate_episode_ids : []);
        if (resolutionCandidateIds.length) {
          lines.push(`  review_resolution_candidate_episode_ids: ${JSON.stringify(resolutionCandidateIds)}`);
        }
        const warningCandidates = Array.isArray(metrics.candidate_episode_rows)
          ? metrics.candidate_episode_rows
          : (Array.isArray(metrics.duration_candidate_episode_rows) ? metrics.duration_candidate_episode_rows : []);
        if (warningCandidates.length) {
          const sample = warningCandidates.slice(0, 4).map((row) => ({
            local_locator_number: row.local_locator_number,
            subject_id: row.subject_id,
            episode_id: row.episode_id,
            episode_type: row.episode_type,
            sort: row.sort,
            ep: row.ep,
            duration_seconds: row.duration_seconds,
            duration_delta_seconds: row.duration_delta_seconds,
            title: row.title,
          }));
          lines.push(`  warning_candidate_episode_rows: ${JSON.stringify(sample)}`);
        }
      }
      for (const context of issueRepairContexts.slice(0, 3)) {
        const kind = context.repair_kind || "repair_context";
        const ref = context.ref || "";
        const nextAction = context.next_action || "";
        const flags = context.mechanical_flags && typeof context.mechanical_flags === "object" ? context.mechanical_flags : {};
        lines.push(`Repair context ${kind}${ref ? ` ${ref}` : ""}: next=${nextAction}; flags=${JSON.stringify(flags)}`);
        const candidates = Array.isArray(context.candidate_episode_rows) ? context.candidate_episode_rows : [];
        if (candidates.length) {
          const sample = candidates.slice(0, 4).map((row) => ({
            source: row.matched_source_path,
            local_locator_number: row.local_locator_number,
            subject_id: row.subject_id,
            episode_id: row.episode_id,
            episode_type: row.episode_type,
            sort: row.sort,
            sort_matches_local_locator: row.sort_matches_local_locator,
            duration_seconds: row.duration_seconds,
          }));
          lines.push(`  candidate_episode_rows: ${JSON.stringify(sample)}`);
        }
      }
      lines.push("Next: if warning_candidate_episode_rows are supportable, validate a small params patch for the named source(s); if not supportable, patch review_resolutions on the supplemental rule using review_resolution_candidate_episode_ids when present, then validate again. A different subject_id alone is not a contradiction for side/SP/OVA/movie-bundle extras. Use fail_closed only when the whole case cannot be resolved. Do not continue broad evidence.");
      return lines;
    }
    if (verifier.passed === true) {
      return [
        "Latest verifier: accepted with no review warnings. Next: submit accepted params.",
      ];
    }
    if (!issues.length) return [];
    const lines = [
      `Latest verifier: invalid, issue_count=${issues.length}.`,
    ];
    for (const issue of issues.slice(0, 4)) {
      const code = issue.issue_code || "issue";
      const ref = issue.ref || "";
      const message = issue.message || "";
      lines.push(`- ${code}${ref ? ` ${ref}` : ""}: ${message}`);
      const relatedRefs = Array.isArray(issue.related_refs) ? issue.related_refs.filter(Boolean).slice(0, 4) : [];
      if (relatedRefs.length) {
        lines.push(`  related_refs: ${JSON.stringify(relatedRefs)}`);
      }
    }
    for (const context of issueRepairContexts.slice(0, 3)) {
      const kind = context.repair_kind || "repair_context";
      const ref = context.ref || "";
      const nextAction = context.next_action || "";
      const flags = context.mechanical_flags && typeof context.mechanical_flags === "object" ? context.mechanical_flags : {};
      lines.push(`Repair context ${kind}${ref ? ` ${ref}` : ""}: next=${nextAction}; flags=${JSON.stringify(flags)}`);
      const sources = Array.isArray(context.related_sources) ? context.related_sources : [];
      for (const source of sources.slice(0, 3)) {
        const pathText = source.source_path || "";
        const duration = source.duration_seconds ?? "";
        const group = source.group_ref || "";
        const shape = source.group_kind_hint || "";
        lines.push(`  source ${pathText}: duration=${duration}${group ? ` group=${group}` : ""}${shape ? ` shape=${shape}` : ""}`);
      }
      const candidates = Array.isArray(context.candidate_episode_rows) ? context.candidate_episode_rows : [];
      if (candidates.length) {
          const sample = candidates.slice(0, 4).map((row) => ({
            source: row.matched_source_path,
            local_locator_number: row.local_locator_number,
            subject_id: row.subject_id,
            episode_id: row.episode_id,
            episode_type: row.episode_type,
            sort: row.sort,
            sort_matches_local_locator: row.sort_matches_local_locator,
            duration_seconds: row.duration_seconds,
          }));
        lines.push(`  candidate_episode_rows: ${JSON.stringify(sample)}`);
      }
    }
    if (issueRepairContexts.some((context) => context?.mechanical_flags?.likely_wrong_target_surface)) {
      lines.push("Issue repair context indicates likely wrong target surface: inspect or patch distinct exposed side/special/OVA/movie-like rows before changing mapped files to supplemental.");
    }
    lines.push("Next: patch named issue, one targeted fact, submit accepted, or concrete fail_closed.");
    return lines;
  } catch {
    return [];
  }
}

async function readCaseBoardNudgeLines() {
  const notesPath = caseInput.scratch_paths?.notes;
  if (!notesPath) return [];
  if (!(await fileExists(notesPath))) {
    return [
      "Case board: notes.md does not exist yet. If you write an Initial Board, keep it compact: cite group refs and blockers only; do not paste the local group JSON.",
    ];
  }
  try {
    const text = await fs.readFile(notesPath, "utf8");
    const headings = [...text.matchAll(/^##\s+(.+)$/gm)].map((match) => match[1].trim()).filter(Boolean);
    const latestHeadings = headings.slice(-5);
    const nextMatches = [...text.matchAll(/^Next:\s*(.+)$/gm)].map((match) => match[1].trim()).filter(Boolean);
    const latestNext = nextMatches.slice(-1)[0] || "";
    const lines = [
      `Case board: notes.md exists with ${headings.length} section(s)${latestHeadings.length ? `; latest sections: ${latestHeadings.join(" | ")}` : ""}.`,
    ];
    const latestHeading = headings.slice(-1)[0] || "";
    if (latestHeading === "Validation Snapshot") {
      lines.push(
        "Latest board section is Validation Snapshot from an older two-step trace. In new work, Validation Snapshot belongs inside the validation transaction.",
      );
    } else if (latestHeading === "Verifier Delta") {
      lines.push(
        "Latest board section is Verifier Delta. The named issue rows/rules are the current repair surface; targeted evidence is useful only when verifier/review feedback asks for it.",
      );
    } else if (latestHeading === "Patch Delta") {
      lines.push("Latest board section is Patch Delta from an older two-step trace. In new work, Patch Delta belongs in patch_delta when validating or submitting a patch.");
    } else if (latestHeading === "Submit Snapshot") {
      lines.push("Latest board section is Submit Snapshot from an older two-step trace. If submit already accepted, the case can be completed; otherwise the accepted params/recipe still need submit.");
    }
    if (latestNext) {
      lines.push(`Case board latest Next: ${latestNext}.`);
    }
    lines.push("If your conversation context is stale, get_case_board_notes(mode:\"tail\") restores the latest working memory.");
    return lines;
  } catch {
    return ["Case board: notes.md exists but could not be summarized. Use get_case_board_notes(mode:\"tail\") if needed."];
  }
}

async function readRunnerProgressNudgeLines() {
  const lines = [];
  const recipePath = caseInput.scratch_paths?.organize_recipe;
  const artifactsDir = caseInput.scratch_paths?.artifacts_dir;
  const draftPath = caseInput.scratch_paths?.recipe_params_draft;
  const verifierPath = artifactsDir ? path.join(artifactsDir, "recipe_verifier_result.json") : "";
  const recipeExists = await fileExists(recipePath);
  const draft = draftPath ? await readJsonFile(draftPath) : null;
  const draftRuleCount = Array.isArray(draft?.rules) ? draft.rules.length : 0;
  const verifierExists = await fileExists(verifierPath);
  const traceRows = await readToolTraceRows();
  const toolNames = traceRows.map((row) => String(row.tool || "")).filter(Boolean);
  const recentTools = toolNames.slice(-6);
  const uniqueTools = [...new Set(toolNames)];
  const atlasRows = traceRows.filter((row) => ["build_bangumi_relation_atlas", "select_bangumi_anchor_subject"].includes(String(row.tool || "")));
  const latestDraftSummary = [...traceRows]
    .reverse()
    .map((row) => (row.result_summary && typeof row.result_summary === "object" ? row.result_summary : null))
    .find((summary) => summary && ("recipe_params_draft_rule_count" in summary || "recipe_params_draft_ready" in summary));
  const latestAnchorAtlasSummary = [...traceRows]
    .reverse()
    .map((row) => (row.result_summary && typeof row.result_summary === "object" ? row.result_summary : null))
    .find((summary) => summary && summary.anchor_atlas_next_tool && Array.isArray(summary.anchor_atlas_candidate_subject_ids) && summary.anchor_atlas_candidate_subject_ids.length);
  if (!toolNames.length) {
    lines.push("Progress so far: no custom tool calls were completed, and no final result exists.");
  } else {
    lines.push(
      `Progress so far: ${toolNames.length} custom tool call(s); recent tools: ${recentTools.join(", ") || "none"}.`,
    );
    lines.push(`Completed tool types: ${uniqueTools.join(", ")}.`);
  }
  lines.push(`Recipe artifact exists: ${recipeExists ? "yes" : "no"}. Draft rule count: ${draftRuleCount}. Verifier artifact exists: ${verifierExists ? "yes" : "no"}.`);
  if (!atlasRows.length && latestAnchorAtlasSummary?.anchor_atlas_candidate_subject_ids?.length) {
    lines.push(`Anchor bootstrap facts: candidate_subject_ids=${latestAnchorAtlasSummary.anchor_atlas_candidate_subject_ids.slice(0, 8).join(", ")}. Use select_bangumi_anchor_subject once the reliable main anchor is clear; otherwise gather one named anchor fact.`);
  }
  if (atlasRows.length) {
    const latestAtlasSummary = atlasRows.length && atlasRows[atlasRows.length - 1].result_summary && typeof atlasRows[atlasRows.length - 1].result_summary === "object"
      ? atlasRows[atlasRows.length - 1].result_summary
      : {};
    if (latestAtlasSummary.bangumi_relation_atlas_id) {
      lines.push(
        `Latest atlas: ${latestAtlasSummary.bangumi_relation_atlas_id}, subjects=${latestAtlasSummary.atlas_subject_count ?? "unknown"}, frontier_exhausted=${latestAtlasSummary.atlas_frontier_exhausted === true ? "true" : "false"}, stop_reason=${latestAtlasSummary.atlas_stop_reason || "unknown"}.`,
      );
      if (draftRuleCount === 0 && !verifierExists) {
        lines.push("Atlas is ready and no decisions are saved. Persist one stable target-surface row with upsert_recipe_group_decision_one, or fetch one targeted fact for the remaining named gap.");
      }
    }
  }
  if (latestDraftSummary) {
    const missingCount = latestDraftSummary.draft_missing_group_count;
    lines.push(
      `Latest draft preview: ready_for_full_validation=${latestDraftSummary.recipe_params_draft_ready === true ? "true" : "false"}, covered_groups=${latestDraftSummary.draft_covered_group_count ?? "unknown"}, missing_groups=${missingCount ?? "unknown"}.`,
    );
    const ruleCount = Number(latestDraftSummary.recipe_params_draft_rule_count || 0);
    const uncoveredPathCount = Number(latestDraftSummary.draft_uncovered_path_count || 0);
    if (ruleCount > 0 && (Number(missingCount || 0) > 0 || uncoveredPathCount > 0)) {
      lines.push("Partial draft exists: next action should save the next stable missing group/subcluster or record one specific blocker; do not spend another broad evidence pass on all remaining groups.");
    }
    if (latestDraftSummary.workpaper_checkpoint_next_tool) {
      lines.push(`Workpaper checkpoint is active: next custom tool should be ${latestDraftSummary.workpaper_checkpoint_next_tool}; save one stable row or record the exact blocker before more evidence.`);
    }
    const qualityIssueCount = Number(latestDraftSummary.draft_quality_issue_count || 0);
    if (qualityIssueCount > 0) {
      lines.push(`Latest draft quality: ${qualityIssueCount} non-testable row(s). Fix, replace, or remove them.`);
    }
  }
  lines.push(...(await readCaseBoardNudgeLines()));
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
      "select_bangumi_anchor_subject",
      "build_bangumi_relation_atlas",
      "find_bangumi_targets_for_local_file",
    ].includes(name),
  ).length;
  if (!verifierExists && validationCalls === 0 && toolNames.length) {
    lines.push(
      `Run progress: no params validation yet; subject_evidence_calls=${subjectEvidenceCalls}, episode_evidence_calls=${episodeEvidenceCalls}.`,
    );
    if (latestDraftSummary?.recipe_params_draft_ready === true) {
      lines.push("Draft progress: complete. Next: validate_recipe_params_draft.");
    } else if (Number(latestDraftSummary?.draft_quality_issue_count || 0) > 0) {
      lines.push("Draft progress: saved rows need field repair before validation.");
    } else if (draftRuleCount > 0) {
      lines.push("Draft progress: partial. Save remaining stable rows or gather one named fact.");
    } else if (subjectEvidenceCalls + episodeEvidenceCalls > 0) {
      lines.push("Saved-decision gap: if any group/subcluster is stable, persist one compact row with upsert_recipe_group_decision_one; keep only unresolved surfaces in targeted evidence.");
      lines.push("Complex package evidence path: one reliable main anchor -> select_bangumi_anchor_subject -> atlas synthesis; side-title fanout is fallback for named atlas gaps.");
    } else {
      lines.push("Decision progress: save mapped or evidence-gap supplemental rows when stable.");
    }
  }
  return lines;
}

async function validateReadyDraftIfNeeded(finalPayload) {
  if (finalPayload?.final_result) {
    return { finalPayload, autoValidateDraft: null };
  }
  const artifactsDir = caseInput.scratch_paths?.artifacts_dir;
  if (!artifactsDir) {
    return { finalPayload, autoValidateDraft: null };
  }
  const verifierPath = path.join(artifactsDir, "recipe_verifier_result.json");
  const draftPath = caseInput.scratch_paths?.recipe_params_draft;
  const verifier = await readJsonFile(verifierPath);
  const verifierMtimeMs = await fileMtimeMs(verifierPath);
  const draftMtimeMs = await fileMtimeMs(draftPath);
  const reviewWarnings = Array.isArray(verifier?.review_warnings) ? verifier.review_warnings : [];
  const verifierCurrentForDraft = Boolean(verifier) && verifierMtimeMs >= draftMtimeMs && draftMtimeMs > 0;
  if (verifierCurrentForDraft) {
    return {
      finalPayload,
      autoValidateDraft: {
        attempted: false,
        reason: verifier?.passed === true && reviewWarnings.length === 0
          ? "latest verifier already accepted this recipe_params_draft; submit accepted params"
          : "latest verifier is current for recipe_params_draft; repair or submit before revalidating",
        ok: true,
        accepted: verifier?.passed === true && reviewWarnings.length === 0,
        status: verifier?.passed === true ? (reviewWarnings.length ? "review" : "accepted") : "invalid",
        summary: verifier?.summary || "",
        verifier_passed: verifier?.passed,
        verifier_issue_count: Array.isArray(verifier?.issues) ? verifier.issues.length : 0,
        review_warning_count: reviewWarnings.length,
        final_result_present: false,
      },
    };
  }
  const draftState = await callPythonTool("get_recipe_params_draft", { detail: false });
  if (draftState?.ready_for_full_validation !== true) {
    return {
      finalPayload,
      autoValidateDraft: {
        attempted: false,
        reason: "recipe_params_draft is not ready for full validation",
        ok: Boolean(draftState?.ok),
        rule_count: draftState?.rule_count ?? 0,
        missing_group_refs: draftState?.coverage_preview?.missing_group_refs || [],
      },
    };
  }
  const result = await callPythonTool("validate_recipe_params_draft", {
    validation_snapshot: {
      summary: "Auto-validate complete recipe_params_draft through the full verifier.",
      accepted_scope: ["recipe_params_draft covers every visible local group"],
      open_issues: [],
      next_action: "read verifier result and submit or patch",
    },
  });
  const updatedFinalPayload = await readFinalResult();
  return {
    finalPayload: updatedFinalPayload?.final_result ? updatedFinalPayload : finalPayload,
    autoValidateDraft: {
      attempted: true,
      ok: Boolean(result?.ok),
      accepted: Boolean(result?.accepted),
      status: result?.status || "",
      summary: result?.summary || "",
      verifier_passed: result?.verifier_result?.passed,
      verifier_issue_count: Array.isArray(result?.verifier_result?.issues) ? result.verifier_result.issues.length : 0,
      review_warning_count: Array.isArray(result?.review_warnings) ? result.review_warnings.length : 0,
      final_result_present: Boolean(updatedFinalPayload?.final_result),
    },
  };
}

function coerceStringArray(value) {
  if (Array.isArray(value)) return value.map((item) => String(item || "")).filter(Boolean);
  if (value === undefined || value === null || value === "") return [];
  return [String(value)];
}

function ruleExactPaths(rule) {
  return coerceStringArray(rule?.exact_paths);
}

async function repairMovieSubjectLevelLocatorIfNeeded() {
  const artifactsDir = caseInput.scratch_paths?.artifacts_dir;
  if (!artifactsDir) return null;
  const verifier = await readJsonFile(path.join(artifactsDir, "recipe_verifier_result.json"));
  const params = await readJsonFile(path.join(artifactsDir, "recipe_params.json"));
  const issues = Array.isArray(verifier?.issues) ? verifier.issues : [];
  const rules = Array.isArray(params?.rules) ? params.rules : [];
  if (verifier?.passed === true || !issues.length || !rules.length) return null;

  const patchRules = [];
  for (const issue of issues) {
    if (issue?.issue_code !== "missing_target_episode") continue;
    const relatedRefs = Array.isArray(issue.related_refs) ? issue.related_refs.map((ref) => String(ref || "")) : [];
    const subjectRefs = relatedRefs
      .map((ref) => Number(String(ref).replace(/^subject:/, "")))
      .filter((value) => Number.isFinite(value) && value > 0);
    const sourceRef = String(issue.ref || relatedRefs.find((ref) => !/^(subject:)?\d+$/.test(String(ref))) || "");
    const rule = rules.find((candidate) => {
      if (!candidate || typeof candidate !== "object") return false;
      if (String(candidate.media_kind || "") !== "movie") return false;
      if (!(candidate.episode_id || candidate.sort || candidate.ep)) return false;
      const subjectId = Number(candidate.subject_id || 0);
      if (subjectRefs.length && !subjectRefs.includes(subjectId)) return false;
      return ruleExactPaths(candidate).includes(sourceRef);
    });
    if (!rule?.name) continue;
    patchRules.push({ name: String(rule.name), unset: ["episode_id", "sort", "ep"] });
  }

  const uniquePatchRules = [...new Map(patchRules.map((rule) => [rule.name, rule])).values()];
  if (!uniquePatchRules.length) return null;
  const result = await callPythonTool("validate_organize_recipe_params_patch", {
    recipe_params_patch: { patch_rules: uniquePatchRules },
    patch_delta: {
      summary: "Auto-repair subject-level movie locator.",
      changed_rules: uniquePatchRules.map((rule) => rule.name),
      reason: "Removed invalid episode locator from exact-path movie rules while preserving Pi-selected subject_id/media_kind.",
    },
  });
  return {
    attempted: true,
    tool: "validate_organize_recipe_params_patch",
    ok: Boolean(result?.ok),
    accepted: Boolean(result?.accepted),
    status: result?.status || "",
    summary: result?.summary || "",
    patched_rules: uniquePatchRules.map((rule) => rule.name),
    verifier_passed: result?.verifier_result?.passed,
    verifier_issue_count: Array.isArray(result?.verifier_result?.issues) ? result.verifier_result.issues.length : 0,
    review_warning_count: Array.isArray(result?.review_warnings) ? result.review_warnings.length : 0,
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
  const firstWaitMs = Math.min(totalBudgetMs, Math.max(30_000, Math.min(45_000, Math.floor(totalBudgetMs * 0.15))));
  let finalWait = await waitForFinalResultOrIdle(session, promptDone, { waitMs: firstWaitMs });
  const nudgeAttempts = [];
  let autoValidateReadyDraft = null;
  async function validateReadyDraftAtCheckpoint(waitResult, phase) {
    if (waitResult.payload?.final_result) return waitResult;
    const draftValidated = await validateReadyDraftIfNeeded(waitResult.payload);
    if (draftValidated.autoValidateDraft?.attempted !== true) return waitResult;
    autoValidateReadyDraft = draftValidated.autoValidateDraft;
    nudgeAttempts.push({
      phase: `${phase}_auto_validate_ready_draft`,
      auto_validate_ready_draft: draftValidated.autoValidateDraft,
      final_result_present: Boolean(draftValidated.finalPayload?.final_result),
    });
    return { ...waitResult, payload: draftValidated.finalPayload };
  }
  finalWait = await validateReadyDraftAtCheckpoint(finalWait, "initial_wait");
  if (finalWait.payload?.final_result) {
    return {
      ...finalWait,
      nudge_attempts: nudgeAttempts,
      auto_validate_ready_draft: autoValidateReadyDraft,
    };
  }

  const verifierNudgeLines = await readLatestVerifierNudgeLines();
  const progressNudgeLines = await readRunnerProgressNudgeLines();
  const nudgeText = [
    "Checkpoint: continue as an action case agent.",
    "If a tool action is available, call it now with no explanation. Otherwise provide one concrete blocker sentence.",
    "- save decisions",
    "- validate complete draft",
    "- patch named verifier issue",
    "- after verifier feedback, use one targeted fact per named issue/warning cluster, capped by the checkpoint before patch/submit/blocker",
    "- submit accepted",
    "- concrete fail_closed",
    ...progressNudgeLines,
    ...verifierNudgeLines,
    "Do not show reasoning narrative, reread skills, or inspect old artifacts/tests.",
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
  finalWait = await validateReadyDraftAtCheckpoint(nudgeWait, "checkpoint");
  if (finalWait.payload?.final_result) {
    return {
      ...finalWait,
      nudge_attempts: nudgeAttempts,
      auto_validate_ready_draft: autoValidateReadyDraft,
    };
  }

  if (autoValidateReadyDraft?.attempted === true && autoValidateReadyDraft.accepted !== true) {
    const autoRepairRemainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
    if (autoRepairRemainingMs >= 20_000) {
      const progressLines = await readRunnerProgressNudgeLines();
      const verifierLines = await readLatestVerifierNudgeLines();
      const autoRepairText = [
        "Auto-validation returned invalid/review. Continue with one tool action, no reasoning narrative.",
        "Use issue_repair_contexts and repair_hints as the repair plan.",
        "Do not convert mapped side/SP/OVA/movie rows to non_bangumi_or_supplemental as the first repair when context shows duration/path mismatch, likely_wrong_target_surface, or candidate episode rows; inspect or patch the alternate target surface first.",
        "- patch named verifier issue",
        "- fetch one named target fact",
        "- after verifier feedback, use one targeted fact per named issue/warning cluster, capped by the checkpoint before patch/submit/blocker",
        "- submit accepted patch",
        "- concrete fail_closed",
      ...progressLines,
      ...verifierLines,
      "Put the correction into patch_delta/tool args, not prose.",
      ].join("\n");
      const autoRepairDone = session
        .prompt(autoRepairText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
        .then(() => ({ ok: true }))
        .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
      const autoRepairWait = await waitForFinalResultOrIdle(session, autoRepairDone, { waitMs: Math.min(75_000, autoRepairRemainingMs) });
      nudgeAttempts.push({
        phase: "auto_validation_repair",
        wait_iterations: autoRepairWait.waitIterations,
        wait_timeout_ms: autoRepairWait.wait_timeout_ms,
        idle_drained: autoRepairWait.idle_drained,
        prompt_settled: autoRepairWait.prompt_settled,
        prompt_error: autoRepairWait.prompt_error,
        final_result_present: Boolean(autoRepairWait.payload?.final_result),
      });
      finalWait = await validateReadyDraftAtCheckpoint(autoRepairWait, "auto_validation_repair");
      if (finalWait.payload?.final_result) {
        return {
          ...finalWait,
          nudge_attempts: nudgeAttempts,
          auto_validate_ready_draft: autoValidateReadyDraft,
        };
      }
    }
  }

  const hardRemainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
  const hardWaitMs = Math.min(90_000, hardRemainingMs);
  if (hardWaitMs >= 20_000) {
    const progressLines = await readRunnerProgressNudgeLines();
    const latestVerifierLines = await readLatestVerifierNudgeLines();
    const hardFinishText = [
      "Hard finish checkpoint: act or close. Do not narrate the decision.",
      "Use issue_repair_contexts before cheap patches; target-surface mismatch must be repaired or explicitly exhausted before supplemental.",
      "- save decisions",
      "- validate complete draft",
      "- patch named verifier issue",
      "- after verifier feedback, use one targeted fact per named issue/warning cluster, capped by the checkpoint before patch/submit/blocker",
      "- submit accepted",
      "- concrete fail_closed",
      ...progressLines,
      ...latestVerifierLines,
      "Budget pressure is not a fail_closed reason.",
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
    finalWait = await validateReadyDraftAtCheckpoint(hardWait, "hard_finish");
  }
  let repairAttempt = 0;
  const maxRepairAttempts = 1;
  while (!finalWait.payload?.final_result && repairAttempt < maxRepairAttempts) {
    const remainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
    if (remainingMs < 20_000) break;
    const verifierLines = await readLatestVerifierNudgeLines();
    const progressLines = await readRunnerProgressNudgeLines();
    const attemptNumber = repairAttempt + 1;
    const repairText = [
      "Final repair loop: call one case tool or close with a concrete evidence reason.",
      "Follow issue_repair_contexts/repair_hints. Do not make a mapped side file supplemental merely to pass duplicate_target when the context points to a distinct target surface.",
      "- save decisions",
      "- validate complete draft",
      "- patch named verifier issue",
      "- after verifier feedback, use one targeted fact per named issue/warning cluster, capped by the checkpoint before patch/submit/blocker",
      "- submit accepted",
      "- concrete fail_closed",
      ...progressLines,
      ...verifierLines,
      "No budget_exhausted fail_closed. No recipe JSON or reasoning prose.",
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
    finalWait = await validateReadyDraftAtCheckpoint(repairWait, `final_repair_${attemptNumber}`);
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
      finalWait = await validateReadyDraftAtCheckpoint(settleWait, `final_repair_${attemptNumber}_settle`);
    }
    repairAttempt += 1;
  }
  return {
    ...finalWait,
    nudge_attempts: nudgeAttempts,
    auto_validate_ready_draft: autoValidateReadyDraft,
  };
}


const instructionPath = path.join(path.dirname(outputPath), "pi_goal_instructions.md");

function buildInstructionText(expandedPromptTemplate, launchTelemetry = {}) {
  return `
Official-style Local-to-Bangumi Pi entry.
Case input JSON is available at: ${inputPath}
Prompt template invocation: ${PRIMARY_PROMPT_INVOCATION}
Prompt template path: ${PRIMARY_PROMPT_TEMPLATE_PATH}
Forced skill load command: ${PRIMARY_SKILL_LOAD_COMMAND}
Forced skill load attempted: ${Boolean(launchTelemetry.forcedSkillLoadAttempted)}
Forced skill load succeeded: ${Boolean(launchTelemetry.forcedSkillLoadSucceeded)}
Forced skill fallback used: ${Boolean(launchTelemetry.forcedSkillLoadFallback)}

${expandedPromptTemplate}

${ACTION_AGENT_OUTPUT_CONTRACT}

For tool arguments and recipe exact_paths, use only source_path values exposed by get_local_group_detail, get_local_file_detail, or case_input.context.local_files[].source_path. Never pass case_input.task_source_path as a local source_path. Prefer group_ref plus file_numbers/file_number_range/path_contains for numbered subclusters before listing long exact_paths.
Use the navigable custom-tool hierarchy rather than expanding every JSON layer at once: get_case_overview for the map, list_local_groups for group index, get_local_group_detail for a chosen group, Bangumi tools for chosen subject/episode evidence, get_recipe_group_decisions for saved group decisions, get_recipe_params_draft for compiled draft state, and get_recipe_state for verifier progress. These tools expose facts and audit state; they do not choose the semantic route for you.
The full local-bangumi-organize skill is loaded before this goal, or an explicit skill expansion fallback was sent before this goal. Use it as experience, then prefer case tools, saved group decisions, and verifier hints over rereading skill files.
Do not inspect old run artifacts, repository tests, or Python schemas as evidence for this case.
Use scratch paths from case_input.scratch_paths only through the custom board/draft/validate/submit tools.
Prefer compact recipe params: validate_organize_recipe_params and validate_recipe_params_draft trial-check semantic parameters; submit_organize_recipe_params finalizes accepted params. The visible completion path is params submit, params patch submit, or fail_closed.
Board and draft tools are Pi-owned working memory. The Python verifier remains the strict mechanical gate for coverage, duplicate targets, legal exposed Bangumi targets, and selector shape.
Convergence protocol: each evidence burst must become saved group decisions, draft params, validation, or a compact named blocker. More search is not progress once the blocker already names a target surface.
For complex franchise/side-content packages, after the first reliable main-title search, choose the anchor with select_bangumi_anchor_subject(anchor_subject_id, reason). That tool atomically records Pi's anchor choice and builds the evidence atlas; Python still does not choose any mapping.
Before finalizing a numbered side/SP/OVA/movie-like visible file as supplemental, Pi must check the target surface itself with find_bangumi_targets_for_local_file on the exact source_path or a representative path. Use returned duration_candidate_episode_rows as facts, not recommendations; keep supplemental only when that evidence exposes no supportable row or the surface is explicitly exhausted. A different subject_id alone is not a contradiction for side/SP/OVA/movie-bundle extras; require concrete relation/title/duration/locator mismatch evidence before recording candidate_rows_not_supportable.
When validation returns issue_repair_contexts, treat them as structured repair instructions. Duplicate-target context is target-surface feedback, not permission to make side/SP/OVA/movie rows supplemental before duration/path mismatch and exposed candidate rows have been audited.
For one-to-one multi-target rows, use selected exact_paths plus episode_ids, and do not also set episode_id/sort/ep. append_rules only adds new named rules; patch or replace existing rule names instead of appending overlaps.
Only after submit_organize_recipe_params or submit_organize_recipe_params_patch returns accepted=true may you call goal_complete. If strict evidence is insufficient or contradictory, call fail_closed with a concrete reason, then goal_complete. After accepted=true, do not call any other tool except goal_complete.
Try to finish before ${caseInput.runtime_policy?.suggested_finish_before_seconds ?? 0} seconds so the final submit has time to complete.
`.trim();
}

function buildGoalObjective(expandedPromptTemplate) {
  return `
Produce a Python-verifier accepted OrganizeRecipeDraft or fail closed.
Runtime task boundaries are saved at ${instructionPath} for audit.
The runner has explicitly loaded /skill:${PRIMARY_SKILL_NAME} or sent an equivalent skill expansion fallback before this /goal.
Use the project prompt template ${PRIMARY_PROMPT_INVOCATION} as the task briefing.

${expandedPromptTemplate}

${ACTION_AGENT_OUTPUT_CONTRACT}

Use case-scoped custom tools for facts and work memory; the raw case input at ${inputPath} is fallback, not the normal working surface.
For exact_paths, use only visible source_path values exposed by case tools, never task_source_path. Prefer group_ref plus file_numbers/file_number_range/path_contains for numbered subclusters before listing long exact_paths.
Python only persists Pi-owned board/decision/draft work and verifies coverage, duplicate targets, legal exposed Bangumi rows, and selector shape.
After gathering a useful evidence batch, materialize it as saved decisions or draft validation before broadening the search.
For uncertain numbered side/SP/OVA/movie-like supplemental rows, call find_bangumi_targets_for_local_file yourself before final submit; use its duration_candidate_episode_rows to judge whether a supportable exposed row exists. A different subject_id alone is not a contradiction for side/SP/OVA/movie-bundle extras; require concrete relation/title/duration/locator mismatch evidence before recording candidate_rows_not_supportable.
Use issue_repair_contexts from validation/submission feedback before cheap supplemental patches; repair or exhaust the named target surface first.
Use episode_ids only with selected exact_paths for one-to-one expansion, never together with episode_id/sort/ep; use append_rules only for new names and patch_rules/replace_rules for existing names.
Do not inspect old run artifacts, repository tests, or Python schemas as case evidence.
After accepted=true, submit explicitly with submit_organize_recipe_params or submit_organize_recipe_params_patch, then call goal_complete. If strict evidence cannot support the case, call fail_closed with a concrete reason, then goal_complete.
`.trim();
}

const result = {
  ok: false,
  status: "error",
  case_id: caseInput.case_id || "",
  instruction_path: instructionPath,
  event_log_path: path.join(path.dirname(outputPath), "pi_event_log.json"),
  assistant_log_path: path.join(path.dirname(outputPath), "pi_assistant_messages.json"),
};
let requiredSkillDiscovery = { discovered: [], missing: [] };
let extensionLoadErrors = [];
let promptTemplateExpanded = "";
let forcedSkillLoadTelemetry = {
  attempted: false,
  succeeded: false,
  error: "",
  fallback: false,
  fallback_succeeded: false,
  fallback_error: "",
};

try {
  const effectiveAgentDir = agentDir || process.env.PI_CODING_AGENT_DIR || path.join(repoRoot, ".pi", "agent");
  const resourceLoader = new DefaultResourceLoader({
    cwd: repoRoot,
    agentDir: effectiveAgentDir,
    additionalExtensionPaths: EXTENSION_PATHS,
    appendSystemPromptOverride: (base) => [
      ...base,
      ACTION_AGENT_SYSTEM_PROMPT_SECTION,
    ],
  });
  await resourceLoader.reload();
  extensionLoadErrors = resourceLoader.getExtensions().errors || [];
  requiredSkillDiscovery = discoverRequiredSkills(resourceLoader);
  promptTemplateExpanded = await readExpandedPrimaryPromptTemplate();
  const { session } = await createAgentSession({
    cwd: repoRoot,
    agentDir: effectiveAgentDir,
    authStorage,
    modelRegistry,
    model: selectedModel,
    resourceLoader,
    tools: enabledToolNames,
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
    const instructionText = buildInstructionText(promptTemplateExpanded, {
      forcedSkillLoadAttempted: forcedSkillLoadTelemetry.attempted,
      forcedSkillLoadSucceeded: forcedSkillLoadTelemetry.succeeded,
      forcedSkillLoadFallback: forcedSkillLoadTelemetry.fallback,
    });
    const goalObjective = buildGoalObjective(promptTemplateExpanded);
    await fs.writeFile(instructionPath, instructionText, "utf8");
    const promptDone = session
      .prompt(`/goal ${goalObjective}`, { expandPromptTemplates: true, source: "api" })
      .then(() => ({ ok: true }))
      .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
    const finalWait = await waitForFinalResultWithNudge(session, promptDone);
    const draftValidated = finalWait.auto_validate_ready_draft
      ? { finalPayload: finalWait.payload, autoValidateDraft: finalWait.auto_validate_ready_draft }
      : await validateReadyDraftIfNeeded(finalWait.payload);
    const movieLocatorRepair = await repairMovieSubjectLevelLocatorIfNeeded();
    const helperCheck = await ensureHelperCheckArtifact();
    const finalPayload = draftValidated.finalPayload;
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
      extensions_loaded: ["local-bangumi-tools", "@narumitw/pi-goal", "@narumitw/pi-retry"],
      retry_stall_timeout_ms: process.env[RETRY_STALL_TIMEOUT_ENV],
      extension_load_errors: extensionLoadErrors.map((item) => ({
        path: item.path,
        error: item.error,
      })),
      required_skills_discovered: requiredSkillDiscovery.discovered.map((item) => ({
        name: item.name,
        file_path: item.filePath,
        description: item.description,
      })),
      required_skills_missing: requiredSkillDiscovery.missing,
      forced_skill_load_attempted: forcedSkillLoadTelemetry.attempted,
      forced_skill_load_succeeded: forcedSkillLoadTelemetry.succeeded,
      forced_skill_load_error: forcedSkillLoadTelemetry.error,
      forced_skill_load_fallback: forcedSkillLoadTelemetry.fallback,
      forced_skill_load_fallback_succeeded: forcedSkillLoadTelemetry.fallback_succeeded,
      forced_skill_load_fallback_error: forcedSkillLoadTelemetry.fallback_error,
      prompt_template_used: PRIMARY_PROMPT_TEMPLATE_NAME,
      prompt_template_path: PRIMARY_PROMPT_TEMPLATE_PATH,
      prompt_template_invocation: PRIMARY_PROMPT_INVOCATION,
      action_system_prompt_appended: true,
      custom_tools_enabled: customToolNames,
      skills_loaded: REQUIRED_SKILL_NAMES,
      helper_check: helperCheck,
      auto_validate_ready_draft: draftValidated.autoValidateDraft,
      auto_repair_movie_subject_locator: movieLocatorRepair,
      submit_after_validation_mode: "explicit_pi_tool_call_only",
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
    required_skills_discovered: requiredSkillDiscovery.discovered.map((item) => ({
      name: item.name,
      file_path: item.filePath,
      description: item.description,
    })),
    required_skills_missing: requiredSkillDiscovery.missing,
    forced_skill_load_attempted: forcedSkillLoadTelemetry.attempted,
    forced_skill_load_succeeded: forcedSkillLoadTelemetry.succeeded,
    forced_skill_load_error: forcedSkillLoadTelemetry.error,
    forced_skill_load_fallback: forcedSkillLoadTelemetry.fallback,
    forced_skill_load_fallback_succeeded: forcedSkillLoadTelemetry.fallback_succeeded,
    forced_skill_load_fallback_error: forcedSkillLoadTelemetry.fallback_error,
    prompt_template_used: PRIMARY_PROMPT_TEMPLATE_NAME,
    prompt_template_path: PRIMARY_PROMPT_TEMPLATE_PATH,
    prompt_template_invocation: PRIMARY_PROMPT_INVOCATION,
    action_system_prompt_appended: true,
  });
}

flushAssistantMessage();
result.assistant_output = buildAssistantOutputStats();
await fs.writeFile(result.event_log_path, JSON.stringify(eventLog, null, 2), "utf8");
await fs.writeFile(result.assistant_log_path, JSON.stringify(assistantMessages, null, 2), "utf8");
await fs.writeFile(outputPath, JSON.stringify(result, null, 2), "utf8");
console.log(JSON.stringify(result));
process.exit(result.ok ? 0 : 1);
