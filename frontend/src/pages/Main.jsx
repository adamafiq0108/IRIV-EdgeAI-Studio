// Dashboard - the screen an operator watches all day.
//
// It renders a BUILDER PAGE ("main"), not a fixed layout: the live feed, the
// violation indicator and every value tile are ordinary widgets, so the feed
// can be resized, moved, or removed entirely on a board that does not want it.
// The first time a system is opened the page is seeded from its zones' actions
// (core/services/builder.py seed_main_widgets), so it is useful before anyone
// has dragged anything - and fully editable after.
//
// /api/dashboard assembles the run state, the computed widgets and the setup
// progress in one poll. One screen, one request: two polls for one page is how
// a UI starts contradicting itself.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { usePoll } from "../api/hooks.js";
import { useApp } from "../state/app.jsx";
import WidgetGrid from "../components/widgets/WidgetGrid.jsx";
import NextPageButton from "../components/NextPageButton.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { Loader2, Play, Square } from "lucide-react";

// The poll IS the visible latency of every verdict and tile. Measured on the
// board: the app rewrites its state every ~54ms (18fps) and /api/dashboard
// answers in ~60ms, so nothing upstream is slow -- what an operator sees as
// "the tile lags the video" is this interval, made obvious by a LIVE MJPEG
// picture sitting next to a polled number.
//
// 600ms: a person entering a zone shows within ~0.3s on average, and one
// viewer costs the master roughly a tenth of a core. Faster than this and
// the request rate starts to matter on a board that is also running
// inference for several systems; slower and the eye catches the gap.
const REFRESH_MS = 600;

