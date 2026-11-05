// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Generic analytics widget - renders by WidgetSpec.kind, never by system slug.
// A system package declares WidgetSpecs (data_fn → JSON); this switch turns each
// kind into UI. A new kind is one case here (product repo, generic), matching
// the old analytics.js/analytics.html behaviour. Optional data hints: color,
// max, unit.
//
// Two axes decide how a widget looks:
//   kind      WHAT it draws  -- one number, a line, a donut, a table.
//   template  how it is DRESSED -- see TEMPLATES in core/services/builder.py.
// A template a kind does not understand falls through to the plain treatment,
// so changing a widget's kind can never leave it rendering as nothing.
import {
  ResponsiveContainer, LineChart, AreaChart, BarChart, PieChart, RadarChart,
  Line, Area, Bar, Pie, Cell, Radar,
  XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, LabelList,
  PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from "recharts";
import { useEffect, useRef, useState } from "react";
import { SERIES_COLORS, hexToRgba, readableTextOn, toRows, dayOf, daySpans,
         dayStops, GRID, TOOLTIP, AXIS_LINE, AXIS_TEXT, CHAR_PX,
         fmtValue, truncateLabel, categoryLayout, showValues } from "../charts.js";
import StreamImg, { withParams } from "../StreamImg.jsx";
import OverlayCanvas from "../OverlayCanvas.jsx";
import DetectGate from "../DetectGate.jsx";
import { gateFor } from "../../lib/viewOnDetect.js";
import { ChevronLeft, ChevronRight, Download, VideoOff } from "lucide-react";
import { usePoll } from "../../api/hooks.js";
import TableCsvDialog from "./TableCsvDialog.jsx";
import { apiUrl } from "../../lib/base.js";
import { STAT_LAYOUT_KINDS, statLayout, titleShown } from "./cardFields.jsx";
//: A stat's alignment and position (cardFields.statLayout) as flex values.
const FLEX_OF = { left: "flex-start", center: "center", right: "flex-end",
                  top: "flex-start", middle: "center", bottom: "flex-end" };

/**
 * `fill` is set when the widget sits in a PLACED grid cell - the Builder canvas
 * and the dashboards it produces. The widget then takes exactly the height the
 * operator dragged instead of imposing its own, which is what keeps "what I
 * arranged" and "what I see" the same picture. Without it (auto-flow layouts)
 * each kind keeps its natural height, as before.
 */
//: How long a drawn-on animation takes, and how far apart a stagger spaces
//: its series. Long enough to read as motion, short enough that a dashboard
//: polling every few seconds is never mid-animation when the next poll lands.
const ANIM_MS = 700;
const STAGGER_MS = 160;

/** Hard-stop gradient stops for a line coloured by threshold band.
 *
 *  N thresholds cut the value axis into N+1 bands; the line takes the colour
 *  of the band it is passing through, so "we crossed the limit at 14:20" is
 *  read off the picture instead of counted off the axis.
 *
 *  The offsets are computed against the LINE's own value range, not the
 *  chart's axis, and that is deliberate rather than an approximation. An SVG
 *  gradient defaults to `objectBoundingBox`, so 0 and 1 are the top and bottom
 *  of the shape being painted -- for a stroke, the line's own extent. Using
 *  the axis range here would put every colour change in the wrong place on any
 *  chart whose data does not happen to fill its axis, and the alternative
 *  (`userSpaceOnUse`) needs the plot area in PIXELS, which nothing inside
 *  <defs> knows until after layout.
 *
 *  Each boundary emits TWO stops at the same offset: a gradient interpolates
 *  between stops, and a threshold that fades over ten pixels is a threshold
 *  nobody can point at.
 */
/** The next clean axis top at or above `v`: 9.72 -> 10, 3.24 -> 4, 32 -> 40.
 *  A chart with a limit line needs headroom above the limit, and the axis
 *  used to end exactly where the headroom did, printing a figure nobody set. */
function niceTop(v) {
  if (!Number.isFinite(v) || v <= 0) return v;
  const mag = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    // toPrecision: 1.2 * 0.1 is 0.12000000000000001, and an axis tick that
    // reads like that is worse than 9.72.
    if (m * mag >= v) return Number((m * mag).toPrecision(12));
  }
  return Number((10 * mag).toPrecision(12));
}

function bandStops(at, colors, lo, hi) {
  const span = hi - lo;
  // A flat line has no extent to spread a gradient over. It still has a
  // value, so it still has a band: whichever one it is sitting in.
  if (!(span > 0)) {
    let i = 0;
    while (i < at.length && lo >= at[i]) i += 1;
    return [{ off: 0, color: colors[i] }, { off: 1, color: colors[i] }];
  }
  const clamp = (v) => Math.min(1, Math.max(0, v));
  const n = at.length;
  const stops = [{ off: 0, color: colors[n] }];   // top of the picture
  for (let i = n - 1; i >= 0; i -= 1) {
    const off = clamp((hi - at[i]) / span);
    stops.push({ off, color: colors[i + 1] });    // last of the band above
    stops.push({ off, color: colors[i] });        // first of the band below
  }
  stops.push({ off: 1, color: colors[0] });
  return stops;
}

/** The value box a click opens on a pie slice, a bar or a radar shape: where
 *  the click was, over the chart, dismissed by a click anywhere else. Not the
 *  hover tooltip -- that floated over the chart whenever the mouse passed. */
function ChartPopup({ pop }) {
  if (!pop) return null;
  return (
    <div className="chart-popup" data-testid="chart-popup"
         style={{ ...TOOLTIP.contentStyle, position: "absolute", left: pop.x, top: pop.y,
                  transform: "translate(-50%, calc(-100% - 10px))", pointerEvents: "none",
                  zIndex: 5, whiteSpace: "nowrap" }}>
      {pop.lines.map((l, i) => <div key={i} style={{ color: l.color }}>{l.text}</div>)}
    </div>
  );
}

/** A click's place inside the chart's wrapper. */
function placeIn(ref, e) {
  const r = ref.current && ref.current.getBoundingClientRect ? ref.current.getBoundingClientRect() : null;
  const x = e && e.clientX != null ? e.clientX : 0;
  const y = e && e.clientY != null ? e.clientY : 0;
  return r ? { x: x - r.left, y: y - r.top } : { x: 0, y: 0 };
}

/** Which line of a chart a click means: the one whose curve passes nearest the
 *  click, within a finger's width; null when the click was out in the empty
 *  plot. Measured on the drawn paths themselves, so it holds for every way a
 *  series can be drawn (line, area, forecast, banded). `ref` is the chart's
 *  wrapper; a line's elements carry the class chart-ser-<index>. */
const PICK_REACH_PX = 28;
export function nearestLine(ref, e, count, filled = false) {
  const box = ref && ref.querySelector ? ref : null;
  if (!box) return null;
  const direct = e.target && e.target.closest ? e.target.closest("[class*='chart-ser-']") : null;
  const idOf = (el) => {
    const m = /chart-ser-(\d+)/.exec(String(el.getAttribute("class") || ""));
    return m ? Number(m[1]) : null;
  };
  const svg = box.querySelector("svg.recharts-surface");
  const ctm = svg && svg.getScreenCTM ? svg.getScreenCTM() : null;
  if (!ctm || !svg.createSVGPoint) return direct ? idOf(direct) : null;
  const pt = svg.createSVGPoint();
  pt.x = e.clientX;
  pt.y = e.clientY;
  const at = pt.matrixTransform(ctm.inverse());
  let best = null;
  let bestD = PICK_REACH_PX;
  // Each line's height at the click's x: a click INSIDE a filled area belongs
  // to the line above it, however far from that line it landed.
  const heights = [];
  for (let i = 0; i < count; i += 1) {
    let nearX = Infinity;
    let curveY = null;
    box.querySelectorAll(`.chart-ser-${i} path.recharts-line-curve, .chart-ser-${i} path.recharts-area-curve, path.chart-ser-${i}`)
      .forEach((path) => {
        if (!path.getTotalLength) return;
        const len = path.getTotalLength();
        const step = Math.max(2, len / 400);
        for (let l = 0; l <= len; l += step) {
          const p = path.getPointAtLength(l);
          const d = Math.hypot(p.x - at.x, p.y - at.y);
          if (d < bestD) {
            bestD = d;
            best = i;
          }
          const dx = Math.abs(p.x - at.x);
          if (dx < nearX) {
            nearX = dx;
            curveY = p.y;
          }
        }
      });
    if (curveY !== null) heights.push({ i, y: curveY });
  }
  if (best !== null) return best;
  if (filled) {
    const above = heights.filter((h) => h.y <= at.y).sort((a, b) => b.y - a.y);
    if (above.length) return above[0].i;
  }
  return direct ? idOf(direct) : null;
}

/** Recharts' animation props for one shape.
 *
 *  Every series used to say `isAnimationActive={false}`, which is why nothing
 *  ever moved: the charts were built for a wall display that must not twitch.
 *  That stays the default -- this returns exactly the old props when the
 *  widget asked for no animation -- and only a deliberate choice turns it on.
 *
 *  `key`/`all` are the series' own key and the full series list, which is
 *  what a stagger needs: its position in that list is its delay.
 */
function animProps(animated, anim, key, all) {
  if (!animated) return { isAnimationActive: false };
  const i = anim === "stagger" && all
    ? Math.max(0, all.findIndex((s) => s.key === key)) : 0;
  return {
    isAnimationActive: true,
    animationDuration: ANIM_MS,
    animationBegin: i * STAGGER_MS,
    // "ease" reads as a shape settling; recharts' default "ease" is fine for
    // the draw animations and for the update tween both.
    animationEasing: "ease",
  };
}

/** A LOLLIPOP bar (the owner's second reference picture): a thin stem from
 *  the axis to the value and a dot at its end, instead of a filled bar. A
 *  recharts Bar `shape`, so the layout, the scale and the tooltip are the
 *  bar's own; `horizontal` is a bar chart laid out with categories on the
 *  left. Classed so a test (and the theme) can find it. */
function Lollipop({ x, y, width, height, fill, horizontal }) {
  if (![x, y, width, height].every(Number.isFinite)) return null;
  if (horizontal) {
    const cy = y + height / 2;
    const r = Math.max(3, Math.min(6, height / 2.5));
    return (
      <g className="chart-lollipop">
        <line x1={x} y1={cy} x2={x + width} y2={cy} stroke={fill} strokeWidth={2} />
        <circle cx={x + width} cy={cy} r={r} fill={fill} />
      </g>
    );
  }
  const cx = x + width / 2;
  const r = Math.max(3, Math.min(6, width / 2.5));
  return (
    <g className="chart-lollipop">
      <line x1={cx} y1={y + height} x2={cx} y2={y} stroke={fill} strokeWidth={2} />
      <circle cx={cx} cy={y} r={r} fill={fill} />
    </g>
  );
}

/** The colour of the band `v` falls in: N limits, N+1 colours, a value ON a
 *  limit belongs to the band above it (the same reading as bandStops). */
function bandFill(v, bands) {
  const at = bands.at || [];
  let i = 0;
  while (i < at.length && v >= at[i]) i += 1;
  return (bands.colors || [])[i];
}

//: Where a viewer's hidden legend entries are remembered for the session,
//: per chart. View-only: never the saved chart, never another viewer's.
const HIDDEN_KEY = (id) => `iriv.chart.hidden.${id}`;
function readHidden(id) {
  try {
    const raw = window.sessionStorage.getItem(HIDDEN_KEY(id));
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.map(String) : [];
  } catch {
    return [];                                      // no storage: start shown
  }
}
function writeHidden(id, list) {
  try {
    if (list.length) window.sessionStorage.setItem(HIDDEN_KEY(id), JSON.stringify(list));
    else window.sessionStorage.removeItem(HIDDEN_KEY(id));
  } catch {
    /* no storage: the choice lasts as long as the card is on screen */
  }
}

/** THE LEGEND, AS SWITCHES (owner, 2026-10-13: "allow users to show or hide
 *  individual data series directly from the chart legend ... so users can
 *  focus on one camera"). One button per series or slice, in its own colour
 *  (bound to the series, never to its visible position): pressed = shown;
 *  a hidden one stays listed, dimmed and struck through. The last one shown
 *  cannot be hidden -- a chart showing nothing reads as broken -- and "Show
 *  all" appears once anything is hidden. Native buttons, so Enter and Space
 *  work and focus is visible. What it changes is this VIEWER's picture
 *  only; an input hidden in the chart's settings is not in the data at all
 *  and so is never listed here. */
