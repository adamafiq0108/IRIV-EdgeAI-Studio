# The Pre-process NODE: what the canvas says the camera's picture goes
# through, and what that compiles to. (The frame work itself is
# smoke_preprocess.py, against systems/demo/app/preprocess.py.)
#
# Everything else on the canvas works on DETECTIONS -- objects that already
# exist by the time a rule reads them. This one works on the frames going in,
# which is why it compiles onto the CAMERA rather than into a function, and
# why "which camera" has to be walked back to the stream wired in front of it.
#
# The three things worth protecting: ORDER (blur then sobel is not sobel then
# blur), the zone ids in a mask becoming real polygons, and a graph drawn
# before this node existed compiling to exactly what it did before.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

#: Zone points are stored NORMALISED 0..1 (detector_control._zone_list), which
#: is why the same shape works at any resolution -- nothing is divided at
#: compile, the points are clamped and rounded and that is all.
ZONES = [
    {"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True,
     "camera": "cam_1",
     "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]},
    {"id": "zone_b", "name": "Dock", "kind": "area", "enabled": True,
     "camera": "cam_2", "points": [[0, 0], [0.5, 0], [0.5, 0.5]]},
    {"id": "line_x", "name": "Gate", "kind": "line", "enabled": True,
     "camera": "cam_1", "points": [[0.0, 0.5], [1.0, 0.5]]},
]
CAMERAS = {"cam_1", "cam_2"}

#: The chain the tests use, in an order that is only right one way round.
CHAIN = [{"op": "grayscale"},
         {"op": "blur", "kind": "gaussian", "ksize": 5, "sigma": 1.5},
         {"op": "sobel", "dx": 1, "dy": 0, "ksize": 3}]


def graph(steps=CHAIN, camera="cam_1", preview=False, wired=True, extra=None):
    """Detection -> Pre-process -> Class Filter -> Zone Counter.

    The Class Filter behind it is deliberate: a Pre-process is not the end of
    a chain, it sits in front of the same rules a stream feeds directly.
    """
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {"camera": camera}},
        {"id": "pre", "type": "filter.preprocess", "position": {"x": 1, "y": 0},
         "data": {"steps": list(steps), "preview": preview}},
        {"id": "f", "type": "filter.class", "position": {"x": 2, "y": 0},
         "data": {"classes": ["person"]}},
        {"id": "z", "type": "zone.ref", "position": {"x": 2, "y": 1},
         "data": {"zone": "zone_a"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 3, "y": 0},
         "data": {"fnId": "bay", "name": "Bay"}},
    ]
    edges = [{"id": "e2", "source": "pre", "target": "f"},
             {"id": "e3", "source": "f", "target": "occ"},
             {"id": "e4", "source": "z", "target": "occ"}]
    if wired:
        edges.append({"id": "e1", "source": "s", "target": "pre"})
    if extra:
        nodes.extend(extra[0])
        edges.extend(extra[1])
    return {"nodes": nodes, "edges": edges}


def compile_(g):
    return wf.compile_graph(wf.clean_graph(g), ZONES, cameras=CAMERAS)


def messages(items, nid=None):
    return " | ".join(m["message"] for m in items
                      if nid is None or m["node"] == nid)


def chain(c):
    return c["preprocess"][0]["steps"]


# 1. it is registered where a Class Filter is, and wires like one -----------
spec = wf.REGISTRY["filter.preprocess"]
assert spec["palette"]["cat"] == "filter", spec["palette"]
assert spec["category"] == "filter", \
    "the wiring category is what edge_allowed reasons about"
assert spec["label"] == "Pre-process" and spec["badge"] == "PRE"
assert spec["data"] == {"steps": [], "preview": False}

ok, why = wf.edge_allowed("source.detection", "filter.preprocess")
assert ok, why
for tgt in ("filter.class", "analytics.occupancy"):
    ok, why = wf.edge_allowed("filter.preprocess", tgt)
    assert ok, f"preprocess -> {tgt}: {why}"

# ...and refuses the nonsense pairs: nothing that is already a DETECTION may
# feed it, because the picture is made before any detection exists.
for src in ("filter.class", "zone.ref", "analytics.occupancy", "output.alert"):
    ok, why = wf.edge_allowed(src, "filter.preprocess")
    assert not ok, f"{src} -> preprocess was allowed"

ok, why = wf.edge_allowed("filter.preprocess", "filter.preprocess")
assert not ok and "one Pre-process per camera" in why, why

