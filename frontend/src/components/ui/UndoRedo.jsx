// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// UndoRedo - one joined control, on every page that has a history.
//
// Two arrows glued into a single segmented unit rather than two loose
// buttons: undo and redo are one faculty with two directions, and a pair
// that reads as one object is easier to find on a busy toolbar than two
// characters set in a text font. The glyphs are proper icons at a fixed box
// size, so they line up with the other icon buttons instead of sitting on
// whatever baseline "↶" happens to have.
//
// A direction with no history is DISABLED, not hidden: the control keeps its
// shape and its place, and a greyed arrow says "nothing to undo" where a
// missing one would just look like a different toolbar.
import { Undo2, Redo2 } from "lucide-react";

export default function UndoRedo({ canUndo, canRedo, onUndo, onRedo,
                                   size = "sm" }) {
  const pad = size === "sm" ? "5px 9px" : "7px 11px";
  const btn = (on, act, icon, label, side) => (
    <button type="button" disabled={!on} onClick={act}
            title={label} aria-label={label}
            style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              padding: pad, lineHeight: 0, cursor: on ? "pointer" : "default",
              background: "transparent", color: "inherit",
              border: "1px solid var(--line, rgba(128,128,128,.28))",
              // Joined: the shared edge is drawn once, so the pair reads as
              // one control rather than two buttons that happen to touch.
              borderRight: side === "left" ? "none" : undefined,
              borderRadius: side === "left" ? "8px 0 0 8px" : "0 8px 8px 0",
              opacity: on ? 1 : 0.4,
              transition: "background .12s, color .12s",
            }}
            onMouseEnter={(e) => { if (on) {
              e.currentTarget.style.background = "rgba(128,128,128,.14)";
              e.currentTarget.style.color = "var(--accent, #04bade)";
            } }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "inherit";
            }}>
      {icon}
    </button>
  );
  return (
    <span style={{ display: "inline-flex", alignItems: "stretch" }}>
      {btn(canUndo, onUndo, <Undo2 size={15} />, "Undo (Ctrl+Z)", "left")}
      {btn(canRedo, onRedo, <Redo2 size={15} />, "Redo (Ctrl+Shift+Z)", "right")}
    </span>
  );
}
