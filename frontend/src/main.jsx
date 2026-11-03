// SPA entry. Mounts the router into #root. The whole app is client-rendered and
// served by FastAPI (see master_server.py catch-all); the backend is JSON-only.
// FIRST, before anything can fire a request: the per-tab session intake reads
// the window.name hand-off into this origin's sessionStorage and empties
// it -- the router and the bootstrap fetch must only ever run after it.
import "./lib/session.js";
import React from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { router } from "./router.jsx";
// index.css imports theme.css itself, into a `legacy` cascade layer - see the
// comment at the top of that file. Importing theme.css here as well would add
// an unlayered copy and re-break every Tailwind padding utility.
import "./index.css";

// A deploy replaces the hashed chunk files while tabs are open on the old
// index.html; the next lazy-loaded route then 404s and the page dies with a
// blank panel (observed live: /assets/Workflow-*.js after a rebuild). Vite
// fires this event for exactly that case -- reload once, and the fresh
// index.html brings matching chunks. The guard stops a reload LOOP if the
// server itself is what is broken: one automatic attempt, then the error is
// allowed to surface.
window.addEventListener("vite:preloadError", (event) => {
  // Time-based rather than a flag cleared on load: a flag cleared when the
  // page loads re-arms BEFORE the next failure, which is a reload loop with
  // extra steps when the server itself is serving broken assets. One
  // automatic attempt per half-minute; a second failure inside the window
  // surfaces as the error it is.
  const KEY = "fx-chunk-reload-at";
  const last = Number(sessionStorage.getItem(KEY) || 0);
  if (Date.now() - last > 30_000) {
    sessionStorage.setItem(KEY, String(Date.now()));
    event.preventDefault();
    window.location.reload();
  }
});

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
