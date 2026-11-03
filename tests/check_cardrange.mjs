// A CARD's one Date range, on the workflow node and in both Builders.
//
// Owner, 2026-10-13, on a stat tile ("Total Violation CAM 1 (Day/Days)",
// object_id of PCB-SENSATA-CAM1): its panel listed Period, Count over and a
// second Period -- "for this also many duplicate or redundant, please make it
// into one". The two Periods were one stored field (`range`) written from two
// places; Count over (`window`) is read only by a card showing a live rule's
// value. Pinned on the real panels:
//
//   1. the screenshot's tile: ONE "Date range", recorded-row options, the
//      value the compile used, the dead Count over kept as saved
//   2. per source: a live rule (Live value (now) first, the rule's counters),
//      a table, recorded events, a table named on the card; a Query: none
//   3. an edit writes the field that source reads, and only that one
//   4. the Builder shows the same control, options and value; its edit
//      reaches the node through card-style
//   5. all 5 x 5 x 5 combinations of the three old controls
//
//   node tests/check_cardrange.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".cardrange-test.cjs");

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
export { SHAPE_GROUPS, CARD_RANGES, LIVE_RANGES, cardRangeOf, cardRangePatch }
  from "${p("src/components/widgets/cardFields.jsx")}";
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

// -- fixtures ------------------------------------------------------------------
const COLS = [{ name: "object_id", type: "text" }, { name: "status", type: "text" },
              { name: "total_panel", type: "number" }];
const TABLES = [{ key: "cam1", name: "PCB-SENSATA-CAM1", columns: COLS }];
const SOURCE_DATA = { source_mode: "", dataset: "", field: "", filters: {},
                      conditions: [], limit: 15 };
const CARD_DATA = { name: "", metric: "", kind: "tile", unit: "", sub: "",
                    window: "live", agg: "", range: "", ...SOURCE_DATA };
const REGISTRY = {
  "dashboard.card": { category: "dashboard", label: "Card", data: CARD_DATA },
  "output.dataset": { category: "output", label: "Data Table" },
  "storage.query": { category: "storage", label: "Query" },
  "analytics.zone_count": { category: "analytics", label: "Zone Counter",
                            metrics: ["count", "today", "week", "month", "total"] },
};
const T1 = { id: "t1", type: "output.dataset",
             data: { dataset: "cam1", name: "PCB-SENSATA-CAM1", columns: COLS } };
const RULE = { id: "r1", type: "analytics.zone_count", data: { name: "Bay count" } };
const QUERY = { id: "q1", type: "storage.query", data: { name: "By shift", sql: "select 1" } };
const META = { variables: { event_fields: ["camera", "zone"], event_values: {}, tables: TABLES } };
const card = (data) => ({ id: "c1", type: "dashboard.card",
                          data: { ...CARD_DATA, name: "Total Violation CAM 1 (Day/Days)", ...data } });
const WIRES = {
  column: [{ id: "w", source: "t1", sourceHandle: "o_object_id", target: "c1" }],
  table: [{ id: "w", source: "t1", sourceHandle: "table", target: "c1" }],
  rule: [{ id: "w", source: "r1", target: "c1" }],
  query: [{ id: "w", source: "q1", target: "c1" }],
  none: [],
};
function openNode(n, wires, onChange = () => {}) {
  const box = document.body.appendChild(document.createElement("div"));
  const props = { node: n, registry: REGISTRY, meta: META, nodes: [n, T1, RULE, QUERY],
                  edges: wires, editable: true, errors: [], warnings: [], onChange,
                  functions: [], onDone: () => {} };
  const m = mod.live(box, "node", props);
  return { box, m, close: () => { m.unmount(); box.remove(); } };
}
const BUILDER_META = {
  sources: { stat: ["fn_r1_today"], bucket: [], events: [] },
  query: { aggregations: ["count", "unique"], columns: ["camera", "zone"] },
  kinds: ["tile", "gauge", "table"], capabilities: [],
  catalog: [{ dataset: "cam1", columns: COLS }],
};
function openWidget(w, patch = () => {}) {
  const box = document.body.appendChild(document.createElement("div"));
  const m = mod.live(box, "widget", { current: w, meta: BUILDER_META, patch });
  return { box, m, close: () => { m.unmount(); box.remove(); } };
}
/** Every labelled control on the panel, by its label. */
const labelsOf = (box) => [...box.querySelectorAll("label.wf-field, label.ui-field")]
  .map((l) => (l.querySelector(".wf-field-label, .ui-field-label")?.childNodes[0]?.textContent || "").trim());
