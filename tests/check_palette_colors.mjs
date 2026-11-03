// Every workflow category has a colour, and the colours group by STAGE.
//
// Hue says which stage a node belongs to -- slate for the canvas tools
// (Common, its own stage at the top), blue takes data in, greens
// measure it, oranges support the graph without being a stage in it, and the
// last three are where a pipeline ends: kept, shown, acted on. The shade
// within a hue tells neighbouring categories apart (Class Filters from
// Zones), which one flat colour per stage could not.
//
// A check rather than a comment because the failure is silent: adding a
// category with a colour picked at random looks fine on its own and quietly
// costs the palette the one property that makes it scannable.
//
//   node tests/check_palette_colors.mjs
import { readFileSync } from "node:fs";
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "frontend", "src");
const registry = readText(join(SRC, "workflow", "registry.js"));
const workflow = readText(join(SRC, "pages", "Workflow.jsx"));

// The scheme as specified: category -> [colour, stage].
const WANT = {
  // Comments, link jumps, the debug probe: its own stage, FIRST (Tok Aba
  // 2026-10-05), in slate so it does not read as more Logic.
  common:    ["#94A3B8", "common"],
  source:    ["#3B82F6", "input"],
  filter:    ["#38BDF8", "input"],
  // The box's own terminal block: a lighter blue than the camera Source,
  // because a wire into DI0 is the other way a pipeline starts.
  board:     ["#60A5FA", "input"],
  // The clock. Input, because a schedule starts a pipeline the way a camera
  // does -- cyan, so "the time came round" never reads as one more lens.
  time:      ["#06B6D4", "input"],
  analytics: ["#10B981", "processing"],
  // "AI Processing": zone functions and object functions under one drawer,
  // two sub-headings. The separate `frame` drawer is gone.
  zone:      ["#22C55E", "processing"],
  // Math computes on what the Functions measured, so it sits in the same
  // stage but must not wear the same green -- teal reads as "still
  // processing" while saying it is a different job.
  math:      ["#14B8A6", "processing"],
  logic:     ["#F97316", "logic"],
  variable:  ["#F59E0B", "logic"],
  database:  ["#8B5CF6", "storage"],
  dashboard: ["#EAB308", "visualization"],
  output:    ["#EF4444", "actions"],
  // Messaging leaves the box and gets answered -- same stage as a siren,
  // different job, so a lighter red of the same family. The protocol
  // families (MQTT, HTTP, API, Modbus) moved in here whole, which retired the
  // External Values drawer their reading halves used to sit in.
  integration: ["#F87171", "actions"],
};

// Stage bases, in the order a graph moves through them.
const STAGE_BASE = {
  common: "#64748B", input: "#2563EB", processing: "#16A34A", logic: "#EA580C",
  storage: "#7C3AED", visualization: "#CA8A04", actions: "#DC2626",
};
const ORDER = ["common", "input", "processing", "logic", "storage",
               "visualization", "actions"];

let bad = 0;
const fail = (msg) => { console.error(msg); bad += 1; };

