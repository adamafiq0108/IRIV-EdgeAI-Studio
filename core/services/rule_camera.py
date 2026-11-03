# -*- coding: utf-8 -*-
"""Which camera an If/Else, a Formula or a Variable runs on.

Those compile to `expr` functions, and an `expr` was given no camera on
purpose (workflow.py compile_graph: it reads numbers, not frames). But the
app runs every function on ONE camera's engine, and "no camera" means the
first one (systems/demo/app/run.py _Pipe.reload, mine()). On a system with
two cameras and a workflow per camera (product owner, 2026-10-12) camera
2's If/Else therefore ran on camera 1's engine, so everything that follows
the rule's camera followed camera 1:

  * the event it raised said camera 1 (run.py stamps the firing pipe),
    and so did every message, webhook and Data Table row built from it;
  * a Snapshot / Record clip left on "follow the rule" was resolved to the
    rule's camera at compile -- blank -- and the app fell back to the first
    camera's frame (run.py _capture_pipe);
  * a Trigger's "who" (its OID port, once per object) is looked up on the
    engine that runs the rule (app/functions.py _collect_live_oids), and
    camera 2's counter was not on camera 1's engine, so it found nobody;
  * box and zone colours (fixed separately, trigger_paint.stamp_camera).

So an `expr` takes the camera of what it READS: the keys in its
expression, through the functions that publish them (another `expr`
recursively) and the zones a zone_/line_ key names. Exactly one concrete
camera -> that camera. Anything else keeps the old behaviour (the first
camera): keys from two cameras, an "every camera" rule, a blank stream (which
means the first camera anyway), a cam_*/objects_*/violations_* key (system
wide or deliberately cross-camera), or a key no function in this workflow
publishes (another canvas's: its camera is not known here).
"""

#: Keys that are not ONE camera's by construction: the merged system-wide
#: aggregates, and the per-camera copies a formula names on purpose.
_SHARED_PREFIXES = ("cam_", "objects_", "violations_")

_MIXED = object()       # "not one camera": leave the rule where it was


def _zone_camera(key, zone_by_id):
    """The camera of the zone a zone_<id>_<metric> / line_<id>_<metric> key
    names (longest id first, ids may hold underscores), or _MIXED."""
    body = key.split("_", 1)[1] if "_" in key else ""
    best = None
    for zid in zone_by_id:
        if body.startswith(f"{zid}_") and (best is None or len(zid) > len(best)):
            best = zid
    if best is None:
        return _MIXED
    return str((zone_by_id[best] or {}).get("camera") or "").strip()


def stamp(functions, zone_by_id, frame_types, function_keys, key_owner):
    """Set params.camera on every camera-less `expr` in `functions` (in
    place) whose inputs all come from one concrete camera. The helpers are
    workflow.py's own (function_keys, key_owner, FRAME_FN_TYPES), passed in
    so this module does not import the compiler it is called from."""
    by_id = {f.get("id"): f for f in functions or () if f.get("id")}
    fids = list(by_id)
    memo = {}

    def cams(fid, stack):
        """The cameras function `fid` reads from: a set ("" = the first
        camera, "*" = every camera), or _MIXED."""
        if fid in memo:
            return memo[fid]
        if fid in stack:
            return _MIXED                       # a loop: no single answer
        doc = by_id[fid]
        params = doc.get("params") or {}
        own = str(params.get("camera") or "").strip()
        if doc.get("type") != "expr" or own:
            if own:
                out = {own}
            elif doc.get("type") in frame_types:
                out = {""}                      # a frame rule on the first camera
            else:
                out = set()                     # a clock, an API value, a constant
            memo[fid] = out
            return out
        out = set()
        for key in function_keys(doc):
            if key.startswith(_SHARED_PREFIXES) or key == "compliance":
                out = _MIXED
            elif key.startswith(("zone_", "line_")):
                got = _zone_camera(key, zone_by_id)
                out = _MIXED if got is _MIXED else out | {got}
            else:
                owner = key_owner(key, fids)
                got = _MIXED if owner is None else cams(owner, stack | {fid})
                out = _MIXED if got is _MIXED else out | got
            if out is _MIXED:
                break
        memo[fid] = out
        return out

    for fid, doc in by_id.items():
        if doc.get("type") != "expr" or (doc.get("params") or {}).get("camera"):
            continue
        got = cams(fid, frozenset())
        if got is not _MIXED and len(got) == 1:
            cam = next(iter(got))
            if cam and cam != "*":
                doc.setdefault("params", {})["camera"] = cam[:32]
