// Feed status box - which of the four rows burned into the top-right of the
// live picture are drawn: model, camera, hailo fps, camera fps.
//
// It lives on CAMERA SETUP, beside Capture. Not on Zones with the Overlay
// rules, despite both ending up on the same picture: an overlay rule is
// workflow output drawn over the SCENE (this person is near that one), while
// this is a readout about the camera and the model running on it. It was
// briefly called "Feed overlay", which read as the second thing and had
// somebody looking for it on the Zones page -- hence the heading it has now.
//
// The box is stamped INTO the JPEG by the detection app (systems/*/app/
// camera.py draw_hud), not drawn over it by this page, which is what puts it
// on every viewer: this feed, the hub's Live Preview, a recorded clip, a
// browser pointed at the stream URL. So a switch here is config the app
// re-reads on its ordinary poll (~2s), and turning a row off turns it off
// everywhere -- which is what somebody clearing a wall display wants.
//
// Every row defaults ON: the box has always shown all four, and a switch
// nobody has touched must not change what is on screen. All four off draws no
// box at all.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { Switch } from "./ui/switch.jsx";
import { useToast } from "./ui/Toast.jsx";

export default function FeedStatusPanel({ editable = true, compact = false }) {
  const toast = useToast();
  const [rows, setRows] = useState([]);
  const [hud, setHud] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get("/api/detector/control/hud")
      .then((d) => { setHud(d.hud || {}); setRows(d.rows || []); })
      .catch(() => { setHud(null); setRows([]); });
  }, []);
  useEffect(load, [load]);

  async function set(key, on) {
    // Optimistic: the switch is the only thing that moves and the server
    // clamps nothing, so waiting a round trip to animate a toggle just reads
    // as a stuck control. A failure puts it back where the server says.
    setHud((cur) => ({ ...(cur || {}), [key]: on }));
    setBusy(true);
    try {
      const d = await api.post("/api/detector/control/hud", { [key]: on });
      setHud(d.hud || {});
    } catch (e) {
      toast.error("Could not change the status box", e.message);
      load();
    } finally { setBusy(false); }
  }

  // Nothing loaded (an older master with no such route, a system mid-restart):
  // render NOTHING, heading included. An empty titled card is a promise of a
  // control that is not there.
  if (!hud || !rows.length) return null;
  const shown = rows.filter((r) => hud[r.key] !== false).length;

  if (compact) {
    // One button per row, right under the video: green while that line is
    // drawn on the feed, BLUE while it is not. Off used to be dimmed grey,
    // which reads as "disabled control" rather than "this is switched off" --
    // an operator wants to see at a glance that something is not on. Blue
    // rather than red because a hidden readout is a choice, not a fault.
    return (
      <div style={{ marginTop: 8 }}>
        <div className="muted small" style={{ marginBottom: 6 }}>Status box :</div>
        {/* A GRID, not a flowing bar: four toggles that mean the same kind of
            thing should be the same size. Sized to their own labels they came
            out four different widths in two ragged rows, which read as four
            unrelated controls rather than one set of four. */}
        <div style={{ display: "grid", gap: 6,
                      gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
          {rows.map((r) => {
            const on = hud[r.key] !== false;
            return (
              <button key={r.key} className="btn btn-sm"
                      disabled={!editable || busy}
                      title={(on ? "Shown on every viewer of this camera - click to hide"
                                 : "Hidden - click to draw it on the feed")}
                      onClick={() => set(r.key, !on)}
                      style={{
                        width: "100%", height: 30, padding: "0 8px",
                        display: "inline-flex", alignItems: "center",
                        justifyContent: "center", whiteSpace: "nowrap",
                        ...(on
                          ? { borderColor: "var(--ok, #23c552)",
                              color: "var(--ok, #23c552)" }
                          : { borderColor: "var(--accent, #04bade)",
                              color: "var(--accent-text, #04bade)" }),
                      }}>
                {r.label}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="panel-card">
      <div className="section-title">Status box on the feed</div>
      <div style={{ marginTop: 10, paddingTop: 8,
                    borderTop: "1px solid rgba(128,128,128,.18)" }}>
        {rows.map((r) => (
          <div key={r.key}
               style={{ display: "flex", alignItems: "center", gap: 12,
                        padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
            <div style={{ minWidth: 0, flex: 1 }}>{r.label}</div>
            <Switch checked={hud[r.key] !== false} disabled={!editable || busy}
                    onCheckedChange={(on) => set(r.key, on)}
                    aria-label={`Show ${r.label} on the feed`} />
          </div>
        ))}
        <p className="muted small" style={{ marginTop: 6, marginBottom: 0 }}>
          {shown
            ? "Drawn on every viewer of this camera, including Live Preview "
              + "and recorded clips. Applies within a few seconds."
            : "No status box is drawn on this camera's feed."}
        </p>
      </div>
    </div>
  );
}
