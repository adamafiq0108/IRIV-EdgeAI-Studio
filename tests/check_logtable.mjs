/**
 * The three Diagnostics pages are one product.
 *
 * The Log Hub, the Events log and the Audit log are read by ONE person, on
 * one afternoon, going from page to page trying to work out what happened.
 * Before this they were three tables built three times: three row heights,
 * three date formats, three ideas of what "failed" looks like, and one of
 * them (Audit) silently dropping the only column that said what an action
 * did.
 *
 * What this pins, in order of how easy it is to undo by accident:
 *
 *  1. ONE TABLE. All three render components/LogTable.jsx, and none of them
 *     builds its own <colgroup>/<thead> again. The moment one page grows a
 *     private table, the other two start drifting from it.
 *  2. THE AUDIT FIX STAYS FIXED. detail is rendered (through the shared
 *     helper -- tests/smoke_auditdetail.py owns that half) and the page
 *     actually PAGES: offset, limit and has_more, or an admin can only ever
 *     read the newest screen of a log kept for a year.
 *  3. THE FOOTAGE LINK. The one thing the Events log has that no other page
 *     does. A standardisation that drops it makes the still that proves what
 *     happened unreachable from the row that describes it.
 *  4. EMPTY STATES STAY DIFFERENT. The one thing deliberately NOT shared:
 *     each page must say what it would show and why it is blank. "No rows" is
 *     the one message a reader cannot act on.
 *  5. CONTRAST for everything the shared table added, in BOTH themes.
 *  6. THE SHELL TRAP. Inside .cy-app, --muted and --accent are BACKGROUND
 *     colours. Text painted with either is near-black on near-black in one
 *     theme, which is how a page in this repo shipped unreadable.
 *
 *     node tests/check_logtable.mjs
 */
import { readFileSync } from "node:fs";
import { readText } from "./_text.mjs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (...p) => readText(join(ROOT, ...p));

const AA = 4.5;
let bad = 0;
const check = (label, ok, detail = "") => {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : `\n      ${detail}`));
  if (!ok) bad += 1;
};

const css = read("frontend", "src", "theme.css");
const index = read("frontend", "src", "index.css");
const table = read("frontend", "src", "components", "LogTable.jsx");
const format = read("frontend", "src", "components", "logFormat.js");
const PAGES = {
  "the Log Hub": read("frontend", "src", "pages", "Logs.jsx"),
  "the Events log": read("frontend", "src", "pages", "EventTable.jsx"),
  "the Audit log": read("frontend", "src", "pages", "Audit.jsx"),
};

