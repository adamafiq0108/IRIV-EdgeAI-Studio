// What a Builder sends back to a compiled card's NODE when it saves.
//
// "Work out: total rows" picked in the Analytics builder was back to "average
// value" after the next workflow save: the card's question travelled only on
// its widget row, which the server owns and rebuilds from the node. So the
// Builders now send the QUESTION to the node with the look (card-style), and
// the backend half is pinned in tests/smoke_builderedits.py. This pins the
// browser half:
//
//   1. only what the operator CHANGED is sent, diffed against the row as the
//      editor loaded it -- a compiler default must never be pinned onto the
//      node by pressing Save;
//   2. the fields are spelled the node's way (query.agg -> agg, ...), one bar
//      per a period and per a column travel as a pair;
//   3. a series row's fold/grouping travels only where it changed;
//   4. both Builders put it on the card-style payload, linked copies too.
//
//   node tests/check_builderedits.mjs
import { rmSync } from "node:fs";
import { readText } from "./_text.mjs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".builderedits-test.cjs");

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  stdin: {
    contents: `export { cardQuestionEdits, seriesStyle } from "${p("src/components/widgets/builderShared.jsx")}";`,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const mod = req(OUT);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The owner's chart as the compiler wrote it: the bucket is the compiler's
// own guess for a number column, never something anybody picked.
const loaded = {
  id: "wf_g", kind: "bar", source: "dataset", dataset: "counting",
  query: { agg: "avg", field: "live_counting", bucket: "hour", range: "" },
  unit: "", limit: 15,
};

// 1. Nothing changed, nothing sent -- the bucket guess stays a guess.
check("an untouched card sends no question at all",
      same(mod.cardQuestionEdits(loaded, structuredClone(loaded)), {}));
check("...and neither does a card the editor never loaded",
      same(mod.cardQuestionEdits(null, loaded), {}));

// 2. "Work out: total rows" -> agg, in the node's spelling, and nothing else.
const counted = structuredClone(loaded);
counted.query.agg = "count";
check("'Work out' changed sends exactly agg",
      same(mod.cardQuestionEdits(loaded, counted), { agg: "count" }),
      JSON.stringify(mod.cardQuestionEdits(loaded, counted)));

// One bar per a column instead of a period: both halves, so the node's
// bucket is cleared rather than left to contradict the column.
const perShift = structuredClone(loaded);
perShift.query = { ...perShift.query, bucket: "", group_by: "shift" };
check("'One bar per' sends bucket and group_by as a pair",
      same(mod.cardQuestionEdits(loaded, perShift), { bucket: "", group_by: "shift" }));

// The shape, the period, the unit, the rows -- each under its node name.
const many = { ...structuredClone(loaded), kind: "line", unit: "pcs", limit: 20 };
many.query.range = "week";
check("shape, period, unit and rows each travel under the node's name",
      same(mod.cardQuestionEdits(loaded, many),
           { kind: "line", range: "week", limit: 20, unit: "pcs" }),
      JSON.stringify(mod.cardQuestionEdits(loaded, many)));

// A sampled chart keeps its time range and interval on the widget itself.
const hist = { id: "wf_h", kind: "line", source: "history", key: "fn_x", range: "24h" };
// The node has ONE step (`bucket`, services/timeaxis.py) -- a sampled chart's
// grid and a store input's bucket alike -- so the Builder's step goes there
// and the old `interval` is cleared.
check("a history chart's time range and step reach the node as range/bucket",
      same(mod.cardQuestionEdits(hist, { ...hist, range: "7d", interval: "1h" }),
           { range: "7d", bucket: "1h", interval: "" }),
      JSON.stringify(mod.cardQuestionEdits(hist, { ...hist, range: "7d", interval: "1h" })));
check("...and a picked date range travels whole",
      same(mod.cardQuestionEdits(hist, { ...hist, range: "custom",
                                         range_from: "2026-10-01T08:00",
                                         range_to: "2026-10-02" }),
           { range: "custom", range_from: "2026-10-01T08:00", range_to: "2026-10-02" }));

// 3. Series: names and colours always (that contract predates this), the
// fold only where it moved.
const chart = { id: "wf_c", kind: "line", source: "history", series: [
  { id: "s1", key: "a", label: "A", color: "", agg: "avg" },
  { id: "s2", key: "b", label: "B", color: "", agg: "avg",
    query: { group_by: "", range: "" } }] };
const moved = structuredClone(chart);
moved.series[1].agg = "max";
moved.series[1].query.range = "today";
const styled = mod.seriesStyle(moved, chart);
check("a series row sends its fold and period only where they changed",
      same(styled.s1, { label: "A", color: "" })
      && same(styled.s2, { label: "B", color: "", agg: "max", query: { range: "today" } }),
      JSON.stringify(styled));
// The owner's CAM 1 row (2026-10-12): a table-column input whose "Work out"
// went from "average value" to "total rows". The row writes that into its
// STORE question (query.agg) and blanks its own fold; the node's series
// entry says it as `agg`, so that is what has to travel -- the blanked fold
// read as "" (the compiler's guess) and the next save put the average back.
const cam = { id: "wf_g", kind: "line", source: "dataset", series: [
  { id: "s1", label: "CAM 1", color: "", source: "dataset", dataset: "pcb_sensata",
    field: "total_panel", ts_field: "", agg: "avg",
    query: { agg: "avg", group_by: "status", range: "page", filters: {} } }] };
const totalRows = structuredClone(cam);
totalRows.series[0] = { ...totalRows.series[0], agg: "",
                        query: { ...totalRows.series[0].query, agg: "count" } };
const camSent = mod.seriesStyle(totalRows, cam).s1;
check("a table input's 'Work out: total rows' reaches the node as agg count",
      camSent.agg === "count" && !camSent.query, JSON.stringify(camSent));
const latest = structuredClone(cam);
latest.series[0] = { ...latest.series[0], agg: "",
                     query: { ...latest.series[0].query, agg: "latest" } };
check("...'the latest value' as the node's own word for it (last)",
      mod.seriesStyle(latest, cam).s1.agg === "last");
check("...and an untouched row sends no fold at all",
      !("agg" in mod.seriesStyle(structuredClone(cam), cam).s1));
check("...and with no loaded row it is names and colours, as before",
      same(mod.seriesStyle(moved).s2, { label: "B", color: "" }));

// 4. Both Builders put it on the payload, for compiled cards and copies.
for (const page of ["Builder.jsx", "AnalyticsBuilder.jsx"]) {
  const src = readText(join(FRONT, "src", "pages", page));
  check(`${page} sends a compiled card's question with its look`,
        src.includes("...cardQuestionEdits(base.get(w.id), w) });")
        && src.includes("seriesStyle(w, base.get(w.id))"));
  check(`${page} sends a linked copy's question to its node too`,
        (src.match(/cardQuestionEdits\(base\.get\(w\.id\), w\)/g) || []).length === 2);
  check(`${page} diffs from the rows it loaded and resets only once it landed`,
        src.includes("base.reset(") && src.includes("if (onNodes || !styled.length) base.reset("));
}

rmSync(OUT, { force: true });
if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall builder-edit checks passed");
