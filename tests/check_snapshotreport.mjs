// The snapshot photo window and its PDF notice (components/SnapshotWindow.jsx,
// lib/snapshotReport.js).
//
//   1. the words: seconds, direction, notice number, what was detected, the
//      timer rows (threshold 20 s, 40 s on camera = +20 seconds; a stay under
//      the threshold = 0; a stay still running = no excess at all)
//   2. the real jsPDF: the notice has the header, the notice number, the facts,
//      the timer only when there is one, a page per close-up only when asked,
//      and "Page n of N" on every page
//   3. the window, the real component in jsdom: facts under or beside the
//      photo, "--" for what is unknown, the timer table only with real timer
//      data, close-ups one at a time with Previous and Next, and the PDF
//      choice about the close-ups
//   4. the source: the page is wired to it, jsPDF loads only on a click
//
//   node tests/check_snapshotreport.mjs
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const SRC = join(FRONT, "src");
const R = await import(pathToFileURL(join(SRC, "lib", "snapshotReport.js")).href);
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const { jsPDF } = req("jspdf");

let bad = 0;
const ok = (c, msg, detail = "") => {
  if (c) console.log(`ok   ${msg}`);
  else { bad += 1; console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); }
};

// -- 1. the words -------------------------------------------------------------------
ok(R.secondsText(20) === "20 seconds" && R.secondsText(1) === "1 second"
   && R.secondsText(20.5) === "20.5 seconds" && R.secondsText(20.02) === "20 seconds"
   && R.secondsText("x") === "--", "seconds are written the way the example writes them");
ok(R.directionText("up") === "Going up" && R.directionText("DOWN") === "Going down"
   && R.directionText("") === "" && R.directionText("left") === "Left",
   "direction reads Going up / Going down, and anything else as it came");
ok(R.noticeDash("") === "--" && R.noticeDash(null) === "--" && R.noticeDash("  ") === "--" && R.noticeDash("x") === "x",
   "an unknown value is a visible dash");
ok(R.noticeNumber({ date: "2026-10-30", time: "09:49:14" }) === "20261030-094914",
   "the notice number is the day and the second of the photo");
ok(R.detectedText({ violation: "Not holding the handrail" }) === "Not holding the handrail"
   && R.detectedText({ crops: [{ label: "NOT HOLDING", primary: true }] }) === "Not holding"
   && R.detectedText({}) === "", "what was detected: the node's words, else the class, else nothing");

const T = (o) => ({ threshold_s: 20, elapsed_s: 20.4, ended: true, total_s: 40, exceeded_s: 20, ...o });
let rows = R.timerRows(T({}));
ok(rows && rows[0][1] === "20 seconds" && rows[1][1] === "40 seconds" && rows[2][1] === "+20 seconds",
   "threshold 20 s, 40 s on camera: configured 20, total 40, violation +20 seconds", JSON.stringify(rows));
rows = R.timerRows(T({ total_s: 15, exceeded_s: 0 }));
ok(rows[2][1].startsWith("0 seconds"), "a stay under the threshold is 0 seconds over, said plainly");
rows = R.timerRows(T({ ended: false, total_s: null, exceeded_s: null }));
ok(rows[1][1].includes("still running") && rows[1][1].includes("20.4")
   && rows[2][1] === "not known yet", "a stay still running shows no total and no excess", JSON.stringify(rows));
ok(R.timerRows(null) === null && R.timerRows({}) === null && R.timerRows({ threshold_s: 0 }) === null,
   "no timer data: no timer section");

// -- 2. the real jsPDF -------------------------------------------------------------------
const JPG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=";
const view = (extra = {}) => ({
  id: "cap_staircase4_oid685_19a3f2c1d80", has_record: true,
  detection_id: "20261030_094914_685", date: "2026-10-30", time: "09:49:14",
  camera_name: "staircase4", device: "staircase4", label: "", oid: "685",
  direction: "", location: "", violation: "Not holding the handrail",
  notice_title: "HANDRAIL VIOLATION NOTICE", crops: [], timer: null, ...extra });