# A jump still reaches one: after resolve_links it IS the stream that fed it.
ok, why = wf.edge_allowed("common.link_in", "filter.preprocess")
assert ok, why


# 2. the steps reach the app, in the operator's order, for the right camera --
c = compile_(graph())
assert not c["errors"], c["errors"]
pre = c["preprocess"]
assert len(pre) == 1, pre
assert pre[0]["camera"] == "cam_1", pre[0]
assert pre[0]["preview"] is False
assert [s["op"] for s in chain(c)] == ["grayscale", "blur", "sobel"], \
    "the chain compiled out of order - the order IS the meaning of the node"
# Every param the op declares is present, so the app never tests for a key.
assert chain(c)[1] == {"op": "blur", "enabled": True, "kind": "gaussian",
                       "ksize": 5, "sigma": 1.5}, chain(c)[1]
assert chain(c)[2] == {"op": "sobel", "enabled": True, "normalize": False,
                       "dx": 1, "dy": 0, "ksize": 3, "scale": 1.0}, chain(c)[2]

# ...and the same chain written backwards compiles backwards. Two chains that
# differ only in order must not compile to the same thing.
back = compile_(graph(steps=list(reversed(CHAIN))))
assert [s["op"] for s in back["preprocess"][0]["steps"]] == \
    ["sobel", "blur", "grayscale"]

# preview is the operator's, and travels with the chain.
assert compile_(graph(preview=True))["preprocess"][0]["preview"] is True


# 3. out-of-range numbers are brought back in, evens are rounded up ---------
c = compile_(graph(steps=[{"op": "blur", "kind": "nonsense", "ksize": 4,
                           "sigma": 900},
                          {"op": "exposure", "gain": -5}]))
assert not c["errors"], c["errors"]
blur, exposure = chain(c)
assert blur["kind"] == "gaussian", \
    "an unknown blur kind falls back to the default"
assert blur["ksize"] == 5, "a kernel has to have a centre"
assert blur["sigma"] == 25.0, blur
assert exposure["gain"] == 0.05 and exposure["gamma"] == 1.0, exposure

# An op this build cannot do is named, never dropped quietly.
c = compile_(graph(steps=[{"op": "grayscale"}, {"op": "hallucinate"}]))
assert c["errors"] and "hallucinate" in messages(c["errors"]), c["errors"]
assert "preprocess" not in c

# A chain longer than the app will run is CUT to the cap, and says so: the
# steps past it were never going to run, and failing the save over a picture
# setting would take the rest of the canvas down with it.
c = compile_(graph(steps=[{"op": "grayscale"}] * (wf.MAX_PREPROCESS_STEPS + 3)))
assert not c["errors"], c["errors"]
assert str(wf.MAX_PREPROCESS_STEPS) in messages(c["warnings"], "pre"), c["warnings"]
assert len(chain(c)) == wf.MAX_PREPROCESS_STEPS, chain(c)

# A Sobel that differentiates in no direction passes the picture through --
# a warning, because the chain still compiles and still runs.
c = compile_(graph(steps=[{"op": "sobel", "dx": 0, "dy": 0}]))
assert not c["errors"], c["errors"]
assert "no direction" in messages(c["warnings"], "pre"), c["warnings"]


# 4. `enabled` -------------------------------------------------------------
#
# Absent means ON, so nothing already saved changes; false survives compile,
# because switching a step off is how a chain gets tuned without deleting the
# numbers that were dialled into it.
c = compile_(graph(steps=[{"op": "grayscale"},
                          {"op": "blur", "ksize": 5, "enabled": False}]))
assert not c["errors"], c["errors"]
assert [s["enabled"] for s in chain(c)] == [True, False], chain(c)
# ...and an off step keeps its place in the order rather than being dropped.
assert [s["op"] for s in chain(c)] == ["grayscale", "blur"]


# 5. a MASK resolves its zones into normalised polygons ---------------------
c = compile_(graph(steps=[{"op": "mask", "zones": ["zone_a"], "fill": "blur"}]))
assert not c["errors"], c["errors"]
mask = chain(c)[0]
assert mask == {"op": "mask", "enabled": True,
                "polygons": [[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]],
                "invert": False, "fill": "blur", "ksize": 21}, mask
assert "zones" not in mask, "the app is handed points, never zone ids"
# The points went through UNDIVIDED: they are stored normalised already.
assert all(0.0 <= v <= 1.0 for p in mask["polygons"][0] for v in p)

