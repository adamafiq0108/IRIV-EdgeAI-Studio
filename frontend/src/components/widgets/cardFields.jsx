// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// ONE definition of a card's properties, for both places a card is edited:
// the workflow node's inspector (pages/Workflow.jsx, dashboard.card and
// dashboard.graph) and the Builders' widget panel (builderShared.jsx
// WidgetInspector, used by the Dashboard and the Analytics builder).
//
// WHY THIS FILE EXISTS. The two panels edit the same card -- since the
// Builders write a compiled card's question back onto its node
// (workflow.CARD_QUESTION_FIELDS), literally the same stored fields -- and
// they had drifted into two vocabularies: the node said "Show" where the
// Builder said "Work out", "average value of a column" where the Builder said
// "average value", "Count" with two choices where the Builder offered eight
// raw names under "Aggregation", "Range" beside "Time range", "Visualization"
// beside "Show as". An operator who set a card in one place could not find the
// setting in the other (owner report, 2026-10-12: "it is confusing").
//
// So the labels, the hints, the order and every option list live HERE, and
// both panels render these components. What stays per panel is what really
// differs: the node's wiring rows (Source, a wired column's summary, the
// Inputs rows of a several-input chart, its Time axis), and the Builder's own
// source picker, appearance and layout sections -- and, by rule, a CHART's
// colours, which only the Builders offer (showsColours, below).
//
// Stored values are unchanged -- this file only names them.
import { useId, useState } from "react";
import {
  AlignCenter, AlignLeft, AlignRight, AlignVerticalJustifyCenter, AlignVerticalJustifyEnd,
  AlignVerticalJustifyStart,
} from "lucide-react";
import { Field as UiField } from "../ui/primitives.jsx";

//: The field set, in the order both panels show it. Everything a panel shows
//: of a card's question comes from here, so a label changed here changes in
//: both places at once.
export const CARD_FIELDS = {
  title: { label: "Title" },
  caption: { label: "Caption",
             hint: "The small line under the value. Blank shows nothing." },
  shape: { label: "Show as", hint: "What shape the card draws." },
  agg: { label: "Work out", hint: "What to work out from every recorded row." },
  column: { label: "Of which column" },
  // The X AXIS FIELD: one point per value of a column. When it is set the
  // axis is that column's own values, and "Group by time" steps aside
  // (services/timeaxis.py precedence); empty, the axis is time.
  per: { label: "One point per",
         hint: "The X axis: one point per value of a column - a date column gives its own dates, a text column its words. Nothing groups by time instead (Group by time)." },
  // A chart INPUT's own grouping and clock (the Inputs rows on the node and
  // the series rows in the Builder).
  pointPer: { label: "One point per",
              hint: "A column of the table - a text column gives one point per value it holds. Nothing keeps this input on the chart's own axis, or gives one value." },
  timestamp: { label: "Date column",
               hint: "Which column says when a row happened. Blank uses the moment the row was saved." },
  bucket: { label: "Group by time", hint: "Count per minute, hour, day and so on. No grouping gives one total." },
  // How several readings that land on one point of a chart become that
  // point (was "Per bucket").
  fold: { label: "Each point shows",
          hint: "Several readings land on one point of the chart; this is how they become one." },
  // The live value a card reads (was "Data key").
  key: { label: "Value", hint: "Every value this system publishes." },
  eventType: { label: "Only events of type", hint: "Empty = every event." },
  period: { label: "Period",
            hint: "Which rows to include. Following the Analytics range means one date pick drives every card on the page." },
  timeRange: { label: "Time range",
               hint: '"Follow the Analytics date picker" charts whatever period that page\'s date picker holds; Today runs from 12 am to now. "Pick dates..." charts an exact start and end.' },
  // The time axis's step (services/timeaxis.py): how far apart the points
  // are. One field on the node and in the Builders, whichever store reads.
  step: { label: "Group by time",
          hint: "How far apart the points are. Automatic fits the time range; a long range may widen it." },
  interval: { label: "Time step", hint: "How far apart the points are. Auto fits the time range." },
  alertWhen: { label: "Alert when", hint: "Turns red when true." },
  labels: { label: "Labels", hint: "What the pill says while fine, and while alerting." },
  rows: { label: "Rows", hint: "How many rows the table shows, newest first." },
  unit: { label: "Unit label" },
};
export const CARD_FIELD_ORDER = Object.keys(CARD_FIELDS);

