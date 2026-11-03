// THE CHART TYPES the picker offers, defined once for the workflow node
// inspector (pages/Workflow.jsx) and both Builders (builderShared.jsx). The
// picker itself is ChartTypePicker.jsx; the model it writes is chartModel.js.
//
// WHY. Tok Aba (2026-10-14): "make the user CHOOSE the chart type first, then
// show only that chart's settings". A stored chart is still a `kind` plus the
// model's X type -- nothing new is saved -- so a chart TYPE here is a NAME for
// a (kind, x_type) pair the operator recognises:
//
//   Line Chart        line, spline (spline = the "Smooth line" option)
//   Area Chart        area (always drawn smooth, Widget.jsx monotone)
//   Time-based Bar    bar with Time on X
//   Comparison Bar    bar with a column / each input on X
//   Pie / Donut       pie, donut
//   Radar Chart       radar (under Comparisons): shown "Coming soon"
//                     (RADAR_READY) -- a saved radar still opens and edits
//   Scatter Plot      not built yet: shown "Coming soon" (SCATTER_READY)
//
// Pure functions only: no React, no fetch.
import { cleanStep } from "./cardFields.jsx";
import { defaultModel, isGraphKind, setXType, presetPatch } from "./chartModel.js";

//: The next slice flips this on; until then the Scatter card is listed,
//: greyed and not selectable, so the family list reads complete.
export const SCATTER_READY = false;
//: Radar is "Coming soon" too (Tok Aba, 2026-10-14): listed and greyed, so a
//: NEW chart or a type change cannot pick it. A chart already saved as a
//: radar is untouched -- it opens on its properties as a Radar Chart and
//: keeps drawing; only choosing it is closed.
export const RADAR_READY = false;

//: The filter pills, in the mockup's order.
export const CHART_FAMILIES = [
  ["all", "All charts"],
  ["trends", "Trends over time"],
  ["comparisons", "Comparisons"],
  ["proportions", "Proportions"],
  ["relationships", "Relationships"],
];

const TIME_NEXT = "Next, choose the interval and the date range, then what each point counts.";
//: Every card of the picker, in grid order. `kind`/`x` are what it stores;
//: `heading`/`panel` title the properties of that type.
export const CHART_TYPES = [
  { id: "line", name: "Line Chart", heading: "Line Chart (Time Series)", family: "trends",
    kind: "line", x: "time",
    desc: "Track changes and trends over time.", best: "Best for trends",
    panel: "Show trends and changes over time.",
    example: "Violations per day for each camera.", next: TIME_NEXT },
  { id: "area", name: "Area Chart", heading: "Area Chart (Time Series)", family: "trends",
    kind: "area", x: "time",
    desc: "Show trends and emphasize the volume over time.", best: "Best for volume over time",
    panel: "Show trends and emphasize the volume over time.",
    example: "Parts inspected per hour, stacked up as a volume.", next: TIME_NEXT },
  { id: "time_bar", name: "Time-based Bar", heading: "Time-based Bar Chart", family: "trends",
    kind: "bar", x: "time",
    desc: "Compare values across time intervals.", best: "Best for time comparisons",
    panel: "Compare values across time intervals (hours, days, weeks or months).",
    example: "Rejects per day this week, side by side for two cameras.", next: TIME_NEXT },
  { id: "compare_bar", name: "Comparison Bar", heading: "Comparison Bar Chart",
    family: "comparisons", kind: "bar", x: "column",
    desc: "Compare values across cameras or categories.", best: "Best for comparisons",
    panel: "Compare values across cameras, devices or categories.",
    example: "Total violations by camera.",
    next: "Next, choose the category field - each camera, or a column - and what each bar counts." },
  { id: "pie", name: "Pie Chart", heading: "Pie Chart", family: "proportions",
    kind: "pie", x: "column",
    desc: "Show how a total is divided into categories.", best: "Best for simple proportions",
    panel: "Show how categories contribute to a whole.",
    example: "Share of each violation type today.",
    next: "Next, choose the category field and what each slice counts." },
  { id: "donut", name: "Donut Chart", heading: "Donut Chart", family: "proportions",
    kind: "donut", x: "column",
    desc: "Show category proportions with a clear center.", best: "Best for proportions",
    panel: "Show category proportions with a clear center.",
    example: "Violations by type, with the total in the middle.",
    next: "Next, choose the category field and what each slice counts." },
  { id: "radar", name: "Radar Chart", heading: "Radar Chart", family: "comparisons",
    kind: "radar", x: "column", coming: !RADAR_READY,
    desc: "Compare several measures across cameras or categories.",
    best: "Best for multi-measure comparison",
    panel: "Compare several measures across cameras or categories.",
    example: "Each camera's count of several violation types.",
    next: "Next, choose the category / metric and what each spoke counts." },
  { id: "scatter", name: "Scatter Plot", heading: "Scatter Plot", family: "relationships",
    kind: "", x: "", coming: !SCATTER_READY,
    desc: "Explore relationships between two numeric values.", best: "Best for relationships",
    panel: "Explore relationships between two numeric values.",
    example: "Detection confidence vs object speed.",
    next: "Next, select two numeric fields for the horizontal and vertical axes. A date filter can optionally limit the records." },
];
export const chartType = (id) => CHART_TYPES.find((t) => t.id === id) || null;
/** Whether a card can be chosen (Scatter is not, until its slice). */
export const selectable = (t) => !!t && !t.coming && !!t.kind;
/** The cards a filter pill shows. */
export const typesFor = (family) => CHART_TYPES.filter(
  (t) => family === "all" || !family || t.family === family);

