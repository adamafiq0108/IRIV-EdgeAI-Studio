// Analytics - date range + a grid of generic widgets.
//
// ONE page, "Overview": the backend folds the system package's own widgets,
// everything the Workflow publishes to Analytics, and the operator's removals
// and additions into it. No tabs - there is nothing to tab between.
//
// This page RENDERS; it does not edit. Composing the Overview happens on
// /analytics/builder (pages/AnalyticsBuilder.jsx), which gets the whole window
// for a palette, a canvas and an inspector - the inline edit mode this page
// used to carry had room for none of that, and had to hide the live grid to
// show the canvas anyway.
//
// Auto-refreshes so widgets update as new events arrive (near real-time).
// Rendering is generic - no slug branching.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import NextPageButton from "../components/NextPageButton.jsx";
import { useApp } from "../state/app.jsx";
import WidgetGrid from "../components/widgets/WidgetGrid.jsx";
import ScopeBadge from "../components/ScopeBadge.jsx";
import DateRangePicker from "../components/ui/DateRangePicker.jsx";
import { saveAnalyticsRange } from "../lib/analyticsRange.js";
import DayStrip from "../components/ui/DayStrip.jsx";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { downloadAnalyticsPdf, periodText, reportFileName } from "../lib/analyticsReport.js";

const REFRESH_MS = 4000;    // live tiles track the feed; same cadence as Detection
const PAGE_ID = "overview"; // the one page the backend computes

