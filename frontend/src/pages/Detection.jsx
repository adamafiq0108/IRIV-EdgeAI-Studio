// Detection - run the camera. That is the whole job of this page.
//
// It used to do four unrelated things: pick a model, choose a camera, run the
// app, and configure GPIO/MQTT outputs. Each of those has a better home now -
// models on AI Models (one page answers "which model"), outputs in the Workflow
// (where an output can be bound to a specific function instead of a bare
// trigger word) - so what is left is the live feed, Start/Stop, the camera, and
// the two capture switches.
//
// Everything here drives the LOCAL app through the master (config.local.toml
// writes + a same-box MJPEG proxy), so it needs no paired device: a standalone
// board watching itself. Zones are drawn on /zones; the app renders them into
// the stream itself, in frame coordinates, so they are aligned by construction.
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { usePoll, useVisibleInterval } from "../api/hooks.js";
import { ErrorNote, SkelEditor } from "../components/Loading.jsx";
import { Badge, Dialog } from "../components/ui/primitives.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { useConfirm } from "../components/ui/ask.jsx";
import { ZONE_COLORS } from "../components/ZoneCanvas.jsx";
import { Switch } from "../components/ui/switch.jsx";
import FeedStatusPanel from "../components/FeedStatusPanel.jsx";
import TutorialNav from "../components/TutorialNav.jsx";
import NextPageButton from "../components/NextPageButton.jsx";
import { Loader2, Play, Square, Camera as CameraIcon,
         ZoomIn, ZoomOut, Usb, Wifi, Network, FileVideo,
         Link2, Lock, Gauge, Check, VideoOff, RefreshCw, Cable,
         SlidersHorizontal, Ruler } from "lucide-react";
import { UsbPorts, EthPorts, useBoardPorts } from "../components/BoardPorts.jsx";
import { authHeaders } from "../lib/session.js";
import { camErrorText, camErrorToast, explainCamError, responseError } from "../lib/camErrors.js";
import { useSteadyRunning } from "../hooks/use-steady-running.js";
import { clickable } from "../hooks/use-escape.js";
import ZonesPanel, { ZONE_ACTIONS_SLOT } from "./Zones.jsx";
import CameraSettingsDialog from "../components/CameraSettingsDialog.jsx";
import MeasurementDialog from "../components/MeasurementDialog.jsx";
import { PasswordInput } from "../components/ui/input.jsx";
import { apiUrl } from "../lib/base.js";
import { askBoth, qualityView } from "../lib/qualityAsk.js";
import { RTSP_PRESETS, RTSP_CUSTOM, selectValueFor, normalizeCustomPath } from "../lib/rtspStream.js";
import { viewLine } from "../lib/viewFeed.js";
import VideoSavingPanel from "../components/VideoSavingPanel.jsx";

// The buses this hardware actually has. No CSI (no ribbon connector) and no
// Synthetic: a demo feed as a pickable option meant systems shipped watching a
// bouncing ball. A system with no camera now says so and asks for a real one.
// "file" is a recorded clip or a still played AS a camera: everything after
// capture -- zones, functions, tracker, outputs -- cannot tell the difference.
// It earns a place beside the buses because a camera that is unplugged, broken
// or not yet installed still leaves a system to build, and because counting
// logic is far easier to trust against footage that can be replayed.
const CAM_MODES = [["usb", "USB"], ["depth", "Depth (3D)"],
                   ["rtsp", "RTSP / IP"], ["gige", "GigE"],
                   ["file", "Video / Image"]];

// The running cap per bus, mirroring detector_control.KIND_LIMITS. USB and
// GigE hand this box raw frames (it does the whole capture); an IP camera
// encodes on its own silicon and only costs a decode here.
// No entry for "file": a clip contends for no bus and no camera, so the only
// limit that would apply is the decode itself.
const KIND_LIMITS = { usb: 2, gige: 2, rtsp: 4, depth: 1 };

// How many cameras one box may run (mirrors the server's cap).
// Four, not more: past that the Pi's CPU (grab + decode + encode per camera)
// runs out before the accelerator does. Mirrors the server's cap.
const MAX_CAMERAS = 4;

// The cap belongs to the accelerator, not to this system: every system on the
// box shares it. `budget` comes from the server ({max, used_here,
// used_elsewhere, remaining}); until it arrives, fall back to counting only
// this system's cameras so the button is never wrongly disabled.
const MAX_PER_SYSTEM = 4;

// How long the page believes its own Stop press while the system still
// reports running. Long enough for an app to finish a frame and quit, short
// enough that a stop which genuinely failed is not hidden.
const STOP_GRACE_MS = 15000;
// How long a "still stopping" report (status.stopping: the switch is off but
// the app has not released the camera and the chip yet) holds the Start
// button. Past this the app is not stopping, it is stuck: Start comes back
// with a note, so the operator is never left without a button.
const STOP_CONFIRM_MS = 20000;
// How soon the FIRST "stopped" answer is asked to prove itself. A stopped
// state has to survive two polls (use-steady-running.js), and waiting for
// the regular 4 s poll held the skeleton for 4 s on every open of a system
// that was simply switched off. The confirming poll comes early instead;
// the proof is kept, the wait is not.
const CONFIRM_STOPPED_MS = 700;

function camBudget(budget, camList) {
  if (budget && typeof budget.remaining === "number") return budget;
  return { max: MAX_CAMERAS, max_per_system: MAX_PER_SYSTEM,
           used_here: camList.length, used_elsewhere: 0,
           remaining: Math.max(0, Math.min(MAX_PER_SYSTEM - camList.length,
                                           MAX_CAMERAS - camList.length)),
           limit: "" };
}

// The camera list, however the server phrased it. `cameras` is the multi-camera
// form; `camera` (the first one) is still sent for older payloads, so a board
// that has not been updated yet still renders exactly one rail entry. An
// EXPLICIT empty `cameras` list means every camera was removed (a blank-canvas
// system) - falling back to the legacy object there resurrected a ghost row
// with no id, whose remove failed with "no camera None". The legacy fallback
// is only for payloads with no `cameras` at all, and it carries the id the
// legacy shape has always meant.
// CAMERA SNAPS: every frame a Snapshot Detection Stream has detected on this
// camera, newest first, with the boxes drawn on. The detector looks at nothing
// between triggers, so this is the only record of what it did look at -- kept
// next to the live feed it belongs to rather than only on the Footage page,
// where it would sit among event pictures. The pictures ARE snapshots (the
// same store, the same Footage page); the app marks them (kind "snap") and
// this picks them out for the camera on screen.
function CameraSnaps({ camera }) {
  const { data } = usePoll("/api/detector/snapshots?limit=300", 2000);
  const [open, setOpen] = useState(null);
  const rows = (data?.snapshots || []).filter(
    (r) => (r.kind === "snap" || String(r.id).includes("_snp_"))
      && String(r.camera) === String(camera.id));
  const shown = rows.slice(0, 24);
  const url = (id) => apiUrl(`/api/detector/snapshots/${encodeURIComponent(id)}`);
  const when = (r) => new Date(r.mtime * 1000).toLocaleTimeString();
  return (
    <div className="panel-card" style={{ alignSelf: "start", minWidth: 0 }}>
      <div className="section-title">Camera snaps ({rows.length})</div>
      <p className="muted small" style={{ marginTop: 0 }}>
        Snapshot mode: the live view above holds the last snap instead of
        playing video, and detection runs only on a snap.
      </p>
      {!rows.length ? (
        <p className="muted small">
          Nothing snapped yet. Each frame this camera detects when it is
          triggered is kept here, with its boxes.
        </p>
      ) : (
        <>
          <div style={{ display: "grid", gap: 6,
                        gridTemplateColumns: "repeat(auto-fill, minmax(104px, 1fr))" }}>
            {shown.map((r) => (
              <button key={r.id} type="button" title={`Snapped ${when(r)}`}
                      onClick={() => setOpen(r)}
                      style={{ padding: 0, border: "1px solid var(--line, rgba(128,128,128,.3))",
                               borderRadius: 6, overflow: "hidden", cursor: "pointer",
                               background: "#000", position: "relative" }}>
                <img src={url(r.id) + "?thumb=1"} alt={`Snap ${when(r)}`} loading="lazy"
                     style={{ display: "block", width: "100%", aspectRatio: "16/9",
                              objectFit: "cover" }} />
                <span style={{ position: "absolute", left: 0, right: 0, bottom: 0,
                               fontSize: 10, padding: "1px 4px", color: "#fff",
                               background: "rgba(0,0,0,.55)", textAlign: "left" }}>
                  {when(r)}
                </span>
              </button>
            ))}
          </div>
          <p className="muted small" style={{ marginTop: 6, marginBottom: 0 }}>
            {rows.length > shown.length
              ? `Showing the newest ${shown.length} of ${rows.length}. `
              : ""}
            Every snap is also on the <Link to="/footage">Footage</Link> page.
          </p>
        </>
      )}
      <Dialog open={!!open} title={open ? `Snapped ${when(open)}` : ""}
              onClose={() => setOpen(null)}>
        {open && (
          <img src={url(open.id)} alt={`Snap ${when(open)}`}
               style={{ display: "block", maxWidth: "100%", maxHeight: "70vh",
                        margin: "0 auto", borderRadius: 6 }} />
        )}
      </Dialog>
    </div>
  );
}

function camListOf(status) {
  if (!status) return [];
  if (Array.isArray(status.cameras)) return status.cameras;
  return status.camera ? [{ id: "main", ...status.camera }] : [];
}

// A source string, safe to put on screen. `rtsp://admin:f5f4f3f2f1@10.0.0.5/…`
// prints the camera's password to whoever is standing behind the operator,
// and it was being printed in the settings header.
// Everything up to the address's LAST "@" goes: a password holding "@" or "/"
// left part of itself on screen when this stopped at the first one.
export function camSourceSafe(source) {
  const s = String(source || "");
  const m = s.match(/^(rtsp:\/\/)(.*)$/i);
  if (!m) return s;
  const at = m[2].lastIndexOf("@");
  return at < 0 ? s : m[1] + m[2].slice(at + 1);
}

