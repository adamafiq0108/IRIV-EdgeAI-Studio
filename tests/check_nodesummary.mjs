// The in / does / out summary of every workflow node (Tok Aba, 2026-10-14),
// workflow/nodeSummary.js, shown at the top of the node's PROPERTIES panel
// (pages/Workflow.jsx NodeSummaryBox) and never on the node face.
//
//   1. CONTRACT: every type in the server REGISTRY either has a summary or is
//      in the explicit skip list; no row empty, none over ROW_MAX, no em
//      dash; `in` / `out` words come from the one vocabulary (TYPE_WORDS)
//   2. tier 2: the commonly used nodes say what THEIR settings do (snapshots
//      under a few settings each); the rest fall back to the registry's own
//      description, never blank
//   3. the Properties panel of every type with a summary shows the box, three
//      labelled rows, under the title; it follows the wiring live (a Counter
//      gaining a Zone, then a Line); Comment and the Link pair show none
//   4. NO node face carries the box, and every type draws exactly the handle
//      ids it drew before the box existed, wired and unwired
//      (tests/golden_node_handles.json, captured at 46c6a5ef)
//   5. no "Show node details" toggle is left behind
//
//   node tests/check_nodesummary.mjs
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
const OUT = join(FRONT, "node_modules", ".nodesummary-test.cjs");

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
const types = Object.keys(registry);
const VOCAB = new Set(Object.values(S.TYPE_WORDS));
const defaults = (t) => ({ ...(registry[t].data || {}) });

// -- 1. contract ------------------------------------------------------------------
const noBox = [];
const rowsBad = [];
const vocabBad = [];
const tiers = { 2: [], 3: [] };
for (const t of types) {
  const box = S.nodeSummary(t, defaults(t), registry[t], { zoneWired: false });
  if (!box) { if (!S.SUMMARY_SKIP.includes(t)) noBox.push(t); continue; }
  tiers[box.tier].push(t);
  for (const k of ["in", "does", "out"]) {
    const v = box[k];
    if (!v || !String(v).trim() || v.length > S.ROW_MAX || v.includes("\u2014")) {
      rowsBad.push(`${t}.${k}=${JSON.stringify(v)}`);
    }
  }
  if (!box.in.split(" · ").every((w) => VOCAB.has(w)) || !VOCAB.has(box.out)) {
    vocabBad.push(`${t}: in=${box.in} out=${box.out}`);
  }
}
console.log(`     ${types.length} registry types: ${tiers[2].length} tier 2, ${tiers[3].length} tier 3, `
            + `${S.SUMMARY_SKIP.length} skipped (${S.SUMMARY_SKIP.join(", ")})`);
check("every registry type has a summary or is in the explicit skip list", !noBox.length, noBox.join(", "));
check("the skip list names real types only", S.SUMMARY_SKIP.every((t) => registry[t]));
check("no row empty, over 44 characters or with an em dash", !rowsBad.length, rowsBad.join("\n      "));
check("in / out words come from the one vocabulary", !vocabBad.length, vocabBad.join("\n      "));
console.log(`     tier 3 (registry description): ${tiers[3].join(", ")}`);

// -- 2. tier 2 follows the settings -----------------------------------------------
const MUST_TIER2 = ["output.trigger", "branch.cases", "branch.compare", "branch.range",
  "branch.match", "branch.multi", "analytics.expr", "math.arith", "output.dataset",
  "storage.query", "variable.ref", "variable.number", "output.snapshot", "output.record",
  "output.telegram", "output.email", "output.mqtt_publish", "output.webhook",
  "output.modbus_write", "zone.ref", "filter.class", "analytics.dwell", "analytics.travel",
  "analytics.heatmap", "analytics.proximity", "analytics.guard", "logic.delay",
  "logic.switch", "logic.when", "logic.catch", "common.debug", "common.inject",
  "dashboard.card", "dashboard.graph", "variable.schedule", "source.detection",
  "analytics.object", "analytics.counter", "analytics.entry_exit"];
const notT2 = MUST_TIER2.filter((t) => !tiers[2].includes(t));
check(`the ${MUST_TIER2.length} commonly used nodes say what their settings do (tier 2)`,
      !notT2.length, notT2.join(", "));