const rangeSel = (box) => box.querySelector('select[aria-label="Date range"]');
const opts = (box) => [...(rangeSel(box)?.options || [])].map((o) => o.value);
function choose(m, sel, value) {
  m.act(() => {
    sel.value = value;
    sel.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  });
}
const RECORDED = mod.CARD_RANGES.map(([v]) => v);
const LIVE = mod.LIVE_RANGES.map(([v]) => v);

// -- 1. the owner's screenshot: a stat tile of a text column --------------------------
{
  const o = openNode(card({ agg: "unique", range: "page", window: "today" }), WIRES.column);
  const labels = labelsOf(o.box);
  console.log(`     stat tile (column) panel: ${labels.join(" > ")}`);
  check("the screenshot's tile shows exactly ONE date control, 'Date range'",
        labels.filter((l) => l === "Date range").length === 1
        && !labels.includes("Period") && !labels.includes("Count over"), JSON.stringify(labels));
  check("...offering what recorded rows can answer (no 'Live value'), set to what it used",
        JSON.stringify(opts(o.box)) === JSON.stringify(RECORDED) && rangeSel(o.box).value === "page",
        JSON.stringify(opts(o.box)));
  const adv = o.box.querySelector("details.chart-advanced");
  check("...and the dead 'Count over: today' it carried is kept as saved under Advanced",
        adv && !adv.open && adv.textContent.includes("Count over: Today (as saved"),
        adv && adv.textContent);
  o.close();
}

// -- 2. per source ------------------------------------------------------------------------
for (const [what, data, wires, want, value] of [
  ["a live rule's value", { window: "today", range: "week" }, WIRES.rule, LIVE, "today"],
  ["a table's rows", { kind: "table", range: "month" }, WIRES.table, RECORDED, "month"],
  ["recorded events", { source_mode: "events", range: "7d" }, WIRES.none, RECORDED, "7d"],
  ["a table named on the card", { source_mode: "dataset", dataset: "cam1", range: "" },
   WIRES.none, RECORDED, ""],
]) {
  const o = openNode(card(data), wires);
  const labels = labelsOf(o.box);
  check(`${what}: one Date range, the right options, its stored value`,
        labels.filter((l) => l === "Date range").length === 1 && !labels.includes("Period")
        && !labels.includes("Count over") && JSON.stringify(opts(o.box)) === JSON.stringify(want)
        && rangeSel(o.box).value === value,
        `${JSON.stringify(labels)} ${JSON.stringify(opts(o.box))} ${rangeSel(o.box)?.value}`);
  o.close();
}
{
  const o = openNode(card({ window: "today", range: "week" }), WIRES.rule);
  check("a live rule's card keeps a dead Period as saved, says it did not apply",
        o.box.querySelector("details.chart-advanced")?.textContent
          .includes("Period: This week (as saved; a card showing a live rule's value does not read it)"));
  o.close();
  const q = openNode(card({ range: "today", window: "week" }), WIRES.query);
  check("a card fed by a Query has no date control: the Query's own period applies",
        !rangeSel(q.box) && q.box.textContent.includes("the Query's own period applies"));
  q.close();
}

// -- 3. what an edit writes ----------------------------------------------------------------
{
  let patch = null;
  const o = openNode(card({ range: "page", window: "today" }), WIRES.column, (p) => { patch = p; });
  choose(o.m, rangeSel(o.box), "30d");
  check("recorded: the one control writes `range` (and leaves the saved window alone)",
        patch && patch.range === "30d" && !("window" in patch), JSON.stringify(patch));
  o.close();
  const r = openNode(card({ window: "today" }), WIRES.rule, (p) => { patch = p; });
  choose(r.m, rangeSel(r.box), "page");
  check("live rule: it writes `window` (follow the Analytics range)",
        patch && patch.window === "follow" && !("range" in patch), JSON.stringify(patch));
  r.close();
}

