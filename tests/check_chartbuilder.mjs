// The chart builder, rendered for real: the workflow node's Inspector and the
// Builders' WidgetInspector, out of Workflow.jsx and builderShared.jsx, on the
// one shared definition (chartFields.jsx / chartModel.js).
//
// Owner brief (2026-10-13), "Refactor the Chart and Graph Builder", and his
// screenshots: CAM 1 and CAM 2 each repeating Work out / One point per / Date
// column / Period, the chart repeating Group by time / Count over / Time
// range. Pinned here:
//
//   1. a chart's "Show as" lists its own family (chart type picker,
//      2026-10-14); the card kinds keep their own groups
//   2. node and Builder draw the SAME chart fields (labels, values, options)
//      for each category: bar, line graph, pie, donut (no axes), radar
//   3. the screenshot chart, read at open time: X = its status column (kept,
//      not switched to a datetime), Y once, the Date range in the main
//      section (mockup 64), the Date column in a collapsed Advanced, one
//      short row per camera
//   4. time controls only when X = Time; Hour / Day / Week / Month / Custom
//      with their defaults; the date range a separate setting
//   5. a dragged X / Y field is preselected and never replaced; saved then
//      reopened in the Builder reads the same
//   6. a hidden input stays listed; an override is one input's; saved then
//      reopened on the node reads the same, in the new shape
//   7. Advanced collapsed, thresholds in it; colours stay Builder-only
//   8. a Builder edit on a compiled card reaches its node in the model's names
//
//   node tests/check_chartbuilder.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".chartbuilder-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// The node Inspector is internal to Workflow.jsx: exported for this bundle
// only, the way check_recorded_inspector.mjs does it.
const exposeInspector = {
  name: "expose-inspector",
  setup(b) {
    b.onLoad({ filter: /pages[\\/]Workflow\.jsx$/ }, (args) => ({
      contents: readFileSync(args.path, "utf8") + "\nexport { Inspector };\n",
      loader: "jsx", resolveDir: dirname(args.path),
    }));
  },
};
const shimXyflow = {
  name: "shim-xyflow",
  setup(b) {
    b.onResolve({ filter: /^@xyflow\/react$/ }, () => (
      { path: "xyflow-shim", namespace: "shim" }));
    b.onResolve({ filter: /^@xyflow\/react\/dist\/style\.css$/ }, () => (
      { path: "xyflow-css", namespace: "shim" }));
    b.onLoad({ filter: /^xyflow-css$/, namespace: "shim" }, () => ({
      contents: "", resolveDir: FRONT, loader: "js" }));
    b.onLoad({ filter: /^xyflow-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement } from "react";
        export const Position = { Left: "left", Right: "right",
                                  Top: "top", Bottom: "bottom" };
        export const MarkerType = { ArrowClosed: "arrowclosed" };
        export const Handle = () => createElement("div");
        export const BaseEdge = () => createElement("div");
        export const EdgeLabelRenderer = ({ children }) => children || null;
        export const ReactFlow = () => createElement("div");
        export const ReactFlowProvider = ({ children }) => children || null;
        export const Background = () => createElement("div");
        export const Controls = () => createElement("div");
        export const MiniMap = () => createElement("div");
        export const Panel = ({ children }) => children || null;
        export const useReactFlow = () => ({});
        export const useUpdateNodeInternals = () => () => {};
        export const useStore = (s) => s({ edges: [], nodeLookup: new Map() });
        export const useNodesState = (v) => [v, () => {}, () => {}];
        export const useEdgesState = (v) => [v, () => {}, () => {}];
        export const addEdge = (c, e) => e;
        export const applyNodeChanges = (c, n) => n;
        export const applyEdgeChanges = (c, e) => e;
        export const getBezierPath = () => ["", 0, 0];
        export const getSmoothStepPath = () => ["", 0, 0];
        export const getStraightPath = () => ["", 0, 0];
        export const useOnSelectionChange = () => {};
        export const useKeyPress = () => false;
        export const SelectionMode = { Partial: "partial" };
        export const ConnectionMode = { Loose: "loose" };
        export const ConnectionLineType = { Bezier: "bezier" };
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import { Inspector } from "${p("src/pages/Workflow.jsx")}";
import { WidgetInspector, catalogWidget, cardQuestionEdits, seriesStyle }
  from "${p("src/components/widgets/builderShared.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { SHAPE_GROUPS } from "${p("src/components/widgets/cardFields.jsx")}";
export { SHOW_AS } from "${p("src/components/widgets/chartTypes.js")}";
export * as CM from "${p("src/components/widgets/chartModel.js")}";
export { catalogWidget, cardQuestionEdits, seriesStyle };
/** Mounted and left up, so a test can edit a control and read the patch. */
export function live(el, comp, props) {
  const root = createRoot(el);
  const C = comp === "node" ? Inspector : WidgetInspector;
  const render = (next) => act(() => {
    root.render(h(ToastProvider, null, h(C, next)));
  });
  render(props);
  return { act, render, unmount: () => act(() => root.unmount()) };
}
`;

await build({
  plugins: [exposeInspector, shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const mod = req(OUT);
const CM = mod.CM;

// -- fixtures: the owner's two cameras ---------------------------------------
const COLS = [{ name: "object_id", type: "text" }, { name: "status", type: "text" },
              { name: "total_panel", type: "number" }, { name: "made_on", type: "datetime" }];
const TABLES = [{ key: "cam1", name: "PCB-SENSATA-CAM1", columns: COLS },
                { key: "cam2", name: "PCB-SENSATA-CAM2", columns: COLS }];
const SOURCE_DATA = { source_mode: "", dataset: "", field: "", filters: {},
                      conditions: [], limit: 15 };
const GRAPH_DATA = { name: "Panels", metric: "", kind: "bar", unit: "", range: "24h",
                     agg: "", group_by: "", bucket: "", series: {}, series_n: 2,
                     window: "live", ...SOURCE_DATA };
const REGISTRY = {
  "dashboard.graph": { category: "dashboard", label: "Chart", data: GRAPH_DATA },
  "dashboard.card": { category: "dashboard", label: "Card", data: {} },
  "output.dataset": { category: "output", label: "Data Table" },
  "analytics.frame": { category: "analytics", label: "In Frame", metrics: ["count"] },
};
const T1 = { id: "t1", type: "output.dataset",
             data: { dataset: "cam1", name: "PCB-SENSATA-CAM1", columns: COLS } };
const T2 = { id: "t2", type: "output.dataset",
             data: { dataset: "cam2", name: "PCB-SENSATA-CAM2", columns: COLS } };
const wire = (src, col, handle) => ({ id: `w${src}${col}`, source: src, sourceHandle: `o_${col}`,
                                      target: "g1", targetHandle: handle });
const TWO = [wire("t1", "object_id", "in"), wire("t2", "object_id", "s2")];
const META = { variables: { event_fields: ["camera", "zone"], event_values: {}, tables: TABLES } };
//: The owner's screenshot, as stored: CAM 1 and CAM 2 each "Work out: total
//: rows, One point per: each status value, Date column: when the row was
//: saved, Period: follow the Analytics range".
const SCREENSHOT = { s1: { agg: "count", ts_field: "", query: { group_by: "status", range: "page" } },
                     s2: { agg: "count", ts_field: "", query: { group_by: "status", range: "page" } } };
const graph = (data) => ({ id: "g1", type: "dashboard.graph", data: { ...GRAPH_DATA, ...data } });

function openNode(n, edges = TWO, onChange = () => {}) {
  const box = document.body.appendChild(document.createElement("div"));
  const props = { node: n, registry: REGISTRY, meta: META, nodes: [n, T1, T2], edges,
                  editable: true, errors: [], warnings: [], onChange, functions: [],
                  onDone: () => {} };
  const m = mod.live(box, "node", props);
  return { box, m, props, close: () => { m.unmount(); box.remove(); } };
}
const BUILDER_META = {
  sources: { stat: ["fn_x_count"], bucket: [], events: [] },
  history: { aggs: ["avg", "sum", "min", "max", "last", "count"] },
  query: { aggregations: ["count", "sum"], columns: ["camera", "zone"] },
  kinds: ["tile", "line", "bar", "area", "spline", "donut", "pie", "radar", "gauge",
          "table", "timeline", "heatmap"],
  capabilities: [], catalog: TABLES.map((t) => ({ dataset: t.key, columns: t.columns })),
};
function openWidget(w, patch = () => {}) {
  const box = document.body.appendChild(document.createElement("div"));
  const m = mod.live(box, "widget", { current: w, meta: BUILDER_META, patch });
  return { box, m, close: () => { m.unmount(); box.remove(); } };
}
/** The compiled card a Builder sees for the screenshot chart (what
 *  workflow.compile_graph hands it: one entry per input, effective values). */
const entry = (id, ds) => ({ id, source: "dataset", dataset: ds, columns: COLS,
                             field: "object_id", agg: "count", ts_field: "",
                             label: ds === "cam1" ? "PCB-SENSATA-CAM1" : "PCB-SENSATA-CAM2",
                             query: { agg: "count", group_by: "status", range: "page" } });
const COMPILED = { id: "wf_g1", title: "Panels", kind: "bar", source: "stat", span: 1,
                   grid: {}, template: "plain", bands: {},
                   series: [entry("s1", "cam1"), entry("s2", "cam2")] };

/** Every labelled field INSIDE the chart builder, in order, with its options. */
function builderFields(box) {
  const root = box.querySelector(".chart-builder");
  if (!root) return null;
  return [...root.querySelectorAll("label.wf-field, label.ui-field")].map((el) => {
    const span = el.querySelector(".wf-field-label, .ui-field-label");
    const sel = el.querySelector("select");
    return { label: (span?.childNodes[0]?.textContent || "").trim(),
             value: sel ? sel.value : null,
             options: sel ? [...sel.options].map((o) => [o.value, o.textContent]) : null };
  });
}
const labels = (f) => (f || []).map((x) => x.label);
const field = (f, label) => (f || []).find((x) => x.label === label);
const select = (box, aria) => box.querySelector(`select[aria-label="${aria}"]`);
function choose(m, sel, value) {
  m.act(() => {
    sel.value = value;
    sel.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  });
}
function click(m, el) {
  m.act(() => { el.click(); });
}

// -- 1. two categories in "Show as" ------------------------------------------
{
  const { box, close } = openNode(graph({ series: SCREENSHOT }));
  const shape = [...box.querySelectorAll("label")].find((l) =>
    (l.querySelector(".wf-field-label")?.childNodes[0]?.textContent || "").trim() === "Show as");
  // Since the chart type picker (2026-10-14): a chart's "Show as" is its
  // FAMILY (this bar groups by status: a Comparison Bar, so Comparisons);
  // another family is "Change chart type".
  const opts = [...shape.querySelectorAll("option")].map((o) => [o.value, o.textContent]);
  console.log(`     Show as: ${JSON.stringify(opts)}`);
  // Radar is "Coming soon" (RADAR_READY): offered only to a saved radar.
  check("Show as on a Comparison Bar: Bar chart (Category) alone, Radar being 'Coming soon'",
        JSON.stringify(opts) === JSON.stringify(mod.SHOW_AS.comparisons.filter(([v]) => v !== "radar"))
        && !shape.querySelector("optgroup"), JSON.stringify(opts));
  close();
  const w = openWidget({ id: "x1", title: "T", kind: "tile", source: "stat", key: "fn_x_count",
                         span: 1, grid: {}, template: "plain", bands: {} });
  const sel = [...w.box.querySelectorAll("label")].find((l) =>
    (l.querySelector(".ui-field-label")?.childNodes[0]?.textContent || "").trim() === "Show as")
    .querySelector("select");
  const g2 = [...sel.querySelectorAll("optgroup")].map((g) => g.label);
  check("...and a hand-made Builder card still finds the card kinds in their own groups",
        g2.includes("Single value") && g2.includes("Lists, events and media")
        && g2.indexOf("Charts - compare categories and values") === 0, JSON.stringify(g2));
  w.close();
}

// -- 2. the node and the Builders draw the same field set ----------------------
const SAME = [];
for (const [what, data, compiled, edges] of [
  ["bar, X = column (the screenshot)", { kind: "bar", series: SCREENSHOT },
   COMPILED],
  ["line graph, X = time", { kind: "line", bucket: "1h", range: "24h",
                             series: { s1: { agg: "sum" }, s2: { agg: "sum" } } },
   { ...COMPILED, kind: "line", source: "history", range: "24h",
     series: [{ ...entry("s1", "cam1"), field: "total_panel", agg: "sum", ts_field: "made_on",
                query: { bucket: "1h", range: "24h" } },
              { ...entry("s2", "cam2"), field: "total_panel", agg: "sum", ts_field: "made_on",
                query: { bucket: "1h", range: "24h" } }] },
   [wire("t1", "total_panel", "in"), wire("t2", "total_panel", "s2")]],
  ["pie (no axes)", { kind: "pie", series: SCREENSHOT }, { ...COMPILED, kind: "pie" }],
  ["donut (no axes)", { kind: "donut", series: SCREENSHOT }, { ...COMPILED, kind: "donut" }],
  ["radar (category / metric)", { kind: "radar", series: SCREENSHOT }, { ...COMPILED, kind: "radar" }],
]) {
  const n = openNode(graph(data), edges || TWO);
  const b = openWidget(compiled);
  const fn = builderFields(n.box);
  const fb = builderFields(b.box);
  SAME.push([what, labels(fn)]);
  check(`${what}: the node and the Builder draw the same chart fields, values and options`,
        fn && fb && JSON.stringify(fn) === JSON.stringify(fb),
        `node    ${JSON.stringify(fn)}\n      builder ${JSON.stringify(fb)}`);
  n.close(); b.close();
}
for (const [what, l] of SAME) console.log(`     ${what}: ${l.join(" > ")}`);
const bySame = Object.fromEntries(SAME);
check("charts and graphs: an X axis and a Y axis",
      ["bar, X = column (the screenshot)", "line graph, X = time"].every((k) =>
        bySame[k].includes("X axis") && bySame[k].includes("Y axis")));
check("pie and donut: a Category field and a Value, no axes",
      ["pie (no axes)", "donut (no axes)"].every((k) => bySame[k][0] === "Category field"
        && bySame[k].includes("Value") && !bySame[k].includes("X axis")
        && !bySame[k].includes("Y axis")), JSON.stringify(bySame["pie (no axes)"]));
check("radar: a Category / metric and a Value, no axes",
      bySame["radar (category / metric)"][0] === "Category / metric"
      && bySame["radar (category / metric)"].includes("Value")
      && !bySame["radar (category / metric)"].includes("X axis"));

// -- 3. the screenshot chart, migrated at read time -----------------------------
{
  const { box, close } = openNode(graph({ series: SCREENSHOT }));
  const f = builderFields(box);
  check("the screenshot chart reads as X = the status column (kept, not switched to a date)",
        field(f, "X axis")?.value === "column" && field(f, "Category field")?.value === "status",
        JSON.stringify(f));
  check("...Y = Count records, once for both cameras",
        field(f, "Y axis")?.value === "count");
  check("...no interval on a column X",
        !field(f, "Interval"), JSON.stringify(labels(f)));
  // Mockup 64: the date range is in the main section on every type, a
  // Comparison Bar's included (it was "Only include rows from" in Advanced).
  const adv = box.querySelector("details.chart-advanced");
  check("...its Date range in the main section, its Date column in Advanced, collapsed",
        adv && !adv.open && field(f, "Date column")?.value === "@row"
        && field(f, "Date range")?.value === "page" && !adv.querySelector('select[aria-label="Date range"]')
        && !field(f, "Only include rows from"),
        JSON.stringify(f));
  const text = box.textContent;
  check("the repeated per-camera controls are gone",
        !/Work out|One point per|Each point shows|Count over|Group by time/.test(text)
        && !/\bPeriod\b/.test(text), text.match(/Work out|One point per|Period|Count over|Group by time/g));
  const rows = [...box.querySelectorAll('input[aria-label^="Series name for input"]')];
  const shows = [...box.querySelectorAll('input[aria-label^="Show input"]')];
  const overrides = [...box.querySelectorAll("details.chart-override")];
  check("each camera row: a series name, a Show toggle and a collapsed override",
        rows.length === 2 && shows.length === 2 && shows.every((c) => c.checked)
        && overrides.length === 2 && overrides.every((d) => !d.open));
  check("...and its source said, not offered",
        box.textContent.includes("PCB-SENSATA-CAM1 · object_id")
        && box.textContent.includes("PCB-SENSATA-CAM2 · object_id"));
  close();
}

// -- 4. time controls only when X = Time; the first edit writes the model --------
{
  let patch = null;
  let n = graph({ series: SCREENSHOT });
  const o = openNode(n, TWO, (p) => { patch = p; });
  choose(o.m, select(o.box, "X axis type"), "time");
  check("choosing Time writes the whole model (x_type, preset, step, range)",
        patch && patch.x_type === "time" && patch.time_interval === "day"
        && patch.bucket === "1d" && patch.range === "7d" && patch.group_by === "",
        JSON.stringify(patch));
  // Re-render with the patch applied, as the canvas does.
  n = graph({ ...n.data, ...patch });
  o.m.render({ ...o.props, node: n, nodes: [n, T1, T2] });
  let f = builderFields(o.box);
  check("with X = Time the Interval and the Date range appear",
        field(f, "Interval")?.value === "day" && field(f, "Date range")?.value === "7d",
        JSON.stringify(labels(f)));
  check("...offering Hour, Day, Week, Month, Custom",
        JSON.stringify(field(f, "Interval").options.map(([v]) => v))
          === JSON.stringify(["hour", "day", "week", "month", "custom"]));
  check("...and 'Only include rows from' is gone (one date range, never two)",
        !field(f, "Only include rows from"));
  choose(o.m, select(o.box, "Interval"), "week");
  n = graph({ ...n.data, ...patch });
  o.m.render({ ...o.props, node: n, nodes: [n, T1, T2] });
  f = builderFields(o.box);
  check("Week: a daily view of 7 days by default, with weekly aggregation as an option",
        patch.bucket === "1d" && patch.range === "7d" && field(f, "Show")?.value === "day"
        && field(f, "Show").options.some(([v]) => v === "alt"), JSON.stringify(patch));
  choose(o.m, select(o.box, "Show"), "alt");
  check("...weekly aggregation: every week over 3 months",
        patch.bucket === "week" && patch.range === "90d", JSON.stringify(patch));
  choose(o.m, select(o.box, "Interval"), "month");
  check("Month: 30 days of days by default", patch.bucket === "1d" && patch.range === "30d");
  n = graph({ ...n.data, ...patch });
  o.m.render({ ...o.props, node: n, nodes: [n, T1, T2] });
  choose(o.m, select(o.box, "Show"), "alt");
  check("...or each calendar month over a year", patch.bucket === "month" && patch.range === "365d");
  choose(o.m, select(o.box, "Interval"), "hour");
  check("Hour: the last 24 hours, hourly", patch.bucket === "1h" && patch.range === "24h");
  choose(o.m, select(o.box, "Interval"), "custom");
  n = graph({ ...n.data, ...patch });
  o.m.render({ ...o.props, node: n, nodes: [n, T1, T2] });
  f = builderFields(o.box);
  check("Custom: the step list and the picked dates, nothing else added",
        patch.range === "custom" && field(f, "Group by") && field(f, "Date range")?.value === "custom"
        && o.box.querySelector('input[aria-label="From"]') && o.box.querySelector('input[aria-label="To"]'),
        JSON.stringify(labels(f)));
  // The date range is its own setting: changing it leaves the interval alone.
  choose(o.m, select(o.box, "Date range"), "page");
  check("the date range changes alone (interval kept), 'follow the Analytics date picker' offered",
        patch.range === "page" && patch.time_interval === "custom");
  // Back to a column: the column X the operator assigned before is not lost
  // from the overrides and Time steps aside.
  choose(o.m, select(o.box, "X axis type"), "column");
  check("back to a column: the time step is cleared", patch.x_type === "column" && patch.bucket === "");
  o.close();
}

// -- 5. a dragged field is preselected and never replaced -------------------------
{
  // The Builder palette's table column ("Recorded data"), dragged in as a tile
  // and shown as a bar: its column is the Y, its grouping the X.
  const dragged = mod.catalogWidget({ kind: "bar", source: "dataset", dataset: "cam1",
                                      columns: COLS, title: "total panel", key: "",
                                      query: { agg: "sum", field: "total_panel",
                                               group_by: "status" } });
  dragged.series = [{ id: "s1", label: "", color: "", source: "", agg: "" }];
  let patched = null;
  const w = openWidget(dragged, (id, p) => { patched = p; });
  let f = builderFields(w.box);
  check("a dragged X field (status) is the X selection",
        field(f, "X axis")?.value === "column" && field(f, "Category field")?.value === "status",
        JSON.stringify(f));
  check("...and the dragged Y field (total_panel, Sum) the Y selection",
        field(f, "Y axis")?.value === "sum" && field(f, "Of column")?.value === "total_panel",
        JSON.stringify(f));
  choose(w.m, select(w.box, "Calculation"), "max");
  check("changing the calculation keeps both fields (no datetime switch)",
        patched && patched.chart.x_type === "column" && patched.chart.x_field === "status"
        && patched.chart.y_field === "total_panel" && patched.query.group_by === "status"
        && patched.query.field === "total_panel" && patched.query.agg === "max"
        && !patched.query.bucket, JSON.stringify(patched));
  // Saved then reopened: the widget as patched renders the same answers.
  w.m.render({ current: { ...dragged, ...patched }, meta: BUILDER_META, patch: () => {} });
  f = builderFields(w.box);
  check("saved then reopened in the Builder: the same X, Y and calculation",
        field(f, "Category field")?.value === "status" && field(f, "Y axis")?.value === "max"
        && field(f, "Of column")?.value === "total_panel", JSON.stringify(f));
  w.close();
  // A table that HAS a datetime column, X assigned: still the column.
  const n = openNode(graph({ kind: "line", series: {
    s1: { query: { group_by: "object_id" } }, s2: { query: { group_by: "object_id" } } } }));
  const fn = builderFields(n.box);
  check("a graph whose inputs were given an X column keeps it, though a date column exists",
        field(fn, "X axis")?.value === "column" && field(fn, "X column")?.value === "object_id",
        JSON.stringify(fn));
  n.close();
}

// -- 6. hidden inputs, overrides, saved-then-reopened on the node -------------------
{
  let patch = null;
  let n = graph({ series: SCREENSHOT });
  const o = openNode(n, TWO, (p) => { patch = p; });
  const show2 = o.box.querySelector('input[aria-label="Show input 2 on the chart"]');
  click(o.m, show2);
  check("hiding camera 2 stores show:false on its input (and the model with it)",
        patch && patch.series.s2.show === false && patch.x_type === "column"
        && (patch.series.s1 || {}).show === undefined, JSON.stringify(patch));
  n = graph({ ...n.data, ...patch });
  o.m.render({ ...o.props, node: n, nodes: [n, T1, T2] });
  check("...and reopened, its row is still listed, unticked",
        !o.box.querySelector('input[aria-label="Show input 2 on the chart"]').checked
        && o.box.querySelectorAll('input[aria-label^="Series name for input"]').length === 2);
  choose(o.m, o.box.querySelector('select[aria-label="Calculation for input 2"]'), "count_nonnull");
  check("an override of the Y calculation is that input's alone",
        patch.series.s2.agg === "count_nonnull" && !(patch.series.s1 || {}).agg
        && patch.agg === "count" && patch.series.s2.show === false, JSON.stringify(patch));
  n = graph({ ...n.data, ...patch });
  o.m.render({ ...o.props, node: n, nodes: [n, T1, T2] });
  const f = builderFields(o.box);
  check("saved then reopened on the node: the same model and overrides",
        field(f, "Category field")?.value === "status" && field(f, "Y axis")?.value === "count"
        && o.box.querySelector('select[aria-label="Calculation for input 2"]').value === "count_nonnull",
        JSON.stringify(f));
  check("...and the per-camera date column and period (shared now) are not overrides",
        !(n.data.series.s1 || {}).query && !("ts_field" in (n.data.series.s1 || {}))
        && n.data.ts_field === "@row"
        && n.data.range === "page", JSON.stringify(n.data));
  o.close();
}

// -- 6b. Count Difference Value (owner, 2026-10-13: "count for total
// different value only") on every chart kind, on both surfaces ------------------
{
  for (const kind of ["bar", "line", "area", "spline", "pie", "donut", "radar"]) {
    const n = openNode(graph({ kind, series: SCREENSHOT }));
    const b = openWidget({ ...COMPILED, kind });
    const opts = (box) => [...(select(box, "Calculation")?.options || [])]
      .map((o) => `${o.value}=${o.textContent}`);
    const on = opts(n.box);
    check(`${kind}: the ${["pie", "donut", "radar"].includes(kind) ? "Value" : "Y axis"} `
          + "calculation offers 'Count Difference Value' (unique), node and Builder alike",
          on.includes("unique=Count Difference Value")
          && JSON.stringify(on) === JSON.stringify(opts(b.box)), JSON.stringify(on));
    check(`${kind}: ...and 'Count Value Changes' (changes), in the owner's order`,
          JSON.stringify(on.map((o) => o.split("=")[1])) === JSON.stringify([
            "Count records", "Count non-null values", "Count Difference Value",
            "Count Value Changes", "Sum", "Average", "Minimum", "Maximum"]),
          JSON.stringify(on));
    n.close(); b.close();
  }
  let patch = null;
  const o = openNode(graph({ series: SCREENSHOT }), TWO, (p) => { patch = p; });
  choose(o.m, select(o.box, "Calculation"), "unique");
  check("picking it stores 'unique' as the chart's Y",
        patch && patch.agg === "unique" && patch.x_type === "column", JSON.stringify(patch));
  choose(o.m, select(o.box, "Calculation"), "changes");
  check("...and Count Value Changes stores 'changes'",
        patch && patch.agg === "changes", JSON.stringify(patch));
  o.close();
  // A Builder chart asking a table itself: the column picker shows, and with
  // none picked it says the X column is counted (and lowers to it).
  let wp = null;
  const dragged = mod.catalogWidget({ kind: "bar", source: "dataset", dataset: "cam1",
                                      columns: COLS, title: "t", key: "",
                                      query: { agg: "count", group_by: "status" } });
  dragged.series = [{ id: "s1", label: "", color: "", source: "", agg: "" }];
  const w = openWidget(dragged, (id, p) => { wp = p; });
  choose(w.m, select(w.box, "Calculation"), "unique");
  w.m.render({ current: { ...dragged, ...wp }, meta: BUILDER_META, patch: () => {} });
  check("...a table chart shows the column picker and says what it counts without one",
        !!select(w.box, "Of column")
        && w.box.textContent.includes("the different values of status are counted")
        && wp.query.agg === "unique" && wp.query.field === "status", JSON.stringify(wp && wp.query));
  w.close();
}

