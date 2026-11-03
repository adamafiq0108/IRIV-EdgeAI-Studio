// RetentionPanel - the platform data-retention policy, hub Administration.
// ONE policy for the whole box, admin-only (the API enforces it; the shell
// hides the nav item too). Events stay live on each system for ~a week, then
// move to per-day archives; this panel decides how long archives, snapshots,
// clips and the analytics database are kept. The maximums (60 days for
// device data on the built-in storage, up to 12 months with the data on the
// NVMe -- retention.storage_tier; 12 months for the database) are shown next
// to every field, refused here, and refused again by the server.
//
// Laid out as a TABLE rather than a list of labelled inputs: every row answers
// the same five questions, and a reader comparing "how long do I keep clips
// against snapshots" was previously reading two paragraphs instead of two
// cells. The columns are the questions -- how long, what is the most I may
// ask for, since when has this applied, and what date does it delete before.
//
// The last of those is the one that matters and the one a duration cannot
// answer. "45 days" is a setting; "the first clips go on 27 Sep" is something
// an operator can check against what they still need. It counts from the date
// the policy came into force -- which is editable, because the data usually
// predates the page and a rule that started today has nothing old enough to
// delete under it yet (core/services/retention.py first_deletion_at). Once
// that date passes the answer becomes the next sweep instead: deletion is by
// age from then on, so "next" is the next pass, not a start date plus a period
// that is already behind us.
//
// Both dates come from the SAVED policy, never the edited one: unsaved numbers
// delete nothing, and a date that moved as you typed would be a promise about
// a rule that is not in force.
//
// Deliberately NO "reset all data" here or anywhere: deletion is per system,
// explicit and confirmed. Auth, credentials, the audit log, configuration,
// templates and workflows are never retention-managed at all.
import { useCallback, useContext, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { AppContext } from "../state/app.jsx";
import { OPEN_EVENT as STORAGE_SETUP_EVENT } from "./NewStoragePrompt.jsx";
import { useToast } from "./ui/Toast.jsx";
import { Button } from "./ui/button.jsx";
import { HoldToDelete } from "./ui/HoldToDelete.jsx";
import { ConfirmDialog } from "./ui/dialogs.jsx";
import { Skeleton } from "./ui/skeleton.jsx";
import { InfoHint } from "./ui/InfoHint.jsx";
import SettingsPage from "./ui/SettingsPage.jsx";
import DatePicker from "./ui/DatePicker.jsx";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "./ui/table.jsx";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "./ui/dropdown-menu.jsx";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader,
  DialogTitle,
} from "./ui/dialog.jsx";
import {
  Save, ShieldCheck, ListX, Info, Trash2, Archive, Camera, Film,
  LineChart, Database, AlertTriangle, CalendarClock, RefreshCw,
  ChevronDown, ScrollText, ListOrdered, RotateCcw, HardDrive,
} from "lucide-react";

/** What "Choose Data to Delete" offers, one row per thing a system
 *  accumulates. The order is the order an operator thinks in: the recordings
 *  first, then the numbers, then the two Diagnostics pages every system
 *  carries (Events Logs, System Logs). `scope` is the API's word
 *  (retention.PURGE_SCOPES); `what` is the sentence the hold dialog shows,
 *  and it names what is KEPT, because that is the half of the answer a narrow
 *  deletion exists for. Each sentence is retention.purge_system's own list
 *  for that scope -- "Database" is the counters scope, and it restarts the
 *  system, so it says so. */
const SELECTED = [
  { scope: "events", label: "Events", icon: Archive,
    what: "The system's recorded events and their daily archives. Footage, the dashboard's numbers, the Event Table and the logs are kept." },
  { scope: "footage", label: "Footage", icon: Film,
    what: "Every snapshot and video clip. Events, the dashboard's numbers and the logs are kept." },
  { scope: "counters", label: "Database", icon: RotateCcw,
    what: "The dashboard's counts, this system's Event Table rows, its data tables and the recorded events they are built from. The system restarts to finish. Snapshots and video clips are kept." },
  { scope: "eventlog", label: "Events log", icon: ListOrdered,
    what: "What the system's Events Logs page shows: its Event Table rows and the recorded events they come from. Footage and the dashboard's counts are kept, and nothing restarts." },
  { scope: "logs", label: "Logs", icon: ScrollText,
    what: "What the system's System Log page shows: the detection app's log, its fault log, the AI accelerator log and the system service log. No recorded data is touched, and the Activity Log is kept." },
];

/** The widest deletion, on its own button beside the menu. Same dialog and
 *  the same three-second hold as a pick; the sentence names what goes (scope
 *  "all": events, archives, snapshots, clips) and, as plainly, what does NOT
 *  -- the database rows and the logs are separate picks, and a button called
 *  "Delete All System Data" must not let anyone assume otherwise. */
const ALL = {
  scope: "all", label: "All Recorded Data", icon: ListX,
  what: "Events, event archives, snapshots and video clips. The dashboard's database (counts and Event Table rows) and the system's logs are kept; use Choose Data to Delete for those. System configuration, zones, dashboards, workflows and user accounts are not deleted, and detection keeps running.",
};

/** The hold every deletion here asks for, in seconds, as the warning says it:
 *  HoldToDelete's own default (holdMs 3000), which this page never overrides. */
const HOLD_SECONDS = 3;

/** Midnight LOCAL on the picked day. The stored start is an instant, and
 *  taking the day as picked keeps "in effect since the 3rd" meaning the whole
 *  of the 3rd rather than the moment the button was pressed. */
function startOfLocalDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

/** A cutoff epoch as an operator reads it: "3 Sep 2026, 10:00". */
function onDate(epoch) {
  if (!epoch) return "-";
  return new Date(epoch * 1000).toLocaleString(undefined,
    { day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit" });
}

/** "in 4 h" / "due now" for the next scheduled pass. */
function inWords(epoch) {
  if (!epoch) return "on the next cycle";
  const mins = Math.round((epoch * 1000 - Date.now()) / 60000);
  if (mins <= 0) return "due now";
  if (mins < 60) return `in ${mins} min`;
  const hrs = Math.round(mins / 60);
  return hrs < 48 ? `in ${hrs} h` : `in ${Math.round(hrs / 24)} days`;
}

const FIELDS = [
  { key: "events_days", label: "Event Archives", unit: "days", icon: Archive,
    tint: "text-violet-500",
    what: "Recorded events and their associated metadata.",
    hint: "Events stay live on each system for about 7 days, then move to daily archives. Archives older than this are deleted." },
  { key: "snapshots_days", label: "Snapshots", unit: "days", icon: Camera,
    tint: "text-emerald-500",
    what: "Evidence images captured by zone actions and workflows.",
    hint: "Evidence images taken by zone actions and workflows." },
  { key: "clips_days", label: "Video Clips", unit: "days", icon: Film,
    tint: "text-rose-500",
    what: "Recorded video clips saved as MP4 files.",
    hint: "Recorded MP4 clips." },
  { key: "history_months", label: "Analytics History", unit: "months",
    icon: LineChart, tint: "text-sky-500",
    what: "Historical data used to generate charts and analytics.",
    hint: "The history behind charts. Older files are compressed, then deleted past this age." },
  { key: "events_months", label: "Event Database", unit: "months",
    icon: Database, tint: "text-amber-500",
    what: "Searchable records of events stored by the system.",
    hint: "The searchable event records behind the Event Table and Analytics." },
];

/** A period as the page says it: "60 days", and a year of days as "12 months
 *  (365 days)" -- the field stays in days (one unit per row, as every other
 *  row), the words say what it amounts to. */
function periodWords(unit, n) {
  if (unit === "days" && n >= 365) return `12 months (${n} days)`;
  return `${n} ${unit}`;
}

/** The most a row may be, in a sentence: "up to 60 days". */
function maxWords(unit, max) {
  return `up to ${periodWords(unit, max)}`;
}

export default function RetentionPanel() {
  const toast = useToast();
  // "Configure Storage" is master's (the popup and its routes are). The
  // context, not useApp(): the panel is also mounted bare, by its test.
  const user = useContext(AppContext)?.user;
  const [policy, setPolicy] = useState(null);
  const [limits, setLimits] = useState({});
  // Where the data lives decides the device-data cap: 60 days on the built-in
  // storage, up to 365 with the data on the NVMe ({limit_days, reason}).
  const [storage, setStorage] = useState(null);
  // Per system: the cap that applies to ITS data, why, and the periods that
  // actually apply ({slug, limit_days, reason, effective}).
  const [sysTiers, setSysTiers] = useState([]);
  // The explanation and the Configure Storage button are for data that is NOT
  // all on the NVMe. `extended` is true only when every system's data is, so
  // once it is there is nothing to say; where systems differ, the one still on
  // the built-in storage keeps its explanation.
  const showStorageHelp = !!storage && (!storage.extended || !!storage.differs);
  // The periods as SAVED, where they differ from what applies: a 365 saved
  // with the data on the NVMe is kept while the drive is not mounted, and
  // applies again when it is back (retention.saved_policy).
  const [saved, setSaved] = useState({});
  // The NVMe-not-mounted alarm (storage_watch): {active, text} | null.
  const [alarm, setAlarm] = useState(null);
  // "This box no longer uses the NVMe drive": the confirm is open / posting.
  const [forgetting, setForgetting] = useState(false);
  const [forgetBusy, setForgetBusy] = useState(false);
  const [edit, setEdit] = useState({});
  const [systems, setSystems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [schedule, setSchedule] = useState(null);
  // One narrow deletion picked from the Choose Data to Delete menu and waiting
  // for its three-second hold: {slug, label, scope} | null. A menu item
  // cannot be held, so the hold moved into a small dialog - the item picks
  // WHAT, the hold still proves the operator meant it.
  const [pick, setPick] = useState(null);
  // slug -> {state, step} for a reset in flight, so the row can say which
  // step it is on instead of leaving the operator watching a still page.
  const [resets, setResets] = useState({});
  // The date the policy counts FROM, while it is being edited. null = "as
  // stored"; the field is only sent when it was actually touched. The server
  // stamps today only on the FIRST save (retention.set_policy, 2026-10-11);
  // after that an untouched field keeps the stored date -- which is what the
  // help text and the save confirmation both say.
  const [editStart, setEditStart] = useState(null);
  // The Save confirmation is open. Saving runs a cleanup on the spot
  // (POST /api/hub/retention sweeps), so it is asked first.
  const [confirmSave, setConfirmSave] = useState(false);

  const load = useCallback(() => {
    api.get("/api/hub/retention")
      .then((d) => {
        setPolicy(d.policy); setLimits(d.limits || {}); setEdit(d.policy);
        setSchedule(d.schedule || null); setStorage(d.storage || null);
        setSysTiers(d.systems || []);
        setSaved(d.saved || {}); setAlarm(d.alarm || null);
        // A saved period above what this box allows right now is KEPT as
        // saved and applied at the cap; the server says so only the once
        // per spell (retention.over_cap_notice) -- so it is said here, not
        // left in a footnote.
        (d.notice || []).forEach((n) => toast.info("Kept for 60 days for now", n));
      })
      .catch((e) => toast.error("Could not load the retention policy", e.message));
    api.get("/api/hub").then((d) => setSystems(d.cards || [])).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(load, [load]);

  async function save() {
    setBusy(true);
    try {
      // Only what the admin CHANGED. Sending the whole form wrote back every
      // value as the page showed it -- and where systems differ, a 365
      // chosen for the one on the NVMe reads as 60 on the others' rows.
      const diff = Object.fromEntries(Object.entries(edit)
        .filter(([k, v]) => String(v) !== String(policy?.[k])));
      const d = await api.post("/api/hub/retention",
                               editStart == null ? diff
                                                 : { ...diff, starts_at: editStart });
      setPolicy(d.policy); setEdit(d.policy); setLimits(d.limits || {});
      setSchedule(d.schedule || null); setStorage(d.storage || null);
      setSysTiers(d.systems || []);
      setSaved(d.saved || {}); setAlarm(d.alarm || null);
      setEditStart(null);
      (d.notice || []).forEach((n) => toast.info("Kept for 60 days for now", n));
      const swept = d.swept?.deleted || 0;
      toast.success("Retention policy saved",
        swept ? `${swept} expired file(s) cleaned up right away.`
              : "Applied - the background sweep enforces it from now on.");
    } catch (e) { toast.error("Could not save the policy", e.message); }
    finally { setBusy(false); setConfirmSave(false); }
  }

  // The admin's way out of the alarm (storage_watch.forget): the box drops
  // its memory of the drive and re-checks. When the banner stays, the
  // server says what still expects the drive, and that is what is shown.
  async function forgetNvme() {
    setForgetBusy(true);
    try {
      const d = await api.post("/api/hub/retention/nvme/forget", {});
      setAlarm(d.alarm || null);
      setForgetting(false);
      const still = Object.entries(d.still_expected || {});
      if (still.length) {
        toast.info("The box still expects the drive",
          still.map(([m, why]) => `${m}: ${why}.`).join(" "));
      } else {
        toast.success("The NVMe drive is no longer expected",
          "Data is kept on the built-in storage for up to 60 days.");
        load();
      }
    } catch (e) { toast.error("Could not change that", e.message); }
    finally { setForgetBusy(false); }
  }

  // Follow a background reset to its end: the row shows which step it is on,
  // and the operator gets a second toast when it finishes. Polling rather
  // than holding the request open, so leaving the page costs nothing and the
  // reset still completes.
  function watch(slug, label) {
    let tries = 0;
    const tick = async () => {
      try {
        const st = await api.get(
          `/api/hub/systems/${encodeURIComponent(slug)}/data/reset-status`);
        setResets((cur) => ({ ...cur, [slug]: st }));
        if (st.state === "running" && tries++ < 120) {
          setTimeout(tick, 1000);
          return;
        }
        if (st.state === "done") {
          const r = st.removed || {};
          toast.success(`${label}: database reset`,
                        `Tallies and the event log cleared, ${r.db_rows || 0} `
                        + "stored row(s) removed. Snapshots and clips kept.");
          load();                       // the cards' figures moved
        } else if (st.state === "failed") {
          toast.error(`${label}: the reset did not finish`, st.error || "");
        }
      } catch {
        // The master (and briefly the hub's view of it) is restarting; keep
        // asking rather than declaring failure on a request that raced it.
        if (tries++ < 120) { setTimeout(tick, 1000); return; }
      }
      setResets((cur) => {
        const next = { ...cur };
        delete next[slug];
        return next;
      });
    };
    setResets((cur) => ({ ...cur, [slug]: { state: "running", step: "starting" } }));
    setTimeout(tick, 600);
  }

  // A reset started elsewhere (another tab, a colleague) is still this
  // system's state, so the panel picks it up on load rather than looking idle
  // while the system underneath it restarts.
  useEffect(() => {
    if (!systems.length) return;
    systems.forEach((s) => {
      api.get(`/api/hub/systems/${encodeURIComponent(s.slug)}/data/reset-status`)
        .then((st) => {
          if (st.state === "running") watch(s.slug, s.name || s.slug);
        })
        .catch(() => {});
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systems.length]);

  // No typed confirmation anywhere: the three-second hold IS the
  // confirmation, and it is a better one -- a dialog's second click lands
  // where the first did and gets muscle-memoried through, while a hold cannot
  // be. The widest scope (Delete All System Data) holds the same three
  // seconds; its
  // dialog names the system so the operator can check the target first.
  async function purge(slug, name, scope) {
    const label = name || slug;
    setBusy(true);
    try {
      const d = await api.post(
        `/api/hub/systems/${encodeURIComponent(slug)}/data/delete`, { scope });
      if (scope === "counters") {
        // The work runs in the background, so SAY SO IMMEDIATELY: it stops
        // detection, clears three stores and restarts the system, and a
        // button that sits silent for that long reads as broken.
        toast.info(`${label}: resetting the database…`,
                   "Clearing the tallies and stored events, then restarting "
                   + "the system. You can leave this page.");
        watch(slug, label);
        return;
      }
      const r = d.removed || {};
      const parts = [];
      if (scope === "logs") {
        parts.push(`${r.log_files || 0} log file(s)`,
                   r.master_ring ? "the master's log ring cleared"
                                 : "the master's log ring not reached");
      } else {
        if (scope === "eventlog")
          parts.push(`${r.db_rows || 0} stored row(s)`);
        if (scope !== "footage")
          parts.push(`${r.events || 0} event file(s)`, `${r.archive || 0} archive(s)`);
        if (scope !== "events" && scope !== "eventlog")
          parts.push(`${r.snapshots || 0} snapshot(s)`, `${r.clips || 0} clip(s)`);
      }
      toast.info(`${label}: ${scope === "logs" ? "logs" : "data"} deleted`,
                 `${parts.join(", ")}.`);
    } catch (e) { toast.error("Could not delete the system's data", e.message); }
    finally { setBusy(false); setPick(null); }
  }

  // A skeleton rather than the word "Loading": the shape of what is coming is
  // already known, so the panel holds its layout instead of collapsing and
  // then shoving everything down when the answer lands.
  if (!policy) {
    return (
      <div className="space-y-4">
        {[0, 1].map((i) => (
          <div key={i} className="rounded-xl border bg-card p-5">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="mt-2 h-3 w-full max-w-md" />
            <div className="mt-4 space-y-3">
              {[0, 1, 2].map((j) => (
                <div key={j} className="flex items-center justify-between gap-4">
                  <Skeleton className="h-3 w-56" />
                  <Skeleton className="h-7 w-20 shrink-0" />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  const startsAt = schedule?.starts_at || 0;
  const started = !!schedule?.starts_at_set;
  // The day the picker shows: the edit in progress, else what is stored, else
  // today for a box that has never had a policy set.
  const startDate = new Date((editStart ?? (startsAt || Date.now() / 1000)) * 1000);
  // A row typed past what this box allows holds the Save: the server would
  // refuse it anyway, and saying so beside the field beats a failed save.
  const anyOver = FIELDS.some((f) => limits[f.key]
                                     && Number(edit[f.key]) > limits[f.key]);

  return (
    <SettingsPage
      title="Data Retention"
      icon={Database}
      description="Choose how long recorded data is kept before it is
                   automatically deleted. These settings apply to every system
                   on this device.">
      {/* The drive the box expects is not mounted: said first, in the
          server's words (storage_watch.ALARM_TEXT), because it is why every
          number below is held to 60 days. */}
      {alarm?.active && (
        <div data-storage-alarm="1" role="alert"
             className="flex items-start gap-2 rounded-xl border border-amber-500/50
                        bg-amber-500/10 p-4 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" />
          <span>{alarm.text}</span>
        </div>
      )}
      {/* The way out when the drive is gone for good: the memory that makes
          it "expected" is dropped on a confirmed click (storage_watch.forget). */}
      {alarm?.active && (
        <div className="flex items-center justify-end">
          <Button type="button" variant="outline" size="sm" data-testid="nvme-forget"
                  disabled={forgetBusy} onClick={() => setForgetting(true)}>
            <HardDrive className="size-4" /> This box no longer uses the NVMe drive
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={forgetting}
        onOpenChange={(o) => { if (!o) setForgetting(false); }}
        title="This box no longer uses the NVMe drive?"
        description={"The box stops expecting the drive and the banner goes. Event "
          + "archives, snapshots and clips are kept on the built-in storage for up "
          + "to 60 days. If the drive is only unplugged for a while, mount it "
          + "again instead. If it is listed in the box's mount table or named by "
          + "a setting, the box still expects it and says so."}
        confirmLabel="No longer uses the drive"
        busy={forgetBusy}
        onConfirm={forgetNvme}
      />
      {/* ------------------------------------------------ retention periods */}
      <section className="rounded-xl border bg-card p-5">
        <h2 className="text-base font-semibold">Retention Periods</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Set how long each type of data is stored before it is automatically
          deleted. One policy applies to all systems on this device.
        </p>
        {/* Storage Capacity: the caps as the server enforces them
            (retention.LIMITS / limits) -- 12 months is only true for device
            data on the NVMe, so the sentence says both, never "12 months"
            alone. */}
        <p className="mt-3 text-sm font-medium">Storage Capacity</p>
        <p data-retention-capacity="1" className="mt-0.5 text-sm text-muted-foreground">
          Event archives, snapshots and video clips can be kept for up to 60
          days on the built-in storage, or up to 12 months when the data is on
          an NVMe drive. Analytics history and the event database can be kept
          for up to {limits.history_months || 12} months.
        </p>
        {/* The device-data cap and WHY ("why can I only pick 60?") is a notice
            in the bell now (NotificationCenter, Important), present only while
            some data is not on the NVMe. */}
        {/* The way back to "New storage detected" (NewStoragePrompt): a
            drive declined there, or one plugged in while nobody was looking,
            is set up from here. Lists every drive the box may use, declined
            or not; the popup does the rest. */}
        {user?.is_admin && showStorageHelp && (
          <div className="mt-2">
            <Button type="button" variant="outline" size="sm" data-testid="storage-setup"
                    onClick={() => window.dispatchEvent(new Event(STORAGE_SETUP_EVENT))}>
              <HardDrive className="size-4" /> Configure Storage
            </Button>
          </div>
        )}
        {/* Where systems differ, each one's own cap and the periods that
            apply to its data: a system on the NVMe keeps the year chosen, one
            on the eMMC is held to 60. */}
        {storage?.differs && (
          <ul data-retention-systems="1" className="mt-2 space-y-1 text-sm">
            {sysTiers.map((t) => (
              <li key={t.slug} className="text-muted-foreground">
                <strong className="text-foreground">{t.slug}</strong>: events{" "}
                {t.effective.events_days}, snapshots {t.effective.snapshots_days},
                clips {t.effective.clips_days} days - {t.reason}
              </li>
            ))}
          </ul>
        )}

        {/* One clock for the whole policy, so it is asked for once here
            rather than five times down a column. Editable because the data
            usually predates the page: a line that has been recording for a
            year should not be told its first deletion is a year away, and
            back-dating is also how a shortened policy is made to act now. */}
        <label className="mt-4 flex flex-wrap items-center gap-2 rounded-lg
                          border bg-muted/30 p-3 text-sm">
          <CalendarClock className="size-4 text-muted-foreground" />
          <span className="font-medium">Policy Effective From</span>
          {/* The product's own calendar, with month and year dropdowns:
              back-dating to "March last year" is the ordinary case here, and
              <input type="date"> made that twelve clicks on an arrow - in
              whichever control the browser happened to render. */}
          <DatePicker value={startDate} max={new Date()}
                      onChange={(d) => setEditStart(
                        Math.floor(startOfLocalDay(d).getTime() / 1000))} />
          {/* What the server really does (retention.first_deletion_at and
              set_policy): nothing of a type goes until its period has run
              since this date, and a save without a picked date keeps it (the
              first save ever starts it at today). An unset policy is "since
              forever", rolling by age. */}
          <span data-retention-from-help="1" className="text-xs text-muted-foreground">
            {started
              ? "Retention periods are calculated from this date: no data of "
                + "a type is deleted until its retention period has passed "
                + "since then. Use an earlier date if you want the policy to "
                + "apply to older data. Saving without picking a date keeps "
                + "this date."
              : "No policy has been saved on this device yet, so the default "
                + "periods apply to all data by its age. Saving a policy "
                + "starts it from today unless you pick an earlier date."}
          </span>
        </label>

        <div className="mt-4 overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Data Type</TableHead>
                <TableHead>Retention Period</TableHead>
                {/* One editable date for the whole policy, so it is asked
                    for once in the header rather than five times down the
                    column -- there is one clock, not five. */}
                <TableHead>Policy Effective From</TableHead>
                <TableHead>Next Scheduled Deletion</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {FIELDS.map((f) => {
                const Icon = f.icon;
                const max = limits[f.key] || (f.unit === "months" ? 12 : 60);
                const value = edit[f.key] ?? "";
                // Past the cap is refused, here and by the server: the field
                // says so and the Save waits until it is back within it.
                const over = Number(value) > max;
                // Whether this kind's period has actually run since the policy
                // started. Until it has, nothing of this kind is deleted --
                // see retention.first_deletion_at.
                const due = !!schedule?.due?.[f.key];
                return (
                  <TableRow key={f.key}>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <span className="flex size-8 shrink-0 items-center justify-center
                                         rounded-md border bg-muted/40">
                          <Icon className={"size-4 " + f.tint} />
                        </span>
                        <span className="min-w-0">
                          <span className="block text-sm font-medium">
                            {f.label}
                            <InfoHint label={`About ${f.label}`}>{f.hint}</InfoHint>
                          </span>
                          <span className="block text-xs text-muted-foreground">
                            {f.what}
                          </span>
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <input
                          type="number" min={1} max={max}
                          title={`Up to ${max} ${f.unit}`}
                          value={value}
                          aria-invalid={over || undefined}
                          onChange={(e) => setEdit({ ...edit, [f.key]: e.target.value })}
                          className="w-20 rounded-md border bg-background px-2 py-1
                                     text-sm tabular-nums"
                        />
                        {/* The unit is fixed per row -- these are different
                            stores with different natural scales, not one
                            number in a chosen unit -- so it reads as a label
                            and not as a picker that refuses to open. */}
                        <span className="rounded-md border bg-muted/40 px-2 py-1
                                         text-xs text-muted-foreground">
                          {f.unit}
                        </span>
                        {/* The most this row may be, always in view: on a box
                            without its data on the NVMe "why will it not take
                            90?" is answered before it is asked. */}
                        <span data-retention-max={f.key}
                              className="text-xs text-muted-foreground">
                          Maximum retention period: {periodWords(f.unit, max)}
                        </span>
                      </div>
                      {/* Saved longer than applies now: the saved value is
                          kept and comes back with the NVMe, so it is said
                          rather than lost. */}
                      {Number(saved?.[f.key]) > Number(policy?.[f.key]) && (
                        <span data-retention-saved={f.key}
                              className="mt-1 block text-xs text-muted-foreground">
                          Saved: {periodWords(f.unit, saved[f.key])}.
                          Applies again when the data is back on the NVMe drive.
                        </span>
                      )}
                      {over && (
                        <span data-retention-over={f.key}
                              className="mt-1 block text-xs text-destructive">
                          More than this box allows: {maxWords(f.unit, max)}.
                        </span>
                      )}
                      {/* Chosen vs applied: a device-data period above what
                          the drive allows is kept as chosen and applied at
                          the cap -- said, not silently rewritten. */}
                      {f.key.endsWith("_days") && storage
                        && Number(policy?.[f.key]) > storage.min_days && (
                        <span data-retention-applies="1"
                              className="mt-1 block text-xs text-muted-foreground">
                          {policy[f.key]} chosen;{" "}
                          {storage.differs
                            ? `${storage.min_days}-${storage.limit_days} apply (by system, below)`
                            : `${storage.limit_days} apply now`}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {started ? onDate(startsAt)
                               : <span title="No policy has been saved on this device: the default periods apply to all data by its age">
                                   since first run
                                 </span>}
                    </TableCell>
                    {/* Red because it is the consequence, not because it is
                        wrong: this is when data starts being removed. Shown
                        from the SAVED policy - an edited number deletes
                        nothing until it is saved. Once the date has passed,
                        deletion is by AGE, so it says so rather than leaving a
                        date in the past reading like a one-off event. */}
                    {/* Once the rule is in force the next deletion is the
                        next SWEEP, not the start date plus a period -- that
                        date is behind us and would read like something still
                        to come. Before it, the sweep finds nothing old enough,
                        so the honest answer is the day it first will. */}
                    <TableCell className="text-sm tabular-nums text-rose-600 dark:text-rose-400">
                      <span title={due
                        ? `Removes anything recorded before `
                          + `${onDate(schedule?.cutoffs?.[f.key])}.`
                        : `Nothing is old enough yet - the ${policy[f.key]} `
                          + `${f.unit} have not run since the policy started.`}>
                        {due
                          ? (schedule?.next_at ? onDate(schedule.next_at)
                                               : "at the next pass")
                          : onDate(schedule?.first_deletion?.[f.key])}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>

        <div className="mt-4 flex items-start gap-3 rounded-lg border
                        border-sky-500/30 bg-sky-500/5 p-3">
          <Info className="mt-0.5 size-4 shrink-0 text-sky-500" />
          {/* Only the values sweep_status really returns: next_at exists
              once a pass has run in this hub process (the first one ~5 min
              after start, then every interval_s), last_at/last_deleted the
              same. Nothing is shown in place of a value that is not there. */}
          <div className="min-w-0 text-xs" data-retention-schedule="1">
            <p className="font-medium">Automatic Data Deletion</p>
            <p className="mt-0.5 text-muted-foreground">
              Data is automatically deleted when its retention period expires.
              A cleanup runs about every{" "}
              {Math.round((schedule?.interval_s || 21600) / 3600)} hours, and
              once more straight after you save the policy.
            </p>
            <ul className="mt-1 space-y-0.5 text-muted-foreground">
              {schedule?.next_at && (
                <li>Next cleanup: {inWords(schedule.next_at)}</li>
              )}
              {schedule?.last_at ? (
                <>
                  <li>Last cleanup: {onDate(schedule.last_at)}</li>
                  <li>Files deleted during last cleanup: {schedule.last_deleted || 0}</li>
                </>
              ) : (
                <li>No cleanup has run since the device last started.</li>
              )}
            </ul>
          </div>
        </div>

        <div className="mt-4 flex justify-end">
          <Button size="sm" disabled={busy || anyOver}
                  onClick={() => setConfirmSave(true)}>
            <Save className="mr-1 size-4" /> Save Retention Policy
          </Button>
        </div>
        {/* Saving sweeps on the spot, so the dialog says what that sweep can
            do with THIS save: the first save with no date picked starts the
            policy today and nothing is old enough yet; a later save with no
            date keeps the stored date; either way, data already past its
            period since the effective date goes now. */}
        <ConfirmDialog
          open={confirmSave}
          onOpenChange={(o) => { if (!o && !busy) setConfirmSave(false); }}
          title="Save retention policy?"
          description={"The updated policy may make existing data eligible for "
            + "deletion. Review the retention periods before saving. "
            + (editStart == null && started
              ? "Saving keeps Policy Effective From at "
                + new Date(startsAt * 1000).toLocaleDateString(undefined,
                  { day: "numeric", month: "short", year: "numeric" })
                + " and runs a cleanup straight away, which deletes any data "
                + "that is already past its retention period since that date."
              : editStart == null
              ? "Saving sets Policy Effective From to today and runs a cleanup "
                + "straight away; nothing is deleted until a retention period "
                + "has passed since today."
              : "Saving sets Policy Effective From to "
                + new Date(editStart * 1000).toLocaleDateString(undefined,
                  { day: "numeric", month: "short", year: "numeric" })
                + " and "
                + "immediately deletes any data that is already past its "
                + "retention period since that date.")}
          confirmLabel="Save Policy"
          busy={busy}
          onConfirm={save}
        />
      </section>

      {/* ------------------------------------------------------ never removed */}
      {/* Each item checked against the sweep: its allowlist only reaches
          media, archives and history files, its database prune only event
          and data-table rows -- accounts, the audit (Activity Log) table,
          config, templates and workflows are out of its reach. */}
      <section className="rounded-xl border bg-card p-5">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <ShieldCheck className="size-4 text-primary" /> Data Not Affected by Retention
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The following items are not automatically deleted by retention
          policies:
        </p>
        <ul data-retention-unaffected="1"
            className="mt-1 list-disc pl-5 text-sm text-muted-foreground">
          <li>User accounts</li>
          <li>Activity Log (audit log)</li>
          <li>System configuration</li>
          <li>Templates</li>
          <li>Workflows</li>
        </ul>
        <p className="mt-1 text-sm text-muted-foreground">
          These items must be managed separately.
        </p>
      </section>

      {/* --------------------------------------------- delete a system's data */}
      <section className="rounded-xl border bg-card p-5">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Trash2 className="size-4 text-destructive" />
          Delete System Data
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Permanently delete recorded data for an individual system. Choose
          which types of data to delete, or delete all recorded data for that
          system.
          <InfoHint label="About deleting system data">
            Delete All System Data removes the system’s events, event archives,
            snapshots and video clips. Choose Data to Delete removes one type
            at a time: events, footage, the dashboard’s database, what its
            Events Logs page shows, or what its System Log page shows. Each
            deletion is for one system only. System configuration, zones,
            dashboards, workflows and user accounts are never deleted here.
          </InfoHint>
        </p>

        <div className="mt-4 overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>System</TableHead>
                <TableHead>Data Types</TableHead>
                <TableHead>Deletion Options</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {systems.map((s) => (
                <TableRow key={s.slug}>
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <span className="flex size-8 shrink-0 items-center justify-center
                                       rounded-md border bg-muted/40 text-xs font-semibold">
                        {(s.name || s.slug).slice(0, 1).toUpperCase()}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium">
                          {s.name || s.slug}
                        </span>
                        {/* Where it actually runs. Two systems can carry
                            similar names; the address is what tells them
                            apart when the next click deletes data. */}
                        <span className="block truncate font-mono text-xs text-muted-foreground">
                          {s.url ? s.url.replace(/^https?:\/\//, "") : s.slug}
                        </span>
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1.5">
                      {["Events", "Footage", "Database", "Logs"].map((t) => (
                        <span key={t}
                              className="rounded-full border px-2 py-0.5 text-xs
                                         text-muted-foreground">
                          {t}
                        </span>
                      ))}
                    </div>
                  </TableCell>
                  {/* Two buttons, not five: the widest deletion on its own,
                      and one menu for everything narrower. Every pick,
                      Delete All System Data included, gets the same
                      three-second hold in a small dialog that names the
                      system. */}
                  <TableCell>
                    <div className="flex w-[13rem] max-w-full">
                      {resets[s.slug]?.state === "running" ? (
                        // While a reset runs, the menu's place becomes the
                        // progress: naming the step is what tells an operator
                        // it is working rather than stuck, and nothing else
                        // on this row can be picked meanwhile.
                        <span className="inline-flex h-8 flex-1 items-center justify-center
                                         gap-1.5 rounded-md border px-2 text-xs
                                         text-muted-foreground">
                          <span className="size-1.5 animate-pulse rounded-full bg-amber-500" />
                          Resetting: {resets[s.slug].step || "working"}…
                        </span>
                      ) : (
                        <DropdownMenu>
                          <DropdownMenuTrigger
                            render={<Button variant="outline" size="sm" disabled={busy}
                                            className="h-8 flex-1 justify-center
                                                       text-destructive" />}>
                            <Trash2 className="mr-1 size-3.5" /> Choose Data to Delete
                            <ChevronDown className="ml-1 size-3.5 opacity-60" />
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-56">
                            {/* Base UI: a label is a GROUP part and throws
                                (error #31) outside one, which is what took
                                the whole Settings page down on first click. */}
                            <DropdownMenuGroup>
                              <DropdownMenuLabel className="text-xs text-muted-foreground">
                                Delete for {s.name || s.slug}
                              </DropdownMenuLabel>
                            </DropdownMenuGroup>
                            <DropdownMenuSeparator />
                            {SELECTED.map((o) => (
                              <DropdownMenuItem key={o.scope} variant="destructive"
                                                onClick={() => setPick({ slug: s.slug,
                                                                         label: s.name || s.slug,
                                                                         scope: o.scope })}>
                                <o.icon /> {o.label}
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="destructive" size="sm" disabled={busy}
                            className="h-8 justify-center"
                            onClick={() => setPick({ slug: s.slug,
                                                     label: s.name || s.slug,
                                                     scope: "all" })}>
                      <ListX className="mr-1 size-3.5" /> Delete All System Data
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {!systems.length && (
                <TableRow>
                  <TableCell colSpan={4}
                             className="py-8 text-center text-sm text-muted-foreground">
                    No systems yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>

        <div data-retention-warning="1"
             className="mt-4 flex items-start gap-2 rounded-lg border
                        border-amber-500/30 bg-amber-500/5 p-3 text-xs">
          <AlertTriangle className="size-4 shrink-0 text-amber-500" />
          <span>
            <strong className="block">Permanent deletion</strong>
            Deleted data cannot be recovered. Every deletion requires you to
            press and hold the confirmation control for {HOLD_SECONDS} seconds.
            Releasing it before the countdown finishes cancels the operation.
          </span>
        </div>
      </section>

      <TrashPane />

      {/* A narrow deletion, picked from the menu: the hold IS the confirmation,
          exactly as the buttons it replaced were, and the sentence says what
          goes and what stays so the operator can check they picked the right
          line before they press. */}
      {(() => {
        const opt = [...SELECTED, ALL].find((o) => o.scope === pick?.scope);
        const all = pick?.scope === "all";
        return (
          <Dialog open={!!pick} onOpenChange={(o) => { if (!o && !busy) setPick(null); }}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>
                  {all ? "Delete all recorded data for this system?"
                       : "Delete selected data?"}
                </DialogTitle>
                <DialogDescription>
                  {all
                    ? <>This will permanently delete all recorded data
                        for <strong>{pick?.label || ""}</strong>.</>
                    : <>This will permanently delete the selected data
                        for <strong>{pick?.label || ""}</strong>.</>}
                  {" "}This action cannot be undone.
                </DialogDescription>
              </DialogHeader>
              {/* WHAT goes and what stays, from purge_system's own list for
                  the scope, so the operator checks the line they picked. */}
              <div data-retention-pick={opt?.scope || ""}
                   className="rounded-md border bg-muted/30 p-3 text-sm">
                <p className="font-medium">{opt?.label || ""}</p>
                <p className="mt-0.5 text-muted-foreground">{opt?.what || ""}</p>
              </div>
              {!all && (
                <p className="text-xs text-muted-foreground">
                  Review your selection before continuing.
                </p>
              )}
              {/* The hold is the one action, so it is drawn as one: solid,
                  full width, its progress fill visible against it - the
                  soft destructive tint the row buttons wear hides the fill.
                  The footer holds only Cancel, as the confirm dialogs do. */}
              <HoldToDelete disabled={busy || !pick} fillClassName="bg-black/25"
                            className="mt-1 h-10 w-full justify-center bg-destructive text-white
                                       hover:bg-destructive/90 dark:bg-destructive dark:hover:bg-destructive/90"
                            label="Hold to Delete"
                            holdLabel="Keep holding…"
                            onConfirm={() => pick && purge(pick.slug, pick.label, pick.scope)} />
              <p className="text-center text-xs text-muted-foreground">
                Hold for {HOLD_SECONDS} seconds. Releasing early cancels the deletion.
              </p>
              <DialogFooter className="mt-2">
                <Button type="button" variant="outline" disabled={busy}
                        onClick={() => setPick(null)}>
                  Cancel
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        );
      })()}

    </SettingsPage>
  );
}

// ---------------------------------------------------------------------------
// Unused Data (was "Trash") - files whose system no longer exists.
//
// Deleting a system used to leave its layout, workflow, recorded rows and
// data folder behind, and nothing lists a file whose system is gone -- so
// they sat on the disk invisible. This is where they are found and removed.
// It lived behind a "Leftover files" button on the Systems page; it is
// housekeeping about stored data, which is what this tab is, so it is here.
// The customer-facing name is "Unused Data"; the component and the route
// keep their internal names (TrashPane, /api/hub/orphans).
// ---------------------------------------------------------------------------

/** provision.orphan_documents' kinds as a customer reads them. A kind the
 *  server adds later still shows, under its own name. */
const ORPHAN_KINDS = {
  layout: "Dashboard layout",
  workflow: "Workflow",
  package: "System files",
  data: "Recorded data (events, snapshots, video clips)",
};

/** Bytes as an operator reads them: "1.4 GB" decides whether to delete a
 *  folder, "1503238553" does not. */
function fileSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

function TrashPane() {
  const toast = useToast();
  const [orphans, setOrphans] = useState(null);
  const [pending, setPending] = useState(null);   // the row awaiting confirm
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    api.get("/api/hub/orphans")
      .then((d) => setOrphans(d.orphans || []))
      .catch((e) => { setOrphans([]); toast.error("Could not read unused data", e.message); });
  }, [toast]);
  useEffect(() => { load(); }, [load]);

  async function clear(slug) {
    setBusy(slug);
    try {
      const d = await api.del(`/api/hub/orphans/${encodeURIComponent(slug)}`);
      if (d?.error) {
        // Part of it is still on the disk, and the row has to say so rather
        // than vanish: a folder another user wrote (a master once run as
        // root) is the case, and the message carries the command that clears
        // it. The list is re-read so what is left shows its real size.
        toast.warn(`${slug}: not everything could be removed`, d.error);
        load();
      } else {
        setOrphans((prev) => (prev || []).filter((o) => o.slug !== slug));
        toast.success(`${slug} removed`);
      }
    } catch (e) { toast.error("Could not remove it", e.message); }
    finally { setBusy(""); setPending(null); }
  }

  const total = (orphans || []).reduce((n, o) => n + (o.bytes || 0), 0);
  return (
    <section className="rounded-xl border bg-card p-5 mt-4">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <Trash2 className="size-4" /> Unused Data
            {!!orphans?.length && (
              <span className="rounded-full border px-2 py-0.5 text-xs font-medium text-muted-foreground">
                {orphans.length} · {fileSize(total)}
              </span>
            )}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Review and remove files left behind by systems that no longer
            exist. These may include dashboard layouts, workflows, stored
            event records, recorded data folders (events, snapshots and video
            clips) and system files. Removing these files does not affect
            systems that still exist.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={!!busy}
                title="Refresh unused data" aria-label="Refresh unused data">
          <RefreshCw className="size-3.5" />
        </Button>
      </div>

      {orphans === null ? (
        <Skeleton className="mt-4 h-10 w-full" />
      ) : !orphans.length ? (
        <div data-unused-empty="1" className="mt-4 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">No unused data found</p>
          <p className="mt-0.5">
            All stored layouts, workflows and data folders belong to systems
            that currently exist. No cleanup is needed.
          </p>
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>System</TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="text-right">Size</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orphans.map((o) => (
                <TableRow key={o.slug}>
                  <TableCell className="font-mono text-sm">{o.slug}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {o.files.map((f) => ORPHAN_KINDS[f.kind] || f.kind).join(", ")}
                  </TableCell>
                  <TableCell className="text-right text-sm tabular-nums">
                    {o.bytes > 0 ? fileSize(o.bytes) : "-"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="outline" size="sm" disabled={!!busy}
                            className="text-destructive"
                            onClick={() => setPending(o)}>
                      {busy === o.slug ? "…" : "Remove"}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <ConfirmDialog
        open={!!pending}
        onOpenChange={(o) => { if (!o) setPending(null); }}
        title="Remove unused data?"
        description={`This will permanently remove the files left behind by `
          + `"${pending?.slug || ""}": `
          + (pending?.files || []).map((f) => ORPHAN_KINDS[f.kind] || f.kind).join(", ")
          + ", and its stored event records. No existing system uses them. "
          + "Review the listed items before continuing."}
        confirmLabel="Remove Unused Data"
        destructive
        busy={!!busy}
        onConfirm={() => clear(pending.slug)}
      />
    </section>
  );
}
