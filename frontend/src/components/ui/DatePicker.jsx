// DatePicker - one day, from a calendar whose caption is a month and a year
// dropdown rather than a pair of arrows.
//
// Replaces <input type="date">, which renders as a different control in every
// browser, cannot be styled to match anything around it, and - the reason it
// actually had to go - makes reaching a date a year back a matter of clicking
// an arrow twelve times. The dropdown caption is the fix: any month of any
// allowed year in two clicks. Navigation arrows are gone entirely rather than
// kept beside the dropdowns, which would be two controls doing one job.
//
// Written against the app's own tokens and its own date maths, like
// DateRangePicker beside it - no react-day-picker/date-fns. What is needed
// here is a month grid and four date helpers, which is less code than the
// adapter around a library would be, and it keeps the picker looking like the
// rest of the product on a box that ships its own bundle.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Calendar as CalendarIcon } from "lucide-react";

import { POPOVER_Z, fixedPlace, layerAbove, scopeOf } from "./DateRangePicker.jsx";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "./select.jsx";

const DAY = 86400000;
const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d, n) => startOfDay(new Date(startOfDay(d).getTime() + n * DAY));
const startOfMonth = (d) => { const x = startOfDay(d); x.setDate(1); return x; };
const sameDay = (a, b) => a && b && startOfDay(a).getTime() === startOfDay(b).getTime();

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"];

/** The six weeks a month grid shows, including the neighbouring days it needs
 *  to start on a Sunday and end on a Saturday. */
function monthGrid(month) {
  const first = startOfMonth(month);
  const lead = first.getDay();
  const cells = [];
  for (let i = 0; i < 42; i += 1) cells.push(addDays(first, i - lead));
  return cells;
}

/**
 * @param {Date|null} value     the selected day
 * @param {(d:Date)=>void} onChange
 * @param {Date} max            latest selectable day (default today)
 * @param {Date} min            earliest selectable day (default 5 years back)
 */
