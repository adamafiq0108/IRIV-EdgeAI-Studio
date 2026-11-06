// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Quick add: drop a wire on empty canvas and pick the node it should go to.
//
// Dragging a wire out of a port and letting go on nothing used to do nothing,
// so building a chain meant: let go, open the palette, find the node, drag it
// out, drag the wire again. This opens a searchable list right where the wire
// was dropped, offering ONLY the nodes that wire can legally reach (the same
// edgeAllowed rule the canvas enforces), and picking one adds it there with
// the wire already on it.
import { useEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";
import { CATEGORY_STYLE } from "./registry.js";

/**
 * @param {object}   props
 * @param {object}   props.at        {x, y} inside the canvas wrapper, where to open
 * @param {Array}    props.items     [{type, label, sub, cat}] the nodes that can connect
 * @param {string[]} props.order     drawer order (PALETTE_ORDER), for grouping
 * @param {string}   props.title     what the wire comes from ("from Class Filter")
 * @param {function} props.onPick    (type) => void
 * @param {function} props.onClose   () => void
 */
export default function QuickAdd({ at, items, order, title, onPick, onClose }) {
  const [q, setQ] = useState("");
  const [hot, setHot] = useState(0);
  const box = useRef(null);
  const input = useRef(null);
  const listRef = useRef(null);

  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    const hits = items.filter((it) => !t || it.label.toLowerCase().includes(t)
      || it.type.toLowerCase().includes(t) || (it.sub || "").toLowerCase().includes(t));
    const rank = (c) => { const i = (order || []).indexOf(c); return i < 0 ? 99 : i; };
    return hits.sort((a, b) => rank(a.cat) - rank(b.cat) || a.label.localeCompare(b.label));
  }, [items, q, order]);

  useEffect(() => { setHot(0); }, [q]);
  useEffect(() => {
    setTimeout(() => input.current?.focus(), 0);
    const down = (e) => { if (box.current && !box.current.contains(e.target)) onClose(); };
    document.addEventListener("pointerdown", down, true);
    return () => document.removeEventListener("pointerdown", down, true);
  }, [onClose]);
  // Keep the highlighted row in view while arrowing through a long list.
  useEffect(() => {
    listRef.current?.querySelector(`[data-qa-i="${hot}"]`)?.scrollIntoView({ block: "nearest" });
  }, [hot]);

  const key = (e) => {
    e.stopPropagation();
    if (e.key === "Escape") { onClose(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setHot((h) => Math.min(shown.length - 1, h + 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setHot((h) => Math.max(0, h - 1)); }
    if (e.key === "Enter" && shown[hot]) { e.preventDefault(); onPick(shown[hot].type); }
  };

  let lastCat = null;
  return (
    <div ref={box} className="wf-quickadd nodrag nowheel" data-wf-quickadd
         style={{ left: at.x, top: at.y }} onKeyDown={key}>
      <div className="wf-quickadd-head">
        <Search size={14} aria-hidden="true" />
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="Search nodes to connect" aria-label="Search nodes to connect" />
      </div>
      {title && <div className="wf-quickadd-sub">{title}</div>}
      <div ref={listRef} className="wf-quickadd-list" role="listbox">
        {shown.map((it, i) => {
          const style = CATEGORY_STYLE[it.cat] || {};
          const head = it.cat !== lastCat;
          lastCat = it.cat;
          return (
            <div key={it.type}>
              {head && !q.trim() && <div className="wf-quickadd-cat">{style.title || it.cat}</div>}
              <button type="button" role="option" aria-selected={i === hot} data-qa-i={i}
                      className={"wf-quickadd-row" + (i === hot ? " on" : "")}
                      onMouseEnter={() => setHot(i)} onClick={() => onPick(it.type)}>
                <span className="wf-quickadd-dot" style={{ background: style.color || "#94a3b8" }} />
                <span className="wf-quickadd-label">{it.label}</span>
                {q.trim() && <span className="wf-quickadd-in">{style.title}</span>}
              </button>
            </div>
          );
        })}
        {!shown.length && (
          <div className="wf-quickadd-empty">
            {items.length ? "No node matches." : "Nothing can be wired from here."}
          </div>
        )}
      </div>
      <div className="wf-quickadd-foot">↑ ↓ to choose · Enter to add · Esc to close</div>
    </div>
  );
}
