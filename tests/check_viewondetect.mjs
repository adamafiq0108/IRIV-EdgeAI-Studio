// "Show video only when an object is detected" ([video] view_on_detect), the
// page side: the state machine in lib/viewOnDetect.js driven with a fake
// clock, when the gate applies at all, and where the pages use it.
//
//   idle -> active (a detection) -> holding (detections stop) -> idle after
//   view_hold_s; a detection during the hold resets it; no video in idle;
//   never on the Detection page; ignored when the overlay is server-drawn.
//
//   node tests/check_viewondetect.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "frontend", "src");
const V = await import(pathToFileURL(join(SRC, "lib", "viewOnDetect.js")).href);
let fail = 0;
const ok = (c, msg, detail = "") => {
  if (c) console.log(`ok   ${msg}`);
  else { fail++; console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); }
};

const DET = { v: 1, cam: "c1", dets: [{ box: [0.1, 0.1, 0.3, 0.4], label: "person" }] };
const NONE = { v: 1, cam: "c1", hud: [["model", "m"]] };

// -- defaults and clamps -----------------------------------------------------------
ok(V.DEFAULT_HOLD_S === 30 && V.MIN_HOLD_S === 2 && V.MAX_HOLD_S === 120,
   "hold: 30 s by default, 2..120");
ok(V.holdSeconds(undefined) === 30 && V.holdSeconds(null) === 30
   && V.holdSeconds("x") === 30 && V.holdSeconds("") === 30, "unreadable hold = 30");
ok(V.holdSeconds(1) === 2 && V.holdSeconds(500) === 120 && V.holdSeconds("12.4") === 12,
   "hold clamped and rounded");
ok(V.PLACEHOLDER === "No object detected -- video starts when one appears",
   "placeholder text");
ok(!V.PLACEHOLDER.includes(String.fromCharCode(0x2014)), "no em dash in the placeholder");

// -- the state machine, fake clock ------------------------------------------------
let t = 1_000_000;
const clock = () => t;
const g = V.createGate(30, clock);
ok(g.state === "idle" && !g.showVideo() && g.deadline() === null,
   "starts idle: no video mounted");
ok(g.message(NONE) === "idle" && !g.showVideo(), "a message without detections stays idle");
ok(g.message(DET) === "active" && g.showVideo(), "a detection: active, video mounted");
t += 120_000;
ok(g.tick() === "active" && g.showVideo(),
   "active never times out on its own (no message = nothing changed)");
ok(g.message(NONE) === "holding" && g.showVideo() && g.deadline() === t + 30_000,
   "detections stop: holding, video still mounted, closes at +30 s");
t += 29_000;
ok(g.tick() === "holding" && g.showVideo(), "29 s into the hold: still showing");
ok(g.message(DET) === "active", "a new detection during the hold: active again");
ok(g.message(NONE) === "holding" && g.deadline() === t + 30_000,
   "...and the hold starts over from the new last detection");
t += 29_999;
ok(g.showVideo(), "just before the new deadline: still showing");
t += 1;
ok(!g.showVideo() && g.state === "idle", "at the deadline: idle, video unmounted");
ok(g.message(NONE) === "idle", "idle stays idle without a detection");
ok(g.message(DET) === "active" && g.reset() === "holding",
   "the overlay stream dropping counts as the detections stopping");
t += 30_000;
ok(g.tick() === "idle", "...and closes after the hold");
const g2 = V.createGate(2, clock);
g2.message(DET); g2.message(NONE);
t += 2_000;
ok(g2.tick() === "idle", "a 2 s hold closes after 2 s");
ok(V.hasDetections({ dets: [] }) === false && V.hasDetections(null) === false
   && V.hasDetections(DET) === true, "hasDetections");

// -- when the gate applies ---------------------------------------------------------
const CLIENT = { mode: "client", delay_ms: 0 };
const on = { overlay: CLIENT, video_saving: { on_demand: false, view_on_detect: true,
                                              view_hold_s: 45 } };
ok(V.gateFor(on).on === true && V.gateFor(on).holdS === 45, "client + view_on_detect: on");
ok(V.gateFor({ ...on, video_saving: { ...on.video_saving, view_hold_s: undefined } })
     .holdS === 30, "...hold 30 s when the answer has none");
