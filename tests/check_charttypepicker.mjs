// The chart type picker (Tok Aba, 2026-10-14, mockups 59-64), rendered for
// real on the workflow node Inspector and the Builders' WidgetInspector:
//
//   1. old kinds map to the right card (line/spline, area, bar by its X,
//      pie, donut, radar); Scatter is listed but cannot be chosen
//   2. a NEW chart opens on the picker; the filters, the radio-group keys,
//      the footer and Continue; Continue writes the chosen type
//   3. an EXISTING chart opens straight on its properties and writes nothing
//   4. per-type panels contain / omit the right sections
//   5. "Change chart type" keeps every compatible field and says, before it
//      happens, what does not carry over
//   6. "Show as" inside a family, and Smooth line, write the kind alone
//   7. the same picker in the Builders: the dragged kind preselected
//
//   node tests/check_charttypepicker.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".charttypepicker-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
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
import { Inspector } from "${p("src/pages/Workflow.jsx")}";
import { WidgetInspector, newWidget } from "${p("src/components/widgets/builderShared.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export * as CT from "${p("src/components/widgets/chartTypes.js")}";
export { newWidget };
export function live(el, comp, props) {
  const root = createRoot(el);
  const C = comp === "node" ? Inspector : WidgetInspector;
  const render = (next) => act(() => { root.render(h(ToastProvider, null, h(C, next))); });
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
const CT = mod.CT;

// -- fixtures (the owner's two cameras, as check_chartbuilder) -----------------
const COLS = [{ name: "object_id", type: "text" }, { name: "status", type: "text" },
              { name: "total_panel", type: "number" }, { name: "made_on", type: "datetime" }];
const TABLES = [{ key: "cam1", name: "PCB-SENSATA-CAM1", columns: COLS },
                { key: "cam2", name: "PCB-SENSATA-CAM2", columns: COLS }];
const GRAPH_DATA = { name: "Total Violation", metric: "", kind: "area", unit: "", range: "24h",
                     agg: "", group_by: "", bucket: "", series: {}, series_n: 2,
                     window: "live", source_mode: "", dataset: "", field: "", filters: {},
                     conditions: [], limit: 15 };
const REGISTRY = {
  "dashboard.graph": { category: "dashboard", label: "Chart", data: GRAPH_DATA },
  "output.dataset": { category: "output", label: "Data Table" },
};
const T1 = { id: "t1", type: "output.dataset",
             data: { dataset: "cam1", name: "PCB-SENSATA-CAM1", columns: COLS } };
const T2 = { id: "t2", type: "output.dataset",
             data: { dataset: "cam2", name: "PCB-SENSATA-CAM2", columns: COLS } };
const wire = (src, col, handle) => ({ id: `w${src}${col}`, source: src, sourceHandle: `o_${col}`,
                                      target: "g1", targetHandle: handle });
const TWO = [wire("t1", "object_id", "in"), wire("t2", "object_id", "s2")];
const META = { variables: { event_fields: [], event_values: {}, tables: TABLES } };
const graph = (data, id = "g1") => ({ id, type: "dashboard.graph", data: { ...GRAPH_DATA, ...data } });

function openNode(n, onChange = () => {}, edges = TWO) {
  const box = document.body.appendChild(document.createElement("div"));
  const props = { node: n, registry: REGISTRY, meta: META, nodes: [n, T1, T2], edges,
                  editable: true, errors: [], warnings: [], onChange, functions: [],
                  onDone: () => {} };
  const m = mod.live(box, "node", props);
  return { box, m, props, close: () => { m.unmount(); box.remove(); } };
}
const BUILDER_META = {
  sources: { stat: ["fn_x_count"], bucket: ["fn_day"], events: [] },
  history: { aggs: ["avg", "sum", "min", "max", "last", "count"] },
  query: { aggregations: ["count"], columns: [] },
  kinds: ["tile", "line", "bar", "area", "spline", "donut", "pie", "radar", "gauge", "table"],
  capabilities: [], catalog: TABLES.map((t) => ({ dataset: t.key, columns: t.columns })),
};
function openWidget(w, patch = () => {}) {
  const box = document.body.appendChild(document.createElement("div"));
  const m = mod.live(box, "widget", { current: w, meta: BUILDER_META, patch });
  return { box, m, close: () => { m.unmount(); box.remove(); } };
}
const picker = (box) => box.querySelector('[data-testid="chart-type-picker"]');
const cards = (box) => [...box.querySelectorAll('[role="radio"]')];
const card = (box, id) => box.querySelector(`[role="radio"][data-type="${id}"]`);
const checked = (box) => cards(box).filter((c) => c.getAttribute("aria-checked") === "true")
  .map((c) => c.dataset.type);
const button = (box, text) => [...box.querySelectorAll("button")].find((b) => b.textContent.trim() === text
  || b.textContent.trim().startsWith(text));
const click = (m, el) => m.act(() => { el.click(); });
const key = (m, el, k) => m.act(() => {
  el.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: k, bubbles: true }));
});
function choose(m, sel, value) {
  m.act(() => { sel.value = value; sel.dispatchEvent(new dom.window.Event("change", { bubbles: true })); });
}
/** Labels of every field in the chart builder, in order. */
function fieldLabels(box) {
  const root = box.querySelector(".chart-builder");
  if (!root) return [];
  return [...root.querySelectorAll("label.wf-field, label.ui-field")].map((el) => (
    el.querySelector(".wf-field-label, .ui-field-label")?.childNodes[0]?.textContent || "").trim());
}
const strip = (box) => box.querySelector('[data-testid="chart-type-strip"]');

