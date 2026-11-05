# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
class_filter.py -- named Class Filters: the naming rule, and pausing one from
the dashboard.

WHY THIS EXISTS. A system often runs two detections side by side ("people"
and "chair"), each behind its own Class Filter. The dashboard's "Class
filter" card lists those filters BY NAME and lets someone switch one off, so
the rules after it stop -- held back the way detection-fed rules are when the
AI is off: nothing published live, no fake zeros, cards that say "paused".

Three halves, all here so the 13k-line compiler only calls in:

  * the NAMING RULE -- with more than one Class Filter, each needs a unique
    name. Enforced when the editor SAVES (routes/workflow.py), never by the
    compiler: a graph that compiled yesterday must compile today. Graphs
    nobody named (saved before the rule, templates, the Zones page's mirror,
    the first-visit migration) are named from their classes instead.
  * the PLAN -- per named filter, which functions and outputs it holds back
    (everything it reaches by wire, plus whatever reads those keys), written
    to config.local.toml [[class_filters.nodes]] by every applied compile.
  * the SWITCH -- [class_filters] paused, written by the card's route and
    read by the running app on its config poll (app/run.py class_pause). In
    config.local.toml, so it survives restarts and updates; no restart.

ONE STATE, TWO SWITCHES (Tok Aba, 2026-10-14). The Class Filter node has an
On / Off switch of its own, and it IS the card's run / pause: Off means
paused -- the rules after the filter are held back and publish nothing live,
exactly what the card has always done. The node's `running` field (absent =
On) is the source of truth:

  * the card's switch writes the node (set_node_running, stamped like a
    Builder edit so a canvas tab opened earlier cannot undo it) AND the
    paused list, so the running app changes at once on its config poll, as
    before;
  * every applied compile rebuilds the paused list FROM the nodes (apply),
    so a canvas save cannot leave the two apart;
  * a system paused before the switch existed has the pause adopted onto
    the node when its canvas is opened (adopt_paused), once.
