// The SQL Playground's layout (Tok Aba, 2026-10-14: "make it look better").
// A visual and usability redesign only: same calls, same features.
//
//   1. source: the editor's own action line holds Format SQL, Save query and
//      Run query (the one filled button, with its Ctrl+Enter hint); the header
//      strip has the Read-only badge, "This system only", the row cap and the
//      Period select with a plain hint and "Insert period filter", which
//      inserts exactly "ts >= %(from)s AND ts < %(to)s"; a Tables strip; a
//      resizable split (role separator, keyboard too); a Snippets panel
//      (toggle, tabs, filter, "Inserts: ..." hints); a results head with the
//      truncation words; the friendlier empty state; the error panel; the
//      request body unchanged ({ sql, period, from, to }); no new dependency
//   2. IN CHROME when it is on this machine, the real page (shimmed api):
//      empty state; Insert period filter puts the exact text in the editor;
//      a run shows the row count and "Showing the first 500 rows" when the
//      answer is truncated, numbers right-aligned; an error lands in the red
//      panel with the database's own message; dragging the handle resizes
//      the editor; the Snippets panel toggles, and starts closed at 1024 px;
//      at 390 px nothing runs off the side. Skipped, and said so, without
//      Chrome. `SQLPG_SHOTS=<dir>` also saves screenshots.
//   3. IN CHROME: Period "Follow Analytics range" (2026-10-14) reads the range
//      the Analytics page saved, says it by the option, sends period custom
//      with its dates, re-reads at every Run; "(today)" when none is saved;
//      the other choices send what they sent before
//
//   node tests/check_sqlplayground_layout.mjs
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
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
const con = src.slice(src.indexOf("function SqlConsole("), src.indexOf("/** One cell."));
const css = readFileSync(join(FRONT, "src", "theme.css"), "utf8");

// 1 ---------------------------------------------------------------------------
const actions = con.slice(con.indexOf('data-sql-actions=""'), con.indexOf('data-sql-split=""'));
const fmt = actions.indexOf(">Format SQL<"), save = actions.indexOf("Save query"),
      runAt = actions.indexOf("▶ Run query");
check("1. Format SQL, Save query, Run query sit together on the editor's own line, Run last",
      fmt > 0 && save > fmt && runAt > save, JSON.stringify([fmt, save, runAt]));
check("...Run query is the one filled button there, with its Ctrl+Enter hint",
      (actions.match(/btn-accent/g) || []).length === 1 && actions.includes("db-sql-run")
      && actions.includes('<kbd className="db-sql-kbd" aria-hidden="true">Ctrl+Enter</kbd>')
      && actions.includes('aria-keyshortcuts="Control+Enter Meta+Enter"'));
check("...and the old bar no longer carries Run or Save",
      !con.slice(con.indexOf('className="db-sql-bar"'), con.indexOf('data-sql-strip=""')).includes("Run query"));
const strip = con.slice(con.indexOf('data-sql-strip=""'), con.indexOf("{naming && ("));
check("the header strip: Read-only badge, This system only, the row cap, Period, plain hint",
      strip.includes('<span className="db-sql-badge">Read-only</span>')
      && strip.includes('<span className="db-sql-fact">This system only</span>')
      && strip.includes("{cat.max_rows} rows max") && strip.includes('value={period}')
      && strip.includes("Applies only to queries that filter by time."));
check("...Insert period filter inserts exactly the text it always did",
      strip.includes("Insert period filter")
      && strip.includes('insertAtCursor("ts >= %(from)s AND ts < %(to)s|")')
      && !strip.includes("link-btn"));
check("a Tables strip, click to insert, columns in the tooltip",
      con.includes('<span className="db-sql-tables-label">Tables</span>')
      && con.includes("title={`Columns: ${cols.join(\", \")}`}")
      && con.includes('onClick={() => insertAtCursor(name + "|")}'));
check("a resizable split between editor and results, by pointer and by keyboard",
      con.includes('role="separator" aria-orientation="horizontal"')
      && con.includes("onPointerDown={startDrag}") && con.includes('e.key === "ArrowUp"')
      && con.includes("style={{ height: editorH }}") && /const SQL_EDITOR_MIN = 160;/.test(src));
