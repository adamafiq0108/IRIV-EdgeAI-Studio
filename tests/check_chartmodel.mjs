// The chart builder's READ-TIME MIGRATION (frontend chartModel.js), over
// every combination of the legacy keys a saved chart may hold.
//
// The brief: "migrate legacy configuration settings where necessary instead
// of silently discarding them", "preserve existing X-axis and Y-axis
// selections when editing a saved chart". A chart saved before the builder
// has its answers spread over the chart (bucket = "Group by time", range =
// "Time range"/"Period", agg = "Work out", window = "Count over") and each
// input (series[sid].agg / ts_field / query.group_by / query.range = "Work
// out", "Date column", "One point per", "Period"), and the compiler combines
// them by rules of its own (workflow.py series_entry, chart_axis,
// apply_series_conf, settle_axis, x_field_window, chart_series._one_x_axis).
//
// For every combination below, this re-states those LEGACY rules
// independently (legacyDrawn) and the MODEL's rules
// (workflow.apply_chart_model, modelDrawn), and checks:
//
//   1. lossless: each input draws the same X, step, Y fold, period and Date
//      column from the migrated model + overrides as it did before
//   2. an assigned X column is never replaced by time (or a datetime)
//   3. idempotent: reading what nodePatch writes gives the same model back,
//      and writing it again changes nothing
//   4. a chart already under the model reads exactly as stored
//
//   node tests/check_chartmodel.mjs
import { createRequire } from "node:module";
import { rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".chartmodel-test.cjs");
await build({
  entryPoints: [join(FRONT, "src/components/widgets/chartModel.js")],
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic",
  logLevel: "silent", external: ["react", "react-dom"],
  define: { "process.env.NODE_ENV": '"development"' },
});
const CM = req(OUT);

let bad = 0;
let shown = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const COLS = [{ name: "object_id", type: "text" }, { name: "status", type: "text" },
              { name: "total_panel", type: "number" }];
const DATED = [...COLS, { name: "made_on", type: "datetime" }];
const TIME = ["timestamp", "date", "datetime"];
const QUERY_RANGE_OF = { "7d": "week", "30d": "month" };
const QUERY_RANGES = ["page", "today", "week", "month", "24h", "7d", "30d", "custom"];
const FOLDS = ["avg", "sum", "min", "max", "last", "count"];
const stepSeconds = { "1h": 3600, "1d": 86400, week: 7 * 86400, month: 30 * 86400 };
const clean = (b) => ({ hour: "1h", day: "1d" }[b] || b || "");
const autoTs = (cols) => (cols.find((c) => TIME.includes(c.type)) || {}).name || "";

/** What one table input drew BEFORE the builder (the compiler's rules). */
function legacyDrawn(d, kind, input, byValue) {
  const conf = (d.series || {})[input.sid] || {};
  const cq = conf.query || {};
  const number = input.type === "number";
  const words = ["text", "boolean", "uuid"].includes(input.type);
  let group = byValue && words ? input.column : "";
  let fold = number ? "avg" : "count";
  let bucket = "";
  let range = "";
  const B = clean(d.bucket);
  if (B) {                                                  // chart_axis
    group = "";
    let want = d.agg === "latest" ? "last" : d.agg;
    if (FOLDS.includes(want)) {
      if (["sum", "avg", "min", "max"].includes(want) && !number) want = "count";
      fold = want;
    }
    bucket = B;
    const period = QUERY_RANGE_OF[d.range] || d.range;
    if (QUERY_RANGES.includes(period)) range = period;
  }
  if (conf.agg) {                                           // apply_series_conf
    if (["avg", "sum", "min", "max", "last"].includes(conf.agg) && !number) { /* kept */ }
    else if (FOLDS.includes(conf.agg)) fold = conf.agg;
  }
  let ts = autoTs(input.columns);
  if ("ts_field" in conf) ts = conf.ts_field || "";
  if ("group_by" in cq && cq.group_by) group = cq.group_by;
  if ("range" in cq && cq.range) range = cq.range;
  if (bucket && group) bucket = "";                         // settle_axis
  else if (bucket && fold === "last") fold = number ? "max" : "count";
  if (group && !range && d.range && !(d.range === "24h" && !B)) {   // x_field_window
    range = QUERY_RANGE_OF[d.range] || d.range;
  }
  // "latest" of a WORD is no store question: the dataset store answers it
  // as a count of rows (datasets_repo.aggregate), so that is what was drawn.
  if (fold === "last" && !number) fold = "count";
  return { group, bucket, fold, range, ts };
}

