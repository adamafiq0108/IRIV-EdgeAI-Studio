// HttpsCard - HTTPS on the Network page (the card titled "HTTPS"), under the
// Software port and Hostname cards.
//
// The box makes its own root certificate and serves HTTPS with a certificate
// it issues itself (core/services/https.py); each computer or phone trusts
// the ROOT once, from the download here, and from then on the box can renew
// its certificate (new address, new hostname, yearly) without anybody
// touching a client again. HTTP keeps working beside it, unchanged.
//
// Live, unlike the port and the hostname: HTTPS is an extra door, so turning
// it on, moving it or closing it moves no address a browser is using - except
// this page when it was opened over HTTPS, which the confirms say.
//
// Master or developer only, like the cards beside it (the server answers 403
// to anyone else and the card draws nothing). Nothing here needs a terminal.
//
// Customer view (Tok Aba, 2026-10-14): the title with an Enabled / Disabled
// pill and the switch, "HTTPS is active", the dashboard addresses with a copy
// button each (opening over HTTP is sent here automatically; there is no second
// switch). Everything else - the long
// explanation, "Trust This Device" with its downloads and per-OS steps,
// "Certificate Details", "Advanced HTTPS Settings" (port, regenerate) and the
// port 443 / not-in-certificate notes - is drawn for a developer account only
// (data-testid https-dev-extras). A failure (data.error) is said to everyone.
// Handlers, payloads and confirms are as before.
import { useCallback, useEffect, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { apiUrl } from "../lib/base.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { Switch } from "./ui/switch.jsx";
import { CardHeader, StateSwitch, pillClass } from "./ui/CardHeader.jsx";
import { Copy, Download, Loader2, Lock, ShieldCheck } from "lucide-react";
import { useIsDeveloper } from "../state/app.jsx";
import { Note } from "./BoxRestartNote.jsx";
import { httpsState, httpsUrls, shortDate } from "./httpsLinks.js";

// Windows' own certificate dialog shows only a SHA-1 thumbprint, so the
// SHA-256 check is certutil on the DER (.cer) file, whose hash IS the
// fingerprint. Current User, not Local Machine: one account, and Pipi's HTTPS
// review (2026-10-10, L2).
const TRUST = [
  ["Windows", "Download the .cer and check it first: in a Command Prompt, "
    + "certutil -hashfile iriv-edge-ai-root.cer SHA256 must print the fingerprint "
    + "above (without the colons) - the window Windows opens shows only a SHA-1 "
    + "thumbprint. Then open the file, Install Certificate, Current User, \"Place all "
    + "certificates in the following store\", Trusted Root Certification Authorities, "
    + "and confirm. Restart the browser. (Local Machine trusts it for every account on "
    + "that PC; choose it only if that is what you want.)"],
  ["macOS", "Open the .crt file (it goes into Keychain Access, System keychain), "
    + "double-click the \"IRIV EdgeAI Studio local CA\" entry, Trust, \"When using this "
    + "certificate: Always Trust\"."],
  ["iPhone / iPad", "Download the .crt in Safari and allow the profile. Settings, "
    + "Profile Downloaded, Install. Then Settings, General, About, Certificate Trust "
    + "Settings, and switch on full trust for the IRIV EdgeAI Studio root."],
  ["Android", "Download the .cer file. Settings, Security (or Encryption & "
    + "credentials), Install a certificate, CA certificate, and pick the file. Chrome "
    + "uses it; some apps do not trust user certificates."],
  ["Linux / Firefox", "Firefox keeps its own list: Settings, Privacy & Security, "
    + "Certificates, View Certificates, Authorities, Import, tick \"Trust this CA to "
    + "identify websites\". Chrome on Linux: Settings, Privacy and security, Security, "
    + "Manage certificates, Authorities, Import."],
];


export default function HttpsCard() {
  const isDev = useIsDeveloper();
  const [data, setData] = useState(null);
  const [port, setPort] = useState("");
  const [busy, setBusy] = useState("");
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const d = await api.get("/api/hub/https");
      if (!d || typeof d.port !== "number") { setData({ hidden: true }); return; }
      setData(d);
      setPort((cur) => cur || String(d.port));
    } catch {
      setData({ hidden: true });          // 403: not this account's card
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (!data || data.hidden) return null;

  const onHttps = !!data.page_https;
  const n = Number(port);
  const portValid = /^\d{4,5}$/.test(port.trim()) && n >= 1024 && n <= 65535;
  const urls = httpsUrls(data);
  const httpHere = `http://${window.location.hostname}`;

  // Paths arrive already through apiUrl (idempotent; the base-path scanner,
  // tests/check_basepath.mjs, sees only direct api.* calls).
  async function post(path, body, failTitle) {
    try {
      const d = await api.post(path, body);
      setData(d); setPort(String(d.port));
      return d;
    } catch (e) {
      toast.error(failTitle, friendlyError(e));
      return null;
    } finally { setBusy(""); }
  }

  async function toggle(on) {
    if (!on && onHttps && !await confirm({
      title: "Turn off HTTPS?",
      description: "This page is open over HTTPS, so it stops working at once. The "
        + `dashboard stays on HTTP (${httpHere} with its usual port).`,
      confirmLabel: "Turn off HTTPS", destructive: true })) return;
    setBusy("toggle");
    const d = await post(apiUrl("/api/hub/https"), { enabled: on },
                         on ? "HTTPS could not be switched on" : "HTTPS could not be switched off");
    if (!d) return;
    // "Trust This Device" is on a developer's card only; a customer is
    // pointed at what the card shows them.
    if (on && d.listening) toast.success("HTTPS is on", isDev
      ? "Trust the root certificate on each computer or phone (Trust This Device)."
      : "Open the dashboard at one of the HTTPS addresses on the card.");
    else if (on) toast.error("HTTPS did not start", d.error ? "See the note on the card." : "Try again.");
    else toast.info("HTTPS is off", "The dashboard answers on HTTP as before.");
  }

  async function savePort() {
    if (onHttps && !await confirm({
      title: `Move HTTPS to port ${n}?`,
      description: "This page is open over HTTPS on the current port and stops working at "
        + "once. Open the new address afterwards.",
      confirmLabel: "Save HTTPS Port" })) return;
    setBusy("port");
    const d = await post(apiUrl("/api/hub/https"), { port: n }, "The device cannot use that port");
    if (d) toast.success("HTTPS port saved", d.enabled ? `HTTPS answers on ${d.port} now.` : "");
  }

  async function regenerate(what) {
    if (what === "root" && !await confirm({
      title: "Replace Root Certificate?",
      description: "Every computer and phone that trusts this device now will need to "
        + "trust the new certificate authority again: until the NEW root certificate "
        + "is downloaded and trusted on each of them, its browser shows a security "
        + "warning. Only do this if the old one was lost or exposed.",
      confirmLabel: "Replace Root Certificate", destructive: true,
      requireText: "new root" })) return;
    setBusy(what);
    const d = await post(apiUrl("/api/hub/https/regenerate"),
                         what === "root" ? { what, confirm: true } : { what },
                         what === "root" ? "Could not replace the root certificate"
                                         : "Could not generate a new certificate");
    if (d) toast.success(what === "root" ? "Root certificate replaced" : "New certificate generated",
                         what === "root" ? "Download it and trust it on every computer and phone again." : "");
  }

  async function copyUrl(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Address copied", text);
    } catch {
      toast.info("Select it and copy", "The browser allows the copy button only over HTTPS.");
    }
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Fingerprint copied", text);
    } catch {
      // A plain-HTTP page may not use the clipboard: the text is selectable.
      toast.info("Select it and copy", "The browser allows the copy button only over HTTPS.");
    }
  }

  const spin = (key) => (busy === key ? <Loader2 size={14} className="animate-spin" /> : null);
  const root = data.root;
  const leaf = data.leaf;
  const live = data.enabled && data.listening;

  return (
    <div className="panel-card" style={{ marginBottom: 14 }} data-testid="https-card">
      {/* The customer view (Tok Aba, 2026-10-14): the title with its state,
          what is true now and the addresses to copy.
          The rest (trust download, certificate details, advanced settings)
          is for a developer account only. */}
      {/* The card's on/off at the top right, its label left of it
          (ui/CardHeader.jsx, the page-wide placement since 2026-10-14). */}
      <CardHeader icon={Lock} title="Secure Connection (HTTPS)"
                  subtitle={<span data-testid="https-about">
                    Encrypted access to your device dashboard.</span>}>
        <StateSwitch checked={!!data.enabled} disabled={!!busy} aria-label="HTTPS"
                     label={live ? "Enabled" : data.enabled ? "Not answering" : "Disabled"}
                     kind={live ? "on" : data.enabled ? "warn" : "off"}
                     labelProps={{ "data-testid": "https-state", title: httpsState(data) }}
                     title={data.enabled ? "Turn HTTPS off (HTTP keeps working)"
                                         : "Turn HTTPS on (HTTP keeps working)"}
                     onCheckedChange={toggle} />
      </CardHeader>

      {data.error && (
        <Note><span data-testid="https-error">{data.error}</span></Note>
      )}

      {/* What is true NOW: HTTPS answering is the box's state, not this page's. */}
      {live && (
        <div className="card-rule" data-testid="https-active">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ShieldCheck size={16} style={{ color: "var(--ok-text)" }} aria-hidden="true" />
            <strong>HTTPS is active</strong>
          </div>
          <p className="muted small" style={{ marginTop: 6 }}>
            Your connection is encrypted. Your browser may still display a certificate
            warning if the device's certificate authority is not trusted. Pages opened
            over HTTP are sent here automatically, and a sign-in or API token over plain
            HTTP is refused.
          </p>
        </div>
      )}

      {data.enabled && urls.length > 0 && (
        <div style={{ marginTop: 12 }} data-testid="https-urls">
          <strong className="small">Dashboard addresses</strong>
          <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0 }}>
            {urls.map((u) => (
              <li key={u} style={{ display: "flex", alignItems: "center", gap: 8,
                                   padding: "4px 0" }}>
                <code style={{ flex: 1, wordBreak: "break-all" }}>{u}</code>
                <button type="button" className="btn btn-icon btn-ghost" aria-label={`Copy ${u}`}
                        title="Copy this address" onClick={() => copyUrl(u)}
                        data-testid="https-copy-url" style={{ width: 28, height: 28 }}>
                  <Copy size={14} aria-hidden="true" />
                </button>
              </li>))}
          </ul>
        </div>
      )}

      {isDev && (<div className="dev-extras" data-testid="https-dev-extras">
      <div className="dev-extras-label">Developer account only</div>
      <p className="muted small" style={{ marginTop: 4 }}>
        HTTPS encrypts the connection between your browser and this device. HTTP
        keeps working as before. Because the device uses its own certificate
        authority, your browser may show a security warning until that authority
        is trusted on your computer or phone.
      </p>
      {onHttps && (
        <Note ok><span data-testid="https-here">This page is open over HTTPS - the
          connection to the device is encrypted.</span></Note>
      )}
      {data.enabled && data.listening && !data.port443 && data.port443_detail && (
        <Note><span>Port 443 is not used: {data.port443_detail}. HTTPS answers on
          port {data.port}.</span></Note>
      )}
      {data.uncovered?.length > 0 && (
        <Note><span>Not in the certificate: {data.uncovered.join(", ")}. The device's root
          may only name local names and private addresses, so use one of the addresses
          below.</span></Note>
      )}

      {root && (
        <div style={{ marginTop: 12 }} data-testid="https-trust">
          <strong className="small">Trust This Device</strong>
          {/* Trust, said apart from encryption: the link is encrypted either
              way; trusting the root is what lets the browser confirm it is
              this device and stop warning. */}
          <p className="muted small" style={{ marginTop: 2 }}>
            The connection is encrypted whether or not the certificate is trusted.
            Trusting it is what lets the browser confirm it is talking to this
            device, so it stops showing a security warning. To avoid the warning,
            install the device's root certificate on each computer or phone that
            needs trusted HTTPS access.
          </p>
          <p className="muted small" style={{ marginTop: 4 }}>
            Before trusting the certificate, verify its fingerprint (under
            Certificate Details) against the one shown when this page is opened on
            the device's own screen, or another trusted source: a page loaded over
            plain HTTP on the network can be altered together with the download.
          </p>
          <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
            <a className="btn btn-sm btn-accent btn-ic" href={apiUrl("/api/hub/https/root.crt")}
               download data-testid="https-download">
              <Download size={14} /> Download Root Certificate (.crt)
            </a>
            <a className="btn btn-sm btn-ic" href={apiUrl("/api/hub/https/root.crt?format=der")} download>
              <Download size={14} /> Download Root Certificate (.cer, Windows and Android)
            </a>
            <button type="button" className="btn btn-sm" data-testid="https-copy"
                    onClick={() => copy(root.fingerprint)}>
              Copy Certificate Fingerprint
            </button>
          </div>
          <details style={{ marginTop: 8 }}>
            <summary className="small" style={{ cursor: "pointer" }}>
              How to trust it on a computer or phone
            </summary>
            <ul className="muted small" style={{ marginTop: 6, paddingLeft: 18 }}>
              {TRUST.map(([os, how]) => <li key={os} style={{ marginBottom: 4 }}><b>{os}:</b> {how}</li>)}
            </ul>
            <p className="muted small">Trust only a root whose fingerprint matches the one
              on the device's own screen. This root can vouch only for websites with local
              names (.local, .lan, .internal, home.arpa, localhost) and private network
              addresses - this device, and other devices on this network if its key were ever
              stolen - and for nothing on the internet, no programs and no e-mail.</p>
          </details>

          {/* The facts a verification needs, folded but one click away. */}
          <details style={{ marginTop: 8 }} data-testid="https-details">
            <summary className="small" style={{ cursor: "pointer", fontWeight: 600 }}>
              Certificate Details
            </summary>
            <div className="muted small" style={{ marginTop: 6 }}>
              Root certificate fingerprint (SHA-256):
            </div>
            <code data-testid="https-fingerprint"
                  style={{ display: "block", userSelect: "all", wordBreak: "break-all",
                           fontSize: 11, marginTop: 2 }}>
              {root.fingerprint}
            </code>
            <div className="muted small" style={{ marginTop: 6 }}>
              Certificate authority: {root.subject}. Valid until {shortDate(root.not_after)}.
            </div>
            {leaf && (
              <div className="muted small" style={{ marginTop: 4 }}>
                This device's certificate is valid until {shortDate(leaf.not_after)} and
                is renewed automatically 30 days before it expires.
              </div>
            )}
            {leaf && (
              <div className="muted small" style={{ marginTop: 4 }} data-testid="https-sans">
                The certificate covers {[...leaf.names, ...leaf.ips].join(", ")}
                {leaf.issued ? ` (made ${shortDate(leaf.issued)}${leaf.reason ? `, ${leaf.reason}` : ""})` : ""}.
              </div>
            )}
          </details>
        </div>
      )}

      <details style={{ marginTop: 10 }} data-testid="https-advanced">
        <summary className="small" style={{ cursor: "pointer", fontWeight: 600 }}>
          Advanced HTTPS Settings
        </summary>
        <div style={{ display: "flex", gap: 6, alignItems: "flex-end", marginTop: 8,
                      flexWrap: "wrap" }}>
          <label style={{ flex: "0 1 160px" }}>
            <div className="muted small">HTTPS Port (default {data.default_port})</div>
            <input className="input" value={port} inputMode="numeric"
                   style={{ minWidth: 0, width: "100%",
                            ...(port && !portValid ? { borderColor: "var(--alert)" } : {}) }}
                   onChange={(e) => setPort(e.target.value)} />
          </label>
          <button type="button" className="btn btn-sm"
                  disabled={!portValid || n === data.port || !!busy} onClick={savePort}>
            {spin("port")} Save HTTPS Port
          </button>
        </div>
        <p className="muted small" style={{ marginTop: 4 }}>
          The port used for secure dashboard access. Changing it may affect existing
          bookmarks and links.
        </p>
        {root && (
          <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
            <button type="button" className="btn btn-sm" disabled={!!busy}
                    title="A new certificate for this device; computers and phones notice nothing"
                    onClick={() => regenerate("leaf")}>
              {spin("leaf")} Generate New Certificate
            </button>
            <button type="button" className="btn btn-sm" disabled={!!busy}
                    title="Every computer and phone must trust the new root again"
                    onClick={() => regenerate("root")}>
              {spin("root")} Replace Root Certificate...
            </button>
          </div>
        )}
        {root && (
          <p className="muted small" style={{ marginTop: 4 }}>
            Generate New Certificate renews this device's certificate under the same
            root; nothing changes on computers and phones. Replace Root Certificate
            makes a new certificate authority, which every computer and phone must
            trust again.
          </p>
        )}
      </details>
      </div>)}
    </div>
  );
}
