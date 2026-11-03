// Dataset Library - browse what the Dataset page captured: the photos and the
// clips, selectable, downloadable as a ZIP, deletable in bulk.
//
// Separate from the capture page on purpose. Capturing is a live activity done
// standing at the camera; reviewing and culling a session is a desk activity
// done later, and mixing them made both cramped. Files come from the NVMe
// (/mnt/nvme/dataset_image, /mnt/nvme/dataset_rec) via /api/detector/dataset.
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { usePoll } from "../api/hooks.js";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { Badge, EmptyState } from "../components/ui/primitives.jsx";
import { Film, Image as ImageIcon } from "lucide-react";
import { useToast } from "../components/ui/Toast.jsx";
import { useConfirm } from "../components/ui/ask.jsx";
import { useApp } from "../state/app.jsx";
import { authHeaders } from "../lib/session.js";
import NoStorage from "../components/NoStorage.jsx";
import { useEscape } from "../hooks/use-escape.js";
import { apiUrl } from "../lib/base.js";

const TABS = [
  { key: "image", label: "Photos", plural: "photos" },
  { key: "clip", label: "Clips", plural: "clips" },
];

function sizeLabel(bytes) {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
}

function when(mtime) {
  return new Date(mtime * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** The name without its trailing _YYYYmmdd_HHMMSS stamp - what the operator
 *  typed, so a capture session can be recognised at a glance. */
function sessionOf(name) {
  const m = name.match(/^(.*)_\d{8}_\d{6}\.[a-z0-9]+$/i);
  return m ? m[1] : name.replace(/\.[a-z0-9]+$/i, "");
}

export default function DatasetLibrary() {
  const toast = useToast();
  const confirm = useConfirm();
  const { user } = useApp();
  const [tab, setTab] = useState("image");
  const [selected, setSelected] = useState(() => new Set());
  const [session, setSession] = useState("");     // "" = every session
  const [preview, setPreview] = useState(null);
  useEscape(!!preview, () => setPreview(null));
  const [busy, setBusy] = useState(false);
  const { data, error, reload } = usePoll("/api/detector/dataset", 10000);

  // Switching tab or session must not leave invisible rows selected - a delete
  // would then remove files the operator cannot see.
  useEffect(() => { setSelected(new Set()); }, [tab, session]);

  const rows = useMemo(() => {
    const list = (tab === "image" ? data?.images : data?.clips) || [];
    return session ? list.filter((r) => sessionOf(r.name) === session) : list;
  }, [data, tab, session]);

  const sessions = useMemo(() => {
    const list = (tab === "image" ? data?.images : data?.clips) || [];
    return [...new Set(list.map((r) => sessionOf(r.name)))].sort();
  }, [data, tab]);

  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelPage title="Dataset library" cards={1} rows={6} />;

  if (!data.available) {
    return (
      <section className="page active">
        <div className="page-head"><div className="page-title">Dataset Library</div></div>
        <NoStorage data={data} verb="Nothing to browse" />
      </section>
    );
  }

  const fileUrl = (name) => apiUrl(`/api/detector/dataset/${tab}/${encodeURIComponent(name)}`);
  const allShown = rows.length > 0 && rows.every((r) => selected.has(r.name));

  function toggle(name) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }
  function toggleAll() {
    setSelected(allShown ? new Set() : new Set(rows.map((r) => r.name)));
  }

  async function download() {
    const names = [...selected];
    if (!names.length) return;
    setBusy(true);
    try {
      const res = await fetch(apiUrl("/api/detector/dataset/export"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        credentials: "same-origin",
        body: JSON.stringify({ kind: tab, names }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error
                                   || `HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `dataset_${tab}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Downloading ${names.length} file${names.length > 1 ? "s" : ""}`);
    } catch (e) { toast.error("Could not download", e.message); }
    finally { setBusy(false); }
  }

  async function remove() {
    const names = [...selected];
    if (!names.length) return;
    const what = `${names.length} ${tab === "image" ? "photo" : "clip"}`
      + `${names.length > 1 ? "s" : ""}`;
    if (!await confirm({ title: `Delete ${what}?`,
                         description: "This cannot be undone.",
                         destructive: true })) return;
    setBusy(true);
    try {
      const d = await api.post("/api/detector/dataset/delete", { kind: tab, names });
      setSelected(new Set());
      setPreview(null);
      toast.success(`Deleted ${d.removed} file${d.removed === 1 ? "" : "s"}`);
      reload().catch(() => {});
    } catch (e) { toast.error("Could not delete", e.message); }
    finally { setBusy(false); }
  }

  const selectedBytes = rows.filter((r) => selected.has(r.name))
    .reduce((a, r) => a + r.size, 0);

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title">Dataset Library</div>
        <div className="page-actions">
          <Link className="btn" to="/dataset">Capture →</Link>
        </div>
      </div>

      <div className="filter-bar" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button key={t.key}
                  className={"btn btn-sm" + (tab === t.key ? " btn-accent" : "")}
                  onClick={() => setTab(t.key)}>
            {t.label} ({((t.key === "image" ? data.images : data.clips) || []).length})
          </button>
        ))}
        {sessions.length > 1 && (
          <select className="input" style={{ width: 180, flex: "none" }}
                  value={session} onChange={(e) => setSession(e.target.value)}>
            <option value="">every capture name</option>
            {sessions.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <button className="btn btn-sm" onClick={toggleAll} disabled={!rows.length}>
          {allShown ? "Select none" : "Select all"}
        </button>
        <div style={{ flex: 1 }} />
        {selected.size > 0 && (
          <span className="muted small">
            {selected.size} selected · {sizeLabel(selectedBytes)}
          </span>
        )}
        <button className="btn btn-sm" disabled={!selected.size || busy}
                onClick={download}>Download</button>
        {user?.is_admin && (
          <button className="btn btn-sm btn-danger" disabled={!selected.size || busy}
                  onClick={remove}>Delete</button>
        )}
      </div>

      {!rows.length ? (
        // The same empty state Footage uses, for the same reason: an empty
        // page is a QUESTION -- "is this broken, or have I just not done the
        // thing yet?" -- and a line of grey prose with a link buried in it
        // answers it more quietly than the thing itself deserves. Here the
        // way onward is the Dataset page, where captures are actually taken.
        <div className="panel-card">
          <EmptyState icon={tab === "image" ? ImageIcon : Film}
                      title={`No ${tab === "image" ? "photos" : "clips"} captured`}
                      actions={[{ label: "Capture on the Dataset page",
                                  to: "/dataset", primary: true }]} />
        </div>
      ) : tab === "image" ? (
        <div style={{ display: "grid", gap: 10,
                      gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))" }}>
          {rows.map((r) => {
            const on = selected.has(r.name);
            return (
              <div key={r.name} className="panel-card"
                   style={{ padding: 6, cursor: "pointer",
                            borderColor: on ? "var(--accent)" : undefined }}
                   onClick={() => toggle(r.name)}>
                <div style={{ position: "relative" }}>
                  <img src={fileUrl(r.name)} alt={r.name} loading="lazy"
                       style={{ width: "100%", borderRadius: 5, display: "block",
                                background: "#000", aspectRatio: "16/9",
                                objectFit: "cover" }} />
                  <input type="checkbox" checked={on} readOnly
                         style={{ position: "absolute", top: 6, left: 6, width: 16,
                                  height: 16 }} />
                  <button className="btn btn-sm"
                          style={{ position: "absolute", top: 4, right: 4, padding: "1px 7px" }}
                          onClick={(e) => { e.stopPropagation(); setPreview(r); }}>
                    view
                  </button>
                </div>
                <div className="mono" style={{ fontSize: 10, marginTop: 5,
                                               overflowWrap: "anywhere" }}>{r.name}</div>
                <div className="muted small">
                  {r.camera_name ? `${r.camera_name} · ` : ""}{sizeLabel(r.size)} · {when(r.mtime)}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="panel-card">
          {rows.map((r) => {
            const on = selected.has(r.name);
            return (
              <div key={r.name}
                   style={{ display: "flex", alignItems: "center", gap: 10,
                            padding: "7px 0",
                            borderBottom: "1px solid rgba(128,128,128,.12)" }}>
                <input type="checkbox" checked={on} onChange={() => toggle(r.name)} />
                <span className="mono" style={{ flex: 1, minWidth: 0, fontSize: 12,
                                                overflowWrap: "anywhere" }}>{r.name}</span>
                {r.camera_name && <span className="muted small">{r.camera_name}</span>}
                <span className="muted small">{sizeLabel(r.size)}</span>
                <span className="muted small">{when(r.mtime)}</span>
                <button className="btn btn-sm" onClick={() => setPreview(r)}>Play</button>
              </div>
            );
          })}
        </div>
      )}

      {/* Portalled for the reason on Dialog in ui/primitives.jsx: a
          transformed ancestor turns `inset: 0` into the page's height, and a
          centred child of that is somewhere down the page rather than in the
          window. The card is capped at the viewport and scrolls ITSELF -- a
          dialog taller than the window that hands its overflow to the page
          behind it is the shape this whole pass is about. */}
      {preview && createPortal(
        <div onClick={() => setPreview(null)}
             style={{ position: "fixed", inset: 0, zIndex: 900,
                      background: "rgba(0,0,0,.82)", display: "grid",
                      placeItems: "center", padding: 24 }}>
          <div className="panel-card" role="dialog" aria-modal="true"
               aria-label={preview.name}
               onClick={(e) => e.stopPropagation()}
               style={{ maxWidth: "92vw", maxHeight: "calc(100vh - 48px)",
                        overflowY: "auto", display: "grid", gap: 10 }}>
            {tab === "image" ? (
              <img src={fileUrl(preview.name)} alt={preview.name}
                   style={{ maxWidth: "88vw", maxHeight: "72vh", borderRadius: 8,
                            display: "block" }} />
            ) : (
              <video src={fileUrl(preview.name)} controls autoPlay
                     style={{ maxWidth: "88vw", maxHeight: "72vh", borderRadius: 8,
                              display: "block", background: "#000" }} />
            )}
            <div className="filter-bar">
              <span className="mono small" style={{ overflowWrap: "anywhere" }}>
                {preview.name}
              </span>
              <div style={{ flex: 1 }} />
              <Badge tone="muted">{sizeLabel(preview.size)}</Badge>
              <a className="btn btn-sm" href={fileUrl(preview.name)}
                 download={preview.name}>Download</a>
              <button className="btn btn-sm" onClick={() => setPreview(null)}>Close</button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </section>
  );
}