// The host[:port] of an IP-camera field: a bare address, or a pasted full
// URL with its credentials and path dropped.
export function camHost(field) {
  const rest = String(field || "").trim().replace(/^rtsp:\/\//i, "");
  return rest.slice(rest.lastIndexOf("@") + 1).split("/")[0];
}

// Names that describe HARDWARE rather than a place: a camera re-pointed from
// USB to RTSP keeps the label the scan gave it, so the header read "Editing
// Arducam B0495 (USB3 2.3MP)" over an rtsp:// source - a label actively
// contradicting what is running.
const BUS_LABEL = /(usb|\/dev\/video|gige|csi|mipi|webcam|\.mp4|\.mkv|\.avi)/i;

// Whether a stored name is the SCAN's, not the operator's. `label` is what
// the device enumeration reported, so a name equal to it (or built from it,
// as the backend does when it copies the label forward) was never chosen by
// anyone - "LRCP U3-imx307" over an rtsp:// source is the old USB device
// still introducing itself.
function isDeviceName(stored, cam) {
  if (!stored) return true;
  const label = String(cam?.label || "");
  if (label && (stored === label || stored.startsWith(`${label}:`))) return true;
  return BUS_LABEL.test(stored);
}

// The backend's own words for a camera failure, folded away. They are what
// support reads off a screenshot, so they stay one click from the plain
// sentence rather than being thrown away -- but they are not the headline.
function CamErrorDetails({ detail }) {
  if (!detail) return null;
  return (
    <details className="muted small" style={{ marginTop: 2 }}>
      <summary style={{ cursor: "pointer" }}>Details</summary>
      <code style={{ fontSize: 11.5, overflowWrap: "anywhere" }}>{detail}</code>
    </details>
  );
}

// The camera fault popup: what is wrong with ONE camera, by the name the
// rail uses, with the one thing to do and the raw words under Details. A
// popup rather than the red banner it replaces: the banner sat between the
// toolbar and the picture on every poll, and the only action on it
// (Reconnect) was easy to miss at the far right. Opened once per episode
// and closed by itself when the camera is back (see faultPopup in the
// page); the rail's status reopens it.
//
// Two faults read the same way: the app's live error while detection runs
// ("fault": Reconnect reopens the device) and the box's word that the
// camera is not there ("absent": Check again looks once more).
const NOT_THERE_CODES = new Set(["usb_unplugged", "usb_failed", "depth_not_found",
                                 "gige_none", "gige_failed"]);
const CANT_REACH_CODES = new Set(["net_unreachable", "net_dns", "net_refused",
                                  "net_failed", "open_failed"]);

function CamFaultDialog({ cam, fault, index, retrying, onClose, onRetry }) {
  if (!cam || !fault) return null;
  const slot = `Camera ${index || 1}`;
  const network = /:\/\//.test(String(cam.source || ""));
  let title, hint, detail, action;
  if (fault.kind === "absent") {
    title = network ? `Can't reach ${slot}` : `${slot} not found`;
    hint = network
      ? "Check the camera is powered on and on the same network."
      : "Check the USB cable is plugged in and the camera is switched on.";
    detail = network
      ? `${camSourceSafe(cam.source)} did not answer on its port`
      : `no USB video device answered for source '${camSourceSafe(cam.source)}'`;
    action = "Check again";
  } else {
    const x = explainCamError(fault.error, { retry: "Reconnect" });
    title = CANT_REACH_CODES.has(x.code) ? `Can't reach ${slot}`
      : NOT_THERE_CODES.has(x.code) ? `${slot} not found`
      : x.title;
    hint = x.fix;
    detail = x.detail;
    action = "Reconnect";
  }
  const name = camName(cam);
  const source = camSourceSafe(cam.source);
  return (
    <Dialog open title={title} onClose={onClose}
            footer={<>
              <button className="btn" onClick={onClose}>Close</button>
              <button className="btn btn-accent" disabled={retrying} onClick={onRetry}
                      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                {retrying ? <Loader2 className="animate-spin" size={14} /> : <RefreshCw size={13} />}
                {retrying ? (action === "Reconnect" ? "Reconnecting…" : "Checking…") : action}
              </button>
            </>}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <span style={{ flex: "none", width: 36, height: 36, borderRadius: 10,
                       display: "grid", placeItems: "center",
                       color: "var(--alert-text, #c81e1e)",
                       background: "color-mix(in srgb, var(--alert, #dc2626) 12%, transparent)" }}>
          <VideoOff size={18} />
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          {/* The name and the address under the heading: the heading names
              the slot, this says what is in it. */}
          <div className="muted small" style={{ overflowWrap: "anywhere", marginBottom: 6 }}>
            {name !== source && name !== slot ? <>{name} · </> : null}
            <code style={{ fontSize: 11.5 }}>{source || "-"}</code>
          </div>
          <p style={{ margin: "0 0 4px" }}>{hint}</p>
          <CamErrorDetails detail={detail} />
        </div>
      </div>
    </Dialog>
  );
}

// A camera failure where there is no card around it (the dialog's "Preview
// snapshot"): what happened, the one thing to do, then the details.
function CamErrorText({ x, style }) {
  return (
    <div className="small" role="alert" style={style}>
      <div style={{ fontWeight: 600, color: "var(--alert-text, #c81e1e)" }}>{x.title}</div>
      <div className="muted">{x.fix}</div>
      <CamErrorDetails detail={x.detail} />
    </div>
  );
}

// The word for a camera that is not there: a network camera is
// "Unreachable" (its host did not answer), anything plugged in "Not found".
// Same split the server's refusal uses (core/services/camera_notice.py).
function absenceWord(cam) {
  return /:\/\//.test(String(cam?.source || "")) ? "Unreachable" : "Not found";
}

// What is wrong with a camera RIGHT NOW, for the fault popup and the rail:
// null, or {kind, key, ...}. "fault" is the app's own live error while
// detection runs (never the amber "reconnecting", which is recovery, not a
// fault); "absent" is the box's word that the camera is not there -- the
// presence probe while stopped, camwatch's camera_missing while running.
// `key` names the EPISODE: one popup per key, however many polls repeat it.
function camFaultOf(cam, running, presence, status) {
  if (!cam || cam.enabled === false) return null;
  if (running) {
    if (status?.ai_paused === "camera_missing") {
      return { kind: "absent", key: `${cam.id}|absent` };
    }
    const err = cam.error ? String(cam.error) : "";
    if (!err || /^reconnecting camera/i.test(err)) return null;
    return { kind: "fault", key: `${cam.id}|${err}`, error: err };
  }
  if (presence[cam.id] === false) return { kind: "absent", key: `${cam.id}|absent` };
  return null;
}

// What to call a camera in the rail: the operator's label, else the raw spec.
function camName(cam) {
  if (!cam) return "camera";
  const stored = String(cam.name || cam.label || "");
  const src = String(cam.source || "");
  // A leftover DEVICE name over an IP source is replaced by the source
  // itself - the stream address IS the identity of an IP camera, and it is
  // the thing an operator checks against the camera's own config page. A
  // name the operator chose ("Front gate") is theirs and survives a re-point.
  if (/^rtsp:\/\//i.test(src) && isDeviceName(stored, cam)) return camSourceSafe(src);
  return stored || camSourceSafe(src) || "camera";
}


// ---- zoom & pan on the live feed -------------------------------------------
//
// WHY: marks are placed by clicking the picture, and at the sizes this is used
// at one screen pixel is about a millimetre of the real part - so click
// precision, not the measurement itself, is the biggest error left. Zooming in
// spends screen pixels on the corner being aimed at and divides that error by
// the zoom factor.
//
// HOW: purely a CSS transform on a wrapper around the <img> and the overlays.
// Nothing re-fetches, nothing remounts, so the MJPEG connection never notices -
// which is the whole reason the transform is on a wrapper and not on the image
// (a new <img>, or a new `src`, would drop the stream).
//
// The overlays keep working UNTOUCHED because they all map the pointer through
// `getBoundingClientRect()` of an element INSIDE the transform: that rect is
// the transformed one, so (clientX - rect.left) / rect.width is still the same
// frame fraction it was at zoom 1. The transform is therefore invisible to the
// stored 0..1 coordinates by construction, not by a second copy of the maths.
const FEED_MIN_ZOOM = 1;             // 1 = fit; there is no zooming out past it
// 8x: the operator's part spans ~107 px of a 640-wide frame (~0.93 mm per
// pixel), so 8x puts ~8 screen pixels on each frame pixel and takes a
// one-pixel aiming slip from ~0.9 mm to ~0.12 mm - level with the ±0.04 mm
// repeatability of the measurement itself. Past 8x there is nothing left to
// see: JPEG blur, not detail, is what gets bigger.
const FEED_MAX_ZOOM = 8;
const FEED_ZOOM_STEP = 1.4;          // one button press / one key press
const FEED_PAN_SLOP = 4;             // px of travel before a click becomes a pan

/** A view {z, x, y} - screen = picture * z + (x, y), in the viewport's own
 *  pixels, with the transform origin at the top-left corner.
 *
 *  Clamped so the picture can never be lost: the zoomed picture (w*z) must
 *  still cover the viewport (w), which pins x into [w - w*z, 0]. At z = 1 that
 *  interval collapses to {0}, i.e. exactly "fit". */
function clampFeedView(v, w, h) {
  const z = Math.min(FEED_MAX_ZOOM, Math.max(FEED_MIN_ZOOM, v.z || 1));
  const minX = w > 0 ? w - w * z : 0;
  const minY = h > 0 ? h - h * z : 0;
  return { z,
           x: Math.min(0, Math.max(minX, v.x || 0)),
           y: Math.min(0, Math.max(minY, v.y || 0)) };
}

/** The view zoomed to `z` about (sx, sy), a position in VIEWPORT pixels -
 *  the picture point under the pointer stays under the pointer. That is what
 *  lets someone drive a corner into the middle of the screen by pointing at
 *  it, instead of chasing it around after every zoom about the centre. */
function zoomFeedViewAt(v, z, sx, sy, w, h) {
  const next = Math.min(FEED_MAX_ZOOM, Math.max(FEED_MIN_ZOOM, z));
  const k = next / (v.z || 1);
  return clampFeedView({ z: next, x: sx - (sx - v.x) * k, y: sy - (sy - v.y) * k },
                       w, h);
}

/**
 * The feed's viewport: it owns the zoom/pan state, transforms its children as
 * one, and hands the view back down.
 *
 * `children` is a function (view, screenNode) => nodes:
 *   - `view.z` lets an overlay counter-scale its HANDLES, so a mark badge stays
 *     20 px at 8x instead of becoming a 160 px blob covering the very feature
 *     it is marking. Positions stay in picture space; only sizes are undone.
 *   - `screenNode` is a layer OUTSIDE the transform. Overlays portal their
 *     status bars into it, so a bar stays pinned, level and legible instead of
 *     sliding off with the picture.
 *
 * `resetKey` snaps back to fit when it changes (switching camera): a zoom kept
 * across a camera change would silently claim the new camera's corner is where
 * the old one's was.
 *
 * GESTURE MODEL - deliberately no modifier key and no mode to remember:
 *   click (no travel)      place / select, exactly as before
 *   drag                   pan, once the view is zoomed in and travel > 4 px
 *   drag ON a handle       move that mark/corner (the handle takes the pointer
 *                          first and stops it here)
 *   wheel / pinch          zoom about the pointer
 *   +/-/0 keys, toolbar    zoom in / out / fit
 * At zoom 1 there is nowhere to pan to, so a drag stays a click and the old
 * behaviour is bit-for-bit intact. When zoomed, a drag that actually moved the
 * picture swallows its trailing click, so panning never drops a stray mark.
 */
function FeedViewport({ resetKey, children, style }) {
  const [view, setView] = useState({ z: 1, x: 0, y: 0 });
  const [screen, setScreen] = useState(null);   // the un-transformed layer
  const hostRef = useRef(null);
  const viewRef = useRef(view);
  const pointers = useRef(new Map());   // live pointers, for pinch
  const panRef = useRef(null);          // {id, x, y, moved}
  const pinchRef = useRef(null);        // {dist, cx, cy}
  const pannedRef = useRef(false);      // this gesture moved the picture

  useEffect(() => { viewRef.current = view; }, [view]);

  const sizeOf = () => {
    const el = hostRef.current;
    return { w: el?.clientWidth || 0, h: el?.clientHeight || 0 };
  };

  // Snap back to fit when the thing being looked at changes.
  useEffect(() => { setView({ z: 1, x: 0, y: 0 }); }, [resetKey]);

  // The card got narrower: a pan that was legal at the old width can be past
  // the edge at the new one.
  useEffect(() => {
    const el = hostRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => {
      setView((cur) => clampFeedView(cur, el.clientWidth, el.clientHeight));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Wheel is bound by hand rather than with onWheel: React registers its wheel
  // listener passively, so preventDefault there is ignored and the page scrolls
  // out from under the zoom.
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      // deltaMode: 0 px, 1 lines, 2 pages. Normalised, or a line-mode mouse
      // would need fifty notches to reach 8x.
      const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1);
      // Read from the updater, never from this closure: this listener is
      // registered once, so a `view` captured here would be the view as it was
      // on mount and every notch would zoom from 1x again.
      setView((cur) => zoomFeedViewAt(cur, cur.z * Math.exp(-dy * 0.0025),
                                      e.clientX - r.left, e.clientY - r.top,
                                      el.clientWidth, el.clientHeight));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Pan and pinch. move/up live on the WINDOW so a pan that leaves the card
  // still tracks and still ends - the same arrangement the mark drags use.
  useEffect(() => {
    const move = (e) => {
      const el = hostRef.current;
      if (!el) return;
      if (pointers.current.has(e.pointerId)) {
        pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      }
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (pinchRef.current && pointers.current.size >= 2) {
        const [a, b] = [...pointers.current.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        const cx = (a.x + b.x) / 2;
        const cy = (a.y + b.y) / 2;
        const from = pinchRef.current;
        const r = el.getBoundingClientRect();
        pinchRef.current = { dist, cx, cy };
        pannedRef.current = true;
        setView((cur) => {
          // The two fingers both slide (pan) and spread (zoom); apply the slide
          // first, then zoom about where they now are.
          const slid = clampFeedView({ ...cur, x: cur.x + (cx - from.cx),
                                       y: cur.y + (cy - from.cy) }, w, h);
          return zoomFeedViewAt(slid, slid.z * (dist / from.dist),
                                cx - r.left, cy - r.top, w, h);
        });
        return;
      }
      const p = panRef.current;
      if (!p) return;
      const dx = e.clientX - p.x;
      const dy = e.clientY - p.y;
      if (!p.moved && Math.hypot(dx, dy) < FEED_PAN_SLOP) return;
      p.moved = true;
      p.x = e.clientX;
      p.y = e.clientY;
      // Only a zoomed-in view has anywhere to pan to; at fit, a shaky click is
      // still a click, exactly as it was before zoom existed.
      if (viewRef.current.z <= 1) return;
      pannedRef.current = true;
      setView((cur) => clampFeedView({ ...cur, x: cur.x + dx, y: cur.y + dy }, w, h));
    };
    const up = (e) => {
      pointers.current.delete(e.pointerId);
      if (pointers.current.size < 2) pinchRef.current = null;
      if (panRef.current?.id === e.pointerId) panRef.current = null;
      // Lifting one finger of a pinch hands the drag to the one still down,
      // rather than freezing until both are lifted.
      if (!panRef.current && pointers.current.size === 1) {
        const [[id, at]] = [...pointers.current.entries()];
        panRef.current = { id, x: at.x, y: at.y, moved: false };
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, []);

  function onPointerDown(e) {
    pannedRef.current = false;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size >= 2) {
      panRef.current = null;
      const [a, b] = [...pointers.current.values()];
      pinchRef.current = { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1,
                           cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
    } else {
      panRef.current = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
    }
  }

  // A pan ends with a click event, and that click would land on the marking
  // layer as a new mark. Swallowed in the CAPTURE phase, before the overlay
  // below ever sees it.
  function onClickCapture(e) {
    if (!pannedRef.current) return;
    pannedRef.current = false;
    e.stopPropagation();
  }

  const zoomBy = (k) => {
    const { w, h } = sizeOf();
    setView((cur) => zoomFeedViewAt(cur, cur.z * k, w / 2, h / 2, w, h));
  };

  function onKeyDown(e) {
    if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomBy(FEED_ZOOM_STEP); }
    else if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomBy(1 / FEED_ZOOM_STEP); }
    else if (e.key === "0") { e.preventDefault(); setView({ z: 1, x: 0, y: 0 }); }
  }

  const zoomed = view.z > 1.001;

  return (
    // `is-zoomed` switches the picture to nearest-neighbour: once magnified, a
    // smoothed image invents pixels between the real ones and hides exactly the
    // boundary the operator is trying to click on.
    //
    // touchAction is none, and the wheel is swallowed, at EVERY zoom - not only
    // while zoomed in: pinching and rolling are how you get from fit to 8x in
    // the first place, and a browser that keeps those gestures for itself
    // scrolls the page instead. The cost is that this one card is not a place
    // to scroll the page from; the rest of the page is.
    <div ref={hostRef} className={`feed-viewport${zoomed ? " is-zoomed" : ""}`}
         tabIndex={0}
         onPointerDown={onPointerDown} onClickCapture={onClickCapture}
         onKeyDown={onKeyDown}
         // The feed is an <img>, so a right-click here offers "Save image
         // as…", "Search image with Google" and the rest -- browser answers to
         // a question nobody asked, over a picture being used as a precision
         // instrument. Marking is a left-click job; the menu only ever lands
         // on top of the corner someone was aiming at.
         onContextMenu={(e) => e.preventDefault()}
         style={{ position: "relative", background: "#000", borderRadius: 8,
                  overflow: "hidden", minHeight: 200, touchAction: "none",
                  ...style }}>
      {/* Everything that belongs to the PICTURE, moved as one. The wrapper
          keeps minHeight in step with the viewport's so the overlays cover the
          same box they did before, including while the feed is stopped. */}
      <div style={{ position: "relative", minHeight: 200,
                    transformOrigin: "0 0", willChange: "transform",
                    transform: `translate(${view.x}px, ${view.y}px) scale(${view.z})` }}>
        {children(view, screen)}
      </div>
      {/* Everything that belongs to the SCREEN: status bars portal in here, so
          they neither pan away nor grow with the zoom. */}
      <div ref={setScreen} style={{ position: "absolute", inset: 0, zIndex: 3,
                                    pointerEvents: "none" }} />
      {/* stopPropagation: pressing a zoom button must not also start a pan or
          drop a mark on the picture underneath it. */}
      <div className="feed-zoombar" onPointerDown={(e) => e.stopPropagation()}
           onClick={(e) => e.stopPropagation()}>
        <button className="btn btn-sm" title="Zoom out (−)"
                disabled={view.z <= FEED_MIN_ZOOM + 1e-6}
                onClick={() => zoomBy(1 / FEED_ZOOM_STEP)}>
          <ZoomOut size={13} />
        </button>
        <span className="feed-zoomval" title="Magnification of the live picture">
          {view.z.toFixed(view.z < 10 ? 1 : 0)}×
        </span>
        <button className="btn btn-sm" title="Zoom in (+) - or roll the wheel over the picture"
                disabled={view.z >= FEED_MAX_ZOOM - 1e-6}
                onClick={() => zoomBy(FEED_ZOOM_STEP)}>
          <ZoomIn size={13} />
        </button>
        <button className="btn btn-sm" title="Back to the whole picture (0)"
                disabled={!zoomed} onClick={() => setView({ z: 1, x: 0, y: 0 })}>
          Fit
        </button>
      </div>
      {/* Said out loud rather than left as a hidden modifier: when there is
          somewhere to pan to, dragging is how you get there. */}
      {zoomed && (
        <div className="feed-zoomhint">drag to pan · wheel to zoom · 0 to fit</div>
      )}
    </div>
  );
}

export default function Detection() {
  const toast = useToast();
  const confirm = useConfirm();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [transition, setTransition] = useState(null); // "start" | "stop" | null
  const [busy, setBusy] = useState(false);
  //: Which camera's settings panel is open, as the CAMERA rather than an id:
  //: the dialog titles itself with the camera's name, and holding an id would
  //: mean looking it up again on every render to draw a heading.
  //:
  //: Declared HERE, with the other hooks, rather than beside the camera list
  //: it belongs to -- there are early returns between the two (error, still
  //: loading), so a hook down there runs on some renders and not others.
  //: React counts hooks per render and refuses the mismatch: "Rendered more
  //: hooks than during the previous render", which reaches the operator as a
  //: blank page and a minified error #310.
  const [settingsFor, setSettingsFor] = useState(null);
  //: Which depth camera's measurement setup is open. Same rule as above:
  //: declared with the hooks, above the early returns.
  const [measureFor, setMeasureFor] = useState(null);

  const [zones, setZones] = useState([]);
  // GigE exposure/gain ride in the same object as the size and the rate: they
  // are camera settings, saved by the same Apply, and keeping them in a state
  // of their own only means a second thing to remember to send.
  const [camera, setCamera] = useState({
    source: "", width: 640, height: 480, fps: 10,
    exposure_auto: "on", exposure_us: 10000, gain_auto: "on", gain_db: 0,
    binning: 1,
  });

  // Camera picker: discovered cameras grouped by connection, plus manual entry.
  // It lives in a panel that opens from the header - choosing a camera is a
  // setup act, watching the feed is the daily one, and the daily one gets the
  // room.
  const [camOpen, setCamOpen] = useState(false);
  // Shadows camOpen so the 4s status poll can see it without being rebuilt
  // (and restarting its interval) every time the panel opens.
  const camOpenRef = useRef(false);
  useEffect(() => { camOpenRef.current = camOpen; }, [camOpen]);
  // {max, used_here, used_elsewhere, remaining} -- the ACCELERATOR's camera
  // budget, shared by every system on this box, so adding one here has to
  // know what the neighbours use. null until the first fetch answers.
  const [camBudgetRaw, setCamBudgetRaw] = useState(null);
  const [cameras, setCameras] = useState({ usb: [], gige: [], depth: [] });
  const [camMode, setCamMode] = useState("usb");     // usb | depth | rtsp | gige | file
  //: Which depth camera, as its source string ("orbbec:<serial>"). One on the
  //: box needs no choosing, so this stays empty and pickedSource falls back to
  //: the unqualified "orbbec".
  const [depthPick, setDepthPick] = useState("");
  // Footage uploaded for this system, and which of it is picked. Detection
  // runs against a clip exactly as it runs against a camera.
  const [media, setMedia] = useState([]);
  const [mediaPick, setMediaPick] = useState("");
  const [mediaBusy, setMediaBusy] = useState(false);
  const [mediaMsg, setMediaMsg] = useState("");
  // RTSP camera preference: assembled into rtsp://user:pass@ip:<port>/<stream>,
  // the port 554 (the RTSP standard) unless the camera was moved off it.
  // Prefilled from the saved camera source, so once applied the operator never
  // re-types it (Reset clears the fields).
  const [rtsp, setRtsp] = useState({ ip: "", user: "", pass: "", port: "554", stream: "stream1" });
  // rtsp.stream is the one source of truth: the text box edits it, the preset
  // select just writes into it and reads back (selectValueFor), so a saved
  // path that is no preset shows in the box with "Custom" selected.
  const streamPath = normalizeCustomPath(rtsp.stream);
  const [showPass, setShowPass] = useState(false);
  const [rtspHosts, setRtspHosts] = useState(null);  // null = not scanned yet
  const [scanning, setScanning] = useState(false);
  const [gigeName, setGigeName] = useState("");      // "" = first GigE camera
  // The faceplate panel inside the camera dialog: which socket/jack is in
  // use, drawn like the board. One flag - it shows the panel matching the
  // MODE (USB sockets for usb, ethernet jacks for rtsp/gige), and closing
  // the dialog or switching modes drops it so the poll stops.
  const [portsOpen, setPortsOpen] = useState(false);
  const boardPorts = useBoardPorts(portsOpen);
  useEffect(() => { setPortsOpen(false); }, [camMode]);
  const [preview, setPreview] = useState(null);      // object URL of a snapshot
  const [previewMsg, setPreviewMsg] = useState("");
  // Resolution: OFF is the safe 640x480 every camera has run at, and the
  // fields stay hidden -- the rate and size balancing themselves is what most
  // installs want. ON reveals the picker. Derived from the SAVED size rather
  // than stored separately: a camera already on 1080p must come back with the
  // switch on, and a second field saying the same thing could disagree with it.
  const [hiRes, setHiRes] = useState(false);
  // What the camera itself says it can do. null = not asked yet.
  const [resCaps, setResCaps] = useState(null);
  const [probing, setProbing] = useState(false);
  //: What the camera answered about RATES, and whether that probe is running.
  //: Separate from resCaps: the two questions are asked by two buttons and
  //: either can be unanswered while the other is not.
  const [fpsCaps, setFpsCaps] = useState(null);
  const [probingFps, setProbingFps] = useState(false);
  //: Has the one "Ask the camera" button been pressed for this camera? Until
  //: it has, the size and rate controls (and their values) stay hidden.
  //: Reset on selecting another camera and on opening/closing the panel.
  const [asked, setAsked] = useState(false);
  const [askingBoth, setAskingBoth] = useState(false);

  // Which camera the feed, the rail highlight and the picker are about.
  // The ref shadows it so the 4s poll can read the current selection without
  // being rebuilt (and restarting the interval) every time it changes.
  const [selectedCam, setSelectedCam] = useState(null);
  const selectedCamRef = useRef(null);
  // The picker is also the ADD form. When this is set, Apply creates a camera
  // instead of editing the selected one.
  const [addingCam, setAddingCam] = useState(false);
  const addingCamRef = useRef(false);   // read by the poll, which must not
                                        // overwrite a half-filled add form

  // Reverse-parse a saved source spec back into the picker fields.
  // `force` = the operator just switched camera, so the picker must follow it;
  // without it (the poll) only the untouched defaults are filled in, because
  // overwriting a half-typed RTSP address every 4 seconds is unusable.
  const applySource = useCallback((source, force) => {
    const src = String(source || "");
    if (src.startsWith("rtsp://")) {
      const m = src.match(/^rtsp:\/\/(?:([^:@/]*)(?::([^@/]*))?@)?([^:/@]+)(?::(\d+))?\/?(.*)$/);
      if (m) {
        // The IP and the stream are filled in; the CREDENTIALS are not.
        // A prefilled "admin" reads as a value the operator typed, and a
        // prefilled password is a saved secret on display. Left blank they
        // mean "keep the ones already saved" (see pickedSource), so a camera
        // is edited without ever retyping them.
        const next = {
          ip: m[3] || "", user: "", pass: "", port: m[4] || "554",
          stream: m[5] || "stream1",
        };
        setRtsp((r) => (force || !r.ip ? next : r));
        setCamMode((mode) => (force || mode === "usb" ? "rtsp" : mode));
      }
    } else if (src.startsWith("file:")) {
      const name = src.slice(5);
      setMediaPick((n) => (force ? name : n || name));
      setCamMode((mode) => (force || mode === "usb" ? "file" : mode));
    } else if (src.startsWith("gige")) {
      const name = src.includes(":") ? src.split(":").slice(1).join(":") : "";
      setGigeName((n) => (force ? name : n || name));
      setCamMode((mode) => (force || mode === "usb" ? "gige" : mode));
    } else if (src.startsWith("orbbec")) {
      // Opening an existing depth camera lands on the mode that can edit it.
      // Without this the panel opens on USB, showing a device list this
      // camera is not in, and Apply would re-point it at a video node.
      setDepthPick((n) => (force ? src : n || src));
      setCamMode((mode) => (force || mode === "usb" ? "depth" : mode));
    } else if (force) {
      setCamMode("usb");
    }
  }, []);

  // Whether any status has landed yet: only the very first answer gets the
  // early confirming poll (CONFIRM_STOPPED_MS).
  const hadStatusRef = useRef(false);
  const loadStatus = useCallback(function load(attempt = 0) {
    api.get("/api/detector/control")
      .then((d) => {
        if (!hadStatusRef.current && !d.running) {
          setTimeout(() => load(), CONFIRM_STOPPED_MS);
        }
        hadStatusRef.current = true;
        setStatus(d); setError(null);
        setZones(d.zones || []);

        // Keep the selection across polls; only fall back to the first camera
        // when nothing is selected yet or the selected one has gone away.
        const cams = camListOf(d);
        let cur = selectedCamRef.current;
        if (cur == null || !cams.some((c) => String(c.id) === String(cur))) {
          cur = cams.length ? cams[0].id : null;
          selectedCamRef.current = cur;
          setSelectedCam(cur);
        }
        // The picker is the operator's WORKSPACE while it is open: a
        // background poll refilling it from the saved camera would undo every
        // edit they have not applied yet -- which is exactly what happened to
        // the High resolution switch, flipped back within 4s of being turned
        // on because the saved camera was still 640x480. Mid-add the picker
        // describes a camera that does not exist yet at all.
        if (addingCamRef.current || camOpenRef.current) return;
        const cam = cams.find((c) => String(c.id) === String(cur)) || d.camera || {};
        setCamera({ source: cam.source ?? "", width: cam.width ?? 640,
                    height: cam.height ?? 480, fps: cam.fps ?? 10,
                    // A camera saved before these existed reads as continuous,
                    // which is what it has been running as.
                    exposure_auto: cam.exposure_auto ?? "on",
                    exposure_us: cam.exposure_us ?? 10000,
                    gain_auto: cam.gain_auto ?? "on",
                    gain_db: cam.gain_db ?? 0,
                    binning: cam.binning ?? 1 });
        setHiRes((cam.width ?? 640) !== 640 || (cam.height ?? 480) !== 480);
        // Prefill the picker from the SAVED source, so re-opening the page
        // shows the camera that is actually configured. Only "usb" is safe to
        // overwrite - it is the initial default, so leaving it alone would
        // show a USB tab for a board running an IP camera. Anything the
        // operator has since touched is left as they left it.
        applySource(cam.source, false);
      })
      .catch((e) => {
        // Same reason as the Zones panel's own load: a timeout while the
        // feed is opening is this page competing with its own stream for the
        // browser's connections, not a system that is down. Ask again before
        // replacing the page with an error.
        if (e?.status === 0 && attempt < 2) {
          setTimeout(() => load(attempt + 1), 1200);
          return;
        }
        setError(e);
      });
  }, [applySource]);

  const loadCameras = useCallback(() => {
    api.get("/api/detector/cameras")
      .then((d) => setCameras({ usb: d.usb || [], gige: d.gige || [],
                                depth: d.depth || [] }))
      .catch(() => setCameras({ usb: [], gige: [], depth: [] }));
  }, []);

  const loadMedia = useCallback(() => {
    api.get("/api/media")
      .then((d) => setMedia(d.media || []))
      .catch(() => setMedia([]));
  }, []);

  // How much of the box's camera budget is left. Refetched after every add or
  // remove, because another system's cameras count against the same total.
  const loadBudget = useCallback(() => {
    api.get("/api/detector/control/camera")
      .then((d) => setCamBudgetRaw(d.budget || null))
      .catch(() => {});
  }, []);

  // Is each camera on the box RIGHT NOW -- {id: true | false | null}, and
  // whether a look is still out (`looking`: the rail says Checking until
  // the first answer). Asked while detection is STOPPED: with no app
  // holding the camera, the status payload can only repeat what the last
  // run saw, and a rail reading Live beside a toast saying the camera was
  // not found is how this started. Never awaited by the page: the cards
  // draw at once and update when the probes answer (about a second).
  const [presence, setPresence] = useState({});
  const [looking, setLooking] = useState(false);
  const probePresence = useCallback(() => {
    setLooking(true);
    api.get("/api/detector/control/cameras/presence")
      .then((d) => setPresence(d.cameras || {}))
      .catch(() => setPresence({}))
      .finally(() => setLooking(false));
  }, []);

  useEffect(() => { loadStatus(); loadCameras(); loadBudget(); loadMedia(); },
            [loadStatus, loadCameras, loadBudget, loadMedia]);
  // The budget moves when ANOTHER system starts or stops, which nothing on
  // this page announces -- poll it gently so the Add camera button appears
  // and disappears by itself as the box's headroom changes.
  // Paused while the tab is hidden, refetched on return (useVisibleInterval).
  useVisibleInterval(loadBudget, 10000);

  // Poll running status while on the page so Start/Stop reflect reality, and
  // refetch the moment the tab is shown again - detection switched on from
  // System Control must not read as stopped here.
  useVisibleInterval(loadStatus, 4000);
  // Only a Stop the operator pressed themselves shows "stopped" at once;
  // any other running=false has to survive two polls first. The intent
  // outlives the request (transition clears before the next poll lands) and
  // is forgotten the moment the system reports running again.
  // Bumped when a camera entry is re-pointed, switched on/off or removed:
  // the stream is one long-lived MJPEG connection keyed by camera id, so
  // without this an edited camera kept showing the OLD device's picture
  // until the page was reloaded.
  const [feedNonce, setFeedNonce] = useState(0);
  const [stopIntent, setStopIntent] = useState(false);
  const stopAt = useRef(0);
  useEffect(() => {
    if (!status) return;
    // The intent survives polls that still say "running": the app takes a
    // moment to wind down, and clearing on the first such poll flipped the
    // page back to a live-looking feed a second after the press -- the whole
    // reason stopping felt slow. It is dropped once the system agrees it has
    // stopped, and abandoned after STOP_GRACE_MS so a stop that genuinely
    // failed surfaces as running rather than as a permanent lie.
    if (!status.running) { stopAt.current = 0; return; }
    if (stopIntent && stopAt.current
        && Date.now() - stopAt.current > STOP_GRACE_MS) {
      setStopIntent(false);
      stopAt.current = 0;
    }
  }, [status, stopIntent]);
  const steadyRunning = useSteadyRunning(status, { immediate: stopIntent });
  // Start holds while the previous run winds down -- whoever pressed Stop,
  // this tab or another: the status says so (stopping), not the press.
  // Timed from when it was FIRST seen; past STOP_CONFIRM_MS it is let go.
  const [stoppingSince, setStoppingSince] = useState(0);
  useEffect(() => {
    if (!status) return;
    setStoppingSince((cur) => (status.stopping ? (cur || Date.now()) : 0));
  }, [status]);
  const stoppingTimedOut = !!stoppingSince && Date.now() - stoppingSince > STOP_CONFIRM_MS;
  const stopping = !!status?.stopping && !stoppingTimedOut;
  useEffect(() => {
    if (!stoppingTimedOut) return;
    toast.error("Detection has not confirmed it stopped",
                "The app did not release the camera and the AI chip within "
                + "20 seconds. Start is available again; if it fails, check "
                + "System Control.");
  }, [stoppingTimedOut, toast]);
  // A real look at every camera the moment the page knows detection is
  // stopped, again when the list changes (a camera added or re-pointed),
  // and gently while it stays stopped -- plugging the camera in should
  // turn Not found into Live without a reload. Nothing while running: the
  // app holds the camera then, and ITS word (error, camera_missing) is the
  // truth the rail follows.
  const camIds = camListOf(status).map((c) => c.id).join(",");
  const stopped = steadyRunning === false;
  useEffect(() => { if (stopped) probePresence(); }, [stopped, camIds, probePresence]);
  useVisibleInterval(() => { if (stopped) probePresence(); }, 10000);

  // The camera fault POPUP (CamFaultDialog below). Opens by itself once per
  // failure episode of the selected camera -- not on every poll that
  // repeats it -- and closes by itself when the camera is back. After
  // Close, the rail's status is what is left, and clicking it reopens.
  // `faultSeen` remembers the episodes already shown; `retrying` is the
  // Reconnect's spinner, let go when the fault changes or clears.
  const [faultPopup, setFaultPopup] = useState(null);     // camera id | null
  // What the FEED is showing for the selected camera, from the panel that
  // owns the player: {live, failed}. The rail's "Live" reads this, not the
  // run state -- Tok Aba (2026-10-09) watched both rows say "Live" over a
  // black box with a loader in it.
  const [feedState, setFeedState] = useState({ live: false, failed: false });
  const onFeedState = useCallback((s) => setFeedState(s), []);
  const faultSeen = useRef(new Set());
  const [retrying, setRetrying] = useState(false);
  const faultCams = camListOf(status);
  const selectedFault = camFaultOf(
    faultCams.find((c) => String(c.id) === String(selectedCam)) || null,
    steadyRunning === true, presence, status);
  const selectedFaultKey = selectedFault ? selectedFault.key : "";
  useEffect(() => {
    if (!selectedFaultKey || faultSeen.current.has(selectedFaultKey)) return;
    faultSeen.current.add(selectedFaultKey);
    setFaultPopup(selectedCam);
  }, [selectedFaultKey, selectedCam]);
  const popupCam = faultPopup == null ? null
    : faultCams.find((c) => String(c.id) === String(faultPopup)) || null;
  const popupFault = camFaultOf(popupCam, steadyRunning === true, presence, status);
  const popupKey = popupFault ? popupFault.key : "";
  useEffect(() => {
    setRetrying(false);
    if (faultPopup != null && !popupKey) setFaultPopup(null);
  }, [faultPopup, popupKey]);

  async function scanRtsp() {
    setScanning(true);
    try {
      const d = await api.get("/api/detector/rtsp_scan");
      setRtspHosts(d.hosts || []);
      if (!(d.hosts || []).length) {
        toast.error("No RTSP cameras found", "No host on this network answers on port 554.");
      }
    } catch (e) { toast.error("Scan failed", e.message); }
    finally { setScanning(false); }
  }

  const editable = status?.editable;
  // What this account may CHANGE here, straight from the server that enforces
  // it (core/services/caps.py). A control nobody may use is not drawn at all:
  // an admin who may re-point a camera but not add one should see one button,
  // not two with a 403 waiting behind the second.
  const can = (cap) => (status?.caps || []).includes(cap);

  async function power(action) {
    setBusy(true);
    setStopIntent(action === "stop");
    if (action === "stop") {
      // OPTIMISTIC: stopping is the one direction that cannot fail
      // half-way in a way the operator must watch -- the app is asked to
      // quit and it quits. Waiting for the round trip AND the next status
      // poll before the page admitted it left a live-looking feed on screen
      // for a second or two after the press, which reads as "it ignored me".
      // The picture goes now; a refused request puts it back below.
      stopAt.current = Date.now();
      setStatus((cur) => (cur ? { ...cur, running: false } : cur));
    } else {
      // Starting genuinely takes time (open the camera, load the model), so
      // it keeps its spinner -- claiming "running" before a frame exists
      // would just show a dead picture.
      setTransition(action);
    }
    try {
      const d = await api.post("/api/detector/control/power", { action });
      // A Start with no camera on the box: the switch is on, detection
      // waits for the camera, and the toast says that, not "started".
      if (d?.waiting) toast.info("Waiting for the camera", d.message);
      else toast.success(`Detection ${action === "start" ? "started" : "stopped"}`,
                         d?.message || undefined);
    } catch (e) {
      toast.error(`Could not ${action} detection`, e.message);
      // The optimism was wrong: put the page back where the system is.
      if (action === "stop") { setStopIntent(false); stopAt.current = 0; }
    } finally {
      setBusy(false);
      setTransition(null);
      loadStatus();
      // A refused Start names the camera that is not there; the rail
      // should say the same at once, not at its next 10 s look.
      if (action === "start") probePresence();
    }
  }

  // Ask the camera what RATES it can deliver at the size now chosen. Kept
  // separate from the resolution probe because the answer depends on that
  // choice: the same sensor does 60fps small and a fraction of it at full
  // frame, so asking before the size is settled would offer rates the camera
  // will refuse.
  async function probeFrameRates(size) {
    const source = pickedSource();
    if (!source) return toast.error("Choose a camera first");
    setProbingFps(true);
    try {
      const qs = new URLSearchParams({
        source, kind: camMode,
        // `size` = the size the resolution probe just landed on (the state
        // has not re-rendered yet); anything else = the current one.
        width: String((Array.isArray(size) ? size[0] : camera.width) || 0),
        height: String((Array.isArray(size) ? size[1] : camera.height) || 0),
      });
      const d = await api.get(`/api/detector/control/cameras/framerates?${qs}`);
      setFpsCaps(d);
      // Pick the best it offers -- that is what the button was pressed for.
      if (d.max) setCamera((c) => ({ ...c, fps: d.max }));
    } catch (e) {
      camErrorToast(toast, "Could not read the camera", e);
    } finally {
      setProbingFps(false);
    }
  }

  // Ask the camera itself what sizes it can deliver. Best-effort on every bus
  // (see detector_control.camera_resolutions): a probe that fails still
  // returns a generic list, flagged `probed: false` so the note below can say
  // the operator is choosing blind rather than pretending it asked.
  async function probeResolutions() {
    const source = pickedSource();
    if (!source) { toast.error("Choose a camera first"); return null; }
    setProbing(true);
    try {
      const qs = new URLSearchParams({ source, kind: camMode });
      const d = await api.get(`/api/detector/control/cameras/resolutions?${qs}`);
      setResCaps(d);
      if (d.max) {
        setCamera((c) => ({ ...c, width: d.max[0], height: d.max[1] }));
      }
      return d.max || [camera.width, camera.height];
    } catch (e) {
      camErrorToast(toast, "Could not read the camera", e);
      return null;
    } finally {
      setProbing(false);
    }
  }

  // The Quality panel's one button: sizes, then rates at that size, then show
  // both controls -- filled with the camera's answer, or the common-values
  // fallback (the notes say so) when it could not answer.
  async function askCamera() {
    if (!pickedSource()) return toast.error("Choose a camera first");
    setAskingBoth(true);
    try {
      await askBoth({ hiRes, probeRes: probeResolutions, probeRates: probeFrameRates });
      setAsked(true);
    } finally {
      setAskingBoth(false);
    }
  }

  // The source string the current picker selection resolves to, in the encoding
  // the backend understands (see detector_control.open_capture_spec).
  function pickedSource() {
    if (camMode === "rtsp") {
      const ip = rtsp.ip.trim();
      if (!ip) return "";
      if (ip.startsWith("rtsp://")) return ip;   // full URL pasted: use as-is
      if (!streamPath.ok) { toast.error("Stream path", streamPath.error); return ""; }
      let user = rtsp.user;
      let pass = rtsp.pass;
      // Blank boxes on a camera that ALREADY has credentials mean "keep
      // them" -- the fields are not prefilled any more, and dropping the
      // credentials because nobody retyped them would break the camera on
      // every unrelated edit (a resolution change, a stream swap).
      if (!user && !pass) {
        const cur = String(activeCam?.source || "");
        const m = cur.match(/^rtsp:\/\/(?:([^:@/]*)(?::([^@/]*))?@)?([^:/@]+)/);
        if (m && m[3] === ip && (m[1] || m[2])) {
          user = decodeURIComponent(m[1] || "");
          pass = decodeURIComponent(m[2] || "");
        }
      }
      const cred = user ? `${encodeURIComponent(user)}:${encodeURIComponent(pass)}@` : "";
      // A blank or out-of-range port falls back to 554 rather than saving
      // a source no camera answers on.
      const n = Number(String(rtsp.port || "").trim());
      const port = Number.isInteger(n) && n >= 1 && n <= 65535 ? n : 554;
      return `rtsp://${cred}${ip}:${port}/${streamPath.path}`;
    }
    // A depth camera is opened by its SDK, so the source names the CAMERA
    // ("orbbec", or "orbbec:<serial>" where a box has two) rather than a
    // /dev node. Picking one out of the USB list instead is the mistake this
    // mode exists to prevent: the node a slot lands on carries raw Bayer or
    // 16-bit depth, which OpenCV cannot turn into frames, so the camera opens
    // and then reports "gave no frames" as though it were broken.
    if (camMode === "depth") return depthPick || "orbbec";
    if (camMode === "gige") return gigeName ? `gige:${gigeName}` : "gige";
    // Relative on purpose (media.py): a system exported as a template still
    // points at its own footage on the next box.
    if (camMode === "file") return mediaPick ? `file:${mediaPick}` : "";
    return camera.source;              // usb: an index string like "0"
  }

  // The human name of the picked device, saved beside the source spec. The spec
  // cannot carry it - a USB camera is the string "0" - and re-deriving it later
  // means re-enumerating, which produces a name that moves when the USB tree
  // does. Captured at the moment of choosing, so it stays put.
  function pickedLabel() {
    if (camMode === "rtsp") {
      // The HOST of a pasted full address, never its user:password: the
      // label is saved and shown to every account (Pipi H2).
      const ip = rtsp.ip.trim();
      return ip ? `IP camera ${camHost(ip)}` : "";
    }
    if (camMode === "gige") {
      const hit = (cameras.gige || []).find((c) => String(c.name || "") === gigeName);
      return hit?.name || (gigeName ? `GigE ${gigeName}` : "GigE camera");
    }
    if (camMode === "file") return mediaPick || "";
    const hit = (cameras.usb || []).find((c) => String(c.index) === String(camera.source));
    return hit?.name || (camera.source ? `USB ${camera.source}` : "");
  }

  // Point the page at another camera: the feed, the rail highlight and the
  // picker all follow the selection, so the picker's fields are refilled from
  // that camera (force=true - this IS the operator asking for it).
  function selectCam(id) {
    if (id == null) return;
    selectedCamRef.current = id;
    setSelectedCam(id);
    addingCamRef.current = false;
    setAddingCam(false);
    setPreview(null); setPreviewMsg("");
    const cam = camListOf(status).find((c) => String(c.id) === String(id));
    if (cam) {
      setCamera({ source: cam.source ?? "", width: cam.width ?? 640,
                  height: cam.height ?? 480, fps: cam.fps ?? 10 });
      setHiRes((cam.width ?? 640) !== 640 || (cam.height ?? 480) !== 480);
      setResCaps(null); setFpsCaps(null); setAsked(false);
      applySource(cam.source, true);
    }
  }

  // FIRST RUN: a system created a minute ago arrives here with one empty
  // camera entry and nothing to look at, and every other thing this page does
  // is blocked until a camera is chosen. So the picker opens itself, once per
  // visit, rather than leaving somebody to find the button that is the only
  // useful control on the screen. Never for a viewer who could not act on it,
  // and never once anything real is configured.
  const autoPicked = useRef(false);
  useEffect(() => {
    if (autoPicked.current || !status || !editable) return;
    const list = camListOf(status);
    if (list.length !== 1) return;
    const src = String(list[0].source || "").trim();
    if (src && src !== "synthetic") return;
    autoPicked.current = true;
    beginAddCam();
  }, [status, editable]);

  function beginAddCam() {
    addingCamRef.current = true;
    setAddingCam(true);
    // Blank the device field: the add form must not inherit the selected
    // camera's source, or Apply quietly adds a second copy of it.
    setCamera((c) => ({ ...c, source: "" }));
    setPreview(null); setPreviewMsg("");
    setResCaps(null); setFpsCaps(null); setAsked(false);
    setCamOpen(true);
  }

  function cancelAddCam() {
    addingCamRef.current = false;
    setAddingCam(false);
    setPreview(null); setPreviewMsg("");
    loadStatus();          // put the picker back on the selected camera
  }

  // Apply: edits the SELECTED camera, or creates a new one when the picker was
  // opened by "+ Add camera".
  async function saveCamera() {
    const source = pickedSource();
    if (!source) return toast.error("Choose a camera first");
    setBusy(true);
    try {
      // A newly created system already carries ONE camera entry with no source
      // ("camera · Idle"). Adding a camera used to APPEND beside it, so a new
      // system's first camera left a permanent empty row above it that nothing
      // would ever fill, and the box's budget was spent on a placeholder.
      // Filling it in is what "add the first camera" means.
      if (addingCam && placeholderCam) {
        await api.post("/api/detector/control/camera",
                       { ...camera, source, label: pickedLabel(), kind: camMode,
                         camera_id: placeholderCam.id });
        addingCamRef.current = false;
        setAddingCam(false);
        selectedCamRef.current = placeholderCam.id;
        setSelectedCam(placeholderCam.id);
        setCamera((c) => ({ ...c, source }));
        setTimeout(() => setFeedNonce((n) => n + 1), 1200);
        toast.success("Camera set", "The app opens it within a few seconds.");
      } else if (addingCam) {
        const known = new Set(camListOf(status).map((c) => String(c.id)));
        const d = await api.post("/api/detector/control/cameras/add", {
          source, name: pickedLabel(), kind: camMode,
          width: camera.width, height: camera.height, fps: camera.fps,
          // Kept only on a GigE entry by the server; sent unconditionally
          // because the KIND is the server's to judge, not the form's.
          exposure_auto: camera.exposure_auto, exposure_us: camera.exposure_us,
          gain_auto: camera.gain_auto, gain_db: camera.gain_db,
          binning: camera.binning,
        });
        const list = d.cameras || [];
        const fresh = list.find((c) => !known.has(String(c.id))) || list[list.length - 1];
        setStatus((s) => (s ? { ...s, cameras: list } : s));
        if (d.budget) setCamBudgetRaw(d.budget);
        addingCamRef.current = false;
        setAddingCam(false);
        if (fresh) {
          selectedCamRef.current = fresh.id;
          setSelectedCam(fresh.id);
        }
        if (fresh && fresh.enabled === false) {
          toast.info("Camera added - switched off",
                     "The box's 6 running cameras are in use. Switch another "
                     + "off in the rail to run this one; its settings are kept.");
        } else {
          toast.success("Camera added", "The app opens it within a few seconds.");
        }
      } else {
        await api.post("/api/detector/control/camera",
                       { ...camera, source, label: pickedLabel(), kind: camMode,
                         ...(selectedCam == null ? {} : { camera_id: selectedCam }) });
        setCamera((c) => ({ ...c, source }));
        // The app needs a moment to close the old device and open the new
        // one; reconnecting instantly would just re-attach to the old
        // stream, so the nonce is bumped after it has had that moment.
        setTimeout(() => setFeedNonce((n) => n + 1), 1200);
        toast.success("Camera updated", "The app reopens it within a few seconds.");
      }
      setCamOpen(false);
      setTimeout(loadStatus, 800);
    } catch (e) { camErrorToast(toast, addingCam ? "Could not add camera" : "Could not set camera", e); }
    finally { setBusy(false); }
  }

  /** Switch one camera on/off. Off keeps its settings; on may be refused by
   *  the running cap, and the server's message says whose cameras are using
   *  the budget. */
  async function toggleCamEnabled(cam) {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/cameras/enable",
                               { camera_id: cam.id, enabled: cam.enabled === false });
      setStatus((s) => (s ? { ...s, cameras: d.cameras || [] } : s));
      if (d.budget) setCamBudgetRaw(d.budget);
      // NO feed nonce here. Switching a camera on or off already remounts the
      // viewer by itself -- the panel gates the feed on this camera's
      // `enabled`, so the flip unmounts and remounts it with a fresh
      // connection. Bumping the nonce on top opened a SECOND and THIRD one
      // per click, and an MJPEG stream is a long-lived HTTP connection:
      // measured at 48 preview opens for 15 toggles. A browser allows six per
      // origin, so toggling quickly emptied the pool and the next request --
      // the very next toggle's POST, and the feed itself -- was never sent.
      // The controls went dead and the picture never came, from one cause.
      //
      // Reconnecting too early is handled where it belongs now: StreamImg
      // reopens a stream that delivers no first frame, and WebRTCVideo drops
      // to the img when a session connects and carries no picture.
      setTimeout(loadStatus, 1500);
      toast.success(cam.enabled === false
        ? `${camName(cam)} switched on`
        : `${camName(cam)} switched off`, "The app applies it within a few seconds.");
    } catch (e) {
      camErrorToast(toast, "Could not switch the camera", e);
      // The POST may have died anywhere -- refused, aborted mid-flight, or
      // applied but unanswered -- so what is on screen is a guess now.
      // Re-read rather than trust it: the toggle snaps to whatever the
      // server actually holds.
      loadStatus();
    }
    finally { setBusy(false); }
  }

  /** Removing a camera deletes the zones drawn on it (a zone is a shape over
   *  ONE camera's picture). The server is asked first, as a dry run, which
   *  zones and canvas nodes that is -- its rule decides, including the
   *  zones with no camera of their own that belong to the first camera -- so
   *  the confirm names them instead of guessing from the page's own list. */
  async function removeCam(cam) {
    const last = camListOf(status).length <= 1;
    let gone = [], nodes = [];
    try {
      const p = await api.post("/api/detector/control/cameras/remove",
                               { camera_id: cam.id, dry_run: true });
      gone = p.zones || [];
      nodes = p.nodes || [];
    } catch (e) { camErrorToast(toast, "Could not remove camera", e); return; }
    const SHOWN = 6;
    const names = gone.slice(0, SHOWN).map((z) => z.name || z.id).join(", ");
    const more = gone.length > SHOWN ? ` and ${gone.length - SHOWN} more` : "";
    const zoneText = gone.length
      ? `This also deletes ${gone.length} zone${gone.length === 1 ? "" : "s"} `
        + `drawn on it: ${names}${more}.`
        + (nodes.length ? " Workflow nodes that used them will ask for a new zone." : "")
      : "No zones are drawn on it.";
    if (!await confirm({
      title: `Remove "${camName(cam)}"?`,
      description: zoneText + (last
        ? " This is the only camera - the system becomes a blank canvas and "
          + "cannot start until one is added."
        : ""),
      destructive: true, confirmLabel: "Remove", cancelLabel: "Cancel" })) return;
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/cameras/remove", { camera_id: cam.id });
      const list = d.cameras || [];
      setStatus((s) => (s ? { ...s, cameras: list } : s));
      if (d.budget) setCamBudgetRaw(d.budget);
      // The zones go off the picture at once; loadStatus below confirms.
      const removed = new Set((d.zones_removed || []).map((z) => String(z.id)));
      if (removed.size) setZones((zs) => zs.filter((z) => !removed.has(String(z.id))));
      const first = list.length ? list[0].id : null;
      selectedCamRef.current = first;
      setSelectedCam(first);
      toast.success(removed.size
        ? `Camera and ${removed.size} zone${removed.size === 1 ? "" : "s"} removed`
        : "Camera removed");
      setTimeout(loadStatus, 800);
    } catch (e) { camErrorToast(toast, "Could not remove camera", e); }
    finally { setBusy(false); }
  }

  // Snapshot the candidate source so the operator sees it before committing.
  // If that source is the one the running app already holds, a fresh grab would
  // fail (one reader per camera) - show the live feed instead.
  async function snapPreview() {
    const source = pickedSource();
    if (!source) return toast.error("Choose a camera first");
    setPreviewMsg("");
    if (preview) { URL.revokeObjectURL(preview); setPreview(null); }
    // Any RUNNING camera holds its device open, not just the selected one.
    if (status?.running && camListOf(status).some((c) => c.source === source)) {
      setPreviewMsg("This is the running camera - see the live feed above.");
      return;
    }
    setBusy(true);
    try {
      // The source travels in a POST body, never a query string: a URL with a
      // camera password ended up in access logs (a64efa56). camera_id lets
      // the server put back a password this page was only shown masked.
      const body = { source, width: camera.width, height: camera.height, fps: camera.fps,
                     ...(selectedCam == null ? {} : { camera_id: String(selectedCam) }) };
      // The preview is opened to answer ONE question -- does the picture look
      // right -- so it is taken with the exposure and gain being chosen, not
      // with the camera's own defaults.
      if (camMode === "gige") {
        body.exposure_auto = camera.exposure_auto || "on";
        body.gain_auto = camera.gain_auto || "on";
        if (camera.exposure_auto === "off") body.exposure_us = camera.exposure_us || 10000;
        if (camera.gain_auto === "off") body.gain_db = camera.gain_db || 0;
        if ((camera.binning || 1) > 1) body.binning = camera.binning;
      }
      const res = await fetch(apiUrl("/api/detector/snapshot"), {
        method: "POST", credentials: "same-origin", body: JSON.stringify(body),
        headers: { "Content-Type": "application/json", ...authHeaders() } });
      // responseError reads the envelope's `error` first -- `detail` alone
      // turned every refusal into a bare "HTTP 409".
      if (!res.ok) throw await responseError(res);
      setPreview(URL.createObjectURL(await res.blob()));
    } catch (e) {
      // The why and the fix, not the backend's sentence -- the raw text
      // stays one click away under "Details" (camErrors.js).
      setPreviewMsg(explainCamError(e, { retry: "Preview Snapshot" }));
    }
    finally { setBusy(false); }
  }

  if (error) return <ErrorNote error={error} />;
  // steadyRunning === null means the answer is not proven yet -- hold the
  // skeleton rather than flashing a "stopped" page over a running system.
  // The EDITOR shape, the same one the route shows while this chunk loads
  // (router.jsx CAMERA_SKEL): one placeholder from click to page.
  if (!status || steadyRunning === null) {
    return <SkelEditor title="Camera Setup" />;
  }

  const running = steadyRunning;
  const camList = camListOf(status);
  const budget = camBudget(camBudgetRaw, camList);
  // Whether the bus the picker is currently on has a running slot left. Falls
  // back to counting this system's own cameras until the server's per-kind
  // budget arrives, so the note is never wrongly shown on a fresh load.
  const busFull = (() => {
    const cap = KIND_LIMITS[camMode];
    if (!cap) return false;
    const bus = budget.by_kind?.[camMode];
    const used = bus ? bus.used
      : camList.filter((c) => c.enabled !== false && c.kind === camMode).length;
    return used >= cap;
  })();
  // No REAL camera: none configured, or every entry still unset. AI vision
  // needs a camera, so this is the state that keeps Start switched off --
  // the server refuses to start such a system too, and saying so here beats
  // letting the operator press a button that can only fail. "synthetic" is
  // still tested for because a system scaffolded before it was withdrawn
  // carries the word in its config.
  // The entry a freshly created system starts life with: present, named, and
  // pointed at nothing. "+ Add camera" fills THIS one rather than appending
  // beside it - see saveCamera.
  const placeholderCam = camList.find(
    (c) => !String(c.source || "").trim() || c.source === "synthetic"
           || c.kind === "unset" || c.kind === "synthetic") || null;
  const needsRealCamera = !camList.length
    || camList.every((c) => !String(c.source || "").trim()
                            || c.source === "synthetic"
                            || c.kind === "unset" || c.kind === "synthetic");
  const activeCam = camList.find((c) => String(c.id) === String(selectedCam)) || camList[0] || null;
  // camName, not the raw fields: a re-pointed camera must not keep announcing
  // the hardware it used to be, here either.
  const camLabel = activeCam ? camName(activeCam) : "no camera";
  // Zones are one list for the whole box; a zone with no `camera` belongs to
  // the first one (the server's default), same rule as the Zones page.
  const firstCamId = camList.length ? camList[0].id : null;
  const shownCamId = activeCam ? activeCam.id : firstCamId;
  const camZones = camList.length > 1
    ? zones.filter((z) => String(z.camera ?? firstCamId) === String(shownCamId))
    : zones;
  // What the Quality panel shows: nothing of size/rate until asked.
  const qv = qualityView({ asked, hiRes, width: camera.width, height: camera.height });

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title">Camera Setup</div>
        <div className="page-actions">
          {/* The zone panel portals Undo / Discard / Save all in here, so
              the things you do before leaving sit beside the button that
              leaves. See ZONE_ACTIONS_SLOT in Zones.jsx. */}
          <div id={ZONE_ACTIONS_SLOT}
               style={{ display: "inline-flex", alignItems: "center", gap: 8 }} />
          <TutorialNav step="detection" />
          <NextPageButton />
        </div>
      </div>

      <CamFaultDialog cam={popupCam} fault={popupFault}
                      index={popupCam ? camList.indexOf(popupCam) + 1 : 0}
                      retrying={retrying || (popupFault?.kind === "absent" && looking)}
                      onClose={() => setFaultPopup(null)}
                      onRetry={async () => {
                        if (!popupCam) return;
                        setRetrying(true);
                        if (popupFault?.kind === "absent" && !running) {
                          // Stopped: a fresh look is all there is to do.
                          probePresence();
                          setTimeout(() => setRetrying(false), 4000);
                          return;
                        }
                        try {
                          // Re-applying the SAME camera touches the config,
                          // which makes the app close and reopen the device
                          // now instead of on its own timer. The entry's
                          // own fields, not the picker's: the popup can be
                          // about a camera other than the one being edited.
                          await api.post("/api/detector/control/camera",
                                         { source: popupCam.source, camera_id: popupCam.id,
                                           width: popupCam.width ?? 640,
                                           height: popupCam.height ?? 480,
                                           fps: popupCam.fps ?? 10,
                                           exposure_auto: popupCam.exposure_auto ?? "on",
                                           exposure_us: popupCam.exposure_us ?? 10000,
                                           gain_auto: popupCam.gain_auto ?? "on",
                                           gain_db: popupCam.gain_db ?? 0,
                                           binning: popupCam.binning ?? 1 });
                          setTimeout(() => setFeedNonce((n) => n + 1), 1500);
                          setTimeout(loadStatus, 1000);
                          // The spinner runs until the fault clears (the
                          // popup closes by itself) or changes; a camera
                          // that never answers gets its button back.
                          setTimeout(() => setRetrying(false), 15000);
                        } catch (e) {
                          setRetrying(false);
                          camErrorToast(toast, "Could not reconnect", e);
                        }
                      }} />

      {/* A POPUP, not an inline panel. Camera setup is a detour from watching
          the feed: opening it used to push the whole page -- feed included --
          down the screen, so the thing being configured scrolled out of view
          exactly when it mattered. */}
      <Dialog open={camOpen} title={addingCam ? "Add a camera" : "Camera settings"}
              onClose={() => { if (addingCam) cancelAddCam(); setCamOpen(false); setAsked(false); }}>
        <div>
          {addingCam ? (
            <p className="muted small">
              Pick the device for the NEW camera - {budget.used_here + budget.used_elsewhere}{" "}
              of {budget.max} cameras in use on this box
              {budget.used_elsewhere
                ? ` (${budget.used_elsewhere} on other systems)` : ""}.
            </p>
          ) : (
            // WHAT is being edited and WHETHER it is working, on one line:
            // the source string alone said nothing about whether the camera
            // was actually answering.
            <div className="cam-head">
              <span>
                Editing <strong>{camName(activeCam)}</strong>
              </span>
              {/* Only when it adds something: an IP camera with no chosen
                  name is already CALLED its stream address, and printing it
                  twice on one line is noise. */}
              {camName(activeCam) !== camSourceSafe(activeCam?.source) && (
                <code className="muted" style={{ fontSize: 11.5 }}>
                  {camSourceSafe(activeCam?.source) || "-"}
                </code>
              )}
              <span className="cam-head-state"
                    title={activeCam?.error ? camErrorText(activeCam.error) : undefined}
                    style={{ color: activeCam?.error ? "var(--alert, #dc2626)"
                             : running ? "var(--ok, #16a34a)" : "var(--muted, #94a3b8)" }}>
                <span style={{ width: 7, height: 7, borderRadius: 999,
                               background: "currentColor" }} />
                {activeCam?.error ? "Fault" : running ? "Online" : "Idle"}
              </span>
            </div>
          )}

          {/* One segmented control, with the bus each option means: four
              loose buttons in a row did not say they were alternatives. */}
          <div className="cam-seg">
            {/* Depth (3D) only where the box offers it (a developer box:
                detector_control.depth_offered). A server that predates the
                flag says nothing, and that means offered, as it always was.
                A depth camera being EDITED keeps its tab, so it can still be
                re-pointed or read. */}
            {CAM_MODES.filter(([m]) => m !== "depth" || budget.depth_offered !== false
                                       || camMode === "depth").map(([m, label]) => (
              <button key={m} type="button"
                      className={camMode === m ? "is-on" : ""}
                      onClick={() => { setCamMode(m); setPreview(null); setPreviewMsg(""); }}>
                {{ usb: <Usb size={14} />, rtsp: <Wifi size={14} />,
                   gige: <Network size={14} />,
                   file: <FileVideo size={14} /> }[m]}
                {label}
              </button>
            ))}
          </div>

          {camMode === "depth" && (
            <>
              <label className="muted small">Depth cameras (opened by their own SDK)</label>
              <div className="filter-bar">
                <select className="input" value={depthPick}
                        onChange={(e) => { setDepthPick(e.target.value); setPreview(null); }}>
                  {cameras.depth.length === 0 && <option value="">- none found -</option>}
                  {cameras.depth.map((c) => (
                    <option key={c.source} value={c.source}>
                      {c.name}{c.serial ? ` (${c.serial})` : ""}
                    </option>
                  ))}
                </select>
                <button className="btn btn-sm" onClick={loadCameras}
                        title="Rescan for depth cameras">
                  <RefreshCw size={14} />
                </button>
              </div>
              {/* The reason this mode exists, said where the mistake is made.
                  An Orbbec DOES appear in the USB list -- it presents several
                  V4L2 nodes -- and picking it there gives a node carrying raw
                  Bayer or 16-bit depth, which OpenCV cannot decode. The camera
                  then opens and reports "gave no frames", which reads as
                  broken hardware. */}
              <p className="muted small">
                {cameras.depth.length
                  ? "Measures distance as well as seeing. Opened through the "
                    + "camera's own SDK, which also aligns the depth picture to "
                    + "the colour one, so every detection can carry how far "
                    + "away it is."
                  : "No depth camera found on this box. If one is plugged in, "
                    + "check it is on a USB 3 socket (the blue one) and rescan."}
              </p>
              {cameras.depth.length > 0 && (
                <p className="muted small">
                  Do not pick this camera from the USB list - the nodes it
                  shows there carry raw or depth formats that cannot be
                  decoded, and it will report that it gave no frames.
                </p>
              )}
            </>
          )}

          {camMode === "usb" && (
            <>
              <label className="muted small">USB / UVC cameras (V4L2)</label>
              {/* ONE row shape for every mode: the picker, then an icon
                  rescan, then an icon Ports toggle. Same order, same icons,
                  whichever bus the camera arrives on. */}
              <div className="filter-bar">
                <select className="input" value={camera.source}
                        onChange={(e) => { setCamera({ ...camera, source: e.target.value }); setPreview(null); }}>
                  <option value="">- select a camera -</option>
                  {cameras.usb.map((c) => (
                    <option key={c.path} value={String(c.index)}>
                      {c.name} ({c.slot != null ? `USB port ${c.slot}` : c.path})
                    </option>
                  ))}
                </select>
                <button className="btn btn-sm" onClick={loadCameras} title="Rescan USB cameras">
                  <RefreshCw size={14} />
                </button>
                <button className={"btn btn-sm" + (portsOpen ? " btn-accent" : "")}
                        title="Show the board's USB sockets - which one each camera is in"
                        onClick={() => setPortsOpen((v) => !v)}>
                  <Cable size={14} />
                </button>
              </div>
              {portsOpen && (
                <div style={{ marginTop: 8 }}>
                  <UsbPorts usb={boardPorts.data?.usb} labels />
                </div>
              )}
              {!cameras.usb.length && <p className="muted small">No USB camera found. Plug one in and rescan.</p>}
            </>
          )}

          {camMode === "file" && (
            <>
              <label className="muted small">
                Recorded video or image - detection runs on it exactly as it
                runs on a camera
              </label>
              <div className="filter-bar">
                <select className="input" style={{ flex: 1, minWidth: 0 }}
                        value={mediaPick}
                        onChange={(e) => { setMediaPick(e.target.value); setPreview(null); }}>
                  <option value="">- select a file -</option>
                  {media.map((m) => (
                    <option key={m.name} value={m.name}>
                      {m.name} ({m.kind}, {(m.bytes / (1024 * 1024)).toFixed(1)} MB)
                    </option>
                  ))}
                </select>
                <button className="btn btn-sm" onClick={loadMedia} title="Refresh the file list">
                  <RefreshCw size={14} />
                </button>
              </div>
              <div className="filter-bar" style={{ marginTop: 6 }}>
                <label className="btn btn-sm" style={{ cursor: mediaBusy ? "wait" : "pointer" }}>
                  {mediaBusy ? "Uploading…" : "Upload…"}
                  <input type="file" hidden disabled={mediaBusy}
                         accept="video/*,image/*"
                         onChange={async (e) => {
                           const f = e.target.files?.[0];
                           e.target.value = "";        // same file twice still fires
                           if (!f) return;
                           setMediaBusy(true); setMediaMsg("");
                           try {
                             const body = new FormData();
                             body.append("file", f);
                             const d = await api.post("/api/media", body);
                             setMediaPick(d.file.name);
                             setMediaMsg(`${d.file.name} uploaded`);
                             loadMedia();
                           } catch (err) {
                             setMediaMsg(err.message || "upload failed");
                           } finally {
                             setMediaBusy(false);
                           }
                         }} />
                </label>
                {mediaPick && (
                  <button className="btn btn-sm" disabled={mediaBusy}
                          onClick={async () => {
                            if (!await confirm({
                              title: `Delete ${mediaPick}?`,
                              description: "A camera pointed at this file stops "
                                + "having anything to play.",
                              destructive: true })) return;
                            setMediaBusy(true);
                            try {
                              await api.del(`/api/media/${encodeURIComponent(mediaPick)}`);
                              setMediaPick(""); setMediaMsg("deleted"); loadMedia();
                            } catch (err) {
                              setMediaMsg(err.message || "could not delete");
                            } finally { setMediaBusy(false); }
                          }}>Delete</button>
                )}
              </div>
              {mediaMsg && <p className="muted small">{mediaMsg}</p>}
              {!media.length && (
                <p className="muted small">
                  Nothing uploaded yet. A video loops when it ends and plays at
                  its own frame rate; an image is held as a still frame.
                </p>
              )}
            </>
          )}

          {camMode === "rtsp" && (
            <>
              {/* WHERE the camera is. */}
              <div className="cam-group">
                <div className="cam-group-title">
                  <Link2 size={15} style={{ color: "var(--accent-text, #04bade)" }} />
                  Connection
                  <span className="cam-group-note">IP camera (RTSP, port 554)</span>
                </div>
              <div className="filter-bar">
                <select className="input" style={{ flex: 1, minWidth: 0, width: "auto" }}
                        value={rtsp.ip}
                        onChange={(e) => { setRtsp({ ...rtsp, ip: e.target.value }); setPreview(null); }}>
                  <option value="">- {rtspHosts === null ? "scan or type an IP below" : `${rtspHosts.length} camera(s) found`} -</option>
                  {(rtspHosts || []).map((ip) => <option key={ip} value={ip}>{ip}</option>)}
                  {rtsp.ip && !(rtspHosts || []).includes(rtsp.ip) && !rtsp.ip.startsWith("rtsp://") && (
                    <option value={rtsp.ip}>{rtsp.ip} (saved)</option>
                  )}
                </select>
                <button className="btn btn-sm" disabled={scanning} onClick={scanRtsp}
                        title={scanning ? "Scanning the network…" : "Scan the network for RTSP cameras"}>
                  <RefreshCw size={14} className={scanning ? "animate-spin" : undefined} />
                </button>
                <button className={"btn btn-sm" + (portsOpen ? " btn-accent" : "")}
                        title="Show the board's ethernet jacks - link and address"
                        onClick={() => setPortsOpen((v) => !v)}>
                  <Cable size={14} />
                </button>
              </div>
              {portsOpen && (
                <div style={{ marginTop: 8 }}>
                  <EthPorts eth={boardPorts.data?.eth} />
                </div>
              )}
              <label className="muted small" style={{ display: "block", marginTop: 6 }}
                     htmlFor="cam-rtsp-address">
                Camera IP Address or Stream URL
              </label>
              <input id="cam-rtsp-address" className="input" style={{ marginTop: 4 }}
                     value={rtsp.ip}
                     placeholder="e.g. 192.168.1.67"
                     onChange={(e) => { setRtsp({ ...rtsp, ip: e.target.value }); setPreview(null); }} />
              <p className="muted small" style={{ margin: "6px 0 0" }}>
                Enter the camera's IP address or RTSP stream URL. A full URL
                such as <code>rtsp://192.168.1.68:554/stream1</code> is used as-is.
              </p>
              </div>
              {/* How to get IN. Optional: plenty of cameras need none. */}
              <div className="cam-group">
                <div className="cam-group-title">
                  <Lock size={15} style={{ color: "var(--accent-text, #04bade)" }} />
                  Login Credentials
                  <span className="cam-group-note">(Optional)</span>
                </div>
              {/* One field per row: four controls sharing one line clipped
                  their own placeholders down to "usern…" and "pa…", so the
                  form could not be read until it was already filled in.
                  The password keeps its reveal - a typo in a credential
                  surfaces as "camera unreachable" ten seconds later, which
                  is a terrible way to find a typo. */}
              <div className="cam-rows">
                <label className="cam-row">
                  <span className="cam-row-label">Username</span>
                  <span className="cam-row-field">
                    <input className="input" value={rtsp.user}
                           placeholder="username" autoComplete="off"
                           onChange={(e) => setRtsp({ ...rtsp, user: e.target.value })} />
                  </span>
                </label>
                <label className="cam-row">
                  <span className="cam-row-label">Password</span>
                  <span className="cam-row-field">
                    <span style={{ display: "block", flex: 1, minWidth: 0 }}>
                      <PasswordInput native className="input"
                             shown={showPass} onToggle={() => setShowPass((v) => !v)}
                             value={rtsp.pass} placeholder="password" autoComplete="new-password"
                             onChange={(e) => setRtsp({ ...rtsp, pass: e.target.value })} />
                    </span>
                  </span>
                </label>
                <label className="cam-row">
                  <span className="cam-row-label">Port</span>
                  <span className="cam-row-field">
                    <input className="input" inputMode="numeric" value={rtsp.port}
                           placeholder="554" autoComplete="off"
                           onChange={(e) => setRtsp({ ...rtsp, port: e.target.value.replace(/\D/g, "").slice(0, 5) })} />
                  </span>
                </label>
                <label className="cam-row">
                  <span className="cam-row-label">Video Stream</span>
                  <span className="cam-row-field">
                    <select className="input" value={selectValueFor(rtsp.stream)}
                            onChange={(e) => {
                              if (e.target.value !== RTSP_CUSTOM) setRtsp({ ...rtsp, stream: e.target.value });
                            }}>
                      {RTSP_PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                      <option value={RTSP_CUSTOM}>Custom (type below)</option>
                    </select>
                    <button className="btn btn-sm" title="Clear the saved camera preference"
                            style={{ flex: "none" }}
                            onClick={() => { setRtsp({ ip: "", user: "", pass: "", port: "554", stream: "stream1" }); setPreview(null); }}>
                      Reset
                    </button>
                  </span>
                </label>
                <label className="cam-row">
                  <span className="cam-row-label">Stream path (or type your own)</span>
                  <span className="cam-row-field">
                    <input className="input" value={rtsp.stream} autoComplete="off"
                           placeholder="e.g. Streaming/Channels/102"
                           onChange={(e) => setRtsp({ ...rtsp, stream: e.target.value })} />
                  </span>
                </label>
                {!streamPath.ok && (
                  <p className="small" style={{ margin: "2px 0 0", color: "var(--danger, #c0392b)" }}>{streamPath.error}</p>
                )}
                <p className="muted small" style={{ margin: "2px 0 0" }}>
                  Choose the stream used for detection. A lower-resolution
                  sub-stream usually uses less processing power. If your camera
                  is not listed, choose Custom and type its stream path.
                </p>
              </div>
              </div>
              {/* One line, and only the rule that is not visible from the
                  form itself: blank boxes are not "no credentials", they are
                  "the saved ones". The stream trade-off now reads off the
                  options, and the rtsp:// paste off the Connection note. */}
              <p className="muted small" style={{ margin: "2px 0 0" }}>
                Leave blank to keep the saved credentials for this camera address.
              </p>
            </>
          )}

          {camMode === "gige" && (
            <>
              <label className="muted small">GigE Vision camera (Aravis + GStreamer)</label>
              {/* Same row as USB: picker, icon rescan, icon Ports. The
                  rescan used to be a text button on its own line below the
                  picker, which no other mode did. */}
              <div className="filter-bar">
                <select className="input" value={gigeName}
                        onChange={(e) => { setGigeName(e.target.value); setPreview(null); }}>
                  <option value="">First GigE camera found</option>
                  {cameras.gige.map((c) => (
                    <option key={c.name} value={c.name}>
                      {(c.detail || c.name)
                        + (c.opens === false ? " - found, cannot open" : "")}
                    </option>
                  ))}
                </select>
                <button className="btn btn-sm" onClick={loadCameras}
                        title="Rescan the network for GigE cameras">
                  <RefreshCw size={14} />
                </button>
                <button className={"btn btn-sm" + (portsOpen ? " btn-accent" : "")}
                        title="Show the board's ethernet jacks - link and address"
                        onClick={() => setPortsOpen((v) => !v)}>
                  <Cable size={14} />
                </button>
              </div>
              {portsOpen && (
                <div style={{ marginTop: 8 }}>
                  <EthPorts eth={boardPorts.data?.eth} />
                </div>
              )}
              {/* A camera the NETWORK answered for but Aravis cannot open is
                  the one case worth spelling out: it is plugged in, powered
                  and addressable, so "no camera found" would be a lie and the
                  fix is a specific one -- an address on this box, or the
                  plugin. The note comes from the scan (detector_control
                  list_gige_cameras), which knows which of the two it is. */}
              {(cameras.gige || []).filter((c) => c.opens === false && c.note)
                .map((c) => (
                  <p key={c.name} className="muted small"
                     style={{ color: "var(--warn, #f59e0b)" }}>
                    <strong>{c.vendor || c.name} {c.model || ""}</strong> - {c.note}
                  </p>
                ))}
              {/* Exposure and gain: the two settings an industrial camera is
                  useless without. Left alone, Aravis runs whatever the camera
                  was last set to by whatever software touched it last - the
                  same camera black on one board and blown out on the next. */}
              <div className="two-col" style={{ marginTop: 10 }}>
                <div>
                  <label className="muted small">Exposure</label>
                  <select className="input" value={camera.exposure_auto || "on"}
                          onChange={(e) => setCamera({ ...camera, exposure_auto: e.target.value })}>
                    <option value="on">Auto - follow the light</option>
                    <option value="once">Auto once - measure now, then hold</option>
                    <option value="off">Manual</option>
                  </select>
                  {camera.exposure_auto === "off" && (
                    <input className="input" type="number" min="1" max="10000000"
                           style={{ marginTop: 6 }}
                           value={camera.exposure_us ?? 10000}
                           onChange={(e) => setCamera({ ...camera, exposure_us: Number(e.target.value) })}
                           placeholder="microseconds" />
                  )}
                </div>
                <div>
                  <label className="muted small">Gain</label>
                  <select className="input" value={camera.gain_auto || "on"}
                          onChange={(e) => setCamera({ ...camera, gain_auto: e.target.value })}>
                    <option value="on">Auto - follow the light</option>
                    <option value="once">Auto once - measure now, then hold</option>
                    <option value="off">Manual</option>
                  </select>
                  {camera.gain_auto === "off" && (
                    <input className="input" type="number" min="0" max="48" step="0.5"
                           style={{ marginTop: 6 }}
                           value={camera.gain_db ?? 0}
                           onChange={(e) => setCamera({ ...camera, gain_db: Number(e.target.value) })}
                           placeholder="dB" />
                  )}
                </div>
              </div>
              {/* The one setting that buys frame rate when the link cannot
                  carry the full picture. Asking for a smaller SIZE does not:
                  on a GigE camera that sets the sensor's region of interest,
                  so it crops the view instead of shrinking the picture. */}
              <div style={{ marginTop: 10 }}>
                <label className="muted small">Sensor binning</label>
                <select className="input" value={camera.binning || 1}
                        onChange={(e) => setCamera({ ...camera, binning: Number(e.target.value) })}>
                  <option value={1}>Off - full detail</option>
                  <option value={2}>2×2 - quarter the data, 4× the rate</option>
                  <option value={4}>4×4 - a sixteenth of the data</option>
                </select>
                {(camera.binning || 1) > 1 && (
                  <p className="muted small" style={{ marginTop: 4 }}>
                    The whole view is kept and the picture is brighter; fine
                    detail is lost, and the size above no longer applies -
                    the sensor sends {camera.binning}× smaller and the app
                    scales from there.
                  </p>
                )}
              </div>
              <p className="muted small" style={{ marginTop: 6 }}>
                {camera.exposure_auto === "off" || camera.gain_auto === "off"
                  ? "Manual holds the picture steady frame to frame - right for "
                    + "a lit inspection cell, wrong anywhere the light changes. "
                    + "Long exposures blur anything moving; gain brightens the "
                    + "noise along with the picture."
                  : "Auto follows the light. “Auto once” measures the scene now "
                    + "and holds it - the setting for a fixed rig, where a "
                    + "passing forklift must not re-expose the shot."}
              </p>
              <p className="muted small">
                Industrial GigE cameras over ethernet. The picker asks the
                network itself (GigE Vision discovery), so a camera shows up
                here whenever it is plugged in and powered - opening it needs
                the Aravis GStreamer plugin as well.
              </p>
            </>
          )}

          {/* This bus is full: say so where the operator is choosing, not
              after they press Add. Adding still works - the camera lands
              switched off, settings kept - so this informs rather than blocks. */}
          {busFull && (
            <p className="muted small" style={{ marginTop: 6 }}>
              All {KIND_LIMITS[camMode]} {camMode === "rtsp" ? "IP" : camMode.toUpperCase()} camera
              slots are in use. A new one is added switched off - turn another off in the rail to run it.
            </p>
          )}

          {/* Resolution is automatic by default -- frames are capped at the
              AI's working size and the rate balances itself across the NPU,
              which is what most installs want. The switch is for the installs
              that do not: a 2K/4K camera whose detail is the whole point. */}
          <div className="cam-group">
            <div className="cam-group-title">
              <Gauge size={15} style={{ color: "var(--accent-text, #04bade)" }} />
              Quality
              <span className="cam-group-note">
                {qv.headNote}
              </span>
            </div>
          <div className="filter-bar" style={{ alignItems: "center" }}>
            <Switch checked={hiRes} disabled={!editable} aria-label="High Resolution"
                    onCheckedChange={(on) => {
                      setHiRes(on);
                      if (!on) setCamera((c) => ({ ...c, width: 640, height: 480 }));
                    }} />
            <span className="muted small">High Resolution</span>
          </div>
          <p className="muted small" style={{ margin: "4px 0 0" }} data-hires-help>
            Use higher resolution for more image detail. This may increase
            processing load.
          </p>
          {!hiRes && (
            <p className="muted small" style={{ marginBottom: 0 }}>
              Running at 640×480 - the AI's working size. Enough for most
              detection, and the cheapest thing this box can do.
            </p>
          )}
          {/* ONE question for both answers: sizes, then rates at that size.
              The size and rate controls stay out of sight until it has been
              asked, so nothing here reads as the camera's own before it is. */}
          <div className="filter-bar" style={{ marginTop: 6, alignItems: "center" }}>
            <button className="btn btn-sm" disabled={!editable || askingBoth}
                    onClick={askCamera}>
              {askingBoth ? "Checking…" : qv.buttonLabel}
            </button>
            {!asked && (
              <span className="muted small">
                Check the resolutions and frame rates supported by this camera.
              </span>
            )}
          </div>
          {qv.showResolution && (
            <>
              <div className="filter-bar" style={{ marginTop: 6 }}>
                <select className="input" style={{ flex: 1, minWidth: 0, width: "auto" }}
                        value={`${camera.width}x${camera.height}`}
                        onChange={(e) => {
                          const [w, h] = e.target.value.split("x").map(Number);
                          setCamera({ ...camera, width: w, height: h });
                          setPreview(null);
                        }}>
                  {(resCaps?.candidates
                    || [[640, 480], [1280, 720], [1920, 1080], [2560, 1440], [3840, 2160]])
                    .map(([w, h]) => (
                      <option key={`${w}x${h}`} value={`${w}x${h}`}>
                        {w}×{h}{resCaps?.max && w === resCaps.max[0] && h === resCaps.max[1]
                          ? " (camera max)" : ""}
                      </option>
                    ))}
                  {!(resCaps?.candidates || []).some(
                    ([w, h]) => w === camera.width && h === camera.height) && (
                    <option value={`${camera.width}x${camera.height}`}>
                      {camera.width}×{camera.height} (saved)
                    </option>
                  )}
                </select>
              </div>
              {resCaps?.note && (
                <p className="muted small" style={{ marginTop: 4 }}>{resCaps.note}</p>
              )}
              {!resCaps && (
                <p className="muted small" style={{ marginTop: 4 }}>
                  These are common sizes, not this camera's own - the camera did
                  not answer. Press "Check Again" to retry.
                </p>
              )}
            </>
          )}
          {qv.showControls && (
            <>
          {qv.showResolution && (
              <p className="muted small" style={{ marginTop: 4, marginBottom: 0 }}>
                More pixels cost this box more CPU per frame, and detection still
                runs at the AI's working size - the gain is in the picture, the
                recordings and the measurements, not in the model.
              </p>
          )}
          {/* The RATE was configured but never offered: the app paces
              detection at exactly this number, so a camera capable of 30
              sat at the stored 10 with no way to say otherwise. */}
          <div className="filter-bar" style={{ marginTop: 8, alignItems: "center" }}>
            <span className="muted small" style={{ flex: "none" }}>Frame rate</span>
            <select className="input" style={{ width: 150 }}
                    value={camera.fps ?? 10} disabled={!editable}
                    onChange={(e) => setCamera({ ...camera, fps: +e.target.value })}>
              {(fpsCaps?.candidates?.length
                  ? fpsCaps.candidates : [5, 10, 15, 20, 25, 30]).map((f) => (
                <option key={f} value={f}>
                  {f} fps{fpsCaps?.max === f ? " (camera max)" : ""}
                </option>
              ))}
            </select>
          </div>
          {!fpsCaps && (
            <p className="muted small" style={{ margin: "4px 0 0" }}>
              These are common rates, not this camera's own - the camera did
              not answer. Press "Check Again" to retry.
            </p>
          )}
          {fpsCaps?.note && (
            <p className="muted small" style={{ margin: "4px 0 0" }}>
              {fpsCaps.probed ? "" : "Not probed - "}{fpsCaps.note}
            </p>
          )}
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Detection runs at this rate. Every running system shares the one
            AI chip - one or two systems hold 25–30 fps easily; four heavy
            models at once and the chip becomes the ceiling, whatever is
            asked here.
          </p>
            </>
          )}
          {/* [video] overlay / view_on_detect / view_hold_s, saved as
              flipped (on_demand is no longer drawn, 2026-10-11). */}
          <VideoSavingPanel editable={editable}
                            classes={status?.detector?.classes || status?.classes || []} />
          </div>

          {/* The actions, on their own footer row: Preview is a question
              ("is this the right camera?"), Apply is the commitment, and
              they were sitting in one undifferentiated line of buttons. */}
          <div className="filter-bar"
               style={{ marginTop: 4, paddingTop: 12, alignItems: "center",
                        borderTop: "1px solid var(--line, rgba(128,128,128,.2))" }}>
            {/* Same grant the server asks for (models.detector_snapshot):
                a preview opens the source, so it is a camera change. */}
            <button className="btn" onClick={snapPreview}
                    disabled={busy || !editable
                              || !(can("detection.camera.change")
                                   || can("detection.camera.add"))}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <CameraIcon size={14} /> Preview Snapshot
            </button>
            {/* This dialog decides WHICH camera and how the box reads it --
                device, size, rate. How the PICTURE looks is the camera's own
                business, and lives one door along: brightness, exposure,
                white balance, whatever this particular camera reports. Not
                offered while adding one, because there is no device to ask
                until it has been applied. */}
            {!addingCam && activeCam && (
              <button className="btn" disabled={busy}
                      onClick={() => setSettingsFor(activeCam)}
                      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <SlidersHorizontal size={14} /> Image Adjustments
              </button>
            )}
            {!addingCam && activeCam && camMode === "depth" && (
              <button className="btn" disabled={busy}
                      onClick={() => setMeasureFor(activeCam)}
                      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <Ruler size={14} /> Measurement
              </button>
            )}
            <div style={{ flex: 1 }} />
            {addingCam && (
              <button className="btn" disabled={busy} onClick={cancelAddCam}>Cancel</button>
            )}
            <button className="btn btn-accent" onClick={saveCamera}
                    disabled={!editable || busy
                              || !can(addingCam && !placeholderCam
                                      ? "detection.camera.add"
                                      : "detection.camera.change")}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              {addingCam ? "Add camera" : "Apply Settings"}
            </button>
          </div>

          {preview && (
            <img src={preview} alt="camera preview"
                 style={{ width: "100%", maxWidth: 520, marginTop: 8, borderRadius: 6, display: "block" }} />
          )}
          {previewMsg && (typeof previewMsg === "string"
            ? <p className="muted small" style={{ marginTop: 6 }}>{previewMsg}</p>
            : <CamErrorText x={previewMsg} style={{ marginTop: 6 }} />)}
        </div>
      </Dialog>

      {/* Opened from either place: the gear on a camera row, or Adjustments
          inside that camera's settings. One dialog, so the two entrances
          cannot drift into showing different things. */}
      <CameraSettingsDialog open={!!settingsFor}
                            onOpenChange={(open) => !open && setSettingsFor(null)}
                            camera={settingsFor} editable={editable} />
      <MeasurementDialog open={!!measureFor}
                         onOpenChange={(open) => !open && setMeasureFor(null)}
                         camera={measureFor} editable={editable}
                         classes={status?.detector?.classes || status?.classes || []} />

      {/* Nothing real is attached: every camera here is the built-in test feed
          (or there are none at all). Say so loudly instead of letting the
          operator watch generated frames and think the system is working --
          a demo pattern looks enough like a running camera to fool a glance. */}
      {/* Cameras ABOVE the picture, not beside it. As a narrow left rail the
          switcher took a column off the feed for its whole height, and the
          feed is the thing being worked on -- aiming a camera and drawing on
          it both want width. Across the top it is one shallow row that grows
          sideways, and the picture gets the full width underneath. */}
      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "minmax(0,1fr)" }}>
        {/* ---- the feed IS the zone editor: one picture for aiming the
            camera and marking what it watches, Start/Stop on its header.
            The rail on the left picks which camera it shows. ---- */}
        <div style={{ minWidth: 0 }}>
          {/* No zone-guidance banner: the zone editor is RIGHT HERE now, so
              a sentence pointing at "the Detection page" explained the page
              to itself. */}
          <ZonesPanel
            camera={selectedCam ?? null}
            /* This page already holds the status and has proven the run
               state; the panel takes both rather than fetching and proving
               them again, which was a second skeleton (and, on a stopped
               system, a second 4 s wait) after this page had rendered. */
            hostStatus={status}
            hostRunning={running}
            hostStopping={stopping}
            onFeedState={onFeedState}
            /* Both feed notices go INSIDE the panel, under the row that names
               the camera and says whether it is live. That row is the subject;
               these say what happened to it. Above the panel they read as
               page-level banners about something further down, with the
               camera's own name and state stranded underneath them. */
            notice={<>
          {/* A real error CARD, not a red sentence: the same treatment every
              other failure on this page gets. It distinguishes the app's own
              recovery (reconnecting 1/3, amber, no action needed) from a
              camera it has given up on (red, with the action that fixes it). */}
          {(() => {
            // The SELECTED camera's own error first: the top-level one is
            // the primary camera's, and with several cameras a broken
            // second camera showed a healthy synthetic feed in silence.
            const camError = activeCam && "error" in (activeCam || {})
              ? activeCam.error : status.error;
            // STOPPED systems raise no camera alarm. A fault belongs to the
            // run that produced it: with detection stopped there is no camera
            // open, so nothing can currently be failing, and a red card here
            // was an alarm about a camera nobody was using -- one that then
            // CLEARED when detection started, which is backwards. The reason
            // is still worth reading (an operator may have stopped the system
            // BECAUSE the camera failed), so it stays on the page as a quiet
            // line about the last run instead of as a live fault.
            if (!running) {
              const lastError = status.last_error;
              if (!lastError) return null;
              // Plain words when it is a camera failure this page knows;
              // anything else (a model that would not load) as it was said.
              const lastX = explainCamError(lastError);
              return (
                <p className="muted small" style={{ marginBottom: 8,
                                                    overflowWrap: "anywhere" }}
                   title={lastX?.known ? lastX.detail : undefined}>
                  Last run ended with: {lastX?.known ? lastX.title : lastX?.said}
                </p>
              );
            }
            if (!camError) return null;
            // Only the app's own RECOVERY is said here, in amber: it needs
            // nothing from the operator. A fault the app has given up on
            // is the popup (CamFaultDialog), once per episode, and after
            // that the rail's status beside the camera.
            if (!/^reconnecting camera/i.test(camError)) return null;
            const camX = explainCamError(camError);
            // Two tokens, not one: --warn FILLS a shape and is read against
            // the page, while the WORDS and the icon have to clear 4.5:1 on
            // the card the theme is painting -- --warn measured 2.9:1 as
            // text on the dark card, a warning nobody can read. Same split
            // LogLevel.jsx's rows use (theme.css .log-level-*).
            const tone = "var(--warn, #b45309)";
            const toneText = "var(--warn-text, #92400e)";
            return (
              <div role="status"
                   style={{ display: "flex", alignItems: "center", gap: 10,
                            border: `1px solid color-mix(in srgb, ${tone} 45%, transparent)`,
                            background: `color-mix(in srgb, ${tone} 10%, transparent)`,
                            borderRadius: 10, padding: "9px 12px",
                            marginBottom: 8 }}>
                <Loader2 size={16} className="animate-spin"
                         style={{ color: toneText, flex: "none" }} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <strong style={{ fontSize: 13, color: toneText }}>
                    Reconnecting the camera…
                  </strong>
                  <div className="muted small" style={{ overflowWrap: "anywhere" }}>
                    {camX.fix}
                  </div>
                </div>
              </div>
            );
          })()}
          {/* Who carries the live view, only when the system asked for
              passthrough ([video] feed); lib/viewFeed.js has the wording. */}
          {running && viewLine(activeCam?.view) && (
            <p className="muted small" style={{ marginBottom: 8 }}>
              {viewLine(activeCam.view)}
            </p>
          )}
          {/* Nothing real is attached: every camera here is the built-in test
              feed (or there are none at all). Say so loudly instead of letting
              the operator watch generated frames and think the system is
              working - a demo pattern looks enough like a running camera to
              fool a glance. */}
          {needsRealCamera && (
            <div className="panel-card" style={{ marginBottom: 8,
                 borderColor: "var(--accent)", background: "rgba(4,186,222,.06)" }}>
              <div className="section-title">No camera connected</div>
              <p className="muted small" style={{ marginBottom: 8 }}>
                {camList.length
                  ? "This system has a camera entry with no source chosen yet."
                  : "This system has no camera yet."}{" "}
                Connect a USB, IP (RTSP) or GigE camera and add it - detection
                cannot start until one is set up.
              </p>
              {/* An empty SLOT is filled by whoever may change a camera; a
                  system with no slot at all needs one added, which is the
                  other permission. */}
              {can(camList.length ? "detection.camera.change"
                                  : "detection.camera.add") && (
                <button className="btn btn-accent btn-sm" disabled={!editable || busy}
                        onClick={beginAddCam}>
                  <CameraIcon size={14} /> Add a camera
                </button>
              )}
            </div>
          )}
            </>}
            /* The camera switcher sits at the TOP of the zone column, right
               above the zone list -- picking a camera and reading that
               camera's zones are one thought, and the list already says
               "on <camera>". Across the page it was the same control an
               arm's length from the sentence it belongs to. */
            sidebarTop={<>
            {/* ---- camera rail: every camera on this box, one card each ---- */}
            <div className="panel-card" style={{ alignSelf: "start", minWidth: 0 }}>
              <div className="section-title">Cameras ({camList.length})</div>
              {!camList.length && (
                <p className="muted small">No camera yet - add one to start detecting.</p>
              )}
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "stretch",
                            gap: 8 }}>
              {camList.map((c) => {
                const sel = String(c.id) === String(selectedCam);
                const on = c.enabled !== false;
                // What this camera is ACTUALLY doing, in its own words and its
                // own colour -- a green dot on a camera whose stream is failing
                // is the lie an operator debugs for an hour. The app reports the
                // per-camera error; running says whether the system is even
                // trying, and `enabled` whether this one was asked to.
                const state = !on ? { label: "Inactive", tone: "var(--muted, #94a3b8)",
                                      dot: "#64748b",
                                      why: "Deactivated - flip the switch to let it run" }
                  : c.error ? { label: "Fault", tone: "var(--alert, #dc2626)",
                                dot: "var(--alert, #dc2626)",
                                why: camErrorText(c.error) }
                  // Stopped by the box: no camera of the system is there.
                  : running && status?.ai_paused === "camera_missing"
                    ? { label: absenceWord(c), tone: "var(--alert, #dc2626)",
                        dot: "var(--alert, #dc2626)",
                        why: `Detection stopped: ${camName(c)} is ${absenceWord(c).toLowerCase()}. It starts again by itself when the camera is back.` }
                  // Stopped: what the probe just saw, not what the last run
                  // remembered (see probePresence). Live here means the
                  // camera answers and Start will open it.
                  : !running && presence[c.id] === false
                    ? { label: absenceWord(c), tone: "var(--alert, #dc2626)",
                        dot: "var(--alert, #dc2626)",
                        why: `${camName(c)} is ${absenceWord(c).toLowerCase()} - Start will be refused until it is back` }
                  // "Live" is reserved for a picture on screen (below). A
                  // camera that merely answers is OK, and said so.
                  : !running && presence[c.id] === true
                    ? { label: "Camera OK", tone: "var(--ok, #16a34a)",
                        dot: "var(--ok, #23c552)",
                        why: "Camera answers - detection is stopped, press Start Detection" }
                  : !running && looking && presence[c.id] === undefined
                    ? { label: "Checking", tone: "var(--warn, #d97706)",
                        dot: "var(--warn, #d97706)",
                        why: "Looking for the camera…" }
                  : !running ? { label: "Idle", tone: "var(--muted, #94a3b8)",
                                 dot: "#64748b",
                                 why: "Detection is stopped - press Start Detection" }
                  : status?.stalled ? { label: "Opening", tone: "var(--warn, #d97706)",
                                dot: "var(--warn, #d97706)",
                                why: "Opening the stream…" }
                  // Running and healthy. Only the SELECTED camera has a
                  // player, so only it can be "Live" -- and only once that
                  // player has a frame on screen. The others are fine as
                  // cameras (the app reports frames from them); what they are
                  // not is on screen, so they do not get the word.
                  : sel && feedState.failed
                    ? { label: "No video", tone: "var(--alert, #dc2626)",
                        dot: "var(--alert, #dc2626)",
                        why: "The camera runs but no picture reached this page - press Retry on the feed" }
                  : sel && !feedState.live
                    ? { label: "Connecting…", tone: "var(--warn, #d97706)",
                        dot: "var(--warn, #d97706)",
                        why: "Opening the video stream in this page…" }
                  : sel
                    ? { label: "Live", tone: "var(--ok, #16a34a)",
                        dot: "var(--ok, #23c552)", why: "Video is showing" }
                  : { label: "Camera OK", tone: "var(--ok, #16a34a)",
                      dot: "var(--ok, #23c552)",
                      why: "The camera delivers frames - select it to watch" };
                return (
                  <div key={c.id} onClick={() => selectCam(c.id)}
                       {...clickable(() => selectCam(c.id))}
                       aria-pressed={sel}
                       title={[camSourceSafe(c.source), state.why].filter(Boolean).join(" · ")}
                       style={{ display: "flex", alignItems: "center", gap: 8,
                                cursor: "pointer",
                                border: `1px solid ${sel ? "var(--accent)"
                                          : c.error && on ? "rgba(220,38,38,.45)"
                                          : "rgba(128,128,128,.2)"}`,
                                background: sel ? "rgba(4,186,222,.12)" : "transparent",
                                borderRadius: 10, padding: "8px 9px",
                                // One camera per line, each filling the column.
                                // Sizing them to their own names made sense
                                // across the page; in a column it left ragged
                                // rows and put "+ Add camera" alongside the
                                // last camera as though it were another one.
                                flex: "1 1 100%", minWidth: 0 }}>
                    <span style={{ flex: "0 0 auto", width: 28, height: 28,
                                   borderRadius: 8, display: "grid",
                                   placeItems: "center",
                                   background: sel ? "rgba(4,186,222,.14)"
                                                   : "rgba(128,128,128,.12)",
                                   color: state.tone }}>
                      <CameraIcon size={15} />
                    </span>
                    <span style={{ flex: 1, minWidth: 0, display: "grid", gap: 1 }}>
                      {/* The SLOT first, the hardware second. Everything that
                          points at a camera -- a workflow branch, a snapshot,
                          a rule -- names the slot, because the hardware in it
                          is a thing an operator swaps. Reading "Camera 2"
                          here is what makes "Camera 2" over there mean
                          something. */}
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                                     whiteSpace: "nowrap", fontSize: 13 }}>
                        Camera {camList.indexOf(c) + 1}
                        <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>
                          {camName(c)}
                        </span>
                      </span>
                      {/* The dot only REPORTS -- activation is the switch beside
                          Start/Stop now. A status light that is also a button read
                          as neither: it was clicked by accident mid-select and
                          missed on purpose. */}
                      {/* ...except a FAULT: that status is the one thing
                          left of the popup after Close, so it reopens it.
                          Stops its own click so it does not also re-select. */}
                      {(() => {
                        const fault = camFaultOf(c, running, presence, status);
                        const reopen = fault ? (e) => {
                          e.stopPropagation();
                          selectCam(c.id);
                          setFaultPopup(c.id);
                        } : null;
                        return (
                          <span style={{ display: "flex", alignItems: "center", gap: 5,
                                         fontSize: 11, color: state.tone,
                                         cursor: reopen ? "pointer" : undefined,
                                         textDecoration: reopen ? "underline dotted" : undefined }}
                                title={reopen ? "Show what is wrong" : undefined}
                                onClick={reopen || undefined}
                                {...(reopen ? clickable(() => { selectCam(c.id); setFaultPopup(c.id); }) : {})}>
                            <span style={{ width: 9, height: 9, borderRadius: 999,
                                           flex: "0 0 auto",
                                           background: state.dot }} />
                            {state.label}
                          </span>
                        );
                      })()}
                    </span>
                    {/* Each camera's own switch, on its own row. One switch
                        for the SELECTED camera (beside Start/Stop) meant
                        activating three cameras was three selections and three
                        clicks in two different places; here the row says what
                        the camera is and decides whether it runs.
                        Stops its own click so flipping the switch does not
                        also select the row. */}
                    <button type="button" role="switch"
                            aria-checked={on}
                            className={"switch" + (on ? " switch-on" : "")
                                       + (busy ? " is-busy" : "")
                                       + (!editable ? " is-disabled" : "")}
                            style={{ "--sw-w": "30px", "--sw-h": "17px",
                                     flex: "0 0 auto" }}
                            disabled={!editable || busy
                                      || !can("detection.camera.activate")}
                            title={!can("detection.camera.activate")
                              ? "How many cameras run is the box owner's setting"
                              : on
                              ? "Active - runs when detection starts. Click to deactivate (settings kept)."
                              : "Inactive - will not run until activated. Click to activate."}
                            onClick={(e) => { e.stopPropagation(); toggleCamEnabled(c); }}>
                      <span className="switch-knob" />
                    </button>
                    {/* The LAST camera is removable too: a system with none is a
                        blank canvas that simply cannot start until one is added.
                        Not offered at all without the capability -- which
                        camera slots EXIST is the box owner's decision, and an
                        operation lead re-points them rather than removing
                        them. */}
                    {/* Every camera carries its own settings -- brightness,
                        exposure, whatever THAT camera reports. On the row
                        rather than under the selected camera's panel, because
                        "adjust the picture on camera 2" should not start with
                        selecting camera 2 and hunting for where the controls
                        went. */}
                    <button className="icon-btn"
                            title={`${camName(c)} settings - brightness, exposure, and whatever else this camera offers`}
                            onClick={(e) => { e.stopPropagation(); setSettingsFor(c); }}
                            style={{ flex: "0 0 auto" }}>
                      <SlidersHorizontal size={14} />
                    </button>
                    {/* Only a depth camera can measure, so only a depth
                        camera gets the ruler -- on an ordinary one it would
                        be a promise the panel then takes back. */}
                    {c.kind === "depth" && (
                      <button className="icon-btn"
                              title={`${camName(c)} measurement - calibrate the floor, measure objects, save what each carries`}
                              onClick={(e) => { e.stopPropagation(); setMeasureFor(c); }}
                              style={{ flex: "0 0 auto" }}>
                        <Ruler size={14} />
                      </button>
                    )}
                    {can("detection.camera.remove") && (
                      <button className="btn btn-sm" disabled={!editable || busy}
                              title={`Remove ${camName(c)}`}
                              onClick={(e) => { e.stopPropagation(); removeCam(c); }}
                              style={{ padding: "0 6px", lineHeight: "18px" }}>×</button>
                    )}
                  </div>
                );
              })}
              {/* The button STAYS at the limit and says why when pressed.
                  Removing it left an operator looking for a control that had
                  silently gone, with a bare "3 out of 4" underneath and no
                  connection between the two -- the answer ("something else on
                  this box is using the camera you want") is exactly the thing
                  worth saying out loud. */}
              {can("detection.camera.add") && (
              <button className="btn btn-sm"
                      style={{ padding: "10px 14px", borderStyle: "dashed",
                               flex: "1 1 100%" }}
                      disabled={!editable || busy}
                      title={budget.remaining > 0
                               ? "Add another camera"
                               : "The box's camera budget is fully used"}
                      onClick={() => {
                        if (camList.length >= (budget.max_configured ?? 16)) {
                          return toast.error(
                            "This system already has its maximum camera entries",
                            `At most ${budget.max_configured ?? 16} per system - `
                            + "delete one you no longer use.");
                        }
                        if (budget.remaining <= 0) {
                          return toast.error(
                            `All ${budget.max ?? MAX_CAMERAS} cameras on this box are in use`,
                            (budget.used_elsewhere
                              ? `${budget.used_here ?? 0} here and `
                                + `${budget.used_elsewhere} on other systems. `
                                + "Stop a system, or switch one of its cameras "
                                + "off, to free a slot."
                              : "Switch one of this system's cameras off to "
                                + "free a slot - its settings are kept."));
                        }
                        beginAddCam();
                      }}>
                + Add camera
              </button>
              )}
              </div>
              {/* The running budget, as a plain count. The full explanation
                  (switched-off starts, per-system caps) lives in the Add
                  button's tooltip; a sentence here read as a warning. */}
              <p className="muted small" style={{ marginTop: 6, marginBottom: 0 }}>
                {/* Used against whichever cap binds: the box cap counts every
                    RUNNING system's cameras, the per-system cap only this one's. */}
                {budget.limit === "box"
                  ? (budget.used_here ?? 0) + (budget.used_elsewhere ?? 0)
                  : (budget.used_here ?? 0)}
                {" out of "}
                {budget.limit === "box"
                  ? (budget.max ?? MAX_CAMERAS)
                  : (budget.max_per_system ?? MAX_PER_SYSTEM)}
              </p>

            </div>
            {activeCam?.snap && <CameraSnaps camera={activeCam} />}
            </>}
            feedNonce={feedNonce}
            underFeed={<FeedStatusPanel editable={editable} compact />}
            toolbar={<>
              {/* Model / Camera / Capture, folded into the toolbar row so
                  the sidebar keeps its space for zones. Each is one button:
                  what it is, and the way to change it. */}
              <Link className="btn btn-sm" to="/models"
                    title="Detector and model - change in AI Models">
                {status.detector.kind}
                {status.detector.model ? ` · ${status.detector.model}` : " · no model"}
              </Link>
              <button className="btn btn-sm"
                      disabled={!editable
                                || !can(camList.length ? "detection.camera.change"
                                                       : "detection.camera.add")}
                      title={!camList.length ? "Add a camera"
                        : `Camera Setting - ${camName(activeCam)}`
                          + (activeCam?.source
                            ? `, source: ${camSourceSafe(activeCam.source)}` : "")}
                      onClick={() => { if (addingCam) cancelAddCam(); setAsked(false); setCamOpen(true); }}
                      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <CameraIcon size={14} />
                {/* Says what the button OPENS. The camera's name already heads
                    the rail and the feed; repeated here it read as a label
                    ("LRCP U3-imx307: LRCP U3-imx307"), not as a way in. The
                    name stays in the tooltip. */}
                {camList.length ? "Camera Setting" : "Add a camera…"}
              </button>
              <CapturePanel editable={editable && can("detection.capture")} compact />
            </>}
            actions={<>
              {/* Active/inactive is per ROW now, in the camera list: one
                  switch for the selected camera meant activating three
                  cameras was three selections and three clicks in two
                  different places. Start still runs every ACTIVE camera. */}
              {running ? (
                <button className="btn btn-sm"
                        disabled={!editable || busy || !can("detection.power")}
                        onClick={() => power("stop")}
                        style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  {transition === "stop" ? <Loader2 className="animate-spin" size={14} /> : <Square size={13} />}
                  {transition === "stop" ? "Stopping…" : "Stop"}
                </button>
              ) : (
                <button className="btn btn-sm btn-accent"
                        disabled={!editable || busy || needsRealCamera || stopping
                                  || !can("detection.power")}
                        title={needsRealCamera
                          ? "Choose a camera first - detection needs one to run"
                          : stopping
                          ? "Stopping - the camera and the AI chip are being released"
                          : "Start detection"}
                        onClick={() => power("start")}
                        style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  {transition === "start" || stopping
                    ? <Loader2 className="animate-spin" size={14} /> : <Play size={13} />}
                  {transition === "start" ? "Starting…" : stopping ? "Stopping…" : "Start"}
                </button>
              )}
            </>} />
        </div>
      </div>
    </section>
  );
}

