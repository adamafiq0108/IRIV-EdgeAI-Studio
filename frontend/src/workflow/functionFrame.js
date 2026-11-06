// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The FUNCTION FRAME: one resizable box that IS a function definition.
//
// The Start is pinned to the frame's left edge and the End to its right, and
// any node whose centre lands inside the frame is part of the body. The Start
// and End are still ordinary nodes underneath -- they hold the name, the
// parameters and the returns, exactly as before, so the server's
// resolve_functions() and every graph saved before the frame existed keep
// working. The frame adds geometry: where the pins sit, which nodes are in,
// and `data.members`, the ids it holds, which the server folds into the body
// (function_bodies) so an unwired node inside a frame never runs on its own.
//
// Everything here is pure: (nodes, edges) in, positions / ids / changes out.

export const FRAME = "function.frame";
export const START = "function.start";
export const END = "function.end";
export const CALL = "function.call";

/** Height of the frame's title bar, in canvas pixels. */
export const FRAME_HEAD = 40;
/** Width the Start and End tabs are drawn at (nodes.jsx uses the same). */
export const PIN_W = 150;
/** The smallest a frame may be dragged to, before its members are counted. */
export const FRAME_MIN = { width: 460, height: 220 };
/** Space kept between the last body node and the frame's edge on resize. */
const PAD = 24;

/** The frame-less fallback size of a node React Flow has not measured yet. */
const DEFAULT_BOX = { width: 210, height: 90 };

export const fnKey = (n) => String(n?.data?.fn || "").trim().toLowerCase();

export function nodeBox(n) {
  const w = n.measured?.width ?? n.width ?? DEFAULT_BOX.width;
  const h = n.measured?.height ?? n.height ?? DEFAULT_BOX.height;
  return { x: n.position.x, y: n.position.y, width: w, height: h };
}

export function frameRect(f) {
  const d = f.data || {};
  return {
    x: f.position.x, y: f.position.y,
    width: Math.max(FRAME_MIN.width, Number(d.width) || 760),
    height: d.collapsed ? FRAME_HEAD
      : Math.max(FRAME_MIN.height, Number(d.height) || 340),
  };
}

/** The rect a collapsed frame would cover if it were open: membership is
 *  decided against this, so folding a frame never drops its body. */
function openRect(f) {
  const d = f.data || {};
  return {
    x: f.position.x, y: f.position.y,
    width: Math.max(FRAME_MIN.width, Number(d.width) || 760),
    height: Math.max(FRAME_MIN.height, Number(d.height) || 340),
  };
}

const inside = (r, px, py) => px >= r.x && px <= r.x + r.width
  && py >= r.y && py <= r.y + r.height;

/** Nodes that can never be a member of a frame: frames, and every Start or
 *  End (they belong to their frame by NAME, not by where they are). */
const isPin = (n) => n.type === START || n.type === END;
const notMember = (n) => n.type === FRAME || n.type === "note.group" || isPin(n);

/** {nodeId: frameId} for every node in a frame.
 *
 *  STICKY: read from each frame's saved `members`, not from where things are
 *  right now. Dragging a frame across other nodes must not swallow them --
 *  a node joins a function only when IT is dropped inside one (see
 *  assignDropped). A node listed by two frames belongs to the smaller. */
export function frameMembership(nodes) {
  const frames = nodes.filter((n) => n.type === FRAME)
    .sort((a, b) => area(openRect(b)) - area(openRect(a)));   // biggest first
  const out = {};
  if (!frames.length) return out;
  const ok = new Set(nodes.filter((n) => !notMember(n)).map((n) => n.id));
  for (const f of frames) {                                     // smaller wins
    for (const id of f.data?.members || []) if (ok.has(id)) out[id] = f.id;
  }
  return out;
}

const area = (r) => r.width * r.height;
const centre = (n) => {
  const b = n.type === FRAME ? openRect(n) : n.type === "note.group" ? groupRect(n) : nodeBox(n);
  return [b.x + b.width / 2, b.y + b.height / 2];
};

/** The node was just DROPPED (dragged and let go, or added): it joins the
 *  smallest frame its centre is inside and the smallest group its centre is
 *  inside, and leaves every other. This is the only way membership changes.
 *  A frame or group dropped is itself a candidate for a (bigger) group.
 *  Returns the new node list, or the same array when nothing changed. */