check("the Snippets panel: a toggle, tabs, a filter, hints saying what is inserted",
      con.includes('aria-controls="db-sql-snips"') && con.includes('aria-label="Snippets"')
      && con.includes('role="tablist" aria-label="Snippet groups"')
      && con.includes('placeholder="Filter snippets…"')
      && con.includes("title={`Inserts: ${text.replace(\"|\", \"\")}`}"));
check("...a drawer at 1024 px and below, full width at 640",
      /@media screen and \(max-width: 1024px\) \{\s*\.db-sql-snips \{\s*position: absolute;/.test(css)
      && /@media screen and \(max-width: 640px\) \{\s*\.db-sql-snips \{ width: 100%;/.test(css));
check("the results head says the count, the time and the truncation",
      con.includes('data-sql-results-head=""') && con.includes("· Showing the first {cat?.max_rows || rowsShown} rows"));
check("the empty state keeps its title and gives the shortcut",
      con.includes("Run a query to see results")
      && con.includes('<kbd className="db-sql-kbd">Ctrl+Enter</kbd>'));
check("an error is a calm red panel with the database's own message",
      con.includes('className="db-sql-msg is-bad" role="alert"')
      && con.includes("The query did not run") && con.includes("{res.error}</pre>"));
check("the current line is highlighted, and the editor shows focus",
      con.includes("db-sql-curline") && /\.db-sql-editor:focus-within \{/.test(css));
check("the run still sends { sql, period, from, to } to /api/datasets/sql",
      con.includes('api.post("/api/datasets/sql",') && con.includes("{ sql, period, from: dateFrom, to: dateTo }"));
let pkgSame = true;
try {
  const head = execSync("git show HEAD:frontend/package.json", { cwd: ROOT }).toString();
  // The Analytics PDF report (2026-11-01) added jsPDF and html2canvas; they are
  // not the SQL editor's, so they are left out of the comparison.
  const PDF = ["jspdf", "html2canvas"];
  const deps = (j) => {
    const all = { ...JSON.parse(j).dependencies, ...JSON.parse(j).devDependencies };
    for (const k of PDF) delete all[k];
    return JSON.stringify(all);
  };
  pkgSame = deps(head) === deps(readFileSync(join(FRONT, "package.json"), "utf8"));
} catch { /* no git here: the source check below still stands */ }
check("no new dependency (package.json unchanged; the editor is still the textarea)",
      pkgSame && con.includes('<textarea className="db-sql-text"'));

// 2 ---------------------------------------------------------------------------
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
                "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
  .find((x) => existsSync(x));
let pw = null;
try { pw = req("playwright-core"); } catch { pw = null; }
const assets = join(ROOT, "core", "static", "app", "assets");
if (!CHROME || !pw || !existsSync(assets)) {
  console.log("skip 2. no Chrome / playwright-core / built assets here: the browser part did not run");
} else {
  const { build } = req("esbuild");
  const dir = mkdtempSync(join(tmpdir(), "sqlpg-"));
  const p = (rel) => join(FRONT, rel).split("\\").join("/");
  const STATUS = { available: true, editable: true, datasets: [
    { key: "pcb_sensata", name: "PCB Sensata", rows: 3, capped: false, mode: "alert",
      columns: [{ name: "zone", type: "text" }, { name: "total", type: "number" }] }] };
  const ROWS = Array.from({ length: 500 }, (_, i) => ({ ts: `2026-10-14 10:${String(i % 60).padStart(2, "0")}:00`, zone: "Bay", total: i * 1.5 }));
  await build({
    plugins: [{
      name: "shim",
      setup(b) {
        b.onResolve({ filter: /api\/client\.js$/ }, () => ({ path: "api", namespace: "shim" }));
        b.onLoad({ filter: /^api$/, namespace: "shim" }, () => ({ contents: `
          window.__posts = [];
          const STATUS = ${JSON.stringify(STATUS)};
          const ROWS = ${JSON.stringify(ROWS)};
          export class ApiError extends Error {}
          export function friendlyError(e) { return String(e && e.message || e); }
          export function saveBlob() {}
          const get = (url) => {
            if (url.startsWith("/api/datasets/status")) return Promise.resolve(STATUS);
            if (url.startsWith("/api/datasets/sql/catalog"))
              return Promise.resolve({ max_rows: 500, catalog: { events: ["ts", "type", "zone"],
                dataset_rows: ["ts", "dataset"], pcb_sensata: ["ts", "zone", "total"] } });
            if (url.includes("/rows")) return Promise.resolve({ rows: [], total: 0 });
            return Promise.resolve([]);
          };
          const post = (url, body) => {
            window.__posts.push([url, body]);
            if (url === "/api/datasets/sql") {
              if (location.search.includes("err=1"))
                return Promise.resolve({ ok: false, error: 'column "totl" does not exist\\nLINE 1: SELECT totl FROM pcb_sensata' });
              return Promise.resolve({ ok: true, columns: ["ts", "zone", "total"], rows: ROWS, truncated: true });
            }
            return Promise.resolve({});
          };
          export const api = { get, post, put: post, del: post };
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
      if (location.search.includes("dark=1")) document.documentElement.classList.add("dark");
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
  const open = async (w, q = "") => {
    const page = await browser.newPage({ viewport: { width: w, height: 860 } });
    const errs = [];
    page.on("pageerror", (e) => errs.push(e.message));
    await page.goto(url + q);
    await page.waitForSelector(".db-tabs", { timeout: 8000 });
    await page.click(".db-tabs .db-tab:has-text(\"SQL Playground\")");
    await page.waitForTimeout(500);
    if (!(await page.$("[data-sql-playground]"))) {
      console.error("playground did not render:", errs.join(" | "), await page.evaluate(() => [document.querySelectorAll(".db-tab").length, [...document.querySelectorAll(".db-tab")].map((t) => t.textContent).join("/"), !!document.querySelector(".db-sql")]));
    }
    await page.waitForSelector("[data-sql-playground]", { timeout: 8000 });
    await page.waitForTimeout(300);
    return { page, errs };
  };
  const shots = process.env.SQLPG_SHOTS;
  const shot = async (page, name) => { if (shots) await page.screenshot({ path: join(shots, name) }); };

  let { page, errs } = await open(1280);
  check("2. in Chrome: the playground renders with no page error, empty state showing",
        !errs.length && !!(await page.$("[data-sql-empty]")), errs.join(" | "));
  await shot(page, "sqlpg-light-1280-empty.png");
  await page.evaluate(() => { const t = document.querySelector(".db-sql-text");
    t.focus(); t.setSelectionRange(t.value.length, t.value.length); });
  await page.click("[data-sql-insert-period]");
  await page.waitForTimeout(150);
  const val = await page.$eval(".db-sql-text", (t) => t.value);
  check("...Insert period filter puts the exact text in at the cursor",
        val.endsWith("ts >= %(from)s AND ts < %(to)s"), JSON.stringify(val));
  const h0 = await page.$eval(".db-sql-editor", (e) => e.getBoundingClientRect().height);
  const sp = await (await page.$("[data-sql-split]")).boundingBox();
  await page.mouse.move(sp.x + sp.width / 2, sp.y + 4);
  await page.mouse.down();
  await page.mouse.move(sp.x + sp.width / 2, sp.y + 84, { steps: 4 });
  await page.mouse.up();
  const h1 = await page.$eval(".db-sql-editor", (e) => e.getBoundingClientRect().height);
  check("...dragging the handle makes the editor taller", h1 > h0 + 60, `${h0} -> ${h1}`);
  await page.click(".db-sql-run");
  await page.waitForTimeout(400);
  const out = await page.evaluate(() => ({
    head: document.querySelector("[data-sql-outcome]")?.textContent,
    num: getComputedStyle(document.querySelector(".db-sql-grid td.db-num")).textAlign,
    sticky: getComputedStyle(document.querySelector(".db-sql-grid th")).position,
    body: window.__posts.find(([u]) => u === "/api/datasets/sql")?.[1] }));
  check("...a run shows the rows, the time and 'Showing the first 500 rows'; numbers right-aligned, header sticky",
        /500 rows/.test(out.head) && /Showing the first 500 rows/.test(out.head)
        && out.num === "right" && out.sticky === "sticky", JSON.stringify(out));
  check("...and sent the same body as before",
        out.body && Object.keys(out.body).join() === "sql,period,from,to" && out.body.period === "today",
        JSON.stringify(out.body));
  await shot(page, "sqlpg-light-1280-results.png");
  const before = !!(await page.$("[data-sql-snips]"));
  await page.click(".db-sql-snip-toggle");
  await page.waitForTimeout(150);
  check("...the Snippets panel is open beside a wide editor and the toggle closes it",
        before && !(await page.$("[data-sql-snips]")));
  await page.close();

  ({ page, errs } = await open(1280, "?err=1"));
  await page.click(".db-sql-run");
  await page.waitForTimeout(400);
  const err = await page.evaluate(() => ({
    panel: document.querySelector("[data-sql-error]")?.textContent,
    color: getComputedStyle(document.querySelector(".db-sql-msg-title")).color }));
  check("...an error is shown in the red panel with the database's own words",
        /The query did not run/.test(err.panel || "") && /column "totl" does not exist/.test(err.panel || ""),
        JSON.stringify(err));
  await shot(page, "sqlpg-light-1280-error.png");
  await page.close();

  ({ page, errs } = await open(1024));
  check("...at 1024 px the Snippets panel starts closed (a toggle)", !(await page.$("[data-sql-snips]")));
  await page.click(".db-sql-snip-toggle");
  await page.waitForTimeout(150);
  const drawer = await page.$eval("[data-sql-snips]", (e) => getComputedStyle(e).position);
  check("...and opens as a drawer over the work area", drawer === "absolute", drawer);
  await shot(page, "sqlpg-light-1024-snippets.png");
  await page.close();

  ({ page, errs } = await open(390));
  const fits = await page.evaluate(() => {
    const els = [...document.querySelectorAll(".db-sql-bar, .db-sql-strip, .db-sql-tables, .db-sql-status")];
    return els.every((e) => e.scrollWidth <= e.clientWidth + 2);
  });
  check("...at 390 px the bars wrap instead of running off the side", fits);
  await shot(page, "sqlpg-light-390.png");
  await page.close();

  // 3. Follow Analytics range (2026-10-14): the range the Analytics page
  //    saved in this tab, read at Run, sent as period custom.
  ({ page, errs } = await open(1280));
  const sent = async () => page.evaluate(() => window.__posts
    .filter(([u]) => u === "/api/datasets/sql").at(-1)?.[1]);
  await page.selectOption(".db-sql-period select", "today");
  await page.click(".db-sql-run");
  await page.waitForTimeout(300);
  const today = await sent();
  check("3. an existing choice sends what it sent before (period today with the date fields)",
        today && today.period === "today" && Object.keys(today).join() === "sql,period,from,to",
        JSON.stringify(today));
  const unset = await page.$eval("[data-sql-follow]", (o) => o.textContent);
  check("...with no Analytics range yet, the option reads Follow Analytics range (today)",
        unset === "Follow Analytics range (today)", unset);
  await page.evaluate(() => sessionStorage.setItem("analytics.range",
    JSON.stringify({ start: "2026-10-12", end: "2026-10-14" })));
  await page.selectOption(".db-sql-period select", "analytics");
  await page.waitForTimeout(150);
  const words = await page.$eval("[data-sql-follow]", (o) => o.textContent);
  await page.click(".db-sql-run");
  await page.waitForTimeout(300);
  const follow = await sent();
  check("...the Analytics range shows by the option and is sent as period custom with its dates",
        words === "Follow Analytics range: 12 Oct 2026 - 14 Oct 2026"
        && follow.period === "custom" && follow.from === "2026-10-12" && follow.to === "2026-10-14",
        JSON.stringify({ words, follow }));
  await page.evaluate(() => sessionStorage.setItem("analytics.range",
    JSON.stringify({ start: "2026-10-01", end: "2026-10-31" })));
  await page.click(".db-sql-run");
  await page.waitForTimeout(300);
  const again = await sent();
  check("...a range changed meanwhile is the one used at the next Run",
        again.from === "2026-10-01" && again.to === "2026-10-31", JSON.stringify(again));
  await shot(page, "sqlpg-light-1280-follow.png");
  await page.close();

  if (shots) {
    for (const [w, q, name] of [[1280, "?dark=1", "sqlpg-dark-1280-results.png"],
                                [1280, "?dark=1&err=1", "sqlpg-dark-1280-error.png"],
                                [1024, "?dark=1", "sqlpg-dark-1024.png"],
                                [390, "?dark=1", "sqlpg-dark-390.png"]]) {
      ({ page } = await open(w, q));
      if (w !== 390) { await page.click(".db-sql-run"); await page.waitForTimeout(400); }
      await shot(page, name);
      await page.close();
    }
  }
  await browser.close();
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
