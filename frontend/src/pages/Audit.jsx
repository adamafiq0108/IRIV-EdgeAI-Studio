// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Audit log -- who did what, and what it did.
//
// WHAT THIS PAGE IS FOR. It is read exactly once, after the fact, by someone
// who was not there. That shapes everything on it:
//
//   * APPEND-ONLY HISTORY. Nothing on this page edits, clears or hides a
//     row. There is no Clear button the way the Log Hub has one, because a
//     log a person can empty is not evidence of anything.
//   * ADMIN ONLY, and the API enforces it (core/routes/audit.py). A
//     developer account's rows are filtered out server-side for everybody
//     but a developer, so this page never claims they are a deployment's own
//     accounts.
//   * IT SURVIVES A RESTART. The Log Hub's ring does not. When the two
//     disagree about whether something happened, this is the one that knows.
//
// The header line says all three out loud, because "User Logs" over a grid
// of timestamps told a reader none of it.
//
// THE FIX THIS PAGE EXISTS FOR. /api/audit has always returned `detail` --
// which setting, which device, whose session -- and the page drew six columns
// that did not include it. So it showed "device.rename" and not what it was
// renamed to. It is in the Message column now, through the SHARED renderer
// (components/auditDetail.js): detail is a JSONB column, so it arrives as an
// object and is `{}` for most actions, which is truthy - rendering it
// straight took the Accounts page down once already with "objects are not
// valid as a React child". One helper, two viewers, no second chance to meet
// that crash. Pinned by tests/smoke_auditdetail.py.
//
// PAGING. The server has always paged (limit / offset / has_more) and the
// page had no offset, so an admin could only ever read the newest 200 rows of
// a log a deployment keeps for a year. Previous / Next and a size picker now,
// the same shape the Accounts > Activity panel uses.
//
// The table is the shared one (components/LogTable.jsx) so this page, the
// Events log and the Log Hub read as one product.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client.js";
import { ErrorNote, SkelTable } from "../components/Loading.jsx";
import { detailText } from "../components/auditDetail.js";
import { logRowClass } from "../components/LogLevel.jsx";
import LogTable, {
  Details, DetailBlock, ExportButtons, Field, Picker, StatusWord,
} from "../components/LogTable.jsx";
import {
  asDate, clock, downloadCsv, downloadLog, isoDay, logLine, stampText, uniq,
} from "../components/logFormat.js";
import DateRangePicker from "../components/ui/DateRangePicker.jsx";

// The server clamps at 1000; none of these asks for more than it will give.
const SIZES = [100, 200, 500, 1000];

const EMPTY_FILTERS = {
  action: "", username: "", result: "", text: "", start: "", end: "",
};

const text = (v) => (v === null || v === undefined || v === "" ? "" : String(v));
const cell = (v) => text(v) || "-";

/** A row that did not succeed is the one somebody came here looking for, so
 *  it is the one thing on the page that carries a level treatment: three
 *  failed sign-ins in a row should be visible without reading the Result
 *  column. Anything else is a quiet row. */
const rowLevel = (row) => {
  const r = String(row.result || "").toLowerCase();
  return r && r !== "ok" && r !== "success" ? "ERROR" : "";
};

const CSV_COLUMNS = [
  ["Time", (r) => stampText(r.ts)],
  ["Result", (r) => r.result],
  ["Action", (r) => r.action],
  ["User", (r) => r.username],
  ["Device", (r) => r.host_serial],
  ["Message", (r) => detailText(r.detail)],
  ["IP", (r) => r.source_ip],
];

/** The .log export: one line per entry, in the order they are shown. */
function logText(rows) {
  return rows.map((r) => {
    const head = `${stampText(r.ts)} ${String(r.result || "ok").toUpperCase().padEnd(8)} `
      + `[${r.action || "?"}] ${r.username || "-"} - `
      + `${detailText(r.detail) || "no detail recorded"}`;
    return logLine(head, [
      r.host_serial && `device: ${r.host_serial}`,
      r.source_ip && `from: ${r.source_ip}`,
    ]);
  }).join("\n");
}

