// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The canvas context menu - Node-RED's, not the browser's.
//
// Right-click is the only place an operator looks for "what can I do here",
// so this is the full command surface: insert any node from the server
// registry at the click position, the clipboard, undo/redo, export, select
// all. Everything carries the keyboard shortcut that does the same thing, and
// anything that cannot act right now is greyed rather than hidden - a missing
// row teaches nothing, a greyed one teaches why.
//
// Three variants: `pane` (empty canvas), `node` (one node) and `selection`
// (a multi-selection). They share the clipboard/history rows; the node variant
// adds Duplicate / Disconnect wires / Delete for the node under the cursor.
import { useEffect, useRef, useState } from "react";
import { CATEGORY_STYLE } from "./registry.js";
import { clickable } from "../hooks/use-escape.js";

const PANEL = {
  background: "var(--surface2, #1d2026)", borderRadius: 8,
  border: "1px solid rgba(128,128,128,.3)", padding: 4,
  boxShadow: "0 6px 24px rgba(0,0,0,.4)",
};

function Row({ children, shortcut, onClick, danger, disabled, submenu, onHover }) {
  return (
    <div onClick={disabled ? undefined : onClick}
         {...clickable(() => onClick?.(), { role: "menuitem", disabled })}
         onMouseEnter={(e) => {
           onHover?.();
           if (!disabled) e.currentTarget.style.background = "rgba(128,128,128,.15)";
         }}
         onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
         style={{ display: "flex", alignItems: "center", gap: 12,
                  padding: "6px 12px", cursor: disabled ? "default" : "pointer",
                  fontSize: 13, borderRadius: 5, opacity: disabled ? 0.4 : 1,
                  whiteSpace: "nowrap",
                  color: danger ? "#ef4444" : "var(--text, #e8eaed)" }}>
      <span style={{ flex: 1, minWidth: 0 }}>{children}</span>
      {shortcut && (
        <span style={{ fontSize: 10.5, color: "var(--muted-text, #999)" }}>{shortcut}</span>
      )}
      {submenu && <span style={{ fontSize: 10, color: "var(--muted-text, #999)" }}>▸</span>}
    </div>
  );
}

const Sep = () => (
  <div style={{ height: 1, margin: "4px 6px",
                background: "rgba(128,128,128,.25)" }} />
);

/** Palette-shaped list of everything that can be inserted, by category.
 *
 * `order` is PALETTE_ORDER -- the order of the DRAWERS -- so a node has to be
 * placed by the category its palette declares, not by the category the
 * compiler wires it with. Filtering on `s.category` here was a silent hole:
 * every drawer that is palette-only (Board I/O, External Values, Schedule,
 * Math, Database, Messaging) came up empty, all 25 analytics nodes landed in
 * one flat list, and storage.query / storage.csv -- category "query", which
 * PALETTE_ORDER never names -- could not be inserted from this menu at all.
 */
function insertGroups(registry, order, zoneList, models, fnDefs) {
  const out = [];
  const paletteCat = (s) => s.palette?.cat || s.category;
  for (const cat of order || []) {
    const generic = Object.entries(registry || {})
      // ...and in every drawer it is ALSO listed in (palette.also_in: the
      // Delay under Common as well as Logic), as the palette draws it.
      .filter(([, s]) => s && (paletteCat(s) === cat
                               || (s.palette?.also_in || []).includes(cat))
                         && !s.deprecated && !s.hidden)
      .map(([type, s]) => ({ key: type, type, label: s.label }));
    let items = generic;
    if (cat === "filter") {
      // The zones drawn on this box, offered by name under the same Filters
      // heading the palette lists them in. APPENDED, not substituted: the
      // older code replaced the node types with the zones as soon as one zone
      // existed, which took the Class Filter off the insert menu on every box
      // that had got as far as drawing a zone.
      items = [...generic, ...(zoneList || []).map((z) => ({
        key: `zone:${z.id}`, type: "zone.ref", label: z.name,
        sub: z.kind, extra: { zone: z.id },
      }))];
    } else if (cat === "logic") {
      // The functions this canvas defines, offered by name -- each one a Call
      // already pointed at it. Same list the palette drawer shows, because a
      // menu that knows about fewer things than the drawer is a menu people
      // stop opening.
      items = [...generic, ...Object.values(fnDefs || {}).map((f) => ({
        key: `fn:${f.name}`, type: "function.call",
        label: `Call ${f.name}`, sub: f.params?.join(", "),
        extra: { fn: f.name },
      }))];
    } else if (cat === "source" && (models || []).length >= 2) {
      items = [...generic, ...models.map((m) => ({
        key: `model:${m}`, type: "source.detection",
        label: `Detection Stream - ${m}`, extra: { model: m },
      }))];
    }
    if (items.length) out.push({ cat, title: CATEGORY_STYLE[cat]?.title || cat, items });
  }
  return out;
}

