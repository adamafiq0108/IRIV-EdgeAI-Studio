// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Where this page is served from: "" (the site root) or "/s/<slug>".
//
// The box can be reached two ways (Settings -> Network -> Software port;
// core/services/webaccess.py): by PORT, where the hub and every system each
// own a port and every page sits at "/", or through the REVERSE PROXY on
// port 80, where the hub is at "/" and a system at "/s/<slug>/" of ONE
// origin. The server says which in the <base href> it writes first into
// index.html (core/services/weblinks.py index_html); nothing else is
// trusted, and anything that is not "/" or "/s/<slug>/" reads as "".
//
// Every root-relative address the app makes goes through apiUrl() or
// pageUrl(): fetches, <img>/<video> sources, downloads, window.open,
// location.assign. tests/check_basepath.mjs fails the build's tests on a
// new "/api/..." literal that does not. The router gets BASE as its
// basename, so <Link to="/workflow"> and navigate() need nothing.

const SHAPE = /^\/s\/[a-z][a-z0-9_]{0,63}\/?$/;

function readBase() {
  if (typeof document === "undefined") return "";
  const el = document.querySelector("base[href]");
  const href = el ? el.getAttribute("href") || "" : "";
  return SHAPE.test(href) ? href.replace(/\/+$/, "") : "";
}

/** "" or "/s/<slug>" -- read once, at load. */
export const BASE = readBase();

/** A root-relative path ("/api/...", "/footage/...") as this page must ask
 *  for it. Absolute URLs (another origin, a system's card URL), protocol-
 *  relative ones and paths already under BASE are returned unchanged, so
 *  wrapping twice is harmless. */
export function apiUrl(path) {
  const p = String(path ?? "");
  if (!BASE || !p.startsWith("/") || p.startsWith("//")) return p;
  if (p === BASE || p.startsWith(`${BASE}/`) || p.startsWith(`${BASE}?`)) return p;
  return BASE + p;
}

/** The same, for a page address handed to the browser (location.assign). */
export const pageUrl = apiUrl;

/** window.location.pathname without BASE: the router-relative path. */
export function stripBase(pathname) {
  const p = String(pathname || "/");
  if (!BASE) return p;
  if (p === BASE) return "/";
  return p.startsWith(`${BASE}/`) ? p.slice(BASE.length) : p;
}