export function assignDropped(nodes, ids) {
  const want = new Set(ids);
  const dropped = nodes.filter((n) => want.has(n.id) && !isPin(n));
  if (!dropped.length) return nodes;
  // A FOLDED function takes no drops: all that is left of it is its title
  // bar, and a node put down where its body used to be is a node put down on
  // the empty canvas -- it must not vanish into a frame nobody can see.
  const boxes = nodes.filter((n) => (n.type === FRAME && !n.data?.collapsed)
                                   || n.type === "note.group")
    .map((b) => ({ b, r: b.type === FRAME ? openRect(b) : groupRect(b) }))
    .sort((a, c) => area(a.r) - area(c.r));                    // smallest first
  const home = {};                     // node id -> {frame, group} it goes to
  for (const n of dropped) {
    const [cx, cy] = centre(n);
    const own = n.type === FRAME ? openRect(n) : n.type === "note.group" ? groupRect(n) : null;
    let frame = null, group = null;
    for (const { b, r } of boxes) {
      if (b.id === n.id || !inside(r, cx, cy)) continue;
      if (own && area(r) <= area(own)) continue;     // a box is never inside a smaller one
      if (b.type === FRAME && !frame && !notMember(n)) frame = b.id;
      if (b.type === "note.group" && !group) group = b.id;
    }
    home[n.id] = { frame, group };
  }
  let changed = false;
  const next = nodes.map((b) => {
    if (b.type !== FRAME && b.type !== "note.group") return b;
    const had = b.data?.members || [];
    const keep = had.filter((id) => !(id in home));
    for (const [id, h] of Object.entries(home)) {
      if ((b.type === FRAME ? h.frame : h.group) === b.id) keep.push(id);
    }
    if (keep.length === had.length && keep.every((id, i) => id === had[i])) return b;
    changed = true;
    return { ...b, data: { ...b.data, members: keep } };
  });
  return changed ? next : nodes;
}

/** {frameId: {start, end}} -- the Start and End a frame owns, by name. */
export function framePins(nodes) {
  const byName = {};
  for (const n of nodes) {
    if (!isPin(n) || !fnKey(n)) continue;
    const slot = (byName[fnKey(n)] ||= {});
    const k = n.type === START ? "start" : "end";
    if (!slot[k]) slot[k] = n;
  }
  const out = {};
  for (const f of nodes) {
    if (f.type !== FRAME) continue;
    out[f.id] = byName[fnKey(f)] || {};
  }
  return out;
}

/** The frame a node belongs to -- by name for a Start / End, by geometry for
 *  everything else -- or null. */
export function frameOf(nodeId, nodes, membership, pins) {
  const n = nodes.find((m) => m.id === nodeId);
  if (!n) return null;
  if (isPin(n)) {
    for (const [fid, p] of Object.entries(pins || framePins(nodes))) {
      if (p.start?.id === nodeId || p.end?.id === nodeId) return fid;
    }
    return null;
  }
  return (membership || frameMembership(nodes))[nodeId] || null;
}

/** Where a frame's Start and End sit: on its left and right edges,
 *  vertically centred in the body. */
export function pinPositions(frame, start, end) {
  const r = openRect(frame);
  const bodyTop = r.y + FRAME_HEAD;
  const bodyH = r.height - FRAME_HEAD;
  const at = (n) => {
    if (!n) return null;
    const h = nodeBox(n).height;
    return Math.round(bodyTop + Math.max(8, (bodyH - h) / 2));
  };
  return {
    start: start ? { x: Math.round(r.x), y: at(start) } : null,
    end: end ? { x: Math.round(r.x + r.width - PIN_W), y: at(end) } : null,
  };
}

/** The smallest size a frame may be resized to: never smaller than the
 *  nodes inside it. */
export function frameMinSize(frame, nodes, membership) {
  let w = FRAME_MIN.width, h = FRAME_MIN.height;
  for (const n of nodes) {
    if ((membership[n.id]) !== frame.id) continue;
    const b = nodeBox(n);
    w = Math.max(w, b.x + b.width - frame.position.x + PIN_W + PAD);
    h = Math.max(h, b.y + b.height - frame.position.y + PAD);
  }
  return { width: Math.ceil(w), height: Math.ceil(h) };
}

