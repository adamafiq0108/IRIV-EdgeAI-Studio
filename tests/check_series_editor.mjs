// A chart holds SEVERAL inputs, and the editor says so without being found.
//
// The multi-series editor existed before this check and was, in practice,
// invisible: it hid behind a button reading "Use series (multi-key)", it took
// stat keys only, and what an operator saw on a chart was a single "Data key"
// field. The complaint that followed - "I cannot add more than one input" -
// was a true report of the UI, not of the feature.
//
// So the facts pinned here are the ones that make it discoverable and honest,
// and every one of them is a thing that was wrong or absent:
//
//   1. every chart kind opens with the series editor and ONE prefilled row,
//      and no single "Data key" field beside it;
//   2. a widget saved before series existed becomes exactly one series, with
//      the colour somebody chose by hand still on it;
//   3. a second series turns per-day colouring off and SAYS why, because
//      Widget.jsx paints by day on a single series only;
//   4. a missing point is a gap, never a zero - a nulled sample must not
//      reach the chart as a measurement of nothing;
//   5. eight series with no colour set get eight different colours.
//
//   node tests/check_series_editor.mjs
import { rmSync } from "node:fs";
import { readText } from "./_text.mjs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
// esbuild, react, react-dom and jsdom are the FRONTEND's dependencies - the
// repo root has no node_modules of its own. Same arrangement as
// check_widget_render.mjs, for the same reason.
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".series-editor-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const fail = (msg) => { console.error(`FAIL ${msg}`); bad += 1; };
const ok = (msg) => console.log(`ok   ${msg}`);
const check = (msg, passed, detail = "") => {
  if (passed) ok(msg);
  else fail(`${msg}${detail ? `\n      ${detail}` : ""}`);
};

