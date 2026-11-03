// Charts drawn so the axes and every detail can be read (rendering addendum
// to the chart builder brief, owner 2026-10-13: "I want the graph and chart
// should be like this, better, where the user can see the X and Y axis
// clearly, and every detail" -- a vertical bar chart with titled axes, nice
// ticks, light gridlines and rotated category names, and a horizontal
// lollipop chart with the categories down the left).
//
// The real Widget.jsx is mounted in jsdom (react-dom/client, effects run --
// recharts draws nothing under SSR), with ResponsiveContainer handing it a
// measured size the way the browser does (onResize), and the SVG is read:
//
//   1. axis titles on both axes, from the configuration (server `axis`) or
//      the operator's own; tick labels, axis lines, light gridlines
//   2. long category names: rotated, cut with an ellipsis, thinned so they
//      never overlap; a time axis stays flat and thins
//   3. a horizontal bar chart: categories on the left, vertical gridlines;
//      the lollipop style: a line and a dot per value
//   4. values on the chart with the unit (automatic for few points, on/off)
//   5. legend with every series name; the tooltip says the series, the full
//      X value and the value with its unit
//   6. pie / donut labels with value and share, a legend; radar's scale
//   7. a small tile: no axis titles, no values, thinned ticks
//   8. the theme paints the axis text from its own tokens (light and dark)
//
//   node tests/check_chartrender.mjs
import { readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".chartrender-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
                 "MouseEvent", "Event"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// ResponsiveContainer measures its parent and jsdom says 0x0: this one hands
// the chart a size AND reports it through onResize, as the browser would.
// globalThis.__SIZE__ lets a case pick a big card or a small tile.
const shim = {
  name: "shim-responsive-container",
  setup(b) {
    b.onResolve({ filter: /^recharts$/ }, (args) => (
      args.importer.replace(/\\/g, "/").endsWith("widgets/Widget.jsx")
        ? { path: "recharts-shim", namespace: "shim" } : null));
    b.onLoad({ filter: /^recharts-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement, cloneElement, useEffect } from "react";
        export * from "recharts";
        export function ResponsiveContainer({ children, onResize }) {
          const size = globalThis.__SIZE__ || { width: 640, height: 320 };
          useEffect(() => { if (onResize) onResize(size.width, size.height); },
                    [size.width, size.height]);
          return createElement("div", { className: "rc-shim" },
                               cloneElement(children, size));
        }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import Widget from "${join(FRONT, "src/components/widgets/Widget.jsx").replace(/\\/g, "/")}";
export function draw(el, widget) {
  const root = createRoot(el);
  act(() => { root.render(h(Widget, { widget, accent: "#04bade" })); });
  return { act, unmount: () => act(() => root.unmount()) };
}
export { nearestLine } from "${join(FRONT, "src/components/widgets/Widget.jsx").replace(/\\/g, "/")}";
export { fmtValue, categoryLayout, showValues, truncateLabel }
  from "${join(FRONT, "src/components/charts.js").replace(/\\/g, "/")}";
`;
await build({
  plugins: [shim], stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic",
  logLevel: "silent", define: { "process.env.NODE_ENV": '"development"' },
});
const mod = req(OUT);

/** Mount a widget, let it measure, and hand back its DOM (left mounted). */
function mount(widget, size = { width: 640, height: 320 }) {
  globalThis.__SIZE__ = size;
  const el = document.body.appendChild(document.createElement("div"));
  const m = mod.draw(el, widget);
  return { el, ...m, close: () => { m.unmount(); el.remove(); } };
}
const texts = (el, sel) => [...el.querySelectorAll(sel)].map((n) => n.textContent.trim());
const card = (kind, data, extra = {}) => ({ id: `w-${kind}`, kind, title: "Panels",
                                            template: "plain", span: 2, data, ...extra });

// -- 1. a vertical bar chart: titled axes, ticks, lines, gridlines, values ------
const STATUS = { labels: ["OK", "VIOLATION", "NO PANEL"],
                 datasets: [{ id: "s1", label: "CAM 1", values: [12, 3, 1] },
                            { id: "s2", label: "CAM 2", values: [9, 5, 0] }],
                 unit: "panels" };
{
  const c = mount(card("bar", STATUS, { unit: "panels",
                                        axis: { x: "status", y: "Total rows (panels)", time: false } }));
  const titles = texts(c.el, ".chart-axis-title");
  check("both axes are titled from the configuration (X the column, Y the calculation and unit)",
        titles.includes("status") && titles.includes("Total rows (panels)"), JSON.stringify(titles));
  const ticks = texts(c.el, ".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value");
  check("every category is a tick label under its bars", ["OK", "VIOLATION", "NO PANEL"]
        .every((t) => ticks.includes(t)), JSON.stringify(ticks));
  const yTicks = texts(c.el, ".recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value");
  check("the value axis has nice ticks from zero",
        yTicks[0] === "0" && yTicks.length >= 3 && yTicks.every((t) => /^[0-9.,kM]+$/.test(t)),
        JSON.stringify(yTicks));
  check("axis lines and tick marks are drawn",
        c.el.querySelectorAll(".recharts-cartesian-axis-line").length === 2
        && c.el.querySelectorAll(".recharts-cartesian-axis-tick-line").length >= 3);
  check("light horizontal gridlines, no vertical ones on a vertical bar chart",
        c.el.querySelectorAll(".recharts-cartesian-grid-horizontal line").length >= 3
        && c.el.querySelectorAll(".recharts-cartesian-grid-vertical line").length === 0);
  const values = texts(c.el, ".chart-value-label");
  check("few bars: automatic prints no value on the chart (it shows on hover)",
        values.length === 0, JSON.stringify(values));
  const legend = texts(c.el, ".chart-legend-item");
  check("the legend names every series (camera)", legend.join() === "CAM 1,CAM 2",
        JSON.stringify(legend));
  // THE VALUE BOX: no hover tooltip; a click on a bar opens it (ChartPopup).
  check("no value box floats on hover",
        !c.el.querySelector(".recharts-tooltip-wrapper .recharts-default-tooltip")
        && !c.el.querySelector("[data-testid='chart-popup']"));
  const bars = [...c.el.querySelectorAll(".recharts-bar-rectangle path")];
  c.act(() => { bars[1].dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  const tip = c.el.querySelector("[data-testid='chart-popup']")?.textContent || "";
  check("clicking a bar says the category, each series and its value with the unit",
        /VIOLATION/.test(tip) && /CAM 1/.test(tip) && /3 panels/.test(tip)
        && /CAM 2/.test(tip) && /5 panels/.test(tip), tip || "(no value box drawn)");
  check("...and dims the other bars",
        [...c.el.querySelectorAll(".recharts-bar-rectangle path")]
          .some((p) => /0\.3/.test(p.getAttribute("fill-opacity") || "")));
  c.act(() => { document.body.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  check("a click outside closes it and brings the bars back",
        !c.el.querySelector("[data-testid='chart-popup']"));
  c.close();
}
{
  const c = mount(card("bar", STATUS, { x_title: "Inspection result", y_title: "Panels",
                                        axis: { x: "status", y: "Total rows", time: false } }));
  const titles = texts(c.el, ".chart-axis-title");
  check("the operator's own axis titles win over the automatic ones",
        titles.includes("Inspection result") && titles.includes("Panels")
        && !titles.includes("status"), JSON.stringify(titles));
  c.close();
}

// -- 2. long category names: rotated, cut, thinned --------------------------------
{
  const names = Array.from({ length: 24 }, (_, i) => `Production line ${i + 1} - north wing`);
  const c = mount(card("bar", { labels: names, values: names.map((_, i) => i + 1) },
                       { axis: { x: "line", y: "Total rows", time: false } }));
  const tickEls = [...c.el.querySelectorAll(".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value")];
  const rotated = tickEls.filter((t) => /rotate\(-(90|45)/.test(t.getAttribute("transform") || ""));
  check("long category names are rotated (90 or 45 degrees)",
        tickEls.length > 0 && rotated.length === tickEls.length,
        tickEls.map((t) => t.getAttribute("transform")).slice(0, 2).join(" | "));
  check("...cut with an ellipsis rather than clipped",
        tickEls.every((t) => t.textContent.length <= 18) && tickEls.some((t) => t.textContent.endsWith("…")),
        JSON.stringify(tickEls.slice(0, 2).map((t) => t.textContent)));
  check("...one upright label per bar while they have room",
        tickEls.length === names.length, String(tickEls.length));
  check("...with the axis tall enough for them (the plot ends above the labels)",
        Number(c.el.querySelector(".recharts-xAxis .recharts-cartesian-axis-line")?.getAttribute("y1"))
          <= 320 - 100, c.el.querySelector(".recharts-xAxis .recharts-cartesian-axis-line")?.getAttribute("y1"));
  c.close();
  const crowd = Array.from({ length: 90 }, (_, i) => `Station ${i + 1}`);
  const cc = mount(card("bar", { labels: crowd, values: crowd.map((_, i) => i) }));
  const ct = cc.el.querySelectorAll(".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value");
  check("90 categories: thinned so the labels never overlap (fewer ticks than bars)",
        ct.length < crowd.length && ct.length >= 10, String(ct.length));
  cc.close();
  const hours = Array.from({ length: 96 }, (_, i) => i);
  const t0 = new Date(2026, 9, 13, 0, 0).getTime();
  const tc = mount(card("line", { labels: hours.map(String), values: hours,
                                  keys: hours.map((i) => t0 + i * 900000), step_s: 900,
                                  step: "Every 15 minutes" },
                        { axis: { x: "Time", y: "Average total panel", time: true } }));
  const tt = [...tc.el.querySelectorAll(".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value")];
  check("a time axis stays level and thins (every few points), titled by its step",
        tt.length > 3 && tt.length < 96
        && tt.every((t) => !/rotate\(-/.test(t.getAttribute("transform") || ""))
        && texts(tc.el, ".chart-axis-title").includes("Every 15 minutes"),
        `${tt.length} ticks; ${texts(tc.el, ".chart-axis-title")}`);
  check("...with no value labels on a dense graph",
        tc.el.querySelectorAll(".chart-value-label").length === 0);
  tc.close();
}

// -- 3. horizontal bars and the lollipop ------------------------------------------
{
  const parties = { labels: ["Democratic Party", "Republican Party", "Libertarian", "Green"],
                    values: [48.2, 46.1, 3.3, 1.1], unit: "%" };
  const c = mount(card("bar", parties, { orientation: "horizontal", bar_style: "lollipop", unit: "%",
                                         axis: { x: "party", y: "Share of the vote (%)", time: false } }));
  const left = texts(c.el, ".recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value");
  check("horizontal: the categories are listed down the left",
        ["Democratic Party", "Green"].every((p) => left.includes(p)), JSON.stringify(left));
  const bottom = texts(c.el, ".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value");
  check("...the values run along the bottom, from zero",
        bottom[0] === "0" && bottom.length >= 3, JSON.stringify(bottom));
  check("...with vertical gridlines",
        c.el.querySelectorAll(".recharts-cartesian-grid-vertical line").length >= 3
        && c.el.querySelectorAll(".recharts-cartesian-grid-horizontal line").length === 0);
  const pops = c.el.querySelectorAll(".chart-lollipop");
  check("lollipop: a stem and a dot for each value, no filled bar",
        pops.length === 4 && [...pops].every((g) => g.querySelector("line") && g.querySelector("circle"))
        && c.el.querySelectorAll(".recharts-bar-rectangle path").length === 0,
        String(pops.length));
  check("...titled: the measure along the bottom, the categories on the left",
        texts(c.el, ".chart-axis-title").join("|") === "Share of the vote (%)|party"
        || texts(c.el, ".chart-axis-title").sort().join("|") === "Share of the vote (%)|party",
        JSON.stringify(texts(c.el, ".chart-axis-title")));
  check("...and no value printed at the end of each line unless asked (hover shows it)",
        texts(c.el, ".chart-value-label").length === 0,
        JSON.stringify(texts(c.el, ".chart-value-label")));
  c.close();
}

// -- 4. values on the chart: off, on --------------------------------------------------
{
  const off = mount(card("bar", STATUS, { data_labels: "off" }));
  check("Show values: Off prints none", off.el.querySelectorAll(".chart-value-label").length === 0);
  off.close();
  const many = { labels: Array.from({ length: 30 }, (_, i) => `d${i}`),
                 values: Array.from({ length: 30 }, (_, i) => i) };
  const auto = mount(card("bar", many));
  check("Automatic prints none on a dense chart (30 bars)",
        auto.el.querySelectorAll(".chart-value-label").length === 0);
  auto.close();
  const on = mount(card("line", { labels: ["a", "b", "c"], values: [1, 2, 3] },
                        { data_labels: "on" }));
  check("On prints the value at each point of a line",
        texts(on.el, ".chart-value-label").join() === "1,2,3",
        JSON.stringify(texts(on.el, ".chart-value-label")));
  on.close();
}

// -- 5/6. pie, donut, radar ------------------------------------------------------------
{
  const share = { labels: ["OK", "VIOLATION", "NO PANEL"], values: [12, 6, 2], unit: "panels" };
  const p = mount(card("pie", share, { unit: "panels" }));
  const lab = texts(p.el, ".recharts-pie-label-text");
  check("pie: each slice labelled with its category, value and share",
        lab.includes("OK: 12 panels (60%)") && lab.includes("NO PANEL: 2 panels (10%)"),
        JSON.stringify(lab));
  check("...and a legend of the categories",
        texts(p.el, ".chart-legend-item").join() === "OK,VIOLATION,NO PANEL",
        JSON.stringify(texts(p.el, ".chart-legend-item")));
  p.close();
  const d = mount(card("donut", share));
  check("donut: the same labels and legend",
        texts(d.el, ".recharts-pie-label-text").length === 3
        && texts(d.el, ".chart-legend-item").length === 3);
  d.close();
  const r = mount(card("radar", { labels: ["speed", "quality", "uptime"],
                                  datasets: [{ id: "s1", label: "CAM 1", values: [3, 4, 5] },
                                             { id: "s2", label: "CAM 2", values: [5, 2, 4] }] }));
  check("radar: a value scale, the spokes named, a legend of the cameras",
        r.el.querySelector(".recharts-polar-radius-axis")
        && texts(r.el, ".recharts-polar-angle-axis-tick-value").join() === "speed,quality,uptime"
        && texts(r.el, ".chart-legend-item").join() === "CAM 1,CAM 2");
  r.close();
}


// -- 6b. click a slice or a radar shape: its value box opens, the rest dim -----------------
{
  const share = { labels: ["OK", "VIOLATION", "NO PANEL"], values: [12, 6, 2], unit: "panels" };
  for (const kind of ["pie", "donut"]) {
    const p = mount(card(kind, share, { unit: "panels", id: "w-" + kind }));
    check(`${kind}: no value box until a slice is clicked`,
          !p.el.querySelector("[data-testid='chart-popup']")
          && !p.el.querySelector(".recharts-tooltip-wrapper .recharts-default-tooltip"));
    const slices = [...p.el.querySelectorAll(".recharts-pie-sector path, path.recharts-sector")];
    if (slices.length >= 2) {
      p.act(() => { slices[1].dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
      const box = p.el.querySelector("[data-testid='chart-popup']")?.textContent || "";
      check(`${kind}: clicking a slice opens its value`, /VIOLATION: 6 panels/.test(box), box);
      check(`${kind}: ...and dims the other slices`,
            [...p.el.querySelectorAll("path.recharts-sector")]
              .some((n) => /0\.3/.test(n.getAttribute("fill-opacity") || "")));
      p.act(() => { document.body.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
      check(`${kind}: a click outside closes it`, !p.el.querySelector("[data-testid='chart-popup']"));
    } else {
      check(`${kind}: slices to click are drawn`, false, String(slices.length));
    }
    p.close();
  }
  const r = mount(card("radar", { labels: ["speed", "quality", "uptime"],
                                  datasets: [{ id: "s1", label: "CAM 1", values: [3, 4, 5] },
                                             { id: "s2", label: "CAM 2", values: [5, 2, 4] }] },
                       { id: "w-radar" }));
  const shapes = [...r.el.querySelectorAll(".recharts-radar-polygon .recharts-polygon")];
  if (shapes.length >= 2) {
    r.act(() => { shapes[1].dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
    const box = r.el.querySelector("[data-testid='chart-popup']")?.textContent || "";
    check("radar: clicking a shape opens its values", /CAM 2/.test(box) && /speed: 5/.test(box), box);
    check("...and dims the other shape",
          [...r.el.querySelectorAll(".recharts-radar-polygon .recharts-polygon")]
            .some((n) => /0\.2/.test(n.getAttribute("opacity") || "")));
  } else {
    check("radar: shapes to click are drawn", false, String(shapes.length));
  }
  r.close();
}

// -- 7. a small dashboard tile ------------------------------------------------------------
{
  const c = mount(card("bar", STATUS, { axis: { x: "status", y: "Total rows", time: false } }),
                  { width: 240, height: 140 });
  check("small tile: the axis titles step aside rather than overlap",
        c.el.querySelectorAll(".chart-axis-title").length === 0);
  check("...the values are not printed", c.el.querySelectorAll(".chart-value-label").length === 0);
  check("...the ticks are still there",
        c.el.querySelectorAll(".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value").length >= 1);
  c.close();
}

// -- helpers ------------------------------------------------------------------------------------
check("fmtValue: grouped thousands, two decimals, k and M",
      mod.fmtValue(1234) === "1,234" && mod.fmtValue(3.14159) === "3.14"
      && mod.fmtValue(15300) === "15.3k" && mod.fmtValue(2500000) === "2.5M"
      && mod.fmtValue(null) === "");
const flat = mod.categoryLayout(["a", "b"], 400);
const slant = mod.categoryLayout(Array(20).fill("Bay 12"), 300);
const tall = mod.categoryLayout(Array(20).fill("A very long category name"), 300);
check("categoryLayout: flat while it fits, 45 degrees when short, 90 when long",
      flat.angle === 0 && slant.angle === -45 && tall.angle === -90 && tall.height <= 124,
      JSON.stringify({ flat, slant, tall }));

// -- 7b. click a line: it is picked, the other dims, its values print -------------------------
{
  const two = { labels: ["a", "b", "c"], keys: ["2026-10-01", "2026-10-02", "2026-10-03"],
                datasets: [{ id: "s1", label: "CAM 1", values: [1, 5, 2] },
                           { id: "s2", label: "CAM 2", values: [4, 2, 6] }] };
  const c = mount(card("line", two, { id: "wl", axis: { x: "day", y: "rows", time: true } }));
  const curves = () => [...c.el.querySelectorAll(".recharts-line-curve")];
  check("a line chart shows no value labels until a line is clicked",
        c.el.querySelectorAll(".chart-value-label").length === 0);
  if (curves().length >= 2) {
    c.act(() => { curves()[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
    check("clicking a line prints its values", c.el.querySelectorAll(".chart-value-label").length > 0);
    check("...and dims the other line",
          curves().some((n) => /0\.18/.test(n.getAttribute("opacity") || "")));
    c.act(() => { document.body.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
    check("a click outside the chart puts it back",
          c.el.querySelectorAll(".chart-value-label").length === 0
          && !curves().some((n) => /0\.18/.test(n.getAttribute("opacity") || "")));
  } else {
    check("two line curves are drawn to click", false, String(curves().length));
  }
  c.close();
}

// -- 7c. the same for an AREA card with two lines (the weekly Total Violation card) ------------
{
  const two = { labels: ["a", "b", "c"], keys: ["2026-10-01", "2026-10-02", "2026-10-03"],
                datasets: [{ id: "s1", label: "CAM 1", values: [1, 5, 2] },
                           { id: "s2", label: "CAM 2", values: [4, 2, 6] }] };
  for (const extra of [{}, { template: "forecast" }, { template: "stripe" }]) {
    const tag = Object.keys(extra)[0] || "plain";
    const c = mount(card("area", two, { id: "wa-" + tag, axis: { x: "day", y: "rows", time: true }, ...extra }));
    const hit = () => c.el.querySelector(".chart-ser-1 .recharts-line-curve, .chart-ser-1 .recharts-area-curve, path.chart-ser-1");
    check(`area (${tag}): every line carries its own class`, !!c.el.querySelector(".chart-ser-0") && !!c.el.querySelector(".chart-ser-1"));
    if (hit()) {
      c.act(() => { hit().dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
      check(`area (${tag}): clicking the second line prints its numbers`, c.el.querySelectorAll(".chart-value-label").length > 0);
      check(`area (${tag}): ...and dims the first`,
            [...c.el.querySelectorAll(".chart-ser-0")].some((n) => /0\.18/.test(n.getAttribute("opacity") || n.querySelector("path")?.getAttribute("opacity") || "")));
      c.act(() => { document.body.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
      check(`area (${tag}): a click outside puts it back`, c.el.querySelectorAll(".chart-value-label").length === 0);
    } else {
      check(`area (${tag}): a line to click is drawn`, false);
    }
    c.close();
  }
}


// -- 7d. which line a click means: near a line, or inside its filled area ----------------------
{
  const flat = (y) => ({ getTotalLength: () => 100, getPointAtLength: (l) => ({ x: l, y }) });
  const fake = (lines) => ({
    querySelector: () => ({ getScreenCTM: () => ({ inverse: () => ({}) }),
                            createSVGPoint: () => ({ x: 0, y: 0, matrixTransform() { return { x: this.x, y: this.y }; } }) }),
    querySelectorAll: (sel) => { const m = /chart-ser-(\d)/.exec(sel); return m && lines[Number(m[1])] ? [lines[Number(m[1])]] : []; },
  });
  const click = (x, y) => ({ clientX: x, clientY: y, target: { closest: () => null } });
  const ref = fake([flat(100), flat(40)]);        // line 0 low on the card, line 1 high
  check("a click on a line picks it", mod.nearestLine(ref, click(50, 95), 2) === 0);
  check("a click in empty space picks nothing on a plain line chart",
        mod.nearestLine(ref, click(50, 70), 2) === null);
  check("inside a filled area it picks the line above the click",
        mod.nearestLine(ref, click(50, 70), 2, true) === 1);
  check("...under both lines, the nearer one above",
        mod.nearestLine(ref, click(50, 130), 2, true) === 0);
  check("...above every line, nothing (the chart resets)",
        mod.nearestLine(ref, click(50, 10), 2, true) === null);
}

// -- 8. light and dark ---------------------------------------------------------------------------
const css = readFileSync(join(FRONT, "src", "theme.css"), "utf8");
check("the theme paints tick labels and axis titles from its own text tokens",
      /\.chart-wrap \.recharts-cartesian-axis-tick-value[^{]*\{[^}]*var\(--muted-text/.test(css)
      && /\.chart-axis-title[^{]*\{[^}]*var\(--text/.test(css));

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
