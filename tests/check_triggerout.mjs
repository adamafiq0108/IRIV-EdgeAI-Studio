// The Trigger node's inspector, in Tok Aba's 2026-10-14 layout (the
// numbered sections are pinned in check_triggerlayout.mjs):
//
//   1. Advanced settings is one fold, closed by default; the name is outside
//   2. the three ticks are opt-in rows under "What should happen?", each
//      saying what is set without being opened
//   3. the payloads are the Inject's TypedInput (same types, same glyphs)
//   4. "once per object (OID)" is offered only with OID ticked upstream
//   5. a rate set hides the alarm's own "raise again", one answer only
//   6. the node face names the new settings
//   7. the canvas accepts a Trigger wired into MQTT Publish and into Modbus
//      Write (value and trigger ports), plain or with an Advanced payload,
//      and agrees with the server about it
//
//   node tests/check_triggerout.mjs
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".triggerout-test.cjs");

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
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        export const api = { post() { return Promise.resolve({}); },
                             get() { return Promise.resolve({}); },
                             put() { return Promise.resolve({}); } };
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
import { WorkflowCtx, buildNodeTypes, triggerWords,
         triggerAdvanced } from "${p("src/workflow/nodes.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
import { edgeAllowed } from "${p("src/workflow/registry.js")}";
export { act, h, Inspector, ToastProvider, WorkflowCtx, buildNodeTypes,
         triggerWords, triggerAdvanced, edgeAllowed };
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

// The server's own registry entry, so a default added there is a default
// here too (the one source of node data is core/services/workflow.py).
const TRIGGER_DATA = {
  name: "", alert: false, severity: "warn", message: "", cooldown_s: 30,
  repeat: "cooldown", draw: false, box_color: "", zone_color: "",
  color_target: "box", payload_type: "", payload: "", idle_type: "",
  idle_payload: "", rate: "", rate_s: 10, out_hold: "", out_hold_s: 0,
};
const wfSrc = readFileSync(join(ROOT, "core", "services", "workflow.py"), "utf8");
for (const key of ["payload_type", "idle_type", "idle_payload", "rate",
                   "rate_s", "out_hold", "out_hold_s"]) {
  check(`the server registry carries ${key}`, wfSrc.includes(`"${key}": `));
}
const REGISTRY = {
  "output.trigger": { category: "output", label: "Trigger", badge: "TRIG",
                      rank: 4, palette: { cat: "logic" }, data: TRIGGER_DATA },
  "analytics.frame": { category: "analytics", label: "In Frame", badge: "FRM",
                       rank: 2, metrics: ["count"],
                       data: { fnId: "", name: "In Frame", OID: false } },
  "branch.multi": { category: "logic", label: "Compare", badge: "CMP",
                    rank: 3, data: { fnId: "", rules: [] } },
  "zone.ref": { category: "zone", label: "Zone", badge: "ZONE", rank: 1,
                data: { zone: "" } },
};

function render(node, fn) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, node);
  try { return fn ? fn(box) : box.innerHTML; } finally {
    r.unmount(); document.body.removeChild(box);
  }
}

function inspect(data, { oid = false, oidPort = false, ruleZone = "",
                         portZone = "" } = {}, fn) {
  const trig = { id: "t1", type: "output.trigger",
                 data: { ...TRIGGER_DATA, name: "Crowded", ...data } };
  const rule = { id: "fr", type: "analytics.frame",
                 data: { fnId: "crowd", name: "Crowd", OID: oid } };
  const cmp = { id: "cmp", type: "branch.multi", data: { fnId: "many" } };
  const nodes = [rule, cmp, trig];
  const edges = [{ id: "e1", source: "fr", target: "cmp", targetHandle: "in1" },
                 { id: "e2", source: "cmp", target: "t1", sourceHandle: "out0",
                   targetHandle: "in" }];
  if (oidPort) {
    edges.push({ id: "e3", source: "fr", target: "t1", sourceHandle: "OID",
                 targetHandle: "oid" });
  }
  // A Zone into the rule's zone port (a zone rule behind the Trigger), and/or
  // into the Trigger's own Zone port.
  if (ruleZone) {
    nodes.push({ id: "zr", type: "zone.ref", data: { zone: ruleZone } });
    edges.push({ id: "e6", source: "zr", target: "fr", targetHandle: "zone" });
  }
  if (portZone) {
    nodes.push({ id: "zp", type: "zone.ref", data: { zone: portZone } });
    edges.push({ id: "e7", source: "zp", target: "t1", targetHandle: "zone" });
  }
  const changes = [];
  const out = render(h(m.ToastProvider, null, h(m.Inspector, {
    node: trig, registry: REGISTRY, meta: { variables: {} }, nodes, edges,
    editable: true, errors: [], warnings: [],
    onChange: (patch) => changes.push(patch),
    functions: [], onDone: () => {} })), fn ? (box) => fn(box, changes) : undefined);
  return out;
}

