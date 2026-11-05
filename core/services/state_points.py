# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""The ON and OFF points of a Detection Stream and a Class Filter.

Tok Aba (2026-10-09): "detection stream just got option logic for on only,
then i want to add logic when it off then what will happen". Both nodes have
a STATE an operator can switch -- the AI detection switch for a Detection
Stream, the dashboard's run/pause for a Class Filter -- and until now that
state was only shown (the AI dot, the Class filter card). These points make
it logic: a node wired to ON acts when the state is (or becomes) on, one
wired to OFF when it is (or becomes) off. Once at start-up for the state it
finds, then once per change, never once per frame.

HOW. Both nodes have exactly two outputs, "on" and "off" (Tok Aba, after
v1.0.31: the detections are the node's own business). ON carries the
detections wherever they can go -- into a Class Filter, a Zone, a counter --
exactly as the old unnamed Detections output did (migrate() moves those
wires onto ON; carries_detections() reads them back). Every other wire from
ON, and every wire from OFF, carries the STATE: before anything is validated
the compiler calls split(), which turns each into a wire from a hidden
`state.watch` node -- one per point -- whose function (type `state_watch`) is
a CLOCK exactly like an Inject: `value` is 1 while the state matches the
point, `fired` is 1 for the one tick it starts matching. So every consumer an
Inject already feeds (Trigger, MQTT Publish, Modbus Write, Telegram, Email,
Debug, a Formula...) works with no new rule, and the wire no longer starts at
a Detection Stream -- which is what keeps the OFF path running while the AI
is off: nothing behind it is "fed by detection" (workflow.frames_fed), and
nothing behind a paused filter's OFF point is held back by the pause
(class_filter.plans), because the hidden node is not on the filter's path.

A point's function is minted when something is wired to it OR when any node
of the graph names its value (fn_st_<node>_on_value): that 1/0 is what the
logic nodes' value pickers offer ("Detection Stream is ON"), the second half
of the ask ("implement into the logic control also"). The canvas lists both
values of every Detection Stream and Class Filter it draws (frontend
registry.js statePointKeys), so a picker can offer one before it exists; a
graph that neither wires nor names a point compiles key for key as before.

The running app (app/functions.py _update_state_watch) reads the state from
what run.py tells it: the AI switch as applied, and [class_filters] paused.
A switched-off (disabled) node is OFF for good.

ANY NODE AFTER A POINT (Tok Aba, same day: "for detection stream off, please
make it allow to connect to all nodes, it just run only when AI Detection is
off", then "same to the class filter nodes"). A point is a GATE as well as a
clock: whatever it reaches runs only while its state matches -- after the
Detection Stream's OFF only while the AI is off, after a Class Filter's ON
only while that filter runs. plans() finds what each point reaches, the same
reading as class_filter.plans; compile_store widens it to whatever reads
those (class_filter.decide); apply() writes it as [state_points] gates, and
the app sits the gated rules out and drops the gated outputs (app/run.py
point_gates) exactly as it does for a paused Class Filter. The point's own
function is never gated: it has to see the state come back to fire.

A state wire into a node that cannot take a clock (refuse) is kept on the
canvas and taken out of the compile: a Line Counter or a Class Filter fed
only by an OFF connection has no detections to read, so it is switched off
with "needs AI detection" said on it, rather than the wire being refused.
(From ON the same wire carries the detections, so it never lands here.)
"""

import json

#: The node types that carry the two points, and what their state is.
SOURCES = {"source.detection": "ai", "filter.class": "class_filter"}
#: The two handle ids, as the canvas draws them.
HANDLES = ("on", "off")
#: The hidden node type split() draws (workflow.REGISTRY "state.watch").
NODE = "state.watch"
#: Most consumers a point remembers in its params (see params()).
MAX_FEEDS = 50


def _slug(value):
    """workflow._slug, repeated here so this module imports nothing from the
    compiler (it is imported BY it)."""
    return "".join(c if c.isalnum() else "_"
                   for c in str(value or "").strip().lower()).strip("_")[:40]


def fn_id(node_id, want):
    """The function id of one point: `st_<node>_<on|off>`. Derived from the
    node id alone, so it is stable across renames and the canvas can work it
    out too (frontend nodes.jsx statePointFn, same recipe). The node part is
    cut to 30 so the suffix always survives _slug's 40."""
    return f"st_{_slug(node_id)[:30]}_{want}"


def drawn_id(node_id):
    """The node the operator drew: a copy inside a Function Call is
    "<call>__<id>", and the dashboard pauses the drawn filter (the same
    reading as class_filter.plans)."""
    return str(node_id).split("__")[-1]


def label(node):
    """What the point's value is called in a picker: the filter's name (else
    its classes), or the stream's model and camera."""
    d = node.get("data") or {}
    if node.get("type") == "filter.class":
        name = str(d.get("name") or "").strip()
        classes = [str(c) for c in (d.get("classes") or []) if str(c).strip()]
        what = name or ", ".join(classes[:3])
        return f"Class Filter {what}".strip()[:60]
    bits = [str(d.get(k) or "").strip() for k in ("model", "camera")]
    bits = [b for b in bits if b]
    return ("Detection Stream" + (f" ({', '.join(bits)})" if bits else ""))[:60]


#: What the old "Detections" output's wires carried as their handle: none
#: (the node had one unnamed output), and the name it would have had.
DETECTION_HANDLES = ("", "detections")
#: Wired from ON, still the state point: a probe shows the 1/0, never the
#: detections it cannot display.
STATE_ONLY = ("common.debug",)


def migrate(graph):
    """Move every wire on the old Detections output of a Detection Stream or
    Class Filter onto ON, IN PLACE; returns how many moved. Tok Aba
    (2026-10-09, after v1.0.31): the two nodes have exactly two outputs, ON
    and OFF, and ON carries the detections (split() reads it back). A wire
    that duplicates one already on ON is dropped rather than drawn twice."""
    by_id = {n.get("id"): n for n in graph.get("nodes") or ()}
    have = {(e.get("source"), e.get("target"),
             str(e.get("targetHandle") or ""))
            for e in graph.get("edges") or ()
            if str(e.get("sourceHandle") or "") == "on"}
    moved, kept = 0, []
    for e in graph.get("edges") or ():
        src = by_id.get(e.get("source")) or {}
        if src.get("type") in SOURCES                 and str(e.get("sourceHandle") or "") in DETECTION_HANDLES:
            moved += 1
            key = (e.get("source"), e.get("target"),
                   str(e.get("targetHandle") or ""))
            if key in have:
                continue
            have.add(key)
            e["sourceHandle"] = "on"
        kept.append(e)
    if moved:
        graph["edges"] = kept
    return moved


def carries_detections(edge, src_type, tgt_type, edge_allowed):
    """Is this wire from a Detection Stream or Class Filter its DETECTIONS
    rather than its state? The old unnamed output always is; ON is when it
    lands on a node the detections could feed (edge_allowed from the node
    itself), which is what a wire on the old output did, so a migrated graph
    compiles exactly as before. OFF never is."""
    hd = str(edge.get("sourceHandle") or "")
    if hd in DETECTION_HANDLES:
        return True
    if hd != "on" or tgt_type in STATE_ONLY or edge_allowed is None:
        return False
    return edge_allowed(src_type, tgt_type,
                        str(edge.get("targetHandle") or ""))[0]


def split(graph, edge_allowed=None):
    """`graph` with every ON/OFF wire that carries the STATE leaving a hidden
    state.watch node instead (shape in the module doc), and every ON wire
    that carries the detections back on the node itself (carries_detections).
    A new dict; the input is untouched. Run on the RESOLVED graph (Functions
    and Links already spliced in)."""
    nodes = list(graph.get("nodes") or [])
    by_id = {n.get("id"): n for n in nodes}
    edges = []
    for e in graph.get("edges") or ():
        src = by_id.get(e.get("source")) or {}
        tgt = by_id.get(e.get("target")) or {}
        if src.get("type") in SOURCES and carries_detections(
                e, src["type"], tgt.get("type"), edge_allowed):
            e = {**e, "sourceHandle": None}
        edges.append(e)
    wired = {(e.get("source"), str(e.get("sourceHandle") or ""))
             for e in edges}
    # Every node's settings as one text: a picked key lives in a Formula's
    # expression, a Variable's key, a Compare's operand...
    named = json.dumps([n.get("data") for n in nodes], default=str)
    made, new_edges = [], []
    for n in nodes:
        watch = SOURCES.get(n.get("type"))
        if not watch:
            continue
        for want in HANDLES:
            if (n["id"], want) not in wired                     and f"fn_{fn_id(n['id'], want)}_" not in named:
                continue
            d = n.get("data") or {}
            made.append({
                "id": f"{n['id']}#{want}", "type": NODE,
                "position": n.get("position") or {"x": 0, "y": 0},
                "data": {"fnId": fn_id(n["id"], want),
                         "name": f"{label(n)} is {want.upper()}",
                         "watch": watch, "want": want,
                         "filter": drawn_id(n["id"]),
                         "origin": n["id"],
                         "_filter_name": str(d.get("name") or "").strip(),
                         # A switched-off node runs nothing: it is OFF.
                         "fixed_off": bool(d.get("disabled"))}})
    for e in edges:
        hd = str(e.get("sourceHandle") or "")
        src = by_id.get(e.get("source"))
        if hd in HANDLES and src is not None and src.get("type") in SOURCES:
            e = {**e, "source": f"{e['source']}#{hd}", "sourceHandle": None}
        new_edges.append(e)
    if not made:
        return {**graph, "edges": edges}
    return {**graph, "nodes": nodes + made, "edges": new_edges}


def refuse(graph, edge_allowed, registry, standalone_types):
    """(graph, gate_only, off): the wires from a point into a node that
    cannot take one (edge_allowed says no) taken out of `graph`, and for each
    node left with nothing else wired in, the "switched off" warning it
    wears -- it is then off, like a switched-off node, and so is what it
    alone fed. `gate_only` keeps those wires for plans(): the point still
    gates what is behind them where something else does feed it."""
    by_id = {n.get("id"): n for n in graph.get("nodes") or ()}
    kept, gate_only = [], []
    for e in graph.get("edges") or ():
        src, tgt = by_id.get(e.get("source")), by_id.get(e.get("target"))
        if src is not None and tgt is not None and src.get("type") == NODE:
            ok, why = edge_allowed(NODE, tgt["type"],
                                   str(e.get("targetHandle") or ""))
            if not ok:
                gate_only.append({**e, "_why": why})
                continue
        kept.append(e)
    if not gate_only:
        return graph, [], {}
    fed = {e.get("target") for e in kept}
    off = {}
    for e in gate_only:
        tid = e["target"]
        if tid in fed or tid in off:
            continue
        spec = registry.get(by_id[tid]["type"]) or {}
        # A node that reads the camera's objects -- a filter, a zone, a rule
        # that is not one of the stand-alone kinds -- says the one thing the
        # operator needs to hear; anything else says why it cannot take it.
        detects = (spec.get("category") in ("source", "filter", "zone")
                   or (spec.get("category") == "analytics"
                       and spec.get("fn_type") not in standalone_types))
        off[tid] = (f"{spec.get('label') or 'this node'} needs AI "
                    "detection - the OFF connection carries no detected "
                    "objects, so it is switched off until an ON "
                    "connection (the detections) is wired in"
                    if detects else
                    f"{e['_why']} - switched off until something it can "
                    "read is wired in")
    return {**graph, "edges": kept}, gate_only, off


def _what(data):
    """The words of one point's gate, for the canvas ("runs when ...")."""
    d = data or {}
    side = "off" if d.get("want") == "off" else "on"
    if d.get("watch") == "class_filter":
        name = str(d.get("_filter_name") or "").strip()
        return f"{name or 'its Class Filter'} is {side}"
    return f"AI detection is {side}"


def plans(graph, gate_only, functions, outputs, fn_meta, cond_meta,
          switch_meta):
    """Per point that reaches anything: {node, name, base, out_base, nodes,
    gate} -- the function and output ids it reaches by wire (refused wires
    included), the reached resolved node ids (for the canvas), and the gate
    the app checks. The points' own functions are left out: they run
    whatever the state. Same reading as class_filter.plans, and
    class_filter.decide widens it."""
    from core.services import workflow as wf                   # noqa: PLC0415
    succ = {}
    for e in list(graph.get("edges") or ()) + list(gate_only or ()):
        succ.setdefault(e.get("source"), []).append(e.get("target"))
    own = {f["id"] for f in functions if f.get("type") == "state_watch"}
    fids = {f["id"] for f in functions}
    metas = list(fn_meta.items()) + list(cond_meta.items()) \
        + [(nid, doc) for (nid, _h), doc in switch_meta.items()]
    out_node = {f"wf_{n['id']}"[:40]: n["id"] for n in graph.get("nodes") or ()}
    out = []
    for p in graph.get("nodes") or ():
        if p.get("type") != NODE:
            continue
        seen, todo = set(), [p["id"]]
        while todo:
            for nxt in succ.get(todo.pop(), ()):
                if nxt not in seen and nxt != p["id"]:
                    seen.add(nxt)
                    todo.append(nxt)
        if not seen:
            continue
        base = {doc["id"] for nid, doc in metas if doc and nid in seen}
        base |= {wf._slug((n.get("data") or {}).get("fnId") or "")
                 for n in graph.get("nodes") or () if n["id"] in seen} & fids
        d = p.get("data") or {}
        gate = {k: v for k, v in params(d, ()).items() if k != "feeds"}
        out.append({"node": p["id"], "name": _what(d),
                    "base": sorted(base - own),
                    "out_base": sorted(o.get("id") for o in outputs
                                       if out_node.get(o.get("id")) in seen),
                    "nodes": sorted(seen), "gate": gate})
    return out


#: Ceilings, as for the Class Filter table: gates a system lists, ids each
#: may hold back.
MAX_GATES = 100
MAX_IDS = 400


def apply(systems_dir, slug, compiled):
    """Write the compile's gates wholesale as [state_points] gates in
    config.local.toml (read by app/run.py point_gates on its config poll).
    No gate, no table. Never raises: the rest of the save has landed."""
    from core.services import detector_control as dc           # noqa: PLC0415
    ids = lambda xs: [str(x)[:60] for x in (xs or [])          # noqa: E731
                      if isinstance(x, str) and x.strip()][:MAX_IDS]
    gates = []
    for g in (compiled.get("state_gates") or [])[:MAX_GATES]:
        if not isinstance(g, dict) or not (g.get("functions")
                                           or g.get("outputs")):
            continue
        gates.append({**{k: v for k, v in (g.get("gate") or {}).items()
                         if k in ("watch", "want", "filter", "fixed_off")},
                      "functions": ids(g.get("functions")),
                      "outputs": ids(g.get("outputs"))})
    try:
        pkg = dc._pkg_dir(systems_dir, slug)
        if not gates and "state_points" not in dc._merged(pkg):
            return        # nothing gated, nothing stored: no write at all
        with dc.edit_local(pkg) as local:
            if gates:
                local["state_points"] = {"gates": gates}
            else:
                local.pop("state_points", None)
    except (ValueError, TypeError, OSError) as exc:
        print(f"[WF] {slug}: ON/OFF connection gates not written: {exc}")


def params(data, feeds):
    """The state_watch function's params, from the hidden node's data.

    `feeds` are the nodes wired to the point: kept so the app treats a new
    wire as a new start (it fires once for the state it finds, as at
    start-up), exactly as an Inject re-arms when its own settings change."""
    d = data or {}
    p = {"watch": d.get("watch") if d.get("watch") in SOURCES.values()
         else "ai",
         "want": "off" if d.get("want") == "off" else "on"}
    if p["watch"] == "class_filter":
        p["filter"] = str(d.get("filter") or "")[:60]
    if d.get("fixed_off"):
        p["fixed_off"] = True
    ids = sorted({str(f) for f in feeds or () if f})[:MAX_FEEDS]
    if ids:
        p["feeds"] = ids
    return p
