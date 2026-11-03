// The chart legend as switches (owner, 2026-10-13, with a screenshot of a
// legend reading CAM 1 / CAM 2: "allow users to show or hide individual data
// series directly from the chart legend ... so users can focus on one camera
// or compare selected series without distraction ... across all applicable
// charts and graphs").
//
// The real Widget.jsx in jsdom, measured, read as SVG and DOM:
//
//   1. bar: pressing CAM 2 takes its bars off; the entry stays listed,
//      dimmed, struck through, aria-pressed=false; "Show all" appears
//   2. the last series shown cannot be hidden (the rule chosen over letting
//      the chart go blank); "Show all" brings everything back
//   3. the value axis rescales to what is shown; the tooltip lists only it
//   4. colours stay with their series, never with the visible position
//   5. the choice survives a data refresh and a reorder (keyed by id) and a
//      remount (sessionStorage); with no storage it still works
//   6. line, area, spline, radar: the series goes and comes back
//   7. pie / donut: the slice goes, the shares are worked out over the rest
//   8. a series hidden in the chart's SETTINGS is not in the data, so it is
//      not in the legend and cannot be switched back on from there
//   9. keyboard: every entry is a native button (Enter and Space)
//
//   node tests/check_chartlegend.mjs
import { readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".chartlegend-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
                 "MouseEvent", "Event", "sessionStorage"]) {
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
  const render = (w) => act(() => { root.render(h(Widget, { widget: w, accent: "#04bade" })); });
  render(widget);
  return { act, render, unmount: () => act(() => root.unmount()) };
}
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


const legend = (el) => [...el.querySelectorAll(".chart-legend-toggle")];
const entry = (el, label) => legend(el).find((b) => b.textContent.trim().startsWith(label));
const press = (c, b) => c.act(() => { b.click(); });
const rects = (el) => el.querySelectorAll(".recharts-bar-rectangle").length;
const yMax = (el) => Math.max(...[...el.querySelectorAll(".recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value")]
  .map((t) => Number(t.textContent.replace(/,/g, "").replace(/k$/, "000"))));
const TWO = { labels: ["Mon", "Tue", "Wed"],
              datasets: [{ id: "s1", label: "CAM 1", values: [10, 8, 9] },
                         { id: "s2", label: "CAM 2", values: [100, 80, 90] }] };
sessionStorage.clear();

// -- 1-4. bar --------------------------------------------------------------------
{
  const c = mount(card("bar", TWO, { id: "w-bar" }));
  const fillsBefore = [...c.el.querySelectorAll(".recharts-bar-rectangle path")]
    .map((p) => p.getAttribute("fill"));
  check("two series: six bars, both entries pressed (shown)",
        rects(c.el) === 6 && legend(c.el).every((b) => b.getAttribute("aria-pressed") === "true"),
        String(rects(c.el)));
  check("...and the value axis is scaled for the bigger camera", yMax(c.el) >= 100, String(yMax(c.el)));
  press(c, entry(c.el, "CAM 2"));
  const cam2 = entry(c.el, "CAM 2");
  check("pressing CAM 2 takes its bars off the chart", rects(c.el) === 3, String(rects(c.el)));
  check("...it stays in the legend, dimmed and struck through, aria-pressed=false",
        cam2 && cam2.getAttribute("aria-pressed") === "false"
        && cam2.style.textDecoration === "line-through" && Number(cam2.style.opacity) < 1
        && cam2.getAttribute("aria-label") === "CAM 2, hidden. Press to show.",
        cam2 && cam2.outerHTML);
  check("...and 'Show all' appears", !!c.el.querySelector(".chart-legend-all"));
  check("the value axis rescales to what is shown", yMax(c.el) < 20, String(yMax(c.el)));
  const fillsAfter = [...c.el.querySelectorAll(".recharts-bar-rectangle path")]
    .map((p) => p.getAttribute("fill"));
  check("CAM 1 keeps its colour", fillsAfter.length === 3
        && fillsAfter.every((f) => f === fillsBefore[0]), JSON.stringify([fillsBefore, fillsAfter]));
  c.act(() => {
    c.el.querySelectorAll(".recharts-bar-rectangle path")[1]
      .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  });
  const tip = c.el.querySelector("[data-testid='chart-popup']")?.textContent || "";
  check("the value box lists only the series shown", /CAM 1/.test(tip) && !/CAM 2/.test(tip), tip);
  const cam1 = entry(c.el, "CAM 1");
  check("CAM 1 is now the last one shown: it says so and cannot be hidden",
        cam1.getAttribute("aria-disabled") === "true"
        && cam1.getAttribute("aria-label") === "CAM 1, shown. The last series shown stays on the chart.");
  press(c, cam1);
  check("...pressing it changes nothing", rects(c.el) === 3
        && entry(c.el, "CAM 1").getAttribute("aria-pressed") === "true");
  // A refresh: new values, same ids -- and the inputs reordered.
  c.render(card("bar", { labels: ["Mon", "Tue", "Wed", "Thu"],
                         datasets: [{ id: "s2", label: "CAM 2", values: [1, 2, 3, 4] },
                                    { id: "s1", label: "CAM 1", values: [5, 6, 7, 8] }] },
                { id: "w-bar" }));
  check("a refresh (and a reorder) keeps CAM 2 hidden: it is keyed by the series id",
        rects(c.el) === 4 && entry(c.el, "CAM 2").getAttribute("aria-pressed") === "false",
        String(rects(c.el)));
  check("the choice is remembered for the session, never in the chart's settings",
        sessionStorage.getItem("iriv.chart.hidden.w-bar") === JSON.stringify(["s2"]),
        sessionStorage.getItem("iriv.chart.hidden.w-bar"));
  c.close();
  const again = mount(card("bar", TWO, { id: "w-bar" }));
  check("...so a remount (the next page load in this tab) still hides it",
        rects(again.el) === 3);
  press(again, again.el.querySelector(".chart-legend-all"));
  check("'Show all' brings everything back and clears the memory",
        rects(again.el) === 6 && !again.el.querySelector(".chart-legend-all")
        && sessionStorage.getItem("iriv.chart.hidden.w-bar") === null);
  press(again, entry(again.el, "CAM 1"));
  const fill2 = again.el.querySelector(".recharts-bar-rectangle path")?.getAttribute("fill");
  check("hiding CAM 1 leaves CAM 2 in its own colour (not CAM 1's)",
        fill2 === "#f59e0b", fill2);
  again.close();
}

