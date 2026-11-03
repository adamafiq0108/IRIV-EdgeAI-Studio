// The Quality panel's single "Ask the camera" gate: the size and rate controls
// stay hidden until it has been pressed, the helper decides what shows, the
// two probes run in order (size, then rates at that size), and the page has
// ONE such button with both selects inside the revealed branch.
//
//   node tests/check_qualityask.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "frontend", "src");
const { qualityView, askBoth } = await import(pathToFileURL(join(SRC, "lib", "qualityAsk.js")).href);
let fail = 0;
const ok = (c, msg) => { if (!c) { fail++; console.error("FAIL " + msg); } };

const cam = { width: 1280, height: 720 };
for (const hiRes of [true, false]) {
  const v = qualityView({ asked: false, hiRes, ...cam });
  ok(!v.showControls && !v.showResolution, `hidden before asking (hiRes=${hiRes})`);
  ok(v.headNote === "" && v.buttonLabel === "Check Camera Capabilities", "no size in header, plain label");
}
let v = qualityView({ asked: true, hiRes: true, ...cam });
ok(v.showControls && v.showResolution && v.headNote === "1280\u00d7720", "revealed with switch on");
ok(v.buttonLabel === "Check Again", "Check Again once revealed");
v = qualityView({ asked: true, hiRes: false, ...cam });
ok(v.showControls && !v.showResolution, "switch off: rate yes, size select no");

// Order and hand-over of the size.
const calls = [];
let r = await askBoth({ hiRes: true,
  probeRes: async () => { calls.push("res"); return [1920, 1080]; },
  probeRates: async (s) => { calls.push("rates:" + s); } });
ok(r === true && calls.join() === "res,rates:1920,1080", "size first, rates at the landed size: " + calls);
calls.length = 0;
r = await askBoth({ hiRes: true, probeRes: async () => { calls.push("res"); return null; },
  probeRates: async () => { calls.push("rates"); } });
ok(r === false && calls.join() === "res", "failed size probe skips rates");
calls.length = 0;
await askBoth({ hiRes: false, probeRes: async () => { calls.push("res"); return [1, 1]; },
  probeRates: async (s) => { calls.push("rates:" + s); } });
ok(calls.join() === "rates:null", "switch off: no size probe, current size");

// The page.
const page = readFileSync(join(SRC, "pages", "Detection.jsx"), "utf8");
ok((page.match(/qv\.buttonLabel/g) || []).length === 1, "one button label use");
ok(!/onClick=\{probeResolutions\}|onClick=\{probeFrameRates\}/.test(page), "old buttons gone");
ok(/onClick=\{askCamera\}/.test(page), "button runs askCamera");
const q = page.slice(page.indexOf("Quality\n"), page.indexOf("The actions, on their own footer row"));
ok((q.match(/Ask the camera/g) || []).length === 1 || (q.match(/>\s*\{askingBoth/g) || []).length === 1,
   "single Ask the camera button in the panel");
const iRes = q.indexOf("qv.showResolution && (");
const iAll = q.indexOf("qv.showControls && (");
ok(iRes > 0 && iAll > iRes, "reveal branches present");
ok(q.indexOf("camera.width}x${camera.height}`}") > iRes, "resolution select inside the revealed branch");
ok(q.indexOf("camera.fps ?? 10") > iAll, "rate select inside the revealed branch");
ok(/setResCaps\(null\); setFpsCaps\(null\); setAsked\(false\);\s*applySource/.test(page),
   "switching camera resets the reveal");
ok(!page.includes(String.fromCharCode(0x2014)) && !readFileSync(join(SRC, "lib", "qualityAsk.js"), "utf8")
   .includes(String.fromCharCode(0x2014)), "no em dash");

// Customer copy pass (2026-10-11): the camera dialog's labels and buttons in
// the spec's words. Buttons that did not exist were not added.
const flat = page.replace(/\s+/g, " ");
ok(flat.includes("Camera IP Address or Stream URL")
   && flat.includes("Enter the camera's IP address or RTSP stream URL."),
   "the address field is named and explained");
ok(flat.includes("Login Credentials") && flat.includes("(Optional)")
   && flat.includes("Leave blank to keep the saved credentials for this camera address."),
   "Login Credentials (Optional), and blank keeps the saved ones for the same address");
ok(flat.includes(">Video Stream<")
   && flat.includes("A lower-resolution sub-stream usually uses less processing power."),
   "Video Stream with the sub-stream trade-off");
ok(flat.includes(">High Resolution<") && flat.includes('aria-label="High Resolution"')
   && flat.includes("Use higher resolution for more image detail. This may increase processing load."),
   "High Resolution, labelled for assistive tech, with its trade-off");
ok(flat.includes("Check the resolutions and frame rates supported by this camera."),
   "the capabilities button says what it checks");
ok(flat.includes("/> Preview Snapshot") && flat.includes("/> Image Adjustments")
   && flat.includes('"Apply Settings"'),
   "Preview Snapshot, Image Adjustments and Apply Settings");

if (fail) { console.error(`${fail} failed`); process.exit(1); }
console.log("check_qualityask: ok");
