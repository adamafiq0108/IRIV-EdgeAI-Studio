// What the Variables drawer lists, and how it prints a value.
//
// Three faults reported together from a live board on 2026-09-24, with a
// screenshot of a drawer reading "Variables 6" over six rows:
//
//   object_type      NaN
//   object_type_2    NaN
//   If / Else if - branch 1     0
//   If / Else if - branch 2     0
//   If / Else if - otherwise    1
//
//   1. NaN. A rule that picks a WORD now reaches the browser, and the row
//      printed Math.round(value * 100) / 100 -- which is NaN for "No Object
//      Detected". The variable was working perfectly and the drawer said it
//      was broken.
//   2. The branches. An If / Else if compiles to one rule per port so the
//      engine has something to gate on. Those are logic, not values anybody
//      declared, and here they crowded out the operator's own names: three
//      branch rows in a drawer holding two real variables. "if else is not
//      variable, it is a logic, so dont need to display on the variable".
//   3. The missing ones. A Set Variable publishes under the VARIABLE it
//      writes while the compiled rule carries the NODE's name, and nobody
//      renames a Set Variable -- so the names an operator chose appeared
//      nowhere and three identical "Set Variable" rows appeared instead.
//      Reported as "when i create variable then it didnt show here".
//   4. The vanishing one. Deleting a node took its variable out of the
//      compiled functions at once, while the running app went on publishing
//      the last value it had -- and "unused" required no live value, so for
//      those seconds the name was in neither list and disappeared from the
//      drawer entirely. "after i remove from the flow, why it didnt show as
//      not used variable? it turn out to be missing".
//
//   node tests/check_varpanel.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".varpanel-test.cjs");

// Workflow.jsx reads the URL hash at module scope, so the module cannot even
// load without a window. Nothing below renders; this is only enough browser
// for the file to be imported at all.
const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent", "localStorage",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "matchMedia"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// Workflow.jsx drags React Flow and the whole editor in at module scope, so
// the helpers under test are pulled out through the same shim the other
// canvas checks use. Nothing here renders: these are the rules, not the look.
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
        const Stub = () => createElement("div");
        export const Handle = Stub;
        export const BaseEdge = Stub;
        export const EdgeLabelRenderer = ({ children }) => children || null;
        export const ReactFlow = Stub;
        export const ReactFlowProvider = ({ children }) => children || null;
        export const Background = Stub;
        export const Controls = Stub;
        export const MiniMap = Stub;
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
await build({
  plugins: [shimXyflow],
  stdin: {
    contents: `export { showValue, isBranchFn, isUnusedVar } from "${p("src/pages/Workflow.jsx")}";`,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const { showValue, isBranchFn, isUnusedVar } = req(OUT);

// ===========================================================================
// 1. a WORD is printed as the word
// ===========================================================================
check("a text value is shown as itself, not as NaN",
      showValue("No Object Detected") === "No Object Detected",
      JSON.stringify(showValue("No Object Detected")));
check("...including one that looks like a sentence with digits in it",
      showValue("Chair 2 Detected") === "Chair 2 Detected",
      JSON.stringify(showValue("Chair 2 Detected")));
check("an empty word stays empty rather than becoming 0",
      showValue("") === "", JSON.stringify(showValue("")));

// ===========================================================================
// 2. numbers keep rounding exactly as they did
// ===========================================================================
check("a number still rounds to two places", showValue(1.23456) === 1.23,
      String(showValue(1.23456)));
check("a whole number is untouched", showValue(7) === 7, String(showValue(7)));
check("zero is zero, not a dash", showValue(0) === 0, String(showValue(0)));
// The stats channel sends a device's reading back as a string more often than
// anyone expects, and those have always rounded. They must keep rounding, or
// this fix trades NaN for a row of unreadable decimals.
check("a numeric string still rounds", showValue("1.23456") === 1.23,
      String(showValue("1.23456")));
check("nothing published reads as a dash", showValue(undefined) === "-",
      String(showValue(undefined)));
check("...and so does null", showValue(null) === "-", String(showValue(null)));
// NaN and Infinity are not values an operator can act on, and printing either
// is what this whole case is about.
check("an actual NaN is a dash, never the word NaN",
      showValue(NaN) === "-", String(showValue(NaN)));
check("infinity is a dash too", showValue(Infinity) === "-",
      String(showValue(Infinity)));

// ===========================================================================
// 3. branch rules are not variables
// ===========================================================================
const bases = new Set(["if_else_if_qev6", "switch_a1"]);
check("an If / Else if branch is recognised as logic",
      isBranchFn("if_else_if_qev6_b0", bases));
check("...every port of it, not just the first",
      isBranchFn("if_else_if_qev6_b1", bases)
      && isBranchFn("if_else_if_qev6_b2", bases));
check("a Switch branch too", isBranchFn("switch_a1_b3", bases));
check("the operator's own variable is NOT logic",
      !isBranchFn("object_type", bases));
// The suffix test alone would eat a variable somebody called "total_b2". It
// is only logic when a branch node on THIS canvas owns the base name.
check("...not even one whose name ends like a branch port",
      !isBranchFn("total_b2", bases), "total_b2 was taken for a branch");
check("...and not a branch base with no node behind it",
      !isBranchFn("some_other_rule_b0", bases));
check("a name with no port number is not logic",
      !isBranchFn("if_else_if_qev6_bx", bases));

// ===========================================================================
// 4. a variable whose node was deleted moves to Unused, it does not vanish
// ===========================================================================
// The compiled functions lose the variable the moment the node goes, while
// the running app keeps publishing its last value until it picks the new
// graph up. Requiring "no live value" to call something unused put the name
// in NEITHER list for those seconds, so an operator who had just deleted a
// node watched their variable disappear. Reported as "after i remove from the
// flow, why it didnt show as not used variable? it turn out to be missing".
const V = { name: "object name", var: "object_name", key: "fn_object_name_value" };
check("a variable nothing publishes and nothing uses is unused",
      isUnusedVar(V, new Set(), new Set()));
check("...even while the box is still publishing its last value",
      isUnusedVar(V, new Set(), new Set()),
      "a stale reading says nothing about whether anything writes the name");
check("a variable something publishes is NOT unused",
      !isUnusedVar(V, new Set(["fn_object_name_value"]), new Set()));
check("a variable a node still reads is NOT unused",
      !isUnusedVar(V, new Set(), new Set(["object_name"])));
check("...and one that is both is certainly not unused",
      !isUnusedVar(V, new Set(["fn_object_name_value"]), new Set(["object_name"])));

console.log();
if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log("ALL OK");
