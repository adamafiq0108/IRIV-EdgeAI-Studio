// The Modbus Server connection's "Drive watchdog" switch, and the Modbus
// Write panel's pointer to it (Tok Aba 2026-10-07; app/buses.py
// watchdog_writes, core/services/modbus_watchdog.py).
//
//   1. the Modbus Write panel has NO per-node "Send"/"Keep sending" any more,
//      only a plain note pointing to the server's Drive watchdog; with the
//      switch on it says what that does to this node (every timeout / 2)
//   2. the connection editor: "Not managed" until first used; the switch
//      turns it on with a 1000 ms Timeout; off says what it writes; the
//      Advanced fold holds 105 / 100 and "Also save to the drive's
//      memory" (off: no save is mentioned or sent); bad numbers are said in
//      the panel;
//      "Stop managing it" goes back; Update sends the fields
//   3. plain words, no em dash
//
//   node tests/check_modbuswatchdog.mjs
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
const OUT = join(FRONT, "node_modules", ".modbuswatchdog-test.cjs");

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
        export const api = {
          post(url, body) { (globalThis.__posts ||= []).push({ url, body });
                            return Promise.resolve({ client: body }); },
          get() { return Promise.resolve({ clients: globalThis.__clients || [],
                                           serial_ports: [], editable: true }); },
          put() { return Promise.resolve({}); },
          del() { return Promise.resolve({}); } };
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
import ModbusServerEditor, { refreshModbusServers }
  from "${p("src/components/ModbusServerEditor.jsx")}";
import { edgeAllowed } from "${p("src/workflow/registry.js")}";
export { act, h, Inspector, ToastProvider, WorkflowCtx, buildNodeTypes,
         triggerWords, triggerAdvanced, edgeAllowed, ModbusServerEditor,
         refreshModbusServers };
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

// The server's own defaults for the node (core/services/workflow.py).
const DUMP = "import sys, json\nsys.path.insert(0, '.')\n"
  + "from core.services.workflow import REGISTRY\n"
  + "sys.stdout.write(json.dumps(REGISTRY['output.modbus_write']['data']))";
let MBW_DATA = null;
for (const py of ["py", "python", "python3"]) {
  const r = spawnSync(py, ["-c", DUMP], { cwd: ROOT, encoding: "utf8" });
  if (r.status === 0 && r.stdout) {
    try { MBW_DATA = JSON.parse(r.stdout); } catch { /* partial output */ }
    if (MBW_DATA) break;
  }
}
if (!MBW_DATA) {
  console.log("skip: no python interpreter to read the Modbus Write defaults");
  process.exit(77);
}
const REGISTRY = {
  "output.modbus_write": { category: "output", label: "Modbus Write",
                           badge: "MBW", rank: 4, output_type: "modbus_write",
                           palette: { cat: "integration", group: "Modbus" },
                           data: MBW_DATA },
};

function render(node, fn) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, node);
  try { return fn(box); } finally {
    r.unmount(); document.body.removeChild(box);
  }
}


async function settle() {
  await m.act(async () => {
    await m.refreshModbusServers().catch(() => {});
    await new Promise((r) => setTimeout(r, 0));
  });
}

