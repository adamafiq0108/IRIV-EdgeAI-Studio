// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// THE CHART BUILDER MODEL, read and written the same way by the workflow node
// inspector (pages/Workflow.jsx) and both Builders (builderShared.jsx).
// Mirrors core/services/chartmodel.py; the design note is
// docs/design/chart-builder-refactor.md.
//
// WHY. A chart was configured through six overlapping controls -- "Group by
// time", "Period", "Count over", "Time range", "Date column", "One point
// per" -- and every camera input repeated four of them (owner brief,
// 2026-10-13). The model asks four questions ONCE per chart:
//
//   x          { x_type: "time" | "column", x_field }
//   y          { y_agg (Y calculation), y_field }
//   time       { time_interval, step }   -- only when X is Time
//   range      { range, range_from, range_to } and the Date column ts_field
//
// and each input keeps only what really differs: its name, whether it is
// shown, and an optional override (Y calculation; legacy per-input X,
// period and Date column are read and kept, never offered on a fresh row).
//
// READ-TIME MIGRATION. A chart saved before the model (no x_type) is read
// through the rules the compiler applies to it -- per input, its effective
// X, Y, period and Date column -- and the common answer is lifted to the
// chart; an input that differs keeps its own as an override. Opening a chart
// changes nothing; the first edit writes the whole model (nodePatch /
// widgetPatch). Pure functions only: no React, no fetch.

import { cleanStep } from "./cardFields.jsx";

//: The two categories "Show as" offers, by purpose (owner: "do not classify
//: by the number of inputs"). A bar may still use Time as its X axis.
export const CHART_CATEGORY = ["bar", "pie", "donut", "radar"];
export const GRAPH_CATEGORY = ["line", "spline", "area"];
export const CHART_KINDS = [...CHART_CATEGORY, ...GRAPH_CATEGORY];
//: Kinds drawn with conventional axes; the rest are a category + a value.
export const AXES_KINDS = ["bar", ...GRAPH_CATEGORY];
export const CATEGORY_KINDS = ["pie", "donut", "radar"];
export const isGraphKind = (kind) => GRAPH_CATEGORY.includes(kind);
/** "axes" | "category" | "" (a card kind: none of this applies). */
export const layoutOf = (kind) => (AXES_KINDS.includes(kind) ? "axes"
  : CATEGORY_KINDS.includes(kind) ? "category" : "");

//: The Y calculations a chart offers: [stored value, words, needs a column,
//: needs a NUMBER column]. The brief's six, in its order.
export const CALCS = [
  ["count", "Count records", false, false],
  ["count_nonnull", "Count non-null values", true, false],
  // How many DIFFERENT values the column holds per point (owner,
  // 2026-10-13, his exact words). Stored as the cards' old "unique".
  ["unique", "Count Difference Value", true, false],
  // How many times the column took a NEW value, in time order (owner, the
  // same day, in the same style). Stored as the cards' old "changes".
  ["changes", "Count Value Changes", true, false],
  ["sum", "Sum", true, true],
  ["avg", "Average", true, true],
  ["min", "Minimum", true, true],
  ["max", "Maximum", true, true],
];
//: Older answers a saved chart may hold, shown "as saved", never offered.
export const LEGACY_CALCS = { latest: "the latest value", last: "the latest value" };
export const calcWords = (v) => (CALCS.find(([c]) => c === v) || [])[1]
  || LEGACY_CALCS[v] || v;
export const calcNeedsColumn = (v) => !!(CALCS.find(([c]) => c === v) || [])[2]
  || ["latest", "last"].includes(v);
export const calcNeedsNumber = (v) => !!(CALCS.find(([c]) => c === v) || [])[3];

