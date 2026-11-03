// ModbusServerEditor - the "Modbus Server" connection, Node-RED style.
//
// A Modbus Read and a Modbus Write on the same PLC are two nodes and ONE
// connection. Node-RED draws that as a config node: each node has a Server row
// (a select of the saved servers, a pencil to edit the chosen one, a plus to
// add one) and the connection itself is edited once, in a panel of its own.
// The people wiring these nodes arrive from Node-RED, so this copies it field
// for field and label for label -- a setting that answers to the same word is
// one they do not have to look up.
//
// Stored per system ([[modbus_clients]] in config.local.toml) through
// /api/detector/control/modbus/clients. Not the box's OWN Modbus server
// (BusSettingsPanel, the Modbus page): that one is a PLC polling us, this is
// us polling a PLC.
//
// The list is shared by every Server row on the page through a tiny module
// cache, so adding a server from one node's panel shows up in the next node's
// select without a reload.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { ArrowLeft, Pencil, Plus } from "lucide-react";
import { apiUrl } from "../lib/base.js";

const URL = apiUrl("/api/detector/control/modbus/clients");

//: Node-RED's own defaults, so a server added here and one pasted from a flow
//: start from the same place. The core fills the same ones in if a field is
//: missing (detector_control.MODBUS_CLIENT_DEFAULTS).
const DEFAULTS = {
  name: "", type: "tcp",
  host: "127.0.0.1", port: 502, tcp_type: "DEFAULT",
  serial_port: "/dev/ttyACM0", serial_type: "RTU",
  baudrate: 9600, databits: 8, stopbits: 1, parity: "none",
  unit_id: 1, timeout_ms: 1000,
  reconnect_on_timeout: true, reconnect_timeout_ms: 2000,
};
//: The board's isolated RS485 tops out at 500 kbps (IRIV EdgeAI CM5 datasheet).
const MAX_BAUD = 500000;
const BAUDS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 500000];

//: The red outline Node-RED puts on a required field left empty.
export const REQUIRED_EMPTY = { borderColor: "var(--alert, #dc2626)",
                                boxShadow: "0 0 0 1px var(--alert, #dc2626)" };

let cache = null;
const listeners = new Set();

export function refreshModbusServers() {
  return api.get(URL).then((d) => {
    cache = d;
    listeners.forEach((fn) => fn(d));
    return d;
  });
}

/** {clients, serial_ports, editable} for this system, shared by every row. */
export function useModbusServers() {
  const [data, setData] = useState(cache);
  useEffect(() => {
    listeners.add(setData);
    if (!cache) refreshModbusServers().catch(() => {});
    return () => { listeners.delete(setData); };
  }, []);
  return data;
}

/**
 * The Server row on a node: select + pencil + plus, exactly Node-RED's.
 * "none" first -- an empty choice has to be visible to be fixable, and a
 * select that silently pre-picks the first server writes to a PLC nobody
 * chose. `required` draws the red outline while nothing is picked.
 */
export function ModbusServerRow({ value, onChange, onEdit, onAdd,
                                  editable = true, required = true }) {
  const data = useModbusServers();
  const clients = data?.clients || [];
  const known = !value || clients.some((c) => c.id === value);
  const may = editable && data?.editable !== false;
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
      <select className="input" value={value || ""} disabled={!editable}
              style={{ flex: 1, minWidth: 0,
                       ...(required && !value ? REQUIRED_EMPTY : {}) }}
              onChange={(e) => onChange(e.target.value)}>
        <option value="">none</option>
        {!known && <option value={value}>{value} (deleted)</option>}
        {clients.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select>
      <button type="button" className="btn btn-sm" disabled={!may || !value || !known}
              title="Edit this Modbus Server" aria-label="Edit this Modbus Server"
              onClick={() => onEdit(value)} style={{ padding: "4px 7px" }}>
        <Pencil size={13} />
      </button>
      <button type="button" className="btn btn-sm" disabled={!may}
              title="Add a new Modbus Server" aria-label="Add a new Modbus Server"
              onClick={() => onAdd()} style={{ padding: "4px 7px" }}>
        <Plus size={13} />
      </button>
    </div>
  );
}