// recharts' ResponsiveContainer measures its parent and jsdom reports 0x0, so
// a chart would draw nothing however real the DOM is. Swapped for one that
// hands its child a fixed size - only for Widget.jsx's own import, so the
// shim itself still resolves the genuine library.
const SIZE = { width: 600, height: 300 };
const shimResponsiveContainer = {
  name: "shim-responsive-container",
  setup(b) {
    b.onResolve({ filter: /^recharts$/ }, (args) => (
      args.importer.replace(/\\/g, "/").endsWith("widgets/Widget.jsx")
        ? { path: "recharts-shim", namespace: "shim" } : null));
    b.onLoad({ filter: /^recharts-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement, cloneElement } from "react";
        export * from "recharts";
        export function ResponsiveContainer({ children }) {
          const sized = typeof children === "function"
            ? children(${JSON.stringify(SIZE)})
            : cloneElement(children, ${JSON.stringify(SIZE)});
          return createElement("div", null, sized);
        }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import Widget from "${p("src/components/widgets/Widget.jsx")}";
import { WidgetInspector, widgetSeries, seriesColorAt, newWidget,
         seriesStyle } from "${p("src/components/widgets/builderShared.jsx")}";
import { SERIES_COLORS, toRows } from "${p("src/components/charts.js")}";

export { widgetSeries, seriesColorAt, newWidget, seriesStyle,
         SERIES_COLORS, toRows };

function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  const html = el.innerHTML;
  act(() => root.unmount());
  return html;
}
export function inspect(el, widget, meta) {
  // The patch callback is a no-op: what is under test is what the panel
  // OFFERS, which is a pure function of the widget it is handed.
  return mount(el, h(WidgetInspector, { current: widget, meta, patch: () => {} }));
}
export function draw(el, widget) {
  return mount(el, h(Widget, { widget, accent: "#04bade" }));
}
`;

await build({
  plugins: [shimResponsiveContainer],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});

const mod = req(OUT);
const host = document.body;
const run = (fn, ...args) => {
  const el = document.createElement("div");
  host.appendChild(el);
  try { return fn(el, ...args); } finally { host.removeChild(el); }
};
const inspect = (widget, meta) => run(mod.inspect, widget, meta);
const draw = (widget) => run(mod.draw, widget);

// What /api/builder hands the inspector, trimmed to what the editor reads.
const META = {
  sources: { stat: ["zone_a_count", "zone_b_count"], bucket: ["by_zone"],
             events: ["ts", "type"] },
  key_labels: { zone_a_count: "Zone A · count" },
  kinds: ["tile", "line", "bar", "area", "spline", "donut", "pie", "radar"],
  history: { ranges: ["24h", "7d"], intervals: ["", "1m", "1h"],
             aggs: ["avg", "sum", "min", "max", "last", "count"] },
  query: { aggregations: ["count", "sum"], buckets: ["hour", "day"],
           ranges: ["today", "week"], columns: ["type", "severity"] },
  catalog: [], capabilities: [], editable: true,
};

//: Mirrors CHART_KINDS in builderShared.jsx and core/services/builder.py.
const CHART_KINDS = ["line", "area", "spline", "bar", "donut", "pie", "radar"];
//: ...and the shapes that show ONE value, which keep the single key field.
const VALUE_KINDS = ["tile", "gauge", "status", "verdict", "heatmap"];

const chartWidget = (kind, extra = {}) => ({
  id: `w_${kind}`, title: "T", kind,
  source: kind === "donut" || kind === "pie" ? "stat" : "history",
  key: "zone_a_count", span: 4, template: "plain", color: "", bg: "",
  bands: {}, by_day: false, grid: {}, status: {}, query: {}, range: "24h",
  ...extra,
});

// -- 1. every chart opens on the series editor, with one row -----------------
for (const kind of CHART_KINDS) {
  const html = inspect(chartWidget(kind), META);
  check(`${kind}: the inputs editor is there without being asked for`,
        html.includes("Inputs") && html.includes("Add series"),
        "the series editor did not render for a chart kind");
  check(`${kind}: it opens with one row, prefilled from the card's own key`,
        // The row's source label, counted once per row: it is rendered twice
        // per Field (the label, and the hint button's aria-label), so the
        // aria-label is the one that says "one row" without arithmetic.
        (html.match(/About Where from/g) || []).length === 1
        && html.includes("Zone A"),
        "a chart opened with no row, with several, or with an empty one");
  check(`${kind}: the single "Data key" field is gone`,
        !html.includes(">Data key<"),
        "a chart offered both a series list and a one-key field");
  // A chart's colours are set HERE, in the Builders, and only here (owner,
  // 2026-10-12), in their Appearance section; the workflow node shows a hint
  // instead. Section membership is pinned in check_cardfields.mjs.
  check(`${kind}: the Builder keeps each input's colour and palette pin`,
        html.includes('aria-label="Colour for series 1"')
        && html.includes("Palette colour for input 1")
        && !html.includes("Colours are set in the Analytics or Dashboard Builder."),
        "the Builder lost a chart's series colour");
}

// A card that shows ONE value keeps the single key: a tile has no second
// input to give, and a series list on it would be an offer it cannot honour.
for (const kind of VALUE_KINDS) {
  const w = { ...chartWidget(kind), source: "stat" };
  const html = inspect(w, META);
  check(`${kind}: shows no key picker and grows no series list`,
        !html.includes(">Value<") && !html.includes("Add series"),
        "a one-value card was offered a series list");
}

// A chart dragged out of the palette is born holding its one series, so the
// editor is not the only place the shape exists.
const born = mod.newWidget({ kind: "line", source: "history", label: "History chart" },
                           META.sources);
check("a chart dragged from the palette is born with one series",
      Array.isArray(born.series) && born.series.length === 1
      && born.series[0].key === "zone_a_count",
      JSON.stringify(born.series));

// A row reading a DATA TABLE asks that table the same question a dataset card
// asks: what to work out, of which column, one point per what, over which
// period. The column declaration travels ON the row, because that is what the
// server checks the two column names against.
const TABLE_META = {
  ...META,
  catalog: [{ origin: "table", label: "passes", kind: "line", dataset: "passes",
              // "datetime" is the word a DECLARATION uses; the store answers
              // with "timestamp" for the same column, so both have to be read
              // as a time column or the picker empties itself on reload.
              columns: [{ name: "ts", type: "timestamp" },
                        { name: "seen_at", type: "datetime" },
                        { name: "count", type: "number" },
                        { name: "lane", type: "text" }] }],
};
const datasetRow = inspect(chartWidget("line", {
  series: [{ id: "s1", source: "dataset", dataset: "passes",
             query: { agg: "avg" },
             columns: TABLE_META.catalog[0].columns }],
}), TABLE_META);
// Since the chart builder (2026-10-13) the question is the CHART's, asked
// once (X axis, Y axis, Date column) for every row; the row keeps its table,
// its name, its Show toggle and a collapsed override.
const rowDoc = new JSDOM(`<body>${datasetRow}</body>`).window.document;
const optionsOf = (aria) => [...(rowDoc.querySelector(`select[aria-label="${aria}"]`)?.options || [])]
  .map((o) => o.value);
