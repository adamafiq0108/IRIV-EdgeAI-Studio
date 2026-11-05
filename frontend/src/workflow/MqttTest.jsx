// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// MqttTest - "is my topic right?", from the node's own inspector.
//
//   PublishTest  under MQTT Publish: subscribes to the node's topic, publishes
//                once, and says whether the message came back and how fast.
//   ListenTest   under MQTT Subscribe: listens on the node's topic for up to
//                10 s and lists what arrives, with what the node's "value in
//                the message" path makes of each one.
//   DebugHistory under a Debug node: the readings it has seen, newest first,
//                the way Node-RED's debug tab lists messages.
//
// The tests run ON THE BOX (core/services/mqtt_test.py) against what the box
// has SAVED for the node, so they test what will actually run - and a node
// edited but not yet saved says so rather than testing yesterday's topic.
// Neither needs detection running, and neither touches the app's own MQTT
// connection.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { clearHistory, historyOf, subscribe } from "./probeHistory.js";

const OK = { color: "var(--ok-text, #16a34a)" };
const BAD = { color: "var(--alert-text, #dc2626)" };
const MONO = { fontFamily: "var(--mono, monospace)", fontSize: 11,
               overflowWrap: "anywhere" };

function clock(t) {
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString();
}

/** The yes/no rows of a publish test, in the order they can fail. */
export function publishSteps(r) {
  if (!r) return [];
  return [
    ["Connected to the broker", !!r.connected],
    ["Broker accepted the publish", !!r.published],
    [r.received ? `Came back on the topic (${r.rtt_ms} ms)` : "Came back on the topic",
     !!r.received],
  ];
}

/** The send button's words: a second click is needed when this box's own
 *  Subscribe nodes would hear the message. */
export function sendLabel({ busy, warning, armed }) {
  if (busy) return "Testing...";
  if (warning) return armed ? "Send anyway" : "Test publish...";
  return "Test publish";
}

export function PublishTest({ node, disabled }) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);      // the server's answer
  const [err, setErr] = useState("");
  // What will be sent, EDITABLE (Yaya, review of 18c4480): a test goes to
  // the real topic, so the person sending it decides what it says. Filled
  // from a dry run - the node's current value, or the plain word "test".
  const [payload, setPayload] = useState("");
  const [plan, setPlan] = useState(null);    // the dry run: warning, sample...
  const [armed, setArmed] = useState(false); // the second click, when warned

  async function prepare() {
    setErr(""); setArmed(false);
    try {
      const d = await api.post("/api/workflow/mqtt-test/publish",
                               { node: node.id, dry: true });
      setPlan(d); setPayload(d.payload ?? "");
    } catch (e) { setPlan(null); setErr(e.message || "the node is not saved yet"); }
  }
  // Only for an account that may send: the dry run is gated like the send.
  useEffect(() => { if (!disabled) prepare(); }, [node.id, disabled]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function send() {
    if (plan?.warning && !armed) { setArmed(true); return; }
    setBusy(true); setErr(""); setRes(null);
    try {
      setRes(await api.post("/api/workflow/mqtt-test/publish",
                            { node: node.id, payload, confirm: armed }));
      setArmed(false);
    } catch (e) {
      setErr(e.message || "the test did not run");
      // A Subscribe saved since the dry run: the server asks for the
      // confirmation the page did not know it needed.
      if (e.status === 409) prepare().then(() => setArmed(true));
    } finally { setBusy(false); }
  }

  const warning = plan?.warning || "";
  return (
    <div style={{ marginTop: 6 }} data-mqtt-test="publish">
      <div className="filter-bar" style={{ alignItems: "center", gap: 6 }}>
        <input className="input mono" value={payload} disabled={disabled || busy}
               maxLength={1000} aria-label="Test payload"
               placeholder="test" style={{ flex: 1 }}
               onChange={(e) => { setPayload(e.target.value); setArmed(false); }} />
        <button type="button" className="btn btn-sm" disabled={disabled || busy || !plan}
                title="Publish this once to the node's topic and listen for it"
                onClick={send}>
          {sendLabel({ busy, warning, armed })}
        </button>
      </div>
      {warning && (
        <div className="small" role="alert" data-mqtt-warning=""
             style={{ ...BAD, marginTop: 4, fontWeight: 600 }}>
          Careful: {warning}.{armed ? " Press Send anyway to send it." : ""}
        </div>
      )}
      <div className="muted small" style={{ marginTop: 4 }}>
        {err ? <span style={BAD}>{err}</span>
          : res ? (
            <>
              <div style={res.ok ? OK : BAD}>{res.message}</div>
              <ul style={{ margin: "4px 0", paddingLeft: 16 }}>
                {publishSteps(res).map(([label, ok]) => (
                  <li key={label} style={ok ? OK : BAD}>
                    {ok ? "yes" : "no"} - {label}
                  </li>
                ))}
              </ul>
              <div>Broker <span style={MONO}>{res.broker}</span>
                {res.login === "set" ? ", logged in" : ""}</div>
              <div>Sent: <span style={MONO}>{res.sent}</span></div>
              {res.received && (
                <div>Received: <span style={MONO}>{res.received_payload}</span></div>
              )}
              {(res.notes || []).map((n) => <div key={n}>{n}</div>)}
            </>
          ) : plan?.sample
            ? "The node has no value yet, so the test sends the word above. "
              + "It goes to the real topic - change it if something reads it."
            : "Sends the text above once to the saved topic - the real one - "
              + "and checks it comes back. Save the canvas first."}
      </div>
    </div>
  );
}