// -- 4. node and Builder alike ---------------------------------------------------------------
for (const [what, n, wires, w] of [
  ["a column tile", card({ agg: "unique", range: "page" }), WIRES.column,
   { id: "wf_c1", title: "T", kind: "tile", source: "dataset", dataset: "cam1", columns: COLS,
     query: { agg: "unique", field: "object_id", range: "page" }, span: 1, grid: {},
     template: "plain", bands: {} }],
  ["a live rule's tile", card({ window: "week" }), WIRES.rule,
   { id: "wf_c1", title: "T", kind: "tile", source: "stat", key: "fn_r1_week", window: "week",
     span: 1, grid: {}, template: "plain", bands: {} }],
]) {
  const o = openNode(n, wires);
  const b = openWidget(w);
  check(`${what}: the Builder shows the same one Date range, options and value`,
        labelsOf(b.box).filter((l) => l === "Date range").length === 1
        && !labelsOf(b.box).includes("Period")
        && JSON.stringify(opts(b.box)) === JSON.stringify(opts(o.box))
        && rangeSel(b.box).value === rangeSel(o.box).value,
        `${JSON.stringify(opts(b.box))} ${rangeSel(b.box)?.value} / ${rangeSel(o.box)?.value}`);
  o.close(); b.close();
}
{
  let patched = null;
  const w = { id: "wf_c1", title: "T", kind: "tile", source: "stat", key: "fn_r1_week",
              window: "week", span: 1, grid: {}, template: "plain", bands: {} };
  const b = openWidget(w, (id, p) => { patched = p; });
  choose(b.m, rangeSel(b.box), "today");
  const edits = mod.cardQuestionEdits(w, { ...w, ...patched });
  check("a Builder edit of a live card goes to its node as `window` (card-style)",
        patched.window === "today" && edits.window === "today", JSON.stringify(edits));
  b.close();
  const hand = openWidget({ id: "h1", title: "T", kind: "tile", source: "stat", key: "fn_r1_today",
                           span: 1, grid: {}, template: "plain", bands: {} });
  check("a hand-made live tile reads one key as it is now: no date control",
        !rangeSel(hand.box));
  hand.close();
}

// -- 5. every combination of the old fields ------------------------------------------------
{
  const RANGES = ["", "page", "today", "week", "month"];
  const WINDOWS = ["live", "follow", "today", "week", "month"];
  const RANGE_OF_WINDOW = { live: "live", follow: "page", today: "today", week: "week", month: "month" };
  const bad5 = [];
  let n = 0;
  // The two Periods were ONE stored field, written from two places: so the
  // 5 x 5 x 5 combinations of the three controls are 5 x 5 stored states.
  for (const p1 of RANGES) for (const p2 of RANGES) for (const win of WINDOWS) {
    const range = p2 || p1;       // the later control's write is what was stored
    for (const source of ["live", "recorded"]) {
      n += 1;
      const d = { range, window: win };
      const r = mod.cardRangeOf(d, source);
      const effective = source === "live" ? RANGE_OF_WINDOW[win] : range;
      const otherSet = source === "live" ? !!range && range !== effective
        : win !== "live" && RANGE_OF_WINDOW[win] !== range;
      const written = { ...d, ...mod.cardRangePatch(source, r.value) };
      const touched = Object.keys(mod.cardRangePatch(source, r.value));
      const again = mod.cardRangeOf(written, source);
      const ok = r.value === effective && !!r.note === otherSet
        && again.value === r.value
        && (source === "live" ? written.window === win && written.range === range
                              : written.range === range && written.window === win)
        && (source === "live" ? JSON.stringify(touched) === '["window"]'
                              : !touched.includes("window"));
      if (!ok) bad5.push({ p1, p2, win, source, r, written });
    }
  }
  check(`all ${n} combinations (5 x 5 x 5 old controls, two sources): the value is what `
        + "the compile used, the other is a note, writing it back changes nothing",
        !bad5.length, JSON.stringify(bad5.slice(0, 3)));
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
