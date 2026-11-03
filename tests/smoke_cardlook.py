# A compiled card's LOOK follows its node, on the Dashboard page too.
#
# The Builder writes a card's template / colours / sizes onto the workflow
# node (card-style) and the node compiles them back onto the wf_* widget: one
# truth, both editors read it. But apply_artifacts, refreshing a wf_* row that
# sits on the MAIN page, kept the STORED row's template, color and bg whenever
# they were set -- and validate() always sets a template -- so the node's fresh
# "outline" was overwritten by the row's old "plain" on the way back to disk.
# Pick a template five times in the Analytics Builder, watch it revert five
# times. (The Overview page took the compiled body and never had the bug.)
#
# Placement stays the operator's.
#
# Second act, same shape: a CLEARED look. The Builder writes every look knob
# onto the node, so a blank compiled colour is the operator's "reset to
# default" -- and both refill loops in apply_artifacts treated blank as "no
# opinion" and copied the stored row's old colour back. Worse for a LINKED
# COPY (a card dragged from the "From workflow" picker, id tile_* with a
# `node` link): its look is its own by design, but only truthy values were
# kept, so a copy whose card colour had been cleared took the node's orange
# back on every canvas save. That is the Peak Hour card on the customer box.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class FakeFns:
    def get_functions(self, systems_dir, slug):
        return []

    def set_functions(self, systems_dir, slug, functions):
        return functions


class FakeDC:
    def get_outputs(self, systems_dir, slug):
        return []

    def set_outputs(self, systems_dir, slug, outs):
        pass

    def set_commands(self, systems_dir, slug, commands):
        pass

    def set_watches(self, systems_dir, slug, watches):
        pass

    # Which rules run with detection off (workflow.logic_artifacts).
    def set_logic(self, systems_dir, slug, frames_free):
        self.logic = frames_free

    def set_preprocess(self, systems_dir, slug, entries):
        pass

    # Asked before the pre-process write, to resolve "the system's first
    # camera". No cameras here: this test is about cards.
    def cameras(self, systems_dir, slug):
        return []


STORED = {"pages": [
    {"id": "main", "widgets": [
        # What validate() leaves on disk after a plain first save: template
        # written as "plain", colours empty, a grid the operator chose.
        {"id": "wf_peak", "kind": "tile", "title": "Peak Hour",
         "template": "plain", "color": "", "bg": "",
         "label_size": "medium", "value_size": "medium",
         "grid": {"x": 2, "y": 1, "w": 3, "h": 2}},
        # A card whose colours were CLEARED in the Builder: card-style put
        # "" on the node, the row still holds what it had. The node wins.
        {"id": "wf_old", "kind": "tile", "title": "Old",
         "template": "plain", "color": "#123456", "bg": "#ABCDEF",
         "unit": "pcs",
         "grid": {"x": 0, "y": 0, "w": 2, "h": 2}}]},
    {"id": "overview", "widgets": [
        # The same cleared card on the Overview, which has its own merge.
        {"id": "wf_old", "kind": "tile", "title": "Old",
         "template": "plain", "color": "#123456", "bg": "#ABCDEF",
         "unit": "pcs", "grid": {"x": 4, "y": 0, "w": 2, "h": 2}},
        # A LINKED COPY of Peak Hour, dressed its own way: outline, its own
        # red, card colour cleared, still. Saved through validate(), so every
        # look key is spelled out -- blank included.
        {"id": "tile_copy", "kind": "tile", "title": "Peak Hour",
         "source": "stat", "key": "fn_x", "node": "peak",
         "template": "outline", "color": "#DC2504", "bg": "",
         "label_size": "medium", "value_size": "medium",
         "anim": "", "anim_replay": False, "bands": {}, "by_day": False,
         "grid": {"x": 8, "y": 0, "w": 4, "h": 2}},
        # A copy saved before the newer knobs existed: no anim/bands/by_day
        # key at all. Those, and only those, come from the node.
        {"id": "tile_early", "kind": "tile", "title": "Peak Hour",
         "source": "stat", "key": "fn_x", "node": "peak",
         "template": "plain", "color": "", "bg": "",
         "grid": {"x": 0, "y": 2, "w": 4, "h": 2}},
        # A LINKED COPY of a chart, dressed in the Builder: its lines' colours,
        # axis titles, orientation, bar style and value labels. None of it can
        # reach the node (card-style speaks to wf_* ids), so the recompile has
        # to leave it alone -- it used to put the node's defaults back.
        {"id": "bar_copy", "kind": "bar", "title": "Compare", "source": "history",
         "key": "", "node": "cmp", "template": "plain", "color": "", "bg": "",
         "x_title": "Hour", "y_title": "Cars", "orientation": "horizontal",
         "bar_style": "lollipop", "data_labels": "on",
         "series": [{"id": "s1", "label": "IN", "color": "#112233"},
                    {"id": "s2", "label": "OUT", "color": ""}],
         "grid": {"x": 0, "y": 4, "w": 4, "h": 4}}]}],
    "hidden": [], "placements": {}, "templates": {}, "colors": {},
    "accents": {}, "fonts": {}, "names": {}}


