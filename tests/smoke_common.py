# Common group: a Link out/in jump must compile exactly like the wire it
# stands for, and Comment/Debug must never reach the engine.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]

CHAIN = [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay", "name": "Bay"}},
    {"id": "when", "type": "logic.when", "position": {"x": 3, "y": 0},
     "data": {"metric": "count", "mode": "above", "value": 2}},
    {"id": "snap", "type": "output.snapshot", "position": {"x": 6, "y": 0}, "data": {}},
]
WIRED = [{"id": "e1", "source": "s", "target": "z"},
         {"id": "e2", "source": "z", "target": "occ"},
         {"id": "e3", "source": "occ", "target": "when"}]

# 1. drawn directly
direct = wf.compile_graph(
    wf.clean_graph({"nodes": CHAIN,
                    "edges": WIRED + [{"id": "e4", "source": "when",
                                       "target": "snap"}]}), ZONES)
assert not direct["errors"], direct["errors"]

# 2. the same thing via a named jump
jumped = wf.compile_graph(wf.clean_graph({
    "nodes": CHAIN + [
        {"id": "lo", "type": "common.link_out", "position": {"x": 4, "y": 0},
         "data": {"link": "to_alerts"}},
        {"id": "li", "type": "common.link_in", "position": {"x": 5, "y": 0},
         "data": {"link": "to_alerts"}},
        {"id": "note", "type": "note.comment", "position": {"x": 0, "y": 3},
         "data": {"text": "explains things"}},
        {"id": "dbg", "type": "common.debug", "position": {"x": 4, "y": 2},
         "data": {"metric": "count"}},
    ],
    "edges": WIRED + [
        {"id": "e4", "source": "when", "target": "lo"},
        {"id": "e5", "source": "li", "target": "snap"},
        {"id": "e6", "source": "occ", "target": "dbg"},
    ]}), ZONES)
assert not jumped["errors"], jumped["errors"]

print("direct outputs:", [(o["type"], o.get("function")) for o in direct["outputs"]])
print("jumped outputs:", [(o["type"], o.get("function")) for o in jumped["outputs"]])
assert direct["outputs"] == jumped["outputs"], "a jump compiled differently to a wire"
assert direct["functions"] == jumped["functions"]
print("== a named jump compiles exactly like the wire it stands for")

# 3. Comment and Debug produce nothing
assert not any("dbg" in str(o.get("id", "")) for o in jumped["outputs"])
print("== Comment and Debug reach the engine as nothing")

# 4. an unmatched jump carries nothing, and the node left unwired says so --
#    reported against the REAL node, not against a link the operator would
#    have to trace by hand.
half = wf.compile_graph(wf.clean_graph({
    "nodes": CHAIN + [{"id": "lo", "type": "common.link_out",
                       "position": {"x": 4, "y": 0}, "data": {"link": "nowhere"}}],
    "edges": WIRED + [{"id": "e4", "source": "when", "target": "lo"}]}), ZONES)
assert not half["outputs"], "an unmatched jump invented an output"
# The node the jump was meant to reach has nothing wired into it, so it is a
# DRAFT: switched off and dim, reported as a warning against the REAL node
# rather than as an error the operator would have to trace back through a
# link. (An unwired node stopped being an error -- see smoke_switch.py.)
assert not half["errors"], half["errors"]
snap_warn = [w for w in half["warnings"] if w["node"] == "snap"]
assert snap_warn and "switched off" in snap_warn[0]["message"], half["warnings"]
print("== unmatched jump carries nothing; the unwired node says so:",
      snap_warn[0]["message"])

# 4b. a jump with no name at all is a normal half-built state, not a crash
unnamed = wf.compile_graph(wf.clean_graph({
    "nodes": CHAIN[:4] + [{"id": "lo", "type": "common.link_out",
                           "position": {"x": 4, "y": 0}, "data": {"link": ""}}],
    "edges": WIRED + [{"id": "e4", "source": "when", "target": "lo"}]}), ZONES)
assert not unnamed["errors"], unnamed["errors"]
print("== an unnamed jump compiles quietly while it is being built")

# 5. wiring rules
for src, tgt, ok in [("common.link_in", "output.snapshot", True),
                     ("analytics.occupancy", "common.link_out", True),
                     ("analytics.occupancy", "common.debug", True),
                     ("common.debug", "output.snapshot", False),
                     ("analytics.occupancy", "common.link_in", False),
                     ("analytics.occupancy", "note.comment", False)]:
    got, why = wf.edge_allowed(src, tgt)
    assert got is ok, f"{src} -> {tgt}: expected {ok}, got {got} ({why})"
print("== wiring rules hold (comment takes none, debug is a dead end)")

# -- 6. A PROBE MAY WATCH ANYTHING -------------------------------------------
#
# The probe answers "what is coming down this wire", and the wires an operator
# is least sure about are the ones at the END: did that Telegram go, did that
# relay actually close. Those were the wires it could not be put on. Asked in
# pipeline order, a probe is refused after an output ("an output is what
# happens when a rule fires, not something to fire on") and after a dashboard
# card (a card feeds nothing), so the node was allowed exactly where it was
# least needed.
#
# It costs the compiler nothing to allow: every node of category "common" is
# deleted, with its edges, before the graph is validated -- which section 3
# above proves -- so no wire into a probe can change what runs.
# A Comment takes no wires at all and a probe is a dead end, so neither can
# stand for its category here -- and both are asserted on directly, above and
# below, rather than skipped quietly.
ONE_PER_CATEGORY = {}
for _t, _s in wf.REGISTRY.items():
    if _t in ("note.comment", "common.debug"):
        continue
    ONE_PER_CATEGORY.setdefault(_s["category"], _t)
