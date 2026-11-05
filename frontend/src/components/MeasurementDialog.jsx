// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Measurement setup for a depth camera: calibrate the floor once, try a
// measurement by hand, and save which numbers each kind of object carries.
//
// THE ORDER ON THE PANEL IS THE ORDER OF THE WORK. Nothing can be measured
// until the floor is calibrated, because the object IS what stands above the
// floor -- so that is the first card, and the rest stay dim until it is done.
// Then a live picture to try on: pick a tool, tap, read the numbers. Then the
// profiles, which are the same tools saved against a class of object so every
// detection of that class carries the numbers chosen for it.
//
// TAPS ARE NORMALISED. The picture on screen is a preview at whatever size
// the browser gave it; the depth grid is the camera's 1280x720. A tap sent as
// pixels of the wrong picture lands on the wrong quarter of the scene, so
// every point leaves here as a fraction of the picture's width and height and
// the server scales it to the grid it actually has.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { Button } from "./ui/button.jsx";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "./ui/dialog.jsx";
import { Ruler, Crosshair, Circle, Pentagon, Minus, RotateCcw, Plus, Trash2, Loader2, RefreshCw } from "lucide-react";
import { apiUrl } from "../lib/base.js";

//: The tools, in the order a person reaches for them. `taps` is how many
//: points each needs before it can answer; 0 = no taps (auto finds the object).
//: Two taps is the unit of manual work. A side is two taps; another side is
//: two more, and they share nothing, so the three edges that matter on a
//: six-sided thing can be measured and the rest left alone. A circle is two
//: taps too, with the operator saying what they span -- rim to rim, or centre
//: to rim -- the way a person with a tape would take it.
const TOOLS = [
  { id: "auto", label: "Auto", icon: Crosshair, taps: 0,
    hint: "Finds the object standing in front of the remembered empty scene (background subtraction): its outline from colour, its size from depth, its angle and every side. Drag a box round it, or click once for the whole picture." },
  { id: "sides", label: "Sides", icon: Minus, taps: 2,
    hint: "Two taps per side: tap one end, then the other. Keep going for more sides - each pair is measured on its own." },
  { id: "circle", label: "Circle", icon: Circle, taps: 2,
    hint: "Two taps, then say what they span: across the whole circle (diameter) or from the centre to the edge (radius)." },
];

const fmt = (v, unit = "mm") => (v == null ? "-" : `${Math.round(v)} ${unit}`);

//: The numbers a profile can keep, and which tools produce each. A profile
//: with none ticked keeps everything its tool measures (measure.py FIELDS).
const FIELD_OPTIONS = [
  { id: "length_mm", label: "length", tools: ["auto"] },
  { id: "width_mm", label: "width", tools: ["auto"] },
  { id: "height_mm", label: "height", tools: ["auto", "circle"] },
  { id: "distance_mm", label: "distance", tools: ["auto", "circle"] },
  { id: "angle_deg", label: "angle", tools: ["auto"] },
  { id: "area_mm2", label: "area", tools: ["auto", "circle"] },
  { id: "perimeter_mm", label: "perimeter", tools: ["auto"] },
  { id: "tilt_deg", label: "tilt (3D)", tools: ["auto"] },
  { id: "volume_cm3", label: "volume (3D)", tools: ["auto"] },
  { id: "diameter_mm", label: "diameter", tools: ["auto", "circle"] },
  { id: "radius_mm", label: "radius", tools: ["auto", "circle"] },
];

