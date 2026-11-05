// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Events log -- the detection event store (/api/events), as an alarm list.
//
// WHAT THIS PAGE IS FOR. The Log Hub answers "what did this software do".
// This one answers "what did the cameras SEE": a person in a restricted zone
// at 14:02, a count that crossed its limit, a plate that did not match. It is
// the same reader, on the same afternoon, usually with one of the two pages
// open to explain the other - so it wears the same table
// (components/LogTable.jsx), the same clock, the same export buttons and the
// same severity treatment. What is specific to events stays here: the
// columns, the filters, the details panel, and the link to the footage.
//
// SEVERITY IS THE SAME VOCABULARY, NOT A SECOND ONE. The event store writes
// info / warn / crit; the Log Hub writes five logging levels. Those are the
// same three degrees of "how much should this worry me", so the chip keeps
// the writer's own word and wears the shared fill / outline / dash treatment
// (severityLevel in components/LogLevel.jsx). A second colour scheme on the
// next page along is how "amber" comes to mean two things in one product.
//
// WHAT THE SERVER DOES AND DOES NOT DO. The event store is queryable, so the
// severity / type / zone / camera filters and the time window are pushed DOWN
// to it and the table is one page of the answer. It has no free-text
// parameter, so the search box filters the page already loaded and says so.
// If a text search over the whole store is wanted, that is a backend field
// (an ILIKE over label/notes, or a tsvector) and not something this page can
// invent.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import { ErrorNote, SkelTable } from "../components/Loading.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import NextPageButton from "../components/NextPageButton.jsx";
import ClearEventsButton from "../components/ClearEventsButton.jsx";
import DateRangePicker from "../components/ui/DateRangePicker.jsx";
import { LogLevelTag, logRowClass, severityLevel } from "../components/LogLevel.jsx";
import LogTable, {
  Details, DetailBlock, ExportButtons, Field, LiveButton, Picker, StatusWord,
} from "../components/LogTable.jsx";
import {
  asDate, clock, downloadCsv, downloadLog, logLine, stampText,
} from "../components/logFormat.js";
import { useApp } from "../state/app.jsx";
import { apiUrl } from "../lib/base.js";

const PAGE_SIZE = 50;
// How many rows an export pulls: the export is of the QUERY, not of the page
// on screen, because nobody exports 50 rows at a time to build a report.
const EXPORT_LIMIT = 5000;
const POLL_MS = 10000;

// Every attribute the event store exposes, with the name it is shown under.
// This is the labelling for the details panel and the export; the TABLE shows
// the seven below it, because a row that has to be read left to right is not
// a row that can be skimmed.
const ATTRS = [
  ["ts", "Time"],
  ["event_id", "Event ID"],
  ["type", "Event type"],
  ["severity", "Severity"],
  ["label", "Event"],
  ["camera", "Camera"],
  ["zone", "Zone"],
  ["detection_label", "Object class"],
  ["confidence", "Confidence"],
  ["track_id", "Tracking ID"],
  ["person_id", "Person ID"],
  ["object_id", "Object ID"],
  ["direction", "Direction"],
  ["status", "Status"],
  ["notes", "Notes"],
  ["model", "AI model"],
  ["host_serial", "Device"],
  ["device_label", "Device name"],
  ["snapshot_id", "Snapshot"],
];

// The dropdowns, and the order they are read in. /api/events/attributes ships
// the distinct values for each.
const FILTER_COLS = [
  ["severity", "Severity", "Any severity"],
  ["type", "Type", "All types"],
  ["zone", "Zone", "All zones"],
  ["camera", "Camera", "All cameras"],
];

const EMPTY_FILTERS = {
  severity: "", type: "", zone: "", camera: "", text: "", start: "", end: "",
};

const text = (v) => (v === null || v === undefined || v === "" ? "" : String(v));
const cell = (v) => text(v) || "-";
const pct = (v) => (v === null || v === undefined || v === ""
  ? "" : `${Math.round(Number(v) * 100)}%`);

/** Where the still that goes with an event lives. Kept as a function because
 *  it is used twice - once in the row, once in the details panel - and a
 *  second copy of a URL is a second thing to forget to change. */
const footageHref = (row) =>
  apiUrl(`/footage/${encodeURIComponent(row.host_serial || "")}`)
  + `/${encodeURIComponent(row.snapshot_id)}`;

/** A whole day, in the ISO the query wants. The picker gives local calendar
 *  days; sending them as midnight-to-midnight LOCAL is what makes "today"
 *  mean today on a board that is not on UTC. */
