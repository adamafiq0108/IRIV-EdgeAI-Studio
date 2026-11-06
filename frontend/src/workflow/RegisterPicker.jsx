// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Modbus Read "Output points": a dropdown with a tick per register. Closed it
// says which registers are shown on the node; open it lists every register the
// node reads, and ticking one gives the node an output point for it.
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

/**
 * @param {object}   props
 * @param {number}   props.count     how many registers the node reads (Quantity)
 * @param {number}   props.start     the start address, for the "addr" hint
 * @param {number[]} props.picked    the ticked register indexes
 * @param {boolean}  props.editable
 * @param {function} props.onChange  (indexes) => void, never empty
 */
export default function RegisterPicker({ count, start, picked, editable, onChange }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const down = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    const key = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", down, true);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", down, true);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  const toggle = (k) => {
    const next = picked.includes(k) ? picked.filter((x) => x !== k) : [...picked, k];
    // At least one point stays: a node with no output is a dead end.
    if (next.length) onChange(next.sort((a, b) => a - b));
  };
  const all = Array.from({ length: count }, (_, k) => k);
  const summary = picked.length === count ? `All ${count} registers`
    : picked.map((k) => `[${k}]`).join(", ");

  return (
    <div ref={box} className={"wf-regdd" + (open ? " open" : "")}>
      <button type="button" className="input wf-regdd-btn" disabled={!editable}
              aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="wf-regdd-sum">{summary}</span>
        <span className="wf-regdd-count">{picked.length}/{count}</span>
        <ChevronDown size={15} aria-hidden="true" className="wf-regdd-chev" />
      </button>
      {open && (
        <div className="wf-regdd-menu" role="listbox" aria-multiselectable="true">
          <div className="wf-regdd-tools">
            <button type="button" className="wf-linkbtn" disabled={picked.length === count}
                    onClick={() => onChange(all)}>Select all</button>
            <button type="button" className="wf-linkbtn"
                    disabled={picked.length === 1 && picked[0] === 0}
                    onClick={() => onChange([0])}>Only [0]</button>
          </div>
          <div className="wf-regdd-list">
            {all.map((k) => {
              const on = picked.includes(k);
              const last = on && picked.length === 1;
              return (
                <button key={k} type="button" role="option" aria-selected={on}
                        className={"wf-regdd-row" + (on ? " on" : "")}
                        disabled={last} title={last ? "At least one register stays shown" : undefined}
                        onClick={() => toggle(k)}>
                  <span className="wf-regdd-box" aria-hidden="true">{on && <Check size={11} strokeWidth={3} />}</span>
                  <span className="wf-regdd-n">[{k}]</span>
                  <span className="wf-regdd-addr">addr {start + k}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
