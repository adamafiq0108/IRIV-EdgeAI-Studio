// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Blynk IoT - connect the dashboard's live values to the Blynk cloud.
//
// Paste the device auth token (Blynk console → Device → Device Info), map any
// stat key - zone counts, line tallies, fn_* function outputs - to a virtual
// pin, enable, save. The master pushes a batch update every interval; the
// status card shows the last push and any error, so a wrong token is visible
// here, not silent.
//
// The token never comes back from the server: the field stays blank once set
// (a hint shows the last 4 characters) and saving with it blank keeps it.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { Badge, Field } from "../components/ui/primitives.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { PasswordInput } from "../components/ui/input.jsx";

export default function Blynk() {
  const toast = useToast();
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [token, setToken] = useState("");     // only what the user types now
  const [status, setStatus] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    api.get("/api/blynk")
      .then((d) => { setMeta(d); setCfg(d.config); setStatus(d.status); })
      .catch(setError);
  }, []);

  // Status refresh at the usual cadence - is the push actually landing?
  // Paused while the tab is hidden, refetched on return (useVisibleInterval).
  useVisibleInterval(() => {
    if (!meta) return;
    api.get("/api/blynk").then((d) => setStatus(d.status)).catch(() => {});
  }, 4000);

  const editable = meta?.editable;
  const statKeys = meta?.stat_keys || [];

  function patch(fields) {
    setCfg((c) => ({ ...c, ...fields }));
    setDirty(true);
  }
  function patchMapping(i, fields) {
    setCfg((c) => ({
      ...c,
      mappings: c.mappings.map((m, j) => (j === i ? { ...m, ...fields } : m)),
    }));
    setDirty(true);
  }

  async function save() {
    try {
      const d = await toast.run(api.put("/api/blynk", { ...cfg, token }),
                                { success: "Blynk settings saved",
                                  failure: "Could not save" });
      setCfg(d.config);
      setToken("");
      setDirty(false);
    } catch { /* toast shown */ }
  }

  async function runTest() {
    setTestResult({ pending: true });
    try {
      const d = await api.post("/api/blynk/test", { token, server: cfg.server });
      setTestResult(d.result);
    } catch (e) {
      setTestResult({ ok: false, error: e.message });
    }
  }

  if (error) return <ErrorNote error={error} />;
  if (!meta || !cfg) return <SkelPage title="Blynk" cards={2} rows={4} />;

  const lastPush = status?.last_push_ts
    ? new Date(status.last_push_ts * 1000).toLocaleTimeString()
    : "never";

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title">Blynk IoT</div>
        <div className="page-actions">
          {dirty ? <Badge tone="warn">unsaved</Badge> : <Badge tone="ok">saved</Badge>}
          <button className="btn btn-accent" disabled={!editable || !dirty} onClick={save}>
            Save
          </button>
        </div>
      </div>

      {!editable && (
        <div className="note-card">Read-only: connecting a cloud is an admin action.</div>
      )}

      <div className="stat-grid">
        {/* connection */}
        <div className="stat-card" style={{ gridColumn: "span 2" }}>
          <div className="stat-label">Connection</div>
          <Field label="Enabled">
            <select className="input" value={cfg.enabled ? "yes" : "no"} disabled={!editable}
                    onChange={(e) => patch({ enabled: e.target.value === "yes" })}>
              <option value="no">off</option>
              <option value="yes">on - push on the interval below</option>
            </select>
          </Field>
          <Field label="Device auth token"
                 hint={cfg.token_set
                   ? `A token is stored (${cfg.token_hint}). Leave blank to keep it.`
                   : "Blynk console → your device → Device Info → Auth Token."}>
            <PasswordInput native className="input" value={token} disabled={!editable}
                   autoComplete="off"
                   placeholder={cfg.token_set ? "•••••••• (unchanged)" : "paste the token"}
                   onChange={(e) => { setToken(e.target.value); setDirty(true); }} />
          </Field>
          <Field label="Server" hint="blynk.cloud unless you self-host.">
            <input className="input" value={cfg.server} disabled={!editable}
                   onChange={(e) => patch({ server: e.target.value })} />
          </Field>
          <Field label="Push interval (seconds)">
            <input className="input" type="number" min="1" max="3600" value={cfg.interval_s}
                   disabled={!editable}
                   onChange={(e) => patch({ interval_s: Number(e.target.value) })} />
          </Field>
          {editable && (
            <button className="btn" onClick={runTest}>Check token</button>
          )}
          {testResult && !testResult.pending && (
            <p className="small" style={{ marginTop: 8 }}>
              {testResult.ok
                ? (testResult.connected
                    ? "✓ token valid - device shows as connected"
                    : "✓ token valid - device offline in Blynk (normal until data flows)")
                : `✗ ${testResult.error}`}
            </p>
          )}
          {testResult?.pending && <p className="muted small">checking…</p>}
        </div>

        {/* status */}
        <div className="stat-card" style={{ gridColumn: "span 2" }}>
          <div className="stat-label">Status</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "8px 0" }}>
            <Badge tone={cfg.enabled ? "ok" : "warn"}>{cfg.enabled ? "enabled" : "disabled"}</Badge>
            <Badge tone={status?.last_error ? "danger" : "ok"}>
              {status?.last_error ? "failing" : "healthy"}
            </Badge>
          </div>
          <p className="small">Last push: <b>{lastPush}</b></p>
          <p className="small">Pushes: <b>{status?.pushes ?? 0}</b> · Failures: <b>{status?.failures ?? 0}</b></p>
          {status?.last_error && (
            <p className="small" style={{ color: "var(--danger, #e5484d)" }}>{status.last_error}</p>
          )}
          <p className="muted small" style={{ marginTop: 8 }}>
            Values land on the mapped virtual pins - build gauges and charts on
            them in the Blynk console or app.
          </p>
        </div>

        {/* mappings */}
        <div className="stat-card" style={{ gridColumn: "span 4" }}>
          <div className="stat-label">Datastream mappings</div>
          <p className="muted small">
            Each row sends one live value to one virtual pin. Function outputs
            (<code>fn_…</code>) appear here as soon as the function is saved.
          </p>
          <table className="mini-table" style={{ width: "100%" }}>
            <tbody>
              <tr><th>Value</th><th>Virtual pin</th><th /></tr>
              {(cfg.mappings || []).map((m, i) => (
                <tr key={i}>
                  <td style={{ minWidth: 240 }}>
                    <select className="input" value={m.key} disabled={!editable}
                            onChange={(e) => patchMapping(i, { key: e.target.value })}>
                      <option value="">- pick a value -</option>
                      {statKeys.map((k) => <option key={k} value={k}>{k}</option>)}
                      {m.key && !statKeys.includes(m.key) && (
                        <option value={m.key}>{m.key} (not publishing now)</option>
                      )}
                    </select>
                  </td>
                  <td style={{ width: 120 }}>
                    <input className="input" value={m.pin} disabled={!editable}
                           placeholder="V1"
                           onChange={(e) => patchMapping(i, { pin: e.target.value })} />
                  </td>
                  <td style={{ width: 40 }}>
                    {editable && (
                      <button className="icon-btn" title="Remove"
                              onClick={() => patch({ mappings: cfg.mappings.filter((_, j) => j !== i) })}>
                        ×
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {!(cfg.mappings || []).length && (
                <tr><td className="muted" colSpan={3}>no mappings yet</td></tr>
              )}
            </tbody>
          </table>
          {editable && (
            <button className="btn btn-sm" style={{ marginTop: 8 }}
                    onClick={() => patch({
                      mappings: [...(cfg.mappings || []),
                                 { key: statKeys[0] || "", pin: `V${(cfg.mappings || []).length + 1}` }],
                    })}>
              + Add mapping
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
