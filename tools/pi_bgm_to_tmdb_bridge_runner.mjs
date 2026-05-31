#!/usr/bin/env node

import {
  AuthStorage,
  createAgentSession,
  DefaultResourceLoader,
  defineTool,
  ModelRegistry,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
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

const NATIVE_TOOL_NAMES = ["read", "grep", "find", "ls"];
const EXTENSION_TOOL_NAMES = ["goal_complete"];
const EXTENSION_PATHS = [
  path.join(repoRoot, "node_modules", "@narumitw", "pi-goal", "src", "goal.ts"),
];
const REQUIRED_SKILL_NAMES = ["tmdb-bridge-contract"];

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

async function readFinalResult() {
  const response = await fetch(`${server}/final`);
  if (!response.ok) return { ok: false, final_result: null };
  return await response.json();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function summarizeEvent(event) {
  const row = { type: event.type };
  if ("toolName" in event && event.toolName) row.tool_name = String(event.toolName);
  if ("toolCallId" in event && event.toolCallId) row.tool_call_id = String(event.toolCallId);
  if (event.type === "tool_execution_start" || event.type === "tool_execution_update") {
    row.status = event.status || "";
  }
  if (event.type === "tool_execution_end") {
    row.status = event.status || "";
  }
  if (event.type === "message_end" && event.message?.role) {
    row.role = event.message.role;
  }
  return row;
}

function captureAssistantDelta(event) {
  if (event.type !== "message_delta") return;
  const delta = event.delta || event.text || "";
  if (!delta || assistantLogCharCount >= MAX_ASSISTANT_LOG_CHARS) return;
  const text = String(delta);
  const remaining = MAX_ASSISTANT_LOG_CHARS - assistantLogCharCount;
  assistantTextBuffer += text.slice(0, remaining);
  assistantLogCharCount += Math.min(text.length, remaining);
}

function extractMessageText(message) {
  if (!message) return "";
  if (typeof message.content === "string") return message.content;
  if (!Array.isArray(message.content)) return "";
  return message.content
    .map((part) => {
      if (typeof part === "string") return part;
      if (part && typeof part.text === "string") return part.text;
      return "";
    })
    .join("");
}

function flushAssistantMessage() {
  const text = assistantTextBuffer.trim();
  if (text) {
    assistantMessages.push({ index: assistantMessages.length + 1, text });
  }
  assistantTextBuffer = "";
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
  "Primary workflow: write compact recipe_params, not one source_path->node mapping per normal episode.",
  "Minimal recipe_params shape: {\"version\":1,\"summary\":\"...\",\"rules\":[{\"name\":\"main_tv\",\"rule_type\":\"episode_sequence\",\"select_bgm\":{\"bangumi_subject_id\":100,\"episode_type\":\"regular\",\"sort_range\":\"1-26\"},\"target_tmdb\":{\"tmdb_ref\":\"tv:45844\",\"season_number\":1,\"episode_range\":\"1-26\",\"number_field\":\"sort\"},\"confidence\":\"High\",\"reason\":\"TMDB title/original/alias/year/season cards match the Bangumi subject.\"},{\"name\":\"extras\",\"rule_type\":\"supplemental_group\",\"select_bgm\":{},\"confidence\":\"Medium\",\"reason\":\"Accepted BGM plan marks these as supplemental.\"}]}",
  "Rule types: episode_sequence, movie, special_sequence, span, supplemental_group.",
  "Target refs are tv:<id> or movie:<id>. Python hydrates the TMDB legal graph and compiles these params into tv:<id>:SxxEyy or movie:<id> nodes.",
  "TMDB titles, original names, aliases, overviews, years, and URL slugs are semantic evidence. They are not target IDs.",
  "Raw bridge_draft node mappings are debug fallback only for exact edge cases.",
].join("\n");

const bridgeDraftQuickReference = [
  "Debug/fallback bridge_draft shape: {\"summary\":\"...\",\"mappings\":[{\"source_path\":\"real source path from bridge_input\",\"disposition\":\"map_to_tmdb\",\"tmdb_legal_node_ids\":[\"tv:45844:S01E01\"],\"confidence\":\"High\",\"reason\":\"...\"}]}",
  "Use raw node mappings only when recipe params cannot express a precise edge case or when debugging generated JSON.",
].join("\n");

const tools = [
  proxyTool(
    "get_bgm_to_tmdb_bridge_context",
    "Get Bridge Context",
    "Read accepted BGM assignments, current TMDB legal graph, and verifier feedback.",
    objectSchema({ detail: Json.Optional(Json.Boolean()) }),
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
  ),
  proxyTool(
    "get_tmdb_legal_graph",
    "Get TMDB Legal Graph",
    "Hydrate TMDB details and legal nodes for refs such as tv:45844 or movie:1234.",
    objectSchema({ tmdb_refs: Json.Array(Json.String()) }),
  ),
  proxyTool(
    "validate_bgm_to_tmdb_bridge_recipe_params",
    "Validate BGM To TMDB Recipe Params",
    `Compile and verify recipe params without finishing. This is the primary workflow.\n${recipeParamsQuickReference}`,
    objectSchema({ recipe_params: Json.Any() }),
  ),
  proxyTool(
    "submit_bgm_to_tmdb_bridge_recipe_params",
    "Submit BGM To TMDB Recipe Params",
    `Submit final recipe params through Python compile+verify. This is the primary workflow.\n${recipeParamsQuickReference}`,
    objectSchema({
      recipe_params: Json.Any(),
      summary: Json.Optional(Json.String()),
    }),
  ),
  proxyTool(
    "validate_bgm_to_tmdb_bridge",
    "Validate BGM To TMDB Bridge",
    `Verify a raw bridge draft without finishing. Debug/fallback only.\n${bridgeDraftQuickReference}`,
    objectSchema({ bridge_draft: Json.Any() }),
  ),
  proxyTool(
    "submit_bgm_to_tmdb_bridge",
    "Submit BGM To TMDB Bridge",
    `Submit a raw bridge draft through the Python verifier. Debug/fallback only.\n${bridgeDraftQuickReference}`,
    objectSchema({
      bridge_draft: Json.Any(),
      summary: Json.Optional(Json.String()),
    }),
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
  ),
];

const customToolNames = tools.map((tool) => tool.name);
const enabledToolNames = [...NATIVE_TOOL_NAMES, ...EXTENSION_TOOL_NAMES, ...customToolNames];

async function waitForFinalResultWithNudge(session, promptDone) {
  const timeoutSeconds = Number(caseInput.runtime_policy?.wall_clock_timeout_seconds || 0);
  const waitMs = Math.max(1000, Math.max(1, timeoutSeconds || 300) * 1000 - 5000);
  const deadline = Date.now() + waitMs;
  let waitIterations = 0;
  let promptSettled = false;
  let promptOutcome = null;
  let nudgeSent = false;
  promptDone.then((outcome) => {
    promptSettled = true;
    promptOutcome = outcome;
  });
  let finalPayload = await readFinalResult();
  while (Date.now() < deadline) {
    waitIterations += 1;
    if (finalPayload.final_result) {
      return { payload: finalPayload, waitIterations, promptOutcome, nudgeSent };
    }
    if (promptSettled && !nudgeSent) {
      nudgeSent = true;
      const nudgeDone = session
        .prompt(
          [
            "No final BGM-to-TMDB bridge result exists yet.",
            "Use tools now: get context if needed, search TMDB candidates, validate_bgm_to_tmdb_bridge_recipe_params, then submit_bgm_to_tmdb_bridge_recipe_params or fail_closed.",
            "Do not hand-write per-source TMDB node mappings for normal episode sequences.",
            "Do not write files, edit code, or print JSON as prose.",
          ].join("\n"),
          { expandPromptTemplates: true, source: "api" },
        )
        .then(() => ({ ok: true }))
        .catch((error) => ({ ok: false, error: error?.stack || error?.message || String(error) }));
      promptSettled = false;
      promptDone = nudgeDone;
      promptDone.then((outcome) => {
        promptSettled = true;
        promptOutcome = outcome;
      });
    }
    await sleep(1000);
    finalPayload = await readFinalResult();
  }
  return { payload: finalPayload, waitIterations, promptOutcome, nudgeSent, wait_timeout_ms: waitMs };
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

const instructionText = `
Complete this BGM-to-TMDB bridge dry-run.
Read the case input JSON at: ${inputPath}
This input already contains an accepted Local-to-Bangumi compiled_plan. Do not rerun Local-to-Bangumi.
Use get_bgm_to_tmdb_bridge_context for the accepted BGM assignments and current TMDB graph.
Use search_tmdb_candidates to find possible TMDB IDs. Recipe validation hydrates declared TMDB refs automatically; call get_tmdb_legal_graph only when you need detailed season cards before drafting.
${recipeParamsQuickReference}
Validate early with validate_bgm_to_tmdb_bridge_recipe_params. After it is accepted, submit the same params with submit_bgm_to_tmdb_bridge_recipe_params and then call goal_complete.
Use raw validate_bgm_to_tmdb_bridge/submit_bgm_to_tmdb_bridge only as debug fallback.
If TMDB evidence is insufficient or contradictory, call fail_closed with concrete related refs, then goal_complete.
Do not use native tools to edit, write, move, copy, link, rename, or inspect old run artifacts for answers.
Available lazy skills:
/skill:tmdb-bridge-contract: use when bridge draft shape, TMDB ID/node policy, or verifier repair is unclear.
/skill:anime-release-reading: use when local release naming semantics remain ambiguous.
Try to finish before ${caseInput.runtime_policy?.suggested_finish_before_seconds ?? 0} seconds.
`.trim();
await fs.writeFile(result.instruction_path, instructionText, "utf8");

const goalObjective = `
Produce verifier-accepted BGM-to-TMDB recipe params for this accepted Local-to-Bangumi compiled plan, or fail closed with concrete evidence gaps. This is dry-run only.
`.trim();

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
    const finalPayload = finalWait.payload;
    Object.assign(result, {
      ok: Boolean(finalPayload.final_result),
      status: finalPayload.final_result?.status || "invalid",
      turn_count: turnCount,
      final_wait: finalWait,
      native_tools_enabled: NATIVE_TOOL_NAMES,
      extension_tools_enabled: EXTENSION_TOOL_NAMES,
      extension_load_errors: extensionLoadErrors.map((item) => ({
        path: item.path,
        error: item.error,
      })),
      custom_tools_enabled: customToolNames,
      skills_loaded: REQUIRED_SKILL_NAMES,
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
