// The Text card and the Image card in the Builders and on the pages (Tok
// Aba, 2026-10-14): Widget.jsx draws them from their own fields,
// staticCardFields.jsx edits them, both Builders' Widgets groups offer them.
//
//   1. palette: Dashboard Widgets = Live video, Camera snaps, Class filter,
//      AI model classes, Image card, Text card; Analytics = Image card, Text card;
//      a new one binds no key
//   2. Text card: words as TEXT (a <script> stays characters), line breaks
//      kept, size / alignment / bold applied, scrolls inside its cell
//   3. Image card: the picture by its asset id through apiUrl, alt and fit
//      applied; no id, or a path-like one, draws no <img> but a sentence
//   4. inspector: the Words / Picture section replaces Data; the fields
//      write the widget; Show as offers only itself
//   5. no title on either card by default (Tok Aba, 2026-10-14): the card
//      is its content; `title` is the name the Builder lists it by, drawn
//      only when "Show title" is turned on (check_showtitle)
//
//   node tests/check_staticcards.mjs
import { createRequire } from "node:module";
import { rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".staticcards-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image", "Event", "CustomEvent",
                 "MouseEvent", "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
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
    import Widget from "${p("src/components/widgets/Widget.jsx")}";
    import { WidgetInspector } from "${p("src/components/widgets/builderShared.jsx")}";
    import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
    export { DASHBOARD_PALETTE, ANALYTICS_PALETTE, newWidget }
      from "${p("src/components/widgets/builderShared.jsx")}";
    export function live(el, comp, props) {
      const root = createRoot(el);
      const C = comp === "widget" ? Widget : WidgetInspector;
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

// -- 1. palette -------------------------------------------------------------------
check("Dashboard Widgets: Live video, Camera snaps, Class filter, Image card, Text card",
      m.DASHBOARD_PALETTE.map((x) => x.label).join() === "Live video,Camera snaps,Class filter,Image card,Text card",
      m.DASHBOARD_PALETTE.map((x) => x.label).join());
check("Analytics Widgets: Image card, Text card",
      m.ANALYTICS_PALETTE.map((x) => x.label).join() === "Image card,Text card");
const fresh = m.newWidget(m.DASHBOARD_PALETTE.find((x) => x.kind === "text_card"), { stat: ["fn_x"] });
check("a new Text card binds no key", fresh.key === "" && fresh.kind === "text_card", JSON.stringify(fresh));

// -- 2. Text card -----------------------------------------------------------------
const base = { id: "t1", title: "Notes", span: 2, grid: {}, template: "plain", data: { static: true } };
{
  const v = mount("widget", { widget: { ...base, kind: "text_card",
    body: "Shift A\n<script>window.__x = 1</script><img src=x onerror=alert(1)> https://evil.example",
    text_size: "large", align: "center", bold: true } });
  const el = v.box.querySelector('[data-testid="text-card"]');
  check("a Text card draws its words as text: no <script>, no <img>, no link",
        el && !v.box.querySelector("script") && !v.box.querySelector("img")
        && !v.box.querySelector("a") && el.textContent.includes("<script>window.__x = 1</script>")
        && !window.__x);
  check("...line breaks kept, size / alignment / bold applied, scrolls in its cell",
        el.style.whiteSpace === "pre-wrap" && el.style.fontSize === "20px"
        && el.style.textAlign === "center" && el.style.fontWeight === "700"
        && el.style.overflowY === "auto");
  v.close();
  const empty = mount("widget", { widget: { ...base, kind: "text_card", body: "", data: null } });
  check("an empty Text card says where to write, even with no preview data",
        empty.box.textContent.includes("Write the words in the panel on the right."));
  empty.close();
}

{
  const box = (w) => {
    const v = mount("widget", { widget: { ...base, kind: "text_card", body: "x", ...w }, fill: true });
    const b = v.box.querySelector('[data-testid="text-card-box"]');
    const out = [b.dataset.valign, b.style.justifyContent];
    v.close();
    return out.join();
  };
  check("an old Text card (no valign) stays at the top", box({}) === "top,flex-start");
  check("...Middle and Below move the words down", box({ valign: "middle" }) === "middle,center"
        && box({ valign: "bottom" }) === "bottom,flex-end");
}

// -- 3. Image card ----------------------------------------------------------------
const AID = "0123456789abcdef0123456789abcdef";
{
  const v = mount("widget", { widget: { ...base, kind: "image_card", asset: AID, alt: "Line 2 layout",
                                        fit: "cover", caption: "Bay 3" } });
  const img = v.box.querySelector("img");
  check("an Image card draws its picture by asset id, with its description and fit",
        img && img.getAttribute("src").endsWith(`/api/builder/assets/${AID}`)
        && img.getAttribute("alt") === "Line 2 layout" && img.style.objectFit === "cover"
        && v.box.querySelector("figcaption").textContent === "Bay 3", img && img.outerHTML);
  v.close();
  for (const [what, asset, words] of [["no picture", "", "Upload a picture"],
                                      ["a path-like id", "../../etc/passwd", "not on this system"],
                                      ["a web address", "https://evil.example/a.png", "not on this system"]]) {
    const w = mount("widget", { widget: { ...base, kind: "image_card", asset } });
    check(`${what}: no <img>, a sentence instead`,
          !w.box.querySelector("img") && w.box.textContent.includes(words), w.box.innerHTML.slice(0, 200));
    w.close();
  }
}

// -- 4. inspector ------------------------------------------------------------------
const META = { sources: { stat: ["fn_x"], bucket: [], events: [] }, history: {}, query: {},
               kinds: ["tile", "line", "text_card", "image_card"], catalog: [], capabilities: [] };
{
  let patched = null;
  const cur = { ...base, kind: "text_card", source: "stat", key: "", body: "hi", bands: {} };
  const v = mount("inspector", { current: cur, meta: META, patch: (id, pt) => { patched = pt; } });
  const heads = [...v.box.querySelectorAll("summary, .section-title, h3, h4")].map((x) => x.textContent);
  check("Text card inspector: a Words section, no Data / Source",
        v.box.textContent.includes("Words") && !v.box.querySelector('select option[value="history"]'),
        JSON.stringify(heads));
  const ta = v.box.querySelector('textarea[aria-label="Text"]');
  v.r.act(() => {
    const set = Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, "value").set;
    set.call(ta, "New words");
    ta.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  check("...typing writes the body", patched && patched.body === "New words", JSON.stringify(patched));
  // The Alignment control is the shared segmented one (cardFields.AlignField),
  // the same the stat cards' Layout row uses.
  const right = v.box.querySelector('[role="radiogroup"][aria-label="Alignment"] input[value="right"]');
  v.r.act(() => { right.click(); });
  check("...alignment writes", patched.align === "right");
  // Position (Above / Middle / Below), under Alignment, Above by default --
  // where a Text card always sat.
  const pos = [...v.box.querySelectorAll('[role="radiogroup"][aria-label="Position"] input')];
  check("...a Position control under Alignment: Above / Middle / Below, Above by default",
        pos.map((x) => x.getAttribute("aria-label")).join() === "Above,Middle,Below"
        && pos.find((x) => x.checked)?.value === "top");
  v.r.act(() => { pos.find((x) => x.value === "bottom").click(); });
  check("...and writes valign", patched.valign === "bottom", JSON.stringify(patched));
  const shape = [...v.box.querySelectorAll("select")].find((s) => [...s.options].some((o) => o.value === "text_card"));
  check("...Show as offers only itself", shape && [...shape.options].map((o) => o.value).join() === "text_card",
        shape && [...shape.options].map((o) => o.value).join());
  v.close();
}
{
  let patched = null;
  const cur = { ...base, kind: "image_card", source: "stat", key: "", asset: AID, bands: {} };
  const v = mount("inspector", { current: cur, meta: META, patch: (id, pt) => { patched = pt; } });
  const file = v.box.querySelector('input[type="file"][aria-label="Upload a picture"]');
  check("Image card inspector: a Picture section with an upload, PNG / JPEG / WebP / GIF only",
        v.box.textContent.includes("Picture") && file
        && file.getAttribute("accept") === "image/png,image/jpeg,image/webp,image/gif");
  check("...no web address field anywhere",
        ![...v.box.querySelectorAll("input")].some((x) => /url|address|link/i.test(x.getAttribute("aria-label") || "")));
  const fit = v.box.querySelector('select[aria-label="Fit"]');
  v.r.act(() => { fit.value = "cover"; fit.dispatchEvent(new dom.window.Event("change", { bubbles: true })); });
  check("...fit writes", patched && patched.fit === "cover");
  v.close();
}

// -- 5. no title drawn ----------------------------------------------------------------
for (const kind of ["text_card", "image_card"]) {
  const v = mount("widget", { widget: { ...base, kind, title: "SECRET NAME", body: "words",
                                        asset: AID, caption: "Bay 3" } });
  check(`${kind}: no title bar and no title text on the card`,
        !v.box.querySelector(".stat-label") && !v.box.textContent.includes("SECRET NAME"),
        v.box.innerHTML.slice(0, 200));
  v.close();
  const i = mount("inspector", { current: { ...base, kind, source: "stat", key: "", title: "SECRET NAME", bands: {} },
                                 meta: META, patch: () => {} });
  const labels = [...i.box.querySelectorAll(".ui-field-label")].map((x) => (x.childNodes[0]?.textContent || "").trim());
  const sw = i.box.querySelector('input[aria-label="Show title"]');
  check(`${kind}: the inspector offers "Name in the Builder", and Show title (under Appearance) off by default`,
        labels.includes("Name in the Builder") && !labels.includes("Title") && sw && !sw.checked
        && sw.closest("details.rail-section").querySelector("summary").textContent.startsWith("Appearance"),
        JSON.stringify(labels));
  i.close();
}
{
  const v = mount("widget", { widget: { ...base, kind: "tile", source: "stat", key: "k", title: "A tile",
                                        data: { value: 3 } } });
  check("every other card still draws its title", v.box.querySelector(".stat-label")?.textContent.includes("A tile"));
  v.close();
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
