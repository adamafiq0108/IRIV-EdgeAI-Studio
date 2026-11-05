// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Builder machinery shared by the two composers: the Dashboard Builder
// (pages/Builder.jsx) and the Analytics Builder (pages/AnalyticsBuilder.jsx).
//
// Both drag the same widgets onto the same GridCanvas, bind them to the same
// keys and preview them through the same endpoint - the only real differences
// are WHICH page they write to and, on Analytics, a global date range. So the
// palette, the widget factories, the debounced live preview and the whole
// properties inspector live here rather than being forked: a field added to one
// composer is a field the other has, and a widget cannot be born with different
// defaults depending on which page created it.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { api } from "../../api/client.js";
import { SERIES_COLORS } from "../charts.js";
import { Field, Section } from "../ui/primitives.jsx";
import { useToast } from "../ui/Toast.jsx";
import { useViewSettings } from "../ui/ViewSettings.jsx";
// The card's properties -- labels, order, option lists -- are defined once and
// shared with the workflow node's inspector (see cardFields.jsx for why).
import {
  BucketField, CARD_FIELDS, EventTypeField, IntervalField, PerField, PeriodField,
  RANGE_LABELS, RowsField, SERIES_FOLDS, ShapeField, TimeRangeField, UnitField,
  StepField, RangeField, X_FIELD_REASON,
  WorkOutField, CARD_SHAPES, CHART_SHAPES, UNIT_KINDS, shapeLabel,
  ChartColourHint, showsColours,
  ColumnField, InputWorkOutField, PointPerField, TimestampField, foldOf,
  StatusFields, SOURCES, INPUT_SOURCES, CardDateRange, cardRangeOf, ShowTitleField, titleShown,
  STAT_LAYOUT_KINDS, StatLayoutFields, statLayout,
} from "./cardFields.jsx";
// The chart builder: one X axis, Y axis, interval and date range per chart,
// the SAME component the workflow node draws (chartFields.jsx), on the model
// chartModel.js reads and lowers.
import { ChartAxisFields, InputRowControls } from "./chartFields.jsx";
import { ImageCardFields, TextCardFields } from "./staticCardFields.jsx";
import { ARRANGE, arrange, canArrange } from "./layerOrder.js";
import { notePaletteDrag } from "./WidgetGrid.jsx";
import { ArrowDown, ArrowDownToLine, ArrowUp, ArrowUpToLine } from "lucide-react";
// Choose the chart type first (2026-10-14): the picker, its strip and the
// in-family "Show as", shared with the workflow node inspector.
import { ChartTypePicker, ChartTypeStrip, ChartShowAs } from "./ChartTypePicker.jsx";
import {
  chartTypeOf, planTypeSwitch, isFreshChart, clearFreshChart, markFreshChart,
} from "./chartTypes.js";
import {
  readWidgetModel, widgetPatch, modelNodeFields, foldOfCalc, autoAxisTitles,
  layoutOf,
} from "./chartModel.js";
import { stepWords } from "./cardFields.jsx";

export { RANGE_LABELS };

/**
 * The template choices worth offering PER KIND. Mirrors TEMPLATES in
 * core/services/builder.py, which is the list the server will actually
 * accept -- anything else is stored as "plain".
 *
 * Split per kind because the treatments are not interchangeable: filling a
 * card with colour means something for a KPI tile and nothing for a line
 * chart, and offering "dashed" on a tile is offering a choice that silently
 * does nothing. Widget.jsx falls back to the plain treatment for any
 * template a kind does not implement, so a widget whose kind is changed
 * afterwards degrades rather than breaking.
 */
export const TEMPLATES_FOR = {
  _default: [["plain", "plain"]],
  tile: [["plain", "plain"], ["accent", "accent - filled with the colour"],
         ["outline", "outline - coloured border"],
         ["soft", "soft - faint tint"]],
  // The first four are single tweaks; the last four are whole looks. Ordered
  // that way on purpose -- an operator picking "how should this graph look"
  // wants the finished treatments, and gets them without reading the list of
  // adjustments first.
  line:   [["plain", "plain"], ["markers", "markers - show every point"],
           ["dashed", "dashed"], ["gradient", "gradient fill"],
           ["stacked", "stacked - shares of a total"],
           ["glow", "glow - lit line and points"],
           ["stripe", "stripe - hatched fill"],
           ["forecast", "forecast - line over a hatched band"]],
  area:   [["plain", "plain"], ["markers", "markers - show every point"],
           ["dashed", "dashed"], ["gradient", "gradient fill"],
           ["stacked", "stacked - shares of a total"],
           ["glow", "glow - lit line and points"],
           ["stripe", "stripe - hatched fill"],
           ["forecast", "forecast - line over a hatched band"]],
  spline: [["plain", "plain"], ["markers", "markers - show every point"],
           ["dashed", "dashed"], ["gradient", "gradient fill"],
           ["stacked", "stacked - shares of a total"],
           ["glow", "glow - lit line and points"],
           ["stripe", "stripe - hatched fill"],
           ["forecast", "forecast - line over a hatched band"]],
  bar:    [["plain", "plain"], ["gradient", "gradient fill"]],
  donut:  [["plain", "plain"], ["legend", "legend - total and a row per slice"]],
  pie:    [["plain", "plain"], ["legend", "legend - total and a row per slice"]],
};

/** How a card ARRIVES and how it MOVES when its numbers change.
 *
 *  Three families, because the difference decides which one to pick:
 *
 *    appear  the whole card moves into place. Works on every widget, chart or
 *            not, because it is the CARD that moves.
 *    draw    the shapes are drawn on -- bars grow out of the axis, a line
 *            draws itself, slices sweep round. Charts only: there is nothing
 *            to draw on a single number.
 *    update  what happens when new numbers arrive. These are the ones an
 *            operator watching a live wall actually sees, over and over.
 *
 *  Each entry says which family it belongs to so the picker can group them
 *  and can grey out the chart-only ones on a tile -- an option that silently
 *  does nothing is worse than an option that is not offered.
 */
export const ANIMATIONS = [
  ["", "none - still", "appear"],
  ["fade_in", "fade in", "appear"],
  ["fade_out", "fade out and back", "update"],
  ["scale_in", "scale in (zoom in)", "appear"],
  ["scale_out", "scale out (settles back)", "appear"],
  ["slide_up", "slide up", "appear"],
  ["slide_down", "slide down", "appear"],
  ["slide_left", "slide left", "appear"],
  ["slide_right", "slide right", "appear"],
  ["rotate", "rotate in", "appear"],
  ["grow", "grow - bars, lines, areas and slices draw themselves", "draw"],
  ["stagger", "stagger - one series after another", "draw"],
  ["morph", "morph - shapes travel to their new values", "update"],
  ["opacity", "opacity - dims and returns on new data", "update"],
  ["color", "colour transition - new colours cross-fade", "update"],
  ["enter_update_exit", "enter, update, exit - animate every data change",
   "update"],
];

//: The families, in the order they read: how it arrives, how it is drawn,
//: what it does when the numbers move.
export const ANIM_FAMILIES = [
  ["appear", "When it appears"],
  ["draw", "How it is drawn (charts)"],
  ["update", "When the numbers change"],
];

//: `draw` animations have nothing to act on where there are no shapes: a
//: number, a pill, a video feed. Offered greyed rather than hidden, so the
//: list is the same list everywhere and the reason is visible.
export const ANIM_DRAWS_SHAPES = ["line", "area", "spline", "bar", "donut",
                                  "pie", "radar", "gauge", "heatmap"];

/** The animation picker, shared by the Builder inspector and the workflow
 *  canvas so a card dressed in either place offers the same list. */