// -- 7. Advanced is collapsed everywhere and holds the thresholds ---------------------
{
  const n = openNode(graph({ kind: "line", series: SCREENSHOT, bands: { at: [5] } }));
  const adv = n.box.querySelector("details.chart-advanced");
  check("node: Advanced settings collapsed, thresholds inside it",
        adv && !adv.open && adv.textContent.includes("Thresholds"));
  n.close();
  const b = openWidget({ ...COMPILED, kind: "line", source: "history",
                         bands: { at: [5], colors: ["#16a34a", "#dc2626"] } });
  const advB = b.box.querySelector("details.chart-advanced");
  check("Builder: the same, and the series colours stay under Appearance",
        advB && !advB.open && advB.textContent.includes("Thresholds")
        && b.box.textContent.includes("Series colours"));
  b.close();
}

// -- 8. a Builder edit on a compiled card goes back to its node in the model's names ---
{
  let patched = null;
  const w = openWidget(COMPILED, (id, p) => { patched = p; });
  choose(w.m, select(w.box, "X axis type"), "time");
  const after = { ...COMPILED, ...patched };
  check("Builder: X = Time lowers to every input (one step and range for both cameras)",
        patched && patched.chart.x_type === "time"
        && patched.series.every((s) => s.query.bucket === "1d" && s.query.range === "7d"
                                && !s.query.group_by), JSON.stringify(patched && patched.series));
  check("...and the inputs keep their own tables (no camera swapped)",
        patched.series.map((s) => s.dataset).join() === "cam1,cam2");
  const edits = mod.cardQuestionEdits(COMPILED, after);
  check("card-style sends the node the model in its own field names",
        edits.x_type === "time" && edits.time_interval === "day" && edits.bucket === "1d"
        && edits.range === "7d" && edits.group_by === "" && edits.agg === "count"
        && edits.ts_field === "@row" && edits.interval === "", JSON.stringify(edits));
  const style = mod.seriesStyle(after, COMPILED);
  check("...and every input's overrides, cleared where it follows the chart",
        ["s1", "s2"].every((sid) => style[sid].agg === "" && style[sid].ts_field === null
          && style[sid].query.group_by === "" && style[sid].query.range === ""
          && style[sid].show === true), JSON.stringify(style));
  w.close();
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