// Local YYYY-MM-DD, matching DateRangePicker's own isoDay -- toISOString()
// gives the UTC day, which is the wrong date for anyone west of it.
const isoDay = (d) => {
  const x = new Date(d);
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}`
       + `-${String(x.getDate()).padStart(2, "0")}`;
};

export default function Analytics() {
  const { system, brand } = useApp();
  const [params, setParams] = useSearchParams();
  // Today/Week/Month/Custom used to be four buttons plus a picker that only
  // appeared for the fourth. DateRangePicker's own preset rail already covers
  // all three (and more), so this page now always operates in explicit
  // start/end terms -- one control instead of the same choice offered twice.
  //
  // The default span is THIS CALENDAR MONTH, not a rolling 30 days: the day
  // strip below lays the span out as days, and "the 1st to today" is the
  // month a person means when they say this month. A custom range replaces
  // it wholesale and the strip follows.
  const now = new Date();
  const monthStart = isoDay(new Date(now.getFullYear(), now.getMonth(), 1));
  const monthEnd = isoDay(new Date(now.getFullYear(), now.getMonth() + 1, 0));
  const start = params.get("start") || monthStart;
  const end = params.get("end") || monthEnd;
  // WHICH DAY inside the span the numbers are for. "" means the whole span.
  //
  // The default depends on how the operator got here: the page OPENS on
  // today (the question it is opened with), but a custom range they just
  // applied means "show me this period" -- so the whole span, every day lit,
  // until they narrow to one day by clicking it.
  const todayIso = isoDay(now);
  const custom = params.has("start");
  const dayParam = params.get("day");
  const day = dayParam === null
    ? (custom ? ""
       : todayIso >= start && todayIso <= end ? todayIso : "")
    : dayParam;
  const accent = brand?.accent || "#04bade";
  // The range this page is SHOWING (the picked day, else the span), kept for
  // the pages that follow it - the SQL Playground's "Follow Analytics range".
  // The same pair the table cards hand to Download CSV as this page's range.
  useEffect(() => {
    saveAnalyticsRange({ start: day || start, end: day || end });
  }, [day, start, end]);

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const toast = useToast();
  // The element holding the cards, for the PDF report (lib/analyticsReport.js).
  const gridRef = useRef(null);
  const [pdfBusy, setPdfBusy] = useState("");

  // A chosen day IS the range for the data: one day in, one day out, so
  // every widget answers "on this day" without a second concept reaching the
  // server. Clearing the day widens back to the whole span.
  const query = useMemo(() => {
    const q = new URLSearchParams({ range: "custom",
                                    start: day || start,
                                    end: day || end,
                                    // the whole span, for the strip's dots
                                    span_start: start, span_end: end });
    return q.toString();
  }, [start, end, day]);

  const load = useCallback((quiet) => {
    api.get(`/api/analytics?${query}`)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => { if (!quiet) setError(e); });
  }, [query]);

  useEffect(() => { load(); }, [load]);
  // Auto-refresh (quiet: a transient error mid-poll must not blank the page).
  //
  // useVisibleInterval, not a bare setInterval: this page can hold dozens of
  // widgets and each database-backed one costs the master its own query, so a
  // forgotten background tab was running that whole sweep every four seconds
  // to render pixels nobody was looking at.
  useVisibleInterval(() => load(true), REFRESH_MS);

  // The report: this page, card by card, as an A4 PDF. Made in the browser
  // from what is drawn, so it covers whatever the Builder put here.
  async function downloadPdf() {
    if (!gridRef.current || pdfBusy) return;
    setPdfBusy("Preparing");
    try {
      const name = system?.display_name || "System";
      await downloadAnalyticsPdf({
        grid: gridRef.current,
        title: `IRIV EdgeAI Studio \u00b7 ${name}`,
        subtitle: periodText(start, end, day),
        dateLabel: periodText(start, end, day),
        fileName: reportFileName(system?.slug || name, start, end, day),
        onProgress: (n, total) => setPdfBusy(`Page ${n} of ${total}`),
      });
    } catch (e) {
      toast.error("Could not make the PDF", e?.message || "Try again in a moment.");
    } finally { setPdfBusy(""); }
  }

  if (error) return <ErrorNote error={error} />;
  if (!data) return <SkelPage title="Analytics" cards={2} rows={4} />;

  // compute_pages folds everything into one page; the fallback covers a
  // payload that predates it.
  const page = (data.pages || [])[0]
    || { id: PAGE_ID, name: "Overview", widgets: data.widgets || [] };
  const widgets = page.widgets || [];
  const gridSize = page.size || "medium";
  const canEdit = !!data.editable;

  return (
    <section className="page active">
      {/* PRINT ONLY. The browser's own print header says the tab title and a
          URL; a report wants to say WHICH system, over WHAT period, and when
          it was taken -- three things nobody can reconstruct from a printed
          page of charts a week later. Hidden on screen, where the page
          already says all of it. */}
      <div className="print-head" aria-hidden="true">
        <strong>{system?.display_name || "System"}</strong>
        <span>{day ? day : `${start} → ${end}`}</span>
        <span>{new Date().toLocaleString()}</span>
      </div>
      <div className="page-head">
        <div className="page-title">Analytics<ScopeBadge /></div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <DateRangePicker start={start} end={end}
                           // An applied range means "show me this period":
                           // the whole span, every day lit. No day param --
                           // the default above resolves it to "".
                           onApply={(r) => setParams({ ...r })} />
          {/* Back to the page as it opens: this month, today selected. Only
              offered once there is something to reset. */}
          {(custom || dayParam !== null) && (
            <button className="btn btn-sm" title="Back to this month, today selected"
                    onClick={() => setParams({})}>
              Reset
            </button>
          )}
          {/* The PDF report is a real file now (lib/analyticsReport.js): the
              cards as drawn, on A4, with the header and the page numbers. The
              old button only opened the print window and was taken out for
              that (2026-10-14). Ctrl+P still prints the page. No page-wide
              CSV: each table card has its own Download CSV, top right, with
              a date range and the columns to take. */}
          <button className="btn btn-sm" onClick={downloadPdf} disabled={!!pdfBusy}
                  title="Download this page as a PDF report" data-analytics-pdf>
            {pdfBusy ? `${pdfBusy}...` : "Download PDF"}
          </button>
          {canEdit && (
            <Link className="btn btn-sm" to="/analytics/builder">Edit layout</Link>
          )}
          <NextPageButton />
        </div>
      </div>

      {/* The days of the span, under the header: picking one scopes every
          widget to that day. Today is picked on open, so the page lands on
          what is happening now instead of on a month-wide average. */}
      <DayStrip start={start} end={end} value={day}
                counts={data.day_counts} allSelected={!day}
                onPick={(d) => setParams({ start, end, day: d })} />
      {!day && (
        <p className="muted small" style={{ margin: "0 0 8px" }}>
          Showing the whole range - click a day above to narrow to it.
        </p>
      )}

      <div ref={gridRef}>
      <WidgetGrid
        widgets={widgets}
        accent={accent}
        size={gridSize}
        // Each table card's Download CSV offers this page's range too.
        exportCsv={{ pageRange: { start: day || start, end: day || end } }}
        empty={
          <div className="empty"><p>
            {canEdit
              ? "Nothing here yet - press Edit layout and add what your workflow publishes."
              : `${system?.display_name} has no analytics widgets yet.`}
          </p></div>
        }
      />
      </div>
    </section>
  );
}
