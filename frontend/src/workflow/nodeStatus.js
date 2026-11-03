// nodeStatus - what the small status dot on a canvas node says, as plain
// functions so tests/check_nodestatus.mjs can run them without React.
//
// The MQTT dot (client UAT 2026-10-04, item 1): green when the broker the
// node talks to is online, red when it is not, grey while nobody knows.
// A node with an MQTT Connection picked reads the app's own answer for
// that Connection (mqtt_<id>_ok / _error, published with the live values
// by app/mqtt_bus.py telemetry); a node on this box's broker (none picked)
// reads the broker page's status, app or no app, because that IS the
// broker it will talk to.
//
// The Inject dot (item 2): did the last injection pass or fail? "Passed"
// is defined from what the box knows: the app took the press and fired it
// (fn_<id>_ok = 1, app/functions.py _update_inject), and every output wired
// straight after it that has reported since (out_wf_<node>_at) reported
// success. "Failed" is a press the box did not take (dropped as stale, the
// POST refused, or no run counted within PRESS_WAIT_MS), the app being down,
// or an output after it failing. Grey until the first injection.
import { MQTT_NODE_TYPES } from "../components/brokerPill.js";

//: How long a press may go without the run counter moving before the dot
//: calls it failed: three polls of the live values, plus the app's own tick.
export const PRESS_WAIT_MS = 12000;

export function injectDot({ nodeId, data, values, appRunning, edges, pending,
                            now = Date.now() }) {
  const fid = String((data || {}).fnId || "");
  if (!fid) return null;
  const v = values || {};
  const runs = Number(v[`fn_${fid}_runs`]) || 0;
  if (pending) {
    if (pending.failed) {
      return { ok: false, title: `the last injection did not reach the box - ${pending.failed}` };
    }
    if (runs <= (Number(pending.runs) || 0)) {
      if (now - pending.at > PRESS_WAIT_MS) {
        return { ok: false, title: "the box did not take the last injection "
                                   + `(no run counted in ${PRESS_WAIT_MS / 1000} s)` };
      }
      return { ok: null, title: "injecting..." };
    }
  }
  if (!appRunning) {
    return { ok: false, title: "the system's app is not running - nothing to inject into" };
  }
  const ok = v[`fn_${fid}_ok`];
  if (ok === undefined || ok === null || !runs) {
    return { ok: null, title: "not injected yet" };
  }
  if (Number(ok) === 0) {
    return { ok: false, title: "the last injection was dropped - it reached the box too late" };
  }
  // Delivered downstream? Every output wired straight after this node
  // reports its last attempt; one that failed since the injection fails it.
  const at = Number(v[`fn_${fid}_last_at`]) || 0;
  let delivered = 0;
  for (const e of edges || []) {
    if (e.source !== nodeId) continue;
    const oid = `wf_${e.target}`.slice(0, 40);
    const outAt = Number(v[`out_${oid}_at`]) || 0;
    if (!outAt || (at && outAt < at - 1)) continue;
    if (Number(v[`out_${oid}_ok`]) === 0) {
      return { ok: false, title: "injected, but an output after it failed - "
                                 + `${v[`out_${oid}_error`] || "see the node"}` };
    }
    delivered += 1;
  }
  return { ok: true, title: `last injection passed - ${runs} so far`
                            + (delivered ? `, delivered to ${delivered} output${delivered > 1 ? "s" : ""}` : "") };
}

export function mqttDot({ type, data, values, appRunning, brokerUp }) {
  if (!MQTT_NODE_TYPES.includes(type)) return null;
  const conn = String((data || {}).connection || "").trim();
  if (!conn) {
    if (brokerUp === true) {
      return { ok: true, title: "MQTT broker: online (this box's broker)" };
    }
    if (brokerUp === false) {
      return { ok: false,
               title: "MQTT broker: offline - this box's broker is not running" };
    }
    return { ok: null, title: "MQTT broker: checking..." };
  }
  if (!appRunning) {
    return { ok: false, title: "MQTT broker: offline - the system's app is not "
                               + "running, so its connection is closed" };
  }
  const ok = (values || {})[`mqtt_${conn}_ok`];
  const why = String((values || {})[`mqtt_${conn}_error`] || "");
  if (ok === 1 || ok === true) {
    return { ok: true, title: "MQTT broker: online (connected)" };
  }
  if (ok === 0 || ok === false) {
    return { ok: false, title: `MQTT broker: offline - ${why || "not connected"}` };
  }
  return { ok: null, title: "MQTT broker: no connection status from the app yet" };
}
