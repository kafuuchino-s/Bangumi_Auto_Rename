import { defineTool } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";

export const LOCAL_BANGUMI_TOOLS_ENV = {
  server: "BAR_PI_CASE_AGENT_TOOL_SERVER",
  token: "BAR_PI_CASE_AGENT_TOOL_TOKEN",
};

function strictObject(properties) {
  return Type.Object(properties, { additionalProperties: false });
}

async function callPythonTool(tool, toolArgs) {
  const server = process.env[LOCAL_BANGUMI_TOOLS_ENV.server] || "";
  const token = process.env[LOCAL_BANGUMI_TOOLS_ENV.token] || "";
  if (!server || !token) {
    return {
      ok: false,
      error: "local_bangumi_tool_bridge_not_configured",
      required_env: Object.values(LOCAL_BANGUMI_TOOLS_ENV),
    };
  }
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

function shouldTerminateAfterTool(name, result) {
  if (!result || typeof result !== "object") return false;
  if (name === "fail_closed") {
    return result.ok === true || Boolean(result.final_result);
  }
  if (
    name === "submit_organize_recipe_params" ||
    name === "submit_organize_recipe_params_patch"
  ) {
    return result.accepted === true || result.status === "accepted" || Boolean(result.final_result);
  }
  return false;
}

const ENVELOPE_TOOL_NAMES = new Set([
  "find_bangumi_targets_for_local_file",
  "validate_recipe_params_draft",
  "validate_organize_recipe_params",
  "validate_organize_recipe_params_patch",
  "submit_organize_recipe_params",
  "submit_organize_recipe_params_patch",
  "fail_closed",
]);

function arrayLength(value) {
  return Array.isArray(value) ? value.length : 0;
}

function compactRows(value, limit = 8) {
  return Array.isArray(value) ? value.slice(0, limit) : [];
}

function compactLocalFactSummary(result) {
  const localFile = result?.local_file && typeof result.local_file === "object" ? result.local_file : {};
  const factSummary = localFile.fact_summary && typeof localFile.fact_summary === "object" ? localFile.fact_summary : {};
  const pathFacts = localFile.path_facts && typeof localFile.path_facts === "object" ? localFile.path_facts : {};
  return {
    basename: localFile.basename || "",
    parent_display: localFile.parent_display || "",
    duration_seconds: factSummary.duration_seconds ?? null,
    probe_status: factSummary.probe_status || "",
    locator_markers: Array.isArray(pathFacts.raw_marker_tokens) ? pathFacts.raw_marker_tokens.slice(0, 8) : [],
  };
}

function compactSubjectRow(value) {
  const subject = value && typeof value === "object" ? value : {};
  return {
    subject_id: subject.subject_id ?? null,
    title: subject.title || "",
    name: subject.name || "",
    name_cn: subject.name_cn || "",
    date: subject.date || "",
    platform: subject.platform || "",
    eps: subject.eps ?? null,
    total_episodes: subject.total_episodes ?? null,
  };
}

function compactEpisodeRow(value) {
  const episode = value && typeof value === "object" ? value : {};
  return {
    subject_id: episode.subject_id ?? null,
    episode_id: episode.episode_id ?? null,
    episode_type: episode.episode_type || episode.item_kind || "",
    api_type: episode.api_type || episode.type || "",
    sort: episode.sort ?? null,
    ep: episode.ep ?? null,
    title: episode.title || "",
    name: episode.name || "",
    name_cn: episode.name_cn || "",
    duration: episode.duration || "",
  };
}

function compactSubjectEpisodeGroups(value, limit = 4, episodeLimit = 4) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, limit).map((group) => ({
    subject: compactSubjectRow(group?.subject),
    episode_count_available: group?.episode_count_available ?? null,
    episode_count_returned: group?.episode_count_returned ?? null,
    episode_rows_limited: Boolean(group?.episode_rows_limited),
    episodes: compactRows(group?.episodes, episodeLimit).map(compactEpisodeRow),
    episode_list_error: group?.episode_list_error || "",
  }));
}

