// The Update card's own controls (Settings > System, UpdatePanel.jsx).
//
// Tok Aba, 2026-10-05: "Check for updates automatically" is a switch, OFF on
// every box; with it off nothing in the background contacts GitHub, and that
// includes the card's own quiet re-check on opening. Pinned, by rendering
// UpdatePanel against a shimmed api:
//   1. a state with no auto_check draws the switch OFF and says what off means
//   2. off: opening the card on a stale cache sends no check
//   3. on: the same stale cache is re-checked quietly, as before
//   4. flipping the switch posts {enabled} to /api/hub/update/auto
//   5. "Update manually" sits beside Check now; choosing a file posts the
//      FILE itself (a raw body, not JSON) to /api/hub/update/manual, and the
//      checked file is shown with its version and an Install button; Install
//      posts {id, reinstall:false}; a same-version file says Reinstall and
//      sends reinstall:true; Cancel deletes the waiting file; a refusal is
//      shown in the box's own words
//
//   node tests/check_updatecard.mjs
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".updatecard-test.cjs");

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
          del(url) { globalThis.__posts.push(["DELETE " + url, null]); return Promise.resolve({ ok: true }); },
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
const STATE = { current: "1.0.15", latest: "v1.0.15", notes: "", checked_at: 1,
                update_available: false, error: "", token_set: true,
                previous: "1.0.14", busy: false };

async function render(update, extra = {}) {
  globalThis.__posts = [];
  globalThis.__get = (url) => {
    if (url.includes("/status")) return Promise.resolve(IDLE);
    if (url.includes("/api/hub/update/auto")) return Promise.resolve({ ok: true, auto_check: true });
    if (url.includes("/api/hub/update/check")) return Promise.resolve({ update });
    return Promise.resolve({ update, ota_ready: true, ...extra });
  };
  const el = document.createElement("div");
  document.body.appendChild(el);
  const r = m.mount(el, h(m.UpdatePanel, { embedded: true }));
  await flush();
  return { el, done: () => { r.unmount(); el.remove(); } };
}
const posted = (path) => globalThis.__posts.filter(([u]) => u.startsWith(path));

// -- 1 + 2. default off ---------------------------------------------------------
{
  const { el, done } = await render({ ...STATE });
  const sw = el.querySelector('[role="switch"]');
  check("1 the card has the auto-check switch", !!sw);
  check("1 ...labelled in plain words",
        el.textContent.includes("Check for updates automatically"), el.textContent);
  check("1 ...OFF when the state carries no auto_check",
        sw && sw.getAttribute("aria-checked") === "false", sw && sw.outerHTML);
  check("1 ...and says what off means",
        el.textContent.includes("Off: the box checks only when you press Check now."));
  check("2 off: opening on a stale cache sends no check",
        posted("/api/hub/update/check").length === 0, JSON.stringify(globalThis.__posts));
  // -- 4. flipping it on -------------------------------------------------------
  await act(async () => { sw.click(); });
  await flush();
  const p = posted("/api/hub/update/auto");
  check("4 flipping the switch posts enabled: true",
        p.length === 1 && p[0][1] && p[0][1].enabled === true, JSON.stringify(p));
  done();
}

// -- 3. on: the quiet re-check stays ---------------------------------------------
{
  const { el, done } = await render({ ...STATE, auto_check: true });
  const sw = el.querySelector('[role="switch"]');
  check("3 on: the switch reads on", sw && sw.getAttribute("aria-checked") === "true");
  check("3 on: a stale cache is re-checked quietly on opening",
        posted("/api/hub/update/check").length === 1, JSON.stringify(globalThis.__posts));
  done();
}