const dayStart = (d) => (d ? new Date(`${d}T00:00:00`).toISOString() : "");
const dayEnd = (d) => (d ? new Date(`${d}T23:59:59.999`).toISOString() : "");

const CSV_COLUMNS = ATTRS.map(([key, label]) => [
  label,
  key === "ts" ? (r) => stampText(r.ts)
  : key === "confidence" ? (r) => pct(r.confidence)
  : (r) => text(r[key]),
]);

/** The .log export: one line a support engineer can paste into a ticket,
 *  with the rest of the record indented under it. */
function logText(rows) {
  return rows.map((r) => {
    const tag = [r.type, r.zone].filter(Boolean).join("/");
    const head = `${stampText(r.ts)} ${String(r.severity || "info").toUpperCase().padEnd(8)} `
      + `${tag ? `[${tag}] ` : ""}${r.camera ? `${r.camera} ` : ""}- `
      + `${r.label || r.detection_label || r.type || "event"}`;
    return logLine(head, [
      r.detection_label && `object: ${r.detection_label}`
        + (r.confidence ? ` (${pct(r.confidence)})` : ""),
      r.status && `status: ${r.status}`,
      r.notes && `notes: ${r.notes}`,
      r.host_serial && `device: ${r.host_serial}`,
      r.event_id && `event id: ${r.event_id}`,
      r.snapshot_id && `snapshot: ${r.snapshot_id}`,
    ]);
  }).join("\n");
}

// Time, Severity and the event itself never leave; Zone goes first because a
// camera name usually implies its zone, then Camera, then Type - and every
// one of them is still in the row's details panel.
const COLUMNS = [
  { key: "ts", label: "Time", width: 84, className: "lh-c-time", mono: true,
    cell: (r) => clock(r.ts), title: (r) => stampText(r.ts) },
  { key: "severity", label: "Severity", width: 120, className: "lh-c-level",
    cell: (r) => (r.severity
      ? <LogLevelTag level={severityLevel(r.severity)} text={r.severity} />
      : <LogLevelTag level="INFO" text="info" />) },
  { key: "type", label: "Type", width: 140, drop: 3, mono: true,
    cell: (r) => cell(r.type) },
  { key: "zone", label: "Zone", width: 130, drop: 1, mono: true,
    cell: (r) => cell(r.zone) },
  { key: "camera", label: "Camera", width: 140, drop: 2, mono: true,
    cell: (r) => cell(r.camera) },
  { key: "label", label: "Event",
    title: (r) => text(r.label) || text(r.detection_label),
    cell: (r) => (
      <>
        {text(r.label) || text(r.detection_label) || text(r.type) || "-"}
        {r.snapshot_id ? (
          /* The one interactive thing inside a row that is itself a button.
             The click must not also open the details panel underneath it. */
          <a className="lh-link" href={footageHref(r)} target="_blank"
             rel="noreferrer" onClick={(e) => e.stopPropagation()}>footage</a>
        ) : null}
      </>
    ),
    sub: (r) => [r.type, r.zone, r.camera].filter(Boolean).join(" · ") },
  { key: "status", label: "Status", width: 92, drop: 5,
    cell: (r) => <StatusWord value={r.status || "open"} /> },
];

