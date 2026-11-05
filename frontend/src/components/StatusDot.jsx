// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// A status dot: green when something is working, red when it is not, grey
// when nobody knows yet. The same look as the AI dot (AiDot.jsx), so a
// canvas with an MQTT dot on a Publish node and an AI dot on a Detection
// Stream reads as one family of lights - and never a control.
//
// `ok` is true / false / null (unknown); `title` is the reason on hover and
// the accessible name; `label` adds words beside the dot; `attr` names the
// data-* attribute tests read the state off (data-<attr>="on|off|unknown").
export default function StatusDot({ ok, title, label = "", size = 9,
                                    attr = "status" }) {
  const state = ok === true ? "on" : ok === false ? "off" : "unknown";
  const colour = { on: "#22c55e", off: "#ef4444", unknown: "#9ca3af" }[state];
  const halo = { on: "rgba(34,197,94,.25)", off: "rgba(239,68,68,.25)",
                 unknown: "rgba(156,163,175,.25)" }[state];
  return (
    <span role="status" aria-label={title} title={title}
          {...{ [`data-${attr}`]: state }}
          style={{ display: "inline-flex", alignItems: "center", gap: 5,
                   fontSize: 11, whiteSpace: "nowrap", cursor: "default" }}>
      <span aria-hidden="true"
            style={{ width: size, height: size, borderRadius: "50%",
                     flexShrink: 0, background: colour,
                     boxShadow: `0 0 0 2px ${halo}` }} />
      {label && <span>{label}</span>}
    </span>
  );
}
