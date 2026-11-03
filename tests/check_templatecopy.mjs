// A template's own preview words, the browser side (Tok Aba, 2026-10-22,
// third round; 2026-10-23, edit what the customer sees): the developer's
// "What you get" editor and the Hub preview.
//
//   1. the preview shows the automatic text when the template has no words
//      of its own, and exactly the developer's lines, sentence and heading
//      when it does; HTML in them stays text
//   2. the editor opens on the AUTOMATIC lines, in the fields, said so in
//      plain words; an automatic line over 140 is shortened and flagged, so
//      Save is not blocked; an edit says "not saved yet"
//   3. a click on a line, the sentence or the heading in "What a customer
//      sees" puts the cursor in its field
//   4. counters, add / move / remove, limits (8 lines, 140 a line, 200 the
//      sentence, 40 the heading, no empty line) keep Save off with the reason
//   5. Save sends lines, sentence and heading and says "Your own text";
//      "Use automatic text" sends all empty and refills the automatic lines;
//      each says customer boxes get it with Publish templates
//   6. no em dash
//
//   node tests/check_templatecopy.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".templatecopy-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "FocusEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
                 "localStorage", "DocumentFragment"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const shim = (name, filter, contents) => ({
  name,
  setup(b) {
    b.onResolve({ filter }, (a) => (
      name !== "api-shim" || a.path.endsWith("api/client.js")
      || (a.path === "./client.js" && /[\\/]src[\\/]api$/.test(a.resolveDir))
        ? { path: name, namespace: "shim" } : undefined));
    b.onLoad({ filter: new RegExp(`^${name}$`), namespace: "shim" }, () => ({
      contents, resolveDir: FRONT, loader: "js" }));
  },
});
const shims = [
  shim("api-shim", /client\.js$/, `
    globalThis.__sent = [];
    export const api = {
      get() { return Promise.resolve({ ok: true, templates: [globalThis.__tpl] }); },
      put(url, body) { globalThis.__sent.push(["PUT", url, body]);
        return Promise.resolve({ ok: true, features: body.features, blurb: body.blurb }); },
      post() { return Promise.resolve({}); },
    };
    export function friendlyError(e) { return String(e && e.message || e); }`),
  shim("app-shim", /state\/app\.jsx$/, `
    export function useApp() { return { user: { is_admin: true, is_developer: true } }; }`),
];

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: shims,
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import TemplateCopyEditor from "${p("src/components/TemplateCopyEditor.jsx")}";
      import { TemplatePreview } from "${p("src/components/TemplateGallery.jsx")}";
      export { act };
      export function mount(el, which, props) {
        const root = createRoot(el);
        act(() => { root.render(h(which === "editor" ? TemplateCopyEditor : TemplatePreview, props)); });
        return { unmount: () => act(() => root.unmount()) };
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

const wait = async (ms) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  for (let i = 0; i < 4; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await wait(0);
};
const type = async (el, value) => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
  await wait(0);
};
const button = (text) => [...document.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);
const items = (root) => [...root.querySelectorAll("[data-what-you-get] li")].map((li) => li.textContent);

const AUTO = { key: "alpha", label: "Alpha", hint: "Its own line.", classes: ["person"],
               preview: { pages: ["Dashboard"], cards: ["In", "Out"], steps: 5, tables: [] } };

// ---- 1. the preview ------------------------------------------------------------------
const host = document.createElement("div");
document.body.appendChild(host);
let ui = m.mount(host, "preview", { t: AUTO, onUse: () => {} });
await wait(0);
check("no words of its own: the automatic text and the template's line",
      items(host).some((l) => l.includes("Looks for: people"))
      && host.querySelector("[data-template-blurb]")?.textContent === "Its own line.", items(host).join(" | "));
ui.unmount();
const CUSTOM = { ...AUTO, features: ["Counts who walks in", "Shows <b>today</b> & yesterday"],
                 blurb: "Know how busy the shop is.", heading: "Inside this template" };
ui = m.mount(host, "preview", { t: CUSTOM, onUse: () => {} });
await wait(0);
check("with its own words: exactly those lines, nothing automatic",
      JSON.stringify(items(host)) === JSON.stringify(CUSTOM.features), items(host).join(" | "));
check("...the sentence under the title replaces the template's line",
      host.querySelector("[data-template-blurb]")?.textContent === "Know how busy the shop is.");
check("...the heading is the developer's", host.querySelector("[data-template-heading]")?.textContent.trim()
      === "Inside this template");
check("...and HTML stays text", !host.querySelector("[data-what-you-get] b")
      && host.textContent.includes("<b>today</b>"));
ui.unmount();

// ---- 2. the editor opens on the automatic lines -----------------------------------
const LONG = { ...AUTO, key: "long",
               preview: { ...AUTO.preview, cards: ["People entering the loading bay this hour",
                                                    "People leaving the loading bay this hour",
                                                    "Average time spent inside the restricted area"] } };
globalThis.__tpl = LONG;
ui = m.mount(host, "editor", { template: { key: "long", label: "Long" }, onClose: () => {} });
await wait(0);
const ed = () => document.querySelector("[data-copy-editor]");
const pv = () => document.querySelector("[data-copy-preview]");
const field = (l) => ed().querySelector(`[aria-label="${l}"]`);
const lineCount = () => document.querySelector("[data-line-count]")?.textContent || "";
const problem = () => document.querySelector("[data-copy-problem]")?.textContent || "";
const state = () => document.querySelector("[data-copy-state]");
const fields = () => [...ed().querySelectorAll('[aria-label^="Line "]')].map((i) => i.value);
check("a template with no words of its own opens on the automatic lines, in the fields",
      fields().length === 4 && fields()[0] === "Looks for: people."
      && fields()[3] === "Add your camera, then change anything you like.", fields().join(" | "));
