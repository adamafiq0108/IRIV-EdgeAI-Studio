// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Footage - the images this system saved. Nothing lands here by accident any
// more: a frame is written only when a zone action asks for a photo or a
// workflow Snapshot node fires, so every image is one somebody configured.
//
// Browsing is by DATE, because that is how an incident is remembered ("last
// Tuesday afternoon"), with selection and bulk delete so a shift's worth can
// be cleared in one action rather than one click per image.
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useApi } from "../api/hooks.js";
import { api, saveBlob } from "../api/client.js";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { Badge, EmptyState } from "../components/ui/primitives.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import DateRangePicker from "../components/ui/DateRangePicker.jsx";
import EmailFootageDialog from "../components/EmailFootageDialog.jsx";
import SnapshotWindow from "../components/SnapshotWindow.jsx";
import { Film, Image as ImageIcon, LayoutGrid, List } from "lucide-react";
import { subscribe, getPending, hide, unhide, commit } from "./footageDeletes.js";
import { useEscape } from "../hooks/use-escape.js";
import { apiUrl } from "../lib/base.js";

//: A file whose camera could not be worked out -- saved before captures were
//: tagged, or by a camera that has since been removed. Grouped together and
//: named, rather than hidden or blamed on whichever camera sorts first.
const NO_CAMERA = "__none__";

//: Grid or table, remembered per browser. Which one is right depends on what
//: is being done: a grid is for RECOGNISING ("the one with the forklift"), a
//: table is for READING (what came from which camera, in what order, how big).
//: Neither answers both, so it is a choice rather than a redesign.
const VIEW_KEY = "footage.view";

//: How many rows a page holds, and where that choice is remembered. Fifty is
//: the default because it fills a screen without making the browser open a
//: thousand thumbnail connections at once.
const SIZE_KEY = "footage.pagesize";
const PAGE_SIZES = [10, 25, 50, 100];

