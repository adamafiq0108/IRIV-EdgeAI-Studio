// The table card (Analytics' Violation Log View, every "table" widget) keeps
// its title on its own row and its rows inside the card.
//
// Beta feedback: the title was drawn into the time ("When", now "Datetime") header row and the
// table ran off the card with nothing to scroll. A placed card centres its
// body, and a table taller than the card overflowed both ways -- the header
// up over the title, the rest out of the bottom. Pinned:
//   1. the title is its own element, before the table's scroll box
//   2. the rows live in a scroll box (overflow auto) that takes the space
//      under the title, and the body column starts at the TOP for a table
//   3. the header is a real <thead>, pinned (sticky) while the rows scroll
//   4. unplaced, the box is height-capped and scrolls too
//
//   node tests/check_tablecard.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".tablecard-test.cjs");

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

// The api client, under the test's control: a press is a POST, recorded.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          post(url, body) { globalThis.__posts.push([url, body]);
                            return Promise.resolve({ ok: true }); },
          get() { return Promise.resolve({}); },
          put() { return Promise.resolve({}); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
        export function saveBlob() {}
      `,
      resolveDir: FRONT, loader: "js",
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
        export const Handle = ({ id, type }) => createElement("div",
          { "data-handleid": id || "", "data-porttype": type });
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
import Widget from "${p("src/components/widgets/Widget.jsx")}";
export { act, h, Widget };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi, shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;

const rows = Array.from({ length: 200 }, (_, i) =>
  [`2026-10-0${1 + (i % 2)} 10:${String(i % 60).padStart(2, "0")}`, "violation",
   "high", `Bay ${i}`, "cam1"]);
const widget = { id: "vlog", kind: "table", title: "Violation Log View",
                 data: { columns: ["Datetime", "Type", "Severity", "Event", "Device"],
                         rows } };

function render(props, fn) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.Widget, props));
  try { return fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

render({ widget, fill: true }, (box) => {
  const card = box.querySelector(".stat-card");
  const title = card?.querySelector(".stat-label");
  const scroll = card?.querySelector('[data-wgt-table="1"]');
  check("the title is its own row, outside the table",
        title?.textContent.includes("Violation Log View")
        && !title.closest("table") && !scroll?.contains(title));
  check("...and comes BEFORE the table's box",
        title && scroll && (title.compareDocumentPosition(scroll)
                            & window.Node.DOCUMENT_POSITION_FOLLOWING));
  check("the rows scroll inside the card (overflow auto, takes the rest)",
        scroll?.style.overflow === "auto" && scroll.style.minHeight === "0px",
        scroll?.getAttribute("style"));
  check("...in a column that starts at the top for a table",
        scroll?.parentElement?.style.justifyContent === "flex-start",
        scroll?.parentElement?.getAttribute("style"));
  const ths = [...(card?.querySelectorAll("thead th") || [])];
  check("the header is a real <thead>, pinned while the rows scroll",
        ths.length === 5 && ths[0].textContent === "Datetime"
        && ths.every((th) => th.style.position === "sticky"));
  check("every row is there (scrolled, not cut)",
        card?.querySelectorAll("tbody tr").length === 200);
});
render({ widget }, (box) => {
  const scroll = box.querySelector('[data-wgt-table="1"]');
  check("unplaced: height-capped and scrolls too",
        scroll?.style.maxHeight === "320px" && scroll.style.overflow === "auto",
        scroll?.getAttribute("style"));
});
// A coloured card (white text on dark blue): the pinned header takes the
// CARD's colour and text colour -- it was a white strip under white words.
render({ widget: { ...widget, bg: "#1e3a8a" }, fill: true }, (box) => {
  const card = box.querySelector(".stat-card");
  const th = card?.querySelector("thead th");
  check("a coloured card's header is painted with the card's colour",
        card?.style.getPropertyValue("--wgt-head-bg") === "#1e3a8a"
        && th?.style.background.includes("var(--wgt-head-bg"),
        [card?.getAttribute("style"), th?.getAttribute("style")]);
  check("...and its text inherits the card's (readable) text colour",
        th?.style.color === "inherit" && /255, 255, 255|#fff/i.test(card.style.color),
        card?.style.color);
});
render({ widget, fill: true }, (box) => {
  const card = box.querySelector(".stat-card");
  check("a plain card keeps the surface colour (no variable set)",
        !card?.style.getPropertyValue("--wgt-head-bg"));
});
render({ widget: { ...widget, data: { columns: ["Datetime"], rows: [] } } }, (box) => {
  check("an empty table still says 'no data yet'",
        box.textContent.includes("no data yet"));
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall table-card checks passed");
dom.window.close();
process.exit(0);