// -- 1. the layout (Tok Aba, 2026-10-14; check_triggerlayout.mjs pins more) ---
// Rows of "What should happen?" open with their chevron; Advanced settings is
// a <details>, closed.
const row = (box, id) => box.querySelector(`[data-trg-row="${id}"]`);
const openRow = (box, id) => {
  const b = row(box, id)?.querySelector(".trg-row-more");
  if (b && b.getAttribute("aria-expanded") !== "true") m.act(() => { b.click(); });
};
inspect({}, {}, (box) => {
  const adv = box.querySelector("details[data-trg-advanced]");
  check("the Trigger has an Advanced settings fold, collapsed",
        !!adv && !adv.open, box.innerHTML.slice(0, 300));
  const name = box.querySelector('input[placeholder^="e.g. Overcrowded"]');
  check("the name stays outside it, in the open", name && !adv?.contains(name));
  // -- 2. the three ticks are opt-in rows under What should happen? -----------
  for (const [id, word] of [["alert", "Raise an alert"], ["boxes", "Highlight objects"],
                            ["zone", "Highlight the zone"]]) {
    const tick = row(box, id)?.querySelector(`input[type="checkbox"][aria-label="${word}"]`);
    check(`'${word}' is an opt-in row, not inside Advanced`, !!tick && !adv?.contains(tick));
  }
  check("the Firing cards are in the open; Output behavior waits in the closed payload row",
        !!row(box, "payload") && !!box.querySelector("[data-rc^='fire-'] [role=radiogroup]")
        && !box.querySelector("[data-rc^='hold-']")
        && !adv.contains(box.querySelector("[data-rc^='fire-']")));
  check("an untouched Trigger shows no payload picker yet",
        !box.querySelector('select[aria-label="Payload when triggered type"]'));
});

// A row that is set says what is set, without being opened.
inspect({ alert: true, severity: "crit", message: "Too many", payload_type: "string",
          payload: "door" }, {}, (box) => {
  check("the alert row's line says what is set",
        row(box, "alert").querySelector("[data-trg-line]").textContent
          === "Alert: critical, 'Too many', again every 30 s",
        row(box, "alert").querySelector("[data-trg-line]").textContent);
  check("...and the payload row's",
        row(box, "payload").querySelector("[data-trg-line]").textContent
          === "Sends door when triggered, 0 when not triggered, live");
});

