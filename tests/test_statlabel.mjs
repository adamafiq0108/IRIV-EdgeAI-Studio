// statLabel: the raw keys from the screenshot must read as plain names.
import fs from "node:fs";
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Derived from this file's own location: the path used to be hardcoded to one
// laptop, which nobody noticed because the runner only globbed *.py and this
// test had never actually run.
const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");

const src = readText(join(REPO, "frontend", "src", "pages", "Workflow.jsx"));
const start = src.indexOf("function statLabel(");
const end = src.indexOf("\n}", start) + 2;
const fn = src.slice(start, end);
// statLabel names a camera by its SLOT, through the registry's camLabel --
// lifted here the same way, so this test keeps evaluating the real source
// rather than a copy that could drift from it.
const reg = readText(join(REPO, "frontend", "src", "workflow", "registry.js"));
const camStart = reg.indexOf("export function camLabel(");
const camFn = reg.slice(camStart, reg.indexOf("\n}", camStart) + 2)
  .replace("export ", "");
const statLabel = new Function(`${camFn}; ${fn}; return statLabel;`)();

const functions = [
  { id: "z_zone_msf_a1", name: "Zone 1 violations now" },
  { id: "total_violation", name: "Total violations" },
];
const cameras = [{ id: "lrcp_u3_imx307", name: "Bay cam" }];
const zones = [{ id: "msf", name: "Zone 1" }];

const cases = [
  "cam_lrcp_u3_imx307__lrcp_u3_imx307_fn_z_zone_msf_a1_count",
  "cam_lrcp_u3_imx307__lrcp_u3_imx307_fps",
  "cam_lrcp_u3_imx307__lrcp_u3_imx307_objects",
  "cam_lrcp_u3_imx307__lrcp_u3_imx307_violations",
  "fn_total_violation_value",
  "objects_in_frame",
  "violations_now",
  "zone_msf_count",
];
for (const k of cases) {
  console.log(String(statLabel(k, functions, cameras, zones)).padEnd(42), " <-- ", k);
}

const a = statLabel(cases[0], functions, cameras, zones);
if (!a.startsWith("Zone 1 violations now")) throw new Error(`not named: ${a}`);
if (!a.includes("Camera 1")) throw new Error(`camera lost: ${a}`);
if (statLabel("fn_total_violation_value", functions, cameras, zones)
    !== "Total violations · value") throw new Error("metric suffix wrong");
if (statLabel("objects_in_frame", functions, cameras, zones)
    !== "Objects in frame") throw new Error("global name wrong");
if (statLabel("zone_msf_count", functions, cameras, zones)
    !== "Zone 1 · count") throw new Error("zone name wrong");
// An unknown key must survive unchanged rather than being mangled.
if (statLabel("something_else", functions, cameras, zones) !== "something_else") {
  throw new Error("unknown key was rewritten");
}
console.log("\nALL OK");
