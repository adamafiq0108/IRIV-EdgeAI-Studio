// The Common drawer lists every link name already on the canvas, and dragging
// out of it gives a Link in or a Link out already carrying that name, so a
// jump name made once is picked, never retyped (a typo carries nothing).
//   node tests/check_linkpalette.mjs
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const wf = readText(join(ROOT, "frontend/src/pages/Workflow.jsx"));
let bad = 0;
function check(label, ok) {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) bad += 1;
}
const names = wf.slice(wf.indexOf("const linkNames = (() => {"),
                       wf.indexOf("const paletteCat = (sp)"));
check("names come from the Link in and Link out nodes on the canvas, unique, empty ones skipped",
      names.includes('"common.link_in"') && names.includes('"common.link_out"')
      && names.includes("new Map()") && names.includes("if (!name) continue;"));
check("each name counts its ins and outs",
      names.includes("row.ins += 1") && names.includes("row.outs += 1"));
check("the search box filters them like every other entry", names.includes("r.name.includes(search)"));
check("the Common drawer shows them under their own heading",
      wf.includes('cat === "common" && linkNames.length > 0')
      && wf.includes("Links on this canvas ("));
check("a Link in and a Link out can each be dragged out already named",
      wf.includes("[\"common.link_in\", \"Link in\", l.ins")
      && wf.includes("[\"common.link_out\", \"Link out\", l.outs")
      && wf.includes("dragProps(type, { link: l.name })"));
check("the drawer's count includes them", wf.includes('(cat === "common" ? linkNames.length : 0)'));
console.log(`\n${bad ? `${bad} FAILED` : "ALL PASS"}`);
process.exit(bad ? 1 : 0);
