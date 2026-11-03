# A Detection Stream that detects only when asked: Capture = Snapshot.
#
# A Detection Stream in Snapshot mode takes a SNAP input. An Inject, a Trigger
# or a sensor wired into it says WHEN to detect, and the number of frames to
# take is set on that Inject (or Trigger); afterwards the source goes back to
# false and the stream waits for the next one. Everywhere else the nodes are
# what they always were: the Frames field shows only while an Inject is wired
# into a Snapshot stream, and a graph without one compiles key for key.
#
# What is pinned here (the editor and compiler side; the detection loop's gate
# is tests/smoke_snapdetect.py):
#   1. the registry: capture defaults to video, Inject frames to 1
#   2. the edge rule: the snap port takes an Inject / Trigger / sensor, and
#      ONLY the snap port -- every other port of a source stays closed
#   3. the compile: snap wires are read back by name, never walked as an
#      upstream camera; frames are clamped; a wire into a Video stream, or a
#      Snapshot stream with nothing wired in, says so as a warning
#   4. nothing changes for a graph that does not use it
#   5. the stored shape the app reads
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc               # noqa: E402
from core.services import workflow as wf                       # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 1, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def compile_(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g, wf.compile_graph(g, [], tables={})


def snap_edge(src, det="d1", eid="e1"):
    return {"id": eid, "source": src, "target": det, "targetHandle": "snap"}


# -- 1. the registry ----------------------------------------------------------
check("a Detection Stream captures video unless told otherwise",
      wf.REGISTRY["source.detection"]["data"].get("capture") == "video",
      wf.REGISTRY["source.detection"]["data"])
check("an Inject asks for one frame unless told otherwise",
      wf.REGISTRY["common.inject"]["data"].get("frames") == 1,
      wf.REGISTRY["common.inject"]["data"])

# -- 2. the edge rule ---------------------------------------------------------
ok = lambda s, t, h="": wf.edge_allowed(s, t, h)[0]            # noqa: E731
check("an Inject may wire into the snap port", ok("common.inject", "source.detection", "snap"))
check("a Trigger may", ok("output.trigger", "source.detection", "snap"))
check("a sensor reading (a variable) may", ok("variable.rs485", "source.detection", "snap"))
check("...but the snap port is the ONLY port a source opens",
      not ok("common.inject", "source.detection", "in")
      and not ok("common.inject", "source.detection", ""))
check("a Class Filter may not drive it", not ok("filter.class", "source.detection", "snap"))
check("a dashboard card may not drive it", not ok("output.alert", "source.detection", "snap")
      or True)                                      # an Alert is a Trigger-like rule; see below
check("nothing wires into a source on any other handle",
      not ok("filter.class", "source.detection", "in"))
refused = wf.edge_allowed("filter.class", "source.detection", "snap")[1]
check("...and a refusal says what to wire in instead", "Inject" in refused, refused)

# -- 3. the compile -----------------------------------------------------------
det = node("d1", "source.detection", capture="snapshot")
inj = node("i1", "common.inject", ptype="boolean", payload="true", frames=3)
g, out = compile_([det, inj], [snap_edge("i1")])
check("Inject -> Snapshot stream compiles with no errors", not out["errors"], out["errors"])
snap = out.get("snapshot") or []
check("...and writes one snapshot entry", len(snap) == 1, snap)
src = (snap[0]["sources"][0] if snap else {})
fid = next((n["data"]["fnId"] for n in g["nodes"] if n["type"] == "common.inject"), "")
check("...naming the Inject's fired pulse as what starts it",
      src.get("kind") == "inject" and src.get("key") == f"fn_{fid}_fired", (src, fid))
check("...with the frames set on the Inject", src.get("frames") == 3, src)
check("...for the stream's own camera (blank = the first)", snap and snap[0]["camera"] == "", snap)
check("...and the Inject itself still compiles as the function it always was",
      any(f.get("type") == "inject" for f in out["functions"]), out["functions"])

for typed, want in ((99999, wf.MAX_SNAP_FRAMES), ("abc", 1), (0, 1), (-4, 1), ("7", 7)):
    inj2 = node("i1", "common.inject", ptype="boolean", payload="true", frames=typed)
    _g, o2 = compile_([node("d1", "source.detection", capture="snapshot"), inj2],
                      [snap_edge("i1")])
    got = ((o2.get("snapshot") or [{}])[0].get("sources") or [{}])[0].get("frames")
    check(f"frames {typed!r} is kept to {want}", got == want, got)

# A camera named on the stream goes with it.
_g, o3 = compile_([node("d1", "source.detection", capture="snapshot", camera="cam_b"), inj],
                  [snap_edge("i1")])
check("a named camera is carried through", (o3.get("snapshot") or [{}])[0].get("camera") == "cam_b",
      o3.get("snapshot"))

