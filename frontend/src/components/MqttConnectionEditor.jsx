// MqttConnectionEditor - the "MQTT Connection" a Subscribe or Publish picks.
//
// A beta tester (2026-10-02): "MQTT there no broker IP? add user and password
// in mqtt, add topic user can change the topic themselves". The broker was
// typed into each Subscribe, and the Publish node had no address at all on
// its face. Now it is the Modbus Server pattern (ModbusServerEditor.jsx),
// on purpose -- one thing to learn: each MQTT node has a Connection row (a
// select of the saved connections, a pencil to edit the chosen one, a plus to
// add one), and the broker, its login and the client settings are set up
// once, here. The TOPIC stays on each node.
//
// Stored per system ([[mqtt_clients]] in config.local.toml) through
// /api/mqtt/clients. The password is never sent back: the row says whether
// one is set, and a blank field on save keeps it.
//
// Skipped from the tester's list for now: driver, simulate, failover,
// return-to-online and the offline buffer. SSL/TLS is shown and not offered.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { ArrowLeft, Pencil, Plus } from "lucide-react";
import { REQUIRED_EMPTY } from "./ModbusServerEditor.jsx";
import { PasswordInput } from "./ui/input.jsx";
import { apiUrl } from "../lib/base.js";

const URL = apiUrl("/api/mqtt/clients");

//: What a new connection starts as: this box's own broker. The core fills the
//: same defaults in (core/services/mqtt_clients.py MQTT_CLIENT_DEFAULTS).
export const DEFAULTS = {
  name: "", host: "127.0.0.1", port: 1883, username: "", password: "",
  client_id: "", keepalive_s: 60, reconnect_s: 1, tls: false,
};

let cache = null;
const listeners = new Set();

export function refreshMqttConnections() {
  return api.get(URL).then((d) => {
    cache = d;
    listeners.forEach((fn) => fn(d));
    return d;
  });
}

/** {clients, editable} for this system, shared by every Connection row. */
export function useMqttConnections() {
  const [data, setData] = useState(cache);
  useEffect(() => {
    listeners.add(setData);
    if (!cache) refreshMqttConnections().catch(() => {});
    return () => { listeners.delete(setData); };
  }, []);
  return data;
}

/** The Connection row on a node: select + pencil + plus, as Modbus Server. */
export function MqttConnectionRow({ value, onChange, onEdit, onAdd,
                                    editable = true, required = true }) {
  const data = useMqttConnections();
  const clients = data?.clients || [];
  const known = !value || clients.some((c) => c.id === value);
  const may = editable && data?.editable !== false;
  const picked = clients.find((c) => c.id === value);
  return (<>
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}
         data-mqtt-connection-row="">
      <select className="input" value={value || ""} disabled={!editable}
              aria-label="MQTT Connection"
              style={{ flex: 1, minWidth: 0,
                       ...(required && !value ? REQUIRED_EMPTY : {}) }}
              onChange={(e) => onChange(e.target.value)}>
        <option value="">none</option>
        {!known && <option value={value}>{value} (deleted)</option>}
        {clients.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}{c.label !== `${c.host}:${c.port}` ? ` (${c.host}:${c.port})` : ""}
          </option>
        ))}
      </select>
      <button type="button" className="btn btn-sm" disabled={!may || !value || !known}
              title="Edit this MQTT Connection" aria-label="Edit this MQTT Connection"
              onClick={() => onEdit(value)} style={{ padding: "4px 7px" }}>
        <Pencil size={13} />
      </button>
      <button type="button" className="btn btn-sm" disabled={!may}
              title="Add a new MQTT Connection" aria-label="Add a new MQTT Connection"
              onClick={() => onAdd()} style={{ padding: "4px 7px" }}>
        <Plus size={13} />
      </button>
    </div>
    {/* Imported from an export, which never carries a password. */}
    {picked?.password_needed && (
      <div className="small" data-mqtt-password-needed=""
           style={{ color: "var(--alert-text, #dc2626)", marginTop: 4 }}>
        Set the password for this Connection - it was not in the export.{" "}
        {may && <button type="button" className="btn btn-sm"
                        onClick={() => onEdit(value)}>Set it</button>}
      </div>
    )}
  </>);
}

function Row({ label, hint, children }) {
  return (
    <label style={{ display: "block", padding: "5px 0" }}>
      <div className="muted small" style={{ marginBottom: 2 }}>{label}</div>
      {children}
      {hint && <div className="muted small" style={{ marginTop: 2 }}>{hint}</div>}
    </label>
  );
}

/** The body the page sends: no label/flags, and the password only if typed
 *  (blank keeps the stored one; clear_password drops it). */
export function saveBody(form, adding) {
  const body = { ...form };
  for (const k of ["label", "password_set", "this_box", "password_needed"]) delete body[k];
  if (adding) delete body.id;
  if (!body.password) delete body.password;
  return body;
}

/**
 * The connection itself, taking over the inspector like the Modbus Server
 * editor. `connId` "" adds one; `onSaved(conn)` is how the node that asked
 * gets the new connection picked for it.
 */
