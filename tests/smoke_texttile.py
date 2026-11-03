# A tile may say a WORD.
#
# Everything a dashboard shows is a number, and for a good reason: the stats
# dict is read by the charts, the master sync and the Blynk pusher, all of
# which expect numbers. But "violate", "Zone 1" and the current shift are
# values an operator has on the canvas and wants on a card, and there were two
# ways to be refused:
#
#   * a text constant wired into a tile answered "Card needs exactly one
#     Analytics input" -- of a card wired to a value sitting right there. A
#     text constant publishes no stat, so it never counted as an input.
#   * a text COLUMN wired into a tile compiled, and then showed a row count:
#     the default aggregation for a label is "count", which is the right
#     answer for a pie of that column and the wrong one for a one-value card.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service            # noqa: E402
from core.services import workflow as wf                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


TABLES = {"pcb": {"name": "PCB", "columns": [
    {"name": "violation", "type": "text"},
    {"name": "total_pcb", "type": "number"}]}}


# -- 1. a text constant on a tile ---------------------------------------------
def const_graph(kind="tile", value="violate", extra_wire=False):
    nodes = [
        {"id": "k1", "type": "variable.number", "position": {"x": 0, "y": 0},
         "data": {"fnId": "violation_status", "name": "violation_status",
                  "value": value, "value_type": "text"}},
        {"id": "c1", "type": "dashboard.card", "position": {"x": 2, "y": 0},
         "data": {"name": "Status", "kind": kind}},
    ]
    edges = [{"id": "e1", "source": "k1", "target": "c1"}]
    if extra_wire:
        nodes += [
            {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 1},
             "data": {}},
            {"id": "f1", "type": "analytics.frame", "position": {"x": 1, "y": 1},
             "data": {"fnId": "seen", "name": "Seen", "mode": "realtime"}},
        ]
        edges += [{"id": "e2", "source": "m1", "target": "f1"},
                  {"id": "e3", "source": "f1", "target": "c1"}]
    return wf.clean_graph({"nodes": nodes, "edges": edges})


c = wf.compile_graph(const_graph(), [], ["person"], ["person"])
check("a text constant wired into a tile compiles", not c["errors"], c["errors"])
w = (c["widgets"] or [{}])[0]
check("...and the words travel ON the card", w.get("text") == "violate", w)
check("...with no stat key, because there is no number to look up",
      w.get("key") == "" and w.get("source") == "stat", w)

# The stats dict stays numbers: a string in it would reach the charts, the
# master sync and the Blynk pusher, none of which can take one.
check("...and nothing was published into the stats",
      not c["functions"], c["functions"])

# A card with a RULE and a constant wired in is ambiguous. Answering it by
# picking the constant would quietly ignore the rule.
# A card with a RULE and a constant wired in shows the RULE -- it is the thing
# that changes -- but says so, because the constant is on the canvas, wired,
# and visibly doing nothing.
c = wf.compile_graph(const_graph(extra_wire=True), [], ["person"], ["person"])
check("a card wired to a rule AND a constant compiles", not c["errors"],
      c["errors"])
check("...showing the rule", (c["widgets"] or [{}])[0].get("key")
      == "fn_seen_count", c["widgets"])
check("...and saying the constant is ignored",
      any("text constant beside it is ignored" in x["message"]
          for x in c["warnings"]), c["warnings"])

# -- 2. the server carries it, and computes it --------------------------------
c = wf.compile_graph(const_graph(), [], ["person"], ["person"])
w = c["widgets"][0]
saved = builder_service.validate([w])[0]
check("a text tile survives the save", saved.get("text") == "violate", saved)
# ...and it needs no key to do so: the check that demands one is the same one
# that used to reject this card outright.
check("...without a key", saved.get("key") == "", saved)
out = builder_service.compute([saved], [], {"fn_seen_count": 3})[0]["data"]
check("the card renders the words", out.get("value") == "violate", out)

# A stat widget with neither a key nor text is still a mistake.
try:
    builder_service.validate([{"id": "w1", "kind": "tile", "source": "stat"}])
    check("a tile with neither a key nor words is refused", False)
