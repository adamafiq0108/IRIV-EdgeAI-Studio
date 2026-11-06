// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Nodes Guideline - every workflow node, what it does, what it may be wired
// from and to, and what each wire means for the data.
//
// Three columns, read left to right: pick a node (grouped as the palette is),
// see it as it is drawn on the canvas with the function and the rules around
// it, then step through the connections it takes part in -- each as a small
// live diagram of the two real nodes wired together, with its RESULT and its
// CONSEQUENCE.
//
// Nothing here is a second copy of the rules. The node list is the server's
// registry, the cards are the canvas's own components (buildNodeTypes), and
// "may be wired from / to" is asked of the same connection check the canvas
// asks before it draws a wire (registry.edgeAllowed). The words that explain a
// link are written by hand for the ones worth it (workflow/guidelineText.js);
// any other gets a sentence made from the two nodes' own descriptions.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { ErrorNote, SkelGuideline } from "../components/Loading.jsx";
import { buildNodeTypes } from "../workflow/nodes.jsx";
import { Diagram } from "../workflow/GuideDiagram.jsx";
import { CATEGORY_STYLE, STAGES } from "../workflow/registry.js";
import { FURNITURE, LISTED_FURNITURE, connectionsOf } from "../workflow/guideRules.js";
import { Drawer, PALETTE_ORDER, PaletteItem, SubHead, nodeMatches } from "../workflow/PaletteParts.jsx";
import { LINKS, NODE_GUIDE, firstSentence, valueWords } from "../workflow/guidelineText.js";
import { NODE_PROPS } from "../workflow/guidelineProps.js";
// A picture of each node's own properties panel, taken from the real one (one file per node type).
const PROP_SHOTS = import.meta.glob("../assets/guide-props/*.png", { eager: true, query: "?url", import: "default" });
const shotOf = (type) => PROP_SHOTS[`../assets/guide-props/${type.replace(/\./g, "_")}.png`];

/** What a node is drawn with in a diagram: its defaults, named. */
const sampleData = (spec) => ({ ...(spec?.data || {}), name: spec?.data?.name || spec?.label });

const ALL = "__all__";

/** One connection as a card says it: the two nodes, what the wire does, what it
 *  means. `kind` is "in" (a takes-input-from card) or "out" (a can-connect-to). */
function makeStep(registry, a, b, kind) {
  const note = LINKS[`${a}>${b}`];
  const A = registry[a], B = registry[b];
  const ga = NODE_GUIDE[a], gb = NODE_GUIDE[b];
  return {
    key: `${a}>${b}`, a, b, kind,
    title: note?.title || `${A.label} to ${B.label}`,
    // What the wire carries, then what the next node does with it.
    result: note?.result
      || (ga ? `${A.label} sends ${ga.gives} to ${B.label}.`
             : `${A.label} sends its result to ${B.label}.`),
    consequence: note?.consequence
      || (gb ? `${B.label} now ${gb.then}` : `${B.label} now works from ${A.label}.`),
  };
}

/** Draws its children once it is near the screen: with every connection of a
 *  node asked for there can be thirty diagrams, and only a few are in view. */
function Lazy({ height, children }) {
  const [seen, setSeen] = useState(typeof IntersectionObserver === "undefined");
  const box = useRef(null);
  useEffect(() => {
    if (seen || !box.current) return undefined;
    const io = new IntersectionObserver((rows) => {
      if (rows.some((r) => r.isIntersecting)) { setSeen(true); io.disconnect(); }
    }, { rootMargin: "400px" });
    io.observe(box.current);
    return () => io.disconnect();
  }, [seen]);
  return <div ref={box} style={{ minHeight: seen ? undefined : height }}>{seen ? children : null}</div>;
}

/** A node on its own, or two nodes wired together, as the canvas draws them. */
function Mini({ types, registry, nodeTypes, height = 150 }) {
  return (
    <Diagram registry={registry} nodeTypes={nodeTypes} height={height}
             label={types.join(",")}
             nodes={types.map((type, i) => ({ id: `n${i}`, type, x: i * 330, y: 0 }))}
             edges={types.slice(1).map((_t, i) => ({ source: `n${i}`, target: `n${i + 1}` }))} />
  );
}

