// The Dataset page's camera choice (owner request, 2026-10-13).
//
// One camera: no picker, and the capture requests carry no camera -- byte for
// byte what they sent before, so the server keeps serving the first camera.
// Two or more: a labelled dropdown of the cameras BY NAME, starting at the one
// the system last captured from, and the chosen id goes with every photo and
// clip request. The picker is mounted for real (JSDOM) and changed the way a
// person changes it; the request bodies come from the same helper the page
// calls. Server side: tests/smoke_dataset_camera.py.
//
//   node tests/check_datasetcamera.mjs
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { readText } from "./_text.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".datasetcamera-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "Event",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle"]) {
  global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (label, ok, detail = "") => {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok || !detail ? "" : `\n      ${detail}`));
  if (!ok) bad += 1;
};

const src = (p) => join(FRONT, "src", p).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import Picker from "${src("components/DatasetCameraPicker.jsx")}";
export * from "${src("lib/datasetCamera.js")}";
export function mount(el, props) {
  const root = createRoot(el);
  act(() => { root.render(h(Picker, props)); });
  return { root, act };
}
`;
await build({
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const lib = req(OUT);
rmSync(OUT, { force: true });

const ONE = { cameras: [{ id: "cam1", name: "Front gate", enabled: true }],
              camera: "cam1", default_camera: "cam1" };
const TWO = { cameras: [{ id: "cam1", name: "Front gate", enabled: true },
                        { id: "cam2", name: "Loading bay", enabled: true },
                        { id: "cam3", name: "Spare", enabled: false }],
              camera: "cam2", default_camera: "cam1" };

function render(props) {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const { root, act } = lib.mount(el, props);
  return { el, done: () => { act(() => root.unmount()); el.remove(); } };
}

// -- one camera ---------------------------------------------------------------
{
  const cams = lib.cameraChoices(ONE);
  const r = render({ cameras: cams, value: "cam1", onChange: () => {} });
  check("one camera: no picker at all", r.el.innerHTML === "", r.el.innerHTML);
  r.done();
  check("one camera: a photo request carries no camera (the old body)",
        JSON.stringify(lib.captureBody({ name: "a" }, cams, "cam1")) === '{"name":"a"}');
  check("one camera: a clip request too",
        JSON.stringify(lib.captureBody({ name: "a", duration_s: 0 }, cams, "cam1"))
        === '{"name":"a","duration_s":0}');
  check("no camera list at all (an older server): no picker, nothing sent",
        !lib.showPicker(lib.cameraChoices({}))
        && !("camera" in lib.captureBody({ name: "a" }, lib.cameraChoices({}), "")));
}

// -- two cameras --------------------------------------------------------------
{
  const cams = lib.cameraChoices(TWO);
  let picked = null;
  const r = render({ cameras: cams, value: lib.initialCamera(TWO),
                     onChange: (v) => { picked = v; } });
  const sel = r.el.querySelector("select");
  check("two cameras: a picker is shown", !!sel);
  check("...labelled, inside its <label>",
        !!sel && sel.closest("label")?.textContent.includes("Camera"));
  const opts = sel ? [...sel.options].map((o) => [o.value, o.textContent]) : [];
  check("...listing the cameras by name, ids as values",
        JSON.stringify(opts) === JSON.stringify([["cam1", "Front gate"], ["cam2", "Loading bay"],
                                                 ["cam3", "Spare (off)"]]),
        JSON.stringify(opts));
  check("...starting at the camera this system last captured from",
        sel?.value === "cam2", sel?.value);
  if (sel) {
    sel.value = "cam1";
    sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  }
  check("choosing a camera reports its id", picked === "cam1", String(picked));
  r.done();
  check("a photo request carries the chosen id",
        JSON.stringify(lib.captureBody({ name: "a" }, cams, "cam2"))
        === '{"name":"a","camera":"cam2"}');
  check("a clip request carries it too",
        lib.captureBody({ name: "a", duration_s: 5 }, cams, "cam2").camera === "cam2");
  check("the base body is never changed in place",
        (() => { const b = { name: "a" }; lib.captureBody(b, cams, "cam2"); return !("camera" in b); })());
  check("a remembered camera that is gone starts at the default",
        lib.initialCamera({ ...TWO, camera: "gone" }) === "cam1");
  check("nothing remembered starts at the default",
        lib.initialCamera({ ...TWO, camera: "" }) === "cam1");
}

// -- the page uses them ---------------------------------------------------------
const page = readText(join(FRONT, "src", "pages", "Dataset.jsx"));
check("the page mounts the picker",
      page.includes("<DatasetCameraPicker cameras={cams} value={cam}"));
check("the photo request is built by captureBody",
      /api\.post\("\/api\/detector\/dataset\/snap",\s*captureBody\(\{ name \}, cams, cam\)\)/.test(page));
check("the clip request is built by captureBody",
      /api\.post\("\/api\/detector\/dataset\/record",\s*captureBody\(/.test(page));
check("the preview follows the chosen camera when there is a choice",
      page.includes("camera={multi ? cam || undefined : undefined}"));
check("a switched-off choice holds both capture buttons",
      (page.match(/\|\| camOff/g) || []).length >= 2);
check("the page names the camera on captured rows",
      page.includes("r.camera_name &&"));
const lib2 = readText(join(FRONT, "src", "pages", "DatasetLibrary.jsx"));
check("the library names the camera too", (lib2.match(/r\.camera_name/g) || []).length >= 2);

console.log(bad ? `\n${bad} FAILED` : "\nALL OK");
process.exit(bad ? 1 : 0);
