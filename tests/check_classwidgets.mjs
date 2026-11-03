// The two dashboard controls of 2026-10-05 -- "AI model classes" and "Class
// filter" -- and the Class Filter naming rule in the panel, pinned to what
// they OFFER:
//
//   1. both are in the Dashboard's widget picker (not Analytics), binding no key
//   2. AI model classes: a dropdown of ticks, the stored choice ticked; ticks
//      gathered and sent with one Apply through the Models page's own route
//      (per model on a two-model system); read-only for a viewer
//   3. Class filter: one switch per NAMED filter (role="switch", aria-checked,
//      green "Running" / silver "Paused" in words), all running by default; a
//      press pauses/runs it at once; busy while saving; read-only for a viewer
//   4. a card behind a paused filter says Paused in words, never a 0
//   5. the panel says the naming rule beside the Name field, in the server's
//      words, and the canvas node wears the name
//
//   node tests/check_classwidgets.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".classwidgets-test.cjs");

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
import { Inspector } from "${p("src/pages/Workflow.jsx")}";
import { buildNodeTypes, WorkflowCtx } from "${p("src/workflow/nodes.jsx")}";
import { classFilterNaming } from "${p("src/workflow/registry.js")}";
import Widget from "${p("src/components/widgets/Widget.jsx")}";
import { PALETTE, ANALYTICS_PALETTE, newWidget } from "${p("src/components/widgets/builderShared.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { act, h, Inspector, buildNodeTypes, WorkflowCtx, classFilterNaming,
         Widget, PALETTE, ANALYTICS_PALETTE, newWidget, ToastProvider };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;

await build({
  plugins: [exposeInspector, shimApi, shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;

function mounted(node) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, node);
  return { box, done: () => { r.unmount(); document.body.removeChild(box); } };
}
const click = (el) => m.act(() => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
});
async function settle(pred) {
  await m.act(async () => {
    for (let i = 0; i < 40 && !pred(); i += 1) {
      await new Promise((ok) => setTimeout(ok, 5));
    }
  });
}

// -- 1. the widget picker -------------------------------------------------------
const kinds = m.PALETTE.map((i) => i.kind);
// The factory still knows it (a saved one keeps working); the Dashboard
// palette stopped offering it on 2026-10-14 (check_builderpalette).
check("the widget factory still knows AI model classes",
      kinds.includes("ai_classes"));
check("...and Class filter", kinds.includes("class_filters"));
check("...on the Dashboard only, not on Analytics (they are controls)",
      !m.ANALYTICS_PALETTE.some((i) => ["ai_classes", "class_filters"].includes(i.kind)));
const fresh = m.newWidget(m.PALETTE.find((i) => i.kind === "ai_classes"), { stat: ["fn_x"] });
check("...and a new one binds no key", fresh.key === "", JSON.stringify(fresh));

// -- 2. AI model classes ----------------------------------------------------------
const classes = (data) => ({ id: "ac1", title: "AI model classes",
                             kind: "ai_classes", data });
const one = { models: [{ model: "", name: "yolov8n",
                         labels: ["person", "chair", "car"],
                         classes: ["person"] }], can_edit: true };
