// A Detection Stream in Snapshot mode takes a SNAP input, and nothing else does.
//
// The canvas side of the feature (the compile and the app are
// tests/smoke_snapshot.py and tests/smoke_snapdetect.py):
//   * a Detection Stream draws NO input port while it is a Video stream -- the
//     default, and every stream saved before this existed, so no old canvas
//     gains a dot it never had
//   * set to Snapshot it draws exactly one, whose handle id is "snap"
//   * the editor's edge rule answers the same as the server's for the wires
//     that matter (an Inject, a Trigger, a sensor in; a filter, any other port
//     refused) -- both ends must agree or a wire draws and is refused on save
//   * the Frames field is offered only while an Inject or Trigger is wired into
//     a Snapshot stream: nowhere else does either node show anything extra
//
//   node tests/check_snapnode.mjs
import { readFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".snapnode-test.cjs");

// The real server registry, for the same reason check_node_render.mjs dumps
// it: a fixture copy would still pass the day the probe is renamed or
// re-deprecated, which is exactly when this test should fail.
const DUMP = "import json,sys\n"
  + "from core.services.workflow import REGISTRY\n"
  + "sys.stdout.write(json.dumps(REGISTRY))";
let registry = null;
for (const py of ["py", "python", "python3"]) {
  const r = spawnSync(py, ["-c", DUMP], { cwd: ROOT, encoding: "utf8" });
  if (r.status === 0 && r.stdout) {
    try { registry = JSON.parse(r.stdout); } catch { /* partial output */ }
    if (registry) break;
  }
}
if (!registry || !Object.keys(registry).length) {
  console.log("skip: no python interpreter to dump the workflow REGISTRY");
  process.exit(77);
}

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle"]) {
  global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const fail = (msg) => { console.error(`FAIL ${msg}`); bad += 1; };
const ok = (msg) => { console.log(`ok   ${msg}`); };

