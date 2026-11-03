// The System tab's version line is never a bare "v".
//
// On a box still on the shipped password the account-setup gate refused
// GET /api/hub/update (409), UpdatePanel folded that refusal into its state
// ({error: "finish account setup first"}, no `current`) and the card read
// "This box is running v" over "Last checked never". The version was never
// missing on the server (core.version.VERSION, frozen at import): the panel
// simply had no second place to read it from. Pinned, by rendering
// UpdatePanel against a shimmed api:
//   1. the update state's own version is printed as v<current>
//   2. when the state cannot be read, the shell's bootstrap version is
//      printed instead (both bootstraps carry `version`, and /api/bootstrap
//      is one of the calls the setup gate always answers)
//   3. with neither, the line says "unknown" -- never "v" on its own
//   4. hub.py and spa.py both put VERSION in their bootstrap
//
//   node tests/check_updateversion.mjs
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".updateversion-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Image", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
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

// The api: GET answers globalThis.__get(url) -- a value, or a thrown
// ApiError-shaped failure the way the real client throws one.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        export class ApiError extends Error {
          constructor(message, status, data) { super(message); this.status = status; this.data = data; }
        }
        globalThis.__posts = [];
        export const api = {
          get(url) { return globalThis.__get(url); },
          post(url, body) { globalThis.__posts.push([url, body]); return globalThis.__get(url); },
        };
        export function isSetupRequired(e) {
          return !!(e && e.status === 409 && e.data && e.data.needs_account_setup);
        }
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const shimAsk = {
  name: "shim-ask",
  setup(b) {
    b.onResolve({ filter: /ui\/ask\.jsx$/ }, () => (
      { path: "ask-shim", namespace: "shim" }));
    b.onLoad({ filter: /^ask-shim$/, namespace: "shim" }, () => ({
      contents: `
        export function useConfirm() { return async () => false; }
        export function useAsk() { return async () => null; }
        export function AskProvider({ children }) { return children; }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import UpdatePanel from "${p("src/components/UpdatePanel.jsx")}";
import { AppContext } from "${p("src/state/app.jsx")}";
import { ApiError } from "${p("src/api/client.js")}";
export { act, h, UpdatePanel, AppContext, ApiError };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi, shimAsk],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  loader: { ".css": "empty" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h, act } = m;

const flush = async () => {
  for (let i = 0; i < 6; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const IDLE = { status: { phase: "idle", running: false } };
const STATE = { current: "1.0.14", latest: "", notes: "", checked_at: 0,
                update_available: false, error: "", token_set: true,
                previous: "", busy: false };
const gateRefusal = () => {
  const e = new m.ApiError("finish account setup first", 409,
                           { ok: false, error: "finish account setup first",
                             needs_account_setup: true });
  return Promise.reject(e);
};

/** Render the panel; `boot` is what the shell's bootstrap said (or null for
 *  a panel drawn outside any shell). Returns the card's version line. */
async function versionLine(get, boot) {
  globalThis.__get = get;
  const el = document.createElement("div");
  document.body.appendChild(el);
  const panel = h(m.UpdatePanel, { embedded: true });
  const node = boot
    ? h(m.AppContext.Provider, { value: { boot } }, panel)
    : panel;
  const r = m.mount(el, node);
  await flush();
  const line = [...el.querySelectorAll("div")]
    .find((d) => d.textContent === "This box is running")
    ?.parentElement?.querySelector(".text-2xl")?.textContent?.trim();
  r.unmount();
  el.remove();
  return line;
}

// -- 1. the update state names the version ------------------------------------
let line = await versionLine(
  (url) => Promise.resolve(url.includes("/status") ? IDLE : { update: STATE }), null);
check("1 the update state's version is printed", line === "v1.0.14", line);

// -- 2. the state cannot be read: the shell's version stands in ----------------
line = await versionLine(
  (url) => (url.includes("/status") ? Promise.resolve(IDLE) : gateRefusal()),
  { version: "1.0.14", needs_account_setup: true });
check("2 a refused update state falls back to the bootstrap's version",
      line === "v1.0.14", line);

// -- 3. nothing to read anywhere: said plainly, never a bare "v" ---------------
line = await versionLine(
  (url) => (url.includes("/status") ? Promise.resolve(IDLE) : gateRefusal()), null);
check("3 with no version at all the line reads \"unknown\"", line === "unknown", line);
check("3 ...and never a bare \"v\"", line !== "v", line);

// -- 5. the gate's refusal is said in the panel's words, with a way out -------
{
  globalThis.__get = (url) => (url.includes("/status") ? Promise.resolve(IDLE)
                                                         : gateRefusal());
  const el = document.createElement("div");
  document.body.appendChild(el);
  const r = m.mount(el, h(m.UpdatePanel, { embedded: true }));
  await flush();
  const text = el.textContent;
  check("5 the setup gate reads \"Finish account setup first\"",
        text.includes("Finish account setup first"), text);
  check("5 ...not the server's lowercase sentence",
        !text.includes("finish account setup first"), text);
  check("5 ...with a button to the setup screen",
        [...el.querySelectorAll("button")].some((b) => /account setup/i.test(b.textContent)));
  check("5 ...and no token box, since saving one is the refused call",
        !el.querySelector("#ota-token"));
  r.unmount();
  el.remove();
}

// -- 4. both bootstraps carry the version ---------------------------------------
for (const f of ["core/routes/hub.py", "core/routes/spa.py"]) {
  const src = readFileSync(join(ROOT, f), "utf8");
  const boot = src.split("def bootstrap")[1].split("\n@router")[0];
  check(`4 ${f} bootstrap carries version`, boot.includes('"version": VERSION,'));
}

console.log(bad ? "FAILED" : "PASS");
process.exit(bad ? 1 : 0);
