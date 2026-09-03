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
import {
  discoverRequiredSkills,
  effectiveRuntimeBudgetSeconds,
  extractMessageText,
  parseArgs,
  promptWithResult,
  safePreview,
  sleep,
  stripMarkdownFrontmatter,
} from "./pi_runner_shared.mjs";

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
  "MULTI-SEASON COVERAGE workflow (cover as many missing videos as a human would): group missing videos by `bangumi_subject_id` -> for a subject with uncovered videos, search_candidates(keywords=[subject_name, subject_name_cn, ...variants]) -> inspect titles -> load_candidate_packages -> submit_candidate(candidate_ref, language, bangumi_subject_id, reason) -> inspect_package -> submit_package(package_ref, reason) -> CHECK which missing videos are still uncovered -> pick another uncovered subject's name and search again -> repeat until all covered or all subjects exhausted -> submit_complete(reason).",
  "A task may span multiple BGM subjects (e.g. Demon Slayer S01+S02+S03+movie = 4 subjects). Each subject usually has its own subtitle thread. ONE thread may cover multiple subjects (e.g. 'ARIA The AVVENIRE + Aria The Arietta' combined OVA thread) — selecting that package once covers both; do NOT select it twice.",
  "Search-term strategy: prefer per-video `subject_name` (Japanese original, e.g. 鬼滅の刃 遊郭編) — hits cleanly but can miss (無限列車編 only hits movie thread, misses TV arc). Also try `subject_name_cn` (Chinese, 鬼灭之刃 游郭篇) — hits more but noisier (PV/花屏/求字幕 threads). Use MULTIPLE variants (Japanese/Chinese/romanized, with/without 篇/编 suffix), disambiguate from mixed results by thread title + package post_text. Do NOT use TMDB English season names (0 hits). Fall back to task-level bgm_subject_name only when per-video subject_name is empty.",
  "search_candidates and load_candidate_packages are batch-limited per call: if a result has remaining_keywords / remaining_candidate_refs, more were deferred — inspect what you have first, and only call again with the remaining refs if none covers the missing videos. Do not try to load everything at once.",
  "CRITICAL: a search hit only returns the thread title. Until you call load_candidate_packages for a candidate, its `packages_loaded=false` and `package_count`/`has_downloadable_attachment` read as `null` (unknown), NOT `0`/`false`. Never read `null` as 'no package' and never fail_closed with no_downloadable_candidates based on `null` package fields — you MUST load_candidate_packages first to turn `null` into a real count. Only after packages_loaded=true can package_count=0 legitimately mean 'no attachment', and even then try another candidate before fail_closed.",
  "Use the CD<idx> candidate refs and PK<idx> package refs shown in context / search results. Titles, detail URLs, and filenames are evidence only; submit must reference the short refs.",
  "Each MV<idx> missing video card has both `video` (post-rename target filename) and `source_video` (pre-rename local original filename, evidence only, may be empty). When the subtitle release group / naming matches the original local files, `source_video` is a stronger pairing hint; prefer it for matching when non-empty.",
  "Each MV<idx> also carries `bangumi_subject_id`/`subject_name`/`subject_name_cn` (per-video BGM subject — group missing videos by this for multi-season coverage) and `preferred_language` (user subtitle language preference, default zh-CN). Use preferred_language to break ties between eligible main-episode packages: zh-CN prefers simplified then bilingual then traditional; zh-TW prefers traditional then bilingual then simplified. This is a preference, not a gate.",
  "submit_candidate gate: candidate must have packages_loaded=true AND (downloadable attachment or packages). Pass bangumi_subject_id to declare which subject this thread covers. Submitting an unloaded candidate (null package fields) is rejected with a 'load first' hint — call load_candidate_packages then re-submit.",
  "submit_package gate: package must have a downloadable link (the ONLY package gate — fixed layer no longer rejects font/special packages; package nature is YOUR judgment from post_text + link labels, see SKILL). submit_package DOES NOT finish the case — it appends a selection (returns selections_count + covered_subject_ids). Continue with more subjects or call submit_complete when done. Pass link_url to pin a specific attachment and bangumi_subject_id to declare which subject this selection covers (essential when one package's links map to different subjects).",
  "submit_complete: terminal accepted path. Requires at least one selection. Does NOT require every subject to have a selection (uncovered subjects with no forum thread are left uncovered — qualified result). If ZERO packages found across all subjects, call fail_closed instead of submit_complete.",
  "Quality ordering among same-language main-episode packages: prefer `revision` flag (修正版/校对/v2/v3/fix) over unmarked, prefer `batch` over single-episode when missing videos span a season, and read inspect_package `post_text` to judge coverage completeness — floor text is the strongest signal, filenames/flags are hints.",
  "DO NOT STOP AFTER ONE SUBJECT. A single package usually covers one season; other seasons stay uncovered. You MUST keep searching other subjects until all videos are covered or all subjects are exhausted. fail_closed with no_downloadable_candidates based on null/unloaded package fields is a CONTRACT VIOLATION. If genuinely uncertain between candidates, call need_confirm.",
  "Do not download files inside the agent; submit_package/submit_complete return the selections for the Python layer to download.",
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