export default function ContextMenu({
  menu, registry, order, zoneList, models, fnDefs,
  canUndo, canRedo, hasSelection, hasClipboard, editable = true,
  onClose, onInsert, onUndo, onRedo, onCut, onCopy, onPaste, onDelete,
  onExport, onSelectAll, onDuplicate, onDisconnect, onDeleteNode,
}) {
  const rootRef = useRef(null);
  const [insertOpen, setInsertOpen] = useState(false);
  const [flip, setFlip] = useState(false);
  const [find, setFind] = useState("");

  // Escape, or a click anywhere that is not this menu, closes it.
  useEffect(() => {
    if (!menu) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) onClose?.();
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onDown, true);
    };
  }, [menu, onClose]);

  useEffect(() => { setInsertOpen(false); setFind(""); }, [menu]);

  if (!menu) return null;

  const run = (fn) => () => { onClose?.(); fn?.(); };
  // The search box at the top of Insert narrows every drawer to the nodes whose name matches.
  const q = find.trim().toLowerCase();
  const groups = insertGroups(registry, order, zoneList, models, fnDefs)
    .map((g) => (q ? { ...g, items: g.items.filter((it) =>
      `${it.label} ${it.sub || ""} ${g.title}`.toLowerCase().includes(q)) } : g))
    .filter((g) => g.items.length);

  return (
    <div ref={rootRef}
         style={{ ...PANEL, position: "absolute", left: menu.x, top: menu.y,
                  zIndex: 20, minWidth: 190 }}
         onContextMenu={(e) => e.preventDefault()}>
      <div style={{ position: "relative" }}
           onMouseEnter={() => {
             if (!editable) return;
             const r = rootRef.current?.getBoundingClientRect();
             setFlip(!!r && r.right + 250 > window.innerWidth);
             setInsertOpen(true);
           }}
           onMouseLeave={() => setInsertOpen(false)}>
        <Row submenu disabled={!editable}>Insert</Row>
        {insertOpen && editable && (
          <div style={{ ...PANEL, position: "absolute", top: -4, zIndex: 21,
                        minWidth: 210, maxHeight: 340, overflowY: "auto",
                        ...(flip ? { right: "100%" } : { left: "100%" }) }}>
            <div style={{ position: "sticky", top: -4, zIndex: 1, padding: "4px 4px 6px",
                          margin: "-4px -4px 2px", background: "var(--surface2, #1d2026)" }}>
              <input className="input" autoFocus value={find} placeholder="Search nodes"
                     aria-label="Search nodes to insert" data-insert-search
                     onChange={(e) => setFind(e.target.value)}
                     onKeyDown={(e) => { if (e.key !== "Escape") e.stopPropagation(); }}
                     style={{ width: "100%", height: 30, fontSize: 13 }} />
            </div>
            {groups.length === 0 && (
              <div className="muted small" style={{ padding: "8px 10px" }}>No node matches.</div>
            )}
            {groups.map((g) => (
              <div key={g.cat}>
                <div className="muted small"
                     style={{ padding: "4px 10px 2px", fontWeight: 600,
                              color: CATEGORY_STYLE[g.cat]?.color || "var(--muted, #999)" }}>
                  {g.title}
                </div>
                {g.items.map((it) => (
                  <Row key={it.key}
                       onClick={run(() => onInsert?.(it.type, it.extra))}>
                    {it.label}{it.sub ? <span className="muted"> · {it.sub}</span> : null}
                  </Row>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      <Sep />
      <Row shortcut="Ctrl+Z" disabled={!editable || !canUndo}
           onClick={run(onUndo)}>Undo</Row>
      {/* Ctrl+Shift+Z is the one the menu names -- it is the shortcut every
          other page here advertises (Zones' redo button says it too), and
          the pair with Ctrl+Z is what an operator guesses. Ctrl+Y still
          works in the keyboard handler for anyone with the habit. */}
      <Row shortcut="Ctrl+Shift+Z" disabled={!editable || !canRedo}
           onClick={run(onRedo)}>Redo</Row>

      <Sep />
      <Row shortcut="Ctrl+X" disabled={!editable || !hasSelection}
           onClick={run(onCut)}>Cut</Row>
      <Row shortcut="Ctrl+C" disabled={!hasSelection}
           onClick={run(onCopy)}>Copy</Row>
      <Row shortcut="Ctrl+V" disabled={!editable || !hasClipboard}
           onClick={run(onPaste)}>Paste</Row>
      {/* One Delete: on a node it removes that node (or the whole selection when the node is
          part of one); elsewhere it removes the selection. */}
      {menu.variant !== "node" && (
        <Row shortcut="Del" danger disabled={!editable || !hasSelection}
             onClick={run(onDelete)}>Delete</Row>
      )}

      {menu.variant === "node" && (
        <>
          <Sep />
          <Row disabled={!editable} onClick={run(onDuplicate)}>Duplicate</Row>
          <Row disabled={!editable} onClick={run(onDisconnect)}>Disconnect wires</Row>
          <Row shortcut="Del" danger disabled={!editable} onClick={run(onDeleteNode)}>Delete</Row>
        </>
      )}

      <Sep />
      <Row shortcut="Ctrl+A" onClick={run(onSelectAll)}>Select all</Row>
      <Row onClick={run(onExport)}>
        {hasSelection ? "Export selection…" : "Export all…"}
      </Row>
    </div>
  );
}