//: A Chart's shapes. Mirrors workflow.CHART_KINDS; named as the chart
//: builder brief names them (2026-10-13), charts first, then graphs.
export const CHART_SHAPES = [
  ["bar", "Bar chart"],
  ["pie", "Pie chart"],
  ["donut", "Donut chart"],
  ["radar", "Radar chart"],
  ["line", "Line graph"],
  ["spline", "Spline graph - smoothed"],
  ["area", "Area graph - filled"],
];
//: A Card's shapes. Mirrors workflow.CARD_KINDS plus the rows themselves.
export const CARD_SHAPES = [
  ["tile", "stat tile - one value (number or text)"],
  ["gauge", "gauge - a dial"],
  // "status" is the verdict card (a big state with colours and a word): the old OK / ALERT pill is
  // gone from the picker, because a card does not decide anything -- the rule belongs on the node
  // in front of it (If / Else if, Trigger). Cards already saved as a pill keep drawing.
  ["verdict", "status - a state"],
  ["table", "table - the latest rows"],
];
//: Every other shape a hand-made Builder card can take, named the same way.
const OTHER_SHAPES = [
  ["status", "status pill (old)"],
  ["heatmap", "heatmap"], ["timeline", "timeline"], ["video", "live video"], ["snap_gallery", "camera snaps"],
  ["button", "button"], ["ai_classes", "AI classes"],
  ["class_filters", "class filters"],
  // The static cards (builder.STATIC_KINDS): words, or an uploaded picture.
  ["text_card", "Text card"], ["image_card", "Image card"],
];
//: How "Show as" is grouped, on the node and in both Builders alike. The
//: chart builder brief (2026-10-13) replaced the 6c301207 grouping ("Charts
//: over time", "Share and compare", by number of inputs) with two groups by
//: PURPOSE: Charts compare categories and values (a bar may still put time on
//: its X axis), Time-series graphs show change over time. The single-value
//: and list/media shapes are CARD kinds, not charts, and keep their groups.
//: Stored values are unchanged.
//:   heatmap  where objects go, drawn as a live picture over the camera's
//:            view (builder._heat_data reads one live stat per cell) -- a
//:            picture, not a list, so it sits with the video;
//:   timeline the recent events, newest first, each with its time -- a list.
export const SHAPE_GROUPS = [
  ["Charts - compare categories and values", ["bar", "pie", "donut", "radar"]],
  ["Time-series graphs - change over time", ["line", "spline", "area"]],
  ["Single value", ["tile", "gauge", "verdict"]],
  ["Lists, events and media", ["table", "timeline", "heatmap", "video"]],
];
const SHAPE_LABELS = Object.fromEntries([...CHART_SHAPES, ...CARD_SHAPES,
                                         ...OTHER_SHAPES]);
/** What a shape is called, wherever it is offered. */
export const shapeLabel = (kind) => SHAPE_LABELS[kind] || kind;

//: WHERE A CHART'S COLOURS ARE SET. The one place the two panels differ by
//: rule rather than by wiring: a chart's colours -- a line's colour, the
//: palette pin of each series, the colour of each threshold band -- only
//: change how the chart LOOKS, and the look is chosen where the chart is
//: looked at (owner, 2026-10-12: "all colours for the graph need to be set at
//: the Analytics or Dashboard Builder only"). What the chart COMPUTES stays on
//: the node: the series' names, folds, groupings and periods, and the
//: threshold VALUES -- each of which compiles to a rule of its own
//: (workflow.band_alerts), so a limit is data even though its colour is not.
//:
//: In the Builders they sit in the Appearance section (builderShared
//: SeriesColours / BandColours), apart from the inputs and limits in Data.
//:
//: A CARD's colours (a verdict's tones, a status pill's ok/alert colour) are
//: what the card SAYS, not dressing, and stay on both surfaces.
//:
//: Hidden on the node, never dropped: the colours ride the node unchanged, and
//: the canvas save keeps the stored ones whatever it sends
//: (workflow.keep_chart_colours).
export const CHART_COLOUR_HINT =
  "Colours are set in the Analytics or Dashboard Builder.";
/** Whether `kind` is one of a Chart's shapes (workflow.CHART_KINDS). */
export const isChartShape = (kind) => CHART_SHAPES.some(([k]) => k === kind);
/** Whether a panel on `surface` ("node" | "builder") offers the colour
 *  controls of a card drawn as `kind`. */
export const showsColours = (surface, kind) =>
  surface !== "node" || !isChartShape(kind);
/** The grey line a chart's node shows where a colour control used to be. */
export function ChartColourHint() {
  return <p className="muted small" style={{ margin: "4px 0 0" }}>{CHART_COLOUR_HINT}</p>;
}

//: What a card can work out of recorded rows: [stored value, words, needs a
//: number column]. Mirrors builder.AGGREGATIONS. `latest` is not a SQL
//: aggregate -- it is the newest recorded value, what a live tile means by
//: "the value"; `max` is a different question (the biggest ever). `changes`
//: is how many times a column took a new value -- what a live-state table is
//: recorded to answer.
export const WORK_OUT = [
  ["count", "total rows", false],
  ["sum", "total value", true],
  ["avg", "average value", true],
  ["min", "smallest value", true],
  ["max", "largest value", true],
  ["latest", "the latest value", true],
  ["unique", "total different values", false],
  ["changes", "value changes", false],
];

//: A chart INPUT read from a table column (a wire from a Data Table, or a
//: Builder series row "A column of a data table"). Its "Work out" is stored
//: ONCE, on the node's series entry, as the fold the compile derives the
//: store question from (data.series[<sid>].agg, builder.SERIES_AGGS) -- so
//: both rows offer exactly the questions a fold can say, in WORK_OUT's words.
//: "total different values" and "value changes" have no fold on a shared
//: chart axis (chart_series cannot add two cells of them up), so neither row
//: offers them.
export const INPUT_ASKS = ["count", "sum", "avg", "min", "max", "latest"];
const FOLD_OF = { count: "count", sum: "sum", avg: "avg", min: "min", max: "max",
                  latest: "last" };
/** The fold a node's series entry stores for a "Work out" answer. */
export const foldOf = (question) => FOLD_OF[question] || "";
/** The "Work out" answer a stored fold stands for. */
export const questionOf = (fold) => (fold === "last" ? "latest" : fold || "");
/** What the compile works out for a table input that holds no answer of its
 *  own (workflow.series_entry, chart_axis): the chart's own fold when it has
 *  a period, else a number column averaged and anything else counted. Shown
 *  as the selected answer, so the node row reads what the Builder row reads
 *  for the very same compiled input. */
export function inputGuess({ columnType, chartAgg, period }) {
  const number = columnType === "number";
  const chart = foldOf(chartAgg);
  if (period && chart) {
    return questionOf(!number && ["sum", "avg", "min", "max"].includes(chart)
      ? "count" : chart);
  }
  return number ? "avg" : "count";
}

//: Column types that date a row, in both spellings ("datetime" is what a
//: declaration says, "timestamp" what the store answers).
export const TIME_TYPES = ["timestamp", "date", "datetime"];

