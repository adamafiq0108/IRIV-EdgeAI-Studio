// NewStoragePrompt - "New storage detected", the popup for a drive fitted
// after the box was installed (Tok Aba 2026-10-07; core/services/
// storage_setup.py). Mounted by HubShell for the master account only: every
// route behind it is master-gated, and a site admin who saw it could only
// be refused.
//
// The flow is the one Tok Aba asked for, one question per screen:
//   ask      Use this drive for storing files?   No = remembered for THIS
//                                                drive (server-side), never
//                                                asked again; X = not now.
//   format   Format it?   Yes -> confirm, No -> use what is on it (refused
//                         here AND on the server when it has no Linux
//                         filesystem, with Format offered instead).
//   confirm  The red one: everything on the drive is erased; the drive's
//            model, size and partitions are shown, and FORMAT must be typed.
//   running  The helper's steps. The dialog cannot be closed and nothing
//            behind it can be clicked until it ends.
//   done     Restart now / Later (only when a restart is actually needed).
//
// Only a drive with NOTHING of it mounted is ever asked about (Tok Aba): a
// mounted one is ready as it is, and one mounted at /mnt/nvme whose folders
// are not writable is repaired by the box itself (storage_job.keep_writable),
// never offered here.
//
// The drive is named by its id from the box's own list, never a device
// path: the server looks it up again, and the root helper looks a third
// time, right before it touches anything.
//
// "Configure Storage" on Data Retention opens it on purpose, declined drives
// included, through a window event (OPEN_EVENT) so neither component has to
// know where the other is mounted.
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, HardDrive, Loader2 } from "lucide-react";
import { api } from "../api/client.js";
import { usePoll } from "../api/hooks.js";
import { useToast } from "./ui/Toast.jsx";
import { Button } from "./ui/button.jsx";
import { Input } from "./ui/input.jsx";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "./ui/dialog.jsx";

export const OPEN_EVENT = "iriv:storage-setup";
const CONFIRM = "FORMAT";
// A finished job the admin has already closed is not shown again on the
// next poll; per browser session, keyed by when it finished.
const SEEN_KEY = "storage.setup.seen";
// "Not now" (the X) for a drive, this session only. "No" is the server's.
const LATER_KEY = "storage.setup.later";

function sessionGet(key) {
  try { return sessionStorage.getItem(key) || ""; } catch { return ""; }
}
function sessionSet(key, value) {
  try { sessionStorage.setItem(key, value); } catch { /* private mode */ }
}

