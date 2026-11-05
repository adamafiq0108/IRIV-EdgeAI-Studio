// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Modbus - this box AS a Modbus device, for a PLC or SCADA head to poll.
//
// The other direction has always existed: the workflow's Modbus nodes READ a
// PLC's registers. This page is the mirror. It decides the LISTENER - which
// transport, which port, which unit id - and nothing else.
//
// What is served is deliberately NOT here. The register map is built from
// `Modbus Register` nodes on the Workflow canvas, because "zone 1 count, at
// address 0" is one sentence and it belongs beside the rule that fills it. A
// table of addresses on a settings page would be the same map, kept somewhere
// the values are not.
//
// Two facts on screen at once, and both are needed: what the box was TOLD to
// do, and what happened when it tried. A server that could not bind its port
// keeps the detection app running - so without the second half, a
// misconfigured listener looks exactly like one no PLC has polled yet.
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import { ErrorNote, SkelEditor } from "../components/Loading.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { ArrowLeft, Cable, CircleCheck, CircleAlert, CircleSlash } from "lucide-react";

const MODES = [
  ["tcp", "TCP", "Ethernet. What most SCADA heads speak."],
  ["rtu", "RTU (serial)", "RS-485 or a USB serial adapter."],
  ["both", "Both", "Answer on either, from the same values."],
];

const BAUDS = [9600, 19200, 38400, 57600, 115200];