globalThis.__posts = [];
{
  const { box, done } = mounted(h(m.Widget, { widget: classes(one) }));
  check("the classes card says what the model detects",
        box.textContent.includes("Detecting person"), box.textContent);
  const open = [...box.querySelectorAll("button")].find((b) => b.textContent.includes("Choose classes"));
  check("...behind a dropdown", !!open && open.getAttribute("aria-expanded") === "false");
  click(open);
  const ticks = [...box.querySelectorAll('[role="group"] input[type="checkbox"]')];
  check("...which opens a tick per class", ticks.length === 3, ticks.length);
  check("...the chosen one ticked", ticks[0].checked && !ticks[1].checked);
  const apply = () => [...box.querySelectorAll("button")].find((b) => b.textContent === "Apply");
  check("...Apply waits for a change", apply()?.disabled === true);
  m.act(() => { ticks[1].click(); });
  check("ticking chair ticks it", box.querySelectorAll('[role="group"] input')[1].checked);
  check("...and offers Apply", apply()?.disabled === false);
  click(apply());
  await settle(() => globalThis.__posts.length > 0);
  check("Apply saves through the Models page's route, both classes",
        JSON.stringify(globalThis.__posts[0])
          === JSON.stringify(["/api/detector/control/params",
                              { classes: ["person", "chair"] }]),
        JSON.stringify(globalThis.__posts));
  check("...and says it in plain words",
        box.textContent.includes("the AI uses it within a few seconds"), box.textContent);
  done();
}
{
  // The setting changed on the Models page: the next poll is what it shows.
  const { box, done } = mounted(h(m.Widget, { widget: classes(one) }));
  check("the card follows the stored setting...", box.textContent.includes("Detecting person"));
  done();
  const moved = { ...one, models: [{ ...one.models[0], classes: ["car"] }] };
  const again = mounted(h(m.Widget, { widget: classes(moved) }));
  check("...including a change made on the Models page",
        again.box.textContent.includes("Detecting car"));
  again.done();
}
{
  const none = { ...one, models: [{ ...one.models[0], classes: [] }] };
  const { box, done } = mounted(h(m.Widget, { widget: classes(none) }));
  check("none chosen reads as every class", box.textContent.includes("Detecting every class"));
  done();
}
{
  const { box, done } = mounted(h(m.Widget, { widget: classes({ ...one, can_edit: false }) }));
  const open = [...box.querySelectorAll("button")].find((b) => b.textContent.includes("Show classes"));
  check("a viewer gets Show classes, not Choose", !!open);
  click(open);
  const ticks = [...box.querySelectorAll('[role="group"] input[type="checkbox"]')];
  check("...the ticks are shown read-only", ticks.length === 3 && ticks.every((t) => t.disabled));
  check("...with no Apply", ![...box.querySelectorAll("button")].some((b) => b.textContent === "Apply"));
  check("...and says why in words", box.textContent.includes("View only"));
  check("...no HTTP code anywhere", !/\b40[13]\b/.test(box.textContent));
  done();
}
{
  const two = { can_edit: true, models: [
    { model: "yolov8n", name: "yolov8n", labels: ["person"], classes: [] },
    { model: "pcbnet", name: "pcbnet", labels: ["pcb", "solder"], classes: ["pcb"] }] };
  globalThis.__posts = [];
  const { box, done } = mounted(h(m.Widget, { widget: classes(two) }));
  const sel = box.querySelector('select[aria-label="AI model"]');
  check("two models: a model picker", !!sel && sel.options.length === 2);
  m.act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")
      .set.call(sel, "pcbnet");
    sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  check("...showing that model's own classes", box.textContent.includes("Detecting pcb"));
  click([...box.querySelectorAll("button")].find((b) => b.textContent.includes("Choose classes")));
  m.act(() => { box.querySelectorAll('[role="group"] input')[1].click(); });
  click([...box.querySelectorAll("button")].find((b) => b.textContent === "Apply"));
  await settle(() => globalThis.__posts.length > 0);
  check("...and saving names that model",
        JSON.stringify(globalThis.__posts[0]?.[1])
          === JSON.stringify({ classes: ["pcb", "solder"], model: "pcbnet" }),
        JSON.stringify(globalThis.__posts));
  done();
}

// -- 3. Class filter ----------------------------------------------------------------
// One switch per named filter: a native <button role="switch"> (Space and
// Enter for free), aria-checked carrying the state, named by the filter, and
// the state in WORDS on the pill -- Running (green, .is-on) / Paused (silver,
// .is-off) -- so colour is never the only signal.
const filters = (data) => ({ id: "cf1", title: "Class filter",
                             kind: "class_filters", data });
const both = { can_edit: true, filters: [
  { node: "f1", name: "people", running: true },
  { node: "f2", name: "chair", running: true }] };