// -- 1. old kinds map to the right card ------------------------------------------
check("line and spline are a Line Chart, area an Area Chart",
      CT.chartTypeOf("line", "time") === "line" && CT.chartTypeOf("spline", "time") === "line"
      && CT.chartTypeOf("area", "time") === "area" && CT.chartTypeOf("line", "column") === "line");
check("a bar with Time on X is a Time-based Bar, with a column a Comparison Bar",
      CT.chartTypeOf("bar", "time") === "time_bar" && CT.chartTypeOf("bar", "column") === "compare_bar");
check("pie, donut and radar are themselves; a card kind is no chart type",
      CT.chartTypeOf("pie", "column") === "pie" && CT.chartTypeOf("donut") === "donut"
      && CT.chartTypeOf("radar") === "radar" && CT.chartTypeOf("tile") === "");
check("Scatter Plot is listed under Relationships and cannot be chosen",
      CT.chartType("scatter").family === "relationships" && !CT.selectable(CT.chartType("scatter"))
      && CT.SCATTER_READY === false);
check("Radar sits under Comparisons", CT.chartType("radar").family === "comparisons");
// Radar is "Coming soon" too (Tok Aba, 2026-10-14): not choosable, while a
// chart saved as a radar keeps opening and editing as one.
check("Radar is 'Coming soon': listed, cannot be chosen, and no switch can reach it",
      !CT.selectable(CT.chartType("radar")) && CT.RADAR_READY === false
      && CT.planTypeSwitch("bar", null, "radar") === null);
for (const [what, data, edges, want] of [
  ["legacy line", { kind: "line", bucket: "1h" }, TWO, "Line Chart (Time Series)"],
  ["legacy spline", { kind: "spline" }, TWO, "Line Chart (Time Series)"],
  ["legacy area", { kind: "area" }, TWO, "Area Chart (Time Series)"],
  ["legacy bar per day", { kind: "bar", bucket: "1d", range: "7d" }, TWO, "Time-based Bar Chart"],
  ["legacy bar of a word column", { kind: "bar" }, TWO, "Comparison Bar Chart"],
  ["model bar, X = Time", { kind: "bar", x_type: "time", bucket: "1h", time_interval: "hour" }, TWO,
   "Time-based Bar Chart"],
  ["legacy pie", { kind: "pie" }, TWO, "Pie Chart"],
  ["legacy donut", { kind: "donut" }, TWO, "Donut Chart"],
  ["legacy radar", { kind: "radar" }, TWO, "Radar Chart"],
  ["an unwired bar", { kind: "bar" }, [], "Comparison Bar Chart"],
]) {
  let wrote = null;
  const o = openNode(graph(data), (pt) => { wrote = pt; }, edges);
  const s = strip(o.box);
  check(`${what}: opens on its properties as "${want}", writes nothing`,
        !picker(o.box) && s && s.textContent.includes(want) && wrote === null,
        `${s && s.textContent} / wrote ${JSON.stringify(wrote)}`);
  o.close();
}
{
  // A chart SAVED as a radar, while Radar is "Coming soon": it opens on its
  // properties, says Radar Chart, keeps Radar in its Show as, and still edits.
  let wrote = null;
  const o = openNode(graph({ kind: "radar", series: { s1: { label: "A" } } }), (pt) => { wrote = pt; });
  const sel = o.box.querySelector('select[aria-label="Show as"]');
  check("a saved radar opens as a Radar Chart, writing nothing, Radar still its Show as",
        !picker(o.box) && strip(o.box)?.textContent.includes("Radar Chart") && wrote === null
        && sel.value === "radar" && [...sel.options].map((x) => x.value).join() === "compare_bar,radar");
  o.m.act(() => {
    const c = o.box.querySelector('select[aria-label="Calculation"]');
    c.value = "sum"; c.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  });
  check("...and edits as before (its Value calculation saves, kind untouched)",
        wrote && wrote.agg === "sum" && !("kind" in wrote), JSON.stringify(wrote));
  click(o.m, button(o.box, "Change chart type"));
  check("...Change chart type opens on Radar (current), greyed, with Cancel",
        !!picker(o.box) && card(o.box, "radar").getAttribute("aria-disabled") === "true"
        && button(o.box, "Cancel"));
  o.close();
}
{
  const o = openNode(graph({ kind: "spline" }));
  check("...a spline shows Smooth line ticked",
        o.box.querySelector('input[aria-label="Smooth line"]')?.checked === true);
  o.close();
}

