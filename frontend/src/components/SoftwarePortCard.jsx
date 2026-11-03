// SoftwarePortCard - how this box is reached, as a setting: ONE choice of two
// (Tok Aba, 2026-10-07).
//
//   Use proxy (port 80)  one address with no port: http://<box>/ for the
//                        dashboard, http://<box>/s/<system>/ for every system,
//                        all through the box's own reverse proxy. Port 80 is
//                        fixed for this choice (core/services/webaccess.py).
//   Custom port          today's behaviour, the default: the dashboard on the
//                        port typed here, each system on its own port.
//
// Lives on the Network page (NetworkPanel.jsx) because that is where "how do
// I reach this box" is answered; drawn only for master or a developer, since
// either choice moves every bookmark on the site and every address the front
// panel prints.
//
// Nothing here is live. Test says whether the choice can work; Apply SAVES
// it for the next restart of the box and asks "Restart now / Later", naming
// the address the dashboard will have. Until the box restarts it keeps
// answering where this browser is, and the card shows both. One restart also
// applies a hostname saved on the card below (BoxRestartNote.jsx). No box
// needs a terminal for this: the port is the software's own setting
// (core/services/hubport.py) and port 80 goes through a root helper the
// installer put there.
//
// Customer view (Tok Aba, 2026-10-14): "Access method" as two radio rows,
// "Standard Access" (PROXY, the API's own mode name; the word "proxy" stays
// out of the page) and "Custom Port" (the port field shows only under it);
// "Current dashboard address" as a link with a Copy button; one note on the
// restart; "Test connection" and "Apply settings". "Test connection" is the
// card's existing Test: POST /api/hub/port/test asks the DEVICE whether the
// selected method can work (port 80 ready, or the typed port free) - it does
// not open the address from this browser. The live/pending summary, the
// longer explanation and the --port note are for a developer account only.
// Restart notes, Keep, the last restart's refusal and the not-own-service
// note stay in sight for everyone: they say what the device is doing.
import { useCallback, useEffect, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { useIsDeveloper } from "../state/app.jsx";
import { Copy, ExternalLink, Info, Loader2, Monitor, TriangleAlert } from "lucide-react";
import { LastResult, NOT_OWN_SERVICE, Note, RestartNote, announceChange, appliesHere,
         askRestart, restartBox, useRestartEvent } from "./BoxRestartNote.jsx";

const PROXY = "proxy";
const PORT = "port";

export default function SoftwarePortCard() {
  const isDev = useIsDeveloper();
  const [data, setData] = useState(null);
  const [port, setPort] = useState("");
  const [choice, setChoice] = useState("");       // "proxy" | "port"
  const [tested, setTested] = useState(null);     // the Test answer for THIS choice/text
  const [busy, setBusy] = useState("");
  const [moving, setMoving] = useState(null);     // set once a restart is under way
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const d = await api.get("/api/hub/port");
      if (!d || typeof d.running !== "number") { setData({ hidden: true }); return; }
      setData(d);
      setPort((cur) => cur || String(d.pending || d.running));
      setChoice((cur) => cur || d.access?.pending || d.access?.mode || PORT);
    } catch (e) {
      // 403 for a site admin: the card is simply not theirs.
      setData({ hidden: true });
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useRestartEvent(load);

  if (!data || data.hidden) return null;

  // A server without the access setting offers the custom port only.
  const access = data.access || null;
  const nowMode = access?.mode || PORT;
  const nextMode = access?.pending || nowMode;
  const proxy = choice === PROXY && !!access;
  const n = Number(port);
  const valid = proxy || (/^\d{4,5}$/.test(port.trim()) && n >= 1024 && n <= 65535);
  const unchanged = proxy ? nextMode === PROXY
    : valid && nextMode === PORT && n === (data.pending || data.running);
  const restart = data.restart || {};
  // Only irivai-hub.service applies a saved choice; from any other hub Apply
  // is refused by the server and disabled here (BoxRestartNote.jsx).
  const applies = appliesHere(restart);
  const pendingAny = data.pending || access?.pending;

  function pick(mode) {
    setChoice(mode);
    setTested(null);
  }

  async function test() {
    setBusy("test");
    try {
      setTested(await api.post("/api/hub/port/test", proxy ? { mode: PROXY } : { port: n }));
    } catch (e) { toast.error(proxy ? "Could not test port 80" : "Could not test the port", friendlyError(e)); }
    finally { setBusy(""); }
  }

  async function restartNow() {
    setBusy("restart");
    try {
      const d = await restartBox(setMoving);
      toast.success("Restarting the device", `Open ${d.after?.name_url} in about a minute.`);
    } catch (e) {
      toast.error("Could not restart the device", friendlyError(e));
      setBusy("");
    }
  }

  async function save(body, cancelling) {
    setBusy("apply");
    let d;
    try { d = await api.post("/api/hub/port", body); }
    catch (e) {
      toast.error(body.mode === PROXY ? "The device cannot use port 80" : "The device cannot use that port",
                  friendlyError(e));
      setBusy(""); return;
    }
    setData(d); setTested(null); setBusy(""); setPort(String(d.pending || d.running));
    setChoice(d.access?.pending || d.access?.mode || PORT);
    announceChange();
    if (cancelling) { toast.info("Change cancelled", "The device stays as it is."); return; }
    if (await askRestart(confirm, d.restart)) { await restartNow(); return; }
    toast.info("Saved", d.restart?.can_reboot
      ? "The device keeps its current address until it restarts."
      : "It applies the next time the device is switched off and on.");
  }

  // "Keep" puts back what the box answers on now, cancelling whatever waits.
  const keep = () => save(nowMode === PROXY ? { mode: PROXY }
                                            : { mode: PORT, port: data.running }, true);
  const STANDARD = "Standard Access (port 80)";
  const nowWords = nowMode === PROXY ? STANDARD : `port ${data.running}`;
  const nextWords = access?.pending ? (access.pending === PROXY ? STANDARD
                                       : `port ${data.pending || data.running}`)
    : (data.pending ? `port ${data.pending}` : "");

  const option = (mode, label, about) => (
    <button type="button" role="radio" aria-checked={choice === mode}
            data-testid={`access-${mode}`} className="radio-opt"
            disabled={!!busy || !!moving} onClick={() => pick(mode)}>
      <span className="radio-dot" aria-hidden="true" />
      <span>
        <strong className="small" data-testid="access-label">{label}</strong>
        <span className="muted small" style={{ display: "block", marginTop: 2 }}>{about}</span>
      </span>
    </button>
  );

  async function copyUrl() {
    try {
      await navigator.clipboard.writeText(data.url);
      toast.success("Address copied", data.url);
    } catch {
      // A plain-HTTP page may not use the clipboard: the link text is selectable.
      toast.info("Select it and copy", "The browser allows the copy button only over HTTPS.");
    }
  }

  // Shown only under Custom Port (or alone, on a server without the choice).
  const portField = !proxy && (
    <label style={{ display: "block", marginTop: access ? 0 : 8, marginLeft: access ? 26 : 0 }}
           data-testid="access-port-field">
      <div className="muted small">Port number (1024 to 65535)</div>
      <input className="input" value={port} inputMode="numeric"
             style={{ minWidth: 0, width: 180, maxWidth: "100%",
                      ...(port && !valid ? { borderColor: "var(--alert)" } : {}) }}
             onChange={(e) => { setPort(e.target.value); setTested(null); }} />
    </label>
  );

  return (
    <div className="panel-card" style={{ marginBottom: 14 }} data-testid="access-card">
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Monitor size={18} aria-hidden="true" />
        <div style={{ flex: 1 }}>
          <strong>Dashboard Access</strong>
          <div className="muted small" style={{ marginTop: 2 }} data-testid="access-about">
            Choose how to access your device dashboard.
          </div>
        </div>
      </div>

      <div className="card-rule">
        <strong className="small">Access method</strong>
        {access ? (
          <div role="radiogroup" aria-label="Access method"
               style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
            {option(PROXY, "Standard Access",
                    "Use the default web address without specifying a port.")}
            {option(PORT, "Custom Port",
                    "Use a specific port number. Intended for advanced configurations.")}
            {portField}
          </div>
        ) : portField}
      </div>

      <div style={{ marginTop: 14 }} data-testid="access-address">
        <strong className="small">Current dashboard address</strong>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
          <a className="link-accent" href={data.url} target="_blank" rel="noreferrer"
             style={{ display: "inline-flex", alignItems: "center", gap: 4, minWidth: 0,
                      wordBreak: "break-all" }} data-testid="access-url">
            {data.url}
            <ExternalLink size={13} aria-hidden="true" style={{ flex: "none" }} />
          </a>
          <div style={{ flex: 1 }} />
          <button type="button" className="btn btn-sm btn-ic" onClick={copyUrl}
                  data-testid="access-copy" title="Copy this address">
            <Copy size={13} aria-hidden="true" /> Copy
          </button>
        </div>
      </div>

      <p className="muted small" style={{ marginTop: 12, display: "flex", gap: 6 }}
         data-testid="access-note">
        <Info size={14} aria-hidden="true" style={{ flex: "none", marginTop: 1 }} />
        <span>Changing the access method may require a device restart. Your dashboard
          may be temporarily unavailable while the change takes effect.</span>
      </p>

      <LastResult last={restart.last} what="port" label="port" />
      <LastResult last={restart.last} what="access" label="address" />
      {(pendingAny || moving) && (
        <RestartNote restart={restart} busy={busy} onRestart={async () => {
          if (!await askRestart(confirm, restart)) return;
          restartNow();
        }} moving={moving} />
      )}
      {pendingAny && !moving && applies && (
        <div style={{ marginTop: 6 }}>
          <button type="button" className="btn btn-sm" disabled={!!busy} onClick={keep}>
            Keep {nowWords}
          </button>
        </div>
      )}
      {!applies && (
        <Note><span data-testid="port-not-own-service">{NOT_OWN_SERVICE}</span></Note>
      )}
      {nowMode === PROXY && access?.live === false && (
        <Note><span data-testid="proxy-not-answering">Port 80 is not answering right
          now, so links keep their port until it does. Restarting the device sets it
          up again.</span></Note>
      )}

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12,
                    flexWrap: "wrap" }}>
        <button type="button" className="btn btn-ic" disabled={!valid || !!busy}
                title={proxy ? "Check that this device can answer without a port (port 80)"
                       : valid ? "Check whether this port is free on the device"
                       : "Type a port between 1024 and 65535 first"}
                onClick={test}>
          {busy === "test" ? <Loader2 size={14} className="animate-spin" /> : null}
          Test connection
        </button>
        <button type="button" className="btn btn-accent btn-ic"
                disabled={!valid || unchanged || !!busy || !!moving || !applies
                          || (tested && !tested.usable)}
                title={!applies ? NOT_OWN_SERVICE
                       : unchanged ? (proxy ? "The device already uses Standard Access"
                                            : "This is the port already set")
                       : !valid ? "Type a port between 1024 and 65535 first"
                       : tested && !tested.usable ? (proxy ? "Port 80 cannot be used - see below"
                                                           : "That port is taken - choose another")
                       : "Save for the next restart"}
                onClick={() => save(proxy ? { mode: PROXY } : { mode: PORT, port: n },
                                    !proxy && nowMode === PORT && n === data.running
                                    && !!pendingAny)}>
          {busy === "apply" ? <Loader2 size={14} className="animate-spin" /> : null}
          Apply settings
        </button>
      </div>
      {tested && (
        <div className="small" data-testid="port-tested"
             style={{ marginTop: 6, textAlign: "right",
                      color: tested.usable ? "var(--ok-text)" : "var(--alert-text)" }}>
          {tested.usable ? null : <TriangleAlert size={13} style={{ verticalAlign: -2 }} />}
          {" "}{tested.detail}
        </div>
      )}

      {isDev && (
        <div className="dev-extras" data-testid="access-dev-extras">
          <div className="dev-extras-label">Developer account only</div>
          <div className="muted small" data-testid="port-now">
            Access: <b>{nowWords}</b>.
            {nextWords ? <> After restart: <b>{nextWords}</b></> : null}
          </div>
          {data.source === "argv" && !proxy && (
            <Note>The software was started with <code>--port</code> on the command
              line, which wins over this setting until it is started without it.</Note>
          )}
          <p className="muted small" style={{ marginTop: 6 }}>
            Changing the access port affects the dashboard, sign-in page,
            front-panel links, and system addresses. The change takes effect when
            the device restarts.
          </p>
          {proxy ? (
            <p className="muted small" data-testid="proxy-explain" style={{ marginTop: 6 }}>
              One address with no port: <code>http://&lt;device&gt;/</code> for the
              dashboard and <code>http://&lt;device&gt;{access?.prefix || "/s/"}&lt;system&gt;/</code> for
              each system. Port 80 is fixed for this choice. Bookmarks with a port
              keep working and lead to the new address. Live video may continue to
              use a separate media port.
            </p>
          ) : (
            <p className="muted small" style={{ marginTop: 6 }}>
              The dashboard answers on the port above (default {data.default}); the
              systems keep their own ports{data.systems?.length
                ? ` (${data.systems.map((s) => s.port).join(", ")})` : ""}.
              Existing bookmarks that include the old port number need to be updated.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
