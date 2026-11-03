// Which camera the Dataset page captures from.
//
// A system with more than one camera lets the operator pick (owner request,
// 2026-10-13); a single-camera system shows no picker and sends no camera, so
// its requests are byte for byte what they were and the server keeps serving
// the first camera. The server checks every id it is sent against the
// system's own cameras and never falls back to another one (see
// core/services/dataset_capture.py, resolve_camera).
//
// Kept apart from the page so tests/check_datasetcamera.mjs can run it.

/** The system's cameras as GET /api/detector/dataset lists them (ids and
 *  names only; a source never reaches the page). */
export function cameraChoices(data) {
  return Array.isArray(data?.cameras) ? data.cameras.filter((c) => c && c.id) : [];
}

/** Only a choice when there is something to choose between. */
export function showPicker(cams) {
  return (cams || []).length > 1;
}

/** Where the page starts: the camera this system last captured from, else
 *  the default (the first running camera, what a bare request gets). */
export function initialCamera(data) {
  const cams = cameraChoices(data);
  const want = String(data?.camera || data?.default_camera || "");
  if (cams.some((c) => c.id === want)) return want;
  return cams[0]?.id || "";
}

/** A capture request's body, with the camera only when there is a choice. */
export function captureBody(base, cams, camera) {
  return showPicker(cams) && camera ? { ...base, camera } : { ...base };
}

/** The name to show for a camera id, or "" when it is not one of these. */
export function cameraName(cams, id) {
  return (cams || []).find((c) => c.id === id)?.name || "";
}
