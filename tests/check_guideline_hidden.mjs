// The Workflow Guideline panel is hidden for now: the component stays, nothing renders it.
import fs from "fs";
let bad = 0;
const ok = (c, m) => { console.log((c ? "ok   " : "FAIL ") + m); if (!c) bad++; };
ok(fs.existsSync("frontend/src/workflow/Guideline.jsx"), "Guideline.jsx still exists");
const wf = fs.readFileSync("frontend/src/pages/Workflow.jsx", "utf8");
const live = wf.replace(/\/\*[\s\S]*?\*\//g, "").split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n");
ok(!/<Guideline\b/.test(live), "Workflow.jsx does not render <Guideline/>");
ok(!/import Guideline\b/.test(live), "the Guideline import is commented out");
ok(!/wf-help-tab/.test(live), "the show-guide tab is not rendered");
console.log(bad ? "FAILED" : "ALL OK");
process.exit(bad ? 1 : 0);
