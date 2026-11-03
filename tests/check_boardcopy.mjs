// Settings > Board I/O: the customer copy pass (2026-10-11).
//
// Wording only; ownership, release refusal and the Node-RED switch are the
// server's and unchanged. Pinned here: the spec heading and columns, the
// friendly kind under each id with the BCM pin kept, "Not currently in use",
// the system NAME (not its slug) on a used connection, the Front Panel
// Controls note, the Node-RED Integration card with its status label and the
// sentence that is true of the factory flow, the release warning that says a
// stopped system's rule blocks too, and a refused release told in words built
// from the page's own list rather than the server's raw sentence.
//
//   node tests/check_boardcopy.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".boardcopy-test.cjs");

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
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// GET answers the board the test sets; POST to /release refuses with a 400
// whose message is the server's slug sentence, which must NOT be shown.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          get() { return Promise.resolve(JSON.parse(JSON.stringify(globalThis.__board))); },
          post(url, body) {
            globalThis.__posts.push([url, body]);
            const e = new Error("DO0 is in use by line_one (Relay). raw server sentence");
            e.status = 400;
            return Promise.reject(e);
          },
        };
        export function friendlyError(e) { return "friendly:" + (e && e.status); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import BoardPanel from "${p("src/components/BoardPanel.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { act, h, BoardPanel, ToastProvider };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  alias: { "@": join(FRONT, "src") },
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;
const settle = () => m.act(async () => { await new Promise((r) => setTimeout(r, 20)); });

async function render(board, fn) {
  globalThis.__board = board;
  globalThis.__posts = [];
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null, h(m.BoardPanel)));
  await settle();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

const port = (id, kind, pin, extra = {}) => ({
  id, kind, pin, label: id, released: false, users: [], busy: false,
  consumer: "", ...extra });
const BOARD = {
  ok: true, detected: true, model: "IRIV",
  panel: { running: true, holds: ["BUTTON", "BUZZER"] },
  nodered: { installed: true, active: false, enabled: false },
  ports: [
    port("DI0", "input", 22),
    port("DO0", "output", 23, { users: [
      { slug: "line_one", name: "Line One", label: "Relay", running: true }] }),
    port("DO1", "output", 24, { released: true }),
    port("L0", "pwm", 12, { busy: true, consumer: "lg" }),
    port("BUZZER", "output", 19),
    port("BUTTON", "input", 4),
  ],
};
const click = (el) => m.act(async () => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
});
const rowOf = (box, id) => [...box.querySelectorAll("tbody tr")]
  .find((tr) => tr.querySelector("td div")?.textContent === id);

await render(BOARD, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("heading is Hardware Inputs & Outputs with the spec subtitle",
        box.querySelector("h1")?.textContent === "Hardware Inputs & Outputs"
        && t.includes("Manage which hardware connections are controlled by this device")
        && t.includes("When a connection is assigned to another application, this device will no longer control or read it."),
        t.slice(0, 300));
  const heads = [...box.querySelectorAll("thead th")].map((th) => th.textContent);
  check("columns are Connection / Hardware Pin / Currently Used By / Device Control",
        JSON.stringify(heads) === JSON.stringify(
          ["Connection", "Hardware Pin", "Currently Used By", "Device Control"]), heads);
  check("ids and BCM pins stay, with the friendly kind underneath",
        rowOf(box, "DI0")?.textContent.includes("Digital input")
        && rowOf(box, "DI0")?.textContent.includes("BCM 22")
        && rowOf(box, "L0")?.textContent.includes("PWM / LED")
        && rowOf(box, "BUZZER")?.textContent.includes("Digital output")
        && rowOf(box, "BUTTON")?.textContent.includes("BCM 4"));
  check("a free connection reads Not currently in use",
        rowOf(box, "DI0")?.textContent.includes("Not currently in use"));
  check("a used connection shows the system NAME, not its slug",
        rowOf(box, "DO0")?.textContent.includes("Line One")
        && !rowOf(box, "DO0")?.textContent.includes("line_one"));
  check("a line another program holds says so, the raw consumer only in the tooltip",
        rowOf(box, "L0")?.textContent.includes("Another application")
        && !rowOf(box, "L0")?.textContent.includes("lg"));
  check("a released connection says it is released",
        rowOf(box, "DO1")?.textContent.includes("Released to other applications"));
  check("the switch is named Device controls this connection",
        !!box.querySelector('[aria-label="DI0: Device controls this connection"]'));
  const note = box.querySelector("[data-board-panel-note]")?.textContent.replace(/\s+/g, " ") || "";
  check("Front Panel Controls names Button and Buzzer and where to disable it",
        note.includes("Front Panel Controls")
        && note.includes("The front panel currently uses the physical Button and Buzzer connections.")
        && note.includes("disable front-panel control under Settings → Front Panel"), note);
  check("Node-RED Integration shows Stopped and the true stopped sentence",
        t.includes("Node-RED Integration")
        && box.querySelector("[data-nodered-status]")?.textContent === "Stopped"
        && t.includes("Node-RED is stopped. Hardware connections remain available to this device."));
  check("the release warning covers a stopped system's rule",
        box.querySelector("[data-board-release-warning]")?.textContent.replace(/\s+/g, " ")
          .includes("including a rule in a system that is stopped"));
  check("the release note says the workflow block list, not a Node-RED list",
        t.includes("Before releasing a connection")
        && t.includes("no longer offered in the workflow block list"));
  check("no old platform wording is left",
        !/Owned by this platform|Terminal\b|taken back|Nothing\b/.test(t), t);

  await click(box.querySelector('[aria-label="DO0: Device controls this connection"]'));
  await settle();
  const body = document.body.textContent;
  check("a refused release names the system and rule in plain words",
        body.includes("This connection is currently in use by Line One (Relay).")
        && body.includes("Disable or remove the rule using it before releasing the connection."),
        body.slice(-400));
  check("...and never prints the server's raw sentence",
        !body.includes("raw server sentence"));
});

await render({ ...BOARD, panel: { running: false, holds: [] },
               nodered: { installed: true, active: true, enabled: true } }, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("Node-RED running: status Running and the switches are all disabled",
        box.querySelector("[data-nodered-status]")?.textContent === "Running"
        && [...box.querySelectorAll("tbody [role=switch]")].every((s) => s.disabled));
  check("...the page says this device cannot control the connections",
        t.includes("This device cannot control or read hardware connections until it is stopped."));
  check("...and no Front Panel note while it is off",
        !box.querySelector("[data-board-panel-note]"));
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall board copy checks passed");
dom.window.close();
process.exit(0);
