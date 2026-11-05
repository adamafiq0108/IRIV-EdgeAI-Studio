// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// App-wide shell context: brand, current user, the system this master serves,
// the capability-driven nav, and the device sidebar tree. Fetched once from
// /api/bootstrap when the protected shell mounts. A 401 means "no session" -
// the shell redirects to /login.
import { createContext, useContext } from "react";

export const AppContext = createContext(null);

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside <AppShell>");
  return ctx;
}

// nav page id -> client route. The backend nav (core/services/nav.py) is
// capability-driven and emits page ids; the SPA owns the URL for each. Keeping
// this map here means a new page is one entry, never a slug branch.
export const PAGE_PATHS = {
  main: "/",
  detection: "/detection",
  zones: "/zones",
  workflow: "/workflow",
  analytics: "/analytics",
  events: "/events",
  // A system's own tables: their shape and their rows, one tool. Not the
  // same thing as `dataset` below, which is captured training photos.
  database: "/database",
  footage: "/footage",
  dataset: "/dataset",
  dataset_library: "/dataset-library",
  // Hub-only page: the hub shell renders this itself, but the route stays
  // mapped so a direct link still resolves.
  audit: "/audit",
  // Per-process, so it is NOT hub-only: each master serves its own ring at its
  // own /logs, and the hub shell renders the same component for the hub's.
  logs: "/logs",
  builder: "/builder",
  blynk: "/blynk",
  modbus: "/modbus",
  models: "/models",
};

// A page the server's nav omitted - a category the admin hid for this
// account, or an admin-only page - must not be reachable by typing its URL.
// Resolve the pathname back to a page id and, when the nav does not list it,
// hand back the first page the nav DOES list; null means "no redirect".
// Only pages a section claims AND the nav can emit are policed: unclaimed
// routes (/zones lands on a Camera Setup tab, /audit is the hub's) are
// outside category control and must keep answering - and so is /builder,
// which SECTIONS lists for grouping but the backend nav never emits (it is
// reached from the Dashboard's own Edit button); policing it bounced the
// admin's "Edit dashboard" click.
import { SECTIONS } from "../components/nav-sections.js";
const CONTROLLED = new Set(SECTIONS.flatMap((s) => s.pages));
CONTROLLED.delete("builder");
/** Is `pathname` at `to`, or on a page under it?
 *
 *  A plain startsWith() is wrong here: "/dataset-library".startsWith("/dataset")
 *  is true, so opening the Library lit up Dataset in the rail as well. A nav
 *  target matches when the path IS it, or continues with a new SEGMENT --
 *  "/manage/pi-01" is under "/manage", "/dataset-library" is not under
 *  "/dataset".
 */
export function atPath(pathname, to) {
  if (!to) return false;
  if (to === "/") return pathname === "/";
  return pathname === to || pathname.startsWith(to.replace(/\/$/, "") + "/");
}


export function navRedirect(nav, pathname) {
  if (!nav?.length) return null;
  const hit = Object.entries(PAGE_PATHS)
    .sort((a, b) => b[1].length - a[1].length)
    .find(([, to]) => (to === "/" ? pathname === "/"
      : pathname === to || pathname.startsWith(to + "/")));
  if (!hit) return null;
  const [page] = hit;
  if (!CONTROLLED.has(page) || nav.some((it) => it.page === page)) return null;
  const first = nav.find((it) => PAGE_PATHS[it.page]);
  return first ? PAGE_PATHS[first.page] : null;
}

// The set of host_serials a scope selects, from the sidebar tree. Returns null
// when nothing is scoped (kind "all") so callers can treat null as "no filter".
// A folder scope includes every device in that folder AND its subfolders; the
// "unassigned" sentinel selects the Unassigned bucket.
export function hostsInScope(sidebar, scope) {
  if (!sidebar || !scope || scope.kind !== "folder") return null;
  if (scope.id === "unassigned") {
    return new Set((sidebar.unassigned || []).map((d) => d.host_serial));
  }
  const set = new Set();
  const collect = (node) => {
    (node.devices || []).forEach((d) => set.add(d.host_serial));
    (node.children || []).forEach(collect);
  };
  const find = (nodes) => {
    for (const n of nodes || []) {
      if (n.id === scope.id) { collect(n); return true; }
      if (find(n.children)) return true;
    }
    return false;
  };
  find(sidebar.tree || []);
  return set;
}

//: The text colour that reads on an accent: white when it reaches 4.5:1,
//: else a dark ink. The default teal gets the ink (white is 2.3:1 on it).
function accentInk(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return "#04222b";
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const [r, g, b] = [0, 2, 4].map((i) => lin(parseInt(m[1].slice(i, i + 2), 16) / 255));
  const L = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return 1.05 / (L + 0.05) >= 4.5 ? "#ffffff" : "#04222b";
}

// Apply the deployment's brand accent (config/brand.toml) as CSS vars, the way
// the old _base.html injected them inline. Called once bootstrap arrives.
export function applyBrand(brand) {
  if (!brand) return;
  const root = document.documentElement;
  if (brand.accent) {
    root.style.setProperty("--accent", brand.accent);
    // .cy-legacy re-declares --accent (see index.css), which would shadow this
    // inline value; it reads the rebrand back through --brand-accent instead.
    root.style.setProperty("--brand-accent", brand.accent);
    // ONE accent: the shadcn tokens follow the brand too, so a rebranded box
    // does not end up with legacy pages in its colour and the rest in teal.
    for (const v of ["--primary", "--ring", "--sidebar-primary", "--sidebar-ring"]) {
      root.style.setProperty(v, brand.accent);
    }
    // ONE ink on it: dark unless the accent is dark enough for white to read.
    root.style.setProperty("--brand-accent-ink", accentInk(brand.accent));
  }
  if (brand.accent_alt) root.style.setProperty("--accent-alt", brand.accent_alt);
  if (brand.product_name) document.title = brand.product_name;
}
