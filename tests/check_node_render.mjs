// Every workflow node component must actually RENDER, wired and unwired.
//
// Written after `ReferenceError: isWired is not defined` reached a real
// appliance and took the whole Workflow page down with "Unexpected
// Application Error!". The edit added portStyle(..., isWired(...)) calls to
// DatasetNode but the matching `const isWired = ...` helper landed only in
// NodeShell and QueryNode -- DatasetNode destructures `variables` too, so the
// pattern the companion edit matched skipped it. Nothing could catch that:
// there is no lint script, esbuild/Rollup do no scope analysis (an undefined
// identifier is a valid build), and no test mounted a single node component.
//
// So this mounts EVERY type the server registry knows, through the same
// buildNodeTypes the canvas uses, in BOTH states that matter -- nothing wired
// and everything wired -- because the crash above lived on exactly one of
// those paths in exactly one component. A throw from any type in either state
// fails the suite.
//
// It also pins the port CONTRACT those isWired calls exist to draw:
// a value port is a circle (border + 50% radius) on both sides, and wired must
// not style identically to unwired. The Formula's dotted link pair is exempt.
// Pinned to the SHAPE and to wired-differs-from-unwired, never to a colour,
// so a palette tweak passes and losing the distinction does not.
//
//   node tests/check_node_render.mjs
import { rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
// esbuild, react, react-dom and jsdom are the FRONTEND's dependencies -- this
// repo root has no node_modules of its own, so resolve from there rather than
// making the whole test suite depend on a root-level install.
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".node-render-test.cjs");

// -- the registry, from the one place it is true ------------------------------
//
// The node registry is SERVER state (core/services/workflow.py REGISTRY,
// served as GET /api/workflow); a fixture copy here would rot the day a type
// is added and this test would silently never render it. So the real thing is
// dumped to JSON by the same interpreter the suite runs under. json.dumps
// escapes non-ASCII, so the Windows console codepage cannot mangle it.
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

// -- a DOM for React to mount into -------------------------------------------
const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle"]) {
  global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const fail = (msg) => { console.error(`FAIL ${msg}`); bad += 1; };

