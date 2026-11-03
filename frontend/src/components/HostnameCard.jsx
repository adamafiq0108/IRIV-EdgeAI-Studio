// HostnameCard - the box's hostname on the LAN (<name>.local), on the Network page
// (the card titled "Hostname").
//
// Every box used to be called "iriv", so on a site with two of them
// iriv.local was a race and avahi renamed the losers iriv-2, iriv-3 in boot
// order. New installs are iriv-<last 8 of the serial>; a box that still has
// the shared name gets the suggestion here. Master or developer only, like
// the Software port card beside it (the server answers 403 to anyone else).
//
// Apply SAVES the name; the box takes it the next time it restarts and keeps
// its current name until then. The confirm names the address the dashboard
// will have, and says so when this page was opened by the old .local name
// (the one session a rename drops). One restart also applies a port saved on
// the card above (BoxRestartNote.jsx). A box without the root helper the
// rename needs says, in plain words, that it needs a one-time update by the
// installer - never a command.
//
// Customer view (Tok Aba, 2026-10-14): the "Device hostname" field with its
// hint, "Your device address" (<name>.local, the name the device answers as
// now) with a copy button, one note on the restart, and "Save Device Name".
// The live/pending summary, the longer explanation and the factory-name
// suggestion ("Use iriv-...") are for a developer account only. Restart
// notes, Keep, the last restart's refusal, "Device Name Already in Use", the
// invalid-name line and the needs-installer line stay in sight for everyone.
// Rules, payload, restart prompt and "Keep" are unchanged.
import { useCallback, useEffect, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { useIsDeveloper } from "../state/app.jsx";
import { Copy, Globe, Info, Loader2, Tag, TriangleAlert } from "lucide-react";
import { LastResult, NOT_OWN_SERVICE, Note, RestartNote, announceChange, appliesHere,
         askRestart, restartBox, useRestartEvent } from "./BoxRestartNote.jsx";

const LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

export default function HostnameCard() {
  const isDev = useIsDeveloper();
  const [data, setData] = useState(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState("");
  const [moving, setMoving] = useState(null);
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const d = await api.get("/api/hub/hostname");
      // Anything without a name is not this endpoint's answer: no card.
      if (!d || typeof d.hostname !== "string") { setData({ hidden: true }); return; }
      setData(d);
      setName((cur) => cur || d.pending || d.hostname);
    } catch {
      setData({ hidden: true });          // 403: not this account's card
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useRestartEvent(load);

  if (!data || data.hidden) return null;

  const typed = String(name || "").trim().toLowerCase().replace(/\.local$/, "");
  const valid = LABEL.test(typed) && !/^\d+$/.test(typed) && typed !== "localhost";
  const unchanged = typed === (data.pending || data.hostname);
  const restart = data.restart || {};
  // Only irivai-hub.service applies a saved name; from any other hub Apply
  // is refused by the server and disabled here (BoxRestartNote.jsx).
  const applies = appliesHere(restart);

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

  async function save(value, cancelling) {
    setBusy("apply");
    let d;
    try { d = await api.post("/api/hub/hostname", { name: value }); }
    catch (e) { toast.error("The device did not accept that name", friendlyError(e)); setBusy(""); return; }
    setData(d); setBusy(""); setName(d.pending || d.hostname);
    announceChange();
    if (cancelling) { toast.info("Change cancelled", `The device keeps the name ${d.hostname}.`); return; }
    const r = d.restart || {};
    const via = data.via_name
      ? `You opened this page as ${data.local_name}, which stops working at the restart.` : "";
    if (await askRestart(confirm, r, via)) { await restartNow(); return; }
    toast.info("Device name saved", r.can_reboot
      ? `The device keeps the name ${d.hostname} until it restarts.`
      : "It applies the next time the device is switched off and on.");
  }

  async function copyAddress() {
    try {
      await navigator.clipboard.writeText(data.local_name);
      toast.success("Address copied", data.local_name);
    } catch {
      // A plain-HTTP page may not use the clipboard: the name is selectable.
      toast.info("Select it and copy", "The browser allows the copy button only over HTTPS.");
    }
  }

  return (
    <div className="panel-card" style={{ marginBottom: 14 }} data-testid="name-card">
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Tag size={18} aria-hidden="true" />
        <div style={{ flex: 1 }}>
          <strong>Device Name</strong>
          <div className="muted small" style={{ marginTop: 2 }} data-testid="name-about">
            Give your device a name that's easy to recognize on your network.
          </div>
        </div>
      </div>

      <div className="card-rule">
        {!data.can_set ? (
          <p className="muted small" style={{ margin: 0 }} data-testid="needs-installer">
            This device needs a one-time update by the installer before its name can
            be changed here. Until then it keeps the name {data.hostname}.
          </p>
        ) : (
          <label style={{ display: "block" }}>
            <strong className="small">Device hostname</strong>
            <input className="input" value={name} spellCheck={false} aria-label="Device hostname"
                   style={{ display: "block", width: "100%", minWidth: 0, marginTop: 6,
                            borderRadius: 10, boxSizing: "border-box",
                            ...(name && !valid ? { borderColor: "var(--alert)" } : {}) }}
                   onChange={(e) => setName(e.target.value)} />
            <div className="muted small" style={{ marginTop: 4 }} data-testid="name-hint">
              Use letters, numbers, and hyphens. Avoid spaces.
            </div>
          </label>
        )}
        {data.can_set && name && !valid && (
          <div className="small" style={{ marginTop: 6, color: "var(--alert-text)" }}>
            <TriangleAlert size={13} style={{ verticalAlign: -2 }} /> Use letters a-z,
            digits and "-" only, up to 63 characters, not starting or ending with "-".
          </div>
        )}
      </div>

      <div style={{ marginTop: 14 }} data-testid="name-address">
        <div className="muted small">Your device address</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
          <Globe size={16} aria-hidden="true" style={{ flex: "none" }} />
          <strong style={{ flex: 1, minWidth: 0, wordBreak: "break-all" }}
                  data-testid="name-local">{data.local_name}</strong>
          <button type="button" className="btn btn-icon btn-ghost" onClick={copyAddress}
                  aria-label={`Copy ${data.local_name}`} title="Copy this address"
                  data-testid="name-copy" style={{ width: 28, height: 28 }}>
            <Copy size={14} aria-hidden="true" />
          </button>
        </div>
      </div>

      <p className="muted small" style={{ marginTop: 12, display: "flex", gap: 6 }}
         data-testid="name-note">
        <Info size={14} aria-hidden="true" style={{ flex: "none", marginTop: 1 }} />
        <span>The new name takes effect after the device restarts. Your device may be
          temporarily unavailable during this process.</span>
      </p>

      <LastResult last={restart.last} what="name" label="name" />
      {data.local_name !== `${data.hostname}.local` && (
        <Note>
          <span data-testid="name-in-use"><b>Device Name Already in Use.</b> Another device
            on the network is using <b>{data.hostname}</b>, so this one answers as{" "}
            <b>{data.local_name}</b> instead. Choose a different name to avoid conflicts.</span>
        </Note>
      )}
      {/* The restart note is drawn once: by the port card when a port waits. */}
      {((data.pending && !restart.port && !restart.access) || moving) && (
        <RestartNote restart={restart} busy={busy} onRestart={async () => {
          if (!await askRestart(confirm, restart)) return;
          restartNow();
        }} moving={moving} />
      )}
      {data.pending && !moving && applies && (
        <div style={{ marginTop: 6 }}>
          <button type="button" className="btn btn-sm" disabled={!!busy}
                  onClick={() => save(data.hostname, true)}>
            Keep {data.hostname}
          </button>
        </div>
      )}
      {!applies && (
        <Note><span data-testid="name-not-own-service">{NOT_OWN_SERVICE}</span></Note>
      )}

      {data.can_set && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
          <button type="button" className="btn btn-accent btn-ic" style={{ borderRadius: 999,
                                                                     padding: "8px 18px" }}
                  disabled={!valid || unchanged || !!busy || !!moving || !applies}
                  title={!applies ? NOT_OWN_SERVICE
                         : unchanged ? "This is the name already set"
                         : !valid ? "a-z, 0-9 and -, not starting or ending with -"
                         : "Save the name for the next restart"}
                  onClick={() => save(typed, typed === data.hostname)}>
            {busy === "apply" ? <Loader2 size={14} className="animate-spin" /> : null}
            Save Device Name
          </button>
        </div>
      )}

      {isDev && (
        <div className="dev-extras" data-testid="name-dev-extras">
          <div className="dev-extras-label">Developer account only</div>
          <div className="muted small" data-testid="name-now">
            Answers as <b>{data.local_name}</b>.
            {data.pending ? <> After restart: <b>{data.pending}.local</b></> : null}
          </div>
          <p className="muted small" style={{ marginTop: 4 }}>
            Choose a unique name for this device on your local network. The name
            can be used to reach the device at <code>{data.local_name}:{data.port}</code>,
            and the front panel shows it. A change takes effect after the device restarts.
          </p>
          {data.shared_name && data.local_name === `${data.hostname}.local` && (
            <Note>
              <span><b>{data.hostname}</b> is the factory name, and every other device
                installed the same way has it too - which device answers to {data.hostname}.local
                depends on which one started first. Suggested: <b>{data.default}</b>.</span>
            </Note>
          )}
          {data.can_set && data.shared_name && data.hostname !== data.default && (
            <button type="button" className="btn btn-sm" disabled={!!busy} style={{ marginTop: 6 }}
                    onClick={() => setName(data.default)}>
              Use {data.default}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
