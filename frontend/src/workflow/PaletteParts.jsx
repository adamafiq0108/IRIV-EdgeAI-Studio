// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The pieces of the workflow palette the Node Guideline page shares with the
// canvas, so the two sidebars are one design: the drawer order, the search
// match, the collapsible drawer, its sub-heading and its item. Workflow.jsx
// and pages/NodeGuideline.jsx both take them from here.
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

export const PALETTE_ORDER = ["common",
                       "source", "filter", "board", "time",
                       // No analytics drawer: analytics is the server's
                       // WIRING category, and the last node still filed under
                       // it in the palette (Custom Formula) reads as Math. A
                       // one-node drawer called Functions, sitting next to a
                       // drawer full of functions, was a place to miss things.
                       "zone",
                       // Maths, its own drawer: the Formula's 34-operation
                       // picker became one node per operation, and thirteen
                       // of them in the Functions drawer would have buried
                       // the things that MEASURE under the things that
                       // calculate.
                       "math",
                       "logic", "variable",
                       "database", "dashboard", "output",
                       // Integration LAST, because it is the end of the end:
                       // a report goes out after everything else has decided
                       // there is something to report. It also holds the
                       // protocol families (MQTT, HTTP, API, Modbus) whole,
                       // reading half and sending half together, which is
                       // why the External Values drawer is gone.
                       "integration"];

// Does this node answer what was typed in the palette's search box? The
// label is the obvious half; the description and the sub-heading are the half
// that finds a node by what it DOES ("modbus", "http", "footage") rather than
// by a name you would have to know already. The type is in too, so
// "storage.query" pasted from a graph file finds its node.
export function nodeMatches(spec, type, q) {
  if (!q) return true;
  const hay = [spec.label, spec.description, spec.palette?.group, spec.group, type];
  return hay.some((v) => v && String(v).toLowerCase().includes(q));
}

/** One collapsible palette section, headed by its category's colour.
 *
 *  Nine categories of node do not fit on a laptop screen at once, so the
 *  palette became a scroll with no landmarks. A drawer per category gives the
 *  operator a way to put away what they are not using, and the colour bar is
 *  the same one the node carries on the canvas -- so the palette and the graph
 *  agree about what kind of thing something is.
 */
export function Drawer({ title, color, count, open, onToggle, icon: Icon, children }) {
  return (
    <div style={{ marginBottom: 6 }}>
      <button type="button" onClick={onToggle} className="wf-drawer"
              aria-expanded={open}
              style={{
                display: "flex", alignItems: "center", gap: 8, width: "100%",
                border: "1px solid transparent", borderRadius: 8,
                background: open ? "rgba(128,128,128,.10)" : "transparent",
                cursor: "pointer", color: "inherit", padding: "6px 8px",
                font: "inherit", textAlign: "left",
              }}>
        {/* The category's own icon and colour, the same pair the node wears on
            the canvas -- so the palette and the graph agree at a glance about
            what kind of thing this is. A colour bar alone made nine rows of
            identical text with a stripe. */}
        <span style={{
          width: 22, height: 22, borderRadius: 6, flexShrink: 0,
          display: "grid", placeItems: "center",
          background: `color-mix(in srgb, ${color} 20%, transparent)`,
          color,
        }}>
          {Icon ? <Icon size={13} /> : null}
        </span>
        <span style={{ fontSize: 12, fontWeight: 650, flex: 1, minWidth: 0,
                       overflow: "hidden", textOverflow: "ellipsis",
                       whiteSpace: "nowrap" }}>
          {title}
        </span>
        <span className="muted"
              style={{ fontSize: 11, fontVariantNumeric: "tabular-nums",
                       minWidth: 16, textAlign: "right" }}>
          {count}
        </span>
        <ChevronRight size={13} className="muted"
                      style={{ flexShrink: 0, transition: "transform .14s",
                               transform: open ? "rotate(90deg)" : "none" }} />
      </button>
      {open && <div style={{ marginTop: 5, paddingLeft: 2 }}>{children}</div>}
    </div>
  );
}

export function SubHead({ children }) {
  return (
    <div className="muted" style={{ fontSize: 11, fontWeight: 700,
                                    letterSpacing: ".05em",
                                    textTransform: "uppercase",
                                    margin: "6px 0 4px 3px" }}>
      {children}
    </div>
  );
}

export function PaletteItem({ color, label, sub, locked, selected, cursor, ...drag }) {
  // Name only. The description under every entry doubled the palette's
  // height and read as noise once the names were known; it survives as the
  // hover tooltip for whoever still wants it.
  return (
    <div {...drag} title={locked || sub || undefined}
         style={{ border: "1px solid rgba(128,128,128,.25)",
                  borderLeft: `3px solid ${color}`,
                  borderRadius: 7, padding: "6px 8px", marginBottom: 6,
                  cursor: locked ? "not-allowed" : (cursor || "grab"),
                  opacity: locked ? 0.55 : 1,
                  background: "var(--surface, transparent)",
                  ...(selected ? { boxShadow: `0 0 0 2px ${color}`, borderColor: color } : null) }}>
      <div style={{ fontSize: 12, fontWeight: 600 }}>{label}</div>
      {/* A locked item says WHY and where to go, in place. Greying it out and
          leaving the operator to guess is how a palette entry reads as
          broken rather than as not-ready-yet. */}
      {locked && (
        <div className="muted small" style={{ marginTop: 3 }}>
          {locked}{" "}
          <Link to="/database" style={{ textDecoration: "underline" }}>
            Database page
          </Link>
        </div>
      )}
    </div>
  );
}
