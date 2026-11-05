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

// -- `change` rows ------------------------------------------------------------
// Every other change a signed-in account makes is written as action `change`
// with the operation's route name in detail.what ("hub.remove_system"), the
// path, the status and what was sent (core/services/change_log.py). Here it
// becomes a sentence: "Removed a system" + "test".

//: Route name -> what the person did. The ones a plain reading of the name
//: gets wrong or leaves vague; everything else goes through changeFallback.
const CHANGES = {
  "auth.change_own_password": "Changed their password",
  "auth.change_own_username": "Changed their username",
  "auth.first_run": "Completed first-time setup",
  "auth.setup_json": "Completed account setup",
  "auth.forgot_json": "Asked for a password reset",
  "auth.reset_json": "Reset a password",
  "auth.reset_request_approve": "Approved a password reset",
  "auth.reset_request_reject": "Rejected a password reset",
  "auth.revoke_session": "Signed out a session",
  "auth.revoke_others": "Signed out other sessions",
  "auth.remove_device": "Removed a sign-in device",
  "auth.remove_other_devices": "Removed other sign-in devices",
  "auth.session_policy": "Changed automatic sign-out",
  "auth.panel_reset": "Reset the front panel password",
  "blynk.save": "Changed Blynk settings",
  "blynk.test": "Tested Blynk",
  "board.io_test": "Tested a board output",
  "boxrestart.restart": "Restarted the box",
  "builder.asset_upload": "Uploaded a dashboard picture",
  "builder.save": "Saved a dashboard",
  "builder.publish": "Published a dashboard",
  "control.action": "Ran a control action",
  "control.apply": "Applied control settings",
  "datasets.add_row": "Added a table row",
  "datasets.edit_row": "Edited a table row",
  "datasets.delete_rows": "Deleted table rows",
  "datasets.save": "Changed the tables",
  "datasets.rename": "Renamed the database",
  "datasets.query_save": "Saved a query",
  "datasets.query_delete": "Deleted a saved query",
  "detector.add_camera": "Added a camera",
  "detector.remove_camera": "Removed a camera",
  "detector.set_camera": "Changed a camera",
  "detector.set_cameras": "Changed the cameras",
  "detector.enable_camera": "Switched a camera on or off",
  "detector.set_camera_controls": "Changed camera picture settings",
  "detector.camera_sdk": "Installed a camera SDK",
  "detector.set_zones": "Changed the zones",
  "detector.zones_editing": "Edited the zones",
  "detector.set_params": "Changed detection settings",
  "detector.set_tracker": "Changed tracking settings",
  "detector.set_capture": "Changed capture settings",
  "detector.set_video": "Changed video settings",
  "detector.set_hud": "Changed the video overlay",
  "detector.set_overlay": "Changed the video overlay",
  "detector.set_outputs": "Changed the outputs",
  "detector.set_modbus": "Changed Modbus settings",
  "detector.save_modbus_client": "Saved a Modbus device",
  "detector.delete_modbus_client": "Removed a Modbus device",
  "detector.power": "Started or stopped detection",
  "detector.set_tutorial": "Changed the tutorial",
  "detector.import_db": "Imported a database",
  "detector.dataset_record": "Started dataset recording",
  "detector.dataset_record_stop": "Stopped dataset recording",
  "detector.dataset_snap": "Took a dataset photo",
  "detector.dataset_delete": "Deleted dataset photos",
  "detector.dataset_repair": "Repaired the dataset",
  "detector.snapshot_post": "Took a snapshot",
  "detector.snapshot_delete": "Deleted a snapshot",
  "detector.snapshots_delete": "Deleted snapshots",
  "detector.clips_delete": "Deleted clips",
  "detector.footage_email": "Emailed footage",
  "detector.footage_schedule_put": "Changed the footage email schedule",
  "detector.measure_calibrate": "Calibrated measurement",
  "detector.measure_clear": "Cleared the measurement calibration",
  "detector.measure_profiles": "Changed measurement profiles",
  "detector.measure_run": "Ran a measurement",
  "detector.webrtc_whep": "Opened a live view",
  "detector.webrtc_session_delete": "Closed a live view",
  "devices.update": "Changed a device",
  "email.save": "Changed email settings",
  "email.send": "Sent an email",
  "email.send_cancel": "Cancelled an email",
  "events.clear": "Cleared the events",
  "events.update": "Changed an event",
  "folders.assign": "Moved a device to a folder",
  "folders.create": "Created a folder",
  "folders.update": "Changed a folder",
  "folders.delete": "Deleted a folder",
  "functions.save": "Saved the functions",
  "hostname.save": "Changed the device name",
  "https.save": "Changed HTTPS settings",
  "https.regenerate": "Made a new HTTPS certificate",
  "hub.autostart": "Changed a system's autostart",
  "hub.capture": "Changed a system's capture",
  "hub.create_system": "Created a system",
  "hub.import_system": "Imported a system",
  "hub.remove_system": "Removed a system",
  "hub.rename_system": "Renamed a system",
  "hub.remove_orphan": "Removed a leftover system",
  "hub.purge_system_data": "Deleted a system's data",
  "hub.detection": "Started or stopped a system's detection",
  "hub.power": "Started or stopped a system",
  "hub.image_upload": "Changed a system's picture",
  "hub.image_delete": "Removed a system's picture",
  "hub.save_template": "Saved a system as a template",
  "hub.rename_user_template": "Renamed a template",
  "hub.delete_user_template": "Deleted a template",
  "hub.set_board_release": "Changed which board terminals are shared",
  "hub.set_nodered": "Switched Node-RED on or off",
  "hub.set_retention": "Changed data retention",
  "hubport.save": "Changed the dashboard port",
  "hubport.test": "Tested the dashboard port",
  "inbox.delete": "Deleted a message",
  "logs.clear": "Cleared the log",
  "logs.purge": "Cleared the log",
  "logs.level": "Changed the log detail level",
  "mail.save_account": "Saved a mailbox",
  "mail.delete_account": "Removed a mailbox",
  "mail.set_default": "Changed the default mailbox",
  "mail.test_account": "Tested a mailbox",
  "media.upload": "Uploaded a file",
  "media.delete": "Deleted a file",
  "models.upload": "Uploaded an AI model",
  "models.local_upload": "Uploaded an AI model",
  "models.delete": "Deleted an AI model",
  "models.local_delete": "Deleted an AI model",
  "models.deploy": "Deployed an AI model",
  "models.save": "Changed AI model settings",
  "models.builtin_fetch": "Downloaded a built-in AI model",
  "models.builtin_select": "Chose a built-in AI model",
  "models.builtin_select_many": "Chose built-in AI models",
  "models.local_select": "Chose an AI model",
  "monitor.set": "Changed the monitor account",
  "monitor.pages": "Changed the monitor pages",
  "monitor.systems": "Changed the monitor systems",
  "monitor.token_make": "Made a monitor link",
  "monitor.token_revoke": "Revoked a monitor link",
  "monitor.token_login": "Opened a monitor link",
  "mqtt_broker.save": "Changed MQTT broker settings",
  "mqtt_broker.install": "Installed the MQTT broker",
  "mqtt_broker.auto": "Changed MQTT broker auto-install",
  "mqtt_clients.save": "Saved an MQTT connection",
  "mqtt_clients.delete": "Removed an MQTT connection",
  "network.ap": "Switched the hotspot on or off",
  "network.ap_save": "Changed the hotspot",
  "network.join": "Joined a Wi-Fi network",
  "network.forget": "Forgot a Wi-Fi network",
  "network.radio": "Switched Wi-Fi on or off",
  "network.reset": "Reset Wi-Fi",
  "network.ipv4": "Changed a network address",
  "network.ipv4_keep": "Kept the new network address",
  "network.ipv4_revert": "Reverted the network address",
  "network.ipv4_test": "Tested a network address",
  "network.wifi_test": "Tested Wi-Fi",
  "panel.save": "Changed the front panel",
  "panel.page": "Stepped the front panel page",
  "panel.beep": "Sounded the buzzer",
  "proxy.footage_delete": "Deleted footage",
  "remote.save": "Changed remote access settings",
  "remote.tunnel_start": "Started remote access",
  "remote.tunnel_stop": "Stopped remote access",
  "roles.create": "Created a role",
  "roles.delete": "Deleted a role",
  "settings.add": "Added a setting",
  "settings.remove": "Removed a setting",
  "storage.forget_nvme": "Forgot the NVMe drive",
  "storage_setup.setup": "Set up storage",
  "storage_setup.decline": "Declined storage setup",
  "system.settings_save": "Changed system settings",
  "telegram.save_bot": "Saved a Telegram bot",
  "telegram.delete_bot": "Removed a Telegram bot",
  "telegram.set_default": "Changed the default Telegram bot",
  "telegram.test_bot": "Tested a Telegram bot",
  "telegram.unblock": "Unblocked a Telegram chat",
  "telegram.unlink": "Unlinked a Telegram chat",
  "update.upload": "Uploaded a software update",
  "update.install": "Installed a software update",
  "update.discard": "Discarded a software update",
  "update.rollback": "Went back to the previous version",
  "users.create": "Created a user",
  "users.update": "Changed a user",
  "users.delete": "Deleted a user",
  "users.reset_password": "Reset a user's password",
  "workflow.save": "Saved the workflow",
  "workflow.compile": "Applied the workflow",
  "workflow.card_style": "Changed a workflow card",
  "workflow.class_filter": "Changed the class filter",
  "workflow.csv_file": "Uploaded a workflow CSV file",
  "workflow.inject": "Triggered a workflow node",
  "workflow.test_output": "Tested a workflow output",
  "workflow.mqtt_test_publish": "Sent a test MQTT message",
  "workflow.mqtt_test_listen": "Listened for MQTT messages",
  "workflow.mqtt_test_stop": "Stopped listening for MQTT messages",
};

