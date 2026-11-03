// Formatting and export for the three Diagnostics log pages.
//
// Its own module with NO imports, the way auditDetail.js is, and for the same
// reason: the component these used to live in pulls in React and half the UI
// kit, none of which a date formatter needs, and a helper that cannot be
// imported by a plain `node` script is a helper nothing tests
// (tests/check_logtable.mjs imports this file directly).
//
// Everything here was lifted verbatim out of pages/Logs.jsx when the Log Hub
// table became the shared one. The Events log and the Audit log render the
// same clock, quote CSV the same way and name their downloads the same way,
// because three Diagnostics pages that disagree about what a timestamp looks
// like read as three products.

const pad = (n) => String(n).padStart(2, "0");

/** A timestamp as this repo's log pages take it: seconds since the epoch, or
 *  an ISO string (the audit and event stores both answer in ISO), or a Date.
 *  Returns null for anything unreadable, so a caller can print "-" rather
 *  than "Invalid Date". */
export function asDate(ts) {
  if (ts === null || ts === undefined || ts === "") return null;
  if (ts instanceof Date) return Number.isNaN(ts.getTime()) ? null : ts;
  // A number, or a string of digits, is epoch SECONDS on every writer in this
  // repo. Milliseconds would put every row in 1970, which is visible; the
  // reverse (seconds read as ms) lands in the year 56000 and is not.
  const n = typeof ts === "number" ? ts
          : /^\d+(\.\d+)?$/.test(String(ts).trim()) ? Number(ts) : NaN;
  const d = Number.isNaN(n) ? new Date(String(ts)) : new Date(n * 1000);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** HH:MM:SS, 24-hour. The milliseconds are in the details panel: three more
 *  digits on every row is three more digits to read past 500 times. */
export function clock(ts) {
  const d = asDate(ts);
  if (!d) return "-";
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** The full stamp, for the details panel and both exports. */
export function stampText(ts) {
  const d = asDate(ts);
  if (!d) return "";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
       + `${clock(ts)}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

/** Local YYYY-MM-DD. toISOString() would give the UTC day, so anywhere behind
 *  UTC an early-morning "today" filter comes back empty. */
export function isoDay(ts) {
  const d = asDate(ts);
  if (!d) return "";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The distinct values of one field, for a filter dropdown. */
export const uniq = (rows, pick) =>
  [...new Set(rows.map(pick).filter(Boolean))].sort();

/** CSV that Excel opens without mangling: quotes doubled, and (with the BOM
 *  that toCsv adds) the UTF-8 in a device name survives the double-click. */
export const csvCell = (v) => {
  const s = v === null || v === undefined ? "" : String(v);
  return /["\n,]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** [label, pick] pairs + rows -> one CSV document, BOM and CRLF included. */
export function toCsv(columns, rows) {
  const head = columns.map(([label]) => csvCell(label)).join(",");
  const body = rows.map((r) => columns.map(([, pick]) => csvCell(pick(r))).join(","));
  return "﻿" + [head, ...body].join("\r\n");
}

/** The suffix on every export: YYYYMMDD-HHMMSS, so a folder of them sorts. */
export const fileStamp = () =>
  stampText(Date.now() / 1000).replace(/[-: ]/g, "").slice(0, 15);

export function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  // Revoked on a timer rather than immediately: Safari has not finished
  // reading the blob when click() returns.
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function downloadCsv(stem, columns, rows) {
  download(`${stem}-${fileStamp()}.csv`, toCsv(columns, rows),
           "text/csv;charset=utf-8");
}

export function downloadLog(stem, text) {
  download(`${stem}-${fileStamp()}.log`, text + "\n", "text/plain;charset=utf-8");
}

/** One .log line: the shape a support engineer expects to paste into a
 *  ticket. `head` is the one line; `extra` the indented facts under it, the
 *  way a log file carries a traceback. */
export function logLine(head, extra) {
  // EVERY line of an extra is indented, not just its first: a traceback is
  // one "extra" and half of it hanging in column 0 reads as the next record.
  return [head, ...extra.filter(Boolean)
    .map((s) => String(s).replace(/^/gm, "    "))].join("\n");
}
