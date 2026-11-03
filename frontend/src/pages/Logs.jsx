// Log Hub -- this process's event log, as an operator alarm list.
//
// WHAT THIS PAGE IS FOR. Somebody opens it when something is already wrong,
// and the question is always one of two: "what just happened on this
// machine", or "what exactly failed, so I can tell someone". Those are two
// different readers at the same moment, and the page answers them in two
// layers rather than averaging them into one:
//
//   * THE TABLE is the operator's. Fixed columns, one line per event, a
//     message written in plain words. It is meant to be SKIMMED - which is
//     why every row is the same height and a long message is truncated
//     rather than allowed to wrap and break the rhythm.
//   * THE DETAILS PANEL is the developer's, and it is collapsed. Exception,
//     stack trace, file, function, line, HTTP status, port, request id,
//     device, app version, metadata: everything with a colon in it lives
//     down there, and nothing developer-shaped is allowed in the Message
//     column.
//
// LIVE, NOT REFRESHED. There is no Refresh button: the ring is polled and
// the table follows. Pausing is the operator's control over that, and the
// one thing it must never do is lose lines quietly - so the poll keeps
// running while paused, the frozen view stays exactly as it was, and the bar
// above the table says how many records are being held back.
//
// FILTERING IS CLIENT-SIDE, on purpose. The whole visible ring is fetched
// once per poll and every filter is applied here. That keeps the paused view
// and the filters consistent (narrowing a filter while paused must not go to
// the server and unfreeze the page), and it means this page does not depend
// on which query parameters the log API happens to support while it is being
// rebuilt. If the ring ever outgrows a browser, the level/search/date
// filters are the three worth pushing back down to the server.
//
// THE TABLE ITSELF IS NOT LOCAL. The colgroup, the sticky head, the uniform
// rows, the row expander, the paused bar and the responsive collapse are
// components/LogTable.jsx, which the Events log and the Audit log wear too -
// the three Diagnostics pages are read by one person during one incident and
// they should not each teach their own furniture. What stays here is what
// only this page knows: its columns, its filters, its details renderer and
// its empty state. The level styling is likewise the shared fill / outline /
// dash system in components/LogLevel.jsx, measured in both themes and pinned
// by tests/check_loglevels.mjs.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client.js";
import { useConfirm } from "../components/ui/ask.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { ErrorNote, SkelTable } from "../components/Loading.jsx";
import { LogLevelTag, logRowClass } from "../components/LogLevel.jsx";
import LogTable, {
  Details, DetailBlock, ExportButtons, LiveButton, Picker, StatusWord,
} from "../components/LogTable.jsx";
import {
  clock, downloadCsv, downloadLog, isoDay, logLine, stampText, uniq,
} from "../components/logFormat.js";
import DateRangePicker from "../components/ui/DateRangePicker.jsx";

// Fast enough that the table reads as live, slow enough that a board running
// four cameras is not answering a log query every animation frame.
const POLL_MS = 2000;
// The server caps this at 1000. The ring itself holds more than that with
// debug capture on, which the footer says out loud.
const FETCH_LIMIT = 1000;

// Five levels, quietest first. SUCCESS and FATAL are deliberately absent:
// success/failed is what the Status column carries, and FATAL is another
// name for CRITICAL - a sixth and seventh level would be two more things to
// tell apart at a glance and no new information.
const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

const EMPTY_FILTERS = {
  level: "", category: "", event: "", module: "", user: "", status: "",
  text: "", start: "", end: "",
};

/** One API record -> the one shape this page renders.
 *
 *  Every field is optional. A log line that predates the new catalogue still
 *  has a time, a level, a logger and a message, and that is a readable row -
 *  the columns it cannot fill stay blank rather than showing "undefined".
 */