const switches = (box) => [...box.querySelectorAll('[role="switch"]')];
const sw = (box, node) => box.querySelector(`[role="switch"][data-node="${node}"]`);
const nameOf = (box, el) => box.querySelector(`#${el.getAttribute("aria-labelledby")}`)?.textContent;
globalThis.__posts = [];
{
  const { box, done } = mounted(h(m.Widget, { widget: filters(both) }));
  const s = switches(box);
  check("the Class filter card lists each named filter as a switch", s.length === 2
        && box.textContent.includes("people") && box.textContent.includes("chair"),
        box.innerHTML.slice(0, 300));
  check("...no browser checkboxes left", !box.querySelector('input[type="checkbox"]'));
  check("...each a real button, so Space and Enter press it",
        s.every((b) => b.tagName === "BUTTON" && b.getAttribute("type") === "button"));
  check("...named by its filter", nameOf(box, s[0]) === "people" && nameOf(box, s[1]) === "chair");
  check("...all running by default: aria-checked, green, saying Running",
        s.every((b) => b.getAttribute("aria-checked") === "true"
                       && b.classList.contains("cf-toggle") && b.classList.contains("is-on")
                       && b.textContent === "Running")
        && !/paused/i.test(box.textContent), box.innerHTML.slice(0, 400));
  check("...pressable", s.every((b) => !b.disabled));
  click(s[0]);
  check("...while it saves, that switch says busy and the others wait",
        sw(box, "f1").getAttribute("aria-busy") === "true"
        && sw(box, "f1").dataset.busy === "1" && sw(box, "f2").disabled);
  await settle(() => globalThis.__posts.length > 0);
  check("switching people off pauses it through the class-filter route",
        JSON.stringify(globalThis.__posts[0])
          === JSON.stringify(["/api/workflow/class-filter", { node: "f1", running: false }]),
        JSON.stringify(globalThis.__posts));
  await settle(() => box.textContent.includes("people is paused"));
  check("...and says so in words", box.textContent.includes("people is paused"), box.textContent);
  const off = sw(box, "f1");
  check("...the switch reads Paused (silver, unchecked) until the next poll",
        off.getAttribute("aria-checked") === "false" && off.classList.contains("is-off")
        && !off.classList.contains("is-on") && off.textContent === "Paused",
        off.outerHTML);
  check("...busy over, pressable again",
        !off.hasAttribute("aria-busy") && !off.disabled && !sw(box, "f2").disabled);
  done();
}
{
  const paused = { ...both, filters: [{ ...both.filters[0], running: false }, both.filters[1]] };
  globalThis.__posts = [];
  const { box, done } = mounted(h(m.Widget, { widget: filters(paused) }));
  check("a paused filter shows switched off, with the word Paused",
        sw(box, "f1").getAttribute("aria-checked") === "false"
        && sw(box, "f1").textContent === "Paused"
        && sw(box, "f2").getAttribute("aria-checked") === "true");
  click(sw(box, "f1"));
  await settle(() => globalThis.__posts.length > 0);
  check("switching it on runs it again",
        JSON.stringify(globalThis.__posts[0]?.[1]) === JSON.stringify({ node: "f1", running: true }));
  done();
}
{
  globalThis.__posts = [];
  const { box, done } = mounted(h(m.Widget, { widget: filters({ ...both, can_edit: false }) }));
  const s = switches(box);
  check("a viewer sees the switches read-only",
        s.length === 2 && s.every((b) => b.disabled && b.classList.contains("is-readonly")));
  check("...still saying Running", s.every((b) => b.textContent === "Running"));
  click(s[0]);
  check("...a press sends nothing", globalThis.__posts.length === 0);
  check("...and why", box.textContent.includes("View only"));
  done();
}
{
  const { box, done } = mounted(h(m.Widget, { widget: filters({ filters: [], can_edit: true }) }));
  check("no named filters: it says how to get some",
        box.textContent.includes("Name the Class Filters on the Workflow page"));
  done();
}
{
  // The looks are tokens, not literals: green from --ok, words from the
  // *-text tokens, a 32 px touch target, and a focus ring.
  const css = readFileSync(join(FRONT, "src", "theme.css"), "utf8");
  // The body of the rule whose selector is exactly `sel`, at a line start.
  const rule = (sel) => {
    const at = css.indexOf(`\n${sel} {`);
    return at < 0 ? "" : css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
  };
  check("the switch is at least 32 px tall", /min-height:\s*32px/.test(rule(".cf-toggle")));
  check("...on: an --ok track, its words in --ok-text",
        /var\(--ok\)/.test(rule(".cf-toggle.is-on .cf-track"))
        && /color:\s*var\(--ok-text\)/.test(rule(".cf-toggle.is-on")));
  check("...off: a silver track, words in --text2",
        /var\(--muted2\)/.test(rule(".cf-track"))
        && /color:\s*var\(--text2\)/.test(rule(".cf-toggle")));
  check("...never painted with --muted or --accent as text",
        !/color:\s*var\(--(muted|accent)\)/.test(
          css.slice(css.indexOf(".cf-list"), css.indexOf("@keyframes cf-busy"))));
  check("...with a visible focus ring", /outline:\s*2px solid/.test(rule(".cf-toggle:focus-visible")));
}