//: The board's isolated RS485 port: a USB-to-UART behind the on-board hub,
//: so Linux names it ttyACM0 (IRIV EdgeAI CM5 datasheet). /dev/ttyAMA10,
//: the old default here, is the Linux console and never carried Modbus.
const DEFAULT_SERIAL = "/dev/ttyACM0";

//: A stated value in the shape of an input, for settings that have one answer.
const FIXED = { background: "var(--panel-2, transparent)", cursor: "default",
                userSelect: "text", opacity: 0.85 };

/** [[path, note]] for the Serial port list: only ports that are THERE (the
 *  built-in RS485 named as such, other USB adapters marked), plus the saved
 *  port when it is missing, marked -- opening the editor must never change
 *  it silently. With nothing plugged in the default is offered, marked too,
 *  so the list is never empty and the client sees which port is expected. */
function portChoices(found, current) {
  const seen = new Set();
  const out = [];
  const add = (p, note) => { if (p && !seen.has(p)) { seen.add(p); out.push([p, note]); } };
  for (const p of found) {
    add(p, p === DEFAULT_SERIAL ? "RS485 (built in)"
      : /ttyUSB|ttyACM/.test(p) ? "USB serial" : "");
  }
  if (!out.length) add(DEFAULT_SERIAL, "RS485 (built in), not connected");
  add(current, "not connected");
  return out;
}

//: Cytron's motor driver register map (holding registers): 105 WDT_MS, the
//: comms watchdog in 100 ms units (0 = off), and 100 CFG_SAVE (write 1 to
//: keep the motor settings in flash). Other drives: Advanced. Only WDT is
//: written on connect; "Also save to the drive's memory" (cfg_save, off)
//: adds CFG_SAVE once per changed value, never on every reconnect, so a
//: flaky link does not wear the drive's flash (app/buses.py).
const WATCHDOG = { watchdog_ms: 1000, wdt_address: 105, cfg_save_address: 100,
                   cfg_save: false };

/**
 * The connection's "Drive watchdog" switch (Tok Aba 2026-10-07). Three states
 * in the data: not set (the keys absent; the box touches nothing, as every
 * server saved before the switch), "off" and "on". The panel shows one
 * switch plus a plain "Not managed" line until it is first used, and a link
 * back to "not managed". The app does the writing on each connect
 * (app/buses.py, watchdog_writes); the core refuses bad numbers with the
 * same words as here (core/services/modbus_watchdog.py).
 */
