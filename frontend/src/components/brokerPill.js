// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// brokerPill - what the Workflow page's MQTT broker pill says, as plain
// functions so tests/check_brokerpill.mjs can run them without React.

//: The node types that talk to a broker (core/services/workflow.py).
export const MQTT_NODE_TYPES = ["variable.mqtt", "output.mqtt", "output.mqtt_publish"];

export function hasMqttNode(nodes) {
  return (nodes || []).some((n) => MQTT_NODE_TYPES.includes(n?.type));
}

//: What each mode is called. Mirrors core/services/mqtt_broker.py
//: MODE_LABELS: who may connect (this box / the network) and whether they
//: log in are two separate settings.
export const MODE_LABELS = {
  local: "local only",
  local_secured: "local only, secured",
  secured: "network, secured",
  open: "network, open - no password",
};

//: The pill's tone and words for a status payload. A broker anyone on the
//: network can use without a password is running, and is still a warning.
export function pillFor(s) {
  if (!s) return { tone: "muted", text: "MQTT broker: checking..." };
  if (s.state === "running") {
    const how = MODE_LABELS[s.mode];
    return { tone: s.mode === "open" ? "warn" : "ok",
             text: `MQTT broker: running on :${s.port}` + (how ? ` (${how})` : "") };
  }
  if (s.state === "other") {
    return { tone: "muted", text: `MQTT broker: port ${s.port} is used by ${s.owner}` };
  }
  if (s.state === "not_installed") return { tone: "warn", text: "MQTT broker: not installed" };
  return { tone: "warn", text: "MQTT broker: not running" };
}
