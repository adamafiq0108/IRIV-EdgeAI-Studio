// probeHistory - what a Debug node has seen, with the time it saw it.
//
// The Debug node's face shows the CURRENT reading. Node-RED's debug tab shows
// a list - each message, when it came - and that list is what answers "is my
// topic right?": you publish, and a line appears. So every Debug node keeps
// the last MAX readings it saw change, here, in the browser, and its
// inspector lists them newest first.
//
// Browser-side on purpose: it is a view of the stats the page already polls,
// so it costs the box nothing and needs no new endpoint. The price is that it
// starts empty when the page opens and sees one reading per poll -- two MQTT
// messages inside one poll show as the later one. For an MQTT Subscribe the
// line is keyed on WHEN the message arrived (fn_<name>_at), so the same value
// sent twice is still two lines; for everything else a line is a CHANGE.
//
// Plain functions, no React, so tests/check_mqtttest.mjs runs them as-is.

export const MAX = 50;

const store = new Map();          // debug node id -> [entry, newest last]
const listeners = new Set();

function outStat(values, id, name) {
  return values ? values[`out_${id}_${name}`] : undefined;
}

/** One history line for what a probe reads now, or null for "nothing yet". */
export function entryFor(spec, values) {
  if (!spec || !values) return null;
  if (spec.kind === "value" && spec.key) {
    const v = values[spec.key];
    if (v === undefined) return null;
    // An MQTT Subscribe publishes its last raw message beside the value.
    const base = spec.key.endsWith("_value") ? spec.key.slice(0, -"value".length) : "";
    const at = base ? Number(values[`${base}at`]) || 0 : 0;
    const entry = { t: at ? at * 1000 : Date.now(), value: v,
                    sig: at ? `at:${at}` : `v:${JSON.stringify(v)}` };
    if (at) {
      entry.payload = String(values[`${base}payload`] ?? "");
      entry.topic = String(values[`${base}topic`] ?? "");
    }
    return entry;
  }
  if (spec.kind === "output" && spec.id) {
    const at = Number(outStat(values, spec.id, "at")) || 0;
    if (!at) return null;
    const ok = Number(outStat(values, spec.id, "ok")) === 1;
    return { t: at * 1000, value: ok ? "sent" : "FAILED", ok,
             note: String(outStat(values, spec.id, "note") || ""),
             error: String(outStat(values, spec.id, "error") || ""),
             sig: `at:${at}|${outStat(values, spec.id, "fired")}` };
  }
  return null;
}

/** Add a line unless it is the one already at the end. True when added. */
export function record(nodeId, entry) {
  if (!nodeId || !entry) return false;
  const list = store.get(nodeId) || [];
  if (list.length && list[list.length - 1].sig === entry.sig) return false;
  const next = [...list, entry].slice(-MAX);
  store.set(nodeId, next);
  listeners.forEach((fn) => fn(nodeId));
  return true;
}

export function historyOf(nodeId) {
  return store.get(nodeId) || [];
}

export function clearHistory(nodeId) {
  store.delete(nodeId);
  listeners.forEach((fn) => fn(nodeId));
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