export default function EventTable() {
  const toast = useToast();
  const { system } = useApp();

  const [attrs, setAttrs] = useState(null);
  const [view, setView] = useState([]);
  const [total, setTotal] = useState(0);
  // The server stops counting past a cap rather than reading a year of
  // rows for an exact total nobody scrolls to. Say "20000+", never a
  // number that looks precise and is not.
  const [capped, setCapped] = useState(false);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [canClear, setCanClear] = useState(false);

  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(0);
  const [live, setLive] = useState(true);
  const [held, setHeld] = useState(0);

  const liveRef = useRef(true);
  const newest = useRef(null);      // the last fetch, whether shown or not
  const frozenTop = useRef(0);      // the stamp at the top of the frozen view

  useEffect(() => { liveRef.current = live; }, [live]);

  const loadAttrs = useCallback(() => {
    api.get("/api/events/attributes")
      .then((d) => setAttrs(d.attributes || {}))
      .catch(() => setAttrs({}));
  }, []);
  useEffect(loadAttrs, [loadAttrs]);

  const query = useCallback((extra) => {
    const p = new URLSearchParams();
    FILTER_COLS.forEach(([col]) => { if (filters[col]) p.append(col, filters[col]); });
    if (filters.start) p.set("from", dayStart(filters.start));
    if (filters.end) p.set("to", dayEnd(filters.end));
    p.set("order", "ts");
    p.set("dir", sort === "oldest" ? "asc" : "desc");
    Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v));
    return p;
  }, [filters, sort]);

  const load = useCallback(() => {
    setLoading(true);
    const p = query({ limit: PAGE_SIZE, offset: page * PAGE_SIZE });
    api.get(`/api/events?${p}`)
      .then((d) => {
        const rows = d.rows || [];
        newest.current = rows;
        setTotal(d.total || 0);
        setCapped(!!d.capped);
        setCanClear(!!d.can_clear);
        setError(null);
        // While paused the newest fetch is counted, never shown. Counted
        // against the stamp frozen when the reader paused, so the number is
        // the number of things THEY have not seen.
        if (liveRef.current) {
          setView(rows);
          setHeld(0);
        } else {
          const top = frozenTop.current;
          setHeld(rows.filter((r) => stampMs(r) > top).length);
        }
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [query, page]);
  useEffect(load, [load]);

  // Auto-refresh the first page so new events appear without a manual reload.
  // Visibility-aware: each refresh queries a table that grows for a year, and
  // a hidden tab was paying that every ten seconds for pixels nobody was
  // looking at. Page 2 is history and does not move.
  useVisibleInterval(() => { if (page === 0) load(); }, POLL_MS);

  // The search box reaches only what is loaded, because the store has no text
  // parameter. Everything else is already narrowed by the server.
  const rows = useMemo(() => {
    const needle = filters.text.trim().toLowerCase();
    if (!needle) return view;
    return view.filter((r) => ATTRS
      .map(([k]) => text(r[k])).join(" ").toLowerCase().includes(needle));
  }, [view, filters.text]);

  function togglePause() {
    setLive((was) => {
      if (was) {
        frozenTop.current = stampMs(view[0]);
        setHeld(newest.current
          ? newest.current.filter((r) => stampMs(r) > frozenTop.current).length
          : 0);
      } else {
        if (newest.current) setView(newest.current);
        setHeld(0);
      }
      return !was;
    });
  }

  const set = (patch) => { setFilters((f) => ({ ...f, ...patch })); setPage(0); };
  const dirty = Object.keys(EMPTY_FILTERS)
    .some((k) => filters[k] !== EMPTY_FILTERS[k]);

  /** Both exports carry the whole QUERY, not the page on screen: the filters
   *  and the window are the reader's choice, the page number is not. */
  async function pull() {
    const d = await api.get(`/api/events?${query({ limit: EXPORT_LIMIT })}`);
    return d.rows || [];
  }
  async function exportCsv() {
    try {
      const got = await pull();
      downloadCsv(`events-${system?.slug || "system"}`, CSV_COLUMNS, got);
      toast.success(`Exported ${got.length} events`);
    } catch (e) { toast.error("Export failed", e.message); }
  }
  async function exportLog() {
    try {
      const got = await pull();
      downloadLog(`events-${system?.slug || "system"}`, logText(got));
      toast.success(`Exported ${got.length} events`);
    } catch (e) { toast.error("Export failed", e.message); }
  }

  // Clear events emptied the store: show it at once, then ask again.
  function cleared() {
    newest.current = [];
    setView([]); setTotal(0); setCapped(false); setHeld(0); setPage(0);
    loadAttrs();
    load();
  }

  if (error && !view.length) return <ErrorNote error={error} />;

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="page active logtable">
      <div className="page-head">
        <div className="page-title">
          Events Log
          <span>
            {/* "recorded" is the whole log; once a filter or a time window
                narrows it, the number is how many MATCH. */}
            {total}{capped ? "+" : ""} event{total === 1 ? "" : "s"}
            {dirty ? " match" : " recorded"}
            {" · See what the cameras captured, stored according to your "
             + "retention settings"}
          </span>
        </div>
        <div className="lh-actions">
          <LiveButton live={live} onToggle={togglePause} />
          <NextPageButton />
        </div>
      </div>

      <div className="panel-card">
        <div className="lh-toolbar">
          <Field label="Search" wide value={filters.text}
                 placeholder="event, object class, note"
                 onChange={(v) => set({ text: v })} />
          {FILTER_COLS.map(([col, label, all]) => (
            <Picker key={col} label={label} all={all} value={filters[col]}
                    onChange={(v) => set({ [col]: v })}
                    options={attrs?.[col] || []} />
          ))}
          <div className="lh-field">
            <span className="lh-field-label">Date range</span>
            <DateRangePicker start={filters.start} end={filters.end}
                             onApply={(r) => set({ start: r.start, end: r.end })} />
          </div>
          <Picker label="Sort" value={sort}
                  onChange={(v) => { setSort(v); setPage(0); }}
                  options={[["newest", "Newest first"], ["oldest", "Oldest first"]]} />
        </div>

        <div className="lh-actions" style={{ marginTop: 14 }}>
          <ExportButtons onCsv={exportCsv} onLog={exportLog} disabled={!rows.length} />
          <button type="button" className="btn btn-sm" disabled={!dirty}
                  onClick={() => { setFilters(EMPTY_FILTERS); setPage(0); }}>
            Clear filters
          </button>
          <ClearEventsButton allowed={canClear} total={total} capped={capped}
                             dirty={dirty} onCleared={cleared} />
          <span className="lh-sep" aria-hidden="true" />
          <span className="lh-note">
            {filters.text
              ? `${rows.length} of ${view.length} on this page match the search`
              : `page ${page + 1} of ${pages}`}
          </span>
          <span style={{ marginLeft: "auto" }} className="lh-note">
            {total}{capped ? "+" : ""} event{total === 1 ? "" : "s"}
          </span>
        </div>
      </div>

      {loading && !view.length ? <SkelTable rows={10} cols={6} /> : (
        <LogTable
          columns={COLUMNS}
          rows={rows}
          rowKey={(r) => r.event_id || `${r.ts}|${r.label}`}
          rowClass={(r) => logRowClass(severityLevel(r.severity))}
          details={(r) => <EventDetails row={r} onCopied={() => toast.success("Copied")} />}
          paused={live ? null : { held, onResume: togglePause }}
          follow={live ? (sort === "oldest" ? "bottom" : "top") : null}
          isFiltered={dirty || page > 0}
          noMatch="No events match these filters in this window."
          empty={"No events recorded yet. Detection writes one every time a "
                 + "rule fires - a zone entry, a count over its limit, a "
                 + "match - and keeping them needs the Postgres store."}
          footer={(
            <div className="lh-pager">
              <span className="lh-note">
                {rows.length
                  ? `${page * PAGE_SIZE + 1}–${page * PAGE_SIZE + rows.length}`
                  : "none"} of {total}{capped ? "+" : ""}
              </span>
              <button type="button" className="btn btn-sm" disabled={page === 0}
                      onClick={() => setPage((p) => p - 1)}>Previous</button>
              {/* Past the count cap ("20000+") the total is "at least", so
                  there is a next page while a full one came back. */}
              <button type="button" className="btn btn-sm"
                      disabled={capped ? rows.length < PAGE_SIZE : page + 1 >= pages}
                      onClick={() => setPage((p) => p + 1)}>Next</button>
            </div>
          )}
        />
      )}

      <div className="lh-note" style={{ marginTop: 10 }}>
        The filters and the date window are asked of the event store; the
        search box narrows the page already loaded.
        {error ? " The last refresh did not answer; the list may be behind." : ""}
      </div>
    </section>
  );
}

/** Newest-first comparison for the paused counter. The store answers in ISO,
 *  so the string is parsed once rather than compared as text - "9" sorts
 *  after "10" and a paused counter that is wrong is worse than none. */
function stampMs(row) {
  const d = row ? asDate(row.ts) : null;
  return d ? d.getTime() : 0;
}

/** Every field the event carries, then the raw record. The raw JSON is there
 *  because this store gains columns as new AI templates land, and a panel
 *  that only shows the fields this page was written against is a panel that
 *  silently hides the new one. */
function EventDetails({ row, onCopied }) {
  const facts = ATTRS.map(([key, label]) => [
    label,
    key === "ts" ? stampText(row.ts)
    : key === "confidence" ? pct(row.confidence)
    : text(row[key]),
  ]);
  const asText = [
    `${stampText(row.ts)} ${row.severity || "info"} `
      + `${row.label || row.detection_label || row.type || ""}`.trim(),
    ...facts.filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`),
    JSON.stringify(row, null, 2),
  ].join("\n");

  return (
    <Details title="Event details" facts={facts} text={asText} onCopied={onCopied}
             lead={row.snapshot_id ? (
               <>
                 <div className="lh-dt">Footage</div>
                 <p className="lh-dd">
                   <a className="lh-link" href={footageHref(row)} target="_blank"
                      rel="noreferrer">Open the snapshot for this event</a>
                 </p>
               </>
             ) : null}>
      <DetailBlock label="Raw record">{JSON.stringify(row, null, 2)}</DetailBlock>
    </Details>
  );
}
