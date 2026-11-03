// A camera failure reads as what happened and the one thing to do.
//
// UAT found the Camera Setup card printing the backend's own sentence --
// "cannot open camera '0'", a raw "HTTP 409" under "Preview snapshot" -- to
// operators who cannot act on either. camErrors.js maps every phrase the app
// and core write (tests/smoke_camcause.py pins that end) to a plain headline
// and a verb-first fix, and keeps the raw text for "Details". Pinned:
//   1. each backend phrase, old and new, lands on its own cause
//   2. a specific cause wins over the "gave no frames" wrapper around it
//   3. no headline or fix carries an em dash, a status code or developer
//      words, and every quoted button is a real on-screen label
//   4. the RTSP password never reaches the details
//   5. a bare HTTP status / dead board reads as the BOARD's problem
//   6. an unknown failure still says what to try; a toast keeps a server
//      sentence that was already written for a person
//
//     node tests/test_camerrors.mjs
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");
let bad = 0;

function check(label, ok, detail = "") {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : "\n      " + detail));
  if (!ok) bad += 1;
}

const { explainCamError, camErrorText, camErrorToast, maskCredentials } =
  await import("file://" + join(REPO, "frontend", "src", "lib", "camErrors.js"));

const SRC = "rtsp://admin:***@192.168.1.50:554/live";
const CASES = [
  // [raw text as the backend writes it, expected cause]
  ["reconnecting camera (2/3)…", "reconnecting"],
  ["no camera chosen - pick one on the Camera Setup page", "no_source"],
  ["CSI (libcamera) cameras are not supported on this hardware - re-add", "csi_unsupported"],
  ["opencv-python is not installed on this host; cannot open or preview a camera", "software_missing"],
  ["the Orbbec SDK is not installed on this box - pip install --no-deps pyorbbecsdk2", "depth_software_missing"],
  ["no Orbbec camera found on this box", "depth_not_found"],
  ["orbbec: device busy", "depth_failed"],
  ["camera '0' is held by another system on this box (pid 812) and its shared feed did not answer", "busy_system"],
  ["camera '0' is owned by another system; consuming its shared feed http://127.0.0.1:5102/raw", "shared"],
  ["cannot open camera '0': in use by another program (ffmpeg, pid 901)", "busy_program"],
  ["cannot open camera '0': in use by this system's own detection", "busy_self"],
  ["camera 'gige' is controlled by another application - a GigE camera has ONE controller", "gige_controlled"],
  ["camera 'gige': no GigE camera answered on the network", "gige_none"],
  ["cannot open camera '0': no device at /dev/video0 - not plugged in", "usb_unplugged"],
  ["cannot open camera '0': permission denied on /dev/video0", "usb_permission"],
  [`camera '${SRC}' did not open: needs a username and password (RTSP 401)`, "auth_missing"],
  [`camera '${SRC}' did not open: username or password rejected (RTSP 401)`, "auth_rejected"],
  [`camera '${SRC}' did not open: access refused for this user (RTSP 403)`, "forbidden"],
  [`camera '${SRC}' did not open: stream path not found (RTSP 404)`, "bad_path"],
  [`camera '${SRC}' did not open: answered, but its video could not be played (RTSP 200)`, "unplayable"],
  [`camera '${SRC}' did not open: host cam.local not found`, "net_dns"],
  [`camera '${SRC}' did not open: 192.168.1.50:554 refused the connection`, "net_refused"],
  [`camera '${SRC}' did not open: no answer from 192.168.1.50:554`, "net_unreachable"],
  [`camera '${SRC}' did not open: answered with status 503 (RTSP)`, "net_odd_reply"],
  [`camera '${SRC}' did not answer - check the address, the credentials and the network path to it`, "net_failed"],
  ["camera opened but produced no frame", "no_picture"],
  ["cannot read this file", "file_unreadable"],
  ["camera read failed", "stalled"],
  ["camera disconnected: '0' gave no frames after 3 reconnect attempts (camera read failed)", "stalled"],
  ["cannot open camera 'gige:Basler-1'", "gige_failed"],
  ["cannot open camera '0'", "usb_failed"],
  ["cannot open camera 'x.mp4' - it may be in use by the running detector, absent, or an unreachable URL.", "open_failed"],
];

// -- 1. every phrase -----------------------------------------------------------
for (const [raw, code] of CASES) {
  const x = explainCamError(raw);
  check(`${code}: ${raw.slice(0, 60)}`, x?.code === code && x.known, JSON.stringify(x));
}

// -- 2. the cause inside the wrapper wins --------------------------------------
const wrapped = explainCamError("camera disconnected: '0' gave no frames after 3 "
  + "reconnect attempts (cannot open camera '0': no device at /dev/video0 - not plugged in)");
check("an unplugged camera inside the disconnect wrapper reads as unplugged",
      wrapped.code === "usb_unplugged", wrapped.code);