async function inspect(clients, fn) {
  globalThis.__clients = clients;
  const node = { id: "mb", type: "output.modbus_write",
                 data: { ...MBW_DATA, server: "drive", address: 0 } };
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null, h(m.Inspector, {
    node, registry: REGISTRY, meta: { variables: {} }, nodes: [node], edges: [],
    editable: true, errors: [], warnings: [], onChange: () => {},
    functions: [], onDone: () => {} })));
  await settle();
  try { return fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

const DRIVE = { id: "drive", name: "Drive", label: "Drive", type: "tcp",
                host: "192.168.4.4", port: 502, unit_id: 1, timeout_ms: 1000,
                reconnect_on_timeout: true, reconnect_timeout_ms: 2000 };
const flat = (s) => (s || "").replace(/\s+/g, " ");

// -- 1. the Modbus Write panel ---------------------------------------------------
check("the node's defaults carry no repeat fields any more",
      !("repeat" in MBW_DATA) && !("repeat_s" in MBW_DATA), JSON.stringify(MBW_DATA));
await inspect([DRIVE], (box) => {
  check("no 'Send' choice on the node",
        !box.querySelector('select[aria-label="Send"]'));
  check("no 'Keep sending the last value' checkbox either",
        !box.querySelector('input[aria-label="Keep sending the last value"]')
        && !box.textContent.includes("Keep sending"));
  const note = box.querySelector("[data-watchdog-note]");
  check("a plain note points to the server's Drive watchdog",
        note && flat(note.textContent).includes(
          "A drive that stops a moment after each write has a Modbus watchdog: "
          + "set it under the server's Drive watchdog"), note?.textContent);
});
await inspect([{ ...DRIVE, watchdog: "on", watchdog_ms: 1000 }], (box) => {
  const note = box.querySelector('[data-watchdog-note="on"]');
  check("switch on: the node says it keeps sending every 0.5 s (half of 1000 ms)",
        note && flat(note.textContent).includes(
          "keeps sending its last value every 0.5 s"), note?.textContent);
});

// -- 2. the connection editor ------------------------------------------------------
async function editor(client, fn) {
  globalThis.__clients = [client];
  globalThis.__posts = [];
  await m.act(async () => { await m.refreshModbusServers(); });
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null, h(m.ModbusServerEditor, {
    serverId: client.id, onBack: () => {}, onSaved: () => {}, editable: true })));
  await settle();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}
const sw = (box) => box.querySelector('input[role="switch"][aria-label="Drive watchdog"]');
const state = (box) => box.querySelector("[data-watchdog-state]")?.textContent;
const text = (box) => flat(box.querySelector("[data-drive-watchdog]")?.textContent);
const setVal = (el, v) => {
  const proto = Object.getPrototypeOf(el);
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(v));
  el.dispatchEvent(new window.Event("input", { bubbles: true }));
};
const field = (box, label) => box.querySelector(`input[aria-label="${label}"]`);

await editor(DRIVE, async (box) => {
  check("an existing connection: the switch is there, unticked, 'Not managed'",
        sw(box) && !sw(box).checked && state(box) === "Not managed", state(box));
  check("...with the plain help text",
        text(box).includes("For motor drivers with a Modbus watchdog (for example "
          + "Cytron's WDT_MS). Off: the motor keeps the last speed until told "
          + "otherwise. On: the box keeps reminding the drive; if the box or "
          + "cable fails, the motor stops by itself."), text(box));
  check("...and no timeout or register fields while not managed",
        !field(box, "Watchdog timeout (ms)") && !field(box, "Watchdog register"));
  m.act(() => { sw(box).click(); });
  check("switching it on: 'On', Timeout 1000 ms",
        state(box) === "On" && field(box, "Watchdog timeout (ms)")?.value === "1000",
        state(box));
  check("...it says what it does: 10 to 105, keep sending every 0.5 s, no save",
        text(box).includes("writes 10 to register 105.")
        && !text(box).includes("writes 1 to register 100")
        && text(box).includes("keeps sending its last value every 0.5 s"), text(box));
  const fold = box.querySelector("[data-drive-watchdog] details");
  check("the Advanced fold holds the two registers, 105 and 100",
        fold && fold.querySelector("summary").textContent === "Advanced"
        && fold.querySelector('input[aria-label="Watchdog register"]').value === "105"
        && fold.querySelector('input[aria-label="Save register"]').value === "100");
  const tick = field(box, "Also save to the drive's memory");
  check("...and 'Also save to the drive's memory', unticked by default",
        tick && !tick.checked
        && text(box).includes("Also save to the drive's memory (survives the "
          + "drive's power cycle without the box)"), text(box));
  m.act(() => { tick.click(); });
  check("ticked: it says the save is written once per changed value",
        field(box, "Also save to the drive's memory").checked
        && text(box).includes("also writes 1 to register 100 (save) whenever that "
          + "value changed, once, not on every connect"), text(box));
  m.act(() => { field(box, "Also save to the drive's memory").click(); });
  m.act(() => { setVal(field(box, "Watchdog timeout (ms)"), 150); });
  check("a timeout of 150 ms is said in the panel",
        text(box).includes("The watchdog timeout must be between 100 and 10000 ms, "
          + "in steps of 100."), text(box));
  m.act(() => { setVal(field(box, "Watchdog timeout (ms)"), 500); });
  check("500 ms is fine: 5 to 105, every 0.25 s",
        !text(box).includes("must be between")
        && text(box).includes("writes 5 to register 105")
        && text(box).includes("every 0.25 s"), text(box));
  m.act(() => { setVal(field(box, "Save register"), 105); });
  check("the same register twice is said in the panel",
        text(box).includes("The watchdog register and the save register must be different."));
  m.act(() => { setVal(field(box, "Save register"), 100); });
  m.act(() => { sw(box).click(); });
  check("switching it off: 'Off', writes 0 to 105 (no save)",
        state(box) === "Off"
        && text(box).includes("writes 0 to register 105.")
        && !text(box).includes("register 100 (save)")
        && text(box).includes("One write of 1000 runs the motor until something writes 0"),
        text(box));
  m.act(() => { sw(box).click(); });
  const update = [...box.querySelectorAll("button")].find((b) => b.textContent === "Update");
  await m.act(async () => { update.click(); await new Promise((r) => setTimeout(r, 0)); });
  const sent = (globalThis.__posts.at(-1) || {}).body || {};
  check("Update sends the switch: on, 500 ms, 105, 100, no save",
        sent.watchdog === "on" && sent.watchdog_ms === 500
        && sent.wdt_address === 105 && sent.cfg_save_address === 100
        && sent.cfg_save === false,
        JSON.stringify(sent));
});