//: The interval presets. Mirrors chartmodel.PRESETS.
export const INTERVALS = [
  ["hour", "Hour"], ["day", "Day"], ["week", "Week"], ["month", "Month"],
  ["custom", "Custom"],
];
export const PRESETS = {
  hour: { range: "24h", step: "1h", hint: "Last 24 hours, one point per hour." },
  day: { range: "7d", step: "1d", hint: "Last 7 days, one point per day." },
  week: { range: "7d", step: "1d", alt: { range: "90d", step: "week" },
          hint: "Each day of the last week - or each week over a longer range." },
  month: { range: "30d", step: "1d", alt: { range: "365d", step: "month" },
           hint: "Each day of the last 30 days - or each calendar month." },
  custom: { range: "custom", step: "",
            hint: "Your own start, end and grouping step." },
};
//: The chart-level Date column meaning "when the row was saved". Never a
//: column name (column names cannot start with "@").
export const ROW_CLOCK = "@row";
const TIME_TYPES = ["timestamp", "date", "datetime"];
const COMPARISON = ["bar", "pie", "donut", "radar"];
const FOLDS = ["avg", "sum", "min", "max", "last", "count", "unique", "changes"];
const WORD_TYPES = ["text", "boolean", "uuid"];
//: chart_axis's map of the Period words onto a history range and back.
const QUERY_RANGE_OF = { "7d": "week", "30d": "month" };
const QUERY_RANGES = ["page", "today", "week", "month", "3h", "6h", "12h", "24h",
                      "7d", "30d", "90d", "180d", "365d", "5m", "15m", "30m", "1h",
                      "custom"];
//: "Count over" for a single-rule comparison chart, from its range.
const WINDOW_OF = { page: "follow", today: "today", week: "week", "7d": "week",
                    month: "month", "30d": "month" };
const RANGE_OF_WINDOW = { follow: "page", today: "today", week: "week",
                          month: "month" };

/** The preset a stored step reads as (chartmodel.interval_of). */
export function intervalOf(step, range = "") {
  const s = cleanStep(step);
  if (s === "1h") return "hour";
  if (s === "1d") return ["30d", "month"].includes(range) ? "month" : "day";
  if (s === "week") return "week";
  if (s === "month") return "month";
  return "custom";
}

/** The fold ("Each point shows") a Y calculation is on an input. */
export const foldOfCalc = (calc) => ({ latest: "last" }[calc] || calc || "");
/** The Y calculation a fold reads as. */
export const calcOfFold = (fold) => ({ last: "latest" }[fold] || fold || "");

/** A fresh model for a kind: Time for a graph, a column for the rest. */
export function defaultModel(kind) {
  return isGraphKind(kind)
    ? { x_type: "time", x_field: "", y_agg: "", y_field: "", time_interval: "hour",
        step: "1h", range: "24h", range_from: "", range_to: "", ts_field: "" }
    : { x_type: "column", x_field: "", y_agg: "count", y_field: "",
        time_interval: "", step: "", range: "", range_from: "", range_to: "",
        ts_field: "" };
}

/** Choosing an interval preset: its step and its default range, written
 *  once (the date range stays its own setting afterwards). `alt` picks the
 *  coarser option Week and Month offer. */
export function presetPatch(model, interval, alt = false) {
  const p = PRESETS[interval] || PRESETS.custom;
  const pick = alt && p.alt ? p.alt : p;
  if (interval === "custom") {
    return { ...model, time_interval: "custom",
             step: model.step && model.time_interval !== "custom" ? model.step : (model.step || ""),
             range: "custom" };
  }
  return { ...model, time_interval: interval, step: pick.step, range: pick.range,
           range_from: "", range_to: "" };
}
/** Week/Month: whether the coarser aggregation (each week / each calendar
 *  month) is the one chosen. */
export const isAlt = (model) => (model.time_interval === "week" && cleanStep(model.step) === "week")
  || (model.time_interval === "month" && cleanStep(model.step) === "month");

/** Switching X between a column and Time. Never auto-picks a column: an
 *  assigned column is only ever replaced by the operator choosing Time. */
