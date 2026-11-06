// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Modbus frame builder: the Advanced editor of a Modbus Read and a Modbus Write.
//
// ID, FC and CRC are fixed by Modbus RTU (first, second, last). Between them
// the fields can be reordered (drag the handle -- mouse or touch -- or use the
// arrows), resized, byte-swapped, and joined by manufacturer-specific fields
// such as a password. The byte map, CRC and hex frame update on every change.
// Save writes the layout onto the node; the box then sends exactly these bytes.
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, ArrowUp, GripVertical, Lock, Plus, X } from "lucide-react";
import {
  FIELD_SIZES, buildFrame, cleanHex, frameText, frameWarnings, hex2, standardFields,
  toBytes, writeData, writeFields,
} from "./modbusFrame.js";

//: A Write's standard function codes, and the most one request may carry.
const WRITE_FCS = [5, 6, 15, 16];
const WRITE_MAX = { 15: 1968, 16: 123 };
//: Fields whose value comes from the write itself, not from the editor.
const AUTO = ["val", "bc"];

let nextKey = 1;
const uid = () => `f${Date.now().toString(36)}${(nextKey += 1)}`;

/** The node's current values as editor rows (ST / Q carry address / quantity). */
function rowsFrom(d, write) {
  const saved = Array.isArray(d.frame?.fields) && d.frame.fields.length ? d.frame.fields
    : write ? writeFields(d.fc ?? 6) : standardFields();
  return saved.map((f) => {
    const size = FIELD_SIZES.includes(Number(f.size)) ? Number(f.size) : 2;
    const pad = (n) => Number(n || 0).toString(16).toUpperCase().padStart(size * 2, "0");
    const hex = f.key === "st" ? pad(d.address ?? 0)
      : f.key === "q" ? pad(d.quantity ?? 1)
      : AUTO.includes(f.key) ? ""
      : (cleanHex(f.hex) ?? "00");
    return { uid: uid(), key: f.key, name: f.name, desc: f.desc || "", size,
             order: f.order === "lo" ? "lo" : "hi", hex };
  });
}

/** A row the standard fixes in place (ID, FC, CRC): lock, no handle, no
 *  actions. Top level, not inside the editor -- a component defined inside
 *  would remount on every keystroke and its input would lose focus. */
function Locked({ name, desc, size, order, children, err }) {
  return (
    <div className="fb-row is-locked">
      <span className="fb-grip" title="Fixed by the Modbus RTU standard"><Lock size={13} /></span>
      <div className="fb-name">
        <strong>{name}</strong>
        <span className="muted small">{desc}</span>
      </div>
      <span className="fb-fixed">{size}</span>
      <span className="fb-fixed">{order}</span>
      <div className="fb-val">{children}{err && <span className="small c-warn">{err}</span>}</div>
      <span className="fb-acts"><span className="ui-badge ui-badge-muted">Locked</span></span>
    </div>
  );
}

/**
 * @param {object}   props
 * @param {object}   props.data      the node's data (unit, fc, address, quantity, frame)
 * @param {boolean}  props.editable
 * @param {boolean}  props.serial    the node's server is RTU (serial)
 * @param {function} props.onSave    (patch) => void
 * @param {function} props.onClose   () => void
 * @param {string}   props.mode      "read" (Modbus Read) or "write" (Modbus Write)
 */