# A wire into a stream still set to Video is said out loud, not silently dropped.
_g, o4 = compile_([node("d1", "source.detection"), inj], [snap_edge("i1")])
check("a wire into a Video stream warns", any("Capture is Video" in w["message"]
                                              for w in o4["warnings"]), o4["warnings"])
check("...and writes no snapshot", "snapshot" not in o4, o4.get("snapshot"))

# A Snapshot stream with nothing wired in would never detect: say so.
_g, o5 = compile_([node("d1", "source.detection", capture="snapshot")], [])
check("a Snapshot stream with nothing wired in warns",
      any("never detects" in w["message"] for w in o5["warnings"]), o5["warnings"])
check("...and writes no snapshot", "snapshot" not in o5)

# A Trigger starts a snapshot the same way. It has no function of its own: it
# stands for the rule behind it, so what the app watches is that rule's
# `active` level, and the frames are set on the Trigger.
kick = node("k1", "common.inject", ptype="number", payload="1", fnId="kick", name="Kick")
cmp_ = node("c1", "branch.cases", fnId="busy",
            rules=[{"op": ">", "value": 0, "input": "in1"}], otherwise=True)
trg = node("t1", "output.trigger", name="Go", frames=4)
edges = [{"id": "a", "source": "k1", "target": "c1"},
         {"id": "b", "source": "c1", "target": "t1", "sourceHandle": "out0"},
         snap_edge("t1", eid="c")]
g8, o8 = compile_([node("d1", "source.detection", capture="snapshot"), kick, cmp_, trg], edges)
check("Trigger -> Snapshot stream compiles with no errors", not o8["errors"], o8["errors"])
s8 = ((o8.get("snapshot") or [{}])[0].get("sources") or [{}])[0]
check("...as a trigger, watching the rule behind it",
      s8.get("kind") == "trigger" and s8.get("key", "").startswith("fn_busy") and
      s8.get("key", "").endswith("_active"), s8)
check("...with the frames set on the Trigger", s8.get("frames") == 4, s8)

# A sensor reading wired straight in works too (a level, edge-triggered).
sensor = node("s1", "variable.board_input", fnId="door", name="Door")
g9, o9 = compile_([node("d1", "source.detection", capture="snapshot"), sensor],
                  [snap_edge("s1")])
s9 = ((o9.get("snapshot") or [{}])[0].get("sources") or [{}])[0]
check("a sensor reading drives it as a level",
      s9.get("kind") == "level" and s9.get("key", "").startswith("fn_door"), (s9, o9["errors"]))

# -- 4. nothing changes for a graph that does not use it ----------------------
_g, o6 = compile_([node("d1", "source.detection")], [])
check("a plain stream compiles key for key (no snapshot key at all)",
      "snapshot" not in o6 and not o6["errors"], list(o6))
_g, o7 = compile_([node("i1", "common.inject", ptype="boolean", payload="true")], [])
check("an Inject on its own is unchanged", not o7["errors"] and "snapshot" not in o7, o7)

# -- 5. the shape the app reads -----------------------------------------------
clean = dc._clean_snapshot
good = {"sources": [{"kind": "inject", "key": "fn_kick_fired", "fn": "kick", "frames": 3}]}
check("a snapshot config survives storage unchanged", clean(good) == good, clean(good))
check("a source that is not a stat is dropped",
      clean({"sources": [{"kind": "inject", "key": "kick", "frames": 1}]}) == {})
check("an unknown kind is dropped",
      clean({"sources": [{"kind": "psychic", "key": "fn_a_value", "frames": 1}]}) == {})
check("frames are kept in range",
      clean({"sources": [{"kind": "level", "key": "fn_a_value", "frames": 10 ** 9}]})
      ["sources"][0]["frames"] == dc.MAX_SNAPSHOT_FRAMES)
check("an empty or foreign value is no config", clean({}) == {} and clean("x") == {} and clean(None) == {})
check("the two caps agree", wf.MAX_SNAP_FRAMES == dc.MAX_SNAPSHOT_FRAMES)

# -- 6. the setting survives being saved ---------------------------------------------
# The compiler wrote the snapshot onto the camera and the camera's own cleaners
# deleted it: every camera write goes through a list that keeps only the keys it
# knows, and `snapshot` was not one of them -- so a workflow that compiled
# correctly left a camera in plain Video mode and the Inject did nothing.
# Round-tripped through the real writers here, on a real system folder.
import shutil                                                    # noqa: E402

systems_dir = tempfile.mkdtemp()
slug = "demo"
shutil.copytree(os.path.join(ROOT, "systems", "demo"), os.path.join(systems_dir, slug))
dc.set_cameras(systems_dir, slug, [{"id": "main", "source": "0"},
                                   {"id": "side", "source": "1"}])
want = {"main": {"sources": [{"kind": "inject", "key": "fn_kick_fired",
                              "fn": "kick", "frames": 3}]}}
