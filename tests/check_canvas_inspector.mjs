// The three inspector panels a chart, a preview and a Pre-process are edited
// through - pinned to what they OFFER, not to how they look.
//
// Each fact here is something that was wrong or missing:
//
//   1. a Chart node on the canvas showed ONE wire's settings however many
//      wires it drew, so the name and the colour of the second line could
//      only be reached by going to the page the card lands on. There is a row
//      per wired series now, and the fields the WIRE decides (where it reads
//      from, which key, which table) are the row's summary rather than
//      controls - the compiler rewrites them on every apply, so a control for
//      them would be a control whose value is thrown away;
//   2. the live preview toasted every rejection on every 250ms debounce, so
//      filling in a row stacked three identical sticky "Preview failed"
//      toasts over the panel being filled in. The same sentence twice in a
//      row is said once;
//   3. a per-series complaint from the server reaches the ROW it is about,
//      where the field that answers it is, rather than the page;
//   4. the Pre-process chain is an ORDERED list - blur then edges is not
//      edges then blur - so each step draws its own op's controls and moving
//      one keeps the rest where they were;
//   5. a step is switched off to SEE what it was doing, so a disabled one
//      keeps its name, its numbers and its place - it must not read as
//      deleted, and it must not be dimmed with a colour, which is what fails
//      contrast in one theme or the other;
//   6. a mask names the zones it masks to, and a zone drawn over another
//      camera is warned about here rather than left to the compile - it
//      hides the wrong part of the picture, which looks exactly like a
//      detector that has stopped working.
//
//   node tests/check_canvas_inspector.mjs
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
// esbuild, react, react-dom and jsdom are the FRONTEND's dependencies - the
// repo root has no node_modules of its own. Same arrangement as
// check_series_editor.mjs, for the same reason.
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".canvas-inspector-test.cjs");

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

// The preview endpoint, under the test's control: what this file is about is
// how the hook BEHAVES when the server says no, so the server is a function
// the case sets. Shimmed only for builderShared's own import, so the real
// client is still the one every other module resolves.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, (args) => (
      args.importer.replace(/\\/g, "/").endsWith("widgets/builderShared.jsx")
        ? { path: "api-shim", namespace: "shim" } : null));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__previewCalls = [];
        globalThis.__previewAnswer = () => Promise.resolve({ widgets: [] });
        export const api = {
          post(url, body) {
            globalThis.__previewCalls.push(body);
            return globalThis.__previewAnswer(body);
          },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
        export function saveBlob() {}
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

// React Flow's <Handle> cannot mount outside <ReactFlow>, and Workflow.jsx
// imports the library at module scope. Nothing under test here draws a
// handle; the shim keeps the import from dragging a canvas into jsdom. Same
// precedent as check_node_render.mjs.
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
import { act, createElement as h, useState } from "react";
import { GraphSeriesRows, PreprocessSteps } from "${p("src/pages/Workflow.jsx")}";
import { WidgetInspector, useLivePreview }
  from "${p("src/components/widgets/builderShared.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";

export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { html: () => el.innerHTML, unmount: () => act(() => root.unmount()) };
}
export function render(el, node) {
  const m = mount(el, node);
  const html = m.html();
  m.unmount();
  return html;
}
export const el = {
  seriesRows: (props) => h(GraphSeriesRows, props),
  preprocess: (props) => h(PreprocessSteps, props),
  inspector: (props) => h(WidgetInspector, props),
};

/** The hook, under a real ToastProvider: what a repeated rejection DOES is
 *  what is being pinned, and that is a toast on the page or no toast. */
