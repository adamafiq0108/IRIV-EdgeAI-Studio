// Every JSX component a file renders must be imported or defined in it.
//
// This exists because a build that passes is NOT proof the page renders. Vite
// compiles `<FormDialog …>` happily whether or not FormDialog was ever
// imported; the failure arrives as a blank page and "FormDialog is not
// defined" the first time that branch renders -- possibly weeks later, on a
// dialog nobody opens often. There is no ESLint in this project, so nothing
// else catches it.
//
// Deliberately narrow: it checks COMPONENT references (a JSX tag starting
// with a capital), not every identifier. That is the mistake this codebase
// actually makes -- moving a component between files, or adding one to a
// panel and forgetting the import -- and a narrow check with no false
// positives gets to stay in CI.
//
//   node tests/check_jsx_refs.mjs
import { readdirSync, readFileSync, statSync } from "node:fs";
import { readText } from "./_text.mjs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "frontend", "src");

/** Every .jsx file under frontend/src. */
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (name !== "node_modules") walk(path, out);
    } else if (name.endsWith(".jsx") || name.endsWith(".js")) {
      out.push(path);
    }
  }
  return out;
}

/** Names this file brings into scope: imports, declarations, destructuring. */
function declared(src) {
  const names = new Set();
  const add = (n) => n && names.add(n);
  // import X, {A as B, C} from "..."  /  import * as NS from "..."
  for (const m of src.matchAll(/import\s+([^;]+?)\s+from\s*["'][^"']+["']/g)) {
    const clause = m[1];
    for (const part of clause.split(/,(?![^{]*})/)) {
      const bit = part.trim();
      if (!bit) continue;
      if (bit.startsWith("{")) {
        for (const spec of bit.replace(/[{}]/g, "").split(",")) {
          const [, alias] = spec.trim().split(/\s+as\s+/);
          add((alias || spec).trim());
        }
      } else if (bit.startsWith("*")) {
        add(bit.split(/\s+as\s+/)[1]?.trim());
      } else {
        add(bit);
      }
    }
  }
  for (const m of src.matchAll(/(?:^|\n)\s*(?:export\s+)?(?:default\s+)?function\s+([A-Z]\w*)/g)) add(m[1]);
  for (const m of src.matchAll(/(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+([A-Z]\w*)/g)) add(m[1]);
  for (const m of src.matchAll(/(?:^|\n)\s*(?:export\s+)?class\s+([A-Z]\w*)/g)) add(m[1]);
  // const { A, B } = something  /  function F({ A }) -- components handed in
  for (const m of src.matchAll(/(?:const|let|var)\s*\{([^}]*)\}\s*=/g)) {
    for (const spec of m[1].split(",")) {
      const [, alias] = spec.trim().split(/:\s*/);
      add((alias || spec).trim().replace(/\s*=.*$/, ""));
    }
  }
  // Props destructured in a signature, incl. `icon: Icon` renames.
  for (const m of src.matchAll(/function\s+\w+\s*\(\s*\{([\s\S]*?)\}\s*\)/g)) {
    for (const spec of m[1].split(",")) {
      const [, alias] = spec.trim().split(/:\s*/);
      add((alias || spec).trim().replace(/\s*=[\s\S]*$/, ""));
    }
  }
  return names;
}

/** The file with comments blanked out, line numbering preserved.
 *
 *  Prose in this codebase mentions components constantly ("the SAME <Widget>
 *  the dashboard renders"), and counting those as usage is how a checker
 *  earns a reputation for crying wolf and gets switched off. Blanking rather
 *  than deleting keeps reported line numbers pointing at the right place.
 */
function stripComments(src) {
  const blank = (m) => m.replace(/[^\n]/g, " ");
  return src
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/(^|[^:"'`\\])\/\/[^\n]*/g, (m, lead) => lead + blank(m.slice(lead.length)))
    // String literals too: a thrown message can name a component
    // ("useApp must be used inside <AppShell>") without rendering one.
    .replace(/"(?:[^"\\\n]|\\.)*"/g, blank)
    .replace(/'(?:[^'\\\n]|\\.)*'/g, blank);
}

/** Component tags used: <Foo …>, <Foo.Bar …>. */
function used(src) {
  const out = new Map();
  const lines = stripComments(src).split("\n");
  lines.forEach((line, i) => {
    for (const m of line.matchAll(/<([A-Z]\w*)(?:\.\w+)?[\s/>]/g)) {
      if (!out.has(m[1])) out.set(m[1], i + 1);
    }
  });
  return out;
}

/** Every `setFoo` this file brings into scope, by any means.
 *
 *  The second half of the same problem: deleting a `useState` and leaving a
 *  `setFoo(...)` call behind compiles perfectly and throws "setFoo is not
 *  defined" the moment that line runs. That shipped once -- a state removed
 *  from the Workflow page, its setter still called in the load effect, which
 *  made the whole page a blank screen on the device and nowhere else.
 *
 *  Setters are worth singling out because the naming convention makes them
 *  unambiguous: `set` + a capital is a state setter by convention everywhere
 *  in this codebase, so a check on them has no false positives to argue
 *  about, unlike checking every identifier.
 */
