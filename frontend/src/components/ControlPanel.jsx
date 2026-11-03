// System Control - the hub's table of every system: status, pipeline type,
// camera source, model, live FPS, an autostart toggle, and
// run / restart / stop for each one's master (admin only). Every per-system
// fact - including the one-word state - comes from /api/hub/pipelines; /api/hub
// supplies only the list of systems. One source per fact is deliberate: mixing
// two polls is what made this table flicker. This page comes BEFORE the Live
// Preview in the nav: choose what runs here, then watch it there.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { usePoll } from "../api/hooks.js";
import { useApp } from "../state/app.jsx";
import { ErrorNote, SkelTable } from "./Loading.jsx";
import { Button } from "./ui/button.jsx";
import { FormDialog } from "./ui/dialogs.jsx";
import { useToast } from "./ui/Toast.jsx";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "./ui/table.jsx";
import { Loader2, Pencil, Play, RotateCw, Square } from "lucide-react";
import { explainCamError } from "../lib/camErrors.js";

// The three states a system can be in, straight from /api/hub/pipelines.
//
// This used to be computed in the browser from TWO independently-polled
// endpoints, so the word changed whenever the two answers arrived at different
// moments - the table appeared to flicker while nothing was happening. The
// server now decides, and the dot and the word read the same field, so they
// cannot disagree.
const STATE = {
  running: { dot: "bg-emerald-500", text: "text-emerald-600 dark:text-emerald-400", label: "Running", badge: "ui-badge-ok" },
  online:  { dot: "bg-sky-500",     text: "text-sky-600 dark:text-sky-400",         label: "Online", badge: "ui-badge-warn" },
  offline: { dot: "bg-slate-400",   text: "text-muted-foreground",                  label: "Offline", badge: "ui-badge-muted" },
  // Powered off on the Hub: its master may answer (systemd restarts it) but
  // nothing runs until Power on -- or a detection start -- brings it back.
  powered_off: { dot: "bg-slate-400", text: "text-muted-foreground",                label: "Powered off", badge: "ui-badge-muted" },
};

//: A start that never lands must not spin forever.
const FLIP_TIMEOUT_MS = 45000;

function Toggle({ on, disabled, onChange, title }) {
  return (
    <button type="button" disabled={disabled} title={title}
            onClick={() => onChange(!on)}
            style={{ width: 36, height: 20, borderRadius: 999, border: "none",
                     cursor: disabled ? "default" : "pointer",
                     background: on ? "#3b82f6" : "rgba(128,128,128,.35)",
                     position: "relative", transition: "background .15s" }}>
      <span style={{ position: "absolute", top: 2, left: on ? 18 : 2,
                     width: 16, height: 16, borderRadius: 999, background: "#fff",
                     transition: "left .15s" }} />
    </button>
  );
}

