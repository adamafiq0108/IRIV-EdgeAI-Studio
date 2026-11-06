// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Scan a Modbus Server for the Unit-Ids that answer -- the Scan button under a
// Modbus Read's Unit-Id.
//
// A Modbus bus has no discovery, so the scan asks every id (1-247) for one
// holding register and lists the ones that answer (data or an error reply both
// mean a device is there). It runs in the system's app, which owns the serial
// port, so it takes turns with the Modbus Read pollers rather than fighting
// them for the wire. Read-only: nothing on any device is changed.
import { useEffect, useRef, useState } from "react";
import { Radar, Square } from "lucide-react";
import { api } from "../api/client.js";

const POLL_MS = 700;
//: How long to wait for the app to pick the request up (it may be starting).
const PICKUP_MS = 30000;

/**
 * @param {object}   props
 * @param {string}   props.server    the Modbus Server id the node reads from
 * @param {boolean}  props.editable
 * @param {*}        props.value     the node's Unit-Id now
 * @param {function} props.onPick    (unitId) => void
 */
export default function ModbusScan({ server, editable, value, onPick }) {
  const [scan, setScan] = useState(null);   // the app's result doc for OUR scan
  const [err, setErr] = useState("");
  const [waiting, setWaiting] = useState(false);
  const timer = useRef(null);
  const asked = useRef(0);

  const stopPolling = () => { if (timer.current) clearTimeout(timer.current); timer.current = null; };
  useEffect(() => stopPolling, []);
  // A different server: what was found belongs to the old one.
  useEffect(() => { stopPolling(); setScan(null); setErr(""); setWaiting(false); }, [server]);

  const poll = (want) => {
    timer.current = setTimeout(async () => {
      try {
        const r = await api.get("/api/workflow/modbus-scan");
        const doc = r?.scan || {};
        if (doc.seq === want && doc.server === server) {
          setScan(doc);
          setWaiting(false);
          if (doc.state !== "scanning") { stopPolling(); return; }
        } else if (Date.now() - asked.current > PICKUP_MS) {
          setWaiting(false);
          setErr("The app did not start the scan. Check the system is running, then try again.");
          stopPolling();
          return;
        }
      } catch (e) {
        setErr(e?.message || String(e));
        setWaiting(false);
        stopPolling();
        return;
      }
      poll(want);
    }, POLL_MS);
  };

  const start = async () => {
    stopPolling();
    setErr(""); setScan(null); setWaiting(true);
    try {
      const r = await api.post("/api/workflow/modbus-scan", { server, action: "start" });
      asked.current = Date.now();
      poll(r.seq);
    } catch (e) {
      setWaiting(false);
      setErr(e?.message || String(e));
    }
  };
  const stop = async () => {
    try { await api.post("/api/workflow/modbus-scan", { server, action: "stop" }); } catch { /* shown by the next poll */ }
  };

  const running = waiting || scan?.state === "scanning";
  const found = scan?.found || [];
  const first = scan?.first ?? 1, last = scan?.last ?? 247;
  const pct = scan ? Math.round(((scan.at - first + 1) / (last - first + 1)) * 100) : 0;

  return (
    <div className="wf-mbscan" data-wf-mbscan>
      <div className="wf-mbscan-row">
        {!running ? (
          <button type="button" className="btn btn-sm" disabled={!editable || !server}
                  title={server ? "Ask every Unit-Id 1-247 on this server which ones answer"
                                : "Pick a Modbus Server first"}
                  onClick={start}>
            <Radar size={14} aria-hidden="true" /> {scan ? "Scan again" : "Scan for devices"}
          </button>
        ) : (
          <button type="button" className="btn btn-sm" onClick={stop}>
            <Square size={12} aria-hidden="true" /> Stop
          </button>
        )}
        {running && (
          <span className="muted small">
            {waiting ? "Starting the scan…" : `Asking Unit-Id ${Math.max(first, scan.at)} of ${last}`}
          </span>
        )}
      </div>
      {running && !waiting && (
        <div className="wf-mbscan-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></div>
      )}
      {found.length > 0 && (
        <select className="input" value={found.includes(Number(value)) ? String(value) : ""}
                disabled={!editable} onChange={(e) => e.target.value && onPick(Number(e.target.value))}
                aria-label="Devices found">
          <option value="">{`Devices found (${found.length}) - pick one`}</option>
          {found.map((u) => <option key={u} value={u}>{`Unit-Id ${u}`}</option>)}
        </select>
      )}
      {scan && scan.state !== "scanning" && !found.length && scan.state !== "error" && (
        <p className="muted small" style={{ margin: "4px 0 0" }}>
          {scan.state === "stopped" ? "Stopped before any device answered."
            : "No device answered. Check the wiring (A/B), the baud rate and the parity of the server."}
        </p>
      )}
      {scan?.state === "done" && found.length > 0 && (
        <p className="muted small" style={{ margin: "4px 0 0" }}>
          {`Scan finished: ${found.length} device${found.length === 1 ? "" : "s"} answered.`}
        </p>
      )}
      {(err || scan?.error) && <p className="small" style={{ margin: "4px 0 0", color: "var(--danger, #dc2626)" }}>{err || scan.error}</p>}
    </div>
  );
}
