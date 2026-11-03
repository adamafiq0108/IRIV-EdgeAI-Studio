// SearchList - a dropdown whose search box is INSIDE the list.
//
// A native <select> cannot be searched, so every picker here used to pair one
// with a text box ABOVE it. That reads as a filter on the PAGE rather than on
// the list: you have to notice it before opening anything, it takes vertical
// space in a panel that has none to spare, and on a node inspector three of
// them stack into a wall of identical boxes.
//
// So the field is a button showing the current choice, and opening it reveals
// a panel whose FIRST ROW is the search. You look at the list, you type, the
// list narrows. One control, searched where it is read.
//
// Used by every picker on the canvas: the variable pickers on each node, the
// Formula operation library, and the insert-variable list in the expression
// editor. Optional groups render as sticky-ish section headings; without them
// it is one flat list.
import { useEffect, useRef, useState } from "react";

/**
 * @param {object}   o
 * @param {string}   o.value        currently selected key ("" for none)
 * @param {Array}    o.items        [{key, label, hint, group}]
 * @param {string[]} o.groups       optional group order; ungrouped items last
 * @param {string}   o.placeholder  button text when nothing is selected
 * @param {string}   o.searchLabel  placeholder inside the search box
 * @param {boolean}  o.editable
 * @param {function} o.onPick       (key) => void
 * @param {node}     o.footer       extra rows under the list (e.g. "+ New…")
 * @param {boolean}  o.keepOpen     do not close after picking (insert lists)
 */
export default function SearchList({
  value, items = [], groups = null, placeholder = "- choose -",
  searchLabel = "Search…", editable = true, onPick, footer = null,
  keepOpen = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const boxRef = useRef(null);
  const searchRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (!boxRef.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    // Searching is the reason to open it, so the caret starts there.
    searchRef.current?.focus();
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const q = query.trim().toLowerCase();
  const hit = (it) => !q
    || String(it.key || "").toLowerCase().includes(q)
    || String(it.label || "").toLowerCase().includes(q)
    || String(it.hint || "").toLowerCase().includes(q);
  const matched = items.filter(hit);

  // Grouped or flat, in the order the caller gave.
  const order = groups && groups.length ? groups : [null];
  const sections = order
    .map((g) => [g, matched.filter((it) => (it.group || null) === (g || null))])
    .filter(([, list]) => list.length);
  const ungrouped = groups && groups.length
    ? matched.filter((it) => !groups.includes(it.group))
    : [];
  if (ungrouped.length) sections.push([null, ungrouped]);

  const current = items.find((it) => it.key === value);

  return (
    <div ref={boxRef} style={{ position: "relative" }}>
      <button type="button" className="input" disabled={!editable}
              onClick={() => { setQuery(""); setOpen((v) => !v); }}
              title={current?.key || ""}
              style={{ width: "100%", textAlign: "left", cursor: "pointer",
                       display: "flex", justifyContent: "space-between",
                       alignItems: "center", gap: 8, overflow: "hidden" }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                       whiteSpace: "nowrap" }}>
          {current ? current.label : placeholder}
        </span>
        <span className="muted" style={{ flexShrink: 0 }}>▾</span>
      </button>
      {open && (
        <div style={{
          position: "absolute", zIndex: 40, top: "calc(100% + 4px)", left: 0,
          right: 0, maxHeight: 320, overflowY: "auto",
          background: "var(--surface2, #1d2026)",
          border: "1px solid var(--border, #333)", borderRadius: 8,
          boxShadow: "0 10px 30px rgba(0,0,0,.45)",
        }}>
          <div style={{ position: "sticky", top: 0, zIndex: 1, padding: 6,
                        background: "var(--surface2, #1d2026)",
                        borderBottom: "1px solid var(--border, #333)" }}>
            <input ref={searchRef} className="input" value={query}
                   placeholder={searchLabel}
                   onChange={(e) => setQuery(e.target.value)} />
          </div>
          {sections.map(([group, list]) => (
            <div key={group || "_"}>
              {group && (
                <div className="muted small"
                     style={{ padding: "6px 10px 2px", letterSpacing: ".04em",
                              textTransform: "uppercase" }}>{group}</div>
              )}
              {list.map((it) => (
                <button key={it.key} type="button" title={it.hint || it.key}
                        onClick={() => {
                          onPick(it.key);
                          if (!keepOpen) setOpen(false);
                        }}
                        style={{
                          display: "block", width: "100%", textAlign: "left",
                          padding: "6px 10px", border: 0, cursor: "pointer",
                          background: it.key === value
                            ? "color-mix(in srgb, var(--accent, #04bade) 22%, transparent)"
                            : "transparent",
                          color: "inherit", font: "inherit",
                          overflow: "hidden", textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}>
                  {it.label}
                </button>
              ))}
            </div>
          ))}
          {!sections.length && (
            <p className="muted small" style={{ padding: 10 }}>
              Nothing matches “{query}”.
            </p>
          )}
          {footer && (
            <div style={{ borderTop: "1px solid var(--border, #333)", padding: 4 }}
                 onClick={() => !keepOpen && setOpen(false)}>
              {footer}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
