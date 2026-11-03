// THE CHART BUILDER, drawn ONCE for the workflow node inspector
// (pages/Workflow.jsx) and both Builders (builderShared.jsx). The model it
// edits is chartModel.js; the labels and the step / range controls it reuses
// are cardFields.jsx.
//
// WHY ONE COMPONENT. The owner's brief (2026-10-13) asks for the workflow
// "1. pick the chart type, 2. configure X, 3. if Time, the interval and the
// date range, 4. configure Y, 5. the camera inputs, 6. optional advanced
// settings" -- the same on every surface. The node and the Builders used to
// draw their own copies of overlapping controls (Group by time, Period,
// Count over, Time range, Date column, One point per); each concept now has
// one control, here, and the panels differ only in their wiring rows.
//
//   Charts (bar)            X: Database column or Time; Y
//   Graphs (line/spline/    X: Time by default (a column allowed); Y
//           area)
//   Pie / donut             Category + Value, no axes
//   Radar                   Category / metric + Value, no axes
//
// Time settings appear only when X is Time. With a column on X the one date
// range still filters rows (the f42c8ebf decision); since the chart type
// picker (2026-10-14, mockup 64) it sits in the main section as "Date
// range", as it does on every type, rather than under Advanced.
//
// PER TYPE (ChartTypePicker.jsx names them): a Time-based Bar and a
// Comparison Bar are both kind "bar" -- the X axis select is what tells
// them apart, offered as Time / Category; the column X of a bar, a pie and a
// donut is the "Category field", whose empty answer is "Camera / Input"
// (one bar / slice per input).
import { Field as UiField } from "../ui/primitives.jsx";
import { StepField, RangeField } from "./cardFields.jsx";
import {
  CALCS, INTERVALS, PRESETS, ROW_CLOCK, calcWords, calcNeedsColumn,
  calcNeedsNumber, isAlt, isGraphKind, layoutOf, presetPatch, setXType,
} from "./chartModel.js";

//: The words of the chart builder, defined once (both panels read these).
export const CHART_FIELDS = {
  xAxis: { label: "X axis", hint: "What runs along the bottom: a column of the table, or time." },
  xColumn: { label: "X column", hint: "One point (or bar) per value of this column." },
  category: { label: "Category field", hint: "One slice per value of this column - or one per camera / input." },
  barCategory: { label: "Category field", hint: "One bar per value of this column - or one per camera / input." },
  metric: { label: "Category / metric", hint: "One spoke per value of this column - or one per camera / input." },
  interval: { label: "Interval", hint: "How the time axis is grouped." },
  show: { label: "Show" },
  step: { label: "Group by" },
  dateRange: { label: "Date range",
               hint: "Which stretch of time, for every input. Follow the Analytics date picker to let that page decide." },
  rowsFrom: { label: "Only include rows from",
              hint: "A filter on the rows every input reads. The X axis stays the column." },
  yAxis: { label: "Y axis", hint: "What each point measures." },
  value: { label: "Value", hint: "What each slice or spoke measures." },
  calc: { label: "Calculation" },
  yColumn: { label: "Of column" },
  dateColumn: { label: "Date column",
                hint: "Which datetime column places a row in time. Automatic uses the table's date column, else when the row was saved." },
  advanced: { label: "Advanced settings" },
};

//: The X axis types, first the one the type is named for: Time on a graph,
//: Category on a bar (choosing the other turns a Time-based Bar into a
//: Comparison Bar and back -- the strip above says which it now is).
const X_TYPES = [["time", "Time"], ["column", "Database column"]];
const BAR_X_TYPES = [["time", "Time"], ["column", "Category"]];
//: The empty Category field: one bar / slice / spoke per input (camera).
const PER_INPUT = "Camera / Input";

/** The X-axis type and column, the interval, the date range, the Y axis and
 *  the collapsed Advanced section, for one chart.
 *
 *  `columns`     columns X may use (union of the inputs' tables; json out)
 *  `yColumns`    columns the chart's own Y column can be (null: each input's
 *                wired column is its own Y, said in a hint instead)
 *  `hasStore`    any input reads recorded rows (a Date column means something)
 *  `timeColumns` datetime columns the inputs' tables declare
 *  `legacyWindow` a "Count over" still stored on a rule-fed graph
 *  `advanced`    extra rows for the Advanced section (thresholds) */
