// Systems grid - the hub's home. One card per registered system; each links out
// to that system's own master (a separate process/URL). A system with no master
// configured still gets a card, disabled, so the gap is visible not silent.
//
// This is the "created systems" surface: a non-technical operator clicks a card
// to run/enter a system. Building new ones lives in the hub sidebar (HubShell).
//
// "Start from a template" (Tok Aba, 2026-10-22; TemplateGallery.jsx): the
// built-in templates as picture cards, here on the home page rather than a
// click away behind New System. ALWAYS FIRST, then "Your systems" (Tok Aba,
// after trying it on .164): the templates are what this page offers, so they
// stay where they are found, also while selecting, searching or at the
// system cap -- there they read as a plain notice with Use disabled rather
// than vanishing, which only left people wondering where they went.
//
// SLOTS (same day): the systems grid always shows as many places as the box
// may hold (provision.MAX_SYSTEMS, 4, sent as max_systems): each system
// takes one, every place left is a dashed "New system" tile (Blank Canvas),
// so the tiles count down 4, 3, 2, 1 and none at the cap. A developer
// account has no cap (max_systems 0): at least 4 places, always one free.
//
// Admins also curate here: Select flips the grid into a pick-cards mode whose
// only action is Remove (DELETE /api/hub/systems/<slug> - stops the local
// master, disables the system, deletes the folder only if it was generated).
// Search filters as you type; it's client-side, the grid is never big enough
// to warrant a query API.
import { useMemo, useRef, useState } from "react";
import { api } from "../api/client.js";
import { usePoll } from "../api/hooks.js";
import { useApp } from "../state/app.jsx";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { Card } from "../components/ui/card.jsx";
import { Button } from "../components/ui/button.jsx";
import { Input } from "../components/ui/input.jsx";
import { ConfirmDialog, FormDialog } from "../components/ui/dialogs.jsx";
import EditSystemDialog from "../components/EditSystemDialog.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { useConfirm } from "../components/ui/ask.jsx";
import { downloadFile } from "../lib/download.js";
import { authHeaders, goWithSession, withSession } from "../lib/session.js";
import {
  Plus, ArrowUpRight, Search, Check, Trash2, ImagePlus, ImageOff,
  Download, Upload, Pencil, FolderPlus, FolderInput, Play, Square, Folders,
  Loader2, BookmarkPlus,
} from "lucide-react";
import { Dialog } from "../components/ui/primitives.jsx";
import { apiUrl } from "../lib/base.js";
import TemplateGallery from "../components/TemplateGallery.jsx";

// status -> how the pill reads. The four values come from core/services/hub.py
// (_status): ok | partial | down | none. Class strings are written out whole
// because Tailwind scans source text - a built-up `bg-${x}-500` gets purged.
const STATUS = {
  // Three states an operator acts on: nothing running (red), the system up
  // but not detecting (blue), both running (green). Blue rather than a dim
  // grey because "up but idle" is a state to notice, not a disabled card.
  running: { dot: "bg-emerald-500", pill: "text-emerald-700 bg-emerald-500/10 dark:text-emerald-400", label: "running" },
  online:  { dot: "bg-sky-500",     pill: "text-sky-700 bg-sky-500/10 dark:text-sky-400",             label: "online" },
  down:    { dot: "bg-red-500",     pill: "text-red-700 bg-red-500/10 dark:text-red-400",             label: "offline" },
  partial: { dot: "bg-amber-500",   pill: "text-amber-700 bg-amber-500/10 dark:text-amber-400",       label: "partial" },
  // Older masters (before /api/health carried ai_running) still send "ok".
  ok:      { dot: "bg-emerald-500", pill: "text-emerald-700 bg-emerald-500/10 dark:text-emerald-400", label: "running" },
  // "none" is the system with no master configured at all - the card is
  // disabled anyway, and its summary says so.
  none:    { dot: "bg-slate-400",   pill: "text-slate-600 bg-slate-500/10 dark:text-slate-300",       label: "not set up" },
  // Its counters are being reset: the master is mid-restart on purpose, so
  // the card says so rather than reporting the outage as a fault.
  resetting: { dot: "bg-amber-500 animate-pulse", pill: "text-amber-700 bg-amber-500/10 dark:text-amber-400", label: "resetting" },
};

