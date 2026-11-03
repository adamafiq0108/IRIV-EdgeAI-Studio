// Thin fetch wrapper. Every call is same-origin (the SPA is served by the same
// FastAPI process), so the session cookie rides automatically. The backend
// speaks one shape: {"ok": true, ...} on success, {"ok": false, "error": "..."}
// with a non-2xx status on failure (see core error handler). A 401 means "no
// session" - callers surface it so the app can route to /login.

import { authHeaders, clearToken } from "../lib/session.js";
import { BASE, apiUrl, pageUrl, stripBase } from "../lib/base.js";

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;   // full JSON body (e.g. a 409 conflict's used_by list)
  }
}

// Keep technical failures out of the UI while retaining helpful validation
// messages returned by the server.
export function friendlyError(error) {
  if (!(error instanceof ApiError)) return error?.message || String(error || "Something went wrong.");

  const requestId = error.data?.rid;
  const suffix = requestId ? ` Reference: ${requestId}.` : "";
  if (error.status === 0) {
    return error.message.includes("time")
      ? "The system took too long to respond. Check that it is online and try again."
      : "Can't reach the system. Check your connection and try again.";
  }
  if (error.status === 401 || error.status === 403) return "You don't have access to do that.";
  if (error.status === 404) return "That item is no longer available. Refresh the page and try again.";
  if (error.status === 409) return error.message || "That item was changed elsewhere. Refresh the page and try again.";
  if (error.status === 429) return "Too many requests. Wait a moment and try again.";
  if (error.status >= 500) return `The system couldn't complete that. Try again, or contact an administrator if it keeps happening.${suffix}`;
  return error.message || "Something went wrong.";
}

//: How long a call may hang before it is abandoned.
//
// EVERY call had to settle for the UI to work, and nothing guaranteed one
// would. `fetch` has no timeout of its own: a request that opens its socket
// and then waits -- a master mid-restart, a box whose port is bound by a
// process that has stopped answering -- never resolves and never rejects.
//
// That is not a slow page, it is a DEAD one. Callers are written as
//
//     setBusy(true); try { await api.post(...) } finally { setBusy(false) }
//
// so a promise that never settles means `finally` never runs and `busy` stays
// true for the life of the tab. On Camera Setup that disables the camera
// switches, the remove buttons, Add camera and Stop -- all of them, silently,
// with no error anywhere. It reads exactly like "I can't deactivate the
// camera", and only a reload clears it.
//
// Generous on purpose: this is a backstop against a hang, not a latency
// budget. Real calls on this box are milliseconds; the slowest honest ones
// measured are a workflow compile over a big events table (~2.5s) and the
// RTSP subnet sweep (~2s). Nothing legitimate is anywhere near this.
const TIMEOUT_MS = 60000;

//: Uploads get their own, far longer budget. A model file is hundreds of
//: megabytes and the browser may be on wifi two rooms away -- a minute is a
//: perfectly ordinary time for one, and aborting it would turn this backstop
//: into the bug it exists to prevent. Still bounded, because a stalled upload
//: locks its page exactly like a stalled POST does.
const UPLOAD_TIMEOUT_MS = 900000;    // 15 minutes

// The Systems hub, as the SERVER told this page to reach it (the shell payload
// and every auth response carry `hub_url`; the hub itself sends ""). Remembered
// here because the places that need it -- a sign-out, a session that expired
// under a background poll -- are not always holding the payload that carried
// it, and a bounce cannot wait for a fetch that may itself 401.
let _hubUrl = "";
// Whether THIS page is the hub itself. Without it, "I don't know where the
// hub is" and "I am the hub" look identical -- both are an empty hub_url --
// and the fallback below would bounce the hub to a port it may not be on.
let _isHub = false;
export function rememberHub(url, isHub = false) {
  _hubUrl = url || "";
  _isHub = !!isHub;
}

