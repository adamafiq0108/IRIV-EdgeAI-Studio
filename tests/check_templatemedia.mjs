// Template preview media, the browser side (Tok Aba, 2026-10-22, phase 2):
// the developer's "Preview media" editor and the Hub's preview slider.
//
//   1. the editor counts "N of 5 slides, video used / no video yet"; Add video
//      is off once a video is in, both Adds are off at 5 slides
//   2. a wrong type or a file over 5 MB is named at once and never sent; a
//      second video and a sixth slide are refused before sending too
//   3. a good picture uploads (multipart to /api/hub/formats/<key>/media);
//      Move, a caption and Delete send the new order / delete
//   4. the preview slider shows a picture, then the video inline, muted,
//      with controls, and the slide's caption; a card never plays a video
//   5. no em dash
//
//   node tests/check_templatemedia.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".templatemedia-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "FocusEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
                 "localStorage", "DocumentFragment", "File", "FormData", "Blob"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.sessionStorage = dom.window.sessionStorage;
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
      get(url) { return Promise.resolve({ ok: true, items: globalThis.__items }); },
      put(url, body) { globalThis.__sent.push(["PUT", url, body]);
        const by = Object.fromEntries(globalThis.__items.map((i) => [i.file, i]));
        globalThis.__items = body.order.map((f) => ({ ...by[f], caption: body.captions[f] }));
        return Promise.resolve({ ok: true, items: globalThis.__items }); },
      del(url) { globalThis.__sent.push(["DELETE", url]);
        const f = url.split("/").pop();
        globalThis.__items = globalThis.__items.filter((i) => i.file !== f);
        return Promise.resolve({ ok: true, items: globalThis.__items }); },
      post() { return Promise.resolve({}); },
    };
    export function friendlyError(e) { return String(e && e.message || e); }`),
  shim("app-shim", /state\/app\.jsx$/, `
    export function useApp() { return { user: { is_admin: true } }; }`),
];

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: shims,
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import TemplateMediaEditor, { checkFile, mediaCount } from "${p("src/components/TemplateMediaEditor.jsx")}";
      import { TemplatePreview, TemplateArt } from "${p("src/components/TemplateGallery.jsx")}";
      export { act, checkFile, mediaCount };
      export function mount(el, which, props) {
        const root = createRoot(el);
        const C = which === "editor" ? TemplateMediaEditor : which === "art" ? TemplateArt : TemplatePreview;
        act(() => { root.render(h(C, props)); });
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
const button = (text) => [...document.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);
const byLabel = (l) => document.querySelector(`[aria-label="${l}"]`);
const pick = async (input, file) => {
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => { input.dispatchEvent(new window.Event("change", { bubbles: true })); });
  await wait(0);
};
const errorText = () => document.querySelector("[data-media-error]")?.textContent || "";
const count = () => document.querySelector("[data-media-count]")?.textContent || "";

const img = (n) => ({ file: `${String(n).padStart(16, "0")}.png`, kind: "image", type: "image/png", caption: "" });
const vid = { file: "aaaaaaaaaaaaaaaa.mp4", kind: "video", type: "video/mp4", caption: "Clip" };
const uploads = [];
global.fetch = window.fetch = async (url, opts) => {
  uploads.push([url, opts]);
  const f = opts.body.get("file");
  globalThis.__items = [...globalThis.__items, { file: "bbbbbbbbbbbbbbbb.png", kind: "image", type: "image/png", caption: "", name: f.name }];
  return { ok: true, status: 200, json: async () => ({ ok: true, items: globalThis.__items }) };
};

// ---- 1. counts and the rule's buttons -------------------------------------------
globalThis.__items = [img(1), vid, img(2)];
const host = document.createElement("div");
document.body.appendChild(host);
let ui = m.mount(host, "editor", { template: { key: "alpha", label: "Alpha" }, onClose: () => {} });
await wait(0);
check("the editor counts slides and the video", count() === "3 of 5 slides, video used", count());
check("...three thumbnails, the video as a video",
      document.querySelectorAll("[data-media-item]").length === 3
      && !!document.querySelector('[data-media-item="aaaaaaaaaaaaaaaa.mp4"] video'));
check("...thumbnails come from the read route",
      document.querySelector("[data-media-item] img")?.getAttribute("src")
        === "/api/hub/template-media/alpha/0000000000000001.png");
check("Add video is off once a video is in, Add picture is on",
      button("Add video")?.disabled === true && button("Add picture")?.disabled === false);

// ---- 2. refused before sending -------------------------------------------------------
await pick(byLabel("Picture file"), new window.File(["<svg/>"], "x.svg", { type: "image/svg+xml" }));
check("an SVG is named at once and not sent", errorText().includes("Only JPEG, PNG") && !uploads.length, errorText());
await pick(byLabel("Picture file"), new window.File([new Uint8Array(5 * 1024 * 1024 + 1)], "big.png", { type: "image/png" }));
check("over 5 MB is named at once and not sent", errorText().includes("larger than 5 MB") && !uploads.length, errorText());
await pick(byLabel("Video file"), new window.File(["x"], "two.mp4", { type: "video/mp4" }));
check("a second video is refused before sending", errorText().includes("already has a video") && !uploads.length, errorText());
check("checkFile: a renamed type does not pass",
      !!m.checkFile(new window.File(["x"], "x.png", { type: "video/mp4" }), []).error);
check("checkFile: a sixth slide is refused",
      m.checkFile(new window.File(["x"], "x.png", { type: "image/png" }), [img(1), img(2), img(3), img(4), img(5)])
        .error.includes("5 slides"));

// ---- 3. upload, move, caption, delete ------------------------------------------------
await pick(byLabel("Picture file"), new window.File(["\x89PNG"], "front.png", { type: "image/png" }));
check("a good picture uploads, multipart, to the template's media",
      uploads.length === 1 && uploads[0][0] === "/api/hub/formats/alpha/media"
      && uploads[0][1].method === "POST" && uploads[0][1].body.get("file")?.name === "front.png");
check("...the list redraws and the error clears",
      count() === "4 of 5 slides, video used" && !errorText(), count());
await click(document.querySelectorAll('[aria-label="Move down"]')[0]);
const put = globalThis.__sent.at(-1);
check("Move down sends the new order",
      put?.[0] === "PUT" && put[2].order[0] === "aaaaaaaaaaaaaaaa.mp4"
      && put[2].order[1] === "0000000000000001.png", JSON.stringify(put));
const cap = byLabel("Caption for slide 2");
const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
await act(async () => {
  setter.call(cap, "The front door");
  cap.dispatchEvent(new window.Event("input", { bubbles: true }));
  cap.dispatchEvent(new window.FocusEvent("focusout", { bubbles: true }));
});
await wait(0);
const put2 = globalThis.__sent.at(-1);
check("a caption is sent with the order",
      put2?.[0] === "PUT" && put2[2].captions["0000000000000001.png"] === "The front door", JSON.stringify(put2));
await click(document.querySelectorAll('[aria-label="Delete slide"]')[0]);
check("Delete deletes that slide",
      globalThis.__sent.at(-1)?.[0] === "DELETE"
      && globalThis.__sent.at(-1)[1] === "/api/hub/formats/alpha/media/aaaaaaaaaaaaaaaa.mp4"
      && count() === "3 of 5 slides, no video yet" && button("Add video")?.disabled === false, count());
ui.unmount();

globalThis.__items = [img(1), img(2), img(3), img(4), vid];
ui = m.mount(host, "editor", { template: { key: "alpha", label: "Alpha" }, onClose: () => {} });
await wait(0);
check("at 5 slides both Adds are off",
      count() === "5 of 5 slides, video used"
      && button("Add picture")?.disabled === true && button("Add video")?.disabled === true);
check("no em dash in the editor", !document.body.textContent.includes("\u2014"));
ui.unmount();

// ---- 4. the slider -------------------------------------------------------------------
const t = { key: "alpha", label: "Alpha", hint: "", classes: [], preview: {},
            media: [{ url: "/api/hub/template-media/alpha/0000000000000001.png", kind: "image", caption: "The door" },
                    { url: "/api/hub/template-media/alpha/aaaaaaaaaaaaaaaa.mp4", kind: "video", caption: "In action" }],
            images: ["/static/img/templates/alpha.png"] };
ui = m.mount(host, "preview", { t, onUse: () => {} });
await wait(0);
const art = () => host.querySelector("[data-template-art]");
check("the slider starts on the picture, with its caption",
      art()?.dataset.templateArt === "image"
      && art().querySelector("img")?.getAttribute("src") === "/api/hub/template-media/alpha/0000000000000001.png"
      && host.querySelector("[data-slide-caption]")?.textContent === "The door");
await click(byLabel("Next picture"));
const v = art()?.querySelector("video");
check("...then the video, inline, muted, with controls",
      art()?.dataset.templateArt === "video" && v?.getAttribute("src") === "/api/hub/template-media/alpha/aaaaaaaaaaaaaaaa.mp4"
      && v.muted === true && v.hasAttribute("controls") && v.hasAttribute("playsinline")
      && host.querySelector("[data-slide-caption]")?.textContent === "In action", art()?.outerHTML);
check("uploaded media wins over the release's own pictures",
      !host.innerHTML.includes("/static/img/templates/alpha.png"));
ui.unmount();

ui = m.mount(host, "art", { t: { ...t, media: [t.media[1], t.media[0]] }, card: true });
await wait(0);
check("a card shows the first picture, never plays the video",
      art()?.dataset.templateArt === "image" && !host.querySelector("video"));
ui.unmount();
ui = m.mount(host, "art", { t: { ...t, media: [t.media[1]] }, card: true });
await wait(0);
check("...a video-only template shows its first frame, no controls, not playing",
      !!host.querySelector("video") && !host.querySelector("video").hasAttribute("controls")
      && !host.querySelector("video").hasAttribute("autoplay"));
ui.unmount();
host.remove();

console.log(bad ? `FAILED (${bad})` : "PASS");
process.exit(bad ? 1 : 0);
