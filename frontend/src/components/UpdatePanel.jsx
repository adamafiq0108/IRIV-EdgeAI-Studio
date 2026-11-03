// UpdatePanel - platform updates, shown inside the header bell's System tab
// (NotificationCenter.jsx). It had a nav item of its own once; an update is
// something the box tells you, not a page you remember to go and read.
//
// An appliance in the field has no shell and no git, so this is the whole
// update story: what it is running, what is available, and one button. The
// button is deliberately not automatic - applying an update restarts every
// system on the box, and the person who decides when a line goes down for a
// minute is the admin standing next to it, not a timer.
//
// `channel` picks which release stream to read: "stable" (what every appliance
// takes) or "dev" (the developer build). The dev channel is only ever passed
// in when the SERVER said this account and this box both have one - the panel
// does not decide that, and could not: every endpoint re-checks.
//
// The progress bar has to survive the thing it is reporting on: applying an
// update restarts the hub serving this page, so mid-run fetches fail for a few
// seconds. Those failures are expected and are NOT shown as errors - the last
// known phase stays on screen until the new hub answers.
import { useContext, useEffect, useRef, useState } from "react";
import { api, isSetupRequired } from "../api/client.js";
import { AppContext } from "../state/app.jsx";
import { useToast } from "./ui/Toast.jsx";
import { offersUpdate } from "../lib/version.js";
import { releaseNoteGroups, releaseNoteItems, roman, noteTag, tagClass } from "./releaseNotes.js";
import { Button } from "./ui/button.jsx";
import { Input, PasswordInput } from "./ui/input.jsx";
import { Label } from "./ui/label.jsx";
import { Badge } from "./ui/badge.jsx";
import { Switch } from "./ui/switch.jsx";
import { ConfirmDialog } from "./ui/dialogs.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { SkelPage } from "./Loading.jsx";
import {
  ArrowUpCircle, CheckCircle2, RotateCcw, KeyRound, RefreshCw, AlertTriangle,
  Upload, FileArchive,
} from "lucide-react";
import { apiUrl, pageUrl } from "../lib/base.js";

// Phases the applier reports, in order, with what to tell someone watching.
const PHASES = {
  starting:   "Starting",
  preflight:  "Checking this box can take the update",
  download:   "Downloading",
  unpack:     "Unpacking",
  venv:       "Installing dependencies",
  rootsteps:  "Updating the box's helpers",
  snapshot:   "Saving a fallback copy",
  flip:       "Switching over",
  restart:    "Restarting the platform",
  gate:       "Waiting for everything to come back",
  appsync:    "Updating system code",
  rollback:   "Rolling back",
};
const TERMINAL = ["done", "failed", "rolled_back", "refused", "idle"];

//: A cached check older than this is refreshed as the card opens.
const STALE_S = 10 * 60;
//: The event id the apply route answers with when a newer release appeared
//: between the check and the press (core/services/log_catalog.py).
const SUPERSEDED = "SYS-1034";
//: The biggest offline update file the box takes (update_offline.MAX_UPLOAD);
//: checked here too so a wrong file is refused before it is sent.
const MAX_FILE = 512 * 1024 * 1024;

/**
 * The release's own words, as an operator should read them.
 *
 * A tag's annotation is a COMMIT MESSAGE, and a commit message carries things
 * written for the repository rather than for the person standing at the box:
 * trailers (Co-Authored-By, Signed-off-by), and a subject line that repeats
 * the version this panel has already shown twice. Both were being printed
 * verbatim -- an appliance's release notes reading "chore: bump to v2.1.5"
 * followed by an email address says nothing about what changed and looks like
 * a leak, because it is one.
 *
 * Anything genuinely written for the reader survives untouched; this only
 * removes lines that were never addressed to them.
 */


function ago(epoch) {
  if (!epoch) return "never";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (secs < 90) return "just now";
  if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
  if (secs < 172800) return `${Math.round(secs / 3600)} h ago`;
  return `${Math.round(secs / 86400)} days ago`;
}