export function DriveWatchdog({ form, setForm, may }) {
  const mode = form.watchdog === "on" || form.watchdog === "off" ? form.watchdog : "";
  const v = { ...WATCHDOG, ...Object.fromEntries(
    Object.entries(form).filter(([k, x]) => k in WATCHDOG && x !== undefined && x !== null)) };
  const saveWords = v.cfg_save
    ? ` Because "Also save" is ticked, it also writes 1 to register ${v.cfg_save_address}
       (save) whenever that value changed, once, not on every connect.`
    : "";
  const ms = v.watchdog_ms === "" ? NaN : Number(v.watchdog_ms);
  const msBad = mode === "on" && !(Number.isInteger(ms) && ms >= 100 && ms <= 10000
                                   && ms % 100 === 0);
  const regBad = (x) => !(x !== "" && Number.isInteger(Number(x))
                          && Number(x) >= 0 && Number(x) <= 65535);
  const sameReg = Number(v.wdt_address) === Number(v.cfg_save_address);
  const put = (patch) => setForm((f) => ({ ...f, ...patch }));
  const num = (k) => (e) => put({ [k]: e.target.value === "" ? "" : +e.target.value });
  return (
    <div data-drive-watchdog="" style={{ padding: "8px 0", borderTop: "1px solid var(--border, #e5e7eb)",
                                         marginTop: 6 }}>
      <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input type="checkbox" role="switch" aria-label="Drive watchdog"
               checked={mode === "on"} disabled={!may}
               onChange={(e) => put({ ...WATCHDOG, ...v,
                                      watchdog: e.target.checked ? "on" : "off" })} />
        <span className="small"><strong>Drive watchdog</strong></span>
        <span className="muted small" data-watchdog-state="">
          {mode === "on" ? "On" : mode === "off" ? "Off" : "Not managed"}
        </span>
      </label>
      <p className="muted small" style={{ margin: "4px 0" }}>
        For motor drivers with a Modbus watchdog (for example Cytron&apos;s
        WDT_MS). Off: the motor keeps the last speed until told otherwise.
        On: the box keeps reminding the drive; if the box or cable fails, the
        motor stops by itself.
      </p>
      {mode === "" && (
        <p className="muted small" data-watchdog-unset="" style={{ margin: "4px 0" }}>
          Not managed: the box leaves the drive&apos;s watchdog as it is.
          Switch it on, or{" "}
          <button type="button" className="btn btn-sm" disabled={!may}
                  data-watchdog-set-off="" style={{ padding: "0 6px" }}
                  onClick={() => put({ ...WATCHDOG, ...v, watchdog: "off" })}>
            turn it off
          </button>{" "}
          so one write keeps the motor running.
        </p>
      )}
      {mode === "off" && (
        <p className="muted small" style={{ margin: "4px 0" }}>
          Each time the box connects it writes 0 to register {v.wdt_address}.
          One write of 1000 runs the motor until something writes 0.{saveWords}
        </p>
      )}
      {mode === "on" && (<>
        <Row label="Timeout (ms)">
          <input className="input" type="number" min="100" max="10000" step="100"
                 aria-label="Watchdog timeout (ms)" value={v.watchdog_ms}
                 disabled={!may} onChange={num("watchdog_ms")}
                 style={msBad ? REQUIRED_EMPTY : undefined} />
        </Row>
        {msBad ? (
          <p className="small c-danger" style={{ margin: "4px 0" }}>
            The watchdog timeout must be between 100 and 10000 ms, in steps of 100.
          </p>
        ) : (
          <p className="muted small" style={{ margin: "4px 0" }}>
            Each time the box connects it writes {ms / 100} to register{" "}
            {v.wdt_address}. Every Modbus Write on this server then keeps
            sending its last value every {Math.max(0.05, ms / 2000)} s.{saveWords}
          </p>
        )}
        {!msBad && ms < 500 && (
          <p className="muted small" data-watchdog-tight="" style={{ margin: "4px 0" }}>
            Under 500 ms leaves little room: one slow reply or a busy box can
            let the drive stop.
          </p>
        )}
      </>)}
      {mode !== "" && (<>
        <details style={{ margin: "4px 0" }}>
          <summary className="small">Advanced</summary>
          <Row label="Watchdog register (WDT_MS)">
            <input className="input" type="number" min="0" max="65535"
                   aria-label="Watchdog register" value={v.wdt_address}
                   disabled={!may} onChange={num("wdt_address")}
                   style={regBad(v.wdt_address) || sameReg ? REQUIRED_EMPTY : undefined} />
          </Row>
          <label style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "5px 0" }}>
            <input type="checkbox" checked={!!v.cfg_save} disabled={!may}
                   aria-label="Also save to the drive's memory"
                   onChange={(e) => put({ cfg_save: e.target.checked })} />
            <span className="small">
              Also save to the drive&apos;s memory (survives the drive&apos;s
              power cycle without the box)
            </span>
          </label>
          <p className="muted small" style={{ margin: "0 0 4px" }}>
            Not needed when the box stays connected: it sets the watchdog
            again every time it connects. Ticked, the save is written once
            per changed value, not on every reconnect, to spare the
            drive&apos;s memory.
          </p>
          <Row label="Save register (CFG_SAVE)">
            <input className="input" type="number" min="0" max="65535"
                   aria-label="Save register" value={v.cfg_save_address}
                   disabled={!may} onChange={num("cfg_save_address")}
                   style={regBad(v.cfg_save_address) || sameReg ? REQUIRED_EMPTY : undefined} />
          </Row>
          {(regBad(v.wdt_address) || regBad(v.cfg_save_address)) && (
            <p className="small c-danger" style={{ margin: "4px 0" }}>
              A register must be a whole number 0 to 65535.
            </p>
          )}
          {sameReg && (
            <p className="small c-danger" style={{ margin: "4px 0" }}>
              The watchdog register and the save register must be different.
            </p>
          )}
          <p className="muted small" style={{ margin: "4px 0" }}>
            Cytron motor drivers: 105 and 100. Other drives: see their manual.
          </p>
        </details>
        <button type="button" className="btn btn-sm" disabled={!may}
                data-watchdog-unmanage=""
                onClick={() => put({ watchdog: "" })}>
          Stop managing it
        </button>
      </>)}
    </div>
  );
}

