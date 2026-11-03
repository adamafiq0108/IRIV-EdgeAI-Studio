// The one read-only line under the Detection page's video that says who
// carries the live view when a system asks for [video] feed = "passthrough"
// (systems/demo/app/passthrough.py): the video gateway pulling the camera's
// main stream itself, or the app as before, and why.
//
// The app reports {feed, why} per camera in its state; the master merges it
// into the status the page already polls. No `view` at all (a system that
// never asked) is no line, so a default system's page is unchanged. Pure on
// purpose: tests/check_viewfeed.mjs runs it in plain node.

export function viewLine(view) {
  if (!view || typeof view !== "object") return "";
  const why = String(view.why || "").trim();
  if (view.feed === "passthrough") {
    return why
      ? `Video: direct from the camera (passthrough) - ${why}`
      : "Video: direct from the camera (passthrough)";
  }
  if (view.feed === "legacy") {
    return why
      ? `Video: through the app (legacy) - ${why}`
      : "Video: through the app (legacy)";
  }
  return "";
}