//: How several samples in one bucket of a chart's time axis become one
//: point. Mirrors builder.SERIES_AGGS -- narrower than WORK_OUT on purpose:
//: "unique" and "changes" are questions the series resolver cannot answer.
export const SERIES_FOLDS = [
  ["avg", "average value"],
  ["sum", "total value"],
  ["min", "smallest value"],
  ["max", "largest value"],
  ["last", "the latest value"],
  ["count", "total rows"],
  ["unique", "Count Difference Value"],
  ["changes", "Count Value Changes"],
];

//: The calendar periods "One bar per" offers before the table's columns.
export const PER_TIMES = [
  ["hour", "each hour"], ["day", "each day"],
  ["week", "each week"], ["month", "each month"],
];
//: "One bar per" left empty on a card that names its own grouping.
export const PER_NONE = "nothing - one total";

//: The event store's time buckets, finest first. Mirrors builder.BUCKETS.
export const EVENT_BUCKETS = [
  ["10s", "per 10 seconds"], ["1m", "per minute"], ["5m", "per 5 minutes"],
  ["15m", "per 15 minutes"], ["hour", "per hour"], ["day", "per day"],
  ["week", "per week"], ["month", "per month"],
];

//: Which recorded rows to include. Mirrors builder.QUERY_RANGES plus "" (all
//: time).
export const PERIODS = [
  ["", "all time"],
  ["page", "follow the Analytics range"],
  ["today", "today"],
  ["week", "this week"],
  ["month", "this month"],
];

//: What each history range key reads as. Mirrors builder.HISTORY_RANGES -
//: "page" and "today" are the day-aligned pair ("page" follows the Analytics
//: date picker; "today" is midnight → now), the rest are rolling windows.
export const RANGE_LABELS = {
  page: "Follow the Analytics range",
  today: "Today (12 am → now)",
  "5m": "Last 5 minutes",
  "15m": "Last 15 minutes",
  "30m": "Last 30 minutes",
  "1h": "Last 1 hour",
  "6h": "Last 6 hours",
  "12h": "Last 12 hours",
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
};

//: A sampled chart's bucket size. Mirrors builder.HISTORY_INTERVALS.
export const INTERVALS = [
  ["", "auto"], ["10s", "10 seconds"], ["1m", "1 minute"], ["5m", "5 minutes"],
  ["15m", "15 minutes"], ["1h", "1 hour"], ["1d", "1 day"],
];

//: The shapes that print a unit after their number. Both panels offer "Unit
//: label" on exactly these, and the compile carries the node's unit onto
//: every one of them whatever feeds it (workflow.py).
export const UNIT_KINDS = ["tile", "gauge", "line", "area", "spline", "bar",
                           "donut", "pie", "radar"];

//: Where a Builder card reads from. Stored values are the server's
//: (builder.SOURCES); the words are for somebody who has never seen the
//: code (owner, 2026-10-12: "users do not understand the difference"):
//:   stat     one value a rule publishes, as it stands now (builder.compute
//:            reads stats[key]);
//:   bucket   a value published as a set of counts, one per group -- per day,
//:            per class (stats[key] is a map; drawn one bar per group);
//:   events   the newest events from the event log, as a list;
//:   query    counts and totals worked out from the recorded events, with
//:            filters (the event store, builder._query_data) -- not SQL;
//:   history  how a live value changed, from the samples the box keeps
//:            (history.values_on_grid), drawn along time.
export const SOURCES = [
  ["stat", "Live value", "A value a rule publishes, as it is right now."],
  ["bucket", "Live counts by group", "A set of counts published together, one per group, such as per day or per class."],
  ["events", "Recent events", "The newest events from the event log, as a list."],
  ["query", "Past events, counted", "Counts or totals worked out from the recorded events, with filters."],
  ["history", "History over time", "How a live value changed, from the readings the box keeps, drawn along time."],
];
//: Where ONE chart input reads from (a Builder series row's "Where from"),
//: in the same words as SOURCES where they mean the same thing.
export const INPUT_SOURCES = [
  ["history", "History over time"],
  ["stat", "Live value"],
  ["query", "Past events, counted"],
  ["dataset", "Data table column"],
];

//: The event types a card can be narrowed to.
export const EVENT_TYPES = [
  ["", "any"], ["violation", "violation"], ["alert", "alert"], ["info", "info"],
];

/** One select, labelled from CARD_FIELDS.
 *
 *  `field` is the panel's own Field component: the node inspector and the
 *  Builder draw a label the same way but with their own CSS classes, and the
 *  label and hint text must not depend on which one is used. A value the list
 *  does not hold (an older stored one) is shown as itself rather than as the
 *  first option, so opening a card never silently changes what it says. */
function Pick({ field: F = UiField, name, hint, value, options, onChange,
                disabled }) {
  const spec = CARD_FIELDS[name];
  const v = value ?? "";
  const list = options.some(([o]) => o === v) ? options : [...options, [v, v]];
  return (
    <F label={spec.label} hint={hint === undefined ? spec.hint : hint}>
      <select className="input" value={v} disabled={disabled}
              onChange={(e) => onChange(e.target.value)}>
        {list.map(([o, label]) => <option key={o} value={o}>{label}</option>)}
      </select>
    </F>
  );
}

/** "Show as". `shapes` defaults by `chart`; `extra` adds an option ahead of
 *  the list (the node's "as saved" for a shape its source no longer offers). */
