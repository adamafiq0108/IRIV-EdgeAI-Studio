// The {placeholders} a message node offers, worked out on the canvas the way
// the compiler works them out (frontend/src/workflow/msgPlaceholders.js).
//
//   1. csvNames: every CSV node wired into a message, in wire order, named as
//      the compiler names it, a clash suffixed _2 (Tok Aba, 2026-10-14)
//   2. the inspector offers one {csv:name} / {csv_text:name} pair per file
//      when two or more are wired, and the old {csv} / {csv_text} for one
//
//   node tests/check_msgplaceholders.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const mod = await import(new URL("../frontend/src/workflow/msgPlaceholders.js", import.meta.url));
const ui = readFileSync(join(ROOT, "frontend", "src", "pages", "Workflow.jsx"), "utf8");

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : `\n      ${detail}`));
  if (!ok) bad += 1;
};

const node = (id, type, data = {}) => ({ id, type, data });
const edge = (source, target) => ({ id: `${source}-${target}`, source, target });
const nodes = [
  node("q1", "storage.query", { name: "Query 1" }),
  node("q2", "storage.query", { name: "Cam two" }),
  node("c1", "storage.csv", { name: "violation_cam1" }),
  node("c2", "storage.csv", { name: "" }),
  node("c3", "storage.csv", { name: "lonely" }),
  node("m", "output.email", {}),
];
const edges = [edge("q1", "c1"), edge("q2", "c2"), edge("c1", "m"), edge("c2", "m"),
               edge("c3", "m")];

// -- 1. csvNames ----------------------------------------------------------------
check("every CSV with a Query, in wire order, named as the compiler names it",
      JSON.stringify(mod.csvNames("m", nodes, edges))
      === JSON.stringify(["violation_cam1.csv", "cam_two.csv"]),
      JSON.stringify(mod.csvNames("m", nodes, edges)));
check("a clash gets a suffix, as unique_csv_names does",
      JSON.stringify(mod.uniqueCsvNames(["same.csv", "same.csv", "same.csv"]))
      === JSON.stringify(["same.csv", "same_2.csv", "same_3.csv"]));
check("a message with nothing wired has no files",
      mod.csvNames("x", nodes, edges).length === 0);

// -- 2. the chips -------------------------------------------------------------------
check("two or more files: one {csv:name} / {csv_text:name} pair per file",
      ui.includes("[`{csv:${f}}`, `the file ${f}, by name`]")
      && ui.includes("[`{csv_text:${f}}`, `the rows of ${f}, in the message`]")
      && ui.includes("csvFiles.length > 1 ? csvFiles.flatMap("));
check("...one file keeps the old {csv} / {csv_text} chips",
      ui.includes("hasCsv && csvFiles.length < 2"));
check("both message inspectors pass the files",
      (ui.match(/hasCsv=\{hasCsv\} csvFiles=\{csvFiles\}/g) || []).length === 2);

// -- 3. an unknown {name}, said while typing (mirrors smoke_msgplaceholders) ------
const wired = ["vs1", "violation_summary_cam1", "vs2", "violation_summary_cam2"];
check("an unknown name is found; built-ins, wired names, files and {\"a\": 1} are not",
      JSON.stringify(mod.unknownPlaceholders(
        ["{label} {violation_summary} {csv:a.csv} {csv_text:b.csv}", "{\"a\": 1} {vs2}"],
        wired, ["a.csv"])) === JSON.stringify(["violation_summary", "csv_text:b.csv"]),
      JSON.stringify(mod.unknownPlaceholders(
        ["{label} {violation_summary} {csv:a.csv} {csv_text:b.csv}"], wired, ["a.csv"])));
check("the nearest wired name is suggested, within half the name",
      mod.closestPlaceholder("violation_summary", wired) === "violation_summary_cam1"
      && mod.closestPlaceholder("abc", ["xyzuvw"]) === "");
check("the inspector draws the note under BOTH message fields, with a click-to-replace",
      (ui.match(/<UnknownPlaceholders texts=/g) || []).length === 2
      && ui.includes("data-unknown-placeholders")
      && ui.includes("onClick={() => onReplace(`{${name}}`, `{${near}}`)}"));
check("...and its known names skip the Schedule / Inject that fires the message",
      ui.includes('const CLOCK_FN_TYPES = ["schedule", "inject", "state_watch"];')
      && ui.includes("!CLOCK_FN_TYPES.includes((registry[n.type] || {}).fn_type)"));

// -- 4. smaller items (job 4) --------------------------------------------------------
check("a message's chips never offer the Schedule / Inject that fires it",
      (ui.match(/<PlaceholderBar wired=\{msgWiredInputs\}/g) || []).length === 2
      && !ui.includes("<PlaceholderBar wired={wiredInputs}"));
check("AI off + a message the app holds while it is off: a calm amber note",
      ui.includes("const heldWhileAiOff = !!aiStatus && !aiOf(aiStatus).ok")
      && ui.includes("waitsForDetection.has(node.id)")
      && (ui.match(/This report is not sent while AI detection is off\./g) || []).length === 2
      && ui.includes("aiStatus={aiStatus} waitsForDetection={waitsForDetection}"));

if (bad) {
  console.log(`\n${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