export function setXType(model, xType, kind) {
  if (xType === model.x_type) return model;
  if (xType === "time") {
    const base = model.time_interval && model.step ? model
      : presetPatch(model, isGraphKind(kind) ? "hour" : "day");
    return { ...base, x_type: "time" };
  }
  return { ...model, x_type: "column" };
}

// -- reading a NODE ------------------------------------------------------------

/** What one wired input of a chart node draws, under the legacy rules the
 *  compiler applies to a node without x_type (workflow.py series_entry,
 *  chart_axis, apply_series_conf, settle_axis, x_field_window). */
function legacyInput(d, kind, input, ctx) {
  const conf = input.conf || {};
  const cq = conf.query || {};
  const B = ctx.period;
  const out = { group: "", bucket: "", fold: "", range: "", ts: undefined };
  if (input.source !== "dataset" || !input.column) {
    // A live rule: its fold; a recorded card: its own question.
    out.fold = conf.agg || (input.source === "history" ? "avg" : "");
    return out;
  }
  const number = input.type === "number";
  const words = WORD_TYPES.includes(input.type);
  if (cq.group_by) out.group = cq.group_by;
  else if (!B && ctx.byValue && words) out.group = input.column;
  out.bucket = out.group ? "" : B;
  let fold = "";
  if (conf.agg) {
    fold = ["avg", "sum", "min", "max", "last"].includes(conf.agg) && !number
      ? "count" : conf.agg;
  } else if (B && FOLDS.includes(foldOfCalc(d.agg))) {
    fold = foldOfCalc(d.agg);
    if (["sum", "avg", "min", "max"].includes(fold) && !number) fold = "count";
  } else {
    fold = number ? "avg" : "count";
  }
  if (out.bucket && fold === "last") fold = number ? "max" : "count";
  out.fold = fold;
  const chartRange = QUERY_RANGE_OF[d.range] || d.range || "";
  if (cq.range) out.range = cq.range;
  else if (B && QUERY_RANGES.includes(chartRange)) out.range = chartRange;
  if (out.group && !out.range && d.range && !(d.range === "24h" && !B)) {
    out.range = QUERY_RANGE_OF[d.range] || d.range;
  }
  if ("ts_field" in conf) {
    // An explicit answer that is the automatic one (the table's first date
    // column, "" with none) reads as Automatic -- the compiled card cannot
    // tell the two apart, and the node and the Builders must agree.
    const auto = ((input.columns || []).find((c) => TIME_TYPES.includes(c.type)) || {}).name || "";
    out.ts = (conf.ts_field || "") === auto ? undefined : (conf.ts_field || "");
  }
  return out;
}

/** The legacy reading of a ONE-wire chart (the col_src path of the compile). */
function legacySingle(d, kind, input) {
  const number = input.type === "number";
  const graph = isGraphKind(kind);
  const out = { group: "", bucket: "", fold: "", range: "" };
  if (d.group_by) out.group = d.group_by;
  else if (d.bucket) out.bucket = cleanStep(d.bucket) || "";
  else if (graph || number) out.bucket = ["week", "month"].includes(d.range) ? "1d" : "1h";
  else out.group = input.column;
  let agg = d.agg || (number ? "latest" : "count");
  if (["sum", "avg", "min", "max"].includes(agg) && !number) agg = "count";
  if (out.bucket && agg === "latest") agg = number ? "max" : "count";
  out.fold = foldOfCalc(agg);
  out.store = agg;
  out.range = QUERY_RANGES.includes(d.range) && ["page", "today", "week", "month", ""]
    .includes(d.range || "") ? (d.range || "") : "";
  return out;
}

/** {model, overrides} for a chart NODE.
 *
 *  `inputs` is what the inspector knows about the wires, in slot order:
 *  [{sid, source ("dataset" | "query" | "history" | "stat" | "none"),
 *    column, type, columns, conf}], and `opts.single` is the one-wire chart
 *  (no series list), `opts.recorded` a chart asking recorded data itself.
 *  overrides = {sid: {agg?, group_by?, range?, ts_field?, show?}}.
 */
