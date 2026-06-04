#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";

const [recipePath, caseInputPath] = process.argv.slice(2);
if (!recipePath) {
  console.error("usage: check-organize-recipe.mjs <recipe.json> [case_input.json]");
  process.exit(2);
}

function loadJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/, ""));
}

function normPath(value) {
  return String(value || "").replaceAll("\\", "/").replace(/^\.\//, "");
}

function globToRegex(glob) {
  let text = normPath(glob || "");
  let regex = "";
  for (let index = 0; index < text.length;) {
    if (text.slice(index, index + 3) === "**/") {
      regex += "(?:.*/)?";
      index += 3;
      continue;
    }
    const char = text[index];
    if (char === "*") {
      regex += "[^/]*";
    } else if (char === "?") {
      regex += ".";
    } else {
      regex += char.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
    index += 1;
  }
  return new RegExp(`^${regex}$`, "i");
}

function recipeRegex(pattern) {
  if (!pattern) return null;
  let text = String(pattern).replace(/\(\?P<([A-Za-z_][A-Za-z0-9_]*)>/g, "(?<$1>");
  text = text.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_m, name) => {
    if (name === "ep") return "(?<ep>\\d+)";
    return ".*?";
  });
  return new RegExp(text, "i");
}

function safeRecipeRegex(pattern) {
  try {
    return recipeRegex(pattern);
  } catch {
    return null;
  }
}

function rangeContains(spec, value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return false;
  for (const rawPart of String(spec || "").split(",")) {
    const part = rawPart.trim();
    if (!part) continue;
    if (part.includes("-")) {
      const [left, right] = part.split("-", 2).map((item) => Number(item.trim()));
      if (Number.isFinite(left) && Number.isFinite(right) && left <= number && number <= right) return true;
      continue;
    }
    const exact = Number(part);
    if (Number.isFinite(exact) && exact === number) return true;
  }
  return false;
}

function rangeCount(spec) {
  const values = [];
  for (const rawPart of String(spec || "").split(",")) {
    const part = rawPart.trim();
    if (!part) continue;
    if (part.includes("-")) {
      const [left, right] = part.split("-", 2).map((item) => Number(item.trim()));
      if (!Number.isFinite(left) || !Number.isFinite(right) || right < left) return 0;
      for (let value = left; value <= right; value += 1) values.push(value);
      continue;
    }
    const exact = Number(part);
    if (!Number.isFinite(exact)) return 0;
    values.push(exact);
  }
  return new Set(values).size;
}

function capturedEpisode(filePath, rule, rx) {
  const sourcePath = normPath(filePath);
  const basename = path.posix.basename(sourcePath);
  const match = rx?.exec(basename) || rx?.exec(sourcePath);
  if (!match) return null;
  const captureName = String(rule?.episode?.capture || "ep");
  const raw = match.groups?.[captureName] ?? match[1];
  if (raw === undefined || raw === null || raw === "") return null;
  const number = Number(String(raw).replace(/^0+/, "") || "0");
  return Number.isFinite(number) ? number : null;
}

function matchesRule(filePath, rule) {
  const selector = rule?.select || {};
  const sourcePath = normPath(filePath);
  const basename = path.posix.basename(sourcePath);
  if (selector.exclude_regex) {
    const exclude = safeRecipeRegex(selector.exclude_regex);
    if (exclude?.test(sourcePath) || exclude?.test(basename)) return false;
  }
  const exact = Array.isArray(selector.exact_paths)
    ? selector.exact_paths.map(normPath).filter(Boolean)
    : [];
  if (exact.length) return exact.includes(sourcePath);
  let ok = true;
  if (selector.path_glob) ok = globToRegex(selector.path_glob).test(sourcePath);
  let rx = null;
  if (selector.filename_regex) {
    rx = safeRecipeRegex(selector.filename_regex);
    ok = ok && Boolean(rx?.test(basename) || rx?.test(sourcePath));
  }
  if (
    ok &&
    rule?.disposition === "map_to_bangumi" &&
    rule?.episode?.range &&
    !hasConcreteEpisodeLocator(rule?.target) &&
    rx
  ) {
    const episodeNumber = capturedEpisode(sourcePath, rule, rx);
    ok = episodeNumber === null || rangeContains(rule.episode.range, episodeNumber);
  }
  return Boolean((selector.path_glob || selector.filename_regex) && ok);
}

function hasConcreteEpisodeLocator(target) {
  return Number(target?.episode_id) > 0 || target?.sort !== undefined && target?.sort !== null || target?.ep !== undefined && target?.ep !== null;
}

function hasEpisodePlaceholder(selector) {
  const pattern = String(selector?.filename_regex || "");
  return pattern.includes("{ep}") || pattern.includes("(?P<ep>") || pattern.includes("(?<ep>");
}

const recipe = loadJson(recipePath);
const caseInput = caseInputPath && fs.existsSync(caseInputPath) ? loadJson(caseInputPath) : {};
const issues = [];
const allowedDispositions = new Set([
  "map_to_bangumi",
  "non_bangumi_or_supplemental",
  "needs_more_evidence",
  "unaligned_fail_closed",
]);
const allowedSourceUnits = new Set(["single_file", "single_file_multi_episode"]);
const allowedMediaKinds = new Set(["tv", "movie", "ova", "oad", "sp", "special", "unknown"]);
const allowedEpisodeTypes = new Set(["main", "regular", "special", "ova", "oad", "movie", "unknown"]);
const files = Array.isArray(caseInput?.context?.local_files)
  ? caseInput.context.local_files.map((item) => normPath(item.source_path)).filter(Boolean)
  : [];

if (!recipe || typeof recipe !== "object") issues.push("recipe must be an object");
if (!Array.isArray(recipe.rules)) issues.push("recipe.rules must be an array");

const covered = new Map();
for (const [index, rule] of (Array.isArray(recipe.rules) ? recipe.rules : []).entries()) {
  const label = rule?.name || `rule[${index}]`;
  if (!rule || typeof rule !== "object") {
    issues.push(`${label}: rule must be an object`);
    continue;
  }
  if (!allowedDispositions.has(rule.disposition)) issues.push(`${label}: invalid disposition`);
  if (!allowedSourceUnits.has(rule.source_unit ?? "single_file")) issues.push(`${label}: invalid source_unit`);
  if (!allowedMediaKinds.has(rule.target?.media_kind ?? "unknown")) issues.push(`${label}: invalid media_kind`);
  if (!allowedEpisodeTypes.has(rule.target?.episode_type ?? "unknown")) issues.push(`${label}: invalid episode_type`);
  if (rule.disposition === "map_to_bangumi" && !(Number(rule.target?.bangumi_subject_id) > 0)) {
    issues.push(`${label}: mapped rule needs bangumi_subject_id`);
  }
  try {
    if (rule.select?.filename_regex) recipeRegex(rule.select.filename_regex);
    if (rule.select?.exclude_regex) recipeRegex(rule.select.exclude_regex);
    if (rule.select?.path_glob) globToRegex(rule.select.path_glob);
  } catch (error) {
    issues.push(`${label}: selector regex/glob is invalid: ${error.message}`);
  }
  if (!rule.select?.exact_paths?.length && !rule.select?.path_glob && !rule.select?.filename_regex) {
    issues.push(`${label}: selector needs exact_paths, path_glob, or filename_regex`);
  }
  const exactCount = Array.isArray(rule.select?.exact_paths)
    ? rule.select.exact_paths.map(normPath).filter(Boolean).length
    : 0;
  if (
    rule.disposition === "map_to_bangumi" &&
    exactCount > 1 &&
    rule.source_unit !== "single_file_multi_episode" &&
    !rule.select?.filename_regex &&
    !hasConcreteEpisodeLocator(rule.target)
  ) {
    issues.push(`${label}: multi-file mapped exact_paths need filename_regex with {ep}, or separate exact rules with episode_id/sort/ep`);
  }
  if (rule.source_unit === "single_file_multi_episode") {
    if (exactCount !== 1) issues.push(`${label}: single_file_multi_episode needs exactly one exact_paths entry`);
    if (hasConcreteEpisodeLocator(rule.target)) issues.push(`${label}: single_file_multi_episode must not hard-code episode_id/sort/ep`);
    if (rangeCount(rule.episode?.range) < 2) issues.push(`${label}: single_file_multi_episode needs episode_range covering at least two episodes`);
  }
  const matched = files.filter((file) => matchesRule(file, rule));
  if (files.length && matched.length === 0) issues.push(`${label}: selector matched no files`);
  if (
    rule.disposition === "map_to_bangumi" &&
    matched.length > 1 &&
    hasEpisodePlaceholder(rule.select || {}) &&
    hasConcreteEpisodeLocator(rule.target)
  ) {
    issues.push(`${label}: sequence rule with {ep} must not hard-code episode_id/sort/ep; use episode_id 0 and sort/ep null so each file resolves from the captured episode number`);
  }
  for (const file of matched) covered.set(file, (covered.get(file) || 0) + 1);
  for (const exactPath of rule.select?.exact_paths || []) {
    const normalized = normPath(exactPath);
    if (files.length && !files.includes(normalized)) issues.push(`${label}: exact path not visible: ${normalized}`);
  }
}

for (const file of files) {
  if (!covered.has(file)) issues.push(`${file}: visible file is not covered by any rule`);
}
for (const [file, count] of covered.entries()) {
  if (count > 1) issues.push(`${file}: visible file is covered by ${count} rules`);
}

console.log(JSON.stringify({ ok: issues.length === 0, issue_count: issues.length, issues }, null, 2));
process.exit(issues.length === 0 ? 0 : 1);
