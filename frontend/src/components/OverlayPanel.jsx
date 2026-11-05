// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Overlay - the workflow rules that DRAW on the feed (a proximity link line,
// a guard bubble), as a card of switches with an add button.
//
// It lives on the ZONES page, under the feed: an overlay is a thing you place
// while looking at the picture, the same act as drawing a zone -- it was on
// Camera Setup before, where it sat beside controls about the camera itself
// rather than about what is watched on it.
//
// Self-contained: fetches its own /api/workflow, saves the whole graph back
// (the API re-cleans and recompiles on every save; there is no per-node
// patch), so the page hosting it needs no wiring.
import { useEffect, useRef, useState } from "react";
import { useApi, usePoll } from "../api/hooks.js";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { Badge } from "./ui/primitives.jsx";
import { Switch } from "./ui/switch.jsx";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { mintId } from "../workflow/registry.js";

// The workflow node types that draw on the feed. Kept in sync with the server
// registry (core/services/workflow.py) -- adding one here without one there
// only produces a node the compiler drops.
const OVERLAY_TYPES = ["analytics.proximity", "analytics.guard",
                       "analytics.trails"];

// Starting data for a rule created from this card. Deliberately usable as-is
// (person ↔ person, person intrudes) so the switch has something to switch;
// fnId is left blank because the server mints it on save.
const OVERLAY_SEED = {
  "analytics.proximity": {
    fnId: "", name: "Proximity", class_a: "person", class_b: "person",
    max_distance: 0.15, draw_link: "always", severity: "warn",
    message: "", cooldown_s: 10,
  },
  "analytics.guard": {
    fnId: "", name: "Guard zone", classes: [], margin_pct: 50,
    alert_classes: ["person"], draw: true, severity: "warn",
    message: "", cooldown_s: 10,
  },
  // Movement trails: every class by default, 10s of history -- usable as-is.
  "analytics.trails": {
    fnId: "", name: "Object motion tracking", classes: [], length_s: 10, draw: true,
  },
};

