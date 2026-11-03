// The date-range picker's arithmetic.
//
// Hand-rolled date maths (no date-fns) is worth pinning, because every bug in
// it is silent: a filter simply returns the wrong rows, and nothing errors.
// The two that actually bite:
//
//   * month stepping from a 31st. `setMonth(+1)` on Jan 31 lands in March,
//     because February has no 31st -- so a calendar paging forward would skip
//     a month entirely. Guarded by setting the day to 1 first.
//   * local vs UTC days. toISOString() gives the UTC day, so anywhere behind
//     UTC an early-morning "today" resolves to yesterday and the filter comes
//     back empty.
//
//   node tests/check_daterange.mjs
import { createRequire } from "node:module";
import { rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const OUT = join(FRONT, "node_modules", ".daterange-test.cjs");

let bad = 0;
const check = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`${ok ? "ok  " : "FAIL"} ${label}: ${JSON.stringify(got)}`);
  if (!ok) { console.log(`      wanted ${JSON.stringify(want)}`); bad += 1; }
};

await build({
  stdin: {
    contents: `export { isoDay, PRESETS, placePanel } from "${
      join(FRONT, "src/components/ui/DateRangePicker.jsx").replace(/\\/g, "/")}";`,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"production"' },
});
const { isoDay, PRESETS, placePanel } = req(OUT);

// -- local days, not UTC -------------------------------------------------------
// 00:30 local on the 12th is still the 12th. Under toISOString() any timezone
// behind UTC reports the 11th, which is the "today shows nothing" bug.
check("an early-morning date keeps its local day",
      isoDay(new Date(2026, 7, 12, 0, 30)), "2026-08-12");
check("a late-evening date keeps its local day",
      isoDay(new Date(2026, 7, 12, 23, 30)), "2026-08-12");
check("single digits are padded", isoDay(new Date(2026, 0, 5)), "2026-01-05");

// -- the presets ---------------------------------------------------------------
const on = (y, m, d) => new Date(y, m, d);
const run = (name, today) => {
  const entry = PRESETS.find(([n]) => n === name);
  const [s, e] = entry[1](today);
  return [isoDay(s), isoDay(e)];
};

const T = on(2026, 7, 12);                 // Wed 12 Aug 2026
check("Today", run("Today", T), ["2026-08-12", "2026-08-12"]);
check("Yesterday", run("Yesterday", T), ["2026-08-11", "2026-08-11"]);
// Inclusive: 7 days ENDING today is the 6th to the 12th, not the 5th.
check("Last 7 days is inclusive", run("Last 7 days", T), ["2026-08-06", "2026-08-12"]);
check("Last 30 days is inclusive", run("Last 30 days", T), ["2026-07-14", "2026-08-12"]);
check("Month to date", run("Month to date", T), ["2026-08-01", "2026-08-12"]);
check("Last month", run("Last month", T), ["2026-07-01", "2026-07-31"]);
check("Year to date", run("Year to date", T), ["2026-01-01", "2026-08-12"]);
check("Last year", run("Last year", T), ["2025-01-01", "2025-12-31"]);

// -- month-length edge cases ---------------------------------------------------
// From the 31st, "last month" must be the whole of the previous month -- the
// case where naive setMonth arithmetic overflows into the wrong one.
check("Last month, viewed from the 31st",
      run("Last month", on(2026, 2, 31)), ["2026-02-01", "2026-02-28"]);
check("Month to date, from the 1st",
      run("Month to date", on(2026, 2, 1)), ["2026-03-01", "2026-03-01"]);
// Crossing a year boundary backwards.
check("Last month in January",
      run("Last month", on(2026, 0, 15)), ["2025-12-01", "2025-12-31"]);
check("Last 30 days crossing new year",
      run("Last 30 days", on(2026, 0, 5)), ["2025-12-07", "2026-01-05"]);
// A leap February, which is the other length arithmetic gets wrong.
check("Last month from March in a leap year",
      run("Last month", on(2024, 2, 15)), ["2024-02-01", "2024-02-29"]);

// Every preset must produce start <= end, whatever day it is asked about.
for (const [name] of PRESETS) {
  for (const day of [on(2026, 0, 1), on(2026, 1, 28), on(2024, 1, 29),
                     on(2026, 2, 31), on(2026, 11, 31)]) {
    const [s, e] = run(name, day);
    if (s > e) { console.log(`FAIL ${name} on ${isoDay(day)} is inverted`); bad += 1; }
  }
}

// -- staying inside the window -------------------------------------------------
// The popover hangs off the button's RIGHT edge, so a button near the left of
// the window put half the calendar -- the preset rail, the previous-month
// arrow -- off screen where it could not be clicked. jsdom reports every rect
// as zero, so the geometry is checked directly with the rects a browser would
// have measured.
const PANEL = { width: 610, height: 330 };
const VIEW = { width: 1440, height: 900 };
const anchor = (left, top, w = 190, h = 30) =>
  ({ left, right: left + w, top, bottom: top + h });

const place = (a, panel = PANEL, view = VIEW) => placePanel(a, panel, view);
const edges = (a, panel = PANEL, view = VIEW) => {
  const { dx } = place(a, panel, view);
  const left = a.right - panel.width + dx;
  return [left, left + panel.width];
};

check("a centred button needs no nudge", place(anchor(600, 100)).dx, 0);

// The reported bug: the picker sat near the left edge and ran off screen.
const [l1, r1] = edges(anchor(40, 100));
check("a left-edge button is pushed back into view", [l1 >= 8, r1 <= 1432],
      [true, true]);

// The mirror case, in case a caller ever anchors it left rather than right.
const [l2, r2] = edges(anchor(1300, 100));
check("a right-edge button is pulled back into view", [l2 >= 8, r2 <= 1432],
      [true, true]);

// Narrower than the panel: it cannot fit, so it must at least START on screen
// (the panel's own max-width scrolls the rest) rather than begin off the left.
const [l3] = edges(anchor(20, 100), PANEL, { width: 420, height: 900 });
check("a window narrower than the panel still starts on screen", l3, 8);

// Vertical: a button low in the window flips the calendar above it.
check("a low button flips the panel above", place(anchor(600, 800)).above, true);
check("a high button does not flip", place(anchor(600, 100)).above, false);
// ...but only when there is room above. In a short window both sides clip, and
// flipping would just move the clipping without fixing it.
check("a short window does not flip pointlessly",
      place(anchor(600, 200), PANEL, { width: 1440, height: 400 }).above, false);

rmSync(OUT, { force: true });
if (bad) { console.error(`\n${bad} date problem(s).`); process.exit(1); }
console.log(`\n== ${PRESETS.length} presets, local-day handling, month-length `
            + "edges and viewport clamping all hold");