except ValueError as exc:
    check("a tile with neither a key nor words is refused",
          "needs a key" in str(exc), str(exc))

# Words on a BUCKET or a HISTORY widget make no sense -- both draw a series
# over time, and words have none -- so the key is still required there.
try:
    builder_service.validate([{"id": "w1", "kind": "line", "source": "history",
                               "text": "violate"}])
    check("a history widget still needs a key", False)
except ValueError as exc:
    check("a history widget still needs a key", "needs a key" in str(exc))


# -- 3. a text COLUMN on a tile shows the value, not a row count ---------------
def column_graph(kind="tile", column="violation"):
    return wf.clean_graph({"nodes": [
        {"id": "t1", "type": "output.dataset", "position": {"x": 0, "y": 0},
         "data": {"dataset": "pcb", "name": "PCB", "op": "select"}},
        {"id": "c1", "type": "dashboard.card", "position": {"x": 1, "y": 0},
         "data": {"name": "Status", "kind": kind}},
    ], "edges": [{"id": "e1", "source": "t1", "sourceHandle": f"o_{column}",
                  "target": "c1"}]})


c = wf.compile_graph(column_graph(), [], tables=TABLES)
w = (c["widgets"] or [{}])[0]
check("a text column on a tile reads its LATEST value",
      (w.get("query") or {}).get("agg") == "latest", w.get("query"))
check("...of that column", (w.get("query") or {}).get("field") == "violation",
      w.get("query"))
# A number is unchanged: latest was already the default there.
c = wf.compile_graph(column_graph(column="total_pcb"), [], tables=TABLES)
check("a number column still reads its latest value",
      ((c["widgets"] or [{}])[0].get("query") or {}).get("agg") == "latest")

# A CHART of a text column still counts: how often each value occurs is the
# only thing a shape can draw from words.
chart = wf.clean_graph({"nodes": [
    {"id": "t1", "type": "output.dataset", "position": {"x": 0, "y": 0},
     "data": {"dataset": "pcb", "name": "PCB", "op": "select"}},
    {"id": "g1", "type": "dashboard.graph", "position": {"x": 1, "y": 0},
     "data": {"name": "Violations", "kind": "donut"}},
], "edges": [{"id": "e1", "source": "t1", "sourceHandle": "o_violation",
              "target": "g1"}]})
c = wf.compile_graph(chart, [], tables=TABLES)
w = (c["widgets"] or [{}])[0]
check("a chart of a text column still counts its values",
      (w.get("query") or {}).get("agg") == "count", w.get("query"))

# A COUNTER card counts, whatever it is fed: that is the card's name, and its
# existing contract (smoke_dataset_ports.py). Only the plain Card shows a
# value.
counter = wf.clean_graph({"nodes": [
    {"id": "t1", "type": "output.dataset", "position": {"x": 0, "y": 0},
     "data": {"dataset": "pcb", "name": "PCB", "op": "select"}},
    {"id": "n1", "type": "dashboard.counter", "position": {"x": 1, "y": 0},
     "data": {"name": "How many"}},
], "edges": [{"id": "e1", "source": "t1", "sourceHandle": "o_violation",
              "target": "n1"}]})
c = wf.compile_graph(counter, [], tables=TABLES)
check("a Counter card still counts a text column",
      ((c["widgets"] or [{}])[0].get("query") or {}).get("agg") == "count",
      (c["widgets"] or [{}])[0].get("query"))

# -- 4. the card SHOWS words rather than mangling them ------------------------
widget_src = io.open(os.path.join(ROOT, "frontend", "src", "components",
                                  "widgets", "Widget.jsx"),
                     encoding="utf-8").read()
check("the tile sizes a text value for reading",
      "function textValue(value, scale" in widget_src
      and "...textValue(data.value, scaleOf(widget.value_size))" in widget_src,
      "the size is still measured from the value; it is now multiplied by "
      "the card's chosen value size (smoke_cardfont.py)")