export default function ControlPanel() {
  const { user } = useApp();
  const toast = useToast();
  const { data, error, reload } = usePoll("/api/hub", 5000);
  const { data: pipes, reload: reloadPipes } = usePoll("/api/hub/pipelines", 5000);
  const [busy, setBusy] = useState({});      // slug -> action in flight
  const [flipping, setFlipping] = useState({});   // slug -> {want, since}
  // The row being renamed; doubles as the dialog's open flag so the field can
  // seed from that row's current name.
  const [renameFor, setRenameFor] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const pipelines = pipes?.pipelines;

  // Release a row's spinner as soon as the ordinary poll agrees with what was
  // asked for. There is no second polling loop any more: one source of truth,
  // one cadence, so the switch can never show a state the server hasn't
  // confirmed.
  useEffect(() => {
    if (!pipelines) return;
    setFlipping((f) => {
      const keys = Object.keys(f);
      if (!keys.length) return f;
      const next = {};
      let changed = false;
      for (const slug of keys) {
        const row = pipelines.find((r) => r.slug === slug);
        const settled = row && (row.state === "running") === f[slug].want;
        const expired = Date.now() - f[slug].since > FLIP_TIMEOUT_MS;
        if (settled || expired) changed = true;
        else next[slug] = f[slug];
      }
      return changed ? next : f;
    });
  }, [pipelines]);

  if (error) return <ErrorNote error={error} />;
  if (!data || !pipes) return <SkelTable rows={5} cols={6} />;
  const cards = data.cards || [];
  const bySlug = Object.fromEntries((pipelines || []).map((p) => [p.slug, p]));

  function refresh() { reload().catch(() => {}); reloadPipes().catch(() => {}); }

  async function power(card, action) {
    setBusy((b) => ({ ...b, [card.slug]: action }));
    try {
      const d = await api.post(`/api/hub/systems/${encodeURIComponent(card.slug)}/power`, { action });
      toast.success(`${card.name}: ${action}`, d.note || undefined);
    } catch (e) {
      toast.error(`Could not ${action} ${card.name}`, e.message);
    } finally {
      setBusy((b) => { const n = { ...b }; delete n[card.slug]; return n; });
      refresh();
    }
  }

  // Rename touches the display name only. The slug is the system's identity -
  // config keys, stat keys, saved layouts and workflow files are all filed
  // under it - so it is shown but never offered for editing. A blank name is
  // meaningful: the server restores the name the package declares.
  async function renameSystem(name) {
    const card = renameFor;
    if (!card) return;
    setRenaming(true);
    try {
      const d = await api.patch(`/api/hub/systems/${encodeURIComponent(card.slug)}`, { name });
      toast.success(`Renamed to ${d.name}`);
      setRenameFor(null);
      reload().catch(() => {});
    } catch (e) {
      toast.error(`Could not rename ${card.name}`, e.message);
    } finally {
      setRenaming(false);
    }
  }

  // Detection run switch. Start takes several seconds (camera + model); the row
  // stays marked in-flight until the pipeline poll reflects the new state.
  async function setDetection(slug, on) {
    setFlipping((f) => ({ ...f, [slug]: { want: on, since: Date.now() } }));
    try {
      const d = await api.post(`/api/hub/systems/${encodeURIComponent(slug)}/detection`, { on });
      toast.success(`${slug}: detection ${on ? "starting" : "stopping"}`,
                    d?.result?.message || undefined);
      reloadPipes().catch(() => {});
    } catch (e) {
      toast.error(`Could not ${on ? "start" : "stop"} detection`, e.message);
      setFlipping((f) => { const n = { ...f }; delete n[slug]; return n; });
    }
  }

  async function setCapture(slug, fields) {
    try {
      await api.post(`/api/hub/systems/${encodeURIComponent(slug)}/capture`, fields);
      const what = "snap" in fields ? `snapshot ${fields.snap ? "on" : "off"}`
                                    : `record ${fields.record ? "on" : "off"}`;
      toast.success(`${slug}: ${what}`, "The app applies it within a few seconds.");
    } catch (e) { toast.error("Could not set capture", e.message); }
    finally { reloadPipes().catch(() => {}); }
  }

  async function setAutostart(slug, on) {
    try {
      const d = await api.post(`/api/hub/systems/${encodeURIComponent(slug)}/autostart`, { on });
      // Turning it on starts the system right away; the API reports what it did.
      toast.success(`${slug}: autostart ${on ? "on" : "off"}`,
                    on ? (d?.started?.message || "Starting now; also starts with the hub on power-on.")
                       : undefined);
    } catch (e) { toast.error("Could not set autostart", e.message); }
    finally { reloadPipes().catch(() => {}); }
  }

  return (
    <div className="rounded-xl border bg-card" style={{ overflowX: "auto" }}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8" />
            <TableHead>System</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Camera / Source</TableHead>
            <TableHead>Model</TableHead>
            {/* No FPS column. It is the one number here that says nothing an
                operator acts on -- Status already reports whether a system is
                detecting, and a frame rate is a diagnostic that belongs on
                the system's own page beside the picture it describes. On a
                page of stopped systems it was a column of dashes. */}
            {/* Operating a system is not administering the platform, and the
                rows an operator can see here are already only the systems they
                were granted (the server scopes /api/hub). So the run switches
                are theirs: an operator who cannot restart their own line has
                to go and find whoever holds the admin password. */}
            <TableHead>Detection</TableHead>
            <TableHead>Snap</TableHead>
            <TableHead>Record</TableHead>
            <TableHead>Autostart</TableHead>
            {/* Icon-only buttons carry their own tooltips; a header word here
                adds nothing but width. */}
            <TableHead className="text-right" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {cards.length ? cards.map((c) => {
            const p = bySlug[c.slug] || {};
            const state = STATE[p.state] ? p.state : "offline";
            const s = STATE[state];
            const detecting = state === "running";
            const alive = state !== "offline";      // the master answers
            const rowBusy = busy[c.slug];
            return (
              <TableRow key={c.slug} className="group">
                <TableCell />
                <TableCell>
                  {/* Rename lives in this cell rather than a column of its own:
                      the admin table is already 13 columns wide. Hidden until
                      the row is hovered (or the button is focused, so keyboard
                      users can still reach it). */}
                  {/* The permanent id (c.slug) is the row's own key and its
                      identity in every API call -- but it is not a second
                      name for the operator to read. Available on hover for
                      whoever needs to match it to a config file. */}
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium"
                          title={`${c.slug} - permanent id, a rename never changes it`}>
                      {c.name}
                    </span>
                    {user?.is_admin && (
                      <button type="button" title="Rename this system"
                              onClick={() => setRenameFor(c)}
                              className="text-muted-foreground opacity-0 transition-opacity hover:text-primary focus-visible:opacity-100 group-hover:opacity-100">
                        <Pencil className="size-3.5" />
                      </button>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <span className="inline-flex items-center gap-2">
                    <span className={"text-sm font-medium " + s.text}>{s.label}</span>
                  </span>
                  {/* Stopped by the box, not the user: no camera of the
                      system is there. Says it comes back by itself, so
                      nobody presses Start at a camera that is unplugged. */}
                  {detecting && p.ai_paused === "camera_missing" && (
                    <div className="text-xs text-muted-foreground">
                      detection stopped: camera not found (starts again when it is plugged back in)
                    </div>
                  )}
                  {/* A camera failure reads as its plain headline, the fix
                      and the raw text on hover; anything else (a model that
                      would not load) is shown as the system said it, with
                      any camera password masked (x.said). */}
                  {p.error && (() => {
                    const x = explainCamError(p.error);
                    return (
                      <div className="text-xs min-w-0 max-w-[180px] truncate"
                        style={{ color: "var(--warn-text)" }}
                        title={x?.known ? `${x.title} ${x.fix} Details: ${x.detail}`
                                        : x?.said}>
                        {x?.known ? x.title : x?.said}
                      </div>
                    );
                  })()}
                </TableCell>
                {/* The name the operator picked, not the machine spec: a USB
                    camera's source is the string "0". `||` not `??` - an empty
                    source is a blank cell, which reads as a bug. */}
                <TableCell className="text-xs" title={p.camera || ""}>
                  {p.camera_label || p.camera || "-"}
                </TableCell>
                <TableCell className="font-mono text-xs">{p.model || "-"}</TableCell>
                <TableCell>
                    <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      {/* Shows what the SERVER reports, never a guess: an
                          optimistic flip snapped back whenever a poll landed
                          before the action finished. The spinner carries the
                          "working on it" signal instead. */}
                      {/* An OFFLINE system has no master listening, so the
                          start never reaches anything: the switch flipped,
                          the request failed, and the row went back. Disabled
                          with the reason on it instead -- start the system
                          first, then its detection. */}
                      <Toggle on={detecting}
                              disabled={!alive || !!rowBusy || !!flipping[c.slug]}
                              title={!alive
                                ? "This system is offline - start it before its detection"
                                : detecting ? "Stop the detection app" : "Start the detection app"}
                              onChange={(on) => setDetection(c.slug, on)} />
                      {flipping[c.slug] && <Loader2 className="animate-spin" size={14} />}
                    </div>
                </TableCell>
                <TableCell>
                    <Toggle on={!!p.capture?.snap} disabled={!!rowBusy}
                            title="Save images for this system - the evidence frame on every event, and the workflow's Snapshot node. Off means no pictures are written at all."
                            onChange={(on) => setCapture(c.slug, { snap: on })} />
                </TableCell>
                <TableCell>
                    <Toggle on={!!p.capture?.record} disabled={!!rowBusy}
                            title="Allow the workflow's Record node to save short clips (max 5s) on alerts"
                            onChange={(on) => setCapture(c.slug, { record: on })} />
                </TableCell>
                <TableCell>
                    <Toggle on={!!p.autostart} disabled={!!rowBusy}
                            title="Start this system (master + detection) when the hub boots"
                            onChange={(on) => setAutostart(c.slug, on)} />
                </TableCell>
                <TableCell className="text-right">
                    <div className="inline-flex gap-1.5">
                      <Button size="sm" variant="outline" disabled={!!rowBusy || alive || !c.url}
                              title="Start the master"
                              onClick={() => power(c, "start")}>
                        <Play className="size-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" disabled={!!rowBusy || !alive}
                              title="Stop, then start it again"
                              onClick={() => power(c, "restart")}>
                        <RotateCw className="size-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" disabled={!!rowBusy || !alive}
                              title="Stop the master"
                              onClick={() => power(c, "stop")}>
                        <Square className="size-3.5" />
                      </Button>
                    </div>
                </TableCell>
              </TableRow>
            );
          }) : (
            <TableRow>
              {/* One less since Arch went: a colSpan that outruns the header
                  makes the empty row push the table wider than its own
                  columns. Every column is shown to everyone now. */}
              <TableCell colSpan={11}
                         className="py-10 text-center text-sm text-muted-foreground">
                No systems yet - create one from Systems → New System.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <FormDialog
        open={!!renameFor}
        onOpenChange={(o) => { if (!o && !renaming) setRenameFor(null); }}
        title="Rename system"
        description={renameFor
          ? `Only the display name changes. The id ${renameFor.slug} stays as it is, so links and saved settings keep working.`
          : ""}
        fields={[{
          name: "name",
          label: "Name",
          defaultValue: renameFor?.name || "",
          placeholder: renameFor?.name || "",
          hint: "Up to 60 characters. Leave blank to restore the name the system package declares.",
        }]}
        submitLabel="Save"
        busy={renaming}
        // FormDialog drops blank values, and blank is meaningful here.
        onSubmit={(v) => renameSystem(v.name || "")}
      />
    </div>
  );
}
