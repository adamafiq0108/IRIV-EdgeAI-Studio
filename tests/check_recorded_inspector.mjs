// The Card and the Chart inspectors, now that they carry the Event Card's
// question -- pinned to what they OFFER, not to how they look.
//
// The Event Card left the palette and its panel moved into the Card and the
// Chart as a Source setting. What has to hold for that to be a merge rather
// than a loss:
//
//   1. a Card offers the Source switch -- the wired value, recorded events, a
//      data table -- and on recorded events it asks the Event Card's whole
//      question: count/distinct, type filter, conditions, period
//   2. the rows that only mean something on an axis (group by, time bucket,
//      one bar per) appear on a Chart and not on a one-value Card
//   3. a Card on recorded events offers the recent-events TABLE as a shape
//      and stops offering what the store cannot answer (a verdict)
//   4. the live-value controls (metric, count over) are gone while the
//      card asks recorded data, because the compile would ignore them
//   5. a plain Card, left on its wire, looks exactly as it did
//
//   node tests/check_recorded_inspector.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".recorded-inspector-test.cjs");

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

// The Inspector is internal to Workflow.jsx. Exported for this bundle only,
// by appending one line at load time -- the page itself is not changed to
// suit the test.
const exposeInspector = {
  name: "expose-inspector",
  setup(b) {
    b.onLoad({ filter: /pages[\\/]Workflow\.jsx$/ }, (args) => ({
      contents: readFileSync(args.path, "utf8") + "\nexport { Inspector };\n",
      loader: "jsx", resolveDir: dirname(args.path),
    }));
  },
};
// React Flow cannot mount outside <ReactFlow>; nothing here draws a handle.
// Same precedent as check_canvas_inspector.mjs and check_node_render.mjs.
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
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export function render(el, props) {
  const root = createRoot(el);
  act(() => { root.render(h(ToastProvider, null, h(Inspector, props))); });
  const html = el.innerHTML;
  act(() => root.unmount());
  return html;
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

// The two node specs as the server serves them -- the DATA keys matter, the
// inspector decides which rows exist from them (sub, window, unit, range).
const SOURCE_DATA = { source_mode: "", dataset: "", field: "", filters: {},
                      conditions: [], limit: 15 };
const REGISTRY = {
  "dashboard.card": { category: "dashboard", label: "Card",
    data: { name: "", metric: "", kind: "tile", unit: "", sub: "",
            window: "live", agg: "", range: "", ...SOURCE_DATA } },
  "dashboard.graph": { category: "dashboard", label: "Chart",
    data: { name: "", metric: "", kind: "area", unit: "", range: "24h",
            agg: "", group_by: "", bucket: "", series: {}, series_n: 2,
            window: "live", ...SOURCE_DATA } },
  "analytics.frame": { category: "analytics", label: "In Frame",
                       metrics: ["count"] },
  "output.dataset": { category: "output", label: "Data Table" },
};
const TABLE = { id: "t1", type: "output.dataset",
                data: { dataset: "defects", name: "Defects",
                        columns: [{ name: "defect_type", type: "text" },
                                  { name: "weight", type: "number" }] } };
const META = { variables: { event_fields: ["camera", "zone"], event_values: {},
                            tables: [] } };

function inspect(node, { nodes = [], edges = [] } = {}) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  try {
    return mod.render(box, {
      node, registry: REGISTRY, meta: META, nodes: [node, ...nodes], edges,
      editable: true, errors: [], warnings: [], onChange: () => {},
      functions: [], onDone: () => {},
    });
  } finally { document.body.removeChild(box); }
}
const card = (data) => ({ id: "c1", type: "dashboard.card",
                          data: { ...REGISTRY["dashboard.card"].data, ...data } });
const chart = (data) => ({ id: "g1", type: "dashboard.graph",
                           data: { ...REGISTRY["dashboard.graph"].data, ...data } });
const has = (html, label) => html.includes(`>${label}<`);

// 1. A CARD ON RECORDED EVENTS asks the Event Card's question.
const evCard = inspect(card({ source_mode: "events", name: "Alerts" }));
check("a Card offers the Source switch",
      has(evCard, "Source") && evCard.includes(">recorded events<"));
check("...and on recorded events asks the whole question",
      // "Work out", narrowed to what the event store answers: the same
      // field, label and words the Builders' panel uses (cardFields.jsx).
      // (Its period is the card's ONE "Date range" since 2026-10-13.)
      ["Work out", "Only events of type", "And where…", "Date range"]
        .every((f) => has(evCard, f)),
      "a row of the Event Card's question is missing");
check("...says it counts EVERY event while nothing is wired",
      evCard.includes("<strong>every</strong> recorded"));

