// The Inject node's three faces -- the canvas node, its inspector, and the
// dashboard button made from it -- pinned to what they OFFER.
//
//   1. the node draws a button, disabled with the reason as its tooltip
//      whenever a press could not reach the app, and a press calls onInject
//   2. the inspector's payload is a TypedInput: a type picker with a glyph in
//      front of the value, the value field changing shape per type, and a
//      JSON that does not parse said on the spot
//   3. the dashboard button presses the same Inject, and is disabled -- with
//      the reason -- for a viewer who may not press it and for a button whose
//      node was deleted
//
//   node tests/check_inject.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".inject-test.cjs");

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
import { WorkflowCtx, buildNodeTypes } from "${p("src/workflow/nodes.jsx")}";
import { TypedInput } from "${p("src/workflow/TypedInput.jsx")}";
import Widget from "${p("src/components/widgets/Widget.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { act, h, WorkflowCtx, buildNodeTypes, Inspector, TypedInput, Widget,
         ToastProvider };
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

function render(node, fn) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, node);
  try { return fn ? fn(box) : box.innerHTML; } finally {
    r.unmount(); document.body.removeChild(box);
  }
}

// -- 1. the canvas node --------------------------------------------------------
const REGISTRY = {
  "common.inject": { category: "variable", label: "Inject", badge: "INJ",
                     rank: 3, palette: { cat: "common" },
                     metrics: ["value", "fired", "runs"],
                     data: { name: "Inject", ptype: "timestamp", payload: "",
                             topic: "", once: false, once_delay_s: 0.1,
                             repeat: "none", every: 1, every_unit: "seconds" } },
};
const Comp = m.buildNodeTypes(REGISTRY)["common.inject"];
const props = { id: "i1", type: "common.inject", selected: false,
                data: { ...REGISTRY["common.inject"].data, name: "Kick",
                        fnId: "kick", ptype: "number", payload: "5",
                        repeat: "interval", every: 30 } };
const ctx = (extra) => ({ registry: REGISTRY, values: {}, errors: {},
  warnings: {}, variables: {}, incoming: new Set(), wired: new Set(),
  offNodes: new Map(), fanIn: new Map(), edges: [], objClasses: new Map(),
  appRunning: true, ...extra });

