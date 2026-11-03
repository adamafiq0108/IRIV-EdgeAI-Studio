// A chart INPUT reads the same on the workflow node and in the Builder.
//
// Owner (2026-10-12, an AREA chart, input "CAM 1" = the total_panel column of
// pcb_sensata): he set "Work out" to a total in the Builder, the node's
// Inputs row still said "from the column type (a number is averaged ...)",
// and a workflow save compiled the average back. Requirement: one stored
// value per setting, shown identically in both places, both directions.
//
// tests/seriessync_fixture.json holds the input as the NODE stores it
// (conf) and as the compiled widget row stores it (rows) -- and
// tests/smoke_seriesedits.py checks that the real compile turns the one into
// the other for area, line, spline and bar. Here both REAL panels are
// rendered from those two halves and compared, field by field:
//
//   1. the chart's Y (the chart builder, 2026-10-13: "Work out" is asked once
//      per chart) and each input's override show the same selected answer in
//      the same words, for count, sum and an input with no answer of its own
//      (the compiler's average), on every chart shape;
//   2. both offer the same calculation list;
//   3. a Builder edit (through cardQuestionEdits + seriesStyle, the payload
//      the save sends) shows on the node when it is reloaded;
//   4. a node edit is what the Builder reads back.
//
//   node tests/check_seriessync.mjs
import { readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".seriessync-test.cjs");
const FIXTURE = JSON.parse(readFileSync(join(ROOT, "tests", "seriessync_fixture.json"), "utf8"));

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
        export const Position = { Left: "left", Right: "right", Top: "top", Bottom: "bottom" };
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
import { GraphSeriesRows } from "${p("src/pages/Workflow.jsx")}";
import { WidgetInspector, seriesStyle, cardQuestionEdits } from "${p("src/components/widgets/builderShared.jsx")}";
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return () => act(() => root.unmount());
}
export const nodeRows = (props) => h(GraphSeriesRows, props);
export const builderPanel = (props) => h(WidgetInspector, props);
export { act, seriesStyle, cardQuestionEdits };
`;
await build({
  plugins: [shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const mod = req(OUT);

// The owner's canvas: two Data Tables (one per camera), total_panel of each
// wired into one area chart.
const COLS = [{ name: "status", type: "text" }, { name: "zone", type: "text" },
              { name: "total_panel", type: "number" }];
const TABLES = [{ key: "pcb_sensata", name: "pcb_sensata", columns: COLS }];
const GRAPH = { id: "g", type: "dashboard.graph" };
const NODES = [GRAPH,
  { id: "t1", type: "output.dataset", data: { dataset: "pcb_sensata", name: "PCB-SENSATA-CAM1" } },
  { id: "t2", type: "output.dataset", data: { dataset: "pcb_sensata", name: "PCB-SENSATA-CAM2" } }];
const EDGES = [
  { id: "w1", source: "t1", sourceHandle: "o_total_panel", target: "g", targetHandle: "in" },
  { id: "w2", source: "t2", sourceHandle: "o_total_panel", target: "g", targetHandle: "s2" }];
const REGISTRY = { "dashboard.graph": { category: "dashboard", label: "Chart" },
                   "output.dataset": { category: "output", label: "Data Table" } };
//: Since the chart builder (2026-10-13) an input's "Work out" is the chart's
//: Y calculation, asked ONCE ("Y axis" / "Value"), and an input that differs
//: keeps its own as a collapsed override ("Calculation for input N").
const CHART_Y = ["Y axis", "Value"];

/** The chart's Y and each input's override as the operator reads them:
 *  name -> the selected option's value and words, and the option list. Read
 *  off the LIVE DOM -- a select's value is a property, not markup. */
function read(el) {
  const out = {};
  for (const lab of el.querySelectorAll("label")) {
    const span = lab.querySelector(".wf-field-label, .ui-field-label");
    const name = span ? (span.childNodes[0]?.textContent || "").trim() : "";
    const sel = lab.querySelector("select");
    const key = CHART_Y.includes(name) ? "Y" : (sel?.getAttribute("aria-label") || "");
    if (!sel || !(key === "Y" || key.startsWith("Calculation for input")) || key in out) continue;
    const opt = sel.options[sel.selectedIndex];
    out[key] = { value: sel.value, words: opt ? opt.textContent : "",
                 options: [...sel.options].map((o) => `${o.value}=${o.textContent}`) };
  }
  out.ofColumn = !!el.querySelector('select[aria-label="Of column"]');
  return out;
}
function render(node) {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const unmount = mod.mount(el, node);
  const got = read(el);
  unmount();
  el.remove();
  return got;
}
const nodeView = (conf, kind = "area", s2 = { label: "CAM 2" }, extra = {}) => render(mod.nodeRows({
  node: GRAPH, nodes: NODES, edges: EDGES, registry: REGISTRY, dbTables: TABLES,
  editable: true, onChange: () => {},
  d: { kind, ...extra, series: { s1: { ...conf, label: "CAM 1", query: { range: "page" } },
                                 s2, ...(extra.series || {}) } } }));
const widgetOf = (row, kind = "area") => ({
  id: "wf_g", title: "Panels", kind, source: "history", range: "24h", span: 1,
  grid: {}, template: "plain", bands: {},
  series: [{ id: "s1", source: "dataset", dataset: "pcb_sensata", color: "",
             columns: COLS, ...row },
           { id: "s2", source: "dataset", dataset: "pcb_sensata", color: "",
             columns: COLS, label: "CAM 2", agg: "avg", field: "total_panel",
             ts_field: "", query: { agg: "avg" } }] });
const META = { sources: { stat: [] }, history: { ranges: ["24h"], intervals: [""] },
               query: {}, kinds: ["area"], catalog: [], capabilities: [] };
const builderView = (row, kind = "area") => render(mod.builderPanel({
  current: widgetOf(row, kind), patch: () => {}, meta: META }));
const show = (v) => JSON.stringify(Object.fromEntries(
  Object.entries(v).map(([k, f]) => [k, typeof f === "object" ? `${f.value} (${f.words})` : f])));
const KEYS = ["Y", "Calculation for input 1", "Calculation for input 2"];

// 1+2. Same stored inputs, same answers, both panels -- every shape, area
// first because it is the owner's chart.
for (const kind of ["area", "line", "spline", "bar", "donut", "pie", "radar"]) {
  for (const [name, conf] of Object.entries(FIXTURE.conf)) {
    const n = nodeView(conf, kind);
    const b = builderView(FIXTURE.rows[name], kind);
    const same = KEYS.every((f) => (n[f]?.value ?? null) === (b[f]?.value ?? null)
                                   && (n[f]?.words ?? null) === (b[f]?.words ?? null));
    check(`${kind}, ${name}: the node and the Builder show the same Y and overrides`,
          same && n.Y && n["Calculation for input 2"], `node    ${show(n)}\n      builder ${show(b)}`);
    if (kind === "area") {
      check(`${kind}, ${name}: same calculation list on both`,
            JSON.stringify(n.Y?.options) === JSON.stringify(b.Y?.options),
            `node    ${n.Y?.options}\n      builder ${b.Y?.options}`);
    }
  }
}
const avg = nodeView(FIXTURE.conf.avg);
check("inputs with no answer of their own read 'Average' (the compiler's guess for a number)",
      avg.Y?.value === "avg" && avg.Y?.words === "Average"
      && avg["Calculation for input 2"]?.value === "", show(avg));
check("...and no input offers a separate 'from the column type' choice",
      !avg.Y.options.some((o) => o.includes("from the column type")));
const counted = nodeView(FIXTURE.conf.count, "area", { label: "CAM 2", agg: "count" });
check("both inputs counting: 'Count records' once, for the chart, and no override",
      counted.Y?.words === "Count records" && counted["Calculation for input 1"]?.value === ""
      && counted["Calculation for input 2"]?.value === "" && !counted.ofColumn, show(counted));
const mixed = nodeView(FIXTURE.conf.count);
check("CAM 1 counting, CAM 2 averaging: the chart counts, CAM 2 overrides with Average",
      mixed.Y?.value === "count" && mixed["Calculation for input 2"]?.value === "avg",
      show(mixed));

// 3. Builder edit -> node reload: what the save sends (cardQuestionEdits and
// seriesStyle against the loaded row) is merged onto the node the way
// card-style does, and the node shows it.
{
  const loaded = widgetOf(FIXTURE.rows.avg);
  let patched = null;
  const el = document.createElement("div");
  document.body.appendChild(el);
  const unmount = mod.mount(el, mod.builderPanel({ current: loaded, meta: META,
                                                  patch: (id, p) => { patched = p; } }));
  const sel = el.querySelector('select[aria-label="Calculation"]');
  mod.act(() => {
    sel.value = "count";
    sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  unmount();
  el.remove();
  const edited = { ...loaded, ...patched };
  const edits = mod.cardQuestionEdits(loaded, edited);
  const sent = mod.seriesStyle(edited, loaded);
  const merged = {};
  for (const [sid, e] of Object.entries(sent)) {
    const out = { label: e.label };
    if (e.agg) out.agg = e.agg;
    if (e.query && (e.query.group_by || e.query.range)) {
      out.query = Object.fromEntries(Object.entries(e.query).filter(([, v]) => v));
    }
    if (typeof e.ts_field === "string") out.ts_field = e.ts_field;
    if (e.show === false) out.show = false;
    merged[sid] = out;
  }
  const reloaded = render(mod.nodeRows({
    node: GRAPH, nodes: NODES, edges: EDGES, registry: REGISTRY, dbTables: TABLES,
    editable: true, onChange: () => {}, d: { kind: "area", ...edits, series: merged } }));
  check("a Builder 'Count records' reaches the node as the chart's Y and shows there",
        edits.agg === "count" && edits.x_type === "time" && reloaded.Y?.words === "Count records",
        `${JSON.stringify(edits)} ${show(reloaded)}`);
  check("...and CAM 2, which averaged too, follows the chart (no stale override)",
        reloaded["Calculation for input 2"]?.value === "", show(reloaded));
}

// 4. Node edit -> the Builder reads it back.
let wrote = null;
{
  const el = document.createElement("div");
  document.body.appendChild(el);
  const unmount = mod.mount(el, mod.nodeRows({
    node: GRAPH, nodes: NODES, edges: EDGES, registry: REGISTRY, dbTables: TABLES,
    editable: true, onChange: (patch) => { wrote = patch; },
    d: { kind: "area", series: { s1: { label: "CAM 1" } } } }));
  const sel = el.querySelector('select[aria-label="Calculation"]');
  mod.act(() => {
    sel.value = "sum";
    sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  unmount();
  el.remove();
}
check("picking 'Sum' on the node stores it as the chart's Y, which the Builder reads as 'Sum'",
      wrote?.agg === "sum" && wrote?.x_type === "time"
      && builderView(FIXTURE.rows.sum).Y?.words === "Sum",
      JSON.stringify(wrote));

rmSync(OUT, { force: true });
if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall series-sync checks passed");