export default function MqttConnectionEditor({ connId, onBack, onSaved,
                                               editable = true }) {
  const toast = useToast();
  const data = useMqttConnections();
  const adding = !connId;
  const [form, setForm] = useState(adding ? { ...DEFAULTS } : null);
  const [busy, setBusy] = useState(false);
  const [clearPw, setClearPw] = useState(false);

  useEffect(() => {
    if (adding || form || !data) return;
    const found = (data.clients || []).find((c) => c.id === connId);
    setForm(found ? { ...DEFAULTS, ...found, password: "" } : { ...DEFAULTS });
  }, [adding, data, form, connId]);

  const may = editable && data?.editable !== false;
  const set = (k) => (e) => {
    const t = e?.target;
    const v = t?.type === "checkbox" ? t.checked
      : t?.type === "number" ? (t.value === "" ? "" : +t.value) : t.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  async function save() {
    setBusy(true);
    try {
      const body = saveBody(form, adding);
      if (clearPw) body.clear_password = true;
      const d = await api.post(URL, body);
      await refreshMqttConnections().catch(() => {});
      toast.success(adding ? "MQTT Connection added" : "MQTT Connection updated",
                    "Nodes using it reconnect within a few seconds.");
      onSaved?.(d.client);
      onBack();
    } catch (e) {
      toast.error("Could not save", e.message);
    } finally { setBusy(false); }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`${URL}/${encodeURIComponent(connId)}`);
      await refreshMqttConnections().catch(() => {});
      toast.success("MQTT Connection deleted");
      onBack();
    } catch (e) {
      toast.error("Could not delete", e.message);   // 409 names the nodes
    } finally { setBusy(false); }
  }

  const head = (
    <div className="section-title"
         style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <button className="btn btn-sm" onClick={onBack} title="Back to the node"
              style={{ display: "inline-flex", alignItems: "center", gap: 4,
                       padding: "2px 8px" }}>
        <ArrowLeft size={13} /> Back
      </button>
      <span>{adding ? "Add new MQTT Connection" : "Edit MQTT Connection"}</span>
    </div>
  );
  if (!form) return (<>{head}<p className="muted small">Loading…</p></>);

  const blank = (v) => v === "" || v === null || v === undefined;
  const thisBox = ["127.0.0.1", "localhost", "::1"].includes(
    String(form.host || "").trim().toLowerCase());

  return (<>
    {head}
    <Row label="Name">
      <input className="input" value={form.name || ""} disabled={!may}
             onChange={set("name")} placeholder="e.g. Line 1 PLC broker" />
    </Row>
    <Row label="IP address"
         hint={thisBox ? "127.0.0.1 is the broker on this box (Settings > MQTT Broker)."
                       : "The broker's address on the network, or its name."}>
      <input className="input mono" value={form.host || ""} disabled={!may}
             onChange={set("host")} placeholder="127.0.0.1"
             data-mqtt-field="host"
             style={blank(form.host) ? REQUIRED_EMPTY : undefined} />
    </Row>
    <Row label="Port">
      <input className="input" type="number" min="1" max="65535"
             value={form.port ?? ""} disabled={!may} onChange={set("port")}
             style={blank(form.port) ? REQUIRED_EMPTY : undefined} />
    </Row>
    <Row label="User name"
         hint={thisBox && !form.username
           ? "Blank: this box's own broker login is used by itself."
           : "Blank for a broker that takes anyone."}>
      <input className="input" value={form.username || ""} disabled={!may}
             autoComplete="off" onChange={set("username")}
             data-mqtt-field="username" />
    </Row>
    <Row label="Password"
         hint={form.password_needed
           ? "Needed: this Connection came from an export, which never carries a password."
           : form.password_set && !clearPw ? "Set. Leave blank to keep it." : undefined}>
      {/* Never filled from the server: the page only knows whether one is
          set. Whatever is typed here replaces it on Update. */}
      <PasswordInput native className="input" value={form.password || ""}
             disabled={!may || !form.username || clearPw} autoComplete="new-password"
             onChange={set("password")} data-mqtt-field="password"
             placeholder={form.password_set ? "unchanged" : ""} />
      {form.password_set && (
        <label className="small" style={{ display: "flex", gap: 6, marginTop: 4 }}>
          <input type="checkbox" checked={clearPw} disabled={!may}
                 onChange={(e) => setClearPw(e.target.checked)} />
          Remove the saved password
        </label>
      )}
    </Row>
    <Row label="Client ID"
         hint="Blank: made up, unique for this box's app. A fixed one must be unique on the broker - two clients with one ID knock each other off.">
      <input className="input mono" value={form.client_id || ""} disabled={!may}
             onChange={set("client_id")} placeholder="automatic" />
    </Row>
    <Row label="Keep alive (s)">
      <input className="input" type="number" min="5" max="3600"
             value={form.keepalive_s ?? ""} disabled={!may} onChange={set("keepalive_s")} />
    </Row>
    <Row label="Reconnect time (s)"
         hint="First retry after this long, doubling up to 30 s while the broker stays away.">
      <input className="input" type="number" min="1" max="300"
             value={form.reconnect_s ?? ""} disabled={!may} onChange={set("reconnect_s")} />
    </Row>
    <label style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0" }}
           className="muted">
      <input type="checkbox" checked={false} disabled readOnly />
      <span className="small">SSL/TLS - coming later</span>
    </label>

    {data?.editable === false && (
      <p className="muted small">
        Changing MQTT Connections needs the right to edit the workflow.
      </p>
    )}
    <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
      {!adding && (
        <button className="btn btn-sm" disabled={busy || !may} onClick={remove}
                style={{ color: "var(--alert-text)" }}>
          Delete
        </button>
      )}
      <span style={{ flex: 1 }} />
      <button className="btn btn-sm" disabled={busy} onClick={onBack}>Cancel</button>
      <button className="btn btn-sm btn-accent" disabled={busy || !may}
              onClick={save}>
        {busy ? "Saving…" : adding ? "Add" : "Update"}
      </button>
    </div>
  </>);
}