// -- 4. the cards after a paused filter ------------------------------------------------
{
  const { box, done } = mounted(h(m.Widget, { widget: { id: "t", title: "People",
    kind: "tile", data: { value: null, paused: true, paused_filter: "people" } } }));
  check("a tile behind a paused filter says Paused, not 0",
        box.textContent.includes("Paused") && box.textContent.includes('"people" Class Filter')
        && !/\b0\b/.test(box.textContent), box.textContent);
  done();
}
{
  const { box, done } = mounted(h(m.Widget, { widget: { id: "t", title: "People today",
    kind: "tile", data: { value: 4, paused_badge: true, paused_filter: "people" } } }));
  check("a kept tally keeps its number under a paused badge",
        box.textContent.includes("4") && box.querySelector('[data-paused="1"]')?.textContent === "paused",
        box.innerHTML.slice(0, 300));
  done();
}

// -- 5. the naming rule in the panel ------------------------------------------------------
const REG = {
  "filter.class": { category: "filter", label: "Class Filter", badge: "FLT",
                    rank: 1, palette: { cat: "filter" }, data: { classes: [] } },
};
const fA = { id: "f1", type: "filter.class", data: { classes: ["person"] } };
const fB = { id: "f2", type: "filter.class", data: { classes: ["chair"], name: "chair" } };
const naming = m.classFilterNaming([fA, fB]);
check("two filters, one unnamed: the panel names the problem",
      naming.f1 === "Name each Class Filter: this workflow has 2, and the dashboard lists them by name"
      && !naming.f2, JSON.stringify(naming));
check("one filter alone needs no name", Object.keys(m.classFilterNaming([fA])).length === 0);
check("a repeated name is said",
      (m.classFilterNaming([{ ...fA, data: { name: "Chair" } }, fB]).f2 || "").includes("already called"));
function inspect(node, nodes) {
  const { box, done } = mounted(h(m.ToastProvider, null, h(m.Inspector, {
    node, registry: REG, meta: { variables: { classes: ["person", "chair"] } },
    nodes, edges: [], editable: true, errors: [], warnings: [],
    onChange: () => {}, functions: [], onDone: () => {} })));
  const html = box.innerHTML;
  done();
  return html;
}
const insp = inspect(fA, [fA, fB]);
check("the Class Filter inspector asks for a Name", insp.includes('aria-label="Class Filter name"'));
check("...and says the rule beside it when it is broken",
      insp.includes("Name each Class Filter: this workflow has 2"));
check("...but not when the filter is alone",
      !inspect(fA, [fA]).includes("Name each Class Filter"));
const Comp = m.buildNodeTypes(REG)["filter.class"];
const face = (() => {
  const { box, done } = mounted(h(m.WorkflowCtx.Provider, { value: {
    registry: REG, values: {}, errors: {}, warnings: {}, variables: {},
    incoming: new Set(), wired: new Set(), offNodes: new Map(), fanIn: new Map(),
    edges: [], objClasses: new Map(), appRunning: true } },
  h(Comp, { id: "f2", type: "filter.class", selected: false, data: fB.data })));
  const t = box.textContent;
  done();
  return t;
})();
check("a named Class Filter wears its name on the canvas", face.includes("chair"), face);

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall class widget checks passed");
dom.window.close();
process.exit(0);