// 2. THE AXIS ROWS belong to a chart.
check("...but a one-value Card has no axis rows",
      !has(evCard, "Group by") && !has(evCard, "Group by time"),
      "group by / bucket offered on a tile");
const evChart = inspect(chart({ source_mode: "events", kind: "bar" }));
// The chart builder (2026-10-13): what one bar is, is its X axis.
check("a Chart on recorded events asks what one bar is",
      has(evChart, "X axis") && has(evChart, "Y axis"));
check("...and draws no series list, having one question",
      !evChart.includes("Series name for input"));

// 3. THE SHAPES the source can draw.
check("a recorded-events Card can be the recent-events table",
      evCard.includes(">table - the latest rows<"));
check("...and is not offered a verdict the store cannot answer",
      !evCard.includes(">verdict - a state<"));

// 4. THE LIVE-VALUE CONTROLS go while it asks recorded data.
check("...nor the live-value rows the compile would ignore",
      !has(evCard, "Count over") && !has(evCard, "Show the value as"));
// The unit is not one of them any more: the compile carries a node's unit
// onto every card that prints a number, and the Builders offer it there too.
check("...but the Unit label stays, as on the Builder's panel",
      has(evCard, "Unit label"));

// A DATA TABLE, named on the card: offered once a table is on the canvas.
const dsChart = inspect(chart({ source_mode: "dataset", dataset: "defects",
                                kind: "donut" }), { nodes: [TABLE] });
check("a Chart can read a data table by name",
      has(dsChart, "Which table") && has(dsChart, "Category field")
      && has(dsChart, "Value"));
check("...and offers a TEXT column as its categories",
      dsChart.includes(">defect_type (text)<"));

// 5. A PLAIN CARD, on its wire, is the card it was.
const rule = { id: "r1", type: "analytics.frame", data: { name: "In frame" } };
const plain = inspect(card({ kind: "tile" }),
                      { nodes: [rule], edges: [{ id: "e", source: "r1", target: "c1" }] });
// ("Count over" is the card's one "Date range" now, with "Live value (now)"
// first -- offered only to a card showing a live rule's value.)
check("a plain Card still shows its live-value rows",
      has(plain, "Date range") && plain.includes(">Live value (now)<")
      && has(plain, "Show the value as") && !has(plain, "Count over"));
check("...offers every one-value shape",
      plain.includes(">verdict - a state<") && plain.includes(">status - an OK / ALERT pill<"));
check("...and not the recorded question",
      !has(plain, "Only events of type") && !has(plain, "And where…"));
check("...while its Source reads as the wired rule's",
      plain.includes(">the value it publishes<")
      && plain.includes(">the events it raised<"));

// -- review fixes (Yaya, 2026-09-29) ---------------------------------------
// R7. A migrated card's saved shape is shown, never replaced by "stat tile".
const gauge = inspect(card({ source_mode: "events", kind: "gauge" }));
check("R7 a recorded Card saved as a gauge shows the gauge",
      gauge.includes('<option value="gauge">gauge - as saved</option>'),
      "the select offered no option for the saved shape");
check("R7 ...and an offered shape gets no extra 'as saved' row",
      !evCard.includes("as saved"));

// R8. The axis rows appear on a Card that still carries a grouping, or that
//     feeds a Chart, so they can be seen and cleared.
const grouped = inspect(card({ source_mode: "events", bucket: "hour" }));
check("R8 a Card with a saved time bucket shows the axis rows",
      has(grouped, "Group by time") && has(grouped, "Group by"));
const feeding = inspect(card({ source_mode: "events" }), {
  nodes: [{ id: "g1", type: "dashboard.graph", data: {} }],
  edges: [{ id: "e", source: "c1", target: "g1" }] });
check("R8 a Card feeding a Chart shows the axis rows",
      has(feeding, "Group by time"));

// R3. A Chart fed by one Card on its plain port edits it as a series row.
const fedChart = inspect(chart({ kind: "bar" }), {
  nodes: [card({ source_mode: "events", name: "Alerts" })],
  edges: [{ id: "e", source: "c1", target: "g1" }] });
check("R3 a Chart fed by one Card lists it as a series",
      fedChart.includes("Series name for input 1"));

// R6. A json column is never offered as what one bar is.
const jsonTable = { ...TABLE, data: { ...TABLE.data, columns: [
  ...TABLE.data.columns, { name: "payload", type: "json" }] } };
const dsJson = inspect(chart({ source_mode: "dataset", dataset: "defects",
                               kind: "bar" }), { nodes: [jsonTable] });
check("R6 a json column is not offered as a grouping",
      !dsJson.includes(">each payload (json)<") && !dsJson.includes(">payload<"));

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall recorded-inspector checks passed");