ok(V.gateFor(on, { page: "detection" }).on === false, "never on the Detection page");
ok(V.gateFor({ video_saving: on.video_saving }).on === false,
   "ignored when the overlay is drawn on the server (no overlay member)");
ok(V.gateFor({ overlay: CLIENT }).on === false && V.gateFor({}).on === false
   && V.gateFor(null).on === false && V.gateFor(undefined).on === false,
   "off when the switch is off or there is no answer");
ok(V.gateFor({ overlay: CLIENT, video_saving: { on_demand: true } }).on === false,
   "on_demand alone does not gate the page");

// -- where the pages use it --------------------------------------------------------
const read = (rel) => readFileSync(join(SRC, ...rel.split("/")), "utf8");
const feed = read("components/LiveFeed.jsx");
const rtc = read("components/WebRTCVideo.jsx");
const widget = read("components/widgets/Widget.jsx");
const wall = read("components/LiveWallPanel.jsx");
const zones = read("pages/Zones.jsx");
const det = read("pages/Detection.jsx");
const panel = read("components/VideoSavingPanel.jsx");
const gate = read("components/DetectGate.jsx");
ok(/\bdetectGate\b/.test(feed), "the live feed asks for the gate");
ok(/detectGate = false/.test(rtc), "WebRTCVideo gates only when asked (default off)");
ok(!/detectGate/.test(zones) && !/detectGate/.test(det),
   "the Detection page (camera setup and zones) never asks for it");
ok(/const held = paused \|\| gatedIdle;/.test(rtc)
   && /if \(mode !== "webrtc" \|\| held\) return undefined;/.test(rtc),
   "a closed gate opens no WebRTC handshake");
