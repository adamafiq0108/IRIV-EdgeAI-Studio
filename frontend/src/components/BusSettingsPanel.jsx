// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// BusSettingsPanel - the RS485/Modbus bus settings, INSIDE the inspector.
//
// These settings (transport, port, baud, unit id) belong to the WIRE, not to
// any one node on it, which is why they are not fields on the node. They used
// to live on their own page, and the RS485 node linked across to it - but
// leaving the canvas to change one number means losing your place in the
// graph, and coming back means finding the node again. The page carried a
// "back to workflow" button precisely because that round trip was bad.
//
// So the same settings render here, in the panel the node was already using,
// behind one back arrow. Nothing about the graph is unmounted; the canvas is
// still on screen. The standalone page still exists for the rail, and both
// read and write the same endpoint - this is a second view of one setting,
// never a second copy of it.
//
// Deliberately ONE COLUMN and plain: the inspector is a narrow rail, and the
// two-column layout the page uses would wrap into a mess at this width.
//
// The field LABELS are the Modbus Server editor's (ModbusServerEditor.jsx),
// word for word where the two overlap -- Type, Host, Port, Serial port, Baud
// rate, Parity, Stop bits, Unit-Id -- so one vocabulary covers both ends of a
// Modbus conversation even though these settings are the box answering a
// PLC, not the box asking one.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { ArrowLeft, CircleCheck, CircleAlert, CircleSlash } from "lucide-react";

const MODES = [
  ["tcp", "TCP", "Ethernet. What most SCADA heads speak."],
  ["rtu", "RTU", "RS-485 or a USB serial adapter."],
  ["both", "Both", "Answer on either, from the same values."],
];
const BAUDS = [9600, 19200, 38400, 57600, 115200];

