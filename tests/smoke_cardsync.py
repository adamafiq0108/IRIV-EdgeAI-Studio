# One card definition, two editors, and who may write what.
#
# The node STORES everything a card is (that is what survives a compile), but
# only one side may WRITE each knob, or the two panels are a question -- which
# one wins? -- whose answer, whichever was saved last, nobody can see:
#
#   the canvas node   identity (title, caption, shape) and DATA, which
#                     includes the threshold bands;
#   the Builders      the LOOK (template, colours, sizes, animation, per-day
#                     colouring) and the placement.
#
# The bands are the one knob offered on both sides -- a limit is a statement
# about the metric -- so the Builders show them under Data, beside the source,
# and both doors write them onto the node.
#
# The canvas save is held to this on the server: the node panel stopped
# OFFERING the look a while ago, but the whole graph still goes up on every
# save, look included, so a tab opened before the Builder restyled a card put
# the old look straight back. "Node says orange, layout says white" was that
# race, and keep_card_look takes the canvas out of it.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                            # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def graph_with(card_data):
    return wf.clean_graph({"nodes": [
        {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "f1", "type": "analytics.frame", "position": {"x": 1, "y": 0},
         "data": {"fnId": "seen", "name": "Seen", "mode": "realtime"}},
        {"id": "peak", "type": "dashboard.card", "position": {"x": 2, "y": 0},
         "data": {"name": "Peak Hour", "kind": "tile", "metric": "count",
                  **card_data}},
    ], "edges": [{"id": "e1", "source": "m1", "target": "f1"},
                 {"id": "e2", "source": "f1", "target": "peak"}]})


def compiled_card(graph):
    out = wf.compile_graph(graph, [], ["person"], ["person"])
    check("the graph compiles clean", not out["errors"], out["errors"])
    return next(w for w in out["widgets"] if w["id"] == "wf_peak")


BANDS = {"at": [5, 10], "colors": ["#00FF00", "#FFAA00", "#FF0000"]}

# -- 1. a threshold set on the NODE reaches the wf_* row ----------------------
w = compiled_card(graph_with({"bands": BANDS, "sub": "since 6 am"}))
check("bands set on the node compile onto the card", w.get("bands") == BANDS, w)
check("...and so does the caption, on a plain stat tile",
      w.get("sub") == "since 6 am", w)

# -- 2. a threshold set in the BUILDER reaches the node -----------------------
# The Builders write through card-style; the route hands the cards to
# apply_card_style, which is what is exercised here.
store = {"workflows": [{"id": "wf", "graph": graph_with({})}]}
node = store["workflows"][0]["graph"]["nodes"][2]
n = wf.apply_card_style(store, [{"id": "wf_peak", "bands": BANDS}])
check("bands sent from a Builder land on the node",
      n == 1 and node["data"].get("bands") == BANDS, node["data"])
check("...and compile back onto the card from there",
      compiled_card(store["workflows"][0]["graph"]).get("bands") == BANDS)
# Cleaned the way the Builder's own save cleans them: a limit list with one
# colour too few is repaired, not stored raw.
wf.apply_card_style(store, [{"id": "wf_peak",
                             "bands": {"at": [3], "colors": ["#0000FF"]}}])
got = node["data"].get("bands")
check("...cleaned by the same rule as a layout save",
      got == {"at": [3], "colors": ["#0000FF", ""]} or (
          isinstance(got, dict) and got.get("at") == [3]
          and len(got.get("colors") or []) == 2), got)
check("a card naming no node is skipped, not refused",
      wf.apply_card_style(store, [{"id": "wf_nope", "bands": BANDS},
                                  "junk"]) == 0)

# -- 3. the CANVAS cannot write a look knob ------------------------------------
# What the Builder put on the node...
wf.apply_card_style(store, [{"id": "wf_peak", "template": "outline",
                             "color": "#DC0404", "bg": "", "value_size": "large",
                             "anim": "fade_in", "anim_replay": True,
                             "by_day": True, "name": "Peak Hour",
                             "sub": "since 6 am"}])
stored = store["workflows"][0]["graph"]
# ...and what a stale tab sends up a moment later: the orange it loaded with,
# a plain template, no animation -- plus a real data edit of its own.
incoming = graph_with({"template": "plain", "color": "", "bg": "#dc3a04",
                       "value_size": "medium", "anim": "", "anim_replay": False,
                       "by_day": False, "bands": {"at": [7], "colors": ["", ""]},
                       "unit": "cars", "sub": "this shift"})
wf.keep_card_look(incoming, stored)
d = incoming["nodes"][2]["data"]
check("the stored look wins over whatever the canvas sent",
      d.get("template") == "outline" and d.get("color") == "#DC0404"
      and d.get("bg") == "" and d.get("value_size") == "large", d)
