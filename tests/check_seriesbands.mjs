// Thresholds per camera, edited (owner, 2026-10-13: "users should be able to
// define different threshold values and colours for each camera
// independently, rather than applying a single threshold configuration to all
// cameras").
//
// On the real panels (workflow node Inspector, Builders' WidgetInspector):
//   - an input's collapsed Override offers "Use the shared thresholds" /
//     "Set thresholds for this input" and, when its own, the LIMITS -- on the
//     node with no colour control (a grey line says where colours are set)
//   - the Builder draws the same limits in the row, and the COLOURS under
//     Appearance: the shared group and one group per camera, by name
//   - a camera's edit lands on that camera only, and travels to its node
//   - the model keeps an input's own bands through being written
//
//   node tests/check_seriesbands.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".seriesbands-test.cjs");

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

// -- fixtures ------------------------------------------------------------------------
const COLS = [{ name: "object_id", type: "text" }, { name: "total_panel", type: "number" }];
const TABLES = [{ key: "cam1", name: "PCB-SENSATA-CAM1", columns: COLS },
                { key: "cam2", name: "PCB-SENSATA-CAM2", columns: COLS }];
const SOURCE_DATA = { source_mode: "", dataset: "", field: "", filters: {},
                      conditions: [], limit: 15 };
const GRAPH_DATA = { name: "Panels", metric: "", kind: "line", unit: "", range: "24h",
                     agg: "", group_by: "", bucket: "1h", series: {}, series_n: 2,
                     window: "live", ...SOURCE_DATA };
const REGISTRY = {
  "dashboard.graph": { category: "dashboard", label: "Chart", data: GRAPH_DATA },
  "output.dataset": { category: "output", label: "Data Table" },
};
const T1 = { id: "t1", type: "output.dataset", data: { dataset: "cam1", name: "PCB-SENSATA-CAM1" } };
const T2 = { id: "t2", type: "output.dataset", data: { dataset: "cam2", name: "PCB-SENSATA-CAM2" } };
const TWO = [{ id: "w1", source: "t1", sourceHandle: "o_total_panel", target: "g1", targetHandle: "in" },
             { id: "w2", source: "t2", sourceHandle: "o_total_panel", target: "g1", targetHandle: "s2" }];
const META = { variables: { event_fields: [], event_values: {}, tables: TABLES } };
const YEL = "#ffff00", RED = "#ff0000", GRN = "#00aa00", BLU = "#0000ff";
const CAM1 = { at: [10], colors: [YEL, RED] };
const SERIES = { s1: { label: "CAM 1", bands: CAM1 }, s2: { label: "CAM 2" } };
const graph = (data) => ({ id: "g1", type: "dashboard.graph", data: { ...GRAPH_DATA, ...data } });
function openNode(n, onChange = () => {}) {
  const box = document.body.appendChild(document.createElement("div"));
  const props = { node: n, registry: REGISTRY, meta: META, nodes: [n, T1, T2], edges: TWO,
                  editable: true, errors: [], warnings: [], onChange, functions: [],
                  onDone: () => {} };
  const m = mod.live(box, "node", props);
  return { box, m, props, close: () => { m.unmount(); box.remove(); } };
}
const BUILDER_META = { sources: { stat: [] }, query: {}, kinds: ["line"], capabilities: [],
                       catalog: TABLES.map((t) => ({ dataset: t.key, columns: t.columns })) };
function openWidget(w, patch = () => {}) {
  const box = document.body.appendChild(document.createElement("div"));
  const m = mod.live(box, "widget", { current: w, meta: BUILDER_META, patch });
  return { box, m, close: () => { m.unmount(); box.remove(); } };
}
const entry = (id, ds, label, extra = {}) => ({ id, source: "dataset", dataset: ds, columns: COLS,
                                                field: "total_panel", agg: "avg", ts_field: "", label,
                                                query: { bucket: "1h", range: "24h" }, ...extra });
const COMPILED = { id: "wf_g1", title: "Panels", kind: "line", source: "history", range: "24h",
                   span: 1, grid: {}, template: "plain",
                   bands: { at: [100], colors: ["#16a34a", "#dc2626"] },
                   series: [entry("s1", "cam1", "CAM 1", { bands: CAM1 }), entry("s2", "cam2", "CAM 2")] };
