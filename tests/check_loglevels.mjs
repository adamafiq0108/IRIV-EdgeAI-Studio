/**
 * A log level is readable at a glance, and stays readable.
 *
 * The Logs page is the page somebody opens when something is already wrong,
 * and the question they are asking is "which of these 200 lines matters".
 * Before this, every level was one of two inline literals -- #dc2626 for both
 * ERROR and CRITICAL, so the two most important levels on the page were the
 * same colour -- and nothing but colour carried the meaning.
 *
 * What this pins, in order of how easy it is to undo by accident:
 *
 *  1. CONTRAST. Every level word clears WCAG AA (4.5:1) against the ground a
 *     log row actually has, in BOTH themes, with the row's own tint
 *     composited underneath. Recomputed here from the stylesheet rather than
 *     trusted from the comment, so retinting a token re-runs the measurement.
 *
 *  2. NOT HUE ALONE. Roughly one man in twelve cannot separate red from green
 *     reliably. CRITICAL / ERROR / WARNING must differ on axes that survive
 *     greyscale: a filled chip vs an outlined one vs none, a solid rule vs a
 *     dashed one vs none, and three shapes. ERROR vs CRITICAL is the pair
 *     that matters most and the pair most designs collapse.
 *
 *  3. ONE SHOUT. Exactly one level gets a filled chip. If four of the five
 *     are loud then none of them is.
 *
 *  4. THE SHELL TRAP. Inside .cy-app, --muted and --accent are BACKGROUND
 *     colours. Text painted with either is near-black on near-black in one
 *     theme, which is how a page in this repo shipped unreadable.
 *
 *     node tests/check_loglevels.mjs
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
const tag = read("frontend", "src", "components", "LogLevel.jsx");
const page = read("frontend", "src", "pages", "Logs.jsx");
// The table itself is shared by all three Diagnostics pages now, so the row
// treatment is applied THERE and each page hands it a level.
const table = read("frontend", "src", "components", "LogTable.jsx");
const events = read("frontend", "src", "pages", "EventTable.jsx");
const audit = read("frontend", "src", "pages", "Audit.jsx");

// -- colour maths -------------------------------------------------------------
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
    if (p.slice(0, 3).some(Number.isNaN)) return null;
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  }
  // The shadcn greys are written in oklch with zero chroma, which is a plain
  // grey -- convert the lightness rather than give up, because
  // --muted-foreground IS the DEBUG colour and it is the tightest one here.
  m = /^oklch\(\s*([0-9.]+)\s+0\s+0\s*\)$/.exec(s);
  if (m) {
    const l = Number(m[1]);
    const lin = l ** 3;                      // OKLab grey -> linear sRGB
    const c = lin <= 0.0031308 ? lin * 12.92
                               : 1.055 * lin ** (1 / 2.4) - 0.055;
    const v8 = Math.round(Math.min(1, Math.max(0, c)) * 255);
    return [v8, v8, v8, 1];
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
const over = (fg, bg) =>
  [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat([1]);
function ratio(fgSpec, ...groundSpecs) {
  let bg = parse(groundSpecs[0]);
  for (const g of groundSpecs.slice(1)) bg = over(parse(g), bg);
  const fg = over(parse(fgSpec), bg);
  const a = lum(fg), b = lum(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// -- the values, read out of the stylesheets ----------------------------------
//
// Read, not hard-coded: the point of the check is that retinting --alert-text
// or the row tint re-runs the measurement instead of silently invalidating a
// number written in a comment.
function block(text, selector) {
  const i = text.indexOf(selector + " {");
  if (i < 0) return null;
  return text.slice(i, text.indexOf("}", i));
}
// index.css carries TWO `.dark` blocks: shadcn's own, and the legacy palette
// the master pages read. The legacy one is the one that defines --alert-text,
// so it is anchored by its first line the way test_contrast.mjs anchors it.
const LEGACY_DARK = ".dark {\n  --bg: #0a0a0a;";
function legacyDarkToken(text, name) {
  const i = text.indexOf(LEGACY_DARK);
  if (i < 0) return null;
  const body = text.slice(i, text.indexOf("\n}", i));
  const m = new RegExp(`--${name}:\\s*([^;]+);`).exec(body);
  return m ? m[1].trim() : null;
}
function decl(text, selector, prop) {
  const b = block(text, selector);
  if (!b) return null;
  const m = new RegExp(`(?:^|[;{\\s])${prop}\\s*:\\s*([^;}]+)`, "i").exec(b);
  return m ? m[1].trim() : null;
}
function token(text, selector, name) {
  const b = block(text, selector);
  if (!b) return null;
  const m = new RegExp(`--${name}:\\s*([^;]+);`).exec(b);
  return m ? m[1].trim() : null;
}

// A log row's ONE ground is the .table-card, which paints --surface.
const SURFACE = { light: "#ffffff", dark: "#171717" };

const alertFill = token(css, ":root", "alert");
const T = {
  light: {
    alert: alertFill,
    alertText: token(css, ":root", "alert-text"),
    warnText: token(css, ":root", "warn-text"),
    text2: token(css, ":root", "text2"),
    mutedFg: token(index, ".cy-app", "muted-foreground"),
    critTint: token(css, ".log-row", "log-crit-tint"),
    errTint: token(css, ".log-row", "log-err-tint"),
  },
  dark: {
    alert: alertFill,           // a FILL keeps one value across both themes
    alertText: legacyDarkToken(index, "alert-text"),
    warnText: legacyDarkToken(index, "warn-text"),
    text2: legacyDarkToken(index, "text2"),
    mutedFg: token(index, ".cy-app.dark, .dark .cy-app", "muted-foreground")
             || token(index, ".dark .cy-app", "muted-foreground"),
    critTint: token(css, ".dark .log-row", "log-crit-tint"),
    errTint: token(css, ".dark .log-row", "log-err-tint"),
  },
};

check("the row tints are defined for both themes",
      !!T.light.critTint && !!T.light.errTint && !!T.dark.critTint && !!T.dark.errTint,
      "a tint that only exists in one theme is a row that vanishes in the other");
check("...and the two themes do NOT share a tint value",
      T.light.critTint !== T.dark.critTint && T.light.errTint !== T.dark.errTint,
      "a red mixed for a white card disappears on #171717 at the same alpha");
check("the CRITICAL chip is painted with the FILL token, not the text one",
      decl(css, ".log-level-critical", "background") === "var(--alert)",
      "--alert keeps one value in both themes because it is read against its "
      + "own shape and carries white text; --alert-text flips and would not");

for (const theme of ["light", "dark"]) {
  const t = T[theme];
  const ground = SURFACE[theme];
  const rows = [
    ["CRITICAL", "#ffffff", [t.alert]],
    ["ERROR", t.alertText, [ground, t.errTint]],
    ["WARNING", t.warnText, [ground]],
    ["INFO", t.text2, [ground]],
    ["DEBUG", t.mutedFg, [ground]],
  ];
  for (const [level, fg, grounds] of rows) {
    const r = fg && grounds.every(Boolean) ? ratio(fg, ...grounds) : 0;
    check(`${theme}: ${level} clears AA (${r.toFixed(2)}:1)`, r >= AA,
          `${fg} on ${grounds.join(" over ")} scores ${r.toFixed(2)}, AA is ${AA}`);
  }
}

// -- not hue alone ------------------------------------------------------------
check("only ONE level gets a filled chip",
      /\.log-level-critical\s*\{[^}]*background:\s*var\(--alert\)/.test(css)
      && !/\.log-level-(error|warning|info|debug)\s*\{[^}]*background:/.test(css),
      "if four of the five shout, none of them does -- and a filled chip is "
      + "the cue that still works printed in black and white");
check("ERROR is the OUTLINED one, which is how it differs from CRITICAL "
      + "without using hue",
      /\.log-level-error\s*\{[^}]*border-color:/.test(css),
      "red text on red text is the pair every design collapses");
check("WARNING's rule is DASHED, CRITICAL's and ERROR's are solid",
      /\.log-row-warning\s*>\s*td:first-child\s*\{[^}]*border-left-style:\s*dashed/.test(css)
      && /\.log-row-critical\s*>\s*td:first-child\s*\{[^}]*border-left-color:/.test(css)
      && /\.log-row-error\s*>\s*td:first-child\s*\{[^}]*border-left-color:/.test(css),
      "solid / dashed / none survives greyscale; red / amber does not");
check("every row reserves the rule, so a quiet line starts at the same x",
      /\.log-row\s*>\s*td:first-child\s*\{[^}]*border-left:\s*4px solid transparent/.test(css),
      "otherwise the whole table shifts 4px whenever an error scrolls in");
check("the three loud levels each carry a SHAPE as well as a colour",
      ["CRITICAL", "ERROR", "WARNING"].every((l) =>
        new RegExp(`${l}:\\s*"\\\\u25`).test(tag)),
      "square / diamond / triangle -- three silhouettes, not three hues");
check("...and the shape is hidden from screen readers",
      /log-level-mark[^>]*aria-hidden="true"/.test(tag),
      "the level word is already there to be read; the glyph is for the eye");

// -- the quiet end ------------------------------------------------------------
check("INFO and DEBUG recede, and DEBUG furthest",
      /\.log-level-info\s*\{[^}]*color:\s*var\(--text2\)/.test(css)
      && /\.log-level-debug\s*\{[^}]*color:\s*var\(--muted-foreground/.test(css),
      "most of a log ring is INFO and INFO is not news");
check("...and neither is painted with --muted or --accent",
      !/\.log-level-(info|debug)\s*\{[^}]*color:\s*var\(--(muted|accent)[,)]/.test(css),
      "inside the hub shell (.cy-app) those two names are BACKGROUND colours "
      + "-- near-black text on a near-black card is how this shipped before");

// -- the page uses it, and nothing else ---------------------------------------
check("the Logs page renders the shared tag, not a local colour map",
      page.includes("LogLevelTag") && page.includes("logRowClass")
      && page.includes('from "../components/LogLevel.jsx"'));
check("...and the old inline literals are gone",
      !page.includes("LEVEL_CLASS") && !/var\(--danger/.test(page),
      "--danger is not a token this repo defines, so every one of those "
      + "resolved to the hard-coded #dc2626 fallback in both themes -- which "
      + "is also why ERROR and CRITICAL were the same colour");
// The FILL family (--ok / --alert / --warn) keeps ONE value across both
// themes, because it is read against its own shape. As a word on a page it is
// therefore wrong in one theme by construction: --warn is #b45309, which is
// 2.9:1 on the dark card. The *-text family is the one that flips.
const fillAsText = [...page.matchAll(/color:\s*"var\(--(ok|alert|warn|danger)[,)]/g)]
  .map((m) => `--${m[1]} at char ${m.index}`);
check("no text on this page is painted with a status FILL token",
      fillAsText.length === 0,
      "use --ok-text / --alert-text / --warn-text, which flip per theme\n      "
      + fillAsText.join("\n      "));
check("the row treatment reaches the row, not just the cell",
      /<tr className=\{rowClass \? rowClass\(row\)/.test(table),
      "an operator skimming 200 lines is not reading the level column");
check("...and every page hands it the shared class function",
      [page, events, audit].every((t) => /rowClass=\{\(r\) => logRowClass\(/.test(t)),
      "a page that builds its own row class is a page whose ERROR rows look "
      + "different from the ERROR rows on the page beside it");

// -- one vocabulary across the three pages ------------------------------------
check("the event store's severity words map ONTO the five levels",
      /export function severityLevel/.test(tag)
      && events.includes("severityLevel(") && !/badge-warn|tone="warn"/.test(events),
      "info / warn / crit are the same three degrees as INFO / WARNING / "
      + "CRITICAL; a second colour scheme for them is how amber comes to mean "
      + "two things in one product");
check("...and the chip still shows the writer's own word",
      /text=\{r\.severity\}/.test(events),
      "an operator who filtered for 'crit' must not be shown 'CRITICAL' - the "
      + "treatment is shared, the vocabulary of the store is not renamed");
check("the audit log's failed rows carry the SAME level treatment",
      /logRowClass\(rowLevel\(r\)\)/.test(audit) && /"ERROR"/.test(audit),
      "a failed sign-in is the row somebody came to that page for");

if (bad) {
  console.log(`\n${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