let pressed = null;
render(h(m.WorkflowCtx.Provider,
         { value: ctx({ injectBlocked: () => "",
                        onInject: (n) => { pressed = n; } }) },
         h(Comp, props)), (box) => {
  const btn = box.querySelector('button[data-action]');
  check("the node draws its button", !!btn);
  check("...enabled when it can fire", btn && !btn.disabled);
  check("...says what it sends and when",
        box.innerHTML.includes("5 · every 30 s"), box.textContent);
  m.act(() => { btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  check("...and a press calls onInject for THIS node", pressed?.id === "i1");
});
render(h(m.WorkflowCtx.Provider,
         { value: ctx({ injectBlocked: () => "Save the workflow first",
                        onInject: () => { pressed = "wrong"; } }) },
         h(Comp, props)), (box) => {
  const btn = box.querySelector('button[data-action]');
  check("an unsaved Inject's button is disabled", btn && btn.disabled);
  check("...with the reason as its tooltip",
        btn?.getAttribute("title") === "Save the workflow first");
});
const ports = render(h(m.WorkflowCtx.Provider,
                       { value: ctx({ injectBlocked: () => "" }) },
                       h(Comp, props)));
check("an Inject takes no wire in, and gives one out",
      !ports.includes('data-porttype="target"') && ports.includes('data-porttype="source"'),
      ports);

// A REPEATING Inject: Start when stopped, Stop when running -- read back from
// the app's own `running` stat -- and the press says which it means.
const repProps = { ...props, data: { ...props.data, every: 5 } };
const actions = [];
for (const [running, want, word] of [[0, "start", "stopped"],
                                     [1, "stop", "running, every 5 s"]]) {
  render(h(m.WorkflowCtx.Provider,
           { value: ctx({ values: { kick_x: 0, fn_kick_running: running },
                          injectBlocked: () => "",
                          onInject: (n, a) => actions.push(a) }) },
           h(Comp, repProps)), (box) => {
    const btn = box.querySelector("button[data-action]");
    check(`a repeating Inject ${running ? "running" : "stopped"} offers ${want}`,
          btn?.getAttribute("data-action") === want,
          btn?.getAttribute("data-action"));
    check(`...and the body says '${word}'`, box.textContent.includes(word),
          box.textContent);
    m.act(() => { btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  });
}
check("...and each press sends its action", actions.join(",") === "start,stop",
      actions.join(","));
const plainAction = render(h(m.WorkflowCtx.Provider,
                             { value: ctx({ injectBlocked: () => "" }) },
                             h(Comp, { ...props, data: { ...props.data,
                                                         repeat: "none" } })));
check("an Inject without a repeat keeps the single press",
      plainAction.includes('data-action="press"')
      && !plainAction.includes("stopped"));
render(h(m.WorkflowCtx.Provider,
         { value: ctx({ injectBlocked: () => "Save the workflow first" }) },
         h(Comp, repProps)), (box) => {
  const btn = box.querySelector("button[data-action]");
  check("a repeating Inject is disabled by the same rules", btn?.disabled
        && btn.getAttribute("title") === "Save the workflow first");
});

// THE CIRCLE (client UAT 2026-10-04, item 2): green when the last injection
// passed, red when it failed, grey before the first. Read off the app's
// fn_<id>_ok / _runs / _last_at, a press this tab made (injectPress), and the
// outputs wired straight after the node (nodeStatus.js injectDot).
const dotState = (extra, p = props) => render(
  h(m.WorkflowCtx.Provider, { value: ctx({ injectBlocked: () => "", ...extra }) },
    h(Comp, p)),
  (box) => {
    const el = box.querySelector("[data-inject]");
    return el ? [el.getAttribute("data-inject"), el.getAttribute("title")] : null;
  });
let st = dotState({ values: {} });
check("the circle is grey before the first injection",
      st && st[0] === "unknown" && st[1] === "not injected yet", JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 2, fn_kick_ok: 1, fn_kick_last_at: 1000 } });
check("...green when the box took the last injection",
      st && st[0] === "on" && st[1].includes("passed") && st[1].includes("2 so far"),
      JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 2, fn_kick_ok: 0 } });
check("...red when the box dropped it",
      st && st[0] === "off" && st[1].includes("dropped"), JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 2, fn_kick_ok: 1 }, appRunning: false });
check("...red while the app is down", st && st[0] === "off"
      && st[1].includes("app is not running"), JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 2, fn_kick_ok: 1 },
                injectPress: { i1: { runs: 2, at: Date.now() } } });
check("...grey 'injecting...' right after a press, until the run counter moves",
      st && st[0] === "unknown" && st[1] === "injecting...", JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 3, fn_kick_ok: 1 },
                injectPress: { i1: { runs: 2, at: Date.now() } } });
check("...green once it has moved", st && st[0] === "on", JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 2, fn_kick_ok: 1 },
                injectPress: { i1: { runs: 2, at: Date.now() - 20000 } } });
check("...red when it never moves", st && st[0] === "off"
      && st[1].includes("did not take"), JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 2, fn_kick_ok: 1 },
                injectPress: { i1: { runs: 2, at: Date.now(), failed: "403" } } });
check("...red when the press itself was refused", st && st[0] === "off"
      && st[1].includes("403"), JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 2, fn_kick_ok: 1, fn_kick_last_at: 1000,
                          out_wf_o1_at: 1000.5, out_wf_o1_ok: 0,
                          out_wf_o1_error: "DO0 is held by another program" },
                edges: [{ id: "e", source: "i1", target: "o1" }] });
check("...red when an output wired after it failed since, naming the failure",
      st && st[0] === "off" && st[1].includes("DO0 is held"), JSON.stringify(st));
st = dotState({ values: { fn_kick_runs: 2, fn_kick_ok: 1, fn_kick_last_at: 1000,
                          out_wf_o1_at: 1000.5, out_wf_o1_ok: 1 },
                edges: [{ id: "e", source: "i1", target: "o1" }] });
check("...green and 'delivered to 1 output' when it worked", st && st[0] === "on"
      && st[1].includes("delivered to 1 output"), JSON.stringify(st));
