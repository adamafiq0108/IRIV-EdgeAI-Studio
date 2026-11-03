// VideoSavingPanel - the Detection page's video display switches, in the
// camera Quality group: [video] overlay, view_on_detect and view_hold_s.
//
// The switches are OFF by default and each saves the moment it is flipped,
// like the Capture and HUD switches beside them (GET/POST
// /api/detector/control/video, core/services/video_saving.py, written into
// the system's config.local.toml). A live tile picks them up the next time
// it opens.
//
// "Detection Box Display" picks who draws the live overlay ([video]
// overlay): the server (the default, "Embed boxes in the video") or the
// browser ("Draw boxes in the browser"). Neither is pushed over the other;
// each option states its trade-off. Its own audited door, POST
// /api/detector/control/video/overlay; the app applies it at its next config
// check, no restart.
//
// 2026-10-12: the Video Visibility block (view_on_detect + hold time) sits
// nested directly under the "Draw boxes in the browser" option, above "Embed
// boxes in the video", because it only works with browser-drawn boxes. Each
// radio also has a one-line "when to use it" (box alignment, not detection
// accuracy - detection is identical in both modes).
//
// view_on_detect reads its detections from the browser overlay, so it is
// disabled while the system draws the overlay on the server - never with a
// bare "Off": the reason AND a "Switch to Draw Boxes in the Browser" button that flips
// the choice above. It never applies to this page's own picture.
//
// Customer copy (2026-10-11): the "Pause video work when nobody is watching"
// switch ([video] on_demand) is no longer drawn. The setting, its API field
// and the app's behaviour are untouched -- a system that already has it on
// keeps it on -- and view_on_detect never depended on it (viewOnDetect.
// gateFor reads view_on_detect and the overlay mode only).
import { useCallback, useEffect, useRef, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { Switch } from "./ui/switch.jsx";
import { useToast } from "./ui/Toast.jsx";
import { DEFAULT_HOLD_S, MAX_HOLD_S, MIN_HOLD_S, holdSeconds } from "../lib/viewOnDetect.js";

const ROW = { display: "flex", alignItems: "center", gap: 12, padding: "8px 0" };

// view_classes: which detected classes open the live video. Not written until
// somebody chooses (absent = every class, as before). The list of choices is
// the ACTIVE model's own classes, handed in by the page; a choice naming a
// class the model no longer has is pruned here, and one left with nothing the
// model can detect goes back to "every class" rather than a video that can
// never open.
const classNames = (list) => [...new Set((Array.isArray(list) ? list : [])
  .map((c) => String(c ?? "").trim()).filter(Boolean))];

export default function VideoSavingPanel({ editable, classes = [] }) {
  const toast = useToast();
  const [video, setVideo] = useState(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [hold, setHold] = useState("");

  const load = useCallback(() => {
    api.get("/api/detector/control/video")
      .then((d) => {
        setVideo(d.video);
        setReason(d.needs_client || "");
        setHold(String(d.video?.view_hold_s ?? DEFAULT_HOLD_S));
      })
      .catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  async function save(fields, { quiet = false } = {}) {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/video", fields);
      setVideo(d.video);
      setHold(String(d.video?.view_hold_s ?? DEFAULT_HOLD_S));
      if (!quiet) toast.success("Video settings updated", "Applied within a few seconds.");
    } catch (e) {
      toast.error("Could not change the video settings", friendlyError(e));
      load();
    } finally { setBusy(false); }
  }

  async function setOverlay(mode) {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/video/overlay", { mode });
      setVideo(d.video);
      toast.success(mode === "client" ? "Boxes now drawn in the browser"
                                      : "Boxes now embedded in the video",
                    "Applied within a few seconds. Reopen a live view to see it.");
    } catch (e) {
      toast.error("Could not change how boxes are displayed", friendlyError(e));
      load();
    } finally { setBusy(false); }
  }

  const known = classNames(classes);
  const stored = Array.isArray(video?.view_classes) ? video.view_classes : null;
  const selected = stored === null ? known : known.filter((c) => stored.includes(c));
  // Switching model: drop what the new model cannot detect (quietly), and
  // never leave a choice that nothing can match.
  const prunedFor = useRef("");
  useEffect(() => {
    if (!editable || busy || !video || known.length === 0 || stored === null) return;
    if (!stored.some((c) => !known.includes(c))) return;
    const key = JSON.stringify([stored, known]);
    if (prunedFor.current === key) return;
    prunedFor.current = key;
    const kept = known.filter((c) => stored.includes(c));
    const next = stored.length > 0 && kept.length === 0 ? null
      : kept.length === known.length ? null : kept;
    save({ view_classes: next }, { quiet: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editable, busy, video, classes]);
  const toggleClass = (name) => {
    const on = selected.includes(name);
    const next = known.filter((c) => (c === name ? !on : selected.includes(c)));
    save({ view_classes: next.length === known.length ? null : next });
  };

  if (!video) return null;
  const serverOverlay = video.overlay !== "client";
  const commitHold = () => {
    const n = holdSeconds(hold);
    setHold(String(n));
    if (n !== video.view_hold_s) save({ view_hold_s: n });
  };

  return (
    <div data-video-saving style={{ marginTop: 10, paddingTop: 8,
                                    borderTop: "1px solid var(--line)" }}>
      <fieldset data-overlay-mode={video.overlay} disabled={!editable || busy}
                style={{ border: 0, margin: 0, padding: "0 0 8px" }}>
        <legend className="muted small" style={{ padding: 0, fontWeight: 650 }}>
          Detection Box Display
        </legend>
        <label style={{ display: "flex", gap: 8, alignItems: "flex-start", paddingTop: 6 }}>
          <input type="radio" name="overlay-mode" value="client"
                 checked={!serverOverlay}
                 onChange={() => setOverlay("client")} />
          <span>
            Draw boxes in the browser
            <span className="muted small" data-overlay-use="client"
                  style={{ display: "block" }}>
              Standard accuracy and low CPU usage.
            </span>
            <span className="muted small" style={{ display: "block" }}>
              Boxes may appear slightly delayed during fast movement.
            </span>
          </span>
        </label>
        <div role="group" aria-labelledby="video-visibility-h" data-video-visibility
             style={{ marginLeft: 24, paddingLeft: 12,
                      borderLeft: "2px solid var(--line)" }}>
          <div id="video-visibility-h" className="muted small"
               style={{ fontWeight: 650, paddingTop: 4 }}>
            Video Visibility
          </div>
          <div style={ROW}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div>Show video only when an object is detected</div>
              <div className="muted small" data-view-on-detect-note>
                {serverOverlay && !video.view_on_detect
                  ? `Unavailable: this ${reason || "needs boxes drawn in the browser"}, `
                    + "and this system embeds them in the video."
                  : "Display the live video only while an object is detected. "
                    + "Applies to live views, not to this page."}
              </div>
              {serverOverlay && !video.view_on_detect && (
                <button type="button" className="btn btn-sm" style={{ marginTop: 6 }}
                        data-turn-on-client disabled={!editable || busy}
                        onClick={() => setOverlay("client")}>
                  Switch to Draw Boxes in the Browser
                </button>
              )}
            </div>
            <Switch checked={!!video.view_on_detect}
                    disabled={!editable || busy || (serverOverlay && !video.view_on_detect)}
                    onCheckedChange={(on) => save({ view_on_detect: on })}
                    aria-label="Show video only when an object is detected" />
          </div>
          {video.view_on_detect && (
            <div style={{ ...ROW, paddingTop: 0 }} data-view-hold>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div>Keep video visible for</div>
                <div className="muted small">
                  Choose how long the video stays visible after the last detection,
                  in seconds ({DEFAULT_HOLD_S} seconds by default, {MIN_HOLD_S} to {MAX_HOLD_S}).
                </div>
              </div>
              <input className="input" type="number" min={MIN_HOLD_S} max={MAX_HOLD_S}
                     style={{ width: 80 }} value={hold} disabled={!editable || busy}
                     aria-label="Keep video visible for (seconds)"
                     onChange={(e) => setHold(e.target.value)}
                     onBlur={commitHold}
                     onKeyDown={(e) => { if (e.key === "Enter") commitHold(); }} />
            </div>
          )}
          {video.view_on_detect && (
            <div style={{ paddingBottom: 8 }} data-view-classes>
              <div>Show video when these classes are detected</div>
              <div className="muted small">
                Only a detection of a ticked class opens the video or keeps it open.
              </div>
              {known.length === 0 ? (
                <div className="muted small" style={{ paddingTop: 6 }} data-view-classes-empty>
                  The classes of the active model are not available yet. Start detection
                  or choose a model, then pick the classes here.
                </div>
              ) : (
                <div role="group" aria-label="Classes that show the video"
                     style={{ display: "grid", gap: 4, paddingTop: 6 }}>
                  {known.map((name) => (
                    <label key={name} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <input type="checkbox" checked={selected.includes(name)}
                             disabled={!editable || busy}
                             onChange={() => toggleClass(name)} />
                      <span>{name}</span>
                    </label>
                  ))}
                </div>
              )}
              {known.length > 0 && selected.length === 0 && (
                <div className="muted small" style={{ paddingTop: 6 }} data-view-classes-none>
                  No class is ticked, so the live video stays hidden.
                </div>
              )}
            </div>
          )}
        </div>
        <label style={{ display: "flex", gap: 8, alignItems: "flex-start", paddingTop: 6 }}>
          <input type="radio" name="overlay-mode" value="server"
                 checked={serverOverlay}
                 onChange={() => setOverlay("server")} />
          <span>
            Embed boxes in the video
            <span className="muted small" data-overlay-use="server"
                  style={{ display: "block" }}>
              High accuracy, may impact CPU usage.
            </span>
            <span className="muted small" style={{ display: "block" }}>
              Boxes appear directly in the video and remain visible in other
              video players.
            </span>
          </span>
        </label>
        <div className="muted small" style={{ paddingTop: 6 }}>
          Snapshots and recorded clips keep their boxes either way.
        </div>
      </fieldset>
    </div>
  );
}