// -- 3. the Inject's picker ----------------------------------------------------
inspect({ payload_type: "number", payload: "5" }, {}, (box) => {
  openRow(box, "payload");
  const sel = box.querySelector('select[aria-label="Payload when triggered type"]');
  check("a ticked payload is the Inject's type picker", !!sel);
  const opts = sel ? [...sel.options].map((o) => o.value) : [];
  check("...offering every Inject type",
        ["string", "number", "boolean", "json", "timestamp"]
          .every((t) => opts.includes(t)), opts.join(","));
  check("...with the same glyphs", ["a-z", "0-9", "true / false", "JSON",
                                    "timestamp"].every((t) => sel?.textContent.includes(t)),
        sel?.textContent);
  const val = box.querySelector('input[aria-label="Payload when triggered"]');
  check("...and the value beside it", val?.value === "5");
});
inspect({ payload_type: "json", payload: "{bad" }, {}, (box) => {
  openRow(box, "payload");
  check("a JSON payload that does not parse says so on the spot",
        box.textContent.includes("does not parse"));
});
inspect({ idle_type: "boolean", idle_payload: "false" }, {}, (box) => {
  openRow(box, "payload");
  check("the idle payload has the same picker",
        !!box.querySelector('select[aria-label="Payload when not triggered type"]'));
});
inspect({}, {}, (box, changes) => {
  openRow(box, "payload");
  const tick = [...box.querySelectorAll("label.wf-check")]
    .find((l) => l.textContent.startsWith("Send payload when triggered"));
  m.act(() => { tick.querySelector("input").click(); });
  check("ticking the payload starts it as the number 1",
        JSON.stringify(changes.at(-1)) === JSON.stringify(
          { payload_type: "number", payload: "1" }), JSON.stringify(changes));
});
inspect({ out_hold_s: -2 }, {}, (box) => {
  check("a negative hold is refused in the panel too",
        box.textContent.includes("The hold time must be between 0 and 86400"));
});
// -- 3b. the three holds, now "Output behavior" cards in the Payload options row
const holdOf = (box) => box.querySelector("[data-rc^='hold-'] [role=radio][aria-checked='true']")?.dataset.radio;
const holdCard = (box, id) => box.querySelector(`[data-rc^='hold-'] [data-radio="${id}"]`);
inspect({}, {}, (box, changes) => {
  openRow(box, "payload");
  const g = box.querySelector("[data-rc^='hold-']");
  check("Output behavior sits in the Payload options row, not in step 2",
        !!g && row(box, "payload").contains(g)
        && !box.querySelector("section[aria-labelledby='trg2-t1']")?.contains(g));
  check("Output behavior is a choice of three: live, interval, for N seconds",
        [...g.querySelectorAll("[role=radio]")].map((r) => r.dataset.radio).join(",")
          === "realtime,interval,hold");
  check("...a fresh Trigger follows the condition (live), no seconds",
        holdOf(box) === "realtime" && !box.textContent.includes("Re-check every (s)")
        && !box.textContent.includes("Stay active for (s)")
        && box.textContent.includes("Output stays active while the condition is true, then turns off."));
  m.act(() => { holdCard(box, "interval").click(); });
  check("choosing Re-check starts it at 10 s",
        JSON.stringify(changes.at(-1))
          === JSON.stringify({ out_hold: "interval", out_hold_s: 10 }),
        JSON.stringify(changes));
});
inspect({ out_hold: "interval", out_hold_s: 7 }, {}, (box, changes) => {
  check("Re-check asks how often, and says what a re-check does",
        holdOf(box) === "interval"
        && box.textContent.includes("Re-check every (s)")
        && box.textContent.includes("still true with the same value stays active"));
  m.act(() => { holdCard(box, "realtime").click(); });
  check("...and back to live drops only the choice",
        JSON.stringify(changes.at(-1)) === JSON.stringify({ out_hold: "realtime" }),
        JSON.stringify(changes));
});
inspect({ out_hold: "hold", out_hold_s: 4 }, {}, (box) => {
  check("Stay active for N seconds asks how long",
        holdOf(box) === "hold" && box.textContent.includes("Stay active for (s)")
        && !box.textContent.includes("Re-check every (s)"));
});
inspect({ out_hold_s: 3 }, {}, (box) => {
  check("a Trigger saved with hold 3 opens as Stay active for N seconds, 3 (unchanged)",
        holdOf(box) === "hold"
        && box.querySelector('input[type="number"][min="0"]')?.value === "3");
});
inspect({ out_hold_s: 0, payload_type: "number", payload: "5" }, {}, (box) => {
  openRow(box, "payload");
  check("...and one saved with hold 0 opens as live", holdOf(box) === "realtime");
});
inspect({ out_hold: "interval", out_hold_s: 0 }, {}, (box) => {
  check("a chosen Interval of 0 is refused in the panel",
        box.textContent.includes("The hold interval must be between 1 and 86400"));
});
inspect({ rate: "cooldown", rate_s: 0 }, {}, (box) => {
  check("a cooldown of 0 is refused in the panel too",
        box.textContent.includes("The time between two fires must be between 1"));
});

// -- 3c. only boxes inside the zone (Tok Aba, 2026-10-07) ---------------------
const ONLY = "Only boxes inside the zone";
const onlyTick = (box) => [...box.querySelectorAll("label.wf-check")]
  .find((l) => l.textContent === ONLY);