//: The hub's port when nothing has told us otherwise. Deployment policy lives
//: in [hub].url on the server and reaches us as hub_url; this is the same
//: default the server falls back to, so the two cannot disagree.
const HUB_PORT_FALLBACK = "5100";

/** Where the login screen lives. On a master: ALWAYS the hub's.
 *
 *  Leaving a system port is the whole point -- a login screen on 5101 is a
 *  door into ONE system, and a person arriving at this browser should be met
 *  by the board.
 *
 *  Three answers, in order of how much we know: what the server just told us,
 *  what the shell told us at boot, and -- when a logout call failed and the
 *  page was never booted with an answer -- this host on the hub's port. That
 *  last one is the difference between "always" and "usually": without it a
 *  logout whose request never landed left the operator on the master's own
 *  login, which is exactly the door this is meant to close.
 *
 *  `next` is deliberately NOT carried across a port change: it names a path
 *  on the master, which the hub does not serve.
 */
export function loginUrl(hub = _hubUrl, next = "") {
  const target = hub || _hubUrl;
  if (target) return `${target.replace(/\/+$/, "")}/login`;
  // Under the reverse proxy the hub is this origin's root (lib/base.js).
  if (BASE) return "/login";
  // A page with NO port in its address is on 80 or 443: the box's front door
  // (the hub behind the port redirect), not a system on a port of its own.
  // Sending it to :5100 would be wrong on plain HTTP and fatal on HTTPS, where
  // 5100 only speaks HTTP (ERR_SSL_PROTOCOL_ERROR).
  if (!_isHub && window.location.port && window.location.port !== HUB_PORT_FALLBACK) {
    return `${window.location.protocol}//${window.location.hostname}`
         + `:${HUB_PORT_FALLBACK}/login`;
  }
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}

// Session expired (idle-timeout) while the page was open: a background poll
// eventually 401s. Bounce to the login screen instead of leaving a stale,
// stuck page. Skipped on the auth screens so a failed login (also 401) doesn't
// loop.
let _redirecting = false;
function redirectToLogin() {
  const p = stripBase(window.location.pathname);
  if (_redirecting || ["/login", "/forgot", "/reset-password"].includes(p)) return;
  _redirecting = true;
  window.location.assign(loginUrl(_hubUrl, p + window.location.search));
}

// Account setup is unfinished (the server's 409 + X-Account-Setup-Required):
// go and finish it. Same-origin on purpose -- hub and masters both serve the
// setup screen, and staying on this port keeps the tab's session.
function redirectToSetup() {
  if (_redirecting || stripBase(window.location.pathname) === "/account-setup") return;
  _redirecting = true;
  window.location.assign(pageUrl("/account-setup"));
}

// Is this response the setup gate's refusal? The header is the tag; the body's
// flag is accepted too, so a proxy that drops custom headers cannot turn the
// bounce off. ANY 409 so tagged bounces, whichever path it came from.
function setupRequired(res, data) {
  return res.status === 409
    && !!(res.headers.get("X-Account-Setup-Required")
          || (data && data.needs_account_setup));
}

/** The same question of a thrown ApiError, for a caller that wants to word
 *  the refusal itself ("Finish account setup first" with a way there) rather
 *  than print the server's text while the bounce is on its way. */
export function isSetupRequired(e) {
  return !!(e instanceof ApiError && e.status === 409
            && e.data && e.data.needs_account_setup);
}

const isBlob = (b) => typeof Blob !== "undefined" && b instanceof Blob;