function normalise(raw) {
  const logger = raw.logger || "";
  // An empty metadata object is the common case, and `{}` rendered as a code
  // block is a panel that looks like it is telling you something.
  const bag = raw.metadata || raw.meta;
  return {
    // Keyed by IDENTITY, never by position. The ring drops lines off its far
    // end, so an index-based key would rename every row each time one
    // arrived - and the details panel somebody had open would jump to a
    // different event underneath them.
    //
    // source + run + seq is exact: seq counts within one process RUN, and the
    // detection app re-execs itself when the accelerator drops it, leaving one
    // spool file holding two runs. Without `run` the two collide precisely at
    // a crash, which is the moment anybody is reading this page. A writer that
    // stamps neither falls back to content, which is very nearly exact and is
    // the best that can be done for it.
    key: raw.seq
      ? `${raw.source || "?"}|${raw.run || "?"}|${raw.seq}`
      : `${raw.ts}|${raw.message}`,
    seq: raw.seq ?? null,
    ts: Number(raw.ts) || 0,
    level: String(raw.level || "INFO").toUpperCase(),
    category: raw.category || "",
    eventId: raw.event_id || "",
    eventCode: raw.event_code || "",
    action: raw.action || "",
    module: raw.module || logger.split(".").pop() || "",
    logger,
    func: raw.function || "",
    file: raw.file || "",
    line: raw.line ?? "",
    message: raw.message || "",
    detail: raw.detail || "",
    user: raw.user || "",
    session: raw.session || "",
    status: String(raw.status || "").toLowerCase(),
    origin: raw.source || "",
    requestId: raw.request_id || raw.rid || "",
    // Two spellings each, and both are read: the page was written against the
    // agreed record while the writer behind it was still being built, so a
    // field that landed as `version` rather than `app_version` shows up as
    // the App version either way instead of quietly reading blank.
    appVersion: raw.app_version || raw.version || "",
    device: raw.device || raw.host || "",
    httpStatus: raw.http_status ?? raw.status_code ?? "",
    port: raw.port ?? "",
    exception: raw.exception || "",
    metadata: bag && Object.keys(bag).length ? bag : null,
    traceback: raw.traceback || "",
  };
}

/** Sort key for "is this record newer than the one at the top of the frozen
 *  view".
 *
 *  The TIMESTAMP, never the sequence number, now that this page merges rows
 *  from several processes. `seq` counts within one process run, so it is not
 *  comparable across the hub, a master and an app - and mixing the two scales
 *  is worse than either: a seq of 42 against an epoch of 1.79e9 makes every
 *  sequence-stamped row look older than everything, so the paused counter
 *  would stop counting exactly the app records this page was rebuilt to show.
 *  `seq` stays the row's IDENTITY (with source and run); it is not an order. */
const stampOf = (row) => (row ? row.ts : 0);

const CSV_COLUMNS = [
  ["Time", (r) => stampText(r.ts)],
  ["Level", (r) => r.level],
  ["Category", (r) => r.category],
  ["Event ID", (r) => r.eventId],
  ["Event code", (r) => r.eventCode],
  ["Module", (r) => r.module],
  ["Message", (r) => r.message],
  ["User", (r) => r.user],
  ["Status", (r) => r.status],
  ["Source", (r) => r.origin || r.logger],
  ["Detail", (r) => r.detail],
  ["File", (r) => r.file],
  ["Function", (r) => r.func],
  ["Line", (r) => r.line],
  ["HTTP status", (r) => r.httpStatus],
  ["Port", (r) => r.port],
  ["Request id", (r) => r.requestId],
  ["Device", (r) => r.device],
  ["App version", (r) => r.appVersion],
  ["Session", (r) => r.session],
  ["Metadata", (r) => (r.metadata ? JSON.stringify(r.metadata) : "")],
  ["Exception", (r) => r.exception],
  ["Traceback", (r) => r.traceback],
];

/** The .log export: the line a support engineer expects to paste into a
 *  ticket, with the traceback indented under it the way a log file has it. */
function logText(rows) {
  return rows.map((r) => {
    const tag = [r.category, r.eventId].filter(Boolean).join("/");
    const where = [r.module, r.line].filter((v) => v !== "" && v !== null).join(":");
    const head = `${stampText(r.ts)} ${r.level.padEnd(8)} `
      + `${tag ? `[${tag}] ` : ""}${where ? `${where} ` : ""}- ${r.message}`;
    return logLine(head, [
      r.detail && `detail: ${r.detail}`,
      r.user && `user: ${r.user}${r.status ? ` (${r.status})` : ""}`,
      r.requestId && `request: ${r.requestId}`,
      r.traceback,
    ]);
  }).join("\n");
}