export default function DatePicker({
  value, onChange, max = new Date(), min = null, className = "",
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState(() => startOfMonth(value || new Date()));
  const boxRef = useRef(null);
  const panelRef = useRef(null);
  // Fixed position from the real rects: the panel is portalled to <body>
  // (2026-10-14), like the range picker's, so no card or table can cover it.
  const [place, setPlace] = useState(null);
  const [scope, setScope] = useState("");
  const [layer, setLayer] = useState(POPOVER_Z);

  const maxDay = useMemo(() => startOfDay(max || new Date()), [max]);
  const minDay = useMemo(() => {
    if (min) return startOfDay(min);
    const d = startOfDay(max || new Date());
    d.setFullYear(d.getFullYear() - 5);
    return startOfMonth(d);
  }, [min, max]);

  // Reopening shows what is currently applied, not the month somebody
  // scrolled to and abandoned last time.
  useEffect(() => {
    if (open) setView(startOfMonth(value || new Date()));
  }, [open, value]);

  // Measured, not guessed: a picker near the bottom or the right edge of the
  // window otherwise opens partly off screen. Same helper the range picker
  // uses, so both flip and nudge by identical rules.
  useLayoutEffect(() => {
    if (!open) return undefined;
    const measure = () => {
      const anchor = boxRef.current, panel = panelRef.current;
      if (!anchor || !panel) return;
      const next = fixedPlace(
        anchor.getBoundingClientRect(),
        { width: panel.offsetWidth, height: panel.scrollHeight },
        { width: window.innerWidth, height: window.innerHeight });
      setPlace((p) => (p && p.left === next.left && p.top === next.top
        && p.maxHeight === next.maxHeight ? p : next));
      setScope(scopeOf(anchor));
      setLayer(layerAbove(anchor));
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    // Captured first: Escape closes the picker, not a dialog around it.
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      setOpen(false);
      boxRef.current?.querySelector("button")?.focus();
    };
    const onDown = (e) => {
      // The month/year dropdowns render in a portal OUTSIDE this box, so a
      // click on one is not "outside the picker" even though the DOM says so.
      if (e.target?.closest?.("[data-slot='select-content']")) return;
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

  // Only the years the picker will actually accept. A list running to 2100 on
  // a control capped at today is a list of choices that do nothing.
  const years = [];
  for (let y = minDay.getFullYear(); y <= maxDay.getFullYear(); y += 1) years.push(y);

  function setMonth(m) {
    const next = startOfMonth(view);
    next.setMonth(Number(m));
    setView(clampView(next));
  }
  function setYear(y) {
    const next = startOfMonth(view);
    next.setFullYear(Number(y));
    setView(clampView(next));
  }
  /** Keep the shown month inside the allowed range - picking "December" in the
   *  current year on a max of today would otherwise show a month in which
   *  every day is disabled. */
  function clampView(m) {
    if (m > startOfMonth(maxDay)) return startOfMonth(maxDay);
    if (m < startOfMonth(minDay)) return startOfMonth(minDay);
    return m;
  }

  const cells = monthGrid(view);
  const label = value
    ? value.toLocaleDateString(undefined,
                               { day: "numeric", month: "short", year: "numeric" })
    : "Pick a date";

  return (
    <div ref={boxRef} className={"relative inline-block " + className}>
      <button type="button" onClick={() => setOpen((v) => !v)}
              className="inline-flex items-center gap-2 rounded-md border
                         bg-background px-2.5 py-1.5 text-sm hover:border-primary/50">
        <CalendarIcon className="size-3.5 text-muted-foreground" />
        {label}
      </button>

      {open && createPortal(
        <div ref={panelRef} data-dp-panel="" role="dialog" aria-label="Pick a date"
             style={{ position: "fixed", zIndex: layer, overflow: "auto",
                      left: place ? place.left : -9999, top: place ? place.top : 0,
                      maxHeight: place ? place.maxHeight : undefined,
                      visibility: place ? "visible" : "hidden" }}
             className={"w-[268px] rounded-xl border bg-popover p-3 "
               + "text-popover-foreground shadow-lg " + scope}>
          {/* The caption IS the navigation. Two dropdowns, full width, no
              arrows beside them. */}
          <div className="flex w-full items-center gap-2">
            <Select value={String(view.getMonth())} onValueChange={setMonth}>
              <SelectTrigger className="h-8 grow text-xs">
                <SelectValue>{MONTHS[view.getMonth()]}</SelectValue>
              </SelectTrigger>
              <SelectContent align="start">
                {MONTHS.map((m, i) => (
                  <SelectItem key={m} value={String(i)}
                              // A month wholly outside the range is shown and
                              // refused, rather than missing: a gap in a list
                              // of months reads as a bug.
                              disabled={startOfMonth(new Date(view.getFullYear(), i, 1))
                                        > startOfMonth(maxDay)}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={String(view.getFullYear())} onValueChange={setYear}>
              <SelectTrigger className="h-8 w-[86px] text-xs">
                <SelectValue>{view.getFullYear()}</SelectValue>
              </SelectTrigger>
              <SelectContent align="start">
                {years.map((y) => (
                  <SelectItem key={y} value={String(y)}>{y}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="mt-2 grid grid-cols-7 gap-0.5">
            {WEEKDAYS.map((w) => (
              <div key={w} className="py-1 text-center text-[10px] text-muted-foreground">
                {w}
              </div>
            ))}
            {cells.map((d) => {
              const selected = sameDay(d, value);
              const disabled = d > maxDay || d < minDay;
              const outside = d.getMonth() !== view.getMonth();
              return (
                <button key={d.getTime()} type="button" disabled={disabled}
                        onClick={() => { onChange?.(d); setOpen(false); }}
                        className={"rounded-md py-1.5 text-xs tabular-nums transition-colors "
                          + (selected
                              ? "bg-primary font-semibold text-primary-foreground"
                              : disabled
                                ? "cursor-default text-muted-foreground/40"
                                : outside
                                  ? "text-muted-foreground/60 hover:bg-muted"
                                  : "hover:bg-muted")}>
                  {d.getDate()}
                </button>
              );
            })}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
