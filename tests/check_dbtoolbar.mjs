// The Database page's table toolbar (Tok Aba, 2026-10-14):
//   [ Search rows... ] [ + Add Row ] [ Refresh ] ... [ Export v ]
//
//   1. source order: search first, Add Row the one filled button, Refresh,
//      then the row / table actions, Export last; the old Export CSV and
//      Export DB buttons are gone from the row
//   2. Export is a dropdown-menu (ui/dropdown-menu.jsx) with two items,
//      each with its one-line hint: Export CSV (this table's rows, by date
//      range and columns, every one of them) and Export database (the whole system, all tables, SQLite)
//   3. the gates are the old ones: Add Row only for an editable system; the
//      exports for anyone who can read it, disabled while unavailable (and
//      Export database while a download builds)
//   4. narrow screens: the search box takes its own line (CSS)
//   5. IN CHROME when it is on this machine: the real page renders the order
//      above, read-only hides Add Row, the menu opens with real menu
//      semantics (aria-haspopup / expanded, items as menuitems, Escape
//      closes and returns focus), and both items fire their handlers with
//      the same requests as before (GET /api/datasets/<t>/rows?limit=5000
//      &order=ts&dir=desc; GET /api/detector/export/db); at 390 px the search
//      box is full width and the menu opens inside the window. Skipped, and
//      said so, without Chrome.
//
//   node tests/check_dbtoolbar.mjs
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, readFileSync, writeFileSync, readdirSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const src = readFileSync(join(FRONT, "src", "pages", "Database.jsx"), "utf8");
const css = readFileSync(join(FRONT, "src", "theme.css"), "utf8");
const bar = src.slice(src.indexOf('<div className="db-toolbar">'), src.indexOf("db-kpis"));

// 1 ---------------------------------------------------------------------------
const at = (needle) => bar.indexOf(needle);
const order = [at('className="input db-rowsearch"'), at("data-db-add-row"), at("data-db-refresh"),
               at("Delete {sel.size} row"), at("Edit table"), at("Delete table"), at("data-db-export")];
check("1. order: search, Add Row, Refresh, the row/table actions, Export last",
      order.every((x) => x > 0) && order.every((x, i) => i === 0 || x > order[i - 1]),
      JSON.stringify(order));