export default function Hub({ onCreate }) {
  const { user } = useApp();
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, reload } = usePoll("/api/hub", 10000);
  const [query, setQuery] = useState("");
  const [selecting, setSelecting] = useState(false);
  const [sel, setSel] = useState(() => new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // The card being renamed; also the dialog's open flag, so the field seeds
  // from that card's current name.
  const [renameFor, setRenameFor] = useState(null);
  // The card being saved as a template (also the dialog's open flag), and a
  // counter that tells the template gallery below to re-read its list.
  const [saveTplFor, setSaveTplFor] = useState(null);
  const [tplTick, setTplTick] = useState(0);

  const cards = data?.cards || [];
  // The box caps how many systems may exist. At the limit the create paths are
  // REMOVED rather than left to fail: an operator should not fill in a wizard
  // and meet a refusal at the end of it. Counted server-side, because a card
  // list is scoped to the account while the cap is per box.
  const maxSystems = data?.max_systems || 0;
  const systemCount = data?.system_count ?? cards.length;
  const atLimit = maxSystems > 0 && systemCount >= maxSystems;
  // Slugs whose start/stop request is in flight - their button is disabled so
  // a double-click cannot fire two spawns at the same port.
  const [powerBusy, setPowerBusy] = useState(() => new Set());

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matching = !q ? cards : cards.filter((c) =>
      (c.name || "").toLowerCase().includes(q) || (c.slug || "").toLowerCase().includes(q));
    return matching;
  }, [cards, query]);

  // Hooks must all run before any early return (React #310), so these refs
  // live here even though they are only used deep in the grid below.
  const fileRef = useRef(null);
  const pendingSlug = useRef(null);
  const importRef = useRef(null);
  // The picked export, waiting on the database question. Null when no import
  // is being set up.
  const [pendingImport, setPendingImport] = useState(null);
  const importDbRef = useRef(null);

  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelPage title="Systems" cards={2} rows={4} />;

  function toggle(slug) {
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug); else next.add(slug);
      return next;
    });
  }

  function exitSelect() {
    setSelecting(false);
    setSel(new Set());
  }

  const selectedCards = cards.filter((c) => sel.has(c.slug));

  // At the cap every create path says the same plain sentence: the template
  // cards (Use this template off), the toolbar's New System (off).
  const limitNote = atLimit
    ? `You have reached the limit of ${maxSystems} systems. Remove one to add another.`
    : "";
  // The template cards: admin only (the gallery draws nothing for anyone
  // else), shown in every mode -- see the banner.
  const gallery = user?.is_admin ? (
    <TemplateGallery onUse={(key) => onCreate(key)} limitNote={limitNote}
                     refreshKey={tplTick} />
  ) : null;
  // The empty places in the grid: the box's cap, or for an uncapped
  // (developer) account at least 4 with one always free.
  const slots = maxSystems > 0 ? maxSystems : Math.max(4, systemCount + 1);
  const freeSlots = Math.max(0, slots - systemCount);

  // -- cover images: uploaded here, stored ON the device (the hub's persistent
  // store), so every browser that opens this hub sees the same picture. One
  // hidden input (fileRef, declared above) serves every card; pendingSlug
  // remembers whose it is.
  function pickImage(slug) {
    pendingSlug.current = slug;
    fileRef.current?.click();
  }

  async function onImagePicked(e) {
    const file = e.target.files?.[0];
    e.target.value = "";                       // same file re-pickable later
    const slug = pendingSlug.current;
    if (!file || !slug) return;
    try {
      const fd = new FormData();
      fd.append("file", file);
      // api.client speaks JSON only; multipart goes through fetch directly.
      const res = await fetch(apiUrl(`/api/hub/systems/${encodeURIComponent(slug)}/image`), {
        method: "POST", credentials: "same-origin", body: fd,
        headers: { ...authHeaders() },
      });
      const ct = res.headers.get("content-type") || "";
      const d = ct.includes("application/json") ? await res.json().catch(() => null) : null;
      if (!res.ok) throw new Error((d && d.error) || res.statusText || `HTTP ${res.status}`);
      toast.success("Image saved", "Stored on the device - every browser sees it.");
      reload().catch(() => {});
    } catch (err) {
      toast.error("Could not save image", err.message);
    }
  }

  async function removeImage(slug) {
    try {
      await api.del(`/api/hub/systems/${encodeURIComponent(slug)}/image`);
      toast.success("Image removed");
      reload().catch(() => {});
    } catch (err) {
      toast.error("Could not remove image", err.message);
    }
  }

  // -- edit (was rename). The slug stays the system's identity (config keys,
  // stat keys, stored layouts, workflow files all hang off it), so the dialog
  // never offers it; the name and the PORT are what it edits -- the port
  // moves the master and restarts it (components/EditSystemDialog.jsx).

  // -- "Save as template": a snapshot of the system's BUILD (workflow, zones,
  // dashboard, settings) filed under "My templates" in the gallery. It is a
  // stored document, never run; cameras, credentials, ports and recorded data
  // are not in it (core/services/templates.py save_user_template).
  async function saveAsTemplate(values) {
    const card = saveTplFor;
    if (!card) return;
    setBusy(true);
    try {
      const d = await api.post(
        `/api/hub/systems/${encodeURIComponent(card.slug)}/save-template`,
        { name: values.name, description: values.description || "" });
      toast.success("Template saved", `${d.name} is under My templates.`);
      setSaveTplFor(null);
      setTplTick((n) => n + 1);
    } catch (err) {
      toast.error("Could not save the template", err.message);
    } finally {
      setBusy(false);
    }
  }

  // -- whole-system export/import. An export carries the site (cameras, zones,
  // workflow, dashboards) but never ports or credentials, so the file is safe
  // to hand to another box - that box assigns its own.
  async function exportSystem(slug) {
    try {
      await downloadFile(apiUrl(`/api/hub/systems/${encodeURIComponent(slug)}/export`),
                         `system-${slug}.json`);
    } catch (err) {
      toast.error("Could not export the system", err.message);
    }
  }

  // Picking the JSON does not import it any more: it opens the question the
  // import has always silently answered "no" to. An export carries the DESIGN
  // -- cameras, zones, workflow, dashboards -- and never the recorded data,
  // because the design is small enough to mail and the data can be gigabytes.
  // So a system rebuilt from an export arrived correct and EMPTY, and there
  // was nowhere to say otherwise. Now there is: the SQLite export from that
  // system's Database page, attached here, lands with it.
  function onExportPicked(e) {
    const file = e.target.files?.[0];
    e.target.value = "";                       // same file re-pickable later
    if (!file) return;
    setPendingImport({ file, withDb: false, db: null, name: "", exported: "" });
    // The name the export carries, so the box can say what "keep it" means.
    // Best effort: a file that cannot be read here is still sent as it is and
    // the server judges it.
    file.text().then((txt) => {
      const exported = String(JSON.parse(txt)?.meta?.name || "").trim();
      setPendingImport((p) => (p && p.file === file ? { ...p, exported, name: exported } : p));
    }).catch(() => {});
  }

  async function runImport() {
    const chosen = pendingImport;
    if (!chosen) return;
    const { file } = chosen;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      // Unchanged or blank = keep the exported name (the server's default);
      // anything else names the new system and its folder.
      const typed = (chosen.name || "").trim();
      fd.append("name", typed === chosen.exported ? "" : typed);
      // Only when there is one. An empty part still arrives as an UploadFile
      // with a blank filename, which the route reads as "no database" -- but
      // not sending it at all is the honest shape.
      if (chosen.withDb && chosen.db) fd.append("database", chosen.db);
      const res = await fetch(apiUrl("/api/hub/systems/import"), {
        method: "POST", credentials: "same-origin", body: fd,
        headers: { ...authHeaders() },
      });
      const ct = res.headers.get("content-type") || "";
      const d = ct.includes("application/json") ? await res.json().catch(() => null) : null;
      // The system cap is enforced server-side; its refusal is the message.
      if (!res.ok) throw new Error((d && (d.error || d.detail)) || res.statusText || `HTTP ${res.status}`);
      const db = d.database || null;
      const rows = db ? Object.values(db.written || {}).reduce((a, b) => a + b, 0) : 0;
      toast.success(`${d.name} imported`,
                    rows ? `${rows.toLocaleString()} rows came with it. `
                           + `Starting at ${d.url}.`
                         : `Starting at ${d.url}.`);
      setPendingImport(null);
      // Cameras that exceeded the box's budget, zones that lost their camera,
      // a workflow still needing zones - each is a to-do, not a failure.
      for (const w of d.warnings || []) toast.warn("Finish setting it up", w);
      reload().catch(() => {});
    } catch (err) {
      toast.error("Could not import the system", err.message);
    } finally {
      setBusy(false);
    }
  }

  // -- start / stop a system's master straight from its card. Same route the
  // System Control table uses; operator-gated server-side. A start takes a
  // couple of seconds to boot and answer probes, so the list is re-polled
  // shortly after rather than trusting the immediate snapshot.
  async function powerSystem(card, action) {
    setPowerBusy((prev) => new Set(prev).add(card.slug));
    try {
      await api.post(`/api/hub/systems/${encodeURIComponent(card.slug)}/power`, { action });
      if (action === "stop") toast.success(`${card.name || card.slug} stopped`);
      else toast.success(`${card.name || card.slug} is starting`,
                         "Its pages come up in a few seconds. AI detection "
                         + "stays off until you turn it on in System Control.");
      await reload().catch(() => {});
      // The button stays busy through the second read, not just the request:
      // a master takes a few seconds to answer, and a card that goes back to
      // showing Start in the meantime invites the second press that spawns a
      // second process on the same port.
      await new Promise((r) => setTimeout(r, 3000));
      await reload().catch(() => {});
    } catch (e) {
      toast.error(`Could not ${action} ${card.name || card.slug}`, e.message);
    } finally {
      setPowerBusy((prev) => {
        const next = new Set(prev);
        next.delete(card.slug);
        return next;
      });
    }
  }

  async function removeSelected() {
    setBusy(true);
    const failed = [];
    for (const c of selectedCards) {
      try {
        const d = await api.del(`/api/hub/systems/${encodeURIComponent(c.slug)}`);
        if (!d.files_deleted) {
          toast.info(`${c.name} removed from the hub`,
            "Hand-written package - its files stay on disk; re-enable it in config/systems.toml any time.");
        }
      } catch (e) {
        failed.push(c.name);
        toast.error(`Could not remove ${c.name}`, e.message);
      }
    }
    const removed = selectedCards.length - failed.length;
    if (removed) toast.success(`Removed ${removed} system${removed === 1 ? "" : "s"}`);
    setBusy(false);
    setConfirmOpen(false);
    exitSelect();
    reload().catch(() => {});
  }

  return (
    // `page active` for the entrance every other page has: this one is built
    // out of Tailwind utilities rather than the .page shell, which is why it
    // was the one screen that appeared without one.
    <div className="page active space-y-4">
      {/* One hidden picker serves every card's "Set image" button. */}
      <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp,image/gif"
             className="hidden" onChange={onImagePicked} />
      <input ref={importRef} type="file" accept="application/json,.json"
             className="hidden" onChange={onExportPicked} />
      {gallery}
      {user?.is_admin && <h2 className="pt-2 text-base font-semibold">Your systems</h2>}
      {/* Toolbar: search filters as you type; Select is the admin's curation
          mode - its only action is Remove, so the button reads as that. */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search systems…"
            aria-label="Search systems"
            className="pl-8"
          />
        </div>
        <div className="ml-auto flex items-center gap-2">
          {/* The at-limit notice leads the row. It exists to explain why
              "New System" is not there, and an explanation placed AFTER the
              controls it explains is read once the reader has already given
              up looking for the button. */}
          {user?.is_admin && !selecting && atLimit && (
            <span className="text-xs text-muted-foreground whitespace-nowrap">
              {systemCount} of {maxSystems} systems - remove one to add another
            </span>
          )}
          {user?.is_admin && cards.length > 0 && !selecting && (
            <Button variant="outline" onClick={() => setSelecting(true)}>Select</Button>
          )}
          {/* Creating is the hub's primary action, so it also lives in the
              toolbar - the create CARD is at the far end of a long grid once a
              few systems exist, which is exactly when you want another. */}
          {/* Rebuilding a system from another box is the same act as creating
              one, so it sits beside New System rather than in a menu. */}
          {user?.is_admin && !selecting && (
            <Button variant="outline" disabled={busy}
                    onClick={() => importRef.current?.click()}>
              <Upload className="size-4" /> Import system
            </Button>
          )}
          {/* Creating a system is an ADMIN act, not a per-account one: how
              many systems this box runs is a decision about the box, and two
              operators each adding "just one" is how a four-camera board ends
              up oversubscribed. The API has always refused non-admins here --
              this stops offering them a button that 403s. */}
          {selecting && (
            <>
              <Button variant="outline" onClick={exitSelect} disabled={busy}>Cancel</Button>
              <Button
                variant="destructive"
                disabled={!sel.size || busy}
                onClick={() => setConfirmOpen(true)}
              >
                <Trash2 className="size-4" /> Remove{sel.size ? ` (${sel.size})` : ""}
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-4">
        {shown.map((card) => {
          // A reset in flight outranks whatever the probe just saw: the
          // master is deliberately down for a second, and "offline" would
          // read as a fault someone needs to fix.
          const st = card.resetting ? STATUS.resetting
            : (STATUS[card.status] || STATUS.none);
          const dead = !card.url;
          const isSel = sel.has(card.slug);
          const inner = (
            <Card
              className={
                "group relative h-full gap-0 overflow-hidden p-0 transition-all duration-200 " +
                (selecting
                  ? (isSel ? "border-destructive/60 ring-2 ring-destructive/60" : "hover:border-primary/40")
                  : dead
                    ? "opacity-70"
                    : "hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-[0_8px_30px_-12px_var(--primary)]")
              }
            >
              {/* Cover: the uploaded image when one is stored on the device,
                  otherwise the brand tint + icon. */}
              <div className="relative flex h-28 items-center justify-center overflow-hidden bg-gradient-to-br from-primary/20 via-primary/10 to-transparent">
                {card.image ? (
                  <img src={card.image} alt=""
                       className="absolute inset-0 h-full w-full object-cover" />
                ) : (
                  <span className="text-4xl font-semibold text-primary/80" aria-hidden>
                    {card.icon || "◆"}
                  </span>
                )}
                {user?.is_admin && !selecting && (
                  <span className="absolute bottom-2 right-2 flex gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100">
                    <button
                      type="button"
                      title={card.image ? "Replace image" : "Set image"}
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); pickImage(card.slug); }}
                      className="flex size-7 pointer-coarse:size-10 items-center justify-center rounded-full border bg-background/80 text-muted-foreground backdrop-blur transition-colors hover:text-primary"
                    >
                      <ImagePlus className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      title="Edit this system"
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); setRenameFor(card); }}
                      className="flex size-7 pointer-coarse:size-10 items-center justify-center rounded-full border bg-background/80 text-muted-foreground backdrop-blur transition-colors hover:text-primary"
                    >
                      <Pencil className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      title="Save as template"
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); setSaveTplFor(card); }}
                      className="flex size-7 pointer-coarse:size-10 items-center justify-center rounded-full border bg-background/80 text-muted-foreground backdrop-blur transition-colors hover:text-primary"
                    >
                      <BookmarkPlus className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      title="Export this system to a file"
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); exportSystem(card.slug); }}
                      className="flex size-7 pointer-coarse:size-10 items-center justify-center rounded-full border bg-background/80 text-muted-foreground backdrop-blur transition-colors hover:text-primary"
                    >
                      <Download className="size-3.5" />
                    </button>
                    {card.image && (
                      <button
                        type="button"
                        title="Remove image"
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); removeImage(card.slug); }}
                        className="flex size-7 pointer-coarse:size-10 items-center justify-center rounded-full border bg-background/80 text-muted-foreground backdrop-blur transition-colors hover:text-destructive"
                      >
                        <ImageOff className="size-3.5" />
                      </button>
                    )}
                  </span>
                )}
                {selecting && (
                  <span
                    className={
                      "absolute left-3 top-3 flex size-5 items-center justify-center rounded-full border " +
                      (isSel
                        ? "border-destructive bg-destructive text-white"
                        : "border-muted-foreground/40 bg-background/80")
                    }
                  >
                    {isSel && <Check className="size-3.5" />}
                  </span>
                )}
                <span
                  className={
                    "absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 " +
                    "text-[11px] font-medium " + st.pill
                  }
                >
                  <span className={"size-1.5 rounded-full " + st.dot} />
                  {st.label}
                </span>
              </div>
              <div className="flex items-start justify-between gap-2 border-t p-4">
                <div className="min-w-0">
                  <div className="truncate font-semibold leading-tight">{card.name}</div>
                  {/* The id shows only when it adds something: a system called
                      "test" with the id "test" says it once. */}
                  {(card.name || "").trim().toLowerCase() !== String(card.slug).toLowerCase() && (
                    <div className="truncate font-mono text-[11px] leading-tight text-muted-foreground">{card.slug}</div>
                  )}
                </div>
                {!dead && !selecting && (
                  <span className="flex shrink-0 items-center gap-2">
                    {/* Start when the master is down, Stop when it answers.
                        Same power route System Control uses; a card mid-reset
                        is already on its way back, so no button then. */}
                    {!card.resetting && (
                      <button
                        type="button"
                        title={st === STATUS.down ? "Start this system" : "Stop this system"}
                        disabled={powerBusy.has(card.slug)}
                        onClick={(e) => {
                          e.preventDefault(); e.stopPropagation();
                          powerSystem(card, st === STATUS.down ? "start" : "stop");
                        }}
                        className={
                          "flex size-7 items-center justify-center rounded-md border bg-background text-muted-foreground transition-colors disabled:opacity-50 " +
                          (st === STATUS.down
                            ? "hover:border-emerald-600/50 hover:text-emerald-600"
                            : "hover:border-red-600/40 hover:text-red-600")
                        }
                      >
                        {powerBusy.has(card.slug)
                          ? <Loader2 className="size-3.5 animate-spin" />
                          : st === STATUS.down
                            ? <Play className="size-3.5" />
                            : <Square className="size-3.5" />}
                      </button>
                    )}
                    <ArrowUpRight className="mt-0.5 size-4 text-muted-foreground transition-all group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary" />
                  </span>
                )}
              </div>
            </Card>
          );
          if (selecting) {
            return (
              <button
                key={card.slug}
                type="button"
                onClick={() => toggle(card.slug)}
                aria-pressed={isSel}
                className="block rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {inner}
              </button>
            );
          }
          if (dead) {
            return <div key={card.slug} title="No master configured for this system">{inner}</div>;
          }
          // "down" is the master not answering -- opening card.url would just
          // be a browser connection-refused page, not a page this app can
          // show anything useful on. Told rather than sent there.
          if (st === STATUS.down) {
            return (
              <button
                key={card.slug} type="button"
                onClick={() => toast.error(`${card.name || card.slug} is not running`,
                                           "Press Start on its card first.")}
                className="block w-full rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {inner}
              </button>
            );
          }
          return (
            <a
              key={card.slug}
              href={card.url}
              // A master is another PORT, and sessionStorage does not cross
              // ports -- the click carries this tab's identity in the link's
              // fragment. The plain href stays for middle-click/copy-link,
              // which degrade to the browser's cookie identity.
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                e.preventDefault();
                goWithSession(withSession(card.url));
              }}
              className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {inner}
            </a>
          );
        })}

        {/* With no create card (at the cap, or not an admin) an account that
            can see nothing would otherwise face a blank page. */}
        {!selecting && !query.trim() && !shown.length && atLimit && (
          <div className="col-span-full py-10 text-center text-sm text-muted-foreground">
            This box already holds its {maxSystems} systems, none of them shared
            with this account.
          </div>
        )}
        {!selecting && !query.trim() && !shown.length && !atLimit
          && !user?.is_admin && (
          <div className="col-span-full py-10 text-center text-sm text-muted-foreground">
            No system has been shared with this account yet - an administrator
            creates systems and grants access to them.
          </div>
        )}

        {query.trim() && !shown.length && (
          <div className="col-span-full py-10 text-center text-sm text-muted-foreground">
            No system matches “{query.trim()}”.
          </div>
        )}

        {/* Create card. Always last, so "build another" is where the eye already
            ends up - and it is the only thing on screen when there are none.
            Deliberately styled AGAINST the system cards: tinted fill, solid
            accent border, accent text. A card that looks like its neighbours
            reads as another system; this one has to read as an action.
            Hidden while selecting: that mode is about removal, not creation;
            at the system cap; and for non-admins, who may open the systems
            shared with them but not add to the box's count. */}
        {/* One tile per free SLOT (see the banner). */}
        {user?.is_admin && !selecting && !query.trim() && !atLimit
          && Array.from({ length: freeSlots }, (_, i) => (
          <button
            key={`slot-${i}`}
            type="button"
            data-new-slot={i}
            onClick={() => onCreate()}
            className="flex min-h-[11.5rem] flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-primary/40 p-6 text-center text-muted-foreground transition-all hover:border-primary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus className="size-5" />
            <span className="text-xs font-medium">New system</span>
          </button>
        ))}
      </div>

      <EditSystemDialog
        card={renameFor}
        onOpenChange={(o) => { if (!o) setRenameFor(null); }}
        onSaved={() => reload().catch(() => {})}
      />

      <FormDialog
        open={!!saveTplFor}
        onOpenChange={(o) => { if (!o && !busy) setSaveTplFor(null); }}
        title="Save as template"
        description={saveTplFor
          ? `Keep ${saveTplFor.name || saveTplFor.slug} as a starting point: its workflow, zones, dashboard and settings. Cameras, passwords, ports and recorded data are not kept. A template is never run - you create a new system from it.`
          : ""}
        fields={[
          { name: "name", label: "Template name", required: true,
            defaultValue: saveTplFor ? `${saveTplFor.name || saveTplFor.slug} template` : "",
            placeholder: "e.g. Loading bay counting" },
          { name: "description", label: "Description (optional)",
            placeholder: "What this template is for" },
        ]}
        submitLabel="Save template"
        busy={busy}
        onSubmit={saveAsTemplate}
      />

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={(o) => !o && setConfirmOpen(false)}
        title={`Remove ${sel.size} system${sel.size === 1 ? "" : "s"}?`}
        description="Their masters are stopped and they disappear from this hub. Generated packages are deleted; hand-written ones (like the demo) keep their files and can be re-enabled in config."
        confirmLabel="Remove"
        destructive
        busy={busy}
        onConfirm={removeSelected}
      >
        <ul className="grid gap-1 text-sm">
          {selectedCards.map((c) => (
            <li key={c.slug} className="flex items-center gap-2">
              <span aria-hidden>{c.icon || "◆"}</span>
              <span className="font-medium">{c.name}</span>
              <span className="font-mono text-xs text-muted-foreground">{c.slug}</span>
            </li>
          ))}
        </ul>
      </ConfirmDialog>


      {/* Two files, two questions. The design is required; the data is asked
          about, because most imports do not want it -- a system copied to a
          second box is usually meant to start empty -- and the ones that do
          would otherwise have no way to say so. */}
      <Dialog open={!!pendingImport} title="Import a system"
              onClose={() => !busy && setPendingImport(null)}
              footer={
                <>
                  <button className="btn btn-sm" disabled={busy}
                          onClick={() => setPendingImport(null)}>Cancel</button>
                  <button className="btn btn-sm btn-accent" disabled={busy
                            || (pendingImport?.withDb && !pendingImport?.db)}
                          onClick={runImport}>
                    {busy ? "Importing…" : "Import"}
                  </button>
                </>
              }>
        {pendingImport && (
          <div className="rail-form">
            <div className="rail-brief">
              <div className="rail-brief-row">
                <span className="muted small">system</span>
                <span className="small">{pendingImport.file.name}</span>
              </div>
            </div>
            <div className="wf-field">
              <label className="wf-field-label" htmlFor="import-name">Name</label>
              <Input id="import-name" value={pendingImport.name} disabled={busy}
                     placeholder={pendingImport.exported || "the name in the file"}
                     onChange={(e) => setPendingImport((p) => ({ ...p, name: e.target.value }))} />
              <p className="muted small">
                Keep the name from the file, or type a new one. The system's
                folder name is made from it, with a number added if that
                folder is already taken.
              </p>
            </div>
            <p className="muted small" style={{ marginTop: 0 }}>
              This file carries the design - cameras, zones, the workflow and
              the dashboards. It never carries recorded data, so the system
              arrives set up and empty.
            </p>
            <label className="wf-check">
              <input type="radio" name="hubdb" checked={!pendingImport.withDb}
                     onChange={() => setPendingImport(
                       (p) => ({ ...p, withDb: false, db: null }))} />
              <span>
                <strong>No database</strong> - start it empty. It records from
                the moment it runs.
              </span>
            </label>
            <label className="wf-check">
              <input type="radio" name="hubdb" checked={!!pendingImport.withDb}
                     onChange={() => setPendingImport((p) => ({ ...p, withDb: true }))} />
              <span>
                <strong>Bring its database</strong> - the SQLite file exported
                from that system's Database page. Its events, its Data Table
                rows and their columns arrive with it.
              </span>
            </label>
            {pendingImport.withDb && (
              <>
                <div className="rail-line">
                  <button type="button" className="btn btn-sm" disabled={busy}
                          onClick={() => importDbRef.current?.click()}>
                    Choose the .db file
                  </button>
                  <span className="small muted">
                    {pendingImport.db ? pendingImport.db.name : "nothing chosen yet"}
                  </span>
                </div>
                <input ref={importDbRef} type="file"
                       accept=".db,.sqlite,.sqlite3" style={{ display: "none" }}
                       onChange={(e) => {
                         const f = e.target.files?.[0] || null;
                         e.target.value = "";
                         setPendingImport((p) => ({ ...p, db: f }));
                       }} />
                <p className="muted small">
                  Snapshots and clips are not in that file and do not travel -
                  an imported event still names the picture it refers to, but
                  the picture stays on the box it came from.
                </p>
              </>
            )}
          </div>
        )}
      </Dialog>

    </div>
  );
}
