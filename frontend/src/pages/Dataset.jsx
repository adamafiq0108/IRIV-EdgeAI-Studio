// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Dataset - capture training material from this system's camera: named photos
// and short clips, written to the NVMe drive (/mnt/nvme/dataset_image and
// /mnt/nvme/dataset_rec).
//
// Frames come from the app's RAW stream, so nothing is annotated: training
// data must not have boxes and HUD text burned into it. Clips are capped at
// 10s each - a dataset is many short samples, not surveillance footage.
//
// The whole page is gated on the NVMe actually being mounted and writable. No
// NVMe, no dataset capture: filling the eMMC with video is how a board
// dies, so the feature refuses rather than degrades.
//
// With more than one camera the operator picks which one is captured (and
// previewed); the choice is sent with every request and remembered per system
// by the server. One camera: no picker, nothing sent, the old behaviour.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { usePoll, useVisibleInterval } from "../api/hooks.js";
import LiveFeed from "../components/LiveFeed.jsx";
import { InfoHint } from "../components/ui/InfoHint.jsx";
import { ErrorNote, SkelEditor } from "../components/Loading.jsx";
import { Badge } from "../components/ui/primitives.jsx";
import NextPageButton from "../components/NextPageButton.jsx";
import PageGuideHost from "../workflow/PageGuides.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import NoStorage from "../components/NoStorage.jsx";
import DatasetCameraPicker from "../components/DatasetCameraPicker.jsx";
import { apiUrl } from "../lib/base.js";
import { cameraChoices, captureBody, initialCamera, showPicker }
  from "../lib/datasetCamera.js";

function sizeMB(bytes) {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
}

function when(mtime) {
  const d = new Date(mtime * 1000);
  return d.toLocaleString(undefined, { month: "short", day: "numeric",
                                       hour: "2-digit", minute: "2-digit" });
}

function FileList({ rows, kind, onOpen }) {
  if (!rows.length) return <p className="muted small">Nothing captured yet.</p>;
  return (
    <div style={{ maxHeight: 320, overflowY: "auto" }}>
      {rows.map((r) => (
        <div key={r.name} style={{ display: "flex", alignItems: "center", gap: 8,
                                   padding: "5px 0", borderBottom: "1px solid rgba(128,128,128,.12)" }}>
          <span className="mono" style={{ flex: 1, minWidth: 0, fontSize: 12,
                                          overflow: "hidden", textOverflow: "ellipsis",
                                          whiteSpace: "nowrap" }}>{r.name}</span>
          {r.camera_name && <span className="muted small">{r.camera_name}</span>}
          <span className="muted small">{sizeMB(r.size)}</span>
          <span className="muted small">{when(r.mtime)}</span>
          <button className="btn btn-sm" onClick={() => onOpen(kind, r.name)}>Open</button>
        </div>
      ))}
    </div>
  );
}

