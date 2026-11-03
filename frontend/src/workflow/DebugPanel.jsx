// Debug - "is this graph actually doing anything?"
//
// A node graph looks correct long before it behaves correctly. Answering that
// question used to mean three places at once: the node badges for compile
// errors, the Analytics page for the values, and the Events page for whether
// anything fired. This panel puts all three beside the canvas that produced
// them, so a change and its consequence are visible in one glance.
//
// Everything here is read-only and derived - there is nothing to save, so
// opening Debug can never affect what runs.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import { aiOf } from "../components/AiDot.jsx";

const EVENT_POLL_MS = 2000;

// A number a person can read at a glance. Thousands are grouped, because
// 85394 and 8539 are the same SHAPE at this size, and the difference between
// them is the whole point of looking.
function fmt(v, metric) {
  if (v === undefined || v === null) return "-";
  if (typeof v !== "number") return String(v);
  if (isDuration(metric)) return fmtDuration(v);
  const n = Number.isInteger(v) ? v : Math.round(v * 100) / 100;
  return n.toLocaleString();
}

// Metrics counted in SECONDS. A Schedule's next_s read 85394, which is a
// number nobody converts in their head -- it is 23h 43m, and that is the
// answer to the question somebody opened Debug to ask.
function isDuration(metric) {
  return typeof metric === "string"
    && (metric.endsWith("_s") || metric === "elapsed");
}