inspect({ draw: true, box_color: "#FF0000" }, {}, (box) => {
  openRow(box, "boxes");
  check("no zone anywhere: 'Only boxes inside the zone' is not offered",
        !onlyTick(box));
  check("...and the colour's hint says plainly it is every object of the "
        + "rule's classes in view, and no other class",
        box.innerHTML.includes("every one of them in view, unless Only boxes inside")
        && box.innerHTML.includes("Other classes keep their own colour"));
});
inspect({ draw: false }, { ruleZone: "bay" }, (box) => {
  check("with a zone but no box colour it is not offered either",
        !onlyTick(box));
});
inspect({ draw: true, box_color: "#FF0000" }, { ruleZone: "bay" }, (box, changes) => {
  openRow(box, "boxes");
  const tick = onlyTick(box);
  check("a zone rule behind the Trigger offers it, unticked",
        !!tick && !tick.querySelector("input").checked);
  check("...saying what off means and naming the zone",
        box.textContent.includes("Every object of the rule's classes in view "
                                 + "takes this colour")
        && box.textContent.includes("Tick to keep it to bay"));
  m.act(() => { tick.querySelector("input").click(); });
  check("...ticking it sets box_in_zone",
        JSON.stringify(changes.at(-1)) === JSON.stringify({ box_in_zone: true }),
        JSON.stringify(changes));
});
inspect({ draw: true, box_color: "#FF0000", box_in_zone: true },
        { ruleZone: "bay", portZone: "floor" }, (box) => {
  openRow(box, "boxes");
  check("the Trigger's own Zone port answers before the rule's zone",
        box.textContent.includes("Only the objects inside floor take this "
                                 + "colour; the rest keep their own."));
});
inspect({ draw: true, box_color: "#FF0000" }, { portZone: "floor" }, (box) => {
  openRow(box, "boxes");
  check("a Zone on the Trigger's own port alone offers it",
        !!onlyTick(box));
});
check("the node line says when boxes are kept to the zone",
      m.triggerWords({ draw: true, box_in_zone: true }).includes("paints boxes in zone")
      && m.triggerWords({ draw: true }).includes("paints boxes"));
check("the server registry carries box_in_zone, off",
      wfSrc.includes('"box_in_zone": False'));

// -- 4. once per object only with OID upstream ---------------------------------
const fireCard = (box, v) => box.querySelector(`[data-rc^='fire-'] [data-radio="${v}"]`);
const optOn = (box, v) => !!fireCard(box, v) && fireCard(box, v).getAttribute("aria-disabled") !== "true";
inspect({}, { oid: false }, (box) => {
  check("without OID upstream, Once per object is not available, and why",
        !optOn(box, "per_object") && optOn(box, "every") && optOn(box, "rate")
        && box.textContent.includes("Once per object id (OID) is offered when"));
});
inspect({}, { oid: true }, (box) => {
  check("OID ticked on the rule upstream offers once per object", optOn(box, "per_object"));
});
inspect({}, { oidPort: true }, (box) => {
  check("...and so does a wire on the Trigger's own OID port", optOn(box, "per_object"));
});
inspect({ rate: "once_per_oid" }, { oid: false }, (box) => {
  check("a saved once-per-object whose OID was unticked keeps its choice, "
        + "and says what is missing",
        fireCard(box, "per_object").getAttribute("aria-checked") === "true"
        && box.textContent.includes("needs to know which object"));
});
inspect({ rate: "once_per_oid" }, { oid: true }, (box) => {
  check("...with OID it says how long an object is remembered",
        box.textContent.includes("for 10 minutes after it leaves"));
});

// -- 5. one answer to "how often" ----------------------------------------------
inspect({ alert: true }, {}, (box) => {
  openRow(box, "alert");
  check("an alert firing every time keeps its own Raise again every (s)",
        box.textContent.includes("Raise again every (s)"));
});
inspect({ alert: true, rate: "cooldown" }, {}, (box) => {
  openRow(box, "alert");
  check("with a rate set it gives way to the one firing choice",
        !box.textContent.includes("Raise again every (s)")
        && box.textContent.includes("How often it raises follows"));
});