function ToggleLegend({ items, hidden, onToggle, onShowAll, compact }) {
  if (!items.length) return null;
  const shown = items.filter((it) => !hidden.includes(it.key)).length;
  const anyHidden = items.some((it) => hidden.includes(it.key));
  return (
    <div className="chart-legend" role="group"
         aria-label="Series on the chart - press one to show or hide it"
         style={{ fontSize: compact ? 10 : 11 }}>
      {items.map((it) => {
        const on = !hidden.includes(it.key);
        const last = on && shown <= 1;
        return (
          <button key={it.key} type="button" className="chart-legend-item chart-legend-toggle"
                  aria-pressed={on}
                  aria-disabled={last || undefined}
                  aria-label={`${it.label}, ${on ? "shown" : "hidden"}. ${last
                    ? "The last series shown stays on the chart."
                    : `Press to ${on ? "hide" : "show"}.`}`}
                  title={last ? "At least one series stays shown" : (on ? "Hide" : "Show")}
                  onClick={() => { if (!last) onToggle(it.key); }}
                  style={{ background: "none", border: 0, padding: "1px 3px",
                           font: "inherit", color: "inherit", borderRadius: 4,
                           cursor: last ? "default" : "pointer",
                           opacity: on ? 1 : 0.45,
                           textDecoration: on ? "none" : "line-through" }}>
            <i aria-hidden="true" style={{ background: it.color }} />
            {it.label}
            {it.mark}
          </button>
        );
      })}
      {anyHidden && (
        <button type="button" className="chart-legend-all" onClick={onShowAll}
                style={{ background: "none", border: 0, padding: "1px 3px",
                         font: "inherit", color: "inherit", cursor: "pointer",
                         textDecoration: "underline" }}>
          Show all
        </button>
      )}
    </div>
  );
}

/** Type styling for a tile whose value is WORDS rather than a number, and
 *  nothing at all for a number.
 *
 *  A stat tile is sized for "47". The same size makes "violate" fill the card
 *  and a shift name overflow it, so text steps down and is allowed to wrap --
 *  in two steps, because "OK" wants to stay big and a sentence cannot.
 */
function textValue(value, scale = 1) {
  if (typeof value !== "string" || value === "" || !Number.isNaN(Number(value))) {
    return null;
  }
  const long = value.length > 12;
  // Multiplied here rather than left to the stylesheet: this size is INLINE,
  // so it wins over .stat-value's calc() and would have ignored the setting
  // on exactly the cards whose value is a word.
  return { fontSize: `${(long ? 1.15 : 1.6) * scale}rem`, lineHeight: 1.25,
           whiteSpace: "normal", overflowWrap: "anywhere" };
}

/** The multiplier a named size means. Medium is 1 -- every card was drawn at
 *  medium before this existed, so a layout that says nothing is unchanged. */
export const TEXT_SCALE = { small: 0.8, medium: 1, large: 1.3, huge: 1.7 };
const scaleOf = (name) => TEXT_SCALE[name] || 1;

/**
 * A workflow INJECT, as a button on a page: pressing it fires that Inject on
 * the running app, through the same route (and the same workflow.edit check)
 * as the button on the canvas node.
 *
 * Disabled -- with the reason as its tooltip -- when the viewer may not press
 * it (`can_press`, added by the route that knows who is looking) or when its
 * node has been deleted from the workflow (`removed`, marked on the next
 * workflow save), which is said on the card instead of leaving a button that
 * presses nothing.
 */