// -- 5. Update manually -----------------------------------------------------------
async function chooseFile(el, name = "irivai-update-v1.0.16.tar.gz") {
  const input = el.querySelector('[data-testid="ota-file"]');
  const file = new window.File([new Uint8Array([31, 139, 8])], name, { type: "application/gzip" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => { input.dispatchEvent(new window.Event("change", { bubbles: true })); });
  await flush();
  return file;
}
const button = (el, re) => [...el.querySelectorAll("button")].find((b) => re.test(b.textContent));
{
  const waiting = { id: "abc123", version: "1.0.16", notes: "Update:\n- {FEATURES UPDATE} a thing",
                    same_version: false, channel: "stable" };
  const { el, done } = await render({ ...STATE });
  const btn = button(el, /^Update manually$/);
  const check_ = button(el, /Check now/);
  check("5 the card has an Update manually button", !!btn);
  check("5 ...beside Check now", btn && check_ && btn.parentElement === check_.parentElement);
  check("5 ...enabled on an idle box", btn && !btn.disabled);
  globalThis.__get = (url) => {
    if (url.includes("/status")) return Promise.resolve(IDLE);
    if (url === "/api/hub/update/manual") return Promise.resolve({ ok: true, offline: waiting });
    if (url.includes("/manual/install")) return Promise.resolve({ status: { phase: "starting", running: true } });
    return Promise.resolve({ update: STATE, ota_ready: true });
  };
  const file = await chooseFile(el);
  const up = posted("/api/hub/update/manual");
  check("5 choosing a file posts the file itself",
        up.length === 1 && up[0][1] === file, JSON.stringify(up.map(([u]) => u)));
  const card = el.querySelector('[data-testid="ota-waiting"]');
  check("5 the checked file is shown with its version",
        card && card.textContent.includes("Version 1.0.16 from a file"), card && card.textContent);
  check("5 ...and its signed notes", card && card.textContent.includes("a thing"));
  const inst = button(el, /^Install$/);
  check("5 ...and an Install button", !!inst);
  done();
}
{
  // Install through the confirm, with the dialog shimmed to its confirm.
  const { el, done } = await render({ ...STATE });
  const waiting = { id: "same1", version: "1.0.15", notes: "", same_version: true };
  globalThis.__get = (url) => {
    if (url.includes("/status")) return Promise.resolve(IDLE);
    if (url === "/api/hub/update/manual") return Promise.resolve({ ok: true, offline: waiting });
    return Promise.resolve({ status: { phase: "starting", running: true } });
  };
  await chooseFile(el, "x.tar.gz");
  const re = button(el, /^Reinstall$/);
  check("5 a same-version file says Reinstall", !!re);
  check("5 ...and is marked as installed now", el.textContent.includes("installed now"));
  await act(async () => { re.click(); });
  await flush();
  const dlg = [...document.querySelectorAll("button")].find((b) => b.textContent === "Reinstall" && b !== re);
  check("5 Reinstall asks for a confirm first", !!dlg);
  check("5 ...nothing installed before it", posted("/api/hub/update/manual/install").length === 0);
  if (dlg) { await act(async () => { dlg.click(); }); await flush(); }
  const ins = posted("/api/hub/update/manual/install");
  check("5 the confirm installs with reinstall: true",
        ins.length === 1 && ins[0][1].id === "same1" && ins[0][1].reinstall === true,
        JSON.stringify(ins));
  done();
}
{
  const { el, done } = await render({ ...STATE });
  globalThis.__get = (url) => {
    if (url.includes("/status")) return Promise.resolve(IDLE);
    if (url === "/api/hub/update/manual") return Promise.resolve({ ok: true, offline: { id: "c1", version: "1.0.16", notes: "" } });
    return Promise.resolve({ update: STATE });
  };
  await chooseFile(el);
  await act(async () => { button(el, /^Cancel$/).click(); });
  await flush();
  check("5 Cancel deletes the waiting file",
        globalThis.__posts.some(([u]) => u === "DELETE /api/hub/update/manual"));
  check("5 ...and the card goes", !el.querySelector('[data-testid="ota-waiting"]'));
  done();
}
{
  const { el, done } = await render({ ...STATE });
  globalThis.__get = (url) => {
    if (url.includes("/status")) return Promise.resolve(IDLE);
    if (url === "/api/hub/update/manual") {
      return Promise.reject(Object.assign(new Error("The software update could not be installed."),
        { status: 400, data: { details: "This file is not signed by IRIV, so it was not accepted." } }));
    }
    return Promise.resolve({ update: STATE });
  };
  await chooseFile(el);
  check("5 a refused file shows no Install", !el.querySelector('[data-testid="ota-waiting"]'));
  done();
}

const src = readFileSync(join(FRONT, "src", "components", "UpdatePanel.jsx"), "utf8");
check("5 a refusal is shown in the box's own words (details first)",
      src.includes('toast.error("This file cannot be used", e.data?.details || e.message)'));
check("no em dash in the card", !src.includes("\u2014"));

console.log(bad ? "FAILED" : "PASS");
process.exit(bad ? 1 : 0);
