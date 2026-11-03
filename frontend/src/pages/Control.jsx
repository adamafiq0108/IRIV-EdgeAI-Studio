// Manage / device control (ported from control.html + control.js). Renders the
// device's Contract v1.2 control snapshot generically - domains/settings/actions
// come straight from the device, so a new control domain needs zero changes
// here.
//
// Confirmations and declared action params are real dialogs, not the browser's
// prompt()/confirm()/alert(): those cannot mark an action destructive, cannot
// validate a required param, and are suppressed outright by some browsers -
// which turns "reboot the device" into a button that silently does nothing.
// Destructive actions now require the operator to TYPE the device label, and
// that value is actually checked (the prompt() version discarded it).
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client.js";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { ConfirmDialog, FormDialog } from "../components/ui/dialogs.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { PasswordInput } from "../components/ui/input.jsx";

// Extract a host/IP from a camera URL/value (mirrors the backend cameras svc).
function hostOf(value) {
  if (!value) return null;
  const v = String(value).trim();
  const m = v.match(/(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/);
  if (m) return m[1];
  try { const h = new URL(v).hostname; return h ? h.toLowerCase() : null; } catch { return null; }
}

// Actions whose effect only shows up after the device has settled.
const SETTLE_AFTER = ["device.reboot", "device.update", "device.reset_data",
                      "app.start", "app.stop", "app.restart"];

// Storage gets a purpose-built panel (not the generic settings form): a usage
// bar over the device's disk, a slider to allocate how much of it detection may
// use, a retention control, and the purge action. The backend's storage.usage()
// already reports sys_total_gb / used_gb / limit_gb, so this is pure rendering.
function StorageCard({ host, domain, onDone }) {
  const toast = useToast();
  const st = domain.status || {};
  const settings = domain.settings || {};
  const total = Math.max(0, Math.floor(st.sys_total_gb || 0));
  const free = st.sys_free_gb || 0;
  const used = st.used_gb || 0;
  const initLimit = settings.max_storage_gb != null
    ? Math.round(settings.max_storage_gb)
    : Math.max(1, Math.round(st.limit_gb || (total ? total * 0.8 : 1)));
  const [limit, setLimit] = useState(initLimit);
  const [months, setMonths] = useState(settings.keep_months ?? st.keep_months ?? 6);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [purgeOpen, setPurgeOpen] = useState(false);

  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const barClass = pct >= 90 ? "crit" : pct >= 75 ? "warn" : "";

  async function save() {
    setBusy(true); setMsg("Saving…");
    try {
      await api.post(`/api/control/${host}/storage`, { max_storage_gb: limit, keep_months: Number(months) });
      setMsg("Saved."); onDone && onDone();
    } catch (e) { setMsg(e.message || "failed"); toast.error("Could not save storage", e.message); }
    setBusy(false);
  }

  async function purge() {
    setBusy(true); setMsg("Purging…");
    try {
      const d = await api.post(`/api/control/${host}/action/storage.purge`, { keep_months: Number(months) });
      const n = d.deleted_files ?? 0;
      setMsg(`Deleted ${n} file(s).`);
      toast.success(`Purged ${n} file(s) older than ${months} month(s)`);
      setPurgeOpen(false);
      onDone && onDone();
    } catch (e) { setMsg(e.message || "failed"); toast.error("Purge failed", e.message); }
    setBusy(false);
  }

  return (
    <div className="panel-card control-domain">
      <div className="section-head"><div className="section-title">Storage</div>
        {st.nvme_mounted && <span className="badge badge-ok">NVMe</span>}
      </div>

      <div className="storage-usage">
        <div className="storage-usage-head">
          <span className="tnum"><b>{used.toFixed(2)} GB</b> used of {limit} GB allocated</span>
          <span className="mono tnum">{pct}%</span>
        </div>
        <div className="storage-bar-wrap"><div className={"storage-bar " + barClass} style={{ width: pct + "%" }} /></div>
        <div className="storage-disk mono tnum">
          Device disk: {total} GB total · {free.toFixed(1)} GB free
          {st.active_images != null ? ` · ${st.active_images} snapshot(s)` : ""}
        </div>
      </div>

      <div className="storage-control">
        <label>Allocate to detection <span className="mono">{limit} GB</span></label>
        {total > 1 ? (
          <input type="range" min="1" max={total} step="1" value={Math.min(limit, total)}
                 onChange={(e) => setLimit(Number(e.target.value))} className="slider" />
        ) : (
          <input type="number" min="1" value={limit} className="input"
                 onChange={(e) => setLimit(Number(e.target.value))} />
        )}
        <div className="slider-ends mono"><span>1 GB</span><span>{total || "?"} GB</span></div>
      </div>

      <div className="storage-control">
        <label>Keep footage <span className="mono">{months} month(s)</span></label>
        <input type="range" min="1" max="24" step="1" value={months}
               onChange={(e) => setMonths(Number(e.target.value))} className="slider" />
        <div className="slider-ends mono"><span>1</span><span>24 months</span></div>
      </div>

      <div className="control-actions">
        <button className="btn btn-accent" onClick={save} disabled={busy}>Save storage</button>
        <button className="btn btn-danger" onClick={() => setPurgeOpen(true)} disabled={busy}>Purge old footage</button>
        <span className="muted">{msg}</span>
      </div>

      <ConfirmDialog
        open={purgeOpen}
        onOpenChange={(o) => !o && setPurgeOpen(false)}
        title={`Purge footage older than ${months} month(s)?`}
        description="This permanently deletes the files from the device. It cannot be undone."
        confirmLabel="Purge"
        destructive
        busy={busy}
        onConfirm={purge}
      />
    </div>
  );
}

function DomainCard({ host, deviceLabel, domain, onDone, usage }) {
  const toast = useToast();
  const [msg, setMsg] = useState("");
  const [scan, setScan] = useState(null);   // camera scan results (found cameras)
  const [busy, setBusy] = useState(false);
  const formRef = useRef(null);

  // Camera already assigned elsewhere (409): hold the attempted payload so the
  // operator can override after reviewing who else uses it.
  const [conflict, setConflict] = useState(null);   // {payload, usedBy}
  // Action flow: optional params dialog, then a confirm step, then execute.
  const [paramsFor, setParamsFor] = useState(null);         // action
  const [confirmFor, setConfirmFor] = useState(null);       // {action, values}

  const confirmName = deviceLabel || host;

  // Fill the camera field from a scan result, then the operator reviews + Saves.
  function useCam(c) {
    const val = c.rtsp_url || c.device || c.ip || "";
    const form = formRef.current;
    const inp = form && form.elements["rtsp_url"];
    if (inp) { inp.value = val; inp.focus(); setMsg("Filled camera field - review and Save."); }
    else { setMsg("This device has no rtsp_url field to fill."); }
  }

  async function doSave(payload) {
    setMsg("Saving…");
    try {
      await api.post(`/api/control/${host}/${encodeURIComponent(domain.name)}`, payload);
      setMsg("Saved."); setConflict(null); onDone && onDone();
    } catch (err) {
      // Camera already assigned to another slave: warn, let the operator
      // override (they may share one camera across slaves while testing).
      if (err.status === 409 && err.data && err.data.conflict) {
        setConflict({ payload, usedBy: err.data.used_by || [] });
        setMsg("");
        return;
      }
      setMsg(err.message || "failed");
    }
  }

  function saveSettings(e) {
    e.preventDefault();
    const payload = {};
    new FormData(e.target).forEach((v, k) => { payload[k] = v; });
    doSave(payload);
  }

  // For the camera domain: which of THIS device's camera IPs are also used by
  // another slave (persistent "shared with" visibility).
  const shared = [];
  if (domain.name === "camera" && usage) {
    for (const v of Object.values(domain.settings || {})) {
      const ip = hostOf(v);
      if (!ip) continue;
      const others = (usage[ip] || []).filter((u) => u.host_serial !== host);
      if (others.length) shared.push({ ip, devices: others.map((o) => o.label) });
    }
  }

  function startAction(a) {
    if ((a.params || []).length) setParamsFor(a);
    else setConfirmFor({ action: a, values: {} });
  }

  async function execute(a, values) {
    setBusy(true);
    try {
      const d = await api.post(`/api/control/${host}/action/${encodeURIComponent(a.name)}`, values);
      setConfirmFor(null);
      // Scan actions return a camera list -> show it inline (clickable) rather
      // than a throwaway alert, so the operator can pick one.
      if (Array.isArray(d.devices)) {
        setScan(d.devices);
        setMsg(`${a.label}: ${d.devices.length} camera(s) found` + (d.devices.length ? "" : " (try Manual, or check the subnet)"));
        toast.info(`${a.label}: ${d.devices.length} camera(s) found`,
                   d.devices.length ? undefined : "Try Manual, or check the subnet.");
      } else {
        const note = d.note ? d.note
          : d.reset ? `Cleared ${d.events_cleared ?? 0} event(s), ${d.snapshots_cleared ?? 0} snapshot(s), ${d.files_deleted ?? 0} file(s).`
          : d.scheduled ? "Scheduled."
          : d.deleted_files !== undefined ? `Deleted ${d.deleted_files} file(s).`
          : d.running === true ? "Running."
          : "Done.";
        setMsg(`${a.label}: ${note}`);
        toast.success(a.label || a.name, note);
      }
      if (SETTLE_AFTER.includes(a.name)) setTimeout(onDone, 2000);
    } catch (e) {
      toast.error(`${a.label || a.name} failed`, e.message);
    } finally {
      setBusy(false);
    }
  }

  const settings = domain.settings || {};
  const status = domain.status || {};
  const actions = domain.actions || [];

  return (
    <div className="panel-card control-domain">
      <div className="section-head"><div className="section-title">{cap(domain.name)}</div></div>
      {shared.length > 0 && (
        <div className="info-box" style={{ marginBottom: 10 }}>
          Shared cameras (also assigned to another device):
          {shared.map((s) => (
            <div key={s.ip} className="mono" style={{ fontSize: 11 }}>• {s.ip} → {s.devices.join(", ")}</div>
          ))}
        </div>
      )}
      {domain.meta && domain.meta.note && (
        <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>{domain.meta.note}</div>
      )}
      {Object.keys(status).length > 0 && (
        <div className="control-status">
          {Object.entries(status).map(([k, v]) => (
            <span className="stat" key={k}><span className="stat-k">{k}</span><span className="stat-v mono">{String(v)}</span></span>
          ))}
        </div>
      )}
      {Object.keys(settings).length > 0 && (
        <form ref={formRef} className="control-settings" onSubmit={saveSettings}>
          {Object.entries(settings).map(([k, v]) => (
            <div className="field" key={k}>
              <label>{k}{k === "rtsp_url" ? " (type manually, or use Scan below)" : ""}</label>
              {k.toLowerCase().includes("password") ? (
                <PasswordInput native className="input" name={k}
                               defaultValue={v == null ? "" : v}
                               autoComplete="new-password" />
              ) : (
                <input className="input" name={k} defaultValue={v == null ? "" : v}
                       type="text" autoComplete="off" />
              )}
            </div>
          ))}
          <button className="btn btn-accent" type="submit">Save {domain.name}</button>{" "}
          <span className="muted">{msg}</span>
        </form>
      )}
      {actions.length > 0 && (
        <div className="control-actions">
          {actions.map((a) => (
            <button key={a.name} className={"btn " + (a.destructive ? "btn-danger" : "")}
                    onClick={() => startAction(a)}>{a.label || a.name}</button>
          ))}
        </div>
      )}
      {scan && scan.length > 0 && (
        <div className="table-card" style={{ marginTop: 10 }}>
          <div className="section-title" style={{ padding: "8px 10px 4px" }}>
            Found cameras <span>click "Use" to fill the camera field, then Save</span>
          </div>
          {scan.map((c, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                                  gap: 8, padding: "6px 10px", borderTop: "1px solid var(--border)" }}>
              <span className="mono" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis" }}>
                {c.rtsp_url || c.device || c.ip}{c.label ? ` - ${c.label}` : ""}
              </span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => useCam(c)}>Use</button>
            </div>
          ))}
        </div>
      )}
      {scan && scan.length === 0 && (
        <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>
          No cameras found on the subnet - enter the RTSP URL manually above.
        </div>
      )}

      {/* Camera already assigned to another device. */}
      <ConfirmDialog
        open={!!conflict}
        onOpenChange={(o) => { if (!o) { setConflict(null); setMsg("Cancelled - pick another camera."); } }}
        title="This camera is already assigned"
        description="Another device is already using it. Sharing one camera across devices works, but both will pull the same stream."
        confirmLabel="Use it anyway"
        onConfirm={() => { const c = conflict; setConflict(null); doSave({ ...c.payload, _force: true }); }}
      >
        <ul className="grid gap-1 text-xs">
          {(conflict?.usedBy || []).map((c) => (
            <li key={c.ip} className="font-mono">
              {c.ip} → {(c.devices || []).map((d) => d.label).join(", ")}
            </li>
          ))}
        </ul>
      </ConfirmDialog>

      {/* Declared action params (Contract v1.2). */}
      <FormDialog
        open={!!paramsFor}
        onOpenChange={(o) => !o && setParamsFor(null)}
        title={paramsFor ? (paramsFor.label || paramsFor.name) : ""}
        description={paramsFor?.params?.length ? "This action takes the following input." : ""}
        fields={(paramsFor?.params || []).map((p) => ({
          name: p.name,
          label: p.label || p.name,
          required: !!p.required,
          type: p.type === "number" ? "number"
              : /password|secret|token/i.test(p.name) ? "password" : "text",
          hint: p.required ? undefined : "Optional",
        }))}
        submitLabel="Continue"
        destructive={!!paramsFor?.destructive}
        onSubmit={(values) => { const a = paramsFor; setParamsFor(null); setConfirmFor({ action: a, values }); }}
      />

      {/* Final confirm. Destructive actions require the device label typed. */}
      <ConfirmDialog
        open={!!confirmFor}
        onOpenChange={(o) => !o && setConfirmFor(null)}
        title={confirmFor ? `Run "${confirmFor.action.label || confirmFor.action.name}"?` : ""}
        description={
          confirmFor?.action?.destructive
            ? "This is destructive and cannot be undone."
            : `This runs on ${confirmName}.`
        }
        confirmLabel={confirmFor ? (confirmFor.action.label || confirmFor.action.name) : "Run"}
        destructive={!!confirmFor?.action?.destructive}
        requireText={confirmFor?.action?.destructive ? confirmName : null}
        busy={busy}
        onConfirm={() => execute(confirmFor.action, confirmFor.values)}
      />
    </div>
  );
}

