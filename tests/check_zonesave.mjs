// What the Zones page SENDS when a line's Crossing threshold is typed in.
//
// Reported from the board on 2026-09-24: "after entering the threshold and
// save, it didnt save it". The server round-trip was proved good by hand
// (POST band_pct 2.5 -> config.local.toml band_pct = 2.5), so whatever loses
// the number is on this page, between the key press and the request body.
//
// A number input is the trap. Per the HTML spec an <input type="number">
// whose text is not a VALID floating point number reports value "" -- and
// "2." is not valid, it may not end in a point. So the instant a decimal
// point is typed the onChange handler sees "", writes null, and React paints
// the box empty, taking the "2" with it. Typing 2.5 therefore leaves either
// nothing or 25 depending on how fast the keys land, and the row the operator
// then saves carries no threshold at all. Nothing in the page is broken in a
// way a reader would see; the field just quietly refuses decimals, which is
// the only kind of value this field is for.
//
//   node tests/check_zonesave.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".zonesave-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent", "FocusEvent",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage", "matchMedia"]) {
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

// The server, under the test's control: every request is recorded, and the
// zone list it answers with is the one a real board answers with (the
// validator drops the factory value, so an untouched line comes back with no
// band_pct at all).
const shim = {
  name: "shim",
  setup(b) {
    const stub = (filter, contents) => {
      b.onResolve({ filter }, (args) => (
        { path: `stub:${args.path}`, namespace: "stub" }));
      b.onLoad({ filter: new RegExp(`^stub:`), namespace: "stub" }, (args) => (
        args.path.match(filter)
          ? { contents, resolveDir: FRONT, loader: "jsx" } : null));
    };
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__calls = [];
        export const api = {
          get(url) {
            globalThis.__calls.push({ method: "GET", url });
            return Promise.resolve(globalThis.__status());
          },
          post(url, body) {
            globalThis.__calls.push({ method: "POST", url, body });
            return Promise.resolve(globalThis.__answer(url, body));
          },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
        export function saveBlob() {}
        export function streamUrl() { return ""; }
      `,
      resolveDir: FRONT, loader: "js",
    }));
    // The feed, the drawing canvas and the router are not what this file is
    // about; each would drag a video element or a <Routes> into jsdom.
    b.onResolve({ filter: /(WebRTCVideo|CameraLoader|OverlayPanel|Loading)\.jsx$/ },
                () => ({ path: "view-shim", namespace: "shim" }));
    b.onLoad({ filter: /^view-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement as h } from "react";
        const Nothing = () => h("div");
        export default Nothing;
        export const ErrorNote = Nothing;
        export const SkelEditor = Nothing;
        export const CameraLoader = Nothing;
      `,
      resolveDir: FRONT, loader: "js",
    }));
    b.onResolve({ filter: /ZoneCanvas\.jsx$/ }, () => (
      { path: "canvas-shim", namespace: "shim" }));
    b.onLoad({ filter: /^canvas-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement as h } from "react";
        export default () => h("div");
        export const ZONE_PALETTE = ["#e11d48", "#0ea5e9"];
        export const zoneColor = (z) => (z && z.color) || "#e11d48";
        export const buildPoints = () => [];
      `,
      resolveDir: FRONT, loader: "js",
    }));
    b.onResolve({ filter: /^react-router-dom$/ }, () => (
      { path: "router-shim", namespace: "shim" }));
    b.onLoad({ filter: /^router-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement as h } from "react";
        export const Link = ({ children }) => h("a", null, children);
        export const useNavigate = () => () => {};
        export const useLocation = () => ({ pathname: "/zones" });
      `,
      resolveDir: FRONT, loader: "js",
    }));
    // The poll is driven by hand here: a 4 second interval in a test is a
    // test that waits, and what matters is only WHETHER a poll lands between
    // the typing and the save.
    b.onResolve({ filter: /api\/hooks\.js$/ }, () => (
      { path: "hooks-shim", namespace: "shim" }));
    b.onLoad({ filter: /^hooks-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__polls = [];
        export function useVisibleInterval(fn) {
          globalThis.__polls[0] = fn;
          return { stop() {}, start() {} };
        }
        export function usePoll(fn) { globalThis.__polls[1] = fn; }
      `,
      resolveDir: FRONT, loader: "js",
    }));
    b.onResolve({ filter: /ui\/ask\.jsx$/ }, () => (
      { path: "ask-shim", namespace: "shim" }));
    b.onLoad({ filter: /^ask-shim$/, namespace: "shim" }, () => ({
      contents: `export const useConfirm = () => () => Promise.resolve(true);
                 export default useConfirm;`,
      resolveDir: FRONT, loader: "js",
    }));
    b.onResolve({ filter: /use-unsaved-guard\.jsx$/ }, () => (
      { path: "guard-shim", namespace: "shim" }));
    b.onLoad({ filter: /^guard-shim$/, namespace: "shim" }, () => ({
      contents: `export const useUnsavedGuard = () => null;
                 export default useUnsavedGuard;`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import ZonesPanel from "${p("src/pages/Zones.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";

export function mount(el) {
  const root = createRoot(el);
  act(() => { root.render(h(ToastProvider, null, h(ZonesPanel, null))); });
  return { unmount: () => act(() => root.unmount()) };
}
export { act };
`;

await build({
  plugins: [shim],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "jsx" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});

const mod = req(OUT);
const { act } = mod;

// A line as a real board answers with it: no band_pct, because the validator
// never writes the factory value.
const LINE = {
  id: "zone_mtuxbskl", name: "Line 2", kind: "line", enabled: true,
  points: [[0.63, 0.94], [0.63, 0.08]], color: "#e11d48", labels: [],
  camera: "cam1", anchor: "bottom_center",
};
const STATUS = () => ({
  ok: true, running: true, editable: true, caps: ["zones.edit", "zones.add"],
  cameras: [{ id: "cam1", name: "Camera 1", enabled: true }],
  camera: "cam1",
  zones: [JSON.parse(JSON.stringify(LINE))],
});
globalThis.__status = STATUS;
// The board echoes what it stored: the validator clamps to one decimal and
// drops the factory 1.2, which is exactly what makes "it did not save" and
// "you typed the default" look identical on the page.
globalThis.__answer = (url, body) => {
  const zones = (body.zones || []).map((z) => {
    const out = { ...z };
    const pct = Number(out.band_pct);
    if (!(pct > 0) || Math.abs(pct - 1.2) < 1e-9) delete out.band_pct;
    else out.band_pct = Math.round(Math.min(5, Math.max(0.2, pct)) * 10) / 10;
    return out;
  });
  return { ok: true, zones };
};

const host = document.body;
const box = document.createElement("div");
host.appendChild(box);
const view = mod.mount(box);
await act(async () => {});             // let the first GET resolve

const setter = Object.getOwnPropertyDescriptor(
  dom.window.HTMLInputElement.prototype, "value").set;
/** What the BROWSER reports, not what was pressed: a number input whose text
 *  is not a valid floating point number reports "" (HTML spec, value
 *  sanitization). "2." is not valid. */
const asNumberInput = (text) => (/^-?\d*\.?\d+$/.test(text) ? text : "");
const type = (input, text) => act(async () => {
  setter.call(input, asNumberInput(text));
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
});
const blur = (input) => act(async () => {
  input.dispatchEvent(new dom.window.FocusEvent("blur", { bubbles: false }));
});
const click = (el) => act(async () => {
  el.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
});
const threshold = () => [...box.querySelectorAll("input[type=number]")]
  .find((i) => i.getAttribute("placeholder") === "1.2");
const saveButton = () => [...box.querySelectorAll("button")]
  .find((b) => /^Save$/.test(b.textContent.trim()) && !b.disabled);
const lastPost = () => [...globalThis.__calls].reverse()
  .find((c) => c.method === "POST" && c.url.endsWith("/zones"));

// ===========================================================================
// 1. the field is there, and it starts empty on a line that has no override
// ===========================================================================
// A zone card is collapsed until it is picked, the way the page opens: one
// compact row per zone, controls only for the chosen one.
const cardRow = () => [...box.querySelectorAll("div")]
  .filter((d) => d.textContent.trim().startsWith("Line 2")
                 && !d.closest(".toast-stack"))
  .pop();
await click(cardRow());

const input = threshold();
check("the line card offers a Crossing threshold box", !!input,
      "no number input with placeholder 1.2 was rendered");
check("...reading the factory 1.2 on a line that has never had one set",
      input && input.value === "1.2",
      input ? `the box reads ${JSON.stringify(input.value)}` : "");

// ===========================================================================
// 2. A WHOLE NUMBER survives typing and reaches the request
// ===========================================================================
await type(input, "3");
await blur(threshold());
const save = saveButton();
check("typing a value lights up that zone's Save", !!save,
      "the Save button stayed disabled, so the page did not see an edit");
if (save) await click(save);
const post3 = lastPost();
check("a whole number reaches the board",
      post3 && post3.body.zones[0].band_pct === 3,
      post3 ? JSON.stringify(post3.body.zones[0].band_pct) : "no POST was made");

// ===========================================================================
// 3. A DECIMAL survives typing -- the reported fault
// ===========================================================================
// Typed one key at a time, which is how a person types 2.5 and how every
// browser reports it: "2", then "2." (invalid -> ""), then "2.5".
globalThis.__status = () => {
  const s = STATUS();
  s.zones[0].band_pct = 3;             // what case 2 just saved
  return s;
};
await act(async () => { globalThis.__polls[0] && globalThis.__polls[0](); });
// A save collapses the card it saved, so the operator opens it again to type
// the next value -- and that is where the saved value has to be showing.
await click(cardRow());
check("a saved threshold is what the reopened card shows",
      threshold() && threshold().value === "3",
      threshold() ? `the box reads ${JSON.stringify(threshold().value)}` : "no box");
const dec = threshold();
await type(dec, "2");
await type(dec, "2.");
await type(dec, "2.5");
await blur(threshold());
const saveDec = saveButton();
if (saveDec) await click(saveDec);
const postDec = lastPost();
check("a decimal threshold reaches the board as typed",
      postDec && postDec.body.zones[0].band_pct === 2.5,
      postDec ? `sent ${JSON.stringify(postDec.body.zones[0].band_pct)} for a typed 2.5`
              : "no POST was made");

// ===========================================================================
// 4. THE REPORTED FAULT: saving the factory value must not look like a
//    failed save
// ===========================================================================
// The board stores nothing when the threshold is the factory 1.2 (every
// config written before the field existed must round-trip byte-identical).
// An empty box over a "1.2" placeholder was the honest reading of that
// document and a useless one on the page: type 1.2, press Save, watch the
// field go blank, and there is no way to tell that from a save that failed.
// Reported from a live board as "after entering the threshold and save, it
// didnt save it".
globalThis.__status = () => {
  const s = STATUS();
  s.zones[0].band_pct = 2.5;           // what case 3 just saved
  return s;
};
await act(async () => { globalThis.__polls[0] && globalThis.__polls[0](); });
await click(cardRow());
check("a saved decimal is what the reopened card shows",
      threshold() && threshold().value === "2.5",
      threshold() ? `the box reads ${JSON.stringify(threshold().value)}` : "no box");

const factory = threshold();
await type(factory, "1.2");
await blur(threshold());
const saveFac = saveButton();
check("typing the factory value still offers a save", !!saveFac);
if (saveFac) await click(saveFac);
const postFac = lastPost();
check("the factory value is sent, and the board drops it as it always did",
      postFac && postFac.body.zones[0].band_pct === 1.2
      && postFac.body.zones[0].band_pct !== undefined,
      postFac ? JSON.stringify(postFac.body.zones[0].band_pct) : "no POST was made");
// The board answered with no band_pct at all. The page must still show the
// number the line decides with.
globalThis.__status = STATUS;
await act(async () => { globalThis.__polls[0] && globalThis.__polls[0](); });
await click(cardRow());
check("...and the box shows 1.2 afterwards rather than going blank",
      threshold() && threshold().value === "1.2",
      threshold() ? `the box reads ${JSON.stringify(threshold().value)}` : "no box");

// ===========================================================================
// 5. a line that has never been touched reads as the factory value too
// ===========================================================================
check("a line with no override reads 1.2, not an empty box",
      threshold() && threshold().value === "1.2",
      threshold() ? `the box reads ${JSON.stringify(threshold().value)}` : "no box");

// ===========================================================================
// 6. an untouched line is never made dirty by the threshold box
// ===========================================================================
// Tabbing THROUGH the field must not mark the zone unsaved: a page that goes
// dirty on its own trains an operator to ignore the dot, and the unsaved
// guard then fires on every navigation. The value now SHOWN is 1.2 while the
// value STORED is nothing, which is exactly the pair that could go dirty by
// itself if the box wrote what it displays.
await blur(threshold());
check("blurring an untouched box does not mark the zone unsaved",
      !saveButton(), "Save lit up although nothing was typed");

// ===========================================================================
// 7. the box can still be cleared and retyped
// ===========================================================================
// Backspacing the last digit must leave an EMPTY box, not redraw 1.2 under
// the cursor for the next keystroke to append to (4 after clearing 1.2 would
// otherwise read 1.24, then clamp to 1.2 on blur, and the operator's 4 would
// have vanished twice over).
const retype = threshold();
await type(retype, "");
check("a cleared box stays cleared while it is being edited",
      threshold() && threshold().value === "",
      threshold() ? `the box reads ${JSON.stringify(threshold().value)}` : "no box");
await type(threshold(), "4");
await blur(threshold());
check("...and the next keystroke is the whole value, not appended to 1.2",
      threshold() && threshold().value === "4",
      threshold() ? `the box reads ${JSON.stringify(threshold().value)}` : "no box");

// ===========================================================================
// 8. TYPING INTO THE FILLED BOX REPLACES IT
// ===========================================================================
// Showing the value in force means a click lands a CURSOR in "1.2" rather
// than opening an empty box, so the next digit is APPENDED: 3 typed to mean
// 3 becomes 1.23, and one decimal place rounds that straight back to 1.2.
// Every single digit does this -- 1.2 followed by anything is 1.2X -- so the
// setting reads as stuck at the factory value however many times it is
// retyped. Reported from the board as "even though i change to another
// value, it always be 1.2".
let selected = false;
const focus = (el) => act(async () => {
  el.select = () => { selected = true; };
  el.dispatchEvent(new dom.window.FocusEvent("focusin", { bubbles: true }));
});
await focus(threshold());
check("focusing the threshold selects what is in it, so a digit replaces it",
      selected,
      "the box keeps a cursor in its value, so typing 3 into 1.2 gives 1.23");

view.unmount();
host.removeChild(box);
console.log();
if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log("ALL OK");
// The toast stack holds timers and jsdom holds its window, so node has live
// handles after the last check and never exits on its own. Everything under
// test has already run.
dom.window.close();
process.exit(0);
