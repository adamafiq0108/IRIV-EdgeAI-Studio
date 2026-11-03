// NetworkPanel - how this box gets on a network, and how you get on to it.
//
// Commissioning is the case this page exists for: somebody is standing at an
// appliance with a laptop, and the box is either on the site's Wi-Fi, on a
// cable, or broadcasting its own access point. Until this page there was one
// way to change any of that - ssh and `nmcli` - which is not a way at all for
// the person who bought the product.
//
// EVERY CONTROL HERE CAN CUT THE CABLE UNDER THE OPERATOR. So: nothing applies
// implicitly (an interface changes when its own Apply is pressed and not
// before), the interface the browser is actually talking over is marked, and
// the recovery path - forget every network, come back as the access point
// printed on the OLED - is one button rather than a documented procedure.
//
// Customer copy (2026-10-11): "hotspot" for the access point and "device" for
// the box in what the page says, Ethernet cards named "Ethernet Connection N
// (ethN)", gateway and DNS under a collapsible "Advanced Network Settings"
// (opened by itself whenever a value is set or a field is wrong, so a locked
// Apply is never unexplained), and the default hotspot password no longer
// printed on the page: Reset says the default is the one on the device's
// screen. The default itself is unchanged (Tok Aba, 2026-10-07). No control,
// route, timer or default moved; there is no "Test Network Settings" (the
// Test button only checks that the address is free) and the page says so.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { Skeleton } from "./ui/skeleton.jsx";
import SettingsPage from "./ui/SettingsPage.jsx";
import { Dialog } from "./ui/primitives.jsx";
import {
  ChevronDown, ChevronUp, CircleCheck, Loader2, Lock, Network, Plus,
  RadioTower, RefreshCw, RotateCcw, SlidersHorizontal, TriangleAlert, Wifi,
} from "lucide-react";
import { CardHeader, StateSwitch, pillClass } from "./ui/CardHeader.jsx";
import { PasswordInput } from "./ui/input.jsx";
import SoftwarePortCard from "./SoftwarePortCard.jsx";
import HostnameCard from "./HostnameCard.jsx";
import HttpsCard from "./HttpsCard.jsx";
import WifiSwitchOverlay from "./WifiSwitchOverlay.jsx";
import { SwitchWifiDialog } from "./SwitchWifiDialog.jsx";
import { apiUrl } from "../lib/base.js";

const dim = "var(--muted-foreground, #a1a1aa)";

// The access point's name and password, checked the way the box checks them
// (netconf.check_ap_ssid / check_ap_password), so Save is only offered for
// something the box will take. Plain sentences: they are shown under the
// fields as they are. Exported for the jsdom check. The default key,
// iriv1234, is a valid choice like any other (Tok Aba, 2026-10-07).
export function apNameProblem(name) {
  const n = String(name ?? "").trim();
  if (!n) return "The network name cannot be empty.";
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(n)) {
    return "The network name cannot contain tabs or other control characters.";
  }
  if (new TextEncoder().encode(n).length > 32) {
    return "The network name is too long. Wi-Fi allows 32 bytes; accented "
      + "letters and emoji take more than one each.";
  }
  return "";
}
export function apPasswordProblem(password) {
  const p = String(password ?? "");
  if (p.length < 8) return "The password needs at least 8 characters.";
  if (p.length > 63) return "The password can have at most 63 characters.";
  if (!/^[\x20-\x7e]+$/.test(p)) {
    return "The password can use letters, digits, spaces and keyboard symbols "
      + "only (no accented letters or emoji).";
  }
  return "";
}