export function ShapeField({ field: F = UiField, chart = false, shapes, extra = [],
                            value, onChange, disabled, hint }) {
  const v = value ?? "";
  // The OK / ALERT pill is no longer offered (a card does not hold a rule); a card already saved as
  // one still shows it, so its shape is not silently replaced.
  const list = [...extra, ...(shapes || (chart ? CHART_SHAPES : CARD_SHAPES))]
    .filter(([k]) => k !== "status" || v === "status");
  if (!list.some(([k]) => k === v)) list.push([v, shapeLabel(v)]);
  const spec = CARD_FIELDS.shape;
  // In SHAPE_GROUPS order, each group holding only the shapes this panel
  // offers; whatever no group names (an "as saved" shape, the dashboard
  // controls) comes first, ungrouped, so nothing offered is ever hidden.
  const grouped = new Set(SHAPE_GROUPS.flatMap(([, kinds]) => kinds));
  const loose = list.filter(([k]) => !grouped.has(k));
  const opt = ([k, label]) => <option key={k} value={k}>{label}</option>;
  return (
    <F label={spec.label} hint={hint === undefined ? spec.hint : hint}>
      <select className="input" value={v} disabled={disabled}
              onChange={(e) => onChange(e.target.value)}>
        {loose.map(opt)}
        {SHAPE_GROUPS.map(([label, kinds]) => {
          const here = kinds.map((k) => list.find(([x]) => x === k)).filter(Boolean);
          return here.length
            ? <optgroup key={label} label={label}>{here.map(opt)}</optgroup> : null;
        })}
      </select>
    </F>
  );
}

/** "Work out". `numeric` false drops the questions only a number answers;
 *  `only` narrows to what a source can answer (the event store). */
export function WorkOutField({ numeric = true, only, ...rest }) {
  const options = WORK_OUT
    .filter(([v, , needsNumber]) => (!only || only.includes(v))
                                    && (numeric || !needsNumber))
    .map(([v, label]) => [v, label]);
  return <Pick name="agg" options={options} {...rest} />;
}

/** "One bar per": a calendar period or a column, as alternatives. Calls
 *  onChange({bucket, group_by}) with both, so picking one clears the other
 *  and a card never carries two groupings that contradict. */
export function PerField({ groupBy, columns = [], emptyLabel = X_NONE,
                           onChange, ...rest }) {
  // The time choices moved to "Group by time" (StepField): this is the X
  // axis FIELD only. An older chart saved "per day" here keeps it -- it is
  // its stored bucket, and the step control shows it.
  const options = [["", emptyLabel],
    // Never a json column: a nested value has no single thing to group by
    // (datasets_repo.aggregate refuses it too).
    // A word column says its type: one point per value it holds is how a
    // chart gets its categories, and that is worth seeing before picking.
    ...columns.filter((c) => c.type !== "json")
      .map((c) => [c.name, !c.type || c.type === "number"
        ? `each ${c.name}` : `each ${c.name} (${c.type})`])];
  return (
    <Pick name="per" options={options} {...rest} value={groupBy || ""}
          onChange={(v) => onChange({ group_by: v })} />
  );
}

//: "One point per" left empty: the X axis is time, grouped below.
export const X_NONE = "nothing - group by time";
//: Why "Group by time" is greyed out while an X field is chosen.
export const X_FIELD_REASON =
  "The X axis uses the field you chose. Clear it to group by time.";

/** "Time bucket" of an event-store question. `only` narrows to what the
 *  server says it accepts. */
export function BucketField({ only, ...rest }) {
  const options = [["", "no grouping"],
    ...EVENT_BUCKETS.filter(([v]) => !only || only.includes(v))];
  return <Pick name="bucket" options={options} {...rest} />;
}

/** "Period" -- which recorded rows to include. */
export function PeriodField(props) {
  return <Pick name="period" options={PERIODS} {...props} />;
}

/** "Time range" of a sampled chart. `ranges` narrows to the server's list. */
export function TimeRangeField({ ranges, ...rest }) {
  const keys = ranges && ranges.length ? ranges : Object.keys(RANGE_LABELS);
  return <Pick name="timeRange" {...rest}
               options={keys.map((r) => [r, RANGE_LABELS[r] || r])} />;
}

/** "Interval" of a sampled chart. */
export function IntervalField({ intervals, ...rest }) {
  const options = intervals && intervals.length
    ? INTERVALS.filter(([v]) => v === "" || intervals.includes(v)) : INTERVALS;
  return <Pick name="interval" options={options} {...rest} />;
}

/** "Only events of type". */
export function EventTypeField(props) {
  return <Pick name="eventType" options={EVENT_TYPES} {...props} />;
}

/** "Rows" -- how many a table card shows. */
export function RowsField({ field: F = UiField, value, onChange, disabled }) {
  return (
    <F label={CARD_FIELDS.rows.label} hint={CARD_FIELDS.rows.hint}>
      <input className="input" type="number" min="1" max="50"
             value={value ?? 15} disabled={disabled}
             onChange={(e) => onChange(Number(e.target.value))} />
    </F>
  );
}

//: "Show title" (Tok Aba, 2026-10-14): whether a card draws its title bar.
//: Absent, every card draws it as it always has -- except the Text and Image
//: cards, which are their own content (builder.title_shown_by_default).
//: Hidden, the title is still the card's name in the Builders.
const TITLE_OFF_BY_DEFAULT = ["text_card", "image_card"];
/** Whether a card draws its title bar. */
export function titleShown(kind, showTitle) {
  return typeof showTitle === "boolean" ? showTitle : !TITLE_OFF_BY_DEFAULT.includes(kind);
}

/** The "Show title" switch, on the node and in both Builders, beside the
 *  Title field (which stays editable either way). */
export function ShowTitleField({ kind, value, onChange, disabled }) {
  const on = titleShown(kind, value);
  return (
    <label className="wf-check" style={{ margin: "0 0 8px" }}
           title="Hidden, the title is still the card's name in the Builder">
      <input type="checkbox" role="switch" aria-checked={on} checked={on}
             disabled={disabled} aria-label="Show title"
             onChange={(e) => onChange(e.target.checked)} />
      <span className="small">Show title</span>
    </label>
  );
}