/** Expand React Flow position changes so dragging a frame carries its Start,
 *  its End and every node inside it. Members already moving in the same batch
 *  (a multi-select drag) are left alone, or they would move twice. Position
 *  changes aimed at a pinned Start / End are dropped: the pins follow the
 *  frame, they are not placed by hand. */
export function expandFrameDrag(changes, nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const moving = new Set(changes.filter((c) => c.type === "position" && c.position)
    .map((c) => c.id));
  let membership = null, pins = null;
  const extra = [];
  for (const c of changes) {
    if (c.type !== "position" || !c.position) continue;
    const f = byId.get(c.id);
    if (!f || f.type !== FRAME) continue;
    const dx = c.position.x - f.position.x, dy = c.position.y - f.position.y;
    if (!dx && !dy) continue;
    membership ||= frameMembership(nodes);
    pins ||= framePins(nodes);
    const carried = nodes.filter((n) => membership[n.id] === f.id);
    const p = pins[f.id] || {};
    if (p.start) carried.push(p.start);
    if (p.end) carried.push(p.end);
    for (const n of carried) {
      if (moving.has(n.id)) continue;
      extra.push({ type: "position", id: n.id, dragging: c.dragging,
                   position: { x: n.position.x + dx, y: n.position.y + dy } });
    }
  }
  return extra.length ? [...changes, ...extra] : changes;
}

/** A frame for every Start / End pair drawn before the frame existed, sized
 *  around the pair and the body between them, so an old canvas opens looking
 *  like a new one. Returns the new frame nodes (maybe none). */
export function legacyFrames(nodes, edges, mint) {
  const framed = new Set(nodes.filter((n) => n.type === FRAME).map(fnKey));
  const starts = nodes.filter((n) => n.type === START && fnKey(n)
                                     && !framed.has(fnKey(n)));
  if (!starts.length) return [];
  const fwd = {};
  for (const e of edges) (fwd[e.source] ||= []).push(e.target);
  const walk = (seed, stop) => {
    const seen = new Set(), stack = [seed];
    while (stack.length) {
      for (const nx of fwd[stack.pop()] || []) {
        if (nx === stop || seen.has(nx)) continue;
        seen.add(nx); stack.push(nx);
      }
    }
    return seen;
  };
  const out = [];
  for (const s of starts) {
    const e = nodes.find((n) => n.type === END && fnKey(n) === fnKey(s));
    if (!e) continue;
    const fromEnd = walk(e.id, s.id);
    const body = [...walk(s.id, e.id)].filter((id) => !fromEnd.has(id) && id !== e.id);
    const boxes = [s, e, ...body.map((id) => nodes.find((n) => n.id === id))
      .filter(Boolean)].map(nodeBox);
    const x0 = Math.min(...boxes.map((b) => b.x)) - PAD;
    const y0 = Math.min(...boxes.map((b) => b.y)) - FRAME_HEAD - PAD;
    const x1 = Math.max(...boxes.map((b) => b.x + b.width)) + PAD;
    const y1 = Math.max(...boxes.map((b) => b.y + b.height)) + PAD;
    out.push({
      id: mint(), type: FRAME, position: { x: x0, y: y0 },
      data: { fn: s.data.fn,
              width: Math.max(FRAME_MIN.width, Math.ceil(x1 - x0 + PIN_W)),
              height: Math.max(FRAME_MIN.height, Math.ceil(y1 - y0)),
              collapsed: false, members: body },
    });
  }
  return out;
}

/** Keep every frame honest, in one pass: Start / End pinned to its edges and
 *  carrying its name, `members` matching what is inside it. Returns the new
 *  node list, or the SAME array when nothing needed to change (so the caller
 *  can skip a render and a save). */
