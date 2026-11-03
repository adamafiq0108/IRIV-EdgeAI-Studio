// DateRangePicker - a preset rail beside a two-month calendar.
//
// Replaces a pair of <input type="date"> boxes. Two inputs can express a
// range but cannot SHOW one: picking "the week either side of the 3rd" meant
// reading two dates and holding the span in your head, and the common asks
// (last 7 days, month to date) meant doing arithmetic by hand. The rail
// answers those in one click; the calendar answers the rest by being a
// picture of the span rather than two numbers describing it.
//
// Written against the app's own tokens and its own date maths -- no
// react-day-picker/date-fns. What is actually needed here is a month grid and
// six date helpers, which is less code than the adapter around a library
// would be, and it keeps the picker looking like the rest of the product.
//
// Selection reads as: first click sets the start and clears the end; the next
// click on or after it sets the end. A click BEFORE the start begins a new
// range rather than inverting the old one -- an inverted range is never what
// somebody meant, and silently swapping the ends hides a mis-click.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Calendar as CalendarIcon } from "lucide-react";

const DAY = 86400000;
const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d, n) => startOfDay(new Date(startOfDay(d).getTime() + n * DAY));
const addMonths = (d, n) => {
  const x = startOfDay(d);
  // Set the day to 1 BEFORE moving the month: from the 31st, "+1 month" on a
  // 30-day month rolls into the following one, so Jan 31 + 1 would land in
  // March and the calendar would skip February entirely.
  x.setDate(1);
  x.setMonth(x.getMonth() + n);
  return x;
};
const startOfMonth = (d) => { const x = startOfDay(d); x.setDate(1); return x; };
const sameDay = (a, b) => a && b && startOfDay(a).getTime() === startOfDay(b).getTime();

/** YYYY-MM-DD in LOCAL time. toISOString() would shift the day in any timezone
 *  behind UTC, which is how a "today" filter can come back empty at 9am. */
