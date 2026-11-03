// A chart's or a card's properties read the SAME on its workflow node and in
// the Builders.
//
// Owner report (2026-10-12): "The properties card for the graph and the bar
// chart need to be the same for the nodes and in the builder, because it is
// confusing." The two panels edit one card -- the Builders now write its
// question back onto the node -- and had drifted into two vocabularies: the
// node said "Show" where the Builder said "Work out", "average value of a
// column" beside "average value", "Count" beside "Aggregation", "Range"
// beside "Time range", "Visualization" beside "Show as".
//
// So one definition (frontend/src/components/widgets/cardFields.jsx) draws
// both, and this check renders BOTH real panels -- the node Inspector out of
// Workflow.jsx and the Builders' WidgetInspector -- for the same card, and
// compares what they offer:
//
//   1. the shared fields appear in the same ORDER with the same LABELS;
//   2. every shared select offers the same options: same stored values, same
//      words, same order ("Work out", "One point per", "Period", "Show as",
//      "Time range", "Time step");
//   3. the event-store question narrows the same "Work out" list;
//
// Since the chart builder (2026-10-13) a CHART's question is the chart
// builder's (X axis, Y axis, Interval, Date range, Advanced: Date column,
// Only include rows from -- chartFields.jsx); a CARD keeps "Work out",
// "Period" and the rest. Both are compared below the same way.
//   4. node-only rows stay node-only (Source on the canvas, Inputs, Time
//      axis), and no panel draws a label the shared definition lacks for a
//      question field.
//
// The final field list is printed, so the report can quote it.
//
//   node tests/check_cardfields.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".cardfields-test.cjs");

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
import { WidgetInspector } from "${p("src/components/widgets/builderShared.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { SHAPE_GROUPS, CARD_FIELDS, CARD_FIELD_ORDER, WORK_OUT, CHART_SHAPES, CARD_SHAPES,
         PERIODS, PER_TIMES, RANGE_LABELS, INTERVALS, CHART_COLOUR_HINT,
         showsColours }
  from "${p("src/components/widgets/cardFields.jsx")}";
export { CHART_FIELDS } from "${p("src/components/widgets/chartFields.jsx")}";
export { SHOW_AS } from "${p("src/components/widgets/chartTypes.js")}";
function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(h(ToastProvider, null, node)); });
  const html = el.innerHTML;
  act(() => root.unmount());
  return html;
}
export const node = (el, props) => mount(el, h(Inspector, props));
export const widget = (el, props) => mount(el, h(WidgetInspector, props));
/** Mounted and left up, so a test can edit a control and see the patch. */
export function live(el, props) {
  const root = createRoot(el);
  act(() => { root.render(h(ToastProvider, null, h(WidgetInspector, props))); });
  return { act, unmount: () => act(() => root.unmount()) };
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

/** Every field a rendered panel shows, in order: its label (the text before
 *  the hover hint) and, for a select, its options as [value, words]. */
function fields(render, props) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  try {
    const html = render(box, props);
    const doc = new JSDOM(`<body>${html}</body>`).window.document;
    return [...doc.querySelectorAll("label.wf-field, label.ui-field")].map((el) => {
      const span = el.querySelector(".wf-field-label, .ui-field-label");
      const label = span ? (span.childNodes[0]?.textContent || "").trim() : "";
      const sel = el.querySelector("select");
      return { label,
               options: sel ? [...sel.options].map((o) => [o.value, o.textContent]) : null };
    });
  } finally { document.body.removeChild(box); }
}

const SHARED = new Set([...Object.values(mod.CARD_FIELDS),
                        ...Object.values(mod.CHART_FIELDS)].map((f) => f.label));
// The question rows: identity (Title/Caption) is compared too, but the
// Builder's Appearance and Layout sections and the node's wiring rows are
// each panel's own.
const QUESTION = ["Show as", "Work out", "Of which column", "One point per",
                  "Group by time", "Only events of type", "Period", "Time range",
                  // the chart builder's (chartFields.CHART_FIELDS)
                  "X axis", "X column", "Interval", "Date range", "Y axis",
                  "Of column", "Date column", "Only include rows from",
                  // a card's one date range (cardFields.CardDateRange)
                  "Time step", "Rows", "Unit label"];
const shared = (list) => list.filter((f) => QUESTION.includes(f.label));
const byLabel = (list, label) => list.find((f) => f.label === label);