const does = (t, d, ctx = {}) => S.nodeSummary(t, { ...defaults(t), ...d }, registry[t], ctx).does;
const SNAP = [
  ["output.trigger", {}, {}, "mark the moment it turns true"],
  ["output.trigger", { alert: true, severity: "critical", draw: true }, {}, "raise a critical alert · paints boxes"],
  ["branch.cases", { rules: [{ op: ">", value: 5 }] }, {}, "test: over 5 · else"],
  ["branch.cases", { rules: [{ op: ">", value: 5 }, { op: "<", value: 2 }] }, {}, "2 tests · first true wins"],
  ["branch.multi", { join: "or", rules: [{}, {}, {}] }, {}, "3 tests joined with OR"],
  ["logic.delay", { delay_s: 12 }, {}, "wait 12s, then pass it on"],
  ["logic.when", { mode: "above", value: 3, hold_s: 2 }, {}, "true while over 3 for 2s"],
  ["analytics.expr", { expr: "a / b * 100" }, {}, "work out a / b * 100"],
  ["analytics.expr", { expr: "" }, {}, "work out a formula (none yet)"],
  ["math.arith", { op: "mul" }, { opLabel: "Multiply" }, "work out: Multiply"],
  ["output.dataset", { name: "Rejects" }, {}, "add a row to Rejects"],
  ["output.dataset", { name: "Rejects", mode: "every", interval_s: 30 }, {}, "add a row to Rejects every 30s"],
  ["output.dataset", { name: "Live", on_repeat: "update" }, {}, "update a row in Live"],
  ["variable.schedule", { every: "daily", at: "08:30" }, {}, "fire daily at 08:30"],
  ["variable.schedule", { every: "minutes", interval_min: 5 }, {}, "fire every 5 minutes"],
  ["output.snapshot", { mode: "interval", interval_s: 3 }, {}, "save a photo every 3s while true"],
  ["output.record", { before_s: 2, after_s: 8 }, {}, "record a clip · 2s before, 8s after"],
  ["output.email", { to: "qa@plant.my" }, {}, "email qa@plant.my"],
  ["output.mqtt_publish", { topic: "line2/rejects" }, {}, "publish to line2/rejects"],
  ["zone.ref", { zone: "z1" }, { zoneName: "Loading bay" }, 'the area "Loading bay"'],
  ["zone.ref", { zone: "" }, {}, "pick a zone in the settings"],
  ["filter.class", { classes: ["person", "forklift"] }, {}, "keep only person, forklift"],
  ["filter.class", { classes: [] }, {}, "keep every class (none picked)"],
  ["analytics.dwell", { mode: "present" }, { zoneWired: true }, "how long objects stay · inside the zone"],
  ["analytics.travel", { speed: "on", speed_unit: "kmh" }, {}, "speed from line A to line B (kmh)"],
  ["common.inject", { ptype: "timestamp", repeat: "interval", every: 30, every_unit: "seconds" }, {},
   "send the time every 30 seconds"],
  ["dashboard.graph", { kind: "bar", x_type: "time" }, { chartName: "Time-based Bar" }, "show as a Time-based Bar"],
  ["dashboard.card", { kind: "gauge" }, { shapeName: "gauge - a dial" }, "show as gauge - a dial"],
  ["analytics.counter", { mode: "realtime" }, { zoneWired: false }, "count what is there now · whole view"],
  ["analytics.counter", { mode: "distinct" }, { zoneWired: true }, "count each object once · inside the zone"],
  ["analytics.counter", {}, { cmode: "line" }, "count what crosses the line"],
  ["analytics.object", {}, { zoneWired: true }, "detect inside the zone wired in"],
];
for (const [t, d, ctx, want] of SNAP) {
  const got = does(t, d, ctx);
  check(`${t} ${JSON.stringify(d)} ${JSON.stringify(ctx)} -> "${S.fit(want)}"`, got === S.fit(want), `got "${got}"`);
}
check("an Inject's out follows its type (time / text / yes/no)",
      S.nodeSummary("common.inject", { ptype: "timestamp" }, registry["common.inject"]).out === "time"
      && S.nodeSummary("common.inject", { ptype: "string" }, registry["common.inject"]).out === "text"
      && S.nodeSummary("common.inject", { ptype: "boolean" }, registry["common.inject"]).out === "yes/no (0/1)");
