// The Analytics page's range, shared (frontend/src/lib/analyticsRange.js), and
// the SQL Playground's "Follow Analytics range" (Tok Aba, 2026-10-14).
//   1. saveAnalyticsRange / readAnalyticsRange: a saved range reads back; an
//      inverted one is put in order; nothing saved (or junk) is today, the
//      Analytics page's own opening view; junk is never written
//   2. the words: one day, or "12 Oct 2026 - 14 Oct 2026"
//   3. Analytics.jsx writes the range it SHOWS (the picked day, else the
//      span) whenever it changes - the same pair it hands the table cards as
//      "This page's range"
//   4. Database.jsx: the Period select offers it, reads it on mount, on focus
//      and at every Run, and sends it as period "custom" with the two dates;
//      every other choice sends what it sent before
//   (the request in a real browser: check_sqlplayground_layout.mjs part 3)
//
//   node tests/check_analyticsrange.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { JSDOM } = req("jsdom");
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "https://localhost/" });
global.sessionStorage = dom.window.sessionStorage;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const m = await import(pathToFileURL(join(FRONT, "src", "lib", "analyticsRange.js")).href);
const NOW = new Date(2026, 9, 14, 9, 30);

// 1 ---------------------------------------------------------------------------
sessionStorage.clear();
check("1. nothing saved: today, the Analytics page's opening view, marked unset",
      JSON.stringify(m.readAnalyticsRange(NOW)) === '{"start":"2026-10-14","end":"2026-10-14","set":false}');
m.saveAnalyticsRange({ start: "2026-10-01", end: "2026-10-31" });
check("...a saved span reads back",
      JSON.stringify(m.readAnalyticsRange(NOW)) === '{"start":"2026-10-01","end":"2026-10-31","set":true}');
m.saveAnalyticsRange({ start: "2026-10-12", end: "2026-10-12" });
check("...a picked day replaces it (the page saves on every change)",
      m.readAnalyticsRange(NOW).start === "2026-10-12" && m.readAnalyticsRange(NOW).end === "2026-10-12");
m.saveAnalyticsRange({ start: "oops", end: "2026-10-20" });
check("...junk is never written", m.readAnalyticsRange(NOW).start === "2026-10-12");
sessionStorage.setItem(m.ANALYTICS_RANGE_KEY, JSON.stringify({ start: "2026-10-20", end: "2026-10-10" }));
check("...an inverted range is put in order",
      JSON.stringify(m.readAnalyticsRange(NOW)) === '{"start":"2026-10-10","end":"2026-10-20","set":true}');
sessionStorage.setItem(m.ANALYTICS_RANGE_KEY, "{not json");
check("...an unreadable entry falls back to today", m.readAnalyticsRange(NOW).set === false);

// 2 ---------------------------------------------------------------------------
check("2. the words", m.analyticsRangeWords({ start: "2026-10-14", end: "2026-10-14" }) === "14 Oct 2026"
      && m.analyticsRangeWords({ start: "2026-10-12", end: "2026-10-14" }) === "12 Oct 2026 - 14 Oct 2026");

// 3 ---------------------------------------------------------------------------
const ana = readFileSync(join(FRONT, "src", "pages", "Analytics.jsx"), "utf8");
check("3. Analytics writes the range it shows, on every change, the pair its cards export",
      /useEffect\(\(\) => \{\s*saveAnalyticsRange\(\{ start: day \|\| start, end: day \|\| end \}\);\s*\}, \[day, start, end\]\);/.test(ana)
      && ana.includes("exportCsv={{ pageRange: { start: day || start, end: day || end } }}"));

// 4 ---------------------------------------------------------------------------
const db = readFileSync(join(FRONT, "src", "pages", "Database.jsx"), "utf8");
check("4. the Period select offers Follow Analytics range, saying the range or (today)",
      db.includes('<option value="analytics" data-sql-follow="">')
      && db.includes("`Follow Analytics range: ${followWords}`")
      && db.includes('"Follow Analytics range (today)"'));
check("...read on mount, on focus and at every Run",
      db.includes("useState(() => readAnalyticsRange())")
      && db.includes("onFocus={() => setFollow(readAnalyticsRange())}")
      && db.includes('const fresh = period === "analytics" ? readAnalyticsRange() : null;'));
check("...sent as period custom with the two dates; every other choice as before",
      db.includes('{ sql, period: "custom", from: fresh.start, to: fresh.end }')
      && db.includes(": { sql, period, from: dateFrom, to: dateTo };")
      && db.includes('api.post("/api/datasets/sql", body)'));
const console_ = readFileSync(join(ROOT, "core", "services", "sqlconsole.py"), "utf8");
check("...and the console's custom is the inclusive-end day window the cards use",
      console_.includes('"custom": None') && console_.includes("stop = b + datetime.timedelta(days=1)"));

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
