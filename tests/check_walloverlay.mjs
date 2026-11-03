// The hub's Live Wall draws a client-mode system's boxes (2026-10-10).
//
// With [video] overlay = "client" the wall showed a plain picture: the boxes
// live on each system's master, the wall is a hub page. The hub now relays
// the events on ONE stream per page, each tagged with its system
// (core/services/hub_overlay.py), and a tile picks its own system + camera.
// Pinned with plain node (no node_modules needed):
//   1. the query a set of tiles asks for: every system once, every tile's
//      camera as <slug>:<id>, "" = the system's first camera
//   2. routing: an envelope reaches only the tile of ITS system and camera;
//      a reset clears only that system's tiles
//   3. source: the wall mounts the canvas only for a local tile of a
//      "client" system, server-mode tiles keep today's markup, and the page
//      still has exactly one place that opens an overlay connection
// tests/check_walloverlaydom.mjs renders the panel itself (needs node_modules).
//
//   node tests/check_walloverlay.mjs
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const lib = await import(pathToFileURL(join(ROOT, "frontend/src/lib/overlay.js")).href);
const { hubFeedKey, hubOverlayQuery, routeHubEvent } = lib;

let bad = 0;
function check(label, ok, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${ok ? "" : `\n      ${detail}`}`);
  if (!ok) bad += 1;
}

// -- 1. the query ------------------------------------------------------------------
const keys = [hubFeedKey("line_a", "cam1"), hubFeedKey("line_a", ""),
              hubFeedKey("dock", "gate 2")].sort();
const q = hubOverlayQuery(keys);
const params = new URLSearchParams(q);
check("every system once, in `slugs`",
      params.get("slugs").split(",").sort().join(",") === "dock,line_a", q);
check("every tile's camera as <slug>:<id>, the first camera as <slug>:",
      params.getAll("camera").sort().join("|") === "dock:gate 2|line_a:|line_a:cam1", q);
check("a key with no camera is the system's first", hubFeedKey("x") === "x\u0000");

// -- 2. routing ---------------------------------------------------------------------
const msg = (cam, first) => ({ v: 1, cam, seq: 1, ...(first ? { first: 1 } : {}) });
check("a message reaches the tile of its system and camera",
      routeHubEvent({ sys: "a", m: msg("cam1") }, "a", "cam1") === "msg");
check("...not the same camera id on ANOTHER system",
      routeHubEvent({ sys: "b", m: msg("cam1") }, "a", "cam1") === null);
check("...not another camera of the same system",
      routeHubEvent({ sys: "a", m: msg("cam2") }, "a", "cam1") === null);
check("a bare tile takes its system's first camera only",
      routeHubEvent({ sys: "a", m: msg("cam1", true) }, "a", "") === "msg"
      && routeHubEvent({ sys: "a", m: msg("cam2") }, "a", "") === null);
check("a reset clears that system's tiles and no other's",
      routeHubEvent({ sys: "a", reset: 1 }, "a", "cam1") === "reset"
      && routeHubEvent({ sys: "a", reset: 1 }, "b", "cam1") === null);
check("garbage routes nowhere",
      routeHubEvent(null, "a", "") === null && routeHubEvent({ sys: "a" }, "a", "") === null);

// -- 3. source ----------------------------------------------------------------------
const wall = readText(join(ROOT, "frontend/src/components/LiveWallPanel.jsx"));
check("the wall mounts the canvas only behind a 'client' answer",
      /overlay=\{ov\s*\?\s*<OverlayCanvas slug=\{s\.slug\} camera=\{cameraOf\(s\)\}[\s\S]*?: undefined\}/.test(wall)
      && wall.includes('return sys?.overlay?.mode === "client" ? sys.overlay : null;'));
check("...for the local AI feed only (a paired camera's feed has no app overlay)",
      wall.includes("if (!s.local) return null;"));
check("...with the system's own delay, as slice 1 applies it",
      wall.includes("delayMs={ov.delay_ms || 0}"));
check("the wall's video still comes straight from each master",
      wall.includes("`${s.url}/api/detector/preview")
      && wall.includes("`${s.url}/video_feed/"));
const canvas = readText(join(ROOT, "frontend/src/components/OverlayCanvas.jsx"));
check("a canvas with a slug listens on the hub's stream, without one on the page's",
      /slug\s*\?\s*subscribeHubOverlay\(slug, cam, onMessage, onReset\)\s*:\s*subscribeOverlay\(cam, onMessage, onReset\)/.test(canvas));
const stream = readText(join(ROOT, "frontend/src/lib/overlayStream.js"));
check("one connection machinery for both, opened only by a subscriber",
      (stream.match(/await fetch\(/g) || []).length === 1
      && stream.includes("export function subscribeHubOverlay")
      && stream.includes("apiUrl(`/api/hub/overlay?${hubOverlayQuery(keys)}`)"));

console.log(`\n${bad ? `${bad} FAILED` : "ALL PASS"}`);
process.exit(bad ? 1 : 0);