// React Flow's real <Handle> can only mount inside <ReactFlow> (it reads an
// internal HandleConfigProvider and the zustand store, and throws outside
// them). The bug class this test exists for lives in OUR components -- the
// props they compute BEFORE Handle renders -- so nodes.jsx's import of
// @xyflow/react is shimmed to a Handle that renders a plain marker div and
// carries the computed style verbatim in data-style, where jsdom's lossy CSS
// serialiser cannot drop clip-path on the way through. Same precedent as the
// ResponsiveContainer shim in check_widget_render.mjs: shim the library part
// that cannot live in jsdom, test every line of ours.
const shimXyflow = {
  name: "shim-xyflow",
  setup(b) {
    b.onResolve({ filter: /^@xyflow\/react$/ }, (args) => (
      args.importer.replace(/\\/g, "/").endsWith("workflow/nodes.jsx")
        ? { path: "xyflow-shim", namespace: "shim" } : null));
    b.onLoad({ filter: /^xyflow-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement } from "react";
        export const Position = { Left: "left", Right: "right",
                                  Top: "top", Bottom: "bottom" };
        // Handles are re-measured by React Flow through this hook; under
        // test there is nothing to measure, so it records the calls instead
        // -- which is how a node that CHANGES its ports can be checked for
        // asking for the re-measure at all (see smoke_handlesync.py for the
        // bug: an edge whose handle is missing from the cache is not drawn).
        globalThis.__nodeInternalUpdates = [];
        export function useUpdateNodeInternals() {
          return (id) => { globalThis.__nodeInternalUpdates.push(id); };
        }
        // A node may read the canvas store to say something about what is
        // WIRED to it (the CSV file borrows its name from the Query feeding
        // it). Under test the store is whatever a case puts here, so a node
        // that consults it renders instead of throwing.
        globalThis.__flowStore = { edges: [], nodeLookup: new Map() };
        export function useStore(selector) {
          return selector(globalThis.__flowStore);
        }
        export function Handle({ type, position, id, style, title }) {
          return createElement("div", {
            className: "test-port",
            "data-porttype": type,
            "data-handleid": id || "",
            "data-position": position,
            "data-style": JSON.stringify(style || {}),
            title, style,
          });
        }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import { WorkflowCtx, buildNodeTypes }
  from "${join(FRONT, "src/workflow/nodes.jsx").replace(/\\/g, "/")}";
export { buildNodeTypes };
export function draw(el, Comp, props, ctx) {
  const root = createRoot(el);
  let err = null;
  // React reports a render throw through the console as well as rethrowing;
  // the throw is what this asserts on, so the console noise is silenced.
  const quiet = console.error;
  console.error = () => {};
  try {
    act(() => { root.render(
      h(WorkflowCtx.Provider, { value: ctx }, h(Comp, props))); });
  } catch (e) {
    err = e;
  } finally {
    console.error = quiet;
  }
  const html = err ? "" : el.innerHTML;
  try { act(() => root.unmount()); } catch { /* already dead */ }
  if (err) throw err;
  return html;
}

// The canvas loads in TWO steps: nodes first, then the graph's edges. A node
// whose ports depend on its wiring (a Trigger's Zone port, a Math node's gate)
// therefore renders once WITHOUT them and again with -- and React Flow, which
// measured the first shape, must be told. This mounts a node, feeds it a
// second context, and reports both what it drew and whether the re-measure
// was asked for.
export function drawTwice(el, Comp, props, ctx1, ctx2) {
  globalThis.__nodeInternalUpdates = [];
  const root = createRoot(el);
  act(() => { root.render(
    h(WorkflowCtx.Provider, { value: ctx1 }, h(Comp, props))); });
  const first = el.innerHTML;
  act(() => { root.render(
    h(WorkflowCtx.Provider, { value: ctx2 }, h(Comp, props))); });
  const second = el.innerHTML;
  const updates = [...globalThis.__nodeInternalUpdates];
  try { act(() => root.unmount()); } catch { /* already dead */ }
  return { first, second, updates };
}
`;

await build({
  plugins: [shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  // CJS, not ESM: react-dom is CommonJS and reaches for node builtins with
  // bare require(), which an ESM bundle turns into a throw.
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  // React's `act` is a development-build API; the production build drops it.
  define: { "process.env.NODE_ENV": '"development"' },
});

const { draw, drawTwice, buildNodeTypes } = req(OUT);
const nodeTypes = buildNodeTypes(registry);

// The canvas's context, in its two states. `wired`/`incoming` only ever get
// asked .has(), so the wired state answers yes for EVERY key rather than this
// test having to know each node's handle ids -- which is also what makes it
// impossible for a renamed handle to quietly fall back to the unwired path.
const VARIABLES = {
  tables: [{ key: "t1", name: "Events", columns: [
    { name: "zone", type: "text" },
    { name: "count", type: "number" },
    { name: "when", type: "datetime" },
  ] }],
  cameras: [{ id: "cam1", name: "Door cam" }],
  zone_list: [{ id: "z1", name: "Zone A", kind: "polygon" }],
};
const EVERYTHING = { has: () => true };
const ctxFor = (wired) => ({
  registry, values: {}, errors: {}, warnings: {}, variables: VARIABLES,
  resizeNode: null,
  incoming: wired ? EVERYTHING : new Set(),
  wired: wired ? EVERYTHING : new Set(),
  offNodes: new Map(), fanIn: new Map(), edges: [], objClasses: new Map(),
});

// Data that steers a component into its handle-rendering code. Everything not
// named here renders with {} -- the state a freshly dropped node starts in.
//
// output.dataset NEEDS variables.tables to hold a table whose key matches
// data.dataset, with columns: without that match DatasetNode returns the
// "pick a table" placeholder before a single column Handle -- and so before a
// single portStyle/isWired call -- renders. The shipped isWired crash lived
// exactly in those per-column calls, so a dataset node without its table
// would have passed this test straight through the bug.
const DATA = {
  "output.dataset": { dataset: "t1", op: "insert", mode: "alert",
                      sources: { count: { src: "stat" } } },
  "storage.query": { name: "Busiest zone",
                     sql: "SELECT zone, count(*) FROM events" },
};

// Beyond the one-per-type sweep: the shapes a type only takes with real
// config -- every Switch ctype, both Variable roles, the per-PID counter
// ports, the Formula's gate, the empty-table Dataset branches.
const VARIANTS = [
  ["logic.switch", { ctype: "multi", join: "and", otherwise: true,
                     rules: [{ input: "in1", op: ">", value: 3 },
                             { input: "in2", op: ">", value: 7 }] }],
  ["logic.switch", { ctype: "compare",
                     rules: [{ input: "in1", op: ">", value: 1,
                               more: [{ link: "or", input: "in2",
                                        op: "<", value: 5 }] }] }],
  ["logic.switch", { ctype: "switch",
                     rules: [{ op: "==", value: 1 }, { op: "==", value: 2 }] }],
  ["logic.switch", { ctype: "range",
                     rules: [{ op: "between", value: 0, value2: 10 },
                             { op: ">", value: 10 }] }],
  ["logic.switch", { ctype: "boolean" }],
  ["variable.ref", { name: "x", io: "in", key: "fn_x_value" }],
  ["variable.ref", { name: "x", io: "out" }],
  ["analytics.frame", { mode: "distinct", pid: true }],
  ["analytics.violations", { mode: "distinct", pid: true, limit: 2 }],
  ["analytics.violations", { mode: "realtime" }],
  ["analytics.dwell", { mode: "countdown", pid: true }],
  ["analytics.dwell", { mode: "dwell", pid: true }],
  ["analytics.formula", { op: "add", inputs: 3, gated: true, name: "F" }],
  ["analytics.formula", { op: "clamp" }],
  ["dashboard.events", { source_mode: "metric", metric: "m" }],
  // The Card and the Chart asking recorded data, which is where the Event
  // Card went: they draw its summary and its output port.
  ["dashboard.card", { source_mode: "events", kind: "table" }],
  ["dashboard.card", { source_mode: "events", filters: { type: "alert" } }],
  ["dashboard.graph", { source_mode: "dataset", kind: "bar", dataset: "t",
                        group_by: "reason" }],
  ["dashboard.graph", { kind: "line" }],
  ["dashboard.card", { kind: "gauge", metric: "m" }],
  ["dashboard.verdict", { states: [{ label: "OK" }, { label: "BAD" }] }],
  ["output.dataset", { dataset: "missing" }],
  ["output.dataset", {}],
  ["storage.query", {}],
  ["common.link_out", { link: "jump" }],
  ["common.link_in", { link: "jump" }],
  // The probe in its saved shape: canvases in the field carry one with a key
  // typed into the inspector and no wire, and that path renders a different
  // branch of the readout from the freshly-dropped one the sweep above
  // covers. (Every state it can be in is driven in check_debugnode.mjs; this
  // row is here so the port contract is pinned for the typed one too.)
  ["common.debug", { metric: "fn_bay_count" }],
  ["note.comment", { text: "hello", width: 200, height: 90 }],
];

const host = document.body;
const scratch = document.createElement("div");

const render = (type, data, wired) => {
  const el = document.createElement("div");
  host.appendChild(el);
  try {
    return draw(el, nodeTypes[type],
                { id: "n1", type, data, selected: false }, ctxFor(wired));
  } finally { host.removeChild(el); }
};

// The shim's marker divs, with the style object portStyle actually computed.
const ports = (html) => {
  scratch.innerHTML = html;
  return [...scratch.querySelectorAll(".test-port")].map((p) => ({
    kind: p.getAttribute("data-porttype"),
    id: p.getAttribute("data-handleid"),
    style: JSON.parse(p.getAttribute("data-style")),
  }));
};

if (Object.keys(nodeTypes).length !== Object.keys(registry).length) {
  fail("buildNodeTypes did not map every registry type");
}

let checked = 0;
const cases = [
  // The registry's OWN defaults underneath, because that is what a node
  // dropped on the canvas actually holds -- addNode spreads spec.data. Testing
  // against bare {} rendered nodes in a state no real node is ever in, which
  // is how a Command node with two default buttons was checked as a Command
  // node with none.
  ...Object.keys(registry).map((t) => [t, { ...(registry[t].data || {}),
                                            ...(DATA[t] || {}) }]),
  ...VARIANTS,
];
for (const [type, data] of cases) {
  if (!registry[type]) { fail(`variant for unknown type ${type}`); continue; }
  let un, wi;
  try {
    un = render(type, data, false);
    wi = render(type, data, true);
  } catch (e) {
    fail(`${type} (${JSON.stringify(data).slice(0, 60)}) threw: ${e.message}`);
    continue;
  }
  checked += 1;
  if (!un || !wi) { fail(`${type} rendered nothing`); continue; }

  const pUn = ports(un), pWi = ports(wi);
  // AN OUTPUT MUST HAVE A WAY IN. Nothing in this file used to say so, which
  // is how a Command node shipped with a button list, a port label for each
  // one, and no dots at all -- `portsInBody` means "this node draws its own
  // port rows", and a node that sets it while drawing none renders none.
  // Silent: the node looked finished, and the wire simply could not be
  // started.
  // A Data Table is one exception, and honestly so: its ports ARE its
  // columns, and a table nobody has given a column yet has nothing to wire.
  // Snapshots is the other: it wires as an output so the pipeline rules can
  // reason about it, but it READS what the box already saved and answers a
  // command button with it -- there is nothing on the canvas to wire in, and
  // its camera is a setting because no node "is" a camera to draw from.
  if (registry[type].category === "output" && registry[type].rank > 0
      && type !== "output.dataset" && type !== "integration.photos"
      && !pUn.some((p) => p.kind === "target")) {
    fail(`${type} is an output with no input port - nothing can be wired to it`);
  }
  if (pUn.length !== pWi.length) {
    fail(`${type} grew or lost ports when wired `
         + `(${pUn.length} -> ${pWi.length})`);
    continue;
  }
  pUn.forEach((p, i) => {
    // The link pair is the one deliberate exception: an association drawn
    // dotted, carrying no value, on the Formula alone. portStyle never
    // touches it and "wired" changes nothing about it, so both the shape
    // rule and the wired-differs rule below skip it.
    if (p.id === "link_in" || p.id === "link_out") return;
    const where = `${type} ${p.kind} port "${p.id}"`;
    // Every value port is a CIRCLE, on BOTH sides. Shape is not spent on
    // direction -- a port already sits on the side of the node it belongs
    // to. The colour inside the border is the palette's business, not this
    // test's.
    if (p.style.borderRadius !== "50%" || !/solid/.test(p.style.border || "")) {
      fail(`${where} is a value port and must be a circle (border + 50% radius)`);
    }
    // Wired must LOOK different from unwired -- which channel says so
    // (fill, opacity, saturation) is a design choice; that SOMETHING says
    // so is the contract.
    if (JSON.stringify(p.style) === JSON.stringify(pWi[i].style)) {
      fail(`${where} styles identically wired and unwired`);
    }
  });
}

// -- the two components that do not go through NodeShell ----------------------
//
// DatasetNode is where the shipped crash lived; these prove the sweep above
// actually reached its column handles rather than an early placeholder.
// A type that already failed the sweep must not crash the run here -- this
// section only ADDS "and the sweep reached the deep lines" on top.
const tryRender = (type) => {
  try { return render(type, DATA[type], false); } catch { return ""; }
};
const ds = tryRender("output.dataset");
const dsPorts = ports(ds);
// One whole-table source + a write-in and read-out pair per column.
if (dsPorts.length < 1 + 2 * VARIABLES.tables[0].columns.length) {
  fail(`DatasetNode rendered ${dsPorts.length} ports -- the column handles `
       + "were never reached");
}
for (const col of VARIABLES.tables[0].columns) {
  if (!ds.includes(col.name)) fail(`DatasetNode did not render column ${col.name}`);
}
// The trigger port follows the MODE: alert and crossing are fired by
// something wired in, the clock modes are not. A crossing-mode table that
// lost its port would compile fine from the column wires and never show
// where the Line Counter goes; a change-mode table that grew one would
// invite a wire the compiler ignores.
const crossHtml = (() => {
  try {
    return render("output.dataset", { ...DATA["output.dataset"], mode: "crossing" }, false);
  } catch { return ""; }
})();
const crossPorts = ports(crossHtml);
if (!crossHtml.includes("on crossing")) {
  fail("DatasetNode in crossing mode did not say 'on crossing' on its face");
}
if (!crossPorts.some((p) => p.kind === "target" && p.id === "trig")) {
  fail("DatasetNode in crossing mode did not render its trig port");
}
if (crossPorts.length < 1 + 1 + 2 * VARIABLES.tables[0].columns.length) {
  fail(`DatasetNode in crossing mode rendered ${crossPorts.length} ports -- `
       + "the column handles were never reached");
}
const changeHtml = (() => {
  try {
    return render("output.dataset", { ...DATA["output.dataset"], mode: "change" }, false);
  } catch { return ""; }
})();
if (ports(changeHtml).some((p) => p.id === "trig")) {
  fail("DatasetNode in change mode rendered a trig port nothing can fire");
}

const q = tryRender("storage.query");
const qPorts = ports(q);
if (!qPorts.some((p) => p.kind === "target")
    || !qPorts.some((p) => p.kind === "source")) {
  fail("QueryNode did not render both its table-in and result-out ports");
}
if (!q.includes("SELECT zone")) fail("QueryNode did not preview its SQL");

// -- the object nodes still have every port -----------------------------------
//
// Their in / does / out rows moved to the Properties panel (2026-10-14), and
// the shell draws their ports like every other node's. The failure this
// guards against is silent: a node that renders perfectly with no way in or
// out of it. Nothing above would notice -- the sweep only compares the wired
// and unwired counts to each other, and zero equals zero. Their one output
// keeps the id "out" that edges saved in the face-box era carry.
for (const type of ["analytics.object_frame", "analytics.object_zone",
                    "analytics.frame", "analytics.violations"]) {
  const html = tryRender(type);
  const ps = ports(html).filter((p) => p.id !== "link_in" && p.id !== "link_out");
  const ins = ps.filter((p) => p.kind === "target").length;
  const outs = ps.filter((p) => p.kind === "source");
  const wantIns = registry[type].zone_kind ? 2 : 1;
  if (ins !== wantIns || outs.length !== 1) {
    fail(`${type} drew ${ins} input(s) and ${outs.length} output(s) -- expected `
         + `${wantIns} and 1`);
  }
  if (outs.length === 1 && outs[0].id !== "out") {
    fail(`${type} output is "${outs[0].id}", not "out" -- saved edges would not draw`);
  }
}

// -- ports that appear WITH the wiring, and the re-measure they owe --------
//
// THE WIRE THAT VANISHED. The canvas loads its nodes before its edges, so a
// node whose ports depend on wiring renders once without them. React Flow
// measures that first shape and caches it by handle id; if the second shape
// arrives unannounced, the edge to the new port has nothing to attach to and
// is simply not drawn. The wire is still in the graph and comes back on
// reload -- which is exactly how it was reported.
//
// A Trigger with a Zone wired to it is the case that found this: no
// zone_color set, so the port exists ONLY because of the wire.
{
  const el = document.createElement("div");
  const type = "output.trigger";
  const Comp = nodeTypes[type];
  const props = { id: "trig1", type, selected: false,
                  data: { name: "not violate" } };
  const before = ctxFor(false);
  const after = { ...ctxFor(true),
                  edges: [{ id: "e1", source: "z1", target: "trig1",
                            targetHandle: "zone" }] };
  const { first, second, updates } = drawTwice(el, Comp, props, before, after);
  const ids = (html) => [...html.matchAll(/data-handleid="([^"]*)"/g)]
    .map((m) => m[1]).filter((h) => h);
  if (ids(first).includes("zone")) {
    fail("a Trigger drew a Zone port before anything was wired to it");
  }
  if (!ids(second).includes("zone")) {
    fail("a Trigger with a Zone wired in did not draw its Zone port "
         + `(handles: ${ids(second).join(",") || "none"})`);
  }
  if (!updates.includes("trig1")) {
    fail("the Trigger changed its ports without asking React Flow to "
         + "re-measure -- this is the bug where the wire disappears until "
         + "the page is reloaded");
  }
  checked += 1;
}

// OBJECT DETECTION with OID ticked hands out WHICH object arrived, beside
// its found/class ports -- and not without it.
{
  const el = document.createElement("div");
  const type = "analytics.object";
  const Comp = nodeTypes[type];
  const idsOf = (html) => [...html.matchAll(/data-handleid="([^"]*)"/g)]
    .map((m) => m[1]);
  const ctx = { ...ctxFor(true), objClasses: new Map([["o1", ["person"]]]) };
  const withOid = idsOf(draw(el, Comp, { id: "o1", type, selected: false,
                                          data: { OID: true } }, ctx));
  const without = idsOf(draw(el, Comp, { id: "o1", type, selected: false,
                                          data: { OID: false } }, ctx));
  const bare = idsOf(draw(el, Comp, { id: "o2", type, selected: false,
                                       data: { OID: true } }, ctxFor(true)));
  if (!withOid.includes("OID") || !withOid.includes("found")
      || !withOid.includes("person")) {
    fail(`Object Detection with OID lacks a port (handles: ${withOid.join(",")})`);
  }
  if (without.includes("OID")) {
    fail("Object Detection draws an OID port with OID off");
  }
  if (!bare.includes("OID") || !bare.includes("found")) {
    fail(`Object Detection with no Class Filter lost its OID port (${bare.join(",")})`);
  }
  checked += 1;
}

// A CHART SWITCHED TO RECORDED EVENTS with rules still on its series ports.
// Its question is one input, but a port a wire sits on must still be drawn --
// otherwise the s2 wire vanishes from the canvas while the compiler still
// reads it (it counts the first rule and names the rest).
{
  const el = document.createElement("div");
  const type = "dashboard.graph";
  const Comp = nodeTypes[type];
  const props = { id: "g1", type, selected: false,
                  data: { kind: "bar", source_mode: "events" } };
  const wiredCtx = { ...ctxFor(true), fanIn: new Map([["g1", 2]]),
                     edges: [{ id: "e1", source: "r1", target: "g1",
                               targetHandle: "in" },
                             { id: "e2", source: "r2", target: "g1",
                               targetHandle: "s2" }] };
  const html = draw(el, Comp, props, wiredCtx);
  const ids = [...html.matchAll(/data-handleid="([^"]*)"/g)].map((m) => m[1]);
  if (!ids.includes("in") || !ids.includes("s2")) {
    fail("a recorded Chart with a rule on s2 did not draw the s2 port "
         + `(handles: ${ids.join(",") || "none"})`);
  }
  if (!html.includes("counts what the first raised")) {
    fail("a recorded Chart with two rules wired claimed to count 'the wired "
         + "function' as if there were one");
  }
  checked += 1;
}

rmSync(OUT, { force: true });

if (bad) {
  console.error(`\n${bad} node render problem(s).`);
  process.exit(1);
}
console.log(`== ${checked} node type/variant cases render wired and unwired; `
            + "value ports are circles, wired ports distinct");
