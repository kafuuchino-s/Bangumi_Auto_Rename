#!/usr/bin/env node

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

// Local-to-Bangumi dry runs stay on the case-scoped custom tool surface, while
// retaining Pi's official progressive-disclosure path for skills/references.
// Enable read only; no shell/edit/write/listing tools are exposed.
const NATIVE_TOOL_NAMES = ["read"];
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
const REQUIRED_SKILL_NAMES = ["tmdb-bridge-contract"];
const PRIMARY_SKILL_NAME = "tmdb-bridge-contract";
const PRIMARY_SKILL_LOAD_COMMAND =
  `/skill:${PRIMARY_SKILL_NAME} Load this skill as method context for the upcoming BGM-to-TMDB bridge task. ` +
  "During this skill-load step only, do not run bridge tools; the next prompt is the task to execute immediately.";

const ACTION_AGENT_OUTPUT_CONTRACT = [
  "Visible output contract: act through tools and artifacts, not reasoning prose.",
  "Do not write headings such as Deciding, Evaluating, Considering, or explain why a tool should be called.",
  "When a custom tool or goal_complete is available, call it directly with no prose; otherwise write one short blocker sentence.",
  "Do not print recipe JSON, full mapping tables, full verifier issues, or old artifact excerpts in assistant text.",
  "Tool arguments count as output: keep board notes, snapshots, reasons, and summaries compact.",
  "For the bridge, the first move is one reliable main-title search, then hydrate the TMDB legal graph; do not fail_closed from an empty draft before a plausible anchor exists unless the case input is malformed.",
  "Do not fail_closed while any BGM-mapped node (regular, special, OVA/OAD, movie, span, side-story) still has no rule: an unsearched BGM movie/special is a missing rule, not global ambiguity.",
  "After a TMDB anchor is hydrated, validate recipe params; do not keep broad-searching recap/summary/CM/bonus-title variants when the graph already carries enough legal-node evidence.",
].join("\n");