const main = { dataUrl: JPG, w: 920, h: 640 };
const raw = (doc) => Buffer.from(doc.output("arraybuffer")).toString("latin1");
{
  const doc = R.assembleNotice(jsPDF, { view: view(), main, crops: [], systemName: "Staircase",
                                        created: "2026-10-30 11:20" });
  const t = raw(doc);
  ok(t.startsWith("%PDF-") && doc.getNumberOfPages() === 1, "one page for a photo with no close-ups");
  ok(t.includes("HANDRAIL VIOLATION NOTICE") && t.includes("Notice no. 20261030-094914"),
     "the red title and the notice number are in the header");
  ok(t.includes("20261030_094914_685") && t.includes("09:49:14") && t.includes("2026-10-30"),
     "the detection id, time and date are printed");
  ok(t.includes("Not holding the handrail") && t.includes("staircase4"),
     "what was detected and the device are printed");
  ok(!t.includes("Direction") && !t.includes("Location"), "an unknown direction and location are left out");
  ok(!t.includes("TIMER") && !t.includes("Configured timer"), "no timer section without a timer");
  ok(t.includes("Created 2026-10-30 11:20") && t.includes("Page 1 of 1"), "created time and page number");
}
{
  const crops = [{ dataUrl: JPG, w: 100, h: 200, label: "person", confidence: 0.91, primary: true },
                 { dataUrl: JPG, w: 80, h: 80, label: "person", confidence: 0.5, primary: false }];
  const doc = R.assembleNotice(jsPDF, { view: view({ timer: T({}), direction: "down" }), main, crops,
                                        systemName: "Staircase" });
  const t = raw(doc);
  ok(doc.getNumberOfPages() === 3, "a page for each close-up asked for", doc.getNumberOfPages());
  ok(t.includes("CLOSE-UP 1 OF 2") && t.includes("CLOSE-UP 2 OF 2") && t.includes("91%"),
     "each close-up page says which it is and how sure the AI was");
  ok(t.includes("Configured timer") && t.includes("+20 seconds") && t.includes("40 seconds"),
     "the timer section carries the three figures when a real timer ran");
  ok(t.includes("Going down"), "the direction reads Going down");
  ok(t.includes("Page 1 of 3") && t.includes("Page 3 of 3"), "every page says Page n of N");
  const none = R.assembleNotice(jsPDF, { view: view({ timer: T({}) }), main, crops: [], systemName: "S" });
  ok(none.getNumberOfPages() === 1, "no close-ups asked for: no close-up pages, even when they exist");
  const plain = R.assembleNotice(jsPDF, { view: view({ notice_title: "" }), main, systemName: "S" });
  ok(raw(plain).includes("VIOLATION NOTICE") && !raw(plain).includes("HANDRAIL VIOLATION"),
     "no title set: the stock heading");
}
ok(R.noticeFileName({ detection_id: "20261030_094914_685" }) === "violation-notice-20261030_094914_685.pdf"
   && R.noticeFileName({ id: "a b/c" }) === "violation-notice-a-b-c.pdf", "a safe file name");
const f = R.fitPicture(920, 640, 182, 120);
ok(Math.abs(f.w / f.h - 920 / 640) < 0.001 && f.w <= 182.001 && f.h <= 120.001,
   "the photo keeps its shape inside its box");

