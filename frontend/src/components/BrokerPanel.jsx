// BrokerPanel - this box's MQTT broker (Mosquitto), under Settings.
//
// Modelled on Node-RED's Aedes broker node, because that is what the people
// asking for it already know: an MQTT port, an optional WebSocket port
// (blank = off), and Security - a username and password, blank = no login.
//
// The one thing that differs from Aedes is deliberate: with no login the
// broker listens on THIS BOX ONLY. An anonymous broker open to the plant LAN
// is a door anybody on that network can write through, so opening it to
// other devices and requiring a login are the same switch here.
//
// The password is never read back. The page is told whether one is SET; a
// blank field on save keeps it. Applying goes through a root helper on the
// box (scripts/mqtt_apply.py), which checks every field again, restarts the
// broker, and puts the previous settings back if the broker does not come up.
//
// Also mounted inside a dialog on the Workflow page (BrokerStatus.jsx), so
// `embedded` drops the page frame there.
//
// Customer copy (2026-10-11), held to what the code does: Apply restarts the
// broker (connected clients drop) and rolls back if it does not come up;
// Reset only re-reads the saved settings, so it asks "Discard Unsaved
// Changes?" and never claims to restore defaults; there are no topic ACLs
// (mqtt_apply.render_conf writes none), so an open broker lets anyone read
// and publish every topic and a login lets every logged-in client do the
// same; TLS does not exist, so customers are not shown a dead checkbox. The
// auto-install switch and the persistence note are developer-tier only
// (the same user.is_developer check SystemPanel uses); their behaviour and
// routes are untouched.
import { useCallback, useEffect, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { useApp } from "../state/app.jsx";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import SettingsPage, { SettingsSection } from "./ui/SettingsPage.jsx";
import { RadioTower, Loader2 } from "lucide-react";
import { PasswordInput } from "./ui/input.jsx";

const INPUT = "rounded-md border bg-background px-3 py-1.5 text-sm";
const BUTTON = "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 "
  + "text-sm font-medium hover:bg-muted disabled:opacity-50";

//: The server's username rule (mqtt_broker.USERNAME_RE) and password length.
const USERNAME = /^[A-Za-z0-9_.-]{1,64}$/;
const PASSWORD_MIN = 8;
const PASSWORD_MAX = 128;

//: Who may connect and whether they log in, in the page's own words.
const MODE = {
  local: "This device only",
  local_secured: "This device only, login required",
  secured: "Devices on the network, login required",
  open: "Devices on the network, no login",
};

//: What each state says in one line, beside the dot.
function stateLine(s) {
  if (!s) return "";
  if (s.state === "running") return "MQTT Broker Running";
  if (s.state === "other") return `Port ${s.port} is used by another program (${s.owner})`;
  if (s.state === "not_installed") return "No MQTT broker is installed on this device";
  return `MQTT Broker Not Running (port ${s.port})`;
}

function formFrom(d) {
  const st = d?.status || {};
  const se = d?.settings || {};
  return {
    port: String(st.port || d?.defaults?.port || 1883),
    ws_port: st.ws_port ? String(st.ws_port) : "",
    // Read from the broker's own conf. One written before this setting
    // existed reads as what it did: a login meant the network, none local.
    // No conf at all (never set up here) defaults to this box only.
    access: st.managed && st.access === "network" ? "network" : "local",
    secure: !!se.username,
    username: se.username || "",
    password: "",
  };
}

//: A port field the server would refuse (mqtt_broker._port), or false.
//: Blank is allowed only where the server allows it (the WebSocket port).
function badPort(value, required) {
  const text = String(value ?? "").trim();
  if (!text) return required;
  if (!/^\d{1,5}$/.test(text)) return true;
  const n = Number(text);
  return n < 1 || n > 65535;
}

/** [title, sentence] for the first thing mqtt_broker.build_request would
 *  refuse, or null. Reserved and in-use ports are only known server-side. */
export function brokerFormProblem(form, { passwordSet = false, sameUser = false } = {}) {
  if (badPort(form.port, true) || badPort(form.ws_port, false))
    return ["Invalid Port", "Enter a valid port number between 1 and 65535."];
  if (form.secure) {
    const user = String(form.username || "").trim();
    if (!user) return ["Username Required",
      "Enter a username when authentication is enabled."];
    if (!USERNAME.test(user)) return ["Invalid Username",
      "Use letters, numbers, underscores, dots, or hyphens (up to 64)."];
    const pw = form.password || "";
    if (!pw && !(passwordSet && sameUser)) return ["Password Required",
      "Enter a password when authentication is enabled."];
    if (pw && (pw.length < PASSWORD_MIN || pw.length > PASSWORD_MAX))
      return ["Invalid Password", `Use ${PASSWORD_MIN}-${PASSWORD_MAX} characters.`];
  }
  return null;
}

//: An Apply refusal, in words. The server's 400 is build_request's sentence
//: or the root helper's (which can carry a journal tail); only a port clash
//: is worth naming, the rest is the spec's plain sentence.
function applyFailure(e) {
  if (e?.status !== 400) return friendlyError(e);
  const msg = String(e?.message || "");
  if (/is used by|own port|must differ/.test(msg))
    return "That port is already in use on this device. Choose another port.";
  return "The MQTT broker settings could not be applied. Check the "
    + "configuration and try again.";
}

/** "Connection Addresses" with a copy button per address: what to type on a
 *  PLC, a phone or another box. Local only says so instead. */
export function Reachable({ s, addresses }) {
  const toast = useToast();
  const local = s.mode === "local" || s.mode === "local_secured";
  const where = local ? [`127.0.0.1:${s.port}`]
    : addresses.map((ip) => `${ip}:${s.port}`);
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied", text);
    } catch { toast.error("Could not copy", text); }
  }
  return (
    <div className="mt-3 space-y-1 text-sm" data-broker-reachable="">
      <p className="font-medium">Connection Addresses</p>
      {local ? (
        <p className="text-muted-foreground">
          Only applications on this device can connect, at{" "}
          <code>127.0.0.1:{s.port}</code>. To let other devices connect, choose
          "Devices on the Network" below.
        </p>
      ) : where.length === 0 ? (
        <p className="text-muted-foreground">
          Other devices connect to this device's network address, port {s.port}.
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {where.map((w) => (
            <span key={w} className="inline-flex items-center gap-1">
              <code className="rounded bg-muted px-1.5 py-0.5">{w}</code>
              <button type="button" className={BUTTON + " px-2 py-0.5 text-xs"}
                      title="Copy Connection Address"
                      onClick={() => copy(w)} aria-label={`Copy ${w}`}>Copy</button>
            </span>
          ))}
          {s.ws_port && (
            <span className="text-xs text-muted-foreground">
              WebSocket on port {s.ws_port}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function BrokerSettings() {
  const toast = useToast();
  const confirm = useConfirm();
  const { user } = useApp() || {};
  const isDev = !!user?.is_developer;
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    api.get("/api/mqtt/broker")
      .then((d) => { setData(d); setForm(formFrom(d)); setError(null); })
      .catch(setError);
  }, []);
  useEffect(load, [load]);

  const s = data?.status;
  const passwordSet = !!data?.settings?.password_set;
  const sameUser = form && form.username === (data?.settings?.username || "");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save() {
    const problem = brokerFormProblem(form, { passwordSet, sameUser });
    if (problem) { toast.error(problem[0], problem[1]); return; }
    setBusy("save");
    try {
      const d = await api.post("/api/mqtt/broker", form);
      setData(d);
      setForm(formFrom(d));
      toast.success("MQTT settings applied",
                    "The broker restarted with the new settings.");
    } catch (e) {
      toast.error("Could Not Apply Settings", applyFailure(e));
    } finally { setBusy(""); }
  }

  // Reset re-reads what is saved on the box; it does not restore defaults.
  // Asked only when there is something to lose.
  async function reset() {
    const dirty = JSON.stringify(form) !== JSON.stringify(formFrom(data));
    if (dirty && !await confirm({
      title: "Discard Unsaved Changes?",
      description: "Your unsaved changes will be lost.",
      confirmLabel: "Discard Changes" })) return;
    load();
  }

  // The hub installs a missing broker by itself at start (after an update,
  // say). This is the "no thanks" - the same marker --no-mqtt writes.
  async function toggleAuto(enabled) {
    setBusy("auto");
    try {
      setData(await api.post("/api/mqtt/broker/auto", { enabled }));
    } catch (e) {
      toast.error("Could not change that", friendlyError(e));
    } finally { setBusy(""); }
  }

  async function install() {
    setBusy("install");
    try {
      const d = await api.post("/api/mqtt/broker/install", {});
      setData(d);
      setForm(formFrom(d));
      toast.success("MQTT broker installed");
    } catch (e) {
      toast.error("The MQTT broker did not install",
                  e?.status === 400 ? "Try again. If it keeps failing, restart the device."
                                    : friendlyError(e));
    } finally { setBusy(""); }
  }

  if (error) {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive/5
                      p-4 text-sm text-destructive">
        {friendlyError(error) || "Could not load the MQTT broker settings."}
      </div>
    );
  }
  if (!data || !form) {
    return <p className="text-sm text-muted-foreground">Loading the MQTT broker settings...</p>;
  }

  const dot = s.state === "running" ? "bg-emerald-500"
    : s.state === "not_installed" ? "bg-muted-foreground" : "bg-amber-500";
  // A box set up before the helper existed: an over-the-air update runs as
  // the service account and cannot install a root helper, so this is the one
  // step somebody with a shell has to take (or re-run the installer).
  // The lines come from the SERVER (mqtt_broker.setup_commands), which knows
  // where this box is installed (/opt/irivai-systor, or a /home/pi tree) and
  // which account it runs as, and are the same lines the Inbox notice sends.
  // The sudoers line is written to a ".tmp" name first (sudo skips files
  // with a dot in sudoers.d) and only moved into place once visudo passes:
  // a live file with a typo in it stops sudo working for every account.
  const setup = data.setup_commands || [];
  const canApply = s.helper && s.state !== "other";
  const openToNetwork = form.access === "network" && !form.secure;

  return (
    <div className="space-y-4">
      <SettingsSection title="Broker Status">
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          <span className={`inline-block size-2.5 rounded-full ${dot}`}
                aria-hidden="true" />
          <span className="font-medium" data-broker-state={s.state}>
            {stateLine(s)}
          </span>
          {s.state === "not_installed" && s.helper && (
            <button type="button" className={BUTTON} onClick={install}
                    disabled={!!busy}>
              {busy === "install" && <Loader2 className="size-4 animate-spin" />}
              Install the MQTT Broker
            </button>
          )}
        </div>
        {s.state === "running" && (
          <p className="mt-1 text-sm text-muted-foreground" data-broker-running="">
            The MQTT broker is running and ready to accept connections using
            the configured network settings. Port {s.port}
            {MODE[s.mode] ? ` · ${MODE[s.mode]}` : ""}.
          </p>
        )}
        {s.state === "running" && <Reachable s={s} addresses={data.addresses || []} />}
        {s.state === "other" && (
          <p className="mt-2 text-sm text-muted-foreground">
            Another MQTT broker already uses this port, so this device will not
            start a second one there. Workflow MQTT steps pointed at
            127.0.0.1:{s.port} use that broker.
          </p>
        )}
        {s.state === "not_installed" && s.helper && (
          <p className="mt-2 text-sm text-muted-foreground">
            Installs the Mosquitto MQTT broker, accepting connections from this
            device only. It can take a few minutes.
          </p>
        )}
        {!s.helper && (
          <div className="mt-3 space-y-2 text-sm">
            <p className="text-muted-foreground">
              This device was installed before the broker could be set up from
              here. Re-run the installer, or run these once in a terminal on
              the device:
            </p>
            <pre className="overflow-x-auto rounded-md border bg-muted/40 p-3
                            text-xs">{setup.join("\n")}</pre>
            {s.state === "not_installed" && (
              <p className="text-muted-foreground">
                The broker itself: <code>sudo apt-get install -y mosquitto
                mosquitto-clients</code>
              </p>
            )}
          </div>
        )}
        {/* Developer tier only: an installer-level switch, not a customer
            setting. Same route, same marker, same default as before. */}
        {isDev && (
          <label className="mt-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={data.auto_install !== false}
                   disabled={!!busy} data-broker-auto=""
                   onChange={(e) => toggleAuto(e.target.checked)} />
            Install the broker automatically when it is missing
            <span className="text-xs text-muted-foreground">
              - checked when the platform starts, e.g. after an update; local only
            </span>
          </label>
        )}
      </SettingsSection>

      {/* Up here, before the ports, and the fields always on show (Tok Aba's
          beta tester, 2026-10-02, could not find them behind a checkbox at
          the bottom). Greyed out until the box is ticked. */}
      <SettingsSection title="Login Credentials"
        description="Choose whether devices must provide a username and password before connecting to the MQTT broker.">
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="flex items-center gap-2 text-sm font-medium sm:col-span-2">
            <input type="checkbox" checked={form.secure}
                   onChange={(e) => setForm((f) => ({ ...f, secure: e.target.checked }))} />
            Require a username and password to connect
          </label>
          {!form.secure && form.access === "local" && (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3
                          text-sm sm:col-span-2" data-broker-note="local">
              Only applications on this device can connect. To let other
              devices on the network connect, choose "Devices on the Network"
              below and set a username and password.
            </p>
          )}
          {openToNetwork && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3
                            text-sm sm:col-span-2" data-broker-note="auth-off">
              <p className="font-medium">Authentication Disabled</p>
              <p className="mt-1">
                Devices on the network can connect without a username or
                password, and can read every message and publish to any topic.
                Enable authentication unless this network is trusted and
                properly isolated.
              </p>
            </div>
          )}
          <>
              <label className="grid gap-1.5">
                <span className="text-sm font-medium">Username</span>
                <input className={INPUT} value={form.username} autoComplete="off"
                       disabled={!form.secure} data-broker-field="username"
                       onChange={set("username")} placeholder="Enter a username" />
                <span className="text-xs text-muted-foreground">
                  Use letters, numbers, underscores, dots, or hyphens.
                </span>
              </label>
              <label className="grid gap-1.5">
                <span className="text-sm font-medium">
                  Password
                  {passwordSet && sameUser && (
                    <span className="ml-2 font-normal text-muted-foreground">
                      (saved; leave blank to keep it)
                    </span>
                  )}
                </span>
                <PasswordInput native className={INPUT} autoComplete="new-password"
                       disabled={!form.secure} data-broker-field="password"
                       value={form.password} onChange={set("password")}
                       placeholder={passwordSet && sameUser ? "Unchanged" : "Enter a password"} />
                <span className="text-xs text-muted-foreground">
                  Use {PASSWORD_MIN}-{PASSWORD_MAX} characters.
                </span>
              </label>
              {form.secure && (<>
              <p className="text-xs text-muted-foreground sm:col-span-2">
                With a login set, the broker refuses clients that do not log
                in. Every client that logs in can read and publish to every
                topic. This device's own MQTT steps log in by themselves;
                running systems pick up a new password when they next connect.
              </p>
              {/* Pipi's wording (security review of c8a64d5). Plain MQTT: the
                  login is only as private as the cable it crosses. */}
              <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3
                            text-sm sm:col-span-2" data-broker-note="no-tls">
                Without TLS, the username, password and every message travel
                unencrypted on the network. Anyone who can watch this network
                can read them and then publish to any topic. Use it only on a
                trusted, isolated plant network; do not reuse this password
                anywhere else.
              </p>
              </>)}
          </>
        </div>
      </SettingsSection>


      <SettingsSection title="Connection Settings"
        description="Configure the ports and network interfaces that MQTT clients can use to connect to this broker.">
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="grid gap-1.5">
            <span className="text-sm font-medium">MQTT Port</span>
            <input className={INPUT} inputMode="numeric" value={form.port}
                   onChange={set("port")} placeholder="1883" />
            <span className="text-xs text-muted-foreground">
              The port used by MQTT clients to connect to the broker. This
              device's own workflows connect through 127.0.0.1 and this port.
            </span>
          </label>
          <label className="grid gap-1.5">
            <span className="text-sm font-medium">WebSocket Port (Optional)</span>
            <input className={INPUT} inputMode="numeric" value={form.ws_port}
                   onChange={set("ws_port")} placeholder="Leave blank to disable" />
            <span className="text-xs text-muted-foreground">
              Enable WebSocket connections for browser-based MQTT clients.
              Leave blank to disable WebSocket access.
            </span>
          </label>
          {/* TLS does not exist yet. Customers are not shown a switch that
              does nothing; the developer tier sees the plain status. */}
          {isDev && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground
                          sm:col-span-2" data-broker-tls="">
              <span className="font-medium">Secure MQTT Connection (TLS)</span>
              <span className="text-xs">Not available yet</span>
            </p>
          )}
        </div>
      </SettingsSection>

      {/* WHO may connect, separate from WHETHER they log in (Tok Aba,
          2026-09-29). Both answers are allowed with or without a login;
          "any device" with no login is the product owner's call, the
          same one Node-RED's Aedes makes, and it carries its warning. */}
      <SettingsSection title="Who Can Connect"
        description="Choose whether the MQTT broker accepts connections only from this device or from other devices on the network.">
        <fieldset className="mt-4 grid gap-3" data-broker-access={form.access}>
          <legend className="sr-only">Who Can Connect</legend>
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" name="broker-access" value="local" className="mt-1"
                   checked={form.access === "local"}
                   onChange={() => setForm((f) => ({ ...f, access: "local" }))} />
            <span>
              <span className="font-medium">This Device Only</span>
              <span className="block text-xs text-muted-foreground">
                Only applications running on this device can connect through
                the local interface.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="radio" name="broker-access" value="network" className="mt-1"
                   checked={form.access === "network"}
                   onChange={() => setForm((f) => ({ ...f, access: "network" }))} />
            <span>
              <span className="font-medium">Devices on the Network</span>
              <span className="block text-xs text-muted-foreground">
                Allow other devices on the network to connect to the MQTT
                broker.
              </span>
            </span>
          </label>
          {form.access === "network" && !form.secure && (
            <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3
                            text-sm text-destructive"
                 role="alert" data-broker-note="open">
              <p className="font-medium">Warning: Unprotected MQTT Access</p>
              <p className="mt-1">
                Devices on this network can connect without a password, read
                every message and publish to any topic, including ones machines
                act on. Only use this configuration on a trusted, isolated
                network. Enable authentication for normal deployments.
              </p>
            </div>
          )}
        </fieldset>
      </SettingsSection>

      {/* An implementation fact, not a setting: developer tier only. */}
      {isDev && data.persistence_note && (
        <p className="text-xs text-muted-foreground" data-broker-persistence="">
          {data.persistence_note}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        <p className="mr-auto text-xs text-muted-foreground" data-broker-apply-help="">
          Apply saves and applies the MQTT broker settings. The broker
          restarts, so connected MQTT clients are disconnected and must
          reconnect. If the broker does not start, the previous settings are
          put back.
        </p>
        <button type="button" className={BUTTON} onClick={reset} disabled={!!busy}>
          Reset
        </button>
        <button type="button"
                className={BUTTON + " bg-primary text-primary-foreground hover:bg-primary/90"}
                onClick={save} disabled={!!busy || !canApply}
                title={canApply ? "" : "The broker cannot be changed from here on this device"}>
          {busy === "save" && <Loader2 className="size-4 animate-spin" />}
          Apply
        </button>
      </div>
    </div>
  );
}

export default function BrokerPanel() {
  return (
    <SettingsPage
      title="MQTT Broker"
      icon={RadioTower}
      description="Configure how other devices and applications connect to this
                   device to exchange messages.">
      <BrokerSettings />
    </SettingsPage>
  );
}
