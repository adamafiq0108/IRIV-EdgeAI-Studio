// The "AI running" dot, rendered: green / red with the reason as its tooltip,
// never a control, on the Workflow toolbar and on every Detection Stream --
// and, with the AI off, the dot is the ONLY sign of it: the Detection Stream
// and the nodes after it look exactly as they do while detecting, no dimming
// and no tag (Tok Aba 2026-10-05). tests/smoke_aiswitch.py pins the app and
// the master side (those nodes still do not run with the AI off).
//
//   node tests/check_aidot.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".aidot-test.cjs");

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

// The Inspector is internal to Workflow.jsx: exported for this bundle only.
const exposeInspector = {
  name: "expose-inspector",
  setup(b) {
    b.onLoad({ filter: /pages[\\/]Workflow\.jsx$/ }, (args) => ({
      contents: readFileSync(args.path, "utf8") + "\nexport { Inspector };\n",
      loader: "jsx", resolveDir: dirname(args.path),
    }));
  },
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
import { WorkflowCtx, buildNodeTypes } from "${p("src/workflow/nodes.jsx")}";
import AiDot, { aiOf } from "${p("src/components/AiDot.jsx")}";
export { act, h, WorkflowCtx, buildNodeTypes, AiDot, aiOf };
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
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;

function render(node, fn) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, node);
  try { return fn(box); } finally {
    r.unmount(); document.body.removeChild(box);
  }
}
const dot = (box) => box.querySelector('[role="status"][data-ai]');

// -- 1. the dot itself ------------------------------------------------------------
render(h(m.AiDot, { ai: { ok: true, reason: "" }, label: true }), (box) => {
  const d = dot(box);
  check("green: 'AI Detection' (title 'AI running')", d?.getAttribute("data-ai") === "on"
        && d.getAttribute("title") === "AI running"
        && box.textContent.includes("AI Detection") && !box.textContent.includes("AI running"),
        box.innerHTML);
  check("...the label is green", /color:\s*var\(--ok-text/.test(box.innerHTML), box.innerHTML);
  check("...drawn green", (box.innerHTML.includes("34, 197, 94") || box.innerHTML.includes("#22c55e")));
});
render(h(m.AiDot, { ai: { ok: false, reason: "AI detection is off" }, label: true }),
       (box) => {
  const d = dot(box);
  check("red: 'AI off', the reason in the tooltip",
        d?.getAttribute("data-ai") === "off"
        && d.getAttribute("title") === "AI off - AI detection is off"
        && box.textContent.includes("AI Detection") && !box.textContent.includes("AI off"),
        d?.getAttribute("title"));
  check("...the label is red", /color:\s*var\(--alert/.test(box.innerHTML), box.innerHTML);
  check("...drawn red", (box.innerHTML.includes("239, 68, 68") || box.innerHTML.includes("#ef4444")));
});
render(h(m.AiDot, { ai: { ok: false, reason: "inference failed: HAILO_TIMEOUT" } }),
       (box) => {
  check("a failed detector says why",
        dot(box)?.getAttribute("title").includes("HAILO_TIMEOUT"));
  check("...and without `label` it is the dot alone", box.textContent === "");
});
render(h(m.AiDot, { ai: null }), (box) => {
  check("no status yet reads as red, never green",
        dot(box)?.getAttribute("data-ai") === "off");
});
render(h(m.AiDot, { ai: { ai_ok: true, ai_error: "" } }), (box) => {
  check("a System Control row (ai_ok / ai_error) is read too",
        dot(box)?.getAttribute("data-ai") === "on");
});
render(h(m.AiDot, { ai: { ok: true } }), (box) => {
  check("it is a status, not a control: nothing to press",
        !box.querySelector("button, input, a"));
});

// -- 2. on the canvas ------------------------------------------------------------
const REGISTRY = {
  "source.detection": { category: "source", label: "Detection Stream",
                        palette: { cat: "source" }, data: { name: "" } },
  "math.arith": { category: "logic", label: "Math", palette: { cat: "logic" },
                  data: { name: "Math", op: "add" } },
};
const types = m.buildNodeTypes(REGISTRY);
const ctx = (extra) => ({ registry: REGISTRY, values: {}, errors: {},
  warnings: {}, variables: {}, incoming: new Set(), wired: new Set(),
  offNodes: new Map(), fanIn: new Map(), edges: [], objClasses: new Map(),
  appRunning: true, ...extra });
const nodeOf = (id, type) => h(types[type], { id, type, selected: false,
                                            data: { ...REGISTRY[type].data } });

render(h(m.WorkflowCtx.Provider,
         { value: ctx({ aiStatus: { ok: true, reason: "" } }) },
         nodeOf("cam", "source.detection")), (box) => {
  check("a Detection Stream wears the dot: green while the AI runs",
        dot(box)?.getAttribute("data-ai") === "on", box.innerHTML.slice(0, 300));
  check("...and is not dimmed", !box.innerHTML.includes("opacity: 0.5"));
});
render(h(m.WorkflowCtx.Provider,
         // waitsForDetection: what the canvas used to be handed; a stale
         // caller still passing it must change nothing.
         { value: ctx({ aiStatus: { ok: false, reason: "AI detection is off" },
                        waitsForDetection: new Set(["cam", "m1"]) }) },
         h("div", null, nodeOf("cam", "source.detection"),
           nodeOf("m1", "math.arith"), nodeOf("m2", "math.arith"))), (box) => {
  const all = [...box.querySelectorAll('[role="status"][data-ai]')];
  check("AI off: the Detection Stream's dot is red, with the reason",
        all.length === 1 && all[0].getAttribute("data-ai") === "off"
        && all[0].getAttribute("title").includes("AI detection is off"),
        all.map((d) => d.getAttribute("title")));
  check("...no other node wears a dot", all.length === 1);
  check("...and no node says it waits (the stream and what it feeds look "
        + "normal)", !/wait/i.test(box.textContent), box.textContent);
  check("...and none is dimmed",
        !box.innerHTML.includes("opacity: 0.5"),
        (box.innerHTML.match(/opacity: 0\.5/g) || []).length);
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall AI-dot checks passed");
dom.window.close();
process.exit(0);