check("...animation and per-day colouring included",
      d.get("anim") == "fade_in" and d.get("anim_replay") is True
      and d.get("by_day") is True, d)
check("...while the canvas's DATA edit is kept: the threshold",
      d.get("bands") == {"at": [7], "colors": ["", ""]}, d)
check("...the unit, and the identity it owns",
      d.get("unit") == "cars" and d.get("sub") == "this shift"
      and d.get("name") == "Peak Hour", d)
# A node the store has never seen carries no look at all: the Builder
# dresses it, as it would any new card.
fresh = graph_with({"template": "gradient", "bg": "#123456", "bands": BANDS})
wf.keep_card_look(fresh, stored={"nodes": []})
d = fresh["nodes"][2]["data"]
check("a new node arrives undressed, whatever it claimed",
      "template" not in d and "bg" not in d and d.get("bands") == BANDS, d)
# ...and a node that is not a card is not touched: its `color` is its own.
other = wf.clean_graph({"nodes": [
    {"id": "z", "type": "zone.ref", "position": {"x": 0, "y": 0},
     "data": {"zone": "a", "color": "#ABCDEF"}}], "edges": []})
wf.keep_card_look(other, stored={"nodes": []})
check("a non-dashboard node keeps every field it sent",
      other["nodes"][0]["data"].get("color") == "#ABCDEF")

# -- 4. a Builder look edit round-trips: node -> row -> node --------------------
w = compiled_card(stored)
check("the Builder's look compiles onto the card",
      w.get("template") == "outline" and w.get("color") == "#DC0404"
      and w.get("value_size") == "large" and w.get("anim") == "fade_in", w)
# The canvas saves in between (stale look and all; the bands go up as the
# tab holds them, because the canvas owns the data and a save without them
# would be a save that cleared them)...
resaved = wf.keep_card_look(
    graph_with({"template": "plain", "bands": {"at": [3], "colors": ["#0000FF", ""]}}),
    stored)
store["workflows"][0]["graph"] = resaved
check("...survives a canvas save in between",
      compiled_card(resaved).get("template") == "outline")
# ...and the Builder clears it: a reset is a write, not a gap to fill.
n = wf.apply_card_style(store, [{"id": "wf_peak", "template": "plain",
                                 "color": "", "value_size": "medium",
                                 "anim": "", "anim_replay": False,
                                 "by_day": False}])
w = compiled_card(resaved)
check("a reset in the Builder reaches the card",
      n > 0 and w.get("template") == "plain" and not w.get("color")
      and w.get("value_size") == "medium" and w.get("anim") == "", w)
check("...leaving the threshold alone", (w.get("bands") or {}).get("at") == [3], w)

# -- 5. a verdict's two words are data, and travel the same way ---------------
# The customer's four verdict cards said COMPLIANT / VIOLATION after the node
# said Empty / Full: hand-made tiles switched to Verdict in the inspector, no
# `node` link, states [] -- so nothing on the canvas could reach them, and the
# Builder offered no way to say the words either.
from core.services import builder as builder_service                # noqa: E402

# An UNLINKED verdict row with its own state rows shows those words...
rows = builder_service.validate([
    {"id": "tile_slot1", "kind": "verdict", "source": "stat", "key": "fn_s1_active",
     "states": [{"value": 0, "label": "Empty", "tone": "ok"},
                {"value": 1, "label": "Full", "tone": "alert"}]}])
slot = rows[0]
check("an unlinked verdict keeps the state rows the Builder wrote",
      [s["label"] for s in slot["states"]] == ["Empty", "Full"], slot)
check("...and shows them, not the stock words",
      builder_service._data(slot, [], {"fn_s1_active": 1})["label"] == "Full"
      and builder_service._data(slot, [], {"fn_s1_active": 0})["label"] == "Empty")
check("...while a row with no state rows still reads the stock words",
      builder_service._data({**slot, "states": []}, [], {"fn_s1_active": 1})["label"]
      == "VIOLATION")

# ...and a LINKED card's words go to the node and come back compiled.
vgraph = wf.clean_graph({"nodes": [
    {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "f1", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "s1", "name": "Slot 1", "mode": "realtime"}},
    {"id": "v1", "type": "dashboard.card", "position": {"x": 2, "y": 0},
     "data": {"name": "Slot 1", "kind": "verdict", "metric": "active"}},
], "edges": [{"id": "e1", "source": "m1", "target": "f1"},
             {"id": "e2", "source": "f1", "target": "v1"}]})
vstore = {"workflows": [{"id": "wf", "graph": vgraph}]}
n = wf.apply_card_style(vstore, [{"id": "wf_v1", "ok_label": "Empty",
                                  "alert_label": "Full"}])
vnode = vgraph["nodes"][2]["data"]
check("verdict words sent from a Builder land on the node",
      n == 2 and vnode.get("ok_label") == "Empty"
      and vnode.get("alert_label") == "Full", vnode)