check("...Add Row is the one filled button (btn-accent) and carries a plus",
      (bar.match(/btn-accent/g) || []).length === 1
      && /btn btn-sm btn-accent btn-ic" onClick=\{addRow\}/.test(bar) && bar.includes("<Plus size"));
check("...the old Export CSV / Export DB buttons are gone from the row",
      !/<button className="btn btn-sm" onClick=\{exportCsv\}/.test(bar)
      && !bar.includes('"Export DB"') && !bar.includes(">\n                Export CSV\n"));

// 2 ---------------------------------------------------------------------------
check("2. Export is the app's dropdown menu with two items and their hints",
      src.includes('from "../components/ui/dropdown-menu.jsx"')
      && /<DropdownMenuTrigger[\s\S]{0,200}data-db-export/.test(bar)
      && bar.includes('<span className="db-export-name">Export CSV</span>')
      && bar.includes("The rows of this table, by date range and columns, as a CSV file.")
      && bar.includes('<span className="db-export-name">Export database</span>')
      && bar.includes("Everything in this system: all tables, as a SQLite file."));
check("...each item calls the handler it always called",
      /<DropdownMenuItem onClick=\{\(\) => setCsvOpen\(true\)\}/.test(bar) && /<DropdownMenuItem onClick=\{exportDb\}/.test(bar));
check("...Export CSV opens the Download CSV dialog (date range, columns, every row); Export database is unchanged",
      src.includes("<TableCsvDialog") && src.includes("path={apiUrl(`/api/datasets/${encodeURIComponent(active.key)}/csv`)}")
      && !src.includes("limit: 5000, order: sort.col")
      && src.includes('fetch(apiUrl("/api/detector/export/db"), { headers: authHeaders() })'));

// 3 ---------------------------------------------------------------------------
check("3. Add Row only for an editable system",
      /\{editable && \(\s*<button className="btn btn-sm btn-accent btn-ic" onClick=\{addRow\}/.test(bar));
check("...the exports for anyone who can read it, disabled while unavailable or building",
      bar.includes("data-db-export=\"\" disabled={!status.available}")
      && bar.includes("onClick={() => setCsvOpen(true)} disabled={!status.available || !active}")
      && bar.includes("onClick={exportDb} disabled={!status.available || exporting}"));

// 4 ---------------------------------------------------------------------------
check("4. the search box is the widest field, and on a narrow screen takes its own line",
      /\.db-rowsearch \{ flex: 1 1 260px;/.test(css)
      && /@media screen and \(max-width: 640px\) \{\s*\.db-rowsearch \{ flex-basis: 100%;/.test(css));
check("...the hint is drawn in a text token, not faded",
      /\.db-export-hint \{[^}]*color: var\(--muted-foreground\)/.test(css));

// 5 ---------------------------------------------------------------------------
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
                "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
  .find((x) => existsSync(x));
let pw = null;
try { pw = req("playwright-core"); } catch { pw = null; }
const assets = join(ROOT, "core", "static", "app", "assets");
if (!CHROME || !pw || !existsSync(assets)) {
  console.log("skip 5. no Chrome / playwright-core / built assets here: the browser part did not run");
} else {
  const { build } = req("esbuild");
  const dir = mkdtempSync(join(tmpdir(), "dbbar-"));
  const p = (rel) => join(FRONT, rel).split("\\").join("/");
  const STATUS = { available: true, editable: true, datasets: [
    { key: "visits", name: "Visits", rows: 1, capped: false, mode: "alert",
      columns: [{ name: "who", type: "text" }, { name: "n", type: "number" }] }] };
  await build({
    plugins: [{
      name: "shim",
      setup(b) {
        b.onResolve({ filter: /api\/client\.js$/ }, () => ({ path: "api", namespace: "shim" }));
        b.onLoad({ filter: /^api$/, namespace: "shim" }, () => ({ contents: `
          window.__calls = [];
          const STATUS = ${JSON.stringify(STATUS)};
          export class ApiError extends Error {}
          export function friendlyError(e) { return String(e && e.message || e); }
          export function saveBlob() {}
          const download = (path, body) => { window.__calls.push("POST " + path + " " + JSON.stringify(body));
            return Promise.resolve({ blob: new Blob(["x"]), filename: "visits.csv" }); };
          const answer = (url, body) => {
            window.__calls.push(url);
            if (url.startsWith("/api/datasets/status"))
              return Promise.resolve({ ...STATUS, editable: !location.search.includes("ro=1") });
            if (url.includes("/rows")) return Promise.resolve({ rows: [{ ts: "2026-10-14 10:00:00", who: "a", n: 1 }], total: 1 });
            return Promise.resolve({});
          };
          export const api = { get: answer, post: answer, put: answer, del: answer, download };
        `, resolveDir: FRONT, loader: "js" }));
      },
    }],
    stdin: { contents: `
      import { createRoot } from "react-dom/client";
      import { createElement as h } from "react";
      import { createMemoryRouter, RouterProvider } from "react-router-dom";
      import Database from "${p("src/pages/Database.jsx")}";
      import { AppContext } from "${p("src/state/app.jsx")}";
      import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
      window.fetch = (url) => { window.__calls.push(String(url));
        return Promise.resolve(new Response(new Blob(["db"]), { status: 200,
          headers: { "Content-Disposition": 'attachment; filename="demo.db"' } })); };
      URL.createObjectURL = () => "blob:x"; URL.revokeObjectURL = () => {};
      HTMLAnchorElement.prototype.click = function () { window.__calls.push("download " + this.download); };
      const value = { user: { username: "tok", role: "master" }, system: { slug: "demo" }, brand: {} };
      const router = createMemoryRouter([{ path: "*", element: h(AppContext.Provider, { value },
        h(ToastProvider, null, h("div", { className: "cy-app" },
          h("main", { className: "cy-legacy", style: { padding: 16 } }, h(Database))))) }]);
      createRoot(document.getElementById("root")).render(h(RouterProvider, { router }));
    `, resolveDir: FRONT, loader: "jsx" },
    bundle: true, format: "iife", platform: "browser", outfile: join(dir, "b.js"),
    jsx: "automatic", logLevel: "silent", loader: { ".css": "empty", ".svg": "dataurl", ".png": "dataurl" },
    alias: { "@": join(FRONT, "src") },
    define: { "process.env.NODE_ENV": '"production"', "import.meta.env.DEV": "false" },
  });
  const sheets = readdirSync(assets).filter((f) => f.endsWith(".css"));
  for (const f of sheets) copyFileSync(join(assets, f), join(dir, f));
  writeFileSync(join(dir, "i.html"), `<!doctype html><html><head><meta charset="utf-8">
    ${sheets.map((f) => `<link rel="stylesheet" href="${f}">`).join("")}</head>
    <body><div id="root"></div><script src="b.js"></script></body></html>`);
  const browser = await pw.chromium.launch({ executablePath: CHROME, headless: true });
  const url = pathToFileURL(join(dir, "i.html")).href;

  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") pageErrors.push(m.text()); });
  await page.goto(url);
  await page.waitForTimeout(1500);
  if (!(await page.$(".db-toolbar"))) {
    console.error("page did not render:", pageErrors.join(" | "),
                  (await page.content()).slice(0, 600));
  }
  await page.waitForSelector(".db-toolbar", { timeout: 8000 });
  await page.waitForTimeout(300);
  const seen = await page.evaluate(() => [...document.querySelector(".db-toolbar").children]
    .map((el) => el.matches("input") ? "search" : el.textContent.trim()).filter(Boolean));
  check("5. in Chrome the toolbar reads: search, Add Row, Refresh, ..., Export",
        seen[0] === "search" && seen[1] === "Add Row" && seen[2] === "Refresh" && seen.at(-1) === "Export",
        JSON.stringify(seen));
  const trig = await page.$("[data-db-export]");
  const before = await trig.evaluate((b) => [b.getAttribute("aria-haspopup"), b.getAttribute("aria-expanded")]);
  await trig.click();
  await page.waitForTimeout(250);
  const menu = await page.evaluate(() => {
    const m = document.querySelector("[role=menu]");
    return m ? { items: [...m.querySelectorAll("[role=menuitem]")].map((i) => i.textContent),
                 expanded: document.querySelector("[data-db-export]").getAttribute("aria-expanded"),
                 r: m.getBoundingClientRect().toJSON() } : null;
  });
  check("...Export opens a real menu: haspopup, expanded, two menuitems with their hints",
        before[0] === "menu" && menu && menu.expanded === "true"
        && JSON.stringify(menu.items) === JSON.stringify([
          "Export CSVThe rows of this table, by date range and columns, as a CSV file.",
          "Export databaseEverything in this system: all tables, as a SQLite file."]),
        JSON.stringify({ before, menu }));
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  const afterEsc = await page.evaluate(() => ({ open: !!document.querySelector("[role=menu]"),
    focus: document.activeElement?.hasAttribute("data-db-export") }));
  check("...Escape closes it and gives focus back to Export", !afterEsc.open && afterEsc.focus,
        JSON.stringify(afterEsc));
  await page.evaluate(() => { window.__calls.length = 0; });
  await trig.click();
  await page.waitForTimeout(200);
  await page.click("[data-db-export-item=csv]");
  await page.waitForTimeout(300);
  const dlg = await page.evaluate(() => {
    const d = document.querySelector(".csv-dialog");
    return d ? { text: d.textContent,
                 cols: [...d.querySelectorAll(".csv-col-name")].map((n) => n.textContent) } : null;
  });
  check("...Export CSV opens the Download CSV dialog: the table's name, Datetime and its columns, and the long-export note",
        dlg && dlg.text.includes("Visits") && JSON.stringify(dlg.cols) === JSON.stringify(["Datetime", "who", "n"])
        && dlg.text.includes("Every row in the range is included. Exporting more than 10,000 rows takes some time"),
        JSON.stringify(dlg));
  await page.click('[data-testid="csv-download"]');
  await page.waitForTimeout(300);
  await trig.click();
  await page.waitForTimeout(200);
  await page.click("[data-db-export-item=db]");
  await page.waitForTimeout(400);
  const calls = await page.evaluate(() => window.__calls);
  check("...Download posts the range and the columns to the table's own route; Export database fires its old request",
        calls.includes('POST /api/datasets/visits/csv {"columns":["Datetime","who","n"],"range":"7d"}')
        && calls.some((c) => c.endsWith("/api/detector/export/db"))
        && calls.includes("download demo.db"),
        JSON.stringify(calls));
  await page.close();

  const ro = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await ro.goto(url + "?ro=1");
  await ro.waitForSelector(".db-toolbar", { timeout: 8000 });
  await ro.waitForTimeout(300);
  const roBar = await ro.evaluate(() => ({ add: !!document.querySelector("[data-db-add-row]"),
    exp: !!document.querySelector("[data-db-export]") }));
  check("...read-only: no Add Row, Export still there", !roBar.add && roBar.exp, JSON.stringify(roBar));
  await ro.close();

  const narrow = await browser.newPage({ viewport: { width: 390, height: 800 } });
  await narrow.goto(url);
  await narrow.waitForSelector(".db-toolbar", { timeout: 8000 });
  await narrow.waitForTimeout(300);
  const n = await narrow.evaluate(() => {
    const bar = document.querySelector(".db-toolbar").getBoundingClientRect();
    const s = document.querySelector(".db-rowsearch").getBoundingClientRect();
    const add = document.querySelector("[data-db-add-row]").getBoundingClientRect();
    return { full: s.width > bar.width - 40, below: add.top >= s.bottom };
  });
  await (await narrow.$("[data-db-export]")).click();
  await narrow.waitForTimeout(250);
  const mr = await narrow.evaluate(() => document.querySelector("[role=menu]")?.getBoundingClientRect().toJSON());
  check("...at 390 px the search box has its own line, the buttons wrap under it, the menu fits",
        n.full && n.below && mr && mr.left >= 0 && mr.right <= 390, JSON.stringify({ n, mr }));
  // Optional pictures for a reviewer: DBBAR_SHOTS=<dir> node tests/check_dbtoolbar.mjs
  if (process.env.DBBAR_SHOTS) {
    for (const theme of ["light", "dark"]) {
      for (const w of [1280, 390]) {
        const sp = await browser.newPage({ viewport: { width: w, height: 760 } });
        await sp.goto(url);
        if (theme === "dark") await sp.evaluate(() => document.documentElement.classList.add("dark"));
        await sp.waitForSelector(".db-toolbar", { timeout: 8000 });
        await sp.waitForTimeout(300);
        await (await sp.$("[data-db-export]")).click();
        await sp.waitForTimeout(300);
        await sp.screenshot({ path: join(process.env.DBBAR_SHOTS, `dbbar-${theme}-${w}.png`) });
        await sp.close();
      }
    }
  }
  await browser.close();
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
