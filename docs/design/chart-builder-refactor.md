# Chart builder refactor -- design note

Boboiboy, 2026-10-13. Branch rnd/video-perf, on top of f42c8ebf. Owner brief:
"Refactor the Chart and Graph Builder" (9 sections) plus the rendering
addendum (axis titles, ticks, orientation, data labels, legend, tooltips).

This note is the Phase 0 deliverable: what exists today, the new model, the
old -> new mapping, how saved charts migrate, and every decision taken where
the brief meets data the box already stores.

## 1. Inventory -- what a chart offers today

### 1a. Workflow node inspector (pages/Workflow.jsx, dashboard.graph)

Chart level:

| Control (label)           | Stored field            | Shown when |
|---------------------------|-------------------------|------------|
| Title                     | name                    | always |
| Show as (4 optgroups)     | kind                    | always |
| Source                    | source_mode             | nothing table/Query wired |
| Work out                  | agg                     | one wired column (ColumnLogic), recorded mode |
| Of which column           | field                   | recorded mode |
| One point per             | group_by                | one wired column, recorded mode |
| Group by time             | bucket (step)           | one wired column; several inputs with a store input; line/area/spline rule chart |
| Period                    | range (today/week/month/page/"") | pie/donut/radar; ColumnLogic on non-time kinds |
| Time range                | range (+ range_from/range_to) | line/area/spline/bar |
| Count over                | window (live/follow/today/week/month) | any chart not in recorded mode |
| Unit label                | unit                    | always |
| Threshold rules           | bands.at, band_alert    | CardBands |
| (hint) colours in Builder | -                       | always |

Per input (GraphSeriesRows, one row per wire, s1..s8):

| Control          | Stored field                     | Shown when |
|------------------|----------------------------------|------------|
| (title, sub-line)| -                                | always |
| Each point shows | series[sid].agg (fold)           | rule input on a time shape |
| Work out         | series[sid].agg (fold, shown as question) | table column |
| Of which column  | (the wire, disabled)             | table column, agg != count |
| One point per    | series[sid].query.group_by       | table column |
| Date column      | series[sid].ts_field             | table column on a time shape |
| Period           | series[sid].query.range          | table column |
| Name             | series[sid].label                | always |

That is the repetition in the owner's screenshots: per camera Work out, One
point per, Date column, Period; at chart level Group by time, Count over, Time
range; six controls answer four questions (X, Y, step, window).

### 1b. Builders (builderShared.jsx WidgetInspector + SeriesEditor)

Chart level (widget): Show as, Source, Work out / Of which column / One point
per / Group by time / Period (card-level dataset question), Time range
(range, range_from, range_to), Group by time (interval), Unit label,
Thresholds; Appearance: series colours, band colours, template, sizes.

Per input row: Where from (history/stat/query/dataset), Value (key) or
Data table, Work out, Of which column, One point per, Date column, Period,
label. Query rows: Work out, column, Group by, Group by time, Only events of
type, Period.

### 1c. Server

- workflow.py: chart_axis / apply_series_conf / settle_axis / x_field_window
  turn the node fields into each compiled series entry; card-style write-back
  (CARD_QUESTION_FIELDS, SERIES_QUESTION_FIELDS), keep_builder_edits,
  keep_chart_colours.
- chart_series.py: _bucket_data (calendar/step axis), _time_data (sampler
  grid), _label_data (grouped by a column), _value_data (one value per input).
- timeaxis.py: step grammar, ranges, local edges, 2000-point widening.
- datasets_repo / events_repo aggregate: count, sum, avg, min, max, unique,
  changes (datasets also "latest" via builder).

## 2. The new model

Two categories in "Show as" (owner: purpose, not number of inputs):

- **Charts - compare categories and values**: bar, pie, donut, radar.
- **Time-series graphs - change over time**: line, spline, area.

