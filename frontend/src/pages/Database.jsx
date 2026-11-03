// Database - one place to manage this system's tables AND their rows.
//
// Deliberately not a database administration tool. The model is a spreadsheet
// that happens to be backed by Postgres: pick a table on the left, its rows
// appear on the right, edit a cell by double-clicking it. Nobody writes SQL,
// and there is no second page to go to for the data.
//
// Tables are made HERE and only here, which is what makes the Workflow's
// Database Entity nodes safe to generate: they render from this registry, so
// adding or renaming a column shows up on the canvas with no manual step.
//
// Two things about rows that look like ordinary CRUD and are not:
//
//   * A DELETE is a flag, not a delete. Rows reach the database through
//     events.jsonl, which the master replays from offset 0 on every boot, so
//     a real delete would be undone by the next restart -- the operator would
//     delete a row, watch it go, and find it back a week later.
//   * A hand-edited value is marked as edited, so what a person typed can be
//     told apart from what the system measured.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client.js";
import { ErrorNote, SkelPage } from "../components/Loading.jsx";
import { FormDialog } from "../components/ui/dialogs.jsx";
import { Dialog } from "../components/ui/primitives.jsx";
import TableCsvDialog from "../components/widgets/TableCsvDialog.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { useConfirm } from "../components/ui/ask.jsx";
import { useApp } from "../state/app.jsx";
import { authHeaders } from "../lib/session.js";
import { apiUrl } from "../lib/base.js";
import { analyticsRangeWords, readAnalyticsRange } from "../lib/analyticsRange.js";
import { ChevronDown, Download, Plus, RefreshCw } from "lucide-react";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "../components/ui/dropdown-menu.jsx";

// What an operator recognises, not what Postgres calls it. The mark is the
// glance-level cue a spreadsheet needs: a column of identical grey names hides
// the one fact that decides what you can do with it.
const TYPES = [
  ["text", "Text", "T"],
  ["number", "Number", "#"],
  ["boolean", "Boolean", "✓"],
  ["date", "Date", "📅"],
  ["timestamp", "Timestamp", "⏱"],
  ["json", "JSON", "{}"],
  ["image", "Image", "🖼"],
  ["uuid", "UUID", "🔑"],
];
const MARK = Object.fromEntries(TYPES.map(([id, , m]) => [id, m]));
const LABEL = Object.fromEntries(TYPES.map(([id, l]) => [id, l]));
// Stored before the type set widened; still means timestamp.
const norm = (t) => (t === "datetime" ? "timestamp" : t);

//: Rows per page, offered in the footer. The grid pages SERVER-side
//: (limit/offset), so a bigger page is a bigger query, not more client work.
// 1000 is the most one read returns (the repo clamps a single read there).
const PAGE_SIZES = [25, 50, 100, 200, 500, 1000];
const PAGE_SIZE = 50;                    // the default, and the legacy constant
const slugify = (s) => String(s || "").trim().toLowerCase()
  .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 32);

