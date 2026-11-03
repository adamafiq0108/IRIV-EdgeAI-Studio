// A Chart node with SEVERAL inputs, as the canvas inspector shows it.
//
// The owner's pcb_sensata canvas wires the `status` column of two Data Tables
// (one per camera) into one Chart. Each fact here was wrong:
//
//   1. both rows were titled by the column, "status" and "status", so the
//      inspector (and the legend, see smoke_graphseries.py) could not say
//      which camera was which. They are named by their Data Table now, the
//      way the compiler names them;
//   2. the chart's own "one point per day" disappeared from the panel the
//      moment the second wire went on (it lived in the one-wire controls),
//      while the compiler went on ignoring it. It is the chart builder's X
//      axis = Time and Interval now (once for every input), and the
//      compiler reads it;
//   3. with a period set, a row no longer claims it is "counting each status
//      value" - the period wins, on the panel as in the compile;
//   4. what the server sends for two aligned series reaches recharts as two
//      keys per day, a zero staying a zero (not a gap).
//
//   node tests/check_graphseries.mjs
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
// esbuild, react, react-dom and jsdom are the FRONTEND's dependencies - the
// repo root has no node_modules of its own (check_canvas_inspector.mjs).
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".graphseries-test.cjs");

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

// React Flow cannot mount outside <ReactFlow>, and Workflow.jsx imports it at
// module scope; nothing here draws a handle. Same shim as
// check_canvas_inspector.mjs.
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
import { GraphSeriesRows } from "${p("src/pages/Workflow.jsx")}";
import { toRows } from "${p("src/components/charts.js")}";

