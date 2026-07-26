import assert from "node:assert/strict";
import test from "node:test";

import {
  discoverRequiredSkills,
  effectiveRuntimeBudgetSeconds,
  extractMessageText,
  parseArgs,
  promptWithResult,
  safePreview,
  stripMarkdownFrontmatter,
} from "../../tools/pi_runner_shared.mjs";

test("parseArgs handles valued, bare, positional, and repeated arguments", () => {
  assert.deepEqual(
    parseArgs([
      "node",
      "runner.mjs",
      "ignored",
      "--input",
      "first.json",
      "--verbose",
      "--input",
      "last.json",
    ]),
    { input: "last.json", verbose: "true" },
  );
});

test("effectiveRuntimeBudgetSeconds preserves runner budget boundaries", () => {
  assert.equal(effectiveRuntimeBudgetSeconds({}), 30);
  assert.equal(
    effectiveRuntimeBudgetSeconds({ wall_clock_timeout_seconds: 5 }),
    5,
  );
  assert.equal(
    effectiveRuntimeBudgetSeconds({ wall_clock_timeout_seconds: 6 }),
    1,
  );
  assert.equal(
    effectiveRuntimeBudgetSeconds({
      wall_clock_timeout_seconds: 20,
      suggested_finish_before_seconds: 12,
    }),
    15,
  );
  assert.equal(
    effectiveRuntimeBudgetSeconds({
      wall_clock_timeout_seconds: 20,
      suggested_finish_before_seconds: 19,
    }),
    18,
  );
});

test("preview, message text, and Markdown frontmatter helpers are stable", () => {
  assert.equal(safePreview("abcdef", 3), "abc...truncated...");
  const circular = {};
  circular.self = circular;
  assert.equal(safePreview(circular), "[object Object]");
  assert.equal(
    extractMessageText({
      content: [
        "a",
        { type: "text", text: "b" },
        { content: "c" },
        { type: "image", data: "ignored" },
      ],
    }),
    "abc",
  );
  assert.equal(
    stripMarkdownFrontmatter("---\r\nname: demo\r\n---\r\nBody\r\n"),
    "Body\n",
  );
  assert.equal(stripMarkdownFrontmatter("Body"), "Body");
});

test("skill discovery and prompt result wrappers keep their contracts", async () => {
  const discovery = discoverRequiredSkills(
    {
      getSkills: () => ({
        skills: [
          {
            name: "present",
            description: "desc",
            filePath: "/skills/present/SKILL.md",
            baseDir: "/skills/present",
          },
        ],
      }),
    },
    ["present", "missing"],
  );
  assert.deepEqual(discovery, {
    discovered: [
      {
        name: "present",
        description: "desc",
        filePath: "/skills/present/SKILL.md",
        baseDir: "/skills/present",
      },
    ],
    missing: ["missing"],
  });

  let received;
  const success = await promptWithResult(
    {
      prompt: async (text, options) => {
        received = { text, options };
      },
    },
    "hello",
    { source: "custom", streamingBehavior: "followUp" },
  );
  assert.deepEqual(success, { ok: true });
  assert.deepEqual(received, {
    text: "hello",
    options: {
      expandPromptTemplates: true,
      source: "custom",
      streamingBehavior: "followUp",
    },
  });

  const failure = await promptWithResult(
    { prompt: async () => Promise.reject(new Error("failed")) },
    "hello",
  );
  assert.equal(failure.ok, false);
  assert.match(failure.error, /failed/);
});