check("the host is named in the headline, the port is not",
      explainCamError(CASES.find((c) => c[1] === "net_unreachable")[0]).title
        === "Can't reach 192.168.1.50.");

// -- 3. wording rules ---------------------------------------------------------
const JARGON = /\u2014|\bHTTP\b|\b(401|403|404|409|500|503)\b|RTSP|\bECONN|errno|timeout|socket|codec|V4L2|\/dev\/|exception|pid\b|stream handle|pill/i;
// Button names as the camera dialog prints them since the 2026-10-11 copy pass.
const LABELS = new Set(["Reconnect", "Preview Snapshot", "Apply Settings", "Camera settings",
                        "High Resolution", "Details"]);
for (const [raw, code] of CASES) {
  for (const retry of ["Reconnect", "Preview Snapshot", ""]) {
    const x = explainCamError(raw, { retry });
    const text = `${x.title} ${x.fix}`;
    if (JARGON.test(text)) check(`${code}: plain words only`, false, text);
    const quoted = [...text.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    const stray = quoted.filter((q) => !LABELS.has(q));
    if (stray.length) check(`${code}: quotes only real buttons`, false, stray.join(", "));
    if (!/^[A-Z]/.test(x.fix)) check(`${code}: the fix starts a sentence`, false, x.fix);
  }
}
check("headlines and fixes pass the wording rules (failures listed above)", true);
check("the retry button is the one beside the message",
      explainCamError("camera read failed", { retry: "Preview Snapshot" }).fix
        .includes('"Preview Snapshot"'));
// Yaya: System Control's tooltip, the settings dialog's toasts and the
// resolution/fps reads told people to press "Reconnect", which none of them
// has. With no button named, the fix names none.
for (const [raw, code] of CASES) {
  const fix = explainCamError(raw).fix;
  if (/"(Reconnect|Preview Snapshot)"/.test(fix)) check(`${code}: no button named by default`, false, fix);
}
check("without a retry button the fix names no button (failures listed above)", true);
check("...and says to try again instead",
      explainCamError("camera read failed").fix === "Check the cable and power, then try again."
      && explainCamError("camera opened but produced no frame").fix.startsWith("Try again."));

// -- 4. no password in the details -------------------------------------------
const leak = explainCamError("camera 'rtsp://admin:f5f4f3@10.0.0.5/live' did not answer - check the address");
check("an older app's raw URL is masked in the details",
      !leak.detail.includes("f5f4f3") && leak.detail.includes("admin:***@"), leak.detail);
check("masking leaves a URL without credentials alone",
      maskCredentials("rtsp://10.0.0.5/live") === "rtsp://10.0.0.5/live");

// -- 5. the board, not the camera ---------------------------------------------
check("a bare status from the preview reads as access",
      explainCamError("HTTP 403").code === "no_access");
check("...a server failure as the board's",
      explainCamError({ message: "Internal Server Error", status: 500 }).code === "board_failed");
check("...a dead board as unreachable",
      explainCamError({ message: "network error", status: 0 }).code === "board_unreachable");
check("a camera sentence on a 409 is still the camera's",
      explainCamError({ message: "camera opened but produced no frame", status: 409 }).code
        === "no_picture");
check("the status rides in the details", explainCamError(
  { message: "camera opened but produced no frame", status: 409 }).detail.includes("409"));

// -- 6. the fallback and the toast --------------------------------------------
const unknown = explainCamError("Traceback (most recent call last): KeyError 'x'");
check("an unknown failure still says what to try, raw under Details",
      !unknown.known && /^Try again\. /.test(unknown.fix)
        && /^Press "Reconnect"/.test(explainCamError("Traceback", { retry: "Reconnect" }).fix)
        && unknown.detail.startsWith("Traceback"));
check("nothing to explain is null", explainCamError("") === null && explainCamError(null) === null);
check("camErrorText joins headline and fix",
      camErrorText("camera read failed").startsWith("The camera stopped sending pictures. Check"));

const said = [];
const toast = { error: (m, d) => said.push([m, d]) };
camErrorToast(toast, "Could not add camera",
              { message: "one system runs at most 2 USB cameras at once - switch another off first", status: 400 });
check("a server sentence written for a person keeps the caller's heading",
      said[0][0] === "Could not add camera" && said[0][1].startsWith("one system runs"), JSON.stringify(said[0]));
camErrorToast(toast, "Could not read the camera", { message: "cannot open camera '0': no device at /dev/video0 - not plugged in", status: 400 });
check("a known cause becomes the headline", said[1][0].startsWith("Camera not found."), said[1][0]);
camErrorToast(toast, "Could not change that setting", { message: "[Errno 5] Input/output error", status: 500 });
check("an unknown raw error keeps the heading and says what to try",
      said[2][0] === "Could not change that setting. Try again. If it keeps happening, send the text below to support."
        || said[2][0] === "The board could not finish that. Try again in a moment.", said[2][0]);

console.log(bad ? `\n${bad} FAILED` : "\nall good");
process.exit(bad ? 1 : 0);