const ACTION_AGENT_SYSTEM_PROMPT_SECTION = `
## BGM-to-TMDB Bridge Action Agent Output Protocol

This session runs as an action-oriented bridge agent. Reason internally, then externalize durable work through custom tools and artifacts.

- Assistant-visible text is a status channel, not a scratchpad.
- On a turn that can call a custom tool or goal_complete, call the tool directly and omit explanatory prose.
- Do not print chain-of-thought, self-review headings, mapping tables, recipe JSON, drafts, verifier issue dumps, or copied tool JSON.
- A normal non-final text-only response is at most one short blocker sentence naming the next missing fact or terminal fail_closed reason.
- When a TMDB anchor is plausible, hydrate the legal graph and draft compact recipe params.
- When the draft is complete, validate_bgm_to_tmdb_bridge_recipe_params is the next visible action; do not describe that you are ready.
- When validation is accepted, submit with submit_bgm_to_tmdb_bridge_recipe_params and then call goal_complete; do not summarize before submitting.
- Keep validation_snapshot, patch_delta, submit_snapshot, reason, and summary short. Transaction notes use strict small envelopes, not arbitrary JSON.
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

const bridgeSearchGuidelines = [
  "Search to identify one plausible TMDB anchor, not to prove every title variant.",
  "After one anchor is plausible, hydrate the legal graph and compare episode titles, order, counts, and season cards before more searching.",
  "Use targeted season-0 or episode-title checks for BGM nodes that TMDB might not expose; then prefer tmdb_absent_group instead of widening the search loop.",
].join("\n");

const bridgeValidateGuidelines = [
  "Validate compact recipe params, not raw per-source node mappings.",
  "If the hydrated graph supports the rule, submit the same canonical params.",
  "If a BGM-mapped node has no TMDB legal node after targeted checks, keep it as tmdb_absent_group; do not convert it to supplemental.",
].join("\n");

const bridgeSubmitGuidelines = [
  "Submit only accepted canonical recipe params.",
  "This is the terminal structured-output path for normal bridge completion.",
].join("\n");

const bridgeFailClosedGuidelines = [
  "Use fail_closed only for concrete global TMDB ambiguity or contradiction: two or more equally-plausible TMDB refs for the same BGM node with no deciding evidence.",
  "Do not use fail_closed just because one mapped BGM node is absent from TMDB; use tmdb_absent_group for that case.",
  "Before fail_closed, recheck: (1) every BGM-mapped subject/assignment (regular, special, OVA/OAD, movie, span, side-story) already has a rule with an explicit disposition; (2) at least one TMDB anchor was searched AND hydrated via get_tmdb_legal_graph; (3) any BGM movie/special/OVA/OAD/side-story you never searched or hydrated is a missing rule, not global ambiguity — search the title, hydrate, then map_to_tmdb or tmdb_absent_group.",
  "The common fail-closed trap: anchor only the TV series, map the regular sequence, then fail_closed the whole case because a BGM movie/special in the same package was never searched. If the frontier scan still has unmapped BGM nodes, you have not finished exploring.",
].join("\n");

const bridgeConfidenceSchema = StringEnum(["High", "Medium", "Low"]);
const bridgeMappingDispositionSchema = StringEnum([
  "map_to_tmdb",
  "tmdb_target_absent",
  "unmapped_supplemental",
]);
const bridgeRuleTypeSchema = StringEnum([
  "episode_sequence",
  "movie",
  "special_sequence",
  "span",
  "tmdb_absent_group",
  "supplemental_group",
]);
const bridgeNumberFieldSchema = StringEnum(["sort", "ep", "extracted_episode_number"]);
const bridgeMediaKindSchema = StringEnum(["tv", "movie", "ova", "oad", "sp", "special", "unknown"]);
const bridgeEpisodeTypeSchema = StringEnum(["main", "regular", "special", "ova", "oad", "movie", "unknown"]);
const bridgeSelectorSchema = Json.Object({
  bangumi_subject_id: Json.Optional(Json.Number()),
  media_kind: Json.Optional(bridgeMediaKindSchema),
  episode_type: Json.Optional(bridgeEpisodeTypeSchema),
  sort_range: Json.Optional(Json.String()),
  ep_range: Json.Optional(Json.String()),
  episode_ids: Json.Optional(Json.Array(Json.Number())),
  rule_name: Json.Optional(Json.String()),
  source_paths: Json.Optional(Json.Array(Json.String())),
});
const bridgeTargetSchema = Json.Object({
  tmdb_ref: Json.Optional(Json.String()),
  season_number: Json.Optional(Json.Number()),
  episode_range: Json.Optional(Json.String()),
  episode_offset: Json.Optional(Json.String()),
  number_field: Json.Optional(bridgeNumberFieldSchema),
  tmdb_legal_node_id: Json.Optional(Json.String()),
});
const strictRecipeParamsRuleSchema = Json.Object({
  name: Json.String(),
  rule_type: bridgeRuleTypeSchema,
  select_bgm: bridgeSelectorSchema,
  target_tmdb: bridgeTargetSchema,
  confidence: bridgeConfidenceSchema,
  reason: Json.String(),
});
const strictRecipeParamsSchema = Json.Object({
  version: Json.Optional(Json.Number()),
  summary: Json.Optional(Json.String()),
  rules: Json.Array(strictRecipeParamsRuleSchema),
});
const strictBridgeMappingSchema = Json.Object({
  source_path: Json.String(),
  disposition: bridgeMappingDispositionSchema,
  tmdb_legal_node_ids: Json.Array(Json.String()),
  confidence: bridgeConfidenceSchema,
  reason: Json.String(),
});
const strictBridgeDraftSchema = Json.Object({
  summary: Json.Optional(Json.String()),
  mappings: Json.Array(strictBridgeMappingSchema),
});

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

User: Load this skill as method context for the upcoming BGM-to-TMDB bridge task. During this skill-load step only, do not run bridge tools; the next prompt is the task to execute immediately.`;
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