// -- the node registry and canvas the server would hand the editor -----------
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
const COLUMNS = [{ name: "shift", type: "text" }, { name: "weight", type: "number" }];
const TABLE = { id: "t1", type: "output.dataset",
                data: { dataset: "defects", name: "Defects", columns: COLUMNS } };
const NODE_META = { variables: { event_fields: ["camera", "zone"], event_values: {},
                                 tables: [] } };
const nodeFields = (n, extra = {}) => fields(mod.node, {
  node: n, registry: REGISTRY, meta: NODE_META, nodes: [n, ...(extra.nodes || [])],
  edges: extra.edges || [], editable: true, errors: [], warnings: [],
  onChange: () => {}, functions: [], onDone: () => {},
});
const BUILDER_META = {
  sources: { stat: ["fn_x_count"], bucket: [], events: [] },
  history: { ranges: Object.keys(mod.RANGE_LABELS),
             intervals: ["", "10s", "1m", "5m", "15m", "1h", "1d"],
             aggs: ["avg", "sum", "min", "max", "last", "count"] },
  query: { aggregations: ["count", "sum", "avg", "min", "max", "unique",
                          "changes", "latest"],
           columns: ["camera", "zone"], buckets: ["hour", "day"] },
  kinds: ["tile", "line", "bar", "area", "spline", "donut", "pie", "gauge",
          "table"],
  capabilities: [], catalog: [],
};
const widgetFields = (w) => fields(mod.widget, { current: w, meta: BUILDER_META,
                                                 patch: () => {} });
const chartNode = (data) => ({ id: "g1", type: "dashboard.graph",
                               data: { ...REGISTRY["dashboard.graph"].data, name: "Defects", ...data } });

// -- 1+2. THE OWNER'S BAR CHART over a data table, both panels -----------------
const nodeBar = nodeFields(chartNode({ source_mode: "dataset", dataset: "defects",
                                       kind: "bar", agg: "avg", field: "weight",
                                       bucket: "day", range: "week" }),
                           { nodes: [TABLE] });
const builderBar = widgetFields({
  id: "wf_g1", title: "Defects", kind: "bar", source: "dataset", dataset: "defects",
  columns: COLUMNS, query: { agg: "avg", field: "weight", bucket: "day", range: "week" },
  span: 1, grid: {}, template: "plain", bands: {},
});
const order = (list) => shared(list).map((f) => f.label);
console.log(`     bar chart, both panels: ${order(builderBar).join(" > ")}`);
check("the bar chart shows the same question fields in the same order",
      JSON.stringify(order(nodeBar)) === JSON.stringify(order(builderBar)),
      `node    ${JSON.stringify(order(nodeBar))}\n      builder ${JSON.stringify(order(builderBar))}`);
for (const label of ["Show as", "X axis", "Interval", "Date range", "Y axis",
                     "Of column", "Date column"]) {
  const a = byLabel(nodeBar, label);
  const b = byLabel(builderBar, label);
  check(`"${label}" offers the same values and words on both`,
        // The Date column is not drawn at all on a table with no date column:
        // then both panels agree by both leaving it out.
        (label === "Date column" || (a && b))
          && JSON.stringify(a && a.options) === JSON.stringify(b && b.options),
        `node    ${JSON.stringify(a && a.options)}\n      builder ${JSON.stringify(b && b.options)}`);
}
// A CARD's "Work out" (a chart's Y is the chart builder's, compared above).
const workOut = byLabel(widgetFields({
  id: "wf_c1", title: "Rows", kind: "tile", source: "dataset", dataset: "defects",
  columns: COLUMNS, query: { agg: "count" }, span: 1, grid: {}, template: "plain",
  bands: {}, sub: "" }), "Work out").options;
check("'Work out' is the one list, stored values unchanged",
      JSON.stringify(workOut) === JSON.stringify(mod.WORK_OUT.map(([v, w]) => [v, w])),
      JSON.stringify(workOut));
check("...with 'total rows' and 'average value' spelled the same everywhere",
      workOut.some(([v, w]) => v === "count" && w === "total rows")
      && workOut.some(([v, w]) => v === "avg" && w === "average value"));
// Since the chart type picker (2026-10-14) a chart's "Show as" lists its own
// family only (a Time-based Bar is a trend); another family is "Change chart
// type".
check("'Show as' on a chart lists its family's types, in the mockup's words",
      JSON.stringify(byLabel(builderBar, "Show as").options)
        === JSON.stringify(mod.SHOW_AS.trends), JSON.stringify(byLabel(builderBar, "Show as").options));