# A zone the system no longer has: named, and the step compiles SWITCHED OFF.
# An empty polygon list would mask everything or nothing depending on
# `invert`, which is a detector that silently stopped seeing.
c = compile_(graph(steps=[{"op": "mask", "zones": ["zone_a", "zone_gone"]}]))
assert not c["errors"], c["errors"]
assert "zone_gone" in messages(c["warnings"], "pre"), c["warnings"]
assert chain(c)[0]["enabled"] is False, chain(c)[0]

# A zone drawn on ANOTHER camera is an error: its shape means nothing here.
c = compile_(graph(steps=[{"op": "mask", "zones": ["zone_b"]}]))
assert c["errors"] and "another camera" in messages(c["errors"]), c["errors"]
assert "preprocess" not in c

# ...and a LINE has no inside to keep.
c = compile_(graph(steps=[{"op": "mask", "zones": ["line_x"]}]))
assert c["errors"] and "no inside" in messages(c["errors"]), c["errors"]

# A mask naming nothing at all says so, and is off rather than total.
c = compile_(graph(steps=[{"op": "mask", "zones": []}]))
assert not c["errors"], c["errors"]
assert "names no zone" in messages(c["warnings"], "pre"), c["warnings"]
assert chain(c)[0]["enabled"] is False


# 6. an EMPTY chain writes nothing ------------------------------------------
c = compile_(graph(steps=[]))
assert not c["errors"], c["errors"]
assert "preprocess" not in c, \
    "an empty chain must reach the app as no chain at all"


# 7. nothing wired in WARNS, and compiles to nothing ------------------------
c = compile_(graph(wired=False))
assert not c["errors"], c["errors"]
assert "wire in the Detection Stream" in messages(c["warnings"], "pre"), \
    c["warnings"]
assert "preprocess" not in c


# 8. two nodes on one stream are refused BY NAME ----------------------------
two = graph(extra=([{"id": "pre2", "type": "filter.preprocess",
                     "position": {"x": 1, "y": 2},
                     "data": {"steps": [{"op": "grayscale"}]}}],
                   [{"id": "e5", "source": "s", "target": "pre2"}]))
c = compile_(two)
msg = messages(c["errors"])
assert c["errors"], "two chains on one camera compiled"
assert "pre" in msg and "pre2" in msg, msg

# ...but two nodes on two DIFFERENT cameras are the normal multi-camera case.
two_cams = graph(extra=(
    [{"id": "s2", "type": "source.detection", "position": {"x": 0, "y": 2},
      "data": {"camera": "cam_2"}},
     {"id": "pre2", "type": "filter.preprocess", "position": {"x": 1, "y": 2},
      "data": {"steps": [{"op": "mask", "zones": ["zone_b"]}]}},
     {"id": "z2", "type": "zone.ref", "position": {"x": 2, "y": 3},
      "data": {"zone": "zone_b"}},
     {"id": "occ2", "type": "analytics.occupancy", "position": {"x": 3, "y": 2},
      "data": {"fnId": "bay2", "name": "Bay 2"}}],
    [{"id": "e5", "source": "s2", "target": "pre2"},
     {"id": "e6", "source": "pre2", "target": "occ2"},
     {"id": "e7", "source": "z2", "target": "occ2"}]))
c = compile_(two_cams)
assert not c["errors"], c["errors"]
assert sorted(p["camera"] for p in c["preprocess"]) == ["cam_1", "cam_2"]
# ...and each camera's mask resolved against ITS OWN zone.
by_cam = {p["camera"]: p for p in c["preprocess"]}
assert by_cam["cam_2"]["steps"][0]["polygons"] == [[[0.0, 0.0], [0.5, 0.0],
                                                    [0.5, 0.5]]]


# 9. a camera that was unplugged under the node ------------------------------
gone = {"nodes": [n for n in graph(camera="cam_9")["nodes"]
                  if n["id"] in ("s", "pre")],
        "edges": [{"id": "e1", "source": "s", "target": "pre"}]}
c = compile_(gone)
assert not c["errors"], "a removed camera must not stop the system compiling"
assert "no longer has" in messages(c["warnings"], "pre"), c["warnings"]
# ...and the chain is still written: the app ignores a camera it does not
# have, and a system that will not compile because somebody unplugged one is
# worse than one that says so.
assert c["preprocess"][0]["camera"] == "cam_9"