const sel = (box, aria) => box.querySelector(`select[aria-label="${aria}"]`);
const inp = (box, aria) => box.querySelector(`input[aria-label="${aria}"]`);
function choose(m, el, value) {
  m.act(() => {
    el.value = value;
    el.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  });
}
function type(m, el, value) {
  m.act(() => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set.call(el, value);
    el.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
}

// -- the node: the limits only ------------------------------------------------------------
{
  let patch = null;
  const o = openNode(graph({ series: SERIES, bands: { at: [100], colors: ["#16a34a", "#dc2626"] } }),
                     (p) => { patch = p; });
  check("node: CAM 1 has its own thresholds, CAM 2 uses the shared ones",
        sel(o.box, "Thresholds for CAM 1")?.value === "own"
        && sel(o.box, "Thresholds for CAM 2")?.value === ""
        && inp(o.box, "CAM 1 Threshold 1")?.value === "10" && !inp(o.box, "CAM 2 Threshold 1"));
  check("...inside the input's collapsed Override, with no colour control on the node",
        inp(o.box, "CAM 1 Threshold 1").closest("details.chart-override")
        && !o.box.querySelector('input[type="color"]')
        && o.box.textContent.includes("Colours are set in the Analytics or Dashboard Builder."));
  type(o.m, inp(o.box, "CAM 1 Threshold 1"), "20");
  check("editing CAM 1's limit writes CAM 1's own bands, colours carried, CAM 2 untouched",
        patch && JSON.stringify(patch.series.s1.bands) === JSON.stringify({ at: [20], colors: [YEL, RED] })
        && !patch.series.s2.bands && patch.series.s1.label === "CAM 1", JSON.stringify(patch));
  choose(o.m, sel(o.box, "Thresholds for CAM 2"), "own");
  check("'Set thresholds for this input' on CAM 2 starts from the shared limits",
        patch && JSON.stringify(patch.series.s2.bands.at) === "[100]"
        && JSON.stringify(patch.series.s1.bands) === JSON.stringify(CAM1), JSON.stringify(patch));
  choose(o.m, sel(o.box, "Thresholds for CAM 1"), "");
  check("'Use the shared thresholds' on CAM 1 drops its own (it follows the chart's)",
        patch && !("bands" in patch.series.s1), JSON.stringify(patch));
  o.close();
}

// -- the Builder: the limits in the row, the colours under Appearance, per camera ---------
{
  let patched = null;
  const b = openWidget(COMPILED, (id, p) => { patched = p; });
  check("Builder: the same limit editor in CAM 1's override",
        sel(b.box, "Thresholds for CAM 1")?.value === "own"
        && inp(b.box, "CAM 1 Threshold 1")?.value === "10"
        && sel(b.box, "Thresholds for CAM 2")?.value === "");
  const heads = [...b.box.querySelectorAll(".ui-field-label")].map((s) => s.textContent.trim())
    .filter((t) => t.startsWith("Threshold colours"));
  check("Appearance: the shared colours and one group per camera with its own, by name",
        JSON.stringify(heads) === JSON.stringify(["Threshold colours - shared",
                                                  "Threshold colours - CAM 1"]), JSON.stringify(heads));
  check("...CAM 1's group names its bands",
        inp(b.box, "CAM 1 colour below 10") && inp(b.box, "CAM 1 colour over 10")
        && !inp(b.box, "CAM 2 colour below 10"));
  type(b.m, inp(b.box, "CAM 1 colour over 10"), "#123456");
  const row1 = patched && patched.series.find((s) => s.id === "s1");
  const row2 = patched && patched.series.find((s) => s.id === "s2");
  check("a colour picked for CAM 1 lands on CAM 1's bands only",
        row1 && JSON.stringify(row1.bands) === JSON.stringify({ at: [10], colors: [YEL, "#123456"] })
        && !row2.bands, JSON.stringify(patched && patched.series));
  const style = mod.seriesStyle({ ...COMPILED, ...patched }, COMPILED);
  check("...and travels to the node with that input (card-style), the other input not",
        JSON.stringify(style.s1.bands) === JSON.stringify({ at: [10], colors: [YEL, "#123456"] })
        && !("bands" in style.s2), JSON.stringify(style));
  b.close();
}

// -- the model keeps them ------------------------------------------------------------------
{
  const d = { kind: "line", bucket: "1h", series: { s1: { label: "CAM 1", bands: CAM1, agg: "sum" },
                                                    s2: { label: "CAM 2", agg: "sum" } } };
  const inputs = ["s1", "s2"].map((sid) => ({ sid, source: "dataset", column: "total_panel",
                                              type: "number", columns: COLS }));
  const { model, overrides } = mod.CM.readNodeModel(d, "line", inputs, {});
  const written = { ...d, ...mod.CM.nodePatch(d, "line", model, overrides, {}) };
  const again = { ...written, ...mod.CM.nodePatch(written, "line",
    mod.CM.readNodeModel(written, "line", inputs, {}).model,
    mod.CM.readNodeModel(written, "line", inputs, {}).overrides, {}) };
  check("migration round trip: an input's own bands survive the model being written, twice",
        JSON.stringify(written.series.s1.bands) === JSON.stringify(CAM1) && !written.series.s2?.bands
        && JSON.stringify(again.series) === JSON.stringify(written.series), JSON.stringify(written.series));
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
