// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// ZonesPanel - create and manage zone DEFINITIONS, nothing else: geometry,
// names, colours, the count-when mode and the enabled flag. Everything a zone
// DOES -- counting, alerts, timers -- is wired on the Workflow page; the
// per-zone actions this panel used to edit duplicated that whole page in
// miniature, so they are gone from here. Each zone automatically becomes a
// Zone node in the workflow builder the moment analytics use it.
//
// A PANEL, not a page: it renders inside Camera Setup's Zones tab, over the
// same live feed the cameras are aimed with. The /zones route lands there.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { VideoOff, SquareDashed, Circle as CircleIcon, Pentagon,
         Spline, Menu } from "lucide-react";
import UndoRedo from "../components/ui/UndoRedo.jsx";
import CameraLoader from "../components/CameraLoader.jsx";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import { useEditHistory } from "../hooks/use-edit-history.js";
import { useSteadyRunning } from "../hooks/use-steady-running.js";
import { useUnsavedGuard } from "../hooks/use-unsaved-guard.jsx";
import { ErrorNote, SkelEditor } from "../components/Loading.jsx";
import { Badge } from "../components/ui/primitives.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { useConfirm } from "../components/ui/ask.jsx";
import ZoneCanvas, { ZONE_PALETTE, zoneColor, buildPoints } from "../components/ZoneCanvas.jsx";
import WebRTCVideo from "../components/WebRTCVideo.jsx";
import { camErrorText } from "../lib/camErrors.js";
import { apiUrl } from "../lib/base.js";

//: How long a feed may sit on "connecting" before it is re-armed, and how many
//: times. Three seconds is chosen from the operator's side: long enough that a
//: camera merely slow to open is never interrupted, short enough that nobody
//: reaches for the browser's reload -- which is the thing that actually breaks
//: (see loadStatus). The waits grow, because a stream that has failed twice
//: will not succeed on a faster retry, and each attempt costs the box a fresh
//: WHEP handshake.
const FEED_RETRY_MS = 3000;
const FEED_RETRIES = 3;

// The camera list, however the server phrased it: `cameras` on a multi-camera
// box, the singular `camera` (= the first one) on an older payload.
function camListOf(status) {
  if (!status) return [];
  if (Array.isArray(status.cameras) && status.cameras.length) return status.cameras;
  return status.camera ? [status.camera] : [];
}

// What a camera is CALLED once anything points at it: its SLOT. The device's
// own name is the hardware in that slot today and goes stale the moment it is
// swapped -- which is exactly what a zone, and the rule reading the zone,
// must survive. Same wording as the Camera Setup rail and the workflow canvas.
function camSlot(list, id) {
  const i = (list || []).findIndex((c) => String(c.id) === String(id));
  return i < 0 ? "camera" : `Camera ${i + 1}`;
}

function camName(cam) {
  if (!cam) return "camera";
  return cam.name || cam.label || cam.source || "camera";
}

/**
 * The status box, behind one button beside the Live View pill.
 *
 * Closes on a click anywhere outside it and on Escape -- the same contract
 * every other menu on the product keeps (ViewSettings, DatePicker, the canvas
 * context menu), so a panel left open is never something to go back and tidy.
 * The listener is only attached while it is open.
 */
function FeedMenu({ children }) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div ref={boxRef} style={{ position: "relative", display: "inline-block" }}>
      <button type="button" className={"btn btn-sm" + (open ? " btn-accent" : "")}
              aria-expanded={open} aria-haspopup="true"
              title="Feed status - snapshots, clips and what the app reports"
              onClick={() => setOpen((v) => !v)}
              style={{ display: "inline-flex", alignItems: "center", padding: "5px 8px" }}>
        <Menu size={15} />
      </button>
      {open && (
        <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0,
                      zIndex: 40, minWidth: 260,
                      background: "var(--surface, #16181d)",
                      border: "1px solid var(--line, rgba(128,128,128,.28))",
                      borderRadius: 10, padding: 10,
                      boxShadow: "0 10px 30px rgba(0,0,0,.35)" }}>
          {children}
        </div>
      )}
    </div>
  );
}

//: Where a host page wants this panel's Save/Discard/undo to appear. The page
//: renders an empty node with this id in its own action row; if it does not,
//: the buttons render where they always did rather than vanishing.
export const ZONE_ACTIONS_SLOT = "zone-actions-slot";

/**
 * Save / Discard / undo, rendered into the host page's action row.
 *
 * A portal rather than lifted state: the dirty set, the edit history and the
 * save all live in the panel, and threading them up to the page merely to
 * place three buttons would put the state further from what it describes.
 * Falls back to rendering in place when the host provides no slot, so the
 * panel is never a page with no way to save.
 */
function PageActions({ children }) {
  const [host, setHost] = useState(null);
  useEffect(() => {
    // After paint: the slot is rendered by the page in the same commit, so
    // looking during render would miss it on the first pass.
    setHost(document.getElementById(ZONE_ACTIONS_SLOT));
  }, []);
  if (!host) {
    return (
      <div className="filter-bar" style={{ marginBottom: 12, flexWrap: "wrap",
                                           justifyContent: "flex-end" }}>
        {children}
      </div>
    );
  }
  return createPortal(children, host);
}

/**
 * `camera`: when given, the shown camera is CONTROLLED by the host page (the
 * Camera Setup rail) and the panel's own switcher row is hidden.
 * `actions`: extra controls (the Start/Stop button) rendered in the Camera
 * view header, so power and drawing live on the same card.
 * `underFeed`: the status box. Rendered behind a menu button at the LEFT of
 * the Live View pill -- it used to sit permanently below the drawing toolbar,
 * spending the picture's vertical room on numbers that are only read when
 * something looks wrong.
 * `sidebarTop`: a card for the TOP of the sidebar column, above the zone
 * list (the host page's camera switcher).
 * `sidebarExtra`: cards the host wants in the SAME COLUMN as the zone list
 * (Model / Camera / Capture), so everything sits in one row with the image.
 * `hostStopping`: the host's word that Stop was pressed and the app is
 * still releasing the camera and the chip -- the overlay says so instead of
 * "Detection is stopped", so a Start that is held reads as waiting, not
 * broken.
 * `hostStatus` / `hostRunning`: the host page's own /api/detector/control
 * answer and its PROVEN run state. Given, the panel starts from them and
 * follows the host's polls instead of fetching and proving the same thing a
 * second time -- which showed a second skeleton after the page had rendered,
 * and on a stopped system held it for another full poll.
 */