function fmtBytes(n) {
  if (!n) return "0 kB";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} kB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function ago(iso) {
  if (!iso) return "never";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(secs)) return "never";
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} minutes ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} hours ago`;
  return `${Math.floor(secs / 86400)} days ago`;
}

const show = (v, kind) => {
  if (v === null || v === undefined || v === "") return "";
  if (kind === "boolean") return v ? "true" : "false";
  if (kind === "timestamp" || kind === "date") {
    try { return new Date(v).toLocaleString(); } catch { return String(v); }
  }
  if (kind === "json") return typeof v === "string" ? v : JSON.stringify(v);
  return String(v);
};

export default function Database() {
  const toast = useToast();
  const confirm = useConfirm();
  const { system } = useApp();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [chosen, setChosen] = useState("");
  const [tableSearch, setTableSearch] = useState("");
  const [rowSearch, setRowSearch] = useState("");
  const [rows, setRows] = useState(null);
  const [total, setTotal] = useState(0);
  // The server stops counting at 20,000 ("20,000+"). The count is a speed
  // guard, not a limit on what can be read: Next keeps going while a full page
  // comes back, so every row recorded can be reached.
  const [capped, setCapped] = useState(false);
  // Past the cap the exact number is asked for once, for the table on screen
  // (GET /api/datasets/<key>/count), so the footer can say "of 24,731" and the
  // last page exists again. {key, n}; stale for another table, so it is keyed.
  const [exact, setExact] = useState(null);
  const exactFor = useRef("");      // the table the count was last asked for
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [sort, setSort] = useState({ col: "ts", dir: "desc" });
  const [sel, setSel] = useState(() => new Set());
  const [expanded, setExpanded] = useState(() => new Set());
  // What the last fetch actually did, shown the way a database tool reports a
  // statement: succeeded, how many rows, how long. Without it "the grid looks
  // the same" and "the query returned nothing" are indistinguishable.
  const [outcome, setOutcome] = useState(null);
  const [view, setView] = useState("rows");   // rows | sql
  // Saved queries, owned HERE so the rail can list them and the console can
  // save into them without each keeping its own copy. `preset` is a query
  // the rail hands the console to open: {sql, n}, n so the same query can be
  // handed over twice in a row.
  const [savedQueries, setSavedQueries] = useState([]);
  const [preset, setPreset] = useState(null);
  const loadSavedQueries = useCallback(() => api.get("/api/datasets/queries")
    .then((d) => setSavedQueries(d.queries || []))
    .catch(() => setSavedQueries([])), []);
  useEffect(() => { loadSavedQueries(); }, [loadSavedQueries]);
  const [openGroups, setOpenGroups] = useState(() => new Set());
  const [renaming, setRenaming] = useState(false);
  const [editing, setEditing] = useState(null);   // {rowId, column}
  const [newTable, setNewTable] = useState(false);
  const [schemaOpen, setSchemaOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadStatus = useCallback(() => {
    api.get("/api/datasets/status")
      .then((d) => {
        setStatus(d); setError(null);
        setChosen((cur) => ((d.datasets || []).some((t) => t.key === cur)
          ? cur : ((d.datasets || [])[0] || {}).key || ""));
      })
      .catch(setError);
  }, []);
  useEffect(loadStatus, [loadStatus]);

  const tables = status?.datasets || [];
  const editable = !!status?.editable;
  const active = useMemo(
    () => tables.find((t) => t.key === chosen) || null, [tables, chosen]);
  const columns = useMemo(
    () => (active?.columns || []).map((c) => ({ ...c, type: norm(c.type) })),
    [active]);

  const loadRows = useCallback(() => {
    if (!chosen || !status?.available) { setRows([]); setTotal(0); return; }
    const p = new URLSearchParams({
      limit: pageSize, offset: page * pageSize,
      order: sort.col, dir: sort.dir,
    });
    const started = performance.now();
    api.get(`/api/datasets/${encodeURIComponent(chosen)}/rows?${p}`)
      .then((d) => {
        setRows(d.rows || []); setTotal(d.total || 0); setCapped(!!d.capped);
        // Once per table, and again on its first page (where an add or a delete
        // is looked at): the full count is the one expensive question here.
        if (d.capped && (exactFor.current !== chosen || page === 0)) {
          exactFor.current = chosen;
          api.get(`/api/datasets/${encodeURIComponent(chosen)}/count`)
            .then((c) => { if (typeof c?.total === "number") setExact({ key: chosen, n: c.total }); })
            .catch(() => {});
        }
        setOutcome({ ok: true, rows: (d.rows || []).length,
                     ms: Math.round(performance.now() - started),
                     at: new Date().toLocaleTimeString() });
      })
      .catch((e) => {
        setOutcome({ ok: false, message: e.message,
                     at: new Date().toLocaleTimeString() });
        setError(e);
      });
  }, [chosen, page, pageSize, sort, status?.available]);
  useEffect(() => { setRows(null); loadRows(); }, [loadRows]);

  // Switching table resets the view: the old sort names a column the new
  // table has not got, and the server would quietly ignore it.
  useEffect(() => {
    setPage(0); setSel(new Set()); setEditing(null);
    setSort({ col: "ts", dir: "desc" }); setRowSearch("");
  }, [chosen]);

  const totalBytes = tables.reduce((n, t) => n + (t.bytes || 0), 0);
  const shownTables = tables.filter((t) => !tableSearch
    || t.name.toLowerCase().includes(tableSearch.toLowerCase())
    || t.key.toLowerCase().includes(tableSearch.toLowerCase()));

  // Row search filters what is on the page, instantly. Deliberately client
  // side: it is a "find that one row I can see" tool, not a query.
  const shownRows = useMemo(() => {
    if (!rows) return null;
    const q = rowSearch.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) => columns.some(
      (c) => show(r[c.name], c.type).toLowerCase().includes(q)));
  }, [rows, rowSearch, columns]);

  async function saveTables(next, message) {
    setBusy(true);
    try {
      await api.post("/api/datasets", { datasets: next });
      toast.success(message);
      loadStatus();
      return true;
    } catch (e) { toast.error("Could not save", e.message); return false; }
    finally { setBusy(false); }
  }

  async function rename(next) {
    setRenaming(false);
    if ((next || "").trim() === (status.name || "")) return;
    try {
      await api.post("/api/datasets/name", { name: next });
      loadStatus();
    } catch (e) { toast.error("Could not rename", e.message); }
  }

  async function addRow() {
    try {
      await api.post(`/api/datasets/${encodeURIComponent(chosen)}/rows`, { cells: {} });
      setPage(0); setSort({ col: "ts", dir: "desc" }); loadRows();
    } catch (e) { toast.error("Could not add a row", e.message); }
  }

  async function saveCell(rowId, column, raw) {
    setEditing(null);
    const before = rows.find((r) => r.event_id === rowId);
    if (!before || show(before[column]) === raw) return;
    // Optimistic: a spreadsheet that waits for a round-trip before showing
    // what you typed feels broken, so it shows it and rolls back if refused.
    setRows((cur) => cur.map((r) => (r.event_id === rowId
      ? { ...r, [column]: raw, edited: true } : r)));
    try {
      await api.patch(`/api/datasets/${encodeURIComponent(chosen)}/rows/${encodeURIComponent(rowId)}`,
                      { cells: { [column]: raw } });
      loadRows();
    } catch (e) {
      setRows((cur) => cur.map((r) => (r.event_id === rowId ? before : r)));
      toast.error("Could not save that cell", e.message);
    }
  }

  async function deleteRows() {
    const ids = [...sel];
    if (!ids.length) return;
    if (!await confirm({
      title: `Delete ${ids.length} row${ids.length === 1 ? "" : "s"}?`,
      description: "They are marked deleted and stop appearing everywhere - "
        + "charts, exports and the workflow. Retention clears them for good "
        + "later.",
      destructive: true })) return;
    try {
      const d = await api.post(
        `/api/datasets/${encodeURIComponent(chosen)}/rows/delete`, { rows: ids });
      setSel(new Set());
      toast.success(`Deleted ${d.deleted} row${d.deleted === 1 ? "" : "s"}`);
      loadRows(); loadStatus();
    } catch (e) { toast.error("Could not delete", e.message); }
  }

  /** One delete flow, wherever it is asked from -- the toolbar button and
   *  the Edit-table dialog must warn with the same words and do the same
   *  thing, or "delete" would mean two different amounts of gone. */
  async function deleteTable() {
    if (!active) return false;
    if (!await confirm({
      title: `Delete the table "${active.name}"?`,
      description: "Its rows stay in the database but nothing "
        + "will read them until a table of this name exists "
        + "again. Workflow nodes writing to it will stop "
        + "compiling.",
      destructive: true })) return false;
    return saveTables(tables.filter((t) => t.key !== active.key).map(strip),
                      `Deleted ${active.name}`);
  }

  function sortBy(col) {
    setSort((s) => ({ col, dir: s.col === col && s.dir === "desc" ? "asc" : "desc" }));
    setPage(0);
  }

  const [exporting, setExporting] = useState(false);
  // The Export CSV dialog: date range, columns, every row (the same dialog a
  // table card's Download CSV opens; POST /api/datasets/<key>/csv).
  const [csvOpen, setCsvOpen] = useState(false);

  /** Download this system's data as a SQLite file.
   *
   *  Fetched rather than linked, because the endpoint is session-gated and a
   *  bare <a href> carries no auth header. The blob is released as soon as
   *  the click is dispatched: an export can be hundreds of megabytes and
   *  holding it after the browser has taken it is holding it twice.
   */
  async function exportDb() {
    setExporting(true);
    try {
      const res = await fetch(apiUrl("/api/detector/export/db"), { headers: authHeaders() });
      if (!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`);
      const blob = await res.blob();
      const name = (res.headers.get("Content-Disposition") || "")
        .match(/filename="?([^"]+)"?/)?.[1] || "system.db";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = name; a.click();
      URL.revokeObjectURL(url);
      const rows = res.headers.get("X-Export-Rows");
      toast.success("Database exported",
                    rows ? `Rows: ${rows}` : "Opens in any SQLite tool.");
    } catch (e) {
      toast.error("Export failed", e.message);
    } finally { setExporting(false); }
  }


  // -- import: the export, read back --------------------------------------
  // Two steps on purpose. The file is READ first and what it holds is shown
  // -- whose system, when it was taken, how many rows -- and only then is
  // there a button that writes. "Import this database" is a question nobody
  // can answer from a filename, and a file from another system entirely is a
  // perfectly valid thing to import and a terrible thing to import by
  // accident.
  const importRef = useRef(null);
  const [pending, setPending] = useState(null);   // {file, found}
  const [importMode, setImportMode] = useState("append");
  const [importing, setImporting] = useState(false);

  async function postDb(file, mode) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);
    const res = await fetch(apiUrl("/api/detector/import/db"), {
      method: "POST", credentials: "same-origin",
      body: fd, headers: { ...authHeaders() },
    });
    const ct = res.headers.get("content-type") || "";
    const d = ct.includes("application/json")
      ? await res.json().catch(() => null) : null;
    if (!res.ok) throw new Error((d && (d.error || d.detail)) || `HTTP ${res.status}`);
    return d;
  }

  async function onImportPicked(e) {
    const file = e.target.files?.[0];
    e.target.value = "";                       // same file re-pickable later
    if (!file) return;
    setImporting(true);
    try {
      const found = await postDb(file, "preview");
      setImportMode("append");
      setPending({ file, found });
    } catch (err) {
      toast.error("That file could not be read", err.message);
    } finally { setImporting(false); }
  }

  async function runImport() {
    if (!pending) return;
    setImporting(true);
    try {
      const d = await postDb(pending.file, importMode);
      const written = d.written || {};
      const rows = (written.events || 0) + (written.dataset_rows || 0);
      toast.success(
        rows ? `Imported ${rows.toLocaleString()} rows`
             : "Nothing new to import",
        d.rekeyed
          ? "The rows came from another system, so they were given new ids."
          : "Rows already here were left alone.");
      for (const w of d.warnings || []) toast.warn("Partly imported", w);
      setPending(null);
      loadStatus(); loadRows();
    } catch (err) {
      toast.error("Import failed", err.message);
    } finally { setImporting(false); }
  }

  if (error) return <section className="page active"><ErrorNote error={error} /></section>;
  if (!status) return <SkelPage />;

  // The exact count, once it has come, replaces "at least 20,000": the pager
  // then knows its last page. Until then (or if it never comes) it is capped.
  const exactTotal = capped && exact && exact.key === chosen ? exact.n : null;
  const effTotal = exactTotal ?? total;
  const effCapped = capped && exactTotal === null;
  const pages = Math.max(1, Math.ceil(effTotal / pageSize));
  // "Showing 1 to 50 of 19,666" -- the count the footer states. From `total`
  // (the server's count) and the page, not from what is on screen: the row
  // SEARCH filters the page client-side, and a footer that followed it would
  // claim the table had shrunk.
  const firstRow = effTotal ? page * pageSize + 1 : 0;
  // Past the server's count cap the total is "at least", so the end of THIS
  // page is what was read, and there is a next page while a full one came back.
  const lastRow = effCapped ? firstRow + (rows || []).length - 1
                            : Math.min(effTotal, (page + 1) * pageSize);
  const hasNext = effCapped ? (rows || []).length >= pageSize : page + 1 < pages;

  return (
    <section className="page active db-page">
      <div className="db-shell">
        {/* ------------------------------------------------ left panel */}
        <aside className="db-side">
          {/* The database itself, then what is in it -- the shape every
              database tool uses, because "which database am I looking at" is
              the question you ask once and then never again. */}
          <div className="db-db">
            <span className="db-db-mark">DB</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              {renaming ? (
                <input className="input" autoFocus defaultValue={status.name}
                       maxLength={60} style={{ fontSize: 13, fontWeight: 600 }}
                       onBlur={(e) => rename(e.target.value)}
                       onKeyDown={(e) => {
                         if (e.key === "Enter") rename(e.target.value);
                         if (e.key === "Escape") setRenaming(false);
                       }} />
              ) : (
                <div className="db-db-name"
                     title={editable ? "Double-click to rename" : undefined}
                     onDoubleClick={() => editable && setRenaming(true)}>
                  {status.name || "Database"}
                </div>
              )}
              <div className="db-db-sub">{fmtBytes(totalBytes)}</div>
            </div>
            {editable && !renaming && (
              <button type="button" className="db-icon-btn" title="Rename"
                      onClick={() => setRenaming(true)}>✎</button>
            )}
            {/* Every system on this box shares ONE database and is told apart
                by a slug column. This carves THIS system out into a SQLite
                file that opens in any database tool -- events, Data Table
                rows, and each table expanded into its own columns. */}
            <button type="button" className="db-icon-btn"
                    disabled={exporting}
                    title={exporting
                      ? "Building the file…"
                      : "Download this system's data as a SQLite database"}
                    onClick={exportDb}>{exporting ? "…" : "⭳"}</button>
            {/* ...and the way back in. The export was a one-way door for a
                long time, which makes it a souvenir rather than a move. */}
            {editable && (
              <button type="button" className="db-icon-btn"
                      disabled={importing}
                      title={importing
                        ? "Reading the file…"
                        : "Load a SQLite export into this system"}
                      onClick={() => importRef.current?.click()}>
                {importing ? "…" : "⭱"}
              </button>
            )}
            <input ref={importRef} type="file" accept=".db,.sqlite,.sqlite3"
                   style={{ display: "none" }} onChange={onImportPicked} />
          </div>
          <input className="input db-search" placeholder="Search…"
                 value={tableSearch} onChange={(e) => setTableSearch(e.target.value)} />
          {/* Directly under the search, above the list it adds to. */}
          {editable && (
            <button type="button" className="btn btn-sm db-newtable"
                    onClick={() => setNewTable(true)}>
              New Table
            </button>
          )}
          <div className="db-side-head">
            <span>Tables</span>
            <button type="button" className="db-icon-btn" title="Refresh"
                    onClick={() => { loadStatus(); loadRows(); }}>⟳</button>
          </div>
          {shownTables.map((t) => {
            const open = expanded.has(t.key);
            return (
              <div key={t.key}>
                {/* One card per table: icon, name, and the two facts that
                    say WHICH table you meant (rows and size). The ⋮ on the
                    right opens its column list -- a separate twisty column
                    put a lone arrow beside every row for a thing most
                    visits never expand. */}
                <div className={"db-table-row" + (t.key === chosen ? " is-active" : "")}>
                  <button type="button" className="db-table-btn"
                          onClick={() => setChosen(t.key)}>
                    <span className="db-mark">▦</span>
                    <span className="db-table-text">
                      <span className="db-table-name">{t.name}</span>
                      <span className="db-table-meta">
                        {t.rows.toLocaleString()}{t.capped ? "+" : ""} rows
                        {t.bytes ? ` · ${fmtBytes(t.bytes)}` : ""}
                      </span>
                    </span>
                  </button>
                  <button type="button" className="db-icon-btn db-table-more"
                          aria-expanded={open}
                          title={open ? "Hide columns" : "Show columns"}
                          onClick={() => setExpanded((cur) => {
                            const next = new Set(cur);
                            if (next.has(t.key)) next.delete(t.key);
                            else next.add(t.key);
                            return next;
                          })}>⋮</button>
                </div>
                {open && (
                  <div className="db-cols">
                    {!t.columns.length && (
                      <div className="db-col-row muted">no columns</div>
                    )}
                    {t.columns.map((c) => (
                      <div className="db-col-row" key={c.name}
                           title={`${c.name} - ${LABEL[norm(c.type)]}`}>
                        <span className="db-mark">{MARK[norm(c.type)]}</span>
                        <span className="db-col-name">{c.name}</span>
                        <span className="db-col-type">{norm(c.type)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {!shownTables.length && (
            <div className="muted small" style={{ padding: "8px 4px" }}>
              {tables.length ? "No table matches." : "No tables yet."}
            </div>
          )}
          {/* Saved queries, by the table they read. A query is kept from the
              SQL Playground; listed here it is one click from running again,
              and grouping by table answers "what have we asked of THIS
              table" without reading every statement. A query over several
              tables is under each; one over none sits under "other". */}
          <div className="db-side-head" style={{ marginTop: 14 }}>
            <span>Saved queries</span>
            <span className="muted small">{savedQueries.length || ""}</span>
          </div>
          {(() => {
            const groups = new Map();
            const q = tableSearch.trim().toLowerCase();
            savedQueries
              .filter((x) => !q || x.name.toLowerCase().includes(q)
                                 || x.sql.toLowerCase().includes(q)
                                 || (x.tables || []).some((t) => t.toLowerCase().includes(q)))
              .forEach((x) => {
                const keys = (x.tables || []).length ? x.tables : ["other"];
                keys.forEach((k) => {
                  if (!groups.has(k)) groups.set(k, []);
                  groups.get(k).push(x);
                });
              });
            const byName = (k) => tables.find((t) => t.key === k)?.name || k;
            const ordered = [...groups.keys()].sort((a, b) =>
              (a === "other") - (b === "other") || byName(a).localeCompare(byName(b)));
            if (!ordered.length) {
              return (
                <div className="muted small" style={{ padding: "8px 4px" }}>
                  {savedQueries.length ? "No saved query matches."
                    : "None yet - Save query in the SQL Playground."}
                </div>
              );
            }
            return ordered.map((k) => {
              const open = openGroups.has(k) || !!q;
              const list = groups.get(k);
              return (
                <div key={k}>
                  <button type="button" className="db-table-btn db-qgroup"
                          aria-expanded={open}
                          onClick={() => setOpenGroups((cur) => {
                            const next = new Set(cur);
                            if (next.has(k)) next.delete(k); else next.add(k);
                            return next;
                          })}>
                    <span className="db-mark">{open ? "▾" : "▸"}</span>
                    <span className="db-table-text">
                      <span className="db-table-name">{byName(k)}</span>
                      <span className="db-table-meta">
                        {list.length} quer{list.length === 1 ? "y" : "ies"}
                      </span>
                    </span>
                  </button>
                  {open && (
                    <div className="db-cols">
                      {list.map((x) => (
                        <button type="button" key={x.key} className="db-col-row db-qrow"
                                title={x.sql}
                                onClick={() => { setPreset({ sql: x.sql, n: Date.now() });
                                                 setView("sql"); }}>
                          <span className="db-mark">›_</span>
                          <span className="db-col-name">{x.name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            });
          })()}

          {/* Pinned to the bottom of the rail: the SQL editor is the other
              half of this page and lives behind a tab an operator has to
              notice. Naming it here is how they find it the first time. */}
          {editable && (
            <div className="db-side-foot">
              <div className="db-help">
                <div className="db-help-title">Need help with SQL?</div>
                <p className="muted small" style={{ margin: "2px 0 8px" }}>
                  Write and run read-only queries against these tables.
                </p>
                <button type="button" className="btn btn-sm"
                        style={{ width: "100%" }}
                        onClick={() => setView("sql")}>
                  ‹/› Open SQL Playground
                </button>
              </div>
            </div>
          )}
        </aside>

        {/* ----------------------------------------------- right panel */}
        <div className="db-main">
          {!status.available && (
            <div className="db-banner">
              <strong>The row store is not reachable.</strong> Tables can still be
              designed, but no rows are stored or shown until Postgres is back.
            </div>
          )}

          {!active ? (
            <div className="db-empty">
              <div className="db-empty-title">Database</div>
              <p className="muted">
                Select a table from the left panel, or create a new table.
              </p>
              {editable && (
                <button className="btn btn-accent" onClick={() => setNewTable(true)}>
                  New Table
                </button>
              )}
            </div>
          ) : (<>
            {/* Tabs: the table you picked, and the query editor. Two ways of
                asking the same database the same questions -- one by
                clicking, one by typing. */}
            <div className="db-tabs">
              <button type="button"
                      className={"db-tab" + (view === "rows" ? " is-active" : "")}
                      onClick={() => setView("rows")}>
                <span className="db-mark">▦</span> {active.name}
              </button>
              {editable && (
                <button type="button"
                        className={"db-tab" + (view === "sql" ? " is-active" : "")}
                        onClick={() => setView("sql")}>
                  <span className="db-mark">›_</span> SQL Playground
                </button>
              )}
            </div>

            {view === "sql" ? (
              <SqlConsole tables={tables} available={status.available}
                          saved={savedQueries} onSavedChange={loadSavedQueries}
                          preset={preset} />
            ) : (<>
            {/* Search first and widest, then the ONE filled button (Add Row),
                Refresh, and the two exports folded into one menu at the far
                right (Tok Aba, 2026-10-14). Same handlers and gates as before:
                Add Row for an editable system only, the exports for anyone
                who can read the table. */}
            <div className="db-toolbar">
              <input className="input db-rowsearch" placeholder="Search rows…"
                     aria-label="Search rows"
                     value={rowSearch} onChange={(e) => setRowSearch(e.target.value)} />
              {editable && (
                <button className="btn btn-sm btn-accent btn-ic" onClick={addRow}
                        data-db-add-row=""
                        disabled={!status.available || !columns.length}>
                  <Plus size={14} aria-hidden="true" /> Add Row
                </button>
              )}
              <button className="btn btn-sm btn-ic" title="Refresh" data-db-refresh=""
                      onClick={() => { loadRows(); loadStatus(); }}>
                <RefreshCw size={13} aria-hidden="true" /> Refresh
              </button>
              <div style={{ flex: 1 }} />
              {sel.size > 0 && editable && (
                <button className="btn btn-sm" onClick={deleteRows}>
                  Delete {sel.size} row{sel.size === 1 ? "" : "s"}
                </button>
              )}
              {editable && (
                <button className="btn btn-sm" onClick={() => setSchemaOpen(true)}>
                  Edit table
                </button>
              )}
              {editable && (
                <button className="btn btn-sm" onClick={deleteTable}
                        title="Delete this table's definition - recorded rows stay until retention clears them">
                  Delete table
                </button>
              )}
              {/* Export CSV is this table's rows, every one in the chosen range
                  (the dialog a table card's Download CSV opens); Export database is the whole system as a SQLite file
                  (GET /api/detector/export/db) - the same download the
                  sidebar's button starts. */}
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={<button type="button" className="btn btn-sm btn-ic db-export-btn"
                                  data-db-export="" disabled={!status.available} />}>
                  <Download size={13} aria-hidden="true" />
                  {exporting ? "Exporting…" : "Export"}
                  <ChevronDown size={13} aria-hidden="true" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="db-export-menu"
                                     style={{ width: "min(270px, calc(100vw - 16px))" }}>
                  <DropdownMenuItem onClick={() => setCsvOpen(true)} disabled={!status.available || !active}
                                    data-db-export-item="csv">
                    <span className="db-export-item">
                      <span className="db-export-name">Export CSV</span>
                      <span className="db-export-hint">The rows of this table, by date range and columns, as a CSV file.</span>
                    </span>
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={exportDb} disabled={!status.available || exporting}
                                    data-db-export-item="db"
                                    title="Download this system's data as a SQLite database">
                    <span className="db-export-item">
                      <span className="db-export-name">Export database</span>
                      <span className="db-export-hint">Everything in this system: all tables, as a SQLite file.</span>
                    </span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {/* The four facts about a table, as cards rather than a line of
                small print: they are what an operator checks BEFORE reading
                any row -- how much is in here, what shape is it, is it still
                being written to. A run-together sentence made them one thing
                to parse instead of four to glance at. */}
            <div className="db-kpis">
              <div className="db-kpi">
                <span className="db-kpi-label">Total Rows</span>
                <span className="db-kpi-value">
                  {exactTotal !== null ? exactTotal.toLocaleString()
                    : <>{active.rows.toLocaleString()}{active.capped ? "+" : ""}</>}
                </span>
              </div>
              <div className="db-kpi">
                <span className="db-kpi-label">Columns</span>
                <span className="db-kpi-value">{columns.length}</span>
              </div>
              <div className="db-kpi">
                <span className="db-kpi-label">Table Size</span>
                <span className="db-kpi-value">{fmtBytes(active.bytes)}</span>
              </div>
              <div className="db-kpi">
                <span className="db-kpi-label">Last updated</span>
                <span className="db-kpi-value db-kpi-soft">{ago(active.last_ts)}</span>
              </div>
            </div>

            {/* What the last read actually did. A grid that looks the same
                whether it returned nothing or failed is a grid you cannot
                trust; a database tool always says which. */}
            {outcome && (
              <div className="db-outcome">
                <span className="muted">Action output:</span>
                {outcome.ok ? (<>
                  <span className="db-ok">✓ Success</span>
                  <b>{outcome.rows} row{outcome.rows === 1 ? "" : "s"}</b>
                  <span className="muted">{(outcome.ms / 1000).toFixed(2)} s</span>
                </>) : (
                  <span className="db-bad">✕ {outcome.message}</span>
                )}
                <span className="muted">{outcome.at}</span>
              </div>
            )}

            <div className="db-grid-wrap">
              {!columns.length ? (
                <div className="db-empty">
                  <p className="muted">
                    This table has no columns yet.
                  </p>
                  {editable && (
                    <button className="btn btn-accent" onClick={() => setSchemaOpen(true)}>
                      Add columns
                    </button>
                  )}
                </div>
              ) : shownRows === null ? (
                <div className="muted" style={{ padding: 24 }}>Loading…</div>
              ) : !shownRows.length ? (
                <div className="db-empty">
                  <p className="muted">
                    {rowSearch ? "No row matches that search."
                      : status.available
                        ? "No rows yet. A workflow Data Table node fills this, or add one by hand."
                        : "The row store is not reachable."}
                  </p>
                </div>
              ) : (
                <table className="db-grid">
                  <thead>
                    <tr>
                      <th className="db-pick">
                        <input type="checkbox"
                               checked={sel.size > 0 && sel.size === shownRows.length}
                               onChange={(e) => setSel(e.target.checked
                                 ? new Set(shownRows.map((r) => r.event_id))
                                 : new Set())} />
                      </th>
                      <th onClick={() => sortBy("ts")} className="db-sortable">
                        Datetime{sort.col === "ts" ? (sort.dir === "desc" ? " ▾" : " ▴") : ""}
                      </th>
                      {columns.map((c) => (
                        <th key={c.name} className="db-sortable"
                            onClick={() => sortBy(c.name)} title={LABEL[c.type]}>
                          <span className="db-mark">{MARK[c.type]}</span>
                          {c.name}
                          {sort.col === c.name ? (sort.dir === "desc" ? " ▾" : " ▴") : ""}
                        </th>
                      ))}
                      {/* Soaks up the leftover width so the real columns stay
                          sized to their content instead of stretching. */}
                      <th className="db-filler" />
                    </tr>
                  </thead>
                  <tbody>
                    {shownRows.map((r) => (
                      <tr key={r.event_id}
                          className={sel.has(r.event_id) ? "is-sel" : ""}>
                        <td className="db-pick">
                          <input type="checkbox" checked={sel.has(r.event_id)}
                                 onChange={(e) => setSel((cur) => {
                                   const next = new Set(cur);
                                   if (e.target.checked) next.add(r.event_id);
                                   else next.delete(r.event_id);
                                   return next;
                                 })} />
                        </td>
                        <td className="db-when">
                          {show(r.ts, "timestamp")}
                          {r.edited && <span className="db-edited" title="edited by hand">•</span>}
                        </td>
                        {columns.map((c) => (
                          <Cell key={c.name} value={r[c.name]} type={c.type}
                                editable={editable}
                                editing={editing?.rowId === r.event_id
                                         && editing?.column === c.name}
                                onEdit={() => editable
                                  && setEditing({ rowId: r.event_id, column: c.name })}
                                onCancel={() => setEditing(null)}
                                onSave={(v) => saveCell(r.event_id, c.name, v)} />
                        ))}
                        <td className="db-filler" />
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* Page size, position, and first/prev/next/last -- the shape
                every database grid has, because "how far in am I" and "take
                me to the end" are the two things a long table is asked. */}
            <div className="db-foot">
              <select className="input db-pagesize" value={pageSize}
                      aria-label="Rows per page"
                      onChange={(e) => { setPageSize(+e.target.value); setPage(0); }}>
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>{n} rows per page</option>
                ))}
              </select>
              <span className="muted small db-foot-count">
                {effTotal
                  ? <>Showing {firstRow.toLocaleString()} to {lastRow.toLocaleString()} of{" "}
                     {effTotal.toLocaleString()}{effCapped ? "+" : ""} rows</>
                  : "No rows"}
                {/* The search filters THIS page only, so it says so rather
                    than letting the count above look wrong. */}
                {rowSearch && shownRows
                  ? ` · ${shownRows.length} match on this page` : ""}
              </span>
              <div className="db-pager">
                <button className="btn btn-sm" disabled={page === 0}
                        title="First page" aria-label="First page"
                        onClick={() => setPage(0)}>⇤</button>
                <button className="btn btn-sm" disabled={page === 0}
                        onClick={() => setPage((p) => p - 1)}>‹ Prev</button>
                <span className="muted small db-foot-page">
                  Page <b>{page + 1}</b>{effCapped ? "" : <> of {pages.toLocaleString()}</>}
                </span>
                <button className="btn btn-sm" disabled={!hasNext}
                        onClick={() => setPage((p) => p + 1)}>Next ›</button>
                <button className="btn btn-sm" disabled={effCapped || page + 1 >= pages}
                        title={effCapped ? "The last page is not known while the table has more rows than the count shows"
                                      : "Last page"}
                        aria-label="Last page"
                        onClick={() => setPage(pages - 1)}>⇥</button>
              </div>
            </div>
            </>)}
          </>)}
        </div>
      </div>

      <NewTableDialog open={newTable} onOpenChange={setNewTable} busy={busy}
                      existing={tables}
                      onCreate={async (table) => {
                        if (await saveTables([...tables.map(strip), table],
                                             `Created ${table.name}`)) {
                          setNewTable(false); setChosen(table.key);
                        }
                      }} />

      <SchemaDialog open={schemaOpen} onOpenChange={setSchemaOpen} table={active}
                    columns={columns} busy={busy} rows={active?.rows || 0}
                    onSave={async (next) => {
                      if (await saveTables(
                        tables.map((t) => (t.key === next.key ? next : strip(t))),
                        `Saved ${next.name}`)) setSchemaOpen(false);
                    }}
                    onDelete={async () => {
                      if (await deleteTable()) setSchemaOpen(false);
                    }} />

      {/* What the file holds, before anything is written. */}
      <Dialog open={!!pending} title="Import this database?"
              onClose={() => !importing && setPending(null)}
              footer={
                <>
                  <button className="btn btn-sm" disabled={importing}
                          onClick={() => setPending(null)}>Cancel</button>
                  <button className={"btn btn-sm "
                            + (importMode === "replace" ? "btn-danger" : "btn-accent")}
                          disabled={importing} onClick={runImport}>
                    {importing ? "Importing…"
                      : importMode === "replace" ? "Replace everything" : "Add the rows"}
                  </button>
                </>
              }>
        {pending && (
          <div className="rail-form">
            <p className="small" style={{ marginTop: 0 }}>
              <strong>{pending.file.name}</strong>
            </p>
            <div className="rail-brief">
              <div className="rail-brief-row">
                <span className="muted small">from</span>
                <span className="small">
                  {pending.found.system_slug || "an unnamed system"}
                  {pending.found.system_slug
                    && pending.found.system_slug !== system?.slug
                    && " - a different system to this one"}
                </span>
              </div>
              <div className="rail-brief-row">
                <span className="muted small">taken</span>
                <span className="small">{pending.found.exported_at || "at an unknown time"}</span>
              </div>
              <div className="rail-brief-row">
                <span className="muted small">rows</span>
                <span className="small">
                  {(pending.found.counts?.events || 0).toLocaleString()} events,{" "}
                  {(pending.found.counts?.dataset_rows || 0).toLocaleString()} table rows
                </span>
              </div>
              <div className="rail-brief-row">
                <span className="muted small">tables</span>
                <span className="small">
                  {(pending.found.tables || []).length
                    ? (pending.found.tables || []).map((t) => t.key).join(", ")
                    : "none - rows only"}
                </span>
              </div>
            </div>
            {pending.found.note && (
              <p className="muted small">{pending.found.note}</p>
            )}
            <label className="wf-check">
              <input type="radio" name="dbmode" checked={importMode === "append"}
                     onChange={() => setImportMode("append")} />
              <span>
                <strong>Add these rows</strong> - what is already here stays.
                Importing the same file twice changes nothing the second time.
              </span>
            </label>
            <label className="wf-check">
              <input type="radio" name="dbmode" checked={importMode === "replace"}
                     onChange={() => setImportMode("replace")} />
              <span>
                <strong>Replace everything</strong> - this system's events and
                Data Table rows are <strong>deleted</strong> first, then the
                file's are added. For restoring a backup.
              </span>
            </label>
            {importMode === "replace" && (
              <p className="small c-danger">
                Every event and Data Table row this system has recorded will be
                deleted. Snapshots and clips are not touched, and they are not
                in the file either.
              </p>
            )}
          </div>
        )}
      </Dialog>

      {csvOpen && active && (
        <TableCsvDialog open onClose={() => setCsvOpen(false)}
                        path={apiUrl(`/api/datasets/${encodeURIComponent(active.key)}/csv`)}
                        columns={["Datetime", ...columns.map((c) => c.name.replace(/_/g, " "))]}
                        what={<>The rows of the table <strong>{active.name || active.key}</strong>, as a CSV file.</>} />
      )}
    </section>
  );
}

/** Only the fields the API stores - the status payload carries row counts too. */
const strip = (t) => ({ key: t.key, name: t.name, columns: t.columns });

/**
 * The query editor.
 *
 * What it can reach is decided in Postgres, not here: the query runs as a role
 * with no rights on any base table, against views that already filter to this
 * system (core/services/sqlconsole.py). So `SELECT * FROM counting` returns
 * this system's rows with the declared columns projected out of the JSONB, and
 * `SELECT * FROM public.events` is refused by the database itself rather than
 * by a check that could be forgotten.
 */
//: The clauses that start a line when the editor's Format button is pressed.
//: Deliberately a keyword-per-line reflow and nothing more -- a real SQL
//: pretty-printer needs a parser, and one that half-understands a statement
//: would rewrite queries it does not understand.
const SQL_CLAUSES = ["SELECT", "FROM", "WHERE", "GROUP BY", "HAVING",
                     "ORDER BY", "LIMIT", "OFFSET", "JOIN", "LEFT JOIN",
                     "RIGHT JOIN", "INNER JOIN", "UNION"];

//: What the palette under the editor offers, by how much SQL you already
//: know. Each is PostgreSQL as this box runs it, written to drop in at the
//: caret and read as a sentence -- `%(from)s` / `%(to)s` are the period the
//: Period picker binds, the same way a card or a Telegram button binds it.
//: `|` marks where the caret lands after the insert.
const SQL_SNIPPETS = {
  Basic: [
    ["SELECT", "SELECT |\nFROM "],
    ["FROM", "FROM |"],
    ["WHERE", "WHERE |"],
    ["AND / OR", "AND |"],
    ["ORDER BY", "ORDER BY | DESC"],
    ["LIMIT", "LIMIT |20"],
    ["COUNT", "COUNT(*)|"],
    ["SUM / AVG", "SUM(|)"],
    ["MIN / MAX", "MAX(|)"],
    ["GROUP BY", "GROUP BY |"],
    ["DISTINCT", "DISTINCT |"],
    ["AS", "AS |"],
    ["LIKE", "LIKE '%|%'"],
    ["IN (...)", "IN (|)"],
    ["BETWEEN", "BETWEEN | AND "],
    ["IS NULL", "IS NULL|"],
    ["in the period", "ts >= %(from)s AND ts < %(to)s|"],
  ],
  Numbers: [
    ["ROUND", "ROUND(|::numeric, 2)"],
    ["TRUNC", "TRUNC(|::numeric, 0)"],
    ["FLOOR / CEIL", "FLOOR(|)"],
    ["ABS", "ABS(|)"],
    ["MOD", "MOD(|, 10)"],
    ["POWER / SQRT", "POWER(|, 2)"],
    ["GREATEST / LEAST", "GREATEST(|, 0)"],
    ["percent", "ROUND(100.0 * | / NULLIF(COUNT(*), 0), 1)"],
    ["STDDEV", "STDDEV(|)"],
    ["VARIANCE", "VARIANCE(|)"],
    ["MODE", "MODE() WITHIN GROUP (ORDER BY |)"],
    ["percentile", "percentile_cont(0.5) WITHIN GROUP (ORDER BY |)"],
    ["CORR", "CORR(|, )"],
    ["::numeric", "|::numeric"],
    ["::integer", "|::integer"],
    ["WIDTH_BUCKET", "WIDTH_BUCKET(|, 0, 100, 10)"],
  ],
  Dates: [
    ["per hour", "date_trunc('hour', ts) AS hour|"],
    ["per day", "ts::date AS day|"],
    ["per week / month", "date_trunc('week', ts) AS week|"],
    ["EXTRACT", "EXTRACT(hour FROM ts)|"],
    ["TO_CHAR", "TO_CHAR(ts, 'YYYY-MM-DD HH24:MI')|"],
    ["day of week", "TO_CHAR(ts, 'Dy')|"],
    ["last 24 h", "ts >= now() - INTERVAL '24 hours'|"],
    ["last 7 days", "ts >= CURRENT_DATE - INTERVAL '7 days'|"],
    ["today", "ts::date = CURRENT_DATE|"],
    ["AGE", "AGE(now(), ts)|"],
    ["difference", "EXTRACT(EPOCH FROM (| - ts))"],
    ["INTERVAL", "INTERVAL '1 hour'|"],
    ["now()", "now()|"],
    ["generate_series", "generate_series(%(from)s, %(to)s, INTERVAL '1 hour') AS hour|"],
    ["in the period", "ts >= %(from)s AND ts < %(to)s|"],
  ],
  Text: [
    ["UPPER / LOWER", "UPPER(|)"],
    ["TRIM", "TRIM(|)"],
    ["LENGTH", "LENGTH(|)"],
    ["SUBSTRING", "SUBSTRING(| FROM 1 FOR 10)"],
    ["LEFT / RIGHT", "LEFT(|, 10)"],
    ["POSITION", "POSITION('|' IN )"],
    ["REPLACE", "REPLACE(|, 'old', 'new')"],
    ["SPLIT_PART", "SPLIT_PART(|, '-', 1)"],
    ["concat ||", "| || ' ' || "],
    ["CONCAT_WS", "CONCAT_WS(' ', |)"],
    ["INITCAP", "INITCAP(|)"],
    ["ILIKE", "ILIKE '%|%'"],
    ["regex ~*", "~* '|'"],
    ["string_agg", "string_agg(|, ', ')"],
    ["::text", "|::text"],
    ["JSON ->>", "| ->> 'key'"],
  ],
  Advanced: [
    ["CASE", "CASE WHEN | THEN 1 ELSE 0 END"],
    ["COALESCE", "COALESCE(|, 0)"],
    ["NULLIF", "NULLIF(|, 0)"],
    ["HAVING", "HAVING COUNT(*) > |"],
    ["FILTER", "COUNT(*) FILTER (WHERE |)"],
    ["DISTINCT ON", "DISTINCT ON (|) "],
    ["JOIN", "JOIN | ON "],
    ["LEFT JOIN", "LEFT JOIN | ON "],
    ["UNION ALL", "UNION ALL\n|"],
    ["EXISTS", "EXISTS (SELECT 1 FROM | WHERE )"],
    ["subquery", "(SELECT | FROM  WHERE )"],
    ["WITH (CTE)", "WITH cte AS (\n  |\n)\nSELECT *\nFROM cte"],
    ["ROW_NUMBER", "ROW_NUMBER() OVER (PARTITION BY | ORDER BY ts DESC)"],
    ["RANK", "RANK() OVER (ORDER BY | DESC)"],
    ["DENSE_RANK", "DENSE_RANK() OVER (ORDER BY | DESC)"],
    ["NTILE", "NTILE(4) OVER (ORDER BY |)"],
    ["LAG / LEAD", "LAG(|) OVER (ORDER BY ts)"],
    ["FIRST_VALUE", "FIRST_VALUE(|) OVER (PARTITION BY  ORDER BY ts)"],
    ["running total", "SUM(|) OVER (ORDER BY ts)"],
    ["moving average", "AVG(|) OVER (ORDER BY ts ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)"],
    ["ARRAY_AGG", "ARRAY_AGG(|)"],
    ["UNNEST", "UNNEST(|)"],
    ["BOOL_OR / AND", "BOOL_OR(|)"],
    ["OFFSET", "OFFSET |0"],
    ["ORDER BY NULLS", "ORDER BY | DESC NULLS LAST"],
  ],
};

//: What the editor completes as you type: the words of the dialect, then the
//: tables and columns this system has (from the catalog). Keywords go in
//: upper case, names as they are.
const SQL_KEYWORDS = [
  "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "ORDER BY", "GROUP BY", "HAVING",
  "LIMIT", "OFFSET", "DISTINCT", "AS", "ASC", "DESC", "LIKE", "ILIKE", "IN", "BETWEEN",
  "IS NULL", "IS NOT NULL", "COUNT", "SUM", "AVG", "MIN", "MAX", "ROUND", "COALESCE",
  "NULLIF", "CASE", "WHEN", "THEN", "ELSE", "END", "JOIN", "LEFT JOIN", "INNER JOIN",
  "ON", "UNION", "UNION ALL", "WITH", "OVER", "PARTITION BY", "ROW_NUMBER", "LAG", "LEAD",
  "FILTER", "EXTRACT", "date_trunc", "now()", "INTERVAL", "generate_series", "string_agg",
  "percentile_cont", "WITHIN GROUP", "EXISTS", "TRUE", "FALSE", "NULL",
  "TRUNC", "FLOOR", "CEIL", "ABS", "MOD", "POWER", "SQRT", "GREATEST", "LEAST", "STDDEV",
  "VARIANCE", "MODE", "CORR", "WIDTH_BUCKET", "TO_CHAR", "CURRENT_DATE", "AGE", "EPOCH",
  "UPPER", "LOWER", "TRIM", "LENGTH", "SUBSTRING", "LEFT", "RIGHT", "POSITION", "REPLACE",
  "SPLIT_PART", "CONCAT_WS", "INITCAP", "DISTINCT ON", "RANK", "DENSE_RANK", "NTILE",
  "FIRST_VALUE", "LAST_VALUE", "ARRAY_AGG", "UNNEST", "BOOL_OR", "BOOL_AND", "NULLS LAST",
  "ROWS BETWEEN", "PRECEDING", "CURRENT ROW",
  "%(from)s", "%(to)s",
];

/** The word under the caret: its start offset and text. "" when the caret
 *  sits after a space or punctuation, which is when nothing is suggested. */
function wordAtCaret(value, at) {
  let i = at;
  while (i > 0 && /[\w%()]/.test(value[i - 1])) i -= 1;
  const word = value.slice(i, at);
  return { start: i, word };
}

function formatSql(sql) {
  let out = String(sql || "").replace(/\s+/g, " ").trim();
  for (const kw of SQL_CLAUSES) {
    out = out.replace(new RegExp(`\\s+${kw}\\b`, "gi"), `\n${kw}`);
  }
  return out;
}

/** A result set as a CSV download. Quotes every field, because a value with
 *  a comma in it is otherwise a row with the wrong number of columns. */
function downloadCsv(res) {
  const cell = (v) => `"${String(v === null || v === undefined ? "" : v)
    .replace(/"/g, '""')}"`;
  const body = [res.columns.map(cell).join(","),
                ...res.rows.map((r) => res.columns.map((c) => cell(r[c])).join(","))]
    .join("\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "query-results.csv";
  a.click();
  URL.revokeObjectURL(url);
}

//: The editor's resizable height (Tok Aba, 2026-10-14): a sensible start, a
//: floor that still shows a few lines, and the line metrics the current-line
//: band is drawn with (theme.css .db-sql-text: 12.5px at 1.6, 12px padding).
const SQL_EDITOR_H = 240;
const SQL_EDITOR_MIN = 160;
const SQL_LINE_H = 20;
const SQL_PAD_TOP = 12;

function SqlConsole({ tables, available, saved = [], onSavedChange, preset }) {
  const [sql, setSql] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [cat, setCat] = useState(null);
  const [ms, setMs] = useState(0);
  // Which pane is under the editor. Results is the answer; Messages is what
  // the run SAID (the error, the truncation note) -- a failed run used to
  // put its message in the same strip as a successful one's row count, so
  // "it worked, no rows" and "it did not run" looked alike.
  const [pane, setPane] = useState("results");
  const [caret, setCaret] = useState({ ln: 1, col: 1 });
  const editorRef = useRef(null);
  // The window %(from)s / %(to)s bind to. The same choices a card's Period
  // offers, plus two dates picked by hand -- so a statement written for a
  // card or a Telegram button can be tried here over the period it will
  // actually run on, not only over "today".
  const [period, setPeriod] = useState("today");
  const [dateFrom, setDateFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [dateTo, setDateTo] = useState(() => new Date().toISOString().slice(0, 10));
  const [snipTab, setSnipTab] = useState("Basic");
  // "Follow Analytics range": the range the Analytics page last showed in
  // this tab (lib/analyticsRange.js), read on mount, when the select is
  // touched, and again at every Run, so a range changed meanwhile is the one
  // used. Sent to the console as period "custom" with its two dates - the
  // console's own inclusive-end day window, the one the cards use.
  const [follow, setFollow] = useState(() => readAnalyticsRange());
  const followWords = follow.set ? analyticsRangeWords(follow) : "today";
  const wantsDates = /%\((from|to)\)s/.test(sql);
  // Autocomplete: what is offered for the word under the caret, and where
  // to draw it. Null = nothing open. Keyboard-first: Up/Down move, Tab or
  // Enter take, Escape closes; typing on keeps filtering.
  const [ac, setAc] = useState(null);      // {items, pick, top, left}

  function suggest(el) {
    if (!el) return;
    const at = el.selectionStart ?? 0;
    if (at !== el.selectionEnd) { setAc(null); return; }
    const { start, word } = wordAtCaret(el.value, at);
    if (!word || word.length < 1) { setAc(null); return; }
    const low = word.toLowerCase();
    const tables = Object.keys(cat?.catalog || {});
    const columns = [];
    for (const [t, cols] of Object.entries(cat?.catalog || {})) {
      for (const c of cols) columns.push({ text: c, hint: t });
    }
    const seen = new Set();
    const items = [];
    const add = (text, hint) => {
      if (!text.toLowerCase().startsWith(low) || text.toLowerCase() === low) return;
      if (seen.has(text)) return;
      seen.add(text); items.push({ text, hint });
    };
    tables.forEach((t) => add(t, "table"));
    columns.forEach((c) => add(c.text, c.hint));
    SQL_KEYWORDS.forEach((k) => add(k, ""));
    if (!items.length) { setAc(null); return; }
    // Where the caret is, in the textarea's own coordinates: the line's
    // bottom edge, and the column times one monospace character. Measured
    // rather than guessed, so a zoomed page still lines up.
    const before = el.value.slice(0, at);
    const lines = before.split("\n");
    const style = getComputedStyle(el);
    const lineH = parseFloat(style.lineHeight) || 20;
    const padT = parseFloat(style.paddingTop) || 0;
    const padL = parseFloat(style.paddingLeft) || 0;
    const ctx = document.createElement("canvas").getContext("2d");
    ctx.font = `${style.fontSize} ${style.fontFamily}`;
    const colX = ctx.measureText(lines[lines.length - 1].slice(0, -word.length)).width;
    setAc({ items: items.slice(0, 8), pick: 0, start,
            top: padT + lines.length * lineH - el.scrollTop,
            left: padL + colX - el.scrollLeft });
  }

  function accept(item) {
    const el = editorRef.current;
    if (!el || !ac) return;
    const at = el.selectionStart ?? 0;
    const text = item.text + (/^[A-Z%]/.test(item.text) && !item.text.endsWith(")") ? " " : "");
    const next = el.value.slice(0, ac.start) + text + el.value.slice(at);
    setSql(next);
    setAc(null);
    const caretAt = ac.start + text.length;
    requestAnimationFrame(() => {
      el.focus();
      try { el.setSelectionRange(caretAt, caretAt); } catch { /* unmounted */ }
      syncCaret(el);
    });
  }

  // Drop text at the caret (replacing a selection), and put the caret where
  // the snippet's `|` says. Through the DOM so the textarea keeps focus and
  // the next click lands where the operator expects; React's value follows.
  function insertAtCursor(text) {
    const el = editorRef.current;
    const at = text.indexOf("|");
    const body = text.replace("|", "");
    if (!el) { setSql((cur) => cur + body); return; }
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? start;
    // A space before, when the caret sits right after a word: "FROM" pasted
    // on to "SELECT *" must not read "SELECT *FROM".
    const before = el.value.slice(0, start);
    const pad = before && !/\s$/.test(before) && !/^\s/.test(body) ? " " : "";
    const next = before + pad + body + el.value.slice(end);
    setSql(next);
    const caretAt = start + pad.length + (at >= 0 ? at : body.length);
    requestAnimationFrame(() => {
      el.focus();
      try { el.setSelectionRange(caretAt, caretAt); } catch { /* unmounted */ }
      syncCaret(el);
    });
  }
  const [tab, setTab] = useState("editor");     // editor | history
  // What has been run this visit, newest first. In memory only: a query
  // history that outlived the tab would be a record of what someone asked
  // the database, stored somewhere nobody agreed to.
  const [history, setHistory] = useState([]);
  const [resSearch, setResSearch] = useState("");
  // What has been KEPT, which is the opposite of history: history is what this
  // tab did, saved queries are what the system knows. They outlive the tab
  // because the workflow palette lists them -- dragging "Busiest zones" onto a
  // canvas is the reason to save one at all.
  const [saveName, setSaveName] = useState("");   // "" = the save row is shut
  const [naming, setNaming] = useState(false);
  const [savingKey, setSavingKey] = useState(null);   // which one is replaced

  const loadSaved = () => Promise.resolve(onSavedChange?.());
  // A query handed over from the rail lands in the editor, replacing what
  // was there: the click on its name IS the choice.
  useEffect(() => {
    if (preset?.sql != null) { setSql(preset.sql); setTab("editor"); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset?.n]);

  async function saveQuery() {
    const name = saveName.trim();
    if (!name) return;
    try {
      await api.post("/api/datasets/queries",
                     { key: savingKey || "", name, sql });
      await loadSaved();
      setNaming(false);
      setSaveName("");
      setSavingKey(null);
    } catch (e) {
      setRes({ ok: false, error: e.message });
      setPane("messages");
    }
  }

  async function forgetQuery(q) {
    try {
      await api.del(`/api/datasets/queries/${q.key}`);
      await loadSaved();
    } catch { /* the list simply stays as it was */ }
  }

  // The caret position for the status bar. Derived from the selection start,
  // so it follows typing, clicking and arrow keys without three handlers.
  function syncCaret(el) {
    if (!el) return;
    const upto = el.value.slice(0, el.selectionStart);
    const lines = upto.split("\n");
    setCaret({ ln: lines.length, col: lines[lines.length - 1].length + 1 });
  }
  const lineCount = Math.max(1, sql.split("\n").length);

  useEffect(() => {
    api.get("/api/datasets/sql/catalog")
      .then(setCat).catch(() => setCat(null));
  }, []);

  useEffect(() => {
    // Open on something that runs, rather than an empty box and a blank look
    // -- unless the rail already handed one over.
    if (!sql && tables.length && preset?.sql == null) {
      setSql(`SELECT *\nFROM ${tables[0].key}\nORDER BY ts DESC`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tables]);

  async function run() {
    setBusy(true);
    setPane("results");
    const started = performance.now();
    let outcome = { ok: false, rows: 0 };
    // The Analytics range is read at the moment of running.
    const fresh = period === "analytics" ? readAnalyticsRange() : null;
    if (fresh) setFollow(fresh);
    const body = fresh ? { sql, period: "custom", from: fresh.start, to: fresh.end }
      : { sql, period, from: dateFrom, to: dateTo };
    try {
      const d = await api.post("/api/datasets/sql", body);
      setRes(d);
      outcome = { ok: !!d.ok, rows: (d.rows || []).length };
      if (!d.ok) setPane("messages");
    } catch (e) {
      setRes({ ok: false, error: e.message });
      setPane("messages");
    } finally {
      setMs(Math.round(performance.now() - started));
      setBusy(false);
      setHistory((cur) => [{ sql, ...outcome,
                             at: new Date().toLocaleTimeString() },
                           ...cur].slice(0, 20));
    }
  }

  // -- layout state (Tok Aba, 2026-10-14 redesign; nothing here reaches the
  //    server) ----------------------------------------------------------------
  // The snippet panel: open beside a wide editor, a toggle on a narrow one.
  const [snipOpen, setSnipOpen] = useState(
    () => typeof window === "undefined" || window.innerWidth > 1024);
  const [snipFilter, setSnipFilter] = useState("");
  // The editor's height, dragged on the handle between it and the results.
  const [editorH, setEditorH] = useState(SQL_EDITOR_H);
  const [scrollTop, setScrollTop] = useState(0);
  const dragRef = useRef(null);
  const setHeight = (h) => setEditorH(Math.max(SQL_EDITOR_MIN,
    Math.min(Math.round(h), Math.round((window.innerHeight || 900) * 0.7))));
  const startDrag = (e) => {
    dragRef.current = { y: e.clientY, h: editorH };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const moveDrag = (e) => {
    if (dragRef.current) setHeight(dragRef.current.h + e.clientY - dragRef.current.y);
  };
  const endDrag = () => { dragRef.current = null; };

  const snipQuery = snipFilter.trim().toLowerCase();
  // A filter searches every shelf at once; without one, the chosen shelf.
  const shownSnips = snipQuery
    ? Object.entries(SQL_SNIPPETS).flatMap(([group, list]) => list
      .filter(([label, text]) => `${label} ${text}`.toLowerCase().includes(snipQuery))
      .map((s) => [...s, group]))
    : SQL_SNIPPETS[snipTab].map((s) => [...s, snipTab]);
  const rowsShown = res?.ok ? res.rows.length : 0;

  const viewTab = (id, words) => (
    <button type="button" role="tab" aria-selected={tab === id}
            className={"db-tab" + (tab === id ? " is-active" : "")}
            onClick={() => setTab(id)}>{words}</button>
  );

  return (
    <div className="db-sql" data-sql-playground="">
      {/* The views of this console, and the snippet panel's toggle. The
          actions that run or keep the statement sit with the editor below. */}
      <div className="db-sql-bar">
        <div role="tablist" aria-label="Query views" className="db-sql-views">
          {viewTab("editor", "Editor")}
          {viewTab("history", `History${history.length ? ` (${history.length})` : ""}`)}
          {viewTab("saved", `Saved${saved.length ? ` (${saved.length})` : ""}`)}
        </div>
        <div style={{ flex: 1 }} />
        <button type="button" className={"btn btn-sm db-sql-snip-toggle" + (snipOpen ? " is-on" : "")}
                aria-expanded={snipOpen} aria-controls="db-sql-snips"
                onClick={() => setSnipOpen((v) => !v)}>
          {"{ }"} Snippets
        </button>
      </div>

      {/* What this console IS, and the period its statements run over, in one
          strip above the text they apply to. */}
      <div className="db-sql-strip" data-sql-strip="">
        <span className="db-sql-badge">Read-only</span>
        <span className="db-sql-fact">This system only</span>
        {cat?.max_rows ? <span className="db-sql-fact">{cat.max_rows} rows max</span> : null}
        <span className="db-sql-strip-sep" aria-hidden="true" />
        {/* The period the statement's %(from)s / %(to)s run over. Shown
            always so the placeholders are discoverable; the hint says what
            it does in words, and the button types the filter in. */}
        <label className="db-sql-period">
          <span className={"small" + (wantsDates ? "" : " muted")}>Period</span>
          <select className="input" value={period}
                  onFocus={() => setFollow(readAnalyticsRange())}
                  onChange={(e) => { setFollow(readAnalyticsRange()); setPeriod(e.target.value); }}>
            <option value="all">all time</option>
            <option value="analytics" data-sql-follow="">
              {follow.set ? `Follow Analytics range: ${followWords}`
                          : "Follow Analytics range (today)"}
            </option>
            <option value="today">today</option>
            <option value="week">this week (7 days)</option>
            <option value="month">this month (30 days)</option>
            <option value="custom">pick dates…</option>
          </select>
        </label>
        {period === "custom" && (
          <span className="db-sql-period">
            <input className="input" type="date" value={dateFrom} max={dateTo}
                   aria-label="From" onChange={(e) => setDateFrom(e.target.value)} />
            <span className="muted small">to</span>
            <input className="input" type="date" value={dateTo} min={dateFrom}
                   aria-label="To" onChange={(e) => setDateTo(e.target.value)} />
          </span>
        )}
        <span className="muted small" data-sql-period-hint="">
          {wantsDates
            ? "Used by this query: the period fills %(from)s and %(to)s when it runs."
            : "Applies only to queries that filter by time."}
        </span>
        {!wantsDates && (
          <button type="button" className="btn btn-sm" data-sql-insert-period=""
                  title="Inserts ts >= %(from)s AND ts < %(to)s at the cursor"
                  onClick={() => insertAtCursor("ts >= %(from)s AND ts < %(to)s|")}>
            Insert period filter
          </button>
        )}
      </div>

      {/* The name is asked for in a row under the bar rather than in a browser
          prompt: the name is what everything downstream lists this query by,
          so it deserves a field you can correct before committing. */}
      {naming && (
        <div className="db-sql-note" style={{ gap: 8 }}>
          <input className="input" autoFocus value={saveName}
                 style={{ maxWidth: 280 }} aria-label="Query name"
                 placeholder="Busiest zones"
                 onChange={(e) => setSaveName(e.target.value)}
                 onKeyDown={(e) => {
                   if (e.key === "Enter") saveQuery();
                   if (e.key === "Escape") { setNaming(false); setSaveName(""); }
                 }} />
          <button className="btn btn-sm btn-accent" disabled={!saveName.trim()}
                  onClick={saveQuery}>
            {savingKey ? "Replace" : "Save"}
          </button>
          <button className="btn btn-sm"
                  onClick={() => { setNaming(false); setSaveName(""); }}>
            Cancel
          </button>
          <span className="muted small">
            {savingKey ? "Replaces the saved query of this name."
              : "Listed on the workflow palette under Database."}
          </span>
        </div>
      )}

      {tab === "saved" && (
        <div className="db-messages db-sql-list" style={{ flex: "0 0 auto" }}>
          {!saved.length && (
            <span className="muted">
              Nothing saved yet - write a SELECT and press Save query. Saved
              ones appear in the workflow palette under Database, ready to drag
              onto a canvas.
            </span>
          )}
          {saved.map((q) => (
            <div key={q.key} style={{ display: "flex", alignItems: "center",
                                      gap: 8 }}>
              <button type="button" className="db-hist" style={{ flex: 1 }}
                      title="Put this back in the editor"
                      onClick={() => { setSql(q.sql); setTab("editor"); }}>
                <span className="db-hist-sql"><b>{q.name}</b> - {q.sql}</span>
                <span className="muted small">
                  {q.tables.length ? `reads ${q.tables.join(", ")}`
                    : "reads no table this system has"}
                </span>
              </button>
              <button type="button" className="btn btn-sm"
                      title="Replace this saved query with the editor's text"
                      onClick={() => { setSavingKey(q.key); setSaveName(q.name);
                                       setNaming(true); }}>
                Replace
              </button>
              <button type="button" className="icon-btn" title="Forget it"
                      aria-label={`Forget ${q.name}`}
                      onClick={() => forgetQuery(q)}>×</button>
            </div>
          ))}
        </div>
      )}

      {tab === "history" && (
        <div className="db-messages db-sql-list" style={{ flex: "0 0 auto" }}>
          {!history.length && <span className="muted">No queries run yet.</span>}
          {history.map((h, i) => (
            <button key={i} type="button" className="db-hist"
                    title="Put this back in the editor"
                    onClick={() => { setSql(h.sql); setTab("editor"); }}>
              <span className="db-hist-sql">{h.sql}</span>
              <span className="muted small">
                {h.ok ? `${h.rows} row${h.rows === 1 ? "" : "s"}` : "failed"}
                {" · "}{h.at}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* What is actually reachable: one chip per table, its columns in the
          tooltip, a click types its name in at the cursor. */}
      <div className="db-sql-tables" data-sql-tables="">
        <span className="db-sql-tables-label">Tables</span>
        {Object.entries(cat?.catalog || {}).map(([name, cols]) => (
          <button key={name} type="button" className="db-sql-chip"
                  title={`Columns: ${cols.join(", ")}`}
                  onClick={() => insertAtCursor(name + "|")}>
            {name}
          </button>
        ))}
        {!Object.keys(cat?.catalog || {}).length && (
          <span className="muted small">No tables yet.</span>
        )}
      </div>

      <div className={"db-sql-work" + (snipOpen ? " has-snips" : "")}>
      <div className="db-sql-main">
      {/* Line numbers beside the text, not inside it: an error that says
          "line 3" needs a line 3 to point at. The gutter scrolls with the
          textarea, which is what keeps them lined up. The current line has a
          faint band behind it, drawn under the (transparent) textarea. */}
      <div className="db-sql-editor" style={{ height: editorH }}>
        <div className="db-sql-gutter" aria-hidden
             ref={(el) => { if (el && editorRef.current) el.scrollTop = editorRef.current.scrollTop; }}>
          {Array.from({ length: lineCount }, (_, i) => (
            <div key={i} className={i + 1 === caret.ln ? "is-cur" : undefined}>{i + 1}</div>
          ))}
        </div>
        <div className="db-sql-textwrap">
          <div className="db-sql-curline" aria-hidden
               style={{ top: SQL_PAD_TOP + (caret.ln - 1) * SQL_LINE_H - scrollTop }} />
        <textarea className="db-sql-text" value={sql} spellCheck={false}
                  ref={editorRef} aria-label="SQL statement"
                  onChange={(e) => { setSql(e.target.value); syncCaret(e.target); suggest(e.target); }}
                  onKeyUp={(e) => { syncCaret(e.target);
                                    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) suggest(e.target); }}
                  onClick={(e) => { syncCaret(e.target); setAc(null); }}
                  onBlur={() => setTimeout(() => setAc(null), 120)}
                  onScroll={(e) => {
                    const g = e.target.parentElement?.previousSibling;
                    if (g) g.scrollTop = e.target.scrollTop;
                    setScrollTop(e.target.scrollTop);
                    setAc(null);
                  }}
                  onKeyDown={(e) => {
                    // Ctrl/Cmd+Enter runs, the way every SQL tool does it.
                    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                      e.preventDefault(); run(); return;
                    }
                    if (!ac) {
                      // Ctrl+Space asks for suggestions where nothing was typed yet.
                      if (e.key === " " && e.ctrlKey) { e.preventDefault(); suggest(e.target); }
                      return;
                    }
                    if (e.key === "ArrowDown") {
                      e.preventDefault(); setAc((a) => ({ ...a, pick: (a.pick + 1) % a.items.length }));
                    } else if (e.key === "ArrowUp") {
                      e.preventDefault(); setAc((a) => ({ ...a, pick: (a.pick - 1 + a.items.length) % a.items.length }));
                    } else if (e.key === "Tab" || e.key === "Enter") {
                      e.preventDefault(); accept(ac.items[ac.pick]);
                    } else if (e.key === "Escape") {
                      e.preventDefault(); setAc(null);
                    }
                  }}
                  placeholder="SELECT * FROM your_table" />
        </div>
        {ac && (
          <div className="db-ac" role="listbox"
               style={{ top: ac.top, left: Math.min(ac.left, 480) + 44 }}>
            {ac.items.map((it, i) => (
              <button key={it.text} type="button" role="option"
                      aria-selected={i === ac.pick}
                      className={"db-ac-item" + (i === ac.pick ? " is-pick" : "")}
                      onMouseDown={(e) => { e.preventDefault(); accept(it); }}
                      onMouseEnter={() => setAc((a) => ({ ...a, pick: i }))}>
                <span>{it.text}</span>
                {it.hint && <span className="muted small">{it.hint}</span>}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* The editor's own line: where the caret is on the left, and the three
          things done TO this text on the right - tidy it, keep it, run it. */}
      <div className="db-sql-status" data-sql-actions="">
        <span className="db-sql-pos muted">Ln {caret.ln}, Col {caret.col}</span>
        <span className="db-sql-pos muted">{lineCount} line{lineCount === 1 ? "" : "s"} · SQL</span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn btn-sm"
                disabled={busy || !sql.trim()}
                title="Put each clause on its own line"
                onClick={() => setSql(formatSql(sql))}>Format SQL</button>
        {/* Save beside Run because it acts on the same text. A saved query
            is not a result -- it is the STATEMENT, kept under a name, and
            the workflow palette lists it there. */}
        <button className="btn btn-sm" disabled={busy || !sql.trim()}
                title="Keep this statement under a name - the workflow palette lists it"
                onClick={() => { setSavingKey(null); setNaming(true); }}>
          Save query
        </button>
        <button className="btn btn-sm btn-accent btn-ic db-sql-run" onClick={run}
                title="Run the statement (Ctrl/⌘ + Enter)"
                aria-keyshortcuts="Control+Enter Meta+Enter"
                disabled={busy || !available || !sql.trim()}>
          {busy ? "Running…" : "▶ Run query"}
          <kbd className="db-sql-kbd" aria-hidden="true">Ctrl+Enter</kbd>
        </button>
      </div>

      {/* The split: drag (or arrow keys) to give the editor or the results
          more room. The results always take what is left. */}
      <div className="db-sql-split" role="separator" aria-orientation="horizontal"
           aria-label="Resize the editor" tabIndex={0} data-sql-split=""
           aria-valuemin={SQL_EDITOR_MIN} aria-valuenow={editorH}
           onPointerDown={startDrag} onPointerMove={moveDrag}
           onPointerUp={endDrag} onPointerCancel={endDrag}
           onKeyDown={(e) => {
             if (e.key === "ArrowUp") { e.preventDefault(); setHeight(editorH - 24); }
             if (e.key === "ArrowDown") { e.preventDefault(); setHeight(editorH + 24); }
           }}>
        <span aria-hidden="true" />
      </div>

      {/* Results and Messages as TABS: a run that failed and a run that
          returned nothing are different answers. The bar states the outcome,
          so the answer is readable without opening either. */}
      <div className="db-restabs" data-sql-results-head="">
        <div role="tablist" aria-label="Run output" className="db-sql-views">
          <button type="button" role="tab" aria-selected={pane === "results"}
                  className={"db-tab" + (pane === "results" ? " is-active" : "")}
                  onClick={() => setPane("results")}>
            Results{res?.ok ? ` (${res.rows.length})` : ""}
          </button>
          <button type="button" role="tab" aria-selected={pane === "messages"}
                  className={"db-tab" + (pane === "messages" ? " is-active" : "")}
                  onClick={() => setPane("messages")}>
            Messages{res && !res.ok ? " (1)" : ""}
          </button>
        </div>
        {res && (
          <span className="db-restabs-out" data-sql-outcome="">
            {res.ok
              ? <><span className="db-ok">✓</span>
                  <b>{rowsShown} row{rowsShown === 1 ? "" : "s"}</b>
                  <span className="muted">· {(ms / 1000).toFixed(2)} s</span>
                  {res.truncated && (
                    <span className="db-sql-trunc" data-sql-truncated="">
                      · Showing the first {cat?.max_rows || rowsShown} rows
                    </span>
                  )}
                  {res.bound && <span className="muted">· {res.bound}</span>}</>
              : <span className="db-bad">✕ Failed</span>}
          </span>
        )}
        <div style={{ flex: 1 }} />
        {res?.ok && res.rows.length > 0 && (<>
          <input className="input db-res-search" placeholder="Search results…"
                 aria-label="Search results"
                 value={resSearch} onChange={(e) => setResSearch(e.target.value)} />
          <button type="button" className="btn btn-sm" onClick={() => downloadCsv(res)}
                  title="Download these rows as CSV">⭳ Download</button>
        </>)}
      </div>

      {pane === "messages" && (
        <div className="db-messages" data-sql-messages="">
          {!res && <span className="muted">Nothing run yet.</span>}
          {res?.ok && (
            <div className="db-sql-msg">
              <div><span className="db-ok">✓ Success</span> - {res.rows.length} row
                {res.rows.length === 1 ? "" : "s"} in {(ms / 1000).toFixed(2)} s</div>
              {res.bound && (
                <div className="muted" style={{ marginTop: 6 }}>
                  This statement takes a period, so it ran {res.bound} - the
                  Period picker above. A Telegram button or a Watch supplies its
                  own window.
                </div>
              )}
              {res.truncated && (
                <div className="db-trunc" style={{ marginTop: 6 }}>
                  Showing the first {cat?.max_rows} rows - narrow the query, or
                  use Export CSV for the whole table.
                </div>
              )}
            </div>
          )}
          {res && !res.ok && (
            <div className="db-sql-msg is-bad" role="alert" data-sql-error="">
              <div className="db-sql-msg-title">The query did not run</div>
              <pre className="db-sql-msg-text">{res.error}</pre>
            </div>
          )}
        </div>
      )}

      <div className="db-grid-wrap db-sql-results" hidden={pane !== "results"}>
        {!res && (
          <div className="db-empty" data-sql-empty="">
            <div className="db-empty-title">Run a query to see results</div>
            <p className="muted">
              Press <b>Run query</b> or <kbd className="db-sql-kbd">Ctrl+Enter</kbd> -
              the rows appear here.
            </p>
          </div>
        )}
        {res?.ok && res.rows.length > 0 && (
          <table className="db-grid db-sql-grid">
            <thead>
              <tr>
                <th className="db-pick muted">#</th>
                {res.columns.map((c) => <th key={c}>{c}</th>)}
                <th className="db-filler" />
              </tr>
            </thead>
            <tbody>
              {/* The search narrows what is SHOWN, not what was fetched --
                  the row numbers stay the query's own, so a filtered view
                  never renumbers the answer. */}
              {res.rows.map((r, i) => [r, i]).filter(([r]) => {
                const q = resSearch.trim().toLowerCase();
                return !q || res.columns.some(
                  (c) => String(r[c] ?? "").toLowerCase().includes(q));
              }).map(([r, i]) => (
                <tr key={i}>
                  <td className="muted" style={{ fontSize: 10.5 }}>{i + 1}</td>
                  {res.columns.map((c) => (
                    <td key={c} className={"db-cell" + (typeof r[c] === "number" ? " db-num" : "")}>
                      {r[c] === null || r[c] === undefined
                        ? <span className="db-null">null</span>
                        : typeof r[c] === "object" ? JSON.stringify(r[c])
                        : String(r[c])}
                    </td>
                  ))}
                  <td className="db-filler" />
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {res?.ok && !res.rows.length && (
          <div className="muted" style={{ padding: 20, textAlign: "center" }}>
            No rows matched.
          </div>
        )}
      </div>
      </div>

        {/* The words of SQL, in a panel of their own: tabs per shelf, a
            filter across all of them, and each chip says what it inserts. */}
        {snipOpen && (
          <aside id="db-sql-snips" className="db-sql-snips" aria-label="Snippets"
                 data-sql-snips="">
            <div className="db-sql-snips-head">
              <strong className="small">Snippets</strong>
              <div style={{ flex: 1 }} />
              <button type="button" className="icon-btn" aria-label="Close snippets"
                      onClick={() => setSnipOpen(false)}>×</button>
            </div>
            <span className="muted small" style={{ marginTop: -6 }}>
              Click one to insert it at the cursor.
            </span>
            <input className="input db-sql-snips-filter" placeholder="Filter snippets…"
                   aria-label="Filter snippets" value={snipFilter}
                   onChange={(e) => setSnipFilter(e.target.value)} />
            {!snipQuery && (
              <div className="db-sql-snips-tabs" role="tablist" aria-label="Snippet groups">
                {Object.keys(SQL_SNIPPETS).map((k) => (
                  <button key={k} type="button" role="tab" aria-selected={snipTab === k}
                          className={"db-sql-snips-tab" + (snipTab === k ? " is-on" : "")}
                          onClick={() => setSnipTab(k)}>{k}</button>
                ))}
              </div>
            )}
            <div className="db-sql-snips-chips">
              {shownSnips.map(([label, text, group]) => (
                <button key={`${group}-${label}`} type="button" className="db-sql-chip"
                        title={`Inserts: ${text.replace("|", "")}`}
                        onClick={() => insertAtCursor(text)}>
                  {label}
                </button>
              ))}
              {!shownSnips.length && <span className="muted small">No snippet matches.</span>}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

/** One cell. Double-click to edit; Enter saves, Escape abandons. */
function Cell({ value, type, editable, editing, onEdit, onCancel, onSave }) {
  const ref = useRef(null);
  useEffect(() => { if (editing) ref.current?.focus(); }, [editing]);

  if (editing) {
    return (
      <td className="db-cell is-editing">
        <input ref={ref} className="db-cell-input"
               defaultValue={value === null || value === undefined ? "" : String(value)}
               type={type === "number" ? "number" : "text"}
               onBlur={(e) => onSave(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key === "Enter") { e.preventDefault(); onSave(e.target.value); }
                 if (e.key === "Escape") { e.preventDefault(); onCancel(); }
               }} />
      </td>
    );
  }
  // A boolean is a tick, not the word "true". It is also the one type you can
  // set in a single click, so it toggles in place rather than opening an
  // editor to type into.
  if (type === "boolean") {
    return (
      <td className="db-cell db-bool">
        <input type="checkbox" checked={value === true} disabled={!editable}
               onChange={(e) => onSave(e.target.checked ? "true" : "false")} />
      </td>
    );
  }
  const text = show(value, type);
  return (
    <td className={"db-cell" + (type === "number" ? " db-num" : "")}
        tabIndex={0}
        title={editable ? "Double-click to edit" : undefined}
        onDoubleClick={onEdit}
        onKeyDown={(e) => { if (e.key === "Enter" && editable) onEdit(); }}>
      {type === "image" && text
        ? <img src={text} alt="" className="db-thumb" />
        : text || <span className="db-null">null</span>}
    </td>
  );
}

/** Name it, give it columns. No PostgreSQL options, no SQL. */
function NewTableDialog({ open, onOpenChange, onCreate, busy, existing }) {
  const [name, setName] = useState("");
  const [cols, setCols] = useState([{ name: "", type: "text" }]);
  useEffect(() => {
    if (open) { setName(""); setCols([{ name: "", type: "text" }]); }
  }, [open]);
  if (!open) return null;

  const key = slugify(name);
  const clean = cols.map((c) => ({ ...c, name: slugify(c.name) }))
    .filter((c) => c.name);
  const dupes = new Set(clean.map((c) => c.name)).size !== clean.length;
  const taken = existing.some((t) => t.key === key);
  const ok = key && /^[a-z]/.test(key) && clean.length && !dupes && !taken;

  // Portalled to the body for the reason spelled out on Dialog in
  // ui/primitives.jsx: any transformed ancestor turns `inset: 0` into
  // the height of the PAGE, and a centred dialog then sits halfway
  // down a long page rather than in the middle of the window.
  return createPortal(
    <div className="db-modal-back" onClick={() => onOpenChange(false)}>
      <div className="db-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="db-modal-title">New table</h3>
        <label className="db-label">Table name</label>
        <input className="input" autoFocus value={name} maxLength={60}
               placeholder="Inspection result"
               onChange={(e) => setName(e.target.value)} />
        {key && <div className="muted small mono" style={{ marginTop: 4 }}>{key}</div>}
        {taken && <div className="db-warn">There is already a table called {key}.</div>}

        <label className="db-label" style={{ marginTop: 14 }}>Columns</label>
        {cols.map((c, i) => (
          <div className="db-colrow" key={i}>
            <input className="input" placeholder="column name" value={c.name}
                   maxLength={32}
                   onChange={(e) => setCols((cur) => cur.map((x, j) => (j === i
                     ? { ...x, name: e.target.value } : x)))} />
            <select className="input" value={c.type}
                    onChange={(e) => setCols((cur) => cur.map((x, j) => (j === i
                      ? { ...x, type: e.target.value } : x)))}>
              {TYPES.map(([id, label]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
            <button className="btn btn-sm"
                    onClick={() => setCols((cur) => cur.filter((_, j) => j !== i))}>
              ✕
            </button>
          </div>
        ))}
        <button className="btn btn-sm" style={{ marginTop: 6 }}
                onClick={() => setCols((cur) => [...cur, { name: "", type: "text" }])}>
          + Add Column
        </button>
        {dupes && <div className="db-warn">Two columns share a name.</div>}

        <div className="db-modal-foot">
          <button className="btn" onClick={() => onOpenChange(false)}>Cancel</button>
          <button className="btn btn-accent" disabled={!ok || busy}
                  onClick={() => onCreate({ key, name: name.trim(), columns: clean })}>
            Create table
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Rename the table, rename columns, change types, drop columns. */
function SchemaDialog({ open, onOpenChange, table, columns, onSave, onDelete,
                        busy, rows }) {
  const confirm = useConfirm();
  const [name, setName] = useState("");
  const [cols, setCols] = useState([]);
  useEffect(() => {
    if (open && table) { setName(table.name); setCols(columns.map((c) => ({ ...c }))); }
  }, [open, table, columns]);
  if (!open || !table) return null;

  const clean = cols.map((c) => ({ name: slugify(c.name), type: c.type }))
    .filter((c) => c.name);
  const dupes = new Set(clean.map((c) => c.name)).size !== clean.length;

  // Portalled to the body for the reason spelled out on Dialog in
  // ui/primitives.jsx: any transformed ancestor turns `inset: 0` into
  // the height of the PAGE, and a centred dialog then sits halfway
  // down a long page rather than in the middle of the window.
  return createPortal(
    <div className="db-modal-back" onClick={() => onOpenChange(false)}>
      <div className="db-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="db-modal-title">Edit table</h3>
        <label className="db-label">Table name</label>
        <input className="input" value={name} maxLength={60}
               onChange={(e) => setName(e.target.value)} />
        {/* The key rows are filed under is not restated here. It never
            changes, it is not editable, and a line of small print under
            every open of this dialog is a fact the operator learns once
            and then reads for ever. It is still on the table's own card. */}
        <label className="db-label" style={{ marginTop: 14 }}>Columns</label>
        {/* The order here IS the order everywhere the table is read -- the
            grid, exports, the workflow node's column ports. Arrows rather
            than drag: two buttons need no library and no gesture to learn,
            and a schema rarely has more than a handful of rows to walk. */}
        {cols.map((c, i) => (
          <div className="db-colrow" key={i}>
            <span className="db-colmove">
              <button type="button" title="Move up" disabled={i === 0}
                      onClick={() => setCols((cur) => {
                        const next = [...cur];
                        [next[i - 1], next[i]] = [next[i], next[i - 1]];
                        return next;
                      })}>▲</button>
              <button type="button" title="Move down"
                      disabled={i === cols.length - 1}
                      onClick={() => setCols((cur) => {
                        const next = [...cur];
                        [next[i], next[i + 1]] = [next[i + 1], next[i]];
                        return next;
                      })}>▼</button>
            </span>
            <input className="input" value={c.name} maxLength={32}
                   onChange={(e) => setCols((cur) => cur.map((x, j) => (j === i
                     ? { ...x, name: e.target.value } : x)))} />
            <select className="input" value={c.type}
                    onChange={(e) => setCols((cur) => cur.map((x, j) => (j === i
                      ? { ...x, type: e.target.value } : x)))}>
              {TYPES.map(([id, label]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
            <button className="btn btn-sm" title="Remove column"
                    onClick={async () => {
                      // Values already recorded are kept -- removing a column
                      // hides it, and adding the same name back brings that
                      // history straight back.
                      if (rows && !await confirm({
                        title: `Remove "${c.name}"?`,
                        description: "Existing values are kept, just hidden. "
                          + "Re-add the same name to bring them back.",
                        confirmLabel: "Remove" })) return;
                      setCols((cur) => cur.filter((_, j) => j !== i));
                    }}>✕</button>
          </div>
        ))}
        <button className="btn btn-sm" style={{ marginTop: 6 }}
                onClick={() => setCols((cur) => [...cur, { name: "", type: "text" }])}>
          + Add Column
        </button>
        {dupes && <div className="db-warn">Two columns share a name.</div>}

        <div className="db-modal-foot">
          <button className="btn" onClick={onDelete}>Delete table</button>
          <div style={{ flex: 1 }} />
          <button className="btn" onClick={() => onOpenChange(false)}>Cancel</button>
          <button className="btn btn-accent" disabled={dupes || busy}
                  onClick={() => onSave({ key: table.key, name: name.trim() || table.key,
                                          columns: clean })}>
            Save
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
