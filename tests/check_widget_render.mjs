// Every widget kind must actually DRAW.
//
// A build passing proves the modules parse and resolve; it proves nothing
// about whether a chart appears. That gap is exactly where the Chart.js ->
// recharts migration could have left a hole: seven chart kinds, eight
// template treatments, and no browser in this repo to look at any of them.
//
// Server rendering is NOT enough to close it. recharts 3 emits only its
// wrapper <div> under renderToString -- the chart body needs client effects,
// so an SSR test reports "rendered fine" for a chart that draws nothing.
// (Measured: a LineChart with explicit width/height SSRs to 127 bytes of
// empty wrapper.) So this mounts into jsdom with react-dom/client and lets
// effects run, which is the point at which recharts produces real SVG.
//
//   node tests/check_widget_render.mjs
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
// esbuild, react, react-dom and jsdom are the FRONTEND's dependencies -- this
// repo root has no node_modules of its own, so resolve from there rather than
// making the whole test suite depend on a root-level install.
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".widget-render-test.cjs");

// -- a DOM for React to mount into -------------------------------------------
const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
// navigator is a getter-only property on modern node globals, so it cannot be
// assigned the way the others can.
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle"]) {
  global[k] = dom.window[k];
}
// Neither is implemented by jsdom; recharts and the stream widget both look
// for them, and an absent constructor is a throw rather than a no-op.
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
global.IS_REACT_ACT_ENVIRONMENT = true;

// Every kind the builder offers, with data shaped the way the server sends it.
const SERIES = { labels: ["a", "b", "c"], values: [1, 2, 3] };
const MULTI = {
  labels: ["a", "b", "c"],
  datasets: [{ label: "one", values: [1, 2, 3] },
             { label: "two", values: [3, 2, 1] }],
};
const CASES = [
  ["tile", { value: 42, sub: "things" }],
  ["tile", { value: 42, delta: { pct: 3.1, was: "12,400", since: "last month" } }],
  ["table", { columns: ["a", "b"], rows: [[1, 2], [3, 4]] }],
  ["timeline", { rows: [["10:00", "something"]] }],
  ["gauge", { value: 30, max: 100, unit: "%" }],
  ["status", { state: "alert", label: "HOT", value: 9 }],
  ["verdict", { state: "ok", label: "CLEAR" }],
  ["line", SERIES], ["area", SERIES], ["spline", SERIES], ["bar", SERIES],
  ["donut", SERIES], ["pie", SERIES], ["radar", SERIES],
  ["line", MULTI], ["bar", MULTI], ["area", MULTI],
];
// Templates crossed with the kinds they dress.
const TEMPLATES = ["plain", "accent", "outline", "soft",
                   "markers", "dashed", "gradient", "legend",
                   "stacked", "glow", "stripe", "forecast"];
//: The kinds that must produce a chart, mirroring CHART_KINDS in
//: core/services/builder.py.
const CHART_KINDS = ["line", "area", "spline", "bar", "donut", "pie", "radar"];