export function syncFrames(nodes) {
  if (!nodes.some((n) => n.type === FRAME)) return nodes;
  const membership = frameMembership(nodes);
  const pins = framePins(nodes);
  const patch = new Map();
  const put = (n, fields) => patch.set(n.id, { ...(patch.get(n.id) || n), ...fields });
  for (const f of nodes) {
    if (f.type !== FRAME) continue;
    // Members are STICKY (frameMembership): only prune ids that are gone,
    // or that another, smaller frame now holds.
    const had = f.data?.members || [];
    const members = had.filter((id) => membership[id] === f.id);
    if (members.length !== had.length) {
      put(f, { data: { ...f.data, members } });
    }
    const { start, end } = pins[f.id] || {};
    const at = pinPositions(f, start, end);
    for (const [pin, pos] of [[start, at.start], [end, at.end]]) {
      if (!pin || !pos) continue;
      // A tolerance, not equality: positions are fractional (a drop lands
      // wherever the pointer was) and an exact compare against a rounded
      // target never settles -- the effect that calls this would loop.
      if (Math.abs(pin.position.x - pos.x) > 0.5 || Math.abs(pin.position.y - pos.y) > 0.5) {
        put(pin, { position: pos });
      }
    }
  }
  if (!patch.size) return nodes;
  return nodes.map((n) => patch.get(n.id) || n);
}

/** Which ids a collapsed frame hides: its pins and every member. */
export function hiddenByFrames(nodes) {
  const out = new Set();
  const frames = nodes.filter((n) => n.type === FRAME && n.data?.collapsed);
  if (!frames.length) return out;
  const membership = frameMembership(nodes);
  const pins = framePins(nodes);
  for (const f of frames) {
    const p = pins[f.id] || {};
    if (p.start) out.add(p.start.id);
    if (p.end) out.add(p.end.id);
    for (const [nid, fid] of Object.entries(membership)) if (fid === f.id) out.add(nid);
  }
  return out;
}

/** May a wire run between these two nodes? A body node only connects to its
 *  own frame's Start, End or other body nodes -- like a local variable in a
 *  function. [ok, reason]. */
export function wireStaysInside(srcId, tgtId, nodes) {
  if (!nodes.some((n) => n.type === FRAME)) return [true, ""];
  const membership = frameMembership(nodes);
  const pins = framePins(nodes);
  const a = frameOf(srcId, nodes, membership, pins);
  const b = frameOf(tgtId, nodes, membership, pins);
  if (a === b) return [true, ""];
  return [false, a && b
    ? "these two nodes are in different functions - a wire cannot cross from one function into another"
    : "a node inside a function only connects to its Start, its End or other nodes in the same frame - wire into the Call instead"];
}

// -- GROUPS ------------------------------------------------------------------
// A Group is a named, coloured box for TAGGING part of a graph. It runs
// nothing; it only carries whatever sits inside it when it is dragged, the
// way a function frame carries its body. Anything whose centre is inside
// moves with it -- nodes, comments, function frames (and so their bodies),
// smaller groups.

export const GROUP = "note.group";
export const GROUP_HEAD = 34;
export const GROUP_MIN = { width: 220, height: 120 };

export function groupRect(g) {
  const d = g.data || {};
  return { x: g.position.x, y: g.position.y,
           width: Math.max(GROUP_MIN.width, Number(d.width) || 520),
           height: Math.max(GROUP_MIN.height, Number(d.height) || 320) };
}

/** Ids of every node a group carries: its saved members, and -- for a
 *  function frame or a smaller group among them -- everything THEY carry.
 *  Sticky, like a frame: dragging a group across nodes picks none of them up. */
export function groupContents(g, nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const pins = framePins(nodes);
  const fm = frameMembership(nodes);
  const out = new Set();
  const stack = [g];
  const seen = new Set([g.id]);
  while (stack.length) {
    const box = stack.pop();
    const ids = box.type === FRAME
      ? [...Object.keys(fm).filter((id) => fm[id] === box.id),
         pins[box.id]?.start?.id, pins[box.id]?.end?.id].filter(Boolean)
      : (box.data?.members || []);
    for (const id of ids) {
      const n = byId.get(id);
      if (!n || seen.has(id)) continue;
      seen.add(id);
      out.add(id);
      if (n.type === FRAME || n.type === GROUP) stack.push(n);
    }
  }
  return [...out];
}

/** Groups saved before membership was stored: take what is inside them now,
 *  once, so an existing group keeps carrying what it visibly holds. */
export function seedGroups(nodes) {
  if (!nodes.some((n) => n.type === GROUP && !Array.isArray(n.data?.members))) return nodes;
  const held = new Set(nodes.filter((g) => g.type === GROUP && Array.isArray(g.data?.members))
    .flatMap((g) => g.data.members));
  return nodes.map((g) => {
    if (g.type !== GROUP || Array.isArray(g.data?.members)) return g;
    const r = groupRect(g);
    const members = nodes.filter((n) => n.id !== g.id && !isPin(n) && !held.has(n.id)
      && (n.type !== GROUP || area(groupRect(n)) < area(r))
      && inside(r, ...centre(n))).map((n) => n.id);
    return { ...g, data: { ...g.data, members } };
  });
}

