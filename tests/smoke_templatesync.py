# The Built-in templates folder IS the publish switch.
#
# The folder and the catalogue used to be two unrelated facts: a system could
# sit under "Built-in templates" while the format of the same name shipped
# EMPTY -- a label and a class list, no workflow, no dashboard -- so an admin
# creating "People Counting" started from scratch and the folder read as a
# lie. That is exactly what the box in front of the user had: counting was
# draft=true, workflows=NO, widgets=0.
#
# Now: dropping a system into the folder snapshots the whole build and
# publishes it as a built-in format; dragging it out drafts it (hidden from
# admins, content kept); dropping it in again republishes the CURRENT build.
# And Blank Canvas -- the one deliberate way to start from nothing -- can
# never be deleted or drafted, because a picker without it has no empty start.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import provision                           # noqa: E402
from core.services import system_groups as groups             # noqa: E402
from core.services import templates as templates_service      # noqa: E402

CFG = tempfile.mkdtemp()

# -- Blank Canvas is load-bearing ----------------------------------------------
# The code ships NO formats any more: the folder's snapshots in
# config/formats.toml are the whole list. The blank canvas is one of them --
# the developer box's "Blank Canvas" system, slug blank_canvas_template_editor,
# publishes as `blank_canvas` (format_key drops the authoring suffix) -- and
# the protection follows that key.
assert provision.TEMPLATES == {}, "the code still ships formats"
assert provision.PROTECTED_FORMAT == "blank_canvas"
assert provision.format_key("blank_canvas_template_editor") == "blank_canvas"
assert provision.format_key("counting_template_editor") == "counting"
assert provision.format_key("Object-Detection") == "object_detection"
for attempt, kwargs in (("delete", {}), ("hide", {"hide_shipped": True})):
    try:
        provision.delete_format("blank_canvas", CFG, **kwargs)
        raise AssertionError(f"Blank Canvas was {attempt}d")
    except ValueError as exc:
        assert "from-scratch" in str(exc), exc
try:
    provision.set_format_draft("blank_canvas", True, CFG)
    raise AssertionError("Blank Canvas was drafted")
except ValueError as exc:
    print("== Blank Canvas cannot be deleted, hidden or drafted:", exc)
# ...and deleting the SYSTEM it was published from keeps the template: only
# the workbench link goes. Any other template goes with its system.
provision.save_format("blank_canvas", {"label": "Blank Canvas", "hint": "",
                                       "widgets": [], "draft": False}, CFG)
provision.save_format("gone_soon", {"label": "Gone", "hint": "",
                                    "widgets": [], "draft": False}, CFG)
assert provision.forget_format("blank_canvas", CFG) is False
assert "blank_canvas" in provision.builtin_formats(CFG)
assert provision.forget_format("gone_soon", CFG) is True
assert "gone_soon" not in provision.builtin_formats(CFG)
print("== deleting the authoring system removes its template, never the blank one")
# The default is the blank canvas where published, else the first published,
# else nothing -- never a key the create would refuse.
assert provision.default_template(CFG) == "blank_canvas"
assert provision.default_template(tempfile.mkdtemp()) == ""
# A rename of the authoring system renames the template.
assert provision.set_format_label("blank_canvas", "Empty Start", CFG) is True
assert provision.builtin_formats(CFG)["blank_canvas"]["label"] == "Empty Start"
assert provision.set_format_label("nope", "x", CFG) is False
print("== default follows what is published; a rename follows the system")

# -- the folder is the publish switch ------------------------------------------
# The wiring is in the route (it needs app state for the registry), so the
# contract is pinned in source: both directions, and the failure path.
SRC = io.open(os.path.join(ROOT, "core", "routes", "hub.py"),
              encoding="utf-8").read()
route = SRC[SRC.index('name="hub.set_group"'):]
# The last route in the file: slice to the next decorator or the end.
cut = route.find("@router.", 10)
route = route if cut < 0 else route[:cut]
assert "BUILTIN_GROUP" in route, "the route does not know the special folder"
assert "snapshot_system" in route and "promote_template" in route, \
    "dropping a system into the folder publishes nothing"
assert "set_format_draft" in route, "dragging it out does not unpublish"
# Publish failure must UNDO the move: a system sitting in the folder
# unpublished is exactly the lie the sync exists to end.
assert "groups_service.assign(slug, was" in route, \
    "a failed publish leaves the system filed under a folder it is not in"