export default function Modbus() {
  const toast = useToast();
  // Arrived from an RS485 node's inspector? Then a way back is owed. Without
  // it the round trip is sidebar, Modbus, change one field, sidebar,
  // Workflow, find the node again -- for a setting the node sent you here to
  // change. Only shown when it was actually a hop, so the page does not grow
  // a Back button for people who opened it from the rail.
  const [params] = useSearchParams();
  const cameFromWorkflow = params.get("from") === "workflow";
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get("/api/detector/control/modbus")
      .then((d) => { setData(d); setForm(d.modbus); })
      .catch(setError);
  }, []);
  useEffect(load, [load]);
  // The listener's STATE is the app's, and it changes without anyone touching
  // this page -- a port frees up, a serial adapter is unplugged. Re-read it on
  // a slow tick so the banner is not a snapshot from whenever the page opened.
  // Paused while the tab is hidden, refetched on return (useVisibleInterval).
  useVisibleInterval(() => {
    api.get("/api/detector/control/modbus")
      .then((d) => setData((cur) => (cur ? { ...cur, state: d.state } : d)))
      .catch(() => {});
  }, 5000);

  if (error) return <ErrorNote error={error} />;
  if (!form) return <SkelEditor />;

  const set = (k) => (e) => {
    const v = e?.target?.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  async function save() {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/modbus", form);
      setForm(d.modbus);
      toast.success("Modbus saved",
                    form.enabled
                      ? "The app applies it within a few seconds."
                      : "The server is switched off.");
      setTimeout(load, 3000);
    } catch (e) {
      toast.error("Could not save", e.message);
    } finally { setBusy(false); }
  }

  const state = data?.state || {};
  const serial = data?.serial_ports || [];
  const rtu = form.mode === "rtu" || form.mode === "both";
  const tcp = form.mode === "tcp" || form.mode === "both";

  // Three states, not two: off is a choice, running is fine, and a server that
  // was asked to run and did not is the one worth a colour.
  const banner = !form.enabled
    ? { Icon: CircleSlash, tone: "var(--muted-foreground, #a1a1aa)",
        title: "Switched off",
        body: "No PLC can poll this system." }
    : state.error
    ? { Icon: CircleAlert, tone: "var(--alert, #dc2626)",
        title: "Not serving", body: state.error }
    : state.running
    ? { Icon: CircleCheck, tone: "var(--ok, #16a34a)",
        title: "Serving",
        body: tcp
          ? `A PLC can poll ${form.host}:${form.port} at unit id ${form.unit_id}.`
          : `A PLC can poll ${form.device} at unit id ${form.unit_id}.` }
    : { Icon: CircleAlert, tone: "var(--warn, #d97706)",
        title: "Starting…",
        body: "Saved. The app picks it up on its next config poll - a few seconds." };

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Cable size={18} /> Modbus
        </div>
        <div className="page-actions">
          {cameFromWorkflow && (
            <Link to="/workflow" className="btn"
                  title="Back to the workflow you came from"
                  style={{ display: "inline-flex", alignItems: "center", gap: 6,
                           whiteSpace: "nowrap" }}>
              <ArrowLeft size={15} /> Back to workflow
            </Link>
          )}
          <button className="btn btn-accent" disabled={busy} onClick={save}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <div className="panel-card" style={{ display: "flex", gap: 10,
                                           alignItems: "flex-start", marginBottom: 14 }}>
        <banner.Icon size={18} style={{ color: banner.tone, flex: "none", marginTop: 1 }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, color: banner.tone }}>{banner.title}</div>
          <div className="muted small" style={{ overflowWrap: "anywhere" }}>
            {banner.body}
          </div>
        </div>
      </div>

      <div className="panel-card" style={{ maxWidth: 620 }}>
        <div className="section-title">Server</div>

        <label style={{ display: "flex", alignItems: "center", gap: 10,
                        padding: "10px 0" }}>
          <input type="checkbox" checked={!!form.enabled} onChange={set("enabled")} />
          <span>
            <div>Answer Modbus requests</div>
            <div className="muted small">
              A PLC reads the values your <b>Modbus Register</b> nodes publish.
              Nothing here can be written by the far end.
            </div>
          </span>
        </label>

        <div style={{ padding: "10px 0" }}>
          <div style={{ marginBottom: 6 }}>Transport</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {MODES.map(([id, label, hint]) => (
              <button key={id} className="btn btn-sm" title={hint}
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

        {tcp && (
          <div style={{ display: "grid", gap: 10,
                        gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))",
                        padding: "10px 0" }}>
            <label>
              <div className="muted small">Listen on</div>
              <input value={form.host} onChange={set("host")}
                     placeholder="0.0.0.0" />
              <div className="muted small">0.0.0.0 answers on every network.</div>
            </label>
            <label>
              <div className="muted small">TCP port</div>
              <input type="number" value={form.port} onChange={set("port")} />
              {/* The single most likely first failure, said before it happens
                  rather than as a permission error afterwards. */}
              <div className="muted small">
                {Number(form.port) < 1024
                  ? "Ports below 1024 need root - this app is not root. Use 5020."
                  : "Modbus's own port is 502, but that needs root; 5020 is the usual stand-in."}
              </div>
            </label>
          </div>
        )}

        {rtu && (
          <div style={{ display: "grid", gap: 10,
                        gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))",
                        padding: "10px 0" }}>
            <label>
              <div className="muted small">Serial port</div>
              <select value={form.device} onChange={set("device")}>
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
            <label>
              <div className="muted small">Baud rate</div>
              <select value={form.baudrate} onChange={set("baudrate")}>
                {BAUDS.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            </label>
            <label>
              <div className="muted small">Parity</div>
              <select value={form.parity} onChange={set("parity")}>
                <option value="N">None</option>
                <option value="E">Even</option>
                <option value="O">Odd</option>
              </select>
            </label>
            <label>
              <div className="muted small">Stop bits</div>
              <select value={form.stopbits} onChange={set("stopbits")}>
                <option value="1">1</option>
                <option value="2">2</option>
              </select>
            </label>
          </div>
        )}

        <div style={{ padding: "10px 0", maxWidth: 220 }}>
          <div className="muted small">Unit id (slave address)</div>
          <input type="number" value={form.unit_id} onChange={set("unit_id")} />
          <div className="muted small">
            1–247. The PLC must ask for this id, or it gets no answer at all.
          </div>
        </div>
      </div>

      <div className="panel-card" style={{ maxWidth: 620, marginTop: 14 }}>
        <div className="section-title">What is served</div>
        <p className="muted small">
          Add a <b>Modbus Register</b> node on the Workflow canvas and wire a
          value into it. The node carries the address, whether it is a holding
          or input register, and the scale.
        </p>
        <p className="muted small">
          Registers are 16-bit whole numbers, so a value with decimals needs a
          scale: at scale 10, 2.5 is sent as 25 and the PLC divides. Values
          outside a register's range are pinned at the limit rather than
          wrapping - a count that wraps to 3 is a lie a PLC would act on.
        </p>
      </div>
    </section>
  );
}