const recipeParamsQuickReference = [
  "Primary workflow: write compact recipe_params, not one source_path->node mapping per normal episode.",
  "Minimal recipe_params shape: {\"version\":1,\"summary\":\"...\",\"rules\":[{\"name\":\"main_tv\",\"rule_type\":\"episode_sequence\",\"select_bgm\":{\"bangumi_subject_id\":100,\"episode_type\":\"regular\",\"sort_range\":\"1-26\"},\"target_tmdb\":{\"tmdb_ref\":\"tv:45844\",\"season_number\":1,\"episode_range\":\"1-26\",\"number_field\":\"sort\"},\"confidence\":\"High\",\"reason\":\"TMDB title/original/alias/year/season cards match the Bangumi subject.\"},{\"name\":\"missing_specials\",\"rule_type\":\"tmdb_absent_group\",\"select_bgm\":{\"bangumi_subject_id\":100,\"episode_type\":\"special\",\"sort_range\":\"1-3\"},\"confidence\":\"High\",\"reason\":\"Hydrated TMDB season 0 and episode-title checks expose no legal node for these BGM specials.\"},{\"name\":\"extras\",\"rule_type\":\"supplemental_group\",\"select_bgm\":{},\"confidence\":\"Medium\",\"reason\":\"Accepted BGM plan marks these as supplemental.\"}]}",
  "Rule types: episode_sequence, movie, special_sequence, span, tmdb_absent_group, supplemental_group.",
  "Target refs are tv:<id> or movie:<id>. Python hydrates the TMDB legal graph and compiles these params into tv:<id>:SxxEyy or movie:<id> nodes.",
  "A single BGM span may map to one TMDB movie node with a movie rule when TMDB models the whole span as a movie instead of individual TV episodes.",
  "When a BGM-mapped episode/special is real in Bangumi but TMDB exposes no matching legal node, cover it with tmdb_absent_group. Do not fail the whole case for that node.",
  "TMDB titles, original names, aliases, overviews, years, and URL slugs are semantic evidence. They are not target IDs.",
  "For multi-season franchise packages, search one strong series/franchise anchor first and treat its hydrated legal graph as the strongest next evidence layer before deciding whether more searches are useful.",
  "If series title evidence is ambiguous, compare BGM episode_title_cards_sample with the visible hydrated TMDB legal-node episode titles. Python tries to present one BGM-aligned TMDB evidence view, so recipe params can stay language-agnostic.",
  "Search policy: after plausible TMDB refs are found and hydrated, validate recipe params. Do not keep searching season/OVA/recap/summary/CM/bonus-title variants when the hydrated graph already carries enough legal-node evidence.",
].join("\n");

