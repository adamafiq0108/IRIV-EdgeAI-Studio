// A table card's own "Download CSV" (Tok Aba, 2026-10-14): Widget.jsx draws
// the button at the card's top right, TableCsvDialog asks for the range and
// the columns, and POST /api/builder/table-csv makes the file.
//
//   1. the button: on table cards where the page asks (Analytics, Dashboard),
//      top right, kept off paper, there with the title hidden too; not on
//      other kinds and not in the Builders' canvas; the header CSV is gone
//   2. the dialog (2026-10-14 layout: chips as a radio group, a column grid
//      with a counter, quiet hints instead of warnings, a summary line):
//      quick ranges (with "This page's range" only where the
//      page has one), Custom range needs its dates, the columns with Select
//      all / Clear, at least one column, Download disabled until it makes sense
//   3. Download sends exactly {id, columns, range | from + to} through the
//      api client (apiUrl, so the base path is honoured) and saves the file;
//      a refusal is said in the dialog
//
//   node tests/check_tablecsv.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".tablecsv-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image", "Event", "CustomEvent",
                 "MouseEvent", "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
global.IS_REACT_ACT_ENVIRONMENT = true;
let saved = null;
global.URL.createObjectURL = () => "blob:x";
global.URL.revokeObjectURL = () => {};
dom.window.HTMLAnchorElement.prototype.click = function click() { saved = this.download; };
let sent = null;
let answer = { ok: true };
global.fetch = async (url, opts) => {
  sent = { url: String(url), method: opts.method, body: JSON.parse(opts.body || "null") };
  if (!answer.ok) {
    return { ok: false, status: 400, statusText: "Bad Request", headers: new Map(),
             json: async () => ({ detail: "a range can be at most 366 days" }) };
  }
  return { ok: true, status: 200,
           headers: { get: (h) => (h.toLowerCase() === "content-disposition"
             ? 'attachment; filename="Rejects_2026-10-08_2026-10-14.csv"' : null) },
           blob: async () => ({ size: 10 }) };
};

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};
const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  stdin: { contents: `
    import { createRoot } from "react-dom/client";
    import { act, createElement as h } from "react";
    import Widget from "${p("src/components/widgets/Widget.jsx")}";
    export { csvRequest, csvSummary, CSV_RANGES } from "${p("src/components/widgets/TableCsvDialog.jsx")}";
    export function live(el, props) {
      const root = createRoot(el);
      const render = (next) => act(() => { root.render(h(Widget, next)); });
      render(props);
      return { act, render, unmount: () => act(() => root.unmount()) };
    }`, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic",
  logLevel: "silent", define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const mount = (props) => {
  const box = document.body.appendChild(document.createElement("div"));
  const r = m.live(box, props);
  return { box, r, close: () => { r.unmount(); box.remove(); } };
};
const TABLE = { id: "t1", title: "Rejects", kind: "table", span: 2, grid: {}, template: "plain",
                data: { columns: ["Datetime", "status", "total panel"], rows: [["10:00", "NG", 3]] } };
const PAGE = { pageRange: { start: "2026-10-08", end: "2026-10-14" } };
const flush = () => new Promise((ok) => setTimeout(ok, 0));

// -- 1. the button --------------------------------------------------------------------
{
  const v = mount({ widget: TABLE, exportCsv: PAGE, fill: true });
  const btn = v.box.querySelector('button[aria-label="Download CSV"]');
  check("a table card on the page has Download CSV at its top right, kept off paper",
        btn && btn.classList.contains("table-csv-btn") && btn.classList.contains("no-print")
        && btn.textContent.includes("CSV") && btn.closest(".stat-card").style.position === "relative");
  v.close();
  const hidden = mount({ widget: { ...TABLE, show_title: false }, exportCsv: PAGE });
  check("...and with the title hidden it is still there",
        !!hidden.box.querySelector('button[aria-label="Download CSV"]') && !hidden.box.querySelector(".stat-label"));
  hidden.close();
  const canvas = mount({ widget: TABLE, fill: true });
  check("not in the Builders' canvas (the page did not ask)", !canvas.box.querySelector(".table-csv-btn"));
  canvas.close();
  const tile = mount({ widget: { id: "x", title: "n", kind: "tile", data: { value: 3 }, template: "plain" },
                      exportCsv: PAGE });
  check("not on other kinds", !tile.box.querySelector(".table-csv-btn"));
  tile.close();
  const empty = mount({ widget: { ...TABLE, data: { columns: [], rows: [] } }, exportCsv: PAGE });
  check("not on a table with no columns yet", !empty.box.querySelector(".table-csv-btn"));
  empty.close();
  const analytics = readFileSync(join(FRONT, "src/pages/Analytics.jsx"), "utf8");
  check("the Analytics header CSV button and its page-wide exporter are gone",
        !analytics.includes("exportTablesCsv") && !analytics.includes("Download every table")
        && analytics.includes("exportCsv={{ pageRange:"));
  check("the Dashboard asks for it too (no page range there)",
        readFileSync(join(FRONT, "src/pages/Main.jsx"), "utf8").includes("exportCsv={{ pageRange: null }}"));
}

// -- 2/3. the dialog (Tok Aba's 2026-10-14 layout) ---------------------------------------
{
  const v = mount({ widget: TABLE, exportCsv: PAGE });
  v.r.act(() => { v.box.querySelector(".table-csv-btn").click(); });
  const dlg = () => document.querySelector(".ui-dialog");
  const chips = () => [...dlg().querySelectorAll('[role="radiogroup"] [role="radio"]')];
  const chip = (val) => chips().find((c) => c.dataset.range === val);
  const chosen = () => chips().find((c) => c.getAttribute("aria-checked") === "true")?.dataset.range;
  const go = () => dlg().querySelector('[data-testid="csv-download"]');
  const summary = () => dlg().querySelector('[data-testid="csv-summary"]')?.textContent;
  const count = () => dlg().querySelector('[data-testid="csv-count"]')?.textContent;
  check("the dialog opens: Download CSV, the line naming the card, Date range and Columns",
        dlg() && dlg().textContent.includes("Download CSV")
        && dlg().querySelector('[data-testid="csv-sub"]')?.textContent.includes("Rejects")
        && dlg().textContent.includes("Date range") && dlg().textContent.includes("Columns"));
  check("the date range is a radio group of chips: Today, Last 7 / 30 days, This page's range "
        + "(the default here), All time, Custom range; no bare radios",
        chips().map((c) => c.dataset.range).join() === "today,7d,30d,page,all,custom"
        && chips().map((c) => c.textContent).join("|")
          === "Today|Last 7 days|Last 30 days|This page's range|All time|Custom range"
        && chosen() === "page" && !dlg().querySelector('input[type="radio"]'));
  check("...one Tab stop (the chosen chip)",
        chips().map((c) => c.tabIndex).join() === "-1,-1,-1,0,-1,-1");
  v.r.act(() => {
    chip("page").dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  });
  check("...the arrow keys move the choice", chosen() === "all", chosen());
  const boxes = () => [...dlg().querySelectorAll('[role="checkbox"][aria-label^="Column "]')];
  check("every column as a checkbox in a grid, all ticked by default, in the card's order",
        boxes().map((b) => b.getAttribute("aria-label")).join() === "Column Datetime,Column status,Column total panel"
        && boxes().every((b) => b.getAttribute("aria-checked") === "true")
        && !!dlg().querySelector(".csv-grid") && !dlg().querySelector('input[type="checkbox"]'));
  check("...with a live counter", count() === "3 of 3 columns selected", count());
  const tool = (name) => [...dlg().querySelectorAll("button")].find((b) => b.textContent === name);
  v.r.act(() => { tool("Clear").click(); });
  check("Clear: no column, Download disabled, a quiet hint (not a warning)",
        boxes().every((b) => b.getAttribute("aria-checked") === "false") && go().disabled
        && count() === "0 of 3 columns selected"
        && dlg().querySelector('[data-testid="csv-cols-hint"]')?.classList.contains("muted")
        && !dlg().querySelector(".c-warn"));
  v.r.act(() => { tool("Select all").click(); });
  v.r.act(() => { boxes()[1].click(); });
  check("Select all, then untick status",
        boxes().map((b) => b.getAttribute("aria-checked")).join() === "true,false,true" && !go().disabled
        && count() === "2 of 3 columns selected");
  v.r.act(() => { chip("custom").click(); });
  check("Custom range: a Pick dates button and a quiet hint, no warning, Download disabled",
        go().disabled && dlg().querySelector('[data-testid="csv-custom"] button')?.textContent.includes("Pick dates")
        && dlg().querySelector('[data-testid="csv-hint"]')?.classList.contains("muted")
        && !dlg().querySelector(".c-warn") && !dlg().textContent.includes("Pick the dates for the custom range")
        && summary() === "Custom range, dates not chosen, 2 columns", summary());
  v.r.act(() => { chip("7d").click(); });
  check("the summary line follows the choice", summary() === "Last 7 days, 2 columns", summary());
  check("every row is included, and a big export is said to take time",
        dlg().textContent.includes("Every row in the range is included. Exporting more than 10,000 rows takes some time: keep this window open until the file is ready.")
        && !dlg().textContent.includes("Up to the newest"));
  await v.r.act(async () => { go().click(); await flush(); });
  check("Download sends {id, columns, range} through the api client",
        sent && sent.method === "POST" && sent.url.endsWith("/api/builder/table-csv")
        && JSON.stringify(sent.body) === '{"id":"t1","columns":["Datetime","total panel"],"range":"7d"}',
        JSON.stringify(sent));
  check("...saves the file under the server's name and closes",
        saved === "Rejects_2026-10-08_2026-10-14.csv" && !document.querySelector(".ui-dialog"));
  // A refusal is said in the dialog.
  answer = { ok: false };
  v.r.act(() => { v.box.querySelector(".table-csv-btn").click(); });
  await v.r.act(async () => { go().click(); await flush(); });
  check("a refusal is said in plain words in the dialog",
        dlg() && dlg().textContent.includes("366 days"), dlg() && dlg().textContent.slice(0, 300));
  answer = { ok: true };
  v.close();
}
check("the page range and a custom range send from / to; a named one sends range",
      JSON.stringify(m.csvRequest({ id: "a", range: "page", pageRange: PAGE.pageRange, columns: ["x"] }))
        === '{"id":"a","columns":["x"],"from":"2026-10-08","to":"2026-10-14"}'
      && JSON.stringify(m.csvRequest({ id: "a", range: "custom", custom: { start: "2026-01-01", end: "2026-01-31" },
                                       columns: ["x"] })) === '{"id":"a","columns":["x"],"from":"2026-01-01","to":"2026-01-31"}');
check("a chosen custom range reads as its dates in the summary",
      m.csvSummary({ range: "custom", custom: { start: "2026-10-12", end: "2026-10-14" }, count: 5,
                     ranges: m.CSV_RANGES }) === "12 Oct 2026 - 14 Oct 2026, 5 columns");
{
  const v = mount({ widget: TABLE, exportCsv: { pageRange: null } });
  v.r.act(() => { v.box.querySelector(".table-csv-btn").click(); });
  const vals = [...document.querySelectorAll('.ui-dialog [role="radio"]')];
  check("on the Dashboard (no page range) there is no 'This page's range', Last 7 days by default",
        !vals.some((c) => c.dataset.range === "page")
        && vals.find((c) => c.getAttribute("aria-checked") === "true")?.dataset.range === "7d");
  v.close();
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