// Time, Result, Action and the Message never leave. The IP goes first (it is
// the fact a reader checks last), then the device, then the user - and all
// three are still in the row's details panel.
const COLUMNS = [
  { key: "ts", label: "Time", width: 84, className: "lh-c-time", mono: true,
    cell: (r) => clock(r.ts), title: (r) => stampText(r.ts) },
  { key: "result", label: "Result", width: 92, className: "lh-c-level",
    cell: (r) => <StatusWord value={r.result || "ok"} /> },
  { key: "action", label: "Action", width: 176, mono: true,
    cell: (r) => cell(r.action) },
  { key: "username", label: "User", width: 130, drop: 3, mono: true,
    cell: (r) => cell(r.username) },
  { key: "host_serial", label: "Device", width: 140, drop: 2, mono: true,
    cell: (r) => cell(r.host_serial) },
  { key: "detail", label: "Message",
    // THE fix: the one column that says what the action actually did, and it
    // goes through the shared helper because detail is an object.
    cell: (r) => detailText(r.detail) || "-",
    title: (r) => detailText(r.detail),
    sub: (r) => [r.username, r.host_serial, r.source_ip].filter(Boolean).join(" · ") },
  { key: "source_ip", label: "IP", width: 130, drop: 1, mono: true,
    cell: (r) => cell(r.source_ip) },
];

