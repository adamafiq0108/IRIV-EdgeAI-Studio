// StorePanel - the online template store (hub → Template Store).
//
// A store template is a WHOLE SYSTEM the owner built, exported and published:
// zones, workflow, dashboard, class filter, plus the .hef compiled for each
// Hailo board. Installing one downloads the bundle, verifies its signature,
// and creates a ready-to-run system - the operator only adds cameras.
//
// Free templates come from the public repo with no key. Pro templates need a
// license key issued by IRIV: pasted once, checked against this device's
// serial and against server time (never only the local clock).
import { useEffect, useRef, useState } from "react";
import { useApp } from "../state/app.jsx";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { Button } from "./ui/button.jsx";
import { Input } from "./ui/input.jsx";
import { Label } from "./ui/label.jsx";
import { Badge } from "./ui/badge.jsx";
import { ConfirmDialog } from "./ui/dialogs.jsx";
import { SkelPage } from "./Loading.jsx";
import {
  Store, KeyRound, Download, CheckCircle2, WifiOff, Cpu, Lock,
} from "lucide-react";

function fmtDate(epoch) {
  if (!epoch) return "";
  try { return new Date(epoch * 1000).toLocaleDateString(); }
  catch { return ""; }
}

export default function StorePanel() {
  const { user } = useApp();
  const toast = useToast();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busyKey, setBusyKey] = useState("");     // template being installed
  const [progress, setProgress] = useState(null); // {state, step, pct, ...}
  const [installFor, setInstallFor] = useState(null); // template picked in the name dialog
  const [sysName, setSysName] = useState("");
  const pollRef = useRef(null);

  async function load(force = false) {
    try {
      const d = await api.get(`/api/hub/store/catalog${force ? "?force=1" : ""}`);
      setData(d);
      setError("");
    } catch (e) {
      setError(e.message || "the store could not be reached");
    }
  }
  useEffect(() => { load(); return () => clearInterval(pollRef.current); }, []);

  function beginInstall(t) {
    setInstallFor(t);
    setSysName(t.name || t.key);
  }

  async function confirmInstall() {
    const t = installFor;
    if (!t) return;
    setInstallFor(null);
    setBusyKey(t.key);
    setProgress({ state: "running", step: "starting", pct: 0 });
    try {
      await api.post("/api/hub/store/install", { key: t.key, name: sysName });
    } catch (e) {
      setBusyKey("");
      setProgress(null);
      toast.error("The install could not start", e.message);
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const d = await api.get(`/api/hub/store/install/${encodeURIComponent(t.key)}`);
        const s = d.status || {};
        setProgress(s);
        if (s.state === "done" || s.state === "failed") {
          clearInterval(pollRef.current);
          setBusyKey("");
          if (s.state === "done") {
            toast.success(`${t.name || t.key} installed`,
              (s.warnings || []).length
                ? `Created as a new system. Notes: ${s.warnings.join(" · ")}`
                : "Created as a new system - open it and add a camera.");
            load(true);
          } else {
            toast.error("The install failed", s.error || "unknown error");
          }
        }
      } catch { /* transient poll miss - keep polling */ }
    }, 1000);
  }

  if (!data && !error) return <SkelPage cards={3} rows={3} />;

  const templates = data?.templates || [];
  const lic = data?.license || {};

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      {/* One-line state banners; the catalogue below still renders. */}
      {data?.offline && (
        <Banner icon={WifiOff}
                text="This device is offline - showing the last catalogue it saw. Connect to the internet to install templates." />
      )}
      {data?.pro_error && <Banner icon={Lock} text={data.pro_error} />}
      {error && !data && (
        <div className="rounded-xl border bg-card p-6 text-sm text-muted-foreground">
          The store could not be reached: {error}
          <Button className="ml-3" size="sm" variant="outline" onClick={() => load(true)}>
            Try again
          </Button>
        </div>
      )}
      {data && !data.crypto && (
        <Banner icon={Lock}
                text="This box is missing the 'cryptography' package, so store downloads cannot be verified or installed." />
      )}

      {/* License - admin manages it here; everyone can see the tier. */}
      {user?.is_admin && <LicenseCard lic={lic} onChanged={() => load(true)} />}

      {/* The catalogue. */}
      {templates.length === 0 && data && !data.offline ? (
        <div className="rounded-xl border bg-card p-8 text-center text-sm text-muted-foreground">
          <Store className="mx-auto mb-2 size-6" />
          No templates published yet.
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {templates.map((t) => (
            <TemplateCard key={t.key} t={t} arch={data?.arch}
                          installing={busyKey === t.key ? progress : null}
                          canInstall={user?.is_admin && !data?.offline && data?.crypto && !busyKey}
                          onInstall={() => beginInstall(t)} />
          ))}
        </div>
      )}

      {/* Name the new system before the download starts. */}
      <ConfirmDialog
        open={!!installFor}
        onOpenChange={(v) => { if (!v) setInstallFor(null); }}
        title={`Install ${installFor?.name || ""}`}
        description={"The bundle downloads, its signature is verified, and a "
          + "ready-to-run system is created. You add cameras afterwards."}
        confirmLabel="Install"
        onConfirm={confirmInstall}
      >
        <div className="grid gap-1.5">
          <Label htmlFor="store-sys-name">System name</Label>
          <Input id="store-sys-name" value={sysName} autoFocus
                 onChange={(e) => setSysName(e.target.value)} />
        </div>
      </ConfirmDialog>
    </div>
  );
}

