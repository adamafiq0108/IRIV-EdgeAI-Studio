// The RTSP "Stream" choice on the camera form: the path AFTER host:port/.
//
// Cameras disagree on what that path is. TP-Link/Tapo answer stream1 (main)
// and stream2 (sub); Hikvision-style cameras serve Streaming/Channels/101
// (main) and /102 (sub) and treat /stream2 as the MAIN picture, so a form that
// offered only stream1/stream2 silently decoded 2560x1440 on those cameras.
// Detection should read the sub-stream, so the common brands are presets and
// anything else is a typed path. Pure on purpose: tests/check_rtspstream.mjs
// runs it in plain node, and the saved config format is untouched (the value
// is still just the text after host:port/).

export const RTSP_PRESETS = [
  { value: "stream1", label: "stream1 - full quality (TP-Link, Tapo)" },
  { value: "stream2", label: "stream2 - lighter substream (TP-Link, Tapo)" },
  { value: "Streaming/Channels/101", label: "Hikvision / Hilook - main stream (101)" },
  { value: "Streaming/Channels/102", label: "Hikvision / Hilook - sub-stream (102)" },
  { value: "cam/realmonitor?channel=1&subtype=0", label: "Dahua / Amcrest - main stream" },
  { value: "cam/realmonitor?channel=1&subtype=1", label: "Dahua / Amcrest - sub-stream" },
];

// The <select> value shown when the typed path matches no preset.
export const RTSP_CUSTOM = "__custom__";

export function isPreset(path) {
  return RTSP_PRESETS.some((p) => p.value === path);
}

// Clean a typed path. Whitespace, "@" and "#" would change how the URL is
// parsed (credentials, fragment), ".." climbs out of the path. "?", "=", "&",
// "/", ".", "_" and "-" are legitimate (Dahua queries, nested paths). An empty
// path is valid and means the default, stream1, as before.
export function normalizeCustomPath(path) {
  let p = String(path ?? "").trim();
  if (p.startsWith("/")) p = p.slice(1);
  if (/\s/.test(p)) return { ok: false, path: p, error: "The path cannot contain spaces." };
  if (/[@#]/.test(p)) return { ok: false, path: p, error: "The path cannot contain @ or #." };
  if (p.includes("..")) return { ok: false, path: p, error: "The path cannot contain \"..\"." };
  return { ok: true, path: p || "stream1", error: "" };
}

// What the preset <select> shows for the text box. A path that equals a preset
// (after the same trim and leading-"/" clean) selects it; anything else,
// including an empty or invalid box, selects "Custom". The box is the source
// of truth, so choosing a preset simply writes its value into the box.
export function selectValueFor(path) {
  const r = normalizeCustomPath(path);
  return r.ok && String(path ?? "").trim() !== "" && isPreset(r.path) ? r.path : RTSP_CUSTOM;
}