function targetLookupEnvelope(name, result) {
  const durationRows = compactRows(result?.duration_candidate_episode_rows, 12);
  return {
    tool: name,
    ok: Boolean(result?.ok),
    source_path: result?.source_path || "",
    source_path_canonicalized_from: result?.source_path_canonicalized_from || "",
    error: result?.error || "",
    local_file: compactLocalFactSummary(result),
    title_query: result?.title_query || "",
    queries_used: compactRows(result?.queries_used, 4),
    duration_candidate_episode_row_count: arrayLength(result?.duration_candidate_episode_rows),
    duration_candidate_episode_rows: durationRows,
    duration_candidate_policy: result?.duration_candidate_policy || "",
    subject_episode_groups: compactSubjectEpisodeGroups(result?.subject_episode_groups, 4, 4),
    repair_hints: Array.isArray(result?.repair_hints) ? result.repair_hints.slice(0, 6) : [],
    next_tool: durationRows.length > 0
      ? "use duration_candidate_episode_rows to judge and patch/validate the named source or group"
      : "if no supportable target row exists, validate the supplemental rule again or fail_closed with evidence",
  };
}

function modelVisibleEnvelope(name, result) {
  if (name === "find_bangumi_targets_for_local_file") {
    return targetLookupEnvelope(name, result);
  }
  const verifierIssues = Array.isArray(result?.verifier_result?.issues) ? result.verifier_result.issues : [];
  const issues = Array.isArray(result?.issues) ? result.issues : verifierIssues;
  const reviewWarnings = Array.isArray(result?.review_warnings) ? result.review_warnings : [];
  const issueRepairContexts = Array.isArray(result?.issue_repair_contexts) ? result.issue_repair_contexts : [];
  const nextAction = result?.case_board_next_action && typeof result.case_board_next_action === "object"
    ? result.case_board_next_action.next_tool
    : "";
  const draftNext = result?.recipe_params_draft_next_action && typeof result.recipe_params_draft_next_action === "object"
    ? result.recipe_params_draft_next_action.next_tool
    : "";
  return {
    tool: name,
    ok: Boolean(result?.ok),
    status: result?.status || (result?.accepted === true ? "accepted" : result?.ok === false ? "error" : ""),
    accepted: Boolean(result?.accepted),
    summary: result?.summary || "",
    error: result?.error || "",
    issue_count: arrayLength(issues),
    issues: issues.slice(0, 6),
    review_warning_count: arrayLength(reviewWarnings),
    review_warnings: reviewWarnings.slice(0, 6),
    issue_repair_context_count: arrayLength(issueRepairContexts),
    issue_repair_contexts: issueRepairContexts.slice(0, 4),
    repair_hints: Array.isArray(result?.repair_hints) ? result.repair_hints.slice(0, 8) : [],
    patch_repair_feedback: result?.patch_repair_feedback && typeof result.patch_repair_feedback === "object" ? result.patch_repair_feedback : {},
    accounting: result?.accounting && typeof result.accounting === "object" ? result.accounting : {},
    next_tool: result?.next_tool || nextAction || draftNext || "",
    final_result_present: Boolean(result?.final_result),
  };
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
      const modelResult = ENVELOPE_TOOL_NAMES.has(name) ? modelVisibleEnvelope(name, result) : result;
      const text = JSON.stringify(modelResult, null, 2);
      return {
        content: [{ type: "text", text: text.length > 60000 ? `${text.slice(0, 60000)}\n...truncated...` : text }],
        details: result,
        terminate: shouldTerminateAfterTool(name, result),
      };
    },
  });
}