check("a data-table row offers its table, and the chart asks the question once",
      ["Data table", "X axis", "Y axis", "Date column", "Override for this input"]
        .every((f) => datasetRow.includes(f))
      && !["Work out", "One point per", ">Period<", "Each point shows"]
        .some((f) => datasetRow.includes(f)),
      "a dataset series row was missing its table, or still repeats the question");
check("...its own Y calculation is an override, 'use the chart's' by default",
      rowDoc.querySelector('select[aria-label="Calculation for input 1"]')?.value === ""
      && rowDoc.querySelector("details.chart-override") && !rowDoc.querySelector("details.chart-override").open);
// Only a NUMBER can be averaged, so the chart's Y column list is the numeric
// columns alone.
check("...and the Y column offers only the numeric columns for an average",
      JSON.stringify(optionsOf("Of column")) === JSON.stringify(["", "count"]),
      JSON.stringify(optionsOf("Of column")));
// Both spellings of a time column are offered to date a row by; nothing else
// is, because the server keeps a ts_field only when its type is a time type.
check("...and both spellings of a time column can date the rows",
      JSON.stringify(optionsOf("Date column")) === JSON.stringify(["", "@row", "ts", "seen_at"]),
      JSON.stringify(optionsOf("Date column")));

// -- 2. the legacy single-key widget round-trips -----------------------------
const legacy = { id: "old", kind: "line", source: "history", key: "zone_a_count",
                 color: "#ff8800", query: {}, range: "24h" };
const migrated = mod.widgetSeries(legacy);
check("a single-key widget reads as exactly one series",
      migrated.length === 1 && migrated[0].key === "zone_a_count"
      && migrated[0].source === "history",
      JSON.stringify(migrated));
check("...and the colour somebody chose by hand survives the migration",
      migrated[0].color === "#ff8800", JSON.stringify(migrated[0]));
// ...and the row stays DERIVED. Saving used to write it onto the widget
// ("withSeries"), which moved the chart onto the server's series renderer:
// a shared 24h grid named after the key, instead of the card's own buckets
// and range under its own title (builderShared.jsx says the rest). The save
// now sends a chart exactly as it is, so the module has no save-side half.
check("saving no longer writes the derived row onto the widget",
      typeof mod.withSeries === "undefined", "withSeries is back");
// Re-reading the untouched widget gives the same one series back, colour
// and all: the editor derives it every time and loses nothing by doing so.
const again = mod.widgetSeries({ ...legacy });
check("...and re-reading the untouched widget gives the same series back",
      again.length === 1 && again[0].key === "zone_a_count"
      && again[0].color === "#ff8800", JSON.stringify(again));
// A widget that already HAS series is never re-derived from its key: the rows
// are the truth.
const kept = mod.widgetSeries({ ...legacy, series: [{ id: "a", key: "x", color: "#111111" }] });
check("a widget that already has series reads exactly those rows",
      kept.length === 1 && kept[0].color === "#111111" && kept[0].id === "a",
      JSON.stringify(kept));

// A compiled card's per-input names and colours are sent back to its node,
// keyed by the series' own id - a colour written onto the widget row would be
// thrown away by the next compile. Blanks travel too, or clearing a label
// would read as "no opinion" and the node would keep the old word.
const styled = mod.seriesStyle({
  id: "wf_abc", kind: "line", source: "history",
  series: [{ id: "s1", key: "a", label: "Zone A", color: "#ff8800" },
           { id: "s2", key: "b", label: "", color: "" }],
});
check("a compiled chart sends its per-input names and colours by series id",
      styled.s1.label === "Zone A" && styled.s1.color === "#ff8800"
      && styled.s2.label === "" && styled.s2.color === "",
      JSON.stringify(styled));
