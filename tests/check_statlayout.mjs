// Where a stat sits in its card (Tok Aba, 2026-10-14): the Layout row --
// Alignment (left / centre / right) and Position (above / middle / below) --
// on the tile, the status pill and the verdict, on the workflow node and in
// both Builders (cardFields.StatLayoutFields), drawn by Widget.jsx.
//
//   1. an old tile, pill and verdict sit exactly where they always did
//   2. all nine alignment x position combinations on a tile place the value
//      block (value row, caption) and the placed cell as asked; status and
//      verdict follow alignment and position too; other kinds are untouched
//   3. the Layout row: Builder and node inspectors, radios (keyboard), write
//      {align} / {valign}; not offered on kinds without a stat block
//   4. both Builders send it to the node
//
//   node tests/check_statlayout.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".statlayout-test.cjs");

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
    export { statLayout } from "${p("src/components/widgets/cardFields.jsx")}";
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
const block = (box) => box.querySelector("[data-align]");
const cell = (box) => box.querySelector(".stat-card > div:last-child");
const FLEX = { left: "flex-start", center: "center", right: "flex-end",
               top: "flex-start", middle: "center", bottom: "flex-end" };
const tile = (extra = {}) => ({ id: "t1", title: "Count", kind: "tile", source: "stat", key: "k", span: 2,
                                grid: {}, template: "plain",
                                data: { value: 418, unit: "pcs", sub: "since 6 am" }, ...extra });

// -- 1. old cards unchanged ---------------------------------------------------------
{
  const v = mount("widget", { widget: tile(), fill: true });
  const b = block(v.box);
  check("an old tile: left and in the middle, as it always was",
        b && b.dataset.align === "left" && b.dataset.valign === "middle"
        && b.style.textAlign === "left" && cell(v.box).style.justifyContent === "center");
  v.close();
  for (const [kind, data] of [["status", { state: "ok", value: 1, label: "OK" }],
                              ["verdict", { state: "ok", label: "COMPLIANT" }]]) {
    const w = mount("widget", { widget: { id: kind, title: kind, kind, source: "stat", key: "k", span: 1,
                                          grid: {}, template: "plain", data }, fill: true });
    const bb = block(w.box);
    check(`an old ${kind}: centred and in the middle, as it always was`,
          bb && bb.dataset.align === "center" && bb.dataset.valign === "middle"
          && bb.style.alignItems === "center");
    w.close();
  }
  check("statLayout defaults", JSON.stringify(m.statLayout("tile")) === '{"align":"left","valign":"middle"}'
        && JSON.stringify(m.statLayout("status")) === '{"align":"center","valign":"middle"}'
        && JSON.stringify(m.statLayout("tile", "nope", "up")) === '{"align":"left","valign":"middle"}'
        // A Text card sat at the top, left: that is its default.
        && JSON.stringify(m.statLayout("text_card")) === '{"align":"left","valign":"top"}');
}

// -- 2. the nine combinations --------------------------------------------------------
let combos = 0;
for (const align of ["left", "center", "right"]) {
  for (const valign of ["top", "middle", "bottom"]) {
    const v = mount("widget", { widget: tile({ align, valign, data: {
      value: "A very long shift name that has to wrap inside the card", sub: "caption line" } }), fill: true });
    const b = block(v.box);
    const row = b && b.firstElementChild;
    const ok = b && b.style.textAlign === align && row.style.justifyContent === FLEX[align]
      && cell(v.box).style.justifyContent === FLEX[valign]
      && v.box.textContent.includes("caption line");
    if (ok) combos += 1;
    else check(`tile ${align} / ${valign}`, false, v.box.innerHTML.slice(0, 300));
    v.close();
  }
}
check("all nine alignment x position combinations place the tile's block as asked", combos === 9);
for (const [kind, data] of [["status", { state: "alert", value: 3, label: "ALERT" }],
                            ["verdict", { state: "alert", label: "VIOLATION" }]]) {
  const w = mount("widget", { widget: { id: kind, title: kind, kind, source: "stat", key: "k", span: 1, grid: {},
                                        template: "plain", align: "right", valign: "bottom", data }, fill: true });
  const bb = block(w.box);
  check(`${kind}: right and below`, bb.style.alignItems === "flex-end"
        && (kind === "verdict" ? bb.style.justifyContent === "flex-end"
                               : cell(w.box).style.justifyContent === "flex-end"));
  w.close();
}
{
  const g = mount("widget", { widget: { id: "g", title: "Dial", kind: "gauge", source: "stat", key: "k", span: 1,
                                        grid: {}, template: "plain", align: "right", valign: "top",
                                        data: { value: 3, max: 10 } }, fill: true });
  check("a gauge has no stat block to move (untouched)", !block(g.box));
  g.close();
}

// -- 3. the Layout row -------------------------------------------------------------------
const META = { sources: { stat: ["k"], bucket: [], events: [] }, history: {}, query: {},
               kinds: ["tile", "status", "verdict", "gauge", "line"], catalog: [], capabilities: [] };