//: The first word of a route name -> the verb a person reads.
const VERBS = {
  add: "Added", create: "Created", new: "Created", save: "Saved", set: "Changed",
  update: "Changed", edit: "Edited", rename: "Renamed", remove: "Removed",
  delete: "Deleted", clear: "Cleared", purge: "Cleared", upload: "Uploaded",
  import: "Imported", install: "Installed", start: "Started", stop: "Stopped",
  restart: "Restarted", reset: "Reset", test: "Tested", publish: "Published",
  deploy: "Deployed", enable: "Enabled", disable: "Disabled", apply: "Applied",
  select: "Chose", approve: "Approved", reject: "Rejected", revoke: "Revoked",
  send: "Sent", fetch: "Downloaded",
};

/** A route name with no hand-written sentence, read as one:
 *  "foo.remove_widget" -> "Removed widget"; failing that, by the method. */
function changeFallback(what, method) {
  const name = String(what || "").split(".").pop() || "";
  const words = name.split("_").filter(Boolean);
  if (words.length && VERBS[words[0]]) {
    return [VERBS[words[0]], ...words.slice(1)].join(" ") || VERBS[words[0]];
  }
  const m = String(method || "").toUpperCase();
  const verb = m === "DELETE" ? "Removed" : m === "POST" ? "Saved" : "Changed";
  return words.length ? `${verb} ${words.join(" ")}` : `${verb} a setting`;
}

