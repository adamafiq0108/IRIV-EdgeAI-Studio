// The Workflow page's Full screen (Tok Aba, 2026-10-14):
// frontend/src/workflow/useCanvasFullscreen.js, wired in pages/Workflow.jsx.
//   1. Workflow.jsx: the button beside the canvas zoom/fit controls (aria-label Full screen / Exit full
//      screen, aria-pressed, Maximize / Minimize), the section's
//      wf-fullscreen class from the hook, Escape left to the context menu
//      and the blueprint editor; theme.css pins the section over the window
//      below the dialogs and toasts
//   2. CSS-only (no Fullscreen API): the button toggles state, label and the
//      section class, <body> gets wf-fs-on; Escape leaves; an Escape typed
//      in a field, or while the page says Escape is busy, or while a dialog
//      is open, does not
//   3. with the Fullscreen API: entering asks for it on the DOCUMENT (so the
//      portalled toasts and dialogs stay visible); the browser leaving it by
//      itself (Escape) brings the CSS layer down; the button exits both
//   4. a document already in fullscreen (the shell's own button) is not
//      taken out of it when the workflow leaves full screen
//   5. unmounting while in full screen restores <body> and exits the
//      browser fullscreen the hook asked for
//   6. no raw /api literal and no em dash in the new code
//
//   node tests/check_workflowfullscreen.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".workflowfullscreen-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Event", "CustomEvent",
                 "MouseEvent", "KeyboardEvent", "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const SRC = readFileSync(join(FRONT, "src", "pages", "Workflow.jsx"), "utf8");
const HOOK = readFileSync(join(FRONT, "src", "workflow", "useCanvasFullscreen.js"), "utf8");
const CSS = readFileSync(join(FRONT, "src", "theme.css"), "utf8");

// 1 ---------------------------------------------------------------------------
check("1. Workflow.jsx uses the hook, Escape left to the menu and the blueprint editor",
      SRC.includes('import useCanvasFullscreen from "../workflow/useCanvasFullscreen.js";')
      && SRC.includes("useCanvasFullscreen({ escBlocked: !!menu || !!bpId })"));
check("...the section takes wf-fullscreen from it",
      SRC.includes('className={"page active wf-page" + (canvasFs.full ? " wf-fullscreen" : "")}'));
check("...the button sits with the canvas zoom/fit controls: labels, pressed state, icons, toggle",
      /<Controls showInteractive=\{false\}>\s*(\{\/\*[\s\S]*?\*\/\}\s*)?<button type="button" className="react-flow__controls-button"\s+data-wf-fs-toggle=""/.test(SRC)
      && SRC.includes('aria-label={canvasFs.full ? "Exit full screen" : "Full screen"}')
      && SRC.includes("aria-pressed={canvasFs.full}") && SRC.includes("onClick={canvasFs.toggle}")
      && SRC.includes("<Minimize2 aria-hidden") && SRC.includes("<Maximize2 aria-hidden"));
const rule = /\.page\.wf-page\.wf-fullscreen \{([^}]*)\}/.exec(CSS)?.[1] || "";
check("...theme.css pins it over the window, below dialogs (50) and toasts (60)",
      /position: fixed/.test(rule) && /inset: 0/.test(rule) && /z-index: 45/.test(rule)
      && /background: var\(--bg\)/.test(rule) && CSS.includes("body.wf-fs-on { overflow: hidden; }"),
      rule);

