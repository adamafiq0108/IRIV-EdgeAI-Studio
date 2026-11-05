// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// camErrors.js -- why a camera failed, in words the person at the board can
// act on.
//
// The camera card, the Live View label's tooltip, the rail and every camera
// toast used to print whatever the backend said: "cannot open camera '0'",
// "camera 'rtsp://...' did not answer", "HTTP 409". True, and useless to an
// operator, who needs two things -- what happened, and the one thing to do
// about it. So every camera failure is passed through explainCamError(), which
// returns a plain headline and a verb-first fix, and keeps the original text
// as `detail` for the "Details" expander, where support can still read it.
//
// THE PATTERNS ARE THE BACKEND'S OWN FIXED PHRASES. The app's camera.py
// (diagnose_open, Camera._failed) and core/services/detector_control.py write
// short facts in stable wording -- "no device at /dev/video0 - not plugged
// in", "username or password rejected (RTSP 401)" -- and this file matches on
// them. Change a phrase there and it must change here; tests/test_camerrors.mjs
// and tests/smoke_camcause.py pin both ends. Older apps still running on a
// customer's box write the older phrases, which are matched too, so a board
// that has not been updated still gets a plain message.
//
// Specific causes are tested BEFORE the general ones: "camera disconnected:
// '0' gave no frames after 3 reconnect attempts (no device at /dev/video0 -
// not plugged in)" is an unplugged camera, not a stalled one.
//
// Headline rules (locked by the product owner): no em dash, no developer
// words (no status codes, device paths, "RTSP", "socket", "timeout"), and a
// button is named by its exact on-screen label in quotes.

const SETTINGS = '"Camera settings"';