const POLL_MS = 700;

export function ListenTest({ node, disabled, seconds = 10 }) {
  const [snap, setSnap] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);
  const running = !!snap?.running;

  // Poll while it runs; stop polling (and the listen) when the inspector
  // closes, so no connection outlives the panel that asked for it.
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  useEffect(() => {
    if (!running) return undefined;
    timer.current = setTimeout(async () => {
      try {
        setSnap(await api.get(`/api/workflow/mqtt-test/listen?id=${encodeURIComponent(snap.id)}`));
      } catch (e) { setErr(e.message || "the listen stopped"); setSnap(null); }
    }, POLL_MS);
    return () => clearTimeout(timer.current);
  }, [snap, running]);
  useEffect(() => () => {
    if (snap?.running) {
      api.post("/api/workflow/mqtt-test/listen/stop", { id: snap.id }).catch(() => {});
    }
  }, [snap?.id]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function start() {
    setBusy(true); setErr(""); setSnap(null);
    try {
      setSnap(await api.post("/api/workflow/mqtt-test/listen",
                             { node: node.id, seconds }));
    } catch (e) { setErr(e.message || "the listen did not start"); }
    finally { setBusy(false); }
  }
  async function stop() {
    try {
      setSnap(await api.post("/api/workflow/mqtt-test/listen/stop", { id: snap.id }));
    } catch { /* it had finished anyway */ }
  }

  const msgs = snap?.messages || [];
  return (
    <div style={{ marginTop: 6 }} data-mqtt-test="listen">
      <button type="button" className="btn btn-sm"
              disabled={disabled || busy}
              onClick={running ? stop : start}>
        {running ? "Stop" : busy ? "Starting..." : `Listen for ${seconds} s`}
      </button>
      <div className="muted small" style={{ marginTop: 4 }}>
        {err ? <span style={BAD}>{err}</span>
          : !snap ? "Listens on the saved topic and shows what arrives, and "
                    + "what the value path above picks out of each message."
          : snap.state === "error" ? <span style={BAD}>{snap.error}</span>
          : (
            <>
              <div>
                {running ? `Listening on ${snap.topic}... ${snap.elapsed}s` :
                  `Listened on ${snap.topic} for ${snap.elapsed}s`}
                {" - "}{msgs.length} message{msgs.length === 1 ? "" : "s"}
              </div>
              {snap.hint && <div style={BAD}>{snap.hint}</div>}
              {msgs.length > 0 && (
                <table className="small" style={{ width: "100%", marginTop: 4 }}>
                  <thead>
                    <tr><th align="left">time</th><th align="left">topic</th>
                        <th align="left">payload</th><th align="left">value</th></tr>
                  </thead>
                  <tbody>
                    {msgs.slice().reverse().map((m, i) => (
                      <tr key={`${m.t}-${i}`}>
                        <td>{clock(m.t * 1000)}</td>
                        <td style={MONO}>{m.topic}</td>
                        <td style={MONO}>{m.payload}</td>
                        <td style={m.error ? BAD : OK}>
                          {m.error ? m.error : m.value}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
      </div>
    </div>
  );
}

/** A Debug node's readings, newest first, with when each was seen. */
export function DebugHistory({ nodeId }) {
  const [, bump] = useState(0);
  useEffect(() => subscribe((id) => { if (id === nodeId) bump((n) => n + 1); }),
            [nodeId]);
  const list = historyOf(nodeId);
  return (
    <div style={{ marginTop: 8 }} data-debug-history="">
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <strong className="small">Messages</strong>
        <span className="muted small">{list.length} seen since this page opened</span>
        {list.length > 0 && (
          <button type="button" className="btn btn-sm"
                  onClick={() => clearHistory(nodeId)}>Clear</button>
        )}
      </div>
      {list.length === 0 ? (
        <p className="muted small">
          Nothing yet. Each new reading lands here with its time - for an MQTT
          Subscribe, each message with its topic and raw payload.
        </p>
      ) : (
        <ul style={{ listStyle: "none", margin: "4px 0", padding: 0,
                     maxHeight: 260, overflowY: "auto" }}>
          {list.slice().reverse().map((e, i) => (
            <li key={`${e.sig}-${i}`} className="small"
                style={{ borderTop: "1px solid var(--border, #ddd)", padding: "3px 0" }}>
              <span className="muted">{clock(e.t)}</span>{" "}
              {e.topic ? <span className="muted" style={MONO}>{e.topic} </span> : null}
              <strong style={e.ok === false ? BAD : undefined}>{String(e.value)}</strong>
              {e.payload !== undefined && (
                <div style={MONO}>payload: {e.payload}</div>
              )}
              {e.note && <div style={MONO}>{e.note}</div>}
              {e.ok === false && e.error && <div style={BAD}>{e.error}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
