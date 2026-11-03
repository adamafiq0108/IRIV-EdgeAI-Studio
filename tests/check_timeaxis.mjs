// A chart's time axis in the browser: the shared "Group by time" and "Time
// range" controls, and the axis labels drawn in the viewer's own clock.
//
// The server half (the step grammar, the window in the query, the buckets,
// widening) is tests/smoke_timeaxis.py. Pinned here:
//
//   1. the node inspector and the Builder draw the SAME two controls, same
//      options in the same order, same stored values;
//   2. "Custom..." reveals a number and a unit, stores "90m"/"2d", and
//      refuses 0, fractions and more than 90 days without storing them;
//   3. "Pick dates..." reveals a start and an end;
//   4. axis labels follow the step: 14:30 within a day, 13 Oct 14:30 across
//      days, 13 Oct for days, Oct 2026 for months -- and the tooltip names
//      the whole bucket.
//
//   node tests/check_timeaxis.mjs
import { readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".timeaxis-test.cjs");

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
    b.onResolve({ filter: /^@xyflow\/react$/ }, () => ({ path: "xyflow-shim", namespace: "shim" }));
    b.onResolve({ filter: /^@xyflow\/react\/dist\/style\.css$/ }, () => ({ path: "xyflow-css", namespace: "shim" }));
    b.onLoad({ filter: /^xyflow-css$/, namespace: "shim" }, () => ({ contents: "", resolveDir: FRONT, loader: "js" }));
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
import { Inspector } from "${p("src/pages/Workflow.jsx")}";
import { WidgetInspector } from "${p("src/components/widgets/builderShared.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { StepField, RangeField, STEP_PRESETS, RANGE_PRESETS, cleanStep, customStep }
  from "${p("src/components/widgets/cardFields.jsx")}";