// -- the same for a CARD -------------------------------------------------------
const nodeTile = nodeFields({ id: "c1", type: "dashboard.card",
                              data: { ...REGISTRY["dashboard.card"].data, name: "Rows",
                                      source_mode: "dataset", dataset: "defects",
                                      kind: "tile", agg: "count", range: "today" } },
                            { nodes: [TABLE] });
const builderTile = widgetFields({
  id: "wf_c1", title: "Rows", kind: "tile", source: "dataset", dataset: "defects",
  columns: COLUMNS, query: { agg: "count", range: "today" }, span: 1, grid: {},
  template: "plain", bands: {}, sub: "",
});
check("the card shows the same question fields in the same order",
      JSON.stringify(order(nodeTile)) === JSON.stringify(order(builderTile)),
      `node    ${JSON.stringify(order(nodeTile))}\n      builder ${JSON.stringify(order(builderTile))}`);
// A card's period is its ONE "Date range" (CardDateRange, 2026-10-13).
for (const label of ["Work out", "Date range"]) {
  check(`the card's "${label}" matches`,
        JSON.stringify(byLabel(nodeTile, label)?.options)
          === JSON.stringify(byLabel(builderTile, label)?.options));
}
check("both say Title and Caption",
      ["Title", "Caption"].every((l) => byLabel(nodeTile, l) && byLabel(builderTile, l)));

// -- a chart of a live rule's SAMPLES: Time range + Interval ------------------
const rule = { id: "r1", type: "analytics.frame", data: { name: "In frame" } };
const nodeLine = nodeFields(chartNode({ kind: "line", range: "6h", interval: "5m" }),
                            { nodes: [rule], edges: [{ id: "e", source: "r1", target: "g1" }] });
const builderLine = widgetFields({
  id: "wf_g1", title: "Defects", kind: "line", source: "history", key: "fn_x_count",
  range: "6h", interval: "5m", span: 1, grid: {}, template: "plain", bands: {},
});
for (const label of ["X axis", "Interval", "Date range", "Y axis"]) {
  const a = byLabel(nodeLine, label);
  const b = byLabel(builderLine, label);
  check(`a sampled chart's "${label}" matches`,
        // The Date column is not drawn at all on a table with no date column:
        // then both panels agree by both leaving it out.
        (label === "Date column" || (a && b))
          && JSON.stringify(a && a.options) === JSON.stringify(b && b.options),
        `node    ${JSON.stringify(a && a.options)}\n      builder ${JSON.stringify(b && b.options)}`);
}

// -- 3. the event store's question: the same list, narrowed --------------------
const nodeEvents = nodeFields(chartNode({ source_mode: "events", kind: "bar" }));
const evOpts = byLabel(nodeEvents, "Y axis")?.options || [];
check("a recorded-events chart asks its Y with the chart builder's calculations",
      evOpts.map(([v]) => v).join() === "count,count_nonnull,unique,changes,sum,avg,min,max"
      && evOpts.some(([v, w]) => v === "unique" && w === "Count Difference Value")
      && evOpts.some(([v, w]) => v === "changes" && w === "Count Value Changes"),
      JSON.stringify(evOpts));
const builderEvents = widgetFields({
  id: "x1", title: "Events", kind: "bar", source: "query",
  query: { agg: "count", bucket: "hour" }, span: 1, grid: {}, template: "plain", bands: {},
});
const evB = byLabel(builderEvents, "Y axis")?.options || [];
check("...and the Builder's events chart the same words for the same values",
      JSON.stringify(evB) === JSON.stringify(evOpts)
      && byLabel(builderEvents, "Interval") && byLabel(nodeEvents, "X axis")
      && byLabel(builderEvents, "Only events of type") && byLabel(nodeEvents, "Only events of type"));
check("the old competing labels are gone from both panels",
      ![...nodeBar, ...builderBar, ...nodeEvents, ...builderEvents, ...nodeLine,
        ...builderLine].some((f) => ["Visualization", "Aggregation", "Show", "Count",
                                     "Range", "…or time bucket", "Filter: event type",
                                     "Distinct what"].includes(f.label)));

// -- 4. node-only rows stay node-only --------------------------------------------
check("the canvas keeps its wiring rows (Source), the Builder its Appearance",
      byLabel(nodeBar, "Source") && byLabel(builderBar, "Template"));