// Credentials never reach the screen, not even under "Details": an RTSP
// source carries its password in the URL, and older apps quote the URL whole.
// The password runs to the LAST "@" before the host (FFmpeg splits there, so
// "admin:P@ss@cam" is a working address); stopping at the first one showed
// the rest of the password. A "/" inside it too (Pipi L5): the URL is parsed,
// not matched by one pattern, by the rule app/faults.py and
// core/services/redact.py keep. "cam:554/a@b" is a host, a port and a path.
// A user with no password is hidden as well, here only.
const URL_IN_TEXT = /([A-Za-z][A-Za-z0-9+.-]*:\/\/)([^\s'"<>]*)/g;
export function maskCredentials(text) {
  return String(text || "").replace(URL_IN_TEXT, (all, scheme, rest) => {
    const at = rest.lastIndexOf("@");
    if (at <= 0) return all;
    const info = rest.slice(0, at);
    const colon = info.indexOf(":");
    const user = colon < 0 ? info : info.slice(0, colon);
    if (user.includes("/")) return all;
    if (colon < 0) return `${scheme}***${rest.slice(at)}`;
    const pass = info.slice(colon + 1);
    if (!pass || /^\d{1,5}\//.test(pass)) return all;
    return `${scheme}${user}:***${rest.slice(at)}`;
  });
}

// [code, pattern, title(match), fix(match, retry)]. `retry` is the label of
// the button that tries again where the message is shown ("Reconnect" on the
// camera card, "Preview snapshot" in the dialog), or empty where no such
// button sits beside it -- a toast, a tooltip, System Control -- and the fix
// then says "try again" instead of naming a button the screen does not have.
const again = (r) => (r ? `press "${r}"` : "try again");
const RULES = [
  ["reconnecting", /^reconnecting camera(?:\s*\((\d)\/3\))?/i,
    () => "The camera stopped sending pictures. Reconnecting now.",
    (m) => (m[1] ? `Attempt ${m[1]} of 3. No action needed yet.`
                 : "No action needed yet.")],
  ["no_source", /no camera chosen/i,
    () => "No camera has been chosen yet.",
    () => `Open ${SETTINGS}, pick a camera, then press "Apply Settings".`],
  ["csi_unsupported", /CSI \(libcamera\)|libcamera/i,
    () => "This board cannot use ribbon-cable cameras.",
    () => "Add the camera again as a USB, IP or GigE camera."],
  ["software_missing", /opencv-python is not installed|no camera backend/i,
    () => "The camera software is missing on this board.",
    () => "Contact support to repair the installation."],
  ["depth_software_missing", /Orbbec SDK is not installed/i,
    () => "The depth camera software is not installed on this board.",
    () => "Contact support to install it."],
  ["depth_not_found", /no Orbbec camera found/i,
    () => "Depth camera not found.",
    (m, r) => `Check its USB cable is plugged in, then ${again(r)}.`],
  ["shared", /is owned by another system; consuming its shared feed/i,
    () => "This camera is shared with another system on this board.",
    () => "No action needed. The picture comes from that system."],
  ["busy_system", /held by another\s+system/i,
    () => "Another system on this board is using this camera.",
    () => "Stop that system, or choose a different camera."],
  // The master's rewrite of busy_program when the holder is this system's
  // own app (models._own_holder): there is no other program to close.
  ["busy_self", /in use by this system's own detection/i,
    () => "This system is already using this camera.",
    () => "Stop detection first, or watch this camera in the live view."],
  ["busy_program", /in use by another program/i,
    () => "Another program on this board is using this camera.",
    (m, r) => `Close that program, then ${again(r)}.`],
  ["gige_controlled", /controlled by another application/i,
    () => "Another program is controlling this camera.",
    () => "Close it (on this board or on a PC), then try again."],
  ["gige_none", /no GigE camera answered/i,
    () => "No GigE camera answered on the network.",
    () => "Check the camera's network cable and power, then try again."],
  ["usb_unplugged", /no device at \S+ - not plugged in/i,
    () => "Camera not found.",
    (m, r) => `Check the USB cable is plugged in, then ${again(r)}.`],
  ["usb_permission", /permission denied on \/dev\//i,
    () => "This system is not allowed to use the camera.",
    (m, r) => `Contact support to give this system access to the camera, then ${again(r)}.`],
  ["auth_missing", /needs a username and password/i,
    () => "The camera needs a username and password.",
    () => `Enter them in ${SETTINGS}.`],
  ["auth_rejected", /username or password rejected/i,
    () => "The camera rejected the username or password.",
    () => `Check them in ${SETTINGS}.`],
  ["forbidden", /access refused for this user/i,
    () => "The camera refused this user.",
    () => "Allow this user to view video in the camera's own settings, or use another user."],
  ["bad_path", /stream path not found/i,
    () => "The camera's video address is wrong.",
    () => `Check the address in ${SETTINGS}. The camera's manual lists the right one.`],
  ["unplayable", /answered, but its video could not be played/i,
    () => "The camera answered, but its video could not be played.",
    (m, r) => `Set the camera's video format to H.264 in its own settings, then ${again(r)}.`],
  ["net_dns", /host (\S+) not found/i,
    (m) => `Can't find the camera address ${m[1]}.`,
    () => `Check the address in ${SETTINGS}.`],
  ["net_refused", /(\[[^\]]+\]|[^\s:'"(]+):\d+ refused the connection/i,
    (m) => `The camera at ${m[1]} refused the connection.`,
    () => `Check the port number in the address in ${SETTINGS}.`],
  ["net_unreachable", /no answer from (\[[^\]]+\]|[^\s:'"(]+):\d+/i,
    (m) => `Can't reach ${m[1]}.`,
    () => "Check the camera is powered on and on the same network."],
  ["net_odd_reply", /answered with status \d+/i,
    () => "The camera gave an unexpected answer.",
    () => `Check the address in ${SETTINGS}.`],
  // An app from before the network check: the cause was not asked for.
  ["net_failed", /did not answer - check the address/i,
    () => "Can't connect to the camera.",
    () => `Check the address, username and password in ${SETTINGS}, and that the camera is on the same network.`],
  ["no_picture", /opened but produced no frame/i,
    () => "The camera connected but sent no picture.",
    (m, r) => `${r ? `Press "${r}" again` : "Try again"}. If it still fails, choose a different size under "High Resolution".`],
  ["file_unreadable", /cannot read this file/i,
    () => "This video file could not be played.",
    () => "Upload it again, or choose a different file."],
  ["stalled", /gave no frames|camera read failed|stopped delivering/i,
    () => "The camera stopped sending pictures.",
    (m, r) => `Check the cable and power, then ${again(r)}.`],
  ["depth_failed", /^orbbec:|depth camera did not open/i,
    () => "The depth camera did not start.",
    (m, r) => `Unplug it and plug it back in, then ${again(r)}.`],
  ["gige_failed", /cannot open camera 'gige/i,
    () => "The GigE camera could not be opened.",
    (m, r) => `Check its network cable and power, then ${again(r)}.`],
  ["usb_failed", /cannot open camera '\d+'/i,
    () => "The camera could not be opened.",
    (m, r) => `Check the USB cable is plugged in, then ${again(r)}.`],
  ["open_failed", /cannot open camera/i,
    () => "The camera could not be opened.",
    () => "Check it is plugged in or reachable on the network, then try again."],
];

// A failure of the REQUEST, not of the camera: the board itself did not
// answer, or refused. Only used when the text says nothing more useful.
function byStatus(status) {
  if (status === 0) {
    return ["board_unreachable", "Can't reach the board.",
            "Check your connection and try again."];
  }
  if (status === 401 || status === 403) {
    return ["no_access", "You don't have access to do that.",
            "Sign in again, or ask the box owner."];
  }
  if (status === 404) {
    return ["unknown_request", "The board did not recognise that request.",
            "Refresh the page and try again."];
  }
  if (status >= 500) {
    return ["board_failed", "The board could not finish that.",
            "Try again in a moment."];
  }
  return null;
}

/**
 * explainCamError(err, {retry}) -> {code, title, fix, detail, said, known} | null
 *
 * `err` is whatever arrived: the camera's error string off the status poll,
 * an Error from fetch, or an ApiError (message + status). `known` is false
 * for the generic fallback, so a caller that shows mixed failures (the
 * systems table) can leave non-camera text alone. `said` is the message as
 * the server wrote it (credentials masked); `detail` adds the status.
 */
export function explainCamError(err, { retry = "" } = {}) {
  if (err == null || err === "") return null;
  const raw = typeof err === "string" ? err : String(err.message ?? err);
  let status = typeof err === "object" && Number.isInteger(err.status) ? err.status : null;
  const bare = /^HTTP (\d{3})$/i.exec(raw.trim());
  if (bare) status = Number(bare[1]);
  if (/^(network error|the system did not answer in time)$/i.test(raw.trim())) status = 0;
  const said = maskCredentials(raw);
  const detail = status && !bare ? `${said} (HTTP ${status})` : said;
  if (!bare && status !== 0) {
    for (const [code, re, title, fix] of RULES) {
      const m = re.exec(raw);
      if (m) return { code, title: title(m), fix: fix(m, retry), detail, said, known: true };
    }
  }
  const s = status == null ? null : byStatus(status);
  if (s) return { code: s[0], title: s[1], fix: s[2], detail, said, known: true };
  return {
    code: "unknown",
    title: "The camera is not working.",
    fix: `${retry ? `Press "${retry}"` : "Try again"}. If it keeps happening, open "Details" and send the text to support.`,
    detail,
    said,
    known: false,
  };
}

/** The headline and the fix as one line, for a tooltip or a toast. */
export function camErrorText(err, opts) {
  const x = explainCamError(err, opts);
  return x ? `${x.title} ${x.fix}` : "";
}

/**
 * A camera toast: the plain headline, the technical text underneath.
 *
 * A server sentence that is already written for a person (the camera budget,
 * "camera source is required") is not a camera FAULT and has no pattern here;
 * it keeps the caller's heading and stays readable as the detail, as before.
 * Anything else unknown -- a bare status, a raw exception -- keeps the heading
 * and says what to try, with the raw text underneath for support. Not the
 * card's fallback: a toast is not beside a "Reconnect" button.
 */
export function camErrorToast(toast, heading, err, opts) {
  const x = explainCamError(err, opts);
  if (!x) return toast.error(heading);
  if (!x.known) {
    return isPlainSentence(x.said)
      ? toast.error(heading, x.said)
      : toast.error(`${heading}. Try again. If it keeps happening, send the text below to support.`,
                    x.detail);
  }
  return toast.error(`${x.title} ${x.fix}`, x.detail);
}

// Words that mean the text was written for a developer, not an operator.
const JARGON = /\b(HTTP|errno|Errno|exception|Exception|Traceback|socket|ECONN\w*|ETIMEDOUT|EHOST\w*|timeout|timed out|V4L2|v4l2|codec|ffmpeg|gstreamer|cv2|stream handle|NoneType|KeyError|ValueError|RuntimeError)\b|\[Errno|\/dev\//;

export function isPlainSentence(text) {
  const t = String(text || "").trim();
  return t.length > 0 && !JARGON.test(t);
}

/**
 * The Error for a failed raw fetch(), for a caller not on api/client.js.
 *
 * The API envelope puts its sentence in `error`; `detail` is only the legacy
 * FastAPI shape. The Preview snapshot read `detail` alone, so every refusal
 * reached explainCamError as a bare "HTTP 409" and read as the board's
 * problem instead of "the camera is in use". Same order as api/client.js.
 */
export async function responseError(res) {
  const j = await res.json().catch(() => null);
  return Object.assign(new Error(j?.error || j?.detail || `HTTP ${res.status}`),
                       { status: res.status });
}
