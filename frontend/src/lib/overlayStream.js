// overlayStream.js - ONE overlay connection per page, shared by every feed on
// it.
//
// A system in [video] overlay = "client" mode sends its boxes as events on
// GET /api/detector/overlay. That is a long-lived HTTP response, and a
// browser allows about six of those per origin: this codebase has already
// lost whole pages to MJPEG previews filling the pool (StreamImg's header).
// So the page does not open one per tile. Every OverlayCanvas subscribes
// here with its camera; the cameras wanted are folded into ONE request
// (repeat `camera`), reopened only when that set changes, and the messages
// are handed to whichever subscribers they belong to.
//
// The hub's Live Wall is the same idea one level up: its tiles show systems
// served by OTHER processes, so their events come relayed by the hub on
// GET /api/hub/overlay, one stream for every system on the wall, each event
// tagged with its system (core/services/hub_overlay.py). Same machinery,
// keyed by system + camera instead of camera alone (makeStream below).
//
// fetch() rather than EventSource: it carries this tab's X-Session (an
// EventSource can send only the cookie), and an AbortController ends it the
// moment the last subscriber leaves - which is also what tells the board to
// stop building messages for this page.
//
// A 404 that says the overlay is drawn on the server (or the system is gone),
// or a 401/403, is final for that camera set; anything else, including a 404
// "no such camera" while the app is still opening its cameras after Start,
// retries with a backoff.
import { apiUrl } from "./base.js";
import { authHeaders } from "./session.js";
import { forCamera, hubFeedKey, hubOverlayQuery, parseSSE, routeHubEvent } from "./overlay.js";

// One shared connection. `url(keys)` is the address for a sorted set of
// subscription keys; `deliver(msg, sub)` hands one parsed event to one
// subscriber ({ key, onMessage, onReset }).
function makeStream(url, deliver) {
  const subs = new Map();
  let nextId = 1;
  //: { key, ctrl, dead } for the open (or given-up) connection, or null.
  let conn = null;
  let syncTimer = null;
  let failures = 0;
  //: key -> the last message delivered for it. The app sends a message only
  //: when something CHANGED, and primes only a NEW connection; a subscriber
  //: joining an already-open stream (a canvas mounting under a tile whose
  //: view-on-detect gate holds the stream, components/DetectGate.jsx) would
  //: otherwise draw nothing until the next change. Dropped with the stream.
  const last = new Map();

  function subscribe(key, onMessage, onReset) {
    const id = nextId++;
    subs.set(id, { key, onMessage: (m) => { last.set(key, m); onMessage(m); },
                   onReset: () => { last.delete(key); if (onReset) onReset(); } });
    const seen = last.get(key);
    if (seen) setTimeout(() => { if (subs.has(id)) onMessage(seen); }, 0);
    scheduleSync();
    return () => {
      subs.delete(id);
      if (![...subs.values()].some((s) => s.key === key)) last.delete(key);
      scheduleSync();
    };
  }

  function wanted() {
    return [...new Set([...subs.values()].map((s) => s.key))].sort();
  }

  // Deferred a tick, so a page mounting four tiles at once opens one request
  // for all four rather than four in a row.
  function scheduleSync() {
    if (syncTimer) return;
    syncTimer = setTimeout(() => { syncTimer = null; sync(); }, 0);
  }

  function sync() {
    const keys = wanted();
    const key = keys.join("\u0001");
    if (conn && conn.key === key) return;
    if (conn) conn.ctrl.abort();
    conn = null;
    failures = 0;
    if (keys.length) open(keys, key);
  }

  function dispatch(text) {
    let msg;
    try { msg = JSON.parse(text); } catch { return; }
    for (const s of subs.values()) deliver(msg, s);
  }

  async function open(keys, key) {
    const ctrl = new AbortController();
    const me = { key, ctrl, dead: false };
    conn = me;
    let final = false;
    try {
      const res = await fetch(url(keys), {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/event-stream", ...authHeaders() },
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) {
        final = [401, 403].includes(res.status);
        // A 404 is final only when it says the overlay is drawn on the server
        // (or the system is gone). The app also answers 404 "no such camera" /
        // "no camera" for the first seconds after Start, while its cameras
        // open, and treating THAT as final left the view-on-detect tile on
        // "No object detected" until the page was reloaded.
        if (res.status === 404) {
          const said = await res.text().catch(() => "");
          final = /on the server|no such system/i.test(said);
        }
        throw new Error(`overlay ${res.status}`);
      }
      failures = 0;
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const out = parseSSE(buf + dec.decode(value, { stream: true }));
        buf = out.rest.length > 1 << 20 ? "" : out.rest;   // a runaway tail
        out.events.forEach(dispatch);
      }
    } catch {
      if (ctrl.signal.aborted) return;          // we closed it: nothing to do
    }
    if (conn !== me) return;
    for (const s of subs.values()) if (s.onReset) s.onReset();
    if (final) { me.dead = true; return; }      // kept, so sync() leaves it be
    failures += 1;
    const wait = Math.min(30000, 1500 * 2 ** Math.min(failures, 5));
    setTimeout(() => {
      if (conn !== me) return;
      conn = null;
      const now = wanted();
      if (now.length) open(now, now.join("\u0001"));
    }, wait);
  }

  return subscribe;
}

const pageStream = makeStream(
  (cams) => apiUrl(`/api/detector/overlay?${cams.map((c) => `camera=${encodeURIComponent(c)}`).join("&")}`),
  (msg, s) => { if (forCamera(msg, s.key)) s.onMessage(msg); },
);

const hubStream = makeStream(
  (keys) => apiUrl(`/api/hub/overlay?${hubOverlayQuery(keys)}`),
  (env, s) => {
    const [slug, camera] = s.key.split("\u0000");
    const what = routeHubEvent(env, slug, camera);
    if (what === "msg") s.onMessage(env.m);
    else if (what === "reset" && s.onReset) s.onReset();
  },
);

/** Listen for `camera` ("" = the first camera). `onMessage(msg)` gets each
 *  message for it; `onReset()` is called when the stream drops, so a feed
 *  does not keep showing boxes nobody is updating. Returns the unsubscribe. */
export function subscribeOverlay(camera, onMessage, onReset) {
  return pageStream(camera == null ? "" : String(camera), onMessage, onReset);
}

/** The same for a hub page showing ANOTHER system's feed (the Live Wall):
 *  `camera` of system `slug`, through the hub's one stream for the page. */
export function subscribeHubOverlay(slug, camera, onMessage, onReset) {
  return hubStream(hubFeedKey(String(slug), camera), onMessage, onReset);
}