// -- 2. a NEW chart opens on the picker -------------------------------------------
{
  CT.markFreshChart("g9");
  let wrote = null;
  const n = graph({}, "g9");
  const o = openNode(n, (pt) => { wrote = pt; }, []);
  const box = o.box;
  check("a chart just dropped opens on 'Choose a chart type'",
        !!picker(box) && box.textContent.includes("Choose a chart type")
        && box.textContent.includes("What do you want to understand from your data?")
        && !box.querySelector(".chart-builder") && !strip(box));
  check("...eight cards, a radio group, nothing chosen yet, Continue disabled",
        cards(box).length === 8 && box.querySelector('[role="radiogroup"]')
        && checked(box).length === 0 && button(box, "Continue").disabled);
  const names = cards(box).map((c) => c.querySelector("strong").textContent);
  check("...in the mockup's names",
        JSON.stringify(names) === JSON.stringify(["Line Chart", "Area Chart", "Time-based Bar",
          "Comparison Bar", "Pie Chart", "Donut Chart", "Radar Chart", "Scatter Plot"]),
        JSON.stringify(names));
  check("...each with a drawing, a description and a 'Best for' tag",
        cards(box).every((c) => c.querySelector("svg") && c.querySelector(".ctp-desc")
                          && /^Best for /.test(c.querySelector(".ctp-best").textContent)));
  const pills = [...box.querySelectorAll(".ctp-pill")];
  check("filter pills: All charts, Trends over time, Comparisons, Proportions, Relationships",
        JSON.stringify(pills.map((x) => x.textContent)) === JSON.stringify(["All charts",
          "Trends over time", "Comparisons", "Proportions", "Relationships"]));
  const shown = (pill) => { click(o.m, pills.find((x) => x.textContent === pill));
                            return cards(box).map((c) => c.dataset.type).join(","); };
  check("Trends over time: Line, Area, Time-based Bar", shown("Trends over time") === "line,area,time_bar");
  check("Comparisons: Comparison Bar, Radar", shown("Comparisons") === "compare_bar,radar");
  check("Proportions: Pie, Donut", shown("Proportions") === "pie,donut");
  check("Relationships: Scatter, greyed 'Coming soon'",
        shown("Relationships") === "scatter"
        && card(box, "scatter").getAttribute("aria-disabled") === "true"
        && card(box, "scatter").textContent.includes("Coming soon"));
  click(o.m, card(box, "scatter"));
  check("...clicking Scatter selects nothing; Continue stays disabled",
        checked(box).length === 0 && button(box, "Continue").disabled);
  shown("All charts");
  click(o.m, card(box, "line"));
  check("clicking a card checks it, the footer says what next",
        JSON.stringify(checked(box)) === '["line"]' && box.textContent.includes("Selected: Line Chart")
        && box.textContent.includes("Example use:") && box.textContent.includes("Next, ")
        && button(box, "Continue with Line Chart") && !button(box, "Continue with Line Chart").disabled);
  check("...one tab stop: the checked card",
        cards(box).filter((c) => c.tabIndex === 0).map((c) => c.dataset.type).join() === "line");
  key(o.m, card(box, "line"), "ArrowRight");
  check("ArrowRight moves the choice to the next card", JSON.stringify(checked(box)) === '["area"]');
  key(o.m, card(box, "area"), "End");
  check("End goes to the last card that can be chosen (Donut, not Radar or Scatter)",
        JSON.stringify(checked(box)) === '["donut"]');
  key(o.m, card(box, "donut"), "ArrowRight");
  check("...and wraps past Radar and Scatter to the first", JSON.stringify(checked(box)) === '["line"]');
  click(o.m, card(box, "radar"));
  check("clicking Radar selects nothing; it is greyed 'Coming soon' under Comparisons too",
        JSON.stringify(checked(box)) === '["line"]'
        && card(box, "radar").getAttribute("aria-disabled") === "true"
        && card(box, "radar").textContent.includes("Coming soon") && card(box, "radar").tabIndex === -1);
  click(o.m, card(box, "pie"));
  check("no Cancel on a new chart (it has no type to go back to)", !button(box, "Cancel"));
  click(o.m, button(box, "Continue with Pie Chart"));
  check("Continue with Pie Chart writes kind pie and its model (Category X, all time)",
        wrote && wrote.kind === "pie" && wrote.x_type === "column" && wrote.group_by === ""
        && wrote.range === "" && wrote.bucket === "", JSON.stringify(wrote));
  const after = graph({ ...n.data, ...wrote }, "g9");
  o.m.render({ ...o.props, node: after, nodes: [after, T1, T2] });
  check("...and the chart now opens on the Pie Chart properties",
        !picker(box) && strip(box)?.textContent.includes("Pie Chart") && !CT.isFreshChart("g9"));
  o.close();
}
{
  // A new chart keeping the kind it was dropped with writes nothing at all.
  CT.markFreshChart("g8");
  let wrote = null;
  const o = openNode(graph({}, "g8"), (pt) => { wrote = pt; }, []);
  click(o.m, card(o.box, "area"));
  click(o.m, button(o.box, "Continue with Area Chart"));
  check("a new chart continued as its own kind (Area) writes nothing and shows its properties",
        wrote === null && !picker(o.box) && strip(o.box)?.textContent.includes("Area Chart"));
  o.close();
  CT.markFreshChart("g7");
  wrote = null;
  const t = openNode(graph({}, "g7"), (pt) => { wrote = pt; }, []);
  click(t.m, card(t.box, "time_bar"));
  click(t.m, button(t.box, "Continue with Time-based Bar"));
  check("a new Time-based Bar is a bar with Time on X (Day preset)",
        wrote && wrote.kind === "bar" && wrote.x_type === "time" && wrote.time_interval === "day"
        && wrote.bucket === "1d" && wrote.range === "7d", JSON.stringify(wrote));
  t.close();
}

