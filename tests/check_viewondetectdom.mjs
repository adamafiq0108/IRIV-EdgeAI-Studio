// [video] view_on_detect, rendered: a Live Wall tile of a client-mode system
// with the switch on, in jsdom, fed overlay events through a fake hub stream
// (tests/check_viewondetect.mjs pins the pure rule; this proves the wiring):
//   1. nothing detected: the placeholder, NO <img> (no video pulled), and the
//      page's ONE hub overlay stream is open for the tile
//   2. a detection arrives: the <img> is mounted with its overlay canvas
//   3. detections stop: still shown during the hold, gone after it (2 s here)
//   4. a detection again: shown again; a server-mode system with the switch
//      on is not gated at all (its picture shows at once)
//
//   node tests/check_viewondetectdom.mjs
import { createRequire } from "node:module";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM, VirtualConsole } = req("jsdom");
const TMP = mkdtempSync(join(tmpdir(), "viewondetect-"));
const OUT = join(TMP, "bundle.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://10.0.0.5:5100/",
                        virtualConsole: new VirtualConsole() });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLSelectElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "requestAnimationFrame",
                 "cancelAnimationFrame", "getComputedStyle", "localStorage",
                 "MutationObserver"]) {
  global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;
global.sessionStorage = dom.window.sessionStorage;

// The hub overlay stream: a body whose reads we answer by hand.
const FETCHES = [];
const reads = [];
const enc = new TextEncoder();
global.fetch = (url) => {
  FETCHES.push(String(url));
  if (!String(url).includes("/api/hub/overlay")) return new Promise(() => {});
  return Promise.resolve({
    ok: true, status: 200,
    body: { getReader: () => ({ read: () => new Promise((r) => reads.push(r)) }) },
  });
};
const send = (env) => {
  const r = reads.shift();
  if (r) r({ value: enc.encode(`data: ${JSON.stringify(env)}\n\n`), done: false });
  return Boolean(r);
};

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const SAVING = { on_demand: false, view_on_detect: true, view_hold_s: 2 };
globalThis.__get = {
  "/api/hub/streams": {
    ok: true,
    systems: [
      { slug: "gated", name: "Gated", url: "http://10.0.0.5:5112", reachable: true,
        cameras: [], devices: [], overlay: { mode: "client", delay_ms: 0 },
        video_saving: SAVING },
      { slug: "srv", name: "Srv", url: "http://10.0.0.5:5113", reachable: true,
        cameras: [], devices: [], video_saving: SAVING },
    ],
  },
};
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /(api\/|^\.\/)client\.js$/ }, () => ({ path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        export const api = { get(url) { return Promise.resolve(globalThis.__get[url]); } };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shimApi],
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import LiveWallPanel from "${p("src/components/LiveWallPanel.jsx")}";
      export { act };
      export function mount(el) {
        const root = createRoot(el);
        act(() => { root.render(h(LiveWallPanel)); });
        return { unmount: () => act(() => root.unmount()) };
      }
    `,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent", loader: { ".css": "empty" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
rmSync(TMP, { recursive: true, force: true });
const { act } = m;

const flush = async () => {
  for (let i = 0; i < 6; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const wait = async (ms) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  await flush();
};
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await flush();
};
const choose = async (select, value) => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
  await act(async () => {
    setter.call(select, value);
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  await flush();
};
const systemSelects = (box) => [...box.querySelectorAll('select[aria-label="System"]')];
const button = (box, text) => [...box.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);
const overlayFetches = () => FETCHES.filter((u) => u.includes("/api/hub/overlay"));
const imgs = (box) => box.querySelectorAll("img").length;
const idle = (box) => box.querySelectorAll('[data-detect-gate="idle"]').length;
const DET = { sys: "gated", m: { v: 1, cam: "main", first: 1, w: 640, h: 480,
                                 dets: [{ box: [0.1, 0.1, 0.4, 0.6], label: "person",
                                          conf: 0.9, color: "#ffaa00" }] } };
const NONE = { sys: "gated", m: { v: 1, cam: "main", first: 1, w: 640, h: 480,
                                  hud: [["model", "m"]] } };

const box = document.createElement("div");
document.body.appendChild(box);
const view = m.mount(box);
await flush();

// -- 1. nothing detected -----------------------------------------------------------
await choose(systemSelects(box)[0], "gated");
await click(button(box, "Local AI feed"));
await flush();
check("nothing detected: the placeholder", idle(box) === 1
      && box.textContent.includes("No object detected -- video starts when one appears"));
check("...and no <img>: no video is pulled", imgs(box) === 0);
check("...while the page's ONE hub overlay stream is open for the tile",
      overlayFetches().length === 1
      && overlayFetches()[0] === "/api/hub/overlay?slugs=gated&camera=gated%3A",
      overlayFetches().join(" "));

// -- 2. a detection ------------------------------------------------------------------
check("the stream is being read", send(DET));
await flush();
check("a detection: the picture is mounted", imgs(box) === 1 && idle(box) === 0);
check("...with its overlay canvas", box.querySelectorAll('canvas[data-overlay="client"]').length === 1);
check("...and still ONE overlay stream (the canvas shares it)", overlayFetches().length === 1,
      overlayFetches().join(" "));

// -- 3. detections stop ------------------------------------------------------------
send(NONE);
await wait(1000);
check("detections stopped: still shown during the hold", imgs(box) === 1);
await wait(1300);
check("...and closed after the hold (2 s)", imgs(box) === 0 && idle(box) === 1);

// -- 4. again, and a server-mode system ------------------------------------------------
send(DET);
await flush();
check("a detection again: shown again", imgs(box) === 1);
await choose(systemSelects(box)[0], "srv");
await click(button(box, "Local AI feed"));
await flush();
check("a server-mode system with the switch on is not gated (picture at once)",
      imgs(box) === 2 && idle(box) === 0);
view.unmount();

console.log(`\n${bad ? `${bad} FAILED` : "ALL PASS"}`);
process.exit(bad ? 1 : 0);