// -- categories --------------------------------------------------------------
const block = registry.split("export const CATEGORY_STYLE = {")[1].split("\n};")[0];
const got = {};
for (const m of block.matchAll(
    /^\s*(\w+):\s*\{\s*color:\s*"(#[0-9A-Fa-f]{6})",\s*stage:\s*"(\w+)"/gm)) {
  got[m[1]] = [m[2].toUpperCase(), m[3]];
}
for (const [cat, [colour, stage]] of Object.entries(WANT)) {
  const have = got[cat];
  if (!have) { fail(`registry.js: ${cat} has no colour/stage`); continue; }
  if (have[0] !== colour) {
    fail(`registry.js: ${cat} is ${have[0]}, should be ${colour}`);
  }
  if (have[1] !== stage) {
    fail(`registry.js: ${cat} is in stage ${have[1]}, should be ${stage}`);
  }
}
for (const cat of Object.keys(got)) {
  if (!(cat in WANT)) fail(`registry.js: ${cat} is a category nobody specified`);
}

// -- stage bases -------------------------------------------------------------
const stages = registry.split("export const STAGES = [")[1].split("\n];")[0];
const bases = {};
for (const m of stages.matchAll(
    /id:\s*"(\w+)",\s*title:\s*"[^"]*",\s*base:\s*"(#[0-9A-Fa-f]{6})"/g)) {
  bases[m[1]] = m[2].toUpperCase();
}
for (const [id, base] of Object.entries(STAGE_BASE)) {
  if (bases[id] !== base) {
    fail(`registry.js: stage ${id} base is ${bases[id] || "missing"}, should be ${base}`);
  }
}
const declared = [...stages.matchAll(/id:\s*"(\w+)"/g)].map((m) => m[1]);
if (declared.join(">") !== ORDER.join(">")) {
  fail(`registry.js: STAGES run ${declared.join(" -> ")}, expected ${ORDER.join(" -> ")}`);
}
// Every category's stage must be one that exists, or its drawer gets no heading.
for (const [cat, [, stage]] of Object.entries(WANT)) {
  if (!declared.includes(stage)) fail(`registry.js: ${cat} names unknown stage ${stage}`);
}

// -- palette order -----------------------------------------------------------
const order = workflow.split("const PALETTE_ORDER = [")[1].split("];")[0];
const ordered = [...order.matchAll(/"(\w+)"/g)].map((m) => m[1]);
// Categories that colour a node but open no drawer. `analytics` is the
// server's wiring category for everything computed: its nodes are filed under
// AI Processing and Math, but a node that declares no palette of its own is
// still coloured by it, so the colour must stay specified.
const WIRING_ONLY = new Set(["analytics"]);
for (const cat of Object.keys(WANT)) {
  if (WIRING_ONLY.has(cat)) {
    if (ordered.includes(cat)) {
      fail(`Workflow.jsx: ${cat} is wiring-only and must not open a drawer`);
    }
    continue;
  }
  if (!ordered.includes(cat)) {
    fail(`Workflow.jsx: ${cat} is missing from PALETTE_ORDER, so its drawer `
         + "renders last with no deliberate place");
  }
}
for (const cat of ordered) {
  if (!(cat in WANT)) fail(`Workflow.jsx: PALETTE_ORDER names unknown ${cat}`);
}

// Stages must be CONTIGUOUS down the palette -- the headings are rendered on
// the first drawer of each stage, so a split stage would print its heading
// twice -- and must run in pipeline order.
const seen = [];
for (const cat of ordered) {
  const stage = WANT[cat]?.[1];
  if (!stage) continue;
  if (seen[seen.length - 1] !== stage) {
    if (seen.includes(stage)) {
      fail(`Workflow.jsx: the ${stage} stage is split -- ${cat} sits apart from `
           + "the rest of its group, so its heading would print twice");
    }
    seen.push(stage);
  }
}
if (seen.join(">") !== ORDER.join(">")) {
  fail(`Workflow.jsx: stages run ${seen.join(" -> ")}, expected ${ORDER.join(" -> ")}`);
}

// -- custom drawers must still list their nodes ------------------------------
// Two drawers render themselves rather than listing node types: Zones shows
// the zones, Database shows the tables. Both must ALSO render `items`, or a
// node whose palette.cat points at them is in the group and drawn nowhere --
// it is not missing from the registry, not missing from the payload, and not
// missing from the bundle, so every check passes while the operator cannot
// find it. That is exactly how the Query node shipped invisible.
const branches = workflow.split('if (cat === "').slice(1);
for (const chunk of branches) {
  const cat = chunk.slice(0, chunk.indexOf('"'));
  // Up to the next such branch: enough of the block to see what it renders.
  const body = chunk.split('if (cat === "')[0];
  // Only the ones that draw a DRAWER. The same `cat === ...` test is used in
  // the inspector to ask what a node is wired to, which has no items to list.
  if (!body.includes("<Drawer")) continue;
  if (!/\bitems\b/.test(body)) {
    fail(`Workflow.jsx: the ${cat} drawer renders itself but never uses `
         + "`items`, so any node with that palette.cat is invisible");
  }
}

if (bad) {
  console.error(`\n${bad} palette problem(s).`);
  process.exit(1);
}
console.log(`== ${Object.keys(WANT).length} categories in ${declared.length} stages, `
            + `colours as specified, contiguous: ${seen.join(" -> ")}`);
