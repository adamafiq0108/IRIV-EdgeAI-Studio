// Every password field has the same show/hide eye: one shared PasswordInput.
//
// Beta feedback asked for a reveal toggle on password fields; a handful had
// one each (sign-in, Wi-Fi join, the RTSP camera), the rest had none (the
// access-point password, MQTT broker login, SMTP, Telegram, Blynk, update
// token, account setup, workflow node secrets). Pinned:
//   1. masked by default; the eye (type=button, aria-label, not in the Tab
//      path) reveals what the FIELD holds and hides it again; the caller's
//      autocomplete / name / class / placeholder are kept
//   2. a password and its repeat can reveal together
//   3. the design-system variant behaves the same
//   4. no file draws its own password input any more
//
//   node tests/check_password.mjs
import { readdirSync } from "node:fs";
import { readText } from "./_text.mjs";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".password-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
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

// The api client, under the test's control: a press is a POST, recorded.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          post(url, body) { globalThis.__posts.push([url, body]);
                            return Promise.resolve({ ok: true }); },
          get() { return Promise.resolve({}); },
          put() { return Promise.resolve({}); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
        export function saveBlob() {}
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const shimXyflow = {
  name: "shim-xyflow",
  setup(b) {
    b.onResolve({ filter: /^@xyflow\/react$/ }, () => (
      { path: "xyflow-shim", namespace: "shim" }));
    b.onResolve({ filter: /^@xyflow\/react\/dist\/style\.css$/ }, () => (
      { path: "xyflow-css", namespace: "shim" }));
    b.onLoad({ filter: /^xyflow-css$/, namespace: "shim" }, () => ({
      contents: "", resolveDir: FRONT, loader: "js" }));
    b.onLoad({ filter: /^xyflow-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement } from "react";
        export const Position = { Left: "left", Right: "right",
                                  Top: "top", Bottom: "bottom" };
        export const MarkerType = { ArrowClosed: "arrowclosed" };
        export const Handle = ({ id, type }) => createElement("div",
          { "data-handleid": id || "", "data-porttype": type });
        export const BaseEdge = () => createElement("div");
        export const EdgeLabelRenderer = ({ children }) => children || null;
        export const ReactFlow = () => createElement("div");
        export const ReactFlowProvider = ({ children }) => children || null;
        export const Background = () => createElement("div");
        export const Controls = () => createElement("div");
        export const MiniMap = () => createElement("div");
        export const Panel = ({ children }) => children || null;
        export const useReactFlow = () => ({});
        export const useUpdateNodeInternals = () => () => {};
        export const useStore = (s) => s({ edges: [], nodeLookup: new Map() });
        export const useNodesState = (v) => [v, () => {}, () => {}];
        export const useEdgesState = (v) => [v, () => {}, () => {}];
        export const addEdge = (c, e) => e;
        export const applyNodeChanges = (c, n) => n;
        export const applyEdgeChanges = (c, e) => e;
        export const getBezierPath = () => ["", 0, 0];
        export const getSmoothStepPath = () => ["", 0, 0];
        export const getStraightPath = () => ["", 0, 0];
        export const useOnSelectionChange = () => {};
        export const useKeyPress = () => false;
        export const SelectionMode = { Partial: "partial" };
        export const ConnectionMode = { Loose: "loose" };
        export const ConnectionLineType = { Bezier: "bezier" };
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h, useState } from "react";
import { PasswordInput } from "${p("src/components/ui/input.jsx")}";
export { act, h, PasswordInput, useState };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi, shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  alias: { "@": join(FRONT, "src") },
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;

function render(node, fn) {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, node);
  try { return fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}
const click = (el) => m.act(() => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
});

// 1. the field on its own: masked, an eye, reveals what is typed, hides again
render(h(m.PasswordInput, { native: true, className: "input", name: "pw",
                            autoComplete: "new-password", defaultValue: "s3cret",
                            placeholder: "unchanged" }), (box) => {
  const input = box.querySelector("input");
  const eye = box.querySelector("button");
  check("starts masked", input?.type === "password");
  check("...with an eye that says what it does",
        eye?.getAttribute("aria-label") === "Show password"
        && eye.getAttribute("type") === "button"
        && eye.getAttribute("tabindex") === "-1");
  check("...and keeps the caller's attributes (autocomplete, name, class)",
        input.getAttribute("autocomplete") === "new-password"
        && input.name === "pw" && input.className === "input"
        && input.placeholder === "unchanged");
  click(eye);
  check("the eye reveals the field", input.type === "text"
        && eye.getAttribute("aria-label") === "Hide password"
        && eye.getAttribute("aria-pressed") === "true");
  check("...what the FIELD holds, nothing fetched", input.value === "s3cret");
  click(eye);
  check("...and masks it again", input.type === "password");
});

// 2. a password and its repeat reveal together when the page says so
render(h("div", null,
  h(function Pair() {
    const [show, setShow] = m.useState(false);
    const t = () => setShow((v) => !v);
    return h("div", null,
             h(m.PasswordInput, { native: true, shown: show, onToggle: t }),
             h(m.PasswordInput, { native: true, shown: show, onToggle: t }));
  })), (box) => {
  const [a, b] = box.querySelectorAll("input");
  click(box.querySelectorAll("button")[1]);
  check("a password and its repeat reveal together", a.type === "text"
        && b.type === "text");
});

// 3. the design-system variant works the same way
render(h(m.PasswordInput, { id: "ota-token", autoComplete: "off" }), (box) => {
  const input = box.querySelector("input");
  const eye = box.querySelector("button");
  check("design-system field: masked, with the same eye",
        input?.type === "password" && eye?.getAttribute("aria-label") === "Show password");
  click(eye);
  check("...and reveals on its own state", input.type === "text");
});

// 4. every password field in the app goes through it
const files = [];
(function walk(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name !== "reui" && e.name !== "node_modules") walk(full);
    } else if (/\.jsx?$/.test(e.name)) files.push(full);
  }
})(join(FRONT, "src"));
const raw = files.filter((f) => !/ui[\\/]input\.jsx$/.test(f))
  .filter((f) => /type="password"|"text" : "password"|type: "password"/.test(
    readText(f)
      // A form-field SPEC ({type: "password"}) is drawn by FormDialog, which
      // uses PasswordInput -- checked below, not counted here.
      .replace(/type: "password"/g, "")))
  .map((f) => f.slice(FRONT.length + 1));
check("no password field draws its own <input type=\"password\">",
      raw.length === 0, raw.join(", "));
check("FormDialog's password fields use it too",
      /f\.type === "password" \? \(\s*<PasswordInput/.test(
        readText(join(FRONT, "src", "components", "ui", "dialogs.jsx"))));
for (const f of ["components/NetworkPanel.jsx", "components/BrokerPanel.jsx",
                 "components/MailPanel.jsx", "components/TelegramPanel.jsx",
                 "components/UpdatePanel.jsx", "pages/AccountSetup.jsx",
                 "pages/Blynk.jsx", "pages/Login.jsx", "pages/Workflow.jsx",
                 "pages/Detection.jsx"]) {
  check(`${f} uses PasswordInput`,
        readText(join(FRONT, "src", f)).includes("<PasswordInput"));
}

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall password-field checks passed");
dom.window.close();
process.exit(0);