check("an optional zone shows on `in` only when one is wired, a line only on a line counter",
      S.nodeSummary("analytics.dwell", {}, registry["analytics.dwell"], { zoneWired: false }).in === "objects"
      && S.nodeSummary("analytics.dwell", {}, registry["analytics.dwell"], { zoneWired: true }).in === "objects · zone"
      && S.nodeSummary("analytics.counter", {}, registry["analytics.counter"], { zoneWired: false, cmode: "line" }).in === "objects · line");
check("tier 3 is the registry's own first sentence, cut to fit",
      tiers[3].every((t) => {
        const want = S.fit(String(registry[t].description || registry[t].label).split(/(?<=[.!?])\s/)[0]
          .replace(/[.!?]$/, ""));
        return S.nodeSummary(t, defaults(t), registry[t]).does === want;
      }));

// -- 3. the Properties panel ---------------------------------------------------------
const VARIABLES = { tables: [{ key: "t1", name: "Events", columns: [{ name: "count", type: "number" }] }],
                    cameras: [], zone_list: [{ id: "z1", name: "Zone A", kind: "polygon" }],
                    event_fields: [], event_values: {} };
const inspectorProps = (node, edges = [], extra = []) => ({
  node, registry, meta: { variables: VARIABLES }, nodes: [node, ...extra], edges, editable: true,
  errors: [], warnings: [], onChange: () => {}, functions: [], onDone: () => {} });
const rowsOf = (el) => {
  const box = el.querySelector('[data-testid="node-summary"]');
  if (!box) return null;
  return [...box.querySelectorAll(".node-summary-row")].map((r) => [
    r.querySelector(".node-summary-pill")?.textContent, r.querySelector(".node-summary-text")?.textContent]);
};
const missing = []; const wrong = []; const skipShown = []; const ierr = [];
for (const t of types) {
  const el = document.body.appendChild(document.createElement("div"));
  const node = { id: "n1", type: t, data: { ...defaults(t), ...(t === "output.dataset" ? { dataset: "t1" } : {}) } };
  let rows = null;
  try {
    const m = openInspector(el, inspectorProps(node));
    rows = rowsOf(el);
    m.close();
  } catch (e) { ierr.push(`${t}: ${e.message}`); }
  el.remove();
  const want = S.summaryFor({ type: t, data: node.data, spec: registry[t], zones: VARIABLES.zone_list });
  if (!want) { if (rows) skipShown.push(t); continue; }
  if (!rows) { missing.push(t); continue; }
  if (JSON.stringify(rows) !== JSON.stringify([["in", want.in], ["does", want.does], ["out", want.out]])) {
    wrong.push(`${t}: ${JSON.stringify(rows)}`);
  }
}
check("every node type's Properties render", !ierr.length, ierr.join("\n      "));
check("every type with a summary shows the box in Properties: in, does, out, as summaryFor says",
      !missing.length && !wrong.length, [...missing, ...wrong].join("\n      "));
