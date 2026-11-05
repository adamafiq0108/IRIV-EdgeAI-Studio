// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Dashboard Builder - drag widgets from the palette onto the canvas, bind each
// to a live data key, publish the result as a deployable system package.
//
// The canvas is ALWAYS the 12-column grid. It used to have an off mode where
// widgets flowed as a plain list, which meant a layout could be saved in a
// shape the dashboard had to guess how to render - and the two disagreed. One
// mode, one geometry, one answer: a widget sits where you drag it.
//
// Drag and drop is the native HTML5 API, not a library.
//
// The preview is rendered by the SAME endpoint and the SAME <Widget> the
// Analytics page uses, against live fleet data. A builder whose preview is a
// mock teaches operators to distrust it.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import { ErrorNote, SkelEditor } from "../components/Loading.jsx";
// The canvas, its geometry and the auto-flow placer are shared with the
// Analytics page, so arranging widgets works identically in both.
import GridCanvas, { autoPlace, notePaletteDrag, placedAt } from "../components/widgets/GridCanvas.jsx";
// Palette, widget factories, the debounced preview and the properties panel are
// shared with the Analytics Builder - see components/widgets/builderShared.jsx.
import {
  DASHBOARD_PALETTE, WorkflowCardsPalette, WidgetInspector, catalogWidget, newWidget, setFont, setOverride,
  isChartKind, seriesStyle, chartLook, useLivePreview, verdictLabels,
  cardQuestionEdits, useRowBase,
} from "../components/widgets/builderShared.jsx";
import { Badge, Dialog, EmptyState, Field } from "../components/ui/primitives.jsx";
import { useToast } from "../components/ui/Toast.jsx";
import ViewSettings from "../components/ui/ViewSettings.jsx";
import NextPageButton from "../components/NextPageButton.jsx";
import { ArrowLeft, LayoutGrid } from "lucide-react";
import { authHeaders } from "../lib/session.js";
import { apiUrl } from "../lib/base.js";
import PageGuideHost from "../workflow/PageGuides.jsx";