check("...deciding by what the value IS, not by what produced it",
      '!Number.isNaN(Number(value))' in widget_src)
check("...and long text wraps instead of running off the card",
      "overflowWrap: \"anywhere\"" in widget_src)

# ...and the picker says so. The shape dropdown promised "one number", which
# was the only place left telling an operator that a word could not go on a
# card -- and it is the place they read before wiring one.
page = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
               encoding="utf-8").read()
# The shape names live in the shared card fields (cardFields.CARD_SHAPES),
# which the node's picker and the Builders' both draw.
shapes = io.open(os.path.join(ROOT, "frontend", "src", "components", "widgets",
                              "cardFields.jsx"), encoding="utf-8").read()
check("the shape picker offers a tile as one VALUE",
      "stat tile - one value (number or text)" in shapes
      and "shapes={CARD_SHAPES.filter(" in page)
check("...and no longer promises a number anywhere it means a value",
      "one number</option>" not in page and "A tile shows one number" not in page
      and "one number\"]" not in shapes)
check("the Query help says what a tile does with a word",
      "or the first cell when" in page)

# -- 5. "Show the value as" reaches the page at all ---------------------------
#
# The inspector's select offers the boolean styles (YES / NO, ON / OFF,
# TRUE / FALSE) and now "text", for a tile bound to a rule that picked a WORD
# -- a text STAT, which is the other route to words on a card and not the
# constant above.
#
# Every one of those options was dead. compute() assembles the rendered widget
# field by field and never mentioned `format`, and that dict is the ONLY shape
# the Dashboard, Analytics and the Builder preview ever see -- so Widget.jsx
# read widget.format as undefined on all three, and the boolean styles had
# never done anything on a real dashboard since the day they were added. The
# same trap this file's neighbours already document for `template`, `bands`
# and `by_day`.
check("'text' is a value format the server accepts",
      "text" in builder_service.VALUE_FORMATS, builder_service.VALUE_FORMATS)

for fmt in ("yesno", "onoff", "truefalse", "text"):
    saved = builder_service.validate([{"id": "f1", "kind": "tile",
                                       "source": "stat", "key": "fn_a_count",
                                       "format": fmt}])[0]
    check(f"a tile saved as {fmt!r} keeps it", saved.get("format") == fmt, saved)
    drawn = builder_service.compute([saved], [], {"fn_a_count": 1})[0]
    check(f"...and the renderer is handed {fmt!r}", drawn.get("format") == fmt,
          drawn)

odd = builder_service.validate([{"id": "f1", "kind": "tile", "source": "stat",
                                 "key": "fn_a_count", "format": "shouty"}])[0]
check("an unknown format still reads as the value itself",
      odd.get("format") == "", odd)


def format_graph(fmt):
    return wf.clean_graph({"nodes": [
        {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "f1", "type": "analytics.frame", "position": {"x": 1, "y": 0},
         "data": {"fnId": "seen", "name": "Seen", "mode": "realtime"}},
        {"id": "c1", "type": "dashboard.card", "position": {"x": 2, "y": 0},
         "data": {"name": "Status", "kind": "tile", "format": fmt}},
    ], "edges": [{"id": "e1", "source": "m1", "target": "f1"},
                 {"id": "e2", "source": "f1", "target": "c1"}]})


c = wf.compile_graph(format_graph("text"), [], ["person"], ["person"])
w = (c["widgets"] or [{}])[0]
check("the compiler carries 'text' off the node", w.get("format") == "text", w)
check("...onto a card that reads a live stat key",
      w.get("key") == "fn_seen_count" and w.get("source") == "stat", w)
saved = builder_service.validate([w])[0]
drawn = builder_service.compute([saved], [], {"fn_seen_count": "People"})[0]
check("...and the page gets both the format and the word",
      drawn.get("format") == "text" and drawn["data"]["value"] == "People",
      drawn)

c = wf.compile_graph(format_graph("shouty"), [], ["person"], ["person"])
check("a format the compiler does not know is dropped, not compiled",
      "format" not in (c["widgets"] or [{}])[0], c["widgets"])

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
