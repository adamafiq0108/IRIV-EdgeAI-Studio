// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// DayStrip - the days of one span, as a row of pickable columns.
//
// The date-range picker above it answers "which period am I looking at";
// this answers "which DAY of it", which is the question an operator asks far
// more often and which a range picker makes into three clicks. Today is
// selected on open, so the page lands on what is happening now.
//
// The strip shows the days of the CURRENT range: a month by default (the
// analytics page's own default), or whatever custom span was applied. Long
// spans scroll horizontally rather than shrinking to unreadable columns -
// a 90-day range is still a strip of 90 legible days, not a grey smear.
import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Local YYYY-MM-DD. toISOString() would give the UTC day, which is the
 *  wrong date for anyone west of it - the same rule the pages use. */
export function isoDay(d) {
  const x = new Date(d);
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}`
       + `-${String(x.getDate()).padStart(2, "0")}`;
}

/** Every day in [start, end] as YYYY-MM-DD, capped so a pasted decade-long
 *  range cannot render 3650 columns. */
function daysBetween(start, end, cap = 120) {
  const out = [];
  const a = new Date(`${start}T00:00:00`);
  const b = new Date(`${end}T00:00:00`);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return out;
  for (let d = a; d <= b && out.length < cap; d.setDate(d.getDate() + 1)) {
    out.push(isoDay(d));
  }
  return out;
}

/**
 * @param start,end  the span to lay out (YYYY-MM-DD)
 * @param value      the selected day, or "" for the whole span
 * @param onPick     (day) => void - the same day again clears back to the span
 * @param counts     optional {day: number} of events per day -- title text
 *                   only; the dot is reserved for today (see below)
 * @param allSelected  paint EVERY day selected: the page is showing the whole
 *                   span, and a strip with nothing lit while every widget is
 *                   full of data read as "nothing is selected" when the truth
 *                   is "all of it is". Clicking any day still narrows to it.
 */
export default function DayStrip({ start, end, value, onPick, counts,
                                   allSelected = false }) {
  const days = daysBetween(start, end);
  const today = isoDay(new Date());
  const railRef = useRef(null);
  const selRef = useRef(null);
  // Which ends the rail can still travel towards, so an arrow that would do
  // nothing is disabled rather than dead under the finger.
  const [ends, setEnds] = useState({ left: false, right: false });

  const syncEnds = () => {
    const el = railRef.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    setEnds({ left: el.scrollLeft > 2, right: el.scrollLeft < max - 2 });
  };

  // Keep the chosen day in view - with a month on screen the selection is
  // often past the right edge on open, and a strip whose selection cannot be
  // seen looks like nothing is selected at all.
  useEffect(() => {
    const el = selRef.current;
    if (el && railRef.current) {
      el.scrollIntoView({ block: "nearest", inline: "center" });
    }
    // after the scroll settles, not during it
    const t = setTimeout(syncEnds, 60);
    return () => clearTimeout(t);
  }, [value, start, end]);

  useEffect(() => {
    syncEnds();
    const el = railRef.current;
    if (!el) return undefined;
    const onResize = () => syncEnds();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [days.length]);

  if (!days.length) return null;

  // One press travels most of a screenful, keeping a couple of days of
  // context: paging by the exact width loses the day you were looking at.
  const nudge = (dir) => {
    const el = railRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * Math.max(120, el.clientWidth * 0.8),
                  behavior: "smooth" });
    setTimeout(syncEnds, 320);
  };

  // The icon arrives as an ELEMENT, not a component: a component in a local
  // variable reads as an undefined reference to the repo's JSX checker, and
  // a build that renders a blank page is not worth the one saved character.
  const arrow = (dir, icon, label) => (
    <button type="button" className="btn btn-sm" aria-label={label} title={label}
            disabled={!(dir < 0 ? ends.left : ends.right)}
            onClick={() => nudge(dir)}
            style={{ flex: "0 0 auto", padding: "6px 6px", lineHeight: 0,
                     opacity: (dir < 0 ? ends.left : ends.right) ? 1 : 0.35 }}>
      {icon}
    </button>
  );

  return (
    // day-strip: named so the print stylesheet can leave it off the PDF --
    // it is a picker, and a picker on paper is clutter.
    <div className="day-strip"
         style={{ display: "flex", alignItems: "center", gap: 6,
                  padding: "2px 0 6px" }}>
      {/* Arrows rather than a scrollbar: the strip is one row of small
          targets, and dragging a 4px bar to reach the 3rd of the month is a
          fiddly way to ask for a date. The rail still scrolls by wheel and
          touch for anyone who prefers it. */}
      {arrow(-1, <ChevronLeft size={16} />, "Earlier days")}
      <div ref={railRef} onScroll={syncEnds}
           style={{ display: "flex", gap: 2, overflowX: "auto", flex: 1,
                    minWidth: 0, scrollbarWidth: "none",
                    msOverflowStyle: "none" }}>
      {days.map((day) => {
        const d = new Date(`${day}T00:00:00`);
        // A day is LIT if it is the chosen one, or the whole span is what
        // the page is showing (range mode) -- but `on` (the chosen one)
        // keeps its meaning for the click handler, so clicking a lit day in
        // range mode narrows to it rather than clearing.
        const on = value === day;
        const lit = on || (allSelected && !value && day <= today);
        const isToday = day === today;
        const future = day > today;
        return (
          <button
            key={day}
            type="button"
            ref={on ? selRef : undefined}
            title={d.toLocaleDateString(undefined, { weekday: "long", day: "numeric",
                                                     month: "long", year: "numeric" })
                   + (counts && counts[day]
                     ? ` - ${counts[day]} event${counts[day] === 1 ? "" : "s"}`
                     : "")}
            // A day past today holds nothing by definition; it is shown (the
            // span is the span) but dimmed, so the strip does not invite a
            // click that can only return an empty page.
            onClick={() => onPick(on ? "" : day)}
            style={{
              flex: "0 0 auto", minWidth: 54, padding: "7px 8px 6px",
              display: "flex", flexDirection: "column", alignItems: "center",
              gap: 1, borderRadius: 10, cursor: "pointer",
              border: "1px solid " + (lit ? "var(--accent, #04bade)" : "transparent"),
              // Range mode paints every day a QUIETER accent than a chosen
              // day would be: thirty fully-saturated columns is a wall, and
              // the single-day state must still be able to out-shout it.
              background: on ? "var(--accent, #04bade)"
                : lit ? "color-mix(in srgb, var(--accent, #04bade) 60%, transparent)"
                : "transparent",
              color: lit ? "#fff"
                : future ? "var(--muted2, #667)"
                : isToday ? "var(--accent, #04bade)" : "var(--text, inherit)",
              opacity: future ? 0.55 : 1,
            }}
          >
            <span style={{ fontSize: 11, lineHeight: 1.15,
                           color: lit ? "rgba(255,255,255,.85)" : "var(--muted, #6b6b6b)" }}>
              {isToday ? "Today" : DOW[d.getDay()]}
            </span>
            {/* The DATE is what the eye lands on, so it carries the size --
                the weekday above it is a label for it, not a peer. */}
            <strong style={{ fontSize: 19, lineHeight: 1.15 }}>{d.getDate()}</strong>
            {/* The dot marks TODAY -- the one day worth finding at a glance
                in a strip of thirty, and the day the page opens on. A day
                that merely holds events is not worth a mark of its own here:
                two kinds of dot in a 4px space read as one kind, randomly
                placed. */}
            <span style={{ width: 5, height: 5, borderRadius: 999,
                           background: isToday
                             ? (lit ? "#fff" : "var(--accent, #04bade)")
                             : "transparent" }} />
          </button>
        );
      })}
      </div>
      {arrow(1, <ChevronRight size={16} />, "Later days")}
    </div>
  );
}