check("...and the editor says the names and colours are the part that stays",
      inspect({ ...chartWidget("line"), id: "wf_abc" }, META)
        .includes("saved back onto that node"),
      "a compiled chart's editor did not say where its inputs come from");

// A dataset row's blank timestamp column is a CHOICE - the row's ingest clock
// - so it is named rather than looking like a field nobody filled in.
check("a dataset row offers the ingest clock as the timestamp default",
      datasetRow.includes("when the row was saved"),
      "the blank timestamp column read as unset");

// -- 3. a second series turns per-day colouring off, and says so -------------
const oneSeries = inspect(chartWidget("line"), META);
check("one series: per-day colouring is offered",
      oneSeries.includes("Colour each day differently")
      && !oneSeries.includes("already say which series is which"),
      "the day toggle was explained away on a single-series chart");
const twoSeries = inspect(chartWidget("line", {
  series: [{ id: "s1", source: "history", key: "zone_a_count", agg: "avg" },
           { id: "s2", source: "history", key: "zone_b_count", agg: "avg" }],
}), META);
check("two series: per-day colouring is disabled",
      /<input[^>]*disabled[^>]*><span>Colour each day differently/.test(twoSeries),
      "the day toggle stayed clickable on a two-series chart");
// ...and the shared axis is spelled out, so an operator mixing sources knows
// their series will be resampled onto it before the picture tells them.
check("two series: the editor says what the shared axis will be",
      twoSeries.includes("share one time axis")
      && twoSeries.includes("left as a gap, not drawn as zero"),
      "a multi-source chart said nothing about how its inputs line up");
check("...and the editor says WHY, naming the count",
      twoSeries.includes("already say which series is which")
      && twoSeries.includes("2 series"),
      "a control was switched off with no explanation");

// The rule this mirrors lives in Widget.jsx - pinned as text, because a
// renderer that quietly widened it would leave the editor lying.
const widgetSrc = readText(join(FRONT, "src/components/widgets/Widget.jsx"));
check("Widget.jsx still paints by day on a single series only",
      widgetSrc.includes("widget.by_day && series.length === 1"),
      "the by_day gate moved - the editor's explanation now disagrees with it");

// -- 4. a missing point is a gap, not a zero ---------------------------------
const holed = {
  labels: ["a", "b", "c"],
  datasets: [{ label: "one", values: [1, null, 3] },
             { label: "two", values: [3, 2, 1] }],
};
const { rows, series } = mod.toRows(holed, "v", "#04bade");
check("a nulled point does not reach the chart as a number",
      rows[1].s0 === undefined && Number.isNaN(Number(rows[1].s0)),
      `row 1 held ${JSON.stringify(rows[1])}`);
check("...while the series beside it keeps its value",
      rows[1].s1 === 2, JSON.stringify(rows[1]));
check("...and both series are still described",
      series.length === 2 && series[0].label === "one",
      JSON.stringify(series));
// Drawn: with several series the line BREAKS at the hole. connectNulls would
// bridge it, which draws a value nobody recorded.
const gapped = draw({ id: "g", kind: "line", title: "T", template: "plain",
                      data: holed });
const curves = gapped.match(/<path[^>]*class="[^"]*recharts-curve[^"]*"[^>]*>/g) || [];
const broken = curves.some((c) => ((c.match(/M/g) || []).length > 1));
check("a hole in one series draws as a break in the line",
      broken, "every curve was one unbroken path - a null was bridged");
check("...and the chart still drew both series",
      (gapped.match(/recharts-line\b/g) || []).length >= 2
      || (gapped.match(/recharts-line-curve/g) || []).length >= 2,
      "a two-series line chart drew fewer than two lines");
// The legend names every series, or a second line is a mystery line.
// (The legend is a row of switches since the legend toggle, 2026-10-13:
// chart-legend-toggle buttons, one per series.)
check("the legend names every series",
      (gapped.match(/chart-legend-toggle/g) || []).length === 2 && gapped.includes("one")
      && gapped.includes("two"),
      "a multi-series chart drew no legend");