export default function ZonesPanel({ camera: cameraProp, actions = null,
                                     underFeed = null, sidebarExtra = null,
                                     sidebarTop = null, notice = null,
                                     toolbar = null, feedNonce = 0,
                                     hostStatus = null, hostRunning = null,
                                     hostStopping = false,
                                     // Told {live, failed} whenever the FEED's
                                     // own state changes -- what the player is
                                     // actually showing, which is what a host
                                     // page's "Live" label has to mean.
                                     onFeedState = null }) {
  const controlled = cameraProp !== undefined;
  // Fixed for the panel's life: a host that hands status over does so from
  // its first render.
  const hosted = hostStatus != null;
  // Last camera id the feed showed -- see feedCam below.
  const lastShownRef = useRef(null);
  const toast = useToast();
  const confirm = useConfirm();
  const [status, setStatus] = useState(hostStatus);
  const [error, setError] = useState(null);
  const [zones, setZones] = useState(() => (hostStatus && hostStatus.zones) || []);
  const [busy, setBusy] = useState(false);
  // Edits stay LOCAL until Save. Zones drive analytics and alerts, so a
  // half-drawn shape or a mis-typed limit reaching the running app the instant
  // it is typed is worse than a button.
  //
  // Dirtiness is tracked PER ZONE: editing zone 2 must light up zone 2's Save,
  // not zone 1's. The page is dirty when any zone is.
  const [dirtyIds, setDirtyIds] = useState(() => new Set());
  const dirty = dirtyIds.size > 0;
  const markDirty = useCallback((id) => {
    setDirtyIds((cur) => (cur.has(id) ? cur : new Set(cur).add(id)));
  }, []);
  // Undo/redo over the whole working copy, Ctrl+Z / Ctrl+Shift+Z. A zone is
  // drawn by hand, so it is mis-drawn by hand; without this the only way back
  // from one bad drag was Discard, which throws away every other edit too.
  // The snapshot carries the dirty set as well, or undoing a change would
  // leave its zone still marked unsaved.
  const historyState = useMemo(() => ({ zones, dirtyIds: [...dirtyIds] }),
                               [zones, dirtyIds]);
  const restore = useCallback((snap) => {
    setZones(snap.zones || []);
    setDirtyIds(new Set(snap.dirtyIds || []));
  }, []);
  const history = useEditHistory(historyState, restore);
  // True between the first pointermove of a shape drag and its pointerup.
  const dragRef = useRef(false);

  // Zone id -> the raw text in its Crossing threshold box while it is being
  // edited. Only a CLEARED box needs remembering: the field shows the value
  // the line actually decides with, so without this, backspacing the last
  // digit would redraw 1.2 under the cursor and the next keystroke would
  // append to it.
  const [bandEdit, setBandEdit] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const [feedKey, setFeedKey] = useState(0);
  // The MEASURED aspect of the frames actually arriving, which is the only
  // reliable one: [camera] width/height in config is what was requested, not
  // what the camera sends.
  const [feedAspect, setFeedAspect] = useState(null);
  // Has THIS camera's stream produced a frame yet? A camera the operator just
  // added needs several seconds to open (RTSP handshake), during which the
  // preview 404s/503s and the retry loop would otherwise show them nothing but
  // a broken image. One flag, flipped by the img's own load event - no polling.
  const [feedLive, setFeedLive] = useState(false);
  //: The feed gave up: every re-arm below ran and no frame came. The player
  //: is unmounted in this state (nothing left connecting, retrying or
  //: holding a socket) and the box says so, with a Retry -- a loader that
  //: turns for ever is a promise the page cannot keep.
  const [feedFailed, setFeedFailed] = useState(false);
  //: The depth camera's picture in false colour instead of its own. Only
  //: offered on a depth camera; the zones draw over either picture alike.
  const [depthView, setDepthView] = useState(false);
  // A "stopped" report has to survive two polls before this panel believes
  // it: a transient false (app restarting, master respawning) used to flip
  // the whole editor to its stopped state and tear the live stream down.
  // The host has already proven the run state when it hands one over; a
  // second proof here would only start a second wait.
  const ownSteadyRunning = useSteadyRunning(status);
  const steadyRunning = hostRunning ?? ownSteadyRunning;
  // Stopping and restarting detection re-opens the camera, so the last frame
  // proves nothing about the new one. Keyed to the STEADY state -- a
  // transient flap must not reset a perfectly good stream.
  useEffect(() => { setFeedLive(false); setFeedFailed(false); }, [steadyRunning]);

  const feedRetries = useRef(0);

  // The host's "Live" is this feed's "live": report it, not the camera's
  // health, which the host already has from the status poll.
  useEffect(() => {
    if (onFeedState) onFeedState({ live: feedLive, failed: feedFailed });
  }, [onFeedState, feedLive, feedFailed]);

  // How many workflow nodes reference each zone - the delete guard.
  const [zoneRefs, setZoneRefs] = useState({});

  const [drawMode, setDrawMode] = useState(null);   // null | box | circle | area | line
  const [draft, setDraft] = useState([]);

  // Which camera is being drawn on. Zones stay ONE list for the whole box -
  // this only decides which of them are shown and which camera a new zone is
  // stamped with.
  const [zoneCam, setZoneCam] = useState(null);

  const loadStatus = useCallback(function load(force = false, attempt = 0) {
    api.get("/api/detector/control")
      .then((d) => {
        setStatus(d); setError(null);
        // Never overwrite edits the operator has not saved yet.
        setDirtyIds((cur) => {
          if (force || !cur.size) { setZones(d.zones || []); return new Set(); }
          return cur;
        });
      })
      .catch((e) => {
        // A TIMEOUT here is usually this page's own doing rather than a box
        // that is down: the live feed is a long-lived connection, a browser
        // allows about six per origin, and a reload while a stream is still
        // opening can leave this request queued behind one that never
        // finishes. Painting "the system did not answer in time" over a
        // perfectly healthy system -- which is what a refresh mid-connect
        // did -- is the wrong answer to a question worth simply asking
        // again. Status 0 is the client's own timeout/network error
        // (api/client.js); a real 4xx/5xx is reported at once.
        if (e?.status === 0 && attempt < 2) {
          setTimeout(() => load(force, attempt + 1), 1200);
          return;
        }
        setError(e);
      });
  }, []);
  const loadRefs = useCallback(() => {
    api.get("/api/workflow")
      .then((d) => setZoneRefs(d.zone_usage || {}))
      .catch(() => setZoneRefs({}));
  }, []);
  useEffect(() => { if (!hosted) loadStatus(); loadRefs(); },
            [hosted, loadStatus, loadRefs]);
  // One merge for both sources of a fresh status: the panel's own poll, or
  // the host page's (applied below as its `hostStatus` changes).
  const mergeStatus = useCallback((d) => {
    // ZONES are the page's own working copy and must survive the poll --
    // that is the whole reason this merges instead of replacing. Everything
    // else in the payload is the server's to state, and narrowing the merge
    // to running/stalled froze the rest at whatever the FIRST load returned.
    //
    // `cameras` was the expensive one. It carries each camera's `enabled` and
    // `error`, which drive the Live View pill, the "Camera inactive"
    // placeholder and whether zones may be drawn at all -- so switching a
    // camera on left this panel insisting it was off for as long as the panel
    // stayed mounted. Going to the hub and back "fixed" it because the
    // remount refetched. `editable` moves too: a permission or a stop can
    // change it, and a stale copy either locks the tools or offers them
    // against a system that will refuse the save.
    //
    // A success also CLEARS a stuck error: the first load can catch a master
    // that is still warming up, and without this the page sat on the error
    // note for ever while detection ran plainly underneath -- the "have to
    // refresh the page" bug.
    setError(null);
    setStatus((s) => (s ? { ...s, running: d.running,
                            stalled: d.stalled,
                            ai_paused: d.ai_paused || null,
                            // Only when the payload actually carries them:
                            // an older/partial answer must not blank the
                            // list this panel is drawing against.
                            ...(d.cameras ? { cameras: d.cameras } : {}),
                            ...(d.camera ? { camera: d.camera } : {}),
                            ...(d.editable === undefined
                                  ? {} : { editable: d.editable }) } : d));
    setDirtyIds((cur) => {
      if (cur.size) return cur;           // never clobber unsaved edits
      setZones((z) => (z.length ? z : d.zones || []));
      return cur;
    });
  }, []);
  useVisibleInterval(() => {
    // Hosted, the host's poll is this panel's poll: two pages asking the
    // same question every 4 s was twice the load for the same answer.
    if (hosted) return;
    api.get("/api/detector/control").then(mergeStatus).catch(() => {});
  }, 4000);
  useEffect(() => { if (hostStatus) mergeStatus(hostStatus); },
            [hostStatus, mergeStatus]);

  // Default to the first camera, and hold the operator's choice across polls;
  // only fall back when the selected camera has been removed.
  useEffect(() => {
    const cams = camListOf(status);
    if (!cams.length) return;
    setZoneCam((cur) => (cur != null && cams.some((c) => String(c.id) === String(cur))
                           ? cur : cams[0].id));
  }, [status]);

  // Controlled camera changed (the host page's rail): the old stream is a
  // different feed, so drop the draft and reconnect -- same reset the
  // panel's own switcher performs.
  //
  // `feedNonce` covers the case the id cannot: the SAME camera entry
  // re-pointed at a different device. MJPEG is one long-lived connection --
  // nothing re-requests it while the id is unchanged -- so an edited camera
  // went on showing the old picture until the page was reloaded.
  useEffect(() => {
    if (!controlled) return;
    setDraft([]); setDrawMode(null); setSelectedId(null);
    setFeedAspect(null); setFeedLive(false); setFeedFailed(false);
    // A different camera is a fresh subject: whatever the last one spent its
    // retries on says nothing about this one.
    feedRetries.current = 0;
    setFeedKey((k) => k + 1);
  }, [controlled, cameraProp, feedNonce]);

  // THE STREAM THAT NEVER PAINTS.
  //
  // WebRTCVideo has its own watchdogs and falls back to MJPEG, but both can
  // end in a session that is "connected" and carrying nothing -- and then the
  // "Camera connecting" sheet sits there until somebody navigates away and
  // back, which is a REMOUNT, which is all it ever needed. So the page does
  // that itself: bumping feedKey remounts the player exactly as re-entering
  // the page does.
  //
  // It stops after a few tries. A camera that is genuinely unreachable should
  // end up saying so rather than flickering a reconnect every three seconds
  // for the rest of the afternoon -- and it DOES say so: the last wait ends
  // in feedFailed, which takes the player down and puts up "No video" with a
  // Retry. It used to end in nothing at all, the loader simply turning on
  // over a player nobody would remount again (Tok Aba, 2026-10-09: "black
  // with only the loader, forever").
  useEffect(() => {
    if (feedLive) { feedRetries.current = 0; return undefined; }
    if (!steadyRunning || feedFailed) return undefined;
    const spent = feedRetries.current >= FEED_RETRIES;
    const wait = FEED_RETRY_MS * (spent ? 2 : feedRetries.current + 1);
    const t = setTimeout(() => {
      if (spent) { setFeedFailed(true); return; }
      feedRetries.current += 1;
      setFeedAspect(null);
      setFeedKey((k) => k + 1);
    }, wait);
    return () => clearTimeout(t);
    // feedKey is a dependency on purpose: each re-arm starts the next wait.
  }, [feedLive, feedFailed, steadyRunning, feedKey]);

  /** Retry, by hand: a fresh budget and a fresh player. */
  const retryFeed = () => {
    feedRetries.current = 0;
    setFeedFailed(false); setFeedLive(false); setFeedAspect(null);
    setFeedKey((k) => k + 1);
  };

  // Leaving mid-edit should not silently bin the work -- the guard covers the
  // router (sidebar, any Link) with a dialog and the browser's own close with
  // the native prompt.
  const unsavedDialog = useUnsavedGuard({
    when: dirty,
    what: "Zones",
    detail: "Zone changes have not been sent to the board yet.",
    busy,
    onSave: () => saveZones(),
  });

  // Editing needs the detector RUNNING, not just permission. A zone is drawn
  // onto the live picture: with detection stopped the canvas shows the last
  // frame or nothing at all, so a shape placed then is aimed at a guess. It
  // would also be saved against a camera whose real frame size is unconfirmed.
  // Read-only until the camera is back, rather than quietly accepting work
  // that lands in the wrong place.
  const editable = !!status?.editable && steadyRunning === true;
  // SAVING is permission only, deliberately NOT gated on running: detection
  // can stop (a camera drop, an operator on System Control) while edits are
  // still unsaved, and locking the Save button then would strand work that
  // was drawn perfectly well while the feed was up.
  const canSave = !!status?.editable;
  // Drawing a zone, moving one and deleting one are three permissions, not
  // one: an operation lead adjusts the area a camera watches, and the areas
  // themselves are the box owner's decision. Straight from the server that
  // enforces it (core/services/caps.py).
  const can = (cap) => (status?.caps || []).includes(cap);

  /**
   * Write the zone list. The server stores zones as ONE document, so every
   * save sends all of them - `only` just says which card's button was pressed,
   * so the toast and the cleared-dirty flag can be about that zone rather than
   * claiming more than happened.
   */
  async function saveZones(only) {
    // Guard: as an onClick handler React hands us the click event -- that is
    // "save all", not "save one", so anything that isn't a zone id means all.
    if (typeof only !== "string") only = null;
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/zones", { zones });
      setZones(d.zones || []);
      setDirtyIds((cur) => {
        if (!only) return new Set();
        const next = new Set(cur);
        next.delete(only);
        return next;
      });
      const z = only && zones.find((x) => x.id === only);
      toast.success(z ? `${z.name} saved` : "Zones saved",
                    "The app applies them within a few seconds.");
      // Saved = done with setup: collapse the open card and drop the edit
      // handles, so the feed goes back to just showing the zones.
      setSelectedId(null);
      setDrawMode(null); setDraft([]);
    } catch (e) { toast.error("Could not save zones", e.message); }
    finally { setBusy(false); }
  }

  // Ctrl+S saves, the way it does on the Workflow canvas. Zone work is a
  // sequence of small drags, and reaching for a button after each one is the
  // interruption; the browser's own "save this page" is never what someone
  // hitting Ctrl+S over a zone editor meant, so it is taken.
  //
  // Ref, not a dependency: the handler is bound once, and re-binding it on
  // every zone edit would be a listener swap per keystroke of a drag.
  const saveRef = useRef(null);
  saveRef.current = { canSave, dirty, busy, save: saveZones };
  useEffect(() => {
    function onKey(e) {
      if (e.key !== "s" && e.key !== "S") return;
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const s = saveRef.current;
      if (!s.canSave) return;                 // read-only: leave the browser to it
      e.preventDefault();
      if (s.dirty && !s.busy) s.save();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // -- take the app's own zone outlines off the feed while editing ----------
  // The app BURNS zone outlines into the video, so an unsaved drag put two
  // shapes on the picture: the one under the pointer and the one the app is
  // still running. The app hides its copy while this page says it is editing,
  // and the ask carries a DEADLINE it renews -- so discarding, navigating away
  // or closing the laptop brings the outlines back on their own, and only a
  // save changes what is drawn from then on.
  const feedCamRef = useRef(null);
  useEffect(() => {
    if (!dirty || !canSave) return undefined;
    let alive = true;
    const ping = () => {
      if (!alive) return;
      api.post("/api/detector/control/zones/editing", {
        editing: true, seconds: 6,
        cameras: feedCamRef.current == null ? [] : [feedCamRef.current],
      }).catch(() => {});          // best effort: the worst case is both shapes
    };
    ping();
    const t = setInterval(ping, 2500);
    return () => {
      alive = false;
      clearInterval(t);
      // Say so immediately rather than waiting out the deadline: a discard
      // should put the picture back at once, and a save has new geometry to
      // draw. (The save route clears it too -- this covers every other way
      // an edit ends.)
      api.post("/api/detector/control/zones/editing", { editing: false })
        .catch(() => {});
    };
  }, [dirty, canSave]);

  function discard() {
    // No confirm: Ctrl+Z brings it all back now, so this is recoverable rather
    // than final -- and a dialog on a recoverable action is noise.
    history.push();
    loadStatus(true);
    setSelectedId(null);
  }

  function patchZone(id, fields) {
    history.push();
    setZones((cur) => cur.map((z) => (z.id === id ? { ...z, ...fields } : z)));
    markDirty(id);
  }

  function finishDraft(pts, isLine) {
    if (isLine && pts.length !== 2) return toast.error("A line needs 2 points");
    if (!isLine && pts.length < 3) return toast.error("Draw at least 3 points");
    const zone = {
      id: `zone_${Date.now().toString(36)}`,
      name: `${isLine ? "Line" : "Zone"} ${zones.length + 1}`,
      kind: isLine ? "line" : "area",
      points: pts,
      violation: false,
      enabled: true,
      // A NEW area counts an object whose box touches it at all. Feet-in-zone
      // is the stricter reading and stays available (and stays what every zone
      // already drawn does -- this is a default on creation, not a change of
      // meaning for the blank value): it needs the floor under the object to
      // be visible and inside the polygon, so a zone drawn around a machine at
      // chest height, or one on a camera looking slightly down, counts nothing
      // and gives no hint why. Touching is the reading an operator expects
      // from "is it in this area".
      // A LINE is a crossing, not an occupancy: its own touch/cross setting is
      // a different control, so nothing is stamped here.
      ...(isLine ? {} : { match: "touch" }),
      color: ZONE_PALETTE[zones.length % ZONE_PALETTE.length],
      labels: [],
      // Which view it was drawn on. Points are normalised per frame, so a zone
      // only means anything against the camera it came from. Controlled mode
      // (the host page's rail picks the camera) stamps that choice instead.
      ...((controlled ? cameraProp : zoneCam) == null
            ? {} : { camera: controlled ? cameraProp : zoneCam }),
    };
    history.push();
    setZones([...zones, zone]);
    setDraft([]); setDrawMode(null); setSelectedId(zone.id); markDirty(zone.id);
  }

  function duplicateZone(z) {
    const copy = {
      ...z,
      id: `zone_${Date.now().toString(36)}`,
      name: `${z.name} copy`,
      points: z.points.map(([x, y]) => [Math.min(1, x + 0.02), Math.min(1, y + 0.02)]),
    };
    history.push();
    setZones([...zones, copy]);
    setSelectedId(copy.id); markDirty(copy.id);
  }

  async function deleteZone(z) {
    const used = zoneRefs[z.id] || 0;
    if (used > 0 && !await confirm({
      title: `Delete "${z.name}"?`,
      description: `It is used by ${used} workflow node${used > 1 ? "s" : ""}. `
        + "Deleting it breaks those analytics until you rewire them.",
      destructive: true })) return;
    history.push();
    setZones(zones.filter((x) => x.id !== z.id));
    if (selectedId === z.id) setSelectedId(null);
    // The deletion itself is the unsaved change, and the zone it belongs to is
    // gone - so it lands on the page's Save all, not on a card's button.
    markDirty(`deleted:${z.id}`);
  }

  if (error) return <ErrorNote error={error} />;
  // Undecided run state holds the skeleton: flashing the stopped editor
  // over a running system reads as an outage that is not happening.
  if (!status || steadyRunning === null) return <SkelEditor />;
  const running = steadyRunning;
  // Alive, but the loop has not written a heartbeat lately: it is loading a
  // model or reopening a camera that dropped. It comes back on its own, so it
  // must not read as "stopped" with a Start button under it -- that invites an
  // operator to start an app that is already running.
  const stalled = !!status.stalled;
  // Stopped by the BOX because no camera of the system is there
  // (app/camwatch.py): not busy, not the user's Stop. It says so, and that
  // it comes back by itself, instead of the "busy" card.
  const waitingCam = status.ai_paused === "camera_missing";

  const camList = camListOf(status);
  const multiCam = camList.length > 1;
  const firstCamId = camList.length ? camList[0].id : null;
  // A zone with no `camera` belongs to the first one - the backend's default.
  const zoneCamOf = (z) => (z.camera == null ? firstCamId : z.camera);
  const shownCam = controlled
    ? (cameraProp == null ? firstCamId : cameraProp)
    : (zoneCam == null ? firstCamId : zoneCam);
  const activeCam = camList.find((c) => String(c.id) === String(shownCam)) || camList[0] || null;
  // The camera the FEED asks for survives a blip: while an app restarts (or
  // an errored camera re-keys the list) the camera list can come back empty
  // for a poll or two, which nulled shownCam -- and a null camera meant the
  // BARE preview path, which serves the app's FIRST camera. The picture then
  // silently swapped to a different camera ("it went to the next video by
  // itself"). Remembering the last shown id keeps the viewer pointed at the
  // camera the operator was actually watching until the list returns.
  if (shownCam != null) lastShownRef.current = shownCam;
  const feedCam = shownCam ?? lastShownRef.current;
  // Which camera the editing hint should scope itself to. A ref because the
  // hint's heartbeat is bound above this line (hooks run before the loading
  // guards), and a viewer on ANOTHER camera of the same system must keep its
  // outlines while this one is being edited.
  feedCamRef.current = feedCam;
  // The pill's state half, in the same priority the camera rail uses
  // (Camera Setup's Detection.jsx): inactive and error both override running,
  // because neither is fixed by pressing Start - an operator watching "Live"
  // over a camera that is switched off or faulted is the thing this reports.
  const camOff = activeCam ? activeCam.enabled === false : false;
  const camError = activeCam ? activeCam.error : null;
  const feedState = camOff
    ? { label: "Inactive", bg: "rgba(148,163,184,.14)", color: "var(--muted-text, #94a3b8)" }
    : camError
    ? { label: "Error", bg: "rgba(220,38,38,.14)", color: "var(--alert, #dc2626)" }
    : running
    ? (stalled
        ? { label: "Busy", bg: "rgba(245,158,11,.14)", color: "var(--warn, #d97706)" }
        // "Live" is the PICTURE being on screen, nothing less. The pill used
        // to say it the moment the app ran, over a box that was still on the
        // loader -- or that would never leave it.
        : feedFailed
        ? { label: "No video", bg: "rgba(220,38,38,.14)", color: "var(--alert, #dc2626)" }
        : feedLive
        ? { label: "Live", bg: "rgba(35,197,82,.14)", color: "var(--ok, #16a34a)" }
        : { label: "Connecting…", bg: "rgba(245,158,11,.14)", color: "var(--warn, #d97706)" })
    : { label: "Stopped", bg: "rgba(148,163,184,.14)", color: "var(--muted-text, #94a3b8)" };
  // Only THIS camera's zones are drawn and listed. `zones` still holds every
  // camera's - the save writes the whole list, so the hidden ones survive.
  const visibleZones = multiCam
    ? zones.filter((z) => String(zoneCamOf(z)) === String(shownCam))
    : zones;
  /** The four drawing tools, in the feed's header beside the menu button.
   *  A component only so the header row stays readable; it closes over the
   *  same draw state the canvas uses, so there is one armed tool, not two. */
  const ShapeTools = () => (
    <>
      {/* Icons ride as ELEMENTS: a component held in a destructured local
          reads as an undefined reference to the JSX checker, and a blank
          page is not worth the tidier map. */}
      {[["box", "Box", <SquareDashed size={14} />],
              ["circle", "Circle", <CircleIcon size={14} />],
              ["area", "Polygon", <Pentagon size={14} />],
              ["line", "Line", <Spline size={14} />]]
              .map(([mode, label, glyph]) => {
                const on = drawMode === mode;
                return (
                  <button key={mode} className="btn btn-sm"
                          disabled={!editable || camOff
                                    || !can("detection.zone.add")}
                          title={camOff
                            ? "This camera is inactive - switch it on to draw zones"
                            : !can("detection.zone.add")
                            ? "New zones are the box owner's - you can adjust the ones here"
                            : editable ? `Draw a ${label.toLowerCase()}`
                                       : "Read-only"}
                          onClick={() => { setDraft([]);
                                           setDrawMode(on ? null : mode); }}
                          style={{ display: "inline-flex", alignItems: "center",
                                   gap: 6,
                                   ...(on ? { borderColor: "var(--accent, #04bade)",
                                              color: "var(--accent-text, #04bade)",
                                              background: "rgba(4,186,222,.10)" }
                                          : {}) }}>
                    {glyph} {label}
                  </button>
                );
              })}
    </>
  );

  const camDims = activeCam || status.camera || { width: 640, height: 480 };

  return (
    <div>
      {unsavedDialog}
      {/* The panel's own toolbar: the host page's quick controls (Model /
          Camera / Capture) on the left, Save/Discard/undo on the right --
          one row, no dead space. */}
      {/* The panel's quick controls, with the feed's own state at the head of
          the row: "Live" belongs against the model and camera it describes,
          not stranded over the picture in a second header. */}
      <div className="filter-bar" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        <span title={camError ? camErrorText(camError) : undefined}
              style={{ display: "inline-flex", alignItems: "stretch",
                       border: "1px solid var(--line, rgba(128,128,128,.28))",
                       borderRadius: 9, overflow: "hidden", fontSize: 12 }}>
          <span style={{ padding: "5px 10px", fontWeight: 600 }}>Live View</span>
          <span style={{ padding: "5px 10px", fontWeight: 600,
                         borderLeft: "1px solid var(--line, rgba(128,128,128,.28))",
                         background: feedState.bg, color: feedState.color }}>
            {feedState.label}
          </span>
        </span>
        {toolbar}
        <div style={{ flex: 1 }} />
        {dirty && <Badge tone="warn">unsaved changes</Badge>}
      </div>
      {/* Whatever the host page has to say about this feed -- a camera that
          has stopped answering, a system with no camera chosen yet. BELOW the
          row that names the camera and its state, because that row is the
          subject and the notice is what happened to it; above the row it read
          as a page-level banner about something further down. */}
      {notice}
      {/* Undo / Discard / Save all belong with the page's OWN actions, beside
          the button that leaves for the Workflow -- they are what you do
          before leaving. Portalled rather than lifted: the dirty set, the
          history and the save all live in this panel, and threading six
          pieces of state up to the page just to place three buttons would
          put the state further from the thing it describes. */}
      <PageActions>
        <UndoRedo canUndo={history.canUndo} canRedo={history.canRedo}
                  onUndo={history.undo} onRedo={history.redo} />
        <button className="btn" disabled={!dirty || busy} onClick={discard}>
          Discard
        </button>
        <button className="btn btn-accent" disabled={!canSave || !dirty || busy}
                title="Save every zone (Ctrl+S)"
                onClick={() => saveZones()}>
          {busy ? "Saving…" : "Save all"}
        </button>
      </PageActions>

      {/* A greyed-out page with no reason given reads as broken. Say why, and
          say where to fix it. Amber, not red: nothing is wrong or destroyed --
          the camera is simply not running yet. */}
      {running && waitingCam && (
        <div className="panel-card" data-state="camera-missing" role="status"
             style={{ borderColor: "var(--alert, #dc2626)" }}>
          <div className="section-title">Detection stopped: camera not found.</div>
          <p className="muted small" style={{ margin: 0 }}>
            It starts again by itself when the camera is plugged back in.
          </p>
        </div>
      )}
      {running && stalled && !waitingCam && (
        <div className="panel-card" style={{ borderColor: "var(--warn)" }}>
          <div className="section-title">Detection is busy</div>
          <p className="muted small" style={{ margin: 0 }}>
            The app is running but has not produced a frame for a moment -
            usually loading a model or reopening a camera that dropped. The
            picture comes back on its own; nothing needs restarting.
          </p>
        </div>
      )}

      {/* No "go start detection" banner: the Start button lives on THIS
          page's Camera view header now. The stopped placeholder inside the
          feed box says the rest. */}

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "minmax(0,2fr) minmax(300px,1fr)" }}>
        {/* ---- drawing surface over the live feed ---- */}
        <div className="panel-card">
          {/* A segmented pill, not a title with a badge stuck to it: the two
              halves are one statement -- what this box IS, and what it is
              doing -- and the state half carries the colour so it reads from
              across the room. */}
          <div style={{ display: "flex", alignItems: "center", gap: 8,
                        marginBottom: 10 }}>
            {/* The status box, folded behind one button to the LEFT of the
                pill. It was a permanent block under the drawing toolbar,
                which spent the picture's vertical room on numbers that are
                only read when something looks wrong. Behind a button it is
                one click away and costs nothing while the feed is fine. */}
            {underFeed && <FeedMenu>{underFeed}</FeedMenu>}
            {/* The drawing tools, beside the menu button. They were a strip
                UNDER the picture, which put the thing you pick a tool with
                below the thing you draw on -- and pushed the feed down by a
                row that is only used while drawing. */}
            <ShapeTools />
            <span style={{ flex: 1 }} />
            {actions}
          </div>
          {/* One system, several views: zones are drawn per camera, so the
              editor has to say which one it is drawing on. A single-camera box
              never sees this row -- nor does a CONTROLLED panel, whose host
              page's camera rail is the switcher. */}
          {!controlled && multiCam && (
            <div className="filter-bar" style={{ marginBottom: 8, flexWrap: "wrap" }}>
              {camList.map((c) => (
                <button key={c.id}
                        className={"btn btn-sm" + (String(c.id) === String(shownCam) ? " btn-accent" : "")}
                        title={c.source || ""}
                        onClick={() => {
                          setZoneCam(c.id);
                          setDraft([]); setDrawMode(null); setSelectedId(null);
                          setFeedAspect(null);
                          setFeedLive(false); setFeedFailed(false);
                          feedRetries.current = 0;
                          setFeedKey((k) => k + 1);
                        }}>
                  {camSlot(camList, c.id)}
                </button>
              ))}
            </div>
          )}
          {/* The box must be EXACTLY the frame, because the drawing overlay is
              stretched across it: sizing it from the configured resolution
              (4:3) while the camera sends 16:9 letterboxed the image inside a
              taller box, and every zone drawn over those black bars landed
              somewhere else once the engine applied it to the real frame. So
              the image drives the height, and the configured ratio is only a
              placeholder for when detection is stopped and there is no frame
              to measure. */}
          <div style={{ position: "relative", background: "#000", borderRadius: 8,
                        overflow: "hidden",
                        // ...and only while that frame IS there. A remount
                        // (retry, depth toggle) leaves no img for a moment;
                        // without a ratio the box collapsed to a sliver and
                        // the loader centred in it sat cut off at the top.
                        ...(running && feedAspect && feedLive
                              ? {}
                              : { aspectRatio: feedAspect
                                    ? `${feedAspect}`
                                    : `${camDims.width || 640} / ${camDims.height || 480}` }) }}>
            {/* Depth / colour, on a depth camera. The false-colour picture
                is how a person checks what the depth actually sees: is the
                floor even, is that dark carton invisible to the stereo, why
                does a lane read "no depth". Boxes and zones paint on both. */}
            {running && !camOff && activeCam?.kind === "depth" && (
              <button type="button" className={"btn btn-sm" + (depthView ? " btn-accent" : "")}
                      style={{ position: "absolute", top: 8, right: 8, zIndex: 3 }}
                      title={depthView ? "Back to the camera's picture"
                                       : "Show the depth picture (red near, blue far, black no reading)"}
                      onClick={() => {
                        setDepthView((v) => !v);
                        // A new stream: the loader until ITS first frame.
                        setFeedLive(false); setFeedFailed(false);
                        feedRetries.current = 0;
                        setFeedKey((k) => k + 1);
                      }}>
                {depthView ? "Colour view" : "Depth view"}
              </button>
            )}
            {running && !camOff && feedFailed ? (
              // Gave up (see the retry effect). The player is NOT mounted
              // here: nothing is connecting, retrying or holding one of the
              // browser's few sockets while this card is up. The camera
              // itself is the app's business and is usually fine -- the
              // sentence says so, and points at the one thing that works.
              <div style={{ position: "absolute", inset: 0, display: "grid",
                            placeItems: "center", padding: 16 }}
                   role="alert" data-feed-failed="1">
                <div style={{ textAlign: "center", display: "grid",
                              justifyItems: "center", gap: 10 }}>
                  <span style={{ width: 62, height: 62, borderRadius: 999,
                                 display: "grid", placeItems: "center",
                                 border: "1px solid var(--alert, #dc2626)66",
                                 background: "rgba(220,38,38,.08)",
                                 color: "var(--alert, #dc2626)" }}>
                    <VideoOff size={24} />
                  </span>
                  <span style={{ fontSize: 15, color: "var(--text, #e8eaed)" }}>
                    No video from {activeCam ? camSlot(camList, activeCam.id) : "this camera"}
                  </span>
                  <span className="muted small">
                    Detection is running, but no picture reached this page.
                    The stream may be blocked or the box may be out of video
                    connections - Retry opens a fresh one.
                  </span>
                  <button type="button" className="btn btn-sm" onClick={retryFeed}>
                    Retry
                  </button>
                </div>
              </div>
            ) : running && !camOff ? (
              // Full quality here on purpose: this is the page the operator
              // draws zones on, so a downscaled frame would cost accuracy.
              // StreamImg owns the reconnect timer and drops the connection
              // when the editor scrolls away or the tab is backgrounded.
              // feedCam, not shownCam: pinned through list blips -- see
              // where feedCam is derived.
              <WebRTCVideo
                key={feedKey}
                camera={feedCam == null ? undefined : feedCam}
                mjpegOnly={depthView && activeCam?.kind === "depth"}
                mjpegSrc={(feedCam == null
                       ? apiUrl(`/api/detector/preview?k=${feedKey}`)
                       : apiUrl(`/api/detector/preview?camera=${encodeURIComponent(feedCam)}&k=${feedKey}`))
                       + (depthView && activeCam?.kind === "depth" ? "&view=depth" : "")}
                alt="live feed"
                onLoad={(e) => {
                  const { naturalWidth: w, naturalHeight: h } = e.target;
                  if (w && h) setFeedAspect(w / h);
                  setFeedLive(true);
                }}
                // The picture went (an img error, or WebRTC dropping to the
                // img mid-stream): the loader back until the next frame.
                onError={() => setFeedLive(false)}
                // The CameraLoader further down is the ONE loader for this
                // box. The player's default ring would be a second one,
                // drawn in its own wrapper (no height yet), cut off at the
                // top edge -- Tok Aba's "two spinners" on Camera Setup.
                placeholder={null}
                style={{ width: "100%" }}
                imgStyle={{ width: "100%", height: "auto", display: "block" }} />
            ) : (
              // Stopped is a STATE, not an error: an icon, a line that says
              // what is happening and a line that says what to do about it,
              // on a faint grid so the box reads as a viewport waiting for a
              // picture rather than as a hole.
              <div style={{ position: "absolute", inset: 0, display: "grid",
                            placeItems: "center", padding: 16,
                            backgroundImage:
                              "radial-gradient(rgba(148,163,184,.16) 1px, transparent 1px)",
                            backgroundSize: "22px 22px" }}>
                <div style={{ textAlign: "center", display: "grid",
                              justifyItems: "center", gap: 10 }}>
                  <span style={{ width: 62, height: 62, borderRadius: 999,
                                 display: "grid", placeItems: "center",
                                 border: "1px solid var(--accent, #04bade)44",
                                 background: "rgba(4,186,222,.06)",
                                 color: "var(--accent-text, #04bade)" }}>
                    <VideoOff size={24} />
                  </span>
                  <span style={{ fontSize: 15, color: "var(--text, #e8eaed)" }}>
                    {camOff ? "Camera inactive"
                      : hostStopping ? "Stopping detection…" : "Detection is stopped"}
                  </span>
                  <span className="muted small">
                    {camOff
                      ? "Switch it on to see the feed and draw zones - its zones and settings are kept"
                      : hostStopping
                      ? "Releasing the camera and the AI chip - Start comes back when that is done"
                      : "Press Start Detection above to see the feed and draw zones"}
                  </span>
                  {/* No Retry button. Neither state it appears in is a
                      failure to retry: detection is stopped until somebody
                      presses Start, and a switched-off camera is off until
                      somebody switches it on. A button that cannot change
                      either invites a click that does nothing and reads as
                      a broken control. Both lines above already say the one
                      thing that does work. */}
                </div>
              </div>
            )}
            {/* Zones are drawn ON the picture. With detection stopped there
                is no picture, so an overlay floating on a black rectangle
                shows a shape at coordinates nothing can be checked against
                -- and invites drawing one that lands somewhere else once the
                feed returns. The zones are safe in the list beside it; they
                reappear with the frame they belong to. */}
            {/* ...and a camera switched OFF is the same case: the picture is
                not there, so a bright outline over the placeholder reads as a
                live zone being watched and says nothing about where it
                actually sits. Listed in the sidebar either way -- kept, just
                not drawn on nothing. */}
            {running && !camOff && !feedFailed && (
            <ZoneCanvas zones={visibleZones}
                        drawMode={drawMode} draft={draft} onDraft={setDraft}
                        onFinish={finishDraft} editable={editable && !camOff}
                        selectedId={selectedId} onSelect={setSelectedId}
                        onEdit={(id, points) => {
                          // ONE undo step per drag, not one per pointermove:
                          // pushed on the first move and not again until the
                          // drag ends, so Ctrl+Z rewinds the whole gesture
                          // rather than a pixel of it.
                          if (!dragRef.current) { dragRef.current = true; history.push(); }
                          setZones((cur) => cur.map((z) => (
                            z.id === id ? { ...z, points } : z)));
                          markDirty(id);
                        }}
                        onEditEnd={(id) => {
                          dragRef.current = false;
                          markDirty(id || selectedId);
                        }} />
            )}
            {/* A just-added camera takes seconds to open. Say so instead of
                flashing a broken image. pointer-events off so zones can still
                be drawn on the placeholder box meanwhile.

                A camera switched OFF is a different sentence entirely. It is
                not connecting and never will be -- the app closes its device
                and leaves it closed -- so "opening the video stream…" was a
                promise the box had no intention of keeping, sitting there for
                as long as anyone cared to watch. */}
            {/* Only for a feed that IS coming. An inactive camera is answered
                by the placeholder above -- a translucent "connecting" sheet
                over it would be two messages about the same thing, one of
                them wrong.

                The shared CameraLoader, so the wait looks the same here as on
                the Detection page and in a dashboard tile. The sentence is
                kept as its label: it is a real distinction (this camera IS
                opening, unlike a switched-off one) and a screen reader still
                hears it. */}
            {running && !camOff && !feedLive && !feedFailed && (
              <CameraLoader label="Camera connecting - opening the video stream…"
                            dim />
            )}
          </div>

          {/* The shape tools as a TOOLBAR: one strip under the picture, the
              chosen tool held down rather than replaced by a sentence, so it
              is always visible which tool is armed and pressing it again is
              how you put it back. The instruction rides beside them. */}
          {/* The instruction for the armed tool stays with the PICTURE --
              it is about what to do on the image. The tool buttons moved to
              the header beside the menu. */}
          {/* Only while a tool is armed. The bar used to render unconditionally
              with its contents gated, so with no tool chosen -- which is most
              of the time -- an empty strip with its own margin and border sat
              under the picture looking like a card that had failed to load. */}
          {drawMode && (
            <div className="filter-bar"
                 style={{ marginTop: 10, flexWrap: "wrap", gap: 6 }}>
              <span className="muted small" style={{ marginLeft: 4 }}>
                {drawMode === "line" ? "Click 2 points for the line"
                  : drawMode === "box" ? "Drag a rectangle on the feed"
                  : drawMode === "circle" ? "Drag from the centre outward"
                  : `Click points; Finish when done · ${draft.length} placed`}
              </span>
              {drawMode === "area" && (
                <button className="btn btn-sm btn-accent"
                        onClick={() => finishDraft(buildPoints("area", draft), false)}>Finish</button>
              )}
              <button className="btn btn-sm"
                      onClick={() => { setDraft([]); setDrawMode(null); }}>Cancel</button>
            </div>
          )}
          {/* `underFeed` used to render here. It is in the header menu now --
              see FeedMenu above. */}
        </div>

        {/* ---- sidebar: the zone list, then the Overlay rules, then
            whatever the host page adds (Model / Camera / Capture) -- one
            column, one row with the image. ---- */}
        <div style={{ display: "grid", gap: 16, alignContent: "start", minWidth: 0 }}>
        {/* Which camera, directly above what is drawn on it: picking the
            camera and listing that camera's zones are one thought, and the
            zone list already says "on <camera>" -- the switcher for it
            belongs against that sentence, not across the page. */}
        {sidebarTop}
        {/* ---- zone list ----
            Clicking the panel's own background deselects, which collapses the
            open card. Without it the only way to close one was to leave the
            page and come back. Each card stops its own click, so selecting a
            different zone still works. */}
        <div className="panel-card" style={{ alignSelf: "start" }}
             onClick={() => setSelectedId(null)}>
          <div className="section-title">
            Zones ({visibleZones.length})
            {multiCam && <> <span className="muted small">on {camSlot(camList, activeCam?.id)}</span></>}
          </div>
          {visibleZones.length === 0 && (
            <p className="muted small">
              No zones {multiCam ? `on ${camSlot(camList, activeCam?.id)} ` : ""}yet. Draw one on the feed.
            </p>
          )}
          {visibleZones.map((z) => {
            const sel = z.id === selectedId;
            const used = zoneRefs[z.id] || 0;
            // Collapsed by default: one compact row per zone, opened by
            // clicking it here OR by clicking the shape on the video feed
            // (ZoneCanvas onSelect drives the same selectedId). Only the
            // chosen zone spends the space of its controls.
            if (!sel) {
              return (
                <div key={z.id}
                     onClick={(e) => { e.stopPropagation(); setSelectedId(z.id); }}
                     style={{ display: "flex", alignItems: "center", gap: 8,
                              border: "1px solid rgba(128,128,128,.2)",
                              borderRadius: 8, padding: "7px 10px", marginBottom: 6,
                              cursor: "pointer",
                              opacity: z.enabled === false ? 0.6 : 1 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 999,
                                 background: zoneColor(z), flex: "0 0 auto" }} />
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", fontSize: 13,
                                 textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {z.name}
                  </span>
                  <Badge tone="muted">{z.kind}</Badge>
                  {/* Enable, right on the row: opening the editor to flip a
                      zone was three clicks for a one-bit decision. This is
                      the SAVED state; a workflow Zone Switch can still
                      override it while the app runs. */}
                  <input type="checkbox" checked={z.enabled !== false}
                         disabled={!editable}
                         title={z.enabled !== false
                           ? "Enabled - counts and draws. Untick to disable."
                           : "Disabled - silent and hidden. Tick to enable."}
                         onClick={(e) => e.stopPropagation()}
                         onChange={(e) => patchZone(z.id, { enabled: e.target.checked })} />
                </div>
              );
            }
            return (
              <div key={z.id}
                   onClick={(e) => { e.stopPropagation(); setSelectedId(z.id); }}
                   style={{ border: "1px solid var(--accent)",
                            borderRadius: 8, padding: 10, marginBottom: 8, cursor: "pointer",
                            opacity: z.enabled === false ? 0.6 : 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input type="color" value={zoneColor(z)} disabled={!editable}
                         onClick={(e) => e.stopPropagation()}
                         onChange={(e) => patchZone(z.id, { color: e.target.value })}
                         title="Zone colour"
                         style={{ width: 26, height: 22, padding: 0, border: "none", background: "none" }} />
                  <input className="input" value={z.name} disabled={!editable}
                         onClick={(e) => e.stopPropagation()}
                         onChange={(e) => patchZone(z.id, { name: e.target.value })}
                         style={{ flex: 1, minWidth: 0 }} />
                  <Badge tone="muted">{z.kind}</Badge>
                  {multiCam && (
                    <Badge tone="muted">
                      {camSlot(camList, zoneCamOf(z))}
                    </Badge>
                  )}
                </div>
                {/* Lines detect too now -- "at the line" is a narrow band
                    around it -- so they get the same picker, with the two
                    anchor choices that mean something for a band. */}
                <label className="muted small" style={{ display: "flex", alignItems: "center",
                                                        gap: 6, marginTop: 8 }}
                       onClick={(e) => e.stopPropagation()}>
                  Detected when
                  <select className="input" style={{ flex: 1 }} disabled={!editable}
                          value={z.match || ""}
                          onChange={(e) => patchZone(z.id, { match: e.target.value })}>
                    {z.kind === "area" ? (
                      <>
                        {/* The blank entry is what a zone drawn before this
                            carries, and it still counts feet-in-zone: changing
                            what BLANK means would quietly renumber every
                            deployment in the field. New zones are stamped
                            `touch` when they are drawn, so this list opens on
                            "box touches zone" for anything made from now on. */}
                        <option value="">unset - feet in zone</option>
                        <option value="touch">box touches zone (default)</option>
                        <option value="feet">feet in zone</option>
                        <option value="center">middle point in zone</option>
                        <option value="full">whole box inside</option>
                      </>
                    ) : (
                      <>
                        <option value="">feet at the line (default)</option>
                        <option value="feet">feet at the line</option>
                        <option value="center">middle point at the line</option>
                        <option value="touch">box touches the line</option>
                      </>
                    )}
                  </select>
                </label>
                {/* The third axis. A zone over a DEPTH camera can also say how
                    near: an object counts only between these two distances
                    from the camera. Blank = no bound. A person whose box reads
                    no depth still counts -- the range narrows a zone, it never
                    blinds it (zones.py Zone.in_range). */}
                {camList.find((c) => String(c.id) === String(zoneCamOf(z)))?.kind === "depth" && (
                  <div className="muted small"
                       style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8,
                                flexWrap: "wrap" }}
                       onClick={(e) => e.stopPropagation()}>
                    <span title="Only objects this far from the camera count - the depth camera's third axis">
                      Depth range
                    </span>
                    <input className="input" type="number" min={0} max={20000} step={50}
                           style={{ width: 88 }} placeholder="near mm" disabled={!editable}
                           value={z.depth_min_mm || ""}
                           onChange={(e) => patchZone(z.id, {
                             depth_min_mm: Math.max(0, Math.min(20000, Number(e.target.value) || 0)) })} />
                    <span>to</span>
                    <input className="input" type="number" min={0} max={20000} step={50}
                           style={{ width: 88 }} placeholder="far mm" disabled={!editable}
                           value={z.depth_max_mm || ""}
                           onChange={(e) => patchZone(z.id, {
                             depth_max_mm: Math.max(0, Math.min(20000, Number(e.target.value) || 0)) })} />
                    <span>mm from the camera</span>
                  </div>
                )}
                {/* A line either HAS a face or does not. With one, the side an
                    object came from decides in vs out; without one, a crossing
                    is just a crossing -- which is the honest answer for a line
                    across a corridor where "in" was never agreed. The arrow on
                    the feed shows which side is IN, and Flip swaps it without
                    redrawing the line backwards. */}
                {z.kind === "line" && (
                  <div style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
                    <label className="muted small"
                           style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      Line type
                      <select className="input" style={{ flex: 1 }} disabled={!editable}
                              value={(z.facing || "forward") === "none" ? "none" : "faced"}
                              onChange={(e) => patchZone(z.id, {
                                facing: e.target.value === "none" ? "none" : "forward",
                              })}>
                        <option value="none">Normal line - count every crossing</option>
                        <option value="faced">Line with face - in one way, out the other</option>
                      </select>
                    </label>
                    {(z.facing || "forward") !== "none" && (
                      <div className="muted small"
                           style={{ display: "flex", alignItems: "center", gap: 6,
                                    marginTop: 6 }}>
                        <span style={{ flex: 1 }}>
                          The arrow on the feed points at the IN side.
                        </span>
                        <button className="btn btn-sm" disabled={!editable}
                                title="Swap which side counts as IN"
                                onClick={() => patchZone(z.id, {
                                  facing: z.facing === "reverse" ? "forward" : "reverse",
                                })}>
                          Flip direction
                        </button>
                      </div>
                    )}
                    {/* How far past the line an object must move before it has
                        crossed. Reported from a live door: one person, an IN
                        and an OUT, because the tracked box shivers wider than
                        the fixed band on that camera. It is a THRESHOLD (a
                        distance), never "sensitivity": the two are inverses,
                        and an installer told to raise the sensitivity must not
                        get a thicker band. The 0.2 / 1.2 / 5 literals mirror
                        zones.py LINE_BAND_PCT_MIN / LINE_DEADBAND /
                        LINE_BAND_PCT_MAX -- this page has no registry feed
                        for zone fields -- and smoke_lineband pins them.
                        Clamped on BLUR, not on every keystroke: snapping "0"
                        to the floor while it is being typed makes 0.5
                        impossible to enter, and the validator clamps
                        whatever reaches it regardless.

                        The box always shows the number the line is ACTUALLY
                        deciding with, never an empty box over a placeholder.
                        The board stores nothing when the value is the factory
                        1.2, so an empty box was the honest reading of the
                        document and the useless one on the page: type 1.2,
                        press Save, and the field goes blank again, which is
                        indistinguishable from a save that failed -- reported
                        as exactly that from a live board on 2026-09-24. What
                        is stored is still nothing; what is shown is what
                        applies. */}
                    <div className="muted small"
                         style={{ display: "flex", alignItems: "center", gap: 6,
                                  marginTop: 6, flexWrap: "wrap" }}
                         onClick={(e) => e.stopPropagation()}>
                      <span title="How far past the line an object must move before it counts as having crossed, as a percentage of the picture width. Raise it if one person hovering on the line is counted in and out; lower it if short steps over the line are missed. 1.2 is the factory setting - about 23 px on a 1920-wide picture. Two frames on the far side are always required.">
                        Crossing threshold
                      </span>
                      <input className="input" type="number" min={0.2} max={5} step={0.1}
                             style={{ width: 72 }} placeholder="1.2" disabled={!editable}
                             value={z.band_pct ?? (bandEdit[z.id] === "" ? "" : 1.2)}
                             // The box is never empty now, so a click puts a
                             // CURSOR in "1.2" and the next digit is appended:
                             // typing 3 to mean 3 gives 1.23, which rounds
                             // straight back to 1.2 on blur, and the setting
                             // looks stuck at the factory value however many
                             // times it is typed. Reported as exactly that.
                             // Selecting on focus makes the first keystroke
                             // replace the value, which is what typing into a
                             // filled-in setting is expected to do.
                             onFocus={(e) => e.target.select()}
                             onChange={(e) => {
                               // A cleared box is a state the operator passes
                               // THROUGH on the way to a new number, so it is
                               // remembered as their editing state rather than
                               // snapping back to 1.2 under the cursor.
                               setBandEdit((cur) => ({ ...cur, [z.id]: e.target.value }));
                               patchZone(z.id, {
                                 band_pct: e.target.value === "" ? null : Number(e.target.value) });
                             }}
                             onBlur={() => {
                               setBandEdit((cur) => {
                                 const next = { ...cur }; delete next[z.id]; return next;
                               });
                               const v = Number(z.band_pct);
                               const next = !(v > 0) ? null
                                 : Math.round(Math.max(0.2, Math.min(5, v)) * 10) / 10;
                               // patchZone marks the page dirty; leaving an
                               // untouched box must not.
                               if (next !== (z.band_pct ?? null)) patchZone(z.id, { band_pct: next });
                             }} />
                      <span>% of picture width</span>
                    </div>
                  </div>
                )}
                {/* What a zone DOES -- counting, alerts, timers -- lives on
                    the Workflow canvas. The per-zone actions that used to
                    unfold here duplicated that whole page in miniature, so a
                    zone card is now just the definition: name, colour,
                    count-when and the enabled switch. */}
                <div className="filter-bar" style={{ marginTop: 8, flexWrap: "wrap" }}>
                  <label className="muted small" style={{ display: "flex", alignItems: "center", gap: 4 }}
                         onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={z.enabled !== false} disabled={!editable}
                           onChange={(e) => patchZone(z.id, { enabled: e.target.checked })} />
                    enabled
                  </label>
                  <div style={{ flex: 1 }} />
                  {/* Enabled only when THIS zone has unsaved edits - editing
                      zone 2 must not light up zone 1's button. (The write
                      itself sends the whole list, because that is how the
                      server stores zones.) */}
                  <button className="btn btn-sm btn-accent"
                          disabled={!canSave || busy || !dirtyIds.has(z.id)}
                          title={dirtyIds.has(z.id) ? `Save ${z.name}`
                                                    : "No unsaved changes in this zone"}
                          onClick={(e) => { e.stopPropagation(); saveZones(z.id); }}>
                    {busy ? "Saving…" : "Save"}
                  </button>
                  {/* Duplicating IS drawing a new zone -- same permission, or
                      the rule would be "no new zones except that way". */}
                  {can("detection.zone.add") && (
                    <button className="btn btn-sm" disabled={!editable || busy}
                            onClick={(e) => { e.stopPropagation(); duplicateZone(z); }}>Duplicate</button>
                  )}
                  {can("detection.zone.delete") && (
                    <button className="btn btn-sm btn-danger" disabled={!editable || busy}
                            onClick={(e) => { e.stopPropagation(); deleteZone(z); }}>Delete</button>
                  )}
                </div>
                <div className="muted small" style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center" }}>
                  <code className="mono">{z.kind === "line" ? `line_${z.id}` : `zone_${z.id}`}</code>
                  <button className="btn btn-sm" title="Copy variable name"
                          onClick={(e) => {
                            e.stopPropagation();
                            navigator.clipboard?.writeText(z.kind === "line" ? `line_${z.id}` : `zone_${z.id}`);
                            toast.success("Copied");
                          }}>⧉</button>
                  {used > 0 && <Badge tone="ok">{used} workflow node{used > 1 ? "s" : ""}</Badge>}
                </div>
              </div>
            );
          })}
        </div>

        {sidebarExtra}
        </div>
      </div>
    </div>
  );
}