const recipeParamsQuickReference = [
  "Default output is compact; pass detail:true only for debugging full repair_hints, compiled_plan, or organize_recipe.",
  "Use group_ref for normal sequences; for numbered subclusters prefer group_ref + file_numbers/file_number_range/path_contains before long exact_paths.",
  "Use exact_paths only for unnumbered, path-ambiguous, or truly mixed exceptions.",
  "Use disposition:\"non_bangumi_or_supplemental\" for scoped supplemental rows.",
  "Accepted validation still needs submit; put board_delta/content, validation_snapshot, patch_delta, and submit_snapshot in their strict small envelope schemas.",
  "Schema is strict: do not use source_path/path/source_paths/source_template/range/offset aliases, nested select/target/episode objects, plural subject fields, boolean flags, patch_delta structural fields, or raw recipe JSON.",
].join("\n");

const stringOrStringArraySchema = Type.Union([Type.String(), Type.Array(Type.String())]);
const boardMemoryEnvelopeSchema = strictObject({
  summary: Type.Optional(Type.String()),
  observations: Type.Optional(Type.Array(Type.String())),
  blockers: Type.Optional(Type.Array(Type.String())),
  next_action: Type.Optional(Type.String()),
});
const validationSnapshotSchema = strictObject({
  summary: Type.Optional(Type.String()),
  accepted_scope: Type.Optional(Type.Array(Type.String())),
  open_issues: Type.Optional(Type.Array(Type.String())),
  next_action: Type.Optional(Type.String()),
});
const patchDeltaSchema = strictObject({
  summary: Type.Optional(Type.String()),
  changed_rules: Type.Optional(Type.Array(Type.String())),
  evidence_refs: Type.Optional(Type.Array(Type.String())),
  reason: Type.Optional(Type.String()),
});
const submitSnapshotSchema = strictObject({
  summary: Type.Optional(Type.String()),
  accepted_rule_count: Type.Optional(Type.Number()),
  review_notes: Type.Optional(Type.Array(Type.String())),
});
const sourceUnitSchema = StringEnum(["single_file", "single_file_multi_episode"]);
const mediaKindSchema = StringEnum(["tv", "movie", "ova", "oad", "sp", "special", "unknown"]);
const episodeTypeSchema = StringEnum(["main", "regular", "special", "ova", "oad", "movie", "unknown"]);
const dispositionSchema = StringEnum([
  "map_to_bangumi",
  "non_bangumi_or_supplemental",
]);
const episodeNumberFieldSchema = StringEnum(["sort", "ep"]);

const recipeParamsRuleSchema = strictObject({
  name: Type.Optional(Type.String()),
  group_ref: Type.Optional(Type.String()),
  file_numbers: Type.Optional(Type.Array(Type.Number())),
  file_number_range: Type.Optional(Type.String()),
  path_contains: Type.Optional(stringOrStringArraySchema),
  exclude_path_contains: Type.Optional(stringOrStringArraySchema),
  exact_paths: Type.Optional(Type.Array(Type.String())),
  source_pattern: Type.Optional(Type.String()),
  filename_regex: Type.Optional(Type.String()),
  exclude_regex: Type.Optional(Type.String()),
  source_unit: Type.Optional(sourceUnitSchema),
  subject_id: Type.Optional(Type.Number()),
  media_kind: Type.Optional(mediaKindSchema),
  episode_id: Type.Optional(Type.Number()),
  episode_ids: Type.Optional(Type.Array(Type.Number())),
  episode_type: Type.Optional(episodeTypeSchema),
  sort: Type.Optional(Type.Number()),
  ep: Type.Optional(Type.Number()),
  episode_range: Type.Optional(Type.String()),
  episode_range_start: Type.Optional(Type.Number()),
  episode_range_end: Type.Optional(Type.Number()),
  episode_offset: Type.Optional(Type.String()),
  episode_number_field: Type.Optional(episodeNumberFieldSchema),
  disposition: Type.Optional(dispositionSchema),
  reason: Type.Optional(Type.String()),
});
const recipeGroupDecisionSchema = recipeParamsRuleSchema;
const recipeParamsPayloadSchema = strictObject({
  version: Type.Optional(Type.Number()),
  summary: Type.Optional(Type.String()),
  rules: Type.Array(recipeParamsRuleSchema),
});
const recipeParamsRulePatchSchema = strictObject({
  name: Type.String(),
  updates: Type.Optional(Type.Partial(recipeParamsRuleSchema, { additionalProperties: false })),
  unset: Type.Optional(Type.Array(Type.String())),
});
const recipeParamsPatchSchema = strictObject({
  patch_rules: Type.Optional(Type.Array(recipeParamsRulePatchSchema)),
  replace_rules: Type.Optional(Type.Array(recipeParamsRuleSchema)),
  append_rules: Type.Optional(Type.Array(recipeParamsRuleSchema)),
  remove_rule_names: Type.Optional(Type.Array(Type.String())),
});

