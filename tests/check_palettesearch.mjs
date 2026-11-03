// The Workflow palette's search finds EVERY node by its label -- and puts
// what it found where you can see it.
//
// A beta tester typed "mqtt" and reported "no MQTT node": the screen filled
// with drawers that matched nothing (Filters with its "No zones yet", the
// Variables drawer's "+ New variable", Database's "No tables yet") under their
// stage titles, and the two MQTT nodes sat below the fold under ACTIONS. The
// matching was right; the empty drawers buried it. Pinned:
//   1. every palette node (the server's own registry) is found by its label
//   2. "mqtt", "modbus", "http", "inject" find their nodes, and NOTHING but
//      drawers with a match is drawn -- no empty drawer, no orphan heading
//   3. with no search the drawers are all there, as before
//
//   node tests/check_palettesearch.mjs
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".palsearch-test.cjs");

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

const exposePalette = {
  name: "expose-palette",
  setup(b) {
    b.onLoad({ filter: /pages[\\/]Workflow\.jsx$/ }, (args) => ({
      contents: readFileSync(args.path, "utf8") + "\nexport { Palette };\n",
      loader: "jsx", resolveDir: dirname(args.path),
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import { MemoryRouter } from "react-router-dom";
import { Palette } from "${p("src/pages/Workflow.jsx")}";
export { act, h, Palette, MemoryRouter };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;

await build({
  plugins: [exposePalette, shimApi, shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;

// The server's registry, exactly as the page receives it.
function registry() {
  const code = "import json,sys; sys.path.insert(0,'.');"
    + "from core.services import workflow as wf;"
    + "print(json.dumps(wf.registry_public()))";
  for (const [cmd, args] of [["py", ["-3", "-c", code]],
                             ["python3", ["-c", code]],
                             ["python", ["-c", code]]]) {
    try {
      return JSON.parse(execFileSync(cmd, args, { cwd: ROOT,
        encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
        maxBuffer: 64 * 1024 * 1024 }));
    } catch { /* next interpreter */ }
  }
  throw new Error("no Python to read the registry with");
}
const REG = registry();

function render(q) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const props = { registry: REG, zoneList: [], models: [], editable: true,
                  search: q.toLowerCase(), searchText: q, functions: [],
                  values: {}, vars: {}, customVars: [], nodes: [],
                  released: [], fnDefs: {} };
  const r = m.mount(box, h(m.MemoryRouter, null, h(m.Palette, props)));
  const text = box.textContent;
  r.unmount();
  document.body.removeChild(box);
  return text;
}

const shown = Object.entries(REG).filter(([, s]) => !s.deprecated && !s.hidden
                                          && s.label);
const missed = shown.filter(([, s]) => !render(s.label).includes(s.label))
  .map(([t, s]) => `${t} (${s.label})`);
check(`every palette node is found by its own label (${shown.length} nodes)`,
      shown.length > 40 && missed.length === 0, missed.join(", "));

const want = {
  mqtt: ["MQTT Subscribe", "MQTT Publish"],
  modbus: ["Modbus Read", "Modbus Write"],
  http: ["Webhook / REST", "Publish to HTTP"],
  inject: ["Inject"],
};
for (const [q, labels] of Object.entries(want)) {
  const t = render(q);
  check(`"${q}" finds ${labels.join(", ")}`, labels.every((l) => t.includes(l)),
        t.slice(0, 300));
}
const mq = render("mqtt");
check("...and draws no drawer that matched nothing",
      !mq.includes("No zones yet") && !mq.includes("New variable")
      && !mq.includes("No tables yet") && !mq.includes("Filters")
      && !mq.includes("Database"), mq.slice(0, 400));
check("...nor a stage title over nothing (only ACTIONS here)",
      !/input|processing|storage|visualization/i.test(mq.split("MQTT")[0]),
      mq.slice(0, 200));
const all = render("");
check("with no search every drawer is still there",
      ["Filters", "Variables", "Database", "Integration"].every((d) => all.includes(d)),
      all.slice(0, 300));

// The Delay is listed in Common as well as Logic (Tok Aba, 2026-10-14), one
// node type in two drawers -- and found ONCE by a search.
function labelCount(q, label, open) {
  localStorage.setItem("wf.palette.open", JSON.stringify(open || {}));
  const box = document.createElement("div");
  document.body.appendChild(box);
  const props = { registry: REG, zoneList: [], models: [], editable: true,
                  search: q.toLowerCase(), searchText: q, functions: [],
                  values: {}, vars: {}, customVars: [], nodes: [],
                  released: [], fnDefs: {} };
  const r = m.mount(box, h(m.MemoryRouter, null, h(m.Palette, props)));
  const n = [...box.querySelectorAll("[draggable] > div:first-child")]
    .filter((el) => el.textContent.trim() === label).length;
  r.unmount();
  document.body.removeChild(box);
  localStorage.removeItem("wf.palette.open");
  return n;
}
check("Delay is drawn in Common only",
      labelCount("", "Delay", { common: true }) === 1
      && labelCount("", "Delay", { logic: true }) === 0
      && labelCount("", "Delay", { common: true, logic: true }) === 1,
      [labelCount("", "Delay", { common: true }), labelCount("", "Delay", { logic: true })]);
check("...one node type, home Common",
      REG["logic.delay"].palette.cat === "common"
      && !REG["logic.delay"].palette.also_in);
check("...and a search lists it once",
      labelCount("delay", "Delay") === 1, labelCount("delay", "Delay"));

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall palette-search checks passed");
dom.window.close();
process.exit(0);
