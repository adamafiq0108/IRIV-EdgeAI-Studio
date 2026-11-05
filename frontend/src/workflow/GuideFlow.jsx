// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The main guideline of the Workflow, as a slider. It tells the workflow of the template
// the system was created from (workflow/GuideFlows.js has one flow per template and a
// generic one for Blank Canvas): each step adds one part to the picture (ringed in red)
// and says, in plain words, what it is, where to find it and what it does; the last
// steps leave the canvas and show where its numbers go -- the Builder's "Dashboard
// cards" list, then the finished page.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Welcome } from "./GuideWelcome.jsx";
import { ChartColumn, ChevronDown, Cpu, Film, LayoutDashboard, Play, Square } from "lucide-react";
import { WorkflowCardsPalette } from "../components/widgets/builderShared.jsx";
import { Diagram } from "./GuideDiagram.jsx";
import { api } from "../api/client.js";
import { flowFor } from "./GuideFlows.js";
import GuideMove from "./GuideMoves.jsx";

//: What the pictures show when a caller (a page guide) names no cards of its own.
const SAMPLE_CARDS = [
  { origin: "card", kind: "tile", title: "People Counter", node: "guide_people", key: "" },
  { origin: "card", kind: "tile", title: "Total Violations (Day)", node: "guide_viol", key: "" },
  { origin: "card", kind: "tile", title: "Peak Hour", node: "guide_peak", key: "" },
];
const CHART_CARDS = [
  { origin: "card", kind: "line", title: "People Over Time", node: "guide_chart", key: "" },
];

/** The node list's Storage drawer, with the table that was made on the Database page. */
export function DbPaletteSnapshot() {
  return (
    <div className="ng-snap ng-pg-shot" data-ng-snap="db-palette" style={{ maxWidth: 320 }}>
      <span className="small muted">STORAGE</span>
      <span className="input">Database</span>
      <span className="small muted">DATA TABLES (1)</span>
      <span className="input ng-new">people_log</span>
      <span className="small muted">ASK OF THEM</span>
      <span className="input">Query</span>
      <span className="input">CSV File</span>
    </div>
  );
}

/** A chart card, drawn as a line over time. */
export function ChartCard({ hot = false, title = "People Over Time" }) {
  return (
    <div className={"stat-card ng-snap-card ng-snap-chart" + (hot ? " ng-new" : "")}>
      <div className="stat-label">{title}</div>
      <svg viewBox="0 0 160 60" width="100%" height="60" aria-hidden="true">
        <polyline fill="none" stroke="var(--primary, #04bade)" strokeWidth="2.5"
                  points="0,48 20,40 40,44 60,26 80,30 100,14 120,22 140,10 160,16" />
      </svg>
    </div>
  );
}

/** The Builder as it looks while you add the card: the real "Dashboard cards"
 *  list on the left, the page being laid out on the right. */
export function BuilderSnapshot({ chart = false, cards, chartCards, cardTitle = "People Counter",
                                 chartTitle = "People Over Time" }) {
  return (
    <div className="ng-snap ng-snap-builder" data-ng-snap={chart ? "builderchart" : "builder"}>
      <div className="builder-palette ng-snap-palette ng-hl-first">
        <WorkflowCardsPalette catalog={chart ? (chartCards || CHART_CARDS) : (cards || SAMPLE_CARDS)}
                              editable onAdd={() => {}}
                              storageKey={chart ? "guide.cards.chart" : "guide.cards.preview"} />
      </div>
      <div className="ng-snap-canvas">
        <div className="ng-snap-hint">Drag it here</div>
        {chart ? <ChartCard hot title={chartTitle} /> : (
          <div className="stat-card ng-snap-card">
            <div className="stat-label">{cardTitle}</div>
            <div className="stat-value">0</div>
          </div>
        )}
      </div>
    </div>
  );
}