export default function Audit() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [size, setSize] = useState(SIZES[0]);
  const [page, setPage] = useState(0);

  // A different action, account or page size starts at the top again: page 3
  // of the old query is not page 3 of the new one.
  useEffect(() => { setPage(0); }, [filters.action, filters.username, size]);

  const load = useCallback(() => {
    setLoading(true);
    // Only what the API actually takes. `result`, the date window and the
    // search are applied below, in the browser, over the page that came back
    // -- /api/audit has no parameter for any of the three, and inventing one
    // is a backend change, not a page change.
    const p = new URLSearchParams();
    if (filters.action) p.set("action", filters.action);
    if (filters.username) p.set("username", filters.username);
    p.set("limit", String(size));
    p.set("offset", String(page * size));
    api.get(`/api/audit?${p}`)
      .then((d) => { setData(d); setError(null); })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [filters.action, filters.username, size, page]);
  useEffect(load, [load]);

  const entries = useMemo(() => data?.entries || [], [data]);

  const rows = useMemo(() => {
    const needle = filters.text.trim().toLowerCase();
    return entries.filter((e) => {
      const result = String(e.result || "ok").toLowerCase();
      if (filters.result && result !== filters.result) return false;
      const day = isoDay(e.ts);
      if (filters.start && day && day < filters.start) return false;
      if (filters.end && day && day > filters.end) return false;
      if (needle) {
        const hay = [e.action, e.username, e.host_serial, e.source_ip,
                     e.result, detailText(e.detail)].join(" ").toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [entries, filters]);

  const results = useMemo(
    () => uniq(entries, (e) => String(e.result || "ok").toLowerCase()), [entries]);

  const set = (patch) => setFilters((f) => ({ ...f, ...patch }));
  const dirty = Object.keys(EMPTY_FILTERS)
    .some((k) => filters[k] !== EMPTY_FILTERS[k]);

  const exportCsv = () => downloadCsv("audit", CSV_COLUMNS, rows);
  const exportLog = () => downloadLog("audit", logText(rows));

  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return <SkelTable rows={10} cols={6} />;

  const from = page * size + 1;
  const hasMore = !!data.has_more;

  return (
    <section className="page active logtable">
      <div className="page-head">
        <div className="page-title">
          Audit Log
          <span>
            Who did what on this system, kept for good
            {" · admin only · append-only: nothing here can be edited or "}
            cleared, and a restart does not empty it
          </span>
        </div>
      </div>

      <div className="panel-card">
        <div className="lh-toolbar">
          <Field label="Search" wide value={filters.text}
                 placeholder="action, message, device, ip"
                 onChange={(v) => set({ text: v })} />
          {/* The action list comes from the server (audit_service.ACTIONS).
              It is known to be incomplete today - several actions the code
              writes are not in it, so they cannot be chosen here; the search
              box reaches them. Filling the list is core/services/audit.py's
              job, not this page's. */}
          <Picker label="Action" value={filters.action} all="All actions"
                  onChange={(v) => set({ action: v })}
                  options={data.actions || []} />
          <Field label="User" type="text" value={filters.username}
                 placeholder="exact username"
                 onChange={(v) => set({ username: v })} />
          <Picker label="Result" value={filters.result} all="Any result"
                  onChange={(v) => set({ result: v })} options={results} />
          <div className="lh-field">
            <span className="lh-field-label">Date range</span>
            <DateRangePicker start={filters.start} end={filters.end}
                             onApply={(r) => set({ start: r.start, end: r.end })} />
          </div>
          <Picker label="Page size" value={String(size)}
                  onChange={(v) => setSize(Number(v))}
                  options={SIZES.map((n) => [String(n), `${n} per page`])} />
        </div>

        <div className="lh-actions" style={{ marginTop: 14 }}>
          <ExportButtons onCsv={exportCsv} onLog={exportLog} disabled={!rows.length} />
          <button type="button" className="btn btn-sm" disabled={!dirty}
                  onClick={() => setFilters(EMPTY_FILTERS)}>Clear filters</button>
          <span className="lh-sep" aria-hidden="true" />
          <span className="lh-note">{loading ? "Loading…" : ""}</span>
          <span style={{ marginLeft: "auto" }} className="lh-note">
            {rows.length === entries.length
              ? `${rows.length} entries on this page`
              : `${rows.length} of ${entries.length} on this page`}
          </span>
        </div>
      </div>

      <LogTable
        columns={COLUMNS}
        rows={rows}
        /* The row id from the table, never the array index: this list re-pages
           under the reader, and an index key would move an open details panel
           onto a different entry. */
        rowKey={(r) => String(r.id ?? `${r.ts}|${r.action}|${r.username}`)}
        rowClass={(r) => logRowClass(rowLevel(r))}
        details={(r) => <AuditDetails row={r} />}
        isFiltered={dirty || page > 0}
        noMatch="No entries match these filters on this page."
        empty={"Nothing audited yet. This log records sign-ins and the "
               + "changes people make to this system - accounts, devices, "
               + "settings - and it fills as the system is used."}
        footer={(
          <div className="lh-pager">
            {/* Which rows these are, not "page 1 of ?": there is no total,
                because counting an append-only log on every page turn buys a
                number nobody acts on. */}
            <span className="lh-note">
              {rows.length ? `${from}–${from + rows.length - 1}` : "none"}
              {" on this page"}
            </span>
            <button type="button" className="btn btn-sm" disabled={!page}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}>
              Previous
            </button>
            <button type="button" className="btn btn-sm" disabled={!hasMore}
                    onClick={() => setPage((p) => p + 1)}>Next</button>
          </div>
        )}
      />

      <div className="lh-note" style={{ marginTop: 10 }}>
        Action and user are asked of the server; result, the date window and
        the search narrow the page already loaded.
        {error ? " The last query did not answer; this page may be behind." : ""}
      </div>
    </section>
  );
}

/** One entry in full. Short and deliberately so: an audit row has seven
 *  fields and six of them are in the table - the panel is here for the
 *  detail that does not fit on one line, and for the exact stamp. */
function AuditDetails({ row }) {
  const message = detailText(row.detail);
  const facts = [
    ["Time", stampText(row.ts) || text(row.ts)],
    ["Action", row.action],
    ["Result", row.result],
    ["User", row.username],
    ["Device", row.host_serial],
    ["IP", row.source_ip],
    ["Entry", row.id],
  ];
  const stamp = asDate(row.ts);
  return (
    <Details title="Entry" facts={facts}
             text={[stampText(row.ts), row.action, row.username, row.result,
                    message, row.source_ip].filter(Boolean).join(" · ")}
             lead={message ? (
               <>
                 <div className="lh-dt">Message</div>
                 <p className="lh-dd">{message}</p>
               </>
             ) : null}>
      {/* The raw value, for an action whose detail is a nested object the one
          line above had to flatten. Absent when there is nothing in it. */}
      {row.detail && typeof row.detail === "object"
        && Object.keys(row.detail).length ? (
          <DetailBlock label="Recorded detail">
            {JSON.stringify(row.detail, null, 2)}
          </DetailBlock>
        ) : null}
      {!stamp ? (
        <p className="lh-dd">Recorded as: {text(row.ts)}</p>
      ) : null}
    </Details>
  );
}