/** The node's own settings, a page at a time: what each field in its properties panel is for.
 *  A node with more settings than fit on one page has Previous and Next. */
function PropsGuide({ type, label, pages, page, onPage }) {
  if (!pages || !pages.length) {
    return (
      <div className="ng-step" data-ng-props>
        <div className="ng-step-head"><span className="ng-tag">Properties</span><strong>{label}</strong></div>
        <p className="muted small">This node has no settings to change.</p>
      </div>
    );
  }
  const at = Math.min(page, pages.length - 1);
  const pg = pages[at];
  return (
    <div className="ng-step" data-ng-props>
      <div className="ng-step-head">
        <span className="ng-tag">Properties</span>
        <strong>{pg.title}</strong>
        {pages.length > 1 && (
          <span className="muted small" style={{ marginLeft: "auto" }}>Page {at + 1} of {pages.length}</span>
        )}
      </div>
      <p className="muted small" style={{ margin: "0 0 8px" }}>
        This is the panel you see when you select {label} on the Workflow canvas. Below it, what each
        setting does.
      </p>
      {shotOf(type) && (
        <div className="ng-shot" data-ng-shot>
          <img src={shotOf(type)} alt={`The ${label} properties panel`} loading="lazy" />
        </div>
      )}
      <dl className="ng-props">
        {pg.items.map((it) => (
          <div key={it.name} className="ng-prop">
            <dt>{it.name}</dt>
            <dd>{it.text}</dd>
          </div>
        ))}
      </dl>
      {pages.length > 1 && (
        <div className="ng-slider-nav" data-ng-props-nav>
          <button type="button" className="btn btn-sm" disabled={at === 0}
                  onClick={() => onPage(at - 1)} data-ng-props-prev>Previous</button>
          <div className="ng-dots" role="tablist" aria-label="Property pages">
            {pages.map((p, i) => (
              <button key={p.title} type="button" role="tab" aria-selected={i === at}
                      aria-label={`Page ${i + 1}: ${p.title}`} title={p.title}
                      className={"ng-dot" + (i === at ? " on" : "")} onClick={() => onPage(i)} />
            ))}
          </div>
          <button type="button" className="btn btn-sm btn-accent" disabled={at === pages.length - 1}
                  onClick={() => onPage(at + 1)} data-ng-props-next>Next</button>
        </div>
      )}
    </div>
  );
}