print("== in the folder = published, out = drafted, failure undoes the move")

assert groups.BUILTIN_GROUP == "builtin"
assert any(g["id"] == "builtin" for g in groups.groups(CFG))
print("== the folder ships in the default set, id 'builtin'")

# -- a published format's workflow reaches a customer install -------------------
# apply_format_workflow moved OUT of core/dev: a customer box has no dev
# package, and starting from a format is what every admin does there.
assert hasattr(templates_service, "apply_format_workflow"), \
    "apply_format_workflow is not in core/services"
create = SRC[SRC.index('name="hub.create_system"'):]
create = create[:create.index("@router.", 10)]
assert "templates_service.apply_format_workflow" in create, \
    "create still imports the dev-only path"
assert "core.dev" not in create, create[create.index("core.dev"):][:120]
print("== the create route applies a format's workflow from core/services")

# ...and a format with no workflow applies nothing, quietly (every shipped
# format today).
out = templates_service.apply_format_workflow(
    tempfile.mkdtemp(), CFG, "nosuch", {"label": "x"}, key="x")
assert out == [], out
print("== a format without a workflow applies nothing")

# -- what actually travels ------------------------------------------------------
# This is the bug the user hit twice: the format published but the system
# created from it still looked empty. Two causes, both pinned:
#
# * The dashboard ARRANGEMENT did not travel -- promote extracted only the
#   main page's widget list, and nothing read even that back on create.
# * One unbound Zone node was a compile ERROR, which stopped apply_artifacts
#   for the whole canvas -- so the zone-free chains (frame counters, datasets,
#   their cards) never applied either, and the dashboards stayed empty.
snapshot = {"app": "irivai-template", "version": 1, "name": "PC",
            "dashboard": {"pages": [{"id": "main",
                                     "widgets": [{"id": "w1", "kind": "tile",
                                                  "title": "T", "source": "stat",
                                                  "key": "objects_in_frame"}]}]},
            "workflows": {"version": 3, "workflows": [{
                "id": "main", "name": "Main", "graph": {
                    "version": 3,
                    "nodes": [{"id": "m1", "type": "source.detection",
                               "position": {"x": 0, "y": 0}, "data": {}}],
                    "edges": []}}]}}
fmt = provision.promote_template("pc_test", snapshot, CFG)
assert fmt.get("has_workflow"), "the workflow did not survive promote"
assert isinstance(fmt.get("dashboard"), dict), \
    "the dashboard layout does not travel with the format"
pages = fmt["dashboard"].get("pages") or []
assert pages and pages[0]["widgets"], fmt["dashboard"]
print("== promote carries the workflow AND the whole dashboard layout")

# An unbound zone is a WAITING chain, not a compile error: the template lands
# with its zones deliberately stripped, and an error there kept everything
# else from applying too.
from core.services import workflow as wf                      # noqa: E402
graph = {"version": 3, "nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0}, "data": {"zone": ""}},
    {"id": "a", "type": "analytics.violations", "position": {"x": 2, "y": 0},
     "data": {"fnId": "waiting", "name": "Waiting"}},
    {"id": "f", "type": "analytics.frame", "position": {"x": 2, "y": 1},
     "data": {"fnId": "frames", "name": "Frames"}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "a"},
    {"id": "e3", "source": "s", "target": "f"},
]}
res = wf.compile_graph(graph, zones=[])
assert not res.get("errors"), res.get("errors")
assert any("waiting" in str(w.get("message", "")).lower()
           for w in res.get("warnings") or []), res.get("warnings")
# ...and the rest of the canvas still compiled: the frame counter applied.
assert any(f["id"] == "frames" for f in res.get("functions") or []), \
    [f["id"] for f in res.get("functions") or []]
assert not any(f["id"] == "waiting" for f in res.get("functions") or [])
print("== an unbound zone waits (disabled chain); everything else applies")

# -- the old gate is gone -------------------------------------------------------
# Admins were required to pick a SAVED template -- a list the same API hands
# them empty -- so creation was possible only for developers. A built-in now
# carries the whole build, so picking one is enough for anybody.
assert "pick a template to start from" not in create, \
    "the saved-template gate is back"
print("== creating from a built-in needs no saved template any more")
print("ALL OK")
