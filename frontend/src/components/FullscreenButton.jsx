// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// FullscreenButton - hand the whole screen to the page.
//
// These boxes get mounted on a wall or a shop-floor panel PC, where the
// browser chrome is a permanent strip of nothing: a tab bar and an address
// nobody will ever type again, eating the top of a 10-inch panel that is
// showing a live feed. This gives that back.
//
// It reports the real state rather than remembering what it asked for.
// Fullscreen can end without this button: Escape, the browser's own control,
// a window manager. A toggle that tracked its own clicks would then show
// "exit" on a page that is not fullscreen, so the state comes from the
// fullscreenchange event and nowhere else.
import { useEffect, useState } from "react";
import { Maximize, Minimize } from "lucide-react";
import { Button } from "./ui/button.jsx";

/** Whether the document is fullscreen right now, across the vendor spellings
 *  still shipped by Safari and older WebKit panels. */
function current() {
  if (typeof document === "undefined") return false;
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

export default function FullscreenButton() {
  const [on, setOn] = useState(current);
  // A browser or a kiosk policy may forbid it outright. Rendering a button
  // that silently does nothing is worse than rendering none.
  const [can] = useState(() => typeof document !== "undefined"
    && !!(document.documentElement?.requestFullscreen
          || document.documentElement?.webkitRequestFullscreen));

  useEffect(() => {
    const sync = () => setOn(current());
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      document.removeEventListener("webkitfullscreenchange", sync);
    };
  }, []);

  if (!can) return null;

  async function toggle() {
    try {
      if (current()) {
        await (document.exitFullscreen?.() ?? document.webkitExitFullscreen?.());
      } else {
        const el = document.documentElement;
        await (el.requestFullscreen?.() ?? el.webkitRequestFullscreen?.());
      }
    } catch {
      // Refused (no user gesture in the eyes of the browser, an iframe
      // without allowfullscreen, a kiosk policy). The event listener above
      // keeps the icon honest either way, so there is nothing to correct.
    }
  }

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={toggle}
      aria-label={on ? "Leave full screen" : "Full screen"}
      title={on ? "Leave full screen (Esc)" : "Full screen"}
    >
      {on ? <Minimize /> : <Maximize />}
    </Button>
  );
}