// -- 4. per-type panels ---------------------------------------------------------
const TIME_NUM = [wire("t1", "total_panel", "in"), wire("t2", "total_panel", "s2")];
for (const [id, data, edges, want, omit] of [
  ["line", { kind: "line", x_type: "time", bucket: "1d", time_interval: "day", range: "page" },
   TIME_NUM, ["X axis", "Interval", "Date range", "Y axis"], ["Category field", "Value"]],
  ["area", { kind: "area", x_type: "time", bucket: "1d", time_interval: "day", range: "page" },
   TIME_NUM, ["X axis", "Interval", "Date range", "Y axis"], ["Category field", "Value"]],
  ["time_bar", { kind: "bar", x_type: "time", bucket: "1d", time_interval: "day", range: "page" },
   TIME_NUM, ["X axis", "Interval", "Date range", "Y axis"], ["Category field", "Value"]],
  ["compare_bar", { kind: "bar", x_type: "column", group_by: "", range: "page", agg: "count" },
   TWO, ["X axis", "Category field", "Date range", "Y axis"], ["Interval", "Value"]],
  ["pie", { kind: "pie", x_type: "column", group_by: "status", range: "page", agg: "count" },
   TWO, ["Category field", "Value", "Date range"], ["X axis", "Y axis", "Interval"]],
  ["donut", { kind: "donut", x_type: "column", group_by: "status", range: "page", agg: "count" },
   TWO, ["Category field", "Value", "Date range"], ["X axis", "Y axis", "Interval"]],
]) {
  const o = openNode(graph(data), () => {}, edges);
  const l = fieldLabels(o.box);
  const s = strip(o.box);
  check(`${id}: strip "${CT.chartType(id).heading}" with Change chart type`,
        s && s.textContent.includes(CT.chartType(id).heading)
        && s.textContent.includes(CT.chartType(id).panel) && button(s, "Change chart type"));
  check(`${id}: shows ${want.join(", ")} in that order`,
        JSON.stringify(l.filter((x) => want.includes(x))) === JSON.stringify(want), JSON.stringify(l));
  check(`${id}: omits ${omit.join(", ")}`, omit.every((x) => !l.includes(x)), JSON.stringify(l));
  const adv = o.box.querySelector("details.chart-advanced");
  // Thresholds are drawn on axes only (BAND_KINDS): a pie or donut never
  // had them, and its Advanced keeps the Date column.
  const bands = ["pie", "donut"].includes(id) ? !adv.textContent.includes("Threshold")
    : adv.textContent.includes("Threshold");
  check(`${id}: Advanced settings collapsed${["pie", "donut"].includes(id) ? "" : ", thresholds in it"}; inputs, unit, colours note`,
        adv && !adv.open && bands
        && o.box.querySelectorAll('input[aria-label^="Series name for input"]').length === 2
        && o.box.querySelectorAll('input[aria-label^="Show input"]').length === 2
        && o.box.querySelectorAll("details.chart-override").length === 2
        && o.box.textContent.includes("Unit label")
        && o.box.textContent.includes("Colours are set in the Analytics or Dashboard Builder."));
  if (id === "time_bar" || id === "compare_bar") {
    const xs = [...o.box.querySelector('select[aria-label="X axis type"]').options].map((x) => x.textContent);
    check(`${id}: X axis offers Time and Category`, xs.join() === "Time,Category", xs.join());
  }
  if (id === "compare_bar") {
    check("compare_bar: the empty Category field is 'Camera / Input'",
          o.box.querySelector('select[aria-label="Category field"]').options[0].textContent === "Camera / Input");
  }
  o.close();
}