// The columns, in the order they are read and with the order they LEAVE in.
// Time, Level and Message never leave; the rest give way from the least often
// useful (the module) to the most (the status), and everything a dropped
// column held is in the row's details panel.
const COLUMNS = [
  { key: "time", label: "Time", width: 84, className: "lh-c-time", mono: true,
    cell: (r) => clock(r.ts), title: (r) => stampText(r.ts) },
  { key: "level", label: "Level", width: 120, className: "lh-c-level",
    cell: (r) => <LogLevelTag level={r.level} /> },
  { key: "category", label: "Category", width: 116, drop: 4, mono: true,
    cell: (r) => r.category || "-" },
  { key: "event", label: "Event ID", width: 132, drop: 3, mono: true,
    cell: (r) => r.eventId || "-" },
  { key: "module", label: "Module", width: 150, drop: 1, mono: true,
    cell: (r) => r.module || "-", title: (r) => r.logger },
  { key: "message", label: "Message", cell: (r) => r.message,
    title: (r) => r.message,
    sub: (r) => [r.category, r.eventId, r.module].filter(Boolean).join(" · ") },
  { key: "user", label: "User", width: 110, drop: 2, mono: true,
    cell: (r) => r.user || "-" },
  { key: "status", label: "Status", width: 92, drop: 5,
    cell: (r) => <StatusWord value={r.status} /> },
];

// WHOSE LOG THIS IS (Tok Aba, 2026-10-14): "each system must show logs for
// itself only ... the main (hub) log should show hub logs only". The server
// decides what reaches the page (core/routes/logs.py own_sources); `hub` only
// picks the words, set by the hub's shell, so the page never says "this
// system" over the hub's rows or the other way round.
export const scopeWord = (hub) => (hub ? "this hub" : "this system");