The card kinds keep their own groups, unchanged and still selectable where
they were: "Single value" (stat tile, gauge, status, verdict) and "Lists,
events and media" (table, timeline, heatmap, live video). The refactor below
applies to the seven chart kinds only. This replaces the 6c301207 grouping
("Charts over time", "Share and compare"); a bar moves from "over time" to
Charts, and may still use Time as its X axis.

Axis layout per kind (chartModel.layoutOf):

| Kind                | Sections shown |
|---------------------|----------------|
| bar                 | X axis (Database column or Time), Y axis |
| line, spline, area  | X axis (Time by default, Database column allowed), Y axis |
| pie, donut          | Category (a column, or "each input is one slice") + Value. No axes |
| radar               | Category / metric (a column, or "each input is one spoke") + Value. No axes |

### 2a. Stored fields (chart level)

Kept flat on the node (and as a `chart` object on a Builder widget), because
the card-style write-back, the stale-tab guard and the OTA rollback all work
field by field. Fields marked NEW did not exist before.

| Field          | Values | Meaning |
|----------------|--------|---------|
| x_type (NEW)   | "time" / "column" | X axis type. Its PRESENCE marks a chart saved under the new model. |
| group_by       | column or "" | X = column: which column ("" = one point per input). Category field on pie/donut/radar. |
| agg            | count, count_nonnull (NEW), sum, avg, min, max; legacy latest/unique/changes still read | Y calculation |
| field          | column or "" | Y column (recorded-data mode; a wired column is its own Y) |
| time_interval (NEW) | hour, day, week, month, custom | the interval preset the operator chose |
| bucket         | timeaxis step ("1h", "1d", "week", "month", "<n>m/h/d", "" automatic) | grouping step when X = Time |
| range, range_from, range_to | timeaxis range keys, "page", "custom" + ISO ends, "" all time | the ONE date range |
| ts_field (NEW at chart level) | "" automatic, "@row" (when the row was saved), or a date column | which datetime column filters and groups |

Per input (data.series[sid] on the node, series[i] on a Builder widget):

