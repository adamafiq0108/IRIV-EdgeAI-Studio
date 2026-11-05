// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Freestyle grid canvas: 12 columns, fixed-height rows. Drag the title bar to
// move, drag the corner handle to resize - the same normalized-pointer maths
// the zone editor uses, just snapped to grid cells. No layout library: the
// cells are CSS grid, the dragging is three handlers.
//
// Shared by the Dashboard Builder and the Analytics page so "arrange your
// widgets" means the same thing, and behaves the same way, in both. Its
// geometry is deliberately identical to the read-only WidgetGrid - any
// difference between them reads as "what I arranged is not what I got".
import { useRef, useState } from "react";
import { ArrowDownToLine, ArrowUpToLine } from "lucide-react";
import { arrange, canArrange, zOf } from "./layerOrder.js";
import { useViewSettings } from "../ui/ViewSettings.jsx";
import Widget, { STATIC_CARD_KINDS } from "./Widget.jsx";
import {
  GRID_COLS, GRID_ROW_PX, GRID_GAP, GRID_SIZES, rowPx, rowPxFor, useRowPx,
  defaultCell, autoPlace, cellAt, paletteDragCell, notePaletteDrag, placedAt,
} from "./WidgetGrid.jsx";

// Geometry and placement live with the read-only renderer (WidgetGrid) so the
// editor and the finished page share one definition; re-exported here for the
// pages that import them alongside the canvas.
export { GRID_COLS, GRID_ROW_PX, GRID_GAP, GRID_SIZES, rowPx, rowPxFor,
         useRowPx,
         defaultCell, autoPlace, cellAt, notePaletteDrag, placedAt };

//: What a palette drag carries (the Builders set these on dragstart).
const PALETTE_TYPES = ["application/x-palette", "application/x-catalog"];

