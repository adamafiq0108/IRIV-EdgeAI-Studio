// Switching cameras on Camera Setup, many times, never leaks a stream.
//
// Tok Aba (2026-10-09, box .164, v1.0.34): Camera 1 -> 2 -> 1 -> 2 a few
// times left the feed black with only the loader, for ever, while both rows
// said "Live". Measured on the box: the browser held exactly six connections
// to the origin (its per-host cap), four of them old /api/detector/preview
// streams this page had switched AWAY from and never closed. A detached
// <img> keeps pulling until the browser collects it, and under a steady
// stream that is never. The pool filled with pictures nobody could see and
// the next preview was never even requested.
//
// Pinned here, with the real components (ZonesPanel, WebRTCVideo, StreamImg)
// in jsdom:
//   1. twelve switches, quick and slow: at most ONE preview <img> is in the
//      document at any time, and it asks for the camera that is selected
//   2. every <img> that left the document had its src cleared on the way out
//      (the abort), and no two were ever open together
//   3. a late `load` from an old img changes nothing; a `load` on the current
//      one takes the loader down at once and the pill says "Live"
//   4. the pill says "Connecting…" while waiting, never "Live"
//   5. a feed that never paints ends in "No video" with a Retry, the player
//      unmounted (no img, no loader); Retry mounts a fresh one
//   6. the host is told {live, failed} as the feed changes (onFeedState)
//   7. WebRTC: a camera switch never starts a handshake under the previous
//      camera's verdict, and every WHEP session is DELETEd exactly once
//
// Timers are scaled 1/50 so the page's retry ladder (3 s, 6 s, 9 s, 6 s)
// runs in under half a second.
//
//   node tests/check_feedswitch.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".feedswitch-test.cjs");

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
                 "matmedia", "matchMedia"]) {
  if (dom.window[k] !== undefined) global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

// Scaled timers: anything over 50 ms runs 50x faster.
const realSetTimeout = global.setTimeout;
const SCALE = 50;
global.setTimeout = (fn, ms, ...a) =>
  realSetTimeout(fn, ms > 50 ? Math.ceil(ms / SCALE) : ms, ...a);
const sleep = (ms) => new Promise((r) => realSetTimeout(r, ms));

// Every <img> the page ever creates, in order.
const imgs = [];
const realCreate = dom.window.document.createElement.bind(dom.window.document);
dom.window.document.createElement = (tag, ...rest) => {
  const el = realCreate(tag, ...rest);
  if (String(tag).toLowerCase() === "img") imgs.push(el);
  return el;
};

// The transport question, per camera: "mjpeg" | "webrtc" | "pending".
const answers = { cam1: "mjpeg", cam2: "mjpeg" };
const pendingResolvers = {};
const whepPosts = [];     // cameras a WHEP POST went out for
const deletes = [];       // session paths DELETEd
let sessionNo = 0;
global.fetch = (url, opts = {}) => {
  const u = String(url);
  const cam = (u.match(/camera=([^&]+)/) || [])[1] || "";
  if (u.includes("/api/detector/webrtc/session/")) {
    deletes.push(u.split("?")[0].split("/").pop());
    return Promise.resolve({ ok: true, status: 204 });
  }
  if (u.includes("/api/detector/webrtc/whep")) {
    whepPosts.push(cam);
    sessionNo += 1;
    const sid = `sess-${cam}-${sessionNo}`;
    return Promise.resolve({
      ok: true, status: 201,
      headers: { get: (h) => (h === "Location"
        ? `/api/detector/webrtc/session/${sid}?camera=${cam}` : null) },
      text: () => Promise.resolve("v=0\r\nanswer"),
    });
  }
  if (u.includes("/api/detector/webrtc")) {
    const a = answers[cam] ?? "mjpeg";
    if (a === "pending") {
      return new Promise((resolve) => {
        pendingResolvers[cam] = (mode) => resolve(
          { ok: true, json: () => Promise.resolve({ mode, ready: true }) });
      });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ mode: a, ready: true }) });
  }
  return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
};

// A peer connection that negotiates instantly and "connects".
class FakePC {
  constructor() { this.connectionState = "new"; this.iceGatheringState = "complete";
                  this.localDescription = null; FakePC.all.push(this); }
  addTransceiver() {}
  async createOffer() { return { type: "offer", sdp: "v=0\r\noffer" }; }
  async setLocalDescription(d) { this.localDescription = d; }
  async setRemoteDescription() {
    this.connectionState = "connected";
    if (this.onconnectionstatechange) this.onconnectionstatechange();
  }
  addEventListener() {} removeEventListener() {}
  close() { this.connectionState = "closed"; }
}
FakePC.all = [];

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
      import WebRTCVideo from "${p("src/components/WebRTCVideo.jsx")}";
      import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
      export { act };
      export function mount(el, which, props = {}) {
        const root = createRoot(el);
        const make = (pr) => (
          which === "zones" ? h(ToastProvider, null, h(ZonesPanel, pr))
          : h(WebRTCVideo, pr));
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

const flush = async (n = 6) => {
  for (let i = 0; i < n; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => realSetTimeout(r, 0)); });
  }
};
const loaders = (el) => [...el.querySelectorAll(".cam-loader")];
const previewImgs = () => imgs.filter((i) => i.isConnected
                                       && (i.getAttribute("src") || "").includes("/api/detector/preview"));