check("Comment and the Link pair show none", !skipShown.length, skipShown.join(", "));
{
  // Under the title, above the settings.
  const el = document.body.appendChild(document.createElement("div"));
  const m = openInspector(el, inspectorProps({ id: "c1", type: "logic.delay", data: { delay_s: 4 } }));
  const title = el.querySelector(".section-title");
  const box = el.querySelector('[data-testid="node-summary"]');
  check("the box sits straight under the node's title, before any setting",
        title && box && title.nextElementSibling === box && !box.querySelector("input, select, textarea"));
  m.close(); el.remove();
}
{
  // LIVE: a Counter gains a Zone wire, then a Line instead.
  const el = document.body.appendChild(document.createElement("div"));
  const node = { id: "c1", type: "analytics.counter", data: { ...defaults("analytics.counter") } };
  const zone = { id: "z", type: "zone.ref", data: { zone: "z1" } };
  const m = openInspector(el, inspectorProps(node));
  const first = rowsOf(el);
  m.rerender(inspectorProps(node, [{ id: "e1", source: "z", target: "c1", targetHandle: "zone" }], [zone]));
  const zoned = rowsOf(el);
  m.rerender(inspectorProps(node, [{ id: "e2", source: "z", target: "c1", targetHandle: "line" }], [zone]));
  const lined = rowsOf(el);
  m.rerender(inspectorProps({ ...node, data: { ...node.data, mode: "distinct" } }));
  const distinct = rowsOf(el);
  m.close(); el.remove();
  check("live: a Counter with nothing wired counts the whole view",
        first && first[0][1] === "objects" && first[1][1] === "count what is there now · whole view", JSON.stringify(first));
  check("...a Zone wired in: in the zone", zoned && zoned[0][1] === "objects · zone"
        && zoned[1][1] === "count what is there now · inside the zone", JSON.stringify(zoned));
  check("...a Line instead: crossings", lined && lined[0][1] === "objects · line"
        && lined[1][1] === "count what crosses the line", JSON.stringify(lined));
  check("...and a setting change (count each once) is said at once",
        distinct && distinct[1][1].startsWith("count each object once"), JSON.stringify(distinct));
}

// -- 4. node faces: no box, no handle moved -------------------------------------------
const nodeTypes = buildNodeTypes(registry);
const GOLDEN = JSON.parse(readFileSync(join(ROOT, "tests", "golden_node_handles.json"), "utf8"));
const EVERYTHING = { has: () => true };
const ctxFor = (wired) => ({
  registry, values: {}, errors: {}, warnings: {}, variables: VARIABLES, resizeNode: null,
  incoming: wired ? EVERYTHING : new Set(), wired: wired ? EVERYTHING : new Set(),
  offNodes: new Map(), fanIn: new Map(), edges: [], objClasses: new Map(),
});
const scratch = document.createElement("div");
const faceBox = []; const moved = []; const ferr = [];
for (const t of types) {
  for (const wiredState of [false, true]) {
    const el = document.body.appendChild(document.createElement("div"));
    let html = "";
    try {
      html = drawFace(el, nodeTypes[t], { id: "n1", type: t, selected: false,
        data: { ...defaults(t), ...(t === "output.dataset" ? { dataset: "t1" } : {}) } }, ctxFor(wiredState));
    } catch (e) { ferr.push(`${t}: ${e.message}`); }
    el.remove();
    scratch.innerHTML = html;
    // The box's own markers, and the old face box's section heading ("What").
    if (scratch.querySelector('[data-testid="node-summary"], .node-summary, .wf-node-summary')
        || [...scratch.querySelectorAll("div")].some((x) => x.childElementCount === 0
                                                       && x.textContent === "What")) faceBox.push(t);
    const ids = [...scratch.querySelectorAll(".test-port")].map((x) => x.getAttribute("data-k")).sort().join(",");
    const key = `${t}${wiredState ? " wired" : ""}`;
    if (GOLDEN[key] !== ids) moved.push(`${key}: ${GOLDEN[key]} -> ${ids}`);
  }
}
check("every node face renders", !ferr.length, ferr.join("\n      "));
check("NO node face carries the in / does / out box", !faceBox.length, faceBox.join(", "));
check("every type draws exactly the handle ids it drew before (wired and unwired, 'out' kept)",
      !moved.length, moved.join("\n      "));
{
  const el = document.body.appendChild(document.createElement("div"));
  const html = drawFace(el, nodeTypes["analytics.counter"],
                        { id: "n1", type: "analytics.counter", data: defaults("analytics.counter"), selected: false },
                        ctxFor(false));
  el.remove();
  check("the Counter face keeps its job pill (object counter)", html.includes("object counter"));
}

// -- 5. no toggle left behind ---------------------------------------------------------
const page = readFileSync(join(FRONT, "src/pages/Workflow.jsx"), "utf8");
const nodesSrc = readFileSync(join(FRONT, "src/workflow/nodes.jsx"), "utf8");
check("no 'Show node details' toggle, key or context flag remains",
      !page.includes("Show node details") && !page.includes("wf.nodeDetails")
      && !page.includes("showDetails") && !nodesSrc.includes("showDetails"));

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