export { axisLabel, bucketTitle, toRows } from "${p("src/components/charts.js")}";
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(h(ToastProvider, null, node)); });
  return () => act(() => root.unmount());
}
export const nodePanel = (props) => h(Inspector, props);
export const builderPanel = (props) => h(WidgetInspector, props);
export { act, h };
`;
await build({
  plugins: [exposeInspector, shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const mod = req(OUT);

/** Mount, hand the live element to `fn`, unmount. */
function within(node, fn) {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const unmount = mod.mount(el, node);
  try { return fn(el); } finally { unmount(); el.remove(); }
}
const fieldSelect = (el, label) => [...el.querySelectorAll("label")].find((l) =>
  (l.querySelector(".wf-field-label, .ui-field-label")?.childNodes[0]?.textContent || "")
    .trim() === label)?.querySelector("select");
const options = (sel) => (sel ? [...sel.options].map((o) => `${o.value}=${o.textContent}`) : null);
const fire = (target, value, type = "change") => mod.act(() => {
  const proto = target.tagName === "SELECT" ? window.HTMLSelectElement.prototype
    : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(target, value);
  target.dispatchEvent(new window.Event(type === "input" ? "input" : "change", { bubbles: true }));
});

// -- 1. the same two controls on the node and in the Builder ------------------
const REGISTRY = {
  "dashboard.graph": { category: "dashboard", label: "Chart",
    data: { name: "", metric: "", kind: "area", unit: "", range: "24h", agg: "",
            group_by: "", bucket: "", series: {}, series_n: 2, window: "live",
            source_mode: "", dataset: "", field: "", filters: {}, conditions: [],
            limit: 15 } },
  "analytics.frame": { category: "analytics", label: "In Frame", metrics: ["count"] },
};
const rule = { id: "r1", type: "analytics.frame", data: { name: "In frame" } };
const chart = { id: "g1", type: "dashboard.graph",
                data: { ...REGISTRY["dashboard.graph"].data, name: "Trend", kind: "area",
                        range: "6h", bucket: "30m" } };
const nodeSide = within(mod.nodePanel({
  node: chart, registry: REGISTRY, meta: { variables: { tables: [] } },
  nodes: [chart, rule], edges: [{ id: "e", source: "r1", target: "g1" }],
  editable: true, errors: [], warnings: [], onChange: () => {}, functions: [],
  onDone: () => {} }), (el) => ({
  step: fieldSelect(el, "Group by"), range: fieldSelect(el, "Date range"),
  stepOpts: options(fieldSelect(el, "Group by")),
  rangeOpts: options(fieldSelect(el, "Date range")),
  stepValue: fieldSelect(el, "Group by")?.value,
  rangeValue: fieldSelect(el, "Date range")?.value }));
const builderSide = within(mod.builderPanel({
  current: { id: "wf_g1", title: "Trend", kind: "area", source: "history", key: "fn_x",
             range: "6h", interval: "30m", span: 1, grid: {}, template: "plain", bands: {} },
  meta: { sources: { stat: ["fn_x"] }, history: { ranges: [], intervals: [] },
          query: {}, kinds: ["area"], catalog: [], capabilities: [] },
  patch: () => {} }), (el) => ({
  stepOpts: options(fieldSelect(el, "Group by")),
  rangeOpts: options(fieldSelect(el, "Date range")),
  stepValue: fieldSelect(el, "Group by")?.value,
  rangeValue: fieldSelect(el, "Date range")?.value }));
// Since the chart builder (2026-10-13) the step is the X axis's Interval:
// a stored 30 minutes reads as Custom, which shows the full step list
// ("Group by") beside the one "Date range".
check("the node and the Builder offer the same 'Group by time' list",
      nodeSide.stepOpts && JSON.stringify(nodeSide.stepOpts) === JSON.stringify(builderSide.stepOpts),
      `node    ${nodeSide.stepOpts}\n      builder ${builderSide.stepOpts}`);
check("...and the same 'Time range' list",
      nodeSide.rangeOpts && JSON.stringify(nodeSide.rangeOpts) === JSON.stringify(builderSide.rangeOpts),
      `node    ${nodeSide.rangeOpts}\n      builder ${builderSide.rangeOpts}`);
check("...reading the same stored step and range",
      nodeSide.stepValue === "30m" && builderSide.stepValue === "30m"
      && nodeSide.rangeValue === "6h" && builderSide.rangeValue === "6h",
      JSON.stringify([nodeSide.stepValue, builderSide.stepValue, nodeSide.rangeValue, builderSide.rangeValue]));
check("the step list is the owner's: every 1/30/60 minutes, 6/12/24 hours, day, week, month, custom",
      ["1m=Every minute", "30m=Every 30 minutes", "1h=Every hour", "6h=Every 6 hours",
       "12h=Every 12 hours", "1d=Every day", "week=Every week", "month=Every month",
       "custom=Custom...", "=Automatic"].every((o) => builderSide.stepOpts.includes(o)),
      builderSide.stepOpts);
check("the range list is one quick pick, plain words, with the date picker and exact dates",
      ["3h=Last 3 hours", "12h=Last 12 hours", "24h=Last 24 hours", "7d=Past week",
       "30d=Past month", "90d=Past 3 months", "180d=Past 6 months", "365d=Past year",
       "page=Follow the Analytics date picker", "custom=Pick dates..."]
        .every((o) => builderSide.rangeOpts.includes(o)), builderSide.rangeOpts);

// -- 1b. an X FIELD chosen wins over the time grouping ----------------------------
// ("One point per <column>", now the chart builder's X axis = Database column):
// the field stays the selection and the time settings are not shown at all;
// with no field the stored step is the X axis's Interval. The node and the
// Builder agree.
const TABLE = { id: "t1", type: "output.dataset",
                data: { dataset: "pcb", name: "pcb",
                        columns: [{ name: "status", type: "text" },
                                  { name: "shift_day", type: "datetime" },
                                  { name: "total_panel", type: "number" }] } };
const REG2 = { ...REGISTRY, "output.dataset": { category: "output", label: "Data Table" } };
for (const [x, locked] of [["status", true], ["shift_day", true], ["", false]]) {
  const dsChart = { id: "g2", type: "dashboard.graph",
                    data: { ...REGISTRY["dashboard.graph"].data, name: "By X", kind: "bar",
                            source_mode: "dataset", dataset: "pcb", agg: "count",
                            group_by: x, bucket: "1d" } };
  const view = (el) => ({
    xType: fieldSelect(el, "X axis")?.value,
    // A bar's column X is its "Category field" (chart type picker, 2026-10-14).
    x: fieldSelect(el, "Category field")?.value ?? "",
    interval: fieldSelect(el, "Interval")?.value ?? null });
  const nodeView = within(mod.nodePanel({
    node: dsChart, registry: REG2, meta: { variables: { tables: [] } },
    nodes: [dsChart, TABLE], edges: [], editable: true, errors: [], warnings: [],
    onChange: () => {}, functions: [], onDone: () => {} }), view);
  const builderView = within(mod.builderPanel({
    current: { id: "wf_g2", title: "By X", kind: "bar", source: "dataset", dataset: "pcb",
               columns: TABLE.data.columns, span: 1, grid: {}, template: "plain", bands: {},
               query: { agg: "count", group_by: x, bucket: "1d" } },
    meta: { sources: { stat: [] }, history: { ranges: [], intervals: [] }, query: {},
            kinds: ["bar"], catalog: [], capabilities: [] },
    patch: () => {} }), view);
  const name = x || "no field";
  check(`X = ${name}: the node ${locked ? "keeps the column and hides the time settings"
                                        : "groups by its step"}`,
        locked ? nodeView.xType === "column" && nodeView.x === x && nodeView.interval === null
               : nodeView.xType === "time" && nodeView.interval === "day",
        JSON.stringify(nodeView));
  check(`X = ${name}: the Builder shows exactly the same`,
        JSON.stringify(builderView) === JSON.stringify(nodeView),
        `node ${JSON.stringify(nodeView)} builder ${JSON.stringify(builderView)}`);
}

// -- 2. Custom... ---------------------------------------------------------------
{
  const wrote = [];
  within(mod.h(mod.StepField, { value: "", onChange: (v) => wrote.push(v) }), (el) => {
    const sel = el.querySelector("select");
    fire(sel, "custom");
    const num = el.querySelector('input[aria-label="Every how many"]');
    const unit = el.querySelector('select[aria-label="Unit"]');
    check("'Custom...' reveals a number and a unit", !!num && !!unit);
    fire(num, "90", "input");
    check("90 minutes is stored as 90m", wrote[wrote.length - 1] === "90m", JSON.stringify(wrote));
    fire(unit, "d");
    fire(num, "2", "input");
    check("2 days is stored as 2d", wrote[wrote.length - 1] === "2d", JSON.stringify(wrote));
    const before = wrote.length;
    fire(num, "0", "input");
    check("0 is refused, said, and not stored",
          wrote.length === before && /whole number/i.test(el.textContent), JSON.stringify(wrote));
    fire(num, "91", "input");
    check("91 days is refused, said, and not stored",
          wrote.length === before && /90 days/.test(el.textContent), JSON.stringify(wrote));
  });
  check("the validators agree: 1.5 hours is not a whole number",
        !!mod.customStep("1.5", "h").error && mod.cleanStep("1.5h") === null
        && mod.cleanStep("hour") === "1h" && mod.cleanStep("0m") === null);
  within(mod.h(mod.StepField, { value: "90m", onChange: () => {} }), (el) => {
    check("a stored custom step opens as that custom step",
          el.querySelector("select").value === "custom"
          && el.querySelector('input[aria-label="Every how many"]').value === "90");
  });
}

// -- 3. Pick dates... ---------------------------------------------------------------
{
  let wrote = null;
  within(mod.h(mod.RangeField, { value: "custom", from: "", to: "",
                                 onChange: (r) => { wrote = r; } }), (el) => {
    const from = el.querySelector('input[aria-label="From"]');
    const to = el.querySelector('input[aria-label="To"]');
    check("'Pick dates...' reveals a start and an end", !!from && !!to && from.type === "datetime-local");
    fire(from, "2026-10-01T08:00", "input");
    check("...stored as range_from on the custom range",
          wrote && wrote.range === "custom" && wrote.range_from === "2026-10-01T08:00",
          JSON.stringify(wrote));
  });
}

// -- 4. axis labels -----------------------------------------------------------------
const at = (y, mo, d, h = 0, mi = 0) => new Date(y, mo - 1, d, h, mi).getTime();
check("within one day, a sub-day step reads HH:mm",
      mod.axisLabel(at(2026, 10, 13, 14, 30), 1800, { oneDay: true }) === "14:30");
check("across days it reads 13 Oct 14:30",
      mod.axisLabel(at(2026, 10, 13, 14, 30), 21600, { oneDay: false }) === "13 Oct 14:30");
check("a day step reads 13 Oct", mod.axisLabel(at(2026, 10, 13), 86400) === "13 Oct");
check("a month step reads Oct 2026", mod.axisLabel(at(2026, 10, 1), 30 * 86400) === "Oct 2026");
check("the tooltip names the whole bucket",
      mod.bucketTitle(at(2026, 10, 13, 12), 21600) === "13 Oct 2026 12:00 - 18:00",
      mod.bucketTitle(at(2026, 10, 13, 12), 21600));
const keys = [at(2026, 10, 13, 0), at(2026, 10, 13, 6), at(2026, 10, 13, 12)];
const { rows } = mod.toRows({ labels: ["a", "b", "c"], keys, step_s: 21600,
                               values: [1, 0, 2] }, "x", "#000");
check("a time payload is labelled in the browser's clock, the server's labels ignored",
      rows.map((r) => r.x).join(",") === "00:00,06:00,12:00" && rows[1].span.includes("06:00 - 12:00"),
      JSON.stringify(rows));
const legacy = mod.toRows({ labels: ["10-13", "10-14"], values: [1, 2] }, "x", "#000");
check("an older payload with no keys keeps its own labels",
      legacy.rows.map((r) => r.x).join(",") === "10-13,10-14");

rmSync(OUT, { force: true });
if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall time-axis checks passed");
