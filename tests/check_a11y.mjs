/**
 * check_a11y.mjs - the accessibility facts that are worth pinning.
 *
 * Not a full audit: an audit needs a rendered page, and this suite runs on
 * source. These are the three things that (a) can be checked from the text and
 * (b) came back wrong before:
 *
 *   1. muted text passes WCAG AA against the surface it is actually on. It was
 *      #737373 on #f7f7f7 = 4.43:1, under the 4.5:1 line -- and muted is the
 *      colour every hint and explanation is written in, the words somebody
 *      reads when they are stuck.
 *   2. no bare grey is used as a text colour, because a second copy of the
 *      value is a second thing to remember to fix.
 *   3. a <div> that behaves like a button carries what a button carries, and
 *      an overlay that closes on a click also closes on Escape -- nobody can
 *      tab to a backdrop.
 *
 *     node tests/check_a11y.mjs
 */
import { readFileSync } from "node:fs";
import { readText } from "./_text.mjs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (...p) => readText(path.join(ROOT, ...p));

let bad = 0;
function check(label, ok, detail = "") {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : `\n      ${detail}`));
  if (!ok) bad += 1;
}

// -- 1. contrast -------------------------------------------------------------
function luminance(hex) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255);
  const f = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
}

const theme = read("frontend", "src", "theme.css");
const index = read("frontend", "src", "index.css");
const token = (css, name) => (css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{3,6})`)) || [])[1];

const bg = token(theme, "bg");
const muted = token(theme, "muted");
const mutedText = token(theme, "muted-text");
check("the page background and muted text are both named as hex tokens",
      Boolean(bg && muted && mutedText), `bg=${bg} muted=${muted}`);
check(`muted text passes AA on the page (${muted} on ${bg})`,
      ratio(muted, bg) >= 4.5, `${ratio(muted, bg).toFixed(2)}:1 - AA needs 4.5`);
check("...and on a white card",
      ratio(muted, "#ffffff") >= 4.5,
      `${ratio(muted, "#ffffff").toFixed(2)}:1`);
check("...and the two muted tokens agree", muted === mutedText,
      "one says text, the other is used as text - they must not drift");
check("the shadcn :root re-assertion carries the same value",
      (index.match(/--muted:\s*(#[0-9a-fA-F]{6})/) || [])[1] === muted,
      "index.css re-asserts --muted for the legacy pages; a stale copy there "
      + "is the bug this file exists to catch");

// Read the .dark BLOCK, not "anything after the first .dark": index.css has
// two of them (the shadcn oklch one and the legacy hex one) with the :root
// re-assertion in between, so a lazy match walked straight past the block it
// meant and graded the light value against the dark background.
const darkBlocks = [...index.matchAll(/\.dark\s*\{([^}]*)\}/g)].map((m) => m[1]);
const inDark = (name) => darkBlocks
  .map((b) => (b.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{3,6})`)) || [])[1])
  .filter(Boolean)
  .pop();
const darkMuted = inDark("muted");
const darkBg = inDark("bg");
check(`muted text passes AA in the dark theme (${darkMuted} on ${darkBg})`,
      ratio(darkMuted, darkBg) >= 4.5, `${ratio(darkMuted, darkBg).toFixed(2)}:1`);

// -- 2. no second copy of the value ------------------------------------------
const src = [
  "pages/Workflow.jsx", "pages/Detection.jsx", "pages/Footage.jsx",
  "pages/Database.jsx", "workflow/nodes.jsx", "workflow/BlueprintEditor.jsx",
  "components/LiveFeed.jsx", "components/ui/DayStrip.jsx",
].map((f) => [f, read("frontend", "src", ...f.split("/"))]);
const strays = src.filter(([, text]) => /color:\s*"#888"/.test(text)).map(([f]) => f);
check("no file hardcodes a grey as a TEXT colour", strays.length === 0,
      strays.join(", "));

// -- 3. keyboard -------------------------------------------------------------
const helper = read("frontend", "src", "hooks", "use-escape.js");
check("the helper gives a non-button what a button has",
      helper.includes("tabIndex: 0")
      && helper.includes('e.key === "Enter" || e.key === " "')
      && helper.includes("e.preventDefault()"),
      "Space scrolls the page by default, so a control that does not stop it "
      + "scrolls when you use it");
check("...and can leave the role alone for a table row",
      helper.includes("...(role ? { role } : {})"),
      'role="button" on a <tr> replaces the row role and the table stops '
      + "being navigable as a table");

for (const [file, what] of [
  ["pages/Detection.jsx", "the camera cards"],
  // The rows moved into the shared table when the Events and Audit logs
  // started wearing it; the keyboard handling moved with them.
  ["components/LogTable.jsx", "the log rows that expand"],
  ["workflow/BlueprintEditor.jsx", "the blueprint tiles"],
  ["workflow/ContextMenu.jsx", "the context menu rows"],
]) {
  const text = read("frontend", "src", ...file.split("/"));
  check(`${what} are reachable by keyboard`,
        text.includes("clickable(") && text.includes("use-escape.js"), file);
}

for (const [file, what] of [
  ["components/EmailFootageDialog.jsx", "the email dialog"],
  ["pages/DatasetLibrary.jsx", "the dataset preview"],
  ["pages/Footage.jsx", "the footage lightbox"],
]) {
  const text = read("frontend", "src", ...file.split("/"));
  check(`${what} closes on Escape, not only on a click`,
        text.includes("useEscape("), file);
  check(`...and says it is a dialog`, text.includes('role="dialog"'), file);
}

console.log();
if (bad) {
  console.log(`${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
