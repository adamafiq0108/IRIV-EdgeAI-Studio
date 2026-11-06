// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Pick which function a Call runs: a dropdown of every function defined on
// this canvas, with a search box. Used on the Call node's face and in its
// inspector, so the operator never has to remember or type a name.
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Search } from "lucide-react";

const FN_COLOR = "#F97316";

/**
 * @param {object}   props
 * @param {string}   props.value     the function name chosen now ("" = none)
 * @param {object}   props.fnDefs    {key: {name, params, returns}} (Workflow.jsx fnDefs)
 * @param {function} props.onPick    (name) => void
 * @param {boolean}  [props.disabled]
 * @param {boolean}  [props.compact] the node-face look: a small pill
 */
export default function FunctionPicker({ value, fnDefs, onPick, disabled, compact }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const box = useRef(null);
  const input = useRef(null);
  const list = useMemo(() => Object.values(fnDefs || {})
    .sort((a, b) => a.name.localeCompare(b.name)), [fnDefs]);
  const shown = list.filter((f) => !q.trim()
    || f.name.toLowerCase().includes(q.trim().toLowerCase()));
  const name = String(value || "").trim();
  const known = !!fnDefs?.[name.toLowerCase()];

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    document.addEventListener("pointerdown", close, true);
    setTimeout(() => input.current?.focus(), 0);
    return () => document.removeEventListener("pointerdown", close, true);
  }, [open]);

  const pick = (n) => { onPick(n); setOpen(false); setQ(""); };

  return (
    <div ref={box} className="nodrag nowheel" style={{ position: "relative", minWidth: 0,
                                                      flex: compact ? "0 1 auto" : 1 }}>
      <button type="button" disabled={disabled}
              onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
              title={disabled ? undefined : "Choose which function this Call runs"}
              className={compact ? "mono" : "input mono"}
              style={{
                display: "flex", alignItems: "center", gap: 6, width: compact ? "auto" : "100%",
                maxWidth: compact ? 160 : undefined, cursor: disabled ? "default" : "pointer",
                textAlign: "left", color: "inherit",
                ...(compact ? { fontSize: 12, padding: "2px 6px 2px 8px", borderRadius: 6,
                                border: `1px solid ${known || !name ? FN_COLOR + "66" : "#ef4444"}`,
                                background: FN_COLOR + "14" } : {}),
              }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                       flex: 1, color: name ? undefined : "var(--muted-text, #999)" }}>
          {name ? `${name}()` : "choose a function"}
        </span>
        <ChevronDown size={12} style={{ flexShrink: 0, opacity: 0.7 }} />
      </button>
      {open && (
        <div onClick={(e) => e.stopPropagation()}
             style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 50,
                      width: 240, borderRadius: 8, padding: 6,
                      background: "var(--surface, #fff)", color: "var(--text, #111)",
                      border: "1px solid rgba(128,128,128,.35)",
                      boxShadow: "0 8px 24px rgba(0,0,0,.18)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 6px",
                        borderRadius: 6, border: "1px solid rgba(128,128,128,.3)",
                        marginBottom: 6 }}>
            <Search size={13} style={{ opacity: 0.6, flexShrink: 0 }} />
            <input ref={input} value={q} onChange={(e) => setQ(e.target.value)}
                   placeholder="Search functions"
                   onKeyDown={(e) => {
                     e.stopPropagation();
                     if (e.key === "Escape") setOpen(false);
                     if (e.key === "Enter" && shown.length) pick(shown[0].name);
                   }}
                   style={{ border: 0, outline: "none", background: "transparent",
                            color: "inherit", fontSize: 12, width: "100%" }} />
          </div>
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            {shown.map((f) => {
              const on = f.name.toLowerCase() === name.toLowerCase();
              const params = (f.params || []).filter(Boolean);
              const rets = (f.returns || []).filter(Boolean);
              return (
                <button key={f.name} type="button" onClick={() => pick(f.name)}
                        style={{ display: "block", width: "100%", textAlign: "left",
                                 padding: "6px 8px", borderRadius: 6, border: 0,
                                 cursor: "pointer", color: "inherit",
                                 background: on ? FN_COLOR + "22" : "transparent" }}
                        onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = "rgba(128,128,128,.12)"; }}
                        onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = "transparent"; }}>
                  <div className="mono" style={{ fontSize: 12, fontWeight: 600 }}>{f.name}()</div>
                  <div style={{ fontSize: 10.5, color: "var(--muted-text, #888)" }}>
                    in: {params.join(", ") || "run"} → out: {rets.join(", ") || "result"}
                  </div>
                </button>
              );
            })}
            {!shown.length && (
              <div style={{ fontSize: 11.5, color: "var(--muted-text, #888)", padding: "6px 8px" }}>
                {list.length ? "No function matches." : "No functions yet - drop a Function from the palette first."}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
