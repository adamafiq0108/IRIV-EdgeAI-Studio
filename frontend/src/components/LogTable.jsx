// LogTable -- the alarm-list table the three Diagnostics pages share.
//
// WHY IT IS SHARED. The Log Hub, the Events log and the Audit log answer
// three different questions, but they are read by ONE person, on one
// afternoon, going from page to page trying to work out what happened. Three
// tables with three row heights, three date formats and three ideas of what
// "failed" looks like make that person re-learn the furniture on every page.
// So the CHROME is here and identical -- fixed columns on a colgroup, a
// sticky head, 28px rows, one hairline, no zebra, a row that opens onto a
// details panel, the same paused bar, the same export buttons -- and what
// goes IN it stays with the page that knows the data: its columns, its
// filters, its details renderer, its empty state.
//
// WHAT IS DELIBERATELY NOT SHARED. The empty state. "No rows" is the one
// message a reader cannot act on: the three pages are empty for three
// different reasons (nothing has happened yet / the event store is not
// running / the ring was emptied by a restart), and only the page knows
// which. Every caller passes its own words.
//
// The level treatment is not redefined here either -- it is the shared
// fill / outline / dash system in components/LogLevel.jsx, measured in both
// themes and pinned by tests/check_loglevels.mjs. Events map their own
// severity words onto it (severityLevel) rather than bringing a second
// colour scheme onto the same shelf.
//
// The stylesheet is the LOG TABLE block in theme.css, scoped to `.logtable`.
// Pinned by tests/check_logtable.mjs.
import { Fragment, useEffect, useRef, useState } from "react";
import { clickable } from "../hooks/use-escape.js";

/** The expander column. Fixed width, no label, and it is appended by this
 *  component rather than declared by every caller -- three copies of the
 *  same last column is three chances for one of them to be 4px different. */
const EXPANDER_WIDTH = 34;

/**
 * columns: [{
 *   key,                 unique, and the React key
 *   label,               the column head
 *   width,               px. Carried as a CSS variable on the <col> so a
 *                        media query can still zero it (see `drop`).
 *   className,           optional extra class on the col/th/td
 *   drop,                1..5, or absent for a column that never leaves.
 *                        1 goes first (widest breakpoint), 5 last.
 *   mono,                monospace + the secondary text colour
 *   cell(row),           what the cell renders
 *   sub(row),            small print under the cell, shown ONLY once the
 *                        narrow breakpoints have taken columns away
 *   title(row),          the hover tooltip, for a value that truncates
 * }]
 *
 * rows, rowKey(row), rowClass(row) -- identity, never position: these lists
 * re-order and lose their far end under the reader, and an index key renames
 * every row when one arrives, moving the details panel somebody had open.
 *
 * details(row)   -> the collapsed panel. Absent means the page has no second
 *                   layer and no row opens.
 * paused         -> null when live (or when the page is not live at all), or
 *                   { held, onResume } to draw the held-count bar.
 * follow         -> "top" | "bottom" | null: which edge to keep in view when
 *                   new rows arrive, and only while the reader is at it.
 * empty / noMatch-> the two empty states, in the page's own words.
 * isFiltered     -> which of the two to show.
 */