/** What the same input draws under the model (workflow.apply_chart_model). */
function modelDrawn(d, kind, input) {
  const conf = (d.series || {})[input.sid] || {};
  const cq = conf.query || {};
  const number = input.type === "number";
  let group = cq.group_by || (d.x_type === "column" && d.group_by
    && input.columns.some((c) => c.name === d.group_by) ? d.group_by : "");
  let bucket = "";
  if (!group && d.x_type === "time") {
    bucket = clean(d.bucket);
    if (!bucket && !CM.isGraphKind(kind)) bucket = ["page", "week", "month", ""].includes(d.range) ? "1d" : "1h";
  }
  let rng = cq.range || d.range || "";
  if ((stepSeconds[bucket] || 0) >= 86400 && QUERY_RANGE_OF[rng]) rng = QUERY_RANGE_OF[rng];
  const range = QUERY_RANGES.includes(rng) ? rng : "";
  let want = conf.agg || CM.foldOfCalc(d.agg || "");
  let fold = number ? "avg" : "count";
  if (["avg", "sum", "min", "max", "last"].includes(want) && !number) fold = "count";
  else if (FOLDS.includes(want)) fold = want;
  if (bucket && fold === "last") fold = number ? "max" : "count";
  const times = input.columns.filter((c) => TIME.includes(c.type)).map((c) => c.name);
  let ts = autoTs(input.columns);
  if ("ts_field" in conf) ts = conf.ts_field || "";
  else if (d.ts_field === "@row") ts = "";
  else if (d.ts_field && times.includes(d.ts_field)) ts = d.ts_field;
  return { group, bucket, fold, range, ts };
}

/** chart_series._one_x_axis: what is DRAWN when inputs disagree on X. */
function oneXAxis(list) {
  if (new Set(list.map((x) => x.group)).size < 2) return list;
  const first = list[0];
  return list.map((x) => (first.group ? { ...x, group: first.group, bucket: "" }
    : { ...x, group: "", bucket: first.bucket || x.bucket }));
}

// -- the matrix -------------------------------------------------------------------
const KINDS = ["bar", "line", "pie"];
const BUCKETS = ["", "day", "1h"];
const GROUPS = [[], ["status", "status"], ["status", undefined], ["object_id", "status"]];
const RANGES_IN = [[], ["page", "page"], ["page", undefined]];
const AGGS = [[], ["count", "count"], ["sum", "sum"], ["count", "sum"]];
const TSS = [[], ["", ""], ["made_on", "made_on"], ["", undefined]];
const CHART_AGG = ["", "count", "latest"];
const CHART_RANGE = ["24h", "week", "7d", "page"];
const COLUMN_SETS = [["total_panel", "total_panel"], ["status", "status"], ["total_panel", "status"]];

let cases = 0;
const failures = { lossless: [], xKept: [], idem: [], stable: [] };
for (const kind of KINDS) for (const bucket of BUCKETS) for (const groups of GROUPS)
for (const ranges of RANGES_IN) for (const aggs of AGGS) for (const tss of TSS)
for (const chartAgg of CHART_AGG) for (const chartRange of CHART_RANGE)
for (const cols of COLUMN_SETS) {
  cases += 1;
  const tables = [DATED, COLS];
  const inputs = cols.map((column, i) => ({
    sid: `s${i + 1}`, source: "dataset", column,
    type: COLS.find((c) => c.name === column).type, columns: tables[i] }));
  const series = {};
  inputs.forEach((inp, i) => {
    const e = {};
    if (aggs[i]) e.agg = aggs[i];
    if (tss[i] !== undefined) e.ts_field = tss[i];
    const q = {};
    if (groups[i]) q.group_by = groups[i];
    if (ranges[i]) q.range = ranges[i];
    if (Object.keys(q).length) e.query = q;
    if (Object.keys(e).length) series[inp.sid] = e;
  });
  const d = { kind, bucket, agg: chartAgg, range: chartRange, series, window: "live" };
  const byValue = !clean(bucket) && ["bar", "pie"].includes(kind);
  const before = oneXAxis(inputs.map((inp) => legacyDrawn(d, kind, inp, byValue)));
  const { model, overrides } = CM.readNodeModel(d, kind, inputs, {});
  const written = { ...d, ...CM.nodePatch(d, kind, model, overrides, {}) };
  const after = oneXAxis(inputs.map((inp) => modelDrawn(written, kind, inp)));
  const label = JSON.stringify({ kind, bucket, groups, ranges, aggs, tss, chartAgg, chartRange, cols });
  // On the shared sampler grid (a graph with no step and no column) a
  // period is the grid's, whichever input said it: not compared there.
  const grid = (x) => CM.isGraphKind(kind) && !x.bucket && !x.group;
  const same = before.every((b, i) => {
    const a = after[i];
    return a.group === b.group && a.bucket === b.bucket && a.fold === b.fold
      && a.ts === b.ts && (grid(b) || a.range === b.range);
  });
  if (!same) failures.lossless.push([label, before, after, model, overrides]);
  if (before[0].group && !(model.x_type === "column" && model.x_field === before[0].group)) {
    failures.xKept.push([label, model]);
  }
  const again = CM.readNodeModel(written, kind, inputs, {});
  const rewritten = { ...written, ...CM.nodePatch(written, kind, again.model, again.overrides, {}) };
  if (JSON.stringify(rewritten) !== JSON.stringify(written)) {
    failures.idem.push([label, written, rewritten]);
  }
  if (again.migrated !== false) failures.stable.push([label]);
}
const show = (list) => list.slice(0, 2).map((x) => JSON.stringify(x)).join("\n      ");
check(`lossless over ${cases} legacy shapes: every input draws the same X, step, Y, period and Date column`,
      !failures.lossless.length, `${failures.lossless.length} differ\n      ${show(failures.lossless)}`);