export default function NodeGuideline() {
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState(null);
  const [sel, setSel] = useState("");
  const [query, setQuery] = useState("");
  const [openCats, setOpenCats] = useState({});
  // Which connection(s) the right column shows: null (a few typical ones), one
  // node a chip was clicked for, or ALL of a direction. {dir: "from"|"to", type}.
  const [focus, setFocus] = useState(null);
  // Which page of the node's properties guide is showing.
  const [propPage, setPropPage] = useState(0);

  useEffect(() => {
    api.get("/api/workflow").then(setMeta).catch(setError);
  }, []);

  const registry = meta?.registry;
  const nodeTypes = useMemo(() => (registry ? buildNodeTypes(registry) : {}), [registry]);

  // The palette as the canvas has it: its drawers in its order, a node in the
  // drawer its palette entry names, the same search.
  const groups = useMemo(() => {
    if (!registry) return [];
    const q = query.trim().toLowerCase();
    const cat = (sp) => sp.palette?.cat || sp.category;
    return PALETTE_ORDER.map((c) => ({
      cat: c,
      style: CATEGORY_STYLE[c],
      items: Object.entries(registry)
        .filter(([type, sp]) => cat(sp) === c && !sp.deprecated && !sp.hidden
                                && (!FURNITURE.includes(type) || LISTED_FURNITURE.includes(type)))
        .filter(([type, sp]) => !q || nodeMatches(sp, type, q))
        .sort((a, b) => String(a[1].palette?.group || "").localeCompare(
                          String(b[1].palette?.group || ""))
                        || (a[1].rank ?? 9) - (b[1].rank ?? 9)
                        || String(a[1].label).localeCompare(String(b[1].label))),
    })).filter((g) => g.style && g.items.length);
  }, [registry, query]);

  // Open on the Detection Stream, where every workflow starts, when nothing is picked (or the pick has
  // been filtered away).
  useEffect(() => {
    if (!registry) return;
    const flat = groups.flatMap((g) => g.items.map(([type]) => type));
    if (!flat.length) return;
    if (!sel || !flat.includes(sel)) {
      setSel(flat.includes("source.detection") ? "source.detection" : flat[0]);
    }
  }, [registry, groups, sel]);

  const spec = registry?.[sel];

  // A new node starts from its typical connections again.
  useEffect(() => { setFocus(null); setPropPage(0); }, [sel]);

  // The drawer holding the picked node is open, so it can be seen in its list.
  useEffect(() => {
    const sp = registry?.[sel];
    if (!sp) return;
    const c = sp.palette?.cat || sp.category;
    setOpenCats((o) => (o[c] ? o : { ...o, [c]: true }));
  }, [registry, sel]);

  // What this node may be wired FROM and TO, by the canvas's own rule.
  const rules = useMemo(
    () => (registry && spec ? connectionsOf(registry, sel) : null),
    [registry, spec, sel]);

  // The sequence: what usually feeds it, then what it can drive.
  const steps = useMemo(() => {
    if (!registry || !spec || !rules) return [];
    const distinct = (list, n) => {
      const seen = new Set(), out = [];
      for (const t of list) {
        const c = registry[t].category;
        if (seen.has(c)) continue;
        seen.add(c); out.push(t);
        if (out.length >= n) break;
      }
      return out;
    };
    // The ordinary pipeline nodes: not the canvas tools (Catch, Delay, Comment...),
    // and not the Variables, which a wire may reach but do not make the point.
    const plain = (list) => list.filter((t) => !["common", "variable"].includes(
      registry[t].category) && registry[t].palette?.cat !== "common");
    const curatedFrom = rules.from.filter((t) => LINKS[`${t}>${sel}`]);
    const curatedTo = rules.to.filter((t) => LINKS[`${sel}>${t}`]);
    const pickFrom = (curatedFrom.length ? curatedFrom : distinct(plain(rules.from), 1)).slice(0, 1);
    const pickTo = [...curatedTo, ...distinct(plain(rules.to).filter((t) => !curatedTo.includes(t)),
                                              Math.max(0, 3 - curatedTo.length))].slice(0, 3);
    return [...pickFrom.map((t) => makeStep(registry, t, sel, "in")),
            ...pickTo.map((t) => makeStep(registry, sel, t, "out"))];
  }, [registry, spec, rules, sel]);

  if (error) return <ErrorNote error={error} />;
  if (!meta) return <SkelGuideline />;

  const guide = NODE_GUIDE[sel];
  const note = guide?.does || firstSentence(spec?.description) || "";
  // One chip clicked: that one connection. "All": every one of that direction.
  // Nothing: the typical few.
  const pool = focus && rules ? (focus.dir === "from" ? rules.from : rules.to) : [];
  const shownSteps = focus && rules
    ? (focus.type === ALL ? pool : [focus.type]).map((t) => (focus.dir === "from"
      ? makeStep(registry, t, sel, "in") : makeStep(registry, sel, t, "out")))
    : steps;
  const label = (t) => registry[t]?.label || t;
  // One side of the allowed connections as a list: what may be wired INTO this
  // node (left) or what it may be wired TO (right). A row picks that one
  // connection for the right-hand column; "All" picks every one.
  const sideList = (list, dir) => {
    const on = (type) => focus?.dir === dir && focus.type === type;
    const pick = (type) => setFocus(on(type) ? null : { dir, type });
    return (
      <div className="ng-side" data-ng-side={dir}>
        <div className="ng-side-head">
          <span>{dir === "from" ? "From / Input" : "To / Output"}</span>
          <em>{list.length}</em>
        </div>
        <div className="ng-rows">
          {list.length > 1 && (
            <button type="button" data-chip-all={dir}
                    className={"ng-row ng-row-all" + (on(ALL) ? " on" : "")}
                    onClick={() => pick(ALL)}
                    title={`Show every connection (${list.length})`}>
              All ({list.length})
            </button>
          )}
          {list.map((t) => {
            // The colour of the node's own category, as on the canvas and in the palette.
            const c = CATEGORY_STYLE[registry[t].palette?.cat || registry[t].category]?.color || "#94A3B8";
            return (
              <button key={t} type="button" data-chip={`${dir}:${t}`}
                      className={"ng-row" + (on(t) ? " on" : "")} onClick={() => pick(t)}
                      title={`Show how ${label(t)} connects`}
                      style={{ borderLeft: `3px solid ${c}`,
                               ...(on(t) ? { background: `color-mix(in srgb, ${c} 22%, transparent)`,
                                             borderColor: c } : null) }}>
                {label(t)}
              </button>
            );
          })}
          {!list.length && <span className="muted small">none</span>}
        </div>
      </div>
    );
  };

  return (
    <section className="page active" data-node-guideline>
      <div className="page-head" style={{ justifyContent: "flex-start", gap: 12 }}>
        <Link className="btn btn-sm" to="/workflow" data-ng-back>Back to Workflow</Link>
        <div className="page-title">Nodes Guideline</div>
      </div>
      <p className="muted small" style={{ margin: "0 0 12px" }}>
        Every node: what it does, what it connects to, and what each connection
        means for the data. Pick a node on the left.
      </p>

      <div className="ng-grid">
        {/* 1. choose a node */}
        <aside className="ng-col ng-list" data-ng-list>
          <div className="ng-title">Choose node type</div>
          <input className="input" placeholder="Search nodes" value={query}
                 aria-label="Search nodes" onChange={(e) => setQuery(e.target.value)} />
          {(() => {
            // The canvas palette's own layout: a stage heading over the first
            // drawer drawn of that stage, a drawer per category that opens and
            // closes, a sub-heading where the palette has one, a card per node.
            let shownStage = null;
            const searching = !!query.trim();
            return groups.map(({ cat, style, items }) => {
              const stage = STAGES.find((st) => st.id === style.stage);
              const first = !!stage && shownStage !== stage.id;
              if (first) shownStage = stage.id;
              let lastGroup = null;
              return (
                <div key={cat}>
                  {first && (
                    <div style={{ display: "flex", alignItems: "center", gap: 6,
                                  margin: shownStage === STAGES[0].id ? "0 2px 5px" : "12px 2px 5px",
                                  fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
                                  textTransform: "uppercase", color: stage.base }}>
                      <span style={{ width: 6, height: 6, borderRadius: 2,
                                     background: stage.base, flex: "none" }} />
                      {stage.title}
                    </div>
                  )}
                  <Drawer title={style.title} color={style.color} icon={style.icon}
                          count={items.length} open={searching || !!openCats[cat]}
                          onToggle={() => setOpenCats((o) => ({ ...o, [cat]: !o[cat] }))}>
                    {items.map(([type, sp]) => {
                      const group = sp.palette?.group || "";
                      const head = group && group !== lastGroup;
                      lastGroup = group || lastGroup;
                      return (
                        <div key={type}>
                          {head && <SubHead>{group}</SubHead>}
                          <PaletteItem color={style.color} label={sp.label} sub={sp.description}
                                       selected={type === sel} cursor="pointer" data-node={type}
                                       onClick={() => setSel(type)} />
                        </div>
                      );
                    })}
                  </Drawer>
                </div>
              );
            });
          })()}
          {!groups.length && <p className="muted small">No node matches.</p>}
        </aside>

        {/* 2. the node, and its function and rules */}
        <div className="ng-col ng-mid">
          <div className="ng-title">Selected node</div>
          {spec && <Mini types={[sel]} registry={registry} nodeTypes={nodeTypes} height={190} />}

          <div className="ng-title" style={{ marginTop: 14 }}>Function and rules</div>
          {spec && rules && (
            <div className="ng-card" data-ng-rules>
              <h3>{spec.label}</h3>
              <div className="ng-sub">What it does</div>
              <p>{note}</p>
              {guide?.example && (
                <>
                  <div className="ng-sub">Example</div>
                  <p>{guide.example}</p>
                </>
              )}
              <div className="ng-sub">What goes in</div>
              <p className="small">{guide?.takes ? guide.takes.replace(/^./, (c) => c.toUpperCase()) + "."
                : "A value from the node before it."}</p>
              <div className="ng-sub">What comes out</div>
              <p className="small">{guide?.gives ? guide.gives.replace(/^./, (c) => c.toUpperCase()) + "."
                : "A value for the node after it."}</p>
              {(spec.metrics || []).length > 0 && (
                <p className="small muted">
                  Values you can pick from: {spec.metrics.map(valueWords).join("; ")}.
                </p>
              )}
              <div className="ng-sub">What it can be connected with</div>
              <p className="small muted" style={{ margin: "0 0 6px" }}>
                Left: what can feed it. Right: what it can feed. Click one to see
                what that connection means.
              </p>
              <div className="ng-two" data-ng-allowed>
                {sideList(rules.from, "from")}
                {sideList(rules.to, "to")}
              </div>
              {rules.refused.length > 0 && (
                <>
                  <div className="ng-sub">What it cannot be connected to</div>
                  {rules.refused.slice(0, 4).map((r) => (
                    <p key={r.type} className="small muted">
                      <strong>{label(r.type)}:</strong> {r.reason}
                    </p>
                  ))}
                </>
              )}
            </div>
          )}
        </div>

        {/* 3. connection sequences and consequences */}
        <div className="ng-col ng-right" data-ng-steps>
          <div className="ng-title">{focus ? "What it connects to, and what that means" : "Properties"}</div>
          {!focus && spec && (
            <>
              <PropsGuide type={sel} label={spec.label} pages={NODE_PROPS[sel]} page={propPage} onPage={setPropPage} />
              <p className="muted small" style={{ margin: "10px 0 0" }}>
                To see how this node connects, pick an input or an output in the middle column.
              </p>
            </>
          )}
          {focus && (
            <div className="ng-focus" data-ng-focus>
              <span>
                {focus.type === ALL
                  ? `All ${pool.length} that it ${focus.dir === "from" ? "can take input from" : "can connect to"}`
                  : `${focus.dir === "from" ? "Input from" : "Connection to"} ${label(focus.type)}`}
              </span>
              <button type="button" className="btn btn-sm" onClick={() => setFocus(null)}>
                Back to the properties
              </button>
            </div>
          )}
          {focus && shownSteps.map((st) => (
            <div key={st.key} className="ng-step" data-ng-link={st.key}>
              <div className="ng-step-head">
                <span className="ng-tag">{st.kind === "in" ? "Takes input from" : "Can connect to"}</span>
                <strong>{st.title}</strong>
                <button type="button" className="ng-open" style={{ marginLeft: "auto" }}
                        title={`Open ${label(st.kind === "in" ? st.a : st.b)}`}
                        onClick={() => setSel(st.kind === "in" ? st.a : st.b)}>
                  Open {label(st.kind === "in" ? st.a : st.b)}
                </button>
              </div>
              <Lazy height={190}>
                <Mini types={[st.a, st.b]} registry={registry} nodeTypes={nodeTypes} height={190} />
              </Lazy>
              <p className="small"><strong>What happens:</strong> {st.result}</p>
              <p className="small"><strong>What it means:</strong> {st.consequence}</p>
            </div>
          ))}
          {focus && !shownSteps.length && (
            <p className="muted small">
              This node has no connections to step through.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