export default function LogTable({
  columns, rows, rowKey, rowClass, details,
  paused = null, follow = null, empty = null, noMatch = null,
  isFiltered = false, footer = null,
}) {
  const [open, setOpen] = useState(null);
  const scroller = useRef(null);
  const atEdge = useRef(true);      // is the reader sitting at the live edge
  const selfScroll = useRef(false); // ignore the scroll event we just caused
  const span = columns.length + 1;

  // Follow the live edge, but only while the reader is already there. Yanking
  // somebody back to the top while they are reading row 80 is how a live
  // table becomes one you have to pause to use at all.
  // Resuming a paused list puts the reader back ON the edge, deliberately:
  // they asked for the held records, and the held records are at the edge.
  useEffect(() => { if (follow) atEdge.current = true; }, [follow]);

  useEffect(() => {
    const box = scroller.current;
    if (!box || !follow || !atEdge.current) return;
    selfScroll.current = true;
    box.scrollTop = follow === "bottom" ? box.scrollHeight : 0;
  }, [rows, follow]);

  function onScroll(e) {
    if (selfScroll.current) { selfScroll.current = false; return; }
    const box = e.currentTarget;
    atEdge.current = follow === "bottom"
      ? box.scrollHeight - box.scrollTop - box.clientHeight < 24
      : box.scrollTop < 24;
  }

  const held = paused ? paused.held : 0;

  return (
    <div className="lh-card">
      {paused && (
        <div className="lh-paused-bar" role="status">
          <span>
            Paused. {held === 0
              ? "Nothing new has arrived yet."
              : `${held} record${held === 1 ? "" : "s"} arrived while paused `
                + "and are being held."}
          </span>
          <button type="button" className="btn btn-sm" onClick={paused.onResume}>
            Resume{held ? ` and show ${held}` : ""}
          </button>
        </div>
      )}

      <div className="lh-scroll" ref={scroller} onScroll={onScroll}>
        <table className="lh-table">
          {/* The widths live on the <colgroup>, and that is load-bearing
              rather than tidiness. The details row spans EVERY column, so a
              column dropped at a narrow width still has to exist as far as
              the fixed layout is concerned - and a column whose only width
              came from a header that is now `display: none` counts as an auto
              column and takes an equal share of what is left. At phone width
              that left the message 19px wide. See the LOG TABLE block in
              theme.css. */}
          <colgroup>
            {/* A column with no width is the one that takes what is left -
                the message, on all three pages. It gets no --lh-w at all
                rather than an invalid one. */}
            {columns.map((c) => (
              <col key={c.key} className={colClass(c)}
                   style={c.width ? { "--lh-w": `${c.width}px` } : undefined} />
            ))}
            <col className="lh-c-exp" style={{ "--lh-w": `${EXPANDER_WIDTH}px` }} />
          </colgroup>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} className={colClass(c)}>{c.label}</th>
              ))}
              <th><span className="sr-only">Details</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? rows.map((row) => {
              const key = rowKey(row);
              const isOpen = open === key;
              const toggle = () => details && setOpen(isOpen ? null : key);
              return (
                <Fragment key={key}>
                  <tr className={rowClass ? rowClass(row) : "log-row"}
                      onClick={toggle}
                      {...(details ? clickable(toggle, { role: false }) : {})}
                      aria-expanded={details ? isOpen : undefined}
                      style={details ? { cursor: "pointer" } : undefined}>
                    {columns.map((c) => {
                      const sub = c.sub ? c.sub(row) : "";
                      return (
                        <td key={c.key}
                            className={[colClass(c), c.mono ? "lh-mono" : ""]
                              .filter(Boolean).join(" ") || undefined}
                            title={c.title ? c.title(row) : undefined}>
                          {c.sub ? <div className="lh-msg">{c.cell(row)}</div>
                                 : c.cell(row)}
                          {/* Only visible once the narrow breakpoints have
                              taken the columns it repeats away. */}
                          {sub ? <div className="lh-sub">{sub}</div> : null}
                        </td>
                      );
                    })}
                    <td className="lh-exp" aria-hidden="true">
                      {details ? (isOpen ? "▾" : "▸") : ""}
                    </td>
                  </tr>
                  {isOpen && details && (
                    /* Deliberately NOT the row class: the row tint and the
                       bold last cell are the alarm-list treatment for a
                       one-line event, and a whole panel of bold red text
                       would shout over the table it belongs to. The panel
                       carries its own left rule instead. */
                    <tr>
                      <td className="lh-detail-cell" colSpan={span}>
                        {details(row)}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            }) : (
              <tr>
                <td className="lh-empty" colSpan={span}>
                  {isFiltered ? noMatch : empty}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {footer}
    </div>
  );
}

/** A column's classes: its own, plus the drop priority the media queries in
 *  theme.css read. Lower numbers leave first. */
function colClass(c) {
  return [c.className, c.drop ? `lh-drop-${c.drop}` : ""]
    .filter(Boolean).join(" ") || undefined;
}

/** One labelled select. `options` takes either plain strings or [value, label]
 *  pairs, because most filters are values out of the data and a few are named
 *  choices. */
export function Picker({ label, value, onChange, options, all }) {
  return (
    <div className="lh-field">
      <span className="lh-field-label">{label}</span>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}
              aria-label={label}>
        {all !== undefined && <option value="">{all}</option>}
        {options.map((o) => {
          const [v, text] = Array.isArray(o) ? o : [o, o];
          return <option key={v} value={v}>{text}</option>;
        })}
      </select>
    </div>
  );
}

/** One labelled text box, for the filters that are typed rather than chosen. */
export function Field({ label, value, onChange, placeholder, wide, type = "search" }) {
  return (
    <div className={"lh-field" + (wide ? " lh-search" : "")}>
      <span className="lh-field-label">{label}</span>
      <input className="input" type={type} placeholder={placeholder}
             aria-label={label} value={value}
             onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

/** success / failed, and nothing else coloured. Anything a writer sends that
 *  is neither is shown as it came, in the neutral grey.
 *
 *  ONE component for all three pages: the Log Hub's `status`, an audit row's
 *  `result` and an event's disposition are the same word to the person
 *  reading them, and "ok" green on one page beside "ok" grey on the next is
 *  read as two different states. */
export function StatusWord({ value }) {
  if (!value) return <span className="lh-status">-</span>;
  const v = String(value).toLowerCase();
  const cls = ["success", "ok", "closed", "pass"].includes(v) ? " lh-status-ok"
            : ["failed", "failure", "error", "denied", "refused"].includes(v)
              ? " lh-status-fail" : "";
  return <span className={"lh-status" + cls}>{value}</span>;
}

/** The collapsed half: everything with a colon in it.
 *
 *  Laid out as a grid of short facts first and the long text last, because
 *  the short facts are what gets read down a phone ("request id, then the app
 *  version") and the long text is what gets pasted.
 *
 *  `facts` is [label, value] pairs; empties are dropped here so no caller has
 *  to filter its own list. `text` is what Copy puts on the clipboard. */
export function Details({ title = "Technical details", facts = [], lead = null,
                          text = "", onCopied, children }) {
  const shown = facts.filter(([, v]) => v !== "" && v !== null && v !== undefined);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      onCopied?.();
    } catch {
      // No clipboard permission (or an insecure origin on a plain-http
      // board): the panel is on screen and selectable, which is the fallback.
    }
  }

  return (
    <div className="lh-detail">
      <div className="lh-detail-head">
        <span className="lh-detail-title">{title}</span>
        {text ? (
          <button type="button" className="btn btn-sm" onClick={copy}>Copy</button>
        ) : null}
      </div>

      {lead}

      <div className="lh-grid">
        {shown.map(([k, v]) => (
          <div key={k}>
            <div className="lh-dt">{k}</div>
            <div className="lh-dd">{String(v)}</div>
          </div>
        ))}
      </div>

      {children}
    </div>
  );
}

/** A labelled block of pre-formatted text inside a details panel: a
 *  traceback, or a record as pretty JSON. */
export function DetailBlock({ label, children }) {
  return (
    <>
      {label ? <div className="lh-dt" style={{ marginTop: 8 }}>{label}</div> : null}
      <pre className="lh-pre">{children}</pre>
    </>
  );
}

/** The two exports, in the one order they appear on every page. Both carry
 *  WHAT IS ON SCREEN, filters and sort included: an export that quietly hands
 *  over everything is an export somebody has to filter a second time, by
 *  hand, in a spreadsheet. */
export function ExportButtons({ onCsv, onLog, disabled }) {
  return (
    <>
      <button type="button" className="btn btn-sm" onClick={onCsv}
              disabled={disabled}>Export CSV</button>
      <button type="button" className="btn btn-sm" onClick={onLog}
              disabled={disabled}>Export LOG</button>
    </>
  );
}

/** The Live / Paused toggle. Three cues and two of them survive greyscale:
 *  the button outlines and bolds, the dot stops moving, and the bar above the
 *  table says how many records are being held back. */
export function LiveButton({ live, onToggle }) {
  return (
    <button type="button" className={"btn lh-live" + (live ? "" : " is-paused")}
            onClick={onToggle} aria-pressed={!live}
            title={live ? "Freeze the list where it is"
                        : "Show everything that arrived while paused"}>
      <span className="lh-dot" aria-hidden="true" />
      {live ? "Live" : "Paused"}
    </button>
  );
}
