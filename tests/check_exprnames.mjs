// A Custom Formula grows one input port per name its expression refers to.
// exprNames() capped that at 8, so a formula over ten parking lots silently
// lost its last two ports (nothing to wire to, and still "Formula looks
// valid"). The server binds a port to a name with no limit; only this list
// held it back.
//   node tests/check_exprnames.mjs
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readText(join(ROOT, "frontend/src/workflow/ExprEditor.jsx"));
let bad = 0;
function check(label, ok, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${ok ? "" : `\n      ${detail}`}`);
  if (!ok) bad += 1;
}

// Pull the two definitions out of the component file (it imports React, so it
// cannot be loaded in plain node) and run them against a small reserved set.
const cap = /export const EXPR_MAX_NAMES = (\d+);/.exec(src);
const fnSrc = /export function exprNames\(expr, max = EXPR_MAX_NAMES\) \{[\s\S]*?\n\}/.exec(src);
check("the cap is a named constant of at least 24", !!cap && Number(cap[1]) >= 24, cap && cap[0]);
check("exprNames takes it as its default", !!fnSrc);
const EXPR_RESERVED = new Set(["max", "min", "round", "and", "or", "not"]);
const EXPR_MAX_NAMES = cap ? Number(cap[1]) : 0;
const exprNames = new Function("EXPR_RESERVED", "EXPR_MAX_NAMES",
  `${fnSrc[0].replace("export ", "")}; return exprNames;`)(EXPR_RESERVED, EXPR_MAX_NAMES);

const ten = "10 - (lot1a + lot2a + lot3a + lot4a + lot5a + lot1b + lot2b + lot3b + lot4b + lot5b)";
check("ten names give ten ports, in the order written",
      JSON.stringify(exprNames(ten)) === JSON.stringify(
        ["lot1a", "lot2a", "lot3a", "lot4a", "lot5a", "lot1b", "lot2b", "lot3b", "lot4b", "lot5b"]),
      JSON.stringify(exprNames(ten)));
check("engine names and repeats are not ports",
      JSON.stringify(exprNames("max(a, a) + round(b) and not c")) === JSON.stringify(["a", "b", "c"]));
const many = Array.from({ length: 60 }, (_, i) => `v${i}`).join(" + ");
check("the ceiling still holds for an absurd formula",
      exprNames(many).length === EXPR_MAX_NAMES, exprNames(many).length);

console.log(`\n${bad ? `${bad} FAILED` : "ALL PASS"}`);
process.exit(bad ? 1 : 0);