export default function Dataset() {
  const toast = useToast();
  const [name, setName] = useState("");
  const [duration, setDuration] = useState(5);
  // "manual" is start/stop -- the operator ends it when they have enough
  // samples. "fixed" records a set number of seconds and stops itself. Manual
  // is the default because how long a training clip should be is a judgement
  // about the subject, not a number this page can guess.
  const [mode, setMode] = useState("manual");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const { data, error, reload } = usePoll("/api/detector/dataset", 3000);

  const loadStatus = useCallback(() => {
    api.get("/api/detector/control")
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);
  useEffect(() => { loadStatus(); }, [loadStatus]);
  // Paused while the tab is hidden, refetched on return (useVisibleInterval).
  useVisibleInterval(loadStatus, 4000);

  // The chosen camera: starts at the one this system last captured from, and
  // starts over if that camera is removed while the page is open.
  const [cam, setCam] = useState(null);
  useEffect(() => {
    const cams = cameraChoices(data);
    if (!cams.length) return;
    if (cam === null || !cams.some((c) => c.id === cam)) setCam(initialCamera(data));
  }, [data, cam]);

  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelEditor />;

  const editable = status?.editable;
  const running = !!status?.running;
  const recording = data.recording && !data.recording.error;
  const maxClip = data.max_clip_s || 10;
  // Poll-driven, so the timer ticks in 3s steps rather than every second. Good
  // enough: this tells the operator a recording is ALIVE and roughly how long
  // it has run, which is what they are watching for.
  const elapsed = recording ? Math.round(data.recording.elapsed_s || 0) : 0;
  const cams = cameraChoices(data);
  const multi = showPicker(cams);
  const chosen = cams.find((c) => c.id === cam);
  // A switched-off camera cannot be captured; say so instead of letting the
  // button ask the server for an answer we already know.
  const camOff = multi && chosen?.enabled === false;

  function open(kind, file) {
    window.open(apiUrl(`/api/detector/dataset/${kind}/${encodeURIComponent(file)}`), "_blank");
  }

  async function snap() {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/dataset/snap",
                               captureBody({ name }, cams, cam));
      toast.success("Photo saved", d.camera_name ? `${d.file} (${d.camera_name})` : d.file);
      reload().catch(() => {});
    } catch (e) { toast.error("Could not save the photo", e.message); }
    finally { setBusy(false); }
  }

  async function record() {
    setBusy(true);
    try {
      // No duration = a start/stop session; the server treats 0/absent as
      // "runs until stopped" (capped, see MAX_DATASET_SESSION_S).
      const d = await api.post("/api/detector/dataset/record",
                               captureBody({ name, duration_s: mode === "fixed" ? duration : 0 },
                                           cams, cam));
      if (d.manual) {
        toast.success("Recording started", "Press Stop when you have enough.");
        reload().catch(() => {});
      } else {
        toast.success(`Recording ${d.duration_s}s`, d.file);
        setTimeout(() => reload().catch(() => {}), (d.duration_s + 2) * 1000);
      }
    } catch (e) { toast.error("Could not start recording", e.message); }
    finally { setBusy(false); }
  }

  async function stopRecord() {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/dataset/record/stop", {});
      toast.success("Recording stopped", d.file || "");
      // The writer closes on its next frame, so the file lands a moment after
      // this answers -- reload twice rather than show an empty library row.
      reload().catch(() => {});
      setTimeout(() => reload().catch(() => {}), 1500);
    } catch (e) { toast.error("Could not stop the recording", e.message); }
    finally { setBusy(false); }
  }

  if (!data.available) {
    return (
      <section className="page active">
        <div className="page-head"><div className="page-title">Dataset</div></div>
        <NoStorage data={data} />
      </section>
    );
  }

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title">Dataset</div>
        <div className="page-actions">
          <PageGuideHost guide="dataset" />
          <Badge tone="ok">NVMe ready</Badge>
          <NextPageButton />
        </div>
      </div>

      {/* A class rather than an inline style, because the second track has a
          300px floor: on a phone that floor cannot be met and the grid simply
          overflows the screen. Only a media query can lift it, and a media
          query cannot reach an inline style. */}
      <div className="capture-split">
        <div className="panel-card">
          <div className="section-title">
            Camera{" "}
            <Badge tone={running ? "ok" : "warn"}>{running ? "running" : "stopped"}</Badge>
          </div>
          {/* Stopped: the black box takes the shape of a video (16:9) across
              the whole card, as the picture will, instead of a thin strip. */}
          <div style={{ position: "relative", background: "#000", borderRadius: 8,
                        overflow: "hidden", minHeight: 200,
                        ...(running ? {} : { aspectRatio: "16 / 9" }) }}>
            <LiveFeed running={running} transition={null}
                      camera={multi ? cam || undefined : undefined} />
          </div>
          <p className="muted small" style={{ marginTop: 8 }}>
            Captured from the raw stream
            <InfoHint label="About what gets captured">
              No boxes or HUD - ready to label. Feed dark? Start Detection.
            </InfoHint>
            {!running && <> · <Link to="/detection">start detection</Link></>}
          </p>
        </div>

        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <div className="panel-card">
            <div className="section-title">Capture</div>
            <DatasetCameraPicker cameras={cams} value={cam}
                                 disabled={!editable || busy || !!recording}
                                 onChange={setCam} />
            {camOff && (
              <p className="small" style={{ marginTop: 0, color: "var(--warn-text)" }}>
                This camera is not running - switch it on in Camera Setup, or pick another.
              </p>
            )}
            <label className="muted small" style={{ display: "grid", gap: 4 }}>
              <span>
                Name
                <InfoHint label="About the capture name">
                  A timestamp is appended, so one name covers a whole session.
                </InfoHint>
              </span>
              <input className="input" value={name} disabled={!editable}
                     placeholder="e.g. forklift_bay3"
                     onChange={(e) => setName(e.target.value)} />
            </label>

            <div className="filter-bar" style={{ marginTop: 10 }}>
              {[["manual", "Start / stop"], ["fixed", "Fixed length"]].map(([m, label]) => (
                <button key={m} className={"btn btn-sm" + (mode === m ? " btn-accent" : "")}
                        disabled={!editable || !!recording}
                        onClick={() => setMode(m)}>{label}</button>
              ))}
            </div>
            {mode === "fixed" ? (
              <label className="muted small" style={{ display: "flex", alignItems: "center",
                                                      gap: 6, marginTop: 8 }}>
                Clip length
                <input className="input" type="number" min="1" max={maxClip} step="1"
                       style={{ width: 72, flex: "none" }} value={duration}
                       disabled={!editable || !!recording}
                       onChange={(e) => setDuration(Math.min(maxClip,
                                                   Math.max(1, +e.target.value)))} />
                seconds <span className="muted">(max {maxClip})</span>
              </label>
            ) : (
              <p className="muted small" style={{ marginTop: 8 }}>
                Records until you press Stop.
              </p>
            )}

            <div className="filter-bar" style={{ marginTop: 10 }}>
              <button className="btn btn-accent" disabled={!editable || busy || !running || camOff}
                      onClick={snap}>Take photo</button>
              {recording && data.recording.manual ? (
                <button className="btn" disabled={!editable || busy
                                                  || data.recording.stopping}
                        onClick={stopRecord}>
                  {data.recording.stopping ? "Stopping…" : "Stop recording"}
                </button>
              ) : (
                <button className="btn" disabled={!editable || busy || !running || !!recording
                                                  || camOff}
                        onClick={record}>
                  {recording ? "Recording…"
                             : (mode === "fixed" ? `Record ${duration}s` : "Start recording")}
                </button>
              )}
            </div>
            {recording && (
              <p className="muted small" style={{ marginTop: 6 }}>
                Recording <code>{data.recording.file}</code>
                {data.recording.camera_name
                  ? ` from ${data.recording.camera_name}` : ""} - {elapsed}s
                {data.recording.frames ? `, ${data.recording.frames} frames` : ""}.
                {data.recording.manual
                  ? " It stops when you press Stop." : ""}
              </p>
            )}
            {!running && (
              <p className="muted small" style={{ marginTop: 6 }}>
                Detection is stopped - there is no camera stream to capture from.
              </p>
            )}
            {data.recording?.error && (
              <p className="small" style={{ marginTop: 6, color: "var(--warn-text)" }}>
                Last recording failed: {data.recording.error}
              </p>
            )}
          </div>

          <div className="panel-card">
            <div className="section-title">Where it goes</div>
            <div className="muted small">
              <div>Photos → <code className="mono">{data.root}/dataset_image</code></div>
              <div>Clips → <code className="mono">{data.root}/dataset_rec</code></div>
            </div>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gap: 16, marginTop: 16,
                    gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <div className="panel-card">
          <div className="section-title">
            Latest photos <Badge tone="muted">{data.images.length} total</Badge>
          </div>
          <FileList rows={data.images.slice(0, 5)} kind="image" onOpen={open} />
        </div>
        <div className="panel-card">
          <div className="section-title">
            Latest clips <Badge tone="muted">{data.clips.length} total</Badge>
          </div>
          <FileList rows={data.clips.slice(0, 5)} kind="clip" onOpen={open} />
        </div>
      </div>
      <p className="muted small" style={{ marginTop: 10 }}>
        Reviewing, downloading or deleting a whole session happens on the{" "}
        <Link to="/dataset-library">Dataset Library</Link> page.
      </p>
    </section>
  );
}