export default function NetworkPanel() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState("");
  const [listOpen, setListOpen] = useState(true);
  const [picked, setPicked] = useState("");          // the row that is open
  const [autoconnect, setAutoconnect] = useState(true);
  const [joining, setJoining] = useState(null);      // {ssid, secure, hidden} or null
  // A NEW password only: the field starts blank (placeholder) and never
  // shows the stored one (Tok Aba, 2026-10-14). Blank = keep the stored key.
  const [apPassword, setApPassword] = useState("");
  const [apName, setApName] = useState("");
  // Filled in from the box's saved values the first time status arrives (and
  // again after a Save or a Reset), not on every poll: a poll must not wipe
  // what the operator is in the middle of typing.
  const [apSeeded, setApSeeded] = useState(false);
  const [toWifi, setToWifi] = useState(false);       // the AP -> Wi-Fi dialog
  // The last Test connection, or a Test and switch that failed: shown at the
  // top of that dialog, with what was typed put back for another try.
  const [wifiOutcome, setWifiOutcome] = useState(null);
  // The switch under way, drawn over the whole window (WifiSwitchOverlay).
  const [overlay, setOverlay] = useState(null);
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const d = await api.get("/api/hub/network");
      setData(d);
      setApSeeded((seeded) => {
        if (!seeded) {
          if (d.ap_name) setApName(d.ap_name);
        }
        return true;
      });
    } catch (e) {
      toast.error("Could not load the network settings", friendlyError(e));
      setData({ available: false, devices: [], wifi: [], saved: [] });
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  // A static uplink being checked, or waiting on Keep / Revert (Pipi M2,
  // core/services/uplinkguard.py): the page follows it every 2 s, and says
  // each outcome once. The box owns the 60 s timer; this only shows it.
  const checks = (data && data.uplink_check) || {};
  const active = Object.values(checks).some((j) => j.state === "checking" || j.state === "pending");
  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [active, load]);
  const seen = useRef({});
  useEffect(() => {
    for (const [dev, j] of Object.entries(checks)) {
      const was = seen.current[dev];
      seen.current[dev] = j.state;
      if (!was || was === j.state || !["checking", "pending", "reverting"].includes(was)) continue;
      if (j.state === "ok") toast.success("Internet works", j.message);
      else if (j.state === "kept") toast.info("Setting kept", j.message);
      else if (j.state === "reverted") toast.error("Setting put back", j.message);
      else if (j.state === "revert_failed") toast.error("Could not put it back", j.message);
      else if (j.state === "local") toast.info("Machine-only network", j.message);
      else if (j.state === "local_failed") toast.error("Could not change the route", j.message);
    }
  }, [checks, toast]);

  // Every write returns the whole status, so the page redraws from the box's
  // new truth rather than from what it hoped the click did.
  async function act(key, url, body, ok) {
    setBusy(key);
    try {
      const d = await api.post(url, body || {});
      setData(d);
      if (ok) toast.success(ok);
      return d;
    } catch (e) {
      // netconf's refusals are sentences written for this page (an address
      // in use, a gateway off the subnet); friendlyError keeps those and
      // turns anything else into plain words.
      toast.error("The device did not accept that change", friendlyError(e));
      return null;
    } finally { setBusy(""); }
  }

  if (!data) return <Skeleton className="h-64 w-full" />;

  if (!data.available) {
    return (
      <SettingsPage title="Network Settings" icon={Network}
                    description="Connect this device to your network, configure its
                                 network address, and manage secure access to the dashboard.">
        <p className="muted small">
          The network on this device is managed by another program, so Wi-Fi
          and Ethernet cannot be configured from here. Nothing is wrong with
          the connection you are using.
        </p>
        {/* The name is settable too (hostnamectl, not nmcli). */}
        <HostnameCard />
        {/* So is the port: the software's, not NetworkManager's. */}
        <SoftwarePortCard />
        {/* ...and HTTPS (the hub's own listener), right after the port. */}
        <HttpsCard />
      </SettingsPage>
    );
  }

  const devices = data.devices || [];
  const apOn = !!data.ap;                       // what wlan0 IS
  const wifiOn = !!data.radio && !apOn;
  const wantAp = (data.mode || "ap") === "ap";  // what it is SET to be
  const sw = data.switch || {};
  const switching = sw.state === "switching";
  const wlan = devices.filter((d) => d.type === "wifi");
  const wired = devices.filter((d) => d.type !== "wifi");
  // The browser rides on wlan0: on the AP, or on the site Wi-Fi.
  const onAp = !!data.via && wlan.some((x) => x.device === data.via);
  const boxUrl = data.box_url || "";
  const hubPort = boxUrl.split(":").pop() || "5100";
  const apAddr = sw.ap_address || "10.42.0.1";

  // ---- the access point's name and password -------------------------------
  // Save is offered only for a change the box will take. While the AP is
  // broadcasting the change is made NOW (the box restarts the AP on it), so
  // the confirm says who drops off; with the AP off it is only stored.
  const savedName = data.ap_name || "";
  const apNameErr = apNameProblem(apName);
  // Blank is "keep the stored password", never a problem.
  const apKeyErr = apPassword ? apPasswordProblem(apPassword) : "";
  const apDirty = apName.trim() !== savedName || apPassword !== "";
  const apProblem = apDirty ? (apNameErr || apKeyErr) : "";
  const canSaveAp = apDirty && !apNameErr && !apKeyErr;

  function reseedAp(d) {
    if (!d) return;
    setApName(d.ap_name || "");
    setApPassword("");
  }

  async function saveAp() {
    const name = apName.trim();
    if (apOn) {
      // The password field is blank unless a new one was typed (it is then
      // kept as stored), so the confirm names the password the hotspot uses.
      const key = apPassword ? "the new password" : "its current password";
      const ok = await confirm({
        title: "Restart the hotspot now?",
        description: `The hotspot restarts as "${name}" with ${key}. `
          + `Every device on it is disconnected and must join again with ${key}.`
          + (onAp ? " You are connected through this hotspot, so this page "
                    + `will stop responding: join "${name}" with ${key}, `
                    + "then open the page again." : ""),
        confirmLabel: "Save and Restart Hotspot" });
      if (!ok) return null;
    }
    setBusy("apsave");
    try {
      // A blank password is left out: the route keeps the stored one
      // ("either may be left out", core/routes/network.py).
      const d = await api.post("/api/hub/network/ap/save",
                               apPassword ? { ssid: name, password: apPassword } : { ssid: name });
      setData(d);
      reseedAp(d);
      const said = d.ap_save || {};
      if (said.applied === "failed") toast.error("Saved, not applied yet", said.message);
      else toast.success("Hotspot settings saved", said.message || "");
      return d;
    } catch (e) {
      if (apOn && onAp && !e.status) {
        // The reply did not make it: the AP restarted under the request.
        toast.info("Reconnect to the hotspot",
                   `Join "${name}" with the new password, then open this page again.`);
      } else {
        toast.error("Could not save the hotspot settings", friendlyError(e));
      }
      return null;
    } finally { setBusy(""); }
  }

  // The radio's job switches NOW, no reboot (wifiswitch.py, Tok Aba
  // 2026-10-07). The box answers at once with the switch under way and every
  // address it may be at afterwards; the overlay blocks the page until the
  // switch is over, follows it here, or finds the box at one of those
  // addresses and opens the page there (WifiSwitchOverlay.jsx says how).
  // A browser on wlan0 loses this address partway through, so the confirm
  // says that first, and which network to join.
  function follow(body, d) {
    const s = (d && d.switch) || {};
    const toAp = body.on !== false;
    // The reply never came (the radio moved first): what the page already
    // knows stands in for the box's list -- the AP's address, its .local name.
    const fallback = [
      ...(toAp ? [{ host: apAddr, why: "the access point's own address", own: true }] : []),
      ...(boxUrl ? [{ host: boxUrl.split(":")[0], why: "its name on the network", own: false }] : []),
    ];
    setOverlay({
      to: body.test ? "test" : toAp ? "ap" : "wifi",
      pick: body.ssid ? { ssid: body.ssid, password: body.password || "",
                          hidden: !!body.hidden } : null,
      network: body.ssid || body.network
        || (data.saved || []).map((x) => x.name).join(" or "),
      apName: (d && d.ap_name) || data.ap_name,
      apPassword: (d && d.ap_password) || data.ap_password,
      // A test ends back where the page is: nothing to look for elsewhere.
      candidates: body.test ? []
        : s.candidates && s.candidates.length ? s.candidates : fallback,
      boxId: (d && d.box_id) || data.box_id || "",
      budgetS: s.budget_s || 45,
      onWlan: onAp,
    });
  }

  function overlayDone(r) {
    const job = overlay || {};
    setOverlay(null);
    if (r.data) setData(r.data);
    else load();
    // A test, or a Test and switch that failed: back to the dialog, filled
    // in, with the outcome in the box's words.
    if (job.to === "test" || (job.to === "wifi" && job.pick && !r.ok && !r.closed)) {
      setWifiOutcome({ ok: !!r.ok && !r.closed, message: r.message || "", pick: job.pick });
      setToWifi(true);
      return;
    }
    if (r.closed) toast.info("Stopped waiting", r.message);
    else if (r.ok) toast.success("Switched", r.message || "");
    else toast.error("Not switched", r.message || "");
  }

  async function startSwitch(body) {
    setBusy("ap");
    try {
      const d = await api.post("/api/hub/network/ap", body);
      setData(d);
      if ((d.switch || {}).state === "done") toast.success("Nothing to switch", d.switch.message);
      else follow(body, d);
    } catch (e) {
      if (onAp && !e.status) follow(body, null);     // the radio moved before the reply
      else toast.error("Could not switch", friendlyError(e));
    } finally { setBusy(""); }
  }

  // Wi-Fi -> access point, on the saved name and key.
  async function switchToAp() {
    const name = data.ap_name;
    const ok = await confirm({
      title: "Turn on the device hotspot now?",
      description: `The device leaves the Wi-Fi network now and broadcasts "${name}" with `
        + "the password shown in the Device Hotspot section. No reboot; Ethernet is "
        + "not affected."
        + (onAp ? " You are connected over Wi-Fi, so this page will stop responding: "
                  + `join "${name}" with its password, then open http://${apAddr}:${hubPort}`
                  + (boxUrl ? ` or http://${boxUrl}` : "") + "." : "")
        + " If the hotspot cannot start, the device goes back to the Wi-Fi network.",
      confirmLabel: "Switch now" });
    if (!ok) return;
    startSwitch({ on: true });
  }

  // Access point -> Wi-Fi: the dialog picked the network; this says what
  // happens to the page and to the AP before anything moves. The hotspot
  // is never raised by itself (Tok Aba, 2026-10-14: wifimove.py), so this
  // is the one place a person could lock themselves out -- the confirm says
  // so in plain words before anything moves.
  async function switchToWifi(pick) {
    const target = pick.ssid || pick.network || "a saved network in range";
    const ok = await confirm({
      title: "Switch to Wi-Fi now?",
      description: `The device stops broadcasting "${data.ap_name}" now and joins ${target}. `
        + (onAp ? "You are connected through this hotspot, so this page will stop "
                  + `responding: join ${target} on this computer, then open `
                  + `http://${boxUrl || "the device"} - or read the device's new address on its `
                  + "screen (wlan0). "
                : "Devices on the hotspot are disconnected. ")
        + "If it cannot connect, the hotspot will NOT come back by itself. Make sure "
        + "you can reach this device another way (cable) or know the Wi-Fi password.",
      confirmLabel: "Switch now" });
    if (!ok) return;
    setToWifi(false);
    setWifiOutcome(null);
    startSwitch({ on: false, ...pick });
  }

  // Test connection: the AP goes off while the box tries the network, then
  // comes back with the outcome (core/services/wifitest.py). The dialog stays
  // open under the overlay, so nothing typed is lost.
  async function testWifi(pick) {
    const ok = await confirm({
      title: "Test this Wi-Fi network?",
      description: `The device stops broadcasting "${data.ap_name}" while it tries `
        + `${pick.ssid} (up to 45 s): the password, then an address from the network. `
        + "Then the hotspot comes back and this page says whether it worked. "
        + "Nothing is saved. "
        + (onAp ? "You are connected through this hotspot: this computer drops off "
                  + "it meanwhile and normally rejoins it by itself; if it does not, join "
                  + `"${data.ap_name}" again.`
                : "Devices on the hotspot are disconnected meanwhile."),
      confirmLabel: "Test now" });
    if (!ok) return;
    setWifiOutcome(null);
    const body = { ...pick, test: true };
    setBusy("ap");
    try {
      const d = await api.post("/api/hub/network/wifi/test", pick);
      setData(d);
      follow(body, d);
    } catch (e) {
      if (onAp && !e.status) follow(body, null);     // the radio moved before the reply
      else setWifiOutcome({ ok: false, message: e.message, pick });
    } finally { setBusy(""); }
  }

  const recentFail = sw.state === "failed" && sw.finished
    && Date.now() / 1000 - sw.finished < 600;
  // A switch to Wi-Fi that could not join left the hotspot OFF (it is never
  // raised by itself): the note offers the one way back from here.
  const hotspotOff = recentFail && !!sw.hotspot_off && !apOn;
  const switchNote = (switching || recentFail || data.mode_mismatch) && (
    <div data-switch-note="1"
         style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8,
                  padding: "8px 10px", borderRadius: 8, fontSize: 12,
                  background: "rgba(217,119,6,.08)", border: "1px solid rgba(217,119,6,.3)" }}>
      {switching ? <Loader2 size={14} className="animate-spin" style={{ flex: "none" }} />
                 : <TriangleAlert size={14} style={{ flex: "none", color: "var(--warn, #d97706)" }} />}
      <span style={{ flex: 1 }}>
        {switching ? (sw.message || "Switching wlan0 now.")
          : recentFail ? sw.message
          : apOn
            ? <>Currently running on <b>Hotspot</b>. Disable the hotspot to enable
                Wi-Fi mode.</>
            : <>Currently running on <b>Wi-Fi</b>. Disable Wi-Fi to enable the
                hotspot.</>}
      </span>
      {!switching && !recentFail && (
        <button className="btn btn-sm" disabled={!!busy}
                onClick={() => (wantAp ? switchToAp() : setToWifi(true))}>
          Switch now
        </button>
      )}
      {hotspotOff && (
        <button className="btn btn-sm" disabled={!!busy} data-hotspot-on="1"
                onClick={switchToAp}>
          Turn Hotspot On
        </button>
      )}
    </div>
  );

  return (
    <SettingsPage
      title="Network Settings" icon={Network}
      description="Connect this device to your network, configure its network
                   address, and manage secure access to the dashboard."
      actions={
        <button className="btn btn-icon" disabled={!!busy} onClick={load}
                title="Refresh Network Status" aria-label="Refresh Network Status">
          <RefreshCw size={14} />
        </button>
      }>

      {overlay && <WifiSwitchOverlay job={overlay} onDone={overlayDone} />}
      {Object.entries(checks).filter(([, j]) => j.state === "pending").slice(0, 1)
        .map(([dev, j]) => (
          <UplinkKeepDialog key={`${dev}-${j.started}`} device={dev} job={j}
                            busy={busy === `uplink:${dev}`}
                            onKeep={() => act(`uplink:${dev}`, apiUrl("/api/hub/network/ipv4/keep"),
                                              { device: dev })}
                            onRevert={() => act(`uplink:${dev}`, apiUrl("/api/hub/network/ipv4/revert"),
                                                { device: dev })} />
        ))}

      {/* ---- Wi-Fi (client) ---- */}
      {/* One radio, two jobs: joined to a network, or broadcasting one. Two
          cards because they are two decisions -- but ONE switch between them:
          this one, and the AP card's button, both switch wlan0 live.
          Wi-Fi off IS the access point; there is no "both off". */}
      {data.has_wifi && (
        <div className="panel-card" style={{ marginBottom: 14 }}>
          {/* The on/off at the top right with its label left of it
              (ui/CardHeader.jsx, 2026-10-14); the scan button sits before it
              so the switch is the last thing on the row. */}
          <CardHeader icon={Wifi} title="Wi-Fi Connection"
                      subtitle="Connect this device to an existing Wi-Fi network.">
            {switching && <Loader2 size={14} className="animate-spin" aria-label="Switching" />}
            <button className="btn btn-icon" disabled={!!busy || !wifiOn}
                    title="Refresh Wi-Fi Status" aria-label="Refresh Wi-Fi Status"
                    onClick={async () => {
                      setBusy("scan");
                      try {
                        const d = await api.post("/api/hub/network/scan", {});
                        setData((s) => ({ ...s, wifi: d.wifi || [] }));
                      } catch (e) { toast.error("Could not scan for Wi-Fi networks", friendlyError(e)); }
                      finally { setBusy(""); }
                    }}>
              {busy === "scan" ? <Loader2 size={14} className="animate-spin" />
                               : <RefreshCw size={14} />}
            </button>
            <StateSwitch checked={!apOn} disabled={!!busy || switching}
                         aria-label="Wi-Fi"
                         labelProps={{ title: wifiOn ? "On" : apOn ? "Off - device hotspot is on" : "Off" }}
                         title={apOn ? "Switch to Wi-Fi now (no reboot)"
                                     : "Turn Wi-Fi off and the device hotspot on, now (no reboot)"}
                         onCheckedChange={(on) => (on ? setToWifi(true) : switchToAp())} />
          </CardHeader>
          <p className="muted small" style={{ marginTop: 8 }}>
            Changing network settings may disconnect your current session. If
            possible, configure the network through a wired Ethernet connection.
          </p>

          {switchNote}
          <SwitchWifiDialog open={toWifi} saved={data.saved || []} busy={busy === "ap"}
                            canTest={!!data.wifi_test} outcome={wifiOutcome}
                            onClose={() => { setToWifi(false); setWifiOutcome(null); }}
                            onGo={switchToWifi} onTest={testWifi} />

          {wifiOn && (
            <>
              {/* The list works the way the operator's laptop does: a row
                  per network, click one and it opens to a "connect
                  automatically" tick and a Connect button, Connect asks for
                  the password with the name already filled in. The old
                  layout -- a Join button per row that filled a form further
                  down the page -- had people typing the SSID by hand
                  because they never saw the field change. */}
              <div className="wifi-list">
                <button type="button" className="wifi-list-head" aria-expanded={listOpen}
                        onClick={() => setListOpen((v) => !v)}>
                  <RadioTower size={16} style={{ flex: "none" }} />
                  <span style={{ flex: 1 }}>Show available networks</span>
                  <span className="muted small">{(data.wifi || []).length || ""}</span>
                  {listOpen ? <ChevronUp size={16} style={{ color: dim }} />
                            : <ChevronDown size={16} style={{ color: dim }} />}
                </button>
                {listOpen && !(data.wifi || []).length && (
                  <p className="muted small wifi-row" style={{ margin: 0, padding: "10px 12px" }}>
                    Nothing found yet - press the scan button.
                  </p>
                )}
                {listOpen && (data.wifi || []).slice(0, 12).map((n) => {
                  const open = picked === n.ssid;
                  return (
                    <div key={n.ssid} className={"wifi-row" + (open ? " is-open" : "")}>
                      <div className="wifi-row-main" role="button" tabIndex={0}
                           onClick={() => setPicked(open ? "" : n.ssid)}
                           onKeyDown={(e) => {
                             if (e.key === "Enter" || e.key === " ") {
                               e.preventDefault(); setPicked(open ? "" : n.ssid);
                             }
                           }}>
                        <span style={{ position: "relative", flex: "none", display: "inline-flex" }}>
                          <Wifi size={18} style={{ color: n.active ? "var(--accent)" : "inherit" }} />
                          {n.secure && (
                            <Lock size={9} style={{ position: "absolute", right: -3, bottom: -2,
                                                    color: dim }} />
                          )}
                        </span>
                        <span style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ overflow: "hidden", textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" }}>{n.ssid}</div>
                          <div className="wifi-row-sub">
                            {n.active ? "Connected" : n.secure ? "Secured" : "Open"}
                          </div>
                        </span>
                        <span className="muted small tnum">{n.signal}%</span>
                      </div>
                      {open && !n.active && (
                        <div className="wifi-row-extra">
                          <label className="small"
                                 style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <input type="checkbox" checked={autoconnect}
                                   onChange={(e) => setAutoconnect(e.target.checked)} />
                            Connect automatically
                          </label>
                          <div style={{ flex: 1 }} />
                          <button className="btn btn-sm btn-accent" disabled={!!busy}
                                  onClick={() => setJoining({ ssid: n.ssid, secure: n.secure,
                                                              hidden: false })}>
                            Connect
                          </button>
                        </div>
                      )}
                      {open && n.active && (
                        <div className="wifi-row-extra">
                          <span className="muted small">
                            You are on this network. Forget it under Saved Networks
                            to leave it.
                          </span>
                        </div>
                      )}
                    </div>
                  );
                })}
                {listOpen && (
                  <div className="wifi-row">
                    <button type="button" className="wifi-row-main"
                            style={{ width: "100%", background: "none", border: 0,
                                     font: "inherit", color: "inherit", textAlign: "left" }}
                            onClick={() => setJoining({ ssid: "", secure: true, hidden: true })}>
                      <Plus size={16} style={{ color: dim }} />
                      <span style={{ flex: 1 }}>
                        <div>Hidden network</div>
                        <div className="wifi-row-sub">Type the name of a network that does not broadcast it</div>
                      </span>
                    </button>
                  </div>
                )}
              </div>

              <JoinDialog target={joining} busy={busy === "join"}
                          autoconnect={autoconnect}
                          onClose={() => setJoining(null)}
                          onJoin={(ssid, password, hidden) =>
                            act("join", apiUrl("/api/hub/network/join"),
                                { ssid, password, hidden, autoconnect },
                                `Joined ${ssid}`).then((d) => {
                                  if (d) { setJoining(null); setPicked(""); }
                                })} />

              {/* ---- saved networks ---- */}
              {/* Off the PROFILE list, not the scan: the network to forget
                  is usually the one from the last site -- out of range here,
                  so never in the scan, and the one the box keeps trying. The
                  connected one can be forgotten too; the confirm says what
                  that costs when you are on it. */}
              {!!(data.saved || []).length && (
                <>
                  <div className="muted small" style={{ marginTop: 12, fontWeight: 600 }}>
                    Saved Networks
                  </div>
                  <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
                    {(data.saved || []).map((c) => (
                      <div key={c.name}
                           style={{ display: "flex", alignItems: "center", gap: 10,
                                    border: "1px solid var(--line, rgba(128,128,128,.22))",
                                    borderRadius: 8, padding: "8px 10px" }}>
                        <Wifi size={13} style={{ flex: "none", color: dim }} />
                        <span style={{ flex: 1, minWidth: 0, overflow: "hidden",
                                       textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {c.name}
                          {c.active && <span className="muted small"> · connected</span>}
                        </span>
                        <button className="btn btn-sm" disabled={!!busy}
                                onClick={async () => {
                                  if (!await confirm({
                                    title: `Forget "${c.name}"?`,
                                    description: c.active
                                      ? "You are connected through it. The device drops "
                                        + "off this network now and will not rejoin it "
                                        + "on its own - make sure you can still reach "
                                        + "the device another way (Ethernet, or the device hotspot)."
                                      : "Its saved password goes with it. The device will "
                                        + "not rejoin this network on its own; you can "
                                        + "join it again from the list with the password.",
                                    confirmLabel: "Forget" })) return;
                                  act("forget", apiUrl("/api/hub/network/forget"),
                                      { name: c.name }, `Forgot ${c.name}`);
                                }}>
                          {busy === "forget" ? <Loader2 size={14} className="animate-spin" /> : null}
                          Forget
                        </button>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {/* ---- this radio's address ---- */}
              {/* The wlan0 IPv4 settings live with the network they are
                  for, not in a card of their own further down the page. */}
              {wlan.map((d) => (
                <div key={d.device}
                     style={{ marginTop: 14, paddingTop: 12,
                              borderTop: "1px solid var(--line, rgba(128,128,128,.22))" }}>
                  <InterfaceCard device={d} busy={busy} act={act}
                                 via={data.via === d.device} embedded
                                 uplinkElsewhere={(data.devices || [])
                                   .filter((o) => o.device !== d.device && o.has_default)
                                   .map((o) => o.device)}
                                 rebootPending={(data.ipv4_reboot || []).includes(d.device)} />
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {/* ---- access point ---- */}
      {/* Customer view (Tok Aba, 2026-10-14, the template for the page's
          on/off switches): the hotspot's On / Off switch at the top right
          (the same live switch the old Turn On / Turn Off Hotspot buttons
          made, with the same confirms and guards), "Hotspot is enabled",
          the name and a new password (never the current one: the field is
          blank with a placeholder, and a blank password is left out of the
          save so the stored one is kept), Save Changes and Reset Settings.
          The longer explanations are for a developer account only. */}
      {data.has_wifi && (
        <div className="panel-card" style={{ marginBottom: 14 }} data-ap-card="1">
          <CardHeader icon={Wifi} title="Device Hotspot"
                      subtitle="Allow phones and computers to connect directly to this device.">
            {switching && <Loader2 size={14} className="animate-spin" aria-label="Switching" />}
            <StateSwitch checked={apOn} aria-label="Device hotspot"
                         disabled={!!busy || switching || (!apOn && (!!apNameErr || !!apKeyErr))}
                         title={apOn ? "Turn the hotspot off and join a Wi-Fi network now (no reboot)"
                           : apNameErr || apKeyErr
                             || "Broadcast this network from the device now (no reboot)"}
                         onCheckedChange={async (on) => {
                           if (!on) { setToWifi(true); return; }
                           // Unsaved edits are saved first, so the AP comes up on
                           // what the fields say and the screen prints the same.
                           if (apDirty && !await saveAp()) return;
                           switchToAp();
                         }} />
          </CardHeader>
          {switchNote}

          {apOn && (
            <div className="card-rule" data-ap-on="1">
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <CircleCheck size={16} style={{ color: "var(--ok-text)", flex: "none" }}
                             aria-hidden="true" />
                <strong>Hotspot is enabled</strong>
              </div>
              <p className="muted small" style={{ margin: "4px 0 0" }}>
                Nearby devices can connect using the network name and password below.
              </p>
            </div>
          )}

          <div className="card-rule">
            <label style={{ display: "block" }}>
              <strong className="small">Network name (SSID)</strong>
              <input className="input" value={apName} data-ap-name="1"
                     maxLength={64} autoComplete="off" spellCheck={false}
                     aria-invalid={apDirty && !!apNameErr}
                     style={{ display: "block", width: "100%", minWidth: 0, marginTop: 6,
                              borderRadius: 10, boxSizing: "border-box" }}
                     title={`The setup name is ${data.ap_name_factory || savedName}; `
                       + "Reset Settings brings it back"}
                     onChange={(e) => setApName(e.target.value)} />
              <div className="muted small" style={{ marginTop: 4 }}>
                The Wi-Fi name shown when connecting to this device.
              </div>
            </label>
            <label style={{ display: "block", marginTop: 12 }}>
              <strong className="small">Hotspot password</strong>
              <div style={{ marginTop: 6 }}>
                <PasswordInput native className="input" value={apPassword}
                       autoComplete="new-password" data-ap-password="1"
                       placeholder="Enter a new password"
                       aria-invalid={!!apPassword && !!apKeyErr}
                       style={{ width: "100%", minWidth: 0, borderRadius: 10,
                                boxSizing: "border-box" }}
                       onChange={(e) => setApPassword(e.target.value)} />
              </div>
              <div className="muted small" style={{ marginTop: 4 }}>
                At least 8 characters.
              </div>
            </label>
            {apProblem && (
              <p className="small" data-ap-problem="1" role="status"
                 style={{ margin: "6px 0 0", color: "var(--warn-text)" }}>
                {apProblem}
              </p>
            )}
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
            <button className="btn btn-accent btn-ic" data-ap-save="1"
                    disabled={!!busy || !canSaveAp}
                    title={!apDirty ? "Nothing changed yet"
                      : apProblem || (apOn ? "Save and restart the hotspot now"
                                           : "Save for the next time the hotspot is on")}
                    onClick={() => { saveAp(); }}>
              {busy === "apsave" ? <Loader2 size={14} className="animate-spin" /> : null}
              Save Changes
            </button>
            {/* ---- recovery ---- */}
            {/* The default password is NOT written here (copy spec 2026-10-11:
                no shared default in customer help). The device's own screen
                shows the hotspot name and password. The default is unchanged. */}
            <button className="btn btn-danger btn-ic" disabled={!!busy} data-ap-reset="1"
                    style={{ color: "var(--alert-text)" }}
                    title="Forget every saved Wi-Fi network and broadcast the setup hotspot"
                    onClick={async () => {
                      // A confirm, because this is the one control on the
                      // page that throws away configuration rather than
                      // adding some -- and it is usually pressed by
                      // somebody who has just lost their way in.
                      const setup = data.ap_name_factory || data.ap_name;
                      if (!await confirm({
                        title: "Reset Hotspot Settings?",
                        description: `This device comes back as ${setup}, `
                          + "the setup hotspot, with the default password shown "
                          + "on the device's screen. Every saved Wi-Fi network is "
                          + "forgotten, and a name and password you set are replaced; "
                          + "devices that joined with them must rejoin."
                          + (apDirty ? " Unsaved changes above are discarded." : ""),
                        destructive: true,
                        confirmLabel: "Reset Hotspot Settings" })) return;
                      // No password sent: the box stores the default key
                      // first, so the radio never broadcasts one the screen
                      // does not show.
                      const d = await act("reset", apiUrl("/api/hub/network/reset"), {},
                                          "Hotspot settings reset - the setup hotspot is on");
                      reseedAp(d);
                    }}>
              <RotateCcw size={14} aria-hidden="true" /> Reset Settings
            </button>
          </div>

        </div>
      )}

      {/* ---- one card per wired interface ---- */}
      {wired.map((d) => (
        <InterfaceCard key={d.device} device={d} busy={busy} act={act}
                       via={data.via === d.device}
                       uplinkElsewhere={(data.devices || []).filter((o) => o.device !== d.device
                                                                          && o.has_default)
                                                             .map((o) => o.device)}
                       rebootPending={(data.ipv4_reboot || []).includes(d.device)} />
      ))}

      {/* The Hostname card: master. */}
      <HostnameCard />
      {/* ---- the software's own port ---- */}
      {/* Set once, if ever. Draws itself only for master or a
          developer (the server answers 403 to anyone else). */}
      <SoftwarePortCard />
      {/* HTTPS with the box's own certificate, directly after the port. */}
      <HttpsCard />
    </SettingsPage>
  );
}

// Access point -> Wi-Fi: which network, searched for with the access point
// still up, and tested before the box is left on it -- SwitchWifiDialog.jsx.
// The confirm that follows knows where the page is and says what happens.
export { SwitchWifiDialog } from "./SwitchWifiDialog.jsx";
// "Internet not reachable through this port. Keep this setting?" -- asked
// when a static uplink was applied and the box could not reach the internet
// through it (uplinkguard.py). The countdown is the BOX's: it puts the old
// settings back by itself when it runs out, page open or not, so closing
// this is not an answer and there is no close button. Exported for the check.
export function UplinkKeepDialog({ device, job, busy, onKeep, onRevert }) {
  const [until] = useState(() => Date.now() + (job.remaining_s ?? 60) * 1000);
  const [left, setLeft] = useState(job.remaining_s ?? 60);
  useEffect(() => {
    const t = setInterval(() => setLeft(Math.max(0, Math.round((until - Date.now()) / 1000))), 1000);
    return () => clearInterval(t);
  }, [until]);
  return (
    <Dialog open title="Internet not reachable through this port"
            footer={(
              <>
                <button type="button" className="btn btn-sm" disabled={busy} onClick={onRevert}>
                  Revert now
                </button>
                <button type="button" className="btn btn-sm btn-accent" disabled={busy}
                        onClick={onKeep}>
                  Keep
                </button>
              </>
            )}>
      <div data-uplink-keep="1" style={{ display: "grid", gap: 8, fontSize: 13 }}>
        <div>
          The device could not reach the internet through <b>{device}</b>
          {job.gateway ? <> (gateway <b>{job.gateway}</b>)</> : null}. Keep this setting?
        </div>
        <div>
          Without an answer, <b>{device}</b> goes back to its previous settings in
          {" "}<b data-uplink-left="1">{left}</b> s - the device does this by itself, even if
          this page loses the connection.
        </div>
      </div>
    </Dialog>
  );
}

// The connect step: name filled in from the row that was clicked (still
// editable, for the hidden-network path where nothing was clicked), password
// typed here. Connect goes blue the moment both are in -- on an open network,
// the moment the name is.
function JoinDialog({ target, busy, autoconnect, onClose, onJoin }) {
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  // Reseeded per target: the dialog is one component reused for every row,
  // and a password typed for one network must not carry into the next.
  useEffect(() => {
    setSsid(target?.ssid || ""); setPassword(""); setShow(false);
  }, [target]);
  if (!target) return null;
  const needsPassword = target.secure !== false;
  const ready = !!ssid.trim() && (!needsPassword || !!password);
  const submit = (e) => { e?.preventDefault?.(); if (ready && !busy) onJoin(ssid.trim(), password, !!target.hidden); };
  return (
    <Dialog open title={target.hidden ? "Join a hidden network" : `Connect to ${target.ssid}`}
            onClose={busy ? undefined : onClose}>
      <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
        <label>
          <div className="muted small">Network name</div>
          <input className="input" value={ssid} placeholder="SSID" autoFocus={!!target.hidden}
                 onChange={(e) => setSsid(e.target.value)} />
        </label>
        <label>
          <div className="muted small">
            {needsPassword ? "Password" : "Password (this network is open)"}
          </div>
          <PasswordInput native className="input" value={password}
                         shown={show} onToggle={() => setShow((v) => !v)}
                         autoFocus={!target.hidden} autoComplete="off"
                         placeholder={needsPassword ? "network password" : "leave blank"}
                         onChange={(e) => setPassword(e.target.value)} />
        </label>
        <div className="muted small">
          {autoconnect ? "The device will rejoin this network by itself."
                       : "Connect once - the device will not rejoin this network on its own."}
          {" "}Joining can drop the connection you are using.
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button type="button" className="btn btn-sm" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-sm btn-accent" disabled={!ready || busy}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : null}
            {busy ? "Connecting…" : "Connect"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

// One interface: what it is doing, and its IPv4.
//
// The form is local state seeded from the device, NOT a controlled mirror of
// it: this card repolls, and a poll landing while somebody is halfway through
// typing an address must not rewrite the field under them.
//
// `embedded`: drawn as a section inside another card (the Wi-Fi card holds
// wlan0) rather than as a card of its own.
//
// Static is two steps in order: switch DHCP off, THEN the address fields
// appear, and Apply stays locked until the address is a valid one. Applying
// with a half-typed address broke the interface (NetworkManager refuses the
// profile, the old lease is gone), so the button will not let it happen.
//
// On a CONNECTED interface a fixed address goes on at once, and then the
// page asks Reboot now / Later (the Wi-Fi / AP switch needs no reboot at all):
// every service only reliably takes the new address after a restart, but
// "now" is not always when the operator can stop the line. Later leaves a
// note on the card until the box next boots.
//
// A wired jack with NOTHING on it (no cable -- the usual state of eth0/eth1
// on a box being commissioned over its own access point) can still be given
// its address in advance. Beta feedback: the card used to lock with "connect
// a cable first", and an installer setting a box up on the bench wants the
// address ready before it goes into the panel. The server writes a profile
// bound to that jack and leaves it down (netconf.set_ipv4, PRESET); nothing
// on the running box changes, so the button is Save and there is no reboot.
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

// The three fields every other device on an installer's bench asks for, in
// the words those devices use: IP Address, Subnet Mask, Default Gateway.
// A prefix length ("24") is what NetworkManager wants and
// what this form used to ask for; it is not what is written on the sheet the
// site hands over, and an installer who types 255.255.255.0 into a box
// labelled "Prefix" has done nothing wrong. The mask is translated on both
// sides -- here for the form, and in netconf.mask_to_prefix for anything
// that arrives at the server another way.
const MASKS = [
  "255.255.255.0", "255.255.254.0", "255.255.252.0", "255.255.248.0",
  "255.255.240.0", "255.255.224.0", "255.255.192.0", "255.255.128.0",
  "255.255.0.0", "255.0.0.0", "255.255.255.128", "255.255.255.192",
  "255.255.255.224", "255.255.255.240", "255.255.255.248", "255.255.255.252",
];

/** 255.255.255.0 -> 24. "" when it is not a mask (ones then zeros, no gaps). */
function maskToPrefix(value) {
  const text = String(value || "").trim();
  if (!IPV4.test(text)) return "";
  let bits = 0;
  let zeroSeen = false;
  for (const part of text.split(".")) {
    for (let i = 7; i >= 0; i -= 1) {
      if (Number(part) & (1 << i)) {
        if (zeroSeen) return "";
        bits += 1;
      } else {
        zeroSeen = true;
      }
    }
  }
  return String(bits);
}

/** 24 -> 255.255.255.0, for showing back what the profile already holds. */
function prefixToMask(value) {
  const n = Number(String(value || "").trim());
  if (!Number.isInteger(n) || n < 0 || n > 32) return "";
  const out = [];
  for (let byte = 0; byte < 4; byte += 1) {
    const take = Math.min(8, Math.max(0, n - byte * 8));
    out.push(256 - 2 ** (8 - take));
  }
  return out.join(".");
}

/** Same network? Both addresses under the same prefix length. */
function sameSubnet(a, b, prefix) {
  const n = (x) => x.split(".").reduce((acc, part) => acc * 256 + Number(part), 0);
  const bits = Number(prefix);
  if (!Number.isInteger(bits) || bits < 0 || bits > 32) return true;
  const size = 2 ** (32 - bits);
  return Math.floor(n(a) / size) === Math.floor(n(b) / size);
}

// What is wrong with the gateway, in the words under the field; "" if
// nothing. The server checks the same and more (netaddr.refuse_bad_host, the
// other ports' addresses): this is so the operator is told while typing.
// Blank is always fine: since 2026-10-09 the box decides which port is the
// internet uplink (netuplink.auto_role) and a port with no gateway is never
// it. Exported for the jsdom check.
export function gatewayProblem(ip, prefix, gateway) {
  const gw = String(gateway || "").trim();
  const me = String(ip || "").trim();
  if (!gw) return "";
  if (!IPV4.test(gw)) return "The gateway is not an IPv4 address.";
  if (gw === me) {
    return "The gateway cannot be this device's own address. It must be the router on "
      + "that network.";
  }
  if (IPV4.test(me) && prefix !== "" && !sameSubnet(me, gw, prefix)) {
    return `The gateway is outside ${me}/${prefix}, so nothing on that network can `
      + "reach it. Check the gateway and the subnet mask.";
  }
  return "";
}

// What is wrong with the DNS field, or "". The same rules as the server's
// netaddr.dns_servers: commas or spaces between, IPv4 only, no address that
// no resolver can be at, at most three (the box never asks a fourth).
// Exported for the jsdom check.
export function dnsProblem(text) {
  const parts = String(text || "").split(/[,\s]+/).filter(Boolean);
  for (const p of parts) {
    if (!IPV4.test(p) || /(^|\.)0\d/.test(p)) return `${p} is not an IPv4 address.`;
    const first = Number(p.split(".")[0]);
    if (p === "0.0.0.0" || p === "255.255.255.255" || first === 127 || first >= 224) {
      return `${p} cannot be a DNS server. Use a resolver's address, such as 8.8.8.8.`;
    }
  }
  if (new Set(parts).size > 3) return "At most three DNS servers.";
  return "";
}

/** What a card is called: "Ethernet Connection 1 (eth0)" for eth0, the
 *  Wi-Fi radio's address section by what it is, anything else by its name. */
export function interfaceTitle(device, embedded = false) {
  const dev = device.device || "";
  if (embedded) return `Wi-Fi IP Address (${dev})`;
  const eth = /^eth(\d+)$/.exec(dev);
  return eth ? `Ethernet Connection ${Number(eth[1]) + 1} (${dev})` : dev;
}

/** A required field's label, marked the way every setup form marks one. */
function Req({ children }) {
  return (
    <div className="muted small">
      <span style={{ color: "var(--alert, #dc2626)" }}>*</span> {children}
    </div>
  );
}

function InterfaceCard({ device, busy, act, via, embedded = false, rebootPending = false,
                         uplinkElsewhere = [] }) {
  const confirm = useConfirm();
  const toast = useToast();
  const [dhcp, setDhcp] = useState(device.dhcp !== false);
  // The static address is what was SET, if anything was; switching DHCP off
  // fills the blanks from what the interface HAS (see takeStatic below).
  const [ip, setIp] = useState(device.static_ip || "");
  // Held as a MASK, which is what the field asks for; the prefix the server
  // wants is derived at the moment it is sent. The prefix box beside it is
  // the same number said the other way -- some sites hand out /24, some hand
  // out 255.255.255.0, and an installer should not have to convert either.
  // Each box keeps its own text while it is being typed and writes the other
  // one the moment what was typed makes sense, so half of "255.255." never
  // rewrites the prefix to something the operator did not choose.
  const [mask, setMask] = useState(
    prefixToMask(device.static_prefix || device.prefix || "24") || "255.255.255.0");
  const [prefixText, setPrefixText] = useState(
    String(device.static_prefix || device.prefix || "24"));
  const [gateway, setGateway] = useState(device.static_gateway || "");
  // DNS servers of the operator's own (Tok Aba, 2026-10-09), on a fixed
  // address or on DHCP. What the profile holds: a fixed one always names its
  // servers (the ones carried over when the field was left blank); a DHCP one
  // only when it overrides the lease's (dns_override), else blank.
  const savedDns = device.dhcp === false || device.dns_override ? (device.static_dns || "") : "";
  // Two boxes, Preferred and Alternate, as Windows lays them out (Tok Aba,
  // 2026-10-09); the server still takes one comma list, so they are joined.
  const savedPair = savedDns.split(/[,\s]+/).filter(Boolean);
  const [dns1, setDns1] = useState(savedPair[0] || "");
  const [dns2, setDns2] = useState(savedPair[1] || "");
  // Advanced Network Settings starts open when it holds something already.
  const [advOpen, setAdvOpen] = useState(
    () => !!(device.static_gateway || savedPair.length));
  const dns = [dns1.trim(), dns2.trim()].filter(Boolean).join(", ");
  const dns1Bad = dnsProblem(dns1);
  const dns2Bad = dnsProblem(dns2)
    || (dns2.trim() && dns2.trim() === dns1.trim() ? "Same as the preferred server." : "");
  // No "this port is the internet uplink" tick any more (it was there 10-07
  // to 10-09): the box decides, by the gateway and by which port reaches the
  // internet (netuplink.auto_role).
  // What the Test button last found out about the address in the field:
  // null before it was asked, then {in_use, checked, detail} for THAT text,
  // so editing the address clears the answer rather than leaving a verdict
  // standing over a different address.
  const [tested, setTested] = useState(null);
  const [testing, setTesting] = useState(false);

  const connected = /connected/i.test(device.state || "")
                    && !/disconnected/i.test(device.state || "");
  const wired = device.type === "ethernet";
  // NetworkManager leaves an "unmanaged" interface alone, so a profile saved
  // for it would never be used -- that one stays locked, and says why.
  const unmanaged = /^unmanaged/i.test(device.state || "");
  // Nothing active on a wired jack: set now, applied when a cable goes in.
  const preset = !connected && wired && !unmanaged;
  const key = "ipv4:" + device.device;
  const ipOk = IPV4.test(ip.trim());
  const prefix = maskToPrefix(mask);
  const gwProblem = gatewayProblem(ip, prefix, gateway);
  const dnsBad = dns1Bad || dns2Bad;
  // On DHCP the only thing Apply can change is the DNS override.
  const dnsChanged = dns.split(/[,\s]+/).filter(Boolean).join(",")
    !== savedDns.split(/[,\s]+/).filter(Boolean).join(",");
  const gwOk = !gwProblem;
  const maskOk = prefix !== "";
  const prefixOk = prefixToMask(prefixText) !== "";

  function typeMask(text) {
    setMask(text);
    const derived = maskToPrefix(text);
    if (derived !== "") setPrefixText(derived);
  }

  function typePrefix(text) {
    setPrefixText(text);
    const derived = prefixToMask(text);
    if (derived !== "") setMask(derived);
  }

  // A conflict found for the address in the field blocks Apply until the
  // address is changed (editing it clears `tested`).
  const conflict = !!(tested && tested.in_use);
  const staticReady = ipOk && gwOk && maskOk && !dnsBad && !conflict;

  /** Ask the box whether another device answers for this address
   *  (ipconflict.py). The Test button, and Apply before it confirms. */
  async function testAddress() {
    setTesting(true);
    try {
      const d = await api.post("/api/hub/network/ipv4/test",
                               { ip: ip.trim(), device: device.device });
      setTested(d);
      return d;
    } catch (e) {
      toast.error("Could not test the address", friendlyError(e));
      return null;
    } finally {
      setTesting(false);
    }
  }

  /** Switching DHCP off starts from what this interface is using NOW.
   *
   *  Every one of these boards is already on the network when somebody
   *  decides to pin its address, and the address they want is almost always
   *  the one it already has, or that address with one octet changed. Starting
   *  from three empty boxes made the operator copy the lease out of the line
   *  above by hand, which is where a typo comes from.
   *
   *  Only blanks are filled: an address typed a moment ago, or one already
   *  saved in the profile, is the operator's and is left alone. It is a
   *  starting point, not a decision -- nothing is applied until Apply, and
   *  the confirmation names the address it is about to take.
   */
  function takeStatic(next) {
    setDhcp(next);
    if (next) return;
    setIp((cur) => cur || device.static_ip || device.ip || "");
    setMask((cur) => cur
      || prefixToMask(device.static_prefix || device.prefix || "24")
      || "255.255.255.0");
    setPrefixText((cur) => cur || String(device.static_prefix || device.prefix || "24"));
    setGateway((cur) => cur || device.static_gateway || device.gateway || "");
    // A gateway carried over is a value the operator should see.
    if (gateway || device.static_gateway || device.gateway) setAdvOpen(true);
    setTested(null);
  }

  // Full-width rounded fields, as the Network page draws them since the
  // 2026-10-14 pass.
  const field = { display: "block", width: "100%", minWidth: 0, boxSizing: "border-box",
                  borderRadius: 10, marginTop: 6 };
  const advForced = !dhcp || !!dns || !!dnsBad;
  // The section is opened from here, never held open by a prop: a <details>
  // whose `open` React controls, fed back through onToggle, locked the whole
  // page in a render loop the moment somebody clicked its summary (found
  // 2026-10-14 in Chrome with the card rendered on its own; the old card had
  // the same wiring). So it is a plain <details>, and this opens it whenever
  // it holds something, a mistake, or DHCP is switched off.
  const advRef = useRef(null);
  useEffect(() => {
    const el = advRef.current;
    if (el && (advOpen || advForced) && !el.open) el.open = true;
  }, [advOpen, advForced]);

  async function save() {
    if (preset) {
      // Nothing is live on this jack: no confirm, no reboot.
      await act(key, apiUrl("/api/hub/network/ipv4"),
          { device: device.device, dhcp, ip, mask, gateway, dns },
          dhcp ? `${device.device} will use DHCP when a cable is connected`
               : `${device.device} will use ${ip.trim()} when a cable is connected`);
      return;
    }
    if (!dhcp) {
      // The same conflict check as Test, before anything
      // is asked or written (the server asks again).
      const t = await testAddress();
      if (t && t.in_use) {
        toast.error("IP conflict", `${t.detail}. Pick another address.`);
        return;
      }
      if (!await confirm({
        title: `Set ${device.device} to ${ip.trim()} / ${mask}?`,
        description: "The address goes on now. Reach the device at "
          + `${ip.trim()} on this interface` + (via
            ? " - this page drops now." : "."),
        confirmLabel: "Apply" })) return;
    } else if (via && !await confirm({
      title: `Apply this to ${device.device}?`,
      description: "You are connected through it, so applying "
        + "this may drop the page.",
      confirmLabel: "Apply anyway" })) return;
    const d = await act(key, apiUrl("/api/hub/network/ipv4"),
        { device: device.device, dhcp, ip, mask, gateway, dns },
        dhcp ? `${device.device} updated`
             : `${device.device} set to ${ip.trim()}`);
    if (d && !dhcp && ((d.uplink_check || {})[device.device] || {}).state
                      === "checking") {
      // The box is checking the internet through this
      // port (Pipi M2). A reboot now would end the check
      // and keep the setting unproven; the card's reboot
      // note offers it once the check is over.
      toast.info("Checking the internet",
                 `${device.device} is set. The device is checking that the `
                 + "internet answers through it.");
      return;
    }
    if (d && !dhcp) {
      // The same choice the Wi-Fi / AP switch gives.
      const now = await confirm({
        title: "Reboot now?",
        description: `${device.device} has its new address. Every service `
          + "picks it up when the device restarts. Reboot now, or later - "
          + "the device keeps running, and this card says a reboot is "
          + "pending until it happens.",
        confirmLabel: "Reboot now", cancelLabel: "Later" });
      if (!now) {
        toast.info("Address set", "Every service picks it up at the next reboot.");
        return;
      }
      try {
        await api.post("/api/hub/panel/power", { action: "reboot" });
        toast.success("Rebooting", `Reach the device at ${ip.trim()} once it is back.`);
      } catch (e) { toast.error("Address set, but the reboot was refused", friendlyError(e)); }
    }
  }

  return (
    <div className={embedded ? "" : "panel-card"} style={embedded ? {} : { marginBottom: 12 }}
         data-net-card={device.device}>
      {/* Customer view (Tok Aba, 2026-10-14): the title with Connected / Not
          connected, "Automatic IP address (DHCP)" with its On / Off switch,
          the current address, "Advanced Network Settings" (address, mask,
          gateway, DNS) and Save Network Settings. The connection profile,
          the lease detail, the DNS in use, the Internet badge and the
          longer field help are for a developer account only. */}
      <CardHeader icon={wired ? Network : Wifi} title={interfaceTitle(device, embedded)}
                  subtitle={wired ? "Wired network connection" : "Wireless network connection"}>
        <span className={pillClass(connected ? "on" : "off")} data-net-state="">
          {connected ? "Connected" : "Not connected"}
        </span>
      </CardHeader>

      {via && (
        // The one fact the browser cannot work out for itself: this is the
        // interface carrying this very page.
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 8,
                      color: "var(--warn-text)", fontSize: 12 }} data-via-warning="1">
          <TriangleAlert size={14} style={{ flex: "none", marginTop: 1 }} />
          <span>
            <b>You Are Connected Through This {wired ? "Port" : "Connection"}</b>
            <span style={{ display: "block" }}>
              Changing these settings may disconnect your current session. Make
              sure you can reconnect before applying changes.
            </span>
          </span>
        </div>
      )}

      {(device.shares_subnet_with || []).length > 0 && (
        // netconf reports two LIVE wired ports on one network (netaddr.
        // shared_subnets). A write is refused before it gets here; two DHCP
        // leases from one switch are not, so this is the only place to say so.
        // A warning, not a block: unplugging one cable is the usual fix.
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 8,
                      padding: "8px 10px", borderRadius: 8, fontSize: 12,
                      background: "rgba(217,119,6,.08)", border: "1px solid rgba(217,119,6,.3)" }}>
          <TriangleAlert size={14} style={{ flex: "none", marginTop: 1,
                                            color: "var(--warn-text)" }} />
          <span style={{ flex: 1 }}>
            <b>{device.device}</b> is on the same network as{" "}
            <b>{device.shares_subnet_with.join(", ")}</b>. With both cables in, the
            device answers on only one of its addresses, and which one is luck. Unplug
            one of them, or give one a fixed address on a different network.
          </span>
        </div>
      )}

      {rebootPending && connected && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8,
                      padding: "8px 10px", borderRadius: 8, fontSize: 12,
                      background: "rgba(217,119,6,.08)", border: "1px solid rgba(217,119,6,.3)" }}>
          <TriangleAlert size={14} style={{ flex: "none", color: "var(--warn-text)" }} />
          <span style={{ flex: 1 }}>
            New address set on <b>{device.device}</b> - every service picks it
            up at the next reboot.
          </span>
          <button className="btn btn-sm" disabled={!!busy}
                  onClick={async () => {
                    if (!await confirm({ title: "Reboot now?",
                                         description: "Every running system stops and comes back "
                                           + "with it, which takes about a minute.",
                                         confirmLabel: "Reboot now" })) return;
                    api.post("/api/hub/panel/power", { action: "reboot" })
                      .then(() => toast.success("Rebooting"))
                      .catch((e) => toast.error("Could not reboot", friendlyError(e)));
                  }}>
            Reboot now
          </button>
        </div>
      )}

      {!connected && !preset ? (
        <p className="muted small" style={{ marginTop: 8, marginBottom: 0 }}>
          {unmanaged
            ? "This connection is managed by another program on this device "
              + "(NetworkManager does not manage it), so its address cannot be set from here."
            : "This connection is not connected. Connect a cable or join Wi-Fi before "
              + "changing its IP address."}
        </p>
      ) : (
        <>
          {preset && (
            <p className="muted small" style={{ marginTop: 8, marginBottom: 0 }}
               data-no-cable="1">
              No network cable detected. Saved settings will apply when a cable
              is connected. Nothing changes on the device until then, and no
              reboot is needed.
            </p>
          )}

          <div className="card-rule" data-net-dhcp="">
            <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <strong className="small">Automatic IP address (DHCP)</strong>
                <div className="muted small" style={{ marginTop: 2 }}>
                  {dhcp ? "Get an IP address automatically from your network."
                        : staticReady ? "Change what you need, then Save Network Settings."
                        : "Fill in the IP address and the subnet mask under Advanced Network "
                          + "Settings, then Save Network Settings."}
                </div>
              </div>
              <StateSwitch checked={dhcp} onCheckedChange={takeStatic}
                           aria-label={`${device.device} uses DHCP`} />
            </div>
            <div style={{ marginTop: 12 }} data-net-current="">
              <div className="muted small">Current IP address</div>
              <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>
                {device.ip || <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>
                  No address yet</span>}
              </div>
            </div>
          </div>

          {/* Address, mask, gateway and DNS, folded under Advanced Network
              Settings -- but never folded over a value, a mistake, or DHCP
              switched off (the address fields are then what Save needs).
              Presentation only: the fields, rules and payload are as before. */}
          <details className="net-advanced card-rule" ref={advRef}
                   data-net-advanced="1">
            <summary className="net-adv-summary">
              <SlidersHorizontal size={16} aria-hidden="true" />
              <span style={{ flex: 1 }}>Advanced Network Settings</span>
              <ChevronDown size={16} aria-hidden="true" className="net-adv-chev" />
            </summary>

            <div style={{ marginTop: 12 }}>
              <label style={{ display: "block" }}>
                <Req>IP address</Req>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input className="input" value={ip} disabled={dhcp}
                         style={{ ...field, flex: "1 1 auto",
                           ...(!dhcp && ip && !ipOk ? { borderColor: "var(--alert)" } : {}) }}
                         placeholder={device.ip || "192.168.1.50"}
                         onChange={(e) => { setIp(e.target.value); setTested(null); }} />
                  {/* Asked BEFORE the address is applied, which is the only
                      moment it helps: two machines on one address shows up
                      as a page that stopped loading, on the interface the
                      operator is holding. */}
                  <button type="button" className="btn btn-sm" style={{ marginTop: 6 }}
                          disabled={dhcp || !ipOk || testing}
                          title={ipOk ? "Check whether this address is already taken"
                                      : "Type a valid address first"}
                          onClick={testAddress}>
                    {testing ? <Loader2 size={14} className="animate-spin" /> : "Test"}
                  </button>
                </div>
                {/* The answer, next to the field it is about (Tok Aba
                    2026-10-09): red for a conflict, green for free, plain for
                    this box's own address or a test that could not run -- an
                    address nobody was asked about is never shown as free. */}
                {tested && (
                  <div className="small" data-ip-test={tested.in_use ? "conflict"
                         : tested.own ? "own" : tested.checked ? "free" : "unchecked"}
                       style={{ marginTop: 4, display: "flex", gap: 4, alignItems: "flex-start",
                                color: tested.in_use ? "var(--alert-text)"
                                  : tested.checked && !tested.own ? "var(--ok-text, #16a34a)"
                                  : "var(--muted-text)",
                                fontWeight: tested.in_use ? 600 : undefined }}>
                    {tested.in_use ? <TriangleAlert size={13} style={{ flex: "none", marginTop: 2 }} />
                      : tested.checked && !tested.own
                        ? <CircleCheck size={13} style={{ flex: "none", marginTop: 2 }} /> : null}
                    <span>{tested.detail}</span>
                  </div>
                )}
                {!tested && (
                  <div className="muted small" style={{ marginTop: 4 }} data-ip-hint="">
                    Shown when DHCP is disabled.
                  </div>
                )}
              </label>

              <label style={{ display: "block", marginTop: 12 }}>
                <Req>Subnet mask</Req>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  {/* A list, not free text: every mask an installer will ever
                      type is one of these, and a typed one that is not a mask
                      ("255.255.0.255") is refused by the server anyway. The
                      field still accepts typing, for the rare one. */}
                  <input className="input" value={mask} list={`masks-${device.device}`}
                         placeholder="255.255.255.0" disabled={dhcp}
                         style={{ ...field, flex: "1 1 auto", minWidth: 128,
                                  ...(!dhcp && mask && !maskOk ? { borderColor: "var(--alert)" } : {}) }}
                         onChange={(e) => typeMask(e.target.value)} />
                  <datalist id={`masks-${device.device}`}>
                    {MASKS.map((mk) => <option key={mk} value={mk} />)}
                  </datalist>
                  {/* The same number the other way round. Some sites hand out
                      a mask, some hand out /24; whichever is on the sheet is
                      the one that can be typed. .input carries min-width:
                      min(240px, 100%), so both widths are pinned here. */}
                  <span className="muted small" style={{ flex: "none", marginTop: 6 }}>/</span>
                  <input className="input" value={prefixText} placeholder="24" disabled={dhcp}
                         inputMode="numeric" aria-label="Prefix length"
                         style={{ flex: "0 0 62px", width: 62, minWidth: 62, marginTop: 6,
                                  padding: "8px 6px", textAlign: "center", borderRadius: 10,
                                  ...(!dhcp && prefixText && !prefixOk
                                      ? { borderColor: "var(--alert)" } : {}) }}
                         onChange={(e) => typePrefix(e.target.value)} />
                </div>
              </label>

              <label style={{ display: "block", marginTop: 12 }}>
                <div className="muted small">Default gateway (optional)</div>
                <input className="input" value={gateway} disabled={dhcp}
                       placeholder={device.gateway || "leave blank"}
                       style={{ ...field,
                                ...(!dhcp && gateway && !gwOk ? { borderColor: "var(--alert)" } : {}) }}
                       onChange={(e) => setGateway(e.target.value)} />
                {/* The gateway is where the box sends everything not on
                    this wire. Which port carries the internet is the box's
                    call (netuplink.auto_role): no gateway, never this one;
                    a gateway, only if the internet answers through it. */}
                {!dhcp && gwProblem && (gateway.trim() || ip.trim()) ? (
                  <div className="small" data-gateway-problem="1"
                       style={{ marginTop: 4, color: "var(--alert-text)" }}>
                    {gwProblem}
                  </div>
                ) : null}
              </label>

              {/* DNS, on either mode: blank is automatic (the lease's on DHCP;
                  on a fixed address the ones in use now, else the gateway --
                  netconf._ipv4_settings). It applies to DHCP too, so these two
                  are never disabled. */}
              <div className="dns-pair card-rule">
                {[["Preferred DNS server", dns1, setDns1, dns1Bad, "e.g. 8.8.8.8", "1"],
                  ["Alternate DNS server (optional)", dns2, setDns2, dns2Bad, "e.g. 1.1.1.1", "2"]]
                  .map(([label, value, set, bad, hint, n]) => (
                    <label key={n} style={{ display: "block", marginTop: n === "2" ? 12 : 0 }}>
                      <div className="muted small">{label}</div>
                      <input className="input" value={value} data-dns-field={n}
                             inputMode="decimal" placeholder={hint} autoComplete="off"
                             style={{ ...field, ...(bad ? { borderColor: "var(--alert)" } : {}) }}
                             onChange={(e) => set(e.target.value.replace(/[^\d.]/g, "").slice(0, 15))} />
                    </label>
                  ))}
                {dnsBad ? (
                  <div className="small" data-dns-problem="1"
                       style={{ marginTop: 4, color: "var(--alert-text)" }}>
                    {dnsBad}
                  </div>
                ) : null}
              </div>
            </div>
          </details>

          {/* A gateway on a second port, said before Save: the port that
              carries the internet now keeps it (netuplink.auto_role), so a
              wrong gateway here cannot take it away. */}
          {!dhcp && gateway.trim() && uplinkElsewhere.length > 0 && (
            <div style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 8,
                          padding: "8px 10px", borderRadius: 8, fontSize: 12,
                          background: "rgba(217,119,6,.08)", border: "1px solid rgba(217,119,6,.3)" }}>
              <TriangleAlert size={14} style={{ flex: "none", marginTop: 1,
                                                color: "var(--warn-text)" }} />
              <span style={{ flex: 1 }} data-routing-notice="1">
                <b style={{ display: "block" }}>Network Routing Notice</b>
                <b>{uplinkElsewhere.join(", ")}</b> already has a route to the internet.
                If it works, it keeps the internet and {device.device} becomes the
                fallback, or a machine-only network if the internet does not answer
                through {gateway.trim()}.
              </span>
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
            <button className="btn btn-accent btn-ic" style={{ borderRadius: 999, padding: "8px 18px" }}
                    disabled={!!busy || (!dhcp && !staticReady)
                              || (dhcp && (!!dnsBad || (device.dhcp !== false && !dnsChanged)))}
                    title={dhcp && dnsBad ? dnsBad
                           : dhcp && device.dhcp !== false && !dnsChanged ? "Nothing changed"
                           : !dhcp && !staticReady ? ((conflict && tested.detail) || gwProblem || dnsBad
                             || "Fill in the address and the subnet mask first")
                           : undefined}
                    onClick={save}>
              {busy === key ? <Loader2 size={14} className="animate-spin" /> : null}
              Save Network Settings
            </button>
          </div>
        </>
      )}

    </div>
  );
}
