// The Debug probe must say WHICH of its five states it is in.
//
// A probe exists to answer one question -- "what is actually coming down this
// wire?" -- asked by somebody who is already unsure. Every failure mode of
// such a node is the same failure: answering confidently and wrongly.
//
//   a probe reading NOTHING that draws 0 is indistinguishable from a rule
//   that is genuinely at zero, which is the case somebody is debugging;
//   a probe drawing last week's number while the app is DOWN is a stale
//   reading presented as a live one;
//   a probe on an Email that shows a dash cannot tell "it never fired"
//   (the condition never held) from "it fired and failed" (the token is
//   wrong) -- two different problems with two different fixes, and the two
//   an operator confuses most.
//
// So this drives the node through each state and asserts on the WORDS, not on
// a value: in four of the five there is no value to show, and the node's job
// is to say so. Pinned to the distinctions, never to the phrasing beyond the
// one word that carries each one.
//
// Also here: the probe was revived from a deprecated node whose body read a
// HAND-TYPED key and never looked at its own wire. Canvases in the field have
// one of those saved, so the typed key stays as the fallback and is checked.
//
//   node tests/check_debugnode.mjs
import { rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".debug-node-test.cjs");

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
// One probe, id "p1", in whatever state the caller describes. `read` is the
// descriptor the page resolves from the wire (faceReads in pages/Workflow.jsx);
// leaving it out is a canvas whose edges have not been read yet, which must
// look the same as nothing wired.
const probe = ({ read, values = {}, appRunning = true, data = {},
                 aiStatus = null, waitsForDetection = null } = {}) => {
  const el = document.createElement("div");
  host.appendChild(el);
  try {
    return draw(el, Probe, { id: "p1", type: "common.debug", data,
                             selected: false },
                { registry, values, errors: {}, warnings: {}, variables: {},
                  incoming: new Set(), wired: new Set(), offNodes: new Map(),
                  fanIn: new Map(), edges: [], objClasses: new Map(),
                  appRunning, aiStatus, waitsForDetection,
                  faceReads: read ? new Map([["p1", read]]) : new Map() });
  } finally { host.removeChild(el); }
};
const text = (html) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();

// -- the port contract --------------------------------------------------------
//
// A probe TAKES a wire and gives nothing back. A source port on it would
// invite a wire the compiler refuses on save, and no target port at all would
// leave the node with no way to be asked anything -- which is the state the
// deprecated version was effectively in, since it only ever read a typed key.
{
  const html = probe();
  const ports = [...html.matchAll(/data-porttype="([^"]*)"/g)].map((m) => m[1]);
  if (!ports.includes("target")) {
    fail("the probe draws no input port - nothing can be wired to it");
  } else if (ports.includes("source")) {
    fail("the probe draws an output port - it is a dead end by design, and "
         + "the wire would be refused on save");
  } else {
    ok("the probe takes one wire in and gives nothing back");
  }
}

// -- (b) nothing wired --------------------------------------------------------
{
  const t = text(probe());
  if (/\b0\b/.test(t)) {
    fail(`an unwired probe drew a number: "${t}"`);
  } else if (!/wire something/i.test(t)) {
    fail(`an unwired probe did not say it is reading nothing: "${t}"`);
  } else {
    ok("nothing wired: the probe says so instead of drawing a 0");
  }
}

// -- (a) an ordinary value, number and text -----------------------------------
{
  const read = { kind: "value", key: "fn_bay_count", label: "Bay counter" };
  const t = text(probe({ read, values: { fn_bay_count: 7 } }));
  if (!t.includes("7")) fail(`a wired probe did not show its number: "${t}"`);
  else if (!t.includes("Bay counter")) {
    fail(`a wired probe did not say WHICH value it is reading: "${t}"`);
  } else ok("wired to a rule: the number, and the name of what publishes it");

  // THE TEXT CASE. Rounding a string gives NaN, which is how a text variable
  // looked broken on every face that showed one; and an empty string has to
  // stay visible as a value rather than vanish into the layout.
  const words = text(probe({ read: { kind: "value", key: "fn_object_name_value",
                                     label: "object name" },
                             values: { fn_object_name_value: "People" } }));
  if (!words.includes("People")) {
    fail(`a probe on a text value did not show the word: "${words}"`);
  } else if (words.includes("NaN")) {
    fail(`a probe rounded a word into NaN: "${words}"`);
  } else ok("wired to a text value: the word, as the word it is");

  // Wired, but the app has published nothing under that key yet: a third
  // state again, and not the same as zero.
  const quiet = text(probe({ read }));
  if (/\b0\b/.test(quiet)) {
    fail(`a probe whose key has no value yet drew a 0: "${quiet}"`);
  } else if (!/waiting|nothing published/i.test(quiet)) {
    fail(`a probe whose key has no value yet said nothing about it: "${quiet}"`);
  } else ok("wired but not published yet: waiting, not zero");
}