class FakeBuilder:
    MAIN_PAGE_ID = "main"
    MAX_WIDGETS = 24
    written = None

    def load(self, config_dir, slug):
        import copy
        return copy.deepcopy(STORED)

    def save(self, config_dir, slug, layout):
        FakeBuilder.written = layout


wf.functions_service = FakeFns()
wf.dc = FakeDC()
wf.builder_service = FakeBuilder()

# The node, after card-style: outline, red accent, blue card, big value.
compiled = {"functions": [], "outputs": [], "pages": [{"id": "overview", "widgets": [
    {"id": "wf_peak", "kind": "tile", "title": "Peak Hour", "source": "stat",
     "key": "fn_x",
     "template": "outline", "color": "#DC0404", "bg": "#04BADE",
     "label_size": "medium", "value_size": "large", "grid": {},
     # What the node says for the knobs the early copy lacks.
     "anim": "fade_in", "anim_replay": True,
     "bands": {"at": [5], "colors": ["#00FF00"]}, "by_day": True},
    # Cleared on the node: the compiler says "" for both colours and carries
    # no unit at all.
    {"id": "wf_old", "kind": "tile", "title": "Old", "template": "plain",
     "color": "", "bg": "", "grid": {}},
    # The chart node's body: the lines (names, no colours) and its own defaults.
    {"id": "wf_cmp", "kind": "bar", "title": "Compare Chart", "source": "history",
     "key": "", "template": "plain", "color": "", "bg": "", "grid": {},
     "series": [{"id": "s1", "label": "IN", "color": ""},
                {"id": "s2", "label": "OUT", "color": "#445566"}]}]}]}

wf.apply_artifacts("systems", None, "smoke", compiled)
pages = {p["id"]: {w["id"]: w for w in p["widgets"]}
         for p in FakeBuilder.written["pages"]}
rows = pages["main"]
peak = rows["wf_peak"]
check("the node's template reaches the Dashboard row",
      peak.get("template") == "outline", repr(peak))
check("...and its accent and card colour", peak.get("color") == "#DC0404"
      and peak.get("bg") == "#04BADE", repr(peak))
check("...and its text sizes", peak.get("value_size") == "large", repr(peak))
check("placement is still the operator's",
      peak.get("grid") == {"x": 2, "y": 1, "w": 3, "h": 2}, repr(peak))
old = rows["wf_old"]
check("a colour cleared on the node is cleared on the Dashboard row",
      old.get("color") == "" and old.get("bg") == "", repr(old))
check("...but a unit the node does not carry is kept",
      old.get("unit") == "pcs", repr(old))

# The Overview page has its own merge: the same contract must hold there.
over = pages["overview"]
old = over["wf_old"]
check("a colour cleared on the node is cleared on the Overview row",
      old.get("color") == "" and old.get("bg") == "", repr(old))
check("...unit and placement kept", old.get("unit") == "pcs"
      and old.get("grid") == {"x": 4, "y": 0, "w": 2, "h": 2}, repr(old))

# The linked copy: every look knob it spells out is its own, blank included.
copy = over["tile_copy"]
check("a linked copy keeps its own template and accent",
      copy.get("template") == "outline" and copy.get("color") == "#DC2504",
      repr(copy))
check("...and its CLEARED card colour stays cleared",
      copy.get("bg") == "", repr(copy))
check("...and its stillness",
      copy.get("anim") == "" and copy.get("anim_replay") is False
      and copy.get("by_day") is False, repr(copy))
# The bands are DATA, not dressing: a limit moved on the canvas must reach
# every chart of that node, the copies included. The copy's own empty {} used
# to win here, so the customer's chart (a copy) never saw the node's limit.
check("...while its body -- the threshold included -- is the node's",
      copy.get("key") == "fn_x" and copy.get("node") == "peak"
      and copy.get("bands") == {"at": [5], "colors": ["#00FF00"]}
      and copy.get("grid") == {"x": 8, "y": 0, "w": 4, "h": 2}, repr(copy))
early = over["tile_early"]
check("a copy saved before a knob existed takes that knob from the node",
      early.get("anim") == "fade_in" and early.get("anim_replay") is True
      and early.get("bands") == {"at": [5], "colors": ["#00FF00"]}
      and early.get("by_day") is True, repr(early))
check("...but keeps the look it did spell out",
      early.get("template") == "plain" and early.get("bg") == ""
      and early.get("color") == "", repr(early))

# A chart copy: the dressing the Builder put on it survives the recompile.
bar = over["bar_copy"]
check("a chart copy keeps its axis titles, orientation, bar style and value labels",
      bar.get("x_title") == "Hour" and bar.get("y_title") == "Cars"
      and bar.get("orientation") == "horizontal" and bar.get("bar_style") == "lollipop"
      and bar.get("data_labels") == "on", repr(bar))
check("...and the colour it gave a line, while a blank takes the node's",
      [s.get("color") for s in bar.get("series") or []] == ["#112233", "#445566"], repr(bar))
check("...the lines' names and sources still come from the node",
      [s.get("label") for s in bar.get("series") or []] == ["IN", "OUT"]
      and bar.get("title") == "Compare" and bar.get("node") == "cmp", repr(bar))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