await editor(DRIVE, async (box) => {
  const off = box.querySelector("[data-watchdog-set-off]");
  check("not managed offers 'turn it off' in one click", !!off);
  m.act(() => { off.click(); });
  check("...which sets Off with the default registers",
        state(box) === "Off" && text(box).includes("writes 0 to register 105"), text(box));
  m.act(() => { box.querySelector("[data-watchdog-unmanage]").click(); });
  check("'Stop managing it' goes back to Not managed",
        state(box) === "Not managed", state(box));
  const update = [...box.querySelectorAll("button")].find((b) => b.textContent === "Update");
  await m.act(async () => { update.click(); await new Promise((r) => setTimeout(r, 0)); });
  const sent = (globalThis.__posts.at(-1) || {}).body || {};
  check("...and Update sends watchdog '' (the server clears the keys)",
        sent.watchdog === "", JSON.stringify(sent));
});

await editor({ ...DRIVE, watchdog: "on", watchdog_ms: 2000, wdt_address: 200,
               cfg_save_address: 201 }, async (box) => {
  check("a saved switch opens as saved: On, 2000 ms, registers 200 / 201",
        state(box) === "On" && field(box, "Watchdog timeout (ms)").value === "2000"
        && text(box).includes("writes 20 to register 200.")
        && text(box).includes("every 1 s"), text(box));
});

// -- 3. plain words ------------------------------------------------------------------
const ed = readFileSync(join(FRONT, "src", "components", "ModbusServerEditor.jsx"), "utf8");
const page = readFileSync(join(FRONT, "src", "pages", "Workflow.jsx"), "utf8");
const core = readFileSync(join(ROOT, "core", "services", "modbus_watchdog.py"), "utf8");
const noteSrc = page.split("function ModbusWatchdogNote")[1].split("function Field")[0];
check("no em dash in the editor, the panel note or the core",
      !ed.includes("\u2014") && !core.includes("\u2014") && !noteSrc.includes("\u2014"));
check("the server refuses the same timeout range the panel states",
      core.includes("WATCHDOG_MS_MIN = 100") && core.includes("WATCHDOG_MS_MAX = 10000"));

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
// The toasts Update raised hold timers open; nothing else is pending.
process.exit(0);