// -- 6. the node face ----------------------------------------------------------
check("the face of an untouched Trigger says nothing new",
      m.triggerWords(TRIGGER_DATA).length === 0);
check("...and names each new setting",
      JSON.stringify(m.triggerWords({ payload_type: "timestamp",
                                      rate: "once_per_oid", out_hold_s: 2 }))
        === JSON.stringify(["sends the time", "once per OID", "holds 2s"]));
check("...the three holds read apart: holds live / re-checks every 10s / "
      + "holds 10s",
      JSON.stringify([{ payload_type: "number", payload: "1" },
                      { out_hold: "interval", out_hold_s: 10 },
                      { out_hold: "hold", out_hold_s: 10 },
                      { out_hold_s: 10 }].map((d) => m.triggerWords(d).at(-1)))
        === JSON.stringify(["holds live", "re-checks every 10s", "holds 10s",
                            "holds 10s"]));
check("...beside how often, which stays its own words",
      JSON.stringify(m.triggerWords({ rate: "cooldown", rate_s: 5,
                                      out_hold: "interval", out_hold_s: 10 }))
        === JSON.stringify(["at most every 5s", "re-checks every 10s"]));
check("...and a plain Trigger (Real-time, nothing sent) still says nothing",
      m.triggerWords({ out_hold: "realtime" }).length === 0
      && !m.triggerAdvanced({ out_hold: "realtime", out_hold_s: 9 })
      && m.triggerAdvanced({ out_hold: "interval", out_hold_s: 9 })
      && m.triggerAdvanced({ out_hold_s: 9 }));

// -- 7. a Trigger reaches MQTT Publish and Modbus Write --------------------------
// Against the server's OWN registry (dumped the way check_debugnode.mjs does),
// because the canvas rule reads each node's category from it. The wire rule
// does not look at the Trigger's data, so a plain Trigger and one with an
// Advanced payload are the same wire; both are asked anyway, so the day the
// rule starts caring about the payload this is where it shows.
const WIRES = [["output.trigger", "output.mqtt_publish", ""],
               ["output.trigger", "output.modbus_write", "in"],
               ["output.trigger", "output.modbus_write", "trig"]];
const ASK = "import json,sys\n"
  + "from core.services.workflow import REGISTRY, edge_allowed\n"
  + `wires = ${JSON.stringify(WIRES)}\n`
  + "sys.stdout.write(json.dumps({'registry': REGISTRY, 'answers': "
  + "[edge_allowed(s, t, h)[0] for s, t, h in wires]}))";
let dump = null;
for (const py of ["py", "python", "python3"]) {
  const r = spawnSync(py, ["-c", ASK], { cwd: ROOT, encoding: "utf8",
                                        maxBuffer: 64 * 1024 * 1024 });
  if (r.status === 0 && r.stdout) {
    try { dump = JSON.parse(r.stdout); } catch { /* partial output */ }
    if (dump) break;
  }
}
if (!dump) {
  check("could ask the server which Trigger wires it allows", false);
} else {
  WIRES.forEach(([src, tgt, handle], i) => {
    const [ok, why] = m.edgeAllowed(dump.registry, src, tgt, handle);
    check(`the canvas accepts ${src} -> ${tgt} (${handle || "in"})`, ok, why);
    check("...and so does the server", dump.answers[i] === true);
  });
  for (const data of [{}, { payload_type: "number", payload: "7" },
                      { payload_type: "string", payload: "door" }]) {
    const reg = { ...dump.registry,
                  "output.trigger": { ...dump.registry["output.trigger"],
                                      data: { ...TRIGGER_DATA, ...data } } };
    check(`...plain or Advanced alike (${JSON.stringify(data)})`,
          WIRES.every(([s, t, hd]) => m.edgeAllowed(reg, s, t, hd)[0]));
  }
}

// No em dash in what this feature added (the house rule).
const page = readFileSync(join(FRONT, "src", "pages", "Workflow.jsx"), "utf8");
const start = page.indexOf("function oidTrackedUpstream(");
const end = page.indexOf("// palette", start);
check("no em dash in the new inspector code",
      start > 0 && !page.slice(start, end).includes("\u2014"));

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall trigger Advanced checks passed");
dom.window.close();
process.exit(0);
