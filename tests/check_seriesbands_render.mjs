// Thresholds per camera, drawn (owner, 2026-10-13: "configure threshold rules
// and colours separately for each data series or camera ... These settings
// should apply only to the corresponding camera's data series").
//
// The real Widget.jsx in jsdom, measured, read as SVG:
//   - bars: each camera's bars painted by ITS bands (CAM 1 below 10 yellow,
//     over 10 red; CAM 2 below 50 green, over 50 blue), its limits named
//   - no cross-talk: a camera with none of its own keeps its series colour
//   - one series keeps the shared bands as before
//   - line / area / spline: one band gradient per camera, its limits named
//   - a camera hidden from the legend draws neither its data nor its limits
//
//   node tests/check_seriesbands_render.mjs
import { readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".seriesbands-render-test.cjs");

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



const YEL = "#ffff00", RED = "#ff0000", GRN = "#00aa00", BLU = "#0000ff";
const OWN = { labels: ["Mon", "Tue"],
              datasets: [{ id: "s1", label: "CAM 1", values: [5, 15],
                           bands: { at: [10], colors: [YEL, RED] } },
                         { id: "s2", label: "CAM 2", values: [40, 60],
                           bands: { at: [50], colors: [GRN, BLU] } }] };
const fills = (el) => [...el.querySelectorAll(".recharts-bar-rectangle path")]
  .map((p) => (p.getAttribute("fill") || "").toLowerCase());
// (recharts 3 draws reference-line labels in its own z-index layer.)
const lineLabels = (el) => [...el.querySelectorAll(".recharts-label")]
  .map((t) => t.textContent);
sessionStorage.clear();

// -- bars: each camera's bars by its own bands -------------------------------------
{
  const c = mount(card("bar", OWN, { id: "sb-bar" }));
  const f = fills(c.el);
  check("bar: CAM 1 below 10 yellow, over 10 red; CAM 2 below 50 green, over 50 blue",
        JSON.stringify(f) === JSON.stringify([YEL, RED, GRN, BLU]), JSON.stringify(f));
  check("...each camera's limit drawn in its name",
        lineLabels(c.el).includes("CAM 1: 10") && lineLabels(c.el).includes("CAM 2: 50"),
        JSON.stringify(lineLabels(c.el)));
  check("...the legend keeps each camera's own series colour",
        [...c.el.querySelectorAll(".chart-legend-toggle i")].map((i) => i.style.background)
          .join() === "rgb(4, 186, 222),rgb(245, 158, 11)");
  // Hiding CAM 1 from the legend: its bars and its limit go, CAM 2's stay.
  c.act(() => { [...c.el.querySelectorAll(".chart-legend-toggle")][0].click(); });
  check("legend-hidden CAM 1: its bars and its limit are not drawn, CAM 2's are",
        JSON.stringify(fills(c.el)) === JSON.stringify([GRN, BLU])
        && !lineLabels(c.el).includes("CAM 1: 10") && lineLabels(c.el).includes("CAM 2: 50"),
        JSON.stringify([fills(c.el), lineLabels(c.el)]));
  c.close();
}
// -- inheritance -------------------------------------------------------------------------
{
  sessionStorage.clear();
  const inherit = { ...OWN, datasets: [OWN.datasets[0], { ...OWN.datasets[1], bands: undefined }] };
  const c = mount(card("bar", inherit, { id: "sb-inh", bands: { at: [1000], colors: ["#123456", "#654321"] } }));
  const f = fills(c.el);
  check("a camera with no thresholds of its own is not painted by CAM 1's (no cross-talk), "
        + "and the shared ones do not repaint a several-series chart",
        f[0] === YEL && f[1] === RED && f[2] === "#f59e0b" && f[3] === "#f59e0b", JSON.stringify(f));
  c.close();
  const one = mount(card("bar", { labels: ["a", "b"], values: [5, 15] },
                        { id: "sb-one", bands: { at: [10], colors: [YEL, RED] } }));
  check("one series and the shared thresholds: its bars by the shared bands",
        JSON.stringify(fills(one.el)) === JSON.stringify([YEL, RED]), JSON.stringify(fills(one.el)));
  one.close();
}
// -- line and area: one gradient per camera --------------------------------------------------
for (const kind of ["line", "area", "spline"]) {
  sessionStorage.clear();
  const c = mount(card(kind, OWN, { id: `sb-${kind}` }));
  const grad = (k) => [...(c.el.querySelector(`linearGradient[id="wg-sb-${kind}-bands-${k}"]`)
    ?.querySelectorAll("stop") || [])].map((s) => s.getAttribute("stop-color").toLowerCase());
  const strokes = [...c.el.querySelectorAll(".recharts-line-curve, .recharts-area-curve")]
    .map((p) => p.getAttribute("stroke"));
  check(`${kind}: each camera's line is coloured by its OWN bands (its own gradient)`,
        grad("s0").includes(YEL) && grad("s0").includes(RED) && !grad("s0").includes(GRN)
        && grad("s1").includes(GRN) && grad("s1").includes(BLU) && !grad("s1").includes(RED)
        && strokes.includes(`url(#wg-sb-${kind}-bands-s0)`) && strokes.includes(`url(#wg-sb-${kind}-bands-s1)`),
        JSON.stringify({ s0: grad("s0"), s1: grad("s1"), strokes }));
  check(`${kind}: ...and each camera's limit drawn, named`,
        lineLabels(c.el).includes("CAM 1: 10") && lineLabels(c.el).includes("CAM 2: 50"),
        JSON.stringify(lineLabels(c.el)));
  c.close();
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