const radios = (box, label) => [...box.querySelectorAll(`[role="radiogroup"][aria-label="${label}"] input[type="radio"]`)];
// Which Builder section a control sits in: Placement is under Appearance
// (Tok Aba, 2026-10-14: "placement must be under [Appearance] also").
const sectionOf = (el) => el?.closest("details")?.querySelector("summary")?.firstChild?.textContent || "";
for (const kind of ["tile", "status", "verdict"]) {
  let patched = null;
  const cur = { id: "x", title: "X", kind, source: "stat", key: kind === "verdict" ? "compliance" : "k",
                span: 1, grid: {}, template: "plain", bands: {} };
  const v = mount("builder", { current: cur, meta: META, patch: (id, pt) => { patched = pt; } });
  const al = radios(v.box, "Alignment");
  const pos = radios(v.box, "Position");
  const want = kind === "tile" ? "left" : "center";
  check(`Builder ${kind}: Alignment (${want}) and Position (Middle) as it draws today`,
        al.length === 3 && pos.length === 3 && al.find((x) => x.checked)?.value === want
        && pos.find((x) => x.checked)?.value === "middle"
        && pos.map((x) => x.getAttribute("aria-label")).join() === "Above,Middle,Below");
  check(`...under Appearance, Alignment first and Position directly under it`,
        sectionOf(al[0]) === "Appearance" && sectionOf(pos[0]) === "Appearance"
        && al[0].closest('[role="radiogroup"]').nextElementSibling
          === pos[0].closest('[role="radiogroup"]'),
        [sectionOf(al[0]), sectionOf(pos[0])].join());
  v.r.act(() => { al.find((x) => x.value === "right").click(); });
  const a = patched;
  v.r.act(() => { pos.find((x) => x.value === "top").click(); });
  check("...picking writes only what was picked", a && a.align === "right" && !("valign" in a)
        && patched.valign === "top" && !("align" in patched), JSON.stringify([a, patched]));
  v.close();
}
for (const kind of ["gauge", "line"]) {
  const v = mount("builder", { current: { id: "y", title: "Y", kind, source: kind === "line" ? "history" : "stat",
                                          key: "k", span: 1, grid: {}, template: "plain", bands: {} },
                               meta: META, patch: () => {} });
  check(`Builder ${kind}: no Layout row (no stat block)`, !radios(v.box, "Position").length);
  v.close();
}
{
  let wrote = null;
  const GD = { name: "Rejects", kind: "tile", metric: "", unit: "", window: "live", source_mode: "" };
  const node = { id: "c1", type: "dashboard.card", data: GD };
  const reg = { "dashboard.card": { category: "dashboard", label: "Card", data: GD } };
  const v = mount("node", { node, registry: reg, meta: { variables: {} }, nodes: [node], edges: [],
                            editable: true, errors: [], warnings: [], onChange: (pt) => { wrote = pt; },
                            functions: [], onDone: () => {} });
  check("node: no Alignment / Position on a dashboard card (Appearance is set in the Builders)",
        !radios(v.box, "Position").length && !radios(v.box, "Alignment").length);
  check("...nor a Placement group", !v.box.querySelector('[aria-label="Placement"]'));
  v.close();
}
{
  let patched = null;
  const cur = { id: "t", title: "Note", kind: "text_card", body: "hi", span: 1, grid: {},
                template: "plain", align: "right" };
  const v = mount("builder", { current: cur, meta: META, patch: (id, pt) => { patched = pt; } });
  const al = radios(v.box, "Alignment");
  const pos = radios(v.box, "Position");
  check("Text card: Alignment and Position under Appearance, not under Words",
        al.length === 3 && pos.length === 3 && sectionOf(al[0]) === "Appearance"
        && sectionOf(pos[0]) === "Appearance" && al.find((x) => x.checked)?.value === "right"
        && pos.find((x) => x.checked)?.value === "top", [sectionOf(al[0])].join());
  v.r.act(() => { pos.find((x) => x.value === "bottom").click(); });
  check("...still writing valign", patched && patched.valign === "bottom", JSON.stringify(patched));
  const n = [...v.box.querySelectorAll("summary")].find((x) => x.firstChild?.textContent === "Appearance")
    ?.querySelector(".rail-section-n")?.textContent;
  check("...and Appearance counts an alignment that differs from the default", n === "1", n);
  v.close();
}
{
  const v = mount("builder", { current: { id: "u", title: "U", kind: "tile", source: "stat", key: "k",
                                          span: 1, grid: {}, template: "plain", bands: {} },
                               meta: META, patch: () => {} });
  const n = [...v.box.querySelectorAll("summary")].find((x) => x.firstChild?.textContent === "Appearance")
    ?.querySelector(".rail-section-n");
  check("a tile at its default placement adds nothing to the Appearance count", !n, n?.textContent);
  v.close();
}

// -- 4. both Builders send it to the node ------------------------------------------------
for (const page of ["Builder.jsx", "AnalyticsBuilder.jsx"]) {
  const src = readFileSync(join(FRONT, "src", "pages", page), "utf8");
  check(`${page} sends align / valign to the node (only once set)`,
        src.includes("...(w.align ? { align: w.align } : {}),")
        && src.includes("...(w.valign ? { valign: w.valign } : {}),"));
  check(`${page} draws the shared inspector, so Placement is under its Appearance too`,
        src.includes("<WidgetInspector current={current} meta={meta} patch={patch}"));
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