export default function BusSettingsPanel({ onBack, editable = true }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get("/api/detector/control/modbus")
      .then((d) => { setData(d); setForm(d.modbus); setError(null); })
      .catch(setError);
  }, []);
  useEffect(load, [load]);

  // The listener's STATE is the app's and changes without anyone touching
  // this panel -- a port frees up, a serial adapter is unplugged.
  useEffect(() => {
    const t = setInterval(() => {
      api.get("/api/detector/control/modbus")
        .then((d) => setData((cur) => (cur ? { ...cur, state: d.state } : d)))
        .catch(() => {});
    }, 5000);
    return () => clearInterval(t);
  }, []);

  const set = (k) => (e) => {
    const v = e?.target?.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  async function save() {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/modbus", form);
      setForm(d.modbus);
      toast.success("Bus settings saved",
                    form.enabled ? "The app applies it within a few seconds."
                                 : "The server is switched off.");
      setTimeout(load, 3000);
    } catch (e) {
      toast.error("Could not save", e.message);
    } finally { setBusy(false); }
  }

  const head = (
    <div className="section-title"
         style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <button className="btn btn-sm" onClick={onBack}
              title="Back to the node"
              style={{ display: "inline-flex", alignItems: "center", gap: 4,
                       padding: "2px 8px" }}>
        <ArrowLeft size={13} /> Back
      </button>
      <span>Bus settings</span>
    </div>
  );

  if (error) {
    return (<>{head}
      <p className="small" style={{ color: "var(--alert-text)" }}>
        {error.message || "Could not read the bus settings."}
      </p></>);
  }
  if (!form) return (<>{head}<p className="muted small">Loading…</p></>);

  const state = data?.state || {};
  const serial = data?.serial_ports || [];
  const rtu = form.mode === "rtu" || form.mode === "both";
  const tcp = form.mode === "tcp" || form.mode === "both";

  // Three states, not two: off is a choice, running is fine, and a server
  // that was asked to run and did not is the one worth a colour.
  const banner = !form.enabled
    ? { Icon: CircleSlash, tone: "var(--muted-foreground, #a1a1aa)",
        title: "Switched off", body: "No PLC can poll this system." }
    : state.error
    ? { Icon: CircleAlert, tone: "var(--alert, #dc2626)",
        title: "Not serving", body: state.error }
    : state.running
    ? { Icon: CircleCheck, tone: "var(--ok, #16a34a)", title: "Serving",
        body: tcp ? `Poll ${form.host}:${form.port} at unit id ${form.unit_id}.`
                  : `Poll ${form.device} at unit id ${form.unit_id}.` }
    : { Icon: CircleAlert, tone: "var(--warn, #d97706)", title: "Starting…",
        body: "Saved. The app picks it up on its next config poll." };

  return (<>
    {head}

    <div style={{ display: "flex", gap: 8, alignItems: "flex-start",
                  margin: "8px 0 12px" }}>
      <banner.Icon size={15} style={{ color: banner.tone, flex: "none",
                                      marginTop: 2 }} />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600, color: banner.tone, fontSize: 12 }}>
          {banner.title}
        </div>
        <div className="muted small" style={{ overflowWrap: "anywhere" }}>
          {banner.body}
        </div>
      </div>
    </div>

    <label style={{ display: "flex", alignItems: "flex-start", gap: 8,
                    padding: "6px 0" }}>
      <input type="checkbox" checked={!!form.enabled} disabled={!editable}
             onChange={set("enabled")} style={{ marginTop: 3 }} />
      <span>
        <div className="small">Answer Modbus requests</div>
        <div className="muted small">
          A PLC reads the values your <b>Modbus Register</b> nodes publish.
          Nothing here can be written by the far end.
        </div>
      </span>
    </label>

    <div style={{ padding: "6px 0" }}>
      <div className="muted small" style={{ marginBottom: 4 }}>Type</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {MODES.map(([id, label, hint]) => (
          <button key={id} className="btn btn-sm" title={hint} disabled={!editable}
                  onClick={() => setForm((f) => ({ ...f, mode: id }))}
                  style={form.mode === id
                    ? { borderColor: "var(--accent, #04bade)",
                        color: "var(--accent-text, #04bade)",
                        background: "rgba(4,186,222,.10)" }
                    : undefined}>
            {label}
          </button>
        ))}
      </div>
    </div>

    {tcp && (<>
      <label style={{ display: "block", padding: "6px 0" }}>
        <div className="muted small">Host</div>
        <input className="input" value={form.host} disabled={!editable}
               onChange={set("host")} placeholder="0.0.0.0" />
        <div className="muted small">0.0.0.0 answers on every network.</div>
      </label>
      <label style={{ display: "block", padding: "6px 0" }}>
        <div className="muted small">Port</div>
        <input className="input" type="number" value={form.port}
               disabled={!editable} onChange={set("port")} />
        {/* The single most likely first failure, said before it happens
            rather than as a permission error afterwards. */}
        <div className="muted small">
          {Number(form.port) < 1024
            ? "Ports below 1024 need root - this app is not root. Use 5020."
            : "Modbus's own port is 502, but that needs root; 5020 is the usual stand-in."}
        </div>
      </label>
    </>)}

    {rtu && (<>
      <label style={{ display: "block", padding: "6px 0" }}>
        <div className="muted small">Serial port</div>
        <select className="input" value={form.device} disabled={!editable}
                onChange={set("device")}>
          {/* The saved one survives a box that cannot see it right now:
              dropping it would silently rewrite the setting. */}
          {form.device && !serial.includes(form.device) && (
            <option value={form.device}>{form.device} (not detected)</option>
          )}
          {serial.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <div className="muted small">
          This port becomes ours - Node-RED cannot hold it too.
        </div>
      </label>
      <label style={{ display: "block", padding: "6px 0" }}>
        <div className="muted small">Baud rate</div>
        <select className="input" value={form.baudrate} disabled={!editable}
                onChange={set("baudrate")}>
          {BAUDS.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
      </label>
      <label style={{ display: "block", padding: "6px 0" }}>
        <div className="muted small">Parity</div>
        <select className="input" value={form.parity} disabled={!editable}
                onChange={set("parity")}>
          <option value="N">none</option>
          <option value="E">even</option>
          <option value="O">odd</option>
        </select>
      </label>
      <label style={{ display: "block", padding: "6px 0" }}>
        <div className="muted small">Stop bits</div>
        <select className="input" value={form.stopbits} disabled={!editable}
                onChange={set("stopbits")}>
          <option value="1">1</option>
          <option value="2">2</option>
        </select>
      </label>
    </>)}

    <label style={{ display: "block", padding: "6px 0" }}>
      <div className="muted small">Unit-Id</div>
      <input className="input" type="number" value={form.unit_id}
             disabled={!editable} onChange={set("unit_id")} />
      <div className="muted small">
        1-247, this box's own slave address. The PLC must ask for this id,
        or it gets no answer at all.
      </div>
    </label>

    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
      <button className="btn btn-sm btn-accent" disabled={busy || !editable}
              onClick={save}>
        {busy ? "Saving…" : "Save"}
      </button>
    </div>
  </>);
}