export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
export const seriesRows = (props) => h(GraphSeriesRows, props);
export { act, toRows };
`;

await build({
  plugins: [shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const mod = req(OUT);

const REGISTRY = {
  "dashboard.graph": { category: "dashboard", label: "Chart" },
  "output.dataset": { category: "output", label: "Data Table" },
};
const COLS = [{ name: "event_id", type: "text" }, { name: "ts", type: "timestamp" },
              { name: "zone", type: "text" }, { name: "total_panel", type: "number" },
              { name: "status", type: "text" }];
const TABLES = [{ key: "pcb_sensata", name: "pcb_sensata", columns: COLS },
                { key: "pcb_sensata_cam2", name: "pcb_sensata_cam2", columns: COLS }];
const GRAPH = { id: "g1", type: "dashboard.graph" };
const NODES = [
  GRAPH,
  { id: "t1", type: "output.dataset",
    data: { dataset: "pcb_sensata", name: "PCB-SENSATA" } },
  { id: "t2", type: "output.dataset",
    data: { dataset: "pcb_sensata_cam2", name: "PCB-SENSATA-CAM2" } },
];
const EDGES = [
  { id: "a1", source: "t1", sourceHandle: "o_status", target: "g1", targetHandle: "in" },
  { id: "a2", source: "t2", sourceHandle: "o_status", target: "g1", targetHandle: "s2" },
];

function open(d, onChange = () => {}) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const m = mod.mount(box, mod.seriesRows({
    node: GRAPH, nodes: NODES, edges: EDGES, registry: REGISTRY,
    d, dbTables: TABLES, editable: true, onChange }));
  return { box, close: () => { m.unmount(); box.remove(); } };
}

// 1. NAMES ------------------------------------------------------------------
const sel = (box, aria) => box.querySelector(`select[aria-label="${aria}"]`);
const change = (el, value) => mod.act(() => {
  el.value = value;
  el.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
});
{
  const { box, close } = open({ kind: "bar", bucket: "day", agg: "count" });
  const titles = [...box.querySelectorAll("strong")].map((s) => s.textContent);
  check("two rows named by their Data Tables, not 'status' twice",
        titles.includes("PCB-SENSATA") && titles.includes("PCB-SENSATA-CAM2")
        && !titles.includes("status"), JSON.stringify(titles));
  const holders = [...box.querySelectorAll('input[aria-label^="Series name for input"]')]
    .map((i) => i.getAttribute("placeholder"));
  check("...and the name boxes suggest the same names",
        holders[0] === "PCB-SENSATA" && holders[1] === "PCB-SENSATA-CAM2",
        JSON.stringify(holders));

  // 2. THE CHART'S TIME AXIS (the chart builder: X axis = Time, Interval) --
  check("a several-input chart shows its time axis, once for every input",
        sel(box, "X axis type")?.value === "time" && !!sel(box, "Interval")
        && box.querySelectorAll('select[aria-label="Interval"]').length === 1);
  // "day" is stored; the shared step reads it as "1d", the Day preset.
  check("...set to the period the chart already holds",
        sel(box, "Interval").value === "day", sel(box, "Interval").value);

  // 3. THE PERIOD WINS OVER COUNTING EACH WORD ------------------------------
  check("with a period set, X is time - no input claims 'each status value'",
        !sel(box, "X column") && !box.textContent.includes("One point per"));
  close();
}
{
  let wrote = null;
  const { box, close } = open({ kind: "bar", bucket: "day" }, (patch) => { wrote = patch; });
  change(sel(box, "Interval"), "week");
  check("picking Week writes the daily view of the last 7 days on the node",
        wrote && wrote.bucket === "1d" && wrote.range === "7d" && wrote.time_interval === "week",
        JSON.stringify(wrote));
  close();
  const again = open({ kind: "bar", bucket: "1d", range: "7d", time_interval: "week",
                       x_type: "time" }, (patch) => { wrote = patch; });
  change(sel(again.box, "Show"), "alt");
  check("...and 'Each week' writes one point per calendar week",
        wrote && wrote.bucket === "week" && wrote.range === "90d", JSON.stringify(wrote));
  again.close();
}
{
  // No period: the old several-input bar chart, counting each word -- read
  // by the chart builder as X = that column.
  const { box, close } = open({ kind: "bar" });
  // A bar's column X is its "Category field" since the chart type picker.
  check("without a period a word column is still counted per value (X = status)",
        sel(box, "X axis type")?.value === "column" && sel(box, "Category field")?.value === "status"
        && sel(box, "Calculation")?.value === "count",
        sel(box, "Category field")?.value);
  close();
}
{
  // The owner's two-camera chart, with colours the Builder pinned: the node
  // shows the rows and NO colour control (owner, 2026-10-12: a chart's
  // colours are set in the Analytics or Dashboard Builder only)...
  let wrote = null;
  const { box, close } = open({ kind: "spline",
                                series: { s1: { color: "#ff8800" },
                                          s2: { color: "#0088ff", label: "Cam 2" } } },
                              (patch) => { wrote = patch; });
  check("a several-input chart's node has no colour swatch or palette pin",
        box.querySelectorAll('input[type="color"]').length === 0
        && !box.textContent.includes("Palette colour"),
        box.querySelectorAll('input[type="color"]').length + " swatches");
  check("...and says once where the colours are set",
        box.textContent.split("Colours are set in the Analytics or Dashboard Builder.")
          .length === 2);
  // ...and an edit it does make carries the pinned colours along untouched.
  const name = box.querySelector('input[aria-label="Series name for input 1"]');
  const set = Object.getOwnPropertyDescriptor(
    dom.window.HTMLInputElement.prototype, "value").set;
  mod.act(() => {
    set.call(name, "Cam 1");
    name.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  check("naming a line on the node keeps every Builder colour",
        wrote && wrote.series.s1.label === "Cam 1"
        && wrote.series.s1.color === "#ff8800"
        && wrote.series.s2.color === "#0088ff", JSON.stringify(wrote));
  // ...and so does a chart-builder edit, which rewrites the inputs' entries.
  change(sel(box, "Interval"), "day");
  check("a chart-builder edit keeps every Builder colour and name too",
        wrote && wrote.series.s1.color === "#ff8800" && wrote.series.s2.color === "#0088ff"
        && wrote.series.s2.label === "Cam 2", JSON.stringify(wrote));
  close();
}

// 4. WHAT REACHES RECHARTS ---------------------------------------------------
// The server's shape for two aligned series: one key per series per day, a
// zero kept as 0 (a day the camera recorded nothing), labels from series 1
// AND series 2's days alike.
const { rows, series } = mod.toRows({
  labels: ["10-08", "10-09", "10-10", "10-11", "10-12"],
  datasets: [{ id: "s1", label: "PCB-SENSATA", values: [3, 1, 4, 1, 5] },
             { id: "s2", label: "PCB-SENSATA-CAM2", values: [0, 0, 2, 6, 0] }],
}, "Total Violation", "#04bade");
check("two series, two legend names",
      series.map((s) => s.label).join("|") === "PCB-SENSATA|PCB-SENSATA-CAM2");
check("each day carries both cameras, zero kept as zero",
      rows.length === 5 && rows[0].s0 === 3 && rows[0].s1 === 0
      && rows[3].s1 === 6 && rows[4].s1 === 0, JSON.stringify(rows));
check("...and two different colours",
      series[0].color !== series[1].color);

rmSync(OUT, { force: true });
console.log(bad ? `\n${bad} failure(s)` : "\nall graph-series inspector checks passed");
process.exit(bad ? 1 : 0);