out = wf.compile_graph(vgraph, [], ["person"], ["person"])
vw = next(w for w in out["widgets"] if w["id"] == "wf_v1")
check("...and compile back as the card's state rows",
      [(s["value"], s["label"], s["tone"]) for s in vw["states"]]
      == [(0, "Empty", "ok"), (1, "Full", "alert")], vw)
check("...and the rail entry carries those rows for a drag",
      next(c for c in out["catalog"] if c["node"] == "v1")["states"] == vw["states"])
# A node with CUSTOM rows compiles those; the word lands on the matching row
# too, or the Builder's edit would reach the node and change nothing.
vnode["states"] = [{"value": 0, "label": "IDLE", "tone": "ok"},
                   {"value": 1, "label": "BUSY", "tone": "alert"},
                   {"value": 2, "label": "JAM", "tone": "warn"}]
wf.apply_card_style(vstore, [{"id": "wf_v1", "ok_label": "Free"}])
check("a word written to a node with custom rows lands on the row of that tone",
      vnode["states"][0]["label"] == "Free" and vnode["states"][1]["label"] == "BUSY",
      vnode["states"])
check("...and a blank word is not painted over a row",
      wf.apply_card_style(vstore, [{"id": "wf_v1", "ok_label": ""}]) == 1
      and vnode["states"][0]["label"] == "Free")

ui = read("frontend", "src", "components", "widgets", "builderShared.jsx")
check("the Builders offer the verdict words under Data",
      "export function verdictLabels(" in ui
      and "{verdictFields}" in ui.split('<Section title="Data">')[1].split("</Section>")[0])
check("...offer to LINK a hand-made twin of a workflow card",
      "Link to {twin.label}" in ui and "c.kind === current.kind" in ui)
check("...and a dragged verdict lands with its state rows",
      "states: c.states.map((s) => ({ ...s }))" in ui)
for jsx in ("Builder.jsx", "AnalyticsBuilder.jsx"):
    src = read("frontend", "src", "pages", jsx)
    check(f"{jsx} sends a verdict's words to its node, linked copies included",
          'w.kind === "verdict" ? verdictLabels(w)' in src
          and "styled.push({ id: `wf_${w.node}`, bands: w.bands || {}, ...words," in src
          # ...and the copy's question ("Work out", the period) goes too.
          and "...cardQuestionEdits(base.get(w.id), w) });" in src)
    # ...and a linked copy's THRESHOLD with them: the bands are data, so a
    # limit typed on a copy goes to the node, and comes back to every card.
    check(f"{jsx} sends a linked copy's bands to its node",
          "} else if (w.node) {" in src)

# -- 6. the panels say the same thing ------------------------------------------
shared = read("frontend", "src", "components", "widgets", "builderShared.jsx")
page = read("frontend", "src", "pages", "Workflow.jsx")
data_section = shared.split('<Section title="Data">')[1].split("</Section>")[0]
look_section = shared.split('<Section title="Appearance"')[1].split("</Section>")[0]
check("the Builders offer the thresholds under Data",
      "<BandEditor kind={current.kind}" in data_section)
check("...and not under Appearance", "BandEditor" not in look_section)
look_count = shared.split('<Section title="Appearance"')[1].split(")}>")[0]
check("...and Appearance no longer counts them",
      "current.by_day" in look_count and "bands" not in look_count, look_count)
# The band COLOURS are look: under Appearance, beside each input's colour
# (owner, 2026-10-12), and nowhere in Data.
check("the band colours and series colours are under Appearance",
      "<BandColours kind={current.kind}" in look_section
      and "<SeriesColours" in look_section
      and "<BandColours" not in data_section and "<SeriesColours" not in data_section)
# One panel for every dashboard node since the Event Card folded into the Card
# and the Chart (its question is RecordedQuery, drawn inside this panel).
# (Opened by "&& !pickingType" since the chart type picker, 2026-10-14: a new
# Chart shows the picker in this panel's place.)
card_panel = page.split('{spec.category === "dashboard" && !pickingType && (')[1]
card_panel = card_panel.split('{node.type === "integration.photos"')[0]
check("the node panel offers the thresholds", "<CardBands" in card_panel)
check("...and the caption", 'onChange={set("sub")}' in card_panel)
for knob in ("TextSizePicker", "AnimationPicker", "DayColourToggle",
             "ColourField", 'set("template")', 'set("bg")', 'set("color")'):
    check(f"...but not the look: {knob}", knob not in card_panel)
for jsx in ("Builder.jsx", "AnalyticsBuilder.jsx"):
    check(f"{jsx} sends the caption with the look",
          'sub: w.sub || ""' in read("frontend", "src", "pages", jsx))
check("the save route locks the look",
      "workflow_service.keep_card_look(graph, w.get(\"graph\"))"
      in read("core", "routes", "workflow.py"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
