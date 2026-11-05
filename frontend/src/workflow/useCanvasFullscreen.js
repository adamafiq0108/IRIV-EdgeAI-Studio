// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// useCanvasFullscreen - the Workflow page's "Full screen" (Tok Aba,
// 2026-10-14: "so the user can play around in there without any space issue").
//
// Two layers, kept in step:
//   1. CSS, always: the page section gets `wf-fullscreen` (theme.css), which
//      pins it over the whole window (position fixed, inset 0) above the app
//      rail and header, and <body> gets `wf-fs-on` (no page scroll). The
//      palette, canvas and inspector are the same elements as before, so
//      nothing remounts: React Flow keeps its graph, its viewport and its
//      history, and its own ResizeObserver picks up the new size.
//   2. The browser's Fullscreen API on the DOCUMENT, when it is offered, so
//      the browser's own tab bar goes too. On the document rather than on the
//      canvas: an element in fullscreen hides everything outside it, and the
//      toasts and the confirm dialogs are portalled to <body>, so they would
//      have opened behind it. Refused or missing: the CSS layer alone stands.
//
// Leaving: the button; Escape (the browser handles it while it is in
// fullscreen, and fullscreenchange brings the CSS layer down with it; when
// the page does get the key, this hook listens, and leaves an Escape that something else
// on the page is using - a dialog, a menu, the blueprint editor - alone);
// or leaving the page, which always restores <body> and exits the browser
// fullscreen this hook asked for (and only that: a document the shell's own
// full-screen button put in fullscreen stays that way). Nothing is remembered
// across reloads: the page always opens normal.
import { useCallback, useEffect, useRef, useState } from "react";

/** The element in browser fullscreen now, across the WebKit spelling. */
function fsElement() {
  if (typeof document === "undefined") return null;
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function exitBrowserFullscreen() {
  try {
    const p = document.exitFullscreen?.() ?? document.webkitExitFullscreen?.();
    if (p && typeof p.catch === "function") p.catch(() => {});
  } catch { /* already out, or not allowed: nothing to undo */ }
}

// An Escape that belongs to something else open on the page.
const OVERLAY = '[role="dialog"], [role="alertdialog"], .modal-backdrop, '
  + ".ui-dialog-backdrop, .fx-menu-backdrop, [data-slot=\"dialog-overlay\"]";

/**
 * @param {{ escBlocked?: boolean }} opts escBlocked: the page has its own use
 *   for Escape right now (its context menu or an editor is open).
 * @returns {{ full: boolean, enter: () => void, exit: () => void, toggle: () => void }}
 */
export default function useCanvasFullscreen({ escBlocked = false } = {}) {
  const [full, setFull] = useState(false);
  // True only while the browser fullscreen is one THIS hook requested.
  const requested = useRef(false);
  const blocked = useRef(escBlocked);
  blocked.current = escBlocked;

  const exit = useCallback(() => {
    setFull(false);
    if (requested.current) {
      requested.current = false;
      if (fsElement()) exitBrowserFullscreen();
    }
  }, []);

  const enter = useCallback(() => {
    setFull(true);
    if (fsElement()) return;                 // already fullscreen (the shell's button)
    const el = document.documentElement;
    const ask = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!ask) return;                        // CSS only
    try {
      const p = ask.call(el);
      requested.current = true;
      if (p && typeof p.then === "function") {
        p.catch(() => { requested.current = false; });   // refused: CSS only
      }
    } catch {
      requested.current = false;
    }
  }, []);

  const exitRef = useRef(exit);
  exitRef.current = exit;

  const toggle = useCallback(() => { (full ? exit : enter)(); }, [full, enter, exit]);

  // The browser left fullscreen by itself (Escape, its own control, F11):
  // the CSS layer follows, when that fullscreen was ours.
  useEffect(() => {
    const sync = () => {
      if (!fsElement() && requested.current) {
        requested.current = false;
        setFull(false);
      }
    };
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      document.removeEventListener("webkitfullscreenchange", sync);
    };
  }, []);

  // While on: <body> stops scrolling, and Escape leaves the CSS-only mode.
  // Capture on window, so this sees the key before a menu's own handler
  // closes the menu (and with it the evidence that the key was the menu's).
  useEffect(() => {
    if (!full) return undefined;
    document.body.classList.add("wf-fs-on");
    const onKey = (e) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ""))) return;
      if (blocked.current || document.querySelector(OVERLAY)) return;
      // Normally the browser keeps Escape to itself while it is fullscreen
      // (fullscreenchange above does the rest); where it hands it to the
      // page instead, the same exit runs, browser fullscreen included.
      exitRef.current();
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.body.classList.remove("wf-fs-on");
      window.removeEventListener("keydown", onKey, true);
    };
  }, [full]);

  // Leaving the page: whatever happened, <body> is restored (the effect
  // above) and the browser fullscreen this hook asked for ends.
  useEffect(() => () => {
    if (requested.current) {
      requested.current = false;
      if (fsElement()) exitBrowserFullscreen();
    }
  }, []);

  return { full, enter, exit, toggle };
}
