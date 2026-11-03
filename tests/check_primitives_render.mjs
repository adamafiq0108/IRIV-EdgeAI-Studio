// Shared UI primitives must render with the props the app actually passes.
//
// Written after EmptyState shipped broken: it decided "is this an icon
// component or a glyph?" with `typeof icon === "function"`, and a lucide icon
// is a forwardRef OBJECT, not a function. So the check failed, the component
// object was rendered as a CHILD, and React threw error #31 -- a blank page
// on Footage with a minified stack.
//
// Nothing in the suite could have caught it: check_jsx_refs proves a name is
// imported, not that rendering it works, and the widget render test does not
// touch primitives. So the primitives get their own mount, with the REAL
// lucide icons the pages pass rather than a stand-in that would have had the
// wrong shape and hidden the bug all over again.
//
//   node tests/check_primitives_render.mjs
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".primitives-render-test.cjs");

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

const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import { EmptyState } from "${join(FRONT, "src/components/ui/primitives.jsx").replace(/\\/g, "/")}";
import { Film, Image as ImageIcon, LayoutGrid } from "lucide-react";
export const ICONS = { Film, ImageIcon, LayoutGrid };
export function draw(el, props) {
  const root = createRoot(el);
  let err = null;
  // React reports a render throw through the console as well as rethrowing;
  // the throw is what this asserts on, so the console noise is silenced.
  const quiet = console.error;
  console.error = () => {};
  try {
    act(() => { root.render(h(EmptyState, props)); });
  } catch (e) {
    err = e;
  } finally {
    console.error = quiet;
  }
  const html = err ? "" : el.innerHTML;
  try { act(() => root.unmount()); } catch { /* already dead */ }
  if (err) throw err;
  return html;
}
`;

await build({
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});

const { draw, ICONS } = req(OUT);
const render = (props) => {
  const el = document.createElement("div");
  document.body.appendChild(el);
  try { return draw(el, props); } finally { document.body.removeChild(el); }
};

const attempt = (label, props, expect) => {
  let html;
  try {
    html = render(props);
  } catch (e) {
    fail(`${label} threw: ${String(e.message).split("\n")[0]}`);
    return;
  }
  if (expect && !expect(html)) fail(`${label} rendered the wrong thing`);
  else console.log(`ok   ${label}`);
};

// The exact shape that broke: a lucide icon, which is a forwardRef object.
for (const [name, Icon] of Object.entries(ICONS)) {
  attempt(`a lucide ${name} icon renders as an icon`,
          { icon: Icon, title: "Nothing here" },
          // A rendered lucide icon is an <svg>; the FAILING version put the
          // component object in as a child, which threw before this point.
          (html) => html.includes("<svg") && html.includes("Nothing here"));
}

// The original callers pass a glyph string -- still supported, and it must
// NOT be treated as a component.
attempt("a glyph string still renders", { icon: "▦", title: "Nothing here" },
        (html) => html.includes("▦") && !html.includes("<svg"));

// The default, for callers that pass no icon at all.
attempt("the default glyph renders", { title: "Nothing here" },
        (html) => html.includes("◇"));

// The rest of the API, so a future edit cannot quietly drop one.
attempt("a hint renders", { title: "T", hint: "some hint" },
        (html) => html.includes("some hint"));
attempt("actions render as buttons",
        { title: "T", actions: [{ label: "Do it" }, { label: "Or this", primary: true }] },
        (html) => html.includes("Do it") && html.includes("Or this"));
attempt("an action with `to` renders a link",
        { title: "T", actions: [{ label: "Go", to: "/zones" }] },
        (html) => html.includes('href="/zones"'));
attempt("the legacy `action` node still renders",
        { title: "T", action: null }, (html) => html.includes("T"));

rmSync(OUT, { force: true });

if (bad) {
  console.error(`\n${bad} primitive render problem(s).`);
  process.exit(1);
}
console.log("\n== EmptyState renders with lucide icons, glyphs and actions");