check("an unsaved Inject (no function yet) wears no circle",
      dotState({ values: {} }, { ...props, data: { ...props.data, fnId: "" } }) === null);
check("the button says what a click does",
      render(h(m.WorkflowCtx.Provider, { value: ctx({ injectBlocked: () => "" }) },
               h(Comp, { ...props, data: { ...props.data, repeat: "none" } })),
             (box) => box.querySelector("button[data-action]").textContent) === "Inject");

// DETECTION OFF: every node looks as it does while detecting, a node a
// Detection Stream reaches included (Tok Aba 2026-10-05): no "waits for
// detection" tag and no dimming. A stale waitsForDetection in the context
// (what the canvas used to be handed) changes nothing.
const tagged = render(h(m.WorkflowCtx.Provider,
                        { value: ctx({ injectBlocked: () => "",
                                       waitsForDetection: new Set(["i1"]) }) },
                        h(Comp, props)));
const running = render(h(m.WorkflowCtx.Provider,
                         { value: ctx({ injectBlocked: () => "" }) },
                         h(Comp, props)));
check("a node fed by detection is not tagged while detection is off",
      !tagged.includes("waits for detection"));
check("...and is not dimmed", !tagged.includes("opacity: 0.5"));
check("...it renders exactly as it does while detection runs",
      tagged === running);

// -- 2. the inspector ----------------------------------------------------------
function inspect(data) {
  const node = { id: "i1", type: "common.inject",
                 data: { ...REGISTRY["common.inject"].data, ...data } };
  return render(h(m.ToastProvider, null, h(m.Inspector, {
    node, registry: REGISTRY, meta: { variables: {} }, nodes: [node], edges: [],
    editable: true, errors: [], warnings: [], onChange: () => {},
    functions: [], onDone: () => {} })));
}
const insp = inspect({ ptype: "number", payload: "5" });
check("the inspector asks for a Payload, a Topic and a Trigger",
      ["Payload", "Topic", "Trigger"].every((f) => insp.includes(f)));
check("...the Trigger is manual (click the node) or automatic (every interval)",
      insp.includes("manual - click the node")
      && insp.includes("automatic - every interval"));
check("...and explains the green / red circle",
      insp.includes("The circle beside the button") && insp.includes("green")
      && insp.includes("red"));
check("...and offers 'inject once after'", insp.includes("Inject once after"));
check("...the payload type picker offers every type",
      ["a-z", "0-9", "true / false", "JSON", "timestamp"]
        .every((t) => insp.includes(t)), insp.slice(0, 400));
const bool = inspect({ ptype: "boolean", payload: "true" });
check("a true/false payload is a true/false picker",
      bool.includes('<option value="false">false</option>'));
const json = inspect({ ptype: "json", payload: "{bad" });
check("a JSON payload is a box that says it does not parse",
      json.includes("<textarea") && json.includes("does not parse"));
const ts = inspect({ ptype: "timestamp" });
check("a timestamp needs no value, and says why",
      ts.includes("taken at the moment it fires"));
const rep = inspect({ repeat: "interval" });
check("a repeat offers 'start repeating when the app starts', on by default",
      rep.includes("Start repeating when the app starts")
      && /type="checkbox" checked=""[^>]*>\s*<span class="small">Start repeating/.test(rep),
      rep.slice(rep.indexOf("Start repeating") - 200, rep.indexOf("Start repeating")));
check("an interval asks every how many of which unit",
      rep.includes('aria-label="Repeat every"') && rep.includes(">minutes<"));

// TypedInput on its own: switching type keeps a value that still fits, and
// starts the new type's own way when it does not.
let got = null;
render(h(m.TypedInput, { type: "string", value: "hello",
                         onChange: (v) => { got = v; } }), (box) => {
  const sel = box.querySelector('select[aria-label="Value type"]');
  m.act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")
      .set.call(sel, "number");
    sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
});
check("TypedInput: 'hello' switched to number starts at 0",
      got && got.type === "number" && got.value === "0", JSON.stringify(got));

// -- 3. the dashboard button ---------------------------------------------------
const widget = (data) => ({ id: "b1", title: "Kick", kind: "button",
                            node: "i1", data });
