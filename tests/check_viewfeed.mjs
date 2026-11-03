// The Detection page's one read-only "Video: ..." line for [video] feed =
// "passthrough": wording per state, nothing at all for a system that never
// asked (no `view` in its status), and the page really renders it from the
// selected camera's status only while the system runs.
//
//   node tests/check_viewfeed.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "frontend", "src");
const { viewLine } = await import(pathToFileURL(join(SRC, "lib", "viewFeed.js")).href);
let fail = 0;
const ok = (c, msg) => { if (!c) { fail++; console.error("FAIL " + msg); } };

ok(viewLine(undefined) === "" && viewLine(null) === "" && viewLine("x") === "",
   "no view = no line");
ok(viewLine({ feed: "odd" }) === "", "unknown feed = no line");
ok(viewLine({ feed: "passthrough", why: "" })
   === "Video: direct from the camera (passthrough)", "passthrough");
ok(viewLine({ feed: "passthrough", why: "waiting for the video gateway" })
   === "Video: direct from the camera (passthrough) - waiting for the video gateway",
   "passthrough waiting");
ok(viewLine({ feed: "legacy", why: "overlay is server" })
   === "Video: through the app (legacy) - overlay is server", "legacy with reason");
ok(viewLine({ feed: "legacy" }) === "Video: through the app (legacy)", "legacy plain");
ok(!viewLine({ feed: "legacy", why: "x" }).includes(String.fromCharCode(0x2014)), "no em dash");

const page = readFileSync(join(SRC, "pages", "Detection.jsx"), "utf8");
ok(/import \{ viewLine \} from "\.\.\/lib\/viewFeed\.js";/.test(page), "page imports viewLine");
ok(/running && viewLine\(activeCam\?\.view\)/.test(page),
   "page shows it for the selected camera, only while running");

if (fail) { console.error(`${fail} failed`); process.exit(1); }
console.log("check_viewfeed: ok");