export default function GridCanvas({ widgets, preview, selected, editable,
                                     setSelected, patch, remove,
                                     size = "medium", onDropCell = null }) {
  const [view] = useViewSettings();
  const ref = useRef(null);
  const drag = useRef(null);   // {id, mode, startX, startY, grid}
  // The card being dragged rides above every other while it moves, so it can
  // be seen and dropped wherever it goes; it returns to its layer after.
  const [dragging, setDragging] = useState(null);
  // DROP AT THE POINTER: while a palette card is dragged over the canvas, a
  // placeholder shows the cell it will land in; dropping places it there
  // (onDropCell), not at the bottom. Click-to-append is the page's and
  // unchanged.
  const [ghost, setGhost] = useState(null);
  const fromPalette = (e) => PALETTE_TYPES.some((t) => [...(e.dataTransfer?.types || [])].includes(t));
  const dropCell = (e) => {
    const { w, h } = paletteDragCell();
    return cellAt({ clientX: e.clientX, clientY: e.clientY,
                    rect: ref.current.getBoundingClientRect(), rowH, w, h });
  };
  const onDragOver = (e) => {
    if (!editable || !onDropCell || !fromPalette(e)) return;
    e.preventDefault();
    const c = dropCell(e);
    setGhost((g) => (g && g.x === c.x && g.y === c.y && g.w === c.w && g.h === c.h
                     && g.ok === c.ok ? g : c));
  };
  const onDragLeave = (e) => {
    if (!ref.current || !ref.current.contains(e.relatedTarget)) setGhost(null);
  };
  const onDrop = (e) => {
    if (!editable || !onDropCell || !fromPalette(e)) return;
    e.preventDefault();
    e.stopPropagation();          // the page's own drop handler would append it
    const c = dropCell(e);
    setGhost(null);
    if (c.ok) onDropCell(e, c);
  };
  // A layer move rewrites the z of every card it passes (layerOrder.js).
  const restack = (id, op) => {
    for (const [wid, grid] of Object.entries(arrange(widgets, id, op))) patch(wid, { grid });
  };
  // The row height the page is set to. Everything that converts pixels to
  // cells has to use THIS and not the constant, or a drag on a big grid moves
  // the card further than the pointer went.
  // The SAME number the finished page uses, from the same function, so a
  // layout dragged here lands where it was dropped there.
  const rowH = useRowPx(size);

  function cells(e) {
    const rect = ref.current?.getBoundingClientRect();
    const cw = rect ? (rect.width - GRID_GAP * (GRID_COLS - 1)) / GRID_COLS : 80;
    const d = drag.current;
    return {
      dx: Math.round((e.clientX - d.startX) / (cw + GRID_GAP)),
      dy: Math.round((e.clientY - d.startY) / (rowH + GRID_GAP)),
    };
  }

  function down(e, w, mode) {
    if (!editable) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { id: w.id, mode, startX: e.clientX, startY: e.clientY,
                     grid: { ...w.grid } };
    setDragging(w.id);
    setSelected?.(w.id);
  }

  function move(e) {
    const d = drag.current;
    if (!d) return;
    const { dx, dy } = cells(e);
    if (d.mode === "move") {
      const x = Math.max(0, Math.min(GRID_COLS - d.grid.w, d.grid.x + dx));
      const y = Math.max(0, Math.min(199, d.grid.y + dy));
      if (x !== d.grid.x || y !== d.grid.y) patch(d.id, { grid: { ...d.grid, x, y } });
    } else {
      const w2 = Math.max(1, Math.min(GRID_COLS - d.grid.x, d.grid.w + dx));
      const h2 = Math.max(1, Math.min(24, d.grid.h + dy));
      patch(d.id, { grid: { ...d.grid, w: w2, h: h2 } });
    }
  }

  const up = () => { drag.current = null; setDragging(null); };

  const bottom = Math.max(6, ...widgets.map((w) => (w.grid?.y || 0) + (w.grid?.h || 2)));
  return (
    <div ref={ref}
         onPointerMove={move} onPointerUp={up} onPointerLeave={up}
         onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}
         style={{
           display: "grid",
           gridTemplateColumns: `repeat(${GRID_COLS}, 1fr)`,
           gridAutoRows: `${rowH}px`,
           gap: GRID_GAP,
           minHeight: (bottom + 2) * (rowH + GRID_GAP),
           // faint cell guides so "where will it land" is visible while
           // editing -- switchable, because on a dense dashboard they compete
           // with the widgets they sit behind (ViewSettings).
           backgroundImage: view.guides
             ? "linear-gradient(to right, rgba(128,128,128,.07) 1px, transparent 1px)," +
               "linear-gradient(to bottom, rgba(128,128,128,.07) 1px, transparent 1px)"
             : "none",
           backgroundSize: `calc((100% + ${GRID_GAP}px) / ${GRID_COLS}) ${rowH + GRID_GAP}px`,
           borderRadius: 8,
           // Layers stack among the cards, never behind the canvas.
           isolation: "isolate",
         }}>
      {widgets.map((w) => {
        const g = w.grid && w.grid.w ? w.grid : { x: 0, y: 0, w: 3, h: 2 };
        // A Text / Image card is drawn from the widget being edited, so a
        // keystroke shows at once instead of after the next preview.
        const rendered = STATIC_CARD_KINDS.includes(w.kind) ? { ...w, data: { static: true } }
          : (preview || []).find((p) => p.id === w.id);
        return (
          <div key={w.id}
               className={`builder-slot${selected === w.id ? " selected" : ""}`}
               style={{
                 gridColumn: `${g.x + 1} / span ${g.w}`,
                 gridRow: `${g.y + 1} / span ${g.h}`,
                 position: "relative",
                 overflow: "hidden",
                 display: "flex",
                 flexDirection: "column",
                 // Its layer, exactly as the page will stack it; the card
                 // being dragged goes over everything until it is let go.
                 ...(dragging === w.id ? { zIndex: 999 } : zOf(w) ? { zIndex: zOf(w) } : null),
               }}
               data-z={zOf(w)}
               onClick={() => setSelected?.(w.id)}>
            {/* Overlaid, not stacked: a bar in the flow steals ~24px from the
                widget, so every cell here would render slightly shorter than
                the same cell on the dashboard. Edit chrome must not change the
                thing being edited. */}
            <div className="builder-slot-bar"
                 style={{ cursor: editable ? "move" : "default", touchAction: "none",
                          position: "absolute", left: 0, right: 0, top: 0, zIndex: 3 }}
                 onPointerDown={(e) => down(e, w, "move")}>
              <span className="drag-handle" title="Drag to move">⠿</span>
              {/* The TITLE, not the stat key: a bar reading
                  tile · fn_z_zone_msfifdf_zoneviol_count identifies the card
                  to the compiler, not to the person arranging it. The key
                  stays on the hover tooltip for anyone debugging a binding.
                  Truncated either way, or a long name pushes the × clean off
                  the card and the widget becomes impossible to remove. */}
              <span className="muted small" title={w.key || w.source}
                    style={{ flex: 1, minWidth: 0, overflow: "hidden",
                             textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {w.kind} · {w.title || w.key || w.source}
              </span>
              {/* Front / back from the card itself; all four moves are in
                  its Properties, under Layout -> Arrange. */}
              {editable && (
                <>
                  <button type="button" className="icon-btn" title="Bring to front"
                          aria-label="Bring to front" disabled={!canArrange(widgets, w.id, "front")}
                          style={{ flexShrink: 0 }}
                          onPointerDown={(e) => e.stopPropagation()}
                          onClick={(e) => { e.stopPropagation(); restack(w.id, "front"); }}>
                    <ArrowUpToLine size={13} aria-hidden="true" />
                  </button>
                  <button type="button" className="icon-btn" title="Send to back"
                          aria-label="Send to back" disabled={!canArrange(widgets, w.id, "back")}
                          style={{ flexShrink: 0 }}
                          onPointerDown={(e) => e.stopPropagation()}
                          onClick={(e) => { e.stopPropagation(); restack(w.id, "back"); }}>
                    <ArrowDownToLine size={13} aria-hidden="true" />
                  </button>
                </>
              )}
              <button className="icon-btn" title="Remove"
                      style={{ flexShrink: 0 }}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => { e.stopPropagation(); remove(w.id); }}>×</button>
            </div>
            <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
              {rendered
                ? <Widget widget={rendered} fill />
                : <div className="stat-card" style={{ height: "100%" }}>
                    <div className="stat-label">{w.title}</div>
                  </div>}
            </div>
            {editable && (
              <div title="Drag to resize"
                   onPointerDown={(e) => { e.stopPropagation(); down(e, w, "resize"); }}
                   style={{
                     position: "absolute", right: 2, bottom: 2, width: 16, height: 16,
                     cursor: "nwse-resize", touchAction: "none",
                     borderRight: "3px solid rgba(128,128,128,.6)",
                     borderBottom: "3px solid rgba(128,128,128,.6)",
                     borderRadius: 2,
                   }} />
            )}
          </div>
        );
      })}
      {/* Where a dragged palette card will land. */}
      {ghost && (
        <div className={`drop-ghost${ghost.ok ? "" : " is-blocked"}`} data-testid="drop-ghost"
             aria-hidden="true"
             style={{ gridColumn: `${ghost.x + 1} / span ${ghost.w}`,
                      gridRow: `${ghost.y + 1} / span ${ghost.h}`, zIndex: 1000,
                      pointerEvents: "none" }} />
      )}
    </div>
  );
}