const tools = [
  proxyTool(
    "get_case_overview",
    "Get Case Overview",
    "Return the case map: counts, compact local group index, seen Bangumi evidence counts, recipe state, and navigation handles. It does not recommend a route.",
    strictObject({}),
  ),
  proxyTool(
    "list_local_groups",
    "List Local Groups",
    "Return the local group index. With detail=true, returns expanded local group facts. Pi chooses which group to inspect.",
    strictObject({ detail: Type.Optional(Type.Boolean()) }),
  ),
  proxyTool(
    "get_local_group_detail",
    "Get Local Group Detail",
    "Expand one local group by group_ref with source paths and optional detailed local file facts. It does not choose Bangumi targets or disposition.",
    strictObject({
      group_ref: Type.String(),
      detail: Type.Optional(Type.Boolean()),
    }),
  ),
  proxyTool(
    "get_local_selector_scaffold",
    "Get Local Selector Scaffold",
    "Return selector/range params stubs for one local group_ref, or all groups when group_ref is omitted. Pi can use group_ref as a local selector shorthand, and fills target or supplemental fields from evidence.",
    strictObject({
      group_ref: Type.Optional(Type.String()),
      detail: Type.Optional(Type.Boolean()),
    }),
  ),
  proxyTool(
    "get_case_context",
    "Get Case Context",
    "Read bounded Local to Bangumi case context. detail=false returns navigation context; detail=true expands the legacy full debug context.",
    strictObject({ detail: Type.Optional(Type.Boolean()) }),
  ),
  proxyTool(
    "get_local_recipe_params_scaffold",
    "Get Local Recipe Params Scaffold",
    "Return local selector/range params stubs copied from local facts only. It does not choose Bangumi targets, media kind, episode type, disposition, or supplemental status.",
    strictObject({ detail: Type.Optional(Type.Boolean()), group_ref: Type.Optional(Type.String()) }),
  ),
  proxyTool(
    "get_recipe_state",
    "Get Recipe State",
    "Return latest params, verifier, submit, and final-result state without changing the case.",
    strictObject({ detail: Type.Optional(Type.Boolean()) }),
  ),
  proxyTool(
    "append_case_board_note",
    "Append Case Board Note",
    "Append one compact strict-envelope section to scratch_paths.notes as Pi-owned working memory. Use directly for Initial Board and ordinary Board Delta. Do not paste local group/evidence JSON; cite group refs, facts, and blockers. Put Validation Snapshot, Patch Delta, and Submit Snapshot into the params transaction tools instead. This is append-only audit I/O; it does not choose targets or recipe state.",
    strictObject({
      section_type: Type.String(),
      content: boardMemoryEnvelopeSchema,
      next_action: Type.Optional(Type.String()),
    }),
  ),
  proxyTool(
    "get_case_board_notes",
    "Get Case Board Notes",
    "Read scratch_paths.notes tail/latest content for context recovery. This is read-only audit I/O; latest Validation Snapshot or Submit Snapshot is the current board, older deltas are history.",
    strictObject({
      mode: Type.Optional(Type.String()),
      max_chars: Type.Optional(Type.Number()),
    }),
  ),
  proxyTool(
    "select_bangumi_anchor_subject",
    "Select Bangumi Anchor Subject",
    "Record the Pi-selected reliable main anime/video anchor and atomically build the full Bangumi relation atlas. Evidence bootstrap only: Python does not choose the anchor, targets, recipe rows, or supplemental status.",
    strictObject({
      anchor_subject_id: Type.Number(),
      reason: Type.Optional(Type.String()),
      board_delta: Type.Optional(boardMemoryEnvelopeSchema),
      max_subjects: Type.Optional(Type.Number()),
      hydrate_episode_surfaces: Type.Optional(Type.Boolean()),
      max_relation_fetches: Type.Optional(Type.Number()),
      emergency_depth: Type.Optional(Type.Number()),
      max_episode_cards_per_subject: Type.Optional(Type.Number()),
      detail: Type.Optional(Type.Boolean()),
    }),
  ),
  proxyTool(
    "build_bangumi_relation_atlas",
    "Build Bangumi Relation Atlas",
    "Debug/manual fallback: from one Pi-chosen reliable anime/video anchor, fully traverse reachable Bangumi anime/video related subjects and write a relation atlas. Normal complex cases should use select_bangumi_anchor_subject so anchor recording and atlas build are one transaction.",
    strictObject({
      anchor_subject_id: Type.Number(),
      max_subjects: Type.Optional(Type.Number()),
      hydrate_episode_surfaces: Type.Optional(Type.Boolean()),
      max_relation_fetches: Type.Optional(Type.Number()),
      emergency_depth: Type.Optional(Type.Number()),
      max_episode_cards_per_subject: Type.Optional(Type.Number()),
    }),
  ),
  proxyTool(
    "upsert_recipe_params_draft",
    "Upsert Recipe Params Draft",
    "Save or replace Pi-owned partial recipe_params rules before full validation. Default result is compact coverage; detail=true returns full draft debug data.",
    strictObject({
      rules: Type.Optional(Type.Union([recipeParamsRuleSchema, Type.Array(recipeParamsRuleSchema)])),
      remove_rule_names: Type.Optional(Type.Array(Type.String())),
      board_delta: Type.Optional(boardMemoryEnvelopeSchema),
      summary: Type.Optional(Type.String()),
      detail: Type.Optional(Type.Boolean()),
    }),
    {
      promptSnippet: "Save canonical recipe params draft rows before full validation",
      promptGuidelines: [
        "Use upsert_recipe_params_draft when Pi already has complete canonical params rows but is not ready to submit.",
        "Use upsert_recipe_params_draft board_delta as a strict small envelope, not arbitrary JSON or prose.",
      ],
    },
  ),
  proxyTool(
    "upsert_recipe_group_decision_one",
    "Upsert One Recipe Group Decision",
    "Preferred action-style path: save exactly one compact Pi-owned group/subcluster decision. The decision parameter is schema-shaped: use subject_id, episode_id/episode_ids, media_kind, episode_type, group_ref, file_numbers/file_number_range/path_contains/exact_paths, and a short reason. Use episode_ids only for one-to-one exact-path expansion; do not combine it with episode_id/sort/ep. Do not invent plural target fields such as target_subject_ids; split multi-movie or mixed target surfaces into separate one-row decisions. Python compiles the saved decision into recipe_params_draft but does not choose Bangumi targets or supplemental status.",
    strictObject({
      decision: recipeGroupDecisionSchema,
      board_delta: Type.Optional(boardMemoryEnvelopeSchema),
      summary: Type.Optional(Type.String()),
      detail: Type.Optional(Type.Boolean()),
    }),
    {
      promptSnippet: "Save one schema-shaped Local-to-Bangumi group decision",
      promptGuidelines: [
        "Use upsert_recipe_group_decision_one when one group or subcluster target-surface judgment is stable enough to persist.",
        "Use upsert_recipe_group_decision_one with one canonical target surface per decision row; split mixed movie, special, or supplemental surfaces.",
      ],
    },
  ),
  proxyTool(
    "upsert_recipe_group_decision",
    "Upsert Recipe Group Decision",
    "Save a batch of Pi-owned group/subcluster decisions using the same schema-shaped rows as upsert_recipe_group_decision_one. Valid canonical rows are saved; invalid rows are reported by decision_index/decision_name and are not migrated. Prefer upsert_recipe_group_decision_one for incremental work. Do not invent plural target fields such as target_subject_ids; split multi-movie or mixed target surfaces into separate decisions. Default result is compact counts/readiness; detail=true returns full saved decision/debug draft data. Python does not choose Bangumi targets or supplemental status.",
    strictObject({
      decisions: Type.Optional(Type.Array(recipeGroupDecisionSchema)),
      remove_decision_names: Type.Optional(Type.Array(Type.String())),
      board_delta: Type.Optional(boardMemoryEnvelopeSchema),
      summary: Type.Optional(Type.String()),
      detail: Type.Optional(Type.Boolean()),
    }),
    {
      promptSnippet: "Save a canonical batch of Local-to-Bangumi group decisions",
      promptGuidelines: [
        "Use upsert_recipe_group_decision only for canonical decision batches whose rows are already independently stable.",
        "Use upsert_recipe_group_decision_one for incremental work when only one row is stable.",
      ],
    },
  ),
  proxyTool(
    "get_recipe_group_decisions",
    "Get Recipe Group Decisions",
    "Read saved group/subcluster decisions plus the compiled recipe_params_draft coverage preview.",
    strictObject({
      detail: Type.Optional(Type.Boolean()),
    }),
  ),
  proxyTool(
    "clear_recipe_group_decisions",
    "Clear Recipe Group Decisions",
    "Clear saved group decisions and the generated recipe_params_draft. This does not change validated params or final results.",
    strictObject({
      reason: Type.Optional(Type.String()),
    }),
  ),
  proxyTool(
    "get_recipe_params_draft",
    "Get Recipe Params Draft",
    "Read the current Pi-owned partial recipe_params draft plus non-semantic local group coverage preview.",
    strictObject({
      detail: Type.Optional(Type.Boolean()),
    }),
  ),
  proxyTool(
    "clear_recipe_params_draft",
    "Clear Recipe Params Draft",
    "Clear the Pi-owned partial recipe_params draft. This does not change validated params or final results.",
    strictObject({
      reason: Type.Optional(Type.String()),
    }),
  ),
  proxyTool(
    "validate_recipe_params_draft",
    "Validate Recipe Params Draft",
    "Run the full params verifier on the current recipe_params_draft only after local coverage is complete. Default result is compact; detail=true is for debug output.",
    strictObject({
      validation_snapshot: Type.Optional(validationSnapshotSchema),
      detail: Type.Optional(Type.Boolean()),
    }),
    {
      promptSnippet: "Validate the saved params draft through the strict verifier",
      promptGuidelines: [
        "Use validate_recipe_params_draft only after saved draft rules cover every visible local group.",
        "Use validate_recipe_params_draft validation_snapshot as a strict small envelope summarizing accepted scope and open issues.",
      ],
    },
  ),
  proxyTool(
    "search_bangumi_subjects",
    "Search Bangumi Subjects",
    "Search Bangumi subjects from clean title terms and add returned subject cards to the case workspace.",
    strictObject({
      query: Type.String(),
      max_subjects: Type.Optional(Type.Number()),
    }),
  ),
  proxyTool(
    "lookup_bangumi_subject",
    "Lookup Bangumi Subject",
    "Fetch details for Bangumi subject IDs.",
    strictObject({ subject_ids: Type.Array(Type.Number()) }),
  ),
  proxyTool(
    "expand_related_subjects",
    "Expand Related Subjects",
    "Fetch related Bangumi subjects for a Bangumi subject ID.",
    strictObject({
      subject_id: Type.Number(),
      relation_kinds: Type.Optional(Type.Array(Type.String())),
      subject_types: Type.Optional(Type.Array(Type.String())),
      max_subjects: Type.Optional(Type.Number()),
    }),
  ),
  proxyTool(
    "expand_related_graph",
    "Expand Related Graph",
    "Fetch a compact related-subject graph from one or more Bangumi subject anchors.",
    strictObject({
      subject_id: Type.Optional(Type.Number()),
      subject_ids: Type.Optional(Type.Array(Type.Number())),
      relation_kinds: Type.Optional(Type.Array(Type.String())),
      subject_types: Type.Optional(Type.Array(Type.String())),
      max_depth: Type.Optional(Type.Number()),
      max_subjects: Type.Optional(Type.Number()),
    }),
  ),
  proxyTool(
    "get_episode_list",
    "Get Episode List",
    "Fetch or expose episode cards for a Bangumi subject ID. For mixed SP/OVA/OAD/movie-like side content or duplicate repair, use episode_scope=\"all\" and enough max_episode_cards to include non-regular rows before deciding no distinct side/special surface exists.",
    strictObject({
      subject_id: Type.Number(),
      episode_scope: Type.Optional(Type.String()),
      max_episode_cards: Type.Optional(Type.Number()),
    }),
  ),
  proxyTool(
    "get_target_detail",
    "Get Target Detail",
    "Expose target episode details by episode IDs, or by subject ID plus sort.",
    strictObject({
      episode_ids: Type.Optional(Type.Array(Type.Number())),
      subject_id: Type.Optional(Type.Number()),
      sort: Type.Optional(Type.Number()),
    }),
  ),
  proxyTool(
    "get_local_file_detail",
    "Get Local File Detail",
    "Expose local file detail by real source paths.",
    strictObject({ paths: Type.Array(Type.String()) }),
  ),
  proxyTool(
    "find_bangumi_targets_for_local_file",
    "Find Bangumi Targets For Local File",
    "Fact helper: search Bangumi and return compact subject/episode rows plus duration_candidate_episode_rows for one visible source_path. Use it for exact side files or mixed-group subclusters that need row-surface evidence. It does not recommend targets or recipes.",
    strictObject({
      source_path: Type.String(),
      title_query: Type.Optional(Type.String()),
      kind_hint: Type.Optional(Type.String()),
      max_subjects: Type.Optional(Type.Number()),
      max_episode_cards: Type.Optional(Type.Number()),
    }),
    {
      promptSnippet: "Expose compact Bangumi row candidates for one exact local source_path",
      promptGuidelines: [
        "Use duration_candidate_episode_rows as facts for Pi judgment; the tool does not choose the target.",
        "If rows are supportable, patch or validate the affected rule; if not, record the concrete contradiction or fail_closed.",
        "Do not continue broad evidence after this helper answers the named source_path.",
      ],
    },
  ),
  proxyTool(
    "get_target_window",
    "Get Target Window",
    "Expose a target episode window by Bangumi subject ID and sort range.",
    strictObject({
      subject_id: Type.Number(),
      sort_start: Type.Optional(Type.Number()),
      sort_end: Type.Optional(Type.Number()),
    }),
  ),
  proxyTool(
    "validate_organize_recipe_params",
    "Validate Organize Recipe Params",
    `Trial-check semantic rule parameters: build an OrganizeRecipeDraft, compile it, and return verifier issues or review warnings without finishing the case. Put the current Validation Snapshot in validation_snapshot so board write and validation happen in one transaction. Accepted validation still requires submit_organize_recipe_params.\n${recipeParamsQuickReference}`,
    strictObject({
      recipe_params: recipeParamsPayloadSchema,
      validation_snapshot: Type.Optional(validationSnapshotSchema),
      detail: Type.Optional(Type.Boolean()),
    }),
    {
      promptSnippet: "Trial-check canonical recipe params without finalizing",
      promptGuidelines: [
        "Use validate_organize_recipe_params to test canonical recipe params and read verifier or review feedback before final submit.",
        "Use validate_organize_recipe_params validation_snapshot as a strict small envelope; accepted validation still needs submit_organize_recipe_params.",
      ],
    },
  ),
  proxyTool(
    "validate_organize_recipe_params_patch",
    "Validate Organize Recipe Params Patch",
    "Patch the latest recipe params from the previous params validate/submit, then validate. If no previous params validation exists but recipe_params_draft does, the patch updates that draft and returns coverage preview without running verifier. append_rules is only for new named rules; use patch_rules/replace_rules for existing names or remove_rule_names before appending a replacement. Put the small Patch Delta in patch_delta so board write and patch/draft update happen in one transaction.",
    strictObject({
      recipe_params_patch: recipeParamsPatchSchema,
      patch_delta: Type.Optional(patchDeltaSchema),
      detail: Type.Optional(Type.Boolean()),
    }),
    {
      promptSnippet: "Validate a strict recipe params patch against latest params",
      promptGuidelines: [
        "Use validate_organize_recipe_params_patch for named repairs after validation or submission feedback.",
        "Use validate_organize_recipe_params_patch patch_delta only for a strict small evidence note; put structural edits in recipe_params_patch.",
      ],
    },
  ),
  proxyTool(
    "submit_organize_recipe_params",
    "Submit Organize Recipe Params",
    `Build the final OrganizeRecipeDraft from semantic rule parameters, then submit it through the strict Python verifier gate. After an accepted validate_recipe_params_draft/validate_organize_recipe_params result, omit recipe_params to reuse the accepted canonical payload. Put the Submit Snapshot in submit_snapshot so board write and submit happen in one transaction.\n${recipeParamsQuickReference}`,
    strictObject({
      recipe_params: Type.Optional(recipeParamsPayloadSchema),
      summary: Type.Optional(Type.String()),
      submit_snapshot: Type.Optional(submitSnapshotSchema),
      detail: Type.Optional(Type.Boolean()),
    }),
    {
      promptSnippet: "Submit accepted canonical recipe params as final structured output",
      promptGuidelines: [
        "Use submit_organize_recipe_params only after params validation is accepted and review warnings are resolved.",
        "After submit_organize_recipe_params returns accepted=true, do not call any tool except goal_complete.",
      ],
    },
  ),
  proxyTool(
    "submit_organize_recipe_params_patch",
    "Submit Organize Recipe Params Patch",
    "Patch the latest recipe params from the previous params validate/submit, then submit. append_rules is only for new names; patch or replace existing names instead. If the same patch was just accepted by validate_organize_recipe_params_patch, submit reuses that accepted merged params instead of applying append_rules twice. Use submit_snapshot for the final board snapshot; patch_delta is optional when submitting a newly changed patch.",
    strictObject({
      recipe_params_patch: recipeParamsPatchSchema,
      summary: Type.Optional(Type.String()),
      patch_delta: Type.Optional(patchDeltaSchema),
      submit_snapshot: Type.Optional(submitSnapshotSchema),
      detail: Type.Optional(Type.Boolean()),
    }),
    {
      promptSnippet: "Submit an accepted strict recipe params patch as final structured output",
      promptGuidelines: [
        "Use submit_organize_recipe_params_patch after an accepted patch validation, or when submitting the same accepted merged patch payload.",
        "After submit_organize_recipe_params_patch returns accepted=true, do not call any tool except goal_complete.",
      ],
    },
  ),
  proxyTool(
    "fail_closed",
    "Fail Closed",
    "Finish safely when the case cannot be mapped under strict evidence and verifier rules.",
    strictObject({
      reason: Type.String(),
      reason_kind: Type.Optional(Type.String()),
      related_refs: Type.Optional(Type.Array(Type.String())),
    }),
    {
      promptSnippet: "Finish safely with a concrete strict-evidence blocker",
      promptGuidelines: [
        "Use fail_closed only when strict evidence is insufficient or contradictory after targeted evidence and verifier feedback have been exhausted.",
        "After fail_closed succeeds, call goal_complete and do not continue evidence search.",
      ],
    },
  ),
];

export const LOCAL_BANGUMI_TOOL_NAMES = tools.map((tool) => tool.name);

export default function localBangumiTools(pi) {
  for (const tool of tools) {
    pi.registerTool(tool);
  }
}