const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import Widget from "${join(FRONT, "src/components/widgets/Widget.jsx").replace(/\\/g, "/")}";
export function draw(el, widget) {
  const root = createRoot(el);
  act(() => { root.render(h(Widget, { widget, accent: "#04bade" })); });
  const html = el.innerHTML;
  act(() => root.unmount());
  return html;
}
`;

let bad = 0;
const fail = (msg) => { console.error(`FAIL ${msg}`); bad += 1; };

// recharts' ResponsiveContainer measures its parent, and jsdom reports every
// element as 0x0 -- so it would render nothing however real the DOM is. Swap
// it for one that hands its child a fixed size. Only for Widget.jsx's own
// import: the shim itself imports the genuine recharts, and the importer
// check is what stops that resolving back to the shim.
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

await build({
  plugins: [shimResponsiveContainer],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  // CJS, not ESM: react-dom and recharts are CommonJS and reach for node
  // builtins with bare require(), which an ESM bundle turns into a throw.
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  // React's `act` is a development-build API; the production build drops it.
  define: { "process.env.NODE_ENV": '"development"' },
});

const { draw } = req(OUT);
const host = document.body;
const render = (widget) => {
  const el = document.createElement("div");
  host.appendChild(el);
  try { return draw(el, widget); } finally { host.removeChild(el); }
};

let checked = 0;
for (const [kind, data] of CASES) {
  for (const template of TEMPLATES) {
    const widget = { id: `w-${kind}-${template}`, kind, title: "T",
                     data, template, span: 1 };
    let html;
    try {
      html = render(widget);
    } catch (e) {
      fail(`${kind}/${template} threw: ${e.message}`);
      continue;
    }
    checked += 1;
    if (!html || !html.includes("stat-card")) {
      fail(`${kind}/${template} rendered no card`);
      continue;
    }
    // A CHART must draw. This is the assertion the SSR version could not
    // make, and the reason this test mounts a real DOM.
    if (CHART_KINDS.includes(kind)) {
      if (!html.includes("<svg")) fail(`${kind}/${template} drew no svg`);
      else if (!html.includes("recharts-surface")) {
        fail(`${kind}/${template} drew an svg with no recharts content`);
      }
    }
  }
}

// -- content, for the parts a chart cannot speak for --------------------------
const one = (kind, data, template = "plain", extra = {}) =>
  render({ id: "x", kind, title: "T", data, template, ...extra });

if (!one("tile", { value: 42, sub: "things" }).includes("42")) {
  fail("a tile did not render its value");
}
// The delta badge is new; it must appear only when the server sends one.
if (!one("tile", { value: 1, delta: { pct: 3.1, was: "9", since: "last month" } }).includes("3.1")) {
  fail("a tile with a delta did not render it");
}
if (one("tile", { value: 1 }).includes("Vs ")) {
  fail("a tile with NO delta rendered a comparison anyway");
}
if (!one("table", { columns: ["Col"], rows: [["cell"]] }).includes("cell")) {
  fail("a table did not render its rows");
}
if (!one("gauge", { value: 77, max: 100 }).includes("77")) {
  fail("a gauge did not render its number");
}
// The donut "legend" template is the one chart template that would otherwise
// look identical to `plain` if its branch were never taken.
const legend = one("donut", SERIES, "legend");
// (A plain ring now labels its slices "b: 2 (33%)" on leader lines -- the
// rendering addendum -- so the share COLUMN, ">33%<", is what tells them apart.)
if (!legend.includes(">33%<")) {
  fail("the donut legend template did not render its slice shares");
}
if (one("donut", SERIES, "plain").includes(">33%<")) {
  fail("a plain donut rendered legend rows");
}
// An accent tile paints the card; plain must not.
if (!one("tile", { value: 1, color: "#ff0000" }, "accent").includes("background")) {
  fail("the accent template did not dress the card");
}
// Templates that change the STROKE have to reach the SVG, or the picker is
// offering a choice that does nothing.
if (!one("line", SERIES, "dashed").includes("stroke-dasharray")) {
  fail("the dashed template did not dash the line");
}
if (!one("line", SERIES, "gradient").includes("linearGradient")) {
  fail("the gradient template did not define a gradient");
}

// -- `bg`: a card's own colour, independent of template/accent ----------------
//
// Distinct knob from `color` (the chart-stroke/tile-accent one) and from
// `template` (accent/outline/soft) -- an operator picking a plain tile still
// needs to be able to say "this one is blue".
// jsdom normalises any hex it is handed to rgb(), same as a real browser's
// computed style would -- assert on the rgb triple, not the hex source.
const blueBg = one("tile", { value: 1 }, "plain", { bg: "#0000ff" });
if (!blueBg.includes("rgb(0, 0, 255)")) {
  fail("bg did not paint the card background on the plain template");
}
// White text on a near-black card, near-black text on a near-white card --
// readableTextOn has to actually flip, or a bright bg swallows white theme
// text silently.
const darkOnLight = one("tile", { value: 1 }, "plain", { bg: "#ffffff" });
if (!darkOnLight.includes("rgb(10, 10, 10)")) {
  fail("a light bg did not switch to dark text");
}
const lightOnDark = one("tile", { value: 1 }, "plain", { bg: "#000000" });
if (!lightOnDark.includes("color: rgb(255, 255, 255)")) {
  fail("a dark bg did not switch to white text");
}
// No bg set: nothing about the plain template's rendering should change.
if (one("tile", { value: 1 }, "plain").includes("rgb(0, 0, 255)")) {
  fail("an unset bg painted the card anyway");
}

// -- the four whole-look chart templates --------------------------------------
//
// Each is a distinct treatment, and each could silently degrade to `plain`
// while still passing the "it drew an svg" check above. So each is asserted on
// the mark that IS the treatment -- and, where it could be confused with an
// existing template, on the plain version NOT having it.
// The GRID is dashed too ("3 3", charts.js GRID), so "does this html contain a
// dasharray" answers the wrong question. Only the series strokes count.
const curves = (html) => html.match(/<path[^>]*recharts-area-curve[^>]*>/g) || [];
const dashed = (paths) => paths.filter((p) => p.includes("stroke-dasharray")).length;

const stacked = one("area", MULTI, "stacked");
// Stacking is what makes it shares-of-a-total rather than two overlaid areas.
if (!stacked.includes("linearGradient")) {
  fail("the stacked template did not define its gradients");
}
const stackedCurves = curves(stacked);
if (stackedCurves.length !== 2) {
  fail(`the stacked template drew ${stackedCurves.length} series, not 2`);
} else if (dashed(stackedCurves) !== 1) {
  // Exactly one solid: the primary. Dash them all and there is no baseline to
  // read the shares against; dash none and it is the gradient template.
  fail(`the stacked template dashed ${dashed(stackedCurves)} of 2 series`);
}
if (dashed(curves(one("area", MULTI, "plain")))) {
  fail("a plain area chart dashed a series anyway");
}

const glow = one("area", SERIES, "glow");
// The blur filter IS the glow; without it this is just the gradient template.
if (!glow.includes("feGaussianBlur")) {
  fail("the glow template did not define its blur filter");
}
if (!glow.includes("recharts-dot")) {
  fail("the glow template drew no points to light");
}
if (one("area", SERIES, "gradient").includes("feGaussianBlur")) {
  fail("a gradient chart defined a glow filter it does not use");
}

const stripe = one("area", SERIES, "stripe");
// A <pattern>, not a second colour -- the whole point is that it survives
// greyscale and colour-blindness.
if (!stripe.includes("<pattern")) {
  fail("the stripe template did not define its hatch pattern");
}
if (!stripe.includes("-hatch")) {
  fail("the stripe template did not fill with the hatch");
}

const forecast = one("line", SERIES, "forecast");
// A band AND a line: the band gives it texture, the line gives it a value to
// read off. Losing either one leaves a chart that says half of what it means.
if (!forecast.includes("<pattern")) {
  fail("the forecast template did not define its band pattern");
}
if (!forecast.includes("recharts-area")) {
  fail("the forecast template drew no band");
}
if (!forecast.includes("recharts-line")) {
  fail("the forecast template drew no line over its band");
}

// -- a value that IS a word ---------------------------------------------------
//
// Reported as "the text value is not displayed". Three readers on the server
// kept numbers only and dropped a text stat on the floor, so no card could
// show one; with the string arriving, the card still had to be told what to
// do with it, and the tile's "Show the value as" picker offered only the
// three formats that turn a NUMBER into a word.
//
// The trap those three formats set: they all test Number(value) > 0, and
// Number("People") is NaN, so a text stat under any of them reads NO / OFF /
// FALSE -- a confident wrong answer, which is worse than a blank. "text" is
// the format that says the value is already the word.
const textTile = one("tile", { value: "People" }, "plain", { format: "text" });
if (!textTile.includes("People")) {
  fail("a tile with format 'text' did not render the word");
}
// ...and with NO format chosen at all, because an operator who never opens
// that picker must still see the value their Set Variable is holding.
if (!one("tile", { value: "People" }).includes("People")) {
  fail("a tile with no format dropped an arriving text value");
}
// The unit is a quantity's qualifier. "People pcs" is not a reading.
if (one("tile", { value: "People", unit: "pcs" }, "plain",
        { format: "text" }).includes("pcs")) {
  fail("a text tile qualified a word with a unit");
}
// A number under format 'text' is still that number, printed as it stands --
// somebody picking the option on a numeric card gets the digits, not "-".
if (!one("tile", { value: 42 }, "plain", { format: "text" }).includes("42")) {
  fail("format 'text' swallowed a numeric value");
}

// -- a GAUGE handed a word ----------------------------------------------------
//
// A dial of a word has no honest needle position. Number("People") is NaN,
// which drew an arc at no position and printed NaN in the middle of the card;
// falling back to 0 would have been worse still -- an empty-reading dial
// indistinguishable from a real zero on a card that is actually misconfigured.
// So the arc is not drawn at all and the card says what it would need.
const wordGauge = one("gauge", { value: "People", max: 100 });
if (!wordGauge.includes("People")) {
  fail("a gauge handed a word did not show the word");
}
if (wordGauge.includes("NaN")) {
  fail("a gauge handed a word printed NaN");
}
if (wordGauge.includes("recharts-surface")) {
  fail("a gauge handed a word drew an arc anyway - a needle at no position");
}
if (!/needs a number/.test(wordGauge)) {
  fail("a gauge handed a word did not say why it is empty");
}
// The unpublished case is NOT the word case: a stat the app has not written
// yet has always read 0 on a dial, and every reboot would otherwise raise a
// new complaint.
const freshGauge = one("gauge", { max: 100 });
if (!freshGauge.includes("recharts-surface")) {
  fail("a gauge with no value yet stopped drawing its arc");
}

rmSync(OUT, { force: true });

if (bad) {
  console.error(`\n${bad} widget render problem(s).`);
  process.exit(1);
}
console.log(`== ${checked} widget kind/template combinations draw, `
            + "charts produce real SVG, content assertions hold");