/** Expand position changes so dragging a group carries what is inside it.
 *  Run BEFORE expandFrameDrag: a frame carried here is then already moving,
 *  and its body is inside the group too, so nothing moves twice. */
export function expandGroupDrag(changes, nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const moving = new Set(changes.filter((c) => c.type === "position" && c.position)
    .map((c) => c.id));
  const extra = [];
  for (const c of changes) {
    if (c.type !== "position" || !c.position) continue;
    const g = byId.get(c.id);
    if (!g || g.type !== GROUP) continue;
    const dx = c.position.x - g.position.x, dy = c.position.y - g.position.y;
    if (!dx && !dy) continue;
    for (const id of groupContents(g, nodes)) {
      if (moving.has(id) || !byId.get(id)) continue;
      moving.add(id);
      const n = byId.get(id);
      extra.push({ type: "position", id, dragging: c.dragging,
                   position: { x: n.position.x + dx, y: n.position.y + dy } });
    }
  }
  return extra.length ? [...changes, ...extra] : changes;
}

/** The rect a group's own members cover, or null when it holds nothing. */
function membersBox(g, nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const boxes = (g.data?.members || []).map((id) => byId.get(id)).filter(Boolean)
    .map((n) => (n.type === FRAME ? frameRect(n) : n.type === GROUP ? groupRect(n) : nodeBox(n)));
  if (!boxes.length) return null;
  const x0 = Math.min(...boxes.map((b) => b.x)), y0 = Math.min(...boxes.map((b) => b.y));
  const x1 = Math.max(...boxes.map((b) => b.x + b.width));
  const y1 = Math.max(...boxes.map((b) => b.y + b.height));
  return { x0, y0, x1, y1 };
}

const GPAD = 20;

/** The smallest a group may be resized to: it always covers its nodes. To
 *  make it smaller, drag nodes out of it first. */
export function groupMinSize(g, nodes) {
  const b = membersBox(g, nodes);
  if (!b) return { ...GROUP_MIN };
  return { width: Math.max(GROUP_MIN.width, Math.ceil(b.x1 - g.position.x + GPAD)),
           height: Math.max(GROUP_MIN.height, Math.ceil(b.y1 - g.position.y + GPAD)) };
}

/** Grow every group so it covers the nodes it holds -- a node dropped near
 *  the edge, or a function frame inside it made bigger, pushes the group's
 *  edge out. Never shrinks a group. Skipped while anything is being dragged,
 *  so a node pulled out of a group leaves it instead of dragging the edge
 *  along. Returns the same array when nothing changed. */
export function fitGroups(nodes) {
  if (!nodes.some((n) => n.type === GROUP)) return nodes;
  if (nodes.some((n) => n.dragging)) return nodes;
  let changed = false;
  // Smallest first, so an inner group has grown before its parent measures it.
  const order = nodes.filter((n) => n.type === GROUP)
    .sort((a, b) => area(groupRect(a)) - area(groupRect(b))).map((n) => n.id);
  let cur = nodes;
  for (const gid of order) {
    const g = cur.find((n) => n.id === gid);
    const b = membersBox(g, cur);
    if (!b) continue;
    const r = groupRect(g);
    const x = Math.min(r.x, Math.floor(b.x0 - GPAD));
    const y = Math.min(r.y, Math.floor(b.y0 - GROUP_HEAD - 8));
    const w = Math.max(r.x + r.width, Math.ceil(b.x1 + GPAD)) - x;
    const h = Math.max(r.y + r.height, Math.ceil(b.y1 + GPAD)) - y;
    if (Math.abs(x - r.x) < 1 && Math.abs(y - r.y) < 1
        && Math.abs(w - r.width) < 1 && Math.abs(h - r.height) < 1) continue;
    changed = true;
    cur = cur.map((n) => (n.id === gid
      ? { ...n, position: { x, y }, data: { ...n.data, width: w, height: h } } : n));
  }
  return changed ? cur : nodes;
}
