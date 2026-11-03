// A circle zone must be ROUND ON SCREEN, not round in storage.
//
// Zone points are normalised (0..1 on each axis) and the canvas is drawn with
// preserveAspectRatio="none", so one unit of x and one unit of y are different
// numbers of pixels on any frame that is not square. Applying one radius to
// both axes therefore drew an ellipse: on a 4:3 feed, a "circle" 33% wider
// than it was tall.
//
// This checks the maths directly rather than the pixels: build a circle at a
// given aspect, project the points back to screen proportions, and assert
// every one is the same distance from the centre.
import { readFileSync } from "node:fs";
import { readText } from "./_text.mjs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readText(join(ROOT, "frontend/src/components/ZoneCanvas.jsx"));

// Lift buildPoints out of the module: importing the file would pull in React
// and the whole component tree for one pure function.
const start = src.indexOf("export function buildPoints");
const end = src.indexOf("\n}", src.indexOf("return pts;", start)) + 2;
const body = src.slice(start, end).replace("export function", "function");
const buildPoints = new Function(`${body}; return buildPoints;`)();

function check(aspect, label) {
  const centre = [0.5, 0.5];
  const edge = [0.75, 0.5];                 // dragged out along x
  const pts = buildPoints("circle", [centre, edge], aspect);

  if (pts.length < 3) throw new Error(`${label}: not a polygon`);

  // Project back into screen proportions: x carries the aspect.
  const radii = pts.map(([x, y]) =>
    Math.hypot((x - centre[0]) * aspect, y - centre[1]));
  const min = Math.min(...radii);
  const max = Math.max(...radii);
  // Chord-to-centre distance is exact for a regular polygon's vertices, so the
  // only slack needed is the 4dp rounding the builder applies.
  const spread = (max - min) / max;
  if (spread > 0.002) {
    throw new Error(`${label}: radius varies by ${(spread * 100).toFixed(1)}% `
                    + `(min ${min.toFixed(4)}, max ${max.toFixed(4)}) - that is an ellipse`);
  }
  return { min, max };
}

// Square: the old code was already correct here, which is exactly why the bug
// survived - it only shows on a frame that is not 1:1.
check(1, "1:1");
// The shapes a camera actually produces.
check(4 / 3, "4:3");
check(16 / 9, "16:9");
check(3 / 4, "portrait 3:4");

// And the regression itself: with the aspect ignored (the old behaviour), a
// 4:3 circle must FAIL this test - otherwise the test proves nothing.
{
  const centre = [0.5, 0.5];
  const pts = buildPoints("circle", [centre, [0.75, 0.5]], 1);   // aspect dropped
  const radii = pts.map(([x, y]) =>
    Math.hypot((x - centre[0]) * (4 / 3), y - centre[1]));
  const spread = (Math.max(...radii) - Math.min(...radii)) / Math.max(...radii);
  if (spread < 0.05) {
    throw new Error("the aspect-blind build looks round on 4:3 - this test cannot "
                    + "detect the bug it exists for");
  }
}

// The preview must be the SAME shape as the thing it previews: a <circle> in
// the stretched unit space renders oval by exactly the aspect.
if (!/<ellipse[\s\S]*?rx=\{r \/ k\}[\s\S]*?ry=\{r\}/.test(src)) {
  throw new Error("the drag preview must be an aspect-corrected ellipse");
}
// ...and the aspect must be MEASURED from the painted box, not assumed from
// the camera's configured size: the element is laid out by CSS.
if (!src.includes("getBoundingClientRect()") || !src.includes("ResizeObserver")) {
  throw new Error("the canvas must measure the aspect it is actually drawn at");
}

console.log("== circle zones are round on screen at 1:1, 4:3, 16:9 and portrait");
