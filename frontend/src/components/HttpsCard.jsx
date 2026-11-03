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
// Master only, like the cards beside it (the server answers 403
// to anyone else and the card draws nothing). Nothing here needs a terminal.
//
// Customer view (Tok Aba, 2026-10-14): the title with an Enabled / Disabled
// pill and the switch, "HTTPS is active", the dashboard addresses with a copy
// button each (opening over HTTP is sent here automatically; there is no second
// switch). A failure (data.error) is said to everyone.
// Handlers, payloads and confirms are as before.
import { useCallback, useEffect, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { apiUrl } from "../lib/base.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { Switch } from "./ui/switch.jsx";
import { CardHeader, StateSwitch, pillClass } from "./ui/CardHeader.jsx";
import { Copy, Download, Loader2, Lock, ShieldCheck } from "lucide-react";
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
    if (on && d.listening) toast.success("HTTPS is on",
      "Open the dashboard at one of the HTTPS addresses on the card.");
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

    </div>
  );
}