function DriveCard({ dev }) {
  return (
    <div data-storage-drive="1" className="rounded-lg border bg-muted/30 p-3 text-sm">
      <div className="flex items-center gap-2 font-medium">
        <HardDrive className="size-4 shrink-0 text-muted-foreground" />
        <span>{dev.model}</span>
        <span className="text-muted-foreground">{dev.size_text} {dev.kind}</span>
      </div>
      {/* The box's own drive that did not mount (an active fstab line names
          it): Mount brings it back; it is never offered a Format (Pipi). */}
      {dev.expected && (
        <p data-storage-expected="1" className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-400">
          {dev.expected_label || "This box's storage drive, not mounted"}
        </p>
      )}
      {dev.partitions?.length ? (
        <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
          {dev.partitions.map((p) => (
            <li key={p.path}>
              {p.path}: {p.size_text}, {p.fstype || "no filesystem"}
              {p.label ? ` "${p.label}"` : ""}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">
          Empty: no partitions and no filesystem.
        </p>
      )}
      {dev.kind === "USB" && (
        <p className="mt-2 text-xs text-muted-foreground">
          A USB drive keeps event archives, snapshots and clips for up to 60
          days; 12 months needs an NVMe drive.
        </p>
      )}
    </div>
  );
}

export default function NewStoragePrompt() {
  const toast = useToast();
  const { data, reload } = usePoll("/api/hub/storage/new", 5000);
  // The drive on screen, the step, and the manual list ("Configure Storage").
  const [dev, setDev] = useState(null);
  const [stage, setStage] = useState("");
  const [choices, setChoices] = useState(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Before "use it as it is" is offered: does the drive already hold
  // another box's IRIV software? (Tok Aba; GET /api/hub/storage/inspect,
  // a read-only look by the root helper). null | "looking" | {foreign, message}
  const [look, setLook] = useState(null);

  function toFormatStage() {
    setError(""); setLook(null); setStage("format");
    if (!dev?.usable || dev.expected) return;
    setLook("looking");
    api.get(`/api/hub/storage/inspect?id=${encodeURIComponent(dev.id)}`)
      .then(setLook)
      .catch((e) => { setLook(null); setError(e.message); });
  }
  const [manual, setManual] = useState(null);   // the devices answer, manual mode

  const job = data?.job;
  const canSetup = (manual || data)?.can_setup;
  const needs = (manual || data)?.needs;
  const running = job?.state === "running";

  // A job in flight (or just finished, not yet seen) owns the dialog, even
  // in a tab that did not start it: the disk is busy whoever asked.
  useEffect(() => {
    if (!job || job.state === "idle") return;
    if (running) {
      if (job.device) setDev(job.device);
      setStage("running");
    } else if ((job.state === "done" || job.state === "failed")
               && String(job.finished) !== sessionGet(SEEN_KEY)) {
      if (job.device) setDev(job.device);
      setStage(job.state);
    }
  }, [job, running]);

  // The automatic ask: a new drive, nothing else on screen.
  useEffect(() => {
    if (stage || !data?.prompt) return;
    if (sessionGet(LATER_KEY) === data.prompt.id) return;
    setDev(data.prompt);
    setStage("ask");
  }, [data, stage]);

  // Progress wants better than the 5 s poll.
  useEffect(() => {
    if (stage !== "running") return undefined;
    const id = setInterval(() => reload().catch(() => {}), 1000);
    return () => clearInterval(id);
  }, [stage, reload]);

  // "Configure Storage" on Data Retention.
  const openManual = useCallback(async () => {
    try {
      const d = await api.get("/api/hub/storage/devices");
      setManual(d);
      if (d.has_drive) {
        toast.info("A drive is already in use",
          "This box already stores its files on a drive at /mnt/nvme.");
        return;
      }
      if (!d.devices?.length) {
        toast.info("No drive to set up",
          "Plug in an NVMe, USB or SATA drive; the system's own storage is never offered.");
        return;
      }
      setError(""); setTyped("");
      if (d.devices.length === 1) { setDev(d.devices[0]); setStage("ask"); }
      else { setChoices(d.devices); setStage("pick"); }
    } catch (e) { toast.error("Could not list the drives", e.message); }
  }, [toast]);
  useEffect(() => {
    window.addEventListener(OPEN_EVENT, openManual);
    return () => window.removeEventListener(OPEN_EVENT, openManual);
  }, [openManual]);

  function close() {
    if (stage === "running") return;              // never mid-format
    if (stage === "done" || stage === "failed") sessionSet(SEEN_KEY, String(job?.finished || ""));
    else if (dev && !manual) sessionSet(LATER_KEY, dev.id);
    setStage(""); setDev(null); setChoices(null); setManual(null);
    setTyped(""); setError("");
  }

  async function decline() {
    setBusy(true);
    try {
      await api.post("/api/hub/storage/decline", { id: dev.id });
      // The last poll still names it until the next one lands.
      sessionSet(LATER_KEY, dev.id);
      reload().catch(() => {});
      toast.info("Drive left as it is",
        "It is not used and not asked about again. Settings > Data Retention > Configure Storage brings it back.");
      setStage(""); setDev(null); setManual(null);
    } catch (e) { toast.error("Could not save that", e.message); }
    finally { setBusy(false); }
  }

  async function begin(format) {
    setBusy(true); setError("");
    try {
      await api.post("/api/hub/storage/setup",
                     { id: dev.id, format, confirm: format ? typed.trim() : "" });
      setStage("running");
      reload().catch(() => {});
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function rebootNow() {
    setBusy(true);
    try {
      await api.post("/api/hub/panel/power", { action: "reboot" });
      sessionSet(SEEN_KEY, String(job?.finished || ""));
      toast.success("Restarting", "The dashboard comes back in about a minute.");
      setStage("");
    } catch (e) { toast.error("Could not restart the box", e.message); }
    finally { setBusy(false); }
  }

  if (!stage) return null;
  const pct = job?.steps ? Math.round((100 * (job.step || 0)) / job.steps) : 5;

  return (
    <Dialog open onOpenChange={(o) => { if (!o) close(); }}
            disablePointerDismissal>
      <DialogContent className="cy-app sm:max-w-md" data-storage-setup={stage}
                     showCloseButton={stage !== "running"}>
        {stage === "pick" && (
          <>
            <DialogHeader>
              <DialogTitle>Set up storage</DialogTitle>
              <DialogDescription>Choose the drive to use for storing files.</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              {(choices || []).map((d) => (
                <button key={d.id} type="button" className="block w-full text-left"
                        onClick={() => { setDev(d); setStage("ask"); }}>
                  <DriveCard dev={d} />
                </button>
              ))}
            </div>
          </>
        )}

        {stage === "ask" && dev && (
          <>
            <DialogHeader>
              <DialogTitle>New storage detected</DialogTitle>
              <DialogDescription>Use this drive for storing files?</DialogDescription>
            </DialogHeader>
            <DriveCard dev={dev} />
            {!canSetup && needs && (
              <p className="text-sm text-amber-700 dark:text-amber-400">
                This box cannot set up a drive yet: {needs}.
              </p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" disabled={busy} onClick={decline}>
                No
              </Button>
              <Button type="button" disabled={busy || !canSetup}
                      onClick={toFormatStage}>
                Yes
              </Button>
            </DialogFooter>
          </>
        )}

        {stage === "format" && dev && (
          <>
            <DialogHeader>
              <DialogTitle>{dev.expected ? "Mount the drive?" : "Format the drive?"}</DialogTitle>
              <DialogDescription>
                {dev.expected
                  ? "This is the box's own storage drive and it did not mount. "
                    + "Mounting it brings it back with everything on it; it is "
                    + "never formatted from here."
                  : "Formatting erases everything on it and prepares it for the box "
                    + "(best for a new drive). Not formatting keeps what is on it and "
                    + "uses it as it is, which needs a Linux filesystem (ext4)."}
              </DialogDescription>
            </DialogHeader>
            <DriveCard dev={dev} />
            {!dev.usable && (
              <p data-storage-refusal="1" className="text-sm text-destructive">
                {dev.mount_refusal}
              </p>
            )}
            {look === "looking" && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Checking what is on the drive
              </p>
            )}
            {look?.foreign && (
              <p data-storage-foreign="1" className="text-sm text-destructive">
                {look.message}
              </p>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              {/* Another box's IRIV drive: Format only (Tok Aba). */}
              {!look?.foreign && (
                <Button type="button" variant="outline"
                        disabled={busy || !dev.usable || look === "looking"}
                        onClick={() => begin(false)}>
                  {dev.expected ? "Mount it" : "No, use it as it is"}
                </Button>
              )}
              {!dev.expected && (
                <Button type="button" variant="destructive" disabled={busy}
                        onClick={() => { setTyped(""); setError(""); setStage("confirm"); }}>
                  Yes, format it
                </Button>
              )}
            </DialogFooter>
          </>
        )}

        {stage === "confirm" && dev && (
          <form onSubmit={(e) => { e.preventDefault();
                                   if (typed.trim() === CONFIRM && !busy) begin(true); }}>
            <DialogHeader>
              <DialogTitle className="text-destructive">Erase this drive?</DialogTitle>
            </DialogHeader>
            <div role="alert" data-storage-erase-warning="1"
                 className="my-3 flex items-start gap-2 rounded-lg border border-destructive/60 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <span>
                <strong>ALL data on this drive will be erased</strong> and cannot
                be recovered: every partition and every file on it.
              </span>
            </div>
            <DriveCard dev={dev} />
            <label htmlFor="storage-confirm" className="mt-3 block text-sm">
              Type <span className="font-semibold">{CONFIRM}</span> to erase {dev.model} ({dev.size_text})
            </label>
            <Input id="storage-confirm" className="mt-1.5" value={typed} autoComplete="off"
                   autoFocus placeholder={CONFIRM} onChange={(e) => setTyped(e.target.value)} />
            {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
            <DialogFooter className="mt-4">
              <Button type="button" variant="outline" disabled={busy}
                      onClick={() => setStage("format")}>
                Cancel
              </Button>
              <Button type="submit" variant="destructive"
                      disabled={busy || typed.trim() !== CONFIRM}>
                Erase and format
              </Button>
            </DialogFooter>
          </form>
        )}

        {stage === "running" && (
          <>
            <DialogHeader>
              <DialogTitle>Setting up the drive</DialogTitle>
              <DialogDescription>
                Do not unplug the drive or switch the box off.
              </DialogDescription>
            </DialogHeader>
            {dev && <DriveCard dev={dev} />}
            <div className="space-y-1.5" aria-live="polite">
              <div className="flex items-center gap-2 text-sm">
                <Loader2 className="size-4 animate-spin" />
                <span>{job?.text || "Starting"}
                  {job?.steps ? ` (step ${job.step} of ${job.steps})` : ""}</span>
              </div>
              <div className="h-2 overflow-hidden rounded bg-muted">
                <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
              </div>
            </div>
          </>
        )}

        {stage === "done" && (
          <>
            <DialogHeader>
              <DialogTitle>The drive is ready</DialogTitle>
              <DialogDescription>{job?.message}</DialogDescription>
            </DialogHeader>
            {job?.reboot ? (
              <>
                <p className="text-sm">
                  Restart the box so every system saves its files to the drive.
                </p>
                <DialogFooter>
                  <Button type="button" variant="outline" disabled={busy} onClick={close}>
                    Later
                  </Button>
                  <Button type="button" disabled={busy} onClick={rebootNow}>
                    Reboot now
                  </Button>
                </DialogFooter>
              </>
            ) : (
              <DialogFooter>
                <Button type="button" onClick={close}>Close</Button>
              </DialogFooter>
            )}
          </>
        )}

        {stage === "failed" && (
          <>
            <DialogHeader>
              <DialogTitle>The drive was not set up</DialogTitle>
              <DialogDescription>{job?.message}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" onClick={close}>Close</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
