// The dashboard grid's row height, against the window it is drawn in.
//
// THE BUG. The grid was fluid sideways and fixed vertically: twelve columns
// that follow the window, and a row height that did not. A card three rows
// tall was 284px on a 27-inch monitor and 284px on a laptop -- a third of one
// screen and half of the other. The only way to see a whole Analytics page on
// a laptop was to zoom the browser to 90%, which is the reader doing by hand
// what the grid would not.
//
// WHAT IS PINNED. That the row SHRINKS and never grows, so every dashboard
// already arranged looks exactly as it did on the screen it was arranged on;
// that it stops shrinking before the cards become a list of grey boxes; and
// that nonsense input falls back to the authored size rather than to zero,
// because this number divides a drag in the builder and a zero there is a
// division by zero in the middle of a mouse move.
//
//   node tests/check_rowfit.mjs
import { createRequire } from "node:module";
import { rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".rowfit-test.cjs");

let bad = 0;
const check = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`${ok ? "ok  " : "FAIL"} ${label}: ${JSON.stringify(got)}`);
  if (!ok) { console.log(`      wanted ${JSON.stringify(want)}`); bad += 1; }
};

await build({
  stdin: {
    contents: `export { rowPxFor, rowPx, GRID_SIZES } from "${
      join(FRONT, "src/components/widgets/WidgetGrid.jsx").replace(/\\/g, "/")}";`,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"production"' },
});
const { rowPxFor, rowPx, GRID_SIZES } = req(OUT);

// -- the screen these numbers were chosen on -----------------------------------
// 900px is the authored viewport. At and above it, nothing changes at all --
// which is what makes this safe to ship to dashboards that already exist.
check("medium on the screen it was authored on", rowPxFor("medium", 900), 88);
check("small likewise", rowPxFor("small", 900), 64);
check("big likewise", rowPxFor("big", 900), 116);

// -- a taller window does NOT get taller rows -----------------------------------
// Growing would rearrange every page ever made on a big monitor, to solve a
// problem only small screens have.
check("a 1440p window keeps the authored height", rowPxFor("medium", 1400), 88);
check("...and an absurd one still does", rowPxFor("medium", 4000), 88);

// -- a laptop gets a shorter row ------------------------------------------------
// 768px of screen is roughly 700px of viewport inside the browser chrome.
// 700/900 of 88 is 68: a fifth shorter, which is about the 90% zoom that was
// being applied by hand.
check("a laptop viewport shrinks the row", rowPxFor("medium", 700), 68);
check("...proportionally for small", rowPxFor("small", 700), 50);
check("...and for big", rowPxFor("big", 700), 90);

// -- but it stops ----------------------------------------------------------------
// Below the floor the cards stop being readable and start being a list of
// boxes, which is worse than scrolling.
const floor = Math.round(88 * 0.68);
check("a very short window hits the floor rather than vanishing",
      rowPxFor("medium", 300), floor);
check("...and cannot go below it at any height", rowPxFor("medium", 1), floor);

// -- nonsense is not zero ---------------------------------------------------------
// This number divides a pointer delta in the builder's drag maths. A zero here
// is a division by zero in the middle of a mouse move.
for (const junk of [0, -1, NaN, null, undefined, "tall"]) {
  check(`a viewport of ${JSON.stringify(junk)} falls back to the authored size`,
        rowPxFor("medium", junk), 88);
}
check("an unknown size falls back to medium", rowPxFor("enormous", 900), 88);
check("...and shrinks like medium too", rowPxFor("enormous", 700), 68);

// -- the named sizes still mean what the layouts assume ---------------------------
check("the three sizes are unchanged",
      [GRID_SIZES.small, GRID_SIZES.medium, GRID_SIZES.big], [64, 88, 116]);
check("rowPx still answers the geometry, ignoring the window",
      [rowPx("small"), rowPx("medium"), rowPx("big")], [64, 88, 116]);

// -- and the order never inverts ---------------------------------------------------
// Whatever the window, small must not end up taller than medium: the three are
// a choice an operator made, not three ways of arriving at the same number.
for (const h of [200, 500, 700, 900, 1600]) {
  const [s, m, b] = ["small", "medium", "big"].map((k) => rowPxFor(k, h));
  check(`small <= medium <= big at ${h}px`, [s <= m, m <= b], [true, true]);
}

rmSync(OUT, { force: true });
console.log();
if (bad) { console.log(`${bad} check(s) failed`); process.exit(1); }
console.log("all good");
