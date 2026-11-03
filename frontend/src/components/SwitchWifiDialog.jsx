// SwitchWifiDialog - access point -> Wi-Fi: which network, and whether it works.
//
// Tok Aba (2026-10-08). While the box broadcasts its access point, "Search
// Wi-Fi" lists the networks around it WITHOUT taking the access point down
// (a scan through the root helper; core/services/wifiscan.py says why that
// works on this radio and a test beside the AP does not). A network is picked
// from the list or typed (hidden ones too), and then:
//
//   Test connection   the box leaves the AP, joins as a trial (password
//                     accepted AND an address from the network's DHCP), and
//                     comes back as the AP; this dialog shows "Wi-Fi
//                     connection is ready" with the address, or the exact
//                     reason, and keeps what was typed for another try.
//   Test and switch   the same trial, but a pass STAYS on the network and the
//                     overlay opens the page at the box's new address; a
//                     failure leaves the hotspot OFF (it is never raised by
//                     itself, Tok Aba 2026-10-14) and, where this page can
//                     still reach the box (a cable), reopens this dialog,
//                     filled in, with the reason.
//
// The AP is off while either runs; the confirm before it says so. A saved
// network (joined before) switches as it always did. A box whose root helper
// predates the trial (an update whose root step has not run) cannot test:
// it offers "Switch without testing", the old join, instead.
import { useEffect, useState } from "react";
import { Loader2, Lock, Search, Wifi } from "lucide-react";
import { api } from "../api/client.js";
import { Dialog } from "./ui/primitives.jsx";
import { PasswordInput } from "./ui/input.jsx";

export const ANY = "";
export const NEW = "__new__";

// The box's own rule for a key (wifi_profile.validate), said before a click.
export function wifiKeyProblem(password) {
  const p = String(password ?? "");
  if (!p) return "";
  if (/^[0-9a-fA-F]{64}$/.test(p)) return "";
  if (p.length < 8) return "A Wi-Fi password has at least 8 characters.";
  if (p.length > 63) return "A Wi-Fi password has at most 63 characters (or 64 hex digits).";
  if (!/^[\x20-\x7e]+$/.test(p)) return "The password can use keyboard characters only.";
  return "";
}

const bad = { color: "var(--danger, #dc2626)", background: "rgba(220,38,38,.08)",
              border: "1px solid rgba(220,38,38,.3)" };
const good = { color: "var(--success, #16a34a)", background: "rgba(22,163,74,.08)",
               border: "1px solid rgba(22,163,74,.3)" };

/**
 * outcome: {ok, message, pick:{ssid,password,hidden}} -- the last test or
 *          failed switch, shown at the top and used to refill the fields.
 * canTest: the box's helper runs trials (status.wifi_test).
 */
