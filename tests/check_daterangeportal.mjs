// The date pickers' popovers are portalled to <body> (2026-10-14).
//
// The bug (Tok Aba's screenshots, System Log and Events Log): the Date range
// popover opened from the filter card was painted UNDER the log table's
// sticky header band further down the page. The filter card was a stacking
// context of its own - its entrance animation, filled with `both`, left a
// transform on it - so the popover's z-index only counted inside that card,
// and the table's sticky <th> (z-index 2, later in the page) won.
//
// Pinned here:
//   1. DateRangePicker and DatePicker render their panel as a child of
//      document.body, position fixed, z-index POPOVER_Z (above sticky
//      headers and the page chrome, below dialogs and toasts), wearing the
//      scope class of the button it came from
//   2. clicking inside the portalled panel does not close it; outside does;
//      Escape closes it and puts focus back on the button
//   3. fixedPlace keeps the panel inside the window: left edge, right edge,
//      bottom (it scrolls rather than running off), and opens from the
//      button's left edge when hanging off its right edge would not fit
//   4. the card entrance animation no longer leaves a stacking context
//      (fill `backwards`, not `both`)
//   5. every page that uses a picker gets the shared component: System Log,
//      Events Log, Audit, Footage, Analytics (DateRangePicker) and Data
//      Retention (DatePicker)
//   6. IN A REAL BROWSER when Chrome is on this machine: a sticky-header table
//      fixture under a transformed card, the popover's own points are
//      hit-testable (elementFromPoint returns the popover, not the header);
//      skipped, and said so, when no Chrome is found; and the same picker
//      inside a dialog is drawn over the dialog
//   7. a picker inside a dialog (z 70) stacks at 71, a click inside it keeps
//      both open, Escape closes the picker only (focus back on its button),
//      the next Escape is the dialog's; toasts sit above every layer
//
//   node tests/check_daterangeportal.mjs
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".daterangeportal-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement", "Event",
                 "CustomEvent", "MouseEvent", "PointerEvent", "KeyboardEvent", "FocusEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import DateRangePicker, { fixedPlace, POPOVER_Z } from "${p("src/components/ui/DateRangePicker.jsx")}";
      import DatePicker from "${p("src/components/ui/DatePicker.jsx")}";
      export { act, fixedPlace, POPOVER_Z };
      export function mount(el, which) {
        const root = createRoot(el);
        act(() => { root.render(which === "day"
          ? h(DatePicker, { value: new Date(2026, 9, 1), onChange: () => {} })
          : h(DateRangePicker, { start: "", end: "", onApply: () => {} })); });
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
const { act } = m;
const flush = async () => {
  for (let i = 0; i < 4; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  }
};
const fire = async (el, type, init = {}) => {
  await act(async () => {
    el.dispatchEvent(new window[type === "keydown" ? "KeyboardEvent" : "MouseEvent"](type,
      { bubbles: true, cancelable: true, ...init }));
  });
  await flush();
};

// 1 + 2 -----------------------------------------------------------------------
for (const [which, sel, name] of [["range", "[data-drp-panel]", "DateRangePicker"],
                                   ["day", "[data-dp-panel]", "DatePicker"]]) {
  // A legacy-scoped page with a card and a sticky table header, as the
  // System Log and Events Log pages have.
  const page = document.createElement("main");
  page.className = "cy-legacy";
  page.innerHTML = `<div class="panel-card"><div class="host"></div></div>
    <table><thead><tr><th style="position:sticky;top:0;z-index:2">TIME</th></tr></thead></table>`;
  document.body.appendChild(page);
  const host = page.querySelector(".host");
  const r = m.mount(host, which);
  await flush();
  const button = host.querySelector("button");
  await fire(button, "click");
  const panel = document.querySelector(sel);
  check(`1. ${name}: the panel is a child of <body>, not of the card`,
        !!panel && panel.parentElement === document.body && !page.contains(panel));
  const z = Number(panel?.style.zIndex);
  check(`...position fixed, z-index ${m.POPOVER_Z} above the sticky header (2), below dialogs (50)`,
        panel?.style.position === "fixed" && z === m.POPOVER_Z && z > 2 && z < 50, `${panel?.style.position} ${z}`);
  check("...wearing its button's scope (cy-legacy), so the tokens mean the same",
        panel?.classList.contains("cy-legacy"));
  await fire(panel.querySelector("button"), "mousedown");
  check(`2. ${name}: a click inside the portalled panel does not close it`,
        !!document.querySelector(sel));
  if (which === "range") {
    await fire(document.body, "keydown", { key: "Escape" });
    check("...Escape closes it and gives focus back to the button",
          !document.querySelector(sel) && document.activeElement === button);
    await fire(button, "click");
  }
  await fire(document.body, "mousedown");
  check(`...a click outside closes it`, !document.querySelector(sel));
  r.unmount();
  page.remove();
}

// 7. INSIDE A DIALOG (the table card's Download CSV, 2026-10-14) --------------
// The dialog layer is z 70 (ui-dialog-backdrop): the picker opened from a
// button inside it must sit over it, keep it open when clicked, and Escape
// must close the picker only.
for (const [which, sel, name] of [["range", "[data-drp-panel]", "DateRangePicker"],
                                   ["day", "[data-dp-panel]", "DatePicker"]]) {
  const backdrop = document.createElement("div");
  backdrop.className = "ui-dialog-backdrop";
  backdrop.style.cssText = "position:fixed;inset:0;z-index:70";
  backdrop.innerHTML = `<div class="ui-dialog" role="dialog" aria-modal="true"><div class="host"></div></div>`;
  let dialogClosed = 0;
  backdrop.addEventListener("mousedown", () => { dialogClosed += 1; });
  const dialogEsc = (e) => { if (e.key === "Escape") dialogClosed += 1; };
  window.addEventListener("keydown", dialogEsc);          // as ui/primitives.jsx Dialog listens
  document.body.appendChild(backdrop);
  const r = m.mount(backdrop.querySelector(".host"), which);
  await flush();
  const button = backdrop.querySelector("button");
  await fire(button, "click");
  const panel = document.querySelector(sel);
  check(`7. ${name} inside a dialog (z 70): its panel stacks just above it (71)`,
        !!panel && Number(panel.style.zIndex) === 71 && panel.parentElement === document.body,
        panel?.style.zIndex);
  await fire(panel.querySelector("button"), "mousedown");
  check("...a click inside the picker neither closes it nor the dialog",
        !!document.querySelector(sel) && dialogClosed === 0, String(dialogClosed));
  await fire(document.body, "keydown", { key: "Escape" });
  check("...Escape closes the picker first, the dialog stays open, focus back on the button",
        !document.querySelector(sel) && dialogClosed === 0 && document.activeElement === button,
        String(dialogClosed));
  await fire(document.body, "keydown", { key: "Escape" });
  check("...and the next Escape is the dialog's", dialogClosed === 1, String(dialogClosed));
  window.removeEventListener("keydown", dialogEsc);
  r.unmount();
  backdrop.remove();
}
const toastZ = /\.toast-stack \{[^}]*z-index: (\d+)/.exec(readFileSync(join(FRONT, "src", "theme.css"), "utf8"));
check("...toasts stay above everything (above legacy modals at 1000)",
      toastZ && Number(toastZ[1]) > 1000, toastZ && toastZ[1]);

// 3 ---------------------------------------------------------------------------
const VIEW = { width: 1280, height: 820 };
const SIZE = { width: 562, height: 300 };
const rect = (left, top, w = 100, hgt = 30) => ({ left, top, right: left + w, bottom: top + hgt,
                                                 width: w, height: hgt });
let q = m.fixedPlace(rect(1100, 200), SIZE, VIEW);
check("3. a button on the right: the panel hangs off its right edge, inside the window",
      q.left + SIZE.width === 1200 && q.top === 236);
q = m.fixedPlace(rect(267, 262), SIZE, VIEW);
check("...a button too far left for that opens from its left edge (no left clip)",
      q.left === 267, JSON.stringify(q));
q = m.fixedPlace(rect(1230, 262, 40), SIZE, VIEW);
check("...never past the right edge", q.left + SIZE.width <= VIEW.width - 8, JSON.stringify(q));
q = m.fixedPlace(rect(400, 700), SIZE, VIEW);
check("...a low button flips it above", q.above && q.top + SIZE.height <= 700, JSON.stringify(q));
q = m.fixedPlace(rect(10, 300, 80), { width: 374, height: 900 }, { width: 390, height: 820 });
check("...a phone: on screen at the left, and it scrolls rather than running off the bottom",
      q.left >= 8 && q.top >= 8 && q.top + q.maxHeight <= 812, JSON.stringify(q));

// 4 ---------------------------------------------------------------------------
const css = readFileSync(join(FRONT, "src", "theme.css"), "utf8");
check("4. the card entrance animation fills backwards (no lasting transform, no stacking context)",
      /\.page\.active > \.panel-card,\s*\n\.page\.active > \.grid > \.panel-card \{\s*\n\s*animation: fade var\(--page-in\) var\(--page-ease\) backwards;/.test(css));

// 5 ---------------------------------------------------------------------------
const uses = [["pages/Logs.jsx", "DateRangePicker"], ["pages/EventTable.jsx", "DateRangePicker"],
              ["pages/Audit.jsx", "DateRangePicker"], ["pages/Footage.jsx", "DateRangePicker"],
              ["pages/Analytics.jsx", "DateRangePicker"], ["components/RetentionPanel.jsx", "DatePicker"]];
for (const [file, comp] of uses) {
  const src = readFileSync(join(FRONT, "src", ...file.split("/")), "utf8");
  check(`5. ${file} uses the shared ${comp}`, src.includes(`<${comp} `));
}
for (const f of ["DateRangePicker.jsx", "DatePicker.jsx"]) {
  const src = readFileSync(join(FRONT, "src", "components", "ui", f), "utf8");
  check(`...${f} portals its panel`, src.includes("createPortal(") && src.includes("document.body"));
}

// 6 ---------------------------------------------------------------------------
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
                "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
  .find((x) => existsSync(x));
let pw = null;
try { pw = req("playwright-core"); } catch { pw = null; }
if (!CHROME || !pw) {
  console.log("skip 6. no Chrome / playwright-core here: the browser hit-test did not run");
} else {
  const dir = mkdtempSync(join(tmpdir(), "drp-"));
  await build({
    stdin: { contents: `
      import { createRoot } from "react-dom/client";
      import { createElement as h } from "react";
      import DateRangePicker from "${p("src/components/ui/DateRangePicker.jsx")}";
      createRoot(document.getElementById("host")).render(
        h(DateRangePicker, { start: "", end: "", onApply: () => {} }));
    `, resolveDir: FRONT, loader: "js" },
    bundle: true, format: "iife", platform: "browser", outfile: join(dir, "b.js"),
    jsx: "automatic", logLevel: "silent", loader: { ".css": "empty" },
    alias: { "@": join(FRONT, "src") }, define: { "process.env.NODE_ENV": '"production"' },
  });
  // The card carries a transform (the stacking context that trapped the old
  // popover); the table's sticky header sits right under the toolbar.
  writeFileSync(join(dir, "i.html"), `<!doctype html><html><body style="margin:0;font:13px sans-serif">
    <div style="padding:20px;transform:translateY(0);background:#eee"><div id="host"></div></div>
    <div style="height:400px;overflow:auto"><table style="width:100%;border-collapse:collapse">
      <thead><tr><th style="position:sticky;top:0;z-index:2;background:#ccc;height:40px">TIME</th>
      <th style="position:sticky;top:0;z-index:2;background:#ccc">EVENT</th></tr></thead>
      <tbody>${"<tr><td style='height:30px'>row</td><td>x</td></tr>".repeat(30)}</tbody></table></div>
    <script src="b.js"></script></body></html>`);
  const browser = await pw.chromium.launch({ executablePath: CHROME, headless: true });
  const page = await browser.newPage({ viewport: { width: 1024, height: 700 } });
  await page.goto(pathToFileURL(join(dir, "i.html")).href);
  await page.waitForTimeout(300);
  await page.click("#host button");
  await page.waitForTimeout(300);
  const res = await page.evaluate(() => {
    const panel = document.querySelector("[data-drp-panel]");
    const r = panel.getBoundingClientRect();
    let miss = 0, n = 0;
    for (let fx = 0.05; fx < 1; fx += 0.1) {
      for (let fy = 0.05; fy < 1; fy += 0.1) {
        n += 1;
        if (!panel.contains(document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy))) miss += 1;
      }
    }
    const th = document.querySelector("th").getBoundingClientRect();
    return { miss, n, overlapsHeader: th.top < r.bottom && th.bottom > r.top,
             centre: panel.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)),
             inView: r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight };
  });
  // The same picker inside a dialog backdrop at z 70.
  writeFileSync(join(dir, "d.html"), `<!doctype html><html><body style="margin:0;font:13px sans-serif">
    <div style="position:fixed;inset:0;z-index:70;display:grid;place-items:center;background:rgba(0,0,0,.4)">
      <div role="dialog" style="width:420px;height:360px;overflow:auto;background:#fff;padding:16px">
        <div id="host"></div><p style="height:600px">dialog body</p></div></div>
    <script src="b.js"></script></body></html>`);
  await page.goto(pathToFileURL(join(dir, "d.html")).href);
  await page.waitForTimeout(300);
  await page.click("#host button");
  await page.waitForTimeout(300);
  const inDlg = await page.evaluate(() => {
    const panel = document.querySelector("[data-drp-panel]");
    const r = panel.getBoundingClientRect();
    let miss = 0;
    for (let fx = 0.05; fx < 1; fx += 0.1) {
      for (let fy = 0.05; fy < 1; fy += 0.1) {
        if (!panel.contains(document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy))) miss += 1;
      }
    }
    return { miss, z: getComputedStyle(panel).zIndex };
  });
  await browser.close();
  check("6. in Chrome the popover crosses the sticky header and still wins every hit-test",
        res.overlapsHeader && res.miss === 0 && res.centre && res.inView, JSON.stringify(res));
  check("...and inside a dialog it is drawn over the dialog: every hit-test lands on it",
        inDlg.miss === 0 && inDlg.z === "71", JSON.stringify(inDlg));
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
