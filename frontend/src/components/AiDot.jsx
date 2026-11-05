// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The "AI running" dot. A STATUS, never a control: Start / Stop detection stay
// where they always were.
//
// AI detection is a switch inside each system's one app (app/run.py
// ai_config), and the rest of the workflow runs whichever way it is set. So
// "is the system running" no longer answers "is the AI running" - this does.
// Green only when a real detector and a camera are actually producing frames
// (the app reports it, core detector_control._ai_ok); red otherwise, with the
// reason on hover ("AI detection is off", "inference failed: ...").
//
// `ai` is {ok, reason} (the workflow page's shape) or anything with ai_ok /
// ai_error (a /api/hub/pipelines row). `label` adds the words beside the dot.
export function aiOf(src) {
  if (!src) return { ok: false, reason: "" };
  if ("ok" in src) return { ok: !!src.ok, reason: src.reason || "" };
  return { ok: !!src.ai_ok, reason: src.ai_error || "" };
}

export default function AiDot({ ai, label = false, size = 9, badge = false }) {
  const { ok, reason } = aiOf(ai);
  const title = ok ? "AI running" : `AI off${reason ? ` - ${reason}` : ""}`;
  // Words only, no dot (System Control).
  if (badge) {
    return (
      <span role="status" aria-label={title} title={title} data-ai={ok ? "on" : "off"}
            style={{ fontSize: 12, fontWeight: 600, whiteSpace: "nowrap", cursor: "default",
                     color: ok ? "var(--ok-text, #16a34a)" : "var(--alert, #dc2626)" }}>
        {ok ? "AI on" : "AI off"}
      </span>
    );
  }
  return (
    <span role="status" aria-label={title} title={title}
          data-ai={ok ? "on" : "off"}
          style={{ display: "inline-flex", alignItems: "center", gap: 5,
                   fontSize: 11, whiteSpace: "nowrap", cursor: "default" }}>
      <span aria-hidden="true"
            style={{ width: size, height: size, borderRadius: "50%",
                     flexShrink: 0,
                     background: ok ? "#22c55e" : "#ef4444",
                     boxShadow: `0 0 0 2px ${ok ? "rgba(34,197,94,.25)"
                                                 : "rgba(239,68,68,.25)"}` }} />
      {/* One name, two colours: green while the AI is producing, red when not
          (the hover title says why). */}
      {label && <span style={{ fontWeight: 600,
                               color: ok ? "var(--ok-text, #16a34a)"
                                         : "var(--alert, #dc2626)" }}>AI Detection</span>}
    </span>
  );
}