// -- 3. the window ------------------------------------------------------------------------
const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Event", "CustomEvent",
                 "MouseEvent", "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage", "Image", "FileReader"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => ({ path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        export const api = {
          get(url) {
            globalThis.__gets.push(url);
            if (globalThis.__fail) return Promise.reject(new Error("no"));
            return Promise.resolve({ ok: true, snapshot: globalThis.__view });
          },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `, resolveDir: FRONT, loader: "js" }));
  },
};
// The app shell's context: the window only reads the system's name from it.
const shimApp = {
  name: "shim-app",
  setup(b) {
    b.onResolve({ filter: /state[\/]app\.jsx$/ }, () => ({ path: "app-shim", namespace: "shim" }));
    b.onLoad({ filter: /^app-shim$/, namespace: "shim" }, () => ({
      contents: `export const useApp = () => ({ system: { display_name: "Staircase" } });`,
      resolveDir: FRONT, loader: "js" }));
  },
};
const OUT = join(FRONT, "node_modules", ".snapshotwindow-test.cjs");
const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shimApi, shimApp],
  stdin: { contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import SnapshotWindow from "${p("src/components/SnapshotWindow.jsx")}";
      import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
      export { act, h, SnapshotWindow, ToastProvider };
      export function mount(el, node) {
        const root = createRoot(el);
        act(() => { root.render(node); });
        return { unmount: () => act(() => root.unmount()) };
      }`, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;
const wait = (ms = 0) => m.act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const row = { id: "cap_staircase4_oid685_19a3f2c1d80", mtime: 1 };
async function open(v, props = {}) {
  globalThis.__gets = [];
  globalThis.__view = v;
  globalThis.__fail = !v;
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null,
    h(m.SnapshotWindow, { row, imgUrl: (id) => `/img/${id}`, canDelete: true,
                          onDelete: () => {}, onClose: () => {}, ...props })));
  await wait(10);
  return { box, close() { r.unmount(); document.body.removeChild(box); } };
}
const click = async (el) => m.act(async () => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 5));
});
// Tags become spaces, so two cells side by side read as two words, not one.
const text = (box) => box.innerHTML.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
{
  const w = await open(view({ timer: null, crops: [] }));
  ok(!!w.box.querySelector("[data-snapshot-window] .fx-snap-photo img")
     && !!w.box.querySelector("[data-snapshot-window] .fx-snap-side"),
     "the photo and its facts are two blocks of one window (side by side or stacked by CSS)");
  ok(globalThis.__gets[0].endsWith(`${encodeURIComponent(row.id)}/meta`), "it asks the master for this photo's record");
  const t = text(w.box);
  ok(t.includes("Detection ID") && t.includes("20261030_094914_685") && t.includes("09:49:14")
     && t.includes("2026-10-30"), "detection id, time and date are shown under or beside the photo");
  ok(t.includes("Not holding the handrail") && t.includes("staircase4"), "what was detected and the device");
  ok(!t.includes("Direction") && !t.includes("Location"), "an unknown direction and location are left out");
  ok(!w.box.querySelector("[data-snapshot-timer]"), "no timer section without timer data");
  ok(!w.box.querySelector("[data-snapshot-crops]"), "no close-up gallery without close-ups");
  w.close();
}
{
  const w = await open(view({ timer: T({}), direction: "up" }));
  const t = text(w.box);
  ok(!!w.box.querySelector("[data-snapshot-timer]") && t.includes("Configured timer 20 seconds")
     && t.includes("Total duration 40 seconds") && t.includes("Violation duration +20 seconds"),
     "with a real timer: configured 20, total 40, violation +20 seconds");
  ok(t.includes("Going up"), "direction reads Going up");
  w.close();
  const run = await open(view({ timer: T({ ended: false, total_s: null, exceeded_s: null }) }));
  const t2 = text(run.box);
  ok(t2.includes("still running") && !t2.includes("+"), "a stay still running: no total, no +excess");
  run.close();
}
{
  const crops = [0, 1, 2].map((n) => ({ n, label: n === 0 ? "person" : "cart", track: n,
                                         confidence: 0.9 - n * 0.2, primary: n === 0 }));
  const w = await open(view({ crops }));
  const gal = () => w.box.querySelector("[data-snapshot-crops]");
  ok(!!gal() && text(gal()).includes("1 of 3") && gal().querySelector("img").getAttribute("src").endsWith("/crops/0"),
     "close-ups are shown one at a time, starting with the object the notice is about");
  await click(w.box.querySelector("[data-crop-next]"));
  ok(text(gal()).includes("2 of 3") && gal().querySelector("img").getAttribute("src").endsWith("/crops/1"), "Next shows the next one");
  await click(w.box.querySelector("[data-crop-prev]"));
  await click(w.box.querySelector("[data-crop-prev]"));
  ok(text(gal()).includes("3 of 3"), "Previous from the first wraps to the last");
  await click(w.box.querySelector("[data-pdf-open]"));
  const ask = w.box.querySelector("[data-pdf-ask]");
  ok(!!ask && ask.textContent.includes("Include the close-ups (3)"),
     "Download PDF asks whether the close-ups go in");
  const cb = ask.querySelector("input[type=checkbox]");
  ok(cb && cb.checked === true, "...ticked by default, and the user can untick it");
  await click(cb);
  ok(w.box.querySelector("[data-pdf-ask] input[type=checkbox]").checked === false, "...unticking works");
  w.close();
  const one = await open(view({ crops: [crops[0]] }));
  ok(!one.box.querySelector("[data-crop-prev]") && !one.box.querySelector("[data-crop-next]"),
     "one close-up needs no Previous or Next");
  one.close();
}
{
  const w = await open(null);
  ok(text(w.box).includes("could not be read") && !!w.box.querySelector("img"),
     "when the record cannot be read the picture is still there, with a plain note");
  ok(w.box.querySelector("[data-pdf-open]").disabled === true, "...and the PDF waits for the facts");
  w.close();
}

// -- 4. the source ------------------------------------------------------------------------
{
  const page = readFileSync(join(SRC, "pages", "Footage.jsx"), "utf8");
  const win = readFileSync(join(SRC, "components", "SnapshotWindow.jsx"), "utf8");
  const lib = readFileSync(join(SRC, "lib", "snapshotReport.js"), "utf8");
  const css = readFileSync(join(SRC, "theme.css"), "utf8");
  ok(/<SnapshotWindow row=\{lightbox\}/.test(page) && /lightbox && !isClip/.test(page),
     "Footage opens a photo in the new window and leaves a clip on the plain player");
  ok(!/^import .*jspdf/m.test(win + lib) && /import\("jspdf"\)/.test(lib),
     "jsPDF loads only when the PDF is asked for");
  ok(/\.fx-snap \{[^}]*grid-template-columns: minmax\(0, 1fr\)/.test(css)
     && /@media screen and \(min-width: 900px\)[^}]*\.fx-snap \{[^}]*1\.55fr/.test(css),
     "the layout is one column, and two from 900 px of screen up");
  for (const [name, t] of [["SnapshotWindow.jsx", win], ["snapshotReport.js", lib]]) {
    ok(!t.includes(String.fromCharCode(0x2014)), `no em dash in ${name}`);
  }
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\ncheck_snapshotreport: ok");
dom.window.close();
process.exit(0);
