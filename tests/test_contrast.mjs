// Text stays readable in BOTH themes.
//
// A colour written as a token follows the theme; a colour written as #f59e0b
// does not. The second kind is what went invisible: an amber warning at 2.0:1
// on a white card, a red error at 3.1:1 on a black one, a port caption at
// 1.4:1 in daylight. This computes the WCAG ratio of every literal text colour
// in the stylesheets and the inline styles against the surfaces each theme
// actually paints, and fails on anything under 4.5:1.
//
// Colours paired with their own background (a chip that fills itself, text on
// a video overlay) are exempt: they are read against a ground this file can
// see is literal too. The exemptions are listed by name, so adding one is a
// decision somebody makes on purpose.
//
//     node tests/test_contrast.mjs
import fs from "node:fs";
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");
const AA = 4.5;
let bad = 0;

function check(label, ok, detail = "") {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : "\n      " + detail));
  if (!ok) bad += 1;
}

// The surfaces a page paints, per theme: --bg, --surface, --surface3.
const LIGHT = ["#f7f7f7", "#ffffff", "#f0f0f0"];
const DARK = ["#0a0a0a", "#171717", "#262626"];

function parse(value) {
  const v = String(value).trim().toLowerCase();
  let m = /^#([0-9a-f]{3})$/.exec(v);
  if (m) return parse("#" + [...m[1]].map((c) => c + c).join(""));
  m = /^#([0-9a-f]{6})$/.exec(v);
  if (m) {
    const n = parseInt(m[1], 16);
    return [n >> 16, (n >> 8) & 255, n & 255, 1];
  }
  m = /^rgba?\(([^)]+)\)$/.exec(v);
  if (m) {
    const p = m[1].split(/[,/\s]+/).filter(Boolean).map(Number);
    if (p.slice(0, 3).some(Number.isNaN)) return null;
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
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

function ratio(fg, bgHex) {
  const bg = parse(bgHex);
  const over = [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3]));
  const a = lum(over), b = lum(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

const worst = (fg, surfaces) => Math.min(...surfaces.map((s) => ratio(fg, s)));

// A literal colour read against a ground this file cannot see -- a video
// frame, a lightbox overlay, printed paper, a card painting its own accent --
// is exempted by writing `contrast-ok:` and the reason at the place it is
// used. A marker in the source rather than a list in the test: the next person
// to touch that rule reads the reason there, and a rule that moves takes its
// exemption with it.
const MARK = "contrast-ok:";

function blockAt(text, pos) {
  const a = text.lastIndexOf("{", pos), b = text.indexOf("}", pos);
  return a >= 0 && b > 0 ? text.slice(a, b) : "";
}
function selectorAt(text, pos) {
  const a = text.lastIndexOf("{", pos);
  const prev = Math.max(text.lastIndexOf("}", a), text.lastIndexOf("{", a));
  return text.slice(prev + 1, a).replace(/\/\*[\s\S]*?\*\//g, "").trim()
             .split("\n").pop().trim();
}

const offenders = [];
for (const rel of ["theme.css", "index.css"]) {
  const text = readText(join(REPO, "frontend", "src", rel));
  const re = /(?:^|[;{\s])color\s*:\s*([^;}!]+)/gim;
  let m;
  while ((m = re.exec(text)) !== null) {
    const fg = parse(m[1].trim());
    if (!fg) continue;                       // var(), inherit, currentColor
    const blk = blockAt(text, m.index);
    if (/background(-color)?\s*:\s*(?!transparent|none)/i.test(blk)) continue;
    // The marker may sit in the rule or in the comment just above it.
    const near = text.slice(Math.max(0, m.index - 400), m.index + 200);
    if (blk.includes(MARK) || near.includes(MARK)) continue;
    const key = `${rel}`;
    const line = text.slice(0, m.index).split("\n").length;
    const l = worst(fg, LIGHT), d = worst(fg, DARK);
    if (l < AA || d < AA) {
      offenders.push(`${rel}:${line} ${m[1].trim()} `
        + `(light ${l.toFixed(2)}, dark ${d.toFixed(2)}) `
        + `-- ${selectorAt(text, m.index) || key}`);
    }
  }
}

check("no stylesheet paints text a literal colour that fails one of the themes",
      offenders.length === 0, offenders.join("\n      "));

// -- the tokens themselves ----------------------------------------------------
const theme = readText(join(REPO, "frontend", "src", "theme.css"));
const index = readText(join(REPO, "frontend", "src", "index.css"));

function tokens(text, blockStart) {
  const start = text.indexOf(blockStart);
  const body = text.slice(start, text.indexOf("\n}", start));
  const out = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}
const light = tokens(theme, ":root {");
const dark = tokens(index, ".dark {\n  --bg: #0a0a0a;");

for (const name of ["ok-text", "alert-text", "warn-text"]) {
  const lv = parse(light[name]), dv = parse(dark[name]);
  check(`--${name} clears AA in light`, !!lv && worst(lv, LIGHT) >= AA,
        `${light[name]} scores ${lv ? worst(lv, LIGHT).toFixed(2) : "?"}`);
  check(`--${name} clears AA in dark`, !!dv && worst(dv, DARK) >= AA,
        `${dark[name]} scores ${dv ? worst(dv, DARK).toFixed(2) : "?"}`);
}
check("...and the two themes do NOT share those values",
      ["ok-text", "alert-text", "warn-text"].every((n) => light[n] !== dark[n]),
      "one value cannot clear 4.5:1 on both a white card and a black one");
// --brand-accent, NOT --accent: the second name means shadcn's accent
// BACKGROUND inside .cy-app, so a text colour built on it goes dark-on-dark
// exactly where the canvas lives.
check("the accent text colour is derived from the BRAND accent, not fixed",
      /color-mix\(in srgb, var\(--brand-accent/.test(light["accent-text"] || "")
      && /color-mix\(in srgb, var\(--brand-accent/.test(dark["accent-text"] || ""),
      "a rebranded deployment would otherwise get somebody else's teal, and "
      + "--accent would make it a background colour on half the pages");
check("the status FILL colours stay put across themes",
      !!light.ok && !!light.alert && !!light.warn
      && !("ok" in dark) && !("alert" in dark) && !("warn" in dark),
      "a dot, a meter and a LIVE badge are read against their own shape, and "
      + "the badge carries white text -- lightening them for dark mode would "
      + "put white on salmon");
check("the text classes paint from the text tokens",
      theme.includes(".c-danger { color: var(--alert-text); }")
      && theme.includes(".c-warn { color: var(--warn-text); }")
      && theme.includes(".c-ok { color: var(--ok-text); }")
      && theme.includes(".c-accent { color: var(--accent-text); }"));

// -- inline styles ------------------------------------------------------------
const files = [];
(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) { if (e.name !== "node_modules") walk(join(dir, e.name)); }
    else if (/\.(jsx|js)$/.test(e.name)) files.push(join(dir, e.name));
  }
})(join(REPO, "frontend", "src"));

const inline = [];
for (const path of files) {
  const text = readText(path);
  for (const m of text.matchAll(/color:\s*["']([^"']+)["']/g)) {
    const fg = parse(m[1]);
    if (!fg) continue;
    const ctx = text.slice(Math.max(0, m.index - 260), m.index + 160);
    if (!ctx.includes("style=") && !ctx.includes("style:")) continue;  // node palette data
    if (/back(ground|Ground)/.test(ctx)) continue;                     // paints its own
    // A wider window than the background check above: the marker is a comment
    // over the ELEMENT, and a style object can be a dozen lines long.
    const marked = text.slice(Math.max(0, m.index - 900), m.index + 300);
    if (marked.includes(MARK)) continue;
    if (worst(fg, LIGHT) >= AA && worst(fg, DARK) >= AA) continue;
    inline.push(`${path.slice(REPO.length + 1)}:`
      + `${text.slice(0, m.index).split("\n").length} ${m[1]}`);
  }
}
check("no inline style paints text a literal colour that fails one of the themes",
      inline.length === 0, inline.join("\n      "));

// -- the two names that mean two different things -----------------------------
// `--accent` is the product cyan out on the legacy pages and shadcn's accent
// BACKGROUND (near-white in light, near-black in dark) inside .cy-app;
// `--muted` is a grey for words out here and a muted BACKGROUND in there. Text
// painted with either reads correctly in one shell and vanishes in the other --
// which is what turned every value on the workflow canvas into dark grey on a
// dark node. --accent-text and --muted-text resolve the same in both.
const shellTrap = [];
for (const path of files) {
  const text = readText(path);
  for (const m of text.matchAll(/color:[ ]*["']var[(]--(accent|muted)[,)]/g)) {
    shellTrap.push(`${path.slice(REPO.length + 1)}:`
      + `${text.slice(0, m.index).split("\n").length} --${m[1]}`);
  }
}
check("text is never painted with --accent or --muted directly",
      shellTrap.length === 0,
      "use --accent-text / --muted-text: inside .cy-app those two names mean "
      + "BACKGROUNDS" + "\n      " + shellTrap.join("\n      "));

if (bad) {
  console.log(`\n${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