// -- (c) the app is not running -----------------------------------------------
//
// The stats map still holds last run's numbers, so this is the one state
// where showing the value WOULD have worked and must not.
{
  const t = text(probe({ read: { kind: "value", key: "fn_bay_count",
                                 label: "Bay counter" },
                         values: { fn_bay_count: 7 }, appRunning: false }));
  if (t.includes("7")) {
    fail(`a probe drew a stale number while the app was stopped: "${t}"`);
  } else if (!/stopped/i.test(t)) {
    fail(`a probe did not say the app is stopped: "${t}"`);
  } else ok("app stopped: said plainly, and the stale number withheld");
}

// -- (d) an output: did it actually fire? -------------------------------------
//
// An Email, a Telegram, a board pin or a Modbus write publishes no reading at
// all -- only a fate. Four stats per output document id, written by the app:
// out_<id>_fired, _ok, _at, _error.
{
  const read = { kind: "output", id: "wf_tg1", label: "Telegram" };
  const never = text(probe({ read, values: { out_wf_tg1_fired: 0,
                                             out_wf_tg1_ok: 0,
                                             out_wf_tg1_at: 0,
                                             out_wf_tg1_error: "" } }));
  if (!/never fired/i.test(never)) {
    fail(`an output that has never fired did not say so: "${never}"`);
  } else if (/fail/i.test(never)) {
    fail(`an output that has never fired was reported as a failure: "${never}"`);
  } else ok("an output that never fired says never fired, not failed");

  const sent = text(probe({ read, values: {
    out_wf_tg1_fired: 3, out_wf_tg1_ok: 1,
    out_wf_tg1_at: Math.round(Date.now() / 1000) - 90, out_wf_tg1_error: "" } }));
  if (!/sent/i.test(sent)) fail(`a successful send did not say so: "${sent}"`);
  else if (!sent.includes("3")) {
    fail(`a successful send did not say how many times: "${sent}"`);
  } else if (!/ago/.test(sent)) {
    fail(`a successful send did not say how long ago: "${sent}"`);
  } else ok("an output that fired says so, how often, and how long ago");

  // THE MESSAGE IS THE POINT. "It failed" sends somebody to the logs;
  // "chat not found" is the fix.
  const failed = text(probe({ read, values: {
    out_wf_tg1_fired: 2, out_wf_tg1_ok: 0,
    out_wf_tg1_at: Math.round(Date.now() / 1000) - 5,
    out_wf_tg1_error: "chat not found" } }));
  if (!/FAILED/.test(failed)) {
    fail(`a failed send was not marked as failed: "${failed}"`);
  } else if (!failed.includes("chat not found")) {
    fail(`a failed send did not show WHY it failed: "${failed}"`);
  } else ok("an output that failed says FAILED, and what the box was told");

  // Nothing published under that id at all: the graph has not reached the app
  // yet. Distinct again from "never fired", which is a running system whose
  // condition has not held.
  const silent = text(probe({ read }));
  if (/never fired/i.test(silent)) {
    fail(`an output the app has not heard of was reported as never firing, `
         + `which is a different problem: "${silent}"`);
  } else if (!/no report|not reported/i.test(silent)) {
    fail(`an output with no stats at all said nothing useful: "${silent}"`);
  } else ok("an output the app has not picked up yet says it has not reported");
}

// -- a Data Table column: real, and not in the stats dict ---------------------
//
// A table's read ports hand back ROWS, out of the database. A dash there
// would read as "nothing is happening" on a table that is filling nicely.
{
  const t = text(probe({ read: { kind: "table", label: "Crossings" } }));
  if (!/database/i.test(t)) {
    fail(`a probe on a Data Table column did not explain itself: "${t}"`);
  } else ok("a Data Table column: the rows are in the database, and it says so");
}