check("...said so in plain words", state()?.dataset.copyState === "automatic"
      && state().textContent.startsWith("Automatic text: these are the words customers see now"));
check("an automatic line over 140 is shortened and flagged, Save is not blocked",
      fields()[1].length <= 140 && fields()[1].endsWith("...")
      && ed().querySelectorAll("[data-line-cut]").length === 1 && !problem()
      && button("Save")?.disabled === false, fields()[1]);
ui.unmount();

globalThis.__tpl = AUTO;
ui = m.mount(host, "editor", { template: { key: "alpha", label: "Alpha" }, onClose: () => {} });
await wait(0);
check("the preview shows the heading, What you get by default",
      pv().querySelector("[data-template-heading]")?.textContent.trim() === "What you get");

// ---- 3. click to edit ------------------------------------------------------------------
await click(pv().querySelectorAll("[data-what-you-get] li")[2]);
check("a click on a line in the preview puts the cursor in its field",
      document.activeElement === field("Line 3"), document.activeElement?.outerHTML);
await click(pv().querySelector("[data-template-heading]"));
check("...the heading too", document.activeElement === field("Heading"));
await click(pv().querySelector("[data-template-blurb]"));
check("...and the sentence", document.activeElement === field("Additional description"));

// ---- 4. editing and limits -----------------------------------------------------------
await type(field("Line 1"), "Counts who walks in");
check("an edit says it is not saved yet", state().dataset.copyState === "unsaved"
      && state().textContent === "Your own text, not saved yet.");
check("...the preview follows", pv().querySelectorAll("[data-what-you-get] li")[0].textContent === "Counts who walks in");
await type(field("Line 2"), "Shows <b>today</b>");
check("HTML stays text in the preview", !pv().querySelector("[data-what-you-get] b")
      && pv().textContent.includes("<b>today</b>"));
check("...with a counter per line", ed().querySelector('[data-copy-line="0"]').textContent.includes("19/140"));
await click(ed().querySelectorAll('[aria-label="Move up"]')[1]);
check("Move up reorders", fields()[0] === "Shows <b>today</b>");
await type(field("Line 1"), "x".repeat(141));
check("141 characters is refused before saving",
      problem().includes("140 characters") && button("Save").disabled === true, problem());
await type(field("Line 1"), "Shows today");
await click(button("Add a line"));
check("an empty line is named and keeps Save off",
      problem().includes("A line is empty") && button("Save").disabled === true, problem());
await click(ed().querySelectorAll('[aria-label="Remove line"]')[4]);
await type(field("Additional description"), "z".repeat(201));
check("a 201-character sentence is refused", problem().includes("200 characters")
      && document.querySelector("[data-blurb-count]").textContent.trim() === "201 / 200", problem());
await type(field("Additional description"), "Know how busy the shop is.");
await type(field("Heading"), "h".repeat(41));
check("a 41-character heading is refused", problem().includes("heading can be at most 40")
      && document.querySelector("[data-heading-count]").textContent.trim() === "41 / 40", problem());
await type(field("Heading"), "What this gives you");
check("...the preview shows the heading and the sentence",
      pv().querySelector("[data-template-heading]").textContent.trim() === "What this gives you"
      && pv().querySelector("[data-template-blurb]").textContent === "Know how busy the shop is.");
for (let i = 0; i < 4; i += 1) {
  // eslint-disable-next-line no-await-in-loop
  await click(button("Add a line"));
  // eslint-disable-next-line no-await-in-loop
  await type(field(`Line ${i + 5}`), `Line ${i + 5}`);
}
check("at 8 lines, Add a line is off", lineCount().includes("8 of 8") && button("Add a line").disabled === true, lineCount());
await click(ed().querySelectorAll('[aria-label="Remove line"]')[7]);
check("Remove takes a line away", lineCount().includes("7 of 8"));

// ---- 5. save and reset ----------------------------------------------------------------
globalThis.__sent.length = 0;
await click(button("Save"));
const put = globalThis.__sent[0];
check("Save sends lines, sentence and heading",
      put?.[1] === "/api/hub/formats/alpha/copy" && put[2].features.length === 7
      && put[2].features[0] === "Shows today" && put[2].blurb === "Know how busy the shop is."
      && put[2].heading === "What this gives you", JSON.stringify(put));
const note = () => document.querySelector("[data-copy-note]")?.textContent || "";
check("...says boxes get it with Publish templates, and that it is your own text",
      note().includes("when you press Publish templates") && state().dataset.copyState === "custom", note());
await click(button("Use automatic text"));
const reset = globalThis.__sent.at(-1);
check("Use automatic text sends all empty",
      reset[2].features.length === 0 && reset[2].blurb === "" && reset[2].heading === ""
      && note().startsWith("Back to the automatic text"), JSON.stringify(reset));
check("...and the automatic lines are back in the fields, marked automatic",
      fields()[0] === "Looks for: people." && state().dataset.copyState === "automatic"
      && pv().querySelector("[data-template-heading]").textContent.trim() === "What you get");
check("no em dash", !document.body.textContent.includes("\u2014"));
ui.unmount();
host.remove();

console.log(bad ? `FAILED (${bad})` : "PASS");
process.exit(bad ? 1 : 0);