export function readNodeModel(d, kind, inputs = [], opts = {}) {
  d = d || {};
  const series = (d.series && typeof d.series === "object") ? d.series : {};
  const overrides = {};
  for (const inp of inputs) {
    const conf = series[inp.sid] || {};
    overrides[inp.sid] = conf.show === false ? { show: false } : {};
  }
  if (["time", "column"].includes(d.x_type)) {
    // Saved under the model: read it as it is.
    const model = {
      x_type: d.x_type, x_field: d.x_type === "column" ? (d.group_by || "") : "",
      y_agg: d.agg || "", y_field: d.field || "",
      time_interval: d.time_interval || intervalOf(d.bucket || d.interval, d.range),
      step: cleanStep(d.bucket || d.interval) || "",
      range: d.range ?? "", range_from: d.range_from || "", range_to: d.range_to || "",
      ts_field: d.ts_field || "",
    };
    for (const inp of inputs) {
      const conf = series[inp.sid] || {};
      const o = overrides[inp.sid];
      if (conf.agg) o.agg = calcOfFold(conf.agg);
      if (conf.query?.group_by) o.group_by = conf.query.group_by;
      if (conf.query?.range) o.range = conf.query.range;
      if ("ts_field" in conf) o.ts_field = conf.ts_field || "";
    }
    return { model, overrides, migrated: false };
  }
  return { ...migrateNode(d, kind, inputs, opts, overrides), migrated: true };
}