// The button as Workflow.jsx draws it, around the real hook.
await build({
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h, useState } from "react";
      import useCanvasFullscreen from "${p("src/workflow/useCanvasFullscreen.js")}";
      export { act };
      function Page({ busy }) {
        const fs = useCanvasFullscreen({ escBlocked: !!busy });
        return h("section", { className: "page active wf-page" + (fs.full ? " wf-fullscreen" : "") },
          h("button", { type: "button", "data-wf-fs-toggle": "", "aria-pressed": fs.full,
                        "aria-label": fs.full ? "Exit full screen" : "Full screen",
                        onClick: fs.toggle }),
          h("input", { "data-field": "" }));
      }
      export function mount(el, props = {}) {
        const root = createRoot(el);
        const draw = (q) => act(() => { root.render(h(Page, q)); });
        draw(props);
        return { redraw: draw, unmount: () => act(() => root.unmount()) };
      }
    `,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent", loader: { ".css": "empty" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { act } = m;
const flush = async () => {
  for (let i = 0; i < 4; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await flush();
};
const key = async (target, k = "Escape") => {
  await act(async () => {
    target.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true }));
  });
  await flush();
};

// A fake Fullscreen API on the document (jsdom has none).
const doc = document;
const html = doc.documentElement;
let fsEl = null;
const calls = [];
function installFs() {
  Object.defineProperty(doc, "fullscreenElement", { configurable: true, get: () => fsEl });
  html.requestFullscreen = function () {
    calls.push(["request", this === html ? "document" : this.tagName]);
    fsEl = this; doc.dispatchEvent(new window.Event("fullscreenchange"));
    return Promise.resolve();
  };
  doc.exitFullscreen = () => {
    calls.push(["exit"]);
    fsEl = null; doc.dispatchEvent(new window.Event("fullscreenchange"));
    return Promise.resolve();
  };
}
function removeFs() {
  delete html.requestFullscreen; delete doc.exitFullscreen;
  Object.defineProperty(doc, "fullscreenElement", { configurable: true, get: () => null });
  fsEl = null; calls.length = 0;
}

async function withPage(props, fn) {
  const box = doc.createElement("div");
  doc.body.appendChild(box);
  const r = m.mount(box, props);
  await flush();
  try { return await fn(box, r); } finally {
    try { r.unmount(); } catch { /* already */ }
    box.remove();
  }
}
const btn = (box) => box.querySelector("[data-wf-fs-toggle]");
const sec = (box) => box.querySelector("section");

// 2 ---------------------------------------------------------------------------
removeFs();
await withPage({}, async (box, r) => {
  check("2. CSS-only: starts normal, labelled Full screen",
        !sec(box).classList.contains("wf-fullscreen")
        && btn(box).getAttribute("aria-label") === "Full screen"
        && btn(box).getAttribute("aria-pressed") === "false");
  await click(btn(box));
  check("...a click: full screen, label Exit full screen, body wf-fs-on",
        sec(box).classList.contains("wf-fullscreen")
        && btn(box).getAttribute("aria-label") === "Exit full screen"
        && btn(box).getAttribute("aria-pressed") === "true"
        && doc.body.classList.contains("wf-fs-on"));
  await key(box.querySelector("[data-field]"));
  check("...Escape typed in a field does not leave", sec(box).classList.contains("wf-fullscreen"));
  const dlg = doc.createElement("div");
  dlg.setAttribute("role", "dialog");
  doc.body.appendChild(dlg);
  await key(doc.body);
  check("...nor while a dialog is open", sec(box).classList.contains("wf-fullscreen"));
  dlg.remove();
  r.redraw({ busy: true });
  await flush();
  await key(doc.body);
  check("...nor while the page uses Escape (context menu, blueprint editor)",
        sec(box).classList.contains("wf-fullscreen"));
  r.redraw({ busy: false });
  await flush();
  await key(doc.body);
  check("...a plain Escape leaves, and <body> is restored",
        !sec(box).classList.contains("wf-fullscreen")
        && btn(box).getAttribute("aria-label") === "Full screen"
        && !doc.body.classList.contains("wf-fs-on"));
  await click(btn(box));
  await click(btn(box));
  check("...the button toggles back too", !sec(box).classList.contains("wf-fullscreen"));
});

// 3 ---------------------------------------------------------------------------
installFs();
await withPage({}, async (box) => {
  await click(btn(box));
  check("3. Fullscreen API: asked for on the DOCUMENT (toasts and dialogs stay visible)",
        JSON.stringify(calls) === '[["request","document"]]' && fsEl === html
        && sec(box).classList.contains("wf-fullscreen"), JSON.stringify(calls));
  // The browser leaves by itself (Escape is the browser's while fullscreen).
  fsEl = null;
  await act(async () => { doc.dispatchEvent(new window.Event("fullscreenchange")); });
  await flush();
  check("...the browser leaving brings the CSS layer down with it",
        !sec(box).classList.contains("wf-fullscreen") && !doc.body.classList.contains("wf-fs-on"));
  calls.length = 0;
  await click(btn(box));
  await key(doc.body);
  check("...an Escape the page does receive exits both too",
        JSON.stringify(calls) === '[["request","document"],["exit"]]' && fsEl === null
        && !sec(box).classList.contains("wf-fullscreen"), JSON.stringify(calls));
  calls.length = 0;
  await click(btn(box));
  await click(btn(box));
  check("...the button exits both", JSON.stringify(calls) === '[["request","document"],["exit"]]'
        && fsEl === null && !sec(box).classList.contains("wf-fullscreen"), JSON.stringify(calls));
});

// 4 ---------------------------------------------------------------------------
calls.length = 0;
fsEl = html;                        // the shell's own Full screen is on
await withPage({}, async (box) => {
  await click(btn(box));
  await click(btn(box));
  check("4. an already-fullscreen document is neither asked again nor taken out",
        calls.length === 0 && fsEl === html && !sec(box).classList.contains("wf-fullscreen"),
        JSON.stringify(calls));
});
fsEl = null;

// 5 ---------------------------------------------------------------------------
calls.length = 0;
{
  const box = doc.createElement("div");
  doc.body.appendChild(box);
  const r = m.mount(box, {});
  await flush();
  await click(btn(box));
  r.unmount();
  await flush();
  check("5. leaving the page in full screen restores <body> and exits the browser fullscreen",
        !doc.body.classList.contains("wf-fs-on") && fsEl === null
        && JSON.stringify(calls) === '[["request","document"],["exit"]]', JSON.stringify(calls));
  box.remove();
}

// 6 ---------------------------------------------------------------------------
check("6. no raw /api literal and no em dash in the hook",
      !/["'`]\/api\//.test(HOOK) && !HOOK.includes("\u2014"));
const added = SRC.split("\n").filter((l) => /canvasFs|useCanvasFullscreen|Maximize2|Minimize2/.test(l));
check("...nor in the Workflow.jsx lines that wire it",
      added.length >= 6 && !added.some((l) => /\/api\//.test(l) || l.includes("\u2014")));

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