/** The app's side menu as it looks, with the page to click ringed. */
export function MenuSnapshot({ hot }) {
  const on = (name) => "ng-menu-item" + (hot === name ? " ng-new" : "");
  return (
    <div className="ng-snap ng-snap-menu" data-ng-snap="menu">
      <div className="ng-menu">
        <div className="ng-menu-brand">
          <span className="ng-menu-logo">C</span>
          <span><strong>IRIV Edge AI Studio</strong><br /><span className="muted small">Panel Detection</span></span>
        </div>
        <div className="ng-menu-system muted"><Cpu size={16} /> Panel Detection</div>
        <div className="muted small ng-menu-group"><ChevronDown size={13} /> Overview</div>
        <div className={on("Dashboard")}><LayoutDashboard size={16} /> Dashboard</div>
        <div className={on("Analytics")}><ChartColumn size={16} /> Analytics</div>
        <div className={on("Footage")}><Film size={16} /> Footage</div>
      </div>
    </div>
  );
}

/** The top of a page with its buttons; `hot` is the one to press. */
export function BarSnapshot({ kind }) {
  if (kind === "startbar") {
    return (
      <div className="ng-snap ng-snap-bar" data-ng-snap="startbar">
        <div className="ng-bar-note muted small">Detection stopped</div>
        <div className="ng-bar">
          <span className="btn btn-sm btn-accent ng-new ng-btn-ico"><Play size={13} /> Start</span>
          <span className="btn btn-sm">Edit dashboard</span>
        </div>
        <div className="ng-bar-note muted small">Detection running</div>
        <div className="ng-bar">
          <span className="btn btn-sm ng-btn-ico"><Square size={13} /> Stop</span>
          <span className="btn btn-sm">Edit dashboard</span>
        </div>
      </div>
    );
  }
  return (
    <div className="ng-snap ng-snap-bar" data-ng-snap={kind}>
      <div className="ng-bar">
        {kind === "dashbar" ? (
          <>
            <span className="btn btn-sm btn-accent">Start</span>
            <span className="btn btn-sm ng-new">Edit dashboard</span>
            <span className="btn btn-sm">Analytics</span>
          </>
        ) : (
          <>
            <span className="btn btn-sm">+ New Page</span>
            <span className="btn btn-sm ng-new">Edit layout</span>
            <span className="btn btn-sm"><Film size={14} /> Footage &rarr;</span>
          </>
        )}
      </div>
    </div>
  );
}

/** The builder once the card is on the page, with the size buttons. */
export function DroppedSnapshot({ title = "People Counter" }) {
  return (
    <div className="ng-snap ng-snap-dropped" data-ng-snap="dropped">
      <div className="ng-bar">
        <span className="btn btn-sm btn-accent">Small</span>
        <span className="btn btn-sm">Medium</span>
        <span className="btn btn-sm">Big</span>
        <span className="btn btn-sm">View</span>
      </div>
      <div className="ng-snap-row">
        <div className="stat-card ng-snap-card ng-new">
          <div className="stat-label">{title}</div>
          <div className="stat-value">0</div>
        </div>
      </div>
    </div>
  );
}

/** The finished Analytics page, with the card showing a live number. */
export function PageSnapshot() {
  return (
    <div className="ng-snap ng-snap-page" data-ng-snap="page">
      <div className="ng-snap-tabs">
        <span className="btn btn-sm btn-accent">Overview</span>
        <span className="btn btn-sm">Sensor Plate</span>
        <span className="btn btn-sm">+ New Page</span>
      </div>
      <div className="ng-snap-row">
        <div className="stat-card ng-snap-card ng-new">
          <div className="stat-label">People Counter</div>
          <div className="stat-value">12</div>
        </div>
        <div className="stat-card ng-snap-card">
          <div className="stat-label">Total Violations (Day)</div>
          <div className="stat-value">393</div>
        </div>
        <div className="stat-card ng-snap-card">
          <div className="stat-label">Peak Hour</div>
          <div className="stat-value">08:00 AM</div>
        </div>
        <ChartCard />
      </div>
    </div>
  );
}

