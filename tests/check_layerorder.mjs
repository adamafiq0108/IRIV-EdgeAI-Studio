// Which card is in front where cards overlap (Tok Aba, 2026-10-14):
// layerOrder.js, the Builders' Arrange buttons (Layout section), the canvas
// chrome's front / back buttons, and both grids stacking by grid.z.
//
//   1. the moves: front / forward / backward / back, one step past exactly
//      one card even where many share a layer, ties kept in list order, only
//      the cards whose layer changes are written, 0 stored as nothing
//   2. disabled at the extreme each moves towards; a lone card cannot move
//   3. read-only page (WidgetGrid) and canvas (GridCanvas) apply z-index; an
//      old page (no z) gets none, so its stacking is the list order as before
//   4. the Arrange buttons in the Builder inspector restack at once; the
//      canvas chrome's front / back buttons do the same
//   5. the card being dragged rides on top while it moves
//
//   node tests/check_layerorder.mjs
import { createRequire } from "node:module";
import { rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".layerorder-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image", "Event", "CustomEvent",
                 "MouseEvent", "PointerEvent", "requestAnimationFrame", "cancelAnimationFrame",
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
    import GridCanvas from "${p("src/components/widgets/GridCanvas.jsx")}";
    import WidgetGrid from "${p("src/components/widgets/WidgetGrid.jsx")}";
    import { WidgetInspector } from "${p("src/components/widgets/builderShared.jsx")}";
    import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
    export * as L from "${p("src/components/widgets/layerOrder.js")}";
    export function live(el, comp, props) {
      const root = createRoot(el);
      const C = comp === "canvas" ? GridCanvas : comp === "page" ? WidgetGrid : WidgetInspector;
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
const L = m.L;
const mount = (comp, props) => {
  const box = document.body.appendChild(document.createElement("div"));
  const r = m.live(box, comp, props);
  return { box, r, close: () => { r.unmount(); box.remove(); } };
};
const card = (id, z, kind = "text_card") => ({ id, title: id, kind, source: "stat", key: "", body: id,
  span: 2, template: "plain", bands: {}, grid: { x: 0, y: 0, w: 4, h: 3, ...(z ? { z } : {}) } });
const apply = (list, patches) => list.map((w) => (patches[w.id] ? { ...w, grid: patches[w.id] } : w));

// -- 1/2. the moves ----------------------------------------------------------------
const three = [card("a"), card("b"), card("c")];          // all on layer 0
check("no layers set: back-to-front is the list order", L.layerOrder(three).join() === "a,b,c");
let pt = L.arrange(three, "a", "forward");
let after = apply(three, pt);
check("Bring forward passes exactly one card, even where all share a layer",
      L.layerOrder(after).join() === "b,a,c", JSON.stringify(pt));
// b becomes 0 (unchanged, not written), a 1, c 2.
check("...writing only the cards whose layer changed",
      Object.keys(pt).sort().join() === "a,c" && pt.a.z === 1 && pt.c.z === 2, JSON.stringify(pt));
check("...and 0 is never stored", Object.values(pt).every((g) => g.z !== 0));
pt = L.arrange(three, "a", "front");
check("Bring to front puts it last", L.layerOrder(apply(three, pt)).join() === "b,c,a");
pt = L.arrange(three, "c", "back");
check("Send to back puts it first, others keep their order",
      L.layerOrder(apply(three, pt)).join() === "c,a,b");
pt = L.arrange(three, "c", "backward");
check("Send backward passes one card", L.layerOrder(apply(three, pt)).join() === "a,c,b");
check("disabled at the extremes: the front card cannot go further front",
      !L.canArrange(three, "c", "front") && !L.canArrange(three, "c", "forward")
      && L.canArrange(three, "c", "back"));
check("...the back card cannot go further back",
      !L.canArrange(three, "a", "back") && !L.canArrange(three, "a", "backward"));
check("...and a lone card cannot move at all",
      ["front", "forward", "backward", "back"].every((op) => !L.canArrange([card("x")], "x", op)));
const mixed = [card("a", 5), card("b"), card("c", -2)];
check("set layers order first, the list breaks ties", L.layerOrder(mixed).join() === "c,b,a");
check("Arrange returns nothing when it cannot move", JSON.stringify(L.arrange(mixed, "a", "front")) === "{}");

// -- 3. z-index on both grids ------------------------------------------------------------
{
  const page = mount("page", { widgets: [card("a"), card("b", 4), card("c", -1)] });
  const cells = [...page.box.querySelectorAll(".wgt-cell")];
  const z = cells.map((c) => c.style.zIndex);
  check("read-only page: a card with a layer gets that z-index, one without gets none",
        z.includes("4") && z.includes("-1") && z.includes(""), JSON.stringify(z));
  check("...and the grid isolates the stacking (never behind the page)",
        page.box.querySelector(".wgt-grid").style.isolation === "isolate");
  page.close();
  const old = mount("page", { widgets: [card("a"), card("b")] });
  check("an old page (no z): no z-index at all, stacking as before",
        [...old.box.querySelectorAll(".wgt-cell")].every((c) => c.style.zIndex === ""));
  old.close();
}

// -- 4. Arrange in the Builder inspector, and the canvas chrome ----------------------------
{
  let list = [card("a"), card("b"), card("c")];
  const patchList = (id, fields) => { list = list.map((w) => (w.id === id ? { ...w, ...fields } : w)); };
  const META = { sources: { stat: [], bucket: [], events: [] }, history: {}, query: {}, kinds: ["text_card"],
                 catalog: [], capabilities: [] };
  const v = mount("inspector", { current: list[0], meta: META, patch: patchList, siblings: list });
  const btn = (name) => v.box.querySelector(`button[aria-label="${name}"]`);
  check("Builder: Arrange under Layout with four plain-word buttons",
        ["Bring to front", "Bring forward", "Send backward", "Send to back"].every((n) => btn(n))
        && btn("Send to back").closest("details.rail-section").querySelector("summary").textContent
          .startsWith("Layout"));
  check("...the back card: Send backward / Send to back disabled",
        btn("Send to back").disabled && btn("Send backward").disabled && !btn("Bring to front").disabled);
  v.r.act(() => { btn("Bring to front").click(); });
  check("...Bring to front restacks at once", L.layerOrder(list).join() === "b,c,a", L.layerOrder(list).join());
  v.r.render({ current: list.find((w) => w.id === "a"), meta: META, patch: patchList, siblings: list });
  check("...and now the front buttons are disabled", btn("Bring to front").disabled && btn("Bring forward").disabled);
  v.close();

  let canvasList = [card("a"), card("b")];
  const c = mount("canvas", { widgets: canvasList, preview: [], selected: null, editable: true,
                              setSelected: () => {}, remove: () => {},
                              patch: (id, f) => { canvasList = canvasList.map((w) => (w.id === id ? { ...w, ...f } : w)); } });
  const slots = [...c.box.querySelectorAll(".builder-slot")];
  const front = slots[0].querySelector('button[aria-label="Bring to front"]');
  check("canvas chrome: Bring to front / Send to back on each card",
        front && slots[0].querySelector('button[aria-label="Send to back"]')
        && slots[1].querySelector('button[aria-label="Bring to front"]').disabled);
  c.r.act(() => { front.click(); });
  check("...and they restack", L.layerOrder(canvasList).join() === "b,a");
  c.r.render({ widgets: canvasList, preview: [], selected: null, editable: true, setSelected: () => {},
               remove: () => {}, patch: () => {} });
  const zs = [...c.box.querySelectorAll(".builder-slot")].map((s) => s.style.zIndex);
  check("canvas: the new layer shows at once (z-index on the slot)", zs.includes("1"), JSON.stringify(zs));
  // -- 5. dragging rides on top
  const bar = c.box.querySelectorAll(".builder-slot-bar")[0];
  c.r.act(() => { bar.dispatchEvent(new dom.window.MouseEvent("pointerdown", { bubbles: true, clientX: 5, clientY: 5 })); });
  check("the card being dragged rides on top while it moves",
        c.box.querySelectorAll(".builder-slot")[0].style.zIndex === "999");
  c.close();
}

rmSync(OUT, { force: true });
console.log(bad ? `${bad} check(s) failed` : "all good");
process.exit(bad ? 1 : 0);