export default function ModbusFrameEditor({ data, editable, serial, onSave, onClose, mode = "read" }) {
  const d = data || {};
  const write = mode === "write";
  // The node's own FC decides how a Write's data is laid out (a coil, a
  // register, a block); the frame's FC byte may differ for a vendor device.
  const fcStd = write ? (WRITE_FCS.includes(Number(d.fc)) ? Number(d.fc) : 6) : Number(d.fc ?? 3);
  const [id, setId] = useState(String(d.unit === "" || d.unit == null ? 1 : d.unit));
  const [fc, setFc] = useState(hex2(Number(d.frame?.fc ? parseInt(d.frame.fc, 16) : fcStd)));
  const [rows, setRows] = useState(() => rowsFrom(d, write));
  // A Write's value comes from its wire at run time; this one only fills the
  // preview so the byte map shows where it lands.
  const [sample, setSample] = useState(fcStd === 5 || fcStd === 15 ? "1" : "100");
  const [drag, setDrag] = useState(null);       // uid being dragged
  const [copied, setCopied] = useState(false);
  const listRef = useRef(null);

  useEffect(() => {
    const key = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);

  // -- validation: an invalid input is shown and NOT applied to the preview --
  const idNum = /^\d+$/.test(id.trim()) ? Number(id) : NaN;
  const idErr = Number.isInteger(idNum) && idNum >= 0 && idNum <= 255 ? "" : "Enter a number 0-255";
  const fcHex = cleanHex(fc);
  const fcErr = fcHex !== null && fcHex.length >= 1 && fcHex.length <= 2 ? "" : "Hex 00-FF";
  const qMax = write ? (WRITE_MAX[fcStd] || 65535) : 32;
  const rowErr = (r) => {
    if (AUTO.includes(r.key)) return "";
    const h = cleanHex(r.hex);
    if (h === null || h === "") return "Not hex";
    if (h.replace(/^0+/, "").length > r.size * 2) return `Too big for ${r.size} byte${r.size > 1 ? "s" : ""}`;
    if (r.key === "q") {
      const q = parseInt(h, 16);
      if (q < 1 || q > qMax) return write ? `FC ${fcStd} writes 1-${qMax} at once` : "This node reads 1-32 registers";
    }
    if (r.key === "st" && parseInt(h, 16) > 0xffff) return "Address is 0000-FFFF";
    if (r.key === "custom" && !String(r.name || "").trim()) return "Name it";
    return "";
  };
  const errors = rows.map(rowErr);
  const anyErr = !!idErr || !!fcErr || errors.some(Boolean);

  // The last VALID values feed the preview, so a half-typed field never
  // produces a wrong frame on screen.
  const lastGood = useRef({ id: 1, fc: "03", rows: {} });
  if (!idErr) lastGood.current.id = idNum;
  if (!fcErr) lastGood.current.fc = fcHex;
  rows.forEach((r, i) => { if (!errors[i]) lastGood.current.rows[r.uid] = cleanHex(r.hex); });
  // A Write's wdata and byte count, from the test value and the quantity.
  const qRow = rows.find((r) => r.key === "q");
  const quantity = write && (fcStd === 15 || fcStd === 16)
    ? (lastGood.current.rows[qRow?.uid] ? parseInt(lastGood.current.rows[qRow.uid], 16) : Number(d.quantity) || 1) : 1;
  const wdata = write ? writeData(fcStd, sample, quantity) : [];
  const autoField = (r) => {
    if (r.key === "bc") return { ...r, hex: wdata.length.toString(16) };
    // A block (FC 15 / 16) goes as is, each register swapped for Lo -> Hi.
    if (fcStd === 15 || fcStd === 16) {
      const raw = r.order === "lo" && fcStd === 16
        ? wdata.reduce((acc, b, i) => (i % 2 ? acc : acc.concat([wdata[i + 1], b])), []) : wdata;
      return { ...r, raw };
    }
    const value = wdata.reduce((acc, b) => acc * 256 + b, 0);
    return { ...r, raw: toBytes(value, r.size, r.order) };
  };
  const frameIn = {
    id: lastGood.current.id, fc: lastGood.current.fc,
    fields: rows.map((r) => (AUTO.includes(r.key) ? autoField(r)
      : { ...r, hex: lastGood.current.rows[r.uid] ?? "0" })),
  };
  const built = useMemo(() => buildFrame(frameIn), [JSON.stringify(frameIn)]); // eslint-disable-line react-hooks/exhaustive-deps
  const warnings = frameWarnings(frameIn, write);
  const text = frameText(built.bytes);

  const patch = (u, p) => setRows((rs) => rs.map((r) => (r.uid === u ? { ...r, ...p } : r)));
  const move = (from, to) => setRows((rs) => {
    if (to < 0 || to >= rs.length || from === to) return rs;
    const next = rs.slice();
    const [it] = next.splice(from, 1);
    next.splice(to, 0, it);
    return next;
  });
  const resize = (r, size) => {
    // Keep the value, re-padded to the new width.
    const h = (cleanHex(r.hex) || "0").replace(/^0+/, "") || "0";
    patch(r.uid, { size, hex: h.padStart(size * 2, "0").slice(-size * 2) });
  };
  const addField = () => setRows((rs) => [...rs, {
    uid: uid(), key: "custom", name: "PASSWORD", desc: "Vendor field", size: 2, order: "hi", hex: "0000",
  }]);
  const reset = () => {
    setFc(hex2(fcStd));
    setRows(rowsFrom({ ...d, frame: null }, write));
  };

  // -- drag to reorder: pointer events, so a finger works as well as a mouse --
  const onGripDown = (e, r) => {
    if (!editable) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setDrag(r.uid);
  };
  const onGripMove = (e) => {
    if (!drag || !listRef.current) return;
    const rowsEls = [...listRef.current.querySelectorAll("[data-fb-row]")];
    const from = rowsEls.findIndex((el) => el.dataset.fbRow === drag);
    let to = from;
    rowsEls.forEach((el, i) => {
      const b = el.getBoundingClientRect();
      if (e.clientY > b.top && e.clientY < b.bottom) to = i;
    });
    if (to !== from && from >= 0) move(from, to);
  };
  const onGripUp = () => setDrag(null);

  const save = () => {
    if (anyErr) return;
    const fcN = parseInt(fcHex, 16);
    const st = rows.find((r) => r.key === "st");
    const q = rows.find((r) => r.key === "q");
    // The standard layout needs no custom frame: the node works as before.
    const std = write ? writeFields(fcStd) : standardFields();
    const standard = fcN === fcStd && rows.length === std.length
      && rows.every((r, i) => r.key === std[i].key && r.size === std[i].size && r.order === std[i].order);
    onSave({
      unit: idNum,
      ...(write ? {} : fcN >= 1 && fcN <= 4 ? { fc: fcN } : {}),
      ...(st ? { address: parseInt(cleanHex(st.hex), 16) } : {}),
      ...(q ? { quantity: parseInt(cleanHex(q.hex), 16) } : {}),
      // The standard layout needs no custom frame: the node reads normally.
      frame: standard ? null : {
        custom: true, fc: hex2(fcN),
        fields: rows.map((r) => ({
          key: r.key, name: String(r.name || "").trim().slice(0, 16), desc: r.desc || "",
          size: r.size, order: r.order,
          ...(r.key === "custom" ? { hex: cleanHex(r.hex) } : {}),
        })),
      },
    });
    onClose();
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1400); }
    catch { /* clipboard blocked (http): the text is selectable */ }
  };


  return createPortal(
    <div className="ui-dialog-backdrop fb-backdrop"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="ui-dialog fb-dialog" role="dialog" aria-modal="true" aria-label="Modbus frame builder">
        <div className="ui-dialog-head">
          <h3>Modbus frame builder</h3>
          <button type="button" className="toast-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="ui-dialog-body">
          <p className="muted small" style={{ margin: 0 }}>
            ID, FC and CRC are fixed by the Modbus RTU standard. Arrange the fields between them
            to match your device manual - drag the handle or use the arrows - and set each
            field&apos;s size and byte order. The frame and CRC update as you go.
          </p>
          {!serial && (
            <p className="small c-warn" style={{ margin: 0 }}>
              This node&apos;s server is TCP. A custom frame is only sent on an RTU (serial)
              server; over TCP the node keeps {write ? "writing" : "reading"} the standard way.
            </p>
          )}

          {write && (
            <label className="ui-field fb-sample">
              <span className="wf-field-label">Test value</span>
              <input className="input" value={sample} inputMode="numeric" style={{ maxWidth: 160 }}
                     onChange={(e) => setSample(e.target.value.replace(/[^0-9-]/g, ""))} />
              <span className="muted small">
                Only for the preview. The real value comes from the wire into the node
                {fcStd === 15 || fcStd === 16 ? `, written to all ${quantity} ${fcStd === 15 ? "coils" : "registers"}` : ""}.
              </span>
            </label>
          )}

          <div className="fb-section">
            <div className="fb-section-head">
              <span className="wf-field-label">Frame layout</span>
              <button type="button" className="btn btn-sm" disabled={!editable || rows.length >= 10} onClick={addField}>
                <Plus size={13} /> Add field
              </button>
            </div>
            <div className="fb-cols muted">
              <span /><span>Field</span><span>Bytes</span><span>Order</span><span>Value (hex)</span><span />
            </div>
            <Locked name="ID" desc="Slave address, 0-255" size="1"
                    order={idErr ? "-" : `0x${hex2(idNum & 255)}`} err={idErr}>
              <input className="input" value={id} disabled={!editable} inputMode="numeric"
                     onChange={(e) => setId(e.target.value)} aria-label="ID (decimal)" />
            </Locked>
            <Locked name="FC" desc="Function code" size="1" order="-" err={fcErr}>
              <input className="input mono" value={fc} disabled={!editable} maxLength={4}
                     onChange={(e) => setFc(e.target.value)} aria-label="FC (hex)" />
            </Locked>

            <div ref={listRef} className="fb-list">
              {rows.map((r, i) => (
                <div key={r.uid} data-fb-row={r.uid}
                     className={"fb-row" + (r.key === "custom" ? " is-custom" : "") + (drag === r.uid ? " is-dragging" : "")}>
                  <span className="fb-grip fb-handle" title="Drag to reorder"
                        onPointerDown={(e) => onGripDown(e, r)} onPointerMove={onGripMove}
                        onPointerUp={onGripUp} onPointerCancel={onGripUp}>
                    <GripVertical size={15} />
                  </span>
                  <div className="fb-name">
                    {r.key === "custom" ? (
                      <input className="input mono fb-namein" value={r.name} maxLength={16} disabled={!editable}
                             onChange={(e) => patch(r.uid, { name: e.target.value.toUpperCase() })}
                             aria-label="Field name" />
                    ) : <strong>{r.name}</strong>}
                    {r.key !== "custom" && <span className="muted small">{r.desc}</span>}
                  </div>
                  {r.key === "val" && (fcStd === 15 || fcStd === 16) ? (
                    <span className="fb-fixed" title="As long as the data">{wdata.length}</span>
                  ) : (
                    <select className="input" value={r.size} disabled={!editable}
                            onChange={(e) => resize(r, Number(e.target.value))} aria-label="Size in bytes">
                      {FIELD_SIZES.map((sz) => <option key={sz} value={sz}>{sz}</option>)}
                    </select>
                  )}
                  <select className="input" value={r.order} disabled={!editable}
                          onChange={(e) => patch(r.uid, { order: e.target.value })} aria-label="Byte order">
                    <option value="hi">Hi → Lo</option>
                    <option value="lo">Lo → Hi</option>
                  </select>
                  <div className="fb-val">
                    {AUTO.includes(r.key) ? (
                      <input className="input mono" disabled readOnly aria-label={`${r.name} (auto)`}
                             value={`auto${r.key === "bc" ? ` (${wdata.length})` : ""}`} />
                    ) : (
                      <input className="input mono" value={r.hex} disabled={!editable}
                             onChange={(e) => patch(r.uid, { hex: e.target.value.toUpperCase() })}
                             aria-label={`${r.name} value (hex)`} />
                    )}
                    {errors[i] && <span className="small c-warn">{errors[i]}</span>}
                  </div>
                  <span className="fb-acts">
                    {r.key === "custom" && <span className="ui-badge ui-badge-warn">Vendor</span>}
                    <button type="button" className="btn btn-sm fb-ico" disabled={!editable || i === 0}
                            onClick={() => move(i, i - 1)} aria-label="Move up"><ArrowUp size={13} /></button>
                    <button type="button" className="btn btn-sm fb-ico" disabled={!editable || i === rows.length - 1}
                            onClick={() => move(i, i + 1)} aria-label="Move down"><ArrowDown size={13} /></button>
                    <button type="button" className="btn btn-sm fb-ico" disabled={!editable || r.key === "val"}
                            title={r.key === "val" ? "A write needs its value" : undefined}
                            onClick={() => setRows((rs) => rs.filter((x) => x.uid !== r.uid))}
                            aria-label="Delete field"><X size={13} /></button>
                  </span>
                </div>
              ))}
              {!rows.length && <p className="muted small" style={{ margin: "4px 0 8px" }}>No fields between FC and CRC.</p>}
            </div>

            <Locked name="CRC" desc="Calculated, always last" size="2" order="Lo → Hi">
              <input className="input mono" value="auto" disabled readOnly aria-label="CRC" />
            </Locked>
          </div>

          {warnings.length > 0 && (
            <div className="fb-warnings" role="status">
              {warnings.map((w) => <p key={w} className="small c-warn">{w}</p>)}
            </div>
          )}

          <div className="fb-section">
            <span className="wf-field-label">Byte map</span>
            <div className="fb-bytes">
              {built.bytes.map((b, i) => (
                <div key={i} className={"fb-byte" + (built.parts[i].kind === "custom" ? " is-custom"
                  : built.parts[i].kind === "locked" ? " is-locked" : "")}>
                  <span className="fb-bi mono">[{i}]</span>
                  <strong className="mono">{hex2(b)}</strong>
                  <span className="fb-bn">{built.parts[i].name}</span>
                </div>
              ))}
            </div>
            <p className="small muted" style={{ margin: "8px 0 0" }}>
              {built.bytes.length} bytes · CRC <span className="mono">0x{built.crc.toString(16).toUpperCase().padStart(4, "0")}</span>
              {" "}(sent <span className="mono">{hex2(built.crc & 255)} {hex2(built.crc >> 8)}</span>)
            </p>
          </div>

          <div className="fb-section">
            <div className="fb-section-head">
              <span className="wf-field-label">Frame to send</span>
              <button type="button" className="btn btn-sm" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            </div>
            <div className="input mono fb-hex">{text}</div>
          </div>
        </div>

        <div className="ui-dialog-foot">
          <button type="button" className="btn" disabled={!editable} onClick={reset}
                  style={{ marginRight: "auto" }}>Reset to standard</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-accent" disabled={!editable || anyErr} onClick={save}
                  title={anyErr ? "Fix the fields marked in red first" : undefined}>Save</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