export default function GuideFlow({ registry, nodeTypes, bare = false, template, big = false, at, onAt }) {
  // The template the system was created from picks the flow. Until it is known the
  // slider is not drawn (the generic flow would flash first); if it cannot be read
  // the generic one is used.
  const [tpl, setTpl] = useState(template);
  useEffect(() => {
    if (template !== undefined) { setTpl(template); return undefined; }
    let live = true;
    api.get("/api/detector/control")
      .then((d) => { if (live) setTpl(d?.template || "blank"); })
      .catch(() => { if (live) setTpl("blank"); });
    return () => { live = false; };
  }, [template]);
  if (tpl === undefined) {
    return <div className={"ng-col ng-home" + (bare ? " ng-bare" : "")} data-ng-flow>
      <p className="muted small">Loading the guide...</p>
    </div>;
  }
  return <FlowSlider key={tpl} flow={flowFor(tpl)} registry={registry} nodeTypes={nodeTypes} bare={bare}
                     big={big} at={at} onAt={onAt} />;
}

/** How tall to draw a step's picture: as tall as its nodes need and no more. A lone node
 *  needs little room; a long, wide flow is scaled down to fit the width, so it needs less
 *  height than its coordinates suggest. */
function diagramHeight(ids, NODES) {
  const pts = ids.map((id) => NODES[id]).filter(Boolean);
  const xs = pts.map((n) => n.x), ys = pts.map((n) => n.y);
  const spanX = Math.max(...xs) - Math.min(...xs);
  const spanY = Math.max(...ys) - Math.min(...ys);
  const scale = Math.min(1, 900 / (spanX + 320));
  return Math.round(Math.max(150, Math.min(320, (spanY + 150) * scale + 40)));
}

// Names the guide's prose uses for a node besides its label in the node list.
const NODE_NAME_EXTRAS = ["Violate", "Line counter", "Line Counter", "Pre-process", "Cycle Time", "Formula",
                          "Object Detection", "Zone Filter", "Class Filter", "Detection Stream"];