/** The picker card a stored chart is: kind + its X type. A bar whose X type
 *  is not known yet (a legacy chart read without its wires) is a Time-based
 *  Bar when it steps by time. "" for a card kind (tile, table...). */
export function chartTypeOf(kind, xType, d = null) {
  if (kind === "line" || kind === "spline") return "line";
  if (kind === "area") return "area";
  if (kind === "pie" || kind === "donut" || kind === "radar") return kind;
  if (kind === "bar") {
    const x = xType || (d && (d.x_type || (cleanStep(d.bucket || d.interval) ? "time" : "column")));
    return x === "time" ? "time_bar" : "compare_bar";
  }
  return "";
}

//: "Show as" inside a type's properties: the types of its own family, in
//: the words of the mockup. Switching here keeps every field (one X type).
export const SHOW_AS = {
  trends: [["line", "Line chart"], ["area", "Area chart"], ["time_bar", "Bar chart (Time based)"]],
  comparisons: [["compare_bar", "Bar chart (Category)"], ["radar", "Radar chart"]],
  proportions: [["pie", "Pie chart"], ["donut", "Donut chart"]],
};

const INTERVAL_WORDS = { hour: "Hour", day: "Day", week: "Week", month: "Month", custom: "Custom" };
const RANGE_WORDS = { "": "All time", page: "Follow the Analytics date picker" };

/** Changing a chart to another type: the kind and model it becomes, and --
 *  in plain words, BEFORE it happens -- what does not carry over.
 *
 *  Kept whatever the type: title, inputs (names, Show, overrides, colours),
 *  Y calculation and column, date range, unit, thresholds. What a type has
 *  no place for is the only thing dropped: the interval going to a category
 *  X, the category field going to a time X, Smooth line going off a line.
 *
 *  `full` says the surface must write the whole model (nodePatch /
 *  widgetPatch): the X type changed, or the chart crosses between a graph
 *  kind and a chart kind, whose legacy reading rules differ (a legacy line
 *  turned into a bar by its kind alone could read back as a Comparison Bar),
 *  or a new chart takes another type's defaults. Otherwise the kind alone is
 *  written, exactly as the old "Show as" did; `same` (the type it already
 *  is) writes nothing. */
export function planTypeSwitch(fromKind, model, toId, { fresh = false } = {}) {
  const to = chartType(toId);
  const from = chartType(chartTypeOf(fromKind, model && model.x_type));
  if (!selectable(to)) return null;
  // A new chart starts from the chosen type's own defaults (a Comparison Bar
  // reads all time, a Line Chart the last 24 hours, hourly), as a chart
  // dropped with that kind always did.
  const base = fresh || !model ? defaultModel(to.kind) : model;
  let kind = to.kind;
  if (to.id === "line" && fromKind === "spline") kind = "spline";
  const dropped = [];
  let next = base;
  // A line or area keeps a Database-column X it already had (allowed on a
  // graph); every other time type puts Time on X.
  const keepColumn = to.x === "time" && base.x_type === "column"
    && ["line", "area"].includes(to.id) && from && ["line", "area"].includes(from.id);
  const wantX = keepColumn ? "column" : to.x;
  if (wantX !== base.x_type) {
    if (wantX === "time") {
      if (base.x_field) {
        dropped.push(`The category field "${base.x_field}" - a time axis groups by time instead.`);
      }
      next = setXType(base, "time", kind);
      // The date range is the operator's and carries over; only "All time"
      // has no place on a time axis, which needs a stretch of time.
      if (base.range) {
        next = { ...next, range: base.range, range_from: base.range_from || "",
                 range_to: base.range_to || "" };
      } else {
        const p = presetPatch(base, isGraphKind(kind) ? "hour" : "day");
        dropped.push(`The date range "All time" - it becomes "${p.range === "24h"
          ? "Last 24 hours" : "Last 7 days"}", as a time axis needs a stretch of time.`);
      }
      next = { ...next, x_field: "" };
    } else {
      if (base.time_interval || base.step) {
        dropped.push(`The interval (${INTERVAL_WORDS[base.time_interval] || "Custom"}) - a ${to.name} has no time axis.`);
      }
      next = { ...base, x_type: "column", x_field: "", time_interval: "", step: "" };
    }
  }
  if (fromKind === "spline" && kind !== "spline") {
    dropped.push(`"Smooth line" - it applies to a Line Chart only${to.id === "area" ? " (an area is always drawn smooth)" : ""}.`);
  }
  // The type it already is: nothing to write (a new chart keeps the kind it
  // was dropped with, untouched, exactly as before the picker existed).
  const same = !!from && from.id === to.id;
  const full = !same && (fresh || next !== base
                         || isGraphKind(fromKind) !== isGraphKind(kind));
  return { kind, model: next, full, same, dropped: fresh || same ? [] : dropped,
           rangeWords: RANGE_WORDS[next.range] };
}

// -- which chart has not had its type chosen yet ---------------------------------
//
// A chart node dropped on the canvas, or a chart widget dragged into a
// Builder, opens on the picker. Held for the session only, by id: nothing
// about it is saved, so a page reload opens the chart on its properties (its
// default kind stands) and no stored chart can ever be mistaken for a new one.
const FRESH = new Set();
export const markFreshChart = (id) => { if (id) FRESH.add(id); };
export const isFreshChart = (id) => FRESH.has(id);
export const clearFreshChart = (id) => { FRESH.delete(id); };