export default function Main() {
  const { brand } = useApp();
  const toast = useToast();
  const { data, error, reload, setData } = usePoll("/api/dashboard", REFRESH_MS);
  // Which power action is in flight ("start" | "stop" | null) -- the button
  // disables on it, so a double-press cannot start two apps.
  const [busy, setBusy] = useState(null);
  // When Stop was pressed. The app takes seconds to actually wind down and
  // the poll would flip the page back to "running" meanwhile -- so for a
  // grace window the page believes the PRESS, not the poll.
  const [stopAt, setStopAt] = useState(0);
  const STOP_GRACE_MS = 8000;
  // Start holds while the app releases the camera and the chip (the
  // payload's `stopping`, whoever pressed Stop). Timed from when it was
  // first seen; past STOP_CONFIRM_MS the app is stuck, not stopping, and
  // Start comes back with a note rather than leaving no button at all.
  const STOP_CONFIRM_MS = 20000;
  const [stoppingSince, setStoppingSince] = useState(0);
  const reportedStopping = !!data?.stopping;
  useEffect(() => {
    setStoppingSince((cur) => (reportedStopping ? (cur || Date.now()) : 0));
  }, [reportedStopping]);
  const stoppingTimedOut = !!stoppingSince && Date.now() - stoppingSince > STOP_CONFIRM_MS;
  useEffect(() => {
    if (!stoppingTimedOut) return;
    toast.error("Detection has not confirmed it stopped",
                "The app did not release the camera and the AI chip within "
                + "20 seconds. Start is available again; if it fails, check "
                + "System Control.");
  }, [stoppingTimedOut, toast]);

  // The video tiles ask the master how to show video (WebRTC or MJPEG) and
  // open the overlay stream ONCE, when they mount. A page opened while
  // detection was stopped got "no video yet" answers and a stream that gave
  // up, and pressing Start changed none of it: only leaving the page and
  // coming back (a fresh mount) fetched the new answers. So when detection
  // goes from not ready to ready, the grid is remounted once, which is what
  // navigating away did by hand. `stalled` (loading a model) is not ready.
  const liveReady = !!data?.running && !data?.stalled;
  const wasReady = useRef(liveReady);
  const [liveEpoch, setLiveEpoch] = useState(0);
  useEffect(() => {
    if (liveReady && !wasReady.current) setLiveEpoch((n) => n + 1);
    wasReady.current = liveReady;
  }, [liveReady]);

  // The route only knows start and stop (core/routes/models.py
  // detector_power answers 400 to anything else), which is why the old
  // Restart arrow in this header is gone: it could only ever fail.
  function power(action) {
    // Stopping is believed at once, the same rule the Detection page follows:
    // the app is asked to quit and it quits. The button reads "Stopping..."
    // only while the request is in flight; the teardown itself is the
    // server's business, and holding the page hostage to it made "stop" feel
    // like "stall".
    if (action === "stop") {
      setBusy("stop");
      setStopAt(Date.now());
      setData((cur) => (cur ? { ...cur, running: false } : cur));
      api.post("/api/detector/control/power", { action })
        .then(() => toast.success("Detection stopped"))
        .catch((e) => toast.error("Could not stop detection", e.message))
        .finally(() => setBusy(null));
      return;
    }
    setBusy("start");
    setStopAt(0);
    api.post("/api/detector/control/power", { action })
      // No camera on the box: the switch is on but detection waits for the
      // camera, and the toast says so rather than "started".
      .then((d) => (d?.waiting
        ? toast.info("Waiting for the camera", d.message)
        : toast.success("Detection started")))
      .catch((e) => toast.error("Could not start detection", e.message))
      .finally(() => { setBusy(null); reload().catch(() => {}); });
  }

  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelPage title="Dashboard" cards={2} rows={4} />;

  const { stalled, widgets = [], setup = {} } = data;
  // Stopped by the BOX because no camera is there (app/camwatch.py). The
  // user's switch is still on, so the button stays Stop (Stop keeps it
  // stopped for good); the note beside it says it comes back by itself.
  const waitingCam = data.ai_paused === "camera_missing";
  const waitingText = "Detection stopped: camera not found. It starts again "
                    + "by itself when the camera is plugged back in.";
  const gridSize = data.grid_size || "medium";
  // Inside the grace window a polled running=true is the old app still dying,
  // not a change of mind.
  const running = data.running
    && !(stopAt && Date.now() - stopAt < STOP_GRACE_MS);
  const stopping = reportedStopping && !stoppingTimedOut;
  const accent = brand?.accent || "#04bade";
  // Stop while running OR stalled (both have an app to stop); a Stop still
  // in flight keeps its own button so "Stopping..." is what the eye sees.
  const showStop = busy === "stop" || (busy !== "start" && (running || stalled));
  // The route enforces detection.power (403 for an account without it).
  // When the payload says which capabilities the caller holds, an account
  // without this one sees the button disabled with the reason the server
  // would give, instead of a button that 403s.
  const mayPower = !Array.isArray(data.caps) || data.caps.includes("detection.power");
  const powerDenied = "Start and stop detection is not allowed on this account - "
                    + "the box owner sets that in Settings > Accounts.";

  // The four-step setup tour is GONE from this page. A system is created
  // from a template that carries its whole build now, so "wire the workflow
  // from scratch" stopped being anybody's first act -- and the card was
  // taking the whole dashboard over on systems that only lacked a camera.
  // The Camera Setup and Zones pages say what is missing where it is fixed.
  return (
    <section className="page active">
      <div className="page-head">
        {/* Just the title. Run state, model and fps are already burned into the
            feed's own HUD, and the Detection page owns them - repeating them
            here stacked four badges down the left of every dashboard. */}
        <div className="page-title">Dashboard</div>
        <div className="page-actions">
          {/* A system that is SET UP does not need to be sent to another
              page to press a button: detection is a thing it has, so the
              control is here -- ONE button that says what it will do,
              "Start" while detection is stopped and "Stop" while it runs,
              the same pair the Detection page header uses. A system still
              missing a camera or a model shows nothing here: the "Set up
              detection" link this used to fall back to was a bright accent
              button in the dashboard's header for a page the rail already
              names (Camera Setup), and the operator asked for it gone.

              `stalled` is alive but busy (loading a model, reopening a
              dropped camera): the button offers Stop there, never Start,
              because a second Start starts a SECOND app under the one
              already running. */}
          {setup.camera && setup.model && (showStop ? (
            <button type="button" className="btn btn-sm" data-power="stop"
                    disabled={!!busy || !mayPower}
                    title={mayPower ? "Stop detection" : powerDenied}
                    aria-label={busy === "stop" ? "Stopping detection" : "Stop detection"}
                    onClick={() => power("stop")}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6,
                             whiteSpace: "nowrap" }}>
              {busy === "stop" ? <Loader2 className="animate-spin" size={14} aria-hidden="true" />
                               : <Square size={13} aria-hidden="true" />}
              {busy === "stop" ? "Stopping…" : "Stop"}
            </button>
          ) : (
            <button type="button" className="btn btn-sm btn-accent" data-power="start"
                    disabled={!!busy || !mayPower || stopping}
                    title={!mayPower ? powerDenied
                           : stopping ? "Stopping - the camera and the AI chip are being released"
                           : "Start detection"}
                    aria-label={busy === "start" ? "Starting detection"
                                : stopping ? "Stopping detection" : "Start detection"}
                    onClick={() => power("start")}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6,
                             whiteSpace: "nowrap" }}>
              {busy === "start" || stopping
                ? <Loader2 className="animate-spin" size={14} aria-hidden="true" />
                : <Play size={13} aria-hidden="true" />}
              {busy === "start" ? "Starting…" : stopping ? "Stopping…" : "Start"}
            </button>
          ))}
          {running && waitingCam && (
            <span className="muted small" data-waiting="camera" role="status"
                  title={waitingText} aria-label={waitingText}
                  style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 6, height: 6, borderRadius: 999,
                             background: "#ef4444", display: "inline-block" }} />
              waiting for camera
            </span>
          )}
          {running && stalled && !waitingCam && (
            <span className="muted small" style={{ display: "inline-flex",
                                                   alignItems: "center", gap: 6 }}>
              <span className="size-1.5 animate-pulse rounded-full"
                    style={{ width: 6, height: 6, borderRadius: 999,
                             background: "#f59e0b", display: "inline-block" }} />
              detection busy…
            </span>
          )}
          {data.editable && <Link className="btn" to="/builder">Edit dashboard</Link>}
          <NextPageButton />
        </div>
      </div>


      <WidgetGrid
        key={liveEpoch}
        widgets={widgets}
        accent={accent}
        size={gridSize}
        // Each table card's Download CSV (the Dashboard has no page range).
        exportCsv={{ pageRange: null }}
        running={!!running}
        empty={
          <div className="empty">
            <p>
              This dashboard is empty. Add widgets in the{" "}
              <Link to="/builder">Dashboard Builder</Link> - anything your
              workflow publishes is there to drag in.
            </p>
          </div>
        }
      />
    </section>
  );
}