// -- 1. one table -------------------------------------------------------------
for (const [name, text] of Object.entries(PAGES)) {
  check(`${name} renders the shared table`,
        /from "\.\.\/components\/LogTable\.jsx"/.test(text)
        && text.includes("<LogTable"),
        "a page with its own table is a page that drifts from the other two");
  check(`...and does not build a second one`,
        !text.includes("<colgroup") && !text.includes("<thead"),
        "the colgroup, the sticky head and the uniform row are the shared "
        + "chrome; a page that re-declares them has already forked it");
  check(`...and takes its clock from the shared formatter`,
        /from "\.\.\/components\/logFormat\.js"/.test(text)
        && !/function clock\(/.test(text),
        "three pages with three date formats read as three products");
}
check("the shared table carries the chrome it is there to carry",
      ["<colgroup", "lh-c-exp", "lh-paused-bar", "lh-detail-cell", "lh-empty",
       "aria-expanded"].every((bit) => table.includes(bit)),
      "colgroup columns, the expander, the paused bar, the details row and "
      + "the empty state are the whole point of sharing it");
check("the export buttons are one component, not three",
      /export function ExportButtons/.test(table)
      && Object.values(PAGES).every((t) => t.includes("<ExportButtons")),
      "Export CSV / Export LOG in a different order on one page is a page "
      + "somebody has to read instead of aim at");
check("both exports exist on all three pages",
      Object.values(PAGES).every((t) => /downloadCsv\(/.test(t)
                                     && /downloadLog\(/.test(t)),
      "CSV is for a spreadsheet and .log is for a ticket; a page with only "
      + "one of them sends somebody to a screenshot");

// -- the widths, and how a column leaves ---------------------------------------
check("a column's width is handed down as --lh-w, never as an inline width",
      /"--lh-w"/.test(table) && !/style=\{\{\s*width:/.test(table),
      "an inline width beats every stylesheet rule there is, and the whole "
      + "responsive collapse below works by zeroing a width from a media query");
check("...and the stylesheet reads it",
      /\.logtable \.lh-table col \{[^}]*width:\s*var\(--lh-w/.test(css));
for (const n of [1, 2, 3, 4, 5]) {
  check(`drop priority ${n} has a breakpoint`,
        new RegExp(`\\.logtable \\.lh-table col\\.lh-drop-${n} \\{ width: 0; \\}`)
          .test(css),
        "a column declared with a priority nothing acts on never leaves, and "
        + "at phone width it is the message that pays for it");
}
check("a dropped column is ZEROED, never display:none",
      !/\.lh-drop-\d[^{]*\{[^}]*display:\s*none/.test(css)
      && /td\.lh-drop-1 \{ visibility: hidden; padding: 0; \}/.test(css),
      "a display:none cell is removed from its ROW, so every cell after it "
      + "takes the width of a column it is not in -- that is how Message "
      + "ended up 19px wide at phone width");
check("the message still wraps, and the rows still relax, below 980px",
      /max-width: 980px\)[^@]*\.logtable \.lh-sub \{ display: block; \}/s.test(css)
      && /max-width: 980px\)[^@]*\.logtable \.lh-msg \{ white-space: normal; \}/s.test(css),
      "below that width there is no room left to truncate INTO, and the "
      + "small-print origin line is what a phone gets instead of the columns");

// -- 2. the audit fix ----------------------------------------------------------
const audit = PAGES["the Audit log"];
check("the Audit page renders detail in a column of its own",
      /key: "detail", label: "Message"/.test(audit)
      && /detailText\(r\.detail\)/.test(audit),
      "every row said an action happened and not what it did");
check("...through the shared helper, never raw",
      /from "\.\.\/components\/auditDetail\.js"/.test(audit)
      && !/\{\s*\w+\.detail\s*\}/.test(audit),
      "detail is `{}` for most actions, which is TRUTHY -- React refuses to "
      + "render an object and the page goes white, on the page an admin "
      + "opened BECAUSE something had already gone wrong");
check("the Audit page pages",
      /p\.set\("offset"/.test(audit) && /p\.set\("limit"/.test(audit)
      && /data\.has_more/.test(audit) && /setPage\(\(p\) => p \+ 1\)/.test(audit),
      "the server has always paged; without an offset an admin could only "
      + "ever read the newest screen of a log kept for a year");
check("...and a new query starts at the top",
      /setPage\(0\);\s*\}, \[filters\.action, filters\.username, size\]/.test(audit),
      "page 3 of the old query is not page 3 of the new one");
check("the size picker offers nothing above the server's ceiling",
      (audit.match(/SIZES = \[([^\]]*)\]/)?.[1] || "")
        .split(",").map(Number).every((n) => n > 0 && n <= 1000),
      "core/routes/audit.py clamps at 1000, and a picker that asks for more "
      + "gets silently less");
check("the Audit page says what it IS",
      /admin only/.test(audit) && /append-only/.test(audit),
      "an append-only, admin-only history that a restart does not empty is "
      + "three facts a reader needs before they trust a row on it");
check("...and offers nothing that would edit it",
      !/Clear ring|api\.post\("\/api\/audit/.test(audit),
      "a log a person can empty is not evidence of anything");
// The ACTIONS list the server ships is known to be short of several actions
// the code writes (tests/pending_auditactions.py owns that). The page must use
// what it ships rather than hard-code a list of its own, so it gets the rest
// for free the day the service is fixed.
check("the action filter comes from the server, not from a copy in the page",
      /options=\{data\.actions \|\| \[\]\}/.test(audit),
      "a second copy of the vocabulary is a second thing to forget");

// -- 3. the footage link -------------------------------------------------------
const events = PAGES["the Events log"];
check("the Events log keeps its link to the footage",
      /\/footage\//.test(events) && /snapshot_id/.test(events),
      "the still that proves what happened must stay reachable from the row "
      + "that describes it");
check("...and the link does not also open the details panel under it",
      /stopPropagation/.test(events),
      "the row is itself a button; a link inside it that bubbles opens a "
      + "panel nobody asked for and loses the click");
check("...and it is painted with --accent-text, not --accent",
      /\.logtable \.lh-link \{[^}]*color:\s*var\(--accent-text\)/.test(css)
      && !/\.logtable \.lh-link \{[^}]*color:\s*var\(--(accent|muted)[,)]/.test(css),
      "inside the hub shell (.cy-app) --accent is a BACKGROUND colour");
check("the Events log still shows the whole record",
      /Raw record/.test(events) && /JSON\.stringify\(row, null, 2\)/.test(events),
      "this store gains columns as new AI templates land, and a panel that "
      + "only shows the fields this page was written against hides the new one");

// -- 4. empty states stay different -------------------------------------------
const empties = Object.entries(PAGES).map(([name, text]) => {
  const m = /empty=\{?["{]([\s\S]*?)\}\n/.exec(text)
         || /empty=\{([\s\S]*?)\}\n/.exec(text);
  return [name, m ? m[1] : ""];
});
for (const [name, words] of empties) {
  check(`${name} says WHY it is empty`, words.length > 40 && !/no rows/i.test(words),
        "each page is empty for a different reason -- nothing has happened "
        + "yet / the event store is not running / a restart emptied the ring "
        + "-- and only the page knows which\n      got: " + words.slice(0, 80));
}
check("...and no two pages say the same thing",
      new Set(empties.map(([, w]) => w.trim())).size === empties.length);

// -- 5. colour maths -----------------------------------------------------------
function parse(v) {
  const s = String(v).trim().toLowerCase();
  let m = /^#([0-9a-f]{3})$/.exec(s);
  if (m) return parse("#" + [...m[1]].map((c) => c + c).join(""));
  m = /^#([0-9a-f]{6})$/.exec(s);
  if (m) {
    const n = parseInt(m[1], 16);
    return [n >> 16, (n >> 8) & 255, n & 255, 1];
  }
  m = /^rgba?\(([^)]+)\)$/.exec(s);
  if (m) {
    const p = m[1].split(/[,/\s]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  }
  // --accent-text is DERIVED, so it has to be resolved rather than looked up:
  // color-mix(in srgb, var(--brand-accent, #04bade) 55%, #000). The fallback
  // is the product cyan, which is what an unbranded deployment gets.
  m = /^color-mix\(in srgb,\s*var\(--brand-accent,\s*(#[0-9a-f]{3,6})\)\s*([\d.]+)%,\s*(#[0-9a-f]{3,6})\)$/
    .exec(s);
  if (m) {
    const a = parse(m[1]), b = parse(m[3]), w = Number(m[2]) / 100;
    return [0, 1, 2].map((i) => Math.round(a[i] * w + b[i] * (1 - w))).concat([1]);
  }
  return null;
}
const lum = (rgb) => {
  const ch = (x) => {
    const c = x / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2]);
};
function ratio(fgSpec, bgSpec) {
  const fg = parse(fgSpec), bg = parse(bgSpec);
  if (!fg || !bg) return 0;
  const a = lum(fg), b = lum(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
// Read out of the stylesheets, not hard-coded: retinting a token has to
// re-run the measurement rather than invalidate a number in a comment.
function block(text, selector) {
  const i = text.indexOf(selector + " {");
  return i < 0 ? null : text.slice(i, text.indexOf("}", i));
}
function token(text, selector, name) {
  const b = block(text, selector);
  if (!b) return null;
  const m = new RegExp(`--${name}:\\s*([^;]+);`).exec(b);
  return m ? m[1].trim() : null;
}
const LEGACY_DARK = ".dark {\n  --bg: #0a0a0a;";
function darkToken(name) {
  const i = index.indexOf(LEGACY_DARK);
  if (i < 0) return null;
  const body = index.slice(i, index.indexOf("\n}", i));
  const m = new RegExp(`--${name}:\\s*([^;]+);`).exec(body);
  return m ? m[1].trim() : null;
}

const T = {
  light: {
    accentText: token(css, ":root", "accent-text"),
    text2: token(css, ":root", "text2"),
    surface: token(css, ":root", "surface"),
    surface3: token(css, ":root", "surface3"),
  },
  dark: {
    accentText: darkToken("accent-text"),
    text2: darkToken("text2"),
    surface: darkToken("surface"),
    surface3: darkToken("surface3"),
  },
};
for (const theme of ["light", "dark"]) {
  const t = T[theme];
  // The footage link sits in a table row, and a row's ground is --surface.
  const link = ratio(t.accentText, t.surface);
  check(`${theme}: the footage link clears AA (${link.toFixed(2)}:1)`, link >= AA,
        `${t.accentText} on ${t.surface}`);
  // The pager strip paints --surface3, where --muted-foreground is only
  // 4.15:1 -- which is why this one line is --text2 and not the note grey.
  const pager = ratio(t.text2, t.surface3);
  check(`${theme}: the pager line clears AA (${pager.toFixed(2)}:1)`, pager >= AA,
        `${t.text2} on ${t.surface3}`);
}
check("the pager line is NOT painted with the note grey",
      /\.logtable \.lh-pager \.lh-note \{[^}]*color:\s*var\(--text2\)/.test(css),
      "--muted-foreground is 4.74:1 on --surface but only 4.15:1 on the "
      + "--surface3 this strip paints, which is under AA");

// -- 6. the shell trap ---------------------------------------------------------
const strays = [...css.matchAll(/\.logtable [^{]*\{[^}]*color:\s*var\(--(accent|muted)[,)]/g)]
  .map((m) => `--${m[1]}`);
check("nothing in the shared table paints TEXT with --accent or --muted",
      strays.length === 0,
      "inside .cy-app those two names are BACKGROUND colours -- near-black "
      + "text on a near-black card is how this shipped before\n      "
      + strays.join(", "));

// -- the formatter is importable on its own -----------------------------------
const { clock, stampText, isoDay, csvCell, toCsv } =
  await import(new URL("../frontend/src/components/logFormat.js", import.meta.url));
check("the formatter has no imports, so a plain node can test it",
      !/^import /m.test(format),
      "a helper that drags React in is a helper nothing tests");
const noon = Date.UTC(2026, 8, 23, 12, 0, 0) / 1000;
check("epoch seconds and an ISO string reach the same clock",
      clock(noon) === clock(new Date(noon * 1000).toISOString()),
      `${clock(noon)} vs ${clock(new Date(noon * 1000).toISOString())} -- the `
      + "Log Hub sends seconds and the audit store sends ISO, and a reader "
      + "comparing the two pages is comparing timestamps");
check("an unreadable stamp is a dash, never 'Invalid Date'",
      clock(null) === "-" && stampText(undefined) === "" && isoDay("") === "");
check("CSV quoting survives a comma, a quote and a newline",
      csvCell('a,b') === '"a,b"' && csvCell('say "hi"') === '"say ""hi"""'
      && csvCell("a\nb") === '"a\nb"');
check("the CSV carries a BOM, so Excel keeps the UTF-8 in a device name",
      toCsv([["A", (r) => r.a]], [{ a: "é" }]).startsWith("﻿"));

// The page's name (Tok Aba, 2026-10-14): "System Log", singular, wherever the
// operator reads it - the page heading, the hub's title and sidebar, the
// loading skeleton and the master's nav.
{
  const hub = read("frontend", "src", "components", "HubShell.jsx");
  const logs = read("frontend", "src", "pages", "Logs.jsx");
  check("the logs page is called System Log inside a system (heading, skeleton, nav) and Logs on the hub (heading, title, sidebar; Tok Aba 2026-10-14)",
        logs.includes('{hub ? "Logs" : "System Log"}')
        && hub.includes('logs: "Logs",')
        && hub.includes('{ id: "logs", label: "Logs",')
        && read("frontend", "src", "router.jsx").includes('L(<Logs />, "System Log")')
        && read("core", "services", "nav.py").includes('("logs",        "System Log",  "")'));
  check("...and no user-facing 'Log Hub' or 'System Logs' is left",
        !/>\s*Log Hub\s*</.test(logs) && !hub.includes('"Log Hub"')
        && !read("frontend", "src", "components", "RetentionPanel.jsx").includes("System Logs page"));
}
// -- each page is its own process (Tok Aba, 2026-10-14) ------------------------
const logsPage = PAGES["the Log Hub"];
const hubShell = read("frontend", "src", "components", "HubShell.jsx");
const hubPage = read("frontend", "src", "pages", "Hub.jsx");
check("the page says whose log it is: 'this hub' on the hub, 'this system' on a system",
      logsPage.includes('(hub ? "this hub" : "this system")')
      && logsPage.includes("${scope} only, since it last started")
      && logsPage.includes("This log holds only ${scope}'s ")
      && /<LogHub hub\b/.test(hubShell),
      "the subtitle or the empty text does not follow the shell");
check("Clear Log no longer promises to clear every system; the hub's says hub only",
      !logsPage.includes("from every system on this box")
      && logsPage.includes("from the hub's log only. Each system keeps its own log.")
      && logsPage.includes("from this system's log."));
// Tok Aba 2026-10-14: no log button on the hub's system cards (the system's own
// System Log is reached from inside the system).
check("the hub system cards carry no View logs button",
      !hubPage.includes('title="View logs"') && !hubPage.includes("data-view-logs")
      && !hubPage.includes("logsUrl") && !hubPage.includes("ScrollText"));

if (bad) {
  console.log(`\n${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
