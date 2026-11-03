# ONE Counter on the canvas: a Zone wired counts inside it, a Line wired
# counts crossings, nothing wired counts the whole frame.
#
# Tok Aba, 2026-10-13: merge "Counter" and "Line Counter" into one node whose
# behaviour follows its wiring. The merge is a CANVAS change. Both node types
# stay real on disk and in the compiler -- analytics.counter (zone / object)
# and analytics.entry_exit (line) -- and the canvas turns one into the other
# when a line is wired onto the Counter's Line port or taken off it. That is
# what keeps every graph already saved on a box compiling to exactly what it
# compiled to before: nothing about an old node is rewritten on load.
#
# What is pinned here:
#   1. GOLDEN: representative old workflows (a zone counter, an object counter,
#      a line counter with every output wired, a Counter holding a line on its
#      Zone port, a series-wired line counter) compile byte-for-byte as they
#      did before the merge. The golden file was captured BEFORE any code
#      changed (tests/golden_counter_unify.json).
#   2. the three modes compile to the three rules, with the same fn ids
#   3. a line on the Counter's new Line port compiles as a line counter
#      (the server backstop for a graph built outside the canvas)
#   4. a Zone AND a Line wired is refused with a plain message
#   5. the palette offers one Counter; the Line Counter type still loads
#
#   py -3 tests/smoke_counterunify.py
#   py -3 tests/smoke_counterunify.py --write-golden   (only before a change)
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_counter_unify.json")

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label
          + ("" if ok else f"\n      {ascii(detail)[:1500]}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


ZONES = [
    {"id": "bay", "name": "Bay", "kind": "area", "enabled": True,
     "camera": "cam1", "color": "#DEBA00",
     "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]},
    {"id": "door", "name": "Door", "kind": "line", "enabled": True,
     "camera": "cam1", "points": [[0.5, 0.0], [0.5, 1.0]], "facing": "right"},
]
TABLES = {
    "visits": {"key": "visits", "name": "Visits",
               "columns": [{"name": "who", "type": "text"},
                           {"name": "n", "type": "number"}]},
}
CAMERAS = ["cam1", "cam2"]


def wire(src, tgt, th=None, sh=None):
    return {"id": f"e_{src}_{tgt}_{th or ''}_{sh or ''}", "source": src,
            "target": tgt, "sourceHandle": sh, "targetHandle": th}


def pos(nodes):
    for i, n in enumerate(nodes):
        n.setdefault("position", {"x": i * 10, "y": 0})
    return nodes


def head(cam="cam1"):
    return [{"id": "src", "type": "source.detection", "data": {"camera": cam}},
            {"id": "cls", "type": "filter.class", "data": {"classes": ["person"]}}]


def downstream(cnt, port=None, oid=True):
    """What reads a counter on a real canvas: a card, a branch into a
    Trigger that paints the zone, a Math node, and a Data Table row."""
    nodes = [
        {"id": "card", "type": "dashboard.card",
         "data": {"name": "People now"}},
        {"id": "sw", "type": "branch.multi",
         "data": {"fnId": "crowd", "name": "Crowd", "join": "and",
                  "rules": [{"op": ">", "value": 1, "input": "in1"}]}},
        {"id": "trig", "type": "output.trigger",
         "data": {"name": "too many", "alert": True, "draw": True,
                  "box_color": "#FF0000", "zone_color": "#FF00FF"}},
        {"id": "add", "type": "math.arith",
         "data": {"fnId": "plus_one", "name": "Add", "op": "add"}},
        {"id": "one", "type": "variable.number",
         "data": {"fnId": "one", "name": "one", "value": 1}},
        {"id": "ds", "type": "output.dataset",
         "data": {"dataset": "visits", "name": "Visits", "mode": "alert",
                  "sources": {}}},
    ]
    edges = [wire(cnt, "card", sh=port), wire(cnt, "sw", "in1", sh=port),
             wire("sw", "trig", sh="out0"), wire("trig", "ds"),
             wire(cnt, "add", "a", sh=port), wire("one", "add", "b"),
             wire(cnt, "ds", "c_n", sh=port)]
    if oid:
        edges.append(wire(cnt, "ds", "c_who", sh="OID"))
    return nodes, edges


def fixtures():
    """name -> store, every one of them a shape that is saved on a box today."""
    out = {}

    # 1. A Counter on an area zone (pcb_sensata's shape), live count, OID on.
    n = head() + [
        {"id": "z", "type": "zone.ref", "data": {"zone": "bay"}},
        {"id": "cnt", "type": "analytics.counter",
         "data": {"fnId": "counter_ab12", "name": "Counter", "mode": "realtime",
                  "OID": True, "enter_after_s": 0, "leave_after_s": 0}}]
    e = [wire("src", "cls"), wire("cls", "cnt"), wire("z", "cnt", "zone"),
         wire("z", "trig", "zone")]
    dn, de = downstream("cnt")
    out["zone_counter"] = (n + dn, e + de)

    # 2. A Counter on an area zone counting each entrant once, with timing.
    n = head() + [
        {"id": "z", "type": "zone.ref", "data": {"zone": "bay"}},
        {"id": "cnt", "type": "analytics.counter",
         "data": {"fnId": "counter_cd34", "name": "Entered", "mode": "distinct",
                  "OID": False, "enter_after_s": 2, "leave_after_s": 1}}]
    e = [wire("src", "cls"), wire("cls", "cnt"), wire("z", "cnt", "zone")]
    dn, de = downstream("cnt", oid=False)
    out["zone_counter_distinct"] = (n + dn, e + de)

    # 3. A Counter with no zone: the whole frame.
    n = head() + [
        {"id": "cnt", "type": "analytics.counter",
         "data": {"fnId": "counter_ef56", "name": "In view", "mode": "realtime",
                  "OID": True}}]
    e = [wire("src", "cls"), wire("cls", "cnt")]
    dn, de = downstream("cnt")
    out["object_counter"] = (n + dn, e + de)

    # 4. A Line Counter, both directions, live, with totals and OID: every
    #    port wired somewhere (asri_test_4's shape).
    n = head() + [
        {"id": "z", "type": "zone.ref", "data": {"zone": "door"}},
        {"id": "lc", "type": "analytics.entry_exit",
         "data": {"fnId": "line_counter_gh78", "name": "Line counter",
                  "mode": "both", "OID": True, "distinct": False,
                  "momentary": True, "totals": True}},
        {"id": "card2", "type": "dashboard.card", "data": {"name": "Total in"}},
        {"id": "card3", "type": "dashboard.card", "data": {"name": "Crossings"}},
        {"id": "xs", "type": "output.dataset",
         "data": {"dataset": "visits", "name": "Visits", "mode": "crossing",
                  "sources": {}}}]
    e = [wire("src", "cls"), wire("cls", "lc"), wire("z", "lc", "zone"),
         wire("lc", "card2", sh="total_in"), wire("lc", "card3", sh="cross")]
    dn, de = downstream("lc", port="in")
    # The crossing table records its own rows; drop the alert-mode table.
    dn = [x for x in dn if x["id"] != "ds"]
    de = [x for x in de if x["target"] != "ds"]
    de += [wire("lc", "xs", "c_who", sh="OID"), wire("lc", "xs", "c_n", sh="in")]
    out["line_counter_both"] = (n + dn, e + de)

    # 5. A Line Counter, one direction, a running total, each object once.
    n = head() + [
        {"id": "z", "type": "zone.ref", "data": {"zone": "door"}},
        {"id": "lc", "type": "analytics.entry_exit",
         "data": {"fnId": "line_counter_ij90", "name": "Out", "mode": "out",
                  "OID": False, "distinct": True, "momentary": False,
                  "totals": False}}]
    e = [wire("src", "cls"), wire("cls", "lc"), wire("z", "lc", "zone")]
    dn, de = downstream("lc", port="out", oid=False)
    out["line_counter_out"] = (n + dn, e + de)

    # 6. A Line Counter wired the old way, in series: Class Filter into the
    #    Zone into the counter's In. Mode-less (saved before modes existed).
    n = head() + [
        {"id": "z", "type": "zone.ref", "data": {"zone": "door"}},
        {"id": "lc", "type": "analytics.entry_exit",
         "data": {"fnId": "lc_old", "name": "Line counter"}}]
    e = [wire("src", "cls"), wire("cls", "z"), wire("z", "lc")]
    dn, de = downstream("lc", oid=False)
    out["line_counter_series"] = (n + dn, e + de)

    # 7. A Counter holding a LINE on its Zone port: counts what is AT the line
    #    (a thin band). Must stay exactly that, not turn into a line counter.
    n = head() + [
        {"id": "z", "type": "zone.ref", "data": {"zone": "door"}},
        {"id": "cnt", "type": "analytics.counter",
         "data": {"fnId": "counter_band", "name": "At the door",
                  "mode": "realtime"}}]
    e = [wire("src", "cls"), wire("cls", "cnt"), wire("z", "cnt", "zone")]
    dn, de = downstream("cnt", oid=False)
    out["counter_line_on_zone_port"] = (n + dn, e + de)

    # 8. Both kinds side by side on two cameras, as one workflow each.
    stores = {}
    for name, (nodes, edges) in out.items():
        stores[name] = {"workflows": [{
            "id": f"wf_{name}", "name": name, "enabled": True,
            "graph": {"version": wf.SCHEMA_VERSION, "nodes": pos(nodes),
                      "edges": edges}}]}
    return stores


def compile_all():
    got = {}
    for name, store in fixtures().items():
        res = wf.compile_store(json.loads(json.dumps(store)), ZONES,
                               tables=TABLES, cameras=CAMERAS)
        got[name] = json.loads(json.dumps(res, sort_keys=True, default=str))
    return got


if "--write-golden" in sys.argv:
    with io.open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
        json.dump(compile_all(), f, indent=1, sort_keys=True)
        f.write("\n")
    print("golden written:", GOLDEN)
    sys.exit(0)

# -- 1. golden: every old shape compiles exactly as before ----------------------
golden = json.load(io.open(GOLDEN, encoding="utf-8"))
now = compile_all()
for name in sorted(golden):
    want, got = golden[name], now.get(name)
    check(f"old workflow '{name}' compiles with no errors",
          not want["errors"], want["errors"])
    for part in sorted(set(want) | set(got or {})):
        check(f"  ...its {part} are unchanged",
              (got or {}).get(part) == want.get(part),
              {"want": want.get(part), "got": (got or {}).get(part)})


# -- 2. the three modes: one node, the wiring picks the rule --------------------
def counter_graph(place=None, port="zone", data=None, ctype="analytics.counter"):
    """Stream -> person filter -> Counter, with `place` (a zone id) wired onto
    `port`, and a card on the count."""
    nodes = head() + [
        {"id": "cnt", "type": ctype,
         "data": {"fnId": "cnt", "name": "Counter", **(data or {})}},
        {"id": "card", "type": "dashboard.card", "data": {"name": "Count"}}]
    edges = [wire("src", "cls"), wire("cls", "cnt"), wire("cnt", "card")]
    if place:
        nodes.append({"id": "z", "type": "zone.ref", "data": {"zone": place}})
        edges.append(wire("z", "cnt", port))
    return wf.clean_graph({"nodes": pos(nodes), "edges": edges})


def fn_of(res, fid="cnt"):
    return next((f for f in res["functions"] if f["id"] == fid), None)


def compile_g(g):
    return wf.compile_graph(json.loads(json.dumps(g)), ZONES, tables=TABLES)


res = compile_g(counter_graph())
fn = fn_of(res)
check("nothing wired: an OBJECT counter, the whole frame",
      not res["errors"] and fn and fn["type"] == "frame_count"
      and "zone" not in fn["params"], (res["errors"], fn))
res = compile_g(counter_graph("bay"))
fn = fn_of(res)
check("a Zone wired: a ZONE counter, inside it",
      not res["errors"] and fn and fn["type"] == "zone_count"
      and fn["params"].get("zone") == "bay", (res["errors"], fn))
res = compile_g(counter_graph("door", port=wf.COUNTER_LINE_PORT))
fn = fn_of(res)
check("a Line wired on the Line port: a LINE counter, across it",
      not res["errors"] and fn and fn["type"] == "line_count"
      and fn["params"].get("line") == "door", (res["errors"], fn))
check("...publishing under the SAME id, so cards and tables keep their key",
      fn and fn["id"] == "cnt")

# -- 3. the backstop compiles exactly what the canvas saves ---------------------
# The canvas turns the Counter into analytics.entry_exit with the line on the
# `zone` port, keeping the direction it was given. The server, handed the
# un-swapped shape, must compile the very same function.
for way in ("in", "out", "both"):
    canvas = compile_g(counter_graph(
        "door", port="zone", ctype="analytics.entry_exit",
        data={"mode": way, "OID": False, "distinct": False, "momentary": True,
              "totals": False, "count_mode": "realtime"}))
    raw = compile_g(counter_graph(
        "door", port=wf.COUNTER_LINE_PORT,
        data={"mode": "realtime", "line_mode": way, "OID": False}))
    check(f"Counter + line ({way}) compiles like the line counter it becomes",
          not canvas["errors"] and not raw["errors"]
          and fn_of(canvas) == fn_of(raw)
          and canvas["widgets"] == raw["widgets"],
          {"canvas": fn_of(canvas), "raw": fn_of(raw),
           "errs": (canvas["errors"], raw["errors"])})
raw = compile_g(counter_graph("door", port=wf.COUNTER_LINE_PORT))
check("...and with no direction set it takes the line counter's default (in)",
      fn_of(raw) and wf.REGISTRY["analytics.entry_exit"]["data"]["mode"] == "in"
      and fn_of(raw)["params"].get("momentary") is True,
      fn_of(raw))
g0 = counter_graph("door", port=wf.COUNTER_LINE_PORT)
snapshot = json.dumps(g0, sort_keys=True)
wf.lower_counter_lines(g0)
check("the lowering does not touch the graph it was given",
      json.dumps(g0, sort_keys=True) == snapshot)

# -- 4. a Zone AND a Line is refused, plainly -----------------------------------
g = counter_graph("bay")
g["nodes"].append({"id": "ln", "type": "zone.ref", "position": {"x": 99, "y": 9},
                   "data": {"zone": "door"}})
g["edges"].append(wire("ln", "cnt", wf.COUNTER_LINE_PORT))
res = compile_g(g)
msgs = [e["message"] for e in res["errors"] if e["node"] == "cnt"]
check("Zone and Line both wired: refused with the Counter's own words",
      any("Zone OR across a Line" in m for m in msgs), res["errors"])
res = compile_g(counter_graph("bay", port=wf.COUNTER_LINE_PORT))
msgs = [e["message"] for e in res["errors"] if e["node"] == "cnt"]
check("an AREA on the Line port is refused (an area has no crossing)",
      any("needs a line" in m and "Bay" in m for m in msgs), res["errors"])

# -- 5. one Counter in the palette; the line counter still loads ----------------
reg = wf.REGISTRY
offered = [t for t, s in reg.items()
           if not s.get("deprecated") and not s.get("hidden")
           and s.get("label") == "Counter"]
check("the palette offers ONE node called Counter",
      offered == ["analytics.counter"], offered)
check("the Line Counter type is out of the palette but still a real node "
      "(hidden, not deprecated)",
      reg["analytics.entry_exit"].get("hidden") is True
      and not reg["analytics.entry_exit"].get("deprecated")
      and reg["analytics.entry_exit"]["fn_type"] == "line_count")
check("...drawn with the Counter's name and badge",
      reg["analytics.entry_exit"]["label"] == "Counter"
      and reg["analytics.entry_exit"]["badge"] == reg["analytics.counter"]["badge"])
ports = [h["id"] for h in reg["analytics.counter"]["handles"]]
check("the Counter has In, Zone and Line ports", ports == ["in", "zone", "line"],
      ports)
check("...and the line counter's line is on `zone`, where every saved wire is",
      [h["id"] for h in reg["analytics.entry_exit"]["handles"]] == ["in", "zone"])
check("a palette search for 'line counter' finds the Counter",
      "line counter" in reg["analytics.counter"]["description"].lower())
for t in ("analytics.counter", "analytics.entry_exit"):
    ok, why = wf.edge_allowed("zone.ref", t, wf.COUNTER_LINE_PORT)
    check(f"a Zone node may be wired to {t}'s ports", ok, why)

# -- 6. the canvas half is where the swap lives ---------------------------------
page = read("frontend", "src", "pages", "Workflow.jsx")
nodes_src = read("frontend", "src", "workflow", "nodes.jsx")
regjs = read("frontend", "src", "workflow", "registry.js")
check("registry.js swaps both ways and finds the swaps from the wiring",
      all(f"export function {name}(" in regjs
          for name in ("counterMode", "counterToLine", "lineToCounter",
                       "counterSwaps")))
check("the canvas runs the swap on every wiring change",
      "counterSwaps(nodes, before.edges, edges" in page
      and "counterToLine" in page and "lineToCounter" in page)
check("the inspector says which of the three it is",
      'label="What it counts"' in page and "COUNTER_MODE_LABEL[cmode]" in page)
# The face's in/does/out rows moved to Properties (2026-10-14); the job pill
# stays on the face (nodes.jsx counterJob).
check("the node face wears the mode as a subtitle",
      "export function counterJob(" in nodes_src and "COUNTER_MODE_LABEL[zoneWired" in nodes_src
      and "{job}" in nodes_src)
check("a Zone + Line refusal exists at wire time too",
      page.count("Zone OR across a") >= 2)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