function declaredSetters(src) {
  const names = new Set();
  const add = (n) => n && /^set[A-Z]/.test(n) && names.add(n);
  // const [value, setValue] = useState(...)
  // The value half may be ABSENT -- `const [, setVersion] = useState(0)`
  // is how a component that only needs to re-render itself is written.
  for (const m of src.matchAll(/\[\s*[\w$]*\s*,\s*(set[A-Z]\w*)\s*\]/g)) add(m[1]);
  // const setX = ... / function setX(...) / let setX
  for (const m of src.matchAll(/(?:const|let|var|function)\s+(set[A-Z]\w*)/g)) add(m[1]);
  // { setX } destructured from props, context or a hook's return
  for (const m of src.matchAll(/[{,]\s*(set[A-Z]\w*)\s*[,}:=]/g)) add(m[1]);
  // imported, and props named `setX` in a signature
  for (const m of src.matchAll(/\b(set[A-Z]\w*)\s*[,}]/g)) add(m[1]);
  return names;
}

/** Every helper a local module EXPORTS, and which module exports it.
 *
 *  The third half of the same problem, and the one that got through twice in
 *  one day: a file calls `useEscape(...)` or `printPage(...)`, the import was
 *  never added, and Vite compiles it happily because an undefined name is only
 *  a problem when the line runs. Both shipped -- one made the whole Workflow
 *  page a crash screen on the device.
 *
 *  Narrowed to helpers exported from lib/ and hooks/, where the names are
 *  distinctive and a call is unambiguous. A general "is every identifier
 *  defined" check is a linter, and this project has none for a reason: a
 *  checker with false positives gets switched off, and then catches nothing.
 */
function helperExports(files) {
  const out = new Map();                      // name -> module basename
  for (const path of files) {
    if (!/[\\/](lib|hooks)[\\/]/.test(path)) continue;
    const src = readText(path);
    const mod = path.split(/[\\/]/).pop();
    for (const m of src.matchAll(/export\s+(?:async\s+)?function\s+([a-z]\w*)/g)) {
      out.set(m[1], mod);
    }
    for (const m of src.matchAll(/export\s+const\s+([a-z]\w*)\s*=/g)) {
      out.set(m[1], mod);
    }
  }
  return out;
}

//: Browser globals that happen to match the setter convention. Without these
//: the check reports every timer in the codebase and gets switched off.
const GLOBAL_SETTERS = new Set(["setTimeout", "setInterval", "setImmediate"]);

/** `setFoo(` calls, excluding `something.setFoo(` which is a method. */
function usedSetters(src) {
  const out = new Map();
  stripComments(src).split("\n").forEach((line, i) => {
    for (const m of line.matchAll(/(^|[^.\w$])(set[A-Z]\w*)\s*\(/g)) {
      if (!GLOBAL_SETTERS.has(m[2]) && !out.has(m[2])) out.set(m[2], i + 1);
    }
  });
  return out;
}

let bad = 0;
const FILES = walk(SRC);
const HELPERS = helperExports(FILES);

/** Where this file MENTIONS a helper from lib/ or hooks/.
 *
 *  A mention, not a call: `onClick={printPage}` hands the function over
 *  without calling it, and that is exactly how the Analytics PDF button was
 *  written -- so a check that only looked for `printPage(` saw nothing and the
 *  missing import shipped. `x.helper` is skipped, being a method on something
 *  else entirely.
 */
function usedHelpers(src) {
  const out = new Map();
  stripComments(src).split("\n").forEach((line, i) => {
    for (const name of HELPERS.keys()) {
      if (out.has(name)) continue;
      const re = new RegExp(`(^|[^.\\w$])${name}(?![\\w$])`);
      if (re.test(line)) out.set(name, i + 1);
    }
  });
  return out;
}

for (const file of FILES) {
  const src = readText(file);
  const have = declared(src);
  for (const [name, line] of used(src)) {
    if (have.has(name)) continue;
    // React.Fragment shorthand and known globals are not components.
    if (["React", "Fragment"].includes(name)) continue;
    const rel = file.slice(file.indexOf("frontend"));
    console.error(`${rel}:${line}: <${name}> is used but never imported or defined`);
    bad += 1;
  }
  // A helper from lib/ or hooks/ that this file calls without importing it.
  // Skipped in the module that exports it, where the name is defined locally.
  const isHelperModule = /[\\\\/](lib|hooks)[\\\\/]/.test(file);
  if (!isHelperModule) {
    const local = declaredSetters(src);
    for (const [name, line] of usedHelpers(src)) {
      // A local state setter may share a name with an exported helper --
      // Blynk's own setToken is not session.js's setToken.
      if (have.has(name) || local.has(name)) continue;
      const rel = file.slice(file.indexOf("frontend"));
      console.error(`${rel}:${line}: ${name}() is used but never imported `
                    + `-- it lives in ${HELPERS.get(name)}`);
      bad += 1;
    }
  }
  const setters = declaredSetters(src);
  for (const [name, line] of usedSetters(src)) {
    if (setters.has(name)) continue;
    const rel = file.slice(file.indexOf("frontend"));
    console.error(`${rel}:${line}: ${name}() is called but never declared `
                  + "-- a useState removed without its setter?");
    bad += 1;
  }
}

if (bad) {
  console.error(`\n${bad} undefined reference(s) - each renders as a blank `
    + "page with 'X is not defined', which the build does not catch.");
  process.exit(1);
}
console.log("== every component rendered and every setter called is declared");