function migrateNode(d, kind, inputsIn, opts, overrides) {
  const graph = isGraphKind(kind);
  // Each input's stored answers ride with it (data.series[<sid>]).
  const inputs = inputsIn.map((i) => ({ ...i, conf: i.conf || (d.series || {})[i.sid] || {} }));
  const model = { x_type: "time", x_field: "", y_agg: d.agg || "", y_field: d.field || "",
                  time_interval: "", step: "", range: d.range ?? "",
                  range_from: d.range_from || "", range_to: d.range_to || "",
                  ts_field: "" };
  const legacyStep = cleanStep(d.bucket) || cleanStep(d.interval) || "";
  // A CHART ASKING RECORDED DATA ITSELF, or one wired to nothing a table can
  // say: its own fields are already the model's.
  const columns = inputs.filter((i) => i.source === "dataset" && i.column);
  if (opts.recorded || !columns.length) {
    // Y as the compile reads it: a recorded question counts unless told
    // otherwise; a rule's samples average unless its input says.
    const firstConf = inputs[0] ? inputs[0].conf || {} : {};
    model.y_agg = d.agg || (opts.recorded ? "count"
      : inputs[0] && ["history", "stat"].includes(inputs[0].source)
        ? calcOfFold(firstConf.agg || "avg") : "");
    if (d.group_by) { model.x_type = "column"; model.x_field = d.group_by; }
    else if (legacyStep || graph) { model.x_type = "time"; model.step = legacyStep; }
    else { model.x_type = "column"; model.x_field = ""; }
    if (!opts.recorded && !graph) {
      // A comparison of live rules: its "Count over" is its date range now.
      model.range = RANGE_OF_WINDOW[d.window] || "";
    }
    if (graph && !model.range) model.range = "24h";
    for (const inp of inputs) {
      const conf = inp.conf || {};
      if (conf.agg && calcOfFold(conf.agg) !== model.y_agg) {
        overrides[inp.sid].agg = calcOfFold(conf.agg);
      }
    }
    model.time_interval = model.x_type === "time" ? intervalOf(model.step, model.range) : "";
    return { model, overrides };
  }
  // TABLE COLUMNS WIRED IN: each input's effective answer, then the common one.
  let eff;
  if (opts.single && columns.length === 1 && inputs.length === 1) {
    const one = legacySingle(d, kind, columns[0]);
    eff = [{ ...one, sid: columns[0].sid }];
  } else {
    const period = cleanStep(d.bucket) || "";
    const byValue = !period && COMPARISON.includes(kind)
      && inputs.every((i) => i.source === "dataset" && i.column);
    eff = columns.map((inp) => ({ ...legacyInput(d, kind, inp, { period, byValue }),
                                  sid: inp.sid }));
  }
  const first = eff[0];
  // X
  if (first.group) { model.x_type = "column"; model.x_field = first.group; }
  else if (first.bucket) { model.x_type = "time"; model.step = first.bucket; }
  else if (graph) { model.x_type = "time"; model.step = legacyStep; }
  else { model.x_type = "column"; model.x_field = ""; }
  for (const e of eff.slice(1)) {
    if (model.x_type === "column" && e.group && e.group !== model.x_field) {
      overrides[e.sid].group_by = e.group;
    } else if (model.x_type === "time" && e.group) {
      overrides[e.sid].group_by = e.group;
    }
  }
  // Y: the store question when the one-wire path asked one, else the fold.
  const yOf = (e) => (e.store === "latest" ? "latest" : calcOfFold(e.fold));
  model.y_agg = yOf(first);
  for (const e of eff.slice(1)) {
    if (yOf(e) !== model.y_agg) overrides[e.sid].agg = yOf(e);
  }
  // Date range
  const gridPath = graph && !first.bucket && !first.group;
  if (gridPath) {
    model.range = d.range || "24h";
    for (const e of eff) if (e.range && e.range !== model.range) overrides[e.sid].range = e.range;
  } else {
    // "All time" cannot be one input's override (a blank period is no
    // override at all), so when any input reads all time the CHART does and
    // the inputs with a period keep theirs.
    model.range = eff.some((e) => !e.range) ? "" : (first.range || "");
    for (const e of eff) {
      if ((e.range || "") !== model.range) overrides[e.sid].range = e.range || "";
    }
  }
  if (model.range === "custom") {
    model.range_from = d.range_from || "";
    model.range_to = d.range_to || "";
  }
  // Date column: lifted only when every input says the same thing.
  const tss = eff.map((e) => e.ts);
  if (tss.every((t) => t !== undefined && t === tss[0])) {
    model.ts_field = tss[0] === "" ? ROW_CLOCK : tss[0];
  } else {
    for (const e of eff) if (e.ts !== undefined) overrides[e.sid].ts_field = e.ts;
  }
  model.time_interval = model.x_type === "time" ? intervalOf(model.step, model.range) : "";
  return { model, overrides };
}

/** The node fields that store a model and its overrides: what an edit in
 *  the node inspector or a Builder writes (the whole model, every time, so a
 *  migrated chart lands in one consistent shape). */
export function nodePatch(d, kind, model, overrides = {}, opts = {}) {
  d = d || {};
  const series = (d.series && typeof d.series === "object") ? d.series : {};
  const patch = modelNodeFields(model, kind, opts);
  const sids = new Set([...Object.keys(series), ...Object.keys(overrides)]);
  const next = {};
  for (const sid of sids) {
    const was = { ...(series[sid] || {}) };
    delete was.agg; delete was.ts_field; delete was.show;
    const q = { ...(was.query || {}) };
    delete q.group_by; delete q.range;
    delete was.query;
    const o = overrides[sid] || {};
    const entry = { ...was };
    if (o.agg) entry.agg = foldOfCalc(o.agg);
    if (o.group_by) q.group_by = o.group_by;
    if (o.range !== undefined && o.range !== (model.range || "")) q.range = o.range;
    if (Object.keys(q).length) entry.query = q;
    if (o.ts_field !== undefined) entry.ts_field = o.ts_field;
    if (o.show === false) entry.show = false;
    if (Object.keys(entry).length) next[sid] = entry;
  }
  patch.series = next;
  return patch;
}