//: WHERE A STAT SITS in its card (Tok Aba, 2026-10-14), on the tile, the
//: status pill and the verdict (builder.STAT_LAYOUT_KINDS): the value block
//: aligned left / centre / right (`align`, the Text card's field and words)
//: and placed above / in the middle / below (`valign`). Absent is where each
//: always sat: a tile left and middle, a pill and a verdict centred.
export const STAT_LAYOUT_KINDS = ["tile", "status", "verdict"];
export const ALIGNS = [["left", "Left"], ["center", "Centre"], ["right", "Right"]];
export const POSITIONS = [["top", "Above"], ["middle", "Middle"], ["bottom", "Below"]];
const ALIGN_ICON = { left: AlignLeft, center: AlignCenter, right: AlignRight };
const POSITION_ICON = { top: AlignVerticalJustifyStart, middle: AlignVerticalJustifyCenter,
                        bottom: AlignVerticalJustifyEnd };
/** {align, valign} a card draws with: its own, else where it always sat. */
export function statLayout(kind, align, valign) {
  return {
    align: ALIGNS.some(([v]) => v === align) ? align
      : (kind === "tile" || kind === "text_card" ? "left" : "center"),
    // A Text card always sat at the top of its card; a stat in the middle.
    valign: POSITIONS.some(([v]) => v === valign) ? valign
      : (kind === "text_card" ? "top" : "middle"),
  };
}

/** "Position": Above / Middle / Below -- the stat's and the Text card's. */
export function PositionField({ value, onChange, disabled }) {
  return <SegmentedField label="Position" options={POSITIONS} icons={POSITION_ICON}
                         value={value} onChange={onChange} disabled={disabled} />;
}

/** A few choices side by side, one picked: native radios, so Tab reaches the
 *  group and the arrow keys move the choice. */
