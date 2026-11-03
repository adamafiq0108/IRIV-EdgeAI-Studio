// ONE camera loader per feed, centred in the feed box.
//
// Tok Aba (2026-10-08, Camera Setup screenshot): the camera-in-a-ring loader
// sometimes showed TWICE -- one half cut off at the top edge of the picture,
// one lower down in the middle. Two owners were drawing it at once:
//   - the page's own loader over the whole feed box (Zones.jsx "Camera
//     connecting" sheet, LiveFeed's Overlay), and
//   - the player's DEFAULT placeholder (WebRTCVideo while it asks which
//     transport, StreamImg while its <img> is torn down), drawn inside a
//     wrapper that has no height yet -- hence centred on the top edge.
// Passing placeholder={null} did not help: WebRTCVideo read it with `??`.
//
// Pinned here, by rendering the real components (no loader shimmed):
//   1. Camera Setup (ZonesPanel), running, transport question still in
//      flight: exactly one .cam-loader, and it is the page's, a direct child
//      of the feed box
//   2. ...after the answer (MJPEG, img mounted, no frame yet): still one
//   3. ...img torn down (tab hidden): still one
//   4. ...switching cameras (controlled camera change): still one
//   5. LiveFeed (Dataset page), the same three states: one each
//   6. LiveFeed starting detection (transition "start"): one
//   7. WebRTCVideo with no placeholder prop keeps its default ring (the
//      dashboard tile and the live wall still get one), and a bare StreamImg
//      torn down shows exactly one
//   8. while there is no frame the Camera Setup box keeps an aspect ratio,
//      so the loader is centred in a real box, not a 0px sliver
//
//   node tests/check_oneloader.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".oneloader-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent", "FocusEvent",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage", "sessionStorage",
                 "matchMedia"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
// No IntersectionObserver: StreamImg then treats itself as on screen, and
// the tab's visibility is the switch this test uses to tear the img down.
global.IS_REACT_ACT_ENVIRONMENT = true;

let hidden = false;
Object.defineProperty(dom.window.document, "visibilityState",
                      { configurable: true, get: () => (hidden ? "hidden" : "visible") });