"""

#: The node field. Absent is On, as every node saved before it was.
RUN_FIELD = "running"


def node_running(data):
    """True / False when the node says, None when it never did."""
    value = (data or {}).get(RUN_FIELD)
    return value if isinstance(value, bool) else None


def naming_message(count):
    """The rule in the words both the save and the panel use (frontend
    workflow/registry.js classFilterNaming says the same sentence)."""
    return (f"Name each Class Filter: this workflow has {count}, and the "
            f"dashboard lists them by name")


def duplicate_message(name):
    return (f'Another Class Filter is already called "{name}" - give each '
            f"one its own name")


def _live(nodes):
    """The Class Filters that run: a switched-off one runs nothing and the
    card does not list it, so it neither counts nor needs a name."""
    return [n for n in nodes or () if isinstance(n, dict)
            and n.get("type") == "filter.class"
            and not (n.get("data") or {}).get("disabled")]


def naming_problems(nodes):
    """[(node id, message)] for the Class Filters that break the rule.

    With MORE THAN ONE, every one needs a name and the names are unique (case
    and outer spaces ignored): two unnamed rows, or two called "people",
    cannot be told apart by the person switching one off. One alone may stay
    unnamed."""
    live = _live(nodes)
    if len(live) < 2:
        return []
    out, seen = [], set()
    for n in live:
        name = str((n.get("data") or {}).get("name") or "").strip()
        if not name:
            out.append((n["id"], naming_message(len(live))))
        elif name.lower() in seen:
            out.append((n["id"], duplicate_message(name)))
        seen.add(name.lower())
    return out


def naming_errors(graph=None, store=None):
    """The rule as compile errors: one per node, plus one general line so
    the save says it in words and not only by reddening nodes off screen."""
    graphs = [graph] if graph is not None else [
        w.get("graph") or {} for w in (store or {}).get("workflows") or ()
        if isinstance(w, dict) and w.get("enabled", True)]
    out = []
    for g in graphs:
        problems = naming_problems((g or {}).get("nodes"))
        out += [{"node": nid, "message": msg} for nid, msg in problems]
        if problems:
            out.append({"node": "", "message": problems[0][1]})
    return out


def _unique(base, taken):
    """`base`, else `base 2`, `base 3`... -- the first not in `taken`."""
    base = str(base or "").strip()[:50] or "Class Filter"
    name, i = base, 2
    while name.lower() in taken:
        name, i = f"{base} {i}", i + 1
    taken.add(name.lower())
    return name


def name_filters(graph, only=None):
    """Name every unnamed Class Filter when the graph runs more than one, IN
    PLACE; returns the graph. Named after what each keeps ("person, car" --
    the words its node already shows), numbered when two keep the same.
    `only` narrows it to node ids the caller drew itself: a filter the
    operator drew next to them is the operator's to name.

    For graphs nobody named on this canvas -- saved before the rule, a
    template landing, the Zones page's mirror, the first-visit migration --
    so they keep applying and the card can list them."""
    live = _live((graph or {}).get("nodes"))
    if len(live) < 2:
        return graph
    taken = {str((n.get("data") or {}).get("name") or "").strip().lower()
             for n in live} - {""}
    for n in live:
        d = n.setdefault("data", {})
        if str(d.get("name") or "").strip() or (
                only is not None and n.get("id") not in only):
            continue
        classes = [str(c) for c in (d.get("classes") or []) if str(c).strip()]
        d["name"] = _unique(", ".join(classes[:3]), taken)
    return graph


def migrate_store(store):
    """A loaded store with its graphs named (name_filters). Idempotent: a
    graph that meets the rule is returned untouched."""
    for w in (store or {}).get("workflows") or ():
        if isinstance(w, dict) and isinstance(w.get("graph"), dict):
            name_filters(w["graph"])
    return store


def kept_name(previous_node):
    """{"name": ...} when a node being redrawn had a name, else {}."""
    name = str(((previous_node or {}).get("data") or {}).get("name") or "")
    return {"name": name} if name.strip() else {}


def plans(graph, canvas_nodes, functions, outputs, fn_meta, cond_meta,
          switch_meta):
    """Per NAMED Class Filter of one canvas: {node, name, base, out_base} --
    the function and output ids it reaches structurally. Unnamed filters are
    left out: the card cannot list them, so nothing can pause them.

    On the RESOLVED graph, so a Link is its wire; a filter inside a function
    copy ("<call>__<id>") is grouped under the drawn node it came from, the
    one the operator named. Same reading as logic_plan's "whatever a
    Detection Stream reaches waits", started at the filter instead, and
    without its AI half (a rule TYPE that reads detections is held back only
    if this filter actually feeds it)."""
    from core.services import workflow as wf                   # noqa: PLC0415
    drawn = {n["id"]: n for n in _live(canvas_nodes)}
    succ = {}
    for e in graph.get("edges") or ():
        succ.setdefault(e.get("source"), []).append(e.get("target"))
    reach = {}
    for n in graph.get("nodes") or ():
        if n.get("type") != "filter.class":
            continue
        cid = n["id"] if n["id"] in drawn else n["id"].split("__")[-1]
        if cid not in drawn:
            continue
        seen, todo = reach.setdefault(cid, set()), [n["id"]]
        seen.add(n["id"])
        while todo:
            for nxt in succ.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
    fids = {f["id"] for f in functions}
    metas = list(fn_meta.items()) + list(cond_meta.items()) \
        + [(nid, doc) for (nid, _h), doc in switch_meta.items()]
    out_node = {f"wf_{n['id']}"[:40]: n["id"] for n in graph.get("nodes") or ()}
    out = []
    for cid, fed in reach.items():
        name = str((drawn[cid].get("data") or {}).get("name") or "").strip()
        if not name:
            continue
        base = {doc["id"] for nid, doc in metas if doc and nid in fed}
        # A Variable written by a Set Variable publishes under its own fnId.
        base |= {wf._slug((n.get("data") or {}).get("fnId") or "")
                 for n in graph.get("nodes") or () if n["id"] in fed} & fids
        out.append({"node": cid, "name": name[:60], "base": sorted(base),
                    "out_base": sorted(o.get("id") for o in outputs
                                       if out_node.get(o.get("id")) in fed),
                    # On / Off, only when the node says (absent = On, and a
                    # graph saved before the switch compiles as it did).
                    **({RUN_FIELD: node_running(drawn[cid].get("data"))}
                       if node_running(drawn[cid].get("data")) is not None else {})})
    return out


def decide(entries, functions, outputs):
    """[{node, name, functions, outputs}]: each plan's structural hold-back,
    widened to what READS it across the whole system -- a function reading
    a held-back key is held back (to a fixpoint), and so is an output fired
    by or reading one. Unlike logic_decide a key nobody publishes holds
    nothing back: this is one filter's branch, not the camera being gone."""
    from core.services import workflow as wf                   # noqa: PLC0415
    fids = {f["id"] for f in functions}
    out = []
    for p in entries or ():
        needs = set(p["base"])
        for _ in range(len(functions) + 1):
            grew = False
            for doc in functions:
                if doc["id"] not in needs and any(
                        wf.key_owner(k, fids) in needs
                        for k in wf.function_keys(doc)):
                    needs.add(doc["id"])
                    grew = True
            if not grew:
                break
        held = {o.get("id") for o in outputs
                if o.get("id") in p["out_base"]
                or (o.get("function") and o["function"] in needs)
                or any(wf.key_owner(k, fids) in needs
                       for k in wf.output_keys(o))}
        out.append({"node": p["node"], "name": p["name"],
                    "functions": sorted(needs), "outputs": sorted(held),
                    **({RUN_FIELD: p[RUN_FIELD]} if RUN_FIELD in p else {})})
    return out