/** The text with every node name in bold and highlighted, so the reader sees which nodes to use. */
function withNodeNames(text, registry) {
  const names = [...new Set([...Object.values(registry || {}).map((sp) => sp?.label), ...NODE_NAME_EXTRAS])]
    .filter((n) => typeof n === "string" && n.length >= 4)
    .sort((a, b) => b.length - a.length);
  if (!names.length) return text;
  const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(?<![A-Za-z])(${names.map(esc).join("|")})(?![A-Za-z])`, "g");
  const out = [];
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<mark key={m.index} className="ng-node-name">{m[1]}</mark>);
    last = m.index + m[1].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function FlowSlider({ flow, registry, nodeTypes, bare, big, at: atIn, onAt }) {
  const { steps: STEPS, nodes: NODES, wires: WIRES, tables: TABLES, zones: ZONES } = flow;
  // The parent can keep the step, so the small card and the large view stay on the same one.
  const [atOwn, setAtOwn] = useState(0);
  const at = atIn ?? atOwn;
  const setAt = (v) => {
    const n = typeof v === "function" ? v(at) : v;
    setAtOwn(n);
    onAt?.(n);
  };
  const box = useRef(null);
  const step = STEPS[at];
  const last = at === STEPS.length - 1;

  // The arrow keys move the slider while it has focus.
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const key = (e) => {
      if (e.key === "ArrowRight") setAt((n) => Math.min(STEPS.length - 1, n + 1));
      if (e.key === "ArrowLeft") setAt((n) => Math.max(0, n - 1));
    };
    el.addEventListener("keydown", key);
    return () => el.removeEventListener("keydown", key);
  }, [STEPS.length]);

  // A step draws only some of the workflow's nodes: close the empty columns the others leave, so
  // the nodes shown sit next to each other instead of far apart.
  const placed = (() => {
    if (!step.show) return NODES;
    const cols = [...new Set(step.show.nodes.map((id) => NODES[id].x))].sort((a, b) => a - b);
    const out = { ...NODES };
    for (const id of step.show.nodes) out[id] = { ...NODES[id], x: cols.indexOf(NODES[id].x) * 330 };
    return out;
  })();

  const picture = step.view?.startsWith("mv-") ? <GuideMove key={step.view} kind={step.view} />
    : step.view === "dbpalette" ? <DbPaletteSnapshot />
    : step.view === "welcome" ? (
    <Welcome heading={flow.welcome.heading} lead={flow.welcome.lead} learn={flow.welcome.learn} />)
    : step.view === "builder" ? <BuilderSnapshot cards={flow.cards} cardTitle={flow.cardTitle} />
    : step.view === "builderchart" ? <BuilderSnapshot chart chartCards={flow.chartCards} chartTitle={flow.chartTitle} />
    : step.view === "menu" ? <MenuSnapshot hot={step.hot} />
    : step.view === "dashbar" || step.view === "analyticsbar" || step.view === "startbar" ? <BarSnapshot kind={step.view} />
    : step.view === "dropped" ? <DroppedSnapshot title={flow.cardTitle} />
    : step.view === "page" ? <PageSnapshot />
      : (
        <Diagram key={at} registry={registry} nodeTypes={nodeTypes}
                 height={Math.round((step.tall || diagramHeight(step.show.nodes, placed)) * (big ? 1.25 : 1))}
                 zoomMax={big ? 0.9 : 0.62}
                 tables={TABLES} zones={ZONES} label={`flow-step-${at + 1}`} scrollable
                 nodes={step.show.nodes.map((id) => ({
                   ...placed[id], mark: [].concat(step.show.mark).includes(id),
                   num: [].concat(step.show.mark).includes(id) ? NODES[id].n : undefined }))}
                 edges={step.show.wires.map((w) => WIRES[w])} />
      );

  return (
    <div className={"ng-col ng-home" + (bare ? " ng-bare" : "")} ref={box} tabIndex={-1}
         data-ng-flow>
      <div className="ng-flow-scroll">
      <div className="ng-title">{flow.label}</div>
      <p className="muted small" style={{ margin: "0 0 10px" }}>
        Every workflow is built the same way: take in what the camera sees, narrow it
        down, measure it, then show or act on the result. Step through this one. The
        numbered red ring marks the nodes each step is about.
      </p>

      <div className="ng-slider-head">
        <span className="ng-tag">Step {at + 1} of {STEPS.length}</span>
        <strong>{step.title}</strong>
      </div>
      {picture}
      {step.show && step.show.nodes.length > 2 && (
        <p className="ng-slide-hint" data-ng-slide-hint>Drag the canvas, or slide the bars, to see all of it.</p>
      )}
      <p className="ng-slide-body" data-ng-slide-body>{withNodeNames(step.body, registry)}</p>
      {step.use && (
        <div className="ng-use" data-ng-use>
          <div><strong>Nodes used</strong>
            {step.use.map((u) => {
              const [n, ...rest] = u.split(" ");
              return <span key={u} className="ng-use-chip"><b>{n}</b> {rest.join(" ")}</span>;
            })}
          </div>
          <div><strong>Setup</strong> {step.setup}</div>
          {step.tip && <div className="ng-use-tip"><strong>Your choice</strong> {step.tip}</div>}
          <div className="ng-use-tip">
            Want to know more about these nodes?{" "}
            <Link to="/workflow/guideline" data-ng-more>Open the Nodes Guideline</Link>
          </div>
        </div>
      )}
      <p className="small muted" style={{ margin: "0 0 10px" }}>
        <strong>Where:</strong> {step.where}
      </p>
      {step.dbSetup && (
        <p style={{ margin: "0 0 12px" }}>
          <Link className="btn btn-sm btn-accent" to="/database?guide=setup" data-ng-db-setup>
            Database Setup
          </Link>
        </p>
      )}

      </div>
      <div className="ng-slider-nav">
        <button type="button" className="btn btn-sm" disabled={at === 0}
                onClick={() => setAt(at - 1)} data-ng-prev>Back</button>
        <div className="ng-dots" role="tablist" aria-label="Steps">
          {STEPS.map((s, i) => (
            <button key={s.title} type="button" role="tab" aria-selected={i === at}
                    aria-label={`Step ${i + 1}: ${s.title}`} title={s.title}
                    className={"ng-dot" + (i === at ? " on" : "")}
                    onClick={() => setAt(i)} />
          ))}
        </div>
        {last ? (
          <button type="button" className="btn btn-sm" onClick={() => setAt(0)}>
            Start again
          </button>
        ) : (
          <button type="button" className="btn btn-sm btn-accent"
                  onClick={() => setAt(at + 1)} data-ng-next>Next</button>
        )}
      </div>
    </div>
  );
}
