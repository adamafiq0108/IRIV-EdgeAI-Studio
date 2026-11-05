// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// "Download CSV" for one table card (Tok Aba, 2026-10-14): the button at the
// card's top right opens this. The person picks a date range and which of the
// card's columns; the server reads the rows again for that range (POST
// /api/builder/table-csv, services/table_export.py) and checks every choice
// against the card itself. Replaces the Analytics page's CSV button, which
// saved only what the browser was showing.
//
// Layout (Tok Aba's "improve this", same day): a line naming the card under
// the title; the date range as a row of chips (a real radio group: arrow keys
// move and choose, one Tab stop); Custom range shows its dates as one button
// ("Pick dates" until chosen) rather than a separate warning; the columns as
// a two-column grid of large click targets with a live "N of M" counter and
// Select all / Clear as text buttons; the row cap in plain words; a summary
// line ("Last 7 days, 5 columns") over Cancel and Download. Nothing the server
// is sent changed (csvRequest).
import { useRef, useState } from "react";
import { Check, Download, Loader2 } from "lucide-react";
import { api, friendlyError, saveBlob } from "../../api/client.js";
import { apiUrl } from "../../lib/base.js";
import { Dialog } from "../ui/primitives.jsx";
import DateRangePicker from "../ui/DateRangePicker.jsx";

//: Mirrors table_export.MAX_SPAN_DAYS. Every row in the range is downloaded
//: (the server's own ceiling is a safety limit, not a promise to cut at 10,000);
//: past this many the file takes a while to prepare, and the dialog says so.
export const CSV_SLOW_ROWS = 10000;
const MAX_SPAN_DAYS = 366;

//: The quick ranges; "page" only where the page has a range of its own.
export const CSV_RANGES = [
  ["today", "Today"], ["7d", "Last 7 days"], ["30d", "Last 30 days"],
  ["page", "This page's range"], ["all", "All time"], ["custom", "Custom range"],
];

/** The request body for a choice (what the server is sent). */
export function csvRequest({ id, range, custom, pageRange, columns }) {
  const out = { id, columns };
  if (range === "page") return { ...out, from: pageRange.start, to: pageRange.end };
  if (range === "custom") return { ...out, from: custom.start, to: custom.end };
  return { ...out, range };
}

const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 86400000) + 1;

/** "12 Oct 2026" for a YYYY-MM-DD, in local time. */
export function csvDay(iso) {
  const [y, mo, d] = String(iso || "").split("-").map(Number);
  if (!y || !mo || !d) return "";
  return new Date(y, mo - 1, d).toLocaleDateString("en-GB",
    { day: "numeric", month: "short", year: "numeric" });
}

/** What the summary line says the file will hold. */
export function csvSummary({ range, custom, count, ranges }) {
  const words = range === "custom"
    ? (custom ? `${csvDay(custom.start)} - ${csvDay(custom.end)}` : "Custom range, dates not chosen")
    : (ranges.find(([v]) => v === range) || [])[1] || range;
  return `${words}, ${count} column${count === 1 ? "" : "s"}`;
}

