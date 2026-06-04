#!/usr/bin/env node

const input = process.argv.slice(2).join(" ").trim();
const text = input.replace(/\\/g, "/");
const base = text.split("/").pop() || text;
const lower = base.toLowerCase();

const tokens = [];
const add = (name, value = true) => tokens.push({ name, value });

const episodeMatches = [...base.matchAll(/(?:s(\d{1,2})e(\d{1,3})|(?:ep|episode|#)\s*[-_. ]?(\d{1,3})|第\s*(\d{1,3})\s*話|(?<!\d)(\d{1,3})(?!\d))/gi)];
for (const match of episodeMatches.slice(0, 8)) {
  add("episode_candidate", match.slice(1).find(Boolean));
}

for (const [name, pattern] of [
  ["special", /\b(?:sp|special|ova|oad|ona|extra)\b/i],
  ["movie", /\b(?:movie|gekijouban|theatrical)\b/i],
  ["recap", /\b(?:recap|summary|digest)\b/i],
  ["opening_or_ending", /\b(?:ncop|nced|op|ed)\d*\b/i],
  ["promo", /\b(?:pv|cm|preview|trailer|teaser|menu)\b/i],
]) {
  if (pattern.test(lower)) add(name);
}

console.log(JSON.stringify({ input, basename: base, tokens }, null, 2));