async function request(method, path, body) {
  const opts = {
    method,
    credentials: "same-origin",
    // Per-tab identity: when this tab holds a session token, every API call
    // acts as that user, whatever the browser-wide cookie says.
    headers: { ...authHeaders() },
  };
  if (body instanceof FormData) {
    // Sent as-is, and deliberately WITHOUT a Content-Type: multipart needs a
    // boundary parameter that only the browser can generate, and setting the
    // header by hand omits it, which the server then cannot parse. Stringify
    // would be worse still -- a File has no JSON form, so the upload would
    // arrive as "{}".
    opts.body = body;
  } else if (isBlob(body)) {
    // A file sent as the raw body (the offline update upload): the server
    // streams it straight to disk, with no multipart and no filename.
    opts.headers["Content-Type"] = "application/octet-stream";
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  // The abort is what guarantees this promise settles. Cleared in `finally`
  // so a call that finished normally does not fire a timer at a dead request.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(),
                           body instanceof FormData || isBlob(body)
                             ? UPLOAD_TIMEOUT_MS : TIMEOUT_MS);
  opts.signal = ctrl.signal;
  try {
    res = await fetch(apiUrl(path), opts);
  } catch (e) {
    // Told apart because they mean different things to a person: "the box did
    // not answer" is a box to go and look at, while a network error is
    // usually this browser's own connection. Both throw, which is what lets
    // the caller's `finally` release its busy flag either way.
    throw ctrl.signal.aborted
      ? new ApiError("the system did not answer in time", 0)
      : new ApiError("network error", 0);
  } finally {
    clearTimeout(timer);
  }

  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json().catch(() => null) : null;

  if (!res.ok) {
    // Only a *session-level* 401 (the auth middleware tags it with this header)
    // means "your session expired" -> bounce to /login. Endpoint-level 401s
    // (bad pairing credentials, failed re-auth) lack the header and are thrown
    // so the calling view can show the real error instead of losing the page.
    if (res.status === 401 && res.headers.get("X-Session-Required")) {
      // A dead per-tab token (expired, revoked) must not keep 401-ing the tab
      // after the user signs back in -- drop it before bouncing.
      clearToken();
      redirectToLogin();
    }
    // The master is still on the shipped password: the server answers every
    // call but the setup ones with this 409 until that is changed (UAT-103).
    if (setupRequired(res, data)) {
      redirectToSetup();
    }
    const msg = (data && (data.error || data.detail)) || res.statusText || `HTTP ${res.status}`;
    throw new ApiError(msg, res.status, data);
  }
  return data;
}

/** A FILE, not JSON: {blob, filename}. Its own path because `request` reads
 *  every answer as JSON, and a zip read that way arrives as null.
 *
 *  On the UPLOAD timeout rather than the ordinary one: the box builds the
 *  archive before the first byte is sent, and a few hundred megabytes of
 *  evidence off an NVMe is minutes, not seconds. An error still arrives as
 *  JSON, so a refusal ("that selection is 3.2 GB") reads as its sentence
 *  instead of as a broken download.
 */
async function download(method, path, body) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), UPLOAD_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(apiUrl(path), {
      method,
      credentials: "same-origin",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
    });
  } catch {
    throw ctrl.signal.aborted
      ? new ApiError("the system did not answer in time", 0)
      : new ApiError("network error", 0);
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    if (res.status === 401 && res.headers.get("X-Session-Required")) {
      clearToken();
      redirectToLogin();
    }
    if (setupRequired(res, data)) {
      redirectToSetup();
    }
    throw new ApiError((data && (data.detail || data.error))
                       || res.statusText || `HTTP ${res.status}`,
                       res.status, data);
  }
  // The server names the file (it knows the system and the timestamp); the
  // header is the only place it can say so.
  const cd = res.headers.get("content-disposition") || "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  return { blob: await res.blob(),
           filename: match ? decodeURIComponent(match[1]) : "download" };
}

/** Save a {blob, filename} to the machine, the way every browser allows. */
export function saveBlob({ blob, filename }) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "download";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked on the next tick: revoking before the click has been handled
  // cancels the download in Safari.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export const api = {
  download: (path, body) => download("POST", path, body),
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  patch: (path, body) => request("PATCH", path, body),
  del: (path, body) => request("DELETE", path, body),
};
