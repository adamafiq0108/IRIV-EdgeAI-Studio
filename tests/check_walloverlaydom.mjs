// The Live Wall, rendered: server-mode tiles open nothing new, client-mode
// tiles share ONE hub overlay stream (tests/check_walloverlay.mjs pins the
// pure logic; this renders LiveWallPanel in jsdom against a shimmed api and
// a recording fetch):
//   1. a server-mode system's local feed: no canvas, no overlay request
//   2. a client-mode system's local feed: one canvas over the picture and ONE
//      GET /api/hub/overlay naming that system and camera
//   3. a second client tile changes the one stream's query, never adds a
//      second stream; clearing every client tile ends it
//
//   node tests/check_walloverlaydom.mjs
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
// A self-contained bundle, so it can live outside node_modules (which may be
// a junction to another checkout's).
const TMP = mkdtempSync(join(tmpdir(), "walloverlay-"));
const OUT = join(TMP, "bundle.cjs");

// A silent console: jsdom has no canvas, and says so on every getContext.
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

const FETCHES = [];
const ABORTED = [];
global.fetch = (url, opts) => {
  FETCHES.push(String(url));
  opts?.signal?.addEventListener("abort", () => ABORTED.push(String(url)));
  return new Promise(() => {});                 // an open stream, never ends
};

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

globalThis.__get = {
  "/api/hub/streams": {
    ok: true,
    systems: [
      { slug: "plain", name: "Plain", url: "http://10.0.0.5:5111", reachable: true,
        cameras: [], devices: [] },
      { slug: "boxes", name: "Boxes", url: "http://10.0.0.5:5112", reachable: true,
        cameras: [], devices: [], overlay: { mode: "client", delay_ms: 40 } },
    ],
  },
};
const shimApi = {
  name: "shim-api",
  setup(b) {
    // api/hooks.js imports it as "./client.js".
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
const canvases = (box) => box.querySelectorAll('canvas[data-overlay="client"]').length;

async function fillLocal(box, slug) {
  await choose(systemSelects(box)[0], slug);
  await click(button(box, "Local AI feed"));
}

const box = document.createElement("div");
document.body.appendChild(box);
const view = m.mount(box);
await flush();

// -- 1. server mode ----------------------------------------------------------------
await fillLocal(box, "plain");
check("a server-mode tile shows its picture", box.querySelectorAll("img").length === 1);
check("...with no canvas", canvases(box) === 0);
check("...and no overlay request at all", overlayFetches().length === 0, FETCHES.join(" "));

// -- 2. client mode ---------------------------------------------------------------
await fillLocal(box, "boxes");
await flush();
check("a client-mode tile gets one canvas over its picture", canvases(box) === 1);
const wrap = box.querySelector('canvas[data-overlay="client"]')?.parentElement;
check("...inside the picture's positioned wrapper",
      wrap?.style.position === "relative" && Boolean(wrap.querySelector("img")));
check("...and the page opens ONE hub overlay stream for it",
      overlayFetches().length === 1
      && overlayFetches()[0] === "/api/hub/overlay?slugs=boxes&camera=boxes%3A",
      overlayFetches().join(" "));
check("the server-mode tile beside it still has no canvas",
      box.querySelectorAll("img").length === 2 && canvases(box) === 1);

// -- 3. one stream, whatever the wall holds ----------------------------------------
await fillLocal(box, "boxes");
await flush();
check("a second tile of the same feed adds a canvas but no second stream",
      canvases(box) === 2 && overlayFetches().length === 1, overlayFetches().join(" "));
const closers = [...box.querySelectorAll('button[aria-label="Close this feed"]')];
for (const c of closers) await click(c);         // eslint-disable-line no-await-in-loop
await flush();
check("clearing every tile ends the stream",
      canvases(box) === 0 && ABORTED.filter((u) => u.includes("/api/hub/overlay")).length === 1,
      ABORTED.join(" "));
view.unmount();

console.log(`\n${bad ? `${bad} FAILED` : "ALL PASS"}`);
process.exit(bad ? 1 : 0);
