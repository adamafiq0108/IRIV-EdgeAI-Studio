// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Per-tab session identity. The ONLY file that touches sessionStorage.
//
// The browser holds ONE session cookie per host, shared by every tab and
// every port -- so a second login used to silently turn every other tab into
// the new user. Each tab now keeps its own token here (sessionStorage is
// per-tab) and sends it as the X-Session header, which the server trusts
// before the cookie. The cookie stays behind as the identity for everything
// that cannot carry a header (<img> streams, <a> downloads) -- there, the
// most recent login in this browser wins.
//
// Cross-port hand-off: sessionStorage is per-origin INCLUDING the port, so a
// token stored on the hub (:5100) does not exist on a master (:512x). The
// token NEVER appears in a URL, not even for an instant (2026-10-21, Tok Aba:
// "never show it"): goWithSession() puts it in window.name, which survives a
// same-tab move to another port of the same host, immediately before it
// leaves; the intake below (runs at import, before the router mounts) takes
// it, empties window.name, and ignores one meant for another origin. Nothing
// is armed before goWithSession() runs, so a tab that never moves carries nothing.

const KEY = "fx-session";

export function getToken() {
  try { return sessionStorage.getItem(KEY) || ""; } catch { return ""; }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(KEY, token);
  } catch { /* storage disabled: cookie mode still works */ }
}

export function clearToken() {
  try { sessionStorage.removeItem(KEY); } catch { /* ignore */ }
}

/** Headers that make a request act as THIS TAB's user. */
export function authHeaders() {
  const token = getToken();
  return token ? { "X-Session": token } : {};
}

const NAME_TAG = "fxs:";
const DISARM_MS = 5000;           // longer than a LAN page load, short enough to matter
const carryTo = new Set();       // origins this tab has decided to hand its identity to

function originOf(url) {
  try { return new URL(url, window.location.href).origin; } catch { return ""; }
}

/** Mark `url` as a destination that receives this tab's identity when it is
 *  opened with goWithSession(). Returns the url UNCHANGED: nothing secret is in it. */
export function withSession(url) {
  if (getToken()) carryTo.add(originOf(url));
  return url;
}

/** Does goWithSession(url) carry the identity along? */
export function sessionCarriesTo(url) {
  return !!getToken() && carryTo.has(originOf(url));
}

/** Move this tab to `url`, handing the identity over when withSession() marked
 *  it. The token rides window.name, set at the last moment. */
export function goWithSession(url) {
  if (sessionCarriesTo(url)) {
    try {
      const before = window.name;
      window.name = `${NAME_TAG}${originOf(url)}|${getToken()}`;
      // A move that never completes (a stopped master, a redirect, a cancelled
      // load) must not leave the token in the tab for whatever page comes next.
      // This timer dies with the page when the move DOES complete, so it only
      // ever runs in the failure case.
      setTimeout(() => {
        if ((window.name || "").startsWith(NAME_TAG)) window.name = before || "";
      }, DISARM_MS);
    } catch { /* ignore */ }
  }
  window.location.assign(url);
}

// Coming back to this page from the back/forward cache must not find the
// token still armed in window.name.
if (typeof window !== "undefined" && window.addEventListener) {
  window.addEventListener("pageshow", () => {
    if ((window.name || "").startsWith(NAME_TAG)) window.name = "";
  });
}

// -- hand-off intake: runs once, at import ----------------------------------
// Guarded so importing this module outside a browser (the node checks that
// pull in OverlayCanvas -> overlayStream -> here) does not throw; a browser
// always has `window`, so nothing changes there.
(() => {
  if (typeof window === "undefined" || !window.location) return;
  const name = window.name || "";
  if (!name.startsWith(NAME_TAG)) return;
  window.name = "";                    // read once, whoever it was meant for
  const bar = name.indexOf("|");
  if (bar < 0) return;
  if (name.slice(NAME_TAG.length, bar) !== window.location.origin) return;
  setToken(name.slice(bar + 1));
})();