function PreviewHarness({ widgets, editing }) {
  useLivePreview(widgets, true, undefined, editing);
  return null;
}
export function previewPage(el, widgets, editing) {
  const root = createRoot(el);
  const draw = (w, e) => act(() => {
    root.render(h(ToastProvider, null, h(PreviewHarness, { widgets: w, editing: e })));
  });
  draw(widgets, editing);
  return {
    edit: (w, e) => draw(w, e),
    html: () => el.innerHTML,
    unmount: () => act(() => root.unmount()),
  };
}
export { act };
`;

await build({
  plugins: [shimApi, shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});

const mod = req(OUT);
const host = document.body;
const run = (fn, ...args) => {
  const box = document.createElement("div");
  host.appendChild(box);
  try { return fn(box, ...args); } finally { host.removeChild(box); }
};

// React listens for the NATIVE input event and reads the value off its own
// tracker, so setting .value directly is invisible to it. This is the way
// round that every React testing library takes.
function type(input, value) {
  const set = Object.getOwnPropertyDescriptor(
    dom.window.HTMLInputElement.prototype, "value").set;
  set.call(input, value);
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
}

// ===========================================================================
// 1. the canvas Chart node: one row per wired series
// ===========================================================================
const REGISTRY = {
  "dashboard.graph": { category: "dashboard", label: "Chart" },
  "analytics.zone_count": { category: "analytics", label: "Zone Counter" },
  "output.dataset": { category: "output", label: "Data Table" },
  "dashboard.card": { category: "dashboard", label: "Card" },
};
const TABLES = [{ key: "passes", name: "passes",
                  columns: [{ name: "ts", type: "timestamp" },
                            { name: "count", type: "number" },
                            { name: "lane", type: "text" }] }];
const GRAPH = { id: "g1", type: "dashboard.graph" };
const NODES = [
  GRAPH,
  { id: "fn1", type: "analytics.zone_count", data: { name: "Lane 1" } },
  { id: "ds1", type: "output.dataset", data: { dataset: "passes" } },
  // A Card asking the recorded event store -- what the Event Card was, now
  // the Card's own Source setting.
  { id: "ev1", type: "dashboard.card",
    data: { name: "Violations", source_mode: "events", agg: "count" } },
];
// One wire on the plain port (every chart drawn before the named ports
// existed is wired this way) and two on named ones.
const EDGES = [
  { id: "e1", source: "fn1", target: "g1", targetHandle: "in" },
  { id: "e2", source: "ds1", sourceHandle: "o_count", target: "g1", targetHandle: "s2" },
  { id: "e3", source: "ev1", target: "g1", targetHandle: "s3" },
];
const seriesProps = (d = {}, onChange = () => {}) => ({
  node: GRAPH, nodes: NODES, edges: EDGES, registry: REGISTRY,
  d: { kind: "line", ...d }, dbTables: TABLES, editable: true, onChange,
});
const rowsHtml = run(mod.render, mod.el.seriesRows(seriesProps()));

check("a chart node lists one row per wired series",
      (rowsHtml.match(/Series name for input \d/g) || []).length === 3,
      "expected three rows for three wires, got "
      + (rowsHtml.match(/Series name for input \d/g) || []).length);
check("...each row named after the wire that feeds it",
      rowsHtml.includes("Lane 1") && rowsHtml.includes("count")
      && rowsHtml.includes("Violations"),
      "a row did not say where its line comes from");
// THE WIRE IS THE TRUTH. Offering a source or a key here would offer a
// control the next compile overwrites.
check("...and never offers the fields the WIRE decides",
      !rowsHtml.includes("Where from") && !rowsHtml.includes("Data key")
      && !rowsHtml.includes("Data table") && !rowsHtml.includes(">Value<"),
      "the canvas row offered a wire-decided field as editable");
check("...but does offer the name of every line",
      (rowsHtml.match(/Series name for input \d/g) || []).length === 3);
// A line's COLOUR is look, and a chart's look is set in the Analytics or
// Dashboard Builder only (owner, 2026-10-12): no swatch, no palette pin, no
// reset on the node -- one hint line in their place.
check("...and no colour control for any line",
      !/type="color"/.test(rowsHtml) && !rowsHtml.includes("Colour for series")
      && !rowsHtml.includes("Palette colour") && !rowsHtml.includes(">reset<"),
      "a colour control is still on the canvas row");
check("...one hint line says where the colours are set",
      rowsHtml.split("Colours are set in the Analytics or Dashboard Builder.")
        .length === 2);
// THE CHART BUILDER (2026-10-13): what a table input used to be asked on its
// own row -- Work out, One point per, Date column, Period -- is asked ONCE for
// the chart (X axis, Y axis, Advanced: Date column), and a row keeps its
// name, its Show toggle and a collapsed override.
check("a data-table input's question is the chart's, asked once",
      ["X axis", "Y axis", "Date column"].every((f) => rowsHtml.includes(f))
      && !["Work out", "One point per", "Period"].some((f) => rowsHtml.includes(`>${f}<`)),
      "the per-input question rows are still drawn");
// ...a recorded-events Card's wire does not: the card IS the question.
check("...and a recorded-events Card row sends you to the card, not a second copy",
      rowsHtml.includes("open that one to change it")
      && !rowsHtml.includes("Aggregation"),
      "the query row offered a second answer to the card's own question");
// Every input -- a live value included -- has its own collapsed override of
// the chart's Y calculation, and nothing else of its own.
check("every input row has a collapsed override and a Show toggle",
      (rowsHtml.match(/Override for this input/g) || []).length === 3
      && (rowsHtml.match(/aria-label="Show input \d on the chart"/g) || []).length === 3
      && !rowsHtml.includes("Each point shows"));
const compare = run(mod.render, mod.el.seriesRows(seriesProps({ kind: "donut" })));
// "Category field" since the chart type picker (2026-10-14, mockup 64).
check("...and a comparison chart has a Category field and a Value, no axes",
      compare.includes(">Category field<") && compare.includes(">Value<")
      && !compare.includes(">X axis<"));

// The edit lands on the NODE, under the series' own id - a wf_* card is
// rebuilt from its node on every compile, so anything written onto the widget
// would last until the next apply and no longer.
let wrote = null;
run((box) => {
  const m = mod.mount(box, mod.el.seriesRows(seriesProps(
    { series: { s3: { label: "Violations per hour", color: "#123456" } } },
    (patch) => { wrote = patch; })));
  const inputs = [...box.querySelectorAll("input")]
    .filter((i) => (i.getAttribute("aria-label") || "").startsWith("Series name for"));
  type(inputs[1], "Lane 2 total");
  m.unmount();
});
check("naming a line writes onto the node, under that series' id",
      !!wrote && !!wrote.series && wrote.series.s2
      && wrote.series.s2.label === "Lane 2 total",
      JSON.stringify(wrote));
// The ids are per SERIES, not per row index, so what the other lines were
// called and coloured is not touched by naming this one.
check("...and what the other lines were called and coloured is untouched",
      !!wrote && wrote.series.s3
      && wrote.series.s3.label === "Violations per hour"
      && wrote.series.s3.color === "#123456",
      JSON.stringify(wrote));

// Nothing wired: an empty list would read as "this node has no inputs".
const empty = run(mod.render, mod.el.seriesRows({
  ...seriesProps(), edges: [] }));
check("an unwired chart says what a wire would do, rather than nothing",
      empty.includes("Nothing is wired in yet"));

// ===========================================================================
// 2. the preview does not toast the same sentence twice
// ===========================================================================
const CHART = {
  id: "w1", title: "T", kind: "line", source: "history", key: "zone_a_count",
  range: "24h", query: {}, series: [{ id: "s1", source: "history", key: "a" }],
};
const HOLED = {
  ...CHART, id: "w2",
  series: [{ id: "s1", source: "dataset", dataset: "", query: { agg: "count" } }],
};

async function settle(ms = 400) {
  await new Promise((r) => setTimeout(r, ms));
  await new Promise((r) => setTimeout(r, 0));
}

{
  globalThis.__previewCalls = [];
  globalThis.__previewAnswer = () => Promise.reject(
    new Error("widget 4: a dataset widget needs a dataset and its columns"));
  const box = document.createElement("div");
  host.appendChild(box);
  const page = mod.previewPage(box, [CHART]);
  await settle();
  // Three edits, three debounced requests, three identical rejections.
  page.edit([{ ...CHART, title: "T2" }]);
  await settle();
  page.edit([{ ...CHART, title: "T3" }]);
  await settle();
  const toasts = box.querySelectorAll(".toast-error").length;
  check("a repeated rejection is toasted once, not once per keystroke",
        toasts === 1, `${toasts} toasts for the same message`);
  check("...and the technical reason travels with it, underneath",
        box.innerHTML.includes("a dataset widget needs a dataset")
        && box.innerHTML.includes("Preview failed"),
        "the friendly headline or the detail was missing");

  // A DIFFERENT reason is news, and is said.
  globalThis.__previewAnswer = () => Promise.reject(new Error("HTTP 500"));
  page.edit([{ ...CHART, title: "T4" }]);
  await settle();
  check("...while a different reason is news and is said",
        box.querySelectorAll(".toast-error").length === 2);

  // And a success re-arms it: the next failure, even the same one, is news.
  globalThis.__previewAnswer = () => Promise.resolve({ widgets: [] });
  page.edit([{ ...CHART, title: "T5" }]);
  await settle();
  globalThis.__previewAnswer = () => Promise.reject(new Error("HTTP 500"));
  page.edit([{ ...CHART, title: "T6" }]);
  await settle();
  check("...and a preview that worked re-arms the next one",
        box.querySelectorAll(".toast-error").length === 3);
  page.unmount();
  host.removeChild(box);
}

{
  // The card being EDITED with a half-filled row is not asked about at all:
  // the operator is on their way out of that state, and a page-wide alarm
  // about it is noise over the panel that fixes it.
  globalThis.__previewCalls = [];
  globalThis.__previewAnswer = () => Promise.resolve({ widgets: [] });
  const box = document.createElement("div");
  host.appendChild(box);
  const page = mod.previewPage(box, [CHART, HOLED], "w2");
  await settle();
  const asked = globalThis.__previewCalls.at(-1) || {};
  const ids = (asked.widgets || []).map((w) => w.id);
  check("the half-filled card under the cursor is left out of the request",
        ids.includes("w1") && !ids.includes("w2"), JSON.stringify(ids));
  check("...and every other card on the page still previews",
        ids.length === 1);
  check("...with nothing toasted about it",
        box.querySelectorAll(".toast-error").length === 0);
  page.unmount();
  host.removeChild(box);
}

// ===========================================================================
// 3. a per-series warning reaches its row
// ===========================================================================
const META = {
  sources: { stat: ["zone_a_count"], bucket: [], events: [] },
  kinds: ["line"], history: { ranges: ["24h"], intervals: [""], aggs: ["avg"] },
  query: { aggregations: ["count"], buckets: [], ranges: [], columns: [] },
  catalog: [], capabilities: [], editable: true,
};
const WARNED = {
  ...CHART,
  series: [{ id: "s1", source: "history", key: "zone_a_count" },
           { id: "s2", source: "dataset", dataset: "", query: { agg: "count" } }],
};
const warnedHtml = run(mod.render, mod.el.inspector({
  current: WARNED, meta: META, patch: () => {},
  preview: [{ id: "w1", warnings: [
    { id: "s2", label: "Series 2", message: "Pick the data table this input reads." }] }],
}));
check("a server warning about one series lands on that series' row",
      warnedHtml.includes("Pick the data table this input reads."),
      "the row said nothing about why it is not drawing");
check("...in the theme's warning colour, not a colour of its own",
      /class="small c-warn"/.test(warnedHtml),
      "a status colour written inline cannot pass contrast in both themes");
const quiet = run(mod.render, mod.el.inspector({
  current: CHART, meta: META, patch: () => {}, preview: [{ id: "w1" }] }));
check("...and a card with nothing wrong carries no warning",
      !quiet.includes("c-warn"));

// ===========================================================================
// 4. the Pre-process chain: every op's controls, and the operator's order
// ===========================================================================
const preHtml = run(mod.render, mod.el.preprocess({
  d: { steps: [] }, editable: true, onChange: () => {} }));
check("an empty chain says the picture passes through unchanged",
      preHtml.includes("No steps - the picture passes through unchanged."));

const STEPS = [
  { op: "grayscale" },
  { op: "exposure", gain: 1.4, gamma: 0.8 },
  { op: "sharpness", amount: 2, ksize: 5 },
  { op: "blur", kind: "gaussian", ksize: 5, sigma: 1.5 },
  { op: "sobel", dx: 1, dy: 0, ksize: 3, scale: 1, normalize: false },
];
const chain = run(mod.render, mod.el.preprocess({
  d: { steps: STEPS, preview: true }, editable: true, onChange: () => {} }));
for (const [op, fields] of [["Grayscale", []],
                            ["Exposure", ["Gain", "Gamma"]],
                            ["Sharpness", ["Amount"]],
                            ["Blur", ["Kind", "Kernel", "Sigma"]],
                            ["Edges (Sobel)", ["Across", "Down", "Scale",
                                               "Normalize"]]]) {
  check(`${op}: the step draws with its own controls`,
        chain.includes(op) && fields.every((f) => chain.includes(f)),
        `missing one of ${fields.join(", ")}`);
}
check("every number is a slider WITH its value beside it",
      (chain.match(/type="range"/g) || []).length === 10
      && chain.includes("1.4") && chain.includes("0.8"),
      "a slider in a rail this narrow cannot be read back on its own");
// A KERNEL is centred on the pixel it replaces, so it is odd. The server
// rounds an even one UP, which would mean a panel showing a number the box
// is not running - so the slider cannot land on one in the first place.
const kernels = [...chain.matchAll(/min="(\d+)" max="(\d+)" step="(\d+)"/g)]
  .map((m) => m.slice(1).map(Number))
  .filter(([lo, , st]) => st === 2 || lo % 2 === 1);
check("every kernel slider steps by 2 from an odd start, so it can only be odd",
      kernels.length === 3 && kernels.every(([lo, hi, st]) => st === 2
                                            && lo % 2 === 1 && hi % 2 === 1),
      JSON.stringify(kernels));
// A number the operator is not told is a number they find out about from the
// frame rate. Said as a REFERENCE figure, because that is what it is.
check("the panel adds up what the chain costs a frame",
      /~\d+\.\d ms per frame at 720p/.test(chain)
      && chain.includes("not a measurement of this box"),
      "the cost of the chain was invisible until the frame rate dropped");
const heavy = run(mod.render, mod.el.preprocess({
  d: { steps: [{ op: "sobel", ksize: 7 }, { op: "mask", fill: "blur", zones: [] }] },
  editable: true, onChange: () => {} }));
check("...and warns when it will not fit the frame budget",
      heavy.includes("~17.0 ms per frame")
      && heavy.includes("likely to cost frame rate")
      && /class="small c-warn"/.test(heavy),
      "a chain over the budget read the same as one well inside it");
// The measured fact everyone gets backwards: a box blur is four times the
// cost of a gaussian on this build.
check("...and nothing calls a box blur the cheap one, because it is not",
      chain.includes("0.9 ms at k5") && chain.includes("4.1 ms at k5")
      && !/box[^<]*cheap/i.test(chain),
      "the panel repeated the assumption instead of the measurement");
// Switched off, a step costs nothing - the readout follows the switch.
const halfOff = run(mod.render, mod.el.preprocess({
  d: { steps: [{ op: "grayscale", enabled: true },
               { op: "sobel", ksize: 7, enabled: false }] },
  editable: true, onChange: () => {} }));
check("...counting only the steps that are switched on",
      halfOff.includes("~0.8 ms per frame"),
      "a switched-off step was still being charged for");
check("the preview toggle says what it changes and what it does not",
      chain.includes("never what is recorded")
      && chain.includes("the detector sees"),
      "the note did not say the detector reads the processed frame either way");

let reordered = null;
run((box) => {
  const m = mod.mount(box, mod.el.preprocess({
    d: { steps: STEPS }, editable: true,
    onChange: (patch) => { reordered = patch; } }));
  const later = [...box.querySelectorAll("button")]
    .find((b) => b.getAttribute("aria-label") === "Move Blur later");
  later.click();
  m.unmount();
});
check("moving a step swaps it with its neighbour and leaves the rest alone",
      !!reordered
      && reordered.steps.map((s) => s.op).join(",")
         === "grayscale,exposure,sharpness,sobel,blur",
      JSON.stringify((reordered || {}).steps?.map((s) => s.op)));
check("...and the step's own numbers travel with it",
      !!reordered && reordered.steps[4].ksize === 5
      && reordered.steps[4].kind === "gaussian",
      "a reorder rebuilt a step instead of moving it");
// The first step cannot go earlier and the last cannot go later: a button
// that does nothing is worse than no button.
let moved = "not called";
run((box) => {
  const m = mod.mount(box, mod.el.preprocess({
    d: { steps: STEPS }, editable: true,
    onChange: () => { moved = "called"; } }));
  const first = [...box.querySelectorAll("button")]
    .find((b) => b.getAttribute("aria-label") === "Move Grayscale earlier");
  check("the first step's 'move earlier' is disabled, not merely inert",
        first.disabled === true, "a button that does nothing is worse than none");
  first.click();
  m.unmount();
});
check("...and nothing is written when it is pressed anyway",
      moved === "not called");

// A step is SWITCHED OFF to see what it was doing, and switched back a
// second later - so it keeps its card, its numbers and its place in the
// order, and only its controls go grey.
let toggled = null;
run((box) => {
  const m = mod.mount(box, mod.el.preprocess({
    d: { steps: STEPS }, editable: true,
    onChange: (patch) => { toggled = patch; } }));
  const box2 = [...box.querySelectorAll("input[type=checkbox]")]
    .find((c) => c.getAttribute("aria-label") === "Disable Blur");
  box2.click();
  m.unmount();
});
check("the header switch writes `enabled` on that step alone",
      !!toggled && toggled.steps[3].enabled === false
      && toggled.steps[3].ksize === 5
      && toggled.steps.filter((s) => s.enabled === false).length === 1,
      JSON.stringify((toggled || {}).steps));

const offCard = run(mod.render, mod.el.preprocess({
  d: { steps: [{ op: "blur", kind: "box", ksize: 5, sigma: 0, enabled: false }] },
  editable: true, onChange: () => {} }));
check("...and a switched-off step still reads as itself, not as deleted",
      offCard.includes("Blur") && offCard.includes("Kernel")
      && offCard.includes("Switched off") && offCard.includes("Its settings are"),
      "a disabled step lost its name or its numbers");
check("...with its controls disabled rather than painted a dim colour",
      (offCard.match(/disabled=""/g) || []).length >= 3
      && !/opacity/.test(offCard),
      "a dimmed step must not be dimmed by a colour: that is what fails "
      + "contrast in one theme or the other");

// ===========================================================================
// 5. the mask step: zones, inverted or not, and what fills the rest
// ===========================================================================
const ZONES = [
  { id: "za", name: "Zone A", kind: "area", camera: "cam1", enabled: true },
  { id: "zb", name: "Zone B", kind: "area", camera: "cam1", enabled: true },
  { id: "zc", name: "Far door", kind: "area", camera: "cam2", enabled: true },
];
const CAMS = [{ id: "cam1", name: "Front" }, { id: "cam2", name: "Yard" }];
const maskProps = (step, onChange = () => {}) => ({
  d: { steps: [{ op: "mask", zones: [], invert: false, fill: "black",
                 enabled: true, ...step }] },
  editable: true, zoneList: ZONES, cameras: CAMS, camera: "cam1", onChange,
});
const maskHtml = run(mod.render, mod.el.preprocess(
  maskProps({ zones: ["za", "zb"] })));
check("a mask step names the zones it is masking to, on the card",
      maskHtml.includes("mask: Zone A, Zone B"),
      "the summary did not name the zones");
// The canvas NODE draws the same step (stepLabel in nodes.jsx) and says
// "inverted" for it. One step read in two places, described one way.
check("...and says so the same way the node on the canvas does",
      run(mod.render, mod.el.preprocess(maskProps({ zones: ["za"], invert: true })))
        .includes("mask: Zone A inverted"));
check("...offers the zone picker every zone the box has drawn",
      maskHtml.includes("Far door") && maskHtml.includes("Add a zone to the mask"),
      "the mask did not reuse the zone picker");
check("...and the picked ones are not offered a second time",
      (maskHtml.match(/>Zone A</g) || []).length === 1);
check("...with the invert toggle and the fill choice beside them",
      maskHtml.includes("Invert the mask") && maskHtml.includes("Fill the hidden part")
      && maskHtml.includes("black - 0.8 ms a frame")
      && maskHtml.includes("7.8 ms a frame"),
      "a mask could not be inverted or filled");
// The blur's kernel belongs to the blur: offered under the other fill it is
// a control that does nothing, which teaches people to ignore the panel.
check("...and the blur kernel appears only once a blurred fill is chosen",
      !maskHtml.includes("Blur kernel")
      && run(mod.render, mod.el.preprocess(maskProps({ fill: "blur" })))
           .includes("Blur kernel"));
// The one thing an operator will reach for the wrong node over.
check("...and says how this differs from a Zone Filter",
      maskHtml.includes("drops detections AFTER")
      && maskHtml.includes("BEFORE the AI runs"),
      "nothing told them which of the two nodes they want");
// A zone drawn over another camera hides the wrong part of the picture, and
// looks exactly like a detector that stopped working.
const stray = run(mod.render, mod.el.preprocess(maskProps({ zones: ["za", "zc"] })));
// Cameras are named the way the whole canvas names them (camLabel):
// "Camera 2", not the label somebody typed into Settings.
check("a zone from another camera is warned about before the save",
      stray.includes("Far door") && stray.includes("Camera 2")
      && stray.includes("Camera 1")
      && /class="small c-warn"/.test(stray),
      "the mismatch was left for the compiler to find");
check("...and a zone from this camera is not",
      !/class="small c-warn"/.test(maskHtml));

let masked = null;
run((box) => {
  const m = mod.mount(box, mod.el.preprocess(
    maskProps({ zones: [] }, (patch) => { masked = patch; })));
  const picker = [...box.querySelectorAll("select")]
    .find((sel) => sel.getAttribute("aria-label") === "Add a zone to the mask");
  const set = Object.getOwnPropertyDescriptor(
    dom.window.HTMLSelectElement.prototype, "value").set;
  set.call(picker, "zb");
  picker.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  m.unmount();
});
check("picking a zone writes its id onto the step",
      !!masked && masked.steps[0].zones.join(",") === "zb"
      && masked.steps[0].fill === "black" && masked.steps[0].invert === false,
      JSON.stringify((masked || {}).steps));

// A fresh mask is born with the whole contract on it, not half of it.
let born = null;
run((box) => {
  const m = mod.mount(box, mod.el.preprocess({
    d: { steps: [] }, editable: true, zoneList: ZONES, cameras: CAMS,
    onChange: (patch) => { born = patch; } }));
  const adder = [...box.querySelectorAll("select")]
    .find((sel) => sel.getAttribute("aria-label") === "Add a step");
  const set = Object.getOwnPropertyDescriptor(
    dom.window.HTMLSelectElement.prototype, "value").set;
  set.call(adder, "mask");
  adder.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  m.unmount();
});
check("a step added from the picker is born with every field it declares",
      !!born && born.steps[0].op === "mask" && born.steps[0].enabled === true
      && Array.isArray(born.steps[0].zones) && born.steps[0].fill === "black",
      JSON.stringify((born || {}).steps));

rmSync(OUT, { force: true });
if (bad) {
  console.log(`\n${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
