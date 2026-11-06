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
import { Component, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Inspector } from "./Workflow.jsx";
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
import { choiceText } from "../workflow/guidelineChoices.js";

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

/** What kind of control a setting is, guessed from its own words -- only for the plain list the
 *  guide falls back to when the real panel cannot be drawn. */
function controlOf(it) {
  const t = `${it.name} ${it.text}`.toLowerCase();
  if (/tick box|a tick\b|switch/.test(t)) return "check";
  if (/\bchoose|pick|one of|either|list\b|select|\bor\b.*\bor\b/.test(t)) return "select";
  return "input";
}

const norm = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();

/** If the real panel throws for some node, the guide shows the plain list instead of a blank. */
class PanelBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(prev) {
    if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

/** The REAL properties panel of a node (the Workflow inspector), drawn for a sample node in a
 *  sandbox -- every dropdown opens with its real choices, every tick box ticks, nothing is
 *  saved -- and played as a tour. EVERY field the panel shows, for every node, is a numbered
 *  step in the order it appears; the explanation stays in one fixed card above the panel and
 *  changes when a field is clicked or Back / Next is pressed. The words come from the
 *  guideline's own text for that setting when it has one, else from the field's (i) mark. */
function SandboxPanel({ type, meta, items }) {
  const registry = meta.registry || {};
  const [node, setNode] = useState(() => ({
    id: "guide-sample", type, position: { x: 0, y: 0 },
    data: { ...(registry[type]?.data || {}), ...sampleData(registry[type]) },
  }));
  const box = useRef(null);
  const [steps, setSteps] = useState([]);        // [{label, hint}] -- the fields, in order
  const [at, setAt] = useState(0);
  const [choices, setChoices] = useState([]);
  const [, bump] = useState(0);
  // The dropdown last clicked or changed: a field can hold several (an If / Else if row has an
  // input picker AND a comparison), and the card explains the one the user is working with.
  const lastSel = useRef(null);
  // What the last pick did to the panel: picking "Snapshot" in Capture adds "Camera between
  // snaps", a Card Widget shape swaps a whole set of fields. Remembered per field + option so
  // the card can say "this choice adds ..." and the new fields wear a "new" badge.
  // {field, option, added: [label], removed: [label]} or null.
  const [effect, setEffect] = useState(null);
  const pending = useRef(null);      // {field, option, before: [label]} set on change

  const labelEl = (field) => field.querySelector(".wf-field-label");
  const rawLabel = (field) => {
    const el = labelEl(field);
    return el ? [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent)
      .join(" ").replace(/\s+/g, " ").trim() : "";
  };
  const fieldsOf = (root) => [...root.querySelectorAll(".wf-field")].filter((f) => rawLabel(f));
  const itemFor = (label) => {
    const l = norm(label);
    if (!l) return null;
    return items.find((it) => norm(it.name) === l)
      || items.find((it) => norm(it.name).startsWith(l) || l.startsWith(norm(it.name))) || null;
  };

  // After every render: number the fields, ring the current one, and read its dropdown's
  // choices. The list of steps follows the panel (picking "Snapshot" adds a field).
  useLayoutEffect(() => {
    const root = box.current;
    if (!root) return;
    const fields = fieldsOf(root);
    const list = fields.map((f) => ({ label: rawLabel(f), hint: f.getAttribute("data-hint") || "" }));
    const sig = list.map((x) => x.label).join("|");
    setSteps((cur) => (cur.map((x) => x.label).join("|") === sig ? cur : list));
    // A pick just changed the panel: note which fields came and went.
    const pend = pending.current;
    if (pend) {
      pending.current = null;
      const now = list.map((x) => x.label);
      const added = now.filter((l) => !pend.before.includes(l));
      const removed = pend.before.filter((l) => !now.includes(l));
      const next = { field: pend.field, option: pend.option, added, removed };
      setEffect((e) => (JSON.stringify(e) === JSON.stringify(next) ? e : next));
    }
    const fresh = effect && effect.added.length ? effect.added : [];
    const i = Math.min(at, Math.max(0, fields.length - 1));
    fields.forEach((f, k) => {
      f.classList.toggle("ng-hl", k === i);
      labelEl(f)?.setAttribute("data-ng-n", String(k + 1));
      f.classList.toggle("ng-new", fresh.includes(list[k].label));
    });
    const hit = fields[i];
    const own = hit ? [...hit.querySelectorAll("select")].filter((sel) => sel.closest(".wf-field") === hit) : [];
    const sel = own.includes(lastSel.current) ? lastSel.current : own[0];
    const opts = sel ? [...sel.options].map((o) => ({ text: o.text.replace(/\s+/g, " ").trim(), on: o.selected }))
      .filter((o) => o.text) : [];
    const key = JSON.stringify(opts);
    setChoices((c) => (JSON.stringify(c) === key ? c : opts));
  });

  // A new pick in a dropdown changes which choice is marked; re-read it.
  useEffect(() => {
    const root = box.current;
    if (!root) return undefined;
    const again = (e) => {
      if (e && e.target && e.target.tagName === "SELECT") {
        lastSel.current = e.target;
        if (e.type === "change") {
          const field = e.target.closest(".wf-field");
          const opt = e.target.options[e.target.selectedIndex];
          pending.current = {
            field: field ? rawLabel(field) : "",
            option: opt ? opt.text.replace(/\s+/g, " ").trim() : "",
            before: fieldsOf(root).map((f) => rawLabel(f)),
          };
        }
      }
      // Re-render AFTER the event has finished. A state update made right here runs (as a
      // microtask) BEFORE React's own onChange for the select: React re-renders the controlled
      // select with its old value, resets the DOM, and then its onChange reads the old value
      // back -- a real click on "Snapshot" never stuck. (A scripted change does not do this,
      // which is why tests passed.)
      setTimeout(() => bump((n) => n + 1), 0);
    };
    root.addEventListener("change", again);
    root.addEventListener("focusin", again);
    return () => {
      root.removeEventListener("change", again);
      root.removeEventListener("focusin", again);
    };
  }, []);

  // Bring the ringed field into view inside the panel when the tour moves.
  useEffect(() => {
    const root = box.current;
    const hit = root && root.querySelector(".wf-field.ng-hl");
    if (!hit) return;
    const b = root.getBoundingClientRect(), r = hit.getBoundingClientRect();
    if (r.top < b.top + 8 || r.bottom > b.bottom - 8) {
      root.scrollTo({ top: Math.max(0, r.top - b.top + root.scrollTop - 16), behavior: "smooth" });
    }
  }, [at]);

  const n = steps.length;
  const i = Math.min(at, Math.max(0, n - 1));
  const cur = steps[i];
  const item = cur ? itemFor(cur.label) : null;
  const text = item?.text || cur?.hint
    || "Set this to suit your case. Its (i) mark in the real panel says more when there is more to say.";
  const sometimes = /only shown|^shown (for|when|after)/i.test(text);

  return (
    <div className="ng-tour" data-ng-tour>
      {/* The explanation: one fixed card, its content follows the tour. */}
      <div className="ng-tourcard" aria-live="polite">
        <div className="ng-callout-progress" aria-hidden="true">
          <span style={{ width: n ? `${((i + 1) / n) * 100}%` : 0 }} />
        </div>
        {cur ? (
          <>
            <div className="ng-callout-head">
              <span className="ng-callout-n">{i + 1}</span>
              <div style={{ minWidth: 0 }}>
                <div className="ng-iprops-step">{item?.group || "Setting"}</div>
                <h4 className="ng-iprops-name">{cur.label}</h4>
              </div>
            </div>
            {sometimes && <span className="ng-iprops-badge">Shown only in some cases</span>}
            <p className="ng-iprops-text">{text}</p>
            {choices.length > 1 && (() => {
              // What the option picked now does, then every option with its own words, so the
              // choice can be weighed before it is made.
              const pick = choices.find((c) => c.on);
              const pickText = pick ? choiceText(type, cur.label, pick.text) : "";
              return (
                <>
                  {pick && (
                    <div className="ng-pick" data-ng-pick>
                      <div className="ng-pick-head">Your pick</div>
                      <div className="ng-pick-name">{pick.text}</div>
                      {pickText && <p className="ng-pick-text">{pickText}</p>}
                      {effect && effect.field === cur.label && effect.option === pick.text
                        && (effect.added.length > 0 || effect.removed.length > 0) && (
                        <div className="ng-pick-effect" data-ng-pick-effect>
                          {effect.added.length > 0 && (
                            <>
                              <div className="ng-pick-effect-head">This choice adds {effect.added.length === 1 ? "a setting" : `${effect.added.length} settings`}</div>
                              <ul>
                                {effect.added.map((l) => {
                                  const k = steps.findIndex((x) => x.label === l);
                                  const it = itemFor(l);
                                  const hint = it?.text || steps[k]?.hint || "";
                                  return (
                                    <li key={l}>
                                      <button type="button" className="ng-pick-effect-go"
                                              disabled={k < 0} onClick={() => setAt(k)}>
                                        {k >= 0 && <span className="ng-pick-effect-n">{k + 1}</span>}
                                        <span className="ng-pick-effect-name">{l}</span>
                                        <span className="ng-pick-effect-arrow" aria-hidden="true">→</span>
                                      </button>
                                      {hint && <div className="ng-pick-effect-text">{hint}</div>}
                                    </li>
                                  );
                                })}
                              </ul>
                            </>
                          )}
                          {effect.removed.length > 0 && (
                            <div className="ng-pick-effect-gone">
                              No longer shown: {effect.removed.join(", ")}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  <div className="ng-choices">
                    <div className="ng-choices-head">All choices</div>
                    <ul>
                      {choices.map((c) => {
                        const t = choiceText(type, cur.label, c.text);
                        return (
                          <li key={c.text} className={c.on ? "on" : ""}>
                            <div className="ng-choice-row">
                              <span>{c.text}</span>
                              {c.on && <span className="ng-choices-now">your pick</span>}
                            </div>
                            {t && !c.on && <div className="ng-choice-text">{t}</div>}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </>
              );
            })()}
          </>
        ) : (
          <p className="ng-iprops-text muted">This node has no settings to change.</p>
        )}
        <div className="ng-callout-nav" data-ng-props-nav>
          <button type="button" className="btn btn-sm" disabled={i === 0}
                  onClick={() => setAt(i - 1)} data-ng-props-prev>Back</button>
          <span className="muted small">{n ? `${i + 1} of ${n}` : ""}</span>
          <button type="button" className="btn btn-sm btn-accent" disabled={i >= n - 1}
                  onClick={() => setAt(i + 1)} data-ng-props-next>Next</button>
        </div>
      </div>
      {/* The real panel: click any field to read about it. */}
      <div ref={box} className="ng-sandbox" data-ng-sandbox
           onClickCapture={(e) => {
             const field = e.target.closest?.(".wf-field");
             if (!field || !box.current?.contains(field)) return;
             const k = fieldsOf(box.current).indexOf(field);
             if (k >= 0 && k !== i) setAt(k);
           }}>
        {/* The same wrapper classes as the Workflow page's right column: the field layout
            (.rail-form) and the inspector's own rules (.wf-inspector) only apply inside them. */}
        <div className="wf-inspector rail-form ng-sandbox-form">
          <Inspector node={node} registry={registry} meta={meta} nodes={[node]} edges={[]}
                     editable errors={{}} warnings={{}} functions={meta.functions || []}
                     customVars={[]} onCreateVar={() => {}} onRetype={() => {}} onFnData={() => {}}
                     onOpenBlueprint={() => {}}
                     onChange={(fields) => setNode((nd) => ({ ...nd, data: { ...nd.data, ...fields } }))} />
        </div>
      </div>
    </div>
  );
}

/** The plain list of a node's settings: the fallback when the real panel cannot be drawn. */
function MockPanel({ label, flat, pages, at, onPage }) {
  return (
    <div className="ng-iprops-panel" role="listbox" aria-label={`${label} settings`}>
      <div className="ng-iprops-title">{label}</div>
      {flat.map((it, i) => {
        const first = i === 0 || flat[i - 1].group !== it.group;
        const kind = controlOf(it);
        return (
          <div key={`${it.group}-${it.name}`}>
            {first && pages.length > 1 && <div className="ng-iprops-group">{it.group}</div>}
            <button type="button" role="option" aria-selected={i === at}
                    className={"ng-iprops-row" + (i === at ? " on" : "")} onClick={() => onPage(i)}>
              <span className="ng-iprops-label">
                {kind === "check" && <span className="ng-iprops-check" aria-hidden="true" />}
                {it.name}
              </span>
              {kind !== "check" && (
                <span className={"ng-iprops-field" + (kind === "select" ? " select" : "")} aria-hidden="true" />
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}

/** The node's own settings, to click through: its real properties panel (try every control --
 *  nothing is saved) beside what the chosen setting does. */
function PropsGuide({ type, label, pages, page, onPage, meta }) {
  if (!pages || !pages.length) {
    return (
      <div className="ng-step" data-ng-props>
        <div className="ng-step-head"><span className="ng-tag">Properties</span><strong>{label}</strong></div>
        <p className="muted small">This node has no settings to change.</p>
      </div>
    );
  }
  const flat = pages.flatMap((pg) => pg.items.map((it) => ({ ...it, group: pg.title })));
  const at = Math.min(Math.max(0, page), flat.length - 1);
  const cur = flat[at];
  const sometimes = /only shown|^shown (for|when|after)/i.test(cur.text);
  const mock = <MockPanel label={label} flat={flat} pages={pages} at={at} onPage={onPage} />;
  return (
    <div className="ng-step ng-props-step" data-ng-props>
      <div className="ng-step-head">
        <span className="ng-tag">Properties</span>
        <strong>{label}</strong>
      </div>
      <p className="muted small" style={{ margin: "0 0 10px" }}>
        The real panel you get when you double-click {label} on the Workflow canvas. Every setting
        is numbered: follow them with Next, or click any one. Try the controls too, nothing is saved.
      </p>
      {(() => {
        // What the chosen setting does, with the way on: shown as a callout under the field
        // in the real panel, or as a card beside the plain list when that is the fallback.
        const explain = (
          <>
            <div className="ng-callout-progress" aria-hidden="true">
              <span style={{ width: `${((at + 1) / flat.length) * 100}%` }} />
            </div>
            <div className="ng-callout-head">
              <span className="ng-callout-n">{at + 1}</span>
              <div style={{ minWidth: 0 }}>
                <div className="ng-iprops-step">{cur.group}</div>
                <h4 className="ng-iprops-name">{cur.name}</h4>
              </div>
            </div>
            {sometimes && <span className="ng-iprops-badge">Shown only in some cases</span>}
            <p className="ng-iprops-text">{cur.text}</p>
            <div className="ng-callout-nav" data-ng-props-nav>
              <button type="button" className="btn btn-sm" disabled={at === 0}
                      onClick={() => onPage(at - 1)} data-ng-props-prev>Back</button>
              <span className="muted small">{at + 1} of {flat.length}</span>
              <button type="button" className="btn btn-sm btn-accent" disabled={at === flat.length - 1}
                      onClick={() => onPage(at + 1)} data-ng-props-next>Next</button>
            </div>
          </>
        );
        const fallback = (
          <div className="ng-iprops" data-ng-iprops>
            {mock}
            <div className="ng-iprops-info" aria-live="polite">{explain}</div>
          </div>
        );
        return (
          <PanelBoundary key={type} resetKey={type} fallback={fallback}>
            {meta ? <SandboxPanel key={type} type={type} meta={meta} items={flat} /> : fallback}
          </PanelBoundary>
        );
      })()}
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
              <PropsGuide type={sel} label={spec.label} pages={NODE_PROPS[sel]} page={propPage} onPage={setPropPage} meta={meta} />
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