// The transport question (/api/detector/webrtc). "pending" holds it open,
// which is the state the double loader showed up in most.
let webrtcAnswer = "pending";
global.fetch = (url) => {
  if (String(url).includes("/api/detector/webrtc")) {
    if (webrtcAnswer === "pending") return new Promise(() => {});
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ mode: webrtcAnswer }) });
  }
  return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
};

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const shim = {
  name: "shim",
  setup(b) {
    const stub = (name, filter, contents) => {
      b.onResolve({ filter }, () => ({ path: name, namespace: "shim" }));
      b.onLoad({ filter: new RegExp(`^${name}$`), namespace: "shim" },
               () => ({ contents, resolveDir: FRONT, loader: "jsx" }));
    };
    stub("api-shim", /api\/client\.js$/, `
      export const api = {
        get() { return Promise.resolve(globalThis.__status()); },
        post() { return Promise.resolve({ ok: true }); },
      };
      export function friendlyError(e) { return String(e && e.message || e); }
      export function saveBlob() {}
      export function streamUrl() { return ""; }
    `);
    // Not what this file is about; each would drag in more of the page.
    stub("overlay-shim", /OverlayPanel\.jsx$/, `export default () => null;`);
    stub("canvas-shim", /ZoneCanvas\.jsx$/, `
      export default () => null;
      export const ZONE_PALETTE = ["#e11d48", "#0ea5e9"];
      export const zoneColor = (z) => (z && z.color) || "#e11d48";
      export const buildPoints = () => [];
    `);
    stub("router-shim", /^react-router-dom$/, `
      import { createElement as h } from "react";
      export const Link = ({ children }) => h("a", null, children);
      export const useNavigate = () => () => {};
      export const useLocation = () => ({ pathname: "/zones" });
    `);
    stub("hooks-shim", /api\/hooks\.js$/, `
      export function useVisibleInterval() { return { stop() {}, start() {} }; }
      export function usePoll() {}
    `);
    stub("ask-shim", /ui\/ask\.jsx$/, `export const useConfirm = () => () => Promise.resolve(true);
      export default useConfirm;`);
    stub("guard-shim", /use-unsaved-guard\.jsx$/, `export const useUnsavedGuard = () => null;
      export default useUnsavedGuard;`);
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shim],
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import ZonesPanel from "${p("src/pages/Zones.jsx")}";
      import LiveFeed from "${p("src/components/LiveFeed.jsx")}";
      import WebRTCVideo from "${p("src/components/WebRTCVideo.jsx")}";
      import StreamImg from "${p("src/components/StreamImg.jsx")}";
      import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
      export { act };
      export function mount(el, which, props = {}) {
        const root = createRoot(el);
        const make = (pr) => (
          which === "zones" ? h(ToastProvider, null, h(ZonesPanel, pr))
          : which === "live" ? h("div", { className: "feedbox", style: { position: "relative" } },
                                 h(LiveFeed, pr))
          : which === "rtc" ? h(WebRTCVideo, pr)
          : h(StreamImg, pr));
        act(() => { root.render(make(props)); });
        return { root, rerender: (pr) => act(() => { root.render(make(pr)); }),
                 unmount: () => act(() => root.unmount()) };
      }
    `,
    resolveDir: FRONT, loader: "jsx",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent", loader: { ".css": "empty" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const mod = req(OUT);
const { act } = mod;

const flush = async () => {
  for (let i = 0; i < 6; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const loaders = (el) => [...el.querySelectorAll(".cam-loader")];
const html = (el) => el.innerHTML.slice(0, 600);
const setHidden = async (v) => {
  hidden = v;
  await act(async () => { document.dispatchEvent(new dom.window.Event("visibilitychange")); });
  await flush();
};

globalThis.__status = () => ({
  ok: true, running: true, editable: true, caps: ["zones.edit", "zones.add"],
  cameras: [{ id: "cam1", name: "Camera 1", enabled: true },
            { id: "cam2", name: "Camera 2", enabled: true }],
  camera: "cam1", zones: [],
});

// The feed box is the positioned black box the player sits in.
const feedBox = (el) => [...el.querySelectorAll("div")]
  .find((d) => d.style.background === "rgb(0, 0, 0)" && d.style.position === "relative");

// 1-4. Camera Setup.
{
  webrtcAnswer = "pending";
  const el = document.createElement("div");
  document.body.appendChild(el);
  const v = mod.mount(el, "zones", { camera: "cam1", hostStatus: globalThis.__status(), hostRunning: true });
  await flush();
  const box = feedBox(el);
  check("Camera Setup renders its feed box", !!box, html(el));
  let ls = loaders(el);
  check("1. transport question in flight: exactly one loader", ls.length === 1,
        `${ls.length} loaders`);
  check("1. ...and it is the page's, a direct child of the feed box",
        ls[0] && box && ls[0].parentElement === box);
  check("8. no frame yet: the feed box holds an aspect ratio",
        !!box && /\d/.test(box.style.aspectRatio || ""), box && box.getAttribute("style"));

  webrtcAnswer = "mjpeg";
  v.rerender({ camera: "cam2", hostStatus: globalThis.__status(), hostRunning: true });
  await flush();
  ls = loaders(el);
  check("4. switching cameras: exactly one loader", ls.length === 1, `${ls.length} loaders`);
  check("2. MJPEG chosen, img mounted, no frame yet: exactly one loader",
        ls.length === 1 && !!el.querySelector("img"), `${ls.length} loaders, img ${!!el.querySelector("img")}`);

  await setHidden(true);
  ls = loaders(el);
  check("3. img torn down (tab hidden): exactly one loader",
        ls.length === 1 && !el.querySelector("img"), `${ls.length} loaders`);
  await setHidden(false);
  v.unmount(); el.remove();
}

// 5-6. LiveFeed.
{
  webrtcAnswer = "pending";
  const el = document.createElement("div");
  document.body.appendChild(el);
  const v = mod.mount(el, "live", { running: false, transition: "start" });
  await flush();
  check("6. starting detection: exactly one loader", loaders(el).length === 1,
        `${loaders(el).length} loaders`);
  v.rerender({ running: true, transition: null });
  await flush();
  let ls = loaders(el);
  check("5. LiveFeed, transport question in flight: exactly one loader", ls.length === 1,
        `${ls.length} loaders`);
  check("5. ...over the whole feed box",
        ls[0] && ls[0].parentElement === el.querySelector(".feedbox"));
  webrtcAnswer = "mjpeg";
  v.rerender({ running: true, transition: null, camera: "cam2" });
  await flush();
  check("5. LiveFeed, MJPEG with no frame yet: exactly one loader",
        loaders(el).length === 1, `${loaders(el).length} loaders`);
  await setHidden(true);
  check("5. LiveFeed, img torn down: exactly one loader",
        loaders(el).length === 1, `${loaders(el).length} loaders`);
  await setHidden(false);
  v.unmount(); el.remove();
}

// 7. The defaults the dashboard tile and the live wall rely on.
{
  webrtcAnswer = "pending";
  let el = document.createElement("div");
  document.body.appendChild(el);
  let v = mod.mount(el, "rtc", { mjpegSrc: "/api/detector/preview" });
  await flush();
  check("7. WebRTCVideo with no placeholder prop keeps its one default ring",
        loaders(el).length === 1, `${loaders(el).length} loaders`);
  v.unmount(); el.remove();

  el = document.createElement("div");
  document.body.appendChild(el);
  await setHidden(true);
  v = mod.mount(el, "img", { src: "/api/detector/preview" });
  await flush();
  check("7. a bare StreamImg torn down (dashboard tile) shows exactly one",
        loaders(el).length === 1, `${loaders(el).length} loaders`);
  await setHidden(false);
  v.unmount(); el.remove();
}

console.log();
if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log("ALL OK");
dom.window.close();
process.exit(0);