function human(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

/** Which page buttons to draw: always the first and last, always the current
 *  and its neighbours, `null` where a run is elided.
 *
 *  Forty numbered buttons is not navigation, it is a wall -- and the two that
 *  are always worth one click ("back to the start", "how far does this go")
 *  are the ones a plain window would drop. */
function pageWindow(current, pages, span = 1) {
  const want = new Set([0, pages - 1]);
  for (let p = current - span; p <= current + span; p += 1) {
    if (p >= 0 && p < pages) want.add(p);
  }
  const out = [];
  let prev = -1;
  for (const p of [...want].sort((a, b) => a - b)) {
    if (prev >= 0 && p > prev + 1) out.push(null);
    out.push(p);
    prev = p;
  }
  return out;
}

function startOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

function isoDay(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Workflow Snapshot nodes save as cap_<ts>; everything else came from a zone
// action's alert.
function kindOf(id) {
  return id.startsWith("cap_") ? "capture" : "event";
}

function when(mtime) {
  return new Date(mtime * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    second: "2-digit",
  });
}

// The table spells the date out. A grid card is read under a day heading, so
// "10:36:15 AM" is unambiguous there; a table row gets copied into a report,
// pasted into a message, read on its own -- and "Sep 4" then has no year.
function whenLong(mtime) {
  return new Date(mtime * 1000).toLocaleString(undefined, {
    day: "2-digit", month: "long", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function dayLabel(mtime) {
  const d = startOfDay(new Date(mtime * 1000));
  const today = startOfDay(new Date());
  const diff = Math.round((today - d) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short",
                                           day: "numeric" });
}

// A thumbnail that fails to load retries a few times with a cache-buster --
// a snapshot is often requested the moment it is still being written, and a
// one-shot <img> stayed black forever with its filename as alt text. Past
// the retries it says what it is instead of shouting the filename.
function Thumb({ src, alt, overlay = false, height }) {
  const [attempt, setAttempt] = useState(0);
  const [dead, setDead] = useState(false);
  useEffect(() => { setAttempt(0); setDead(false); }, [src]);
  const box = height
    ? { width: (height * 16) / 9, height, flex: "none" }
    : { width: "100%", aspectRatio: "16/9" };
  if (dead) {
    // A clip whose poster frame could not be made (no OpenCV on the box, or
    // a file still being written) falls back to the play triangle it always
    // had. A photo says so in words -- it has no second thing to be.
    return (
      <div style={{ ...box, borderRadius: 6, background: "#000",
                    display: "grid", placeItems: "center",
                    color: overlay ? "#8aa" : "var(--muted, #6b6b6b)",
                    fontSize: overlay ? 26 : 11 }}>
        {overlay ? "▶" : "image unavailable"}
      </div>
    );
  }
  const img = (
    <img src={attempt ? `${src}${src.includes("?") ? "&" : "?"}r=${attempt}` : src}
         alt={alt} loading="lazy"
         onError={() => {
           if (attempt >= 3) { setDead(true); return; }
           setTimeout(() => setAttempt((a) => a + 1), 800 * (attempt + 1));
         }}
         style={{ ...box, borderRadius: 6, display: "block",
                  background: "#000", objectFit: "cover" }} />
  );
  if (!overlay) return img;
  // The play mark stays ON the poster frame: the picture says which clip this
  // is, the triangle says it is a clip and not a photo. Losing the second
  // would make the two media indistinguishable in a mixed-looking grid.
  return (
    <div style={{ ...box, position: "relative" }}>
      {img}
      {/* contrast-ok: the play glyph sits on the video thumbnail, with its own
          shadow under it -- there is no theme surface behind it. */}
      <span style={{ position: "absolute", inset: 0, display: "grid",
                     placeItems: "center", pointerEvents: "none",
                     color: "#fff", fontSize: height ? 16 : 26,
                     textShadow: "0 1px 6px rgba(0,0,0,.9)" }}>
        {"▶"}
      </span>
    </div>
  );
}

export default function Footage() {
  const toast = useToast();
  // Which camera's footage. This used to be "Zone alerts / Workflow
  // captures", which sorted by the MECHANISM that saved a picture -- a
  // distinction nobody looking for an incident has ever cared about. On a
  // four-camera system the question is always "which camera", and the badge
  // on each card still says how it was saved.
  const [cam, setCam] = useState("all");
  // WHICH Snapshot node's page: "all" (every photo, whatever page), or a page's slug.
  const [pageKey, setPageKey] = useState("all");
  // Always a bounded window. "Today"/"7 days"/"30 days" used to be separate
  // buttons; DateRangePicker's own preset rail already offers all of those
  // (and more), so keeping a parallel set of buttons here was the same
  // choice offered twice in two different widgets.
  const [from, setFrom] = useState(isoDay(new Date(Date.now() - 6 * 86400000)));
  const [to, setTo] = useState(isoDay(new Date()));
  const [selected, setSelected] = useState(() => new Set());
  const [lightbox, setLightbox] = useState(null);
  useEscape(!!lightbox, () => setLightbox(null));
  // Deleted-but-not-yet-committed ids: hidden from the grid while the Undo
  // window is open, so the page looks done the instant the operator clicks.
  // Kept OUTSIDE the component (footageDeletes.js) so that leaving Footage
  // mid-window neither un-hides them nor strands the delete.
  const pending = useSyncExternalStore(subscribe, getPending);
  const [media, setMedia] = useState("photo");   // photo | clip
  const [view, setView] = useState(() => {
    try { return localStorage.getItem(VIEW_KEY) === "table" ? "table" : "grid"; }
    catch { return "grid"; }
  });
  useEffect(() => {
    try { localStorage.setItem(VIEW_KEY, view); } catch { /* private window */ }
  }, [view]);
  // Table order. Newest first is the default because footage is looked for by
  // when it happened; the other columns are there for the other questions
  // ("everything from that camera", "the big ones").
  const [sort, setSort] = useState({ col: "mtime", dir: "desc" });
  const sortBy = (col) => setSort((s) => (s.col === col
    ? { col, dir: s.dir === "desc" ? "asc" : "desc" }
    // A new column starts descending for time and size (biggest, newest --
    // what people mean by "sort by that"), ascending for names.
    : { col, dir: col === "mtime" || col === "size" ? "desc" : "asc" }));
  const arrow = (col) => (sort.col === col ? (sort.dir === "desc" ? " ▾" : " ▴") : "");
  //: A primitive the reset effect can depend on -- `sort` is a fresh object
  //: every render, so depending on it would reset the page on every render.
  const sortKey = `${sort.col}:${sort.dir}`;
  // Paging. A month of a busy line is thousands of rows, and rendering them
  // all meant thousands of <img> elements: the browser opened a connection per
  // thumbnail and the page took seconds to become usable.
  const [pageSize, setPageSize] = useState(() => {
    const n = Number(localStorage?.getItem?.(SIZE_KEY));
    return PAGE_SIZES.includes(n) ? n : 50;
  });
  const [page, setPage] = useState(0);
  useEffect(() => {
    try { localStorage.setItem(SIZE_KEY, String(pageSize)); }
    catch { /* private window */ }
  }, [pageSize]);
  // Any change to WHAT is listed goes back to the first page: page 7 of the
  // old list is a meaningless position in the new one.
  useEffect(() => { setPage(0); }, [cam, from, to, media, pageSize, sortKey]);
  const [mailOpen, setMailOpen] = useState(false);
  const [zipping, setZipping] = useState(false);
  // Read when the page is opened, and again after a delete: the lists hold
  // every photo and clip there is, so they are not re-fetched on a timer.
  const { data, error, reload } = useApi("/api/detector/snapshots");
  const { data: clipData, reload: reloadClips } = useApi("/api/detector/clips");
  // What this account may DO with footage, answered by the same endpoint
  // that lists it -- and enforced by the same grant behind that answer
  // (core/services/caps.py). Deleting and exporting are two capabilities: an
  // account may be trusted to send an incident out and not to erase it.
  const can = (cap) => ((data?.caps || clipData?.caps || []).includes(cap));

  // Changing what is on screen clears the selection: a bulk delete must never
  // reach images the operator can no longer see.
  useEffect(() => { setSelected(new Set()); }, [cam, from, to, media, pageKey]);
  // Photos and clips are filtered by the same camera buttons, but a camera
  // that saved photos may have recorded no clips -- so switching media with a
  // camera picked would land on an empty grid with no visible reason.
  useEffect(() => { setCam("all"); setPageKey("all"); }, [media]);

  // Everything in this media, before the camera filter -- the camera buttons
  // are built from it, so they must not disappear as soon as one is picked.
  const all = useMemo(() => (media === "clip"
    ? (clipData?.clips || []).map((c) => ({
        id: c.name, mtime: c.mtime, size: c.bytes,
        camera: c.camera || "", camera_name: c.camera_name || "",
        page: c.page || "" }))
    : (data?.snapshots || [])), [data, clipData, media]);

  // What the picked DATES leave: every count on this page (the Photos and Clips
  // buttons, the pages, the cameras) is of these, so a button never promises
  // photos the grid will not show.
  const lo = startOfDay(new Date(from)).getTime() / 1000;
  const hi = (startOfDay(new Date(to)).getTime() + 86400000) / 1000;
  const inDates = (r) => r.mtime >= lo && r.mtime < hi && !pending.has(r.id);
  const photosInDates = useMemo(
    () => (data?.snapshots || []).filter(inDates),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, lo, hi, pending]);
  const clipsInDates = useMemo(
    () => (clipData?.clips || []).filter((c) => inDates({ id: c.name, mtime: c.mtime })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [clipData, lo, hi, pending]);

  // The Snapshot nodes' pages, and how many photos each holds in those dates.
  const snapPages = useMemo(
    () => (media === "clip" ? clipData?.pages : data?.pages) || [], [data, clipData, media]);
  const pageCounts = useMemo(() => {
    const m = new Map();
    for (const r of (media === "clip" ? clipsInDates : photosInDates)) {
      // With a camera picked, a page counts only what that camera saved on it.
      if (cam !== "all" && (r.camera || NO_CAMERA) !== cam) continue;
      if (r.page) m.set(r.page, (m.get(r.page) || 0) + 1);
    }
    return m;
  }, [photosInDates, clipsInDates, media, snapPages, cam]);
  // ...and only the pages that camera has anything on are offered.
  const shownPages = useMemo(
    () => (cam === "all" ? snapPages : snapPages.filter((pg) => pageCounts.get(pg.slug))),
    [snapPages, pageCounts, cam]);
  // A picked page the camera has nothing on goes back to every page.
  useEffect(() => {
    if (cam !== "all" && pageKey !== "all" && !pageCounts.get(pageKey)) setPageKey("all");
  }, [cam, pageKey, pageCounts]);

  // One button per camera that actually saved something in this media. A
  // camera with no footage is not offered: a filter that can only ever
  // produce an empty grid is a button that lies about having results.
  const cameras = useMemo(() => {
    const seen = new Map();
    for (const r of all) {
      if (!inDates({ id: r.id, mtime: r.mtime })) continue;
      if (pageKey !== "all" && r.page !== pageKey) continue;
      const key = r.camera || NO_CAMERA;
      if (!seen.has(key)) {
        seen.set(key, { key, label: r.camera_name || r.camera || "Unattributed",
                        count: 0 });
      }
      seen.get(key).count += 1;
    }
    return [...seen.values()].sort((a, b) => (a.key === NO_CAMERA ? 1 : 0)
                                           - (b.key === NO_CAMERA ? 1 : 0)
                                           || a.label.localeCompare(b.label));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all, lo, hi, pending, pageKey, media, snapPages]);

  const rows = useMemo(() => {
    // Clips carry `name`, snapshots carry `id`. Normalise here so one grid,
    // one selection model and one delete path serve both.
    let list = all;
    if (pageKey !== "all") {
      list = list.filter((r) => r.page === pageKey);
    }
    if (cam !== "all") list = list.filter((r) => (r.camera || NO_CAMERA) === cam);
    // LOCAL-midnight to LOCAL-midnight, not "24h ago" -- an operator asking
    // for a day means since midnight, and a rolling window silently hides
    // this morning's images once it is past noon.
    list = list.filter((r) => r.mtime >= lo && r.mtime < hi);
    // Awaiting the Undo window: gone from view, still on disk.
    if (pending.size) list = list.filter((r) => !pending.has(r.id));
    return list;
  }, [all, cam, lo, hi, pending, pageKey, media, snapPages]);

  // Everything the filters allow, in the order it will be shown -- BEFORE the
  // page is cut out of it. Ordering has to happen across the whole list or
  // "biggest first" would mean "biggest on this page", which is a different
  // and much less useful sentence.
  const ordered = useMemo(() => {
    if (view !== "table" || sort.col === "mtime") {
      return sort.dir === "asc" && view === "table" ? rows.slice().reverse() : rows;
    }
    const key = (r) => {
      if (sort.col === "camera") return (r.camera_name || r.camera || "").toLowerCase();
      if (sort.col === "kind") {
        return media === "clip" ? "clip"
          : kindOf(r.id) === "capture" ? "capture" : "alert";
      }
      if (sort.col === "size") return r.size ?? 0;
      return String(r.id).toLowerCase();
    };
    const flat = rows.slice().sort((a, b) => {
      const ka = key(a);
      const kb = key(b);
      // Numbers compare as numbers; everything else with a collator, so
      // "Camera 10" sorts after "Camera 9" rather than between 1 and 2.
      const cmp = typeof ka === "number"
        ? ka - kb
        : String(ka).localeCompare(String(kb), undefined, { numeric: true });
      // Ties fall back to newest-first, so equal rows keep a stable, useful
      // order instead of whatever the filesystem listed.
      return (sort.dir === "desc" ? -cmp : cmp) || b.mtime - a.mtime;
    });
    return flat;
  }, [rows, view, sort, media]);

  const total = ordered.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  // A filter that shortens the list must not leave somebody stranded on page
  // 12 of 3 looking at nothing, with no clue that the rows exist elsewhere.
  const pageNow = Math.min(page, pages - 1);
  const paged = useMemo(
    () => ordered.slice(pageNow * pageSize, pageNow * pageSize + pageSize),
    [ordered, pageNow, pageSize]);

  // The page, grouped by day so a long list reads as a diary rather than a
  // wall. Sorting by anything other than time DROPS the headings and shows
  // one flat list -- "all the Arducam clips" split into eleven per-day runs is
  // not sorted by camera, it is sorted by day and then by camera, which is the
  // answer to a question nobody asked.
  const blocks = useMemo(() => {
    if (view === "table" && sort.col !== "mtime") {
      return [{ label: "", rows: paged }];
    }
    const out = [];
    for (const r of paged) {
      const label = dayLabel(r.mtime);
      const last = out[out.length - 1];
      if (last && last.label === label) last.rows.push(r);
      else out.push({ label, rows: [r] });
    }
    return out;
  }, [paged, view, sort.col]);

  // A single failed poll (a master mid-restart, a busy moment) must not
  // blank a page that already holds footage -- the grid keeps showing what
  // it has and the next 8s poll heals it. Only a failure BEFORE anything
  // loaded is a page-level error.
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return <SkelPage title="Footage" cards={1} rows={5} />;

  const isClip = media === "clip";
  const imgUrl = (id) => (isClip
    ? apiUrl(`/api/detector/clips/${encodeURIComponent(id)}`)
    : apiUrl(`/api/detector/snapshots/${encodeURIComponent(id)}`));
  // "Select all" means the page. Ticking 1,997 rows behind a Delete that only
  // shows the first fifty of them is not a selection anybody made on purpose.
  const allShown = paged.length > 0 && paged.every((r) => selected.has(r.id));

  function toggle(id) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  // DEFERRED delete, not a confirmed one. Nothing is sent to the server while
  // the Undo toast is up: the rows are hidden locally, and the request only
  // goes out when the regret window closes. That makes Undo a real
  // cancellation rather than a restore that could fail -- and it costs one
  // click instead of a dialog, which is the right trade for something the
  // operator can see the result of immediately.
  //
  // `pendingIds` is what the grid filters out meanwhile, so the page already
  // looks deleted while it still is not.
  function deleteLater(ids, noun) {
    if (!ids.length) return;
    hide(ids);
    setSelected(new Set());
    setLightbox(null);
    const n = ids.length;
    toast.undoable(
      `Deleted ${n} ${noun}${n === 1 ? "" : "s"}`,
      // The toast owns the timer and lives above the router, so this runs
      // whatever page the operator is on when the window closes. commit()
      // refetches BEFORE it stops hiding the ids, which is what stops the
      // deleted images flashing back for the length of one round trip.
      () => commit(ids, isClip,
                   () => Promise.allSettled([reload(), reloadClips()]))
              .catch((e) => toast.error("Could not delete", e.message)),
      { detail: "Undo to keep them.", onUndo: () => unhide(ids) });
  }

  /** The list, as a spreadsheet -- the table's columns and nothing heavier.
   *
   *  Built here rather than server-side because the rows on screen are already
   *  the answer: the camera filter, the date range and the undo-pending
   *  suppression have all been applied, and an endpoint would have to be told
   *  about each of them to agree with what somebody is looking at.
   *
   *  Ticked rows win when there are any -- the same rule Export evidence and
   *  Delete follow, so a selection means one thing on this page.
   */
  /** The ticked footage as ONE zip, saved here.
   *
   *  Same selection rule as Delete -- ticked rows win, and
   *  with none ticked it means the whole filtered list, not the visible page.
   *  The FILES, though, not the list: a CSV of filenames is not evidence.
   *
   *  The box builds the archive: it has the files, and a browser fetching two
   *  hundred images one at a time to zip them itself would be two hundred
   *  round trips to rebuild what the disk already holds.
   */
  async function downloadZip() {
    const shown = ordered;
    const want = selected.size ? shown.filter((r) => selected.has(r.id)) : shown;
    if (!want.length || zipping) return;
    setZipping(true);
    try {
      const kind = isClip ? "clip" : "snapshot";
      const got = await api.download("/api/detector/footage/download", {
        items: want.map((r) => ({ kind, id: r.id })),
      });
      saveBlob(got);
      toast.success(`${want.length} file${want.length === 1 ? "" : "s"} downloaded`,
                    got.filename);
    } catch (e) {
      toast.error("Could not build the download", e.message);
    } finally {
      setZipping(false);
    }
  }

  function removeSelected() {
    deleteLater([...selected], isClip ? "clip" : "image");
  }

  function removeOne(id) {
    deleteLater([id], isClip ? "clip" : "image");
  }

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title">Footage</div>
        <div className="page-actions">
          <DateRangePicker start={from} end={to}
                           onApply={(r) => { setFrom(r.start); setTo(r.end); }} />
          <Link className="btn" to="/events">Events Logs →</Link>
        </div>
      </div>

      <div className="filter-bar" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        <button className={"btn btn-sm"
                  + (media === "photo" && pageKey === "all" ? " btn-accent" : "")}
                data-all-photos
                onClick={() => { setMedia("photo"); setPageKey("all"); }}>
          All photos ({photosInDates.length})
        </button>
        <button className={"btn btn-sm"
                  + (media === "clip" && pageKey === "all" ? " btn-accent" : "")}
                data-all-clips
                onClick={() => { setMedia("clip"); setPageKey("all"); }}>
          All clips ({clipsInDates.length})
        </button>
        <span style={{ width: 8 }} />
        {/* One camera, or all of them. Offered only when there is more than
            one to choose between -- a single-camera system would otherwise
            get a filter whose two buttons always show the same grid. */}
        {cameras.length > 1 && (
          <>
            <button className={"btn btn-sm" + (cam === "all" ? " btn-accent" : "")}
                    onClick={() => setCam("all")}>All</button>
            {cameras.map((c) => (
              <button key={c.key}
                      className={"btn btn-sm" + (cam === c.key ? " btn-accent" : "")}
                      title={c.key === NO_CAMERA
                        ? "Saved before captures recorded their camera, or by a "
                          + "camera that has since been removed"
                        : c.label}
                      onClick={() => setCam(c.key)}>
                {c.label} <span className="muted">({c.count})</span>
              </button>
            ))}
          </>
        )}
        <button className="btn btn-sm" disabled={!paged.length}
                title={total > paged.length
                  ? `The ${paged.length} on this page` : undefined}
                onClick={() => setSelected(allShown ? new Set()
                                                    : new Set(paged.map((r) => r.id)))}>
          {allShown ? "Select none" : "Select all"}
        </button>
        <div style={{ flex: 1 }} />
        {/* Grid or table. A grid is for recognising a picture; a table is for
            reading what came from where and when. */}
        <span style={{ display: "inline-flex" }}>
          <button className={"btn btn-sm" + (view === "grid" ? " btn-accent" : "")}
                  title="Thumbnails" onClick={() => setView("grid")}>
            <LayoutGrid size={13} />
          </button>
          <button className={"btn btn-sm" + (view === "table" ? " btn-accent" : "")}
                  title="Table - camera, time and size in columns"
                  onClick={() => setView("table")}>
            <List size={13} />
          </button>
        </span>
        <span className="muted small">
          {selected.size ? `${selected.size} of ${rows.length} selected`
            : `${rows.length} ${isClip ? "clip" : "image"}${rows.length === 1 ? "" : "s"}`}
        </span>
        {/* Email sits BESIDE Delete because they are the two things done to a
            selection, and it defaults to that selection: an operator who has
            ticked six images means those six. With nothing ticked it offers
            the visible date range instead, which is the other way people say
            "send me Tuesday". */}
        {/* DOWNLOAD is the third thing done to a selection, and the one
            people reach for first: the files, on this machine, in one zip.
            Beside Export evidence because they are the same bundle going to
            two different places -- an inbox, or the folder you are standing
            in front of. The Monitor account holds the narrow "Download
            footage" capability only (no email, no schedules), so this button
            answers to it as well as to the wider export one. */}
        {(can("footage.download") || can("footage.export")) && (
          <button className="btn btn-sm" disabled={zipping || !rows.length}
                  title={selected.size
                    ? `The ${selected.size} ticked, as one zip`
                    : "Everything in this range, as one zip"}
                  onClick={downloadZip}>
            {zipping ? "Preparing…"
              : selected.size ? `Download (${selected.size})` : "Download all"}
          </button>
        )}
        {can("footage.export") && (
          <button className="btn btn-sm" onClick={() => setMailOpen(true)}>
            {selected.size ? `Export evidence (${selected.size})` : "Export evidence"}
          </button>
        )}
        {can("footage.delete") && (
          <button className="btn btn-sm btn-danger" disabled={!selected.size}
                  onClick={removeSelected}>
            {`Delete${selected.size ? ` (${selected.size})` : ""}`}
          </button>
        )}
      </div>

      {/* One page for every Snapshot node that has a name, so the photos of
          different nodes are never mixed: the node's own name is the tab. */}
      {shownPages.length > 0 && (
        <div className="filter-bar" style={{ marginBottom: 8, flexWrap: "wrap" }}
             data-snapshot-pages>
          {shownPages.map((pg) => (
            <button key={pg.slug} data-page={pg.slug}
                    className={"btn btn-sm" + (pageKey === pg.slug ? " btn-accent" : "")}
                    onClick={() => setPageKey(pg.slug)}>
              {pg.name} <span className="muted">({pageCounts.get(pg.slug) || 0})</span>
            </button>
          ))}
        </div>
      )}

      {mailOpen && (
        <EmailFootageDialog
          onClose={() => setMailOpen(false)}
          media={media}
          from={from} to={to}
          selection={[...selected].map((id) => ({
            kind: isClip ? "clip" : "snapshot", id }))}
          shownCount={rows.length} />
      )}

      {rows.length === 0 ? (
        <div className="panel-card">
          <EmptyState icon={isClip ? Film : ImageIcon}
                      title={`No ${isClip ? "clips" : "images"} captured`}
                      actions={[{ label: "Set up capture", to: "/detection" }]} />
        </div>
      ) : blocks.map((g) => (
        <div key={g.label || "all"} style={{ marginBottom: 16 }}>
          {/* No heading on a flat, non-date sort: a day label over rows that
              are not in day order would be a lie about what is underneath. */}
          {g.label ? (
            <div className="muted small" style={{ marginBottom: 6, fontWeight: 600 }}>
              {g.label} <span style={{ fontWeight: 400 }}>· {g.rows.length}</span>
            </div>
          ) : null}
          {view === "table" ? (
            /* db-grid, the same table the Database page uses -- a second
               hand-rolled table style would make two pages that do the same
               thing look like two products. */
            <div className="panel-card" style={{ padding: 0, overflowX: "auto" }}>
              <table className="db-grid ft-grid">
                <thead>
                  <tr>
                    <th className="db-pick">
                      {/* Select-all for THIS day. The toolbar's own "Select
                          all" takes the whole page; this one takes the block
                          it sits on, which is how somebody says "Tuesday". */}
                      <input type="checkbox"
                             checked={g.rows.every((r) => selected.has(r.id))}
                             onChange={(e) => setSelected((cur) => {
                               const next = new Set(cur);
                               for (const r of g.rows) {
                                 if (e.target.checked) next.add(r.id);
                                 else next.delete(r.id);
                               }
                               return next;
                             })} />
                    </th>
                    {/* Sortable, and the arrow says which one is deciding the
                        order. Sorting by anything but time drops the day
                        headings -- see `blocks`. */}
                    <th className="db-sortable ft-id" onClick={() => sortBy("id")}>
                      ID{arrow("id")}
                    </th>
                    <th className="db-sortable" onClick={() => sortBy("mtime")}>
                      Created{arrow("mtime")}
                    </th>
                    <th className="db-sortable" onClick={() => sortBy("camera")}>
                      Camera{arrow("camera")}
                    </th>
                    <th className="db-sortable" onClick={() => sortBy("kind")}>
                      Kind{arrow("kind")}
                    </th>
                    <th>{isClip ? "Frame" : "Snapshot"}</th>
                    {isClip && <th>Clip</th>}
                    <th className="db-sortable" style={{ textAlign: "right" }}
                        onClick={() => sortBy("size")}>
                      Size{arrow("size")}
                    </th>
                    {can("footage.delete") && <th>Actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {g.rows.map((r) => {
                    const on = selected.has(r.id);
                    return (
                      <tr key={r.id} className={on ? "is-sel" : ""}
                          style={{ cursor: "pointer" }}
                          onClick={() => setLightbox(r)}>
                        <td className="db-pick" onClick={(e) => e.stopPropagation()}>
                          <input type="checkbox" checked={on} readOnly
                                 onClick={() => toggle(r.id)} />
                        </td>
                        <td className="mono small ft-id" title={r.id}>{r.id}</td>
                        <td className="db-when">{whenLong(r.mtime)}</td>
                        <td>
                          {r.camera_name || r.camera
                            || <span className="muted">-</span>}
                        </td>
                        <td>
                          <Badge tone={isClip ? "ok"
                                   : kindOf(r.id) === "capture" ? "muted" : "warn"}>
                            {isClip ? "clip"
                              : kindOf(r.id) === "capture" ? "capture" : "alert"}
                          </Badge>
                        </td>
                        {/* Small, but still the picture: a row that only says
                            "clip_cam1_19a3f2.mp4" is a filename, not footage. */}
                        <td>
                          <Thumb src={imgUrl(r.id) + "?thumb=1"} alt={r.id}
                                 height={38} />
                        </td>
                        {/* Play is its own column for clips, because "open
                            this" is a different act from "look at this" and
                            a thumbnail cannot say which of the two it is. */}
                        {isClip && (
                          <td onClick={(e) => e.stopPropagation()}>
                            <button className="ft-play" title="Play this clip"
                                    onClick={() => setLightbox(r)}>▶</button>
                          </td>
                        )}
                        <td className="small" style={{ textAlign: "right" }}>
                          {human(r.size)}
                        </td>
                        {can("footage.delete") && (
                          <td onClick={(e) => e.stopPropagation()}>
                            <button className="ft-del" onClick={() => removeOne(r.id)}>
                              Delete
                            </button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
          <div style={{ display: "grid", gap: 12,
                        gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
            {g.rows.map((r) => {
              const on = selected.has(r.id);
              return (
                <div key={r.id} className="panel-card"
                     style={{ padding: 8, cursor: "pointer",
                              borderColor: on ? "var(--accent)" : undefined }}
                     onClick={() => setLightbox(r)}>
                  <div style={{ position: "relative" }}>
                    {/* A clip gets a POSTER FRAME, same as a photo gets a
                        thumbnail. Every clip used to be the same black
                        rectangle with a play triangle on it, so a page of
                        thirty said nothing about which one held the thing you
                        were looking for -- the only way to find out was to
                        open each in turn. */}
                    <Thumb src={imgUrl(r.id) + "?thumb=1"} alt={r.id}
                           overlay={isClip} />
                    {/* The checkbox is where selection lives now -- the card
                        itself OPENS the picture, because that is what a
                        click on a photo means everywhere else. */}
                    <input type="checkbox" checked={on} readOnly
                           onClick={(e) => { e.stopPropagation(); toggle(r.id); }}
                           style={{ position: "absolute", top: 6, left: 6,
                                    width: 18, height: 18, cursor: "pointer" }} />
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
                    <Badge tone={isClip ? "ok"
                             : kindOf(r.id) === "capture" ? "muted" : "warn"}>
                      {isClip ? "clip" : kindOf(r.id) === "capture" ? "capture" : "alert"}
                    </Badge>
                    <span className="muted small" style={{ flex: 1, textAlign: "right" }}>
                      {when(r.mtime)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          )}
        </div>
      ))}

      {/* The pager. Offered only when there is more than one page: a footer
          reading "1–12 of 12" beside a single disabled page button is furniture
          that says nothing. */}
      {rows.length > 0 && pages > 1 && (
        <div className="ft-foot">
          <label className="ft-foot-size">
            <span className="muted small">Per page</span>
            <select className="input" value={pageSize}
                    onChange={(e) => setPageSize(Number(e.target.value))}>
              {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          {/* Where you are, in rows rather than pages -- "1–50 of 1,997" is
              the fact; "page 1 of 40" is a restatement of the buttons. */}
          <span className="muted small ft-foot-count">
            {(pageNow * pageSize + 1).toLocaleString()}–
            {Math.min(total, (pageNow + 1) * pageSize).toLocaleString()}
            {" of "}{total.toLocaleString()}
          </span>
          <div className="ft-pager">
            <button className="btn btn-sm" disabled={pageNow === 0}
                    title="First page" onClick={() => setPage(0)}>«</button>
            <button className="btn btn-sm" disabled={pageNow === 0}
                    title="Previous page"
                    onClick={() => setPage(pageNow - 1)}>‹</button>
            {pageWindow(pageNow, pages).map((p, i) => (p === null ? (
              <span key={`gap${i}`} className="muted small ft-gap">…</span>
            ) : (
              <button key={p}
                      className={"btn btn-sm" + (p === pageNow ? " btn-accent" : "")}
                      aria-current={p === pageNow ? "page" : undefined}
                      onClick={() => setPage(p)}>{p + 1}</button>
            )))}
            <button className="btn btn-sm" disabled={pageNow + 1 >= pages}
                    title="Next page"
                    onClick={() => setPage(pageNow + 1)}>›</button>
            <button className="btn btn-sm" disabled={pageNow + 1 >= pages}
                    title="Last page"
                    onClick={() => setPage(pages - 1)}>»</button>
          </div>
        </div>
      )}

      {/* The viewer is sized in CSS, not inline, so a phone can have different
          numbers from a monitor: at 24px of scrim padding and a 70vh picture
          there is nothing left for the caption row on a handset in landscape,
          and a media query cannot reach an inline style. */}
      {/* Portalled to the body for the same reason the email dialog is: a
          full-window overlay is only full-window while NO ancestor is a
          containing block, and a transform anywhere above it (the page's own
          entrance, a card's animation) silently shrinks `inset: 0` to that
          ancestor's box -- which is how the scrim ended up covering the list
          instead of the window. */}
      {lightbox && !isClip && createPortal(
        // A photo opens in its own window: the picture with its facts beside
        // it (or under it on a narrow screen), the timer, the close-ups, and
        // the PDF notice. A clip keeps the plain player below.
        <SnapshotWindow row={lightbox} imgUrl={imgUrl}
                        canDelete={can("footage.delete")}
                        onDelete={removeOne} onClose={() => setLightbox(null)} />,
        document.body)}
      {lightbox && isClip && createPortal(
        <div className="fx-lightbox" onClick={() => setLightbox(null)}>
          <div className="panel-card fx-lightbox-card"
               role="dialog" aria-modal="true" aria-label={lightbox.id}
               onClick={(e) => e.stopPropagation()}>
            {isClip ? (
              <video src={imgUrl(lightbox.id)} controls autoPlay
                     className="fx-lightbox-media"
                     style={{ background: "#000" }} />
            ) : (
              <img src={imgUrl(lightbox.id)} alt={lightbox.id}
                   className="fx-lightbox-media" />
            )}
            <div className="filter-bar">
              <code className="mono small">{lightbox.id}</code>
              <span className="muted small">{when(lightbox.mtime)}</span>
              <div style={{ flex: 1 }} />
              <a className="btn btn-sm" href={imgUrl(lightbox.id)}
                 download={isClip ? lightbox.id : `${lightbox.id}.jpg`}>Download</a>
              {can("footage.delete") && (
                <button className="btn btn-sm btn-danger"
                        onClick={() => removeOne(lightbox.id)}>Delete</button>
              )}
              <button className="btn btn-sm" onClick={() => setLightbox(null)}>Close</button>
            </div>
          </div>
        </div>,
        document.body)}
    </section>
  );
}