function Banner({ icon: Icon, text }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border bg-card px-4 py-3 text-sm text-muted-foreground">
      <Icon className="size-4 shrink-0" />
      <span className="min-w-0">{text}</span>
    </div>
  );
}

function TemplateCard({ t, arch, installing, canInstall, onInstall }) {
  const compatible = t.compatible;
  return (
    <div className="flex flex-col rounded-xl border bg-card p-5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{t.name || t.key}</p>
          <p className="mt-1 line-clamp-3 text-xs text-muted-foreground">
            {t.summary || "No description."}
          </p>
        </div>
        <Badge variant={t.tier === "pro" ? "default" : "secondary"}>
          {t.tier === "pro" ? "Pro" : "Free"}
        </Badge>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        {/* Hardware badge against THIS box's detected chip. */}
        <span className={"inline-flex items-center gap-1 rounded-md border px-2 py-0.5 "
          + (compatible ? "text-emerald-600 dark:text-emerald-500"
                        : "text-amber-600 dark:text-amber-500")}>
          <Cpu className="size-3" />
          {compatible ? `Compatible (${arch})`
            : arch ? "No build for this box"
                   : "No Hailo chip detected"}
        </span>
        {t.has_code && (
          <span className="rounded-md border px-2 py-0.5 text-muted-foreground">
            includes logic
          </span>
        )}
        {t.installed_slug && (
          <span className="inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-emerald-600 dark:text-emerald-500">
            <CheckCircle2 className="size-3" /> Installed
          </span>
        )}
      </div>

      <div className="mt-4 flex items-center justify-end gap-2">
        {installing ? (
          <div className="w-full">
            <div className="mb-1 flex justify-between text-xs text-muted-foreground">
              <span className="truncate">{installing.step || "working"}</span>
              <span className="tabular-nums">{installing.pct ?? 0}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary transition-[width] duration-500"
                   style={{ width: `${installing.pct ?? 0}%` }} />
            </div>
          </div>
        ) : t.locked ? (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Lock className="size-3.5" /> Requires a license key
          </span>
        ) : (
          <Button size="sm" disabled={!canInstall || !compatible}
                  onClick={onInstall}
                  title={!compatible ? "No build for this box's accelerator" : undefined}>
            <Download className="size-4" />
            {t.installed_slug ? "Install again" : "Install"}
          </Button>
        )}
      </div>
    </div>
  );
}

function LicenseCard({ lic, onChanged }) {
  const toast = useToast();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  async function activate() {
    if (!key.trim()) return;
    setBusy(true);
    try {
      const d = await api.post("/api/hub/store/license", { key: key.trim() });
      toast.success("License activated",
        `Pro tier until ${fmtDate(d.license?.expires_at)}.`);
      setKey("");
      onChanged?.();
    } catch (e) {
      toast.error("The key was not accepted", e.message);
    } finally { setBusy(false); }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.del("/api/hub/store/license");
      setConfirmRemove(false);
      toast.info("License removed", "This device is back on the free tier.");
      onChanged?.();
    } catch (e) {
      toast.error("Could not remove the license", e.message);
    } finally { setBusy(false); }
  }

  // Expiry inside 30 days is worth a heads-up, not an alarm.
  const soon = lic.valid && lic.expires_at
    && lic.expires_at - Date.now() / 1000 < 30 * 86400;

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-center gap-2">
        <KeyRound className="size-4 text-muted-foreground" />
        <p className="text-sm font-semibold">Store access</p>
        <Badge variant={lic.valid ? "default" : "secondary"}>
          {lic.valid ? `Pro · until ${fmtDate(lic.expires_at)}` : "Free tier"}
        </Badge>
        {soon && (
          <span className="text-xs text-amber-600 dark:text-amber-500">
            expires soon - ask for a renewal key
          </span>
        )}
        {!lic.valid && lic.present && (
          <span className="text-xs text-amber-600 dark:text-amber-500">{lic.reason}</span>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {/* The key is write-only: pasted, verified, never displayed back. */}
        <Input
          className="min-w-0 flex-1 font-mono text-xs"
          placeholder="Paste a license key (IRIV1.…) - activation needs internet"
          value={key}
          autoComplete="off"
          onChange={(e) => setKey(e.target.value)}
        />
        <Button size="sm" disabled={busy || !key.trim()} onClick={activate}>
          Activate
        </Button>
        {lic.present && (
          <Button size="sm" variant="outline" disabled={busy}
                  onClick={() => setConfirmRemove(true)}>
            Remove key
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={confirmRemove}
        onOpenChange={setConfirmRemove}
        title="Remove the license key?"
        description="This device drops back to the free catalogue. The key itself stays valid - it can be pasted again on this device."
        confirmLabel="Remove key"
        busy={busy}
        onConfirm={remove}
      />
    </div>
  );
}
