// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Software update - the About page's card for updating the box by hand.
//
// There is no online update. The admin picks a release file (irivai-<version>
// .tar.gz, built with scripts/make_release.py), the box checks it and says what
// it is, and only then is there a button that installs. The box keeps the live
// version and the one before it, so an update can be undone.
import { useCallback, useEffect, useRef, useState } from "react";
import { Package, Upload } from "lucide-react";
import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}

export default function SoftwareUpdateCard() {
  const { user } = useApp();
  const toast = useToast();
  const confirm = useConfirm();
  const fileRef = useRef(null);
  const [st, setSt] = useState(null);
  const [busy, setBusy] = useState("");          // "" | upload | install | rollback | restart
  const [over, setOver] = useState(false);       // a file is being dragged over the drop area

  const load = useCallback(async () => {
    try { setSt(await api.get("/api/hub/update/status")); }
    catch { /* the card then shows nothing rather than a wrong state */ }
  }, []);
  useEffect(() => { if (user?.is_admin) load(); }, [user?.is_admin, load]);
  if (!user?.is_admin) return null;

  // The hub restarts after a switch: wait for it to go away and come back, then
  // reload so the page is the new version's.
  async function waitForRestart() {
    setBusy("restart");
    let wentDown = false;
    const until = Date.now() + 4 * 60 * 1000;
    while (Date.now() < until) {
      await new Promise((r) => setTimeout(r, 3000));
      try {
        await api.get("/api/hub/update/status");
        if (wentDown) { window.location.reload(); return; }
      } catch { wentDown = true; }
    }
    setBusy("");
    toast.warn("The box is taking longer than usual", "Reload this page in a minute.");
  }

  function onPicked(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    upload(file);
  }

  function onDrop(e) {
    e.preventDefault();
    setOver(false);
    if (!busy) upload(e.dataTransfer.files?.[0]);
  }

  async function upload(file) {
    if (!file) return;
    setBusy("upload");
    try {
      await api.post("/api/hub/update/upload", file);
      await load();
    } catch (err) {
      toast.error("That file cannot be used", err.message);
      await load();
    } finally { setBusy(""); }
  }

  async function discard() {
    try { await api.del("/api/hub/update/upload"); } catch { /* gone already */ }
    load();
  }

  async function install() {
    const v = st?.staged?.version;
    if (!await confirm({
      title: `Install version ${v}?`,
      description: "The dashboard and every system on this box restart, which takes "
        + "about a minute. Settings, accounts and recorded data are kept, and the "
        + "current version stays on the box so you can go back.",
    })) return;
    setBusy("install");
    try {
      await api.post("/api/hub/update/install", {});
      await waitForRestart();
    } catch (err) {
      toast.error("The update was not installed", err.message);
      setBusy("");
      load();
    }
  }

  async function rollback() {
    const p = st?.previous;
    if (!await confirm({
      title: `Go back to version ${p?.version}?`,
      description: "The dashboard and every system restart on the previous version.",
      destructive: true,
    })) return;
    setBusy("rollback");
    try {
      await api.post("/api/hub/update/rollback", {});
      await waitForRestart();
    } catch (err) {
      toast.error("Could not go back", err.message);
      setBusy("");
    }
  }

  const staged = st?.staged;
  const ready = st?.ready;
  return (
    <section className="flex h-full flex-col rounded-xl border bg-card p-4" data-software-update>
      <div className="mb-3 flex items-start gap-2.5">
        <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
          <Package size={15} aria-hidden="true" />
        </span>
        <div>
          <h2 className="text-sm font-semibold leading-tight">Software update</h2>
          <div className="mt-0.5 text-xs text-muted-foreground">
            Update this box with a release file. Nothing is downloaded from the internet.
          </div>
        </div>
      </div>

      {!st ? (
        <p className="text-sm text-muted-foreground">Reading…</p>
      ) : !ready ? (
        <p className="text-sm text-muted-foreground">
          This box is not set up for software updates. Use the latest OS image.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
            <div>
              <div className="text-xs text-muted-foreground">Running</div>
              <div className="font-semibold">{st.live?.version || "-"}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Previous</div>
              <div className="font-semibold">{st.previous?.version || "none"}</div>
            </div>
          </div>

          {staged && !staged.error && (
            <div className="mt-3 rounded-lg border p-3 text-sm">
              <div>
                Ready to install <strong>version {staged.version}</strong>
                <span className="text-muted-foreground">
                  {" "}· {fmtBytes(staged.bytes)} · {staged.files.toLocaleString()} files
                </span>
              </div>
            </div>
          )}
          {staged?.error && (
            <p className="mt-3 text-sm text-destructive">{staged.error}</p>
          )}

          <input ref={fileRef} type="file" accept=".gz,.tgz,application/gzip"
                 style={{ display: "none" }} onChange={onPicked} />
          {!(staged && !staged.error) && (
            <div role="button" tabIndex={0} aria-label="Choose an update file"
                 aria-disabled={!!busy}
                 className="mt-3 flex min-h-36 flex-1 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed px-4 py-5 text-center outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
                 style={{ borderColor: over ? "var(--primary, #04bade)" : undefined,
                          background: over ? "color-mix(in srgb, var(--primary, #04bade) 8%, transparent)" : undefined,
                          opacity: busy ? 0.6 : 1 }}
                 onClick={() => { if (!busy) fileRef.current?.click(); }}
                 onKeyDown={(e) => {
                   if ((e.key === "Enter" || e.key === " ") && !busy) {
                     e.preventDefault();
                     fileRef.current?.click();
                   }
                 }}
                 onDragOver={(e) => { e.preventDefault(); if (!busy) setOver(true); }}
                 onDragLeave={() => setOver(false)}
                 onDrop={onDrop}>
              <Upload size={22} className="text-muted-foreground" aria-hidden="true" />
              <div className="text-sm font-medium">
                {busy === "upload" ? "Uploading…" : "Drag & drop release file here"}
              </div>
              <div className="text-xs text-muted-foreground">or click to browse from device</div>
            </div>
          )}

          <div className="mt-auto grid gap-2 pt-3">
            {staged && !staged.error ? (
              <>
                <button type="button" className="btn btn-accent" disabled={!!busy}
                        style={{ width: "100%", justifyContent: "center" }}
                        onClick={install}>
                  {busy === "install" || busy === "restart" ? "Installing…" : "Install"}
                </button>
                <button type="button" className="btn btn-hover-blue" disabled={!!busy}
                        style={{ width: "100%", justifyContent: "center" }}
                        onClick={discard}>
                  Cancel
                </button>
              </>
            ) : (
              <button type="button" className="btn btn-hover-blue" disabled={!!busy}
                      style={{ width: "100%", justifyContent: "center" }}
                      onClick={() => fileRef.current?.click()}>
                Choose update file
              </button>
            )}
            {st.previous && !staged && (
              <button type="button" className="btn btn-hover-blue" disabled={!!busy}
                      style={{ width: "100%", justifyContent: "center" }}
                      onClick={rollback}>
                Go back to {st.previous.version}
              </button>
            )}
          </div>
          {busy === "restart" && (
            <p className="mt-2 text-xs text-muted-foreground">
              Restarting - this page reloads by itself when the box is back.
            </p>
          )}
        </>
      )}
    </section>
  );
}