check("a compiled card's Source is shown but not offered in the Builder",
      /<select[^>]*disabled[^>]*>/.test(
        mod.widget(document.body.appendChild(document.createElement("div")), {
          current: { id: "wf_g1", title: "x", kind: "bar", source: "dataset",
                     columns: COLUMNS, query: { agg: "count" }, span: 1, grid: {},
                     template: "plain", bands: {} },
          meta: BUILDER_META, patch: () => {} })));

// -- 5. a CHART's colours are the Builders' only; a CARD's stay on both --------
// Owner request (2026-10-12): "all colours for the graph need to be set at the
// Analytics or Dashboard Builder only". cardFields.showsColours decides it.
const html = (render, props) => {
  const box = document.body.appendChild(document.createElement("div"));
  try { return render(box, props); } finally { document.body.removeChild(box); }
};
const nodeHtml = (n, extra = {}) => html(mod.node, {
  node: n, registry: REGISTRY, meta: NODE_META, nodes: [n, ...(extra.nodes || [])],
  edges: extra.edges || [], editable: true, errors: [], warnings: [],
  onChange: () => {}, functions: [], onDone: () => {},
});
const COLOUR_BITS = [/type="color"/, /Palette colour/, /Colour for series/,
                     /colour below the first threshold/, /aria-label="colour /];
const colourBits = (h) => COLOUR_BITS.filter((re) => re.test(h)).map(String);
const HINT = "Colours are set in the Analytics or Dashboard Builder.";
check("the hint is the shared sentence", mod.CHART_COLOUR_HINT === HINT,
      mod.CHART_COLOUR_HINT);
check("showsColours: a chart's colours on the node no, in the Builder yes; a card's both",
      !mod.showsColours("node", "bar") && !mod.showsColours("node", "spline")
      && mod.showsColours("builder", "bar") && mod.showsColours("node", "tile")
      && mod.showsColours("node", "gauge") && mod.showsColours("node", "verdict"));

// (a) a bar chart with two inputs, and a spline with thresholds, on the node.
const nodeBarTwo = nodeHtml(
  chartNode({ kind: "bar", series: { s1: { color: "#ff8800", label: "A" },
                                     s2: { label: "B" } } }),
  { nodes: [TABLE], edges: [
    { id: "e1", source: "t1", sourceHandle: "o_weight", target: "g1", targetHandle: "s1" },
    { id: "e2", source: "t1", sourceHandle: "o_shift", target: "g1", targetHandle: "s2" }] });
check("(a) a bar chart's node draws its series rows (names kept)",
      (nodeBarTwo.match(/Series name for input \d/g) || []).length === 2,
      String((nodeBarTwo.match(/Series name for input \d/g) || []).length));
check("(a) ...with no colour swatch, palette pin or reset",
      colourBits(nodeBarTwo).length === 0 && !nodeBarTwo.includes(">reset<"),
      colourBits(nodeBarTwo).join(", "));
check("(a) ...and the one hint line in their place",
      nodeBarTwo.split(HINT).length === 2, "hint missing or repeated");
const rule2 = { id: "r1", type: "analytics.frame", data: { name: "In frame" } };
const nodeSpline = nodeHtml(
  chartNode({ kind: "spline", bands: { at: [10, 50],
                                       colors: ["#ffff00", "#ff0000", "#550000"] } }),
  { nodes: [rule2], edges: [{ id: "e", source: "r1", target: "g1" }] });
check("(a) a spline's node keeps its threshold VALUES and Add threshold",
      nodeSpline.includes(">Thresholds<") && nodeSpline.includes('value="10"')
      && nodeSpline.includes('value="50"') && nodeSpline.includes("Add threshold"));
check("(a) ...but no threshold colour",
      colourBits(nodeSpline).length === 0, colourBits(nodeSpline).join(", "));
check("(a) ...and says where the colours are set", nodeSpline.includes(HINT));

// (b) a CARD on the node keeps its colours.
const nodeVerdict = nodeHtml(
  { id: "c1", type: "dashboard.card",
    data: { ...REGISTRY["dashboard.card"].data, name: "Bay", kind: "verdict",
            ok_color: "#00aa00", alert_color: "#aa0000" } },
  { nodes: [rule2], edges: [{ id: "e", source: "r1", target: "c1" }] });
check("(b) a verdict card's node still offers its colours",
      (nodeVerdict.match(/type="color"/g) || []).length >= 2
      && !nodeVerdict.includes(HINT),
      String((nodeVerdict.match(/type="color"/g) || []).length));
const nodeGauge = nodeHtml(
  { id: "c1", type: "dashboard.card",
    data: { ...REGISTRY["dashboard.card"].data, name: "Load", kind: "gauge" } },
  { nodes: [rule2], edges: [{ id: "e", source: "r1", target: "c1" }] });
check("(b) a gauge's node is not told its colours moved",
      !nodeGauge.includes(HINT));

// (c) the Builder still offers a chart's colours -- under APPEARANCE only.
// Owner, 2026-10-12: chart colours belong in Appearance, which only the
// Builders have; the series list, the names and the threshold values stay in
// Data. Pinned by SECTION, read off the real panel's <details>.
const SPLINE = {
  id: "wf_g1", title: "Trend", kind: "spline", source: "history", range: "24h",
  series: [{ id: "s1", source: "history", key: "fn_x_count", color: "", label: "A" },
           { id: "s2", source: "history", key: "fn_x_count", color: "#0088ff" }],
  bands: { at: [10], colors: ["#ffff00", "#ff0000"] },
  span: 1, grid: {}, template: "plain" };
const builderSpline = html(mod.widget, { meta: BUILDER_META, patch: () => {},
                                         current: SPLINE });
const sections = (h) => {
  const doc = new JSDOM(`<body>${h}</body>`).window.document;
  return Object.fromEntries([...doc.querySelectorAll("details.rail-section")].map(
    (d) => [d.querySelector("summary").childNodes[0].textContent.trim(), d.innerHTML]));
};
const sec = sections(builderSpline);
console.log(`     builder chart sections: ${Object.keys(sec).join(" > ")}`);
check("(c) the Builder's chart panel has Card, Data, Appearance, Layout",
      ["Card", "Data", "Appearance", "Layout"].every((k) => k in sec),
      Object.keys(sec).join(", "));
check("(c) Appearance holds each input's colour, its reset and the palette pin",
      /aria-label="Colour for series 1"/.test(sec.Appearance)
      && /aria-label="Colour for series 2"/.test(sec.Appearance)
      && /Reset the colour of series 2/.test(sec.Appearance)
      && /Palette colour for input 1/.test(sec.Appearance)
      && !/Palette colour for input 2/.test(sec.Appearance),
      colourBits(sec.Appearance || "").join(", "));
check("(c) ...and one swatch per threshold band, named by its limit, in order",
      /aria-label="colour below 10"/.test(sec.Appearance)
      && /aria-label="colour over 10"/.test(sec.Appearance)
      && sec.Appearance.indexOf("colour below 10") < sec.Appearance.indexOf("colour over 10")
      && sec.Appearance.includes(">Threshold colours<"));
check("(c) Card and Data hold no colour control for the chart",
      colourBits(sec.Card || "").length === 0 && colourBits(sec.Data || "").length === 0,
      `Card: ${colourBits(sec.Card || "")}  Data: ${colourBits(sec.Data || "")}`);
check("(c) Data keeps the inputs, their names, Add series and the threshold values",
      /Series name for input 1/.test(sec.Data) && /Series name for input 2/.test(sec.Data)
      && sec.Data.includes("Add series") && sec.Data.includes(">Thresholds<")
      && /aria-label="Threshold 1"[^>]*value="10"|value="10"[^>]*aria-label="Threshold 1"/.test(sec.Data)
      && sec.Data.includes("Add threshold")
      && sec.Data.includes("Each band's colour is under Appearance."));
check("(c) the Builder never shows the node's hint", !builderSpline.includes(HINT));
const builderTileHtml = html(mod.widget, { meta: BUILDER_META, patch: () => {}, current: {
  id: "t9", title: "T", kind: "tile", source: "stat", key: "fn_x_count",
  span: 1, grid: {}, template: "plain", bands: {} } });
const tileSec = sections(builderTileHtml);
check("(c) a tile's Appearance gets no chart colour block",
      !tileSec.Appearance.includes("Series colours")
      && !tileSec.Appearance.includes("Threshold colours"));

// Edits in Appearance write the SAME stored series / bands Data writes.
function editIn(widget, label, value) {
  const box = document.body.appendChild(document.createElement("div"));
  let patched = null;
  const m = mod.live(box, { meta: BUILDER_META, current: widget,
                            patch: (id, fields) => { patched = fields; } });
  try {
    const input = box.querySelector(`input[aria-label="${label}"]`);
    const set = Object.getOwnPropertyDescriptor(
      dom.window.HTMLInputElement.prototype, "value").set;
    m.act(() => {
      set.call(input, value);
      input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    });
  } finally { m.unmount(); box.remove(); }
  return patched;
}
const bandEdit = editIn(SPLINE, "colour over 10", "#123456");
check("(c) a band colour set in Appearance writes the same bands, limits kept",
      bandEdit && JSON.stringify(bandEdit.bands)
        === JSON.stringify({ at: [10], colors: ["#ffff00", "#123456"] }),
      JSON.stringify(bandEdit));
const seriesEdit = editIn(SPLINE, "Colour for series 1", "#abcdef");
const sList = seriesEdit && seriesEdit.series;
check("(c) a series colour set in Appearance writes the same series list",
      Array.isArray(sList) && sList.length === 2 && sList[0].color === "#abcdef"
      && sList[0].label === "A" && sList[1].color === "#0088ff",
      JSON.stringify(seriesEdit));
const limitEdit = editIn(SPLINE, "Threshold 1", "20");
check("(c) a threshold value set in Data keeps the Appearance colours",
      limitEdit && JSON.stringify(limitEdit.bands)
        === JSON.stringify({ at: [20], colors: ["#ffff00", "#ff0000"] }),
      JSON.stringify(limitEdit));

// -- a STATUS pill: its rule is drawn by both panels from StatusFields ------
// (shape coverage audit, 2026-10-12: the Builder offered "Alert when" on a
// compiled pill, the node offered nothing, and the compile carried no rule.)
{
  const rule = { id: "r1", type: "analytics.frame", data: { name: "In frame" } };
  const nodePill = nodeFields({ id: "c1", type: "dashboard.card",
                                data: { ...REGISTRY["dashboard.card"].data, name: "Pill",
                                        kind: "status", op: "<", value: 3 } },
                              { nodes: [rule], edges: [{ id: "e", source: "r1", target: "c1" }] });
  const builderPill = widgetFields({ id: "wf_c1", title: "Pill", kind: "status",
                                     source: "stat", key: "fn_x_count", span: 1, grid: {},
                                     template: "plain", bands: {},
                                     status: { op: "<", value: 3, ok_label: "OK",
                                               alert_label: "ALERT" } });
  check("a status pill offers 'Alert when' and 'Labels' on both panels",
        ["Alert when", "Labels"].every((l) => byLabel(nodePill, l) && byLabel(builderPill, l)));
  check("...with the same comparison list",
        JSON.stringify(byLabel(nodePill, "Alert when")?.options)
          === JSON.stringify(byLabel(builderPill, "Alert when")?.options));
}

// -- "Show as" is GROUPED, the same way on both surfaces --------------------
// (owner, 2026-10-12: "state which one is graph, which one is chart, or
// categorize it"). Stored values and words unchanged; every shape the
// dropdown offered before is still there, under one group each.
function shapeGroups(render, props) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  try {
    const doc = new JSDOM(`<body>${render(box, props)}</body>`).window.document;
    const lab = [...doc.querySelectorAll("label")].find((l) =>
      (l.querySelector(".wf-field-label, .ui-field-label")?.childNodes[0]?.textContent || "")
        .trim() === "Show as");
    const sel = lab?.querySelector("select");
    return sel ? [...sel.querySelectorAll("optgroup")].map((g) => [g.label,
      [...g.querySelectorAll("option")].map((o) => `${o.value}=${o.textContent}`)]) : null;
  } finally { document.body.removeChild(box); }
}
const DROPDOWN = ["tile", "line", "bar", "area", "spline", "donut", "pie", "radar",
                  "gauge", "table", "timeline", "video", "status", "verdict", "heatmap"];