export default function Builder() {
  const toast = useToast();
  // ?new=<slug>&name=<display name> (the hub's "New system" card): compose a
  // NEW system on this master's live data. The canvas starts empty, the
  // publish fields are pre-set to the new identity, and Save (which writes
  // THIS master's layout) is disabled so the host system stays untouched -
  // Publish → Install is the way out.
  const [params] = useSearchParams();
  const newSlug = (params.get("new") || "").trim();
  const newName = (params.get("name") || "").trim();
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState(null);
  // This editor is the DASHBOARD Builder: it edits the "main" page and only
  // that (core/services/builder.py MAIN_PAGE_ID). It used to grow page tabs
  // and a "+ Page" button, which made it a second, competing answer to the
  // question the Analytics Builder already answers -- and two builders that
  // both claim the same widgets is a "where do I edit this?" with no answer.
  // Only "main" is ever loaded into state -- the save below is a `partial`
  // write, so every other page in the document is left untouched on disk
  // without this editor having to hold (and risk saving back a stale copy
  // of) any of it.
  const [pages, setPages] = useState([{ id: "main", name: "Dashboard", widgets: [] }]);
  const activePage = Math.max(0, pages.findIndex((p) => p.id === "main"));
  const widgets = pages[activePage]?.widgets || [];
  // The row height lives ON the page, so it saves with it and the finished
  // dashboard matches the canvas. Medium is what every layout was built at.
  const size = pages[activePage]?.size || "medium";
  const setSize = (value) => setPages((ps) => ps.map(
    (p, i) => (i === activePage ? { ...p, size: value } : p)));
  // A plain function (not memoised) so it always targets the CURRENT page.
  const setWidgets = (updater) => {
    setPages((ps) => ps.map((p, i) => i === activePage
      ? { ...p, widgets: typeof updater === "function" ? updater(p.widgets) : updater }
      : p));
    setDirty(true);
  };
  const [selected, setSelected] = useState(null);
  const [dirty, setDirty] = useState(false);
  // Every row as this editor loaded it: what a save diffs a compiled card's
  // question against (cardQuestionEdits), so only what changed goes back.
  const base = useRowBase();
  const [publishOpen, setPublishOpen] = useState(false);
  const [pub, setPub] = useState({ slug: "", display_name: "", subtitle: "", icon: "" });

  useEffect(() => {
    api.get("/api/builder")
      .then((d) => {
        setMeta(d);
        // This editor only ever edits "main" -- see the note above the
        // `pages` state below -- so only that page is kept in state. A save
        // sends `partial: true` and omits every other page entirely, which is
        // what tells the server "leave those alone"; holding them here too
        // would just be a second, staler copy that a save could clobber the
        // real one with.
        const allPages = d.layout?.pages && d.layout.pages.length
          ? d.layout.pages
          : [{ id: "main", name: "Dashboard", widgets: d.layout?.widgets || [] }];
        const loaded = allPages.filter((p) => p.id === "main");
        if (!loaded.length) loaded.push({ id: "main", name: "Dashboard", widgets: [] });
        // The canvas IS the grid - there is no other mode - so a layout saved
        // before placements existed gets flowed onto it on the way in rather
        // than rendering as a pile in the top-left cell. Hidden cards (removed
        // wf_* cards) stay out of the draft; the layout keeps their docs so
        // the Analytics Builder can offer them back.
        const hidden = new Set(d.layout?.hidden || []);
        const placedAll = d.layout?.placements || {};
        const drafted = newSlug
          ? [{ id: "main", name: "Dashboard", widgets: [] }]
          : loaded.map((p) => {
              const placed = placedAll[p.id] || {};
              const ws = (p.widgets || [])
                .filter((w) => !hidden.has(w.id))
                .map((w) => (placed[w.id] ? { ...w, grid: placed[w.id] } : w));
              return { ...p, widgets: autoPlace(ws) };
            });
        base.reset(drafted.flatMap((p) => p.widgets || []));
        setPages(drafted);
        setPub((p) => ({
          ...p,
          slug: newSlug || d.slug,
          display_name: newName || newSlug || d.slug,
        }));
      })
      .catch(setError);
    // newSlug/newName come from the URL and are fixed for this mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live preview, debounced: every edit re-renders against real data, but a
  // drag across ten slots must not be ten round trips.
  // The SELECTED card is named to the hook: a row being typed into is
  // incomplete until it is finished, and that is not a page-wide failure.
  const preview = useLivePreview(widgets, !!meta, undefined, selected);

  const sources = meta?.sources || { stat: [], bucket: [], events: [] };
  const editable = meta?.editable;

  // `cell`: dropped on the canvas -- placed where the pointer let go
  // (GridCanvas onDropCell). Without one (click, or a drop beside the grid)
  // it goes after everything, as it always did.
  function add(item, atIndex, cell) {
    const made = newWidget(item, sources);
    const widget = cell ? placedAt(made, cell) : made;
    base.add(widget);
    setWidgets((list) => {
      const copy = [...list];
      copy.splice(atIndex ?? copy.length, 0, widget);
      return autoPlace(copy);
    });
    setSelected(widget.id);
  }

  /** A catalogue entry, already configured, dropped in as an ordinary widget. */
  function addFromCatalog(c, atIndex, cell) {
    const made = catalogWidget(c);
    const widget = cell ? placedAt(made, cell) : made;
    base.add(widget);
    setWidgets((list) => {
      const copy = [...list];
      copy.splice(atIndex ?? copy.length, 0, widget);
      return autoPlace(copy);
    });
    setSelected(widget.id);
  }

  function patch(id, fields) {
    setWidgets((list) => list.map((w) => (w.id === id ? { ...w, ...fields } : w)));
  }

  function remove(id) {
    setWidgets((list) => list.filter((w) => w.id !== id));
    if (selected === id) setSelected(null);
    // A wf_* card is rebuilt from its workflow node on every compile, so
    // deleting its row would quietly come back. Hiding is what sticks -- and
    // it is written immediately (not on Save) so a recompile racing this
    // editor cannot resurrect the card in between.
    if (String(id).startsWith("wf_")) hideCompiled(id);
  }

  // Hiding is read-modify-write on a shared list; serialise the writes so two
  // quick removals cannot each put back the other's card.
  const hideQueue = useRef(Promise.resolve());

  function hideCompiled(id) {
    const run = hideQueue.current.then(async () => {
      const layout = (await api.get("/api/builder")).layout || {};
      const hidden = layout.hidden || [];
      if (!hidden.includes(id)) {
        await api.put("/api/builder", { ...layout, partial: true,
                                        hidden: [...hidden, id] });
      }
    });
    hideQueue.current = run.catch(() => {});
    run.then(() => toast.success("Card hidden",
                                 "Its node keeps computing. Restore it from "
                                 + "the Analytics Builder's Unused list."))
       .catch((e) => toast.error("Could not remove the card", e.message));
  }

  /** Drop from the palette onto the canvas. Reordering is not a thing here:
      position is the grid cell you drag the widget to, not its index in a
      list. */
  function onDropAt(index, e) {
    e.preventDefault();
    const paletteIdx = e.dataTransfer.getData("application/x-palette");
    if (paletteIdx !== "") {
      const item = DASHBOARD_PALETTE[Number(paletteIdx)];
      if (item) add(item, index);
      return;
    }
    const catalogIdx = e.dataTransfer.getData("application/x-catalog");
    if (catalogIdx !== "") {
      const item = (meta.catalog || [])[Number(catalogIdx)];
      if (item) addFromCatalog(item, index);
    }
  }

  async function save() {
    // A bare `{ pages }` write would reset `hidden` and `placements` to empty
    // (the server validates what it is given, it does not merge) - every card
    // removed or repositioned on the Analytics side would silently come back.
    // Re-read and carry them, and record the dragged wf_* grids as placements:
    // those rows are compiler-owned, so a placement is where their position
    // actually lives.
    //
    // `partial: true` (see the PUT below) is what makes this safe against a
    // workflow recompile racing this save: this editor only ever sends the
    // "main" page, so merge_partial (core/services/builder.py) drops the
    // wf_* rows out of what it sends and re-attaches whatever is freshly on
    // disk for them, and leaves every OTHER page untouched rather than
    // overwriting it with the stale copy this editor loaded at mount. A full
    // (non-partial) write here was the actual bug behind renames/colour/size
    // reverting after a restart: it clobbered whatever a wf_* card recompile
    // had just written moments earlier.
    const job = (async () => {
      await hideQueue.current;
      const layout = (await api.get("/api/builder")).layout || {};
      const hidden = layout.hidden || [];
      const placements = { ...(layout.placements || {}) };
      const stored = Object.fromEntries(
        (layout.pages || []).map((p) => [p.id, p.widgets || []]));
      // How each wf_* card is dressed travels as an override beside its
      // position: both are operator decisions about a server-owned row, and a
      // template written onto the row itself is discarded by the merge.
      const dressed = { ...(layout.templates || {}) };
      // Same story for the card's own background colour: a second,
      // independent override map, since `bg` is a different knob from
      // `template`.
      const coloured = { ...(layout.colors || {}) };
      // ...and the ACCENT (a chart's stroke, a tile's accent paint), which had
      // no override map at all: it was written onto the compiler-owned row and
      // thrown away by the next merge.
      const accented = { ...(layout.accents || {}) };
      // How big a compiled card's words are -- an override for the same
      // reason as the three above.
      const fonts = { ...(layout.fonts || {}) };
      // ...and what a card is CALLED. Renaming is the first thing anybody does
      // to a dashboard, and it was the one edit with nowhere to live: typed in,
      // written onto a server-owned row, gone at the next compile. A compiled
      // card sends its name to its node with the rest of its look; a PACKAGE
      // card has no node, so this map is its only home.
      const named = { ...(layout.names || {}) };
      const styled = [];
      const compiledCards = [];
      const out = pages.map((p) => {
        const placed = { ...(placements[p.id] || {}) };
        for (const w of p.widgets || []) {
          if (String(w.id).startsWith("wf_")) {
            // Back onto the node that publishes it -- see the note in
            // AnalyticsBuilder's save and /api/workflow/card-style.
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
                          // compile, so these are the half of them that has
                          // to live where the rebuild reads from.
                          ...(isChartKind(w.kind)
                            ? { series: seriesStyle(w, base.get(w.id)) } : {}),
                          // ...and how it is DRAWN (axis titles, bars,
                          // values on the chart): look, kept on the node.
                          ...chartLook(w),
                          // ...and its QUESTION where it was changed here --
                          // see AnalyticsBuilder's save.
                          ...cardQuestionEdits(base.get(w.id), w) });
            compiledCards.push(w);
          } else if (w.node) {
            // A LINKED COPY keeps its own look, but its DATA -- the threshold
            // bands, a verdict's state rows -- is the node's and is rewritten
            // from it on every recompile. So the bands and the words go to
            // the node, and only those (see AnalyticsBuilder for why the
            // bands joined).
            const words = w.kind === "verdict" ? verdictLabels(w) : {};
            // ...and its question: the copy's body is refreshed from the
            // node on every recompile, so it has to reach the node.
            styled.push({ id: `wf_${w.node}`, bands: w.bands || {}, ...words,
                          ...cardQuestionEdits(base.get(w.id), w) });
          } else if (w.pkg) {
            // A package card is declared in Python: there is no node to send
            // its look to, so the maps ARE where it lives. Writing them here
            // is what the wf_ branch used to do to these cards by mistake --
            // clearing the entry and never putting it back, which is how a
            // package card lost its colour on the next save.
            setOverride(dressed, w.id, w.template, "plain");
            setOverride(coloured, w.id, w.bg, "");
            setOverride(accented, w.id, w.color, "");
            setOverride(named, w.id, w.title, "");
            setFont(fonts, w.id, w);
          }
          if (String(w.id).startsWith("wf_") || placed[w.id]) placed[w.id] = w.grid;
        }
        if (Object.keys(placed).length) placements[p.id] = placed;
        // Hidden cards were filtered off this canvas at load; their layout
        // rows must survive the write or there is nothing left to restore.
        const drafted = new Set((p.widgets || []).map((w) => w.id));
        const kept = (stored[p.id] || [])
          .filter((w) => hidden.includes(w.id) && !drafted.has(w.id));
        // The rows go exactly as they stand. A save must not change how a
        // card DRAWS: the series list it carries (or does not) decides which
        // server path renders it, so a chart that has none is sent with none
        // (builderShared.jsx, beside widgetSeries, says what rewriting that
        // here did to the charts).
        const rows = p.widgets || [];
        return { ...p, widgets: kept.length ? [...rows, ...kept] : rows };
      });
      // Did the look actually land on the nodes AND come back out as a
      // recompiled layout? The route answers ok:false when the graph has an
      // error (the node is written, the layout is not), and throws when the
      // caller may edit dashboards but not the canvas. Either way the wf_*
      // rows this partial save sends are dropped by merge_partial, so the
      // maps are the only place the choice survives until the canvas
      // compiles again. Deleting them first and asking second was how a
      // template picked five times went back to default five times.
      let onNodes = false;
      if (styled.length) {
        try {
          const r = await api.post("/api/workflow/card-style", { cards: styled });
          onNodes = !r || r.ok !== false;
        } catch {
          onNodes = false;
        }
      }
      for (const w of compiledCards) {
        if (onNodes) {
          // The node now holds every one of those, so any leftover override
          // is a second answer that would quietly win over the one just
          // written.
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
      if (styled.length && !onNodes) {
        toast.info("The canvas could not take the card styling yet - it is "
                   + "kept on the dashboard, and moves to the canvas when the "
                   + "workflow compiles clean.");
      }
      await api.put("/api/builder", { ...layout, partial: true, pages: out, hidden,
                                      placements, templates: dressed, colors: coloured,
                                      accents: accented, fonts, names: named });
      // The node holds the question now, so the next save diffs from here.
      // Not when it did not land: the edit must still be sent next time.
      if (onNodes || !styled.length) base.reset(pages.flatMap((p) => p.widgets || []));
    })();
    await toast.run(job,
                    { success: "Layout saved - it is live now",
                      failure: "Could not save the layout" });
    setDirty(false);
  }

  async function publish(target) {
    // Installing under THIS system's own slug is not a new package - it is
    // "save this dashboard", and the install path would only error with
    // "already exists". Do the save instead of failing.
    if (target === "install" && !newSlug && pub.slug === meta.slug) {
      await save();
      setPublishOpen(false);
      toast.info("This is the current system - dashboard saved (no new package needed).");
      return;
    }
    const body = { slug: pub.slug, layout: { widgets }, meta: pub, target };
    if (target === "install") {
      const d = await toast.run(api.post("/api/builder/publish", body),
                                { success: "Package installed",
                                  failure: "Publish failed" });
      toast.info(d.next);
      setPublishOpen(false);
      return;
    }
    // Download: the zip is a binary body, so this goes around the JSON client.
    const res = await fetch(apiUrl("/api/builder/publish"), {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      toast.error("Publish failed", data?.error || `HTTP ${res.status}`);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${pub.slug}-system-package.zip`;
    a.click();
    URL.revokeObjectURL(url);
    setPublishOpen(false);
    toast.success("Package downloaded - drop it into your deployment repo");
  }

  const current = useMemo(() => widgets.find((w) => w.id === selected) || null,
                          [widgets, selected]);

  if (error) return <ErrorNote error={error} />;
  if (!meta) return <SkelEditor />;

  return (
    <section className="page active">
      <div className="page-head">
        {/* The way out, at the left where a Back button is expected: keep the work (Save, on the
            right) or leave it. */}
        <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
          {!newSlug && (
            <Link className="btn" to="/"
                  style={{ display: "inline-flex", alignItems: "center", gap: 6,
                           whiteSpace: "nowrap" }}
                  title={dirty
                    ? "Back to the dashboard - your unsaved changes stay here"
                    : "Back to the dashboard"}>
              <ArrowLeft size={15} /> Back to dashboard
            </Link>
          )}
          <div className="page-title">Dashboard Builder{newSlug ? ` - new system “${newName || newSlug}”` : ""}</div>
        </div>
        <div className="page-actions">
          <PageGuideHost guide="dashboardBuilder" />
          {/* The way OUT. Composing a new system is a flow that ends in
              Install, but editing this system's dashboard just... ends, and
              the only way back to the thing being edited was the sidebar or
              the browser's Back button. Hidden during the ?new= flow, where
              there is no dashboard to return to yet.

              Deliberately not a "Done" button that saves and leaves: Save is
              its own control right here, and one button doing two things is
              how unsaved work gets lost by someone who meant only to look. */}
          {!newSlug && (dirty ? <Badge tone="warn">unsaved</Badge> : <Badge tone="ok">saved</Badge>)}
          {!newSlug && (
            <button className="btn" disabled={!editable || !dirty} onClick={save}>Save</button>
          )}
          {/* Publish… is gone from the ordinary builder: templates are
              published by dropping a system into the Built-in templates
              folder on the Systems grid, and a second publish flow here was
              a second answer to the same question. The dialog machinery
              stays for the ?new= composing flow, which still installs. */}
          {newSlug && (
            <button className="btn btn-accent" disabled={!editable || !widgets.length}
                    onClick={() => setPublishOpen(true)}>Publish…</button>
          )}
          <NextPageButton />
        </div>
      </div>

      {!editable && (
        <div className="note-card">Read-only: composing a layout is an admin action.</div>
      )}

      {newSlug && (
        <div className="info-box">
          Composing new system <b>{newName || newSlug}</b> (slug <code>{newSlug}</code>). Nothing
          changes {meta.slug ? <code>{meta.slug}</code> : "this system"} until you <b>Publish → Install</b>.
        </div>
      )}

      <div className="builder-grid">
        <aside className="builder-palette">
          {/* The few widgets no workflow node makes (DASHBOARD_PALETTE);
              every card that reads data is a Dashboard card, below. */}
          <div className="section-title">Widgets</div>
          {DASHBOARD_PALETTE.map((item, idx) => (
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
          <p className="muted small">Drag onto the canvas, or double-click to append.</p>

          {/* The cards drawn on the Workflow canvas, by what they show
              (Tok Aba, 2026-10-14). They arrive set up as drawn, linked to
              their node. */}
          <WorkflowCardsPalette catalog={meta.catalog || []} editable={editable}
                                onAdd={(c) => addFromCatalog(c)}
                                storageKey="builder.cards.dashboard"
                                // The card selected on the canvas opens its heading:
                                // a linked copy names its node, a compiled one is wf_<node>.
                                selectedNode={current ? (current.node
                                  || (String(current.id).startsWith("wf_") ? String(current.id).slice(3) : null))
                                  : null} />
        </aside>

        <div className="builder-canvas"
             onDragOver={(e) => e.preventDefault()}
             onDrop={(e) => onDropAt(widgets.length, e)}>
          {/* One page, so no tabs: this canvas IS the Dashboard. Widgets for
              the Analytics Overview are composed in the Analytics Builder. */}
          <div className="filter-bar" style={{ marginBottom: 10, gap: 6, flexWrap: "wrap" }}>
            <div style={{ flex: 1 }} />
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
                          const item = DASHBOARD_PALETTE[Number(pi)];
                          if (item) add(item, undefined, cell);
                          return;
                        }
                        const ci = e.dataTransfer.getData("application/x-catalog");
                        const entry = ci !== "" ? (meta.catalog || [])[Number(ci)] : null;
                        if (entry) addFromCatalog(entry, undefined, cell);
                      }} />
        </div>

        <aside className="builder-inspector rail-form">
          <div className="section-title">Properties</div>
          {!current && <p className="muted small">Select a widget to edit it.</p>}
          <WidgetInspector current={current} meta={meta} patch={patch} siblings={widgets}
                           preview={preview} />
        </aside>
      </div>

      <Dialog open={publishOpen} title="Publish as a system package"
              onClose={() => setPublishOpen(false)}
              footer={
                <>
                  <button className="btn" onClick={() => publish("download")}>Download .zip</button>
                  <button className="btn btn-accent" onClick={() => publish("install")}>
                    Install on this host
                  </button>
                </>
              }>
        <p className="muted small">
          Creates a new system package. Install refuses to overwrite a different existing one.
        </p>
        <p className="muted small">
          To update <b>this</b> system's dashboard, use <b>Save</b> instead - no publish needed.
        </p>
        <Field label="Slug" hint="lowercase, underscores">
          <input className="input" value={pub.slug}
                 onChange={(e) => setPub({ ...pub, slug: e.target.value })} />
        </Field>
        <Field label="Display name">
          <input className="input" value={pub.display_name}
                 onChange={(e) => setPub({ ...pub, display_name: e.target.value })} />
        </Field>
        <Field label="Subtitle">
          <input className="input" value={pub.subtitle}
                 onChange={(e) => setPub({ ...pub, subtitle: e.target.value })} />
        </Field>
      </Dialog>
    </section>
  );
}