// -- 5. Change chart type keeps what fits, says what does not --------------------
{
  let wrote = null;
  const data = { kind: "line", x_type: "time", bucket: "1d", time_interval: "day", range: "page",
                 agg: "sum", unit: "pcs", bands: { at: [5] },
                 series: { s1: { label: "CAM 1" }, s2: { label: "CAM 2", show: false } } };
  let n = graph(data);
  const o = openNode(n, (pt) => { wrote = pt; }, TIME_NUM);
  click(o.m, button(o.box, "Change chart type"));
  check("Change chart type opens the picker on the current type, with Cancel",
        !!picker(o.box) && JSON.stringify(checked(o.box)) === '["line"]' && button(o.box, "Cancel"));
  click(o.m, button(o.box, "Cancel"));
  check("...Cancel goes back to the properties, nothing written",
        !picker(o.box) && strip(o.box) && wrote === null);
  click(o.m, button(o.box, "Change chart type"));
  click(o.m, card(o.box, "pie"));
  const note = o.box.querySelector(".ctp-drop");
  check("picking Pie says, before Continue, that the interval (Day) is not kept",
        note && note.textContent.includes("The interval (Day)") && wrote === null,
        note && note.textContent);
  click(o.m, button(o.box, "Continue with Pie Chart"));
  check("Continue: kind pie, X a category, the date range, Y and unit kept",
        wrote && wrote.kind === "pie" && wrote.x_type === "column" && wrote.bucket === ""
        && wrote.range === "page" && wrote.agg === "sum" && !("unit" in wrote) && !("bands" in wrote),
        JSON.stringify(wrote));
  check("...every input keeps its name and its Show",
        wrote.series.s1.label === "CAM 1" && wrote.series.s2.label === "CAM 2"
        && wrote.series.s2.show === false, JSON.stringify(wrote.series));
  n = graph({ ...n.data, ...wrote });
  o.m.render({ ...o.props, node: n, nodes: [n, T1, T2] });
  check("...and it reopens as a Pie Chart, names in place",
        strip(o.box).textContent.includes("Pie Chart")
        && o.box.querySelector('input[aria-label="Series name for input 1"]').value === "CAM 1");
  // Back to time: the category field is what does not fit.
  wrote = null;
  const pie = graph({ kind: "pie", x_type: "column", group_by: "status", range: "", agg: "count" });
  o.m.render({ ...o.props, node: pie, nodes: [pie, T1, T2], onChange: (pt) => { wrote = pt; } });
  click(o.m, button(o.box, "Change chart type"));
  click(o.m, card(o.box, "time_bar"));
  const n2 = o.box.querySelector(".ctp-drop")?.textContent || "";
  check("Pie -> Time-based Bar names the category field and the All-time range it cannot keep",
        n2.includes('"status"') && n2.includes("All time"), n2);
  click(o.m, button(o.box, "Continue with Time-based Bar"));
  check("...and writes a bar with Time on X",
        wrote && wrote.kind === "bar" && wrote.x_type === "time" && wrote.group_by === ""
        && wrote.range === "7d", JSON.stringify(wrote));
  o.close();
}