export function isoDay(d) {
  const x = startOfDay(d);
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}`
       + `-${String(x.getDate()).padStart(2, "0")}`;
}

const fmt = (d) => (d ? startOfDay(d).toLocaleDateString(undefined,
  { month: "short", day: "numeric", year: "numeric" }) : "");

/** The named windows, as [start, end] against a given "today". */
export const PRESETS = [
  ["Today", (t) => [t, t]],
  ["Yesterday", (t) => [addDays(t, -1), addDays(t, -1)]],
  ["Last 7 days", (t) => [addDays(t, -6), t]],
  ["Last 30 days", (t) => [addDays(t, -29), t]],
  ["Month to date", (t) => [startOfMonth(t), t]],
  ["Last month", (t) => {
    const s = addMonths(startOfMonth(t), -1);
    return [s, addDays(startOfMonth(t), -1)];
  }],
  ["Year to date", (t) => [new Date(t.getFullYear(), 0, 1), t]],
  ["Last year", (t) => [new Date(t.getFullYear() - 1, 0, 1),
                        new Date(t.getFullYear() - 1, 11, 31)]],
];

/**
 * Where the popover goes, given the button's rect, the panel's size and the
 * window. Pure, and exported, because jsdom has no layout engine -- every
 * rect it reports is zero -- so the only way to TEST the clamping is to hand
 * it the rectangles a real browser would have measured.
 *
 * `dx` is a nudge applied to a panel that otherwise hangs off the button's
 * right edge; `above` flips it over the button.
 */
export function placePanel(anchor, size, view, margin = 8) {
  const left = anchor.right - size.width;
  let dx = 0;
  if (left + size.width > view.width - margin) {
    dx = view.width - margin - (left + size.width);
  }
  // Left wins: a panel too wide for the window should start at the left edge
  // and scroll, not start off screen where its presets are unreachable.
  if (left + dx < margin) dx = margin - left;
  // Flip up only when there is genuinely more room there; flipping into a
  // second clipped position helps nobody.
  const above = anchor.bottom + 6 + size.height > view.height - margin
             && anchor.top - 6 - size.height > margin;
  return { dx, above };
}

/**
 * The panel's FIXED position (it is portalled to <body>, 2026-10-14): left,
 * top and, when neither below nor above has room, a max height so it scrolls
 * inside the window instead of running off it. Built on placePanel, so the
 * nudge and flip rules are the same ones the tests pin.
 */
export function fixedPlace(anchor, size, view, margin = 8) {
  const { dx, above } = placePanel(anchor, size, view, margin);
  let left = Math.max(margin, anchor.right - size.width + dx);
  // Hanging off the button's right edge would push it past the window's
  // left: open it from the button's LEFT edge instead when that fits, so it
  // still starts under the button rather than at the window's edge.
  if (anchor.right - size.width < margin
      && anchor.left + size.width <= view.width - margin) left = Math.max(margin, anchor.left);
  let top = above ? anchor.top - 6 - size.height : anchor.bottom + 6;
  let maxHeight = view.height - 2 * margin;
  if (top + size.height > view.height - margin) {
    // Below does not fit and above was no better: keep it on screen and let
    // it scroll, Apply included.
    top = Math.max(margin, view.height - margin - size.height);
    maxHeight = view.height - top - margin;
  }
  if (top < margin) top = margin;
  return { left, top, maxHeight, above };
}

/** Where a portalled panel takes its tokens from: the scope its button sits
 *  in. Outside .cy-legacy / .cy-app the shared names (--accent, --muted) mean
 *  something else, so the panel wears the same scope class as its anchor. */
export function scopeOf(el) {
  if (el?.closest?.(".cy-legacy")) return "cy-legacy";
  if (el?.closest?.(".cy-app")) return "cy-app";
  return "";
}

//: THE LAYER MODEL a picker follows (2026-10-14):
//:   sticky table headers 2-5 < page chrome 10-40 < Workflow full screen 45
//:   < a picker on a page (48) < dialogs (shadcn 50, ui-dialog 70, legacy
//:   modals 1000) < a picker opened INSIDE one of them (that layer + 1)
//:   < toasts (.toast-stack, above everything).
//: A picker on a page sits under any dialog; a picker whose button is inside
//: a dialog sits just over that dialog (layerAbove).
export const POPOVER_Z = 48;

/** The z-index a portalled panel needs to sit over everything its button is
 *  drawn inside: the highest z-index among the button's ancestors, plus one,
 *  and never below POPOVER_Z. A button inside a dialog (z 70) gets 71. */
export function layerAbove(el) {
  let top = POPOVER_Z - 1;
  for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
    const z = parseInt(getComputedStyle(e).zIndex, 10);
    if (Number.isFinite(z) && z > top) top = z;
  }
  return top + 1;
}

/** The six weeks a month grid shows, including the neighbouring days it needs
 *  to start on a Sunday and end on a Saturday. */
function monthGrid(month) {
  const first = startOfMonth(month);
  const lead = first.getDay();
  const cells = [];
  for (let i = 0; i < 42; i += 1) cells.push(addDays(first, i - lead));
  return cells;
}

function Month({ month, start, end, hover, max, onPick, onHover }) {
  const cells = monthGrid(month);
  const inMonth = (d) => d.getMonth() === month.getMonth();
  // While only the start is chosen, the hovered day previews the other end --
  // so the shape of the range is visible before it is committed.
  const provisionalEnd = end || (start && hover && hover > start ? hover : null);
  const inRange = (d) => start && provisionalEnd
    && d > startOfDay(start) && d < startOfDay(provisionalEnd);

  return (
    <div style={{ minWidth: 196 }}>
      <div style={{ textAlign: "center", fontWeight: 600, fontSize: 12.5,
                    padding: "2px 0 8px" }}>
        {month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)",
                    gap: 1 }}>
        {WEEKDAYS.map((w) => (
          <div key={w} className="muted" style={{ fontSize: 10, textAlign: "center",
                                                  padding: "2px 0" }}>{w}</div>
        ))}
        {cells.map((d) => {
          const isStart = sameDay(d, start);
          const isEnd = sameDay(d, provisionalEnd);
          const edge = isStart || isEnd;
          const disabled = max && d > startOfDay(max);
          return (
            <button key={d.getTime()} type="button" disabled={disabled}
                    onClick={() => onPick(d)}
                    onMouseEnter={() => onHover(d)}
                    style={{
                      appearance: "none", border: 0, cursor: disabled ? "default" : "pointer",
                      font: "inherit", fontSize: 11.5, lineHeight: 1,
                      padding: "6px 0", borderRadius: edge ? 6 : 0,
                      background: edge ? "var(--accent, #04bade)"
                                : inRange(d) ? "rgba(148,163,184,.16)" : "transparent",
                      color: edge ? "#04121a"
                           : disabled ? "rgba(148,163,184,.35)"
                           : inMonth(d) ? "var(--text, #e8eaed)" : "rgba(148,163,184,.45)",
                      fontWeight: edge ? 700 : 400,
                    }}>
              {d.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * @param {string} start  YYYY-MM-DD, or ""
 * @param {string} end    YYYY-MM-DD, or ""
 * @param {(range:{start:string,end:string}) => void} onApply
 * @param {Date}   max    latest selectable day (defaults to today)
 */
export default function DateRangePicker({ start, end, onApply, max = new Date(),
                                          label = "Date range" }) {
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState(start ? new Date(start) : null);
  const [to, setTo] = useState(end ? new Date(end) : null);
  const [hover, setHover] = useState(null);
  const [view, setView] = useState(() => startOfMonth(end ? new Date(end) : new Date()));
  const boxRef = useRef(null);
  const panelRef = useRef(null);
  // Fixed position, decided from the real rects (the panel lives in <body>).
  const [place, setPlace] = useState(null);
  const [scope, setScope] = useState("");
  const [layer, setLayer] = useState(POPOVER_Z);

  // The panel is ~610px wide and hangs off the button's RIGHT edge, so a
  // button anywhere near the left of the window put half the calendar off
  // screen. Measured rather than guessed: the anchor's rect and the panel's
  // own width are both stable under the transform this writes, so reading
  // them cannot feed back into itself and oscillate.
  useLayoutEffect(() => {
    if (!open) return undefined;
    const measure = () => {
      const anchor = boxRef.current, panel = panelRef.current;
      if (!anchor || !panel) return;
      const next = fixedPlace(
        anchor.getBoundingClientRect(),
        { width: panel.offsetWidth, height: panel.scrollHeight },
        { width: window.innerWidth, height: window.innerHeight });
      // Same-value guard: the scroll listener fires constantly, and a fresh
      // object each time would rerender the calendar on every pixel.
      setPlace((p) => (p && p.left === next.left && p.top === next.top
        && p.maxHeight === next.maxHeight ? p : next));
    };
    setScope(scopeOf(boxRef.current));
    setLayer(layerAbove(boxRef.current));
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open]);

  // Reopening shows what is currently applied, not what was abandoned last
  // time -- a picker that reopens mid-edit is a picker that applies something
  // the operator did not choose.
  useEffect(() => {
    if (!open) return;
    setFrom(start ? new Date(start) : null);
    setTo(end ? new Date(end) : null);
    setHover(null);
    setView(startOfMonth(end ? new Date(end) : new Date()));
  }, [open, start, end]);

  useEffect(() => {
    if (!open) return undefined;
    // Captured on the window, before anything else hears the key: Escape closes
    // the PICKER, and only the picker - a dialog it sits in stays open.
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      setOpen(false);
      // Back to the button the panel came from: the panel is in <body>, and
      // focus must not be left at the end of the document.
      boxRef.current?.querySelector("button")?.focus();
    };
    // The panel is portalled, so "outside" is outside the button AND the panel.
    const onDown = (e) => {
      if (boxRef.current?.contains(e.target) || panelRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  // Opening moves focus into the panel (its first quick choice): it lives at
  // the end of <body>, so Tab from the button would otherwise skip it.
  useEffect(() => {
    if (!open) return undefined;
    const id = requestAnimationFrame(() => panelRef.current?.querySelector("button")?.focus());
    return () => cancelAnimationFrame(id);
  }, [open]);

  const today = useMemo(() => startOfDay(new Date()), []);
  const summary = start && end ? `${fmt(new Date(start))} – ${fmt(new Date(end))}`
                : start ? `${fmt(new Date(start))} – …` : label;

  function pick(d) {
    if (!from || to) { setFrom(d); setTo(null); return; }
    if (d < from) { setFrom(d); setTo(null); return; }
    setTo(d);
  }

  function apply() {
    if (!from || !to) return;
    onApply({ start: isoDay(from), end: isoDay(to) });
    setOpen(false);
  }

  return (
    <div ref={boxRef} style={{ position: "relative", display: "inline-block" }}>
      <button type="button" className="btn btn-sm" onClick={() => setOpen((v) => !v)}
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <CalendarIcon size={13} />
        {summary}
      </button>

      {/* Portalled to <body> (2026-10-14): drawn inside the toolbar card it
          was trapped in that card's stacking context (the entrance animation
          leaves a transform on .panel-card), so a table's sticky header further
          down the page painted OVER it, and an overflow container could clip
          it. Fixed position from the button's rect, on top of everything but
          dialogs and toasts. */}
      {open && createPortal(
        <div ref={panelRef} data-drp-panel="" role="dialog" aria-label={label}
             className={scope || undefined}
             onKeyDown={(e) => { if (e.key === "Escape") e.stopPropagation(); }}
             style={{
          position: "fixed", zIndex: layer,
          left: place ? place.left : -9999, top: place ? place.top : 0,
          visibility: place ? "visible" : "hidden",
          display: "flex", flexWrap: "wrap", background: "var(--surface2, #1d2026)",
          color: "var(--text, #e8eaed)",
          border: "1px solid rgba(128,128,128,.3)", borderRadius: 10,
          boxShadow: "0 12px 32px rgba(0,0,0,.45)", overflow: "auto",
          // A window too small for the whole picker scrolls it rather than
          // cutting the Apply button off, which is the one control that must
          // always be reachable.
          maxWidth: "calc(100vw - 16px)",
          maxHeight: place ? place.maxHeight : "calc(100vh - 16px)",
        }}>
          {/* The rail answers the common asks without arithmetic. */}
          <div style={{ display: "grid", gap: 1, padding: 6, alignContent: "start",
                        borderRight: "1px solid rgba(128,128,128,.2)",
                        minWidth: 132 }}>
            {PRESETS.map(([name, range]) => {
              const [s, e] = range(today);
              const active = sameDay(from, s) && sameDay(to, e);
              return (
                <button key={name} type="button" className="btn btn-sm"
                        style={{ justifyContent: "flex-start", fontSize: 11.5,
                                 background: active ? "rgba(148,163,184,.16)" : "transparent",
                                 border: 0 }}
                        onClick={() => { setFrom(s); setTo(e); setView(startOfMonth(e)); }}>
                  {name}
                </button>
              );
            })}
          </div>

          <div style={{ padding: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button type="button" className="btn btn-sm" title="Previous month"
                      onClick={() => setView((v) => addMonths(v, -1))}>‹</button>
              <div style={{ flex: 1 }} />
              <button type="button" className="btn btn-sm" title="Next month"
                      onClick={() => setView((v) => addMonths(v, 1))}>›</button>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginTop: 4 }}
                 onMouseLeave={() => setHover(null)}>
              <Month month={view} start={from} end={to} hover={hover} max={max}
                     onPick={pick} onHover={setHover} />
              <Month month={addMonths(view, 1)} start={from} end={to} hover={hover}
                     max={max} onPick={pick} onHover={setHover} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8,
                          marginTop: 10, paddingTop: 8,
                          borderTop: "1px solid rgba(128,128,128,.2)" }}>
              {/* NO "Reset" here. The page's own toolbar carries a Reset a
                  few pixels away, and that one means something quite
                  different: it clears the PAGE back to its default range.
                  This one only unpicked the two dates inside an open popover,
                  which is what Cancel already does for anyone who has changed
                  their mind -- two buttons of the same name, one row apart,
                  doing different things. */}
              <div style={{ flex: 1 }} />
              <button type="button" className="btn btn-sm"
                      onClick={() => setOpen(false)}>Cancel</button>
              {/* Disabled until BOTH ends exist: applying a half-range would
                  either guess the other end or silently do nothing. */}
              <button type="button" className="btn btn-sm btn-accent"
                      disabled={!from || !to} onClick={apply}>Apply</button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
