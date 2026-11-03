// The Class Filter node's On / Off (Tok Aba, 2026-10-14), ONE state with the
// dashboard's Class filter card (Off = paused; services/class_filter.py):
//
//   1. the switch at the top right of the node's Properties header, the state
//      pill to its left, On for a node saved before it existed; it writes
//      only `running`; a real switch (role, aria-checked, keyboard); read-only
//      when the canvas is
//   2. the node face shows Off -- set here or paused from the dashboard --
//      and the Properties summary says so
//   3. the dashboard card's switch says it is the node's state
//
//   node tests/check_classfilter_switch.mjs
import { readFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".classfilter-switch-test.cjs");

// The registry, from the one place it is true (as check_node_render does).
const DUMP = "import json,sys\n"
  + "from core.services.workflow import REGISTRY\n"
  + "sys.stdout.write(json.dumps(REGISTRY))";
let registry = null;
for (const py of ["py", "python", "python3"]) {
  const r = spawnSync(py, ["-c", DUMP], { cwd: ROOT, encoding: "utf8" });
  if (r.status === 0 && r.stdout) {
    try { registry = JSON.parse(r.stdout); } catch { /* partial output */ }
    if (registry) break;
  }
}
if (!registry || !Object.keys(registry).length) {
  console.log("skip: no python interpreter to dump the workflow REGISTRY");
  process.exit(77);
}

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
global.fetch = () => new Promise(() => {});

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// The node Inspector is internal to Workflow.jsx: exported for this bundle.
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
        export function Handle({ type, id }) {
          return createElement("div", { className: "test-port", "data-k": type + ":" + (id || "") });
        }
        const D = () => createElement("div");
        const C = ({ children }) => children || null;
        export const BaseEdge = D, ReactFlow = D, Background = D, Controls = D, MiniMap = D;
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
    import { WorkflowCtx, buildNodeTypes } from "${p("src/workflow/nodes.jsx")}";
    import { Inspector } from "${p("src/pages/Workflow.jsx")}";
    import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
    import { MemoryRouter } from "react-router-dom";
    export * as S from "${p("src/workflow/nodeSummary.js")}";
    export { buildNodeTypes };
    function mount(el, tree) {
      const root = createRoot(el);
      const quiet = console.error; console.error = () => {};
      try { act(() => { root.render(tree); }); } finally { console.error = quiet; }
      return root;
    }
    export function drawFace(el, Comp, props, ctx) {
      const root = mount(el, h(WorkflowCtx.Provider, { value: ctx }, h(Comp, props)));
      const html = el.innerHTML;
      act(() => root.unmount());
      return html;
    }
    export function openInspector(el, props) {
      // A Data Table's panel links to the Database page: a router is needed.
      const tree = (pr) => h(MemoryRouter, null, h(ToastProvider, null, h(Inspector, pr)));
      const root = mount(el, tree(props));
      return {
        rerender: (next) => act(() => { root.render(tree(next)); }),
        close: () => act(() => root.unmount()),
      };
    }`, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic",
  logLevel: "silent", define: { "process.env.NODE_ENV": '"development"' },
});
const { S, buildNodeTypes, drawFace, openInspector } = req(OUT);
const VARIABLES = { tables: [], cameras: [], zone_list: [], event_fields: [], event_values: {} };
const inspectorProps = (node, onChange = () => {}, editable = true) => ({
  node, registry, meta: { variables: VARIABLES }, nodes: [node], edges: [], editable,
  errors: [], warnings: [], onChange, functions: [], onDone: () => {} });
const filter = (data) => ({ id: "f1", type: "filter.class",
                            data: { ...(registry["filter.class"].data || {}), classes: ["person"],
                                    name: "people", ...data } });

// -- the inspector header -------------------------------------------------------------
{
  let wrote = null;
  const el = document.body.appendChild(document.createElement("div"));
  const m = openInspector(el, inspectorProps(filter({}), (pt) => { wrote = pt; }));
  const sw = el.querySelector('button[role="switch"][aria-label="Class Filter on"]');
  const title = el.querySelector(".section-title");
  const pill = sw && sw.parentElement.querySelector(".state-pill");
  check("Class Filter panel: an On / Off switch at the top right of its header",
        sw && title.contains(sw) && sw.parentElement.style.marginLeft === "auto");
  check("...a node saved before the switch reads On (pill left of the switch, green)",
        sw.getAttribute("aria-checked") === "true" && pill.textContent === "On"
        && pill.classList.contains("is-on") && pill.nextElementSibling === sw);
  m.rerender(inspectorProps(filter({}), (pt) => { wrote = pt; }));
  el.querySelector('button[role="switch"][aria-label="Class Filter on"]').click();
  check("...switching it writes running: false on the node, nothing else",
        JSON.stringify(wrote) === '{"running":false}', JSON.stringify(wrote));
  m.rerender(inspectorProps(filter({ running: false }), (pt) => { wrote = pt; }));
  const off = el.querySelector('button[role="switch"][aria-label="Class Filter on"]');
  check("...Off reads Off (plain pill), and switching back writes running: true",
        off.getAttribute("aria-checked") === "false"
        && off.parentElement.querySelector(".state-pill").textContent === "Off"
        && !off.parentElement.querySelector(".state-pill").classList.contains("is-on"));
  off.click();
  check("...and switching it back On writes running: true", wrote && wrote.running === true);
  check("a native button: Space / Enter switch it, Tab reaches it",
        off.tagName === "BUTTON" && off.type === "button");
  m.rerender(inspectorProps(filter({}), () => {}, false));
  check("read-only: the switch is shown but cannot be used",
        el.querySelector('button[role="switch"][aria-label="Class Filter on"]').disabled);
  m.close(); el.remove();
  const other = document.body.appendChild(document.createElement("div"));
  const o = openInspector(other, inspectorProps({ id: "p", type: "filter.preprocess", data: { steps: [] } }));
  check("a Pre-process (the other filter) has no such switch",
        !other.querySelector('button[aria-label="Class Filter on"]'));
  o.close(); other.remove();
}

// -- the node face and the summary ----------------------------------------------------------
const nodeTypes = buildNodeTypes(registry);
const ctx = (paused) => ({ registry, values: {}, errors: {}, warnings: {}, variables: VARIABLES,
  resizeNode: null, incoming: new Set(), wired: new Set(), offNodes: new Map(), fanIn: new Map(),
  edges: [], objClasses: new Map(), filtersPaused: new Set(paused ? ["f1"] : []) });
const face = (data, paused) => {
  const el = document.body.appendChild(document.createElement("div"));
  const html = drawFace(el, nodeTypes["filter.class"], { id: "f1", type: "filter.class",
    data: filter(data).data, selected: false }, ctx(paused));
  el.remove();
  return html;
};
check("face: On shows no Off badge", !face({}).includes("data-filter-off"));
check("face: Off shows an Off badge (the rules after it are paused)",
      face({ running: false }).includes("data-filter-off") && face({ running: false }).includes("Off"));
check("face: a pause set from the dashboard shows the same badge",
      face({}, true).includes("data-filter-off"));
check("summary: Off says so in the Properties box",
      S.nodeSummary("filter.class", { classes: ["person"], running: false }, registry["filter.class"]).does
        === "off · the rules after it are paused"
      && S.nodeSummary("filter.class", { classes: ["person"] }, registry["filter.class"]).does === "keep only person");

// -- the dashboard card says the same state ---------------------------------------------------
const widgetSrc = readFileSync(join(FRONT, "src/components/widgets/Widget.jsx"), "utf8");
check("the dashboard card's switch says it is the node's state too",
      widgetSrc.includes("Paused - Off on its workflow node too"));

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
