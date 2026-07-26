export function parseArgs(argv) {
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

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function effectiveRuntimeBudgetSeconds(runtimePolicy) {
  const timeoutSeconds = Number(runtimePolicy?.wall_clock_timeout_seconds || 0);
  const finishBeforeSeconds = Number(runtimePolicy?.suggested_finish_before_seconds || 0);
  if (timeoutSeconds > 5) {
    return Math.max(1, Math.min(timeoutSeconds - 2, Math.max(finishBeforeSeconds, timeoutSeconds - 5)));
  }
  return finishBeforeSeconds || Math.max(1, timeoutSeconds || 30);
}

export function safePreview(value, limit = 2000) {
  let text;
  try {
    text = typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    text = String(value);
  }
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit)}...truncated...` : text;
}

export function extractMessageText(message) {
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

export function discoverRequiredSkills(resourceLoader, requiredSkillNames) {
  const loadedSkills = resourceLoader.getSkills().skills || [];
  const discovered = [];
  const missing = [];
  for (const skillName of requiredSkillNames) {
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

export function stripMarkdownFrontmatter(text) {
  if (!text.startsWith("---")) return text;
  const normalized = text.replace(/\r\n/g, "\n");
  const end = normalized.indexOf("\n---", 3);
  if (end === -1) return text;
  const after = normalized.indexOf("\n", end + 4);
  return after === -1 ? "" : normalized.slice(after + 1);
}

export async function promptWithResult(session, text, options = {}) {
  try {
    await session.prompt(text, {
      expandPromptTemplates: true,
      source: "api",
      ...options,
    });
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error?.stack || error?.message || String(error) };
  }
}