async function readStateSnapshot() {
  try {
    const response = await fetch(`${server}/state`);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
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
    `Search the subtitle provider for candidates by BGM subject name variants and load them as CD<idx> facts. Pass BGM name variants (name_cn / name / source title) as keywords[]; the tool searches a batch-limited subset per call (primary variants first) and reports remaining_keywords if more are deferred — call again with the remaining keywords only if no loaded candidate matches the arc.\n${selectionQuickReference}`,
    objectSchema({ keywords: Json.Array(Json.String()), limit: Json.Optional(Json.Number()) }),
    {
      promptSnippet: "Search the provider by BGM subject name variants (batch-limited)",
      promptGuidelines: [
        "Pass BGM subject name variants (bgm_subject_name_cn, bgm_subject_name, source title hint) as keywords[]; primary variants are searched first.",
        "If the result has remaining_keywords, more variants were deferred — only search them if no loaded candidate matches the arc.",
        "Inspect returned candidate titles to pick the matching arc (right season / not OVA / not special for main episodes).",
      ],
    },
  ),
  proxyTool(
    "load_candidate_packages",
    "Load Candidate Packages",
    "Deep-load thread packages for candidates as PK<idx> facts. Pass plausible candidate_refs; the tool loads a batch-limited subset per call and reports remaining_candidate_refs if more are deferred — inspect the loaded ones first, then call again with the remaining refs only if none matches.",
    objectSchema({ candidate_refs: Json.Array(Json.String()) }),
    {
      promptSnippet: "Deep-load packages for candidates (batch-limited)",
      promptGuidelines: [
        "Pass candidate_refs you want to inspect; the tool loads a batch-limited subset per call.",
        "If the result has remaining_candidate_refs, inspect the loaded candidates first; only load the rest if none matches the arc.",
        "Call after search_candidates to populate package cards before submit_package.",
      ],
    },
  ),
  proxyTool(
    "inspect_package",
    "Inspect Package",
    "Read a package's full details (post_text, links, flags) to judge main episode vs special/font.",
    objectSchema({ package_ref: Json.String() }),
    {
      promptSnippet: "Inspect a package before selecting it",
      promptGuidelines: ["Judge package nature yourself from post_text + link labels. BD-BOX/BDRip/Raw in a label is the video SOURCE the subtitle is timed for, NOT a video file (subtitle forums don't host video); the attachment is almost always a .rar/.zip/.7z/.ass. A 楼主 package may be a nested archive (outer .rar → inner .rar per season); the extractor unpacks nested archives, so select it if post_text confirms subtitles. See SKILL."],
    },
  ),
  proxyTool(
    "submit_candidate",
    "Submit Candidate",
    `Select a candidate thread + language. Gate: candidate must have packages_loaded=true AND (downloadable attachment or packages). Submitting an unloaded candidate (null package_count) is rejected — call load_candidate_packages first.\nParam bangumi_subject_id: declare which missing-video subject this thread covers (from the missing video card). One thread may cover several subjects — pass the subject id it is being selected for so the selection is accounted against that subject's uncovered videos. For multi-subject tasks you will call submit_candidate + submit_package again for each remaining subject.\n${selectionQuickReference}`,
    objectSchema({
      candidate_ref: Json.String(),
      language: Json.Optional(Json.String()),
      reason: Json.Optional(Json.String()),
      bangumi_subject_id: Json.Optional(Json.Number()),
    }),
    {
      promptSnippet: "Select the matching candidate thread for a subject",
      promptGuidelines: ["Submit only after load_candidate_packages (so packages_loaded=true); pick the candidate whose arc matches the missing videos of ONE subject.", "Pass bangumi_subject_id so this selection is accounted against that subject."],
    },
  ),
  proxyTool(
    "submit_package",
    "Submit Package",
    `Select a package to download. Gate: has_downloadable_link (the ONLY package gate — the fixed layer no longer rejects font/special packages; package nature is YOUR judgment, see SKILL "Package nature judgment"). IMPORTANT: submit_package does NOT finish the case — it APPENDS one selection (thread + package + language + bangumi_subject_id). After submit_package, re-check uncovered videos; if more subjects still lack subtitles, search + load + submit_candidate + submit_package again for those subjects. Only when you judge no more useful packages can be obtained, call submit_complete to finish. Do NOT stop after one subject.\n\nATTACHMENT SELECTION (link_url): a package may contain SEVERAL direct-download attachments in one floor — e.g. a thread bundles 前篇 [01-04].zip AND 後篇 [05-08].7z as two separate attachments in the same post. The fixed layer does NOT pick which attachment to download — YOU pick. Call inspect_package first to see every link's label/filename_hint/kind/is_direct_download, then pass link_url with the EXACT url of the attachment that covers the subject's missing videos (match by label/filename: 前篇/前章/01-04 vs 後篇/後章/05-08, simplified .sc vs traditional .tc, etc.). If a package has multiple main-episode attachments covering DIFFERENT episode ranges (like 前篇+後篇), submit_package once per attachment with its link_url — each becomes a separate selection that downloads and pairs independently. If you omit link_url, the first downloadable attachment is used (fine for single-attachment packages, but for multi-attachment packages you SHOULD specify to avoid downloading the wrong one). The link_url must be one of the package's direct-download links (inspect_package shows them).\n\nSUBJECT DECLARATION (bangumi_subject_id): pass bangumi_subject_id to declare which BGM subject this selection covers — essential when one package's links cover DIFFERENT subjects (e.g. 前篇 link → subject 319390, 後篇 link → subject 352905). This keeps the selection accounted against the right subject's uncovered videos. If omitted, the subject from the last submit_candidate for this candidate is used (which may be wrong if you submit_candidate multiple times for the same thread with different subjects — prefer passing it explicitly with link_url).\n${selectionQuickReference}`,
    objectSchema({
      package_ref: Json.String(),
      reason: Json.Optional(Json.String()),
      link_url: Json.Optional(Json.String()),
      bangumi_subject_id: Json.Optional(Json.Number()),
    }),
    {
      promptSnippet: "Append a package selection for one subject",
      promptGuidelines: [
        "Submit only after load_candidate_packages + inspect_package.",
        "submit_package APPENDS a selection — it is NOT terminal. Re-check uncovered videos afterwards.",
        "For multi-subject tasks, repeat submit_candidate + submit_package for each subject that still lacks subtitles.",
        "For multi-attachment packages, inspect_package first and pass link_url = the exact attachment url covering the subject's episodes (前篇/後篇, simplified/traditional, etc.); submit once per attachment if one floor bundles several main-episode archives.",
        "Pass bangumi_subject_id with link_url when one package's attachments map to different subjects (前篇→A, 後篇→B), so the selection is accounted against the right subject.",
        "Package nature (subtitle vs font vs special) is YOUR judgment from post_text + link labels — the fixed layer no longer tags it. See SKILL.",
      ],
    },
  ),
  proxyTool(
    "submit_complete",
    "Submit Complete",
    `Terminal accepted path. Call ONLY after at least one submit_package has appended a selection. submit_complete finishes the case with all accumulated selections (thread + package + language + bangumi_subject_id tuples). Gate: requires ≥1 selection; does NOT require every missing-video subject to be covered (if some subjects have no acgrip thread / no downloadable package, leave them uncovered and still submit_complete — the workflow reports them as uncovered). Do NOT call submit_complete with zero selections — that is fail_closed, not submit_complete.\n${selectionQuickReference}`,
    objectSchema({ reason: Json.Optional(Json.String()) }),
    {
      promptSnippet: "Finish after accumulating ≥1 package selection",
      promptGuidelines: [
        "Call only after ≥1 submit_package.",
        "Required: at least one selection. Not required: every subject covered.",
        "If no package could be selected at all, use fail_closed instead.",
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
      promptGuidelines: ["Use fail_closed for concrete wrong-arc / no-candidate situations, and ONLY after you have load_candidate_packages for the plausible candidates and confirmed their packages are truly empty (packages_loaded=true, package_count=0). fail_closed with no_downloadable_candidates based on null/unloaded package fields is a contract violation."],
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
  const loadCalls = toolNames.filter((name) => name === "load_candidate_packages").length;
  const submitCandCalls = toolNames.filter((name) => name === "submit_candidate").length;
  const submitPkgCalls = toolNames.filter((name) => name === "submit_package").length;
  const submitCompleteCalls = toolNames.filter((name) => name === "submit_complete").length;
  if (!contextCalls && toolNames.length) {
    lines.push("Next: call get_auto_fetch_context to read missing videos and scan scope.");
  }
  if (contextCalls && !searchCalls && !submitCandCalls) {
    lines.push("Next: search_candidates with a title/source hint.");
  }
  if (searchCalls && !loadCalls) {
    lines.push("Next: load_candidate_packages for plausible candidates — search only returned thread titles, package_count is still null (unknown). You MUST load before judging downloadability or submit_candidate. Do NOT fail_closed no_downloadable based on null package fields.");
  }
  if (submitCandCalls && !submitPkgCalls) {
    lines.push("Candidate accepted but no package submitted. inspect_package + submit_package.");
  }
  // 多季覆盖 nudge：读 state 判断已 submit 几个包 + 仍有未覆盖 subject
  const snapshot = await readStateSnapshot();
  if (snapshot && snapshot.ok) {
    const selCount = Number(snapshot.selections_count || 0);
    const uncovered = Array.isArray(snapshot.uncovered_subject_ids) ? snapshot.uncovered_subject_ids : [];
    const totalSubjects = Number(snapshot.total_subject_count || 0);
    if (submitPkgCalls && !submitCompleteCalls && selCount > 0) {
      lines.push(`submit_package has appended ${selCount} selection(s) but submit_complete has NOT been called.`);
      if (uncovered.length && totalSubjects > 1) {
        lines.push(
          `STILL UNCOVERED: ${uncovered.length} of ${totalSubjects} subject(s) have no package yet (subject ids: ${uncovered.join(", ")}). Do NOT stop now — search_candidates + load_candidate_packages + submit_candidate + submit_package for the remaining subject(s). A single package usually covers one season.`,
        );
      } else {
        lines.push("All subjects are covered (or only one subject exists). If you judge no more useful packages can be obtained, call submit_complete to finish.");
      }
    }
    if (submitCompleteCalls) {
      lines.push("submit_complete already called — case is terminal.");
    }
  }
  return lines;
}

async function waitForFinalResultOrIdle(session, promptDone, options = {}) {
  const defaultWaitMs = Math.max(
    1_000,
    effectiveRuntimeBudgetSeconds(caseInput.runtime_policy) * 1_000,
  );
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
  const totalBudgetMs = Math.max(
    30_000,
    effectiveRuntimeBudgetSeconds(caseInput.runtime_policy) * 1_000,
  );
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
    "- load_candidate_packages (turns null package_count into a real value — REQUIRED before submit_candidate)",
    "- submit_candidate (with bangumi_subject_id)",
    "- inspect_package",
    "- submit_package (APPENDS a selection — NOT terminal; continue with other subjects)",
    "- submit_complete (terminal, ONLY after ≥1 submit_package; does NOT require every subject covered)",
    "- patch named gate issue",
    "- concrete fail_closed / need_confirm (only after packages_loaded=true; never on null package fields)",
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
      "- submit_package (APPENDS a selection — not terminal)",
      "- submit_complete (terminal, only after ≥1 submit_package; does NOT require every subject covered)",
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
    if (hardWait.payload?.final_result) {
      finalWait = hardWait;
    }
  }

  // Final repair loop（参考 pi_case_agent_runner.mjs）：Pi 卡住/无 final 时多轮
  // nudge 把它拉回工具调用或 fail_closed，而不是 2 次 nudge 就放弃交 Python 兜底
  // budget_exhausted。大样本（多候选多包）实测会卡在 load 后无响应，需循环拉回。
  let repairAttempt = 0;
  const maxRepairAttempts = 3;
  while (!finalWait.payload?.final_result && repairAttempt < maxRepairAttempts) {
    const remainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
    if (remainingMs < 20_000) break;
    const progressLines = await readRunnerProgressNudgeLines();
    const gateLines = await readLatestGateNudgeLines();
    const repairText = [
      "Final repair loop: call one auto fetch tool or close with a concrete evidence reason.",
      "This turn must be exactly one custom tool call or fail_closed/need_confirm; no prose.",
      "- submit_package (if a package is chosen — APPENDS a selection, not terminal)",
      "- submit_complete (if ≥1 package already selected and no more useful packages can be obtained — terminal)",
      "- patch named gate issue (invalid_ref_shape → use the PK<idx> shown in context; not_downloadable → pick another package; candidate_not_downloadable on unloaded candidate → load_candidate_packages first)",
      "- load_candidate_packages + inspect_package (if packages still null/unloaded or still gathering evidence)",
      "- search_candidates (if a remaining uncovered subject still needs a thread)",
      "- concrete fail_closed / need_confirm (only after packages_loaded=true; null package fields are NOT a fail_closed reason)",
      ...progressLines,
      ...gateLines,
      "No budget_exhausted fail_closed. Do not reread skills or narrate.",
    ].join("\n");
    const repairDone = session
      .prompt(repairText, { expandPromptTemplates: true, source: "api", streamingBehavior: "followUp" })
      .then(() => ({ ok: true }))
      .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
    const repairWait = await waitForFinalResultOrIdle(session, repairDone, { waitMs: Math.min(90_000, remainingMs) });
    nudgeAttempts.push({
      phase: `final_repair_${repairAttempt + 1}`,
      wait_iterations: repairWait.waitIterations,
      wait_timeout_ms: repairWait.wait_timeout_ms,
      idle_drained: repairWait.idle_drained,
      prompt_settled: repairWait.prompt_settled,
      prompt_error: repairWait.prompt_error,
      final_result_present: Boolean(repairWait.payload?.final_result),
    });
    if (repairWait.payload?.final_result) {
      finalWait = repairWait;
      break;
    }
    // settle wait：repair 后 Pi 可能在 settle 阶段才出 final
    const remainingAfterRepairMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
    if (!repairWait.idle_drained && remainingAfterRepairMs >= 20_000) {
      const settleWait = await waitForFinalResultOrIdle(session, repairDone, { waitMs: Math.min(90_000, remainingAfterRepairMs) });
      nudgeAttempts.push({
        phase: `final_repair_${repairAttempt + 1}_settle`,
        wait_iterations: settleWait.waitIterations,
        wait_timeout_ms: settleWait.wait_timeout_ms,
        idle_drained: settleWait.idle_drained,
        prompt_settled: settleWait.prompt_settled,
        prompt_error: settleWait.prompt_error,
        final_result_present: Boolean(settleWait.payload?.final_result),
      });
      if (settleWait.payload?.final_result) {
        finalWait = settleWait;
        break;
      }
    }
    repairAttempt += 1;
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

const recoveryFeedback = Array.isArray(
  caseInput?.context?.prior_download_feedback,
)
  ? caseInput.context.prior_download_feedback
  : [];
const recoveryInstruction = recoveryFeedback.length > 0
  ? `\nThis is preferred-language recovery after ${recoveryFeedback.length} downloaded selection(s) failed or remained unconfirmed. Re-rank ALL remaining candidates across sources; do not follow display order or walk adjacent packages mechanically. Prefer explicit requested-script evidence over unlabeled packages. After two failures from the same candidate title/source, switch candidate/source unless a new attachment has explicit requested-script evidence.`
  : "";

const instructionText = `
Complete this subtitle auto fetch case.
Read the case input JSON at: ${inputPath}
Use get_auto_fetch_context for the missing video cards (MV<idx>) and scan scope.
${selectionQuickReference}${recoveryInstruction}
Search candidates matching the missing videos' title / source_video hint. Inspect candidate arcs; submit the matching candidate + language. Then load packages, inspect the main-episode package, and submit_package. Then call goal_complete.
If no candidate matches the arc (wrong season / OVA / 特别篇), call fail_closed with a concrete reason. If genuinely uncertain between candidates, call need_confirm.
Do not use native tools to download, move, copy, link, rename, or inspect old run artifacts for answers.
Available lazy skills:
/skill:auto-fetch-contract: use when selection workflow, ref policy, or gate repair is unclear.
${ACTION_AGENT_OUTPUT_CONTRACT}
Try to finish before ${caseInput.runtime_policy?.suggested_finish_before_seconds ?? 0} seconds.
`.trim();

const goalObjective = `
Produce an accepted candidate + package selection for fetching a subtitle archive for the missing videos, or fail closed / need confirm for global ambiguity. This is dry-run only (no download inside the agent).${recoveryInstruction}

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
  requiredSkillDiscovery = discoverRequiredSkills(
    resourceLoader,
    REQUIRED_SKILL_NAMES,
  );
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