const WANT_GROUPS = mod.SHAPE_GROUPS.map(([label]) => label);
// The chart builder brief (2026-10-13) regrouped the charts BY PURPOSE:
// Charts compare categories and values, Time-series graphs show change.
check("the groups are the four agreed, in order",
      JSON.stringify(WANT_GROUPS) === JSON.stringify([
        "Charts - compare categories and values",
        "Time-series graphs - change over time",
        "Single value", "Lists, events and media"]));
const handMade = shapeGroups(mod.widget, {
  current: { id: "x1", title: "x", kind: "tile", source: "stat", key: "", span: 1,
             grid: {}, template: "plain", bands: {} },
  meta: { ...BUILDER_META, kinds: DROPDOWN }, patch: () => {} });
const offered = (handMade || []).flatMap(([, opts]) => opts.map((o) => o.split("=")[0]));
check("a Builder card offers every shape it offered before, each once, grouped",
      handMade && handMade.length === 4
      && DROPDOWN.every((k) => offered.filter((x) => x === k).length === 1)
      && offered.length === DROPDOWN.length,
      JSON.stringify(handMade));
check("...in the agreed groups",
      JSON.stringify(handMade?.map(([l, o]) => [l, o.map((x) => x.split("=")[0])]))
        === JSON.stringify(mod.SHAPE_GROUPS));