/** The sentence for a `change` row. */
function changeLabel(entry) {
  const d = entry?.detail || {};
  return CHANGES[d.what] || changeFallback(d.what, d.method);
}

//: Path words that name a KIND of thing, never the thing itself.
const GENERIC = new Set(["api", "hub", "systems", "system", "users", "roles", "detector", "datasets",
  "rows", "delete", "save", "settings", "update", "upload", "install", "rollback", "discard",
  "models", "local", "builtin", "network", "mail", "accounts", "telegram", "bots", "monitor",
  "media", "folders", "devices", "auth", "board", "panel", "workflow", "events", "logs",
  "cameras", "camera", "zones", "control", "remote", "storage", "mqtt", "clients", "default",
  "test", "image", "rename", "power", "autostart", "detection", "capture", "page", "beep", "port"]);

//: Path words followed by the NAME of one of them.
const COLLECTIONS = new Set(["systems", "users", "roles", "cameras", "folders", "accounts", "bots",
  "templates", "clients", "devices", "queries", "sessions", "snapshots", "images"]);

/** What the change was made TO, for the line beside the sentence: the named
 *  thing in the path (a system, a user, a camera), else a short list of the
 *  settings that were sent. Never the raw method, path or status. */
function changeDetail(entry, nameOf) {
  const d = entry?.detail || {};
  const parts = String(d.path || "").split("/").filter(Boolean).map(decodeURIComponent);
  // The segment after a collection word is the thing ("/systems/test" ->
  // "test", even when it is called "test"); failing that, the last word in
  // the path that does not name a kind of thing.
  // A segment that is a word of the operation's own name ("test" in
  // mail.test_account at /accounts/test) is the action, not a name.
  const own = new Set(String(d.what || "").split(".").pop().split("_"));
  let target = "";
  for (let i = parts.length - 2; i >= 0 && !target; i -= 1) {
    if (COLLECTIONS.has(parts[i].toLowerCase()) && !own.has(parts[i + 1].toLowerCase())) {
      target = parts[i + 1];
    }
  }
  if (!target) {
    target = [...parts].reverse().find((p) => !GENERIC.has(p.toLowerCase()) && !own.has(p.toLowerCase())
                                               && !/^\d+$/.test(p)) || "";
  }
  const bits = [];
  if (target) bits.push(`"${parts.includes("systems") ? nameOf(target) : target}"`);
  const sent = d.sent && typeof d.sent === "object" && !Array.isArray(d.sent) ? Object.keys(d.sent) : [];
  const shown = sent.filter((k) => k !== "...").slice(0, 4);
  if (shown.length) {
    const words = shown.map((k) => k.replace(/_/g, " "));
    bits.push(`changed ${words.join(", ")}${sent.length > shown.length ? " and more" : ""}`);
  }
  if (d.upload_bytes) bits.push(`${Math.max(1, Math.round(d.upload_bytes / 1024))} KB uploaded`);
  return bits.join(" · ");
}

/** The name an audit row goes by. Always a string; never throws, whatever
 *  shape the row arrives in. */
export function actionLabel(entry) {
  const action = String(entry?.action || "");
  if (action === "login" && entry?.result && entry.result !== "ok") {
    return "Sign-in failed";
  }
  if (action === "change") return changeLabel(entry);
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
  if (action === "change") return changeDetail(entry, nameOf);
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