export function SegmentedField({ label, options, value, onChange, disabled, icons = null }) {
  const name = useId();
  return (
    <div className="seg-field" role="radiogroup" aria-label={label}>
      <span className="seg-field-label">{label}</span>
      <div className="seg">
        {options.map(([v, words]) => {
          const Icon = icons && icons[v];
          return (
            <label key={v} className={`seg-opt${value === v ? " is-on" : ""}`}>
              <input type="radio" name={name} value={v} checked={value === v} disabled={disabled}
                     aria-label={words} onChange={() => onChange(v)} />
              {Icon && <Icon size={13} aria-hidden="true" />}
              <span>{words}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

/** "Alignment": left / centre / right -- the Text card's and the stat's. */
export function AlignField({ value, onChange, disabled }) {
  return <SegmentedField label="Alignment" options={ALIGNS} icons={ALIGN_ICON}
                         value={value} onChange={onChange} disabled={disabled} />;
}

/** The stat's "Placement" row: Alignment and Position, on the node and in both
 *  Builders. Writes {align} or {valign} -- only what was picked. */
export function StatLayoutFields({ kind, align, valign, onChange, disabled }) {
  const now = statLayout(kind, align, valign);
  return (
    // "Placement", not "Layout": the Builders already have a Layout section
    // (width and grid), and two of one name would be one question too many.
    <div className="stat-layout-fields" role="group" aria-label="Placement">
      <span className="muted small">Placement</span>
      <AlignField value={now.align} disabled={disabled} onChange={(v) => onChange({ align: v })} />
      <PositionField value={now.valign} disabled={disabled} onChange={(v) => onChange({ valign: v })} />
    </div>
  );
}

/** "Unit label" -- the word after the number. */
export function UnitField({ field: F = UiField, value, onChange, disabled }) {
  return (
    <F label={CARD_FIELDS.unit.label}>
      <input className="input" value={value || ""} disabled={disabled}
             maxLength={12} placeholder="e.g. cars"
             onChange={(e) => onChange(e.target.value)} />
    </F>
  );
}

/** "Work out" on a table INPUT: INPUT_ASKS in WORK_OUT's words, narrowed to
 *  counting for a column that is not a number (the compile counts it
 *  whatever is asked). The value is the question; callers store foldOf(). */
export function InputWorkOutField({ numeric = true, ...rest }) {
  return <WorkOutField only={INPUT_ASKS} numeric={numeric} {...rest} />;
}

/** "Of which column" on a table input. A wired input's column is the wire's
 *  and is shown disabled -- the same on both panels; a hand-made row picks
 *  from `columns`. */
export function ColumnField({ field: F = UiField, value, columns, onChange,
                              disabled }) {
  const list = columns && columns.length ? columns : (value ? [{ name: value }] : []);
  return (
    <F label={CARD_FIELDS.column.label}>
      <select className="input" value={value || ""} disabled={disabled}
              onChange={(e) => onChange && onChange(e.target.value)}>
        <option value="">- choose a column -</option>
        {list.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
      </select>
    </F>
  );
}

/** "One point per" on a table input: nothing (the chart's own axis, or one
 *  value), or a column -- a word column named as the categories it gives. */
export function PointPerField({ value, columns = [], timed, ...rest }) {
  const options = [["", timed ? "nothing - follow the chart's time axis"
                               : "nothing - one value"],
    ...columns.filter((c) => c.type !== "json").map((c) => [c.name,
      ["number", ...TIME_TYPES].includes(c.type) ? c.name : `each ${c.name} value`])];
  return <Pick name="pointPer" options={options} value={value || ""} {...rest} />;
}

/** "Timestamp column" on a table input of a time chart. */
export function TimestampField({ value, columns = [], ...rest }) {
  const options = [["", "when the row was saved"],
    ...columns.filter((c) => TIME_TYPES.includes(c.type)).map((c) => [c.name, c.name])];
  return <Pick name="timestamp" options={options} value={value || ""} {...rest} />;
}

//: A status pill's comparison operators. Mirrors builder.STATUS_OPS.
export const STATUS_OPS = [">", ">=", "<", "<=", "==", "!="];

/** A status pill's rule and its two words: "Alert when <op> <value>", then
 *  what it says while fine and while alerting. Stored on the node as op,
 *  value, ok_label, alert_label (the compile builds the pill's rule from
 *  them), so the node and the Builders edit one rule. `onChange` gets the
 *  fields that changed, in those names. */
export function StatusFields({ field: F = UiField, op, value, okLabel, alertLabel,
                               onChange, disabled }) {
  return (
    <>
      <F label={CARD_FIELDS.alertWhen.label} hint={CARD_FIELDS.alertWhen.hint}>
        <div className="rail-row">
          <select className="input" style={{ flex: 1 }} value={op || ">="}
                  disabled={disabled} aria-label="Comparison"
                  onChange={(e) => onChange({ op: e.target.value })}>
            {STATUS_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <input className="input" style={{ flex: 1 }} type="number"
                 value={value ?? 1} disabled={disabled} aria-label="Alert value"
                 onChange={(e) => onChange({ value: Number(e.target.value) })} />
        </div>
      </F>
      <F label={CARD_FIELDS.labels.label} hint={CARD_FIELDS.labels.hint}>
        <div className="rail-row">
          <input className="input" placeholder="OK" value={okLabel || ""}
                 disabled={disabled} aria-label="Word while fine"
                 onChange={(e) => onChange({ ok_label: e.target.value })} />
          <input className="input" placeholder="ALERT" value={alertLabel || ""}
                 disabled={disabled} aria-label="Word while alerting"
                 onChange={(e) => onChange({ alert_label: e.target.value })} />
        </div>
      </F>
    </>
  );
}

// -- THE TIME AXIS (services/timeaxis.py) ---------------------------------------
//
// One "Group by time" (how far apart the points are) and one "Time range"
// (which stretch of time), the same on the workflow node and in both
// Builders, with the stored values the server's timeaxis module reads:
//   step   "" (Automatic) | "<n>m" "<n>h" "<n>d" | "week" | "month"
//          (old "hour"/"day"/"10s"/"15m" still read)
//   range  a quick pick, "page", or "custom" with range_from/range_to.

//: The step presets, in the order they read. Mirrors timeaxis.STEP_PRESETS.
export const STEP_PRESETS = [
  ["", "Automatic"], ["1m", "Every minute"], ["5m", "Every 5 minutes"],
  ["30m", "Every 30 minutes"], ["1h", "Every hour"], ["6h", "Every 6 hours"],
  ["12h", "Every 12 hours"], ["1d", "Every day"], ["week", "Every week"],
  ["month", "Every month"],
];
const STEP_ALIASES = { hour: "1h", day: "1d", "1w": "week" };
const STEP_UNITS = [["m", "minutes"], ["h", "hours"], ["d", "days"]];
const UNIT_S = { s: 1, m: 60, h: 3600, d: 86400 };
//: A custom step's bounds: a minute to 90 days (timeaxis.MIN/MAX_STEP_S).
export const STEP_MIN_S = 60;
export const STEP_MAX_S = 90 * 86400;

/** A step as it would be stored, or null when it cannot be. Mirrors
 *  timeaxis.clean_step for the m/h/d grammar. */
export function cleanStep(value) {
  const lower = String(value ?? "").trim().toLowerCase();
  const s = STEP_ALIASES[lower] ?? lower;
  if (["", "week", "month"].includes(s)) return s;
  const m = s.match(/^(\d{1,5})([smhd])$/);
  if (!m) return null;
  const secs = Number(m[1]) * UNIT_S[m[2]];
  if (m[2] === "s") return secs === 10 || (secs >= 60 && secs < 3600 && secs % 60 === 0) ? s : null;
  if (Number(m[1]) < 1 || secs < STEP_MIN_S || secs > STEP_MAX_S) return null;
  return s;
}

/** What a custom number + unit says, or the reason it cannot be stored. */
export function customStep(amount, unit) {
  const raw = String(amount ?? "").trim();
  if (!/^\d+$/.test(raw)) return { error: "A whole number, 1 or more." };
  const n = Number(raw);
  if (n < 1) return { error: "A whole number, 1 or more." };
  const secs = n * UNIT_S[unit];
  if (secs < STEP_MIN_S) return { error: "At least 1 minute." };
  if (secs > STEP_MAX_S) return { error: "At most 90 days - use Every month beyond that." };
  return { value: `${n}${unit}` };
}

/** "Every 90 minutes" for a stored step. */
export function stepWords(step) {
  const s = cleanStep(step);
  const preset = STEP_PRESETS.find(([v]) => v === s);
  if (preset) return preset[1];
  const m = String(s || "").match(/^(\d+)([smhd])$/);
  if (!m) return String(step || "");
  const unit = { s: "second", m: "minute", h: "hour", d: "day" }[m[2]];
  return `Every ${m[1]} ${unit}${m[1] === "1" ? "" : "s"}`;
}

/** "Group by time": the presets, and "Custom..." which reveals a number and
 *  a unit (minutes / hours / days), checked as typed and stored only when
 *  valid. A stored step that is not a preset shows as the custom it is. */
export function StepField({ field: F = UiField, value, onChange, disabled,
                            hint, emptyLabel, lockedReason, label }) {
  // An X FIELD assigned ("One point per <column>") wins over the time
  // grouping: the control stays visible, greyed, with the one-line reason.
  if (lockedReason) disabled = true;
  const stored = cleanStep(value);
  const presetHit = STEP_PRESETS.some(([v]) => v === stored);
  const m = String(stored || "").match(/^(\d+)([mhd])$/);
  const [custom, setCustom] = useState(!presetHit && !!m);
  const [amount, setAmount] = useState(m ? m[1] : "90");
  const [unit, setUnit] = useState(m ? m[2] : "m");
  const showCustom = custom || (!presetHit && !!m);
  const check = customStep(amount, unit);
  const presets = STEP_PRESETS.map(([v, words]) => [v, v === "" && emptyLabel ? emptyLabel : words]);
  // A stored legacy value that is neither a preset nor m/h/d (a "10s")
  // shows as itself rather than as the first option.
  const loose = stored !== null && !presetHit && !m ? [[String(value), stepWords(value)]] : [];
  const commit = (n, u) => {
    const out = customStep(n, u);
    if (out.value) onChange(out.value);
  };
  return (
    <F label={label || CARD_FIELDS.step.label} hint={hint === undefined ? CARD_FIELDS.step.hint : hint}>
      <select className="input" disabled={disabled} aria-label={label || CARD_FIELDS.step.label}
              value={showCustom ? "custom" : (presetHit ? stored : String(value ?? ""))}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "custom") { setCustom(true); commit(amount, unit); return; }
                setCustom(false);
                onChange(v);
              }}>
        {[...presets, ...loose].map(([v, words]) => <option key={v} value={v}>{words}</option>)}
        <option value="custom">Custom...</option>
      </select>
      {showCustom && (
        <span className="rail-row" style={{ marginTop: 6 }}>
          <input className="input" type="number" min="1" step="1" value={amount}
                 disabled={disabled} aria-label="Every how many"
                 aria-invalid={check.error ? "true" : undefined}
                 style={{ flex: "0 0 90px" }}
                 onChange={(e) => { setAmount(e.target.value); commit(e.target.value, unit); }} />
          <select className="input" value={unit} disabled={disabled}
                  aria-label="Unit"
                  onChange={(e) => { setUnit(e.target.value); commit(amount, e.target.value); }}>
            {STEP_UNITS.map(([u, words]) => <option key={u} value={u}>{words}</option>)}
          </select>
        </span>
      )}
      {showCustom && check.error && (
        <span className="small c-warn" role="alert">{check.error}</span>
      )}
      {lockedReason && <span className="muted small">{lockedReason}</span>}
    </F>
  );
}

//: The range quick picks, in the order they read. Mirrors
//: timeaxis.RANGE_PRESETS. "Pick dates..." reveals the exact start and end.
export const RANGE_PRESETS = [
  ["3h", "Last 3 hours"], ["6h", "Last 6 hours"], ["12h", "Last 12 hours"],
  ["24h", "Last 24 hours"], ["today", "Today"], ["7d", "Past week"],
  ["30d", "Past month"], ["90d", "Past 3 months"], ["180d", "Past 6 months"],
  ["365d", "Past year"], ["page", "Follow the Analytics date picker"],
  ["custom", "Pick dates..."],
];
//: Older stored ranges, still read, in plain words.
const RANGE_LEGACY = { "5m": "Last 5 minutes", "15m": "Last 15 minutes",
                       "30m": "Last 30 minutes", "1h": "Last 1 hour",
                       week: "Last 7 days", month: "Last 30 days" };

/** "Time range": one quick-pick select; "Pick dates..." reveals a start and
 *  an end (a time optional), stored as range_from / range_to. onChange gets
 *  {range, range_from, range_to}. */
export function RangeField({ field: F = UiField, value, from, to, onChange,
                             disabled, hint, label, allowAll = false }) {
  // `allowAll`: "All time" is a choice (a filter on rows, the chart builder's
  // "Only include rows from") rather than a missing range read as 24 hours.
  const v = allowAll ? (value || "") : (value || "24h");
  const presets = allowAll ? [["", "All time"], ...RANGE_PRESETS] : RANGE_PRESETS;
  const known = presets.some(([k]) => k === v);
  const options = known ? presets
    : [...presets, [v, RANGE_LEGACY[v] || RANGE_LABELS[v] || v]];
  const set = (patch) => onChange({ range: v, range_from: from || "",
                                    range_to: to || "", ...patch });
  return (
    <F label={label || CARD_FIELDS.timeRange.label}
       hint={hint === undefined ? CARD_FIELDS.timeRange.hint : hint}>
      <select className="input" value={v} disabled={disabled}
              aria-label={label || CARD_FIELDS.timeRange.label}
              onChange={(e) => set({ range: e.target.value })}>
        {options.map(([k, words]) => <option key={k} value={k}>{words}</option>)}
      </select>
      {v === "custom" && (
        <span style={{ display: "grid", gap: 4, marginTop: 6 }}>
          <input className="input" type="datetime-local" value={(from || "").slice(0, 16)}
                 disabled={disabled} aria-label="From"
                 onChange={(e) => set({ range_from: e.target.value })} />
          <input className="input" type="datetime-local" value={(to || "").slice(0, 16)}
                 disabled={disabled} aria-label="To"
                 onChange={(e) => set({ range_to: e.target.value })} />
          {!from && <span className="small c-warn">Pick a start.</span>}
        </span>
      )}
    </F>
  );
}

// -- A CARD'S ONE DATE RANGE ------------------------------------------------------
//
// Owner, 2026-10-13, on a stat tile's panel ("Total Violation CAM 1"): "for
// this also many duplicate or redundant, please make it into one." The card
// showed THREE controls for "which stretch of time does this value cover":
//
//   Period      (ColumnLogic / TableColumns / RecordedQuery)  -> `range`
//   Count over  ("window": live / follow / today / week / month) -> `window`
//   Period      (again, at the foot of the panel)              -> `range`
//
// The two Periods were ONE field written from two places. Count over is a
// different field that only a card fed by a LIVE RULE reads (it picks the
// rule's today / week / month / total counter, workflow._windowed_metric);
// `range` is what a card asking RECORDED rows reads (a table column, a table,
// recorded events or a table named on the card). Neither source reads the
// other's field, so a card had one effective setting and up to two dead ones.
//
// Now: one "Date range", writing the field this card's source reads. The
// options are the ones that source can answer -- "Live value (now)" only for
// a live rule; the quick picks and "Pick dates..." only for recorded rows. A
// value left in the OTHER field by the old panel is not discarded: it is read
// back as a note "as saved" under Advanced, and nothing new can set it.
// A card fed by a Query node has no control: the Query's own period applies.

//: The date range of a card asking RECORDED rows. Mirrors QUERY_RANGES.
export const CARD_RANGES = [
  ["", "All time"], ["page", "Follow the Analytics date picker"],
  ["today", "Today"], ["week", "This week"], ["month", "This month"],
  ["3h", "Last 3 hours"], ["6h", "Last 6 hours"], ["12h", "Last 12 hours"],
  ["24h", "Last 24 hours"], ["7d", "Past week"], ["30d", "Past month"],
  ["90d", "Past 3 months"], ["180d", "Past 6 months"], ["365d", "Past year"],
  ["custom", "Pick dates..."],
];
//: ...of a card showing a LIVE RULE's value: the counters a rule keeps.
//: Stored in `window` (workflow.CARD_WINDOWS); "page" is its "follow".
export const LIVE_RANGES = [
  ["live", "Live value"], ["page", "Date Selection"],
  ["today", "Today"], ["week", "Weekly"], ["month", "Monthly"],
];
const WINDOW_OF_RANGE = { live: "live", page: "follow", today: "today", week: "week",
                          month: "month" };
const RANGE_OF_WINDOW = { live: "live", follow: "page", today: "today", week: "week",
                          month: "month" };
const wordsOf = (list, v) => (list.find(([k]) => k === v) || [])[1] || v;

/** A card's ONE date range, read from what it stores: {value, from, to,
 *  note}. `source` is "live" (a rule's value), "recorded" (rows) or "none"
 *  (a Query decides). The value is the field the compile USED for this
 *  source; the other field, when the old panel left something there that
 *  disagrees, is the `note` -- shown as saved, never discarded. */
export function cardRangeOf(d, source) {
  d = d || {};
  const range = d.range || "";
  const window = d.window || "live";
  if (source === "live") {
    const value = RANGE_OF_WINDOW[window] || "live";
    const note = range && range !== value
      ? `Period: ${wordsOf(CARD_RANGES, range)} (as saved; a card showing a live rule's `
        + "value does not read it)" : "";
    return { value, from: "", to: "", note };
  }
  if (source === "recorded") {
    const note = window !== "live" && RANGE_OF_WINDOW[window] !== range
      ? `Count over: ${wordsOf(LIVE_RANGES, RANGE_OF_WINDOW[window])} (as saved; it `
        + "applies only to a card showing a live rule's value)" : "";
    return { value: range, from: d.range_from || "", to: d.range_to || "",
             note };
  }
  return { value: "", from: "", to: "", note: "" };
}

/** What a change of the ONE control writes: the field this source reads,
 *  and only that one. */
export function cardRangePatch(source, value, from = "", to = "") {
  if (source === "live") return { window: WINDOW_OF_RANGE[value] || "live" };
  return { range: value || "", range_from: value === "custom" ? (from || "") : "",
           range_to: value === "custom" ? (to || "") : "" };
}

/** "Date range" on a card -- the same control on the workflow node and in
 *  both Builders. onChange gets the stored fields to write
 *  (cardRangePatch). */
export function CardDateRange({ field: F = UiField, source, value, from, to, note,
                                onChange, disabled }) {
  if (source === "none") {
    return (
      <p className="muted small" style={{ margin: "4px 0" }}>
        Date range: the Query&apos;s own period applies.
      </p>
    );
  }
  const list = source === "live" ? LIVE_RANGES : CARD_RANGES;
  const v = value ?? (source === "live" ? "live" : "");
  const options = list.some(([k]) => k === v) ? list : [...list, [v, `${v} - as saved`]];
  const write = (patch) => onChange(cardRangePatch(source, patch.value ?? v,
                                                   patch.from ?? from, patch.to ?? to));
  return (
    <>
      <F label="Date range"
         hint={source === "live"
           ? "Live value is the number right now; the others read the rule's counter for that period. Date Selection lets the Analytics page's date picker decide."
           : "Which recorded rows this card covers. Follow the Analytics date picker lets that page decide."}>
        <select className="input" value={v} disabled={disabled} aria-label="Date range"
                onChange={(e) => write({ value: e.target.value })}>
          {options.map(([k, w]) => <option key={k} value={k}>{w}</option>)}
        </select>
        {source === "recorded" && v === "custom" && (
          <span style={{ display: "grid", gap: 4, marginTop: 6 }}>
            <input className="input" type="datetime-local" value={(from || "").slice(0, 16)}
                   disabled={disabled} aria-label="From"
                   onChange={(e) => write({ from: e.target.value })} />
            <input className="input" type="datetime-local" value={(to || "").slice(0, 16)}
                   disabled={disabled} aria-label="To"
                   onChange={(e) => write({ to: e.target.value })} />
          </span>
        )}
      </F>
      {note && (
        <details className="chart-advanced">
          <summary className="muted small">Advanced (1 as saved)</summary>
          <p className="muted small" style={{ margin: "4px 0" }}>{note}</p>
        </details>
      )}
    </>
  );
}