check("an assigned X column is never replaced (by time or a datetime)",
      !failures.xKept.length, show(failures.xKept));
check("idempotent: written once, read and written again, nothing changes",
      !failures.idem.length, show(failures.idem));
check("once written, the chart reads as the model (no second migration)",
      !failures.stable.length, show(failures.stable));
shown += cases;

// -- named shapes, read the way the operator will see them ---------------------------
const two = (column, type = "text") => [0, 1].map((i) => ({
  sid: `s${i + 1}`, source: "dataset", column, type, columns: COLS }));
{
  const { model, overrides } = CM.readNodeModel({ kind: "bar", range: "24h", series: {
    s1: { agg: "count", ts_field: "", query: { group_by: "status", range: "page" } },
    s2: { agg: "count", ts_field: "", query: { group_by: "status", range: "page" } } } },
  "bar", two("object_id"));
  check("the owner's screenshot: X status, Y count records, follow the Analytics range, saved time",
        model.x_type === "column" && model.x_field === "status" && model.y_agg === "count"
        && model.range === "page" && model.ts_field === ""
        && Object.values(overrides).every((o) => !Object.keys(o).length),
        JSON.stringify({ model, overrides }));
}
{
  const { model } = CM.readNodeModel({ kind: "bar", range: "24h" }, "bar", two("status"));
  check("a bar of a text column with nothing set: each value, counted, all time",
        model.x_type === "column" && model.x_field === "status" && model.y_agg === "count"
        && model.range === "", JSON.stringify(model));
}
{
  const { model } = CM.readNodeModel({ kind: "bar", bucket: "day", range: "7d", agg: "count" },
                                     "bar", two("total_panel", "number"));
  check("a bar per day: X time, Day preset, Y count",
        model.x_type === "time" && model.step === "1d" && model.time_interval === "day"
        && model.y_agg === "count" && model.range === "week", JSON.stringify(model));
}
{
  const { model } = CM.readNodeModel({ kind: "line", range: "6h", interval: "5m" },
                                     "line", [{ sid: "s1", source: "history" }]);
  check("a rule's history graph: X time, its old Time step kept as a Custom step",
        model.x_type === "time" && model.step === "5m" && model.time_interval === "custom"
        && model.range === "6h", JSON.stringify(model));
}
{
  const { model } = CM.readNodeModel({ kind: "pie", window: "follow", range: "24h" },
                                     "pie", [{ sid: "s1", source: "stat" }, { sid: "s2", source: "stat" }]);
  check("a pie of live rules: its Count over (follow) becomes the date range (page)",
        model.x_type === "column" && model.x_field === "" && model.range === "page",
        JSON.stringify(model));
  const patch = CM.nodePatch({}, "pie", model, {}, {});
  check("...and writing it derives Count over back from the range",
        patch.window === "follow", JSON.stringify(patch));
}
{
  const { model, overrides } = CM.readNodeModel({ kind: "bar", bucket: "day", series: {
    s1: { agg: "sum" }, s2: { agg: "max" } } }, "bar", two("total_panel", "number"));
  check("inputs that disagree on Y: the first is the chart's, the other an override",
        model.y_agg === "sum" && overrides.s2.agg === "max" && !overrides.s1.agg,
        JSON.stringify({ model, overrides }));
}
{
  const d = { kind: "bar", x_type: "column", group_by: "status", agg: "count_nonnull",
              range: "page", ts_field: "@row", time_interval: "", bucket: "",
              series: { s2: { show: false, agg: "max" } } };
  const { model, overrides, migrated } = CM.readNodeModel(d, "bar", two("total_panel", "number"));
  check("a chart saved under the model reads as stored, overrides and hidden inputs included",
        !migrated && model.x_field === "status" && model.y_agg === "count_nonnull"
        && model.ts_field === "@row" && overrides.s2.show === false
        && overrides.s2.agg === "max", JSON.stringify({ model, overrides }));
}
{
  // Count Difference Value: the cards' old "unique", now a chart calculation.
  const d = { kind: "bar", range: "24h", series: { s1: { agg: "unique" }, s2: { agg: "unique" } } };
  const { model, overrides } = CM.readNodeModel(d, "bar", two("object_id"));
  const written = { ...d, ...CM.nodePatch(d, "bar", model, overrides, {}) };
  const again = CM.readNodeModel(written, "bar", two("object_id"));
  check("legacy per-input 'unique' migrates to the chart's Count Difference Value, round trip stable",
        model.y_agg === "unique" && !overrides.s1.agg && written.agg === "unique"
        && !again.migrated && again.model.y_agg === "unique"
        && CM.calcWords("unique") === "Count Difference Value" && CM.calcNeedsColumn("unique"),
        JSON.stringify({ model, written }));
  check("...and its automatic Y title",
        CM.autoAxisTitles({ x_type: "column", x_field: "status", y_agg: "unique",
                            y_field: "object_id" }).y === "Count Difference Value of object id");
}
{
  // Count Value Changes: the cards' old "changes", now a chart calculation.
  const d = { kind: "line", bucket: "1h", series: { s1: { agg: "changes" }, s2: { agg: "changes" } } };
  const { model, overrides } = CM.readNodeModel(d, "line", two("status"));
  const written = { ...d, ...CM.nodePatch(d, "line", model, overrides, {}) };
  const again = CM.readNodeModel(written, "line", two("status"));
  check("legacy per-input 'changes' migrates to the chart's Count Value Changes, round trip stable",
        model.y_agg === "changes" && written.agg === "changes" && !again.migrated
        && again.model.y_agg === "changes" && CM.calcWords("changes") === "Count Value Changes"
        && CM.calcNeedsColumn("changes")
        && CM.autoAxisTitles({ x_type: "time", step: "1h", y_agg: "changes",
                               y_field: "status" }).y === "Count Value Changes of status",
        JSON.stringify({ model, written }));
}
// -- presets and titles ---------------------------------------------------------------
{
  const m = CM.defaultModel("line");
  const h = CM.presetPatch(m, "hour");
  const dd = CM.presetPatch(m, "day");
  const w1 = CM.presetPatch(m, "week");
  const w2 = CM.presetPatch(m, "week", true);
  const m1 = CM.presetPatch(m, "month");
  const m2 = CM.presetPatch(m, "month", true);
  const c = CM.presetPatch({ ...m, step: "6h" }, "custom");
  check("Hour 24h/1h, Day 7d/1d, Week 7d/1d or 90d/week, Month 30d/1d or 365d/month, Custom picked dates",
        [h, dd, w1, w2, m1, m2].map((x) => `${x.range}/${x.step}`).join(" ")
          === "24h/1h 7d/1d 7d/1d 90d/week 30d/1d 365d/month"
        && c.range === "custom" && c.step === "6h" && CM.isAlt(w2) && CM.isAlt(m2) && !CM.isAlt(w1));
  const t = CM.setXType({ ...CM.defaultModel("bar"), x_field: "status" }, "time", "bar");
  check("choosing Time on a bar starts it at the Day preset; back to a column keeps its field",
        t.x_type === "time" && t.time_interval === "day"
        && CM.setXType(t, "column", "bar").x_field === "status");
  const at = CM.autoAxisTitles;
  check("automatic axis titles say the configuration",
        JSON.stringify(at({ x_type: "time", step: "6h", y_agg: "avg", y_field: "total_panel" },
                          { unit: "panels", stepWords: (s) => `Every ${s}` }))
          === JSON.stringify({ x: "Every 6h", y: "Average total panel (panels)" })
        && JSON.stringify(at({ x_type: "column", x_field: "status", y_agg: "count" }))
          === JSON.stringify({ x: "status", y: "Total rows" })
        && at({ x_type: "time", step: "1h", y_agg: "count" }).x === "Hour"
        && at({ x_type: "time", step: "", y_agg: "count_nonnull", y_field: "object_id" }).y
          === "Count of object id");
}

rmSync(OUT, { force: true });
console.log(`     ${shown} legacy shapes migrated`);
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
