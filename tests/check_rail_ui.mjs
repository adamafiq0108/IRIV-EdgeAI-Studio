/**
 * The two property rails are one rail.
 *
 * The Workflow node inspector and the Builder's Properties panel do the same
 * job on the same narrow column: label a control, group controls into a row,
 * explain in small print. They had grown two dialects -- one with 11px
 * sentence-case labels and a 32px control height, the other with 10px
 * UPPERCASE ones and whatever height the base style gave -- and inside each,
 * spacing decided per call site: gaps of 2, 3, 4, 5, 6, 8, 10 and 14px, and
 * six font sizes between 9.5 and 13.
 *
 * This checks the standard rather than the appearance: one class holds the
 * rhythm, both rails carry it, the scale is the scale, and a status colour is
 * the theme's (which is also the only one that passes contrast).
 *
 *     node tests/check_rail_ui.mjs
 */
import { readFileSync } from "node:fs";
import { readText } from "./_text.mjs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (...p) => readText(join(ROOT, ...p));

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : `\n      ${detail}`));
  if (!ok) bad += 1;
};

const css = read("frontend", "src", "theme.css");
const wf = read("frontend", "src", "pages", "Workflow.jsx");
const shared = read("frontend", "src", "components", "widgets", "builderShared.jsx");
const analytics = read("frontend", "src", "pages", "AnalyticsBuilder.jsx");
const dash = read("frontend", "src", "pages", "Builder.jsx");
const rails = { "Workflow.jsx": wf, "builderShared.jsx": shared };

// -- 1. one rhythm, worn by both rails ---------------------------------------
check("the rail rhythm is a class of its own",
      css.includes(".rail-form { --wf-h: 32px; }"),
      "it was scoped to .wf-inspector, so the Builder rail could not have it");
for (const [name, src] of [["workflow", wf], ["analytics", analytics],
                           ["dashboard", dash]]) {
  check(`...worn by the ${name} rail`, /rail-form/.test(src));
}
check("...and the field rule covers both Field components",
      css.includes(".rail-form .wf-field, .rail-form .ui-field"),
      "the shared Field renders .ui-field and the workflow one .wf-field; "
      + "inside a rail they are the same thing and must read the same");
check("...including the labels, which were two different sizes and cases",
      /\.rail-form \.ui-field > \.ui-field-label/.test(css)
      && /text-transform: none/.test(css));

// -- 2. the scale ------------------------------------------------------------
check("spacing is a scale, not a per-call-site decision",
      ["--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-5"].every((t) => css.includes(t)));
check("...and so is the type", ["--fs-xs", "--fs-sm", "--fs-md"].every((t) => css.includes(t)));

const OFF_SCALE_FONT = /fontSize: (?:9\.5|10\.5|11\.5|12\.5|14\.5)\b/g;
const OFF_SCALE_GAP = /gap: (?:2|3|5|7|9|10|11)\b/g;
for (const [name, src] of Object.entries(rails)) {
  const f = src.match(OFF_SCALE_FONT) || [];
  const g = src.match(OFF_SCALE_GAP) || [];
  check(`${name}: no off-scale type`, f.length === 0, f.join(", "));
  check(`${name}: no off-scale gaps`, g.length === 0,
        g.join(", ") + " - the scale is 4 / 6 / 8 / 12 / 16");
}

// -- 3. a warning is one colour ----------------------------------------------
// #f59e0b is 2.1:1 on the page and #ef4444 is 3.35:1; --warn and --alert were
// measured for exactly this, and the classes for them already existed.
for (const [name, src] of Object.entries(rails)) {
  const inline = src.match(/color: "#(?:f59e0b|ef4444)"/g) || [];
  check(`${name}: status colour comes from the theme`, inline.length === 0,
        `${inline.length} note(s) still colour themselves inline`);
}
// The classes now paint from the *-text tokens rather than the fill ones: a
// fill colour is read against its own shape, a word against the surface the
// theme is painting, and one value cannot clear 4.5:1 on both a white card and
// a black one. See test_contrast.mjs.
check("the classes those notes use exist",
      css.includes(".c-warn { color: var(--warn-text); }")
      && css.includes(".c-danger { color: var(--alert-text); }"));
check("...and are actually used", wf.includes('className="small c-warn"')
      && wf.includes('className="small c-danger"'));

// -- 4. the Properties rail asks four questions, not forty -------------------
for (const title of ["Card", "Data", "Appearance", "Layout"]) {
  check(`the Properties rail has a ${title} section`,
        shared.includes(`<Section title="${title}"`));
}
check("...Appearance and Layout start folded",
      (shared.match(/<Section title="(?:Appearance|Layout)" open=\{false\}/g) || []).length === 2,
      "a card is bound far more often than it is dressed");
// Section now lives in the shared primitives: the Email footage dialog wanted
// exactly the same thing, and a second copy would be a second set of
// decisions about what a section looks like.
const prims = read("frontend", "src", "components", "ui", "primitives.jsx");
check("...a folded section still says how many of its fields are set",
      prims.includes("rail-section-n") && css.includes(".rail-section-n"));
check("...and there is ONE Section component, not one per page",
      prims.includes("export function Section(")
      && !shared.includes("function Section(")
      && shared.includes("Section } from \"../ui/primitives.jsx\""));
check("...and the two colours sit with the template that governs them",
      shared.indexOf("<ColourField label=\"Accent colour\"")
        > shared.indexOf("<Field label=\"Template\"")
      && shared.indexOf("<ColourField label=\"Accent colour\"")
        < shared.indexOf("<Section title=\"Layout\""),
      "they used to sit at the very bottom, past every data field");
check("...and one component draws both of them",
      (shared.match(/<ColourField/g) || []).length === 2
      && shared.includes("function ColourField("),
      "two hand-rolled copies of the same eight inline styles drift apart");

// -- 5. the section heading is reachable without a mouse ---------------------
check("sections are <details>, so they open from the keyboard",
      prims.includes('<details className="rail-section"')
      && css.includes(".rail-section > summary:focus-visible"));

if (bad) {
  console.log(`\n${bad} FAILED`);
  process.exit(1);
}
console.log("ALL OK");