function Row({ label, children }) {
  return (
    <label style={{ display: "block", padding: "5px 0" }}>
      <div className="muted small" style={{ marginBottom: 2 }}>{label}</div>
      {children}
    </label>
  );
}

/**
 * The connection itself, taking over the inspector the way the bus settings
 * do -- the canvas stays on screen, one Back arrow returns to the node.
 * `serverId` "" adds a new one; `onSaved(server)` is how the node that asked
 * gets the new server picked for it, as Node-RED does.
 */
export default function ModbusServerEditor({ serverId, onBack, onSaved,
                                             editable = true }) {
  const toast = useToast();
  const data = useModbusServers();
  const adding = !serverId;
  const [form, setForm] = useState(adding ? { ...DEFAULTS } : null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (adding || form || !data) return;
    const found = (data.clients || []).find((c) => c.id === serverId);
    setForm(found ? { ...DEFAULTS, ...found } : { ...DEFAULTS });
  }, [adding, data, form, serverId]);

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
      const body = { ...form };
      delete body.label;
      if (adding) delete body.id;
      const d = await api.post(URL, body);
      await refreshModbusServers().catch(() => {});
      toast.success(adding ? "Modbus Server added" : "Modbus Server updated",
                    "Nodes using it pick the change up within a few seconds.");
      onSaved?.(d.client);
      onBack();
    } catch (e) {
      toast.error("Could not save", e.message);
    } finally { setBusy(false); }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del(`${URL}/${encodeURIComponent(serverId)}`);
      await refreshModbusServers().catch(() => {});
      toast.success("Modbus Server deleted");
      onBack();
    } catch (e) {
      // 409 names the nodes still using it -- the message IS the next step.
      toast.error("Could not delete", e.message);
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
      <span>{adding ? "Add new Modbus Server" : "Edit Modbus Server"}</span>
    </div>
  );
  if (!form) return (<>{head}<p className="muted small">Loading…</p></>);

  const serial = form.type === "serial";
  const ports = data?.serial_ports || [];
  const blank = (v) => v === "" || v === null || v === undefined;

  return (<>
    {head}
    <Row label="Name">
      <input className="input" value={form.name || ""} disabled={!may}
             onChange={set("name")} placeholder="e.g. Line 1 PLC" />
    </Row>
    <Row label="Type">
      <select className="input" value={form.type} disabled={!may}
              onChange={set("type")}>
        <option value="tcp">TCP</option>
        {/* Stored as "serial" (every saved server has it), shown as RTU:
            no other serial framing is spoken here, and the client who
            reads this list thinks in Modbus terms, TCP or RTU. */}
        <option value="serial">RTU</option>
      </select>
    </Row>

    {!serial && (<>
      <Row label="Host">
        <input className="input" value={form.host || ""} disabled={!may}
               onChange={set("host")} placeholder="127.0.0.1"
               style={blank(form.host) ? REQUIRED_EMPTY : undefined} />
      </Row>
      <Row label="Port">
        <input className="input" type="number" min="1" max="65535"
               value={form.port ?? ""} disabled={!may} onChange={set("port")}
               style={blank(form.port) ? REQUIRED_EMPTY : undefined} />
      </Row>
      <Row label="TCP Type">
        {/* Stated, not offered: nothing on this box speaks Node-RED's
            other TCP framings, and a dropdown with one entry still reads
            as a choice. */}
        <div className="input" style={FIXED}>DEFAULT</div>
      </Row>
    </>)}

    {serial && (<>
      <Row label="Serial port">
        {/* A list, not a text box: the board's own RS485 port first, then
            whatever USB adapter is plugged in. A saved port that is not
            there right now stays in the list, marked, so opening the
            editor never silently changes it. */}
        <select className="input mono" value={form.serial_port || DEFAULT_SERIAL}
                disabled={!may} onChange={set("serial_port")}>
          {portChoices(ports, form.serial_port).map(([p, note]) => (
            <option key={p} value={p}>{note ? `${p} (${note})` : p}</option>
          ))}
        </select>
      </Row>
      <Row label="Serial Type">
        <div className="input" style={FIXED}>RTU</div>
      </Row>
      <Row label="Baud rate">
        <input className="input" type="number" list="mb-bauds" min="300"
               max={MAX_BAUD} value={form.baudrate ?? ""} disabled={!may}
               onChange={(e) => setForm((f) => ({ ...f, baudrate: e.target.value === ""
                 ? "" : Math.min(MAX_BAUD, +e.target.value) }))}
               style={blank(form.baudrate) ? REQUIRED_EMPTY : undefined} />
        <datalist id="mb-bauds">
          {BAUDS.map((b) => <option key={b} value={b} />)}
        </datalist>
      </Row>
      <Row label="Data bits">
        <select className="input" value={form.databits} disabled={!may}
                onChange={(e) => setForm((f) => ({ ...f, databits: +e.target.value }))}>
          {[8, 7, 6, 5].map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
      </Row>
      <Row label="Stop bits">
        <select className="input" value={form.stopbits} disabled={!may}
                onChange={(e) => setForm((f) => ({ ...f, stopbits: +e.target.value }))}>
          <option value={1}>1</option>
          <option value={2}>2</option>
        </select>
      </Row>
      <Row label="Parity">
        <select className="input" value={form.parity} disabled={!may}
                onChange={set("parity")}>
          <option value="none">none</option>
          <option value="even">even</option>
          <option value="odd">odd</option>
        </select>
      </Row>
    </>)}

    <Row label="Unit-Id">
      <input className="input" type="number" min="0" max="255"
             value={form.unit_id ?? ""} disabled={!may} onChange={set("unit_id")}
             style={blank(form.unit_id) ? REQUIRED_EMPTY : undefined} />
    </Row>
    <Row label="Timeout (ms)">
      <input className="input" type="number" min="100" max="60000"
             value={form.timeout_ms ?? ""} disabled={!may}
             onChange={set("timeout_ms")} />
    </Row>
    <label style={{ display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 0" }}>
      <input type="checkbox" checked={!!form.reconnect_on_timeout}
             disabled={!may} onChange={set("reconnect_on_timeout")} />
      <span className="small">Reconnect on timeout</span>
    </label>
    <Row label="Reconnect timeout (ms)">
      <input className="input" type="number" min="100" max="60000"
             value={form.reconnect_timeout_ms ?? ""}
             disabled={!may || !form.reconnect_on_timeout}
             onChange={set("reconnect_timeout_ms")} />
    </Row>
    <DriveWatchdog form={form} setForm={setForm} may={may} />

    {data?.editable === false && (
      <p className="muted small">
        Changing Modbus Servers is not allowed on this account - the box
        owner sets that in Settings &gt; Accounts.
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
