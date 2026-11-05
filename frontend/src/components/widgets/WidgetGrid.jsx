// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Read-only renderer for a Builder page.
//
// The Builder places widgets on a 12-column grid ({x, y, w, h} cells); older
// layouts have no placement at all and just carry a `span`. Both render here,
// so a dashboard composed years apart still looks like one page - and so the
// Dashboard and the Builder canvas agree about where a widget sits, which they
// cannot do if each page implements its own grid.
import { useEffect, useState } from "react";
import Widget from "./Widget.jsx";
import { zOf } from "./layerOrder.js";

// The one definition of the grid's geometry. GridCanvas (the editable version)
// imports these rather than declaring its own - two copies is how an editor
// and its preview drift apart.
export const GRID_COLS = 12;
export const GRID_ROW_PX = 88;
export const GRID_GAP = 10;

// How tall one row is, by name. The GRID does not change with it -- twelve
// columns, always -- so a card that spans 3x2 still spans 3x2 and nothing on
// an arranged page moves. Only the pixels a row is worth change, so the whole
// dashboard grows or shrinks together.
//
// Medium is exactly what every page was built at, which is what makes this
// safe to add to layouts that already exist: they say nothing, they get
// medium, they look identical.
export const GRID_SIZES = { small: 64, medium: GRID_ROW_PX, big: 116 };

//: The window these numbers were chosen on. A row was worth 88px on a desktop
//: browser about 900px tall inside the chrome, and every dashboard in
//: existence was arranged against that.
const AUTHORED_VIEWPORT = 900;

//: How far a row may shrink, as a share of its named size. Below this the
//: cards stop being readable and start being a list of boxes, which is worse
//: than scrolling.
const FLOOR = 0.68;

/**
 * The pixels one row is worth, for a named size on a window this tall.
 *
 * The grid was fluid sideways and fixed vertically: twelve columns that follow
 * the window, and a row height that did not. So a 3-row card was 284px on a
 * 27-inch monitor and 284px on a laptop -- a third of one screen and half of
 * the other -- and the only way to see a whole page on a small screen was to
 * zoom the browser out, which is the reader doing by hand what the grid would
 * not.
 *
 * It shrinks and never grows. A window at or above the one these numbers were
 * authored on gets exactly the old pixels, so every dashboard that already
 * exists looks the same on the screen it was arranged on; a shorter window
 * gets a proportionally shorter row, down to a floor.
 *
 * Pure, and separate from the hook, because this is the part worth pinning in
 * a test -- the hook is only where the number comes from.
 */
export function rowPxFor(size, viewportH) {
  const named = GRID_SIZES[size] || GRID_ROW_PX;
  const h = Number(viewportH) > 0 ? Number(viewportH) : AUTHORED_VIEWPORT;
  const scaled = Math.round(h * (named / AUTHORED_VIEWPORT));
  return Math.max(Math.round(named * FLOOR), Math.min(named, scaled));
}

/**
 * The same number, re-read when the window changes.
 *
 * A listener rather than a CSS clamp, because the BUILDER needs this as a
 * number: it converts a drag in pixels into a move in cells, sizes its own
 * canvas, and paints the background grid. A clamp would have fixed the
 * finished page and left the editor measuring something else, which is the
 * one failure the shared geometry in this file exists to prevent.
 */