function fmtDuration(v) {
  const s = Math.max(0, Math.round(v));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

function when(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString();
}

export default function DebugPanel({ open, onClose, functions, values, appRunning,
                                     errors, warnings, nodes, onSelectNode,
                                     labelFor, hintFor, fnOwners,
                                     aiStatus, waitsForDetection }) {
  // Detection off, and this heading's node is one the app holds for it: the
  // row is empty because nobody is watching the scene, not because the
  // graph is broken (the same sentence the Debug probe says, nodes.jsx).
  const heldByAi = (nodeId) => !!(aiStatus && !aiOf(aiStatus).ok && nodeId
                                  && waitsForDetection && waitsForDetection.has(nodeId));
  const [events, setEvents] = useState([]);
  const [tab, setTab] = useState("values");
  // A graph with thirty rules publishes a hundred numbers. Pinning answers
  // "the three I am tuning"; this answers "the one I am looking for right
  // now", which is a different question and a more common one.
  const [q, setQ] = useState("");
  // "Only the ones I starred." A graph with thirty rules publishes a hundred
  // numbers, and the three being tuned are somewhere in the scroll.
  const [pinnedOnly, setPinnedOnly] = useState(false);

  const loadEvents = useCallback(() => {
    api.get("/api/events?limit=50")
      .then((d) => setEvents(d.events || d.rows || []))
      .catch(() => {});
  }, []);

  useEffect(() => { if (open) loadEvents(); }, [open, loadEvents]);
  // Paused while the tab is hidden, refetched on return (useVisibleInterval).
  useVisibleInterval(() => { if (open) loadEvents(); }, EVENT_POLL_MS);

  if (!open) return null;

  const problems = [
    ...Object.entries(errors || {}).map(([id, m]) => ({ id, message: m, kind: "error" })),
    ...Object.entries(warnings || {}).map(([id, m]) => ({ id, message: m, kind: "warning" })),
  ];
  // fnId -> the node that produced it, so a row can jump to its node -- and
  // whether that node is starred, which decides the order below.
  const nodeByFn = {};
  const pinnedFns = new Set();
  for (const n of nodes || []) {
    const fid = (n.data || {}).fnId;
    if (!fid) continue;
    nodeByFn[fid] = n.id;
    if (n.data?.pinned) pinnedFns.add(fid);
  }
  // ...and the nodes whose compiled function is NOT named after their fnId.
  // A Set Variable's rule takes the name of the variable it writes and a
  // wired Variable publishes under its own name, so neither could ever match
  // the loop above: their headings showed with a dead jump arrow, and a star
  // on either node pinned a function id that never appeared. Resolved by the
  // page (fnOwners), which can see the edges these ids come from.
  const nodeIds = new Set((nodes || []).map((n) => n.id));
  for (const [fid, id] of Object.entries(fnOwners || {})) {
    if (!nodeIds.has(id)) continue;
    nodeByFn[fid] = id;
    const owner = (nodes || []).find((n) => n.id === id);
    if (owner?.data?.pinned) pinnedFns.add(fid);
  }
  const label = (metric) => (labelFor ? labelFor(metric) : metric);
  const hint = (metric) => (hintFor ? hintFor(metric) : "");
  const needle = q.trim().toLowerCase();
  // Starred first, ALWAYS -- the toggle only decides whether the rest are
  // shown at all. Pinning something and having it stay where it was is
  // pinning that did nothing.
  const metricsOfFn = (fn) => (fn.metrics || Object.keys(values)
    .filter((k) => k.startsWith(`fn_${fn.id}_`))
    .map((k) => k.slice(`fn_${fn.id}_`.length)));
  const rows = (functions || [])
    .filter((fn) => !pinnedOnly || pinnedFns.has(fn.id))
    .filter((fn) => !needle
      || String(fn.name || fn.id).toLowerCase().includes(needle)
      || metricsOfFn(fn).some((m) => String(label(m)).toLowerCase().includes(needle)))
    .slice()
    .sort((a, b) => (pinnedFns.has(b.id) ? 1 : 0) - (pinnedFns.has(a.id) ? 1 : 0));
  // One node, one card. A branch node compiles to a function PER WAY OUT, each
  // naming itself "If / Else if - branch 1", "- branch 2", "- otherwise":
  // three top-level rows for one node on the canvas, which reads as three
  // nodes. Grouped back under the name they share, the three numbers sit
  // together -- and exactly one of them is 1 at any moment, which IS the
  // branch being taken and the thing somebody opened Debug to see.
  const groups = [];
  const byTitle = new Map();
  const SEP = " \u00b7 ";
  for (const fn of rows) {
    const whole = String(fn.name || fn.id);
    const cut = whole.indexOf(SEP);
    const title = cut > 0 ? whole.slice(0, cut) : whole;
    const part = cut > 0 ? whole.slice(cut + SEP.length) : "";
    let g = byTitle.get(title);
    if (!g) {
      g = { title, pinned: false, items: [] };
      byTitle.set(title, g);
      groups.push(g);
    }
    g.pinned = g.pinned || pinnedFns.has(fn.id);
    g.items.push({ fn, part });
  }

  const TABS = [
    ["values", `Values (${functions.length})`],
    ["events", `Events (${events.length})`],
    ["problems", `Problems (${problems.length})`],
  ];

  return (
    <div style={{
      position: "absolute", right: 8, top: 8, bottom: 8, width: 340, zIndex: 15,
      display: "flex", flexDirection: "column",
      background: "var(--surface2, #1d2026)", borderRadius: 10,
      border: "1px solid rgba(128,128,128,.3)", boxShadow: "0 6px 24px rgba(0,0,0,.4)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px",
                    borderBottom: "1px solid rgba(128,128,128,.2)" }}>
        <strong style={{ flex: 1 }}>Debug</strong>
        <span className="muted small" title={appRunning
          ? "The detection app is up - values below are live"
          : "The detection app is not running, so nothing is publishing values"}>
          <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 999,
                         marginRight: 5,
                         background: appRunning ? "#10b981" : "#9ca3af" }} />
          {appRunning ? "live" : "app stopped"}
        </span>
        <button className="btn btn-sm" onClick={onClose}>✕</button>
      </div>

      <div className="filter-bar" style={{ gap: 4, padding: "6px 8px" }}>
        {/* A tab you have to open to learn something is wrong is a tab that
            gets opened once. Problems colours itself when it HAS any, so the
            panel says so from whichever tab you happen to be on. */}
        {TABS.map(([id, label]) => (
          <button key={id} className={"btn btn-sm" + (tab === id ? " btn-accent" : "")}
                  style={id === "problems" && problems.length && tab !== id
                    ? { color: "var(--danger, #ef4444)", borderColor: "#ef444466" } : undefined}
                  onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "0 10px 10px" }}>
        {tab === "values" && (
          <>
            {/* One row: both of these narrow the same list, so they sit on
                the same line. The checkbox used to have a bordered bar to
                itself with its hint pushed to the far edge -- two lines of
                chrome above the search box, and a stranded sentence a
                tooltip says better. */}
            <div style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 0" }}>
              <input className="input" placeholder="Find a value..." value={q}
                     style={{ flex: 1, minWidth: 0 }}
                     onChange={(e) => setQ(e.target.value)} />
              <label className="muted small"
                     title={pinnedFns.size
                       ? "Show only the nodes you starred"
                       : "star a node to pin it"}
                     style={{ display: "flex", alignItems: "center", gap: 6,
                              whiteSpace: "nowrap", cursor: "pointer",
                              opacity: pinnedFns.size ? 1 : 0.6 }}>
                <input type="checkbox" checked={pinnedOnly}
                       style={{ margin: 0 }}
                       onChange={(e) => setPinnedOnly(e.target.checked)} />
                Pinned only{pinnedFns.size ? ` (${pinnedFns.size})` : ""}
              </label>
            </div>
            {groups.length ? groups.map((g) => (
            <div key={g.title} style={{ padding: "7px 0",
                                        borderBottom: "1px solid rgba(128,128,128,.12)" }}>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                {g.pinned && (
                  <span style={{ color: "var(--warn-text)", flexShrink: 0 }}>*</span>
                )}
                {/* The NAME first. The jump button used to take the left edge,
                    so no two names on the panel started in the same place and
                    the list could not be scanned down. */}
                <strong style={{ flex: 1, minWidth: 0, overflow: "hidden",
                                 textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={g.title}>
                  {g.title}
                </strong>
                <button className="btn btn-sm" style={{ padding: "1px 6px", flexShrink: 0 }}
                        disabled={!nodeByFn[g.items[0].fn.id]}
                        title={nodeByFn[g.items[0].fn.id]
                          ? "Show the node that publishes this" : ""}
                        onClick={() => onSelectNode(nodeByFn[g.items[0].fn.id])}>&#8599;</button>
              </div>
              {g.items.map(({ fn, part }) => {
                const ms = metricsOfFn(fn);
                return ms.map((metric, i) => (
                  <div key={`${fn.id}-${metric}`}
                       style={{ display: "flex", gap: 8, alignItems: "baseline",
                                padding: "1px 0" }}>
                    {/* The node's own word for this port, not the stat key:
                        "net" and "Inside" were the same tally under two names,
                        and only one of them is anybody's. The DESCRIPTION is
                        the tooltip -- printed on the row it filled the panel
                        with one repeated paragraph. */}
                    <span className="muted small"
                          style={{ flex: 1, minWidth: 0, overflow: "hidden",
                                   textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          title={hint(metric) ? `${hint(metric)}\n${metric}` : metric}>
                      {part && i === 0 ? part : ""}
                      {part && i === 0 && ms.length > 1 ? " - " : ""}
                      {(!part || ms.length > 1) ? label(metric) : ""}
                    </span>
                    <span className="mono"
                          style={{ flexShrink: 0, fontSize: 12.5, fontWeight: 600,
                                   color: values[`fn_${fn.id}_${metric}`] === undefined
                                     ? "var(--muted, #8b93a1)"
                                     : "var(--accent-text, #04bade)" }}>
                      {fmt(values[`fn_${fn.id}_${metric}`], metric)}
                    </span>
                  </div>
                ));
              })}
              {/* A HEADING WITH NOTHING UNDER IT. The rows are reverse
                  engineered from the live stats by prefix (metricsOfFn), so a
                  rule that has published nothing yet -- the app is down, or
                  it has not run a first tick -- renders as a name and a gap,
                  which reads as a broken node rather than as a quiet one.
                  Reported as a Set Variable "showing no value": it was doing
                  exactly this. */}
              {!g.items.some(({ fn }) => metricsOfFn(fn).length) && (
                <div className="muted small" style={{ padding: "1px 0" }}>
                  {!appRunning ? "the app is stopped - nothing is publishing"
                    : heldByAi(nodeByFn[g.items[0].fn.id])
                      ? "AI off - waits for detection"
                      : "nothing published yet"}
                </div>
              )}
            </div>
            )) : (
              <p className="muted small" style={{ paddingTop: 8 }}>
                {needle
                  ? `Nothing here matches "${q.trim()}".`
                  : pinnedOnly
                    ? "Nothing pinned yet - click the star on a node's header."
                    : "This graph publishes nothing yet. Wire a zone into an analytics node."}
              </p>
            )}
          </>
        )}

        {tab === "events" && (
          events.length ? events.map((e, i) => (
            <div key={e.id || i} style={{ padding: "5px 0",
                                          borderBottom: "1px solid rgba(128,128,128,.12)" }}>
              <div style={{ display: "flex", gap: 6 }}>
                <span className="muted small" style={{ width: 62, flexShrink: 0 }}>
                  {when(e.ts)}
                </span>
                <span className="small" style={{ flex: 1, minWidth: 0 }}>
                  {e.message || e.label || e.type}
                </span>
                {e.severity && (
                  <span className="mono small"
                        style={{ color: e.severity === "crit" ? "#ef4444"
                          : e.severity === "warn" ? "#f59e0b" : undefined }}>
                    {e.severity}
                  </span>
                )}
              </div>
            </div>
          )) : (
            <p className="muted small" style={{ paddingTop: 8 }}>
              Nothing has fired yet. An alert appears here the moment its
              trigger is met - usually within a second or two.
            </p>
          )
        )}

        {tab === "problems" && (
          problems.length ? problems.map((p, i) => (
            <div key={`${p.id}-${i}`} style={{ padding: "6px 0",
                                               borderBottom: "1px solid rgba(128,128,128,.12)" }}>
              <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                <button className="btn btn-sm" style={{ padding: "1px 6px" }}
                        title="Show this node" onClick={() => onSelectNode(p.id)}>↗</button>
                <span className="small" style={{ flex: 1,
                  color: p.kind === "error" ? "#ef4444" : "#f59e0b" }}>
                  {p.message}
                </span>
              </div>
            </div>
          )) : (
            <p className="muted small" style={{ paddingTop: 8 }}>
              No compile problems - this graph is being applied to the running app.
            </p>
          )
        )}
      </div>
    </div>
  );
}
