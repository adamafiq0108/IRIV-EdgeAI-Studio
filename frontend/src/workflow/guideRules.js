// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// What a node may and may not be connected with, for the Node Guideline page.
//
// The canvas's own connection check (registry.edgeAllowed) answers "may a value
// flow from this kind of node to that one" by pipeline order. It does not know
// that some nodes have no way in (a Zone Filter only names an area; an Inject
// only starts a flow) or no way out (a Snapshot, an Email: they act, and hand on
// nothing). A guideline that listed those wires would teach something the canvas
// cannot even draw, so this adds the two facts the guide already holds
// (workflow/guidelineText.js) and puts the plain-words reason beside every
// refusal. Kept apart from the page so a test can ask it directly.
import { CATEGORY_STYLE, STAGES, edgeAllowed } from "./registry.js";
import { LINKS, NODE_GUIDE } from "./guidelineText.js";

//: Never offered as a "from" or "to": canvas furniture that takes anything, and
//: the function brackets, which are not a stage in a pipeline.
export const FURNITURE = ["note.comment", "common.debug", "common.link_in", "common.link_out",
                          "function.start", "function.end", "function.call"];

/** Nodes that go only into another node's Zone point: they name an area. */
const ZONE_ONLY = new Set(["zone.ref"]);

export const stageOf = (spec) => CATEGORY_STYLE[spec?.category]?.stage || "common";
export const stageRank = (id) => Math.max(0, STAGES.findIndex((s) => s.id === id));

const portIds = (spec) => (spec?.handles || []).map((h) => (typeof h === "string" ? h : h?.id));
const firstHandle = (spec) => portIds(spec)[0] || "in";

/** Does anything wire INTO this node? (A Zone Filter or an Inject has no way in.) */
export const takesInput = (type) => NODE_GUIDE[type]?.inputs !== false;
/** Does anything come OUT of this node? (A Snapshot or an Email acts and ends.) */
export const givesOutput = (type) => !/^nothing/i.test(NODE_GUIDE[type]?.gives || "");

/** May `a` be wired into `b`: on the port a hand-written link names, or any of
 *  b's ports, by the canvas's own rule. [ok, the canvas's reason if not]. */
export function wireOk(registry, a, b) {
  const spec = registry[b];
  const ports = [LINKS[`${a}>${b}`]?.handle, firstHandle(spec), ...portIds(spec)].filter(Boolean);
  let first = null;
  for (const port of new Set(ports)) {
    const r = edgeAllowed(registry, a, b, port);
    if (r[0]) return r;
    first = first || r;
  }
  return first || edgeAllowed(registry, a, b, "in");
}

const lower = (t) => String(t || "").replace(/^nothing,\s*/i, "");

/** Why `from` cannot be wired into `to`, in words a beginner can use. */
function reasonNot(registry, from, to, rawWhy) {
  const A = registry[from], B = registry[to];
  if (!takesInput(to)) {
    const how = lower(NODE_GUIDE[to]?.takes);
    return `${B.label} does not take anything in${how ? `: ${how}` : ""}.`
      + (ZONE_ONLY.has(to)
        ? " To use it, wire it into the Zone point of a Counter, a Cycle Time or an "
          + "Object Detection." : "");
  }
  if (ZONE_ONLY.has(from)) {
    return `${A.label} only goes into the Zone point of a node that measures inside an `
      + `area (a Counter, a Cycle Time, an Object Detection). ${B.label} has no Zone point.`;
  }
  if (stageRank(stageOf(B)) < stageRank(stageOf(A))) {
    return `${B.label} comes earlier in the flow than ${A.label}. Data only moves forward `
      + "(camera, then filters, then measuring, then deciding, then doing something), "
      + "so it cannot go back.";
  }
  if (/makes its own value/.test(rawWhy || "")) {
    return `${B.label} creates its own value, so nothing can be wired into it.`;
  }
  return `${A.label} and ${B.label} are not meant to be wired together.`;
}

/** {from, to, refused: [{type, reason}]} for one node: what may be wired into it,
 *  what it may be wired to, and what it may not, with the reason. */
export function connectionsOf(registry, sel) {
  const spec = registry[sel];
  if (!spec) return { from: [], to: [], refused: [] };
  const types = Object.keys(registry).filter((t) => !FURNITURE.includes(t)
    && !registry[t].deprecated && !registry[t].hidden && t !== sel);
  const from = [], to = [], refused = [];
  for (const t of types) {
    // Into this node.
    if (takesInput(sel) && givesOutput(t)
        && (!ZONE_ONLY.has(t) || portIds(spec).includes("zone"))
        && wireOk(registry, t, sel)[0]) {
      from.push(t);
    }
    // Out of this node.
    if (!givesOutput(sel)) continue;                   // it acts and ends: nothing follows
    const zoneOnly = ZONE_ONLY.has(sel);
    if (!takesInput(t) || (zoneOnly && !portIds(registry[t]).includes("zone"))) {
      refused.push({ type: t, reason: reasonNot(registry, sel, t), strong: true });
      continue;
    }
    const [ok, why] = wireOk(registry, sel, t);
    if (ok) to.push(t);
    else refused.push({ type: t, reason: reasonNot(registry, sel, t, why), strong: false });
  }
  const order = (a, b) => stageRank(stageOf(registry[a])) - stageRank(stageOf(registry[b]))
                          || (registry[a].rank ?? 9) - (registry[b].rank ?? 9);
  from.sort(order); to.sort(order);
  // The refusals that teach most first: a node with no way in, then the rest.
  refused.sort((a, b) => Number(b.strong) - Number(a.strong)
                      || Number(b.type === "zone.ref") - Number(a.type === "zone.ref"));
  return { from, to, refused };
}