export default function Logs({ onPerformance, hub = false }) {
  const scope = scopeWord(hub);
  // `meta` is the envelope (summary, levels, capture); `view` is the list the
  // table is showing, which is NOT the newest fetch while paused.
  const [meta, setMeta] = useState(null);
  const [view, setView] = useState([]);
  const [error, setError] = useState(null);
  const [live, setLive] = useState(true);
  const [held, setHeld] = useState(0);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [sort, setSort] = useState("newest");
  const [busy, setBusy] = useState("");
  const confirm = useConfirm();
  const toast = useToast();

  // Read by the poll, which must not be torn down and rebuilt every time
  // somebody pauses or types in the search box.
  const liveRef = useRef(true);
  const newest = useRef(null);      // the last fetch, whether shown or not
  const frozenTop = useRef(0);      // the stamp at the top of the frozen view

  useEffect(() => { liveRef.current = live; }, [live]);

  // ONE poll for the life of the page. The URL never changes, because no
  // filter is sent to the server (see the header comment), so nothing here
  // restarts the interval or drops a line mid-flight.
  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const d = await api.get(`/api/logs?limit=${FETCH_LIMIT}`);
        if (!alive) return;
        const rows = (d.entries || []).map(normalise);
        newest.current = rows;
        setMeta(d);
        setError(null);
        if (liveRef.current) {
          setView(rows);
          setHeld(0);
        } else {
          // Counted against the stamp frozen at the moment of pausing, not
          // against a length: the ring drops old lines off the far end, so
          // "the list got longer" is not the same question.
          const top = frozenTop.current;
          setHeld(rows.filter((r) => stampOf(r) > top).length);
        }
      } catch (e) {
        if (alive) setError(e);
      }
    }
    tick();
    const id = setInterval(() => { if (!document.hidden) tick(); }, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const all = view;

  // The option lists come from the WHOLE buffer, not from the filtered rows:
  // a dropdown that loses its other choices the moment you use it cannot be
  // used to change your mind.
  const options = useMemo(() => ({
    category: uniq(all, (r) => r.category),
    event: uniq(all, (r) => r.eventId),
    module: uniq(all, (r) => r.module),
    user: uniq(all, (r) => r.user),
    status: uniq(all, (r) => r.status),
  }), [all]);

  const rows = useMemo(() => {
    const floor = LEVELS.indexOf(filters.level);
    const needle = filters.text.trim().toLowerCase();
    const out = all.filter((r) => {
      if (floor >= 0 && LEVELS.indexOf(r.level) < floor) return false;
      if (filters.category && r.category !== filters.category) return false;
      if (filters.event && r.eventId !== filters.event) return false;
      if (filters.module && r.module !== filters.module) return false;
      if (filters.user && r.user !== filters.user) return false;
      if (filters.status && r.status !== filters.status) return false;
      if (filters.start && isoDay(r.ts) < filters.start) return false;
      if (filters.end && isoDay(r.ts) > filters.end) return false;
      if (needle) {
        // The search box reaches the technical half too: a request id or an
        // exception name is exactly what somebody arrives here holding.
        const hay = [r.message, r.detail, r.eventId, r.eventCode, r.module,
                     r.user, r.requestId, r.exception, r.logger, r.traceback]
          .join(" ").toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    return sort === "oldest" ? out.slice().reverse() : out;
  }, [all, filters, sort]);

  function togglePause() {
    setLive((was) => {
      if (was) {
        // Freeze against what is on screen right now, so the count that
        // appears is the count of things this reader has not seen.
        frozenTop.current = stampOf(view[0]);
        setHeld(newest.current
          ? newest.current.filter((r) => stampOf(r) > frozenTop.current).length
          : 0);
      } else {
        if (newest.current) setView(newest.current);
        setHeld(0);
      }
      return !was;
    });
  }

  const set = (patch) => setFilters((f) => ({ ...f, ...patch }));
  const dirty = Object.keys(EMPTY_FILTERS)
    .some((k) => filters[k] !== EMPTY_FILTERS[k]);

  async function setCapture(next) {
    setBusy(next === "DEBUG" ? "Turning debug capture on…" : "Back to info…");
    try {
      await api.post("/api/logs/level", { level: next });
    } catch (e) {
      toast.error("Could not change the capture level", e.message);
    } finally { setBusy(""); }
  }

  async function clearRing() {
    if (!await confirm({ title: "Clear the log ring?",
                         description: "The lines are gone for good.",
                         destructive: true, confirmLabel: "Clear" })) return;
    setBusy("Clearing…");
    try {
      await api.post("/api/logs/clear", {});
      setView([]);
      newest.current = [];
      setHeld(0);
    } catch (e) {
      toast.error("Could not clear the log ring", e.message);
    } finally { setBusy(""); }
  }

  // Clear Log is NOT Clear ring. Clear ring empties the ring of the one
  // process answering; Clear Log empties every store this page reads (on a
  // system its master's ring, its app's spool and app.log's lines up to a
  // mark; on the hub the hub's ring only) and leaves a record saying who did
  // it. The Audit page is a separate store, keeps its rows and gains a
  // logs.clear entry. See core/routes/logs.py purge_logs.
  async function clearLog() {
    const held = (newest.current || all).length;
    const count = held >= FETCH_LIMIT ? `At least ${held}` : String(held);
    if (!await confirm({
      title: "Clear the whole log?",
      description: `${count} record${held === 1 ? "" : "s"} will be deleted `
        + (hub ? "from the hub's log only. Each system keeps its own log. "
               : "from this system's log. ")
        + "This cannot be undone. Use Export CSV or Export LOG first if you "
        + "may need them.",
      destructive: true, confirmLabel: "Clear Log" })) return;
    setBusy("Clearing the log…");
    try {
      const done = await api.post("/api/logs/purge", {});
      // Fetched now rather than on the next poll, and shown even while
      // paused: the person who pressed Clear Log expects to see it land.
      const d = await api.get(`/api/logs?limit=${FETCH_LIMIT}`);
      const fresh = (d.entries || []).map(normalise);
      newest.current = fresh;
      setMeta(d);
      setView(fresh);
      setHeld(0);
      frozenTop.current = stampOf(fresh[0]);
      if (done.unreached?.length) {
        toast.error("Some systems were not cleared",
                    `Not answering: ${done.unreached.join(", ")}`);
      }
    } catch (e) {
      toast.error("Could not clear the log", e.message);
    } finally { setBusy(""); }
  }

  // Both exports carry WHAT IS ON SCREEN, filters and sort order included.
  // An export that quietly hands over everything is an export somebody has
  // to filter a second time, by hand, in a spreadsheet.
  const exportCsv = () => downloadCsv("logs", CSV_COLUMNS, rows);
  const exportLog = () => downloadLog("logs", logText(rows));

  if (error && !meta) return <ErrorNote error={error} />;
  if (!meta) return <SkelTable rows={14} cols={6} />;

  const summary = meta.summary || {};
  const counts = summary.counts || {};
  const capture = meta.capture || summary.capture || "INFO";
  const debugOn = capture === "DEBUG";

  return (
    <section className="page active logtable">
      <div className="page-head">
        <div className="page-title">
          {/* The hub's own log is just "Logs"; a system's is "System Log". */}
          {hub ? "Logs" : "System Log"}
          <span>
            {summary.held ?? all.length} of {summary.capacity ?? 0} records held
            {" · capturing "}{String(capture).toLowerCase()} and worse
            {` · ${scope} only, since it last started`}
          </span>
        </div>
        <div className="lh-actions">
          <LiveButton live={live} onToggle={togglePause} />
          {onPerformance && (
            /* LINKED, not embedded, and not rebuilt: CPU, RAM, FPS, Hailo and
               temperature already have a page, and a second reading of the
               same numbers is a second reading that can disagree with the
               first. */
            <button type="button" className="btn" onClick={onPerformance}>
              Performance
            </button>
          )}
        </div>
      </div>

      <div className="panel-card">
        <div className="lh-toolbar">
          <div className="lh-field lh-search">
            <span className="lh-field-label">Search</span>
            <input className="input" type="search"
                   placeholder="message, request id, exception"
                   value={filters.text}
                   onChange={(e) => set({ text: e.target.value })} />
          </div>

          <Picker label="Level" value={filters.level} onChange={(v) => set({ level: v })}
                  all="All levels"
                  options={(meta.levels || LEVELS).map((l) => [l, `${l} and worse`])} />
          <Picker label="Category" value={filters.category} all="All categories"
                  onChange={(v) => set({ category: v })} options={options.category} />
          <Picker label="Event ID" value={filters.event} all="All events"
                  onChange={(v) => set({ event: v })} options={options.event} />
          <Picker label="Module" value={filters.module} all="All modules"
                  onChange={(v) => set({ module: v })} options={options.module} />
          <Picker label="User" value={filters.user} all="All users"
                  onChange={(v) => set({ user: v })} options={options.user} />
          <Picker label="Status" value={filters.status} all="Any status"
                  onChange={(v) => set({ status: v })} options={options.status} />

          <div className="lh-field">
            <span className="lh-field-label">Date range</span>
            <DateRangePicker start={filters.start} end={filters.end}
                             onApply={(r) => set({ start: r.start, end: r.end })} />
          </div>

          <Picker label="Sort" value={sort} onChange={setSort}
                  options={[["newest", "Newest first"], ["oldest", "Oldest first"]]} />
        </div>

        <div className="lh-actions" style={{ marginTop: 14 }}>
          <ExportButtons onCsv={exportCsv} onLog={exportLog} disabled={!rows.length} />
          <button type="button" className="btn btn-sm" onClick={() => setFilters(EMPTY_FILTERS)}
                  disabled={!dirty}>Clear filters</button>
          <span className="lh-sep" aria-hidden="true" />
          <label className="lh-note" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={debugOn} disabled={!!busy}
                   onChange={(e) => setCapture(e.target.checked ? "DEBUG" : "INFO")} />
            Capture debug
          </label>
          <button type="button" className="btn btn-sm" onClick={clearRing}
                  disabled={!!busy}>Clear ring</button>
          {meta.can_clear && (
            <button type="button" className="btn btn-sm" onClick={clearLog}
                    disabled={!!busy}>Clear Log</button>
          )}
          <span className="lh-note">{busy}</span>
          <span style={{ marginLeft: "auto" }} className="lh-note">
            {rows.length === all.length
              ? `${rows.length} records`
              : `${rows.length} of ${all.length} records`}
          </span>
        </div>
      </div>

      {/* The tally of the whole buffer, and the fastest filter on the page.
          It counts every record this page HOLDS - the same set the table is
          drawn from - and not the filtered rows,
          because a row of numbers that narrows with the filter agrees with
          itself and says nothing.

          It used to be the serving process's own ring, cumulative since that
          process started, which on the hub meant "0 CRITICAL" over a screen
          of critical app rows. The counts now come from the same set as the
          table (see core/routes/logs.py), so the strip and the rows cannot
          disagree. */}
      <div className="lh-strip" style={{ marginBottom: 12 }}>
        {LEVELS.slice().reverse().map((l) => (
          <button key={l} type="button"
                  className={"lh-stat" + (filters.level === l ? " is-on" : "")}
                  aria-pressed={filters.level === l}
                  /* The count is that level alone; the filter it sets is the
                     same threshold the Level box uses, so say so rather than
                     let the number and the result disagree silently. */
                  title={`Show ${l} and worse`}
                  onClick={() => set({ level: filters.level === l ? "" : l })}>
            <span className="lh-stat-n">{counts[l] || 0}</span>
            {l}
          </button>
        ))}
        {debugOn && (
          <span className="lh-note" style={{ marginLeft: 4 }}>
            Debug capture fills the ring far faster than the extra room it
            buys. Copy what you need, and switch back to Info when you are done.
          </span>
        )}
      </div>

      <LogTable
        columns={COLUMNS}
        rows={rows}
        rowKey={(r) => r.key}
        rowClass={(r) => logRowClass(r.level)}
        details={(r) => <LogDetails row={r} onCopied={() => toast.success("Copied")} />}
        paused={live ? null : { held, onResume: togglePause }}
        follow={live ? (sort === "oldest" ? "bottom" : "top") : null}
        isFiltered={all.length > 0}
        noMatch="No records match these filters."
        empty={`Nothing logged yet. This log holds only ${scope}'s `
               + "events, and only since it last started."}
      />

      <div className="lh-note" style={{ marginTop: 10 }}>
        Held in memory only: a restart empties this. Exports carry the rows
        currently on screen, in the order they are shown.
        {error ? " The last poll did not answer; the list may be behind." : ""}
      </div>
    </section>
  );
}

/** The collapsed half: everything with a colon in it. The panel's shell is
 *  shared (Details); what goes in it is this page's own record. */
function LogDetails({ row, onCopied }) {
  const facts = [
    ["Time", stampText(row.ts)],
    ["Event ID", row.eventId],
    ["Event code", row.eventCode],
    ["Category", row.category],
    ["Action", row.action],
    ["Source", row.origin],
    ["Module", row.module],
    ["Function", row.func],
    ["File", row.file],
    ["Line", row.line],
    ["HTTP status", row.httpStatus],
    ["Port", row.port],
    ["Request id", row.requestId],
    ["Device", row.device],
    ["App version", row.appVersion],
    ["User", row.user],
    ["Session", row.session],
    ["Status", row.status],
    ["Logger", row.logger],
  ];

  const asText = [
    `${stampText(row.ts)} ${row.level} ${row.message}`,
    ...facts.filter(([, v]) => v !== "" && v !== null && v !== undefined)
      .map(([k, v]) => `${k}: ${v}`),
    row.detail && `Detail: ${row.detail}`,
    row.metadata && `Metadata: ${JSON.stringify(row.metadata)}`,
    row.exception && `Exception: ${row.exception}`,
    row.traceback,
  ].filter(Boolean).join("\n");

  return (
    <Details facts={facts} text={asText} onCopied={onCopied}
             lead={row.detail ? (
               <>
                 <div className="lh-dt">Detail</div>
                 <p className="lh-dd">{row.detail}</p>
               </>
             ) : null}>
      {row.exception && (
        <>
          <div className="lh-dt" style={{ marginTop: 8 }}>Exception</div>
          <div className="lh-dd">{row.exception}</div>
        </>
      )}
      {row.metadata && (
        <DetailBlock>{JSON.stringify(row.metadata, null, 2)}</DetailBlock>
      )}
      {row.traceback && <DetailBlock>{row.traceback}</DetailBlock>}
    </Details>
  );
}