ok(/if \(gatedIdle\) \{\s*return <DetectPlaceholder/.test(rtc),
   "...and mounts no media element, only the placeholder");
ok(/<DetectGate \{\.\.\.gateFor\(data\)\} camera=\{data\.camera \|\| ""\}>/.test(widget),
   "a dashboard video tile is gated from its own data");
ok(/<DetectGate \{\.\.\.gateOf\(s\)\} slug=\{s\.slug\}/.test(wall),
   "a Live Wall tile is gated through the hub relay (slug)");
ok(/subscribeHubOverlay\(slug, cam, onMessage, onReset\)/.test(gate)
   && /subscribeOverlay\(cam, onMessage, onReset\)/.test(gate),
   "the gate listens on the page's shared overlay streams, no new connection");
ok(/<VideoSavingPanel editable=\{editable\}\s+classes=\{status\?\.detector\?\.classes/.test(det)
   && /import VideoSavingPanel from "\.\.\/components\/VideoSavingPanel\.jsx";/.test(det),
   "the Detection page shows the video display switches");
// Customer copy pass (2026-10-11): the on_demand switch is gone from the UI;
// view_on_detect never depended on it (gateFor, checked above).
ok(!panel.includes("<div>Pause video work")
   && !panel.includes('aria-label="Pause video work')
   && !panel.includes("save({ on_demand")
   && panel.includes("Show video only when an object is detected")
   && panel.includes("Display the live video only while an object is detected."),
   "the on_demand switch is gone, Show video only when an object is detected stays");
ok(panel.includes("Keep video visible for")
   && panel.includes("Choose how long the video stays visible after the last detection")
   && panel.includes("{DEFAULT_HOLD_S} seconds by default"),
   "the hold field is Keep video visible for, 30 seconds by default");
ok(panel.includes("serverOverlay && !video.view_on_detect") && panel.includes("reason"),
   "view_on_detect is disabled with the reason in server mode");
// 2026-10-12: Video Visibility is nested under the browser-draw radio, before
// the embed radio, grouped for screen readers; each radio has a one-line use.
{
  const iClient = panel.indexOf('value="client"');
  const iGroup = panel.indexOf("data-video-visibility");
  const iSwitch = panel.indexOf('aria-label="Show video only when an object is detected"');
  const iHold = panel.indexOf("data-view-hold");
  const iServer = panel.indexOf('value="server"');
  ok(iClient > 0 && iClient < iGroup && iGroup < iSwitch && iSwitch < iHold && iHold < iServer,
     "source order: browser radio, nested Video Visibility (switch, hold), embed radio");
  ok(/role="group" aria-labelledby="video-visibility-h"/.test(panel)
     && panel.includes('id="video-visibility-h"'),
     "the nested block is a labelled group (heading kept)");
  ok(panel.includes("Standard accuracy and low CPU usage.")
     && panel.includes("High accuracy, may impact CPU usage.")
     && panel.indexOf("Standard accuracy and low CPU") < iGroup
     && panel.indexOf("High accuracy, may impact") > iServer,
     "each radio carries its box-alignment line");
  ok(panel.includes("Switch to Draw Boxes in the Browser") && panel.includes("data-turn-on-client"),
     "the server-mode reason and switch button are still in the nested block");
}

// -- view_classes: only the chosen classes open the video ------------------------------
{
  const det = (...labels) => ({ v: 1, cam: "c1", dets: labels.map((label) => ({ box: [0, 0, 1, 1], label })) });
  ok(V.viewClassesOf(null) === null && V.viewClassesOf({}) === null
     && V.viewClassesOf({ view_classes: "x" }) === null, "nothing chosen = any class (null)");
  ok(JSON.stringify(V.viewClassesOf({ view_classes: [" A ", "B"] })) === '["A","B"]'
     && V.viewClassesOf({ view_classes: [] }).length === 0, "a list is kept, an empty one too");
  ok(V.hasDetections(det("A"), null) && V.hasDetections(det("zzz")), "with no choice any detection counts");
  ok(V.hasDetections(det("A"), ["A"]) && V.hasDetections(det("a"), ["A"]), "a listed class counts, case ignored");
  ok(!V.hasDetections(det("C"), ["A", "B"]), "an unlisted class alone does not count");
  ok(V.hasDetections(det("C", "B"), ["A", "B"]), "any one listed class among several counts (OR)");
  ok(!V.hasDetections(det("A"), []), "an empty choice means no class counts");
  ok(!V.hasDetections(NONE, ["A"]) && !V.hasDetections(null, ["A"]), "no detections, nothing counts");
  ok(V.gateFor({ video_saving: { view_on_detect: true, view_classes: ["A"] },
                 overlay: { mode: "client" } }).classes[0] === "A", "gateFor hands the classes on");
  ok(V.gateFor({ video_saving: { view_on_detect: true }, overlay: { mode: "client" } }).classes === null,
     "...and null when none were chosen");

  let u = 5_000_000;
  const gc = V.createGate(30, () => u, ["A", "B"]);
  ok(gc.message(det("C")) === "idle" && !gc.showVideo(), "C alone does not open the video");
  ok(gc.message(det("A")) === "active" && gc.showVideo(), "A opens it");
  ok(gc.message(det("C")) === "holding", "then only C: the hold starts, A is gone");
  u += 20_000;
  ok(gc.message(det("C")) === "holding" && gc.deadline() === 5_000_000 + 30_000,
     "C does not extend the hold");
  u += 11_000;
  ok(gc.showVideo() === false, "the video closes view_hold_s after the last matching detection");
  ok(gc.message(det("B")) === "active", "B opens it again");
  ok(gc.changeClasses(["A"]) === "holding", "choosing only A while B is on screen starts the hold at once");
  ok(gc.changeClasses(["A", "B"]) === "active", "ticking B again reopens it from the same picture");
  ok(gc.changeClasses([]) === "holding" && V.createGate(30, () => u, []).message(det("A")) === "idle",
     "none ticked: nothing opens the video");
}
ok(panel.includes("data-view-classes") && panel.includes("Show video when these classes are detected")
   && panel.includes("type=\"checkbox\"") && panel.includes("view_classes"),
   "the panel offers the class checkboxes under the hold time");
ok(!/const known = \[".+"\]|"person"|"car"/.test(panel), "no class name is hard-coded in the panel");
for (const [name, text] of [["viewOnDetect.js", read("lib/viewOnDetect.js")],
                            ["DetectGate.jsx", gate], ["VideoSavingPanel.jsx", panel]]) {
  ok(!text.includes(String.fromCharCode(0x2014)), `no em dash in ${name}`);
}

if (fail) { console.error(`${fail} failed`); process.exit(1); }
console.log("check_viewondetect: ok");
