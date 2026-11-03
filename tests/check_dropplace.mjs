// A card dragged from a Builder palette lands where the pointer lets go
// (Tok Aba, 2026-10-14: "don't make it always display at the bottom").
//
//   1. the pointer -> cell maths (WidgetGrid.cellAt): page / canvas offsets,
//      gaps and row height, clamped so a card never runs off the right edge,
//      other column counts, below the last row, outside the canvas
//   2. placedAt: the card's own default size, kept inside the grid
//   3. on the canvas: dragging over shows a placeholder at the snapped cell,
//      dropping hands that cell to the page (onDropCell), leaving clears it;
//      a drop that is not a palette card is ignored; a read-only canvas
//      shows nothing
//   4. click-to-append still appends after everything
//
//   node tests/check_dropplace.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".dropplace-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image", "Event", "CustomEvent",
                 "MouseEvent", "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};
const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  stdin: { contents: `
    import { createRoot } from "react-dom/client";
    import { act, createElement as h } from "react";
    import GridCanvas from "${p("src/components/widgets/GridCanvas.jsx")}";
    export * as G from "${p("src/components/widgets/WidgetGrid.jsx")}";
    export function live(el, props) {
      const root = createRoot(el);
      const render = (next) => act(() => { root.render(h(GridCanvas, next)); });
      render(props);
      return { act, render, unmount: () => act(() => root.unmount()) };
    }`, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic",
  logLevel: "silent", define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const G = m.G;

// -- 1. the maths ---------------------------------------------------------------------
// A 1210px canvas: 12 columns of (1210 - 11 x 10) / 12 = 91.67px, each with a
// 10px gap -> 101.67px a step; rows 88px + 10px gap = 98px a step.
const rect = { left: 100, top: 50, width: 1210, height: 900 };
const at = (cx, cy, extra = {}) => {
  const c = G.cellAt({ clientX: cx, clientY: cy, rect, rowH: 88, w: 4, h: 3, ...extra });
  return [c.x, c.y, c.ok].join();
};
check("the top-left corner is cell 0,0", at(100, 50) === "0,0,true");
check("a point inside column 3, row 2 (offsets and gaps counted)",
      at(100 + 3 * 101.67 + 20, 50 + 2 * 98 + 30) === "3,2,true", at(100 + 3 * 101.67 + 20, 50 + 2 * 98 + 30));
check("a page scrolled down: the canvas's own rect moved, the cell did not",
      G.cellAt({ clientX: 150, clientY: -250 + 2 * 98 + 30, rowH: 88, w: 4, h: 3,
                 rect: { ...rect, top: -250 } }).y === 2);
check("near the right edge a 4-wide card moves left to fit (x <= 12 - 4)", at(100 + 1200, 60) === "8,0,true");
check("...a 12-wide card can only sit at x 0", G.cellAt({ clientX: 900, clientY: 60, rect, rowH: 88, w: 12, h: 2 }).x === 0);
check("a 6-column grid steps by its own columns",
      G.cellAt({ clientX: 100 + 2 * 205 + 50, clientY: 60, rect, rowH: 88, cols: 6, w: 2, h: 2 }).x === 2);
check("below the last card: the row under the pointer, not further down", at(150, 50 + 9 * 98 + 5) === "0,9,true");
check("an empty grid works the same way", at(100 + 101.67 * 5 + 5, 50 + 5) === "5,0,true");
check("left of or above the canvas is not a place", at(90, 60).endsWith("false") && at(150, 40).endsWith("false"));

// -- 2. placedAt ------------------------------------------------------------------------
check("a dropped tile takes its own size (3 x 2) at the cell",
      JSON.stringify(G.placedAt({ id: "t", kind: "tile" }, { x: 5, y: 7 }).grid) === '{"x":5,"y":7,"w":3,"h":2}');
check("...a live video (6 wide) moves left to fit",
      G.placedAt({ id: "v", kind: "video" }, { x: 10, y: 0 }).grid.x === 6);

// -- 3. on the canvas ---------------------------------------------------------------------
const rowH = G.rowPxFor("medium", window.innerHeight);
function dragEvent(type, cx, cy, data) {
  const ev = new dom.window.Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "clientX", { value: cx });
  Object.defineProperty(ev, "clientY", { value: cy });
  Object.defineProperty(ev, "dataTransfer", { value: {
    types: Object.keys(data), getData: (t) => data[t] ?? "", dropEffect: "copy" } });
  return ev;
}
{
  let dropped = null;
  const box = document.body.appendChild(document.createElement("div"));
  const props = { widgets: [{ id: "a", title: "A", kind: "tile", grid: { x: 0, y: 0, w: 3, h: 2 }, template: "plain" }],
                  preview: [], editable: true, selected: null, setSelected: () => {}, patch: () => {},
                  remove: () => {}, onDropCell: (e, cell) => { dropped = { cell, idx: e.dataTransfer.getData("application/x-palette") }; } };
  const v = m.live(box, props);
  const canvas = box.firstElementChild;
  canvas.getBoundingClientRect = () => rect;
  G.notePaletteDrag("text_card");                 // 4 x 3
  const cx = 100 + 2 * 101.67 + 10;
  const cy = 50 + 3 * (rowH + 10) + 5;
  v.act(() => { canvas.dispatchEvent(dragEvent("dragover", cx, cy, { "application/x-palette": "1" })); });
  const ghost = box.querySelector('[data-testid="drop-ghost"]');
  check("dragging a palette card over the canvas shows where it will land",
        ghost && ghost.style.gridColumn === "3 / span 4" && ghost.style.gridRow === "4 / span 3"
        && ghost.style.pointerEvents === "none", ghost && ghost.outerHTML);
  v.act(() => { canvas.dispatchEvent(dragEvent("drop", cx, cy, { "application/x-palette": "1" })); });
  check("dropping hands the page that cell (column 2, row 3)",
        dropped && dropped.cell.x === 2 && dropped.cell.y === 3 && dropped.idx === "1", JSON.stringify(dropped));
  check("...and the placeholder goes", !box.querySelector('[data-testid="drop-ghost"]'));
  dropped = null;
  v.act(() => { canvas.dispatchEvent(dragEvent("drop", cx, cy, { "text/plain": "hello" })); });
  check("a drop that is not a palette card is ignored", dropped === null);
  v.act(() => { canvas.dispatchEvent(dragEvent("dragover", cx, cy, { "application/x-catalog": "0" })); });
  check("a workflow card from the palette shows the placeholder too",
        !!box.querySelector('[data-testid="drop-ghost"]'));
  const leave = new dom.window.Event("dragleave", { bubbles: true });
  Object.defineProperty(leave, "relatedTarget", { value: document.body });
  v.act(() => { canvas.dispatchEvent(leave); });
  check("leaving the canvas clears it", !box.querySelector('[data-testid="drop-ghost"]'));
  v.render({ ...props, editable: false });
  v.act(() => { canvas.dispatchEvent(dragEvent("dragover", cx, cy, { "application/x-palette": "1" })); });
  check("a read-only canvas shows no placeholder", !box.querySelector('[data-testid="drop-ghost"]'));
  v.unmount(); box.remove();
}

// -- 4. click-to-append ---------------------------------------------------------------------
const placed = G.autoPlace([{ id: "a", kind: "tile", grid: { x: 0, y: 0, w: 3, h: 2 } },
                            { id: "b", kind: "tile", grid: { x: 0, y: 4, w: 3, h: 2 } },
                            { id: "new", kind: "tile" }]);
check("click-to-append (no cell) still goes after everything",
      placed[2].grid.y === 6 && placed[2].grid.x === 0, JSON.stringify(placed[2].grid));
for (const page of ["Builder.jsx", "AnalyticsBuilder.jsx"]) {
  const src = readFileSync(join(FRONT, "src", "pages", page), "utf8");
  check(`${page}: double-click still appends; the canvas drop places at the pointer`,
        src.includes("onDoubleClick={() => add(item)}") && src.includes("onDropCell={(e, cell) =>"));
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
