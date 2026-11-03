// What an operator reads before deciding to take an update.
//
// The notes come from a git tag, written by a person, and they used to be
// shown as the raw block of text they arrived as -- which read as a commit
// message, because that is what it was. Somebody deciding whether to update a
// box at a customer site is not reading a commit message.
//
// So they are turned into entries here. Pinned by running the parser, because
// the input is human-written and the interesting cases are all the ways people
// mark a bullet.
//
//   node tests/check_releasenotes.mjs
import { createRequire } from "node:module";
import { readText } from "./_text.mjs";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".relnotes-test.cjs");

let bad = 0;
const check = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) {
    console.log(`      got    ${JSON.stringify(got)}`);
    console.log(`      wanted ${JSON.stringify(want)}`);
    bad += 1;
  }
};

await build({
  stdin: {
    contents: `export { releaseNoteItems, releaseNoteGroups, roman, noteTag, tagClass } from "${
      join(FRONT, "src/components/releaseNotes.js").replace(/\\/g, "/")}";`,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"production"' },
});
const { releaseNoteItems, releaseNoteGroups, roman, noteTag, tagClass } = req(OUT);

// -- the format the notes are written in ----------------------------------------
check("one change per line becomes one entry each",
      releaseNoteItems("Analytics: rows follow the window\nDatabase: a board stores rows"),
      ["Analytics: rows follow the window", "Database: a board stores rows"]);

// -- however the author marked the bullet ----------------------------------------
// The list draws its own marker, so a typed one would show as a second bullet
// beside it.
for (const [mark, name] of [["- ", "a hyphen"], ["* ", "an asterisk"],
                            ["• ", "a bullet character"],
                            ["  -   ", "a hyphen with stray spacing"]]) {
  check(`${name} is stripped, not shown twice`,
        releaseNoteItems(`${mark}Sidebar: the page reflows`),
        ["Sidebar: the page reflows"]);
}

// -- what is not a change ---------------------------------------------------------
check("git trailers are dropped",
      releaseNoteItems("Network: iriv.local resolves\nCo-Authored-By: someone <a@b.c>\nSigned-off-by: x"),
      ["Network: iriv.local resolves"]);
check("a conventional-commit subject is dropped: it restates the version the "
      + "heading already shows",
      releaseNoteItems("chore: version 1.0.6\nNetwork: iriv.local resolves"),
      ["Network: iriv.local resolves"]);
check("...only at the top, never a real entry further down",
      releaseNoteItems("Network: iriv.local resolves\nfix: something an author wrote"),
      ["Network: iriv.local resolves", "fix: something an author wrote"]);
check("a rule of dashes is a separator somebody typed, not a change",
      releaseNoteItems("Analytics: rows follow the window\n------\nDatabase: rows"),
      ["Analytics: rows follow the window", "Database: rows"]);
check("blank lines do not become empty bullets",
      releaseNoteItems("Analytics: one\n\n\nDatabase: two"),
      ["Analytics: one", "Database: two"]);

// -- nothing at all ---------------------------------------------------------------
for (const empty of ["", "   \n  \n", null, undefined]) {
  check(`${JSON.stringify(empty)} yields no list, so the section hides`,
        releaseNoteItems(empty), []);
}
check("a release whose notes are ONLY a version subject shows nothing",
      releaseNoteItems("chore: version 1.0.6"), []);

// -- and the text itself is left alone ---------------------------------------------
// Only the leading marker is touched. A dash inside a sentence is the author's.
check("punctuation inside an entry survives",
      releaseNoteItems("Update: the board can be reached at iriv.local:5100"),
      ["Update: the board can be reached at iriv.local:5100"]);

// -- grouped by area (v1.0.7 onwards) ---------------------------------------------
// The owner writes a long release as headings with the changes under them.
// Both shapes must render the same, and the flat list must still read
// "Area: change" so nothing that consumed it changes.
const grouped = "Workflow:\n- a line has its own threshold\n-  two spaces before this one\n\n"
  + "Settings: a box in customer mode hides the developer's panels\n"
  + "Security:\n- first run replaces the shipped password\n- oversized usernames are refused";
check("a heading line opens a group and the bullets under it fill it",
      releaseNoteGroups(grouped),
      [{ area: "Workflow", items: ["a line has its own threshold", "two spaces before this one"] },
       { area: "Settings", items: ["a box in customer mode hides the developer's panels"] },
       { area: "Security", items: ["first run replaces the shipped password", "oversized usernames are refused"] }]);
check("...and the flat list reads Area: change for every entry, either shape",
      releaseNoteItems(grouped),
      ["Workflow: a line has its own threshold", "Workflow: two spaces before this one",
       "Settings: a box in customer mode hides the developer's panels",
       "Security: first run replaces the shipped password", "Security: oversized usernames are refused"]);
check("consecutive one-line entries about one area fold into one group",
      releaseNoteGroups("Logs: one\nLogs: two\nUpdate: three"),
      [{ area: "Logs", items: ["one", "two"] }, { area: "Update", items: ["three"] }]);
check("a colon inside a bullet under a heading is the author's punctuation, not a new area",
      releaseNoteGroups("Dashboard:\n- a tile shows YES / NO, ON / OFF: or the word itself"),
      [{ area: "Dashboard", items: ["a tile shows YES / NO, ON / OFF: or the word itself"] }]);
check("a bullet with no heading and no area is kept bare",
      releaseNoteGroups("- something changed"),
      [{ area: "", items: ["something changed"] }]);
check("a version subject at the top is still dropped ahead of a heading",
      releaseNoteGroups("chore: version 1.0.7\nNetwork:\n- eth1 lights up"),
      [{ area: "Network", items: ["eth1 lights up"] }]);

// -- the status outline (Tok Aba, 2026-09-25): numbered sections -------------------
check("a Roman-numbered section heading opens a group under its full name",
      releaseNoteGroups("I. Introduction:\n- version 1.0.7 is out\nII. Accomplishments:\n- Workflow: a line has its own threshold"),
      [{ area: "I. Introduction", items: ["version 1.0.7 is out"] },
       { area: "II. Accomplishments", items: ["Workflow: a line has its own threshold"] }]);
check("...and a bullet's own Area: prefix stays part of the line under that section",
      releaseNoteItems("II. Accomplishments:\n- Workflow: a line has its own threshold"),
      ["II. Accomplishments: Workflow: a line has its own threshold"]);

// -- the numbering is the card's, not the author's ---------------------------------
check("group headings are numbered I, II, ... by the renderer",
      [1, 2, 3, 4, 5, 9, 12].map(roman), ["I", "II", "III", "IV", "V", "IX", "XII"]);
for (const [file, what] of [["UpdatePanel.jsx", "the Update card"], ["NotificationCenter.jsx", "the Inbox dialog"]]) {
  const src = readText(join(FRONT, "src/components", file));
  check(`${what} draws roman(gi + 1) before each area heading`,
        src.includes("{roman(gi + 1)}. {g.area}"), true);
}

// -- a point's kind in braces (v1.0.8 onwards) -------------------------------------
check("a leading {TAG} is split off a point",
      noteTag("{BUG SOLVED} the gauge shows the word"), { tag: "BUG SOLVED", text: "the gauge shows the word" });
check("...case and spacing are the author's problem, not the reader's",
      noteTag("{features update}the Log Hub"), { tag: "FEATURES UPDATE", text: "the Log Hub" });
check("a point without a tag is left whole",
      noteTag("the second Ethernet port lights up"), { tag: "", text: "the second Ethernet port lights up" });
check("the four kinds have their own colour and an unknown one is plain",
      ["FEATURES UPDATE", "BUG SOLVED", "INTERFACE UPDATE", "NOTIFICATION UPDATE", "SOMETHING"].map((t) => tagClass(t) !== tagClass("SOMETHING")),
      [true, true, true, true, false]);
for (const [file, what] of [["UpdatePanel.jsx", "the Update card"], ["NotificationCenter.jsx", "the Inbox dialog"]]) {
  const src = readText(join(FRONT, "src/components", file));
  check(`${what} draws the tag as a badge before the point`, src.includes("noteTag(item)") && src.includes("tagClass(tag)"), true);
}

rmSync(OUT, { force: true });
console.log();
if (bad) { console.log(`${bad} check(s) failed`); process.exit(1); }
console.log("all good");