export default function MeasurementDialog({ open, onOpenChange, camera, editable, classes }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  //: What the panel is doing while busy, in words. A disabled button with no
  //: change of text is "I clicked and nothing happened" -- which is exactly
  //: what was reported the first time somebody pressed Calibrate on a fit
  //: that takes a few seconds.
  const [phase, setPhase] = useState("");
  const [tool, setTool] = useState("auto");
  //: For the circle tool: what the two taps span.
  const [circleMode, setCircleMode] = useState("diameter");
  //: For Auto: 2D measures the extracted silhouette flat, at the object's
  //: distance; 3D fits the object's depth points, so a tilted object reads
  //: its true size, with tilt angle and bounding volume.
  const [dims, setDims] = useState("2d");
  //: The outline's corners as the operator has them, in the picture's pixels
  //: (fw x fh). Start as Auto found them; a drag moves one, a click on a side
  //: adds one, a double-click removes one. While a corner is being dragged
  //: the sides and angles are previewed from the picture alone; on release
  //: the server re-measures the shape with the depth under it.
  const [corners, setCorners] = useState(null);
  const [dragCorner, setDragCorner] = useState(null);       // index being moved
  //: Has the operator changed the corners Auto found? Then they are THEIR
  //: shape: Measure now re-measures it on the live depth rather than
  //: re-detecting, and Save as profile saves it, so the next object of the
  //: class gets the same corners re-placed inside its box.
  const [cornersEdited, setCornersEdited] = useState(false);
  const [points, setPoints] = useState([]);           // normalised taps
  const [box, setBox] = useState(null);               // normalised drag box, auto tool
  const [drag, setDrag] = useState(null);
  const [result, setResult] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [feedKey, setFeedKey] = useState(0);
  const imgRef = useRef(null);
  const cameraId = camera?.id;

  // The classes a profile can name: what the model is filtered to, else
  // everything it can emit. Fetched here rather than handed in, because the
  // page's status payload does not carry the label list and a dropdown built
  // from a guess is an empty dropdown.
  const [labels, setLabels] = useState([]);
  const load = useCallback(() => {
    if (!open || !cameraId) return;
    api.get(`/api/detector/control/cameras/measure?camera_id=${encodeURIComponent(cameraId)}`)
      .then((d) => { setState(d); setProfiles(d.profiles || []);
                     setSavedProfiles(JSON.stringify(d.profiles || [])); })
      .catch((e) => toast.error("Could not read the measurement setup", e.message));
    api.get("/api/detector/control/params")
      .then((d) => setLabels((d.classes && d.classes.length ? d.classes : d.labels) || []))
      .catch(() => setLabels(classes || []));
  }, [open, cameraId]);
  useEffect(load, [load]);
  useEffect(() => { if (open) { setPoints([]); setBox(null); setResult(null); setFeedKey((k) => k + 1); } }, [open]);
  // While the camera is NOT answering with depth, ask again every few
  // seconds. The commonest reason is that detection has not been started
  // yet; the operator starts it and comes back to a panel that already knows.
  useEffect(() => {
    if (!open || !state || state.live || !cameraId) return undefined;
    const t = setInterval(() => {
      api.get(`/api/detector/control/cameras/measure?camera_id=${encodeURIComponent(cameraId)}`)
        .then((d) => setState(d)).catch(() => {});
    }, 4000);
    return () => clearInterval(t);
  }, [open, state?.live, cameraId]);

  const floor = state?.floor;
  //: The remembered empty scene (background subtraction). Auto works from
  //: either this or the floor plane; this is what Calibrate always captures.
  const background = state?.background;
  const calibrated = !!(background || floor);
  const live = state?.live;
  const current = TOOLS.find((t) => t.id === tool) || TOOLS[0];

  // -- the floor -----------------------------------------------------------------
  async function calibrate() {
    if (!await confirm({
      title: "Remember the empty scene?",
      description: "Everything the camera sees RIGHT NOW becomes the background. "
        + "Clear the scene first - anything left in view becomes part of the "
        + "background and can never be measured, and anything placed in front "
        + "of it later is measured against it.",
      confirmLabel: "The scene is empty - calibrate",
    })) return;
    setBusy(true); setPhase("Calibrating - remembering the empty scene from several depth pictures…");
    try {
      const d = await api.post("/api/detector/control/cameras/measure/calibrate",
                               { camera_id: cameraId });
      toast.success("Scene remembered",
                    `${Math.round(100 * (d.background?.coverage || 0))}% of the picture has depth. `
                    + (d.floor
                        ? `Flat floor found too: ${Math.round(d.floor.centre_mm || 0)} mm away at the centre, camera tilted ${d.tilt_deg}°.`
                        : `No flat floor plane (${d.floor_problem || "not flat"}); heights are measured against the scene.`));
      load();
    } catch (e) {
      toast.error("Could not calibrate", e.message);
    } finally { setBusy(false); setPhase(""); }
  }

  async function clearFloor() {
    setBusy(true);
    try {
      await api.post("/api/detector/control/cameras/measure/clear", { camera_id: cameraId });
      load();
    } catch (e) { toast.error("Could not clear", e.message); }
    finally { setBusy(false); }
  }

  // -- the picture ---------------------------------------------------------------
  function norm(e) {
    const r = imgRef.current?.getBoundingClientRect();
    if (!r || !r.width || !r.height) return null;
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
            Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  }

  function onDown(e) {
    const p = norm(e);
    if (!p) return;
    if (tool === "auto") { setDrag({ from: p, to: p }); setBox(null); setResult(null); return; }
    setPoints((ps) => [...ps, p]);
    setResult(null);
  }
  function onMove(e) {
    if (!drag) return;
    const p = norm(e);
    if (p) setDrag((d) => ({ ...d, to: p }));
  }
  function onUp() {
    if (!drag) return;
    const { from, to } = drag;
    setDrag(null);
    const b = [Math.min(from[0], to[0]), Math.min(from[1], to[1]),
               Math.max(from[0], to[0]), Math.max(from[1], to[1])];
    // A click, not a drag: measure the whole picture.
    setBox(b[2] - b[0] < 0.01 || b[3] - b[1] < 0.01 ? null : b);
  }

  const ready = tool === "auto" ? calibrated
    : tool === "sides" ? points.length >= 2 && points.length % 2 === 0
    : points.length >= 2;

  async function measureNow(redetect = false) {
    // Corners the operator moved are the shape to measure, on the depth of
    // this moment -- until they ask to re-detect.
    if (tool === "auto" && cornersEdited && corners && !redetect) {
      await remeasureOutline(corners);
      return;
    }
    if (redetect) setCornersEdited(false);
    setBusy(true); setPhase("Measuring…");
    try {
      const body = { camera_id: cameraId, tool };
      if (tool === "auto") { if (box) body.box = box; } else body.points = points;
      if (tool === "circle") body.mode = circleMode;
      if (tool === "auto") body.dims = dims;
      const r = await api.post("/api/detector/control/cameras/measure/run", body);
      setResult(r);
      setCorners(r?.outline?.vertices ? r.outline.vertices.map((p) => [p[0], p[1]]) : null);
    } catch (e) {
      toast.error("Could not measure", e.message);
    } finally { setBusy(false); setPhase(""); }
  }

  //: The shape as the operator set it, re-measured with the depth under it.
  async function remeasureOutline(next) {
    if (!next || next.length < 3 || !result) return;
    setCornersEdited(true);
    setBusy(true); setPhase("Measuring your corners…");
    try {
      const r = await api.post("/api/detector/control/cameras/measure/run", {
        camera_id: cameraId, tool: "outline", dims,
        points: next.map(([u, v]) => [Math.min(1, Math.max(0, u / fw)), Math.min(1, Math.max(0, v / fh))]),
      });
      // The silhouette stays the extraction's; the corners and every number
      // measured from them are now the operator's.
      setResult({ ...r, outline: { ...r.outline, silhouette: result.outline?.silhouette || r.outline?.silhouette } });
    } catch (e) {
      toast.error("Could not measure the corners", e.message);
    } finally { setBusy(false); setPhase(""); }
  }
  //: Picture-plane preview of the corners while one is moving: the angle at
  //: each corner from the pixels alone, and each side scaled by the last
  //: measurement's millimetres-per-pixel -- close, and instant; the server's
  //: depth-true numbers replace them on release.
  function previewOutline(cs) {
    const n = cs.length;
    const px = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
    const sidesPx = cs.map((p, i) => px(p, cs[(i + 1) % n]));
    const prevPx = (result?.outline?.vertices || []).reduce((acc, p, i, arr) =>
      acc + px(p, arr[(i + 1) % arr.length]), 0);
    const mmPerPx = prevPx > 0 && result?.outline?.perimeter_mm ? result.outline.perimeter_mm / prevPx : null;
    const angles = cs.map((p, i) => {
      const a = cs[(i - 1 + n) % n], b = cs[(i + 1) % n];
      const v1 = [a[0] - p[0], a[1] - p[1]], v2 = [b[0] - p[0], b[1] - p[1]];
      const d = Math.hypot(...v1) * Math.hypot(...v2);
      return d > 0 ? Math.acos(Math.max(-1, Math.min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / d))) * 180 / Math.PI : null;
    });
    return { sides_mm: sidesPx.map((s) => (mmPerPx ? s * mmPerPx : null)), corner_angles_deg: angles };
  }
  function cornerFromEvent(e) {
    const r = imgRef.current?.getBoundingClientRect();
    if (!r) return null;
    return [Math.min(fw, Math.max(0, (e.clientX - r.left) / r.width * fw)),
            Math.min(fh, Math.max(0, (e.clientY - r.top) / r.height * fh))];
  }
  useEffect(() => {
    if (dragCorner == null) return undefined;
    const move = (e) => {
      const p = cornerFromEvent(e);
      if (p) setCorners((cs) => cs.map((c, i) => (i === dragCorner ? p : c)));
    };
    const up = () => {
      setDragCorner(null);
      setCorners((cs) => { remeasureOutline(cs); return cs; });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  }, [dragCorner]);      // eslint-disable-line react-hooks/exhaustive-deps

  // -- profiles -------------------------------------------------------------------
  function addProfile() {
    // The profile is the measurement just taken, saved: the tool, its taps,
    // for a circle what the taps span -- and the detected object the taps
    // were on, so the taps FOLLOW that class of object instead of staying
    // at a fixed spot the object walks out from under.
    // Auto with corners the operator moved: saved as an OUTLINE profile --
    // their corners, anchored to the detection under them, so every object
    // of the class gets the same shape re-placed inside its own box.
    if (tool === "auto" && cornersEdited && corners?.length >= 3) {
      const norm = corners.map(([u, v]) => [Math.min(1, Math.max(0, u / fw)), Math.min(1, Math.max(0, v / fh))]);
      let best = null, bestN = 0;
      for (const d of state?.detections || []) {
        const b = d.box || [];
        if (b.length !== 4) continue;
        const n = norm.filter(([u, v]) => b[0] <= u && u <= b[2] && b[1] <= v && v <= b[3]).length;
        if (n > bestN) { best = d; bestN = n; }
      }
      if (!best) {
        toast.warn("No detected object under the corners",
                   "The outline is saved as a fixed spot. Press Refresh with the object detected, then Follow object on the row.");
      }
      setProfiles((ps) => [...ps, { id: `m${Date.now().toString(36)}`, name: "", cls: best?.label || "",
                                    tool: "outline", enabled: true, dims,
                                    points: norm, anchor_box: best?.box || undefined }]);
      return;
    }
    const under = tool !== "auto" && result?.object?.box ? result.object : null;
    if (tool !== "auto" && !under) {
      toast.warn("No detected object under the taps",
                   "Measure once with the object in view, then Add: the profile then follows the object. Saved as a fixed spot for now.");
    }
    setProfiles((ps) => [...ps, { id: `m${Date.now().toString(36)}`, name: "", cls: under?.label || "",
                                  tool, enabled: true,
                                  mode: tool === "circle" ? circleMode : undefined,
                                  dims: tool === "auto" ? dims : undefined,
                                  points: tool === "auto" ? undefined : points,
                                  anchor_box: under?.box || undefined }]);
  }
  //: Anchor an existing fixed-spot profile to the detected object under its
  //: taps right now -- so a profile saved before anchoring existed, or one
  //: added with nothing in view, can be made to follow without re-tapping.
  function anchorToObject(i) {
    const p = profiles[i];
    const dets = state?.detections || [];
    let best = null, bestN = 0;
    for (const d of dets) {
      const b = d.box || [];
      if (b.length !== 4) continue;
      const n = (p.points || []).filter(([u, v]) => b[0] <= u && u <= b[2] && b[1] <= v && v <= b[3]).length;
      if (n > bestN) { best = d; bestN = n; }
    }
    if (!best) {
      toast.warn("No detected object under this profile's taps",
                 "Put the object where the taps are (it must be detected), press Refresh, then try again.");
      return;
    }
    setProfiles((ps) => ps.map((x, j) => j === i
      ? { ...x, anchor_box: best.box, cls: x.cls || best.label || "" } : x));
  }
  //: Something to save? The list as last saved, for the button to compare
  //: against -- so deleting the LAST profile still offers Save (an empty
  //: list is a change too), and an untouched list does not.
  const [savedProfiles, setSavedProfiles] = useState("[]");
  const profilesDirty = JSON.stringify(profiles) !== savedProfiles;
  //: Tick/untick one of a profile's numbers, or one of the auto outline's sides.
  function toggleIn(i, key, value) {
    setProfiles((ps) => ps.map((x, j) => {
      if (j !== i) return x;
      const have = new Set(x[key] || []);
      if (have.has(value)) have.delete(value); else have.add(value);
      return { ...x, [key]: [...have].sort((a, b) => (typeof a === "number" ? a - b : String(a).localeCompare(String(b)))) };
    }));
  }
  async function saveProfiles(next) {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/cameras/measure/profiles",
                               { camera_id: cameraId, profiles: next });
      setProfiles(d.profiles || []);
      setSavedProfiles(JSON.stringify(d.profiles || []));
      toast.success("Saved", next.length
        ? "Detections of those classes now carry these measurements."
        : "No profiles - detections carry their depth only.");
    } catch (e) { toast.error("Could not save profiles", e.message); }
    finally { setBusy(false); }
  }

  // Geometry the server sent back, drawn over the picture in normalised
  // coordinates so it lands where it was measured whatever the preview size.
  const fw = result?.frame?.width || live?.width || 1;
  const fh = result?.frame?.height || live?.height || 1;
  const toPct = ([u, v]) => ({ left: `${(u / fw) * 100}%`, top: `${(v / fh) * 100}%` });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="cy-app z-[80]" overlayClassName="z-[80]"
                     style={{ maxWidth: 820 }}>
        <DialogHeader>
          <DialogTitle>Measurement - {camera?.name || camera?.label || "camera"}</DialogTitle>
          <DialogDescription>
            Sizes in millimetres, from depth. Calibrate the floor once with the
            scene empty, then measure by hand or save what each kind of object
            should carry.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 py-1" style={{ maxHeight: "70vh", overflowY: "auto" }}>
          {!state && <p className="muted small">Reading the camera…</p>}
          {state && !state.is_depth && (
            <p className="c-warn small">This is not a depth camera - only a depth camera can measure.</p>
          )}

          {/* -- 1. the floor ---------------------------------------------- */}
          {state?.is_depth && (
            <section className="rounded-xl border p-4">
              <div className="flex items-start justify-between gap-3">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <strong>1. Empty scene</strong>
                  <p className="muted small" style={{ margin: "2px 0 0" }}>
                    {background
                      ? <>Remembered {background.at?.replace("T", " ") || ""}
                          {background.coverage != null ? `, ${Math.round(100 * background.coverage)}% of the picture with depth` : ""}.
                          Anything that later stands in front of it is an object
                          {background.rgb_file ? " - its outline traced in colour, its size from depth" : " (depth only - recalibrate for a colour outline)"}.
                          {floor
                            ? <> Flat floor found: {fmt(floor.centre_mm)} away at the centre, {floor.rms_mm != null ? `${floor.rms_mm.toFixed(1)} mm` : "-"} of scatter.</>
                            : <> No flat floor plane - heights are measured against the scene.</>}
                          {" "}Recalibrate if the camera or the scene has moved.</>
                      : floor
                        ? <>Floor plane calibrated {floor.at?.replace("T", " ") || ""} (older calibration, no
                            remembered scene). Recalibrate to use background subtraction.</>
                        : "Not calibrated. Clear everything from the camera's view, then press Calibrate: "
                          + "the camera remembers the empty scene, and an object is whatever later stands in front of it."}
                  </p>
                  {!live && (
                    <p className="c-warn small" style={{ margin: "6px 0 0", display: "flex",
                                                          alignItems: "center", gap: 6 }}>
                      <Loader2 size={12} className="animate-spin" style={{ flex: "0 0 auto" }} />
                      <span>
                        {state.live_problem || "The camera is not answering with depth right now."}
                        {" "}Calibrate unlocks when it does - checking every few seconds.
                      </span>
                    </p>
                  )}
                  {phase && (
                    <p className="small" style={{ margin: "6px 0 0", display: "flex",
                                                   alignItems: "center", gap: 6,
                                                   color: "var(--accent-text, #04bade)" }}>
                      <Loader2 size={12} className="animate-spin" /> {phase}
                    </p>
                  )}
                </div>
                <div className="flex gap-2" style={{ flex: "0 0 auto" }}>
                  {!live && (
                    <Button variant="outline" size="sm" disabled={busy} onClick={load}
                            title="Ask the camera again now">
                      <RefreshCw className="size-3.5" />
                    </Button>
                  )}
                  {calibrated && (
                    <Button variant="outline" size="sm" disabled={busy || !editable} onClick={clearFloor}
                            title="Forget the calibration">
                      <RotateCcw className="size-3.5" />
                    </Button>
                  )}
                  {/* The button says why it is off. A disabled button with no
                      reason is a click that does nothing, and that reads as
                      a broken page rather than a precondition. */}
                  <Button size="sm" disabled={busy || !editable || !live} onClick={calibrate}
                          title={!live ? "Start detection on this system first - the scene is read from the running camera"
                                 : !editable ? "You can view this camera but not change it"
                                 : "Remember the empty scene in view now"}>
                    {busy && phase.startsWith("Calibrating")
                      ? <Loader2 className="size-3.5 animate-spin" />
                      : <Ruler className="size-3.5" />}
                    {busy && phase.startsWith("Calibrating") ? "Calibrating…"
                      : calibrated ? "Recalibrate" : "Calibrate"}
                  </Button>
                </div>
              </div>
            </section>
          )}

          {/* The camera or the scene has moved since it was remembered: Auto
              would measure the wall. Said here, once, with the fix beside it. */}
          {state?.is_depth && state?.moved && (
            <section className="rounded-xl border p-4"
                     style={{ borderColor: "var(--warn, #f59e0b)", background: "rgba(245,158,11,.08)" }}>
              <div className="flex items-center gap-3">
                <div style={{ flex: 1 }}>
                  <strong className="c-warn">The picture no longer matches the remembered scene</strong>
                  <p className="muted small" style={{ margin: "2px 0 0" }}>
                    {Math.round(100 * (state.scene_drift || 0))}% of the depth picture has changed - the camera or
                    the scene has moved. Auto will measure against the wrong background until you clear the
                    view and recalibrate. Sides and Circle taps are unaffected: they read the live depth.
                  </p>
                </div>
                <Button size="sm" disabled={busy || !editable || !live} onClick={calibrate}>
                  <Ruler className="size-3.5" /> Recalibrate
                </Button>
              </div>
            </section>
          )}

          {/* -- 2. try it ---------------------------------------------------- */}
          {state?.is_depth && (
            <section className="rounded-xl border p-4" style={{ opacity: live ? 1 : 0.55 }}>
              <strong>2. Measure</strong>
              <div className="flex flex-wrap gap-2" style={{ margin: "8px 0" }}>
                {TOOLS.map((t) => (
                  <button key={t.id} type="button"
                          className={"btn btn-sm" + (tool === t.id ? " btn-accent" : "")}
                          onClick={() => { setTool(t.id); setPoints([]); setBox(null); setResult(null); }}
                          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <t.icon size={13} /> {t.label}
                  </button>
                ))}
                {tool === "auto" && (
                  <div className="login-tabs" style={{ margin: 0, padding: 2 }} role="tablist"
                       title="2D: the silhouette measured flat at the object's distance. 3D: the object's depth points - true size on a tilt, tilt angle, volume.">
                    {[["2d", "2D"], ["3d", "3D"]].map(([id, label]) => (
                      <button key={id} type="button" role="tab" aria-selected={dims === id}
                              className={"login-tab" + (dims === id ? " is-on" : "")}
                              style={{ padding: "3px 10px", fontSize: 11 }}
                              onClick={() => { setDims(id); setResult(null); }}>{label}</button>
                    ))}
                  </div>
                )}
                {tool === "circle" && (
                  <div className="login-tabs" style={{ margin: 0, padding: 2 }} role="tablist"
                       title="What the two taps span">
                    {[["diameter", "Diameter"], ["radius", "Radius"]].map(([id, label]) => (
                      <button key={id} type="button" role="tab" aria-selected={circleMode === id}
                              className={"login-tab" + (circleMode === id ? " is-on" : "")}
                              style={{ padding: "3px 10px", fontSize: 11 }}
                              onClick={() => { setCircleMode(id); setResult(null); }}>{label}</button>
                    ))}
                  </div>
                )}
                <div style={{ flex: 1 }} />
                {points.length > 0 && (
                  <button type="button" className="btn btn-sm"
                          onClick={() => { setPoints((ps) => ps.slice(0, -1)); setResult(null); }}
                          title="Take back the last tap">
                    Undo tap
                  </button>
                )}
                <button type="button" className="btn btn-sm" disabled={!points.length && !box}
                        onClick={() => { setPoints([]); setBox(null); setResult(null); }}>
                  Clear taps
                </button>
              </div>
              <p className="muted small" style={{ margin: "0 0 8px" }}>{current.hint}
                {tool === "auto" && !calibrated && " Calibrate first."}
                {result?.outline?.vertices && editable && (
                  <> Not quite on the edge? <strong>Drag a corner</strong> - the angles and sides follow as you
                  move, and are re-measured with depth when you let go. Click the small dot on a side to add a
                  corner; double-click a corner to remove it.</>
                )}</p>

              {/* The picture. A live MJPEG preview, tapped on. */}
              <div ref={imgRef}
                   style={{ position: "relative", width: "100%", aspectRatio: `${fw} / ${fh}`,
                            background: "#000", borderRadius: 8, overflow: "hidden",
                            cursor: tool === "auto" ? "crosshair" : "cell",
                            userSelect: "none", touchAction: "none" }}
                   onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
                   onPointerLeave={onUp}>
                {live ? (
                  <img alt="" draggable={false}
                       src={apiUrl(`/api/detector/preview?camera=${encodeURIComponent(cameraId)}&k=${feedKey}`)}
                       style={{ width: "100%", height: "100%", display: "block", objectFit: "fill",
                                pointerEvents: "none" }} />
                ) : (
                  <div className="muted small" style={{ position: "absolute", inset: 0,
                                                          display: "grid", placeItems: "center" }}>
                    Start detection to see the camera
                  </div>
                )}
                {/* The wait, where the eye already is. A spinner on a button
                    at the bottom of the card is missed; one over the footage
                    is not. */}
                {phase && (
                  <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center",
                                background: "rgba(0,0,0,.35)", pointerEvents: "none", zIndex: 2 }}>
                    <div style={{ display: "grid", placeItems: "center", gap: 8, color: "#fff",
                                  fontSize: 12 }}>
                      <Loader2 size={34} className="animate-spin" />
                      {phase}
                    </div>
                  </div>
                )}
                {/* the taps, joined in pairs for the Sides tool so an
                    unfinished side is visibly waiting for its second tap */}
                {tool === "sides" && points.length > 1 && (
                  <svg viewBox="0 0 1 1" preserveAspectRatio="none"
                       style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                                pointerEvents: "none" }}>
                    {points.map((pt, i) => (i % 2 === 1
                      ? <line key={i} x1={points[i - 1][0]} y1={points[i - 1][1]} x2={pt[0]} y2={pt[1]}
                              stroke="var(--accent, #04bade)" strokeWidth={0.003}
                              vectorEffect="non-scaling-stroke" /> : null))}
                  </svg>
                )}
                {/* taps */}
                {points.map(([u, v], i) => (
                  <span key={i} style={{ position: "absolute", left: `${u * 100}%`, top: `${v * 100}%`,
                                         width: 14, height: 14, marginLeft: -7, marginTop: -7,
                                         borderRadius: 999, border: "2px solid var(--accent, #04bade)",
                                         background: "rgba(4,186,222,.35)", fontSize: 9,
                                         color: "#fff", textAlign: "center", lineHeight: "10px",
                                         pointerEvents: "none" }}>{i + 1}</span>
                ))}
                {/* the drag box */}
                {(drag || box) && (() => {
                  const b = drag ? [Math.min(drag.from[0], drag.to[0]), Math.min(drag.from[1], drag.to[1]),
                                    Math.max(drag.from[0], drag.to[0]), Math.max(drag.from[1], drag.to[1])] : box;
                  return <div style={{ position: "absolute", left: `${b[0] * 100}%`, top: `${b[1] * 100}%`,
                                       width: `${(b[2] - b[0]) * 100}%`, height: `${(b[3] - b[1]) * 100}%`,
                                       border: "1px dashed var(--accent, #04bade)", pointerEvents: "none" }} />;
                })()}
                {/* what came back: the object's outline / circle */}
                {result?.outline?.vertices && (
                  <svg viewBox={`0 0 ${fw} ${fh}`} preserveAspectRatio="none"
                       style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                                pointerEvents: "none" }}>
                    {/* The extraction itself: the object's silhouette, dense,
                        filled -- what was cut out of the picture. The few
                        straight sides are drawn on it, numbered, so a side in
                        the list and a side on the object are the same edge. */}
                    <polygon points={(result.outline.silhouette?.length >= 3
                                        ? result.outline.silhouette : (corners || result.outline.vertices))
                                       .map((p) => p.join(",")).join(" ")}
                             fill="rgba(35,197,82,.28)" stroke="#23c552" strokeWidth={fw / 320}
                             strokeLinejoin="round" />
                    {(() => {
                      const cs = corners || result.outline.vertices;
                      const moving = dragCorner != null;
                      const live = moving ? previewOutline(cs) : result.outline;
                      const n = cs.length;
                      return cs.map((p, i) => {
                        const q = cs[(i + 1) % n];
                        const mm = live.sides_mm?.[i];
                        const ang = live.corner_angles_deg?.[i];
                        return <g key={i}>
                          <line x1={p[0]} y1={p[1]} x2={q[0]} y2={q[1]} stroke="#fff"
                                strokeWidth={fw / 500} strokeDasharray={`${fw / 200} ${fw / 300}`} />
                          {/* a side's midpoint: click to add a corner there */}
                          <circle cx={(p[0] + q[0]) / 2} cy={(p[1] + q[1]) / 2} r={fw / 260}
                                  fill="rgba(0,0,0,.5)" stroke="#fff" strokeWidth={fw / 900}
                                  style={{ pointerEvents: editable ? "all" : "none", cursor: "copy" }}
                                  onPointerDown={(e) => { e.stopPropagation(); e.preventDefault(); }}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    const next = [...cs.slice(0, i + 1), [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2], ...cs.slice(i + 1)];
                                    setCorners(next); remeasureOutline(next);
                                  }} />
                          {/* the corner: drag to move, double-click to remove */}
                          <circle cx={p[0]} cy={p[1]} r={fw / (moving && dragCorner === i ? 150 : 190)}
                                  fill={moving && dragCorner === i ? "#ffd166" : "#23c552"} stroke="#000" strokeWidth={fw / 900}
                                  style={{ pointerEvents: editable ? "all" : "none", cursor: "grab" }}
                                  onPointerDown={(e) => { e.stopPropagation(); e.preventDefault(); setCorners(cs.map((c) => [c[0], c[1]])); setDragCorner(i); }}
                                  onDoubleClick={(e) => {
                                    e.stopPropagation();
                                    if (n <= 3) return;
                                    const next = cs.filter((_, j) => j !== i);
                                    setCorners(next); remeasureOutline(next);
                                  }} />
                          {ang != null && (
                            <text x={p[0]} y={p[1] - fh / 45} fill="#ffd166" fontSize={fw / (moving ? 55 : 70)} textAnchor="middle"
                                  fontWeight={moving ? 700 : 400}
                                  style={{ paintOrder: "stroke", stroke: "#000", strokeWidth: fw / 350 }}>
                              {`${moving ? "~" : ""}${Math.round(ang)}°`}
                            </text>
                          )}
                          <text x={(p[0] + q[0]) / 2} y={(p[1] + q[1]) / 2 - fh / 90}
                                fill="#fff" fontSize={fw / 55} textAnchor="middle"
                                style={{ paintOrder: "stroke", stroke: "#000", strokeWidth: fw / 300 }}>
                            {mm == null ? `${i + 1}` : `${i + 1}: ${moving ? "~" : ""}${Math.round(mm)}`}
                          </text>
                        </g>;
                      });
                    })()}
                  </svg>
                )}
                {result?.segments && result.segments_mm && (
                  <svg viewBox={`0 0 ${fw} ${fh}`} preserveAspectRatio="none"
                       style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                                pointerEvents: "none" }}>
                    {result.segments.map(([a, b], i) => (
                      <g key={i}>
                        <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="#23c552"
                              strokeWidth={fw / 350} />
                        <text x={(a[0] + b[0]) / 2} y={(a[1] + b[1]) / 2 - fh / 60}
                              fill="#fff" fontSize={fw / 45} textAnchor="middle" fontWeight="700"
                              style={{ paintOrder: "stroke", stroke: "#000", strokeWidth: fw / 250 }}>
                          {result.segments_mm[i] == null ? "?"
                            : `${result.approx?.[i] ? "≈" : ""}${Math.round(result.segments_mm[i])} mm`}
                        </text>
                      </g>
                    ))}
                  </svg>
                )}
                {result?.shape === "circle" && result.centre && (result.radius_px || result.radius_mm) && (
                  <svg viewBox={`0 0 ${fw} ${fh}`} preserveAspectRatio="none"
                       style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                                pointerEvents: "none" }}>
                    <circle cx={result.centre[0]} cy={result.centre[1]}
                            r={result.radius_px || (result.radius_mm * (live?.intrinsics?.fx || 690) / (result.distance_mm || 1000))}
                            fill="rgba(35,197,82,.12)" stroke="#23c552" strokeWidth={fw / 350} />
                    {result.points?.length === 2 && (
                      <line x1={result.points[0].u} y1={result.points[0].v}
                            x2={result.points[1].u} y2={result.points[1].v}
                            stroke="#23c552" strokeWidth={fw / 500} strokeDasharray={`${fw / 150} ${fw / 200}`} />
                    )}
                    <text x={result.centre[0]} y={result.centre[1] - fh / 60}
                          fill="#fff" fontSize={fw / 45} textAnchor="middle" fontWeight="700"
                          style={{ paintOrder: "stroke", stroke: "#000", strokeWidth: fw / 250 }}>
                      {result.mode === "radius"
                        ? `r ${Math.round(result.radius_mm)} mm`
                        : `⌀ ${Math.round(result.diameter_mm)} mm`}
                    </text>
                  </svg>
                )}
                {/* the auto box: length and width on the object too, not
                    only in the list below */}
                {result?.shape === "box" && result.corners && (
                  <svg viewBox={`0 0 ${fw} ${fh}`} preserveAspectRatio="none"
                       style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                                pointerEvents: "none" }}>
                    <text x={result.corners.reduce((a, c) => a + c[0], 0) / 4}
                          y={result.corners.reduce((a, c) => a + c[1], 0) / 4}
                          fill="#fff" fontSize={fw / 45} textAnchor="middle" fontWeight="700"
                          style={{ paintOrder: "stroke", stroke: "#000", strokeWidth: fw / 250 }}>
                      {`${Math.round(result.length_mm)} × ${Math.round(result.width_mm)} mm`}
                      {result.height_mm != null ? ` · h ${Math.round(result.height_mm)}` : ""}
                    </text>
                  </svg>
                )}
              </div>

              <div className="flex items-center gap-2" style={{ marginTop: 8 }}>
                <Button size="sm" disabled={busy || !live || !ready} onClick={() => measureNow(false)}
                        title={!live ? "Start detection first" : !ready ? "Add the taps the tool needs"
                               : tool === "auto" && cornersEdited ? "Measure YOUR corners again on the depth of this moment" : ""}>
                  {phase.startsWith("Measuring") ? <Loader2 className="size-3.5 animate-spin" /> : <Ruler className="size-3.5" />}
                  {phase.startsWith("Measuring") ? "Measuring…"
                    : tool === "auto" && cornersEdited ? "Measure my corners" : "Measure now"}
                </Button>
                {tool === "auto" && cornersEdited && (
                  <Button variant="outline" size="sm" disabled={busy || !live} onClick={() => measureNow(true)}
                          title="Throw the moved corners away and let Auto find the outline afresh">
                    Re-detect
                  </Button>
                )}
                {!ready && tool !== "auto" && (
                  <span className="muted small">
                    {tool === "sides" && points.length % 2 === 1
                      ? "tap the other end of this side"
                      : `${Math.max(0, 2 - points.length)} more tap${2 - points.length === 1 ? "" : "s"}`}
                  </span>
                )}
                {ready && tool === "sides" && (
                  <span className="muted small">{points.length / 2} side{points.length === 2 ? "" : "s"} - tap two more for another</span>
                )}
                <div style={{ flex: 1 }} />
                {result && (
                  <Button variant="outline" size="sm" disabled={busy || !editable} onClick={addProfile}
                          title="Save this tool, and these taps, against a kind of object">
                    <Plus className="size-3.5" /> Save as profile
                  </Button>
                )}
              </div>

              {/* the numbers */}
              {result && (
                <div className="grid gap-1" style={{ marginTop: 10,
                     gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}>
                  {result.shape === "box" && <>
                    <Stat label="Length" value={fmt(result.length_mm)} />
                    <Stat label="Width" value={fmt(result.width_mm)} />
                  </>}
                  {result.shape === "circle" && <>
                    <Stat label="Diameter" value={fmt(result.diameter_mm)} />
                    <Stat label="Radius" value={fmt(result.radius_mm)} />
                  </>}
                  {result.height_mm != null && <Stat label="Height" value={fmt(result.height_mm)}
                                                     sub={background ? "in front of the scene" : "above the floor"} />}
                  {result.distance_mm != null && <Stat label="Distance" value={fmt(result.distance_mm)} sub="from the camera" />}
                  {result.angle_deg != null && <Stat label="Angle" value={`${result.angle_deg}°`} sub="of the long side in the picture" />}
                  {result.tilt_deg != null && <Stat label="Tilt" value={`${result.tilt_deg}°`} sub={floor ? "of the top, to the floor" : "of the top, to the camera"} />}
                  {result.volume_cm3 != null && <Stat label="Volume" value={`${Math.round(result.volume_cm3)} cm³`} sub="bounding, L × W × H" />}
                  {result.edge_distance_mm != null && result.edge_distance_mm !== result.distance_mm && (
                    <Stat label="Edge distance" value={fmt(result.edge_distance_mm)} sub="the outline is converted at this depth" />
                  )}
                  {result.dims && <Stat label="Measured" value={result.dims === "3d" ? "3D" : "2D"}
                                        sub={result.dims === "3d" ? `from ${result.points_3d || "-"} depth points` : "flat, at the object's distance"} />}
                  {result.area_mm2 != null && <Stat label="Area" value={fmt(result.area_mm2 / 100, "cm²")} />}
                  {result.segments_mm && result.segments_mm.map((s, i) => (
                    <Stat key={i} label={`Side ${i + 1}`}
                          value={s == null ? "no depth" : `${result.approx?.[i] ? "≈ " : ""}${fmt(s)}`}
                          sub={s == null ? "neither tap had a reading" : result.approx?.[i] ? "depth borrowed from just inside the edge" : undefined} />
                  ))}
                  {(result.notes?.length > 0 || (result.segments_mm || []).some((s) => s == null)) && (
                    <div className="muted small" style={{ gridColumn: "1 / -1" }}>
                      {result.notes?.map((n, i) => <div key={i}>{n}.</div>)}
                      No depth right under a tap happens at a dark edge, on something shiny, or when the
                      object is nearer than the camera measures (keep it past about 0.3 m). The tap is
                      kept where you put it; only the depth is read a little inward.
                    </div>
                  )}
                  {result.shape === "circle" && result.mode && (
                    <Stat label="Taps spanned" value={result.mode === "radius" ? "the radius" : "the diameter"} />
                  )}
                  {result.outline && <Stat label="Sides" value={`${result.outline.sides_mm.length}`}
                                           sub={`perimeter ${fmt(result.outline.perimeter_mm)}`} />}
                  {result.fit_rms_mm != null && <Stat label="Fit" value={`±${result.fit_rms_mm} mm`} sub="tap scatter" />}
                </div>
              )}
            </section>
          )}

          {/* -- 3. profiles -------------------------------------------------- */}
          {state?.is_depth && (
            <section className="rounded-xl border p-4">
              <div className="flex items-center gap-2">
                <strong style={{ flex: 1 }}>3. What each object carries</strong>
                <Button variant="outline" size="sm" disabled={busy || !editable} onClick={addProfile}>
                  <Plus className="size-3.5" /> Add
                </Button>
              </div>
              <p className="muted small" style={{ margin: "2px 0 8px" }}>
                One row per kind of object: which tool measures it, and which of its
                numbers to keep. Every detection of that class then carries them -
                drawn under its box on the live feed, logged by a Data Table column
                of the same name, and published as <code>measure_&lt;profile&gt;_…</code>
                for Workflow rules. A blank class means every object.
              </p>
              {!profiles.length && <p className="muted small">No profiles yet.</p>}
              <div className="grid gap-2">
                {profiles.map((p, i) => (
                  <div key={p.id || i} className="flex flex-wrap items-center gap-2 rounded-lg border p-2">
                    <input className="input" placeholder="Name (e.g. Carton)" value={p.name || ""}
                           style={{ flex: "1 1 120px" }} disabled={!editable}
                           onChange={(e) => setProfiles((ps) => ps.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
                    <select className="input" value={p.cls || ""} style={{ flex: "1 1 120px" }} disabled={!editable}
                            onChange={(e) => setProfiles((ps) => ps.map((x, j) => j === i ? { ...x, cls: e.target.value } : x))}>
                      <option value="">every object</option>
                      {(labels.length ? labels : (classes || [])).map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                    <select className="input" value={p.tool || "auto"} style={{ width: "auto" }} disabled={!editable}
                            onChange={(e) => setProfiles((ps) => ps.map((x, j) => j === i ? { ...x, tool: e.target.value } : x))}>
                      {TOOLS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                      {p.tool === "outline" && <option value="outline">Outline (your corners)</option>}
                    </select>
                    {(p.tool || "auto") === "auto" && (
                      <select className="input" value={p.dims === "3d" ? "3d" : "2d"} style={{ width: "auto" }} disabled={!editable}
                              title="2D: the silhouette flat at the object's distance. 3D: the object's depth points - true size on a tilt, tilt, volume."
                              onChange={(e) => setProfiles((ps) => ps.map((x, j) => j === i ? { ...x, dims: e.target.value } : x))}>
                        <option value="2d">2D - flat</option>
                        <option value="3d">3D - depth points</option>
                      </select>
                    )}
                    {p.tool === "circle" && (
                      <select className="input" value={p.mode || "diameter"} style={{ width: "auto" }} disabled={!editable}
                              title="What the two saved taps span"
                              onChange={(e) => setProfiles((ps) => ps.map((x, j) => j === i ? { ...x, mode: e.target.value } : x))}>
                        <option value="diameter">taps span the diameter</option>
                        <option value="radius">taps span the radius</option>
                      </select>
                    )}
                    {p.points?.length ? (
                      <span className="muted small"
                            title={p.anchor_box
                              ? "The taps were placed on a detected object and move with every object of this class"
                              : "The taps stay where they were placed in the picture; only an object standing there is measured"}>
                        {p.points.length} taps · {p.anchor_box ? "follows the object" : "fixed spot"}
                      </span>
                    ) : null}
                    {p.points?.length && !p.anchor_box ? (
                      <button type="button" className="btn btn-sm" disabled={!editable}
                              style={{ padding: "0 8px", lineHeight: "20px", fontSize: 11 }}
                              title="Tie the taps to the detected object under them now, so they move with every object of that class"
                              onClick={() => anchorToObject(i)}>
                        Follow object
                      </button>
                    ) : null}
                    <button className="icon-btn" title="Remove" disabled={!editable}
                            onClick={() => setProfiles((ps) => ps.filter((_, j) => j !== i))}>
                      <Trash2 size={13} />
                    </button>
                    {/* Which numbers this object carries. None ticked = all
                        the tool measures. The Sides tool measures exactly the
                        sides that were tapped, so it has nothing to tick. */}
                    {p.tool !== "sides" && (
                      <div className="muted small"
                           style={{ flexBasis: "100%", display: "flex", flexWrap: "wrap", gap: 4,
                                    alignItems: "center" }}>
                        <span style={{ marginRight: 2 }}>Keep:</span>
                        {FIELD_OPTIONS.filter((f) => f.tools.includes(p.tool || "auto")).map((f) => {
                          const on = !(p.fields || []).length || (p.fields || []).includes(f.id);
                          const explicit = (p.fields || []).includes(f.id);
                          return (
                            <button key={f.id} type="button" disabled={!editable}
                                    className={"btn btn-sm" + (explicit ? " btn-accent" : "")}
                                    style={{ padding: "0 8px", lineHeight: "20px", fontSize: 11,
                                             opacity: on ? 1 : 0.55 }}
                                    title={explicit ? "Kept - click to drop" : on ? "Kept (nothing ticked = everything) - click to keep only this and the ticked ones" : "Dropped - click to keep"}
                                    onClick={() => toggleIn(i, "fields", f.id)}>
                              {f.label}
                            </button>
                          );
                        })}
                        {!(p.fields || []).length && <span style={{ opacity: 0.7 }}>(all)</span>}
                      </div>
                    )}
                    {/* Which of the auto outline's sides matter. Side 1 runs
                        from the outline's first corner to the second, as
                        labelled on the picture after an Auto measurement;
                        none ticked = every side. */}
                    {(p.tool || "auto") === "auto" && (
                      <div className="muted small"
                           style={{ flexBasis: "100%", display: "flex", flexWrap: "wrap", gap: 4,
                                    alignItems: "center" }}>
                        <span style={{ marginRight: 2 }}>Sides:</span>
                        {Array.from({ length: Math.max(result?.outline?.sides_mm?.length || 0, 4,
                                                        ...(p.sides || []).map((s) => s + 1)) },
                                    (_, k) => k).map((k) => {
                          const on = (p.sides || []).includes(k);
                          return (
                            <button key={k} type="button" disabled={!editable}
                                    className={"btn btn-sm" + (on ? " btn-accent" : "")}
                                    style={{ padding: "0 8px", lineHeight: "20px", fontSize: 11 }}
                                    title={on ? `Side ${k + 1} is reported - click to drop`
                                              : `Report side ${k + 1}`}
                                    onClick={() => toggleIn(i, "sides", k)}>
                              {k + 1}
                            </button>
                          );
                        })}
                        {!(p.sides || []).length && <span style={{ opacity: 0.7 }}>(all sides)</span>}
                        {result?.outline?.sides_mm?.length
                          ? <span style={{ opacity: 0.7 }}>- numbered as on the picture above</span>
                          : <span style={{ opacity: 0.7 }}>- run Auto above to see them numbered</span>}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-end gap-2" style={{ marginTop: 8 }}>
                {profilesDirty && <span className="muted small">unsaved changes</span>}
                <Button size="sm" disabled={busy || !editable || !profilesDirty}
                        title={profilesDirty ? "Save this list, deletions included" : "Nothing changed"}
                        onClick={() => saveProfiles(profiles)}>
                  Save profiles
                </Button>
              </div>
            </section>
          )}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="rounded-lg border p-2">
      <div className="muted small">{label}</div>
      <div className="mono" style={{ fontSize: 15, fontWeight: 600 }}>{value}</div>
      {sub && <div className="muted" style={{ fontSize: 10.5 }}>{sub}</div>}
    </div>
  );
}
