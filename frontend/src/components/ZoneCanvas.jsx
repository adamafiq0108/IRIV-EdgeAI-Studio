// ZoneCanvas - the zone drawing/overlay surface, shared by the Zones page
// (full editor) and the Detection page (read-only overlay).
//
// Zones are drawn in NORMALIZED (0..1) space directly: the SVG overlay uses a
// 0..1 viewBox, so a click maps to a coordinate the app stores as-is and that
// survives a resolution change. Area = polygon (>=3 points), line = 2 points.
// Box and circle are drag shapes that compile to polygons.
import { useEffect, useRef, useState } from "react";

export const ZONE_COLORS = { violation: "#ef4444", area: "#22d3ee", line: "#f59e0b" };
// New zones cycle through these so several zones are visually distinct; each is
// editable per-zone afterwards.
export const ZONE_PALETTE = ["#22d3ee", "#ef4444", "#f59e0b", "#a855f7", "#10b981", "#3b82f6"];

export function zoneColor(z) {
  return z.color || (z.violation ? ZONE_COLORS.violation : ZONE_COLORS[z.kind] || ZONE_COLORS.area);
}

/**
 * Where IN is on a faced line: the midpoint, and a unit-ish normal pointing at
 * the side the app counts as an entry. The arrow drawn from it runs the other
 * way along that normal -- from outside INTO the line -- because what an
 * operator needs to read off it is which way somebody must walk to be counted,
 * not which half of the picture is called in.
 *
 * The app defines the IN side as the POSITIVE side of the drawn direction
 * a→b (app/zones.py `_side_of_line`), and (-dy, dx) is the normal that lands
 * there - so this arrow and the counter can never point different ways.
 * Returns null for a plain line, which has no side to draw.
 */
export function lineFace(z, aspect = 1) {
  if (z.kind !== "line" || (z.points || []).length !== 2) return null;
  const facing = z.facing || "forward";
  if (facing === "none") return null;
  const [[x1, y1], [x2, y2]] = z.points;
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy);
  if (!len) return null;
  const sign = facing === "reverse" ? -1 : 1;
  // Normalised in SCREEN proportions, then converted back per-axis: on a 4:3
  // frame an arrow built in raw normalised units leans, because one unit of x
  // is not one unit of y (same reason buildPoints scales a circle).
  const k = aspect > 0 ? aspect : 1;
  const nx = (-dy / len) * sign, ny = (dx / len) * sign;
  const m = Math.hypot(nx * k, ny) || 1;
  return {
    mid: [(x1 + x2) / 2, (y1 + y2) / 2],
    normal: [nx / m, ny / m],
  };
}

// Turn the raw clicks into zone points for the chosen shape. Exported so the
// Zones page can finish a manual area from its own "Finish" button.
export function buildPoints(mode, pts, aspect = 1) {
  if (mode === "box" && pts.length === 2) {
    const [[x1, y1], [x2, y2]] = pts;
    const ax1 = Math.min(x1, x2), ay1 = Math.min(y1, y2), ax2 = Math.max(x1, x2), ay2 = Math.max(y1, y2);
    return [[ax1, ay1], [ax2, ay1], [ax2, ay2], [ax1, ay2]];
  }
  if (mode === "circle" && pts.length === 2) {
    // Points are NORMALISED (0..1 on each axis) and the canvas is drawn with
    // preserveAspectRatio="none", so one unit of x and one unit of y are
    // different numbers of pixels on any frame that is not square. A radius
    // applied equally to both axes therefore drew an ellipse -- on 4:3, a
    // circle 33% wider than it was tall.
    //
    // So the radius is measured in SCREEN proportions (x scaled by the
    // aspect), and converted back per-axis on the way out. The stored shape
    // is still normalised, which is what the app reads; it is only round
    // rather than oval.
    const [c, edge] = pts;
    const k = aspect > 0 ? aspect : 1;
    const r = Math.hypot((edge[0] - c[0]) * k, edge[1] - c[1]);
    const N = 32;
    return Array.from({ length: N }, (_, i) => {
      const a = (i / N) * 2 * Math.PI;
      return [+(c[0] + (r * Math.cos(a)) / k).toFixed(4),
              +(c[1] + r * Math.sin(a)).toFixed(4)];
    });
  }
  return pts;   // area (manual) and line pass through
}