// -- 6. Show as within a family; Smooth line ------------------------------------------
{
  let wrote = null;
  const o = openNode(graph({ kind: "line", bucket: "1h" }), (pt) => { wrote = pt; }, TIME_NUM);
  const sel = o.box.querySelector('select[aria-label="Show as"]');
  check("Show as on a Line Chart: Line chart, Area chart, Bar chart (Time based)",
        [...sel.options].map((x) => x.textContent).join() === "Line chart,Area chart,Bar chart (Time based)");
  choose(o.m, sel, "area");
  check("line -> area on a legacy chart writes the kind alone (as the old Show as did)",
        JSON.stringify(wrote) === '{"kind":"area"}', JSON.stringify(wrote));
  wrote = null;
  click(o.m, o.box.querySelector('input[aria-label="Smooth line"]'));
  check("Smooth line writes kind spline alone", JSON.stringify(wrote) === '{"kind":"spline"}');
  wrote = null;
  choose(o.m, o.box.querySelector('select[aria-label="Show as"]'), "time_bar");
  check("line -> Bar chart (Time based) writes the model so the bar stays on Time",
        wrote && wrote.kind === "bar" && wrote.x_type === "time", JSON.stringify(wrote));
  o.close();
}

// -- 7. the Builders: the same picker ---------------------------------------------
{
  const w = mod.newWidget({ kind: "bar", source: "bucket", label: "Bar chart" },
                          BUILDER_META.sources);
  let patched = null;
  const b = openWidget(w, (id, pt) => { patched = pt; });
  check("Builder: a chart just added opens on the picker, its dragged kind preselected",
        !!picker(b.box) && JSON.stringify(checked(b.box)) === '["compare_bar"]'
        && !b.box.textContent.includes("Appearance"));
  click(b.m, button(b.box, "Continue with Comparison Bar"));
  check("...Continue with the same type writes nothing and shows the properties",
        patched === null && !picker(b.box) && strip(b.box)?.textContent.includes("Comparison Bar Chart"));
  b.close();
  // An old hand-made widget (no `chart` model) opens on its properties.
  patched = null;
  const old = { id: "line_old", title: "Old", kind: "line", source: "history", key: "fn_x_count",
                range: "24h", span: 4, grid: {}, template: "plain", bands: {},
                series: [{ id: "s1", label: "A", color: "#ff0000", source: "history", key: "fn_x_count" }] };
  const ob = openWidget(old, (id, pt) => { patched = pt; });
  check("Builder: an old chart opens straight on its properties, writing nothing",
        !picker(ob.box) && strip(ob.box)?.textContent.includes("Line Chart (Time Series)") && patched === null);
  click(ob.m, button(ob.box, "Change chart type"));
  click(ob.m, card(ob.box, "donut"));
  click(ob.m, button(ob.box, "Continue with Donut Chart"));
  check("...Change chart type to Donut keeps its row (name, colour) and writes the model",
        patched && patched.kind === "donut" && patched.chart?.x_type === "column"
        && patched.series[0].label === "A" && patched.series[0].color === "#ff0000",
        JSON.stringify(patched));
  ob.close();
  // A hand-made chart can still become a card shape ("Not a chart").
  const hb = openWidget({ ...old, id: "line_old2" });
  const opts = [...hb.box.querySelector('select[aria-label="Show as"]').querySelectorAll("optgroup option")]
    .map((x) => x.value);
  check("...and a hand-made chart's Show as still offers the card shapes, apart",
        opts.includes("shape:tile") && opts.includes("shape:table"), JSON.stringify(opts));
  hb.close();
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