const tools = [
  proxyTool(
    "get_bgm_to_tmdb_bridge_context",
    "Get Bridge Context",
    "Read accepted BGM assignments, current TMDB legal graph, and verifier feedback.",
    objectSchema({ detail: Json.Optional(Json.Boolean()) }),
    {
      promptSnippet: "Inspect the accepted BGM frontier and current TMDB legal graph",
      promptGuidelines: [
        "Read the accepted BGM assignments and hydrate only the TMDB graph you need.",
        "Keep the accepted BGM plan as the frontier; do not rerun Local-to-Bangumi.",
      ],
    },
  ),
  proxyTool(
    "search_tmdb_candidates",
    "Search TMDB Candidates",
    "Search TMDB candidates by title evidence and return TMDB IDs plus semantic title cards.",
    objectSchema({
      query: Json.String(),
      media_type: Json.Optional(Json.String()),
      year: Json.Optional(Json.Number()),
      max_candidates: Json.Optional(Json.Number()),
    }),
    {
      promptSnippet: "Search for one plausible TMDB anchor",
      promptGuidelines: bridgeSearchGuidelines.split("\n"),
    },
  ),
  proxyTool(
    "get_tmdb_legal_graph",
    "Get TMDB Legal Graph",
    "Hydrate TMDB details and legal nodes for refs such as tv:45844 or movie:1234.",
    objectSchema({ tmdb_refs: Json.Array(Json.String()) }),
    {
      promptSnippet: "Hydrate the candidate TMDB legal graph",
      promptGuidelines: [
        "Use this after a plausible TMDB ref is found.",
        "Compare season cards, episode titles, aliases, year, and overview against the accepted BGM plan.",
      ],
    },
  ),
  proxyTool(
    "validate_bgm_to_tmdb_bridge_recipe_params",
    "Validate BGM To TMDB Recipe Params",
    `Compile and verify recipe params without finishing. This is the primary workflow.\n${recipeParamsQuickReference}`,
    objectSchema({ recipe_params: strictRecipeParamsSchema }),
    {
      promptSnippet: "Validate canonical bridge recipe params",
      promptGuidelines: bridgeValidateGuidelines.split("\n"),
    },
  ),
  proxyTool(
    "submit_bgm_to_tmdb_bridge_recipe_params",
    "Submit BGM To TMDB Recipe Params",
    `Submit final recipe params through Python compile+verify. This is the primary workflow.\n${recipeParamsQuickReference}`,
    objectSchema({
      recipe_params: strictRecipeParamsSchema,
      summary: Json.Optional(Json.String()),
    }),
    {
      promptSnippet: "Submit accepted canonical bridge recipe params",
      promptGuidelines: bridgeSubmitGuidelines.split("\n"),
    },
  ),
  proxyTool(
    "fail_closed",
    "Fail Closed",
    "Finish safely when TMDB evidence is insufficient or contradictory.",
    objectSchema({
      reason: Json.String(),
      reason_kind: Json.Optional(Json.String()),
      related_refs: Json.Optional(Json.Array(Json.String())),
    }),
    {
      promptSnippet: "Finish safely with fail_closed",
      promptGuidelines: bridgeFailClosedGuidelines.split("\n"),
    },
  ),
];

const customToolNames = tools.map((tool) => tool.name);
const enabledToolNames = [...NATIVE_TOOL_NAMES, ...EXTENSION_TOOL_NAMES, ...customToolNames];

