// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// A panel-card whose body folds away behind its own header.
//
// Pages that stack several tall cards (AI Models) otherwise make the lower
// ones a long scroll away. The open/closed choice is an operator preference,
// so it persists per card in localStorage under the caller's key -- same
// convention as the workflow palette width ("wf.palette.w").
//
// The body is HIDDEN, never unmounted: a card can hold a half-filled form, an
// inline editor or a save in flight, and unmounting would silently throw that
// away the moment someone folded the card to look at the one below it.
import { useEffect, useState } from "react";

// `actions` sit at the header's right edge, OUTSIDE the fold button, so a
// click on one does not also fold the card.
export default function CollapsibleCard({ storageKey, title, chips, actions, children }) {
  // Expanded by default -- on a first visit nothing must look like it is
  // missing from the page.
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem(storageKey) !== "0"; } catch { return true; }
  });
  useEffect(() => {
    try { localStorage.setItem(storageKey, open ? "1" : "0"); } catch { /* ignore */ }
  }, [storageKey, open]);

  return (
    <div className="panel-card">
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
                style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0,
                         background: "none", border: "none", padding: 0, margin: 0,
                         font: "inherit", color: "inherit", textAlign: "left",
                         cursor: "pointer", flexWrap: "wrap" }}>
          <span aria-hidden="true"
                style={{ display: "inline-block", fontSize: 11, lineHeight: 1,
                         transition: "transform .15s ease",
                         transform: `rotate(${open ? 90 : 0}deg)` }}>▶</span>
          <span className="section-title">{title}</span>
          {chips}
        </button>
        {actions && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "none" }}>
            {actions}
          </div>
        )}
      </div>
      <div hidden={!open} style={{ marginTop: 12 }}>{children}</div>
    </div>
  );
}