// Capture - the snapshot and record switches, plus the clips they produced.
//
// These are a CAPABILITY, not a schedule: the workflow's Snapshot and Record
// nodes decide when to fire, these decide whether anything may be written to
// disk at all. They belong beside the camera because that is the thing they
// consume, and they were previously read-only here, which left an operator
// staring at "record off" with no way to change it.
function CapturePanel({ editable, compact = false }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get("/api/detector/control/capture").then(setData).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  // Paused while the tab is hidden, refetched on return (useVisibleInterval).
  useVisibleInterval(load, 8000);

  async function set(fields) {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/capture", fields);
      setData((prev) => ({ ...(prev || {}), capture: d.capture }));
      toast.success("Capture updated", "The app applies it within a few seconds.");
    } catch (e) { toast.error("Could not change capture", e.message); }
    finally { setBusy(false); load(); }
  }

  if (!data) return null;
  const clips = data.clips || [];
  const cap = data.capture || {};
  // No second storage: the switches are refused server-side, so offer the
  // reason instead of a toggle that can only bounce.
  const noStore = data.storage_external === false;

  if (compact) {
    // Toolbar form: one button per capture switch, green while it may write
    // to disk, BLUE while it may not -- same language as the status-box
    // buttons under the feed. Nothing being recorded is exactly the state an
    // operator must notice without reading a tooltip.
    const toggle = (label, key, on) => (
      <button className="btn btn-sm" disabled={!editable || busy || (noStore && !on)}
              title={noStore && !on
                ? "Needs second storage: fit an NVMe (or set [app].data_dir to another drive) - recordings must not fill the boot media"
                : on ? `${label} may be saved - click to switch off`
                     : `${label} are NOT saved - click to allow`}
              onClick={() => set({ [key]: !on })}
              style={on ? { borderColor: "var(--ok, #23c552)",
                            color: "var(--ok, #23c552)" }
                        : { borderColor: "var(--accent, #04bade)",
                            color: "var(--accent-text, #04bade)" }}>
        {label}
      </button>
    );
    return (
      <>
        {toggle("Snapshots", "snap", !!cap.snap)}
        {toggle("Clips", "record", !!cap.record)}
      </>
    );
  }

  return (
    <div style={{ marginTop: 10, paddingTop: 8,
                  borderTop: "1px solid rgba(128,128,128,.18)" }}>
      {/* No explanations: "Snapshots" and "Clips" say what they are, and a
          paragraph under each switch made a four-line panel eleven lines long
          for facts the operator learns once. The switch IS the control. */}
      {/* Same shape as the overlay rules above: label left, switch hard
          right, no wrapping. `model-row` is not used here for the same reason
          it is not used there -- it wraps in this narrow column. */}
      <div style={{ display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
        <div style={{ minWidth: 0, flex: 1 }}>Snapshots</div>
        <Switch checked={!!cap.snap} disabled={!editable || busy}
                onCheckedChange={(on) => set({ snap: on })}
                aria-label="Save snapshots" />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
        <div style={{ minWidth: 0, flex: 1 }}>Clips</div>
        <Switch checked={!!cap.record} disabled={!editable || busy}
                onCheckedChange={(on) => set({ record: on })}
                aria-label="Save clips" />
      </div>

      <p className="muted small" style={{ marginTop: 6, marginBottom: 0 }}>
        {clips.length
          ? <>{clips.length} clip{clips.length > 1 ? "s" : ""} saved · </>
          : "No clips yet · "}
        <Link to="/footage">Footage →</Link>
      </p>
    </div>
  );
}