export default function Control() {
  const { host } = useParams();
  const toast = useToast();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usage, setUsage] = useState(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);

  const load = useCallback(async () => {
    try { setData(await api.get(`/api/control/${host}`)); setError(null); }
    catch (e) { setError(e); }
    // Fleet camera-usage map (for "shared with" badges). Best-effort: a slow or
    // failed probe never blocks the Manage page.
    api.get("/api/cameras/usage").then((d) => setUsage(d.usage || {})).catch(() => {});
  }, [host]);
  useEffect(() => { load(); }, [load]);

  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelPage title="System Control" cards={3} rows={4} />;
  const { device = {}, detail = {}, domains = [], banner } = data;

  async function submitRename({ display_name }) {
    if (!display_name || display_name === device.label) { setRenameOpen(false); return; }
    setRenaming(true);
    try {
      await api.patch(`/api/devices/${host}`, { display_name });
      setRenameOpen(false);
      toast.success(`Renamed to "${display_name}"`);
      load();
    } catch (e) {
      toast.error("Rename failed", e.message);
    } finally {
      setRenaming(false);
    }
  }

  // Camera in its own column; Device, Detection and Storage stacked beside it.
  const renderDomain = (d) => d.name === "storage"
    ? <StorageCard key={d.name} host={host} domain={d} onDone={load} />
    : <DomainCard key={d.name} host={host} deviceLabel={device.label} domain={d} onDone={load} usage={usage} />;
  const cameraDomain = domains.find((d) => d.name === "camera");
  const stackOrder = ["device", "detection", "storage"];
  const stacked = [
    ...stackOrder.map((n) => domains.find((d) => d.name === n)).filter(Boolean),
    ...domains.filter((d) => d.name !== "camera" && !stackOrder.includes(d.name)),
  ];

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title">
          Manage - {device.label}{" "}
          <button className="btn btn-ghost btn-sm" onClick={() => setRenameOpen(true)} title="Rename device"
                  style={{ verticalAlign: "middle", marginLeft: 6 }}>✎ Rename</button>
        </div>
        <div className="page-sub mono">{device.host_serial}{" "}
          <span className={"pill " + (device.online ? "ok" : "warn")}>{device.online ? "online" : "offline"}</span>
        </div>
      </div>

      {banner && <div className="panel-card"><div className="muted">{banner}</div></div>}

      {detail && Object.keys(detail).length > 0 && (
        <div className="detail-grid">
          {Object.entries(detail).map(([section, rows]) => (
            <div className="panel-card detail-card" key={section}>
              <div className="section-title">{section}</div>
              <div className="detail-rows">
                {Object.entries(rows).map(([k, v]) => (
                  <div className="detail-row" key={k}><span className="k">{k}</span><span className="v mono">{String(v)}</span></div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="control-columns">
        <div className="control-col">{cameraDomain && renderDomain(cameraDomain)}</div>
        <div className="control-col">{stacked.map(renderDomain)}</div>
      </div>

      <FormDialog
        open={renameOpen}
        onOpenChange={(o) => !o && setRenameOpen(false)}
        title="Rename device"
        description="This is the label operators see. The host serial never changes."
        fields={[{
          name: "display_name", label: "Display name", required: true,
          defaultValue: device.label || "", placeholder: device.host_serial,
        }]}
        submitLabel="Rename"
        busy={renaming}
        onSubmit={submitRename}
      />
    </section>
  );
}

function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