export default function OverlayPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  // usePoll, not a bare fetch: it keeps the last good payload per path for
  // the life of the tab, so opening Camera Setup again paints the overlay
  // rules AT ONCE instead of waiting on /api/workflow -- which compiles the
  // whole graph before it answers, and was the delay every time.
  // The rules (the graph) change only when this panel or the Workflow page
  // saves, so they load once (and again after this panel's own saves); the
  // live values come from the light /api/workflow/live poll (perf item L2).
  const { data: wf, reload: load } = useApi("/api/workflow");
  const { data: live } = usePoll("/api/workflow/live", 5000);
  // A save from the Workflow page (or another tab) moves the workflow's
  // stamp: reload the rules then, as the old 5 s poll of the whole payload
  // did -- without paying for it every 5 s when nothing changed.
  const stamp = live?.workflow_stamp;
  const seenStamp = useRef(stamp);
  useEffect(() => {
    if (stamp === undefined) return;
    if (seenStamp.current !== undefined && seenStamp.current !== stamp) {
      load().catch(() => {});
    }
    seenStamp.current = stamp;
  }, [stamp, load]);
  const [busy, setBusy] = useState(false);

  // Nothing cached yet: show the card and its heading rather than nothing at
  // all. A panel that appears late reads as a page still loading; a panel
  // that is there and filling in reads as a panel.
  if (!wf) {
    return (
      <div className="panel-card">
        <div className="section-title">Overlay</div>
        <p className="muted small" style={{ margin: 0 }}>Loading overlay rules…</p>
      </div>
    );
  }

  const graph = wf.workflow?.graph || {};
  const nodes = graph.nodes || [];
  const values = live?.values || wf.values || {};
  const editable = !!wf.editable;
  const rules = nodes.filter((n) => OVERLAY_TYPES.includes(n.type));

  async function saveGraph(nextNodes, okMsg) {
    setBusy(true);
    try {
      await api.put("/api/workflow", { graph: { nodes: nextNodes, edges: graph.edges || [] } });
      toast.success(okMsg);
      load();
    } catch (e) { toast.error("Could not save the overlay", e.message); }
    finally { setBusy(false); }
  }

  function toggle(id) {
    saveGraph(nodes.map((n) => (n.id === id
      ? { ...n, data: { ...(n.data || {}), disabled: !n.data?.disabled } }
      : n)), "Overlay updated");
  }

  function addRule(type) {
    saveGraph([...nodes, { id: mintId(), type,
                           position: { x: 80, y: 80 + rules.length * 140 },
                           data: { ...OVERLAY_SEED[type] } }],
              "Rule added - open the Workflow to tune it");
  }

  async function removeRule(id) {
    // The node goes; its edges are dangling and clean_graph drops them on the
    // save. Confirmed first: this deletes a workflow node, and anything the
    // operator wired after it on the canvas loses its source.
    if (!await confirm({
      title: "Remove this rule?",
      description: "Anything wired after it on the Workflow canvas loses its "
        + "input.",
      destructive: true, confirmLabel: "Remove" })) return;
    saveGraph(nodes.filter((n) => n.id !== id), "Rule removed");
  }

  return (
    <div className="panel-card">
      <div className="section-title">Overlay</div>

      {!rules.length && (
        <p className="muted small" style={{ marginTop: 6 }}>No overlay rules yet.</p>
      )}
      {rules.map((n) => {
        const d = n.data || {};
        const prox = n.type === "analytics.proximity";
        const trail = n.type === "analytics.trails";
        const summary = prox
          ? `${d.class_a || "any"} ↔ ${d.class_b || "any"} · alert under `
            + `${Math.round((Number(d.max_distance) || 0) * 100)}% of frame width`
          : trail
          ? `dotted trail behind ${(d.classes || []).length ? d.classes.join(", ") : "every object"}`
            + ` · ${d.length_s ?? 10}s of movement`
          : `bubble around ${(d.classes || []).length ? d.classes.join(", ") : "every class"}`
            + ` · alerts on ${(d.alert_classes || []).length ? d.alert_classes.join(", ") : "-"}`;
        // No fnId yet means the node has never compiled, so it publishes nothing.
        const stat = d.fnId
          ? (prox ? `${values[`fn_${d.fnId}_pairs`] ?? 0} pairs close`
             : trail ? `${values[`fn_${d.fnId}_tracked`] ?? 0} tracked`
                     : `${values[`fn_${d.fnId}_inside_now`] ?? 0} inside now`)
          : "not compiled yet";
        return (
          // Label left, switch hard right -- one column of switches down the
          // card instead of three different places to look.
          <div key={n.id} style={{ padding: "10px 0",
                                   borderBottom: "1px solid var(--line)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <span className="truncate-1" style={{ display: "inline-block",
                                                      maxWidth: "100%" }}>
                  {d.name || (prox ? "Proximity"
                              : trail ? "Object motion tracking" : "Guard zone")}
                </span>{" "}
                <Badge tone={d.disabled ? "muted" : "ok"}>{stat}</Badge>
              </div>
              <Switch checked={!d.disabled} disabled={!editable || busy}
                      onCheckedChange={() => toggle(n.id)}
                      aria-label={`Enable ${d.name || n.type}`} />
              <button className="icon-btn" title="Remove this rule"
                      disabled={!editable || busy}
                      aria-label={`Remove ${d.name || n.type}`}
                      onClick={() => removeRule(n.id)}>×</button>
            </div>
            <div className="muted small" style={{ marginTop: 2 }}>{summary}</div>
            <Link className="btn btn-sm" to="/workflow"
                  style={{ marginTop: 6 }}>Edit in Workflow →</Link>
          </div>
        );
      })}

      {/* Always offered: a line and a bubble RUN TOGETHER, and a class can
          carry one of each (or several) - adding is never gated on empty. */}
      <div className="filter-bar" style={{ marginTop: rules.length ? 10 : 4 }}>
        <button className="btn btn-sm" disabled={!editable || busy}
                onClick={() => addRule("analytics.proximity")}>Add link line</button>
        <button className="btn btn-sm" disabled={!editable || busy}
                onClick={() => addRule("analytics.guard")}>Add guard zone</button>
        <button className="btn btn-sm" disabled={!editable || busy}
                onClick={() => addRule("analytics.trails")}>Add motion tracking</button>
      </div>
    </div>
  );
}