check("...and the stored values and words are the old ones",
      (handMade || []).flatMap(([, o]) => o).includes("tile=stat tile - one value (number or text)")
      && (handMade || []).flatMap(([, o]) => o).includes("video=live video"));
const nodeChart = shapeGroups(mod.node, {
  node: chartNode({ kind: "area" }), registry: REGISTRY, meta: NODE_META,
  nodes: [chartNode({ kind: "area" })], edges: [], editable: true, errors: [],
  warnings: [], onChange: () => {}, functions: [], onDone: () => {} });
const builderChart = shapeGroups(mod.widget, {
  current: { id: "wf_g1", title: "x", kind: "area", source: "history", key: "fn_x_count",
             range: "24h", span: 1, grid: {}, template: "plain", bands: {} },
  meta: BUILDER_META, patch: () => {} });
// A chart's own family, ungrouped, the same on both (chart type picker).
check("a Chart's 'Show as' is the same on the node and in the Builder (its family, no groups)",
      nodeChart && JSON.stringify(nodeChart) === JSON.stringify(builderChart)
      && nodeChart.length === 0,
      `node    ${JSON.stringify(nodeChart)}\n      builder ${JSON.stringify(builderChart)}`);
const rule1 = { id: "r1", type: "analytics.frame", data: { name: "In frame" } };
const cardNodeOf = { id: "c1", type: "dashboard.card",
                     data: { ...REGISTRY["dashboard.card"].data, name: "C", kind: "tile" } };