/**
 * props:
 *   zones        - [{id, kind, points, color, violation, enabled}]
 *   drawMode     - null | "box" | "circle" | "area" | "line" (null = view only)
 *   draft        - in-progress click points (area/line), owned by the parent
 *   onDraft      - setDraft
 *   onFinish     - (points, isLine) => void  - a complete shape was drawn
 *   editable     - gates all interaction
 *   selectedId   - highlighted zone
 *   onSelect     - (id) => void when a zone shape is clicked (view mode only)
 *   onEdit       - (zoneId, points) => void, live while dragging a vertex or
 *                  the whole shape of the SELECTED zone (parent updates state)
 *   onEditEnd    - (zoneId) => void when the drag ends (parent saves)
 */
export default function ZoneCanvas({ zones, drawMode, draft, onDraft, onFinish,
                                     editable, selectedId, onSelect,
                                     onEdit, onEditEnd }) {
  const svgRef = useRef(null);
  // The canvas's on-screen width:height. Measured rather than taken from the
  // camera's configured size: the element is laid out by CSS, and what a
  // circle has to look round in is the box it is actually painted in.
  const [aspect, setAspect] = useState(1);
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return undefined;
    const measure = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setAspect(r.width / r.height);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const [drag, setDrag] = useState(null);       // {start, end} for box/circle
  // Geometry edit drag on the selected zone: grab a vertex handle to move one
  // point, or the shape body to move every point together. Kept in a ref --
  // re-rendering per pointermove is the parent's business via onEdit.
  const edit = useRef(null);   // {zoneId, mode: 'vertex'|'body', index, start, orig}
  // Which zone is being dragged RIGHT NOW, so it can draw dotted while the
  // pointer is down and solid again the moment it is released. The ref above
  // already knows, but a ref does not re-render and this has to change with
  // the gesture.
  //
  // Dotted is the whole signal: one shape on screen, and its dashes say "this
  // is not settled yet". The old position used to be left behind as a second
  // outline for reference, and it read as a second zone -- two rectangles on
  // the picture, and no way to tell at a glance which one the app would keep.
  const [movingId, setMovingId] = useState(null);

  function svgPoint(evt) {
    const rect = svgRef.current.getBoundingClientRect();
    const x = (evt.clientX - rect.left) / rect.width;
    const y = (evt.clientY - rect.top) / rect.height;
    return [Math.min(1, Math.max(0, +x.toFixed(4))), Math.min(1, Math.max(0, +y.toFixed(4)))];
  }
  const isDragShape = drawMode === "box" || drawMode === "circle";
  const canEditGeometry = editable && !drawMode && !!onEdit;

  function onCanvasClick(evt) {
    if (!drawMode) {
      // Clicking EMPTY picture leaves setup mode: a zone's own shape stops
      // propagation, so a click landing here means "none of them".
      if (onSelect) onSelect(null);
      return;
    }
    if (!editable || isDragShape) return;
    const pt = svgPoint(evt);
    const next = [...draft, pt];
    if (drawMode === "line" && next.length === 2) { finish(next); return; }
    onDraft(next);
  }
  function onPointerDown(evt) {
    if (!isDragShape || !editable) return;
    evt.preventDefault();
    const pt = svgPoint(evt);
    setDrag({ start: pt, end: pt });
  }
  function onPointerMove(evt) {
    if (edit.current) { moveEdit(evt); return; }
    if (!drag || !isDragShape) return;
    setDrag((d) => (d ? { ...d, end: svgPoint(evt) } : d));
  }
  function onPointerUp() {
    if (edit.current) { endEdit(); return; }
    if (!drag || !isDragShape) return;
    const { start, end } = drag;
    setDrag(null);
    // Ignore an accidental tiny drag (a click that didn't move).
    if (Math.hypot(end[0] - start[0], end[1] - start[1]) < 0.02) return;
    finish([start, end]);
  }
  function finish(raw) {
    const pts = buildPoints(drawMode, raw, aspect)
      .map(([x, y]) => [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))]);
    onFinish(pts, drawMode === "line");
  }

  // -- geometry editing ------------------------------------------------------
  function startEdit(evt, zone, mode, index = -1) {
    if (!canEditGeometry) return;
    evt.stopPropagation();
    evt.preventDefault();
    svgRef.current.setPointerCapture?.(evt.pointerId);
    edit.current = { zoneId: zone.id, mode, index,
                     start: svgPoint(evt),
                     orig: zone.points.map((p) => [...p]) };
    setMovingId(zone.id);
  }
  function moveEdit(evt) {
    const e = edit.current;
    const pt = svgPoint(evt);
    if (e.mode === "vertex") {
      const next = e.orig.map((p, i) => (i === e.index ? pt : p));
      onEdit(e.zoneId, next);
      return;
    }
    // Whole-shape move: clamp the DELTA so the shape keeps its form at the
    // edges instead of squashing point-by-point against the border.
    let dx = pt[0] - e.start[0], dy = pt[1] - e.start[1];
    const xs = e.orig.map((p) => p[0]), ys = e.orig.map((p) => p[1]);
    dx = Math.max(-Math.min(...xs), Math.min(1 - Math.max(...xs), dx));
    dy = Math.max(-Math.min(...ys), Math.min(1 - Math.max(...ys), dy));
    onEdit(e.zoneId, e.orig.map(([x, y]) => [+(x + dx).toFixed(4), +(y + dy).toFixed(4)]));
  }
  function endEdit() {
    const e = edit.current;
    edit.current = null;
    setMovingId(null);
    if (e && onEditEnd) onEditEnd(e.zoneId);
  }

  return (
    <svg ref={svgRef} viewBox="0 0 1 1" preserveAspectRatio="none"
         onClick={onCanvasClick}
         onPointerDown={onPointerDown} onPointerMove={onPointerMove}
         onPointerUp={onPointerUp} onPointerLeave={onPointerUp}
         style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                  touchAction: "none",
                  cursor: drawMode ? "crosshair" : "default" }}>
      {zones.map((z) => {
        const color = zoneColor(z);
        const disabled = z.enabled === false;
        const sel = z.id === selectedId;
        // Held right now: dashes while the pointer is down, solid the moment
        // it lets go. A disabled zone dashes too -- a moving disabled zone is
        // both, and one dash pattern says both things.
        const moving = z.id === movingId;
        const common = {
          opacity: disabled ? 0.35 : 1,
          strokeDasharray: moving ? "0.018 0.012" : disabled ? "0.015" : undefined,
          onClick: (e) => { if (!drawMode && onSelect) { e.stopPropagation(); onSelect(z.id); } },
          // Selected shape drags as a whole; others click-to-select.
          onPointerDown: sel && canEditGeometry
            ? (e) => startEdit(e, z, "body") : undefined,
          // `!drawMode && (…)` evaluated to the BOOLEAN false while drawing,
          // and React rejects a non-object style prop outright - so starting a
          // second zone crashed the page as soon as an existing one re-rendered.
          style: drawMode
            ? undefined
            : sel && canEditGeometry ? { cursor: "move" }
            : onSelect ? { cursor: "pointer" } : undefined,
        };
        if (z.kind === "line") {
          const [[x1, y1], [x2, y2]] = z.points;
          // The face arrow: which side of this line the app calls IN. Without
          // it the direction lives only in the order two clicks happened to
          // land in, which nothing on screen ever said.
          const face = lineFace(z, aspect);
          // The crossing threshold as a slab, on the SELECTED line only: how
          // far past the line an object must move before it has crossed
          // (app/zones.py Zone.band, percent of picture width, either side).
          // Drawn so an installer sees what 3% means on THIS camera rather
          // than guessing at a number. A plain strokeWidth would be
          // anisotropic in the 0..1 viewBox (the same reason lineFace
          // corrects the arrow), so the offset is built along the
          // pixel-space normal and converted back per axis. Feedback only.
          const slab = sel && (() => {
            const b = (z.band_pct ?? 1.2) / 100;
            const k = aspect > 0 ? aspect : 1;
            const dx = x2 - x1, dy = y2 - y1;
            const m = Math.hypot(dy, dx * k) || 1;
            const ox = -b * dy / m, oy = b * k * k * dx / m;
            return [[x1 + ox, y1 + oy], [x2 + ox, y2 + oy],
                    [x2 - ox, y2 - oy], [x1 - ox, y1 - oy]]
              .map((p) => p.join(",")).join(" ");
          })();
          return (
            <g key={z.id}>
              {slab && (
                <polygon points={slab} fill={color} fillOpacity={0.15}
                         stroke="none" pointerEvents="none" />
              )}
              <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color}
                    strokeWidth={sel ? "0.010" : "0.006"} {...common} />
              {face && (() => {
                const L = 0.075;
                const [mx, my] = face.mid;
                const [nx, ny] = face.normal;
                // The TIP is on the line and the tail is out on the way-in
                // side: the arrow is the movement that gets counted.
                const sx = mx - nx * L, sy = my - ny * L;
                // Head: the tip, plus two points stepped back along the
                // normal and out along the line itself.
                const bx = mx - nx * L * 0.38, by = my - ny * L * 0.38;
                const hx = -ny * 0.022 * (aspect > 0 ? 1 / aspect : 1);
                const hy = nx * 0.022;
                return (
                  <g style={{ pointerEvents: "none" }} opacity={disabled ? 0.35 : 0.9}>
                    <line x1={sx} y1={sy} x2={mx} y2={my} stroke={color}
                          strokeWidth={sel ? "0.008" : "0.005"} />
                    <polygon points={`${mx},${my} ${bx + hx},${by + hy} ${bx - hx},${by - hy}`}
                             fill={color} />
                  </g>
                );
              })()}
            </g>
          );
        }
        return <polygon key={z.id} points={z.points.map((p) => p.join(",")).join(" ")}
                        // Lighter fill while it is being moved, so the dashes
                        // read as dashes rather than as an edge on a solid.
                        fill={color + (moving ? "22" : sel ? "55" : "33")} stroke={color}
                        strokeWidth={sel ? "0.007" : "0.004"} {...common} />;
      })}

      {/* vertex handles on the selected zone: drag one to reshape */}
      {canEditGeometry && zones.filter((z) => z.id === selectedId).map((z) =>
        z.points.map((p, i) => (
          <circle key={`${z.id}-h${i}`} cx={p[0]} cy={p[1]} r="0.011"
                  fill="#fff" stroke={zoneColor(z)} strokeWidth="0.004"
                  style={{ cursor: "grab" }}
                  onPointerDown={(e) => startEdit(e, z, "vertex", i)} />
        )))}
      {draft.length > 0 && (
        <polyline points={draft.map((p) => p.join(",")).join(" ")}
                  fill="none" stroke="#fff" strokeWidth="0.005" strokeDasharray="0.01" />
      )}
      {draft.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="0.008" fill="#fff" />)}
      {/* live drag preview for box / circle */}
      {drag && drawMode === "box" && (
        <rect x={Math.min(drag.start[0], drag.end[0])} y={Math.min(drag.start[1], drag.end[1])}
              width={Math.abs(drag.end[0] - drag.start[0])} height={Math.abs(drag.end[1] - drag.start[1])}
              fill="#ffffff22" stroke="#fff" strokeWidth="0.004" strokeDasharray="0.01" />
      )}
      {drag && drawMode === "circle" && (() => {
        // An ELLIPSE in the stretched unit space is what renders as a circle
        // on screen -- a <circle> here would be drawn oval by exactly the
        // aspect, and previewing a different shape from the one that gets
        // created is worse than no preview.
        const k = aspect > 0 ? aspect : 1;
        const r = Math.hypot((drag.end[0] - drag.start[0]) * k,
                             drag.end[1] - drag.start[1]);
        return (
          <ellipse cx={drag.start[0]} cy={drag.start[1]} rx={r / k} ry={r}
                   fill="#ffffff22" stroke="#fff" strokeWidth="0.004"
                   strokeDasharray="0.01" />
        );
      })()}
    </svg>
  );
}