/** The CHART-LEVEL node fields a model is stored as (no inputs): what the
 *  node inspector writes and what a Builder sends a compiled card's node
 *  through card-style (workflow.CARD_QUESTION_FIELDS). */
export function modelNodeFields(model, kind, opts = {}) {
  const patch = {
    x_type: model.x_type,
    group_by: model.x_type === "column" ? (model.x_field || "") : "",
    agg: model.y_agg || "",
    time_interval: model.x_type === "time" ? (model.time_interval || intervalOf(model.step, model.range)) : "",
    bucket: model.x_type === "time" ? (model.step || "") : "",
    interval: "",
    range: model.range || "",
    range_from: model.range === "custom" ? (model.range_from || "") : "",
    range_to: model.range === "custom" ? (model.range_to || "") : "",
    ts_field: model.ts_field || "",
  };
  if (opts.recorded) {
    // Count Difference Value with no column picked counts the X column.
    patch.field = model.y_field || (["unique", "changes"].includes(model.y_agg)
      && model.x_type === "column"
      ? (model.x_field || "") : "");
  }
  if (COMPARISON.includes(kind) && model.x_type === "column") {
    patch.window = WINDOW_OF[model.range] || "live";
  }
  return patch;
}

// -- reading a BUILDER WIDGET --------------------------------------------------

const rowSource = (w, s) => s.source || (["dataset", "query"].includes(w.source)
  ? w.source : w.source === "history" ? "history" : "stat");

/** What one Builder row (or a compiled card's entry) draws, as stored. */
function rowEffective(w, s) {
  const src = rowSource(w, s);
  const q = (s.source ? s.query : (s.query || w.query)) || {};
  // The compiled card says the Date column it USES; the table's first date
  // column ("" with none) is what Automatic means, so it reads as that.
  const cols = s.columns || w.columns || [];
  const auto = (cols.find((c) => TIME_TYPES.includes(c.type)) || {}).name || "";
  const ts = s.ts_field === undefined || s.ts_field === auto ? undefined : s.ts_field;
  const out = { sid: s.id, source: src, group: q.group_by || "", bucket: q.bucket || "",
                range: q.range || "", ts, field: s.field || q.field || "",
                show: s.show !== false };
  if (src === "dataset" || src === "query") {
    out.y = q.agg && q.agg !== "" ? q.agg : calcOfFold(s.agg) || "count";
  } else {
    out.y = calcOfFold(s.agg || "avg");
  }
  return out;
}

/** {model, overrides} for a Builder widget: its `chart` when it has one, else
 *  lifted from what its rows (or a compiled card's entries) say. */