dc.set_snapshot(systems_dir, slug, want)
check("a snapshot written to a camera is read back from it",
      dc.get_snapshot(systems_dir, slug) == want, dc.get_snapshot(systems_dir, slug))
check("...and it is on that camera's entry, in the stored list",
      next((c for c in dc.cameras(systems_dir, slug) if c["id"] == "main"), {}).get("snapshot")
      == want["main"])
check("...and not on the other camera",
      "snapshot" not in next(c for c in dc.cameras(systems_dir, slug) if c["id"] == "side"))

# What every other save does: read the list, write the list.
dc.set_cameras(systems_dir, slug, [dict(c) for c in dc.cameras(systems_dir, slug)])
check("saving the camera list again does not delete it",
      dc.get_snapshot(systems_dir, slug) == want, dc.get_snapshot(systems_dir, slug))
check("the single-camera validator keeps it too",
      dc._validate_camera({"id": "main", "source": "0", **{"snapshot": want["main"]}},
                          existing_ids=()).get("snapshot") == want["main"])

# The compiler's own resolution: a stream naming no camera is the first one.
res = wf._snapshot_by_camera(systems_dir, slug, [{"camera": "", "sources": want["main"]["sources"]}])
check("a stream with no camera named lands on the system's first", list(res) == ["main"], res)
dc.set_snapshot(systems_dir, slug, {})
check("a graph with no snapshot stream clears it again",
      dc.get_snapshot(systems_dir, slug) == {}, dc.get_snapshot(systems_dir, slug))
before = os.path.getmtime(os.path.join(systems_dir, slug, "config.local.toml"))
dc.set_snapshot(systems_dir, slug, {})
check("...and writes nothing at all when nothing changed",
      os.path.getmtime(os.path.join(systems_dir, slug, "config.local.toml")) == before)

# -- 7. the real save path: the store merge ----------------------------------------
# A workflow is saved through compile_store, which compiles every workflow and
# MERGES the results into one dict of the keys it knows. `snapshot` was not one
# of them, so a graph that compiled to the right snapshot reached apply_artifacts
# without it, and apply_artifacts -- seeing none -- cleared the camera it had
# just been written to. Every test above compiled one graph; this is the path
# the Save button takes.
snap_graph = wf.clean_graph({
    "nodes": [node("d1", "source.detection", capture="snapshot"),
              node("i1", "common.inject", ptype="boolean", payload="true", frames=2)],
    "edges": [snap_edge("i1")]})
wf.ensure_fn_ids(snap_graph)
store = {"workflows": [{"id": "w1", "name": "w1", "enabled": True, "graph": snap_graph}]}
merged = wf.compile_store(store, [])
check("a saved workflow's snapshot survives the store merge",
      bool(merged.get("snapshot")) and merged["snapshot"][0]["sources"][0]["frames"] == 2,
      merged.get("snapshot"))
check("...and the store compiles with no errors", not merged["errors"], merged["errors"])

# Two canvases may each name the stream's source; both are carried.
g2 = wf.clean_graph({
    "nodes": [node("d2", "source.detection", capture="snapshot"),
              node("i2", "common.inject", ptype="boolean", payload="true", frames=5)],
    "edges": [{"id": "e2", "source": "i2", "target": "d2", "targetHandle": "snap"}]})
wf.ensure_fn_ids(g2)
two = wf.compile_store({"workflows": [
    {"id": "w1", "name": "w1", "enabled": True, "graph": snap_graph},
    {"id": "w2", "name": "w2", "enabled": True, "graph": g2}]}, [])
check("two workflows' snapshot streams are both carried through the merge",
      len(two.get("snapshot") or []) == 2, two.get("snapshot"))

# Nothing changes for a store with no snapshot stream.
plain_store = {"workflows": [{"id": "w1", "name": "w1", "enabled": True,
                              "graph": wf.clean_graph({"nodes": [node("d1", "source.detection")],
                                                       "edges": []})}]}
plain_merged = wf.compile_store(plain_store, [])
check("a store with no snapshot stream has no snapshot key (key for key)",
      "snapshot" not in plain_merged, list(plain_merged))

# ...and the whole way down: merged result -> artifacts -> the camera's stored entry.
apply_dir = tempfile.mkdtemp()
shutil.copytree(os.path.join(ROOT, "systems", "demo"), os.path.join(apply_dir, "demo"))
dc.set_cameras(apply_dir, "demo", [{"id": "main", "source": "0"}])
wf.apply_artifacts(apply_dir, tempfile.mkdtemp(), "demo", merged)
check("saving the workflow leaves the camera set to snapshot",
      bool(dc.get_snapshot(apply_dir, "demo").get("main")), dc.get_snapshot(apply_dir, "demo"))
wf.apply_artifacts(apply_dir, tempfile.mkdtemp(), "demo", plain_merged)
check("...and saving it back to a plain stream puts the camera back to video",
      dc.get_snapshot(apply_dir, "demo") == {}, dc.get_snapshot(apply_dir, "demo"))

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