export function AnimationPicker({ kind, value, replay, onChange, disabled }) {
  const shapes = ANIM_DRAWS_SHAPES.includes(kind);
  const chosen = String(value || "");
  const family = (ANIMATIONS.find(([v]) => v === chosen) || [])[2];
  return (
    <>
      <Field label="Animation"
             hint="How the card arrives, and how it moves when its numbers change. Separate from the template, which is how it is dressed.">
        <select className="input" value={chosen} disabled={disabled}
                onChange={(e) => onChange({ anim: e.target.value })}>
          {ANIM_FAMILIES.map(([fam, title]) => (
            <optgroup key={fam} label={title}>
              {ANIMATIONS.filter(([, , f]) => f === fam).map(([v, label]) => (
                <option key={v || "none"} value={v}
                        disabled={fam === "draw" && !shapes}>
                  {label}
                  {fam === "draw" && !shapes ? " - needs a chart" : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </Field>
      {/* An ARRIVAL animation replaying on every poll is the fastest way to
          make a wall display unreadable, so it is a deliberate second choice
          and it is off. The update animations need no such switch: moving
          when the numbers move IS what they are. */}
      {family === "appear" && chosen && (
        // The rail's own checkbox row, not a hand-spaced one: inside a rail a
        // label is a block, so a flex row spelled inline collapses.
        <label className="wf-check">
          <input type="checkbox" checked={!!replay} disabled={disabled}
                 onChange={(e) => onChange({ anim_replay: e.target.checked })} />
          <span>Play it again whenever the data changes</span>
        </label>
      )}
    </>
  );
}

//: Charts that draw a LINE over time, and so have a level worth banding.
//: Mirrors builder.TIMESERIES_KINDS -- a donut has no axis to put a limit on.
//: The bar joined with per-camera thresholds (owner, 2026-10-13): each bar is
//: painted by the band its value is in. Mirrors builder.BAND_KINDS.
export const BAND_KINDS = ["line", "area", "spline", "bar"];

//: ...and the ones with a dated axis long enough to span days. The bar chart
//: is in because a bar per day is the clearest per-day chart there is.
//: Mirrors builder.DAY_COLOUR_KINDS.
export const DAY_KINDS = ["line", "area", "spline", "bar"];

/** "Colour each day differently".
 *
 *  A week of hourly buckets is one line in one colour: the picture shows the
 *  humps but not WHICH DAY each hump was, so the operator ends up reading tick
 *  labels off the bottom axis -- the reading a chart is supposed to save them.
 *  Opt-in rather than automatic: a single-colour line is still the right chart
 *  for one day, and a card whose look changes by itself when a range widens is
 *  a card that broke.
 */
export function DayColourToggle({ kind, value, series = 1, onChange }) {
  if (!DAY_KINDS.includes(kind)) return null;
  // One line, one meaning. Widget.jsx paints by day only where the chart
  // draws a SINGLE series, because "the colour is which day" and "the colour
  // is which series" are two readings of one channel and a chart cannot
  // carry both. Said here rather than left as a tick that silently does
  // nothing the moment a second input is added.
  const many = series > 1;
  return (
    <label className="wf-check">
      <input type="checkbox" checked={!!value && !many} disabled={many}
             onChange={(e) => onChange({ by_day: e.target.checked })} />
      <span>Colour each day differently</span>
      <span className="rail-brief">
        {many
          ? `Off while this chart has ${series} series - the line colours `
            + "already say which series is which. Remove the extra series to "
            + "colour by day."
          : "Only on a range covering more than one day. The newest day keeps "
            + "the accent colour above; click a day to look at it on its own."}
      </span>
    </label>
  );
}

//: Mirrors builder.MAX_BANDS / BAND_RAMP. Four thresholds, five bands: past
//: that the colours stop meaning "this is the level" and start meaning "this
//: is a rainbow".
export const MAX_BANDS = 4;
const BAND_RAMP = ["#16a34a", "#f59e0b", "#dc2626", "#7c2d12", "#450a0a"];

//: What each band IS, in words, given where it sits in the list. Written out
//: rather than numbered because "over 80" is the question the operator came
//: with, and "band 3" is not. "over" is the word the threshold rows use, so a
//: colour in Appearance reads as the limit it belongs to in Data.
function bandLabel(i, at) {
  if (!at.length) return "every value";
  if (i === 0) return `below ${at[0]}`;
  if (i === at.length) return `over ${at[at.length - 1]}`;
  return `${at[i - 1]} to ${at[i]}`;
}

//: The colours of `value`'s bands, always len(at) + 1 long: a missing one is
//: the ramp's, the same fill builder.clean_bands makes on the server.
function bandColours(value) {
  const at = Array.isArray(value?.at) ? value.at : [];
  const given = Array.isArray(value?.colors) ? value.colors : [];
  return at.concat([0]).map((_, i) => given[i]
    || BAND_RAMP[Math.min(i, BAND_RAMP.length - 1)]);
}

/** Threshold bands for a line chart: the LIMITS.
 *
 *  N limits cut the value axis into N+1 bands, each with its own colour, so
 *  the line's colour says which level it is at. The limits are data -- each
 *  one compiles to a rule (workflow.band_alerts) -- and are edited here, in
 *  the Builders' Data section and on the workflow node alike. The COLOURS are
 *  look, and a chart's look is set in the Builders' Appearance section only
 *  (BandColours, below; owner, 2026-10-12): two editors, one stored `bands`.
 *
 *  The colours still ride every edit made here. Every edit re-sorts, and a
 *  colour belongs to a BAND, so when the limits reorder the colours follow
 *  their band; adding a limit splits a band and the new one inherits its
 *  colour (nothing on screen changes until a colour is chosen); removing one
 *  merges two back together. So no limit edit repaints what Appearance set.
 *
 *  `surface` is which panel draws it: on the node the one hint line says
 *  where the colours are set; in the Builder, that they are under Appearance.
 */
export function BandEditor({ kind, value, onChange, surface = "builder",
                            label = "Thresholds", ariaPrefix = "" }) {
  if (!BAND_KINDS.includes(kind)) return null;
  const at = Array.isArray(value?.at) ? value.at : [];
  const colors = Array.isArray(value?.colors) ? value.colors : [];
  const colorAt = (i) => colors[i] || BAND_RAMP[Math.min(i, BAND_RAMP.length - 1)];

  function commit(nextAt, nextColors) {
    const order = nextAt.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
    const sorted = order.map(([v]) => v);
    const moved = [nextColors[0]].concat(order.map(([, i]) => nextColors[i + 1]));
    onChange({ at: sorted,
               colors: sorted.map((_, i) => moved[i] || colorAt(i))
                             .concat([moved[sorted.length] || colorAt(sorted.length)]) });
  }

  function addOne() {
    const last = at.length ? at[at.length - 1] : 0;
    commit(at.concat([last + 10]),
           colors.slice(0, at.length + 1).concat([colorAt(at.length)]));
  }

  function removeAt(i) {
    commit(at.filter((_, j) => j !== i),
           colors.filter((_, j) => j !== i + 1));
  }

  return (
    <Field label={label}
           hint="Limits on the value axis. Each one splits the line into another band.">
      <div style={{ display: "grid", gap: 6 }}>
        {/* Once there is something to colour (a bar chart's node draws this
            editor empty, beside its inputs' own hint line). */}
        {!showsColours(surface, kind) && at.length > 0 && <ChartColourHint />}
        {at.map((t, i) => (
          <div key={i} className="row" style={{ gap: 8, alignItems: "center" }}>
            <span className="muted small" style={{ width: 30 }}>over</span>
            <input className="input" type="number" value={t} step="any"
                   aria-label={`${ariaPrefix}Threshold ${i + 1}`}
                   style={{ minWidth: 0, flex: "1 1 70px" }}
                   onChange={(e) => {
                     const num = Number(e.target.value);
                     if (!Number.isFinite(num)) return;
                     commit(at.map((v, j) => (j === i ? num : v)),
                            at.concat([0]).map((_, j) => colorAt(j)));
                   }} />
            <button type="button" className="btn btn-sm btn-ghost"
                    title="Remove this threshold"
                    onClick={() => removeAt(i)}>×</button>
          </div>
        ))}
        <div className="row" style={{ gap: 8 }}>
          <button type="button" className="btn btn-sm" onClick={addOne}
                  disabled={at.length >= MAX_BANDS}>
            Add threshold
          </button>
          {at.length >= MAX_BANDS && (
            <span className="muted small">
              {MAX_BANDS} is the most - past that the colours stop reading as levels.
            </span>
          )}
        </div>
        {showsColours(surface, kind) && at.length > 0 && (
          <span className="muted small">Each band's colour is under Appearance.</span>
        )}
      </div>
    </Field>
  );
}

/** The threshold bands' COLOURS, in the Builders' Appearance section.
 *
 *  One swatch per band, in the bands' own order (lowest first), each named by
 *  the limit it belongs to ("below 10", "10 to 50", "over 50") so it reads
 *  against the threshold rows in Data. It writes the same stored `bands` the
 *  limits editor writes -- the limits untouched, one colour changed -- so the
 *  two never diverge. Nothing to colour until a limit exists.
 */
export function BandColours({ kind, value, onChange, title = "Threshold colours",
                             ariaPrefix = "" }) {
  if (!BAND_KINDS.includes(kind)) return null;
  const at = Array.isArray(value?.at) ? value.at : [];
  if (!at.length) return null;
  const colors = bandColours(value);
  return (
    // A block wearing the field classes: several inputs, so not one <label>.
    <div className="ui-field">
      <span className="ui-field-label">{title}</span>
      <div style={{ display: "grid", gap: 6 }}>
        {colors.map((c, i) => (
          <div key={i} className="row" style={{ gap: 8, alignItems: "center" }}>
            <input type="color" value={c} aria-label={`${ariaPrefix}colour ${bandLabel(i, at)}`}
                   onChange={(e) => onChange({ at: [...at],
                     colors: colors.map((x, j) => (j === i ? e.target.value : x)) })}
                   style={{ width: 34, height: 26, padding: 0, border: 0,
                            background: "none" }} />
            <span className="muted small">{bandLabel(i, at)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** ONE INPUT'S OWN THRESHOLDS (owner, 2026-10-13: "configure threshold rules
 *  and colours separately for each data series or camera"). A choice -- use
 *  the chart's shared thresholds, or set this camera's own -- and, when its
 *  own, the LIMITS (BandEditor). Stored on the input as `bands`; absent means
 *  the shared ones, so every chart saved before behaves as it did. The
 *  colours are look: on the node a grey line says where they are set, in the
 *  Builders they are under Appearance, one group per camera. Drawn inside the
 *  input row's collapsed Override, on the node and in the Builders alike. */
export function SeriesBands({ kind, name, value, shared, onChange, surface = "builder" }) {
  if (!BAND_KINDS.includes(kind)) return null;
  const own = !!(value && Array.isArray(value.at) && value.at.length);
  return (
    <>
      <Field label="Thresholds for this input">
        <select className="input" value={own ? "own" : ""}
                aria-label={`Thresholds for ${name}`}
                onChange={(e) => onChange(e.target.value === "own"
                  // Starts from the shared limits (or one at 10), colours too.
                  ? (shared && Array.isArray(shared.at) && shared.at.length
                    ? { at: [...shared.at], colors: [...(shared.colors || [])] }
                    : { at: [10], colors: [BAND_RAMP[0], BAND_RAMP[2]] })
                  : null)}>
          <option value="">Use the shared thresholds</option>
          <option value="own">Set thresholds for this input</option>
        </select>
      </Field>
      {own && (
        <BandEditor kind={kind} value={value} surface={surface}
                    label={`Thresholds of ${name}`} ariaPrefix={`${name} `}
                    onChange={(b) => onChange(b && b.at && b.at.length ? b : null)} />
      )}
    </>
  );
}

/** Write one override entry, or clear it when the value is the default.
 *
 *  The override maps beside a layout hold what an operator decided about a
 *  card whose ROW is not theirs to keep -- a compiled card rewritten by every
 *  workflow compile, a package card declared in Python. A map that also stored
 *  the defaults would grow an entry per card per knob and say nothing, so a
 *  value equal to the default REMOVES the entry rather than recording it.
 */
export function setOverride(map, id, value, fallback) {
  if (value && value !== fallback) map[id] = value;
  else delete map[id];
}

/** The same, for the two text sizes, which share one entry per card. */
export function setFont(map, id, widget) {
  const entry = { ...(map[id] || {}) };
  for (const [field, key] of [["label", "label_size"], ["value", "value_size"]]) {
    if (widget[key] && widget[key] !== "medium") entry[field] = widget[key];
    else delete entry[field];
  }
  if (Object.keys(entry).length) map[id] = entry;
  else delete map[id];
}

//: The chart shapes, mirroring builder.CHART_KINDS: these draw a SERIES and so
//: have something to group by, where a tile draws one number.
const CHART_KINDS = ["line", "area", "spline", "bar", "donut", "pie", "radar"];

//: What a card can ask of a table column is cardFields.WORK_OUT -- one
//: vocabulary with the workflow inspector, so a card reads the same in
//: either place.

//: How many series one chart may hold. Mirrors builder.MAX_SERIES, and the
//: fallback palette is the reason for the number: past eight SERIES_COLORS
//: repeats, and two lines in the same colour with nothing to tell them apart
//: is a chart that lies about how many things it is drawing.
export const MAX_SERIES = 8;

/** The colour series `i` draws in when nobody picked one.
 *
 *  charts.js `toRows` falls back BY POSITION, so the editor has to fall back
 *  the same way: a row showing one colour while the chart draws another is
 *  worse than a row showing no colour at all.
 */
export const seriesColorAt = (i) => SERIES_COLORS[i % SERIES_COLORS.length];

let sid = 0;
//: Stable per ROW rather than per position: the colour, the label and the
//: question belong to the series, so removing the row above one must not
//: hand its colour to the row that moves up into its place.
export const nextSeriesId = () => `s${Date.now().toString(36)}${(sid++).toString(36)}`;

//: Where a single series reads from, in the words the operator chooses it by.
//: `bucket`, `events` and `sql` are deliberately absent: SQL is out of series
//: by decision, and the other two are reached through the row that inherits
//: the card's own source (INHERIT_LABELS) because they have no per-row shape
//: of their own to edit.
export const SERIES_SOURCES = INPUT_SOURCES;

//: Which of those each shape offers. A live scalar has no time axis to be
//: drawn along, so it is not offered on a line; a ring of a time series says
//: nothing, so recorded samples are not offered on a donut. A bar chart is
//: both charts at once - a bar per hour and a bar per zone - so it takes the
//: lot.
const SOURCES_FOR_KIND = {
  line:   ["history", "query", "dataset"],
  area:   ["history", "query", "dataset"],
  spline: ["history", "query", "dataset"],
  bar:    ["history", "stat", "query", "dataset"],
  radar:  ["stat", "query", "dataset"],
  donut:  ["stat", "query", "dataset"],
  pie:    ["stat", "query", "dataset"],
};

//: Column types that can DATE a row, in both spellings. "datetime" is the
//: word a table declaration uses; the store normalises it to "timestamp"
//: (core/repos/datasets.py _TYPE_ALIASES), so the very column that said
//: "datetime" when it was picked says "timestamp" when the saved row is read
//: back. Matching on one word only would empty the picker on reload and show
//: no selection for a choice that was made and stored correctly.
const TIME_COL_TYPES = ["timestamp", "date", "datetime"];

//: Card sources a series row cannot spell. A row left with no source means
//: "whatever this card reads", which is how a bucket or a data-table card
//: keeps its own editor below instead of having half of it copied into a row
//: with nowhere to put the other half.
const INHERIT_LABELS = {
  bucket: "this card's bucket",
  events: "this card's events",
  dataset: "this card's data table (set below)",
  sql: "this card's SQL",
};

/** Does this shape draw a SERIES (and so hold several of them)? */
export const isChartKind = (kind) => CHART_KINDS.includes(kind);

/** The series a chart edits: what the widget stores, or the one row that a
 *  single-key widget already is.
 *
 *  Read-side and lossless. A widget saved before series existed carries a
 *  key, a source and a colour, and that IS one series - so the editor opens
 *  showing it as one, rather than as an empty list behind a button nobody
 *  found. The colour comes across because it is the one thing somebody chose
 *  by hand, and a migration that resets it to the palette loses a decision.
 *
 *  Sources a row cannot spell (bucket, events, dataset, sql) migrate to a row
 *  with NO source, which the contract reads as the widget's own: the filters,
 *  the group_by and the table such a card carries stay where they are.
 */
export function widgetSeries(w) {
  if (!w) return [];
  const rows = Array.isArray(w.series) ? w.series : [];
  // Positional ids for a stored row that has none. Deterministic on purpose:
  // this runs on every render, and an id minted here would remount the colour
  // input under the operator's cursor.
  if (rows.length) {
    return rows.map((s, i) => ((s && s.id) ? s : { ...(s || {}), id: `s${i + 1}` }));
  }
  const source = ["history", "stat", "query"].includes(w.source) ? w.source : "";
  const q = w.query || {};
  return [{
    id: "s1",
    label: "",
    color: w.color || "",
    source,
    agg: source === "history" ? (q.agg || "avg") : "last",
    ...(w.key ? { key: w.key } : {}),
    ...(source === "query" ? { query: { ...q } } : {}),
  }];
}

//: There is deliberately NO save-side half to that migration. A chart saved
//: before series existed used to leave the editor with the one row above
//: written onto it ("withSeries"), on the theory that the row was lossless.
//: It is lossless as a CONFIG and not as a PICTURE: the server draws a widget
//: that carries a series list through chart_series (a shared time grid at the
//: card's `range`, defaulting to 24h, the series named after its key or id),
//: and a widget without one through its own source path (a query chart's own
//: hour/day buckets over today/this week, a history chart's raw samples, the
//: series named after the card). So pressing Save on a layout that had never
//: been touched since 1.0.10 turned "People Detected" per hour today and
//: "Total Detection (Weekly)" per day into two identical 24h lines called by
//: their key (Tok Aba, 2026-10-06). A save sends the rows exactly as stored;
//: a chart gains a series list only when the operator edits one in the
//: editor, which writes the whole list (SeriesEditor) and means it.

/** A compiled chart's per-series WORDS and COLOURS, keyed by series id, on
 *  their way back to the workflow node that publishes the card.
 *
 *  A wf_* card is rebuilt from its node on every compile, so a label or a
 *  colour written onto the widget row would last until the next apply and
 *  then vanish - the same reason the template and the text sizes travel this
 *  way. The node stores them as data.series[<id>] = {label, color}, and the
 *  id is the series' own (stable per wire), so rewiring or reordering moves
 *  neither the word nor the colour onto the line beside it.
 *
 *  Every row is sent, blanks included: an id left out would read as "no
 *  opinion" and the node would keep a label the operator has just cleared.
 */

export function seriesStyle(w, before) {
  // UNDER THE CHART BUILDER MODEL (w.chart): what each input says of its own
  // is its override (chartModel.readWidgetModel), sent whole for every input
  // whenever the model or an override moved -- "" and null clear one on the
  // node -- so a migrated chart lands on its node in one consistent shape.
  if (w && w.chart && ["time", "column"].includes(w.chart.x_type)) {
    const rows = widgetSeries(w);
    const { overrides } = readWidgetModel(w, rows);
    const moved = JSON.stringify(w.chart) !== JSON.stringify((before || {}).chart || null)
      || rows.some((s) => {
        const b = widgetSeries(before).find((x) => x.id === s.id);
        return !b || (b.show !== false) !== (s.show !== false);
      });
    const out = {};
    for (const s of rows) {
      if (!s || !s.id) continue;
      const entry = { label: s.label || "", color: s.color || "" };
      const b0 = widgetSeries(before).find((x) => x.id === s.id);
      if (JSON.stringify(s.bands || null) !== JSON.stringify((b0 && b0.bands) || null)) {
        entry.bands = s.bands || null;          // null: back to the shared ones
      }
      if (moved) {
        const o = overrides[s.id] || {};
        entry.agg = o.agg ? foldOfCalc(o.agg) : "";
        entry.ts_field = o.ts_field !== undefined ? o.ts_field : null;
        entry.query = { group_by: o.group_by || "", range: o.range || "" };
        entry.show = s.show !== false;
      }
      out[s.id] = entry;
    }
    return out;
  }
  // The row as the editor LOADED it, by series id: a series' question (its
  // fold, its timestamp column, its grouping and period) is sent only where
  // the operator changed it, so a value the compiler merely guessed is never
  // pinned onto the node by pressing Save.
  const was = new Map(widgetSeries(before).map((s) => [s.id, s]));
  const out = {};
  for (const s of widgetSeries(w)) {
    if (!s || !s.id) continue;
    const entry = { label: s.label || "", color: s.color || "" };
    const b = was.get(s.id);
    if (JSON.stringify(s.bands || null) !== JSON.stringify((b && b.bands) || null)) {
      entry.bands = s.bands || null;            // null: back to the shared ones
    }
    if (b) {
      const q = s.query || {};
      const bq = b.query || {};
      // A table input's "Work out" is its STORE question (query.agg); the
      // row blanks its own fold when that changes. The node says the same
      // thing as the series entry's `agg` (the fold the compile derives the
      // question from), so that is what travels -- sending the blanked fold
      // told the node "" (the compiler's guess) and the next workflow save
      // put "average value" back (owner, CAM 1, 2026-10-12).
      const asked = foldOf(q.agg);
      if (s.source === "dataset" && (q.agg || "") !== (bq.agg || "") && asked) {
        entry.agg = asked;
      } else if ((s.agg || "") !== (b.agg || "")) {
        entry.agg = s.agg || "";
      }
      if ((s.ts_field || "") !== (b.ts_field || "")) entry.ts_field = s.ts_field || "";
      const query = {};
      if ((q.group_by || "") !== (bq.group_by || "")) query.group_by = q.group_by || "";
      if ((q.range || "") !== (bq.range || "")) query.range = q.range || "";
      if (Object.keys(query).length) entry.query = query;
    }
    out[s.id] = entry;
  }
  return out;
}

/** A compiled card's QUESTION, as the operator changed it here, in the
 *  NODE's own field names (workflow.CARD_QUESTION_FIELDS) -- on its way back
 *  to that node through /api/workflow/card-style.
 *
 *  Why: "Work out", "One bar per", the period and the shape are offered on a
 *  wf_* card exactly as on its node, but the row they were written onto is
 *  server-owned (the partial save keeps the stored one), so the choice lasted
 *  until the next workflow save and then the node's own answer came back.
 *
 *  Diffed against the row as this editor LOADED it (`before`), never sent
 *  whole: the compiled row carries the compiler's guesses (a bucket chosen
 *  for the period, "latest" for a number column), and writing those onto the
 *  node would turn every default into a pinned value. No `before`, nothing
 *  sent. The wiring -- source, key, which column -- is the canvas's and is
 *  never in here; `field` only for a card that names its column itself.
 */
export function cardQuestionEdits(before, after) {
  if (!before || !after) return {};
  const out = {};
  const same = (a, b) => (a ?? "") === (b ?? "");
  const bq = before.query || {};
  const aq = after.query || {};
  if (after.kind && !same(after.kind, before.kind)) out.kind = after.kind;
  if (!same(aq.agg, bq.agg)) out.agg = aq.agg || "";
  if (!same(aq.field, bq.field)) out.field = aq.field || "";
  // The two are alternatives (one bar per period OR per column), so they
  // travel as a pair: setting one must clear the other on the node too.
  if (!same(aq.bucket, bq.bucket) || !same(aq.group_by, bq.group_by)) {
    out.bucket = aq.bucket || "";
    out.group_by = aq.group_by || "";
  }
  // One `range` on the node: a recorded card's period lives in its query, a
  // sampled chart's time range on the widget itself.
  if (!same(aq.range, bq.range)) out.range = aq.range || "";
  if (!same(after.range, before.range)) out.range = after.range || "";
  if (after.limit && after.limit !== before.limit) out.limit = after.limit;
  // The time axis (services/timeaxis.py): the node has ONE step, `bucket`,
  // which a sampled chart draws as its grid and a store input as its
  // bucket -- so the Builder's step goes there, and the old `interval` is
  // cleared so the two cannot disagree.
  if (!same(after.interval, before.interval)) {
    out.bucket = after.interval || "";
    out.interval = "";
  }
  if (!same(after.range_from, before.range_from) || !same(after.range_to, before.range_to)) {
    out.range = after.range || "";
    out.range_from = after.range_from || "";
    out.range_to = after.range_to || "";
  }
  if (!same(after.unit, before.unit)) out.unit = after.unit || "";
  // A card's ONE Date range: a live rule's counter (`window`), or a
  // recorded card's picked dates, which ride its query.
  if (!same(after.window, before.window)) out.window = after.window || "live";
  if (!same(aq.range_from, bq.range_from) || !same(aq.range_to, bq.range_to)) {
    out.range = aq.range || "";
    out.range_from = aq.range_from || "";
    out.range_to = aq.range_to || "";
  }
  // THE CHART BUILDER MODEL: when it moved, the whole model goes to the node
  // in the node's own field names (chartModel.modelNodeFields), over the
  // lowered fields diffed above -- one consistent shape, the same one the
  // node inspector writes.
  if (after.chart && ["time", "column"].includes(after.chart.x_type)
      && JSON.stringify(after.chart) !== JSON.stringify(before.chart || null)) {
    Object.assign(out, modelNodeFields(after.chart, after.kind));
  }
  // A status pill's rule and words, under the node's names.
  if (after.kind === "status") {
    const bs = before.status || {};
    const as = after.status || {};
    if (as.op && !same(as.op, bs.op)) out.op = as.op;
    if (as.value !== undefined && as.value !== bs.value) out.value = as.value;
    if (!same(as.ok_label, bs.ok_label)) out.ok_label = as.ok_label || "";
    if (!same(as.alert_label, bs.alert_label)) out.alert_label = as.alert_label || "";
  }
  return out;
}

/** The rows as this editor loaded them, by id -- the `before` that
 *  cardQuestionEdits and seriesStyle diff against. Reset after every save,
 *  so the next save sends only what changed since. */
export function useRowBase() {
  const ref = useRef(new Map());
  return useMemo(() => {
    const clone = (w) => JSON.parse(JSON.stringify(w));
    return {
      reset(rows) { ref.current = new Map((rows || []).map((w) => [w.id, clone(w)])); },
      add(w) { if (w && w.id && !ref.current.has(w.id)) ref.current.set(w.id, clone(w)); },
      get(id) { return ref.current.get(id) || null; },
    };
  }, []);
}

/** The question a "query" source asks of the recorded events: what to work
 *  out, over which column, grouped how, over what period.
 *
 *  Factored out of the inspector because a SERIES row asks exactly this
 *  question - and two copies of it, one spelled inline in the row editor,
 *  would drift the first time a bucket was added to the vocabulary. Takes
 *  and returns the WHOLE query, so a caller never has to know that group_by
 *  and bucket are alternatives.
 */
export function QueryFields({ query, meta, grouped = true, onChange,
                             chartBuilder = false, oneRange = false }) {
  const q = query || {};
  const qv = (meta || {}).query || {};
  const setQ = (partial) => onChange({ ...q, ...partial });
  // On a chart the question (what to count, grouped how, over which dates)
  // is the chart builder's; only the filter stays here.
  if (chartBuilder) {
    return (
      <EventTypeField value={(q.filters || {}).type || ""}
                      onChange={(v) => setQ({ filters: { ...(q.filters || {}), type: v } })} />
    );
  }
  // The same labels, order and options the node's "recorded events" panel
  // draws (cardFields.jsx), narrowed to what the server says it accepts.
  return (
    <>
      <WorkOutField value={q.agg || "count"} only={qv.aggregations || ["count"]}
                    onChange={(v) => setQ({ agg: v })} />
      {q.agg && q.agg !== "count" && (
        <Field label={CARD_FIELDS.column.label}>
          <select className="input" value={q.field || ""}
                  onChange={(e) => setQ({ field: e.target.value })}>
            <option value="">- choose a column -</option>
            {(qv.columns || []).map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
      )}
      {grouped && (
        <>
          <Field label="Group by"
                 hint="A recorded field - one bar per value, in name order. Empty for one total.">
            <select className="input" value={q.group_by || ""}
                    onChange={(e) => setQ({ group_by: e.target.value, bucket: "" })}>
              <option value="">- column -</option>
              {(qv.columns || []).map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <StepField value={q.bucket || ""} emptyLabel="None - one total"
                     lockedReason={q.group_by ? X_FIELD_REASON : ""}
                     onChange={(v) => setQ({ bucket: v })} />
        </>
      )}
      <EventTypeField value={(q.filters || {}).type || ""}
                      onChange={(v) => setQ({ filters: { ...(q.filters || {}), type: v } })} />
      {!oneRange && (
        <PeriodField value={q.range || ""} onChange={(v) => setQ({ range: v })} />
      )}
    </>
  );
}

/** The data tables this system writes, read off the workflow catalogue.
 *
 *  The compiler already publishes one entry per table column, each carrying
 *  its table name and that table's columns, so a dataset series row can be
 *  offered a real list with no server change. A `meta.datasets` of its own
 *  would be the straighter contract - it is in the handoff note, not invented
 *  here.
 */
export function datasetChoices(meta) {
  const seen = new Map();
  for (const c of (meta || {}).catalog || []) {
    if (!c || !c.dataset) continue;
    const cols = Array.isArray(c.columns) ? c.columns : [];
    const had = seen.get(c.dataset);
    // The widest column list wins: catalogue entries for the same table are
    // one per column, and only some of them carry the whole schema.
    if (!had || cols.length > had.length) seen.set(c.dataset, cols);
  }
  return [...seen.entries()].map(([name, columns]) => ({ name, columns }));
}

/** The fixed vocabulary: generic widget kinds, before any workflow is involved. */
export const PALETTE = [
  { kind: "tile",   source: "stat",    icon: "▣", label: "Stat tile",  hint: "one number" },
  { kind: "line",   source: "bucket",  icon: "◠", label: "Line chart",  hint: "a per-day bucket" },
  { kind: "line",   source: "history", icon: "∿", label: "History chart", hint: "a live value over time", palette: "history" },
  { kind: "bar",    source: "bucket",  icon: "▥", label: "Bar chart",   hint: "a per-day bucket" },
  { kind: "donut",  source: "bucket",  icon: "◔", label: "Donut",       hint: "a grouped bucket" },
  { kind: "table",  source: "events",  icon: "▤", label: "Event table", hint: "recent events" },
  { kind: "video",  source: "stat",    icon: "▶", label: "Live video",  hint: "the camera feed" },
  { kind: "status", source: "stat",    icon: "◉", label: "Status / alert", hint: "OK or ALERT pill" },
  // Three states, not two - "no object" is its own answer, so it gets its own
  // widget rather than a status pill that has to lie about an empty frame.
  { kind: "verdict", source: "stat", key: "compliance", icon: "⬤",
    label: "Violation status", hint: "green / red / no object" },
  // WHERE, rather than how many. Bound to a Heatmap function's key: the card
  // reads the whole family of per-cell stats off that one prefix.
  { kind: "heatmap", source: "stat", icon: "▦", label: "Heatmap",
    hint: "where objects go" },
  { kind: "snap_gallery", source: "stat", key: "", icon: "▤",
    label: "Camera snaps", hint: "latest snapshots, Previous / Next" },
  // CONTROLS, not readings: they change a setting when ticked. The AI
  // model's classes are the Models page's own choice (same setting, both
  // ways); the Class Filters are the workflow's named ones, ticked = running.
  { kind: "ai_classes", source: "stat", key: "", icon: "☑",
    label: "AI model classes", hint: "choose what the model detects" },
  { kind: "class_filters", source: "stat", key: "", icon: "☰",
    label: "Class filter", hint: "pause or run a named Class Filter" },
  // STATIC cards: what the operator puts on them, no data at all.
  { kind: "image_card", source: "stat", key: "", icon: "▧",
    label: "Image card", hint: "a picture you upload" },
  { kind: "text_card", source: "stat", key: "", icon: "¶",
    label: "Text card", hint: "your own words" },
];

//: The kinds that change something rather than read a value. They bind no
//: key and have no Data section; a reading cannot be turned into one.
export const CONTROL_KINDS = ["button", "ai_classes", "class_filters"];
//: Cards that show what the operator put on them (builder.STATIC_KINDS):
//: no key, no Data section; like a control, each is only ever itself.
export const STATIC_KINDS = ["text_card", "image_card"];
const SELF_ONLY_KINDS = [...CONTROL_KINDS, ...STATIC_KINDS];

//: Widgets that answer "what is happening RIGHT NOW" - a camera feed, an
//: alert pill, a compliance light. They belong on the Dashboard, which is the
//: page someone watches. Analytics answers "what happened over a period", and
//: a live tile inside a Today/Week/Month view is a category error: the range
//: control does not apply to it, so it silently ignores the very thing the
//: page is scoped by.
//: An Inject BUTTON too: it is something you press now, not a reading.
const LIVE_ONLY_KINDS = ["video", "snap_gallery", "status", "verdict", ...CONTROL_KINDS];

//: WHAT THE BUILDERS' "Widgets" GROUP OFFERS (Tok Aba, 2026-10-14). Every
//: card that READS data now comes from the Workflow canvas ("Dashboard
//: cards", below), so the generic data widgets -- stat tile, line / history /
//: bar chart, donut, event table, heatmap -- left the palette. What stays is
//: what no workflow node makes: the camera feed and the two controls.
//:
//: Status / alert and Violation status left too (Tok Aba, 2026-10-14): both
//: are made on the canvas as a verdict / status card. A Violation status that
//: builder.seed_main_widgets put on a new system's dashboard is untouched --
//: it renders, edits and deletes as before; it just is not offered here.
//:
//: PALETTE above stays the factory vocabulary (newWidget), so a widget of any
//: of those kinds already on a page keeps rendering and editing as before.
//: There is no image or text widget kind (builder.KINDS) -- none is invented.
//: AI model classes left the palette too (Tok Aba, 2026-10-14); one already
//: on a page keeps rendering, editing and deleting as before.
const DASHBOARD_WIDGET_KINDS = ["video", "snap_gallery", "class_filters", "image_card", "text_card"];
export const DASHBOARD_PALETTE = DASHBOARD_WIDGET_KINDS
  .map((k) => PALETTE.find((p) => p.kind === k)).filter(Boolean);
//: Analytics answers "what happened over a period": of the above only the
//: Image and Text cards suit it (the rest are LIVE_ONLY_KINDS).
export const ANALYTICS_PALETTE = DASHBOARD_PALETTE.filter(
  (p) => !LIVE_ONLY_KINDS.includes(p.kind));

let uid = 0;
export const nextId = (kind) => `${kind}_${Date.now().toString(36)}${(uid++).toString(36)}`;

/** A palette entry as a fresh widget, bound to the first key that fits. */
export function newWidget(item, sources) {
  // History widgets sample the same scalar keys the stat source lists.
  const keys = sources[item.source === "history" ? "stat" : item.source] || [];
  const w = {
    id: nextId(item.kind),
    title: item.label,
    kind: item.kind,
    source: item.source,
    // A palette entry may pin its own key (the verdict widget always reads
    // `compliance`); otherwise the first available key is a starting point.
    key: item.key ?? (item.source === "events" || item.kind === "video" || item.kind === "snap_gallery"
      ? "" : (keys[0] || "")),
    span: ["tile", "status", "verdict"].includes(item.kind) ? 1
      : CONTROL_KINDS.includes(item.kind) || STATIC_KINDS.includes(item.kind) ? 2 : 4,
    capability: "", color: "", sub: "", unit: "", limit: 15, query: {},
    // The default treatment/paint. Carried explicitly so a widget always HAS
    // the field -- the inspector reads current.template/current.bg, and an
    // absent key would make the picker look unset on a brand-new widget.
    template: "plain", bg: "",
    // No thresholds until somebody sets one: a brand-new chart is a plain
    // single-colour line, exactly as it was before bands existed.
    bands: {},
    // ...and one colour for every day in it, for the same reason.
    by_day: false,
    grid: {},
    status: item.kind === "status"
      ? { op: ">=", value: 1, ok_label: "OK", alert_label: "ALERT" } : {},
    range: item.source === "history" ? "24h" : "",
  };
  // A chart is born holding ONE series, prefilled from the source and key it
  // was dragged with. Not an empty list: the editor's first row is what tells
  // an operator that a chart can hold more than one input at all, and a list
  // that starts empty says the opposite.
  // ...and opens on "Choose a chart type", the dragged kind preselected
  // (chartTypes.markFreshChart: this session only, nothing saved).
  if (isChartKind(w.kind)) markFreshChart(w.id);
  return isChartKind(w.kind) ? { ...w, series: widgetSeries(w) } : w;
}

/** A verdict card's two words -- normal and alert -- read off its state rows.
 *
 *  {} when the rows are a custom mapping a pair of words cannot express (a
 *  warning tier, a state at some other value): those are edited on the
 *  workflow node, row by row. Blank words when there are no rows at all,
 *  which is the operator's "back to the stock words" and has to reach the
 *  node as a blank, not as nothing.
 *
 *  Shared by the inspector (what to show) and both Builders' saves (what to
 *  write onto the node), so the two cannot read the same rows differently.
 */
//: How a chart is DRAWN, set in Appearance (rendering addendum, owner
//: 2026-10-13): axis titles ("" = automatic), a bar chart's orientation and
//: style, values on the chart ("" = automatic). Mirrors chartmodel.LOOK_FIELDS.
export const CHART_LOOK_FIELDS = ["x_title", "y_title", "orientation", "bar_style",
                                  "data_labels"];
/** A compiled chart's look, on its way back to its node through card-style
 *  (every field, blanks included, so clearing one reaches the node). */
export function chartLook(w) {
  if (!w || !isChartKind(w.kind)) return {};
  return Object.fromEntries(CHART_LOOK_FIELDS.map((f) => [f, w[f] || ""]));
}

/** The chart's look controls: axis titles (placeholders say the automatic
 *  ones), and on a bar its orientation and style; values on the chart. */
function ChartLookFields({ current, patch, model }) {
  if (!isChartKind(current.kind)) return null;
  const axes = layoutOf(current.kind) === "axes";
  const auto = autoAxisTitles(model, { unit: current.unit || "", stepWords });
  return (
    <>
      {axes && (
        <>
          <Field label="X axis title" hint="Blank uses the automatic title shown.">
            <input className="input" value={current.x_title || ""} maxLength={40}
                   placeholder={auto.x} aria-label="X axis title"
                   onChange={(e) => patch(current.id, { x_title: e.target.value })} />
          </Field>
          <Field label="Y axis title" hint="Blank uses the automatic title shown.">
            <input className="input" value={current.y_title || ""} maxLength={40}
                   placeholder={auto.y} aria-label="Y axis title"
                   onChange={(e) => patch(current.id, { y_title: e.target.value })} />
          </Field>
        </>
      )}
      {current.kind === "bar" && (
        <>
          <Field label="Bars" hint="Horizontal lists the categories down the left.">
            <select className="input" value={current.orientation || ""}
                    aria-label="Bar orientation"
                    onChange={(e) => patch(current.id, { orientation: e.target.value })}>
              <option value="">Vertical</option>
              <option value="horizontal">Horizontal</option>
            </select>
          </Field>
          <Field label="Bar style">
            <select className="input" value={current.bar_style || ""}
                    aria-label="Bar style"
                    onChange={(e) => patch(current.id, { bar_style: e.target.value })}>
              <option value="">Filled bars</option>
              <option value="lollipop">Lollipop - a line and a dot</option>
            </select>
          </Field>
        </>
      )}
      <Field label="Show values on the chart"
             hint="Automatic and Off show a value when you hover a point; On prints every value on the chart.">
        <select className="input" value={current.data_labels || ""}
                aria-label="Show values on the chart"
                onChange={(e) => patch(current.id, { data_labels: e.target.value })}>
          <option value="">Automatic</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>
      </Field>
    </>
  );
}

export function verdictLabels(w) {
  const states = Array.isArray(w.states) ? w.states : [];
  if (!states.length) return { ok_label: "", alert_label: "" };
  const ok = states.find((s) => s.tone === "ok");
  const alert = states.find((s) => s.tone === "alert");
  const plain = ok && alert
    && states.every((s) => ["ok", "alert", "empty"].includes(s.tone));
  return plain ? { ok_label: ok.label || "", alert_label: alert.label || "" } : {};
}

/** A catalogue entry, already configured by the workflow compiler, as a widget. */
export function catalogWidget(c) {
  return {
    id: nextId(c.kind),
    title: c.title || c.label,
    kind: c.kind,
    source: c.source,
    key: c.key,
    span: c.kind === "tile" || c.kind === "button" ? 1 : 4,
    capability: "", color: "", sub: "", unit: c.unit || "", limit: 15,
    template: c.template || "plain", bg: c.bg || "",
    // A dataset entry carries its table and the question to ask of it; a stat
    // entry carries neither. Dropping them here was why a table column could
    // be dragged into the editor and then render nothing -- the widget landed
    // with source "dataset" and no dataset to read.
    query: c.query ? { ...c.query } : {},
    ...(c.dataset ? { dataset: c.dataset, columns: c.columns || [] } : {}),
    // ...and the SERIES a compiled chart carries. Same omission one field
    // along: a two-line chart dragged out of the palette that lands showing
    // one line is not the card that was picked up.
    ...(Array.isArray(c.series) && c.series.length
      ? { series: c.series.map((s) => ({ ...s })) } : {}),
    // ...and a QUERY entry carries its statement and the tables it may read.
    // Exactly the same omission as the dataset one above, one source along: a
    // Query card dragged out of the palette landed with source "sql" and no
    // SQL, which the server refuses ("a query widget needs a SELECT statement
    // and the tables it may read") -- so the drop failed on a card that was
    // perfectly well formed the moment before it was picked up.
    ...(c.sql ? { sql: c.sql, tables: c.tables || [] } : {}),
    // How it arrives, alongside the template it is dressed in: both are the
    // card's look, and a copy that keeps one but not the other is a copy that
    // does not look like what was dragged.
    anim: c.anim || "", anim_replay: !!c.anim_replay,
    // ...and so are its thresholds, for the same reason: a banded chart
    // dragged out of the palette that lands as a plain line is not the card
    // that was picked up.
    bands: c.bands && c.bands.at ? { at: [...c.bands.at],
                                     colors: [...(c.bands.colors || [])] } : {},
    // ...and so is per-day colouring: a card dragged out of the palette that
    // lands in one colour is not the card that was picked up.
    by_day: !!c.by_day,
    // The LINK back to the workflow node this was dragged from. It is what
    // keeps the copy alive: every workflow apply refreshes a linked widget's
    // body from the node's fresh compile, so editing the node updates every
    // card made from it. Without it the copy froze at drag time -- verdict
    // labels included.
    ...(c.node ? { node: c.node } : {}),
    // A BUTTON presses an Inject: which function it shows the runs of. The
    // press itself goes to `node`, and every workflow save refreshes this.
    ...(c.inject ? { inject: { ...c.inject } } : {}),
    // A verdict's state rows are its BODY -- what 0 and 1 mean -- and the
    // link above refreshes them on the next recompile. Until then the copy
    // read COMPLIANT / VIOLATION whatever the node said, because the rows
    // were not carried across at all.
    ...(Array.isArray(c.states) && c.states.length
      ? { states: c.states.map((s) => ({ ...s })) } : {}),
    grid: {}, status: {},
    range: c.source === "history" ? (c.range || "24h") : "",
  };
}

//: What each history range key reads as lives in cardFields.jsx
//: (RANGE_LABELS, re-exported above): the node's Time range reads it too.

//: THE "Dashboard cards" GROUP (Tok Aba, 2026-10-14): only what was drawn on
//: the Workflow canvas -- the named dashboard nodes (catalogue origin "card")
//: and the Inject buttons (origin "control") -- under headings in plain
//: words. The measurements, variables and table columns the catalogue also
//: carries are not listed any more (GET /api/builder still sends them; the
//: compile is unchanged). A chart's heading is its chart type, read the way
//: the chart type picker reads it (chartTypes.chartTypeOf). Only headings
//: with a card are drawn; a kind nobody named lands under "Other".
export const CARD_CATEGORIES = [
  ["status", "Status"], ["tile", "Number tile"], ["gauge", "Gauge"],
  ["line", "Line chart"], ["area", "Area chart"], ["time_bar", "Time-based bar"],
  ["compare_bar", "Comparison bar"], ["pie", "Pie chart"], ["donut", "Donut chart"],
  ["radar", "Radar chart"], ["table", "Table"], ["events", "Event table"],
  ["heatmap", "Heatmap"], ["button", "Button"], ["other", "Other"],
];
//: The catalogue origins the group lists.
const CARD_ORIGINS = ["card", "control"];

/** The heading a workflow card sits under. */
export function cardCategory(c) {
  const k = (c && c.kind) || "";
  if ((c && c.origin === "control") || k === "button") return "button";
  if (k === "verdict" || k === "status") return "status";
  if (["tile", "gauge", "heatmap"].includes(k)) return k;
  if (k === "timeline") return "events";
  if (k === "table") return ["events", "query"].includes(c.source) ? "events" : "table";
  if (isChartKind(k)) {
    let x;
    try { x = readWidgetModel(c, widgetSeries(c)).model.x_type; } catch { x = undefined; }
    return chartTypeOf(k, x) || "other";
  }
  return "other";
}

/** [[categoryId, heading, [[entry, catalogIndex], ...]], ...] for the cards
 *  of `catalog`, non-empty headings only. The index is the drag payload. */
export function workflowCardGroups(catalog) {
  const rows = (catalog || []).map((c, i) => [c, i])
    .filter(([c]) => c && CARD_ORIGINS.includes(c.origin));
  return CARD_CATEGORIES.map(([id, title]) => [
    id, title, rows.filter(([c]) => cardCategory(c) === id)])
    .filter(([, , list]) => list.length);
}

/** Which Dashboard-cards headings are open, remembered per browser under
 *  `key` ({categoryId: true|false}). A browser that refuses storage still
 *  works; it just forgets. */
function readOpen(key) {
  try { return JSON.parse(localStorage.getItem(key) || "{}") || {}; } catch { return {}; }
}
function writeOpen(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}

/**
 * The Builders' "Dashboard cards": every card drawn on the Workflow canvas,
 * grouped by what it shows. Drag one onto the canvas, or click it to append.
 * With none yet, one sentence and the way to the Workflow page -- the only
 * place a data card is made now.
 *
 * Each heading is a drop-down (Tok Aba, 2026-10-14): a button with the
 * count, its list shown or hidden. All start shut but the first, unless this
 * browser remembers otherwise (`storageKey`, one per Builder); the heading
 * holding the card selected on the canvas (`selectedNode`, the card's
 * workflow node id) opens by itself.
 */
export function WorkflowCardsPalette({ catalog, editable, onAdd, storageKey = "builder.cards.open",
                                       selectedNode = null }) {
  const groups = useMemo(() => workflowCardGroups(catalog), [catalog]);
  const [saved, setSaved] = useState(() => readOpen(storageKey));
  const isOpen = (id, i) => (id in saved ? !!saved[id] : i === 0);
  const toggle = (id, i) => {
    const next = { ...saved, [id]: !isOpen(id, i) };
    setSaved(next);
    writeOpen(storageKey, next);
  };
  // The card picked on the canvas: its heading opens (and stays open).
  useEffect(() => {
    if (!selectedNode) return;
    const g = groups.find(([, , list]) => list.some(([c]) => c.node === selectedNode));
    if (!g || saved[g[0]] === true) return;
    const i = groups.indexOf(g);
    if (i === 0 && !(g[0] in saved)) return;
    const next = { ...saved, [g[0]]: true };
    setSaved(next);
    writeOpen(storageKey, next);
  }, [selectedNode, groups]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="palette-cards" data-testid="workflow-cards">
      <div className="section-title" style={{ marginTop: 12 }}>Dashboard cards</div>
      {!groups.length ? (
        <div className="palette-empty">
          <p className="muted small">
            Add a card on the Workflow canvas and it will appear here.
          </p>
          <p className="muted small">Give it a title on the canvas to list it.</p>
          <Link className="btn btn-sm" to="/workflow">Open the Workflow</Link>
        </div>
      ) : (
        <>
          <p className="muted small">
            Cards you drew on the Workflow canvas. Drag one onto the canvas, or
            click it to append.
          </p>
          {groups.map(([id, title, list], gi) => {
            const open = isOpen(id, gi);
            const listId = `palette-cat-${storageKey}-${id}`.replace(/[^a-zA-Z0-9_-]/g, "-");
            return (
              <div key={id} className="palette-cat" role="group" aria-label={title}>
                <button type="button" className="palette-cat-title" aria-expanded={open}
                        aria-controls={listId} onClick={() => toggle(id, gi)}>
                  <ChevronRight size={13} aria-hidden="true"
                                className={`palette-cat-chevron${open ? " is-open" : ""}`} />
                  <span>{title}</span>
                  <span className="palette-cat-count">{list.length}</span>
                </button>
                {open && (
                  <div id={listId} className="palette-cat-list">
                    {list.map(([c, i]) => (
                      // Keyed by POSITION in the catalogue: several entries
                      // carry a blank key.
                      <button key={`${c.node || c.key || c.label}-${i}`} type="button"
                              className="palette-item" disabled={!editable}
                              draggable={editable} title={c.key || c.label}
                              onDragStart={(e) => {
                                e.dataTransfer.setData("application/x-catalog", String(i));
                                notePaletteDrag(c.kind);       // the drop placeholder's size
                              }}
                              onClick={() => onAdd(c)}>
                        <span className="palette-icon">✦</span>
                        <span>
                          <strong>{c.title || c.label}</strong>
                          <em>{c.kind}{c.unit ? ` · ${c.unit}` : ""}
                            {c.group ? ` · ${c.group}` : ""}</em>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

/**
 * Live preview, debounced: every edit re-renders against real data, but a drag
 * across ten slots must not be ten round trips.
 *
 * `ready` gates the first call until the page has its metadata. `range` is the
 * window the preview is scoped to (Analytics' global selector); omitted, the
 * endpoint keeps its own default.
 */
export function useLivePreview(widgets, ready, range, editingId) {
  const toast = useToast();
  const [view] = useViewSettings();
  const [preview, setPreview] = useState([]);
  // The last rejection this hook said out loud. A preview runs every 250ms
  // while the operator types, so an unfinished row used to stack one identical
  // sticky toast per keystroke - three copies of "widget 4: a dataset widget
  // needs a dataset and its columns" over the panel being filled in. The same
  // sentence twice in a row tells nobody anything the first one did not, so it
  // is said once and re-armed the moment the reason changes or the preview
  // succeeds.
  const said = useRef("");
  useEffect(() => {
    // Live preview off: stop asking the server entirely rather than asking
    // and discarding. A heavy dashboard re-queries on every drag, which is
    // the cost this switch exists to remove.
    if (!ready || !view.livePreview) return undefined;
    // THE CARD UNDER THE CURSOR, HALF FILLED IN, IS NOT AN ERROR.
    //
    // A row being typed into is incomplete for as long as it takes to fill
    // it, and the page must not alarm anybody about a state the operator is
    // on their way out of. So that one card is left out of the request and
    // its rows are marked in the editor instead (WidgetInspector reads these
    // warnings off the preview entry); every other card on the page keeps
    // previewing, which is what it was doing before the editing started.
    const editing = editingId
      ? (widgets || []).find((w) => w.id === editingId) : null;
    const holes = editing ? seriesHoles(editing) : [];
    const skip = holes.length ? editingId : null;
    const asked = skip ? widgets.filter((w) => w.id !== skip) : widgets;
    const t = setTimeout(() => {
      api.post("/api/builder/preview",
               range ? { widgets: asked, range } : { widgets: asked })
        .then((d) => {
          said.current = "";
          setPreview((old) => mergePreview(old, d.widgets || [], skip, holes));
        })
        .catch((e) => {
          // Friendly on top, technical underneath - the Toast contract. The
          // server's sentence is what tells two failures apart, so it is also
          // what the dedupe is keyed on.
          const detail = String(e?.message || e || "");
          if (said.current === detail) return;
          said.current = detail;
          toast.error("Preview failed", detail);
        });
    }, 250);
    return () => clearTimeout(t);
  }, [widgets, ready, range, editingId, view.livePreview]); // eslint-disable-line react-hooks/exhaustive-deps
  return preview;
}

/** What the server just answered, with the card being edited left as it was.
 *
 *  The skipped card keeps its PREVIOUS picture rather than losing one: a card
 *  that blanks the moment you click into its second row reads as "you broke
 *  it", which is the opposite of what is happening. Its warnings ride along
 *  on the same entry, in the shape the server sends them ({id,label,message}
 *  per series), so the editor has one thing to read whether the complaint
 *  came from here or from the server.
 */
function mergePreview(old, fresh, skip, holes) {
  if (!skip) return fresh;
  const kept = (old || []).find((p) => p.id === skip);
  return [...fresh, { ...(kept || { id: skip }), id: skip, warnings: holes }];
}

/** The rows of a chart that cannot be drawn yet, as warnings on those rows.
 *
 *  Same shape and the same voice as the server's own per-series warnings, and
 *  deliberately only the holes a row CANNOT be previewed through - a query row
 *  with nothing but a count is a perfectly good row, and marking it would put
 *  a warning on a chart that is finished.
 */
export function seriesHoles(w) {
  if (!w || !isChartKind(w.kind) || w.source === "sql") return [];
  const out = [];
  widgetSeries(w).forEach((s, i) => {
    const name = s.label || `Series ${i + 1}`;
    const add = (message) => out.push({ id: s.id || `s${i + 1}`, label: name, message });
    if (s.source === "dataset") {
      const agg = (s.query || {}).agg || "count";
      if (!s.dataset) add("Pick the data table this input reads.");
      else if (agg !== "count" && !s.field) add("Pick the column to work out.");
    } else if (s.source === "history" || s.source === "stat") {
      if (!s.key) add("Pick the value this input reads.");
    }
  });
  return out;
}

/** The per-row complaints on a previewed widget, as {seriesId: message}.
 *
 *  The server sends one entry per series that could not be drawn; anything
 *  without an id is about the card as a whole and is not a row's problem.
 */
export function seriesWarnings(entry) {
  const out = {};
  for (const w of (entry && entry.warnings) || []) {
    if (w && w.id && w.message) out[w.id] = String(w.message);
  }
  return out;
}

/**
 * The properties panel for the selected widget. `meta` is the /api/builder
 * payload (sources, kinds, key labels, history/query vocabulary, cameras,
 * capabilities); `patch(id, fields)` is the caller's widget updater.
 */
/** How big the words on a card are.
 *
 * TWO settings, not one, because the card's chrome and the card's answer are
 * read at different distances: a wall display wants a huge number over a small
 * title, and a dense page of small cards wants the title legible and the
 * number modest. One "font size" could say neither.
 *
 * The value size is offered only on the kinds that HAVE a value to size -- a
 * line chart's number is its axis, and a control that did nothing on eight of
 * the ten shapes would be a control nobody trusts on the other two.
 */
const TEXT_SIZE_OPTIONS = [["small", "S"], ["medium", "M"],
                           ["large", "L"], ["huge", "XL"]];
const HAS_VALUE = ["tile", "gauge", "verdict"];

function TextSizePicker({ current, patch }) {
  const row = (field, value) => (
    <div className="rail-row">
      {TEXT_SIZE_OPTIONS.map(([v, label]) => (
        <button key={v} type="button" title={v}
                className={"btn btn-sm" + ((value || "medium") === v ? " btn-accent" : "")}
                onClick={() => patch(current.id, { [field]: v })}>
          {label}
        </button>
      ))}
    </div>
  );
  return (
    <>
      <Field label="Title & note size"
             hint="The card's heading and the small line under its value. Applies to every kind of card, charts included.">
        {row("label_size", current.label_size)}
      </Field>
      {HAS_VALUE.includes(current.kind) && (
        <Field label="Value size"
               hint="The number or word this card exists to show.">
          {row("value_size", current.value_size)}
        </Field>
      )}
    </>
  );
}

/** A colour: the swatch, the hex, and the way back to the default.
 *
 *  Both colour fields were hand-rolled with the same eight inline styles, so
 *  they could and did drift apart. The native picker is offered because
 *  landing on a shade by typing hex is guesswork; the text box stays because
 *  a brand colour is usually pasted, not picked.
 */
function ColourField({ label, hint, value, fallback, onChange, resetTitle }) {
  return (
    <Field label={label} hint={hint}>
      <div className="rail-colour">
        <input type="color" value={value || fallback} title="Pick a colour"
               onChange={(e) => onChange(e.target.value)} />
        <input className="input" placeholder={fallback} value={value || ""}
               onChange={(e) => onChange(e.target.value)} />
        {value ? (
          <button type="button" className="btn btn-sm" title={resetTitle}
                  onClick={() => onChange("")}>↺</button>
        ) : null}
      </div>
    </Field>
  );
}

/** "Arrange": which card is in front where cards overlap (layerOrder.js).
 *  Each button is disabled at the end it moves towards. */
const ARRANGE_ICON = { front: ArrowUpToLine, forward: ArrowUp, backward: ArrowDown,
                       back: ArrowDownToLine };
function ArrangeFields({ current, siblings, patch }) {
  return (
    <div className="arrange-fields" role="group" aria-label="Arrange">
      <span className="seg-field-label">Arrange</span>
      <div className="arrange-buttons">
        {ARRANGE.map(([op, words]) => {
          const Icon = ARRANGE_ICON[op];
          return (
            <button key={op} type="button" className="btn btn-sm" aria-label={words}
                    disabled={!canArrange(siblings, current.id, op)}
                    onClick={() => {
                      for (const [wid, grid] of Object.entries(arrange(siblings, current.id, op))) {
                        patch(wid, { grid });
                      }
                    }}>
              <Icon size={13} aria-hidden="true" /> {words}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function WidgetInspector({ current, meta, patch, preview, siblings = null }) {
  const sources = meta?.sources || { stat: [], bucket: [], events: [] };
  // The chart type picker: open for a chart just added from the palette
  // (isFreshChart) or after "Change chart type". Hooks before the early
  // return, as React requires.
  const [typePickFor, setTypePickFor] = useState(null);
  const [, bumpPicker] = useState(0);
  if (!current) return null;
  // WHAT THE PREVIEW MADE OF THIS CARD. An incomplete row is reported per
  // series ({id,label,message}) rather than as a page-wide alarm, so the
  // complaint is read on the row that caused it, beside the field that
  // answers it. Accepts the whole preview list (what both Builders already
  // hold) or a single entry.
  const previewed = (Array.isArray(preview)
    ? preview.find((p) => p && p.id === current.id) : preview) || null;
  const rowWarnings = seriesWarnings(previewed);
  // A wf_* card is COMPILED from a workflow node and rewritten on every
  // compile; a partial save deliberately keeps the stored compiled copy so a
  // recompile cannot lose it. WHAT the card shows is therefore edited on its
  // node -- but how it SITS is the operator's (placements, kept beside the
  // layout), and how it LOOKS is written back onto the node itself when this
  // page saves (/api/workflow/card-style). That write-back is what stopped
  // the two sides disagreeing: the look used to live in an override map here,
  // so a card restyled on this page still showed its old dress on the canvas,
  // and a dress picked on the canvas was quietly overruled here.
  const compiled = String(current.id || "").startsWith("wf_");
  const set = (...vals) => vals.filter(
    (v) => v !== undefined && v !== null && v !== "" && v !== "plain"
      && v !== "medium" && !(Array.isArray(v) && !v.length)).length;

  // Every CHART edits a series list; everything that shows one value (a tile,
  // a gauge, a status pill, a verdict, a heatmap) keeps the single data key.
  // SQL is out of series by decision, so a SQL-backed chart keeps its
  // statement editor and nothing else about it changes.
  const chartSeries = isChartKind(current.kind) && current.source !== "sql";
  const seriesRows = chartSeries ? widgetSeries(current) : [];
  // A row with NO source reads whatever the CARD reads, so the card's own
  // source editor (a bucket key, a data table's question) still belongs on
  // screen. Once every row names its own source that editor would be a second
  // copy of controls the rows already carry, and two controls writing one
  // field is how they come to disagree.
  const inherits = seriesRows.some((s) => !s.source);
  // THE CHART BUILDER MODEL of this chart (chartModel.readWidgetModel): its
  // stored `chart`, or -- a chart saved before the builder -- what its rows
  // say, lifted. Every edit writes the model and lowers it onto the rows and
  // the card (widgetPatch), which is what the renderer reads.
  const chartState = chartSeries ? readWidgetModel(current, seriesRows) : null;
  // Its CHART TYPE (chartTypes.js): kind plus the model's X type.
  const chartTypeId = isChartKind(current.kind)
    ? chartTypeOf(current.kind, chartState?.model?.x_type) : "";
  const pickingType = !!chartTypeId
    && (isFreshChart(current.id) || typePickFor === current.id);
  const applyChartType = (id) => {
    const fresh = isFreshChart(current.id);
    const plan = planTypeSwitch(current.kind, chartState ? chartState.model : null, id, { fresh });
    clearFreshChart(current.id);
    setTypePickFor(null);
    bumpPicker((n) => n + 1);
    if (!plan || plan.same) return;
    if (!plan.full || !chartSeries) { patch(current.id, { kind: plan.kind }); return; }
    patch(current.id, { kind: plan.kind,
                        ...widgetPatch({ ...current, kind: plan.kind, series: seriesRows },
                                       seriesRows, plan.model, chartState.overrides) });
  };
  const writeChart = (m, ovr = chartState.overrides, rows = seriesRows) =>
    patch(current.id, widgetPatch({ ...current, series: rows }, rows, m, ovr));
  // The columns X, Y and the Date column may name: every input's table, and
  // the event store's fields for an event question.
  const tableChoices = datasetChoices(meta);
  const chartColumns = [];
  const addCols = (cols) => {
    for (const c of cols || []) {
      if (c && c.name && !chartColumns.some((x) => x.name === c.name)) chartColumns.push(c);
    }
  };
  if (chartSeries) {
    if (current.source === "dataset") addCols(current.columns);
    for (const s of seriesRows) {
      if (s.source === "dataset") {
        addCols(s.columns || (tableChoices.find((t) => t.name === s.dataset) || {}).columns);
      }
      if (s.source === "query" || (!s.source && current.source === "query")) {
        addCols(((meta || {}).query?.columns || []).map((n) => ({ name: n, type: "text" })));
      }
    }
  }
  const chartTimeCols = chartColumns
    .filter((c) => ["timestamp", "date", "datetime"].includes(c.type)).map((c) => c.name);
  const chartHasStore = ["dataset", "query"].includes(current.source)
    || seriesRows.some((s) => ["dataset", "query"].includes(s.source));

  // ---- the pieces, built once and placed into a section below --------------
  // Written as values rather than inline, because the same block sometimes
  // belongs to a different section depending on the kind of card, and a
  // condition spelled twice is a condition that will disagree with itself.

  // Same rule as the query editor below: the card's own table question stays
  // while a series row still inherits it, and gives way to the rows once they
  // each name their own source.
  const datasetFields = current.source === "dataset" && !chartSeries && (() => {
    const q = current.query || {};
    const cols = current.columns || [];
    const setQ = (partial) => patch(current.id, { query: { ...q, ...partial } });
    const agg = q.agg || "count";
    // Only a number can be totalled, averaged or ranked; unique/changes
    // count occurrences, so they take any column.
    const fieldCols = cols.filter(
      (c) => ["unique", "changes"].includes(agg) || c.type === "number");
    const isChart = CHART_KINDS.includes(current.kind);
    // A DATASET widget -- one dragged in from a Data Table -- had no editor at
    // all here: the question it asks was fixed at the moment it was dragged.
    // So a bar chart of a table could not be told what to total, what to group
    // by, or to go by date. Worse, a chart left grouped by the column it sums
    // draws one bar per VALUE of it -- "0.0" and "1.0" for a 0/1 column --
    // which reads as a broken chart and is really an unanswerable question.
    // Labels, order and options from cardFields.jsx: the node's own panel
    // draws the same four rows for the same question. The two groupings are
    // alternatives -- PerField sets one and clears the other.
    return (
      <>
        <WorkOutField value={agg} onChange={(v) => setQ({ agg: v })} />
        {agg !== "count" && (
          <Field label={CARD_FIELDS.column.label}>
            <select className="input" value={q.field || ""}
                    onChange={(e) => setQ({ field: e.target.value })}>
              <option value="">- choose a column -</option>
              {fieldCols.map((c) => (
                <option key={c.name} value={c.name}>{c.name}</option>
              ))}
            </select>
          </Field>
        )}
        {isChart && (
          <>
            <PerField groupBy={q.group_by} columns={cols} emptyLabel="nothing - one total, or by time"
                      onChange={(pair) => setQ(pair)} />
            <StepField value={q.bucket || ""} emptyLabel="None - one total"
                       lockedReason={q.group_by ? X_FIELD_REASON : ""}
                       onChange={(v) => setQ({ bucket: v })} />
            {q.group_by && q.group_by === q.field && (
              <p className="muted small c-warn">
                Grouping by the same column you are working out draws one
                bar per value of it - a 0/1 column gives you exactly two.
                Group by a period of time, or by another column.
              </p>
            )}
          </>
        )}
        {/* Which rows: the card's ONE Date range (cardRangeFields). */}
      </>
    );
  })();

  const cameraField = (current.kind === "video" || current.kind === "snap_gallery") && (() => {
    // A system may run several cameras behind one detection app, all served
    // from /api/detector/preview?camera=<id>. When the payload carries the
    // camera list we offer it; otherwise the id is typed, which still
    // round-trips (blank = the first camera).
    const raw = meta.cameras || sources.cameras;
    const cams = (Array.isArray(raw) ? raw : [])
      .filter((c) => c && (c.id || typeof c === "string"))
      .map((c) => (typeof c === "string" ? { id: c, name: c } : c));
    return (
      <Field label="Camera (blank = first)" hint="Blank shows the first camera.">
        {cams.length ? (
          <select className="input" value={current.camera || ""}
                  onChange={(e) => patch(current.id, { camera: e.target.value })}>
            <option value="">First camera</option>
            {cams.map((c) => (
              <option key={c.id} value={c.id}>{c.name || c.label || c.id}</option>
            ))}
          </select>
        ) : (
          <input className="input" value={current.camera || ""}
                 placeholder="camera id"
                 onChange={(e) => patch(current.id, { camera: e.target.value })} />
        )}
      </Field>
    );
  })();

  const keyFields = (() => {
    // Time-series shapes draw along an axis of time; the comparison shapes
    // put their series side by side.
    const isTs = ["line", "area", "spline"].includes(current.kind);
    const hv = meta.history || {};
    // A key the system declares but has not published yet (the app is
    // stopped, or the function has not evaluated once). It is bindable --
    // saying so beats the widget rendering empty with no explanation.
    const pending = new Set(meta.pending_keys || []);
    const keyLabel = (k) => {
      const friendly = (meta.key_labels || {})[k];
      const base = friendly ? `${friendly}  (${k})` : k;
      return pending.has(k) ? `${base} - no data yet` : base;
    };
    // No "Value" picker on a single-value card (Tok Aba, 2026-10-19): the
    // value is whatever the workflow node feeds it, so a card that was not
    // dragged from a workflow node has nothing to show, and a list of every
    // published id to choose from only confused people.
    const timed = current.source === "history"
      || seriesRows.some((s) => s.source === "history");
    return (
      <>
        {chartSeries && (
          <SeriesEditor
            series={seriesRows} widget={current} meta={meta} keyLabel={keyLabel}
            warnings={rowWarnings} chartState={chartState}
            onOverride={(sid, next) => writeChart(chartState.model,
                                                  { ...chartState.overrides, [sid]: next })}
            onChange={(next) => (current.chart
              // A chart under the model: a new or repointed row takes the
              // chart's X, Y and range at once.
              ? patch(current.id, { ...seriesPatch(current, next),
                                    ...widgetPatch({ ...current, series: next }, next,
                                                   chartState.model, chartState.overrides) })
              : patch(current.id, seriesPatch(current, next)))} />
        )}
        {timed && !chartSeries && (
          <RangeField value={current.range || "24h"} from={current.range_from}
                      to={current.range_to}
                      onChange={(r) => patch(current.id, r)} />
        )}
        {/* The interval is the shared x axis every series lands on, so it is
            offered wherever that axis exists - not only where the card was
            born a history card. A chart's is the chart builder's. */}
        {timed && isTs && !chartSeries && (
          <StepField value={current.interval
                            || ((seriesRows.find((s) => s.query && s.query.bucket) || {}).query || {}).bucket
                            || ""}
                     lockedReason={seriesRows.length
                       && seriesRows.every((s) => s.query && s.query.group_by)
                       ? X_FIELD_REASON : ""}
                     onChange={(v) => patch(current.id, { interval: v })} />
        )}
      </>
    );
  })();

  // A status pill's rule -- the same StatusFields the node draws; on a
  // compiled pill the Builders' save sends it to the node (cardQuestionEdits).
  const statusFields = current.kind === "status" && (() => {
    const st = current.status
      || { op: ">=", value: 1, ok_label: "OK", alert_label: "ALERT" };
    return (
      <StatusFields op={st.op} value={st.value} okLabel={st.ok_label}
                    alertLabel={st.alert_label}
                    onChange={(partial) => patch(current.id, { status: { ...st, ...partial } })} />
    );
  })();

  // The card's OWN query, for a card that is not a chart - or a chart whose
  // series still inherit it. A chart whose rows all name their source edits
  // the query on the row that asks it, where the label and the colour that
  // answer belongs to are.
  const queryFields = current.source === "query" && (!chartSeries || inherits) && (
    <QueryFields query={current.query} meta={meta}
                 grouped={current.kind !== "tile"} chartBuilder={chartSeries}
                 oneRange={!chartSeries}
                 onChange={(q) => patch(current.id, { query: q })} />
  );
  // A CARD's ONE Date range (cardFields.CardDateRange), the same control the
  // workflow node draws: rows a card counts take `query.range`; a compiled
  // card showing a live rule's value takes `window` (its node's "Count
  // over", the rule's counter). A hand-made live tile reads one key as it
  // is now and has no range to offer.
  const cardSource = chartSeries || CONTROL_KINDS.includes(current.kind)
    || ["video", "snap_gallery", "heatmap", "timeline"].includes(current.kind) ? null
    : ["dataset", "query"].includes(current.source) ? "recorded"
      : current.source === "sql" ? "none"
        : compiled && current.source === "stat" ? "live" : null;
  const cardRangeFields = cardSource && (() => {
    const q = current.query || {};
    const r = cardRangeOf(cardSource === "live"
      ? { window: current.window || "live" }
      : { range: q.range || "", range_from: q.range_from, range_to: q.range_to },
      cardSource);
    return (
      <CardDateRange source={cardSource} value={r.value} from={r.from} to={r.to}
                     note={r.note}
                     onChange={(fields) => patch(current.id, cardSource === "live"
                       ? { window: fields.window }
                       : { query: { ...q, range: fields.range,
                                    range_from: fields.range_from,
                                    range_to: fields.range_to } })} />
    );
  })();
  // THE CHART BUILDER (chartFields.ChartAxisFields), the same component the
  // workflow node draws: X, Y, interval and date range once for the chart,
  // the thresholds in its collapsed Advanced section.
  const chartBuilderFields = chartSeries && (
    <ChartAxisFields kind={current.kind} model={chartState.model}
                     onChange={(m) => writeChart(m)} columns={chartColumns}
                     // A compiled card whose inputs are wires: each input's
                     // own column is its Y (the node says the same). A card
                     // asking one table itself picks its Y column here.
                     yColumns={compiled && seriesRows.some((s) => s.source) ? null : chartColumns}
                     yHint={compiled && seriesRows.some((s) => s.source)
                       ? "Each input measures the column wired into its workflow node." : ""}
                     hasStore={chartHasStore} timeColumns={chartTimeCols}
                     advanced={(
                       <BandEditor kind={current.kind} value={current.bands}
                                   onChange={(bands) => patch(current.id, { bands })} />
                     )} />
  );

  // The two words a verdict shows. DATA, not dressing: "Empty" / "Full" is
  // what the value's 0 and 1 MEAN, so it sits with the source, and on a card
  // the workflow publishes (a wf_* row, or a copy linked to its node) the
  // Builders' save writes it onto the node (verdictLabels + card-style) the
  // way the bands travel. On a card with no node the words live on the row
  // as state rows -- the server drew COMPLIANT / VIOLATION for every such
  // card and offered nowhere to change it, which is how four hand-made
  // verdict tiles kept the stock words after the canvas said otherwise.
  const verdictFields = current.kind === "verdict" && (() => {
    const states = Array.isArray(current.states) ? current.states : [];
    const linked = compiled || !!current.node;
    const words = verdictLabels(current);
    if (!("ok_label" in words)) {
      return (
        <p className="muted small">
          {states.length} custom states
          {linked ? " - edit them on the workflow node." : "."}
        </p>
      );
    }
    const okRow = states.find((s) => s.tone === "ok");
    const alertRow = states.find((s) => s.tone === "alert");
    // The legacy compliance stat reads 1 ok / 0 alert / -1 empty; every
    // other key reads 0 calm / 1 alerting. Rows written for the wrong
    // convention would show the words the wrong way round.
    const compliance = !states.length && (current.key || "compliance") === "compliance";
    const setWords = (ok, alert) => {
      if (!ok && !alert) {
        patch(current.id, { states: [] });         // back to the stock words
        return;
      }
      const row = (was, label, tone, value) => ({
        value: was ? was.value : value, label, tone,
        ...(was?.color ? { color: was.color } : {}),
      });
      patch(current.id, { states: [
        row(okRow, ok || "COMPLIANT", "ok", compliance ? 1 : 0),
        row(alertRow, alert || "VIOLATION", "alert", compliance ? 0 : 1),
        ...(compliance && !states.some((s) => s.tone === "empty")
          ? [{ value: -1, label: "NO OBJECT", tone: "empty" }] : []),
        ...states.filter((s) => s.tone === "empty"),
      ] });
    };
    return (
      <Field label="Labels"
             hint={linked
               ? "Normal / alert. Saved onto the workflow node too, so the canvas says the same words."
               : "Normal / alert - what the card says at 0 and at 1. Blank both for the stock words."}>
        <div className="rail-row">
          <input className="input" placeholder="COMPLIANT" value={words.ok_label}
                 onChange={(e) => setWords(e.target.value, words.alert_label)} />
          <input className="input" placeholder="VIOLATION" value={words.alert_label}
                 onChange={(e) => setWords(words.ok_label, e.target.value)} />
        </div>
      </Field>
    );
  })();

  // A card made by hand that reads the very value a named workflow card
  // reads. It looks like that card and is not it: every canvas edit refreshes
  // a LINKED copy's body (labels, states, filters) and reaches this one
  // never. Linking is one field on the row, so it is offered here rather
  // than as an instruction to delete the card and drag it again. Same kind
  // only -- a tile linked to a chart node would become a chart on the next
  // recompile, which is not what "link" says.
  const twin = !compiled && !current.node && current.source === "stat" && current.key
    ? (meta.catalog || []).find((c) => c.node && c.key === current.key
                                       && c.kind === current.kind)
    : null;
  const linkHint = twin && (
    <p className="muted small c-warn">
      Not linked to the workflow card <strong>{twin.label}</strong>, which
      reads the same value - edits on its node never reach this card.{" "}
      <button type="button" className="btn btn-sm"
              onClick={() => patch(current.id, {
                node: twin.node,
                ...(Array.isArray(twin.states) && twin.states.length
                  ? { states: twin.states.map((s) => ({ ...s })) } : {}),
              })}>
        Link to {twin.label}
      </button>
    </p>
  );

  // A NEW chart, or "Change chart type": the picker instead of the panel.
  if (pickingType) {
    return (
      <ChartTypePicker key={current.id}
                       value={isFreshChart(current.id) ? "" : chartTypeId}
                       initial={chartTypeId} kind={current.kind}
                       model={chartState ? chartState.model : null}
                       fresh={isFreshChart(current.id)} onContinue={applyChartType}
                       onCancel={isFreshChart(current.id) ? null : () => setTypePickFor(null)} />
    );
  }
  // Cards whose content has a Placement (Alignment + Position), shown in
  // Appearance: the three stat kinds and the Text card.
  const placed = STAT_LAYOUT_KINDS.includes(current.kind) || current.kind === "text_card";
  return (
    <>
      {chartTypeId && (
        <ChartTypeStrip typeId={chartTypeId} onChange={() => setTypePickFor(current.id)} />
      )}
      {compiled && (
        <p className="muted small" style={{ marginTop: 0 }}>
          Built by the Workflow. What it works out, its period and its shape
          are the same fields as on its canvas node and are saved back onto
          that node, so both sides agree - the wires into the node decide
          where it reads from. How it looks and where it sits are set here.
        </p>
      )}

      <Section title="Card">
        {/* A Text or Image card draws no title unless "Show title" below is
            turned on: its `title` is first the name it is listed by here. */}
        {STATIC_KINDS.includes(current.kind) ? (
          <Field label="Name in the Builder"
                 hint="Tells your cards apart while arranging the page. Shown on the card only with Show title on.">
            <input className="input" value={current.title} aria-label="Name in the Builder"
                   onChange={(e) => patch(current.id, { title: e.target.value })} />
          </Field>
        ) : (
        <Field label="Title">
          <input className="input" value={current.title}
                 onChange={(e) => patch(current.id, { title: e.target.value })} />
        </Field>
        )}
        {/* Straight under the Title, because that is where it reads on the
            card: the caption is the small line beneath the value, and the two
            are written together. */}
        {current.kind === "tile" && (
          <Field label="Caption" hint="The small line under the value. Blank shows nothing.">
            <input className="input" value={current.sub || ""}
                   onChange={(e) => patch(current.id, { sub: e.target.value })} />
          </Field>
        )}
        {/* "Show as", with the node's own names for the shapes. A compiled
            card offers exactly what its node can draw -- a Chart's shapes or
            a Card's -- because that is where the choice is saved. */}
        {(() => {
          const shapes = compiled
            ? (isChartKind(current.kind) ? CHART_SHAPES : CARD_SHAPES)
            : (meta.kinds || ["tile", "line", "bar", "area", "donut", "pie", "gauge", "heatmap", "table", "timeline"])
              // A button is not a shape a reading can take: it only
              // exists as an Inject dragged in from the workflow, so
              // it is offered to that card alone and never turns a
              // tile into a dead button. Same for the other
              // controls: each is only ever itself.
              .filter((k) => (SELF_ONLY_KINDS.includes(k)
                ? k === current.kind : !SELF_ONLY_KINDS.includes(current.kind)))
              .map((k) => [k, shapeLabel(k)]);
          // A CHART: its own family here (every field carries over); another
          // family is "Change chart type". A hand-made chart may still turn
          // into a card shape, listed apart as "Not a chart".
          return chartTypeId ? (
            <ChartShowAs typeId={chartTypeId} kind={current.kind} onType={applyChartType}
                         onKind={(k) => patch(current.id, { kind: k })}
                         otherShapes={shapes.filter(([k]) => !isChartKind(k))} />
          ) : (
            <ShapeField value={current.kind}
                        onChange={(v) => patch(current.id, { kind: v })} shapes={shapes} />
          );
        })()}
      </Section>

      {/* A Text / Image card reads nothing: its own words or picture instead
          of a Data section (staticCardFields.jsx). */}
      {STATIC_KINDS.includes(current.kind) ? (
        <Section title={current.kind === "text_card" ? "Words" : "Picture"}>
          {current.kind === "text_card"
            ? <TextCardFields current={current} patch={patch} />
            : <ImageCardFields current={current} patch={patch} />}
        </Section>
      ) : (
      <Section title="Data">
        {/* A control reads no key: what it shows is a setting, said here. */}
        {CONTROL_KINDS.includes(current.kind) && current.kind !== "button" ? (
          <p className="muted small" style={{ margin: 0 }}>
            {current.kind === "ai_classes"
              ? "Shows the classes chosen for this system's AI model, the same "
                + "choice as on the AI Models page. Changing it here changes it there."
              : "Lists the named Class Filters of this system's workflow. Name them "
                + "on the Workflow page; unticked ones are paused."}
          </p>
        ) : (<>
        <Field label="Source"
               hint={compiled
                 ? "Decided by what is wired into its workflow node."
                 : (SOURCES.find(([v]) => v === current.source) || [])[2]
                   || "Where this card's numbers come from."}>
          {/* A compiled card's source is the wiring on the canvas: a choice
              here could not be saved anywhere, so it is shown, not offered. */}
          <select className="input" value={current.source} disabled={compiled}
                  onChange={(e) => patch(current.id, { source: e.target.value })}>
            {/* Plain words (cardFields.SOURCES); the stored values are
                the server's and unchanged. A stored source the list does not
                name (a data table, SQL) still shows as itself. */}
            {SOURCES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            {!SOURCES.some(([v]) => v === current.source) && (
              <option value={current.source}>{current.source}</option>
            )}
          </select>
          {/* The chosen source in one plain line, under the choice. */}
          {(SOURCES.find(([v]) => v === current.source) || [])[2] && (
            <span className="muted small">
              {(SOURCES.find(([v]) => v === current.source) || [])[2]}
            </span>
          )}
        </Field>
        {chartBuilderFields}
        {datasetFields}
        {cameraField}
        {current.kind === "snap_gallery" && (
          <Field label="Snaps to page through" hint="1 to 20, newest first.">
            <input className="input" type="number" min={1} max={20}
                   value={current.count ?? 10}
                   onChange={(e) => patch(current.id, {
                     count: Math.max(1, Math.min(20, Number(e.target.value) || 10)) })} />
          </Field>
        )}
        {keyFields}
        {queryFields}
        {cardRangeFields}
        {statusFields}
        {verdictFields}
        {linkHint}
        {current.kind === "table" && (
          <RowsField value={current.limit}
                     onChange={(v) => patch(current.id, { limit: v })} />
        )}
        {/* The word after the number, as the node's own panel offers it. */}
        {UNIT_KINDS.includes(current.kind) && (
          <UnitField value={current.unit}
                     onChange={(v) => patch(current.id, { unit: v })} />
        )}
        {/* The THRESHOLDS sit with the data, not the dressing: a limit is a
            statement about the number the card reads ("above 5 is a
            problem"), which is why it is the one knob the workflow node also
            edits -- the node owns the data, and this is data. The colour each
            band paints is dressing, and is under Appearance (BandColours). */}
        {!chartSeries && (
          <BandEditor kind={current.kind} value={current.bands}
                      onChange={(bands) => patch(current.id, { bands })} />
        )}
        </>)}
      </Section>
      )}

      {/* FOLDED by default, and that is the point of the split: a card is
          bound far more often than it is dressed, and the dressing used to
          sit between the title and the data. Everything in here is the
          Builders' alone: the workflow node stores it (a wf_* card is
          rebuilt from its node on every compile) but cannot set it -- the
          canvas save keeps whatever look the node already holds. */}
      <Section title="Appearance" open={false}
               count={set(current.template, current.color, current.bg,
                          current.label_size, current.value_size,
                          current.anim, current.by_day,
                          typeof current.show_title === "boolean"
                            && current.show_title !== titleShown(current.kind) ? "x" : "",
                          placed && current.align
                            && current.align !== statLayout(current.kind).align ? "x" : "",
                          placed && current.valign
                            && current.valign !== statLayout(current.kind).valign ? "x" : "",
                          seriesRows.some((r) => r.color) || "")}>
        {/* Whether the card draws its title: how it LOOKS, so it is set here
            and only here (Tok Aba, 2026-10-14). On a compiled card it is
            written back to its node with the rest of the look, where it is
            stored, compiled and kept as before. */}
        <ShowTitleField kind={current.kind} value={current.show_title}
                        onChange={(on) => patch(current.id, { show_title: on })} />
        {/* WHERE the stat (tile, status, verdict) or the Text card's words
            sit -- Alignment, then Position: how the card LOOKS, so here with
            Show title and only here (Tok Aba, 2026-10-14). On a compiled
            card written back to its node with the rest of the look, where
            it is stored and compiled as before. */}
        {placed && (
          <StatLayoutFields kind={current.kind} align={current.align} valign={current.valign}
                            onChange={(pt) => patch(current.id, pt)} />
        )}
        {/* `kind` is WHAT it draws; `template` is how it is dressed. Offered
            per kind so the list is only ever the treatments that do something
            to THIS widget -- a "dashed" option on a tile is a choice that
            silently changes nothing. */}
        <Field label="Template" hint="How this widget is styled.">
          <select className="input" value={current.template || "plain"}
                  onChange={(e) => patch(current.id, { template: e.target.value })}>
            {(TEMPLATES_FOR[current.kind] || TEMPLATES_FOR._default)
              .map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </Field>
        {/* Beside the template, not fifteen fields below it: the template is
            what decides whether either colour does anything. */}
        <ColourField label="Accent colour" hint="Blank = brand accent."
                     value={current.color} fallback="#04bade"
                     resetTitle="Reset to brand accent"
                     onChange={(v) => patch(current.id, { color: v })} />
        {/* Distinct from the accent above: that one is what a chart's stroke
            or a tile's accent/outline/soft treatment reads. This is the CARD's
            own background, painted regardless of which template it uses. */}
        <ColourField label="Card colour" hint="Blank = let the template decide."
                     value={current.bg} fallback="#04bade"
                     resetTitle="Reset to template default"
                     onChange={(v) => patch(current.id, { bg: v })} />
        {/* A CHART's colours: each input's, and each threshold band's.
            Here and only here (owner, 2026-10-12) -- the workflow node shows
            a hint instead, and Data keeps the inputs and the limits these
            colour. Both write the same stored series / bands Data does. */}
        {chartSeries && (
          <SeriesColours series={seriesRows}
                         onChange={(next) => patch(current.id, seriesPatch(current, next))} />
        )}
        <BandColours kind={current.kind} value={current.bands}
                     title={chartSeries && seriesRows.some((r) => r.bands && r.bands.at?.length)
                       ? "Threshold colours - shared" : "Threshold colours"}
                     onChange={(bands) => patch(current.id, { bands })} />
        {/* ...and each camera's own, headed by its name: only the inputs
            that have thresholds of their own. */}
        {chartSeries && seriesRows.map((r, i) => (r.bands && r.bands.at?.length ? (
          <BandColours key={r.id} kind={current.kind} value={r.bands}
                       title={`Threshold colours - ${r.label || `Series ${i + 1}`}`}
                       ariaPrefix={`${r.label || `Series ${i + 1}`} `}
                       onChange={(bands) => patch(current.id, seriesPatch(current,
                         seriesRows.map((x) => (x.id === r.id ? { ...x, bands } : x))))} />
        ) : null))}
        {chartSeries && (
          <ChartLookFields current={current} patch={patch} model={chartState.model} />
        )}
        <TextSizePicker current={current} patch={patch} />
        <AnimationPicker kind={current.kind} value={current.anim}
                         replay={current.anim_replay}
                         onChange={(fields) => patch(current.id, fields)} />
        <DayColourToggle kind={current.kind} value={current.by_day}
                         series={seriesRows.length || 1}
                         onChange={(fields) => patch(current.id, fields)} />
      </Section>

      <Section title="Layout" open={false}
               count={set(current.capability)}>
        <Field label="Width" hint="How many of the four columns it spans.">
          <select className="input" value={current.span}
                  onChange={(e) => patch(current.id, { span: Number(e.target.value) })}>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n} of 4</option>)}
          </select>
        </Field>
        {/* Front / back where cards overlap: a card's layer is part of
            where it sits on the page, like its grid cell below. */}
        {current.grid?.w && siblings ? (
          <ArrangeFields current={current} siblings={siblings} patch={patch} />
        ) : null}
        {current.grid?.w ? (
          <Field label="Grid cell" hint="X · Y · width · height. Or drag on the canvas.">
            <div className="rail-row">
              {["x", "y", "w", "h"].map((k) => (
                <input key={k} className="input" type="number" title={k}
                       style={{ flex: "1 1 40px" }}
                       value={current.grid[k]}
                       onChange={(e) => patch(current.id, {
                         grid: { ...current.grid, [k]: Number(e.target.value) },
                       })} />
              ))}
            </div>
          </Field>
        ) : null}
        <Field label="Only when capability" hint="Blank = always shown.">
          <select className="input" value={current.capability}
                  onChange={(e) => patch(current.id, { capability: e.target.value })}>
            <option value="">always show</option>
            {(meta.capabilities || []).map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
      </Section>
    </>
  );
}

// Key picker - a combobox over every published key: ONE control that opens a
// list with a search box INSIDE it. A system with a dozen zones and workflow
// variables publishes far more keys than a plain dropdown scans comfortably;
// typing part of the VARIABLE NAME (the friendly label or the raw fn_* key)
// narrows the open list live. Native <select> cannot host an input, hence the
// hand-rolled popover.
export function KeyPicker({ value, keys, keyLabel, onChange }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  // Close on a click anywhere else and on Escape - the standard dropdown
  // contract; without it a stale open list floats over the next field.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);
  // Focus the search the moment the list opens - the whole point is typing.
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);

  const needle = q.trim().toLowerCase();
  const shown = needle
    ? keys.filter((k) => k.toLowerCase().includes(needle)
                         || keyLabel(k).toLowerCase().includes(needle))
    : keys;

  function pick(k) {
    onChange(k);
    setOpen(false);
    setQ("");
  }

  return (
    <div ref={boxRef} style={{ position: "relative" }}>
      <button type="button" className="input"
              onClick={() => setOpen((o) => !o)}
              style={{ display: "flex", alignItems: "center", gap: 6,
                       width: "100%", cursor: "pointer", textAlign: "left" }}>
        <span style={{ flex: 1, minWidth: 0, overflow: "hidden",
                       textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {value ? keyLabel(value) : "- pick a key -"}
        </span>
        <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
      </button>
      {open && (
        <div style={{ position: "absolute", zIndex: 40, top: "100%", left: 0,
                      right: 0, marginTop: 4, borderRadius: 8,
                      border: "1px solid rgba(128,128,128,.35)",
                      background: "var(--surface, #1c1c1e)",
                      boxShadow: "0 8px 24px rgba(0,0,0,.35)", padding: 6 }}>
          <input ref={inputRef} className="input" placeholder="Search variables…"
                 value={q} onChange={(e) => setQ(e.target.value)}
                 style={{ marginBottom: 6 }} />
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            {!!value && (
              <button type="button" className="btn btn-sm"
                      onClick={() => pick("")}
                      style={{ display: "block", width: "100%",
                               textAlign: "left", marginBottom: 2 }}>
                - clear -
              </button>
            )}
            {shown.map((k) => (
              <button type="button" key={k} title={k}
                      onClick={() => pick(k)}
                      className={"btn btn-sm" + (k === value ? " btn-accent" : "")}
                      style={{ display: "block", width: "100%",
                               textAlign: "left", marginBottom: 2,
                               overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>
                {keyLabel(k)}
              </button>
            ))}
            {!shown.length && (
              <p className="muted small" style={{ padding: 6, margin: 0 }}>
                Nothing matches - check the Workflow's variable names.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** The patch a series edit writes: the rows, and the card source that has to
 *  agree with them.
 *
 *  The card's source is the fallback a row with NO source reads, so it may
 *  only follow the rows while no row is relying on it - moving it under an
 *  inheriting row would silently repoint that row at something else.
 */
export function seriesPatch(w, next) {
  const first = next.find((s) => s.source);
  const follow = !next.some((s) => !s.source) && first && first.source !== w.source;
  return { series: next, ...(follow ? { source: first.source } : {}) };
}

//: What each source IS, in the words the alignment note reads in.
const SOURCE_WORDS = {
  history: "recorded samples of a live value",
  stat: "live values",
  query: "recorded events",
  dataset: "a data table",
  "": "this card's own source",
};

/** What the shared x axis will BE, said in the editor.
 *
 *  Mixing an hourly event count with a sampled live value is a perfectly
 *  reasonable chart, and it is also the one that looks broken: the counts
 *  land on the widget's buckets rather than their own, so a line steps where
 *  the operator expected it to curve. Discovering that from the picture means
 *  distrusting the data; reading it here means knowing what was asked for.
 */
export function SeriesAlignment({ widget, series }) {
  if (!series || series.length < 2) return null;
  const isTs = ["line", "area", "spline"].includes(widget.kind);
  const timed = isTs || series.some(
    (s) => s.source === "history" || (s.query && s.query.bucket));
  const names = [...new Set(series.map((s) => SOURCE_WORDS[s.source || ""]
                                              || SOURCE_WORDS[""]))];
  const iv = widget.interval || "";
  const range = (RANGE_LABELS[widget.range || "24h"] || widget.range || "24h")
    .toLowerCase();
  return (
    <p className="muted small" style={{ marginTop: -2 }}>
      {timed
        ? `All ${series.length} series share one time axis: `
          + `${iv ? `${iv} buckets` : "buckets sized to fit"} across ${range}.`
        : `All ${series.length} series share one set of labels, drawn side by side.`}
      {names.length > 1 && (
        ` They come from ${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
        + `, so they are lined up onto that axis - a count recorded per hour is `
        + "resampled onto these buckets rather than drawn at its own spacing.")}
      {" A point a series has no value for is left as a gap, not drawn as zero."}
    </p>
  );
}

/** Each input's COLOUR, in the Builders' Appearance section.
 *
 *  Split off the series rows in Data because a colour only changes how the
 *  chart LOOKS (owner, 2026-10-12: chart colours under Appearance, which only
 *  the Builders have). It writes the same series list SeriesEditor writes --
 *  one row's `color` changed, everything else as it was -- through the same
 *  onChange, so the two sections edit one stored list and cannot diverge.
 *  The swatch shows the colour the CHART will use: unset falls back by
 *  position, exactly as charts.js does.
 */
export function SeriesColours({ series, onChange }) {
  if (!series.length) return null;
  const patchRow = (i, fields) =>
    onChange(series.map((s, j) => (j === i ? { ...s, ...fields } : s)));
  return (
    // A block wearing the field classes: several inputs, so not one <label>.
    <div className="ui-field">
      <span className="ui-field-label">Series colours</span>
      <div style={{ display: "grid", gap: 6 }}>
        {series.map((s, i) => (
          <div key={s.id || i}>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="color" value={s.color || seriesColorAt(i)}
                     title="Series colour" aria-label={`Colour for series ${i + 1}`}
                     style={{ width: 30, height: 26, padding: 2, flexShrink: 0,
                              border: "1px solid rgba(128,128,128,.35)", borderRadius: 6,
                              background: "transparent", cursor: "pointer" }}
                     onChange={(e) => patchRow(i, { color: e.target.value })} />
              <span className="small" style={{ flex: 1, minWidth: 0, overflow: "hidden",
                                               textOverflow: "ellipsis",
                                               whiteSpace: "nowrap" }}>
                {s.label || `Series ${i + 1}`}
              </span>
              {s.color && (
                <button type="button" className="btn btn-sm" style={{ flexShrink: 0 }}
                        title="Back to the palette colour for this position"
                        aria-label={`Reset the colour of series ${i + 1}`}
                        onClick={() => patchRow(i, { color: "" })}>↺</button>
              )}
            </div>
            {!s.color && (
              <span className="muted small">
                Palette colour for input {i + 1} - pick one to pin it.
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Series editor - how a chart is configured, full stop.
 *
 * Every chart kind opens with one row in it, prefilled from whatever the card
 * already read, and a button to add the next. It used to be an opt-in behind
 * a button reading "Use series (multi-key)", bound to stat keys only: an
 * operator who wanted a second input on a chart was looking at a single "Data
 * key" field and concluding, correctly, that a chart holds one thing.
 *
 * A row picks WHERE IT READS FROM first, because that decides which questions
 * it can be asked - a live value needs a key, a recorded event needs a
 * question, a table column needs a table. Then its label, which is what makes
 * several series readable as several. Its COLOUR is look and lives in the
 * Appearance section (SeriesColours, below; owner, 2026-10-12) - the small
 * square on each row is only the legend, so a row can be matched to its line.
 */
export function SeriesEditor({ series, widget, meta, keyLabel = (k) => k,
                               warnings = {}, onChange, chartState = null,
                               onOverride = null }) {
  const kind = widget.kind;
  const sources = (meta || {}).sources || {};
  const hv = (meta || {}).history || {};
  const aggs = (hv.aggs || []).length ? hv.aggs
    : ["avg", "sum", "min", "max", "last", "count"];
  const tables = datasetChoices(meta);
  const offered = SOURCES_FOR_KIND[kind] || SERIES_SOURCES.map(([v]) => v);
  const isTs = ["line", "area", "spline"].includes(kind);
  // The card's own source, for the row that inherits it. Offered as a choice
  // only while a row actually holds it: `bucket` and `events` have no per-row
  // shape, so it is a source you can keep, not one you can pick.
  const inheritWord = INHERIT_LABELS[widget.source] || "";
  const anyInherits = series.some((s) => !s.source);
  // A card the Workflow compiles: its inputs are the wires into its node and
  // are rewritten on every apply, but the LABEL and the COLOUR of each input
  // are saved back onto that node (seriesStyle), so they survive. Said here
  // because the difference decides where to go to change a thing.
  const compiled = String(widget.id || "").startsWith("wf_");

  const patchRow = (i, fields) =>
    onChange(series.map((s, j) => (j === i ? { ...s, ...fields } : s)));

  function addRow() {
    // The new row reads where the last one reads: two series on one chart are
    // usually two of the same thing, and the alternative - always landing on
    // the first source in the list - makes the common case a re-pick.
    const last = series[series.length - 1];
    const src = offered.includes(last?.source) ? last.source : offered[0];
    onChange([...series, {
      id: nextSeriesId(), label: "", color: "", source: src,
      agg: src === "history" || src === "dataset" ? "avg" : "last",
      ...(src === "query" ? { query: {} } : {}),
      // A dataset row's question lives in its query, the same one the card's
      // own dataset editor writes: "total rows" is the answer that needs no
      // column chosen, so it is the one a fresh row can already answer.
      ...(src === "dataset"
        ? { query: { agg: "count" },
            ...(tables.length
              ? { dataset: tables[0].name, columns: tables[0].columns || [] }
              : {}) }
        : {}),
      key: "",
    }]);
  }

  // Changing a row's source keeps its LABEL and its COLOUR and drops only the
  // fields the old source owned: an operator repointing "Zone A count" at the
  // event store named and coloured that line already.
  function setSource(i, src) {
    const s = series[i];
    const keep = { id: s.id, label: s.label || "", color: s.color || "",
                   source: src,
                   agg: src === "history" ? (s.agg || "avg") : "last" };
    if (src === "history" || src === "stat") keep.key = s.key || "";
    if (src === "query") keep.query = s.query || {};
    if (src === "dataset") {
      keep.dataset = s.dataset || tables[0]?.name || "";
      keep.field = s.field || "";
      keep.ts_field = s.ts_field || "";
      // The declaration the server checks those two names against.
      keep.columns = s.columns
        || (tables.find((t) => t.name === keep.dataset) || {}).columns || [];
      keep.query = { agg: "count", ...(s.query || {}) };
      // The fold is the server's to derive from that question.
      keep.agg = "";
    }
    onChange(series.map((row, j) => (j === i ? keep : row)));
  }

  function rowFields(s, i) {
    const src = s.source || "";
    if (chartState) return sourceFields(s, i);
    if (src === "history" || src === "stat") {
      return (
        <>
          <Field label="Value"
                 hint="Every value this system publishes. The same list the single-key cards bind to.">
            <KeyPicker value={s.key || ""} keys={sources.stat || []}
                       keyLabel={keyLabel}
                       onChange={(k) => patchRow(i, { key: k })} />
          </Field>
          {src === "history" && (
            <Field label={CARD_FIELDS.fold.label}
                   hint={CARD_FIELDS.fold.hint}>
              {/* The node's own words for the folds (cardFields.SERIES_FOLDS). */}
              <select className="input" value={s.agg || "avg"}
                      onChange={(e) => patchRow(i, { agg: e.target.value })}>
                {SERIES_FOLDS.filter(([a]) => aggs.includes(a)).map(([a, label]) => (
                  <option key={a} value={a}>{label}</option>
                ))}
              </select>
            </Field>
          )}
        </>
      );
    }
    if (src === "query") {
      return (
        <QueryFields query={s.query} meta={meta} grouped
                     onChange={(q) => patchRow(i, { query: q })} />
      );
    }
    if (src === "dataset") {
      const table = tables.find((t) => t.name === s.dataset);
      // The catalogue's declaration, or the one the row is already carrying:
      // a table the catalogue has stopped publishing must not leave the row
      // that reads it with no columns to name.
      const cols = (table && table.columns) || s.columns || [];
      // The question asked of the STORE (total rows, newest value, distinct
      // values) lives in the row's query, exactly as the card's own dataset
      // editor writes it. The row's fold - how several rows in one bucket
      // become one point - is derived from it server-side, so it is not a
      // second control saying almost the same thing.
      const q = s.query || {};
      const setQ = (partial) => patchRow(i, { query: { ...q, ...partial } });
      const agg = q.agg || "count";
      // Only a number can be totalled, averaged or ranked; unique/changes
      // count occurrences, so they take any column. Same rule as the card's
      // own dataset editor, because it is the same question.
      const valueCols = cols.filter(
        (c) => ["unique", "changes"].includes(agg) || c.type === "number");
      const fieldType = ((cols.find((c) => c.name === s.field)) || {}).type || "number";
      return (
        <>
          <Field label="Data table">
            {tables.length ? (
              // The table's COLUMNS travel on the row, not only its name: the
              // server checks the column and the timestamp column against the
              // declaration the row carries, so a row that names a table and
              // no columns has every column it names thrown away.
              <select className="input" value={s.dataset || ""}
                      onChange={(e) => patchRow(i, {
                        dataset: e.target.value, field: "", ts_field: "",
                        columns: (tables.find((t) => t.name === e.target.value)
                                  || {}).columns || [],
                      })}>
                <option value="">- choose a table -</option>
                {tables.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
              </select>
            ) : (
              <input className="input" placeholder="table name" value={s.dataset || ""}
                     onChange={(e) => patchRow(i, { dataset: e.target.value })} />
            )}
          </Field>
          {/* The SAME row the workflow node draws for this input
              (cardFields.jsx): Work out, Of which column, One point per,
              Timestamp column, Period. On a compiled chart the answer is
              saved onto the node's series entry as a fold (seriesStyle), so
              only the questions a fold can say are offered, the column is
              the wire's, and a word column can only be counted -- exactly
              what the node's row offers. The row's own FOLD is cleared as
              Work out changes: the server derives it from the question, and
              a fold left behind would quietly override it. */}
          {compiled ? (
            <InputWorkOutField value={agg} numeric={fieldType === "number"}
                               onChange={(v) => patchRow(i, { query: { ...q, agg: v }, agg: "" })} />
          ) : (
            <WorkOutField value={agg}
                          onChange={(v) => patchRow(i, { query: { ...q, agg: v }, agg: "" })} />
          )}
          {agg !== "count" && (
            <ColumnField value={s.field || ""} disabled={compiled}
                         columns={compiled ? [] : valueCols}
                         onChange={(v) => patchRow(i, { field: v })} />
          )}
          {/* No time buckets here, unlike the card's own dataset editor: the
              buckets are the CHART's, shared by every input. */}
          <PointPerField value={q.group_by || ""} columns={cols} timed={isTs}
                         onChange={(v) => setQ({ group_by: v })} />
          {q.group_by && q.group_by === s.field && (
            <p className="muted small c-warn">
              Grouping by the same column you are working out gives one point
              per value of it - a 0/1 column gives you exactly two. Group by
              another column, or by nothing.
            </p>
          )}
          {isTs && (
            <TimestampField value={s.ts_field || ""} columns={cols}
                            onChange={(v) => patchRow(i, { ts_field: v })} />
          )}
          <PeriodField value={q.range || ""} hint="Which rows this input includes."
                       onChange={(v) => setQ({ range: v })} />
        </>
      );
    }
    // No source: the card's own. A bucket still binds a key here; a table or
    // a SQL statement is edited in the card's own controls under this list.
    const ownKeys = sources[widget.source === "history" ? "stat" : widget.source];
    return Array.isArray(ownKeys) && ownKeys.length ? (
      <Field label={CARD_FIELDS.key.label} hint={CARD_FIELDS.key.hint}>
        <KeyPicker value={s.key || ""} keys={ownKeys} keyLabel={keyLabel}
                   onChange={(k) => patchRow(i, { key: k })} />
      </Field>
    ) : (
      <p className="muted small" style={{ margin: "2px 0" }}>
        Reads {inheritWord || "this card's own source"} - set it below this list.
      </p>
    );
  }

  // UNDER THE CHART BUILDER a row says only WHERE it reads -- the value, the
  // event filter, the table -- because X, Y and the date range are the
  // chart's (ChartAxisFields), shared by every row; what a row may change of
  // its own is in its collapsed Override (InputRowControls).
  function sourceFields(s, i) {
    const src = s.source || "";
    if (src === "history" || src === "stat") {
      return (
        <Field label="Value"
               hint="Every value this system publishes. The same list the single-key cards bind to.">
          <KeyPicker value={s.key || ""} keys={sources.stat || []}
                     keyLabel={keyLabel}
                     onChange={(k) => patchRow(i, { key: k })} />
        </Field>
      );
    }
    if (src === "query") {
      return (
        <QueryFields query={s.query} meta={meta} chartBuilder
                     onChange={(q) => patchRow(i, { query: q })} />
      );
    }
    if (src === "dataset") {
      return (
        <Field label="Data table">
          {tables.length ? (
            <select className="input" value={s.dataset || ""} disabled={compiled}
                    onChange={(e) => patchRow(i, {
                      dataset: e.target.value, field: "", ts_field: "",
                      columns: (tables.find((t) => t.name === e.target.value)
                                || {}).columns || [],
                    })}>
              <option value="">- choose a table -</option>
              {tables.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
              {s.dataset && !tables.some((t) => t.name === s.dataset) && (
                <option value={s.dataset}>{s.dataset}</option>
              )}
            </select>
          ) : (
            <input className="input" placeholder="table name" value={s.dataset || ""}
                   disabled={compiled}
                   onChange={(e) => patchRow(i, { dataset: e.target.value })} />
          )}
        </Field>
      );
    }
    const ownKeys = sources[widget.source === "history" ? "stat" : widget.source];
    return Array.isArray(ownKeys) && ownKeys.length ? (
      <Field label={CARD_FIELDS.key.label} hint={CARD_FIELDS.key.hint}>
        <KeyPicker value={s.key || ""} keys={ownKeys} keyLabel={keyLabel}
                   onChange={(k) => patchRow(i, { key: k })} />
      </Field>
    ) : (
      <p className="muted small" style={{ margin: "2px 0" }}>
        Reads {inheritWord || "this card's own source"}.
      </p>
    );
  }

  // A plain block wearing the Field classes rather than <Field> itself: every
  // ROW contains Fields, and Field renders a <label> - a label inside a label
  // is invalid, and clicking the inner one lands on the outer one's control.
  return (
    <div className="ui-field">
      <span className="ui-field-label">Inputs</span>
      <p className="ui-field-hint" style={{ margin: 0 }}>
        {compiled
          ? "The inputs are the wires into this card's workflow node, and a "
            + "recompile rewrites them. The name of each one is saved back "
            + "onto that node, so it stays. Colours are under Appearance."
          : "What this chart draws. One row per input, each with its own "
            + "source and label. Colours are under Appearance."}
      </p>
      <div style={{ display: "grid", gap: 8 }}>
        {series.map((s, i) => {
          // The swatch shows the colour the CHART will use, which for an
          // unset colour is the palette entry for this position - the same
          // fallback charts.js makes, so the editor never shows a colour the
          // picture will not draw.
          const swatch = s.color || seriesColorAt(i);
          return (
            <div key={s.id || i}
                 style={{ border: "1px solid rgba(128,128,128,.28)", borderRadius: 8,
                          padding: 8, overflow: "hidden" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center",
                            marginBottom: 6 }}>
                <i aria-hidden style={{ width: 12, height: 12, borderRadius: 3,
                                        background: swatch, flexShrink: 0,
                                        border: "1px solid rgba(128,128,128,.45)" }} />
                <strong className="small" style={{ flex: 1, minWidth: 0 }}>
                  {s.label || `Series ${i + 1}`}
                </strong>
                {/* The last row cannot be removed: a chart with no inputs is
                    a card that draws nothing and says why nowhere. */}
                <button type="button" className="btn btn-sm btn-ghost"
                        title={series.length > 1 ? "Remove this input"
                                                 : "A chart needs at least one input"}
                        disabled={series.length < 2} style={{ flexShrink: 0 }}
                        onClick={() => onChange(series.filter((_, j) => j !== i))}>
                  ×
                </button>
              </div>
              <Field label="Where from"
                     hint="What this input reads. It decides which questions the rest of this row can ask.">
                <select className="input" value={s.source || ""}
                        onChange={(e) => setSource(i, e.target.value)}>
                  {/* Kept, not offered: a bucket or an events card has no
                      per-row shape, so the row that already inherits it can
                      stay there and no new row can be put there. */}
                  {!s.source && (
                    <option value="">{inheritWord || "this card's own source"}</option>
                  )}
                  {SERIES_SOURCES.filter(([v]) => offered.includes(v))
                    .map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                </select>
              </Field>
              {rowFields(s, i)}
              {/* The preview's verdict on THIS input, under the fields that
                  answer it. It used to arrive as a page toast naming "widget
                  4", which told an operator mid-edit that something was
                  wrong and nothing about where. */}
              {warnings[s.id] && (
                <p className="small c-warn" style={{ margin: "6px 0 0" }}>
                  {warnings[s.id]}
                </p>
              )}
              {chartState ? (
                // Its name, whether it is drawn, and its collapsed override:
                // the same row the workflow node draws (chartFields.jsx).
                <InputRowControls index={i + 1} label={s.label}
                                  placeholder={`Series ${i + 1}`}
                                  show={s.show !== false}
                                  override={(chartState.overrides || {})[s.id] || {}}
                                  chartCalc={chartState.model.y_agg}
                                  columns={s.source === "dataset" && !compiled
                                    ? (s.columns || (tables.find((t) => t.name === s.dataset)
                                                     || {}).columns || []) : null}
                                  onLabel={(v) => patchRow(i, { label: v })}
                                  onShow={(on) => onOverride(s.id, {
                                    ...((chartState.overrides || {})[s.id] || {}),
                                    show: on ? undefined : false })}
                                  onOverride={(next) => onOverride(s.id, {
                                    ...next,
                                    ...(s.show === false ? { show: false } : {}) })}>
                  <SeriesBands kind={widget.kind} name={s.label || `Series ${i + 1}`}
                               value={s.bands} shared={widget.bands}
                               onChange={(b) => onChange(series.map((row, j) => {
                                 if (j !== i) return row;
                                 const next = { ...row };
                                 if (b) next.bands = b; else delete next.bands;
                                 return next;
                               }))} />
                </InputRowControls>
              ) : (
                <div style={{ display: "flex", gap: 6, marginTop: 6,
                              alignItems: "center" }}>
                  {/* .input ships width:100%; inside a flex row that overflows
                      the rail - override with width:auto + minWidth:0. */}
                  <input className="input" style={{ flex: 1, minWidth: 0, width: "auto" }}
                         placeholder={`label (Series ${i + 1})`} value={s.label || ""}
                         aria-label={`Label for series ${i + 1}`}
                         onChange={(e) => patchRow(i, { label: e.target.value })} />
                </div>
              )}
            </div>
          );
        })}
      </div>
      <SeriesAlignment widget={widget} series={series} />
      <div className="row" style={{ gap: 8, marginTop: 6 }}>
        <button type="button" className="btn btn-sm" onClick={addRow}
                disabled={series.length >= MAX_SERIES || !offered.length}>
          + Add series
        </button>
        {series.length >= MAX_SERIES && (
          <span className="muted small">
            {MAX_SERIES} is the most - past that the palette repeats and two
            series wear one colour.
          </span>
        )}
        {anyInherits && series.length > 1 && (
          <span className="muted small">
            One input still reads {inheritWord || "this card's own source"}.
          </span>
        )}
      </div>
    </div>
  );
}
