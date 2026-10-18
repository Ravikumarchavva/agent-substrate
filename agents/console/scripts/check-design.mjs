#!/usr/bin/env node
// Design-system ratchet. Counts the things DESIGN.md forbids outside src/design and fails if any count goes UP versus
// design-baseline.json, so the old code can be migrated screen by screen while new code cannot make it worse.
//
//   node scripts/check-design.mjs            check against the baseline
//   node scripts/check-design.mjs --update   write the current counts (run after a migration lowers them)
//
// When every count is 0 the baseline file is deleted and these become plain lint errors.
import { readdirSync, readFileSync, statSync, writeFileSync, existsSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(import.meta.url), "..", "..");
const src = join(root, "src");
const baselinePath = join(root, "design-baseline.json");

const RULES = {
  // A raw control instead of design/ui Button / Input.
  rawButton: /<button\b/g,
  rawInput: /<input\b/g,
  // A colour literal instead of a token.
  hexColour: /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![0-9a-fA-F])/g,
  // A one-off size instead of the scale: h-[72px], text-[11px], p-[3px]…
  arbitrarySize: /\b(?:min-h|max-h|min-w|max-w|h|w|size|text|gap|rounded|p[xytblr]?|m[xytblr]?|top|left|right|bottom|inset)-\[-?[\d.]+(?:px|rem)\]/g,
};

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (relative(src, path) === "design" || name === "node_modules") continue;
      yield* files(path);
    } else if (/\.(tsx|ts)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      yield path;
    }
  }
}

const counts = Object.fromEntries(Object.keys(RULES).map((k) => [k, 0]));
for (const file of files(src)) {
  const text = readFileSync(file, "utf8");
  for (const [rule, pattern] of Object.entries(RULES)) counts[rule] += (text.match(pattern) ?? []).length;
}

if (process.argv.includes("--update")) {
  writeFileSync(baselinePath, JSON.stringify(counts, null, 2) + "\n");
  console.log("design baseline updated:", counts);
  process.exit(0);
}

if (!existsSync(baselinePath)) {
  const bad = Object.entries(counts).filter(([, n]) => n > 0);
  if (bad.length) {
    console.error("design violations:", Object.fromEntries(bad));
    process.exit(1);
  }
  process.exit(0);
}

const baseline = JSON.parse(readFileSync(baselinePath, "utf8"));
const worse = Object.keys(RULES).filter((k) => counts[k] > (baseline[k] ?? 0));
const better = Object.keys(RULES).filter((k) => counts[k] < (baseline[k] ?? 0));
for (const k of Object.keys(RULES)) console.log(`${k.padEnd(14)} ${String(counts[k]).padStart(5)}  (baseline ${baseline[k] ?? 0})`);
if (worse.length) {
  console.error(`\nFAIL: ${worse.join(", ")} went up. Use design/ui and the token scales (src/design/DESIGN.md).`);
  process.exit(1);
}
if (better.length) console.log(`\nImproved: ${better.join(", ")}. Run with --update to lock it in.`);
