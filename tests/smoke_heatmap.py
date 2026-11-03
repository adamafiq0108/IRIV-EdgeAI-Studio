# WHERE things happen, which no counter can answer.
#
# A count says a zone was busy; a heatmap says which corner of it was, and
# that is the reading somebody rearranges a floor from. The map is published as
# one stat per cell (fn_<id>_c<row>_<col>) -- the same shape an object watch's
# per-class keys have -- so it needs no new transport, and the dashboard widget
# picks the family up off ONE key by its prefix.
#
# The whole path is checked here: the node compiles to a heatmap function with
# its grid, the map is offered to the dashboard as a map (not as a tile showing
# the busiest cell), and the widget reads the cells back into a grid.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service              # noqa: E402
from core.services import functions as functions_service          # noqa: E402
from core.services import workflow as wf                          # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def wire(src, tgt, handle=None):
    return {"id": f"e_{src}_{tgt}", "source": src, "target": tgt,
            "sourceHandle": None, "targetHandle": handle}


def compile_(nodes, edges=()):
    return wf.compile_graph(
        wf.clean_graph({"nodes": nodes, "edges": list(edges)}), ZONES)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# -- the node compiles to a map --------------------------------------------
GRAPH = [node("src", "source.detection"),
         node("f1", "filter.class", classes=["person"]),
         node("h", "analytics.heatmap", fnId="h", name="Floor",
              cols=16, rows=10, interval_s=2, hours=4)]
out = compile_(GRAPH, [wire("src", "f1"), wire("f1", "h")])
check("a wired Heatmap compiles", not out["errors"], str(out["errors"]))
fn = next((f for f in out["functions"] if f["id"] == "h"), None)
check("...as a heatmap function", fn and fn["type"] == "heatmap", str(fn))
if fn:
    p = fn["params"]
    check("...carrying the grid it was drawn with",
          (p.get("cols"), p.get("rows")) == (16, 10), str(p))
    check("...how often one object may add to it, and how fast it forgets",
          (p.get("interval_s"), p.get("hours")) == (2, 4), str(p))
    # Without this the map counts every class the camera sees, which is not
    # what a Class Filter upstream says.
    check("...and the classes the filter upstream picked",
          p.get("classes") == ["person"], str(p))
    check("no zone wired means the whole view", "zone" not in p, str(p))

# A zone is OPTIONAL and read off the WIRING, like every other zone-optional
# node -- one wired in maps that area only.
out = compile_(GRAPH + [node("z", "zone.ref", zone="bay")],
               [wire("src", "f1"), wire("f1", "h"), wire("z", "h", "zone")])
fn = next((f for f in out["functions"] if f["id"] == "h"), None)
check("a Zone wired in scopes the map to it",
      fn and fn["params"].get("zone") == "bay", str(fn))

# -- the engine agrees the type exists -------------------------------------
check("the engine publishes the map's two readings",
      functions_service.OUTPUTS["heatmap"] == ("hot", "spots"))
check("...and validates a heatmap function",
      "heatmap" in functions_service.TYPES)
clean = functions_service.validate_function(
    {"id": "h", "name": "Floor", "type": "heatmap",
     "params": {"cols": 999, "rows": 1, "interval_s": -5, "hours": 9999}})
p = clean["params"]
check("an impossible grid is clamped, not refused",
      (p["cols"], p["rows"]) == (32, 2), str(p))
check("...and so are its clocks",
      (p["interval_s"], p["hours"]) == (0.0, 168.0), str(p))

# -- the dashboard offers it as a MAP --------------------------------------
entries = [c for c in out["catalog"] if c.get("node") == "h"]
check("the Heatmap is offered to the dashboard",
      any(c["kind"] == "heatmap" for c in entries), str(entries))
check("...bound to the function, not to one cell",
      any(c["kind"] == "heatmap" and c["key"] == "fn_h_hot" for c in entries),
      str(entries))
# The two numbers ABOUT the map stay available as ordinary tiles: "the busiest
# patch" is a perfectly good card on its own.
check("...and its readings are still offered as tiles",
      any(c["kind"] != "heatmap" and c["key"].endswith("_hot") for c in entries),
      str(entries))

# -- the widget reads the family off one key -------------------------------
check("heatmap is a widget kind", "heatmap" in builder_service.KINDS)
widget = builder_service.validate([
    {"id": "w1", "kind": "heatmap", "source": "stat", "key": "fn_h_hot",
     "title": "Floor"}])[0]
STATS = {"fn_h_c0_0": 1, "fn_h_c0_1": 5, "fn_h_c1_0": 0, "fn_h_c1_1": 3,
         "fn_h_hot": 5, "fn_h_spots": 3,
         # A DIFFERENT function's cells must not leak into this map.
         "fn_other_c0_0": 99}
data = builder_service.compute([widget], [], STATS)[0]["data"]
check("the grid is read back, cell for cell",
      data["grid"] == [[1.0, 5.0], [0.0, 3.0]], str(data))
check("...its shape read off the keys", (data["rows"], data["cols"]) == (2, 2),
      str(data))
check("...scaled against its own busiest patch", data["max"] == 5.0, str(data))
check("...and another function's cells stay out of it",
      99 not in [v for row in data["grid"] for v in row], str(data))
check("the map's own readings ride along",
      (data["value"], data["spots"]) == (5.0, 3.0), str(data))

# Bound by hand from the key picker, an operator picks a CELL -- that is what
# the picker lists. It has to land on the same map rather than on one cell.
by_cell = builder_service.validate([
    {"id": "w2", "kind": "heatmap", "source": "stat", "key": "fn_h_c1_1"}])[0]
check("a cell key binds the whole map",
      builder_service.compute([by_cell], [], STATS)[0]["data"]["grid"]
      == [[1.0, 5.0], [0.0, 3.0]])

# A map nothing has published yet is empty, not broken.
empty = builder_service.compute([widget], [], {})[0]["data"]
check("an empty map says so instead of failing",
      empty["grid"] == [] and empty["max"] == 0, str(empty))

# -- and the frontend can draw it ------------------------------------------
check("Widget.jsx renders a heatmap",
      'kind === "heatmap"' in read("frontend", "src", "components", "widgets",
                                   "Widget.jsx"))
palette = read("frontend", "src", "components", "widgets", "builderShared.jsx")
check("the builder palette offers one", 'kind: "heatmap"' in palette)
check("...and the kind picker lists it", '"gauge", "heatmap"' in palette)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