export function ChartAxisFields({ field: F = UiField, kind, model, onChange, disabled,
                                  columns = [], yColumns = null, yHint = "",
                                  hasStore = true, timeColumns = [],
                                  legacyWindow = "", advanced = null }) {
  const layout = layoutOf(kind);
  if (!layout || !model) return null;
  const set = (patch) => onChange({ ...model, ...patch });
  const colOptions = columns.filter((c) => c.type !== "json");
  const xIsTime = layout === "axes" && model.x_type === "time";
  const graph = isGraphKind(kind);
  // -- X / Category --------------------------------------------------------
  const xColumnSelect = (spec, emptyWords) => {
    const v = model.x_field || "";
    const known = colOptions.some((c) => c.name === v) || v === "";
    return (
      <F label={spec.label} hint={spec.hint}>
        <select className="input" value={v} disabled={disabled}
                aria-label={spec.label}
                onChange={(e) => set({ x_type: "column", x_field: e.target.value })}>
          <option value="">{emptyWords}</option>
          {colOptions.map((c) => (
            <option key={c.name} value={c.name}>
              {c.type && c.type !== "number" ? `${c.name} (${c.type})` : c.name}
            </option>
          ))}
          {!known && <option value={v}>{v} - as saved</option>}
        </select>
      </F>
    );
  };
  // -- Y / Value -------------------------------------------------------------
  const calc = model.y_agg || "";
  const calcKnown = CALCS.some(([c]) => c === calc);
  const ySpec = layout === "axes" ? CHART_FIELDS.yAxis : CHART_FIELDS.value;
  const yColList = (yColumns || []).filter((c) => (calcNeedsNumber(calc)
    ? c.type === "number" : c.type !== "json"));
  // A column X: the one date range filters the rows every input reads, in
  // the main section like every type's (mockup 64). Only where rows are read
  // -- a live rule's comparison takes its counter from the range on save.
  const rangeFrom = !xIsTime && hasStore && (
    <div role="group" aria-label="Date range" className="chart-builder-sec">
      <RangeField field={F} disabled={disabled} allowAll
                  value={model.range || ""} from={model.range_from} to={model.range_to}
                  hint={CHART_FIELDS.rowsFrom.hint} label={CHART_FIELDS.dateRange.label}
                  onChange={(r) => set(r)} />
    </div>
  );
  const advCount = [model.ts_field, legacyWindow].filter(Boolean).length;
  const bar = kind === "bar";
  // The Date column only has a choice where the table declares a date column
  // (else Automatic can only mean the saved time); a value already set stays
  // visible. A section with nothing in it is not drawn at all.
  const showDate = hasStore && (timeColumns.length > 0
    || (!!model.ts_field && model.ts_field !== ROW_CLOCK));
  const hasAdvanced = showDate || !!legacyWindow || !!advanced;
  return (
    <div className="chart-builder" data-chart-layout={layout}>
      <div role="group" aria-label={layout === "axes" ? "X axis" : "Category"}
           className="chart-builder-sec">
        {layout === "axes" ? (
          <>
            <F label={CHART_FIELDS.xAxis.label} hint={CHART_FIELDS.xAxis.hint}>
              <select className="input" value={model.x_type} disabled={disabled}
                      aria-label="X axis type"
                      onChange={(e) => onChange(setXType(model, e.target.value, kind))}>
                {(bar ? BAR_X_TYPES : X_TYPES).map(([v, w]) => <option key={v} value={v}>{w}</option>)}
              </select>
            </F>
            {model.x_type === "column" && xColumnSelect(
              bar ? CHART_FIELDS.barCategory : CHART_FIELDS.xColumn,
              graph ? "- choose a column -" : PER_INPUT)}
          </>
        ) : xColumnSelect(kind === "radar" ? CHART_FIELDS.metric : CHART_FIELDS.category,
                          PER_INPUT)}
      </div>
      {layout === "axes" && rangeFrom}
      {xIsTime && (
        <div role="group" aria-label="Time" className="chart-builder-sec">
          <F label={CHART_FIELDS.interval.label}
             hint={(PRESETS[model.time_interval] || {}).hint || CHART_FIELDS.interval.hint}>
            <select className="input" value={model.time_interval || "custom"}
                    disabled={disabled} aria-label="Interval"
                    onChange={(e) => onChange(presetPatch(model, e.target.value))}>
              {INTERVALS.map(([v, w]) => <option key={v} value={v}>{w}</option>)}
            </select>
          </F>
          {["week", "month"].includes(model.time_interval) && (
            <F label={CHART_FIELDS.show.label}>
              <select className="input" value={isAlt(model) ? "alt" : "day"}
                      disabled={disabled} aria-label="Show"
                      onChange={(e) => onChange(presetPatch(model, model.time_interval,
                                                            e.target.value === "alt"))}>
                <option value="day">
                  {model.time_interval === "week" ? "Each day (last 7 days)" : "Each day (last 30 days)"}
                </option>
                <option value="alt">
                  {model.time_interval === "week" ? "Each week (last 3 months)" : "Each calendar month (last year)"}
                </option>
              </select>
            </F>
          )}
          {model.time_interval === "custom" && (
            <StepField field={F} value={model.step || ""} disabled={disabled}
                       label={CHART_FIELDS.step.label}
                       hint="How far apart the points are. Automatic fits the date range."
                       onChange={(v) => set({ step: v })} />
          )}
          <RangeField field={F} disabled={disabled}
                      value={model.range || "24h"} from={model.range_from} to={model.range_to}
                      label={CHART_FIELDS.dateRange.label} hint={CHART_FIELDS.dateRange.hint}
                      onChange={(r) => set(r)} />
        </div>
      )}
      <div role="group" aria-label={ySpec.label} className="chart-builder-sec">
        <F label={ySpec.label} hint={yHint || ySpec.hint}>
          <select className="input" value={calc} disabled={disabled}
                  aria-label={CHART_FIELDS.calc.label}
                  onChange={(e) => set({ y_agg: e.target.value })}>
            {!calc && <option value="">Automatic - by column type</option>}
            {CALCS.map(([v, w]) => <option key={v} value={v}>{w}</option>)}
            {calc && !calcKnown && <option value={calc}>{calcWords(calc)} - as saved</option>}
          </select>
        </F>
        {yColumns && calcNeedsColumn(calc) && !model.y_field && (
          <p className="muted small" style={{ margin: "2px 0" }}>
            {["unique", "changes"].includes(calc) && model.x_type === "column" && model.x_field
              ? `No column picked: the ${calc === "unique" ? "different values" : "value changes"} of ${model.x_field} are counted.`
              : "Pick the column to work this out of - until then nothing is drawn."}
          </p>
        )}
        {yColumns && calcNeedsColumn(calc) && (
          <F label={CHART_FIELDS.yColumn.label}>
            <select className="input" value={model.y_field || ""} disabled={disabled}
                    aria-label={CHART_FIELDS.yColumn.label}
                    onChange={(e) => set({ y_field: e.target.value })}>
              <option value="">- choose a column -</option>
              {yColList.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              {model.y_field && !yColList.some((c) => c.name === model.y_field) && (
                <option value={model.y_field}>{model.y_field} - as saved</option>
              )}
            </select>
          </F>
        )}
      </div>
      {layout === "category" && rangeFrom}
      {hasAdvanced && (
      <details className="chart-advanced" data-testid="chart-advanced">
        <summary className="muted small">
          {CHART_FIELDS.advanced.label}{advCount ? ` (${advCount} set)` : ""}
        </summary>
        {showDate && (
          <F label={CHART_FIELDS.dateColumn.label} hint={CHART_FIELDS.dateColumn.hint}>
            <select className="input" value={model.ts_field || ""} disabled={disabled}
                    aria-label={CHART_FIELDS.dateColumn.label}
                    onChange={(e) => set({ ts_field: e.target.value })}>
              <option value="">Automatic (table date, else when saved)</option>
              <option value={ROW_CLOCK}>when the row was saved</option>
              {timeColumns.map((c) => <option key={c} value={c}>table date: {c}</option>)}
              {model.ts_field && model.ts_field !== ROW_CLOCK
                && !timeColumns.includes(model.ts_field) && (
                <option value={model.ts_field}>{model.ts_field} - as saved</option>
              )}
            </select>
          </F>
        )}
        {legacyWindow && (
          <p className="muted small" style={{ margin: "4px 0" }}>
            Count over: {legacyWindow} (as saved). It picks which counter of the
            wired rule this graph draws; set a rule&apos;s period on the rule.
          </p>
        )}
        {advanced}
      </details>
      )}
    </div>
  );
}

/** One input's own row: its name, whether it is drawn, and -- collapsed -- an
 *  optional override of the chart's Y calculation. A per-input X column,
 *  period or Date column saved before the chart builder is listed there as
 *  well, with a button that hands it back to the chart. Same on the node and
 *  in the Builders. */
export function InputRowControls({ field: F = UiField, index, label, placeholder,
                                   onLabel, show = true, onShow, override = {},
                                   onOverride, chartCalc = "", disabled,
                                   columns = null, children }) {
  const o = override || {};
  const legacy = [["group_by", "X column", o.group_by],
                  ["range", "Date range", o.range],
                  ["ts_field", "Date column", o.ts_field]]
    .filter(([, , v]) => v !== undefined && v !== null);
  const count = [o.agg, o.field].filter(Boolean).length + legacy.length;
  const clear = (key) => {
    const next = { ...o };
    delete next[key];
    onOverride(next);
  };
  return (
    <>
      <div className="chart-input-row" style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6 }}>
        <input className="input" style={{ flex: 1, minWidth: 0, width: "auto" }}
               value={label || ""} disabled={disabled} placeholder={placeholder}
               aria-label={`Series name for input ${index}`}
               onChange={(e) => onLabel(e.target.value)} />
        <label className="wf-check" style={{ margin: 0, flexShrink: 0 }}
               title="Hidden inputs stay listed here and are not drawn">
          <input type="checkbox" checked={show !== false} disabled={disabled}
                 aria-label={`Show input ${index} on the chart`}
                 onChange={(e) => onShow(e.target.checked)} />
          <span className="small">Show</span>
        </label>
      </div>
      <details className="chart-override">
        <summary className="muted small">
          Override for this input{count ? ` (${count})` : ""}
        </summary>
        <F label="Calculation" hint={`Leave on the chart's to use ${calcWords(chartCalc) || "the chart's Y"}.`}>
          <select className="input" value={o.agg || ""} disabled={disabled}
                  aria-label={`Calculation for input ${index}`}
                  onChange={(e) => onOverride({ ...o, agg: e.target.value || undefined })}>
            <option value="">Use the chart&apos;s</option>
            {CALCS.map(([v, w]) => <option key={v} value={v}>{w}</option>)}
            {o.agg && !CALCS.some(([c]) => c === o.agg) && (
              <option value={o.agg}>{calcWords(o.agg)} - as saved</option>
            )}
          </select>
        </F>
        {columns && calcNeedsColumn(o.agg || chartCalc) && (
          <F label="Of column">
            <select className="input" value={o.field || ""} disabled={disabled}
                    aria-label={`Column for input ${index}`}
                    onChange={(e) => onOverride({ ...o, field: e.target.value || undefined })}>
              <option value="">Use the chart&apos;s</option>
              {columns.filter((c) => c.type !== "json").map((c) => (
                <option key={c.name} value={c.name}>{c.name}</option>
              ))}
            </select>
          </F>
        )}
        {legacy.map(([key, words, v]) => (
          <p key={key} className="muted small" style={{ margin: "4px 0" }}>
            {words}: <strong>{v === "" ? (key === "ts_field" ? "when the row was saved" : "all time") : v}</strong>
            {" "}(saved for this input before the chart settings were shared){" "}
            <button type="button" className="btn btn-sm" disabled={disabled}
                    onClick={() => clear(key)}>Use the chart&apos;s</button>
          </p>
        ))}
        {children}
      </details>
    </>
  );
}

