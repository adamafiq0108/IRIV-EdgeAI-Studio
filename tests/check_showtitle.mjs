// "Show title" on every Dashboard / Analytics card (Tok Aba, 2026-10-14),
// in the browser: Widget.jsx draws the title bar or not, the switch sits
// beside the Title on the workflow node and in the Builders
// (cardFields.ShowTitleField), and both Builders send it to the node.
//
//   1. defaults: every kind shows its title when nobody said, except the
//      Text and Image cards; an old widget renders exactly as before
//   2. off hides the title bar only -- the value, unit, caption, legend,
//      table and verdict words stay; an AI-off badge still says itself
//   3. on shows a Text / Image card's name as a normal title bar
//   4. the switch: in the Builders' Appearance section only (Tok Aba,
//      2026-10-14: the look is set in the Builders), not on the node; keyboard-usable,
//      Title text still editable; it writes show_title
//   5. both Builders send it to the node with the card's look
//
//   node tests/check_showtitle.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".showtitle-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image", "Event", "CustomEvent",
                 "MouseEvent", "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
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
        const D = () => createElement("div");
        const C = ({ children }) => children || null;
        export const Handle = D, BaseEdge = D, ReactFlow = D, Background = D, Controls = D, MiniMap = D;
        export const EdgeLabelRenderer = C, ReactFlowProvider = C, Panel = C;
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
        export const ConnectionLineType = { Bezier: "bezier" };`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [exposeInspector, shimXyflow],
  stdin: { contents: `
    import { createRoot } from "react-dom/client";
    import { act, createElement as h } from "react";
    import { MemoryRouter } from "react-router-dom";
    import Widget from "${p("src/components/widgets/Widget.jsx")}";
    import { WidgetInspector } from "${p("src/components/widgets/builderShared.jsx")}";
    import { Inspector } from "${p("src/pages/Workflow.jsx")}";
    import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
    export { titleShown } from "${p("src/components/widgets/cardFields.jsx")}";
    export function live(el, comp, props) {
      const root = createRoot(el);
      const C = comp === "widget" ? Widget : comp === "node" ? Inspector : WidgetInspector;
      const render = (next) => act(() => {
        root.render(h(MemoryRouter, null, h(ToastProvider, null, h(C, next))));
      });
      render(props);
      return { act, render, unmount: () => act(() => root.unmount()) };
    }`, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic",
  logLevel: "silent", define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const mount = (comp, props) => {
  const box = document.body.appendChild(document.createElement("div"));
  const r = m.live(box, comp, props);
  return { box, r, close: () => { r.unmount(); box.remove(); } };
};
const head = (box) => box.querySelector(".stat-label");

// -- 1/2/3. the card ----------------------------------------------------------------
const CARDS = [
  ["tile", { source: "stat", key: "k", data: { value: 42, sub: "since 6 am" } }, ["42", "since 6 am"]],
  ["line", { source: "history", key: "k", data: { labels: ["a", "b"], datasets: [{ label: "CAM 1", values: [1, 2] }] } }, []],
  ["table", { source: "events", data: { columns: ["Datetime", "Type"], rows: [["10:00", "alert"]] } }, ["Datetime", "alert"]],
  ["verdict", { source: "stat", key: "compliance", data: { value: 1, label: "COMPLIANT", state: "ok" } }, ["COMPLIANT"]],
];
for (const [kind, extra, keep] of CARDS) {
  const w = { id: `${kind}1`, title: `TITLE-${kind}`, kind, span: 2, grid: {}, template: "plain", ...extra };
  const dflt = mount("widget", { widget: w });
  check(`${kind}: the title shows by default (an old widget, unchanged)`,
        head(dflt.box)?.textContent.includes(`TITLE-${kind}`));
  dflt.close();
  const off = mount("widget", { widget: { ...w, show_title: false } });
  check(`${kind}: Show title off hides the title bar, nothing else`,
        !head(off.box) && !off.box.textContent.includes(`TITLE-${kind}`)
        && keep.every((x) => off.box.textContent.includes(x)), off.box.textContent.slice(0, 160));
  off.close();
}
{
  const w = { id: "t9", title: "Hidden name", kind: "tile", source: "stat", key: "k", span: 1, grid: {},
              template: "plain", show_title: false, data: { value: 3, ai_badge: true } };
  const v = mount("widget", { widget: w });
  check("hidden title: an AI OFFLINE badge still says itself, without the title",
        head(v.box) && v.box.textContent.includes("AI OFFLINE") && !v.box.textContent.includes("Hidden name"));
  v.close();
}
for (const kind of ["text_card", "image_card"]) {
  const w = { id: `${kind}1`, title: `NAME-${kind}`, kind, span: 2, grid: {}, template: "plain",
              body: "words", data: { static: true } };
  const dflt = mount("widget", { widget: w });
  check(`${kind}: no title by default`, !head(dflt.box) && !dflt.box.textContent.includes(`NAME-${kind}`));
  dflt.close();
  const on = mount("widget", { widget: { ...w, show_title: true } });
  check(`${kind}: Show title on draws its name as a normal title bar`,
        head(on.box)?.textContent.includes(`NAME-${kind}`));
  on.close();
}
check("the default per kind (titleShown)",
      m.titleShown("tile") && m.titleShown("line") && m.titleShown("verdict")
      && !m.titleShown("text_card") && !m.titleShown("image_card")
      && m.titleShown("text_card", true) && !m.titleShown("tile", false));

// -- 4. the switch -----------------------------------------------------------------------
const META = { sources: { stat: ["k"], bucket: [], events: [] }, history: {}, query: {},
               kinds: ["tile", "line"], catalog: [], capabilities: [] };
{
  let patched = null;
  const cur = { id: "t1", title: "Count", kind: "tile", source: "stat", key: "k", span: 1, grid: {},
                template: "plain", bands: {} };
  const v = mount("builder", { current: cur, meta: META, patch: (id, pt) => { patched = pt; } });
  const sw = v.box.querySelector('input[aria-label="Show title"]');
  const title = [...v.box.querySelectorAll("input.input")].find((x) => x.value === "Count");
  const section = sw && sw.closest("details.rail-section");
  check("Builder: a 'Show title' switch under Appearance, on by default, a real checkbox",
        sw && sw.checked && sw.getAttribute("role") === "switch" && sw.type === "checkbox" && title
        && section && section.querySelector("summary").textContent.startsWith("Appearance"),
        section && section.querySelector("summary").textContent);
  v.r.act(() => { sw.click(); });
  check("...turning it off writes show_title: false", patched && patched.show_title === false,
        JSON.stringify(patched));
  v.r.render({ current: { ...cur, show_title: false }, meta: META, patch: () => {} });
  const t2 = [...v.box.querySelectorAll("input.input")].find((x) => x.value === "Count");
  check("...and the Title stays editable with the title hidden", t2 && !t2.disabled);
  v.close();
}
{
  let wrote = null;
  const GD = { name: "Rejects", kind: "tile", metric: "", unit: "", window: "live", source_mode: "" };
  const node = { id: "c1", type: "dashboard.card", data: GD };
  const reg = { "dashboard.card": { category: "dashboard", label: "Card", data: GD } };
  // A node that already holds show_title keeps it (stored, compiled and
  // synced as before -- smoke_showtitle); only the Builders change it.
  const v = mount("node", { node: { ...node, data: { ...GD, show_title: false } }, registry: reg,
                            meta: { variables: {} }, nodes: [node], edges: [],
                            editable: true, errors: [], warnings: [], onChange: (pt) => { wrote = pt; },
                            functions: [], onDone: () => {} });
  check("node: no Show title switch on a dashboard card's node (one place only: the Builders)",
        !v.box.querySelector('input[aria-label="Show title"]') && wrote === null);
  v.close();
}

// -- 5. both Builders send it to the node --------------------------------------------------
for (const page of ["Builder.jsx", "AnalyticsBuilder.jsx"]) {
  const src = readFileSync(join(FRONT, "src", "pages", page), "utf8");
  check(`${page} sends show_title to the node with the card's look (only once set)`,
        src.includes('...(typeof w.show_title === "boolean" ? { show_title: w.show_title } : {})'));
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