// -- 6. line, area, spline, radar --------------------------------------------------------
for (const [kind, sel] of [["line", ".recharts-line-curve"], ["area", ".recharts-area-curve"],
                           ["spline", ".recharts-area-curve, .recharts-line-curve"],
                           ["radar", ".recharts-radar-polygon"]]) {
  sessionStorage.clear();
  const c = mount(card(kind, TWO, { id: `w-${kind}` }));
  const before = c.el.querySelectorAll(sel).length;
  press(c, entry(c.el, "CAM 1"));
  const after = c.el.querySelectorAll(sel).length;
  press(c, entry(c.el, "CAM 1"));
  const back = c.el.querySelectorAll(sel).length;
  check(`${kind}: pressing CAM 1 takes it off the chart, pressing again brings it back`,
        before >= 2 && after === before / 2 && back === before, `${before} -> ${after} -> ${back}`);
  c.close();
}

// -- 7. pie / donut ---------------------------------------------------------------------
for (const kind of ["pie", "donut"]) {
  sessionStorage.clear();
  const share = { labels: ["OK", "VIOLATION", "NO PANEL"], values: [12, 6, 2] };
  const c = mount(card(kind, share, { id: `w-${kind}` }));
  const sectors = () => c.el.querySelectorAll(".recharts-pie-sector").length;
  const before = sectors();
  press(c, entry(c.el, "VIOLATION"));
  const labels = [...c.el.querySelectorAll(".recharts-pie-label-text")].map((t) => t.textContent);
  check(`${kind}: pressing a slice's entry takes the slice off`, before === 3 && sectors() === 2,
        `${before} -> ${sectors()}`);
  check(`${kind}: ...and the shares are worked out over the slices shown (12 of 14 = 86%)`,
        labels.includes("OK: 12 (86%)") && labels.includes("NO PANEL: 2 (14%)"), JSON.stringify(labels));
  c.close();
}
{
  sessionStorage.clear();
  const share = { labels: ["OK", "VIOLATION"], values: [12, 6] };
  const c = mount(card("donut", share, { id: "w-dl", template: "legend" }));
  const rows = () => [...c.el.querySelectorAll(".chart-legend-toggle")];
  press(c, rows().find((b) => b.textContent.includes("VIOLATION")));
  check("donut 'legend' template: its rows are the switches, the centre total follows",
        c.el.textContent.includes("12") && !c.el.textContent.includes("18")
        && rows().find((b) => b.textContent.includes("VIOLATION")).getAttribute("aria-pressed") === "false");
  c.close();
}

// -- 8. hidden in the settings -----------------------------------------------------------
{
  sessionStorage.clear();
  // The server leaves an input hidden in the settings out of the data (it is
  // not even queried, chart_series.resolve): the legend never offers it.
  const c = mount(card("bar", { labels: ["Mon"], datasets: [{ id: "s1", label: "CAM 1", values: [3] }] },
                       { id: "w-cfg" }));
  check("a series hidden in the chart's settings is not in the legend, so it cannot be "
        + "switched back on there; the one shown cannot be hidden either",
        legend(c.el).length === 1 && !entry(c.el, "CAM 2")
        && entry(c.el, "CAM 1").getAttribute("aria-disabled") === "true");
  c.close();
}

// -- 9. keyboard --------------------------------------------------------------------------
{
  const c = mount(card("line", TWO, { id: "w-kb" }));
  const all = legend(c.el);
  check("every entry is a native button (focusable, Enter and Space press it) with its state",
        all.length === 2 && all.every((b) => b.tagName === "BUTTON" && b.type === "button"
          && b.hasAttribute("aria-pressed") && b.getAttribute("aria-label").includes("Press to"))
        && c.el.querySelector(".chart-legend").getAttribute("role") === "group");
  c.close();
}

// -- 5b. no storage ------------------------------------------------------------------------
{
  Object.defineProperty(window, "sessionStorage", { configurable: true,
    get() { throw new Error("storage blocked"); } });
  let ok = true;
  try {
    const c = mount(card("bar", TWO, { id: "w-nostore" }));
    press(c, entry(c.el, "CAM 2"));
    ok = rects(c.el) === 3;
    c.close();
  } catch (e) {
    ok = false;
  }
  check("with no storage at all the legend still works (the choice lasts while on screen)", ok);
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