// -- (e) the hand-typed key still works ---------------------------------------
//
// The old probe read only a key typed into its inspector, and canvases in the
// field have one saved. Migrating those would mean guessing which wire was
// meant, so the typed key stays as a FALLBACK -- and the wire wins when there
// is one, because that is what the operator just drew.
{
  const typed = text(probe({ data: { metric: "fn_bay_count" },
                             values: { fn_bay_count: 4 } }));
  if (!typed.includes("4")) {
    fail(`a probe with a hand-typed key stopped reading it: "${typed}"`);
  } else ok("a hand-typed key still reads, for the probes already drawn");

  const both = text(probe({ data: { metric: "fn_bay_count" },
                            read: { kind: "value", key: "fn_gate_count",
                                    label: "Gate" },
                            values: { fn_bay_count: 4, fn_gate_count: 9 } }));
  if (!both.includes("9") || both.includes("4")) {
    fail(`the typed key beat the wire the operator drew: "${both}"`);
  } else ok("the wire wins over the typed key");
}

// -- the same reader, on a Set Variable's face --------------------------------
//
// A SET VARIABLE DOES NOT PUBLISH UNDER ITS OWN ID. It publishes as the
// variable wired into its input a (_set_target_fid in the compiler), so the
// key its face composed for itself -- fn_<its own fnId>_value, the rule every
// other node follows -- is a key nobody has ever written. Every Set Variable
// on every box in the field showed "= ..." for ever while the variable beside
// it counted perfectly, and that is most of what "set variable didn't display
// the result" meant.
//
// It reads through the same faceReads map the probe does, because it is the
// same question (which value does this face show?) and the answer needs the
// edges in both cases.
{
  const setvar = buildNodeTypes(registry)["math.setvar"];
  const drawSetvar = (ctxExtra) => {
    const el = document.createElement("div");
    host.appendChild(el);
    try {
      return draw(el, setvar,
                  { id: "sv1", type: "math.setvar", selected: false,
                    data: { ...(registry["math.setvar"].data || {}),
                            fnId: "set_variable_j066", name: "Set Variable" } },
                  { registry, errors: {}, warnings: {}, variables: {},
                    incoming: new Set(), wired: new Set(),
                    offNodes: new Map(), fanIn: new Map(), edges: [],
                    objClasses: new Map(), appRunning: true,
                    values: {}, faceReads: new Map(), ...ctxExtra });
    } finally { host.removeChild(el); }
  };
  // The bug: the value exists, under the VARIABLE's key, and the node cannot
  // see it.
  const blind = text(drawSetvar({
    values: { fn_object_name_value: "People",
              fn_set_variable_j066_value: undefined } }));
  if (blind.includes("People")) {
    fail("the fixture is wrong - the setvar read the variable with no map");
  }
  const seeing = text(drawSetvar({
    values: { fn_object_name_value: "People" },
    faceReads: new Map([["sv1", { kind: "value", key: "fn_object_name_value",
                                 label: "object name" }]]) }));
  if (!seeing.includes("People")) {
    fail(`a Set Variable did not show the variable it writes: "${seeing}"`);
  } else if (seeing.includes("NaN")) {
    fail(`a Set Variable rounded its word into NaN: "${seeing}"`);
  } else {
    ok("a Set Variable reads the variable it writes, text and all");
  }
  // Nothing wired into input a: there is no variable yet, and inventing a
  // key would put a number from somewhere else on the node's face.
  const empty = text(drawSetvar({
    values: { fn_object_name_value: "People" },
    faceReads: new Map([["sv1", { kind: "none", why: "wire the Variable" }]]) }));
  if (empty.includes("People")) {
    fail(`a Set Variable with no target showed another variable: "${empty}"`);
  } else ok("a Set Variable with no target shows no value at all");
}

