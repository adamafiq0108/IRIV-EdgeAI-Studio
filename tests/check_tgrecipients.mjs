// The Telegram node's Send to: All linked people / Selected people / A chat ID
// (Tok Aba, 2026-10-14). Pinned:
//   1. the inspector offers the three choices, and a node saved before (no
//      to_mode) opens on "A chat ID (advanced)" with its chat box as it was
//   2. All shows how many people get it, who on expand, and says groups are
//      only sent to when ticked
//   3. Selected lists everyone linked as a checkbox (name shown as, @username,
//      the email when the server sent one), stores Telegram user ids only, a
//      person linked only in a group cannot be ticked, groups are offered
//      separately, and a selected id no longer linked is flagged
//   4. nobody linked yet says what to do
//   5. the node's summary says who hears it
//
//   node tests/check_tgrecipients.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".tgrecipients-test.cjs");

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
global.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
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
        export const api = {
          get(url) { globalThis.__gets = (globalThis.__gets || []).concat([url]);
                     return Promise.resolve(globalThis.__answer); },
          post() { return Promise.resolve({}); }, put() { return Promise.resolve({}); },
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
    b.onResolve({ filter: /^@xyflow\/react$/ }, () => ({ path: "xyflow-shim", namespace: "shim" }));
    b.onResolve({ filter: /^@xyflow\/react\/dist\/style\.css$/ }, () => (
      { path: "xyflow-css", namespace: "shim" }));
    b.onLoad({ filter: /^xyflow-css$/, namespace: "shim" }, () => ({
      contents: "", resolveDir: FRONT, loader: "js" }));
    b.onLoad({ filter: /^xyflow-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement } from "react";
        export const Position = { Left: "left", Right: "right", Top: "top", Bottom: "bottom" };
        export const MarkerType = { ArrowClosed: "arrowclosed" };
        export const Handle = () => createElement("div");
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
const expose = {
  name: "expose",
  setup(b) {
    b.onLoad({ filter: /pages[\\/]Workflow\.jsx$/ }, (args) => ({
      contents: readFileSync(args.path, "utf8") + "\nexport { TelegramPeople };\n",
      loader: "jsx", resolveDir: dirname(args.path),
    }));
  },
};
const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [expose, shimApi, shimXyflow],
  stdin: { contents: `
    import { createRoot } from "react-dom/client";
    import { act, createElement as h } from "react";
    import { TelegramPeople } from "${p("src/pages/Workflow.jsx")}";
    import { nodeSummary } from "${p("src/workflow/nodeSummary.js")}";
    export { act, h, TelegramPeople, nodeSummary, createRoot };
  `, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent", loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);

const LIST = {
  ok: true, bot: "cytron_tech_bot",
  people: [
    { user_id: "781784546", username: "a_afiq123", name: "Adam Afiq",
      email: "adamafiq@cytron.io", private: true },
    { user_id: "222", username: "", name: "Bo", email: "bo@cytron.io", private: true },
    { user_id: "333", username: "", name: "Cy", email: "cy@cytron.io", private: false },
  ],
  groups: [{ chat_id: "-100777", people: ["333"] }],
};

async function render(props, answer = LIST) {
  globalThis.__answer = answer;
  const box = document.createElement("div");
  document.body.appendChild(box);
  const root = m.createRoot(box);
  await m.act(async () => { root.render(m.h(m.TelegramPeople, props)); });
  await m.act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { box, done: () => { m.act(() => root.unmount()); box.remove(); } };
}

// -- 1. the three choices ------------------------------------------------------------
const ui = readFileSync(join(FRONT, "src", "pages", "Workflow.jsx"), "utf8");
check("Send to offers All linked people, Selected people, A chat ID (advanced)",
      ui.includes('<option value="all">All linked people</option>')
      && ui.includes('<option value="selected">Selected people</option>')
      && ui.includes('<option value="chat">A chat ID (advanced)</option>'));
check("a node saved before (no to_mode) opens on the chat ID, its box as it was",
      ui.includes('value={["all", "selected"].includes(d.to_mode) ? d.to_mode : "chat"}')
      && ui.includes('<Field label="Chat ID"') && ui.includes('onChange={set("chat_id")} />'));

// -- 2. All -------------------------------------------------------------------------------
let r = await render({ d: { to_mode: "all" }, mode: "all", editable: true, onChange() {} });
const allText = r.box.textContent;
check("All says how many people get it, by the bot's name",
      allText.includes("2 people linked to cytron_tech_bot will get it"), allText);
check("...who, on expand, as the Connected Bots page names them",
      allText.includes('name shown as "Adam Afiq" · @a_afiq123 · adamafiq@cytron.io'), allText);
check("...one linked only in a group is said, not counted",
      allText.includes("1 linked only in a group"));
check("...and groups are only sent to when ticked under Selected",
      allText.includes("Groups are not included in All"));
check("the list is read from the master's people route",
      (globalThis.__gets || []).includes("/api/telegram/recipients"));
r.done();

// -- 3. Selected ------------------------------------------------------------------------
let changed = null;
r = await render({ d: { to_mode: "selected", to_people: ["222", "999"] }, mode: "selected",
                   editable: true, onChange: (v) => { changed = v; } });
const boxes = [...r.box.querySelectorAll("input[type=checkbox]")];
check("Selected: one checkbox per person and per group",
      boxes.length === 4, boxes.length);
check("...the chosen one ticked, a group-only person cannot be ticked",
      boxes[1].checked && !boxes[0].checked && boxes[2].disabled);
check("...a selected id no longer linked is flagged",
      r.box.textContent.includes("1 selected person is no longer linked"), r.box.textContent);
await m.act(async () => { boxes[0].click(); });
check("...ticking stores the Telegram user id, nothing else",
      changed && JSON.stringify(changed.to_people) === '["222","999","781784546"]'
      && !JSON.stringify(changed).includes("cytron.io"), JSON.stringify(changed));
await m.act(async () => { boxes[3].click(); });
check("...and a group is a target only by its own chat id",
      changed && changed.to_people.includes("-100777"), JSON.stringify(changed));
r.done();

// -- 4. nobody linked ---------------------------------------------------------------------
r = await render({ d: { to_mode: "all" }, mode: "all", editable: true, onChange() {} },
                 { ok: true, bot: "x", people: [], groups: [] });
check("nobody linked yet says what to do",
      r.box.textContent.includes("Nobody is linked to this bot yet: they send /start to the bot first"));
r.done();
r = await render({ d: { to_mode: "all" }, mode: "all", editable: true, onChange() {} },
                 { ok: true, bot: "x", people: [{ user_id: "5", username: "u", name: "N",
                                                  private: true }], groups: [] });
check("a non-admin answer (no email) still names the person",
      r.box.textContent.includes('name shown as "N" · @u') && !r.box.textContent.includes("undefined"));
r.done();

// -- 5. the summary ---------------------------------------------------------------------------
const s = (d) => m.nodeSummary("output.telegram", d).does;
check("summary: all / selected / chat / nothing",
      s({ to_mode: "all" }) === "send a Telegram message to all linked people"
      && s({ to_mode: "selected", to_people: ["1", "2", "3"] })
        === "send a Telegram message to 3 selected people"
      && s({ to_mode: "selected", to_people: ["1"] })
        === "send a Telegram message to 1 selected person"
      && s({ chat_id: "781784546" }) === "send a Telegram message to chat 781784546"
      && s({}) === "send a Telegram message",
      [s({ to_mode: "all" }), s({ chat_id: "781784546" })].join(" | "));

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall Telegram recipient checks passed");
dom.window.close();
process.exit(0);