#: Ceilings: filters a system lists, ids each may hold back.
MAX_FILTERS = 50
MAX_IDS = 400


def table(systems_dir, slug):
    """{"nodes": [{node, name, functions, outputs}], "paused": [node ids]}.
    Empty lists when there is no table: nothing is paused by default."""
    from core.services import detector_control as dc           # noqa: PLC0415
    raw = dc._merged(dc._pkg_dir(systems_dir, slug)).get("class_filters")
    raw = raw if isinstance(raw, dict) else {}
    return {"nodes": [n for n in (raw.get("nodes") or [])
                      if isinstance(n, dict) and n.get("node")],
            "paused": [str(p) for p in (raw.get("paused") or [])
                       if isinstance(p, str)]}


def apply(systems_dir, slug, compiled):
    """Write the compile's plan wholesale, like [logic]. The operator's
    paused list is KEPT, pruned to filters that still exist with a name -- a
    filter deleted or left unnamed must not come back paused one day. Never
    raises: the rest of the save has already landed."""
    from core.services import detector_control as dc           # noqa: PLC0415
    ids = lambda xs: [str(x)[:60] for x in (xs or [])          # noqa: E731
                      if isinstance(x, str) and x.strip()][:MAX_IDS]
    nodes = [{"node": str(e["node"])[:60],
              "name": str(e.get("name") or "").strip()[:60],
              "functions": ids(e.get("functions")),
              "outputs": ids(e.get("outputs"))}
             for e in (compiled.get("class_filters") or [])[:MAX_FILTERS]
             if isinstance(e, dict) and str(e.get("node") or "").strip()]
    try:
        if not nodes and "class_filters" not in dc._merged(
                dc._pkg_dir(systems_dir, slug)):
            return        # nothing named, nothing stored: no write at all
    except (ValueError, TypeError, OSError):
        pass
    try:
        with dc.edit_local(dc._pkg_dir(systems_dir, slug)) as local:
            had = local.get("class_filters")
            had = had if isinstance(had, dict) else {}
            known = {n["node"] for n in nodes}
            # The paused list FROM THE NODES: Off on the node is paused, On is
            # not. A node that never said (saved before the switch) keeps
            # whatever the card set, as it always did.
            said = {str(e["node"])[:60]: e[RUN_FIELD]
                    for e in (compiled.get("class_filters") or [])
                    if isinstance(e, dict) and isinstance(e.get(RUN_FIELD), bool)}
            paused = [p for p in (had.get("paused") or [])
                      if p in known and p not in said]
            paused += [n for n in sorted(said) if n in known and said[n] is False]
            if nodes or paused:
                local["class_filters"] = {"paused": paused, "nodes": nodes}
            else:
                # No named filter: no table, so the config is exactly what
                # it was before this feature existed.
                local.pop("class_filters", None)
    except (ValueError, TypeError, OSError) as exc:
        print(f"[WF] {slug}: Class Filter list not written: {exc}")


