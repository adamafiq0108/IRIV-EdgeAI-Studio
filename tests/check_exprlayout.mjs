// The Custom Formula editor (frontend/src/workflow/ExprEditor.jsx), Tok Aba's
// layout of 2026-10-14. Rendered against a shimmed validate-expr:
//   1. layout: the Expression label with its info mark and the "Formula
//      editor" tag, a Templates control, ONE textarea (the single source of
//      truth: no second editor anywhere), the chip row "Variables are
//      highlighted", the operator row (+ - x / % ( ) > == and or, More for
//      < >= <= != not), the Variables and Functions panels, the validity line
//   2. the operator buttons insert at the caret; the "x" button inserts *
//      and the division sign inserts /; More shows the rest
//   3. Variables and Functions panels open, search, and insert at the caret;
//      a function goes in as name( with the caret inside; iff says "if"
//   4. autocomplete opens while typing a word, inserts on Enter, Tab and
//      click, and does not take Enter while it is closed
//   5. a template fills the box with an editable formula
//   6. the validity line: valid (green, Result type: Number, Preview
//      sentence), invalid (the server's message), a name the server reads
//      that is not published (amber chip and line), and "Now:" from the
//      node's own published value
//   7. exprNames and EXPR_RESERVED are unchanged (ports per name); no em dash
//
//   node tests/check_exprlayout.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".exprlayout-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "HTMLTextAreaElement", "HTMLSelectElement",
                 "Element", "Node", "SVGElement", "Event", "CustomEvent", "MouseEvent",
                 "PointerEvent", "KeyboardEvent", "FocusEvent", "requestAnimationFrame",
                 "cancelAnimationFrame", "getComputedStyle", "localStorage"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => ({ path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          post(url, body) { globalThis.__posts.push([url, body]);
            return Promise.resolve(globalThis.__validate(body.expr)); },
          get() { return Promise.resolve({}); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shimApi],
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h, useState } from "react";
      import ExprEditor, { exprNames, EXPR_RESERVED, TEMPLATES, wordAt }
        from "${p("src/workflow/ExprEditor.jsx")}";
      import { WorkflowCtx } from "${p("src/workflow/nodes.jsx")}";
      export { act, exprNames, EXPR_RESERVED, TEMPLATES, wordAt };
      function Host({ start, props }) {
        const [v, setV] = useState(start);
        globalThis.__value = v;
        return h(WorkflowCtx.Provider, { value: { values: globalThis.__values || {} } },
          h(ExprEditor, { value: v, editable: true, onChange: setV, ...props }));
      }
      export function mount(el, start = "", props = {}) {
        const root = createRoot(el);
        act(() => { root.render(h(Host, { start, props })); });
        return { unmount: () => act(() => root.unmount()) };
      }
    `,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent", loader: { ".css": "empty", ".svg": "dataurl", ".png": "dataurl" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { act } = m;

const flush = async (ms = 0) => {
  for (let i = 0; i < 4; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  }
};
const settle = () => flush(110);            // past the 300 ms validation debounce
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await flush();
};
const mouseDown = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true, cancelable: true })); });
  await flush();
};
const setter = (proto) => Object.getOwnPropertyDescriptor(proto, "value").set;
const type = async (el, value, caret = value.length) => {
  await act(async () => {
    setter(el.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype
                                     : window.HTMLInputElement.prototype).call(el, value);
    el.selectionStart = el.selectionEnd = caret;
    el.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
  await flush();
};
const key = async (el, k) => {
  let ev;
  await act(async () => {
    ev = new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
  });
  await flush();
  return ev;
};
const caretAt = (el, n) => { el.focus(); el.selectionStart = el.selectionEnd = n; };
const btnText = (box, t) => [...box.querySelectorAll("button")].find((b) => b.textContent.trim() === t);

globalThis.__validate = (expr) => ({ ok: true, error: null,
  keys: [...new Set(String(expr).match(/[A-Za-z_][A-Za-z0-9_]*/g) || [])]
    .filter((w) => !["max", "min", "iff", "and", "or", "not"].includes(w)) });

async function withEditor(start, props, fn) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, start, props);
  await flush();
  try { return await fn(box); } finally { r.unmount(); box.remove(); }
}
const area = (box) => box.querySelector("textarea");

// 1 ---------------------------------------------------------------------------
await withEditor("(violation_count / max(object_count, 1)) * 100",
                 { wired: [{ name: "violation_count", label: "Violations" },
                           { name: "object_count", label: "Objects" }],
                   knownNames: ["violation_count", "object_count"] }, async (box) => {
  await settle();
  const t = box.textContent;
  check("1. Expression label with an info mark, the Formula editor tag, Templates",
        box.querySelector(".expr-head label")?.textContent.startsWith("Expression")
        && !!box.querySelector(".expr-head [aria-label='About Expression'], .expr-head button")
        && box.querySelector(".expr-tag")?.textContent === "Formula editor"
        && !!box.querySelector("select[aria-label='Templates']"));
  check("...ONE textarea, monospace box, and no second editor",
        box.querySelectorAll("textarea").length === 1 && area(box).classList.contains("expr-area")
        && box.querySelectorAll("[contenteditable='true']").length === 0);
  const chips = [...box.querySelectorAll(".expr-chip")].map((c) => c.textContent);
  check("...the names it uses as chips, Variables are highlighted",
        JSON.stringify(chips) === '["violation_count","object_count"]'
        && t.includes("Variables are highlighted"), JSON.stringify(chips));
  const ops = [...box.querySelectorAll(".expr-ops button[data-op]")].map((b) => b.textContent);
  check("...the operator row as designed, with More",
        JSON.stringify(ops) === JSON.stringify(["+", "−", "×", "÷", "%", "(", ")", ">", "==", "and", "or"])
        && !!btnText(box, "More"), JSON.stringify(ops));
  check("...Variables and Functions panels with their lines",
        box.querySelector("[data-testid=expr-variables]")?.textContent.includes("Search and insert available variables")
        && box.querySelector("[data-testid=expr-functions]")?.textContent.includes(
          "Insert functions such as max(), min(), and if()"));
  const v = box.querySelector("[data-expr-validity]").textContent.replace(/\s+/g, " ");
  check("...valid: Formula looks valid, Result type: Number, the Preview sentence",
        v.includes("Formula looks valid") && v.includes("Result type: Number")
        && v.includes("Preview available when input data is available."), v);
});

// 2 ---------------------------------------------------------------------------
await withEditor("a b", {}, async (box) => {
  const el = area(box);
  caretAt(el, 1);
  await click(box.querySelector(".expr-ops button[data-op='*']"));
  check("2. the multiplication button inserts * at the caret",
        globalThis.__value === "a * b" || globalThis.__value === "a *  b", JSON.stringify(globalThis.__value));
  await type(el, "x y", 1);
  caretAt(el, 1);
  await click(box.querySelector(".expr-ops button[data-op='/']"));
  check("...the division button inserts /", globalThis.__value.includes("x / ") , globalThis.__value);
  await click(btnText(box, "More"));
  const more = [...box.querySelectorAll(".expr-ops button[data-op]")].map((b) => b.dataset.op);
  check("...More shows < >= <= != not", ["<", ">=", "<=", "!=", "not"].every((o) => more.includes(o)),
        JSON.stringify(more));
});

// 3 ---------------------------------------------------------------------------
await withEditor("", { wired: [{ name: "people_inside", label: "Occupancy" }],
                       knownNames: ["people_inside"] }, async (box) => {
  const el = area(box);
  const vp = box.querySelector("[data-testid=expr-variables]");
  await click(vp.querySelector(".expr-panel-head"));
  check("3. Variables opens with a search and the wired names",
        vp.querySelector("input[aria-label='Search variables']")
        && vp.textContent.includes("people_inside") && vp.textContent.includes("Occupancy"));
  caretAt(el, 0);
  await click([...vp.querySelectorAll(".expr-panel-row")].find((b) => b.textContent.includes("people_inside")));
  check("...a click inserts the name at the caret", globalThis.__value === "people_inside", globalThis.__value);
  const fp = box.querySelector("[data-testid=expr-functions]");
  await click(fp.querySelector(".expr-panel-head"));
  await type(fp.querySelector("input"), "if");
  const rows = [...fp.querySelectorAll(".expr-panel-row")];
  check("...Functions search finds iff by 'if', and the hint says it is the engine's if",
        rows.some((r) => r.textContent.includes("iff(cond, a, b)") && r.textContent.includes("the engine's if")),
        rows.map((r) => r.textContent).join(" | "));
  caretAt(area(box), 0);
  await click(rows.find((r) => r.textContent.includes("iff(cond, a, b)")));
  check("...a function goes in as name( at the caret",
        globalThis.__value === "iff(people_inside", globalThis.__value);
});

// 4 ---------------------------------------------------------------------------
await withEditor("", { wired: [{ name: "object_count", label: "Objects" }], knownNames: ["object_count"] },
async (box) => {
  const el = area(box);
  const enterClosed = await key(el, "Enter");
  check("4. with no suggestion open, Enter is the textarea's", !enterClosed.defaultPrevented);
  await type(el, "obj");
  const list = box.querySelector("[role=listbox]");
  check("...typing part of a name opens the suggestions",
        !!list && list.textContent.includes("object_count")
        && el.getAttribute("aria-expanded") === "true", box.innerHTML.slice(0, 300));
  const ev = await key(el, "Enter");
  check("...Enter inserts the suggestion (and is taken)",
        ev.defaultPrevented && globalThis.__value === "object_count", globalThis.__value);
  await type(el, "object_count + ma");
  const ev2 = await key(el, "Tab");
  check("...Tab inserts a function with its bracket",
        ev2.defaultPrevented && globalThis.__value === "object_count + max(", globalThis.__value);
  await type(el, "mi");
  const opt = [...box.querySelectorAll("[role=option]")].find((o) => o.textContent.includes("min("));
  await mouseDown(opt);
  check("...a click on a suggestion inserts it", globalThis.__value === "min(", globalThis.__value);
});

// 5 ---------------------------------------------------------------------------
await withEditor("old", {}, async (box) => {
  const sel = box.querySelector("select[aria-label='Templates']");
  const vp = m.TEMPLATES.find((t) => t.label === "Violation percentage");
  check("5. the Violation percentage template is offered in plain names",
        vp?.expr === "(violation_count / max(object_count, 1)) * 100"
        && [...sel.options].some((o) => o.value === vp.expr));
  await act(async () => {
    sel.value = vp.expr;
    sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  await flush();
  check("...picking it fills the box with that formula, still editable",
        globalThis.__value === vp.expr && !area(box).disabled, globalThis.__value);
});

// 6 ---------------------------------------------------------------------------
globalThis.__validate = () => ({ ok: false, error: "invalid syntax at column 7", keys: [] });
await withEditor("a + * b", {}, async (box) => {
  await settle();
  const v = box.querySelector("[data-expr-validity]");
  check("6. invalid: the server's message, in the alert colour, box marked",
        v.textContent.includes("invalid syntax at column 7") && !!v.querySelector(".expr-status.is-bad")
        && box.querySelector(".expr-box").classList.contains("is-bad")
        && area(box).getAttribute("aria-invalid") === "true");
});
globalThis.__validate = (expr) => ({ ok: true, error: null, keys: ["fn_gone_count", "capacity"] });
await withEditor("fn_gone_count / capacity", { statKeys: ["fn_other"], knownNames: ["capacity"] },
async (box) => {
  await settle();
  const warn = box.querySelector(".expr-chip.is-warn");
  check("...a name the server reads but nobody publishes: amber chip and line",
        warn?.textContent === "fn_gone_count"
        && box.querySelector(".expr-status-line.is-warn")?.textContent.includes("fn_gone_count"));
});
globalThis.__validate = () => ({ ok: true, error: null, keys: ["a"] });
globalThis.__values = { fn_f1_value: 42.5 };
await withEditor("a > 3", { fnId: "f1" }, async (box) => {
  await settle();
  const r = box.querySelector("[data-expr-result]").textContent.replace(/\s+/g, " ");
  check("...live: Now: the node's own published value; a comparison says 1 or 0",
        box.querySelector("[data-expr-now]")?.textContent === "42.5"
        && r.includes("(a comparison gives 1 or 0)") && !r.includes("Preview available"), r);
});
globalThis.__values = {};

// 7 ---------------------------------------------------------------------------
check("7. exprNames unchanged: engine words are not ports",
      JSON.stringify(m.exprNames("(violation_count / max(object_count, 1)) * 100 and iff(x, pi, e)"))
        === '["violation_count","object_count","x"]');
check("...wordAt reads the word at the caret", JSON.stringify(m.wordAt("a + obj", 7)) === '{"start":4,"word":"obj"}');
const SRC = readFileSync(join(FRONT, "src", "workflow", "ExprEditor.jsx"), "utf8");
check("...no em dash, and no raw /api literal other than the validate endpoint",
      !SRC.includes("\u2014")
      && (SRC.match(/["'`]\/api\//g) || []).length === 1
      && SRC.includes('"/api/functions/validate-expr"'));

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall good");
process.exit(0);