export function readWidgetModel(w, rows) {
  w = w || {};
  const kind = w.kind;
  const effs = (rows || []).map((s) => rowEffective(w, s));
  const overrides = {};
  for (const e of effs) overrides[e.sid] = e.show ? {} : { show: false };
  let model;
  const stored = w.chart && ["time", "column"].includes(w.chart.x_type) ? w.chart : null;
  if (stored) {
    // Saved under the model: the overrides are stored with it (a compiled
    // card's are its node's, carried by the compile), never re-derived from
    // what the rows ended up asking -- the compiler adjusts a row (a text
    // column counted, "latest" per step read as the largest) and that is
    // not the operator overriding anything.
    const { overrides: kept, ...rest } = stored;
    model = { ...defaultModel(kind), ...rest };
    for (const [sid, o] of Object.entries(kept || {})) {
      if (overrides[sid]) Object.assign(overrides[sid], o);
      else overrides[sid] = { ...o };
    }
    return { model, overrides };
  }
  {
    const store = effs.filter((e) => ["dataset", "query"].includes(e.source));
    const first = store[0] || effs[0] || { group: "", bucket: "", y: "", range: "" };
    const step = cleanStep(w.interval) || cleanStep(first.bucket) || "";
    model = defaultModel(kind);
    if (first.group) Object.assign(model, { x_type: "column", x_field: first.group });
    else if (first.bucket || isGraphKind(kind) || w.source === "history") {
      Object.assign(model, { x_type: "time", step });
    } else Object.assign(model, { x_type: "column", x_field: "" });
    model.y_agg = first.y || model.y_agg;
    model.y_field = first.field || "";
    const timed = isGraphKind(kind) || w.source === "history";
    model.range = timed ? (w.range || first.range || "24h") : (first.range || "");
    model.range_from = w.range_from || "";
    model.range_to = w.range_to || "";
    const tss = store.map((e) => e.ts);
    model.ts_field = tss.length && tss.every((t) => t !== undefined && t === tss[0])
      ? (tss[0] === "" ? ROW_CLOCK : tss[0]) : "";
    model.time_interval = model.x_type === "time" ? intervalOf(model.step, model.range) : "";
  }
  // Whatever a row says that the chart does not is that row's override.
  const chartTs = model.ts_field === ROW_CLOCK ? "" : model.ts_field;
  for (const e of effs) {
    const o = overrides[e.sid];
    const storeRow = ["dataset", "query"].includes(e.source);
    if (e.y && model.y_agg && e.y !== model.y_agg) o.agg = e.y;
    if (storeRow && e.group && !(model.x_type === "column" && e.group === model.x_field)) {
      o.group_by = e.group;
    }
    if (storeRow && e.range && e.range !== (model.range || "")
        && !(QUERY_RANGE_OF[model.range] === e.range)) o.range = e.range;
    if (storeRow && e.ts !== undefined && model.ts_field && e.ts !== chartTs) o.ts_field = e.ts;
    if (e.field && model.y_field && e.field !== model.y_field) o.field = e.field;
  }
  return { model, overrides };
}

/** The widget fields a model and its overrides LOWER to: `chart` (what the
 *  editors read back), the rows' questions and the card's range and step --
 *  what the renderer draws from. Every row shares the chart unless it holds
 *  an override; a row's own source and table are never touched. */