function coerceStringArray(value) {
  if (Array.isArray(value)) return value.map((item) => String(item || "")).filter(Boolean);
  if (value === undefined || value === null || value === "") return [];
  return [String(value)];
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

const VERIFIER_FEEDBACK_TOOL_NAMES = new Set([
  "validate_bgm_to_tmdb_bridge_recipe_params",
  "submit_bgm_to_tmdb_bridge_recipe_params",
]);

function isVerifierFeedbackTraceRow(row) {
  const name = String(row?.tool || "");
  if (!VERIFIER_FEEDBACK_TOOL_NAMES.has(name)) return false;
  const summary = row?.result_summary && typeof row.result_summary === "object" ? row.result_summary : {};
  const status = String(summary.status || "").trim().toLowerCase();
  return (
    "verifier_passed" in summary
    || Number(summary.verifier_issue_count || 0) > 0
    || ["invalid", "review", "accepted"].includes(status)
  );
}

function latestVerifierFeedbackTraceRow(traceRows) {
  return [...traceRows].reverse().find(isVerifierFeedbackTraceRow) || null;
}

function latestRecipeParamsTraceRow(traceRows) {
  return [...traceRows].reverse().find((row) => String(row?.tool || "") === "submit_bgm_to_tmdb_bridge_recipe_params") || null;
}

async function readLatestVerifierNudgeLines() {
  const artifactsDir = path.join(path.dirname(outputPath), "artifacts");
  const verifierPath = path.join(artifactsDir, "bgm_to_tmdb_bridge_verifier_result.json");
  if (!(await fileExists(verifierPath))) return [];
  try {
    const verifier = await readJsonFile(verifierPath);
    const issues = Array.isArray(verifier?.issues) ? verifier.issues : [];
    const reviewWarnings = Array.isArray(verifier?.review_warnings) ? verifier.review_warnings : [];
    const issueRepairContexts = Array.isArray(verifier?.issue_repair_contexts) ? verifier.issue_repair_contexts : [];
    if (verifier?.passed === true && reviewWarnings.length === 0) {
      return ["Latest verifier: accepted with no review warnings. Next: submit accepted recipe params."];
    }
    const lines = [];
    if (verifier?.passed === true && reviewWarnings.length) {
      lines.push(`Latest verifier: review, warning_count=${reviewWarnings.length}.`);
      for (const warning of reviewWarnings.slice(0, 4)) {
        const code = warning.code || "review_warning";
        const message = warning.message || "";
        lines.push(`- ${code}: ${message}`);
        if (warning.repair_hint) {
          lines.push(`  repair_hint: ${String(warning.repair_hint).slice(0, 420)}`);
        }
      }
      lines.push("Review warnings are blocking completion. Add concrete semantic evidence to the named rule or call fail_closed with a concrete contradiction.");
    }
    if (issues.length) {
      lines.push(`Latest verifier: invalid, issue_count=${issues.length}.`);
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
    }
    for (const context of issueRepairContexts.slice(0, 3)) {
      const kind = context.repair_kind || "repair_context";
      const ref = context.ref || "";
      const nextAction = context.next_action || "";
      const flags = context.mechanical_flags && typeof context.mechanical_flags === "object" ? context.mechanical_flags : {};
      lines.push(`Repair context ${kind}${ref ? ` ${ref}` : ""}: next=${nextAction}; flags=${JSON.stringify(flags)}`);
    }
    const repairHints = Array.isArray(verifier?.repair_hints) ? verifier.repair_hints : [];
    for (const hint of repairHints.slice(0, 4)) {
      lines.push(`repair_hint: ${String(hint || "").slice(0, 520)}`);
    }
    lines.push("Next: patch named issue, one targeted search/graph check, submit accepted, or concrete fail_closed.");
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
    lines.push("Progress so far: no bridge tool calls were completed, and no final result exists.");
  } else {
    lines.push(`Progress so far: ${toolNames.length} bridge tool call(s); recent tools: ${recentTools.join(", ") || "none"}.`);
    lines.push(`Completed tool types: ${uniqueTools.join(", ")}.`);
  }
  const validationCalls = toolNames.filter((name) => name === "validate_bgm_to_tmdb_bridge_recipe_params").length;
  const submitCalls = toolNames.filter((name) => name === "submit_bgm_to_tmdb_bridge_recipe_params").length;
  const searchCalls = toolNames.filter((name) => name === "search_tmdb_candidates").length;
  const graphCalls = toolNames.filter((name) => name === "get_tmdb_legal_graph").length;
  if (!validationCalls && toolNames.length) {
    lines.push(`Run progress: no recipe validation yet; search_calls=${searchCalls}, graph_calls=${graphCalls}.`);
    if (searchCalls && !graphCalls) {
      lines.push("Next: hydrate the plausible TMDB ref with get_tmdb_legal_graph before drafting rules.");
    } else if (graphCalls) {
      lines.push("Next: draft compact recipe params and call validate_bgm_to_tmdb_bridge_recipe_params.");
    }
  }
  if (validationCalls && !submitCalls) {
    lines.push("Validation was called but no submit yet. If the latest validation returned accepted=true, submit the same params now.");
  }
  return lines;
}

async function autoSubmitLatestAcceptedParams() {
  const artifactsDir = path.join(path.dirname(outputPath), "artifacts");
  const verifierPath = path.join(artifactsDir, "bgm_to_tmdb_bridge_verifier_result.json");
  const recipeParamsPath = path.join(artifactsDir, "bgm_to_tmdb_recipe_params.json");
  const verifier = await readJsonFile(verifierPath);
  const recipeParams = await readJsonFile(recipeParamsPath);
  if (!recipeParams || verifier?.passed !== true) {
    return { attempted: false, reason: "no accepted recipe params available for auto-submit" };
  }
  const traceRows = await readToolTraceRows();
  const latestSubmit = latestRecipeParamsTraceRow(traceRows);
  if (latestSubmit && latestSubmit.result_summary?.accepted === true) {
    return { attempted: false, reason: "a submit already succeeded in the trace" };
  }
  const result = await callPythonTool("submit_bgm_to_tmdb_bridge_recipe_params", {
    recipe_params: recipeParams,
    summary: "Runner auto-submitted the latest accepted recipe params after Pi runtime ended without an explicit submit.",
  });
  return {
    attempted: true,
    ok: Boolean(result?.ok),
    accepted: Boolean(result?.accepted),
    status: result?.status || "",
    summary: result?.summary || "",
    verifier_passed: result?.verifier_result?.passed,
    verifier_issue_count: Array.isArray(result?.verifier_result?.issues) ? result.verifier_result.issues.length : 0,
  };
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
  const totalBudgetMs = Math.max(
    30_000,
    effectiveRuntimeBudgetSeconds(caseInput.runtime_policy) * 1_000,
  );
  const firstWaitMs = Math.min(totalBudgetMs, Math.max(30_000, Math.min(45_000, Math.floor(totalBudgetMs * 0.15))));
  let finalWait = await waitForFinalResultOrIdle(session, promptDone, { waitMs: firstWaitMs });
  const nudgeAttempts = [];
  let autoSubmit = null;
  async function autoSubmitAtCheckpoint(waitResult, phase) {
    if (waitResult.payload?.final_result) return waitResult;
    const submitted = await autoSubmitLatestAcceptedParams();
    if (submitted.attempted !== true) return waitResult;
    autoSubmit = submitted;
    nudgeAttempts.push({
      phase: `${phase}_auto_submit`,
      auto_submit: submitted,
      final_result_present: Boolean((await readFinalResult()).final_result),
    });
    return { ...waitResult, payload: await readFinalResult() };
  }
  finalWait = await autoSubmitAtCheckpoint(finalWait, "initial_wait");
  if (finalWait.payload?.final_result) {
    return {
      ...finalWait,
      nudge_attempts: nudgeAttempts,
      auto_submit: autoSubmit,
    };
  }

  const verifierNudgeLines = await readLatestVerifierNudgeLines();
  const progressNudgeLines = await readRunnerProgressNudgeLines();
  const nudgeText = [
    "Checkpoint: continue as an action bridge agent.",
    "If a tool action is available, call it now with no explanation. Otherwise provide one concrete blocker sentence.",
    "- search one plausible TMDB anchor",
    "- hydrate the graph",
    "- validate compact recipe params",
    "- patch named verifier issue",
    "- submit accepted",
    "- concrete fail_closed",
    ...progressNudgeLines,
    ...verifierNudgeLines,
    "Do not hand-write per-source TMDB node mappings for normal episode sequences.",
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
  finalWait = await autoSubmitAtCheckpoint(nudgeWait, "checkpoint");
  if (finalWait.payload?.final_result) {
    return {
      ...finalWait,
      nudge_attempts: nudgeAttempts,
      auto_submit: autoSubmit,
    };
  }

  if (autoSubmit?.attempted === true && autoSubmit.accepted !== true) {
    const autoRepairRemainingMs = Math.max(0, totalBudgetMs - (Date.now() - startedAt));
    if (autoRepairRemainingMs >= 20_000) {
      const progressLines = await readRunnerProgressNudgeLines();
      const verifierLines = await readLatestVerifierNudgeLines();
      const autoRepairText = [
        "Auto-submit returned invalid/review. Continue with one tool action, no reasoning narrative.",
        "This turn must be exactly one custom tool call or fail_closed; no prose.",
        "Use issue_repair_contexts and repair_hints as the repair plan.",
        "Use tmdb_absent_group for BGM nodes that TMDB does not expose; do not convert them to supplemental.",
        "Do not convert mapped BGM nodes to supplemental to make validation pass; repair TMDB ref, season, episode range, span/movie shape, or target-absent boundary first.",
        "- patch named verifier issue",
        "- fetch one targeted graph/search fact",
        "- submit accepted",
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
        phase: "auto_submit_repair",
        wait_iterations: autoRepairWait.waitIterations,
        wait_timeout_ms: autoRepairWait.wait_timeout_ms,
        idle_drained: autoRepairWait.idle_drained,
        prompt_settled: autoRepairWait.prompt_settled,
        prompt_error: autoRepairWait.prompt_error,
        final_result_present: Boolean(autoRepairWait.payload?.final_result),
      });
      finalWait = await autoSubmitAtCheckpoint(autoRepairWait, "auto_submit_repair");
      if (finalWait.payload?.final_result) {
        return {
          ...finalWait,
          nudge_attempts: nudgeAttempts,
          auto_submit: autoSubmit,
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
      "This turn must be exactly one custom tool call or fail_closed; no prose.",
      "Use issue_repair_contexts before cheap patches; target-surface mismatch must be repaired or explicitly exhausted before supplemental.",
      "Before fail_closed, recheck the frontier: every BGM-mapped node needs a rule, at least one TMDB anchor must be hydrated, and an unsearched BGM movie/special/OVA/OAD/side-story is a missing rule (search→hydrate→map_to_tmdb or tmdb_absent_group), not global ambiguity.",
      "- validate compact recipe params",
      "- patch named verifier issue",
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
    finalWait = await autoSubmitAtCheckpoint(hardWait, "hard_finish");
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
      "Final repair loop: call one bridge tool or close with a concrete evidence reason.",
      "This turn must be exactly one custom tool call or fail_closed; no prose.",
      "Follow issue_repair_contexts/repair_hints. Do not make a mapped BGM node supplemental merely to pass when the context points to a distinct target surface.",
      "Before fail_closed, recheck the frontier: any BGM movie/special/OVA/OAD/side-story never searched is a missing rule, not global ambiguity.",
      "After verifier feedback, read/status tools are not a repair; use patch, one targeted search/graph check, submit accepted, or concrete fail_closed.",
      "- validate compact recipe params",
      "- patch named verifier issue",
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
    finalWait = await autoSubmitAtCheckpoint(repairWait, `final_repair_${attemptNumber}`);
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
      finalWait = await autoSubmitAtCheckpoint(settleWait, `final_repair_${attemptNumber}_settle`);
    }
    repairAttempt += 1;
  }
  return {
    ...finalWait,
    nudge_attempts: nudgeAttempts,
    auto_submit: autoSubmit,
  };
}

const result = {
  ok: false,
  status: "invalid",
  input_path: inputPath,
  output_path: outputPath,
  case_id: caseInput.sample_id || "bgm-to-tmdb-bridge",
  instruction_path: path.join(path.dirname(outputPath), "pi_bgm_to_tmdb_goal_instructions.md"),
  event_log_path: path.join(path.dirname(outputPath), "pi_bgm_to_tmdb_event_log.json"),
  assistant_log_path: path.join(path.dirname(outputPath), "pi_bgm_to_tmdb_assistant_messages.json"),
};

let requiredSkillDiscovery = { discovered: [], missing: [] };
let extensionLoadErrors = [];
let forcedSkillLoadTelemetry = {
  attempted: false,
  succeeded: false,
  error: "",
  fallback: false,
  fallback_succeeded: false,
  fallback_error: "",
};

const instructionText = `
Complete this BGM-to-TMDB bridge dry-run.
Read the case input JSON at: ${inputPath}
This input already contains an accepted Local-to-Bangumi compiled_plan. Do not rerun Local-to-Bangumi.
Use get_bgm_to_tmdb_bridge_context for the accepted BGM assignments and current TMDB graph.
Use search_tmdb_candidates to find possible TMDB IDs. Recipe validation hydrates declared TMDB refs automatically; call get_tmdb_legal_graph only when you need detailed season cards before drafting.
${recipeParamsQuickReference}
For multi-season franchise packages, after one anchor search finds a plausible result, use its hydrated legal graph as the next evidence layer before deciding whether individual season/OVA/OAD/special searches are useful. Use the hydrated season cards, season 0 cards, aliases, and episode titles to decide whether more searches are necessary.
Treat the accepted BGM plan as the frontier. After the main TMDB anchor is hydrated, keep BGM specials, OVA/OAD, recap movies, spans, and side-story subjects in a TMDB side frontier; map any frontier node explained by hydrated legal nodes, then search additional TMDB titles only for graph misses or conflicting candidates.
When series title evidence is unclear, use BGM episode_title_cards_sample and the visible hydrated TMDB legal-node episode titles as the decisive semantic cross-check for the season/range. Python tries to present one BGM-aligned TMDB evidence view, so recipe params can stay language-agnostic.
Validate early with validate_bgm_to_tmdb_bridge_recipe_params. After it is accepted, submit the same params with submit_bgm_to_tmdb_bridge_recipe_params and then call goal_complete.
If plausible TMDB refs have been found and hydrated, do not keep searching recap/summary/CM/bonus title variants. Validate current recipe params; if a mapped BGM assignment has no concrete TMDB legal node after targeted season-0/episode-title checks, use tmdb_absent_group for that assignment and keep the rest accepted.
Do not convert BGM-mapped OVA/OAD/SP/movie/side-story nodes to supplemental to make validation pass; repair TMDB ref, season, episode range, span/movie shape, or target-absent boundary first. supplemental_group is only for assignments already supplemental in the Local-to-Bangumi plan.
If global TMDB identity evidence is insufficient or contradictory, call fail_closed with concrete related refs, then goal_complete. Do not fail_closed just because an otherwise identified BGM episode/special lacks a TMDB node; use tmdb_absent_group for that case. Before any fail_closed, run the Before Fail Closed recheck: every BGM-mapped node (regular, special, OVA/OAD, movie, span, side-story) must already have a rule with an explicit disposition, at least one TMDB anchor must be searched and hydrated, and any BGM movie/special/OVA/OAD/side-story you never searched is a missing rule (search → hydrate → map_to_tmdb or tmdb_absent_group), not global ambiguity. The common trap is anchoring only the TV series and fail_closed-ing the whole case because a BGM movie/special in the same package was never searched.
Do not use native tools to edit, write, move, copy, link, rename, or inspect old run artifacts for answers.
Available lazy skills:
/skill:tmdb-bridge-contract: use when bridge draft shape, TMDB ID/node policy, or verifier repair is unclear.
${ACTION_AGENT_OUTPUT_CONTRACT}
Try to finish before ${caseInput.runtime_policy?.suggested_finish_before_seconds ?? 0} seconds.
`.trim();

const goalObjective = `
Produce verifier-accepted BGM-to-TMDB recipe params for this accepted Local-to-Bangumi compiled plan, using tmdb_absent_group for BGM nodes missing from TMDB when needed, or fail closed for global identity ambiguity. This is dry-run only.

${ACTION_AGENT_OUTPUT_CONTRACT}
`.trim();

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
    const autoSubmit = finalWait.auto_submit || (await autoSubmitLatestAcceptedParams());
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
      skills_loaded: REQUIRED_SKILL_NAMES,
      custom_tools_enabled: customToolNames,
      action_system_prompt_appended: true,
      auto_submit: autoSubmit,
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