# 10. a graph WITHOUT the node compiles exactly as it did before -------------
#
# The whole migration story for a new node type: no rewrite on load, and no
# new key in the artifacts of a canvas that does not use it.
plain = {"nodes": [n for n in graph()["nodes"] if n["id"] != "pre"],
         "edges": [{"id": "e1", "source": "s", "target": "f"},
                   {"id": "e3", "source": "f", "target": "occ"},
                   {"id": "e4", "source": "z", "target": "occ"}]}
c = compile_(plain)
assert not c["errors"], c["errors"]
# (frames_free / waits_for_detection are about EVERY canvas -- which of its
# nodes run with detection off -- not about this node type.)
assert set(c) == {"functions", "outputs", "widgets", "catalog", "tables",
                  "commands", "watches", "errors", "warnings",
                  "frames_free", "waits_for_detection"}, sorted(c)
assert c["functions"], "the rest of the graph still compiles"


# 11. the store merge: one camera, one picture, across canvases -------------
def wrapped(gid, g):
    return {"id": gid, "name": gid, "enabled": True, "graph": g}


store = {"workflows": [wrapped("a", graph()), wrapped("b", graph())]}
c = wf.compile_store(store, ZONES, cameras=CAMERAS)
assert len(c["preprocess"]) == 1, c["preprocess"]
assert any("already pre-processes" in e["message"] for e in c["errors"]), \
    c["errors"]

store = {"workflows": [wrapped("a", graph())]}
c = wf.compile_store(store, ZONES, cameras=CAMERAS)
assert not c["errors"], c["errors"]
assert [s["op"] for s in c["preprocess"][0]["steps"]] == \
    ["grayscale", "blur", "sobel"]

# 12. the chain SURVIVES a camera-list rewrite ------------------------------
#
# It lives on the camera entry, beside `controls` and `measure`, and every
# writer on the Cameras page rewrites that whole list. A key missing from
# either of those two passes is a chain deleted by somebody renaming a camera
# -- which is exactly how a saved brightness and a calibrated floor were lost
# before those two were carried through.
from core.services import detector_control as dc               # noqa: E402

stored = {"cameras": [{"id": "cam_1", "source": "0",
                       "preprocess": [{"op": "grayscale", "enabled": True},
                                      {"op": "blur", "ksize": 5}],
                       "preprocess_preview": True}]}
listed = dc._camera_list(stored)[0]
assert [s["op"] for s in listed["preprocess"]] == ["grayscale", "blur"], listed
assert listed["preprocess_preview"] is True
revalidated = dc._validate_camera(listed, allow_unset=True)
assert revalidated["preprocess"] == listed["preprocess"], revalidated
assert revalidated["preprocess_preview"] is True

# ...and nothing is written onto a camera that has no chain, so a system with
# no Pre-process node reads exactly as it did before the node existed.
plain_cam = dc._validate_camera({"id": "cam_1", "source": "0"},
                                allow_unset=True)
assert "preprocess" not in plain_cam and "preprocess_preview" not in plain_cam

# A mask's POLYGONS are the one nested value a step may carry, and they
# survive the same rewrite -- a mask that loses its points masks nothing.
square = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]
odd = dc._clean_preprocess([{"op": "mask", "polygons": [square, [[0.1, 0.1]]],
                             "fill": "blur", "enabled": False},
                            {"op": "../../etc"}, "grayscale",
                            {"op": "blur", "ksize": 5}])
assert [s["op"] for s in odd] == ["mask", "blur"], odd
assert odd[0]["polygons"] == [square], \
    "a shape with fewer than three points is not a shape"
assert odd[0]["fill"] == "blur" and odd[0]["enabled"] is False

# ...and the whole thing survives the TOML this box writes and reads back.
# A step is a list of TABLES inside a [[cameras]] item, which is the one
# shape the writer had never been asked for before.
import tomllib                                                 # noqa: E402
from core import config as config_mod                          # noqa: E402

text = config_mod._toml_dump({"cameras": [dc._validate_camera(
    {"id": "cam_1", "source": "0", "preprocess_preview": True,
     "preprocess": [{"op": "mask", "enabled": True, "polygons": [square],
                     "invert": False, "fill": "black"},
                    {"op": "blur", "enabled": False, "kind": "box",
                     "ksize": 5, "sigma": 0.0}]}, allow_unset=True)]})
read_back = tomllib.loads(text)["cameras"][0]
assert read_back["preprocess_preview"] is True
assert [s["op"] for s in read_back["preprocess"]] == ["mask", "blur"], text
assert read_back["preprocess"][0]["polygons"] == [square], text
assert read_back["preprocess"][1]["enabled"] is False, text

print("ok")