//: `path`, `columns` and `what` let a place that has no table CARD use the same
//: dialog: the Database page offers one of its own tables (POST
//: /api/datasets/<key>/csv, the columns by the labels the server gives them).
export default function TableCsvDialog({ open, onClose, widget, pageRange = null,
                                         path = apiUrl("/api/builder/table-csv"),
                                         columns = null, what = null }) {
  const all = (columns || widget?.data?.columns || []).map(String);
  const [range, setRange] = useState(pageRange ? "page" : "7d");
  const [custom, setCustom] = useState(null);
  const [cols, setCols] = useState(all);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const chipsRef = useRef(null);
  const ranges = CSV_RANGES.filter(([v]) => v !== "page" || pageRange);
  const span = range === "custom" && custom ? daysBetween(custom.start, custom.end) : 0;
  // What still stands between the person and a file. Not a choice they got
  // WRONG, so it is a quiet hint under the field, not a warning colour; only a
  // range the server would refuse is said as a problem.
  const needsDates = range === "custom" && !custom;
  const needsCols = !cols.length;
  const tooLong = span > MAX_SPAN_DAYS;
  const blocked = needsDates || needsCols || tooLong;
  const toggle = (c) => setCols((now) => (now.includes(c)
    ? now.filter((x) => x !== c) : all.filter((x) => now.includes(x) || x === c)));

  // Arrow keys move the choice along the chips, one Tab stop for the group.
  const onChipKey = (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const at = ranges.findIndex(([v]) => v === range);
    const next = ranges[(at + step + ranges.length) % ranges.length][0];
    setRange(next);
    requestAnimationFrame(() => chipsRef.current
      ?.querySelector(`[data-range="${next}"]`)?.focus());
  };

  const download = async () => {
    setBusy(true);
    setError("");
    try {
      const file = await api.download(path,
        csvRequest({ id: widget?.id, range, custom, pageRange, columns: cols }));
      saveBlob(file);
      onClose?.();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };

  const summary = csvSummary({ range, custom, count: cols.length, ranges });
  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} title="Download CSV"
            footer={(
              <div className="csv-foot">
                <span className="csv-summary muted small" data-testid="csv-summary"
                      aria-live="polite">{summary}</span>
                <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
                <button type="button" className="btn btn-accent btn-ic" onClick={download}
                        disabled={busy || blocked} data-testid="csv-download" aria-busy={busy}>
                  {busy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                        : <Download size={14} aria-hidden="true" />}
                  {busy ? "Preparing..." : "Download"}
                </button>
              </div>
            )}>
      <div className="csv-dialog">
        <p className="muted small csv-sub" data-testid="csv-sub">
          {what ?? <>The rows of the table card <strong>{widget?.title || "Table"}</strong>, as a CSV file.</>}
        </p>

        <section className="csv-sec" aria-labelledby="csv-range-h">
          <h4 id="csv-range-h" className="csv-h">Date range</h4>
          <div ref={chipsRef} role="radiogroup" aria-labelledby="csv-range-h"
               className="csv-chips" onKeyDown={onChipKey}>
            {ranges.map(([v, words]) => {
              const on = range === v;
              return (
                <button key={v} type="button" role="radio" aria-checked={on}
                        data-range={v} tabIndex={on ? 0 : -1}
                        className={"csv-chip" + (on ? " is-on" : "")}
                        onClick={() => setRange(v)}>
                  {on && <Check size={13} aria-hidden="true" />}{words}
                </button>
              );
            })}
          </div>
          {range === "custom" && (
            <div className="csv-custom" data-testid="csv-custom">
              <DateRangePicker start={custom?.start} end={custom?.end} label="Pick dates"
                               onApply={(r) => setCustom({ start: r.start, end: r.end })} />
              {needsDates && (
                <span className="muted small" data-testid="csv-hint">
                  Choose the first and last day to download.
                </span>
              )}
            </div>
          )}
          {tooLong && (
            <p className="small c-warn" role="alert">
              A range can be at most {MAX_SPAN_DAYS} days.
            </p>
          )}
        </section>

        <section className="csv-sec" aria-labelledby="csv-cols-h">
          <div className="csv-sec-head">
            <h4 id="csv-cols-h" className="csv-h">Columns</h4>
            <span className="muted small" data-testid="csv-count" aria-live="polite">
              {cols.length} of {all.length} column{all.length === 1 ? "" : "s"} selected
            </span>
            <span style={{ flex: 1 }} />
            <span className="csv-links">
              <button type="button" className="csv-link" onClick={() => setCols(all)}>Select all</button>
              <button type="button" className="csv-link" onClick={() => setCols([])}>Clear</button>
            </span>
          </div>
          <div className="csv-grid" role="group" aria-labelledby="csv-cols-h">
            {all.map((c) => {
              const on = cols.includes(c);
              return (
                <button key={c} type="button" role="checkbox" aria-checked={on}
                        aria-label={`Column ${c}`} className={"csv-col" + (on ? " is-on" : "")}
                        onClick={() => toggle(c)}>
                  <span className="csv-box" aria-hidden="true">
                    {on && <Check size={12} strokeWidth={3} />}
                  </span>
                  <span className="csv-col-name">{c}</span>
                </button>
              );
            })}
          </div>
          {needsCols && (
            <span className="muted small" data-testid="csv-cols-hint">
              Choose at least one column.
            </span>
          )}
        </section>

        <p className="muted small" data-testid="csv-note">
          Every row in the range is included. Exporting more than{" "}
          {CSV_SLOW_ROWS.toLocaleString("en-US")} rows takes some time: keep this window
          open until the file is ready.
        </p>
        {error && <p className="small c-danger" role="alert">{error}</p>}
      </div>
    </Dialog>
  );
}
