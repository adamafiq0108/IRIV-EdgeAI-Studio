// The Builders' palette (Tok Aba, 2026-10-14): every card that reads data
// comes from the Workflow canvas, grouped by what it shows; the generic data
// widgets and the Measurements / Variables / Recorded data lists are gone.
//
//   1. Widgets: the Dashboard keeps only what no workflow node makes (Live
//      video, Class filter, Image card, Text card; AI model classes left
//      on 2026-10-14, a saved one still works; Status / alert and Violation
//      status left on 2026-10-14, they are verdict / status cards on the
//      canvas); Analytics has the Image and Text cards
//   2. Dashboard cards: card + button origins only, under plain headings in
//      the chart picker's mapping, empty headings not drawn, unknown -> Other
//   3. the empty state: one sentence and a link to the Workflow page
//   4. an old page keeps working: a removed-kind widget still renders and
//      edits, and the server still accepts every old kind
//
//   node tests/check_builderpalette.mjs
import { createRequire } from "node:module";
import { readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".builderpalette-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image", "Event",
                 "CustomEvent", "MouseEvent", "requestAnimationFrame",
                 "cancelAnimationFrame", "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};
const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  stdin: { contents: `
    import { createRoot } from "react-dom/client";
    import { act, createElement as h } from "react";
    import { MemoryRouter } from "react-router-dom";
    import { WorkflowCardsPalette, WidgetInspector } from "${p("src/components/widgets/builderShared.jsx")}";
    import Widget from "${p("src/components/widgets/Widget.jsx")}";
    import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
    export { PALETTE, DASHBOARD_PALETTE, ANALYTICS_PALETTE, CARD_CATEGORIES, cardCategory,
             workflowCardGroups, catalogWidget } from "${p("src/components/widgets/builderShared.jsx")}";
    export function live(el, comp, props) {
      const root = createRoot(el);
      const C = comp === "palette" ? WorkflowCardsPalette : comp === "widget" ? Widget : WidgetInspector;
      const render = (next) => act(() => {
        root.render(h(MemoryRouter, null, h(ToastProvider, null, h(C, next))));
      });
      render(props);
      return { act, render, unmount: () => act(() => root.unmount()) };
    }`, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic",
  logLevel: "silent", define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const mount = (comp, props) => {
  const box = document.body.appendChild(document.createElement("div"));
  const r = m.live(box, comp, props);
  return { box, r, close: () => { r.unmount(); box.remove(); } };
};

// -- 1. Widgets ------------------------------------------------------------------
const dash = m.DASHBOARD_PALETTE.map((x) => x.label);
console.log(`     Dashboard Widgets: ${dash.join(", ")}`);
// ...plus the Image and Text cards (static, no data; check_staticcards).
check("Dashboard Widgets: Live video, Camera snaps, Class filter, Image card, Text card (no AI model classes)",
      JSON.stringify(dash) === JSON.stringify(["Live video", "Camera snaps", "Class filter", "Image card", "Text card"]),
      JSON.stringify(dash));
check("...Status / alert and Violation status are no longer offered there",
      !dash.includes("Status / alert") && !dash.includes("Violation status"));
const GONE = ["Stat tile", "Line chart", "History chart", "Bar chart", "Donut", "Event table", "Heatmap"];
check("...and the generic data widgets are gone from both Builders",
      GONE.every((g) => !dash.includes(g) && !m.ANALYTICS_PALETTE.some((x) => x.label === g)));
check("Analytics Widgets: only the Image and Text cards (nothing live suits a period view)",
      m.ANALYTICS_PALETTE.map((x) => x.label).join() === "Image card,Text card");
check("the factory vocabulary still knows every old kind (old pages, newWidget)",
      ["tile", "line", "bar", "donut", "table", "heatmap"].every((k) => m.PALETTE.some((x) => x.kind === k)));
for (const page of ["Builder.jsx", "AnalyticsBuilder.jsx"]) {
  const src = readFileSync(join(FRONT, "src", "pages", page), "utf8");
  check(`${page}: no "From workflow" picker, measurements or recorded columns listed`,
        !src.includes("Add from workflow") && !src.includes("CatalogPicker")
        && src.includes("<WorkflowCardsPalette"));
}
const shared = readFileSync(join(FRONT, "src/components/widgets/builderShared.jsx"), "utf8");
check("the old catalogue groups (Measurements, Variables, Recorded data) are not offered",
      !shared.includes('"Measurements"') && !shared.includes('"Recorded data"')
      && !shared.includes('["variable", "Variables"'));

// -- 2. Dashboard cards, categorised ---------------------------------------------
const card = (title, kind, extra = {}) => ({ origin: "card", title, label: title, kind,
                                             source: "stat", key: "", node: title, ...extra });
const CATALOG = [
  { origin: "measure", label: "In Zone Now", kind: "tile", key: "fn_bay" },
  card("Compliance", "verdict"), card("Alarm", "status"), card("Count", "tile"),
  card("Level", "gauge"),
  card("Trend", "line", { source: "history" }), card("Smooth", "spline", { source: "history" }),
  card("Volume", "area", { source: "history" }),
  card("Per day", "bar", { chart: { x_type: "time", step: "1d" } }),
  card("Legacy per day", "bar", { source: "dataset", dataset: "t", columns: [],
                                  query: { agg: "count", bucket: "1d" } }),
  card("By camera", "bar", { chart: { x_type: "column", x_field: "" } }),
  card("Share", "pie"), card("Ring", "donut"), card("Spider", "radar"),
  card("Rows", "table", { source: "dataset" }), card("Events", "table", { source: "query" }),
  card("Where", "heatmap"),
  { origin: "control", label: "Reset", title: "Reset", kind: "button", node: "inj" },
  { origin: "variable", label: "Target", kind: "tile", key: "var_target" },
  { origin: "table", label: "total_panel", kind: "tile", dataset: "t" },
  card("Mystery", "sparkle"),
];
const groups = m.workflowCardGroups(CATALOG);
const flat = Object.fromEntries(groups.map(([id, , rows]) => [id, rows.map(([c]) => c.title || c.label)]));
console.log(`     groups: ${JSON.stringify(flat)}`);
check("headings in the agreed order, only those with a card",
      JSON.stringify(groups.map(([, t]) => t)) === JSON.stringify(["Status / verdict", "Number tile",
        "Gauge", "Line chart", "Area chart", "Time-based bar", "Comparison bar", "Pie chart",
        "Donut chart", "Radar chart", "Table", "Event table", "Heatmap", "Button", "Other"]),
      JSON.stringify(groups.map(([, t]) => t)));
check("Status / verdict holds the verdict and the status pill",
      JSON.stringify(flat.status) === '["Compliance","Alarm"]');
check("Line chart holds line and smooth line (spline)", JSON.stringify(flat.line) === '["Trend","Smooth"]');
check("a bar is a Time-based bar when it steps by time (model or legacy), else a Comparison bar",
      JSON.stringify(flat.time_bar) === '["Per day","Legacy per day"]'
      && JSON.stringify(flat.compare_bar) === '["By camera"]');
check("a table of events is an Event table, a data table a Table",
      JSON.stringify(flat.events) === '["Events"]' && JSON.stringify(flat.table) === '["Rows"]');
check("an Inject button is listed under Button", JSON.stringify(flat.button) === '["Reset"]');
check("an unknown kind lands under Other, never dropped", JSON.stringify(flat.other) === '["Mystery"]');
check("measurements, variables and table columns are not listed",
      !Object.values(flat).flat().some((t) => ["In Zone Now", "Target", "total_panel"].includes(t)));
{
  // Each heading is a drop-down (Tok Aba, 2026-10-14): a button with the
  // count; only the first opens by default; the state is remembered per
  // Builder; the heading of the card selected on the canvas opens itself.
  localStorage.clear();
  let added = null;
  const KEY = "builder.cards.test";
  const v = mount("palette", { catalog: CATALOG, editable: true, storageKey: KEY,
                               onAdd: (c) => { added = c; } });
  const heads = () => [...v.box.querySelectorAll("button.palette-cat-title")];
  const head = (t) => heads().find((x) => x.textContent.startsWith(t));
  check("rendered: the 'Dashboard cards' title and one heading button per group",
        v.box.textContent.includes("Dashboard cards") && heads().length === groups.length);
  check("...each heading says its count ('Line chart 2')",
        head("Line chart").querySelector(".palette-cat-count").textContent === "2"
        && head("Status / verdict").querySelector(".palette-cat-count").textContent === "2");
  check("...all start shut but the first (aria-expanded)",
        heads().map((x) => x.getAttribute("aria-expanded")).join() ===
          ["true", ...Array(groups.length - 1).fill("false")].join()
        && v.box.querySelectorAll("button.palette-item").length === 2);
  v.r.act(() => { head("Pie chart").click(); });
  const share = [...v.box.querySelectorAll("button.palette-item")].find((x) => x.textContent.includes("Share"));
  check("clicking a heading opens its list", head("Pie chart").getAttribute("aria-expanded") === "true" && !!share);
  check("...each card shows its name and its type as before",
        share && share.querySelector("strong").textContent === "Share"
        && share.querySelector("em").textContent === "pie");
  v.r.act(() => { share.click(); });
  check("...clicking a card adds it", added && added.title === "Share");
  check("...and it can be dragged (the catalogue index is the payload)",
        share.getAttribute("draggable") === "true");
  v.r.act(() => { head("Status / verdict").click(); });
  check("clicking an open heading shuts it", head("Status / verdict").getAttribute("aria-expanded") === "false");
  const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
  check("...and both choices are remembered for this Builder", saved.pie === true && saved.status === false,
        JSON.stringify(saved));
  v.close();
  const again = mount("palette", { catalog: CATALOG, editable: true, storageKey: KEY, onAdd: () => {} });
  const h2 = (t) => [...again.box.querySelectorAll("button.palette-cat-title")].find((x) => x.textContent.startsWith(t));
  check("reopened: the remembered state wins over the default",
        h2("Pie chart").getAttribute("aria-expanded") === "true"
        && h2("Status / verdict").getAttribute("aria-expanded") === "false");
  again.r.render({ catalog: CATALOG, editable: true, storageKey: KEY, onAdd: () => {}, selectedNode: "Spider" });
  check("the card selected on the canvas opens its heading (Radar chart)",
        h2("Radar chart").getAttribute("aria-expanded") === "true");
  again.close();
  // A browser that refuses storage still works, with the defaults.
  const real = Object.getOwnPropertyDescriptor(dom.window, "localStorage");
  Object.defineProperty(global, "localStorage", { configurable: true,
    get() { throw new Error("denied"); } });
  let ok = true;
  try {
    const nostore = mount("palette", { catalog: CATALOG, editable: true, storageKey: KEY, onAdd: () => {} });
    const hs = [...nostore.box.querySelectorAll("button.palette-cat-title")];
    nostore.r.act(() => { hs[1].click(); });
    ok = hs[0].getAttribute("aria-expanded") === "true" && hs[1].getAttribute("aria-expanded") === "true";
    nostore.close();
  } catch (e) { ok = false; console.error(e.message); }
  Object.defineProperty(global, "localStorage", { configurable: true, writable: true,
                                                  value: real ? dom.window.localStorage : undefined });
  check("...and with storage refused the headings still open and shut", ok);
}

// -- 3. the empty state ------------------------------------------------------------
{
  const v = mount("palette", { catalog: [CATALOG[0], CATALOG[18]], editable: true, onAdd: () => {} });
  const link = v.box.querySelector("a");
  check("no workflow cards: the sentence and a link to the Workflow page",
        v.box.textContent.includes("Add a card on the Workflow canvas and it will appear here.")
        && link && link.getAttribute("href") === "/workflow" && !v.box.querySelector(".palette-cat"),
        v.box.innerHTML.slice(0, 400));
  v.close();
}

// -- 4. an old page keeps working ------------------------------------------------------
{
  // A page holding a status pill and a Violation status (the one
  // seed_main_widgets puts on a new system): both still render and edit.
  const meta = { sources: { stat: ["compliance", "fn_x"], bucket: [], events: [] }, history: {},
                 query: {}, kinds: ["tile", "status", "verdict"], catalog: [], capabilities: [] };
  for (const old of [
    { id: "status_old", title: "Line alarm", kind: "status", source: "stat", key: "fn_x", span: 1,
      grid: {}, template: "plain", bands: {}, status: { op: ">=", value: 1, ok_label: "OK", alert_label: "ALERT" } },
    { id: "seed_verdict", title: "Status", kind: "verdict", source: "stat", key: "compliance", span: 1,
      grid: {}, template: "plain", bands: {} },
  ]) {
    const w = mount("widget", { widget: { ...old, data: { value: 1 } } });
    check(`a saved ${old.kind} widget still renders`, w.box.textContent.length > 0 && !/error/i.test(w.box.textContent),
          w.box.textContent.slice(0, 160));
    w.close();
    let patched = null;
    const i = mount("inspector", { current: old, meta, patch: (id, pt) => { patched = [id, pt]; } });
    const title = i.box.querySelector("input.input");
    i.r.act(() => {
      const set = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set;
      set.call(title, "Renamed");
      title.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    });
    check(`...and still edits (${old.kind} Title)`, patched && patched[0] === old.id
          && patched[1].title === "Renamed", JSON.stringify(patched));
    i.close();
  }
  // An AI model classes card already on a page (no longer offered in the
  // palette, Tok Aba 2026-10-14): still renders and edits.
  {
    const ai = { id: "ai_old", title: "Model classes", kind: "ai_classes", source: "stat", key: "",
                 span: 2, grid: {}, template: "plain", bands: {} };
    const w = mount("widget", { widget: { ...ai, data: { classes: ["person", "car"], chosen: ["person"] } } });
    check("a saved AI model classes card still renders", w.box.textContent.includes("Model classes"),
          w.box.textContent.slice(0, 160));
    w.close();
    let patched = null;
    const i = mount("inspector", { current: ai, meta, patch: (id, pt) => { patched = [id, pt]; } });
    const title = i.box.querySelector("input.input");
    i.r.act(() => {
      const set = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set;
      set.call(title, "Classes");
      title.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    });
    check("...and still edits (its Title)", patched && patched[0] === "ai_old" && patched[1].title === "Classes",
          JSON.stringify(patched));
    i.close();
  }
  check("workflow verdict and status cards still list under Status / verdict",
        JSON.stringify(m.workflowCardGroups([card("Compliance", "verdict"), card("Alarm", "status")])
          .map(([id, , rows]) => [id, rows.length])) === '[["status",2]]');
}
{
  const old = { id: "tile_old1", title: "Old stat tile", kind: "tile", source: "stat",
                key: "fn_x", span: 1, grid: {}, template: "plain", bands: {}, sub: "" };
  const w = mount("widget", { widget: { ...old, data: { value: 7 } } });
  check("a stat tile from the removed palette still renders", w.box.textContent.includes("Old stat tile"),
        w.box.textContent.slice(0, 200));
  w.close();
  let patched = null;
  const meta = { sources: { stat: ["fn_x"], bucket: [], events: [] }, history: {}, query: {},
                 kinds: ["tile", "line", "bar"], catalog: [], capabilities: [] };
  const i = mount("inspector", { current: old, meta, patch: (id, pt) => { patched = [id, pt]; } });
  const title = i.box.querySelector("input.input");
  i.r.act(() => {
    const set = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set;
    set.call(title, "Renamed");
    title.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  check("...and still edits (its Title)", patched && patched[0] === "tile_old1"
        && patched[1].title === "Renamed", JSON.stringify(patched));
  i.close();
  const kinds = readFileSync(join(ROOT, "core/services/builder.py"), "utf8")
    .match(/^KINDS = \(([\s\S]*?)\)\n/m)[1];
  check("the server still accepts every kind the old palette made (no refusal added)",
        ["tile", "line", "bar", "donut", "table", "heatmap", "status", "verdict", "video"]
          .every((k) => kinds.includes(`"${k}"`)));
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