const nodeCard = shapeGroups(mod.node, {
  node: cardNodeOf, registry: REGISTRY, meta: NODE_META, nodes: [cardNodeOf, rule1],
  edges: [{ id: "e", source: "r1", target: "c1" }], editable: true, errors: [],
  warnings: [], onChange: () => {}, functions: [], onDone: () => {} });
check("a Card's 'Show as' on the node is grouped too (Single value)",
      nodeCard && nodeCard.find(([g]) => g === "Single value")
      && nodeCard.find(([g]) => g === "Single value")[1].length === 4
      && nodeCard[0]?.[0] === "Single value"
      && nodeCard[0][1].length === 4, JSON.stringify(nodeCard));

// -- "Source" in plain words, stored values unchanged ------------------------
// (owner, 2026-10-12: "users do not understand the difference" between
// Stat (live) / Bucket (live) / Events (live) / Query (event DB) / History
// (sampled)).
{
  const src = byLabel(widgetFields({ id: "x2", title: "x", kind: "tile", source: "stat",
                                     key: "", span: 1, grid: {}, template: "plain",
                                     bands: {} }), "Source");
  check("Source offers the five plain names on the same stored values",
        JSON.stringify(src?.options) === JSON.stringify([
          ["stat", "Live value"], ["bucket", "Live counts by group"],
          ["events", "Recent events"], ["query", "Past events, counted"],
          ["history", "History over time"]]), JSON.stringify(src?.options));
  check("...and none of the old jargon is left",
        !JSON.stringify(src?.options).match(/\(live\)|event DB|sampled/));
}

// The final list, for the report.
console.log("\nshared card fields, in order:");
for (const key of mod.CARD_FIELD_ORDER) {
  const f = mod.CARD_FIELDS[key];
  console.log(`  ${key.padEnd(10)} ${f.label}`);
}
console.log(`  Work out options: ${mod.WORK_OUT.map(([v, w]) => `${w} (${v})`).join(", ")}`);
check("every question label either panel draws comes from the shared table",
      QUESTION.every((l) => SHARED.has(l)));

rmSync(OUT, { force: true });
if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall card-field checks passed");
