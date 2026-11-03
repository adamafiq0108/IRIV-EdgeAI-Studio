# A threshold line on a card is a decision, so it compiles to a rule.
#
# Bands were a picture: they coloured the value axis and said nothing to the
# rest of the graph. But somebody who draws a line at 80 has DECIDED something
# at 80, and had to say it a second time -- in a Compare, or a Watch -- which
# is one limit kept in two places that drift apart the first time anybody
# moves the line.
#
# Each edge now compiles to a rule named after the card: it raises an alert
# and publishes 1/0 that any formula, branch or card can read. It reads the
# SAME stat the card reads, so it answers as the value crosses rather than on
# a sampler's timer.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def graph(**card):
    data = {"name": "Line rate", "kind": "area",
            "bands": {"at": [50, 80], "colors": []}}
    data.update(card)
    return {"nodes": [
        {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person"]}},
        {"id": "c1", "type": "analytics.frame", "position": {"x": 2, "y": 0},
         "data": {"fnId": "src", "name": "Src", "mode": "realtime"}},
        {"id": "g1", "type": "dashboard.graph", "position": {"x": 3, "y": 0},
         "data": data},
    ], "edges": [
        {"id": "e1", "source": "m1", "target": "f1"},
        {"id": "e2", "source": "f1", "target": "c1"},
        {"id": "e3", "source": "c1", "target": "g1"},
    ]}


def rules(g):
    out = wf.compile_graph(g, [], ["person"], ["person"])
    assert not out["errors"], out["errors"]
    return out, [f for f in out["functions"] if f["id"].startswith("th_")]


out, th = rules(graph())
check("each threshold line becomes a rule of its own", len(th) == 2,
      [f["id"] for f in th])
check("...named after the CARD, saying which line it is",
      [f["name"] for f in th] == ["Line rate · over 50", "Line rate · over 80"],
      [f["name"] for f in th])
check("...watching the same stat the card draws, so it answers live",
      all(f["params"]["expr"].startswith("fn_src_count >=") for f in th),
      [f["params"]["expr"] for f in th])
# SILENT by default. The rule exists -- that is what makes it wireable, and
# what puts it in the pickers and under Alerts in the palette -- but a line
# drawn on a chart is a thing to measure against, and turning every one
# already drawn into events on a box running in the field is a decision
# somebody should make on purpose.
check("...and raises nothing until asked", not any(f.get("trigger") for f in th),
      [f.get("trigger") for f in th])
_, loud = rules(graph(band_alert="alert"))
check("...and does raise one when asked", all(f.get("trigger") for f in loud))
check("...with a quiet period, or a value sitting ON the line fires every frame",
      all(f["trigger"]["cooldown_s"] > 0 for f in loud),
      [f["trigger"]["cooldown_s"] for f in loud])
check("the card itself still draws its bands",
      (out["widgets"][0].get("bands") or {}).get("at") == [50.0, 80.0],
      out["widgets"][0].get("bands"))

# The point of the rule: the rest of the graph can read it. It publishes a
# value under its own id like every other function, which is what puts it in
# the metric pickers, the formulas and the branch inputs.
check("...and publishes a value the rest of the graph can wire to",
      all(f["type"] == "expr" and f.get("enabled") for f in th))

# ONE line: the rule is simply the card's name. Nobody would have called it
# "Line rate - over 50" if there were only one line to mean.
_, one = rules(graph(bands={"at": [80], "colors": []}))
check("one line, one rule, named exactly as the card is",
      [f["name"] for f in one] == ["Line rate"], [f["name"] for f in one])

# A band that really is only a picture.
_, none = rules(graph(band_alert="off"))
check("a card can say its lines are decoration and get no rules", not none,
      [f["id"] for f in none])

# No lines at all: nothing to decide, nothing to compile.
_, plain = rules(graph(bands={}))
check("a card with no lines compiles no rules", not plain)

# An UNNAMED card is titled after whatever feeds it, so a rule from one would
# reach the events log called "Card". Naming it is the same opt-in the
# dashboard palette already asks for.
#
# Silently -- and that is the point of these three. Each of them used to warn,
# and every one of them fires on a card that WORKS: the lines are drawn, the
# chart is right, and the only thing missing is a rule nobody asked for. A
# warning there trains an operator to ignore the warnings that do mean
# something.
out, unnamed = rules(graph(name=""))
check("an unnamed card gets no rules, and says nothing about it", not unnamed
      and not out["warnings"], out["warnings"])

# Severity and message are the operator's.
_, mine = rules(graph(band_alert="alert", band_severity="crit",
                      band_message="Line 2 over limit",
                      band_cooldown_s=5, bands={"at": [80], "colors": []}))
check("severity, message and quiet period are the operator's to set",
      mine[0]["trigger"]["severity"] == "crit"
      and mine[0]["trigger"]["message"] == "Line 2 over limit"
      and mine[0]["trigger"]["cooldown_s"] == 5, mine[0]["trigger"])

# Two cards, two sets of rules: the id is the NODE's, so two cards drawing the
# same line do not collide into one rule.
g = graph()
g["nodes"].append({"id": "g2", "type": "dashboard.graph",
                   "position": {"x": 4, "y": 0},
                   "data": {"name": "Other", "kind": "area",
                            "bands": {"at": [50], "colors": []}}})
g["edges"].append({"id": "e4", "source": "c1", "target": "g2"})
_, two = rules(g)
check("two cards drawing the same line are two rules, not one",
      len({f["id"] for f in two}) == 3, [f["id"] for f in two])

# A chart that AGGREGATES draws a different number from the one the stat
# holds -- per-day totals, an average per bucket -- and the line is drawn
# across the DRAWN series. A live rule against it would be a different
# question wearing the same number, firing for reasons invisible on the chart.
out, agg = rules(graph(bucket="1d", agg="sum"))
check("an aggregating chart keeps its lines and gets no rule", not agg,
      [f["id"] for f in agg])
check("...and does not warn about a chart that is drawing correctly",
      not any("different number" in w["message"] for w in out["warnings"]),
      out["warnings"])

# A card fed by a TABLE draws rows that were recorded, not a live value, so
# there is no number in the stats dict for a rule to watch. The right answer,
# and a quiet one: this is the commonest shape on a real box -- a chart hung
# off a Data Table column -- and those charts are fine. Warning on each of
# them put a banner on a graph that had nothing wrong with it.
def from_table():
    return {"nodes": [
        {"id": "t1", "type": "output.dataset", "position": {"x": 0, "y": 0},
         "data": {"dataset": "log", "name": "Log"}},
        {"id": "g1", "type": "dashboard.graph", "position": {"x": 1, "y": 0},
         "data": {"name": "Per day", "kind": "area",
                  "bands": {"at": [10], "colors": []}}},
    ], "edges": [
        {"id": "e1", "source": "t1", "target": "g1",
         "sourceHandle": "o_status", "targetHandle": None},
    ]}


TABLES = {"log": {"key": "log", "name": "Log", "columns": [
    {"name": "status", "type": "number"}]}}
out = wf.compile_graph(from_table(), [], ["person"], ["person"], tables=TABLES)
assert not out["errors"], out["errors"]
made = [f for f in out["functions"] if f["id"].startswith("th_")]
check("a card reading a TABLE gets no rule -- there is no live value", not made,
      [f["id"] for f in made])
check("...and is not warned about: the card itself is working",
      not any("live value" in w["message"] for w in out["warnings"]),
      out["warnings"])

page = open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
            encoding="utf-8").read()
# The controls sit under Advanced, and the closed row says what they are set
# to. The default is the right answer for almost every line -- three stacked
# controls in the main column made the LINES above them, the thing actually
# being set, read as a footnote to their own settings.
check("the card says so, and can be told not to",
      'onChange({ band_alert: e.target.value })' in page
      and "band_severity" in page and "band_cooldown_s" in page
      and "band_message" in page)
check("...under Advanced, summarised on the closed row",
      "<AdvancedSection label={`Threshold rules" in page
      and '"value only"' in page)
# A rule nobody can find is a rule that gets rebuilt by hand in a Compare.
check("every threshold is listed under Alerts in the palette, ready to wire",
      "const thresholds = (functions || [])" in page
      and 'String(f.id || "").startsWith("th_")' in page
      and 'cat === "integration" && thresholds.length > 0' in page
      and 'dragProps("variable.ref"' in page)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
