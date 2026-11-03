// The RTSP Stream choice: presets, the custom path and its refusals.
//
// Hikvision-style cameras serve Streaming/Channels/101 (main) and /102 (sub);
// /stream2 on them is the MAIN picture, so a form with only stream1/stream2
// decoded 2560x1440 without anyone knowing. This pins the pure module and that
// the Detection page really uses it.
//
//   node tests/check_rtspstream.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "frontend", "src");
const m = await import(pathToFileURL(join(SRC, "lib", "rtspStream.js")).href);
let fail = 0;
const ok = (c, msg) => { if (!c) { fail++; console.error("FAIL " + msg); } };

const vals = m.RTSP_PRESETS.map((p) => p.value);
ok(JSON.stringify(vals) === JSON.stringify([
  "stream1", "stream2", "Streaming/Channels/101", "Streaming/Channels/102",
  "cam/realmonitor?channel=1&subtype=0", "cam/realmonitor?channel=1&subtype=1",
]), "preset values " + vals);
ok(m.RTSP_PRESETS.every((p) => p.label && !p.value.startsWith("/")), "labels set, no leading slash");
ok(m.isPreset("Streaming/Channels/102") && m.isPreset("stream1"), "isPreset true");
ok(!m.isPreset("Streaming/Channels/201") && !m.isPreset("") && !m.isPreset("/stream2"), "isPreset false");

const n = m.normalizeCustomPath;
const good = (inp, want) => { const r = n(inp); ok(r.ok && r.path === want, `${JSON.stringify(inp)} -> ${r.path} (${r.error})`); };
const bad = (inp) => { const r = n(inp); ok(!r.ok && r.error, `${JSON.stringify(inp)} must be refused`); };
good("Streaming/Channels/102", "Streaming/Channels/102");
good("/stream2", "stream2");
good("  /Streaming/Channels/201  ", "Streaming/Channels/201");
good("cam/realmonitor?channel=1&subtype=1", "cam/realmonitor?channel=1&subtype=1");
good("live/ch_0-1.sdp", "live/ch_0-1.sdp");
good("", "stream1");
good("   ", "stream1");
good(undefined, "stream1");
bad("a b"); bad("x@y"); bad("a#b"); bad("..");  bad("a/../b"); bad("a\tb");

// select <-> text: a preset fills the box (the box holds its value), typing
// selects the matching preset, anything else (also empty/invalid) is Custom.
const sv = m.selectValueFor;
for (const v of vals) ok(sv(v) === v, "preset " + v + " selects itself");
ok(sv("/Streaming/Channels/102 ") === "Streaming/Channels/102", "typed with slash/space matches preset");
ok(sv("Streaming/Channels/201") === m.RTSP_CUSTOM, "unknown path -> custom");
ok(sv("") === m.RTSP_CUSTOM && sv("a b") === m.RTSP_CUSTOM, "empty/invalid -> custom");

const det = readFileSync(join(SRC, "pages", "Detection.jsx"), "utf8");
ok(/from "\.\.\/lib\/rtspStream\.js"/.test(det), "Detection imports rtspStream");
ok(/RTSP_PRESETS\.map/.test(det), "select is built from the presets");
ok(!/<option value="stream2">/.test(det), "no hard-coded stream2 option");
ok(/Custom \(type below\)/.test(det) && /Stream path \(or type your own\)/.test(det), "custom option and always-visible box");

if (fail) { console.error(fail + " failure(s)"); process.exit(1); }
console.log("rtspstream ok");