export function widgetPatch(w, rows, model, overrides = {}) {
  const timed = model.x_type === "time";
  const step = timed ? (model.step || "") : "";
  const lowerQuery = (q, o, cols, src) => {
    const out = { ...(q || {}) };
    const has = (name) => src === "query" || !cols || !cols.length
      || cols.some((c) => c.name === name);
    const group = o.group_by
      || (model.x_type === "column" && model.x_field && has(model.x_field) ? model.x_field : "");
    out.group_by = group;
    out.bucket = !group && step ? step : "";
    const y = o.agg || model.y_agg || out.agg || "count";
    out.agg = y === "last" ? "latest" : y;
    out.range = o.range !== undefined ? o.range : (model.range || "");
    if (out.range === "custom") {
      out.range_from = model.range_from || "";
      out.range_to = model.range_to || "";
    } else { delete out.range_from; delete out.range_to; }
    return out;
  };
  const tsFor = (o, cols) => {
    if (o.ts_field !== undefined) return o.ts_field;
    if (model.ts_field === ROW_CLOCK) return "";
    const times = (cols || []).filter((c) => TIME_TYPES.includes(c.type)).map((c) => c.name);
    if (model.ts_field && times.includes(model.ts_field)) return model.ts_field;
    return times[0] || "";
  };
  const nextRows = (rows || []).map((s) => {
    const o = overrides[s.id] || {};
    const src = rowSource(w, s);
    const row = { ...s };
    if (o.show === false) row.show = false; else delete row.show;
    if (src === "dataset" || src === "query") {
      if (s.source) {
        const cols = s.columns || [];
        row.query = lowerQuery(s.query, o, cols, src);
        row.agg = "";
        if (src === "dataset") {
          let field = o.field || (model.y_field && cols.some((c) => c.name === model.y_field)
            ? model.y_field : s.field || "");
          // Count Difference Value of WHAT: with no column picked, the
          // chart's X column (its categories) is what is counted.
          if (!field && ["unique", "changes"].includes(row.query.agg) && model.x_type === "column"
              && cols.some((c) => c.name === model.x_field)) field = model.x_field;
          row.field = calcNeedsColumn(row.query.agg) ? field : (s.field || "");
          row.ts_field = tsFor(o, cols);
          if (row.ts_field) row.by_ts_field = true; else delete row.by_ts_field;
        }
      }
    } else {
      const y = o.agg || model.y_agg;
      const fold = foldOfCalc(y === "count_nonnull" ? "count" : y);
      if (FOLDS.includes(fold)) row.agg = fold;
    }
    return row;
  });
  // The overrides ride inside `chart`, so reopening reads back exactly what
  // was set (readWidgetModel), not what the rows ended up asking.
  const kept = {};
  for (const [sid, o] of Object.entries(overrides)) {
    const { show, ...rest } = o || {};
    const clean = Object.fromEntries(Object.entries(rest)
      .filter(([, v]) => v !== undefined && v !== null));
    if (Object.keys(clean).length) kept[sid] = clean;
  }
  const { overrides: _drop, ...plain } = model;
  const patch = { chart: { ...plain, ...(Object.keys(kept).length ? { overrides: kept } : {}) },
                  series: nextRows };
  if (isGraphKind(w.kind) || w.source === "history" || timed) {
    patch.range = timed ? (["", "custom", "page"].includes(model.range) || QUERY_RANGES.includes(model.range)
      ? (model.range || "24h") : "24h") : (w.range || "");
    patch.range_from = model.range === "custom" ? (model.range_from || "") : "";
    patch.range_to = model.range === "custom" ? (model.range_to || "") : "";
    patch.interval = step;
  }
  if (["dataset", "query"].includes(w.source)) {
    const cols = w.columns || [];
    patch.query = lowerQuery(w.query, {}, cols, w.source);
    if (w.source === "dataset" && calcNeedsColumn(patch.query.agg)) {
      // The picked column, or -- Count Difference Value with none picked --
      // the chart's X column.
      const fallback = ["unique", "changes"].includes(patch.query.agg) && model.x_type === "column"
        ? model.x_field : "";
      if (model.y_field || fallback) patch.query.field = model.y_field || fallback;
    }
  }
  return patch;
}

// -- titles ---------------------------------------------------------------------

const STEP_TITLES = { "1h": "Hour", "1d": "Day", week: "Week", month: "Month" };

/** The automatic axis titles a chart's configuration says (rendering
 *  addendum: "X: the column name or the time step, Y: the calculation and
 *  column or unit"). `stepWords` comes from cardFields so this file keeps no
 *  second copy of the step vocabulary. */
export function autoAxisTitles(model, { unit = "", field = "", stepWords } = {}) {
  if (!model) return { x: "", y: "" };
  let x = "";
  if (model.x_type === "column") {
    x = model.x_field ? model.x_field.replace(/_/g, " ") : "Input";
  } else {
    const s = cleanStep(model.step);
    x = s ? (STEP_TITLES[s] || (stepWords ? stepWords(s) : s)) : "Time";
  }
  const col = (model.y_field || field || "").replace(/_/g, " ");
  const calc = model.y_agg || "";
  let y;
  if (!calc || calc === "count") y = "Total rows";
  else if (calc === "count_nonnull") y = col ? `Count of ${col}` : "Count non-null";
  else if (calc === "unique") y = col ? `Count Difference Value of ${col}` : "Count Difference Value";
  else if (calc === "changes") y = col ? `Count Value Changes of ${col}` : "Count Value Changes";
  else y = col ? `${calcWords(calc)} ${col}` : calcWords(calc);
  if (unit) y = `${y} (${unit})`;
  return { x, y };
}
