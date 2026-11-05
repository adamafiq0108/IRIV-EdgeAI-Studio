// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// ViewSettings - the builder canvas's own display preferences.
//
// These are preferences about the EDITOR, not about the dashboard being
// edited: nothing here is saved into a layout or sent to the server, so
// toggling one can never change what an operator sees on the finished page.
// That is why they live in localStorage rather than in the layout document.
//
// Deliberately only the settings that DO something. The template this was
// modelled on offers Smart Filters / Group Totals / Pinned Columns, which
// belong to a data-grid product; wiring them here would have meant three
// switches that move and change nothing, which is worse than not offering
// them. Two real ones is what this canvas actually has to vary -- the grid's
// SIZE is not among them: that belongs to the page, saves with it, and is
// chosen in the builder's header.
import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";

const KEY = "builder.view";
const DEFAULTS = { guides: true, livePreview: true };

function read() {
  try {
    return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(KEY) || "{}") || {}) };
  } catch { return { ...DEFAULTS }; }
}

/** Shared state: the popover writes it, the canvas reads it, and a `storage`
 *  event keeps two open tabs from disagreeing about the same preference. */
export function useViewSettings() {
  const [value, setValue] = useState(read);
  useEffect(() => {
    const onStorage = (e) => { if (e.key === KEY) setValue(read()); };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  const update = (patch) => {
    setValue((cur) => {
      const next = { ...cur, ...patch };
      try { localStorage.setItem(KEY, JSON.stringify(next)); }
      catch { /* private mode: the setting still applies, it just forgets */ }
      return next;
    });
  };
  return [value, update];
}

function Toggle({ label, hint, checked, onChange }) {
  return (
    <label style={{ display: "flex", alignItems: "flex-start", gap: 10,
                    padding: "8px 2px", cursor: "pointer" }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600 }}>{label}</div>
        {hint && <div className="muted small">{hint}</div>}
      </div>
      <input type="checkbox" checked={checked} style={{ marginTop: 3 }}
             onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

export default function ViewSettings() {
  const [value, update] = useViewSettings();
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div ref={boxRef} style={{ position: "relative", display: "inline-block" }}>
      <button type="button" className={"btn btn-sm" + (open ? " btn-accent" : "")}
              onClick={() => setOpen((v) => !v)} title="Canvas view settings"
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <SlidersHorizontal size={13} /> View
      </button>

      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 60,
          width: 260, padding: "8px 12px 12px",
          background: "var(--surface2, #1d2026)",
          border: "1px solid rgba(128,128,128,.3)", borderRadius: 10,
          boxShadow: "0 12px 32px rgba(0,0,0,.45)",
        }}>
          <div className="section-title" style={{ marginBottom: 2 }}>Canvas</div>
          <Toggle label="Grid guides" hint="Faint cell lines while arranging."
                  checked={value.guides}
                  onChange={(v) => update({ guides: v })} />
          <Toggle label="Live preview"
                  hint="Render real data. Off is faster on a heavy dashboard."
                  checked={value.livePreview}
                  onChange={(v) => update({ livePreview: v })} />

          {/* DENSITY WAS HERE, and it did nothing: two buttons, a stored
              value, and not one line of code that read it. The real control
              is Small / Medium / Big in the builder's own header -- it sets
              the row height, and it saves with the PAGE so the finished
              dashboard matches what was arranged. A second, local, silent
              "density" beside it would be a setting that contradicts the one
              that works. */}

          <div style={{ display: "flex", marginTop: 12, paddingTop: 8,
                        borderTop: "1px solid rgba(128,128,128,.2)" }}>
            <button type="button" className="btn btn-sm"
                    onClick={() => update(DEFAULTS)}>Reset</button>
          </div>
        </div>
      )}
    </div>
  );
}