export function useRowPx(size) {
  const [h, setH] = useState(
    () => (typeof window === "undefined" ? AUTHORED_VIEWPORT : window.innerHeight));
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onResize = () => setH(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return rowPxFor(size, h);
}

/** The named size's own pixels, ignoring the window. Kept for callers that
 *  want the geometry rather than the rendering -- printing, for one. */
export const rowPx = (size) => GRID_SIZES[size] || GRID_ROW_PX;

/** Default cell size when a widget first enters grid mode. */
export const defaultCell = (kind) =>
  kind === "video" ? { w: 6, h: 4 }
  : kind === "tile" || kind === "status" || kind === "verdict"
    || kind === "button" ? { w: 3, h: 2 }
  // A tick list wants the room its rows (and an open dropdown) take.
  : kind === "ai_classes" || kind === "class_filters" ? { w: 4, h: 4 }
  : { w: 4, h: 3 };

/**
 * The grid cell under the pointer, for a card of `w` x `h` dropped there
 * (Tok Aba, 2026-10-14: "make it pop up at the place of the mouse").
 *
 * `rect` is the canvas's getBoundingClientRect(), which is in the same
 * viewport coordinates as the pointer's clientX / clientY -- so the page's
 * scroll, and the canvas scrolling sideways in its own box, are already
 * accounted for. A column is (width - the gaps) / cols wide, a row
 * `rowH` tall, each followed by the gap. The card's TOP-LEFT lands on the
 * cell under the pointer; it moves left as needed so it never runs off the
 * right edge (x <= cols - w), and never above the top. `ok` is false when
 * the pointer is not over the canvas at all.
 */
export function cellAt({ clientX, clientY, rect, rowH, w = 4, h = 3,
                         cols = GRID_COLS, gap = GRID_GAP }) {
  const width = Math.max(1, rect.width || 0);
  const colW = Math.max(1, (width - gap * (cols - 1)) / cols);
  const px = clientX - rect.left;
  const py = clientY - rect.top;
  const ww = Math.max(1, Math.min(cols, w));
  const x = Math.max(0, Math.min(cols - ww, Math.floor(px / (colW + gap))));
  const y = Math.max(0, Math.min(199, Math.floor(py / (Math.max(1, rowH) + gap))));
  return { x, y, w: ww, h, ok: px >= 0 && px <= width && py >= 0 };
}

//: Which card a palette drag is carrying. The browser does not let a drop
//: target read the dragged data until the drop itself, so the placeholder
//: could not know how wide a card it is placing; the palette says so when
//: the drag starts (notePaletteDrag) and the canvas asks (paletteDragCell).
let paletteKind = "";
export function notePaletteDrag(kind) { paletteKind = String(kind || ""); }
export const paletteDragCell = () => defaultCell(paletteKind);

/** A new card placed at a dropped cell: its own default size, kept inside
 *  the grid's right edge. The same {x, y, w, h} a moved card stores. */
export function placedAt(widget, cell) {
  const { w, h } = defaultCell(widget.kind);
  return { ...widget, grid: { x: Math.max(0, Math.min(GRID_COLS - w, cell.x)),
                              y: Math.max(0, cell.y), w, h } };
}

/**
 * Give every widget a grid cell. Placed ones keep theirs; unplaced ones flow
 * left-to-right into rows BELOW everything placed. This is what renders a page
 * where some widgets were arranged and some never were - without it the two
 * kinds landed on top of each other, and it is the same function the editors
 * use, so the editing canvas and the finished page cannot disagree.
 */
export function autoPlace(list) {
  const placed = list.filter((w) => w.grid && w.grid.w);
  let y = placed.length
    ? Math.max(...placed.map((w) => (w.grid.y || 0) + (w.grid.h || 2)))
    : 0;
  let x = 0, rowH = 0;
  return list.map((w) => {
    if (w.grid && w.grid.w) return w;
    const { w: cw, h: ch } = defaultCell(w.kind);
    if (x + cw > GRID_COLS) { x = 0; y += rowH; rowH = 0; }
    const out = { ...w, grid: { x, y, w: cw, h: ch } };
    x += cw; rowH = Math.max(rowH, ch);
    return out;
  });
}

export default function WidgetGrid({ widgets, accent, empty = null,
                                     running = true, size = "medium", exportCsv = null }) {
  // Before the early returns below: a hook that sometimes runs is not a hook.
  const rowH = useRowPx(size);
  const list = widgets || [];
  if (!list.length) return empty;

  // How smooth each video tile is allowed to be.
  //
  // Every tile is a blocking relay thread on the master, so a wall of them at
  // full rate starves the box -- which is why they were pinned at 8fps. But
  // 8fps next to the Detection page's full rate reads as slow motion, and the
  // usual dashboard has exactly ONE feed on it. So the budget is shared: one
  // tile gets the pipeline's own rate (no cap at all), and it only drops as
  // more feeds are added.
  const feeds = list.filter((w) => w.kind === "video").length;
  const streamFps = feeds <= 1 ? 0 : feeds === 2 ? 15 : 8;

  // No placements anywhere: the old auto-flow layout, natural heights.
  if (!list.some((w) => w.grid && w.grid.w)) {
    return (
      <div className="stats-grid g4">
        {list.map((w) => <Widget key={w.id} widget={w} accent={accent}
                                   streamFps={streamFps} running={running}
                                   exportCsv={exportCsv} />)}
      </div>
    );
  }

  // Anything still unplaced flows below the placed ones - never on top.
  // Sorted top-to-bottom, then left-to-right. On a desktop this changes
  // nothing: every widget states its own cell, so DOM order is invisible. On
  // a phone the grid collapses to ONE column (see .wgt-grid in theme.css) and
  // DOM order becomes the only order there is - unsorted, the dashboard would
  // read in whatever sequence the widgets were created.
  const shown = autoPlace(list).slice().sort(
    (a, b) => (a.grid?.y || 0) - (b.grid?.y || 0) || (a.grid?.x || 0) - (b.grid?.x || 0));

  return (
    <div
      className="wgt-grid"
      // EXACTLY the editing canvas's geometry: same columns, same fixed row
      // height, same gap. Any difference here shows up as "what I arranged is
      // not what I got" - so the widgets fit the cells (Widget's `fill`)
      // rather than the cells accommodating the widgets.
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${GRID_COLS}, 1fr)`,
        gridAutoRows: `${rowH}px`,
        gap: GRID_GAP,
        // A card's layer (grid.z, layerOrder.js) stacks it among the page's
        // cards and never behind the page itself.
        isolation: "isolate",
      }}
    >
      {shown.map((w) => {
        const g = w.grid;
        return (
          <div key={w.id}
               className="wgt-cell"
               style={{
                 gridColumn: `${(g.x || 0) + 1} / span ${Math.min(GRID_COLS, g.w)}`,
                 gridRow: `${(g.y || 0) + 1} / span ${Math.max(1, g.h || 2)}`,
                 minWidth: 0,
                 minHeight: 0,
                 // In front of / behind the cards it overlaps; unset keeps
                 // the order it always had.
                 ...(zOf(w) ? { zIndex: zOf(w) } : null),
               }}>
            <Widget widget={w} accent={accent} fill streamFps={streamFps}
                    running={running} exportCsv={exportCsv} />
          </div>
        );
      })}
    </div>
  );
}
