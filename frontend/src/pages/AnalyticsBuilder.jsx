// Analytics Builder - the full-width composer for the Analytics Overview.
//
// The same machinery as the Dashboard Builder (palette, grid canvas, live
// preview, properties inspector - all of it imported from
// components/widgets/builderShared.jsx), with three differences that come from
// what Analytics IS:
//
//   1. ONE page. Analytics computes a single "overview" page and
//      workflow.apply_artifacts sweeps every builder page that is not
//      `main`/`overview`, so a second page here would be deleted behind the
//      operator's back. There are no page tabs, and nothing can create one.
//   2. A GLOBAL date range (Today / Week / Month) - the window the preview is
//      rendered in, exactly as the live Analytics page scopes itself with
//      `/api/analytics?range=`. Per-widget ranges (a history widget's `range`,
//      a query widget's `query.range`) stay per-widget overrides.
//   3. Package cards share the canvas. A widget declared in the system package
//      has no layout entry to edit, so it is dragged as a `pkg` widget whose
//      POSITION is saved as a placement and whose removal is a `hidden` entry.
//
// Saving is a PARTIAL write: only the operator's own widgets are sent, so the
// wf_* widgets a workflow recompile puts on this same page survive
// (core/services/builder.py merge_partial is the other half of that contract).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { ErrorNote, SkelEditor } from "../components/Loading.jsx";
import GridCanvas, { autoPlace, notePaletteDrag, placedAt } from "../components/widgets/GridCanvas.jsx";
import {
  ANALYTICS_PALETTE, WorkflowCardsPalette,
  WidgetInspector, catalogWidget, newWidget, setFont, setOverride,
  isChartKind, seriesStyle, chartLook, useLivePreview, verdictLabels,
  cardQuestionEdits, useRowBase,
} from "../components/widgets/builderShared.jsx";
import { Badge, EmptyState } from "../components/ui/primitives.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import { useConfirm } from "../components/ui/ask.jsx";
import ViewSettings from "../components/ui/ViewSettings.jsx";
import { LayoutGrid } from "lucide-react";

const PAGE_ID = "overview";   // the one page Analytics computes; edits land here
const SIZES = ["small", "medium", "big"];

/** A package card as a draggable canvas widget. The layout owns WHERE it sits;
    the package owns WHAT it is, so there is nothing else to edit on it. */
const pkgDraft = (w) => ({
  id: w.id, title: w.title, kind: w.kind,
  // The dressing rides along: the server applies the operator's template
  // override before this row arrives, and dropping it here would show
  // "plain" in the inspector for a card that is not.
  template: w.template || "plain",
  source: "stat", key: "", grid: w.grid || {}, pkg: true,
});

