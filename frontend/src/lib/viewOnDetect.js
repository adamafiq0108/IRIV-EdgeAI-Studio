// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// viewOnDetect.js - "Show video only when an object is detected" ([video]
// view_on_detect), the part that decides.
//
// A live tile on a system with this switch on does not open its video
// (WebRTC or MJPEG) until the AI reports a detection on that camera, shows a
// placeholder meanwhile, and closes the video view_hold_s seconds (30 by
// default) after the detections stop. With passthrough an idle scene then
// costs the box nothing at all: the gateway pulls the camera only while a
// reader exists. With the legacy view it works together with on_demand.
//
// WHERE THE DETECTIONS COME FROM. The overlay messages the page already
// receives in [video] overlay = "client" mode (systems/demo/app/
// overlay_feed.py): `dets` is the boxes of the camera's latest inference.
// No new request, no new poll. In "server" mode there are no messages, so
// the switch is IGNORED there (the Detection page says why) - the video is
// shown as always rather than never.
//
// THE RULE, per camera:
//   idle     no video, the placeholder. A message with dets -> active.
//   active   video open. A message WITHOUT dets (or the stream dropping) ->
//            holding. The app sends a message only when something CHANGED,
//            so "no message" while active means "the same boxes are still
//            there": active never times out on its own.
//   holding  video still open; `holdS` seconds after it began -> idle. A
//            message with dets on the way -> active again (the hold resets).
//
// Never applied on the Detection page: zones are drawn on a picture, and
// detection itself is never gated by any of this.
//
// Pure on purpose - no React, an injected clock - so tests/check_viewondetect.mjs
// drives it in plain node. The hook and the placeholder live in
// components/DetectGate.jsx.

export const DEFAULT_HOLD_S = 30;
export const MIN_HOLD_S = 2;
export const MAX_HOLD_S = 120;

//: What the tile says while it waits. The double hyphen is deliberate (no em
//: dash anywhere in this product).
export const PLACEHOLDER = "No object detected -- video starts when one appears";

/** view_hold_s clamped like the master and the app clamp it. */
export function holdSeconds(value) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n) || value === null || value === "" || value === undefined) {
    return DEFAULT_HOLD_S;
  }
  return Math.min(MAX_HOLD_S, Math.max(MIN_HOLD_S, n));
}

/**
 * Does the gate apply to this feed? `info` is a payload that already carries
 * the overlay mode (the /api/detector/webrtc answer, a video tile's data, a
 * /api/hub/streams system): `overlay` {mode: "client"} and, only while a
 * switch is on, `video_saving` {view_on_detect, view_hold_s}.
 * Returns { on, holdS }. `page: "detection"` is always off.
 */
export function gateFor(info, { page } = {}) {
  const saving = info && typeof info === "object" ? info.video_saving : null;
  const on = Boolean(saving && saving.view_on_detect === true
                     && info.overlay && info.overlay.mode === "client"
                     && page !== "detection");
  return { on, holdS: holdSeconds(saving ? saving.view_hold_s : undefined),
           classes: viewClassesOf(saving) };
}

/** The classes that open the video: null = any class (nothing was chosen),
 *  an array = only those (an empty one = none, so the video stays closed). */
export function viewClassesOf(saving) {
  const list = saving ? saving.view_classes : null;
  return Array.isArray(list) ? list.map((c) => String(c).trim()) : null;
}

const norm = (v) => String(v == null ? "" : v).trim().toLowerCase();

/** True when an overlay message reports a detection that counts: any
 *  detection when `classes` is null, otherwise one whose label is listed. A
 *  detection of an unlisted class is as good as none. */
export function hasDetections(msg, classes = null) {
  if (!msg || !Array.isArray(msg.dets) || msg.dets.length === 0) return false;
  if (!Array.isArray(classes)) return true;
  if (classes.length === 0) return false;
  const want = new Set(classes.map(norm));
  return msg.dets.some((d) => want.has(norm(d && (d.label ?? d.class ?? d.name))));
}

/**
 * One camera's gate. `now()` is the clock in milliseconds (Date.now by
 * default). message(msg) / reset() / tick() each return the state after.
 */
export function createGate(holdS = DEFAULT_HOLD_S, now = () => Date.now(),
                           classes = null) {
  const holdMs = holdSeconds(holdS) * 1000;
  let state = "idle";
  let since = 0;                      // when holding began
  let want = Array.isArray(classes) ? classes : null;
  let last = null;                    // the latest message, to re-judge on a change

  function tick() {
    if (state === "holding" && now() - since >= holdMs) state = "idle";
    return state;
  }

  function stopSeeing() {
    tick();
    if (state === "active") { state = "holding"; since = now(); }
    return state;
  }

  return {
    get state() { return state; },
    message(msg) {
      last = msg;
      if (hasDetections(msg, want)) { state = "active"; return state; }
      return stopSeeing();
    },
    /** The chosen classes changed: judge the latest message again, so what is
     *  on screen now counts under the new choice at once (the app sends a
     *  message only when something changed, so waiting for the next one
     *  could be for ever). */
    changeClasses(next) {
      want = Array.isArray(next) ? next : null;
      return last ? this.message(last) : state;
    },
    /** The overlay stream dropped: nothing is known to be there any more. */
    reset() { return stopSeeing(); },
    tick,
    /** When holding ends (ms on the same clock), or null when not holding. */
    deadline() { return state === "holding" ? since + holdMs : null; },
    /** Is the video mounted in this state? */
    showVideo() { tick(); return state !== "idle"; },
  };
}