function InjectButton({ widget, data, fill }) {
  const [state, setState] = useState({ busy: false, said: "", ok: true });
  // A REPEATING Inject is Start / Stop, like its node. What the last press
  // asked is shown until the next poll reports the app's own state, so the
  // button does not flip back for the few seconds in between.
  const [asked, setAsked] = useState(null);
  const repeat = !!data.repeat;
  const running = asked === null ? !!data.running : asked;
  if (asked !== null && !!data.running === asked) setAsked(null);
  const action = !repeat ? "press" : running ? "stop" : "start";
  const removed = !!data.removed;
  const why = removed ? "this button's Inject node was removed"
    : !data.node ? "not linked to an Inject node"
      : data.can_press === false ? "You need the right to edit the workflow to press this"
        : "";
  const press = async () => {
    setState({ busy: true, said: "", ok: true });
    try {
      // Imported at press time, not at load: the widget module is also drawn
      // by page-less renderers (grid geometry, print), and the api client
      // reads the browser session the moment it loads.
      const { api } = await import("../../api/client.js");
      await api.post("/api/workflow/inject", { node: data.node, action });
      if (action !== "press") setAsked(action === "start");
      setState({ busy: false,
                 said: { press: "injected", start: "started",
                         stop: "stopped" }[action], ok: true });
    } catch (e) {
      setState({ busy: false, said: e.message || "did not fire", ok: false });
    }
  };
  return (
    <div data-testid="inject-box"
         style={fill
           // A placed card: the button takes the whole card, and its label grows
           // with the card (container units), so resizing the card resizes it.
           ? { display: "flex", flexDirection: "column", gap: 6, height: "100%",
               minHeight: 0, containerType: "size" }
           : { display: "grid", gap: 6, justifyItems: "start" }}>
      <button type="button" className="btn btn-accent" data-action={action}
              style={fill ? { flex: 1, minHeight: 36, width: "100%",
                              fontSize: "clamp(14px, 18cqmin, 72px)" } : undefined}
              disabled={!!why || state.busy}
              title={why || { press: "Inject now", start: "Start the repeat",
                              stop: "Stop the repeat" }[action]}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); press(); }}>
        {state.busy ? "…"
          : repeat ? `${running ? "■ Stop" : "▶ Start"} ${widget.title || "Inject"}`
            : (widget.title || "Inject")}
      </button>
      {repeat && !removed && (
        <div className="stat-sub">{running ? "running" : "stopped"}</div>
      )}
      {removed ? (
        <div className="stat-sub c-warn">this button&apos;s Inject node was removed</div>
      ) : (
        <div className="stat-sub"
             style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <span>
            {state.said ? (
              <span style={{ color: state.ok ? "var(--ok, #16a34a)"
                                             : "var(--alert, #dc2626)" }}>
                {state.said}
              </span>
            ) : (data.runs ? "injected" : "not injected yet")}
          </span>
          {typeof data.detect_s === "number" && (
            // How long the chip took to detect the last snap, at the row's right end.
            <span data-testid="inject-detect-time"
                  style={{ fontVariantNumeric: "tabular-nums" }}>
              {data.detect_s.toFixed(2)} s
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** The api client, imported at use time for the reason InjectButton gives. */
async function postJson(url, body) {
  const { api } = await import("../../api/client.js");
  return api.post(url, body);
}

//: A control on a placed grid card must not start a drag of the card.
const keepClick = { onMouseDown: (e) => e.stopPropagation() };

/**
 * "AI model classes": which classes the system's AI model keeps, as a
 * dropdown of ticks. NOT a copy of the setting: it reads the very value the
 * Models page's Detection settings card reads (data.models, refreshed on
 * every dashboard poll) and saves through that card's own route, so a change
 * on either page is what the other shows next.
 *
 * Ticks are gathered and sent with one Apply, not one save per tick: each
 * save reloads the detector in the running app, and eight ticks would be
 * eight reloads. None ticked means every class, as on the Models page.
 *
 * `data.locked` while AI detection is not fully stopped: classes are added or
 * removed only with it stopped (core/services/class_lock.py, and the route
 * answers 409 regardless). The ticks grey out, a try says why and changes
 * nothing, and the next dashboard poll after Stop opens them again.
 */
function AiClassesCard({ data }) {
  const models = Array.isArray(data.models) ? data.models : [];
  const can = data.can_edit !== false;
  const locked = !!data.locked;
  const lockSaid = data.locked_reason || "Stop AI detection to add or remove classes";
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState("");
  const [picked, setPicked] = useState(null);      // null = follow the server
  const [asked, setAsked] = useState(null);        // what the last Apply sent
  const [query, setQuery] = useState("");
  const [state, setState] = useState({ busy: false, said: "", ok: true });
  // The reason goes with the lock: the poll that opens the ticks clears it.
  if (!locked && state.said === lockSaid) setState({ busy: false, said: "", ok: true });
  const cur = models.find((m) => m.model === sel) || models[0];
  if (!cur) {
    return <div className="stat-sub">No AI model is chosen for this system yet.</div>;
  }
  const server = (cur.classes || []).map(String);
  const same = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
  if (asked !== null && same(asked, server)) setAsked(null);
  const shown = picked ?? asked ?? server;
  const labels = (cur.labels || []).map(String);
  const q = query.trim().toLowerCase();
  const rows = labels.filter((c) => !q || c.toLowerCase().includes(q));
  const toggle = (c) => {
    if (!can) return;
    if (locked) { setState({ busy: false, said: lockSaid, ok: false }); return; }
    setPicked(shown.includes(c) ? shown.filter((x) => x !== c) : [...shown, c]);
  };
  const apply = async () => {
    if (locked) { setState({ busy: false, said: lockSaid, ok: false }); return; }
    setState({ busy: true, said: "", ok: true });
    try {
      await postJson(apiUrl("/api/detector/control/params"),
                     { classes: picked || [], ...(cur.model ? { model: cur.model } : {}) });
      setAsked(picked || []);
      setPicked(null);
      setState({ busy: false, ok: true,
                 said: "saved - the AI uses it within a few seconds" });
    } catch (e) {
      setState({ busy: false, said: e.message || "not saved", ok: false });
    }
  };
  const summary = shown.length
    ? `Detecting ${shown.slice(0, 4).join(", ")}`
      + (shown.length > 4 ? ` and ${shown.length - 4} more` : "")
    : "Detecting every class";
  return (
    <div data-ai-classes="1" {...keepClick}
         style={{ display: "grid", gap: 6, minWidth: 0, width: "100%" }}>
      {models.length > 1 && (
        <select className="input" aria-label="AI model" value={cur.model}
                onChange={(e) => { setSel(e.target.value); setPicked(null); }}>
          {models.map((m) => <option key={m.model} value={m.model}>{m.name || m.model}</option>)}
        </select>
      )}
      <div className="stat-sub" style={{ overflowWrap: "anywhere" }}>{summary}</div>
      <button type="button" className="btn btn-sm" aria-expanded={open}
              style={{ justifySelf: "start" }}
              onClick={(e) => { e.stopPropagation(); setOpen(!open); }}>
        {can ? "Choose classes" : "Show classes"} {open ? "▴" : "▾"}
      </button>
      {open && (
        <div role="group" aria-label="AI model classes"
             style={{ border: "1px solid var(--line, rgba(128,128,128,.25))",
                      borderRadius: 8, padding: 6, display: "grid", gap: 6,
                      background: "var(--surface, transparent)" }}>
          {labels.length === 0 ? (
            <div className="muted small">
              This model has no list of classes, so every detection is kept.
            </div>
          ) : (
            <>
              {labels.length > 12 && (
                <input className="input" placeholder="Search classes…" value={query}
                       aria-label="Search classes"
                       onChange={(e) => setQuery(e.target.value)} />
              )}
              <div style={{ maxHeight: 180, overflowY: "auto" }}>
                {rows.map((c) => (
                  <label key={c} style={{ display: "flex", alignItems: "center",
                                          gap: 8, padding: "4px 4px", fontSize: 13,
                                          cursor: can && !locked ? "pointer" : "default" }}
                         // A greyed tick fires nothing; the row still answers
                         // a click on a locked card with the reason.
                         onClick={locked && can ? () => toggle(c) : undefined}>
                    <input type="checkbox" checked={shown.includes(c)}
                           disabled={!can || locked || state.busy} onChange={() => toggle(c)} />
                    <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{c}</span>
                  </label>
                ))}
                {rows.length === 0 && (
                  <div className="muted small">No class matches “{query}”.</div>
                )}
              </div>
            </>
          )}
          {can && locked && (
            <div className="muted small" data-class-lock="1">{lockSaid}.</div>
          )}
          {can ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button type="button" className="btn btn-sm btn-accent"
                      disabled={picked === null || locked || state.busy} onClick={apply}>
                {state.busy ? "Saving…" : "Apply"}
              </button>
              {shown.length > 0 && (
                <button type="button" className="btn btn-sm" disabled={locked || state.busy}
                        onClick={() => setPicked([])}>Every class</button>
              )}
              {picked !== null && (
                <button type="button" className="btn btn-sm" disabled={state.busy}
                        onClick={() => setPicked(null)}>Cancel</button>
              )}
            </div>
          ) : (
            <div className="muted small">
              View only: changing the classes needs the right to choose AI models.
            </div>
          )}
        </div>
      )}
      {state.said && (
        <div className="stat-sub" style={{ color: state.ok ? "var(--ok, #16a34a)"
                                                           : "var(--alert, #dc2626)" }}>
          {state.said}
        </div>
      )}
    </div>
  );
}

/**
 * "Class filter": the workflow's NAMED Class Filters, one switch each -- a
 * green "Running" pill while it runs, a silver "Paused" one when it does not.
 * Switch one off and it is paused -- the rules and outputs after it stop, and
 * their cards say "paused" rather than 0. Every filter runs until someone
 * uses this. A switch is cheap (no detector reload), so it is sent at once;
 * a native <button role="switch"> so Space and Enter work with no key code.
 * Not ui/switch.jsx: that is an 18 px unlabelled track, and this one has to
 * say its state in words and be a 32 px touch target. Looks: .cf-* in theme.css.
 * what it asked is shown until the next poll reports the setting.
 */
function ClassFilterCard({ data }) {
  const filters = Array.isArray(data.filters) ? data.filters : [];
  const can = data.can_edit !== false;
  const [asked, setAsked] = useState({});
  const [state, setState] = useState({ busy: "", said: "", ok: true });
  const running = (f) => (f.node in asked ? asked[f.node] : !!f.running);
  for (const f of filters) {
    if (f.node in asked && asked[f.node] === !!f.running) {
      const { [f.node]: _gone, ...rest } = asked;
      setAsked(rest);
      break;
    }
  }
  if (!filters.length) {
    return (
      <div className="stat-sub">
        No named Class Filters yet. Name the Class Filters on the Workflow page
        to switch them here.
      </div>
    );
  }
  const flip = async (f) => {
    const want = !running(f);
    setState({ busy: f.node, said: "", ok: true });
    try {
      await postJson(apiUrl("/api/workflow/class-filter"), { node: f.node, running: want });
      setAsked((a) => ({ ...a, [f.node]: want }));
      setState({ busy: "", ok: true,
                 said: `${f.name} ${want ? "runs again" : "is paused"}` });
    } catch (e) {
      setState({ busy: "", said: e.message || "not changed", ok: false });
    }
  };
  return (
    <div data-class-filters="1" {...keepClick}
         style={{ display: "grid", gap: 4, minWidth: 0, width: "100%" }}>
      <div role="group" aria-label="Class filters" className="cf-list">
        {filters.map((f) => {
          const on = running(f);
          const busy = state.busy === f.node;
          const nameId = `cf-${f.node}-name`;
          return (
            <div key={f.node} className="cf-row">
              <span id={nameId} className="cf-name">{f.name}</span>
              <button type="button" role="switch" aria-checked={on}
                      aria-labelledby={nameId} aria-busy={busy || undefined}
                      data-node={f.node} data-busy={busy ? "1" : undefined}
                      className={`cf-toggle ${on ? "is-on" : "is-off"}${can ? "" : " is-readonly"}`}
                      // One state with the node's own On / Off on the canvas.
                      title={on ? "Running - On on its workflow node too"
                                : "Paused - Off on its workflow node too"}
                      disabled={!can || !!state.busy}
                      onClick={() => flip(f)}>
                <span className="cf-track" aria-hidden="true">
                  <span className="cf-knob" />
                </span>
                <span className="cf-label">{on ? "Running" : "Paused"}</span>
              </button>
            </div>
          );
        })}
      </div>
      {!can && (
        <div className="muted small">
          View only: switching Class Filters needs the right to edit the workflow.
        </div>
      )}
      {state.said && (
        <div className="stat-sub" style={{ color: state.ok ? "var(--ok, #16a34a)"
                                                           : "var(--alert, #dc2626)" }}>
          {state.said}
        </div>
      )}
    </div>
  );
}

/** CAMERA SNAPS card: the newest snaps a Snapshot Detection Stream detected
 *  (the same pictures as Camera Setup's "Camera snaps" and the Footage page),
 *  one at a time with Previous / Next. Newest first; at most `count` (20 cap).
 *  The list is the app's own snapshot list, filtered to snaps and, when the
 *  card names one, to that camera. */
function SnapGalleryCard({ data, fill }) {
  const { data: list } = usePoll("/api/detector/snapshots?limit=300", 3000);
  const [at, setAt] = useState(0);
  const cam = String(data?.camera || "");
  const max = Math.max(1, Math.min(20, Number(data?.count) || 10));
  const rows = (list?.snapshots || [])
    .filter((r) => (r.kind === "snap" || String(r.id).includes("_snp_"))
      && (!cam || String(r.camera) === cam))
    .slice(0, max);
  const i = Math.min(at, Math.max(0, rows.length - 1));
  const cur = rows[i];
  const when = (r) => new Date(r.mtime * 1000).toLocaleString();
  const btn = (disabled) => ({
    border: "1px solid var(--line, rgba(128,128,128,.3))", background: "transparent",
    color: "inherit", borderRadius: 6, padding: "2px 8px", cursor: disabled ? "default" : "pointer",
    opacity: disabled ? 0.4 : 1, display: "inline-flex", alignItems: "center", gap: 2,
  });
  return (
    <div data-testid="snap-gallery"
         style={{ display: "flex", flexDirection: "column", gap: 6,
                  ...(fill ? { height: "100%", minHeight: 0 } : null) }}>
      <div style={{ background: "#000", borderRadius: 6, overflow: "hidden",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    ...(fill ? { flex: 1, minHeight: 0 } : { aspectRatio: "16 / 9" }) }}>
        {cur ? (
          <img src={apiUrl(`/api/detector/snapshots/${encodeURIComponent(cur.id)}`)}
               alt={`Snap ${when(cur)}`}
               style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }} />
        ) : (
          <div className="muted small" style={{ padding: 12, textAlign: "center" }}>
            Nothing snapped yet
          </div>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    gap: 8, flexShrink: 0 }}>
        <button type="button" aria-label="Previous snap" disabled={i <= 0}
                onClick={() => setAt(i - 1)} style={btn(i <= 0)}>
          <ChevronLeft size={14} /> Newer
        </button>
        <span className="muted small" style={{ textAlign: "center" }}>
          {cur ? `${i + 1} of ${rows.length} - ${when(cur)}` : "0 of 0"}
        </span>
        <button type="button" aria-label="Next snap" disabled={i >= rows.length - 1}
                onClick={() => setAt(i + 1)} style={btn(i >= rows.length - 1)}>
          Older <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

//: Text and Image cards (builder.STATIC_KINDS): drawn from their own fields,
//: never from data, so they draw even before (or without) a preview answer.
export const STATIC_CARD_KINDS = ["text_card", "image_card"];
const TEXT_CARD_PX = { small: 12, medium: 15, large: 20 };
const ASSET_ID = /^[0-9a-f]{32}$/;

/** A Text card: the operator's own words. Rendered as TEXT -- React escapes
 *  it, so "<script>" reads as those characters; nothing is parsed as HTML or
 *  markdown and no link is clickable. Line breaks kept; a long text scrolls
 *  inside the card rather than spilling out of its cell. */
function TextCard({ w, fill }) {
  const body = String(w.body || "");
  // Above / Middle / Below in the card (Tok Aba, 2026-10-14). A Text card
  // always sat at the top, so that is what an unset one keeps.
  const valign = statLayout("text_card", w.align, w.valign).valign;
  return (
    <div data-testid="text-card-box" data-valign={valign}
         style={{ display: "flex", flexDirection: "column", justifyContent: FLEX_OF[valign],
                  ...(fill ? { flex: 1, minHeight: 0 } : null) }}>
    <div data-testid="text-card"
         style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", overflowY: "auto",
                  ...(fill ? { minHeight: 0, maxHeight: "100%" } : { maxHeight: 320 }),
                  textAlign: ["left", "center", "right"].includes(w.align) ? w.align : "left",
                  fontWeight: w.bold ? 700 : 400,
                  fontSize: TEXT_CARD_PX[w.text_size] || TEXT_CARD_PX.medium,
                  lineHeight: 1.45, color: body ? "var(--text, #e8eaed)" : "var(--muted-text, #94a3b8)" }}>
      {body || "Write the words in the panel on the right."}
    </div>
    </div>
  );
}

/** An Image card: a picture uploaded to this box, by its asset id only (the
 *  route checks the session, sniffs the type and sends nosniff). A picture
 *  that is not on this box -- a template copied from another one -- says so. */
function ImageCard({ w, fill }) {
  const [broken, setBroken] = useState(false);
  const ok = ASSET_ID.test(String(w.asset || "")) && !broken;
  const note = (text) => (
    <div className="muted small" style={{ textAlign: "center", padding: 12 }}>{text}</div>
  );
  return (
    <figure data-testid="image-card"
            style={{ margin: 0, display: "flex", flexDirection: "column", gap: 4,
                     ...(fill ? { flex: 1, minHeight: 0 } : {}) }}>
      {ok ? (
        <img src={apiUrl(`/api/builder/assets/${w.asset}`)} alt={w.alt || ""}
             onError={() => setBroken(true)} draggable={false}
             style={{ width: "100%", borderRadius: 6, display: "block",
                      objectFit: w.fit === "cover" ? "cover" : "contain",
                      ...(fill ? { flex: 1, minHeight: 0, height: "100%" } : { maxHeight: 320 }) }} />
      ) : note(w.asset ? "This picture is not on this system - upload it again."
                       : "Upload a picture in the panel on the right.")}
      {w.caption && (
        <figcaption className="muted small" style={{ textAlign: "center", overflowWrap: "anywhere" }}>
          {w.caption}
        </figcaption>
      )}
    </figure>
  );
}

export default function Widget({ widget, accent = "#1d4ed8", fill = false,
                                 streamFps = 8, running = true, exportCsv = null }) {
  const { kind, title, data, span = 1, id } = widget;
  const showTitle = titleShown(kind, widget.show_title);
  // "Download CSV" at a table card's top right, where the page asks for it
  // (exportCsv: {pageRange} on Analytics and the Dashboard; never in the
  // Builders' canvas, never on paper).
  const [csvOpen, setCsvOpen] = useState(false);
  // The line (series) a click picked on a time-series chart: it is drawn
  // strong, the others dim, and only its values print. A click anywhere else
  // puts the chart back (Tok Aba, 2026-10-27).
  const [lineSel, setLineSel] = useState(null);
  // The slice, bar or radar shape a click picked, and its value box.
  const [pop, setPop] = useState(null);
  const plotRef = useRef(null);
  useEffect(() => {
    if (lineSel === null && pop === null) return undefined;
    const away = (e) => {
      if (!(plotRef.current && plotRef.current.contains(e.target))) {
        setLineSel(null);
        setPop(null);
      }
    };
    document.addEventListener("click", away);
    return () => document.removeEventListener("click", away);
  }, [lineSel, pop]);
  const csvButton = !!exportCsv && kind === "table" && !!data && !data.error
    && (data.columns || []).length > 0;
  // Where a stat sits (tile, status pill, verdict): its own, else where each
  // always sat. Read once here so the body and the placed cell agree.
  const statPos = STAT_LAYOUT_KINDS.includes(kind)
    ? statLayout(kind, widget.align, widget.valign) : null;
  const template = widget.template || "plain";
  // Which day the operator has asked to look at, on a chart coloured by day.
  // Card-local and deliberately not persisted: it is a way of READING the
  // picture, not a setting -- the next person to open the dashboard should
  // see all of it, not somebody else's focus from last Tuesday.
  const [focusDay, setFocusDay] = useState("");
  // The size the chart was given, measured by recharts' ResponsiveContainer
  // (onResize): a small dashboard tile thins its ticks and drops its axis
  // titles rather than letting them overlap or clip; the large Analytics view
  // gets every detail. Null until measured -- laid out as a medium card.
  const [box, setBox] = useState(null);
  // Which series this VIEWER has hidden from the legend, by series id (a
  // slice by its id or label) -- so a poll, a reorder or a re-render keeps
  // the choice. Session-only, never written into the chart's configuration.
  const [hiddenSel, setHiddenSel] = useState(() => readHidden(id || title));
  const toggleSeries = (key) => setHiddenSel((cur) => {
    const next = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key];
    writeHidden(id || title, next);
    return next;
  });
  const showAllSeries = () => { writeHidden(id || title, []); setHiddenSel([]); };
  const onBox = (w, h) => setBox((b) => (b && b.w === w && b.h === h ? b : { w, h }));
  // How the card ARRIVES, and how it MOVES when its numbers change. Separate
  // from `template`, which is how it is dressed -- a stacked area chart may
  // fade in or slide in without either choice touching the other.
  //
  // "" everywhere is the default and stays it: a wall display that redraws
  // every few seconds must not be made to move by an upgrade.
  const anim = String(widget.anim || "");
  // The three families (see builder.ANIMATIONS). `draw` and the update
  // animations are Recharts' own -- the shapes tween to their new values --
  // where `appear` is the whole card moving, which is CSS on the container
  // and therefore works on a tile, a table and a video feed as well.
  const animDraw = anim === "grow" || anim === "stagger";
  const animUpdate = anim === "morph" || anim === "enter_update_exit";
  const animated = animDraw || animUpdate;
  // A CLASS for the CSS half, and a key that makes React remount the body so
  // the animation plays again. Without the remount an arrival animation runs
  // once in the life of the page, which on a dashboard left open all day
  // means "never" -- and with it on every poll, nothing is readable. So the
  // key only moves when the operator asked for it to.
  const animSig = widget.anim_replay ? JSON.stringify(data ?? null) : "";
  const animClass = anim && !animDraw && !animUpdate ? `wgt-anim wgt-anim-${anim}`
    : anim === "color" ? "wgt-anim wgt-anim-color" : "";
  // Gradient fills are SVG defs, which are document-global: two widgets both
  // calling their gradient "g" would have the second silently paint with the
  // first one's colours. Keyed by widget id, which is unique per layout.
  const gid = `wg-${String(id || title || "w").replace(/[^a-zA-Z0-9_-]/g, "")}`;

  const style = fill
    ? { height: "100%", minHeight: 0, display: "flex", flexDirection: "column" }
    : { gridColumn: `span ${span}` };
  // Charts fill the cell when placed, keep their natural height otherwise.
  const chartH = fill ? { height: "100%", minHeight: 0 } : { height: 180 };

  let body = null;
  let cardExtra = null;      // template dressing applied to the card itself

  if (kind === "text_card") {
    body = <TextCard w={widget} fill={fill} />;
  } else if (kind === "image_card") {
    body = <ImageCard w={widget} fill={fill} />;
  } else if (data == null) {
    body = <div className="stat-sub">widget failed - see master logs</div>;
  } else if (data.ai_off && !data.ai_badge) {
    // Bound to something only the AI measures, and the AI is switched off:
    // the app publishes nothing for it, so the card says so -- never a 0,
    // which reads as "nobody there", and not "no data yet" either. Worded
    // AI OFFLINE (Tok Aba 2026-10-05): a state, not "waiting" for anything.
    body = (
      <div data-ai-off="1" title="AI detection is off - this reads what the camera sees"
           style={{ opacity: 0.55, display: "flex", flexDirection: "column",
                    alignItems: "center", justifyContent: "center", gap: 4,
                    minHeight: 60, color: "var(--muted-text, #94a3b8)" }}>
        <div style={{ fontWeight: 650, fontSize: 18 }}>AI OFFLINE</div>
        <div className="muted small">Detection is off</div>
      </div>
    );
  } else if (data.paused && !data.paused_badge) {
    // Its numbers come from rules behind a Class Filter someone switched off
    // on the dashboard: they publish nothing live, so the card says paused
    // in words -- the AI-off treatment for one branch, never a 0.
    body = (
      <div data-paused="1"
           style={{ opacity: 0.55, display: "flex", flexDirection: "column",
                    alignItems: "center", justifyContent: "center", gap: 4,
                    minHeight: 60, color: "var(--muted-text, #94a3b8)",
                    textAlign: "center" }}>
        <div style={{ fontWeight: 650, fontSize: 18 }}>Paused</div>
        <div className="muted small">
          {data.paused_filter
            ? `the "${data.paused_filter}" Class Filter is switched off`
            : "its Class Filter is switched off"}
        </div>
      </div>
    );
  } else if (kind === "ai_classes") {
    body = <AiClassesCard data={data} />;
  } else if (kind === "class_filters") {
    body = <ClassFilterCard data={data} />;
  } else if (kind === "snap_gallery") {
    body = <SnapGalleryCard data={data} fill={fill} />;
  } else if (kind === "video") {
    // Live MJPEG feed (already annotated with boxes + zones by the detection
    // app). StreamImg is the whole player: it drops the connection when the
    // widget scrolls out of the dashboard or the tab is backgrounded, which a
    // grid of these otherwise keeps open all day. `contain` fits the frame
    // inside whatever box it is given, so it is neither cropped nor
    // overflowing - forcing 4:3 here is what used to slice the bottom off a
    // short cell.
    //
    // The rate is the PAGE's decision (WidgetGrid shares one budget across
    // however many feeds it is showing), because only the page knows how many
    // there are. A fixed 8fps here made a lone dashboard feed look like slow
    // motion next to the Detection page, which streams uncapped - same
    // camera, same pipeline, visibly different picture. streamFps = 0 means
    // "no cap": the app serves at whatever rate it is actually producing.
    // Detection stopped: say so, the same way the Zones page does, instead
    // of a broken image retrying a stream that cannot exist. The placeholder
    // appears the INSTANT the operator presses Stop (the page believes the
    // press optimistically), while the app winds down in the background.
    // Two reasons a feed is not coming, and they need different sentences:
    // detection is stopped (press Start), or THIS camera is switched off
    // (switch it on). Both beat a black rectangle retrying a stream that
    // cannot exist. Same shape as the Camera Setup placeholder.
    body = (!running || data.inactive) ? (
      <div style={{ width: "100%", background: "#000", borderRadius: 6,
                    display: "flex", flexDirection: "column",
                    alignItems: "center", justifyContent: "center", gap: 8,
                    color: "var(--muted-text, #94a3b8)",
                    ...(fill ? { height: "100%", minHeight: 0 }
                             : { aspectRatio: "4 / 3" }) }}>
        <VideoOff size={28} style={{ opacity: 0.7 }} />
        <div style={{ fontWeight: 650, fontSize: 14, color: "var(--text, #e2e8f0)" }}>
          {data.inactive && running ? "Camera inactive" : "Detection is stopped"}
        </div>
        <div className="muted small">
          {data.inactive && running
            ? "Switch it on in Camera Setup to bring the feed back"
            : "Press Start above to bring the feed back"}
        </div>
      </div>
    ) : (
      <div style={{ width: "100%", background: "#000", borderRadius: 6,
                    overflow: "hidden",
                    ...(fill ? { height: "100%", minHeight: 0 }
                             : { aspectRatio: "4 / 3" }) }}>
        {/* data.url is the server's root-relative path: it needs the prefix
            under the reverse proxy exactly as the default does.
            [video] view_on_detect: the picture only while something is
            detected (DetectGate; a pass-through when the switch is off). */}
        <DetectGate {...gateFor(data)} camera={data.camera || ""}>
        <StreamImg src={withParams(data.url ? apiUrl(data.url)
                                            : apiUrl("/api/detector/preview"),
                                   streamFps ? { fps: streamFps } : {})}
                   alt="live feed"
                   style={{ width: "100%", height: "100%" }}
                   imgStyle={{ width: "100%", height: "100%", objectFit: "contain",
                               display: "block" }}
                   // [video] overlay = "client": the plain picture, boxes drawn
                   // here (the widget data says so; nothing in server mode).
                   overlay={data.overlay?.mode === "client"
                     ? <OverlayCanvas camera={data.camera || ""}
                                      delayMs={Number(data.overlay.delay_ms) || 0} />
                     : undefined} />
        </DetectGate>
      </div>
    );
  } else if (kind === "verdict") {
    // The card an operator glances at from across a room, so the STATE is the
    // whole card - colour, ring and word together - not a small pill floating
    // in white space.
    //
    // Three states, deliberately. "No object" is not "compliant": the zone is
    // simply empty, and colouring that green is a claim the system cannot make,
    // so it is grey and says so.
    const TONE = {
      ok:      { c: "#16a34a", glyph: "✓", text: "Everything within limits" },
      alert:   { c: "#dc2626", glyph: "!", text: "Condition active" },
      // A middle state for custom mappings (0 idle, 1 warning, 2 violation).
      warn:    { c: "#d97706", glyph: "!", text: "Needs attention" },
      empty:   { c: "#64748b", glyph: "–", text: "Nothing detected in frame" },
      unknown: { c: "#64748b", glyph: "?", text: "Waiting for the detector" },
    };
    const stock = TONE[data.state] || TONE.unknown;
    // The operator may give a STATE its own colour ("normal is blue on this
    // line") -- the tone still decides the glyph and the sentence, because a
    // colour carries no words.
    const tone = data.state_color ? { ...stock, c: data.state_color } : stock;
    const alerting = data.state === "alert";
    body = (
      <div data-align={statPos.align} data-valign={statPos.valign} style={{
        flex: 1, minHeight: 0, display: "flex", flexDirection: "column",
        alignItems: FLEX_OF[statPos.align], justifyContent: FLEX_OF[statPos.valign], gap: 10,
        padding: "16px 12px", borderRadius: 12,
        background: `color-mix(in srgb, ${tone.c} 12%, transparent)`,
        border: `1px solid color-mix(in srgb, ${tone.c} 45%, transparent)`,
      }}>
        <div style={{
          width: 44, height: 44, borderRadius: 999, flexShrink: 0,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 22, fontWeight: 800, color: "#fff", background: tone.c,
          // A live violation pulses; a steady state must not, or the pulse
          // stops meaning anything.
          boxShadow: alerting ? `0 0 0 6px color-mix(in srgb, ${tone.c} 22%, transparent)` : "none",
          animation: alerting ? "verdict-pulse 1.6s ease-in-out infinite" : "none",
        }}>{tone.glyph}</div>
        <div style={{ fontSize: 19, fontWeight: 800, letterSpacing: 0.6,
                      color: tone.c, textAlign: statPos.align, lineHeight: 1.1 }}>
          {data.label || "-"}
        </div>
        <div className="stat-sub" style={{ margin: 0, textAlign: statPos.align }}>{tone.text}</div>
      </div>
    );
  } else if (kind === "status") {
    const alert = data.state === "alert";
    const color = alert ? "#e5484d" : (data.color || "#2f9e44");
    body = (
      <div data-align={statPos.align} data-valign={statPos.valign}
           style={{ display: "flex", flexDirection: "column", alignItems: FLEX_OF[statPos.align],
                    justifyContent: "center", padding: "12px 0", textAlign: statPos.align }}>
        <div style={{ padding: "8px 22px", borderRadius: 999, fontWeight: 700,
                      letterSpacing: 1, color: "#fff", background: color }}>
          {data.label || (alert ? "ALERT" : "OK")}
        </div>
        <div className="stat-sub" style={{ marginTop: 8 }}>
          current value: {data.value != null ? data.value : "-"}
        </div>
      </div>
    );
  } else if (kind === "button") {
    body = <InjectButton widget={widget} data={data} fill={fill} />;
  } else if (kind === "heatmap") {
    // WHERE things happened, drawn as the grid the function published: one
    // patch of the camera's view per cell, shaded by how much has happened in
    // it. Every cell is scaled against the BUSIEST one, so the picture reads
    // the same whether the map has counted ten objects or ten thousand --
    // an absolute scale would be a black rectangle for the first hour and a
    // solid one by the afternoon.
    const grid = data.grid || [];
    const peak = Number(data.max || 0);
    const c = data.color || widget.color || accent;
    body = grid.length ? (
      <div style={{ display: "flex", flexDirection: "column", gap: 6,
                    flex: 1, minHeight: 0 }}>
        <div style={{
          flex: 1, minHeight: fill ? 0 : 140,
          display: "grid", gap: 1, borderRadius: 6, overflow: "hidden",
          gridTemplateColumns: `repeat(${data.cols || grid[0].length}, 1fr)`,
          gridTemplateRows: `repeat(${data.rows || grid.length}, 1fr)`,
          background: "var(--panel-2, #0f172a)",
        }}>
          {grid.flatMap((row, r) => row.map((v, cIdx) => {
            // A cell with nothing in it stays background rather than a faint
            // wash: "nobody has been here" is a real answer and it should
            // look like one.
            const share = peak > 0 ? Number(v) / peak : 0;
            return (
              <div key={`${r}-${cIdx}`}
                   title={`row ${r + 1}, column ${cIdx + 1}: ${Math.round(Number(v))}`}
                   style={{ background: share > 0 ? hexToRgba(c, 0.08 + share * 0.92)
                                                  : "transparent" }} />
            );
          }))}
        </div>
        <div className="stat-sub" style={{ margin: 0 }}>
          busiest patch {Math.round(peak)}
          {data.spots != null ? ` · ${Math.round(data.spots)} patches used` : ""}
        </div>
      </div>
    ) : (
      <div className="stat-sub">no map yet - the heatmap fills as objects move</div>
    );
  } else if (kind === "tile") {
    // The KPI card. `delta` is optional and entirely the server's to send --
    // absent, the tile is exactly what it always was. When present it reads
    // as a change against the previous period, with its own direction: a
    // FALLING refund count is good news, so the arrow states the direction
    // and the colour is left to `delta.good` rather than assumed from sign.
    const c = data.color || widget.color || accent;
    const d = data.delta;
    const dir = d && Number(d.pct) < 0 ? "down" : "up";
    const good = d && (d.good === undefined ? dir === "up" : !!d.good);
    const invert = template === "accent";

    if (template === "accent") {
      cardExtra = { background: c, borderColor: c, color: "#fff" };
    } else if (template === "outline") {
      cardExtra = { borderColor: c };
    } else if (template === "soft") {
      cardExtra = { background: hexToRgba(c, 0.1),
                    borderColor: hexToRgba(c, 0.35) };
    }

    body = (
      <div data-align={statPos.align} data-valign={statPos.valign}
           style={{ textAlign: statPos.align, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8,
                      flexWrap: "wrap", justifyContent: FLEX_OF[statPos.align] }}>
          {/* contrast-ok: `invert` is the treatment that fills the whole card
              with the accent, so this white sits on that paint, not on the
              theme's surface. */}
          <div className="stat-value"
               style={{ ...(invert ? { color: "#fff" }
                    : (template === "outline" || template === "soft")
                      ? { color: c } : (data.color ? { color: data.color } : undefined)),
                    // WORDS, not a number. A tile's value type is sized for
                    // "47", and a shift name or "violate" at that size runs
                    // off the card -- so a text value is set smaller and is
                    // allowed to wrap. Measured, not declared: the value may
                    // be text because a constant says so, because a text
                    // column's latest row does, or because a query returned a
                    // word, and the card cannot be asked to know which.
                    ...textValue(data.value, scaleOf(widget.value_size)) }}>
            {data.value == null ? "-"
              // WORDS ON PURPOSE. The three formats below turn a number into
              // a word; this one says the value ALREADY is one and must be
              // printed as it stands. Without it a text stat picked up
              // whichever 0-or-1 format happened to be set and read "NO",
              // because Number("People") is NaN and NaN > 0 is false -- a
              // card confidently answering a question nobody asked. String()
              // rather than the bare value so a boolean, which React renders
              // as nothing at all, still shows something.
              : widget.format === "text" ? String(data.value)
              : widget.format === "yesno" ? (Number(data.value) > 0 ? "YES" : "NO")
              : widget.format === "onoff" ? (Number(data.value) > 0 ? "ON" : "OFF")
              : widget.format === "truefalse" ? (Number(data.value) > 0 ? "TRUE" : "FALSE")
              : data.value}
            {/* THE UNIT, beside the number, which is what a unit is. A gauge
                has always drawn it here; a tile never did, so "Unit label" was
                a field that only appeared if no caption was set -- and then
                appeared as a CAPTION, which is a different thing. Numbers
                only: "418 pcs" reads, "VIOLATE pcs" does not, and a tile
                showing a word has no quantity to qualify. */}
            {data.unit && data.value != null && !widget.format
              && Number.isFinite(Number(data.value)) && (
              <span style={{ fontSize: "0.45em", fontWeight: 600,
                             marginLeft: 4, opacity: 0.75 }}>
                {data.unit}
              </span>
            )}
          </div>
          {d && (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 3,
              padding: "2px 7px", borderRadius: 999,
              fontSize: 11, fontWeight: 700, whiteSpace: "nowrap",
              background: invert ? "rgba(255,255,255,.22)"
                        : good ? "rgba(16,185,129,.15)" : "rgba(239,68,68,.15)",
              color: invert ? "#fff" : good ? "#10b981" : "#ef4444",
            }}>
              {dir === "up" ? "↑" : "↓"} {d.pct}%
            </span>
          )}
        </div>
        {/* No caption, no line. An empty .stat-sub is still a block with a
            margin, so a card with nothing to say under its value reserved the
            space anyway -- and `data.label` filled it with a word the operator
            never wrote. The comparison line is the one thing that earns the
            space without being typed: it is a fact about the number, not a
            label for it. */}
        {/* contrast-ok: same accent-filled card as the value above. */}
        {(d?.since || data.sub) && (
          <div className="stat-sub" style={invert ? { color: "rgba(255,255,255,.85)" } : undefined}>
            {d?.since
              ? <>Vs {d.since}: <strong>{d.was}</strong></>
              : data.sub}
          </div>
        )}
      </div>
    );
  } else if (kind === "table") {
    // Its OWN scroll box, header pinned. A placed card centres its body
    // vertically, and a table taller than the card overflowed both ways --
    // the top rows (the column headers) were drawn up over the card's title,
    // and the bottom ran off the card with nothing to scroll (beta feedback,
    // the Violation Log on Analytics). The box takes what the card leaves
    // under the title and scrolls inside it.
    body = (
      <div className="wgt-table-scroll" data-wgt-table="1"
           style={fill ? { flex: "1 1 auto", minHeight: 0, overflow: "auto" }
                       : { maxHeight: 320, overflow: "auto" }}>
        <table className="mini-table" style={{ marginTop: 0 }}>
          <thead>
            <tr>{(data.columns || []).map((c, i) => (
              <th key={i} style={{ position: "sticky", top: 0, zIndex: 1,
                                   // The CARD's paint: a coloured card's
                                   // header is the card colour with the
                                   // card's own text colour, not a white
                                   // strip under white words.
                                   background: "var(--wgt-head-bg, var(--surface, #fff))",
                                   color: "inherit" }}>{c}</th>
            ))}</tr>
          </thead>
          <tbody>
            {(data.rows || []).length
              ? data.rows.map((row, i) => (
                  <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                ))
              : <tr><td className="muted">no data yet</td></tr>}
          </tbody>
        </table>
      </div>
    );
  } else if (kind === "gauge") {
    // A single value against a max, drawn as a half-donut with the number in
    // the middle. data: {value, max, unit}. Two slices and a 180° sweep --
    // the same shape Chart.js drew with rotation/circumference.
    // The ?? 0 stays: a card whose stat has not been published yet reads 0
    // and always has, and turning every freshly booted gauge into the "not a
    // number" state below would be a new complaint every restart.
    const raw = data.value ?? (data.values || [])[0] ?? 0;
    const val = Number(raw);
    const max = Number(data.max ?? 100) || 100;
    const pct = Math.max(0, Math.min(1, val / max));
    const color = data.color || widget.color || accent;
    // A DIAL OF A WORD IS NOT A DIAL. Now that a text stat actually reaches
    // the browser, a gauge bound to one is a wiring mistake somebody has to
    // be able to SEE: Number("People") is NaN, which drew a needle at no
    // position and printed NaN in the middle, and defaulting to 0 would have
    // been worse -- a full-looking dial reading empty, indistinguishable
    // from a real zero. So the arc is not drawn at all; the word is shown as
    // the word it is, and the card says what it would need instead.
    body = !Number.isFinite(val) ? (
      <div className="chart-wrap" style={{ ...chartH, minHeight: fill ? 0 : 150,
                                           display: "flex", flexDirection: "column",
                                           alignItems: "center",
                                           justifyContent: "center" }}>
        <div className="stat-value"
             style={{ color, ...textValue(String(raw),
                                          scaleOf(widget.value_size)) }}>
          {String(raw)}
        </div>
        <div className="stat-sub">a dial needs a number - show this on a tile</div>
      </div>
    ) : (
      <div className="chart-wrap" style={{ ...chartH, minHeight: fill ? 0 : 150,
                                           position: "relative" }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={[{ v: pct }, { v: 1 - pct }]} dataKey="v"
                 startAngle={180} endAngle={0}
                 cx="50%" cy="72%" innerRadius="62%" outerRadius="88%"
                 stroke="none" {...animProps(animated, anim)}>
              <Cell fill={color} />
              <Cell fill={hexToRgba(color, 0.12)} />
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div style={{ position: "absolute", inset: 0, top: "40%", textAlign: "center" }}>
          <div className="stat-value" style={{ color }}>{val}{data.unit || ""}</div>
        </div>
      </div>
    );
  } else if (kind === "timeline") {
    const rows = data.rows || [];
    body = rows.length ? (
      <div className="timeline-list" style={{ maxHeight: 240, overflowY: "auto" }}>
        {rows.map((row, i) => (
          <div key={i} style={{ display: "flex", gap: 8, padding: "4px 0", borderBottom: "1px solid rgba(128,128,128,.12)" }}>
            <span className="muted small" style={{ minWidth: 130 }}>{row[0]}</span>
            <span className="small">{row.slice(1).filter(Boolean).join(" · ")}</span>
          </div>
        ))}
      </div>
    ) : <div className="stat-sub">no data yet</div>;
  } else if (["bar", "line", "area", "spline", "pie", "donut", "radar"].includes(kind)) {
    const labels = data.labels || [];
    const values = data.values || [];
    // A series the server could not read arrives EMPTY with a warning beside
    // it, keyed by the series' own id -- so one unreadable input leaves the
    // other three drawn instead of blanking the card. Surfaced per series on
    // the legend below, which is the only place that can say WHICH input.
    const warnings = Array.isArray(data.warnings) ? data.warnings : [];
    const warnById = new Map(warnings.filter((w) => w && w.id)
                                     .map((w) => [w.id, w]));
    //: The same messages as one line, for the card that drew nothing at all.
    //: A note about the whole chart (no series id), such as inputs that
    //: disagreed on the X axis: said in grey under it.
    const chartNote = warnings.filter((w) => w && !w.id && w.message)
      .map((w) => w.message).join(" ");
    const warnLine = warnings
      .map((w) => (w.label ? `${w.label}: ${w.message}` : w.message))
      .filter(Boolean).join(" · ");
    if (!labels.length) {
      // With the reason, when there is one. A freshly booted master has no
      // samples for ten seconds or so and every history card says so here --
      // as an explanation of the empty card, in the card's own quiet voice,
      // not as a warning badge that appears at every boot and clears itself.
      // A badge like that is a badge people learn to scroll past.
      body = (
        <div className="stat-sub">
          no data yet{warnLine ? ` - ${warnLine}` : ""}
        </div>
      );
    } else {
      const color = data.color || widget.color || accent;
      const { rows, series: allSeries } = toRows(data, title, widget.color || accent);
      // A series' legend key: its own id when the server sent one, else its
      // label -- never its position, which a reorder would move.
      const seriesKey = (s, i) => String(((data.datasets || [])[i] || {}).id || s.label);
      const keyed = allSeries.map((s, i) => ({ ...s, lkey: seriesKey(s, i) }));
      // What is DRAWN: the series the viewer has not hidden, colours kept as
      // assigned above. Never nothing -- a stale choice that would hide every
      // series (the data changed) shows them all instead.
      const visibleSeries = keyed.filter((s) => !hiddenSel.includes(s.lkey));
      const series = visibleSeries.length ? visibleSeries : keyed;
      const hiddenNow = visibleSeries.length ? hiddenSel : [];
      // Does anything at all draw? A card where NO series has a value already
      // reads as empty, and marking every legend entry on it says "empty"
      // once per input. The marks appear as soon as one input HAS data, which
      // is the case worth pointing at: three lines and a gap where the fourth
      // should be. A ZERO counts as data - the event store's zero-fill inside
      // a bounded window is a reading, not a hole.
      const drawn = rows.some(
        (r) => keyed.some((s) => Number.isFinite(Number(r[s.key]))));
      const marked = warnById.size > 0 && drawn;
      //: The mark itself: the legend entry keeps its colour and gains a sign,
      //: with the reason on hover. `--warn-text` rather than `--warn`,
      //: because this is TEXT and the fill token is not contrast-checked for
      //: text (tests/test_contrast.mjs pins the pair).
      const warnMark = (note) => (note ? (
        <span className="chart-warn" title={note.message}
              aria-label={note.message}>!</span>
      ) : null);
      // recharts hands the formatter the entry it drew, so the series is
      // found by its recharts dataKey and the warning by the series' OWN id -
      // which is what survives the list being reordered.
      //: A comparison chart is one value per SERIES, so its ids arrive as a
      //: list beside the values rather than on datasets.
      const sliceIds = Array.isArray(data.ids) ? data.ids : [];
      // Colour by DAY, when the operator asked for it and the axis is dated.
      // A chart of two days at hourly buckets is one long line in one colour:
      // the shape says WHEN something happened only if you read the tick
      // labels off the bottom, which is exactly the reading the picture was
      // supposed to save. Off unless asked -- a single-colour line is still
      // the right chart for one day, and for a chart of zones there are no
      // days to colour (daySpans returns null and this all stays off).
      const dayAccent = series.length ? series[0].color : color;
      const spans = widget.by_day && series.length === 1
        ? daySpans(rows.map((r) => r.x), dayAccent) : null;
      // Clicking a day focuses it and dims the rest; clicking it again, or
      // anywhere off a day, gives the whole chart back. A toggle rather than a
      // timeout: a highlight that expires on its own goes away exactly when
      // somebody is still looking at it.
      const focus = spans && spans.some((d) => d.day === focusDay) ? focusDay : "";
      const pickDay = (day) => setFocusDay((cur) => (cur === day ? "" : day || ""));
      const dayKey = spans ? (
        <div className="chart-days">
          {spans.map((d) => (
            <button key={d.day} type="button"
                    className={"chart-day" + (focus === d.day ? " is-on" : "")
                               + (focus && focus !== d.day ? " is-off" : "")}
                    onClick={(e) => { e.stopPropagation(); pickDay(d.day); }}
                    title={focus === d.day ? "Show every day again"
                                           : `Show ${d.day} on its own`}>
              <i style={{ background: d.color }} />{d.day}
            </button>
          ))}
          {focus && (
            <span className="chart-day-hint">showing {focus} - click it again for all</span>
          )}
        </div>
      ) : null;
      // What the axes MEAN. Derived from the widget's own query rather than
      // sent by the server: every source already carries enough to say it --
      // a bucketed query plots along time, a grouped one along that column,
      // and a history series is time by definition. The y side is the unit
      // when one is set, else what is being measured.
      const q = widget.query || {};
      // A time axis the server described (services/timeaxis.py) is titled
      // by its step -- "Every 6 hours" says what one point is.
      const xName = data.step ? data.step
        : q.bucket ? "time"
        : q.group_by ? String(q.group_by).replace(/_/g, " ")
        : widget.source === "history" ? "time"
        : q.field ? String(q.field).replace(/_/g, " ") : "";
      const yName = widget.unit || data.unit
        || (q.agg && q.agg !== "latest"
              ? `${q.agg === "unique" ? "distinct" : q.agg}${q.field ? ` ${String(q.field).replace(/_/g, " ")}` : ""}`
              : (series.length === 1 ? series[0].label : ""));
      // THE AXES, DRAWN SO A VALUE CAN BE READ OFF THEM (rendering
      // addendum, owner 2026-10-13). Titles: the operator's own (Builder
      // Appearance) else what the configuration says (server `axis`,
      // chartmodel.axis_titles) else the guess above; a time axis the server
      // widened is titled by the step it really drew.
      const axisInfo = widget.axis || {};
      const autoX = axisInfo.time && data.step ? data.step : (axisInfo.x || xName);
      const autoY = axisInfo.y || yName;
      const xTitle = widget.x_title || autoX || "";
      const yTitle = widget.y_title || autoY || "";
      const unit = widget.unit || data.unit || "";
      const W = box ? box.w : 0;
      const H = box ? box.h : 0;
      // A small tile: titles go, ticks thin, values stay off.
      const compact = (W > 0 && W < 300) || (H > 0 && H < 170);
      const titled = !compact;
      // THE LEGEND: every series, shown or hidden by this viewer, each a
      // switch (ToggleLegend), with an unread input's mark beside its name.
      const legendBox = !(compact && keyed.length === 1) ? (
        <ToggleLegend compact={compact} hidden={hiddenNow}
                      onToggle={toggleSeries} onShowAll={showAllSeries}
                      items={keyed.map((s) => ({
                        key: s.lkey, label: s.label, color: s.color,
                        mark: marked ? warnMark(warnById.get(s.id)) : null }))} />
      ) : null;
      const timeAxis = Array.isArray(data.keys) && data.keys.length === labels.length;
      const horizontal = kind === "bar" && widget.orientation === "horizontal";
      const lollipop = kind === "bar" && widget.bar_style === "lollipop";
      const nums = rows.flatMap((r) => series.map((s) => Number(r[s.key])))
        .filter(Number.isFinite);
      const valueChars = Math.max(1, ...nums.map((v) => fmtValue(v).length));
      const valueAxisW = Math.round(Math.min(72, Math.max(30, valueChars * CHAR_PX + 12)))
        + (titled && (horizontal ? xTitle : yTitle) ? 16 : 0);
      const catChars = Math.min(24, Math.max(1, ...rows.map((r) => String(r.x ?? "").length)));
      const catAxisW = Math.round(Math.min(170, catChars * CHAR_PX + 14))
        + (titled && xTitle ? 16 : 0);
      const plotW = (W || 520) - (horizontal ? catAxisW : valueAxisW) - 24;
      const lay = categoryLayout(rows.map((r) => r.x), plotW, { time: timeAxis, compact });
      const tick = { fontSize: compact ? 10 : 11, fill: AXIS_TEXT };
      const lines = { tickLine: { stroke: AXIS_LINE }, axisLine: { stroke: AXIS_LINE } };
      const titleOf = (value, vertical) => (titled && value ? {
        value, className: "chart-axis-title", fill: AXIS_TEXT, fontSize: 11,
        ...(vertical ? { angle: -90, position: "insideLeft", offset: 4,
                         style: { textAnchor: "middle" } }
                     : { position: "insideBottom", offset: 0 }),
      } : undefined);
      // The category axis (X; on the left for a horizontal bar chart).
      const catAxis = {
        dataKey: "x", tick, ...lines, interval: lay.interval,
        tickFormatter: (v) => truncateLabel(v, lay.maxChars),
        ...(lay.angle ? { angle: lay.angle, textAnchor: "end" } : {}),
        height: lay.height + (titled && xTitle ? 18 : 0),
        label: titleOf(xTitle, false),
      };
      // The value axis: nice ticks from zero, grouped thousands.
      const valueAxis = {
        tick, ...lines, tickCount: 6, tickFormatter: fmtValue,
        allowDecimals: nums.some((v) => !Number.isInteger(v)),
        width: valueAxisW, label: titleOf(yTitle, true),
      };
      const valuesOn = showValues(widget.data_labels, rows.length, series.length,
                                  { compact, kind });
      const fmtU = (v) => (v === null || v === undefined || v === ""
        ? "no value" : `${fmtValue(v)}${unit ? ` ${unit}` : ""}`);
      // Every hover: the series name, the point's whole bucket (time) or its
      // full label (categories), and the value with its unit.
      const tipValue = (v, name) => [fmtU(v), name];
      // The legend never sits on the data: below the plot, and on a small
      // tile with one series (the title already names it) not at all.

      // The tooltip's heading: the point's whole bucket, start to end, when
      // the axis is time (charts.js bucketTitle); the label otherwise.
      const spanLabel = (label, payload) =>
        (payload && payload[0] && payload[0].payload && payload[0].payload.span) || label;
      const sliceColor = (i) => (data.colors || [])[i]
        || hexToRgba(color, 0.85 - i * 0.14);

      if (kind === "donut" || kind === "pie") {
        // A slice's legend key: its input's id, else its label. Hidden ones
        // leave the ring, and the shares, labels and the centre total are
        // worked out over the slices still shown. Colours stay by slice.
        const sliceKey = (i) => String(sliceIds[i] || labels[i]);
        const allSlices = labels.map((l, i) => ({ i, key: sliceKey(i), name: l, v: values[i] }));
        const liveSlices = allSlices.filter((x) => !hiddenSel.includes(x.key));
        const slices = liveSlices.length ? liveSlices : allSlices;
        const slicesHidden = liveSlices.length ? hiddenSel : [];
        const total = slices.reduce((a, x) => a + (Number(x.v) || 0), 0);
        const donut = kind === "donut";
        // Each slice says what it is and how much: "OK: 12 (40%)", outside
        // the ring on a leader line -- while there are few enough slices to
        // label without collisions and the card is big enough to hold them.
        // The "legend" template lists them beside the ring instead.
        const sliceLabels = template !== "legend" && !compact && labels.length <= 8
          && widget.data_labels !== "off";
        const pieLabel = ({ name, value, percent }) =>
          `${truncateLabel(name, 14)}: ${fmtValue(value)}${unit ? ` ${unit}` : ""}`
          + ` (${Math.round((percent || 0) * 100)}%)`;
        const chart = (
          <div ref={plotRef} style={{ position: "relative", width: "100%", height: "100%" }}
               onClick={() => setPop(null)}>
          <ResponsiveContainer width="100%" height="100%" onResize={onBox}>
            <PieChart>
              <Pie data={slices.map((x) => ({ name: x.name, v: x.v }))}
                   dataKey="v" nameKey="name"
                   cx="50%" cy="50%"
                   innerRadius={donut ? (sliceLabels ? "45%" : "58%") : 0}
                   outerRadius={sliceLabels ? "66%" : "82%"}
                   paddingAngle={donut ? 2 : 0}
                   label={sliceLabels ? pieLabel : false}
                   labelLine={sliceLabels ? { stroke: AXIS_LINE } : false}
                   stroke="none" {...animProps(animated, anim)}
                   onClick={(_d, i, e) => {
                     if (e && e.stopPropagation) e.stopPropagation();
                     const x = slices[i];
                     if (!x) return;
                     setPop({ key: x.key, ...placeIn(plotRef, e),
                              lines: [{ text: `${x.name}: ${fmtValue(x.v)}${unit ? ` ${unit}` : ""}` }] });
                   }}>
                {slices.map((x) => (
                  <Cell key={x.key} fill={sliceColor(x.i)} className="chart-pickable"
                        fillOpacity={pop && pop.key !== x.key ? 0.3 : 1} />
                ))}
              </Pie>
              {/* No hover box: a click on a slice opens its value (ChartPopup). */}
              <Tooltip {...TOOLTIP} content={() => null} />
            </PieChart>
          </ResponsiveContainer>
          <ChartPopup pop={pop} />
          </div>
        );

        // The "legend" template: the donut keeps its own total in the middle
        // and every slice gets a row with its value AND its share. A ring of
        // colours with the key somewhere else is a chart you have to decode
        // twice.
        body = template === "legend" ? (
          <div style={{ ...chartH, display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ position: "relative", flex: "0 0 42%", height: "100%",
                          minWidth: 0, minHeight: 90 }}>
              {chart}
              {donut && (
                <div style={{ position: "absolute", inset: 0, display: "flex",
                              flexDirection: "column", alignItems: "center",
                              justifyContent: "center", pointerEvents: "none" }}>
                  <span className="muted" style={{ fontSize: 10 }}>{data.unit || "total"}</span>
                  <strong style={{ fontSize: 16 }}>{total.toLocaleString()}</strong>
                </div>
              )}
            </div>
            <div style={{ flex: 1, minWidth: 0, display: "grid", gap: 4,
                          overflowY: "auto", maxHeight: "100%" }}>
              {/* Each row is a switch: press it to take the slice off the
                  ring (and out of the shares), press again to bring it back. */}
              {allSlices.map(({ i, key: sk, name: l }) => {
                const on = !slicesHidden.includes(sk);
                const last = on && slices.length <= 1;
                return (
                <button key={sk} type="button" className="chart-legend-toggle"
                        aria-pressed={on} aria-disabled={last || undefined}
                        aria-label={`${l}, ${on ? "shown" : "hidden"}. ${last
                          ? "The last slice shown stays on the chart."
                          : `Press to ${on ? "hide" : "show"}.`}`}
                        onClick={() => { if (!last) toggleSeries(sk); }}
                        style={{ display: "flex", alignItems: "center", gap: 6,
                                 fontSize: 11.5, background: "none", border: 0,
                                 padding: 0, font: "inherit", color: "inherit",
                                 textAlign: "left", cursor: last ? "default" : "pointer",
                                 opacity: on ? 1 : 0.45,
                                 textDecoration: on ? "none" : "line-through" }}>
                  <span style={{ width: 8, height: 8, borderRadius: 999,
                                 flexShrink: 0, background: sliceColor(i) }} />
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden",
                                 textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {l}
                    {/* The slice's own id, not its position: a series list
                        reordered on the editor must not move a warning onto
                        the input beside it. */}
                    {warnMark(marked ? warnById.get(sliceIds[i]) : null)}
                  </span>
                  <strong style={{ flexShrink: 0 }}>{values[i]}</strong>
                  {total > 0 && on && (
                    <span className="muted" style={{ flexShrink: 0, width: 34,
                                                     textAlign: "right" }}>
                      {Math.round((Number(values[i]) || 0) / total * 100)}%
                    </span>
                  )}
                </button>
                );
              })}
              {slicesHidden.some((k) => allSlices.some((x) => x.key === k)) && (
                <button type="button" className="chart-legend-all" onClick={showAllSeries}
                        style={{ background: "none", border: 0, padding: 0, font: "inherit",
                                 color: "inherit", textAlign: "left", cursor: "pointer",
                                 textDecoration: "underline", fontSize: 11 }}>
                  Show all
                </button>
              )}
            </div>
          </div>
        ) : (
          // A plain ring has no legend to hang a mark on - the slice names
          // live in the tooltip - so an unread input says so under the chart
          // instead. Muted, one line, and only while something else drew.
          <div className="chart-block">
            <div className="chart-wrap" style={chartH}>{chart}</div>
            {/* The slices named under the ring, each in its colour: a plain
                HTML row (recharts' Pie legend draws nothing until its
                sectors have animated in), wrapping, never over the ring. */}
            {!compact && (
              <ToggleLegend hidden={slicesHidden} onToggle={toggleSeries}
                            onShowAll={showAllSeries}
                            items={allSlices.map((x) => ({
                              key: x.key, label: x.name, color: sliceColor(x.i),
                              mark: marked ? warnMark(warnById.get(sliceIds[x.i])) : null }))} />
            )}
            {marked && warnLine && (
              <div className="stat-sub chart-warn-line">{warnLine}</div>
            )}
          </div>
        );

      } else if (kind === "radar") {
        body = (
          <div className="chart-block">
          <div className="chart-wrap" ref={plotRef} style={{ ...chartH, position: "relative" }}
               onClick={() => setPop(null)}>
            <ResponsiveContainer width="100%" height="100%" onResize={onBox}>
              <RadarChart data={rows}>
                <PolarGrid stroke="rgba(148,163,184,.3)" />
                <PolarAngleAxis dataKey="x" tick={tick}
                                tickFormatter={(v) => truncateLabel(v, compact ? 8 : 16)} />
                {/* The value scale along one spoke, so a shape can be read as
                    numbers and not only as a shape. */}
                {!compact && (
                  <PolarRadiusAxis angle={90} tick={{ ...tick, fontSize: 10 }}
                                   tickFormatter={fmtValue} axisLine={false}
                                   tickCount={4} />
                )}
                {/* Every series, not only the first: a radar given two inputs
                    used to draw one shape and silently drop the other, which
                    is the same card telling two different stories depending
                    on which kind it was switched to. */}
                {series.map((s) => (
                  <Radar key={s.key} dataKey={s.key} name={s.label}
                         stroke={s.color} strokeWidth={pop && pop.key === s.key ? 3.5 : 2}
                         fill={hexToRgba(s.color, 0.22)}
                         className="chart-pickable"
                         {...(pop && pop.key !== s.key ? { opacity: 0.2 } : null)}
                         onClick={(_d, e) => {
                           const ev = e || _d;
                           if (ev && ev.stopPropagation) ev.stopPropagation();
                           setPop({ key: s.key, ...placeIn(plotRef, ev),
                                    lines: [{ text: s.label, color: s.color },
                                            ...rows.map((r) => ({
                                              text: `${r.x}: ${fmtValue(r[s.key])}${unit ? ` ${unit}` : ""}` }))] });
                         }}
                         {...animProps(animated, anim, s.key, series)} />
                ))}
                <Tooltip {...TOOLTIP} content={() => null} />
              </RadarChart>
            </ResponsiveContainer>
            <ChartPopup pop={pop} />
          </div>
          {/* Named, like every other chart here (a shape on a radar is a
              shape with no subject without it), and each name a switch. */}
          {legendBox}
          </div>
        );

      } else if (kind === "bar") {
        // Which day each ROW belongs to, so a bar can be painted by day and
        // clicked to focus it -- the same reading the line chart gets, drawn
        // the way a bar chart can take it (a per-bar Cell, not a gradient).
        const dayAt = spans
          ? rows.map((_r, i) => spans.find((d) => i >= d.from && i <= d.to) || null)
          : null;
        // THRESHOLD COLOURS ON BARS: each bar painted by the band its value
        // is in -- a series' OWN bands (per camera) on that series' bars
        // only, else the chart's shared bands while it draws one series
        // (one channel, one meaning: with several series the colour says
        // which series). Thresholds win over colour-by-day.
        const sharedBars = widget.bands && Array.isArray(widget.bands.at)
          && widget.bands.at.length ? widget.bands : null;
        const levelOf = (s) => s.bands || (sharedBars && series.length === 1 ? sharedBars : null);
        body = (
          <div className="chart-block">
          <div className="chart-wrap" ref={plotRef} style={{ ...chartH, position: "relative" }}
               onClick={() => setPop(null)}>
            <ResponsiveContainer width="100%" height="100%" onResize={onBox}>
              <BarChart data={rows} layout={horizontal ? "vertical" : "horizontal"}
                        margin={{ top: valuesOn && !horizontal ? 14 : 6,
                                  right: valuesOn && horizontal ? 36 : 12,
                                  left: 0, bottom: 0 }}
                        onClick={dayAt ? (e) => pickDay(dayOf(e?.activeLabel)) : undefined}>
                {/* Light gridlines across the VALUE axis: horizontal ones on a
                    vertical bar chart, vertical ones on a horizontal one. */}
                <CartesianGrid {...GRID} strokeDasharray=""
                               stroke="rgba(148,163,184,.28)"
                               horizontal={!horizontal} vertical={horizontal} />
                {horizontal ? (
                  <>
                    {/* Categories down the left, values along the bottom
                        (the owner's second reference picture). */}
                    <XAxis type="number" {...valueAxis} domain={[0, data.max ?? "auto"]}
                           width={undefined} height={titled && yTitle ? 40 : 24}
                           label={titleOf(yTitle, false)} />
                    <YAxis type="category" dataKey="x" tick={tick} {...lines}
                           width={catAxisW}
                           tickFormatter={(v) => truncateLabel(v, compact ? 10 : 24)}
                           interval={(H || 240) / Math.max(1, rows.length) < 13
                             ? Math.ceil(13 / ((H || 240) / Math.max(1, rows.length))) - 1 : 0}
                           label={titleOf(xTitle, true)} />
                  </>
                ) : (
                  <>
                    <XAxis {...catAxis} />
                    <YAxis {...valueAxis} domain={[0, data.max ?? "auto"]} />
                  </>
                )}
                {/* No hover box: a click on a bar opens its values (ChartPopup). */}
                <Tooltip {...TOOLTIP} content={() => null} cursor={false} />
                {/* Each series' limits as dashed lines in the colour of the
                    band above them, named by the series when it has its own. */}
                {series.filter((s) => levelOf(s)).flatMap((s) => levelOf(s).at.map((t, i) => (
                  <ReferenceLine key={`thr-${s.key}-${i}`} {...(horizontal ? { x: t } : { y: t })}
                                 className="chart-threshold" stroke={levelOf(s).colors[i + 1]}
                                 strokeDasharray="4 4" strokeWidth={1} ifOverflow="extendDomain"
                                 label={{ value: s.bands ? `${s.label}: ${t}` : String(t),
                                          position: horizontal ? "insideTopLeft" : "insideTopRight",
                                          fontSize: 9, fill: levelOf(s).colors[i + 1] }} />
                )))}

                {series.map((s) => (
                  <Bar key={s.key} dataKey={s.key} name={s.label}
                       fill={template === "gradient" ? `url(#${gid}-${s.key})` : s.color}
                       radius={lollipop ? 0 : horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0]}
                       shape={lollipop ? (p) => <Lollipop {...p} horizontal={horizontal} /> : undefined}
                       cursor="pointer"
                       onClick={(_d, i, e) => {
                         if (e && e.stopPropagation) e.stopPropagation();
                         const row = rows[i];
                         if (!row) return;
                         setPop({ key: i, ...placeIn(plotRef, e),
                                  lines: [{ text: String(row.x) },
                                          ...series.map((t) => ({
                                            text: `${t.label}: ${fmtValue(row[t.key])}${unit ? ` ${unit}` : ""}`,
                                            color: t.color }))] });
                       }}
                       {...animProps(animated, anim, s.key, series)}>
                    {levelOf(s) ? rows.map((r, i) => (
                      <Cell key={i} className="chart-band-cell"
                            fillOpacity={pop && pop.key !== i ? 0.3 : 1}
                            fill={Number.isFinite(Number(r[s.key]))
                              ? bandFill(Number(r[s.key]), levelOf(s)) : s.color} />
                    )) : dayAt ? dayAt.map((d, i) => (
                      <Cell key={i} fill={d ? d.color : s.color}
                            fillOpacity={focus && d && d.day !== focus ? 0.16
                              : pop && pop.key !== i ? 0.3 : 1} />
                    )) : rows.map((_r, i) => (
                      <Cell key={i} fillOpacity={pop && pop.key !== i ? 0.3 : 1} />
                    ))}
                    {/* The value at the end of each bar, with its unit. */}
                    {valuesOn && (
                      <LabelList dataKey={s.key} position={horizontal ? "right" : "top"}
                                 className="chart-value-label" fill={AXIS_TEXT}
                                 fontSize={10.5}
                                 formatter={(v) => (v === null || v === undefined ? ""
                                   : `${fmtValue(v)}${unit ? ` ${unit}` : ""}`)} />
                    )}
                  </Bar>
                ))}
                {template === "gradient" && (
                  <defs>
                    {series.map((s) => (
                      <linearGradient key={s.key} id={`${gid}-${s.key}`}
                                      x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={s.color} stopOpacity={0.95} />
                        <stop offset="100%" stopColor={s.color} stopOpacity={0.25} />
                      </linearGradient>
                    ))}
                  </defs>
                )}
              </BarChart>
            </ResponsiveContainer>
            <ChartPopup pop={pop} />
          </div>
          {/* The legend names the series -- one included, except on a small
              tile where the title already does -- and each name is a switch. */}
          {legendBox}
          {dayKey}
          {/* The step was widened to fit a long range: said, in grey. */}
          {(data.step_note || chartNote) && (
            <div className="stat-sub chart-step-note">
              {[data.step_note, chartNote].filter(Boolean).join(" ")}
            </div>
          )}
          </div>
        );

      } else {
        // line / area / spline. `area` fills by definition; `gradient` makes
        // any of them fade to nothing, which is the treatment images 5-10 use.
        // The four richer treatments are whole LOOKS, not one tweak each:
        // stacked shares of a total, a lit headline series, a hatched fill,
        // and a projection band. They all still draw the same `series` from
        // the same query -- the template decides only how it is painted.
        const stacked = template === "stacked";
        const glow = template === "glow";
        const stripe = template === "stripe";
        const forecast = template === "forecast";
        const gradient = template === "gradient" || stacked || glow || stripe;
        // `forecast` is a LINE over a hatched band, so it needs both marks in
        // one chart -- which recharts only allows inside an AreaChart (Line
        // renders happily there; Area does not render inside a LineChart).
        const filled = kind === "area" || gradient || forecast;
        const Chart = filled ? AreaChart : LineChart;
        const curve = kind === "spline" ? "monotone"
                    : kind === "line" ? "linear" : "monotone";
        // `markers` draws every sample; the default draws the line alone,
        // because a dense history turns dots into a smear. `glow` is markers
        // with the light on -- the same reason it is only worth it on a
        // headline chart with few points.
        const dot = template === "markers" ? { r: 3, strokeWidth: 0 }
          : glow ? { r: 4, strokeWidth: 2, stroke: "var(--surface, #16181d)",
                     filter: `url(#${gid}-dotglow)` }
          : false;
        const dash = template === "dashed" ? "5 4" : undefined;
        // Stacked: the LAST series is the primary one (drawn on top, solid);
        // the ones under it are dashed, so a share reads as a share rather
        // than as three charts that happen to touch.
        const primary = series.length ? series[series.length - 1].key : null;

        // Threshold bands. The thresholds themselves are drawn whatever else
        // is going on -- a limit you can see is worth having even on a chart
        // with four series. The COLOURING is narrower: it applies to a single
        // series only, because "the line's colour is its level" and "each
        // line's colour is which line it is" are two different meanings for
        // one channel, and a stacked chart's shares are not levels at all.
        const bands = (widget.bands && Array.isArray(widget.bands.at)
                       && widget.bands.at.length) ? widget.bands : null;
        const banded = !!bands && series.length === 1 && !stacked && !forecast;
        // A series' OWN bands (per camera): that series' line is coloured by
        // ITS levels, whatever the others do, and its limits are drawn in its
        // name. A series without its own follows the shared rule above.
        const ownBanded = !stacked && !forecast && series.some((s) => s.bands);
        const ownStops = {};
        if (ownBanded) {
          for (const s of series) {
            if (!s.bands) continue;
            const vals = rows.map((r) => Number(r[s.key])).filter(Number.isFinite);
            if (vals.length) {
              ownStops[s.key] = bandStops(s.bands.at, s.bands.colors,
                                          Math.min(...vals), Math.max(...vals));
            }
          }
        }
        // Thresholds and days both want the line's colour, and they cannot
        // both have it: "the colour is the level" and "the colour is the day"
        // are two meanings for one channel. Thresholds win where both are set
        // -- a limit crossed is the more urgent reading -- and the day key
        // below still names the days.
        const dayCuts = !banded && !ownBanded && !stacked && !forecast
          ? dayStops(spans, rows.length, focus) : null;
        // The fill under a day-coloured area follows the same cuts at a wash,
        // so the shape is tinted by day rather than only outlined by it.
        const dayFill = dayCuts
          ? dayCuts.map((st) => ({ ...st, opacity: st.opacity * 0.3 })) : null;
        let stops = null;
        if (banded) {
          const key = series[0].key;
          const nums = rows.map((r) => Number(r[key])).filter(Number.isFinite);
          if (nums.length) {
            stops = bandStops(bands.at, bands.colors,
                              Math.min(...nums), Math.max(...nums));
          }
        }
        // A threshold above everything the chart plots would sit off the top
        // of it -- drawn, and invisible, which is the one thing a limit line
        // must never be. So the axis is asked to make room for it.
        //
        // ...and to make it a ROUND number. The 8% headroom used to be the
        // axis top itself, so a limit at 9 gave an axis ending at 9.72 -- a
        // figure nobody set, printed at the top of the chart where the limit
        // sits, and read as the limit having drifted (customer box, "I set
        // 10 and it shows 9.72"). The headroom is still there; the tick it
        // lands on is the next clean one.
        const ownLimits = series.flatMap((s) => (s.bands ? s.bands.at : []));
        const yTop = bands || ownLimits.length
          ? niceTop(Math.max(data.max ?? 0, ...(bands ? bands.at : []), ...ownLimits,
                             ...rows.flatMap((r) => series.map((s) => Number(r[s.key])))
                                    .filter(Number.isFinite)) * 1.08)
          : (data.max ?? "auto");

        body = (
          <div className="chart-block">
          <div className="chart-wrap" ref={plotRef} style={chartH}
               onClick={(e) => {
                 const i = nearestLine(plotRef.current, e, series.length, filled);
                 setLineSel(i === null || i === undefined ? null : series[i]?.key ?? null);
               }}>
            <ResponsiveContainer width="100%" height="100%" onResize={onBox}>
              <Chart data={rows}
                     margin={{ top: valuesOn || lineSel !== null ? 14 : 6, right: 12, left: 0,
                                           bottom: 0 }}
                     onClick={dayCuts ? (e) => pickDay(dayOf(e?.activeLabel)) : undefined}
                     style={dayCuts ? { cursor: "pointer" } : undefined}>
                <defs>
                  {gradient && series.map((s) => (
                    <linearGradient key={s.key} id={`${gid}-${s.key}`}
                                    x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={s.color}
                            stopOpacity={stacked ? 0.5 : 0.45} />
                      <stop offset="100%" stopColor={s.color}
                            stopOpacity={stacked ? 0.1 : 0} />
                    </linearGradient>
                  ))}
                  {/* A hatch, not a second hue: it survives greyscale, and it
                      reads as "not a solid measurement" on a forecast. */}
                  {(stripe || forecast) && series.map((s) => (
                    <pattern key={s.key} id={`${gid}-${s.key}-hatch`}
                             patternUnits="userSpaceOnUse" width="6" height="6">
                      {forecast && (
                        <rect width="6" height="6" fill={s.color} opacity="0.04" />
                      )}
                      {stripe && (
                        <rect width="6" height="6" fill={`url(#${gid}-${s.key})`} />
                      )}
                      <path d="M0,6 L6,0" stroke={s.color} strokeWidth="0.8"
                            opacity="0.35" />
                    </pattern>
                  ))}
                  {glow && (
                    <filter id={`${gid}-dotglow`} x="-50%" y="-50%"
                            width="200%" height="200%">
                      <feGaussianBlur stdDeviation="3" result="b" />
                      <feComposite in="SourceGraphic" in2="b" operator="over" />
                    </filter>
                  )}
                  {glow && (
                    <filter id={`${gid}-lineglow`} x="-10%" y="-20%"
                            width="120%" height="140%">
                      <feGaussianBlur stdDeviation="6" result="b" />
                      <feComposite in="SourceGraphic" in2="b" operator="over" />
                    </filter>
                  )}
                  {Object.entries(ownStops).map(([key, list]) => (
                    <linearGradient key={`own-${key}`} id={`${gid}-bands-${key}`}
                                    x1="0" y1="0" x2="0" y2="1">
                      {list.map((st, i) => (
                        <stop key={i} offset={`${(st.off * 100).toFixed(3)}%`}
                              stopColor={st.color} />
                      ))}
                    </linearGradient>
                  ))}
                  {stops && (
                    <linearGradient id={`${gid}-bands`}
                                    x1="0" y1="0" x2="0" y2="1">
                      {stops.map((s, i) => (
                        <stop key={i} offset={`${(s.off * 100).toFixed(3)}%`}
                              stopColor={s.color} />
                      ))}
                    </linearGradient>
                  )}
                  {/* Days run ACROSS the picture, so this gradient is
                      horizontal where the band one is vertical -- and its
                      offsets are sample positions rather than values. */}
                  {dayCuts && [["days", dayCuts], ["daysfill", dayFill]].map(
                    ([name, list]) => (
                      <linearGradient key={name} id={`${gid}-${name}`}
                                      x1="0" y1="0" x2="1" y2="0">
                        {list.map((st, i) => (
                          <stop key={i} offset={`${(st.off * 100).toFixed(3)}%`}
                                stopColor={st.color} stopOpacity={st.opacity} />
                        ))}
                      </linearGradient>
                    ))}
                </defs>
                <CartesianGrid {...GRID} strokeDasharray="" stroke="rgba(148,163,184,.28)" />
                <XAxis {...catAxis} />
                <YAxis {...valueAxis} domain={[0, yTop]} />
                {/* Hover a point for its value; a click on a line prints every
                    value of that line (lineSel). */}
                <Tooltip {...TOOLTIP} labelFormatter={spanLabel} formatter={tipValue} />

                {/* The thresholds themselves: dashed, in the colour of the
                    band ABOVE each one, and labelled with their value. Dashed
                    because a solid rule at the same weight as the data reads
                    as another series. Behind the line, so a value sitting
                    exactly on its limit is still the thing you can see. */}
                {bands && bands.at.map((t, i) => (
                  <ReferenceLine key={`thr-${i}`} y={t}
                                 stroke={bands.colors[i + 1]}
                                 strokeDasharray="4 4" strokeWidth={1}
                                 ifOverflow="extendDomain"
                                 // Inside the plot: "right" put the value in
                                 // the margin, which the card clips, so the
                                 // one number that says what the line IS
                                 // was the one you could not see.
                                 label={{ value: String(t), position: "insideTopRight",
                                          fontSize: 9,
                                          fill: bands.colors[i + 1] }} />
                ))}
                {/* A series' own limits, dashed in the colour of the band
                    above each, named by the series. */}
                {series.filter((s) => s.bands).flatMap((s) => s.bands.at.map((t, i) => (
                  <ReferenceLine key={`thr-${s.key}-${i}`} y={t} className="chart-threshold"
                                 stroke={s.bands.colors[i + 1]} strokeDasharray="4 4"
                                 strokeWidth={1} ifOverflow="extendDomain"
                                 label={{ value: `${s.label}: ${t}`, position: "insideTopLeft",
                                          fontSize: 9, fill: s.bands.colors[i + 1] }} />
                )))}
                {series.map((s, si) => {
                  const picked = lineSel === s.key;
                  const faded = lineSel !== null && !picked;
                  const pickProps = {
                    className: "chart-pickable chart-ser-" + si,
                    ...(faded ? { opacity: 0.18 } : null),
                  };
                  // Numbers on screen (a picked line, or "On"): the series
                  // does not replay its draw on each new sample, so a point
                  // that is already there stays put and only the new one
                  // arrives.
                  const still = (valuesOn || picked) ? { isAnimationActive: false } : null;
                  // The value at each point, with its unit: when there are few,
                  // or on the line a click picked (forecast lines too).
                  const pointValues = (valuesOn || picked) ? (
                    <LabelList dataKey={s.key} position="top" className="chart-value-label"
                               isAnimationActive={false}
                               fill={AXIS_TEXT} fontSize={10.5}
                               formatter={(v) => (v === null || v === undefined ? ""
                                 : `${fmtValue(v)}${unit ? ` ${unit}` : ""}`)} />
                  ) : null;
                  if (forecast) {
                    // The band and the line are the same numbers drawn twice:
                    // the band gives the projection its texture, the line
                    // gives it a value you can read off.
                    //
                    // So the band is kept out of the LEGEND and out of the
                    // TOOLTIP both. Hiding it from the legend alone still left
                    // the hover reading "Total Violation: 0" twice, one number
                    // listed under two names that were the same name -- which
                    // reads as two series where there is one.
                    return [
                      <Area key={`${s.key}-band`} type={curve} dataKey={s.key}
                            name={s.label} legendType="none" tooltipType="none"
                            stroke="none" {...(faded ? { opacity: 0.18 } : null)}
                            fill={`url(#${gid}-${s.key}-hatch)`}
                            connectNulls={series.length === 1} {...animProps(animated, anim, s.key, series)} />,
                      <Line key={s.key} type={curve} dataKey={s.key}
                            name={s.label} stroke={s.color} strokeWidth={picked ? 4 : 2.5}
                            {...pickProps}
                            dot={false} activeDot={{ r: 4 }}
                            connectNulls={series.length === 1} {...animProps(animated, anim, s.key, series)} {...still}>
                        {pointValues}
                      </Line>,
                    ];
                  }
                  // Banded: the stroke is the gradient, so the LINE carries
                  // the level. The fill under an area stays the series colour
                  // -- a fill's bounding box runs from the line down to the
                  // baseline, not across the values, so the same gradient
                  // would put its colour changes somewhere else entirely.
                  const strokeOf = ownStops[s.key] ? `url(#${gid}-bands-${s.key})`
                    : stops ? `url(#${gid}-bands)`
                    : dayCuts ? `url(#${gid}-days)` : s.color;
                  if (!filled) {
                    return (
                      <Line key={s.key} type={curve} dataKey={s.key} name={s.label}
                            stroke={strokeOf} strokeWidth={picked ? 3.5 : 2} strokeDasharray={dash}
                            {...pickProps}
                            dot={(valuesOn || picked) && !dot ? { r: 2.5, strokeWidth: 0, fill: s.color } : dot}
                            activeDot={{ r: 4 }}
                            connectNulls={series.length === 1} {...animProps(animated, anim, s.key, series)} {...still}>
                        {pointValues}
                      </Line>
                    );
                  }
                  const supporting = stacked && s.key !== primary;
                  return (
                    <Area key={s.key} type={curve} dataKey={s.key} name={s.label}
                          stackId={stacked ? "1" : undefined}
                          stroke={strokeOf}
                          {...pickProps}
                          strokeWidth={supporting ? 0.8 : picked ? 3.5 : 2}
                          strokeDasharray={supporting ? "3 3" : dash}
                          filter={glow ? `url(#${gid}-lineglow)` : undefined}
                          fill={dayCuts ? `url(#${gid}-daysfill)`
                                : stripe ? `url(#${gid}-${s.key}-hatch)`
                                : gradient ? `url(#${gid}-${s.key})`
                                : hexToRgba(s.color, 0.22)}
                          dot={dot} activeDot={{ r: glow ? 6 : 4 }}
                          // ONE series: history has holes (app restarts, quiet
                          // periods write no sample), and without this every
                          // hole cuts the line into lonely dots.
                          //
                          // SEVERAL series: the holes mean something else.
                          // Inputs from different sources are resampled onto
                          // one axis, so a missing point is "this input had
                          // nothing there" -- bridging it draws a value
                          // nobody recorded, and drawing it as zero claims a
                          // measurement of zero. A gap is the truth.
                          connectNulls={series.length === 1} {...animProps(animated, anim, s.key, series)} {...still}>
                      {pointValues}
                    </Area>
                  );
                })}
              </Chart>
            </ResponsiveContainer>
          </div>
          {/* The legend names the series -- one included, except on a small
              tile where the title already does -- and each name is a switch. */}
          {legendBox}
          {dayKey}
          {/* The step was widened to fit a long range: said, in grey. */}
          {(data.step_note || chartNote) && (
            <div className="stat-sub chart-step-note">
              {[data.step_note, chartNote].filter(Boolean).join(" ")}
            </div>
          )}
          </div>
        );
      }
    }
  }

  // `bg`: the card's OWN paint, independent of `template` and `color` --
  // those decide what a chart's stroke or a tile's accent/outline/soft
  // treatment reads; this is "make this specific card blue" regardless of
  // any of that. Applied last, so it wins the background/border a template
  // set, and its own contrast-checked text colour (readableTextOn) rides the
  // container so plain text -- the title, and a tile's value when no other
  // rule already claims its own colour -- inherits it rather than vanishing
  // against a background the operator picked without knowing the theme's
  // usual text colour would fight it.
  const bgExtra = widget.bg
    ? { background: widget.bg, borderColor: widget.bg, color: readableTextOn(widget.bg) }
    : null;

  // The animation rides on the CARD, so a fade or a slide moves the title
  // with the picture rather than sliding a chart out from under its own
  // heading. `key` is what replays it: React remounts the card when the
  // signature moves, and the signature only moves if the operator asked for
  // the animation to play again on new data.
  return (
    <div key={animSig && lineSel === null && widget.data_labels !== "on" ? `${id}-${animSig}` : undefined}
         className={`stat-card${animClass ? " " + animClass : ""}`}
         // The two text sizes travel as CSS VARIABLES rather than as inline
         // font sizes, so the stylesheet keeps its say: the phone and tablet
         // rules still shrink a big number, they just shrink the chosen size
         // instead of a fixed one.
         style={{ ...style, ...cardExtra, ...bgExtra,
                  ...(csvButton ? { position: "relative" } : null),
                  // What a pinned table header paints itself with (a sticky
                  // header needs a SOLID background to hide the rows under it).
                  ...(bgExtra ? { "--wgt-head-bg": bgExtra.background } : null),
                  "--label-scale": scaleOf(widget.label_size),
                  "--value-scale": scaleOf(widget.value_size) }}>
      {/* The title bar, unless the card's "Show title" says no (Tok Aba,
          2026-10-14; off by default on a Text or Image card, which is its own
          content). Hidden, the AI-off / paused badges still say themselves;
          the title stays in the layout as the card's name in the Builder. */}
      {(showTitle || data?.ai_badge || data?.paused_badge) && (
      <div className="stat-label"
           style={{ ...(fill ? { flexShrink: 0 } : null),
                    /* contrast-ok: both branches are reached only when the CARD
                       carries its own paint -- bgExtra picks white or near-black
                       against the operator's chosen background by luminance
                       (readableTextOn), and cardExtra is a template that has
                       already filled the card. */
                    ...(bgExtra ? { color: hexToRgba(bgExtra.color === "#fff" ? "ffffff" : "0a0a0a", .9) }
                      : cardExtra?.color ? { color: "rgba(255,255,255,.9)" } : null) }}>
        {showTitle && title}
        {/* Partly (or only historically) about what the AI measures: a
            chart keeps its history, a kept tally its real number, a mixed
            card its free values -- the badge says why the rest stopped. */}
        {data?.ai_badge && (
          <span data-ai-off="1" className="muted small"
                title="AI detection is off - this reads what the camera sees"
                style={{ marginLeft: 6, opacity: 0.8 }}>AI OFFLINE</span>
        )}
        {/* Partly behind a paused Class Filter: the kept numbers stay, the
            rest are blanked, and the badge says why. */}
        {data?.paused_badge && !data?.ai_badge && (
          <span data-paused="1" className="muted small"
                title={data.paused_filter
                  ? `The "${data.paused_filter}" Class Filter is switched off`
                  : "Its Class Filter is switched off"}
                style={{ marginLeft: 6, opacity: 0.8 }}>paused</span>
        )}
      </div>
      )}
      {/* The table's own download, top right, whether or not a title bar is
          drawn (TableCsvDialog). */}
      {csvButton && (
        <>
          <button type="button" className="table-csv-btn no-print" title="Download CSV"
                  aria-label="Download CSV" onClick={() => setCsvOpen(true)}>
            <Download size={12} aria-hidden="true" /> CSV
          </button>
          {csvOpen && (
            <TableCsvDialog open onClose={() => setCsvOpen(false)} widget={widget}
                            pageRange={exportCsv.pageRange || null} />
          )}
        </>
      )}
      {/* When placed, the body takes whatever is left under the title - so the
          card's height is the operator's decision, not the content's. */}
      {fill
        ? <div style={{ flex: 1, minHeight: 0, display: "flex",
                        flexDirection: "column",
                        // A table starts at the top and scrolls; centring it
                        // is what pushed its header up over the title.
                        // ...and so does a tick list, which grows downwards.
                        justifyContent: statPos ? FLEX_OF[statPos.valign]
                          : ["table", "ai_classes", "class_filters", ...STATIC_CARD_KINDS]
                            .includes(kind) ? "flex-start" : "center",
                        overflowY: ["ai_classes", "class_filters"].includes(kind)
                          ? "auto" : undefined }}>{body}</div>
        : body}
    </div>
  );
}
