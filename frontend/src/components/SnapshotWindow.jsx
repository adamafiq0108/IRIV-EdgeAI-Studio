// SnapshotWindow - the Footage page's window for ONE photo.
//
// The photo at its own shape and the facts about it side by side on a wide
// screen, one under the other on a narrow one (theme.css .fx-snap): detection
// id, time and date; what was detected, where and on which device; the timer
// when a real one was running; and -- when the Snapshot node was set to crop --
// a close-up of each detected object, one at a time with Previous and Next.
// Download PDF makes the violation notice (lib/snapshotReport.js) and asks
// whether the close-ups go in it.
//
// The facts come from /api/detector/snapshots/<id>/meta. A snapshot with no
// record (an older one) still has its id, time and date, read back from its
// name; what cannot be known is not shown, and is never filled in.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";
import { useToast } from "./ui/Toast.jsx";
import { apiUrl } from "../lib/base.js";
import {
  noticeDash, detectedText, directionText, downloadNotice, timerRows,
} from "../lib/snapshotReport.js";

function Fact({ name, children, big }) {
  return (
    <div className="fx-fact">
      <div className="fx-fact-name">{name}</div>
      <div className={"fx-fact-val" + (big ? " fx-fact-big" : "")}>{children}</div>
    </div>
  );
}

export default function SnapshotWindow({ row, imgUrl, canDelete, onDelete, onClose }) {
  const { system } = useApp();
  const toast = useToast();
  const [view, setView] = useState(null);
  const [failed, setFailed] = useState(false);
  const [crop, setCrop] = useState(0);
  const [asking, setAsking] = useState(false);
  const [withCrops, setWithCrops] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let dead = false;
    setView(null); setFailed(false); setCrop(0); setAsking(false);
    api.get(`/api/detector/snapshots/${encodeURIComponent(row.id)}/meta`)
      .then((d) => { if (!dead) setView(d.snapshot); })
      .catch(() => { if (!dead) setFailed(true); });
    return () => { dead = true; };
  }, [row.id]);

  const cropUrl = (n) => apiUrl(
    `/api/detector/snapshots/${encodeURIComponent(row.id)}/crops/${n}`);
  const crops = view?.crops || [];
  const here = crops[Math.min(crop, Math.max(0, crops.length - 1))];
  const timer = timerRows(view?.timer);

  async function makePdf() {
    setBusy(true);
    try {
      await downloadNotice({
        view, photoUrl: imgUrl(row.id), cropUrl,
        includeCrops: withCrops && crops.length > 0,
        systemName: system?.display_name || "System",
      });
      setAsking(false);
    } catch (e) {
      toast.error("Could not make the PDF", e?.message || "Try again in a moment.");
    } finally { setBusy(false); }
  }

  return (
    <div className="fx-lightbox" onClick={onClose}>
      <div className="panel-card fx-lightbox-card fx-snap-card" role="dialog"
           aria-modal="true" aria-label={row.id} onClick={(e) => e.stopPropagation()}>
        <div className="fx-snap" data-snapshot-window>
          <div className="fx-snap-photo">
            <img src={imgUrl(row.id)} alt={row.id} className="fx-lightbox-media" />
          </div>

          <div className="fx-snap-side">
            {failed && (
              <p className="muted small">
                The details of this photo could not be read. The picture is still here.
              </p>
            )}
            {view && (
              <>
                <div className="fx-facts" data-snapshot-facts>
                  <Fact name="Detection ID"><span className="mono">{noticeDash(view.detection_id)}</span></Fact>
                  <Fact name="Time">{noticeDash(view.time)}</Fact>
                  <Fact name="Date">{noticeDash(view.date)}</Fact>
                </div>

                <div className="fx-cols">
                  <div>
                    <div className="fx-head">Observation</div>
                    <Fact name="Detected" big>
                      <span className="fx-detected">{noticeDash(detectedText(view))}</span>
                    </Fact>
                    {view.direction && (
                      <Fact name="Direction">{noticeDash(directionText(view.direction))}</Fact>
                    )}
                  </div>
                  <div>
                    <div className="fx-head">Place / device</div>
                    {view.location && <Fact name="Location">{noticeDash(view.location)}</Fact>}
                    <Fact name="Device">{noticeDash(view.device)}</Fact>
                  </div>
                </div>

                {timer && (
                  <div data-snapshot-timer>
                    <div className="fx-head">Timer</div>
                    <table className="fx-timer">
                      <tbody>
                        {timer.map(([name, val]) => (
                          <tr key={name}><th scope="row">{name}</th><td>{val}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {crops.length > 0 && here && (
                  <div data-snapshot-crops>
                    <div className="fx-head">
                      Close-ups
                      <span className="muted small" style={{ marginLeft: 8, fontWeight: 400 }}>
                        {crop + 1} of {crops.length}
                      </span>
                    </div>
                    <img src={cropUrl(here.n)} alt={`Close-up ${crop + 1}`} className="fx-crop" />
                    <div className="fx-crop-line">
                      <span>
                        {noticeDash(here.label)}
                        {here.confidence !== null && here.confidence !== undefined
                          ? ` ${Math.round(Number(here.confidence) * 100)}%` : ""}
                        {here.primary ? " (this notice)" : ""}
                      </span>
                      {crops.length > 1 && (
                        <span className="fx-crop-nav">
                          <button type="button" className="btn btn-sm" data-crop-prev
                                  onClick={() => setCrop((crop + crops.length - 1) % crops.length)}>
                            Previous
                          </button>
                          <button type="button" className="btn btn-sm" data-crop-next
                                  onClick={() => setCrop((crop + 1) % crops.length)}>
                            Next
                          </button>
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}

            {asking && view && (
              <div className="fx-ask" data-pdf-ask>
                {crops.length > 0 && (
                  <label className="fx-ask-row">
                    <input type="checkbox" checked={withCrops}
                           onChange={(e) => setWithCrops(e.target.checked)} />
                    <span>
                      Include the close-ups ({crops.length}), one page for each
                    </span>
                  </label>
                )}
                <div className="fx-ask-row">
                  <button type="button" className="btn btn-sm btn-primary" disabled={busy}
                          onClick={makePdf} data-pdf-make>
                    {busy ? "Making the PDF..." : "Download PDF"}
                  </button>
                  <button type="button" className="btn btn-sm" disabled={busy}
                          onClick={() => setAsking(false)}>Cancel</button>
                </div>
              </div>
            )}

            <div className="filter-bar fx-snap-actions">
              <code className="mono small" title={row.id}>{row.id}</code>
              <div style={{ flex: 1 }} />
              <a className="btn btn-sm" href={imgUrl(row.id)} download={`${row.id}.jpg`}>
                Download photo
              </a>
              <button type="button" className="btn btn-sm" disabled={!view || asking}
                      data-pdf-open
                      onClick={() => (crops.length > 0 ? setAsking(true) : makePdf())}>
                Download PDF
              </button>
              {canDelete && (
                <button type="button" className="btn btn-sm btn-danger"
                        onClick={() => onDelete(row.id)}>Delete</button>
              )}
              <button type="button" className="btn btn-sm" onClick={onClose}>Close</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