| Field         | Meaning |
|---------------|---------|
| label         | series name (unchanged) |
| color         | Builder-only (unchanged, keep_chart_colours) |
| show (NEW)    | false = not drawn, still listed in the editors |
| agg           | Y calculation override ("" = the chart's) |
| field         | Builder rows only: Y column override |
| query.group_by, query.range, ts_field | legacy per-input X / range / date column; read and kept as overrides under the row's collapsed "Override" section, never offered on a fresh row |

Removed from the node and the Builders as separate controls: Period,
Count over (on charts), per-input Work out / One point per / Date column /
Period as always-visible rows, the history "Time step" (`interval`).

### 2b. Time presets (on top of timeaxis.py)

| Interval | Default range | Default step | Option |
|----------|---------------|--------------|--------|
| Hour     | Last 24 hours (24h) | every hour (1h) | - |
| Day      | Past week (7d) | every day (1d) | - |
| Week     | Past week (7d) | every day (1d) | "Each week" -> Past 3 months (90d), step week |
| Month    | Past month (30d) | every day (1d) | "Each calendar month" -> Past year (365d), step month |
| Custom   | Pick dates (custom, start/end) | as stored, else Automatic | the full step list: Automatic, 1/5/30 min, 1/6/12 h, day, week, month, Custom N minutes/hours/days |

Interval and date range are separate settings: choosing an interval writes
its default range once; the "Date range" control below it changes the range
alone (any quick pick, Follow the Analytics date picker, Pick dates, All time
where it is meaningful). The finer steps of f42c8ebf stay reachable through
Custom.

Automatic datetime column when X = Time: the table's declared date column,
else when the row was saved -- the same rule series_ts_field always applied.
"Date column" moves to Advanced (Automatic / when the row was saved / a
date column).

### 2c. Y calculations

Charts offer exactly: Count records (count), Count non-null values
(count_nonnull, NEW), Sum, Average, Minimum, Maximum. A stored legacy value
(latest, unique, changes) is shown "as saved" and still computes as before.
Added the same day (owner): a seventh, "Count Difference Value" (stored as
the old "unique"): COUNT(DISTINCT) of the column per bucket, nulls and blanks
skipped, never added up across buckets (widened steps ask the store for the
widened bucket; the shared grid counts a dataset input from the cell's rows
and asks the event store for cell-wide buckets; the sampler counts different
sampled values per cell, and over the whole window for a pie). With no
column picked on a table chart it counts the X column, and says so.
And an eighth, "Count Value Changes" (stored as the old "changes"): how
many times the column took a new value, in time order. Decided: along time
the look-back runs over the WHOLE ordered series (SQL LAG without a
partition, the sampler sample by sample), so a change at a bucket boundary
counts in the bucket where the new value appears and bucket counts add up
exactly when merged or widened; grouped by a column the look-back stays
inside each group. Ordered by the Date column, else the saved time. Nulls
and blanks are no value (3, null, 3 is no change). The first value in the
window counts. Order of the Y list: Count records, Count non-null values,
Count Difference Value, Count Value Changes, Sum, Average, Minimum,
Maximum.
Single-value cards keep the full Work out list (latest, total different
values, value changes included) -- they are card kinds, not charts.

Behaviour fix: "count" with a column used to run count(column) in SQL (non-
null values) while the label said "total rows" and the in-memory path counted
every row. count is now count(*) in both stores; count(column) is
count_nonnull. Visible only on a column holding nulls.

### 2d. X / Y rules per kind

- bar: X = a database column (one bar per value) or "each input" (one bar
  per camera), or Time (one bar per step); Y = calculation (+ column).
- line/spline/area: X = Time by default; a database column is allowed (kept
  from f42c8ebf: "X may be a database column").
- pie/donut: Category = a column, or each input is a slice; Value = count or
  a calculation of a number column.
- radar: Category / metric = a column, or each input is a spoke; Value as
  pie.

Precedence (kept from f42c8ebf, now explicit): an assigned column -- the old
"One point per <column>", a text column's own values on a category chart, or
a field dragged from a table -- is the selection the X section opens with,
and nothing switches it to a datetime field. Choosing Time is the operator's
act.

### 2e. Date range with a column X (reconciling the brief with f42c8ebf)

The brief: hide unnecessary time configuration when X is a column. f42c8ebf
(owner decision): the date range still filters rows when an X field is set.
Both hold: with X = column the interval and step are hidden; the ONE date
range stays as "Only include rows from" inside the collapsed Advanced
section (shown for inputs that read recorded rows, which always carry a
saved time). Default for a migrated category chart is what it read before
(usually All time).

### 2f. Advanced (collapsed by default)

Date column, Only include rows from (column X), threshold rules, a legacy
Count over that is not "live" on a rule-fed time chart (shown as saved with
its hint, nothing new can set it), and per-input overrides.

## 3. Old -> new mapping

| Old label / field | New home |
|-------------------|----------|
| One point per (chart, group_by) | X axis -> Database column -> that column |
| One point per (input, query.group_by) | lifted to X when every input agrees; else that input's Override |
| a text column's own values on a category chart (by_value default) | X / Category = that column, made explicit |
| Group by time (bucket) | X axis -> Time -> Interval (preset) / step under Custom |
| Time step (interval, history) | folded into the step (bucket) on save; interval cleared |
| Time range (range) | Date range |
| Period (chart range on pie/donut/radar, ColumnLogic) | Date range |
| Period (input, query.range) | lifted to Date range when every input agrees; else that input's Override |
| Count over (window) on a comparison chart fed by one rule | derived from Date range on save (page -> follow, today, week/7d -> week, month/30d -> month, else live) |
| Count over on a rule-fed time chart | Advanced, as saved (read-only) |
| Date column (input ts_field) | lifted to the chart's Date column (Advanced) when every input agrees; else Override |
| Work out (chart agg) | Y axis -> Calculation |
| Work out (input agg, a fold) | lifted to Y when every input agrees; else that input's Override |
| Each point shows (rule input fold) | Y calculation (fold), Override per input |
| Of which column (field) | Y axis -> Column (recorded mode); a wired column is its own Y |
| Name | Series name (unchanged) |
| colours | Builder Appearance only (unchanged) |
| Threshold rules | Advanced (node), Data (Builders, unchanged place) |

## 4. Migration and compatibility

- READ TIME (frontend, chartModel.js): a chart with no x_type is read through
  the legacy rules exactly as the compiler applies them -- per input, its
  effective X, Y fold, range and date column -- and the common answer is
  lifted to the chart; any input that differs keeps its value as an
  override. Nothing is discarded, and opening a chart changes nothing on it.
- ON SAVE: the first edit of a chart in the node inspector or a Builder writes
  the whole model (x_type, group_by, agg, time_interval, bucket, range,
  ts_field) plus each input's overrides. The server cleans it
  (chartmodel.normalize: interval folded into bucket, overrides equal to the
  chart value dropped, window derived on a single-rule comparison chart).
  Idempotent: normalize(normalize(x)) == normalize(x).
- COMPILE: a node without x_type compiles on the legacy path, byte for byte
  as before. A node with x_type applies the chart-level X/Y/range/date column
  to every input that has no override (workflow.apply_chart_model).
- OLD KEYS stay readable and are written alongside the model, so an OTA
  rollback to the previous release still draws the chart.
- BUILDER WIDGETS: a hand-made chart carries `chart` (the same model) and
  the lowered legacy fields on the widget and its rows, so the renderer reads
  one shape; a compiled card's `chart` is the node's model, computed by the
  compile, and edits go back through card-style.

## 5. Conflicts and safest interpretations

1. Count over: it only acts on a chart fed by ONE live rule (it picks the
   rule's today/week/month counter). Folded into the date range for the
   comparison shapes; on a rule-fed time chart it is not a date range at all,
   so it is kept as saved under Advanced instead of being silently dropped.
2. "Latest value" fold on a live rule: not in the brief's six; kept readable
   as saved, not offered.
3. Graph node default range is "24h" while a category chart's table inputs
   read all time: migration keeps the effective window (All time), so pies and
   bars do not suddenly shrink to a day.
4. Date column on the per-step path: before, a nominated date column was
   honoured only on the sampler grid; the step path bucketed on the saved
   time. Now both paths use it (capped rows, as the grid path already was).
5. A bar with X = Time and an Automatic step uses the legacy rule (an hour,
   a day for week/month ranges) so it never draws a single bar.

## 6. Risks

- Migration correctness: covered by the JS matrix (tests/check_chartmodel.mjs)
  and the compile equivalence checks (tests/smoke_chartmodel.py).
- Pi query cost: unchanged per input (one aggregate per input per poll);
  hidden inputs are not queried; a nominated date column on the step path
  reads at most MAX_TS_ROWS rows.
- Timezone/DST: buckets stay on timeaxis local edges; the Week/Month presets
  use calendar steps (date_trunc / local month starts).

## 7. Decisions taken while building (added after implementation)

- One table input under the model compiles through the series path, so X,
  Y, range and Date column follow one set of rules; the card keeps the
  input's name as its title when it has none.
- "All time" cannot be one input's override (a blank period means "no
  override"), so when any input of a migrated chart read all time the
  chart's range becomes All time and the inputs with a period keep theirs
  as overrides.
- An explicit per-input Date column equal to what Automatic would pick
  reads as Automatic on both surfaces (a compiled card cannot tell them
  apart).
- A whole-day step along Past week / Past month uses the day-aligned
  window ("week"/"month"), as the per-day chart always did.
- Inputs that are all grouped by a column now draw as labels on a graph
  too (before, a graph drew them on the time grid as if ungrouped).
- The Date column steps and filters the per-step and per-column paths only
  for charts saved under the model (entry flag `by_ts_field`); older charts
  keep the saved time there, as they always drew.
- Builder widgets carry the model as `chart` (with `overrides`); the rows
  and the card carry the lowered fields the renderer reads, so the server
  path for hand-made charts is unchanged.

## 8. Rendering addendum (owner, same day)

Axis titles from the configuration (server `axis`, chartmodel.axis_titles)
or the operator's own (x_title / y_title, Builder Appearance), visible
ticks, axis lines, light gridlines, nice ticks from zero, rotated / cut /
thinned category labels, horizontal and lollipop bars, values on the chart
(automatic at 12 points or fewer), legend below the plot, tooltips with
series, full bucket and unit, labelled pie / donut slices with an HTML
legend, a radar value scale, and a compact mode for small tiles. All with
recharts (no new library); the lollipop is a recharts Bar `shape`, the
pie/donut legend is plain HTML because recharts' Pie legend stays empty
until the sectors animate in.

## 9. Known limitations

- A one-wire chart compiled before the model is a single dataset widget,
  so the Builder shows it an "Of column" select the node does not (the
  node's wire is the column). It is consistent again after the first save
  under the model.
- Rotation, thinning and the compact threshold use an estimated character
  width (6.4 px at 11 px); a browser pass is needed to confirm no overlap
  with real fonts.
- Count non-null treats an empty string as no value in both stores.

## 10. Chart type picker (2026-10-14, Tok Aba, mockups 59-64)

Choose the type first, then only that type's settings. UI only: nothing new
is stored, and old charts compile and normalize exactly as before
(tests/smoke_charttypes_golden.py, golden captured before the change).

- Types are names for (kind, x_type): Line Chart = line / spline (spline is
  the "Smooth line" tick), Area Chart = area (always drawn smooth, so no
  tick), Time-based Bar = bar + Time X, Comparison Bar = bar + column X,
  Pie, Donut, Radar (under Comparisons). Definitions and the switch rules
  are frontend/src/components/widgets/chartTypes.js; the picker, the
  coloured strip and the in-family "Show as" are ChartTypePicker.jsx, used
  by the node Inspector and the Builders' WidgetInspector alike.
- Scatter Plot is listed under Relationships, greyed "Coming soon" and not
  selectable, behind chartTypes.SCATTER_READY (the next slice flips it).
- A NEW chart (a node dropped on the canvas, a chart widget added from a
  Builder palette) opens on the picker; the "new" mark is session-only and
  never saved, so a stored chart always opens on its properties. Picking the
  kind it was dropped with writes nothing; another type writes the model.
- "Change chart type" keeps title, inputs (names, Show, overrides,
  colours), Y, date range, unit and thresholds; what a type has no place
  for (the interval going to a category X, the category field and an
  All-time range going to a time X, Smooth line) is listed before Continue.
- "Show as" lists the types of the chart's own family; a hand-made Builder
  chart still offers the card shapes under "Not a chart".
- The date range of a column-X chart moved from Advanced ("Only include
  rows from") to the main section as "Date range", as on every type in the
  mockup; a bar's X axis offers Time / Category and its column X is the
  "Category field" ("Camera / Input" = one bar per input), as on pie/donut.
- Not in the mockup but kept: Source, the Advanced Date column, thresholds
  only on axes kinds (pie/donut never had them). The mockup's per-panel live
  preview: the node inspector has none; the Builders' canvas previews live.
- Radar Chart, like Scatter, is listed but "Coming soon" (chartTypes.RADAR_READY = false; Tok Aba 2026-10-14): a new chart or a type change cannot pick it; a chart saved as a radar opens and edits as a Radar Chart, with Radar kept in its own Show as.
