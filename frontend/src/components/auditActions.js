// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The audit log's `action`, in the words a customer reads it in.
//
// The server writes dotted ids ("system.settings", "session.revoke_others")
// and keeps writing them: they are what the Audit page filters on and what a
// developer greps for. The Accounts > Activity Log is read by the people who
// run the box, and an id there is a word they have to translate. So the
// mapping lives HERE, in the browser, and never changes what is stored.
//
// Two of the spec's events are not actions of their own. The Hailo balancer
// and the shared poll cache switches are both written as `system.settings`
// with the detail "hailo load balancer on|off" / "shared poll cache on|off"
// (core/routes/auth.py, system_settings_save), so they are told apart by that
// detail. A failed sign-in is `login` with result "fail".
//
// Nothing here hides a row. Every action the server records today is one a
// person took on purpose (none is a rendering or processing event), so the
// customer view shows all of them, named; a developer additionally sees the
// raw id beside the name.
//
// No imports beyond logFormat.js (itself import-free), so a plain `node`
// script can test it (tests/check_activity.mjs), the way auditDetail.js is.
import { asDate } from "./logFormat.js";

//: Plain action -> name. Anything not listed falls back to actionFallback.
const NAMES = {
  "login": "Signed in",
  "logout": "Signed out",
  "account.rename": "Username changed",
  "password_reset": "Password reset",
  "password_reset_link": "Password reset link sent",
  "password_reset_requested": "Password reset requested",
  "password_reset_approved": "Password reset approved",
  "password_reset_rejected": "Password reset rejected",
  "session.revoke": "Session signed out",
  "session.revoke_others": "Other sessions signed out",
  "session.policy": "Automatic sign-out changed",
  // Registered sign-in devices (Accounts > Login Sessions). A browser an
  // account signs in from, not a camera or a paired board -- the detail
  // names the browser and system ("Chrome · Windows").
  "device.registered": "Device registered",
  "device.removed": "Device removed",
  "device.remove_others": "Other devices removed",
  "device.expired": "Device expired (inactive for 30 days)",
  "device.refused": "Sign-in refused (device limit reached)",
  "system.settings": "System setting changed",
  // The Detection page's "Draw boxes" choice (core/routes/video_saving.py):
  // a person picking where the live boxes are drawn, which every viewer of
  // that system then sees. A customer setting, so it is shown.
  "video.overlay": "Video box drawing changed",
  // Neutral on purpose: the outcome ("installed" / "failed") is in the
  // detail, and the row's result stays "ok" either way.
  "camera.sdk": "Camera SDK install",
  "device.rename": "Device renamed",
  "device.pair": "Device paired",
  "device.forget": "Device removed",
  "device.move": "Device moved",
  "folder.create": "Folder created",
  "folder.rename": "Folder renamed",
  "folder.move": "Folder moved",
  "folder.delete": "Folder deleted",
  "hub.hostname": "Device name changed",
  "hub.port": "Dashboard port changed",
  "hub.restart": "Device restarted",
  "mqtt.broker_settings": "MQTT broker settings changed",
  "mqtt.broker_install": "MQTT broker installed",
  "remote.settings": "Remote access settings changed",
  "remote.tunnel_start": "Remote access started",
  "remote.tunnel_stop": "Remote access stopped",
  "storage.setup": "Storage setup",
  "storage.decline": "Storage setup declined",
};

//: Switches whose on/off lands in a `system.settings` / `session.policy`
//: detail. The detail is the server's own words, matched exactly; the name
//: already says everything it did, so the detail is not repeated beside it.
const SWITCHES = {
  "hailo load balancer": "AI Processing Balance",
  "shared poll cache": "Shared Dashboard Cache",
  "email": "Email",
  "auto logout": "Automatic Sign-out",
};

function switchOf(entry) {
  const m = /^(.+) (on|off)$/.exec(String(entry?.detail || "").trim());
  if (!m || !SWITCHES[m[1]]) return null;
  return `${SWITCHES[m[1]]} ${m[2] === "on" ? "enabled" : "disabled"}`;
}

/** An id this page has no name for, made readable: "storage.nvme_thing" ->
 *  "Storage nvme thing". Never empty, never the raw dotted id. */
export function actionFallback(action) {
  const words = String(action || "").split(/[._\s-]+/).filter(Boolean).join(" ");
  if (!words) return "Activity";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The name an audit row goes by. Always a string; never throws, whatever
 *  shape the row arrives in. */
export function actionLabel(entry) {
  const action = String(entry?.action || "");
  if (action === "login" && entry?.result && entry.result !== "ok") {
    return "Sign-in failed";
  }
  if (action === "system.settings" || action === "session.policy") {
    const named = switchOf(entry);
    if (named) return named;
  }
  return NAMES[action] || actionFallback(action);
}

/** The detail beside the name, for the customer view. `text` is the row's
 *  detail already through detailText; `nameOf` maps a system slug to the
 *  name it goes by now. "" when the name has said it all. */
export function actionDetail(entry, text, nameOf = (s) => s) {
  const action = String(entry?.action || "");
  if ((action === "system.settings" || action === "session.policy")
      && switchOf(entry)) return "";
  if (action === "video.overlay") {
    // "<slug>: boxes drawn client -> server", as Detection Box Display reads.
    const m = /^(.*): boxes drawn (\w+) -> (\w+)$/.exec(String(text || ""));
    const where = { client: "Draw boxes in the browser",
                    server: "Embed boxes in the video" };
    if (m) {
      return `${nameOf(m[1])}: ${where[m[2]] || m[2]} -> ${where[m[3]] || m[3]}`;
    }
  }
  return String(text || "");
}

/** A row's outcome when it is not "ok", as a word: fail -> "Failed". */
export function resultLabel(result) {
  const r = String(result || "");
  if (!r || r === "ok") return "";
  if (r === "fail") return "Failed";
  return r.charAt(0).toUpperCase() + r.slice(1);
}

/** "11 Oct 2026, 11:52 AM" in the reader's own locale and time zone. An
 *  unreadable stamp comes back as it was rather than "Invalid Date". */
export function whenText(ts) {
  const d = asDate(ts);
  if (!d) return String(ts || "-").slice(0, 19).replace("T", " ") || "-";
  return d.toLocaleString(undefined, {
    day: "numeric", month: "short", year: "numeric",
    hour: "numeric", minute: "2-digit",
  });
}