export default function AnalyticsBuilder() {
  const toast = useToast();
  const confirm = useConfirm();
  const [range, setRange] = useState("month");
  const [meta, setMeta] = useState(null);        // /api/builder
  const [data, setData] = useState(null);        // /api/analytics
  const [error, setError] = useState(null);
  const [widgets, setWidgets] = useState([]);    // the draft: own + pkg widgets
  const [selected, setSelected] = useState(null);
  const [dirty, setDirty] = useState(false);
  // How tall a grid row is on this page. Saved WITH the layout, not kept
  // in the browser: the finished Analytics page has to match what was
  // arranged here, and a browser preference could not travel.
  const [size, setSize] = useState("medium");
  const [busy, setBusy] = useState(false);
  // Every row as this editor loaded it: what a save diffs a compiled card's
  // question against (cardQuestionEdits), so only what changed goes back.
  const base = useRowBase();

  /** The computed Overview for the current range: what the package cards
      actually render, and the source of the "removed" list. */
  const loadAnalytics = useCallback(
    (r) => api.get(`/api/analytics?range=${encodeURIComponent(r)}`),
    []);

  // First load: the saved layout and the computed page, folded into one draft.
  useEffect(() => {
    let alive = true;
    Promise.all([api.get("/api/builder"), loadAnalytics(range)])
      .then(([m, d]) => {
        if (!alive) return;
        setMeta(m);
        setData(d);
        const saved = ((m.layout || {}).pages || []).find((p) => p.id === PAGE_ID);
        // Medium is what every page was built at, so a layout that says
        // nothing looks exactly as it did before this setting existed.
        setSize(SIZES.includes(saved?.size) ? saved.size : "medium");
        // A hidden card (a removed wf_* card included) stays in the layout so
        // it can be restored, but it must not appear on the canvas.
        const hidden = new Set((m.layout || {}).hidden || []);
        // A wf_* row's stored grid is compiler-owned and stale by design (the
        // partial merge keeps it as-is); WHERE the operator put the card lives
        // in placements. Overlay them or every reload snaps the cards back.
        const placed = ((m.layout || {}).placements || {})[PAGE_ID] || {};
        const page = (d.pages || [])[0] || { widgets: d.widgets || [] };
        const pkg = (page.widgets || []).filter((w) => !w.builder).map(pkgDraft);
        const own = (saved?.widgets || [])
          .filter((w) => !hidden.has(w.id))
          .map((w) => (placed[w.id] ? { ...w, grid: placed[w.id] } : w));
        const drafted = autoPlace([...pkg, ...own]);
        base.reset(drafted);
        setWidgets(drafted);
      })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
    // Range changes refresh `data` below; the draft is built once so a range
    // switch never throws away work in progress.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Range switch: re-scope the package cards' data. The operator's own widgets
  // are re-previewed by the hook below with the same range.
  useEffect(() => {
    if (!meta) return;
    loadAnalytics(range).then(setData).catch(() => {});
  }, [range, meta, loadAnalytics]);

  // Only the operator's own widgets go to the preview endpoint - a package card
  // is not a layout widget and has nothing for it to render from.
  const own = useMemo(() => widgets.filter((w) => !w.pkg), [widgets]);
  // The SELECTED card is named to the hook: a row being typed into is
  // incomplete until it is finished, and that is not a page-wide failure.
  const ownPreview = useLivePreview(own, !!meta, range, selected);
  // Package cards are previewed by the Analytics payload itself, which is the
  // very renderer the finished page uses.
  const preview = useMemo(() => {
    const page = (data?.pages || [])[0] || { widgets: data?.widgets || [] };
    return [...(page.widgets || []), ...ownPreview];
  }, [data, ownPreview]);

  const sources = meta?.sources || { stat: [], bucket: [], events: [] };
  const editable = !!meta?.editable;
  const catalog = data?.catalog || [];
  const removed = data?.hidden || [];
  const current = useMemo(() => widgets.find((w) => w.id === selected) || null,
                          [widgets, selected]);

  // `cell`: dropped on the canvas -- placed where the pointer let go
  // (GridCanvas onDropCell); without one it goes after everything.
  function place(made, cell) {
    const widget = cell ? placedAt(made, cell) : made;
    base.add(widget);
    setWidgets((list) => autoPlace([...list, widget]));
    setSelected(widget.id);
    setDirty(true);
  }
  const add = (item, cell) => place(newWidget(item, sources), cell);
  const addFromCatalog = (c, cell) => place(catalogWidget(c), cell);

  function patch(id, fields) {
    setWidgets((list) => list.map((w) => (w.id === id ? { ...w, ...fields } : w)));
    setDirty(true);
  }

  // Two kinds of widget live side by side. A layout widget can simply be
  // deleted; a package card is declared in code, where there is nothing to
  // delete - so it is HIDDEN instead, which is both the only thing that works
  // and the reversible option (see "Removed" below).
  function remove(id) {
    const w = widgets.find((x) => x.id === id);
    setWidgets((list) => list.filter((x) => x.id !== id));
    if (selected === id) setSelected(null);
    setDirty(true);
    // A compiled card belongs to a workflow node and is rebuilt on every
    // compile, so deleting its layout entry would silently undo itself. It is
    // HIDDEN instead -- same as a package card -- which sticks across
    // recompiles and stays restorable from the "Removed" list. The node keeps
    // computing; only the card goes.
    if (w?.pkg || String(id).startsWith("wf_")) hidePackage(id);
  }

  // Hiding is read-modify-write against a shared list, and removing several
  // cards in a row fires several of them at once: each would read the list
  // BEFORE the others' writes landed and put back everything but its own card,
  // so only the last deletion survived -- which reads as "I deleted them all
  // and they came back". Serialised, so every write sees the one before it.
  const hideQueue = useRef(Promise.resolve());

  function queueHidden(update) {
    const run = hideQueue.current.then(async () => {
      const layout = (await api.get("/api/builder")).layout || {};
      await api.put("/api/builder", { ...layout, partial: true,
                                      hidden: update(layout.hidden || []) });
    });
    // Keep the chain alive after a failure, or one error would stall every
    // later removal on the page.
    hideQueue.current = run.catch(() => {});
    return run;
  }

  async function hidePackage(widgetId) {
    setBusy(true);
    try {
      await queueHidden((hidden) => (hidden.includes(widgetId)
        ? hidden : [...hidden, widgetId]));
      setData(await loadAnalytics(range));
    } catch (e) { toast.error("Could not remove the card", e.message); }
    finally { setBusy(false); }
  }

  async function restore(widgetId) {
    setBusy(true);
    try {
      await queueHidden((hidden) => hidden.filter((h) => h !== widgetId));
      const d = await loadAnalytics(range);
      setData(d);
      // Put the card back on the canvas where the operator can see it, rather
      // than only after a reload. A package card comes back as a pkg draft; a
      // builder card (wf_* included) comes back as its stored layout widget,
      // which never left the layout while hidden.
      const page = (d.pages || [])[0] || { widgets: d.widgets || [] };
      const back = (page.widgets || []).find((w) => w.id === widgetId && !w.builder);
      let draft = back ? pkgDraft(back) : null;
      if (!draft) {
        const layout = (await api.get("/api/builder")).layout || {};
        const saved = (layout.pages || []).find((p) => p.id === PAGE_ID);
        draft = (saved?.widgets || []).find((w) => w.id === widgetId) || null;
        const grid = ((layout.placements || {})[PAGE_ID] || {})[widgetId];
        if (draft && grid) draft = { ...draft, grid };
      }
      if (draft) {
        base.add(draft);
        setWidgets((list) => (list.some((w) => w.id === widgetId)
          ? list : autoPlace([...list, draft])));
        setDirty(true);
      }
    } catch (e) { toast.error("Could not restore the card", e.message); }
    finally { setBusy(false); }
  }

  // "Insert all" is the same operation restore() does per row, applied to
  // every id at once: the hidden list is emptied, so anything still real
  // (its layout row, or its package definition) renders again. A card whose
  // WORKFLOW NODE is long gone has no row left to insert -- the compiler
  // stopped emitting it on the recompile that followed the node's deletion
  // -- so clearing its hidden entry just stops tracking it; there is nothing
  // to bring back. A package card is code-defined and always has one, so for
  // those this always means "back onto the canvas" -- confirmed up front so
  // that is not a surprise.
  async function clearAllHidden() {
    if (!removed.length) return;
    if (!await confirm({
      title: `Insert ${removed.length} unused card`
        + `${removed.length === 1 ? "" : "s"} back onto the canvas?`,
      description: "Any whose workflow node is gone stay gone - there is "
        + "nothing left to insert. Any built-in ones come back.",
      confirmLabel: "Insert them" })) return;
    setBusy(true);
    try {
      await queueHidden(() => []);
      const d = await loadAnalytics(range);
      setData(d);
    } catch (e) { toast.error("Could not insert the cards", e.message); }
    finally { setBusy(false); }
  }

  /** Drop from either palette onto the canvas. */
  function onDrop(e) {
    e.preventDefault();
    const paletteIdx = e.dataTransfer.getData("application/x-palette");
    if (paletteIdx !== "") {
      const item = ANALYTICS_PALETTE[Number(paletteIdx)];
      if (item) add(item);
      return;
    }
    const catalogIdx = e.dataTransfer.getData("application/x-catalog");
    if (catalogIdx !== "") {
      const item = catalog[Number(catalogIdx)];
      if (item) addFromCatalog(item);
    }
  }

  async function save() {
    setBusy(true);
    try {
      // Let any in-flight removals land first. This write carries `hidden`
      // straight from what it reads, so reading before a queued hide finished
      // would send the OLD list back and quietly un-hide the cards that were
      // just removed.
      await hideQueue.current;
      // Re-read immediately before writing: the layout may have gained wf_*
      // widgets since this page loaded.
      const layout = (await api.get("/api/builder")).layout || { pages: [] };
      const pages = layout.pages || [];
      // The operator's rows go exactly as they stand. A save must not change
      // how a card DRAWS: the series list it carries (or does not) decides
      // which server path renders it, so a chart that has none is sent with
      // none (builderShared.jsx, beside widgetSeries, says what rewriting
      // that here did to the charts).
      const mine = widgets.filter((w) => !w.pkg);
      // A wf_* card's row in `pages` is server-owned (the partial merge strips
      // it from what we send and keeps the stored one), so WHERE it sits must
      // travel as a placement -- same as a package card -- or every drag of a
      // workflow card would save as a no-op and the live page would disagree
      // with this editor.
      const placed = Object.fromEntries(
        widgets.filter((w) => w.pkg || String(w.id).startsWith("wf_"))
               .map((w) => [w.id, w.grid]));
      // Same story for HOW a wf_* card is dressed: its widget row is
      // server-owned, so the template picked here travels as an override,
      // keyed by id, which the server applies on every load. Without this the
      // picker saved into a row that the merge threw away -- it looked like
      // the template select simply did not work.
      const dressed = { ...(layout.templates || {}) };
      // Same story for the card's own background colour -- a second,
      // independent override map, because `bg` is a different knob from
      // `template` and an operator may set one without the other.
      const coloured = { ...(layout.colors || {}) };
      // ...and the same again for the ACCENT (a chart's stroke, a tile's
      // accent paint). It had no override map, so the picker wrote it onto a
      // row the merge threw away -- which is why the accent colour looked
      // like it simply did nothing here.
      const accented = { ...(layout.accents || {}) };
      // ...and how big its words are. Same override map, same reason: a
      // compiled card's row is rewritten by the next recompile, so a size set
      // on the widget itself would be forgotten -- silently, which is worse
      // than not offering the control.
      const fonts = { ...(layout.fonts || {}) };
      // ...and what the card is CALLED. Renaming is the first thing anybody
      // does to a dashboard and it was the one edit with nowhere to live: a
      // compiled card now sends its name to its node with the rest of its
      // look, and a PACKAGE card, which has no node, keeps it here.
      const named = { ...(layout.names || {}) };
      // What to push back onto the workflow nodes.
      const styled = [];
      const compiledCards = [];
      for (const w of widgets) {
        // Compiled cards AND package cards: both have server-owned rows (one
        // rewritten by the compiler, one declared in Python), so for both the
        // template can only live in the override map.
        if (String(w.id).startsWith("wf_")) {
          // A compiled card's LOOK goes back to the node that publishes
          // it (see /api/workflow/card-style), so the canvas and this page
          // cannot show two different cards. The override maps are cleared
          // for it at the same time -- a leftover entry there would quietly
          // win over the node the operator just edited.
          styled.push({ id: w.id, name: w.title || "", sub: w.sub || "",
                        // "Show title", only once somebody set it.
                        ...(typeof w.show_title === "boolean" ? { show_title: w.show_title } : {}),
                        // ...and where the stat sits, likewise.
                        ...(w.align ? { align: w.align } : {}),
                        ...(w.valign ? { valign: w.valign } : {}),
                        template: w.template || "plain",
                        bg: w.bg || "", color: w.color || "",
                        anim: w.anim || "", anim_replay: !!w.anim_replay,
                        bands: w.bands || {}, by_day: !!w.by_day,
                        label_size: w.label_size || "medium",
                        value_size: w.value_size || "medium",
                        // A verdict's two words are data, and go with it.
                        ...(w.kind === "verdict" ? verdictLabels(w) : {}),
                        // ...and a chart's per-input names and colours, by
                        // series id: the node rebuilds the inputs on every
                        // compile, so these are the half of them that has to
                        // live where the rebuild reads from.
                        ...(isChartKind(w.kind)
                          ? { series: seriesStyle(w, base.get(w.id)) } : {}),
                          // ...and how it is DRAWN (axis titles, bars,
                          // values on the chart): look, kept on the node.
                          ...chartLook(w),
                        // ...and its QUESTION, where it was changed here:
                        // "Work out", one bar per, the period, the shape. The
                        // row is server-owned, so the node is the only place
                        // that answer can live past the next workflow save.
                        ...cardQuestionEdits(base.get(w.id), w) });
          compiledCards.push(w);
        } else if (w.node) {
          // A linked copy's DATA is the node's (rewritten from it on every
          // recompile): its threshold bands, and a verdict's two words. Those
          // go to the node -- and only those, its look being its own. Same
          // as Builder.jsx. The bands used to stay on the copy's row, so a
          // limit typed here never reached the canvas, and one moved on the
          // canvas never reached this chart.
          const words = w.kind === "verdict" ? verdictLabels(w) : {};
          // ...and its question, for the same reason: the copy's body is
          // refreshed from the node on every recompile, so "Work out" set on
          // the copy has to reach the node or it reverts at the next save.
          styled.push({ id: `wf_${w.node}`, bands: w.bands || {}, ...words,
                        ...cardQuestionEdits(base.get(w.id), w) });
        } else if (w.pkg) {
          // A package widget is declared in Python: no node to send its look
          // to, so the maps are where it lives. This branch used to be folded
          // in with the compiled one above, which CLEARED these entries and
          // then pushed a style the route could not apply -- so a package
          // card's colour and title were dropped on every save.
          setOverride(dressed, w.id, w.template, "plain");
          setOverride(coloured, w.id, w.bg, "");
          setOverride(accented, w.id, w.color, "");
          setOverride(named, w.id, w.title, "");
          setFont(fonts, w.id, w);
        }
      }
      // Everything the operator added belongs to "overview" - the only page
      // Analytics has. A second page would be invisible here forever, and
      // workflow.apply_artifacts would sweep it away.
      const idx = pages.findIndex((p) => p.id === PAGE_ID);
      const next = idx >= 0
        ? pages.map((p, i) => (i === idx ? { ...p, size, widgets: mine } : p))
        : [...pages, { id: PAGE_ID, name: "Overview", size, widgets: mine }];
      // partial: merge only what the operator owns. A workflow recompile
      // running concurrently rewrites the wf_* widgets; a full write here would
      // put back the copy we read a moment ago and lose them.
      // The nodes FIRST, because that call recompiles: the layout write
      // below then lands on top of freshly compiled card rows rather than
      // being overwritten by them a moment later.
      //
      // Did it land? ok:false means the node took the look but the graph has
      // an error, so the layout was NOT recompiled; a throw means this user
      // may edit dashboards but not the canvas. Either way the wf_* rows this
      // partial write sends are dropped by merge_partial, so the override
      // maps are the only place the look survives -- they are cleared only
      // once the node is known to hold it, and written as the fallback
      // otherwise (same as Builder.jsx).
      let onNodes = false;
      if (styled.length) {
        try {
          const r = await api.post("/api/workflow/card-style", { cards: styled });
          onNodes = !r || r.ok !== false;
        } catch (e) {
          // Not fatal: the layout still saves and the card looks right here.
          // Said out loud because the CANVAS will not agree until it works.
          toast.error("The workflow nodes could not be updated", e.message);
        }
      }
      for (const w of compiledCards) {
        if (onNodes) {
          delete dressed[w.id];
          delete coloured[w.id];
          delete accented[w.id];
          delete fonts[w.id];
          delete named[w.id];
        } else {
          setOverride(dressed, w.id, w.template, "plain");
          setOverride(coloured, w.id, w.bg, "");
          setOverride(accented, w.id, w.color, "");
          setOverride(named, w.id, w.title, "");
          setFont(fonts, w.id, w);
        }
      }
      // Said out loud, as Builder.jsx does: the fallback maps cannot hold a
      // RESET (plain, no card colour, brand accent -- an empty entry is the
      // same as no entry), so a look that did not reach the node is the one
      // case where "saved" here is not "saved" on the canvas. Silent, it read
      // as the picker doing nothing.
      if (styled.length && !onNodes) {
        toast.info("The canvas could not take the card styling yet - it is "
                   + "kept on the dashboard, and moves to the canvas when the "
                   + "workflow compiles clean.");
      }
      await api.put("/api/builder", {
        ...layout, partial: true, pages: next,
        placements: { ...(layout.placements || {}), [PAGE_ID]: placed },
        templates: dressed, colors: coloured, accents: accented, fonts,
        names: named,
      });
      // The node holds the question now, so the next save diffs from here.
      // Not when it did not land: the edit must still be sent next time.
      if (onNodes || !styled.length) base.reset(widgets);
      setDirty(false);
      toast.success("Layout saved - it is live on Analytics now");
      setData(await loadAnalytics(range));
    } catch (e) { toast.error("Could not save the layout", e.message); }
    finally { setBusy(false); }
  }

  if (error) return <ErrorNote error={error} />;
  if (!meta || !data) return <SkelEditor />;

  const ranges = (data.ranges || ["today", "week", "month"]).filter((r) => r !== "custom");

  return (
    <section className="page active">
      <div className="page-head">
        <div className="page-title">Analytics Builder</div>
        <div className="page-actions" style={{ display: "flex", gap: 8,
                                               alignItems: "center", flexWrap: "wrap" }}>
          {ranges.map((r) => (
            <button key={r} className={"range-btn" + (r === range ? " active" : "")}
                    onClick={() => setRange(r)}>
              {r.charAt(0).toUpperCase() + r.slice(1)}
            </button>
          ))}
          {dirty ? <Badge tone="warn">unsaved</Badge> : <Badge tone="ok">saved</Badge>}
          <button className="btn btn-accent" disabled={!editable || !dirty || busy}
                  onClick={save}>Save</button>
          <Link className="btn btn-sm" to="/analytics">Back to Analytics</Link>
        </div>
      </div>

      {!editable && (
        <div className="note-card">Read-only: composing a layout is an admin action.</div>
      )}

      <div className="builder-grid">
        <aside className="builder-palette">
          {/* Nothing static suits Analytics today (ANALYTICS_PALETTE is
              empty): the group is drawn only when it has something. */}
          {ANALYTICS_PALETTE.length > 0 && <div className="section-title">Widgets</div>}
          {ANALYTICS_PALETTE.map((item, idx) => (
            <div key={`${item.kind}-${item.source}`} className="palette-item" draggable
                 onDragStart={(e) => {
                   e.dataTransfer.setData("application/x-palette", String(idx));
                   notePaletteDrag(item.kind);
                 }}
                 onDoubleClick={() => add(item)}>
              <span className="palette-icon">{item.icon}</span>
              <span>
                <strong>{item.label}</strong>
                <em>{item.hint}</em>
              </span>
            </div>
          ))}
          {ANALYTICS_PALETTE.length > 0 && (
            <p className="muted small">Drag onto the canvas, or double-click to append.</p>
          )}

          {/* The cards drawn on the Workflow canvas, by what they show
              (Tok Aba, 2026-10-14). They arrive set up as drawn. */}
          <WorkflowCardsPalette catalog={catalog} editable={editable}
                                onAdd={(c) => addFromCatalog(c)}
                                storageKey="builder.cards.analytics"
                                // The card selected on the canvas opens its heading:
                                // a linked copy names its node, a compiled one is wf_<node>.
                                selectedNode={current ? (current.node
                                  || (String(current.id).startsWith("wf_") ? String(current.id).slice(3) : null))
                                  : null} />

          {removed.length > 0 && (
            <>
              {/* "Unused", not "Removed": these cards still exist and one
                  click puts them back, and a heading that says "removed"
                  reads as gone-for-good -- the opposite of what the list is
                  for. */}
              <div className="filter-bar" style={{ marginTop: 12, alignItems: "center" }}>
                <div className="section-title" style={{ flex: 1 }}>Unused</div>
                <button className="btn btn-sm" disabled={busy || !editable}
                        onClick={clearAllHidden}>Insert all</button>
              </div>
              {removed.map((h) => (
                <button key={h.id} className="btn btn-sm" disabled={busy || !editable}
                        style={{ display: "block", width: "100%", textAlign: "left",
                                 marginBottom: 4 }}
                        onClick={() => restore(h.id)}>
                  ↺ {h.title}
                </button>
              ))}
            </>
          )}
        </aside>

        <div className="builder-canvas"
             onDragOver={(e) => e.preventDefault()}
             onDrop={onDrop}>
          <div className="filter-bar" style={{ marginTop: 0, marginBottom: 8,
                                               alignItems: "center", justifyContent: "flex-end" }}>
            {/* Beside View, over the canvas it changes -- the header is for
                what leaves this page (Save, Back), and a control that resizes
                what you are looking at belongs next to what you are looking
                at. The grid stays twelve columns wide, so nothing already
                arranged moves: a card that spans 3x2 still spans 3x2, and the
                whole page grows or shrinks together. */}
            <div style={{ display: "flex", gap: 4 }}>
              {[["small", "Small"], ["medium", "Medium"], ["big", "Big"]].map(([v, label]) => (
                <button key={v} type="button" title={`${label} grid`}
                        className={"btn btn-sm" + (size === v ? " btn-accent" : "")}
                        disabled={!editable}
                        onClick={() => { setSize(v); setDirty(true); }}>
                  {label}
                </button>
              ))}
            </div>
            <ViewSettings />
          </div>
          {!widgets.length && (
            <EmptyState icon={LayoutGrid} title="Nothing here yet"
                        hint="Drag a widget from the left to start building." />
          )}
          <GridCanvas widgets={widgets} preview={preview} selected={selected}
                      editable={editable} setSelected={setSelected}
                      patch={patch} remove={remove} size={size}
                      onDropCell={(e, cell) => {
                        const pi = e.dataTransfer.getData("application/x-palette");
                        if (pi !== "") {
                          const item = ANALYTICS_PALETTE[Number(pi)];
                          if (item) add(item, cell);
                          return;
                        }
                        const ci = e.dataTransfer.getData("application/x-catalog");
                        const entry = ci !== "" ? catalog[Number(ci)] : null;
                        if (entry) addFromCatalog(entry, cell);
                      }} />
        </div>

        <aside className="builder-inspector rail-form">
          <div className="section-title">Properties</div>
          {!current && <p className="muted small">Select a widget to edit it.</p>}
          {current?.pkg ? (
            <p className="muted small">
              <b>{current.title}</b> is from this system's package - drag to move, × to remove. Not editable here.
            </p>
          ) : (
            <WidgetInspector current={current} meta={meta} patch={patch} siblings={widgets}
                             preview={preview} />
          )}
        </aside>
      </div>

    </section>
  );
}