const pill = (el) => {
  const lab = [...el.querySelectorAll("span")].find((s) => s.textContent === "Live View");
  return lab && lab.nextElementSibling ? lab.nextElementSibling.textContent : "";
};

const status = () => ({
  ok: true, running: true, editable: true, caps: ["zones.edit", "zones.add"],
  cameras: [{ id: "cam1", name: "Camera 1", enabled: true },
            { id: "cam2", name: "Camera 2", enabled: true }],
  camera: "cam1", zones: [],
});
globalThis.__status = status;

const reports = [];
const onFeedState = (s) => reports.push(s);

// 1-6. Camera Setup on the MJPEG path (what every box ran until today).
{
  const el = document.createElement("div");
  document.body.appendChild(el);
  const props = (cam) => ({ camera: cam, hostStatus: status(), hostRunning: true, onFeedState });
  const v = mod.mount(el, "zones", props("cam1"));
  await flush();
  check("1. first camera: one preview img, for cam1",
        previewImgs().length === 1 && previewImgs()[0].src.includes("camera=cam1"),
        previewImgs().map((i) => i.src).join(" | "));
  check("4. pill says Connecting… before a frame", pill(el) === "Connecting…", pill(el));

  // A re-render of the same player (a caller's state flipping, a poll landing)
  // must leave the open stream alone: React 19 runs a ref's cleanup whenever
  // its identity changes, and an inline ref once aborted the img on every
  // render, leaving a src-less img drawn as a broken image (the dashboard and
  // a video file on Camera Setup, 2026-10-09). The dashboard's LiveFeed passes
  // fresh onLoad/onError closures each render, so this does the same.
  {
    const box = document.createElement("div");
    document.body.appendChild(box);
    const mk = () => ({ camera: "camQ", mjpegOnly: true,
                        mjpegSrc: "/api/detector/preview?camera=camQ",
                        onLoad: () => {}, onError: () => {} });
    const q = mod.mount(box, "webrtc", mk());
    await flush();
    // The img gets its src a few timer ticks after mount; under a loaded
    // machine six zero-ms flushes can finish first. Wait on the condition
    // (generous deadline), not on how many ticks happened to run.
    for (let t0 = Date.now(); Date.now() - t0 < 5000;) {
      const im = previewImgs().filter((i) => box.contains(i))[0];
      if (im && (im.getAttribute("src") || "").includes("camQ")) break;
      // eslint-disable-next-line no-await-in-loop
      await act(async () => { await sleep(20); });
    }
    const first = previewImgs().filter((i) => box.contains(i))[0];
    // Paint it. An img that never loads is, by design, retried by the page's
    // stall ladder (3 s, scaled here to 60 ms); on a loaded machine five
    // re-renders can take longer than that, and the retry's remount looks like
    // the very bug this pins. A feed that has painted has no stall timer.
    if (first) await act(async () => { first.dispatchEvent(new dom.window.Event("load")); });
    for (let i = 0; i < 5; i += 1) {
      q.rerender(mk());
      // eslint-disable-next-line no-await-in-loop
      await flush(1);
    }
    const now = previewImgs().filter((i) => box.contains(i));
    check("1b. five re-renders of the SAME player: the same img, still holding its src",
          now.length === 1 && now[0] === first && !!first
          && !!first.getAttribute("src") && first.getAttribute("src").includes("camQ"),
          `${now.length} imgs, same=${now[0] === first}, src=${String(first && first.getAttribute("src"))}`);
    q.unmount(); box.remove();
    await flush(1);
  }

  let worst = 0;
  let wrongCam = 0;
  let cam = "cam1";
  // Six slow switches (the answer arrives between them) and six fast ones
  // (three re-renders before anything can answer).
  for (let i = 0; i < 12; i += 1) {
    cam = cam === "cam1" ? "cam2" : "cam1";
    v.rerender(props(cam));
    // eslint-disable-next-line no-await-in-loop
    if (i < 6) await flush(); else await flush(1);
    const open = previewImgs();
    worst = Math.max(worst, open.length);
    if (open.some((im) => !im.src.includes(`camera=${cam}`))) wrongCam += 1;
  }
  await flush();
  check("1. twelve switches: never more than one preview img open", worst <= 1,
        `worst ${worst}`);
  check("1. ...and the open one always asks for the selected camera", wrongCam === 0,
        `${wrongCam} wrong`);
  const gone = imgs.filter((i) => !i.isConnected);
  check("2. a dozen imgs have come and gone", gone.length >= 10, `${gone.length}`);
  check("2. every img that left had its src cleared (the abort)",
        gone.every((i) => !i.hasAttribute("src")),
        gone.filter((i) => i.hasAttribute("src")).map((i) => i.src).join(" | "));

  // 3. stale vs current load.
  const stale = gone[gone.length - 1];
  await act(async () => { stale.dispatchEvent(new dom.window.Event("load")); });
  await flush();
  check("3. a late load from an old img changes nothing",
        loaders(el).length === 1 && pill(el) === "Connecting…", `${loaders(el).length} / ${pill(el)}`);
  const cur = previewImgs()[0];
  await act(async () => { cur.dispatchEvent(new dom.window.Event("load")); });
  await flush();
  check("3. a load on the current img: loader gone, pill Live",
        loaders(el).length === 0 && pill(el) === "Live", `${loaders(el).length} / ${pill(el)}`);
  check("6. the host was told live", reports.length && reports[reports.length - 1].live === true,
        JSON.stringify(reports.slice(-2)));

  // 5. a feed that never paints.
  v.rerender(props(cam === "cam1" ? "cam2" : "cam1"));
  await flush();
  check("5. after a switch the pill is Connecting… again", pill(el) === "Connecting…", pill(el));
  const t0 = Date.now();
  let failed = null;
  while (Date.now() - t0 < 4000) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await sleep(40); });
    await flush(1);                                  // eslint-disable-line no-await-in-loop
    failed = el.querySelector("[data-feed-failed]");
    if (failed) break;
  }
  check("5. the ladder ends in a No video card, not a loader for ever", !!failed,
        el.innerHTML.slice(0, 400));
  check("5. ...that names the failure", !!failed && /No video/.test(failed.textContent));
  check("5. ...with the player unmounted: no img, no loader",
        previewImgs().length === 0 && loaders(el).length === 0,
        `${previewImgs().length} imgs, ${loaders(el).length} loaders`);
  check("5. pill says No video", pill(el) === "No video", pill(el));
  check("6. the host was told failed", reports[reports.length - 1]?.failed === true,
        JSON.stringify(reports.slice(-2)));
  const retry = failed && [...failed.querySelectorAll("button")].find((b) => /Retry/.test(b.textContent));
  check("5. there is a Retry button", !!retry);
  if (retry) {
    await act(async () => { retry.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
    await flush();
    check("5. Retry mounts a fresh player: one img, one loader, Connecting…",
          previewImgs().length === 1 && loaders(el).length === 1 && pill(el) === "Connecting…",
          `${previewImgs().length} imgs, ${loaders(el).length} loaders, ${pill(el)}`);
  }
  v.unmount(); el.remove();
  await flush();
  check("unmount closes the last preview", previewImgs().length === 0
        && imgs.every((i) => i.isConnected || !i.hasAttribute("src")));
}

// 7. WebRTC: the mode/camera race and the WHEP DELETE.
{
  global.RTCPeerConnection = FakePC;
  answers.cam1 = "webrtc"; answers.cam2 = "pending";
  const el = document.createElement("div");
  document.body.appendChild(el);
  const v = mod.mount(el, "rtc", { camera: "cam1", mjpegSrc: "/api/detector/preview?camera=cam1" });
  await flush(10);
  check("7. cam1 on webrtc: one WHEP POST for cam1", whepPosts.join(",") === "cam1", whepPosts.join(","));
  v.rerender({ camera: "cam2", mjpegSrc: "/api/detector/preview?camera=cam2" });
  await flush(10);
  check("7. switch to cam2 while its verdict is pending: NO handshake for cam2 yet",
        whepPosts.join(",") === "cam1", whepPosts.join(","));
  check("7. cam1's session was DELETEd on the switch", deletes.filter((d) => d.startsWith("sess-cam1")).length === 1,
        deletes.join(","));
  check("7. the player shows the placeholder, not a stale img",
        previewImgs().length === 0, `${previewImgs().length}`);
  pendingResolvers.cam2 && pendingResolvers.cam2("webrtc");
  await flush(10);
  check("7. the answer arrives: a handshake for cam2", whepPosts.join(",") === "cam1,cam2", whepPosts.join(","));
  v.unmount(); el.remove();
  await flush(4);
  const counts = {};
  for (const d of deletes) counts[d] = (counts[d] || 0) + 1;
  check("7. every session DELETEd exactly once",
        Object.keys(counts).length === 2 && Object.values(counts).every((n) => n === 1),
        JSON.stringify(counts));
}

console.log();
if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log("ALL OK");
dom.window.close();
process.exit(0);