// Same shim and the same reason as check_node_render.mjs: React Flow's Handle
// only mounts inside <ReactFlow>, and the code under test is everything our
// component computes before it.
const shimXyflow = {
  name: "shim-xyflow",
  setup(b) {
    b.onResolve({ filter: /^@xyflow\/react$/ }, (args) => (
      args.importer.replace(/\\/g, "/").endsWith("workflow/nodes.jsx")
        ? { path: "xyflow-shim", namespace: "shim" } : null));
    b.onLoad({ filter: /^xyflow-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement } from "react";
        export const Position = { Left: "left", Right: "right",
                                  Top: "top", Bottom: "bottom" };
        export function useUpdateNodeInternals() { return () => {}; }
        globalThis.__flowStore = { edges: [], nodeLookup: new Map() };
        export function useStore(selector) {
          return selector(globalThis.__flowStore);
        }
        export function Handle({ type, id }) {
          return createElement("div", { className: "test-port",
                                        "data-porttype": type,
                                        "data-handleid": id || "" });
        }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import { WorkflowCtx, buildNodeTypes }
  from "${join(FRONT, "src/workflow/nodes.jsx").replace(/\\/g, "/")}";
import { edgeAllowed }
  from "${join(FRONT, "src/workflow/registry.js").replace(/\\/g, "/")}";
export { buildNodeTypes, edgeAllowed };
export function draw(el, Comp, props, ctx) {
  const root = createRoot(el);
  act(() => { root.render(
    h(WorkflowCtx.Provider, { value: ctx }, h(Comp, props))); });
  const html = el.innerHTML;
  try { act(() => root.unmount()); } catch { /* already dead */ }
  return html;
}
`;

await build({
  plugins: [shimXyflow],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});

const { draw, buildNodeTypes, edgeAllowed } = req(OUT);
const Probe = buildNodeTypes(registry)["common.debug"];
if (!Probe) {
  console.error("FAIL the registry has no common.debug to render");
  process.exit(1);
}


const host = document.body;
const Source = buildNodeTypes(registry)["source.detection"];
if (!Source) {
  console.error("FAIL the registry has no source.detection to render");
  process.exit(1);
}
const draw1 = (data) => {
  const el = document.createElement("div");
  host.appendChild(el);
  try {
    return draw(el, Source, { id: "d1", type: "source.detection", data,
                              selected: false },
                { registry, values: {}, errors: {}, warnings: {}, variables: { cameras: [] },
                  incoming: new Set(), wired: new Set(), offNodes: new Map(),
                  fanIn: new Map(), edges: [], objClasses: new Map(),
                  appRunning: true, aiStatus: null, waitsForDetection: null,
                  faceReads: new Map() });
  } finally { host.removeChild(el); }
};
const portsOf = (html) => [...html.matchAll(/data-porttype="([^"]*)"[^>]*data-handleid="([^"]*)"/g)]
  .map((m) => `${m[1]}:${m[2]}`);

// -- the ports ------------------------------------------------------------------
for (const [label, data] of [["no capture field at all (an old canvas)", {}],
                             ["capture = video", { capture: "video" }]]) {
  const ports = portsOf(draw1(data));
  if (ports.some((p) => p.startsWith("target"))) {
    fail(`${label}: the stream draws an input port ${ports.join(",")}`);
  } else {
    ok(`${label}: no input port, as before`);
  }
}
{
  const ports = portsOf(draw1({ capture: "snapshot" }));
  const targets = ports.filter((p) => p.startsWith("target"));
  if (targets.length === 1 && targets[0] === "target:snap") {
    ok("capture = snapshot: exactly one input, the snap port");
  } else {
    fail(`capture = snapshot: expected one target:snap, got ${ports.join(",")}`);
  }
  if (ports.filter((p) => p.startsWith("source")).length < 2) {
    fail("the ON / OFF outputs are gone in Snapshot mode");
  } else {
    ok("...and the ON / OFF outputs are still there");
  }
}

// -- the edge rule: the same answers as the server ---------------------------------
const PAIRS = [
  ["common.inject", "source.detection", "snap"],
  ["output.trigger", "source.detection", "snap"],
  ["variable.rs485", "source.detection", "snap"],
  ["variable.board_input", "source.detection", "snap"],
  ["common.inject", "source.detection", "in"],
  ["common.inject", "source.detection", ""],
  ["filter.class", "source.detection", "snap"],
  ["filter.class", "source.detection", "in"],
  ["source.detection", "filter.class", "in"],
  ["zone.ref", "source.detection", "snap"],
];
const ASK = ["import json,sys",
  "from core.services.workflow import edge_allowed",
  "pairs = json.loads(sys.argv[1])",
  "sys.stdout.write(json.dumps([edge_allowed(*p)[0] for p in pairs]))"].join(String.fromCharCode(10));
let server = null;
for (const py of ["py", "python", "python3"]) {
  const r = spawnSync(py, ["-c", ASK, JSON.stringify(PAIRS)], { cwd: ROOT, encoding: "utf8" });
  if (r.status === 0 && r.stdout) { try { server = JSON.parse(r.stdout); } catch { /* partial */ } }
  if (server) break;
}
if (!server) { console.log("skip: no python to ask the server"); process.exit(77); }
PAIRS.forEach((p, i) => {
  const [okClient] = edgeAllowed(registry, p[0], p[1], p[2]);
  if (okClient !== server[i]) {
    fail(`edge ${p.join(" -> ")}: the editor says ${okClient}, the server ${server[i]}`);
  }
});
if (!bad) ok(`the editor and the server agree on all ${PAIRS.length} snap wires`);

// -- the Frames field: only while wired into a Snapshot stream ---------------------
const wf = readFileSync(join(ROOT, "frontend/src/pages/Workflow.jsx"), "utf8");
const nodesSrc = readFileSync(join(ROOT, "frontend/src/workflow/nodes.jsx"), "utf8");
wf.includes("const wiredToSnap") && wf.includes('(tgt.data || {}).capture === "snapshot"')
  ? ok("Workflow.jsx works out whether a node is wired into a Snapshot stream")
  : fail("the wiredToSnap test is gone from Workflow.jsx");
wf.includes("showFrames={wiredToSnap}") && wf.includes("{wiredToSnap && <SnapFrames")
  ? ok("...and offers Frames to an Inject and a Trigger only then")
  : fail("the Frames field is not gated on wiredToSnap");
nodesSrc.includes('d.capture === "snapshot"')
  ? ok("nodes.jsx draws the snap input only in Snapshot mode")
  : fail("nodes.jsx no longer gates the snap input on capture");
for (const [name, text] of [["Workflow.jsx", wf], ["nodes.jsx", nodesSrc]]) {
  text.includes("\u2014") ? fail(`an em dash in ${name}`) : null;
}

// -- the Camera snaps gallery: only for a camera in Snapshot mode ------------------
const det = readFileSync(join(ROOT, "frontend/src/pages/Detection.jsx"), "utf8");
det.includes("{activeCam?.snap && <CameraSnaps camera={activeCam} />}")
  ? ok("Camera Setup shows Camera snaps only for a camera set to Snapshot")
  : fail("the Camera snaps card is not gated on the camera being in Snapshot mode");
det.includes('String(r.id).includes("_snp_")') && det.includes('String(r.camera) === String(camera.id)')
  ? ok("...and lists only that camera's snapped frames, not every snapshot")
  : fail("the Camera snaps list is not filtered to this camera's snaps");
det.includes('Every snap is also on the <Link to="/footage">Footage</Link> page')
  ? ok("...and says where else they are")
  : fail("the Camera snaps card no longer points at the Footage page");

try { rmSync(OUT, { force: true }); } catch { /* ignore */ }
console.log();
if (bad) { console.error(`${bad} check(s) FAILED`); process.exit(1); }
console.log("all snapshot node checks passed");