assert len(ONE_PER_CATEGORY) > 5, ONE_PER_CATEGORY
refused = []
for cat, t in sorted(ONE_PER_CATEGORY.items()):
    got, why = wf.edge_allowed(t, "common.debug")
    if not got:
        refused.append(f"{cat} ({t}): {why}")
assert not refused, "a probe cannot be put on: " + "; ".join(refused)
print("== a probe takes a wire from every category:",
      ", ".join(sorted(ONE_PER_CATEGORY)))

# The named ones, spelled out, because these are the wires the node was
# brought back FOR and a category representative could drift off them.
for t in ("output.telegram", "output.email", "output.io_out", "output.rs485",
          "output.modbus_reg", "output.webhook", "output.mqtt",
          "output.dataset", "output.trigger", "dashboard.card",
          "math.setvar", "variable.ref", "logic.when", "storage.query"):
    got, why = wf.edge_allowed(t, "common.debug")
    assert got, f"{t} cannot be probed: {why}"
print("== ...including every output an operator asks 'did that send?' about")

# ...and nothing reads a probe back, from any category: it is a dead end, so
# a probe can never become part of what runs by being wired onward.
for cat, t in sorted(ONE_PER_CATEGORY.items()):
    got, why = wf.edge_allowed("common.debug", t)
    assert not got, f"a probe fed {t} ({cat}) - it must be a dead end"
# Neither end of the probe rule may open a hole in the Comment rule, and a
# probe watching a probe is a loop of nothing.
for src, tgt in [("note.comment", "common.debug"),
                 ("common.debug", "note.comment"),
                 ("common.debug", "common.debug")]:
    got, why = wf.edge_allowed(src, tgt)
    assert not got, f"{src} -> {tgt} was allowed ({why})"
print("== ...and nothing may be wired out of one")

# 7. the probe's own defaults. `metric` is the key typed into the inspector,
#    which was the ONLY way the deprecated node ever read anything; probes
#    saved on canvases in the field carry one, and the node still honours it
#    when nothing is wired in.
assert wf.REGISTRY["common.debug"]["data"] == {"metric": ""}, \
    wf.REGISTRY["common.debug"]["data"]
assert wf.REGISTRY["common.debug"]["category"] == "common", \
    "the probe left the category that is stripped before compile"
print("== the probe's defaults are unchanged; it is still canvas furniture")

# 8. ...and a probe on an OUTPUT still compiles to nothing. Section 3 proved
#    it for a probe on an analytics node; the wire this change added is the
#    one onto an output, and "compiles to nothing" is the promise that makes
#    allowing it safe.
probed = wf.compile_graph(wf.clean_graph({
    "nodes": CHAIN + [
        {"id": "tg", "type": "output.telegram", "position": {"x": 6, "y": 2},
         "data": {"chat_id": "1", "message": "hi",
                  "bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"}},
        {"id": "dbg2", "type": "common.debug", "position": {"x": 7, "y": 2},
         "data": {}},
    ],
    "edges": WIRED + [
        {"id": "e4", "source": "when", "target": "snap"},
        {"id": "e5", "source": "when", "target": "tg"},
        {"id": "e6", "source": "tg", "target": "dbg2"},
    ]}), ZONES)
assert not probed["errors"], probed["errors"]
assert not any("dbg2" in str(o.get("id", "")) for o in probed["outputs"]), \
    "a probe on an output compiled to something"
# The outputs are exactly what the same graph without the probe produces.
plain = wf.compile_graph(wf.clean_graph({
    "nodes": CHAIN + [
        {"id": "tg", "type": "output.telegram", "position": {"x": 6, "y": 2},
         "data": {"chat_id": "1", "message": "hi",
                  "bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"}}],
    "edges": WIRED + [
        {"id": "e4", "source": "when", "target": "snap"},
        {"id": "e5", "source": "when", "target": "tg"}]}), ZONES)
assert probed["outputs"] == plain["outputs"], \
    "probing an output changed what that output compiles to"
assert probed["functions"] == plain["functions"]
# The probe reads the output's own out_<id>_* stats, and the id it has to
# build to do that is the compiler's. Pinned here because the browser mirrors
# the f"wf_{node id}"[:40] form by hand -- it is the one thing that makes the
# whole output readout point at the right document.
tg_doc = [o for o in probed["outputs"] if o.get("type") == "telegram"]
assert tg_doc and tg_doc[0]["id"] == "wf_tg"[:40], \
    f"an output's document id is not wf_<node id>: {tg_doc}"
print("== a probe on a Telegram compiles to nothing, and that Telegram's "
      "document id is", tg_doc[0]["id"])

# -- a comment sits BEHIND the nodes it explains, unless it is told otherwise --
#
# A note is background for the part of the graph it describes. Drawn at the
# same level as the nodes, a big one covered them and swallowed their clicks,
# so it went behind -- and stays behind for every note saved before the choice
# existed, which is what a missing `layer` means.
import io                                                     # noqa: E402

note_data = wf.REGISTRY["note.comment"]["data"]
assert note_data.get("layer") == "back", note_data
editor = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
                 encoding="utf-8").read()
assert 'n.type === "note.comment" && (n.data || {}).layer !== "front"' in editor \
    and "zIndex: -1" in editor, "the canvas does not push a comment behind the nodes"
assert '<option value="front">in front of the nodes</option>' in editor, \
    "the inspector offers no way to bring a note to the front"
print("== a comment sits behind the nodes, or in front when asked")
print("ALL OK")