// -- (f) an Advanced Trigger, AI on and off -----------------------------------
//
// Tok Aba (2026-10-05, v1.0.16): a Trigger with payload 17, once per object,
// hold 10 s, idle 0, wired into a probe. The page now resolves that probe to
// fn_<rule>_out under the Trigger's own name (pinned in smoke_triggerout.py);
// here is what the node then SAYS. With the AI on the idle 0 is a reading,
// not "waiting". With the AI off the app holds every detection-fed node and
// publishes nothing for it, so "nothing published yet" would stand for ever:
// the probe must say that detection is off instead.
{
  const read = { kind: "value", key: "fn_many_b0_out", label: "People_Trigger",
                 node: "t1" };
  const on = { ok: true, reason: "" };
  const off = { ok: false, reason: "AI detection is off" };
  const waits = new Set(["t1", "cmp", "fr", "src"]);
  let t = text(probe({ read, values: { fn_many_b0_out: 0 }, aiStatus: on,
                       waitsForDetection: waits }));
  if (!/\b0\b/.test(t) || /waiting|holding|nothing published/i.test(t)) {
    fail(`AI on: the idle 0 is a reading, and was not drawn as one: "${t}"`);
  } else if (!t.includes("People_Trigger")) {
    fail(`AI on: the probe did not name the Trigger: "${t}"`);
  } else ok("AI on: an Advanced Trigger's idle 0 is shown as 0, by name");
  t = text(probe({ read, values: { fn_many_b0_out: 17 }, aiStatus: on,
                   waitsForDetection: waits }));
  if (!t.includes("17")) fail(`AI on: the payload 17 was not shown: "${t}"`);
  else ok("AI on: ...and 17 while it holds");
  t = text(probe({ read, values: {}, aiStatus: off, waitsForDetection: waits }));
  if (/nothing published|holding|waiting/i.test(t)) {
    fail(`AI off: the probe still says the node is quiet rather than that `
         + `detection is off: "${t}"`);
  } else if (!/AI off/i.test(t) || !/detection/i.test(t)
             || !t.includes("People_Trigger")) {
    fail(`AI off: the probe did not say plainly that detection is off: "${t}"`);
  } else ok("AI off: the probe says the AI is off and names who waits for it");
  // A node that does NOT wait for detection (a Constant, an Inject) runs
  // with the AI off, so its probe keeps its ordinary words.
  t = text(probe({ read: { kind: "value", key: "fn_ten_value", label: "Ten",
                           node: "k" },
                   values: {}, aiStatus: off, waitsForDetection: waits }));
  if (/AI off/i.test(t) || !/nothing published/i.test(t)) {
    fail(`AI off: a free node's probe was blamed on the AI: "${t}"`);
  } else ok("AI off: a node that runs without detection keeps its own words");
  // The app down still wins: a stale reading is the worse lie.
  t = text(probe({ read, values: { fn_many_b0_out: 17 }, appRunning: false,
                   aiStatus: off, waitsForDetection: waits }));
  if (!/app stopped/i.test(t) || t.includes("17")) {
    fail(`app down and AI off: the probe did not say the app is down first: "${t}"`);
  } else ok("app down: said before the AI, with no stale value");
}

// -- the two wiring rules must agree ------------------------------------------
//
// edge_allowed lives twice: in core/services/workflow.py, which decides what
// SAVES, and in frontend/src/workflow/registry.js, which decides what can be
// DRAWN. A wire the canvas allows and the server refuses is the worst of the
// two failures -- the operator draws it, it appears, and the save comes back
// with an error about a wire that is on screen in front of them. Widening the
// probe's rule touched both files, so both are asked the same questions here,
// against the real server registry rather than a list kept by hand.
{
  const ASK = "import json,sys\n"
    + "from core.services.workflow import REGISTRY, edge_allowed\n"
    + "pairs=[]\n"
    + "seen=set()\n"
    + "for t,s in REGISTRY.items():\n"
    + "    if s['category'] in seen: continue\n"
    + "    seen.add(s['category']); pairs.append(t)\n"
    + "out=[]\n"
    + "for t in pairs:\n"
    + "    out.append([t,'common.debug',edge_allowed(t,'common.debug')[0]])\n"
    + "    out.append(['common.debug',t,edge_allowed('common.debug',t)[0]])\n"
    + "sys.stdout.write(json.dumps(out))";
  let answers = null;
  for (const py of ["py", "python", "python3"]) {
    const r = spawnSync(py, ["-c", ASK], { cwd: ROOT, encoding: "utf8" });
    if (r.status === 0 && r.stdout) {
      try { answers = JSON.parse(r.stdout); } catch { /* partial output */ }
      if (answers) break;
    }
  }
  if (!answers) {
    fail("could not ask the server which probe wires it allows");
  } else {
    const disagree = answers.filter(([src, tgt, server]) =>
      edgeAllowed(registry, src, tgt, "")[0] !== server);
    if (disagree.length) {
      fail("the canvas and the server disagree about these wires, so each "
           + "one draws and is then refused on save (or cannot be drawn at "
           + "all): "
           + disagree.map(([s, t, v]) => `${s} -> ${t} (server ${v})`).join(", "));
    } else {
      ok(`both wiring rules answer the same for ${answers.length} probe wires`);
    }
  }
}

rmSync(OUT, { force: true });

if (bad) {
  console.error(`\n${bad} Debug probe problem(s).`);
  process.exit(1);
}
console.log("== the Debug probe names every state it can be in");