// -- 5. a series that could not be read is marked, by ID ---------------------
//
// The server answers with the series it COULD read plus a warning for each
// one it could not, so a mistyped key never blanks a chart that three other
// inputs are drawing on. The mark belongs to the series, which means it is
// keyed on the id - position moves when the editor's rows are reordered.
const warned = {
  labels: ["a", "b", "c"],
  datasets: [{ id: "s2", label: "two", values: [3, 2, 1] },
             { id: "s1", label: "one", values: [null, null, null] }],
  warnings: [{ id: "s1", label: "one", message: "no such key: zone_z_count" }],
};
const warnedHtml = draw({ id: "w", kind: "line", title: "T", template: "plain",
                          data: warned });
check("an unreadable series is marked on the legend",
      warnedHtml.includes("chart-warn")
      && warnedHtml.includes("no such key: zone_z_count"),
      "a warned series drew with nothing to say it was warned");
// The mark sits on the series the warning NAMES, whatever order the datasets
// arrive in - the whole reason the contract carries an id.
const items = warnedHtml.match(/<button[^>]*chart-legend-toggle[\s\S]*?<\/button>/g) || [];
const itemFor = (label) => items.find((li) => li.includes(`>${label}<`));
check("...on the series the warning names, not the one in that position",
      items.length === 2
      && (itemFor("one") || "").includes("chart-warn")
      && !(itemFor("two") || "x").includes("chart-warn"),
      items.join("\n"));
check("...and the series that DID read is still drawn",
      warnedHtml.includes("recharts-line"),
      "a warning blanked the whole chart");

// A card with nothing on it anywhere says why in its own quiet voice, and
// grows no badge: a mark that appears on every chart at every boot (the
// sampler takes a cycle to record anything) is a mark people learn to ignore.
const empty = draw({ id: "e", kind: "line", title: "T", template: "plain",
                     data: { labels: [], values: [],
                             warnings: [{ id: "s1", label: "one",
                                          message: "no samples recorded for this key" }] } });
check("a card that drew nothing explains itself instead of badging itself",
      empty.includes("no data yet - one: no samples recorded for this key")
      && !empty.includes("chart-warn"),
      empty.slice(0, 400));
// ...and the same while the axis exists but no series has a value on it.
const allEmpty = draw({ id: "e2", kind: "line", title: "T", template: "plain",
                        data: { labels: ["a", "b"],
                                datasets: [{ id: "s1", label: "one", values: [null, null] }],
                                warnings: [{ id: "s1", label: "one",
                                             message: "no samples recorded for this key" }] } });
check("...and an axis with no values on it does not badge every series either",
      !allEmpty.includes("chart-warn"),
      "every legend entry on an empty chart was marked");
// A ZERO is a reading, not a hole: the event store's zero-fill inside a
// bounded window means "nothing happened then", which is data.
const zeroes = draw({ id: "z", kind: "line", title: "T", template: "plain",
                      data: { labels: ["a", "b"],
                              datasets: [{ id: "s1", label: "one", values: [0, 0] },
                                         { id: "s2", label: "two", values: [null, null] }],
                              warnings: [{ id: "s2", label: "two",
                                           message: "dataset not found" }] } });
check("a series of zeroes counts as drawn, so its neighbour's warning shows",
      zeroes.includes("chart-warn") && zeroes.includes("dataset not found"),
      "zero was treated as no data");

// -- 6. eight unset colours are eight different colours ----------------------
const eight = {
  labels: ["a", "b"],
  datasets: Array.from({ length: 8 }, (_, i) => (
    { label: `s${i}`, values: [i, i + 1] })),
};
const colors = mod.toRows(eight, "v", "#04bade").series.map((s) => s.color);
check("eight series with no colour set get eight distinct colours",
      new Set(colors).size === 8, colors.join(" "));
check("...and the editor's swatch picks the same colour by position",
      colors.every((c, i) => c === mod.seriesColorAt(i)),
      "the editor would show a colour the chart does not draw");
check("...and every one of them is a real hex colour",
      colors.every((c) => /^#[0-9a-f]{6}$/i.test(c)), colors.join(" "));

rmSync(OUT, { force: true });
console.log(bad ? `\n${bad} failed` : "\nall series-editor checks passed");
process.exit(bad ? 1 : 0);