export default function UpdatePanel({ embedded = false, channel = "stable",
                                      onStateChange, onDone }) {
  const toast = useToast();
  const confirm = useConfirm();
  // The shell's bootstrap carries the running version too (it is answered on
  // every box state, the setup gate included). It is the fallback for the
  // version line when the update state itself cannot be read: a refused
  // /api/hub/update used to leave the card reading "This box is running v".
  const app = useContext(AppContext);
  const bootVersion = app?.boot?.version || "";
  const [state, setState] = useState(null);
  const [otaReady, setOtaReady] = useState(true);
  // A declared dev box (the server says; any admin). On the Customer build
  // tab it turns the Update confirm into a warning: a customer build has no
  // developer tools, so installing one removes them (Pipi gate, L4).
  const [devBox, setDevBox] = useState(false);
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(false);
  const [autoBusy, setAutoBusy] = useState(false);
  // "Update manually": the offline update file the box checked and holds,
  // waiting for Install ({id, version, notes, same_version}), or null.
  const [waiting, setWaiting] = useState(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef(null);
  const [tokenValue, setTokenValue] = useState("");
  // Which of the two ConfirmDialogs below is open: "apply" | "rollback" | "".
  const [dialog, setDialog] = useState("");
  const poll = useRef(null);
  // Whether this panel is still on screen. readStatus() is async, so closing
  // the popover between the fetch going out and coming back would otherwise
  // let watch() start a 2s interval with nobody left to clear it -- it would
  // keep polling until whatever job it saw reached a terminal phase, or
  // forever if it never did.
  const alive = useRef(true);
  // Held in a ref, not a dependency: the callback identity changes on every
  // parent render, and letting that restart the load effect would refetch the
  // update state several times a second.
  const report = useRef(onStateChange);
  report.current = onStateChange;

  const qs = `?channel=${encodeURIComponent(channel)}`;

  function publish(next) {
    setState(next);
    if (report.current) report.current(next);
  }

  // The account-setup gate (routes/auth.py SETUP_OPEN): the master is still on
  // the shipped password, so every write here answers 409. The client bounces
  // the tab to the setup screen on that answer; this is what the card and the
  // toast say in the meantime, in the panel's words with the way there, not
  // the server's lowercase sentence.
  const goSetup = () => window.location.assign(pageUrl("/account-setup"));
  function setupToast() {
    toast.error("Finish account setup first",
                "This box is still on the password it shipped with. Set your own "
                + "password, then come back here.",
                { action: { label: "Go to account setup", onClick: goSetup } });
  }

  async function load() {
    try {
      const d = await api.get(`/api/hub/update${qs}`);
      publish(d.update || {});
      setOtaReady(d.ota_ready !== false);
      setDevBox(!!d.dev_box);
      return d.update || {};
    } catch (e) {
      // What the card already knew (the version, the last check) stays; only
      // the error is new. A fresh load with nothing known keeps the version
      // the shell was given.
      const known = { current: bootVersion, ...(state || {}) };
      if (isSetupRequired(e)) {
        publish({ ...known, setup_required: true, error: "" });
        return;
      }
      publish({ ...known, error: e.message || "could not read the update state" });
    }
    return null;
  }

  // Re-reads when the channel switches: the two streams have different
  // versions, different notes, and one may have nothing published at all.
  //
  // The state read here is the box's CACHE, written by a timer every six
  // hours. Three releases went out in one day and a card opened between
  // the second and the third offered the second (Tok Aba, 2026-10-05). So
  // a cache older than a few minutes is refreshed as the card opens, with
  // no toast either way: the card itself is the answer. Only while "Check
  // for updates automatically" is on: with it off the box contacts GitHub
  // only when someone presses Check now or Update.
  useEffect(() => {
    alive.current = true;
    setState(null);
    load().then((u) => {
      if (!alive.current || !u || !u.token_set || u.busy || u.checking) return;
      if (!u.auto_check) return;
      if (Date.now() / 1000 - Number(u.checked_at || 0) > STALE_S) check(true);
    });
    readStatus();
    return () => { alive.current = false; clearInterval(poll.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel]);

  async function readStatus() {
    try {
      const d = await api.get("/api/hub/update/status");
      setStatus(d.status || null);
      if (d.status?.running || !TERMINAL.includes(d.status?.phase)) watch();
    } catch { /* not signed in yet, or the hub is restarting - ignore */ }
  }

  // Poll while a job runs. A failed fetch during the restart phase is the
  // update working, not the update broken, so nothing is cleared here.
  function watch() {
    clearInterval(poll.current);
    if (!alive.current) return;
    poll.current = setInterval(async () => {
      try {
        const d = await api.get("/api/hub/update/status");
        const s = d.status || {};
        setStatus(s);
        if (!s.running && TERMINAL.includes(s.phase)) {
          clearInterval(poll.current);
          load();
          if (onDone) onDone();
          if (s.phase === "done") {
            toast.success(`Now running ${s.version}`, "The update finished and every system is back.");
            offerReboot(s.version);
          } else if (s.phase === "rolled_back") {
            toast.error("The update was rolled back", s.error || "The new version did not come up, so the previous one was restored.");
          } else if (s.phase === "refused") {
            toast.error("The update was refused", s.error || "");
          } else if (s.phase === "failed") {
            toast.error("The update failed", s.error || "");
          }
        }
      } catch { /* the hub is restarting - keep the last phase on screen */ }
    }, 2000);
  }

  /** Ask for the restart that finishes an update.
   *
   *  The applier swaps the tree and restarts the hub and its masters, which
   *  is what puts the new version on this page. It does not restart the rest
   *  of the board -- the detection apps, the media server, the front panel,
   *  the root helpers -- so until the box comes up again it is part new
   *  version and part the one it had. That half-state is invisible, and it is
   *  what "I updated and nothing changed" is made of.
   *
   *  Asked, never taken: an update is applied by somebody standing at a line
   *  that may be running, and when the box restarts is their call. Saying
   *  Later leaves the note on the card, which offers the same button until it
   *  is done.
   */
  async function offerReboot(version) {
    const now = await confirm({
      title: "Restart the box to finish?",
      description: `This page is on ${version ? `v${version}` : "the new version"} `
        + "already. The detection apps and the rest of the board keep running the "
        + "version they started with until the box restarts, so restart now, or "
        + "later - nothing is lost either way, and the page keeps offering it.",
      confirmLabel: "Restart now",
      cancelLabel: "Later" });
    if (!now) {
      // The page itself is the old release's bundle, kept alive on purpose
      // through the hub restart (see watch). Left running it would carry the
      // old client code against the new server for as long as the tab stays
      // open -- the board report's Settings page drawn under a gate the old
      // bundle did not know. Reload it on the new release, after the toast
      // has been read.
      toast.info("Update finished", "Restart the box when you can - until then "
                 + "part of it still runs the version it had. This page reloads "
                 + "on the new version.");
      setTimeout(() => window.location.reload(), 3000);
      return;
    }
    try {
      await api.post("/api/hub/panel/power", { action: "reboot" });
      toast.success("Restarting", "The page comes back when the box does.");
    } catch (e) {
      toast.error("Could not restart the box", e.message);
    }
  }

  // `quiet`: the refresh the card makes for itself on opening; the card
  // shows the answer and no toast repeats it.
  async function check(quiet = false) {
    setChecking(true);
    try {
      const d = await api.post("/api/hub/update/check", { channel });
      if (d.update?.checking) {
        // Another check (the timer's, or a second click) is already out;
        // the answer it brings lands in the next GET. Not "up to date".
        if (!quiet) toast.info("Already checking for updates", "The result follows shortly.");
        setChecking(false);
        return;
      }
      publish(d.update || {});
      if (quiet) {
        // nothing to say: the card now shows what the box found
      } else if (offersUpdate(d.update)) {
        toast.success(
          `Version ${String(d.update.latest).replace(/^v/i, "")} is available`, "");
      } else if (!d.update?.error) {
        toast.success("This box is up to date", "");
      }
    } catch (e) {
      if (quiet) { /* the card keeps what it knew; Check now says why */ }
      else if (isSetupRequired(e)) setupToast();
      else toast.error("Could not check for updates", e.message);
    }
    setChecking(false);
  }

  // The switch is one per box (both channels); the server answers what it
  // stored, and the card shows that rather than what was clicked.
  async function setAuto(on) {
    setAutoBusy(true);
    try {
      const d = await api.post("/api/hub/update/auto", { enabled: on });
      publish({ ...(state || {}), auto_check: !!d.auto_check });
      toast.success(on ? "Automatic update checks are on"
                       : "Automatic update checks are off",
                    on ? "The box checks as soon as it has internet, then every few hours."
                       : "The box checks only when you press Check now.");
    } catch (e) {
      if (isSetupRequired(e)) setupToast();
      else toast.error("Could not change automatic update checks", e.message);
    }
    setAutoBusy(false);
  }

  // The box checks the file (signature, hash, version, which kind of box it
  // is for) before it answers; a refusal says why in plain words.
  async function uploadFile(file) {
    if (!file) return;
    if (file.size > MAX_FILE) {
      toast.error("This file cannot be used", "That file is too large to be an update file.");
      return;
    }
    setUploading(true);
    try {
      const d = await api.post("/api/hub/update/manual", file);
      setWaiting(d.offline || null);
    } catch (e) {
      if (isSetupRequired(e)) setupToast();
      else toast.error("This file cannot be used", e.data?.details || e.message);
    }
    setUploading(false);
  }

  async function cancelFile() {
    setWaiting(null);
    try { await api.del("/api/hub/update/manual"); } catch { /* it expires anyway */ }
  }

  async function installFile() {
    setDialog("");
    const file = waiting;
    try {
      const d = await api.post("/api/hub/update/manual/install",
                               { id: file?.id, reinstall: !!file?.same_version });
      setWaiting(null);
      setStatus(d.status || { phase: "starting", percent: 0, running: true });
      watch();
    } catch (e) {
      if (isSetupRequired(e)) { setupToast(); return; }
      toast.error("Could not start the update", e.data?.details || e.message);
      if (/no longer waiting/i.test(e.data?.details || "")) setWaiting(null);
    }
  }

  async function saveToken() {
    try {
      await api.post("/api/hub/update/token", { token: tokenValue, channel });
      setTokenValue("");
      toast.success("Update token saved", "");
      await check();
    } catch (e) {
      if (isSetupRequired(e)) setupToast();
      else toast.error("Could not save the token", e.message);
    }
  }

  async function run(kind) {
    setDialog("");
    const url = kind === "rollback"
      ? apiUrl("/api/hub/update/rollback") : apiUrl("/api/hub/update/apply");
    // Rollback carries no channel: it installs nothing, it points the box at a
    // release already on its disk, whichever channel put it there.
    const body = kind === "rollback" ? {} : { version: state?.latest, channel };
    try {
      const d = await api.post(url, body);
      setStatus(d.status || { phase: "starting", percent: 0, running: true });
      watch();
    } catch (e) {
      if (isSetupRequired(e)) { setupToast(); return; }
      if (kind !== "rollback" && e.data?.event_id === SUPERSEDED) {
        // The box asked GitHub again before installing and a newer release
        // had appeared. Not an error: the card re-reads and offers it.
        toast.info("A newer version is available", e.data?.details || e.message);
        load();
        return;
      }
      // The box's own words first ("could not confirm that this is the
      // newest version - no internet connection ..."), the catalog's
      // generic sentence only when it gave none.
      toast.error(kind === "rollback" ? "Could not roll back"
                                      : "Could not start the update",
                  e.data?.details || e.message);
      // Whatever refused it, the box's answer is fresher than this page: a
      // version withdrawn, a release still uploading, a token gone stale.
      if (kind !== "rollback") load();
    }
  }

  if (!state) return <SkelPage cards={2} rows={embedded ? 1 : 2} />;

  const busy = status && (status.running || !TERMINAL.includes(status.phase));
  const expired = state.error === "token_expired";
  const isDev = channel === "dev";
  const overDev = !isDev && devBox;
  // No token yet is a state to explain, not an error to flag: the token box
  // below says it. On the Developer build tab it is a dev box nobody has
  // given the developer token to.
  const noToken = !state.token_set && /^no update token/i.test(state.error || "");
  // The release tag ("v2.1.1") as a bare number: every "v" on this page is
  // written by the page, so carrying the tag's own would read "vv2.1.1".
  const latest = String(state.latest || "").replace(/^v/i, "");
  // Never a bare "v": the update state's version, else the shell's, else
  // said plainly. A version this page cannot name is a fact worth reading,
  // a lone letter is not.
  const current = String(state.current || bootVersion || "").replace(/^v/i, "");
  const pad = embedded ? "p-4" : "p-5";

  return (
    <div className={embedded ? "space-y-3" : "mx-auto max-w-3xl space-y-4"}>
      {/* What this box is, right now. */}
      <div className={`rounded-xl border bg-card ${pad}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm text-muted-foreground">This box is running</div>
            <div className="flex items-center gap-2">
              <div className="text-2xl font-semibold">
                {current ? `v${current}` : "unknown"}
              </div>
              {/* Named, not implied. Which stream a box is being offered
                  changes what "update" means, and a developer switching
                  between the two needs to see which one is on screen. */}
              {isDev && <Badge variant="outline">developer build</Badge>}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              Last checked {ago(state.checked_at)}
              {state.previous ? ` · can roll back to v${state.previous}` : ""}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={check}
                    disabled={checking || busy}>
              <RefreshCw className={"mr-1 size-4 " + (checking ? "animate-spin" : "")} />
              Check now
            </Button>
            {/* An offline update file, for a box with no internet or no
                token. Same install path as Update; the box checks the file
                before it offers to install it. */}
            <Button variant="outline" size="sm"
                    onClick={() => fileInput.current?.click()}
                    disabled={uploading || busy || !otaReady || !!state.setup_required}>
              <Upload className={"mr-1 size-4 " + (uploading ? "animate-pulse" : "")} />
              {uploading ? "Checking the file..." : "Update manually"}
            </Button>
            <input ref={fileInput} type="file" className="hidden" data-testid="ota-file"
                   accept=".gz,.tar.gz,application/gzip"
                   onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; uploadFile(f); }} />
            {state.previous && !busy && (
              <Button variant="outline" size="sm"
                      onClick={() => setDialog("rollback")}>
                <RotateCcw className="mr-1 size-4" />
                Roll back
              </Button>
            )}
          </div>
        </div>
        {/* Off on every box until an admin turns it on: off, nothing in the
            background talks to GitHub. One switch covers both channels. */}
        <div className="mt-3 flex items-center justify-between gap-3 border-t pt-3">
          <div className="min-w-0">
            <Label htmlFor="ota-auto">Check for updates automatically</Label>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {state.auto_check
                ? "The box checks as soon as it has internet, then every few hours."
                : "Off: the box checks only when you press Check now."}
            </div>
          </div>
          <Switch id="ota-auto" checked={!!state.auto_check}
                  disabled={autoBusy || !!state.setup_required}
                  onCheckedChange={(on) => setAuto(!!on)} />
        </div>
      </div>

      {/* An appliance that predates the release layout cannot flip a symlink
          it does not have. Said plainly, once, instead of failing at apply. */}
      {!otaReady && (
        <Note icon={AlertTriangle}>
          This box is not set up for over-the-air updates. Re-run the installer
          on it once; after that, updates arrive here.
        </Note>
      )}

      {/* A fine-grained token expires within a year. When it does, every check
          fails in a way that looks like a network fault unless it says so. */}
      {expired && (
        <Note icon={KeyRound}>
          The update token for this box has expired. Paste a new one below -
          nothing else about the box changes.
        </Note>
      )}

      {/* The setup gate, in the panel's words and with the way there. No
          token box under it: saving one is exactly the call it refuses. */}
      {state.setup_required && (
        <Note icon={KeyRound}>
          Finish account setup first. This box is still on the password it
          shipped with, so it cannot save an update token or check for updates
          yet.{" "}
          <Button variant="link" size="sm" className="h-auto p-0 text-sm"
                  onClick={goSetup}>
            Go to account setup
          </Button>
        </Note>
      )}
      {!state.token_set && !state.setup_required && (
        <div className={`rounded-xl border bg-card ${pad}`}>
          <Label htmlFor="ota-token">{isDev ? "Developer update token" : "Update token"}</Label>
          <p className="mb-2 mt-1 text-xs text-muted-foreground">
            {isDev
              ? "Developer builds come from their own release repo, with their own "
                + "read-only key. This box does not have that key yet. Paste it here "
                + "to get developer builds; it is stored on this box only and is "
                + "never shown again."
              : "A read-only key that lets this box download releases. It is stored "
                + "on this box only and is never shown again."}
          </p>
          <div className="flex gap-2">
            <PasswordInput id="ota-token" value={tokenValue} autoComplete="off"
                   placeholder="github_pat_…"
                   onChange={(e) => setTokenValue(e.target.value)} />
            <Button onClick={saveToken} disabled={!tokenValue.trim()}>Save</Button>
          </div>
        </div>
      )}

      {/* The offer. */}
      {offersUpdate(state) && !busy && (
        <div className={`rounded-xl border bg-card ${pad}`}>
          {/* The heading and the button share the top line; the notes get the
              full width beneath. Side by side, the notes were squeezed into
              whatever the button left over and read as a grey slab. */}
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <ArrowUpCircle className="size-5 shrink-0 text-primary" />
              <span className="truncate font-semibold">Version {latest}</span>
              <Badge variant="secondary" className="shrink-0">new</Badge>
            </div>
            <Button className="shrink-0" onClick={() => setDialog("apply")}
                    disabled={!!state.publishing}>
              Update
            </Button>
          </div>
          {/* The newest release is listed but its files are still going up
              (the publisher uploads them after creating it). It stays the
              one offered -- never the release below it -- and the box
              refuses to install it until the files are there. */}
          {state.publishing && (
            <div className="mt-2 text-xs text-muted-foreground">
              Version {latest} is still being published. Check again in a few minutes.
            </div>
          )}
          {releaseNoteItems(state.notes).length > 0 && (
            <div className="mt-3">
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                What changed
              </div>
              {/* bg-muted/40, not bg-background: the panel sits ON a card, and
                  in dark mode the background token is nearly the card's own
                  colour -- the box vanished and the text with it. The muted
                  tone is defined to sit against a card in BOTH themes, and the
                  text is full foreground rather than the muted pairing, since
                  release notes are meant to be read rather than glanced at.
                  A list rather than a <pre>: this is a summary somebody reads
                  to decide whether to take an update, and a wall of commit
                  text is what they were skipping. */}
              {/* Grouped by area: a release with thirty lines reads as a few
                  short lists under headings rather than one long list where
                  every line starts with the same word. */}
              <div className="max-h-64 space-y-3 overflow-auto rounded-lg border bg-muted/40 px-4 py-3 text-xs leading-relaxed text-foreground">
                {releaseNoteGroups(state.notes).map((g, gi) => (
                  <div key={gi}>
                    {g.area && (
                      <div className="mb-0.5 font-semibold">{roman(gi + 1)}. {g.area}</div>
                    )}
                    <ul className="list-disc space-y-1 pl-4 marker:text-muted-foreground">
                      {g.items.map((item, i) => <li key={i}>{(() => { const { tag, text } = noteTag(item); return (<>{tag && <span className={"mr-1.5 inline-block rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide " + tagClass(tag)}>{tag}</span>}{text}</>); })()}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* The uploaded file, checked and waiting. Its notes are the signed
          manifest's short notes, drawn the way the offer draws them. */}
      {waiting && !busy && (
        <div className={`rounded-xl border bg-card ${pad}`} data-testid="ota-waiting">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <FileArchive className="size-5 shrink-0 text-primary" />
              <span className="truncate font-semibold">Version {waiting.version} from a file</span>
              {waiting.same_version && <Badge variant="outline" className="shrink-0">installed now</Badge>}
            </div>
            <div className="flex shrink-0 gap-2">
              <Button variant="outline" onClick={cancelFile}>Cancel</Button>
              <Button onClick={() => setDialog("manual")}>
                {waiting.same_version ? "Reinstall" : "Install"}
              </Button>
            </div>
          </div>
          <div className="mt-2 text-xs text-muted-foreground">
            The file is signed and complete. It waits here for an hour.
          </div>
          {releaseNoteItems(waiting.notes).length > 0 && (
            <div className="mt-3 max-h-48 space-y-2 overflow-auto rounded-lg border bg-muted/40 px-4 py-3 text-xs leading-relaxed text-foreground">
              {releaseNoteGroups(waiting.notes).map((g, gi) => (
                <div key={gi}>
                  {g.area && <div className="mb-0.5 font-semibold">{roman(gi + 1)}. {g.area}</div>}
                  <ul className="list-disc space-y-1 pl-4 marker:text-muted-foreground">
                    {g.items.map((item, i) => <li key={i}>{noteTag(item).text}</li>)}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!offersUpdate(state) && !state.error && latest && !busy && (
        <Note icon={CheckCircle2}>
          This box is up to date.
        </Note>
      )}
      {/* The sentence is the server's wording by cause; the raw failure
          ("<urlopen error [Errno 113] ...>") is `details`, on hover only. */}
      {state.error && !expired && !noToken && (
        <Note icon={AlertTriangle} title={state.details}>{state.error}</Note>
      )}

      {/* An update that finished and has not been restarted since. Read from
          the box, so it survives a refresh, another browser, and a Later
          pressed yesterday. */}
      {status?.reboot_pending && !busy && (
        <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-500/40 bg-amber-500/5 p-3 text-xs">
          <AlertTriangle className="mt-px size-3.5 shrink-0 text-amber-500" />
          <div className="min-w-0 flex-1">
            <div className="font-medium">Restart to finish the update</div>
            <div className="mt-0.5 text-muted-foreground">
              This page runs v{status.version}. The detection apps and the rest of
              the board keep running the version they started with until the box
              restarts.
            </div>
          </div>
          <Button size="sm" variant="outline" className="shrink-0"
                  onClick={() => offerReboot(status.version)}>
            Restart
          </Button>
        </div>
      )}

      {/* Progress. Kept on screen after the job ends so the outcome is read,
          not guessed from a card that silently went back to normal. */}
      {status && status.phase !== "idle" && (
        busy ? (
          <div className={`rounded-xl border bg-card ${pad}`}>
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">
                {PHASES[status.phase] || phaseLabel(status.phase)}
                {status.version ? ` - v${status.version}` : ""}
              </span>
              <span className="text-muted-foreground">{status.percent}%</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary transition-all"
                   style={{ width: `${Math.min(100, status.percent)}%` }} />
            </div>
            {status.detail && (
              <div className="mt-2 text-xs text-muted-foreground">{status.detail}</div>
            )}
          </div>
        ) : (
          // FINISHED, so no bar. A full progress bar sitting under a card that
          // offers a newer version reads as an update running right now; the
          // outcome is one line, and the failures keep their colour because
          // those are the ones somebody has to do something about.
          <div className={"flex items-start gap-2 rounded-xl border bg-card p-3 text-xs "
            + (status.phase === "done" ? "text-muted-foreground" : "text-destructive")}>
            {status.phase === "done"
              ? <CheckCircle2 className="mt-px size-3.5 shrink-0" />
              : <AlertTriangle className="mt-px size-3.5 shrink-0" />}
            <span>
              {status.phase === "done"
                ? `Last update finished - v${status.version}`
                : `${phaseLabel(status.phase)}${status.version ? ` - v${status.version}` : ""}`}
              {status.error ? `: ${status.error}` : ""}
            </span>
          </div>
        )
      )}

      <ConfirmDialog
        open={dialog === "apply"}
        onOpenChange={(o) => !o && setDialog("")}
        title={overDev ? `Install the customer build v${latest}?`
                       : `Update this box to v${latest}?`}
        description={(overDev
            ? "This is a developer box. The customer build has no developer tools, "
              + "so installing it removes them from this box. Roll back puts the "
              + "developer build back. "
            : "")
            + "Every system on this box stops detecting while it updates, "
            + "for about a minute or two. If the new version does not come back "
            + "up, the box puts the current one back on its own."}
        confirmLabel={overDev ? "Install customer build" : "Update now"}
        onConfirm={() => run("apply")}
      />
      <ConfirmDialog
        open={dialog === "manual"}
        onOpenChange={(o) => !o && setDialog("")}
        title={waiting?.same_version ? `Install v${waiting?.version} again?`
                                     : `Update this box to v${waiting?.version} from the file?`}
        description={(waiting?.same_version
            ? `This box already runs v${waiting?.version}. Installing it again puts a `
              + "fresh copy of the same version in place. "
            : "")
            + "Every system on this box stops detecting while it updates, "
            + "for about a minute or two. If the new version does not come back "
            + "up, the box puts the current one back on its own."}
        confirmLabel={waiting?.same_version ? "Reinstall" : "Update now"}
        onConfirm={installFile}
      />
      <ConfirmDialog
        open={dialog === "rollback"}
        onOpenChange={(o) => !o && setDialog("")}
        title={`Go back to v${state.previous}?`}
        description={"This box will restart on the previous version. Systems "
            + "stop detecting for about a minute."}
        confirmLabel="Roll back"
        onConfirm={() => run("rollback")}
      />
    </div>
  );
}

function phaseLabel(phase) {
  return {
    done: "Finished", failed: "Failed", rolled_back: "Rolled back",
    refused: "Not applied",
  }[phase] || phase;
}

function Note({ icon: Icon, title, children }) {
  return (
    <div className="flex items-start gap-2 rounded-xl border bg-card p-4 text-sm text-muted-foreground">
      <Icon className="mt-0.5 size-4 shrink-0" />
      <span title={title || undefined}>{children}</span>
    </div>
  );
}