globalThis.__posts = [];
{
  // The press is async (the client is imported at press time), so this case
  // waits for it rather than reading the moment after the click.
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.Widget, { widget: widget({
    node: "i1", runs: 2, value: 5, removed: false, can_press: true }) }));
  const btn = [...box.querySelectorAll("button")].find((b) => b.textContent === "Kick");
  check("the dashboard button is labelled with the Inject's name", !!btn);
  check("...says it was injected, not a count", box.textContent.includes("injected") && !box.textContent.includes("since start"));
  await m.act(async () => {
    btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    for (let i = 0; i < 20 && !globalThis.__posts.length; i += 1) {
      await new Promise((ok) => setTimeout(ok, 5));
    }
  });
  check("...and presses the same Inject",
        JSON.stringify(globalThis.__posts[0]) ===
          JSON.stringify(["/api/workflow/inject", { node: "i1", action: "press" }]),
        JSON.stringify(globalThis.__posts));
  r.unmount(); document.body.removeChild(box);
}
render(h(m.Widget, { widget: widget({ node: "i1", runs: 0, removed: false,
                                      can_press: false }) }), (box) => {
  const btn = [...box.querySelectorAll("button")].find((b) => b.textContent === "Kick");
  check("a viewer who may not press it sees it disabled", btn && btn.disabled);
});
// How long the chip took to detect the last snap: on the status row, at the
// right end, in seconds with two decimals; nothing when there is no reading.
render(h(m.Widget, { widget: widget({ node: "i1", runs: 1, removed: false,
                                      can_press: true, detect_s: 0.043 }) }), (box) => {
  const t = box.querySelector('[data-testid="inject-detect-time"]');
  check("the detection time shows as seconds with two decimals", t && t.textContent === "0.04 s",
        t && t.textContent);
  check("...on the status row, not inside the button",
        t && !t.closest("button") && t.parentElement.textContent.startsWith("injected"));
});
render(h(m.Widget, { widget: widget({ node: "i1", runs: 1, removed: false,
                                      can_press: true, detect_s: null }) }), (box) => {
  check("no detection time without a reading",
        !box.querySelector('[data-testid="inject-detect-time"]'));
});
render(h(m.Widget, { widget: widget({ node: "i1", runs: 0, removed: true,
                                      can_press: true }) }), (box) => {
  const btn = [...box.querySelectorAll("button")].find((b) => b.textContent === "Kick");
  check("a button whose Inject was deleted is disabled", btn && btn.disabled);
  check("...and says so", box.textContent.includes("Inject node was removed"));
});

// A REPEATING Inject on the dashboard: Start / Stop, and the press says which.
globalThis.__posts = [];
{
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.Widget, { widget: widget({
    node: "i1", runs: 1, value: 1, removed: false, can_press: true,
    repeat: true, running: true }) }));
  const btn = box.querySelector("button[data-action]");
  check("a running repeat on the dashboard offers Stop",
        btn?.getAttribute("data-action") === "stop"
        && btn.textContent.includes("Stop"), btn?.textContent);
  check("...and says it is running", box.textContent.includes("running"));
  await m.act(async () => {
    btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    for (let i = 0; i < 20 && !globalThis.__posts.length; i += 1) {
      await new Promise((ok) => setTimeout(ok, 5));
    }
  });
  check("...and pressing it sends stop",
        JSON.stringify(globalThis.__posts[0]) ===
          JSON.stringify(["/api/workflow/inject", { node: "i1", action: "stop" }]),
        JSON.stringify(globalThis.__posts));
  r.unmount(); document.body.removeChild(box);
}
render(h(m.Widget, { widget: widget({ node: "i1", runs: 0, removed: false,
                                      can_press: true, repeat: true,
                                      running: false }) }), (box) => {
  const btn = box.querySelector("button[data-action]");
  check("a stopped repeat on the dashboard offers Start",
        btn?.getAttribute("data-action") === "start"
        && btn.textContent.includes("Start"), btn?.textContent);
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall inject checks passed");
// jsdom's visual mode keeps an animation-frame loop alive, and the press
// above left a settled import behind it; neither is ours to wait for.
dom.window.close();
process.exit(0);