export function SwitchWifiDialog({ open, saved, busy, canTest, outcome,
                                   onClose, onGo, onTest }) {
  const [pick, setPick] = useState(ANY);
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [hidden, setHidden] = useState(false);
  const [secure, setSecure] = useState(null);      // from the scan; null = unknown
  const [found, setFound] = useState(null);        // {list, fresh} | null
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState("");
  useEffect(() => {
    if (!open) return;
    const again = outcome && outcome.pick;
    setPick(again || !saved.length ? NEW : ANY);
    setSsid(again ? again.ssid || "" : "");
    setPassword(again ? again.password || "" : "");
    setHidden(!!(again && again.hidden));
    setSecure(null);
    setSearchErr("");
  }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!open) return null;

  const typed = pick === NEW;
  const keyErr = typed ? wifiKeyProblem(password) : "";
  const ready = typed ? !!ssid.trim() && !keyErr && !(secure && !password) : true;
  const body = () => (typed ? { ssid: ssid.trim(), password, ...(hidden ? { hidden: true } : {}) }
    : pick ? { network: pick } : {});
  const go = (e) => {
    e?.preventDefault?.();
    if (ready && !busy) onGo(body());
  };

  async function search() {
    setSearching(true);
    setSearchErr("");
    try {
      const d = await api.post("/api/hub/network/scan", {});
      setFound({ list: d.wifi || [], fresh: d.fresh !== false });
    } catch (e) {
      setSearchErr(e.message || "The search did not work.");
    } finally { setSearching(false); }
  }
  function choose(n) {
    if (n.enterprise) return;
    setPick(NEW);
    if (n.ssid !== ssid) setPassword("");
    setSsid(n.ssid);
    setSecure(!!n.secure);
    setHidden(false);
  }

  const shown = outcome && outcome.message;
  return (
    <Dialog open title="Switch to Wi-Fi" onClose={busy ? undefined : onClose}>
      <form onSubmit={go} style={{ display: "grid", gap: 10 }} data-switch-wifi="1">
        {shown && (
          <div role={outcome.ok ? "status" : "alert"} data-switch-outcome={outcome.ok ? "ok" : "failed"}
               style={{ ...(outcome.ok ? good : bad), padding: "8px 10px", borderRadius: 8,
                        fontSize: 12 }}>
            {outcome.message}
            {!outcome.ok && " Correct it and try again."}
          </div>
        )}
        <label>
          <div className="muted small">Network to join</div>
          <select className="input" value={pick} data-switch-pick="1"
                  onChange={(e) => setPick(e.target.value)}>
            {!!saved.length && <option value={ANY}>Any saved network in range</option>}
            {saved.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            <option value={NEW}>Another network...</option>
          </select>
        </label>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button type="button" className="btn btn-sm" data-wifi-search="1"
                  disabled={busy || searching} onClick={search}>
            {searching ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            {searching ? " Searching..." : " Search Wi-Fi"}
          </button>
          <span className="muted small">
            {found && !found.fresh ? "The box could not scan just now; this is the last list it had."
              : "The access point stays on while the box searches."}
          </span>
        </div>
        {searchErr && <div className="muted small" style={{ color: bad.color }}>{searchErr}</div>}
        {found && (
          <div data-wifi-found="1" style={{ maxHeight: 180, overflowY: "auto", display: "grid",
                                            gap: 2, border: "1px solid var(--border, #3f3f46)",
                                            borderRadius: 8, padding: 4 }}>
            {!found.list.length && <div className="muted small" style={{ padding: 6 }}>
              No network found. A hidden network can be typed under Another network.</div>}
            {found.list.map((n) => (
              <button type="button" key={n.ssid} data-wifi-row={n.ssid}
                      className="btn btn-sm" disabled={busy || n.enterprise}
                      aria-pressed={typed && ssid === n.ssid}
                      title={n.enterprise ? "WPA-Enterprise networks are not supported" : n.ssid}
                      onClick={() => choose(n)}
                      style={{ display: "flex", gap: 8, justifyContent: "flex-start",
                               fontWeight: typed && ssid === n.ssid ? 600 : 400 }}>
                <Wifi size={14} style={{ flex: "none" }} />
                <span style={{ flex: 1, textAlign: "left", overflow: "hidden",
                               textOverflow: "ellipsis" }}>{n.ssid}</span>
                <span className="muted small">
                  {n.enterprise ? "Enterprise (not supported)" : n.secure ? n.security : "Open"}
                </span>
                {n.secure && !n.enterprise && <Lock size={12} style={{ flex: "none" }} />}
                <span className="muted small" style={{ minWidth: 34, textAlign: "right" }}>
                  {n.signal}%</span>
              </button>
            ))}
          </div>
        )}

        {typed && (
          <>
            <label>
              <div className="muted small">Network name</div>
              <input className="input" value={ssid} placeholder="SSID" data-switch-ssid="1"
                     onChange={(e) => { setSsid(e.target.value); setSecure(null); }} />
            </label>
            <label>
              <div className="muted small">
                {secure ? "Password" : "Password (blank for an open network)"}
              </div>
              <PasswordInput native className="input" value={password} autoComplete="off"
                             data-switch-password="1"
                             onChange={(e) => setPassword(e.target.value)} />
              {keyErr && <div className="muted small" style={{ color: bad.color }}>{keyErr}</div>}
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={hidden} data-switch-hidden="1"
                     onChange={(e) => setHidden(e.target.checked)} />
              <span className="small">This network is hidden (it does not broadcast its name)</span>
            </label>
          </>
        )}
        <div className="muted small">
          The switch happens now, with no reboot. If the box cannot join, the
          hotspot does NOT come back by itself: make sure you can reach the box
          another way (cable) or know the Wi-Fi password.
          {typed && canTest && " Test connection tries the password and gets an address, "
            + "then brings the access point back and tells you the result. The access "
            + "point is off while a test runs."}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4,
                      flexWrap: "wrap" }}>
          <button type="button" className="btn btn-sm" disabled={busy} onClick={onClose}>Cancel</button>
          {typed && canTest && (
            <button type="button" className="btn btn-sm" disabled={!ready || busy}
                    data-wifi-test="1" onClick={() => onTest(body())}>
              Test connection
            </button>
          )}
          <button type="submit" className="btn btn-sm btn-accent" disabled={!ready || busy}>
            {!typed ? "Switch to Wi-Fi" : canTest ? "Test and switch" : "Switch without testing"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export default SwitchWifiDialog;
