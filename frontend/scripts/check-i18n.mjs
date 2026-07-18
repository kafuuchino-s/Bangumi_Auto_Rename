import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const modulePath = new URL("..", import.meta.url).pathname.replace(/^\/(\w:)/, "$1");
const root = path.resolve(modulePath);
const resourceDir = path.join(root, "src", "i18n", "resources");
const files = fs.readdirSync(resourceDir).filter((name) => name.endsWith(".ts") && name !== "index.ts");
const placeholderPattern = /\{\{\s*([\w.-]+)\s*\}\}/g;

function loadResource(file) {
  const source = fs.readFileSync(path.join(resourceDir, file), "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: file,
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(output, { module, exports: module.exports });
  return { source, exports: module.exports };
}

function flatten(value, prefix = "", output = new Map()) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) {
      flatten(child, prefix ? `${prefix}.${key}` : key, output);
    }
  } else {
    output.set(prefix, String(value ?? ""));
  }
  return output;
}

function placeholders(value) {
  return [...value.matchAll(placeholderPattern)].map((match) => match[1]).sort();
}

for (const file of files) {
  const base = path.basename(file, ".ts");
  const { exports } = loadResource(file);
  const zh = exports[base];
  const en = exports[`${base}En`];
  if (!zh || !en) throw new Error(`${file}: expected ${base} and ${base}En exports`);

  const zhMap = flatten(zh);
  const enMap = flatten(en);
  const zhKeys = [...zhMap.keys()].sort();
  const enKeys = [...enMap.keys()].sort();
  if (JSON.stringify(zhKeys) !== JSON.stringify(enKeys)) {
    const missingInEn = zhKeys.filter((key) => !enMap.has(key));
    const missingInZh = enKeys.filter((key) => !zhMap.has(key));
    throw new Error(`${file}: key mismatch; missing in en-US=${missingInEn.join(",")}; missing in zh-CN=${missingInZh.join(",")}`);
  }
  for (const key of zhKeys) {
    const zhPlaceholders = placeholders(zhMap.get(key));
    const enPlaceholders = placeholders(enMap.get(key));
    if (JSON.stringify(zhPlaceholders) !== JSON.stringify(enPlaceholders)) {
      throw new Error(`${file}:${key}: interpolation mismatch (${zhPlaceholders.join(",")} vs ${enPlaceholders.join(",")})`);
    }
  }
}

const contract = fs.readFileSync(path.resolve(root, "..", "src", "api", "contract.py"), "utf8");
const web = fs.readFileSync(path.resolve(root, "..", "src", "web.py"), "utf8");
const { exports: errorExports } = loadResource("errors.ts");
const errorKeys = new Set(Object.keys(errorExports.errors));
for (const code of ["path_not_found", "task_not_found", "validation_error", "permission_denied", "task_conflict", "internal_error"]) {
  if (!(contract.includes(`"${code}"`) || web.includes(`"${code}"`)) || !errorKeys.has(code)) {
    throw new Error(`missing error-code translation: ${code}`);
  }
}
console.log(`i18n check passed (${files.length} namespaces, normalized keys and placeholders verified)`);