def set_running(systems_dir, slug, node, running):
    """Run (True) or pause (False) one named Class Filter; returns the new
    paused list. A filter the system does not list -- unnamed, unsaved, or
    since deleted -- is refused in words: a tick that silently changed
    nothing would look like a fault in the app."""
    from core.services import detector_control as dc           # noqa: PLC0415
    node = str(node or "").strip()
    with dc.edit_local(dc._pkg_dir(systems_dir, slug)) as local:
        raw = local.get("class_filters")
        raw = dict(raw) if isinstance(raw, dict) else {}
        if not any(isinstance(n, dict) and n.get("node") == node
                   for n in raw.get("nodes") or ()):
            raise ValueError("that Class Filter is not on this system's "
                             "workflow any more - reload the dashboard")
        paused = [p for p in (raw.get("paused") or [])
                  if isinstance(p, str) and p != node]
        raw["paused"] = paused + ([] if running else [node])
        local["class_filters"] = raw
    return raw["paused"]


def adopt_paused(graph, systems_dir, slug):
    """Write a pause the card set BEFORE the node had a switch onto the node
    (running: False), so the canvas shows Off where the dashboard says
    paused. Only on nodes that never said either way. Returns whether the
    graph changed (the caller saves it, once)."""
    try:
        paused = set(table(systems_dir, slug)["paused"])
    except Exception:                                         # noqa: BLE001
        return False
    changed = False
    for n in (graph or {}).get("nodes") or ():
        if not isinstance(n, dict) or n.get("type") != "filter.class" \
                or n.get("id") not in paused:
            continue
        data = n.setdefault("data", {})
        if node_running(data) is None:
            data[RUN_FIELD] = False
            changed = True
    return changed


def set_node_running(store, node, running):
    """The card's switch, written onto the Class Filter NODE of the saved
    workflow: only its `running` field, stamped like a Builder edit
    (workflow.keep_builder_edits) so a canvas tab opened earlier cannot put
    the old value back, while a change made on the canvas still wins.
    Returns whether the node changed; ValueError when there is no such
    Class Filter."""
    from core.services import workflow as wf                   # noqa: PLC0415
    for w in (store or {}).get("workflows") or ():
        for n in ((w or {}).get("graph") or {}).get("nodes") or ():
            if not isinstance(n, dict) or n.get("id") != node \
                    or n.get("type") != "filter.class":
                continue
            data = n.setdefault("data", {})
            now = node_running(data)
            if (now is None and running) or now == running:
                return False
            rev = wf._edit_rev(data.get(wf.BUILDER_EDIT_KEY)) + 1
            wf._stamp_builder_edit(data, RUN_FIELD, data.get(RUN_FIELD), rev)
            data[RUN_FIELD] = bool(running)
            return True
    raise ValueError("that Class Filter is not on this system's workflow any more "
                     "- reload the dashboard")
